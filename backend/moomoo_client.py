"""Moomoo OpenD Execution & Paper Portfolio Sync Engine.

Strictly enforces TrdEnv.SIMULATE on every trading and account call so live
funds can never be accessed or modified.
"""

from __future__ import annotations

import json
import logging
import socket
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

try:
    from moomoo import (
        KLType,
        ModifyOrderOp,
        OpenQuoteContext,
        OpenSecTradeContext,
        OrderType,
        RET_OK,
        SecurityFirm,
        SubType,
        TrdEnv,
        TrdMarket,
        TrdSide,
    )

    MOOMOO_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback when SDK is not installed
    MOOMOO_SDK_AVAILABLE = False

    class _SimEnum:
        SIMULATE = "SIMULATE"
        REAL = "REAL"

    class TrdEnv:  # type: ignore[no-redef]
        SIMULATE = "SIMULATE"
        REAL = "REAL"

    class TrdSide:  # type: ignore[no-redef]
        BUY = "BUY"
        SELL = "SELL"

    class OrderType:  # type: ignore[no-redef]
        NORMAL = "NORMAL"
        MARKET = "MARKET"

    class ModifyOrderOp:  # type: ignore[no-redef]
        CANCEL = "CANCEL"
        NORMAL = "NORMAL"

    class KLType:  # type: ignore[no-redef]
        K_1M = "K_1M"
        K_5M = "K_5M"
        K_60M = "K_60M"
        K_DAY = "K_DAY"

    class SubType:  # type: ignore[no-redef]
        QUOTE = "QUOTE"
        TICKER = "TICKER"
        K_1M = "K_1M"
        K_5M = "K_5M"
        K_60M = "K_60M"
        K_DAY = "K_DAY"

    class TrdMarket:  # type: ignore[no-redef]
        US = "US"

    class SecurityFirm:  # type: ignore[no-redef]
        FUTUINC = "FUTUINC"

    RET_OK = 0


class PaperTradingGuardrailError(RuntimeError):
    """Raised if any code path attempts to use a non-SIMULATE trading environment."""


def assert_simulate_env(trd_env: Any) -> Any:
    """Hard-assert that trd_env is strictly TrdEnv.SIMULATE."""
    if trd_env != TrdEnv.SIMULATE:
        raise PaperTradingGuardrailError(
            f"SECURITY VIOLATION: Attempted trading operation with trd_env={trd_env!r}. "
            "Only TrdEnv.SIMULATE is permitted."
        )
    return TrdEnv.SIMULATE


def normalize_ticker(symbol: str) -> str:
    """Normalize ticker to Moomoo US format (e.g. 'AAPL' -> 'US.AAPL')."""
    clean = symbol.strip().upper()
    if "." not in clean:
        return f"US.{clean}"
    return clean


def strip_market_prefix(symbol: str) -> str:
    """Strip market prefix (e.g. 'US.AAPL' -> 'AAPL')."""
    return symbol.split(".")[-1].upper()


class MoomooPaperClient:
    """Wrapper around Moomoo OpenD Quote & Trade contexts locked to TrdEnv.SIMULATE."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        assert_simulate_env(self.settings.moomoo_trd_env if MOOMOO_SDK_AVAILABLE else TrdEnv.SIMULATE)

        self.host = self.settings.moomoo_opend_host
        self.port = self.settings.moomoo_opend_port
        self.cli_bin = str(Path(self.settings.moomoo_cli_bin).expanduser())
        self.sim_account_id = self.settings.moomoo_sim_account_id
        self.trd_env = TrdEnv.SIMULATE  # Hardcoded constant
        self._quote_ctx: Any = None
        self._trade_ctx: Any = None
        self.connected: bool = False
        self.mode: str = "DISCONNECTED"
        # Moomoo cloud API rate-limits (HTTP 439) bursts of concurrent CLI calls.
        self._cli_sem = threading.Semaphore(2)
        self._last_good_positions: list[dict[str, Any]] | None = None
        self._last_good_account: dict[str, Any] | None = None
        self._last_good_orders: list[dict[str, Any]] | None = None

        # Local sandbox fallback state (used only when OpenD gateway is offline)
        self._sim_cash: float = 100_000.0
        self._sim_initial_equity: float = 100_000.0
        self._sim_realized_pnl: float = 0.0
        self._sim_positions: dict[str, dict[str, Any]] = {}
        self._sim_orders: list[dict[str, Any]] = []
        self._sim_deals: list[dict[str, Any]] = []
        self._last_prices: dict[str, float] = {
            "US.AAPL": 228.50,
            "US.NVDA": 141.20,
            "US.MSFT": 424.10,
            "US.TSLA": 338.40,
            "US.SPY": 596.80,
            "US.QQQ": 511.30,
            "US.AMD": 142.75,
            "US.META": 572.60,
        }

    def _run_moomoo_cli(self, args: list[str], timeout: float = 15.0) -> Any:
        """Execute ~/.moomoo/bin/moomoo with --format json and parse output."""
        cmd = [self.cli_bin, *args, "--format", "json"]
        with self._cli_sem:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "").strip() or f"CLI exited {proc.returncode}")
        return json.loads(proc.stdout)

    def connect(self) -> bool:
        """Connect to OpenD gateway, or Moomoo CLI OAuth paper account, or local fallback."""
        assert_simulate_env(self.trd_env)
        if MOOMOO_SDK_AVAILABLE:
            try:
                with socket.create_connection((self.host, self.port), timeout=1.0):
                    pass

                self._quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
                market = getattr(TrdMarket, self.settings.moomoo_trd_market, TrdMarket.US)
                firm = getattr(SecurityFirm, self.settings.moomoo_security_firm, SecurityFirm.FUTUINC)
                self._trade_ctx = OpenSecTradeContext(
                    filter_trdmarket=market,
                    host=self.host,
                    port=self.port,
                    security_firm=firm,
                )
                ret, data = self._trade_ctx.accinfo_query(trd_env=assert_simulate_env(TrdEnv.SIMULATE))
                if ret != RET_OK:
                    raise ConnectionError(f"OpenD accinfo_query failed: {data}")
                self.connected = True
                self.mode = "OPEND_SIMULATE"
                logger.info("Connected to Moomoo OpenD Paper Trading at %s:%s", self.host, self.port)
                return True
            except Exception as exc:
                logger.info("OpenD (%s:%s) unavailable (%s); checking Moomoo CLI...", self.host, self.port, exc)
                self.close()

        # Try Moomoo CLI OAuth connection (pinned to sim_account_id)
        if Path(self.cli_bin).exists() and self.sim_account_id:
            try:
                funds = self._run_moomoo_cli(["sim", "account", "funds", "--account", self.sim_account_id])
                if isinstance(funds, dict) and "total_asset" in funds:
                    self.connected = True
                    self.mode = "MOOMOO_CLI_SIMULATE"
                    logger.info(
                        "Connected to Moomoo Paper Account %s via CLI (%s) | NAV=$%s",
                        self.sim_account_id,
                        self.cli_bin,
                        funds.get("total_asset"),
                    )
                    return True
            except Exception as exc:
                logger.warning("Moomoo CLI check failed (%s); using LOCAL_SIM_FALLBACK.", exc)

        self.connected = False
        self.mode = "LOCAL_SIM_FALLBACK"
        return False

    def close(self) -> None:
        """Close OpenD contexts cleanly."""
        if self._quote_ctx is not None:
            try:
                self._quote_ctx.close()
            except Exception:
                pass
            self._quote_ctx = None
        if self._trade_ctx is not None:
            try:
                self._trade_ctx.close()
            except Exception:
                pass
            self._trade_ctx = None
        self.connected = False

    def subscribe_watchlist(self, symbols: list[str] | None = None) -> dict[str, Any]:
        """Subscribe to L1 quotes, tick-by-tick, and 1m/5m/1h/1D klines."""
        tickers = [normalize_ticker(s) for s in (symbols or self.settings.watchlist_symbols)]
        if not self.connected or self._quote_ctx is None:
            return {"status": "LOCAL_SIM_FALLBACK", "subscribed": tickers}

        sub_types = [SubType.QUOTE, SubType.TICKER, SubType.K_1M, SubType.K_5M, SubType.K_60M, SubType.K_DAY]
        ret, msg = self._quote_ctx.subscribe(tickers, sub_types, subscribe_push=True)
        if ret != RET_OK:
            logger.error("Moomoo subscribe error: %s", msg)
            return {"status": "ERROR", "error": str(msg), "subscribed": []}
        return {"status": "OK", "subscribed": tickers}

    def get_live_quotes(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        """Fetch L1 quotes for watchlist + active holdings."""
        tickers = list(dict.fromkeys([normalize_ticker(s) for s in (symbols or self.settings.watchlist_symbols)]))
        if self.connected and self._quote_ctx is not None:
            ret, df = self._quote_ctx.get_stock_quote(tickers)
            if ret == RET_OK and isinstance(df, pd.DataFrame) and not df.empty:
                quotes = []
                for _, row in df.iterrows():
                    code = str(row.get("code", ""))
                    last_price = float(row.get("last_price", 0.0))
                    prev_close = float(row.get("prev_close_price", last_price) or last_price)
                    chg_pct = ((last_price - prev_close) / prev_close * 100.0) if prev_close else 0.0
                    self._last_prices[code] = last_price
                    quotes.append(
                        {
                            "ticker": code,
                            "symbol": strip_market_prefix(code),
                            "price": round(last_price, 4),
                            "prev_close": round(prev_close, 4),
                            "change_pct": round(chg_pct, 2),
                            "volume": int(row.get("volume", 0) or 0),
                            "high": float(row.get("high_price", last_price) or last_price),
                            "low": float(row.get("low_price", last_price) or last_price),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "source": "OPEND_SIMULATE",
                        }
                    )
                return quotes

        if self.mode == "MOOMOO_CLI_SIMULATE":
            try:
                raw_quotes = self._run_moomoo_cli(["quote", *tickers])
                if isinstance(raw_quotes, list) and raw_quotes:
                    quotes = []
                    for row in raw_quotes:
                        code = str(row.get("code", ""))
                        last_price = float(row.get("last_price", 0.0) or 0.0)
                        prev_close = float(row.get("prev_close_price", last_price) or last_price)
                        chg_pct = ((last_price - prev_close) / prev_close * 100.0) if prev_close else 0.0
                        self._last_prices[code] = last_price
                        quotes.append(
                            {
                                "ticker": code,
                                "symbol": strip_market_prefix(code),
                                "price": round(last_price, 4),
                                "prev_close": round(prev_close, 4),
                                "change_pct": round(chg_pct, 2),
                                "volume": int(row.get("volume", 0) or 0),
                                "high": float(row.get("high_price", last_price) or last_price),
                                "low": float(row.get("low_price", last_price) or last_price),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "source": "MOOMOO_CLI_LIVE",
                            }
                        )
                    return quotes
            except Exception as exc:
                logger.warning("Moomoo CLI quote error (%s), falling back to Yahoo:", exc)

        # Local fallback: refresh with Yahoo Finance chart API
        return self._get_fallback_quotes(tickers)

    def _get_fallback_quotes(self, tickers: list[str]) -> list[dict[str, Any]]:
        """Fetch live intraday/extended-hours quotes concurrently via Yahoo chart API."""
        import concurrent.futures
        import httpx

        def _fetch_one(code: str) -> dict[str, Any]:
            sym = strip_market_prefix(code)
            price = self._last_prices.get(code, 100.0)
            prev_close = price
            volume = 0
            high_p = price
            low_p = price
            source = "CACHED"
            try:
                resp = httpx.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                    params={"interval": "1m", "range": "1d", "includePrePost": "true"},
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=3.0,
                )
                if resp.status_code == 200:
                    meta = resp.json()["chart"]["result"][0]["meta"]
                    price = float(meta.get("regularMarketPrice") or price)
                    prev_close = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
                    volume = int(meta.get("regularMarketVolume") or 0)
                    high_p = float(meta.get("regularMarketDayHigh") or price)
                    low_p = float(meta.get("regularMarketDayLow") or price)
                    self._last_prices[code] = price
                    source = "YAHOO_LIVE"
            except Exception:
                pass

            chg_pct = ((price - prev_close) / prev_close * 100.0) if prev_close else 0.0
            return {
                "ticker": code,
                "symbol": sym,
                "price": round(price, 4),
                "prev_close": round(prev_close, 4),
                "change_pct": round(chg_pct, 2),
                "volume": volume,
                "high": round(high_p, 4),
                "low": round(low_p, 4),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": source,
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, max(1, len(tickers)))) as pool:
            return list(pool.map(_fetch_one, tickers))

    def get_account_info(self, positions_override: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Query simulated account NAV, cash, buying power, and P&L."""
        assert_simulate_env(self.trd_env)
        if self.connected and self._trade_ctx is not None:
            ret, df = self._trade_ctx.accinfo_query(trd_env=assert_simulate_env(TrdEnv.SIMULATE))
            if ret == RET_OK and isinstance(df, pd.DataFrame) and not df.empty:
                row = df.iloc[0]
                nav = float(row.get("total_assets", 0.0) or 0.0)
                cash = float(row.get("cash", 0.0) or 0.0)
                buying_power = float(row.get("power", cash) or cash)
                unrealized = float(row.get("unrealized_pl", 0.0) or 0.0)
                realized = float(row.get("realized_pl", 0.0) or 0.0)
                return {
                    "trd_env": "SIMULATE",
                    "mode": self.mode,
                    "nav": round(nav, 2),
                    "cash": round(cash, 2),
                    "buying_power": round(buying_power, 2),
                    "unrealized_pnl": round(unrealized, 2),
                    "realized_pnl": round(realized, 2),
                    "day_pnl": round(unrealized, 2),
                    "day_pnl_pct": round((unrealized / nav * 100.0) if nav else 0.0, 2),
                }

        if self.mode == "MOOMOO_CLI_SIMULATE":
            try:
                funds = self._run_moomoo_cli(["sim", "account", "funds", "--account", self.sim_account_id])
                nav = float(funds.get("total_asset", 0.0) or 0.0)
                cash = float(funds.get("balance", 0.0) or 0.0)
                buying_power = float(funds.get("max_power_long", cash) or cash)
                positions = positions_override if positions_override is not None else self.get_positions()
                unrealized = sum(p["unrealized_pnl"] for p in positions)
                day_pnl = nav - 100_000.0
                realized = day_pnl - unrealized
                account = {
                    "trd_env": "SIMULATE",
                    "mode": self.mode,
                    "account_id": self.sim_account_id,
                    "nav": round(nav, 2),
                    "cash": round(cash, 2),
                    "buying_power": round(buying_power, 2),
                    "unrealized_pnl": round(unrealized, 2),
                    "realized_pnl": round(realized, 2),
                    "day_pnl": round(day_pnl, 2),
                    "day_pnl_pct": round((day_pnl / 100_000.0 * 100.0), 2),
                }
                self._last_good_account = account
                return account
            except Exception as exc:
                logger.warning("Moomoo CLI funds error: %s", exc)
                if self._last_good_account is not None:
                    return self._last_good_account

        # Local fallback calculation
        positions = positions_override if positions_override is not None else self.get_positions()
        mkt_val = sum(p["market_value"] for p in positions)
        unrealized = sum(p["unrealized_pnl"] for p in positions)
        nav = self._sim_cash + mkt_val
        day_pnl = nav - self._sim_initial_equity
        return {
            "trd_env": "SIMULATE",
            "mode": self.mode,
            "nav": round(nav, 2),
            "cash": round(self._sim_cash, 2),
            "buying_power": round(self._sim_cash, 2),
            "unrealized_pnl": round(unrealized, 2),
            "realized_pnl": round(self._sim_realized_pnl, 2),
            "day_pnl": round(day_pnl, 2),
            "day_pnl_pct": round((day_pnl / self._sim_initial_equity * 100.0) if self._sim_initial_equity else 0.0, 2),
        }

    def get_positions(self) -> list[dict[str, Any]]:
        """Query simulated open positions with portfolio weights and P&L."""
        assert_simulate_env(self.trd_env)
        if self.connected and self._trade_ctx is not None:
            ret, df = self._trade_ctx.position_list_query(trd_env=assert_simulate_env(TrdEnv.SIMULATE))
            if ret == RET_OK and isinstance(df, pd.DataFrame):
                if df.empty:
                    return []
                total_mkt = float(df["market_val"].sum()) or 1.0
                positions = []
                for _, row in df.iterrows():
                    qty = float(row.get("qty", 0.0) or 0.0)
                    if qty == 0:
                        continue
                    mkt_val = float(row.get("market_val", 0.0) or 0.0)
                    positions.append(
                        {
                            "ticker": str(row.get("code", "")),
                            "symbol": strip_market_prefix(str(row.get("code", ""))),
                            "qty": qty,
                            "avg_cost": round(float(row.get("cost_price", 0.0) or 0.0), 4),
                            "current_price": round(float(row.get("nominal_price", 0.0) or 0.0), 4),
                            "market_value": round(mkt_val, 2),
                            "weight_pct": round(mkt_val / total_mkt * 100.0, 2),
                            "unrealized_pnl": round(float(row.get("pl_val", 0.0) or 0.0), 2),
                            "unrealized_pnl_pct": round(float(row.get("pl_ratio", 0.0) or 0.0), 2),
                        }
                    )
                return positions

        if self.mode == "MOOMOO_CLI_SIMULATE":
            try:
                raw_pos = self._run_moomoo_cli(
                    ["sim", "position", "--account", self.sim_account_id, "--market", self.settings.moomoo_trd_market]
                )
                pos_list = (
                    raw_pos
                    if isinstance(raw_pos, list)
                    else raw_pos.get("positions", raw_pos.get("list", []))
                    if isinstance(raw_pos, dict)
                    else []
                )
                if not pos_list:
                    self._last_good_positions = []
                    return []
                total_mkt = sum(abs(float(p.get("mv", p.get("market_val", 0.0)) or 0.0)) for p in pos_list) or 1.0
                positions = []
                for row in pos_list:
                    qty = float(row.get("qty", 0.0) or 0.0)
                    if qty == 0:
                        continue
                    sym_raw = str(row.get("symbol", row.get("code", "")))
                    code = normalize_ticker(sym_raw)
                    mkt_val = float(row.get("mv", row.get("market_val", 0.0)) or 0.0)
                    cur_px = float(row.get("cur_price", row.get("nominal_price", 0.0)) or 0.0)
                    pnl = float(row.get("profit", row.get("pl_val", 0.0)) or 0.0)
                    pnl_ratio = float(row.get("profit_ratio", row.get("pl_ratio", 0.0)) or 0.0)
                    positions.append(
                        {
                            "ticker": code,
                            "symbol": strip_market_prefix(code),
                            "qty": qty,
                            "avg_cost": round(float(row.get("cost_price", 0.0) or 0.0), 4),
                            "current_price": round(cur_px, 4),
                            "market_value": round(mkt_val, 2),
                            "weight_pct": round(abs(mkt_val) / total_mkt * 100.0, 2),
                            "unrealized_pnl": round(pnl, 2),
                            "unrealized_pnl_pct": round(pnl_ratio, 2),
                        }
                    )
                self._last_good_positions = positions
                return positions
            except Exception as exc:
                logger.warning("Moomoo CLI positions error: %s", exc)
                if self._last_good_positions is not None:
                    return self._last_good_positions

        # Local fallback positions
        total_equity = self._sim_cash + sum(
            pos["qty"] * self._last_prices.get(code, pos["avg_cost"])
            for code, pos in self._sim_positions.items()
        )
        total_equity = total_equity or 1.0
        result = []
        for code, pos in self._sim_positions.items():
            if pos["qty"] == 0:
                continue
            cur = self._last_prices.get(code, pos["avg_cost"])
            mkt_val = pos["qty"] * cur
            pnl = (cur - pos["avg_cost"]) * pos["qty"]
            pnl_pct = ((pnl / (abs(pos["qty"]) * pos["avg_cost"])) * 100.0) if pos["avg_cost"] else 0.0
            result.append(
                {
                    "ticker": code,
                    "symbol": strip_market_prefix(code),
                    "qty": pos["qty"],
                    "avg_cost": round(pos["avg_cost"], 4),
                    "current_price": round(cur, 4),
                    "market_value": round(mkt_val, 2),
                    "weight_pct": round(abs(mkt_val) / total_equity * 100.0, 2),
                    "unrealized_pnl": round(pnl, 2),
                    "unrealized_pnl_pct": round(pnl_pct, 2),
                    "stop_loss": pos.get("stop_loss"),
                    "take_profit": pos.get("take_profit"),
                }
            )
        return result

    @staticmethod
    def _cli_order_entries(src: Any) -> list[dict[str, Any]]:
        if isinstance(src, list):
            return src
        if not isinstance(src, dict):
            return []
        inner = src.get("data") if isinstance(src.get("data"), dict) else src
        return inner.get("orders", inner.get("list", [])) or []

    @staticmethod
    def _normalize_cli_order(o: dict[str, Any]) -> dict[str, Any]:
        status_map = {1: "UNSUBMITTED", 2: "OPEN", 3: "CANCELLED", 4: "FILLED", 5: "FAILED"}
        sym_raw = str(o.get("symbol", o.get("code", "")))
        side_val = o.get("side", o.get("trd_side"))
        raw_status = o.get("status", o.get("order_status", ""))
        ts_us = int(o.get("create_time", o.get("updated_time", 0)) or 0)
        fill_px = float(o.get("avg_fill_price") or 0.0)
        return {
            "order_id": str(o.get("order_id", o.get("orderId", ""))),
            "ts": datetime.fromtimestamp(ts_us / 1e6).strftime("%Y-%m-%d %H:%M:%S") if ts_us else "",
            "ts_us": ts_us,
            "ticker": normalize_ticker(sym_raw) if sym_raw else "",
            "side": "BUY" if side_val in (1, "BUY") else "SELL",
            "qty": float(o.get("qty", 0.0) or 0.0),
            "filled_qty": float(o.get("cum_qty", 0.0) or 0.0),
            "price": round(fill_px or float(o.get("price") or 0.0), 4),
            "fill_price": round(fill_px, 4),
            "order_type": "LIMIT" if o.get("order_type") == 1 else "MARKET",
            "status": status_map.get(raw_status, str(raw_status)),
            "trd_env": "SIMULATE",
        }

    def get_order_history(self, page_size: int = 100, max_pages: int = 40) -> list[dict[str, Any]]:
        """Full simulated order history across all sessions, newest first."""
        assert_simulate_env(self.trd_env)
        if self.connected and self._trade_ctx is not None:
            try:
                ret, df = self._trade_ctx.history_order_list_query(
                    start="2020-01-01",
                    end=datetime.now().strftime("%Y-%m-%d"),
                    trd_env=assert_simulate_env(TrdEnv.SIMULATE),
                )
                if ret == RET_OK and isinstance(df, pd.DataFrame) and not df.empty:
                    return df.to_dict(orient="records")
            except Exception as exc:
                logger.warning("OpenD history_order_list_query error: %s", exc)
            return self.get_orders()
        if self.mode == "MOOMOO_CLI_SIMULATE":
            items: list[dict[str, Any]] = []
            seen: set[str] = set()
            next_key = ""
            for _ in range(max_pages):
                args = [
                    "sim", "order", "history",
                    "--account", self.sim_account_id,
                    "--market", self.settings.moomoo_trd_market,
                    "--page-size", str(page_size),
                ]
                if next_key:
                    args += ["--next-key", next_key]
                src = self._run_moomoo_cli(args, timeout=30.0)
                for o in self._cli_order_entries(src):
                    item = self._normalize_cli_order(o)
                    if item["order_id"] not in seen:
                        seen.add(item["order_id"])
                        items.append(item)
                pag = src.get("pagination", {}) if isinstance(src, dict) else {}
                if not pag.get("has_more") or not pag.get("next_key"):
                    break
                next_key = str(pag["next_key"])
            items.sort(key=lambda x: x["ts_us"], reverse=True)
            return items
        return list(reversed(self._sim_orders))

    def get_orders(self) -> list[dict[str, Any]]:
        """Query simulated order blotter."""
        assert_simulate_env(self.trd_env)
        if self.connected and self._trade_ctx is not None:
            ret, df = self._trade_ctx.order_list_query(trd_env=assert_simulate_env(TrdEnv.SIMULATE))
            if ret == RET_OK and isinstance(df, pd.DataFrame) and not df.empty:
                return df.to_dict(orient="records")
        if self.mode == "MOOMOO_CLI_SIMULATE":
            try:
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    open_fut = pool.submit(
                        self._run_moomoo_cli,
                        ["sim", "order", "list", "--account", self.sim_account_id, "--market", self.settings.moomoo_trd_market],
                    )
                    hist_fut = pool.submit(
                        self._run_moomoo_cli,
                        ["sim", "order", "history", "--account", self.sim_account_id, "--market", self.settings.moomoo_trd_market],
                    )
                    open_orders = open_fut.result()
                    hist_orders = hist_fut.result()
                items: list[dict[str, Any]] = []
                seen_ids: set[str] = set()
                for src in (open_orders, hist_orders):
                    for o in self._cli_order_entries(src):
                        item = self._normalize_cli_order(o)
                        if item["order_id"] in seen_ids:
                            continue
                        seen_ids.add(item["order_id"])
                        items.append(item)
                items.sort(key=lambda x: x["ts_us"], reverse=True)
                self._last_good_orders = items
                return items
            except Exception as exc:
                logger.warning("Moomoo CLI orders error: %s", exc)
                if self._last_good_orders is not None:
                    return self._last_good_orders
        return list(reversed(self._sim_orders))

    def get_deals(self) -> list[dict[str, Any]]:
        """Query simulated executed trade fills."""
        assert_simulate_env(self.trd_env)
        if self.connected and self._trade_ctx is not None:
            ret, df = self._trade_ctx.deal_list_query(trd_env=assert_simulate_env(TrdEnv.SIMULATE))
            if ret == RET_OK and isinstance(df, pd.DataFrame) and not df.empty:
                return df.to_dict(orient="records")
        return list(reversed(self._sim_deals))

    def place_order(
        self,
        ticker: str,
        side: str,
        qty: float,
        price: float = 0.0,
        order_type: str = "MARKET",
        stop_loss: float | None = None,
        take_profit: float | None = None,
        trd_env: Any = TrdEnv.SIMULATE,
    ) -> dict[str, Any]:
        """Place a paper trading order. Strictly asserts TrdEnv.SIMULATE."""
        assert_simulate_env(trd_env)
        assert_simulate_env(self.trd_env)

        code = normalize_ticker(ticker)
        side_upper = side.upper()
        if side_upper not in ("BUY", "SELL"):
            raise ValueError(f"Unsupported order side: {side}")
        if qty <= 0:
            raise ValueError(f"Order quantity must be positive, got {qty}")

        if self.connected and self._trade_ctx is not None:
            trd_side = TrdSide.BUY if side_upper == "BUY" else TrdSide.SELL
            moomoo_otype = OrderType.MARKET if order_type.upper() == "MARKET" else OrderType.NORMAL
            ret, data = self._trade_ctx.place_order(
                price=float(price),
                qty=float(qty),
                code=code,
                trd_side=trd_side,
                order_type=moomoo_otype,
                trd_env=assert_simulate_env(TrdEnv.SIMULATE),
            )
            if ret != RET_OK:
                raise RuntimeError(f"Moomoo OpenD place_order failed: {data}")
            order_id = str(data.iloc[0]["order_id"]) if isinstance(data, pd.DataFrame) and not data.empty else str(uuid.uuid4())
            return {
                "order_id": order_id,
                "ticker": code,
                "side": side_upper,
                "qty": float(qty),
                "price": float(price),
                "order_type": order_type.upper(),
                "status": "SUBMITTED",
                "trd_env": "SIMULATE",
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "ts": datetime.now(timezone.utc).isoformat(),
            }

        if self.mode == "MOOMOO_CLI_SIMULATE":
            is_market = order_type.upper() == "MARKET" or price <= 0
            ref_px = float(price)
            if ref_px <= 0:
                ref_px = float(self._last_prices.get(code, 0.0))
            if ref_px <= 0 and not is_market:
                q = self.get_live_quotes([code])
                ref_px = float(q[0]["price"]) if q else 100.0
            args = [
                "sim",
                "order",
                "place",
                str(int(qty)),
                "--symbol",
                code,
                "--side",
                side_upper,
                "--account",
                self.sim_account_id,
                "--market",
                self.settings.moomoo_trd_market,
            ]
            if is_market:
                # True market order fills immediately; a limit at the last price
                # sits queued and never fills outside RTH.
                args += ["--type", "market"]
            else:
                args += ["--type", "limit", "--price", f"{ref_px:.2f}"]
            res = self._run_moomoo_cli(args)
            order_id = str(res.get("orderId", res.get("order_id", uuid.uuid4().hex[:10]))) if isinstance(res, dict) else uuid.uuid4().hex[:10]
            return {
                "order_id": order_id,
                "ticker": code,
                "side": side_upper,
                "qty": float(int(qty)),
                "price": round(ref_px, 2),
                "order_type": "MARKET" if is_market else "LIMIT",
                "status": "SUBMITTED_MOOMOO_SIM",
                "trd_env": "SIMULATE",
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "ts": datetime.now(timezone.utc).isoformat(),
            }

        # Local sandbox execution: MARKET orders always fill at live market price
        if order_type.upper() == "MARKET" or price <= 0:
            live = self._last_prices.get(code)
            if live is None:
                quotes = self._get_fallback_quotes([code])
                live = quotes[0]["price"] if quotes else 100.0
            exec_price = float(live)
        else:
            exec_price = float(price)
        order_id = f"SIM-{uuid.uuid4().hex[:10].upper()}"
        notional = exec_price * qty

        if side_upper == "BUY":
            existing = self._sim_positions.get(code)
            if existing and existing["qty"] < 0:
                # Covering a short position (and optionally flipping long)
                covered = min(qty, abs(existing["qty"]))
                self._sim_realized_pnl += (existing["avg_cost"] - exec_price) * covered
                self._sim_cash -= notional
                new_qty = existing["qty"] + qty
                if new_qty == 0:
                    self._sim_positions.pop(code, None)
                elif new_qty > 0:
                    self._sim_positions[code] = {
                        "qty": float(new_qty),
                        "avg_cost": exec_price,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                    }
                else:
                    existing["qty"] = float(new_qty)
            else:
                if notional > self._sim_cash:
                    raise ValueError(f"Insufficient simulated cash (${self._sim_cash:.2f}) for ${notional:.2f} order.")
                self._sim_cash -= notional
                if existing:
                    total_qty = existing["qty"] + qty
                    avg_cost = (existing["avg_cost"] * existing["qty"] + notional) / total_qty
                    existing.update({"qty": total_qty, "avg_cost": avg_cost, "stop_loss": stop_loss, "take_profit": take_profit})
                else:
                    self._sim_positions[code] = {
                        "qty": float(qty),
                        "avg_cost": exec_price,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                    }
        else:
            self._sim_cash += notional
            existing = self._sim_positions.get(code)
            if existing:
                if existing["qty"] > 0:
                    closed = min(qty, existing["qty"])
                    self._sim_realized_pnl += (exec_price - existing["avg_cost"]) * closed
                new_qty = existing["qty"] - qty
                if new_qty == 0:
                    self._sim_positions.pop(code, None)
                elif new_qty < 0 and existing["qty"] > 0:
                    # Flipped from long to short
                    self._sim_positions[code] = {
                        "qty": float(new_qty),
                        "avg_cost": exec_price,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                    }
                elif new_qty < 0:
                    # Adding to existing short
                    total_short = abs(existing["qty"]) + qty
                    avg_cost = (existing["avg_cost"] * abs(existing["qty"]) + notional) / total_short
                    existing.update({"qty": float(new_qty), "avg_cost": avg_cost, "stop_loss": stop_loss, "take_profit": take_profit})
                else:
                    existing["qty"] = float(new_qty)
            else:
                # Opening a new paper short position
                self._sim_positions[code] = {
                    "qty": -float(qty),
                    "avg_cost": exec_price,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                }

        order_record = {
            "order_id": order_id,
            "ticker": code,
            "side": side_upper,
            "qty": float(qty),
            "price": round(exec_price, 4),
            "order_type": order_type.upper(),
            "status": "FILLED_SIM",
            "trd_env": "SIMULATE",
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        self._sim_orders.append(order_record)
        self._sim_deals.append(order_record)
        return order_record

    def modify_order(
        self,
        order_id: str,
        qty: float,
        price: float,
        trd_env: Any = TrdEnv.SIMULATE,
    ) -> dict[str, Any]:
        """Modify an open simulated order. Strictly asserts TrdEnv.SIMULATE."""
        assert_simulate_env(trd_env)
        assert_simulate_env(self.trd_env)
        if self.connected and self._trade_ctx is not None:
            ret, data = self._trade_ctx.modify_order(
                ModifyOrderOp.NORMAL,
                order_id,
                float(qty),
                float(price),
                trd_env=assert_simulate_env(TrdEnv.SIMULATE),
            )
            if ret != RET_OK:
                raise RuntimeError(f"Moomoo OpenD modify_order failed: {data}")
            return {"order_id": order_id, "status": "MODIFIED", "trd_env": "SIMULATE"}
        return {"order_id": order_id, "status": "MODIFIED_SIM", "qty": qty, "price": price, "trd_env": "SIMULATE"}

    def cancel_order(self, order_id: str, trd_env: Any = TrdEnv.SIMULATE) -> dict[str, Any]:
        """Cancel an open simulated order. Strictly asserts TrdEnv.SIMULATE."""
        assert_simulate_env(trd_env)
        assert_simulate_env(self.trd_env)
        if self.connected and self._trade_ctx is not None:
            ret, data = self._trade_ctx.modify_order(
                ModifyOrderOp.CANCEL,
                order_id,
                0,
                0,
                trd_env=assert_simulate_env(TrdEnv.SIMULATE),
            )
            if ret != RET_OK:
                raise RuntimeError(f"Moomoo OpenD cancel_order failed: {data}")
            return {"order_id": order_id, "status": "CANCELLED", "trd_env": "SIMULATE"}
        if self.mode == "MOOMOO_CLI_SIMULATE":
            self._run_moomoo_cli(
                [
                    "sim",
                    "order",
                    "cancel",
                    str(order_id),
                    "--account",
                    self.sim_account_id,
                    "--market",
                    self.settings.moomoo_trd_market,
                ]
            )
            return {"order_id": str(order_id), "status": "CANCELLED", "trd_env": "SIMULATE"}
        return {"order_id": order_id, "status": "CANCELLED_SIM", "trd_env": "SIMULATE"}

    def fetch_historical_klines(
        self,
        ticker: str,
        start: str,
        end: str,
        ktype: str = "K_DAY",
    ) -> pd.DataFrame:
        """Fetch historical OHLCV via Moomoo request_history_kline, with yfinance fallback."""
        code = normalize_ticker(ticker)
        if self.connected and self._quote_ctx is not None:
            kl_enum = getattr(KLType, ktype, KLType.K_DAY)
            frames = []
            page_req_key = None
            while True:
                ret, df, page_req_key = self._quote_ctx.request_history_kline(
                    code,
                    start=start,
                    end=end,
                    ktype=kl_enum,
                    max_count=1000,
                    page_req_key=page_req_key,
                )
                if ret != RET_OK:
                    break
                if isinstance(df, pd.DataFrame) and not df.empty:
                    frames.append(df)
                if page_req_key is None:
                    break
            if frames:
                combined = pd.concat(frames, ignore_index=True)
                combined = combined.rename(columns={"time_key": "date"})
                combined["date"] = pd.to_datetime(combined["date"])
                return combined[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)

        # yfinance fallback
        import yfinance as yf

        sym = strip_market_prefix(code)
        interval_map = {"K_1M": "1m", "K_5M": "5m", "K_60M": "1h", "K_DAY": "1d"}
        df = yf.download(sym, start=start, end=end, interval=interval_map.get(ktype, "1d"), progress=False, auto_adjust=True)
        if df.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df.reset_index()
        date_col = "Datetime" if "Datetime" in df.columns else "Date"
        df = df.rename(
            columns={
                date_col: "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        return df[["date", "open", "high", "low", "close", "volume"]].dropna().reset_index(drop=True)
