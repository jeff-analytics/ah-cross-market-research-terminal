# v5.1.8 — Quote Semantics & Chart Stability Hotfix

## False pause fix
- Reclassified Tencent/Sina `quote_time` as the exchange-side last-trade time used for display.
- Live freshness is now validated with each HTTP snapshot's `fetched_at` timestamp.
- A/H synchronization checks the A/H fetch-time gap rather than the difference between the two securities' last trades.
- An H-share that has not traded for several minutes can remain a current live snapshot if the provider response itself is current.

## Intraday OHLC tolerance
- Missing high/low values no longer disable A/H premium calculation.
- When a provider temporarily omits high/low, the terminal keeps the previous same-session value when available.
- A first-snapshot OHLC omission is recorded as an informational display note.

## Chart stability
- Fixed the daily chart viewport at 390px.
- Removed the flex/intrinsic-height feedback loop that could enlarge the SVG after every render.
- WebSocket quote pushes now update the live quote cards without re-rendering unchanged daily history every second.
- Loading daily history renders the chart exactly once after the data response.

## Completed daily-bar semantics
- The daily chart now has a final API-level completed-session cutoff.
- During A/H trading hours, today's provisional provider K-line is excluded even when the provider labels its current intraday value as `close`.
- A plotted date must have a valid A-share close, H-share close and HKD/CNY value.
- The chart therefore ends at the latest completed common trading day; after both markets close and the daily data are complete, that day becomes eligible automatically.

## Validation
- Python: validation suite includes completed-daily regressions.
- Python compile: passed.
- JavaScript syntax check: passed.
- Added regressions for stale last-trade/current transport semantics, optional OHLC, and fixed chart height/WebSocket behavior.

## Final UI refinement (same v5.1.8)

- Sidebar date now follows the local calendar date and refreshes after midnight.
- Removed the Market Center daily-chart provider label from the footer.
- Refined colors, borders, tables, navigation state and A/H chart palette for an institutional financial-workstation appearance.
- Platform distributions were reduced to the launch/install files and runtime assets required for normal use.
