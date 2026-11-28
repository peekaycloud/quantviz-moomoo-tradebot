"""FastAPI + WebSocket Server for the Autonomous AI Quantitative Trading Bot."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.ai_advisor import AIAdvisor
from backend.database import TradeDatabase
from backend.moomoo_client import MoomooPaperClient, TrdEnv, normalize_ticker, strip_market_prefix
from backend.risk_manager import RiskManager
from backtester.backtest_engine import BacktestEngine
from config.settings import get_settings
from skills.social_research_skills import SocialResearchOrchestrator
from strategies.indicators import compute_tradingview_technicals, summarize_technicals

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class BacktestRequest(BaseModel):
    ticker: str = "US.NVDA"
    strategy: str = "rsi_macd_momentum"
    benchmark: str = "US.SPY"
    lookback_days: int = Field(default=730, ge=90, le=3650)


class OrderRequest(BaseModel):
    ticker: str
    side: str
    qty: float = Field(gt=0)
    price: float = 0.0
    order_type: str = "MARKET"
    stop_loss: float | None = None
    take_profit: float | None = None


class ModeRequest(BaseModel):
    mode: str


class WatchlistAddRequest(BaseModel):
    tickers: str


class BatchScanRequest(BaseModel):
    tickers: list[str] | None = None


class BotRuntime:
    """Shared runtime state across FastAPI routes and background tasks."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.db = TradeDatabase(self.settings.duckdb_path)
        self.moomoo = MoomooPaperClient(self.settings)
        self.backtester = BacktestEngine(self.moomoo, self.settings)
        self.researcher = SocialResearchOrchestrator(self.settings)
        self.advisor = AIAdvisor(self.settings)
        self.risk = RiskManager(self.settings)
        self.watchlist: list[str] = list(self.settings.watchlist_symbols)
        self.ws_clients: set[WebSocket] = set()
        self._poll_task: asyncio.Task[None] | None = None
        self._refresh_lock = asyncio.Lock()
        self.last_account: dict[str, Any] | None = None
        self.last_positions: list[dict[str, Any]] = []
        self.last_orders: list[dict[str, Any]] = []
        self.quote_cache: dict[str, dict[str, Any]] = {}
        self._movers_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._history_cache: tuple[float, list[dict[str, Any]]] | None = None

    async def start(self) -> None:
        self.moomoo.connect()
        self.moomoo.subscribe_watchlist()
        self._poll_task = asyncio.create_task(self._background_poll_loop())

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._poll_task
        self.moomoo.close()
        self.db.close()

    async def account_and_positions(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Cached account/positions for risk checks; fresh fetch only before first tick."""
        if self.last_account is not None:
            return self.last_account, self.last_positions
        positions = await asyncio.to_thread(self.moomoo.get_positions)
        account = await asyncio.to_thread(self.moomoo.get_account_info, positions)
        return account, positions

    async def refresh_snapshot(self, full: bool = True) -> None:
        """Refresh cached portfolio state and broadcast.

        full=True fetches positions/quotes/orders/funds in one parallel wave.
        full=False fetches quotes only and marks cached positions/funds to market,
        keeping CLI call volume under the Moomoo API rate limit.
        """
        async with self._refresh_lock:
            if not full and self.last_account is None:
                full = True
            tickers = list(dict.fromkeys(self.watchlist + [p["ticker"] for p in self.last_positions]))
            if full:
                positions, quotes, orders, account = await asyncio.gather(
                    asyncio.to_thread(self.moomoo.get_positions),
                    asyncio.to_thread(self.moomoo.get_live_quotes, tickers),
                    asyncio.to_thread(self.moomoo.get_orders),
                    asyncio.to_thread(self.moomoo.get_account_info, self.last_positions),
                )
            else:
                quotes = await asyncio.to_thread(self.moomoo.get_live_quotes, tickers)
                positions = [dict(p) for p in self.last_positions]
                orders = self.last_orders
                account = dict(self.last_account) if self.last_account else None

            quote_map = {q["ticker"]: float(q["price"]) for q in quotes if float(q.get("price", 0.0)) > 0}
            for p in positions:
                live_px = quote_map.get(p["ticker"])
                if live_px and p.get("avg_cost"):
                    qty = float(p["qty"])
                    avg = float(p["avg_cost"])
                    p["current_price"] = round(live_px, 4)
                    p["market_value"] = round(qty * live_px, 2)
                    pnl = (live_px - avg) * qty
                    p["unrealized_pnl"] = round(pnl, 2)
                    p["unrealized_pnl_pct"] = round((pnl / (abs(qty) * avg)) * 100.0, 2)

            if full:
                if self.moomoo.mode == "LOCAL_SIM_FALLBACK":
                    account = self.moomoo.get_account_info(positions)
                elif self.moomoo.mode != "OPEND_SIMULATE":
                    # Funds were fetched against last cycle's positions; rebase P&L on fresh prices.
                    unrealized = round(sum(p["unrealized_pnl"] for p in positions), 2)
                    account["unrealized_pnl"] = unrealized
                    account["realized_pnl"] = round(account["day_pnl"] - unrealized, 2)
            elif account is not None and self.moomoo.mode == "MOOMOO_CLI_SIMULATE":
                # Delta-adjust from the broker's authoritative NAV: moomoo's cash
                # balance excludes short-sale proceeds, so cash + sum(market_value)
                # understates NAV whenever shorts are held.
                prev_mv = sum(p.get("market_value", 0.0) for p in self.last_positions)
                delta = round(sum(p["market_value"] for p in positions) - prev_mv, 2)
                unrealized = round(sum(p["unrealized_pnl"] for p in positions), 2)
                nav = round(account["nav"] + delta, 2)
                day_pnl = round(nav - 100_000.0, 2)
                account["nav"] = nav
                account["day_pnl"] = day_pnl
                account["day_pnl_pct"] = round(day_pnl / 100_000.0 * 100.0, 2)
                account["unrealized_pnl"] = unrealized
                account["realized_pnl"] = round(day_pnl - unrealized, 2)

            for q in quotes:
                self.quote_cache[q["ticker"]] = q
            self.last_positions = positions
            self.last_orders = orders
            self.last_account = account
            await asyncio.to_thread(self.db.record_portfolio_snapshot, account)

            await self._broadcast(
                {
                    "type": "tick_update",
                    "account": account,
                    "positions": positions,
                    "quotes": quotes,
                    "orders": orders,
                    "execution_mode": self.risk.execution_mode,
                    "circuit_breaker": self.risk.is_circuit_breaker_tripped(account),
                }
            )

    def schedule_refresh(self) -> None:
        """Trigger an immediate post-trade refresh without blocking the response."""

        async def _safe() -> None:
            try:
                await self.refresh_snapshot()
            except Exception as exc:
                logger.debug("Post-trade refresh error: %s", exc)

        asyncio.create_task(_safe())

    async def _background_poll_loop(self) -> None:
        cycle = 0
        while True:
            # CLI mode: alternate quote-only fast cycles with full refreshes to
            # stay under the Moomoo API rate limit; other modes are cheap locally.
            full = self.moomoo.mode != "MOOMOO_CLI_SIMULATE" or cycle % 2 == 0
            try:
                await self.refresh_snapshot(full=full)
            except Exception as exc:
                logger.debug("Background poll error: %s", exc)
            cycle += 1
            await asyncio.sleep(0.5)

    async def _broadcast(self, message: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for ws in list(self.ws_clients):
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.ws_clients.discard(ws)


runtime = BotRuntime()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await runtime.start()
    yield
    await runtime.stop()


app = FastAPI(
    title="Autonomous AI Quantitative Trading Bot (Moomoo Paper)",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "trd_env": "SIMULATE",
        "moomoo_connected": runtime.moomoo.connected,
        "moomoo_mode": runtime.moomoo.mode,
        "execution_mode": runtime.risk.execution_mode,
    }


@app.get("/api/portfolio")
async def get_portfolio() -> dict[str, Any]:
    if runtime.last_account is None:
        await runtime.refresh_snapshot()
    account = runtime.last_account or await asyncio.to_thread(runtime.moomoo.get_account_info)
    orders = runtime.last_orders
    deals = await asyncio.to_thread(runtime.moomoo.get_deals)
    history = runtime.db.get_portfolio_history(limit=200)
    db_orders = runtime.db.get_orders(limit=100)
    return {
        "account": account,
        "positions": runtime.last_positions,
        "orders": orders or db_orders,
        "deals": deals,
        "nav_history": history,
        "execution_mode": runtime.risk.execution_mode,
        "circuit_breaker_tripped": runtime.risk.is_circuit_breaker_tripped(account),
    }


@app.get("/api/trade-history")
async def get_trade_history() -> dict[str, Any]:
    """Complete simulated order history (all sessions), paginated from the broker."""
    now = time.time()
    if runtime._history_cache is not None and now - runtime._history_cache[0] < 60.0:
        orders = runtime._history_cache[1]
    else:
        orders = await asyncio.to_thread(runtime.moomoo.get_order_history)
        runtime._history_cache = (now, orders)
    return {"orders": orders, "count": len(orders)}


@app.post("/api/positions/close-all")
async def close_all_positions() -> dict[str, Any]:
    """Close or cover all open paper positions concurrently in TrdEnv.SIMULATE."""
    account, _ = await runtime.account_and_positions()
    positions = await asyncio.to_thread(runtime.moomoo.get_positions)
    if not positions:
        return {"closed_count": 0, "orders": []}

    async def _close_one(pos: dict[str, Any]) -> dict[str, Any]:
        qty = abs(float(pos["qty"]))
        side = "BUY" if float(pos["qty"]) < 0 else "SELL"
        price = float(pos.get("current_price") or 0.0)
        check = runtime.risk.validate_order(
            ticker=pos["ticker"],
            side=side,
            qty=qty,
            price=price,
            stop_loss=None,
            account_info=account,
            positions=positions,
        )
        if not check.approved:
            return {"ticker": pos["ticker"], "error": check.reason}
        order = await asyncio.to_thread(
            runtime.moomoo.place_order,
            pos["ticker"],
            side,
            qty,
            price,
            "MARKET",
            None,
            None,
            TrdEnv.SIMULATE,
        )
        await asyncio.to_thread(runtime.db.log_order, order)
        return order

    results = await asyncio.gather(*(_close_one(p) for p in positions))
    closed = [r for r in results if "order_id" in r]
    runtime.schedule_refresh()
    return {"closed_count": len(closed), "orders": results}


SP500_HIGH_MOVERS_UNIVERSE = [
    "US.MU", "US.NVDA", "US.AMD", "US.AVGO", "US.TSM", "US.ASML", "US.ARM",
    "US.MRVL", "US.SMCI", "US.INTC", "US.QCOM", "US.AAPL", "US.MSFT", "US.GOOGL",
    "US.AMZN", "US.META", "US.TSLA", "US.NFLX", "US.PLTR", "US.CRWD", "US.PANW",
    "US.SNOW", "US.CRM", "US.ORCL", "US.COIN", "US.LLY", "US.UNH", "US.JPM",
    "US.GS", "US.V", "US.MA", "US.COST", "US.WMT", "US.BA", "US.XOM",
    "US.SPY", "US.QQQ",
]


@app.get("/api/quotes")
async def get_quotes() -> dict[str, Any]:
    tickers = list(dict.fromkeys(runtime.watchlist + [p["ticker"] for p in runtime.last_positions]))
    missing = [t for t in tickers if t not in runtime.quote_cache]
    if missing:
        fresh = await asyncio.to_thread(runtime.moomoo.get_live_quotes, missing)
        for q in fresh:
            runtime.quote_cache[q["ticker"]] = q
    quotes = [dict(runtime.quote_cache[t]) for t in tickers if t in runtime.quote_cache]
    recs = {strip_market_prefix(r["ticker"]): r for r in runtime.db.get_recommendations(limit=50)}
    for q in quotes:
        rec = recs.get(q["symbol"])
        q["ai_verdict"] = rec["verdict"] if rec else "HOLD"
        q["ai_confidence"] = rec["confidence"] if rec else 50.0
        q["social_score"] = rec["social_score"] if rec else 0.0
    return {"quotes": quotes}


@app.post("/api/watchlist/add")
async def add_watchlist_tickers(req: WatchlistAddRequest) -> dict[str, Any]:
    """Add one or multiple comma/space-separated tickers; fetch quotes only for the new symbols."""
    raw_tokens = [t.strip() for t in req.tickers.replace("\n", ",").replace(" ", ",").split(",") if t.strip()]
    added: list[str] = []
    for tok in raw_tokens:
        code = normalize_ticker(tok)
        if code not in runtime.watchlist:
            runtime.watchlist.append(code)
            added.append(code)
    if added:
        fresh = await asyncio.to_thread(runtime.moomoo.get_live_quotes, added)
        for q in fresh:
            runtime.quote_cache[q["ticker"]] = q
        await asyncio.to_thread(runtime.moomoo.subscribe_watchlist, added)
    quotes = (await get_quotes())["quotes"]
    return {"added": added, "watchlist": runtime.watchlist, "quotes": quotes}


@app.delete("/api/watchlist/{ticker}")
def remove_watchlist_ticker(ticker: str) -> dict[str, Any]:
    """Remove a ticker from the live watchlist."""
    code = normalize_ticker(ticker)
    runtime.watchlist = [t for t in runtime.watchlist if t != code]
    return {"removed": code, "watchlist": runtime.watchlist}


@app.get("/api/movers")
async def get_high_movers(limit: int = 12) -> dict[str, Any]:
    """Scan S&P 500 / high-beta momentum universe and rank by |change_pct| (30s cache)."""
    now = time.monotonic()
    if runtime._movers_cache is None or now - runtime._movers_cache[0] > 30.0:
        universe_quotes = await asyncio.to_thread(runtime.moomoo.get_live_quotes, SP500_HIGH_MOVERS_UNIVERSE)
        runtime._movers_cache = (now, universe_quotes)
    quotes = [dict(q) for q in runtime._movers_cache[1]]
    recs = {strip_market_prefix(r["ticker"]): r for r in runtime.db.get_recommendations(limit=50)}
    for q in quotes:
        rec = recs.get(q["symbol"])
        q["ai_verdict"] = rec["verdict"] if rec else "HOLD"
        q["ai_confidence"] = rec["confidence"] if rec else 50.0
    ranked = sorted(quotes, key=lambda x: abs(float(x.get("change_pct", 0.0))), reverse=True)
    return {"movers": ranked[:limit], "universe_size": len(SP500_HIGH_MOVERS_UNIVERSE)}


@app.get("/api/technicals/{ticker}")
async def get_tradingview_technicals(ticker: str, timeframe: str = "1D") -> dict[str, Any]:
    """Return TradingView-style Oscillators, Moving Averages, and Gauge summaries."""
    code = normalize_ticker(ticker)
    tf = timeframe.upper()
    if tf not in ("1H", "1D", "1W"):
        tf = "1D"
    ktype = "K_60M" if tf == "1H" else "K_DAY"
    lookback = 60 if tf == "1H" else (1095 if tf == "1W" else 365)

    ohlcv, live_quotes = await asyncio.gather(
        asyncio.to_thread(runtime.backtester.load_ohlcv, code, lookback, ktype),
        asyncio.to_thread(runtime.moomoo.get_live_quotes, [code]),
    )
    live_px = float(live_quotes[0]["price"]) if live_quotes and float(live_quotes[0].get("price", 0.0)) > 0 else None
    tv = compute_tradingview_technicals(ohlcv, live_price=live_px, timeframe=tf)
    return {
        "ticker": code,
        "symbol": strip_market_prefix(code),
        "quote": live_quotes[0] if live_quotes else None,
        **tv,
    }


@app.post("/api/scan-movers")
async def scan_top_movers(top_n: int = 10) -> dict[str, Any]:
    """Run AI Research & Signal generation across the top N high-moving stocks."""
    movers_data = await get_high_movers(top_n)
    top_tickers = [m["ticker"] for m in movers_data["movers"]]
    sem = asyncio.Semaphore(6)

    async def _bounded(t: str) -> Any:
        async with sem:
            return await run_research_and_signal(t)

    results = await asyncio.gather(*(_bounded(t) for t in top_tickers), return_exceptions=True)
    scanned = [r["recommendation"] for r in results if isinstance(r, dict) and "recommendation" in r]
    return {"scanned_count": len(scanned), "recommendations": scanned}


@app.post("/api/scan-batch")
async def scan_batch_tickers(req: BatchScanRequest) -> dict[str, Any]:
    """Scan all watchlist tickers (or a custom list) concurrently for AI recommendations."""
    targets = [normalize_ticker(t) for t in (req.tickers or runtime.watchlist)][:25]
    sem = asyncio.Semaphore(6)

    async def _bounded(t: str) -> Any:
        async with sem:
            return await run_research_and_signal(t)

    results = await asyncio.gather(*(_bounded(t) for t in targets), return_exceptions=True)
    scanned = [r["recommendation"] for r in results if isinstance(r, dict) and "recommendation" in r]
    return {"scanned_count": len(scanned), "recommendations": scanned}


@app.get("/api/social")
def get_social(ticker: str | None = None, limit: int = 100) -> dict[str, Any]:
    items = runtime.db.get_social_items(ticker=ticker, limit=limit)
    return {"items": items}


@app.get("/api/recommendations")
async def get_recommendations(limit: int = 30) -> dict[str, Any]:
    recs = runtime.db.get_recommendations(limit=limit)
    pending_tickers = list({r["ticker"] for r in recs if r.get("status") == "PENDING"})
    if pending_tickers:
        live_map = {
            t: float(runtime.quote_cache[t]["price"])
            for t in pending_tickers
            if t in runtime.quote_cache and float(runtime.quote_cache[t].get("price", 0.0)) > 0
        }
        missing = [t for t in pending_tickers if t not in live_map]
        if missing:
            fresh = await asyncio.to_thread(runtime.moomoo.get_live_quotes, missing)
            for q in fresh:
                runtime.quote_cache[q["ticker"]] = q
                if float(q.get("price", 0.0)) > 0:
                    live_map[q["ticker"]] = float(q["price"])
        for r in recs:
            if r.get("status") != "PENDING":
                continue
            live_px = live_map.get(r["ticker"])
            if not live_px:
                continue
            old_mid = (float(r["entry_low"]) + float(r["entry_high"])) / 2.0
            stop_dist = abs(float(r["stop_loss"]) - old_mid) or (live_px * 0.04)
            rr = float(r.get("risk_reward") or runtime.settings.default_rr_target)
            r["entry_low"] = round(live_px * 0.995, 2)
            r["entry_high"] = round(live_px * 1.005, 2)
            if "SELL" in r.get("verdict", ""):
                r["stop_loss"] = round(live_px + stop_dist, 2)
                r["take_profit_1"] = round(max(0.01, live_px - stop_dist * 1.5), 2)
                r["take_profit_2"] = round(max(0.01, live_px - stop_dist * rr), 2)
            else:
                r["stop_loss"] = round(max(0.01, live_px - stop_dist), 2)
                r["take_profit_1"] = round(live_px + stop_dist * 1.5, 2)
                r["take_profit_2"] = round(live_px + stop_dist * rr, 2)
    return {"recommendations": recs}


@app.post("/api/research/{ticker}")
async def run_research_and_signal(ticker: str) -> dict[str, Any]:
    """Gather login-free social/news intelligence, compute technicals, and generate AI Recommendation."""
    code = normalize_ticker(ticker)
    sym = strip_market_prefix(code)

    intel_task = runtime.researcher.gather_ticker_intelligence(sym)
    ohlcv_task = asyncio.to_thread(runtime.backtester.load_ohlcv, code, 365)
    quote_task = asyncio.to_thread(runtime.moomoo.get_live_quotes, [code])
    intel, ohlcv, live_quotes = await asyncio.gather(intel_task, ohlcv_task, quote_task)

    runtime.db.save_social_items(intel["items"])
    technicals = summarize_technicals(ohlcv)
    if live_quotes and float(live_quotes[0].get("price", 0.0)) > 0:
        technicals["price"] = float(live_quotes[0]["price"])

    # Pull latest backtest win probability if available
    past_bts = runtime.db.get_backtest_runs(ticker=code, limit=1)
    win_prob = (past_bts[0]["win_rate"] / 100.0) if past_bts else 0.55

    card = await runtime.advisor.generate_recommendation(
        ticker=code,
        technicals=technicals,
        intelligence=intel,
        backtest_win_prob=win_prob,
    )
    runtime.db.save_recommendation(card.to_db_record())

    # If FULL_AUTO and strong actionable signal, auto-submit through RiskManager
    auto_order = None
    if runtime.risk.execution_mode == "FULL_AUTO" and card.verdict in ("STRONG BUY", "BUY"):
        account, _ = await runtime.account_and_positions()
        positions = await asyncio.to_thread(runtime.moomoo.get_positions)
        entry_price = (card.trade_plan.entry_low + card.trade_plan.entry_high) / 2.0
        target_notional = account["nav"] * runtime.settings.max_position_pct * 0.8
        qty = float(int(target_notional // max(entry_price, 0.01)))
        if qty >= 1:
            check = runtime.risk.validate_order(
                ticker=code,
                side="BUY",
                qty=qty,
                price=entry_price,
                stop_loss=card.trade_plan.stop_loss,
                account_info=account,
                positions=positions,
            )
            if check.approved:
                auto_order = await asyncio.to_thread(
                    runtime.moomoo.place_order,
                    ticker=code,
                    side="BUY",
                    qty=qty,
                    price=entry_price,
                    order_type="MARKET",
                    stop_loss=card.trade_plan.stop_loss,
                    take_profit=card.trade_plan.take_profit_1,
                    trd_env=TrdEnv.SIMULATE,
                )
                await asyncio.to_thread(runtime.db.log_order, auto_order)
                runtime.db.update_recommendation_status(card.rec_id, "AUTO_EXECUTED")
                runtime.schedule_refresh()

    return {
        "recommendation": card.model_dump(),
        "technicals": technicals,
        "intelligence_summary": {
            "buzz_count": intel["buzz_count"],
            "social_score": intel["social_score"],
            "catalyst_score": intel["catalyst_score"],
        },
        "auto_order": auto_order,
    }


@app.post("/api/recommendations/{rec_id}/execute")
async def execute_recommendation(rec_id: str) -> dict[str, Any]:
    """1-Click Approve execution of a pending AI Recommendation in Paper Trading."""
    recs = runtime.db.get_recommendations(limit=100)
    rec = next((r for r in recs if r["rec_id"] == rec_id), None)
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found.")

    verdict = rec["verdict"]
    if verdict == "HOLD":
        raise HTTPException(status_code=400, detail="HOLD recommendation has no trade action.")

    side = "BUY" if "BUY" in verdict else "SELL"
    account, _ = await runtime.account_and_positions()
    positions = await asyncio.to_thread(runtime.moomoo.get_positions)
    cached = runtime.quote_cache.get(rec["ticker"])
    if cached and float(cached.get("price", 0.0)) > 0:
        price = float(cached["price"])
    else:
        live_quotes = await asyncio.to_thread(runtime.moomoo.get_live_quotes, [rec["ticker"]])
        price = (
            float(live_quotes[0]["price"])
            if live_quotes and live_quotes[0].get("price", 0) > 0
            else (float(rec["entry_low"]) + float(rec["entry_high"])) / 2.0
        )

    if side == "BUY":
        target_notional = account["nav"] * runtime.settings.max_position_pct * 0.8
        qty = float(max(1, int(target_notional // max(price, 0.01))))
    else:
        held = next((p for p in positions if p["ticker"] == rec["ticker"]), None)
        if held and float(held["qty"]) > 0:
            qty = float(held["qty"])
        else:
            # Size a new paper short sale within the 5% position cap
            target_notional = account["nav"] * runtime.settings.max_position_pct * 0.8
            qty = float(max(1, int(target_notional // max(price, 0.01))))

    check = runtime.risk.validate_order(
        ticker=rec["ticker"],
        side=side,
        qty=qty,
        price=price,
        stop_loss=rec["stop_loss"],
        account_info=account,
        positions=positions,
    )
    if not check.approved and check.max_allowed_qty >= 1:
        qty = float(int(check.max_allowed_qty))
        check = runtime.risk.validate_order(
            ticker=rec["ticker"],
            side=side,
            qty=qty,
            price=price,
            stop_loss=rec["stop_loss"],
            account_info=account,
            positions=positions,
        )
    if not check.approved:
        raise HTTPException(status_code=400, detail=check.reason)

    order = await asyncio.to_thread(
        runtime.moomoo.place_order,
        ticker=rec["ticker"],
        side=side,
        qty=qty,
        price=price,
        order_type="MARKET",
        stop_loss=rec["stop_loss"],
        take_profit=rec["take_profit_1"],
        trd_env=TrdEnv.SIMULATE,
    )
    await asyncio.to_thread(runtime.db.log_order, order)
    runtime.db.update_recommendation_status(rec_id, "EXECUTED")
    runtime.schedule_refresh()
    return {"status": "EXECUTED", "order": order}


@app.post("/api/orders")
async def place_manual_order(req: OrderRequest) -> dict[str, Any]:
    """Place a manual paper trading order guarded by RiskManager and TrdEnv.SIMULATE."""
    code = normalize_ticker(req.ticker)
    account, positions = await runtime.account_and_positions()
    held = next((p for p in positions if p["ticker"] == code), None)
    held_qty = float(held["qty"]) if held else 0.0
    # Opens/adds must see broker truth: a cached position set that predates a
    # mid-refresh fill lets a second add pass as "new" and breach the size cap.
    # Pure closes/covers are cap-exempt, so they stay on the fast cached path.
    pure_close = (req.side.upper() == "SELL" and held_qty >= req.qty) or (
        req.side.upper() == "BUY" and held_qty < 0 and req.qty <= abs(held_qty)
    )
    if not pure_close:
        positions = await asyncio.to_thread(runtime.moomoo.get_positions)
        held = next((p for p in positions if p["ticker"] == code), None)
        held_qty = float(held["qty"]) if held else 0.0

    ref_price = req.price
    if ref_price <= 0:
        cached = runtime.quote_cache.get(code)
        if cached and float(cached.get("price", 0.0)) > 0:
            ref_price = float(cached["price"])
        else:
            quotes = await asyncio.to_thread(runtime.moomoo.get_live_quotes, [code])
            ref_price = quotes[0]["price"] if quotes else 100.0

    check = runtime.risk.validate_order(
        ticker=code,
        side=req.side,
        qty=req.qty,
        price=ref_price,
        stop_loss=req.stop_loss,
        account_info=account,
        positions=positions,
    )
    if not check.approved:
        raise HTTPException(status_code=400, detail=check.reason)

    order = await asyncio.to_thread(
        runtime.moomoo.place_order,
        ticker=code,
        side=req.side,
        qty=req.qty,
        price=ref_price,
        order_type=req.order_type,
        stop_loss=req.stop_loss,
        take_profit=req.take_profit,
        trd_env=TrdEnv.SIMULATE,
    )
    await asyncio.to_thread(runtime.db.log_order, order)
    runtime.schedule_refresh()
    return {"status": "OK", "order": order}


@app.post("/api/orders/{order_id}/cancel")
async def cancel_order_endpoint(order_id: str) -> dict[str, Any]:
    """Cancel an open/queued simulated order in TrdEnv.SIMULATE."""
    try:
        res = await asyncio.to_thread(runtime.moomoo.cancel_order, order_id, trd_env=TrdEnv.SIMULATE)
        runtime.schedule_refresh()
        return res
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/orders/cancel-all")
async def cancel_all_open_orders() -> dict[str, Any]:
    """Cancel all OPEN/SUBMITTED simulated orders concurrently."""
    orders = await asyncio.to_thread(runtime.moomoo.get_orders)
    open_orders = [o for o in orders if str(o.get("status", "")).upper() in ("OPEN", "SUBMITTED", "UNSUBMITTED")]
    if not open_orders:
        return {"cancelled_count": 0, "results": []}

    async def _cancel_one(oid: str) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(runtime.moomoo.cancel_order, oid, TrdEnv.SIMULATE)
        except Exception as exc:
            return {"order_id": oid, "error": str(exc)}

    results = await asyncio.gather(*(_cancel_one(str(o["order_id"])) for o in open_orders))
    cancelled = [r for r in results if r.get("status") in ("CANCELLED", "CANCELLED_SIM")]
    runtime.schedule_refresh()
    return {"cancelled_count": len(cancelled), "results": results}


@app.post("/api/backtest")
def run_backtest(req: BacktestRequest) -> dict[str, Any]:
    """Run automated walk-forward backtest + Auto-Gatekeeper validation."""
    result = runtime.backtester.run_backtest(
        ticker=req.ticker,
        strategy_name=req.strategy,
        benchmark=req.benchmark,
        lookback_days=req.lookback_days,
    )
    payload = result.to_dict()
    runtime.db.save_backtest_run(payload)
    return payload


@app.get("/api/backtests")
def list_backtests(ticker: str | None = None, limit: int = 30) -> dict[str, Any]:
    return {"backtests": runtime.db.get_backtest_runs(ticker=ticker, limit=limit)}


@app.post("/api/settings/mode")
def set_execution_mode(req: ModeRequest) -> dict[str, Any]:
    try:
        mode = runtime.risk.set_execution_mode(req.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"execution_mode": mode}


@app.websocket("/ws/stream")
async def websocket_stream(ws: WebSocket) -> None:
    await ws.accept()
    runtime.ws_clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        runtime.ws_clients.discard(ws)


# Serve built React frontend at / when frontend/dist exists
_DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _DIST_DIR.exists():
    _ASSETS_DIR = _DIST_DIR / "assets"
    if _ASSETS_DIR.exists():
        app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")

    @app.get("/")
    def serve_frontend_root() -> FileResponse:
        return FileResponse(str(_DIST_DIR / "index.html"), headers={"Cache-Control": "no-store"})
