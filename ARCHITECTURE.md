# ARCHITECTURE — Polymarket Insider Bot

**Version** : 1.0
**Date** : 2026-04-14
**Statut** : Document de référence pour implémentation
**Basé sur** : PRD v1.0 approuvé

---

## 1. Décisions d'architecture

### D1 — requests synchrone vs httpx async

**Décision : requests synchrone + session réutilisée, pas de threading en V1.**

Justification :
- Le PRD impose "pas d'asyncio en V1" et "code < 500 lignes hors tests"
- Volume réel : 8-15 wallets max par run, chacun nécessite 2-3 appels API
- Estimation du pire cas : 15 wallets * 3 appels * 1s/appel = 45s, bien sous la limite de 5 minutes
- `requests.Session` réutilise les connexions TCP (keep-alive) : réduit la latence de 30-40%
- Le vrai goulot d'étranglement est le rate limit Gamma API (60 req/min), pas la concurrence
- Introduire `concurrent.futures.ThreadPoolExecutor` pour 8-15 wallets ajoute de la complexité pour un gain de ~20-30s — non justifié en V1
- Si la mesure réelle dépasse 4 minutes sur un run, introduire un pool de 4 threads dans fetcher.py pour `fetch_wallet_history()` uniquement (décision isolée, 5 lignes de changement)

**CONFLIT PRD résolu :** la question posée dans le PRD sur le risque de dépassement des 5 minutes est mitigée par (a) le cache intra-run Gamma API (D4) et (b) le fait que les appels Polygon peuvent retourner le statut "inconnu" en cas de timeout sans bloquer le run.

### D2 — python-telegram-bot 20.x vs appel HTTP direct

**Décision : utiliser `requests` directement sur l'API Telegram Bot, sans python-telegram-bot.**

Justification :
- python-telegram-bot 20.x est entièrement asyncio depuis sa réécriture. L'utiliser dans un script synchrone nécessite `asyncio.run()`, ce qui introduit un event loop pour envoyer 1 à 3 messages max par run. Overhead non justifié.
- L'API Telegram Bot est triviale : `POST https://api.telegram.org/bot{TOKEN}/sendMessage` avec `chat_id` et `text` comme paramètres JSON. Aucune librairie dédiée n'est nécessaire.
- Avantage : zéro dépendance supplémentaire, zero abstraction, comportement prévisible, code < 10 lignes dans `alerter.py`.
- Alternative rejetée : `telebot` (pyTelegramBotAPI) — synchrone mais ajoute une dépendance inutile pour un simple POST.

**Impact sur requirements.txt :** `python-telegram-bot` est retiré. Seul `requests` est utilisé pour tous les appels HTTP (Polymarket Data API, Gamma API, Polygon RPC, Telegram).

### D3 — Âge du wallet Polygon via JSON-RPC sans web3.py

**Décision : `eth_getTransactionCount` sur "latest" pour compter les transactions, puis heuristique sur la première transaction pour la date.**

Problème : `eth_getTransactionCount` retourne le nombre de transactions actuelles du wallet, pas la date de création. Pour l'âge du wallet (signal S1), il faut la date du premier trade.

**Stratégie retenue :**

Étape 1 — Utiliser le champ `timestamp` du trade lui-même comme proxy de l'âge du wallet.

La Data API retourne l'historique complet des trades d'un wallet via `GET /activity?user={address}&limit=500`. Le premier trade (le plus ancien dans la liste paginée) donne une estimation de l'âge du wallet. C'est un proxy valide : un wallet insider qui apparaît dans les trades Polymarket a nécessairement son premier trade Polymarket comme signal d'activité. Si le wallet n'a aucun historique Polymarket, son âge est inconnu et S1 n'est pas activé (fallback safe).

Étape 2 — `eth_getTransactionCount` pour S2 (nombre de transactions lifetime).

```
POST https://polygon-rpc.com
{
  "jsonrpc": "2.0",
  "method": "eth_getTransactionCount",
  "params": ["0x{wallet_address}", "latest"],
  "id": 1
}
```
Retourne un entier hexadécimal à convertir avec `int(result, 16)`.

Avantages :
- Pas de `web3.py` (évite 50+ sous-dépendances)
- 1 seul appel POST pour S2
- Pour S1 : zéro appel supplémentaire (timestamp du premier trade Polymarket suffit)
- Timeout 5s, retry 3x, fallback : signaux S1/S2 non activés si RPC down

**Alternative rejetée :** binary search sur `eth_getBlockByNumber` pour trouver le premier bloc du wallet. Trop complexe (10-20 requêtes par wallet), non justifié pour un proxy d'âge.

### D4 — Cache intra-run pour la Gamma API

**Décision : dictionnaire en mémoire (`dict`) passé par référence dans la session du run.**

```python
# Dans main.py, initialisé une fois
gamma_cache: dict[str, dict] = {}

# Dans fetcher.py
def fetch_market_metadata(condition_id: str, session: requests.Session, cache: dict) -> dict:
    if condition_id in cache:
        return cache[condition_id]
    data = _get_gamma(f"/markets/{condition_id}", session)
    cache[condition_id] = data
    return data
```

Justification :
- Plusieurs wallets peuvent trader sur le même marché dans la même fenêtre de 30 minutes
- Le cache évite les appels Gamma API en double — potentiellement 30-40% des appels économisés
- Implémentation : 3 lignes, zéro dépendance (pas de `functools.lru_cache` pour garder le contrôle explicite)
- Pas de TTL nécessaire : le cache est détruit à la fin du run (stateless par nature)
- Limite de protection : si le nombre de marchés uniques dépasse 50 dans un run, appliquer un garde-fou `if len(cache) < 50` avant d'écrire dans le cache (cas extrême, non critique)

### D5 — Gestion des erreurs et terminaison propre

**Décision : exception hierarchy avec terminaison gracieuse — exit(0) toujours sauf bug inattendu.**

Stratégie :

```
FetchError (API down, 429, timeout)  →  log WARNING + return []  →  run se termine, exit(0)
ScoreError (données corrompues)       →  log WARNING + skip wallet  →  run continue
AlertError (Telegram down)            →  log ERROR + run se termine, exit(0)
Exception inattendue                  →  log CRITICAL + exit(1)  →  GitHub Actions marque le run "failed"
```

Règles :
1. Toutes les fonctions de `fetcher.py` retournent `[]` ou `None` en cas d'erreur (pas d'exception remontée)
2. Un `FetchError` critique (Data API down) est loggé et le run se termine normalement (exit 0) : le scheduling continue
3. `exit(1)` uniquement pour les bugs non anticipés (TypeError, AttributeError, etc.) afin de rendre le problème visible dans GitHub Actions
4. Retry avec backoff exponentiel : 3 tentatives, délai initial 2s, délai max 16s

```python
def _get_with_retry(url: str, session: requests.Session, params: dict = None) -> dict | None:
    delays = [2, 4, 8]
    for i, delay in enumerate(delays):
        try:
            r = session.get(url, params=params, timeout=10)
            if r.status_code == 429:
                time.sleep(delay)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if i == len(delays) - 1:
                logging.warning(f"[FETCH ERROR] {url} — {e}")
                return None
            time.sleep(delay)
    return None
```

---

## 2. Contrats d'interface

### Types de données (dataclasses)

```python
# src/models.py  (fichier additionnel, ~30 lignes, remplace les TypedDict)

from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Trade:
    """Un trade brut retourné par la Data API Polymarket."""
    proxy_wallet: str          # adresse du wallet (proxyWallet dans l'API)
    condition_id: str          # identifiant du marché
    side: str                  # "BUY" ou "SELL"
    outcome: str               # "YES" ou "NO" (outcome label)
    size: float                # montant en USDC
    price: float               # prix entre 0 et 1
    timestamp: int             # Unix timestamp (secondes)
    transaction_hash: str      # hash de la transaction
    market_slug: str           # slug pour construire le lien polymarket.com
    market_question: str       # titre du marché (champ "title" dans l'API)

@dataclass
class WalletProfile:
    """Profil enrichi d'un wallet, construit après fetch_wallet_history()."""
    address: str
    tx_count_polygon: int              # nonce Polygon (S2) — -1 si inconnu
    first_polymarket_trade_ts: int     # timestamp du plus vieux trade Polymarket (S1 proxy)
    total_trades_count: int            # nombre de trades Polymarket lifetime
    avg_bet_usdc: float                # mise moyenne sur les trades historiques
    active_market_count: int           # nb de marchés distincts dans l'historique récent (filtre bot)
    market_exposures: dict[str, float] # {condition_id: montant_total_usdc} pour S5

@dataclass
class MarketMetadata:
    """Métadonnées d'un marché retournées par la Gamma API."""
    condition_id: str
    question: str
    slug: str
    end_date_iso: Optional[str]        # date de résolution ISO8601 — None si inconnue
    liquidity_usdc: float              # liquidité totale pour S3

@dataclass
class SignalResult:
    """Résultat du scoring pour un trade spécifique."""
    s1_fresh_wallet: bool      # wallet < WALLET_AGE_DAYS_MAX jours
    s2_low_history: bool       # < WALLET_TX_MIN transactions Polygon
    s3_order_book_impact: bool # impact > ORDER_BOOK_IMPACT_PCT %
    s4_bet_vs_history: bool    # mise > BET_VS_HISTORY_MULTIPLIER * avg_bet
    s5_concentration: bool     # > CONCENTRATION_PCT % sur un marché
    s6_timing: bool            # trade < TIMING_HOURS_BEFORE h avant résolution
    score: int                 # somme pondérée des signaux activés (0-100)
    details: dict[str, str]    # valeurs mesurées pour le log (ex: {"age_days": "3"})

@dataclass
class ScoredTrade:
    """Trade scoré, prêt pour l'alerter."""
    trade: Trade
    wallet: WalletProfile
    market: MarketMetadata
    signals: SignalResult
```

Note : `models.py` est un 7ème fichier dans `src/`. Il ne contient que des dataclasses pures, aucune logique. Il remplace les TypedDict pour bénéficier des valeurs par défaut et de la lisibilité. Coût : ~40 lignes.

---

## 3. Flux de données

```
GitHub Actions cron (*/30 * * * *)
           |
           v
     [main.py]
     1. Charger config (pydantic-settings)
     2. Créer requests.Session
     3. Créer gamma_cache = {}
           |
           v
     [fetcher.py] fetch_recent_trades(session, lookback_minutes)
     → GET data-api.polymarket.com/trades?limit=500
     → Filtrer côté client : timestamp >= now - lookback_minutes * 60
     → Retourne : list[Trade]
           |
           v
     [filters.py] apply_filters(trades, config)
     → Filtre 1 : size < MIN_BET_USDC → exclure
     → Filtre 2 : size < 1 USDC → wash trading
     → Filtre 3 : YES+NO même wallet même marché même heure → wash trading
     → Retourne : list[Trade] (filtrée)
           |
           v
     [fetcher.py] enrich_trades(filtered_trades, session, gamma_cache, config)
     → Pour chaque wallet unique :
         fetch_wallet_history(wallet, session) → WalletProfile
         apply_bot_filter(wallet_profile, config) → bool (exclure si bot arb)
     → Pour chaque marché unique (via gamma_cache) :
         fetch_market_metadata(condition_id, session, gamma_cache) → MarketMetadata
     → Retourne : list[tuple[Trade, WalletProfile, MarketMetadata]]
           |
           v
     [scorer.py] score_trade(trade, wallet, market, config) → ScoredTrade
     → Calculer S1...S6 indépendamment
     → Agréger en score 0-100
     → Retourne : list[ScoredTrade]
           |
           v
     [main.py] Filtrer : scored_trade.signals.score >= ALERT_SCORE_THRESHOLD
           |
           v
     [alerter.py] send_alert(scored_trade, config)
     → Formater le message texte
     → POST api.telegram.org/bot{TOKEN}/sendMessage
     → Log [ALERT] confirmé
```

---

## 4. Structure de fetcher.py

**Responsabilité :** tous les appels HTTP vers les APIs externes. Aucune logique métier.

```python
# src/fetcher.py

import time
import logging
import requests
from src.models import Trade, WalletProfile, MarketMetadata
from src.config import Settings

# --- Fonctions publiques ---

def fetch_recent_trades(session: requests.Session, lookback_minutes: int) -> list[Trade]:
    """
    Récupère les trades récents depuis la Data API.
    Stratégie : récupérer les 500 trades les plus récents (limite max sans pagination),
    puis filtrer côté client par timestamp >= now - lookback_minutes * 60.
    Retourne [] si l'API est down.
    """

def fetch_wallet_history(wallet_address: str, session: requests.Session) -> WalletProfile | None:
    """
    Récupère l'historique complet d'un wallet via Data API + Polygon RPC.
    GET data-api.polymarket.com/activity?user={address}&limit=500
    POST polygon-rpc.com (eth_getTransactionCount)
    Retourne None si les deux appels échouent.
    """

def fetch_market_metadata(condition_id: str, session: requests.Session, cache: dict) -> MarketMetadata | None:
    """
    Récupère les métadonnées d'un marché depuis la Gamma API.
    Cache en mémoire dans le dictionnaire `cache` passé par référence.
    GET gamma-api.polymarket.com/markets/{condition_id}
    Retourne None si l'API est down.
    """

# --- Fonctions privées ---

def _get_with_retry(url: str, session: requests.Session, params: dict = None, max_retries: int = 3) -> dict | list | None:
    """
    GET HTTP avec retry et backoff exponentiel sur 429/erreur réseau.
    Délais : [2s, 4s, 8s]. Timeout 10s par requête.
    Retourne None si toutes les tentatives échouent.
    """

def _post_rpc(url: str, payload: dict, session: requests.Session) -> dict | None:
    """
    POST JSON-RPC (pour Polygon RPC).
    Timeout 5s, retry 3x, délais [2s, 4s, 8s].
    Retourne None si timeout ou erreur réseau.
    """

def _parse_trade(raw: dict) -> Trade | None:
    """
    Convertit un dict brut de l'API en dataclass Trade.
    Retourne None si un champ obligatoire est manquant (évite les KeyError en aval).
    Champs mappés :
      raw["proxyWallet"]   → Trade.proxy_wallet
      raw["conditionId"]   → Trade.condition_id
      raw["side"]          → Trade.side  (BUY/SELL)
      raw["outcome"]       → Trade.outcome (YES/NO)
      raw["size"]          → Trade.size  (float, USDC)
      raw["price"]         → Trade.price (float, 0-1)
      raw["timestamp"]     → Trade.timestamp (int, secondes)
      raw["transactionHash"] → Trade.transaction_hash
      raw["slug"]          → Trade.market_slug
      raw["title"]         → Trade.market_question
    """

def _build_wallet_profile(address: str, history: list[dict], tx_count: int) -> WalletProfile:
    """
    Construit un WalletProfile à partir des données brutes de l'API.
    Calcule : avg_bet_usdc, active_market_count, market_exposures, first_polymarket_trade_ts.
    """
```

**Endpoints utilisés :**

| Endpoint | Méthode | Paramètres clés |
|----------|---------|-----------------|
| `https://data-api.polymarket.com/trades` | GET | `limit=500`, `takerOnly=true` |
| `https://data-api.polymarket.com/activity` | GET | `user={address}`, `limit=500` |
| `https://gamma-api.polymarket.com/markets/{condition_id}` | GET | — |
| `https://polygon-rpc.com` | POST | `eth_getTransactionCount`, `"latest"` |

**Note sur la pagination :** La Data API ne propose pas de filtre timestamp natif (confirmé). On récupère les 500 trades les plus récents (`limit=500`) et on filtre côté client par `timestamp >= now - lookback_minutes * 60`. Si un run a > 500 trades en 30 minutes, seuls les 500 premiers sont analysés — acceptable pour un usage initial.

---

## 5. Structure de scorer.py

**Responsabilité :** calculer le score composite. Ne fait aucun appel HTTP.

```python
# src/scorer.py

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
    """
    Point d'entrée principal. Calcule les 6 signaux et le score total.
    Retourne un ScoredTrade avec le détail des signaux et les valeurs mesurées.
    """

def _signal_s1(wallet: WalletProfile, cfg: Settings) -> tuple[bool, dict]:
    """
    S1 — Age du wallet.
    Condition : (now - wallet.first_polymarket_trade_ts) < cfg.wallet_age_days_max * 86400
    Si first_polymarket_trade_ts == -1 (inconnu) : retourne False (pas activé, fail safe)
    Retourne (activé: bool, details: {"age_days": str})
    """

def _signal_s2(wallet: WalletProfile, cfg: Settings) -> tuple[bool, dict]:
    """
    S2 — Faible historique Polygon.
    Condition : 0 <= wallet.tx_count_polygon < cfg.wallet_tx_min
    Si tx_count_polygon == -1 (inconnu) : retourne False
    Retourne (activé: bool, details: {"tx_count": str})
    """

def _signal_s3(trade: Trade, market: MarketMetadata, cfg: Settings) -> tuple[bool, dict]:
    """
    S3 — Impact order book.
    Condition : (trade.size / market.liquidity_usdc) * 100 > cfg.order_book_impact_pct
    Si market.liquidity_usdc == 0 : retourne False
    Retourne (activé: bool, details: {"impact_pct": str})
    """

def _signal_s4(trade: Trade, wallet: WalletProfile, cfg: Settings) -> tuple[bool, dict]:
    """
    S4 — Mise vs historique.
    Condition : trade.size > wallet.avg_bet_usdc * cfg.bet_vs_history_multiplier
    Si avg_bet_usdc == 0 (premier trade) : retourne True (nouveau wallet qui mise gros = signal)
    Retourne (activé: bool, details: {"multiplier": str, "avg_bet": str})
    """

def _signal_s5(trade: Trade, wallet: WalletProfile, cfg: Settings) -> tuple[bool, dict]:
    """
    S5 — Concentration marché.
    Condition : (wallet.market_exposures.get(trade.condition_id, 0) / sum(wallet.market_exposures.values())) * 100 > cfg.concentration_pct
    Si sum == 0 : retourne False
    Retourne (activé: bool, details: {"concentration_pct": str})
    """

def _signal_s6(trade: Trade, market: MarketMetadata, cfg: Settings) -> tuple[bool, dict]:
    """
    S6 — Timing pré-résolution.
    Condition : market.end_date_iso est renseigné ET
                0 < (end_datetime - trade_datetime) < cfg.timing_hours_before * 3600
    Si end_date_iso est None : retourne False
    Retourne (activé: bool, details: {"hours_before": str})
    """
```

---

## 6. Structure de filters.py

**Responsabilité :** exclure les trades et wallets anti-bruit avant le scoring. Ne fait aucun appel HTTP.

```python
# src/filters.py

from src.models import Trade, WalletProfile
from src.config import Settings

def apply_trade_filters(trades: list[Trade], cfg: Settings) -> list[Trade]:
    """
    Applique les filtres sur la liste brute de trades.
    Ordre d'application (du plus rapide au plus complexe) :
      1. Filtre montant < 1 USDC (wash trading micro-montants)
      2. Filtre mise < MIN_BET_USDC (signal trop faible)
      3. Filtre aller-retour YES+NO dans la même heure (wash trading)
    Logge chaque exclusion : [FILTER] trade {tx_hash[:8]}... exclu: {raison}
    Retourne la liste filtrée.
    """

def is_arb_bot(wallet: WalletProfile, cfg: Settings) -> bool:
    """
    Retourne True si le wallet est un bot d'arbitrage à exclure.
    Condition : wallet.active_market_count > cfg.bot_market_count_max
    Logge si True : [FILTER] wallet {address[:8]}... exclu: bot ({n} marchés actifs)
    """

def _is_wash_trade_roundtrip(wallet_address: str, trades: list[Trade]) -> bool:
    """
    Détecte si un wallet a tradé YES et NO sur le même marché dans la même heure.
    Utilise un dict {(wallet, condition_id, heure): set(sides)} pour regrouper.
    """
```

**Ordre d'application dans main.py :**
1. `apply_trade_filters(trades, cfg)` — sur la liste complète
2. `is_arb_bot(wallet_profile, cfg)` — après enrichissement, avant scoring

---

## 7. Structure de alerter.py

**Responsabilité :** formater et envoyer les alertes Telegram via l'API HTTP directe.

```python
# src/alerter.py

import logging
import requests
from src.models import ScoredTrade
from src.config import Settings

def send_alert(scored_trade: ScoredTrade, cfg: Settings, session: requests.Session) -> bool:
    """
    Formate et envoie un message Telegram pour un trade suspect.
    Utilise l'API Telegram Bot directement (POST sendMessage).
    Retourne True si envoi réussi, False sinon.
    Logge : [ALERT] message envoyé pour {wallet[:8]}... score {score}/100
    """

def format_message(scored_trade: ScoredTrade) -> str:
    """
    Construit le texte du message Telegram.
    Aucune valeur "None" ne doit apparaître dans le message final.
    Les champs absents affichent "inconnu" ou sont omis.
    Format défini dans PRD section 3.1 F5.
    """

def _post_telegram(token: str, chat_id: str, text: str, session: requests.Session) -> bool:
    """
    POST https://api.telegram.org/bot{token}/sendMessage
    Paramètres : chat_id, text, parse_mode="HTML" (pour <code> sur l'adresse wallet)
    Timeout 10s, 1 retry en cas d'erreur réseau.
    Retourne True si status_code == 200, False sinon.
    """
```

**Format du message Telegram :**

```
ALERTE INSIDER — Score {score}/100

Marche : "{market_question}"
Wallet : {wallet[:6]}...{wallet[-4:]} (age: {age_days}j, {tx_count} txs)
Mise : {size:.0f} USDC sur {outcome} @ {price:.0%}
Impact liquidite : {impact_pct:.1f}% de l'order book

Signaux detectes :
{ligne par signal actif avec son poids}

Cote actuelle : {price:.0%}
Lien : https://polymarket.com/event/{market_slug}
```

Exemple de lignes de signaux (seuls les signaux actifs sont affichés) :
```
[+20] Wallet frais (3 jours)
[+15] Peu de transactions (7 txs Polygon)
[+20] Impact order book : 4.2%
[+15] Mise 5.3x vs historique
```

**Note :** `parse_mode="HTML"` permet d'utiliser `<code>0x1234...abcd</code>` pour l'adresse wallet. Garder le texte simple, sans Markdown (évite les problèmes d'échappement).

---

## 8. Structure de config.py

```python
# src/config.py

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
    polygon_rpc_url: str = Field(default="https://polygon-rpc.com")

# Singleton — importé directement par les modules
def get_settings() -> Settings:
    return Settings()
```

**Variables d'environnement obligatoires** (doivent être dans GitHub Secrets) :
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

**Variables optionnelles** (GitHub Variables avec valeurs par défaut) :
- Toutes les autres — voir tableau PRD section 3.1 F6

---

## 9. Structure de main.py

```python
# src/main.py

import logging
import sys
import time
import requests
from src.config import get_settings
from src import fetcher, filters, scorer, alerter

def setup_logging() -> None:
    """Configure le logging vers stdout (capturé par GitHub Actions)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )

def main() -> None:
    run_start = time.time()
    setup_logging()

    logging.info(f"[RUN START] {datetime.utcnow().isoformat()} UTC")

    # 1. Config
    cfg = get_settings()
    logging.info(f"[CONFIG] seuil={cfg.alert_score_threshold}, fenêtre={cfg.lookback_minutes}min, min_bet={cfg.min_bet_usdc} USDC")

    # 2. Session HTTP réutilisée pour tous les appels
    session = requests.Session()
    session.headers.update({"User-Agent": "polymarket-insider-bot/1.0"})
    gamma_cache: dict = {}

    # 3. Fetch des trades récents
    trades = fetcher.fetch_recent_trades(session, cfg.lookback_minutes)
    logging.info(f"[FETCH] {len(trades)} trades dans les {cfg.lookback_minutes} dernières minutes")

    if not trades:
        logging.info("[RUN END] Aucun trade récent, durée: {:.1f}s".format(time.time() - run_start))
        return

    # 4. Filtres anti-bruit (pré-enrichissement)
    filtered_trades = filters.apply_trade_filters(trades, cfg)
    logging.info(f"[FILTER] {len(trades) - len(filtered_trades)} trades exclus, {len(filtered_trades)} retenus")

    if not filtered_trades:
        logging.info("[RUN END] Aucun trade après filtrage, durée: {:.1f}s".format(time.time() - run_start))
        return

    # 5. Enrichissement : wallet profiles + market metadata
    unique_wallets = list({t.proxy_wallet for t in filtered_trades})
    logging.info(f"[ANALYSIS] {len(unique_wallets)} wallets uniques à analyser")

    wallet_profiles: dict[str, WalletProfile | None] = {}
    for wallet_address in unique_wallets:
        profile = fetcher.fetch_wallet_history(wallet_address, session)
        if profile is None:
            logging.warning(f"[FETCH] wallet {wallet_address[:8]}... — historique indisponible, ignoré")
            continue
        if filters.is_arb_bot(profile, cfg):
            continue  # log géré dans is_arb_bot
        wallet_profiles[wallet_address] = profile

    # 6. Scoring
    scored_trades: list[ScoredTrade] = []
    for trade in filtered_trades:
        wallet = wallet_profiles.get(trade.proxy_wallet)
        if wallet is None:
            continue
        market = fetcher.fetch_market_metadata(trade.condition_id, session, gamma_cache)
        if market is None:
            logging.warning(f"[FETCH] marché {trade.condition_id[:8]}... — métadonnées indisponibles, ignoré")
            continue
        scored = scorer.score_trade(trade, wallet, market, cfg)
        logging.info(f"[SCORE] {trade.proxy_wallet[:8]}... → score {scored.signals.score}/100 [{_active_signals_str(scored)}]")
        scored_trades.append(scored)

    # 7. Alertes
    alerts_sent = 0
    for st in scored_trades:
        if st.signals.score >= cfg.alert_score_threshold:
            success = alerter.send_alert(st, cfg, session)
            if success:
                alerts_sent += 1

    logging.info(f"[ALERT] {alerts_sent} alerte(s) envoyée(s) (seuil: {cfg.alert_score_threshold})")
    logging.info("[RUN END] durée: {:.1f}s".format(time.time() - run_start))

def _active_signals_str(st: ScoredTrade) -> str:
    """Formate 'S1+S3+S6' pour le log [SCORE]."""
    active = []
    s = st.signals
    if s.s1_fresh_wallet: active.append("S1")
    if s.s2_low_history: active.append("S2")
    if s.s3_order_book_impact: active.append("S3")
    if s.s4_bet_vs_history: active.append("S4")
    if s.s5_concentration: active.append("S5")
    if s.s6_timing: active.append("S6")
    return "+".join(active) if active else "aucun"

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.critical(f"[CRITICAL] Erreur non anticipée: {e}", exc_info=True)
        sys.exit(1)
```

---

## 10. requirements.txt

```
requests==2.32.3
pydantic-settings==2.3.4
python-dotenv==1.0.1
pytest==8.2.2
pytest-mock==3.14.0
```

**Justification :**
- `requests` : tous les appels HTTP (Data API, Gamma API, Polygon RPC, Telegram)
- `pydantic-settings` : validation des variables d'environnement avec types et valeurs par défaut (inclut `pydantic` comme sous-dépendance)
- `python-dotenv` : chargement du fichier `.env` en local (pydantic-settings le supporte nativement, mais python-dotenv est explicite)
- `pytest` + `pytest-mock` : tests unitaires uniquement
- `python-telegram-bot` : **retiré** (voir D2 — API HTTP directe via requests)
- `web3` : **non inclus** (voir D3 — JSON-RPC via requests)
- `httpx`, `asyncio`, `aiohttp` : **non inclus** (voir D1 — sync uniquement)

---

## 11. .env.example

```bash
# Credentials Telegram (obligatoires — ne pas commiter avec de vraies valeurs)
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# Seuils d'alerte (optionnel — valeurs par défaut indiquées)
ALERT_SCORE_THRESHOLD=60
MIN_BET_USDC=500

# Paramètres des signaux (optionnel)
WALLET_AGE_DAYS_MAX=7
WALLET_TX_MIN=10
ORDER_BOOK_IMPACT_PCT=2
BET_VS_HISTORY_MULTIPLIER=3
CONCENTRATION_PCT=60
TIMING_HOURS_BEFORE=4

# Filtres anti-bruit (optionnel)
BOT_MARKET_COUNT_MAX=50

# Paramètres de run (optionnel)
LOOKBACK_MINUTES=30
POLYGON_RPC_URL=https://polygon-rpc.com
```

---

## 12. Tests unitaires (contrat, sans code)

### tests/test_scorer.py

Cas à couvrir :

| Cas | Description | Résultat attendu |
|-----|-------------|-----------------|
| TC-S-01 | Wallet avec tous les signaux activés | score == 100 |
| TC-S-02 | Wallet standard (âge > 30j, tx > 100, mise habituelle) | score <= 20 |
| TC-S-03 | S1 non activé si first_polymarket_trade_ts == -1 | s1 == False |
| TC-S-04 | S2 non activé si tx_count_polygon == -1 | s2 == False |
| TC-S-05 | S3 non activé si liquidity_usdc == 0 | s3 == False |
| TC-S-06 | S4 activé si avg_bet_usdc == 0 (premier trade) | s4 == True |
| TC-S-07 | S6 non activé si end_date_iso == None | s6 == False |
| TC-S-08 | S6 non activé si résolution dans > 4h | s6 == False |
| TC-S-09 | S6 activé si résolution dans 2h | s6 == True |
| TC-S-10 | Score est exactement la somme des poids des signaux actifs | score == sum des poids |
| TC-S-11 | Modifier ALERT_SCORE_THRESHOLD ne change pas le score (score est indépendant du seuil) | comportement isolé |

**Fixtures à préparer :**
- `make_trade(size=1000, price=0.7, outcome="YES", timestamp=now-60)` — factory function
- `make_wallet(tx_count=5, avg_bet=200, active_markets=3)` — factory function
- `make_market(liquidity=50000, end_date_iso="2026-04-14T20:00:00Z")` — factory function
- `default_config` — Settings avec valeurs par défaut

### tests/test_filters.py

Cas à couvrir :

| Cas | Description | Résultat attendu |
|-----|-------------|-----------------|
| TC-F-01 | Trade size < 1 USDC | exclu |
| TC-F-02 | Trade size < MIN_BET_USDC (499 USDC) | exclu |
| TC-F-03 | Trade size == MIN_BET_USDC (500 USDC) | retenu |
| TC-F-04 | Trade size > MIN_BET_USDC (1000 USDC) | retenu |
| TC-F-05 | Wallet avec YES + NO sur même marché dans même heure | exclu (wash trade) |
| TC-F-06 | Wallet avec YES + NO sur même marché dans heures différentes | retenu |
| TC-F-07 | Wallet avec YES + NO sur marchés différents dans même heure | retenu |
| TC-F-08 | Wallet avec active_market_count > BOT_MARKET_COUNT_MAX | is_arb_bot == True |
| TC-F-09 | Wallet avec active_market_count == BOT_MARKET_COUNT_MAX | is_arb_bot == False |
| TC-F-10 | Liste vide en entrée | liste vide en sortie |
| TC-F-11 | Tous les trades filtrés | liste vide en sortie |

---

## 13. GitHub Actions — schedule.yml complet

```yaml
# .github/workflows/schedule.yml

name: Polymarket Insider Bot

on:
  schedule:
    - cron: '*/30 * * * *'    # Toutes les 30 minutes
  workflow_dispatch:            # Déclenchement manuel pour tests

jobs:
  scan:
    name: Run insider scan
    runs-on: ubuntu-latest
    timeout-minutes: 10         # Hard stop à 10min (run normal attendu < 5min)

    env:
      # Credentials — GitHub Secrets (obligatoires)
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}

      # Seuils — GitHub Variables avec valeurs par défaut
      ALERT_SCORE_THRESHOLD: ${{ vars.ALERT_SCORE_THRESHOLD || '60' }}
      MIN_BET_USDC: ${{ vars.MIN_BET_USDC || '500' }}
      WALLET_AGE_DAYS_MAX: ${{ vars.WALLET_AGE_DAYS_MAX || '7' }}
      WALLET_TX_MIN: ${{ vars.WALLET_TX_MIN || '10' }}
      ORDER_BOOK_IMPACT_PCT: ${{ vars.ORDER_BOOK_IMPACT_PCT || '2' }}
      BET_VS_HISTORY_MULTIPLIER: ${{ vars.BET_VS_HISTORY_MULTIPLIER || '3' }}
      CONCENTRATION_PCT: ${{ vars.CONCENTRATION_PCT || '60' }}
      TIMING_HOURS_BEFORE: ${{ vars.TIMING_HOURS_BEFORE || '4' }}
      BOT_MARKET_COUNT_MAX: ${{ vars.BOT_MARKET_COUNT_MAX || '50' }}
      LOOKBACK_MINUTES: ${{ vars.LOOKBACK_MINUTES || '30' }}
      POLYGON_RPC_URL: ${{ vars.POLYGON_RPC_URL || 'https://polygon-rpc.com' }}

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'           # Cache pip entre runs pour réduire le temps de setup

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run insider scan
        run: python -m src.main
        # exit(0) = run "success" même si aucune alerte
        # exit(1) = run "failed" = bug inattendu, visible dans l'onglet Actions

      - name: Run tests (optionnel, déclenché uniquement manuellement)
        if: github.event_name == 'workflow_dispatch'
        run: pytest tests/ -v
```

**Notes importantes sur le workflow :**
- `cache: 'pip'` : réduit le temps d'installation de ~30s à ~5s après le premier run
- `timeout-minutes: 10` : le job est tué si le run dépasse 10 minutes (protection contre les boucles infinies)
- Les tests sont exécutés uniquement sur `workflow_dispatch` (déclenchement manuel) pour ne pas ralentir le cron
- Pour exécuter les tests en CI de façon permanente, créer un workflow séparé `ci.yml` sur `push`/`pull_request`

---

## 14. Structure finale des fichiers

```
polymarket-insider-bot/
├── .github/
│   └── workflows/
│       └── schedule.yml          # Cron + déclenchement manuel
├── src/
│   ├── __init__.py               # Vide
│   ├── main.py                   # Orchestration (~80 lignes)
│   ├── fetcher.py                # Appels HTTP Polymarket + Polygon (~120 lignes)
│   ├── scorer.py                 # 6 signaux + score composite (~80 lignes)
│   ├── filters.py                # Filtres anti-bruit (~60 lignes)
│   ├── alerter.py                # Formatage + envoi Telegram (~50 lignes)
│   ├── config.py                 # Pydantic-settings (~40 lignes)
│   └── models.py                 # Dataclasses Trade/WalletProfile/etc. (~40 lignes)
├── tests/
│   ├── __init__.py               # Vide
│   ├── test_scorer.py            # 11 cas de test (~80 lignes)
│   └── test_filters.py           # 11 cas de test (~60 lignes)
├── .env.example                  # Template commité sans valeurs réelles
├── .gitignore                    # Inclut .env, __pycache__, .pytest_cache
├── requirements.txt              # 5 dépendances
└── PRD.md                        # Déjà présent
```

**Estimation totale du code source (hors tests) :**
- main.py : ~80 lignes
- fetcher.py : ~120 lignes
- scorer.py : ~80 lignes
- filters.py : ~60 lignes
- alerter.py : ~50 lignes
- config.py : ~40 lignes
- models.py : ~40 lignes
- **Total : ~470 lignes** (sous la contrainte PRD de 500 lignes)

---

## 15. Décisions rejetées et alternatives

| Décision | Alternative rejetée | Raison du rejet |
|----------|--------------------|--------------------|
| requests sync | httpx async | Complexité asyncio non justifiée pour 8-15 wallets/run |
| requests sync | concurrent.futures ThreadPoolExecutor | Gain de 20-30s insuffisant pour le risque de race conditions sur gamma_cache |
| API Telegram directe via requests | python-telegram-bot 20.x | v20 entièrement asyncio, overhead non justifié pour 1-3 messages/run |
| API Telegram directe via requests | telebot (pyTelegramBotAPI) | Dépendance supplémentaire pour un simple POST HTTP |
| Proxy d'âge via premier trade Polymarket | eth_getBlockByNumber binary search | 10-20 requêtes Polygon par wallet vs 0 appel supplémentaire |
| dict Python pour cache Gamma | functools.lru_cache | lru_cache ne supporte pas les paramètres dict, contrôle explicite préférable |
| dataclasses | TypedDict | dataclasses offrent les valeurs par défaut et la sérialisation, plus lisibles |
| dataclasses | Pydantic BaseModel | Overkill pour des modèles internes sans validation HTTP |
| models.py séparé | Types inline dans chaque module | Évite les imports circulaires, centralise les contrats |
| exit(0) sur FetchError | exit(1) | Un run sans données = pas un bug, GitHub Actions ne doit pas alerter |

---

## 16. Questions ouvertes résolues (issues du PRD)

| Q# | Question PRD | Résolution architecture |
|----|-------------|------------------------|
| Q1 | Data API supporte-t-elle le filtrage par timestamp ? | Non confirmé. Stratégie : `limit=500` + filtre côté client. Acceptable pour usage initial. |
| Q2 | Âge wallet sans web3.py ? | Proxy via premier trade Polymarket (0 appel supplémentaire) + eth_getTransactionCount pour S2. |
| Q3 | Capital total du wallet exposable par l'API ? | Inféré depuis `market_exposures` calculé dans `_build_wallet_profile()` à partir des trades historiques. |
| Q4 | Message de démarrage Telegram ? | Hors scope V1, pas d'impact sur l'architecture. |

## 17. Risques résiduels à surveiller en Phase 1 (dev)

| Risque | Criticité | Action |
|--------|-----------|--------|
| La Data API ne retourne pas les trades globaux sans `user=` filtré | Haute | Tester `GET /trades?limit=500` sans paramètre user en Phase 1. Si bloquant : utiliser les trades filtrés par une liste de marchés actifs via Gamma API. |
| Le champ `outcome` (YES/NO) n'est pas dans la réponse `/trades` | Moyenne | Vérifier la présence du champ. Fallback : utiliser `side` (BUY/SELL) comme proxy. |
| `liquidity_usdc` absent de la réponse Gamma API | Moyenne | Si absent : S3 non calculé (retourne False). Ne bloque pas le run. |
| Polygon RPC public `polygon-rpc.com` en downtime | Faible | S1 et S2 non activés, run continue normalement. |
