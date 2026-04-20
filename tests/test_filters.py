import pytest
from src.models import Trade, WalletProfile
from src.config import Settings
from src.filters import apply_trade_filters, is_arb_bot


# --- Tests apply_trade_filters ---

def test_tc_f_01_excludes_micro_amount(cfg, make_trade):
    """TC-F-01 : Trade size < 1 USDC -> exclu (wash trading)"""
    trade = make_trade(size=0.5)
    result = apply_trade_filters([trade], cfg)
    assert result == []


def test_tc_f_02_excludes_below_min_bet(cfg, make_trade):
    """TC-F-02 : Trade size < MIN_BET_USDC (499 USDC) -> exclu"""
    trade = make_trade(size=499.0)
    result = apply_trade_filters([trade], cfg)
    assert result == []


def test_tc_f_03_retains_exact_min_bet(cfg, make_trade):
    """TC-F-03 : Trade size == MIN_BET_USDC (500 USDC) -> retenu"""
    trade = make_trade(size=500.0)
    result = apply_trade_filters([trade], cfg)
    assert len(result) == 1


def test_tc_f_04_retains_above_min_bet(cfg, make_trade):
    """TC-F-04 : Trade size > MIN_BET_USDC (1000 USDC) -> retenu"""
    trade = make_trade(size=1000.0)
    result = apply_trade_filters([trade], cfg)
    assert len(result) == 1


def test_tc_f_05_excludes_wash_trade_roundtrip_same_hour(cfg, make_trade):
    """TC-F-05 : Wallet avec YES @ t=0 et NO @ t=1800 sur même marché dans même heure -> exclu"""
    base_ts = 1776193200  # début de l'heure 493387 (1776193200 // 3600 = 493387)
    trade_yes = make_trade(
        proxy_wallet="0xwasher",
        condition_id="0xmarket1",
        outcome="Yes",
        side="BUY",
        timestamp=base_ts,
        transaction_hash="0xtx1",
    )
    trade_no = make_trade(
        proxy_wallet="0xwasher",
        condition_id="0xmarket1",
        outcome="No",
        side="BUY",
        timestamp=base_ts + 1800,  # même heure
        transaction_hash="0xtx2",
    )
    result = apply_trade_filters([trade_yes, trade_no], cfg)
    assert result == []


def test_tc_f_06_retains_opposite_sides_different_hours(cfg, make_trade):
    """TC-F-06 : Wallet avec YES @ t=0 et NO @ t=7200 (heures différentes) -> retenu"""
    base_ts = 1776196000
    trade_yes = make_trade(
        proxy_wallet="0xwallet",
        condition_id="0xmarket1",
        outcome="Yes",
        side="BUY",
        timestamp=base_ts,
        transaction_hash="0xtx1",
    )
    trade_no = make_trade(
        proxy_wallet="0xwallet",
        condition_id="0xmarket1",
        outcome="No",
        side="BUY",
        timestamp=base_ts + 7200,  # heure différente
        transaction_hash="0xtx2",
    )
    result = apply_trade_filters([trade_yes, trade_no], cfg)
    assert len(result) == 2


def test_tc_f_07_retains_opposite_outcomes_different_markets_same_hour(cfg, make_trade):
    """TC-F-07 : Wallet avec Yes sur marché A et No sur marché B dans la même heure -> retenu"""
    base_ts = 1776196000
    trade_a = make_trade(
        proxy_wallet="0xwallet",
        condition_id="0xmarketA",
        outcome="Yes",
        timestamp=base_ts,
        transaction_hash="0xtx1",
    )
    trade_b = make_trade(
        proxy_wallet="0xwallet",
        condition_id="0xmarketB",
        outcome="No",
        timestamp=base_ts + 100,
        transaction_hash="0xtx2",
    )
    result = apply_trade_filters([trade_a, trade_b], cfg)
    assert len(result) == 2


def test_tc_f_10_empty_list(cfg):
    """TC-F-10 : Liste vide en entrée -> liste vide en sortie"""
    result = apply_trade_filters([], cfg)
    assert result == []


def test_tc_f_11_all_filtered(cfg, make_trade):
    """TC-F-11 : Tous les trades filtrés -> liste vide"""
    trades = [make_trade(size=0.1), make_trade(size=0.5, transaction_hash="0xtx2")]
    result = apply_trade_filters(trades, cfg)
    assert result == []


# --- Tests is_arb_bot ---

def test_tc_f_08_arb_bot_above_threshold(cfg, make_wallet):
    """TC-F-08 : Wallet actif sur > BOT_MARKET_COUNT_MAX marchés -> is_arb_bot == True"""
    wallet = make_wallet(active_market_count=51)
    assert is_arb_bot(wallet, cfg) is True


def test_tc_f_09_arb_bot_at_threshold(cfg, make_wallet):
    """TC-F-09 : Wallet actif sur == BOT_MARKET_COUNT_MAX marchés -> is_arb_bot == False"""
    wallet = make_wallet(active_market_count=50)
    assert is_arb_bot(wallet, cfg) is False
