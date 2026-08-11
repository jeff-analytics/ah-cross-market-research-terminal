import os

# Unit/API tests must be deterministic and must never depend on public market-data
# endpoints. Production keeps on-demand history sync enabled by default.
os.environ.setdefault("AH_ON_DEMAND_HISTORY_SYNC", "0")
