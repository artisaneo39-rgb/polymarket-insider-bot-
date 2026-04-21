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
    alert_score_threshold: int = Field(default=75, ge=0, le=100)
    min_bet_usdc: float = Field(default=5000.0, gt=0)

    # Paramètres des signaux
    wallet_age_days_max: int = Field(default=7, gt=0)
    wallet_tx_min: int = Field(default=10, gt=0)
    order_book_impact_pct: float = Field(default=2.0, gt=0)
    bet_vs_history_multiplier: float = Field(default=3.0, gt=0)
    concentration_pct: float = Field(default=60.0, gt=0, le=100)
    timing_hours_before: int = Field(default=4, gt=0)

    # Filtres anti-bruit
    bot_market_count_max: int = Field(default=50, gt=0)
    wallet_blacklist: str = Field(default="", description="Adresses wallet à ignorer, séparées par des virgules")

    # Paramètres de run
    lookback_minutes: int = Field(default=30, gt=0)
    # polygon-rpc.com nécessite désormais une clé Ankr — fallback sur 1rpc.io/matic (public, sans clé)
    polygon_rpc_url: str = Field(default="https://1rpc.io/matic")

    # PnL Tracker (optionnel — désactivé si non configuré)
    github_gist_id: str = Field(default="", description="ID du Gist GitHub pour le PnL tracker")
    github_gist_token: str = Field(default="", description="GitHub Personal Access Token (scope: gist)")


def get_settings() -> Settings:
    return Settings()
