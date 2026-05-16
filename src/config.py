import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    alpaca_mode: str
    alpaca_key_id: str
    alpaca_secret: str
    anthropic_api_key: str
    quiver_api_key: str
    managed_equity_ceiling_usd: float
    strategy_mean_reversion_enabled: bool
    strategy_political_enabled: bool
    strategy_crypto_enabled: bool
    data_dir: Path
    log_dir: Path

    @property
    def is_live(self) -> bool:
        return self.alpaca_mode == "live"

    @property
    def alpaca_base_url(self) -> str:
        return "https://api.alpaca.markets" if self.is_live else "https://paper-api.alpaca.markets"

    @property
    def paper_only_sentinel(self) -> Path:
        return self.data_dir / ".paper-only"


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes")


def load() -> Config:
    cfg = Config(
        alpaca_mode=os.getenv("ALPACA_MODE", "paper"),
        alpaca_key_id=os.getenv("ALPACA_API_KEY_ID", ""),
        alpaca_secret=os.getenv("ALPACA_API_SECRET_KEY", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        quiver_api_key=os.getenv("QUIVER_API_KEY", ""),
        managed_equity_ceiling_usd=float(os.getenv("MANAGED_EQUITY_CEILING_USD", "200")),
        strategy_mean_reversion_enabled=_bool("STRATEGY_MEAN_REVERSION_ENABLED", True),
        strategy_political_enabled=_bool("STRATEGY_POLITICAL_ENABLED", False),
        strategy_crypto_enabled=_bool("STRATEGY_CRYPTO_ENABLED", False),
        data_dir=Path(os.getenv("DATA_DIR", "/data")),
        log_dir=Path(os.getenv("LOG_DIR", "/logs")),
    )

    if cfg.alpaca_mode not in ("paper", "live"):
        sys.exit(f"ALPACA_MODE must be 'paper' or 'live', got: {cfg.alpaca_mode!r}")

    # Live mode requires the .paper-only sentinel to be deleted. Two-step friction:
    # editing .env alone is not enough — operator must also rm the sentinel on the host.
    if cfg.is_live and cfg.paper_only_sentinel.exists():
        sys.exit(
            f"Refusing to start: ALPACA_MODE=live but {cfg.paper_only_sentinel} still exists. "
            f"Delete the sentinel on the host first, then restart the container."
        )

    if not cfg.alpaca_key_id or not cfg.alpaca_secret:
        sys.exit("ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY must both be set")

    if cfg.strategy_political_enabled and not cfg.quiver_api_key:
        sys.exit("STRATEGY_POLITICAL_ENABLED=true but QUIVER_API_KEY is missing")

    return cfg
