"""Technical indicators: RSI, MACD, Bollinger Bands, ATR, and Volume Profile."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def compute_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute RSI(14), MACD(12,26,9), Bollinger Bands(20,2), ATR(14), and Volume metrics."""
    if df.empty:
        return df.copy()

    out = df.copy()
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    volume = out["volume"].astype(float)

    # RSI (14)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))

    # MACD (12, 26, 9)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["macd"] = ema12 - ema26
    out["macd_signal"] = out["macd"].ewm(span=9, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]

    # Bollinger Bands (20, 2)
    sma20 = close.rolling(window=20, min_periods=20).mean()
    std20 = close.rolling(window=20, min_periods=20).std()
    out["bb_mid"] = sma20
    out["bb_upper"] = sma20 + 2.0 * std20
    out["bb_lower"] = sma20 - 2.0 * std20
    out["bb_pct_b"] = (close - out["bb_lower"]) / (out["bb_upper"] - out["bb_lower"]).replace(0.0, np.nan)

    # ATR (14)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr_14"] = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()

    # EMAs & Volume Profile
    out["ema_20"] = close.ewm(span=20, adjust=False).mean()
    out["ema_50"] = close.ewm(span=50, adjust=False).mean()
    out["vol_sma_20"] = volume.rolling(window=20, min_periods=1).mean()
    out["vol_ratio"] = volume / out["vol_sma_20"].replace(0.0, np.nan)
    out["vwap_20"] = (close * volume).rolling(window=20, min_periods=1).sum() / volume.rolling(window=20, min_periods=1).sum().replace(0.0, np.nan)

    return out


def summarize_technicals(df: pd.DataFrame) -> dict[str, Any]:
    """Extract latest technical snapshot and normalized [-1, +1] technical score."""
    if df.empty or len(df) < 20:
        return {
            "price": 0.0,
            "rsi_14": 50.0,
            "macd": 0.0,
            "macd_signal": 0.0,
            "macd_hist": 0.0,
            "bb_pct_b": 0.5,
            "atr_14": 0.0,
            "vol_ratio": 1.0,
            "technical_score": 0.0,
            "triggers": ["Insufficient historical bars for technicals."],
        }

    enriched = compute_technical_indicators(df)
    last = enriched.iloc[-1]
    price = float(last["close"])
    rsi = float(last["rsi_14"]) if pd.notna(last["rsi_14"]) else 50.0
    macd = float(last["macd"]) if pd.notna(last["macd"]) else 0.0
    macd_sig = float(last["macd_signal"]) if pd.notna(last["macd_signal"]) else 0.0
    macd_hist = float(last["macd_hist"]) if pd.notna(last["macd_hist"]) else 0.0
    bb_pct_b = float(last["bb_pct_b"]) if pd.notna(last["bb_pct_b"]) else 0.5
    atr = float(last["atr_14"]) if pd.notna(last["atr_14"]) else price * 0.02
    vol_ratio = float(last["vol_ratio"]) if pd.notna(last["vol_ratio"]) else 1.0
    ema20 = float(last["ema_20"]) if pd.notna(last["ema_20"]) else price
    ema50 = float(last["ema_50"]) if pd.notna(last["ema_50"]) else price

    score = 0.0
    triggers: list[str] = []

    if rsi < 35:
        score += 0.30
        triggers.append(f"RSI(14) oversold at {rsi:.1f}")
    elif rsi > 70:
        score -= 0.30
        triggers.append(f"RSI(14) overbought at {rsi:.1f}")
    elif 45 <= rsi <= 65:
        score += 0.10
        triggers.append(f"RSI(14) healthy momentum at {rsi:.1f}")

    if macd > macd_sig and macd_hist > 0:
        score += 0.30
        triggers.append("MACD bullish crossover above signal line")
    elif macd < macd_sig and macd_hist < 0:
        score -= 0.30
        triggers.append("MACD bearish momentum below signal line")

    if ema20 > ema50 and price > ema20:
        score += 0.25
        triggers.append("Price trading above rising EMA20 > EMA50")
    elif ema20 < ema50 and price < ema20:
        score -= 0.25
        triggers.append("Price trading below bearish EMA20 < EMA50")

    if vol_ratio >= 1.5:
        score += 0.15 if score >= 0 else -0.15
        triggers.append(f"Elevated volume spike ({vol_ratio:.2f}x 20D avg)")

    score = max(-1.0, min(1.0, round(score, 3)))
    return {
        "price": round(price, 4),
        "rsi_14": round(rsi, 2),
        "macd": round(macd, 4),
        "macd_signal": round(macd_sig, 4),
        "macd_hist": round(macd_hist, 4),
        "bb_pct_b": round(bb_pct_b, 3),
        "atr_14": round(atr, 4),
        "vol_ratio": round(vol_ratio, 2),
        "ema_20": round(ema20, 4),
        "ema_50": round(ema50, 4),
        "technical_score": score,
        "triggers": triggers or ["Neutral technical consolidation"],
    }


def _rate_counts(buy: int, neutral: int, sell: int) -> dict[str, Any]:
    total = max(1, buy + neutral + sell)
    score = (buy - sell) / total
    if score >= 0.5:
        rating = "Strong Buy"
    elif score >= 0.15:
        rating = "Buy"
    elif score <= -0.5:
        rating = "Strong Sell"
    elif score <= -0.15:
        rating = "Sell"
    else:
        rating = "Neutral"
    return {
        "buy": buy,
        "neutral": neutral,
        "sell": sell,
        "score": round(score, 3),
        "rating": rating,
    }


def compute_tradingview_technicals(
    df: pd.DataFrame,
    live_price: float | None = None,
    timeframe: str = "1D",
) -> dict[str, Any]:
    """Compute TradingView-style Oscillators, Moving Averages, and Gauge summaries."""
    if df.empty or len(df) < 30:
        empty_sum = _rate_counts(0, 1, 0)
        return {
            "timeframe": timeframe,
            "price": live_price or 0.0,
            "oscillators": [],
            "moving_averages": [],
            "oscillators_summary": empty_sum,
            "moving_averages_summary": empty_sum,
            "overall_summary": empty_sum,
        }

    work = df.copy()
    work["date"] = pd.to_datetime(work["date"])
    if timeframe == "1W":
        work = (
            work.set_index("date")
            .resample("W-FRI")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna()
            .reset_index()
        )
        if len(work) < 15:
            work = df.copy()

    if live_price and live_price > 0:
        work.loc[work.index[-1], "close"] = float(live_price)
        work.loc[work.index[-1], "high"] = max(float(work.iloc[-1]["high"]), float(live_price))
        work.loc[work.index[-1], "low"] = min(float(work.iloc[-1]["low"]), float(live_price))

    enriched = compute_technical_indicators(work)
    close = enriched["close"].astype(float)
    high = enriched["high"].astype(float)
    low = enriched["low"].astype(float)
    price = float(close.iloc[-1])

    # Stochastic %K (14, 3, 3)
    low14 = low.rolling(14).min()
    high14 = high.rolling(14).max()
    stoch_k = 100.0 * (close - low14) / (high14 - low14).replace(0.0, np.nan)
    stoch_k_smooth = stoch_k.rolling(3).mean()
    val_stoch = float(stoch_k_smooth.iloc[-1]) if pd.notna(stoch_k_smooth.iloc[-1]) else 50.0

    # CCI (20)
    tp = (high + low + close) / 3.0
    tp_sma = tp.rolling(20).mean()
    mad = (tp - tp_sma).abs().rolling(20).mean()
    cci = (tp - tp_sma) / (0.015 * mad.replace(0.0, np.nan))
    val_cci = float(cci.iloc[-1]) if pd.notna(cci.iloc[-1]) else 0.0

    # ADX (14) + DI
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    atr14 = enriched["atr_14"].replace(0.0, np.nan)
    plus_di = 100.0 * pd.Series(plus_dm, index=work.index).ewm(alpha=1 / 14, adjust=False).mean() / atr14
    minus_di = 100.0 * pd.Series(minus_dm, index=work.index).ewm(alpha=1 / 14, adjust=False).mean() / atr14
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx = dx.ewm(alpha=1 / 14, adjust=False).mean()
    val_adx = float(adx.iloc[-1]) if pd.notna(adx.iloc[-1]) else 20.0
    val_pdi = float(plus_di.iloc[-1]) if pd.notna(plus_di.iloc[-1]) else 20.0
    val_mdi = float(minus_di.iloc[-1]) if pd.notna(minus_di.iloc[-1]) else 20.0

    # Awesome Oscillator
    mid = (high + low) / 2.0
    ao = mid.rolling(5).mean() - mid.rolling(34).mean()
    val_ao = float(ao.iloc[-1]) if pd.notna(ao.iloc[-1]) else 0.0

    # Momentum (10)
    mom = close - close.shift(10)
    val_mom = float(mom.iloc[-1]) if pd.notna(mom.iloc[-1]) else 0.0

    # Williams %R (14)
    willr = -100.0 * (high14 - close) / (high14 - low14).replace(0.0, np.nan)
    val_willr = float(willr.iloc[-1]) if pd.notna(willr.iloc[-1]) else -50.0

    val_rsi = float(enriched["rsi_14"].iloc[-1]) if pd.notna(enriched["rsi_14"].iloc[-1]) else 50.0
    val_macd = float(enriched["macd"].iloc[-1]) if pd.notna(enriched["macd"].iloc[-1]) else 0.0
    val_macd_sig = float(enriched["macd_signal"].iloc[-1]) if pd.notna(enriched["macd_signal"].iloc[-1]) else 0.0

    oscillators = [
        {
            "name": "Relative Strength Index (14)",
            "value": round(val_rsi, 2),
            "action": "Buy" if val_rsi < 30 else ("Sell" if val_rsi > 70 else "Neutral"),
        },
        {
            "name": "Stochastic %K (14, 3, 3)",
            "value": round(val_stoch, 2),
            "action": "Buy" if val_stoch < 20 else ("Sell" if val_stoch > 80 else "Neutral"),
        },
        {
            "name": "Commodity Channel Index (20)",
            "value": round(val_cci, 2),
            "action": "Buy" if val_cci < -100 else ("Sell" if val_cci > 100 else "Neutral"),
        },
        {
            "name": "Average Directional Index (14)",
            "value": round(val_adx, 2),
            "action": "Buy" if (val_adx > 25 and val_pdi > val_mdi) else ("Sell" if (val_adx > 25 and val_mdi > val_pdi) else "Neutral"),
        },
        {
            "name": "Awesome Oscillator",
            "value": round(val_ao, 2),
            "action": "Buy" if val_ao > 0 else ("Sell" if val_ao < 0 else "Neutral"),
        },
        {
            "name": "Momentum (10)",
            "value": round(val_mom, 2),
            "action": "Buy" if val_mom > 0 else ("Sell" if val_mom < 0 else "Neutral"),
        },
        {
            "name": "MACD Level (12, 26)",
            "value": round(val_macd, 2),
            "action": "Buy" if val_macd > val_macd_sig else ("Sell" if val_macd < val_macd_sig else "Neutral"),
        },
        {
            "name": "Williams Percent Range (14)",
            "value": round(val_willr, 2),
            "action": "Buy" if val_willr < -80 else ("Sell" if val_willr > -20 else "Neutral"),
        },
    ]

    moving_averages: list[dict[str, Any]] = []
    for period in (10, 20, 50, 100, 200):
        ema_val = float(close.ewm(span=period, adjust=False).mean().iloc[-1])
        sma_series = close.rolling(period, min_periods=min(period, len(close))).mean()
        sma_val = float(sma_series.iloc[-1]) if pd.notna(sma_series.iloc[-1]) else price
        moving_averages.append(
            {
                "name": f"Exponential Moving Average ({period})",
                "value": round(ema_val, 2),
                "action": "Buy" if price > ema_val else ("Sell" if price < ema_val else "Neutral"),
            }
        )
        moving_averages.append(
            {
                "name": f"Simple Moving Average ({period})",
                "value": round(sma_val, 2),
                "action": "Buy" if price > sma_val else ("Sell" if price < sma_val else "Neutral"),
            }
        )

    vwap_val = float(enriched["vwap_20"].iloc[-1]) if pd.notna(enriched["vwap_20"].iloc[-1]) else price
    moving_averages.append(
        {
            "name": "Volume Weighted Avg Price (20)",
            "value": round(vwap_val, 2),
            "action": "Buy" if price > vwap_val else ("Sell" if price < vwap_val else "Neutral"),
        }
    )

    osc_buy = sum(1 for x in oscillators if x["action"] == "Buy")
    osc_neu = sum(1 for x in oscillators if x["action"] == "Neutral")
    osc_sell = sum(1 for x in oscillators if x["action"] == "Sell")

    ma_buy = sum(1 for x in moving_averages if x["action"] == "Buy")
    ma_neu = sum(1 for x in moving_averages if x["action"] == "Neutral")
    ma_sell = sum(1 for x in moving_averages if x["action"] == "Sell")

    return {
        "timeframe": timeframe,
        "price": round(price, 4),
        "oscillators": oscillators,
        "moving_averages": moving_averages,
        "oscillators_summary": _rate_counts(osc_buy, osc_neu, osc_sell),
        "moving_averages_summary": _rate_counts(ma_buy, ma_neu, ma_sell),
        "overall_summary": _rate_counts(osc_buy + ma_buy, osc_neu + ma_neu, osc_sell + ma_sell),
    }
