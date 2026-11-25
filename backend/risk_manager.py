"""Hard Risk Manager & Circuit Breaker for Paper Trading."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.moomoo_client import strip_market_prefix
from config.settings import Settings, get_settings

SECTOR_MAP = {
    "AAPL": "Technology",
    "NVDA": "Semiconductors",
    "AMD": "Semiconductors",
    "MSFT": "Technology",
    "META": "Communication",
    "GOOGL": "Communication",
    "TSLA": "Consumer Discretionary",
    "AMZN": "Consumer Discretionary",
    "SPY": "Broad ETF",
    "QQQ": "Tech ETF",
}


@dataclass
class RiskCheckResult:
    """Result of pre-trade risk validation."""

    approved: bool
    reason: str
    max_allowed_qty: float


class RiskManager:
    """Enforces position sizing, sector exposure, mandatory stop-loss, and daily drawdown limits."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.execution_mode: str = self.settings.execution_mode

    def set_execution_mode(self, mode: str) -> str:
        mode_upper = mode.upper()
        if mode_upper not in ("SEMI_AUTO", "FULL_AUTO"):
            raise ValueError("Execution mode must be SEMI_AUTO or FULL_AUTO.")
        self.execution_mode = mode_upper
        return self.execution_mode

    def is_circuit_breaker_tripped(self, account_info: dict[str, Any]) -> bool:
        """Return True if daily drawdown exceeds -2% (or configured limit)."""
        day_pnl_pct = float(account_info.get("day_pnl_pct", 0.0)) / 100.0
        return day_pnl_pct <= -abs(self.settings.daily_drawdown_limit_pct)

    def validate_order(
        self,
        ticker: str,
        side: str,
        qty: float,
        price: float,
        stop_loss: float | None,
        account_info: dict[str, Any],
        positions: list[dict[str, Any]],
    ) -> RiskCheckResult:
        """Validate a proposed paper trade against all hard risk guardrails."""
        side_upper = side.upper()
        sym = strip_market_prefix(ticker)
        nav = float(account_info.get("nav", 0.0))

        if nav <= 0:
            return RiskCheckResult(False, "Account NAV is zero or unavailable.", 0.0)

        if side_upper == "SELL":
            held = next((p for p in positions if strip_market_prefix(p["ticker"]) == sym), None)
            held_qty = float(held["qty"]) if held else 0.0
            # Closing an existing long position is always allowed without a new stop-loss
            if held_qty > 0 and qty <= held_qty:
                return RiskCheckResult(True, "SELL order approved (closing long position).", held_qty)

            # Opening or adding to a SHORT position: enforce full entry guardrails
            if self.is_circuit_breaker_tripped(account_info):
                return RiskCheckResult(
                    False,
                    f"CIRCUIT BREAKER TRIPPED: Daily loss {account_info.get('day_pnl_pct', 0.0):.2f}% "
                    f"breached -{self.settings.daily_drawdown_limit_pct*100:.1f}% limit. New shorts halted.",
                    0.0,
                )

            if stop_loss is None or stop_loss <= price:
                return RiskCheckResult(
                    False,
                    "Mandatory valid stop-loss ABOVE entry price is required for SHORT SELL orders.",
                    0.0,
                )

            if price <= 0 or qty <= 0:
                return RiskCheckResult(False, "Order price and quantity must be positive.", 0.0)

            short_qty = qty - max(0.0, held_qty)
            existing_short_val = abs(min(0.0, held_qty)) * price
            max_pos_val = nav * self.settings.max_position_pct
            remaining_pos_val = max(0.0, max_pos_val - existing_short_val)

            sector = SECTOR_MAP.get(sym, "Other")
            sector_val = sum(
                abs(float(p.get("market_value", 0.0)))
                for p in positions
                if SECTOR_MAP.get(strip_market_prefix(p["ticker"]), "Other") == sector
            )
            remaining_sector_val = max(0.0, nav * self.settings.max_sector_pct - sector_val)
            allowed_notional = min(remaining_pos_val, remaining_sector_val)
            max_allowed_qty = float(int(allowed_notional // price))

            if short_qty * price > remaining_pos_val + 1e-6:
                return RiskCheckResult(
                    False,
                    f"Short notional ${short_qty * price:,.2f} exceeds max {self.settings.max_position_pct*100:.1f}% "
                    f"portfolio cap (remaining: ${remaining_pos_val:,.2f}).",
                    max_allowed_qty,
                )
            if short_qty * price > remaining_sector_val + 1e-6:
                return RiskCheckResult(
                    False,
                    f"Sector '{sector}' exposure would exceed max {self.settings.max_sector_pct*100:.1f}% cap.",
                    max_allowed_qty,
                )
            return RiskCheckResult(True, "SHORT SELL order passed all risk guardrails.", max_allowed_qty)

        # BUY guardrails
        held = next((p for p in positions if strip_market_prefix(p["ticker"]) == sym), None)
        held_qty = float(held["qty"]) if held else 0.0
        # Covering an existing short position is always allowed without a new stop-loss
        if held_qty < 0 and qty <= abs(held_qty):
            return RiskCheckResult(True, "BUY order approved (covering short position).", abs(held_qty))

        if self.is_circuit_breaker_tripped(account_info):
            return RiskCheckResult(
                False,
                f"CIRCUIT BREAKER TRIPPED: Daily loss {account_info.get('day_pnl_pct', 0.0):.2f}% "
                f"breached -{self.settings.daily_drawdown_limit_pct*100:.1f}% limit. New buys halted.",
                0.0,
            )

        if stop_loss is None or stop_loss <= 0 or stop_loss >= price:
            return RiskCheckResult(
                False,
                "Mandatory valid stop-loss below entry price is required for all BUY orders.",
                0.0,
            )

        if price <= 0 or qty <= 0:
            return RiskCheckResult(False, "Order price and quantity must be positive.", 0.0)

        # 1. Max 5% per position
        existing_pos_val = sum(
            float(p.get("market_value", 0.0))
            for p in positions
            if strip_market_prefix(p["ticker"]) == sym
        )
        max_pos_val = nav * self.settings.max_position_pct
        remaining_pos_val = max(0.0, max_pos_val - existing_pos_val)

        # 2. Max 20% per sector
        sector = SECTOR_MAP.get(sym, "Other")
        sector_val = sum(
            float(p.get("market_value", 0.0))
            for p in positions
            if SECTOR_MAP.get(strip_market_prefix(p["ticker"]), "Other") == sector
        )
        max_sector_val = nav * self.settings.max_sector_pct
        remaining_sector_val = max(0.0, max_sector_val - sector_val)

        # 3. Available buying power
        buying_power = float(account_info.get("buying_power", account_info.get("cash", 0.0)))
        allowed_notional = min(remaining_pos_val, remaining_sector_val, buying_power)
        max_allowed_qty = float(int(allowed_notional // price))

        order_notional = qty * price
        if order_notional > remaining_pos_val + 1e-6:
            return RiskCheckResult(
                False,
                f"Position size ${order_notional:,.2f} exceeds max {self.settings.max_position_pct*100:.1f}% "
                f"portfolio cap (remaining: ${remaining_pos_val:,.2f}).",
                max_allowed_qty,
            )

        if order_notional > remaining_sector_val + 1e-6:
            return RiskCheckResult(
                False,
                f"Sector '{sector}' exposure would exceed max {self.settings.max_sector_pct*100:.1f}% "
                f"cap (remaining: ${remaining_sector_val:,.2f}).",
                max_allowed_qty,
            )

        if order_notional > buying_power + 1e-6:
            return RiskCheckResult(
                False,
                f"Order notional ${order_notional:,.2f} exceeds buying power ${buying_power:,.2f}.",
                max_allowed_qty,
            )

        return RiskCheckResult(True, "BUY order passed all risk guardrails.", max_allowed_qty)
