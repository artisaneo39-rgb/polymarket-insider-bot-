# src/tracker.py
"""
PnL tracker via GitHub Gist.
Stocke les alertes envoyées et calcule le PnL simulé après résolution des marchés.
"""
import json
import logging
import requests
from datetime import datetime, timezone
from src.models import ScoredTrade


GIST_FILENAME = "polymarket_tracker.json"
SIMULATED_BET_USDC = 1000.0  # Mise simulée par alerte pour le calcul PnL


def load_tracker(gist_id: str, github_token: str, session: requests.Session) -> dict:
    """
    Charge le fichier JSON depuis le Gist GitHub.
    Retourne un dict vide si le Gist est inaccessible ou vide.
    """
    if not gist_id or not github_token:
        return {}
    try:
        headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github+json"}
        r = session.get(f"https://api.github.com/gists/{gist_id}", headers=headers, timeout=10)
        if r.status_code != 200:
            logging.warning(f"[TRACKER] Impossible de charger le Gist: {r.status_code}")
            return {}
        files = r.json().get("files", {})
        if GIST_FILENAME not in files:
            return {}
        content = files[GIST_FILENAME].get("content", "{}")
        return json.loads(content)
    except Exception as e:
        logging.warning(f"[TRACKER] Erreur chargement Gist: {e}")
        return {}


def save_tracker(data: dict, gist_id: str, github_token: str, session: requests.Session) -> bool:
    """
    Sauvegarde le dict JSON dans le Gist GitHub.
    Retourne True si succès.
    """
    if not gist_id or not github_token:
        return False
    try:
        headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github+json"}
        payload = {"files": {GIST_FILENAME: {"content": json.dumps(data, indent=2, ensure_ascii=False)}}}
        r = session.patch(f"https://api.github.com/gists/{gist_id}", headers=headers, json=payload, timeout=10)
        if r.status_code == 200:
            logging.info("[TRACKER] Gist mis a jour avec succes")
            return True
        logging.warning(f"[TRACKER] Erreur sauvegarde Gist: {r.status_code}")
        return False
    except Exception as e:
        logging.warning(f"[TRACKER] Erreur sauvegarde Gist: {e}")
        return False


def record_alert(tracker_data: dict, scored_trade: ScoredTrade) -> dict:
    """
    Enregistre une nouvelle alerte dans le tracker.
    Clé = condition_id du marché (une entrée par marché).
    """
    condition_id = scored_trade.trade.condition_id
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    entry = {
        "date": now,
        "wallet": scored_trade.trade.proxy_wallet,
        "market": scored_trade.market.question,
        "market_slug": scored_trade.market.slug,
        "outcome_taken": scored_trade.trade.outcome,
        "price_at_alert": round(scored_trade.trade.price, 4),
        "score": scored_trade.signals.score,
        "end_date": scored_trade.market.end_date_iso,
        "resolved": False,
        "winning_outcome": None,
        "won": None,
        "pnl_usdc": None,
    }

    if condition_id not in tracker_data:
        tracker_data[condition_id] = []

    # Éviter les doublons (même wallet + même marché dans les 24h)
    existing = tracker_data[condition_id]
    for e in existing:
        if e.get("wallet") == scored_trade.trade.proxy_wallet and not e.get("resolved"):
            logging.info(f"[TRACKER] Alerte deja enregistree pour {condition_id[:8]}... wallet {scored_trade.trade.proxy_wallet[:8]}...")
            return tracker_data

    tracker_data[condition_id].append(entry)
    logging.info(f"[TRACKER] Nouvelle alerte enregistree: {scored_trade.market.question[:50]}")
    return tracker_data


def check_resolutions(tracker_data: dict, session: requests.Session) -> dict:
    """
    Pour chaque alerte non résolue, vérifie si le marché a été résolu via la Gamma API.
    Met à jour won/pnl_usdc si résolu.
    """
    for condition_id, entries in tracker_data.items():
        for entry in entries:
            if entry.get("resolved"):
                continue

            # Vérifier la résolution via Gamma API
            winning_outcome = _get_winning_outcome(condition_id, session)
            if winning_outcome is None:
                continue  # Marché pas encore résolu

            entry["resolved"] = True
            entry["winning_outcome"] = winning_outcome

            # Calculer le PnL simulé
            outcome_taken = entry.get("outcome_taken", "")
            price_at_alert = entry.get("price_at_alert", 0.5)

            won = outcome_taken.lower() == winning_outcome.lower()
            entry["won"] = won

            if won:
                # Gain = (1 / price_at_alert) * mise - mise
                entry["pnl_usdc"] = round((SIMULATED_BET_USDC / price_at_alert) - SIMULATED_BET_USDC, 2)
            else:
                entry["pnl_usdc"] = -SIMULATED_BET_USDC

            result_str = "GAGNE" if won else "PERDU"
            logging.info(f"[TRACKER] Marche resolu: {entry['market'][:50]} — {result_str} | PnL: {entry['pnl_usdc']:+.0f} USDC")

    return tracker_data


def compute_summary(tracker_data: dict) -> dict:
    """
    Calcule les statistiques globales du tracker.
    Retourne un dict avec win_rate, total_pnl, nb_alerts, nb_resolved.
    """
    all_entries = [e for entries in tracker_data.values() for e in entries]
    resolved = [e for e in all_entries if e.get("resolved")]
    won = [e for e in resolved if e.get("won")]

    total_pnl = sum(e.get("pnl_usdc", 0) for e in resolved if e.get("pnl_usdc") is not None)
    win_rate = len(won) / len(resolved) * 100 if resolved else 0

    return {
        "nb_alerts": len(all_entries),
        "nb_resolved": len(resolved),
        "nb_won": len(won),
        "win_rate_pct": round(win_rate, 1),
        "total_pnl_usdc": round(total_pnl, 2),
        "simulated_bet_per_alert": SIMULATED_BET_USDC,
    }


def _get_winning_outcome(condition_id: str, session: requests.Session) -> str | None:
    """
    Interroge la Gamma API pour savoir si un marché est résolu et quel outcome a gagné.
    Retourne le label de l'outcome gagnant, ou None si pas encore résolu.
    """
    try:
        url = "https://gamma-api.polymarket.com/markets"
        params = {"conditionIds": condition_id}
        r = session.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json()
        items = data if isinstance(data, list) else data.get("data", [])
        if not items:
            return None
        market = items[0]

        # Vérifier si le marché est résolu
        if not market.get("closed") and not market.get("resolved"):
            return None

        # Trouver l'outcome gagnant
        # La Gamma API expose "outcomes" (liste) et "outcomePrices" (liste de prix finaux)
        outcomes = market.get("outcomes")
        outcome_prices = market.get("outcomePrices")

        if outcomes and outcome_prices:
            try:
                if isinstance(outcomes, str):
                    outcomes = json.loads(outcomes)
                if isinstance(outcome_prices, str):
                    outcome_prices = json.loads(outcome_prices)
                prices = [float(p) for p in outcome_prices]
                winner_idx = prices.index(max(prices))
                return outcomes[winner_idx]
            except (ValueError, IndexError, json.JSONDecodeError):
                pass

        # Fallback : champ "winner" ou "winnerOutcome"
        return market.get("winner") or market.get("winnerOutcome")

    except Exception as e:
        logging.warning(f"[TRACKER] Erreur check resolution {condition_id[:8]}...: {e}")
        return None
