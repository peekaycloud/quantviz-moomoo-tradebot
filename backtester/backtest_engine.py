"""Automated Historical Backtesting & Strategy Gatekeeper Engine."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backend.moomoo_client import MoomooPaperClient, normalize_ticker, strip_market_prefix
from config.settings import Settings, get_settings
from strategies.base import generate_strategy_signals

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Structured output of a walk-forward strategy backtest."""

    run_id: str
    ticker: str
    strategy: str
    benchmark: str
    cagr: float
    sharpe: float
    sortino: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    total_trades: int
    win_probability: float
    passed_gatekeeper: bool
    gatekeeper_reason: str
    equity_curve: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BacktestEngine:
    """Ingests OHLCV to Parquet, runs friction-adjusted backtests, and enforces gatekeeper rules."""

    def __init__(
        self,
        moomoo_client: MoomooPaperClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = moomoo_client or MoomooPaperClient(self.settings)
        self.parquet_dir = Path(self.settings.parquet_dir)
        self.parquet_dir.mkdir(parents=True, exist_ok=True)

    def load_ohlcv(
        self,
        ticker: str,
        lookback_days: int = 730,
        ktype: str = "K_DAY",
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Fetch historical OHLCV via Moomoo/yfinance and persist to local Parquet."""
        code = normalize_ticker(ticker)
        sym = strip_market_prefix(code)
        parquet_path = self.parquet_dir / f"{sym}_{ktype}.parquet"

        if use_cache and parquet_path.exists():
            age_hours = (datetime.now().timestamp() - parquet_path.stat().st_mtime) / 3600.0
            if age_hours < 12:
                try:
                    cached = pd.read_parquet(parquet_path)
                    if not cached.empty and len(cached) >= 60:
                        return cached
                except Exception:
                    pass

        end_dt = datetime.now(timezone.utc).date()
        start_dt = end_dt - timedelta(days=lookback_days)
        df = self.client.fetch_historical_klines(
            code,
            start=start_dt.isoformat(),
            end=end_dt.isoformat(),
            ktype=ktype,
        )
        if not df.empty:
            df.to_parquet(parquet_path, index=False)
        return df

    def run_backtest(
        self,
        ticker: str,
        strategy_name: str = "rsi_macd_momentum",
        benchmark: str = "US.SPY",
        lookback_days: int = 730,
        initial_capital: float = 100_000.0,
        position_size_pct: float = 1.0,
        df_override: pd.DataFrame | None = None,
        bench_override: pd.DataFrame | None = None,
    ) -> BacktestResult:
        """Run friction-modeled walk-forward backtest and apply Auto-Gatekeeper."""
        code = normalize_ticker(ticker)
        df = df_override.copy() if df_override is not None else self.load_ohlcv(code, lookback_days=lookback_days)
        if df.empty or len(df) < 60:
            return BacktestResult(
                run_id=f"BT-{uuid.uuid4().hex[:8].upper()}",
                ticker=code,
                strategy=strategy_name,
                benchmark=normalize_ticker(benchmark),
                cagr=0.0,
                sharpe=0.0,
                sortino=0.0,
                max_drawdown=0.0,
                win_rate=0.0,
                profit_factor=0.0,
                total_trades=0,
                win_probability=0.5,
                passed_gatekeeper=False,
                gatekeeper_reason="Insufficient historical OHLCV bars (< 60).",
                equity_curve=[],
            )

        signals_df = generate_strategy_signals(df, strategy_name=strategy_name)

        # Benchmark data
        bench_df = (
            bench_override.copy()
            if bench_override is not None
            else self.load_ohlcv(benchmark, lookback_days=lookback_days)
        )

        slippage = self.settings.slippage_bps / 10_000.0
        commission = self.settings.commission_rate
        total_friction = slippage + commission

        cash = initial_capital
        shares = 0.0
        entry_price = 0.0
        trades_pnl: list[float] = []
        equity_series: list[float] = []
        dates: list[str] = []

        # Walk-forward execution: signal at bar t executes at close[t] (or next bar open)
        for i in range(len(signals_df)):
            row = signals_df.iloc[i]
            price = float(row["close"])
            date_str = str(pd.to_datetime(row["date"]).date())

            if shares == 0.0 and bool(row["entry"]):
                alloc = cash * min(1.0, max(0.05, position_size_pct))
                buy_price = price * (1.0 + total_friction)
                if buy_price > 0 and alloc > 0:
                    shares = alloc / buy_price
                    cash -= alloc
                    entry_price = buy_price
            elif shares > 0.0 and bool(row["exit"]):
                sell_price = price * (1.0 - total_friction)
                proceeds = shares * sell_price
                pnl = proceeds - (shares * entry_price)
                trades_pnl.append(pnl)
                cash += proceeds
                shares = 0.0
                entry_price = 0.0

            equity = cash + (shares * price)
            equity_series.append(equity)
            dates.append(date_str)

        # Close open position at end of sample for realized trade accounting
        if shares > 0.0:
            last_price = float(signals_df.iloc[-1]["close"]) * (1.0 - total_friction)
            trades_pnl.append(shares * (last_price - entry_price))

        eq = pd.Series(equity_series, dtype=float)
        returns = eq.pct_change().dropna()

        n_days = max(1, (pd.to_datetime(dates[-1]) - pd.to_datetime(dates[0])).days)
        years = max(n_days / 365.25, 1 / 252)
        total_return = (eq.iloc[-1] / initial_capital) - 1.0
        cagr = ((1.0 + total_return) ** (1.0 / years)) - 1.0 if eq.iloc[-1] > 0 else -1.0

        std = float(returns.std()) if len(returns) > 1 else 0.0
        sharpe = (float(returns.mean()) / std) * np.sqrt(252) if std > 1e-12 else 0.0

        downside = returns[returns < 0]
        down_std = float(downside.std()) if len(downside) > 1 else 0.0
        sortino = (float(returns.mean()) / down_std) * np.sqrt(252) if down_std > 1e-12 else 0.0

        running_max = eq.cummax()
        drawdowns = (eq - running_max) / running_max.replace(0.0, np.nan)
        max_drawdown = abs(float(drawdowns.min())) if not drawdowns.empty else 0.0

        total_trades = len(trades_pnl)
        wins = [p for p in trades_pnl if p > 0]
        losses = [abs(p) for p in trades_pnl if p < 0]
        win_rate = (len(wins) / total_trades) if total_trades > 0 else 0.0
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        profit_factor = (gross_profit / gross_loss) if gross_loss > 1e-9 else (2.0 if gross_profit > 0 else 0.0)

        # Align benchmark equity curve
        bench_map: dict[str, float] = {}
        if not bench_df.empty:
            bench_df = bench_df.copy()
            bench_df["date_str"] = pd.to_datetime(bench_df["date"]).dt.date.astype(str)
            first_bench = float(bench_df.iloc[0]["close"]) or 1.0
            for _, brow in bench_df.iterrows():
                bench_map[str(brow["date_str"])] = initial_capital * (float(brow["close"]) / first_bench)

        curve: list[dict[str, Any]] = []
        last_bench = initial_capital
        for d, val in zip(dates, equity_series):
            if d in bench_map:
                last_bench = bench_map[d]
            curve.append(
                {
                    "date": d,
                    "strategy_equity": round(val, 2),
                    "benchmark_equity": round(last_bench, 2),
                }
            )

        # Auto-Gatekeeper Rule
        reasons: list[str] = []
        if sharpe < self.settings.min_sharpe_ratio:
            reasons.append(f"Sharpe {sharpe:.2f} < min {self.settings.min_sharpe_ratio:.2f}")
        if max_drawdown > self.settings.max_drawdown_pct:
            reasons.append(f"Max Drawdown {max_drawdown*100:.1f}% > max {self.settings.max_drawdown_pct*100:.1f}%")
        if total_trades < 3:
            reasons.append(f"Too few trades ({total_trades} < 3)")

        passed = len(reasons) == 0
        reason_text = "PASSED Auto-Gatekeeper" if passed else "REJECTED: " + "; ".join(reasons)

        return BacktestResult(
            run_id=f"BT-{uuid.uuid4().hex[:8].upper()}",
            ticker=code,
            strategy=strategy_name,
            benchmark=normalize_ticker(benchmark),
            cagr=round(cagr * 100.0, 2),
            sharpe=round(sharpe, 3),
            sortino=round(sortino, 3),
            max_drawdown=round(max_drawdown * 100.0, 2),
            win_rate=round(win_rate * 100.0, 2),
            profit_factor=round(profit_factor, 3),
            total_trades=total_trades,
            win_probability=round(win_rate, 3) if total_trades > 0 else 0.5,
            passed_gatekeeper=passed,
            gatekeeper_reason=reason_text,
            equity_curve=curve,
        )
