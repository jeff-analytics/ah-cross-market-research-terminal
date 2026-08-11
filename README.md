# A/H Cross-Market Research Terminal

Local A/H dual-listed equity research terminal with intraday quotes, completed-day history, A/H premium analytics, screening, watchlists and data-quality controls.

**Version: v5.1.8**

## v5.1.8 final UI revision

- Sidebar date uses the machine/browser calendar date and updates automatically after midnight.
- Market Center daily chart shows completed trading days only. The current unfinished session is excluded.
- The data-provider label under the Market Center daily chart has been removed.
- The visual system has been refined into a denser financial-workstation style with a dark navy shell, restrained blue accent, quieter borders, compact tables and consistent A/H chart colors.
- Intraday A/H snapshots remain in memory, SQLite writes remain asynchronous, the focused security uses WebSocket push, providers expose health state, and focus/watchlist/priority/full-universe refresh tasks remain separated.

## Windows

First run: `INSTALL_WINDOWS.bat`, then `START_TERMINAL.bat`. Normal use only requires `START_TERMINAL.bat`. Use `STOP_LIVE_MONITOR.bat` if a detached live monitor remains after use.

## macOS

If needed, run `chmod +x ./*.command` once. First run: `INSTALL_MAC.command`, then `START_TERMINAL.command`. Normal use only requires `START_TERMINAL.command`. Use `STOP_LIVE_MONITOR.command` if a detached live monitor remains after use.

## Runtime layout

```text
server.py                 FastAPI application
src/                      market data, analysis, runtime and storage modules
web/                      browser terminal UI
data/                     bundled universe, history and runtime configuration
scripts/start_terminal.py unified launcher
scripts/ensure_universe.py
scripts/ensure_daily_market_data.py
scripts/ensure_live_monitor.py
scripts/live_monitor.py
requirements.txt
VERSION.txt
```

The platform release packages intentionally omit old diagnostics, duplicate `.cmd`/`.bat` launchers, historical acceptance reports, preview exports and legacy changelog files that are not needed for normal operation.

## Data semantics

Intraday quotes and daily bars are separate. During the trading session, current A/H quotes can update continuously, while the daily chart ends at the latest completed common trading day. A daily-chart row requires valid A-share close, H-share close and required FX conversion inputs.

This terminal is for research and software demonstration. Public market data can be delayed, incomplete or unavailable.
