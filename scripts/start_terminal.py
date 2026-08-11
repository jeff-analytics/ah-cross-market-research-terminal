from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()
PRODUCT = "ah-cross-market-research-terminal"
HOST = os.environ.get("AH_TERMINAL_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("AH_TERMINAL_PORT", "8000"))
PORT_SCAN_SPAN = max(1, int(os.environ.get("AH_TERMINAL_PORT_SPAN", "50")))


def health(port: int, timeout: float = 0.8) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/health", timeout=timeout) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex((HOST, port)) == 0


def listener_pid(port: int) -> int | None:
    if os.name == "nt":
        try:
            out = subprocess.check_output(["netstat", "-ano", "-p", "tcp"], text=True, encoding="utf-8", errors="ignore")
        except Exception:
            return None
        needle = f":{port}"
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper() == "TCP" and needle in parts[1] and parts[3].upper() == "LISTENING":
                try:
                    return int(parts[-1])
                except ValueError:
                    pass
        return None
    try:
        out = subprocess.check_output(["lsof", "-tiTCP:%d" % port, "-sTCP:LISTEN"], text=True, errors="ignore")
        first = next((line.strip() for line in out.splitlines() if line.strip()), "")
        return int(first) if first.isdigit() else None
    except Exception:
        return None


def stop_old_terminal(port: int, info: dict) -> bool:
    if info.get("product") != PRODUCT and not str(info.get("version", "")).startswith(("4.", "3.")):
        return False
    pid = listener_pid(port)
    if not pid or pid == os.getpid():
        return False
    print(f"Detected an older A/H Terminal on port {port} (PID {pid}, version {info.get('version')}).")
    print("Stopping the stale local server so this package cannot open an old UI...")
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            if not port_open(port):
                return True
            time.sleep(0.2)
        if os.name != "nt" and port_open(port):
            os.kill(pid, signal.SIGKILL)
    except Exception:
        pass
    return not port_open(port)


def choose_port() -> int:
    info = health(DEFAULT_PORT)
    if info and info.get("product") == PRODUCT and info.get("version") == VERSION:
        return DEFAULT_PORT
    if info and info.get("product") == PRODUCT:
        if stop_old_terminal(DEFAULT_PORT, info):
            return DEFAULT_PORT
    if not port_open(DEFAULT_PORT):
        return DEFAULT_PORT
    # Do not kill unrelated services. Use a nearby free port instead.
    for port in range(DEFAULT_PORT + 1, DEFAULT_PORT + PORT_SCAN_SPAN):
        if not port_open(port):
            print(f"Port {DEFAULT_PORT} is occupied by another service; using {port} instead.")
            return port
    raise RuntimeError(f"No free local port found from {DEFAULT_PORT} across {PORT_SCAN_SPAN} candidate ports")


def run_helper(script: str, label: str) -> None:
    path = ROOT / "scripts" / script
    if not path.exists():
        return
    print(label)
    try:
        rc = subprocess.run([sys.executable, str(path)], cwd=ROOT, check=False).returncode
        if rc:
            print(f"WARNING: {label} returned code {rc}. The terminal will continue with the latest valid local data.")
    except Exception as exc:
        print(f"WARNING: {label} failed: {exc}")


def main() -> int:
    os.chdir(ROOT)
    print("=" * 68)
    print(f" A/H Cross-Market Research Terminal {VERSION}")
    print(" Unified launcher: dynamic A/H universe + A/H live monitor + daily update + web terminal")
    print("=" * 68)

    run_helper("ensure_universe.py", "[1/4] Checking dynamic A/H universe...")
    run_helper("ensure_daily_market_data.py", "[2/4] Verifying whole-universe daily A/H market freshness...")
    run_helper("ensure_live_monitor.py", "[3/4] Checking adaptive A+H live monitor...")

    port = choose_port()
    existing = health(port)
    if existing and existing.get("product") == PRODUCT and existing.get("version") == VERSION:
        url = f"http://{HOST}:{port}/?v={VERSION}&t={int(time.time())}"
        print(f"Terminal {VERSION} is already running: {url}")
        webbrowser.open(url)
        return 0

    print(f"[4/4] Starting web terminal on http://{HOST}:{port}")
    cmd = [sys.executable, "-m", "uvicorn", "server:app", "--host", HOST, "--port", str(port)]
    proc = subprocess.Popen(cmd, cwd=ROOT)

    try:
        ready = None
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError(f"Web server exited before startup, code={proc.returncode}")
            ready = health(port, timeout=0.5)
            if ready and ready.get("product") == PRODUCT and ready.get("version") == VERSION:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("Web server did not become ready within 20 seconds")

        url = f"http://{HOST}:{port}/?v={VERSION}&t={int(time.time())}"
        print(f"READY: {url}")
        print("The platform launcher starts universe sync, A/H live monitoring, daily updates and the web UI.")
        print("Press Ctrl+C to stop the web terminal.")
        webbrowser.open(url)
        return proc.wait()
    except KeyboardInterrupt:
        print("\nStopping terminal...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        if proc.poll() is None:
            proc.terminate()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
