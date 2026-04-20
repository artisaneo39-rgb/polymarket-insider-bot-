import time
import logging
import requests
from src.models import Trade, WalletProfile, MarketMetadata


def fetch_recent_trades(session: requests.Session, lookback_minutes: int) -> list:
    """
    Récupère les trades des lookback_minutes dernières minutes depuis la Data API.
    Stratégie : GET /trades?limit=500, puis filtre côté client par timestamp.
    Retourne [] si l'API est down ou si aucun trade récent.
    """
    url = "https://data-api.polymarket.com/trades"
    params = {"limit": 500}

    now = int(time.time())
    cutoff = now - (lookback_minutes * 60)

    raw_data = _get_with_retry(url, session, params)
    if raw_data is None:
        return []

    # raw_data peut être une liste directement ou un dict avec une clé "data" ou "trades"
    if isinstance(raw_data, list):
        items = raw_data
    elif isinstance(raw_data, dict):
        items = raw_data.get("data", raw_data.get("trades", []))
    else:
        return []

    trades = []
    for item in items:
        if not isinstance(item, dict):
            continue
        trade = _parse_trade(item)
        if trade is None:
            continue
        if trade.timestamp >= cutoff:
            trades.append(trade)

    return trades


def fetch_wallet_history(wallet_address: str, session: requests.Session,
                         polygon_rpc_url: str = "https://1rpc.io/matic") -> object:
    """
    Récupère l'historique d'un wallet via Data API + Polygon RPC.
    GET https://data-api.polymarket.com/activity?user={address}&limit=500
    POST polygon_rpc_url (eth_getTransactionCount)

    Note : /activity retourne la même structure que /trades (liste de dicts).
    Le champ usdcSize est le montant en USDC (size est en tokens).
    Retourne None si les deux appels échouent complètement.
    """
    url = "https://data-api.polymarket.com/activity"
    params = {"user": wallet_address, "limit": 500}

    raw = _get_with_retry(url, session, params)

    # Normaliser la réponse (liste ou dict avec erreur)
    if raw is None or isinstance(raw, dict) and raw.get("error"):
        history = []
    elif isinstance(raw, list):
        history = raw
    elif isinstance(raw, dict):
        history = raw.get("data", raw.get("activity", []))
    else:
        history = []

    # Appel Polygon RPC pour tx_count (signal S2)
    tx_count = _get_polygon_tx_count(wallet_address, session, polygon_rpc_url)

    # Si aucun historique ET RPC échoue -> retourner None
    if not history and tx_count == -1:
        return None

    return _build_wallet_profile(wallet_address, history, tx_count)


def fetch_market_metadata(condition_id: str, session: requests.Session, cache: dict) -> object:
    """
    Récupère les métadonnées d'un marché depuis la Gamma API.
    Cache en mémoire dans le dictionnaire `cache` passé par référence.
    GET https://gamma-api.polymarket.com/markets?conditionIds={condition_id}

    Structure Gamma API confirmée :
    - Retourne une liste directe
    - Champs clés : question, slug, endDate, endDateIso, liquidity (str), liquidityNum (float)
    - liquidity_usdc : utiliser liquidityNum si présent, sinon liquidity (à parser)

    Retourne None si l'API est down ou si le marché n'existe pas.
    """
    if condition_id in cache:
        return cache[condition_id]

    url = "https://gamma-api.polymarket.com/markets"
    params = {"conditionIds": condition_id}

    raw = _get_with_retry(url, session, params)

    if raw is None:
        return None

    # La réponse est une liste directe (confirmé par validation API)
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("data", raw.get("markets", [raw]))
    else:
        return None

    if not items:
        return None

    item = items[0] if isinstance(items, list) else items

    metadata = _parse_market_metadata(condition_id, item)
    if metadata is not None:
        cache[condition_id] = metadata

    return metadata


def _get_polygon_tx_count(wallet_address: str, session: requests.Session,
                           rpc_url: str = "https://1rpc.io/matic") -> int:
    """
    Retourne le nonce Polygon du wallet (nb de transactions) via JSON-RPC.
    Retourne -1 si le RPC est down, timeout, ou retourne une erreur.
    """
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getTransactionCount",
        "params": [wallet_address, "latest"],
        "id": 1,
    }
    result = _post_rpc(rpc_url, payload, session)
    if result is None:
        return -1
    # Vérifier si le RPC a retourné une erreur JSON-RPC (ex: clé API manquante)
    if isinstance(result, dict) and result.get("error"):
        logging.warning(f"[RPC ERROR] {rpc_url} — {result['error'].get('message', 'unknown error')}")
        return -1
    try:
        hex_count = result.get("result", "0x0")
        return int(hex_count, 16)
    except (ValueError, TypeError):
        return -1


def _post_rpc(url: str, payload: dict, session: requests.Session) -> object:
    """
    POST JSON-RPC (pour Polygon RPC).
    Timeout 5s, retry 3x, délais [2s, 4s, 8s].
    Retourne None si timeout ou erreur réseau.
    """
    delays = [2, 4, 8]
    for i, delay in enumerate(delays):
        try:
            r = session.post(url, json=payload, timeout=5)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if i == len(delays) - 1:
                logging.warning(f"[RPC ERROR] {url} — {e}")
                return None
            time.sleep(delay)
    return None


def _build_wallet_profile(address: str, history: list, tx_count: int) -> WalletProfile:
    """
    Construit un WalletProfile à partir des données brutes de /activity.

    Structure confirmée /activity :
    - size : montant en TOKENS (pas USDC)
    - usdcSize : montant en USDC (à utiliser en priorité)
    - timestamp : Unix secondes
    - conditionId : identifiant du marché
    """
    if not history:
        return WalletProfile(
            address=address,
            tx_count_polygon=tx_count,
            first_polymarket_trade_ts=-1,
            total_trades_count=0,
            avg_bet_usdc=0.0,
            active_market_count=0,
            market_exposures={},
        )

    sizes = []
    timestamps = []
    market_exposures = {}

    for item in history:
        if not isinstance(item, dict):
            continue

        # usdcSize en priorité (montant USDC réel), fallback sur amount/collateralAmount
        # Note : 'size' dans /activity est en tokens, PAS en USDC
        size = 0.0
        for key in ("usdcSize", "amount", "collateralAmount"):
            val = item.get(key)
            if val is not None:
                try:
                    size = float(val)
                    break
                except (ValueError, TypeError):
                    pass
        # Fallback sur 'size' seulement si rien d'autre n'est disponible
        if size == 0.0:
            val = item.get("size")
            if val is not None:
                try:
                    size = float(val)
                except (ValueError, TypeError):
                    pass

        # Extraire le timestamp (déjà en secondes Unix d'après la validation API)
        ts = 0
        for key in ("timestamp", "createdAt", "blockTimestamp"):
            val = item.get(key)
            if val is not None:
                try:
                    ts = int(val)
                    # Normaliser si millisecondes
                    if ts > 1_000_000_000_000:
                        ts = ts // 1000
                    break
                except (ValueError, TypeError):
                    pass

        # Extraire le condition_id du marché
        cid = item.get("conditionId") or item.get("market") or item.get("asset", "")

        if size > 0:
            sizes.append(size)
        if ts > 0:
            timestamps.append(ts)
        if cid and size > 0:
            market_exposures[str(cid)] = market_exposures.get(str(cid), 0.0) + size

    first_ts = min(timestamps) if timestamps else -1
    avg_bet = sum(sizes) / len(sizes) if sizes else 0.0
    active_market_count = len(market_exposures)

    return WalletProfile(
        address=address,
        tx_count_polygon=tx_count,
        first_polymarket_trade_ts=first_ts,
        total_trades_count=len(history),
        avg_bet_usdc=avg_bet,
        active_market_count=active_market_count,
        market_exposures=market_exposures,
    )


def _parse_market_metadata(condition_id: str, raw: dict) -> object:
    """
    Parse la réponse Gamma API en MarketMetadata.

    Structure confirmée Gamma API :
    - question : titre du marché
    - slug : identifiant URL
    - endDate : ISO datetime (ex: "2026-07-31T12:00:00Z")
    - endDateIso : date seule (ex: "2026-07-31")
    - liquidity : string USDC (ex: "82116.6296")
    - liquidityNum : float USDC (même valeur, parsée)

    Retourne None si les champs essentiels manquent.
    """
    if not raw or not isinstance(raw, dict):
        return None

    question = (raw.get("question") or raw.get("title") or
                raw.get("description") or f"Market {condition_id[:8]}")
    slug = (raw.get("slug") or raw.get("marketSlug") or
            raw.get("conditionId", condition_id)[:16])
    # Préférer endDateIso (date seule) puis endDate (datetime complet)
    end_date = (raw.get("endDateIso") or raw.get("endDate") or
                raw.get("resolutionDate") or raw.get("expirationDate"))

    # liquidityNum est le float direct (préféré), liquidity est la string
    liquidity = 0.0
    for key in ("liquidityNum", "liquidityClob", "liquidity", "liquidityUsdc",
                "volume", "totalVolume"):
        val = raw.get(key)
        if val is not None:
            try:
                liquidity = float(val)
                break
            except (ValueError, TypeError):
                pass

    return MarketMetadata(
        condition_id=condition_id,
        question=str(question),
        slug=str(slug),
        end_date_iso=str(end_date) if end_date else None,
        liquidity_usdc=liquidity,
    )


def _get_with_retry(url: str, session: requests.Session, params: dict = None, max_retries: int = 3) -> object:
    """
    GET HTTP avec retry et backoff exponentiel sur 429/erreur réseau.
    Délais : [2s, 4s, 8s]. Timeout 10s par requête.
    Retourne None si toutes les tentatives échouent.
    """
    delays = [2, 4, 8]
    for i in range(max_retries):
        try:
            r = session.get(url, params=params, timeout=10)
            if r.status_code == 429:
                logging.warning(f"[FETCH] 429 rate limit sur {url}, attente {delays[i]}s")
                time.sleep(delays[i])
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if i == max_retries - 1:
                logging.warning(f"[FETCH ERROR] {url} — {e}")
                return None
            time.sleep(delays[i])
    return None


def _parse_trade(raw: dict) -> object:
    """
    Convertit un dict brut de l'API en dataclass Trade.
    Retourne None si un champ obligatoire est manquant.

    Mapping des champs API -> Trade :
      proxyWallet      -> proxy_wallet
      conditionId      -> condition_id
      side             -> side (BUY/SELL)
      outcome          -> outcome (YES/NO) — peut être absent, fallback sur side
      size             -> size (float, USDC)
      price            -> price (float, 0-1)
      timestamp        -> timestamp (int secondes — peut être en ms, à normaliser)
      transactionHash  -> transaction_hash
      slug / market.slug -> market_slug
      title / market.title / question -> market_question
    """
    try:
        proxy_wallet = raw.get("proxyWallet") or raw.get("maker") or raw.get("user")
        condition_id = raw.get("conditionId") or raw.get("market")

        if not proxy_wallet or not condition_id:
            return None

        side = raw.get("side", "BUY")
        outcome = raw.get("outcome") or raw.get("outcomeIndex") or side

        size = float(raw.get("size", raw.get("usdcSize", raw.get("amount", 0))))
        price = float(raw.get("price", 0.5))

        ts = raw.get("timestamp") or raw.get("createdAt") or raw.get("blockTimestamp", 0)
        # Normaliser : si timestamp en millisecondes (> 1e12), convertir en secondes
        try:
            ts_int = int(ts)
        except (TypeError, ValueError):
            ts_int = 0
        if ts_int > 1_000_000_000_000:
            ts_int = ts_int // 1000

        tx_hash = raw.get("transactionHash") or raw.get("txHash") or raw.get("id", "")

        # market_slug et market_question peuvent être dans un sous-objet "market"
        market_obj = raw.get("market", {}) if isinstance(raw.get("market"), dict) else {}
        market_slug = (
            raw.get("slug")
            or market_obj.get("slug")
            or market_obj.get("conditionId")
            or condition_id[:16]
        )
        market_question = (
            raw.get("title")
            or raw.get("question")
            or market_obj.get("question")
            or market_obj.get("title")
            or f"Market {condition_id[:8]}"
        )

        return Trade(
            proxy_wallet=str(proxy_wallet),
            condition_id=str(condition_id),
            side=str(side),
            outcome=str(outcome),
            size=size,
            price=price,
            timestamp=ts_int,
            transaction_hash=str(tx_hash),
            market_slug=str(market_slug),
            market_question=str(market_question),
        )
    except Exception as e:
        logging.warning(f"[PARSE ERROR] Impossible de parser le trade: {e} — {raw}")
        return None
