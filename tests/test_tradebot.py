"""Comprehensive unit & integration tests for TradeBot."""

from __future__ import annotations

import asyncio
from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.ai_advisor import AIAdvisor, RecommendationCard
from backend.app import app
from backend.moomoo_client import (
    MoomooPaperClient,
    PaperTradingGuardrailError,
    TrdEnv,
    assert_simulate_env,
)
from backend.risk_manager import RiskManager
from backtester.backtest_engine import BacktestEngine
from config.settings import Settings
from skills.social_research_skills import (
    SocialItem,
    deduplicate_items,
    is_noise_or_spam,
    make_item_hash,
    score_sentiment,
)


def _make_synthetic_ohlcv(n: int = 260, drift: float = 0.0005, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n)
    rets = rng.normal(drift, 0.015, size=n)
    close = 100.0 * np.cumprod(1.0 + rets)
    high = close * (1.0 + rng.uniform(0.001, 0.015, size=n))
    low = close * (1.0 - rng.uniform(0.001, 0.015, size=n))
    open_p = close * (1.0 + rng.normal(0.0, 0.005, size=n))
    vol = rng.integers(500_000, 3_000_000, size=n)
    return pd.DataFrame(
        {
            "date": dates,
            "open": open_p,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
        }
    )


def test_simulate_guardrail_enforced() -> None:
    assert assert_simulate_env(TrdEnv.SIMULATE) == TrdEnv.SIMULATE
    with pytest.raises(PaperTradingGuardrailError):
        assert_simulate_env("REAL")

    client = MoomooPaperClient(Settings(duckdb_path="data/test_tradebot.duckdb"))
    with pytest.raises(PaperTradingGuardrailError):
        client.place_order("US.AAPL", "BUY", 1, price=200.0, trd_env="REAL")
    with pytest.raises(PaperTradingGuardrailError):
        client.modify_order("ORD-1", 1, 200.0, trd_env="REAL")
    with pytest.raises(PaperTradingGuardrailError):
        client.cancel_order("ORD-1", trd_env="REAL")


def test_risk_manager_rules_and_circuit_breaker() -> None:
    rm = RiskManager(Settings())
    acc = {"nav": 100_000.0, "cash": 100_000.0, "buying_power": 100_000.0, "day_pnl_pct": 0.0}
    positions: list[dict] = []

    # Missing stop-loss rejected
    res_no_sl = rm.validate_order("US.AAPL", "BUY", 10, 200.0, None, acc, positions)
    assert not res_no_sl.approved

    # Exceeds 5% ($5,000) position cap rejected
    res_oversize = rm.validate_order("US.AAPL", "BUY", 30, 200.0, 190.0, acc, positions)
    assert not res_oversize.approved

    # Valid 4% ($4,000) order approved
    res_ok = rm.validate_order("US.AAPL", "BUY", 20, 200.0, 190.0, acc, positions)
    assert res_ok.approved

    # -2.5% daily loss trips circuit breaker
    tripped_acc = {**acc, "day_pnl_pct": -2.5}
    res_cb = rm.validate_order("US.AAPL", "BUY", 10, 200.0, 190.0, tripped_acc, positions)
    assert not res_cb.approved
    assert "CIRCUIT BREAKER" in res_cb.reason


def test_backtest_engine_and_gatekeeper() -> None:
    settings = Settings(min_sharpe_ratio=1.2, max_drawdown_pct=0.15)
    engine = BacktestEngine(settings=settings)
    df = _make_synthetic_ohlcv(300, drift=0.0002, seed=7)
    bench = _make_synthetic_ohlcv(300, drift=0.0003, seed=99)

    result = engine.run_backtest(
        ticker="US.NVDA",
        strategy_name="rsi_macd_momentum",
        benchmark="US.SPY",
        df_override=df,
        bench_override=bench,
    )
    assert result.ticker == "US.NVDA"
    assert len(result.equity_curve) == len(df)
    assert isinstance(result.passed_gatekeeper, bool)
    assert result.gatekeeper_reason


def test_social_deduplication_and_spam_filter() -> None:
    assert is_noise_or_spam("Join my telegram for guaranteed 100x crypto airdrop")
    assert not is_noise_or_spam("NVIDIA beats Q3 earnings expectations with record data center revenue")
    assert score_sentiment("Strong breakout and upgrade with record profit growth") > 0
    assert score_sentiment("Weak guidance, downgrade, and lawsuit investigation") < 0

    now = datetime.utcnow()
    i1 = SocialItem(
        item_hash=make_item_hash("reddit", "NVDA", "NVDA beats earnings", "https://reddit.com/1"),
        ts=now,
        platform="reddit/r/stocks",
        ticker="NVDA",
        author="user1",
        title="NVDA beats earnings",
        content="NVIDIA reported strong data center growth and raised guidance.",
        url="https://reddit.com/1",
        engagement=50.0,
        sentiment_score=0.7,
    )
    i2 = SocialItem(
        item_hash=make_item_hash("x_twitter", "NVDA", "NVDA beats earnings!", "https://x.com/2"),
        ts=now,
        platform="x_twitter",
        ticker="NVDA",
        author="user2",
        title="NVDA beats earnings!",
        content="NVIDIA reported strong data center growth and raised guidance.",
        url="https://x.com/2",
        engagement=20.0,
        sentiment_score=0.7,
    )
    deduped = deduplicate_items([i1, i2])
    assert len(deduped) == 1


def test_ai_advisor_recommendation_schema() -> None:
    advisor = AIAdvisor(Settings(llm_provider="local_quant"))
    technicals = {
        "price": 140.0,
        "rsi_14": 58.0,
        "atr_14": 3.5,
        "vol_ratio": 1.8,
        "technical_score": 0.65,
        "triggers": ["MACD bullish crossover above signal line"],
    }
    intel = {
        "social_score": 0.4,
        "catalyst_score": 0.6,
        "items": [{"platform": "yahoo_finance", "title": "NVDA announces next-gen GPU"}],
    }
    card = asyncio.run(advisor.generate_recommendation("US.NVDA", technicals, intel, backtest_win_prob=0.62))
    assert isinstance(card, RecommendationCard)
    assert card.verdict in ("STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL")
    assert len(card.thesis) == 3
    assert card.trade_plan.stop_loss < card.trade_plan.entry_low


def test_fastapi_endpoints() -> None:
    with TestClient(app) as client:
        r_health = client.get("/health")
        assert r_health.status_code == 200
        assert r_health.json()["trd_env"] == "SIMULATE"

        r_port = client.get("/api/portfolio")
        assert r_port.status_code == 200
        assert r_port.json()["account"]["trd_env"] == "SIMULATE"

        r_mode = client.post("/api/settings/mode", json={"mode": "FULL_AUTO"})
        assert r_mode.status_code == 200
        assert r_mode.json()["execution_mode"] == "FULL_AUTO"

        r_mode_back = client.post("/api/settings/mode", json={"mode": "SEMI_AUTO"})
        assert r_mode_back.status_code == 200
