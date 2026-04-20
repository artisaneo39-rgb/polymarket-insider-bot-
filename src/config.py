from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Credentials Telegram (obligatoires)
    telegram_bot_token: str
    telegram_chat_id: str

    # Seuils d'alerte
    alert_score_threshold: int = Field(default=60, ge=0, le=100)
    min_bet_usdc: float = Field(default=500.0, gt=0)

    # Paramètres des signaux
    wallet_age_days_max: int = Field(default=7, gt=0)
    wallet_tx_min: int = Field(default=10, gt=0)
    order_book_impact_pct: float = Field(default=2.0, gt=0)
    bet_vs_history_multiplier: float = Field(default=3.0, gt=0)
    concentration_pct: float = Field(default=60.0, gt=0, le=100)
    timing_hours_before: int = Field(default=4, gt=0)

    # Filtres anti-bruit
    bot_market_count_max: int = Field(default=50, gt=0)

    # Paramètres de run
    lookback_minutes: int = Field(default=30, gt=0)
    # polygon-rpc.com nécessite désormais une clé Ankr — fallback sur 1rpc.io/matic (public, sans clé)
    polygon_rpc_url: str = Field(default="https://1rpc.io/matic")


def get_settings() -> Settings:
    return Settings()
