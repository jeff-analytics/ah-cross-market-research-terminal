from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.config import (
    BOOTSTRAP_PAIRS_FILE,
    PAIRS_FILE,
    SETTINGS,
    UNIVERSE_HISTORY_FILE,
    UNIVERSE_LOG_FILE,
    UNIVERSE_SNAPSHOT_FILE,
)

REQUIRED_COLUMNS = {
    "company_id", "company_name", "a_code", "h_code", "a_ticker", "h_ticker",
    "industry", "status", "source",
}


def _clean_code(value: object, width: int) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        text = str(int(value))
    else:
        raw = str(value).strip()
        if re.fullmatch(r"\d+\.0+", raw):
            raw = raw.split(".", 1)[0]
        text = re.sub(r"\D", "", raw)
    return text.zfill(width)[-width:] if text else ""


def _a_exchange(code: str) -> str:
    return "SH" if code.startswith(("5", "6", "9")) else "SZ"


def _a_ticker(code: str) -> str:
    return f"{code}.SS" if _a_exchange(code) == "SH" else f"{code}.SZ"


def _h_ticker(code: str) -> str:
    return f"{code}.HK"


def infer_industry(name: str) -> str:
    rules: list[tuple[str, tuple[str, ...]]] = [
        ("银行", ("银行",)),
        ("保险", ("保险", "人寿", "人保", "太保", "平安")),
        ("证券与金融", ("证券", "中金", "期货", "申万", "国泰海通")),
        ("医药医疗", ("医药", "生物", "医疗", "制药", "康龙", "药明", "康希诺", "百济", "荣昌", "昭衍", "凯莱英", "复旦张江", "昊海", "春立")),
        ("能源电力", ("石油", "石化", "神华", "煤", "电力", "发电", "华能", "华电", "龙源", "绿能", "海油", "兖矿", "中广核")),
        ("有色与材料", ("铜", "铝", "黄金", "钢铁", "水泥", "玻璃", "钼", "锂", "磁", "新材", "材料", "金隅", "福莱特")),
        ("交通运输", ("铁路", "高速", "港", "航空", "东航", "国航", "外运", "海控", "海能", "海发", "辽港", "交建")),
        ("汽车与装备", ("汽车", "比亚迪", "中车", "机械", "电气", "中联", "三一", "潍柴", "郑煤机", "世宝", "赛力斯", "埃斯顿", "先导")),
        ("科技电子", ("科技", "通信", "电信", "移动", "中兴", "芯", "微电", "半导体", "光纤", "智能", "数控", "澜起", "兆易", "蓝思", "华虹", "中芯", "广和通", "旭创")),
        ("消费", ("食品", "饮料", "啤酒", "家电", "免税", "牧原", "海天", "安井", "东鹏", "美凯龙", "海尔", "美的")),
        ("房地产与公用", ("地产", "万科", "北辰", "公用", "环保", "创业环保")),
        ("建筑工程", ("中铁", "铁建", "中冶", "能建", "中铝国际", "上海电气", "东方电气")),
    ]
    clean = str(name).replace(" ", "")
    for industry, keywords in rules:
        if any(keyword in clean for keyword in keywords):
            return industry
    return "综合"



def _snapshot_metadata() -> dict:
    if UNIVERSE_SNAPSHOT_FILE.exists():
        try:
            payload = json.loads(UNIVERSE_SNAPSHOT_FILE.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
    return {"snapshot_date": None, "active_companies": SETTINGS.expected_universe_count}


def _validated_snapshot_source() -> str:
    date_text = str(_snapshot_metadata().get("snapshot_date") or "undated")
    return f"validated_snapshot_{date_text}"


def normalize_registry(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    aliases = {
        "H股代码": "h_code", "A股代码": "a_code", "名称": "company_name",
        "hk_code": "h_code", "name": "company_name", "A股证券代码": "a_code",
        "H股证券代码": "h_code",
    }
    frame = frame.rename(columns=aliases).copy()
    required = {"h_code", "a_code", "company_name"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Universe source missing columns: {sorted(missing)}")
    frame["h_code"] = frame["h_code"].map(lambda value: _clean_code(value, 5))
    frame["a_code"] = frame["a_code"].map(lambda value: _clean_code(value, 6))
    frame["company_name"] = frame["company_name"].astype(str).str.replace(r"\s+", "", regex=True)
    frame = frame[(frame["h_code"].str.len() == 5) & (frame["a_code"].str.len() == 6)]
    frame = frame.drop_duplicates("a_code", keep="first").drop_duplicates("h_code", keep="first")
    if "status" not in frame:
        frame["status"] = "active"
    frame["status"] = frame["status"].fillna("active")
    frame["source"] = source
    frame["company_id"] = "AH_" + frame["a_code"] + "_" + frame["h_code"]
    frame["a_exchange"] = frame["a_code"].map(_a_exchange)
    frame["a_ticker"] = frame["a_code"].map(_a_ticker)
    frame["h_ticker"] = frame["h_code"].map(_h_ticker)
    frame["industry"] = frame["company_name"].map(infer_industry)
    frame["updated_at"] = datetime.now(timezone.utc).isoformat()
    columns = [
        "company_id", "company_name", "a_code", "h_code", "a_exchange",
        "a_ticker", "h_ticker", "industry", "status", "source", "updated_at",
    ]
    return frame[columns].sort_values(["status", "company_name"]).reset_index(drop=True)


def bootstrap_registry(force: bool = False) -> pd.DataFrame:
    if PAIRS_FILE.exists() and not force:
        raw = pd.read_csv(PAIRS_FILE, dtype=str)
        if REQUIRED_COLUMNS.issubset(raw.columns):
            return raw
        registry = normalize_registry(raw, _validated_snapshot_source())
        registry.to_csv(PAIRS_FILE, index=False, encoding="utf-8-sig")
        return registry
    if not BOOTSTRAP_PAIRS_FILE.exists():
        raise FileNotFoundError(f"Missing bootstrap registry: {BOOTSTRAP_PAIRS_FILE}")
    raw = pd.read_csv(BOOTSTRAP_PAIRS_FILE, dtype=str)
    registry = normalize_registry(raw, _validated_snapshot_source())
    registry.to_csv(PAIRS_FILE, index=False, encoding="utf-8-sig")
    return registry


def load_pairs(active_only: bool = True) -> pd.DataFrame:
    if not PAIRS_FILE.exists():
        bootstrap_registry()
    pairs = pd.read_csv(PAIRS_FILE, dtype=str)
    missing = REQUIRED_COLUMNS.difference(pairs.columns)
    if missing:
        pairs = normalize_registry(pairs, "local_upgraded_registry")
        pairs.to_csv(PAIRS_FILE, index=False, encoding="utf-8-sig")
    if pairs["company_id"].duplicated().any() or pairs["a_code"].duplicated().any() or pairs["h_code"].duplicated().any():
        raise ValueError("Pair registry contains duplicate company/A-share/H-share identifiers")
    if active_only:
        pairs = pairs[pairs["status"].str.lower().eq("active")]
    return pairs.sort_values("company_name").reset_index(drop=True)


def _append_history(count: int, source: str, status: str, note: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    meta = _snapshot_metadata()
    history: dict = {"validated_snapshot": {"date": meta.get("snapshot_date"), "companies": int(meta.get("active_companies") or SETTINGS.expected_universe_count)}, "snapshots": []}
    if UNIVERSE_HISTORY_FILE.exists():
        try:
            prior = json.loads(UNIVERSE_HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(prior, dict):
                history.update(prior)
        except Exception:
            pass
    snapshots = history.get("snapshots") if isinstance(history.get("snapshots"), list) else []
    snapshots.append({"updated_at": now, "companies": int(count), "source": source, "status": status, "note": note})
    history["snapshots"] = snapshots[-100:]
    history["current_registry"] = snapshots[-1]
    UNIVERSE_HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def universe_status() -> dict[str, object]:
    pairs = load_pairs(active_only=True)
    active = int(len(pairs))
    log: dict = {}
    if UNIVERSE_LOG_FILE.exists():
        try:
            log = json.loads(UNIVERSE_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            log = {}
    return {
        "target_count": SETTINGS.expected_universe_count,
        "active_count": active,
        "full_ready": active >= SETTINGS.expected_universe_count,
        "source": log.get("source") or (pairs["source"].iloc[0] if not pairs.empty else "unknown"),
        "updated_at": log.get("updated_at"),
        "status": log.get("status") or "local",
        "error": log.get("error"),
    }


def sync_universe_from_eastmoney() -> dict[str, object]:
    """Refresh the current A/H universe through AKShare's Eastmoney comparison adapter.

    The bundled registry is a validated release snapshot. A remote response may
    expand it, but a smaller response is never allowed to silently shrink a known-good
    local universe. This guards against pagination, anti-bot and temporary failures.
    """
    started = datetime.now(timezone.utc)
    current = bootstrap_registry(force=False)
    current_active = int(current["status"].str.lower().eq("active").sum())
    try:
        import akshare as ak
        raw = ak.stock_zh_ah_spot_em()
        registry = normalize_registry(raw, "eastmoney_ah_comparison")
        registry = registry[registry["status"].str.lower().eq("active")].copy()
        remote_count = len(registry)
        if remote_count < SETTINGS.minimum_valid_universe:
            raise RuntimeError(f"Eastmoney returned only {remote_count} valid A/H pairs")
        if remote_count < current_active:
            raise RuntimeError(
                f"Remote universe {remote_count} is below current validated registry {current_active}; current registry retained"
            )
        backup = PAIRS_FILE.with_name("ah_pairs.previous.csv")
        if PAIRS_FILE.exists():
            PAIRS_FILE.replace(backup)
        registry.to_csv(PAIRS_FILE, index=False, encoding="utf-8-sig")
        result: dict[str, object] = {
            "status": "success", "updated_at": datetime.now(timezone.utc).isoformat(),
            "companies": remote_count, "target_count": SETTINGS.expected_universe_count,
            "full_ready": remote_count >= SETTINGS.expected_universe_count,
            "source": "eastmoney_ah_comparison",
            "elapsed_seconds": (datetime.now(timezone.utc) - started).total_seconds(),
        }
        _append_history(remote_count, "eastmoney_ah_comparison", "success", "Dynamic full-universe refresh succeeded.")
    except Exception as exc:
        # Preserve the complete current local registry. Never replace valid local
        # members with a smaller/error response.
        local = load_pairs(active_only=True)
        result = {
            "status": "retained", "updated_at": datetime.now(timezone.utc).isoformat(),
            "companies": int(len(local)), "target_count": SETTINGS.expected_universe_count,
            "full_ready": len(local) >= SETTINGS.expected_universe_count,
            "source": str(local["source"].iloc[0]) if not local.empty else _validated_snapshot_source(),
            "error": str(exc),
            "elapsed_seconds": (datetime.now(timezone.utc) - started).total_seconds(),
        }
        _append_history(len(local), str(result["source"]), "retained", "Online sync failed or was incomplete; validated full registry retained.")
    UNIVERSE_LOG_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
