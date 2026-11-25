"""DuckDB persistence layer for state, portfolio snapshots, orders, social feed, and backtests."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


class TradeDatabase:
    """Thread-safe DuckDB wrapper for TradeBot state and analytics."""

    def __init__(self, db_path: str = "data/tradebot.duckdb") -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = duckdb.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                    ts TIMESTAMP PRIMARY KEY,
                    nav DOUBLE,
                    cash DOUBLE,
                    buying_power DOUBLE,
                    unrealized_pnl DOUBLE,
                    realized_pnl DOUBLE,
                    day_pnl DOUBLE,
                    day_pnl_pct DOUBLE
                );

                CREATE TABLE IF NOT EXISTS trade_orders (
                    order_id VARCHAR PRIMARY KEY,
                    ts TIMESTAMP,
                    ticker VARCHAR,
                    side VARCHAR,
                    qty DOUBLE,
                    price DOUBLE,
                    order_type VARCHAR,
                    status VARCHAR,
                    trd_env VARCHAR,
                    stop_loss DOUBLE,
                    take_profit DOUBLE,
                    source VARCHAR,
                    raw_json VARCHAR
                );

                CREATE TABLE IF NOT EXISTS social_items (
                    item_hash VARCHAR PRIMARY KEY,
                    ts TIMESTAMP,
                    platform VARCHAR,
                    ticker VARCHAR,
                    author VARCHAR,
                    title VARCHAR,
                    content VARCHAR,
                    url VARCHAR,
                    engagement DOUBLE,
                    sentiment_score DOUBLE
                );

                CREATE TABLE IF NOT EXISTS backtest_runs (
                    run_id VARCHAR PRIMARY KEY,
                    ts TIMESTAMP,
                    ticker VARCHAR,
                    strategy VARCHAR,
                    cagr DOUBLE,
                    sharpe DOUBLE,
                    sortino DOUBLE,
                    max_drawdown DOUBLE,
                    win_rate DOUBLE,
                    profit_factor DOUBLE,
                    total_trades INTEGER,
                    passed_gatekeeper BOOLEAN,
                    gatekeeper_reason VARCHAR,
                    equity_curve_json VARCHAR
                );

                CREATE TABLE IF NOT EXISTS ai_recommendations (
                    rec_id VARCHAR PRIMARY KEY,
                    ts TIMESTAMP,
                    ticker VARCHAR,
                    verdict VARCHAR,
                    confidence DOUBLE,
                    technical_score DOUBLE,
                    catalyst_score DOUBLE,
                    social_score DOUBLE,
                    entry_low DOUBLE,
                    entry_high DOUBLE,
                    stop_loss DOUBLE,
                    take_profit_1 DOUBLE,
                    take_profit_2 DOUBLE,
                    risk_reward DOUBLE,
                    thesis_json VARCHAR,
                    status VARCHAR
                );
                """
            )

    def record_portfolio_snapshot(self, summary: dict[str, Any]) -> None:
        """Persist a portfolio NAV snapshot."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO portfolio_snapshots
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    now,
                    float(summary.get("nav", 0.0)),
                    float(summary.get("cash", 0.0)),
                    float(summary.get("buying_power", 0.0)),
                    float(summary.get("unrealized_pnl", 0.0)),
                    float(summary.get("realized_pnl", 0.0)),
                    float(summary.get("day_pnl", 0.0)),
                    float(summary.get("day_pnl_pct", 0.0)),
                ],
            )

    def get_portfolio_history(self, limit: int = 200) -> list[dict[str, Any]]:
        """Return historical NAV snapshots ordered chronologically."""
        with self._lock:
            df = self._conn.execute(
                """
                SELECT * FROM portfolio_snapshots
                ORDER BY ts DESC
                LIMIT ?
                """,
                [limit],
            ).fetchdf()
        if df.empty:
            return []
        df = df.sort_values("ts")
        df["ts"] = df["ts"].astype(str)
        return df.to_dict(orient="records")

    def log_order(self, order: dict[str, Any]) -> None:
        """Insert or update an order record."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO trade_orders
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    str(order.get("order_id")),
                    order.get("ts") or now,
                    str(order.get("ticker", "")),
                    str(order.get("side", "")),
                    float(order.get("qty", 0.0)),
                    float(order.get("price", 0.0)),
                    str(order.get("order_type", "MARKET")),
                    str(order.get("status", "SUBMITTED")),
                    str(order.get("trd_env", "SIMULATE")),
                    float(order["stop_loss"]) if order.get("stop_loss") is not None else None,
                    float(order["take_profit"]) if order.get("take_profit") is not None else None,
                    str(order.get("source", "AI_BOT")),
                    json.dumps(order, default=str),
                ],
            )

    def get_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch recent simulated orders."""
        with self._lock:
            df = self._conn.execute(
                "SELECT * FROM trade_orders ORDER BY ts DESC LIMIT ?",
                [limit],
            ).fetchdf()
        if df.empty:
            return []
        df["ts"] = df["ts"].astype(str)
        return df.to_dict(orient="records")

    def save_social_items(self, items: list[dict[str, Any]]) -> int:
        """Deduplicated insert of social/news items. Returns count inserted."""
        if not items:
            return 0
        inserted = 0
        with self._lock:
            for item in items:
                exists = self._conn.execute(
                    "SELECT 1 FROM social_items WHERE item_hash = ?",
                    [item["item_hash"]],
                ).fetchone()
                if exists:
                    continue
                self._conn.execute(
                    """
                    INSERT INTO social_items
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        item["item_hash"],
                        item.get("ts") or datetime.now(timezone.utc).replace(tzinfo=None),
                        item.get("platform", "unknown"),
                        item.get("ticker", ""),
                        item.get("author", ""),
                        item.get("title", ""),
                        item.get("content", ""),
                        item.get("url", ""),
                        float(item.get("engagement", 0.0)),
                        float(item.get("sentiment_score", 0.0)),
                    ],
                )
                inserted += 1
        return inserted

    def get_social_items(self, ticker: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Query recent social & news intelligence items."""
        with self._lock:
            if ticker:
                df = self._conn.execute(
                    "SELECT * FROM social_items WHERE ticker = ? ORDER BY ts DESC LIMIT ?",
                    [ticker.upper(), limit],
                ).fetchdf()
            else:
                df = self._conn.execute(
                    "SELECT * FROM social_items ORDER BY ts DESC LIMIT ?",
                    [limit],
                ).fetchdf()
        if df.empty:
            return []
        df["ts"] = df["ts"].astype(str)
        return df.to_dict(orient="records")

    def save_backtest_run(self, run: dict[str, Any]) -> None:
        """Persist a backtest run and its equity curve."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO backtest_runs
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run["run_id"],
                    now,
                    run["ticker"],
                    run["strategy"],
                    float(run["cagr"]),
                    float(run["sharpe"]),
                    float(run["sortino"]),
                    float(run["max_drawdown"]),
                    float(run["win_rate"]),
                    float(run["profit_factor"]),
                    int(run["total_trades"]),
                    bool(run["passed_gatekeeper"]),
                    str(run.get("gatekeeper_reason", "")),
                    json.dumps(run.get("equity_curve", []), default=str),
                ],
            )

    def get_backtest_runs(self, ticker: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Fetch stored backtest runs."""
        with self._lock:
            if ticker:
                df = self._conn.execute(
                    "SELECT * FROM backtest_runs WHERE ticker = ? ORDER BY ts DESC LIMIT ?",
                    [ticker.upper(), limit],
                ).fetchdf()
            else:
                df = self._conn.execute(
                    "SELECT * FROM backtest_runs ORDER BY ts DESC LIMIT ?",
                    [limit],
                ).fetchdf()
        if df.empty:
            return []
        df["ts"] = df["ts"].astype(str)
        records = df.to_dict(orient="records")
        for rec in records:
            raw_curve = rec.pop("equity_curve_json", "[]")
            rec["equity_curve"] = json.loads(raw_curve) if raw_curve else []
        return records

    def save_recommendation(self, rec: dict[str, Any]) -> None:
        """Persist an AI recommendation card, superseding older PENDING cards for the same ticker."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock:
            self._conn.execute(
                "UPDATE ai_recommendations SET status = 'SUPERSEDED' WHERE ticker = ? AND status = 'PENDING'",
                [rec["ticker"]],
            )
            self._conn.execute(
                """
                INSERT OR REPLACE INTO ai_recommendations
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    rec["rec_id"],
                    now,
                    rec["ticker"],
                    rec["verdict"],
                    float(rec["confidence"]),
                    float(rec["technical_score"]),
                    float(rec["catalyst_score"]),
                    float(rec["social_score"]),
                    float(rec["entry_low"]),
                    float(rec["entry_high"]),
                    float(rec["stop_loss"]),
                    float(rec["take_profit_1"]),
                    float(rec["take_profit_2"]),
                    float(rec["risk_reward"]),
                    json.dumps(rec.get("thesis", [])),
                    str(rec.get("status", "PENDING")),
                ],
            )

    def get_recommendations(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return latest active AI recommendations (excluding SUPERSEDED)."""
        with self._lock:
            df = self._conn.execute(
                "SELECT * FROM ai_recommendations WHERE status != 'SUPERSEDED' ORDER BY ts DESC LIMIT ?",
                [limit],
            ).fetchdf()
        if df.empty:
            return []
        df["ts"] = df["ts"].astype(str)
        records = df.to_dict(orient="records")
        for rec in records:
            raw_thesis = rec.pop("thesis_json", "[]")
            rec["thesis"] = json.loads(raw_thesis) if raw_thesis else []
        return records

    def update_recommendation_status(self, rec_id: str, status: str) -> None:
        """Update recommendation status (e.g. PENDING -> EXECUTED / REJECTED)."""
        with self._lock:
            self._conn.execute(
                "UPDATE ai_recommendations SET status = ? WHERE rec_id = ?",
                [status, rec_id],
            )

    def close(self) -> None:
        """Close DuckDB connection."""
        with self._lock:
            self._conn.close()
