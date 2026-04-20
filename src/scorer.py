# src/scorer.py
import logging
from datetime import datetime, timezone
from src.models import Trade, WalletProfile, MarketMetadata, SignalResult, ScoredTrade
from src.config import Settings

SIGNAL_WEIGHTS = {
    "s1_fresh_wallet": 20,
    "s2_low_history": 15,
    "s3_order_book_impact": 20,
    "s4_bet_vs_history": 15,
    "s5_concentration": 15,
    "s6_timing": 15,
}
# Total max = 100


def score_trade(trade: Trade, wallet: WalletProfile, market: MarketMetadata, cfg: Settings) -> ScoredTrade:
    """Point d'entrée : calcule les 6 signaux et le score total."""
    s1, d1 = _signal_s1(wallet, cfg)
    s2, d2 = _signal_s2(wallet, cfg)
    s3, d3 = _signal_s3(trade, market, cfg)
    s4, d4 = _signal_s4(trade, wallet, cfg)
    s5, d5 = _signal_s5(trade, wallet, cfg)
    s6, d6 = _signal_s6(trade, market, cfg)

    score = (
        (SIGNAL_WEIGHTS["s1_fresh_wallet"] if s1 else 0) +
        (SIGNAL_WEIGHTS["s2_low_history"] if s2 else 0) +
        (SIGNAL_WEIGHTS["s3_order_book_impact"] if s3 else 0) +
        (SIGNAL_WEIGHTS["s4_bet_vs_history"] if s4 else 0) +
        (SIGNAL_WEIGHTS["s5_concentration"] if s5 else 0) +
        (SIGNAL_WEIGHTS["s6_timing"] if s6 else 0)
    )

    details = {**d1, **d2, **d3, **d4, **d5, **d6}

    signals = SignalResult(
        s1_fresh_wallet=s1,
        s2_low_history=s2,
        s3_order_book_impact=s3,
        s4_bet_vs_history=s4,
        s5_concentration=s5,
        s6_timing=s6,
        score=score,
        details=details,
    )

    return ScoredTrade(trade=trade, wallet=wallet, market=market, signals=signals)


def _signal_s1(wallet: WalletProfile, cfg: Settings):
    """S1 — Wallet frais. Retourne (bool, details)."""
    if wallet.first_polymarket_trade_ts == -1:
        return False, {"s1_age_days": "inconnu"}
    now = int(datetime.now(timezone.utc).timestamp())
    age_days = (now - wallet.first_polymarket_trade_ts) / 86400
    active = age_days < cfg.wallet_age_days_max
    return active, {"s1_age_days": f"{age_days:.1f}"}


def _signal_s2(wallet: WalletProfile, cfg: Settings):
    """S2 — Faible historique Polygon. Retourne (bool, details)."""
    if wallet.tx_count_polygon == -1:
        return False, {"s2_tx_count": "inconnu"}
    active = 0 <= wallet.tx_count_polygon < cfg.wallet_tx_min
    return active, {"s2_tx_count": str(wallet.tx_count_polygon)}


def _signal_s3(trade: Trade, market: MarketMetadata, cfg: Settings):
    """S3 — Impact order book. Retourne (bool, details)."""
    if market.liquidity_usdc <= 0:
        return False, {"s3_impact_pct": "inconnu"}
    impact_pct = (trade.size / market.liquidity_usdc) * 100
    active = impact_pct > cfg.order_book_impact_pct
    return active, {"s3_impact_pct": f"{impact_pct:.2f}"}


def _signal_s4(trade: Trade, wallet: WalletProfile, cfg: Settings):
    """S4 — Mise vs historique. Retourne (bool, details)."""
    if wallet.avg_bet_usdc == 0:
        return True, {"s4_multiplier": "premier trade", "s4_avg_bet": "0"}
    multiplier = trade.size / wallet.avg_bet_usdc
    active = multiplier > cfg.bet_vs_history_multiplier
    return active, {"s4_multiplier": f"{multiplier:.1f}x", "s4_avg_bet": f"{wallet.avg_bet_usdc:.0f}"}


def _signal_s5(trade: Trade, wallet: WalletProfile, cfg: Settings):
    """S5 — Concentration marché. Retourne (bool, details)."""
    total = sum(wallet.market_exposures.values())
    if total <= 0:
        return False, {"s5_concentration_pct": "inconnu"}
    market_exposure = wallet.market_exposures.get(trade.condition_id, 0.0)
    concentration_pct = (market_exposure / total) * 100
    active = concentration_pct > cfg.concentration_pct
    return active, {"s5_concentration_pct": f"{concentration_pct:.1f}"}


def _signal_s6(trade: Trade, market: MarketMetadata, cfg: Settings):
    """S6 — Timing pré-résolution. Retourne (bool, details)."""
    if not market.end_date_iso:
        return False, {"s6_hours_before": "inconnu"}
    try:
        end_dt = datetime.fromisoformat(market.end_date_iso.replace("Z", "+00:00"))
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        trade_dt = datetime.fromtimestamp(trade.timestamp, tz=timezone.utc)
        hours_before = (end_dt - trade_dt).total_seconds() / 3600
        active = 0 < hours_before < cfg.timing_hours_before
        return active, {"s6_hours_before": f"{hours_before:.1f}"}
    except (ValueError, TypeError) as e:
        logging.warning(f"[SCORE] S6 erreur parsing end_date_iso '{market.end_date_iso}': {e}")
        return False, {"s6_hours_before": "erreur"}
