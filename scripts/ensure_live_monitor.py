from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PID_FILE = DATA / "live_monitor.pid"
STOP_FILE = DATA / "live_monitor.stop"
LOG_FILE = DATA / "live_monitor_console.log"
MONITOR = ROOT / "scripts" / "live_monitor.py"


def pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
            )
            return str(pid) in result.stdout
        except OSError:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def existing_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid_running(pid) else None


def start_monitor() -> tuple[bool, str]:
    DATA.mkdir(parents=True, exist_ok=True)
    pid = existing_pid()
    if pid:
        return True, f"Live monitor already running (PID {pid})."

    PID_FILE.unlink(missing_ok=True)
    STOP_FILE.unlink(missing_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log = LOG_FILE.open("a", encoding="utf-8")

    kwargs: dict = {
        "cwd": str(ROOT),
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "close_fds": os.name != "nt",
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )

    try:
        process = subprocess.Popen(
            [sys.executable, str(MONITOR), "--sleep", "1"],
            **kwargs,
        )
    except Exception as exc:
        log.close()
        return False, f"Unable to start live monitor: {exc}"
    log.close()

    for _ in range(20):
        time.sleep(0.1)
        pid = existing_pid()
        if pid:
            return True, f"Live monitor started (PID {pid})."
        if process.poll() is not None:
            return False, f"Live monitor exited immediately with code {process.returncode}. See {LOG_FILE.name}."
    return True, "Live monitor process launched; heartbeat will appear shortly."


if __name__ == "__main__":
    ok, message = start_monitor()
    print(message)
    raise SystemExit(0 if ok else 1)
