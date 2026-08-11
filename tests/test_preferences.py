from pathlib import Path

import src.storage.preferences as preferences


def test_watchlist_round_trip(tmp_path: Path, monkeypatch):
    target = tmp_path / "watchlist.json"
    monkeypatch.setattr(preferences, "WATCHLIST_FILE", target)
    preferences.save_watchlist(["A", "B", "A"])
    assert preferences.load_watchlist(["A", "B", "C"]) == ["A", "B"]
    assert preferences.watchlist_mode() == "custom"
