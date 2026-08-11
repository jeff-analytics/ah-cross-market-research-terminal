from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.analysis.screener import build_screener
from src.data.loader import load_prices
from src.reporting.card import build_explanation_card

prices = load_prices()
screen, histories = build_screener(prices)
assert not screen.empty
row = screen.iloc[0]
card = build_explanation_card(row, histories[row["company_id"]])
assert "异常解释卡" in card.markdown
print("Smoke test passed")
print(screen[["company_name", "a_premium_pct", "premium_change_pp", "anomaly_level"]].head())
