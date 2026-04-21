import logging
from collections import defaultdict
from datetime import datetime, timezone
from src.models import Trade, WalletProfile
from src.config import Settings


SPORTS_KEYWORDS = [
    "football", "soccer", "nba", "nfl", "nhl", "mlb", "tennis", "cricket",
    "ufc", "boxing", "mma", "f1", "formula 1", "golf", "hockey", "rugby",
    "baseball", "basketball", "olympics", "premier league", "champions league",
    "world cup", "superbowl", "super bowl", "wimbledon", "tour de france",
]

# Mots-clés qui signalent un marché de pure spéculation (jamais d'insider)
NOISE_KEYWORDS = [
    # Entertainment / pop culture
    "gta", "grand theft auto", "game", "video game", "movie", "film", "album",
    "song", "music", "artist", "actor", "actress", "celebrity", "oscar", "grammy",
    "emmy", "netflix", "disney", "marvel", "dc comics", "anime", "series", "season",
    "episode", "trailer", "release date", "launch date",
    # Geopolitique mélangée avec pop culture (combinaisons absurdes)
    "before gta", "before iphone", "before ps6", "before xbox",
    # Memes / réseaux sociaux
    "elon", "tweet", "meme", "viral", "tiktok", "instagram", "followers",
    "subscribers", "views",
    # Divers non-financier
    "weather", "earthquake", "hurricane", "disaster", "alien", "ufo",
]

# Catégories de marchés où l'insider trading est plausible
VALID_MARKET_KEYWORDS = [
    # Crypto / finance
    "bitcoin", "btc", "ethereum", "eth", "crypto", "price", "ath", "etf",
    "fed", "interest rate", "inflation", "gdp", "recession", "s&p", "nasdaq",
    "stock", "ipo", "earnings", "merger", "acquisition",
    # Politique électorale (info asymétrique possible)
    "election", "president", "senate", "congress", "vote", "poll", "primary",
    "win", "candidate", "approval",
    # Régulation / juridique
    "sec", "regulation", "lawsuit", "court", "ruling", "ban", "approve",
    "legislation", "bill", "act",
    # Géopolitique (insiders possibles : diplomates, journalistes, think tanks)
    "russia", "ukraine", "israel", "gaza", "taiwan", "north korea", "china",
    "ceasefire", "peace deal", "war", "sanctions", "nato", "g7", "g20",
]


def is_noise_market(market, cfg=None) -> bool:
    """
    Retourne True si le marché doit être exclu du scoring.
    Critères :
    1. Contient un mot-clé sport
    2. Contient un mot-clé "noise" (entertainment, géopolitique floue, memes)
    3. N'appartient à AUCUNE catégorie valide (whitelist)
    4. Résolution > 180 jours dans le futur
    """
    question_lower = (market.question or "").lower()

    # Filtre 1 : mots-clés sportifs
    for keyword in SPORTS_KEYWORDS:
        if keyword in question_lower:
            logging.info(f"[FILTER] marche exclu: sport ({keyword}) — '{market.question[:50]}'")
            return True

    # Filtre 2 : mots-clés noise explicites
    for keyword in NOISE_KEYWORDS:
        if keyword in question_lower:
            logging.info(f"[FILTER] marche exclu: noise ({keyword}) — '{market.question[:50]}'")
            return True

    # Filtre 3 : whitelist — si aucun mot-clé valide, exclure
    has_valid_keyword = any(keyword in question_lower for keyword in VALID_MARKET_KEYWORDS)
    if not has_valid_keyword:
        logging.info(f"[FILTER] marche exclu: hors whitelist — '{market.question[:50]}'")
        return True

    # Filtre 4 : résolution trop lointaine
    if market.end_date_iso:
        try:
            end_dt = datetime.fromisoformat(market.end_date_iso.replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            days_until = (end_dt - datetime.now(timezone.utc)).days
            if days_until > 180:
                logging.info(f"[FILTER] marche exclu: resolution trop lointaine ({days_until}j) — '{market.question[:50]}'")
                return True
        except (ValueError, TypeError):
            pass

    return False


def is_blacklisted(wallet_address: str, cfg) -> bool:
    """
    Retourne True si le wallet est dans la blacklist configurée.
    La blacklist est une string d'adresses séparées par des virgules dans WALLET_BLACKLIST.
    """
    if not cfg.wallet_blacklist:
        return False
    blacklist = [addr.strip().lower() for addr in cfg.wallet_blacklist.split(",") if addr.strip()]
    if wallet_address.lower() in blacklist:
        logging.info(f"[FILTER] wallet {wallet_address[:8]}... exclu: blacklist")
        return True
    return False


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
