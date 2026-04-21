import pytest
from datetime import datetime, timezone, timedelta
from pydantic_settings import PydanticBaseSettingsSource
from src.models import Trade, WalletProfile, MarketMetadata
from src.config import Settings


class _TestSettings(Settings):
    """Variante de Settings qui ignore les variables d'environnement.

    En CI (GitHub Actions), les env vars comme MIN_BET_USDC=5000 ont une
    priorité plus haute que les kwargs du constructeur dans pydantic-settings.
    Cette sous-classe n'utilise que les valeurs passées en argument, ce qui
    rend les tests indépendants de l'environnement d'exécution.
    """

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ):
        return (init_settings,)  # init kwargs uniquement, pas d'env vars


@pytest.fixture
def cfg():
    return _TestSettings(
        telegram_bot_token="test_token",
        telegram_chat_id="123456",
        min_bet_usdc=500.0,
    )


@pytest.fixture
def make_trade():
    def _make(**kwargs):
        now = int(datetime.now(timezone.utc).timestamp())
        defaults = dict(
            proxy_wallet="0xabc123",
            condition_id="0xmarket1",
            side="BUY",
            outcome="Yes",
            size=1000.0,
            price=0.5,
            timestamp=now - 60,
            transaction_hash="0xtxhash1",
            market_slug="test-market",
            market_question="Will X happen?",
        )
        defaults.update(kwargs)
        return Trade(**defaults)
    return _make


@pytest.fixture
def make_wallet():
    def _make(**kwargs):
        now = int(datetime.now(timezone.utc).timestamp())
        defaults = dict(
            address="0xabc123",
            tx_count_polygon=50,
            first_polymarket_trade_ts=now - (30 * 86400),
            total_trades_count=20,
            avg_bet_usdc=300.0,
            active_market_count=5,
            market_exposures={"0xmarket1": 1000.0, "0xmarket2": 500.0},
        )
        defaults.update(kwargs)
        return WalletProfile(**defaults)
    return _make


@pytest.fixture
def make_market():
    def _make(**kwargs):
        future_6h = datetime.now(timezone.utc) + timedelta(hours=6)
        defaults = dict(
            condition_id="0xmarket1",
            question="Will X happen?",
            slug="will-x-happen",
            end_date_iso=future_6h.strftime("%Y-%m-%dT%H:%M:%SZ"),
            liquidity_usdc=50000.0,
        )
        defaults.update(kwargs)
        return MarketMetadata(**defaults)
    return _make
