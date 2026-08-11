from __future__ import annotations
from src.storage.refresh_policy import RefreshPolicy, save_refresh_policy

policy = save_refresh_policy(RefreshPolicy(True, 1, 3, 15, 3))
print('High-frequency policy enabled:')
print(f'  Focus / watchlist : {policy.watchlist_seconds}s')
print(f'  Priority pool     : {policy.priority_seconds}s')
print(f'  Full universe     : {policy.universe_seconds}s')
print(f'  Status check      : {policy.status_seconds}s')
print('Restart is not required; the monitor reads this policy every cycle.')
