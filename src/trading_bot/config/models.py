from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from trading_bot.core.instruments import SYMBOL_NOT_EXECUTION_WHITELISTED, is_executable
from trading_bot.providers.capabilities import SUPPORTED_PROVIDER_IDS
from trading_bot.strategies.base import SUPPORTED_STRATEGY_IDS


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AppConfig(StrictModel):
    environment: str = "development"
    log_level: str = "INFO"
    timezone: Literal["UTC"] = "UTC"


class DatabaseConfig(StrictModel):
    url: str = "sqlite+aiosqlite:///data/trading_bot.db"
    parquet_root: Path = Path("data/parquet")


class TradingConfig(StrictModel):
    mode: Literal["backtest", "paper", "shadow", "live"] = "paper"
    asset_class: Literal["us_equity"] = "us_equity"
    # The one instrument Hyverion may buy or sell. Everything else is a sensor.
    primary_instrument: str = "QQQ"
    live_trading: bool = False
    shadow_trading: bool = True
    # Long-only, regular session, flat overnight and no options in this release.
    # Each needs its own strategy and validation before it can be turned on.
    shorting_enabled: Literal[False] = False
    allow_overnight: Literal[False] = False
    premarket_trading: Literal[False] = False
    after_hours_trading: Literal[False] = False
    options_trading: Literal[False] = False
    operating_region: str | None = "US"
    allowed_symbols: tuple[str, ...] = ("QQQ",)
    target_warning_percent: Decimal = Decimal("1")
    target_critical_percent: Decimal = Decimal("2")

    @field_validator("primary_instrument")
    @classmethod
    def validate_primary(cls, value: str) -> str:
        symbol = value.strip().upper()
        if not is_executable(symbol):
            raise ValueError(f"{SYMBOL_NOT_EXECUTION_WHITELISTED}: {symbol}")
        return symbol

    @field_validator("allowed_symbols")
    @classmethod
    def validate_allowed_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().upper() for item in value if item.strip())
        if not normalized:
            raise ValueError("at least one executable symbol is required")
        rejected = [item for item in normalized if not is_executable(item)]
        if rejected:
            raise ValueError(f"{SYMBOL_NOT_EXECUTION_WHITELISTED}: {rejected[0]}")
        return normalized


class BrokerConfig(StrictModel):
    """Which broker the ExecutionEngine talks to. Paper only in this release.

    The account equity comes from this broker; there is no configured capital.
    ``simulator`` is internal (tests, fixtures, research replays) and is never
    offered to the owner.
    """

    provider: Literal["simulator", "alpaca"] = "alpaca"
    environment: Literal["paper"] = "paper"


class MarketDataConfig(StrictModel):
    provider: Literal["alpaca", "fixture"] = "alpaca"
    # IEX is Alpaca's free real-time feed; it covers only part of consolidated volume.
    feed: Literal["iex", "sip"] = "iex"
    stale_after_seconds: int = Field(default=10, gt=0)
    max_reconnect_delay_seconds: int = Field(default=60, gt=0)


class LadderLevelConfig(StrictModel):
    """One profit-protection level. PnL bands are a percent of broker equity,
    so the ladder scales with the account instead of fixed dollar amounts."""

    level: int = Field(ge=0, le=4)
    minimum_pnl_percent: Decimal
    maximum_pnl_percent: Decimal
    risk_multiplier: Decimal = Field(ge=0, le=1)
    minimum_score: int = Field(ge=0, le=100)
    minimum_confirmations: int = Field(ge=0)
    protected_fraction: Decimal = Field(ge=0, le=1)


RiskProfileName = Literal["conservador", "medio", "alto", "personalizado"]


class RiskConfig(StrictModel):
    # Named profile the percentages below come from (config/risk_profiles.py).
    # "personalizado" means the owner edited a value by hand.
    profile: RiskProfileName = "medio"
    base_risk_percent: Decimal = Field(default=Decimal("0.5"), gt=0, le=5)
    # Optional dollar caps. ``None`` means "percent of broker equity only"
    # (PAPER). LIVE refuses to start unless every cap is set.
    max_base_risk_usd: Decimal | None = Field(default=None, gt=0)
    daily_loss_percent: Decimal = Field(default=Decimal("2"), gt=0, le=20)
    daily_loss_hard_cap_usd: Decimal | None = Field(default=None, gt=0)
    weekly_loss_percent: Decimal = Field(default=Decimal("5"), gt=0, le=30)
    weekly_loss_hard_cap_usd: Decimal | None = Field(default=None, gt=0)
    max_profit_giveback_percent: Decimal = Field(default=Decimal("35"), gt=0, le=100)
    max_positions: int = Field(default=3, ge=1, le=5)
    # 100 % = no leverage: the paper cash account, never margin.
    max_total_exposure_percent: Decimal = Field(default=Decimal("100"), gt=0, le=100)
    max_asset_exposure_percent: Decimal = Field(default=Decimal("100"), gt=0, le=100)
    max_correlated_risk_percent: Decimal = Field(default=Decimal("2"), gt=0, le=10)
    max_spread_bps: Decimal = Field(default=Decimal("20"), gt=0, le=100)
    max_slippage_bps: Decimal = Field(default=Decimal("15"), gt=0, le=100)
    min_liquidity_usd: Decimal = Field(default=Decimal("100000"), ge=0)
    max_losing_streak: int = Field(default=3, ge=1, le=10)
    cooldown_minutes_after_loss: int = Field(default=30, ge=0, le=1440)
    max_account_drawdown_percent: Decimal = Field(default=Decimal("10"), gt=0, le=50)
    # Deterministic time stop: no position may stay open longer than this,
    # regardless of AI availability or whether price reached stop/target.
    max_position_hold_minutes: int = Field(default=240, ge=1, le=10080)
    # Reject entries whose limit price strays from the observed market price.
    max_entry_deviation_bps: Decimal = Field(default=Decimal("50"), gt=0)
    # A risk decision authorizes one execution and expires quickly.
    max_decision_age_seconds: int = Field(default=60, ge=1, le=600)
    # Open positions are re-checked this often between entry cycles, so a stop,
    # target or time limit is not missed while the loop waits for the next cycle.
    position_check_seconds: int = Field(default=10, ge=2, le=300)
    # Optional: stop new entries once the day made this much. ``None`` = no cap.
    daily_profit_hard_cap_usd: Decimal | None = Field(default=None, gt=0)
    # New entries per New York session; exits never count.
    max_trades_per_day: int = Field(default=10, ge=1, le=20)
    # No entries in the first minutes after the open (price discovery).
    opening_no_trade_minutes: int = Field(default=5, ge=0, le=60)
    # With overnight disabled, positions are closed this long before the close.
    eod_flatten_minutes_before_close: int = Field(default=10, ge=1, le=120)
    # MacroRiskGate (§22, §115): window around HIGH events. In supervised mode
    # it blocks entries; in paper_autonomous mode it is recorded as context.
    macro_pre_block_minutes: int = Field(default=15, ge=0, le=240)
    macro_post_cooldown_minutes: int = Field(default=15, ge=0, le=240)
    # A calendar older than this is "unavailable" and blocks entries (always).
    macro_calendar_max_age_hours: int = Field(default=168, ge=1, le=720)
    ladder: tuple[LadderLevelConfig, ...]

    @field_validator("ladder")
    @classmethod
    def validate_ladder(cls, value: tuple[LadderLevelConfig, ...]) -> tuple[LadderLevelConfig, ...]:
        if [level.level for level in value] != list(range(5)):
            raise ValueError("risk ladder must contain ordered levels 0 through 4")
        for current, following in pairwise(value):
            if current.maximum_pnl_percent != following.minimum_pnl_percent:
                raise ValueError("risk ladder levels must be contiguous")
        return value

    @model_validator(mode="after")
    def ordered_loss_limits(self) -> RiskConfig:
        # One trade can never risk more than a day, nor a day more than a week.
        if self.base_risk_percent > self.daily_loss_percent:
            raise ValueError("risk per trade cannot exceed the daily loss limit")
        if self.daily_loss_percent > self.weekly_loss_percent:
            raise ValueError("daily loss limit cannot exceed weekly loss limit")
        return self

    def missing_dollar_caps(self) -> tuple[str, ...]:
        caps = {
            "max_base_risk_usd": self.max_base_risk_usd,
            "daily_loss_hard_cap_usd": self.daily_loss_hard_cap_usd,
            "weekly_loss_hard_cap_usd": self.weekly_loss_hard_cap_usd,
            "daily_profit_hard_cap_usd": self.daily_profit_hard_cap_usd,
        }
        return tuple(name for name, value in caps.items() if value is None)


class EnvelopeBound(StrictModel):
    minimum: Decimal
    maximum: Decimal

    @model_validator(mode="after")
    def ordered(self) -> EnvelopeBound:
        if self.minimum > self.maximum:
            raise ValueError("envelope minimum cannot exceed its maximum")
        return self


# Hard rails for anything the AI (or the owner from the app) may tune.
# Only a human editing config/autonomy.yaml can widen them.
DEFAULT_ENVELOPE: dict[str, tuple[str, str]] = {
    "base_risk_percent": ("0.05", "1.0"),
    "daily_loss_percent": ("0.25", "3"),
    "weekly_loss_percent": ("0.5", "10"),
    "max_account_drawdown_percent": ("2", "20"),
    "max_trades_per_day": ("1", "20"),
    "opening_no_trade_minutes": ("0", "30"),
    "eod_flatten_minutes_before_close": ("5", "60"),
    "macro_pre_block_minutes": ("0", "60"),
    "macro_post_cooldown_minutes": ("0", "60"),
    "max_profit_giveback_percent": ("20", "60"),
}


def _default_envelope() -> dict[str, EnvelopeBound]:
    return {
        name: EnvelopeBound(minimum=Decimal(low), maximum=Decimal(high))
        for name, (low, high) in DEFAULT_ENVELOPE.items()
    }


class AutonomyConfig(StrictModel):
    """How much the AI decides on its own. PAPER only; LIVE is always supervised.

    * ``paper_autonomous``: the AI sizes and times entries inside the envelope,
      the macro window is context (not a block), the critic vetoes only hard
      contradictions, and improvements that pass validation are promoted.
    * ``supervised``: the previous behaviour (macro gate blocks, any critic
      REVISE blocks, every change needs the owner).
    """

    mode: Literal["paper_autonomous", "supervised"] = "paper_autonomous"
    auto_promote_paper: bool = True
    shadow_sessions_before_promotion: int = Field(default=5, ge=1, le=60)
    envelope: dict[str, EnvelopeBound] = Field(default_factory=_default_envelope)

    @field_validator("envelope", mode="after")
    @classmethod
    def keep_default_bounds(cls, value: dict[str, EnvelopeBound]) -> dict[str, EnvelopeBound]:
        # A yaml envelope overrides bounds one by one; a key it omits keeps its
        # default bound instead of becoming unbounded.
        return {**_default_envelope(), **value}

    def bound(self, name: str) -> EnvelopeBound | None:
        return self.envelope.get(name)

    def violations(self, values: dict[str, Decimal]) -> tuple[str, ...]:
        out: list[str] = []
        for name, value in values.items():
            bound = self.envelope.get(name)
            if bound is not None and not bound.minimum <= value <= bound.maximum:
                out.append(f"{name}={value} outside [{bound.minimum}, {bound.maximum}]")
        return tuple(out)


AIRole = Literal["analysis", "decision", "improvement"]
AI_ROLES: tuple[AIRole, ...] = ("analysis", "decision", "improvement")


class AIModelsConfig(StrictModel):
    """Model per role for the primary subscription (``None`` = the CLI default).

    * ``analysis``: the specialists that read and summarise (most calls).
    * ``decision``: strategy and critic, which decide whether to trade.
    * ``improvement``: the nightly self-improvement review.

    Fallback subscriptions always use their own CLI default model, because a
    model name belongs to one vendor.
    """

    analysis: str | None = "claude-sonnet-5"
    decision: str | None = "claude-opus-5-5"
    improvement: str | None = "claude-opus-5-5"

    @field_validator("analysis", "decision", "improvement")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or normalized.lower() == "default":
            return None
        if not all(ch.isalnum() or ch in "-._:" for ch in normalized) or len(normalized) > 80:
            raise ValueError(f"invalid model name: {normalized!r}")
        return normalized

    def for_role(self, role: str) -> str | None:
        return getattr(self, role, None) if role in AI_ROLES else None


class ProviderLimitConfig(StrictModel):
    context_window: int | None = Field(default=None, ge=0)
    session_limit: Decimal | None = Field(default=None, ge=0)
    four_hour_limit: Decimal | None = Field(default=None, ge=0)
    weekly_limit: Decimal | None = Field(default=None, ge=0)
    source: Literal["CONFIGURED", "OFFICIAL", "UNKNOWN"] = "CONFIGURED"


class AIConfig(StrictModel):
    primary_provider: str = "disabled"
    primary_auth_mode: Literal["api", "subscription"] = "subscription"
    # Opus through a CLI can take a while on a long context.
    request_timeout_seconds: int = Field(default=180, ge=10, le=900)
    max_schema_retries: int = 1
    fallback_providers: tuple[str, ...] = ()
    models: AIModelsConfig = AIModelsConfig()
    provider_limits: dict[str, ProviderLimitConfig] = Field(default_factory=dict)

    @field_validator("primary_provider")
    @classmethod
    def normalize_primary_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized != "disabled" and normalized not in SUPPORTED_PROVIDER_IDS:
            raise ValueError(f"unsupported primary provider: {normalized}")
        return normalized

    @field_validator("fallback_providers")
    @classmethod
    def normalize_fallback_providers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value)
        if any(not item for item in normalized):
            raise ValueError("fallback providers cannot contain empty identifiers")
        if len(set(normalized)) != len(normalized):
            raise ValueError("fallback providers must be unique and ordered")
        unknown = set(normalized).difference(SUPPORTED_PROVIDER_IDS)
        if unknown:
            raise ValueError(f"unsupported fallback provider: {sorted(unknown)[0]}")
        return normalized

    @model_validator(mode="after")
    def validate_provider_chain(self) -> AIConfig:
        if self.primary_provider == "disabled" and self.fallback_providers:
            raise ValueError("fallback providers require one configured primary provider")
        if self.primary_provider in self.fallback_providers:
            raise ValueError("primary provider cannot also be a fallback provider")
        return self


class ExternalDataConfig(StrictModel):
    news_provider: str = "disabled"
    social_provider: str = "disabled"
    collection_interval_seconds: int = Field(default=300, ge=15, le=86400)
    collection_max_backoff_seconds: int = Field(default=3600, ge=60, le=604800)
    # URLs are operator-supplied only after a source/ToS/robots review.
    news_feeds: dict[str, str] = Field(default_factory=dict)
    news_reviewed_sources: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_collection_backoff(self) -> ExternalDataConfig:
        if self.collection_max_backoff_seconds < self.collection_interval_seconds:
            raise ValueError("collection backoff cannot be shorter than its interval")
        return self


class StrategiesConfig(StrictModel):
    enabled: tuple[str, ...]
    signal_weights_version: str
    signal_weights: dict[str, Decimal]

    @field_validator("signal_weights")
    @classmethod
    def weights_sum_to_one(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        if sum(value.values(), Decimal("0")) != Decimal("1"):
            raise ValueError("signal weights must sum exactly to 1")
        return value

    @field_validator("enabled")
    @classmethod
    def validate_enabled(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip().lower() for item in value if item.strip())
        if not normalized:
            raise ValueError("at least one deterministic strategy must be enabled")
        if len(normalized) != len(set(normalized)):
            raise ValueError("enabled strategies must be unique")
        unknown = set(normalized).difference(SUPPORTED_STRATEGY_IDS)
        if unknown:
            raise ValueError(f"strategy plugin is not available: {sorted(unknown)[0]}")
        return normalized


class MemoryConfig(StrictModel):
    """Working-memory backend. Redis credentials live in ``REDIS_URL`` (a secret)."""

    # Opt-in: when disabled agents receive no memory capsule at all.
    enabled: bool = False
    working_backend: Literal["in_process", "redis"] = "in_process"
    working_key_prefix: str = Field(default="hyverion", pattern=r"^[a-z0-9_-]{1,32}$")
    # Markdown export of strategic memory; the database stays authoritative.
    vault_path: Path = Path("data/knowledge")
    # Pinned local embedding model (scripts/fetch_embedding_model.py).
    embedding_model_dir: Path = Path("data/models/all-MiniLM-L6-v2")


CONFIG_VERSION: Literal[2] = 2


class PublicSettings(StrictModel):
    config_version: Literal[2] = CONFIG_VERSION
    app: AppConfig
    database: DatabaseConfig
    trading: TradingConfig
    broker: BrokerConfig = BrokerConfig()
    market_data: MarketDataConfig = MarketDataConfig()
    risk: RiskConfig
    ai: AIConfig
    external_data: ExternalDataConfig
    strategies: StrategiesConfig
    memory: MemoryConfig = MemoryConfig()
    autonomy: AutonomyConfig = AutonomyConfig()

    @model_validator(mode="after")
    def validate_rails(self) -> PublicSettings:
        if self.trading.live_trading or self.trading.mode == "live":
            missing = self.risk.missing_dollar_caps()
            if missing:
                raise ValueError(f"LIVE requires every USD risk cap: {', '.join(missing)}")
        violations = self.autonomy.violations(risk_envelope_values(self.risk))
        if violations:
            raise ValueError(f"risk outside the autonomy envelope: {violations[0]}")
        return self


def risk_envelope_values(risk: RiskConfig) -> dict[str, Decimal]:
    return {
        "base_risk_percent": risk.base_risk_percent,
        "daily_loss_percent": risk.daily_loss_percent,
        "weekly_loss_percent": risk.weekly_loss_percent,
        "max_account_drawdown_percent": risk.max_account_drawdown_percent,
        "max_trades_per_day": Decimal(risk.max_trades_per_day),
        "opening_no_trade_minutes": Decimal(risk.opening_no_trade_minutes),
        "eod_flatten_minutes_before_close": Decimal(risk.eod_flatten_minutes_before_close),
        "macro_pre_block_minutes": Decimal(risk.macro_pre_block_minutes),
        "macro_post_cooldown_minutes": Decimal(risk.macro_post_cooldown_minutes),
        "max_profit_giveback_percent": risk.max_profit_giveback_percent,
    }


class SecretSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # Alpaca PAPER keys only. No live credential name exists in this release.
    alpaca_paper_key_id: SecretStr | None = None
    alpaca_paper_secret_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    xai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    news_api_key: SecretStr | None = None
    x_api_key: SecretStr | None = None
    x_bearer_token: SecretStr | None = None
    reddit_client_id: SecretStr | None = None
    reddit_client_secret: SecretStr | None = None
    control_api_token: SecretStr | None = None
    redis_url: SecretStr | None = None


class Settings(StrictModel):
    public: PublicSettings
    secrets: SecretSettings
