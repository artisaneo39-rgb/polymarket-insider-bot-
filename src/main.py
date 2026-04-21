# src/main.py
import logging
import sys
import time
import requests
from datetime import datetime, timezone

from src.config import get_settings
from src import fetcher, filters, scorer, alerter
from src import tracker as pnl_tracker
from src.models import WalletProfile


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )


def _active_signals_str(st) -> str:
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


def main() -> None:
    run_start = time.time()
    setup_logging()

    logging.info(f"[RUN START] {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")

    # 1. Config
    cfg = get_settings()
    logging.info(f"[CONFIG] seuil={cfg.alert_score_threshold}, fenetre={cfg.lookback_minutes}min, min_bet={cfg.min_bet_usdc} USDC")

    # 2. Session HTTP réutilisée pour tous les appels
    session = requests.Session()
    session.headers.update({"User-Agent": "polymarket-insider-bot/1.0"})
    gamma_cache: dict = {}

    # Charger le tracker PnL (optionnel — ignoré si GIST_ID non configuré)
    tracker_data = pnl_tracker.load_tracker(cfg.gist_id, cfg.gist_token, session)
    if tracker_data is not None and cfg.gist_id:
        tracker_data = pnl_tracker.check_resolutions(tracker_data, session)
        summary = pnl_tracker.compute_summary(tracker_data)
        logging.info(f"[TRACKER] {summary['nb_alerts']} alertes | {summary['nb_resolved']} resolues | win rate: {summary['win_rate_pct']}% | PnL simule: {summary['total_pnl_usdc']:+.0f} USDC")

    # Heartbeat quotidien (08:00-08:30 UTC)
    alerter.send_heartbeat(cfg, session)

    # 3. Fetch des trades récents
    trades = fetcher.fetch_recent_trades(session, cfg.lookback_minutes)
    logging.info(f"[FETCH] {len(trades)} trades dans les {cfg.lookback_minutes} dernieres minutes")

    if not trades:
        logging.info(f"[RUN END] Aucun trade recent, duree: {time.time() - run_start:.1f}s")
        return

    # 4. Filtres anti-bruit
    filtered_trades = filters.apply_trade_filters(trades, cfg)
    logging.info(f"[FILTER] {len(trades) - len(filtered_trades)} trades exclus, {len(filtered_trades)} retenus")

    if not filtered_trades:
        logging.info(f"[RUN END] Aucun trade apres filtrage, duree: {time.time() - run_start:.1f}s")
        return

    # 5. Enrichissement : wallet profiles
    unique_wallets = list({t.proxy_wallet for t in filtered_trades})
    logging.info(f"[ANALYSIS] {len(unique_wallets)} wallets uniques a analyser")

    wallet_profiles: dict = {}
    for wallet_address in unique_wallets:
        profile = fetcher.fetch_wallet_history(wallet_address, session)
        if profile is None:
            logging.warning(f"[FETCH] wallet {wallet_address[:8]}... historique indisponible, ignore")
            continue
        if filters.is_blacklisted(wallet_address, cfg):
            continue
        if filters.is_arb_bot(profile, cfg):
            continue
        wallet_profiles[wallet_address] = profile

    # 6. Scoring
    scored_trades = []
    for trade in filtered_trades:
        wallet = wallet_profiles.get(trade.proxy_wallet)
        if wallet is None:
            continue
        market = fetcher.fetch_market_metadata(trade.condition_id, session, gamma_cache)
        if market is None:
            logging.warning(f"[FETCH] marche {trade.condition_id[:8]}... metadonnees indisponibles, ignore")
            continue
        if filters.is_noise_market(market, cfg):
            continue
        scored = scorer.score_trade(trade, wallet, market, cfg)
        logging.info(f"[SCORE] {trade.proxy_wallet[:8]}... score {scored.signals.score}/100 [{_active_signals_str(scored)}]")
        scored_trades.append(scored)

    # 7. Alertes
    alerts_sent = 0
    for st in scored_trades:
        if st.signals.score >= cfg.alert_score_threshold:
            success = alerter.send_alert(st, cfg, session)
            if success:
                alerts_sent += 1
            if success and cfg.gist_id:
                tracker_data = pnl_tracker.record_alert(tracker_data, st)

    logging.info(f"[ALERT] {alerts_sent} alerte(s) envoyee(s) (seuil: {cfg.alert_score_threshold})")

    # Sauvegarder le tracker si modifié
    if cfg.gist_id and tracker_data:
        pnl_tracker.save_tracker(tracker_data, cfg.gist_id, cfg.gist_token, session)

    logging.info(f"[RUN END] duree: {time.time() - run_start:.1f}s")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.critical(f"[CRITICAL] Erreur non anticipee: {e}", exc_info=True)
        sys.exit(1)
