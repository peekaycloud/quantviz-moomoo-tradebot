"""Vectorized strategy definitions for walk-forward backtesting."""

from __future__ import annotations

import pandas as pd

from strategies.indicators import compute_technical_indicators

AVAILABLE_STRATEGIES = {
    "rsi_macd_momentum": "RSI + MACD Momentum Crossover",
    "bb_mean_reversion": "Bollinger Band Mean Reversion",
    "ema_trend_following": "EMA 20/50 Trend Following",
}


def generate_strategy_signals(df: pd.DataFrame, strategy_name: str = "rsi_macd_momentum") -> pd.DataFrame:
    """Return DataFrame with boolean 'entry' and 'exit' signal columns."""
    data = compute_technical_indicators(df)
    if data.empty:
        data["entry"] = pd.Series(dtype=bool)
        data["exit"] = pd.Series(dtype=bool)
        return data

    if strategy_name == "rsi_macd_momentum":
        entry = (data["macd"] > data["macd_signal"]) & (data["rsi_14"] > 45) & (data["rsi_14"] < 72)
        exit_sig = (data["macd"] < data["macd_signal"]) | (data["rsi_14"] > 78)
    elif strategy_name == "bb_mean_reversion":
        entry = (data["close"] < data["bb_lower"]) & (data["rsi_14"] < 35)
        exit_sig = (data["close"] > data["bb_mid"]) | (data["rsi_14"] > 65)
    elif strategy_name == "ema_trend_following":
        entry = (data["ema_20"] > data["ema_50"]) & (data["close"] > data["ema_20"])
        exit_sig = (data["ema_20"] < data["ema_50"]) | (data["close"] < data["ema_50"])
    else:
        raise ValueError(f"Unknown strategy: {strategy_name}. Choose from {list(AVAILABLE_STRATEGIES)}")

    data["entry"] = entry.fillna(False).astype(bool)
    data["exit"] = exit_sig.fillna(False).astype(bool)
    return data
