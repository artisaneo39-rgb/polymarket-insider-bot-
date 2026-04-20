# CLAUDE.md — Polymarket Insider Bot

**Statut actuel : MVP TERMINE — En attente de deploiement GitHub Actions**
**Derniere mise a jour :** 2026-04-15 (Story 4.2 DONE — GitHub Actions workflow cree, MVP complet)

---

## Avancement BMAD

| Phase BMAD | Statut | Fichier |
|-----------|--------|---------|
| Product Brief | DONE | `_bmad-output/product-brief.md` (reference dans PRD) |
| PRD | DONE | `PRD.md` |
| Architecture | DONE | `ARCHITECTURE.md` |
| Stories (TEA) | DONE | `STORIES.md` |
| Sprint 1 | DONE | voir section ci-dessous |
| Sprint 2 (GitHub Actions) | DONE | `.github/workflows/schedule.yml` |

---

## Sprint 1 — Fondations et premiere validation API

### Objectif du sprint

Creer la structure complete du projet Python, valider que l'API Polymarket Data est accessible et retourne le format attendu, et implementer les filtres anti-bruit en TDD. A la fin de ce sprint, le projet est installable, les fondations de donnees sont posees, l'acces API est confirme, et la logique de filtrage est testee et fonctionnelle.

### Stories selectionnees

| Story | Titre | Estimation | Statut |
|-------|-------|-----------|--------|
| **1.1** | Structure du projet et fondations (models.py, config.py, requirements.txt, .env.example, .gitignore) | S (< 2h) | DONE |
| **1.2** | fetch_recent_trades() avec validation API reelle | M (demi-journee) | DONE |
| **2.1** | filters.py — filtres anti-bruit en TDD (11 cas de test) | M (demi-journee) | DONE |
| **2.2** | fetch_wallet_history() + fetch_market_metadata() | M (demi-journee) | DONE |
| **3.1** | alerter.py — envoi Telegram | M (demi-journee) | DONE |
| **3.2** | main.py : orchestration complete | M (demi-journee) | DONE |
| **4.1** | Tests finaux (scorer.py TDD, 22 tests) | M (demi-journee) | DONE |
| **4.2** | GitHub Actions + deploiement | S (< 2h) | DONE |

### Criteres de succes du sprint

- [x] `pip install -r requirements.txt` s'execute sans erreur sur Python 3.11
- [x] `from src.models import Trade, WalletProfile, MarketMetadata, SignalResult, ScoredTrade` fonctionne
- [x] `fetch_recent_trades(session, 30)` retourne une `list[Trade]` sur l'API reelle — **500 trades retournes**
- [x] La commande curl `https://data-api.polymarket.com/trades?limit=10` retourne du JSON valide
- [x] Q1 resolue et documentee : **OUI, `/trades` sans `user=` retourne des trades globaux — 16 wallets uniques sur 20 trades**
- [x] `pytest tests/test_filters.py -v` : 11/11 tests passent
- [x] `.env` est dans `.gitignore`
- [x] `pytest tests/ -v` : 22/22 tests passent
- [x] `.github/workflows/schedule.yml` cree et YAML valide
- [x] Cron `*/30 * * * *` configure
- [x] Aucun secret hardcode dans `src/*.py`

### Rapport API — Story 1.2 (2026-04-14)

**1. Structure de la reponse API**
- Format : liste JSON directe (pas de wrapper `{"data": [...]}`)
- Champs presents par trade : `proxyWallet`, `side`, `asset`, `conditionId`, `size`, `price`, `timestamp`, `title`, `slug`, `icon`, `eventSlug`, `outcome`, `outcomeIndex`, `name`, `pseudonym`, `bio`, `profileImage`, `profileImageOptimized`, `transactionHash`
- Le timestamp est en secondes Unix (ex: `1776196612`) — pas en millisecondes, pas besoin de normaliser

**2. Q1 — Wallets uniques (VALIDE)**
- `/trades?limit=20` => 16 wallets uniques sur 20 trades
- L'endpoint retourne bien des trades globaux de wallets differents
- Aucune ESCALADE necessaire

**3. Champ `outcome`**
- Present dans chaque trade, ex: `"outcome": "Up"`, `"outcome": "No"`, `"outcome": "Under"`
- Ce ne sont PAS des YES/NO classiques — ce sont les labels specifiques de chaque marche
- Le champ `outcomeIndex` est aussi present (0 ou 1)
- Le modele Trade stocke `outcome` tel quel (str), ce qui est correct

**4. Resultat du test Python**
- `fetch_recent_trades(session, 30)` => **500 trades** recuperes sur les 30 dernieres minutes
- Premier trade: `wallet=0x7ca56b03... size=105.53 outcome=Under ts=1776196470`
- Toutes les conversions de type fonctionnent correctement

### Rapport API — Story 2.2 (2026-04-14)

**1. Structure de la reponse /activity**
- Format : liste JSON directe (meme structure que /trades)
- Champs presents : `proxyWallet`, `timestamp`, `conditionId`, `type`, `size`, `usdcSize`, `transactionHash`, `price`, `asset`, `side`, `outcomeIndex`, `title`, `slug`, `icon`, `eventSlug`, `outcome`, `name`, `pseudonym`, `bio`, `profileImage`, `profileImageOptimized`
- **Attention** : `size` est en TOKENS (ex: 5.0), `usdcSize` est le vrai montant USDC (ex: 4.995) — utiliser `usdcSize` en priorite
- Timestamp en secondes Unix (pas de normalisation necessaire)

**2. Structure de la Gamma API**
- Format : liste JSON directe
- `liquidity` : string "60469.2567" — PRESENT
- `liquidityNum` : float 60469.2567 — PRESENT (prefere dans le code)
- `liquidityClob` : float identique — PRESENT
- `endDate` : "2026-07-31T12:00:00Z" (datetime complet)
- `endDateIso` : "2026-07-31" (date seule — prefere dans le code)
- **R3 RESOLU** : `liquidity_usdc` est toujours present (3 champs disponibles)

**3. Polygon RPC**
- `polygon-rpc.com` redirige desormais vers Ankr et necessite une cle API
- Solution : `https://1rpc.io/matic` fonctionne sans cle, retourne le nonce correctement
- Default mis a jour dans `config.py` (`polygon_rpc_url`)
- Sur un wallet actif : `tx_count_polygon = 1` (cohérent, proxy wallet Polymarket)

**4. Resultats des criteres d'acceptance**
- CA1 : `fetch_wallet_history(wallet_reel)` — OK (tx_count=1, trades=500, avg=10.14 USDC, markets=178)
- CA2 : `fetch_market_metadata(condition_id_reel)` — OK (liquidity=60469.26, end_date=2026-07-31)
- CA3 : Cache — OK (2 appels same condition_id = 1 seul appel HTTP, `market2 is market1 = True`)
- CA4 : RPC down (mock) — OK (`tx_count_polygon=-1` sentinel, WalletProfile retourné quand meme)
- CA5 : R3 documente — OK (`liquidity` et `liquidityNum` tous deux presents dans Gamma API)

---

## Instructions de deploiement

### Prérequis
- Repository GitHub public créé
- Telegram Bot créé via @BotFather (récupérer le token)
- Chat ID Telegram (utilisateur ou groupe) où recevoir les alertes

### Étape 1 — Configurer les GitHub Secrets (obligatoires)

Dans `Settings > Secrets and variables > Actions > Secrets` du repo GitHub :

| Nom du secret | Valeur | Pourquoi secret |
|--------------|--------|-----------------|
| `TELEGRAM_BOT_TOKEN` | `123456789:ABCdefGHI...` | Clé d'accès au bot Telegram, ne pas exposer |
| `TELEGRAM_CHAT_ID` | `-1001234567890` ou `123456789` | ID du destinataire des alertes |

### Étape 2 — Configurer les GitHub Variables (optionnel, valeurs par défaut disponibles)

Dans `Settings > Secrets and variables > Actions > Variables` du repo GitHub :

| Nom de la variable | Valeur par défaut | Description |
|-------------------|------------------|-------------|
| `ALERT_SCORE_THRESHOLD` | `60` | Score minimum pour déclencher une alerte (0-100) |
| `MIN_BET_USDC` | `500` | Montant minimum d'un trade pour être analysé (USDC) |
| `WALLET_AGE_DAYS_MAX` | `7` | Age maximum d'un wallet pour activer le signal S1 |
| `WALLET_TX_MIN` | `10` | Nombre minimum de transactions Polygon pour S2 |
| `ORDER_BOOK_IMPACT_PCT` | `2` | Impact minimum sur l'order book pour S3 (%) |
| `BET_VS_HISTORY_MULTIPLIER` | `3` | Multiplicateur vs historique pour activer S4 |
| `CONCENTRATION_PCT` | `60` | Concentration minimale sur un marché pour S5 (%) |
| `TIMING_HOURS_BEFORE` | `4` | Heures avant résolution pour activer S6 |
| `BOT_MARKET_COUNT_MAX` | `50` | Seuil de marchés actifs pour détecter un bot (S7) |
| `LOOKBACK_MINUTES` | `30` | Fenêtre temporelle d'analyse en minutes |
| `POLYGON_RPC_URL` | `https://1rpc.io/matic` | RPC Polygon public sans clé API |

### Étape 3 — Pousser le code

```bash
cd /Users/antoinebirabent/polymarket-insider-bot
git init
git add .
git commit -m "feat: MVP Polymarket Insider Bot"
git remote add origin git@github.com:{USERNAME}/{REPO}.git
git push -u origin main
```

### Étape 4 — Activer les GitHub Actions

- Le workflow `.github/workflows/schedule.yml` est détecté automatiquement après le push
- Le cron `*/30 * * * *` démarre automatiquement (toutes les 30 minutes)
- Pour tester manuellement : `Actions > Polymarket Insider Bot > Run workflow`

### Étape 5 — Vérifier le premier run

1. Aller dans `Actions > Polymarket Insider Bot`
2. Attendre le prochain run automatique (au plus 30 minutes)
3. Vérifier que le job `scan` se termine en `success`
4. Si le bot détecte un trade suspect, un message Telegram est envoyé

### Notes de sécurité

- `.env` est dans `.gitignore` — ne jamais le commiter
- Scanner avant push : `git log --all -p | grep -i "token\|secret\|password"`
- Les seuils dans GitHub Variables ne sont pas sensibles (valeurs par défaut visibles dans le workflow)
- Seuls `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` sont dans les Secrets

---

## Structure du projet finale

```
polymarket-insider-bot/
├── .github/
│   └── workflows/
│       └── schedule.yml          # Story 4.2 — DONE
├── src/
│   ├── __init__.py               # Story 1.1 — DONE
│   ├── main.py                   # Story 3.2 — DONE
│   ├── fetcher.py                # Stories 1.2 + 2.2 — DONE
│   ├── scorer.py                 # Story 2.3 — DONE
│   ├── filters.py                # Story 2.1 — DONE
│   ├── alerter.py                # Story 3.1 — DONE
│   ├── config.py                 # Story 1.1 — DONE
│   └── models.py                 # Story 1.1 — DONE
├── tests/
│   ├── __init__.py               # Story 1.1 — DONE
│   ├── conftest.py               # Story 4.1 — DONE
│   ├── test_scorer.py            # Stories 2.3 + 4.1 — DONE (11 tests)
│   └── test_filters.py           # Stories 2.1 + 4.1 — DONE (11 tests)
├── .env.example                  # Story 1.1 — DONE
├── .env                          # JAMAIS commite (.gitignore)
├── .gitignore                    # Story 1.1 — DONE
├── requirements.txt              # Story 1.1 — DONE
├── PRD.md
├── ARCHITECTURE.md
└── STORIES.md
```

---

## Commandes utiles

### Installation
```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Lancer le bot localement
```bash
# Creer .env a partir du template
cp .env.example .env
# Remplir TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID dans .env
python -m src.main
```

### Tests unitaires
```bash
pytest tests/ -v
pytest tests/test_scorer.py -v
pytest tests/test_filters.py -v
```

### Validation de l'API Polymarket (Story 1.2)
```bash
# Verifier que /trades retourne des trades globaux (sans user=)
curl -s "https://data-api.polymarket.com/trades?limit=10" | python3 -m json.tool | head -100

# Diagnostic wallets uniques (Q1)
curl -s "https://data-api.polymarket.com/trades?limit=10" | python3 -c "import json,sys; data=json.load(sys.stdin); print(f'{len(data)} trades, wallets uniques: {len(set(t[\"proxyWallet\"] for t in data if \"proxyWallet\" in t))}')"

# Verifier la structure d'un wallet (remplacer par une vraie adresse)
curl -s "https://data-api.polymarket.com/activity?user=0x{adresse}&limit=10" | python3 -m json.tool

# Verifier la Gamma API (remplacer par un vrai condition_id)
curl -s "https://gamma-api.polymarket.com/markets/{condition_id}" | python3 -m json.tool
```

### Compter les lignes de code (contrainte PRD : < 500 lignes)
```bash
wc -l src/*.py
```

---

## Points d'attention critiques

### 1. Validation API avant tout (Story 1.2)
La question Q1 du PRD (le endpoint `/trades` sans `user=` retourne-t-il des trades globaux ?) a ete validee : **OUI — 16 wallets uniques sur 20 trades**.

### 2. Secrets et securite
- `.env` doit etre dans `.gitignore` AVANT le premier commit
- GitHub Secrets pour `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID`
- GitHub Variables (pas Secrets) pour les seuils (pas sensibles, valeurs par defaut visibles)
- Scanner le code avant push : `git log --all -p | grep -i "token\|secret\|password"`

### 3. Pas de python-telegram-bot
Le PRD mentionne `python-telegram-bot` mais l'ARCHITECTURE.md a tranche : utiliser `requests` directement sur `https://api.telegram.org/bot{TOKEN}/sendMessage`. Le `requirements.txt` NE doit PAS inclure `python-telegram-bot`.

### 4. Valeurs sentinelles
- `tx_count_polygon == -1` signifie "inconnu" (Polygon RPC down) -> S2 non active
- `first_polymarket_trade_ts == -1` signifie "inconnu" -> S1 non active
- Toutes les fonctions de `fetcher.py` retournent `[]` ou `None` en cas d'erreur (jamais d'exception remontee)

### 5. TDD pour scorer.py et filters.py
Ces deux modules ne font aucun appel HTTP. Les 22 cas de test sont implementes et passent tous.

---

## Risques residuels (a surveiller en developpement)

| Risque | Criticite | Story de validation |
|--------|-----------|---------------------|
| `/trades` sans `user=` ne retourne pas les trades globaux | Haute | Story 1.2 — **RESOLU : 16 wallets uniques sur 20 trades** |
| Champ `outcome` (YES/NO) absent de la reponse `/trades` | Moyenne | Story 1.2 — **RESOLU : champ present, labels variables (Up/No/Under/...)** |
| `liquidity_usdc` absent de la reponse Gamma API | Moyenne | Story 2.2 — **RESOLU : present (liquidity str + liquidityNum float)** |
| Polygon RPC public en downtime frequents | Faible | Story 2.2 — **RESOLU : polygon-rpc.com remplace par 1rpc.io/matic (sans cle)** |
| Run > 5 minutes sur un marche tres actif | Faible | Story 3.2 — timeout-minutes: 10 dans GitHub Actions |

---

## Metriques de succes MVP (a mesurer apres 30 jours)

- Au moins 3 alertes avec score >= 70 sur des marches dont la cote a bouge de plus de 15% dans les 24h suivant l'alerte
- Taux de faux positifs < 30%
- Zero downtime critique non resolu
- Code total < 500 lignes hors tests (`wc -l src/*.py`)
