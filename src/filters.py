import logging
from collections import defaultdict
from src.models import Trade, WalletProfile
from src.config import Settings


def apply_trade_filters(trades: list, cfg: Settings) -> list:
    """
    Applique les filtres anti-bruit sur la liste brute de trades.
    Ordre : 1) < 1 USDC, 2) < MIN_BET_USDC, 3) aller-retour YES+NO même heure
    Retourne la liste filtrée.
    """
    if not trades:
        return []

    # Filtre 1 : wash trading micro-montants (< 1 USDC)
    filtered = []
    for trade in trades:
        if trade.size < 1.0:
            logging.info(f"[FILTER] trade {trade.transaction_hash[:8]}... exclu: montant < 1 USDC ({trade.size})")
            continue
        filtered.append(trade)

    # Filtre 2 : mise < MIN_BET_USDC
    filtered2 = []
    for trade in filtered:
        if trade.size < cfg.min_bet_usdc:
            logging.info(f"[FILTER] trade {trade.transaction_hash[:8]}... exclu: montant < {cfg.min_bet_usdc} USDC ({trade.size})")
            continue
        filtered2.append(trade)

    # Filtre 3 : aller-retour YES+NO même wallet, même marché, même heure
    wash_wallets = _find_wash_trade_wallets(filtered2)
    filtered3 = []
    for trade in filtered2:
        key = (trade.proxy_wallet, trade.condition_id)
        if key in wash_wallets:
            logging.info(f"[FILTER] trade {trade.transaction_hash[:8]}... exclu: wash trade aller-retour (wallet {trade.proxy_wallet[:8]}...)")
            continue
        filtered3.append(trade)

    return filtered3


def is_arb_bot(wallet: WalletProfile, cfg: Settings) -> bool:
    """
    Retourne True si le wallet est un bot d'arbitrage à exclure.
    Condition : wallet.active_market_count > cfg.bot_market_count_max
    """
    if wallet.active_market_count > cfg.bot_market_count_max:
        logging.info(f"[FILTER] wallet {wallet.address[:8]}... exclu: bot ({wallet.active_market_count} marchés actifs)")
        return True
    return False


def _find_wash_trade_wallets(trades: list) -> set:
    """
    Détecte les paires (wallet, condition_id) ayant tradé dans les deux sens
    (outcomes différents) sur le même marché dans la même heure.
    Retourne un set de tuples (proxy_wallet, condition_id) à exclure.
    """
    # Grouper par (wallet, condition_id, heure) -> set d'outcomes
    groups = defaultdict(set)
    for trade in trades:
        hour_bucket = trade.timestamp // 3600
        key = (trade.proxy_wallet, trade.condition_id, hour_bucket)
        groups[key].add(trade.outcome)

    # Si un groupe a >= 2 outcomes différents -> wash trade
    wash_pairs = set()
    for (wallet, condition_id, _), outcomes in groups.items():
        if len(outcomes) >= 2:
            wash_pairs.add((wallet, condition_id))

    return wash_pairs
