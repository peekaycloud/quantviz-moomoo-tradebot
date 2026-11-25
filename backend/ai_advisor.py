"""AI Decision Engine & Buy/Hold/Sell Recommendation Pipeline.

Synthesizes Technical Indicators, Backtest Win Probability, and Social/News
Sentiment into a strict Pydantic RecommendationCard schema.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class TradePlan(BaseModel):
    """Actionable entry, ATR stop-loss, take-profit targets, and R:R ratio."""

    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward: float = Field(ge=0.0)


class RecommendationCard(BaseModel):
    """Strict JSON schema for AI Buy/Hold/Sell recommendation."""

    rec_id: str
    ticker: str
    verdict: Literal["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"]
    confidence: float = Field(ge=0.0, le=100.0)
    technical_score: float = Field(ge=-1.0, le=1.0)
    catalyst_score: float = Field(ge=-1.0, le=1.0)
    social_score: float = Field(ge=-1.0, le=1.0)
    trade_plan: TradePlan
    thesis: list[str] = Field(min_length=3, max_length=3)
    status: str = "PENDING"

    def to_db_record(self) -> dict[str, Any]:
        return {
            "rec_id": self.rec_id,
            "ticker": self.ticker,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "technical_score": self.technical_score,
            "catalyst_score": self.catalyst_score,
            "social_score": self.social_score,
            "entry_low": self.trade_plan.entry_low,
            "entry_high": self.trade_plan.entry_high,
            "stop_loss": self.trade_plan.stop_loss,
            "take_profit_1": self.trade_plan.take_profit_1,
            "take_profit_2": self.trade_plan.take_profit_2,
            "risk_reward": self.trade_plan.risk_reward,
            "thesis": self.thesis,
            "status": self.status,
        }


class _LLMOutput(BaseModel):
    verdict: Literal["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"]
    confidence: float = Field(ge=0.0, le=100.0)
    thesis: list[str] = Field(min_length=3, max_length=3)


class AIAdvisor:
    """Generates structured Buy/Hold/Sell recommendations using LLM or quant fallback."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _compute_trade_plan(self, price: float, atr: float, verdict: str) -> TradePlan:
        safe_price = max(price, 0.01)
        safe_atr = atr if atr > 0 else safe_price * 0.02
        stop_dist = safe_atr * self.settings.default_atr_stop_mult
        rr = self.settings.default_rr_target

        if verdict in ("SELL", "STRONG SELL"):
            stop_loss = round(safe_price + stop_dist, 2)
            tp1 = round(max(0.01, safe_price - stop_dist * 1.5), 2)
            tp2 = round(max(0.01, safe_price - stop_dist * rr), 2)
        else:
            stop_loss = round(max(0.01, safe_price - stop_dist), 2)
            tp1 = round(safe_price + stop_dist * 1.5, 2)
            tp2 = round(safe_price + stop_dist * rr, 2)

        return TradePlan(
            entry_low=round(safe_price * 0.995, 2),
            entry_high=round(safe_price * 1.005, 2),
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            risk_reward=round(rr, 2),
        )

    def _quant_synthesize(
        self,
        ticker: str,
        technicals: dict[str, Any],
        intelligence: dict[str, Any],
        backtest_win_prob: float,
    ) -> tuple[Literal["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"], float, list[str]]:
        tech_score = float(technicals.get("technical_score", 0.0))
        cat_score = float(intelligence.get("catalyst_score", 0.0))
        soc_score = float(intelligence.get("social_score", 0.0))
        bt_bias = (backtest_win_prob - 0.5) * 1.2  # [-0.6, +0.6]

        composite = 0.45 * tech_score + 0.25 * cat_score + 0.15 * soc_score + 0.15 * bt_bias
        if composite >= 0.45:
            verdict: Literal["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"] = "STRONG BUY"
        elif composite >= 0.15:
            verdict = "BUY"
        elif composite <= -0.45:
            verdict = "STRONG SELL"
        elif composite <= -0.15:
            verdict = "SELL"
        else:
            verdict = "HOLD"

        confidence = round(min(95.0, max(35.0, 50.0 + abs(composite) * 55.0)), 1)
        triggers = technicals.get("triggers", ["Neutral technical posture"])
        top_items = intelligence.get("items", [])[:2]
        source_summary = (
            "; ".join(f"[{it.get('platform')}] {it.get('title', '')[:65]}" for it in top_items)
            if top_items
            else "No high-impact social/news catalysts detected in current window"
        )

        thesis = [
            f"Technicals ({tech_score:+.2f}): {triggers[0]} (RSI={technicals.get('rsi_14', 50):.1f}, Vol={technicals.get('vol_ratio', 1.0):.2f}x).",
            f"Catalysts & Sentiment (News={cat_score:+.2f}, Social={soc_score:+.2f}): {source_summary}.",
            f"Backtest Win Probability: {backtest_win_prob*100:.1f}% historical win rate with ATR-calibrated {self.settings.default_rr_target:.1f}R target.",
        ]
        return verdict, confidence, thesis

    async def generate_recommendation(
        self,
        ticker: str,
        technicals: dict[str, Any],
        intelligence: dict[str, Any],
        backtest_win_prob: float = 0.55,
    ) -> RecommendationCard:
        """Synthesize technicals, backtest probability, and social/news into a RecommendationCard."""
        price = float(technicals.get("price", 100.0))
        atr = float(technicals.get("atr_14", price * 0.02))
        tech_score = float(technicals.get("technical_score", 0.0))
        cat_score = float(intelligence.get("catalyst_score", 0.0))
        soc_score = float(intelligence.get("social_score", 0.0))

        verdict, confidence, thesis = self._quant_synthesize(
            ticker, technicals, intelligence, backtest_win_prob
        )

        # Optional LLM refinement when API key is configured
        prompt = self._build_prompt(ticker, technicals, intelligence, backtest_win_prob)
        llm_out = await self._call_llm(prompt)
        if llm_out is not None:
            verdict = llm_out.verdict
            confidence = llm_out.confidence
            thesis = llm_out.thesis

        trade_plan = self._compute_trade_plan(price, atr, verdict)

        return RecommendationCard(
            rec_id=f"REC-{uuid.uuid4().hex[:8].upper()}",
            ticker=ticker.upper(),
            verdict=verdict,
            confidence=confidence,
            technical_score=max(-1.0, min(1.0, tech_score)),
            catalyst_score=max(-1.0, min(1.0, cat_score)),
            social_score=max(-1.0, min(1.0, soc_score)),
            trade_plan=trade_plan,
            thesis=thesis,
            status="PENDING",
        )

    def _build_prompt(
        self,
        ticker: str,
        technicals: dict[str, Any],
        intelligence: dict[str, Any],
        backtest_win_prob: float,
    ) -> str:
        top_headlines = [
            f"- [{i.get('platform')}] ({float(i.get('sentiment_score') or 0.0):+.2f}) {i.get('title')}"
            for i in intelligence.get("items", [])[:6]
        ]
        return (
            f"Analyze {ticker} for a quantitative paper-trading desk.\n"
            f"Technicals: {json.dumps(technicals)}\n"
            f"Backtest Win Probability: {backtest_win_prob:.2%}\n"
            f"Catalyst Score: {intelligence.get('catalyst_score', 0.0)}, "
            f"Social Score: {intelligence.get('social_score', 0.0)}\n"
            f"Recent Sources:\n" + ("\n".join(top_headlines) or "None") + "\n\n"
            "Return strict JSON with keys: verdict (STRONG BUY|BUY|HOLD|SELL|STRONG SELL), "
            "confidence (0-100), thesis (exactly 3 concise bullet strings citing sources & technicals)."
        )

    async def _call_llm(self, prompt: str) -> _LLMOutput | None:
        provider = self.settings.llm_provider
        if provider == "openai" and self.settings.openai_api_key:
            try:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(
                    api_key=self.settings.openai_api_key,
                    base_url=self.settings.openai_base_url or None,
                )
                resp = await client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=[
                        {"role": "system", "content": "You are an institutional quantitative analyst. Output strict JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                )
                raw = resp.choices[0].message.content or "{}"
                return _LLMOutput.model_validate_json(raw)
            except Exception as exc:
                logger.warning("OpenAI synthesis failed, using quant fallback: %s", exc)
        elif provider == "gemini" and self.settings.gemini_api_key:
            try:
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=self.settings.gemini_api_key)
                resp = client.models.generate_content(
                    model=self.settings.gemini_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=_LLMOutput,
                        temperature=0.2,
                    ),
                )
                return _LLMOutput.model_validate_json(resp.text)
            except Exception as exc:
                logger.warning("Gemini synthesis failed, using quant fallback: %s", exc)
        return None
