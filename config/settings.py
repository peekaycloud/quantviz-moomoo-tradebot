"""Centralized configuration for the Autonomous AI Quantitative Trading Bot."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Moomoo OpenD & CLI
    moomoo_opend_host: str = Field(default="127.0.0.1")
    moomoo_opend_port: int = Field(default=11111)
    moomoo_trd_env: Literal["SIMULATE"] = Field(default="SIMULATE")
    moomoo_trd_market: str = Field(default="US")
    moomoo_security_firm: str = Field(default="FUTUINC")
    moomoo_unlock_pwd: str = Field(default="")
    moomoo_cli_bin: str = Field(default="~/.moomoo/bin/moomoo")
    moomoo_sim_account_id: str = Field(default="5829916")

    # LLM / AI Advisor
    llm_provider: Literal["openai", "gemini", "local_quant"] = Field(default="openai")
    openai_api_key: str = Field(default="")
    openai_base_url: str = Field(default="")
    openai_model: str = Field(default="gpt-4o-mini")
    gemini_api_key: str = Field(default="")
    gemini_model: str = Field(default="gemini-2.5-flash")

    # Execution & Risk Guardrails
    execution_mode: Literal["SEMI_AUTO", "FULL_AUTO"] = Field(default="SEMI_AUTO")
    watchlist: str = Field(
        default=(
            "US.MU,US.NVDA,US.AMD,US.AVGO,US.TSM,US.ARM,US.MRVL,US.INTC,"
            "US.AAPL,US.MSFT,US.GOOGL,US.AMZN,US.META,US.TSLA,US.NFLX,"
            "US.PLTR,US.CRWD,US.ORCL,US.COIN,US.SPY,US.QQQ"
        )
    )
    max_position_pct: float = Field(default=0.05, ge=0.005, le=0.25)
    max_sector_pct: float = Field(default=0.20, ge=0.05, le=0.50)
    daily_drawdown_limit_pct: float = Field(default=0.02, ge=0.005, le=0.10)
    default_atr_stop_mult: float = Field(default=2.0, ge=0.5, le=5.0)
    default_rr_target: float = Field(default=2.5, ge=1.0, le=6.0)

    # Backtester Gatekeeper
    min_sharpe_ratio: float = Field(default=1.2)
    max_drawdown_pct: float = Field(default=0.15)
    commission_rate: float = Field(default=0.0005)
    slippage_bps: float = Field(default=5.0)

    # Storage & Server
    duckdb_path: str = Field(default="data/tradebot.duckdb")
    parquet_dir: str = Field(default="data/ohlcv")
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8000)
    poll_interval_seconds: int = Field(default=2, ge=1, le=60)
    research_interval_seconds: int = Field(default=300, ge=30, le=3600)
    sec_edgar_user_agent: str = Field(default="QuantTradeBot/1.0 (contact@example.com)")

    @field_validator("moomoo_trd_env")
    @classmethod
    def enforce_simulate_only(cls, value: str) -> str:
        """Hard-assert that only SIMULATE paper trading is ever allowed."""
        if value != "SIMULATE":
            raise ValueError("Live trading is disabled: MOOMOO_TRD_ENV must be 'SIMULATE'.")
        return value

    @property
    def watchlist_symbols(self) -> list[str]:
        """Return normalized watchlist symbols (e.g. ['US.AAPL', ...])."""
        return [s.strip().upper() for s in self.watchlist.split(",") if s.strip()]

    def ensure_dirs(self) -> None:
        """Create required local storage directories."""
        Path(self.duckdb_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.parquet_dir).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance."""
    settings = Settings()
    settings.ensure_dirs()
    return settings
