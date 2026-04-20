# PRD — Polymarket Insider Bot

**Version** : 1.0
**Date** : 2026-04-13
**Auteur** : Product Manager (Claude Code)
**Statut** : Approuvé pour développement
**Basé sur** : Product Brief v1.0 validé (GO)

---

## 1. Résumé exécutif

Bot Python autonome qui se déclenche toutes les 30 minutes via GitHub Actions, interroge les APIs publiques Polymarket, calcule un score "insider" composite (0-100) pour chaque wallet ayant tradé récemment, et envoie une alerte Telegram uniquement si un ou plusieurs wallets dépassent le seuil configuré.

Le bot est strictement passif (lecture seule, aucune exécution automatique). Il sert d'outil d'observation pour identifier manuellement des opportunités de copy trading avant que la cote intègre l'information.

**Contrainte centrale** : silence absolu si aucun wallet suspect — zéro message envoyé si tous les scores sont sous le seuil.

---

## 2. Objectifs et métriques de succès

### Objectifs

| # | Objectif | Critère de succès mesurable |
|---|----------|-----------------------------|
| O1 | Détecter les wallets à comportement statistiquement anormal | Score composite calculé pour 100% des trades analysés lors de chaque run |
| O2 | Éliminer le bruit (wash trading, bots d'arb) | Moins de 30% de faux positifs parmi les alertes envoyées à J+30 |
| O3 | Alerter uniquement sur signal significatif | 0 message envoyé si aucun wallet ne dépasse le seuil |
| O4 | Rester simple et maintenable | Code total < 500 lignes hors tests, lisible sans commentaire |
| O5 | Coût infrastructure zéro | Fonctionne intégralement sur GitHub Actions (repo public, runners standard) |

### Métriques de validation à 30 jours d'usage

- Au moins 3 alertes avec score >= 70 sur des marchés dont la cote a bougé de plus de 15% dans les 24h suivant l'alerte
- Taux de faux positifs < 30% (alertes sur wash traders ou bots d'arbitrage identifiés après coup)
- Zero downtime critique non résolu (les runs GitHub Actions sont observables dans les logs)

---

## 3. Fonctionnalités

### 3.1 Must-have (MVP)

#### F1 — Déclenchement périodique via GitHub Actions cron

- Le workflow se déclenche toutes les 30 minutes (`*/30 * * * *`)
- Chaque run est autonome et stateless : pas de daemon, pas de base de données persistante entre runs
- Les runs GitHub Actions sont loggés nativement, accessible depuis l'interface web du repo
- Le workflow doit se terminer en moins de 5 minutes pour rester dans les limites de timeout

**Critère d'acceptance** : le workflow `schedule.yml` s'exécute automatiquement toutes les 30 minutes et est visible dans l'onglet "Actions" du repo GitHub.

#### F2 — Appel API Polymarket (lecture seule)

- Récupérer les trades des 30 dernières minutes via `GET /trades` de la Data API (`https://data-api.polymarket.com/trades`)
- Pour chaque wallet unique détecté dans ces trades, récupérer son historique via `GET /trades?maker_address=<wallet>`
- Récupérer les métadonnées du marché concerné via Gamma API (`https://gamma-api.polymarket.com/markets/<condition_id>`)
- Pour l'âge du wallet : appel Polygon RPC (`eth_getTransactionCount` sur le premier bloc connu du wallet)
- Aucune authentification requise pour ces endpoints en lecture

**Limites techniques à respecter :**
- Gamma API : 60 req/min pour accès non authentifié
- Data API : backoff exponentiel sur 429, délai initial 2s
- Polygon RPC : utiliser un endpoint public (Alchemy free tier ou `polygon-rpc.com`)

**Critère d'acceptance** : un run complet récupère correctement les trades des 30 dernières minutes et enrichit chaque wallet avec son historique sans lever d'exception sur un marché actif.

#### F3 — Scoring "insider" composite (0-100)

Six signaux sont calculés de façon indépendante puis agrégés. Chaque signal est soit activé (points accordés) soit inactif (0 point). Le score est la somme des signaux activés.

| Signal | Points | Condition d'activation | Variable d'env de configuration |
|--------|--------|------------------------|----------------------------------|
| S1 — Age du wallet | 20 | Wallet créé il y a < `WALLET_AGE_DAYS_MAX` jours (défaut : 7) | `WALLET_AGE_DAYS_MAX` |
| S2 — Faible historique | 15 | Wallet a effectué < `WALLET_TX_MIN` transactions lifetime (défaut : 10) | `WALLET_TX_MIN` |
| S3 — Impact order book | 20 | Mise représente > `ORDER_BOOK_IMPACT_PCT` % de la liquidité du marché (défaut : 2) | `ORDER_BOOK_IMPACT_PCT` |
| S4 — Mise vs historique | 15 | Mise > `BET_VS_HISTORY_MULTIPLIER`x la mise moyenne du wallet (défaut : 3) | `BET_VS_HISTORY_MULTIPLIER` |
| S5 — Concentration marché | 15 | > `CONCENTRATION_PCT` % du capital du wallet sur un seul marché (défaut : 60) | `CONCENTRATION_PCT` |
| S6 — Timing pré-résolution | 15 | Trade passé < `TIMING_HOURS_BEFORE` h avant la résolution du marché (défaut : 4) | `TIMING_HOURS_BEFORE` |

**Score final** = somme des points des signaux activés (0 à 100).

**Règle de déclenchement d'alerte** : score >= `ALERT_SCORE_THRESHOLD` (défaut : 60) ET mise >= `MIN_BET_USDC` (défaut : 500 USDC).

**Critère d'acceptance** : un wallet fictif avec tous les seuils dépassés reçoit un score de 100/100 ; un wallet standard (âge > 30j, mise habituelle) reçoit un score <= 20/100 dans les tests unitaires.

#### F4 — Filtres anti-bruit (pré-scoring)

Les wallets et trades suivants sont exclus avant le scoring :

| Filtre | Règle | Justification |
|--------|-------|---------------|
| Anti-wash trading | Exclure les trades dont le montant est < 1 USDC | Wash trading fréquent sur micro-montants |
| Anti-bot d'arbitrage | Exclure les wallets actifs sur > `BOT_MARKET_COUNT_MAX` marchés simultanément (défaut : 50) | Market makers légitimes à exclure |
| Anti-self-trade | Exclure si le wallet a tradé dans les deux sens (YES et NO) sur le même marché dans la même heure | Pattern de wash trading intentionnel |
| Mise minimale | Exclure les trades < `MIN_BET_USDC` USDC (défaut : 500) | Réduire le volume à analyser, signal trop faible |

**Critère d'acceptance** : un wallet avec 60 marchés actifs simultanément n'apparaît jamais dans les alertes, même avec un score de 100.

#### F5 — Alerte Telegram (uniquement si signal)

Le bot envoie un message Telegram au chat configuré **uniquement** si au moins un wallet passe les filtres et dépasse le seuil de score.

**Format du message** (fixe, non configurable en V1) :

```
ALERTE INSIDER — Score {score}/100

Marche : "{market_question}"
Wallet : {wallet_short} (age: {age_days} jours, {tx_count} txs)
Mise : {amount} USDC sur {side} @ {price}
Impact liquidite : {impact_pct}% de l'order book

Signaux detectes :
 [+20] Wallet frais < 7j          (si S1 actif)
 [+15] < 10 transactions          (si S2 actif)
 [+20] Impact > 2% order book     (si S3 actif)
 [+15] Mise {multiplier}x vs historique (si S4 actif)
 [+15] Concentration marche unique (si S5 actif)
 [+15] Timing < {hours}h resolution (si S6 actif)

Cote actuelle : {price_pct}%
Lien : polymarket.com/event/{market_slug}
```

Note : les emojis sont volontairement absents du format texte ci-dessus pour maintenir la lisibilité du code. Le message réel peut inclure un emoji de warning en debut de message si souhaite — à definir lors de l'implémentation.

**Règle silence** : si tous les scores sont < `ALERT_SCORE_THRESHOLD` ou si aucun trade ne passe les filtres, aucun message n'est envoyé. Aucun message "rien à signaler" non plus.

**Critère d'acceptance** : sur un run avec 0 trade suspect, la conversation Telegram reste vide. Sur un run avec 1 trade suspect simulé, exactement 1 message est envoyé.

#### F6 — Configuration via variables d'environnement

Toutes les valeurs seuils sont configurables sans modifier le code :

| Variable | Défaut | Description |
|----------|--------|-------------|
| `TELEGRAM_BOT_TOKEN` | — (obligatoire) | Token du bot Telegram |
| `TELEGRAM_CHAT_ID` | — (obligatoire) | ID du chat de destination |
| `ALERT_SCORE_THRESHOLD` | `60` | Score minimum pour déclencher une alerte |
| `MIN_BET_USDC` | `500` | Mise minimale en USDC pour qu'un trade soit analysé |
| `WALLET_AGE_DAYS_MAX` | `7` | Signal S1 : âge max du wallet en jours |
| `WALLET_TX_MIN` | `10` | Signal S2 : nb max de transactions lifetime |
| `ORDER_BOOK_IMPACT_PCT` | `2` | Signal S3 : % d'impact sur l'order book |
| `BET_VS_HISTORY_MULTIPLIER` | `3` | Signal S4 : multiplicateur vs historique |
| `CONCENTRATION_PCT` | `60` | Signal S5 : % max de concentration sur un marché |
| `TIMING_HOURS_BEFORE` | `4` | Signal S6 : heures avant résolution |
| `BOT_MARKET_COUNT_MAX` | `50` | Filtre bot : nb max de marchés actifs simultanément |
| `LOOKBACK_MINUTES` | `30` | Fenêtre temporelle de scan (en minutes) |
| `POLYGON_RPC_URL` | `https://polygon-rpc.com` | RPC Polygon pour l'âge des wallets |

En local : fichier `.env` (non commité). En production : GitHub Secrets.

**Critère d'acceptance** : modifier `ALERT_SCORE_THRESHOLD=80` via la variable d'environnement change le seuil sans modifier une ligne de code.

#### F7 — Logs détaillés dans GitHub Actions

Chaque run produit des logs structurés permettant le debug sans accès à une base de données :

```
[RUN START] 2026-04-13 14:30:01 UTC
[FETCH] 47 trades dans les 30 dernières minutes
[FILTER] 12 trades exclus (< 500 USDC), 3 wallets exclus (bots arb)
[ANALYSIS] 8 wallets uniques analysés
[SCORE] 0x1234...abcd → score 78/100 [S1+S3+S4+S6 actifs]
[SCORE] 0xabcd...1234 → score 35/100 [S2 actif seulement]
[ALERT] 1 alerte envoyée (seuil: 60)
[RUN END] durée: 23s
```

**Critère d'acceptance** : les logs permettent de retrouver exactement pourquoi un wallet a reçu tel score, sans accès au code.

---

### 3.2 Nice-to-have (post-MVP)

Ces fonctionnalités ne font pas partie du MVP. Elles sont documentées pour éviter d'architecturer le code contre elles.

| ID | Fonctionnalité | Valeur | Complexité |
|----|---------------|--------|------------|
| NH1 | Tracker le PnL des wallets alertés (validation du signal a posteriori) | Réduit le biais du survivant, mesure la précision réelle | Moyenne — nécessite persistance entre runs |
| NH2 | Commandes Telegram `/status`, `/mute 1h` | Contrôle sans redéploiement | Faible — mode polling ou webhook |
| NH3 | Whitelist/blacklist de wallets | Exclure des wallets connus (market makers légitimes) | Faible — liste dans env var |
| NH4 | Détection de clusters de wallets liés | Insiders qui fractionnent leurs entrées | Haute — analyse de graphe |
| NH5 | Backtesting sur données historiques Polymarket | Calibration des seuils avant prod | Moyenne — script séparé |
| NH6 | Score adaptatif par catégorie de marché | Seuils différents pour marchés sport vs politique | Haute |

---

## 4. Hors scope (ce qu'on ne fait PAS en V1)

Ces éléments sont explicitement exclus et ne doivent pas influencer les décisions d'architecture du MVP.

| Hors scope | Raison |
|-----------|--------|
| Auto-exécution de trades | Risque non géré : les insiders peuvent piéger les copieurs automatiques |
| Interface web | Pas de valeur ajoutée pour usage solo, Telegram suffit |
| Base de données persistante entre runs | Complexifie inutilement l'infra — chaque run est stateless |
| Marchés sportifs | Wash trading jusqu'à 90% selon Columbia 2025 — signal/bruit trop faible |
| Marchés à résolution > 6 mois | Signal S6 (timing) jamais activé, biais du survivant amplifié |
| Machine learning / modèles statistiques avancés | Black box, overfitting sur données bruitées, non auditable |
| Multi-plateformes (Kalshi, Manifold…) | Hors scope technique V1 |
| Authentification Polymarket (L1/L2) | Uniquement lecture publique, aucune clé privée requise |
| Notifications email ou Slack | Telegram uniquement, cohérent avec les bots existants |
| Tests de charge / stress testing | Usage solo, volumes faibles |

---

## 5. Architecture technique

### 5.1 Vue d'ensemble

```
GitHub Actions (cron */30 * * * *)
        |
        v
  main.py (point d'entrée)
        |
        +-- fetcher.py      → appels Data API + Gamma API + Polygon RPC
        |
        +-- scorer.py       → calcul score composite par wallet/trade
        |
        +-- filters.py      → anti-wash, anti-bot, mise minimale
        |
        +-- alerter.py      → formatage et envoi Telegram
        |
        +-- config.py       → lecture variables d'environnement avec pydantic-settings
```

### 5.2 Stack

| Composant | Choix | Version cible |
|-----------|-------|---------------|
| Langage | Python | 3.11+ |
| HTTP | `requests` | 2.31+ |
| Telegram | `python-telegram-bot` | 20.x |
| Config / validation | `pydantic-settings` | 2.x |
| Variables d'env | `python-dotenv` | 1.x |
| Scheduling | GitHub Actions `schedule` | — |
| Secrets | GitHub Secrets (prod) / `.env` (local) | — |

Dépendances volontairement minimales. Pas de `asyncio` en V1 (runs courts, pas de WebSocket en mode cron), pas de SQLite (stateless), pas de `web3.py` si le Polygon RPC est accessible via `requests` simple.

### 5.3 Structure du projet

```
polymarket-insider-bot/
├── .github/
│   └── workflows/
│       └── schedule.yml        # Cron GitHub Actions
├── src/
│   ├── __init__.py
│   ├── main.py                 # Point d'entrée, orchestration
│   ├── fetcher.py              # Appels API Polymarket + Polygon
│   ├── scorer.py               # Calcul du score composite
│   ├── filters.py              # Filtres anti-bruit
│   ├── alerter.py              # Envoi Telegram
│   └── config.py               # Config pydantic-settings
├── tests/
│   ├── test_scorer.py          # Tests unitaires scoring
│   └── test_filters.py         # Tests unitaires filtres
├── .env.example                # Template variables (commité, sans valeurs)
├── requirements.txt
└── PRD.md
```

### 5.4 Workflow GitHub Actions

```yaml
# .github/workflows/schedule.yml (aperçu)
on:
  schedule:
    - cron: '*/30 * * * *'
  workflow_dispatch:              # Déclenchement manuel pour tests

jobs:
  scan:
    runs-on: ubuntu-latest
    timeout-minutes: 10           # Hard stop à 10min (run normal < 5min)
    env:
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
      ALERT_SCORE_THRESHOLD: ${{ vars.ALERT_SCORE_THRESHOLD || '60' }}
      # ... autres variables avec valeurs par défaut
```

### 5.5 Contraintes techniques identifiées

| Contrainte | Impact | Mitigation |
|-----------|--------|-----------|
| Gamma API : 60 req/min non authentifié | Limite le nombre de marchés enrichissables par run | Cache en mémoire dans le run, pagination intelligente |
| Data API : pas de rate limit documenté officiellement | Risque de 429 non anticipé | Backoff exponentiel sur 429, délai 2s initial |
| Polygon RPC public : variable selon provider | Latence ou indisponibilité possible | Retry 3x, timeout 5s, fallback : âge wallet = inconnu (S1 non activé) |
| GitHub Actions cron : délai de 5 à 10min possible | Fenêtre de 30min peut se chevaucher légèrement | Acceptable pour usage solo, non critique |
| `python-telegram-bot` v20 : limite 30 msg/s globale | Non impactant (1 à 3 alertes max par run) | Aucune mitigation nécessaire en V1 |
| Repo public : code visible de tous | Pas de secret dans le code, uniquement GitHub Secrets | Politique de commit : `.env` dans `.gitignore` obligatoire |

---

## 6. User Stories

### US1 — Run normal sans alerte

**En tant que** développeur utilisant le bot,
**Je veux** que le bot tourne toutes les 30 minutes sans m'envoyer de messages,
**Afin de** ne pas être spammé quand il ne se passe rien de notable sur Polymarket.

**Critères d'acceptance :**
- Le run GitHub Actions se termine avec status "success"
- Aucun message Telegram envoyé
- Les logs indiquent le nombre de wallets analysés et les scores calculés
- Le run se termine en moins de 5 minutes

### US2 — Détection et alerte d'un wallet suspect

**En tant que** développeur utilisant le bot,
**Je veux** recevoir une alerte Telegram quand un wallet avec un score >= 60 trade sur Polymarket,
**Afin de** pouvoir décider manuellement si je copie la position.

**Critères d'acceptance :**
- L'alerte est reçue dans les 30 minutes suivant le trade (fenêtre de polling)
- L'alerte inclut : score, détail des signaux activés, montant, marché, lien direct
- L'alerte ne contient pas de données manquantes ou de valeurs "None"
- Si plusieurs wallets suspects dans le même run, un message par wallet

### US3 — Ajustement de la sensibilité

**En tant que** développeur utilisant le bot,
**Je veux** changer le seuil d'alerte de 60 à 75 sans modifier le code,
**Afin de** réduire les faux positifs pendant la phase de calibration.

**Critères d'acceptance :**
- Modifier `ALERT_SCORE_THRESHOLD` dans GitHub Variables (ou `.env` local) suffit
- Le changement est effectif au prochain run sans redéploiement
- Le log du run indique le seuil utilisé

### US4 — Debug d'une alerte manquée

**En tant que** développeur utilisant le bot,
**Je veux** retrouver dans les logs GitHub Actions pourquoi un wallet spécifique n'a pas été alerté,
**Afin de** calibrer les seuils si le bot a raté un trade pertinent.

**Critères d'acceptance :**
- Les logs indiquent pour chaque wallet analysé : score total, signaux activés, signaux non activés avec les valeurs mesurées
- Les logs indiquent quels wallets ont été exclus par les filtres et pourquoi
- Les logs sont accessibles depuis l'interface web GitHub Actions pendant 90 jours

### US5 — Exclusion d'un bot d'arbitrage connu

**En tant que** développeur utilisant le bot,
**Je veux** que les wallets actifs sur plus de 50 marchés simultanément soient automatiquement exclus,
**Afin de** ne pas recevoir d'alertes sur des market makers qui ne sont pas des insiders.

**Critères d'acceptance :**
- Un wallet avec 60 positions actives ne génère jamais d'alerte, même avec un score de 100
- Les logs indiquent "[FILTER] wallet 0x... exclu: bot (62 marchés actifs)"

---

## 7. Exigences non-fonctionnelles

| Exigence | Cible | Mesure |
|----------|-------|--------|
| Durée d'un run complet | < 5 minutes | Logs GitHub Actions (durée visible) |
| Fiabilité du scheduling | Run effectif dans les 40 minutes suivant l'heure prévue | Monitoring manuel des logs Actions |
| Code maintenable | < 500 lignes hors tests | `wc -l src/*.py` |
| Zéro secret dans le code | Aucune clé hardcodée | Review `.env.example` + scan git |
| Lisibilité des logs | Debug possible sans IDE | Review manuelle des logs Actions |
| Robustesse aux API down | Pas d'exception non catchée | Toutes les exceptions API sont loggées + run termine proprement |
| Coût infrastructure | 0 $/mois | GitHub Actions public repo, runners standard |

---

## 8. Risques et mitigations

### Risques techniques

| Risque | Probabilité | Impact | Mitigation dans le code |
|--------|-------------|--------|------------------------|
| Changement de format API Polymarket | Faible | Élevé | Abstraire tous les appels dans `fetcher.py`, tests sur données réelles lors du dev |
| Rate limiting Polygon RPC public | Moyenne | Faible | Retry 3x avec timeout 5s, si échec : S1 non calculé, wallet scoré sans ce signal |
| Faux positifs sur bots d'arbitrage | Haute | Moyen | Filtre `BOT_MARKET_COUNT_MAX` + scoring multi-signal (un seul critère ne suffit pas) |
| Wash trading (25% du volume) | Haute | Moyen | Filtre anti-wash obligatoire en V1 (montant < 1 USDC, aller-retour dans la même heure) |
| Run qui dépasse 10 minutes | Faible | Faible | Timeout GitHub Actions configuré à 10min, pagination limitée à 200 trades par run |

### Risques produit

| Risque | Probabilité | Impact | Mitigation comportementale |
|--------|-------------|--------|---------------------------|
| Biais du survivant | Certaine | Moyen | NH1 (PnL tracking post-alerte) à implémenter dès que le bot a 30 jours de données |
| Manipulation (faux insider pour piéger les copieurs) | Faible | Élevé | Validation humaine obligatoire, jamais d'exécution automatique |
| Dégradation du signal dans le temps | Probable | Moyen | Recalibration des seuils à J+30, J+90 basée sur les métriques réelles |

---

## 9. Questions ouvertes

| ID | Question | Priorité | Impact si non résolu |
|----|----------|----------|----------------------|
| Q1 | La Data API Polymarket retourne-t-elle les trades des 30 dernières minutes sans pagination sur des marchés actifs (> 100 trades/30min) ? | Haute | A tester en phase de développement — si pagination nécessaire, adapter `fetcher.py` |
| Q2 | Comment calculer l'âge d'un wallet Polygon avec `requests` simple sans `web3.py` ? (endpoint RPC `eth_getBlockByNumber` sur le 1er bloc du wallet) | Moyenne | Si `web3.py` requis, ajouter la dépendance — n'est pas bloquant |
| Q3 | Le signal S5 (concentration marché) nécessite de connaître le capital total du wallet : la Data API expose-t-elle cette information directement, ou faut-il l'inférer des trades historiques ? | Haute | Si non disponible directement → approximer par la somme des positions ouvertes — à valider en dev |
| Q4 | Faut-il un message de démarrage Telegram au lancement du premier run (confirmation que le bot est opérationnel) ? | Basse | Pas bloquant — peut être ajouté en post-MVP |

Aucune question n'est classée BLOQUANT : le développement peut démarrer. Q1 et Q3 seront résolues lors de la phase d'exploration API (Phase 1 du plan de dev).

---

## 10. Plan de développement (référence)

Issu du Product Brief, reproduit pour référence :

| Phase | Contenu | Durée estimée |
|-------|---------|---------------|
| Phase 1 — Ingestion | `fetcher.py` : appels Data API, Gamma API, Polygon RPC. Validation des données retournées en local. | 2-3 jours |
| Phase 2 — Scoring | `scorer.py` + `filters.py` : 6 signaux + filtres anti-bruit. Tests unitaires avec données réelles. | 3-5 jours |
| Phase 3 — Alertes | `alerter.py` : envoi Telegram. Configuration via `.env`. Test du format message. | 1-2 jours |
| Phase 4 — GitHub Actions | `schedule.yml` : workflow cron, GitHub Secrets, logs, timeout. Test de bout en bout. | 1-2 jours |
| Phase 5 — Calibration | 30 jours d'usage réel, ajustement des seuils, mesure du taux de faux positifs. | En continu |

**Durée totale MVP** : 7 à 12 jours ouvrés pour un développeur Python expérimenté.

---

## 11. Prochaines étapes

Ce projet est purement backend/API, sans interface utilisateur.

**Invoquer directement @architect** pour :
- Valider la structure de `fetcher.py` (gestion pagination, retry, cache en mémoire intra-run)
- Définir le contrat d'interface entre `fetcher.py`, `scorer.py`, et `alerter.py` (types de données)
- Choisir entre `requests` synchrone et `httpx` async pour les appels API
- Structurer les tests unitaires du scorer avec des données fixtures Polymarket réelles

---

## Sources

- [Polymarket CLOB API — Rate Limits](https://docs.polymarket.com/api-reference/rate-limits)
- [Polymarket CLOB API — Authentication](https://docs.polymarket.com/api-reference/authentication)
- [GitHub Actions — Public repos free unlimited minutes](https://docs.github.com/en/actions/concepts/billing-and-usage)
- [python-telegram-bot — Avoiding flood limits](https://github.com/python-telegram-bot/python-telegram-bot/wiki/Avoiding-flood-limits)
- [Product Brief v1.0](_bmad-output/product-brief.md)
