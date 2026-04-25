# src/alerter.py
import logging
import requests
from datetime import datetime, timezone
from src.models import ScoredTrade
from src.config import Settings

SIGNAL_LABELS = {
    "s1_fresh_wallet": ("Wallet frais", 20),
    "s2_low_history": ("Peu de transactions Polygon", 15),
    "s3_order_book_impact": ("Impact order book", 20),
    "s4_bet_vs_history": ("Mise anormale vs historique", 15),
    "s5_concentration": ("Concentration marche unique", 15),
    "s6_timing": ("Timing pre-resolution", 15),
}


def send_alert(scored_trade: ScoredTrade, cfg: Settings, session: requests.Session) -> bool:
    """
    Formate et envoie un message Telegram pour un trade suspect.
    Retourne True si envoi réussi, False sinon.
    """
    text = format_message(scored_trade)
    success = _post_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, text, session)
    if success:
        logging.info(f"[ALERT] Message envoye pour {scored_trade.trade.proxy_wallet[:8]}... score {scored_trade.signals.score}/100")
    else:
        logging.error(f"[ALERT ERROR] Echec envoi Telegram pour {scored_trade.trade.proxy_wallet[:8]}...")
    return success


def format_message(scored_trade: ScoredTrade) -> str:
    """
    Construit le texte du message Telegram.
    Aucune valeur 'None' dans le message final.
    """
    t = scored_trade.trade
    w = scored_trade.wallet
    m = scored_trade.market
    s = scored_trade.signals

    # Adresse wallet tronquée
    wallet_short = f"{t.proxy_wallet[:6]}...{t.proxy_wallet[-4:]}" if len(t.proxy_wallet) > 10 else t.proxy_wallet

    # Âge du wallet
    age_str = s.details.get("s1_age_days", "inconnu")
    if age_str not in ("inconnu", "erreur"):
        try:
            age_str = f"{float(age_str):.0f}j"
        except ValueError:
            age_str = "inconnu"

    # Nombre de transactions
    tx_str = s.details.get("s2_tx_count", "inconnu")

    # Impact order book
    impact_str = s.details.get("s3_impact_pct", "inconnu")
    if impact_str not in ("inconnu", "erreur"):
        impact_str = f"{impact_str}%"

    # Prix en pourcentage
    price_pct = f"{t.price:.0%}"

    # Nom du marché (échapper les caractères HTML)
    question = (m.question or "Marche inconnu").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # Lignes de signaux actifs
    signal_lines = []
    signal_map = [
        ("s1_fresh_wallet", s.s1_fresh_wallet),
        ("s2_low_history", s.s2_low_history),
        ("s3_order_book_impact", s.s3_order_book_impact),
        ("s4_bet_vs_history", s.s4_bet_vs_history),
        ("s5_concentration", s.s5_concentration),
        ("s6_timing", s.s6_timing),
    ]
    for key, active in signal_map:
        if active:
            label, weight = SIGNAL_LABELS[key]
            detail = ""
            if key == "s1_fresh_wallet":
                detail = f" ({age_str})"
            elif key == "s2_low_history":
                detail = f" ({tx_str} txs)"
            elif key == "s3_order_book_impact":
                detail = f" ({impact_str})"
            elif key == "s4_bet_vs_history":
                mult = s.details.get("s4_multiplier", "")
                detail = f" ({mult})"
            elif key == "s5_concentration":
                conc = s.details.get("s5_concentration_pct", "")
                detail = f" ({conc}%)" if conc else ""
            elif key == "s6_timing":
                hours = s.details.get("s6_hours_before", "")
                detail = f" ({hours}h avant resolution)" if hours else ""
            signal_lines.append(f"[+{weight}] {label}{detail}")

    signals_text = "\n".join(signal_lines) if signal_lines else "Aucun signal actif"

    # Lien polymarket
    slug = m.slug or t.condition_id[:16]
    link = f"https://polymarket.com/event/{slug}"

    message = (
        f"ALERTE INSIDER — Score {s.score}/100\n"
        f"\n"
        f"Marche : \"{question}\"\n"
        f"Wallet : <code>{wallet_short}</code> (age: {age_str}, {tx_str} txs)\n"
        f"Mise : {t.size:.0f} USDC sur {t.outcome} @ {price_pct}\n"
        f"Impact liquidite : {impact_str} de l'order book\n"
        f"\n"
        f"Signaux detectes :\n"
        f"{signals_text}\n"
        f"\n"
        f"Cote actuelle : {price_pct}\n"
        f"Lien : {link}"
    )

    return message


def send_heartbeat(cfg: Settings, session: requests.Session) -> bool:
    """
    Envoie un message de statut quotidien entre 08:00 et 08:59 UTC.
    Retourne True si envoi, False si hors fenêtre horaire.
    """
    now = datetime.now(timezone.utc)
    if now.hour == 8:
        text = (
            f"Bot actif — {now.strftime('%Y-%m-%d %H:%M')} UTC\n"
            f"Seuil: {cfg.alert_score_threshold}/100 | "
            f"Fenetre: {cfg.lookback_minutes}min | "
            f"Min bet: {cfg.min_bet_usdc:.0f} USDC"
        )
        success = _post_telegram(cfg.telegram_bot_token, cfg.telegram_chat_id, text, session)
        if success:
            logging.info("[HEARTBEAT] Message de statut quotidien envoye")
        return success
    return False


def _post_telegram(token: str, chat_id: str, text: str, session: requests.Session) -> bool:
    """
    POST https://api.telegram.org/bot{token}/sendMessage
    parse_mode="HTML" pour <code> sur l'adresse wallet.
    Timeout 10s, 1 retry en cas d'erreur réseau.
    Retourne True si status_code == 200, False sinon.
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    for attempt in range(2):
        try:
            r = session.post(url, json=payload, timeout=10)
            if r.status_code == 200:
                return True
            logging.warning(f"[TELEGRAM] status {r.status_code}: {r.text[:200]}")
            return False
        except requests.RequestException as e:
            if attempt == 1:
                logging.error(f"[TELEGRAM ERROR] {e}")
                return False
    return False
