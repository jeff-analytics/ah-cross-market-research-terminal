from src.analysis.historical import find_similar_events
from src.analysis.screener import build_screener
from src.data.demo import generate_demo_data
from src.reporting.card import build_explanation_card


def test_end_to_end_pipeline():
    prices = generate_demo_data()
    screen, histories = build_screener(prices)
    assert len(screen) >= 100
    assert {"company_residual_pp", "comparability_score", "severity_score"}.issubset(screen.columns)
    row = screen.iloc[0]
    card = build_explanation_card(row, histories[row["company_id"]])
    assert "变化来源" in card.markdown


def test_historical_analogs_available():
    prices = generate_demo_data()
    screen, histories = build_screener(prices)
    company_id = screen.iloc[0]["company_id"]
    analogs = find_similar_events(histories[company_id])
    assert not analogs.empty
