# STORIES — Polymarket Insider Bot

**Version** : 1.0
**Date** : 2026-04-14
**Auteur** : Technical Excellence Agent (TEA)
**Statut** : Prêt pour Sprint 1
**Basé sur** : PRD v1.0 + ARCHITECTURE v1.0

---

## Readiness Check

### Les contrats d'interface sont-ils suffisamment definis pour commencer a coder sans ambiguite ?

**OUI.** L'ARCHITECTURE.md fournit :
- Les signatures completes de toutes les fonctions publiques et privees de chaque module
- Les 4 dataclasses (`Trade`, `WalletProfile`, `MarketMetadata`, `SignalResult`, `ScoredTrade`) avec leurs champs et types
- Les endpoints API avec leurs parametres (`/trades?limit=500`, `/activity?user=...&limit=500`, `/markets/{condition_id}`, Polygon RPC)
- Les valeurs sentinelles explicites (`-1` pour les champs inconnus, `None` pour les retours d'erreur)
- Le flux de donnees complet de `main.py` avec l'ordre d'appel exact

Un developpeur peut coder chaque module de facon independante sans consulter les autres.

### Y a-t-il des dependances externes a valider avant de coder (APIs, endpoints) ?

**OUI — 2 points a valider en Story 1.2 avant de continuer :**

| Risque | Criticite | Validation requise |
|--------|-----------|-------------------|
| R1 : `GET /trades?limit=500` sans `user=` retourne-t-il les trades globaux ? | Haute | Test manuel en Story 1.2 (commande curl fournie) |
| R2 : Le champ `outcome` (YES/NO) est-il present dans la reponse `/trades` ? | Moyenne | Inspecter la reponse brute en Story 1.2 |
| R3 : `liquidity_usdc` present dans la reponse Gamma API ? | Moyenne | Tester en Story 2.2 sur un marche reel |

Ces risques ne bloquent pas le demarrage — les fallbacks sont definis dans l'architecture (S3 desactive si liquidite absente, etc.) mais leur validation conditionne le bon calibrage du scorer.

### Les tests peuvent-ils etre ecrits avant le code (TDD) pour scorer.py et filters.py ?

**OUI, completement.** Les modules `scorer.py` et `filters.py` :
- Ne font aucun appel HTTP
- Ont des signatures de fonctions 100% definies dans l'ARCHITECTURE.md
- Ont 22 cas de test specifies (TC-S-01 a TC-S-11, TC-F-01 a TC-F-11) avec inputs et outputs attendus
- Dependent uniquement des dataclasses `models.py` qui sont elles-memes des structures de donnees pures

**Ordre TDD recommande pour ces modules :**
1. Creer `models.py` (dataclasses pures, 0 logique)
2. Ecrire les fixtures de test (`make_trade`, `make_wallet`, `make_market`)
3. Ecrire les tests avant le code (`test_scorer.py`, `test_filters.py`)
4. Implementer `scorer.py` et `filters.py` jusqu'a ce que les tests passent

---

## Apercu des epics et estimation globale

| Epic | Contenu | Stories | Estimation totale |
|------|---------|---------|------------------|
| Epic 1 | Fondations | 2 stories | S + M = ~3h |
| Epic 2 | Filtres et scoring | 3 stories | M + M + M = ~4.5h |
| Epic 3 | Alertes et orchestration | 2 stories | S + M = ~3h |
| Epic 4 | Tests et deploiement | 2 stories | M + M = ~3h |
| **Total** | | **9 stories** | **~13.5h** |

---

## Epic 1 — Fondations du projet

**Objectif :** Creer la structure du projet, les modeles de donnees, la configuration et valider que l'API Polymarket est accessible et retourne le format attendu.

**Definition of Done epic :** `src/models.py`, `src/config.py`, `requirements.txt`, `.env.example`, `.gitignore` commits et fonctionnels. La fonction `fetch_recent_trades()` retourne une liste de `Trade` sur l'API reelle.

---

### Story 1.1 — Structure du projet et fondations

**Titre :** En tant que developpeur, je veux un projet Python structure avec les modeles de donnees et la configuration pour pouvoir demarrer l'implementation des modules metier.

**Estimation :** S (< 2h)

**Description :**
Creer l'integralite de la structure de fichiers du projet (dossiers, fichiers vides, configuration). Implementer les 3 fichiers qui ne dependent d'aucun autre module : `models.py`, `config.py`, et les fichiers de configuration projet.

**Fichiers a creer :**
- `src/__init__.py` (vide)
- `tests/__init__.py` (vide)
- `src/models.py` — 5 dataclasses : `Trade`, `WalletProfile`, `MarketMetadata`, `SignalResult`, `ScoredTrade`
- `src/config.py` — classe `Settings` (pydantic-settings) avec toutes les variables d'environnement et valeurs par defaut
- `requirements.txt` — 5 dependances exactes (versions figees)
- `.env.example` — template complet avec toutes les variables
- `.gitignore` — inclut `.env`, `__pycache__/`, `.pytest_cache/`, `*.pyc`, `.DS_Store`

**Criteres d'acceptance :**
- [ ] `pip install -r requirements.txt` s'execute sans erreur sur Python 3.11
- [ ] `from src.models import Trade, WalletProfile, MarketMetadata, SignalResult, ScoredTrade` fonctionne sans erreur
- [ ] `from src.config import get_settings` fonctionne (avec un `.env` minimal contenant `TELEGRAM_BOT_TOKEN=test` et `TELEGRAM_CHAT_ID=123`)
- [ ] `Settings()` avec valeurs par defaut retourne `alert_score_threshold == 60`, `min_bet_usdc == 500.0`, `lookback_minutes == 30`
- [ ] Instanciation `Settings()` sans `TELEGRAM_BOT_TOKEN` leve une `ValidationError` (champ obligatoire)
- [ ] `.env` est dans `.gitignore` (verifier avec `git check-ignore .env`)
- [ ] `Trade(proxy_wallet="0x1234", condition_id="abc", side="BUY", outcome="YES", size=1000.0, price=0.7, timestamp=1713000000, transaction_hash="0xdef", market_slug="test-market", market_question="Will X happen?")` s'instancie sans erreur

**Dependencies :** aucune (premiere story)

**Risques a surveiller :**
- pydantic-settings 2.x a une syntaxe differente de pydantic 1.x — verifier `SettingsConfigDict` (pas `class Config`)
- Le champ `case_sensitive=False` est requis pour que `TELEGRAM_BOT_TOKEN` en majuscules soit lu par `telegram_bot_token` en minuscules

---

### Story 1.2 — fetch_recent_trades() avec validation API reelle

**Titre :** En tant que developpeur, je veux implementer `fetch_recent_trades()` et valider contre l'API Polymarket reelle pour confirmer que le format des donnees correspond aux dataclasses definies.

**Estimation :** M (demi-journee)

**Description :**
Implementer dans `fetcher.py` uniquement les fonctions necessaires pour `fetch_recent_trades()` :
- `fetch_recent_trades(session, lookback_minutes)` — fonction publique
- `_get_with_retry(url, session, params, max_retries)` — fonction privee de retry
- `_parse_trade(raw)` — conversion dict brut -> dataclass `Trade`

Puis valider manuellement que l'API retourne les donnees attendues (validation de Q1 et R2 de l'architecture).

**Fichiers a creer/modifier :**
- `src/fetcher.py` — implementation partielle (3 fonctions sur 6)

**Criteres d'acceptance :**

Validation fonctionnelle (code) :
- [ ] `fetch_recent_trades(session, 30)` retourne une `list[Trade]` (peut etre vide si aucun trade recent)
- [ ] Si l'API est down (simulee avec une mauvaise URL), la fonction retourne `[]` sans lever d'exception
- [ ] `_get_with_retry` attend bien 2s puis 4s puis 8s sur des reponses 429 consecutives (verifiable avec mock)
- [ ] `_parse_trade` retourne `None` si un champ obligatoire est absent du dict brut
- [ ] Les trades retournes ont un `timestamp >= now - lookback_minutes * 60`

Validation API reelle (test manuel obligatoire) :

```bash
# Commande de validation a executer et documenter dans les notes de la story
curl -s "https://data-api.polymarket.com/trades?limit=10" | python3 -m json.tool | head -100
```

- [ ] La commande curl retourne des donnees JSON (pas une erreur 403/404/401)
- [ ] **Q1 resolu :** Le endpoint `/trades` sans parametre `user=` retourne bien des trades de wallets differents (pas uniquement les trades d'un wallet specifique). Documenter le resultat dans les notes de la story.
- [ ] **R2 resolu :** Inspecter la reponse brute et confirmer la presence (ou absence) du champ `outcome`. Si absent, documenter le champ alternatif utilise comme fallback (ex: `side`).
- [ ] Les champs `proxyWallet`, `conditionId`, `size`, `price`, `timestamp`, `transactionHash` sont presents dans la reponse

**Dependances :** Story 1.1 (models.py et config.py requis)

**Risques a surveiller :**
- **Risque critique R1** : si `GET /trades` sans `user=` ne retourne pas de trades globaux, l'architecture entiere est remise en question. Dans ce cas, alternative : recuperer la liste des marches actifs via Gamma API puis faire des appels `/trades?market={id}` par marche. Documenter ce risque dans les notes de la story avant de continuer.
- Le champ `slug` et `title` peuvent etre dans un sous-objet `market` plutot qu'a la racine du trade — verifier la structure reelle de la reponse
- La pagination peut etre necessaire si > 500 trades en 30 minutes sur un marche tres actif

---

## Epic 2 — Filtres et scoring

**Objectif :** Implementer la logique metier de filtrage et de scoring. Ces modules sont 100% testables en isolation (aucun appel HTTP). L'approche TDD est fortement recommandee.

**Definition of Done epic :** `filters.py`, `scorer.py` implementes avec tous les cas de test definis dans l'ARCHITECTURE.md qui passent.

---

### Story 2.1 — filters.py : filtres anti-bruit

**Titre :** En tant que developpeur, je veux implementer les filtres anti-bruit pour exclure les wash trades et bots d'arbitrage avant le scoring.

**Estimation :** M (demi-journee)

**Description :**
Implementer `filters.py` avec les 3 fonctions definies dans l'architecture, puis ecrire les tests unitaires `test_filters.py` en TDD.

**Fichiers a creer/modifier :**
- `src/filters.py` — implementation complete (3 fonctions)
- `tests/test_filters.py` — 11 cas de test (TC-F-01 a TC-F-11)

**Criteres d'acceptance :**
- [ ] `pytest tests/test_filters.py -v` : 11/11 tests passent
- [ ] TC-F-01 : `apply_trade_filters([trade_size_0.5], cfg)` retourne `[]`
- [ ] TC-F-02 : `apply_trade_filters([trade_size_499], cfg)` retourne `[]`
- [ ] TC-F-03 : `apply_trade_filters([trade_size_500], cfg)` retourne `[trade]`
- [ ] TC-F-04 : `apply_trade_filters([trade_size_1000], cfg)` retourne `[trade]`
- [ ] TC-F-05 : Un wallet avec YES @ t=0 et NO @ t=1800 sur le meme marche est exclu (meme heure)
- [ ] TC-F-06 : Un wallet avec YES @ t=0 et NO @ t=7200 sur le meme marche est retenu (heures differentes)
- [ ] TC-F-07 : Un wallet avec YES sur marche A et NO sur marche B dans la meme heure est retenu
- [ ] TC-F-08 : `is_arb_bot(wallet_51_marches, cfg)` retourne `True`
- [ ] TC-F-09 : `is_arb_bot(wallet_50_marches, cfg)` retourne `False`
- [ ] TC-F-10 : `apply_trade_filters([], cfg)` retourne `[]`
- [ ] TC-F-11 : tous les trades filtres -> `apply_trade_filters([...], cfg)` retourne `[]`
- [ ] Les logs `[FILTER] trade ... exclu: ...` sont produits pour chaque exclusion (verifiable avec `caplog` pytest)

**Dependances :** Story 1.1 (models.py requis pour les fixtures)

**Risques a surveiller :**
- La detection du wash trade aller-retour necessite de grouper les trades par `(wallet, condition_id, floor(timestamp / 3600))` — attention a la conversion du timestamp en heure entiere
- Ordre des filtres : appliquer d'abord le filtre `< 1 USDC` (le plus rapide), puis `< MIN_BET_USDC`, puis l'aller-retour (le plus couteux)

---

### Story 2.2 — fetch_wallet_history() et fetch_market_metadata()

**Titre :** En tant que developpeur, je veux implementer l'enrichissement des wallets et des marches pour alimenter le scorer avec les donnees necessaires au calcul des 6 signaux.

**Estimation :** M (demi-journee)

**Description :**
Completer `fetcher.py` avec les fonctions d'enrichissement :
- `fetch_wallet_history(wallet_address, session)` — Data API `/activity` + Polygon RPC
- `fetch_market_metadata(condition_id, session, cache)` — Gamma API avec cache
- `_post_rpc(url, payload, session)` — appel JSON-RPC Polygon
- `_build_wallet_profile(address, history, tx_count)` — construction du `WalletProfile`

Puis valider manuellement les 2 endpoints sur des donnees reelles.

**Fichiers a creer/modifier :**
- `src/fetcher.py` — 4 fonctions supplementaires

**Criteres d'acceptance :**
- [ ] `fetch_wallet_history("0x{adresse_reel}", session)` retourne un `WalletProfile` non-None sur un wallet ayant des trades Polymarket
- [ ] `fetch_wallet_history("0x000000000000000000000000000000000000dead", session)` retourne `None` ou un `WalletProfile` avec `total_trades_count == 0` (wallet sans historique)
- [ ] `fetch_market_metadata("{condition_id_reel}", session, {})` retourne un `MarketMetadata` non-None
- [ ] Le cache fonctionne : 2 appels consecutifs avec le meme `condition_id` ne declenchent qu'un seul appel HTTP (verifiable avec mock de `session.get`)
- [ ] Si Polygon RPC est down (mock `_post_rpc` retourne `None`), `tx_count_polygon` vaut `-1` et `fetch_wallet_history` retourne quand meme un `WalletProfile` (pas de retour `None` a cause du RPC)
- [ ] **R3 resolu :** Confirmer que `liquidity_usdc` est disponible dans la reponse Gamma API. Si absent, documenter le champ alternatif ou la strategie de fallback.
- [ ] `_build_wallet_profile` calcule correctement `avg_bet_usdc` comme la moyenne des `size` des trades historiques
- [ ] `_build_wallet_profile` calcule correctement `active_market_count` comme le nombre de `condition_id` distincts

**Dependances :** Story 1.2 (fetcher.py partiel, `_get_with_retry` deja implemente)

**Risques a surveiller :**
- L'endpoint `/activity?user=...&limit=500` peut retourner les activites dans un format different de `/trades` — verifier si `_parse_trade` est reutilisable ou si un `_parse_activity` distinct est necessaire
- `market_exposures` doit etre `{condition_id: montant_total_usdc}` — il faut sommer les `size` par marche, pas les compter
- Le nombre de marches `active_market_count` doit compter les marchés sur le periode récente (dernier mois), pas lifetime — confirmer la fenetre temporelle pertinente

---

### Story 2.3 — scorer.py : 6 signaux et score composite

**Titre :** En tant que developpeur, je veux implementer le scorer composite pour calculer un score 0-100 sur chaque trade enrichi a partir des 6 signaux definis dans le PRD.

**Estimation :** M (demi-journee)

**Description :**
Implementer `scorer.py` avec les 7 fonctions definies dans l'architecture, puis ecrire les tests unitaires `test_scorer.py` en TDD.

**Fichiers a creer/modifier :**
- `src/scorer.py` — implementation complete (7 fonctions)
- `tests/test_scorer.py` — 11 cas de test (TC-S-01 a TC-S-11)

**Criteres d'acceptance :**
- [ ] `pytest tests/test_scorer.py -v` : 11/11 tests passent
- [ ] TC-S-01 : wallet avec tous les signaux actives -> `score == 100`
- [ ] TC-S-02 : wallet standard (age > 30j, tx > 100, mise habituelle, impact faible, concentration faible, resolution lointaine) -> `score <= 20`
- [ ] TC-S-03 : `first_polymarket_trade_ts == -1` -> `s1_fresh_wallet == False`
- [ ] TC-S-04 : `tx_count_polygon == -1` -> `s2_low_history == False`
- [ ] TC-S-05 : `liquidity_usdc == 0` -> `s3_order_book_impact == False`
- [ ] TC-S-06 : `avg_bet_usdc == 0` (premier trade) -> `s4_bet_vs_history == True`
- [ ] TC-S-07 : `end_date_iso == None` -> `s6_timing == False`
- [ ] TC-S-08 : resolution dans 6h (> `TIMING_HOURS_BEFORE` par defaut de 4h) -> `s6_timing == False`
- [ ] TC-S-09 : resolution dans 2h (< `TIMING_HOURS_BEFORE`) -> `s6_timing == True`
- [ ] TC-S-10 : score est exactement la somme des poids des signaux actifs (ex: S1+S3 = 20+20 = 40)
- [ ] TC-S-11 : modifier `cfg.alert_score_threshold` ne change pas le score retourne par `score_trade()`
- [ ] Le champ `signals.details` contient les valeurs mesurees (ex: `{"age_days": "3"}`) pour chaque signal

**Dependances :** Story 2.2 (models.py avec tous les champs complets, validation API terminee)

**Risques a surveiller :**
- S4 : si `avg_bet_usdc == 0`, activer le signal (convention documentee dans l'architecture) — ne pas diviser par zero
- S5 : si `sum(market_exposures.values()) == 0`, retourner `False` — verifier ce cas limite dans les tests
- S6 : la conversion `end_date_iso` (ISO8601) en timestamp Unix doit gerer les fuseaux horaires — utiliser `datetime.fromisoformat()` avec `timezone.utc`
- Les `SIGNAL_WEIGHTS` doivent totaliser exactement 100 (verifier : 20+15+20+15+15+15 = 100)

---

## Epic 3 — Alertes et orchestration

**Objectif :** Connecter tous les modules dans un pipeline fonctionnel de bout en bout, avec l'envoi d'alertes Telegram.

**Definition of Done epic :** `python -m src.main` s'execute localement sans erreur sur un marche Polymarket actif et envoie (ou non) une alerte Telegram selon les trades detectes.

---

### Story 3.1 — alerter.py : formatage et envoi Telegram

**Titre :** En tant que developpeur, je veux implementer l'alerter pour formater les messages et les envoyer via l'API Telegram, afin de recevoir les alertes insider sur mon telephone.

**Estimation :** S (< 2h)

**Description :**
Implementer `alerter.py` avec les 3 fonctions definies dans l'architecture. Tester manuellement avec un vrai bot Telegram et un `ScoredTrade` factice.

**Fichiers a creer/modifier :**
- `src/alerter.py` — implementation complete (3 fonctions)

**Criteres d'acceptance :**
- [ ] `format_message(scored_trade)` ne retourne jamais de string contenant le mot "None" (tester avec des champs optionnels a `None`)
- [ ] `format_message(scored_trade)` avec tous les signaux actives produit exactement les 6 lignes `[+XX]` dans le message
- [ ] `format_message(scored_trade)` avec 0 signaux actives ne produit aucune ligne `[+XX]`
- [ ] `send_alert(scored_trade, cfg, session)` avec un vrai token Telegram et chat_id retourne `True` et le message est recu sur Telegram
- [ ] Le message recu contient : score, nom du marche, adresse wallet (format `0x1234...abcd`), montant, outcome, prix, lien polymarket.com
- [ ] `_post_telegram` retourne `False` si le token est invalide (pas d'exception non geree)
- [ ] Test manuel : creer un `ScoredTrade` factice avec score 75 (S1+S2+S3+S6) et verifier le format du message recu sur Telegram

**Dependances :** Story 2.3 (ScoredTrade complet requis)

**Risques a surveiller :**
- `parse_mode="HTML"` : les caracteres `<`, `>`, `&` dans le titre du marche doivent etre echappes (`&lt;`, `&gt;`, `&amp;`) sinon Telegram retourne une erreur 400
- Le champ `wallet_short` doit etre formatte `{address[:6]}...{address[-4:]}` — verifier avec une vraie adresse Ethereum (42 caracteres avec `0x`)
- `age_days` peut etre `None` si `first_polymarket_trade_ts == -1` — afficher "inconnu" dans ce cas

---

### Story 3.2 — main.py : orchestration complete

**Titre :** En tant que developpeur, je veux que main.py orchestre le pipeline complet (fetch -> filter -> enrich -> score -> alert) pour qu'un seul `python -m src.main` execute le bot de bout en bout.

**Estimation :** M (demi-journee)

**Description :**
Implementer `main.py` en connectant tous les modules. Tester le pipeline complet en local sur un marche Polymarket actif.

**Fichiers a creer/modifier :**
- `src/main.py` — implementation complete (~80 lignes)

**Criteres d'acceptance :**
- [ ] `python -m src.main` s'execute sans erreur avec un `.env` valide (vrais tokens Telegram)
- [ ] Les logs produits suivent exactement le format defini dans le PRD F7 : `[RUN START]`, `[FETCH]`, `[FILTER]`, `[ANALYSIS]`, `[SCORE]`, `[ALERT]`, `[RUN END]`
- [ ] Le log `[CONFIG]` indique le seuil et la fenetre temporelle utilises
- [ ] Si 0 trade dans les 30 dernieres minutes, le run se termine avec log `[RUN END]` et exit 0
- [ ] Si exception non geree (bug), le run se termine avec `[CRITICAL]` et exit 1
- [ ] Le log `[SCORE]` pour chaque wallet affiche le score et les signaux actifs (ex: `S1+S3+S6`)
- [ ] Le run se termine en moins de 5 minutes sur un marche actif (mesure du `[RUN END]`)
- [ ] Test de bout en bout : simuler un `ScoredTrade` avec score >= `ALERT_SCORE_THRESHOLD` et verifier qu'exactement 1 message Telegram est envoye
- [ ] Test de bout en bout : si tous les scores < seuil, 0 message Telegram envoye

**Dependances :** Story 3.1 (tous les modules implementes)

**Risques a surveiller :**
- Import `datetime` manquant dans `main.py` (le code de l'architecture l'utilise sans l'importer explicitement)
- La session `requests.Session` doit etre partagee entre `fetcher` et `alerter` — ne pas creer une nouvelle session dans `alerter.py`
- `wallet_profiles` est un `dict` — si un wallet est absent (historique indisponible), `wallet_profiles.get(trade.proxy_wallet)` retourne `None` : le trade est silencieusement ignore (comportement souhaite, a verifier)

---

## Epic 4 — Tests et deploiement

**Objectif :** Garantir la couverture de tests et deployer le bot sur GitHub Actions avec le scheduling cron.

**Definition of Done epic :** Le workflow GitHub Actions s'execute automatiquement toutes les 30 minutes, les tests passent en CI, et le premier run complet en production est observable dans les logs.

---

### Story 4.1 — Tests unitaires complets

**Titre :** En tant que developpeur, je veux une suite de tests unitaires complete pour scorer.py et filters.py afin de pouvoir modifier les seuils et la logique sans regression.

**Estimation :** M (demi-journee)

**Description :**
Consolider et completer les tests unitaires ecrits incrementalement dans les stories precedentes. Ajouter les fixtures partagees dans `tests/conftest.py` (si necessaire). Verifier la couverture.

**Note :** Si les stories 2.1 et 2.3 ont ete faites en TDD, cette story consiste principalement a consolider, nettoyer et verifier la completude des tests. Estimation reduite a S dans ce cas.

**Fichiers a creer/modifier :**
- `tests/test_scorer.py` — 11 cas de test finalises
- `tests/test_filters.py` — 11 cas de test finalises
- `tests/conftest.py` (optionnel) — fixtures partagees (`make_trade`, `make_wallet`, `make_market`, `default_config`)

**Criteres d'acceptance :**
- [ ] `pytest tests/ -v` : 22/22 tests passent (11 scorer + 11 filters)
- [ ] `pytest tests/ --tb=short` : 0 warning, 0 erreur
- [ ] Les fixtures `make_trade`, `make_wallet`, `make_market` acceptent des kwargs pour surcharger les valeurs par defaut (ex: `make_trade(size=200)`)
- [ ] `default_config` est une instance `Settings` avec valeurs par defaut, utilisable sans fichier `.env` (valeurs hardcodees dans la fixture)
- [ ] Tous les cas limites documentes dans l'ARCHITECTURE.md section 12 sont couverts
- [ ] Les tests ne font aucun appel HTTP reel (pas de mock necessaire si scorer/filters sont purement fonctionnels)
- [ ] Chaque test a un nom explicite decrivant le comportement teste (ex: `test_s1_returns_false_when_timestamp_unknown`)

**Dependances :** Stories 2.1 et 2.3 (scorer.py et filters.py implementes)

**Risques a surveiller :**
- `Settings()` dans les fixtures necessite des variables d'environnement Telegram — utiliser `Settings(telegram_bot_token="test", telegram_chat_id="123")` pour instancier sans `.env`
- Attention au comportement de `pydantic-settings` qui lit le `.env` reel si present — utiliser `model_config = SettingsConfigDict(env_file=None)` ou `_env_file=None` dans les fixtures

---

### Story 4.2 — GitHub Actions et deploiement

**Titre :** En tant que developpeur, je veux que le bot se declenche automatiquement toutes les 30 minutes sur GitHub Actions pour ne pas avoir a le lancer manuellement.

**Estimation :** M (demi-journee)

**Description :**
Creer le workflow GitHub Actions, configurer les GitHub Secrets et Variables, pousser le code sur le repo public, et valider le premier run automatique.

**Fichiers a creer/modifier :**
- `.github/workflows/schedule.yml` — workflow complet (tel que defini dans ARCHITECTURE.md section 13)

**Criteres d'acceptance :**
- [ ] Le fichier `.github/workflows/schedule.yml` est valide YAML (verifier avec `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/schedule.yml'))"`)
- [ ] Le workflow contient `on: schedule: - cron: '*/30 * * * *'` ET `on: workflow_dispatch`
- [ ] `timeout-minutes: 10` est configure dans le job `scan`
- [ ] Les 2 GitHub Secrets sont configures : `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- [ ] Les 10 GitHub Variables sont configurees avec leurs valeurs par defaut
- [ ] Le declenchement manuel (`workflow_dispatch`) dans l'onglet Actions s'execute avec succes
- [ ] Le log du premier run affiche les 6 etapes de log (`[RUN START]` a `[RUN END]`)
- [ ] Le run se termine avec status "success" (pas "failed")
- [ ] Un second declenchement manuel 30 secondes apres le premier confirme le comportement reproductible
- [ ] Les tests s'executent sur `workflow_dispatch` et passent (step conditionnel `if: github.event_name == 'workflow_dispatch'`)
- [ ] Aucun secret n'est present dans le code source commite (verifier avec `git log --all -p | grep -i "token\|secret\|password"`)

**Dependances :** Story 3.2 (main.py fonctionnel, tous les modules implementes)

**Risques a surveiller :**
- Le cron GitHub Actions peut avoir un delai de 5 a 15 minutes par rapport a l'heure prevue (comportement normal, documenté dans la doc GitHub)
- `python -m src.main` necessite que le `PYTHONPATH` soit a la racine du projet — verifier que le workflow `cd` vers la racine ou que le `run` utilise le bon repertoire de travail
- Le `.env` ne doit jamais etre commite — double-verifier `.gitignore` avant le premier push
- Sur un repo public, les GitHub Actions sont gratuites et illimitees — verifier que le repo est bien public avant de compter sur ce mecanisme

---

## Ordre d'implementation recommande

```
Story 1.1  (S)  →  Story 1.2  (M)  →  Story 2.1  (M)
                                              |
                                       Story 2.2  (M)
                                              |
                                       Story 2.3  (M)
                                              |
                                       Story 3.1  (S)
                                              |
                                       Story 3.2  (M)
                                              |
                         Story 4.1  (M) ←─────┘
                                |
                         Story 4.2  (M)
```

**Point de decision apres Story 1.2 :** Si Q1 (API `/trades` sans `user=`) n'est pas valide, stopper et redefinir l'approche de fetch avant de continuer les stories 2.x.

---

## Variables d'environnement — recapitulatif complet

| Variable | Type | Obligatoire | Defaut | Secret |
|----------|------|-------------|--------|--------|
| `TELEGRAM_BOT_TOKEN` | str | OUI | — | OUI (GitHub Secret) |
| `TELEGRAM_CHAT_ID` | str | OUI | — | OUI (GitHub Secret) |
| `ALERT_SCORE_THRESHOLD` | int | non | 60 | non (GitHub Variable) |
| `MIN_BET_USDC` | float | non | 500 | non |
| `WALLET_AGE_DAYS_MAX` | int | non | 7 | non |
| `WALLET_TX_MIN` | int | non | 10 | non |
| `ORDER_BOOK_IMPACT_PCT` | float | non | 2.0 | non |
| `BET_VS_HISTORY_MULTIPLIER` | float | non | 3.0 | non |
| `CONCENTRATION_PCT` | float | non | 60.0 | non |
| `TIMING_HOURS_BEFORE` | int | non | 4 | non |
| `BOT_MARKET_COUNT_MAX` | int | non | 50 | non |
| `LOOKBACK_MINUTES` | int | non | 30 | non |
| `POLYGON_RPC_URL` | str | non | https://polygon-rpc.com | non |

---

## Strategie de test globale

### Tests unitaires (automatises, framework : pytest + pytest-mock)

| Module | Fichier de test | Nb de tests | Approche |
|--------|----------------|-------------|----------|
| `scorer.py` | `tests/test_scorer.py` | 11 | TDD recommande — ecrire les tests avant le code |
| `filters.py` | `tests/test_filters.py` | 11 | TDD recommande — ecrire les tests avant le code |

**Cas limites prioritaires :**
- Valeurs sentinelles (`-1` pour `tx_count_polygon`, `-1` pour `first_polymarket_trade_ts`)
- Division par zero (S3 avec `liquidity_usdc == 0`, S5 avec `sum(market_exposures) == 0`)
- Valeurs limite exactes pour les filtres (trade a exactement `MIN_BET_USDC`, wallet a exactement `BOT_MARKET_COUNT_MAX` marches)
- ISO8601 avec fuseau horaire pour S6 (ex: `2026-04-14T20:00:00+00:00`)

### Tests d'integration (manuels, sur API reelle)

| Story | Validation |
|-------|-----------|
| 1.2 | `curl -s "https://data-api.polymarket.com/trades?limit=10"` — verifier structure JSON |
| 2.2 | `fetch_wallet_history()` sur une vraie adresse — verifier `WalletProfile` complet |
| 2.2 | `fetch_market_metadata()` sur un vrai marche — verifier `liquidity_usdc` present |
| 3.1 | Envoyer un message Telegram factice avec `send_alert()` — verifier format sur telephone |
| 3.2 | `python -m src.main` en local — verifier les logs et le comportement de bout en bout |

### Tests manuels de bout en bout (Story 3.2 et 4.2)

| Scenario | Actions | Resultat attendu |
|----------|---------|-----------------|
| Run normal | `python -m src.main` avec vraies credentials | Logs complets, 0 ou N alertes, exit 0 |
| Run sans trade | Vider `lookback_minutes=1` temporairement | Log `[RUN END] Aucun trade` sans alerte |
| Seuil eleve | `ALERT_SCORE_THRESHOLD=100` | Aucune alerte envoyee meme si trades detectes |
| API Telegram down | Token invalide | Log `[ALERT ERROR]`, exit 0, pas de crash |
| workflow_dispatch | Bouton "Run workflow" dans GitHub Actions | Run visible dans onglet Actions, status success |

---

## Conflits PRD non resolus

Aucun conflit PRD non resolu. L'ARCHITECTURE.md a explicitement documente et resolu les 4 questions ouvertes du PRD (Q1-Q4) et les conflits potentiels (D1-D5).

**Seul point d'attention :** Q1 (format exact de la Data API) est resolu par une strategie d'adaptation cote client, mais doit etre valide empiriquement en Story 1.2. Ce n'est pas un bloquant mais un point de verification.
