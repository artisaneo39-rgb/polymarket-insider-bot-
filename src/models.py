from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Trade:
    """Un trade brut retourné par la Data API Polymarket."""
    proxy_wallet: str
    condition_id: str
    side: str                  # "BUY" ou "SELL"
    outcome: str               # "YES" ou "NO"
    size: float                # montant en USDC
    price: float               # prix entre 0 et 1
    timestamp: int             # Unix timestamp (secondes)
    transaction_hash: str
    market_slug: str
    market_question: str


@dataclass
class WalletProfile:
    """Profil enrichi d'un wallet."""
    address: str
    tx_count_polygon: int              # nonce Polygon (S2) — -1 si inconnu
    first_polymarket_trade_ts: int     # timestamp du plus vieux trade Polymarket — -1 si inconnu
    total_trades_count: int
    avg_bet_usdc: float
    active_market_count: int
    market_exposures: dict             # {condition_id: montant_total_usdc}


@dataclass
class MarketMetadata:
    """Métadonnées d'un marché retournées par la Gamma API."""
    condition_id: str
    question: str
    slug: str
    end_date_iso: Optional[str]        # None si inconnue
    liquidity_usdc: float


@dataclass
class SignalResult:
    """Résultat du scoring pour un trade spécifique."""
    s1_fresh_wallet: bool
    s2_low_history: bool
    s3_order_book_impact: bool
    s4_bet_vs_history: bool
    s5_concentration: bool
    s6_timing: bool
    score: int                         # 0-100
    details: dict                      # valeurs mesurées pour le log


@dataclass
class ScoredTrade:
    """Trade scoré, prêt pour l'alerter."""
    trade: Trade
    wallet: WalletProfile
    market: MarketMetadata
    signals: SignalResult
