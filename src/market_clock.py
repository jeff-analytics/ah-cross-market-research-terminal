from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from src.config import MARKET_CALENDAR_FILE

TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class MarketState:
    code: str
    label: str
    a_open: bool
    h_open: bool
    fx_open: bool
    watchlist_seconds: int
    priority_seconds: int
    universe_seconds: int
    store_seconds: int
    # v5.1.6 final: make the economic meaning of the displayed premium explicit.
    premium_mode: str = "realtime_sync"
    a_session: str = "open"
    h_session: str = "open"
    a_trading_day: bool = True
    h_trading_day: bool = True

    @property
    def any_open(self) -> bool:
        return self.a_open or self.h_open

    @property
    def both_open(self) -> bool:
        return self.a_open and self.h_open


@dataclass(frozen=True)
class DayOverride:
    a_open: bool
    h_open: bool
    note: str = ""


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "open", "是"}


def load_calendar_overrides(path: Path = MARKET_CALENDAR_FILE) -> dict[date, DayOverride]:
    if not path.exists():
        return {}
    overrides: dict[date, DayOverride] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                day = date.fromisoformat(str(row.get("date", "")).strip())
            except ValueError:
                continue
            weekday_default = day.weekday() < 5
            overrides[day] = DayOverride(
                a_open=_as_bool(row.get("a_open"), weekday_default),
                h_open=_as_bool(row.get("h_open"), weekday_default),
                note=str(row.get("note") or ""),
            )
    return overrides


def trading_day_flags(day: date, overrides: dict[date, DayOverride] | None = None) -> tuple[bool, bool, str]:
    overrides = overrides if overrides is not None else load_calendar_overrides()
    if day in overrides:
        item = overrides[day]
        return item.a_open, item.h_open, item.note
    weekday_open = day.weekday() < 5
    return weekday_open, weekday_open, "weekday-default"


def _state(
    code: str,
    label: str,
    a_open: bool,
    h_open: bool,
    fx_open: bool,
    watchlist: int,
    priority: int,
    universe: int,
    store: int,
    premium_mode: str,
    a_session: str,
    h_session: str,
    a_day: bool,
    h_day: bool,
) -> MarketState:
    return MarketState(
        code, label, a_open, h_open, fx_open,
        watchlist, priority, universe, store,
        premium_mode, a_session, h_session, a_day, h_day,
    )


def _continuous_state(
    *,
    suffix: str,
    a_day: bool,
    h_day: bool,
    a_session: str = "open",
    h_session: str = "open",
) -> MarketState:
    """State during a clock window in which the eligible market(s) can trade."""
    if a_day and h_day:
        return _state(
            f"BOTH_{suffix}", "A/H同时交易", True, True, True,
            5, 15, 60, 30, "realtime_sync", a_session, h_session, a_day, h_day,
        )
    if a_day:
        return _state(
            f"A_ONLY_{suffix}", "仅A股交易", True, False, True,
            10, 20, 60, 30, "indicative_single_leg", a_session, "holiday", a_day, h_day,
        )
    if h_day:
        return _state(
            f"H_ONLY_{suffix}", "仅H股交易", False, True, True,
            10, 20, 60, 30, "indicative_single_leg", "holiday", h_session, a_day, h_day,
        )
    return _state(
        "CLOSED", "两地休市", False, False, False,
        0, 0, 0, 300, "previous_close", "holiday", "holiday", a_day, h_day,
    )


def get_market_state(now: datetime | None = None, overrides: dict[date, DayOverride] | None = None) -> MarketState:
    """Return the A/H market state and the economic interpretation of A/H premium.

    Formal real-time comparability exists only while both markets trade continuously.
    One-sided windows remain calculable as *indicative* premiums, while lunch, pre-open
    and post-close states are explicitly labelled reference snapshots instead of being
    misrepresented as synchronized live prices.
    """
    now = now.astimezone(TZ) if now is not None else datetime.now(TZ)
    a_day, h_day, _ = trading_day_flags(now.date(), overrides)
    current = now.time().replace(tzinfo=None)

    if not a_day and not h_day:
        return _state(
            "CLOSED", "两地休市", False, False, False,
            0, 0, 0, 300, "previous_close", "holiday", "holiday", a_day, h_day,
        )

    # Before formal continuous trading, retain the previous completed daily A/H close.
    # Opening-auction / pre-open indications are deliberately not treated as real-time
    # comparable prices in v5.1.6.
    if current < time(8, 50):
        return _state(
            "CLOSED", "非交易时段", False, False, False,
            0, 0, 0, 300, "previous_close",
            "closed" if a_day else "holiday", "closed" if h_day else "holiday", a_day, h_day,
        )
    if time(8, 50) <= current < time(9, 30):
        return _state(
            "PRE_OPEN", "盘前 · 上一收盘参考", False, False, False,
            30, 60, 120, 60, "previous_close",
            "pre_open" if a_day else "holiday", "pre_open" if h_day else "holiday", a_day, h_day,
        )

    if time(9, 30) <= current < time(11, 30):
        return _continuous_state(suffix="MORNING", a_day=a_day, h_day=h_day)

    # A share is in lunch break; H share continues until noon when it is a trading day.
    if time(11, 30) <= current < time(12, 0):
        if h_day:
            return _state(
                "H_ONLY_MORNING", "H股单边交易 · A股午休", False, True, True,
                10, 20, 60, 30, "indicative_single_leg",
                "break" if a_day else "holiday", "open", a_day, h_day,
            )
        return _state(
            "BOTH_BREAK", "两市暂停", False, False, True,
            120, 180, 300, 300, "lunch_snapshot",
            "break" if a_day else "holiday", "holiday", a_day, h_day,
        )

    if time(12, 0) <= current < time(13, 0):
        return _state(
            "LUNCH", "两市午间休市", False, False, True,
            120, 180, 300, 300, "lunch_snapshot",
            "break" if a_day else "holiday", "break" if h_day else "holiday", a_day, h_day,
        )

    if time(13, 0) <= current < time(14, 57):
        return _continuous_state(suffix="AFTERNOON", a_day=a_day, h_day=h_day)

    # A-share closing call auction (14:57-15:00) has a different price-formation
    # mechanism from continuous trading. Keep calculating, but label it indicative.
    if time(14, 57) <= current < time(15, 0):
        if a_day and h_day:
            return _state(
                "CLOSING_AUCTION", "A股收盘竞价 · H股连续交易", True, True, True,
                5, 15, 60, 30, "auction_indicative", "auction", "open", a_day, h_day,
            )
        if a_day:
            return _state(
                "A_ONLY_AUCTION", "A股收盘竞价 · H股休市", True, False, True,
                10, 20, 60, 30, "indicative_single_leg", "auction", "holiday", a_day, h_day,
            )
        if h_day:
            return _state(
                "H_ONLY_AFTERNOON", "仅H股交易", False, True, True,
                10, 20, 60, 30, "indicative_single_leg", "holiday", "open", a_day, h_day,
            )

    # A share is closed while H share remains in continuous/CAS trading.
    if time(15, 0) <= current < time(16, 10):
        if h_day:
            return _state(
                "H_ONLY_AFTERNOON", "H股单边交易 · A股已收盘", False, True, True,
                10, 20, 60, 30, "indicative_single_leg",
                "closed" if a_day else "holiday", "open", a_day, h_day,
            )
        return _state(
            "A_CLOSED_H_HOLIDAY", "A股已收盘 · H股休市", False, False, True,
            60, 120, 300, 60, "indicative_close",
            "closed" if a_day else "holiday", "holiday", a_day, h_day,
        )

    # Brief post-close window: refresh both legs once more so the database can capture
    # the day's final official/frozen prices. When only one market traded that day,
    # the result remains an indicative close rather than a synchronized close.
    if time(16, 10) <= current < time(16, 30):
        mode = "close" if a_day and h_day else "indicative_close"
        label = "两市已收盘 · 日终口径" if a_day and h_day else "单边交易日 · 日终参考"
        return _state(
            "POST_CLOSE", label, False, False, True,
            60, 120, 300, 60, mode,
            "closed" if a_day else "holiday", "closed" if h_day else "holiday", a_day, h_day,
        )

    # After both markets close, keep a low-frequency close refresh. Public quote
    # providers normally retain the day's final frozen quote, including OHLC, volume
    # and turnover. This also lets a terminal started after the close populate the
    # current day's final quote instead of falling back to an incomplete cache row.
    mode = "close" if a_day and h_day else "indicative_close"
    return _state(
        "CLOSED", "两市已收盘", False, False, True,
        300, 600, 900, 300, mode,
        "closed" if a_day else "holiday", "closed" if h_day else "holiday", a_day, h_day,
    )
