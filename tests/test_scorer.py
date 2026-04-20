# tests/test_scorer.py
import pytest
from datetime import datetime, timezone, timedelta
from src.models import Trade, WalletProfile, MarketMetadata, SignalResult, ScoredTrade
from src.config import Settings
from src.scorer import score_trade


# --- Tests ---

def test_tc_s_01_all_signals_active_score_100(cfg, make_trade, make_wallet, make_market):
    """TC-S-01 : Tous les signaux activés -> score == 100"""
    now = int(datetime.now(timezone.utc).timestamp())
    # S1 : wallet frais (3 jours)
    # S2 : peu de txs (5)
    # S3 : impact élevé (trade 5000 sur liquidité 10000 = 50%)
    # S4 : mise énorme vs historique (10x)
    # S5 : concentration 100% sur un marché
    # S6 : résolution dans 2h
    resolution = datetime.now(timezone.utc) + timedelta(hours=2)
    trade = make_trade(size=5000.0)
    wallet = make_wallet(
        tx_count_polygon=5,
        first_polymarket_trade_ts=now - (3 * 86400),
        avg_bet_usdc=100.0,
        market_exposures={"0xmarket1": 5000.0},  # 100% concentration
    )
    market = make_market(
        liquidity_usdc=10000.0,
        end_date_iso=resolution.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    result = score_trade(trade, wallet, market, cfg)
    assert result.signals.score == 100


def test_tc_s_02_standard_wallet_low_score(cfg, make_trade, make_wallet, make_market):
    """TC-S-02 : Wallet standard -> score <= 20"""
    now = int(datetime.now(timezone.utc).timestamp())
    trade = make_trade(size=500.0)
    wallet = make_wallet(
        tx_count_polygon=200,
        first_polymarket_trade_ts=now - (60 * 86400),  # 60 jours
        avg_bet_usdc=400.0,  # mise habituelle proche de la mise actuelle
        active_market_count=10,
        market_exposures={"0xmarket1": 500.0, "0xmarket2": 500.0,
                          "0xmarket3": 500.0, "0xmarket4": 500.0},
    )
    market = make_market(
        liquidity_usdc=500000.0,  # très grande liquidité -> impact faible
        end_date_iso=None,  # S6 non activé
    )
    result = score_trade(trade, wallet, market, cfg)
    assert result.signals.score <= 20


def test_tc_s_03_s1_false_when_timestamp_unknown(cfg, make_trade, make_wallet, make_market):
    """TC-S-03 : first_polymarket_trade_ts == -1 -> s1 == False"""
    trade = make_trade()
    wallet = make_wallet(first_polymarket_trade_ts=-1)
    market = make_market()
    result = score_trade(trade, wallet, market, cfg)
    assert result.signals.s1_fresh_wallet is False


def test_tc_s_04_s2_false_when_tx_count_unknown(cfg, make_trade, make_wallet, make_market):
    """TC-S-04 : tx_count_polygon == -1 -> s2 == False"""
    trade = make_trade()
    wallet = make_wallet(tx_count_polygon=-1)
    market = make_market()
    result = score_trade(trade, wallet, market, cfg)
    assert result.signals.s2_low_history is False


def test_tc_s_05_s3_false_when_liquidity_zero(cfg, make_trade, make_wallet, make_market):
    """TC-S-05 : liquidity_usdc == 0 -> s3 == False"""
    trade = make_trade(size=1000.0)
    wallet = make_wallet()
    market = make_market(liquidity_usdc=0.0)
    result = score_trade(trade, wallet, market, cfg)
    assert result.signals.s3_order_book_impact is False


def test_tc_s_06_s4_true_when_first_trade(cfg, make_trade, make_wallet, make_market):
    """TC-S-06 : avg_bet_usdc == 0 (premier trade) -> s4 == True"""
    trade = make_trade(size=500.0)
    wallet = make_wallet(avg_bet_usdc=0.0)
    market = make_market()
    result = score_trade(trade, wallet, market, cfg)
    assert result.signals.s4_bet_vs_history is True


def test_tc_s_07_s6_false_when_no_end_date(cfg, make_trade, make_wallet, make_market):
    """TC-S-07 : end_date_iso == None -> s6 == False"""
    trade = make_trade()
    wallet = make_wallet()
    market = make_market(end_date_iso=None)
    result = score_trade(trade, wallet, market, cfg)
    assert result.signals.s6_timing is False


def test_tc_s_08_s6_false_when_resolution_far(cfg, make_trade, make_wallet, make_market):
    """TC-S-08 : résolution dans 6h (> TIMING_HOURS_BEFORE=4h) -> s6 == False"""
    resolution = datetime.now(timezone.utc) + timedelta(hours=6)
    trade = make_trade()
    wallet = make_wallet()
    market = make_market(end_date_iso=resolution.strftime("%Y-%m-%dT%H:%M:%SZ"))
    result = score_trade(trade, wallet, market, cfg)
    assert result.signals.s6_timing is False


def test_tc_s_09_s6_true_when_resolution_near(cfg, make_trade, make_wallet, make_market):
    """TC-S-09 : résolution dans 2h (< TIMING_HOURS_BEFORE=4h) -> s6 == True"""
    resolution = datetime.now(timezone.utc) + timedelta(hours=2)
    trade = make_trade()
    wallet = make_wallet()
    market = make_market(end_date_iso=resolution.strftime("%Y-%m-%dT%H:%M:%SZ"))
    result = score_trade(trade, wallet, market, cfg)
    assert result.signals.s6_timing is True


def test_tc_s_10_score_equals_sum_of_active_weights(cfg, make_trade, make_wallet, make_market):
    """TC-S-10 : score == somme exacte des poids des signaux actifs"""
    now = int(datetime.now(timezone.utc).timestamp())
    # Activer uniquement S1 (20pts) et S3 (20pts) = 40pts attendus
    trade = make_trade(size=5000.0)  # S3 : 5000/10000 = 50% > 2%
    wallet = make_wallet(
        tx_count_polygon=50,           # S2 non activé (50 >= 10)
        first_polymarket_trade_ts=now - (3 * 86400),  # S1 activé (3j < 7j)
        avg_bet_usdc=4000.0,          # S4 non activé (5000 < 3*4000)
        market_exposures={"0xmarket1": 5000.0, "0xmarket2": 5000.0},  # S5 : 50% < 60%
    )
    market = make_market(
        liquidity_usdc=10000.0,
        end_date_iso=None,             # S6 non activé
    )
    result = score_trade(trade, wallet, market, cfg)
    assert result.signals.s1_fresh_wallet is True
    assert result.signals.s3_order_book_impact is True
    assert result.signals.s2_low_history is False
    assert result.signals.s4_bet_vs_history is False
    assert result.signals.s5_concentration is False
    assert result.signals.s6_timing is False
    assert result.signals.score == 40  # 20 + 20


def test_tc_s_11_score_independent_of_threshold(cfg, make_trade, make_wallet, make_market):
    """TC-S-11 : changer alert_score_threshold ne change pas le score"""
    trade = make_trade()
    wallet = make_wallet()
    market = make_market()

    result1 = score_trade(trade, wallet, market, cfg)

    cfg_high = Settings(
        telegram_bot_token="test",
        telegram_chat_id="123",
        alert_score_threshold=80,
    )
    result2 = score_trade(trade, wallet, market, cfg_high)

    assert result1.signals.score == result2.signals.score
