from __future__ import annotations



 



import json

import math

import os

import re

import sqlite3

import time

import uuid

import traceback

import pandas_market_calendars as mcal

import logging

import zipfile

from logging.handlers import RotatingFileHandler

from contextlib import contextmanager

from datetime import datetime, timedelta

from typing import Any, Dict, Iterable, List, Optional, Tuple

from zoneinfo import ZoneInfo

 



import pandas as pd



import requests



 



# -----------------------------------------------------------------------------



# CONFIG



# -----------------------------------------------------------------------------



 



DATA_DIR = os.getenv("DATA_DIR", "/data")



os.makedirs(DATA_DIR, exist_ok=True)



 



DB_FILE = os.getenv("DB_FILE", os.path.join(DATA_DIR, "bot_state.sqlite3"))



 



# Legacy JSON paths are only used for one-time migration if present.



PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")



SIGNALS_FILE = os.path.join(DATA_DIR, "signals.json")



TRADES_FILE = os.path.join(DATA_DIR, "trades.json")



UPDATES_FILE = os.path.join(DATA_DIR, "updates.json")



EQUITY_FILE = os.path.join(DATA_DIR, "equity.json")



HEARTBEAT_FILE = os.path.join(DATA_DIR, "heartbeat.txt")





FMP_API_KEY = os.getenv("FMP_API_KEY")



FMP_BASE = os.getenv(

    "FMP_BASE",

    "https://financialmodelingprep.com/stable"

)



TOKEN = os.getenv("TOKEN")



 



try:



    CHAT_ID = int(os.getenv("CHAT_ID", "0"))



except ValueError as exc:



    raise RuntimeError("CHAT_ID must be an integer") from exc





try:

    SIGNAL_CHANNEL_ID = int(os.getenv("SIGNAL_CHANNEL_ID", "0"))

except ValueError:

    SIGNAL_CHANNEL_ID = 0



PUBLIC_SIGNAL_ENABLED = os.getenv("PUBLIC_SIGNAL_ENABLED", "0").strip() == "1"

PUBLIC_SIGNAL_SILENT = os.getenv("PUBLIC_SIGNAL_SILENT", "0").strip() == "1"



 



if not TOKEN or CHAT_ID == 0:



    raise RuntimeError("Missing TOKEN or CHAT_ID")



 



if not FMP_API_KEY:



    raise RuntimeError("Missing FMP_API_KEY")



 



SESSION = requests.Session()



NY_TZ = ZoneInfo("America/New_York")



 



STRATEGY_VERSION = os.getenv("STRATEGY_VERSION", "v3.8-aggressive-45-15-40-monitor")
INITIAL_CASH = float(os.getenv("INITIAL_CASH", "4000"))

# Risk / execution controls.
MIN_CASH_REQUIRED = float(os.getenv("MIN_CASH_REQUIRED", "100"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "12"))
MAX_TOTAL_RISK = float(os.getenv("MAX_TOTAL_RISK", "0.06"))
MAX_POSITION_EQUITY_PCT = float(os.getenv("MAX_POSITION_EQUITY_PCT", "0.20"))
CASH_USAGE_BUFFER = float(os.getenv("CASH_USAGE_BUFFER", "0.98"))
SIGNAL_COOLDOWN_SEC = int(os.getenv("SIGNAL_COOLDOWN_SEC", str(24 * 3600)))
STOP_COOLDOWN_SEC = int(os.getenv("STOP_COOLDOWN_SEC", "1800"))
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.03"))
MAX_ENTRY_EXTENSION_PCT = float(os.getenv("MAX_ENTRY_EXTENSION_PCT", "0.01"))

# -----------------------------------------------------------------------------
# V2.8 VCP-ONLY LEADER ENGINE + WINNER-CAPTURE UPGRADE
# -----------------------------------------------------------------------------
# Backtest synthesis direction:
# - Pure weak/high-beta and broad pullbacks were rejected.
# - Broad pullbacks, weak names, and ordinary RS breakouts were rejected.
# - The most robust sleeve from testing was leader VCP/contraction breakout.
# - This version keeps the broad liquid leader universe for opportunity,
#   but allows only VCP-style breakout setups by default.
# - Max signals per scan stays at 3 for paper/forward testing.
# - V2.8 keeps v2.7 entries unchanged and only improves winner capture: later
#   breakeven, later/smaller partial, and wider trailing stop.
V2_MIN_MARKET_SCORE = int(os.getenv("V2_MIN_MARKET_SCORE", "5"))
V2_MIN_SCORE = int(os.getenv("V2_MIN_SCORE", "80"))
V2_BREAKOUT_MIN_SCORE = int(os.getenv("V2_BREAKOUT_MIN_SCORE", "999"))
V2_VCP_MIN_SCORE = int(os.getenv("V2_VCP_MIN_SCORE", "86"))
V2_PULLBACK_MIN_SCORE = int(os.getenv("V2_PULLBACK_MIN_SCORE", "999"))
V2_WEAK_MIN_SCORE = int(os.getenv("V2_WEAK_MIN_SCORE", "999"))
V2_MAX_SIGNALS_PER_SCAN = int(os.getenv("V2_MAX_SIGNALS_PER_SCAN", "3"))

# Universe controls. V2.8 is leader-only and VCP-only by default. MEDIUM/WEAK
# buckets and ordinary RS breakouts stay disabled because tests showed those
# sleeves were more cost-sensitive and less robust.
V2_ALLOW_BREAKOUTS = os.getenv("V2_ALLOW_BREAKOUTS", "0") != "0"
V2_ALLOW_VCP = os.getenv("V2_ALLOW_VCP", "1") != "0"
V2_ALLOW_MEDIUM = os.getenv("V2_ALLOW_MEDIUM", "0") != "0"
V2_ALLOW_WEAK = os.getenv("V2_ALLOW_WEAK", "0") != "0"
V2_ALLOW_PULLBACKS = os.getenv("V2_ALLOW_PULLBACKS", "0") != "0"

# Liquidity / volatility filters.
V2_MIN_PRICE = float(os.getenv("V2_MIN_PRICE", "12"))
V2_MIN_AVG_DOLLAR_VOLUME = float(os.getenv("V2_MIN_AVG_DOLLAR_VOLUME", "75000000"))
V2_MIN_ATR_PCT = float(os.getenv("V2_MIN_ATR_PCT", "0.012"))
V2_MAX_ATR_PCT = float(os.getenv("V2_MAX_ATR_PCT", "0.10"))
V2_MAX_RISK_PER_SHARE_PCT = float(os.getenv("V2_MAX_RISK_PER_SHARE_PCT", "0.10"))

# RS breakout behavior. Disabled by default in v2.8, kept only as an env-test option.
V2_MIN_BREAKOUT_VOLUME_RATIO = float(os.getenv("V2_MIN_BREAKOUT_VOLUME_RATIO", "1.25"))
V2_MIN_PULLBACK_VOLUME_RATIO = float(os.getenv("V2_MIN_PULLBACK_VOLUME_RATIO", "999"))
V2_MAX_BREAKOUT_RSI = float(os.getenv("V2_MAX_BREAKOUT_RSI", "79"))
V2_MAX_BREAKOUT_DAY_MOVE_PCT = float(os.getenv("V2_MAX_BREAKOUT_DAY_MOVE_PCT", "6.2"))
V2_MAX_PULLBACK_DAY_MOVE_PCT = float(os.getenv("V2_MAX_PULLBACK_DAY_MOVE_PCT", "0"))

# VCP/contraction breakout behavior. This is the active v2.8 setup family.
V2_VCP_MAX_BASE_RANGE_PCT = float(os.getenv("V2_VCP_MAX_BASE_RANGE_PCT", "0.22"))
V2_VCP_MAX_ATR_RATIO = float(os.getenv("V2_VCP_MAX_ATR_RATIO", "0.88"))
V2_VCP_MAX_BB_WIDTH_RANK = float(os.getenv("V2_VCP_MAX_BB_WIDTH_RANK", "0.50"))
V2_VCP_MIN_VOLUME_RATIO = float(os.getenv("V2_VCP_MIN_VOLUME_RATIO", "1.22"))
V2_VCP_MIN_CLOSE_LOCATION = float(os.getenv("V2_VCP_MIN_CLOSE_LOCATION", "0.65"))
V2_VCP_REQUIRE_MA200 = os.getenv("V2_VCP_REQUIRE_MA200", "1") != "0"

# Leader breakout quality gates.
V2_BREAKOUT_REQUIRE_POSITIVE_RS = os.getenv("V2_BREAKOUT_REQUIRE_POSITIVE_RS", "1") != "0"
V2_BLOCK_PULLBACK_IN_UNCERTAIN = os.getenv("V2_BLOCK_PULLBACK_IN_UNCERTAIN", "1") != "0"
V2_PULLBACK_REQUIRE_POSITIVE_RS = os.getenv("V2_PULLBACK_REQUIRE_POSITIVE_RS", "1") != "0"
V2_BREAKOUT_MIN_CLOSE_LOCATION = float(os.getenv("V2_BREAKOUT_MIN_CLOSE_LOCATION", "0.62"))
V2_BREAKOUT_MIN_RS_SCORE = int(os.getenv("V2_BREAKOUT_MIN_RS_SCORE", "12"))
V2_BREAKOUT_REQUIRE_MA20_SLOPE = os.getenv("V2_BREAKOUT_REQUIRE_MA20_SLOPE", "1") != "0"
V2_BREAKOUT_MIN_STOCK_RET_20 = float(os.getenv("V2_BREAKOUT_MIN_STOCK_RET_20", "-0.01"))
V2_BREAKOUT_MIN_STOCK_RET_63 = float(os.getenv("V2_BREAKOUT_MIN_STOCK_RET_63", "-0.03"))
V2_BREAKOUT_MIN_REL_20_SPY = float(os.getenv("V2_BREAKOUT_MIN_REL_20_SPY", "0.00"))
V2_BREAKOUT_MIN_REL_63_SPY = float(os.getenv("V2_BREAKOUT_MIN_REL_63_SPY", "-0.02"))
V2_BREAKOUT_MIN_REL_20_QQQ = float(os.getenv("V2_BREAKOUT_MIN_REL_20_QQQ", "-0.02"))
V2_BREAKOUT_REQUIRE_55DAY_OR_STRONG_RS = os.getenv("V2_BREAKOUT_REQUIRE_55DAY_OR_STRONG_RS", "1") != "0"
V2_BREAKOUT_STRONG_RS_20_SPY = float(os.getenv("V2_BREAKOUT_STRONG_RS_20_SPY", "0.04"))
V2_BREAKOUT_MAX_BASE_RANGE_PCT = float(os.getenv("V2_BREAKOUT_MAX_BASE_RANGE_PCT", "0.34"))
V2_BREAKOUT_MAX_EXTENSION_MA20 = float(os.getenv("V2_BREAKOUT_MAX_EXTENSION_MA20", "0.12"))
V2_BREAKOUT_MAX_EXTENSION_ATR = float(os.getenv("V2_BREAKOUT_MAX_EXTENSION_ATR", "1.75"))

# Major-index trend filters. These are the main defense against 2022-style long
# fakeouts. Disable through env only if deliberately testing a looser paper mode.
V2_REQUIRE_SPY_ABOVE_MA100 = os.getenv("V2_REQUIRE_SPY_ABOVE_MA100", "0") != "0"
V2_REQUIRE_QQQ_ABOVE_MA100 = os.getenv("V2_REQUIRE_QQQ_ABOVE_MA100", "1") != "0"
V2_REQUIRE_SPY_ABOVE_MA200 = os.getenv("V2_REQUIRE_SPY_ABOVE_MA200", "1") != "0"
V2_REQUIRE_QQQ_ABOVE_MA200 = os.getenv("V2_REQUIRE_QQQ_ABOVE_MA200", "0") != "0"

# Stop model.
V2_BREAKOUT_ATR_STOP_MULT = float(os.getenv("V2_BREAKOUT_ATR_STOP_MULT", "1.95"))
V2_PULLBACK_ATR_STOP_MULT = float(os.getenv("V2_PULLBACK_ATR_STOP_MULT", "1.95"))
V2_STOP_WIDER_OF_ATR_AND_STRUCTURE = os.getenv("V2_STOP_WIDER_OF_ATR_AND_STRUCTURE", "1") != "0"

# Winner-capture / exit management.
BREAKEVEN_R_TRIGGER = float(os.getenv("BREAKEVEN_R_TRIGGER", "1.00"))
PARTIAL_TAKE_PROFIT_R = float(os.getenv("PARTIAL_TAKE_PROFIT_R", "1.25"))
PARTIAL_TAKE_PROFIT_PCT = float(os.getenv("PARTIAL_TAKE_PROFIT_PCT", "0.10"))
PARTIAL_TAKE_PROFIT_FRACTION = float(os.getenv("PARTIAL_TAKE_PROFIT_FRACTION", "0.40"))
TRAIL_MULT_EARLY = float(os.getenv("TRAIL_MULT_EARLY", "4.00"))
TRAIL_MULT_LATE = float(os.getenv("TRAIL_MULT_LATE", "3.00"))
TRAIL_TIGHTEN_PCT = float(os.getenv("TRAIL_TIGHTEN_PCT", "0.05"))

# Risk boost disabled by default; selection quality should create edge, not leverage.
V2_RISK_BOOST_ENABLED = os.getenv("V2_RISK_BOOST_ENABLED", "0") != "0"
V2_A_PLUS_RISK_BOOST = float(os.getenv("V2_A_PLUS_RISK_BOOST", "1.08"))
V2_A_RISK_BOOST = float(os.getenv("V2_A_RISK_BOOST", "1.03"))


# -----------------------------------------------------------------------------
# BEAR SLEEVE V1 - ROBUST 3X INVERSE VCP ENGINE
# -----------------------------------------------------------------------------
# Research conclusion:
# - Do NOT loosen the long VCP strategy to handle bear markets.
# - Use a separate inverse-ETF sleeve only when market regime is truly risk-off.
# - The more robust bear candidate was broad 3x inverse VCP, not aggressive 2x
#   reclaim. It made less money in backtests but survived slippage/execution stress
#   better and had materially lower drawdown.
BEAR_SLEEVE_ENABLED = os.getenv("BEAR_SLEEVE_ENABLED", "0") != "0"
BEAR_BLOCK_LONG_SIGNALS_IN_BEAR = os.getenv("BEAR_BLOCK_LONG_SIGNALS_IN_BEAR", "1") != "0"
BEAR_STRATEGY_VERSION = os.getenv("BEAR_STRATEGY_VERSION", "bear_v1_robust_3x_vcp")

# Broad 3x inverse ETFs only. Keep this tight until forward-tested.
BEAR_WATCHLIST = [
    "SPXU",  # 3x inverse S&P 500
    "SQQQ",  # 3x inverse Nasdaq 100
    "SDOW",  # 3x inverse Dow
    "TZA",   # 3x inverse Russell 2000
]

# Bear regime score: max 60. Defaults mirror the tested 30/15 regime idea.
BEAR_ENTRY_SCORE = int(os.getenv("BEAR_ENTRY_SCORE", "30"))
BEAR_EXIT_SCORE = int(os.getenv("BEAR_EXIT_SCORE", "15"))
BEAR_MAX_SIGNALS_PER_SCAN = int(os.getenv("BEAR_MAX_SIGNALS_PER_SCAN", "2"))
BEAR_MAX_OPEN_POSITIONS = int(os.getenv("BEAR_MAX_OPEN_POSITIONS", "2"))

# Bear VCP entry filters. These are deliberately not ultra-loose.
BEAR_MIN_PRICE = float(os.getenv("BEAR_MIN_PRICE", "8"))
BEAR_MIN_AVG_DOLLAR_VOLUME = float(os.getenv("BEAR_MIN_AVG_DOLLAR_VOLUME", "30000000"))
BEAR_MIN_ATR_PCT = float(os.getenv("BEAR_MIN_ATR_PCT", "0.015"))
BEAR_MAX_ATR_PCT = float(os.getenv("BEAR_MAX_ATR_PCT", "0.18"))
BEAR_VCP_MIN_SCORE = int(os.getenv("BEAR_VCP_MIN_SCORE", "78"))
BEAR_VCP_MAX_BASE_RANGE_PCT = float(os.getenv("BEAR_VCP_MAX_BASE_RANGE_PCT", "0.34"))
BEAR_VCP_MAX_ATR_RATIO = float(os.getenv("BEAR_VCP_MAX_ATR_RATIO", "1.02"))
BEAR_VCP_MAX_BB_WIDTH_RANK = float(os.getenv("BEAR_VCP_MAX_BB_WIDTH_RANK", "0.70"))
BEAR_VCP_MIN_VOLUME_RATIO = float(os.getenv("BEAR_VCP_MIN_VOLUME_RATIO", "1.05"))
BEAR_VCP_MIN_CLOSE_LOCATION = float(os.getenv("BEAR_VCP_MIN_CLOSE_LOCATION", "0.58"))
BEAR_MAX_RSI = float(os.getenv("BEAR_MAX_RSI", "86"))
BEAR_MAX_DAY_MOVE_PCT = float(os.getenv("BEAR_MAX_DAY_MOVE_PCT", "12"))
BEAR_MAX_RISK_PER_SHARE_PCT = float(os.getenv("BEAR_MAX_RISK_PER_SHARE_PCT", "0.16"))

# Bear position sizing and exits. Kept separate from long v2.8 exits.
BEAR_RISK_PCT = float(os.getenv("BEAR_RISK_PCT", "0.03"))
BEAR_ATR_STOP_MULT = float(os.getenv("BEAR_ATR_STOP_MULT", "2.40"))
BEAR_TRAIL_MULT_EARLY = float(os.getenv("BEAR_TRAIL_MULT_EARLY", "4.40"))
BEAR_TRAIL_MULT_LATE = float(os.getenv("BEAR_TRAIL_MULT_LATE", "4.40"))
BEAR_BREAKEVEN_R_TRIGGER = float(os.getenv("BEAR_BREAKEVEN_R_TRIGGER", "1.20"))
BEAR_PARTIAL_TAKE_PROFIT_R = float(os.getenv("BEAR_PARTIAL_TAKE_PROFIT_R", "1.20"))
BEAR_PARTIAL_TAKE_PROFIT_PCT = float(os.getenv("BEAR_PARTIAL_TAKE_PROFIT_PCT", "0.10"))
BEAR_PARTIAL_TAKE_PROFIT_FRACTION = float(os.getenv("BEAR_PARTIAL_TAKE_PROFIT_FRACTION", "0.40"))
BEAR_TIME_STOP_DAYS = int(os.getenv("BEAR_TIME_STOP_DAYS", "10"))
BEAR_TIME_STOP_MIN_R = float(os.getenv("BEAR_TIME_STOP_MIN_R", "0.25"))
BEAR_MAX_HOLDING_DAYS = int(os.getenv("BEAR_MAX_HOLDING_DAYS", "30"))


# -----------------------------------------------------------------------------
# V3.6 PRIVATE WEALTH SLEEVE - CORE ETF ROTATION + CORE LEDGER
# -----------------------------------------------------------------------------
# Research conclusion from the 2022-2026 cache run:
# - Keep v2.8 long VCP + bear inverse as the tactical swing engine.
# - Add only a private core ETF rotation sleeve now.
# - Do not integrate speculative/weak/medium or standalone metals sleeves yet;
#   the research run rejected them after costs. GLD/SLV/metals remain inside
#   the diversified core universe, where they can win when ranked strongly.
# - This sleeve sends PRIVATE allocation guidance only. It does not touch the
#   public channel. In v3.6 it has its own core ledger and updates shared cash only after you confirm with corebuy/coresell.
WEALTH_SLEEVE_ENABLED = os.getenv("WEALTH_SLEEVE_ENABLED", "1") != "0"
WEALTH_STRATEGY_VERSION = os.getenv("WEALTH_STRATEGY_VERSION", "wealth_core_rotation_v3_6_core_ledger")
WEALTH_CORE_ACCOUNT_ALLOC_PCT = float(os.getenv("WEALTH_CORE_ACCOUNT_ALLOC_PCT", "0.45"))
WEALTH_CORE_TOP_N = int(os.getenv("WEALTH_CORE_TOP_N", "5"))
WEALTH_ALERT_REPEAT_DAYS = int(os.getenv("WEALTH_ALERT_REPEAT_DAYS", "20"))
WEALTH_REVIEW_AFTER_CLOSE_MINUTE = int(os.getenv("WEALTH_REVIEW_AFTER_CLOSE_MINUTE", str(16 * 60 + 15)))
WEALTH_MIN_SCORE = float(os.getenv("WEALTH_MIN_SCORE", "-0.20"))

WEALTH_CORE_UNIVERSE = [
    # Broad equity / growth / sector leadership
    "SPY", "QQQ", "VTI", "VOO", "IWM", "DIA",
    "SMH", "SOXX", "XLK", "XLV", "XLE", "XLF", "XLI", "XLY", "XLC",
    # Metals / commodities / defensive alternatives
    "GLD", "IAU", "SLV", "DBC", "DBB", "CPER",
    # Cash-like / rates / bond defense
    "BIL", "SGOV", "SHY", "IEF", "TLT",
]

WEALTH_CASH_LIKE = {"BIL", "SGOV", "SHY"}
WEALTH_DEFENSIVE_ALLOWED = {"BIL", "SGOV", "SHY", "IEF", "TLT", "GLD", "IAU", "XLP", "XLV", "XLU"}


# -----------------------------------------------------------------------------
# V3.5 INSTITUTIONAL PORTFOLIO INTELLIGENCE LAYER
# -----------------------------------------------------------------------------
# This layer wraps the existing v3.0 sleeves. It does not rewrite the VCP edge.
# It adds private allocation intelligence, concentration control, volatility
# weighting, sleeve reporting, and drawdown guardrails.
WEALTH_DYNAMIC_ALLOCATION_ENABLED = os.getenv("WEALTH_DYNAMIC_ALLOCATION_ENABLED", "1") != "0"
WEALTH_CORE_ALLOC_BULL = float(os.getenv("WEALTH_CORE_ALLOC_BULL", "0.45"))
WEALTH_CORE_ALLOC_UNCERTAIN = float(os.getenv("WEALTH_CORE_ALLOC_UNCERTAIN", "0.45"))
WEALTH_CORE_ALLOC_BEAR = float(os.getenv("WEALTH_CORE_ALLOC_BEAR", "0.45"))
WEALTH_CORE_ALLOC_RISK_OFF = float(os.getenv("WEALTH_CORE_ALLOC_RISK_OFF", "0.45"))
WEALTH_TACTICAL_LONG_ALLOC_BULL = float(os.getenv("WEALTH_TACTICAL_LONG_ALLOC_BULL", "0.15"))
WEALTH_TACTICAL_LONG_ALLOC_UNCERTAIN = float(os.getenv("WEALTH_TACTICAL_LONG_ALLOC_UNCERTAIN", "0.10"))
WEALTH_TACTICAL_LONG_ALLOC_BEAR = float(os.getenv("WEALTH_TACTICAL_LONG_ALLOC_BEAR", "0.00"))
WEALTH_BEAR_ALLOC_BULL = float(os.getenv("WEALTH_BEAR_ALLOC_BULL", "0.00"))
WEALTH_BEAR_ALLOC_UNCERTAIN = float(os.getenv("WEALTH_BEAR_ALLOC_UNCERTAIN", "0.05"))
WEALTH_BEAR_ALLOC_BEAR = float(os.getenv("WEALTH_BEAR_ALLOC_BEAR", "0.15"))
WEALTH_MIN_CASH_RESERVE_PCT = float(os.getenv("WEALTH_MIN_CASH_RESERVE_PCT", "0.00"))

WEALTH_VOL_WEIGHTING_ENABLED = os.getenv("WEALTH_VOL_WEIGHTING_ENABLED", "1") != "0"
WEALTH_SCORE_WEIGHTING_ENABLED = os.getenv("WEALTH_SCORE_WEIGHTING_ENABLED", "1") != "0"
WEALTH_CLUSTER_CONTROL_ENABLED = os.getenv("WEALTH_CLUSTER_CONTROL_ENABLED", "1") != "0"
WEALTH_MAX_ASSETS_PER_CLUSTER = int(os.getenv("WEALTH_MAX_ASSETS_PER_CLUSTER", "2"))
WEALTH_MAX_SINGLE_CORE_ASSET_PCT = float(os.getenv("WEALTH_MAX_SINGLE_CORE_ASSET_PCT", "0.25"))
WEALTH_MIN_SINGLE_CORE_ASSET_PCT = float(os.getenv("WEALTH_MIN_SINGLE_CORE_ASSET_PCT", "0.05"))
WEALTH_REBALANCE_DRIFT_THRESHOLD_PCT = float(os.getenv("WEALTH_REBALANCE_DRIFT_THRESHOLD_PCT", "0.05"))

# V3.6 core-ledger controls. Core wealth is now a real private sleeve with
# separate positions/trades/signals, but it shares the same account cash so
# tactical sizing, equity, risk guards, withdrawals, and statistics stay honest.
CORE_LEDGER_ENABLED = os.getenv("CORE_LEDGER_ENABLED", "1") != "0"
CORE_REQUIRE_ACTIVE_PLAN_FOR_BUY = os.getenv("CORE_REQUIRE_ACTIVE_PLAN_FOR_BUY", "1") != "0"
CORE_REQUIRE_LIVE_QUOTE = os.getenv("CORE_REQUIRE_LIVE_QUOTE", "1") != "0"
CORE_QUOTE_DEVIATION_LIMIT = float(os.getenv("CORE_QUOTE_DEVIATION_LIMIT", "0.05"))
CORE_MIN_TRADE_DOLLARS = float(os.getenv("CORE_MIN_TRADE_DOLLARS", "25"))
CORE_ACTION_DOLLAR_THRESHOLD = float(os.getenv("CORE_ACTION_DOLLAR_THRESHOLD", "50"))
CORE_REBALANCE_SELL_CONFIRM_MONTHS = int(os.getenv("CORE_REBALANCE_SELL_CONFIRM_MONTHS", "2"))
CORE_POSITION_EPSILON = float(os.getenv("CORE_POSITION_EPSILON", "0.000001"))
CORE_CASH_RESERVE_PROTECT = os.getenv("CORE_CASH_RESERVE_PROTECT", "1") != "0"
CORE_ALLOW_FRACTIONAL_SHARES = os.getenv("CORE_ALLOW_FRACTIONAL_SHARES", "1") != "0"
CORE_ALLOW_BUY_OUTSIDE_PLAN = os.getenv("CORE_ALLOW_BUY_OUTSIDE_PLAN", "0") != "0"

PORTFOLIO_RISK_GUARD_ENABLED = os.getenv("PORTFOLIO_RISK_GUARD_ENABLED", "1") != "0"
PORTFOLIO_SOFT_DD_REDUCE_PCT = float(os.getenv("PORTFOLIO_SOFT_DD_REDUCE_PCT", "0.12"))
PORTFOLIO_HARD_DD_PAUSE_PCT = float(os.getenv("PORTFOLIO_HARD_DD_PAUSE_PCT", "0.20"))
PORTFOLIO_DD_LOOKBACK_DAYS = int(os.getenv("PORTFOLIO_DD_LOOKBACK_DAYS", "400"))
PORTFOLIO_RISK_ALERT_REPEAT_DAYS = int(os.getenv("PORTFOLIO_RISK_ALERT_REPEAT_DAYS", "1"))

WEALTH_ASSET_CLUSTERS = {
    # Broad equity / growth
    "SPY": "broad_equity", "VOO": "broad_equity", "VTI": "broad_equity", "DIA": "broad_equity", "IWM": "small_caps",
    "QQQ": "growth_tech", "XLK": "growth_tech", "SMH": "semis", "SOXX": "semis",
    # Sectors
    "XLV": "defensive_equity", "XLP": "defensive_equity", "XLU": "defensive_equity", "XLF": "financials",
    "XLE": "energy", "XLI": "industrials", "XLY": "consumer_cyclical", "XLC": "communication",
    # Metals / commodities
    "GLD": "gold", "IAU": "gold", "SLV": "silver", "DBC": "commodities", "DBB": "industrial_metals", "CPER": "copper",
    # Cash / bonds
    "BIL": "cash_like", "SGOV": "cash_like", "SHY": "short_bonds", "IEF": "intermediate_bonds", "TLT": "long_bonds",
}


# Manual buy protection.

# These prevent typo buys like UBST when the real signal was UPST.

REQUIRE_ACTIVE_SIGNAL_FOR_BUY = os.getenv("REQUIRE_ACTIVE_SIGNAL_FOR_BUY", "1") != "0"

REQUIRE_BUY_TICKER_IN_WATCHLIST = os.getenv("REQUIRE_BUY_TICKER_IN_WATCHLIST", "1") != "0"

REQUIRE_LIVE_QUOTE_FOR_BUY = os.getenv("REQUIRE_LIVE_QUOTE_FOR_BUY", "1") != "0"



# Reject manual buy price if it is too far from current quote.

# This protects against typo prices too.

BUY_QUOTE_DEVIATION_LIMIT = float(os.getenv("BUY_QUOTE_DEVIATION_LIMIT", "0.05"))



# Withdrawal / profit distribution controls.

WITHDRAWAL_REVIEW_DAYS = int(os.getenv("WITHDRAWAL_REVIEW_DAYS", "90"))



# Below this equity, withdraw less to protect compounding.

WITHDRAWAL_BUILD_PHASE_EQUITY = float(os.getenv("WITHDRAWAL_BUILD_PHASE_EQUITY", "10000"))



# Small-account phase withdrawal rate.

WITHDRAWAL_BUILD_PHASE_RATE = float(os.getenv("WITHDRAWAL_BUILD_PHASE_RATE", "0.10"))



# Normal withdrawal rate from new profits above high-water mark.

WITHDRAWAL_PROFIT_RATE = float(os.getenv("WITHDRAWAL_PROFIT_RATE", "0.25"))



# Minimum profit above high-water mark required before withdrawal alert.

WITHDRAWAL_MIN_PROFIT = float(os.getenv("WITHDRAWAL_MIN_PROFIT", "500"))



# Minimum withdrawal amount worth alerting.

WITHDRAWAL_MIN_AMOUNT = float(os.getenv("WITHDRAWAL_MIN_AMOUNT", "100"))



# Keep this cash inside the bot after withdrawal.

WITHDRAWAL_MIN_CASH_AFTER = float(os.getenv("WITHDRAWAL_MIN_CASH_AFTER", "500"))



# If you ignore a withdrawal signal, remind again after this many days.

WITHDRAWAL_ALERT_REPEAT_DAYS = int(os.getenv("WITHDRAWAL_ALERT_REPEAT_DAYS", "7"))

 



# Data / calendar controls.



REQUIRE_FRESH_DAILY_CANDLE = os.getenv("REQUIRE_FRESH_DAILY_CANDLE", "1") != "0"



FAIL_CLOSED_ON_EARNINGS_UNKNOWN = os.getenv("FAIL_CLOSED_ON_EARNINGS_UNKNOWN", "1") != "0"



MANAGE_ONLY_REGULAR_HOURS = os.getenv("MANAGE_ONLY_REGULAR_HOURS", "1") != "0"



PRICE_MISSING_ALERT_THRESHOLD = int(os.getenv("PRICE_MISSING_ALERT_THRESHOLD", "3"))

# Earnings lookahead. Default kept at 7 days to preserve prior behavior,
# but you can raise it to 10 if you want stricter earnings avoidance.
EARNINGS_LOOKAHEAD_DAYS = int(os.getenv("EARNINGS_LOOKAHEAD_DAYS", "7"))



 



# Telegram safety.



MAX_TELEGRAM_MESSAGE = 3900



 



# -----------------------------------------------------------------------------
# WATCHLIST
# -----------------------------------------------------------------------------

# V2.8 uses a broadened STRONG-only universe: liquid ETFs and institutional
# leader stocks. MEDIUM/WEAK lists are kept empty on purpose so the scanner,
# manual-buy guard, and risk sizing all operate from the same leader universe.
#
# Important:
# - Do NOT add meme/weak names here just to get more signals.
# - This list is intentionally diversified across sectors.
# - With FMP Premium, this size should be reasonable, but avoid expanding much
#   above ~180-220 tickers unless you later add earnings-calendar caching.
STRONG = [
    # -------------------------------------------------------------------------
    # Broad / index / sector ETFs
    # -------------------------------------------------------------------------
    "SPY", "QQQ", "IWM", "DIA",
    "SMH", "SOXX",
    "XLK", "IGV",
    "XLF", "KRE",
    "XLE", "XOP",
    "XLV", "IBB",
    "XLI", "IYT",
    "XLP", "XLY", "XLC", "XLB",
    "XLU", "XLRE",
    "ITB",

    # -------------------------------------------------------------------------
    # Mega-cap / platform / dominant compounders
    # -------------------------------------------------------------------------
    "MSFT", "NVDA", "META", "AMZN", "GOOGL",
    "AVGO", "AAPL", "TSLA", "NFLX",
    "ORCL", "CRM", "ADBE", "INTU", "SHOP",

    # -------------------------------------------------------------------------
    # Semiconductors / AI hardware / electronic design / networking leaders
    # -------------------------------------------------------------------------
    "AMD", "MU", "LRCX", "ASML", "QCOM",
    "KLAC", "AMAT", "TSM", "TXN", "ADI",
    "MRVL", "MPWR", "ON", "NXPI", "ARM",
    "ANET", "CDNS", "SNPS",

    # -------------------------------------------------------------------------
    # Software / cybersecurity / cloud / digital infrastructure
    # -------------------------------------------------------------------------
    "PANW", "CRWD", "ZS", "NET", "NOW",
    "PLTR", "DDOG", "MDB", "TEAM", "WDAY",
    "FTNT", "HUBS", "APP", "TTD", "VRT",

    # -------------------------------------------------------------------------
    # Financials / payments / exchanges / asset managers / insurers
    # -------------------------------------------------------------------------
    "JPM", "GS", "MS", "BAC", "WFC",
    "SCHW", "BLK", "SPGI", "MCO",
    "CME", "ICE", "NDAQ",
    "V", "MA", "AXP",
    "BX", "KKR", "APO",
    "PGR", "CB",

    # -------------------------------------------------------------------------
    # Industrials / infrastructure / aerospace / transports
    # -------------------------------------------------------------------------
    "CAT", "DE", "GE", "ETN", "HON",
    "RTX", "URI", "PH", "CMI", "EMR",
    "ITW", "ROK", "TT", "PWR", "FAST",
    "PCAR", "LMT", "NOC", "GD", "TDG",
    "GWW", "UNP", "CSX",

    # -------------------------------------------------------------------------
    # Health care / medtech / pharma / services leaders
    # -------------------------------------------------------------------------
    "LLY", "UNH", "ABBV", "ISRG", "TMO",
    "ABT", "MRK", "JNJ", "AMGN", "REGN",
    "VRTX", "SYK", "BSX", "MDT", "DHR",
    "GILD", "HCA", "MCK", "COR", "IQV",

    # -------------------------------------------------------------------------
    # Consumer / retail / travel / restaurants / marketplaces
    # -------------------------------------------------------------------------
    "COST", "WMT", "MCD", "HD", "LOW",
    "BKNG", "NKE", "SBUX", "CMG", "TJX",
    "ROST", "AZO", "ORLY", "YUM", "DPZ",
    "MAR", "HLT", "RCL", "MELI", "UBER",

    # -------------------------------------------------------------------------
    # Energy / materials / industrial commodities / construction materials
    # -------------------------------------------------------------------------
    "XOM", "CVX", "SLB", "FCX", "LIN",
    "COP", "EOG", "MPC", "PSX", "VLO",
    "NUE", "STLD", "SCCO", "NEM",
    "APD", "SHW", "ECL", "MLM", "VMC",

    # -------------------------------------------------------------------------
    # Utilities / power / real estate / defensive infrastructure
    # -------------------------------------------------------------------------
    "NEE", "CEG", "VST", "DLR", "EQIX", "PLD", "AMT",
]

MEDIUM: List[str] = []
WEAK: List[str] = []

# Deduplicate while preserving order.
WATCHLIST = list(dict.fromkeys(STRONG))

# Manual buys may come from either the long VCP watchlist or the bear inverse sleeve.
ALLOWED_BUY_TICKERS = set(WATCHLIST) | set(BEAR_WATCHLIST)

# -----------------------------------------------------------------------------
# GENERAL HELPERS



# -----------------------------------------------------------------------------



 



def safe_convert(obj: Any) -> Any:



    """Convert numpy/pandas scalar values to JSON-serializable Python values."""



    if isinstance(obj, dict):



        return {k: safe_convert(v) for k, v in obj.items()}



    if isinstance(obj, list):



        return [safe_convert(v) for v in obj]



    if hasattr(obj, "item"):



        return obj.item()



    return obj



 



 



def json_dumps(data: Any) -> str:



    return json.dumps(safe_convert(data), separators=(",", ":"), allow_nan=False)



 



 



def json_loads_dict(raw: Optional[str]) -> Dict[str, Any]:



    if not raw:



        return {}



    try:



        data = json.loads(raw)



        return data if isinstance(data, dict) else {}



    except Exception:



        return {}



 



 



def json_loads_list(raw: Optional[str]) -> List[Any]:



    if not raw:



        return []



    try:



        data = json.loads(raw)



        return data if isinstance(data, list) else []



    except Exception:



        return []



 



 



def is_finite_positive(value: float) -> bool:



    return math.isfinite(value) and value > 0



 



 



def normalize_ticker(ticker: str) -> Optional[str]:



    ticker = ticker.strip().upper()



    # Allows normal US tickers plus dash/dot variants like BRK.B or BRK-B.



    if re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,14}", ticker):



        return ticker



    return None



 



 

def now_ts() -> float:

    return time.time()





portfolio: Dict[str, Any] = {"cash": INITIAL_CASH, "positions": {}}

last_signals: Dict[str, Any] = {}

missing_price_counts: Dict[str, int] = {}



PANIC_MODE = False

LAST_HEARTBEAT = now_ts()

LAST_SCAN_ATTEMPT = 0



NYSE = mcal.get_calendar("NYSE")

 



 



def ny_now() -> datetime:



    return datetime.now(NY_TZ)



 



 



def ny_date_str(ts: Optional[float] = None) -> str:



    dt = datetime.fromtimestamp(ts if ts is not None else now_ts(), NY_TZ)



    return dt.date().isoformat()



 

AUDIT_LOG = os.path.join(DATA_DIR, "audit.log")



LOG_FILE = os.path.join(DATA_DIR, "bot.log")



logger = logging.getLogger("trading_bot")

logger.setLevel(logging.INFO)



handler = RotatingFileHandler(

    LOG_FILE,

    maxBytes=5_000_000,

    backupCount=3

)



formatter = logging.Formatter(

    "%(asctime)s | %(levelname)s | %(message)s"

)



handler.setFormatter(formatter)



if not logger.handlers:

    logger.addHandler(handler)



def audit(event: str, details: str = "") -> None:

    try:

        ts = ny_now().strftime("%Y-%m-%d %H:%M:%S %Z")



        line = f"{ts} | {event}"



        if details:

            line += f" | {details}"



        with open(AUDIT_LOG, "a", encoding="utf-8") as f:

            f.write(line + "\n")



    except Exception as exc:

        print(f"[AUDIT ERROR] {exc}")





def format_money(value: float) -> str:

    return f"${round(value, 2)}"



def pct_from_entry(entry_price: Any, exit_price: Any) -> Optional[float]:

    try:

        entry = float(entry_price)

        exit_ = float(exit_price)



        if entry <= 0:

            return None



        return ((exit_ - entry) / entry) * 100



    except Exception:

        return None





def format_pct(value: Optional[float]) -> str:

    if value is None:

        return "n/a"



    sign = "+" if value >= 0 else ""

    return f"{sign}{round(value, 2)}%"



def format_plain_pct(value: Any, decimals: int = 0) -> str:

    """

    Format a normal percent without plus/minus sign.



    Used for position-size guide like:

    17.86 -> 18%

    """

    try:

        if value is None:

            return "n/a"



        value_float = float(value)



        if decimals <= 0:

            return f"{int(round(value_float))}%"



        return f"{round(value_float, decimals)}%"



    except Exception:

        return "n/a"





def get_performance_base_capital() -> float:

    """

    Base capital used for realized P/L percentage.



    Priority:

    1) performance_base_capital from meta table

    2) INITIAL_CASH fallback

    """

    value = get_meta("performance_base_capital")



    if value is not None:

        try:

            base = float(value)



            if math.isfinite(base) and base > 0:

                return base



        except ValueError:

            pass



    return INITIAL_CASH if math.isfinite(INITIAL_CASH) and INITIAL_CASH > 0 else 0.0





def maybe_set_performance_base_from_cash_tx(

    conn: sqlite3.Connection,

    amount: float

) -> None:

    """

    Sets performance base capital after clean reset + setcash.



    It only auto-sets if:

    - base is missing

    - no positions exist

    - no trades exist



    This prevents accidental baseline changes during normal paper/live trading.

    """

    if not math.isfinite(amount) or amount <= 0:

        return



    existing = conn.execute(

        "SELECT value FROM meta WHERE key = 'performance_base_capital'"

    ).fetchone()



    if existing is not None:

        return



    pos_count = conn.execute(

        "SELECT COUNT(*) AS n FROM positions"

    ).fetchone()["n"]



    trade_count = conn.execute(

        "SELECT COUNT(*) AS n FROM trades"

    ).fetchone()["n"]



    core_pos_count = conn.execute(
        "SELECT COUNT(*) AS n FROM core_positions"
    ).fetchone()["n"]

    core_trade_count = conn.execute(
        "SELECT COUNT(*) AS n FROM core_trades"
    ).fetchone()["n"]

    if int(pos_count) == 0 and int(trade_count) == 0 and int(core_pos_count) == 0 and int(core_trade_count) == 0:

        conn.execute(

            "INSERT INTO meta(key, value) VALUES ('performance_base_capital', ?)",

            (str(round(amount, 2)),),

        )





def realized_performance_all_time() -> Dict[str, Any]:
    """
    All-time realized P/L from swing trades plus core realized sells.

    This does NOT include open unrealized P/L.
    """
    trades = load_trades()
    swing_profit = round(sum(float(t.get("profit", 0)) for t in trades), 2)

    core_trades = load_core_trades() if CORE_LEDGER_ENABLED else []
    core_profit = round(
        sum(float(t.get("realized_profit") or 0.0) for t in core_trades if str(t.get("side")).upper() == "SELL"),
        2,
    )

    total_profit = round(swing_profit + core_profit, 2)
    base_capital = get_performance_base_capital()
    pct = None
    if base_capital > 0:
        pct = (total_profit / base_capital) * 100

    return {
        "profit": total_profit,
        "swing_profit": swing_profit,
        "core_realized_profit": core_profit,
        "pct": pct,
        "base_capital": round(base_capital, 2),
        "trade_records": len(trades) + len(core_trades),
        "swing_trade_records": len(trades),
        "core_trade_records": len(core_trades),
    }


def market_label(market: str) -> str:

    labels = {

        "BULL": "🐂 BULL",

        "BEAR": "🐻 BEAR",

        "UNCERTAIN": "🟡 UNCERTAIN",

    }

    return labels.get(str(market).upper(), f"⚪ {market}")





def setup_label(setup_type: str) -> str:
    setup = str(setup_type).lower()

    if setup == "breakout":
        return "🚀 RS Breakout"

    if setup == "vcp_breakout":
        return "📦 VCP Breakout"

    if setup == "bear_vcp_inverse":
        return "🐻 Inverse VCP Bear Sleeve"

    if setup == "pullback":
        return "🔁 Pullback"

    if setup == "reclaim":
        return "♻️ Reclaim"

    return f"⚙️ {setup_type}"


def sleeve_label(entry_data: Dict[str, Any]) -> str:
    sleeve = str(entry_data.get("strategy_sleeve", "LONG_VCP")).upper()

    if sleeve == "BEAR_INVERSE":
        return "🐻 BEAR INVERSE SLEEVE"

    if sleeve == "LONG_VCP":
        return "🐂 BULL / LONG VCP SLEEVE"

    return f"⚙️ {sleeve}"


def sleeve_short_label(entry_data: Dict[str, Any]) -> str:
    sleeve = str(entry_data.get("strategy_sleeve", "LONG_VCP")).upper()

    if sleeve == "BEAR_INVERSE":
        return "BEAR INVERSE"

    if sleeve == "LONG_VCP":
        return "LONG VCP"

    return sleeve


def yes_no(value: bool) -> str:

    return "✅ Yes" if value else "❌ No"



def heartbeat() -> None:

    try:

        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:

            f.write(str(now_ts()))

    except Exception as exc:

        print(f"[HEARTBEAT ERROR] {exc}")





# -----------------------------------------------------------------------------



# SQLITE PERSISTENCE



# -----------------------------------------------------------------------------



 



def db_connect() -> sqlite3.Connection:



    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)



    conn.row_factory = sqlite3.Row



    conn.execute("PRAGMA foreign_keys = ON")



    conn.execute("PRAGMA busy_timeout = 30000")



    return conn



 



 



@contextmanager



def db_tx() -> Iterable[sqlite3.Connection]:



    conn = db_connect()



    try:



        conn.execute("BEGIN IMMEDIATE")



        yield conn



        conn.commit()



    except Exception:



        conn.rollback()



        raise



    finally:



        conn.close()



 



 



def init_db() -> None:



    conn = db_connect()



    try:



        conn.execute("PRAGMA journal_mode = WAL")



        conn.execute("PRAGMA synchronous = FULL")



        conn.executescript(



            """



            CREATE TABLE IF NOT EXISTS meta (



                key TEXT PRIMARY KEY,



                value TEXT NOT NULL



            );



 



            CREATE TABLE IF NOT EXISTS account (



                id INTEGER PRIMARY KEY CHECK (id = 1),



                cash REAL NOT NULL CHECK (cash >= 0)



            );



 



            CREATE TABLE IF NOT EXISTS positions (



                ticker TEXT PRIMARY KEY,



                position_id TEXT NOT NULL UNIQUE,



                strategy_version TEXT NOT NULL,



                shares INTEGER NOT NULL CHECK (shares > 0),



                entry_price REAL NOT NULL CHECK (entry_price > 0),



                initial_stop REAL NOT NULL CHECK (initial_stop > 0),



                stop REAL NOT NULL CHECK (stop > 0),



                highest REAL NOT NULL CHECK (highest > 0),



                partial_taken INTEGER NOT NULL DEFAULT 0 CHECK (partial_taken IN (0,1)),



                entry_time REAL NOT NULL,



                atr REAL,



                risk_per_share REAL,



                entry_data_json TEXT NOT NULL DEFAULT '{}'



            );



 



            CREATE TABLE IF NOT EXISTS trades (



                id TEXT PRIMARY KEY,



                position_id TEXT,



                strategy_version TEXT,



                ticker TEXT NOT NULL,



                entry_price REAL NOT NULL,



                exit_price REAL NOT NULL,



                shares INTEGER NOT NULL CHECK (shares > 0),



                profit REAL NOT NULL,



                entry_time REAL NOT NULL,



                exit_time REAL NOT NULL,



                duration_sec INTEGER NOT NULL,



                exit_reason TEXT NOT NULL,



                entry_data_json TEXT NOT NULL DEFAULT '{}',



                risk_per_share REAL,



                r_multiple REAL,



                created_at REAL NOT NULL,



                UNIQUE(position_id, exit_time, exit_reason)





            );



 



            CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);



            CREATE INDEX IF NOT EXISTS idx_trades_position_id ON trades(position_id);



            CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON trades(exit_time);



 



            CREATE TABLE IF NOT EXISTS signals (



                ticker TEXT PRIMARY KEY,



                time REAL NOT NULL,



                entry_data_json TEXT NOT NULL DEFAULT '{}'



            );



 



            CREATE TABLE IF NOT EXISTS equity_snapshots (



                snapshot_date TEXT PRIMARY KEY,



                time REAL NOT NULL,



                equity REAL NOT NULL,



                cash REAL NOT NULL,



                positions_value REAL NOT NULL



            );



 



            CREATE TABLE IF NOT EXISTS processed_updates (



                update_id INTEGER PRIMARY KEY,



                processed_at REAL NOT NULL,



                status TEXT NOT NULL



            );



 



            CREATE TABLE IF NOT EXISTS cooldowns (



                ticker TEXT PRIMARY KEY,



                time REAL NOT NULL



            );



 



            CREATE TABLE IF NOT EXISTS breakout_memory (



                ticker TEXT PRIMARY KEY,



                levels_json TEXT NOT NULL DEFAULT '[]'



            );



            CREATE TABLE IF NOT EXISTS withdrawals (

                id TEXT PRIMARY KEY,

                time REAL NOT NULL,

                amount REAL NOT NULL CHECK (amount > 0),

                equity_before REAL NOT NULL,

                cash_before REAL NOT NULL,

                cash_after REAL NOT NULL,

                high_water_mark_before REAL NOT NULL,

                high_water_mark_after REAL NOT NULL,

                note TEXT NOT NULL DEFAULT ''

            );

            CREATE TABLE IF NOT EXISTS core_positions (

                ticker TEXT PRIMARY KEY,

                core_position_id TEXT NOT NULL UNIQUE,

                strategy_version TEXT NOT NULL,

                shares REAL NOT NULL CHECK (shares > 0),

                avg_entry_price REAL NOT NULL CHECK (avg_entry_price > 0),

                cost_basis REAL NOT NULL CHECK (cost_basis >= 0),

                entry_time REAL NOT NULL,

                last_update_time REAL NOT NULL,

                highest REAL,

                sleeve TEXT NOT NULL DEFAULT 'CORE_WEALTH',

                target_account_pct REAL,

                last_plan_id TEXT,

                notes TEXT NOT NULL DEFAULT ''

            );

            CREATE TABLE IF NOT EXISTS core_trades (

                id TEXT PRIMARY KEY,

                core_position_id TEXT,

                ticker TEXT NOT NULL,

                side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),

                shares REAL NOT NULL CHECK (shares > 0),

                price REAL NOT NULL CHECK (price > 0),

                amount REAL NOT NULL,

                realized_profit REAL,

                time REAL NOT NULL,

                strategy_version TEXT NOT NULL,

                plan_id TEXT,

                reason TEXT NOT NULL DEFAULT '',

                created_at REAL NOT NULL

            );

            CREATE INDEX IF NOT EXISTS idx_core_trades_ticker ON core_trades(ticker);

            CREATE INDEX IF NOT EXISTS idx_core_trades_time ON core_trades(time);

            CREATE TABLE IF NOT EXISTS core_signals (

                id TEXT PRIMARY KEY,

                time REAL NOT NULL,

                plan_date TEXT NOT NULL,

                market_regime TEXT NOT NULL,

                account_equity REAL NOT NULL,

                core_target_pct REAL NOT NULL,

                plan_json TEXT NOT NULL DEFAULT '{}',

                status TEXT NOT NULL DEFAULT 'ACTIVE'

            );

            CREATE INDEX IF NOT EXISTS idx_core_signals_time ON core_signals(time);



            """



        )



        row = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()



        if row is None:



            conn.execute("INSERT INTO account(id, cash) VALUES (1, ?)", (INITIAL_CASH,))



        conn.commit()



    finally:



        conn.close()



 



 



def get_meta(key: str, default: Optional[str] = None) -> Optional[str]:



    conn = db_connect()



    try:



        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()



        return row["value"] if row else default



    finally:



        conn.close()



 



 



def set_meta(key: str, value: str) -> None:



    with db_tx() as conn:



        conn.execute(



            "INSERT INTO meta(key, value) VALUES (?, ?) "



            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",



            (key, value),



        )



 



 



def get_cash(conn: sqlite3.Connection) -> float:



    row = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()



    if row is None:



        raise RuntimeError("Account row missing")



    return float(row["cash"])



 



 



def set_cash_tx(conn: sqlite3.Connection, cash: float) -> None:



    if not math.isfinite(cash) or cash < 0:



        raise ValueError("Cash must be finite and non-negative")



    conn.execute("UPDATE account SET cash = ? WHERE id = 1", (round(cash, 6),))



 



 



def row_to_position(row: sqlite3.Row) -> Dict[str, Any]:



    return {



        "position_id": row["position_id"],



        "strategy_version": row["strategy_version"],



        "shares": int(row["shares"]),



        "price": float(row["entry_price"]),



        "initial_stop": float(row["initial_stop"]),



        "stop": float(row["stop"]),



        "highest": float(row["highest"]),



        "partial_taken": bool(row["partial_taken"]),



        "entry_time": float(row["entry_time"]),



        "atr": None if row["atr"] is None else float(row["atr"]),



        "risk_per_share": None if row["risk_per_share"] is None else float(row["risk_per_share"]),



        "entry_data": json_loads_dict(row["entry_data_json"]),



    }



 



 



def load_portfolio() -> Dict[str, Any]:



    conn = db_connect()



    try:



        cash = get_cash(conn)



        rows = conn.execute("SELECT * FROM positions ORDER BY ticker").fetchall()



        positions = {row["ticker"]: row_to_position(row) for row in rows}



        return {"cash": cash, "positions": positions}



    finally:



        conn.close()



 



 



def refresh_portfolio() -> None:



    global portfolio



    portfolio = load_portfolio()



 



 



def upsert_position_tx(conn: sqlite3.Connection, ticker: str, pos: Dict[str, Any]) -> None:



    entry_data_json = json_dumps(pos.get("entry_data", {}))



    conn.execute(



        """



        INSERT INTO positions(



            ticker, position_id, strategy_version, shares, entry_price,



            initial_stop, stop, highest, partial_taken, entry_time,



            atr, risk_per_share, entry_data_json



        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)



        ON CONFLICT(ticker) DO UPDATE SET



            position_id = excluded.position_id,



            strategy_version = excluded.strategy_version,



            shares = excluded.shares,



            entry_price = excluded.entry_price,



            initial_stop = excluded.initial_stop,



            stop = excluded.stop,



            highest = excluded.highest,



            partial_taken = excluded.partial_taken,



            entry_time = excluded.entry_time,



            atr = excluded.atr,



            risk_per_share = excluded.risk_per_share,



            entry_data_json = excluded.entry_data_json



        """,



        (



            ticker,



            pos["position_id"],



            pos.get("strategy_version", STRATEGY_VERSION),



            int(pos["shares"]),



            float(pos["price"]),



            float(pos["initial_stop"]),



            float(pos["stop"]),



            float(pos["highest"]),



            1 if pos.get("partial_taken") else 0,



            float(pos["entry_time"]),



            None if pos.get("atr") is None else float(pos["atr"]),



            None if pos.get("risk_per_share") is None else float(pos["risk_per_share"]),



            entry_data_json,



        ),



    )



 



 



def delete_position_tx(conn: sqlite3.Connection, ticker: str) -> None:



    conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))



 



 



def load_trades() -> List[Dict[str, Any]]:



    conn = db_connect()



    try:



        rows = conn.execute("SELECT * FROM trades ORDER BY exit_time ASC, created_at ASC").fetchall()



        return [row_to_trade(row) for row in rows]



    finally:



        conn.close()



 



 



def row_to_trade(row: sqlite3.Row) -> Dict[str, Any]:



    return {



        "id": row["id"],



        "position_id": row["position_id"],



        "strategy_version": row["strategy_version"],



        "ticker": row["ticker"],



        "entry_price": float(row["entry_price"]),



        "exit_price": float(row["exit_price"]),



        "shares": int(row["shares"]),



        "profit": float(row["profit"]),



        "entry_time": float(row["entry_time"]),



        "exit_time": float(row["exit_time"]),



        "duration_sec": int(row["duration_sec"]),



        "exit_reason": row["exit_reason"],



        "entry_data": json_loads_dict(row["entry_data_json"]),



        "risk_per_share": None if row["risk_per_share"] is None else float(row["risk_per_share"]),



        "r_multiple": None if row["r_multiple"] is None else float(row["r_multiple"]),



    }



 



 



def insert_trade_tx(conn: sqlite3.Connection, trade: Dict[str, Any]) -> None:



    conn.execute(



        """



        INSERT INTO trades(



            id, position_id, strategy_version, ticker, entry_price, exit_price,



            shares, profit, entry_time, exit_time, duration_sec, exit_reason,



            entry_data_json, risk_per_share, r_multiple, created_at



        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)



        """,



        (



            trade["id"],



            trade.get("position_id"),



            trade.get("strategy_version"),



            trade["ticker"],



            float(trade["entry_price"]),



            float(trade["exit_price"]),



            int(trade["shares"]),



            float(trade["profit"]),



            float(trade["entry_time"]),



            float(trade["exit_time"]),



            int(trade["duration_sec"]),



            trade["exit_reason"],



            json_dumps(trade.get("entry_data", {})),



            None if trade.get("risk_per_share") is None else float(trade["risk_per_share"]),



            None if trade.get("r_multiple") is None else float(trade["r_multiple"]),



            now_ts(),



        ),



    )



 



 



def load_signals() -> Dict[str, Any]:



    conn = db_connect()



    try:



        rows = conn.execute("SELECT * FROM signals").fetchall()



        return {



            row["ticker"]: {



                "time": float(row["time"]),



                "entry_data": json_loads_dict(row["entry_data_json"]),



            }



            for row in rows



        }



    finally:



        conn.close()



 



 



def save_signal(ticker: str, signal_time: float, entry_data: Dict[str, Any]) -> None:



    global last_signals



    with db_tx() as conn:



        conn.execute(



            "INSERT INTO signals(ticker, time, entry_data_json) VALUES (?, ?, ?) "



            "ON CONFLICT(ticker) DO UPDATE SET time = excluded.time, entry_data_json = excluded.entry_data_json",



            (ticker, signal_time, json_dumps(entry_data)),



        )



    last_signals = load_signals()



 



 



def save_signals() -> None:



    """Compatibility function: persist current in-memory last_signals dict."""



    with db_tx() as conn:



        conn.execute("DELETE FROM signals")



        for ticker, signal in last_signals.items():



            if isinstance(signal, dict):



                signal_time = float(signal.get("time", 0))



                entry_data = signal.get("entry_data", {})



            else:



                signal_time = float(signal)



                entry_data = {}



            conn.execute(



                "INSERT INTO signals(ticker, time, entry_data_json) VALUES (?, ?, ?)",



                (ticker, signal_time, json_dumps(entry_data)),



            )



 



 



def clear_signals() -> None:



    global last_signals



    with db_tx() as conn:



        conn.execute("DELETE FROM signals")



    last_signals = {}



 



 



def get_cooldowns() -> Dict[str, float]:



    conn = db_connect()



    try:



        rows = conn.execute("SELECT ticker, time FROM cooldowns").fetchall()



        return {row["ticker"]: float(row["time"]) for row in rows}



    finally:



        conn.close()



 



 



def set_cooldown(ticker: str, timestamp: float) -> None:



    with db_tx() as conn:



        conn.execute(



            "INSERT INTO cooldowns(ticker, time) VALUES (?, ?) "



            "ON CONFLICT(ticker) DO UPDATE SET time = excluded.time",



            (ticker, timestamp),



        )



 



 



def get_breakout_levels(ticker: str) -> set:



    conn = db_connect()



    try:



        row = conn.execute("SELECT levels_json FROM breakout_memory WHERE ticker = ?", (ticker,)).fetchone()



        if not row:



            return set()



        return set(int(x) for x in json_loads_list(row["levels_json"]))



    finally:



        conn.close()



 



 



def set_breakout_levels(ticker: str, levels: set) -> None:



    with db_tx() as conn:



        conn.execute(



            "INSERT INTO breakout_memory(ticker, levels_json) VALUES (?, ?) "



            "ON CONFLICT(ticker) DO UPDATE SET levels_json = excluded.levels_json",



            (ticker, json_dumps(sorted(levels))),



        )



 



 



def clear_breakout_levels(ticker: str) -> None:



    with db_tx() as conn:



        conn.execute("DELETE FROM breakout_memory WHERE ticker = ?", (ticker,))



 



 



def load_update_id() -> Optional[int]:



    value = get_meta("last_update_id")



    if value is None:



        # One-time import from legacy updates.json if present.



        try:



            if os.path.exists(UPDATES_FILE):



                with open(UPDATES_FILE, "r", encoding="utf-8") as f:



                    data = json.load(f)



                    if isinstance(data, dict) and data.get("last_update_id") is not None:



                        return int(data["last_update_id"])



        except Exception:



            return None



        return None



    try:



        return int(value)



    except ValueError:



        return None



 



 



def save_update_id(update_id: int) -> None:



    set_meta("last_update_id", str(int(update_id)))



 



 



def is_update_processed(update_id: int) -> bool:



    conn = db_connect()



    try:



        row = conn.execute("SELECT 1 FROM processed_updates WHERE update_id = ?", (int(update_id),)).fetchone()



        return row is not None



    finally:



        conn.close()



 



 



def mark_update_processed_tx(conn: sqlite3.Connection, update_id: Optional[int], status: str) -> None:



    if update_id is None:



        return



    conn.execute(



        "INSERT OR IGNORE INTO processed_updates(update_id, processed_at, status) VALUES (?, ?, ?)",



        (int(update_id), now_ts(), status),



    )



    conn.execute(



        "INSERT INTO meta(key, value) VALUES ('last_update_id', ?) "



        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",



        (str(int(update_id)),),



    )



 



 



def mark_update_processed(update_id: int, status: str) -> None:



    with db_tx() as conn:



        mark_update_processed_tx(conn, update_id, status)



 



# -----------------------------------------------------------------------------



# LEGACY JSON MIGRATION



# -----------------------------------------------------------------------------



 



def try_load_json(path: str, default: Any) -> Any:



    try:



        if not os.path.exists(path):



            return default



        with open(path, "r", encoding="utf-8") as f:



            return json.load(f)



    except Exception as exc:



        print(f"[MIGRATION JSON ERROR] {path}: {exc}")



        return default



 



 



def migrate_legacy_json_once() -> None:



    if get_meta("legacy_json_migration_done") == "1":



        return



 



    with db_tx() as conn:



        pos_count = conn.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"]



        trade_count = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]



        signal_count = conn.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"]



 



        if os.path.exists(PORTFOLIO_FILE) and pos_count == 0:



            legacy_portfolio = try_load_json(PORTFOLIO_FILE, {})



            if isinstance(legacy_portfolio, dict):



                legacy_cash = legacy_portfolio.get("cash")



                if isinstance(legacy_cash, (int, float)) and math.isfinite(float(legacy_cash)) and float(legacy_cash) >= 0:



                    set_cash_tx(conn, float(legacy_cash))



                legacy_positions = legacy_portfolio.get("positions", {})



                if isinstance(legacy_positions, dict):



                    for ticker, pos in legacy_positions.items():



                        nticker = normalize_ticker(str(ticker))



                        if nticker is None or not isinstance(pos, dict):



                            continue



                        try:



                            entry = float(pos.get("price"))



                            shares = int(pos.get("shares"))



                            stop = float(pos.get("stop", entry * 0.95))



                            initial_stop = float(pos.get("initial_stop", stop))



                            highest = float(pos.get("highest", entry))



                            risk_per_share = pos.get("risk_per_share")



                            if risk_per_share is None:



                                risk_per_share = entry - initial_stop



                            risk_per_share = float(risk_per_share)



                            if not (shares > 0 and entry > 0 and stop > 0 and initial_stop > 0 and highest > 0):



                                continue



                            new_pos = {



                                "position_id": pos.get("position_id") or f"{nticker}_{int(time.time())}_{uuid.uuid4().hex[:8]}",



                                "strategy_version": pos.get("strategy_version") or STRATEGY_VERSION,



                                "shares": shares,



                                "price": entry,



                                "initial_stop": initial_stop,



                                "stop": stop,



                                "highest": max(highest, entry),



                                "partial_taken": bool(pos.get("partial_taken", False)),



                                "entry_time": float(pos.get("entry_time", now_ts())),



                                "atr": pos.get("atr"),



                                "risk_per_share": risk_per_share if risk_per_share > 0 else None,



                                "entry_data": pos.get("entry_data", {}),



                            }



                            upsert_position_tx(conn, nticker, new_pos)



                        except Exception as exc:



                            print(f"[MIGRATION POSITION SKIP] {ticker}: {exc}")



 



        if os.path.exists(TRADES_FILE) and trade_count == 0:



            legacy_trades = try_load_json(TRADES_FILE, [])



            if isinstance(legacy_trades, list):



                for item in legacy_trades:



                    if not isinstance(item, dict):



                        continue



                    try:



                        ticker = normalize_ticker(str(item.get("ticker", "")))



                        if ticker is None:



                            continue



                        shares = int(item.get("shares"))



                        entry = float(item.get("entry_price"))



                        exit_price = float(item.get("exit_price"))



                        profit = float(item.get("profit", (exit_price - entry) * shares))



                        if not (shares > 0 and entry > 0 and exit_price > 0 and math.isfinite(profit)):



                            continue



                        risk_per_share = item.get("risk_per_share")



                        r_multiple = item.get("r_multiple")



                        if risk_per_share is not None:



                            risk_per_share = float(risk_per_share)



                            if risk_per_share <= 0:



                                risk_per_share = None



                        if r_multiple is None and risk_per_share:



                            r_multiple = (exit_price - entry) / risk_per_share



                        trade = {



                            "id": str(item.get("id") or uuid.uuid4().hex),



                            "position_id": item.get("position_id"),



                            "strategy_version": item.get("strategy_version") or STRATEGY_VERSION,



                            "ticker": ticker,



                            "entry_price": entry,



                            "exit_price": exit_price,



                            "shares": shares,



                            "profit": round(profit, 2),



                            "entry_time": float(item.get("entry_time", now_ts())),



                            "exit_time": float(item.get("exit_time", now_ts())),



                            "duration_sec": int(item.get("duration_sec", 0)),



                            "exit_reason": item.get("exit_reason", "legacy"),



                            "entry_data": item.get("entry_data", {}),



                            "risk_per_share": risk_per_share,



                            "r_multiple": None if r_multiple is None else round(float(r_multiple), 4),



                        }



                        insert_trade_tx(conn, trade)



                    except Exception as exc:



                        print(f"[MIGRATION TRADE SKIP] {exc}")



 



        if os.path.exists(SIGNALS_FILE) and signal_count == 0:



            legacy_signals = try_load_json(SIGNALS_FILE, {})



            if isinstance(legacy_signals, dict):



                for ticker, signal in legacy_signals.items():



                    nticker = normalize_ticker(str(ticker))



                    if nticker is None:



                        continue



                    try:



                        if isinstance(signal, dict):



                            signal_time = float(signal.get("time", 0))



                            entry_data = signal.get("entry_data", {})



                        else:



                            signal_time = float(signal)



                            entry_data = {}



                        conn.execute(



                            "INSERT OR REPLACE INTO signals(ticker, time, entry_data_json) VALUES (?, ?, ?)",



                            (nticker, signal_time, json_dumps(entry_data)),



                        )



                    except Exception as exc:



                        print(f"[MIGRATION SIGNAL SKIP] {ticker}: {exc}")



 



        conn.execute(



            "INSERT INTO meta(key, value) VALUES ('legacy_json_migration_done', '1') "



            "ON CONFLICT(key) DO UPDATE SET value = '1'"



        )



 



# -----------------------------------------------------------------------------



# TELEGRAM



# -----------------------------------------------------------------------------



 



def send(msg: Any) -> None:



    text = str(msg)



    if not text:



        text = " "



 



    chunks = [text[i:i + MAX_TELEGRAM_MESSAGE] for i in range(0, len(text), MAX_TELEGRAM_MESSAGE)]



    for chunk in chunks:



        try:



            res = SESSION.post(



                f"https://api.telegram.org/bot{TOKEN}/sendMessage",



                data={"chat_id": CHAT_ID, "text": chunk},



                timeout=5,



            )



            if res.status_code >= 400:



                print(f"[TELEGRAM SEND HTTP ERROR] {res.status_code}: {res.text[:500]}")



                continue



            payload = res.json()



            if not payload.get("ok", False):



                print(f"[TELEGRAM SEND API ERROR] {payload}")



        except Exception as exc:



            print(f"[TELEGRAM SEND ERROR] {exc}")



 



 



def send_document(path: str, caption: str = "") -> None:



    try:



        with open(path, "rb") as f:



            res = SESSION.post(



                f"https://api.telegram.org/bot{TOKEN}/sendDocument",



                files={"document": f},



                data={"chat_id": CHAT_ID, "caption": caption},



                timeout=30,



            )



        if res.status_code >= 400:



            send(f"ERROR sending document: HTTP {res.status_code}")



            return



        payload = res.json()



        if not payload.get("ok", False):



            send(f"ERROR sending document: {payload}")



    except Exception as exc:



        send(f"ERROR sending document: {exc}")



 

def fmt_public_number(value: Any, decimals: int = 2) -> str:

    try:

        if value is None:

            return "n/a"



        return str(round(float(value), decimals))



    except Exception:

        return "n/a"



def public_signal_footer() -> str:

    return (

        "⚠️ Not financial advice. Educational / forward-test bot alert only.\n"

        "Do your own research and due diligence.\n"

        "Use your own risk tolerance, account size, tax situation, and execution plan.\n"

        "I may hold or trade this instrument. Signals can be wrong, delayed, or invalidated.\n"

        "Paid access, if any, is for automated alerts only — no profit guarantee or personalized advice."

    )





def public_channel_terms_text() -> str:

    return (

        "📌 CHANNEL DISCLAIMER\n\n"

        "This private channel shares automated trading-bot alerts for educational and forward-testing purposes only.\n\n"

        "Nothing posted here is financial advice, investment advice, personalized advice, portfolio management, "

        "or a guarantee of profit.\n\n"

        "I am not your financial adviser. I do not know your financial situation, account size, risk tolerance, "

        "tax situation, investment goals, or execution ability.\n\n"

        "Trading stocks, ETFs, crypto-related equities, and high-volatility assets can cause losses. "

        "Losses can happen because of gaps, slippage, delayed execution, bad data, earnings, news, market events, "

        "or system errors.\n\n"

        "All decisions are your own. Do your own research and due diligence before acting. "

        "Never risk money you cannot afford to lose.\n\n"

        "I may personally hold, buy, or sell instruments mentioned in this channel.\n\n"

        "Signals may be delayed, wrong, changed, or invalidated by market conditions. "

        "Past performance, paper-trading results, and forward-test results do not guarantee future results.\n\n"

        "Any paid access, if offered later, is only for access to automated bot alerts and educational tracking. "

        "It is not payment for guaranteed returns, personalized advice, or account management."

    )





def should_forward_public_position(pos: Dict[str, Any]) -> bool:

    """

    Only forward exits/partials to the public channel if the original entry

    was also sent to the public channel.



    This prevents old paper positions or manual/private positions from creating

    confusing public exit signals.

    """

    entry_data = pos.get("entry_data", {}) or {}

    return bool(entry_data.get("public_signal_sent"))



def send_public_signal(msg: Any) -> Tuple[bool, str]:

    """

    Send clean public trade signals to Telegram channel.



    This is intentionally separate from private admin Telegram messages.

    """

    if not PUBLIC_SIGNAL_ENABLED:

        return False, "PUBLIC_SIGNAL_ENABLED is off"



    if SIGNAL_CHANNEL_ID == 0:

        return False, "SIGNAL_CHANNEL_ID is missing or invalid"



    text = str(msg)



    if not text:

        text = " "



    chunks = [

        text[i:i + MAX_TELEGRAM_MESSAGE]

        for i in range(0, len(text), MAX_TELEGRAM_MESSAGE)

    ]



    for chunk in chunks:

        try:

            res = SESSION.post(

                f"https://api.telegram.org/bot{TOKEN}/sendMessage",

                json={

                    "chat_id": SIGNAL_CHANNEL_ID,

                    "text": chunk,

                    "disable_notification": PUBLIC_SIGNAL_SILENT,

                },

                timeout=5,

            )



            if res.status_code >= 400:

                err = f"HTTP {res.status_code}: {res.text[:500]}"

                print(f"[PUBLIC SIGNAL HTTP ERROR] {err}")

                return False, err



            payload = res.json()



            if not payload.get("ok", False):

                err = str(payload)

                print(f"[PUBLIC SIGNAL API ERROR] {err}")

                return False, err



        except Exception as exc:

            err = str(exc)

            print(f"[PUBLIC SIGNAL ERROR] {err}")

            return False, err



    return True, "sent"





def format_public_entry_signal(

    ticker: str,

    entry_data: Dict[str, Any]

) -> str:

    setup = entry_data.get("setup_type", "unknown")

    market = entry_data.get("market", "UNKNOWN")



    position_size = format_plain_pct(

        entry_data.get("position_size_pct"),

        decimals=0

    )



    trade_risk = format_plain_pct(

        entry_data.get("single_trade_risk_pct"),

        decimals=2

    )



    return (

        "📈 ENTRY SIGNAL\n\n"

        f"🏷️ Ticker: {ticker}\n"

        f"🌎 Market: {market_label(market)}\n"

        f"🧬 Sleeve: {sleeve_label(entry_data)}\n"

        f"⚙️ Setup: {setup_label(setup)}\n\n"



        f"🟢 ENTRY: {fmt_public_number(entry_data.get('signal_price'))}\n"

        f"🟡 MAX ENTRY LIMIT: {fmt_public_number(entry_data.get('max_valid_entry'))}\n"

        f"🔴 STOP/LOSS: {fmt_public_number(entry_data.get('stop'))}\n"

        f"📐 POSITION SIZE GUIDE: about {position_size} of account value\n"

        f"⚠️ Trade risk guide: about {trade_risk} of account value\n\n"



        f"📊 RSI: {fmt_public_number(entry_data.get('rsi'), 1)}\n"

        f"⭐ Score: {entry_data.get('score')}\n"

        f"📊 Volume ratio: {fmt_public_number(entry_data.get('volume_ratio'))}\n\n"



        f"{public_signal_footer()}"

    )





def format_public_partial_signal(

    ticker: str,

    price: float,

    trade: Dict[str, Any]

) -> str:

    gain_pct = pct_from_entry(

        trade.get("entry_price"),

        price

    )



    entry_data = trade.get("entry_data", {}) or {}

    partial_fraction = entry_data.get("partial_take_profit_fraction", PARTIAL_TAKE_PROFIT_FRACTION)

    try:
        partial_pct_label = int(round(float(partial_fraction) * 100))
    except Exception:
        partial_pct_label = int(round(PARTIAL_TAKE_PROFIT_FRACTION * 100))

    return (

        f"💰 PARTIAL TAKE-PROFIT (EXIT ~{partial_pct_label}% OF POSITION)\n\n"

        f"🏷️ Ticker: {ticker}\n"

        f"🧬 Sleeve: {sleeve_label(entry_data)}\n"

        f"📤 Action: take partial profit on ~{partial_pct_label}% of your own position\n"

        f"💵 Partial exit price: {fmt_public_number(price)} ({format_pct(gain_pct)})\n"

        f"🎯 R multiple: {fmt_public_number(trade.get('r_multiple'))}\n\n"



        "Bot status: partial-profit condition triggered.\n"

        "Review your own plan before taking any action.\n\n"



        f"{public_signal_footer()}"

    )





def format_public_exit_signal(

    ticker: str,

    price: float,

    trade: Dict[str, Any],

    reason: str

) -> str:

    reason_label = {

        "stop": "Stop / risk exit",

        "manual": "Manual exit",

        "time_stop": "Time stop",

        "max_hold": "Max holding period",

        "bear_regime_exit": "Bear regime cooled",

    }.get(str(reason).lower(), str(reason))



    exit_pct = pct_from_entry(

        trade.get("entry_price"),

        price

    )



    return (

        "📉 EXIT SIGNAL\n\n"

        f"🏷️ Ticker: {ticker}\n"

        f"🧬 Sleeve: {sleeve_label(trade.get('entry_data', {}) or {})}\n"

        f"📌 Reason: {reason_label}\n"

        "📤 Action: exit your remaining position according to your own sizing\n"

        f"💵 Exit price: {fmt_public_number(price)} ({format_pct(exit_pct)})\n"

        f"🎯 R multiple: {fmt_public_number(trade.get('r_multiple'))}\n\n"



        "Bot status: exit condition triggered.\n"

        "Review your own plan before taking any action.\n\n"



        f"{public_signal_footer()}"

    )

 



def get_updates() -> None:



    last_update_id = load_update_id()



    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"



    if last_update_id is not None:



        url += f"?offset={last_update_id + 1}"



 



    try:



        res = SESSION.get(url, timeout=5)



        res.raise_for_status()



        payload = res.json()



        if not payload.get("ok", False):



            print(f"[TELEGRAM GETUPDATES API ERROR] {payload}")



            return



    except Exception as exc:



        print(f"[TELEGRAM GETUPDATES ERROR] {exc}")



        return



 



    for update in payload.get("result", []):



        update_id = update.get("update_id")



        if update_id is None:



            continue



 



        if is_update_processed(int(update_id)):



            save_update_id(int(update_id))



            continue



 



        message = update.get("message") or update.get("edited_message")



        if not message:



            mark_update_processed(int(update_id), "ignored_no_message")



            continue



 



        chat_id = message.get("chat", {}).get("id")



        if chat_id != CHAT_ID:



            print(f"[UNAUTHORIZED TELEGRAM COMMAND] chat_id={chat_id} update_id={update_id}")



            mark_update_processed(int(update_id), "unauthorized")



            continue



 



        text = message.get("text") or ""



        try:



            handle_command(text, update_id=int(update_id))



            # handle_command marks trade-mutating updates inside the same transaction.



            if not is_update_processed(int(update_id)):



                mark_update_processed(int(update_id), "processed")



        except Exception as exc:



            logger.exception(

                f"[COMMAND ERROR] update_id={update_id} text={text!r}: {exc}"

            )



            traceback.print_exc()



            send(f"ERROR processing command: {exc}")



            # Mark failed to avoid infinite crash loops; transactional trade commands roll back on error.



            if not is_update_processed(int(update_id)):



                mark_update_processed(int(update_id), "failed")



 



# -----------------------------------------------------------------------------



# DATA PROVIDER



# -----------------------------------------------------------------------------



 



from typing import Union



def request_json(

    url: str,

    timeout: Union[int, Tuple[int, int]] = 5,

    context: str = "",

    retries: int = 1,

) -> Any:



    last_exc: Optional[Exception] = None



    for attempt in range(retries + 1):



        try:



            res = SESSION.get(url, timeout=timeout)



            res.raise_for_status()



            return res.json()



        except Exception as exc:



            last_exc = exc



            if attempt < retries:



                time.sleep(0.5 * (attempt + 1))



    if context:



        raise RuntimeError(f"{context}: {last_exc}") from last_exc



    raise RuntimeError(str(last_exc)) from last_exc



 



 



def get_prices_batch(tickers: List[str]) -> Dict[str, float]:



    prices: Dict[str, float] = {}



    clean_tickers = []



    seen = set()



    for ticker in tickers:



        nticker = normalize_ticker(str(ticker))



        if nticker and nticker not in seen:



            clean_tickers.append(nticker)



            seen.add(nticker)



 



    if not clean_tickers:



        return prices



 



    try:



        symbols = ",".join(clean_tickers)



        url = f"{FMP_BASE}/batch-quote?symbols={symbols}&apikey={FMP_API_KEY}"



        data = request_json(url, timeout=5, context="batch quote", retries=1)



 



        if not isinstance(data, list):



            print(f"[BATCH PRICE BAD SCHEMA] {data}")



            return prices



        if len(data) == 0:

            print("[BATCH PRICE EMPTY RESPONSE]")

            return prices



        for item in data:



            ticker = normalize_ticker(str(item.get("symbol", "")))



            raw_price = item.get("price")



            try:



                price = float(raw_price)



            except (TypeError, ValueError):



                continue



            if ticker and is_finite_positive(price):



                prices[ticker] = price



                print(f"💲 PRICE | {ticker} = {price}")



 



    except Exception as exc:



        logger.warning(f"[BATCH PRICE ERROR] {exc}")



 



    return prices



 



 



def get_historical(ticker: str, limit: int = 120) -> Optional[pd.DataFrame]:



    nticker = normalize_ticker(ticker)



    if nticker is None:



        return None



 



    try:



        url = f"{FMP_BASE}/historical-price-eod/full?symbol={nticker}&apikey={FMP_API_KEY}"



        data = request_json(url, timeout=(3, 10), context=f"historical {nticker}", retries=1)



 



        if not isinstance(data, list) or len(data) == 0:



            print(f"[HIST EMPTY] {nticker}")



            return None



 



        df = pd.DataFrame(data)



        df = df.rename(



            columns={



                "open": "Open",



                "high": "High",



                "low": "Low",



                "close": "Close",



                "volume": "Volume",



            }



        )



 



        required = ["date", "Open", "High", "Low", "Close", "Volume"]



        missing = [c for c in required if c not in df.columns]



        if missing:



            print(f"[HIST BAD SCHEMA] {nticker} missing={missing}")



            return None



 



        df["date"] = pd.to_datetime(df["date"], errors="coerce")



        for col in ["Open", "High", "Low", "Close", "Volume"]:



            df[col] = pd.to_numeric(df[col], errors="coerce")



 



        df = df.dropna(subset=required)



        df = df.sort_values("date").tail(limit).reset_index(drop=True)



 



        if df.empty:

            print(f"[HIST NO VALID ROWS] {nticker}")

            return None



        if len(df) < 30:

            print(f"[HIST TOO SHORT] {nticker}")

            return None



 



        print(



            f"[📚 DATA OK] {nticker} | "



            f"date={df.iloc[-1]['date'].date().isoformat()} | "



            f"rows={len(df)} | last={df['Close'].iloc[-1]:.2f}"



        )



        return df



 



    except Exception as exc:



        logger.warning(f"[HIST ERROR] {nticker}: {exc}")



        return None



def get_intraday_5min(ticker: str) -> Optional[pd.DataFrame]:

    nticker = normalize_ticker(ticker)



    if nticker is None:

        return None



    try:

        url = f"{FMP_BASE}/historical-chart/5min?symbol={nticker}&apikey={FMP_API_KEY}"



        data = request_json(

            url,

            timeout=(3, 10),

            context=f"intraday 5min {nticker}",

            retries=1

        )



        if not isinstance(data, list) or len(data) == 0:

            print(f"[INTRADAY EMPTY] {nticker}")

            return None



        df = pd.DataFrame(data)



        df = df.rename(

            columns={

                "open": "Open",

                "high": "High",

                "low": "Low",

                "close": "Close",

                "volume": "Volume",

            }

        )



        required = ["date", "Open", "High", "Low", "Close", "Volume"]



        missing = [c for c in required if c not in df.columns]



        if missing:

            print(f"[INTRADAY BAD SCHEMA] {nticker} missing={missing}")

            return None



        df["date"] = pd.to_datetime(df["date"], errors="coerce")



        for col in ["Open", "High", "Low", "Close", "Volume"]:

            df[col] = pd.to_numeric(df[col], errors="coerce")



        df = df.dropna(subset=required)

        df = df.sort_values("date").reset_index(drop=True)



        if df.empty:

            print(f"[INTRADAY NO VALID ROWS] {nticker}")

            return None



        return df



    except Exception as exc:

        logger.warning(f"[INTRADAY ERROR] {nticker}: {exc}")

        return None





def build_live_daily_df(ticker: str, limit: int = 120) -> Optional[pd.DataFrame]:

    """

    Build a daily dataframe where today's candle is synthesized from 5-minute intraday bars.



    Used for near-close scans so the bot does not rely on yesterday's EOD candle.

    """

    nticker = normalize_ticker(ticker)



    if nticker is None:

        return None



    hist = get_historical(nticker, limit=limit + 5)



    if hist is None or hist.empty:

        return None



    intraday = get_intraday_5min(nticker)



    if intraday is None or intraday.empty:

        print(f"[LIVE DAILY SKIP] {nticker} no intraday data")

        return None



    today = ny_now().date()



    today_rows = intraday[intraday["date"].dt.date == today].copy()



    if today_rows.empty:

        print(f"[LIVE DAILY SKIP] {nticker} no intraday rows for {today}")

        return None



    # Keep only regular-session bars: 09:30–16:00 New York time.

    bar_minutes = today_rows["date"].dt.hour * 60 + today_rows["date"].dt.minute



    today_rows = today_rows[

        (bar_minutes >= (9 * 60 + 30)) &

        (bar_minutes <= (16 * 60))

    ].copy()



    if today_rows.empty:

        print(f"[LIVE DAILY SKIP] {nticker} no regular-session intraday rows for {today}")

        return None



    live_bar = {

        "date": pd.Timestamp(today.isoformat()),

        "Open": float(today_rows["Open"].iloc[0]),

        "High": float(today_rows["High"].max()),

        "Low": float(today_rows["Low"].min()),

        "Close": float(today_rows["Close"].iloc[-1]),

        "Volume": float(today_rows["Volume"].sum()),

    }



    # Remove any existing row for today from EOD data, then append synthetic current-day row.

    hist = hist[pd.to_datetime(hist["date"]).dt.date < today].copy()

    hist = hist.tail(limit - 1)



    live_df = pd.concat(

        [hist, pd.DataFrame([live_bar])],

        ignore_index=True

    )



    live_df = live_df.sort_values("date").tail(limit).reset_index(drop=True)



    print(

        f"[LIVE DAILY OK] {nticker} | "

        f"date={today.isoformat()} | "

        f"close={live_bar['Close']:.2f} | "

        f"high={live_bar['High']:.2f} | "

        f"low={live_bar['Low']:.2f} | "

        f"volume={int(live_bar['Volume'])}"

    )



    return live_df





def get_signal_dataframe(ticker: str, limit: int = 120) -> Optional[pd.DataFrame]:

    current_ny = ny_now()

    minutes = current_ny.hour * 60 + current_ny.minute



    # From 15:45 onward on market days, use intraday data to synthesize today's daily candle.

    # This avoids depending on the EOD endpoint being refreshed immediately.

    if is_market_weekday(current_ny) and minutes >= (15 * 60 + 45):

        return build_live_daily_df(ticker, limit=limit)



    return get_historical(ticker, limit=limit)

 



def is_daily_data_current(df: pd.DataFrame) -> bool:



    if df is None or df.empty or "date" not in df.columns:

        return False



    try:



        last_date = pd.to_datetime(df.iloc[-1]["date"]).date()



        current_ny = ny_now()

        today = current_ny.date()



        schedule = NYSE.schedule(

            start_date=today - timedelta(days=10),

            end_date=today

        )



        if schedule.empty:

            return False



        sessions = [d.date() for d in schedule.index]



        expected_session = sessions[-1]



        # Before the near-close scan window, expect previous completed session.

        # During/after near-close, today's synthetic intraday-built daily candle is acceptable.

        if expected_session == today:

            minutes = current_ny.hour * 60 + current_ny.minute



            if minutes < (15 * 60 + 45):

                if len(sessions) >= 2:

                    expected_session = sessions[-2]



        print(

            f"[FRESH CHECK] "

            f"last={last_date} "

            f"expected={expected_session} "

            f"ny={current_ny.strftime('%Y-%m-%d %H:%M')}"

        )



        return last_date >= expected_session



    except Exception as exc:

        print(f"[STALE CHECK ERROR] {exc}")

        return False



def earnings_status(ticker: str, days: int = 7) -> str:



    """Return SOON, CLEAR, or UNKNOWN. UNKNOWN should generally fail closed."""



    nticker = normalize_ticker(ticker)



    if nticker is None:



        return "UNKNOWN"



 



    today = ny_now().date()



    end_date = today + timedelta(days=days)



 



    endpoints = [



        # Documented broad-calendar style endpoint.



        f"{FMP_BASE}/earnings-calendar?from={today.isoformat()}&to={end_date.isoformat()}&apikey={FMP_API_KEY}",



        # Compatibility with prior script endpoint, if available on the user's plan.



        f"{FMP_BASE}/earning-calendar-confirmed?symbol={nticker}&apikey={FMP_API_KEY}",



    ]



 



    any_valid_response = False



 



    for url in endpoints:



        try:



            data = request_json(url, timeout=5, context=f"earnings {nticker}", retries=1)



            if not isinstance(data, list):



                continue



            any_valid_response = True



 



            for item in data:



                if not isinstance(item, dict):



                    continue



                symbol = normalize_ticker(str(item.get("symbol", nticker)))



                if symbol != nticker:



                    continue



                date_str = item.get("date") or item.get("fiscalDateEnding")



                if not date_str:



                    continue



                try:



                    earnings_date = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()



                except ValueError:



                    continue



                diff = (earnings_date - today).days



                if 0 <= diff <= days:



                    print(f"[EARNINGS SOON] {nticker} -> {earnings_date.isoformat()}")



                    return "SOON"



 



            # If broad calendar endpoint returned valid data and ticker wasn't found, this is clear.



            if "earnings-calendar?" in url:



                return "CLEAR"



 



        except Exception as exc:



            print(f"[EARNINGS ERROR] {nticker}: {exc}")



 



    return "CLEAR" if any_valid_response else "UNKNOWN"



 



# -----------------------------------------------------------------------------



# INDICATORS



# -----------------------------------------------------------------------------



 



def rsi(series: pd.Series, period: int = 14) -> pd.Series:



    delta = series.diff()



    gain = delta.clip(lower=0).rolling(period).mean()



    loss = -delta.clip(upper=0).rolling(period).mean()



    loss = loss.replace(0, 1e-9)



    rs = gain / loss



    return 100 - (100 / (1 + rs))



 



 



def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:



    tr = pd.concat(



        [



            df["High"] - df["Low"],



            (df["High"] - df["Close"].shift()).abs(),



            (df["Low"] - df["Close"].shift()).abs(),



        ],



        axis=1,



    ).max(axis=1)



    return tr.rolling(period).mean()



 



# -----------------------------------------------------------------------------



# MARKET / RISK HELPERS



# -----------------------------------------------------------------------------



 



def pct_change_over(series: pd.Series, bars: int) -> Optional[float]:
    try:
        clean = series.dropna()

        if len(clean) <= bars:
            return None

        old = float(clean.iloc[-bars - 1])
        new = float(clean.iloc[-1])

        if old <= 0:
            return None

        return (new / old) - 1

    except Exception:
        return None


def rolling_slope_positive(series: pd.Series, lookback: int = 10) -> bool:
    try:
        clean = series.dropna()

        if len(clean) <= lookback:
            return False

        return float(clean.iloc[-1]) > float(clean.iloc[-lookback])

    except Exception:
        return False


def close_location(df: pd.DataFrame) -> float:
    try:
        high = float(df["High"].iloc[-1])
        low = float(df["Low"].iloc[-1])
        close = float(df["Close"].iloc[-1])

        if high <= low:
            return 0.5

        return max(0.0, min(1.0, (close - low) / (high - low)))

    except Exception:
        return 0.5


def market_regime_details() -> Dict[str, Any]:
    """
    V2.8 market regime score.

    The score is still simple, but the frames use enough lookback for MA100/MA200
    filters used by the VCP-only leader contraction-breakout strategy.
    """
    frames: Dict[str, Optional[pd.DataFrame]] = {}

    for symbol in ["SPY", "QQQ", "IWM", "SMH"]:
        frames[symbol] = get_signal_dataframe(symbol, limit=260)

    score = 0
    notes: List[str] = []

    for symbol in ["SPY", "QQQ"]:
        df = frames.get(symbol)

        if df is None or df.empty or len(df) < 60:
            notes.append(f"{symbol}:no_data")
            continue

        close = df["Close"]
        ma20 = close.rolling(20).mean().iloc[-1]
        ma50 = close.rolling(50).mean().iloc[-1]

        if pd.isna(ma20) or pd.isna(ma50):
            notes.append(f"{symbol}:ma_nan")
            continue

        if float(close.iloc[-1]) > float(ma50):
            score += 1

        if float(ma20) > float(ma50):
            score += 1

        if rolling_slope_positive(close.rolling(50).mean(), lookback=10):
            score += 1

    for symbol in ["IWM", "SMH"]:
        df = frames.get(symbol)

        if df is None or df.empty or len(df) < 60:
            continue

        close = df["Close"]
        ma50 = close.rolling(50).mean().iloc[-1]

        if not pd.isna(ma50) and float(close.iloc[-1]) > float(ma50):
            score += 1

    if score >= 6:
        condition = "BULL"
    elif score <= 2:
        condition = "BEAR"
    else:
        condition = "UNCERTAIN"

    return {
        "condition": condition,
        "score": score,
        "max_score": 8,
        "notes": notes,
        "frames": frames,
    }


def market_condition() -> str:
    try:
        return str(market_regime_details().get("condition", "UNCERTAIN"))

    except Exception as exc:
        print(f"[MARKET ERROR] {exc}")
        return "UNCERTAIN"



def frame_last_close_ma(df: Optional[pd.DataFrame], ma_period: int) -> Tuple[Optional[float], Optional[float]]:
    try:
        if df is None or df.empty or len(df) < ma_period + 5:
            return None, None

        close = df["Close"].dropna()
        ma = close.rolling(ma_period).mean().iloc[-1]

        if pd.isna(ma):
            return None, None

        return float(close.iloc[-1]), float(ma)

    except Exception:
        return None, None


def bear_regime_details(market_details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Bear sleeve regime score, max 60.

    This is separate from the long VCP market score. It looks for broad-market
    risk-off conditions before allowing inverse ETF signals.
    """
    frames: Dict[str, Optional[pd.DataFrame]] = {}

    if market_details and isinstance(market_details.get("frames"), dict):
        frames.update(market_details.get("frames", {}))

    for symbol in ["SPY", "QQQ", "IWM", "SMH"]:
        if frames.get(symbol) is None:
            frames[symbol] = get_signal_dataframe(symbol, limit=260)

    score = 0
    notes: List[str] = []

    for symbol in ["SPY", "QQQ"]:
        df = frames.get(symbol)

        if df is None or df.empty or len(df) < 220:
            notes.append(f"{symbol}:no_data")
            continue

        close = df["Close"].dropna()
        ma20 = close.rolling(20).mean().iloc[-1]
        ma50 = close.rolling(50).mean().iloc[-1]
        ma100 = close.rolling(100).mean().iloc[-1]
        ma200 = close.rolling(200).mean().iloc[-1]
        last = float(close.iloc[-1])

        if not pd.isna(ma50) and last < float(ma50):
            score += 6
        if not pd.isna(ma20) and not pd.isna(ma50) and float(ma20) < float(ma50):
            score += 5
        if not pd.isna(ma100) and last < float(ma100):
            score += 4
        if not pd.isna(ma200) and last < float(ma200):
            score += 8

        ret20 = pct_change_over(close, 20)
        if ret20 is not None and ret20 < 0:
            score += 3

    for symbol in ["IWM", "SMH"]:
        df = frames.get(symbol)

        if df is None or df.empty or len(df) < 60:
            continue

        close = df["Close"].dropna()
        ma50 = close.rolling(50).mean().iloc[-1]

        if not pd.isna(ma50) and float(close.iloc[-1]) < float(ma50):
            score += 4

    active = score >= BEAR_ENTRY_SCORE
    exit_pressure_low = score <= BEAR_EXIT_SCORE

    return {
        "active": active,
        "score": score,
        "max_score": 60,
        "exit_pressure_low": exit_pressure_low,
        "entry_score_required": BEAR_ENTRY_SCORE,
        "exit_score": BEAR_EXIT_SCORE,
        "notes": notes,
        "frames": frames,
    }




def compute_equity_snapshot_data() -> Dict[str, float]:
    refresh_portfolio()

    swing_positions = portfolio["positions"]
    core_positions = load_core_positions() if CORE_LEDGER_ENABLED else {}
    all_tickers = list(dict.fromkeys(list(swing_positions.keys()) + list(core_positions.keys())))
    prices = get_prices_batch(all_tickers)

    swing_value = 0.0
    for ticker, pos in swing_positions.items():
        price = prices.get(ticker, pos["price"])
        swing_value += float(price) * int(pos["shares"])

    core_value = 0.0
    core_cost = 0.0
    for ticker, pos in core_positions.items():
        price = prices.get(ticker, pos.get("avg_entry_price", 0))
        core_value += float(price) * float(pos["shares"])
        core_cost += float(pos.get("cost_basis", 0) or 0)

    positions_value = swing_value + core_value
    equity = float(portfolio["cash"]) + positions_value
    return {
        "cash": round(float(portfolio["cash"]), 2),
        "positions_value": round(positions_value, 2),
        "swing_positions_value": round(swing_value, 2),
        "core_positions_value": round(core_value, 2),
        "core_cost_basis": round(core_cost, 2),
        "core_unrealized_profit": round(core_value - core_cost, 2),
        "equity": round(equity, 2),
    }

def save_equity_snapshot() -> None:



    snapshot = compute_equity_snapshot_data()



    snapshot_date = ny_date_str()



    with db_tx() as conn:



        conn.execute(



            "INSERT OR REPLACE INTO equity_snapshots(snapshot_date, time, equity, cash, positions_value) "



            "VALUES (?, ?, ?, ?, ?)",



            (snapshot_date, now_ts(), snapshot["equity"], snapshot["cash"], snapshot["positions_value"]),



        )



        conn.execute(



            "INSERT INTO meta(key, value) VALUES ('last_equity_snapshot_date', ?) "



            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",



            (snapshot_date,),



        )



 



 




def open_risk_details() -> Dict[str, float]:
    refresh_portfolio()
    positions = portfolio["positions"]
    prices = get_prices_batch(list(positions.keys()))
    snapshot = compute_equity_snapshot_data()
    equity = float(snapshot.get("equity", 0.0) or 0.0)

    initial_risk_dollars = 0.0
    current_stop_risk_dollars = 0.0

    for ticker, pos in positions.items():
        current_price = prices.get(ticker, pos["price"])
        risk_per_share = pos.get("risk_per_share")
        if isinstance(risk_per_share, (int, float)) and risk_per_share > 0:
            initial_risk_dollars += float(risk_per_share) * int(pos["shares"])
        current_stop_risk = max(0.0, float(current_price) - float(pos.get("stop", current_price)))
        current_stop_risk_dollars += current_stop_risk * int(pos["shares"])

    if equity <= 0:
        return {
            "equity": 0.0,
            "initial_risk_dollars": 0.0,
            "current_stop_risk_dollars": 0.0,
            "initial_risk_pct": 0.0,
            "current_stop_risk_pct": 0.0,
            "core_value": float(snapshot.get("core_positions_value", 0.0) or 0.0),
            "swing_value": float(snapshot.get("swing_positions_value", 0.0) or 0.0),
        }

    return {
        "equity": round(equity, 2),
        "initial_risk_dollars": round(initial_risk_dollars, 2),
        "current_stop_risk_dollars": round(current_stop_risk_dollars, 2),
        "initial_risk_pct": initial_risk_dollars / equity,
        "current_stop_risk_pct": current_stop_risk_dollars / equity,
        "core_value": float(snapshot.get("core_positions_value", 0.0) or 0.0),
        "swing_value": float(snapshot.get("swing_positions_value", 0.0) or 0.0),
    }

def get_withdrawal_hwm() -> Optional[float]:

    value = get_meta("withdrawal_high_water_mark")



    if value is None:

        return None



    try:

        hwm = float(value)

        return hwm if math.isfinite(hwm) and hwm > 0 else None

    except ValueError:

        return None





def get_withdrawal_hwm_initialized_at() -> Optional[float]:

    value = get_meta("withdrawal_hwm_initialized_at")



    if value is None:

        return None



    try:

        ts = float(value)

        return ts if math.isfinite(ts) and ts > 0 else None

    except ValueError:

        return None





def set_withdrawal_hwm(value: float) -> None:

    if not math.isfinite(value) or value <= 0:

        raise ValueError("Withdrawal high-water mark must be positive")



    set_meta("withdrawal_high_water_mark", str(round(value, 2)))

    set_meta("withdrawal_hwm_initialized_at", str(now_ts()))





def auto_initialize_withdrawal_hwm_if_needed() -> None:

    """

    One-time automatic baseline.



    This prevents the bot from immediately suggesting withdrawals from old/legacy profits.

    Future withdrawal signals will only use profits above this baseline.

    """

    existing = get_withdrawal_hwm()



    if existing is not None:

        return



    snapshot = compute_equity_snapshot_data()

    equity = float(snapshot["equity"])



    if equity <= 0:

        return



    set_withdrawal_hwm(equity)



    audit(

        "WITHDRAWAL_HWM_INITIALIZED",

        f"equity={equity}"

    )



    print(f"[WITHDRAWAL HWM INIT] equity={round(equity, 2)}")





def get_last_withdrawal_time() -> Optional[float]:

    conn = db_connect()



    try:

        row = conn.execute(

            "SELECT MAX(time) AS t FROM withdrawals"

        ).fetchone()



        if row is None or row["t"] is None:

            return None



        return float(row["t"])



    finally:

        conn.close()





def load_withdrawals() -> List[Dict[str, Any]]:

    conn = db_connect()



    try:

        rows = conn.execute(

            "SELECT * FROM withdrawals ORDER BY time ASC"

        ).fetchall()



        return [

            {

                "id": row["id"],

                "time": float(row["time"]),

                "amount": float(row["amount"]),

                "equity_before": float(row["equity_before"]),

                "cash_before": float(row["cash_before"]),

                "cash_after": float(row["cash_after"]),

                "high_water_mark_before": float(row["high_water_mark_before"]),

                "high_water_mark_after": float(row["high_water_mark_after"]),

                "note": row["note"],

            }

            for row in rows

        ]



    finally:

        conn.close()





def withdrawal_funny_note() -> str:

    jokes = [

        "The profits have requested a small vacation before the market asks for them back.",

        "Your bot says: take some cookies off the table before the market eats the plate.",

        "Time to pay the human. The robot has been working overtime.",

        "Profit detected. Please convert a tiny piece of stress into actual life.",

        "The account grew. Your coffee budget has officially filed a withdrawal request.",

    ]



    idx = int(now_ts()) % len(jokes)

    return jokes[idx]





def compute_withdrawal_plan() -> Dict[str, Any]:

    snapshot = compute_equity_snapshot_data()



    equity = float(snapshot["equity"])

    cash = float(snapshot["cash"])



    hwm = get_withdrawal_hwm()



    if hwm is None:

        return {

            "initialized": False,

            "equity": equity,

            "cash": cash,

            "high_water_mark": None,

            "profit_above_hwm": 0.0,

            "rate": 0.0,

            "gross_suggested": 0.0,

            "cash_cap": max(0.0, cash - WITHDRAWAL_MIN_CASH_AFTER),

            "suggested": 0.0,

            "eligible": False,

            "reason": "Withdrawal high-water mark is not initialized.",

            "days_since_clock": None,

        }



    profit_above_hwm = equity - hwm



    if equity < WITHDRAWAL_BUILD_PHASE_EQUITY:

        rate = WITHDRAWAL_BUILD_PHASE_RATE

        phase = "BUILD"

    else:

        rate = WITHDRAWAL_PROFIT_RATE

        phase = "NORMAL"



    gross_suggested = max(0.0, profit_above_hwm * rate)

    cash_cap = max(0.0, cash - WITHDRAWAL_MIN_CASH_AFTER)

    suggested = min(gross_suggested, cash_cap)



    last_withdrawal_time = get_last_withdrawal_time()

    clock_start = last_withdrawal_time or get_withdrawal_hwm_initialized_at()



    days_since_clock = None



    if clock_start is not None:

        days_since_clock = (now_ts() - clock_start) / 86400



    eligible = True

    reason = "Eligible"



    if profit_above_hwm <= 0:

        eligible = False

        reason = "No profit above high-water mark."



    elif profit_above_hwm < WITHDRAWAL_MIN_PROFIT:

        eligible = False

        reason = (

            f"Profit above high-water mark is below minimum "

            f"{format_money(WITHDRAWAL_MIN_PROFIT)}."

        )



    elif suggested < WITHDRAWAL_MIN_AMOUNT:

        eligible = False

        reason = (

            f"Suggested withdrawal is below minimum "

            f"{format_money(WITHDRAWAL_MIN_AMOUNT)} or cash cap is too low."

        )



    elif days_since_clock is not None and days_since_clock < WITHDRAWAL_REVIEW_DAYS:

        eligible = False

        reason = (

            f"Review period not reached. "

            f"{round(days_since_clock, 1)} days passed; "

            f"target is {WITHDRAWAL_REVIEW_DAYS} days."

        )



    return {

        "initialized": True,

        "phase": phase,

        "equity": round(equity, 2),

        "cash": round(cash, 2),

        "high_water_mark": round(hwm, 2),

        "profit_above_hwm": round(profit_above_hwm, 2),

        "rate": rate,

        "gross_suggested": round(gross_suggested, 2),

        "cash_cap": round(cash_cap, 2),

        "suggested": round(suggested, 2),

        "eligible": eligible,

        "reason": reason,

        "days_since_clock": None if days_since_clock is None else round(days_since_clock, 1),

    }





def record_withdrawal(

    amount: float,

    note: str = "",

    update_id: Optional[int] = None

) -> Tuple[bool, str]:



    if not math.isfinite(amount) or amount <= 0:

        return False, "Withdrawal amount must be positive and finite."



    refresh_portfolio()



    snapshot = compute_equity_snapshot_data()



    equity_before = float(snapshot["equity"])

    cash_before = float(snapshot["cash"])



    if amount > cash_before:

        return False, "Withdrawal amount is larger than available cash."



    if cash_before - amount < WITHDRAWAL_MIN_CASH_AFTER:

        return (

            False,

            f"Withdrawal rejected: cash after withdrawal would fall below "

            f"{format_money(WITHDRAWAL_MIN_CASH_AFTER)}."

        )



    hwm_before = get_withdrawal_hwm()



    if hwm_before is None:

        return False, "Withdrawal high-water mark is not initialized."



    # After withdrawal, keep HWM at the pre-withdrawal equity peak.

    hwm_after = max(hwm_before, equity_before)



    cash_after = cash_before - amount



    with db_tx() as conn:

        set_cash_tx(conn, cash_after)



        conn.execute(

            """

            INSERT INTO withdrawals(

                id, time, amount, equity_before, cash_before, cash_after,

                high_water_mark_before, high_water_mark_after, note

            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)

            """,

            (

                uuid.uuid4().hex,

                now_ts(),

                round(amount, 2),

                round(equity_before, 2),

                round(cash_before, 2),

                round(cash_after, 2),

                round(hwm_before, 2),

                round(hwm_after, 2),

                str(note or ""),

            ),

        )



        conn.execute(

            "INSERT INTO meta(key, value) VALUES ('withdrawal_high_water_mark', ?) "

            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",

            (str(round(hwm_after, 2)),),

        )



        mark_update_processed_tx(conn, update_id, "processed_withdrawal")



    refresh_portfolio()



    audit(

        "WITHDRAWAL",

        f"amount={amount} equity_before={equity_before} "

        f"cash_before={cash_before} cash_after={cash_after} "

        f"hwm_before={hwm_before} hwm_after={hwm_after}"

    )



    return True, (

        f"🏦 WITHDRAWAL RECORDED\n\n"

        f"💸 Amount: {format_money(amount)}\n"

        f"💼 Equity before: {format_money(equity_before)}\n"

        f"💵 Cash before: {format_money(cash_before)}\n"

        f"💵 Cash after: {format_money(cash_after)}\n"

        f"🏔️ New high-water mark: {format_money(hwm_after)}"

    )





def maybe_send_withdrawal_signal() -> None:

    """

    Automatic withdrawal alert.



    Checks once per market day after 16:05 NY time.

    If eligible, sends a Telegram signal.

    Does not spam: repeated alerts are limited by WITHDRAWAL_ALERT_REPEAT_DAYS.

    """

    current_ny = ny_now()



    if not is_market_weekday(current_ny):

        return



    minutes = current_ny.hour * 60 + current_ny.minute



    # Check after market close, so equity/quotes are more stable.

    if minutes < (16 * 60 + 5):

        return



    today = current_ny.date().isoformat()



    if get_meta("last_withdrawal_check_date") == today:

        return



    set_meta("last_withdrawal_check_date", today)



    try:

        plan = compute_withdrawal_plan()



        if not plan.get("initialized"):

            print("[WITHDRAWAL CHECK] HWM not initialized")

            return



        if not plan.get("eligible"):

            print(f"[WITHDRAWAL CHECK] Not eligible: {plan.get('reason')}")

            return



        last_alert_raw = get_meta("last_withdrawal_alert_ts")

        last_alert_ts = None



        if last_alert_raw:

            try:

                last_alert_ts = float(last_alert_raw)

            except ValueError:

                last_alert_ts = None



        if last_alert_ts is not None:

            days_since_alert = (now_ts() - last_alert_ts) / 86400



            if days_since_alert < WITHDRAWAL_ALERT_REPEAT_DAYS:

                print(

                    "[WITHDRAWAL CHECK] Eligible but alert recently sent "

                    f"{round(days_since_alert, 1)} days ago"

                )

                return



        set_meta("last_withdrawal_alert_ts", str(now_ts()))



        send(

            "🏦 WITHDRAWAL SIGNAL\n\n"

            "🎉 Time to pay yourself.\n\n"

            f"📊 Phase: {plan['phase']}\n"

            f"💼 Equity: {format_money(plan['equity'])}\n"

            f"💵 Cash: {format_money(plan['cash'])}\n"

            f"🏔️ High-water mark: {format_money(plan['high_water_mark'])}\n"

            f"📈 Profit above HWM: {format_money(plan['profit_above_hwm'])}\n\n"

            f"📤 Rate: {round(plan['rate'] * 100, 2)}%\n"

            f"✅ Suggested withdrawal: {format_money(plan['suggested'])}\n\n"

            f"😂 Bot note:\n{withdrawal_funny_note()}\n\n"

            f"After you manually withdraw it, send:\n"

            f"withdrawdone {round(plan['suggested'], 2)}"

        )



    except Exception as exc:

        logger.exception(f"[WITHDRAWAL SIGNAL ERROR] {exc}")

        print(f"[WITHDRAWAL SIGNAL ERROR] {exc}")

 






def clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def wealth_asset_cluster(ticker: str) -> str:
    return WEALTH_ASSET_CLUSTERS.get(str(ticker).upper(), "other")


def current_drawdown_details() -> Dict[str, Any]:
    """Current equity drawdown from highest known equity snapshot.

    Uses saved equity snapshots plus the current live equity estimate. This is a
    guardrail, not a broker-grade NAV engine.
    """
    snapshot = compute_equity_snapshot_data()
    current_equity = float(snapshot.get("equity", 0.0) or 0.0)

    high_equity = current_equity
    since_date = ny_date_str()

    conn = db_connect()
    try:
        cutoff_ts = now_ts() - (PORTFOLIO_DD_LOOKBACK_DAYS * 86400)
        row = conn.execute(
            """
            SELECT snapshot_date, equity
            FROM equity_snapshots
            WHERE time >= ?
            ORDER BY equity DESC
            LIMIT 1
            """,
            (cutoff_ts,),
        ).fetchone()

        if row is not None and float(row["equity"]) > high_equity:
            high_equity = float(row["equity"])
            since_date = str(row["snapshot_date"])
    finally:
        conn.close()

    dd_pct = 0.0
    dd_dollars = 0.0
    if high_equity > 0:
        dd_dollars = current_equity - high_equity
        dd_pct = dd_dollars / high_equity

    return {
        "current_equity": round(current_equity, 2),
        "high_equity": round(high_equity, 2),
        "high_equity_date": since_date,
        "drawdown_dollars": round(dd_dollars, 2),
        "drawdown_pct": round(dd_pct * 100, 2),
        "soft_threshold_pct": round(-PORTFOLIO_SOFT_DD_REDUCE_PCT * 100, 2),
        "hard_threshold_pct": round(-PORTFOLIO_HARD_DD_PAUSE_PCT * 100, 2),
    }


def portfolio_risk_guard_details() -> Dict[str, Any]:
    dd = current_drawdown_details()
    dd_raw = float(dd.get("drawdown_pct", 0.0) or 0.0) / 100.0

    soft_active = dd_raw <= -PORTFOLIO_SOFT_DD_REDUCE_PCT
    hard_active = dd_raw <= -PORTFOLIO_HARD_DD_PAUSE_PCT

    return {
        **dd,
        "enabled": PORTFOLIO_RISK_GUARD_ENABLED,
        "soft_active": bool(soft_active),
        "hard_active": bool(hard_active),
        "block_new_entries": bool(PORTFOLIO_RISK_GUARD_ENABLED and hard_active),
        "recommended_action": (
            "PAUSE new entries; manage exits only."
            if hard_active else
            "Reduce new exposure; keep position management active."
            if soft_active else
            "Normal risk mode."
        ),
    }


def maybe_send_portfolio_risk_guard_alert(details: Optional[Dict[str, Any]] = None) -> None:
    if not PORTFOLIO_RISK_GUARD_ENABLED:
        return

    info = details or portfolio_risk_guard_details()
    if not info.get("soft_active") and not info.get("hard_active"):
        return

    today = ny_date_str()
    last_key = "last_portfolio_risk_guard_alert_day"
    if get_meta(last_key) == today:
        return

    set_meta(last_key, today)
    send(format_portfolio_risk_guard(details=info))


def sleeve_from_trade(trade: Dict[str, Any]) -> str:
    entry_data = trade.get("entry_data", {}) or {}
    sleeve = str(entry_data.get("strategy_sleeve") or "").upper()
    if sleeve:
        return sleeve

    family = str(entry_data.get("strategy_family") or "").lower()
    ticker = str(trade.get("ticker", "")).upper()
    if "bear" in family or ticker in BEAR_WATCHLIST:
        return "BEAR_INVERSE"
    if entry_data:
        return "LONG_VCP"
    return "LEGACY_OR_MANUAL"



def sleeve_performance_summary() -> Dict[str, Any]:
    trades = load_trades()
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for trade in trades:
        grouped.setdefault(sleeve_from_trade(trade), []).append(trade)

    rows = []
    for sleeve, items in sorted(grouped.items()):
        profits = [float(x.get("profit", 0.0) or 0.0) for x in items]
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        pf = None if gross_loss == 0 else gross_profit / gross_loss
        rows.append({
            "sleeve": sleeve,
            "trade_records": len(items),
            "profit": round(sum(profits), 2),
            "win_rate_pct": round((len(wins) / len(items)) * 100, 2) if items else 0.0,
            "profit_factor": None if pf is None else round(pf, 3),
            "avg_profit": round(sum(profits) / len(profits), 2) if profits else 0.0,
        })

    core_trades = load_core_trades() if CORE_LEDGER_ENABLED else []
    core_sells = [t for t in core_trades if str(t.get("side")).upper() == "SELL"]
    core_profit = round(sum(float(t.get("realized_profit") or 0.0) for t in core_sells), 2)
    if core_trades:
        rows.append({
            "sleeve": "CORE_WEALTH_REALIZED",
            "trade_records": len(core_trades),
            "profit": core_profit,
            "win_rate_pct": None,
            "profit_factor": None,
            "avg_profit": round(core_profit / len(core_sells), 2) if core_sells else 0.0,
        })

    swing_profit = round(sum(float(t.get("profit", 0.0) or 0.0) for t in trades), 2)
    return {
        "rows": rows,
        "total_profit": round(swing_profit + core_profit, 2),
        "swing_profit": swing_profit,
        "core_realized_profit": core_profit,
        "trade_records": len(trades) + len(core_trades),
    }

def dynamic_portfolio_allocation_targets() -> Dict[str, Any]:
    market_details = market_regime_details()
    market = str(market_details.get("condition", "UNCERTAIN"))
    bear_details = bear_regime_details(market_details=market_details)
    risk = portfolio_risk_guard_details()

    if not WEALTH_DYNAMIC_ALLOCATION_ENABLED:
        core = WEALTH_CORE_ACCOUNT_ALLOC_PCT
        long_tactical = 0.30
        bear = 0.0
        cash = max(0.0, 1.0 - core - long_tactical - bear)
    elif risk.get("hard_active"):
        core = WEALTH_CORE_ALLOC_RISK_OFF
        long_tactical = 0.0
        bear = 0.0
        cash = 1.0 - core
    elif market == "BULL":
        core = WEALTH_CORE_ALLOC_BULL
        long_tactical = WEALTH_TACTICAL_LONG_ALLOC_BULL
        bear = WEALTH_BEAR_ALLOC_BULL
        cash = 1.0 - core - long_tactical - bear
    elif market == "BEAR":
        core = WEALTH_CORE_ALLOC_BEAR
        long_tactical = WEALTH_TACTICAL_LONG_ALLOC_BEAR
        bear = WEALTH_BEAR_ALLOC_BEAR if BEAR_SLEEVE_ENABLED else 0.0
        cash = 1.0 - core - long_tactical - bear
    else:
        core = WEALTH_CORE_ALLOC_UNCERTAIN
        long_tactical = WEALTH_TACTICAL_LONG_ALLOC_UNCERTAIN
        bear = WEALTH_BEAR_ALLOC_UNCERTAIN if int(bear_details.get("score", 0) or 0) >= BEAR_EXIT_SCORE else 0.0
        cash = 1.0 - core - long_tactical - bear

    if risk.get("soft_active") and not risk.get("hard_active"):
        # Soft drawdown mode: cut tactical sleeve exposure and hold the difference as cash.
        long_tactical *= 0.5
        bear *= 0.75
        cash = 1.0 - core - long_tactical - bear

    if cash < WEALTH_MIN_CASH_RESERVE_PCT:
        shortfall = WEALTH_MIN_CASH_RESERVE_PCT - cash
        long_reduction = min(shortfall, max(0.0, long_tactical))
        long_tactical -= long_reduction
        shortfall -= long_reduction
        if shortfall > 0:
            bear_reduction = min(shortfall, max(0.0, bear))
            bear -= bear_reduction
            shortfall -= bear_reduction
        if shortfall > 0:
            core = max(0.0, core - shortfall)
        cash = 1.0 - core - long_tactical - bear

    # Normalize small floating leftovers.
    values = {
        "core_wealth_pct": max(0.0, core),
        "long_vcp_tactical_pct": max(0.0, long_tactical),
        "bear_inverse_tactical_pct": max(0.0, bear),
        "cash_reserve_pct": max(0.0, cash),
    }
    total = sum(values.values())
    if total > 0:
        values = {k: v / total for k, v in values.items()}

    return {
        "strategy_version": "v3_5_dynamic_allocation",
        "ny_time": ny_now().strftime("%Y-%m-%d %H:%M %Z"),
        "market": market,
        "market_score": int(market_details.get("score", 0) or 0),
        "bear_score": int(bear_details.get("score", 0) or 0),
        "risk_guard": risk,
        **{k: round(v * 100, 2) for k, v in values.items()},
    }


def format_portfolio_allocation_plan() -> str:
    plan = dynamic_portfolio_allocation_targets()
    risk = plan.get("risk_guard", {}) or {}

    return (
        "🏛️ INSTITUTIONAL ALLOCATION PLAN v3.6\n\n"
        "Private bot only. This is portfolio guidance, not an automatic trade.\n\n"
        f"🕒 NY time: {plan.get('ny_time')}\n"
        f"🌎 Market: {market_label(str(plan.get('market', 'UNKNOWN')))} "
        f"({plan.get('market_score')}/8)\n"
        f"🐻 Bear pressure score: {plan.get('bear_score')}/60\n"
        f"🛡️ Risk guard: {risk.get('recommended_action')}\n"
        f"📉 Current DD: {risk.get('drawdown_pct')}% from {format_money(float(risk.get('high_equity', 0) or 0))}\n\n"
        "Target account buckets:\n"
        f"🏦 Core wealth rotation: {plan.get('core_wealth_pct')}%\n"
        f"🐂 Long VCP tactical: {plan.get('long_vcp_tactical_pct')}%\n"
        f"🐻 Bear inverse tactical: {plan.get('bear_inverse_tactical_pct')}%\n"
        f"💵 Cash reserve: {plan.get('cash_reserve_pct')}%\n\n"
        "Rules:\n"
        "• Core sleeve is long-term/private allocation guidance.\n"
        "• VCP/bear sleeves remain signal-driven tactical systems.\n"
        "• In hard drawdown mode, new entries pause and exits/management continue."
    )


def format_portfolio_risk_guard(details: Optional[Dict[str, Any]] = None) -> str:
    info = details or portfolio_risk_guard_details()
    return (
        "🛡️ PORTFOLIO RISK GUARD v3.6\n\n"
        f"Enabled: {yes_no(bool(info.get('enabled')))}\n"
        f"Current equity: {format_money(float(info.get('current_equity', 0) or 0))}\n"
        f"High equity: {format_money(float(info.get('high_equity', 0) or 0))} "
        f"({info.get('high_equity_date')})\n"
        f"Drawdown: {info.get('drawdown_pct')}% "
        f"({format_money(float(info.get('drawdown_dollars', 0) or 0))})\n\n"
        f"Soft threshold: {info.get('soft_threshold_pct')}%\n"
        f"Hard pause threshold: {info.get('hard_threshold_pct')}%\n"
        f"Soft active: {yes_no(bool(info.get('soft_active')))}\n"
        f"Hard pause active: {yes_no(bool(info.get('hard_active')))}\n\n"
        f"Action: {info.get('recommended_action')}"
    )


def format_sleeve_performance_report() -> str:
    summary = sleeve_performance_summary()
    rows = summary.get("rows", []) or []
    msg = (
        "📊 SLEEVE PERFORMANCE v3.6\n\n"
        f"Total realized P/L: {format_money(float(summary.get('total_profit', 0) or 0))}\n"
        f"Trade records: {summary.get('trade_records')}\n\n"
    )
    if not rows:
        msg += "No trade records yet."
        return msg

    for row in rows:
        pf = row.get("profit_factor")
        msg += (
            f"{row.get('sleeve')}\n"
            f"  P/L: {format_money(float(row.get('profit', 0) or 0))}\n"
            f"  Records: {row.get('trade_records')} | WR: {row.get('win_rate_pct')}% | "
            f"PF: {pf if pf is not None else 'n/a'}\n"
            f"  Avg record P/L: {format_money(float(row.get('avg_profit', 0) or 0))}\n\n"
        )
    return msg

def pct_change_last(df: pd.DataFrame, bars: int) -> Optional[float]:
    try:
        if df is None or len(df) <= bars:
            return None
        now = float(df["Close"].iloc[-1])
        old = float(df["Close"].iloc[-1 - bars])
        if old <= 0:
            return None
        return (now / old) - 1
    except Exception:
        return None


def realized_vol_last(df: pd.DataFrame, bars: int = 63) -> Optional[float]:
    try:
        if df is None or len(df) <= bars:
            return None
        returns = df["Close"].pct_change().tail(bars).dropna()
        if returns.empty:
            return None
        return float(returns.std() * math.sqrt(252))
    except Exception:
        return None



def wealth_core_score_ticker(ticker: str, regime: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Score one asset for the private v3.5 core-rotation sleeve."""
    try:
        df = get_historical(ticker, limit=280)
        if df is None or df.empty or len(df) < 210:
            return None

        close = df["Close"]
        price = float(close.iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])
        ma100 = float(close.rolling(100).mean().iloc[-1])

        roc21 = pct_change_last(df, 21)
        roc63 = pct_change_last(df, 63)
        roc126 = pct_change_last(df, 126)
        vol63 = realized_vol_last(df, 63)

        if roc21 is None or roc63 is None or roc126 is None or vol63 is None:
            return None

        trend_ok = ticker in WEALTH_CASH_LIKE or price > ma200
        if not trend_ok:
            return None

        base_score = (0.45 * roc126) + (0.35 * roc63) + (0.20 * roc21) - (0.18 * vol63)

        # In confirmed broad bear conditions, require non-defensive assets to be
        # exceptionally strong; otherwise defensive/cash-like assets can dominate.
        regime = regime or market_condition()
        if regime == "BEAR" and ticker not in WEALTH_DEFENSIVE_ALLOWED:
            if base_score < 0.12:
                return None

        if base_score < WEALTH_MIN_SCORE:
            return None

        cluster = wealth_asset_cluster(ticker)
        inv_vol = 1.0 / max(float(vol63), 0.03)
        score_boost = max(0.05, base_score - WEALTH_MIN_SCORE + 0.05)
        weight_score = inv_vol * (score_boost if WEALTH_SCORE_WEIGHTING_ENABLED else 1.0)

        return {
            "ticker": ticker,
            "cluster": cluster,
            "price": round(price, 2),
            "ma100": round(ma100, 2),
            "ma200": round(ma200, 2),
            "roc_1m_pct": round(roc21 * 100, 2),
            "roc_3m_pct": round(roc63 * 100, 2),
            "roc_6m_pct": round(roc126 * 100, 2),
            "vol_3m_pct": round(vol63 * 100, 2),
            "score": round(base_score, 4),
            "weight_score": round(weight_score, 6),
            "trend_ok": trend_ok,
            "regime": regime,
        }

    except Exception as exc:
        print(f"[WEALTH SCORE ERROR] {ticker}: {exc}")
        return None


def select_cluster_controlled_assets(scored: List[Dict[str, Any]], top_n: int) -> List[Dict[str, Any]]:
    if not WEALTH_CLUSTER_CONTROL_ENABLED:
        return scored[:max(1, top_n)]

    selected: List[Dict[str, Any]] = []
    cluster_counts: Dict[str, int] = {}
    for item in scored:
        cluster = str(item.get("cluster", "other"))
        count = cluster_counts.get(cluster, 0)
        if count >= WEALTH_MAX_ASSETS_PER_CLUSTER:
            continue
        selected.append(item)
        cluster_counts[cluster] = count + 1
        if len(selected) >= max(1, top_n):
            break
    return selected


def assign_core_weights(top: List[Dict[str, Any]], core_account_pct: float) -> List[Dict[str, Any]]:
    if not top:
        return []

    if WEALTH_VOL_WEIGHTING_ENABLED:
        raw_scores = [max(0.0001, float(x.get("weight_score", 0.0001) or 0.0001)) for x in top]
    else:
        raw_scores = [1.0 for _ in top]

    total = sum(raw_scores)
    weights = [x / total for x in raw_scores] if total > 0 else [1.0 / len(top) for _ in top]

    # Cap extreme concentration, then renormalize. This is intentionally simple
    # and robust rather than an optimizer.
    cap = clamp_float(WEALTH_MAX_SINGLE_CORE_ASSET_PCT, 0.05, 0.80)
    floor = clamp_float(WEALTH_MIN_SINGLE_CORE_ASSET_PCT, 0.00, cap)

    weights = [min(cap, max(0.0, w)) for w in weights]
    total = sum(weights)
    if total > 0:
        weights = [w / total for w in weights]

    # Apply small floor only when it is mathematically possible.
    if floor > 0 and floor * len(weights) <= 0.90:
        weights = [max(floor, w) for w in weights]
        total = sum(weights)
        if total > 0:
            weights = [w / total for w in weights]

    enriched = []
    for item, sleeve_weight in zip(top, weights):
        row = dict(item)
        row["target_core_pct"] = round(sleeve_weight * 100, 2)
        row["target_account_pct"] = round(sleeve_weight * core_account_pct * 100, 2)
        enriched.append(row)
    return enriched



def compute_wealth_core_plan() -> Dict[str, Any]:
    """Build ranked, actionable private core wealth plan with ledger-aware actions."""
    refresh_portfolio()

    allocation = dynamic_portfolio_allocation_targets()
    current_regime = str(allocation.get("market", market_condition()))

    scored = []
    for ticker in WEALTH_CORE_UNIVERSE:
        item = wealth_core_score_ticker(ticker, regime=current_regime)
        if item is not None:
            scored.append(item)

    scored = sorted(scored, key=lambda x: float(x.get("score", -999)), reverse=True)
    selected = select_cluster_controlled_assets(scored, WEALTH_CORE_TOP_N)

    if WEALTH_DYNAMIC_ALLOCATION_ENABLED:
        core_account_pct = float(allocation.get("core_wealth_pct", WEALTH_CORE_ACCOUNT_ALLOC_PCT * 100) or 0.0) / 100.0
    else:
        core_account_pct = WEALTH_CORE_ACCOUNT_ALLOC_PCT

    top = assign_core_weights(selected, core_account_pct)
    account_equity = compute_equity_snapshot_data().get("equity", 0.0)
    sleeve_value = float(account_equity) * core_account_pct

    core_details = core_position_market_value_details() if CORE_LEDGER_ENABLED else {"rows": [], "value": 0.0}
    current_rows = {str(row.get("ticker", "")).upper(): row for row in core_details.get("rows", [])}

    top_map = {str(item.get("ticker", "")).upper(): item for item in top}
    actions: List[Dict[str, Any]] = []

    # Ranked BUY/ADD/HOLD/TRIM actions for selected assets, best first.
    for rank, item in enumerate(top, start=1):
        ticker = str(item["ticker"]).upper()
        target_value = float(account_equity) * (float(item.get("target_account_pct", 0) or 0) / 100.0)
        current_value = float(current_rows.get(ticker, {}).get("market_value", 0.0) or 0.0)
        drift = target_value - current_value
        drift_pct_account = 0.0 if float(account_equity) <= 0 else (drift / float(account_equity)) * 100.0

        if current_value <= 0 and target_value >= CORE_ACTION_DOLLAR_THRESHOLD:
            action = "BUY"
        elif drift >= max(CORE_ACTION_DOLLAR_THRESHOLD, float(account_equity) * WEALTH_REBALANCE_DRIFT_THRESHOLD_PCT):
            action = "ADD"
        elif drift <= -max(CORE_ACTION_DOLLAR_THRESHOLD, float(account_equity) * WEALTH_REBALANCE_DRIFT_THRESHOLD_PCT):
            action = "TRIM"
        else:
            action = "HOLD"

        actions.append({
            "rank": rank,
            "ticker": ticker,
            "action": action,
            "cluster": item.get("cluster"),
            "score": item.get("score"),
            "price": item.get("price"),
            "target_account_pct": item.get("target_account_pct"),
            "target_core_pct": item.get("target_core_pct"),
            "target_value": round(target_value, 2),
            "current_value": round(current_value, 2),
            "suggested_dollars": round(abs(drift), 2),
            "drift_dollars": round(drift, 2),
            "drift_pct_account": round(drift_pct_account, 2),
            "roc_1m_pct": item.get("roc_1m_pct"),
            "roc_3m_pct": item.get("roc_3m_pct"),
            "roc_6m_pct": item.get("roc_6m_pct"),
            "vol_3m_pct": item.get("vol_3m_pct"),
        })

    # Exit / rotate candidates: current holdings not selected anymore, or trend/score deteriorated.
    selected_tickers = set(top_map.keys())
    scored_map = {str(item.get("ticker", "")).upper(): item for item in scored}
    for ticker, row in current_rows.items():
        if ticker in selected_tickers:
            continue
        score_item = scored_map.get(ticker)
        current_value = float(row.get("market_value", 0.0) or 0.0)
        if score_item is None:
            reason = "Removed from qualified core universe or lost MA200/trend filter."
            score = None
        else:
            reason = "No longer ranked in selected top core assets."
            score = score_item.get("score")
        actions.append({
            "rank": None,
            "ticker": ticker,
            "action": "SELL",
            "cluster": None if score_item is None else score_item.get("cluster"),
            "score": score,
            "price": row.get("mark_price"),
            "target_account_pct": 0.0,
            "target_core_pct": 0.0,
            "target_value": 0.0,
            "current_value": round(current_value, 2),
            "suggested_dollars": round(current_value, 2),
            "drift_dollars": round(-current_value, 2),
            "drift_pct_account": None if float(account_equity) <= 0 else round((-current_value / float(account_equity)) * 100, 2),
            "reason": reason,
        })

    actionable = [a for a in actions if str(a.get("action")).upper() in {"BUY", "ADD", "TRIM", "SELL"}]
    plan_id = uuid.uuid4().hex

    return {
        "plan_id": plan_id,
        "strategy_version": WEALTH_STRATEGY_VERSION,
        "private_only": True,
        "ny_time": ny_now().strftime("%Y-%m-%d %H:%M %Z"),
        "market": allocation.get("market", market_condition()),
        "market_score": allocation.get("market_score"),
        "bear_score": allocation.get("bear_score"),
        "allocation": allocation,
        "risk_guard": allocation.get("risk_guard", {}),
        "account_equity": round(float(account_equity), 2),
        "target_core_account_pct": round(core_account_pct * 100, 2),
        "target_core_value": round(sleeve_value, 2),
        "current_core_value": round(float(core_details.get("value", 0.0) or 0.0), 2),
        "current_core_cost_basis": round(float(core_details.get("cost_basis", 0.0) or 0.0), 2),
        "current_core_unrealized_profit": round(float(core_details.get("unrealized_profit", 0.0) or 0.0), 2),
        "top_n": len(top),
        "top": top,
        "actions": actions,
        "actionable": actionable,
        "all_scored": scored,
        "cluster_control_enabled": WEALTH_CLUSTER_CONTROL_ENABLED,
        "vol_weighting_enabled": WEALTH_VOL_WEIGHTING_ENABLED,
        "ledger_enabled": CORE_LEDGER_ENABLED,
    }


def format_wealth_core_plan(plan: Dict[str, Any]) -> str:
    actions = plan.get("actions", []) or []
    allocation = plan.get("allocation", {}) or {}
    risk = plan.get("risk_guard", {}) or {}

    msg = (
        "🏛️ CORE WEALTH REBALANCE PLAN v3.6\n\n"
        "Private bot only. This is a real core-ledger plan. Execute in broker first, then record with corebuy/coresell.\n\n"
        f"🕒 NY time: {plan.get('ny_time')}\n"
        f"🌎 Market: {market_label(str(plan.get('market', 'UNKNOWN')))} "
        f"({plan.get('market_score')}/8)\n"
        f"🐻 Bear pressure: {plan.get('bear_score')}/60\n"
        f"🛡️ Risk guard: {risk.get('recommended_action')}\n"
        f"💼 Total equity estimate: {format_money(float(plan.get('account_equity', 0) or 0))}\n"
        f"🏦 Target core sleeve: {plan.get('target_core_account_pct')}% = {format_money(float(plan.get('target_core_value', 0) or 0))}\n"
        f"📦 Current core value: {format_money(float(plan.get('current_core_value', 0) or 0))}\n"
        f"📈 Core unrealized P/L: {format_money(float(plan.get('current_core_unrealized_profit', 0) or 0))}\n\n"
        "Portfolio bucket guide:\n"
        f"• Core wealth: {allocation.get('core_wealth_pct')}%\n"
        f"• Long VCP tactical: {allocation.get('long_vcp_tactical_pct')}%\n"
        f"• Bear inverse tactical: {allocation.get('bear_inverse_tactical_pct')}%\n"
        f"• Cash reserve: {allocation.get('cash_reserve_pct')}%\n\n"
    )

    if not actions:
        msg += "No qualified core assets and no open core positions needing action."
        return msg

    ranked = [a for a in actions if a.get("rank") is not None]
    exits = [a for a in actions if str(a.get("action")).upper() == "SELL"]

    if ranked:
        msg += "🎯 Ranked core candidates — best to least attractive\n"
        for item in ranked:
            action = str(item.get("action", "HOLD")).upper()
            verb = {"BUY": "🟢 BUY", "ADD": "🟢 ADD", "HOLD": "🟡 HOLD", "TRIM": "🟠 TRIM"}.get(action, action)
            msg += (
                f"{item.get('rank')}) {verb} {item['ticker']} ({item.get('cluster', 'other')})\n"
                f"   Target: {item.get('target_account_pct')}% acct / {format_money(float(item.get('target_value', 0) or 0))}\n"
                f"   Current: {format_money(float(item.get('current_value', 0) or 0))} | Action size: ~{format_money(float(item.get('suggested_dollars', 0) or 0))}\n"
                f"   Price: {item.get('price')} | 1m {format_pct(item.get('roc_1m_pct'))} | 3m {format_pct(item.get('roc_3m_pct'))} | 6m {format_pct(item.get('roc_6m_pct'))}\n"
                f"   Vol: {item.get('vol_3m_pct')}% | Score: {item.get('score')}\n"
            )
        msg += "\n"

    if exits:
        msg += "🔴 Core exit / rotation candidates\n"
        for item in exits:
            msg += (
                f"SELL {item['ticker']} — current {format_money(float(item.get('current_value', 0) or 0))}\n"
                f"Reason: {item.get('reason', 'No longer qualified')}\n"
            )
        msg += "\n"

    msg += (
        "How to execute after broker fill:\n"
        "• corebuy TICKER SHARES at PRICE\n"
        "• coresell TICKER SHARES at PRICE\n\n"
        "Core rules:\n"
        "• BUY/ADD/HOLD/TRIM/SELL is monthly/slow allocation logic, not a swing stop system.\n"
        "• The core ledger shares the same cash account, so swing sizing stays honest.\n"
        "• Do not use normal bought/sold for core positions."
    )

    return msg[:MAX_TELEGRAM_MESSAGE]

def maybe_send_wealth_core_signal() -> None:
    """Monthly private wealth-sleeve alert after market close."""
    if not WEALTH_SLEEVE_ENABLED:
        return

    current_ny = ny_now()
    minutes = current_ny.hour * 60 + current_ny.minute

    if is_market_weekday(current_ny) and minutes < WEALTH_REVIEW_AFTER_CLOSE_MINUTE:
        return

    # Review at most once per calendar month, with a repeat-day guard.
    month_key = current_ny.strftime("%Y-%m")
    if get_meta("last_wealth_core_month") == month_key:
        return

    last_raw = get_meta("last_wealth_core_alert_ts")
    if last_raw:
        try:
            days_since = (now_ts() - float(last_raw)) / 86400
            if days_since < WEALTH_ALERT_REPEAT_DAYS:
                return
        except ValueError:
            pass

    try:
        plan = compute_wealth_core_plan()
        save_core_plan_signal(plan)
        set_meta("last_wealth_core_month", month_key)
        set_meta("last_wealth_core_alert_ts", str(now_ts()))
        send(format_wealth_core_plan(plan))
        audit("WEALTH_CORE_SIGNAL", f"month={month_key} top={[x.get('ticker') for x in plan.get('top', [])]}")
    except Exception as exc:
        logger.exception(f"[WEALTH CORE SIGNAL ERROR] {exc}")
        print(f"[WEALTH CORE SIGNAL ERROR] {exc}")


def risk_pct_for_ticker(ticker: str) -> Optional[float]:



    if ticker in STRONG:



        return 0.03



    if ticker in MEDIUM:



        return 0.02



    if ticker in WEAK:



        return 0.01



    return None



 



 




def approximate_equity_from_portfolio() -> float:
    snapshot = compute_equity_snapshot_data()
    return float(snapshot.get("equity", 0.0) or 0.0)

def daily_drawdown_exceeded() -> bool:



    conn = db_connect()



    try:

        rows = conn.execute(

            """

            SELECT profit

            FROM trades

            WHERE exit_time >= ?

            """,

            (

                datetime.combine(

                    ny_now().date(),

                    datetime.min.time(),

                    tzinfo=NY_TZ

                ).timestamp(),

            ),

        ).fetchall()



        total = sum(float(r["profit"]) for r in rows)



        equity = approximate_equity_from_portfolio()



        if equity <= 0:

            return False



        return (total / equity) <= -MAX_DAILY_LOSS_PCT



    finally:

        conn.close()

 



def is_market_weekday(dt: datetime) -> bool:



    schedule = NYSE.schedule(

        start_date=dt.date(),

        end_date=dt.date()

    )



    return not schedule.empty

 



 



def is_regular_market_hours(dt: datetime) -> bool:



    if not is_market_weekday(dt):



        return False



    minutes = dt.hour * 60 + dt.minute



    return (9 * 60 + 30) <= minutes <= (16 * 60)





def is_morning_scan_window(dt: datetime) -> bool:

    minutes = dt.hour * 60 + dt.minute



    # Morning scan window: 06:45 to 09:25 New York time.

    # This uses the latest completed daily candle, usually yesterday's candle before market open.

    return (6 * 60 + 45) <= minutes <= (9 * 60 + 25)



def is_near_close_scan_window(dt: datetime) -> bool:

    minutes = dt.hour * 60 + dt.minute



    # Official weekday scan window: 15:50 to 15:58 New York time.

    # This gives the bot time to scan before the 16:00 close.

    return (15 * 60 + 50) <= minutes <= (15 * 60 + 58)



def expected_daily_bar_date() -> Optional[str]:

    current_ny = ny_now()

    today = current_ny.date()



    try:

        schedule = NYSE.schedule(

            start_date=today - timedelta(days=10),

            end_date=today

        )



        if schedule.empty:

            return None



        sessions = [d.date() for d in schedule.index]

        expected_session = sessions[-1]



        # Before the near-close scan window, the latest completed candle is the previous session.

        # During/after near-close, we expect to build today's synthetic candle from intraday data.

        if expected_session == today:

            minutes = current_ny.hour * 60 + current_ny.minute



            if minutes < (15 * 60 + 45):

                if len(sessions) >= 2:

                    expected_session = sessions[-2]



        return expected_session.isoformat()



    except Exception as exc:

        print(f"[EXPECTED BAR DATE ERROR] {exc}")

        return None



def outstanding_signal_reservations(

    expected_bar_date: Optional[str]

) -> Tuple[float, float, List[str]]:

    """

    Reserve risk/capital for already-sent signals from the same daily candle.



    This prevents repeated forcescan calls from producing more and more

    candidates from the same candle after earlier signals were already sent.

    """

    refresh_portfolio()



    signals = load_signals()

    open_positions = set(portfolio["positions"].keys())



    reserved_risk = 0.0

    reserved_capital = 0.0

    reserved_tickers: List[str] = []



    for ticker, signal in signals.items():

        if ticker in open_positions:

            # If already bought, actual open-position risk handles it.

            continue



        if not isinstance(signal, dict):

            continue



        entry_data = signal.get("entry_data", {}) or {}



        if expected_bar_date and entry_data.get("daily_bar_date") != expected_bar_date:

            continue



        try:

            risk_amount = float(entry_data.get("risk_amount", 0) or 0)

        except (TypeError, ValueError):

            risk_amount = 0.0



        try:

            capital = float(entry_data.get("capital", 0) or 0)

        except (TypeError, ValueError):

            capital = 0.0



        if risk_amount > 0:

            reserved_risk += risk_amount

            reserved_tickers.append(ticker)



        if capital > 0:

            reserved_capital += capital



    return reserved_risk, reserved_capital, reserved_tickers



# -----------------------------------------------------------------------------



# ANALYTICS



# -----------------------------------------------------------------------------



 



def weekly_performance() -> float:



    trades = load_trades()



    week_ago = now_ts() - 7 * 86400



    return round(sum(float(t.get("profit", 0)) for t in trades if float(t.get("exit_time", 0)) >= week_ago), 2)



 



 



def win_rate() -> float:



    trades = load_trades()



    if not trades:



        return 0.0



    wins = sum(1 for t in trades if float(t.get("profit", 0)) > 0)



    return round((wins / len(trades)) * 100, 2)



 



 



def ticker_stats() -> Tuple[Tuple[str, float], Tuple[str, float]]:



    trades = load_trades()



    stats: Dict[str, float] = {}



    for t in trades:



        ticker = str(t.get("ticker", "UNKNOWN"))



        stats.setdefault(ticker, 0.0)



        stats[ticker] += float(t.get("profit", 0))



    best = max(stats.items(), key=lambda x: x[1], default=("None", 0.0))



    worst = min(stats.items(), key=lambda x: x[1], default=("None", 0.0))



    return best, worst



 



 



def avg_trade_duration() -> str:



    trades = load_trades()



    if not trades:



        return "0 hrs"



    avg = sum(int(t.get("duration_sec", 0)) for t in trades) / len(trades)



    hours = avg / 3600



    if hours >= 72:



        days = hours / 24



        return f"{round(days, 2)} days ({round(hours, 2)} hrs)"



    return f"{round(hours, 2)} hrs"



 



 



def expectancy_summary() -> Dict[str, Any]:



    trades = load_trades()



    r_values = [float(t["r_multiple"]) for t in trades if t.get("r_multiple") is not None]



    profits = [float(t.get("profit", 0)) for t in trades]



    gross_profit = sum(p for p in profits if p > 0)



    gross_loss = abs(sum(p for p in profits if p < 0))



    profit_factor = None if gross_loss == 0 else gross_profit / gross_loss



 



    if not r_values:



        return {



            "trades": len(trades),



            "r_trades": 0,



            "avg_r": None,



            "median_r": None,



            "avg_win_r": None,



            "avg_loss_r": None,



            "profit_factor": profit_factor,



        }



 



    wins_r = [x for x in r_values if x > 0]



    losses_r = [x for x in r_values if x < 0]



    return {



        "trades": len(trades),



        "r_trades": len(r_values),



        "avg_r": round(sum(r_values) / len(r_values), 3),



        "median_r": round(float(pd.Series(r_values).median()), 3),



        "avg_win_r": round(sum(wins_r) / len(wins_r), 3) if wins_r else None,



        "avg_loss_r": round(sum(losses_r) / len(losses_r), 3) if losses_r else None,



        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,



    }



 



 



def position_level_summary() -> Dict[str, Any]:



    trades = load_trades()



    grouped: Dict[str, Dict[str, Any]] = {}



    for trade in trades:



        position_id = trade.get("position_id") or f"legacy_{trade.get('id')}"



        g = grouped.setdefault(



            position_id,



            {



                "profit": 0.0,



                "risk_dollars": 0.0,



                "ticker": trade.get("ticker"),



                "exit_count": 0,



            },



        )



        g["profit"] += float(trade.get("profit", 0))



        rps = trade.get("risk_per_share")



        if rps is not None and float(rps) > 0:



            g["risk_dollars"] += float(rps) * int(trade.get("shares", 0))



        g["exit_count"] += 1



 



    values = []



    for g in grouped.values():



        if g["risk_dollars"] > 0:



            values.append(g["profit"] / g["risk_dollars"])



 



    return {



        "positions_closed_or_partially_closed": len(grouped),



        "positions_with_r": len(values),



        "avg_position_r": round(sum(values) / len(values), 3) if values else None,



        "median_position_r": round(float(pd.Series(values).median()), 3) if values else None,



    }



 



# -----------------------------------------------------------------------------



# TRADING ACCOUNTING OPERATIONS



# -----------------------------------------------------------------------------



 



def make_trade_from_position(



    ticker: str,



    pos: Dict[str, Any],



    shares: int,



    exit_price: float,



    exit_reason: str,



) -> Dict[str, Any]:



    entry = float(pos["price"])



    profit = (exit_price - entry) * shares



    risk_per_share = pos.get("risk_per_share")



    r_multiple: Optional[float] = None



    if isinstance(risk_per_share, (int, float)) and risk_per_share > 0:



        r_multiple = (exit_price - entry) / float(risk_per_share)



 



    entry_time = float(pos.get("entry_time", now_ts()))



    exit_time = now_ts()



    return {



        "id": uuid.uuid4().hex,



        "position_id": pos.get("position_id"),



        "strategy_version": pos.get("strategy_version", STRATEGY_VERSION),



        "ticker": ticker,



        "entry_price": entry,



        "exit_price": exit_price,



        "shares": shares,



        "profit": round(profit, 2),



        "entry_time": entry_time,



        "exit_time": exit_time,



        "duration_sec": int(exit_time - entry_time),



        "exit_reason": exit_reason,



        "entry_data": pos.get("entry_data", {}),



        "risk_per_share": risk_per_share if isinstance(risk_per_share, (int, float)) and risk_per_share > 0 else None,



        "r_multiple": None if r_multiple is None else round(r_multiple, 4),



    }



 



 



def record_buy(



    ticker: str,



    shares: int,



    price: float,



    update_id: Optional[int] = None,



) -> Tuple[bool, str]:



    ticker = normalize_ticker(ticker) or ""



    if not ticker:



        return False, "Invalid ticker"



    if shares <= 0:



        return False, "Shares must be positive"



    if not is_finite_positive(price):



        return False, "Price must be positive and finite"



    signal = last_signals.get(ticker, {})



    signal_data = signal.get("entry_data", {}) if isinstance(signal, dict) else {}



    signal_price = signal_data.get("signal_price")



    if REQUIRE_BUY_TICKER_IN_WATCHLIST and ticker not in ALLOWED_BUY_TICKERS:

        return (

            False,

            f"Buy rejected: {ticker} is not in WATCHLIST or BEAR_WATCHLIST.\n"

            "This may be a typo. Add it to the correct universe first if you really want to trade it."

        )



    if REQUIRE_ACTIVE_SIGNAL_FOR_BUY and not signal_data:

        return (

            False,

            f"Buy rejected: no active signal found for {ticker}.\n"

            "This protects you from typo buys like UBST instead of UPST.\n"

            "Use forcescan/wait for a signal, or disable REQUIRE_ACTIVE_SIGNAL_FOR_BUY if you intentionally want manual buys."

        )



    if REQUIRE_LIVE_QUOTE_FOR_BUY:

        quote = get_prices_batch([ticker]).get(ticker)



        if quote is None:

            return (

                False,

                f"Buy rejected: no live quote found for {ticker}.\n"

                "Cash was not changed. Check ticker spelling."

            )



        quote_deviation = abs(price - quote) / quote



        if quote_deviation > BUY_QUOTE_DEVIATION_LIMIT:

            return (

                False,

                f"Buy rejected: entered price is too far from live quote.\n"

                f"Ticker: {ticker}\n"

                f"Your price: {round(price, 2)}\n"

                f"Live quote: {round(quote, 2)}\n"

                f"Difference: {round(quote_deviation * 100, 2)}%\n"

                f"Max allowed: {round(BUY_QUOTE_DEVIATION_LIMIT * 100, 2)}%"

            )



    signal_max_valid_entry = signal_data.get("max_valid_entry")

    if isinstance(signal_max_valid_entry, (int, float)) and signal_max_valid_entry > 0:
        max_allowed_price = float(signal_max_valid_entry)
        max_entry_label = "signal max entry limit"

    elif isinstance(signal_price, (int, float)) and signal_price > 0:
        max_allowed_price = float(signal_price) * (1 + MAX_ENTRY_EXTENSION_PCT)
        max_entry_label = f"{round(MAX_ENTRY_EXTENSION_PCT * 100, 2)}% above signal"

    else:
        max_allowed_price = None
        max_entry_label = "n/a"

    if max_allowed_price is not None and price > max_allowed_price:
        return (
            False,
            f"Entry rejected: price too extended above signal.\n"
            f"Entry signal: {round(float(signal_price), 2) if isinstance(signal_price, (int, float)) else 'n/a'}\n"
            f"Your price: {round(price, 2)}\n"
            f"Max entry limit: {round(max_allowed_price, 2)} ({max_entry_label})"
        )

    atr_val: Optional[float] = None

    stop: Optional[float] = None

    signal_atr = signal_data.get("atr")
    signal_stop = signal_data.get("stop")

    # V2: prefer the exact signaled STOP/LOSS. The old code recalculated
    # stop from ATR, which could make the recorded trade different from the alert.
    if isinstance(signal_atr, (int, float)) and signal_atr > 0:
        atr_val = float(signal_atr)

    if isinstance(signal_stop, (int, float)) and 0 < float(signal_stop) < price:
        stop = float(signal_stop)

    if stop is None and isinstance(signal_atr, (int, float)) and signal_atr > 0:
        atr_val = float(signal_atr)
        stop = price - (1.5 * atr_val)

    if stop is None:
        try:
            df = get_signal_dataframe(ticker, limit=120)

            if df is not None and not df.empty:
                val = atr(df).iloc[-1]

                if not pd.isna(val) and is_finite_positive(float(val)):
                    atr_val = float(val)
                    stop = price - (1.5 * atr_val)

        except Exception as exc:
            print(f"[BUY ATR ERROR] {ticker}: {exc}")



    if stop is None or stop <= 0 or stop >= price:



        stop = price * 0.95



        atr_val = None



 



    risk_per_share = price - stop



    if risk_per_share <= 0:



        return False, "Invalid stop/risk calculation"



 



 



    position_id = f"{ticker}_{int(now_ts())}_{uuid.uuid4().hex[:8]}"



    new_pos = {



        "position_id": position_id,



        "strategy_version": str(signal_data.get("strategy_version", STRATEGY_VERSION)),



        "shares": shares,



        "price": price,



        "initial_stop": stop,



        "stop": stop,



        "highest": price,



        "partial_taken": False,



        "entry_time": now_ts(),



        "atr": atr_val,



        "risk_per_share": risk_per_share,



        "entry_data": signal_data,



    }



 



    with db_tx() as conn:



        existing = conn.execute("SELECT 1 FROM positions WHERE ticker = ?", (ticker,)).fetchone()

        if existing is not None:
            mark_update_processed_tx(conn, update_id, "rejected_existing_position")
            return False, "Position already exists"

        open_count = conn.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"]

        if int(open_count) >= MAX_OPEN_POSITIONS:
            mark_update_processed_tx(conn, update_id, "rejected_max_open_positions")
            return False, f"Buy rejected: max open positions reached ({MAX_OPEN_POSITIONS})."

        cash = get_cash(conn)



        cost = shares * price



        if cost > cash:



            mark_update_processed_tx(conn, update_id, "rejected_insufficient_cash")



            return False, "Not enough cash"



 



        set_cash_tx(conn, cash - cost)



        upsert_position_tx(conn, ticker, new_pos)



        mark_update_processed_tx(conn, update_id, "processed_buy")



 



    refresh_portfolio()



    audit(

        "BUY",

        f"{ticker} shares={shares} price={price} "

        f"position_id={position_id}"

    )



    return True, (

        f"✅ BOUGHT {ticker}\n\n"

        f"📦 Shares: {shares}\n"

        f"💵 Price: {price}\n"

        f"💰 Cash left: {format_money(portfolio['cash'])}"

    )



 



 



def record_sell(



    ticker: str,



    shares: int,



    price: float,



    exit_reason: str = "manual",



    update_id: Optional[int] = None,



) -> Tuple[bool, str]:



    ticker = normalize_ticker(ticker) or ""



    if not ticker:



        return False, "Invalid ticker"



    if shares <= 0:



        return False, "Shares must be positive"



    if not is_finite_positive(price):



        return False, "Price must be positive and finite"



 



    with db_tx() as conn:



        row = conn.execute("SELECT * FROM positions WHERE ticker = ?", (ticker,)).fetchone()



        if row is None:



            mark_update_processed_tx(conn, update_id, "rejected_no_position")



            return False, "No position to sell"



 



        pos = row_to_position(row)



        current_shares = int(pos["shares"])



        if shares > current_shares:



            mark_update_processed_tx(conn, update_id, "rejected_too_many_shares")



            return False, f"You only have {current_shares} shares"



 



        trade = make_trade_from_position(ticker, pos, shares, price, exit_reason)



        insert_trade_tx(conn, trade)



 



        cash = get_cash(conn)



        set_cash_tx(conn, cash + shares * price)



 



        remaining = current_shares - shares



        if remaining <= 0:



            delete_position_tx(conn, ticker)



        else:



            pos["shares"] = remaining



            # Manual partial means the planned partial should not also auto-fire later.



            if exit_reason == "manual":



                pos["partial_taken"] = True



            upsert_position_tx(conn, ticker, pos)



 



        mark_update_processed_tx(conn, update_id, f"processed_{exit_reason}_sell")



 



    refresh_portfolio()



    audit(

        "SELL",

        f"{ticker} shares={shares} price={price} "

        f"reason={exit_reason}"

    )



    if exit_reason == "manual" and should_forward_public_position(pos):

        if remaining <= 0:

            send_public_signal(

                format_public_exit_signal(

                    ticker=ticker,

                    price=price,

                    trade=trade,

                    reason="manual"

                )

            )

        else:

            send_public_signal(

                format_public_partial_signal(

                    ticker=ticker,

                    price=price,

                    trade=trade

                )

            )



    return True, (

        f"💰 SOLD {ticker}\n\n"

        f"📦 Shares: {shares}\n"

        f"💵 Exit Price: {price}\n"

        f"📊 P/L: {format_money(trade['profit'])}\n"

        f"💼 Cash: {format_money(portfolio['cash'])}"

    )



 



 



def record_auto_exit_or_partial(



    ticker: str,



    shares: int,



    price: float,



    exit_reason: str,



    updated_fields: Optional[Dict[str, Any]] = None,



) -> Optional[Dict[str, Any]]:



    """Transactional auto exit. Returns trade dict if executed, else None."""



    ticker = normalize_ticker(ticker) or ""



    if not ticker or shares <= 0 or not is_finite_positive(price):



        return None





    with db_tx() as conn:



        row = conn.execute("SELECT * FROM positions WHERE ticker = ?", (ticker,)).fetchone()



        if row is None:



            return None



        pos = row_to_position(row)



        current_shares = int(pos["shares"])



        if shares > current_shares:



            shares = current_shares



        if updated_fields:



            pos.update(updated_fields)



 



        trade = make_trade_from_position(ticker, pos, shares, price, exit_reason)



        insert_trade_tx(conn, trade)



 



        cash = get_cash(conn)



        set_cash_tx(conn, cash + shares * price)



 



        remaining = current_shares - shares



        if remaining <= 0:



            delete_position_tx(conn, ticker)



            set_cooldown_tx(conn, ticker, now_ts())



        else:



            pos["shares"] = remaining



            if exit_reason == "partial":



                pos["partial_taken"] = True



            upsert_position_tx(conn, ticker, pos)



 



    refresh_portfolio()



    if trade:

        audit(

            exit_reason.upper(),

            f"{ticker} shares={shares} "

            f"price={price} "

            f"profit={trade['profit']}"

        )



    return trade



 



def set_cooldown_tx(conn: sqlite3.Connection, ticker: str, timestamp: float) -> None:



    conn.execute(



        "INSERT INTO cooldowns(ticker, time) VALUES (?, ?) "



        "ON CONFLICT(ticker) DO UPDATE SET time = excluded.time",



        (ticker, timestamp),



    )



 



 



def update_position_fields(ticker: str, fields: Dict[str, Any]) -> None:



    ticker = normalize_ticker(ticker) or ""



    if not ticker:



        return



    with db_tx() as conn:



        row = conn.execute("SELECT * FROM positions WHERE ticker = ?", (ticker,)).fetchone()



        if row is None:



            return



        pos = row_to_position(row)



        pos.update(fields)



        upsert_position_tx(conn, ticker, pos)



    refresh_portfolio()



def void_buy(

    ticker: str,

    update_id: Optional[int] = None

) -> Tuple[bool, str]:

    """

    Undo a mistaken buy without creating a fake sell trade.



    Use only for admin/paper correction when the buy itself was a mistake,

    for example: bought UBST instead of UPST.



    It refunds original entry cost and deletes the open position.

    It refuses to run if the position already has trade history.

    """

    ticker = normalize_ticker(ticker) or ""



    if not ticker:

        return False, "Invalid ticker"



    with db_tx() as conn:

        row = conn.execute(

            "SELECT * FROM positions WHERE ticker = ?",

            (ticker,),

        ).fetchone()



        if row is None:

            mark_update_processed_tx(conn, update_id, "rejected_voidbuy_no_position")

            return False, f"No open position found for {ticker}"



        pos = row_to_position(row)

        position_id = pos.get("position_id")



        existing_trade = conn.execute(

            "SELECT 1 FROM trades WHERE position_id = ? LIMIT 1",

            (position_id,),

        ).fetchone()



        if existing_trade is not None:

            mark_update_processed_tx(conn, update_id, "rejected_voidbuy_has_trades")

            return (

                False,

                f"Void rejected: {ticker} already has trade history. "

                "Use normal sell/edit correction instead."

            )



        shares = int(pos["shares"])

        entry_price = float(pos["price"])

        refund = shares * entry_price



        cash = get_cash(conn)

        set_cash_tx(conn, cash + refund)



        delete_position_tx(conn, ticker)



        conn.execute(

            "DELETE FROM cooldowns WHERE ticker = ?",

            (ticker,),

        )



        mark_update_processed_tx(conn, update_id, "processed_voidbuy")



    refresh_portfolio()

    missing_price_counts.pop(ticker, None)



    audit(

        "VOID_BUY",

        f"{ticker} shares={shares} entry_price={entry_price} refund={refund}"

    )



    return True, (

        f"🧹 VOID BUY COMPLETE {ticker}\n\n"

        f"📦 Removed shares: {shares}\n"

        f"💵 Entry price: {round(entry_price, 2)}\n"

        f"💰 Cash refunded: {format_money(refund)}\n"

        f"💼 Cash now: {format_money(portfolio['cash'])}\n\n"

        "No fake sell trade was created."

    )





# -----------------------------------------------------------------------------
# CORE WEALTH LEDGER v3.6
# -----------------------------------------------------------------------------


def row_to_core_position(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "ticker": row["ticker"],
        "core_position_id": row["core_position_id"],
        "strategy_version": row["strategy_version"],
        "shares": float(row["shares"]),
        "avg_entry_price": float(row["avg_entry_price"]),
        "cost_basis": float(row["cost_basis"]),
        "entry_time": float(row["entry_time"]),
        "last_update_time": float(row["last_update_time"]),
        "highest": None if row["highest"] is None else float(row["highest"]),
        "sleeve": row["sleeve"],
        "target_account_pct": None if row["target_account_pct"] is None else float(row["target_account_pct"]),
        "last_plan_id": row["last_plan_id"],
        "notes": row["notes"],
    }


def load_core_positions() -> Dict[str, Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM core_positions ORDER BY ticker").fetchall()
        return {row["ticker"]: row_to_core_position(row) for row in rows}
    finally:
        conn.close()


def load_core_trades() -> List[Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM core_trades ORDER BY time ASC, created_at ASC").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def load_latest_core_signal() -> Optional[Dict[str, Any]]:
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT * FROM core_signals WHERE status = 'ACTIVE' ORDER BY time DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["plan"] = json_loads_dict(data.get("plan_json"))
        return data
    finally:
        conn.close()


def save_core_plan_signal(plan: Dict[str, Any]) -> str:
    plan_id = str(plan.get("plan_id") or uuid.uuid4().hex)
    plan = dict(plan)
    plan["plan_id"] = plan_id
    with db_tx() as conn:
        conn.execute("UPDATE core_signals SET status = 'SUPERSEDED' WHERE status = 'ACTIVE'")
        conn.execute(
            """
            INSERT INTO core_signals(
                id, time, plan_date, market_regime, account_equity,
                core_target_pct, plan_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
            """,
            (
                plan_id,
                now_ts(),
                ny_date_str(),
                str(plan.get("market", "UNKNOWN")),
                float(plan.get("account_equity", 0) or 0),
                float(plan.get("target_core_account_pct", 0) or 0),
                json_dumps(plan),
            ),
        )
    return plan_id


def current_core_plan_for_validation() -> Dict[str, Any]:
    # Recompute so buys/sells use the latest market/equity state. Also persists
    # it as the active plan so each confirmed core trade can reference a plan_id.
    plan = compute_wealth_core_plan()
    save_core_plan_signal(plan)
    return plan


def latest_core_plan_action_map(plan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    actions = plan.get("actions", []) or []
    return {str(item.get("ticker", "")).upper(): item for item in actions if item.get("ticker")}


def core_position_market_value_details(
    prices: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    positions = load_core_positions()
    tickers = list(positions.keys())
    prices = prices or get_prices_batch(tickers)
    rows: List[Dict[str, Any]] = []
    total_value = 0.0
    total_cost = 0.0
    total_unrealized = 0.0

    for ticker, pos in positions.items():
        mark = float(prices.get(ticker, pos.get("avg_entry_price", 0)) or pos.get("avg_entry_price", 0))
        shares = float(pos.get("shares", 0) or 0)
        value = shares * mark
        cost = float(pos.get("cost_basis", 0) or 0)
        unrealized = value - cost
        total_value += value
        total_cost += cost
        total_unrealized += unrealized
        rows.append({
            **pos,
            "mark_price": round(mark, 4),
            "market_value": round(value, 2),
            "unrealized_profit": round(unrealized, 2),
            "unrealized_pct": None if cost <= 0 else round((unrealized / cost) * 100, 2),
        })

    realized = sum(float(t.get("realized_profit") or 0.0) for t in load_core_trades() if str(t.get("side")).upper() == "SELL")
    return {
        "positions": positions,
        "rows": rows,
        "value": round(total_value, 2),
        "cost_basis": round(total_cost, 2),
        "unrealized_profit": round(total_unrealized, 2),
        "realized_profit": round(realized, 2),
        "total_profit": round(realized + total_unrealized, 2),
    }


def format_core_shares(value: Any) -> str:
    try:
        x = float(value)
        if abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
        return (f"{x:.6f}").rstrip("0").rstrip(".")
    except Exception:
        return str(value)


def validate_core_price_against_quote(ticker: str, price: float) -> Tuple[bool, str, Optional[float]]:
    if not CORE_REQUIRE_LIVE_QUOTE:
        return True, "Quote check disabled", None
    quotes = get_prices_batch([ticker])
    quote = quotes.get(ticker)
    if quote is None or quote <= 0:
        return False, "Live quote unavailable for core trade.", None
    deviation = abs(price - quote) / quote
    if deviation > CORE_QUOTE_DEVIATION_LIMIT:
        return (
            False,
            f"Core trade rejected: your price is too far from live quote.\n"
            f"Live quote: {round(quote, 2)}\n"
            f"Your price: {round(price, 2)}\n"
            f"Max deviation: {round(CORE_QUOTE_DEVIATION_LIMIT * 100, 2)}%",
            quote,
        )
    return True, "OK", quote


def core_target_for_ticker(plan: Dict[str, Any], ticker: str) -> Optional[Dict[str, Any]]:
    ticker = ticker.upper()
    for item in plan.get("top", []) or []:
        if str(item.get("ticker", "")).upper() == ticker:
            return item
    return None


def record_core_buy(
    ticker: str,
    shares: float,
    price: float,
    update_id: Optional[int] = None,
) -> Tuple[bool, str]:
    ticker = normalize_ticker(ticker) or ""
    if not ticker:
        return False, "Invalid ticker"
    if not CORE_LEDGER_ENABLED:
        return False, "Core ledger is disabled."
    if shares <= 0 or not math.isfinite(shares):
        return False, "Core shares must be positive and finite."
    if (not CORE_ALLOW_FRACTIONAL_SHARES) and abs(shares - round(shares)) > 1e-9:
        return False, "Fractional core shares are disabled."
    if not is_finite_positive(price):
        return False, "Core price must be positive and finite."
    if ticker not in WEALTH_CORE_UNIVERSE:
        return False, f"{ticker} is not in the core wealth universe."

    amount = shares * price
    if amount < CORE_MIN_TRADE_DOLLARS:
        return False, f"Core trade amount is below minimum {format_money(CORE_MIN_TRADE_DOLLARS)}."

    plan = current_core_plan_for_validation()
    target = core_target_for_ticker(plan, ticker)
    action = latest_core_plan_action_map(plan).get(ticker)
    if CORE_REQUIRE_ACTIVE_PLAN_FOR_BUY and not CORE_ALLOW_BUY_OUTSIDE_PLAN:
        if target is None:
            allowed = ", ".join(str(x.get("ticker")) for x in plan.get("top", [])[:WEALTH_CORE_TOP_N])
            return False, (
                f"Core buy rejected: {ticker} is not in the active core plan.\n"
                f"Current ranked core candidates: {allowed or 'none'}"
            )
        if action and str(action.get("action", "")).upper() in {"TRIM", "SELL", "AVOID"}:
            return False, f"Core buy rejected: current plan action for {ticker} is {action.get('action')}."

    ok, msg, quote = validate_core_price_against_quote(ticker, price)
    if not ok:
        return False, msg

    with db_tx() as conn:
        cash = get_cash(conn)
        if amount > cash:
            mark_update_processed_tx(conn, update_id, "rejected_core_insufficient_cash")
            return False, "Not enough cash for core buy."

        row = conn.execute("SELECT * FROM core_positions WHERE ticker = ?", (ticker,)).fetchone()
        now = now_ts()
        target_pct = None if target is None else float(target.get("target_account_pct", 0) or 0)
        plan_id = str(plan.get("plan_id"))

        if row is None:
            core_position_id = f"CORE_{ticker}_{int(now)}_{uuid.uuid4().hex[:8]}"
            new_shares = shares
            new_cost = amount
            avg_price = price
            entry_time = now
            highest = price
            conn.execute(
                """
                INSERT INTO core_positions(
                    ticker, core_position_id, strategy_version, shares,
                    avg_entry_price, cost_basis, entry_time, last_update_time,
                    highest, sleeve, target_account_pct, last_plan_id, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'CORE_WEALTH', ?, ?, '')
                """,
                (
                    ticker,
                    core_position_id,
                    WEALTH_STRATEGY_VERSION,
                    round(new_shares, 8),
                    round(avg_price, 6),
                    round(new_cost, 6),
                    now,
                    now,
                    round(highest, 6),
                    target_pct,
                    plan_id,
                ),
            )
        else:
            pos = row_to_core_position(row)
            core_position_id = pos["core_position_id"]
            old_shares = float(pos["shares"])
            old_cost = float(pos["cost_basis"])
            new_shares = old_shares + shares
            new_cost = old_cost + amount
            avg_price = new_cost / new_shares
            entry_time = float(pos["entry_time"])
            highest = max(float(pos.get("highest") or price), price)
            conn.execute(
                """
                UPDATE core_positions
                SET shares = ?, avg_entry_price = ?, cost_basis = ?,
                    last_update_time = ?, highest = ?, target_account_pct = ?,
                    last_plan_id = ?, strategy_version = ?
                WHERE ticker = ?
                """,
                (
                    round(new_shares, 8),
                    round(avg_price, 6),
                    round(new_cost, 6),
                    now,
                    round(highest, 6),
                    target_pct,
                    plan_id,
                    WEALTH_STRATEGY_VERSION,
                    ticker,
                ),
            )

        conn.execute(
            """
            INSERT INTO core_trades(
                id, core_position_id, ticker, side, shares, price, amount,
                realized_profit, time, strategy_version, plan_id, reason, created_at
            ) VALUES (?, ?, ?, 'BUY', ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                core_position_id,
                ticker,
                round(shares, 8),
                round(price, 6),
                round(amount, 6),
                now,
                WEALTH_STRATEGY_VERSION,
                plan_id,
                "core_plan_buy",
                now,
            ),
        )
        set_cash_tx(conn, cash - amount)
        mark_update_processed_tx(conn, update_id, "processed_core_buy")

    refresh_portfolio()
    audit("CORE_BUY", f"{ticker} shares={shares} price={price} amount={amount}")
    return True, (
        f"🏛️ CORE BUY RECORDED {ticker}\n\n"
        f"📦 Shares: {format_core_shares(shares)}\n"
        f"💵 Price: {round(price, 2)}\n"
        f"💰 Amount: {format_money(amount)}\n"
        f"🎯 Plan action: {None if action is None else action.get('action')}\n"
        f"🏦 Target account weight: {None if target is None else target.get('target_account_pct')}%\n"
        f"💵 Cash left: {format_money(portfolio['cash'])}"
    )


def record_core_sell(
    ticker: str,
    shares: float,
    price: float,
    update_id: Optional[int] = None,
) -> Tuple[bool, str]:
    ticker = normalize_ticker(ticker) or ""
    if not ticker:
        return False, "Invalid ticker"
    if not CORE_LEDGER_ENABLED:
        return False, "Core ledger is disabled."
    if shares <= 0 or not math.isfinite(shares):
        return False, "Core shares must be positive and finite."
    if not is_finite_positive(price):
        return False, "Core price must be positive and finite."

    ok, msg, quote = validate_core_price_against_quote(ticker, price)
    if not ok:
        return False, msg

    plan = current_core_plan_for_validation()
    action = latest_core_plan_action_map(plan).get(ticker)

    with db_tx() as conn:
        row = conn.execute("SELECT * FROM core_positions WHERE ticker = ?", (ticker,)).fetchone()
        if row is None:
            mark_update_processed_tx(conn, update_id, "rejected_core_no_position")
            return False, "No core position to sell."

        pos = row_to_core_position(row)
        current_shares = float(pos["shares"])
        if shares - current_shares > CORE_POSITION_EPSILON:
            mark_update_processed_tx(conn, update_id, "rejected_core_too_many_shares")
            return False, f"You only have {format_core_shares(current_shares)} core shares of {ticker}."

        shares = min(shares, current_shares)
        avg = float(pos["avg_entry_price"])
        proceeds = shares * price
        realized_profit = (price - avg) * shares
        remaining = current_shares - shares
        now = now_ts()
        plan_id = str(plan.get("plan_id"))

        conn.execute(
            """
            INSERT INTO core_trades(
                id, core_position_id, ticker, side, shares, price, amount,
                realized_profit, time, strategy_version, plan_id, reason, created_at
            ) VALUES (?, ?, ?, 'SELL', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                pos["core_position_id"],
                ticker,
                round(shares, 8),
                round(price, 6),
                round(proceeds, 6),
                round(realized_profit, 6),
                now,
                WEALTH_STRATEGY_VERSION,
                plan_id,
                "core_plan_sell",
                now,
            ),
        )

        if remaining <= CORE_POSITION_EPSILON:
            conn.execute("DELETE FROM core_positions WHERE ticker = ?", (ticker,))
        else:
            new_cost = avg * remaining
            target = core_target_for_ticker(plan, ticker)
            target_pct = None if target is None else float(target.get("target_account_pct", 0) or 0)
            conn.execute(
                """
                UPDATE core_positions
                SET shares = ?, cost_basis = ?, last_update_time = ?,
                    target_account_pct = ?, last_plan_id = ?
                WHERE ticker = ?
                """,
                (round(remaining, 8), round(new_cost, 6), now, target_pct, plan_id, ticker),
            )

        cash = get_cash(conn)
        set_cash_tx(conn, cash + proceeds)
        mark_update_processed_tx(conn, update_id, "processed_core_sell")

    refresh_portfolio()
    audit("CORE_SELL", f"{ticker} shares={shares} price={price} proceeds={proceeds} profit={realized_profit}")
    return True, (
        f"🏛️ CORE SELL RECORDED {ticker}\n\n"
        f"📦 Shares: {format_core_shares(shares)}\n"
        f"💵 Price: {round(price, 2)}\n"
        f"💰 Proceeds: {format_money(proceeds)}\n"
        f"📊 Realized core P/L: {format_money(realized_profit)} ({format_pct((price - avg) / avg * 100 if avg > 0 else None)})\n"
        f"🎯 Plan action: {None if action is None else action.get('action')}\n"
        f"💵 Cash now: {format_money(portfolio['cash'])}"
    )


def format_core_portfolio_report() -> str:
    details = core_position_market_value_details()
    rows = details.get("rows", []) or []
    snapshot = compute_equity_snapshot_data()
    msg = (
        "🏛️ CORE WEALTH PORTFOLIO\n\n"
        f"💵 Shared cash: {format_money(snapshot['cash'])}\n"
        f"🏦 Core value: {format_money(float(details.get('value', 0) or 0))}\n"
        f"📏 Cost basis: {format_money(float(details.get('cost_basis', 0) or 0))}\n"
        f"📈 Unrealized P/L: {format_money(float(details.get('unrealized_profit', 0) or 0))}\n"
        f"✅ Realized core P/L: {format_money(float(details.get('realized_profit', 0) or 0))}\n"
        f"💼 Total equity: {format_money(snapshot['equity'])}\n\n"
    )
    if not rows:
        return msg + "No core positions recorded yet. Use wealthplan, then corebuy after broker execution."

    for row in rows:
        msg += (
            f"📦 {row['ticker']}\n"
            f"Shares: {format_core_shares(row['shares'])}\n"
            f"Avg: {round(float(row['avg_entry_price']), 2)} | Now: {round(float(row['mark_price']), 2)}\n"
            f"Value: {format_money(float(row['market_value']))}\n"
            f"P/L: {format_money(float(row['unrealized_profit']))} ({format_pct(row.get('unrealized_pct'))})\n"
            f"Target account weight: {row.get('target_account_pct')}%\n\n"
        )
    return msg[:MAX_TELEGRAM_MESSAGE]


def format_core_pnl_report() -> str:
    details = core_position_market_value_details()
    trades = load_core_trades()
    buys = [t for t in trades if str(t.get("side")).upper() == "BUY"]
    sells = [t for t in trades if str(t.get("side")).upper() == "SELL"]
    return (
        "🏛️ CORE WEALTH P/L\n\n"
        f"🏦 Core value: {format_money(float(details.get('value', 0) or 0))}\n"
        f"📏 Cost basis: {format_money(float(details.get('cost_basis', 0) or 0))}\n"
        f"📈 Unrealized P/L: {format_money(float(details.get('unrealized_profit', 0) or 0))}\n"
        f"✅ Realized P/L: {format_money(float(details.get('realized_profit', 0) or 0))}\n"
        f"💰 Total core P/L: {format_money(float(details.get('total_profit', 0) or 0))}\n\n"
        f"Buy records: {len(buys)}\n"
        f"Sell records: {len(sells)}"
    )


def format_core_exposure_report() -> str:
    snapshot = compute_equity_snapshot_data()
    plan = compute_wealth_core_plan()
    details = core_position_market_value_details()
    equity = float(snapshot.get("equity", 0) or 0)
    actual_pct = 0.0 if equity <= 0 else (float(details.get("value", 0) or 0) / equity) * 100
    target_pct = float(plan.get("target_core_account_pct", 0) or 0)
    return (
        "🏛️ CORE EXPOSURE\n\n"
        f"💼 Total equity: {format_money(equity)}\n"
        f"🏦 Core value: {format_money(float(details.get('value', 0) or 0))}\n"
        f"🎯 Target core: {round(target_pct, 2)}% of account\n"
        f"📊 Actual core: {round(actual_pct, 2)}% of account\n"
        f"📐 Drift: {round(actual_pct - target_pct, 2)} percentage points\n\n"
        "Use wealthplan for ranked BUY/ADD/HOLD/TRIM/SELL actions."
    )


def write_json_file(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(safe_convert(data), f, indent=2)




def format_combined_portfolio_report() -> str:
    refresh_portfolio()
    cash = float(portfolio["cash"])
    swing_positions = portfolio["positions"]
    core_rows = core_position_market_value_details().get("rows", []) if CORE_LEDGER_ENABLED else []
    snapshot = compute_equity_snapshot_data()

    if not swing_positions and not core_rows:
        return (
            "📋 PORTFOLIO\n\n"
            f"💵 Cash: {format_money(cash)}\n"
            f"🏦 Total Equity: {format_money(snapshot['equity'])}\n"
            "No open swing or core positions"
        )

    tickers = list(swing_positions.keys())
    prices = get_prices_batch(tickers)
    msg = (
        "📋 PORTFOLIO\n\n"
        f"💵 Cash: {format_money(cash)}\n"
        f"⚡ Swing value: {format_money(snapshot.get('swing_positions_value', 0))}\n"
        f"🏛️ Core value: {format_money(snapshot.get('core_positions_value', 0))}\n"
        f"🏦 Total equity: {format_money(snapshot['equity'])}\n\n"
    )

    if swing_positions:
        msg += "⚡ SWING / TACTICAL POSITIONS\n\n"
        for ticker, pos in swing_positions.items():
            current_price = prices.get(ticker, pos["price"])
            entry = pos["price"]
            shares = pos["shares"]
            pnl = (current_price - entry) * shares
            risk_per_share = pos.get("risk_per_share")
            r_now = None
            if isinstance(risk_per_share, (int, float)) and risk_per_share > 0:
                r_now = (current_price - entry) / risk_per_share
            msg += (
                f"📦 {ticker}\n"
                f"Shares: {shares}\n"
                f"Entry: {round(entry, 2)}\n"
                f"Now: {round(current_price, 2)}\n"
                f"🛡️ Stop: {round(pos['stop'], 2)}\n"
                f"📈 High: {round(pos['highest'], 2)}\n"
                f"🎯 R now: {None if r_now is None else round(r_now, 2)}\n"
                f"💰 P/L: {format_money(pnl)}\n\n"
            )

    if core_rows:
        msg += "🏛️ CORE WEALTH POSITIONS\n\n"
        for row in core_rows:
            msg += (
                f"📦 {row['ticker']}\n"
                f"Shares: {format_core_shares(row['shares'])}\n"
                f"Avg: {round(float(row['avg_entry_price']), 2)}\n"
                f"Now: {round(float(row['mark_price']), 2)}\n"
                f"Value: {format_money(float(row['market_value']))}\n"
                f"P/L: {format_money(float(row['unrealized_profit']))} ({format_pct(row.get('unrealized_pct'))})\n\n"
            )

    return msg[:MAX_TELEGRAM_MESSAGE]


def table_rows(table: str) -> List[Dict[str, Any]]:

    allowed = {

        "positions",

        "trades",

        "signals",

        "equity_snapshots",

        "withdrawals",

        "cooldowns",

        "breakout_memory",

        "core_positions",

        "core_trades",

        "core_signals",

    }



    if table not in allowed:

        raise ValueError("Table export not allowed")



    conn = db_connect()



    try:

        rows = conn.execute(f"SELECT * FROM {table}").fetchall()

        return [dict(row) for row in rows]



    finally:

        conn.close()





def export_state_bundle(prefix: str = "bot_state_export") -> str:

    """

    Exports portfolio, trades, signals, withdrawals, risk snapshot,

    and key database tables into a zip file.



    This is safe to forward for analysis because it does not include API keys,

    Telegram token, or environment variables.

    """

    refresh_portfolio()



    ts = ny_now().strftime("%Y%m%d_%H%M%S")

    export_root = os.path.join(DATA_DIR, "exports")

    export_dir = os.path.join(export_root, f"{prefix}_{ts}")



    os.makedirs(export_dir, exist_ok=True)



    risk = open_risk_details()

    withdrawal_plan = compute_withdrawal_plan()



    write_json_file(

        os.path.join(export_dir, "portfolio.json"),

        portfolio

    )



    write_json_file(

        os.path.join(export_dir, "trades.json"),

        load_trades()

    )



    write_json_file(

        os.path.join(export_dir, "signals.json"),

        load_signals()

    )



    write_json_file(

        os.path.join(export_dir, "withdrawals.json"),

        load_withdrawals()

    )



    write_json_file(

        os.path.join(export_dir, "open_risk.json"),

        risk

    )



    write_json_file(

        os.path.join(export_dir, "withdrawal_plan.json"),

        withdrawal_plan

    )



    write_json_file(

        os.path.join(export_dir, "meta_snapshot.json"),

        {

            "strategy_version": STRATEGY_VERSION,

            "ny_time": ny_now().strftime("%Y-%m-%d %H:%M:%S %Z"),

            "last_scan_day": get_meta("last_scan_day"),

            "last_scan_bar_date": get_meta("last_scan_bar_date"),

            "last_equity_snapshot_date": get_meta("last_equity_snapshot_date"),

            "withdrawal_high_water_mark": get_meta("withdrawal_high_water_mark"),

            "withdrawal_hwm_initialized_at": get_meta("withdrawal_hwm_initialized_at"),

            "positions_count": len(portfolio["positions"]),

            "core_positions_count": len(load_core_positions()) if CORE_LEDGER_ENABLED else 0,

            "cash": portfolio["cash"],

            "panic_mode": PANIC_MODE,

            "performance_base_capital": get_meta("performance_base_capital"),

        }

    )



    for table in [

        "positions",

        "trades",

        "signals",

        "equity_snapshots",

        "withdrawals",

        "cooldowns",

        "breakout_memory",

        "core_positions",

        "core_trades",

        "core_signals",

    ]:

        write_json_file(

            os.path.join(export_dir, f"{table}.table.json"),

            table_rows(table)

        )



    # CSV versions are useful for analysis.

    try:

        trades = load_trades()

        if trades:

            pd.DataFrame(safe_convert(trades)).to_csv(

                os.path.join(export_dir, "trades.csv"),

                index=False

            )



        positions_rows = []

        for ticker, pos in portfolio["positions"].items():

            row = {"ticker": ticker}

            row.update(safe_convert(pos))

            positions_rows.append(row)



        if positions_rows:

            pd.DataFrame(positions_rows).to_csv(

                os.path.join(export_dir, "positions.csv"),

                index=False

            )



    except Exception as exc:

        print(f"[CSV EXPORT WARNING] {exc}")



    zip_path = f"{export_dir}.zip"



    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:

        for root, _, files in os.walk(export_dir):

            for filename in files:

                full_path = os.path.join(root, filename)

                arcname = os.path.relpath(full_path, export_dir)

                z.write(full_path, arcname)



    return zip_path





def send_json_export(data: Any, filename: str, caption: str = "") -> None:

    path = os.path.join(DATA_DIR, filename)



    write_json_file(path, data)



    send_document(path, caption=caption or filename)



def reset_all_paper_state(update_id: Optional[int] = None) -> Tuple[bool, str, Optional[str]]:

    global last_signals, missing_price_counts

    """

    Dangerous command used only when moving from paper testing to live.



    It exports a backup first, then clears trading state.

    It intentionally keeps Telegram processed update history and legacy migration flag

    so old Telegram commands and old JSON files are not accidentally reprocessed.

    """

    backup_path = export_state_bundle(prefix="pre_reset_backup")



    with db_tx() as conn:

        conn.execute("DELETE FROM positions")

        conn.execute("DELETE FROM trades")

        conn.execute("DELETE FROM signals")

        conn.execute("DELETE FROM cooldowns")

        conn.execute("DELETE FROM breakout_memory")

        conn.execute("DELETE FROM equity_snapshots")

        conn.execute("DELETE FROM withdrawals")
        conn.execute("DELETE FROM core_positions")
        conn.execute("DELETE FROM core_trades")
        conn.execute("DELETE FROM core_signals")



        # Set cash to zero as a fail-safe.

        # You will explicitly set real live cash afterward.

        set_cash_tx(conn, 0.0)



        conn.execute(

            """

            DELETE FROM meta

            WHERE key IN (

                'last_scan_day',

                'last_scan_bar_date',

                'last_equity_snapshot_date',

                'withdrawal_high_water_mark',

                'withdrawal_hwm_initialized_at',

                'last_withdrawal_check_date',

                'last_withdrawal_alert_ts',

                'performance_base_capital',
                'last_wealth_core_month',
                'last_wealth_core_alert_ts'

            )

            """

        )



        mark_update_processed_tx(conn, update_id, "processed_resetall")



    refresh_portfolio()



    last_signals = {}

    missing_price_counts = {}



    audit("RESET_ALL", "Paper/live state reset; backup exported first")



    return True, (

        "🧨 RESET ALL COMPLETE\n\n"

        "A backup was exported first.\n\n"

        "Current bot state:\n"

        f"💵 Cash: {format_money(portfolio['cash'])}\n"

        f"📦 Swing positions: {len(portfolio['positions'])}\n"
        f"🏛️ Core positions: {len(load_core_positions()) if CORE_LEDGER_ENABLED else 0}\n\n"

        "Next live-start commands:\n"

        "1) setcash YOUR_REAL_CASH\n"

        "2) withdrawinit\n"

        "3) scanstatus\n"

        "4) portfolio\n"

        "5) openrisk"

    ), backup_path

 



# -----------------------------------------------------------------------------



# COMMANDS



# -----------------------------------------------------------------------------



 



def handle_command(text: str, update_id: Optional[int] = None) -> None:



    global portfolio, last_signals

    global PANIC_MODE



    text = (text or "").strip()



    if not text:

        return



    text_lower = text.lower()



    audit("COMMAND", text)



    if text_lower == "panic":



        PANIC_MODE = True



        audit("PANIC_ON")



        send(

            "🚨 PANIC MODE ENABLED\n\n"

            "🔒 Scanning disabled.\n"

            "🛡️ Position management still active."

        )



        return



    if text_lower == "resume":



        PANIC_MODE = False



        audit("PANIC_OFF")



        send("✅ Bot resumed.\n\n🔎 Scanning enabled again.")



        return



    if text_lower in {"help", "/help"}:



        send(



            "Commands:\n"

            "pnl | equity | openrisk | winrate | expectancy | stats | duration | summary | portfolio | scanstatus | bearstatus | allocationplan | riskstatus | sleevestatus\n"

            "wealthplan | wealthstatus | corestatus | coreportfolio | corepnl | coreexposure\n"

            "setupstats | showtrades | showsignals | resetsignals | resetscan | forcescan | download_trades\n"

            "testchannel | postchannelterms\n"

            "download_state | download_portfolio | download_signals | download_withdrawals\n"

            "withdrawinit | withdrawplan | withdrawdone AMOUNT | showwithdrawals\n"

            "resetall  (then resetall CONFIRM-LIVE)\n"

            "setcash AMOUNT\n"

            "voidbuy TICKER  (undo mistaken swing buy without fake sell trade)\n"

            "corebuy TICKER SHARES at PRICE\n"

            "coresell TICKER SHARES at PRICE\n"

            "editbuy TICKER PRICE\n"

            "editsell TICKER PRICE  (edits latest trade for ticker; adjusts cash)\n"

            "bought TICKER SHARES at PRICE\n"

            "sold TICKER SHARES at PRICE"
        )



        return



    if text_lower == "testchannel":

        ok, info = send_public_signal(

            "🧪 TEST SIGNAL CHANNEL\n\n"

            "If you see this, public channel forwarding works."

        )



        if ok:

            send("✅ Public signal channel test sent.")

        else:

            send(f"❌ Public signal channel test failed:\n\n{info}")



        return



    if text_lower == "postchannelterms":

        ok, info = send_public_signal(public_channel_terms_text())



        if ok:

            send(

                "✅ Channel disclaimer posted.\n\n"

                "Now open the channel and pin that message manually."

            )

        else:

            send(f"❌ Channel disclaimer failed:\n\n{info}")



        return



    if text_lower == "pnl":



        perf = realized_performance_all_time()



        send(

            "📊 REALIZED P/L — ALL TIME\n\n"

            f"💰 Realized P/L: {format_money(perf['profit'])} "

            f"({format_pct(perf['pct'])})\n"

            f"📏 Base capital: {format_money(perf['base_capital'])}\n"

            f"🧾 Trade records: {perf['trade_records']}\n\n"

            "Note: this is realized P/L only. Open unrealized P/L is not included."

        )



        return



 



    if text_lower == "equity":

        snapshot = compute_equity_snapshot_data()

        send(
            "💼 ACCOUNT EQUITY\n\n"
            f"💵 Cash: {format_money(snapshot['cash'])}\n"
            f"⚡ Swing positions: {format_money(snapshot.get('swing_positions_value', 0))}\n"
            f"🏛️ Core wealth positions: {format_money(snapshot.get('core_positions_value', 0))}\n"
            f"📦 Total positions: {format_money(snapshot['positions_value'])}\n"
            f"🏦 Total Equity: {format_money(snapshot['equity'])}"
        )

        return


    if text_lower == "openrisk":



        details = open_risk_details()



        send(

            "🛡️ OPEN RISK\n\n"

            f"💼 Equity: {format_money(details['equity'])}\n"

            f"⚠️ Initial open risk: {format_money(details['initial_risk_dollars'])} "

            f"({round(details['initial_risk_pct'] * 100, 2)}%)\n"

            f"📉 Current stop risk: {format_money(details['current_stop_risk_dollars'])} "

            f"({round(details['current_stop_risk_pct'] * 100, 2)}%)\n"

            f"🚦 Max allowed: {round(MAX_TOTAL_RISK * 100, 2)}%"

        )



        return



 



    if text_lower == "winrate":



        send(f"🏆 Win Rate: {win_rate()}%")



        return



 



    if text_lower == "expectancy":



        e = expectancy_summary()



        p = position_level_summary()



        send(

            "📈 EXPECTANCY\n\n"

            f"🧾 Trades: {e['trades']}\n"

            f"🎯 R-trades: {e['r_trades']}\n"

            f"📊 Avg R/trade: {e['avg_r']}\n"

            f"📍 Median R: {e['median_r']}\n"

            f"✅ Avg win R: {e['avg_win_r']}\n"

            f"❌ Avg loss R: {e['avg_loss_r']}\n"

            f"⚖️ Profit factor: {e['profit_factor']}\n\n"

            f"📦 Position-level count: {p['positions_closed_or_partially_closed']}\n"

            f"📊 Avg position R: {p['avg_position_r']}\n"

            f"📍 Median position R: {p['median_position_r']}"

        )



        return



 



    if text_lower == "stats":



        best, worst = ticker_stats()



        send(

            f"📊 TICKER STATS\n\n"

            f"📈 Best: {best[0]} ({format_money(best[1])})\n"

            f"📉 Worst: {worst[0]} ({format_money(worst[1])})"

        )



        return



 



    if text_lower == "duration":



        send(f"⏱️ Avg Trade Duration: {avg_trade_duration()}")



        return



 



    if text_lower == "summary":



        perf = realized_performance_all_time()



        wr = win_rate()



        best, worst = ticker_stats()



        duration = avg_trade_duration()



        e = expectancy_summary()



        send(

            "📋 SUMMARY\n\n"

            f"📊 Realized P/L all-time: {format_money(perf['profit'])} ({format_pct(perf['pct'])})\n"

            f"🏆 Win Rate: {wr}%\n"

            f"⏱️ Avg Duration: {duration}\n"

            f"🎯 Avg R: {e['avg_r']}\n"

            f"⚖️ Profit Factor: {e['profit_factor']}\n\n"

            f"📈 Best: {best[0]} ({format_money(best[1])})\n"

            f"📉 Worst: {worst[0]} ({format_money(worst[1])})"

        )



        return



    if text_lower == "scanstatus":

        refresh_portfolio()

        last_scan_day = get_meta("last_scan_day")

        details = open_risk_details()



        send(

            "🧭 SCAN STATUS\n\n"

            f"🕒 NY time: {ny_now().strftime('%Y-%m-%d %H:%M %Z')}\n"

            f"📅 Last scan day: {last_scan_day}\n"

            f"📦 Positions: {len(portfolio['positions'])}/{MAX_OPEN_POSITIONS}\n"

            f"💵 Cash: {format_money(portfolio['cash'])}\n"

            f"💼 Equity: {format_money(details['equity'])}\n"

            f"⚠️ Initial risk: {round(details['initial_risk_pct'] * 100, 2)}%\n"

            f"🛡️ Current stop risk: {round(details['current_stop_risk_pct'] * 100, 2)}%\n"

            f"🕯️ Fresh candle required: {yes_no(REQUIRE_FRESH_DAILY_CANDLE)}\n"

            f"🚨 Panic mode: {yes_no(PANIC_MODE)}"

        )



        return



    if text_lower == "bearstatus":
        details = bear_regime_details(market_details=market_regime_details())
        open_bear = count_open_bear_positions()

        send(
            "🐻 BEAR SLEEVE STATUS\n\n"
            f"Enabled: {yes_no(BEAR_SLEEVE_ENABLED)}\n"
            f"Active now: {yes_no(bool(details.get('active')))}\n"
            f"Bear score: {details.get('score')}/{details.get('max_score')}\n"
            f"Entry threshold: {BEAR_ENTRY_SCORE}\n"
            f"Exit/calm threshold: {BEAR_EXIT_SCORE}\n"
            f"Open bear positions: {open_bear}/{BEAR_MAX_OPEN_POSITIONS}\n"
            f"Universe: {', '.join(BEAR_WATCHLIST)}\n\n"
            "Live candidate: robust 3x inverse VCP sleeve.\n"
            "Aggressive 2x reclaim remains research-only, not live-integrated."
        )

        return

    if text_lower == "resetscan":

        with db_tx() as conn:

            conn.execute(

                "DELETE FROM meta WHERE key IN ('last_scan_day', 'last_scan_bar_date')"

            )



        send("🔄 Scan day reset.\n\nBot may scan again during the scan window.")

        return



    if text_lower == "forcescan":

        send("🔎 Manual scan started.\n\nCheck Telegram/logs for signals and scan summary.")



        current_ny = ny_now()



        official_scan_window = (

            (

                is_market_weekday(current_ny)

                and is_near_close_scan_window(current_ny)

            )

            or

            (

                not is_market_weekday(current_ny)

                and current_ny.weekday() == 5

                and is_morning_scan_window(current_ny)

            )

        )



        scanned_ok = scan_market()



        if scanned_ok and official_scan_window:

            today = current_ny.date().isoformat()

            expected_bar = expected_daily_bar_date()



            set_meta("last_scan_day", today)



            if expected_bar:

                set_meta("last_scan_bar_date", expected_bar)



            send("✅ Manual scan completed.\n\n📅 Marked done for today.")

        elif scanned_ok:

            send(

                "✅ Manual scan completed.\n\n"

                "ℹ️ Not marked done because this was outside the official scan window."

            )

        else:

            send("⚠️ Manual scan completed, but historical data was not usable.")



        return



    if text_lower == "wealthplan":
        plan = compute_wealth_core_plan()
        save_core_plan_signal(plan)
        send(format_wealth_core_plan(plan))
        return

    if text_lower == "wealthstatus":
        alloc = dynamic_portfolio_allocation_targets()
        send(
            "🏛️ WEALTH / CORE LEDGER STATUS v3.6\n\n"
            f"Enabled: {yes_no(WEALTH_SLEEVE_ENABLED)}\n"
            f"Strategy: {WEALTH_STRATEGY_VERSION}\n"
            f"Dynamic allocation: {yes_no(WEALTH_DYNAMIC_ALLOCATION_ENABLED)}\n"
            f"Vol weighting: {yes_no(WEALTH_VOL_WEIGHTING_ENABLED)}\n"
            f"Cluster control: {yes_no(WEALTH_CLUSTER_CONTROL_ENABLED)}\n"
            f"Top assets: {WEALTH_CORE_TOP_N}\n"
            f"Current core target: {alloc.get('core_wealth_pct')}% of account\n"
            f"Long VCP target: {alloc.get('long_vcp_tactical_pct')}% of account\n"
            f"Bear tactical target: {alloc.get('bear_inverse_tactical_pct')}% of account\n"
            f"Cash reserve target: {alloc.get('cash_reserve_pct')}% of account\n"
            f"Last wealth month: {get_meta('last_wealth_core_month')}\n"
            f"Public channel: ❌ never used for this sleeve\n\n"
            "Commands:\n"
            "wealthplan — ranked BUY/ADD/HOLD/TRIM/SELL plan\n"
            "corebuy TICKER SHARES at PRICE — record core buy\n"
            "coresell TICKER SHARES at PRICE — record core sell\n"
            "coreportfolio | corepnl | coreexposure | corestatus\n"
            "allocationplan | riskstatus | sleevestatus"
        )
        return


    if text_lower in {"corestatus", "coreledger"}:
        latest = load_latest_core_signal()
        details = core_position_market_value_details()
        alloc = dynamic_portfolio_allocation_targets()
        send(
            "🏛️ CORE LEDGER STATUS v3.6\n\n"
            f"Enabled: {yes_no(CORE_LEDGER_ENABLED)}\n"
            f"Strategy: {WEALTH_STRATEGY_VERSION}\n"
            f"Core target now: {alloc.get('core_wealth_pct')}% of account\n"
            f"Core value: {format_money(float(details.get('value', 0) or 0))}\n"
            f"Core realized P/L: {format_money(float(details.get('realized_profit', 0) or 0))}\n"
            f"Core unrealized P/L: {format_money(float(details.get('unrealized_profit', 0) or 0))}\n"
            f"Active plan: {None if latest is None else latest.get('plan_date')}\n\n"
            "Commands:\n"
            "wealthplan — ranked BUY/ADD/HOLD/TRIM/SELL plan\n"
            "corebuy TICKER SHARES at PRICE — record broker core buy\n"
            "coresell TICKER SHARES at PRICE — record broker core sell\n"
            "coreportfolio | corepnl | coreexposure"
        )
        return

    if text_lower == "coreportfolio":
        send(format_core_portfolio_report())
        return

    if text_lower == "corepnl":
        send(format_core_pnl_report())
        return

    if text_lower == "coreexposure":
        send(format_core_exposure_report())
        return

    core_cmd = re.fullmatch(
        r"(?i)\s*(corebuy|coresell)\s+([A-Z0-9.\-]{1,15})\s+([0-9]+(?:\.[0-9]+)?)\s+(?:at|@)\s+([0-9]+(?:\.[0-9]+)?)\s*",
        text,
    )

    if core_cmd:
        action = core_cmd.group(1).lower()
        ticker = normalize_ticker(core_cmd.group(2))
        shares = float(core_cmd.group(3))
        price = float(core_cmd.group(4))

        if not ticker:
            send("Invalid ticker")
            return

        if action == "corebuy":
            ok, msg = record_core_buy(ticker, shares, price, update_id=update_id)
            send(msg if ok else "❌ ERROR: " + msg)
            return

        if action == "coresell":
            ok, msg = record_core_sell(ticker, shares, price, update_id=update_id)
            send(msg if ok else "❌ ERROR: " + msg)
            return

    if text_lower in {"allocationplan", "allocplan"}:
        send(format_portfolio_allocation_plan())
        return

    if text_lower == "riskstatus":
        send(format_portfolio_risk_guard())
        return

    if text_lower == "sleevestatus":
        send(format_sleeve_performance_report())
        return

    if text_lower == "withdrawinit":

        snapshot = compute_equity_snapshot_data()

        equity = float(snapshot["equity"])



        set_withdrawal_hwm(equity)



        send(

            "🏔️ WITHDRAWAL HIGH-WATER MARK RESET\n\n"

            f"💼 Current equity: {format_money(equity)}\n\n"

            "Future withdrawal signals will only use profits above this level."

        )



        return



    if text_lower == "withdrawplan":

        plan = compute_withdrawal_plan()



        if not plan["initialized"]:

            send(

                "🏦 WITHDRAWAL PLAN\n\n"

                f"💼 Equity: {format_money(plan['equity'])}\n"

                f"💵 Cash: {format_money(plan['cash'])}\n\n"

                f"⚠️ {plan['reason']}"

            )



            return



        send(

            "🏦 WITHDRAWAL PLAN\n\n"

            f"📊 Phase: {plan['phase']}\n"

            f"💼 Equity: {format_money(plan['equity'])}\n"

            f"💵 Cash: {format_money(plan['cash'])}\n"

            f"🏔️ High-water mark: {format_money(plan['high_water_mark'])}\n"

            f"📈 Profit above HWM: {format_money(plan['profit_above_hwm'])}\n\n"

            f"📤 Withdrawal rate: {round(plan['rate'] * 100, 2)}%\n"

            f"🧮 Gross suggested: {format_money(plan['gross_suggested'])}\n"

            f"💵 Cash cap: {format_money(plan['cash_cap'])}\n"

            f"✅ Suggested withdrawal: {format_money(plan['suggested'])}\n\n"

            f"🗓️ Days since withdrawal/review start: {plan['days_since_clock']}\n"

            f"🚦 Eligible: {yes_no(plan['eligible'])}\n"

            f"ℹ️ Reason: {plan['reason']}"

        )



        return



    if text_lower.startswith("withdrawdone"):

        parts = text.split(maxsplit=2)



        if len(parts) < 2:

            send("Usage: withdrawdone 250")

            return



        try:

            amount = float(parts[1])

        except ValueError:

            send("Invalid amount")

            return



        note = parts[2] if len(parts) >= 3 else ""



        ok, msg = record_withdrawal(

            amount,

            note=note,

            update_id=update_id

        )



        send(msg if ok else "❌ ERROR: " + msg)



        return



    if text_lower == "showwithdrawals":

        withdrawals = load_withdrawals()



        if not withdrawals:

            send("🏦 WITHDRAWALS\n\nNo withdrawals recorded yet.")

            return



        total = sum(float(w["amount"]) for w in withdrawals)



        msg = (

            "🏦 WITHDRAWALS\n\n"

            f"💸 Total withdrawn: {format_money(total)}\n"

            f"🧾 Count: {len(withdrawals)}\n\n"

        )



        for item in withdrawals[-10:]:

            dt = datetime.fromtimestamp(

                item["time"],

                NY_TZ

            ).strftime("%Y-%m-%d")



            msg += (

                f"📅 {dt}\n"

                f"Amount: {format_money(item['amount'])}\n"

                f"Equity before: {format_money(item['equity_before'])}\n"

                f"HWM after: {format_money(item['high_water_mark_after'])}\n\n"

            )



        send(msg[:MAX_TELEGRAM_MESSAGE])



        return



    if text_lower == "portfolio":
        send(format_combined_portfolio_report())
        return

    if text_lower == "showportfolio_raw":

        refresh_portfolio()



        send_json_export(

            portfolio,

            "portfolio_raw.json",

            "portfolio_raw.json"

        )



        return



 



    if text_lower == "showtrades":

        send_json_export(

            load_trades(),

            "trades_export.json",

            "trades_export.json"

        )



        return



 



    if text_lower == "showsignals":

        last_signals = load_signals()



        send_json_export(

            last_signals,

            "signals_export.json",

            "signals_export.json"

        )



        return



 



    if text_lower == "resetsignals":



        clear_signals()



        if update_id is not None:



            mark_update_processed(update_id, "processed_resetsignals")



        send("🔄 Signals reset.\n\nTrade history and portfolio are unchanged.")



        return



 



    if text_lower == "setupstats":



        trades = load_trades()



        breakout = [t for t in trades if t.get("entry_data", {}).get("setup_type") == "breakout"]



        pullback = [t for t in trades if t.get("entry_data", {}).get("setup_type") == "pullback"]



 



        def stats(trades_list: List[Dict[str, Any]]) -> str:



            if not trades_list:



                return "0 trades"



            total = sum(float(t.get("profit", 0)) for t in trades_list)



            win = sum(1 for t in trades_list if float(t.get("profit", 0)) > 0)



            wr = (win / len(trades_list)) * 100



            r_vals = [float(t["r_multiple"]) for t in trades_list if t.get("r_multiple") is not None]



            avg_r = round(sum(r_vals) / len(r_vals), 3) if r_vals else None



            return f"{len(trades_list)} trades | P/L: {format_money(total)} | WR: {round(wr, 2)}% | Avg R: {avg_r}"



 



        send(

            f"⚙️ SETUP STATS\n\n"

            f"🚀 Breakout: {stats(breakout)}\n"

            f"🔁 Pullback: {stats(pullback)}"

        )



        return



 



    if text_lower == "download_trades":



        path = os.path.join(DATA_DIR, "trades_export.json")



        with open(path, "w", encoding="utf-8") as f:



            json.dump(safe_convert(load_trades()), f, indent=2)



        send_document(path, caption="trades_export.json")



        return



    if text_lower == "download_state":

        path = export_state_bundle(prefix="bot_state_export")



        send_document(

            path,

            caption="bot_state_export.zip"

        )



        return



    if text_lower == "download_portfolio":

        refresh_portfolio()



        send_json_export(

            portfolio,

            "portfolio_export.json",

            "portfolio_export.json"

        )



        return



    if text_lower == "download_signals":

        send_json_export(

            load_signals(),

            "signals_export.json",

            "signals_export.json"

        )



        return



    if text_lower == "download_withdrawals":

        send_json_export(

            load_withdrawals(),

            "withdrawals_export.json",

            "withdrawals_export.json"

        )



        return



    if text_lower == "resetall":

        send(

            "⚠️ DANGEROUS RESET COMMAND\n\n"

            "This will export a backup, then clear:\n"

            "positions, trades, signals, cooldowns, breakout memory, equity snapshots, withdrawals, and cash.\n\n"

            "It will NOT delete Telegram update history, so old commands will not be reprocessed.\n\n"

            "To confirm, send exactly:\n"

            "resetall CONFIRM-LIVE"

        )



        return



    if text_lower == "resetall confirm-live":

        ok, msg, backup_path = reset_all_paper_state(update_id=update_id)



        if backup_path:

            send_document(

                backup_path,

                caption="pre_reset_backup.zip"

            )



        send(msg if ok else "❌ ERROR: " + msg)



        return



    if text_lower.startswith("voidbuy"):

        parts = text.split()



        if len(parts) != 2:

            send("Usage: voidbuy TICKER")

            return



        ticker = normalize_ticker(parts[1])



        if not ticker:

            send("Invalid ticker")

            return



        ok, msg = void_buy(

            ticker,

            update_id=update_id

        )



        send(msg if ok else "❌ ERROR: " + msg)



        return

 



    if text_lower.startswith("setcash"):



        parts = text.split()



        if len(parts) != 2:



            send("Usage: setcash 1670.15")



            return



        try:



            amount = float(parts[1])



        except ValueError:



            send("Invalid number")



            return



        if not math.isfinite(amount) or amount < 0:



            send("Cash must be finite and non-negative")



            return



        with db_tx() as conn:



            set_cash_tx(conn, amount)



            maybe_set_performance_base_from_cash_tx(conn, amount)



            mark_update_processed_tx(conn, update_id, "processed_setcash")



        refresh_portfolio()



        send(f"💵 Cash updated to {format_money(amount)}")



        return



 



    if text_lower.startswith("editbuy"):



        parts = text.split()



        if len(parts) != 3:



            send("Usage: editbuy TICKER PRICE")



            return



        ticker = normalize_ticker(parts[1])



        if not ticker:



            send("Invalid ticker")



            return



        try:



            new_price = float(parts[2])



        except ValueError:



            send("Invalid price")



            return



        if not is_finite_positive(new_price):



            send("Price must be positive and finite")



            return



 



        with db_tx() as conn:



            row = conn.execute("SELECT * FROM positions WHERE ticker = ?", (ticker,)).fetchone()



            if row is None:



                mark_update_processed_tx(conn, update_id, "rejected_editbuy_no_position")



                send("Position not found")



                return



            pos = row_to_position(row)



            old_price = pos["price"]



            shares = int(pos["shares"])



            cash = get_cash(conn)



            cash_adjustment = (old_price - new_price) * shares



            new_cash = cash + cash_adjustment



            if new_cash < 0:



                mark_update_processed_tx(conn, update_id, "rejected_editbuy_negative_cash")



                send("Edit would make cash negative")



                return



            risk_per_share = new_price - pos["initial_stop"]



            if risk_per_share <= 0:



                mark_update_processed_tx(conn, update_id, "rejected_editbuy_invalid_risk")



                send("Edit rejected: new entry price must be above initial_stop. Use a manual correction instead.")



                return



            pos["price"] = new_price



            pos["risk_per_share"] = risk_per_share



            pos["highest"] = max(pos.get("highest", new_price), new_price)



            set_cash_tx(conn, new_cash)



            upsert_position_tx(conn, ticker, pos)



            mark_update_processed_tx(conn, update_id, "processed_editbuy")



        refresh_portfolio()



        send(



            f"BUY UPDATED {ticker}\n"



            f"Old: {round(old_price, 2)}\n"



            f"New: {round(new_price, 2)}\n"



            f"Cash adjusted by: {format_money(cash_adjustment)}"



        )



        return



 



    if text_lower.startswith("editsell"):



        parts = text.split()



        if len(parts) != 3:



            send("Usage: editsell TICKER PRICE")



            return



        ticker = normalize_ticker(parts[1])



        if not ticker:



            send("Invalid ticker")



            return



        try:



            new_price = float(parts[2])



        except ValueError:



            send("Invalid price")



            return



        if not is_finite_positive(new_price):



            send("Price must be positive and finite")



            return



 



        with db_tx() as conn:



            row = conn.execute(



                "SELECT * FROM trades WHERE ticker = ? ORDER BY exit_time DESC, created_at DESC LIMIT 1",



                (ticker,),



            ).fetchone()



            if row is None:



                mark_update_processed_tx(conn, update_id, "rejected_editsell_no_trade")



                send("Trade not found")



                return



            trade = row_to_trade(row)



            old_price = trade["exit_price"]



            shares = int(trade["shares"])



            cash_adjustment = (new_price - old_price) * shares



            cash = get_cash(conn)



            new_cash = cash + cash_adjustment



            if new_cash < 0:



                mark_update_processed_tx(conn, update_id, "rejected_editsell_negative_cash")



                send("Edit would make cash negative")



                return



            entry_price = float(trade["entry_price"])



            profit = (new_price - entry_price) * shares



            risk_per_share = trade.get("risk_per_share")



            r_multiple = None



            if risk_per_share is not None and float(risk_per_share) > 0:



                r_multiple = (new_price - entry_price) / float(risk_per_share)



            conn.execute(



                "UPDATE trades SET exit_price = ?, profit = ?, r_multiple = ? WHERE id = ?",



                (new_price, round(profit, 2), None if r_multiple is None else round(r_multiple, 4), trade["id"]),



            )



            set_cash_tx(conn, new_cash)



            mark_update_processed_tx(conn, update_id, "processed_editsell")



        refresh_portfolio()



        send(



            f"SELL UPDATED {ticker}\n"



            f"Trade ID: {trade['id']}\n"



            f"Old: {round(old_price, 2)}\n"



            f"New: {round(new_price, 2)}\n"



            f"Cash adjusted by: {format_money(cash_adjustment)}"



        )



        return



 



    trade_cmd = re.fullmatch(



        r"(?i)\s*(bought|sold)\s+([A-Z0-9.\-]{1,15})\s+(\d+)\s+(?:at|@)\s+([0-9]+(?:\.[0-9]+)?)\s*",



        text,



    )



    if not trade_cmd:



        # Preserve original behavior: unknown short/invalid commands are ignored, but alert on likely trade commands.



        if text_lower.startswith(("bought", "sold")):



            send("Invalid command format. Use: bought TICKER SHARES at PRICE or sold TICKER SHARES at PRICE")



        return



 



    action = trade_cmd.group(1).lower()



    ticker = normalize_ticker(trade_cmd.group(2))



    shares = int(trade_cmd.group(3))



    price = float(trade_cmd.group(4))



 



    if not ticker:



        send("Invalid ticker")



        return



    if shares <= 0 or not is_finite_positive(price):



        send("Shares and price must be positive")



        return



 



    if action == "bought":



        ok, msg = record_buy(ticker, shares, price, update_id=update_id)



        send(msg if ok else "❌ ERROR: " + msg)



        return



 



    if action == "sold":



        ok, msg = record_sell(ticker, shares, price, exit_reason="manual", update_id=update_id)



        send(msg if ok else "❌ ERROR: " + msg)



        return



 



# -----------------------------------------------------------------------------



# STRATEGY / SIGNAL ANALYSIS



# -----------------------------------------------------------------------------



 



def analyze(
    ticker: str,
    market: str,
    df: pd.DataFrame,
    market_details: Optional[Dict[str, Any]] = None,
) -> Optional[Tuple[str, float, int, float, int, Dict[str, Any]]]:
    """
    V2.8 VCP-only leader engine with winner-capture upgrade.

    Primary and only live setup: VCP / volatility-contraction breakout. Entry logic is kept from v2.7 research; exit logic is upgraded to let winners breathe.

    Kept intentionally disabled by default:
    - ordinary RS breakout sleeve,
    - weak bucket,
    - medium bucket,
    - broad pullbacks,
    - random reclaim trades.

    This is balanced rather than dead-conservative: the universe remains broad,
    but the setup family is restricted to the most robust VCP/contraction pattern.
    """
    try:
        if df is None or df.empty:
            print(f"[ANALYZE SKIP] {ticker} - no data")
            return None
    except Exception as exc:
        print(f"[ANALYZE ERROR] {ticker}: {exc}")
        return None

    if ticker not in STRONG:
        return None

    if ticker in MEDIUM and not V2_ALLOW_MEDIUM:
        return None

    if ticker in WEAK and not V2_ALLOW_WEAK:
        return None

    if len(df) < 220:
        return None

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    price = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])

    if price <= 0 or prev_close <= 0:
        return None

    rsi_val = rsi(close).iloc[-1]
    atr_val = atr(df).iloc[-1]
    atr50_val = atr(df, period=50).iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1]
    ma100 = close.rolling(100).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1]
    avg_vol = volume.rolling(20).mean().iloc[-1]

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd.iloc[-1] - macd_signal.iloc[-1]

    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_width = ((bb_mid + 2 * bb_std) - (bb_mid - 2 * bb_std)) / bb_mid
    bb_current = bb_width.iloc[-1]
    bb_lookback = bb_width.dropna().iloc[-100:]

    bb_width_rank = None
    if not pd.isna(bb_current) and len(bb_lookback) >= 50:
        bb_width_rank = float((bb_lookback <= bb_current).mean())

    required_vals = [
        rsi_val, atr_val, atr50_val, ma10, ma20, ma50, ma100, ma200,
        avg_vol, macd_hist
    ]

    if any(pd.isna(x) for x in required_vals):
        return None

    if ma20 <= 0 or ma50 <= 0 or ma100 <= 0 or ma200 <= 0 or avg_vol <= 0 or atr_val <= 0 or atr50_val <= 0:
        return None

    atr_val = float(atr_val)
    atr50_val = float(atr50_val)
    atr_pct = atr_val / price
    atr_ratio = atr_val / atr50_val if atr50_val > 0 else None
    avg_dollar_volume = float(avg_vol) * price
    daily_move_pct = ((price - prev_close) / prev_close) * 100
    volume_ratio = float(volume.iloc[-1]) / float(avg_vol)
    close_loc = close_location(df)

    if price < V2_MIN_PRICE:
        return None

    if avg_dollar_volume < V2_MIN_AVG_DOLLAR_VOLUME:
        return None

    if atr_pct < V2_MIN_ATR_PCT or atr_pct > V2_MAX_ATR_PCT:
        return None

    market_score = 0
    spy_df = None
    qqq_df = None
    smh_df = None

    if market_details:
        market_score = int(market_details.get("score", 0) or 0)
        frames = market_details.get("frames", {}) or {}
        spy_df = frames.get("SPY")
        qqq_df = frames.get("QQQ")
        smh_df = frames.get("SMH")

    if market == "BEAR" or market_score < V2_MIN_MARKET_SCORE:
        return None

    def frame_above_ma(frame: Optional[pd.DataFrame], period: int) -> bool:
        try:
            if frame is None or frame.empty or len(frame) < period:
                return False
            ma_val = frame["Close"].rolling(period).mean().iloc[-1]
            if pd.isna(ma_val):
                return False
            return float(frame["Close"].iloc[-1]) > float(ma_val)
        except Exception:
            return False

    if V2_REQUIRE_SPY_ABOVE_MA100 and not frame_above_ma(spy_df, 100):
        return None

    if V2_REQUIRE_SPY_ABOVE_MA200 and not frame_above_ma(spy_df, 200):
        return None

    if V2_REQUIRE_QQQ_ABOVE_MA100 and not frame_above_ma(qqq_df, 100):
        return None

    if V2_REQUIRE_QQQ_ABOVE_MA200 and not frame_above_ma(qqq_df, 200):
        return None

    # Relative strength vs SPY/QQQ/SMH.
    stock_ret_10 = pct_change_over(close, 10)
    stock_ret_20 = pct_change_over(close, 20)
    stock_ret_63 = pct_change_over(close, 63)
    spy_ret_20 = pct_change_over(spy_df["Close"], 20) if spy_df is not None and not spy_df.empty else None
    spy_ret_63 = pct_change_over(spy_df["Close"], 63) if spy_df is not None and not spy_df.empty else None
    qqq_ret_20 = pct_change_over(qqq_df["Close"], 20) if qqq_df is not None and not qqq_df.empty else None
    smh_ret_20 = pct_change_over(smh_df["Close"], 20) if smh_df is not None and not smh_df.empty else None

    rel_20 = None if stock_ret_20 is None or spy_ret_20 is None else stock_ret_20 - spy_ret_20
    rel_63 = None if stock_ret_63 is None or spy_ret_63 is None else stock_ret_63 - spy_ret_63
    rel_qqq_20 = None if stock_ret_20 is None or qqq_ret_20 is None else stock_ret_20 - qqq_ret_20
    rel_smh_20 = None if stock_ret_20 is None or smh_ret_20 is None else stock_ret_20 - smh_ret_20

    trend_score = 0

    if price > float(ma50):
        trend_score += 12

    if float(ma20) > float(ma50):
        trend_score += 10

    if price > float(ma20):
        trend_score += 6

    if price > float(ma10):
        trend_score += 4

    if price > float(ma100):
        trend_score += 6

    if price > float(ma200):
        trend_score += 8

    if float(ma50) > float(ma200):
        trend_score += 4

    if rolling_slope_positive(close.rolling(50).mean(), lookback=10):
        trend_score += 10

    if rolling_slope_positive(close.rolling(20).mean(), lookback=5):
        trend_score += 4

    if float(macd_hist) > 0:
        trend_score += 4

    if close_loc >= 0.55:
        trend_score += 5

    if avg_dollar_volume >= 100_000_000:
        trend_score += 5

    rs_score = 0

    if stock_ret_10 is not None and stock_ret_10 > 0:
        rs_score += 3

    if stock_ret_20 is not None and stock_ret_20 > 0:
        rs_score += 5

    if stock_ret_63 is not None and stock_ret_63 > 0:
        rs_score += 5

    if rel_20 is not None:
        if rel_20 > 0:
            rs_score += 8
        if rel_20 > 0.03:
            rs_score += 5
        if rel_20 > 0.08:
            rs_score += 4

    if rel_63 is not None:
        if rel_63 > 0:
            rs_score += 8
        if rel_63 > 0.06:
            rs_score += 5

    if rel_qqq_20 is not None and rel_qqq_20 > 0:
        rs_score += 4

    semi_leaders = {"NVDA", "AMD", "MU", "LRCX", "ASML", "QCOM", "AVGO", "SMH", "SOXX", "KLAC", "AMAT", "TSM", "TXN", "ADI", "MRVL", "MPWR", "ON", "NXPI", "ARM"}
    if rel_smh_20 is not None and rel_smh_20 > 0 and ticker in semi_leaders:
        rs_score += 3

    recent_high_20 = float(close.iloc[-21:-1].max())
    recent_high_55 = float(close.iloc[-56:-1].max())
    breakout_20 = price > recent_high_20
    breakout_55 = price > recent_high_55

    if not (breakout_20 or breakout_55):
        return None

    recent_high_20_window = float(high.iloc[-21:].max())
    recent_low_20_window = float(low.iloc[-21:].min())
    recent_range_20_pct = (recent_high_20_window - recent_low_20_window) / price

    breakout_level = recent_high_55 if breakout_55 else recent_high_20
    breakout_extension_atr = max(0.0, (price - breakout_level) / atr_val) if atr_val > 0 else 999.0
    extension_ma20 = (price - float(ma20)) / float(ma20)

    breakout_score = -999

    if V2_ALLOW_BREAKOUTS:
        breakout_score = trend_score + rs_score

        if breakout_20:
            breakout_score += 8

        if breakout_55:
            breakout_score += 12

        if volume_ratio >= V2_MIN_BREAKOUT_VOLUME_RATIO:
            breakout_score += 8

        if volume_ratio >= 1.5:
            breakout_score += 5

        if close_loc >= V2_BREAKOUT_MIN_CLOSE_LOCATION:
            breakout_score += 8

        if 50 <= float(rsi_val) <= V2_MAX_BREAKOUT_RSI:
            breakout_score += 6

        if 0 < daily_move_pct <= V2_MAX_BREAKOUT_DAY_MOVE_PCT:
            breakout_score += 5

        if float(ma20) > float(ma50):
            breakout_score += 5

        if bb_width_rank is not None and bb_width_rank <= 0.50:
            breakout_score += 3

        # Hard filters for the RS-breakout sleeve.
        if float(rsi_val) > V2_MAX_BREAKOUT_RSI:
            breakout_score = -999

        if daily_move_pct <= 0 or daily_move_pct > V2_MAX_BREAKOUT_DAY_MOVE_PCT:
            breakout_score = -999

        if volume_ratio < V2_MIN_BREAKOUT_VOLUME_RATIO:
            breakout_score = -999

        if close_loc < V2_BREAKOUT_MIN_CLOSE_LOCATION:
            breakout_score = -999

        if not (price > float(ma20) > float(ma50)):
            breakout_score = -999

        if V2_BREAKOUT_REQUIRE_MA20_SLOPE and not rolling_slope_positive(close.rolling(20).mean(), lookback=5):
            breakout_score = -999

        if stock_ret_20 is None or stock_ret_20 < V2_BREAKOUT_MIN_STOCK_RET_20:
            breakout_score = -999

        if stock_ret_63 is None or stock_ret_63 < V2_BREAKOUT_MIN_STOCK_RET_63:
            breakout_score = -999

        if rel_20 is None or rel_20 < V2_BREAKOUT_MIN_REL_20_SPY:
            breakout_score = -999

        if rel_63 is None or rel_63 < V2_BREAKOUT_MIN_REL_63_SPY:
            breakout_score = -999

        if rel_qqq_20 is None or rel_qqq_20 < V2_BREAKOUT_MIN_REL_20_QQQ:
            breakout_score = -999

        if V2_BREAKOUT_REQUIRE_55DAY_OR_STRONG_RS:
            strong_rs = rel_20 is not None and rel_20 >= V2_BREAKOUT_STRONG_RS_20_SPY
            if not (breakout_55 or strong_rs):
                breakout_score = -999

        if recent_range_20_pct > V2_BREAKOUT_MAX_BASE_RANGE_PCT:
            breakout_score = -999

        if extension_ma20 > V2_BREAKOUT_MAX_EXTENSION_MA20:
            breakout_score = -999

        if breakout_extension_atr > V2_BREAKOUT_MAX_EXTENSION_ATR:
            breakout_score = -999

        if rs_score < V2_BREAKOUT_MIN_RS_SCORE:
            breakout_score = -999

    vcp_score = -999

    if V2_ALLOW_VCP:
        vcp_candidate = (
            recent_range_20_pct <= V2_VCP_MAX_BASE_RANGE_PCT
            and atr_ratio is not None and atr_ratio <= V2_VCP_MAX_ATR_RATIO
            and bb_width_rank is not None and bb_width_rank <= V2_VCP_MAX_BB_WIDTH_RANK
            and volume_ratio >= V2_VCP_MIN_VOLUME_RATIO
            and close_loc >= V2_VCP_MIN_CLOSE_LOCATION
            and price > float(ma20) > float(ma50)
            and daily_move_pct > 0
            and daily_move_pct <= V2_MAX_BREAKOUT_DAY_MOVE_PCT
            and float(rsi_val) <= V2_MAX_BREAKOUT_RSI
        )

        if V2_VCP_REQUIRE_MA200 and price <= float(ma200):
            vcp_candidate = False

        if V2_BREAKOUT_REQUIRE_POSITIVE_RS and not (
            (rel_20 is not None and rel_20 >= V2_BREAKOUT_MIN_REL_20_SPY)
            or (rel_63 is not None and rel_63 >= V2_BREAKOUT_MIN_REL_63_SPY)
        ):
            vcp_candidate = False

        if vcp_candidate:
            vcp_score = trend_score + rs_score + 18

            if breakout_55:
                vcp_score += 10

            if recent_range_20_pct <= V2_VCP_MAX_BASE_RANGE_PCT * 0.75:
                vcp_score += 6

            if atr_ratio is not None and atr_ratio <= V2_VCP_MAX_ATR_RATIO * 0.85:
                vcp_score += 5

            if bb_width_rank is not None and bb_width_rank <= 0.30:
                vcp_score += 5

            if volume_ratio >= 1.5:
                vcp_score += 5

            if rel_20 is not None and rel_20 > 0:
                vcp_score += 5

    candidates = [
        ("vcp_breakout", int(round(vcp_score)), max(V2_MIN_SCORE, V2_VCP_MIN_SCORE), True, breakout_level),
        ("breakout", int(round(breakout_score)), max(V2_MIN_SCORE, V2_BREAKOUT_MIN_SCORE), True, breakout_level),
    ]

    setup_type, score, min_score, is_breakout, selected_breakout_level = max(candidates, key=lambda x: x[1])

    if market == "UNCERTAIN":
        min_score += 5

    if setup_type in {"breakout", "vcp_breakout"} and V2_BREAKOUT_REQUIRE_POSITIVE_RS:
        if not (
            (rel_20 is not None and rel_20 >= V2_BREAKOUT_MIN_REL_20_SPY)
            or (rel_63 is not None and rel_63 >= V2_BREAKOUT_MIN_REL_63_SPY)
        ):
            return None

    if score < min_score:
        return None

    atr_stop = price - (V2_BREAKOUT_ATR_STOP_MULT * atr_val)
    structure_stop = float(selected_breakout_level) - (0.35 * atr_val)
    stop = min(atr_stop, structure_stop) if V2_STOP_WIDER_OF_ATR_AND_STRUCTURE else max(atr_stop, structure_stop)
    stop_model = "v2_8_vcp_exit_upgrade_structure_atr" if V2_STOP_WIDER_OF_ATR_AND_STRUCTURE else "v2_8_vcp_exit_upgrade_tighter_atr"

    if stop <= 0 or stop >= price:
        stop = price - (V2_BREAKOUT_ATR_STOP_MULT * atr_val)
        stop_model = "fallback_v28_atr"

    risk = price - stop

    if risk <= 0:
        return None

    if (risk / price) > V2_MAX_RISK_PER_SHARE_PCT:
        return None

    risk_pct = risk_pct_for_ticker(ticker)

    if risk_pct is None:
        return None

    if V2_RISK_BOOST_ENABLED:
        if score >= 180:
            risk_pct *= V2_A_PLUS_RISK_BOOST
        elif score >= 172:
            risk_pct *= V2_A_RISK_BOOST

    refresh_portfolio()
    account_equity = approximate_equity_from_portfolio()
    available_cash = float(portfolio["cash"])

    shares_by_risk = int((account_equity * risk_pct) / risk)
    shares_by_position_cap = int((account_equity * MAX_POSITION_EQUITY_PCT) / price)
    shares_by_cash = int((available_cash * CASH_USAGE_BUFFER) / price)
    shares = min(shares_by_risk, shares_by_position_cap, shares_by_cash)

    if shares <= 0:
        return None

    rank_score = float(score)

    if setup_type == "vcp_breakout":
        rank_score += 6.0

    if breakout_55:
        rank_score += 8.0

    if rel_20 is not None:
        rank_score += max(0.0, min(10.0, rel_20 * 100.0))

    if rel_63 is not None:
        rank_score += max(0.0, min(8.0, rel_63 * 50.0))

    rank_score += max(0.0, min(8.0, (volume_ratio - 1.0) * 6.0))
    rank_score += max(0.0, min(10.0, (close_loc - 0.50) * 40.0))

    if bb_width_rank is not None and bb_width_rank <= 0.35:
        rank_score += 4.0

    if atr_ratio is not None and atr_ratio <= 0.85:
        rank_score += 3.0

    metrics = {
        "setup_type": setup_type,
        "strategy_family": "v2_8_vcp_exit_upgrade",
        "breakout": True,
        "breakout_20": breakout_20,
        "breakout_55": breakout_55,
        "breakout_level": selected_breakout_level,
        "breakout_extension_atr": breakout_extension_atr,
        "extension_ma20": extension_ma20,
        "recent_range_20_pct": recent_range_20_pct,
        "atr": atr_val,
        "atr_pct": atr_pct,
        "atr_ratio_14_50": atr_ratio,
        "bb_width_rank_100": bb_width_rank,
        "volume_ratio": volume_ratio,
        "daily_move_pct": daily_move_pct,
        "market_score": market_score,
        "trend_score": trend_score,
        "rs_score": rs_score,
        "rank_score": rank_score,
        "breakout_score": breakout_score if breakout_score != -999 else None,
        "vcp_score": vcp_score if vcp_score != -999 else None,
        "pullback_score": None,
        "min_score_required": min_score,
        "stock_ret_10": stock_ret_10,
        "stock_ret_20": stock_ret_20,
        "stock_ret_63": stock_ret_63,
        "rel_20_spy": rel_20,
        "rel_63_spy": rel_63,
        "rel_20_qqq": rel_qqq_20,
        "rel_20_smh": rel_smh_20,
        "ma20": float(ma20),
        "ma50": float(ma50),
        "ma100": float(ma100),
        "ma200": float(ma200),
        "close_location": close_loc,
        "avg_dollar_volume": avg_dollar_volume,
        "stop_model": stop_model,
        "risk_pct_used": risk_pct,
    }

    return ticker, price, shares, stop, score, metrics


# -----------------------------------------------------------------------------
# POSITION MANAGEMENT



# -----------------------------------------------------------------------------



 



def repair_position_if_needed(ticker: str, pos: Dict[str, Any]) -> Dict[str, Any]:



    changed = False



    entry = float(pos.get("price", 0) or 0)



    if entry <= 0:



        raise ValueError(f"Invalid entry price for {ticker}")



 



    if not is_finite_positive(float(pos.get("stop", 0) or 0)):



        pos["stop"] = entry * 0.95



        changed = True



    if not is_finite_positive(float(pos.get("highest", 0) or 0)):



        pos["highest"] = entry



        changed = True



    if not is_finite_positive(float(pos.get("initial_stop", 0) or 0)):



        pos["initial_stop"] = pos["stop"]



        changed = True



    if "partial_taken" not in pos:



        pos["partial_taken"] = False



        changed = True



    risk_per_share = pos.get("risk_per_share")



    if not isinstance(risk_per_share, (int, float)) or risk_per_share <= 0:



        fallback_risk = entry - float(pos["initial_stop"])



        if fallback_risk <= 0:



            fallback_risk = entry * 0.05



            pos["initial_stop"] = entry - fallback_risk



            if pos["stop"] >= entry:



                pos["stop"] = pos["initial_stop"]



        pos["risk_per_share"] = fallback_risk



        changed = True



        print(f"[POSITION REPAIRED RISK] {ticker} risk_per_share={fallback_risk}")



 



    if float(pos["highest"]) < entry:



        pos["highest"] = entry



        changed = True



 



    if changed:



        update_position_fields(ticker, pos)



    return pos



 



 



def position_exit_params(pos: Dict[str, Any]) -> Dict[str, Any]:
    """Per-position exit settings.

    Long VCP positions use v2.8 defaults. Bear inverse positions carry their own
    exit settings inside entry_data so both sleeves can coexist in one bot.
    """
    entry_data = pos.get("entry_data", {}) or {}

    return {
        "breakeven_r_trigger": float(entry_data.get("breakeven_r_trigger", BREAKEVEN_R_TRIGGER)),
        "partial_r": float(entry_data.get("partial_take_profit_r", PARTIAL_TAKE_PROFIT_R)),
        "partial_pct": float(entry_data.get("partial_take_profit_pct", PARTIAL_TAKE_PROFIT_PCT)),
        "partial_fraction": float(entry_data.get("partial_take_profit_fraction", PARTIAL_TAKE_PROFIT_FRACTION)),
        "trail_mult_early": float(entry_data.get("trail_mult_early", TRAIL_MULT_EARLY)),
        "trail_mult_late": float(entry_data.get("trail_mult_late", TRAIL_MULT_LATE)),
        "trail_tighten_pct": float(entry_data.get("trail_tighten_pct", TRAIL_TIGHTEN_PCT)),
        "time_stop_days": int(entry_data.get("time_stop_days", 0) or 0),
        "time_stop_min_r": float(entry_data.get("time_stop_min_r", 0.25)),
        "max_holding_days": int(entry_data.get("max_holding_days", 0) or 0),
    }


def manage_positions() -> None:



    refresh_portfolio()



    tickers = list(portfolio["positions"].keys())



    if not tickers:



        return



 



    prices = get_prices_batch(tickers)



 



    for ticker in tickers:



        refresh_portfolio()



        pos = portfolio["positions"].get(ticker)



        if pos is None:



            continue



 



        price = prices.get(ticker)



        if price is None:



            missing_price_counts[ticker] = missing_price_counts.get(ticker, 0) + 1



            if missing_price_counts[ticker] == PRICE_MISSING_ALERT_THRESHOLD:



                send(f"WARNING: Missing price for {ticker} {missing_price_counts[ticker]} times. Position not managed.")



            continue



        missing_price_counts[ticker] = 0



 



        try:



            pos = repair_position_if_needed(ticker, pos)



        except Exception as exc:



            print(f"[POSITION REPAIR ERROR] {ticker}: {exc}")



            send(f"CRITICAL: Position repair failed for {ticker}: {exc}")



            continue



 



        entry = float(pos["price"])



        old_highest = float(pos["highest"])



        new_highest = max(old_highest, price)



        if new_highest > old_highest:



            print(f"[📈 NEW HIGH] {ticker} -> {new_highest}")



            pos["highest"] = new_highest



 



        risk_per_share = pos.get("risk_per_share")



        trade_r: Optional[float] = None



        if isinstance(risk_per_share, (int, float)) and risk_per_share > 0:



            trade_r = (price - entry) / float(risk_per_share)



 



        print(



            f"[MANAGE] {ticker} | "



            f"price={price} | stop={round(float(pos['stop']), 2)} | "



            f"high={round(float(pos['highest']), 2)} | R={None if trade_r is None else round(trade_r, 2)}"



        )



 



        exit_params = position_exit_params(pos)

        # Compute effective stop first. This fixes the gap-through-trailing-stop bug.



        effective_stop = float(pos["stop"])



        atr_val = pos.get("atr")



        if isinstance(atr_val, (int, float)) and atr_val > 0:



            # Preserve original intent: tighter trail once price is up 5%.



            multiplier = exit_params["trail_mult_late"] if (price >= entry * (1 + exit_params["trail_tighten_pct"]) or (trade_r is not None and trade_r >= 1.5) or pos.get("partial_taken", False)) else exit_params["trail_mult_early"]



            theoretical_trail = float(pos["highest"]) - (multiplier * float(atr_val))



            if theoretical_trail > effective_stop:



                effective_stop = theoretical_trail



 



        # Breakeven rule, upgraded to R-based trigger from the prior version.



        if trade_r is not None and trade_r >= exit_params["breakeven_r_trigger"] and effective_stop < entry:



            effective_stop = entry



 



        # STOP / TRAILING STOP CHECK FIRST.



        if price <= effective_stop:



            fill_price = min(price, effective_stop)



            trade = record_auto_exit_or_partial(

                ticker=ticker,

                shares=int(pos["shares"]),

                price=fill_price,

                exit_reason="stop",

                updated_fields={

                    "highest": pos["highest"],

                    "stop": effective_stop

                },

            )



            if trade:

                exit_gain_pct = pct_from_entry(entry, fill_price)



                send(

                    f"📉 EXIT {ticker}\n"

                    f"🧬 Sleeve: {sleeve_short_label(pos.get('entry_data', {}) or {})}\n"

                    "📤 Action: exit your remaining position according to your own sizing\n"

                    f"💵 Exit price: {round(fill_price, 2)} ({format_pct(exit_gain_pct)})\n"

                    f"P/L: {format_money(trade['profit'])}\n"

                    f"R: {trade.get('r_multiple')}"

                )



                if should_forward_public_position(pos):

                    send_public_signal(

                        format_public_exit_signal(

                            ticker=ticker,

                            price=fill_price,

                            trade=trade,

                            reason="stop"

                        )

                    )



            continue



 



        # Save stop/high updates before checking partials.



        updates: Dict[str, Any] = {}



        if effective_stop > float(pos["stop"]):



            updates["stop"] = effective_stop



        if float(pos["highest"]) != old_highest:



            updates["highest"] = pos["highest"]



        if updates:



            update_position_fields(ticker, updates)



            pos.update(updates)



 



        # Time / regime exits. Used mainly by the bear inverse sleeve.
        holding_days = (now_ts() - float(pos.get("entry_time", now_ts()))) / 86400

        time_exit_reason: Optional[str] = None

        entry_data_for_exit = pos.get("entry_data", {}) or {}

        if entry_data_for_exit.get("strategy_sleeve") in {"BEAR_INVERSE", "BEAR_STOCK"}:
            try:
                bear_now = bear_regime_details(market_details=market_regime_details())
                if int(bear_now.get("score", 0) or 0) <= BEAR_EXIT_SCORE:
                    time_exit_reason = "bear_regime_exit"
            except Exception as exc:
                print(f"[BEAR REGIME EXIT CHECK ERROR] {ticker}: {exc}")

        if time_exit_reason is None and exit_params["max_holding_days"] > 0 and holding_days >= exit_params["max_holding_days"]:
            time_exit_reason = "max_hold"

        elif (
            time_exit_reason is None
            and exit_params["time_stop_days"] > 0
            and holding_days >= exit_params["time_stop_days"]
            and trade_r is not None
            and trade_r < exit_params["time_stop_min_r"]
        ):
            time_exit_reason = "time_stop"

        if time_exit_reason is not None:
            trade = record_auto_exit_or_partial(
                ticker=ticker,
                shares=int(pos["shares"]),
                price=price,
                exit_reason=time_exit_reason,
                updated_fields={"highest": pos["highest"], "stop": float(pos["stop"])}
            )

            if trade:
                exit_gain_pct = pct_from_entry(entry, price)
                send(
                    f"⏱️ EXIT {ticker}\n"
                    f"🧬 Sleeve: {sleeve_short_label(pos.get('entry_data', {}) or {})}\n"
                    f"Reason: {time_exit_reason}\n"
                    "📤 Action: exit your remaining position according to your own sizing\n"
                    f"💵 Exit price: {round(price, 2)} ({format_pct(exit_gain_pct)})\n"
                    f"P/L: {format_money(trade['profit'])}\n"
                    f"R: {trade.get('r_multiple')}"
                )

                if should_forward_public_position(pos):
                    send_public_signal(
                        format_public_exit_signal(
                            ticker=ticker,
                            price=price,
                            trade=trade,
                            reason=time_exit_reason
                        )
                    )

            continue

        # Partial take-profit logic: V2.8 defaults are +10% OR +1.25R, with 40% partial.



        partial_trigger = (price >= entry * (1 + exit_params["partial_pct"])) or (trade_r is not None and trade_r >= exit_params["partial_r"])



        if partial_trigger and not pos.get("partial_taken", False) and int(pos["shares"]) > 1:



            sell_shares = max(1, int(math.floor(int(pos["shares"]) * exit_params["partial_fraction"])))

            sell_shares = min(sell_shares, int(pos["shares"]) - 1)



            trade = record_auto_exit_or_partial(



                ticker=ticker,



                shares=sell_shares,



                price=price,



                exit_reason="partial",



                updated_fields={"highest": pos["highest"], "stop": pos["stop"]},



            )



            if trade:

                partial_gain_pct = pct_from_entry(entry, price)



                partial_reasons = []



                if price >= entry * (1 + exit_params["partial_pct"]):

                    partial_reasons.append(f"+{round(exit_params['partial_pct'] * 100, 2)}% target")



                if trade_r is not None and trade_r >= exit_params["partial_r"]:

                    partial_reasons.append(f"+{round(exit_params['partial_r'], 2)}R target")



                partial_reason_text = " + ".join(partial_reasons) if partial_reasons else "partial condition"



                remaining_shares = int(pos["shares"]) - sell_shares

                partial_fraction_label = int(round(float(exit_params.get("partial_fraction", PARTIAL_TAKE_PROFIT_FRACTION)) * 100))

                send(

                    f"💰 PARTIAL {ticker}\n"

                    f"🧬 Sleeve: {sleeve_short_label(pos.get('entry_data', {}) or {})}\n"

                    f"📤 SELL NOW: {sell_shares} shares (~{partial_fraction_label}% of position)\n"

                    f"📦 Remaining after partial: {remaining_shares} shares\n"

                    f"💵 Partial exit price: {round(price, 2)} ({format_pct(partial_gain_pct)})\n"

                    f"📌 Trigger: {partial_reason_text}\n"

                    f"P/L: {format_money(trade['profit'])}\n"

                    f"R: {trade.get('r_multiple')}"

                )



                if should_forward_public_position(pos):

                    send_public_signal(

                        format_public_partial_signal(

                            ticker=ticker,

                            price=price,

                            trade=trade

                        )

                    )



# -----------------------------------------------------------------------------



# SCANNING



# -----------------------------------------------------------------------------



 



def should_skip_for_existing_signal(

    ticker: str,

    expected_bar_date: Optional[str] = None

) -> bool:



    signal = last_signals.get(ticker)



    if not signal:

        return False



    try:

        if isinstance(signal, dict):

            signal_time = float(signal.get("time", 0))

            entry_data = signal.get("entry_data", {}) or {}

        else:

            signal_time = float(signal)

            entry_data = {}



        # Important for weekend/Monday behavior:

        # if the signal was already produced for the same daily candle, do not resend it.

        if expected_bar_date and entry_data.get("daily_bar_date") == expected_bar_date:

            return True



        return now_ts() - signal_time < SIGNAL_COOLDOWN_SEC



    except Exception:

        return False

 



def count_open_bear_positions() -> int:
    refresh_portfolio()
    count = 0
    for pos in portfolio["positions"].values():
        entry_data = pos.get("entry_data", {}) or {}
        if entry_data.get("strategy_sleeve") == "BEAR_INVERSE":
            count += 1
    return count


def analyze_bear_signal(
    ticker: str,
    df: pd.DataFrame,
    bear_details: Dict[str, Any],
) -> Optional[Tuple[str, float, int, float, int, Dict[str, Any]]]:
    """Robust 3x inverse ETF VCP bear sleeve.

    This is separate from the long v2.8 VCP strategy. It only runs when
    bear_regime_details() says the broad market is risk-off.
    """
    if not BEAR_SLEEVE_ENABLED:
        return None

    if ticker not in BEAR_WATCHLIST:
        return None

    if not bear_details.get("active"):
        return None

    if df is None or df.empty or len(df) < 220:
        return None

    close = df["Close"].dropna()
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    price = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])

    if price <= 0 or prev_close <= 0:
        return None

    rsi_val = rsi(close).iloc[-1]
    atr_val = atr(df).iloc[-1]
    atr50_val = atr(df, period=50).iloc[-1]
    ma10 = close.rolling(10).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1]
    avg_vol = volume.rolling(20).mean().iloc[-1]

    bb_mid = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_width = ((bb_mid + (2 * bb_std)) - (bb_mid - (2 * bb_std))) / bb_mid
    bb_width_rank = bb_width.rolling(100).rank(pct=True).iloc[-1]

    required = [rsi_val, atr_val, atr50_val, ma10, ma20, ma50, avg_vol, bb_width_rank]
    if any(pd.isna(x) for x in required):
        return None

    atr_pct = float(atr_val) / price
    atr_ratio = float(atr_val) / float(atr50_val) if float(atr50_val) > 0 else None
    avg_dollar_volume = float(avg_vol) * price
    volume_ratio = float(volume.iloc[-1]) / float(avg_vol) if float(avg_vol) > 0 else 0.0
    close_loc = close_location(df)
    daily_move_pct = ((price - prev_close) / prev_close) * 100

    if price < BEAR_MIN_PRICE:
        return None
    if avg_dollar_volume < BEAR_MIN_AVG_DOLLAR_VOLUME:
        return None
    if atr_pct < BEAR_MIN_ATR_PCT or atr_pct > BEAR_MAX_ATR_PCT:
        return None

    recent_high_20 = float(close.iloc[-21:-1].max())
    recent_high_55 = float(close.iloc[-56:-1].max())
    breakout_20 = price > recent_high_20
    breakout_55 = price > recent_high_55

    if not (breakout_20 or breakout_55):
        return None

    recent_high_20_window = float(high.iloc[-21:].max())
    recent_low_20_window = float(low.iloc[-21:].min())
    recent_range_20_pct = (recent_high_20_window - recent_low_20_window) / price
    breakout_level = recent_high_55 if breakout_55 else recent_high_20
    breakout_extension_atr = max(0.0, (price - breakout_level) / float(atr_val)) if float(atr_val) > 0 else 999.0

    vcp_candidate = (
        recent_range_20_pct <= BEAR_VCP_MAX_BASE_RANGE_PCT
        and atr_ratio is not None and atr_ratio <= BEAR_VCP_MAX_ATR_RATIO
        and float(bb_width_rank) <= BEAR_VCP_MAX_BB_WIDTH_RANK
        and volume_ratio >= BEAR_VCP_MIN_VOLUME_RATIO
        and close_loc >= BEAR_VCP_MIN_CLOSE_LOCATION
        and price > float(ma20) > float(ma50)
        and daily_move_pct > 0
        and daily_move_pct <= BEAR_MAX_DAY_MOVE_PCT
        and float(rsi_val) <= BEAR_MAX_RSI
    )

    if not vcp_candidate:
        return None

    score = 0

    if price > float(ma50):
        score += 12
    if float(ma20) > float(ma50):
        score += 10
    if price > float(ma20):
        score += 6
    if price > float(ma10):
        score += 4
    if rolling_slope_positive(close.rolling(20).mean(), lookback=5):
        score += 6
    if breakout_20:
        score += 8
    if breakout_55:
        score += 12
    if volume_ratio >= BEAR_VCP_MIN_VOLUME_RATIO:
        score += 8
    if volume_ratio >= 1.5:
        score += 5
    if close_loc >= BEAR_VCP_MIN_CLOSE_LOCATION:
        score += 8
    if recent_range_20_pct <= BEAR_VCP_MAX_BASE_RANGE_PCT * 0.75:
        score += 6
    if atr_ratio is not None and atr_ratio <= BEAR_VCP_MAX_ATR_RATIO * 0.90:
        score += 5
    if float(bb_width_rank) <= 0.45:
        score += 5
    score += int(bear_details.get("score", 0) or 0) // 4

    if score < BEAR_VCP_MIN_SCORE:
        return None

    atr_stop = price - (BEAR_ATR_STOP_MULT * float(atr_val))
    structure_stop = float(breakout_level) - (0.35 * float(atr_val))
    stop = min(atr_stop, structure_stop)

    if stop <= 0 or stop >= price:
        stop = price - (BEAR_ATR_STOP_MULT * float(atr_val))

    risk = price - stop

    if risk <= 0:
        return None

    if (risk / price) > BEAR_MAX_RISK_PER_SHARE_PCT:
        return None

    refresh_portfolio()
    account_equity = approximate_equity_from_portfolio()
    available_cash = float(portfolio["cash"])

    shares_by_risk = int((account_equity * BEAR_RISK_PCT) / risk)
    shares_by_position_cap = int((account_equity * MAX_POSITION_EQUITY_PCT) / price)
    shares_by_cash = int((available_cash * CASH_USAGE_BUFFER) / price)
    shares = min(shares_by_risk, shares_by_position_cap, shares_by_cash)

    if shares <= 0:
        return None

    rank_score = float(score)
    if breakout_55:
        rank_score += 8
    rank_score += max(0.0, min(8.0, (volume_ratio - 1.0) * 6.0))
    rank_score += max(0.0, min(10.0, (close_loc - 0.50) * 40.0))

    metrics = {
        "setup_type": "bear_vcp_inverse",
        "strategy_sleeve": "BEAR_INVERSE",
        "strategy_family": "bear_v1_robust_3x_vcp",
        "breakout": True,
        "breakout_20": breakout_20,
        "breakout_55": breakout_55,
        "breakout_level": breakout_level,
        "breakout_extension_atr": breakout_extension_atr,
        "recent_range_20_pct": recent_range_20_pct,
        "atr": float(atr_val),
        "atr_pct": atr_pct,
        "atr_ratio_14_50": atr_ratio,
        "bb_width_rank_100": float(bb_width_rank),
        "volume_ratio": volume_ratio,
        "daily_move_pct": daily_move_pct,
        "bear_score": int(bear_details.get("score", 0) or 0),
        "rank_score": rank_score,
        "min_score_required": BEAR_VCP_MIN_SCORE,
        "close_location": close_loc,
        "avg_dollar_volume": avg_dollar_volume,
        "risk_pct_used": BEAR_RISK_PCT,
        "stop_model": "bear_v1_robust_3x_vcp_structure_atr",
        "exit_params": {
            "breakeven_r_trigger": BEAR_BREAKEVEN_R_TRIGGER,
            "partial_take_profit_r": BEAR_PARTIAL_TAKE_PROFIT_R,
            "partial_take_profit_pct": BEAR_PARTIAL_TAKE_PROFIT_PCT,
            "partial_take_profit_fraction": BEAR_PARTIAL_TAKE_PROFIT_FRACTION,
            "trail_mult_early": BEAR_TRAIL_MULT_EARLY,
            "trail_mult_late": BEAR_TRAIL_MULT_LATE,
            "trail_tighten_pct": TRAIL_TIGHTEN_PCT,
            "time_stop_days": BEAR_TIME_STOP_DAYS,
            "time_stop_min_r": BEAR_TIME_STOP_MIN_R,
            "max_holding_days": BEAR_MAX_HOLDING_DAYS,
        },
    }

    return ticker, price, shares, stop, score, metrics


def scan_market() -> bool:
    global last_signals

    refresh_portfolio()
    last_signals = load_signals()

    usable_data_found = False
    attempted_historical = 0

    skip_counts = {
        "cooldown": 0,
        "existing_position": 0,
        "max_positions": 0,
        "low_cash": 0,
        "existing_signal": 0,
        "earnings_soon": 0,
        "earnings_unknown": 0,
        "no_historical": 0,
        "stale_data": 0,
        "strategy_filter": 0,
        "risk_cap": 0,
        "cash_reserve": 0,
        "candidates": 0,
        "signals_sent": 0,
        "bear_regime_block_long": 0,
        "bear_candidates": 0,
        "bear_signals_sent": 0,
    }

    today = ny_date_str()
    expected_bar = expected_daily_bar_date()

    stale = []

    for ticker, signal in last_signals.items():
        if isinstance(signal, dict):
            entry_data = signal.get("entry_data", {}) or {}
        else:
            entry_data = {}

        signal_day = entry_data.get("signal_date_ny")
        signal_bar_day = entry_data.get("daily_bar_date")

        if expected_bar:
            if signal_bar_day != expected_bar:
                stale.append(ticker)
        else:
            if signal_day != today:
                stale.append(ticker)

    if stale:
        with db_tx() as conn:
            for ticker in stale:
                conn.execute(
                    "DELETE FROM signals WHERE ticker = ?",
                    (ticker,)
                )

        last_signals = load_signals()

    heartbeat()

    market_details = market_regime_details()
    market = str(market_details.get("condition", "UNCERTAIN"))
    market_score = int(market_details.get("score", 0) or 0)

    market_emoji = "🟡"

    if market == "BULL":
        market_emoji = "🐂"
    elif market == "BEAR":
        market_emoji = "🐻"

    print(f"{market_emoji} MARKET | {market} | score={market_score}/8")

    bear_details = bear_regime_details(market_details=market_details)
    bear_active = bool(BEAR_SLEEVE_ENABLED and bear_details.get("active"))
    print(
        f"🐻 BEAR SLEEVE | active={yes_no(bear_active)} | "
        f"score={bear_details.get('score')}/{bear_details.get('max_score')} | "
        f"entry={BEAR_ENTRY_SCORE}"
    )

    cooldowns = get_cooldowns()
    base_risk_details = open_risk_details()

    reserved_signal_risk, reserved_signal_capital, reserved_signal_tickers = (
        outstanding_signal_reservations(expected_bar)
    )

    if reserved_signal_tickers:
        print(
            "[SIGNAL RESERVATION] "
            f"tickers={reserved_signal_tickers} | "
            f"risk={round(reserved_signal_risk, 2)} | "
            f"capital={round(reserved_signal_capital, 2)}"
        )

    if PANIC_MODE:
       print("[SCAN BLOCKED] PANIC_MODE")
       return True

    if daily_drawdown_exceeded():
       print("[🚨 SCAN BLOCKED] DAILY LOSS LIMIT")
       return True

    guard = portfolio_risk_guard_details()
    if guard.get("block_new_entries"):
       print("[🚨 SCAN BLOCKED] PORTFOLIO HARD DRAWDOWN GUARD")
       maybe_send_portfolio_risk_guard_alert(guard)
       return True

    if guard.get("soft_active"):
       maybe_send_portfolio_risk_guard_alert(guard)

    available_signal_slots = max(
        0,
        MAX_OPEN_POSITIONS - len(portfolio["positions"]) - len(reserved_signal_tickers)
    )

    if available_signal_slots <= 0:
        print("[SCAN COMPLETE] No open position slots available after signal reservations.")
        return True

    candidates: List[Dict[str, Any]] = []

    long_scan_universe = [] if (bear_active and BEAR_BLOCK_LONG_SIGNALS_IN_BEAR) else WATCHLIST

    if bear_active and BEAR_BLOCK_LONG_SIGNALS_IN_BEAR:
        skip_counts["bear_regime_block_long"] = len(WATCHLIST)
        print("[LONG SCAN BLOCKED] Bear sleeve is active; no new long VCP signals will be sent.")

    for ticker in long_scan_universe:
        try:
            refresh_portfolio()

            if ticker in cooldowns and now_ts() - cooldowns[ticker] < STOP_COOLDOWN_SEC:
                skip_counts["cooldown"] += 1
                continue

            if ticker in portfolio["positions"]:
                skip_counts["existing_position"] += 1
                continue

            if len(portfolio["positions"]) >= MAX_OPEN_POSITIONS:
                skip_counts["max_positions"] += 1
                continue

            if portfolio["cash"] < MIN_CASH_REQUIRED:
                skip_counts["low_cash"] += 1
                continue

            if should_skip_for_existing_signal(ticker, expected_bar):
                skip_counts["existing_signal"] += 1
                continue

            earnings = earnings_status(ticker, days=EARNINGS_LOOKAHEAD_DAYS)

            if earnings == "SOON":
                skip_counts["earnings_soon"] += 1
                print(f"[SKIP EARNINGS] {ticker}")
                continue

            if earnings == "UNKNOWN" and FAIL_CLOSED_ON_EARNINGS_UNKNOWN:
                skip_counts["earnings_unknown"] += 1
                print(f"[SKIP EARNINGS UNKNOWN] {ticker}")
                continue

            attempted_historical += 1

            df = get_signal_dataframe(ticker, limit=260)

            if df is None or df.empty or len(df) < 220:
                skip_counts["no_historical"] += 1
                continue

            if REQUIRE_FRESH_DAILY_CANDLE and not is_daily_data_current(df):
                skip_counts["stale_data"] += 1

                last_date = pd.to_datetime(df.iloc[-1]["date"]).date()
                current_ny = ny_now()

                print(
                    f"[🧊 STALE DATA SKIP] "
                    f"{ticker} | "
                    f"last={last_date} | "
                    f"today={current_ny.date()} | "
                    f"ny={current_ny.strftime('%H:%M')}"
                )

                continue

            usable_data_found = True

            close = df["Close"].dropna()

            if len(close) < 2:
                continue

            price = float(close.iloc[-1])
            prev_close = float(close.iloc[-2])

            if prev_close <= 0:
                continue

            move = ((price - prev_close) / prev_close) * 100
            levels = [10, 15, 20]
            existing_levels = get_breakout_levels(ticker)
            triggered_levels = [lvl for lvl in levels if move >= lvl]

            if triggered_levels:
                highest_level = max(triggered_levels)

                if highest_level not in existing_levels:
                    send(
                        f"🚀 BREAKOUT ALERT\n\n"
                        f"🏷️ Ticker: {ticker}\n"
                        f"📊 Move: +{round(move, 2)}%\n"
                        f"🎚️ Level: {highest_level}%+"
                    )

                    existing_levels.update(triggered_levels)
                    set_breakout_levels(ticker, existing_levels)

            elif move < 8:
                clear_breakout_levels(ticker)

            risk_details = base_risk_details

            if risk_details["equity"] <= 0:
                continue

            if (risk_details["initial_risk_dollars"] + reserved_signal_risk) / risk_details["equity"] >= MAX_TOTAL_RISK:
                skip_counts["risk_cap"] += 1
                continue

            result = analyze(ticker, market, df, market_details=market_details)

            if not result:
                skip_counts["strategy_filter"] += 1
                continue

            ticker, price, shares, stop, score, metrics = result

            risk_amount = (price - stop) * shares
            capital = shares * price
            equity_at_signal = float(risk_details["equity"])

            position_size_pct = (
                (capital / equity_at_signal) * 100
                if equity_at_signal > 0
                else 0.0
            )

            single_trade_risk_pct = (
                (risk_amount / equity_at_signal) * 100
                if equity_at_signal > 0
                else 0.0
            )

            max_valid_entry = min(
                price * (1 + MAX_ENTRY_EXTENSION_PCT),
                price + (0.35 * float(metrics.get("atr", 0) or 0))
            )

            entry_data = {
                "rsi": round(float(rsi(df["Close"]).iloc[-1]), 2),
                "score": int(score),
                "market": market,
                "market_score": metrics.get("market_score"),
                "atr": round(float(metrics.get("atr", 0)), 4),
                "atr_pct": round(float(metrics.get("atr_pct", 0)) * 100, 2),
                "stop": round(stop, 2),
                "breakout": bool(metrics.get("breakout")),
                "setup_type": metrics.get("setup_type"),
                "strategy_sleeve": "LONG_VCP",
                "strategy_family": "v2_8_vcp_long",
                "volume_ratio": None if metrics.get("volume_ratio") is None else round(float(metrics.get("volume_ratio")), 2),
                "daily_move_pct": round(float(metrics.get("daily_move_pct", 0)), 2),
                "trend_score": int(metrics.get("trend_score", 0) or 0),
                "rs_score": int(metrics.get("rs_score", 0) or 0),
                "min_score_required": int(metrics.get("min_score_required", 0) or 0),
                "rel_20_spy": None if metrics.get("rel_20_spy") is None else round(float(metrics.get("rel_20_spy")) * 100, 2),
                "rel_63_spy": None if metrics.get("rel_63_spy") is None else round(float(metrics.get("rel_63_spy")) * 100, 2),
                "close_location": round(float(metrics.get("close_location", 0)), 2),
                "rank_score": round(float(metrics.get("rank_score", score) or score), 2),
                "recent_range_20_pct": round(float(metrics.get("recent_range_20_pct", 0)) * 100, 2),
                "breakout_extension_atr": round(float(metrics.get("breakout_extension_atr", 0)), 2),
                "extension_ma20_pct": round(float(metrics.get("extension_ma20", 0)) * 100, 2),
                "avg_dollar_volume": round(float(metrics.get("avg_dollar_volume", 0)), 2),
                "stop_model": metrics.get("stop_model"),
                "strategy_version": STRATEGY_VERSION,
                "signal_time": now_ts(),
                "signal_date_ny": ny_date_str(),
                "daily_bar_date": pd.to_datetime(df.iloc[-1]["date"]).date().isoformat(),
                "signal_price": round(price, 2),
                "max_valid_entry": round(max_valid_entry, 2),
                "shares": int(shares),
                "capital": round(capital, 2),
                "equity_at_signal": round(equity_at_signal, 2),
                "position_size_pct": round(position_size_pct, 2),
                "single_trade_risk_pct": round(single_trade_risk_pct, 2),
                "risk_amount": round(risk_amount, 2),
            }

            candidates.append({
                "ticker": ticker,
                "price": price,
                "shares": shares,
                "stop": stop,
                "score": score,
                "rank_score": float(metrics.get("rank_score", score) or score),
                "risk_amount": risk_amount,
                "capital": capital,
                "entry_data": entry_data,
            })

            skip_counts["candidates"] += 1

        except Exception as exc:
            logger.exception(f"[❌ SCAN ERROR] {ticker}: {exc}")
            traceback.print_exc()
            send(f"WARNING: scan error for {ticker}: {exc}")

    # Bear inverse sleeve candidates. This runs only in confirmed risk-off regimes.
    if bear_active:
        open_bear_positions = count_open_bear_positions()
        bear_slots = max(0, BEAR_MAX_OPEN_POSITIONS - open_bear_positions)

        if bear_slots <= 0:
            print(f"[BEAR SCAN COMPLETE] No bear sleeve slots available: {open_bear_positions}/{BEAR_MAX_OPEN_POSITIONS}")

        else:
            for ticker in BEAR_WATCHLIST:
                try:
                    refresh_portfolio()

                    if ticker in cooldowns and now_ts() - cooldowns[ticker] < STOP_COOLDOWN_SEC:
                        skip_counts["cooldown"] += 1
                        continue

                    if ticker in portfolio["positions"]:
                        skip_counts["existing_position"] += 1
                        continue

                    if should_skip_for_existing_signal(ticker, expected_bar):
                        skip_counts["existing_signal"] += 1
                        continue

                    attempted_historical += 1
                    df = get_signal_dataframe(ticker, limit=260)

                    if df is None or df.empty or len(df) < 220:
                        skip_counts["no_historical"] += 1
                        continue

                    if REQUIRE_FRESH_DAILY_CANDLE and not is_daily_data_current(df):
                        skip_counts["stale_data"] += 1
                        continue

                    usable_data_found = True
                    result = analyze_bear_signal(ticker, df, bear_details=bear_details)

                    if not result:
                        skip_counts["strategy_filter"] += 1
                        continue

                    ticker, price, shares, stop, score, metrics = result
                    risk_amount = (price - stop) * shares
                    capital = shares * price
                    risk_details = base_risk_details
                    equity_at_signal = float(risk_details["equity"])

                    if equity_at_signal <= 0:
                        continue

                    position_size_pct = (capital / equity_at_signal) * 100
                    single_trade_risk_pct = (risk_amount / equity_at_signal) * 100
                    max_valid_entry = min(
                        price * (1 + MAX_ENTRY_EXTENSION_PCT),
                        price + (0.35 * float(metrics.get("atr", 0) or 0))
                    )

                    exit_params = metrics.get("exit_params", {}) or {}
                    entry_data = {
                        "rsi": round(float(rsi(df["Close"]).iloc[-1]), 2),
                        "score": int(score),
                        "market": "BEAR",
                        "market_score": market_score,
                        "bear_score": metrics.get("bear_score"),
                        "atr": round(float(metrics.get("atr", 0)), 4),
                        "atr_pct": round(float(metrics.get("atr_pct", 0)) * 100, 2),
                        "stop": round(stop, 2),
                        "breakout": bool(metrics.get("breakout", False)),
                        "setup_type": metrics.get("setup_type", "bear_stock_rs"),
                        "strategy_sleeve": metrics.get("strategy_sleeve", "BEAR_STOCK"),
                        "bear_stock_bucket": metrics.get("bear_stock_bucket") or metrics.get("bucket"),
                        "rel_21_spy": None if metrics.get("rel21_spy") is None else round(float(metrics.get("rel21_spy")) * 100, 2),
                        "rel_63_spy": None if metrics.get("rel63_spy") is None else round(float(metrics.get("rel63_spy")) * 100, 2),
                        "rel_21_qqq": None if metrics.get("rel21_qqq") is None else round(float(metrics.get("rel21_qqq")) * 100, 2),
                        "volume_ratio": None if metrics.get("volume_ratio") is None else round(float(metrics.get("volume_ratio")), 2),
                        "daily_move_pct": round(float(metrics.get("daily_move_pct", 0)), 2),
                        "min_score_required": int(metrics.get("min_score_required", 0) or 0),
                        "close_location": round(float(metrics.get("close_location", 0)), 2),
                        "rank_score": round(float(metrics.get("rank_score", score) or score), 2),
                        "recent_range_20_pct": round(float(metrics.get("recent_range_20_pct", 0)) * 100, 2),
                        "breakout_extension_atr": round(float(metrics.get("breakout_extension_atr", 0)), 2),
                        "avg_dollar_volume": round(float(metrics.get("avg_dollar_volume", 0)), 2),
                        "stop_model": metrics.get("stop_model"),
                        "strategy_version": BEAR_STRATEGY_VERSION,
                        "signal_time": now_ts(),
                        "signal_date_ny": ny_date_str(),
                        "daily_bar_date": pd.to_datetime(df.iloc[-1]["date"]).date().isoformat(),
                        "signal_price": round(price, 2),
                        "max_valid_entry": round(max_valid_entry, 2),
                        "shares": int(shares),
                        "capital": round(capital, 2),
                        "equity_at_signal": round(equity_at_signal, 2),
                        "position_size_pct": round(position_size_pct, 2),
                        "single_trade_risk_pct": round(single_trade_risk_pct, 2),
                        "risk_amount": round(risk_amount, 2),
                        "breakeven_r_trigger": exit_params.get("breakeven_r_trigger"),
                        "partial_take_profit_r": exit_params.get("partial_take_profit_r"),
                        "partial_take_profit_pct": exit_params.get("partial_take_profit_pct"),
                        "partial_take_profit_fraction": exit_params.get("partial_take_profit_fraction"),
                        "trail_mult_early": exit_params.get("trail_mult_early"),
                        "trail_mult_late": exit_params.get("trail_mult_late"),
                        "trail_tighten_pct": exit_params.get("trail_tighten_pct"),
                        "time_stop_days": exit_params.get("time_stop_days"),
                        "time_stop_min_r": exit_params.get("time_stop_min_r"),
                        "max_holding_days": exit_params.get("max_holding_days"),
                    }

                    candidates.append({
                        "ticker": ticker,
                        "price": price,
                        "shares": shares,
                        "stop": stop,
                        "score": score,
                        "rank_score": float(metrics.get("rank_score", score) or score),
                        "risk_amount": risk_amount,
                        "capital": capital,
                        "entry_data": entry_data,
                        "is_bear": True,
                    })

                    skip_counts["candidates"] += 1
                    skip_counts["bear_candidates"] += 1

                except Exception as exc:
                    logger.exception(f"[❌ BEAR SCAN ERROR] {ticker}: {exc}")
                    traceback.print_exc()
                    send(f"WARNING: bear scan error for {ticker}: {exc}")

    candidates = sorted(candidates, key=lambda x: float(x.get("rank_score", x.get("score", 0)) or 0), reverse=True)

    sent_count = 0
    bear_sent_count = 0

    for candidate in candidates:
        is_bear_candidate = bool(candidate.get("is_bear"))

        if is_bear_candidate:
            if bear_sent_count >= BEAR_MAX_SIGNALS_PER_SCAN:
                continue
            if bear_sent_count >= max(0, BEAR_MAX_OPEN_POSITIONS - count_open_bear_positions()):
                skip_counts["max_positions"] += 1
                continue
        else:
            if sent_count >= V2_MAX_SIGNALS_PER_SCAN:
                break

        total_signals_this_scan = sent_count + bear_sent_count

        if total_signals_this_scan >= available_signal_slots:
            skip_counts["max_positions"] += 1
            print(
                f"[SIGNAL SLOT LIMIT] sent={total_signals_this_scan} "
                f"available_slots={available_signal_slots}"
            )
            break

        ticker = candidate["ticker"]
        price = float(candidate["price"])
        shares = int(candidate["shares"])
        stop = float(candidate["stop"])
        score = int(candidate["score"])
        rank_score = float(candidate.get("rank_score", score) or score)
        risk_amount = float(candidate["risk_amount"])
        capital = float(candidate["capital"])
        entry_data = candidate["entry_data"]

        risk_details = base_risk_details
        equity_at_signal = float(risk_details["equity"])

        projected_risk_pct = (
            risk_details["initial_risk_dollars"] + reserved_signal_risk + risk_amount
        ) / equity_at_signal

        projected_capital = reserved_signal_capital + capital

        if projected_risk_pct > MAX_TOTAL_RISK:
            skip_counts["risk_cap"] += 1
            print(
                f"[SKIP MAX RISK] {ticker} projected={round(projected_risk_pct * 100, 2)}% "
                f"max={round(MAX_TOTAL_RISK * 100, 2)}%"
            )
            continue

        if projected_capital > portfolio["cash"] * CASH_USAGE_BUFFER:
            skip_counts["cash_reserve"] += 1
            print(
                f"[SKIP SIGNAL CASH RESERVE] {ticker} projected_capital={round(projected_capital, 2)} "
                f"cash={round(portfolio['cash'], 2)}"
            )
            continue

        entry_data["projected_total_risk_pct"] = round(projected_risk_pct * 100, 2)

        score_label = "🐻 Bear RS score" if is_bear_candidate else "⭐ V2.8 VCP score"
        extra_context = ""

        if is_bear_candidate:
            extra_context = (
                f"🐻 Bear regime score: {entry_data.get('bear_score')}/60\n"
                f"🧺 Bear stock bucket: {entry_data.get('bear_stock_bucket') or 'n/a'}\n"
                f"💪 RS vs SPY: 21d {entry_data.get('rel_21_spy')}% | 63d {entry_data.get('rel_63_spy')}%\n"
                f"💪 RS vs QQQ: 21d {entry_data.get('rel_21_qqq')}%\n"
                f"🚪 Primary exit: bear pressure <= {BEAR_EXIT_SCORE}\n"
            )
        else:
            extra_context = (
                f"💪 RS vs SPY: 20d {entry_data.get('rel_20_spy')}% | 63d {entry_data.get('rel_63_spy')}%\n"
                f"🧱 Trend score: {entry_data.get('trend_score')} | RS score: {entry_data.get('rs_score')}\n"
            )

        send(
            "📈 ENTRY SIGNAL\n\n"
            f"🏷️ Ticker: {ticker}\n"
            f"🌎 Market: {market_label(entry_data['market'])} ({entry_data.get('market_score')}/8)\n"
            f"🧬 Sleeve: {sleeve_label(entry_data)}\n"
            f"⚙️ Setup: {setup_label(entry_data['setup_type'])}\n\n"
            f"🟢 ENTRY: {round(price, 2)}\n"
            f"🟡 MAX ENTRY LIMIT: {entry_data['max_valid_entry']}\n"
            f"🔴 STOP/LOSS: {round(stop, 2)}\n"
            f"📐 Position size: {round(entry_data['position_size_pct'], 2)}% of equity\n\n"
            f"{score_label}: {score} / required {entry_data.get('min_score_required')}\n"
            f"📊 RSI: {round(float(entry_data['rsi']), 1)}\n"
            f"📊 Volume ratio: {entry_data.get('volume_ratio')}\n"
            f"🏅 Rank score: {round(rank_score, 2)}\n"
            f"{extra_context}\n"
            f"🛒 Bot buy size: {shares} shares\n"
            f"💰 Capital: {format_money(capital)}\n"
            f"⚠️ Trade risk: {format_money(risk_amount)} "
            f"({round(entry_data['single_trade_risk_pct'], 2)}% of equity)\n"
            f"📉 Projected total risk: {round(projected_risk_pct * 100, 2)}%"
            f"\n\n⚠️ Educational signal only. Use your own risk and account size."
        )

        public_ok, public_info = send_public_signal(
            format_public_entry_signal(ticker, entry_data)
        )

        entry_data["public_signal_sent"] = bool(public_ok)
        entry_data["public_signal_time"] = now_ts() if public_ok else None
        entry_data["public_signal_error"] = None if public_ok else str(public_info)

        save_signal(ticker, now_ts(), entry_data)

        skip_counts["signals_sent"] += 1
        if is_bear_candidate:
            skip_counts["bear_signals_sent"] += 1
            bear_sent_count += 1
        else:
            sent_count += 1
        reserved_signal_risk += risk_amount
        reserved_signal_capital += capital

    refresh_portfolio()

    print(
        "[SCAN SUMMARY] "
        f"usable_data_found={usable_data_found} | "
        f"attempted_historical={attempted_historical} | "
        f"positions={len(portfolio['positions'])}/{MAX_OPEN_POSITIONS} | "
        f"cash={round(portfolio['cash'], 2)} | "
        f"skips={skip_counts}"
    )

    if usable_data_found:
        return True

    if attempted_historical == 0:
        print("[SCAN COMPLETE] No eligible tickers reached historical-data check.")
        return True

    return False



# -----------------------------------------------------------------------------



# STARTUP / MAIN LOOP



# -----------------------------------------------------------------------------



 



def startup_checks() -> None:



    init_db()



    migrate_legacy_json_once()



    refresh_portfolio()



    global last_signals



    last_signals = load_signals()



 



    # Hard sanity checks. Do not silently continue with impossible state.



    if portfolio["cash"] < 0:



        raise RuntimeError("Invalid account state: negative cash")



    for ticker, pos in portfolio["positions"].items():



        if int(pos.get("shares", 0)) <= 0:



            raise RuntimeError(f"Invalid position state for {ticker}: non-positive shares")



        if not is_finite_positive(float(pos.get("price", 0))):



            raise RuntimeError(f"Invalid position state for {ticker}: invalid entry price")



    auto_initialize_withdrawal_hwm_if_needed()



 



def maybe_save_daily_equity_snapshot() -> None:



    today = ny_date_str()



    last = get_meta("last_equity_snapshot_date")



    if last == today:



        return



    try:



        save_equity_snapshot()



        print("[EQUITY SNAPSHOT SAVED]")



    except Exception as exc:



        print(f"[EQUITY SNAPSHOT ERROR] {exc}")



        send(f"WARNING: equity snapshot failed: {exc}")



 



 



def main() -> None:



    global LAST_HEARTBEAT, LAST_SCAN_ATTEMPT



    startup_checks()



    send(

        f"🚀 BOT STARTED\n"

        f"Strategy: {STRATEGY_VERSION}\n"

        f"Panic Mode: {PANIC_MODE}"

    )

    heartbeat()

    audit("BOT_STARTED")

    audit("MAIN_LOOP_STARTED")



    send(f"SERVER TIME: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}")



    send(f"NY TIME: {ny_now().strftime('%Y-%m-%d %H:%M:%S %Z')}")



 



    while True:



        try:



            LAST_HEARTBEAT = now_ts()

            heartbeat()



            current_ny = ny_now()



            maybe_save_daily_equity_snapshot()



            maybe_send_withdrawal_signal()

            maybe_send_wealth_core_signal()



            get_updates()

 



            if (not MANAGE_ONLY_REGULAR_HOURS) or is_regular_market_hours(current_ny):



                manage_positions()



 

            # Weekend handling

            if not is_market_weekday(current_ny):



                # Saturday-only scan

                if current_ny.weekday() == 5:



                    last_scan_day = get_meta("last_scan_day")

                    last_scan_bar = get_meta("last_scan_bar_date")

                    today = current_ny.date().isoformat()

                    expected_bar = expected_daily_bar_date()



                    if (

                        is_morning_scan_window(current_ny)

                        and last_scan_day != today

                        and (expected_bar is None or last_scan_bar != expected_bar)

                        and now_ts() - LAST_SCAN_ATTEMPT > 300

                    ):



                        LAST_SCAN_ATTEMPT = now_ts()



                        scanned_ok = scan_market()



                        if scanned_ok:

                            set_meta("last_scan_day", today)



                            if expected_bar:

                                set_meta("last_scan_bar_date", expected_bar)



                time.sleep(60)

                continue

 





            # Run once during the New York near-close scan window.



            last_scan_day = get_meta("last_scan_day")

            last_scan_bar = get_meta("last_scan_bar_date")

            today = current_ny.date().isoformat()

            expected_bar = expected_daily_bar_date()



            if (

                is_near_close_scan_window(current_ny)

                and last_scan_day != today

                and (expected_bar is None or last_scan_bar != expected_bar)

                and now_ts() - LAST_SCAN_ATTEMPT > 300

            ):



                LAST_SCAN_ATTEMPT = now_ts()



                scanned_ok = scan_market()



                if scanned_ok:

                    set_meta("last_scan_day", today)



                    if expected_bar:

                        set_meta("last_scan_bar_date", expected_bar)

                else:

                    print("[SCAN NOT MARKED DONE] Historical data was not usable; will retry.")



            time.sleep(25)



 



        except KeyboardInterrupt:



            send("BOT STOPPED BY KEYBOARD INTERRUPT")



            raise



        except Exception as exc:



            logger.exception(f"[MAIN LOOP ERROR] {exc}")



            traceback.print_exc()



            send(f"ERROR: {exc}")



            time.sleep(25)



 



 





# =============================================================================
# V3.7 EXTENSION: SPEC_ALPHA LIVE LEDGER + PUBLIC CORE/SPEC PLANS
# =============================================================================
# This section wraps v3.6 without rewriting the VCP/bear/core logic.
# It adds a separate medium/weak monthly momentum sleeve with its own ledger.

CORE_PUBLIC_SIGNAL_ENABLED = os.getenv("CORE_PUBLIC_SIGNAL_ENABLED", "1") != "0"

SPEC_ALPHA_ENABLED = os.getenv("SPEC_ALPHA_ENABLED", "1") != "0"
SPEC_ALPHA_LEDGER_ENABLED = os.getenv("SPEC_ALPHA_LEDGER_ENABLED", "1") != "0"
SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED = os.getenv("SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED", "1") != "0"
SPEC_ALPHA_ACCOUNT_ALLOC_PCT = float(os.getenv("SPEC_ALPHA_ACCOUNT_ALLOC_PCT", "0.40"))
SPEC_ALPHA_TOP_N = int(os.getenv("SPEC_ALPHA_TOP_N", "10"))
SPEC_ALPHA_SCORE_MODE = os.getenv("SPEC_ALPHA_SCORE_MODE", "mom63").strip().lower()
SPEC_ALPHA_MIN_PRICE = float(os.getenv("SPEC_ALPHA_MIN_PRICE", "5"))
SPEC_ALPHA_MIN_AVG_DOLLAR_VOLUME = float(os.getenv("SPEC_ALPHA_MIN_AVG_DOLLAR_VOLUME", "5000000"))
SPEC_ALPHA_REQUIRE_SPY_ABOVE_MA200 = os.getenv("SPEC_ALPHA_REQUIRE_SPY_ABOVE_MA200", "1") != "0"
SPEC_ALPHA_REQUIRE_STOCK_ABOVE_MA200 = os.getenv("SPEC_ALPHA_REQUIRE_STOCK_ABOVE_MA200", "1") != "0"
SPEC_ALPHA_MAX_PER_SECTOR = int(os.getenv("SPEC_ALPHA_MAX_PER_SECTOR", "2"))
SPEC_ALPHA_MAX_CRYPTO_NAMES = int(os.getenv("SPEC_ALPHA_MAX_CRYPTO_NAMES", "2"))
SPEC_ALPHA_MAX_SINGLE_ASSET_PCT = float(os.getenv("SPEC_ALPHA_MAX_SINGLE_ASSET_PCT", "0.18"))
SPEC_ALPHA_MIN_SINGLE_ASSET_PCT = float(os.getenv("SPEC_ALPHA_MIN_SINGLE_ASSET_PCT", "0.04"))
SPEC_ALPHA_REBALANCE_DRIFT_THRESHOLD_PCT = float(os.getenv("SPEC_ALPHA_REBALANCE_DRIFT_THRESHOLD_PCT", "0.01"))
SPEC_ALPHA_ACTION_DOLLAR_THRESHOLD = float(os.getenv("SPEC_ALPHA_ACTION_DOLLAR_THRESHOLD", "50"))
SPEC_ALPHA_MIN_TRADE_DOLLARS = float(os.getenv("SPEC_ALPHA_MIN_TRADE_DOLLARS", "25"))
SPEC_ALPHA_QUOTE_DEVIATION_LIMIT = float(os.getenv("SPEC_ALPHA_QUOTE_DEVIATION_LIMIT", "0.06"))
SPEC_ALPHA_REQUIRE_LIVE_QUOTE = os.getenv("SPEC_ALPHA_REQUIRE_LIVE_QUOTE", "1") != "0"
SPEC_ALPHA_REQUIRE_ACTIVE_PLAN_FOR_BUY = os.getenv("SPEC_ALPHA_REQUIRE_ACTIVE_PLAN_FOR_BUY", "1") != "0"
SPEC_ALPHA_ALLOW_FRACTIONAL_SHARES = os.getenv("SPEC_ALPHA_ALLOW_FRACTIONAL_SHARES", "1") != "0"
SPEC_ALPHA_PLAN_VALID_DAYS = int(os.getenv("SPEC_ALPHA_PLAN_VALID_DAYS", "10"))
SPEC_ALPHA_ALERT_REPEAT_DAYS = int(os.getenv("SPEC_ALPHA_ALERT_REPEAT_DAYS", "7"))
SPEC_ALPHA_REVIEW_AFTER_CLOSE_MINUTE = int(os.getenv("SPEC_ALPHA_REVIEW_AFTER_CLOSE_MINUTE", str(16 * 60 + 10)))
SPEC_ALPHA_SCORE_SLEEP_SEC = float(os.getenv("SPEC_ALPHA_SCORE_SLEEP_SEC", "0.02"))

SPEC_ALPHA_UNIVERSE = [
    "PBF",
    "OXY",
    "LNTH",
    "MOS",
    "ATI",
    "HQY",
    "AVAV",
    "CLF",
    "EQT",
    "CAR",
    "DRS",
    "STLD",
    "NUE",
    "SWX",
    "ALB",
    "HRB",
    "UTHR",
    "ATGE",
    "PPC",
    "UNM",
    "COGT",
    "LEGN",
    "OLLI",
    "LW",
    "FCN",
    "EBR",
    "RBA",
    "RYTM",
    "SYM",
    "ENPH",
    "CELH",
    "COMM",
    "FSLR",
    "SPXC",
    "ASTS",
    "GSAT",
    "XMTR",
    "AMBC",
    "AKRO",
    "WFRD",
    "CWAN",
    "DKS",
    "IMVT",
    "NTNX",
    "PR",
    "ZETA",
    "GAP",
    "AXON",
    "BURL",
    "FICO",
    "AAON",
    "HL",
    "ETNB",
    "CROX",
    "HAL",
    "STRL",
    "WMG",
    "SMMT",
    "TGTX",
    "W",
    "LTH",
    "COMP",
    "CENX",
    "MKTX",
    "ESAB",
    "PTGX",
    "OSCR",
    "IOT",
    "FTAI",
    "MIR",
    "PCOR",
    "COIN",
    "RIOT",
    "BBIO",
    "HUT",
    "MSTR",
    "DUOL",
    "DKNG",
    "ROKU",
    "RELY",
    "CMG",
    "MARA",
    "APLD",
    "IONQ",
    "POWL",
    "JOBY",
    "CUK",
    "XPO",
    "AVDL",
    "ACHR",
    "RIVN",
    "SOFI",
    "AUR",
    "VNO",
    "MOD",
    "UEC",
    "LBRT",
    "ESTC",
    "AFRM",
    "IBRX",
    "SRRK",
    "UUUU",
    "ALL",
    "CYTK",
    "KYMR",
    "IBP",
    "CFLT",
    "LUNR",
    "SYRE",
    "VST",
    "HIMS",
    "WSM",
    "PRM",
    "CDE",
    "AMRK",
    "VCTR",
    "AR",
    "CAVA",
    "INSM",
    "CRS",
    "WULF",
    "GME",
    "ALNY",
    "CHWY",
    "LUMN",
    "YOU",
    "MIRM",
    "PCVX",
    "UAL",
    "EAT",
    "RGTI",
    "CRDO",
    "PL",
    "RDDT",
    "GH",
    "BROS",
    "SSRM",
    "TPR",
    "MPT",
    "LQDA",
    "CORT",
    "MP",
    "HALO",
    "OKTA",
    "RGLD",
    "VRSN",
    "LOAR",
    "MRCY",
    "DG",
    "OKLO",
    "FL",
    "AGX",
    "NRG",
    "FIVE",
    "CORZ",
    "CIFR",
    "QS",
    "VSAT",
    "SATS",
    "HOUS",
    "ARWR",
    "BITF",
    "PACS",
    "SPHR",
    "VICR",
    "FOLD",
    "AA",
    "VIAV",
    "MRNA",
    "COHR",
    "WLK",
    "KGS",
    "VG",
    "APLS",
    "LYB",
    "DOCN",
    "DOW",
    "PTEN",
    "DAR",
    "IRDM",
    "TTMI",
    "RSI",
    "CRWV",
    "CRCL",
    "PYPL",
    "SNOW",
    "F",
    "TGT",
    "CCL",
    "AAL",
    "WDAY",
    "CHTR",
    "HUM",
    "DAL",
    "MDB",
    "DVN",
    "FISV",
    "ZTS",
    "AZO",
    "ZS",
    "KVUE",
    "TTD",
    "DHI",
    "ADSK",
    "EL",
    "URI",
    "BDX",
    "TEM",
    "LNG",
    "ULTA",
    "DLTR",
    "FANG",
    "DXCM",
    "EA",
    "PINS",
    "EXPE",
    "HUBS",
    "EW",
    "KR",
    "U",
    "EBAY",
    "CCI",
    "STZ",
    "KHC",
    "KDP",
    "O",
    "COR",
    "EXC",
    "LEN",
    "AIG",
    "ODFL",
    "NCLH",
    "HSY",
    "GIS",
    "PCG",
    "ROP",
    "ETSY",
    "CARR",
    "TTWO",
    "MET",
    "CNC",
    "TSCO",
    "LUV",
    "CTSH",
    "KMB",
    "D",
    "MSCI",
    "LHX",
    "IDXX",
    "IQV",
    "HPQ",
    "ZM",
    "LVS",
    "TWLO",
    "PSA",
    "ILMN",
    "DPZ",
    "PAYX",
    "HBAN",
    "YUM",
    "XEL",
    "HPE",
    "BIIB",
    "ROK",
    "DECK",
    "PCAR",
    "AJG",
    "GEHC",
    "A",
    "CAH",
    "OKE",
    "BBY",
    "VRSK",
    "GPN",
    "ADM",
    "KEY",
    "VEEV",
    "LYV",
    "FAST",
    "SYY",
    "CF",
    "EFX",
    "BLDR",
    "ALGN",
    "CTVA",
    "CPRT",
    "LYFT",
    "AMP",
    "MLM",
    "VICI",
    "CFG",
    "SYF",
    "APA",
    "PPG",
    "WYNN",
    "DOCU",
    "DRI",
    "SBAC",
    "AFL",
    "OTIS",
    "CLX",
    "FITB",
    "DD",
    "STT",
    "PEG",
    "IR",
    "PHM",
    "EXE",
    "MTB",
    "RF",
    "VMC",
    "ED",
    "CSGP",
    "IT",
    "RMD",
    "LPLA",
    "ETR",
    "NDAQ",
    "PRU",
    "AME",
    "CDW",
    "TRGP",
    "GLXY",
    "MGM",
    "CBRE",
    "ZBH",
    "WEC",
    "BAX",
    "KEYS",
    "TROW",
    "RBRK",
    "TOST",
    "NTAP",
    "PSKY",
    "Z",
    "PODD",
    "MOH",
    "IP",
    "MTCH",
    "TLN",
    "WST",
    "LH",
    "EXR",
    "PPL",
    "TSN",
    "OMC",
    "GDDY",
    "GNRC",
    "CZR",
    "HIG",
    "CHD",
    "AWK",
    "HUBB",
    "CART",
    "AKAM",
    "CAG",
    "DOV",
    "FTV",
    "FE",
    "AGNC",
    "JBL",
    "AVB",
    "XYL",
    "DTE",
    "CNP",
    "EIX",
    "M",
    "JBHT",
    "WAT",
    "SWK",
    "KMX",
    "ES",
    "POOL",
    "IFF",
    "OVV",
    "CMS",
    "EXPD",
    "VLTO",
    "GPC",
    "RL",
    "RJF",
    "EPAM",
    "WAB",
    "TOL",
    "FND",
    "P",
    "SJM",
    "BG",
    "SAIA",
    "ARES",
    "DT",
    "ALLY",
    "NI",
    "MKC",
    "CPAY",
    "BJ",
    "AES",
    "AVTR",
    "HST",
    "AEE",
    "PATH",
    "TRU",
    "VFC",
    "ZBRA",
    "THC",
    "NLY",
    "CHRW",
    "CPB",
    "CRL",
    "LII",
    "ARE",
    "FOXA",
    "EQR",
    "WY",
    "NXT",
    "PTC",
    "FDS",
    "BRO",
    "MAS",
    "TXRH",
    "BALL",
    "CE",
    "COO",
    "INCY",
    "SN",
    "INVH",
    "BAH",
    "PKG",
    "ATO",
    "IRM",
    "TYL",
    "BMRN",
    "EVRG",
    "LDOS",
    "MAA",
    "OC",
    "NTRA",
    "UHS",
    "GTLB",
    "KNX",
    "DOC",
    "WSO",
    "FHN",
    "CG",
    "CCK",
    "TAP",
    "DINO",
    "PFG",
    "HAS",
    "RVTY",
    "CPT",
    "RRC",
    "S",
    "FFIV",
    "ALK",
    "BR",
    "IEX",
    "VTRS",
    "NBIX",
    "AVY",
    "BXP",
    "SUI",
    "EQH",
    "WRB",
    "GEN",
    "UDR",
    "WCC",
    "EMN",
    "TXT",
    "RGEN",
    "MTN",
    "JKHY",
    "TKO",
    "KIM",
    "LNT",
    "MASI",
    "USFD",
    "GTLS",
    "H",
    "KRMN",
    "J",
    "SGI",
    "TW",
    "DVA",
    "HRL",
    "TECH",
    "SIRI",
    "PEN",
    "BWA",
    "SFM",
    "BEN",
    "FBIN",
    "CRBG",
    "BLD",
    "ZION",
    "GWRE",
    "AMH",
    "SSNC",
    "DBX",
    "FWONK",
    "HSIC",
    "MKSI",
    "CASY",
    "RPRX",
    "TFX",
    "MTZ",
    "REXR",
    "TRMB",
    "IVZ",
    "ACI",
    "MANH",
    "OWL",
    "ARMK",
    "LKQ",
    "SOLV",
    "AGCO",
    "ELS",
    "MHK",
    "PNW",
    "PCTY",
    "MTDR",
    "CINF",
    "ELAN",
    "CHRD",
    "WAL",
    "MIDD",
    "CHYM",
    "HEI",
    "TTAN",
    "AOS",
    "WPC",
    "ROL",
    "FLG",
    "ARCC",
    "BOOT",
    "RRX",
    "PFGC",
    "WMS",
    "CNM",
    "ACM",
    "LEA",
    "EWBC",
    "CUBE",
    "FLR",
    "REG",
    "GL",
    "WSC",
    "OHI",
    "GLPI",
    "FNF",
    "SCI",
    "WH",
    "MPLX",
    "AXTA",
    "NWSA",
    "LNC",
    "NOV",
    "BRKR",
    "URBN",
    "RPM",
    "MUR",
    "LPX",
    "GMED",
    "VOYA",
    "CNX",
    "FRT",
    "WEX",
    "RAL",
    "JEF",
    "CLH",
    "BYD",
    "ADC",
    "OSK",
    "WWD",
    "SNX",
    "DOX",
    "TTC",
    "NYT",
    "ARW",
    "WBS",
    "GGG",
    "EXEL",
    "NDSN",
    "LECO",
    "L",
    "AXSM",
    "BRX",
    "WTRG",
    "BWXT",
    "EHC",
    "HR",
    "TTEK",
    "PAA",
    "NE",
    "SARO",
    "ST",
    "UGI",
    "CHH",
    "IONS",
    "CHDN",
    "AHR",
    "BC",
    "SITE",
    "NNN",
    "FR",
    "STWD",
    "SF",
    "BSY",
    "ITT",
    "OMF",
    "LINE",
    "LAMR",
    "CGNX",
    "FLS",
    "LSTR",
    "GXO",
    "MGY",
    "GKOS",
    "CMC",
    "OGE",
    "AMTM",
    "PSN",
    "HXL",
    "HLI",
    "VLY",
    "ALSN",
    "TMHC",
    "DY",
    "JXN",
    "DTM",
    "PNFP",
    "BPOP",
    "INGR",
    "OZK",
    "ORI",
    "RHP",
    "RITM",
    "STAG",
    "SSB",
    "POR",
    "R",
    "ULS",
    "RVMD",
    "WTFC",
    "GNTX",
    "COLB",
    "TKR",
    "RKT",
    "FOX",
    "PB",
    "KEX",
    "MTG",
    "MORN",
    "ONB",
    "ECG",
    "MSM",
    "ATR",
    "COKE",
    "FAF",
    "SEIC",
    "APPF",
    "SAIL",
    "EXLS",
    "APG",
    "AM",
    "ORA",
    "LEVI",
    "BTSG",
    "MC",
    "TRNO",
    "KRG",
    "CRC",
    "NFG",
    "WES",
    "ENSG",
    "SBRA",
    "TXNM",
    "CR",
    "FNB",
    "TPG",
    "PEGA",
    "SON",
    "RDN",
    "ESI",
    "PTCT",
    "UMBF",
    "EPRT",
    "DLB",
    "AVT",
    "CBSH",
    "ZWS",
    "PAGP",
    "ASB",
    "OBDC",
    "IDA",
    "ZG",
    "DCI",
    "UFPI",
    "MDU",
    "CTRE",
    "OGS",
    "SIGI",
    "ADT",
    "HLNE",
    "OUT",
    "BKH",
    "VIRT",
    "VNOM",
    "LFUS",
    "HASI",
    "GTES",
    "CWST",
    "MAC",
    "FROG",
    "EPR",
    "HESM",
    "HWC",
    "JBTM",
    "LB",
    "BEPC",
    "SANM",
    "RLI",
    "CGON",
    "NOVT",
    "NUVL",
    "PECO",
    "SR",
    "ENS",
    "CWEN",
    "GBCI",
    "GVA",
    "HOMB",
    "NJR",
    "UBSI",
    "AEIS",
    "APGE",
    "PRMB",
    "AROC",
    "KTOS",
    "PIPR",
    "SFD",
    "BIPC",
    "NWS",
    "PRIM",
    "ACA",
    "MAIN",
    "SNDR",
    "BGC",
    "SUN",
    "FFIN",
    "ROAD",
    "STEP",
    "AUB",
    "EBC",
    "OTF",
    "RUSHA",
    "REYN",
    "IEP",
    "LAUR",
    "LGND",
    "CNA",
    "SNEX",
    "INGM",
    "FI",
    "HOLX",
    "EXAS",
    "IPG",
    "CMA",
    "CYBR",
    "DAY",
    "CIVI",
    "FYBR",
    "CADE",
    "COOP",
    "AL",
    "ERJ",
    "ACLX",
    "ASGN",
    "CCCS",
    "INFA",
    "GMS",
    "DVAX",
    "ALE",
    "AVDX",
    "HI",
    "CSGS",
    "AXL",
    "ATUS",
    "IBDQ",
    "BRFS",
    "EB",
    "BASE",
    "DOOO",
    "ALEX"
]
SPEC_ALPHA_SECTOR_MAP = {
    "A": "Healthcare",
    "AA": "Basic Materials",
    "AAL": "Industrials",
    "AAON": "Industrials",
    "ACA": "Industrials",
    "ACHR": "Industrials",
    "ACI": "Consumer Defensive",
    "ACLX": "Healthcare",
    "ACM": "Industrials",
    "ADC": "Real Estate",
    "ADM": "Consumer Defensive",
    "ADSK": "Technology",
    "ADT": "Industrials",
    "AEE": "Utilities",
    "AEIS": "Industrials",
    "AES": "Utilities",
    "AFL": "Financial Services",
    "AFRM": "Technology",
    "AGCO": "Industrials",
    "AGNC": "Real Estate",
    "AGX": "Industrials",
    "AHR": "Real Estate",
    "AIG": "Financial Services",
    "AJG": "Financial Services",
    "AKAM": "Technology",
    "AKRO": "Healthcare",
    "AL": "Industrials",
    "ALB": "Basic Materials",
    "ALE": "Utilities",
    "ALEX": "Real Estate",
    "ALGN": "Healthcare",
    "ALK": "Industrials",
    "ALL": "Financial Services",
    "ALLY": "Financial Services",
    "ALNY": "Healthcare",
    "ALSN": "Consumer Cyclical",
    "AM": "Energy",
    "AMBC": "Financial Services",
    "AME": "Industrials",
    "AMH": "Real Estate",
    "AMP": "Financial Services",
    "AMRK": "Financial Services",
    "AMTM": "Industrials",
    "AOS": "Industrials",
    "APA": "Energy",
    "APG": "Industrials",
    "APGE": "Healthcare",
    "APLD": "Technology",
    "APLS": "Healthcare",
    "APPF": "Technology",
    "AR": "Energy",
    "ARCC": "Financial Services",
    "ARE": "Real Estate",
    "ARES": "Financial Services",
    "ARMK": "Industrials",
    "AROC": "Energy",
    "ARW": "Technology",
    "ARWR": "Healthcare",
    "ASB": "Financial Services",
    "ASGN": "Technology",
    "ASTS": "Technology",
    "ATGE": "Consumer Defensive",
    "ATI": "Industrials",
    "ATO": "Utilities",
    "ATR": "Healthcare",
    "ATUS": "Communication Services",
    "AUB": "Financial Services",
    "AUR": "Technology",
    "AVAV": "Industrials",
    "AVB": "Real Estate",
    "AVDL": "Healthcare",
    "AVDX": "Technology",
    "AVT": "Technology",
    "AVTR": "Healthcare",
    "AVY": "Consumer Cyclical",
    "AWK": "Utilities",
    "AXL": "Consumer Cyclical",
    "AXON": "Industrials",
    "AXSM": "Healthcare",
    "AXTA": "Basic Materials",
    "AZO": "Consumer Cyclical",
    "BAH": "Industrials",
    "BALL": "Consumer Cyclical",
    "BASE": "Technology",
    "BAX": "Healthcare",
    "BBIO": "Healthcare",
    "BBY": "Consumer Cyclical",
    "BC": "Consumer Cyclical",
    "BDX": "Healthcare",
    "BEN": "Financial Services",
    "BEPC": "Utilities",
    "BG": "Consumer Defensive",
    "BGC": "Financial Services",
    "BIIB": "Healthcare",
    "BIPC": "Utilities",
    "BITF": "Financial Services",
    "BJ": "Consumer Defensive",
    "BKH": "Utilities",
    "BLD": "Industrials",
    "BLDR": "Industrials",
    "BMRN": "Healthcare",
    "BOOT": "Consumer Cyclical",
    "BPOP": "Financial Services",
    "BR": "Technology",
    "BRFS": "Consumer Defensive",
    "BRKR": "Healthcare",
    "BRO": "Financial Services",
    "BROS": "Consumer Cyclical",
    "BRX": "Real Estate",
    "BSY": "Technology",
    "BTSG": "Healthcare",
    "BURL": "Consumer Cyclical",
    "BWA": "Consumer Cyclical",
    "BWXT": "Industrials",
    "BXP": "Real Estate",
    "BYD": "Consumer Cyclical",
    "CADE": "Financial Services",
    "CAG": "Consumer Defensive",
    "CAH": "Healthcare",
    "CAR": "Industrials",
    "CARR": "Industrials",
    "CART": "Consumer Cyclical",
    "CASY": "Consumer Cyclical",
    "CAVA": "Consumer Cyclical",
    "CBRE": "Real Estate",
    "CBSH": "Financial Services",
    "CCCS": "Technology",
    "CCI": "Real Estate",
    "CCK": "Consumer Cyclical",
    "CCL": "Consumer Cyclical",
    "CDE": "Basic Materials",
    "CDW": "Technology",
    "CE": "Basic Materials",
    "CELH": "Consumer Defensive",
    "CENX": "Basic Materials",
    "CF": "Basic Materials",
    "CFG": "Financial Services",
    "CFLT": "Technology",
    "CG": "Financial Services",
    "CGNX": "Technology",
    "CGON": "Healthcare",
    "CHD": "Consumer Defensive",
    "CHDN": "Consumer Cyclical",
    "CHH": "Consumer Cyclical",
    "CHRD": "Energy",
    "CHRW": "Industrials",
    "CHTR": "Communication Services",
    "CHWY": "Consumer Cyclical",
    "CHYM": "Financial Services",
    "CIFR": "Financial Services",
    "CINF": "Financial Services",
    "CIVI": "Energy",
    "CLF": "Basic Materials",
    "CLH": "Industrials",
    "CLX": "Consumer Defensive",
    "CMA": "Financial Services",
    "CMC": "Basic Materials",
    "CMG": "Consumer Cyclical",
    "CMS": "Utilities",
    "CNA": "Financial Services",
    "CNC": "Healthcare",
    "CNM": "Industrials",
    "CNP": "Utilities",
    "CNX": "Energy",
    "COGT": "Healthcare",
    "COHR": "Technology",
    "COIN": "Financial Services",
    "COKE": "Consumer Defensive",
    "COLB": "Financial Services",
    "COMM": "Technology",
    "COMP": "Technology",
    "COO": "Healthcare",
    "COOP": "Financial Services",
    "COR": "Healthcare",
    "CORT": "Healthcare",
    "CORZ": "Technology",
    "CPAY": "Technology",
    "CPB": "Consumer Defensive",
    "CPRT": "Industrials",
    "CPT": "Real Estate",
    "CR": "Industrials",
    "CRBG": "Financial Services",
    "CRC": "Energy",
    "CRCL": "Financial Services",
    "CRDO": "Technology",
    "CRL": "Healthcare",
    "CROX": "Consumer Cyclical",
    "CRS": "Industrials",
    "CRWV": "Technology",
    "CSGP": "Real Estate",
    "CSGS": "Technology",
    "CTRE": "Real Estate",
    "CTSH": "Technology",
    "CTVA": "Basic Materials",
    "CUBE": "Real Estate",
    "CUK": "Consumer Cyclical",
    "CWAN": "Technology",
    "CWEN": "Utilities",
    "CWST": "Industrials",
    "CYBR": "Technology",
    "CYTK": "Healthcare",
    "CZR": "Consumer Cyclical",
    "D": "Utilities",
    "DAL": "Industrials",
    "DAR": "Consumer Defensive",
    "DAY": "Technology",
    "DBX": "Technology",
    "DCI": "Industrials",
    "DD": "Basic Materials",
    "DECK": "Consumer Cyclical",
    "DG": "Consumer Defensive",
    "DHI": "Consumer Cyclical",
    "DINO": "Energy",
    "DKNG": "Consumer Cyclical",
    "DKS": "Consumer Cyclical",
    "DLB": "Technology",
    "DLTR": "Consumer Defensive",
    "DOC": "Real Estate",
    "DOCN": "Technology",
    "DOCU": "Technology",
    "DOOO": "Consumer Cyclical",
    "DOV": "Industrials",
    "DOW": "Basic Materials",
    "DOX": "Technology",
    "DPZ": "Consumer Cyclical",
    "DRI": "Consumer Cyclical",
    "DRS": "Industrials",
    "DT": "Technology",
    "DTE": "Utilities",
    "DTM": "Energy",
    "DUOL": "Technology",
    "DVA": "Healthcare",
    "DVAX": "Healthcare",
    "DVN": "Energy",
    "DXCM": "Healthcare",
    "DY": "Industrials",
    "EA": "Communication Services",
    "EAT": "Consumer Cyclical",
    "EB": "Technology",
    "EBAY": "Consumer Cyclical",
    "EBC": "Financial Services",
    "EBR": "Utilities",
    "ECG": "Industrials",
    "ED": "Utilities",
    "EFX": "Industrials",
    "EHC": "Healthcare",
    "EIX": "Utilities",
    "EL": "Consumer Defensive",
    "ELAN": "Healthcare",
    "ELS": "Real Estate",
    "EMN": "Basic Materials",
    "ENPH": "Energy",
    "ENS": "Industrials",
    "ENSG": "Healthcare",
    "EPAM": "Technology",
    "EPR": "Real Estate",
    "EPRT": "Real Estate",
    "EQH": "Financial Services",
    "EQR": "Real Estate",
    "EQT": "Energy",
    "ERJ": "Industrials",
    "ES": "Utilities",
    "ESAB": "Industrials",
    "ESI": "Basic Materials",
    "ESTC": "Technology",
    "ETNB": "Healthcare",
    "ETR": "Utilities",
    "ETSY": "Consumer Cyclical",
    "EVRG": "Utilities",
    "EW": "Healthcare",
    "EWBC": "Financial Services",
    "EXAS": "Healthcare",
    "EXC": "Utilities",
    "EXE": "Energy",
    "EXEL": "Healthcare",
    "EXLS": "Technology",
    "EXPD": "Industrials",
    "EXPE": "Consumer Cyclical",
    "EXR": "Real Estate",
    "F": "Consumer Cyclical",
    "FAF": "Financial Services",
    "FANG": "Energy",
    "FAST": "Industrials",
    "FBIN": "Industrials",
    "FCN": "Industrials",
    "FDS": "Financial Services",
    "FE": "Utilities",
    "FFIN": "Financial Services",
    "FFIV": "Technology",
    "FHN": "Financial Services",
    "FI": "Technology",
    "FICO": "Technology",
    "FISV": "Technology",
    "FITB": "Financial Services",
    "FIVE": "Consumer Defensive",
    "FL": "Consumer Cyclical",
    "FLG": "Financial Services",
    "FLR": "Industrials",
    "FLS": "Industrials",
    "FNB": "Financial Services",
    "FND": "Consumer Cyclical",
    "FNF": "Financial Services",
    "FOLD": "Healthcare",
    "FOX": "Communication Services",
    "FOXA": "Communication Services",
    "FR": "Real Estate",
    "FROG": "Technology",
    "FRT": "Real Estate",
    "FSLR": "Energy",
    "FTAI": "Industrials",
    "FTV": "Industrials",
    "FWONK": "Communication Services",
    "FYBR": "Communication Services",
    "GAP": "Consumer Cyclical",
    "GBCI": "Financial Services",
    "GDDY": "Technology",
    "GEHC": "Healthcare",
    "GEN": "Technology",
    "GGG": "Industrials",
    "GH": "Healthcare",
    "GIS": "Consumer Defensive",
    "GKOS": "Healthcare",
    "GL": "Financial Services",
    "GLPI": "Real Estate",
    "GLXY": "Financial Services",
    "GME": "Consumer Cyclical",
    "GMED": "Healthcare",
    "GMS": "Industrials",
    "GNRC": "Industrials",
    "GNTX": "Consumer Cyclical",
    "GPC": "Consumer Cyclical",
    "GPN": "Financial Services",
    "GSAT": "Communication Services",
    "GTES": "Industrials",
    "GTLB": "Technology",
    "GTLS": "Industrials",
    "GVA": "Industrials",
    "GWRE": "Technology",
    "GXO": "Industrials",
    "H": "Consumer Cyclical",
    "HAL": "Energy",
    "HALO": "Healthcare",
    "HAS": "Consumer Cyclical",
    "HASI": "Financial Services",
    "HBAN": "Financial Services",
    "HEI": "Industrials",
    "HESM": "Energy",
    "HI": "Industrials",
    "HIG": "Financial Services",
    "HIMS": "Healthcare",
    "HL": "Basic Materials",
    "HLI": "Financial Services",
    "HLNE": "Financial Services",
    "HOLX": "Healthcare",
    "HOMB": "Financial Services",
    "HOUS": "Real Estate",
    "HPE": "Technology",
    "HPQ": "Technology",
    "HQY": "Healthcare",
    "HR": "Real Estate",
    "HRB": "Consumer Cyclical",
    "HRL": "Consumer Defensive",
    "HSIC": "Healthcare",
    "HST": "Real Estate",
    "HSY": "Consumer Defensive",
    "HUBB": "Industrials",
    "HUBS": "Technology",
    "HUM": "Healthcare",
    "HUT": "Financial Services",
    "HWC": "Financial Services",
    "HXL": "Industrials",
    "IBDQ": "Financial Services",
    "IBP": "Consumer Cyclical",
    "IBRX": "Healthcare",
    "IDA": "Utilities",
    "IDXX": "Healthcare",
    "IEP": "Industrials",
    "IEX": "Industrials",
    "IFF": "Basic Materials",
    "ILMN": "Healthcare",
    "IMVT": "Healthcare",
    "INCY": "Healthcare",
    "INFA": "Technology",
    "INGM": "Technology",
    "INGR": "Consumer Defensive",
    "INSM": "Healthcare",
    "INVH": "Real Estate",
    "IONQ": "Technology",
    "IONS": "Healthcare",
    "IOT": "Technology",
    "IP": "Consumer Cyclical",
    "IPG": "Communication Services",
    "IQV": "Healthcare",
    "IR": "Industrials",
    "IRDM": "Communication Services",
    "IRM": "Real Estate",
    "IT": "Industrials",
    "ITT": "Industrials",
    "IVZ": "Financial Services",
    "J": "Industrials",
    "JBHT": "Industrials",
    "JBL": "Technology",
    "JBTM": "Industrials",
    "JEF": "Financial Services",
    "JKHY": "Technology",
    "JOBY": "Industrials",
    "JXN": "Financial Services",
    "KDP": "Consumer Defensive",
    "KEX": "Industrials",
    "KEY": "Financial Services",
    "KEYS": "Technology",
    "KGS": "Energy",
    "KHC": "Consumer Defensive",
    "KIM": "Real Estate",
    "KMB": "Consumer Defensive",
    "KMX": "Consumer Cyclical",
    "KNX": "Industrials",
    "KR": "Consumer Defensive",
    "KRG": "Real Estate",
    "KRMN": "Industrials",
    "KTOS": "Industrials",
    "KVUE": "Consumer Defensive",
    "KYMR": "Healthcare",
    "L": "Financial Services",
    "LAMR": "Real Estate",
    "LAUR": "Consumer Defensive",
    "LB": "Energy",
    "LBRT": "Energy",
    "LDOS": "Technology",
    "LEA": "Consumer Cyclical",
    "LECO": "Industrials",
    "LEGN": "Healthcare",
    "LEN": "Consumer Cyclical",
    "LEVI": "Consumer Cyclical",
    "LFUS": "Technology",
    "LGND": "Healthcare",
    "LH": "Healthcare",
    "LHX": "Industrials",
    "LII": "Industrials",
    "LINE": "Real Estate",
    "LKQ": "Consumer Cyclical",
    "LNC": "Financial Services",
    "LNG": "Energy",
    "LNT": "Utilities",
    "LNTH": "Healthcare",
    "LOAR": "Industrials",
    "LPLA": "Financial Services",
    "LPX": "Basic Materials",
    "LQDA": "Healthcare",
    "LSTR": "Industrials",
    "LTH": "Consumer Cyclical",
    "LUMN": "Communication Services",
    "LUNR": "Industrials",
    "LUV": "Industrials",
    "LVS": "Consumer Cyclical",
    "LW": "Consumer Defensive",
    "LYB": "Basic Materials",
    "LYFT": "Technology",
    "LYV": "Communication Services",
    "M": "Consumer Cyclical",
    "MAA": "Real Estate",
    "MAC": "Real Estate",
    "MAIN": "Financial Services",
    "MANH": "Technology",
    "MARA": "Financial Services",
    "MAS": "Consumer Cyclical",
    "MASI": "Healthcare",
    "MC": "Financial Services",
    "MDB": "Technology",
    "MDU": "Industrials",
    "MET": "Financial Services",
    "MGM": "Consumer Cyclical",
    "MGY": "Energy",
    "MHK": "Consumer Cyclical",
    "MIDD": "Industrials",
    "MIR": "Industrials",
    "MIRM": "Healthcare",
    "MKC": "Consumer Defensive",
    "MKSI": "Technology",
    "MKTX": "Financial Services",
    "MLM": "Basic Materials",
    "MOD": "Consumer Cyclical",
    "MOH": "Healthcare",
    "MORN": "Financial Services",
    "MOS": "Basic Materials",
    "MP": "Basic Materials",
    "MPLX": "Energy",
    "MPT": "Real Estate",
    "MRCY": "Industrials",
    "MRNA": "Healthcare",
    "MSCI": "Financial Services",
    "MSM": "Industrials",
    "MSTR": "Technology",
    "MTB": "Financial Services",
    "MTCH": "Communication Services",
    "MTDR": "Energy",
    "MTG": "Financial Services",
    "MTN": "Consumer Cyclical",
    "MTZ": "Industrials",
    "MUR": "Energy",
    "NBIX": "Healthcare",
    "NCLH": "Consumer Cyclical",
    "NDAQ": "Financial Services",
    "NDSN": "Industrials",
    "NE": "Energy",
    "NFG": "Energy",
    "NI": "Utilities",
    "NJR": "Utilities",
    "NLY": "Real Estate",
    "NNN": "Real Estate",
    "NOV": "Energy",
    "NOVT": "Technology",
    "NRG": "Utilities",
    "NTAP": "Technology",
    "NTNX": "Technology",
    "NTRA": "Healthcare",
    "NUE": "Basic Materials",
    "NUVL": "Healthcare",
    "NWS": "Communication Services",
    "NWSA": "Communication Services",
    "NXT": "Technology",
    "NYT": "Communication Services",
    "O": "Real Estate",
    "OBDC": "Financial Services",
    "OC": "Industrials",
    "ODFL": "Industrials",
    "OGE": "Utilities",
    "OGS": "Utilities",
    "OHI": "Real Estate",
    "OKE": "Energy",
    "OKLO": "Utilities",
    "OKTA": "Technology",
    "OLLI": "Consumer Defensive",
    "OMC": "Communication Services",
    "OMF": "Financial Services",
    "ONB": "Financial Services",
    "ORA": "Utilities",
    "ORI": "Financial Services",
    "OSCR": "Healthcare",
    "OSK": "Industrials",
    "OTF": "Financial Services",
    "OTIS": "Industrials",
    "OUT": "Real Estate",
    "OVV": "Energy",
    "OWL": "Financial Services",
    "OXY": "Energy",
    "OZK": "Financial Services",
    "P": "Industrials",
    "PAA": "Energy",
    "PACS": "Financial Services",
    "PAGP": "Energy",
    "PATH": "Technology",
    "PAYX": "Industrials",
    "PB": "Financial Services",
    "PBF": "Energy",
    "PCAR": "Industrials",
    "PCG": "Utilities",
    "PCOR": "Technology",
    "PCTY": "Technology",
    "PCVX": "Healthcare",
    "PECO": "Real Estate",
    "PEG": "Utilities",
    "PEGA": "Technology",
    "PEN": "Healthcare",
    "PFG": "Financial Services",
    "PFGC": "Consumer Defensive",
    "PHM": "Consumer Cyclical",
    "PINS": "Communication Services",
    "PIPR": "Financial Services",
    "PKG": "Consumer Cyclical",
    "PL": "Industrials",
    "PNFP": "Financial Services",
    "PNW": "Utilities",
    "PODD": "Healthcare",
    "POOL": "Industrials",
    "POR": "Utilities",
    "POWL": "Industrials",
    "PPC": "Consumer Defensive",
    "PPG": "Basic Materials",
    "PPL": "Utilities",
    "PR": "Energy",
    "PRIM": "Industrials",
    "PRM": "Basic Materials",
    "PRMB": "Consumer Defensive",
    "PRU": "Financial Services",
    "PSA": "Real Estate",
    "PSKY": "Communication Services",
    "PSN": "Industrials",
    "PTC": "Technology",
    "PTCT": "Healthcare",
    "PTEN": "Energy",
    "PTGX": "Healthcare",
    "PYPL": "Financial Services",
    "QS": "Consumer Cyclical",
    "R": "Industrials",
    "RAL": "Industrials",
    "RBA": "Industrials",
    "RBRK": "Technology",
    "RDDT": "Communication Services",
    "RDN": "Financial Services",
    "REG": "Real Estate",
    "RELY": "Technology",
    "REXR": "Real Estate",
    "REYN": "Consumer Cyclical",
    "RF": "Financial Services",
    "RGEN": "Healthcare",
    "RGLD": "Basic Materials",
    "RGTI": "Technology",
    "RHP": "Real Estate",
    "RIOT": "Financial Services",
    "RITM": "Real Estate",
    "RIVN": "Consumer Cyclical",
    "RJF": "Financial Services",
    "RKT": "Financial Services",
    "RL": "Consumer Cyclical",
    "RLI": "Financial Services",
    "RMD": "Healthcare",
    "ROAD": "Industrials",
    "ROK": "Industrials",
    "ROKU": "Communication Services",
    "ROL": "Consumer Cyclical",
    "ROP": "Technology",
    "RPM": "Basic Materials",
    "RPRX": "Healthcare",
    "RRC": "Energy",
    "RRX": "Industrials",
    "RSI": "Consumer Cyclical",
    "RUSHA": "Consumer Cyclical",
    "RVMD": "Healthcare",
    "RVTY": "Healthcare",
    "RYTM": "Healthcare",
    "S": "Technology",
    "SAIA": "Industrials",
    "SAIL": "Technology",
    "SANM": "Technology",
    "SARO": "Industrials",
    "SATS": "Technology",
    "SBAC": "Real Estate",
    "SBRA": "Real Estate",
    "SCI": "Consumer Cyclical",
    "SEIC": "Financial Services",
    "SF": "Financial Services",
    "SFD": "Consumer Defensive",
    "SFM": "Consumer Defensive",
    "SGI": "Consumer Defensive",
    "SIGI": "Financial Services",
    "SIRI": "Communication Services",
    "SITE": "Industrials",
    "SJM": "Consumer Defensive",
    "SMMT": "Healthcare",
    "SN": "Consumer Cyclical",
    "SNDR": "Industrials",
    "SNEX": "Financial Services",
    "SNOW": "Technology",
    "SNX": "Technology",
    "SOFI": "Financial Services",
    "SOLV": "Healthcare",
    "SON": "Consumer Cyclical",
    "SPHR": "Communication Services",
    "SPXC": "Industrials",
    "SR": "Utilities",
    "SRRK": "Healthcare",
    "SSB": "Financial Services",
    "SSNC": "Technology",
    "SSRM": "Basic Materials",
    "ST": "Technology",
    "STAG": "Real Estate",
    "STEP": "Financial Services",
    "STLD": "Basic Materials",
    "STRL": "Industrials",
    "STT": "Financial Services",
    "STWD": "Real Estate",
    "STZ": "Consumer Defensive",
    "SUI": "Real Estate",
    "SUN": "Energy",
    "SWK": "Industrials",
    "SWX": "Utilities",
    "SYF": "Financial Services",
    "SYM": "Industrials",
    "SYRE": "Healthcare",
    "SYY": "Consumer Defensive",
    "TAP": "Consumer Defensive",
    "TECH": "Healthcare",
    "TEM": "Healthcare",
    "TFX": "Healthcare",
    "TGT": "Consumer Defensive",
    "TGTX": "Healthcare",
    "THC": "Healthcare",
    "TKO": "Communication Services",
    "TKR": "Industrials",
    "TLN": "Utilities",
    "TMHC": "Consumer Cyclical",
    "TOL": "Consumer Cyclical",
    "TOST": "Technology",
    "TPG": "Financial Services",
    "TPR": "Consumer Cyclical",
    "TRGP": "Energy",
    "TRMB": "Technology",
    "TRNO": "Real Estate",
    "TROW": "Financial Services",
    "TRU": "Industrials",
    "TSCO": "Consumer Cyclical",
    "TSN": "Consumer Defensive",
    "TTAN": "Technology",
    "TTC": "Industrials",
    "TTD": "Communication Services",
    "TTEK": "Industrials",
    "TTMI": "Technology",
    "TTWO": "Communication Services",
    "TW": "Financial Services",
    "TWLO": "Technology",
    "TXNM": "Utilities",
    "TXRH": "Consumer Cyclical",
    "TXT": "Industrials",
    "TYL": "Technology",
    "U": "Technology",
    "UAL": "Industrials",
    "UBSI": "Financial Services",
    "UDR": "Real Estate",
    "UEC": "Energy",
    "UFPI": "Basic Materials",
    "UGI": "Utilities",
    "UHS": "Healthcare",
    "ULS": "Industrials",
    "ULTA": "Consumer Cyclical",
    "UMBF": "Financial Services",
    "UNM": "Financial Services",
    "URBN": "Consumer Cyclical",
    "URI": "Industrials",
    "USFD": "Consumer Defensive",
    "UTHR": "Healthcare",
    "UUUU": "Energy",
    "VCTR": "Financial Services",
    "VEEV": "Healthcare",
    "VFC": "Consumer Cyclical",
    "VG": "Energy",
    "VIAV": "Technology",
    "VICI": "Real Estate",
    "VICR": "Technology",
    "VIRT": "Financial Services",
    "VLTO": "Industrials",
    "VLY": "Financial Services",
    "VMC": "Basic Materials",
    "VNO": "Real Estate",
    "VNOM": "Energy",
    "VOYA": "Financial Services",
    "VRSK": "Technology",
    "VRSN": "Technology",
    "VSAT": "Technology",
    "VST": "Utilities",
    "VTRS": "Healthcare",
    "W": "Consumer Cyclical",
    "WAB": "Industrials",
    "WAL": "Financial Services",
    "WAT": "Healthcare",
    "WBS": "Financial Services",
    "WCC": "Industrials",
    "WDAY": "Technology",
    "WEC": "Utilities",
    "WES": "Energy",
    "WEX": "Technology",
    "WFRD": "Energy",
    "WH": "Consumer Cyclical",
    "WLK": "Basic Materials",
    "WMG": "Communication Services",
    "WMS": "Industrials",
    "WPC": "Real Estate",
    "WRB": "Financial Services",
    "WSC": "Industrials",
    "WSM": "Consumer Cyclical",
    "WSO": "Industrials",
    "WST": "Healthcare",
    "WTFC": "Financial Services",
    "WTRG": "Utilities",
    "WULF": "Financial Services",
    "WWD": "Industrials",
    "WY": "Basic Materials",
    "WYNN": "Consumer Cyclical",
    "XEL": "Utilities",
    "XMTR": "Industrials",
    "XPO": "Industrials",
    "XYL": "Industrials",
    "YOU": "Technology",
    "YUM": "Consumer Cyclical",
    "Z": "Communication Services",
    "ZBH": "Healthcare",
    "ZBRA": "Technology",
    "ZETA": "Technology",
    "ZG": "Communication Services",
    "ZION": "Financial Services",
    "ZM": "Technology",
    "ZS": "Technology",
    "ZTS": "Healthcare",
    "ZWS": "Industrials"
}
SPEC_ALPHA_BUCKET_MAP = {
    "A": "MEDIUM",
    "AA": "MEDIUM",
    "AAL": "MEDIUM",
    "AAON": "MEDIUM",
    "ACA": "MEDIUM",
    "ACHR": "MEDIUM",
    "ACI": "MEDIUM",
    "ACLX": "WEAK_DELISTED",
    "ACM": "MEDIUM",
    "ADC": "MEDIUM",
    "ADM": "MEDIUM",
    "ADSK": "MEDIUM",
    "ADT": "MEDIUM",
    "AEE": "MEDIUM",
    "AEIS": "MEDIUM",
    "AES": "MEDIUM",
    "AFL": "MEDIUM",
    "AFRM": "MEDIUM",
    "AGCO": "MEDIUM",
    "AGNC": "MEDIUM",
    "AGX": "MEDIUM",
    "AHR": "MEDIUM",
    "AIG": "MEDIUM",
    "AJG": "MEDIUM",
    "AKAM": "MEDIUM",
    "AKRO": "WEAK_DELISTED",
    "AL": "WEAK_DELISTED",
    "ALB": "MEDIUM",
    "ALE": "WEAK_DELISTED",
    "ALEX": "WEAK_DELISTED",
    "ALGN": "MEDIUM",
    "ALK": "MEDIUM",
    "ALL": "MEDIUM",
    "ALLY": "MEDIUM",
    "ALNY": "MEDIUM",
    "ALSN": "MEDIUM",
    "AM": "MEDIUM",
    "AMBC": "WEAK_DELISTED",
    "AME": "MEDIUM",
    "AMH": "MEDIUM",
    "AMP": "MEDIUM",
    "AMRK": "WEAK_DELISTED",
    "AMTM": "MEDIUM",
    "AOS": "MEDIUM",
    "APA": "MEDIUM",
    "APG": "MEDIUM",
    "APGE": "MEDIUM",
    "APLD": "MEDIUM",
    "APLS": "WEAK_DELISTED",
    "APPF": "MEDIUM",
    "AR": "MEDIUM",
    "ARCC": "MEDIUM",
    "ARE": "MEDIUM",
    "ARES": "MEDIUM",
    "ARMK": "MEDIUM",
    "AROC": "MEDIUM",
    "ARW": "MEDIUM",
    "ARWR": "MEDIUM",
    "ASB": "MEDIUM",
    "ASGN": "WEAK_DELISTED",
    "ASTS": "MEDIUM",
    "ATGE": "WEAK_DELISTED",
    "ATI": "MEDIUM",
    "ATO": "MEDIUM",
    "ATR": "MEDIUM",
    "ATUS": "WEAK_DELISTED",
    "AUB": "MEDIUM",
    "AUR": "MEDIUM",
    "AVAV": "MEDIUM",
    "AVB": "MEDIUM",
    "AVDL": "WEAK_DELISTED",
    "AVDX": "WEAK_DELISTED",
    "AVT": "MEDIUM",
    "AVTR": "MEDIUM",
    "AVY": "MEDIUM",
    "AWK": "MEDIUM",
    "AXL": "WEAK_DELISTED",
    "AXON": "MEDIUM",
    "AXSM": "MEDIUM",
    "AXTA": "MEDIUM",
    "AZO": "MEDIUM",
    "BAH": "MEDIUM",
    "BALL": "MEDIUM",
    "BASE": "WEAK_DELISTED",
    "BAX": "MEDIUM",
    "BBIO": "MEDIUM",
    "BBY": "MEDIUM",
    "BC": "MEDIUM",
    "BDX": "MEDIUM",
    "BEN": "MEDIUM",
    "BEPC": "MEDIUM",
    "BG": "MEDIUM",
    "BGC": "MEDIUM",
    "BIIB": "MEDIUM",
    "BIPC": "MEDIUM",
    "BITF": "WEAK_DELISTED",
    "BJ": "MEDIUM",
    "BKH": "MEDIUM",
    "BLD": "MEDIUM",
    "BLDR": "MEDIUM",
    "BMRN": "MEDIUM",
    "BOOT": "MEDIUM",
    "BPOP": "MEDIUM",
    "BR": "MEDIUM",
    "BRFS": "WEAK_DELISTED",
    "BRKR": "MEDIUM",
    "BRO": "MEDIUM",
    "BROS": "MEDIUM",
    "BRX": "MEDIUM",
    "BSY": "MEDIUM",
    "BTSG": "MEDIUM",
    "BURL": "MEDIUM",
    "BWA": "MEDIUM",
    "BWXT": "MEDIUM",
    "BXP": "MEDIUM",
    "BYD": "MEDIUM",
    "CADE": "WEAK_DELISTED",
    "CAG": "MEDIUM",
    "CAH": "MEDIUM",
    "CAR": "MEDIUM",
    "CARR": "MEDIUM",
    "CART": "MEDIUM",
    "CASY": "MEDIUM",
    "CAVA": "MEDIUM",
    "CBRE": "MEDIUM",
    "CBSH": "MEDIUM",
    "CCCS": "WEAK_DELISTED",
    "CCI": "MEDIUM",
    "CCK": "MEDIUM",
    "CCL": "MEDIUM",
    "CDE": "MEDIUM",
    "CDW": "MEDIUM",
    "CE": "MEDIUM",
    "CELH": "MEDIUM",
    "CENX": "MEDIUM",
    "CF": "MEDIUM",
    "CFG": "MEDIUM",
    "CFLT": "WEAK_DELISTED",
    "CG": "MEDIUM",
    "CGNX": "MEDIUM",
    "CGON": "MEDIUM",
    "CHD": "MEDIUM",
    "CHDN": "MEDIUM",
    "CHH": "MEDIUM",
    "CHRD": "MEDIUM",
    "CHRW": "MEDIUM",
    "CHTR": "MEDIUM",
    "CHWY": "MEDIUM",
    "CHYM": "MEDIUM",
    "CIFR": "MEDIUM",
    "CINF": "MEDIUM",
    "CIVI": "WEAK_DELISTED",
    "CLF": "MEDIUM",
    "CLH": "MEDIUM",
    "CLX": "MEDIUM",
    "CMA": "WEAK_DELISTED",
    "CMC": "MEDIUM",
    "CMG": "MEDIUM",
    "CMS": "MEDIUM",
    "CNA": "MEDIUM",
    "CNC": "MEDIUM",
    "CNM": "MEDIUM",
    "CNP": "MEDIUM",
    "CNX": "MEDIUM",
    "COGT": "MEDIUM",
    "COHR": "MEDIUM",
    "COIN": "MEDIUM",
    "COKE": "MEDIUM",
    "COLB": "MEDIUM",
    "COMM": "WEAK_DELISTED",
    "COMP": "MEDIUM",
    "COO": "MEDIUM",
    "COOP": "WEAK_DELISTED",
    "COR": "MEDIUM",
    "CORT": "MEDIUM",
    "CORZ": "MEDIUM",
    "CPAY": "MEDIUM",
    "CPB": "MEDIUM",
    "CPRT": "MEDIUM",
    "CPT": "MEDIUM",
    "CR": "MEDIUM",
    "CRBG": "MEDIUM",
    "CRC": "MEDIUM",
    "CRCL": "MEDIUM",
    "CRDO": "MEDIUM",
    "CRL": "MEDIUM",
    "CROX": "MEDIUM",
    "CRS": "MEDIUM",
    "CRWV": "MEDIUM",
    "CSGP": "MEDIUM",
    "CSGS": "WEAK_DELISTED",
    "CTRE": "MEDIUM",
    "CTSH": "MEDIUM",
    "CTVA": "MEDIUM",
    "CUBE": "MEDIUM",
    "CUK": "WEAK_DELISTED",
    "CWAN": "MEDIUM",
    "CWEN": "MEDIUM",
    "CWST": "MEDIUM",
    "CYBR": "WEAK_DELISTED",
    "CYTK": "MEDIUM",
    "CZR": "MEDIUM",
    "D": "MEDIUM",
    "DAL": "MEDIUM",
    "DAR": "MEDIUM",
    "DAY": "WEAK_DELISTED",
    "DBX": "MEDIUM",
    "DCI": "MEDIUM",
    "DD": "MEDIUM",
    "DECK": "MEDIUM",
    "DG": "MEDIUM",
    "DHI": "MEDIUM",
    "DINO": "MEDIUM",
    "DKNG": "MEDIUM",
    "DKS": "MEDIUM",
    "DLB": "MEDIUM",
    "DLTR": "MEDIUM",
    "DOC": "MEDIUM",
    "DOCN": "MEDIUM",
    "DOCU": "MEDIUM",
    "DOOO": "WEAK_DELISTED",
    "DOV": "MEDIUM",
    "DOW": "MEDIUM",
    "DOX": "MEDIUM",
    "DPZ": "MEDIUM",
    "DRI": "MEDIUM",
    "DRS": "MEDIUM",
    "DT": "MEDIUM",
    "DTE": "MEDIUM",
    "DTM": "MEDIUM",
    "DUOL": "MEDIUM",
    "DVA": "MEDIUM",
    "DVAX": "WEAK_DELISTED",
    "DVN": "MEDIUM",
    "DXCM": "MEDIUM",
    "DY": "MEDIUM",
    "EA": "MEDIUM",
    "EAT": "MEDIUM",
    "EB": "WEAK_DELISTED",
    "EBAY": "MEDIUM",
    "EBC": "MEDIUM",
    "EBR": "WEAK_DELISTED",
    "ECG": "MEDIUM",
    "ED": "MEDIUM",
    "EFX": "MEDIUM",
    "EHC": "MEDIUM",
    "EIX": "MEDIUM",
    "EL": "MEDIUM",
    "ELAN": "MEDIUM",
    "ELS": "MEDIUM",
    "EMN": "MEDIUM",
    "ENPH": "MEDIUM",
    "ENS": "MEDIUM",
    "ENSG": "MEDIUM",
    "EPAM": "MEDIUM",
    "EPR": "MEDIUM",
    "EPRT": "MEDIUM",
    "EQH": "MEDIUM",
    "EQR": "MEDIUM",
    "EQT": "MEDIUM",
    "ERJ": "WEAK_DELISTED",
    "ES": "MEDIUM",
    "ESAB": "MEDIUM",
    "ESI": "MEDIUM",
    "ESTC": "MEDIUM",
    "ETNB": "WEAK_DELISTED",
    "ETR": "MEDIUM",
    "ETSY": "MEDIUM",
    "EVRG": "MEDIUM",
    "EW": "MEDIUM",
    "EWBC": "MEDIUM",
    "EXAS": "WEAK_DELISTED",
    "EXC": "MEDIUM",
    "EXE": "MEDIUM",
    "EXEL": "MEDIUM",
    "EXLS": "MEDIUM",
    "EXPD": "MEDIUM",
    "EXPE": "MEDIUM",
    "EXR": "MEDIUM",
    "F": "MEDIUM",
    "FAF": "MEDIUM",
    "FANG": "MEDIUM",
    "FAST": "MEDIUM",
    "FBIN": "MEDIUM",
    "FCN": "MEDIUM",
    "FDS": "MEDIUM",
    "FE": "MEDIUM",
    "FFIN": "MEDIUM",
    "FFIV": "MEDIUM",
    "FHN": "MEDIUM",
    "FI": "WEAK_DELISTED",
    "FICO": "MEDIUM",
    "FISV": "MEDIUM",
    "FITB": "MEDIUM",
    "FIVE": "MEDIUM",
    "FL": "WEAK_DELISTED",
    "FLG": "MEDIUM",
    "FLR": "MEDIUM",
    "FLS": "MEDIUM",
    "FNB": "MEDIUM",
    "FND": "MEDIUM",
    "FNF": "MEDIUM",
    "FOLD": "WEAK_DELISTED",
    "FOX": "MEDIUM",
    "FOXA": "MEDIUM",
    "FR": "MEDIUM",
    "FROG": "MEDIUM",
    "FRT": "MEDIUM",
    "FSLR": "MEDIUM",
    "FTAI": "MEDIUM",
    "FTV": "MEDIUM",
    "FWONK": "MEDIUM",
    "FYBR": "WEAK_DELISTED",
    "GAP": "MEDIUM",
    "GBCI": "MEDIUM",
    "GDDY": "MEDIUM",
    "GEHC": "MEDIUM",
    "GEN": "MEDIUM",
    "GGG": "MEDIUM",
    "GH": "MEDIUM",
    "GIS": "MEDIUM",
    "GKOS": "MEDIUM",
    "GL": "MEDIUM",
    "GLPI": "MEDIUM",
    "GLXY": "MEDIUM",
    "GME": "MEDIUM",
    "GMED": "MEDIUM",
    "GMS": "WEAK_DELISTED",
    "GNRC": "MEDIUM",
    "GNTX": "MEDIUM",
    "GPC": "MEDIUM",
    "GPN": "MEDIUM",
    "GSAT": "MEDIUM",
    "GTES": "MEDIUM",
    "GTLB": "MEDIUM",
    "GTLS": "MEDIUM",
    "GVA": "MEDIUM",
    "GWRE": "MEDIUM",
    "GXO": "MEDIUM",
    "H": "MEDIUM",
    "HAL": "MEDIUM",
    "HALO": "MEDIUM",
    "HAS": "MEDIUM",
    "HASI": "MEDIUM",
    "HBAN": "MEDIUM",
    "HEI": "MEDIUM",
    "HESM": "MEDIUM",
    "HI": "WEAK_DELISTED",
    "HIG": "MEDIUM",
    "HIMS": "MEDIUM",
    "HL": "MEDIUM",
    "HLI": "MEDIUM",
    "HLNE": "MEDIUM",
    "HOLX": "WEAK_DELISTED",
    "HOMB": "MEDIUM",
    "HOUS": "WEAK_DELISTED",
    "HPE": "MEDIUM",
    "HPQ": "MEDIUM",
    "HQY": "MEDIUM",
    "HR": "MEDIUM",
    "HRB": "MEDIUM",
    "HRL": "MEDIUM",
    "HSIC": "MEDIUM",
    "HST": "MEDIUM",
    "HSY": "MEDIUM",
    "HUBB": "MEDIUM",
    "HUBS": "MEDIUM",
    "HUM": "MEDIUM",
    "HUT": "MEDIUM",
    "HWC": "MEDIUM",
    "HXL": "MEDIUM",
    "IBDQ": "WEAK_DELISTED",
    "IBP": "MEDIUM",
    "IBRX": "MEDIUM",
    "IDA": "MEDIUM",
    "IDXX": "MEDIUM",
    "IEP": "MEDIUM",
    "IEX": "MEDIUM",
    "IFF": "MEDIUM",
    "ILMN": "MEDIUM",
    "IMVT": "MEDIUM",
    "INCY": "MEDIUM",
    "INFA": "WEAK_DELISTED",
    "INGM": "MEDIUM",
    "INGR": "MEDIUM",
    "INSM": "MEDIUM",
    "INVH": "MEDIUM",
    "IONQ": "MEDIUM",
    "IONS": "MEDIUM",
    "IOT": "MEDIUM",
    "IP": "MEDIUM",
    "IPG": "WEAK_DELISTED",
    "IQV": "MEDIUM",
    "IR": "MEDIUM",
    "IRDM": "MEDIUM",
    "IRM": "MEDIUM",
    "IT": "MEDIUM",
    "ITT": "MEDIUM",
    "IVZ": "MEDIUM",
    "J": "MEDIUM",
    "JBHT": "MEDIUM",
    "JBL": "MEDIUM",
    "JBTM": "MEDIUM",
    "JEF": "MEDIUM",
    "JKHY": "MEDIUM",
    "JOBY": "MEDIUM",
    "JXN": "MEDIUM",
    "KDP": "MEDIUM",
    "KEX": "MEDIUM",
    "KEY": "MEDIUM",
    "KEYS": "MEDIUM",
    "KGS": "MEDIUM",
    "KHC": "MEDIUM",
    "KIM": "MEDIUM",
    "KMB": "MEDIUM",
    "KMX": "MEDIUM",
    "KNX": "MEDIUM",
    "KR": "MEDIUM",
    "KRG": "MEDIUM",
    "KRMN": "MEDIUM",
    "KTOS": "MEDIUM",
    "KVUE": "MEDIUM",
    "KYMR": "MEDIUM",
    "L": "MEDIUM",
    "LAMR": "MEDIUM",
    "LAUR": "MEDIUM",
    "LB": "MEDIUM",
    "LBRT": "MEDIUM",
    "LDOS": "MEDIUM",
    "LEA": "MEDIUM",
    "LECO": "MEDIUM",
    "LEGN": "MEDIUM",
    "LEN": "MEDIUM",
    "LEVI": "MEDIUM",
    "LFUS": "MEDIUM",
    "LGND": "MEDIUM",
    "LH": "MEDIUM",
    "LHX": "MEDIUM",
    "LII": "MEDIUM",
    "LINE": "MEDIUM",
    "LKQ": "MEDIUM",
    "LNC": "MEDIUM",
    "LNG": "MEDIUM",
    "LNT": "MEDIUM",
    "LNTH": "MEDIUM",
    "LOAR": "MEDIUM",
    "LPLA": "MEDIUM",
    "LPX": "MEDIUM",
    "LQDA": "MEDIUM",
    "LSTR": "MEDIUM",
    "LTH": "MEDIUM",
    "LUMN": "MEDIUM",
    "LUNR": "MEDIUM",
    "LUV": "MEDIUM",
    "LVS": "MEDIUM",
    "LW": "MEDIUM",
    "LYB": "MEDIUM",
    "LYFT": "MEDIUM",
    "LYV": "MEDIUM",
    "M": "MEDIUM",
    "MAA": "MEDIUM",
    "MAC": "MEDIUM",
    "MAIN": "MEDIUM",
    "MANH": "MEDIUM",
    "MARA": "MEDIUM",
    "MAS": "MEDIUM",
    "MASI": "MEDIUM",
    "MC": "MEDIUM",
    "MDB": "MEDIUM",
    "MDU": "MEDIUM",
    "MET": "MEDIUM",
    "MGM": "MEDIUM",
    "MGY": "MEDIUM",
    "MHK": "MEDIUM",
    "MIDD": "MEDIUM",
    "MIR": "MEDIUM",
    "MIRM": "MEDIUM",
    "MKC": "MEDIUM",
    "MKSI": "MEDIUM",
    "MKTX": "MEDIUM",
    "MLM": "MEDIUM",
    "MOD": "MEDIUM",
    "MOH": "MEDIUM",
    "MORN": "MEDIUM",
    "MOS": "MEDIUM",
    "MP": "MEDIUM",
    "MPLX": "MEDIUM",
    "MPT": "MEDIUM",
    "MRCY": "MEDIUM",
    "MRNA": "MEDIUM",
    "MSCI": "MEDIUM",
    "MSM": "MEDIUM",
    "MSTR": "MEDIUM",
    "MTB": "MEDIUM",
    "MTCH": "MEDIUM",
    "MTDR": "MEDIUM",
    "MTG": "MEDIUM",
    "MTN": "MEDIUM",
    "MTZ": "MEDIUM",
    "MUR": "MEDIUM",
    "NBIX": "MEDIUM",
    "NCLH": "MEDIUM",
    "NDAQ": "MEDIUM",
    "NDSN": "MEDIUM",
    "NE": "MEDIUM",
    "NFG": "MEDIUM",
    "NI": "MEDIUM",
    "NJR": "MEDIUM",
    "NLY": "MEDIUM",
    "NNN": "MEDIUM",
    "NOV": "MEDIUM",
    "NOVT": "MEDIUM",
    "NRG": "MEDIUM",
    "NTAP": "MEDIUM",
    "NTNX": "MEDIUM",
    "NTRA": "MEDIUM",
    "NUE": "MEDIUM",
    "NUVL": "MEDIUM",
    "NWS": "MEDIUM",
    "NWSA": "MEDIUM",
    "NXT": "MEDIUM",
    "NYT": "MEDIUM",
    "O": "MEDIUM",
    "OBDC": "MEDIUM",
    "OC": "MEDIUM",
    "ODFL": "MEDIUM",
    "OGE": "MEDIUM",
    "OGS": "MEDIUM",
    "OHI": "MEDIUM",
    "OKE": "MEDIUM",
    "OKLO": "MEDIUM",
    "OKTA": "MEDIUM",
    "OLLI": "MEDIUM",
    "OMC": "MEDIUM",
    "OMF": "MEDIUM",
    "ONB": "MEDIUM",
    "ORA": "MEDIUM",
    "ORI": "MEDIUM",
    "OSCR": "MEDIUM",
    "OSK": "MEDIUM",
    "OTF": "MEDIUM",
    "OTIS": "MEDIUM",
    "OUT": "MEDIUM",
    "OVV": "MEDIUM",
    "OWL": "MEDIUM",
    "OXY": "MEDIUM",
    "OZK": "MEDIUM",
    "P": "MEDIUM",
    "PAA": "MEDIUM",
    "PACS": "MEDIUM",
    "PAGP": "MEDIUM",
    "PATH": "MEDIUM",
    "PAYX": "MEDIUM",
    "PB": "MEDIUM",
    "PBF": "MEDIUM",
    "PCAR": "MEDIUM",
    "PCG": "MEDIUM",
    "PCOR": "MEDIUM",
    "PCTY": "MEDIUM",
    "PCVX": "MEDIUM",
    "PECO": "MEDIUM",
    "PEG": "MEDIUM",
    "PEGA": "MEDIUM",
    "PEN": "MEDIUM",
    "PFG": "MEDIUM",
    "PFGC": "MEDIUM",
    "PHM": "MEDIUM",
    "PINS": "MEDIUM",
    "PIPR": "MEDIUM",
    "PKG": "MEDIUM",
    "PL": "MEDIUM",
    "PNFP": "MEDIUM",
    "PNW": "MEDIUM",
    "PODD": "MEDIUM",
    "POOL": "MEDIUM",
    "POR": "MEDIUM",
    "POWL": "MEDIUM",
    "PPC": "MEDIUM",
    "PPG": "MEDIUM",
    "PPL": "MEDIUM",
    "PR": "MEDIUM",
    "PRIM": "MEDIUM",
    "PRM": "MEDIUM",
    "PRMB": "MEDIUM",
    "PRU": "MEDIUM",
    "PSA": "MEDIUM",
    "PSKY": "MEDIUM",
    "PSN": "MEDIUM",
    "PTC": "MEDIUM",
    "PTCT": "MEDIUM",
    "PTEN": "MEDIUM",
    "PTGX": "MEDIUM",
    "PYPL": "MEDIUM",
    "QS": "MEDIUM",
    "R": "MEDIUM",
    "RAL": "MEDIUM",
    "RBA": "MEDIUM",
    "RBRK": "MEDIUM",
    "RDDT": "MEDIUM",
    "RDN": "MEDIUM",
    "REG": "MEDIUM",
    "RELY": "MEDIUM",
    "REXR": "MEDIUM",
    "REYN": "MEDIUM",
    "RF": "MEDIUM",
    "RGEN": "MEDIUM",
    "RGLD": "MEDIUM",
    "RGTI": "MEDIUM",
    "RHP": "MEDIUM",
    "RIOT": "MEDIUM",
    "RITM": "MEDIUM",
    "RIVN": "MEDIUM",
    "RJF": "MEDIUM",
    "RKT": "MEDIUM",
    "RL": "MEDIUM",
    "RLI": "MEDIUM",
    "RMD": "MEDIUM",
    "ROAD": "MEDIUM",
    "ROK": "MEDIUM",
    "ROKU": "MEDIUM",
    "ROL": "MEDIUM",
    "ROP": "MEDIUM",
    "RPM": "MEDIUM",
    "RPRX": "MEDIUM",
    "RRC": "MEDIUM",
    "RRX": "MEDIUM",
    "RSI": "MEDIUM",
    "RUSHA": "MEDIUM",
    "RVMD": "MEDIUM",
    "RVTY": "MEDIUM",
    "RYTM": "MEDIUM",
    "S": "MEDIUM",
    "SAIA": "MEDIUM",
    "SAIL": "MEDIUM",
    "SANM": "MEDIUM",
    "SARO": "MEDIUM",
    "SATS": "MEDIUM",
    "SBAC": "MEDIUM",
    "SBRA": "MEDIUM",
    "SCI": "MEDIUM",
    "SEIC": "MEDIUM",
    "SF": "MEDIUM",
    "SFD": "MEDIUM",
    "SFM": "MEDIUM",
    "SGI": "MEDIUM",
    "SIGI": "MEDIUM",
    "SIRI": "MEDIUM",
    "SITE": "MEDIUM",
    "SJM": "MEDIUM",
    "SMMT": "MEDIUM",
    "SN": "MEDIUM",
    "SNDR": "MEDIUM",
    "SNEX": "MEDIUM",
    "SNOW": "MEDIUM",
    "SNX": "MEDIUM",
    "SOFI": "MEDIUM",
    "SOLV": "MEDIUM",
    "SON": "MEDIUM",
    "SPHR": "MEDIUM",
    "SPXC": "MEDIUM",
    "SR": "MEDIUM",
    "SRRK": "MEDIUM",
    "SSB": "MEDIUM",
    "SSNC": "MEDIUM",
    "SSRM": "MEDIUM",
    "ST": "MEDIUM",
    "STAG": "MEDIUM",
    "STEP": "MEDIUM",
    "STLD": "MEDIUM",
    "STRL": "MEDIUM",
    "STT": "MEDIUM",
    "STWD": "MEDIUM",
    "STZ": "MEDIUM",
    "SUI": "MEDIUM",
    "SUN": "MEDIUM",
    "SWK": "MEDIUM",
    "SWX": "MEDIUM",
    "SYF": "MEDIUM",
    "SYM": "MEDIUM",
    "SYRE": "MEDIUM",
    "SYY": "MEDIUM",
    "TAP": "MEDIUM",
    "TECH": "MEDIUM",
    "TEM": "MEDIUM",
    "TFX": "MEDIUM",
    "TGT": "MEDIUM",
    "TGTX": "MEDIUM",
    "THC": "MEDIUM",
    "TKO": "MEDIUM",
    "TKR": "MEDIUM",
    "TLN": "MEDIUM",
    "TMHC": "MEDIUM",
    "TOL": "MEDIUM",
    "TOST": "MEDIUM",
    "TPG": "MEDIUM",
    "TPR": "MEDIUM",
    "TRGP": "MEDIUM",
    "TRMB": "MEDIUM",
    "TRNO": "MEDIUM",
    "TROW": "MEDIUM",
    "TRU": "MEDIUM",
    "TSCO": "MEDIUM",
    "TSN": "MEDIUM",
    "TTAN": "MEDIUM",
    "TTC": "MEDIUM",
    "TTD": "MEDIUM",
    "TTEK": "MEDIUM",
    "TTMI": "MEDIUM",
    "TTWO": "MEDIUM",
    "TW": "MEDIUM",
    "TWLO": "MEDIUM",
    "TXNM": "MEDIUM",
    "TXRH": "MEDIUM",
    "TXT": "MEDIUM",
    "TYL": "MEDIUM",
    "U": "MEDIUM",
    "UAL": "MEDIUM",
    "UBSI": "MEDIUM",
    "UDR": "MEDIUM",
    "UEC": "MEDIUM",
    "UFPI": "MEDIUM",
    "UGI": "MEDIUM",
    "UHS": "MEDIUM",
    "ULS": "MEDIUM",
    "ULTA": "MEDIUM",
    "UMBF": "MEDIUM",
    "UNM": "MEDIUM",
    "URBN": "MEDIUM",
    "URI": "MEDIUM",
    "USFD": "MEDIUM",
    "UTHR": "MEDIUM",
    "UUUU": "MEDIUM",
    "VCTR": "MEDIUM",
    "VEEV": "MEDIUM",
    "VFC": "MEDIUM",
    "VG": "MEDIUM",
    "VIAV": "MEDIUM",
    "VICI": "MEDIUM",
    "VICR": "MEDIUM",
    "VIRT": "MEDIUM",
    "VLTO": "MEDIUM",
    "VLY": "MEDIUM",
    "VMC": "MEDIUM",
    "VNO": "MEDIUM",
    "VNOM": "MEDIUM",
    "VOYA": "MEDIUM",
    "VRSK": "MEDIUM",
    "VRSN": "MEDIUM",
    "VSAT": "MEDIUM",
    "VST": "MEDIUM",
    "VTRS": "MEDIUM",
    "W": "MEDIUM",
    "WAB": "MEDIUM",
    "WAL": "MEDIUM",
    "WAT": "MEDIUM",
    "WBS": "MEDIUM",
    "WCC": "MEDIUM",
    "WDAY": "MEDIUM",
    "WEC": "MEDIUM",
    "WES": "MEDIUM",
    "WEX": "MEDIUM",
    "WFRD": "MEDIUM",
    "WH": "MEDIUM",
    "WLK": "MEDIUM",
    "WMG": "MEDIUM",
    "WMS": "MEDIUM",
    "WPC": "MEDIUM",
    "WRB": "MEDIUM",
    "WSC": "MEDIUM",
    "WSM": "MEDIUM",
    "WSO": "MEDIUM",
    "WST": "MEDIUM",
    "WTFC": "MEDIUM",
    "WTRG": "MEDIUM",
    "WULF": "MEDIUM",
    "WWD": "MEDIUM",
    "WY": "MEDIUM",
    "WYNN": "MEDIUM",
    "XEL": "MEDIUM",
    "XMTR": "MEDIUM",
    "XPO": "MEDIUM",
    "XYL": "MEDIUM",
    "YOU": "MEDIUM",
    "YUM": "MEDIUM",
    "Z": "MEDIUM",
    "ZBH": "MEDIUM",
    "ZBRA": "MEDIUM",
    "ZETA": "MEDIUM",
    "ZG": "MEDIUM",
    "ZION": "MEDIUM",
    "ZM": "MEDIUM",
    "ZS": "MEDIUM",
    "ZTS": "MEDIUM",
    "ZWS": "MEDIUM"
}
SPEC_ALPHA_CRYPTO_TICKERS = set([
    "BITF",
    "CIFR",
    "COIN",
    "HUT",
    "MARA",
    "MSTR",
    "RIOT",
    "WULF"
])

_old_init_db = init_db
_old_compute_equity_snapshot_data = compute_equity_snapshot_data
_old_dynamic_portfolio_allocation_targets = dynamic_portfolio_allocation_targets
_old_format_combined_portfolio_report = format_combined_portfolio_report
_old_sleeve_performance_summary = sleeve_performance_summary
_old_realized_performance_all_time = realized_performance_all_time
_old_maybe_send_wealth_core_signal = maybe_send_wealth_core_signal
_old_handle_command = handle_command


def init_db() -> None:
    _old_init_db()
    conn = db_connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS spec_positions (
                ticker TEXT PRIMARY KEY,
                spec_position_id TEXT NOT NULL UNIQUE,
                strategy_version TEXT NOT NULL,
                shares REAL NOT NULL CHECK (shares > 0),
                avg_entry_price REAL NOT NULL CHECK (avg_entry_price > 0),
                cost_basis REAL NOT NULL CHECK (cost_basis >= 0),
                entry_time REAL NOT NULL,
                last_update_time REAL NOT NULL,
                highest REAL,
                sleeve TEXT NOT NULL DEFAULT 'SPEC_ALPHA',
                target_account_pct REAL,
                last_plan_id TEXT,
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS spec_trades (
                id TEXT PRIMARY KEY,
                spec_position_id TEXT,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
                shares REAL NOT NULL CHECK (shares > 0),
                price REAL NOT NULL CHECK (price > 0),
                amount REAL NOT NULL,
                realized_profit REAL,
                time REAL NOT NULL,
                strategy_version TEXT NOT NULL,
                plan_id TEXT,
                reason TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_spec_trades_ticker ON spec_trades(ticker);
            CREATE INDEX IF NOT EXISTS idx_spec_trades_time ON spec_trades(time);

            CREATE TABLE IF NOT EXISTS spec_signals (
                id TEXT PRIMARY KEY,
                time REAL NOT NULL,
                plan_date TEXT NOT NULL,
                market_regime TEXT NOT NULL,
                account_equity REAL NOT NULL,
                spec_target_pct REAL NOT NULL,
                plan_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            );

            CREATE INDEX IF NOT EXISTS idx_spec_signals_time ON spec_signals(time);
            """
        )
        conn.commit()
    finally:
        conn.close()


def row_to_spec_position(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "ticker": row["ticker"],
        "spec_position_id": row["spec_position_id"],
        "strategy_version": row["strategy_version"],
        "shares": float(row["shares"]),
        "avg_entry_price": float(row["avg_entry_price"]),
        "cost_basis": float(row["cost_basis"]),
        "entry_time": float(row["entry_time"]),
        "last_update_time": float(row["last_update_time"]),
        "highest": None if row["highest"] is None else float(row["highest"]),
        "sleeve": row["sleeve"],
        "target_account_pct": None if row["target_account_pct"] is None else float(row["target_account_pct"]),
        "last_plan_id": row["last_plan_id"],
        "notes": row["notes"],
    }


def load_spec_positions() -> Dict[str, Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM spec_positions ORDER BY ticker").fetchall()
        return {row["ticker"]: row_to_spec_position(row) for row in rows}
    finally:
        conn.close()


def load_spec_trades() -> List[Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM spec_trades ORDER BY time ASC, created_at ASC").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def load_latest_spec_plan() -> Optional[Dict[str, Any]]:
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT * FROM spec_signals WHERE status = 'ACTIVE' ORDER BY time DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["plan"] = json_loads_dict(data.get("plan_json"))
        return data
    finally:
        conn.close()


def save_spec_plan_signal(plan: Dict[str, Any]) -> str:
    plan_id = str(plan.get("plan_id") or uuid.uuid4().hex)
    plan = dict(plan)
    plan["plan_id"] = plan_id
    with db_tx() as conn:
        conn.execute("UPDATE spec_signals SET status = 'SUPERSEDED' WHERE status = 'ACTIVE'")
        conn.execute(
            """
            INSERT INTO spec_signals(
                id, time, plan_date, market_regime, account_equity,
                spec_target_pct, plan_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
            """,
            (
                plan_id,
                now_ts(),
                ny_date_str(),
                str(plan.get("market", "UNKNOWN")),
                float(plan.get("account_equity", 0) or 0),
                float(plan.get("target_spec_account_pct", 0) or 0),
                json_dumps(plan),
            ),
        )
    return plan_id


def spec_alpha_market_filter_ok() -> bool:
    if not SPEC_ALPHA_REQUIRE_SPY_ABOVE_MA200:
        return True
    try:
        df = get_historical("SPY", limit=240)
        if df is None or df.empty or len(df) < 210:
            return False
        price = float(df["Close"].iloc[-1])
        ma200 = float(df["Close"].rolling(200).mean().iloc[-1])
        return price > ma200
    except Exception as exc:
        print(f"[SPEC MARKET FILTER ERROR] {exc}")
        return False


def spec_alpha_score_ticker(ticker: str) -> Optional[Dict[str, Any]]:
    try:
        df = get_historical(ticker, limit=280)
        if df is None or df.empty or len(df) < 210:
            return None
        close = df["Close"]
        volume = df["Volume"]
        price = float(close.iloc[-1])
        if price < SPEC_ALPHA_MIN_PRICE:
            return None
        ma200 = float(close.rolling(200).mean().iloc[-1])
        if SPEC_ALPHA_REQUIRE_STOCK_ABOVE_MA200 and price <= ma200:
            return None
        roc21 = pct_change_last(df, 21)
        roc63 = pct_change_last(df, 63)
        roc126 = pct_change_last(df, 126)
        vol63 = realized_vol_last(df, 63)
        avg_dv = float((close * volume).rolling(20).mean().iloc[-1])
        if roc21 is None or roc63 is None or roc126 is None or vol63 is None:
            return None
        if avg_dv < SPEC_ALPHA_MIN_AVG_DOLLAR_VOLUME:
            return None
        if SPEC_ALPHA_SCORE_MODE == "mom20":
            score = (0.58 * roc21) + (0.30 * roc63) + (0.12 * roc126) - (0.18 * vol63)
        else:
            score = (0.58 * roc63) + (0.27 * roc21) + (0.15 * roc126) - (0.20 * vol63)
        sector = SPEC_ALPHA_SECTOR_MAP.get(ticker, "Unknown")
        bucket = SPEC_ALPHA_BUCKET_MAP.get(ticker, "UNKNOWN")
        inv_vol = 1.0 / max(float(vol63), 0.04)
        weight_score = inv_vol * max(0.0001, score + 0.20)
        return {
            "ticker": ticker,
            "sector": sector,
            "bucket": bucket,
            "price": round(price, 2),
            "ma200": round(ma200, 2),
            "roc_1m_pct": round(roc21 * 100, 2),
            "roc_3m_pct": round(roc63 * 100, 2),
            "roc_6m_pct": round(roc126 * 100, 2),
            "vol_3m_pct": round(vol63 * 100, 2),
            "avg_dollar_volume": round(avg_dv, 2),
            "score": round(float(score), 6),
            "weight_score": round(float(weight_score), 6),
            "is_crypto": ticker in SPEC_ALPHA_CRYPTO_TICKERS,
        }
    except Exception as exc:
        print(f"[SPEC SCORE ERROR] {ticker}: {exc}")
        return None


def select_spec_alpha_assets(scored: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    sector_counts: Dict[str, int] = {}
    crypto_count = 0
    for item in scored:
        sector = str(item.get("sector", "Unknown"))
        is_crypto = bool(item.get("is_crypto"))
        if is_crypto and crypto_count >= SPEC_ALPHA_MAX_CRYPTO_NAMES:
            continue
        if sector_counts.get(sector, 0) >= SPEC_ALPHA_MAX_PER_SECTOR:
            continue
        selected.append(item)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if is_crypto:
            crypto_count += 1
        if len(selected) >= SPEC_ALPHA_TOP_N:
            break
    return selected


def assign_spec_alpha_weights(top: List[Dict[str, Any]], spec_account_pct: float) -> List[Dict[str, Any]]:
    if not top:
        return []
    raw = [max(0.0001, float(x.get("weight_score", 0.0001) or 0.0001)) for x in top]
    total = sum(raw)
    weights = [x / total for x in raw] if total > 0 else [1.0 / len(top)] * len(top)
    cap = clamp_float(SPEC_ALPHA_MAX_SINGLE_ASSET_PCT, 0.05, 0.80)
    floor = clamp_float(SPEC_ALPHA_MIN_SINGLE_ASSET_PCT, 0.00, cap)
    weights = [min(cap, max(0.0, w)) for w in weights]
    total = sum(weights)
    if total > 0:
        weights = [w / total for w in weights]
    if floor > 0 and floor * len(weights) <= 0.90:
        weights = [max(floor, w) for w in weights]
        total = sum(weights)
        if total > 0:
            weights = [w / total for w in weights]
    enriched = []
    for item, sleeve_weight in zip(top, weights):
        row = dict(item)
        row["target_spec_pct"] = round(sleeve_weight * 100, 2)
        row["target_account_pct"] = round(sleeve_weight * spec_account_pct * 100, 2)
        enriched.append(row)
    return enriched


def spec_position_market_value_details(prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    positions = load_spec_positions()
    tickers = list(positions.keys())
    prices = prices or get_prices_batch(tickers)
    rows: List[Dict[str, Any]] = []
    total_value = 0.0
    total_cost = 0.0
    total_unrealized = 0.0
    for ticker, pos in positions.items():
        mark = float(prices.get(ticker, pos.get("avg_entry_price", 0)) or pos.get("avg_entry_price", 0))
        shares = float(pos.get("shares", 0) or 0)
        value = shares * mark
        cost = float(pos.get("cost_basis", 0) or 0)
        unrealized = value - cost
        total_value += value
        total_cost += cost
        total_unrealized += unrealized
        rows.append({
            **pos,
            "mark_price": round(mark, 4),
            "market_value": round(value, 2),
            "unrealized_profit": round(unrealized, 2),
            "unrealized_pct": None if cost <= 0 else round((unrealized / cost) * 100, 2),
        })
    realized = sum(float(t.get("realized_profit") or 0.0) for t in load_spec_trades() if str(t.get("side")).upper() == "SELL")
    return {"positions": positions, "rows": rows, "value": round(total_value, 2), "cost_basis": round(total_cost, 2), "unrealized_profit": round(total_unrealized, 2), "realized_profit": round(realized, 2), "total_profit": round(realized + total_unrealized, 2)}


def compute_spec_alpha_plan() -> Dict[str, Any]:
    refresh_portfolio()
    allocation = dynamic_portfolio_allocation_targets()
    current_regime = str(allocation.get("market", market_condition()))
    risk = allocation.get("risk_guard", {}) or {}
    market_ok = spec_alpha_market_filter_ok()
    snapshot = compute_equity_snapshot_data()
    account_equity = float(snapshot.get("equity", 0) or 0)
    spec_account_pct = 0.0
    if SPEC_ALPHA_ENABLED and market_ok and not risk.get("hard_active"):
        spec_account_pct = float(allocation.get("spec_alpha_pct", SPEC_ALPHA_ACCOUNT_ALLOC_PCT * 100) or 0.0) / 100.0
    scored: List[Dict[str, Any]] = []
    if spec_account_pct > 0:
        for idx, ticker in enumerate(SPEC_ALPHA_UNIVERSE, start=1):
            item = spec_alpha_score_ticker(ticker)
            if item is not None:
                scored.append(item)
            if SPEC_ALPHA_SCORE_SLEEP_SEC > 0 and idx % 20 == 0:
                time.sleep(SPEC_ALPHA_SCORE_SLEEP_SEC)
    scored = sorted(scored, key=lambda x: float(x.get("score", -999)), reverse=True)
    selected = select_spec_alpha_assets(scored)
    top = assign_spec_alpha_weights(selected, spec_account_pct)
    sleeve_value = account_equity * spec_account_pct
    spec_details = spec_position_market_value_details() if SPEC_ALPHA_LEDGER_ENABLED else {"rows": [], "value": 0.0}
    current_rows = {str(row.get("ticker", "")).upper(): row for row in spec_details.get("rows", [])}
    selected_tickers = {str(item.get("ticker", "")).upper() for item in top}
    actions: List[Dict[str, Any]] = []
    for rank, item in enumerate(top, start=1):
        ticker = str(item["ticker"]).upper()
        target_value = account_equity * (float(item.get("target_account_pct", 0) or 0) / 100.0)
        current_value = float(current_rows.get(ticker, {}).get("market_value", 0.0) or 0.0)
        drift = target_value - current_value
        threshold = max(SPEC_ALPHA_ACTION_DOLLAR_THRESHOLD, account_equity * SPEC_ALPHA_REBALANCE_DRIFT_THRESHOLD_PCT)
        if current_value <= 0 and target_value >= SPEC_ALPHA_ACTION_DOLLAR_THRESHOLD:
            action = "BUY"
        elif drift >= threshold:
            action = "ADD"
        elif drift <= -threshold:
            action = "TRIM"
        else:
            action = "HOLD"
        actions.append({"rank": rank, "ticker": ticker, "action": action, "sector": item.get("sector"), "bucket": item.get("bucket"), "score": item.get("score"), "price": item.get("price"), "target_account_pct": item.get("target_account_pct"), "target_spec_pct": item.get("target_spec_pct"), "target_value": round(target_value, 2), "current_value": round(current_value, 2), "suggested_dollars": round(abs(drift), 2), "drift_dollars": round(drift, 2), "drift_pct_account": 0.0 if account_equity <= 0 else round((drift / account_equity) * 100, 2), "roc_1m_pct": item.get("roc_1m_pct"), "roc_3m_pct": item.get("roc_3m_pct"), "roc_6m_pct": item.get("roc_6m_pct"), "vol_3m_pct": item.get("vol_3m_pct"), "is_crypto": item.get("is_crypto")})
    scored_map = {str(item.get("ticker", "")).upper(): item for item in scored}
    for ticker, row in current_rows.items():
        if ticker in selected_tickers:
            continue
        score_item = scored_map.get(ticker)
        if not market_ok:
            reason = "SPY/market filter failed; monthly SPEC_ALPHA rotation should move to cash."
        elif spec_account_pct <= 0:
            reason = "SPEC_ALPHA allocation is currently zero by risk/allocation guard."
        elif score_item is None:
            reason = "Lost MA200/trend/liquidity qualification."
        else:
            reason = "Dropped out of selected monthly SPEC_ALPHA top list."
        current_value = float(row.get("market_value", 0.0) or 0.0)
        actions.append({"rank": None, "ticker": ticker, "action": "SELL", "sector": SPEC_ALPHA_SECTOR_MAP.get(ticker, "Unknown"), "bucket": SPEC_ALPHA_BUCKET_MAP.get(ticker, "UNKNOWN"), "score": None if score_item is None else score_item.get("score"), "price": row.get("mark_price"), "target_account_pct": 0.0, "target_spec_pct": 0.0, "target_value": 0.0, "current_value": round(current_value, 2), "suggested_dollars": round(current_value, 2), "drift_dollars": round(-current_value, 2), "drift_pct_account": None if account_equity <= 0 else round((-current_value / account_equity) * 100, 2), "reason": reason})
    actionable = [a for a in actions if str(a.get("action")).upper() in {"BUY", "ADD", "TRIM", "SELL"}]
    return {"plan_id": uuid.uuid4().hex, "strategy_version": "spec_alpha_v3_7_monthly_momentum", "private_only": False, "public_allowed": True, "ny_time": ny_now().strftime("%Y-%m-%d %H:%M %Z"), "market": current_regime, "market_ok": market_ok, "score_mode": SPEC_ALPHA_SCORE_MODE, "top_n": SPEC_ALPHA_TOP_N, "universe_size": len(SPEC_ALPHA_UNIVERSE), "scored_count": len(scored), "target_spec_account_pct": round(spec_account_pct * 100, 2), "target_spec_value": round(sleeve_value, 2), "current_spec_value": round(float(spec_details.get("value", 0.0) or 0.0), 2), "current_spec_cost_basis": round(float(spec_details.get("cost_basis", 0.0) or 0.0), 2), "current_spec_unrealized_profit": round(float(spec_details.get("unrealized_profit", 0.0) or 0.0), 2), "account_equity": round(account_equity, 2), "allocation": allocation, "risk_guard": risk, "top": top, "actions": actions, "actionable": actionable, "all_scored": scored[:100]}


def current_spec_plan_for_validation() -> Dict[str, Any]:
    latest = load_latest_spec_plan()
    if latest is not None:
        try:
            age_days = (now_ts() - float(latest.get("time", 0))) / 86400
            plan = latest.get("plan") or {}
            if age_days <= SPEC_ALPHA_PLAN_VALID_DAYS and plan:
                return plan
        except Exception:
            pass
    plan = compute_spec_alpha_plan()
    save_spec_plan_signal(plan)
    return plan


def latest_spec_plan_action_map(plan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(item.get("ticker", "")).upper(): item for item in plan.get("actions", []) if item.get("ticker")}


def spec_target_for_ticker(plan: Dict[str, Any], ticker: str) -> Optional[Dict[str, Any]]:
    ticker = ticker.upper()
    for item in plan.get("top", []) or []:
        if str(item.get("ticker", "")).upper() == ticker:
            return item
    return None


def validate_spec_price_against_quote(ticker: str, price: float) -> Tuple[bool, str, Optional[float]]:
    if not SPEC_ALPHA_REQUIRE_LIVE_QUOTE:
        return True, "Quote check disabled", None
    quotes = get_prices_batch([ticker])
    quote = quotes.get(ticker)
    if quote is None or quote <= 0:
        return False, "Live quote unavailable for SPEC_ALPHA trade.", None
    deviation = abs(price - quote) / quote
    if deviation > SPEC_ALPHA_QUOTE_DEVIATION_LIMIT:
        return False, f"SPEC_ALPHA trade rejected: price too far from live quote.\nLive quote: {round(quote, 2)}\nYour price: {round(price, 2)}\nMax deviation: {round(SPEC_ALPHA_QUOTE_DEVIATION_LIMIT * 100, 2)}%", quote
    return True, "OK", quote


def record_spec_buy(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
    ticker = normalize_ticker(ticker) or ""
    if not ticker:
        return False, "Invalid ticker"
    if not SPEC_ALPHA_LEDGER_ENABLED:
        return False, "SPEC_ALPHA ledger is disabled."
    if ticker not in SPEC_ALPHA_UNIVERSE:
        return False, f"{ticker} is not in the SPEC_ALPHA universe."
    if shares <= 0 or not math.isfinite(shares):
        return False, "SPEC_ALPHA shares must be positive and finite."
    if (not SPEC_ALPHA_ALLOW_FRACTIONAL_SHARES) and abs(shares - round(shares)) > 1e-9:
        return False, "Fractional SPEC_ALPHA shares are disabled."
    if not is_finite_positive(price):
        return False, "SPEC_ALPHA price must be positive and finite."
    amount = shares * price
    if amount < SPEC_ALPHA_MIN_TRADE_DOLLARS:
        return False, f"SPEC_ALPHA trade amount is below minimum {format_money(SPEC_ALPHA_MIN_TRADE_DOLLARS)}."
    plan = current_spec_plan_for_validation()
    target = spec_target_for_ticker(plan, ticker)
    action = latest_spec_plan_action_map(plan).get(ticker)
    if SPEC_ALPHA_REQUIRE_ACTIVE_PLAN_FOR_BUY:
        if target is None:
            allowed = ", ".join(str(x.get("ticker")) for x in plan.get("top", [])[:SPEC_ALPHA_TOP_N])
            return False, f"SPEC_ALPHA buy rejected: {ticker} is not in active spec plan. Current top: {allowed or 'none'}"
        if action and str(action.get("action", "")).upper() in {"TRIM", "SELL", "AVOID"}:
            return False, f"SPEC_ALPHA buy rejected: current plan action for {ticker} is {action.get('action')}."
    ok, msg, quote = validate_spec_price_against_quote(ticker, price)
    if not ok:
        return False, msg
    with db_tx() as conn:
        cash = get_cash(conn)
        if amount > cash:
            mark_update_processed_tx(conn, update_id, "rejected_spec_insufficient_cash")
            return False, "Not enough cash for SPEC_ALPHA buy."
        row = conn.execute("SELECT * FROM spec_positions WHERE ticker = ?", (ticker,)).fetchone()
        now = now_ts()
        target_pct = None if target is None else float(target.get("target_account_pct", 0) or 0)
        plan_id = str(plan.get("plan_id"))
        if row is None:
            spec_position_id = f"SPEC_{ticker}_{int(now)}_{uuid.uuid4().hex[:8]}"
            conn.execute("""
                INSERT INTO spec_positions(ticker, spec_position_id, strategy_version, shares, avg_entry_price, cost_basis, entry_time, last_update_time, highest, sleeve, target_account_pct, last_plan_id, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'SPEC_ALPHA', ?, ?, '')
            """, (ticker, spec_position_id, "spec_alpha_v3_7_monthly_momentum", round(shares, 8), round(price, 6), round(amount, 6), now, now, round(price, 6), target_pct, plan_id))
        else:
            pos = row_to_spec_position(row)
            spec_position_id = pos["spec_position_id"]
            old_shares = float(pos["shares"])
            old_cost = float(pos["cost_basis"])
            new_shares = old_shares + shares
            new_cost = old_cost + amount
            avg_price = new_cost / new_shares
            highest = max(float(pos.get("highest") or price), price)
            conn.execute("""
                UPDATE spec_positions SET shares = ?, avg_entry_price = ?, cost_basis = ?, last_update_time = ?, highest = ?, target_account_pct = ?, last_plan_id = ?, strategy_version = ? WHERE ticker = ?
            """, (round(new_shares, 8), round(avg_price, 6), round(new_cost, 6), now, round(highest, 6), target_pct, plan_id, "spec_alpha_v3_7_monthly_momentum", ticker))
        conn.execute("""
            INSERT INTO spec_trades(id, spec_position_id, ticker, side, shares, price, amount, realized_profit, time, strategy_version, plan_id, reason, created_at)
            VALUES (?, ?, ?, 'BUY', ?, ?, ?, NULL, ?, ?, ?, ?, ?)
        """, (uuid.uuid4().hex, spec_position_id, ticker, round(shares, 8), round(price, 6), round(amount, 6), now, "spec_alpha_v3_7_monthly_momentum", plan_id, "spec_plan_buy", now))
        set_cash_tx(conn, cash - amount)
        mark_update_processed_tx(conn, update_id, "processed_spec_buy")
    refresh_portfolio()
    audit("SPEC_BUY", f"{ticker} shares={shares} price={price} amount={amount}")
    return True, f"⚡ SPEC_ALPHA BUY RECORDED {ticker}\n\n📦 Shares: {format_core_shares(shares)}\n💵 Price: {round(price, 2)}\n💰 Amount: {format_money(amount)}\n🎯 Plan action: {None if action is None else action.get('action')}\n📐 Target account weight: {None if target is None else target.get('target_account_pct')}%\n💵 Cash left: {format_money(portfolio['cash'])}"


def record_spec_sell(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
    ticker = normalize_ticker(ticker) or ""
    if not ticker:
        return False, "Invalid ticker"
    if not SPEC_ALPHA_LEDGER_ENABLED:
        return False, "SPEC_ALPHA ledger is disabled."
    if shares <= 0 or not math.isfinite(shares):
        return False, "SPEC_ALPHA shares must be positive and finite."
    if not is_finite_positive(price):
        return False, "SPEC_ALPHA price must be positive and finite."
    ok, msg, quote = validate_spec_price_against_quote(ticker, price)
    if not ok:
        return False, msg
    plan = current_spec_plan_for_validation()
    action = latest_spec_plan_action_map(plan).get(ticker)
    with db_tx() as conn:
        row = conn.execute("SELECT * FROM spec_positions WHERE ticker = ?", (ticker,)).fetchone()
        if row is None:
            mark_update_processed_tx(conn, update_id, "rejected_spec_no_position")
            return False, "No SPEC_ALPHA position to sell."
        pos = row_to_spec_position(row)
        current_shares = float(pos["shares"])
        if shares - current_shares > CORE_POSITION_EPSILON:
            mark_update_processed_tx(conn, update_id, "rejected_spec_too_many_shares")
            return False, f"You only have {format_core_shares(current_shares)} SPEC_ALPHA shares of {ticker}."
        shares = min(shares, current_shares)
        avg = float(pos["avg_entry_price"])
        proceeds = shares * price
        realized_profit = (price - avg) * shares
        remaining = current_shares - shares
        now = now_ts()
        plan_id = str(plan.get("plan_id"))
        conn.execute("""
            INSERT INTO spec_trades(id, spec_position_id, ticker, side, shares, price, amount, realized_profit, time, strategy_version, plan_id, reason, created_at)
            VALUES (?, ?, ?, 'SELL', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (uuid.uuid4().hex, pos["spec_position_id"], ticker, round(shares, 8), round(price, 6), round(proceeds, 6), round(realized_profit, 6), now, "spec_alpha_v3_7_monthly_momentum", plan_id, "spec_plan_sell", now))
        if remaining <= CORE_POSITION_EPSILON:
            conn.execute("DELETE FROM spec_positions WHERE ticker = ?", (ticker,))
        else:
            new_cost = avg * remaining
            target = spec_target_for_ticker(plan, ticker)
            target_pct = None if target is None else float(target.get("target_account_pct", 0) or 0)
            conn.execute("UPDATE spec_positions SET shares = ?, cost_basis = ?, last_update_time = ?, target_account_pct = ?, last_plan_id = ? WHERE ticker = ?", (round(remaining, 8), round(new_cost, 6), now, target_pct, plan_id, ticker))
        cash = get_cash(conn)
        set_cash_tx(conn, cash + proceeds)
        mark_update_processed_tx(conn, update_id, "processed_spec_sell")
    refresh_portfolio()
    audit("SPEC_SELL", f"{ticker} shares={shares} price={price} proceeds={proceeds} profit={realized_profit}")
    return True, f"⚡ SPEC_ALPHA SELL RECORDED {ticker}\n\n📦 Shares: {format_core_shares(shares)}\n💵 Price: {round(price, 2)}\n💰 Proceeds: {format_money(proceeds)}\n📊 Realized SPEC_ALPHA P/L: {format_money(realized_profit)} ({format_pct((price - avg) / avg * 100 if avg > 0 else None)})\n🎯 Plan action: {None if action is None else action.get('action')}\n💵 Cash now: {format_money(portfolio['cash'])}"


def dynamic_portfolio_allocation_targets() -> Dict[str, Any]:
    """
    V3.7 aggressive 45/15/40 allocation map.

    User-selected normal target mix:
      - 45% core wealth rotation
      - 15% tactical VCP/bear sleeve
      - 40% SPEC_ALPHA medium/weak monthly momentum rotation

    This is the final aggressive-growth allocation: SPEC_ALPHA is the main
    return engine, core wealth remains the stabilizer, and VCP/bear remains
    the tactical signal sleeve. Hard drawdown mode still pauses new
    tactical/spec exposure and moves the difference to cash.
    """
    base = _old_dynamic_portfolio_allocation_targets()
    risk = base.get("risk_guard", {}) or {}
    market = str(base.get("market", "UNCERTAIN"))
    bear_score = int(base.get("bear_score", 0) or 0)

    core_pct = round(WEALTH_CORE_ACCOUNT_ALLOC_PCT * 100, 2)
    spec_full_pct = round(SPEC_ALPHA_ACCOUNT_ALLOC_PCT * 100, 2) if SPEC_ALPHA_ENABLED else 0.0
    tactical_total_pct = 15.0

    if risk.get("hard_active"):
        spec_pct = 0.0
        long_vcp = 0.0
        bear = 0.0
        cash = max(0.0, 100.0 - core_pct)
    elif market == "BEAR":
        # SPEC_ALPHA uses a SPY/MA200 risk-on filter, so in bear regimes its
        # target moves to cash while the tactical bucket becomes bear sleeve.
        spec_pct = 0.0
        long_vcp = 0.0
        bear = tactical_total_pct if BEAR_SLEEVE_ENABLED else 0.0
        cash = max(0.0, 100.0 - core_pct - bear)
    elif market == "UNCERTAIN":
        spec_pct = spec_full_pct
        if BEAR_SLEEVE_ENABLED and bear_score >= BEAR_EXIT_SCORE:
            long_vcp = 10.0
            bear = 5.0
        else:
            long_vcp = 15.0
            bear = 0.0
        cash = max(0.0, 100.0 - core_pct - spec_pct - long_vcp - bear)
    else:
        spec_pct = spec_full_pct
        long_vcp = tactical_total_pct
        bear = 0.0
        cash = max(0.0, 100.0 - core_pct - spec_pct - long_vcp - bear)

    if risk.get("soft_active") and not risk.get("hard_active"):
        # Soft drawdown mode keeps core intact but halves aggressive sleeves.
        reduced_spec = spec_pct * 0.5
        reduced_long = long_vcp * 0.5
        reduced_bear = bear * 0.75
        cash += (spec_pct - reduced_spec) + (long_vcp - reduced_long) + (bear - reduced_bear)
        spec_pct, long_vcp, bear = reduced_spec, reduced_long, reduced_bear

    base["strategy_version"] = "v3_7_aggressive_45_15_40_dynamic_allocation"
    base["core_wealth_pct"] = round(core_pct, 2)
    base["spec_alpha_pct"] = round(spec_pct, 2)
    base["long_vcp_tactical_pct"] = round(long_vcp, 2)
    base["bear_inverse_tactical_pct"] = round(bear, 2)
    base["cash_reserve_pct"] = round(cash, 2)
    return base


def compute_equity_snapshot_data() -> Dict[str, float]:
    refresh_portfolio()
    swing_positions = portfolio["positions"]
    core_positions = load_core_positions() if CORE_LEDGER_ENABLED else {}
    spec_positions = load_spec_positions() if SPEC_ALPHA_LEDGER_ENABLED else {}
    all_tickers = list(dict.fromkeys(list(swing_positions.keys()) + list(core_positions.keys()) + list(spec_positions.keys())))
    prices = get_prices_batch(all_tickers)
    swing_value = 0.0
    for ticker, pos in swing_positions.items():
        price = prices.get(ticker, pos["price"])
        swing_value += float(price) * int(pos["shares"])
    core_value = 0.0; core_cost = 0.0
    for ticker, pos in core_positions.items():
        price = prices.get(ticker, pos.get("avg_entry_price", 0))
        core_value += float(price) * float(pos["shares"])
        core_cost += float(pos.get("cost_basis", 0) or 0)
    spec_value = 0.0; spec_cost = 0.0
    for ticker, pos in spec_positions.items():
        price = prices.get(ticker, pos.get("avg_entry_price", 0))
        spec_value += float(price) * float(pos["shares"])
        spec_cost += float(pos.get("cost_basis", 0) or 0)
    positions_value = swing_value + core_value + spec_value
    equity = float(portfolio["cash"]) + positions_value
    return {"cash": round(float(portfolio["cash"]), 2), "positions_value": round(positions_value, 2), "swing_positions_value": round(swing_value, 2), "core_positions_value": round(core_value, 2), "core_cost_basis": round(core_cost, 2), "core_unrealized_profit": round(core_value - core_cost, 2), "spec_positions_value": round(spec_value, 2), "spec_cost_basis": round(spec_cost, 2), "spec_unrealized_profit": round(spec_value - spec_cost, 2), "equity": round(equity, 2)}


def realized_performance_all_time() -> Dict[str, Any]:
    trades = load_trades()
    swing_profit = round(sum(float(t.get("profit", 0)) for t in trades), 2)
    core_trades = load_core_trades() if CORE_LEDGER_ENABLED else []
    core_profit = round(sum(float(t.get("realized_profit") or 0.0) for t in core_trades if str(t.get("side")).upper() == "SELL"), 2)
    spec_trades = load_spec_trades() if SPEC_ALPHA_LEDGER_ENABLED else []
    spec_profit = round(sum(float(t.get("realized_profit") or 0.0) for t in spec_trades if str(t.get("side")).upper() == "SELL"), 2)
    total_profit = round(swing_profit + core_profit + spec_profit, 2)
    base_capital = get_performance_base_capital()
    pct_val = None if base_capital <= 0 else (total_profit / base_capital) * 100
    return {"profit": total_profit, "pct": pct_val, "base_capital": round(base_capital, 2), "swing_realized_profit": swing_profit, "core_realized_profit": core_profit, "spec_realized_profit": spec_profit, "trade_records": len(trades) + len(core_trades) + len(spec_trades), "swing_trade_records": len(trades), "core_trade_records": len(core_trades), "spec_trade_records": len(spec_trades)}


def sleeve_performance_summary() -> Dict[str, Any]:
    summary = _old_sleeve_performance_summary()
    rows = summary.get("rows", []) or []
    spec_trades = load_spec_trades() if SPEC_ALPHA_LEDGER_ENABLED else []
    spec_sells = [t for t in spec_trades if str(t.get("side")).upper() == "SELL"]
    spec_profit = round(sum(float(t.get("realized_profit") or 0.0) for t in spec_sells), 2)
    if spec_trades:
        rows.append({"sleeve": "SPEC_ALPHA_REALIZED", "trade_records": len(spec_trades), "profit": spec_profit, "win_rate_pct": None, "profit_factor": None, "avg_profit": round(spec_profit / len(spec_sells), 2) if spec_sells else 0.0})
    summary["rows"] = rows
    summary["spec_realized_profit"] = spec_profit
    summary["total_profit"] = round(float(summary.get("total_profit", 0) or 0) + spec_profit, 2)
    summary["trade_records"] = int(summary.get("trade_records", 0) or 0) + len(spec_trades)
    return summary


def format_portfolio_allocation_plan() -> str:
    plan = dynamic_portfolio_allocation_targets()
    risk = plan.get("risk_guard", {}) or {}
    return (
        "🏛️ INSTITUTIONAL ALLOCATION PLAN v3.7 AGGRESSIVE 45/15/40\n\n"
        "Private bot only. This is portfolio guidance, not an automatic trade.\n\n"
        f"🕒 NY time: {plan.get('ny_time')}\n"
        f"🌎 Market: {market_label(str(plan.get('market', 'UNKNOWN')))} ({plan.get('market_score')}/8)\n"
        f"🐻 Bear pressure score: {plan.get('bear_score')}/60\n"
        f"🛡️ Risk guard: {risk.get('recommended_action')}\n"
        f"📉 Current DD: {risk.get('drawdown_pct')}% from {format_money(float(risk.get('high_equity', 0) or 0))}\n\n"
        "Target account buckets:\n"
        f"🏦 Core wealth rotation: {plan.get('core_wealth_pct')}%\n"
        f"⚡ SPEC_ALPHA rotation: {plan.get('spec_alpha_pct')}%\n"
        f"🐂 Long VCP tactical: {plan.get('long_vcp_tactical_pct')}%\n"
        f"🐻 Bear inverse tactical: {plan.get('bear_inverse_tactical_pct')}%\n"
        f"💵 Cash reserve: {plan.get('cash_reserve_pct')}%\n\n"
        "Rules:\n"
        "• Core sleeve is long-term allocation.\n"
        "• SPEC_ALPHA is monthly medium/weak momentum rotation.\n"
        "• VCP/bear sleeves remain signal-driven tactical systems.\n"
        "• In hard drawdown mode, new entries pause and exits/management continue."
    )


def format_spec_alpha_plan(plan: Dict[str, Any]) -> str:
    actions = plan.get("actions", []) or []
    risk = plan.get("risk_guard", {}) or {}
    msg = (
        "⚡ SPEC_ALPHA MONTHLY ROTATION PLAN v3.7 AGGRESSIVE 45/15/40\n\n"
        "Private execution plan. Medium/weak momentum rotation. Execute in broker first, then record with specbuy/specsell.\n\n"
        f"🕒 NY time: {plan.get('ny_time')}\n"
        f"🌎 Market: {market_label(str(plan.get('market', 'UNKNOWN')))} | Market filter OK: {yes_no(bool(plan.get('market_ok')))}\n"
        f"🛡️ Risk guard: {risk.get('recommended_action')}\n"
        f"💼 Equity estimate: {format_money(float(plan.get('account_equity', 0) or 0))}\n"
        f"⚡ Target SPEC_ALPHA sleeve: {plan.get('target_spec_account_pct')}% = {format_money(float(plan.get('target_spec_value', 0) or 0))}\n"
        f"📦 Current SPEC value: {format_money(float(plan.get('current_spec_value', 0) or 0))}\n"
        f"📈 SPEC unrealized P/L: {format_money(float(plan.get('current_spec_unrealized_profit', 0) or 0))}\n"
        f"🧪 Universe/scored: {plan.get('universe_size')} / {plan.get('scored_count')}\n"
        f"🎚️ Mode: {plan.get('score_mode')} | Top N: {plan.get('top_n')}\n\n"
    )
    ranked = [a for a in actions if a.get("rank") is not None]
    exits = [a for a in actions if str(a.get("action")).upper() == "SELL"]
    if ranked:
        msg += "🎯 Ranked SPEC_ALPHA candidates — best to least attractive\n"
        for item in ranked[:SPEC_ALPHA_TOP_N]:
            action = str(item.get("action", "HOLD")).upper()
            verb = {"BUY": "🟢 BUY", "ADD": "🟢 ADD", "HOLD": "🟡 HOLD", "TRIM": "🟠 TRIM"}.get(action, action)
            msg += (
                f"{item.get('rank')}) {verb} {item['ticker']} ({item.get('sector', 'Unknown')})\n"
                f"   Target: {item.get('target_account_pct')}% acct / {format_money(float(item.get('target_value', 0) or 0))}\n"
                f"   Current: {format_money(float(item.get('current_value', 0) or 0))} | Action size: ~{format_money(float(item.get('suggested_dollars', 0) or 0))}\n"
                f"   Price: {item.get('price')} | 1m {format_pct(item.get('roc_1m_pct'))} | 3m {format_pct(item.get('roc_3m_pct'))} | 6m {format_pct(item.get('roc_6m_pct'))}\n"
                f"   Vol: {item.get('vol_3m_pct')}% | Score: {item.get('score')} | Bucket: {item.get('bucket')}\n"
            )
        msg += "\n"
    if exits:
        msg += "🔴 SPEC_ALPHA exit / rotation candidates\n"
        for item in exits:
            msg += f"SELL {item['ticker']} — current {format_money(float(item.get('current_value', 0) or 0))}\nReason: {item.get('reason', 'No longer selected')}\n"
        msg += "\n"
    msg += "How to execute after broker fill:\n• specbuy TICKER SHARES at PRICE\n• specsell TICKER SHARES at PRICE\n"
    return msg[:MAX_TELEGRAM_MESSAGE]


def format_public_core_plan(plan: Dict[str, Any]) -> str:
    actions = plan.get("actions", []) or []
    ranked = [a for a in actions if a.get("rank") is not None]
    exits = [a for a in actions if str(a.get("action")).upper() == "SELL"]
    msg = "🏛️ CORE WEALTH PLAN\n\nLong-term allocation model. No share counts. Use your own account size.\n\n"
    msg += f"🌎 Market: {market_label(str(plan.get('market', 'UNKNOWN')))}\n🎯 Target core sleeve: {plan.get('target_core_account_pct')}% of account\n\n"
    for item in ranked[:WEALTH_CORE_TOP_N]:
        action = str(item.get("action", "HOLD")).upper()
        verb = {"BUY": "🟢 BUY", "ADD": "🟢 ADD", "HOLD": "🟡 HOLD", "TRIM": "🟠 TRIM"}.get(action, action)
        msg += f"{item.get('rank')}) {verb} {item['ticker']}\nTarget: {item.get('target_account_pct')}% of account | Price: {item.get('price')}\n1m {format_pct(item.get('roc_1m_pct'))} | 3m {format_pct(item.get('roc_3m_pct'))} | 6m {format_pct(item.get('roc_6m_pct'))}\n\n"
    if exits:
        msg += "🔴 Rotation exits:\n"
        for item in exits[:10]:
            msg += f"SELL/REMOVE {item['ticker']} — {item.get('reason', 'No longer selected')}\n"
        msg += "\n"
    msg += public_signal_footer()
    return msg[:MAX_TELEGRAM_MESSAGE]


def format_public_spec_plan(plan: Dict[str, Any]) -> str:
    actions = plan.get("actions", []) or []
    ranked = [a for a in actions if a.get("rank") is not None]
    exits = [a for a in actions if str(a.get("action")).upper() == "SELL"]
    msg = "⚡ SPEC_ALPHA ROTATION PLAN\n\nMedium/weak monthly momentum sleeve. No share counts. Use your own account size.\n\n"
    msg += f"🌎 Market: {market_label(str(plan.get('market', 'UNKNOWN')))} | Market filter: {yes_no(bool(plan.get('market_ok')))}\n🎯 Target SPEC sleeve: {plan.get('target_spec_account_pct')}% of account\n🎚️ Mode: {plan.get('score_mode')} | Top {plan.get('top_n')}\n\n"
    for item in ranked[:SPEC_ALPHA_TOP_N]:
        action = str(item.get("action", "HOLD")).upper()
        verb = {"BUY": "🟢 BUY", "ADD": "🟢 ADD", "HOLD": "🟡 HOLD", "TRIM": "🟠 TRIM"}.get(action, action)
        msg += f"{item.get('rank')}) {verb} {item['ticker']} ({item.get('sector', 'Unknown')})\nTarget: {item.get('target_account_pct')}% of account | Price: {item.get('price')}\n1m {format_pct(item.get('roc_1m_pct'))} | 3m {format_pct(item.get('roc_3m_pct'))} | 6m {format_pct(item.get('roc_6m_pct'))}\nScore: {item.get('score')}\n\n"
    if exits:
        msg += "🔴 Rotation exits:\n"
        for item in exits[:10]:
            msg += f"SELL/REMOVE {item['ticker']} — {item.get('reason', 'No longer selected')}\n"
        msg += "\n"
    msg += public_signal_footer()
    return msg[:MAX_TELEGRAM_MESSAGE]


def maybe_send_spec_alpha_signal() -> None:
    if not SPEC_ALPHA_ENABLED:
        return
    current_ny = ny_now()
    minutes = current_ny.hour * 60 + current_ny.minute
    if is_market_weekday(current_ny) and minutes < SPEC_ALPHA_REVIEW_AFTER_CLOSE_MINUTE:
        return
    month_key = current_ny.strftime("%Y-%m")
    if get_meta("last_spec_alpha_month") == month_key:
        return
    last_raw = get_meta("last_spec_alpha_alert_ts")
    if last_raw:
        try:
            days_since = (now_ts() - float(last_raw)) / 86400
            if days_since < SPEC_ALPHA_ALERT_REPEAT_DAYS:
                return
        except ValueError:
            pass
    try:
        plan = compute_spec_alpha_plan()
        save_spec_plan_signal(plan)
        set_meta("last_spec_alpha_month", month_key)
        set_meta("last_spec_alpha_alert_ts", str(now_ts()))
        send(format_spec_alpha_plan(plan))
        if PUBLIC_SIGNAL_ENABLED and SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED:
            send_public_signal(format_public_spec_plan(plan))
        audit("SPEC_ALPHA_SIGNAL", f"month={month_key} top={[x.get('ticker') for x in plan.get('top', [])]}")
    except Exception as exc:
        logger.exception(f"[SPEC ALPHA SIGNAL ERROR] {exc}")
        print(f"[SPEC ALPHA SIGNAL ERROR] {exc}")


def maybe_send_wealth_core_signal() -> None:
    _old_maybe_send_wealth_core_signal()
    maybe_send_spec_alpha_signal()


def format_spec_portfolio_report() -> str:
    details = spec_position_market_value_details()
    rows = details.get("rows", []) or []
    snapshot = compute_equity_snapshot_data()
    msg = f"⚡ SPEC_ALPHA PORTFOLIO\n\n💵 Shared cash: {format_money(snapshot['cash'])}\n⚡ SPEC value: {format_money(float(details.get('value', 0) or 0))}\n📏 Cost basis: {format_money(float(details.get('cost_basis', 0) or 0))}\n📈 Unrealized P/L: {format_money(float(details.get('unrealized_profit', 0) or 0))}\n✅ Realized SPEC P/L: {format_money(float(details.get('realized_profit', 0) or 0))}\n💼 Total equity: {format_money(snapshot['equity'])}\n\n"
    if not rows:
        return msg + "No SPEC_ALPHA positions recorded yet. Use specplan, then specbuy after broker execution."
    for row in rows:
        msg += f"📦 {row['ticker']}\nShares: {format_core_shares(row['shares'])}\nAvg: {round(float(row['avg_entry_price']), 2)} | Now: {round(float(row['mark_price']), 2)}\nValue: {format_money(float(row['market_value']))}\nP/L: {format_money(float(row['unrealized_profit']))} ({format_pct(row.get('unrealized_pct'))})\nTarget account weight: {row.get('target_account_pct')}%\n\n"
    return msg[:MAX_TELEGRAM_MESSAGE]


def format_spec_pnl_report() -> str:
    details = spec_position_market_value_details()
    trades = load_spec_trades()
    buys = [t for t in trades if str(t.get("side")).upper() == "BUY"]
    sells = [t for t in trades if str(t.get("side")).upper() == "SELL"]
    return f"⚡ SPEC_ALPHA P/L\n\n⚡ SPEC value: {format_money(float(details.get('value', 0) or 0))}\n📏 Cost basis: {format_money(float(details.get('cost_basis', 0) or 0))}\n📈 Unrealized P/L: {format_money(float(details.get('unrealized_profit', 0) or 0))}\n✅ Realized P/L: {format_money(float(details.get('realized_profit', 0) or 0))}\n💰 Total SPEC P/L: {format_money(float(details.get('total_profit', 0) or 0))}\n\nBuy records: {len(buys)}\nSell records: {len(sells)}"


def format_spec_exposure_report() -> str:
    snapshot = compute_equity_snapshot_data()
    plan = compute_spec_alpha_plan()
    details = spec_position_market_value_details()
    equity = float(snapshot.get("equity", 0) or 0)
    actual_pct = 0.0 if equity <= 0 else (float(details.get("value", 0) or 0) / equity) * 100
    target_pct = float(plan.get("target_spec_account_pct", 0) or 0)
    return f"⚡ SPEC_ALPHA EXPOSURE\n\n💼 Total equity: {format_money(equity)}\n⚡ SPEC value: {format_money(float(details.get('value', 0) or 0))}\n🎯 Target SPEC: {round(target_pct, 2)}% of account\n📊 Actual SPEC: {round(actual_pct, 2)}% of account\n📐 Drift: {round(actual_pct - target_pct, 2)} percentage points\n\nUse specplan for ranked BUY/ADD/HOLD/TRIM/SELL actions."


def format_combined_portfolio_report() -> str:
    refresh_portfolio()
    cash = float(portfolio["cash"])
    swing_positions = portfolio["positions"]
    core_rows = core_position_market_value_details().get("rows", []) if CORE_LEDGER_ENABLED else []
    spec_rows = spec_position_market_value_details().get("rows", []) if SPEC_ALPHA_LEDGER_ENABLED else []
    snapshot = compute_equity_snapshot_data()
    if not swing_positions and not core_rows and not spec_rows:
        return f"📋 PORTFOLIO\n\n💵 Cash: {format_money(cash)}\n🏦 Total Equity: {format_money(snapshot['equity'])}\nNo open swing, core, or SPEC positions"
    prices = get_prices_batch(list(swing_positions.keys()))
    msg = f"📋 PORTFOLIO\n\n💵 Cash: {format_money(cash)}\n⚡ Swing value: {format_money(snapshot.get('swing_positions_value', 0))}\n🏛️ Core value: {format_money(snapshot.get('core_positions_value', 0))}\n⚡ SPEC value: {format_money(snapshot.get('spec_positions_value', 0))}\n🏦 Total equity: {format_money(snapshot['equity'])}\n\n"
    if swing_positions:
        msg += "⚡ SWING / TACTICAL POSITIONS\n\n"
        for ticker, pos in swing_positions.items():
            current_price = prices.get(ticker, pos["price"])
            entry = pos["price"]; shares = pos["shares"]
            pnl = (current_price - entry) * shares
            risk_per_share = pos.get("risk_per_share")
            r_now = None
            if isinstance(risk_per_share, (int, float)) and risk_per_share > 0:
                r_now = (current_price - entry) / risk_per_share
            msg += f"📦 {ticker}\nShares: {shares}\nEntry: {round(entry, 2)}\nNow: {round(current_price, 2)}\n🛡️ Stop: {round(pos['stop'], 2)}\n📈 High: {round(pos['highest'], 2)}\n🎯 R now: {None if r_now is None else round(r_now, 2)}\n💰 P/L: {format_money(pnl)}\n\n"
    if core_rows:
        msg += "🏛️ CORE WEALTH POSITIONS\n\n"
        for row in core_rows:
            msg += f"📦 {row['ticker']}\nShares: {format_core_shares(row['shares'])}\nAvg: {round(float(row['avg_entry_price']), 2)}\nNow: {round(float(row['mark_price']), 2)}\nValue: {format_money(float(row['market_value']))}\nP/L: {format_money(float(row['unrealized_profit']))} ({format_pct(row.get('unrealized_pct'))})\n\n"
    if spec_rows:
        msg += "⚡ SPEC_ALPHA POSITIONS\n\n"
        for row in spec_rows:
            msg += f"📦 {row['ticker']}\nShares: {format_core_shares(row['shares'])}\nAvg: {round(float(row['avg_entry_price']), 2)}\nNow: {round(float(row['mark_price']), 2)}\nValue: {format_money(float(row['market_value']))}\nP/L: {format_money(float(row['unrealized_profit']))} ({format_pct(row.get('unrealized_pct'))})\n\n"
    return msg[:MAX_TELEGRAM_MESSAGE]


def handle_command(text: str, update_id: Optional[int] = None) -> None:
    text_clean = (text or "").strip()
    text_lower = text_clean.lower()
    if text_lower in {"help", "/help"}:
        send(
            "Commands:\n"
            "pnl | equity | openrisk | winrate | expectancy | stats | duration | summary | portfolio | scanstatus | bearstatus | allocationplan | riskstatus | sleevestatus\n"
            "wealthplan | wealthstatus | corestatus | coreportfolio | corepnl | coreexposure\n"
            "specplan | specstatus | specportfolio | specpnl | specexposure\n"
            "setupstats | showtrades | showsignals | resetsignals | resetscan | forcescan | download_trades\n"
            "testchannel | postchannelterms\n"
            "download_state | download_portfolio | download_signals | download_withdrawals\n"
            "withdrawinit | withdrawplan | withdrawdone AMOUNT | showwithdrawals\n"
            "resetall  (then resetall CONFIRM-LIVE)\n"
            "setcash AMOUNT\n"
            "voidbuy TICKER\n"
            "corebuy TICKER SHARES at PRICE | coresell TICKER SHARES at PRICE\n"
            "specbuy TICKER SHARES at PRICE | specsell TICKER SHARES at PRICE\n"
            "editbuy TICKER PRICE | editsell TICKER PRICE\n"
            "bought TICKER SHARES at PRICE | sold TICKER SHARES at PRICE"
        )
        return
    if text_lower == "equity":
        snapshot = compute_equity_snapshot_data()
        send(f"💼 ACCOUNT EQUITY\n\n💵 Cash: {format_money(snapshot['cash'])}\n⚡ Swing positions: {format_money(snapshot.get('swing_positions_value', 0))}\n🏛️ Core wealth positions: {format_money(snapshot.get('core_positions_value', 0))}\n⚡ SPEC_ALPHA positions: {format_money(snapshot.get('spec_positions_value', 0))}\n📦 Total positions: {format_money(snapshot['positions_value'])}\n🏦 Total Equity: {format_money(snapshot['equity'])}")
        return
    if text_lower == "allocationplan":
        send(format_portfolio_allocation_plan())
        return
    if text_lower == "wealthplan":
        plan = compute_wealth_core_plan()
        save_core_plan_signal(plan)
        send(format_wealth_core_plan(plan))
        if PUBLIC_SIGNAL_ENABLED and CORE_PUBLIC_SIGNAL_ENABLED:
            ok, info = send_public_signal(format_public_core_plan(plan))
            if not ok:
                send(f"⚠️ Core public plan failed:\n{info}")
        return
    if text_lower == "specplan":
        send("⚡ SPEC_ALPHA plan started. This can take several minutes because it scores the broad medium/weak universe.")
        plan = compute_spec_alpha_plan()
        save_spec_plan_signal(plan)
        send(format_spec_alpha_plan(plan))
        if PUBLIC_SIGNAL_ENABLED and SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED:
            ok, info = send_public_signal(format_public_spec_plan(plan))
            if not ok:
                send(f"⚠️ SPEC_ALPHA public plan failed:\n{info}")
        return
    if text_lower in {"specstatus", "specledger"}:
        latest = load_latest_spec_plan()
        send(f"⚡ SPEC_ALPHA STATUS v3.7\n\nEnabled: {yes_no(SPEC_ALPHA_ENABLED)}\nLedger enabled: {yes_no(SPEC_ALPHA_LEDGER_ENABLED)}\nPublic enabled: {yes_no(SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED)}\nTarget allocation: {round(SPEC_ALPHA_ACCOUNT_ALLOC_PCT * 100, 2)}%\nMode: {SPEC_ALPHA_SCORE_MODE} | Top N: {SPEC_ALPHA_TOP_N}\nUniverse size: {len(SPEC_ALPHA_UNIVERSE)}\nOpen SPEC positions: {len(load_spec_positions()) if SPEC_ALPHA_LEDGER_ENABLED else 0}\nLatest active plan: {None if latest is None else latest.get('plan_date')}\n\nCommands:\nspecplan\nspecbuy TICKER SHARES at PRICE\nspecsell TICKER SHARES at PRICE\nspecportfolio | specpnl | specexposure")
        return
    if text_lower == "specportfolio":
        send(format_spec_portfolio_report())
        return
    if text_lower == "specpnl":
        send(format_spec_pnl_report())
        return
    if text_lower == "specexposure":
        send(format_spec_exposure_report())
        return
    spec_trade_cmd = re.fullmatch(r"(?i)\s*(specbuy|specsell)\s+([A-Z0-9.\-]{1,15})\s+([0-9]+(?:\.[0-9]+)?)\s+(?:at|@)\s+([0-9]+(?:\.[0-9]+)?)\s*", text_clean)
    if spec_trade_cmd:
        action = spec_trade_cmd.group(1).lower()
        ticker = normalize_ticker(spec_trade_cmd.group(2))
        shares = float(spec_trade_cmd.group(3))
        price = float(spec_trade_cmd.group(4))
        if not ticker:
            send("Invalid ticker")
            return
        if action == "specbuy":
            ok, msg = record_spec_buy(ticker, shares, price, update_id=update_id)
            send(msg if ok else "❌ ERROR: " + msg)
            return
        if action == "specsell":
            ok, msg = record_spec_sell(ticker, shares, price, update_id=update_id)
            send(msg if ok else "❌ ERROR: " + msg)
            return
    return _old_handle_command(text, update_id=update_id)



# -----------------------------------------------------------------------------
# V3.7 EXPORT / RESET HARDENING
# -----------------------------------------------------------------------------
# These overrides make the SPEC_ALPHA ledger first-class for backups, downloads,
# and resetall. They intentionally wrap the existing v3.6 export/reset behavior
# instead of changing trading logic.

_V37_TABLE_EXPORT_ALLOWED = {
    "positions",
    "trades",
    "signals",
    "equity_snapshots",
    "withdrawals",
    "cooldowns",
    "breakout_memory",
    "core_positions",
    "core_trades",
    "core_signals",
    "spec_positions",
    "spec_trades",
    "spec_signals",
}


def table_rows(table: str) -> List[Dict[str, Any]]:
    if table not in _V37_TABLE_EXPORT_ALLOWED:
        raise ValueError("Table export not allowed")

    conn = db_connect()
    try:
        try:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in rows]
    finally:
        conn.close()


def export_state_bundle(prefix: str = "bot_state_export") -> str:
    """
    V3.7 state export.

    Includes swing positions/trades, core positions/trades, SPEC_ALPHA
    positions/trades/signals, allocation snapshots, and key diagnostics.
    It intentionally excludes API keys, Telegram token, and environment variables.
    """
    refresh_portfolio()

    ts = ny_now().strftime("%Y%m%d_%H%M%S")
    export_root = os.path.join(DATA_DIR, "exports")
    export_dir = os.path.join(export_root, f"{prefix}_{ts}")
    os.makedirs(export_dir, exist_ok=True)

    try:
        risk = open_risk_details()
    except Exception as exc:
        risk = {"error": str(exc)}

    try:
        withdrawal_plan = compute_withdrawal_plan()
    except Exception as exc:
        withdrawal_plan = {"error": str(exc)}

    try:
        allocation = dynamic_portfolio_allocation_targets()
    except Exception as exc:
        allocation = {"error": str(exc)}

    try:
        snapshot = compute_equity_snapshot_data()
    except Exception as exc:
        snapshot = {"error": str(exc)}

    write_json_file(os.path.join(export_dir, "portfolio.json"), portfolio)
    write_json_file(os.path.join(export_dir, "trades.json"), load_trades())
    write_json_file(os.path.join(export_dir, "signals.json"), load_signals())
    write_json_file(os.path.join(export_dir, "withdrawals.json"), load_withdrawals())
    write_json_file(os.path.join(export_dir, "open_risk.json"), risk)
    write_json_file(os.path.join(export_dir, "withdrawal_plan.json"), withdrawal_plan)
    write_json_file(os.path.join(export_dir, "allocation_plan.json"), allocation)
    write_json_file(os.path.join(export_dir, "equity_snapshot_live.json"), snapshot)

    # Core ledger, if present.
    try:
        write_json_file(os.path.join(export_dir, "core_positions.json"), load_core_positions())
    except Exception as exc:
        write_json_file(os.path.join(export_dir, "core_positions_error.json"), {"error": str(exc)})
    try:
        write_json_file(os.path.join(export_dir, "core_trades.json"), load_core_trades())
    except Exception as exc:
        write_json_file(os.path.join(export_dir, "core_trades_error.json"), {"error": str(exc)})
    try:
        write_json_file(os.path.join(export_dir, "core_signals.json"), table_rows("core_signals"))
    except Exception as exc:
        write_json_file(os.path.join(export_dir, "core_signals_error.json"), {"error": str(exc)})

    # SPEC_ALPHA ledger.
    try:
        write_json_file(os.path.join(export_dir, "spec_positions.json"), load_spec_positions())
    except Exception as exc:
        write_json_file(os.path.join(export_dir, "spec_positions_error.json"), {"error": str(exc)})
    try:
        write_json_file(os.path.join(export_dir, "spec_trades.json"), load_spec_trades())
    except Exception as exc:
        write_json_file(os.path.join(export_dir, "spec_trades_error.json"), {"error": str(exc)})
    try:
        write_json_file(os.path.join(export_dir, "spec_signals.json"), table_rows("spec_signals"))
    except Exception as exc:
        write_json_file(os.path.join(export_dir, "spec_signals_error.json"), {"error": str(exc)})

    write_json_file(
        os.path.join(export_dir, "meta_snapshot.json"),
        {
            "strategy_version": STRATEGY_VERSION,
            "ny_time": ny_now().strftime("%Y-%m-%d %H:%M:%S %Z"),
            "last_scan_day": get_meta("last_scan_day"),
            "last_scan_bar_date": get_meta("last_scan_bar_date"),
            "last_equity_snapshot_date": get_meta("last_equity_snapshot_date"),
            "withdrawal_high_water_mark": get_meta("withdrawal_high_water_mark"),
            "withdrawal_hwm_initialized_at": get_meta("withdrawal_hwm_initialized_at"),
            "positions_count": len(portfolio.get("positions", {})),
            "cash": portfolio.get("cash"),
            "panic_mode": PANIC_MODE,
            "core_enabled": globals().get("WEALTH_CORE_ENABLED", None),
            "spec_alpha_enabled": SPEC_ALPHA_ENABLED,
            "spec_alpha_ledger_enabled": SPEC_ALPHA_LEDGER_ENABLED,
            "spec_alpha_public_signal_enabled": SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED,
            "spec_alpha_target_pct": SPEC_ALPHA_ACCOUNT_ALLOC_PCT,
        },
    )

    for table in sorted(_V37_TABLE_EXPORT_ALLOWED):
        write_json_file(os.path.join(export_dir, f"{table}.table.json"), table_rows(table))

    # CSV versions are useful for analysis.
    try:
        trades = load_trades()
        if trades:
            pd.DataFrame(safe_convert(trades)).to_csv(os.path.join(export_dir, "trades.csv"), index=False)

        core_trades = load_core_trades()
        if core_trades:
            pd.DataFrame(safe_convert(core_trades)).to_csv(os.path.join(export_dir, "core_trades.csv"), index=False)

        spec_trades = load_spec_trades()
        if spec_trades:
            pd.DataFrame(safe_convert(spec_trades)).to_csv(os.path.join(export_dir, "spec_trades.csv"), index=False)

        positions_rows = []
        for ticker, pos in portfolio.get("positions", {}).items():
            row = {"ticker": ticker, "sleeve": "TACTICAL"}
            row.update(safe_convert(pos))
            positions_rows.append(row)
        if positions_rows:
            pd.DataFrame(positions_rows).to_csv(os.path.join(export_dir, "positions.csv"), index=False)

        core_positions = load_core_positions()
        if core_positions:
            pd.DataFrame(safe_convert(core_positions)).to_csv(os.path.join(export_dir, "core_positions.csv"), index=False)

        spec_positions = load_spec_positions()
        if spec_positions:
            pd.DataFrame(safe_convert(spec_positions)).to_csv(os.path.join(export_dir, "spec_positions.csv"), index=False)
    except Exception as exc:
        print(f"[CSV EXPORT WARNING] {exc}")

    zip_path = f"{export_dir}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(export_dir):
            for filename in files:
                full_path = os.path.join(root, filename)
                arcname = os.path.relpath(full_path, export_dir)
                z.write(full_path, arcname)

    return zip_path


_V37_OLD_RESET_ALL_PAPER_STATE = reset_all_paper_state


def reset_all_paper_state(update_id: Optional[int] = None) -> Tuple[bool, str, Optional[str]]:
    """V3.7 reset: includes SPEC_ALPHA ledger cleanup after backup export."""
    ok, msg, backup_path = _V37_OLD_RESET_ALL_PAPER_STATE(update_id=update_id)

    with db_tx() as conn:
        conn.execute("DELETE FROM spec_positions")
        conn.execute("DELETE FROM spec_trades")
        conn.execute("DELETE FROM spec_signals")
        conn.execute(
            """
            DELETE FROM meta
            WHERE key IN (
                'last_spec_alpha_check_month',
                'last_spec_alpha_signal_ts'
            )
            """
        )

    audit("RESET_ALL_SPEC_ALPHA", "SPEC_ALPHA state cleared")

    msg += (
        "\n\n"
        "V3.7 extra cleanup:\n"
        "✅ SPEC_ALPHA positions cleared\n"
        "✅ SPEC_ALPHA trades cleared\n"
        "✅ SPEC_ALPHA signals cleared"
    )

    return ok, msg, backup_path


# -----------------------------------------------------------------------------
# V3.8 INSTITUTIONAL MONITOR LAYER
# -----------------------------------------------------------------------------
# This layer is intentionally diagnostic-only. It does not change entries,
# exits, stops, allocation, public/private signals, or any trading decision.
# It exists so download_state / download_institutional include the data needed
# to review the bot professionally without slowing the main loop.

INSTITUTIONAL_MONITOR_ENABLED = os.getenv("INSTITUTIONAL_MONITOR_ENABLED", "1") != "0"
INSTITUTIONAL_CONCENTRATION_WARN_PCT = float(os.getenv("INSTITUTIONAL_CONCENTRATION_WARN_PCT", "35"))
INSTITUTIONAL_TOP_POSITION_WARN_PCT = float(os.getenv("INSTITUTIONAL_TOP_POSITION_WARN_PCT", "15"))
INSTITUTIONAL_VAR_LOOKBACK_TRADES = int(os.getenv("INSTITUTIONAL_VAR_LOOKBACK_TRADES", "50"))


def _v38_float(value: Any, default: float = 0.0) -> float:
    try:
        val = float(value)
        return val if math.isfinite(val) else default
    except Exception:
        return default


def _v38_pct(part: float, whole: float) -> float:
    return 0.0 if whole <= 0 else (part / whole) * 100.0


def _v38_cluster_for_ticker(ticker: str) -> str:
    t = str(ticker).upper()

    clusters = {
        "cash_like": {"BIL", "SGOV", "SHY", "IEF", "TLT"},
        "broad_equity": {"SPY", "VOO", "VTI", "DIA", "IWM"},
        "growth_tech": {"QQQ", "XLK", "IGV", "XLC", "MSFT", "AAPL", "META", "GOOGL", "AMZN", "NFLX", "CRM", "ADBE", "INTU", "SHOP"},
        "semis_ai": {"SMH", "SOXX", "NVDA", "AVGO", "AMD", "MU", "LRCX", "ASML", "QCOM", "KLAC", "AMAT", "TSM", "TXN", "ADI", "MRVL", "MPWR", "ON", "NXPI", "ARM", "ANET", "CDNS", "SNPS"},
        "cyber_cloud": {"PANW", "CRWD", "ZS", "NET", "NOW", "PLTR", "DDOG", "MDB", "TEAM", "WDAY", "FTNT", "HUBS", "APP", "TTD"},
        "financials": {"XLF", "KRE", "JPM", "GS", "MS", "BAC", "WFC", "SCHW", "BLK", "SPGI", "MCO", "CME", "ICE", "NDAQ", "V", "MA", "AXP", "BX", "KKR", "APO", "PGR", "CB"},
        "industrials": {"XLI", "IYT", "CAT", "DE", "GE", "ETN", "HON", "RTX", "URI", "PH", "CMI", "EMR", "ITW", "ROK", "TT", "PWR", "FAST", "PCAR", "LMT", "NOC", "GD", "TDG", "GWW", "UNP", "CSX", "LUNR", "PL", "RKLB"},
        "healthcare": {"XLV", "IBB", "LLY", "UNH", "ABBV", "ISRG", "TMO", "ABT", "MRK", "JNJ", "AMGN", "REGN", "VRTX", "SYK", "BSX", "MDT", "DHR", "GILD", "HCA", "MCK", "COR", "IQV", "LQDA", "SYRE"},
        "consumer": {"XLP", "XLY", "COST", "WMT", "MCD", "HD", "LOW", "BKNG", "NKE", "SBUX", "CMG", "TJX", "ROST", "AZO", "ORLY", "YUM", "DPZ", "MAR", "HLT", "RCL", "MELI", "UBER"},
        "energy_materials": {"XLE", "XOP", "XLB", "XOM", "CVX", "SLB", "FCX", "LIN", "COP", "EOG", "MPC", "PSX", "VLO", "NUE", "STLD", "SCCO", "NEM", "APD", "SHW", "ECL", "MLM", "VMC", "DBC", "DBB", "CPER"},
        "metals": {"GLD", "IAU", "SLV", "GDX", "GDXJ", "SIL", "SILJ", "AEM", "GOLD", "KGC", "WPM", "FNV"},
        "real_estate_utilities": {"XLU", "XLRE", "NEE", "CEG", "VST", "DLR", "EQIX", "PLD", "AMT", "HOUS"},
        "crypto_beta": {"COIN", "HOOD", "MSTR", "MARA", "RIOT", "CLSK", "IREN", "WULF", "HUT", "BITF"},
        "bear_inverse": {"SQQQ", "SPXU", "SDOW", "TZA"},
    }

    for name, members in clusters.items():
        if t in members:
            return name
    return "other"


def _v38_entry_sleeve_from_pos(ticker: str, pos: Dict[str, Any]) -> str:
    entry_data = pos.get("entry_data", {}) if isinstance(pos, dict) else {}
    sleeve = str(entry_data.get("strategy_sleeve") or entry_data.get("sleeve") or "").upper()
    if sleeve:
        return sleeve
    if str(ticker).upper() in {"SQQQ", "SPXU", "SDOW", "TZA"}:
        return "BEAR_INVERSE"
    return "LONG_VCP_OR_TACTICAL"


def _v38_collect_holdings(prices: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
    refresh_portfolio()
    swing_positions = portfolio.get("positions", {}) or {}
    core_positions = load_core_positions() if globals().get("CORE_LEDGER_ENABLED", False) else {}
    spec_positions = load_spec_positions() if globals().get("SPEC_ALPHA_LEDGER_ENABLED", False) else {}

    tickers = list(dict.fromkeys(list(swing_positions.keys()) + list(core_positions.keys()) + list(spec_positions.keys())))
    if prices is None:
        prices = get_prices_batch(tickers) if tickers else {}

    holdings: List[Dict[str, Any]] = []

    for ticker, pos in swing_positions.items():
        shares = _v38_float(pos.get("shares"), 0.0)
        entry = _v38_float(pos.get("price"), 0.0)
        price = _v38_float(prices.get(ticker, entry), entry)
        value = shares * price
        holdings.append({
            "ticker": ticker,
            "ledger": "swing",
            "sleeve": _v38_entry_sleeve_from_pos(ticker, pos),
            "cluster": _v38_cluster_for_ticker(ticker),
            "shares": shares,
            "entry_price": entry,
            "mark_price": price,
            "market_value": round(value, 2),
            "cost_basis": round(entry * shares, 2),
            "unrealized_profit": round((price - entry) * shares, 2),
            "stop": pos.get("stop"),
            "highest": pos.get("highest"),
        })

    for ticker, pos in core_positions.items():
        shares = _v38_float(pos.get("shares"), 0.0)
        entry = _v38_float(pos.get("avg_entry_price"), 0.0)
        cost_basis = _v38_float(pos.get("cost_basis"), entry * shares)
        price = _v38_float(prices.get(ticker, entry), entry)
        value = shares * price
        holdings.append({
            "ticker": ticker,
            "ledger": "core",
            "sleeve": "CORE_WEALTH",
            "cluster": _v38_cluster_for_ticker(ticker),
            "shares": shares,
            "entry_price": entry,
            "mark_price": price,
            "market_value": round(value, 2),
            "cost_basis": round(cost_basis, 2),
            "unrealized_profit": round(value - cost_basis, 2),
            "target_account_pct": pos.get("target_account_pct"),
        })

    for ticker, pos in spec_positions.items():
        shares = _v38_float(pos.get("shares"), 0.0)
        entry = _v38_float(pos.get("avg_entry_price"), 0.0)
        cost_basis = _v38_float(pos.get("cost_basis"), entry * shares)
        price = _v38_float(prices.get(ticker, entry), entry)
        value = shares * price
        holdings.append({
            "ticker": ticker,
            "ledger": "spec",
            "sleeve": "SPEC_ALPHA",
            "cluster": _v38_cluster_for_ticker(ticker),
            "shares": shares,
            "entry_price": entry,
            "mark_price": price,
            "market_value": round(value, 2),
            "cost_basis": round(cost_basis, 2),
            "unrealized_profit": round(value - cost_basis, 2),
            "target_account_pct": pos.get("target_account_pct"),
        })

    return holdings


def institutional_datahealth_snapshot() -> Dict[str, Any]:
    refresh_portfolio()
    swing_positions = portfolio.get("positions", {}) or {}
    core_positions = load_core_positions() if globals().get("CORE_LEDGER_ENABLED", False) else {}
    spec_positions = load_spec_positions() if globals().get("SPEC_ALPHA_LEDGER_ENABLED", False) else {}
    tickers = list(dict.fromkeys(list(swing_positions.keys()) + list(core_positions.keys()) + list(spec_positions.keys())))
    prices = get_prices_batch(tickers) if tickers else {}
    holdings = _v38_collect_holdings(prices)

    missing_quotes = [t for t in tickers if t not in prices]
    bad_values = []
    stop_warnings = []

    for h in holdings:
        if h["shares"] <= 0 or h["entry_price"] <= 0 or h["mark_price"] <= 0:
            bad_values.append(h["ticker"])
        if h["ledger"] == "swing":
            stop = _v38_float(h.get("stop"), 0.0)
            if stop <= 0:
                stop_warnings.append({"ticker": h["ticker"], "issue": "missing_or_invalid_stop"})
            elif h["sleeve"] != "BEAR_INVERSE" and stop > h["mark_price"] * 1.25:
                stop_warnings.append({"ticker": h["ticker"], "issue": "stop_far_above_price_check_manually"})

    status = "OK"
    if missing_quotes or bad_values or len(stop_warnings) >= 3:
        status = "WARNING"
    if _v38_float(portfolio.get("cash"), 0.0) < 0:
        status = "CRITICAL"

    return {
        "status": status,
        "ny_time": ny_now().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "expected_daily_bar_date": expected_daily_bar_date(),
        "last_scan_day": get_meta("last_scan_day"),
        "last_scan_bar_date": get_meta("last_scan_bar_date"),
        "panic_mode": PANIC_MODE,
        "cash_negative": _v38_float(portfolio.get("cash"), 0.0) < 0,
        "holdings_count": len(holdings),
        "quote_tickers_requested": len(tickers),
        "quote_tickers_received": len(prices),
        "missing_quotes": missing_quotes,
        "bad_value_tickers": bad_values,
        "stop_warnings": stop_warnings,
        "notes": [
            "On-demand diagnostic only; does not block trades.",
            "Missing quotes may be temporary provider/API issues or market-closed behavior.",
        ],
    }


def institutional_riskmatrix_snapshot() -> Dict[str, Any]:
    snapshot = compute_equity_snapshot_data()
    equity = _v38_float(snapshot.get("equity"), 0.0)
    holdings = _v38_collect_holdings()

    by_ledger: Dict[str, float] = {"cash": _v38_float(snapshot.get("cash"), 0.0)}
    by_sleeve: Dict[str, float] = {}
    by_cluster: Dict[str, float] = {}

    for h in holdings:
        value = _v38_float(h.get("market_value"), 0.0)
        by_ledger[h["ledger"]] = by_ledger.get(h["ledger"], 0.0) + value
        by_sleeve[h["sleeve"]] = by_sleeve.get(h["sleeve"], 0.0) + value
        by_cluster[h["cluster"]] = by_cluster.get(h["cluster"], 0.0) + value

    top_positions = sorted(holdings, key=lambda x: _v38_float(x.get("market_value"), 0.0), reverse=True)[:10]
    for h in top_positions:
        h["account_pct"] = round(_v38_pct(_v38_float(h.get("market_value"), 0.0), equity), 2)

    cluster_rows = []
    warnings = []
    for cluster, value in sorted(by_cluster.items(), key=lambda x: x[1], reverse=True):
        pct_val = round(_v38_pct(value, equity), 2)
        row = {"cluster": cluster, "value": round(value, 2), "account_pct": pct_val}
        cluster_rows.append(row)
        if pct_val >= INSTITUTIONAL_CONCENTRATION_WARN_PCT:
            warnings.append(f"Cluster {cluster} is {pct_val}% of equity")

    for h in top_positions:
        if h.get("account_pct", 0) >= INSTITUTIONAL_TOP_POSITION_WARN_PCT:
            warnings.append(f"Top position {h['ticker']} is {h['account_pct']}% of equity")

    ledger_rows = [
        {"ledger": k, "value": round(v, 2), "account_pct": round(_v38_pct(v, equity), 2)}
        for k, v in sorted(by_ledger.items(), key=lambda x: x[1], reverse=True)
    ]
    sleeve_rows = [
        {"sleeve": k, "value": round(v, 2), "account_pct": round(_v38_pct(v, equity), 2)}
        for k, v in sorted(by_sleeve.items(), key=lambda x: x[1], reverse=True)
    ]

    return {
        "equity": round(equity, 2),
        "ledger_exposure": ledger_rows,
        "sleeve_exposure": sleeve_rows,
        "cluster_exposure": cluster_rows,
        "top_positions": top_positions,
        "warnings": warnings,
        "status": "WARNING" if warnings else "OK",
    }


def institutional_stress_snapshot() -> Dict[str, Any]:
    risk = institutional_riskmatrix_snapshot()
    holdings = _v38_collect_holdings()
    equity = _v38_float(risk.get("equity"), 0.0)

    scenarios = {
        "broad_risk_off": {
            "description": "Broad risk-off: core -8%, long swing -10%, SPEC -18%, bear inverse +10%.",
            "default": -0.08,
            "CORE_WEALTH": -0.08,
            "SPEC_ALPHA": -0.18,
            "LONG_VCP_OR_TACTICAL": -0.10,
            "BEAR_INVERSE": 0.10,
        },
        "spec_momentum_unwind": {
            "description": "SPEC momentum unwind: SPEC -25%, growth/semis -10%, other core -4%.",
            "default": -0.04,
            "SPEC_ALPHA": -0.25,
            "LONG_VCP_OR_TACTICAL": -0.08,
            "BEAR_INVERSE": 0.05,
        },
        "growth_semis_shock": {
            "description": "Growth/semis shock: semis/growth clusters -15%, SPEC -12%, other assets -5%.",
            "default": -0.05,
            "SPEC_ALPHA": -0.12,
            "LONG_VCP_OR_TACTICAL": -0.10,
            "BEAR_INVERSE": 0.05,
            "cluster_overrides": {"semis_ai": -0.15, "growth_tech": -0.15, "cyber_cloud": -0.15},
        },
        "bear_inverse_whipsaw": {
            "description": "Bear sleeve whipsaw: bear inverse -15%, other risk assets +2%.",
            "default": 0.02,
            "BEAR_INVERSE": -0.15,
        },
    }

    results = []
    for name, cfg in scenarios.items():
        pnl = 0.0
        for h in holdings:
            value = _v38_float(h.get("market_value"), 0.0)
            sleeve = h.get("sleeve")
            shock = cfg.get(sleeve, cfg.get("default", 0.0))
            cluster_overrides = cfg.get("cluster_overrides", {}) or {}
            if h.get("cluster") in cluster_overrides:
                shock = cluster_overrides[h.get("cluster")]
            pnl += value * float(shock)
        results.append({
            "scenario": name,
            "description": cfg.get("description"),
            "estimated_pnl": round(pnl, 2),
            "estimated_pct_of_equity": round(_v38_pct(pnl, equity), 2),
        })

    worst = min(results, key=lambda x: x["estimated_pnl"], default=None)
    return {
        "equity": round(equity, 2),
        "scenarios": results,
        "worst_scenario": worst,
        "status": "WARNING" if worst and worst.get("estimated_pct_of_equity", 0) <= -10 else "OK",
        "note": "Scenario model is approximate and for monitoring only; it does not block trades.",
    }


def institutional_execution_snapshot() -> Dict[str, Any]:
    trades = load_trades()
    rows = []
    for t in trades[-INSTITUTIONAL_VAR_LOOKBACK_TRADES:]:
        entry_data = t.get("entry_data", {}) or {}
        signal_price = entry_data.get("signal_price")
        entry_price = t.get("entry_price")
        if isinstance(signal_price, (int, float)) and signal_price and float(signal_price) > 0:
            bps = ((float(entry_price) - float(signal_price)) / float(signal_price)) * 10000
            rows.append({
                "ticker": t.get("ticker"),
                "sleeve": entry_data.get("strategy_sleeve") or entry_data.get("strategy_family") or entry_data.get("setup_type") or "unknown",
                "signal_price": round(float(signal_price), 4),
                "entry_price": round(float(entry_price), 4),
                "entry_slippage_bps": round(bps, 2),
                "exit_reason": t.get("exit_reason"),
                "profit": t.get("profit"),
            })

    avg_bps = None
    worst_bps = None
    if rows:
        vals = [float(r["entry_slippage_bps"]) for r in rows]
        avg_bps = round(sum(vals) / len(vals), 2)
        worst_bps = round(max(vals), 2)

    core_trades = load_core_trades() if globals().get("CORE_LEDGER_ENABLED", False) else []
    spec_trades = load_spec_trades() if globals().get("SPEC_ALPHA_LEDGER_ENABLED", False) else []

    return {
        "swing_trades_with_signal_price": len(rows),
        "avg_entry_slippage_bps": avg_bps,
        "worst_entry_slippage_bps": worst_bps,
        "recent_rows": rows[-20:],
        "core_trade_records": len(core_trades),
        "spec_trade_records": len(spec_trades),
        "status": "WARNING" if worst_bps is not None and worst_bps > 100 else "OK",
        "note": "Core/SPEC are monthly ledger trades and do not always have a single signal-price slippage metric.",
    }


def institutional_drift_snapshot() -> Dict[str, Any]:
    perf = realized_performance_all_time()
    sleeve = sleeve_performance_summary()
    snapshot = compute_equity_snapshot_data()
    core_trades = load_core_trades() if globals().get("CORE_LEDGER_ENABLED", False) else []
    spec_trades = load_spec_trades() if globals().get("SPEC_ALPHA_LEDGER_ENABLED", False) else []
    swing_trades = load_trades()

    notes = []
    status = "OK"
    if len(swing_trades) < 30:
        notes.append("Swing sample is still small; do not judge model drift yet.")
    if len(spec_trades) < 10:
        notes.append("SPEC_ALPHA sample is still small; monthly rotation needs several months before judgment.")
    if len(core_trades) < 5:
        notes.append("Core sample is still small; use allocation drift rather than trade stats.")
    if perf.get("profit", 0) < 0 and perf.get("trade_records", 0) >= 20:
        status = "WARNING"
        notes.append("Realized total P/L is negative with at least 20 trade records; review fills and sleeve behavior.")

    return {
        "status": status,
        "realized_performance": perf,
        "sleeve_summary": sleeve,
        "equity_snapshot": snapshot,
        "sample_counts": {
            "swing_trades": len(swing_trades),
            "core_trades": len(core_trades),
            "spec_trades": len(spec_trades),
        },
        "model_reference": {
            "version": "v3.7 aggressive 45/15/40 research reference",
            "modeled_base_return_50bps_pct": 104.10,
            "modeled_spec_100bps_stress_total_return_pct": 76.40,
            "modeled_spec_best10_removed_total_return_pct": 61.92,
            "warning": "Model reference is from historical research; live drift requires forward sample.",
        },
        "notes": notes,
    }


def institutional_validation_snapshot() -> Dict[str, Any]:
    return {
        "strategy_version": STRATEGY_VERSION,
        "allocation": "45% core / 15% VCP-bear tactical / 40% SPEC_ALPHA in supportive regimes",
        "research_reference": {
            "base_case_50bps": {"modeled_return_pct": 104.10, "modeled_final_equity_on_4000": 8164.00},
            "optimistic_spec_25bps": {"modeled_return_pct": 120.94, "modeled_final_equity_on_4000": 8837.60},
            "spec_100bps_stress": {"modeled_return_pct": 76.40, "modeled_final_equity_on_4000": 7056.16},
            "spec_best10_removed": {"modeled_return_pct": 61.92, "modeled_final_equity_on_4000": 6476.96},
            "spec_crypto_adjusted": {"modeled_return_pct": 94.77, "modeled_final_equity_on_4000": 7790.72},
        },
        "known_limitations": [
            "Integrated result is a modeled sleeve allocation, not a perfect shared-cash tick-by-tick execution simulation.",
            "SPEC_ALPHA is aggressive and had meaningful historical sleeve drawdown.",
            "Forward fills, slippage, and monthly rotation behavior must be monitored.",
            "Research cache quality and survivorship limitations still matter.",
        ],
        "live_validation_rules": [
            "Do not judge SPEC_ALPHA until several monthly rotations exist.",
            "Compare execution slippage to 50 bps model assumption.",
            "Watch concentration in semis/growth/spec momentum clusters.",
            "Export download_state regularly for review.",
        ],
    }


def institutional_snapshot() -> Dict[str, Any]:
    data = {}
    sections = [
        ("datahealth", institutional_datahealth_snapshot),
        ("riskmatrix", institutional_riskmatrix_snapshot),
        ("stressstatus", institutional_stress_snapshot),
        ("executionstatus", institutional_execution_snapshot),
        ("driftstatus", institutional_drift_snapshot),
        ("validationstatus", institutional_validation_snapshot),
    ]
    for name, fn in sections:
        try:
            data[name] = fn()
        except Exception as exc:
            data[name] = {"status": "ERROR", "error": str(exc)}
    data["generated_at"] = ny_now().strftime("%Y-%m-%d %H:%M:%S %Z")
    data["monitor_version"] = "v3.8_institutional_monitor_diagnostic_only"
    data["trading_logic_changed"] = False
    return data


def _v38_status_emoji(status: Any) -> str:
    s = str(status or "").upper()
    if s == "OK":
        return "✅"
    if s == "WARNING":
        return "🟡"
    if s == "CRITICAL" or s == "ERROR":
        return "🔴"
    return "⚪"


def format_institutional_status() -> str:
    snap = institutional_snapshot()
    dh = snap.get("datahealth", {})
    rm = snap.get("riskmatrix", {})
    ss = snap.get("stressstatus", {})
    ex = snap.get("executionstatus", {})
    dr = snap.get("driftstatus", {})
    val = snap.get("validationstatus", {})
    worst = ss.get("worst_scenario") or {}
    top_clusters = (rm.get("cluster_exposure") or [])[:5]
    cluster_text = "\n".join(
        f"• {row.get('cluster')}: {row.get('account_pct')}%"
        for row in top_clusters
    ) or "No holdings yet."

    return (
        "🏛️ INSTITUTIONAL STATUS v3.8\n\n"
        "Diagnostic-only layer. Trading logic is unchanged.\n\n"
        f"🧪 Data health: {_v38_status_emoji(dh.get('status'))} {dh.get('status')} "
        f"({dh.get('quote_tickers_received')}/{dh.get('quote_tickers_requested')} quotes)\n"
        f"🧮 Risk matrix: {_v38_status_emoji(rm.get('status'))} {rm.get('status')}\n"
        f"🔥 Stress: {_v38_status_emoji(ss.get('status'))} worst {worst.get('scenario')} "
        f"{worst.get('estimated_pct_of_equity')}%\n"
        f"🎯 Execution: {_v38_status_emoji(ex.get('status'))} avg slip {ex.get('avg_entry_slippage_bps')} bps | worst {ex.get('worst_entry_slippage_bps')} bps\n"
        f"🧭 Drift: {_v38_status_emoji(dr.get('status'))} {dr.get('status')}\n\n"
        f"💼 Equity: {format_money(float((rm or {}).get('equity', 0) or 0))}\n"
        "Top exposure clusters:\n"
        f"{cluster_text}\n\n"
        "Commands:\n"
        "datahealth | riskmatrix | stressstatus | executionstatus | driftstatus | validationstatus\n"
        "download_institutional | download_state"
    )


def format_datahealth_status() -> str:
    d = institutional_datahealth_snapshot()
    missing = d.get("missing_quotes") or []
    stops = d.get("stop_warnings") or []
    return (
        "🧪 DATA HEALTH v3.8\n\n"
        f"Status: {_v38_status_emoji(d.get('status'))} {d.get('status')}\n"
        f"NY time: {d.get('ny_time')}\n"
        f"Expected daily bar: {d.get('expected_daily_bar_date')}\n"
        f"Last scan day/bar: {d.get('last_scan_day')} / {d.get('last_scan_bar_date')}\n"
        f"Panic mode: {yes_no(bool(d.get('panic_mode')))}\n"
        f"Quotes: {d.get('quote_tickers_received')}/{d.get('quote_tickers_requested')}\n"
        f"Missing quotes: {', '.join(missing[:20]) if missing else 'None'}\n"
        f"Bad value tickers: {', '.join(d.get('bad_value_tickers') or []) or 'None'}\n"
        f"Stop warnings: {len(stops)}\n\n"
        "This is diagnostic-only and does not block trades."
    )


def format_riskmatrix_status() -> str:
    r = institutional_riskmatrix_snapshot()
    ledgers = "\n".join(f"• {x['ledger']}: {format_money(x['value'])} ({x['account_pct']}%)" for x in r.get("ledger_exposure", []))
    clusters = "\n".join(f"• {x['cluster']}: {format_money(x['value'])} ({x['account_pct']}%)" for x in (r.get("cluster_exposure") or [])[:10])
    tops = "\n".join(f"• {x['ticker']}: {format_money(x['market_value'])} ({x.get('account_pct')}%)" for x in (r.get("top_positions") or [])[:8])
    warnings = "\n".join(f"⚠️ {w}" for w in r.get("warnings", [])) or "✅ No concentration warnings."
    return (
        "🧮 RISK MATRIX v3.8\n\n"
        f"Status: {_v38_status_emoji(r.get('status'))} {r.get('status')}\n"
        f"Total equity: {format_money(r.get('equity', 0))}\n\n"
        "Ledger exposure:\n" + (ledgers or "No holdings.") + "\n\n"
        "Top clusters:\n" + (clusters or "No holdings.") + "\n\n"
        "Top positions:\n" + (tops or "No holdings.") + "\n\n"
        f"{warnings}"
    )


def format_stress_status() -> str:
    s = institutional_stress_snapshot()
    rows = "\n".join(
        f"• {x['scenario']}: {format_money(x['estimated_pnl'])} ({x['estimated_pct_of_equity']}%)"
        for x in s.get("scenarios", [])
    )
    worst = s.get("worst_scenario") or {}
    return (
        "🔥 STRESS STATUS v3.8\n\n"
        f"Status: {_v38_status_emoji(s.get('status'))} {s.get('status')}\n"
        f"Equity: {format_money(s.get('equity', 0))}\n"
        f"Worst scenario: {worst.get('scenario')} {format_money(worst.get('estimated_pnl', 0))} ({worst.get('estimated_pct_of_equity')}%)\n\n"
        f"{rows}\n\n"
        "Approximate monitoring only. It does not block trades."
    )


def format_execution_status() -> str:
    e = institutional_execution_snapshot()
    recent = "\n".join(
        f"• {r['ticker']}: {r['entry_slippage_bps']} bps | {r.get('exit_reason')} | P/L {format_money(r.get('profit', 0))}"
        for r in (e.get("recent_rows") or [])[-8:]
    ) or "No swing trades with stored signal price yet."
    return (
        "🎯 EXECUTION STATUS v3.8\n\n"
        f"Status: {_v38_status_emoji(e.get('status'))} {e.get('status')}\n"
        f"Swing trades with signal price: {e.get('swing_trades_with_signal_price')}\n"
        f"Average entry slippage: {e.get('avg_entry_slippage_bps')} bps\n"
        f"Worst entry slippage: {e.get('worst_entry_slippage_bps')} bps\n"
        f"Core trade records: {e.get('core_trade_records')}\n"
        f"SPEC trade records: {e.get('spec_trade_records')}\n\n"
        "Recent rows:\n" + recent
    )


def format_drift_status() -> str:
    d = institutional_drift_snapshot()
    perf = d.get("realized_performance", {})
    counts = d.get("sample_counts", {})
    notes = "\n".join(f"• {n}" for n in d.get("notes", [])) or "No major drift notes yet."
    return (
        "🧭 MODEL DRIFT STATUS v3.8\n\n"
        f"Status: {_v38_status_emoji(d.get('status'))} {d.get('status')}\n"
        f"Realized total P/L: {format_money(perf.get('profit', 0))} ({format_pct(perf.get('pct'))})\n"
        f"Swing/Core/SPEC realized: {format_money(perf.get('swing_realized_profit', 0))} / {format_money(perf.get('core_realized_profit', 0))} / {format_money(perf.get('spec_realized_profit', 0))}\n"
        f"Sample counts: swing {counts.get('swing_trades')} | core {counts.get('core_trades')} | spec {counts.get('spec_trades')}\n\n"
        f"{notes}"
    )


def format_validation_status() -> str:
    v = institutional_validation_snapshot()
    rr = v.get("research_reference", {})
    base = rr.get("base_case_50bps", {})
    stress = rr.get("spec_100bps_stress", {})
    best_removed = rr.get("spec_best10_removed", {})
    limitations = "\n".join(f"• {x}" for x in v.get("known_limitations", []))
    rules = "\n".join(f"• {x}" for x in v.get("live_validation_rules", []))
    return (
        "🧪 VALIDATION STATUS v3.8\n\n"
        f"Strategy: {v.get('strategy_version')}\n"
        f"Allocation: {v.get('allocation')}\n\n"
        f"Base 50 bps model: +{base.get('modeled_return_pct')}% | final ${base.get('modeled_final_equity_on_4000')} on $4,000\n"
        f"100 bps SPEC stress model: +{stress.get('modeled_return_pct')}% | final ${stress.get('modeled_final_equity_on_4000')}\n"
        f"Best-10 removed model: +{best_removed.get('modeled_return_pct')}% | final ${best_removed.get('modeled_final_equity_on_4000')}\n\n"
        "Known limitations:\n" + limitations + "\n\n"
        "Live validation rules:\n" + rules
    )


def download_institutional_report() -> str:
    ts = ny_now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(DATA_DIR, f"institutional_snapshot_{ts}.json")
    write_json_file(path, institutional_snapshot())
    return path


_V38_OLD_EXPORT_STATE_BUNDLE = export_state_bundle


def export_state_bundle(prefix: str = "bot_state_export") -> str:
    zip_path = _V38_OLD_EXPORT_STATE_BUNDLE(prefix=prefix)
    if not INSTITUTIONAL_MONITOR_ENABLED:
        return zip_path
    try:
        snap = institutional_snapshot()
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("institutional_snapshot.json", json.dumps(safe_convert(snap), indent=2))
            z.writestr("institutional_status.txt", format_institutional_status())
    except Exception as exc:
        try:
            with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr("institutional_snapshot_error.json", json.dumps({"error": str(exc)}, indent=2))
        except Exception:
            pass
    return zip_path


_V38_OLD_HANDLE_COMMAND = handle_command


def handle_command(text: str, update_id: Optional[int] = None) -> None:
    text_clean = (text or "").strip()
    text_lower = text_clean.lower()

    if text_lower in {"institutionalstatus", "institutional_status"}:
        send(format_institutional_status())
        return
    if text_lower == "datahealth":
        send(format_datahealth_status())
        return
    if text_lower == "riskmatrix":
        send(format_riskmatrix_status())
        return
    if text_lower == "stressstatus":
        send(format_stress_status())
        return
    if text_lower == "executionstatus":
        send(format_execution_status())
        return
    if text_lower == "driftstatus":
        send(format_drift_status())
        return
    if text_lower == "validationstatus":
        send(format_validation_status())
        return
    if text_lower == "download_institutional":
        path = download_institutional_report()
        send_document(path, caption="institutional_snapshot.json")
        return
    if text_lower in {"help", "/help"}:
        _V38_OLD_HANDLE_COMMAND(text, update_id=update_id)
        send(
            "V3.8 institutional diagnostics:\n"
            "institutionalstatus | datahealth | riskmatrix | stressstatus | executionstatus | driftstatus | validationstatus\n"
            "download_institutional\n\n"
            "These are diagnostic-only and do not change trading logic."
        )
        return

    return _V38_OLD_HANDLE_COMMAND(text, update_id=update_id)




# -----------------------------------------------------------------------------
# V3.9 EU-RETAIL DEPLOYMENT CANDIDATE
# -----------------------------------------------------------------------------
# Offline-researched patch. It keeps v3.8 accounting, exports, Telegram commands,
# SPEC_ALPHA, long VCP, withdrawal, and institutional monitor logic intact.
# It changes only two broker-blocked live universes:
# 1) Core: U.S. ETFs -> USD-priced UCITS/ETP lines.
# 2) Bear: inverse ETFs -> long-only health/defense bear-stock relative strength.

STRATEGY_VERSION = os.getenv(
    "STRATEGY_VERSION",
    "v3.9-ucits-core-bear-stock-45-15-40-monitor"
)
WEALTH_STRATEGY_VERSION = os.getenv(
    "WEALTH_STRATEGY_VERSION",
    "wealth_core_ucits_usd_clean_v3_9_core_ledger"
)
BEAR_STRATEGY_VERSION = os.getenv(
    "BEAR_STRATEGY_VERSION",
    "bear_stock_rs_health_defense_v3_9"
)

# USD-priced UCITS/ETP core universe selected from the offline UCITS cache.
WEALTH_CORE_UNIVERSE = [
    "VUAA.L", "VUSD.L", "CSUS.L",
    "CNDX.L", "IUIT.L", "SMH.L",
    "IUHC.L", "IUFS.L", "IUES.L",
    "EGLN.L", "PHAG.L", "CMOD.L", "COPA.L",
    "IB01.L", "IBTA.L", "IDBT.L", "DTLA.L",
]
WEALTH_ASSET_CLUSTERS = {
    "VUAA.L": "broad_equity", "VUSD.L": "broad_equity", "CSUS.L": "broad_equity",
    "CNDX.L": "growth_tech", "IUIT.L": "growth_tech", "SMH.L": "semis",
    "IUHC.L": "defensive_equity", "IUFS.L": "financials", "IUES.L": "energy",
    "EGLN.L": "gold", "PHAG.L": "silver", "CMOD.L": "commodities", "COPA.L": "copper",
    "IB01.L": "cash_like", "IBTA.L": "short_bonds", "IDBT.L": "intermediate_bonds", "DTLA.L": "long_bonds",
}
WEALTH_CASH_LIKE = {"IB01.L"}
WEALTH_DEFENSIVE_ALLOWED = {"IB01.L", "IBTA.L", "IDBT.L", "DTLA.L", "EGLN.L", "IUHC.L"}

# Bear-stock strategy selected by robust offline backtesting.
# Only healthcare and defense/aerospace survived the slippage and stress review
# cleanly enough for a first live candidate. Energy/gold/utilities/broad baskets
# remain research-only.
BEAR_STOCK_BUCKETS: Dict[str, str] = {
    "UNH": "healthcare", "HUM": "healthcare", "ELV": "healthcare", "CI": "healthcare",
    "CAH": "healthcare", "MCK": "healthcare", "COR": "healthcare", "CVS": "healthcare",
    "JNJ": "healthcare", "MRK": "healthcare", "LLY": "healthcare", "ABBV": "healthcare",
    "AMGN": "healthcare", "REGN": "healthcare", "GILD": "healthcare", "VRTX": "healthcare",
    "BMY": "healthcare", "PFE": "healthcare", "ISRG": "healthcare", "TMO": "healthcare",
    "SYK": "healthcare", "BSX": "healthcare", "HCA": "healthcare", "THC": "healthcare",
    "UHS": "healthcare", "RMD": "healthcare", "HOLX": "healthcare", "VTRS": "healthcare",
    "LMT": "defense_aerospace", "NOC": "defense_aerospace", "RTX": "defense_aerospace",
    "GD": "defense_aerospace", "LHX": "defense_aerospace", "HII": "defense_aerospace",
    "BWXT": "defense_aerospace", "LDOS": "defense_aerospace", "BAH": "defense_aerospace",
    "KTOS": "defense_aerospace", "AVAV": "defense_aerospace", "MRCY": "defense_aerospace",
    "HWM": "defense_aerospace", "HEI": "defense_aerospace", "SPR": "defense_aerospace",
}
BEAR_WATCHLIST = list(dict.fromkeys(BEAR_STOCK_BUCKETS.keys()))
ALLOWED_BUY_TICKERS = set(WATCHLIST) | set(BEAR_WATCHLIST)

# v3.9 bear-stock defaults. The sleeve is top-1, cash-first, and USD only.
BEAR_MAX_SIGNALS_PER_SCAN = int(os.getenv("BEAR_MAX_SIGNALS_PER_SCAN", "1"))
BEAR_MAX_OPEN_POSITIONS = int(os.getenv("BEAR_MAX_OPEN_POSITIONS", "1"))
BEAR_MIN_PRICE = float(os.getenv("BEAR_MIN_PRICE", "10"))
BEAR_STOCK_MAX_PRICE = float(os.getenv("BEAR_STOCK_MAX_PRICE", "500"))
BEAR_MIN_AVG_DOLLAR_VOLUME = float(os.getenv("BEAR_MIN_AVG_DOLLAR_VOLUME", "50000000"))
BEAR_MIN_ATR_PCT = float(os.getenv("BEAR_MIN_ATR_PCT", "0.010"))
BEAR_MAX_ATR_PCT = float(os.getenv("BEAR_MAX_ATR_PCT", "0.085"))
BEAR_STOCK_MIN_REL_21_SPY = float(os.getenv("BEAR_STOCK_MIN_REL_21_SPY", "0.04"))
BEAR_STOCK_MIN_REL_63_SPY = float(os.getenv("BEAR_STOCK_MIN_REL_63_SPY", "0.08"))
BEAR_STOCK_MIN_REL_21_QQQ = float(os.getenv("BEAR_STOCK_MIN_REL_21_QQQ", "0.00"))
BEAR_STOCK_MAX_ACCOUNT_EXPOSURE_PCT = float(os.getenv("BEAR_STOCK_MAX_ACCOUNT_EXPOSURE_PCT", "0.15"))
BEAR_STOCK_CATASTROPHE_STOP_PCT = float(os.getenv("BEAR_STOCK_CATASTROPHE_STOP_PCT", "0.20"))

# Match research behavior: exit on bear-score cooldown; no partial/trailing/time churn by default.
BEAR_RISK_PCT = float(os.getenv("BEAR_RISK_PCT", "0.03"))
BEAR_BREAKEVEN_R_TRIGGER = float(os.getenv("BEAR_BREAKEVEN_R_TRIGGER", "999"))
BEAR_PARTIAL_TAKE_PROFIT_R = float(os.getenv("BEAR_PARTIAL_TAKE_PROFIT_R", "999"))
BEAR_PARTIAL_TAKE_PROFIT_PCT = float(os.getenv("BEAR_PARTIAL_TAKE_PROFIT_PCT", "999"))
BEAR_PARTIAL_TAKE_PROFIT_FRACTION = float(os.getenv("BEAR_PARTIAL_TAKE_PROFIT_FRACTION", "0.00"))
BEAR_TRAIL_MULT_EARLY = float(os.getenv("BEAR_TRAIL_MULT_EARLY", "999"))
BEAR_TRAIL_MULT_LATE = float(os.getenv("BEAR_TRAIL_MULT_LATE", "999"))
BEAR_TIME_STOP_DAYS = int(os.getenv("BEAR_TIME_STOP_DAYS", "0"))
BEAR_MAX_HOLDING_DAYS = int(os.getenv("BEAR_MAX_HOLDING_DAYS", "0"))

_V39_OLD_SETUP_LABEL = setup_label

def setup_label(setup_type: str) -> str:
    setup = str(setup_type).lower()
    if setup == "bear_stock_rs":
        return "🐻 Bear Stock Relative Strength"
    return _V39_OLD_SETUP_LABEL(setup_type)

_V39_OLD_SLEEVE_LABEL = sleeve_label

def sleeve_label(entry_data: Dict[str, Any]) -> str:
    sleeve = str(entry_data.get("strategy_sleeve", "LONG_VCP")).upper()
    if sleeve == "BEAR_STOCK":
        return "🐻 BEAR STOCK RS SLEEVE"
    return _V39_OLD_SLEEVE_LABEL(entry_data)

_V39_OLD_SLEEVE_SHORT_LABEL = sleeve_short_label

def sleeve_short_label(entry_data: Dict[str, Any]) -> str:
    sleeve = str(entry_data.get("strategy_sleeve", "LONG_VCP")).upper()
    if sleeve == "BEAR_STOCK":
        return "BEAR STOCK RS"
    return _V39_OLD_SLEEVE_SHORT_LABEL(entry_data)


def count_open_bear_positions() -> int:
    refresh_portfolio()
    count = 0
    for pos in portfolio["positions"].values():
        entry_data = pos.get("entry_data", {}) or {}
        if entry_data.get("strategy_sleeve") in {"BEAR_INVERSE", "BEAR_STOCK"}:
            count += 1
    return count

_V39_OLD_SLEEVE_FROM_TRADE = sleeve_from_trade

def sleeve_from_trade(trade: Dict[str, Any]) -> str:
    entry_data = trade.get("entry_data", {}) or {}
    sleeve = str(entry_data.get("strategy_sleeve") or "").upper()
    if sleeve in {"BEAR_STOCK", "BEAR_INVERSE"}:
        return sleeve
    family = str(entry_data.get("strategy_family") or "").lower()
    ticker = str(trade.get("ticker", "")).upper()
    if "bear_stock" in family or ticker in BEAR_WATCHLIST:
        return "BEAR_STOCK"
    return _V39_OLD_SLEEVE_FROM_TRADE(trade)


def _v39_ret(series: pd.Series, bars: int) -> Optional[float]:
    try:
        clean = series.dropna()
        if len(clean) <= bars:
            return None
        old = float(clean.iloc[-bars - 1])
        new = float(clean.iloc[-1])
        if old <= 0:
            return None
        return (new / old) - 1.0
    except Exception:
        return None


def analyze_bear_signal(
    ticker: str,
    df: pd.DataFrame,
    bear_details: Dict[str, Any],
) -> Optional[Tuple[str, float, int, float, int, Dict[str, Any]]]:
    """v3.9 robust health/defense top-1 bear-stock RS sleeve."""
    if not BEAR_SLEEVE_ENABLED:
        return None
    ticker = normalize_ticker(ticker) or ""
    if ticker not in BEAR_WATCHLIST:
        return None
    if not bear_details.get("active"):
        return None
    if df is None or df.empty or len(df) < 220:
        return None

    close = df["Close"].dropna()
    volume = df["Volume"]
    price = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    if price <= 0 or prev_close <= 0:
        return None

    atr_val = atr(df).iloc[-1]
    avg_vol = volume.rolling(20).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1]
    ma200 = close.rolling(200).mean().iloc[-1]
    rsi_val = rsi(close).iloc[-1]
    if any(pd.isna(x) for x in [atr_val, avg_vol, ma20, ma50, ma200, rsi_val]):
        return None

    atr_val = float(atr_val)
    atr_pct = atr_val / price
    avg_dollar_volume = float(avg_vol) * price
    volume_ratio = float(volume.iloc[-1]) / float(avg_vol) if float(avg_vol) > 0 else 0.0
    daily_move_pct = ((price - prev_close) / prev_close) * 100.0
    close_loc = close_location(df)

    if price < BEAR_MIN_PRICE or price > BEAR_STOCK_MAX_PRICE:
        return None
    if avg_dollar_volume < BEAR_MIN_AVG_DOLLAR_VOLUME:
        return None
    if atr_pct < BEAR_MIN_ATR_PCT or atr_pct > BEAR_MAX_ATR_PCT:
        return None
    if not (price > float(ma20) > float(ma50) and price > float(ma200)):
        return None

    ret21 = _v39_ret(close, 21)
    ret63 = _v39_ret(close, 63)
    if ret21 is None or ret63 is None or ret21 <= 0 or ret63 <= 0:
        return None

    frames = bear_details.get("frames", {}) if isinstance(bear_details, dict) else {}
    spy_df = frames.get("SPY")
    qqq_df = frames.get("QQQ")
    if spy_df is None or getattr(spy_df, "empty", True) or len(spy_df) < 80:
        spy_df = get_signal_dataframe("SPY", limit=260)
    if qqq_df is None or getattr(qqq_df, "empty", True) or len(qqq_df) < 80:
        qqq_df = get_signal_dataframe("QQQ", limit=260)
    if spy_df is None or qqq_df is None or spy_df.empty or qqq_df.empty:
        return None

    spy_ret21 = _v39_ret(spy_df["Close"].dropna(), 21)
    spy_ret63 = _v39_ret(spy_df["Close"].dropna(), 63)
    qqq_ret21 = _v39_ret(qqq_df["Close"].dropna(), 21)
    if spy_ret21 is None or spy_ret63 is None or qqq_ret21 is None:
        return None

    rel21_spy = ret21 - spy_ret21
    rel63_spy = ret63 - spy_ret63
    rel21_qqq = ret21 - qqq_ret21
    if rel21_spy < BEAR_STOCK_MIN_REL_21_SPY:
        return None
    if rel63_spy < BEAR_STOCK_MIN_REL_63_SPY:
        return None
    if rel21_qqq < BEAR_STOCK_MIN_REL_21_QQQ:
        return None

    returns63 = close.pct_change().tail(63).dropna()
    vol63_ann = float(returns63.std() * math.sqrt(252)) if not returns63.empty else 0.0
    rank_score = ((0.40 * rel63_spy) + (0.25 * rel21_spy) + (0.15 * ret63) + (0.10 * ret21) - (0.10 * vol63_ann)) * 100.0
    score = int(round(max(0.0, rank_score) * 10))

    stop = price * (1.0 - BEAR_STOCK_CATASTROPHE_STOP_PCT)
    risk = price - stop
    if risk <= 0:
        return None

    refresh_portfolio()
    account_equity = approximate_equity_from_portfolio()
    available_cash = float(portfolio["cash"])
    shares_by_risk = int((account_equity * BEAR_RISK_PCT) / risk)
    shares_by_position_cap = int((account_equity * min(MAX_POSITION_EQUITY_PCT, BEAR_STOCK_MAX_ACCOUNT_EXPOSURE_PCT)) / price)
    shares_by_cash = int((available_cash * CASH_USAGE_BUFFER) / price)
    shares = min(shares_by_risk, shares_by_position_cap, shares_by_cash)
    if shares <= 0:
        return None

    bucket = BEAR_STOCK_BUCKETS.get(ticker, "bear_stock")
    metrics = {
        "setup_type": "bear_stock_rs",
        "strategy_sleeve": "BEAR_STOCK",
        "strategy_family": BEAR_STRATEGY_VERSION,
        "strategy_version": BEAR_STRATEGY_VERSION,
        "bear_stock_bucket": bucket,
        "bucket": bucket,
        "breakout": False,
        "atr": atr_val,
        "atr_pct": atr_pct,
        "volume_ratio": volume_ratio,
        "daily_move_pct": daily_move_pct,
        "close_location": close_loc,
        "avg_dollar_volume": avg_dollar_volume,
        "ret21": float(ret21),
        "ret63": float(ret63),
        "rel21_spy": rel21_spy,
        "rel63_spy": rel63_spy,
        "rel21_qqq": rel21_qqq,
        "vol63": vol63_ann,
        "bear_score": int(bear_details.get("score", 0) or 0),
        "rank_score": rank_score,
        "min_score_required": "RS filters",
        "stop_model": "bear_stock_health_defense_v3_9_catastrophe_score_exit",
        "exit_params": {
            "breakeven_r_trigger": BEAR_BREAKEVEN_R_TRIGGER,
            "partial_take_profit_r": BEAR_PARTIAL_TAKE_PROFIT_R,
            "partial_take_profit_pct": BEAR_PARTIAL_TAKE_PROFIT_PCT,
            "partial_take_profit_fraction": BEAR_PARTIAL_TAKE_PROFIT_FRACTION,
            "trail_mult_early": BEAR_TRAIL_MULT_EARLY,
            "trail_mult_late": BEAR_TRAIL_MULT_LATE,
            "trail_tighten_pct": TRAIL_TIGHTEN_PCT,
            "time_stop_days": BEAR_TIME_STOP_DAYS,
            "time_stop_min_r": BEAR_TIME_STOP_MIN_R,
            "max_holding_days": BEAR_MAX_HOLDING_DAYS,
        },
    }
    return ticker, price, shares, stop, score, metrics


def format_portfolio_allocation_plan() -> str:
    plan = dynamic_portfolio_allocation_targets()
    risk = plan.get("risk_guard", {}) or {}
    return (
        "🏛️ INSTITUTIONAL ALLOCATION PLAN v3.9 UCITS CORE / BEAR STOCK\n\n"
        "Private bot only. This is portfolio guidance, not an automatic trade.\n\n"
        f"🕒 NY time: {plan.get('ny_time')}\n"
        f"🌎 Market: {market_label(str(plan.get('market', 'UNKNOWN')))} "
        f"({plan.get('market_score')}/8)\n"
        f"🐻 Bear pressure score: {plan.get('bear_score')}/60\n"
        f"🛡️ Risk guard: {risk.get('recommended_action')}\n"
        f"📉 Current DD: {risk.get('drawdown_pct')}% from {format_money(float(risk.get('high_equity', 0) or 0))}\n\n"
        "Target account buckets:\n"
        f"🏦 Core UCITS/USD rotation: {plan.get('core_wealth_pct')}%\n"
        f"⚡ SPEC_ALPHA rotation: {plan.get('spec_alpha_pct', SPEC_ALPHA_ACCOUNT_ALLOC_PCT * 100)}%\n"
        f"🐂 Long VCP tactical: {plan.get('long_vcp_tactical_pct')}%\n"
        f"🐻 Bear stock tactical: {plan.get('bear_inverse_tactical_pct')}%\n"
        f"💵 Cash reserve: {plan.get('cash_reserve_pct')}%\n\n"
        "Rules:\n"
        "• Core sleeve now uses the researched USD-priced UCITS/ETP universe.\n"
        "• Bear sleeve now uses long-only USD healthcare/defense relative strength, not inverse ETFs.\n"
        "• SPEC_ALPHA and Long VCP are unchanged.\n"
        "• In hard drawdown mode, new entries pause and exits/management continue."
    )


def _v38_cluster_for_ticker(ticker: str) -> str:
    t = str(ticker).upper()
    if t in BEAR_STOCK_BUCKETS:
        return BEAR_STOCK_BUCKETS[t]
    if t in WEALTH_ASSET_CLUSTERS:
        return WEALTH_ASSET_CLUSTERS[t]
    return "other"


def _v38_entry_sleeve_from_pos(ticker: str, pos: Dict[str, Any]) -> str:
    entry_data = pos.get("entry_data", {}) if isinstance(pos, dict) else {}
    sleeve = str(entry_data.get("strategy_sleeve") or entry_data.get("sleeve") or "").upper()
    if sleeve:
        return sleeve
    if str(ticker).upper() in BEAR_WATCHLIST:
        return "BEAR_STOCK"
    return "LONG_VCP_OR_TACTICAL"

_V39_OLD_HANDLE_COMMAND = handle_command

def handle_command(text: str, update_id: Optional[int] = None) -> None:
    text_clean = (text or "").strip()
    text_lower = text_clean.lower()

    if text_lower == "bearstatus":
        details = bear_regime_details(market_details=market_regime_details())
        open_bear = count_open_bear_positions()
        buckets = sorted(set(BEAR_STOCK_BUCKETS.values()))
        send(
            "🐻 BEAR STOCK SLEEVE STATUS v3.9\n\n"
            f"Enabled: {yes_no(BEAR_SLEEVE_ENABLED)}\n"
            f"Strategy: {BEAR_STRATEGY_VERSION}\n"
            f"Active now: {yes_no(bool(details.get('active')))}\n"
            f"Bear score: {details.get('score')}/{details.get('max_score')}\n"
            f"Entry threshold: {BEAR_ENTRY_SCORE}\n"
            f"Exit/calm threshold: {BEAR_EXIT_SCORE}\n"
            f"Open bear-stock positions: {open_bear}/{BEAR_MAX_OPEN_POSITIONS}\n"
            f"Universe size: {len(BEAR_WATCHLIST)} USD stocks\n"
            f"Buckets: {', '.join(buckets)}\n\n"
            "Live candidate: health/defense top-1 relative strength.\n"
            "No inverse ETFs, no options, no EUR instruments.\n"
            "Trades still use bought/sold after broker fill."
        )
        return

    if text_lower == "wealthstatus":
        alloc = dynamic_portfolio_allocation_targets()
        send(
            "🏛️ WEALTH / CORE LEDGER STATUS v3.9\n\n"
            f"Enabled: {yes_no(WEALTH_SLEEVE_ENABLED)}\n"
            f"Strategy: {WEALTH_STRATEGY_VERSION}\n"
            f"Universe: USD-priced UCITS/ETP core candidates\n"
            f"Dynamic allocation: {yes_no(WEALTH_DYNAMIC_ALLOCATION_ENABLED)}\n"
            f"Vol weighting: {yes_no(WEALTH_VOL_WEIGHTING_ENABLED)}\n"
            f"Cluster control: {yes_no(WEALTH_CLUSTER_CONTROL_ENABLED)}\n"
            f"Top assets: {WEALTH_CORE_TOP_N}\n"
            f"Current core target: {alloc.get('core_wealth_pct')}% of account\n"
            f"Long VCP target: {alloc.get('long_vcp_tactical_pct')}% of account\n"
            f"Bear stock target: {alloc.get('bear_inverse_tactical_pct')}% of account\n"
            f"Cash reserve target: {alloc.get('cash_reserve_pct')}% of account\n"
            f"Last wealth month: {get_meta('last_wealth_core_month')}\n"
            f"Public channel: ❌ never used for this sleeve\n\n"
            "Commands:\n"
            "wealthplan — ranked BUY/ADD/HOLD/TRIM/SELL plan\n"
            "corebuy TICKER SHARES at PRICE — record core buy\n"
            "coresell TICKER SHARES at PRICE — record core sell\n"
            "coreportfolio | corepnl | coreexposure | corestatus\n"
            "allocationplan | riskstatus | sleevestatus"
        )
        return

    if text_lower in {"help", "/help"}:
        _V39_OLD_HANDLE_COMMAND(text, update_id=update_id)
        send(
            "V3.9 deployment candidate notes:\n"
            "• Core universe is USD-priced UCITS/ETP. Use corebuy/coresell only after broker fill.\n"
            "• Bear sleeve is long-only health/defense stock RS, not inverse ETFs. Use bought/sold after signal/fill.\n"
            "• SPEC_ALPHA and Long VCP remain unchanged."
        )
        return

    return _V39_OLD_HANDLE_COMMAND(text, update_id=update_id)


# =============================================================================
# V4 EXPANDED FUTURE-GROWTH ALPHA EXTENSION
# =============================================================================
# Offline-researched candidate. This keeps v3.9 UCITS core, bear-stock sleeve,
# SPEC_ALPHA, long VCP, and all existing ledgers intact.
# It adds a separate monthly Growth Alpha sleeve with its own ledger.
# Target research allocation:
#   Core UCITS: 20%
#   Expanded Growth Alpha: 45%
#   SPEC_ALPHA: 20%
#   Tactical VCP/Bear-stock: 5%
#   Crypto tactical swing: 10%
# Options remain research-only and are not included here.
# V4 only expands the Growth Alpha universe from the current concentrated 70-name set
# to the offline-tested A-to-Z future-growth universe. Core, SPEC, tactical,
# indicators, ledgers, risk guard, and monitor logic remain otherwise unchanged.

STRATEGY_VERSION = os.getenv(
    "STRATEGY_VERSION",
    "v4-expanded-growth-25-45-20-10-monitor"
)

# Override v3.9/v3.8 allocation defaults.
WEALTH_CORE_ACCOUNT_ALLOC_PCT = float(os.getenv("WEALTH_CORE_ACCOUNT_ALLOC_PCT", "0.20"))
SPEC_ALPHA_ACCOUNT_ALLOC_PCT = float(os.getenv("SPEC_ALPHA_ACCOUNT_ALLOC_PCT", "0.20"))
WEALTH_CORE_ALLOC_BULL = float(os.getenv("WEALTH_CORE_ALLOC_BULL", "0.20"))
WEALTH_CORE_ALLOC_UNCERTAIN = float(os.getenv("WEALTH_CORE_ALLOC_UNCERTAIN", "0.20"))
WEALTH_CORE_ALLOC_BEAR = float(os.getenv("WEALTH_CORE_ALLOC_BEAR", "0.20"))
WEALTH_CORE_ALLOC_RISK_OFF = float(os.getenv("WEALTH_CORE_ALLOC_RISK_OFF", "0.20"))
WEALTH_TACTICAL_LONG_ALLOC_BULL = float(os.getenv("WEALTH_TACTICAL_LONG_ALLOC_BULL", "0.10"))
WEALTH_TACTICAL_LONG_ALLOC_UNCERTAIN = float(os.getenv("WEALTH_TACTICAL_LONG_ALLOC_UNCERTAIN", "0.05"))
WEALTH_TACTICAL_LONG_ALLOC_BEAR = float(os.getenv("WEALTH_TACTICAL_LONG_ALLOC_BEAR", "0.00"))
WEALTH_BEAR_ALLOC_BULL = float(os.getenv("WEALTH_BEAR_ALLOC_BULL", "0.00"))
WEALTH_BEAR_ALLOC_UNCERTAIN = float(os.getenv("WEALTH_BEAR_ALLOC_UNCERTAIN", "0.05"))
WEALTH_BEAR_ALLOC_BEAR = float(os.getenv("WEALTH_BEAR_ALLOC_BEAR", "0.10"))

GROWTH_ALPHA_ENABLED = os.getenv("GROWTH_ALPHA_ENABLED", "1") != "0"
GROWTH_ALPHA_LEDGER_ENABLED = os.getenv("GROWTH_ALPHA_LEDGER_ENABLED", "1") != "0"
GROWTH_ALPHA_ACCOUNT_ALLOC_PCT = float(os.getenv("GROWTH_ALPHA_ACCOUNT_ALLOC_PCT", "0.45"))
GROWTH_ALPHA_TOP_N = int(os.getenv("GROWTH_ALPHA_TOP_N", "5"))
GROWTH_ALPHA_MAX_PER_CLUSTER = int(os.getenv("GROWTH_ALPHA_MAX_PER_CLUSTER", "2"))
GROWTH_ALPHA_MIN_PRICE = float(os.getenv("GROWTH_ALPHA_MIN_PRICE", "8"))
GROWTH_ALPHA_MIN_AVG_DOLLAR_VOLUME = float(os.getenv("GROWTH_ALPHA_MIN_AVG_DOLLAR_VOLUME", "25000000"))
GROWTH_ALPHA_REQUIRE_SPY_QQQ_ABOVE_MA200 = os.getenv("GROWTH_ALPHA_REQUIRE_SPY_QQQ_ABOVE_MA200", "1") != "0"
GROWTH_ALPHA_REQUIRE_MARKET_SCORE = int(os.getenv("GROWTH_ALPHA_REQUIRE_MARKET_SCORE", "5"))
GROWTH_ALPHA_REQUIRE_MA50 = os.getenv("GROWTH_ALPHA_REQUIRE_MA50", "1") != "0"
GROWTH_ALPHA_REQUIRE_MA200 = os.getenv("GROWTH_ALPHA_REQUIRE_MA200", "1") != "0"
GROWTH_ALPHA_MAX_SINGLE_ASSET_PCT = float(os.getenv("GROWTH_ALPHA_MAX_SINGLE_ASSET_PCT", "0.24"))
GROWTH_ALPHA_MIN_SINGLE_ASSET_PCT = float(os.getenv("GROWTH_ALPHA_MIN_SINGLE_ASSET_PCT", "0.00"))
GROWTH_ALPHA_REBALANCE_DRIFT_THRESHOLD_PCT = float(os.getenv("GROWTH_ALPHA_REBALANCE_DRIFT_THRESHOLD_PCT", "0.015"))
GROWTH_ALPHA_ACTION_DOLLAR_THRESHOLD = float(os.getenv("GROWTH_ALPHA_ACTION_DOLLAR_THRESHOLD", "50"))
GROWTH_ALPHA_MIN_TRADE_DOLLARS = float(os.getenv("GROWTH_ALPHA_MIN_TRADE_DOLLARS", "25"))
GROWTH_ALPHA_QUOTE_DEVIATION_LIMIT = float(os.getenv("GROWTH_ALPHA_QUOTE_DEVIATION_LIMIT", "0.06"))
GROWTH_ALPHA_REQUIRE_LIVE_QUOTE = os.getenv("GROWTH_ALPHA_REQUIRE_LIVE_QUOTE", "1") != "0"
GROWTH_ALPHA_REQUIRE_ACTIVE_PLAN_FOR_BUY = os.getenv("GROWTH_ALPHA_REQUIRE_ACTIVE_PLAN_FOR_BUY", "1") != "0"
GROWTH_ALPHA_ALLOW_FRACTIONAL_SHARES = os.getenv("GROWTH_ALPHA_ALLOW_FRACTIONAL_SHARES", "1") != "0"
GROWTH_ALPHA_PLAN_VALID_DAYS = int(os.getenv("GROWTH_ALPHA_PLAN_VALID_DAYS", "10"))
GROWTH_ALPHA_ALERT_REPEAT_DAYS = int(os.getenv("GROWTH_ALPHA_ALERT_REPEAT_DAYS", "7"))
GROWTH_ALPHA_REVIEW_AFTER_CLOSE_MINUTE = int(os.getenv("GROWTH_ALPHA_REVIEW_AFTER_CLOSE_MINUTE", str(16 * 60 + 12)))
GROWTH_ALPHA_SCORE_SLEEP_SEC = float(os.getenv("GROWTH_ALPHA_SCORE_SLEEP_SEC", "0.01"))

GROWTH_ALPHA_CLUSTER_MAP = {
    # mega_platform
    "AAPL": "mega_platform", "AMZN": "mega_platform", "GOOGL": "mega_platform", "META": "mega_platform",
    "MSFT": "mega_platform",
    # healthcare_defensive
    "ABBV": "healthcare_defensive", "AMGN": "healthcare_defensive", "GILD": "healthcare_defensive", "JNJ": "healthcare_defensive",
    "MRK": "healthcare_defensive",
    # space_growth
    "ACHR": "space_growth", "ASTS": "space_growth", "JOBY": "space_growth", "LUNR": "space_growth",
    "RKLB": "space_growth",
    # enterprise_software
    "ADBE": "enterprise_software", "CRM": "enterprise_software", "HUBS": "enterprise_software", "INTU": "enterprise_software",
    "NOW": "enterprise_software", "ORCL": "enterprise_software", "TEAM": "enterprise_software", "WDAY": "enterprise_software",
    # gold_miners
    "AEM": "gold_miners", "GOLD": "gold_miners", "KGC": "gold_miners", "NEM": "gold_miners",
    # fintech_beta
    "AFRM": "fintech_beta", "HOOD": "fintech_beta", "PYPL": "fintech_beta", "SOFI": "fintech_beta",
    # insurance_broker
    "AJG": "insurance_broker", "AON": "insurance_broker", "BRO": "insurance_broker",
    # semis_ai
    "AMAT": "semis_ai", "AMD": "semis_ai", "ARM": "semis_ai", "ASML": "semis_ai",
    "AVGO": "semis_ai", "KLAC": "semis_ai", "LRCX": "semis_ai", "MPWR": "semis_ai",
    "MRVL": "semis_ai", "MU": "semis_ai", "NVDA": "semis_ai", "NXPI": "semis_ai",
    "ON": "semis_ai", "QCOM": "semis_ai", "SMCI": "semis_ai", "TSM": "semis_ai",
    # industrial_quality
    "AME": "industrial_quality", "DOV": "industrial_quality", "EMR": "industrial_quality", "HON": "industrial_quality",
    "IR": "industrial_quality", "PH": "industrial_quality",
    # digital_infrastructure
    "AMT": "digital_infrastructure", "CCI": "digital_infrastructure",
    # networking_ai
    "ANET": "networking_ai",
    # ai_infrastructure
    "APLD": "ai_infrastructure", "ETN": "ai_infrastructure", "PWR": "ai_infrastructure", "VRT": "ai_infrastructure",
    # software_ai
    "APP": "software_ai", "PLTR": "software_ai",
    # defense_aero
    "AVAV": "defense_aero", "BAH": "defense_aero", "BWXT": "defense_aero", "DRS": "defense_aero",
    "GD": "defense_aero", "HII": "defense_aero", "KTOS": "defense_aero", "LDOS": "defense_aero",
    "LHX": "defense_aero", "LMT": "defense_aero", "MRCY": "defense_aero", "NOC": "defense_aero",
    "RTX": "defense_aero",
    # industrial_tech
    "AXON": "industrial_tech", "GE": "industrial_tech", "URI": "industrial_tech",
    # auto_parts_defensive
    "AZO": "auto_parts_defensive", "ORLY": "auto_parts_defensive",
    # staples_retail
    "BJ": "staples_retail", "CASY": "staples_retail", "COST": "staples_retail", "KR": "staples_retail",
    "SFM": "staples_retail", "WMT": "staples_retail",
    # quality_financial
    "BRK-B": "quality_financial",
    # medtech
    "BSX": "medtech", "SYK": "medtech",
    # discount_retail
    "BURL": "discount_retail", "DG": "discount_retail", "DLTR": "discount_retail", "OLLI": "discount_retail",
    "ROST": "discount_retail", "TJX": "discount_retail",
    # healthcare_services
    "CAH": "healthcare_services", "COR": "healthcare_services", "HCA": "healthcare_services", "MCK": "healthcare_services",
    "THC": "healthcare_services", "UHS": "healthcare_services",
    # industrial_cyclical
    "CAT": "industrial_cyclical", "DE": "industrial_cyclical",
    # insurance_quality
    "CB": "insurance_quality", "PGR": "insurance_quality", "RLI": "insurance_quality", "TRV": "insurance_quality",
    "WRB": "insurance_quality",
    # eda_ai
    "CDNS": "eda_ai", "SNPS": "eda_ai",
    # ai_power
    "CEG": "ai_power", "GEV": "ai_power", "NRG": "ai_power", "TLN": "ai_power",
    "VST": "ai_power",
    # robotics_automation
    "CGNX": "robotics_automation", "FANUY": "robotics_automation", "ROK": "robotics_automation", "TER": "robotics_automation",
    "ZBRA": "robotics_automation",
    # staples
    "CHD": "staples", "CL": "staples", "CLX": "staples", "CPB": "staples",
    "GIS": "staples", "HSY": "staples", "KHC": "staples", "KMB": "staples",
    "KO": "staples", "MDLZ": "staples", "MO": "staples", "PEP": "staples",
    "PG": "staples", "PM": "staples",
    # managed_care
    "CI": "managed_care", "ELV": "managed_care", "HUM": "managed_care", "UNH": "managed_care",
    # waste_services
    "CLH": "waste_services", "CWST": "waste_services", "RSG": "waste_services", "WCN": "waste_services",
    "WM": "waste_services",
    # energy_eandp
    "CNQ": "energy_eandp", "COP": "energy_eandp", "EOG": "energy_eandp", "FANG": "energy_eandp",
    # crypto_beta
    "COIN": "crypto_beta", "MSTR": "crypto_beta",
    # cyber_cloud
    "CRWD": "cyber_cloud", "FTNT": "cyber_cloud", "NET": "cyber_cloud", "PANW": "cyber_cloud",
    "ZS": "cyber_cloud",
    # energy_major
    "CVX": "energy_major", "XOM": "energy_major",
    # cloud_data
    "DDOG": "cloud_data", "MDB": "cloud_data", "SNOW": "cloud_data",
    # life_science_tools
    "DHR": "life_science_tools", "TMO": "life_science_tools",
    # data_center_reit
    "DLR": "data_center_reit", "EQIX": "data_center_reit",
    # materials_quality
    "ECL": "materials_quality", "SHW": "materials_quality",
    # gas
    "EQT": "gas",
    # copper_materials
    "FCX": "copper_materials", "SCCO": "copper_materials",
    # gold_royalty
    "FNV": "gold_royalty", "RGLD": "gold_royalty", "WPM": "gold_royalty",
    # aerospace_quality
    "HEI": "aerospace_quality", "TDG": "aerospace_quality", "TXT": "aerospace_quality",
    # satellite_space
    "IRDM": "satellite_space", "VSAT": "satellite_space",
    # robotics_medtech
    "ISRG": "robotics_medtech",
    # industrial_gas
    "LIN": "industrial_gas",
    # healthcare_growth
    "LLY": "healthcare_growth", "REGN": "healthcare_growth", "VRTX": "healthcare_growth",
    # lng_infrastructure
    "LNG": "lng_infrastructure",
    # commerce_platform
    "MELI": "commerce_platform", "SHOP": "commerce_platform",
    # construction_materials
    "MLM": "construction_materials", "VMC": "construction_materials",
    # refiners
    "MPC": "refiners", "PSX": "refiners", "VLO": "refiners",
    # consumer_platform
    "NFLX": "consumer_platform",
    # nuclear_speculative
    "NNE": "nuclear_speculative", "OKLO": "nuclear_speculative", "SMR": "nuclear_speculative",
    # steel
    "NUE": "steel", "STLD": "steel",
    # midstream
    "OKE": "midstream", "TRGP": "midstream", "WMB": "midstream",
    # silver_miners
    "PAAS": "silver_miners",
    # automation_software
    "PATH": "automation_software",
    # space_speculative
    "SPCE": "space_speculative",
    # robotics_speculative
    "SYM": "robotics_speculative",
    # consumer_growth
    "TSLA": "consumer_growth",
    # adtech_platform
    "TTD": "adtech_platform",
    # mobility_platform
    "UBER": "mobility_platform",
    # water_infrastructure
    "XYL": "water_infrastructure",
}
GROWTH_ALPHA_UNIVERSE = list(dict.fromkeys(GROWTH_ALPHA_CLUSTER_MAP.keys()))

_V310_OLD_INIT_DB = init_db

def init_db() -> None:
    _V310_OLD_INIT_DB()
    conn = db_connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS growth_positions (
                ticker TEXT PRIMARY KEY,
                growth_position_id TEXT NOT NULL UNIQUE,
                strategy_version TEXT NOT NULL,
                shares REAL NOT NULL CHECK (shares > 0),
                avg_entry_price REAL NOT NULL CHECK (avg_entry_price > 0),
                cost_basis REAL NOT NULL CHECK (cost_basis >= 0),
                entry_time REAL NOT NULL,
                last_update_time REAL NOT NULL,
                highest REAL,
                sleeve TEXT NOT NULL DEFAULT 'GROWTH_ALPHA',
                target_account_pct REAL,
                last_plan_id TEXT,
                notes TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS growth_trades (
                id TEXT PRIMARY KEY,
                growth_position_id TEXT,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
                shares REAL NOT NULL CHECK (shares > 0),
                price REAL NOT NULL CHECK (price > 0),
                amount REAL NOT NULL,
                realized_profit REAL,
                time REAL NOT NULL,
                strategy_version TEXT NOT NULL,
                plan_id TEXT,
                reason TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_growth_trades_ticker ON growth_trades(ticker);
            CREATE INDEX IF NOT EXISTS idx_growth_trades_time ON growth_trades(time);
            CREATE TABLE IF NOT EXISTS growth_signals (
                id TEXT PRIMARY KEY,
                time REAL NOT NULL,
                plan_date TEXT NOT NULL,
                market_regime TEXT NOT NULL,
                account_equity REAL NOT NULL,
                growth_target_pct REAL NOT NULL,
                plan_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            );
            CREATE INDEX IF NOT EXISTS idx_growth_signals_time ON growth_signals(time);
            """
        )
        conn.commit()
    finally:
        conn.close()


def row_to_growth_position(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "ticker": row["ticker"], "growth_position_id": row["growth_position_id"],
        "strategy_version": row["strategy_version"], "shares": float(row["shares"]),
        "avg_entry_price": float(row["avg_entry_price"]), "cost_basis": float(row["cost_basis"]),
        "entry_time": float(row["entry_time"]), "last_update_time": float(row["last_update_time"]),
        "highest": None if row["highest"] is None else float(row["highest"]),
        "sleeve": row["sleeve"],
        "target_account_pct": None if row["target_account_pct"] is None else float(row["target_account_pct"]),
        "last_plan_id": row["last_plan_id"], "notes": row["notes"],
    }


def load_growth_positions() -> Dict[str, Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM growth_positions ORDER BY ticker").fetchall()
        return {row["ticker"]: row_to_growth_position(row) for row in rows}
    finally:
        conn.close()


def load_growth_trades() -> List[Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM growth_trades ORDER BY time ASC, created_at ASC").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def load_latest_growth_plan() -> Optional[Dict[str, Any]]:
    conn = db_connect()
    try:
        row = conn.execute("SELECT * FROM growth_signals WHERE status = 'ACTIVE' ORDER BY time DESC LIMIT 1").fetchone()
        if row is None:
            return None
        data = dict(row)
        data["plan"] = json_loads_dict(data.get("plan_json"))
        return data
    finally:
        conn.close()


def save_growth_plan_signal(plan: Dict[str, Any]) -> str:
    plan_id = str(plan.get("plan_id") or uuid.uuid4().hex)
    plan = dict(plan)
    plan["plan_id"] = plan_id
    with db_tx() as conn:
        conn.execute("UPDATE growth_signals SET status = 'SUPERSEDED' WHERE status = 'ACTIVE'")
        conn.execute(
            """
            INSERT INTO growth_signals(id, time, plan_date, market_regime, account_equity,
                                       growth_target_pct, plan_json, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
            """,
            (plan_id, now_ts(), ny_date_str(), str(plan.get("market", "UNKNOWN")),
             float(plan.get("account_equity", 0) or 0),
             float(plan.get("target_growth_account_pct", 0) or 0), json_dumps(plan)),
        )
    return plan_id


def growth_alpha_market_filter_ok(market_details: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    try:
        details = market_details or market_regime_details()
        if int(details.get("score", 0) or 0) < GROWTH_ALPHA_REQUIRE_MARKET_SCORE:
            return False, "market score below threshold"
        if GROWTH_ALPHA_REQUIRE_SPY_QQQ_ABOVE_MA200:
            frames = details.get("frames", {}) if isinstance(details.get("frames"), dict) else {}
            for symbol in ["SPY", "QQQ"]:
                df = frames.get(symbol) or get_signal_dataframe(symbol, limit=260)
                last, ma = frame_last_close_ma(df, 200)
                if last is None or ma is None or last <= ma:
                    return False, f"{symbol} below MA200"
        return True, "OK"
    except Exception as exc:
        return False, f"market filter error: {exc}"


def growth_alpha_score_ticker(ticker: str) -> Optional[Dict[str, Any]]:
    try:
        df = get_historical(ticker, limit=280)
        if df is None or df.empty or len(df) < 210:
            return None
        close = df["Close"]
        volume = df["Volume"]
        price = float(close.iloc[-1])
        if price < GROWTH_ALPHA_MIN_PRICE:
            return None
        ma50 = float(close.rolling(50).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])
        if GROWTH_ALPHA_REQUIRE_MA200 and price <= ma200:
            return None
        if GROWTH_ALPHA_REQUIRE_MA50 and price <= ma50:
            return None
        roc21 = pct_change_last(df, 21)
        roc63 = pct_change_last(df, 63)
        roc126 = pct_change_last(df, 126)
        vol63v = realized_vol_last(df, 63)
        avg_dv = float((close * volume).rolling(20).mean().iloc[-1])
        if roc21 is None or roc63 is None or roc126 is None or vol63v is None:
            return None
        if avg_dv < GROWTH_ALPHA_MIN_AVG_DOLLAR_VOLUME:
            return None
        if roc21 < -0.08 or roc63 <= 0 or roc126 <= 0:
            return None
        score = (0.45 * roc126) + (0.35 * roc63) + (0.20 * roc21) - (0.10 * vol63v)
        if score <= 0:
            return None
        cluster = GROWTH_ALPHA_CLUSTER_MAP.get(ticker, "other")
        inv_vol = 1.0 / max(float(vol63v), 0.08)
        weight_score = inv_vol * max(0.0001, score + 0.10)
        return {
            "ticker": ticker, "cluster": cluster, "price": round(price, 2),
            "ma50": round(ma50, 2), "ma200": round(ma200, 2),
            "roc_1m_pct": round(roc21 * 100, 2), "roc_3m_pct": round(roc63 * 100, 2),
            "roc_6m_pct": round(roc126 * 100, 2), "vol_3m_pct": round(vol63v * 100, 2),
            "avg_dollar_volume": round(avg_dv, 2), "score": round(float(score), 6),
            "weight_score": round(float(weight_score), 6),
        }
    except Exception as exc:
        print(f"[GROWTH SCORE ERROR] {ticker}: {exc}")
        return None


def select_growth_alpha_assets(scored: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    cluster_counts: Dict[str, int] = {}
    for item in scored:
        cluster = str(item.get("cluster", "other"))
        if cluster_counts.get(cluster, 0) >= GROWTH_ALPHA_MAX_PER_CLUSTER:
            continue
        selected.append(item)
        cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
        if len(selected) >= GROWTH_ALPHA_TOP_N:
            break
    return selected


def assign_growth_alpha_weights(top: List[Dict[str, Any]], growth_account_pct: float) -> List[Dict[str, Any]]:
    if not top:
        return []
    raw = [max(0.0001, float(x.get("weight_score", 0.0001) or 0.0001)) for x in top]
    total = sum(raw)
    weights = [x / total for x in raw] if total > 0 else [1.0 / len(top)] * len(top)
    cap = clamp_float(GROWTH_ALPHA_MAX_SINGLE_ASSET_PCT, 0.05, 0.80)
    floor = clamp_float(GROWTH_ALPHA_MIN_SINGLE_ASSET_PCT, 0.0, cap)
    weights = [min(cap, max(0.0, w)) for w in weights]
    total = sum(weights)
    if total > 0:
        weights = [w / total for w in weights]
    if floor > 0 and floor * len(weights) <= 0.90:
        weights = [max(floor, w) for w in weights]
        total = sum(weights)
        if total > 0:
            weights = [w / total for w in weights]
    enriched = []
    for item, sleeve_weight in zip(top, weights):
        row = dict(item)
        row["target_growth_pct"] = round(sleeve_weight * 100, 2)
        row["target_account_pct"] = round(sleeve_weight * growth_account_pct * 100, 2)
        enriched.append(row)
    return enriched


def growth_position_market_value_details(prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    positions = load_growth_positions() if GROWTH_ALPHA_LEDGER_ENABLED else {}
    tickers = list(positions.keys())
    prices = prices or get_prices_batch(tickers)
    rows: List[Dict[str, Any]] = []
    total_value = 0.0
    total_cost = 0.0
    total_unrealized = 0.0
    for ticker, pos in positions.items():
        mark = float(prices.get(ticker, pos.get("avg_entry_price", 0)) or pos.get("avg_entry_price", 0))
        shares = float(pos.get("shares", 0) or 0)
        value = shares * mark
        cost = float(pos.get("cost_basis", 0) or 0)
        unrealized = value - cost
        total_value += value
        total_cost += cost
        total_unrealized += unrealized
        rows.append({**pos, "mark_price": round(mark, 4), "market_value": round(value, 2),
                     "unrealized_profit": round(unrealized, 2),
                     "unrealized_pct": None if cost <= 0 else round((unrealized / cost) * 100, 2)})
    realized = sum(float(t.get("realized_profit") or 0.0) for t in load_growth_trades() if str(t.get("side")).upper() == "SELL")
    return {"positions": positions, "rows": rows, "value": round(total_value, 2),
            "cost_basis": round(total_cost, 2), "unrealized_profit": round(total_unrealized, 2),
            "realized_profit": round(realized, 2), "total_profit": round(realized + total_unrealized, 2)}


def compute_growth_alpha_plan() -> Dict[str, Any]:
    refresh_portfolio()
    allocation = dynamic_portfolio_allocation_targets()
    risk = allocation.get("risk_guard", {}) or {}
    market_details = market_regime_details()
    market_ok, market_reason = growth_alpha_market_filter_ok(market_details=market_details)
    growth_pct = float(allocation.get("growth_alpha_pct", GROWTH_ALPHA_ACCOUNT_ALLOC_PCT * 100) or 0.0) / 100.0
    if not GROWTH_ALPHA_ENABLED or risk.get("hard_active") or not market_ok:
        growth_pct = 0.0
    account_equity = float(compute_equity_snapshot_data().get("equity", 0.0) or 0.0)
    sleeve_value = account_equity * growth_pct
    scored = []
    if GROWTH_ALPHA_ENABLED and growth_pct > 0:
        for idx, ticker in enumerate(GROWTH_ALPHA_UNIVERSE, start=1):
            item = growth_alpha_score_ticker(ticker)
            if item is not None:
                scored.append(item)
            if GROWTH_ALPHA_SCORE_SLEEP_SEC > 0 and idx % 20 == 0:
                time.sleep(GROWTH_ALPHA_SCORE_SLEEP_SEC)
    scored = sorted(scored, key=lambda x: float(x.get("score", -999)), reverse=True)
    selected = select_growth_alpha_assets(scored)
    top = assign_growth_alpha_weights(selected, growth_pct)
    details = growth_position_market_value_details()
    current_rows = {str(r.get("ticker", "")).upper(): r for r in details.get("rows", [])}
    actions: List[Dict[str, Any]] = []
    for rank, item in enumerate(top, start=1):
        ticker = str(item["ticker"]).upper()
        target_value = account_equity * (float(item.get("target_account_pct", 0) or 0) / 100.0)
        current_value = float(current_rows.get(ticker, {}).get("market_value", 0.0) or 0.0)
        drift = target_value - current_value
        threshold = max(GROWTH_ALPHA_ACTION_DOLLAR_THRESHOLD, account_equity * GROWTH_ALPHA_REBALANCE_DRIFT_THRESHOLD_PCT)
        if current_value <= 0 and target_value >= GROWTH_ALPHA_ACTION_DOLLAR_THRESHOLD:
            action = "BUY"
        elif drift >= threshold:
            action = "ADD"
        elif drift <= -threshold:
            action = "TRIM"
        else:
            action = "HOLD"
        actions.append({"rank": rank, "ticker": ticker, "action": action, "cluster": item.get("cluster"),
                        "score": item.get("score"), "price": item.get("price"),
                        "target_account_pct": item.get("target_account_pct"), "target_growth_pct": item.get("target_growth_pct"),
                        "target_value": round(target_value, 2), "current_value": round(current_value, 2),
                        "suggested_dollars": round(abs(drift), 2), "drift_dollars": round(drift, 2),
                        "roc_1m_pct": item.get("roc_1m_pct"), "roc_3m_pct": item.get("roc_3m_pct"),
                        "roc_6m_pct": item.get("roc_6m_pct"), "vol_3m_pct": item.get("vol_3m_pct")})
    selected_tickers = {str(x.get("ticker", "")).upper() for x in top}
    scored_map = {str(x.get("ticker", "")).upper(): x for x in scored}
    for ticker, row in current_rows.items():
        if ticker in selected_tickers:
            continue
        reason = "Dropped out of selected Growth Alpha top list."
        if not market_ok:
            reason = f"Growth market filter failed: {market_reason}."
        actions.append({"rank": None, "ticker": ticker, "action": "SELL", "cluster": None if scored_map.get(ticker) is None else scored_map[ticker].get("cluster"),
                        "score": None if scored_map.get(ticker) is None else scored_map[ticker].get("score"),
                        "price": row.get("mark_price"), "target_account_pct": 0.0, "target_growth_pct": 0.0,
                        "target_value": 0.0, "current_value": round(float(row.get("market_value", 0) or 0), 2),
                        "suggested_dollars": round(float(row.get("market_value", 0) or 0), 2), "reason": reason})
    actionable = [a for a in actions if str(a.get("action")).upper() in {"BUY", "ADD", "TRIM", "SELL"}]
    return {"plan_id": uuid.uuid4().hex, "strategy_version": "growth_alpha_v4_expanded_future_growth",
            "private_only": True, "ny_time": ny_now().strftime("%Y-%m-%d %H:%M %Z"),
            "market": market_details.get("condition", "UNKNOWN"), "market_score": market_details.get("score"),
            "market_ok": market_ok, "market_reason": market_reason,
            "target_growth_account_pct": round(growth_pct * 100, 2), "target_growth_value": round(sleeve_value, 2),
            "current_growth_value": round(float(details.get("value", 0) or 0), 2),
            "current_growth_cost_basis": round(float(details.get("cost_basis", 0) or 0), 2),
            "current_growth_unrealized_profit": round(float(details.get("unrealized_profit", 0) or 0), 2),
            "account_equity": round(account_equity, 2), "allocation": allocation, "risk_guard": risk,
            "top": top, "actions": actions, "actionable": actionable, "all_scored": scored[:100],
            "universe_size": len(GROWTH_ALPHA_UNIVERSE), "scored_count": len(scored), "top_n": GROWTH_ALPHA_TOP_N}


def latest_growth_plan_action_map(plan: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {str(i.get("ticker", "")).upper(): i for i in plan.get("actions", []) or [] if i.get("ticker")}


def growth_target_for_ticker(plan: Dict[str, Any], ticker: str) -> Optional[Dict[str, Any]]:
    ticker = ticker.upper()
    for item in plan.get("top", []) or []:
        if str(item.get("ticker", "")).upper() == ticker:
            return item
    return None


def current_growth_plan_for_validation() -> Dict[str, Any]:
    plan = compute_growth_alpha_plan()
    save_growth_plan_signal(plan)
    return plan


def validate_growth_price_against_quote(ticker: str, price: float) -> Tuple[bool, str, Optional[float]]:
    if not GROWTH_ALPHA_REQUIRE_LIVE_QUOTE:
        return True, "Quote check disabled", None
    quote = get_prices_batch([ticker]).get(ticker)
    if quote is None or quote <= 0:
        return False, "Live quote unavailable for Growth Alpha trade.", None
    deviation = abs(price - quote) / quote
    if deviation > GROWTH_ALPHA_QUOTE_DEVIATION_LIMIT:
        return False, (f"Growth trade rejected: price too far from live quote.\n"
                       f"Live quote: {round(quote, 2)}\nYour price: {round(price, 2)}\n"
                       f"Max deviation: {round(GROWTH_ALPHA_QUOTE_DEVIATION_LIMIT * 100, 2)}%"), quote
    return True, "OK", quote


def record_growth_buy(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
    ticker = normalize_ticker(ticker) or ""
    if not ticker:
        return False, "Invalid ticker"
    if not GROWTH_ALPHA_LEDGER_ENABLED:
        return False, "Growth Alpha ledger is disabled."
    if ticker not in GROWTH_ALPHA_UNIVERSE:
        return False, f"{ticker} is not in the Growth Alpha universe."
    if shares <= 0 or not math.isfinite(shares):
        return False, "Growth Alpha shares must be positive and finite."
    if (not GROWTH_ALPHA_ALLOW_FRACTIONAL_SHARES) and abs(shares - round(shares)) > 1e-9:
        return False, "Fractional Growth Alpha shares are disabled."
    if not is_finite_positive(price):
        return False, "Growth Alpha price must be positive and finite."
    amount = shares * price
    if amount < GROWTH_ALPHA_MIN_TRADE_DOLLARS:
        return False, f"Growth trade amount is below minimum {format_money(GROWTH_ALPHA_MIN_TRADE_DOLLARS)}."
    plan = current_growth_plan_for_validation()
    target = growth_target_for_ticker(plan, ticker)
    action = latest_growth_plan_action_map(plan).get(ticker)
    if GROWTH_ALPHA_REQUIRE_ACTIVE_PLAN_FOR_BUY and target is None:
        allowed = ", ".join(str(x.get("ticker")) for x in plan.get("top", [])[:GROWTH_ALPHA_TOP_N])
        return False, f"Growth buy rejected: {ticker} is not in active Growth Alpha plan. Current top: {allowed or 'none'}"
    if action and str(action.get("action", "")).upper() in {"TRIM", "SELL", "AVOID"}:
        return False, f"Growth buy rejected: current plan action for {ticker} is {action.get('action')}."
    ok, msg, quote = validate_growth_price_against_quote(ticker, price)
    if not ok:
        return False, msg
    with db_tx() as conn:
        cash = get_cash(conn)
        if amount > cash:
            mark_update_processed_tx(conn, update_id, "rejected_growth_insufficient_cash")
            return False, "Not enough cash for Growth Alpha buy."
        row = conn.execute("SELECT * FROM growth_positions WHERE ticker = ?", (ticker,)).fetchone()
        now = now_ts()
        target_pct = None if target is None else float(target.get("target_account_pct", 0) or 0)
        plan_id = str(plan.get("plan_id"))
        if row is None:
            growth_position_id = f"GROWTH_{ticker}_{int(now)}_{uuid.uuid4().hex[:8]}"
            conn.execute(
                """
                INSERT INTO growth_positions(ticker, growth_position_id, strategy_version, shares, avg_entry_price,
                                             cost_basis, entry_time, last_update_time, highest, sleeve,
                                             target_account_pct, last_plan_id, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'GROWTH_ALPHA', ?, ?, '')
                """,
                (ticker, growth_position_id, "growth_alpha_v4_expanded_future_growth", round(shares, 8),
                 round(price, 6), round(amount, 6), now, now, round(price, 6), target_pct, plan_id),
            )
        else:
            pos = row_to_growth_position(row)
            growth_position_id = pos["growth_position_id"]
            new_shares = float(pos["shares"]) + shares
            new_cost = float(pos["cost_basis"]) + amount
            avg_price = new_cost / new_shares
            highest = max(float(pos.get("highest") or price), price)
            conn.execute(
                """
                UPDATE growth_positions SET shares = ?, avg_entry_price = ?, cost_basis = ?, last_update_time = ?,
                                            highest = ?, target_account_pct = ?, last_plan_id = ?, strategy_version = ?
                WHERE ticker = ?
                """,
                (round(new_shares, 8), round(avg_price, 6), round(new_cost, 6), now, round(highest, 6),
                 target_pct, plan_id, "growth_alpha_v4_expanded_future_growth", ticker),
            )
        conn.execute(
            """
            INSERT INTO growth_trades(id, growth_position_id, ticker, side, shares, price, amount, realized_profit,
                                      time, strategy_version, plan_id, reason, created_at)
            VALUES (?, ?, ?, 'BUY', ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (uuid.uuid4().hex, growth_position_id, ticker, round(shares, 8), round(price, 6), round(amount, 6),
             now, "growth_alpha_v4_expanded_future_growth", plan_id, "growth_plan_buy", now),
        )
        set_cash_tx(conn, cash - amount)
        mark_update_processed_tx(conn, update_id, "processed_growth_buy")
    refresh_portfolio()
    audit("GROWTH_BUY", f"{ticker} shares={shares} price={price} amount={amount}")
    return True, (f"🚀 GROWTH_ALPHA BUY RECORDED {ticker}\n\n"
                  f"📦 Shares: {format_core_shares(shares)}\n💵 Price: {round(price, 2)}\n"
                  f"💰 Amount: {format_money(amount)}\n🎯 Plan action: {None if action is None else action.get('action')}\n"
                  f"📐 Target account weight: {None if target is None else target.get('target_account_pct')}%\n"
                  f"💵 Cash left: {format_money(portfolio['cash'])}")


def record_growth_sell(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
    ticker = normalize_ticker(ticker) or ""
    if not ticker:
        return False, "Invalid ticker"
    if not GROWTH_ALPHA_LEDGER_ENABLED:
        return False, "Growth Alpha ledger is disabled."
    if shares <= 0 or not math.isfinite(shares):
        return False, "Growth Alpha shares must be positive and finite."
    if not is_finite_positive(price):
        return False, "Growth Alpha price must be positive and finite."
    ok, msg, quote = validate_growth_price_against_quote(ticker, price)
    if not ok:
        return False, msg
    plan = current_growth_plan_for_validation()
    action = latest_growth_plan_action_map(plan).get(ticker)
    with db_tx() as conn:
        row = conn.execute("SELECT * FROM growth_positions WHERE ticker = ?", (ticker,)).fetchone()
        if row is None:
            mark_update_processed_tx(conn, update_id, "rejected_growth_no_position")
            return False, "No Growth Alpha position to sell."
        pos = row_to_growth_position(row)
        current_shares = float(pos["shares"])
        if shares - current_shares > CORE_POSITION_EPSILON:
            mark_update_processed_tx(conn, update_id, "rejected_growth_too_many_shares")
            return False, f"You only have {format_core_shares(current_shares)} Growth Alpha shares of {ticker}."
        shares = min(shares, current_shares)
        avg = float(pos["avg_entry_price"])
        proceeds = shares * price
        realized_profit = (price - avg) * shares
        remaining = current_shares - shares
        now = now_ts()
        plan_id = str(plan.get("plan_id"))
        conn.execute(
            """
            INSERT INTO growth_trades(id, growth_position_id, ticker, side, shares, price, amount, realized_profit,
                                      time, strategy_version, plan_id, reason, created_at)
            VALUES (?, ?, ?, 'SELL', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uuid.uuid4().hex, pos["growth_position_id"], ticker, round(shares, 8), round(price, 6),
             round(proceeds, 6), round(realized_profit, 6), now, "growth_alpha_v4_expanded_future_growth",
             plan_id, "growth_plan_sell", now),
        )
        if remaining <= CORE_POSITION_EPSILON:
            conn.execute("DELETE FROM growth_positions WHERE ticker = ?", (ticker,))
        else:
            conn.execute("UPDATE growth_positions SET shares = ?, cost_basis = ?, last_update_time = ?, last_plan_id = ? WHERE ticker = ?",
                         (round(remaining, 8), round(avg * remaining, 6), now, plan_id, ticker))
        cash = get_cash(conn)
        set_cash_tx(conn, cash + proceeds)
        mark_update_processed_tx(conn, update_id, "processed_growth_sell")
    refresh_portfolio()
    audit("GROWTH_SELL", f"{ticker} shares={shares} price={price} proceeds={proceeds} profit={realized_profit}")
    return True, (f"🚀 GROWTH_ALPHA SELL RECORDED {ticker}\n\n"
                  f"📦 Shares: {format_core_shares(shares)}\n💵 Price: {round(price, 2)}\n"
                  f"💰 Proceeds: {format_money(proceeds)}\n📊 Realized Growth P/L: {format_money(realized_profit)} "
                  f"({format_pct((price - avg) / avg * 100 if avg > 0 else None)})\n"
                  f"🎯 Plan action: {None if action is None else action.get('action')}\n💵 Cash now: {format_money(portfolio['cash'])}")


_V310_OLD_DYNAMIC = dynamic_portfolio_allocation_targets

def dynamic_portfolio_allocation_targets() -> Dict[str, Any]:
    base = _V310_OLD_DYNAMIC()
    market = str(base.get("market", "UNCERTAIN"))
    risk = base.get("risk_guard", {}) or {}
    bear_score = int(base.get("bear_score", 0) or 0)
    if risk.get("hard_active"):
        core, growth, spec, long_vcp, bear = 25.0, 0.0, 0.0, 0.0, 0.0
    elif market == "BULL":
        core, growth, spec, long_vcp, bear = 25.0, 45.0, 20.0, 10.0, 0.0
    elif market == "BEAR":
        core, growth, spec, long_vcp, bear = 25.0, 0.0, 0.0, 0.0, 10.0 if BEAR_SLEEVE_ENABLED else 0.0
    else:
        core, growth, spec, long_vcp = 25.0, 20.0, 10.0, 5.0
        bear = 5.0 if BEAR_SLEEVE_ENABLED and bear_score >= BEAR_EXIT_SCORE else 0.0
    if risk.get("soft_active") and not risk.get("hard_active"):
        reduced_growth = growth * 0.50
        reduced_spec = spec * 0.50
        reduced_long = long_vcp * 0.50
        reduced_bear = bear * 0.75
        growth, spec, long_vcp, bear = reduced_growth, reduced_spec, reduced_long, reduced_bear
    if not GROWTH_ALPHA_ENABLED:
        growth = 0.0
    if not SPEC_ALPHA_ENABLED:
        spec = 0.0
    cash = max(0.0, 100.0 - core - growth - spec - long_vcp - bear)
    base["strategy_version"] = "v4_expanded_growth_25_45_20_10_dynamic_allocation"
    base["core_wealth_pct"] = round(core, 2)
    base["growth_alpha_pct"] = round(growth, 2)
    base["spec_alpha_pct"] = round(spec, 2)
    base["long_vcp_tactical_pct"] = round(long_vcp, 2)
    base["bear_inverse_tactical_pct"] = round(bear, 2)
    base["cash_reserve_pct"] = round(cash, 2)
    return base


_V310_OLD_COMPUTE_EQUITY = compute_equity_snapshot_data

def compute_equity_snapshot_data() -> Dict[str, float]:
    snapshot = _V310_OLD_COMPUTE_EQUITY()
    growth_positions = load_growth_positions() if GROWTH_ALPHA_LEDGER_ENABLED else {}
    prices = get_prices_batch(list(growth_positions.keys()))
    growth_value = 0.0
    growth_cost = 0.0
    for ticker, pos in growth_positions.items():
        price = prices.get(ticker, pos.get("avg_entry_price", 0))
        growth_value += float(price) * float(pos["shares"])
        growth_cost += float(pos.get("cost_basis", 0) or 0)
    snapshot["growth_alpha_positions_value"] = round(growth_value, 2)
    snapshot["growth_alpha_cost_basis"] = round(growth_cost, 2)
    snapshot["growth_alpha_unrealized_profit"] = round(growth_value - growth_cost, 2)
    snapshot["positions_value"] = round(float(snapshot.get("positions_value", 0) or 0) + growth_value, 2)
    snapshot["equity"] = round(float(snapshot.get("equity", 0) or 0) + growth_value, 2)
    return snapshot


_V310_OLD_REALIZED = realized_performance_all_time

def realized_performance_all_time() -> Dict[str, Any]:
    perf = _V310_OLD_REALIZED()
    growth_trades = load_growth_trades() if GROWTH_ALPHA_LEDGER_ENABLED else []
    growth_profit = round(sum(float(t.get("realized_profit") or 0.0) for t in growth_trades if str(t.get("side")).upper() == "SELL"), 2)
    perf["growth_realized_profit"] = growth_profit
    perf["profit"] = round(float(perf.get("profit", 0) or 0) + growth_profit, 2)
    base = float(perf.get("base_capital", 0) or 0)
    perf["pct"] = None if base <= 0 else (perf["profit"] / base) * 100
    perf["growth_trade_records"] = len(growth_trades)
    perf["trade_records"] = int(perf.get("trade_records", 0) or 0) + len(growth_trades)
    return perf


def format_growth_alpha_plan(plan: Dict[str, Any]) -> str:
    msg = (
        "🚀 EXPANDED GROWTH_ALPHA MONTHLY LEADER ROTATION PLAN v4\n\n"
        "Private execution plan. Execute in broker first, then record with growthbuy/growthsell.\n\n"
        f"🕒 NY time: {plan.get('ny_time')}\n"
        f"🌎 Market: {market_label(str(plan.get('market', 'UNKNOWN')))} ({plan.get('market_score')}/8)\n"
        f"🚦 Market filter OK: {yes_no(bool(plan.get('market_ok')))} — {plan.get('market_reason')}\n"
        f"💼 Equity estimate: {format_money(float(plan.get('account_equity', 0) or 0))}\n"
        f"🚀 Target Growth sleeve: {plan.get('target_growth_account_pct')}% = {format_money(float(plan.get('target_growth_value', 0) or 0))}\n"
        f"📦 Current Growth value: {format_money(float(plan.get('current_growth_value', 0) or 0))}\n"
        f"📈 Growth unrealized P/L: {format_money(float(plan.get('current_growth_unrealized_profit', 0) or 0))}\n"
        f"🧪 Universe/scored: {plan.get('universe_size')} / {plan.get('scored_count')}\n"
        f"🎚️ Top N: {plan.get('top_n')} | Max per cluster: {GROWTH_ALPHA_MAX_PER_CLUSTER}\n\n"
    )
    ranked = [a for a in plan.get("actions", []) or [] if a.get("rank") is not None]
    exits = [a for a in plan.get("actions", []) or [] if str(a.get("action")).upper() == "SELL"]
    if ranked:
        msg += "🎯 Ranked Growth Alpha candidates — best to least attractive\n"
        for item in ranked[:GROWTH_ALPHA_TOP_N]:
            action = str(item.get("action", "HOLD")).upper()
            verb = {"BUY": "🟢 BUY", "ADD": "🟢 ADD", "HOLD": "🟡 HOLD", "TRIM": "🟠 TRIM"}.get(action, action)
            msg += (
                f"{item.get('rank')}) {verb} {item['ticker']} ({item.get('cluster', 'other')})\n"
                f"   Target: {item.get('target_account_pct')}% acct / {format_money(float(item.get('target_value', 0) or 0))}\n"
                f"   Current: {format_money(float(item.get('current_value', 0) or 0))} | Action size: ~{format_money(float(item.get('suggested_dollars', 0) or 0))}\n"
                f"   Price: {item.get('price')} | 1m {format_pct(item.get('roc_1m_pct'))} | 3m {format_pct(item.get('roc_3m_pct'))} | 6m {format_pct(item.get('roc_6m_pct'))}\n"
                f"   Vol: {item.get('vol_3m_pct')}% | Score: {item.get('score')}\n"
            )
        msg += "\n"
    if exits:
        msg += "🔴 Growth Alpha exit / rotation candidates\n"
        for item in exits:
            msg += f"SELL {item['ticker']} — current {format_money(float(item.get('current_value', 0) or 0))}\nReason: {item.get('reason', 'No longer selected')}\n"
        msg += "\n"
    msg += (
        "How to execute after broker fill:\n"
        "• growthbuy TICKER SHARES at PRICE\n"
        "• growthsell TICKER SHARES at PRICE\n\n"
        "Growth rules:\n"
        "• Monthly high-growth leader rotation, not a swing stop system.\n"
        "• Separate Growth ledger; do not use bought/sold, corebuy, or specbuy.\n"
        "• Cluster caps are active to reduce one-sector dependence."
    )
    return msg[:MAX_TELEGRAM_MESSAGE]


def format_growth_portfolio_report() -> str:
    details = growth_position_market_value_details()
    rows = details.get("rows", []) or []
    snapshot = compute_equity_snapshot_data()
    msg = (f"🚀 GROWTH_ALPHA PORTFOLIO\n\n"
           f"💵 Shared cash: {format_money(snapshot['cash'])}\n"
           f"🚀 Growth value: {format_money(float(details.get('value', 0) or 0))}\n"
           f"📏 Cost basis: {format_money(float(details.get('cost_basis', 0) or 0))}\n"
           f"📈 Unrealized P/L: {format_money(float(details.get('unrealized_profit', 0) or 0))}\n"
           f"✅ Realized Growth P/L: {format_money(float(details.get('realized_profit', 0) or 0))}\n"
           f"💼 Total equity: {format_money(snapshot['equity'])}\n\n")
    if not rows:
        return msg + "No Growth Alpha positions recorded yet. Use growthplan, then growthbuy after broker execution."
    for row in rows:
        msg += (f"📦 {row['ticker']}\n"
                f"Shares: {format_core_shares(row['shares'])}\n"
                f"Avg: {round(float(row['avg_entry_price']), 2)} | Now: {round(float(row['mark_price']), 2)}\n"
                f"Value: {format_money(float(row['market_value']))}\n"
                f"P/L: {format_money(float(row['unrealized_profit']))} ({format_pct(row.get('unrealized_pct'))})\n"
                f"Target account weight: {row.get('target_account_pct')}%\n\n")
    return msg[:MAX_TELEGRAM_MESSAGE]


def format_growth_pnl_report() -> str:
    details = growth_position_market_value_details()
    trades = load_growth_trades()
    buys = [t for t in trades if str(t.get("side")).upper() == "BUY"]
    sells = [t for t in trades if str(t.get("side")).upper() == "SELL"]
    return (f"🚀 GROWTH_ALPHA P/L\n\n"
            f"🚀 Growth value: {format_money(float(details.get('value', 0) or 0))}\n"
            f"📏 Cost basis: {format_money(float(details.get('cost_basis', 0) or 0))}\n"
            f"📈 Unrealized P/L: {format_money(float(details.get('unrealized_profit', 0) or 0))}\n"
            f"✅ Realized P/L: {format_money(float(details.get('realized_profit', 0) or 0))}\n"
            f"💰 Total Growth P/L: {format_money(float(details.get('total_profit', 0) or 0))}\n\n"
            f"Buy records: {len(buys)}\nSell records: {len(sells)}")


def format_growth_exposure_report() -> str:
    snapshot = compute_equity_snapshot_data()
    details = growth_position_market_value_details()
    equity = float(snapshot.get("equity", 0) or 0)
    alloc = dynamic_portfolio_allocation_targets()
    target_pct = float(alloc.get("growth_alpha_pct", 0) or 0)
    actual_pct = 0.0 if equity <= 0 else (float(details.get("value", 0) or 0) / equity) * 100
    return (f"🚀 GROWTH_ALPHA EXPOSURE\n\n"
            f"💼 Total equity: {format_money(equity)}\n"
            f"🚀 Growth value: {format_money(float(details.get('value', 0) or 0))}\n"
            f"🎯 Target Growth: {round(target_pct, 2)}% of account\n"
            f"📊 Actual Growth: {round(actual_pct, 2)}% of account\n"
            f"📐 Drift: {round(actual_pct - target_pct, 2)} percentage points\n\n"
            "Use growthplan for ranked BUY/ADD/HOLD/TRIM/SELL actions.")


_V310_OLD_PORTFOLIO_REPORT = format_combined_portfolio_report

def format_combined_portfolio_report() -> str:
    base_msg = _V310_OLD_PORTFOLIO_REPORT()
    rows = growth_position_market_value_details().get("rows", []) if GROWTH_ALPHA_LEDGER_ENABLED else []
    if not rows:
        return base_msg.replace("No open swing, core, or SPEC positions", "No open swing, core, SPEC, or Growth Alpha positions")
    msg = base_msg + "\n\n🚀 GROWTH_ALPHA POSITIONS\n\n"
    for row in rows:
        msg += (f"📦 {row['ticker']}\n"
                f"Shares: {format_core_shares(row['shares'])}\n"
                f"Avg: {round(float(row['avg_entry_price']), 2)} | Now: {round(float(row['mark_price']), 2)}\n"
                f"Value: {format_money(float(row['market_value']))}\n"
                f"P/L: {format_money(float(row['unrealized_profit']))} ({format_pct(row.get('unrealized_pct'))})\n\n")
    return msg[:MAX_TELEGRAM_MESSAGE]


def format_portfolio_allocation_plan() -> str:
    plan = dynamic_portfolio_allocation_targets()
    risk = plan.get("risk_guard", {}) or {}
    return (
        "🏛️ INSTITUTIONAL ALLOCATION PLAN v4 EXPANDED GROWTH 25/45/20/10\n\n"
        "Private bot only. This is portfolio guidance, not an automatic trade.\n\n"
        f"🕒 NY time: {plan.get('ny_time')}\n"
        f"🌎 Market: {market_label(str(plan.get('market', 'UNKNOWN')))} ({plan.get('market_score')}/8)\n"
        f"🐻 Bear pressure score: {plan.get('bear_score')}/60\n"
        f"🛡️ Risk guard: {risk.get('recommended_action')}\n"
        f"📉 Current DD: {risk.get('drawdown_pct')}% from {format_money(float(risk.get('high_equity', 0) or 0))}\n\n"
        "Target account buckets:\n"
        f"🏦 Core UCITS/USD rotation: {plan.get('core_wealth_pct')}%\n"
        f"🚀 Growth Alpha rotation: {plan.get('growth_alpha_pct')}%\n"
        f"⚡ SPEC_ALPHA rotation: {plan.get('spec_alpha_pct')}%\n"
        f"🐂 Long VCP tactical: {plan.get('long_vcp_tactical_pct')}%\n"
        f"🐻 Bear stock tactical: {plan.get('bear_inverse_tactical_pct')}%\n"
        f"💵 Cash reserve: {plan.get('cash_reserve_pct')}%\n\n"
        "Rules:\n"
        "• Core uses researched USD-priced UCITS/ETP universe.\n"
        "• Growth Alpha is monthly high-growth leader rotation with cluster caps.\n"
        "• SPEC_ALPHA remains monthly medium/weak momentum.\n"
        "• VCP/Bear-stock remains signal-driven tactical.\n"
        "• Options remain research-only and are not live-integrated."
    )


def maybe_send_growth_alpha_signal() -> None:
    if not GROWTH_ALPHA_ENABLED:
        return
    current_ny = ny_now()
    minutes = current_ny.hour * 60 + current_ny.minute
    if is_market_weekday(current_ny) and minutes < GROWTH_ALPHA_REVIEW_AFTER_CLOSE_MINUTE:
        return
    month_key = current_ny.strftime("%Y-%m")
    if get_meta("last_growth_alpha_month") == month_key:
        return
    last_raw = get_meta("last_growth_alpha_alert_ts")
    if last_raw:
        try:
            days_since = (now_ts() - float(last_raw)) / 86400
            if days_since < GROWTH_ALPHA_ALERT_REPEAT_DAYS:
                return
        except ValueError:
            pass
    try:
        plan = compute_growth_alpha_plan()
        save_growth_plan_signal(plan)
        set_meta("last_growth_alpha_month", month_key)
        set_meta("last_growth_alpha_alert_ts", str(now_ts()))
        send(format_growth_alpha_plan(plan))
        audit("GROWTH_ALPHA_SIGNAL", f"month={month_key} top={[x.get('ticker') for x in plan.get('top', [])]}")
    except Exception as exc:
        logger.exception(f"[GROWTH ALPHA SIGNAL ERROR] {exc}")
        print(f"[GROWTH ALPHA SIGNAL ERROR] {exc}")


_V310_OLD_MAYBE_SEND_MONTHLY = maybe_send_wealth_core_signal

def maybe_send_wealth_core_signal() -> None:
    _V310_OLD_MAYBE_SEND_MONTHLY()
    maybe_send_growth_alpha_signal()


_V310_OLD_EXPORT_STATE_BUNDLE = export_state_bundle

def export_state_bundle(prefix: str = "bot_state_export") -> str:
    zip_path = _V310_OLD_EXPORT_STATE_BUNDLE(prefix=prefix)
    try:
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("growth_positions.table.json", json.dumps(safe_convert(list(load_growth_positions().values())), indent=2))
            z.writestr("growth_trades.table.json", json.dumps(safe_convert(load_growth_trades()), indent=2))
            latest = load_latest_growth_plan()
            z.writestr("growth_latest_plan.json", json.dumps(safe_convert(latest or {}), indent=2))
    except Exception as exc:
        print(f"[GROWTH EXPORT WARNING] {exc}")
    return zip_path


_V310_OLD_RESET_ALL = reset_all_paper_state

def reset_all_paper_state(update_id: Optional[int] = None) -> Tuple[bool, str, Optional[str]]:
    ok, msg, backup_path = _V310_OLD_RESET_ALL(update_id=update_id)
    with db_tx() as conn:
        conn.execute("DELETE FROM growth_positions")
        conn.execute("DELETE FROM growth_trades")
        conn.execute("DELETE FROM growth_signals")
        conn.execute("DELETE FROM meta WHERE key IN ('last_growth_alpha_month', 'last_growth_alpha_alert_ts')")
    return ok, msg + "\n✅ Growth Alpha positions/trades/signals cleared", backup_path


_V310_OLD_HANDLE_COMMAND = handle_command

def handle_command(text: str, update_id: Optional[int] = None) -> None:
    text_clean = (text or "").strip()
    text_lower = text_clean.lower()

    if text_lower == "growthplan":
        send("🚀 Growth Alpha plan started. This can take a minute because it scores the high-growth universe.")
        plan = compute_growth_alpha_plan()
        save_growth_plan_signal(plan)
        send(format_growth_alpha_plan(plan))
        return
    if text_lower == "growthstatus":
        alloc = dynamic_portfolio_allocation_targets()
        latest = load_latest_growth_plan()
        details = growth_position_market_value_details()
        send(
            "🚀 EXPANDED GROWTH_ALPHA STATUS v4\n\n"
            f"Enabled: {yes_no(GROWTH_ALPHA_ENABLED)}\n"
            f"Ledger enabled: {yes_no(GROWTH_ALPHA_LEDGER_ENABLED)}\n"
            f"Target now: {alloc.get('growth_alpha_pct')}% of account\n"
            f"Universe size: {len(GROWTH_ALPHA_UNIVERSE)}\n"
            f"Top N: {GROWTH_ALPHA_TOP_N} | Max per cluster: {GROWTH_ALPHA_MAX_PER_CLUSTER}\n"
            f"Growth value: {format_money(float(details.get('value', 0) or 0))}\n"
            f"Active plan: {None if latest is None else latest.get('plan_date')}\n\n"
            "Commands:\n"
            "growthplan\n"
            "growthbuy TICKER SHARES at PRICE\n"
            "growthsell TICKER SHARES at PRICE\n"
            "growthportfolio | growthpnl | growthexposure"
        )
        return
    if text_lower == "growthportfolio":
        send(format_growth_portfolio_report())
        return
    if text_lower == "growthpnl":
        send(format_growth_pnl_report())
        return
    if text_lower == "growthexposure":
        send(format_growth_exposure_report())
        return
    if text_lower == "equity":
        snapshot = compute_equity_snapshot_data()
        send(
            "💼 ACCOUNT EQUITY\n\n"
            f"💵 Cash: {format_money(snapshot['cash'])}\n"
            f"⚡ Swing positions: {format_money(snapshot.get('swing_positions_value', 0))}\n"
            f"🏛️ Core wealth positions: {format_money(snapshot.get('core_positions_value', 0))}\n"
            f"🚀 Growth Alpha positions: {format_money(snapshot.get('growth_alpha_positions_value', 0))}\n"
            f"⚡ SPEC_ALPHA positions: {format_money(snapshot.get('spec_positions_value', 0))}\n"
            f"📦 Total positions: {format_money(snapshot['positions_value'])}\n"
            f"🏦 Total Equity: {format_money(snapshot['equity'])}"
        )
        return

    growth_cmd = re.fullmatch(
        r"(?i)\s*(growthbuy|growthsell)\s+([A-Z0-9.\-]{1,15})\s+([0-9]+(?:\.[0-9]+)?)\s+(?:at|@)\s+([0-9]+(?:\.[0-9]+)?)\s*",
        text_clean,
    )
    if growth_cmd:
        action = growth_cmd.group(1).lower()
        ticker = normalize_ticker(growth_cmd.group(2))
        shares = float(growth_cmd.group(3))
        price = float(growth_cmd.group(4))
        if not ticker:
            send("Invalid ticker")
            return
        if action == "growthbuy":
            ok, msg = record_growth_buy(ticker, shares, price, update_id=update_id)
            send(msg if ok else "❌ ERROR: " + msg)
            return
        if action == "growthsell":
            ok, msg = record_growth_sell(ticker, shares, price, update_id=update_id)
            send(msg if ok else "❌ ERROR: " + msg)
            return

    if text_lower in {"help", "/help"}:
        _V310_OLD_HANDLE_COMMAND(text_clean, update_id=update_id)
        send(
            "V4 Expanded Growth Alpha notes:\n"
            "• Allocation target in final v4.1.1 override: Core 20% / Growth 45% / SPEC 20% / Long VCP 5% / Crypto 10%.\n"
            "• Expanded Growth Alpha is a separate monthly ledger. Use growthbuy/growthsell only.\n"
            "• Do not record Growth Alpha with bought/sold, corebuy, or specbuy.\n"
            "• Options remain research-only."
        )
        return

    return _V310_OLD_HANDLE_COMMAND(text_clean, update_id=update_id)



# =============================================================================
# V4.1.1 FREEZE: AGGRESSIVE GROWTH + ALT-CRYPTO TACTICAL SWING EXTENSION
# Freeze candidate from offline A-to-Z test: Core 20 / Growth 45 / SPEC 20 / Long VCP 5 / Crypto 10.
# Crypto entry uses 20-day breakout, top-1, gate 2 of BTC/ETH/SOL above MA200.
# =============================================================================
# Offline-researched candidate. This keeps v4 expanded Growth, Core, SPEC, and
# Long VCP intact, disables the weak bear-stock tactical allocation, and adds a
# separate crypto tactical swing sleeve.
# Target research allocation:
#   Core UCITS/USD: 20%
#   Expanded Growth Alpha: 45%
#   SPEC_ALPHA: 20%
#   Long VCP tactical: 5%
#   Crypto tactical swing: 10%
#   Bear-stock tactical: 0%
# Options remain research-only and are not included here.
#
# Crypto design:
# - BTC/ETH/SOL are used as regime indicators.
# - The live default tradable universe avoids BTC/ETH/SOL and buys cheaper major
#   crypto names only: AVAX/LINK/ADA/XRP/DOGE/LTC.
# - SUI/BCH and other names can be researched later but are not default here.
# - Do not record crypto through bought/sold, corebuy, growthbuy, or specbuy.
#   Use cryptobuy/cryptosell only.

STRATEGY_VERSION = os.getenv(
    "STRATEGY_VERSION",
    "v4.1.1-freeze-growth-crypto-swing-20-45-20-5-10-monitor"
)

# v4.1.1 freeze allocation defaults.
# Bear-stock / inverse bear sleeve is intentionally disabled in v4.1.1; crypto tactical replaces it.
GROWTH_ALPHA_ACCOUNT_ALLOC_PCT = float(os.getenv("GROWTH_ALPHA_ACCOUNT_ALLOC_PCT", "0.45"))
SPEC_ALPHA_ACCOUNT_ALLOC_PCT = float(os.getenv("SPEC_ALPHA_ACCOUNT_ALLOC_PCT", "0.20"))
WEALTH_TACTICAL_LONG_ALLOC_BULL = float(os.getenv("WEALTH_TACTICAL_LONG_ALLOC_BULL", "0.05"))
WEALTH_TACTICAL_LONG_ALLOC_UNCERTAIN = float(os.getenv("WEALTH_TACTICAL_LONG_ALLOC_UNCERTAIN", "0.03"))
WEALTH_TACTICAL_LONG_ALLOC_BEAR = float(os.getenv("WEALTH_TACTICAL_LONG_ALLOC_BEAR", "0.00"))
WEALTH_BEAR_ALLOC_BULL = float(os.getenv("WEALTH_BEAR_ALLOC_BULL", "0.00"))
WEALTH_BEAR_ALLOC_UNCERTAIN = float(os.getenv("WEALTH_BEAR_ALLOC_UNCERTAIN", "0.00"))
WEALTH_BEAR_ALLOC_BEAR = float(os.getenv("WEALTH_BEAR_ALLOC_BEAR", "0.00"))

CRYPTO_ALPHA_ENABLED = os.getenv("CRYPTO_ALPHA_ENABLED", "1") != "0"
CRYPTO_ALPHA_LEDGER_ENABLED = os.getenv("CRYPTO_ALPHA_LEDGER_ENABLED", "1") != "0"
CRYPTO_ALPHA_ACCOUNT_ALLOC_PCT = float(os.getenv("CRYPTO_ALPHA_ACCOUNT_ALLOC_PCT", "0.10"))
CRYPTO_ALPHA_MAX_OPEN_POSITIONS = int(os.getenv("CRYPTO_ALPHA_MAX_OPEN_POSITIONS", "1"))
CRYPTO_ALPHA_MIN_TRADE_DOLLARS = float(os.getenv("CRYPTO_ALPHA_MIN_TRADE_DOLLARS", "25"))
CRYPTO_ALPHA_QUOTE_DEVIATION_LIMIT = float(os.getenv("CRYPTO_ALPHA_QUOTE_DEVIATION_LIMIT", "0.08"))
CRYPTO_ALPHA_REQUIRE_LIVE_QUOTE = os.getenv("CRYPTO_ALPHA_REQUIRE_LIVE_QUOTE", "1") != "0"
CRYPTO_ALPHA_REQUIRE_ACTIVE_PLAN_FOR_BUY = os.getenv("CRYPTO_ALPHA_REQUIRE_ACTIVE_PLAN_FOR_BUY", "1") != "0"
CRYPTO_ALPHA_PLAN_VALID_DAYS = int(os.getenv("CRYPTO_ALPHA_PLAN_VALID_DAYS", "3"))
CRYPTO_ALPHA_REVIEW_MINUTE = int(os.getenv("CRYPTO_ALPHA_REVIEW_MINUTE", str(21 * 60 + 30)))
CRYPTO_ALPHA_SIGNAL_COOLDOWN_HOURS = int(os.getenv("CRYPTO_ALPHA_SIGNAL_COOLDOWN_HOURS", "20"))

CRYPTO_ALPHA_INDICATORS = ["BTCUSD", "ETHUSD", "SOLUSD"]
CRYPTO_ALPHA_UNIVERSE = [
    "AVAXUSD", "LINKUSD", "ADAUSD", "XRPUSD", "DOGEUSD", "LTCUSD"
]
if os.getenv("CRYPTO_ALPHA_INCLUDE_SUI", "0") != "0":
    CRYPTO_ALPHA_UNIVERSE.append("SUIUSD")
CRYPTO_ALPHA_UNIVERSE = list(dict.fromkeys(CRYPTO_ALPHA_UNIVERSE))
CRYPTO_ALPHA_ALL_SYMBOLS = list(dict.fromkeys(CRYPTO_ALPHA_INDICATORS + CRYPTO_ALPHA_UNIVERSE))

CRYPTO_ALPHA_BREAKOUT_DAYS = int(os.getenv("CRYPTO_ALPHA_BREAKOUT_DAYS", "20"))
CRYPTO_ALPHA_GATE_MIN_ABOVE_MA200 = int(os.getenv("CRYPTO_ALPHA_GATE_MIN_ABOVE_MA200", "2"))
CRYPTO_ALPHA_MIN_PRICE = float(os.getenv("CRYPTO_ALPHA_MIN_PRICE", "0.01"))
CRYPTO_ALPHA_MAX_PRICE = float(os.getenv("CRYPTO_ALPHA_MAX_PRICE", "500"))
CRYPTO_ALPHA_MIN_ATR_PCT = float(os.getenv("CRYPTO_ALPHA_MIN_ATR_PCT", "0.02"))
CRYPTO_ALPHA_MAX_ATR_PCT = float(os.getenv("CRYPTO_ALPHA_MAX_ATR_PCT", "0.35"))
CRYPTO_ALPHA_MAX_RSI = float(os.getenv("CRYPTO_ALPHA_MAX_RSI", "88"))
CRYPTO_ALPHA_MAX_EXTENSION_MA20 = float(os.getenv("CRYPTO_ALPHA_MAX_EXTENSION_MA20", "0.50"))
CRYPTO_ALPHA_ATR_STOP_MULT = float(os.getenv("CRYPTO_ALPHA_ATR_STOP_MULT", "2.5"))
CRYPTO_ALPHA_TRAIL_ATR_MULT = float(os.getenv("CRYPTO_ALPHA_TRAIL_ATR_MULT", "3.5"))
CRYPTO_ALPHA_MAX_SINGLE_ASSET_PCT = float(os.getenv("CRYPTO_ALPHA_MAX_SINGLE_ASSET_PCT", "1.00"))
CRYPTO_ALPHA_STRATEGY_VERSION = "crypto_swing_alt_majors_v4_1_1_breakout20"


def get_crypto_quote(ticker: str) -> Optional[float]:
    ticker = normalize_ticker(ticker) or ""
    if not ticker:
        return None
    quote = get_prices_batch([ticker]).get(ticker)
    if quote is not None and quote > 0:
        return float(quote)
    try:
        url = f"{FMP_BASE}/quote?symbol={ticker}&apikey={FMP_API_KEY}"
        data = request_json(url, timeout=5, context=f"crypto quote {ticker}", retries=1)
        if isinstance(data, list) and data:
            raw = data[0].get("price")
            price = float(raw)
            if is_finite_positive(price):
                return price
    except Exception as exc:
        print(f"[CRYPTO QUOTE ERROR] {ticker}: {exc}")
    return None


def get_crypto_prices_batch(tickers: List[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for ticker in tickers:
        nticker = normalize_ticker(str(ticker))
        if not nticker:
            continue
        price = get_crypto_quote(nticker)
        if price is not None and price > 0:
            out[nticker] = float(price)
    return out


_V41_OLD_INIT_DB = init_db

def init_db() -> None:
    _V41_OLD_INIT_DB()
    conn = db_connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS crypto_positions (
                ticker TEXT PRIMARY KEY,
                crypto_position_id TEXT NOT NULL UNIQUE,
                strategy_version TEXT NOT NULL,
                units REAL NOT NULL CHECK (units > 0),
                avg_entry_price REAL NOT NULL CHECK (avg_entry_price > 0),
                cost_basis REAL NOT NULL CHECK (cost_basis >= 0),
                initial_stop REAL,
                stop REAL,
                highest REAL,
                entry_time REAL NOT NULL,
                last_update_time REAL NOT NULL,
                sleeve TEXT NOT NULL DEFAULT 'CRYPTO_ALPHA',
                target_account_pct REAL,
                last_plan_id TEXT,
                notes TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS crypto_trades (
                id TEXT PRIMARY KEY,
                crypto_position_id TEXT,
                ticker TEXT NOT NULL,
                side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
                units REAL NOT NULL CHECK (units > 0),
                price REAL NOT NULL CHECK (price > 0),
                amount REAL NOT NULL,
                realized_profit REAL,
                time REAL NOT NULL,
                strategy_version TEXT NOT NULL,
                plan_id TEXT,
                reason TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_crypto_trades_ticker ON crypto_trades(ticker);
            CREATE INDEX IF NOT EXISTS idx_crypto_trades_time ON crypto_trades(time);
            CREATE TABLE IF NOT EXISTS crypto_signals (
                id TEXT PRIMARY KEY,
                time REAL NOT NULL,
                plan_date TEXT NOT NULL,
                account_equity REAL NOT NULL,
                crypto_target_pct REAL NOT NULL,
                plan_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            );
            CREATE INDEX IF NOT EXISTS idx_crypto_signals_time ON crypto_signals(time);
            """
        )
        conn.commit()
    finally:
        conn.close()


def row_to_crypto_position(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "ticker": row["ticker"],
        "crypto_position_id": row["crypto_position_id"],
        "strategy_version": row["strategy_version"],
        "units": float(row["units"]),
        "avg_entry_price": float(row["avg_entry_price"]),
        "cost_basis": float(row["cost_basis"]),
        "initial_stop": None if row["initial_stop"] is None else float(row["initial_stop"]),
        "stop": None if row["stop"] is None else float(row["stop"]),
        "highest": None if row["highest"] is None else float(row["highest"]),
        "entry_time": float(row["entry_time"]),
        "last_update_time": float(row["last_update_time"]),
        "sleeve": row["sleeve"],
        "target_account_pct": None if row["target_account_pct"] is None else float(row["target_account_pct"]),
        "last_plan_id": row["last_plan_id"],
        "notes": row["notes"],
    }


def load_crypto_positions() -> Dict[str, Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM crypto_positions ORDER BY ticker").fetchall()
        return {row["ticker"]: row_to_crypto_position(row) for row in rows}
    finally:
        conn.close()


def load_crypto_trades() -> List[Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM crypto_trades ORDER BY time ASC, created_at ASC").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def load_latest_crypto_signal() -> Optional[Dict[str, Any]]:
    conn = db_connect()
    try:
        row = conn.execute("SELECT * FROM crypto_signals WHERE status = 'ACTIVE' ORDER BY time DESC LIMIT 1").fetchone()
        if row is None:
            return None
        data = dict(row)
        data["plan"] = json_loads_dict(data.get("plan_json"))
        return data
    finally:
        conn.close()


def save_crypto_plan_signal(plan: Dict[str, Any]) -> str:
    plan_id = str(plan.get("plan_id") or uuid.uuid4().hex)
    plan = dict(plan)
    plan["plan_id"] = plan_id
    with db_tx() as conn:
        conn.execute("UPDATE crypto_signals SET status = 'SUPERSEDED' WHERE status = 'ACTIVE'")
        conn.execute(
            """
            INSERT INTO crypto_signals(id, time, plan_date, account_equity, crypto_target_pct, plan_json, status)
            VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE')
            """,
            (
                plan_id,
                now_ts(),
                ny_date_str(),
                float(plan.get("account_equity", 0) or 0),
                float(plan.get("target_crypto_account_pct", 0) or 0),
                json_dumps(plan),
            ),
        )
    return plan_id


def crypto_position_market_value_details(prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    positions = load_crypto_positions()
    tickers = list(positions.keys())
    prices = prices or get_crypto_prices_batch(tickers)
    rows: List[Dict[str, Any]] = []
    total_value = 0.0
    total_cost = 0.0
    for ticker, pos in positions.items():
        mark = float(prices.get(ticker, pos.get("avg_entry_price", 0)) or pos.get("avg_entry_price", 0))
        units = float(pos.get("units", 0) or 0)
        value = units * mark
        cost = float(pos.get("cost_basis", 0) or 0)
        total_value += value
        total_cost += cost
        rows.append({
            **pos,
            "mark_price": round(mark, 8),
            "market_value": round(value, 2),
            "unrealized_profit": round(value - cost, 2),
            "unrealized_pct": None if cost <= 0 else round(((value - cost) / cost) * 100, 2),
        })
    realized = sum(float(t.get("realized_profit") or 0.0) for t in load_crypto_trades() if str(t.get("side")).upper() == "SELL")
    return {
        "positions": positions,
        "rows": rows,
        "value": round(total_value, 2),
        "cost_basis": round(total_cost, 2),
        "unrealized_profit": round(total_value - total_cost, 2),
        "realized_profit": round(realized, 2),
        "total_profit": round(realized + (total_value - total_cost), 2),
    }


def crypto_indicator_gate() -> Dict[str, Any]:
    rows = []
    ok_count = 0
    for sym in CRYPTO_ALPHA_INDICATORS:
        df = get_historical(sym, limit=260)
        if df is None or len(df) < 220:
            rows.append({"ticker": sym, "ok": False, "reason": "no_data"})
            continue
        close = df["Close"]
        price = float(close.iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])
        ma50 = float(close.rolling(50).mean().iloc[-1])
        ok = price > ma200
        ok_count += 1 if ok else 0
        rows.append({"ticker": sym, "price": round(price, 6), "ma50": round(ma50, 6), "ma200": round(ma200, 6), "ok": ok})
    gate_ok = ok_count >= CRYPTO_ALPHA_GATE_MIN_ABOVE_MA200
    return {"ok": gate_ok, "ok_count": ok_count, "required": CRYPTO_ALPHA_GATE_MIN_ABOVE_MA200, "rows": rows}


def crypto_score_ticker(ticker: str, gate_ref_ret: Optional[float] = None) -> Optional[Dict[str, Any]]:
    try:
        df = get_historical(ticker, limit=280)
        if df is None or len(df) < 220:
            return None
        close = df["Close"]
        price = float(close.iloc[-1])
        high = df["High"]
        low = df["Low"]
        ma20 = float(close.rolling(20).mean().iloc[-1])
        ma50 = float(close.rolling(50).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])
        roc21 = pct_change_last(df, 21)
        roc63 = pct_change_last(df, 63)
        roc126 = pct_change_last(df, 126) or 0.0
        atr_series = atr(df, 14)
        atr_val = float(atr_series.iloc[-1])
        atr_pct = atr_val / price if price > 0 else 0.0
        rsi_val = float(rsi(close, 14).iloc[-1])
        prior_high = float(close.shift(1).rolling(CRYPTO_ALPHA_BREAKOUT_DAYS).max().iloc[-1])
        if price < CRYPTO_ALPHA_MIN_PRICE or price > CRYPTO_ALPHA_MAX_PRICE:
            return None
        if not (price > ma50 and price > ma200):
            return None
        if roc21 is None or roc63 is None or roc21 <= 0 or roc63 <= 0:
            return None
        if not (CRYPTO_ALPHA_MIN_ATR_PCT <= atr_pct <= CRYPTO_ALPHA_MAX_ATR_PCT):
            return None
        if rsi_val > CRYPTO_ALPHA_MAX_RSI:
            return None
        if price > ma20 * (1 + CRYPTO_ALPHA_MAX_EXTENSION_MA20):
            return None
        if price <= prior_high:
            return None
        rel = roc63 - float(gate_ref_ret or 0.0)
        score = (0.35 * roc126) + (0.35 * roc63) + (0.20 * roc21) + (0.10 * rel) - (0.10 * atr_pct)
        stop = max(0.000001, price - (CRYPTO_ALPHA_ATR_STOP_MULT * atr_val))
        trail = max(0.000001, price - (CRYPTO_ALPHA_TRAIL_ATR_MULT * atr_val))
        return {
            "ticker": ticker,
            "price": round(price, 8),
            "ma20": round(ma20, 8),
            "ma50": round(ma50, 8),
            "ma200": round(ma200, 8),
            "prior_high": round(prior_high, 8),
            "roc_1m_pct": round(roc21 * 100, 2),
            "roc_3m_pct": round(roc63 * 100, 2),
            "roc_6m_pct": round(roc126 * 100, 2),
            "atr": round(atr_val, 8),
            "atr_pct": round(atr_pct * 100, 2),
            "rsi": round(rsi_val, 2),
            "score": round(score, 6),
            "stop": round(stop, 8),
            "trail_reference": round(trail, 8),
            "max_valid_entry": round(price * (1 + MAX_ENTRY_EXTENSION_PCT), 8),
        }
    except Exception as exc:
        print(f"[CRYPTO SCORE ERROR] {ticker}: {exc}")
        return None


def compute_crypto_alpha_plan() -> Dict[str, Any]:
    refresh_portfolio()
    snapshot = compute_equity_snapshot_data()
    equity = float(snapshot.get("equity", 0.0) or 0.0)
    allocation = dynamic_portfolio_allocation_targets()
    risk = allocation.get("risk_guard", {}) or {}
    target_pct = float(allocation.get("crypto_alpha_pct", CRYPTO_ALPHA_ACCOUNT_ALLOC_PCT * 100) or 0.0) / 100.0
    target_value = equity * target_pct
    gate = crypto_indicator_gate()
    gate_ref_rets = []
    for row in gate.get("rows", []):
        sym = row.get("ticker")
        df = get_historical(str(sym), limit=100)
        ret = pct_change_last(df, 63) if df is not None else None
        if ret is not None:
            gate_ref_rets.append(ret)
    gate_ref_ret = sum(gate_ref_rets) / len(gate_ref_rets) if gate_ref_rets else 0.0

    positions = load_crypto_positions() if CRYPTO_ALPHA_LEDGER_ENABLED else {}
    details = crypto_position_market_value_details() if CRYPTO_ALPHA_LEDGER_ENABLED else {"rows": [], "value": 0.0}
    current_rows = {str(row.get("ticker", "")).upper(): row for row in details.get("rows", [])}
    actions: List[Dict[str, Any]] = []
    scored: List[Dict[str, Any]] = []

    if CRYPTO_ALPHA_ENABLED and target_pct > 0 and not risk.get("hard_active") and gate.get("ok"):
        for ticker in CRYPTO_ALPHA_UNIVERSE:
            item = crypto_score_ticker(ticker, gate_ref_ret=gate_ref_ret)
            if item is not None:
                scored.append(item)
    scored = sorted(scored, key=lambda x: float(x.get("score", -999)), reverse=True)
    selected = scored[:CRYPTO_ALPHA_MAX_OPEN_POSITIONS]
    selected_tickers = {str(x.get("ticker", "")).upper() for x in selected}

    for item in selected:
        ticker = str(item["ticker"]).upper()
        current_value = float(current_rows.get(ticker, {}).get("market_value", 0.0) or 0.0)
        target_dollars = target_value * min(CRYPTO_ALPHA_MAX_SINGLE_ASSET_PCT, 1.0)
        drift = target_dollars - current_value
        action = "HOLD"
        if current_value <= 0 and target_dollars >= CRYPTO_ALPHA_MIN_TRADE_DOLLARS:
            action = "BUY"
        elif drift > max(CRYPTO_ALPHA_MIN_TRADE_DOLLARS, equity * 0.015):
            action = "ADD"
        elif drift < -max(CRYPTO_ALPHA_MIN_TRADE_DOLLARS, equity * 0.015):
            action = "TRIM"
        actions.append({
            **item,
            "action": action,
            "target_account_pct": round(target_pct * 100, 2),
            "target_value": round(target_dollars, 2),
            "current_value": round(current_value, 2),
            "suggested_dollars": round(abs(drift), 2),
            "drift_dollars": round(drift, 2),
        })

    # Exit/rotation candidates for current crypto holdings.
    for ticker, row in current_rows.items():
        if ticker in selected_tickers:
            # Still selected; also check live exit conditions.
            continue
        exit_reason = "Crypto regime gate is off or ticker is no longer selected."
        df = get_historical(ticker, limit=80)
        mark = float(row.get("mark_price", row.get("avg_entry_price", 0)) or 0)
        if df is not None and len(df) >= 30:
            ma20 = float(df["Close"].rolling(20).mean().iloc[-1])
            atr_val = float(atr(df, 14).iloc[-1])
            highest = max(float(row.get("highest") or mark), mark)
            trail = highest - (CRYPTO_ALPHA_TRAIL_ATR_MULT * atr_val)
            stop = float(row.get("stop") or 0.0)
            if mark < ma20:
                exit_reason = "Close/mark is below MA20."
            elif stop > 0 and mark <= stop:
                exit_reason = "Initial stop hit."
            elif mark <= trail:
                exit_reason = "ATR trailing stop hit."
        actions.append({
            "ticker": ticker,
            "action": "SELL",
            "price": mark,
            "target_account_pct": 0.0,
            "target_value": 0.0,
            "current_value": round(float(row.get("market_value", 0) or 0), 2),
            "suggested_dollars": round(float(row.get("market_value", 0) or 0), 2),
            "reason": exit_reason,
        })

    actionable = [a for a in actions if str(a.get("action")).upper() in {"BUY", "ADD", "TRIM", "SELL"}]
    return {
        "plan_id": uuid.uuid4().hex,
        "strategy_version": CRYPTO_ALPHA_STRATEGY_VERSION,
        "ny_time": ny_now().strftime("%Y-%m-%d %H:%M %Z"),
        "account_equity": round(equity, 2),
        "target_crypto_account_pct": round(target_pct * 100, 2),
        "target_crypto_value": round(target_value, 2),
        "current_crypto_value": round(float(details.get("value", 0.0) or 0.0), 2),
        "current_crypto_unrealized_profit": round(float(details.get("unrealized_profit", 0.0) or 0.0), 2),
        "allocation": allocation,
        "risk_guard": risk,
        "gate": gate,
        "universe": CRYPTO_ALPHA_UNIVERSE,
        "indicator_symbols": CRYPTO_ALPHA_INDICATORS,
        "top": selected,
        "actions": actions,
        "actionable": actionable,
        "all_scored": scored,
    }


def format_crypto_alpha_plan(plan: Dict[str, Any]) -> str:
    gate = plan.get("gate", {}) or {}
    risk = plan.get("risk_guard", {}) or {}
    msg = (
        "🪙 CRYPTO TACTICAL SWING PLAN v4.1.1\n\n"
        "Private bot only. Execute in broker/exchange first, then record with cryptobuy/cryptosell.\n\n"
        f"🕒 NY time: {plan.get('ny_time')}\n"
        f"🛡️ Risk guard: {risk.get('recommended_action')}\n"
        f"💼 Equity estimate: {format_money(float(plan.get('account_equity', 0) or 0))}\n"
        f"🪙 Target crypto sleeve: {plan.get('target_crypto_account_pct')}% = {format_money(float(plan.get('target_crypto_value', 0) or 0))}\n"
        f"📦 Current crypto value: {format_money(float(plan.get('current_crypto_value', 0) or 0))}\n"
        f"📈 Crypto unrealized P/L: {format_money(float(plan.get('current_crypto_unrealized_profit', 0) or 0))}\n\n"
        f"🚦 Regime gate: {yes_no(bool(gate.get('ok')))} ({gate.get('ok_count')}/{len(CRYPTO_ALPHA_INDICATORS)} indicators above MA200; required {gate.get('required')})\n"
    )
    for row in gate.get("rows", []):
        msg += f"• {row.get('ticker')}: price {row.get('price')} | MA200 {row.get('ma200')} | OK {yes_no(bool(row.get('ok')))}\n"
    msg += "\n"
    actions = plan.get("actions", []) or []
    ranked = [a for a in actions if str(a.get("action")).upper() in {"BUY", "ADD", "HOLD", "TRIM"}]
    exits = [a for a in actions if str(a.get("action")).upper() == "SELL"]
    if not actions:
        msg += "No crypto action. If the gate is off, crypto sleeve stays cash.\n"
    if ranked:
        msg += "🎯 Crypto candidates\n"
        for i, item in enumerate(ranked, start=1):
            action = str(item.get("action", "HOLD")).upper()
            verb = {"BUY": "🟢 BUY", "ADD": "🟢 ADD", "HOLD": "🟡 HOLD", "TRIM": "🟠 TRIM"}.get(action, action)
            msg += (
                f"{i}) {verb} {item.get('ticker')}\n"
                f"   Price: {item.get('price')} | Max entry: {item.get('max_valid_entry')} | Stop: {item.get('stop')}\n"
                f"   Target: {item.get('target_account_pct')}% acct / {format_money(float(item.get('target_value', 0) or 0))}\n"
                f"   Current: {format_money(float(item.get('current_value', 0) or 0))} | Action size: ~{format_money(float(item.get('suggested_dollars', 0) or 0))}\n"
                f"   1m {format_pct(item.get('roc_1m_pct'))} | 3m {format_pct(item.get('roc_3m_pct'))} | 6m {format_pct(item.get('roc_6m_pct'))} | ATR {item.get('atr_pct')}% | Score {item.get('score')}\n"
            )
        msg += "\n"
    if exits:
        msg += "🔴 Crypto exit / rotation candidates\n"
        for item in exits:
            msg += f"SELL {item.get('ticker')} — current {format_money(float(item.get('current_value', 0) or 0))}\nReason: {item.get('reason', 'Exit condition')}\n"
        msg += "\n"
    msg += (
        "How to execute after broker/exchange fill:\n"
        "• cryptobuy TICKER UNITS at PRICE\n"
        "• cryptosell TICKER UNITS at PRICE\n\n"
        "Crypto rules:\n"
        "• BTC/ETH/SOL are indicators; default buys use cheaper major crypto names.\n"
        "• Crypto has its own ledger and shares account cash.\n"
        "• Do not use bought/sold, corebuy, growthbuy, or specbuy for crypto."
    )
    return msg[:MAX_TELEGRAM_MESSAGE]


def crypto_target_for_ticker(plan: Dict[str, Any], ticker: str) -> Optional[Dict[str, Any]]:
    ticker = ticker.upper()
    for item in plan.get("top", []) or []:
        if str(item.get("ticker", "")).upper() == ticker:
            return item
    return None


def validate_crypto_price_against_quote(ticker: str, price: float) -> Tuple[bool, str, Optional[float]]:
    if not CRYPTO_ALPHA_REQUIRE_LIVE_QUOTE:
        return True, "Quote check disabled", None
    quote = get_crypto_quote(ticker)
    if quote is None or quote <= 0:
        return False, "Live crypto quote unavailable.", None
    deviation = abs(price - quote) / quote
    if deviation > CRYPTO_ALPHA_QUOTE_DEVIATION_LIMIT:
        return False, (f"Crypto trade rejected: price too far from live quote.\n"
                       f"Live quote: {round(quote, 8)}\nYour price: {round(price, 8)}\n"
                       f"Max deviation: {round(CRYPTO_ALPHA_QUOTE_DEVIATION_LIMIT * 100, 2)}%"), quote
    return True, "OK", quote


def record_crypto_buy(ticker: str, units: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
    ticker = normalize_ticker(ticker) or ""
    if not ticker:
        return False, "Invalid ticker"
    if not CRYPTO_ALPHA_LEDGER_ENABLED:
        return False, "Crypto ledger is disabled."
    if ticker not in CRYPTO_ALPHA_UNIVERSE:
        return False, f"{ticker} is not in the v4.1.1 crypto universe."
    if units <= 0 or not math.isfinite(units):
        return False, "Crypto units must be positive and finite."
    if not is_finite_positive(price):
        return False, "Crypto price must be positive and finite."
    amount = units * price
    if amount < CRYPTO_ALPHA_MIN_TRADE_DOLLARS:
        return False, f"Crypto trade amount is below minimum {format_money(CRYPTO_ALPHA_MIN_TRADE_DOLLARS)}."
    plan = compute_crypto_alpha_plan()
    target = crypto_target_for_ticker(plan, ticker)
    if CRYPTO_ALPHA_REQUIRE_ACTIVE_PLAN_FOR_BUY and target is None:
        allowed = ", ".join(str(x.get("ticker")) for x in plan.get("top", []))
        return False, f"Crypto buy rejected: {ticker} is not in the active crypto plan. Current top: {allowed or 'none'}"
    if target is not None:
        max_entry = float(target.get("max_valid_entry") or 0)
        if max_entry > 0 and price > max_entry:
            return False, f"Crypto buy rejected: price {price} is above max entry {max_entry}."
    ok, msg, quote = validate_crypto_price_against_quote(ticker, price)
    if not ok:
        return False, msg
    stop = None if target is None else float(target.get("stop") or 0)
    highest = price
    now = now_ts()
    plan_id = str(plan.get("plan_id"))
    target_pct = None if target is None else float(target.get("target_account_pct", 0) or 0)
    with db_tx() as conn:
        cash = get_cash(conn)
        if amount > cash:
            mark_update_processed_tx(conn, update_id, "rejected_crypto_insufficient_cash")
            return False, "Not enough cash for crypto buy."
        row = conn.execute("SELECT * FROM crypto_positions WHERE ticker = ?", (ticker,)).fetchone()
        if row is None:
            crypto_position_id = f"CRYPTO_{ticker}_{int(now)}_{uuid.uuid4().hex[:8]}"
            conn.execute(
                """
                INSERT INTO crypto_positions(
                    ticker, crypto_position_id, strategy_version, units, avg_entry_price, cost_basis,
                    initial_stop, stop, highest, entry_time, last_update_time, sleeve, target_account_pct, last_plan_id, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CRYPTO_ALPHA', ?, ?, '')
                """,
                (ticker, crypto_position_id, CRYPTO_ALPHA_STRATEGY_VERSION, round(units, 10), round(price, 10), round(amount, 6),
                 None if stop is None or stop <= 0 else round(stop, 10), None if stop is None or stop <= 0 else round(stop, 10),
                 round(highest, 10), now, now, target_pct, plan_id),
            )
        else:
            pos = row_to_crypto_position(row)
            crypto_position_id = pos["crypto_position_id"]
            old_units = float(pos["units"])
            old_cost = float(pos["cost_basis"])
            new_units = old_units + units
            new_cost = old_cost + amount
            avg_price = new_cost / new_units
            highest = max(float(pos.get("highest") or price), price)
            conn.execute(
                """
                UPDATE crypto_positions
                SET units = ?, avg_entry_price = ?, cost_basis = ?, stop = ?, highest = ?, last_update_time = ?,
                    target_account_pct = ?, last_plan_id = ?, strategy_version = ?
                WHERE ticker = ?
                """,
                (round(new_units, 10), round(avg_price, 10), round(new_cost, 6), None if stop is None or stop <= 0 else round(stop, 10),
                 round(highest, 10), now, target_pct, plan_id, CRYPTO_ALPHA_STRATEGY_VERSION, ticker),
            )
        conn.execute(
            """
            INSERT INTO crypto_trades(id, crypto_position_id, ticker, side, units, price, amount, realized_profit, time, strategy_version, plan_id, reason, created_at)
            VALUES (?, ?, ?, 'BUY', ?, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (uuid.uuid4().hex, crypto_position_id, ticker, round(units, 10), round(price, 10), round(amount, 6), now,
             CRYPTO_ALPHA_STRATEGY_VERSION, plan_id, "crypto_plan_buy", now),
        )
        set_cash_tx(conn, cash - amount)
        mark_update_processed_tx(conn, update_id, "processed_crypto_buy")
    refresh_portfolio()
    audit("CRYPTO_BUY", f"{ticker} units={units} price={price} amount={amount}")
    return True, (f"🪙 CRYPTO BUY RECORDED {ticker}\n\n"
                  f"📦 Units: {format_core_shares(units)}\n"
                  f"💵 Price: {round(price, 8)}\n"
                  f"💰 Amount: {format_money(amount)}\n"
                  f"💵 Cash left: {format_money(portfolio['cash'])}")


def record_crypto_sell(ticker: str, units: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
    ticker = normalize_ticker(ticker) or ""
    if not ticker:
        return False, "Invalid ticker"
    if not CRYPTO_ALPHA_LEDGER_ENABLED:
        return False, "Crypto ledger is disabled."
    if units <= 0 or not math.isfinite(units):
        return False, "Crypto units must be positive and finite."
    if not is_finite_positive(price):
        return False, "Crypto price must be positive and finite."
    ok, msg, quote = validate_crypto_price_against_quote(ticker, price)
    if not ok:
        return False, msg
    with db_tx() as conn:
        row = conn.execute("SELECT * FROM crypto_positions WHERE ticker = ?", (ticker,)).fetchone()
        if row is None:
            mark_update_processed_tx(conn, update_id, "rejected_crypto_no_position")
            return False, "No crypto position to sell."
        pos = row_to_crypto_position(row)
        current_units = float(pos["units"])
        if units - current_units > 1e-10:
            mark_update_processed_tx(conn, update_id, "rejected_crypto_too_many_units")
            return False, f"You only have {format_core_shares(current_units)} units of {ticker}."
        units = min(units, current_units)
        avg = float(pos["avg_entry_price"])
        proceeds = units * price
        realized_profit = (price - avg) * units
        remaining = current_units - units
        now = now_ts()
        plan = compute_crypto_alpha_plan()
        plan_id = str(plan.get("plan_id"))
        conn.execute(
            """
            INSERT INTO crypto_trades(id, crypto_position_id, ticker, side, units, price, amount, realized_profit, time, strategy_version, plan_id, reason, created_at)
            VALUES (?, ?, ?, 'SELL', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (uuid.uuid4().hex, pos["crypto_position_id"], ticker, round(units, 10), round(price, 10), round(proceeds, 6),
             round(realized_profit, 6), now, CRYPTO_ALPHA_STRATEGY_VERSION, plan_id, "crypto_plan_sell", now),
        )
        if remaining <= 1e-10:
            conn.execute("DELETE FROM crypto_positions WHERE ticker = ?", (ticker,))
        else:
            new_cost = avg * remaining
            conn.execute("UPDATE crypto_positions SET units = ?, cost_basis = ?, last_update_time = ? WHERE ticker = ?",
                         (round(remaining, 10), round(new_cost, 6), now, ticker))
        cash = get_cash(conn)
        set_cash_tx(conn, cash + proceeds)
        mark_update_processed_tx(conn, update_id, "processed_crypto_sell")
    refresh_portfolio()
    audit("CRYPTO_SELL", f"{ticker} units={units} price={price} proceeds={proceeds} profit={realized_profit}")
    return True, (f"🪙 CRYPTO SELL RECORDED {ticker}\n\n"
                  f"📦 Units: {format_core_shares(units)}\n"
                  f"💵 Price: {round(price, 8)}\n"
                  f"💰 Proceeds: {format_money(proceeds)}\n"
                  f"📊 Realized crypto P/L: {format_money(realized_profit)} ({format_pct((price - avg) / avg * 100 if avg > 0 else None)})\n"
                  f"💵 Cash now: {format_money(portfolio['cash'])}")


_V41_OLD_DYNAMIC = dynamic_portfolio_allocation_targets

def dynamic_portfolio_allocation_targets() -> Dict[str, Any]:
    base = _V41_OLD_DYNAMIC()
    market = str(base.get("market", "UNCERTAIN"))
    risk = base.get("risk_guard", {}) or {}
    if risk.get("hard_active"):
        core, growth, spec, long_vcp, bear, crypto = 20.0, 0.0, 0.0, 0.0, 0.0, 0.0
    elif market == "BULL":
        core, growth, spec, long_vcp, bear, crypto = 20.0, 45.0, 20.0, 5.0, 0.0, 10.0
    elif market == "BEAR":
        core, growth, spec, long_vcp, bear, crypto = 20.0, 0.0, 0.0, 0.0, 0.0, 0.0
    else:
        core, growth, spec, long_vcp, bear, crypto = 20.0, 20.0, 10.0, 3.0, 0.0, 5.0
    if risk.get("soft_active") and not risk.get("hard_active"):
        growth *= 0.50
        spec *= 0.50
        long_vcp *= 0.50
        crypto *= 0.50
    if not GROWTH_ALPHA_ENABLED:
        growth = 0.0
    if not SPEC_ALPHA_ENABLED:
        spec = 0.0
    if not CRYPTO_ALPHA_ENABLED:
        crypto = 0.0
    cash = max(0.0, 100.0 - core - growth - spec - long_vcp - bear - crypto)
    base["strategy_version"] = "v4_1_1_freeze_growth_crypto_swing_20_45_20_5_10_dynamic_allocation"
    base["core_wealth_pct"] = round(core, 2)
    base["growth_alpha_pct"] = round(growth, 2)
    base["spec_alpha_pct"] = round(spec, 2)
    base["long_vcp_tactical_pct"] = round(long_vcp, 2)
    base["bear_inverse_tactical_pct"] = round(bear, 2)
    base["crypto_alpha_pct"] = round(crypto, 2)
    base["cash_reserve_pct"] = round(cash, 2)
    return base


_V41_OLD_COMPUTE_EQUITY = compute_equity_snapshot_data

def compute_equity_snapshot_data() -> Dict[str, float]:
    snapshot = _V41_OLD_COMPUTE_EQUITY()
    crypto_positions = load_crypto_positions() if CRYPTO_ALPHA_LEDGER_ENABLED else {}
    prices = get_crypto_prices_batch(list(crypto_positions.keys()))
    crypto_value = 0.0
    crypto_cost = 0.0
    for ticker, pos in crypto_positions.items():
        price = prices.get(ticker, pos.get("avg_entry_price", 0))
        crypto_value += float(price) * float(pos["units"])
        crypto_cost += float(pos.get("cost_basis", 0) or 0)
    snapshot["crypto_alpha_positions_value"] = round(crypto_value, 2)
    snapshot["crypto_alpha_cost_basis"] = round(crypto_cost, 2)
    snapshot["crypto_alpha_unrealized_profit"] = round(crypto_value - crypto_cost, 2)
    snapshot["positions_value"] = round(float(snapshot.get("positions_value", 0) or 0) + crypto_value, 2)
    snapshot["equity"] = round(float(snapshot.get("equity", 0) or 0) + crypto_value, 2)
    return snapshot


_V41_OLD_REALIZED = realized_performance_all_time

def realized_performance_all_time() -> Dict[str, Any]:
    perf = _V41_OLD_REALIZED()
    crypto_trades = load_crypto_trades() if CRYPTO_ALPHA_LEDGER_ENABLED else []
    crypto_profit = round(sum(float(t.get("realized_profit") or 0.0) for t in crypto_trades if str(t.get("side")).upper() == "SELL"), 2)
    perf["crypto_realized_profit"] = crypto_profit
    perf["profit"] = round(float(perf.get("profit", 0) or 0) + crypto_profit, 2)
    base_cap = float(perf.get("base_capital", 0) or 0)
    perf["pct"] = None if base_cap <= 0 else (perf["profit"] / base_cap) * 100
    perf["crypto_trade_records"] = len(crypto_trades)
    perf["trade_records"] = int(perf.get("trade_records", 0) or 0) + len(crypto_trades)
    return perf


_V41_OLD_PORTFOLIO_REPORT = format_combined_portfolio_report

def format_combined_portfolio_report() -> str:
    base_msg = _V41_OLD_PORTFOLIO_REPORT()
    rows = crypto_position_market_value_details().get("rows", []) if CRYPTO_ALPHA_LEDGER_ENABLED else []
    if not rows:
        return base_msg.replace("No open swing, core, SPEC, or Growth Alpha positions", "No open swing, core, SPEC, Growth Alpha, or Crypto positions")
    msg = base_msg + "\n\n🪙 CRYPTO_ALPHA POSITIONS\n\n"
    for row in rows:
        msg += (f"📦 {row['ticker']}\n"
                f"Units: {format_core_shares(row['units'])}\n"
                f"Avg: {round(float(row['avg_entry_price']), 8)} | Now: {round(float(row['mark_price']), 8)}\n"
                f"Value: {format_money(float(row['market_value']))}\n"
                f"P/L: {format_money(float(row['unrealized_profit']))} ({format_pct(row.get('unrealized_pct'))})\n\n")
    return msg[:MAX_TELEGRAM_MESSAGE]


def format_crypto_portfolio_report() -> str:
    details = crypto_position_market_value_details()
    rows = details.get("rows", []) or []
    snapshot = compute_equity_snapshot_data()
    msg = (f"🪙 CRYPTO_ALPHA PORTFOLIO\n\n"
           f"💵 Shared cash: {format_money(snapshot['cash'])}\n"
           f"🪙 Crypto value: {format_money(float(details.get('value', 0) or 0))}\n"
           f"📏 Cost basis: {format_money(float(details.get('cost_basis', 0) or 0))}\n"
           f"📈 Unrealized P/L: {format_money(float(details.get('unrealized_profit', 0) or 0))}\n"
           f"✅ Realized Crypto P/L: {format_money(float(details.get('realized_profit', 0) or 0))}\n"
           f"💼 Total equity: {format_money(snapshot['equity'])}\n\n")
    if not rows:
        return msg + "No crypto positions recorded yet. Use cryptoplan, then cryptobuy after broker/exchange execution."
    for row in rows:
        msg += (f"📦 {row['ticker']}\n"
                f"Units: {format_core_shares(row['units'])}\n"
                f"Avg: {round(float(row['avg_entry_price']), 8)} | Now: {round(float(row['mark_price']), 8)}\n"
                f"Value: {format_money(float(row['market_value']))}\n"
                f"P/L: {format_money(float(row['unrealized_profit']))} ({format_pct(row.get('unrealized_pct'))})\n"
                f"Stop: {row.get('stop')} | High: {row.get('highest')}\n\n")
    return msg[:MAX_TELEGRAM_MESSAGE]


def format_crypto_pnl_report() -> str:
    details = crypto_position_market_value_details()
    trades = load_crypto_trades()
    buys = [t for t in trades if str(t.get("side")).upper() == "BUY"]
    sells = [t for t in trades if str(t.get("side")).upper() == "SELL"]
    return (f"🪙 CRYPTO_ALPHA P/L\n\n"
            f"🪙 Crypto value: {format_money(float(details.get('value', 0) or 0))}\n"
            f"📏 Cost basis: {format_money(float(details.get('cost_basis', 0) or 0))}\n"
            f"📈 Unrealized P/L: {format_money(float(details.get('unrealized_profit', 0) or 0))}\n"
            f"✅ Realized P/L: {format_money(float(details.get('realized_profit', 0) or 0))}\n"
            f"💰 Total Crypto P/L: {format_money(float(details.get('total_profit', 0) or 0))}\n\n"
            f"Buy records: {len(buys)}\nSell records: {len(sells)}")


def format_crypto_exposure_report() -> str:
    snapshot = compute_equity_snapshot_data()
    details = crypto_position_market_value_details()
    equity = float(snapshot.get("equity", 0) or 0)
    alloc = dynamic_portfolio_allocation_targets()
    target_pct = float(alloc.get("crypto_alpha_pct", 0) or 0)
    actual_pct = 0.0 if equity <= 0 else (float(details.get("value", 0) or 0) / equity) * 100
    return (f"🪙 CRYPTO EXPOSURE\n\n"
            f"💼 Total equity: {format_money(equity)}\n"
            f"🪙 Crypto value: {format_money(float(details.get('value', 0) or 0))}\n"
            f"🎯 Target Crypto: {round(target_pct, 2)}% of account\n"
            f"📊 Actual Crypto: {round(actual_pct, 2)}% of account\n"
            f"📐 Drift: {round(actual_pct - target_pct, 2)} percentage points\n\n"
            "Use cryptoplan for BUY/HOLD/SELL actions.")


def format_portfolio_allocation_plan() -> str:
    plan = dynamic_portfolio_allocation_targets()
    risk = plan.get("risk_guard", {}) or {}
    return (
        "🏛️ INSTITUTIONAL ALLOCATION PLAN v4.1.1 FREEZE 20/45/20/5/10\n\n"
        "Private bot only. This is portfolio guidance, not an automatic trade.\n\n"
        f"🕒 NY time: {plan.get('ny_time')}\n"
        f"🌎 Market: {market_label(str(plan.get('market', 'UNKNOWN')))} ({plan.get('market_score')}/8)\n"
        f"🐻 Bear pressure score: {plan.get('bear_score')}/60\n"
        f"🛡️ Risk guard: {risk.get('recommended_action')}\n"
        f"📉 Current DD: {risk.get('drawdown_pct')}% from {format_money(float(risk.get('high_equity', 0) or 0))}\n\n"
        "Target account buckets:\n"
        f"🏦 Core UCITS/USD rotation: {plan.get('core_wealth_pct')}%\n"
        f"🚀 Growth Alpha rotation: {plan.get('growth_alpha_pct')}%\n"
        f"⚡ SPEC_ALPHA rotation: {plan.get('spec_alpha_pct')}%\n"
        f"🐂 Long VCP tactical: {plan.get('long_vcp_tactical_pct')}%\n"
        f"🪙 Crypto tactical swing: {plan.get('crypto_alpha_pct')}%\n"
        f"🐻 Bear stock tactical: {plan.get('bear_inverse_tactical_pct')}%\n"
        f"💵 Cash reserve: {plan.get('cash_reserve_pct')}%\n\n"
        "Rules:\n"
        "• Core/Growth/SPEC are monthly rotation sleeves.\n"
        "• Long VCP and Crypto are tactical/swing-style sleeves.\n"
        "• Crypto uses BTC/ETH/SOL as indicators and buys cheaper major crypto candidates.\n"
        "• Bear-stock tactical is disabled in this candidate.\n"
        "• Options remain research-only."
    )


_V41_OLD_EXPORT_STATE_BUNDLE = export_state_bundle

def export_state_bundle(prefix: str = "bot_state_export") -> str:
    zip_path = _V41_OLD_EXPORT_STATE_BUNDLE(prefix=prefix)
    try:
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("crypto_positions.table.json", json.dumps(safe_convert(list(load_crypto_positions().values())), indent=2))
            z.writestr("crypto_trades.table.json", json.dumps(safe_convert(load_crypto_trades()), indent=2))
            latest = load_latest_crypto_signal()
            z.writestr("crypto_latest_plan.json", json.dumps(safe_convert(latest or {}), indent=2))
    except Exception as exc:
        print(f"[CRYPTO EXPORT WARNING] {exc}")
    return zip_path


_V41_OLD_RESET_ALL = reset_all_paper_state

def reset_all_paper_state(update_id: Optional[int] = None) -> Tuple[bool, str, Optional[str]]:
    ok, msg, backup_path = _V41_OLD_RESET_ALL(update_id=update_id)
    with db_tx() as conn:
        conn.execute("DELETE FROM crypto_positions")
        conn.execute("DELETE FROM crypto_trades")
        conn.execute("DELETE FROM crypto_signals")
    return ok, msg + "\n✅ Crypto Alpha positions/trades/signals cleared", backup_path


_V41_OLD_HANDLE_COMMAND = handle_command

def handle_command(text: str, update_id: Optional[int] = None) -> None:
    text_clean = (text or "").strip()
    text_lower = text_clean.lower()

    if text_lower == "cryptoplan":
        send("🪙 Crypto tactical plan started. This scores BTC/ETH/SOL indicators and cheaper major crypto candidates.")
        plan = compute_crypto_alpha_plan()
        save_crypto_plan_signal(plan)
        send(format_crypto_alpha_plan(plan))
        return
    if text_lower == "cryptostatus":
        alloc = dynamic_portfolio_allocation_targets()
        latest = load_latest_crypto_signal()
        details = crypto_position_market_value_details()
        gate = crypto_indicator_gate()
        send(
            "🪙 CRYPTO_ALPHA STATUS v4.1.1\n\n"
            f"Enabled: {yes_no(CRYPTO_ALPHA_ENABLED)}\n"
            f"Ledger enabled: {yes_no(CRYPTO_ALPHA_LEDGER_ENABLED)}\n"
            f"Target now: {alloc.get('crypto_alpha_pct')}% of account\n"
            f"Indicators: {', '.join(CRYPTO_ALPHA_INDICATORS)}\n"
            f"Universe: {', '.join(CRYPTO_ALPHA_UNIVERSE)}\n"
            f"Regime gate: {yes_no(bool(gate.get('ok')))} ({gate.get('ok_count')}/{len(CRYPTO_ALPHA_INDICATORS)} above MA200)\n"
            f"Crypto value: {format_money(float(details.get('value', 0) or 0))}\n"
            f"Active plan: {None if latest is None else latest.get('plan_date')}\n\n"
            "Commands:\ncryptoplan\ncryptobuy TICKER UNITS at PRICE\ncryptosell TICKER UNITS at PRICE\ncryptoportfolio | cryptopnl | cryptoexposure"
        )
        return
    if text_lower == "cryptoportfolio":
        send(format_crypto_portfolio_report())
        return
    if text_lower == "cryptopnl":
        send(format_crypto_pnl_report())
        return
    if text_lower == "cryptoexposure":
        send(format_crypto_exposure_report())
        return
    if text_lower == "equity":
        snapshot = compute_equity_snapshot_data()
        send(
            "💼 ACCOUNT EQUITY\n\n"
            f"💵 Cash: {format_money(snapshot['cash'])}\n"
            f"⚡ Swing positions: {format_money(snapshot.get('swing_positions_value', 0))}\n"
            f"🏛️ Core wealth positions: {format_money(snapshot.get('core_positions_value', 0))}\n"
            f"🚀 Growth Alpha positions: {format_money(snapshot.get('growth_alpha_positions_value', 0))}\n"
            f"⚡ SPEC_ALPHA positions: {format_money(snapshot.get('spec_positions_value', 0))}\n"
            f"🪙 Crypto Alpha positions: {format_money(snapshot.get('crypto_alpha_positions_value', 0))}\n"
            f"📦 Total positions: {format_money(snapshot['positions_value'])}\n"
            f"🏦 Total Equity: {format_money(snapshot['equity'])}"
        )
        return

    crypto_cmd = re.fullmatch(
        r"(?i)\s*(cryptobuy|cryptosell)\s+([A-Z0-9.\-]{1,15})\s+([0-9]+(?:\.[0-9]+)?)\s+(?:at|@)\s+([0-9]+(?:\.[0-9]+)?)\s*",
        text_clean,
    )
    if crypto_cmd:
        action = crypto_cmd.group(1).lower()
        ticker = normalize_ticker(crypto_cmd.group(2))
        units = float(crypto_cmd.group(3))
        price = float(crypto_cmd.group(4))
        if not ticker:
            send("Invalid ticker")
            return
        if action == "cryptobuy":
            ok, msg = record_crypto_buy(ticker, units, price, update_id=update_id)
            send(msg if ok else "❌ ERROR: " + msg)
            return
        if action == "cryptosell":
            ok, msg = record_crypto_sell(ticker, units, price, update_id=update_id)
            send(msg if ok else "❌ ERROR: " + msg)
            return

    if text_lower in {"help", "/help"}:
        _V41_OLD_HANDLE_COMMAND(text_clean, update_id=update_id)
        send(
            "V4.1.1 Freeze notes:\n"
            "• Allocation target: Core 20% / Growth 45% / SPEC 20% / Long VCP 5% / Crypto 10%.\n"
            "• Crypto uses BTC/ETH/SOL as regime indicators and buys cheaper major crypto candidates.\n"
            "• Use cryptobuy/cryptosell only. Do not mix crypto with bought/sold, corebuy, growthbuy, or specbuy."
        )
        return

    return _V41_OLD_HANDLE_COMMAND(text_clean, update_id=update_id)


# =============================================================================
# V4.1.1 LIVE HOTFIX 2026-05-27
# =============================================================================
# Scope:
# - Fix Growth Alpha market-filter DataFrame truth-value crash.
# - Remove stale/untradeable SPEC_ALPHA corporate-action tickers from planning and buys.
# - Disable legacy inverse/bear sleeve by default in the v4.1.1 crypto-tactical freeze.
# - Keep public Core/SPEC plans off by default unless explicitly re-enabled by env.
# No strategy research changes: allocation and indicators remain the v4.1.1 freeze.

STRATEGY_VERSION = os.getenv(
    "STRATEGY_VERSION",
    "v4.1.1-hotfix-growth-spec-clean-20-45-20-5-10-monitor"
)

# Safety: v4.1.1 replaced the weak/blocked bear sleeve with crypto tactical. Keep the
# legacy bear sleeve disabled unless intentionally re-enabled by environment variable.
BEAR_SLEEVE_ENABLED = os.getenv("BEAR_SLEEVE_ENABLED", "0") != "0"
if not BEAR_SLEEVE_ENABLED:
    BEAR_WATCHLIST = []
ALLOWED_BUY_TICKERS = set(WATCHLIST) | set(BEAR_WATCHLIST)

# Public plan posting should be explicitly enabled after outputs are verified.
CORE_PUBLIC_SIGNAL_ENABLED = os.getenv("CORE_PUBLIC_SIGNAL_ENABLED", "0") == "1"
SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED = os.getenv("SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED", "0") == "1"

# Corporate-action / stale-universe blocks. Do not substitute replacement tickers automatically.
# HOUS: Anywhere/HOUS corporate-action issue. AMRK: A-Mark renamed to GOLD. Both polluted plans.
SPEC_ALPHA_EXCLUDED_TICKERS = {
    x.strip().upper()
    for x in os.getenv("SPEC_ALPHA_EXCLUDED_TICKERS", "HOUS,AMRK").split(",")
    if x.strip()
}
SPEC_ALPHA_UNIVERSE = [
    t for t in SPEC_ALPHA_UNIVERSE
    if str(t).upper() not in SPEC_ALPHA_EXCLUDED_TICKERS
]

_V411_HOTFIX_OLD_GROWTH_MARKET_FILTER_OK = growth_alpha_market_filter_ok

def growth_alpha_market_filter_ok(market_details: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """v4.1.1 hotfix: avoid evaluating pandas DataFrames as booleans."""
    try:
        details = market_details if isinstance(market_details, dict) else market_regime_details()
        if int(details.get("score", 0) or 0) < GROWTH_ALPHA_REQUIRE_MARKET_SCORE:
            return False, "market score below threshold"
        if GROWTH_ALPHA_REQUIRE_SPY_QQQ_ABOVE_MA200:
            frames = details.get("frames", {}) if isinstance(details.get("frames"), dict) else {}
            for symbol in ["SPY", "QQQ"]:
                df = frames.get(symbol)
                if not isinstance(df, pd.DataFrame) or df.empty:
                    df = get_signal_dataframe(symbol, limit=260)
                last, ma = frame_last_close_ma(df, 200)
                if last is None or ma is None:
                    return False, f"{symbol} MA200 data unavailable"
                if last <= ma:
                    return False, f"{symbol} below MA200"
        return True, "OK"
    except Exception as exc:
        return False, f"market filter error: {exc}"

_V411_HOTFIX_OLD_SPEC_SCORE_TICKER = spec_alpha_score_ticker

def spec_alpha_score_ticker(ticker: str) -> Optional[Dict[str, Any]]:
    nticker = normalize_ticker(str(ticker)) or ""
    if nticker in SPEC_ALPHA_EXCLUDED_TICKERS:
        return None
    return _V411_HOTFIX_OLD_SPEC_SCORE_TICKER(nticker)

_V411_HOTFIX_OLD_RECORD_SPEC_BUY = record_spec_buy

def record_spec_buy(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
    nticker = normalize_ticker(str(ticker)) or ""
    if nticker in SPEC_ALPHA_EXCLUDED_TICKERS:
        return False, (
            f"SPEC_ALPHA buy rejected: {nticker} is blocked by v4.1.1 hotfix corporate-action cleanup. "
            "Do not substitute another ticker manually; rerun specplan."
        )
    return _V411_HOTFIX_OLD_RECORD_SPEC_BUY(nticker, shares, price, update_id=update_id)

_V411_HOTFIX_OLD_COMPUTE_SPEC_PLAN = compute_spec_alpha_plan

def compute_spec_alpha_plan() -> Dict[str, Any]:
    plan = _V411_HOTFIX_OLD_COMPUTE_SPEC_PLAN()
    plan["excluded_tickers"] = sorted(SPEC_ALPHA_EXCLUDED_TICKERS)
    plan["universe_size"] = len(SPEC_ALPHA_UNIVERSE)
    # Defensive scrub in case a cached/scored object somehow leaks through.
    for key in ["top", "actions", "actionable", "all_scored"]:
        rows = plan.get(key)
        if isinstance(rows, list):
            plan[key] = [r for r in rows if str(r.get("ticker", "")).upper() not in SPEC_ALPHA_EXCLUDED_TICKERS]
    return plan


_V411_HOTFIX_OLD_CURRENT_SPEC_PLAN_FOR_VALIDATION = current_spec_plan_for_validation

def _v411_hotfix_plan_contains_excluded(plan: Dict[str, Any]) -> bool:
    for key in ["top", "actions", "actionable", "all_scored"]:
        rows = plan.get(key)
        if isinstance(rows, list):
            for row in rows:
                if str(row.get("ticker", "")).upper() in SPEC_ALPHA_EXCLUDED_TICKERS:
                    return True
    return False

def current_spec_plan_for_validation() -> Dict[str, Any]:
    latest = load_latest_spec_plan()
    if latest is not None:
        try:
            age_days = (now_ts() - float(latest.get("time", 0))) / 86400
            plan = latest.get("plan") or {}
            if age_days <= SPEC_ALPHA_PLAN_VALID_DAYS and plan and not _v411_hotfix_plan_contains_excluded(plan):
                return plan
        except Exception:
            pass
    plan = compute_spec_alpha_plan()
    save_spec_plan_signal(plan)
    return plan

_V411_HOTFIX_OLD_FORMAT_SPEC_PLAN = format_spec_alpha_plan

def format_spec_alpha_plan(plan: Dict[str, Any]) -> str:
    msg = _V411_HOTFIX_OLD_FORMAT_SPEC_PLAN(plan)
    excluded = plan.get("excluded_tickers") or []
    if excluded:
        note = (
            "\n\n🧹 v4.1.1 hotfix cleanup:\n"
            f"Blocked stale SPEC tickers: {', '.join(excluded)}.\n"
            "No replacement ticker is used automatically."
        )
        if len(msg) + len(note) < MAX_TELEGRAM_MESSAGE:
            msg += note
    return msg[:MAX_TELEGRAM_MESSAGE]

_V411_HOTFIX_OLD_FORMAT_PUBLIC_SPEC_PLAN = format_public_spec_plan

def format_public_spec_plan(plan: Dict[str, Any]) -> str:
    msg = _V411_HOTFIX_OLD_FORMAT_PUBLIC_SPEC_PLAN(plan)
    excluded = plan.get("excluded_tickers") or []
    if excluded:
        note = f"\n\nOperational cleanup: excluded stale tickers {', '.join(excluded)}."
        if len(msg) + len(note) < MAX_TELEGRAM_MESSAGE:
            msg += note
    return msg[:MAX_TELEGRAM_MESSAGE]

_V411_HOTFIX_OLD_GROWTH_STATUS_REPORT = format_growth_alpha_plan

def format_growth_alpha_plan(plan: Dict[str, Any]) -> str:
    msg = _V411_HOTFIX_OLD_GROWTH_STATUS_REPORT(plan)
    if plan.get("market_ok") and int(plan.get("scored_count", 0) or 0) == 0 and float(plan.get("target_growth_account_pct", 0) or 0) > 0:
        note = "\n\n⚠️ Growth market filter passed but no tickers scored. Check FMP data/quotes before trading."
        if len(msg) + len(note) < MAX_TELEGRAM_MESSAGE:
            msg += note
    return msg[:MAX_TELEGRAM_MESSAGE]

_V411_HOTFIX_OLD_BEAR_STATUS_COMMAND = handle_command

def handle_command(text: str, update_id: Optional[int] = None) -> None:
    text_clean = (text or "").strip()
    text_lower = text_clean.lower()
    if text_lower == "bearstatus":
        send(
            "🐻 BEAR SLEEVE STATUS v4.1.1\n\n"
            f"Enabled: {yes_no(BEAR_SLEEVE_ENABLED)}\n"
            "Bear-stock / inverse bear tactical is disabled in v4.1.1.\n"
            "Crypto tactical swing is the researched replacement sleeve.\n\n"
            "Use cryptostatus / cryptoplan for the active crypto tactical sleeve."
        )
        return
    return _V411_HOTFIX_OLD_BEAR_STATUS_COMMAND(text_clean, update_id=update_id)


# -----------------------------------------------------------------------------
# V4.1.1 HOTFIX 2026-05-27
# -----------------------------------------------------------------------------
# Operational bug fixes only. No strategy research changes.
# 1) Fix Growth Alpha market-filter DataFrame truth-value bug.
# 2) Block stale SPEC corporate-action tickers HOUS/AMRK from plans and buys.
# 3) Extend institutional diagnostics to Growth Alpha and Crypto Alpha ledgers.
# 4) Default Core/SPEC public monthly plan forwarding to OFF unless explicitly enabled.

V411_HOTFIX_VERSION = "v4.1.1-hotfix-20260527"

# Public monthly plan forwarding should be explicitly enabled, not default-on.
# This does not affect private Telegram commands. If Railway variables explicitly set
# CORE_PUBLIC_SIGNAL_ENABLED=1 or SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED=1, they still win.
CORE_PUBLIC_SIGNAL_ENABLED = os.getenv("CORE_PUBLIC_SIGNAL_ENABLED", "0").strip() == "1"
SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED = os.getenv("SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED", "0").strip() == "1"

# Remove known stale corporate-action names from SPEC_ALPHA. These names should not
# be planned, publicly shown, or accepted for manual specbuy. Do not substitute a
# different ticker without separate offline research.
SPEC_ALPHA_BLOCKLIST = {
    x.strip().upper()
    for x in os.getenv("SPEC_ALPHA_BLOCKLIST", "HOUS,AMRK").split(",")
    if x.strip()
}
SPEC_ALPHA_UNIVERSE = [
    t for t in SPEC_ALPHA_UNIVERSE
    if str(t).upper() not in SPEC_ALPHA_BLOCKLIST
]


def _v411_plan_has_blocked_spec_ticker(plan: Dict[str, Any]) -> bool:
    try:
        for key in ("top", "actions", "actionable", "all_scored"):
            for item in plan.get(key, []) or []:
                ticker = str(item.get("ticker", "")).upper()
                if ticker in SPEC_ALPHA_BLOCKLIST:
                    return True
    except Exception:
        return True
    return False


# Override the Growth Alpha market filter to avoid evaluating a pandas DataFrame
# as a boolean. The old code used `frames.get(symbol) or get_signal_dataframe(...)`,
# which raises: "The truth value of a DataFrame is ambiguous".
def growth_alpha_market_filter_ok(market_details: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    try:
        details = market_details if market_details is not None else market_regime_details()
        if int(details.get("score", 0) or 0) < GROWTH_ALPHA_REQUIRE_MARKET_SCORE:
            return False, "market score below threshold"

        if GROWTH_ALPHA_REQUIRE_SPY_QQQ_ABOVE_MA200:
            raw_frames = details.get("frames", {})
            frames = raw_frames if isinstance(raw_frames, dict) else {}
            for symbol in ["SPY", "QQQ"]:
                df = frames.get(symbol)
                if df is None or (hasattr(df, "empty") and bool(df.empty)):
                    df = get_signal_dataframe(symbol, limit=260)
                last, ma = frame_last_close_ma(df, 200)
                if last is None or ma is None or last <= ma:
                    return False, f"{symbol} below MA200"
        return True, "OK"
    except Exception as exc:
        logger.exception(f"[GROWTH MARKET FILTER HOTFIX ERROR] {exc}")
        return False, f"market filter error: {exc}"


# Ignore old active SPEC plans if they contain blocked corporate-action tickers.
def current_spec_plan_for_validation() -> Dict[str, Any]:
    latest = load_latest_spec_plan()
    if latest is not None:
        try:
            age_days = (now_ts() - float(latest.get("time", 0))) / 86400
            plan = latest.get("plan") or {}
            if age_days <= SPEC_ALPHA_PLAN_VALID_DAYS and plan and not _v411_plan_has_blocked_spec_ticker(plan):
                return plan
        except Exception:
            pass
    plan = compute_spec_alpha_plan()
    save_spec_plan_signal(plan)
    return plan


_V411_OLD_RECORD_SPEC_BUY = record_spec_buy

def record_spec_buy(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
    ticker_norm = normalize_ticker(ticker) or ""
    if ticker_norm in SPEC_ALPHA_BLOCKLIST:
        return False, (
            f"SPEC_ALPHA buy rejected: {ticker_norm} is blocked by the v4.1.1 corporate-action/stale-ticker list.\n"
            "No substitute ticker is allowed without offline research."
        )
    return _V411_OLD_RECORD_SPEC_BUY(ticker, shares, price, update_id=update_id)


# Make public SPEC output robust even if an old cached plan is accidentally used.
def format_public_spec_plan(plan: Dict[str, Any]) -> str:
    actions = plan.get("actions", []) or []
    ranked = [
        a for a in actions
        if a.get("rank") is not None and str(a.get("ticker", "")).upper() not in SPEC_ALPHA_BLOCKLIST
    ]
    exits = [a for a in actions if str(a.get("action")).upper() == "SELL"]
    msg = "⚡ SPEC_ALPHA ROTATION PLAN\n\nMedium/weak monthly momentum sleeve. No share counts. Use your own account size.\n\n"
    msg += (
        f"🌎 Market: {market_label(str(plan.get('market', 'UNKNOWN')))} | "
        f"Market filter: {yes_no(bool(plan.get('market_ok')))}\n"
        f"🎯 Target SPEC sleeve: {plan.get('target_spec_account_pct')}% of account\n"
        f"🎚️ Mode: {plan.get('score_mode')} | Top {plan.get('top_n')}\n"
        f"🚫 Excluded stale tickers: {', '.join(sorted(SPEC_ALPHA_BLOCKLIST))}\n\n"
    )
    for item in ranked[:SPEC_ALPHA_TOP_N]:
        action = str(item.get("action", "HOLD")).upper()
        verb = {"BUY": "🟢 BUY", "ADD": "🟢 ADD", "HOLD": "🟡 HOLD", "TRIM": "🟠 TRIM"}.get(action, action)
        msg += (
            f"{item.get('rank')}) {verb} {item['ticker']} ({item.get('sector', 'Unknown')})\n"
            f"Target: {item.get('target_account_pct')}% of account | Price: {item.get('price')}\n"
            f"1m {format_pct(item.get('roc_1m_pct'))} | 3m {format_pct(item.get('roc_3m_pct'))} | "
            f"6m {format_pct(item.get('roc_6m_pct'))}\n"
            f"Score: {item.get('score')}\n\n"
        )
    if exits:
        msg += "🔴 Rotation exits:\n"
        for item in exits[:10]:
            msg += f"SELL/REMOVE {item['ticker']} — {item.get('reason', 'No longer selected')}\n"
        msg += "\n"
    msg += public_signal_footer()
    return msg[:MAX_TELEGRAM_MESSAGE]


# Diagnostics helpers including Growth Alpha and Crypto Alpha ledgers.
def _v411_hotfix_cluster_for_ticker(ticker: str) -> str:
    t = str(ticker).upper()
    if "GROWTH_ALPHA_CLUSTER_MAP" in globals() and t in GROWTH_ALPHA_CLUSTER_MAP:
        return GROWTH_ALPHA_CLUSTER_MAP.get(t, "growth_other")
    if "CRYPTO_ALPHA_UNIVERSE" in globals() and t in set(CRYPTO_ALPHA_UNIVERSE) | set(CRYPTO_ALPHA_INDICATORS):
        return "crypto_alpha"
    return _v38_cluster_for_ticker(t)


def _v38_collect_holdings(prices: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
    refresh_portfolio()
    swing_positions = portfolio.get("positions", {}) or {}
    core_positions = load_core_positions() if globals().get("CORE_LEDGER_ENABLED", False) else {}
    spec_positions = load_spec_positions() if globals().get("SPEC_ALPHA_LEDGER_ENABLED", False) else {}
    growth_positions = load_growth_positions() if globals().get("GROWTH_ALPHA_LEDGER_ENABLED", False) else {}
    crypto_positions = load_crypto_positions() if globals().get("CRYPTO_ALPHA_LEDGER_ENABLED", False) else {}

    tickers = list(dict.fromkeys(
        list(swing_positions.keys()) + list(core_positions.keys()) + list(spec_positions.keys()) +
        list(growth_positions.keys()) + list(crypto_positions.keys())
    ))
    if prices is None:
        prices = get_prices_batch(tickers) if tickers else {}

    holdings: List[Dict[str, Any]] = []

    for ticker, pos in swing_positions.items():
        shares = _v38_float(pos.get("shares"), 0.0)
        entry = _v38_float(pos.get("price"), 0.0)
        price = _v38_float(prices.get(ticker, entry), entry)
        value = shares * price
        holdings.append({
            "ticker": ticker, "ledger": "swing", "sleeve": _v38_entry_sleeve_from_pos(ticker, pos),
            "cluster": _v411_hotfix_cluster_for_ticker(ticker), "shares": shares,
            "entry_price": entry, "mark_price": price, "market_value": round(value, 2),
            "cost_basis": round(entry * shares, 2), "unrealized_profit": round((price - entry) * shares, 2),
            "stop": pos.get("stop"), "highest": pos.get("highest"),
        })

    for ledger_name, sleeve_name, positions in [
        ("core", "CORE_WEALTH", core_positions),
        ("spec", "SPEC_ALPHA", spec_positions),
        ("growth", "GROWTH_ALPHA", growth_positions),
    ]:
        for ticker, pos in positions.items():
            shares = _v38_float(pos.get("shares"), 0.0)
            entry = _v38_float(pos.get("avg_entry_price"), 0.0)
            cost_basis = _v38_float(pos.get("cost_basis"), entry * shares)
            price = _v38_float(prices.get(ticker, entry), entry)
            value = shares * price
            holdings.append({
                "ticker": ticker, "ledger": ledger_name, "sleeve": sleeve_name,
                "cluster": _v411_hotfix_cluster_for_ticker(ticker), "shares": shares,
                "entry_price": entry, "mark_price": price, "market_value": round(value, 2),
                "cost_basis": round(cost_basis, 2), "unrealized_profit": round(value - cost_basis, 2),
                "target_account_pct": pos.get("target_account_pct"),
            })

    for ticker, pos in crypto_positions.items():
        units = _v38_float(pos.get("units"), 0.0)
        entry = _v38_float(pos.get("avg_entry_price"), 0.0)
        cost_basis = _v38_float(pos.get("cost_basis"), entry * units)
        price = _v38_float(prices.get(ticker, entry), entry)
        value = units * price
        holdings.append({
            "ticker": ticker, "ledger": "crypto", "sleeve": "CRYPTO_ALPHA",
            "cluster": "crypto_alpha", "shares": units, "entry_price": entry,
            "mark_price": price, "market_value": round(value, 2),
            "cost_basis": round(cost_basis, 2), "unrealized_profit": round(value - cost_basis, 2),
            "target_account_pct": pos.get("target_account_pct"), "stop": pos.get("stop"), "highest": pos.get("highest"),
        })

    return holdings


def institutional_datahealth_snapshot() -> Dict[str, Any]:
    refresh_portfolio()
    swing_positions = portfolio.get("positions", {}) or {}
    core_positions = load_core_positions() if globals().get("CORE_LEDGER_ENABLED", False) else {}
    spec_positions = load_spec_positions() if globals().get("SPEC_ALPHA_LEDGER_ENABLED", False) else {}
    growth_positions = load_growth_positions() if globals().get("GROWTH_ALPHA_LEDGER_ENABLED", False) else {}
    crypto_positions = load_crypto_positions() if globals().get("CRYPTO_ALPHA_LEDGER_ENABLED", False) else {}
    tickers = list(dict.fromkeys(
        list(swing_positions.keys()) + list(core_positions.keys()) + list(spec_positions.keys()) +
        list(growth_positions.keys()) + list(crypto_positions.keys())
    ))
    prices = get_prices_batch(tickers) if tickers else {}
    holdings = _v38_collect_holdings(prices)

    missing_quotes = [t for t in tickers if t not in prices]
    bad_values = []
    stop_warnings = []
    for h in holdings:
        if h["shares"] <= 0 or h["entry_price"] <= 0 or h["mark_price"] <= 0:
            bad_values.append(h["ticker"])
        if h["ledger"] == "swing":
            stop = _v38_float(h.get("stop"), 0.0)
            if stop <= 0:
                stop_warnings.append({"ticker": h["ticker"], "issue": "missing_or_invalid_stop"})
        if h["ledger"] == "crypto":
            stop = _v38_float(h.get("stop"), 0.0)
            if stop <= 0:
                stop_warnings.append({"ticker": h["ticker"], "issue": "missing_crypto_stop"})

    status = "OK"
    if missing_quotes or bad_values or len(stop_warnings) >= 3:
        status = "WARNING"
    if _v38_float(portfolio.get("cash"), 0.0) < 0:
        status = "CRITICAL"

    return {
        "status": status,
        "ny_time": ny_now().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "expected_daily_bar_date": expected_daily_bar_date(),
        "last_scan_day": get_meta("last_scan_day"),
        "last_scan_bar_date": get_meta("last_scan_bar_date"),
        "panic_mode": PANIC_MODE,
        "cash_negative": _v38_float(portfolio.get("cash"), 0.0) < 0,
        "holdings_count": len(holdings),
        "quote_tickers_requested": len(tickers),
        "quote_tickers_received": len(prices),
        "missing_quotes": missing_quotes,
        "bad_value_tickers": bad_values,
        "stop_warnings": stop_warnings,
        "notes": [
            "On-demand diagnostic only; does not block trades.",
            "Includes swing, core, SPEC, Growth Alpha, and Crypto Alpha ledgers after v4.1.1 hotfix.",
        ],
    }


def institutional_stress_snapshot() -> Dict[str, Any]:
    risk = institutional_riskmatrix_snapshot()
    holdings = _v38_collect_holdings()
    equity = _v38_float(risk.get("equity"), 0.0)

    scenarios = {
        "broad_risk_off": {
            "description": "Broad risk-off: core -8%, growth -18%, SPEC -18%, VCP -10%, crypto -25%.",
            "default": -0.08,
            "CORE_WEALTH": -0.08,
            "GROWTH_ALPHA": -0.18,
            "SPEC_ALPHA": -0.18,
            "LONG_VCP_OR_TACTICAL": -0.10,
            "CRYPTO_ALPHA": -0.25,
        },
        "growth_spec_unwind": {
            "description": "Growth/SPEC momentum unwind: growth -22%, SPEC -25%, crypto -20%, core -4%.",
            "default": -0.04,
            "GROWTH_ALPHA": -0.22,
            "SPEC_ALPHA": -0.25,
            "CRYPTO_ALPHA": -0.20,
            "LONG_VCP_OR_TACTICAL": -0.08,
        },
        "growth_semis_ai_shock": {
            "description": "AI/semis/growth shock: mapped growth clusters -22%, SPEC -12%, crypto -15%.",
            "default": -0.05,
            "GROWTH_ALPHA": -0.12,
            "SPEC_ALPHA": -0.12,
            "CRYPTO_ALPHA": -0.15,
            "LONG_VCP_OR_TACTICAL": -0.10,
            "cluster_overrides": {
                "semis_ai": -0.22,
                "software_ai": -0.20,
                "ai_infrastructure": -0.20,
                "ai_power": -0.18,
                "mega_platforms": -0.16,
            },
        },
        "crypto_flush": {
            "description": "Crypto flush: crypto -35%, Growth -8%, SPEC -8%, Core -2%.",
            "default": -0.02,
            "CRYPTO_ALPHA": -0.35,
            "GROWTH_ALPHA": -0.08,
            "SPEC_ALPHA": -0.08,
            "LONG_VCP_OR_TACTICAL": -0.05,
        },
    }

    results = []
    for name, cfg in scenarios.items():
        pnl = 0.0
        for h in holdings:
            value = _v38_float(h.get("market_value"), 0.0)
            sleeve = h.get("sleeve")
            shock = cfg.get(sleeve, cfg.get("default", 0.0))
            cluster_overrides = cfg.get("cluster_overrides", {}) or {}
            if h.get("cluster") in cluster_overrides:
                shock = cluster_overrides[h.get("cluster")]
            pnl += value * float(shock)
        results.append({
            "scenario": name,
            "description": cfg.get("description"),
            "pnl": round(pnl, 2),
            "pct_equity": round(_v38_pct(pnl, equity), 2),
        })

    worst = min(results, key=lambda x: x.get("pnl", 0.0), default={"scenario": "none", "pnl": 0.0, "pct_equity": 0.0})
    return {
        "status": "WARNING" if worst.get("pct_equity", 0.0) <= -10 else "OK",
        "equity": round(equity, 2),
        "worst_scenario": worst,
        "scenarios": results,
        "notes": ["Approximate monitoring only; does not block trades.", "v4.1.1 hotfix scenarios include Growth and Crypto sleeves."],
    }


def format_riskmatrix_status() -> str:
    r = institutional_riskmatrix_snapshot()
    equity = float(r.get("equity", 0) or 0)
    ledgers = "\n".join(
        f"• {x['ledger']}: {format_money(float(x['value']))} ({x['account_pct']}%)"
        for x in r.get("ledger_exposure", [])
    )
    clusters = "\n".join(
        f"• {x['cluster']}: {format_money(float(x['value']))} ({x['account_pct']}%)"
        for x in r.get("cluster_exposure", [])[:10]
    )
    positions = "\n".join(
        f"• {x['ticker']} [{x.get('ledger')}]: {format_money(float(x['market_value']))} ({x.get('account_pct')}%)"
        for x in r.get("top_positions", [])[:10]
    )
    warnings = "\n".join(f"⚠️ {w}" for w in r.get("warnings", [])) or "✅ No concentration warnings."
    return (
        "🧮 RISK MATRIX v4.2.1 RECON\n\n"
        f"Status: {_v38_status_emoji(r.get('status'))} {r.get('status')}\n"
        f"Total equity: {format_money(equity)}\n\n"
        "Ledger exposure:\n" + (ledgers or "No holdings.") + "\n\n"
        "Top clusters:\n" + (clusters or "No holdings.") + "\n\n"
        "Top positions:\n" + (positions or "No holdings.") + "\n\n"
        f"{warnings}"
    )


def format_stress_status() -> str:
    s = institutional_stress_snapshot()
    worst = s.get("worst_scenario", {}) or {}
    rows = "\n".join(
        f"• {x['scenario']}: {format_money(float(x['pnl']))} ({x['pct_equity']}%)"
        for x in s.get("scenarios", [])
    )
    return (
        "🔥 STRESS STATUS v4.2.1 RECON\n\n"
        f"Status: {_v38_status_emoji(s.get('status'))} {s.get('status')}\n"
        f"Equity: {format_money(float(s.get('equity', 0) or 0))}\n"
        f"Worst scenario: {worst.get('scenario')} {format_money(float(worst.get('pnl', 0) or 0))} "
        f"({worst.get('pct_equity')}%)\n\n"
        f"{rows}\n\n"
        "Approximate monitoring only. It does not block trades."
    )


# Add a small hotfix status command without replacing existing command handling.
_V411_HOTFIX_OLD_HANDLE_COMMAND = handle_command

def handle_command(text: str, update_id: Optional[int] = None) -> None:
    text_clean = (text or "").strip()
    text_lower = text_clean.lower()
    if text_lower in {"hotfixstatus", "v411hotfix"}:
        market_ok, reason = growth_alpha_market_filter_ok()
        send(
            "🛠️ V4.1.1 HOTFIX STATUS\n\n"
            f"Version: {V411_HOTFIX_VERSION}\n"
            f"Growth market filter: {yes_no(market_ok)} — {reason}\n"
            f"SPEC blocklist: {', '.join(sorted(SPEC_ALPHA_BLOCKLIST))}\n"
            f"SPEC universe size: {len(SPEC_ALPHA_UNIVERSE)}\n"
            f"Core public enabled: {yes_no(CORE_PUBLIC_SIGNAL_ENABLED)}\n"
            f"SPEC public enabled: {yes_no(SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED)}\n"
            "Diagnostics now include Growth and Crypto holdings."
        )
        return
    return _V411_HOTFIX_OLD_HANDLE_COMMAND(text_clean, update_id=update_id)



# -----------------------------------------------------------------------------
# V4.2 IBKR READ-ONLY RECONCILIATION LAYER
# -----------------------------------------------------------------------------
# Purpose:
# - Connect the Railway bot to an external IBKR bridge snapshot endpoint.
# - Reconcile broker cash/positions with bot-ledger positions.
# - Treat broker positions that are not owned by bot ledgers as EXTERNAL_LEGACY.
# - Do NOT place broker orders in this version.
# - Do NOT blindly import broker portfolio into strategy ledgers.
# - Optional supervised sync updates bot cash and matching managed positions from
#   broker average cost, but only after explicit Telegram confirmation.

V42_VERSION = "v4.2.1-ibkr-readonly-reconcile-hotfix-20260528"

# Display label fix: if the environment is empty or still has the old v4.1.1
# hotfix label, show this as the v4.2.1 reconciliation candidate.
# If the operator intentionally sets a different STRATEGY_VERSION, keep it.
_STRATEGY_ENV_RAW = os.getenv("STRATEGY_VERSION", "").strip()
if _STRATEGY_ENV_RAW in {"", "v4.1.1-hotfix-growth-spec-clean-20-45-20-5-10-monitor"}:
    STRATEGY_VERSION = "v4.2.1-ibkr-reconcile-v4.1.1-hotfix-20-45-20-5-10-monitor"
IBKR_RECON_ENABLED = os.getenv("IBKR_RECON_ENABLED", "1") != "0"
IBKR_RECON_AUTO_ENABLED = os.getenv("IBKR_RECON_AUTO_ENABLED", "0") == "1"
IBKR_RECON_AFTER_CLOSE_MINUTE = int(os.getenv("IBKR_RECON_AFTER_CLOSE_MINUTE", str(16 * 60 + 10)))
IBKR_BRIDGE_URL = os.getenv("IBKR_BRIDGE_URL", "").strip().rstrip("/")
IBKR_BRIDGE_TOKEN = os.getenv("IBKR_BRIDGE_TOKEN", "").strip()
IBKR_SNAPSHOT_FILE = os.getenv("IBKR_SNAPSHOT_FILE", "").strip()
IBKR_RECON_CASH_TOLERANCE = float(os.getenv("IBKR_RECON_CASH_TOLERANCE", "5.0"))
IBKR_RECON_QTY_TOLERANCE = float(os.getenv("IBKR_RECON_QTY_TOLERANCE", "0.0005"))
IBKR_RECON_VALUE_TOLERANCE = float(os.getenv("IBKR_RECON_VALUE_TOLERANCE", "2.0"))
IBKR_SYNC_ALLOW_CASH = os.getenv("IBKR_SYNC_ALLOW_CASH", "1") != "0"
IBKR_SYNC_ALLOW_AVG_COST = os.getenv("IBKR_SYNC_ALLOW_AVG_COST", "1") != "0"
IBKR_SYNC_ALLOW_QTY = os.getenv("IBKR_SYNC_ALLOW_QTY", "1") != "0"
IBKR_BRIDGE_TIMEOUT = float(os.getenv("IBKR_BRIDGE_TIMEOUT", "45"))
IBKR_BRIDGE_RETRIES = int(os.getenv("IBKR_BRIDGE_RETRIES", "2"))


def _v42_float(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
        if math.isfinite(x):
            return x
    except Exception:
        pass
    return default


def _v42_round(value: Any, digits: int = 2) -> float:
    return round(_v42_float(value), digits)


def _v42_account_map(snapshot: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for row in snapshot.get("account_summary", []) or []:
        try:
            # [account, tag, value, currency, model]
            if len(row) >= 3:
                tag = str(row[1])
                value = str(row[2])
                ccy = str(row[3]) if len(row) > 3 else ""
                if ccy in {"", "USD", "BASE"}:
                    out[tag] = value
        except Exception:
            continue
    for row in snapshot.get("account_values", []) or []:
        try:
            if len(row) >= 3:
                tag = str(row[1])
                value = str(row[2])
                ccy = str(row[3]) if len(row) > 3 else ""
                if ccy in {"", "USD", "BASE"} and tag not in out:
                    out[tag] = value
        except Exception:
            continue
    return out


def _v42_broker_cash(snapshot: Dict[str, Any]) -> float:
    m = _v42_account_map(snapshot)
    for key in ["TotalCashValue", "TotalCashBalance", "CashBalance", "AvailableFunds-S"]:
        if key in m:
            return _v42_float(m[key])
    return 0.0


def _v42_broker_netliq(snapshot: Dict[str, Any]) -> float:
    m = _v42_account_map(snapshot)
    for key in ["NetLiquidation", "EquityWithLoanValue"]:
        if key in m:
            return _v42_float(m[key])
    return 0.0


def _v42_broker_grosspos(snapshot: Dict[str, Any]) -> float:
    m = _v42_account_map(snapshot)
    return _v42_float(m.get("GrossPositionValue", 0.0))


def _v42_normalize_broker_symbol(raw: Any) -> str:
    sym = normalize_ticker(str(raw or ""))
    return sym or str(raw or "").strip().upper()


def _v42_broker_positions(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    positions: Dict[str, Dict[str, Any]] = {}
    for p in snapshot.get("portfolio", []) or []:
        try:
            contract = p.get("contract") or {}
            symbol = _v42_normalize_broker_symbol(contract.get("symbol") or contract.get("localSymbol"))
            if not symbol:
                continue
            qty = _v42_float(p.get("position"))
            if abs(qty) <= 1e-12:
                continue
            positions[symbol] = {
                "ticker": symbol,
                "qty": qty,
                "market_price": _v42_float(p.get("marketPrice")),
                "market_value": _v42_float(p.get("marketValue")),
                "avg_cost": _v42_float(p.get("averageCost")),
                "unrealized_pnl": _v42_float(p.get("unrealizedPNL")),
                "realized_pnl": _v42_float(p.get("realizedPNL")),
                "sec_type": str(contract.get("secType") or ""),
                "currency": str(contract.get("currency") or ""),
                "exchange": str(contract.get("primaryExchange") or contract.get("exchange") or ""),
                "con_id": contract.get("conId"),
                "local_symbol": str(contract.get("localSymbol") or symbol),
            }
        except Exception:
            continue
    return positions


def _v42_bot_managed_positions() -> Dict[str, List[Dict[str, Any]]]:
    refresh_portfolio()
    rows: Dict[str, List[Dict[str, Any]]] = {}

    def add(ticker: str, ledger: str, qty: float, avg: float, cost: float, position_id: str = "") -> None:
        t = str(ticker).upper()
        rows.setdefault(t, []).append({
            "ticker": t,
            "ledger": ledger,
            "qty": float(qty),
            "avg_entry_price": float(avg),
            "cost_basis": float(cost),
            "position_id": position_id,
        })

    for ticker, pos in (portfolio.get("positions") or {}).items():
        add(ticker, "TACTICAL", float(pos.get("shares", 0) or 0), float(pos.get("price", 0) or 0), float(pos.get("shares", 0) or 0) * float(pos.get("price", 0) or 0), str(pos.get("position_id", "")))

    try:
        for ticker, pos in load_core_positions().items():
            add(ticker, "CORE", pos.get("shares", 0), pos.get("avg_entry_price", 0), pos.get("cost_basis", 0), pos.get("core_position_id", ""))
    except Exception:
        pass
    try:
        for ticker, pos in load_growth_positions().items():
            add(ticker, "GROWTH", pos.get("shares", 0), pos.get("avg_entry_price", 0), pos.get("cost_basis", 0), pos.get("growth_position_id", ""))
    except Exception:
        pass
    try:
        for ticker, pos in load_spec_positions().items():
            add(ticker, "SPEC", pos.get("shares", 0), pos.get("avg_entry_price", 0), pos.get("cost_basis", 0), pos.get("spec_position_id", ""))
    except Exception:
        pass
    try:
        for ticker, pos in load_crypto_positions().items():
            add(ticker, "CRYPTO", pos.get("units", 0), pos.get("avg_entry_price", 0), pos.get("cost_basis", 0), pos.get("crypto_position_id", ""))
    except Exception:
        pass
    return rows


def _v42_bot_cash() -> float:
    refresh_portfolio()
    return float(portfolio.get("cash", 0.0) or 0.0)


# --- DB extension ---
_V42_OLD_INIT_DB = init_db

def init_db() -> None:
    _V42_OLD_INIT_DB()
    conn = db_connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS broker_snapshots (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL DEFAULT 'IBKR',
                account TEXT,
                created_utc TEXT,
                imported_at REAL NOT NULL,
                broker_cash REAL,
                broker_netliq REAL,
                broker_gross_positions REAL,
                raw_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_broker_snapshots_imported_at ON broker_snapshots(imported_at);

            CREATE TABLE IF NOT EXISTS broker_position_snapshots (
                snapshot_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                sec_type TEXT,
                currency TEXT,
                exchange_name TEXT,
                con_id TEXT,
                qty REAL NOT NULL,
                market_price REAL,
                market_value REAL,
                avg_cost REAL,
                unrealized_pnl REAL,
                classification TEXT NOT NULL DEFAULT 'UNKNOWN',
                matched_ledger TEXT,
                created_at REAL NOT NULL,
                PRIMARY KEY(snapshot_id, ticker)
            );
            CREATE INDEX IF NOT EXISTS idx_broker_position_snapshots_ticker ON broker_position_snapshots(ticker);

            CREATE TABLE IF NOT EXISTS broker_reconcile_events (
                id TEXT PRIMARY KEY,
                snapshot_id TEXT,
                time REAL NOT NULL,
                status TEXT NOT NULL,
                summary_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _v42_store_snapshot(snapshot: Dict[str, Any], classification: Optional[Dict[str, Any]] = None) -> str:
    sid = str(snapshot.get("snapshot_id") or snapshot.get("id") or uuid.uuid4().hex)
    acct = None
    try:
        acct = ((snapshot.get("connection") or {}).get("account_selected") or (snapshot.get("managed_accounts") or [None])[0])
    except Exception:
        acct = None
    imported = now_ts()
    raw = json_dumps(snapshot)
    broker_positions = _v42_broker_positions(snapshot)
    by_class = classification or {}
    matched_ledgers = {}
    classes = {}
    for k in by_class.get("matched", []) or []:
        classes[str(k.get("ticker", "")).upper()] = "MANAGED_MATCHED"
        matched_ledgers[str(k.get("ticker", "")).upper()] = str(k.get("ledger", ""))
    for k in by_class.get("external", []) or []:
        classes[str(k.get("ticker", "")).upper()] = "EXTERNAL_LEGACY"
    for k in by_class.get("missing_in_broker", []) or []:
        classes[str(k.get("ticker", "")).upper()] = "BOT_MISSING_IN_BROKER"
    for k in by_class.get("ambiguous", []) or []:
        classes[str(k.get("ticker", "")).upper()] = "AMBIGUOUS"

    with db_tx() as conn:
        conn.execute(
            """
            INSERT INTO broker_snapshots(id, source, account, created_utc, imported_at,
                broker_cash, broker_netliq, broker_gross_positions, raw_json)
            VALUES (?, 'IBKR', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET raw_json = excluded.raw_json, imported_at = excluded.imported_at
            """,
            (
                sid,
                str(acct or ""),
                str(snapshot.get("created_utc") or ""),
                imported,
                _v42_broker_cash(snapshot),
                _v42_broker_netliq(snapshot),
                _v42_broker_grosspos(snapshot),
                raw,
            ),
        )
        conn.execute("DELETE FROM broker_position_snapshots WHERE snapshot_id = ?", (sid,))
        for ticker, bp in broker_positions.items():
            conn.execute(
                """
                INSERT INTO broker_position_snapshots(snapshot_id, ticker, sec_type, currency, exchange_name,
                    con_id, qty, market_price, market_value, avg_cost, unrealized_pnl,
                    classification, matched_ledger, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sid, ticker, bp.get("sec_type"), bp.get("currency"), bp.get("exchange"), str(bp.get("con_id") or ""),
                    bp.get("qty"), bp.get("market_price"), bp.get("market_value"), bp.get("avg_cost"), bp.get("unrealized_pnl"),
                    classes.get(ticker, "UNKNOWN"), matched_ledgers.get(ticker, ""), imported,
                ),
            )
    return sid


def _v42_latest_snapshot_from_db() -> Optional[Dict[str, Any]]:
    conn = db_connect()
    try:
        row = conn.execute("SELECT raw_json FROM broker_snapshots ORDER BY imported_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        return json_loads_dict(row["raw_json"])
    finally:
        conn.close()


def _v42_fetch_snapshot() -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    if not IBKR_RECON_ENABLED:
        return False, "IBKR reconciliation is disabled", None
    if IBKR_BRIDGE_URL:
        url = IBKR_BRIDGE_URL.rstrip("/") + "/snapshot"
        headers = {}
        params = {}
        if IBKR_BRIDGE_TOKEN:
            headers["X-IBKR-Bridge-Token"] = IBKR_BRIDGE_TOKEN
            params["token"] = IBKR_BRIDGE_TOKEN
        last_exc: Optional[Exception] = None
        attempts = max(1, IBKR_BRIDGE_RETRIES + 1)
        for attempt in range(attempts):
            try:
                res = SESSION.get(url, headers=headers, params=params, timeout=IBKR_BRIDGE_TIMEOUT)
                if res.status_code >= 400:
                    return False, f"Bridge HTTP {res.status_code}: {res.text[:300]}", None
                data = res.json()
                if not isinstance(data, dict):
                    return False, "Bridge returned non-object JSON", None
                return True, "fresh bridge snapshot", data
            except Exception as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    time.sleep(1.0 * (attempt + 1))
        cached = _v42_latest_snapshot_from_db()
        if cached:
            return True, f"latest stored snapshot fallback after bridge fetch failed: {last_exc}", cached
        return False, f"Bridge fetch failed: {last_exc}", None
    if IBKR_SNAPSHOT_FILE:
        try:
            with open(IBKR_SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return False, "Snapshot file returned non-object JSON", None
            return True, "snapshot file", data
        except Exception as exc:
            return False, f"Snapshot file failed: {exc}", None
    data = _v42_latest_snapshot_from_db()
    if data:
        return True, "latest stored snapshot", data
    return False, "No IBKR bridge URL/file configured and no stored snapshot exists", None


def _v42_reconcile_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    broker = _v42_broker_positions(snapshot)
    bot = _v42_bot_managed_positions()
    broker_cash = _v42_broker_cash(snapshot)
    bot_cash = _v42_bot_cash()
    broker_netliq = _v42_broker_netliq(snapshot)
    broker_gross = _v42_broker_grosspos(snapshot)

    matched: List[Dict[str, Any]] = []
    external: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    ambiguous: List[Dict[str, Any]] = []

    for ticker, bpos in sorted(broker.items()):
        owners = bot.get(ticker, [])
        if not owners:
            row = dict(bpos)
            row["classification"] = "EXTERNAL_LEGACY"
            external.append(row)
        elif len(owners) > 1:
            ambiguous.append({"ticker": ticker, "broker": bpos, "bot_ledgers": owners, "reason": "Ticker exists in multiple bot ledgers; broker snapshot only has aggregate position."})
        else:
            owner = owners[0]
            qty_diff = _v42_float(bpos.get("qty")) - _v42_float(owner.get("qty"))
            value_diff = _v42_float(bpos.get("market_value")) - (_v42_float(owner.get("qty")) * _v42_float(bpos.get("market_price")))
            avg_diff = _v42_float(bpos.get("avg_cost")) - _v42_float(owner.get("avg_entry_price"))
            matched.append({
                "ticker": ticker,
                "ledger": owner.get("ledger"),
                "broker_qty": _v42_float(bpos.get("qty")),
                "bot_qty": _v42_float(owner.get("qty")),
                "qty_diff": qty_diff,
                "broker_avg": _v42_float(bpos.get("avg_cost")),
                "bot_avg": _v42_float(owner.get("avg_entry_price")),
                "avg_diff": avg_diff,
                "broker_value": _v42_float(bpos.get("market_value")),
                "broker_unrealized_pnl": _v42_float(bpos.get("unrealized_pnl")),
                "exchange": bpos.get("exchange"),
                "currency": bpos.get("currency"),
                "needs_sync": abs(qty_diff) > IBKR_RECON_QTY_TOLERANCE or abs(avg_diff) > 0.01,
            })

    for ticker, owners in sorted(bot.items()):
        if ticker not in broker:
            missing.append({"ticker": ticker, "bot_ledgers": owners, "reason": "Bot-managed position not found in broker snapshot."})

    external_value = sum(_v42_float(x.get("market_value")) for x in external)
    managed_broker_value = sum(_v42_float(x.get("broker_value")) for x in matched)
    cash_diff = broker_cash - bot_cash
    status = "OK"
    warnings = []
    if abs(cash_diff) > IBKR_RECON_CASH_TOLERANCE:
        warnings.append(f"Broker cash differs from bot cash by {format_money(cash_diff)}")
        status = "WARN"
    if missing:
        warnings.append(f"{len(missing)} bot-managed tickers missing in broker")
        status = "WARN"
    if ambiguous:
        warnings.append(f"{len(ambiguous)} ambiguous tickers across multiple bot ledgers")
        status = "WARN"

    return {
        "status": status,
        "snapshot_created_utc": snapshot.get("created_utc"),
        "account": (snapshot.get("connection") or {}).get("account_selected") or (snapshot.get("managed_accounts") or [None])[0],
        "broker_cash": round(broker_cash, 2),
        "bot_cash": round(bot_cash, 2),
        "cash_diff": round(cash_diff, 2),
        "broker_netliq": round(broker_netliq, 2),
        "broker_gross_positions": round(broker_gross, 2),
        "managed_broker_value": round(managed_broker_value, 2),
        "external_legacy_value": round(external_value, 2),
        "matched": matched,
        "external": external,
        "missing_in_broker": missing,
        "ambiguous": ambiguous,
        "warnings": warnings,
        "notes": [
            "External legacy positions are visible to the bot but excluded from Core/Growth/SPEC/Tactical/Crypto strategy ledgers.",
            "Broker net liquidation includes external legacy positions; bot strategy equity excludes them unless you explicitly manage them outside bot.",
        ],
    }


def _v42_fetch_store_reconcile() -> Tuple[bool, str, Optional[Dict[str, Any]], Optional[str]]:
    ok, info, snap = _v42_fetch_snapshot()
    if not ok or snap is None:
        return False, info, None, None
    rec = _v42_reconcile_snapshot(snap)
    sid = _v42_store_snapshot(snap, rec)
    with db_tx() as conn:
        conn.execute(
            "INSERT INTO broker_reconcile_events(id, snapshot_id, time, status, summary_json) VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, sid, now_ts(), rec.get("status", "UNKNOWN"), json_dumps(rec)),
        )
    return True, info, rec, sid


def _v42_format_money_signed(x: Any) -> str:
    val = _v42_float(x)
    return ("+" if val >= 0 else "") + format_money(val)


def format_brokerstatus() -> str:
    ok, info, rec, sid = _v42_fetch_store_reconcile()
    if not ok or rec is None:
        return "🏦 IBKR BROKER STATUS v4.2.1\n\n❌ " + info
    warnings = "\n".join("⚠️ " + w for w in rec.get("warnings", [])) or "✅ No major broker/bot warnings."
    return (
        "🏦 IBKR BROKER STATUS v4.2.1\n\n"
        f"Connection source: {info}\n"
        f"Snapshot ID: {sid}\n"
        f"Account: {rec.get('account')}\n"
        f"Status: {_v38_status_emoji(rec.get('status'))} {rec.get('status')}\n\n"
        f"Broker cash: {format_money(float(rec.get('broker_cash', 0) or 0))}\n"
        f"Bot cash: {format_money(float(rec.get('bot_cash', 0) or 0))}\n"
        f"Cash diff: {_v42_format_money_signed(rec.get('cash_diff'))}\n"
        f"Broker net liquidation: {format_money(float(rec.get('broker_netliq', 0) or 0))}\n"
        f"Broker gross positions: {format_money(float(rec.get('broker_gross_positions', 0) or 0))}\n"
        f"Bot-managed broker value: {format_money(float(rec.get('managed_broker_value', 0) or 0))}\n"
        f"External legacy value: {format_money(float(rec.get('external_legacy_value', 0) or 0))}\n\n"
        f"Managed matches: {len(rec.get('matched', []))}\n"
        f"External legacy positions: {len(rec.get('external', []))}\n"
        f"Missing in broker: {len(rec.get('missing_in_broker', []))}\n"
        f"Ambiguous: {len(rec.get('ambiguous', []))}\n\n"
        f"{warnings}\n\n"
        "Read-only reconciliation only. No orders placed."
    )[:MAX_TELEGRAM_MESSAGE]


def format_brokerpositions() -> str:
    ok, info, rec, sid = _v42_fetch_store_reconcile()
    if not ok or rec is None:
        return "📦 IBKR POSITIONS v4.2.1\n\n❌ " + info
    rows = rec.get("matched", [])
    if not rows:
        matched_text = "No bot-managed positions found in broker."
    else:
        parts = []
        for r in rows[:20]:
            flag = "⚠️" if r.get("needs_sync") else "✅"
            parts.append(
                f"{flag} {r.get('ticker')} [{r.get('ledger')}] qty {round(float(r.get('broker_qty',0)),6)} | "
                f"IBKR avg {round(float(r.get('broker_avg',0)),4)} vs bot {round(float(r.get('bot_avg',0)),4)} | "
                f"value {format_money(float(r.get('broker_value',0)))}"
            )
        matched_text = "\n".join(parts)
    return (
        "📦 IBKR BOT-MANAGED POSITIONS v4.2.1\n\n"
        f"Snapshot ID: {sid}\n"
        f"{matched_text}\n\n"
        "Use brokersyncpreview to see supervised sync proposals."
    )[:MAX_TELEGRAM_MESSAGE]


def format_brokerexternal() -> str:
    ok, info, rec, sid = _v42_fetch_store_reconcile()
    if not ok or rec is None:
        return "🧳 EXTERNAL LEGACY POSITIONS v4.2.1\n\n❌ " + info
    ext = sorted(rec.get("external", []), key=lambda x: abs(float(x.get("market_value", 0) or 0)), reverse=True)
    if not ext:
        rows = "No external legacy positions found."
    else:
        rows = "\n".join(
            f"• {x.get('ticker')} {round(float(x.get('qty',0)),6)} @ {round(float(x.get('market_price',0)),4)} | "
            f"value {format_money(float(x.get('market_value',0)))} | avg {round(float(x.get('avg_cost',0)),4)} | "
            f"P/L {format_money(float(x.get('unrealized_pnl',0)))} | {x.get('exchange')}"
            for x in ext[:25]
        )
    return (
        "🧳 EXTERNAL LEGACY POSITIONS v4.2.1\n\n"
        "These are broker holdings outside bot strategy ledgers. Bot sees them, but does not trade or count them as Core/Growth/SPEC/Tactical/Crypto.\n\n"
        f"Total external value: {format_money(float(rec.get('external_legacy_value', 0) or 0))}\n\n"
        f"{rows}"
    )[:MAX_TELEGRAM_MESSAGE]


def format_brokerreconcile() -> str:
    ok, info, rec, sid = _v42_fetch_store_reconcile()
    if not ok or rec is None:
        return "🧮 IBKR RECONCILIATION v4.2.1\n\n❌ " + info
    warnings = "\n".join("⚠️ " + w for w in rec.get("warnings", [])) or "✅ No major mismatches."
    sync_needed = [x for x in rec.get("matched", []) if x.get("needs_sync")]
    sync_rows = "\n".join(
        f"• {x.get('ticker')} [{x.get('ledger')}] qty diff {round(float(x.get('qty_diff',0)),6)}, avg diff {round(float(x.get('avg_diff',0)),4)}"
        for x in sync_needed[:15]
    ) or "No managed position avg/qty sync needed."
    missing_rows = "\n".join(f"• {x.get('ticker')}: {x.get('reason')}" for x in rec.get("missing_in_broker", [])[:10]) or "None."
    amb_rows = "\n".join(f"• {x.get('ticker')}: {x.get('reason')}" for x in rec.get("ambiguous", [])[:10]) or "None."
    return (
        "🧮 IBKR RECONCILIATION v4.2.1\n\n"
        f"Snapshot ID: {sid}\n"
        f"Source: {info}\n"
        f"Status: {_v38_status_emoji(rec.get('status'))} {rec.get('status')}\n\n"
        f"Cash: broker {format_money(float(rec.get('broker_cash',0)))} vs bot {format_money(float(rec.get('bot_cash',0)))} "
        f"({ _v42_format_money_signed(rec.get('cash_diff')) })\n"
        f"Broker net liquidation: {format_money(float(rec.get('broker_netliq',0)))}\n"
        f"External legacy value: {format_money(float(rec.get('external_legacy_value',0)))}\n\n"
        f"{warnings}\n\n"
        "Managed positions needing sync preview:\n"
        f"{sync_rows}\n\n"
        "Missing in broker:\n"
        f"{missing_rows}\n\n"
        "Ambiguous:\n"
        f"{amb_rows}\n\n"
        "No ledger changes were made."
    )[:MAX_TELEGRAM_MESSAGE]


def format_brokersyncpreview() -> str:
    ok, info, rec, sid = _v42_fetch_store_reconcile()
    if not ok or rec is None:
        return "🧾 BROKER SYNC PREVIEW v4.2.1\n\n❌ " + info
    candidates = [x for x in rec.get("matched", []) if x.get("needs_sync")]
    rows = []
    if abs(float(rec.get("cash_diff", 0) or 0)) > 0.01:
        rows.append(f"• Cash: bot {format_money(float(rec.get('bot_cash',0)))} -> broker {format_money(float(rec.get('broker_cash',0)))}")
    for x in candidates[:20]:
        rows.append(
            f"• {x.get('ticker')} [{x.get('ledger')}]: qty {round(float(x.get('bot_qty',0)),6)} -> {round(float(x.get('broker_qty',0)),6)}, "
            f"avg {round(float(x.get('bot_avg',0)),4)} -> {round(float(x.get('broker_avg',0)),4)}"
        )
    if not rows:
        rows_text = "No sync actions proposed."
    else:
        rows_text = "\n".join(rows)
    return (
        "🧾 BROKER SYNC PREVIEW v4.2.1\n\n"
        "This would align bot cash and bot-managed position quantities/average costs with IBKR.\n"
        "It will NOT adopt external legacy positions and will NOT rewrite historical trade records.\n\n"
        f"Snapshot ID: {sid}\n"
        f"{rows_text}\n\n"
        "To apply, send exactly:\n"
        "brokersyncapply CONFIRM\n\n"
        "Use only after reviewing brokerreconcile."
    )[:MAX_TELEGRAM_MESSAGE]


def _v42_update_ledger_position_from_broker(conn: sqlite3.Connection, ledger: str, ticker: str, qty: float, avg: float) -> None:
    cost = qty * avg
    now = now_ts()
    if ledger == "CORE":
        conn.execute("UPDATE core_positions SET shares=?, avg_entry_price=?, cost_basis=?, last_update_time=? WHERE ticker=?", (round(qty,8), round(avg,6), round(cost,6), now, ticker))
    elif ledger == "GROWTH":
        conn.execute("UPDATE growth_positions SET shares=?, avg_entry_price=?, cost_basis=?, last_update_time=? WHERE ticker=?", (round(qty,8), round(avg,6), round(cost,6), now, ticker))
    elif ledger == "SPEC":
        conn.execute("UPDATE spec_positions SET shares=?, avg_entry_price=?, cost_basis=?, last_update_time=? WHERE ticker=?", (round(qty,8), round(avg,6), round(cost,6), now, ticker))
    elif ledger == "CRYPTO":
        conn.execute("UPDATE crypto_positions SET units=?, avg_entry_price=?, cost_basis=?, last_update_time=? WHERE ticker=?", (round(qty,8), round(avg,6), round(cost,6), now, ticker))
    elif ledger == "TACTICAL":
        # Tactical positions are share/stop based; only sync quantity and entry price, leave stop/risk unchanged.
        conn.execute("UPDATE positions SET shares=?, entry_price=? WHERE ticker=?", (int(round(qty)), round(avg,6), ticker))


def broker_sync_apply_confirmed() -> Tuple[bool, str]:
    ok, info, rec, sid = _v42_fetch_store_reconcile()
    if not ok or rec is None:
        return False, info
    if rec.get("ambiguous"):
        return False, "Sync blocked: ambiguous tickers exist across multiple bot ledgers. Resolve manually first."
    if rec.get("missing_in_broker"):
        return False, "Sync blocked: some bot-managed positions are missing in broker. Resolve manually first."
    matched = rec.get("matched", []) or []
    actions = []
    with db_tx() as conn:
        if IBKR_SYNC_ALLOW_CASH:
            set_cash_tx(conn, float(rec.get("broker_cash", 0) or 0))
            actions.append(f"cash -> {format_money(float(rec.get('broker_cash',0)))}")
        for x in matched:
            if not x.get("needs_sync"):
                continue
            ticker = str(x.get("ticker", "")).upper()
            ledger = str(x.get("ledger", "")).upper()
            qty = float(x.get("broker_qty", 0) or 0)
            avg = float(x.get("broker_avg", 0) or 0)
            if qty <= 0 or avg <= 0:
                continue
            if not (IBKR_SYNC_ALLOW_AVG_COST or IBKR_SYNC_ALLOW_QTY):
                continue
            _v42_update_ledger_position_from_broker(conn, ledger, ticker, qty, avg)
            actions.append(f"{ticker}[{ledger}] qty/avg -> {round(qty,6)} @ {round(avg,4)}")
        conn.execute(
            "INSERT INTO broker_reconcile_events(id, snapshot_id, time, status, summary_json) VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, sid, now_ts(), "SYNC_APPLIED", json_dumps({"actions": actions, "source": info})),
        )
    refresh_portfolio()
    audit("IBKR_SYNC_APPLIED", "; ".join(actions))
    return True, (
        "✅ BROKER SYNC APPLIED v4.2.1\n\n"
        + ("\n".join(f"• {a}" for a in actions) if actions else "No changes needed.")
        + "\n\nExternal legacy positions were not adopted. Historical trade records were not rewritten."
    )


def maybe_send_ibkr_reconcile_after_close() -> None:
    if not (IBKR_RECON_ENABLED and IBKR_RECON_AUTO_ENABLED):
        return
    current_ny = ny_now()
    if not is_market_weekday(current_ny):
        return
    minutes = current_ny.hour * 60 + current_ny.minute
    if minutes < IBKR_RECON_AFTER_CLOSE_MINUTE:
        return
    today = current_ny.date().isoformat()
    if get_meta("last_ibkr_reconcile_day") == today:
        return
    set_meta("last_ibkr_reconcile_day", today)
    try:
        send(format_brokerreconcile())
    except Exception as exc:
        logger.exception(f"[IBKR AUTO RECON ERROR] {exc}")
        send(f"⚠️ IBKR auto reconcile failed: {exc}")


# Wrap monthly/after-close loop hook. This is called each main loop iteration.
_V42_OLD_MAYBE_SEND_MONTHLY = maybe_send_wealth_core_signal

def maybe_send_wealth_core_signal() -> None:
    _V42_OLD_MAYBE_SEND_MONTHLY()
    maybe_send_ibkr_reconcile_after_close()


_V42_OLD_EXPORT_STATE_BUNDLE = export_state_bundle

def export_state_bundle(prefix: str = "bot_state_export") -> str:
    zip_path = _V42_OLD_EXPORT_STATE_BUNDLE(prefix=prefix)
    try:
        conn = db_connect()
        try:
            snapshots = [dict(r) for r in conn.execute("SELECT id, source, account, created_utc, imported_at, broker_cash, broker_netliq, broker_gross_positions FROM broker_snapshots ORDER BY imported_at DESC LIMIT 20").fetchall()]
            positions = [dict(r) for r in conn.execute("SELECT * FROM broker_position_snapshots ORDER BY created_at DESC, ticker ASC LIMIT 500").fetchall()]
            events = [dict(r) for r in conn.execute("SELECT * FROM broker_reconcile_events ORDER BY time DESC LIMIT 50").fetchall()]
        finally:
            conn.close()
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("broker_snapshots_summary.table.json", json.dumps(safe_convert(snapshots), indent=2))
            z.writestr("broker_position_snapshots.table.json", json.dumps(safe_convert(positions), indent=2))
            z.writestr("broker_reconcile_events.table.json", json.dumps(safe_convert(events), indent=2))
    except Exception as exc:
        print(f"[V42 BROKER EXPORT WARNING] {exc}")
    return zip_path


# Extend help and commands.
_V42_OLD_HANDLE_COMMAND = handle_command

def handle_command(text: str, update_id: Optional[int] = None) -> None:
    text_clean = (text or "").strip()
    text_lower = text_clean.lower()
    if text_lower in {"brokerhelp", "ibkrhelp"}:
        send(
            "🏦 IBKR RECONCILIATION COMMANDS v4.2.1\n\n"
            "brokerstatus — fetch/store latest IBKR snapshot and show account summary\n"
            "brokerpositions — show bot-managed positions as seen by IBKR\n"
            "brokerexternal — show external legacy broker positions outside bot scope\n"
            "brokerreconcile — compare IBKR vs bot ledgers\n"
            "brokersyncpreview — preview cash/avg-cost sync for bot-managed positions\n"
            "brokersyncapply CONFIRM — supervised sync of bot cash + matching managed positions from IBKR\n\n"
            "No broker orders are placed in v4.2.1."
        )
        return
    if text_lower in {"brokerstatus", "ibkrstatus"}:
        send(format_brokerstatus()); return
    if text_lower in {"brokerpositions", "ibkrpositions"}:
        send(format_brokerpositions()); return
    if text_lower in {"brokerexternal", "ibkrexternal"}:
        send(format_brokerexternal()); return
    if text_lower in {"brokerreconcile", "ibkrreconcile"}:
        send(format_brokerreconcile()); return
    if text_lower in {"brokersyncpreview", "ibkrsyncpreview"}:
        send(format_brokersyncpreview()); return
    if text_lower == "brokersyncapply confirm":
        ok, msg = broker_sync_apply_confirmed()
        send(msg if ok else f"❌ BROKER SYNC REJECTED\n\n{msg}")
        return
    if text_lower.startswith("brokersyncapply"):
        send("⚠️ Dangerous sync command. To apply the preview, send exactly:\n\nbrokersyncapply CONFIRM")
        return
    return _V42_OLD_HANDLE_COMMAND(text_clean, update_id=update_id)



# -----------------------------------------------------------------------------
# V4.2.1 DISPLAY / OPERATIONAL STATUS WRAPPER
# -----------------------------------------------------------------------------
# This final wrapper does not change trading logic. It fixes the public/private
# command label so operators can confirm that the IBKR reconciliation layer is
# the currently deployed candidate, while keeping the older v4.1.1 hotfix
# information available.

_V421_OLD_HANDLE_COMMAND = handle_command

def handle_command(text: str, update_id: Optional[int] = None) -> None:
    text_clean = (text or "").strip()
    text_lower = text_clean.lower()
    if text_lower in {"v42status", "brokerhotfixstatus", "hotfixstatus"}:
        market_ok, reason = growth_alpha_market_filter_ok()
        send(
            "🛠️ V4.2.1 IBKR RECON / HOTFIX STATUS\n\n"
            f"Strategy display: {STRATEGY_VERSION}\n"
            f"V4.2 layer: {V42_VERSION}\n"
            f"Growth market filter: {yes_no(market_ok)} — {reason}\n"
            f"SPEC blocklist: {', '.join(sorted(SPEC_ALPHA_BLOCKLIST))}\n"
            f"SPEC universe size: {len(SPEC_ALPHA_UNIVERSE)}\n"
            f"IBKR recon enabled: {yes_no(IBKR_RECON_ENABLED)}\n"
            f"IBKR auto reconcile: {yes_no(IBKR_RECON_AUTO_ENABLED)}\n"
            f"Bridge URL configured: {yes_no(bool(IBKR_BRIDGE_URL))}\n"
            f"Bridge timeout/retries: {IBKR_BRIDGE_TIMEOUT}s / {IBKR_BRIDGE_RETRIES} retries\n"
            f"Core public enabled: {yes_no(CORE_PUBLIC_SIGNAL_ENABLED)}\n"
            f"SPEC public enabled: {yes_no(SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED)}\n\n"
            "Read-only reconciliation only. No broker orders are placed."
        )
        return
    if text_lower in {"brokerping", "bridgeping"}:
        ok, info, snap = _v42_fetch_snapshot()
        if ok and isinstance(snap, dict):
            conn = snap.get("connection") or {}
            send(
                "🏓 IBKR BRIDGE PING v4.2.1\n\n"
                f"Status: ✅ OK\n"
                f"Source: {info}\n"
                f"Account: {conn.get('account_selected') or (snap.get('managed_accounts') or ['n/a'])[0]}\n"
                f"Created UTC: {snap.get('created_utc', 'n/a')}\n"
                "No broker orders are placed."
            )
        else:
            send(f"🏓 IBKR BRIDGE PING v4.2.1\n\n❌ {info}")
        return
    return _V421_OLD_HANDLE_COMMAND(text_clean, update_id=update_id)


if __name__ == "__main__":



    main()
