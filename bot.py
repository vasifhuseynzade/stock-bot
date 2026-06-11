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

STRATEGY_VERSION = os.getenv("STRATEGY_VERSION", "v4.8.3-final-freeze-20-45-15-10-10-monitor")
INITIAL_CASH = float(os.getenv("INITIAL_CASH", "0"))

# Risk / execution controls.
MAX_TOTAL_RISK = float(os.getenv("MAX_TOTAL_RISK", "0.06"))
MAX_POSITION_EQUITY_PCT = float(os.getenv("MAX_POSITION_EQUITY_PCT", "0.20"))
CASH_USAGE_BUFFER = float(os.getenv("CASH_USAGE_BUFFER", "0.98"))
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.03"))
MAX_ENTRY_EXTENSION_PCT = float(os.getenv("MAX_ENTRY_EXTENSION_PCT", "0.01"))

# -----------------------------------------------------------------------------
# V2.8 VCP-ONLY LEADER ENGINE + WINNER-CAPTURE UPGRADE
# -----------------------------------------------------------------------------
# Backtest synthesis direction:
# - Pure weak/high-beta and broad pullbacks were rejected.
# -----------------------------------------------------------------------------
# V4.8 ACTIVE-ONLY COMPATIBILITY FLAGS
# -----------------------------------------------------------------------------
# Legacy VCP/Bear/Options strategies were removed from the live code path.
# Minimal constants remain only so older helper/report paths cannot raise NameError.
BEAR_SLEEVE_ENABLED = False
BEAR_WATCHLIST: List[str] = []

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
CORE_POSITION_EPSILON = float(os.getenv("CORE_POSITION_EPSILON", "0.000001"))
CORE_ALLOW_FRACTIONAL_SHARES = os.getenv("CORE_ALLOW_FRACTIONAL_SHARES", "1") != "0"
CORE_ALLOW_BUY_OUTSIDE_PLAN = os.getenv("CORE_ALLOW_BUY_OUTSIDE_PLAN", "0") != "0"

PORTFOLIO_RISK_GUARD_ENABLED = os.getenv("PORTFOLIO_RISK_GUARD_ENABLED", "1") != "0"
PORTFOLIO_SOFT_DD_REDUCE_PCT = float(os.getenv("PORTFOLIO_SOFT_DD_REDUCE_PCT", "0.12"))
PORTFOLIO_HARD_DD_PAUSE_PCT = float(os.getenv("PORTFOLIO_HARD_DD_PAUSE_PCT", "0.20"))
PORTFOLIO_DD_LOOKBACK_DAYS = int(os.getenv("PORTFOLIO_DD_LOOKBACK_DAYS", "400"))

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


MANAGE_ONLY_REGULAR_HOURS = os.getenv("MANAGE_ONLY_REGULAR_HOURS", "1") != "0"


# Earnings lookahead. Default kept at 7 days to preserve prior behavior,
# but you can raise it to 10 if you want stricter earnings avoidance.

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


def format_pct(value: Optional[float]) -> str:

    if value is None:

        return "n/a"

    sign = "+" if value >= 0 else ""

    return f"{sign}{round(value, 2)}%"


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

    Sets performance base capital after a clean reset + depositcash.

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


def market_label(market: str) -> str:

    labels = {

        "BULL": "🐂 BULL",

        "BEAR": "🐻 BEAR",

        "UNCERTAIN": "🟡 UNCERTAIN",

    }

    return labels.get(str(market).upper(), f"⚪ {market}")


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

def _old_init_db() -> None:

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


def clear_signals() -> None:

    global last_signals

    with db_tx() as conn:

        conn.execute("DELETE FROM signals")

    last_signals = {}


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

def _V47_OLD_OPEN_RISK_DETAILS() -> Dict[str, float]:
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

def load_cash_deposits() -> List[Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute(
            "SELECT * FROM cash_deposits ORDER BY time ASC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def cash_deposit_summary() -> Dict[str, Any]:
    deposits = load_cash_deposits()
    withdrawals = load_withdrawals()
    deposited = round(sum(float(x.get("amount") or 0.0) for x in deposits), 2)
    withdrawn = round(sum(float(x.get("amount") or 0.0) for x in withdrawals), 2)
    net_external = round(deposited - withdrawn, 2)
    return {
        "cash_deposited": deposited,
        "cash_withdrawn": withdrawn,
        "deposited_cash": deposited,
        "withdrawn_cash": withdrawn,
        "net_external_cash": net_external,
        "deposited_cash_total": deposited,
        "withdrawn_cash_total": withdrawn,
        "net_external_cash_flow": net_external,
        "deposit_count": len(deposits),
        "withdrawal_count": len(withdrawals),
    }


def _cash_deposit_total_tx(conn: sqlite3.Connection) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM cash_deposits"
    ).fetchone()
    return 0.0 if row is None else float(row["total"] or 0.0)


def _increase_performance_base_for_deposit_tx(
    conn: sqlite3.Connection,
    amount: float,
) -> None:
    if not math.isfinite(amount) or amount <= 0:
        return
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'performance_base_capital'"
    ).fetchone()
    existing = 0.0
    if row is not None:
        try:
            existing = float(row["value"])
        except (TypeError, ValueError):
            existing = 0.0
    new_base = max(0.0, existing) + amount
    conn.execute(
        "INSERT INTO meta(key, value) VALUES ('performance_base_capital', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(round(new_base, 2)),),
    )


def record_cash_deposit(
    amount: float,
    note: str = "",
    update_id: Optional[int] = None,
) -> Tuple[bool, str]:
    if not math.isfinite(amount) or amount <= 0:
        return False, "Deposit amount must be positive and finite."

    refresh_portfolio()
    snapshot_before = compute_equity_snapshot_data()
    equity_before = float(snapshot_before.get("equity", 0.0) or 0.0)

    with db_tx() as conn:
        cash_before = get_cash(conn)
        cash_after = cash_before + amount
        set_cash_tx(conn, cash_after)

        hwm_row = conn.execute(
            "SELECT value FROM meta WHERE key = 'withdrawal_high_water_mark'"
        ).fetchone()
        hwm_before: Optional[float] = None
        if hwm_row is not None:
            try:
                hwm_before = float(hwm_row["value"])
            except (TypeError, ValueError):
                hwm_before = None

        if hwm_before is None or not math.isfinite(hwm_before) or hwm_before <= 0:
            hwm_after = equity_before + amount
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('withdrawal_hwm_initialized_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(now_ts()),),
            )
        else:
            hwm_after = hwm_before + amount

        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('withdrawal_high_water_mark', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(round(hwm_after, 2)),),
        )
        _increase_performance_base_for_deposit_tx(conn, amount)

        equity_after = equity_before + amount
        conn.execute(
            """
            INSERT INTO cash_deposits(
                id, time, amount, cash_before, cash_after,
                equity_before, equity_after,
                withdrawal_hwm_before, withdrawal_hwm_after,
                note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                now_ts(),
                round(amount, 2),
                round(cash_before, 2),
                round(cash_after, 2),
                round(equity_before, 2),
                round(equity_after, 2),
                None if hwm_before is None else round(hwm_before, 2),
                round(hwm_after, 2),
                str(note or ""),
                now_ts(),
            ),
        )
        mark_update_processed_tx(conn, update_id, "processed_depositcash")

    refresh_portfolio()
    audit(
        "CASH_DEPOSIT",
        f"amount={amount} equity_before={equity_before} cash_after={cash_after} hwm_after={hwm_after}",
    )
    return True, (
        "CASH DEPOSIT RECORDED\n\n"
        f"Amount: {format_money(amount)}\n"
        f"Cash before: {format_money(cash_before)}\n"
        f"Cash after: {format_money(cash_after)}\n"
        f"Equity before: {format_money(equity_before)}\n"
        f"Equity after: {format_money(equity_after)}\n"
        f"Deposit-adjusted withdrawal HWM: {format_money(hwm_after)}\n\n"
        "Deposits increase principal and do not count as profit."
    )


def format_cash_deposit_report() -> str:
    summary = cash_deposit_summary()
    deposits = load_cash_deposits()
    msg = (
        "CASH DEPOSITS v4.9.7\n\n"
        f"Deposited cash: {format_money(summary['cash_deposited'])}\n"
        f"Withdrawn cash: {format_money(summary['cash_withdrawn'])}\n"
        f"Net external cash: {format_money(summary['net_external_cash'])}\n"
        f"Deposit records: {summary['deposit_count']}\n"
        f"Withdrawal records: {summary['withdrawal_count']}\n\n"
    )
    if not deposits:
        return msg + "No cash deposits recorded yet. Use: depositcash AMOUNT optional note"
    msg += "Recent deposits:\n"
    for item in deposits[-10:]:
        dt = datetime.fromtimestamp(float(item["time"]), NY_TZ).strftime("%Y-%m-%d")
        note = str(item.get("note") or "")
        msg += (
            f"{dt} | {format_money(float(item['amount']))} | "
            f"cash after {format_money(float(item['cash_after']))}"
        )
        if note:
            msg += f" | {note}"
        msg += "\n"
    return msg[:MAX_TELEGRAM_MESSAGE]


def cash_flow_lines() -> str:
    summary = cash_deposit_summary()
    return (
        f"Deposited cash: {format_money(summary['cash_deposited'])}\n"
        f"Withdrawn cash: {format_money(summary['cash_withdrawn'])}\n"
        f"Net external cash: {format_money(summary['net_external_cash'])}"
    )


def format_withdrawal_plan_report() -> str:
    plan = compute_withdrawal_plan()
    if not plan["initialized"]:
        return (
            "WITHDRAWAL PLAN v4.9.7\n\n"
            f"Equity: {format_money(plan['equity'])}\n"
            f"Cash: {format_money(plan['cash'])}\n"
            f"{cash_flow_lines()}\n\n"
            f"Status: {plan['reason']}"
        )[:MAX_TELEGRAM_MESSAGE]
    return (
        "WITHDRAWAL PLAN v4.9.7\n\n"
        f"Phase: {plan['phase']}\n"
        f"Equity: {format_money(plan['equity'])}\n"
        f"Cash: {format_money(plan['cash'])}\n"
        f"Deposit-adjusted HWM: {format_money(plan['high_water_mark'])}\n"
        f"Profit above HWM: {format_money(plan['profit_above_hwm'])}\n\n"
        f"Withdrawal rate: {round(plan['rate'] * 100, 2)}%\n"
        f"Gross suggested: {format_money(plan['gross_suggested'])}\n"
        f"Cash cap: {format_money(plan['cash_cap'])}\n"
        f"Suggested withdrawal: {format_money(plan['suggested'])}\n\n"
        f"{cash_flow_lines()}\n\n"
        f"Days since withdrawal/review start: {plan['days_since_clock']}\n"
        f"Eligible: {yes_no(plan['eligible'])}\n"
        f"Reason: {plan['reason']}\n\n"
        "Deposits are principal, not strategy profit."
    )[:MAX_TELEGRAM_MESSAGE]


def format_realized_pnl_report() -> str:
    perf = realized_performance_all_time()
    return (
        "REALIZED P/L - ALL TIME v4.9.7\n\n"
        f"Realized strategy P/L: {format_money(perf['profit'])} ({format_pct(perf['pct'])})\n"
        f"Performance base capital: {format_money(perf['base_capital'])}\n"
        f"Trade records: {perf['trade_records']}\n\n"
        f"Deposited cash: {format_money(perf.get('cash_deposited', 0))}\n"
        f"Withdrawn cash: {format_money(perf.get('cash_withdrawn', 0))}\n"
        f"Net external cash: {format_money(perf.get('net_external_cash', 0))}\n\n"
        "Note: deposits/withdrawals are external cash flow, not realized strategy P/L."
    )[:MAX_TELEGRAM_MESSAGE]


def format_summary_report() -> str:
    perf = realized_performance_all_time()
    wr = win_rate()
    best, worst = ticker_stats()
    duration = avg_trade_duration()
    e = expectancy_summary()
    return (
        "SUMMARY v4.9.7\n\n"
        f"Realized strategy P/L all-time: {format_money(perf['profit'])} ({format_pct(perf['pct'])})\n"
        f"Performance base capital: {format_money(perf['base_capital'])}\n"
        f"Win Rate: {wr}%\n"
        f"Avg Duration: {duration}\n"
        f"Avg R: {e['avg_r']}\n"
        f"Profit Factor: {e['profit_factor']}\n\n"
        f"Deposited cash: {format_money(perf.get('cash_deposited', 0))}\n"
        f"Withdrawn cash: {format_money(perf.get('cash_withdrawn', 0))}\n"
        f"Net external cash: {format_money(perf.get('net_external_cash', 0))}\n\n"
        f"Best: {best[0]} ({format_money(best[1])})\n"
        f"Worst: {worst[0]} ({format_money(worst[1])})"
    )[:MAX_TELEGRAM_MESSAGE]

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
    flows = cash_deposit_summary()
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
            **flows,
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
        reason = "No profit above deposit-adjusted high-water mark. Deposits are not profit."
    elif profit_above_hwm < WITHDRAWAL_MIN_PROFIT:
        eligible = False
        reason = (
            f"Profit above deposit-adjusted high-water mark is below minimum "
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
        **flows,
    }

def record_withdrawal(
    amount: float,
    note: str = "",
    update_id: Optional[int] = None,
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
            f"{format_money(WITHDRAWAL_MIN_CASH_AFTER)}.",
        )

    hwm_before = get_withdrawal_hwm()
    if hwm_before is None:
        return False, "Withdrawal high-water mark is not initialized."

    profit_above_hwm = equity_before - hwm_before
    if profit_above_hwm <= 0:
        return (
            False,
            "Withdrawal rejected: no profit above deposit-adjusted high-water mark. "
            "Deposited cash is principal, not profit.",
        )
    if amount > profit_above_hwm + 0.01:
        return (
            False,
            f"Withdrawal rejected: amount exceeds profit above deposit-adjusted HWM "
            f"({format_money(profit_above_hwm)}). Deposited cash cannot be withdrawn as profit.",
        )

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
        f"amount={amount} equity_before={equity_before} cash_before={cash_before} "
        f"cash_after={cash_after} hwm_before={hwm_before} hwm_after={hwm_after}",
    )
    return True, (
        "WITHDRAWAL RECORDED\n\n"
        f"Amount: {format_money(amount)}\n"
        f"Equity before: {format_money(equity_before)}\n"
        f"Cash before: {format_money(cash_before)}\n"
        f"Cash after: {format_money(cash_after)}\n"
        f"Deposit-adjusted HWM: {format_money(hwm_after)}"
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

def _V39_OLD_SLEEVE_FROM_TRADE(trade: Dict[str, Any]) -> str:
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

def _old_sleeve_performance_summary() -> Dict[str, Any]:
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

def _V43_OLD_COMPUTE_CORE_PLAN() -> Dict[str, Any]:
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


# -----------------------------------------------------------------------------

# ANALYTICS

# -----------------------------------------------------------------------------


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

def _V44_OLD_CURRENT_CORE_PLAN_FOR_VALIDATION() -> Dict[str, Any]:
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


def _V44_OLD_RECORD_CORE_SELL(
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


def send_json_export(data: Any, filename: str, caption: str = "") -> None:

    path = os.path.join(DATA_DIR, filename)

    write_json_file(path, data)

    send_document(path, caption=caption or filename)

def _V37_OLD_RESET_ALL_PAPER_STATE(update_id: Optional[int] = None) -> Tuple[bool, str, Optional[str]]:

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

        "1) depositcash YOUR_REAL_CASH initial live deposit\n"

        "2) withdrawinit\n"

        "3) scanstatus\n"

        "4) portfolio\n"

        "5) openrisk"

    ), backup_path

# -----------------------------------------------------------------------------

# COMMANDS

# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------

# STRATEGY / SIGNAL ANALYSIS

# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# POSITION MANAGEMENT

# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------

# SCANNING

# -----------------------------------------------------------------------------


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


def _V310_OLD_INIT_DB() -> None:
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

def _V411_HOTFIX_OLD_SPEC_SCORE_TICKER(ticker: str) -> Optional[Dict[str, Any]]:
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

def _V411_HOTFIX_OLD_COMPUTE_SPEC_PLAN() -> Dict[str, Any]:
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

def _V411_HOTFIX_OLD_RECORD_SPEC_BUY(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
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

def _V44_OLD_RECORD_SPEC_SELL(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
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


def _V310_OLD_COMPUTE_EQUITY() -> Dict[str, float]:
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

def _V310_OLD_REALIZED() -> Dict[str, Any]:
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

def _V38_OLD_EXPORT_STATE_BUNDLE(prefix: str = "bot_state_export") -> str:
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


def _V310_OLD_RESET_ALL(update_id: Optional[int] = None) -> Tuple[bool, str, Optional[str]]:
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


def _V443_OLD_INSTITUTIONAL_SNAPSHOT() -> Dict[str, Any]:
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

def _V443_OLD_FORMAT_INSTITUTIONAL_STATUS() -> str:
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

def _V443_OLD_FORMAT_DATAHEALTH_STATUS() -> str:
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


def _V443_OLD_FORMAT_EXECUTION_STATUS() -> str:
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

def _V443_OLD_FORMAT_DRIFT_STATUS() -> str:
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


def download_institutional_report() -> str:
    ts = ny_now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(DATA_DIR, f"institutional_snapshot_{ts}.json")
    write_json_file(path, institutional_snapshot())
    return path


def _V310_OLD_EXPORT_STATE_BUNDLE(prefix: str = "bot_state_export") -> str:
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

# v3.9 bear-stock defaults. The sleeve is top-1, cash-first, and USD only.

# Match research behavior: exit on bear-score cooldown; no partial/trailing/time churn by default.


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


def _V41_OLD_INIT_DB() -> None:
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

def _V43_OLD_COMPUTE_GROWTH_PLAN() -> Dict[str, Any]:
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

def _V43_OLD_RECORD_GROWTH_BUY(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
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

def _V44_OLD_RECORD_GROWTH_SELL(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
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


def _V41_OLD_COMPUTE_EQUITY() -> Dict[str, float]:
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


def _V41_OLD_REALIZED() -> Dict[str, Any]:
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


def _V41_OLD_EXPORT_STATE_BUNDLE(prefix: str = "bot_state_export") -> str:
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


def _V41_OLD_RESET_ALL(update_id: Optional[int] = None) -> Tuple[bool, str, Optional[str]]:
    ok, msg, backup_path = _V310_OLD_RESET_ALL(update_id=update_id)
    with db_tx() as conn:
        conn.execute("DELETE FROM growth_positions")
        conn.execute("DELETE FROM growth_trades")
        conn.execute("DELETE FROM growth_signals")
        conn.execute("DELETE FROM meta WHERE key IN ('last_growth_alpha_month', 'last_growth_alpha_alert_ts')")
    return ok, msg + "\n✅ Growth Alpha positions/trades/signals cleared", backup_path


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

CRYPTO_ALPHA_ENABLED = os.getenv("CRYPTO_ALPHA_ENABLED", "1") != "0"
CRYPTO_ALPHA_LEDGER_ENABLED = os.getenv("CRYPTO_ALPHA_LEDGER_ENABLED", "1") != "0"
CRYPTO_ALPHA_ACCOUNT_ALLOC_PCT = float(os.getenv("CRYPTO_ALPHA_ACCOUNT_ALLOC_PCT", "0.10"))
CRYPTO_ALPHA_MAX_OPEN_POSITIONS = int(os.getenv("CRYPTO_ALPHA_MAX_OPEN_POSITIONS", "1"))
CRYPTO_ALPHA_MIN_TRADE_DOLLARS = float(os.getenv("CRYPTO_ALPHA_MIN_TRADE_DOLLARS", "25"))
CRYPTO_ALPHA_QUOTE_DEVIATION_LIMIT = float(os.getenv("CRYPTO_ALPHA_QUOTE_DEVIATION_LIMIT", "0.08"))
CRYPTO_ALPHA_REQUIRE_LIVE_QUOTE = os.getenv("CRYPTO_ALPHA_REQUIRE_LIVE_QUOTE", "1") != "0"
CRYPTO_ALPHA_REQUIRE_ACTIVE_PLAN_FOR_BUY = os.getenv("CRYPTO_ALPHA_REQUIRE_ACTIVE_PLAN_FOR_BUY", "1") != "0"

CRYPTO_ALPHA_INDICATORS = ["BTCUSD", "ETHUSD", "SOLUSD"]
CRYPTO_ALPHA_UNIVERSE = [
    "AVAXUSD", "LINKUSD", "ADAUSD", "XRPUSD", "DOGEUSD", "LTCUSD"
]
if os.getenv("CRYPTO_ALPHA_INCLUDE_SUI", "0") != "0":
    CRYPTO_ALPHA_UNIVERSE.append("SUIUSD")
CRYPTO_ALPHA_UNIVERSE = list(dict.fromkeys(CRYPTO_ALPHA_UNIVERSE))
CRYPTO_ALPHA_ALL_SYMBOLS = list(dict.fromkeys(CRYPTO_ALPHA_INDICATORS + CRYPTO_ALPHA_UNIVERSE))

CRYPTO_ALPHA_BREAKOUT_DAYS = int(os.getenv("CRYPTO_ALPHA_BREAKOUT_DAYS", "20"))
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


def _V42_OLD_INIT_DB() -> None:
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


def _V46_OLD_COMPUTE_EQUITY() -> Dict[str, float]:
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


def _V46_OLD_REALIZED_PERF() -> Dict[str, Any]:
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


def _V42_OLD_EXPORT_STATE_BUNDLE(prefix: str = "bot_state_export") -> str:
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


def _V46_OLD_RESET_ALL(update_id: Optional[int] = None) -> Tuple[bool, str, Optional[str]]:
    ok, msg, backup_path = _V41_OLD_RESET_ALL(update_id=update_id)
    with db_tx() as conn:
        conn.execute("DELETE FROM crypto_positions")
        conn.execute("DELETE FROM crypto_trades")
        conn.execute("DELETE FROM crypto_signals")
    return ok, msg + "\n✅ Crypto Alpha positions/trades/signals cleared", backup_path


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


def spec_alpha_score_ticker(ticker: str) -> Optional[Dict[str, Any]]:
    nticker = normalize_ticker(str(ticker)) or ""
    if nticker in SPEC_ALPHA_EXCLUDED_TICKERS:
        return None
    return _V411_HOTFIX_OLD_SPEC_SCORE_TICKER(nticker)


def _V411_OLD_RECORD_SPEC_BUY(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
    nticker = normalize_ticker(str(ticker)) or ""
    if nticker in SPEC_ALPHA_EXCLUDED_TICKERS:
        return False, (
            f"SPEC_ALPHA buy rejected: {nticker} is blocked by v4.1.1 hotfix corporate-action cleanup. "
            "Do not substitute another ticker manually; rerun specplan."
        )
    return _V411_HOTFIX_OLD_RECORD_SPEC_BUY(nticker, shares, price, update_id=update_id)


def _V43_OLD_COMPUTE_SPEC_PLAN() -> Dict[str, Any]:
    plan = _V411_HOTFIX_OLD_COMPUTE_SPEC_PLAN()
    plan["excluded_tickers"] = sorted(SPEC_ALPHA_EXCLUDED_TICKERS)
    plan["universe_size"] = len(SPEC_ALPHA_UNIVERSE)
    # Defensive scrub in case a cached/scored object somehow leaks through.
    for key in ["top", "actions", "actionable", "all_scored"]:
        rows = plan.get(key)
        if isinstance(rows, list):
            plan[key] = [r for r in rows if str(r.get("ticker", "")).upper() not in SPEC_ALPHA_EXCLUDED_TICKERS]
    return plan


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
def _V44_OLD_CURRENT_SPEC_PLAN_FOR_VALIDATION() -> Dict[str, Any]:
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


def _V43_OLD_RECORD_SPEC_BUY(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
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


# Add a small hotfix status command without replacing existing command handling.


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

V42_VERSION = "v4.3-ibkr-readonly-reconcile-hotfix-20260528"

# Display label fix: if the environment is empty or still has the old v4.1.1
# hotfix label, show this as the v4.3 reconciliation candidate.
# If the operator intentionally sets a different STRATEGY_VERSION, keep it.
_STRATEGY_ENV_RAW = os.getenv("STRATEGY_VERSION", "").strip()
if _STRATEGY_ENV_RAW in {"", "v4.1.1-hotfix-growth-spec-clean-20-45-20-5-10-monitor"}:
    STRATEGY_VERSION = "v4.3-ibkr-reconcile-v4.1.1-hotfix-20-45-20-5-10-monitor"
IBKR_RECON_ENABLED = os.getenv("IBKR_RECON_ENABLED", "1") != "0"
IBKR_RECON_AUTO_ENABLED = os.getenv("IBKR_RECON_AUTO_ENABLED", "0") == "1"
IBKR_RECON_AFTER_CLOSE_MINUTE = int(os.getenv("IBKR_RECON_AFTER_CLOSE_MINUTE", str(16 * 60 + 10)))
IBKR_BRIDGE_URL = os.getenv("IBKR_BRIDGE_URL", "").strip().rstrip("/")
IBKR_BRIDGE_TOKEN = os.getenv("IBKR_BRIDGE_TOKEN", "").strip()
IBKR_SNAPSHOT_FILE = os.getenv("IBKR_SNAPSHOT_FILE", "").strip()
IBKR_RECON_CASH_TOLERANCE = float(os.getenv("IBKR_RECON_CASH_TOLERANCE", "5.0"))
IBKR_RECON_QTY_TOLERANCE = float(os.getenv("IBKR_RECON_QTY_TOLERANCE", "0.0005"))
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

def _V46_OLD_INIT_DB() -> None:
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

def _V443_OLD_FORMAT_BROKERSTATUS() -> str:
    ok, info, rec, sid = _v42_fetch_store_reconcile()
    if not ok or rec is None:
        return "🏦 IBKR BROKER STATUS v4.3\n\n❌ " + info
    warnings = "\n".join("⚠️ " + w for w in rec.get("warnings", [])) or "✅ No major broker/bot warnings."
    return (
        "🏦 IBKR BROKER STATUS v4.3\n\n"
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

def _V443_OLD_FORMAT_BROKERPOSITIONS() -> str:
    ok, info, rec, sid = _v42_fetch_store_reconcile()
    if not ok or rec is None:
        return "📦 IBKR POSITIONS v4.3\n\n❌ " + info
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
        "📦 IBKR BOT-MANAGED POSITIONS v4.3\n\n"
        f"Snapshot ID: {sid}\n"
        f"{matched_text}\n\n"
        "Use brokersyncpreview to see supervised sync proposals."
    )[:MAX_TELEGRAM_MESSAGE]

def _V443_OLD_FORMAT_BROKEREXTERNAL() -> str:
    ok, info, rec, sid = _v42_fetch_store_reconcile()
    if not ok or rec is None:
        return "🧳 EXTERNAL LEGACY POSITIONS v4.3\n\n❌ " + info
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
        "🧳 EXTERNAL LEGACY POSITIONS v4.3\n\n"
        "These are broker holdings outside bot strategy ledgers. Bot sees them, but does not trade or count them as Core/Growth/SPEC/Tactical/Crypto.\n\n"
        f"Total external value: {format_money(float(rec.get('external_legacy_value', 0) or 0))}\n\n"
        f"{rows}"
    )[:MAX_TELEGRAM_MESSAGE]

def _V443_OLD_FORMAT_BROKERRECONCILE() -> str:
    ok, info, rec, sid = _v42_fetch_store_reconcile()
    if not ok or rec is None:
        return "🧮 IBKR RECONCILIATION v4.3\n\n❌ " + info
    warnings = "\n".join("⚠️ " + w for w in rec.get("warnings", [])) or "✅ No major mismatches."
    sync_needed = [x for x in rec.get("matched", []) if x.get("needs_sync")]
    sync_rows = "\n".join(
        f"• {x.get('ticker')} [{x.get('ledger')}] qty diff {round(float(x.get('qty_diff',0)),6)}, avg diff {round(float(x.get('avg_diff',0)),4)}"
        for x in sync_needed[:15]
    ) or "No managed position avg/qty sync needed."
    missing_rows = "\n".join(f"• {x.get('ticker')}: {x.get('reason')}" for x in rec.get("missing_in_broker", [])[:10]) or "None."
    amb_rows = "\n".join(f"• {x.get('ticker')}: {x.get('reason')}" for x in rec.get("ambiguous", [])[:10]) or "None."
    return (
        "🧮 IBKR RECONCILIATION v4.3\n\n"
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

def _V443_OLD_FORMAT_BROKERSYNCPREVIEW() -> str:
    ok, info, rec, sid = _v42_fetch_store_reconcile()
    if not ok or rec is None:
        return "🧾 BROKER SYNC PREVIEW v4.3\n\n❌ " + info
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
        "🧾 BROKER SYNC PREVIEW v4.3\n\n"
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

def _V443_OLD_BROKER_SYNC_APPLY_CONFIRMED() -> Tuple[bool, str]:
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
        "✅ BROKER SYNC APPLIED v4.3\n\n"
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


def _V46_OLD_EXPORT_STATE_BUNDLE(prefix: str = "bot_state_export") -> str:
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


# -----------------------------------------------------------------------------
# V4.3 DISPLAY / OPERATIONAL STATUS WRAPPER
# -----------------------------------------------------------------------------
# This final wrapper does not change trading logic. It fixes the public/private
# command label so operators can confirm that the IBKR reconciliation layer is
# the currently deployed candidate, while keeping the older v4.1.1 hotfix
# information available.


# -----------------------------------------------------------------------------
# V4.3 CORE COMMISSION / IBKR SYMBOL-MAPPING HOTFIX
# -----------------------------------------------------------------------------
# Operational-only patch. It does not change strategy selection. It fixes two
# live-account issues:
# 1) Small LSE/UCITS orders may show IBKR average cost including fixed fees;
#    validate the actual fill price against quote and record fee separately.
# 2) IBKR portfolio symbols for LSE UCITS often arrive without the .L suffix;
#    map known core UCITS symbols back to bot tickers for reconciliation.

V42_VERSION = "v4.3-ibkr-recon-core-fee"
STRATEGY_VERSION = os.getenv(
    "STRATEGY_VERSION",
    "v4.3-ibkr-recon-core-fee-20-45-20-5-10-monitor",
)

IBKR_CORE_SYMBOL_ALIASES = {
    "SMH": "SMH.L",
    "IUIT": "IUIT.L",
    "CNDX": "CNDX.L",
    "CMOD": "CMOD.L",
    "COPA": "COPA.L",
    "VUAA": "VUAA.L",
    "VUSD": "VUSD.L",
    "CSUS": "CSUS.L",
    "IUHC": "IUHC.L",
    "IUFS": "IUFS.L",
    "IUES": "IUES.L",
    "EGLN": "EGLN.L",
    "PHAG": "PHAG.L",
    "IB01": "IB01.L",
    "IBTA": "IBTA.L",
    "IDBT": "IDBT.L",
    "DTLA": "DTLA.L",
}

IBKR_UCITS_EXCHANGES = {
    "LSE", "LSEETF", "EUIBSI", "LSEETF1", "LSEETF2", "CHIXUK",
    "BATEUK", "TRQXUK", "AQUIS", "XLON", "LONDON",
}

def _v422_canonical_broker_symbol(contract: Dict[str, Any]) -> str:
    raw = contract.get("symbol") or contract.get("localSymbol")
    sym = _v42_normalize_broker_symbol(raw)
    exch = str(contract.get("primaryExchange") or contract.get("exchange") or "").upper()
    local = _v42_normalize_broker_symbol(contract.get("localSymbol") or sym)
    # Map LSE/UCITS broker symbols without .L back to bot symbols only when the
    # exchange looks like a London/UK venue. This avoids mapping a US SMH ETF to SMH.L.
    for candidate in [sym, local]:
        if candidate in IBKR_CORE_SYMBOL_ALIASES and (exch in IBKR_UCITS_EXCHANGES or candidate not in WATCHLIST):
            return IBKR_CORE_SYMBOL_ALIASES[candidate]
    return sym

def _v42_broker_positions(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:  # type: ignore[override]
    positions: Dict[str, Dict[str, Any]] = {}
    for p in snapshot.get("portfolio", []) or []:
        try:
            contract = p.get("contract") or {}
            symbol = _v422_canonical_broker_symbol(contract)
            if not symbol:
                continue
            qty = _v42_float(p.get("position"))
            if abs(qty) <= 1e-12:
                continue
            raw_symbol = _v42_normalize_broker_symbol(contract.get("symbol") or contract.get("localSymbol"))
            positions[symbol] = {
                "ticker": symbol,
                "broker_symbol": raw_symbol,
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
                "local_symbol": str(contract.get("localSymbol") or raw_symbol),
            }
        except Exception:
            continue
    return positions

def _V43_OLD_RECORD_CORE_BUY(  # type: ignore[override]
    ticker: str,
    shares: float,
    price: float,
    update_id: Optional[int] = None,
    fee: float = 0.0,
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
    if fee is None:
        fee = 0.0
    try:
        fee = float(fee)
    except Exception:
        return False, "Core fee/commission must be numeric."
    if not math.isfinite(fee) or fee < 0:
        return False, "Core fee/commission must be finite and non-negative."
    if ticker not in WEALTH_CORE_UNIVERSE:
        return False, f"{ticker} is not in the core wealth universe."

    gross_amount = shares * price
    amount = gross_amount + fee
    effective_avg = amount / shares if shares > 0 else price
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

    # Validate the actual execution price, not the commission-adjusted IBKR average cost.
    ok, msg, quote = validate_core_price_against_quote(ticker, price)
    if not ok:
        return False, msg + "\n\nIf this is an IBKR average cost including commission, use:\ncorebuy TICKER SHARES at FILL_PRICE fee COMMISSION"

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
            avg_price = effective_avg
            entry_time = now
            highest = price
            conn.execute(
                """
                INSERT INTO core_positions(
                    ticker, core_position_id, strategy_version, shares,
                    avg_entry_price, cost_basis, entry_time, last_update_time,
                    highest, sleeve, target_account_pct, last_plan_id, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'CORE_WEALTH', ?, ?, ?)
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
                    f"fee={round(fee, 6)}; fill_price={round(price, 6)}",
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
            old_notes = str(pos.get("notes") or "")
            new_note = (old_notes + "; " if old_notes else "") + f"fee={round(fee, 6)}; fill_price={round(price, 6)}"
            conn.execute(
                """
                UPDATE core_positions
                SET shares = ?, avg_entry_price = ?, cost_basis = ?,
                    last_update_time = ?, highest = ?, target_account_pct = ?,
                    last_plan_id = ?, strategy_version = ?, notes = ?
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
                    new_note[:500],
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
                round(price, 6),  # actual fill price excluding commission
                round(amount, 6), # total cost including commission
                now,
                WEALTH_STRATEGY_VERSION,
                plan_id,
                "core_plan_buy" if fee <= 0 else f"core_plan_buy_fee_{round(fee, 4)}",
                now,
            ),
        )
        set_cash_tx(conn, cash - amount)
        mark_update_processed_tx(conn, update_id, "processed_core_buy")

    refresh_portfolio()
    audit("CORE_BUY", f"{ticker} shares={shares} fill_price={price} fee={fee} total={amount}")
    fee_line = f"\n🧾 Fee/commission: {format_money(fee)}\n📌 Effective avg cost: {round(effective_avg, 4)}" if fee > 0 else ""
    return True, (
        f"🏛️ CORE BUY RECORDED {ticker}\n\n"
        f"📦 Shares: {format_core_shares(shares)}\n"
        f"💵 Fill price: {round(price, 4)}"
        f"{fee_line}\n"
        f"💰 Total cost: {format_money(amount)}\n"
        f"🎯 Plan action: {None if action is None else action.get('action')}\n"
        f"🏦 Target account weight: {None if target is None else target.get('target_account_pct')}%\n"
        f"💵 Cash left: {format_money(portfolio['cash'])}"
    )


# -----------------------------------------------------------------------------
# V4.3 COST-AWARE SMALL-ACCOUNT EXECUTION OVERLAY
# -----------------------------------------------------------------------------
# Operational/execution policy only. No new alpha logic.
# Purpose:
# - Avoid fee drag from tiny LSE/UCITS Core orders.
# - Keep monthly rotation rankings intact while limiting live execution to the
#   best few candidates when account size is still small.
# - Avoid duplicate tickers across Growth and SPEC, because IBKR reports one
#   aggregate broker position while the bot tracks separate strategy ledgers.

V43_VERSION = "v4.3-cost-aware-execution-20-45-20-5-10-monitor"
if os.getenv("ALLOW_STRATEGY_VERSION_OVERRIDE", "0").strip() == "1":
    STRATEGY_VERSION = os.getenv("STRATEGY_VERSION", V43_VERSION)
else:
    STRATEGY_VERSION = V43_VERSION

V43_COST_AWARE_ENABLED = os.getenv("V43_COST_AWARE_ENABLED", "1").strip() != "0"
V43_SMALL_ACCOUNT_EQUITY = float(os.getenv("V43_SMALL_ACCOUNT_EQUITY", "10000"))

# LSE/UCITS fixed commissions make tiny Core tickets inefficient.
V43_CORE_MIN_ORDER_DOLLARS = float(os.getenv("V43_CORE_MIN_ORDER_DOLLARS", "400"))
V43_CORE_MAX_NEW_BUYS_SMALL = int(os.getenv("V43_CORE_MAX_NEW_BUYS_SMALL", "2"))
V43_CORE_MAX_NEW_BUYS_PER_CLUSTER_SMALL = int(os.getenv("V43_CORE_MAX_NEW_BUYS_PER_CLUSTER_SMALL", "1"))
V43_CORE_BLOCK_TINY_SELLS = os.getenv("V43_CORE_BLOCK_TINY_SELLS", "1").strip() != "0"

# Growth remains main engine, but small accounts should enter top few leaders first.
V43_GROWTH_EXECUTE_TOP_N_SMALL = int(os.getenv("V43_GROWTH_EXECUTE_TOP_N_SMALL", "3"))
V43_GROWTH_MIN_ORDER_DOLLARS = float(os.getenv("V43_GROWTH_MIN_ORDER_DOLLARS", "250"))
V43_GROWTH_AVOID_SPEC_OVERLAP = os.getenv("V43_GROWTH_AVOID_SPEC_OVERLAP", "1").strip() != "0"
V43_GROWTH_MAX_NEW_BUYS_PER_CLUSTER_SMALL = int(os.getenv("V43_GROWTH_MAX_NEW_BUYS_PER_CLUSTER_SMALL", "1"))

# SPEC top 10 remains a hold list. Buys/adds should focus on best-ranked names only.
V43_SPEC_MAX_HOLDINGS_SMALL = int(os.getenv("V43_SPEC_MAX_HOLDINGS_SMALL", "6"))
V43_SPEC_BUY_RANK_LIMIT_SMALL = int(os.getenv("V43_SPEC_BUY_RANK_LIMIT_SMALL", "5"))
V43_SPEC_MIN_ORDER_DOLLARS = float(os.getenv("V43_SPEC_MIN_ORDER_DOLLARS", "75"))
V43_SPEC_AVOID_GROWTH_OVERLAP = os.getenv("V43_SPEC_AVOID_GROWTH_OVERLAP", "1").strip() != "0"
V43_SPEC_MAX_NEW_BUYS_PER_SECTOR_SMALL = int(os.getenv("V43_SPEC_MAX_NEW_BUYS_PER_SECTOR_SMALL", "1"))

def _v43_equity() -> float:
    try:
        return float(compute_equity_snapshot_data().get("equity", 0.0) or 0.0)
    except Exception:
        return 0.0

def _v43_small_account_mode() -> bool:
    if not V43_COST_AWARE_ENABLED:
        return False
    eq = _v43_equity()
    return eq > 0 and eq < V43_SMALL_ACCOUNT_EQUITY

def _v43_mark_skip(item: Dict[str, Any], reason: str, new_action: str = "SKIP") -> None:
    item["v43_original_action"] = item.get("action")
    item["action"] = new_action
    item["v43_skip_reason"] = reason

def _v43_refresh_actionable(plan: Dict[str, Any]) -> Dict[str, Any]:
    plan["actionable"] = [
        a for a in plan.get("actions", []) or []
        if str(a.get("action", "")).upper() in {"BUY", "ADD", "TRIM", "SELL"}
    ]
    return plan

def _v43_position_tickers(loader_name: str) -> set:
    try:
        loader = globals().get(loader_name)
        if loader is None:
            return set()
        positions = loader()
        if isinstance(positions, dict):
            return {str(x).upper() for x in positions.keys()}
    except Exception:
        pass
    return set()

def _v43_has_position(loader_name: str, ticker: str) -> bool:
    return str(ticker).upper() in _v43_position_tickers(loader_name)

# ---- Core cost-aware plan/action overlay ----

def _V44_OLD_COMPUTE_CORE_PLAN() -> Dict[str, Any]:  # type: ignore[override]
    plan = _V43_OLD_COMPUTE_CORE_PLAN()
    if not V43_COST_AWARE_ENABLED:
        return plan

    small = _v43_small_account_mode()
    plan["v43_cost_aware"] = {
        "enabled": True,
        "small_account_mode": small,
        "small_account_equity_threshold": V43_SMALL_ACCOUNT_EQUITY,
        "core_min_order_dollars": V43_CORE_MIN_ORDER_DOLLARS,
        "core_max_new_buys_small": V43_CORE_MAX_NEW_BUYS_SMALL,
        "core_max_new_buys_per_cluster_small": V43_CORE_MAX_NEW_BUYS_PER_CLUSTER_SMALL,
        "policy": "Rank top core assets, but execute only the best 1-2 sizeable Core orders in small accounts. Tiny Core orders stay as cash.",
    }
    if not small:
        return plan

    allowed_new = 0
    new_cluster_counts: Dict[str, int] = {}
    for item in plan.get("actions", []) or []:
        action = str(item.get("action", "")).upper()
        current_value = float(item.get("current_value", 0.0) or 0.0)
        suggested = float(item.get("suggested_dollars", 0.0) or 0.0)
        cluster = str(item.get("cluster") or "other")

        if action in {"BUY", "ADD"}:
            if suggested < V43_CORE_MIN_ORDER_DOLLARS:
                _v43_mark_skip(item, f"Core action below cost-aware minimum ${V43_CORE_MIN_ORDER_DOLLARS:.0f}; leave cash unallocated.", "HOLD" if current_value > 0 else "SKIP")
                continue
            if current_value <= 0:
                if allowed_new >= V43_CORE_MAX_NEW_BUYS_SMALL:
                    _v43_mark_skip(item, f"Small-account Core max new buys reached ({V43_CORE_MAX_NEW_BUYS_SMALL}).", "SKIP")
                    continue
                if new_cluster_counts.get(cluster, 0) >= V43_CORE_MAX_NEW_BUYS_PER_CLUSTER_SMALL:
                    _v43_mark_skip(item, f"Small-account Core cluster cap reached for {cluster}.", "SKIP")
                    continue
                allowed_new += 1
                new_cluster_counts[cluster] = new_cluster_counts.get(cluster, 0) + 1
        elif action in {"TRIM", "SELL"} and V43_CORE_BLOCK_TINY_SELLS and suggested < V43_CORE_MIN_ORDER_DOLLARS:
            _v43_mark_skip(item, f"Core {action.lower()} below cost-aware minimum; avoid paying fixed commission for tiny rebalance.", "HOLD")

    return _v43_refresh_actionable(plan)


def _V442_OLD_RECORD_CORE_BUY(ticker: str, shares: float, price: float, update_id: Optional[int] = None, fee: float = 0.0) -> Tuple[bool, str]:  # type: ignore[override]
    if V43_COST_AWARE_ENABLED:
        ticker_norm = normalize_ticker(str(ticker)) or ""
        try:
            total = float(shares) * float(price) + float(fee or 0.0)
        except Exception:
            total = 0.0
        if total < V43_CORE_MIN_ORDER_DOLLARS:
            return False, (
                f"Core buy rejected by v4.3 cost-aware execution: total order {format_money(total)} is below "
                f"minimum {format_money(V43_CORE_MIN_ORDER_DOLLARS)}. Leave this Core allocation as cash until order size is worthwhile."
            )
        try:
            plan = current_core_plan_for_validation()
            action = latest_core_plan_action_map(plan).get(ticker_norm, {})
            if str(action.get("action", "")).upper() == "SKIP":
                return False, f"Core buy rejected by v4.3 plan policy: {action.get('v43_skip_reason', 'not executable in cost-aware mode')}"
        except Exception:
            pass
    return _V43_OLD_RECORD_CORE_BUY(ticker, shares, price, update_id=update_id, fee=fee)

# ---- Growth cost-aware plan/action overlay ----

def _V44_OLD_COMPUTE_GROWTH_PLAN() -> Dict[str, Any]:  # type: ignore[override]
    plan = _V43_OLD_COMPUTE_GROWTH_PLAN()
    if not V43_COST_AWARE_ENABLED:
        return plan

    small = _v43_small_account_mode()
    spec_tickers = _v43_position_tickers("load_spec_positions")
    allowed_new_clusters: Dict[str, int] = {}
    plan["v43_cost_aware"] = {
        "enabled": True,
        "small_account_mode": small,
        "growth_execute_top_n_small": V43_GROWTH_EXECUTE_TOP_N_SMALL,
        "growth_min_order_dollars": V43_GROWTH_MIN_ORDER_DOLLARS,
        "avoid_spec_overlap": V43_GROWTH_AVOID_SPEC_OVERLAP,
        "policy": "Growth ranks top 5, but small-account execution prioritizes top 3, avoids SPEC overlap, and skips tiny orders.",
    }
    if not small:
        return plan

    for item in plan.get("actions", []) or []:
        action = str(item.get("action", "")).upper()
        if action not in {"BUY", "ADD"}:
            continue
        ticker = str(item.get("ticker", "")).upper()
        rank = int(item.get("rank") or 999)
        current_value = float(item.get("current_value", 0.0) or 0.0)
        suggested = float(item.get("suggested_dollars", 0.0) or 0.0)
        cluster = str(item.get("cluster") or "other")

        if V43_GROWTH_AVOID_SPEC_OVERLAP and ticker in spec_tickers:
            _v43_mark_skip(item, f"Ticker already exists in SPEC ledger; avoid duplicate broker position across sleeves.", "HOLD" if current_value > 0 else "SKIP")
            continue
        if current_value <= 0 and rank > V43_GROWTH_EXECUTE_TOP_N_SMALL:
            _v43_mark_skip(item, f"Small-account Growth execution only opens top {V43_GROWTH_EXECUTE_TOP_N_SMALL} ranks first.", "SKIP")
            continue
        if suggested < V43_GROWTH_MIN_ORDER_DOLLARS:
            _v43_mark_skip(item, f"Growth action below minimum {format_money(V43_GROWTH_MIN_ORDER_DOLLARS)}.", "HOLD" if current_value > 0 else "SKIP")
            continue
        if current_value <= 0:
            if allowed_new_clusters.get(cluster, 0) >= V43_GROWTH_MAX_NEW_BUYS_PER_CLUSTER_SMALL:
                _v43_mark_skip(item, f"Small-account Growth cluster cap reached for {cluster}.", "SKIP")
                continue
            allowed_new_clusters[cluster] = allowed_new_clusters.get(cluster, 0) + 1

    return _v43_refresh_actionable(plan)


def _V442_OLD_RECORD_GROWTH_BUY(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:  # type: ignore[override]
    if V43_COST_AWARE_ENABLED:
        ticker_norm = normalize_ticker(str(ticker)) or ""
        amount = float(shares) * float(price)
        if amount < V43_GROWTH_MIN_ORDER_DOLLARS:
            return False, f"Growth buy rejected by v4.3: order {format_money(amount)} is below minimum {format_money(V43_GROWTH_MIN_ORDER_DOLLARS)}."
        if V43_GROWTH_AVOID_SPEC_OVERLAP and _v43_has_position("load_spec_positions", ticker_norm):
            return False, f"Growth buy rejected by v4.3: {ticker_norm} is already held in SPEC. Avoid duplicate ticker across sleeves."
        try:
            plan = current_growth_plan_for_validation()
            action = latest_growth_plan_action_map(plan).get(ticker_norm, {})
            if str(action.get("action", "")).upper() == "SKIP":
                return False, f"Growth buy rejected by v4.3 plan policy: {action.get('v43_skip_reason', 'not executable in cost-aware mode')}"
            if str(action.get("action", "")).upper() not in {"BUY", "ADD"}:
                return False, f"Growth buy rejected by v4.3: current plan action for {ticker_norm} is {action.get('action')}."
        except Exception:
            pass
    return _V43_OLD_RECORD_GROWTH_BUY(ticker, shares, price, update_id=update_id)

# ---- SPEC cost-aware plan/action overlay ----

def _V44_OLD_COMPUTE_SPEC_PLAN() -> Dict[str, Any]:  # type: ignore[override]
    plan = _V43_OLD_COMPUTE_SPEC_PLAN()
    if not V43_COST_AWARE_ENABLED:
        return plan

    small = _v43_small_account_mode()
    spec_positions = load_spec_positions() if SPEC_ALPHA_LEDGER_ENABLED else {}
    spec_count = len(spec_positions)
    growth_tickers = _v43_position_tickers("load_growth_positions")
    new_sector_counts: Dict[str, int] = {}
    plan["v43_cost_aware"] = {
        "enabled": True,
        "small_account_mode": small,
        "spec_max_holdings_small": V43_SPEC_MAX_HOLDINGS_SMALL,
        "spec_buy_rank_limit_small": V43_SPEC_BUY_RANK_LIMIT_SMALL,
        "spec_min_order_dollars": V43_SPEC_MIN_ORDER_DOLLARS,
        "avoid_growth_overlap": V43_SPEC_AVOID_GROWTH_OVERLAP,
        "policy": "SPEC top 10 is the hold list; small-account buys/adds only execute from ranks 1-5 when size and holding-count rules pass.",
    }
    if not small:
        return plan

    for item in plan.get("actions", []) or []:
        action = str(item.get("action", "")).upper()
        if action not in {"BUY", "ADD"}:
            continue
        ticker = str(item.get("ticker", "")).upper()
        rank = int(item.get("rank") or 999)
        current_value = float(item.get("current_value", 0.0) or 0.0)
        suggested = float(item.get("suggested_dollars", 0.0) or 0.0)
        sector = str(item.get("sector") or item.get("bucket") or "other")

        if V43_SPEC_AVOID_GROWTH_OVERLAP and ticker in growth_tickers:
            _v43_mark_skip(item, "Ticker already exists in Growth ledger; avoid duplicate broker position across sleeves.", "HOLD" if current_value > 0 else "SKIP")
            continue
        if rank > V43_SPEC_BUY_RANK_LIMIT_SMALL:
            _v43_mark_skip(item, f"SPEC small-account execution buys/adds only ranks 1-{V43_SPEC_BUY_RANK_LIMIT_SMALL}; rank {rank} remains hold/watch.", "HOLD" if current_value > 0 else "SKIP")
            continue
        if suggested < V43_SPEC_MIN_ORDER_DOLLARS:
            _v43_mark_skip(item, f"SPEC action below minimum {format_money(V43_SPEC_MIN_ORDER_DOLLARS)}.", "HOLD" if current_value > 0 else "SKIP")
            continue
        if current_value <= 0 and spec_count >= V43_SPEC_MAX_HOLDINGS_SMALL:
            _v43_mark_skip(item, f"SPEC max holdings reached ({spec_count}/{V43_SPEC_MAX_HOLDINGS_SMALL}); sell rotation exits before new buys.", "SKIP")
            continue
        if current_value <= 0:
            if new_sector_counts.get(sector, 0) >= V43_SPEC_MAX_NEW_BUYS_PER_SECTOR_SMALL:
                _v43_mark_skip(item, f"SPEC new-buy sector cap reached for {sector}.", "SKIP")
                continue
            new_sector_counts[sector] = new_sector_counts.get(sector, 0) + 1

    return _v43_refresh_actionable(plan)


def _V442_OLD_RECORD_SPEC_BUY(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:  # type: ignore[override]
    if V43_COST_AWARE_ENABLED:
        ticker_norm = normalize_ticker(str(ticker)) or ""
        amount = float(shares) * float(price)
        spec_positions = load_spec_positions() if SPEC_ALPHA_LEDGER_ENABLED else {}
        already_held = ticker_norm in {str(x).upper() for x in spec_positions.keys()}
        if amount < V43_SPEC_MIN_ORDER_DOLLARS:
            return False, f"SPEC buy rejected by v4.3: order {format_money(amount)} is below minimum {format_money(V43_SPEC_MIN_ORDER_DOLLARS)}."
        if (not already_held) and len(spec_positions) >= V43_SPEC_MAX_HOLDINGS_SMALL:
            return False, f"SPEC buy rejected by v4.3: max SPEC holdings reached ({len(spec_positions)}/{V43_SPEC_MAX_HOLDINGS_SMALL}). Sell rotation exits before new buys."
        if V43_SPEC_AVOID_GROWTH_OVERLAP and _v43_has_position("load_growth_positions", ticker_norm):
            return False, f"SPEC buy rejected by v4.3: {ticker_norm} is already held in Growth. Avoid duplicate ticker across sleeves."
        try:
            plan = current_spec_plan_for_validation()
            action = latest_spec_plan_action_map(plan).get(ticker_norm, {})
            if str(action.get("action", "")).upper() == "SKIP":
                return False, f"SPEC buy rejected by v4.3 plan policy: {action.get('v43_skip_reason', 'not executable in cost-aware mode')}"
            if str(action.get("action", "")).upper() not in {"BUY", "ADD"}:
                return False, f"SPEC buy rejected by v4.3: current plan action for {ticker_norm} is {action.get('action')}."
        except Exception:
            pass
    return _V43_OLD_RECORD_SPEC_BUY(ticker, shares, price, update_id=update_id)

# ---- v4.3 status / allocation / diagnostics labels ----


# -----------------------------------------------------------------------------
# V4.4 MONTHLY ROTATION LOCK OVERLAY
# -----------------------------------------------------------------------------
# Operational policy only. No new alpha logic.
# Purpose:
# - Core, Growth Alpha, and SPEC_ALPHA are monthly rotation sleeves.
# - Daily/redeploy plan checks should NOT create next-day churn.
# - Rotation sells/trims are actionable only during a monthly rebalance window
#   and after a minimum hold period, unless a hard portfolio/risk exit applies.
# - Daily plans can still monitor rankings and show watchlist items.

V44_VERSION = "v4.4-monthly-lock-cost-aware-20-45-20-5-10-monitor"
if os.getenv("ALLOW_STRATEGY_VERSION_OVERRIDE", "0").strip() == "1":
    STRATEGY_VERSION = os.getenv("STRATEGY_VERSION", V44_VERSION)
else:
    STRATEGY_VERSION = V44_VERSION

V44_MONTHLY_LOCK_ENABLED = os.getenv("V44_MONTHLY_LOCK_ENABLED", "1").strip() != "0"
V44_MONTHLY_REBALANCE_SESSIONS = int(os.getenv("V44_MONTHLY_REBALANCE_SESSIONS", "5"))
V44_FORCE_REBALANCE_WINDOW = os.getenv("V44_FORCE_REBALANCE_WINDOW", "0").strip() == "1"
V44_ALLOW_HARD_MONTHLY_EXITS = os.getenv("V44_ALLOW_HARD_MONTHLY_EXITS", "1").strip() != "0"
V44_BLOCK_DAILY_TRIMS = os.getenv("V44_BLOCK_DAILY_TRIMS", "1").strip() != "0"
V44_ALLOW_MANUAL_LOCKED_SELL = os.getenv("V44_ALLOW_MANUAL_LOCKED_SELL", "0").strip() == "1"
V44_CORE_MIN_HOLD_DAYS = int(os.getenv("V44_CORE_MIN_HOLD_DAYS", "45"))
V44_GROWTH_MIN_HOLD_DAYS = int(os.getenv("V44_GROWTH_MIN_HOLD_DAYS", "21"))
V44_SPEC_MIN_HOLD_DAYS = int(os.getenv("V44_SPEC_MIN_HOLD_DAYS", "21"))
V44_SPEC_HOLD_RANK_BUFFER = int(os.getenv("V44_SPEC_HOLD_RANK_BUFFER", "20"))
V44_GROWTH_HOLD_RANK_BUFFER = int(os.getenv("V44_GROWTH_HOLD_RANK_BUFFER", "10"))
V44_CORE_HOLD_RANK_BUFFER = int(os.getenv("V44_CORE_HOLD_RANK_BUFFER", "8"))

def _v44_monthly_rebalance_window_info() -> Dict[str, Any]:
    """Return whether today is inside the configured monthly rebalance window."""
    current = ny_now()
    if V44_FORCE_REBALANCE_WINDOW:
        return {"open": True, "reason": "Forced open by V44_FORCE_REBALANCE_WINDOW=1", "session_number": None}
    try:
        month_start = current.replace(day=1).date()
        sched = NYSE.schedule(start_date=month_start, end_date=current.date())
        sessions = [d.date() for d in sched.index]
        if current.date() not in sessions:
            return {"open": False, "reason": "Today is not a NYSE session.", "session_number": None}
        session_number = sessions.index(current.date()) + 1
        is_open = session_number <= max(1, V44_MONTHLY_REBALANCE_SESSIONS)
        return {
            "open": bool(is_open),
            "reason": f"NYSE session {session_number} of month; window is first {V44_MONTHLY_REBALANCE_SESSIONS} sessions.",
            "session_number": session_number,
        }
    except Exception as exc:
        return {"open": False, "reason": f"Calendar check failed: {exc}", "session_number": None}

def _v44_position_age_days(sleeve: str, ticker: str) -> Optional[float]:
    ticker = str(ticker).upper()
    try:
        if sleeve == "CORE":
            positions = load_core_positions() if CORE_LEDGER_ENABLED else {}
        elif sleeve == "GROWTH":
            positions = load_growth_positions() if GROWTH_ALPHA_LEDGER_ENABLED else {}
        elif sleeve == "SPEC":
            positions = load_spec_positions() if SPEC_ALPHA_LEDGER_ENABLED else {}
        else:
            positions = {}
        pos = positions.get(ticker) or positions.get(ticker.upper())
        if not pos:
            return None
        raw_ts = pos.get("entry_time") or pos.get("last_update_time")
        if raw_ts is None:
            return None
        ts = float(raw_ts)
        if not math.isfinite(ts) or ts <= 0:
            return None
        return max(0.0, (now_ts() - ts) / 86400.0)
    except Exception:
        return None

def _v44_min_hold_days(sleeve: str) -> int:
    if sleeve == "CORE":
        return V44_CORE_MIN_HOLD_DAYS
    if sleeve == "GROWTH":
        return V44_GROWTH_MIN_HOLD_DAYS
    if sleeve == "SPEC":
        return V44_SPEC_MIN_HOLD_DAYS
    return 21

def _v44_hold_rank_buffer(sleeve: str) -> int:
    if sleeve == "CORE":
        return V44_CORE_HOLD_RANK_BUFFER
    if sleeve == "GROWTH":
        return V44_GROWTH_HOLD_RANK_BUFFER
    if sleeve == "SPEC":
        return V44_SPEC_HOLD_RANK_BUFFER
    return 10

def _v44_scored_rank_map(plan: Dict[str, Any], sleeve: str) -> Dict[str, int]:
    ranked: Dict[str, int] = {}
    for idx, item in enumerate(plan.get("all_scored", []) or [], start=1):
        ticker = str(item.get("ticker", "")).upper()
        if ticker and ticker not in ranked:
            ranked[ticker] = idx
    return ranked

def _v44_hard_monthly_exit(plan: Dict[str, Any], item: Dict[str, Any]) -> Tuple[bool, str]:
    """Hard exits bypass monthly lock only for real risk/allocation reasons."""
    if not V44_ALLOW_HARD_MONTHLY_EXITS:
        return False, "Hard exits disabled by configuration."
    risk = plan.get("risk_guard", {}) or {}
    if bool(risk.get("hard_active")):
        return True, "Portfolio hard-pause/risk guard is active."
    reason = str(item.get("reason", "")).lower()
    hard_phrases = [
        "market filter failed",
        "allocation is currently zero",
        "allocation guard",
        "risk/allocation guard",
        "hard pause",
        "panic",
    ]
    for phrase in hard_phrases:
        if phrase in reason:
            return True, f"Hard monthly exit reason: {item.get('reason')}"
    return False, "Rotation exit only."

def _v44_lock_action(item: Dict[str, Any], sleeve: str, original_action: str, reason: str) -> None:
    item["v44_monthly_lock"] = True
    item["v44_original_action"] = original_action
    item["v44_lock_reason"] = reason
    item["action"] = "HOLD"
    item["reason"] = reason

def _v44_apply_monthly_lock(plan: Dict[str, Any], sleeve: str) -> Dict[str, Any]:
    if not V44_MONTHLY_LOCK_ENABLED:
        return plan
    win = _v44_monthly_rebalance_window_info()
    window_open = bool(win.get("open"))
    min_hold = _v44_min_hold_days(sleeve)
    rank_buffer = _v44_hold_rank_buffer(sleeve)
    scored_rank = _v44_scored_rank_map(plan, sleeve)
    locked: List[Dict[str, Any]] = []

    for item in plan.get("actions", []) or []:
        action = str(item.get("action", "")).upper()
        if action not in {"SELL", "TRIM"}:
            continue
        if action == "TRIM" and not V44_BLOCK_DAILY_TRIMS:
            continue
        ticker = str(item.get("ticker", "")).upper()
        hard, hard_reason = _v44_hard_monthly_exit(plan, item)
        if hard:
            item["v44_monthly_lock_checked"] = True
            item["v44_lock_bypassed"] = True
            item["v44_lock_bypass_reason"] = hard_reason
            continue
        age_days = _v44_position_age_days(sleeve, ticker)
        score_rank = scored_rank.get(ticker)
        reasons: List[str] = []
        if not window_open:
            reasons.append(str(win.get("reason")))
        if age_days is not None and age_days < min_hold:
            reasons.append(f"min hold {min_hold} days not reached; held {round(age_days, 1)} days")
        # If a current position is still in the broader ranked/qualified list, do not churn it.
        if score_rank is not None and score_rank <= rank_buffer:
            reasons.append(f"still qualified inside {sleeve} hold buffer rank {score_rank}/{rank_buffer}")
        if reasons:
            lock_reason = (
                f"v4.4 monthly lock: {original_action_label(action)} blocked. "
                + "; ".join(reasons)
                + ". Treat as HOLD/watch, not an execution order."
            )
            _v44_lock_action(item, sleeve, action, lock_reason)
            locked.append(item)

    plan["v44_monthly_lock"] = {
        "enabled": True,
        "sleeve": sleeve,
        "rebalance_window_open": window_open,
        "rebalance_window_reason": win.get("reason"),
        "min_hold_days": min_hold,
        "hold_rank_buffer": rank_buffer,
        "locked_count": len(locked),
        "locked_tickers": [x.get("ticker") for x in locked],
        "policy": "Monthly sleeves do not execute daily/redeploy rotation sells or trims. Locked exits are HOLD/watch until monthly window and minimum hold are satisfied, unless a hard risk/allocation exit applies.",
    }
    return _v43_refresh_actionable(plan)

def original_action_label(action: str) -> str:
    action = str(action).upper()
    return {"SELL": "sell", "TRIM": "trim"}.get(action, action.lower())


# ---- Core monthly-lock overlay ----

def compute_wealth_core_plan() -> Dict[str, Any]:  # type: ignore[override]
    return _v44_apply_monthly_lock(_V44_OLD_COMPUTE_CORE_PLAN(), "CORE")


# ---- Growth monthly-lock overlay ----

def _V442_OLD_COMPUTE_GROWTH_PLAN() -> Dict[str, Any]:  # type: ignore[override]
    return _v44_apply_monthly_lock(_V44_OLD_COMPUTE_GROWTH_PLAN(), "GROWTH")


# ---- SPEC monthly-lock overlay ----

def compute_spec_alpha_plan() -> Dict[str, Any]:  # type: ignore[override]
    return _v44_apply_monthly_lock(_V44_OLD_COMPUTE_SPEC_PLAN(), "SPEC")


# ---- Validation-plan freshness guard ----
# Existing databases may contain a still-valid v4.3 plan without v4.4 lock metadata.
# For sell validation, force recomputation once so locked exits cannot slip through.

def current_core_plan_for_validation() -> Dict[str, Any]:  # type: ignore[override]
    plan = _V44_OLD_CURRENT_CORE_PLAN_FOR_VALIDATION()
    if V44_MONTHLY_LOCK_ENABLED and not (plan.get("v44_monthly_lock") or {}).get("enabled"):
        plan = compute_wealth_core_plan()
        save_core_plan_signal(plan)
    return plan


def current_spec_plan_for_validation() -> Dict[str, Any]:  # type: ignore[override]
    plan = _V44_OLD_CURRENT_SPEC_PLAN_FOR_VALIDATION()
    if V44_MONTHLY_LOCK_ENABLED and not (plan.get("v44_monthly_lock") or {}).get("enabled"):
        plan = compute_spec_alpha_plan()
        save_spec_plan_signal(plan)
    return plan

# ---- Sell guards: prevent accidental execution of locked monthly exits ----
def _v44_locked_sell_message(sleeve: str, ticker: str, plan: Dict[str, Any]) -> Optional[str]:
    action = None
    if sleeve == "CORE":
        action = latest_core_plan_action_map(plan).get(ticker)
    elif sleeve == "GROWTH":
        action = latest_growth_plan_action_map(plan).get(ticker)
    elif sleeve == "SPEC":
        action = latest_spec_plan_action_map(plan).get(ticker)
    if action and bool(action.get("v44_monthly_lock")) and not V44_ALLOW_MANUAL_LOCKED_SELL:
        return (
            f"{sleeve} sell blocked by v4.4 monthly lock for {ticker}.\n"
            f"Original action: {action.get('v44_original_action')}\n"
            f"Reason: {action.get('v44_lock_reason')}\n"
            "This is a monthly rotation sleeve, not a daily churn system. "
            "Set V44_ALLOW_MANUAL_LOCKED_SELL=1 only if you intentionally want to override."
        )
    return None


def _V441_OLD_RECORD_CORE_SELL(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:  # type: ignore[override]
    if V44_MONTHLY_LOCK_ENABLED:
        ticker_norm = normalize_ticker(str(ticker)) or ""
        try:
            plan = current_core_plan_for_validation()
            locked = _v44_locked_sell_message("CORE", ticker_norm, plan)
            if locked:
                return False, locked
        except Exception:
            pass
    return _V44_OLD_RECORD_CORE_SELL(ticker, shares, price, update_id=update_id)


def _V441_OLD_RECORD_GROWTH_SELL(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:  # type: ignore[override]
    if V44_MONTHLY_LOCK_ENABLED:
        ticker_norm = normalize_ticker(str(ticker)) or ""
        try:
            plan = current_growth_plan_for_validation()
            locked = _v44_locked_sell_message("GROWTH", ticker_norm, plan)
            if locked:
                return False, locked
        except Exception:
            pass
    return _V44_OLD_RECORD_GROWTH_SELL(ticker, shares, price, update_id=update_id)


def _V441_OLD_RECORD_SPEC_SELL(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:  # type: ignore[override]
    if V44_MONTHLY_LOCK_ENABLED:
        ticker_norm = normalize_ticker(str(ticker)) or ""
        try:
            plan = current_spec_plan_for_validation()
            locked = _v44_locked_sell_message("SPEC", ticker_norm, plan)
            if locked:
                return False, locked
        except Exception:
            pass
    return _V44_OLD_RECORD_SPEC_SELL(ticker, shares, price, update_id=update_id)

# ---- Labels/status ----


# -----------------------------------------------------------------------------
# V4.4.1 USER-FRIENDLY MONTHLY PLAN CLARITY OVERLAY
# -----------------------------------------------------------------------------
# Operational/reporting only. No alpha logic changes.
# Purpose:
# - Monthly rotation plans must be easier to execute safely.
# - Show estimated shares/units, suggested max limit, and exact command examples.
# - Make it clear that Core/Growth/SPEC are monthly rotation sleeves, not daily churn.
# - Add a controlled overweight-resize exception so accidental oversize manual buys
#   can be reduced without disabling the monthly-lock system globally.

V441_VERSION = "v4.4.1-clear-plans-monthly-lock-cost-aware-20-45-20-5-10-monitor"
if os.getenv("ALLOW_STRATEGY_VERSION_OVERRIDE", "0").strip() == "1":
    STRATEGY_VERSION = os.getenv("STRATEGY_VERSION", V441_VERSION)
else:
    STRATEGY_VERSION = V441_VERSION

V441_CORE_LIMIT_BUFFER_PCT = float(os.getenv("V441_CORE_LIMIT_BUFFER_PCT", "0.003"))
V441_GROWTH_LIMIT_BUFFER_PCT = float(os.getenv("V441_GROWTH_LIMIT_BUFFER_PCT", "0.005"))
V441_SPEC_LIMIT_BUFFER_PCT = float(os.getenv("V441_SPEC_LIMIT_BUFFER_PCT", "0.005"))
V441_ALLOW_OVERWEIGHT_RESIZE_SELL = os.getenv("V441_ALLOW_OVERWEIGHT_RESIZE_SELL", "1").strip() != "0"
V441_OVERWEIGHT_RESIZE_THRESHOLD = float(os.getenv("V441_OVERWEIGHT_RESIZE_THRESHOLD", "1.20"))

def _v441_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except Exception:
        return default

def _v441_action_emoji(action: str) -> str:
    action = str(action or "HOLD").upper()
    return {
        "BUY": "🟢 BUY",
        "ADD": "🟢 ADD",
        "HOLD": "🟡 HOLD",
        "TRIM": "🟠 TRIM",
        "SELL": "🔴 SELL",
        "SKIP": "⚪ SKIP",
        "WATCH": "👀 WATCH",
    }.get(action, action)

def _v441_qty_text(dollars: float, price: float) -> str:
    if dollars <= 0 or price <= 0:
        return "n/a"
    qty = dollars / price
    if qty >= 10:
        return str(round(qty, 2))
    if qty >= 1:
        return str(round(qty, 4)).rstrip("0").rstrip(".")
    return str(round(qty, 6)).rstrip("0").rstrip(".")

def _v441_limit_price(price: float, buffer_pct: float) -> float:
    if price <= 0:
        return 0.0
    return round(price * (1.0 + buffer_pct), 4)

def _v441_skip_reason(item: Dict[str, Any]) -> str:
    for key in ["v43_skip_reason", "v44_lock_reason", "skip_reason", "reason"]:
        val = item.get(key)
        if val:
            return str(val)
    return "Not actionable under current policy."

def _v441_execution_line(item: Dict[str, Any], sleeve: str, limit_buffer: float) -> str:
    ticker = str(item.get("ticker", "?")).upper()
    action = str(item.get("action", "HOLD")).upper()
    price = _v441_float(item.get("price"), 0.0)
    target_value = _v441_float(item.get("target_value"), 0.0)
    current_value = _v441_float(item.get("current_value"), 0.0)
    action_dollars = _v441_float(item.get("suggested_dollars"), 0.0)
    target_pct = item.get("target_account_pct", "n/a")
    rank = item.get("rank")
    group = item.get("cluster") or item.get("sector") or item.get("bucket") or "other"
    qty = _v441_qty_text(action_dollars, price)
    max_limit = _v441_limit_price(price, limit_buffer)

    head = f"{rank}) " if rank is not None else ""
    line = (
        f"{head}{_v441_action_emoji(action)} {ticker} ({group})\n"
        f"   Target: {format_money(target_value)} ({target_pct}% acct) | Current: {format_money(current_value)} | Action: {format_money(action_dollars)}\n"
        f"   Plan price: {price} | Est. qty: {qty} | Max limit guide: {max_limit if max_limit else 'n/a'}\n"
    )
    if action in {"BUY", "ADD"} and price > 0 and action_dollars > 0:
        cmd = {
            "CORE": "corebuy",
            "GROWTH": "growthbuy",
            "SPEC": "specbuy",
        }.get(sleeve.upper(), "buy")
        fee_note = " fee COMMISSION" if sleeve.upper() == "CORE" else ""
        line += f"   Command after fill: {cmd} {ticker} ACTUAL_QTY at ACTUAL_FILL_PRICE{fee_note}\n"
    if action in {"SKIP", "WATCH"} or item.get("v43_skip_reason") or item.get("v44_monthly_lock"):
        line += f"   Why: {_v441_skip_reason(item)}\n"
    return line

def _v441_plan_header(title: str, plan: Dict[str, Any], target_key: str, current_key: str) -> str:
    risk = plan.get("risk_guard", {}) or {}
    market = plan.get("market", "UNKNOWN")
    market_extra = ""
    if plan.get("market_score") is not None:
        market_extra = f" ({plan.get('market_score')}/8)"
    return (
        f"{title}\n\n"
        "Private execution guide. Execute in broker first, then record with the correct ledger command.\n"
        "Monthly rotation sleeve: daily checks are monitoring; do not churn daily exits.\n\n"
        f"🕒 NY time: {plan.get('ny_time')}\n"
        f"🌎 Market: {market_label(str(market))}{market_extra}\n"
        f"🛡️ Risk guard: {risk.get('recommended_action', 'n/a')}\n"
        f"💼 Equity estimate: {format_money(_v441_float(plan.get('account_equity')))}\n"
        f"🎯 Target sleeve: {format_money(_v441_float(plan.get(target_key)))}\n"
        f"📦 Current sleeve: {format_money(_v441_float(plan.get(current_key)))}\n\n"
    )

def _v441_format_monthly_plan(plan: Dict[str, Any], sleeve: str, title: str, target_key: str, current_key: str, limit_buffer: float, max_rank: int = 10) -> str:
    actions = plan.get("actions", []) or []
    ranked = [a for a in actions if a.get("rank") is not None]
    exits = [a for a in actions if str(a.get("action", "")).upper() in {"SELL", "TRIM"}]
    actionable = [a for a in ranked if str(a.get("action", "")).upper() in {"BUY", "ADD"}]
    msg = _v441_plan_header(title, plan, target_key, current_key)

    if actionable:
        msg += "✅ ACTIONABLE BUY/ADD NOW\n"
        for item in actionable[:5]:
            msg += _v441_execution_line(item, sleeve, limit_buffer)
        msg += "\n"
    else:
        msg += "✅ ACTIONABLE BUY/ADD NOW\nNone under current cost-aware/monthly-lock rules.\n\n"

    if exits:
        msg += "🔴 EXIT / TRIM MONITOR\n"
        for item in exits[:5]:
            msg += _v441_execution_line(item, sleeve, limit_buffer)
        msg += "Note: v4.4 monthly-lock blocks daily/redeploy exits unless the monthly window + min hold rules allow them.\n\n"

    if ranked:
        msg += "📋 RANKED HOLD / WATCH LIST\n"
        for item in ranked[:max_rank]:
            msg += _v441_execution_line(item, sleeve, limit_buffer)
            if len(msg) > MAX_TELEGRAM_MESSAGE - 800:
                msg += "...list truncated to fit Telegram.\n"
                break

    if sleeve.upper() == "CORE":
        msg += (
            "\nExecution rules:\n"
            f"• Core min order: {format_money(V43_CORE_MIN_ORDER_DOLLARS)}; tiny Core allocation stays cash.\n"
            "• For LSE/UCITS with commission: corebuy TICKER QTY at FILL_PRICE fee COMMISSION.\n"
        )
    elif sleeve.upper() == "GROWTH":
        msg += (
            "\nExecution rules:\n"
            f"• Small-account Growth opens/adds top {V43_GROWTH_EXECUTE_TOP_N_SMALL} first, min order {format_money(V43_GROWTH_MIN_ORDER_DOLLARS)}.\n"
            "• Avoid duplicate Growth/SPEC tickers.\n"
        )
    elif sleeve.upper() == "SPEC":
        msg += (
            "\nExecution rules:\n"
            f"• SPEC top 10 is hold/watch; buy/add ranks 1-{V43_SPEC_BUY_RANK_LIMIT_SMALL} only, min order {format_money(V43_SPEC_MIN_ORDER_DOLLARS)}.\n"
            f"• Max SPEC holdings before new buys: {V43_SPEC_MAX_HOLDINGS_SMALL}.\n"
        )
    msg += "• Use ACTUAL_FILL_PRICE after broker fill; do not enter broker average cost if it includes commission.\n"
    return msg[:MAX_TELEGRAM_MESSAGE]


def _v441_position_over_target(plan: Dict[str, Any], ticker: str, loader_name: str, target_multiplier: float = V441_OVERWEIGHT_RESIZE_THRESHOLD) -> Tuple[bool, str]:
    try:
        ticker_norm = normalize_ticker(str(ticker)) or ""
        if not ticker_norm:
            return False, "bad ticker"
        actions = {str(a.get("ticker", "")).upper(): a for a in plan.get("actions", []) or []}
        item = actions.get(ticker_norm)
        if not item:
            return False, "ticker not in current plan"
        target_value = _v441_float(item.get("target_value"), 0.0)
        loader = globals().get(loader_name)
        if loader is None:
            return False, "loader missing"
        positions = loader()
        pos = positions.get(ticker_norm)
        if not pos:
            return False, "position not found"
        prices = get_prices_batch([ticker_norm])
        mark = _v441_float(prices.get(ticker_norm), _v441_float(pos.get("avg_entry_price") or pos.get("price"), 0.0))
        current_value = mark * _v441_float(pos.get("shares"), 0.0)
        if target_value > 0 and current_value > target_value * target_multiplier:
            return True, f"current {format_money(current_value)} > {round(target_multiplier,2)}x target {format_money(target_value)}"
        return False, f"current {format_money(current_value)} within target tolerance"
    except Exception as exc:
        return False, str(exc)


def record_growth_sell(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:  # type: ignore[override]
    if V441_ALLOW_OVERWEIGHT_RESIZE_SELL:
        try:
            ok, reason = _v441_position_over_target(current_growth_plan_for_validation(), ticker, "load_growth_positions")
            if ok:
                return _V44_OLD_RECORD_GROWTH_SELL(ticker, shares, price, update_id=update_id)
        except Exception:
            pass
    return _V441_OLD_RECORD_GROWTH_SELL(ticker, shares, price, update_id=update_id)


def record_spec_sell(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:  # type: ignore[override]
    if V441_ALLOW_OVERWEIGHT_RESIZE_SELL:
        try:
            ok, reason = _v441_position_over_target(current_spec_plan_for_validation(), ticker, "load_spec_positions")
            if ok:
                return _V44_OLD_RECORD_SPEC_SELL(ticker, shares, price, update_id=update_id)
        except Exception:
            pass
    return _V441_OLD_RECORD_SPEC_SELL(ticker, shares, price, update_id=update_id)


def record_core_sell(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:  # type: ignore[override]
    if V441_ALLOW_OVERWEIGHT_RESIZE_SELL:
        try:
            ok, reason = _v441_position_over_target(current_core_plan_for_validation(), ticker, "load_core_positions")
            if ok:
                return _V44_OLD_RECORD_CORE_SELL(ticker, shares, price, update_id=update_id)
        except Exception:
            pass
    return _V441_OLD_RECORD_CORE_SELL(ticker, shares, price, update_id=update_id)


# =============================================================================
# V4.4.2 PARTIAL-FILL + LEDGER CORRECTION HOTFIX
# =============================================================================
# Purpose:
# - Do not change strategy, universe, scoring, allocations, or risk model.
# - Keep v4.4 monthly lock and v4.3 cost-aware execution.
# - Add a narrow way to record legitimate broker partial fills below normal
#   minimum-order thresholds, without reopening tiny planned micro-trades.
# - Add confirmed manual ledger position correction commands for rare mistakes.

V442_VERSION = "v4.4.2-partial-fill-ledger-tools-20-45-20-5-10-monitor"
if os.getenv("ALLOW_STRATEGY_VERSION_OVERRIDE", "0").strip() == "1":
    STRATEGY_VERSION = os.getenv("STRATEGY_VERSION", V442_VERSION)
else:
    STRATEGY_VERSION = V442_VERSION

V442_ALLOW_PARTIAL_FILL_RECORDING = os.getenv("V442_ALLOW_PARTIAL_FILL_RECORDING", "1").strip() != "0"
V442_PARTIAL_FILL_MIN_DOLLARS = float(os.getenv("V442_PARTIAL_FILL_MIN_DOLLARS", "25"))
V442_UNDERFILL_PRIORITY_ENABLED = os.getenv("V442_UNDERFILL_PRIORITY_ENABLED", "1").strip() != "0"
V442_GROWTH_UNDERFILL_RATIO = float(os.getenv("V442_GROWTH_UNDERFILL_RATIO", "0.85"))
V442_GROWTH_UNDERFILL_MIN_DOLLARS = float(os.getenv("V442_GROWTH_UNDERFILL_MIN_DOLLARS", "75"))
V442_ALLOW_LEDGER_EDIT = os.getenv("V442_ALLOW_LEDGER_EDIT", "1").strip() != "0"

def _v442_action_for_ticker(plan: Dict[str, Any], ticker: str) -> Optional[Dict[str, Any]]:
    ticker_norm = normalize_ticker(str(ticker)) or ""
    for item in plan.get("actions", []) or []:
        if str(item.get("ticker", "")).upper() == ticker_norm:
            return item
    return None

def _v442_validate_partial_monthly_buy(plan: Dict[str, Any], ticker: str, shares: float, price: float, sleeve: str) -> Tuple[bool, str]:
    if not V442_ALLOW_PARTIAL_FILL_RECORDING:
        return False, "partial-fill recording is disabled"
    ticker_norm = normalize_ticker(str(ticker)) or ""
    if not ticker_norm:
        return False, "invalid ticker"
    amount = float(shares) * float(price)
    if not math.isfinite(amount) or amount <= 0:
        return False, "invalid amount"
    if amount < V442_PARTIAL_FILL_MIN_DOLLARS:
        return False, f"partial fill below minimum recordable notional {format_money(V442_PARTIAL_FILL_MIN_DOLLARS)}"
    item = _v442_action_for_ticker(plan, ticker_norm)
    if item is None:
        return False, f"{ticker_norm} is not in the active {sleeve} plan"
    original_action = str(item.get("v44_original_action") or item.get("action") or "").upper()
    action = str(item.get("action") or "").upper()
    acceptable = {"BUY", "ADD", "HOLD", "SKIP"}
    if action not in acceptable and original_action not in acceptable:
        return False, f"{ticker_norm} is not a buy/add/hold candidate in the active {sleeve} plan"
    plan_price = _v441_float(item.get("price"), 0.0)
    if plan_price > 0:
        deviation = abs(float(price) - plan_price) / plan_price
        if deviation > BUY_QUOTE_DEVIATION_LIMIT:
            return False, f"fill price deviates too far from plan price ({round(deviation*100, 2)}% > {round(BUY_QUOTE_DEVIATION_LIMIT*100, 2)}%)"
    return True, "OK"

def _v442_append_position_note(table: str, ticker: str, note: str) -> None:
    try:
        with db_tx() as conn:
            row = conn.execute(f"SELECT notes FROM {table} WHERE ticker = ?", (ticker,)).fetchone()
            if row is None:
                return
            old = str(row["notes"] or "")
            joined = (old + " | " + note).strip(" |") if old else note
            conn.execute(f"UPDATE {table} SET notes = ?, last_update_time = ? WHERE ticker = ?", (joined[:1000], now_ts(), ticker))
    except Exception as exc:
        print(f"[V4.4.2 NOTE WARNING] {ticker}: {exc}")


def _V443_OLD_RECORD_CORE_BUY(ticker: str, shares: float, price: float, update_id: Optional[int] = None, fee: float = 0.0, partial_ok: bool = False) -> Tuple[bool, str]:  # type: ignore[override]
    ticker_norm = normalize_ticker(str(ticker)) or ""
    if partial_ok:
        plan = current_core_plan_for_validation()
        ok, reason = _v442_validate_partial_monthly_buy(plan, ticker_norm, shares, price, "CORE")
        if not ok:
            return False, "Core partial-fill recording rejected: " + reason
        # Bypass v4.3 minimum-order gate only for an already-filled broker partial.
        ok2, msg = _V43_OLD_RECORD_CORE_BUY(ticker_norm, shares, price, update_id=update_id, fee=fee)
        if ok2:
            _v442_append_position_note("core_positions", ticker_norm, f"v4.4.2 partial fill recorded: {shares} @ {price}, fee={fee}")
            msg += "\n\n🧩 v4.4.2: recorded as broker partial fill below normal Core minimum-order threshold."
        return ok2, msg
    return _V442_OLD_RECORD_CORE_BUY(ticker, shares, price, update_id=update_id, fee=fee)


def _V443_OLD_RECORD_GROWTH_BUY(ticker: str, shares: float, price: float, update_id: Optional[int] = None, partial_ok: bool = False) -> Tuple[bool, str]:  # type: ignore[override]
    ticker_norm = normalize_ticker(str(ticker)) or ""
    if partial_ok:
        plan = current_growth_plan_for_validation()
        ok, reason = _v442_validate_partial_monthly_buy(plan, ticker_norm, shares, price, "GROWTH")
        if not ok:
            return False, "Growth partial-fill recording rejected: " + reason
        if V43_GROWTH_AVOID_SPEC_OVERLAP and _v43_has_position("load_spec_positions", ticker_norm) and not _v43_has_position("load_growth_positions", ticker_norm):
            return False, f"Growth partial-fill recording rejected: {ticker_norm} is already held in SPEC. Keep one ticker in one monthly ledger."
        ok2, msg = _V43_OLD_RECORD_GROWTH_BUY(ticker_norm, shares, price, update_id=update_id)
        if ok2:
            item = _v442_action_for_ticker(plan, ticker_norm) or {}
            target_value = _v441_float(item.get("target_value"), 0.0)
            _v442_append_position_note("growth_positions", ticker_norm, f"v4.4.2 partial fill recorded: {shares} @ {price}; target_value={round(target_value,2)}")
            msg += "\n\n🧩 v4.4.2: recorded as broker partial fill below normal Growth minimum-order threshold. Future plans may show UNDERFILLED ADD priority if it remains a leader."
        return ok2, msg
    return _V442_OLD_RECORD_GROWTH_BUY(ticker, shares, price, update_id=update_id)


def _V443_OLD_RECORD_SPEC_BUY(ticker: str, shares: float, price: float, update_id: Optional[int] = None, partial_ok: bool = False) -> Tuple[bool, str]:  # type: ignore[override]
    ticker_norm = normalize_ticker(str(ticker)) or ""
    if partial_ok:
        plan = current_spec_plan_for_validation()
        ok, reason = _v442_validate_partial_monthly_buy(plan, ticker_norm, shares, price, "SPEC")
        if not ok:
            return False, "SPEC partial-fill recording rejected: " + reason
        if V43_SPEC_AVOID_GROWTH_OVERLAP and _v43_has_position("load_growth_positions", ticker_norm) and not _v43_has_position("load_spec_positions", ticker_norm):
            return False, f"SPEC partial-fill recording rejected: {ticker_norm} is already held in Growth. Keep one ticker in one monthly ledger."
        ok2, msg = _V43_OLD_RECORD_SPEC_BUY(ticker_norm, shares, price, update_id=update_id)
        if ok2:
            _v442_append_position_note("spec_positions", ticker_norm, f"v4.4.2 partial fill recorded: {shares} @ {price}")
            msg += "\n\n🧩 v4.4.2: recorded as broker partial fill below normal SPEC minimum-order threshold."
        return ok2, msg
    return _V442_OLD_RECORD_SPEC_BUY(ticker, shares, price, update_id=update_id)


def compute_growth_alpha_plan() -> Dict[str, Any]:  # type: ignore[override]
    plan = _V442_OLD_COMPUTE_GROWTH_PLAN()
    if not (V442_UNDERFILL_PRIORITY_ENABLED and V43_COST_AWARE_ENABLED):
        return plan
    try:
        for item in plan.get("actions", []) or []:
            ticker = str(item.get("ticker", "")).upper()
            rank = int(item.get("rank") or 999)
            target_value = _v441_float(item.get("target_value"), 0.0)
            current_value = _v441_float(item.get("current_value"), 0.0)
            suggested = max(0.0, target_value - current_value)
            ratio = current_value / target_value if target_value > 0 else 1.0
            if (
                current_value > 0
                and target_value > 0
                and ratio < V442_GROWTH_UNDERFILL_RATIO
                and suggested >= V442_GROWTH_UNDERFILL_MIN_DOLLARS
                and rank <= V43_GROWTH_EXECUTE_TOP_N_SMALL
                and str(item.get("action", "")).upper() in {"HOLD", "SKIP"}
            ):
                item["action"] = "ADD"
                item["suggested_dollars"] = round(suggested, 2)
                item["v442_underfill_priority"] = True
                item["v43_skip_reason"] = (
                    f"v4.4.2 underfill priority: current {format_money(current_value)} is {round(ratio*100,1)}% "
                    f"of target {format_money(target_value)}; catch-up add is allowed if price is within max limit."
                )
        plan["v442_underfill_priority"] = {
            "enabled": True,
            "growth_underfill_ratio": V442_GROWTH_UNDERFILL_RATIO,
            "growth_underfill_min_dollars": V442_GROWTH_UNDERFILL_MIN_DOLLARS,
        }
    except Exception as exc:
        print(f"[V4.4.2 UNDERFILL PLAN WARNING] {exc}")
    return plan

# Rewrap plan validation so the v4.4 monthly-lock layer sees the v4.4.2 underfill-adjusted Growth plan.
def current_growth_plan_for_validation() -> Dict[str, Any]:  # type: ignore[override]
    latest = load_latest_growth_plan()
    if latest is None:
        return compute_growth_alpha_plan()
    plan = latest.get("plan", {}) or {}
    if V44_MONTHLY_LOCK_ENABLED and not (plan.get("v44_monthly_lock") or {}).get("enabled"):
        plan = _v44_apply_monthly_lock(plan, "GROWTH")
    if V442_UNDERFILL_PRIORITY_ENABLED:
        # Recompute rather than mutate an old stored plan if underfill status may have changed after broker fills.
        return compute_growth_alpha_plan()
    return plan


def _v442_set_monthly_position(table: str, id_col: str, ticker: str, shares: float, avg_price: float, note: str) -> Tuple[bool, str]:
    ticker_norm = normalize_ticker(str(ticker)) or ""
    if not ticker_norm:
        return False, "invalid ticker"
    if not (math.isfinite(float(shares)) and float(shares) > 0 and math.isfinite(float(avg_price)) and float(avg_price) > 0):
        return False, "shares and average price must be positive"
    if not V442_ALLOW_LEDGER_EDIT:
        return False, "ledger edit commands are disabled"
    try:
        with db_tx() as conn:
            row = conn.execute(f"SELECT * FROM {table} WHERE ticker = ?", (ticker_norm,)).fetchone()
            if row is None:
                return False, f"{ticker_norm} not found in {table}; use the normal buy command first"
            cost = round(float(shares) * float(avg_price), 6)
            old_notes = str(row["notes"] or "") if "notes" in row.keys() else ""
            new_notes = (old_notes + " | " + note).strip(" |") if old_notes else note
            conn.execute(
                f"UPDATE {table} SET shares = ?, avg_entry_price = ?, cost_basis = ?, last_update_time = ?, notes = ? WHERE ticker = ?",
                (round(float(shares), 8), round(float(avg_price), 6), cost, now_ts(), new_notes[:1000], ticker_norm),
            )
        return True, (
            f"🛠️ LEDGER POSITION EDITED\n\n"
            f"Ledger table: {table}\n"
            f"Ticker: {ticker_norm}\n"
            f"Shares/units: {round(float(shares), 8)}\n"
            f"Avg price: {round(float(avg_price), 6)}\n"
            f"Cost basis: {format_money(cost)}\n\n"
            "Cash and historical trade records were NOT changed. Run brokerreconcile/brokersyncpreview after this if needed."
        )
    except Exception as exc:
        return False, str(exc)


def _v442_set_crypto_position(ticker: str, units: float, avg_price: float, note: str) -> Tuple[bool, str]:
    ticker_norm = normalize_ticker(str(ticker)) or ""
    if not ticker_norm:
        return False, "invalid ticker"
    if not (math.isfinite(float(units)) and float(units) > 0 and math.isfinite(float(avg_price)) and float(avg_price) > 0):
        return False, "units and average price must be positive"
    if not V442_ALLOW_LEDGER_EDIT:
        return False, "ledger edit commands are disabled"
    try:
        with db_tx() as conn:
            row = conn.execute("SELECT * FROM crypto_positions WHERE ticker = ?", (ticker_norm,)).fetchone()
            if row is None:
                return False, f"{ticker_norm} not found in crypto_positions; use cryptobuy first"
            cost = round(float(units) * float(avg_price), 6)
            old_notes = str(row["notes"] or "")
            new_notes = (old_notes + " | " + note).strip(" |") if old_notes else note
            conn.execute(
                "UPDATE crypto_positions SET units = ?, avg_entry_price = ?, cost_basis = ?, last_update_time = ?, notes = ? WHERE ticker = ?",
                (round(float(units), 8), round(float(avg_price), 6), cost, now_ts(), new_notes[:1000], ticker_norm),
            )
        return True, (
            f"🛠️ CRYPTO LEDGER POSITION EDITED\n\n"
            f"Ticker: {ticker_norm}\nUnits: {round(float(units), 8)}\nAvg price: {round(float(avg_price), 6)}\nCost basis: {format_money(cost)}\n\n"
            "Cash and historical trade records were NOT changed."
        )
    except Exception as exc:
        return False, str(exc)


def _V443_OLD_FORMAT_GROWTH_PLAN(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    msg = _v441_format_monthly_plan(
        plan,
        sleeve="GROWTH",
        title="🚀 GROWTH ALPHA PLAN v4.4.2 — CLEAR EXECUTION + UNDERFILL VIEW",
        target_key="target_growth_value",
        current_key="current_growth_value",
        limit_buffer=V441_GROWTH_LIMIT_BUFFER_PCT,
        max_rank=GROWTH_ALPHA_TOP_N,
    )
    extra = (
        "\n🧩 v4.4.2 underfill rule:\n"
        f"• Confirmed partial fills can be recorded with: growthbuy TICKER QTY at PRICE partial\n"
        f"• Underfilled top-{V43_GROWTH_EXECUTE_TOP_N_SMALL} Growth leaders can be prioritized for later catch-up adds if still leading.\n"
    )
    return (msg + extra)[:MAX_TELEGRAM_MESSAGE]

def _V443_OLD_FORMAT_CORE_PLAN(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    msg = _v441_format_monthly_plan(
        plan,
        sleeve="CORE",
        title="🏛️ CORE WEALTH PLAN v4.4.2 — CLEAR EXECUTION VIEW",
        target_key="target_core_value",
        current_key="current_core_value",
        limit_buffer=V441_CORE_LIMIT_BUFFER_PCT,
        max_rank=WEALTH_CORE_TOP_N,
    )
    return (msg + "\n🧩 v4.4.2: Core partial fills may be recorded with 'partial' only when they already happened in broker.\n")[:MAX_TELEGRAM_MESSAGE]

def _V443_OLD_FORMAT_SPEC_PLAN(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    msg = _v441_format_monthly_plan(
        plan,
        sleeve="SPEC",
        title="⚡ SPEC_ALPHA PLAN v4.4.2 — CLEAR EXECUTION VIEW",
        target_key="target_spec_value",
        current_key="current_spec_value",
        limit_buffer=V441_SPEC_LIMIT_BUFFER_PCT,
        max_rank=SPEC_ALPHA_TOP_N,
    )
    return (msg + "\n🧩 v4.4.2: SPEC partial fills may be recorded with 'partial' only when they already happened in broker.\n")[:MAX_TELEGRAM_MESSAGE]


# =============================================================================
# V4.4.3 REPORTING / LABEL / VALIDATION CLEANUP
# =============================================================================
# Reporting-only patch. No strategy, universe, allocation, ledger, execution,
# monthly-lock, cost-aware, partial-fill, or IBKR reconciliation logic changes.
# Purpose:
# - Remove stale v3.8/v4.3/v4.4.2 labels from user-facing reports.
# - Replace the old v3.8 institutional validation snapshot with current
#   v4.4.2/v4.4.3 operating assumptions and limitations.

V443_VERSION = "v4.4.3-reporting-cleanup-20-45-20-5-10-monitor"
if os.getenv("ALLOW_STRATEGY_VERSION_OVERRIDE", "0").strip() == "1":
    STRATEGY_VERSION = os.getenv("STRATEGY_VERSION", V443_VERSION)
else:
    STRATEGY_VERSION = V443_VERSION


def _v443_label_cleanup(msg: Any) -> str:
    text = str(msg)
    replacements = [
        ("INSTITUTIONAL STATUS v3.8", "INSTITUTIONAL STATUS v4.4.3"),
        ("DATA HEALTH v3.8", "DATA HEALTH v4.4.3"),
        ("RISK MATRIX v3.8", "RISK MATRIX v4.4.3"),
        ("STRESS STATUS v3.8", "STRESS STATUS v4.4.3"),
        ("EXECUTION STATUS v3.8", "EXECUTION STATUS v4.4.3"),
        ("MODEL DRIFT STATUS v3.8", "MODEL DRIFT STATUS v4.4.3"),
        ("VALIDATION STATUS v3.8", "VALIDATION STATUS v4.4.3"),
        ("IBKR BROKER STATUS v4.3", "IBKR BROKER STATUS v4.4.3"),
        ("IBKR POSITIONS v4.3", "IBKR POSITIONS v4.4.3"),
        ("IBKR BOT-MANAGED POSITIONS v4.3", "IBKR BOT-MANAGED POSITIONS v4.4.3"),
        ("EXTERNAL LEGACY POSITIONS v4.3", "EXTERNAL LEGACY POSITIONS v4.4.3"),
        ("IBKR RECONCILIATION v4.3", "IBKR RECONCILIATION v4.4.3"),
        ("BROKER SYNC PREVIEW v4.3", "BROKER SYNC PREVIEW v4.4.3"),
        ("BROKER SYNC APPLIED v4.3", "BROKER SYNC APPLIED v4.4.3"),
        ("IBKR RECONCILIATION COMMANDS v4.3", "IBKR RECONCILIATION COMMANDS v4.4.3"),
        ("IBKR BRIDGE PING v4.3", "IBKR BRIDGE PING v4.4.3"),
        ("RISK MATRIX v4.3 RECON", "RISK MATRIX v4.4.3 RECON"),
        ("STRESS STATUS v4.3 RECON", "STRESS STATUS v4.4.3 RECON"),
        ("v4.3 RECON", "v4.4.3 RECON"),
        ("v4.3", "v4.4.3"),
        ("V4.3", "V4.4.3"),
        ("V4.4.2 PARTIAL-FILL + MONTHLY LOCK STATUS", "V4.4.3 REPORTING CLEANUP + PARTIAL-FILL + MONTHLY LOCK STATUS"),
        ("GROWTH ALPHA PLAN v4.4.2", "GROWTH ALPHA PLAN v4.4.3"),
        ("CORE WEALTH PLAN v4.4.2", "CORE WEALTH PLAN v4.4.3"),
        ("SPEC_ALPHA PLAN v4.4.2", "SPEC_ALPHA PLAN v4.4.3"),
        ("v4.4.2 underfill rule", "v4.4.3 underfill rule"),
        ("v4.4.2: Core partial fills", "v4.4.3: Core partial fills"),
        ("v4.4.2: SPEC partial fills", "v4.4.3: SPEC partial fills"),
        ("v4.4.2: recorded as broker partial fill", "v4.4.3: recorded as broker partial fill"),
        ("v4.4.2 manual ledger edit", "v4.4.3 manual ledger edit"),
        ("v4.4.2-partial-fill-ledger-tools-20-45-20-5-10-monitor", V443_VERSION),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    # Avoid double replacements when a string already contains v4.4.3.
    text = text.replace("v4.4.3.3", "v4.4.3").replace("V4.4.3.3", "V4.4.3")
    return text

# ---- Validation snapshot cleanup ----


def institutional_snapshot() -> Dict[str, Any]:  # type: ignore[override]
    data = _V443_OLD_INSTITUTIONAL_SNAPSHOT()
    data["validationstatus"] = institutional_validation_snapshot()
    data["monitor_version"] = "v4.4.3_reporting_cleanup_diagnostic_only"
    data["strategy_version"] = STRATEGY_VERSION
    data["trading_logic_changed_by_v443"] = False
    data["reporting_cleanup"] = True
    return data

# ---- Wrap existing report formatters with label cleanup ----

def _V45_OLD_FORMAT_INSTITUTIONAL_STATUS() -> str:  # type: ignore[override]
    return _v443_label_cleanup(_V443_OLD_FORMAT_INSTITUTIONAL_STATUS())[:MAX_TELEGRAM_MESSAGE]

def _V45_OLD_FORMAT_DATAHEALTH_STATUS() -> str:  # type: ignore[override]
    return _v443_label_cleanup(_V443_OLD_FORMAT_DATAHEALTH_STATUS())[:MAX_TELEGRAM_MESSAGE]


def format_execution_status() -> str:  # type: ignore[override]
    return _v443_label_cleanup(_V443_OLD_FORMAT_EXECUTION_STATUS())[:MAX_TELEGRAM_MESSAGE]

def format_drift_status() -> str:  # type: ignore[override]
    return _v443_label_cleanup(_V443_OLD_FORMAT_DRIFT_STATUS())[:MAX_TELEGRAM_MESSAGE]


def _V45_OLD_FORMAT_BROKERSTATUS() -> str:  # type: ignore[override]
    return _v443_label_cleanup(_V443_OLD_FORMAT_BROKERSTATUS())[:MAX_TELEGRAM_MESSAGE]

def format_brokerpositions() -> str:  # type: ignore[override]
    return _v443_label_cleanup(_V443_OLD_FORMAT_BROKERPOSITIONS())[:MAX_TELEGRAM_MESSAGE]

def format_brokerexternal() -> str:  # type: ignore[override]
    return _v443_label_cleanup(_V443_OLD_FORMAT_BROKEREXTERNAL())[:MAX_TELEGRAM_MESSAGE]

def _V45_OLD_FORMAT_BROKERRECONCILE() -> str:  # type: ignore[override]
    return _v443_label_cleanup(_V443_OLD_FORMAT_BROKERRECONCILE())[:MAX_TELEGRAM_MESSAGE]

def _V45_OLD_FORMAT_BROKERSYNCPREVIEW() -> str:  # type: ignore[override]
    return _v443_label_cleanup(_V443_OLD_FORMAT_BROKERSYNCPREVIEW())[:MAX_TELEGRAM_MESSAGE]

def broker_sync_apply_confirmed() -> Tuple[bool, str]:  # type: ignore[override]
    ok, msg = _V443_OLD_BROKER_SYNC_APPLY_CONFIRMED()
    return ok, _v443_label_cleanup(msg)

def _V45_OLD_FORMAT_GROWTH_PLAN(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    return _v443_label_cleanup(_V443_OLD_FORMAT_GROWTH_PLAN(plan))[:MAX_TELEGRAM_MESSAGE]

def _V45_OLD_FORMAT_CORE_PLAN(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    return _v443_label_cleanup(_V443_OLD_FORMAT_CORE_PLAN(plan))[:MAX_TELEGRAM_MESSAGE]

def _V45_OLD_FORMAT_SPEC_PLAN(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    return _v443_label_cleanup(_V443_OLD_FORMAT_SPEC_PLAN(plan))[:MAX_TELEGRAM_MESSAGE]


# ---- Relabel a few command response strings without changing behavior ----

def record_core_buy(ticker: str, shares: float, price: float, update_id: Optional[int] = None, fee: float = 0.0, partial_ok: bool = False) -> Tuple[bool, str]:  # type: ignore[override]
    ok, msg = _V443_OLD_RECORD_CORE_BUY(ticker, shares, price, update_id=update_id, fee=fee, partial_ok=partial_ok)
    return ok, _v443_label_cleanup(msg)


def record_growth_buy(ticker: str, shares: float, price: float, update_id: Optional[int] = None, partial_ok: bool = False) -> Tuple[bool, str]:  # type: ignore[override]
    ok, msg = _V443_OLD_RECORD_GROWTH_BUY(ticker, shares, price, update_id=update_id, partial_ok=partial_ok)
    return ok, _v443_label_cleanup(msg)


def record_spec_buy(ticker: str, shares: float, price: float, update_id: Optional[int] = None, partial_ok: bool = False) -> Tuple[bool, str]:  # type: ignore[override]
    ok, msg = _V443_OLD_RECORD_SPEC_BUY(ticker, shares, price, update_id=update_id, partial_ok=partial_ok)
    return ok, _v443_label_cleanup(msg)

# ---- Status/command cleanup ----


# =============================================================================
# V4.5 HYBRID CRYPTO + GROWTH / CORE / SPEC CANDIDATE
# =============================================================================
# Research-backed candidate based on the 2026-05-31 execution-aware and crypto
# intraday tests.
#
# Intentional changes versus v4.4.3:
# - Allocation target becomes Core 20 / Growth 50 / SPEC 20 / Crypto 10.
# - Long VCP and Bear/Inverse allocation are set to 0 and their signal scans are
#   suppressed by default to avoid disabled-strategy noise.
# - Old alt-only crypto sleeve is replaced with BTC/ETH/SOL hybrid trend logic:
#     70% of crypto sleeve: daily 20-day breakout, BTC MA200 gate.
#     30% of crypto sleeve: 4h compression breakout, 2-of-3 MA200 gate.
# - Cost-aware execution, monthly-lock, partial-fill tools, manual ledger repair,
#   read-only IBKR reconciliation, and external legacy handling remain unchanged.
#
# This patch is not broker automation. No IBKR orders are placed.

V45_VERSION = "v4.5-hybrid-crypto-growth-core-spec-20-50-20-0-10-monitor"
if os.getenv("ALLOW_STRATEGY_VERSION_OVERRIDE", "0").strip() == "1":
    STRATEGY_VERSION = os.getenv("STRATEGY_VERSION", V45_VERSION)
else:
    STRATEGY_VERSION = V45_VERSION

# Allocation / sleeve policy.
V45_CORE_ALLOC = float(os.getenv("V45_CORE_ALLOC", "20"))
V45_GROWTH_ALLOC = float(os.getenv("V45_GROWTH_ALLOC", "50"))
V45_SPEC_ALLOC = float(os.getenv("V45_SPEC_ALLOC", "20"))
V45_LONG_VCP_ALLOC = float(os.getenv("V45_LONG_VCP_ALLOC", "0"))
V45_CRYPTO_ALLOC = float(os.getenv("V45_CRYPTO_ALLOC", "10"))
V45_BEAR_ALLOC = float(os.getenv("V45_BEAR_ALLOC", "0"))

# Suppress disabled tactical systems by default. Legacy functions remain in the
# file for backward compatibility with old state/exports, but they do not create
# new signals unless explicitly re-enabled.
V45_LONG_VCP_SIGNAL_ENGINE_ENABLED = os.getenv("V45_LONG_VCP_SIGNAL_ENGINE_ENABLED", "0") != "0"
V45_BEAR_SIGNAL_ENGINE_ENABLED = os.getenv("V45_BEAR_SIGNAL_ENGINE_ENABLED", "0") != "0"
BEAR_SLEEVE_ENABLED = BEAR_SLEEVE_ENABLED and V45_BEAR_SIGNAL_ENGINE_ENABLED and V45_BEAR_ALLOC > 0
try:
    if not V45_LONG_VCP_SIGNAL_ENGINE_ENABLED or V45_LONG_VCP_ALLOC <= 0:
        V2_MAX_SIGNALS_PER_SCAN = 0
        V2_ALLOW_VCP = False
        V2_ALLOW_BREAKOUTS = False
        V2_ALLOW_PULLBACKS = False
        V2_ALLOW_MEDIUM = False
        V2_ALLOW_WEAK = False
    if not BEAR_SLEEVE_ENABLED:
        BEAR_MAX_SIGNALS_PER_SCAN = 0
except Exception:
    pass

# Make the live allocation variables reflect v4.5 where old code reads them.
WEALTH_CORE_ACCOUNT_ALLOC_PCT = V45_CORE_ALLOC / 100.0
try:
    GROWTH_ALPHA_ACCOUNT_ALLOC_PCT = V45_GROWTH_ALLOC / 100.0
    SPEC_ALPHA_ACCOUNT_ALLOC_PCT = V45_SPEC_ALLOC / 100.0
    CRYPTO_ALPHA_ACCOUNT_ALLOC_PCT = V45_CRYPTO_ALLOC / 100.0
except Exception:
    pass

# Crypto v4.5 configuration.
CRYPTO_ALPHA_STRATEGY_VERSION = "crypto_alpha_hybrid_major_trend_70_30_v4_5"
CRYPTO_ALPHA_INDICATORS = ["BTCUSD", "ETHUSD", "SOLUSD"]
CRYPTO_ALPHA_UNIVERSE = ["BTCUSD", "ETHUSD", "SOLUSD"]
CRYPTO_ALPHA_ALL_SYMBOLS = list(dict.fromkeys(CRYPTO_ALPHA_INDICATORS + CRYPTO_ALPHA_UNIVERSE))
CRYPTO_ALPHA_MAX_OPEN_POSITIONS = int(os.getenv("CRYPTO_ALPHA_MAX_OPEN_POSITIONS", "2"))
CRYPTO_ALPHA_MIN_TRADE_DOLLARS = float(os.getenv("CRYPTO_ALPHA_MIN_TRADE_DOLLARS", "75"))
CRYPTO_ALPHA_BREAKOUT_DAYS = int(os.getenv("CRYPTO_ALPHA_BREAKOUT_DAYS", "20"))
V45_CRYPTO_DAILY_WEIGHT = float(os.getenv("V45_CRYPTO_DAILY_WEIGHT", "0.70"))
V45_CRYPTO_4H_WEIGHT = float(os.getenv("V45_CRYPTO_4H_WEIGHT", "0.30"))
V45_CRYPTO_4H_ENABLED = os.getenv("V45_CRYPTO_4H_ENABLED", "1") != "0"
V45_CRYPTO_4H_COMPRESSION_LOOKBACK = int(os.getenv("V45_CRYPTO_4H_COMPRESSION_LOOKBACK", "120"))
V45_CRYPTO_4H_BREAKOUT_BARS = int(os.getenv("V45_CRYPTO_4H_BREAKOUT_BARS", "60"))
V45_CRYPTO_4H_BB_RATIO_MAX = float(os.getenv("V45_CRYPTO_4H_BB_RATIO_MAX", "0.75"))
V45_CRYPTO_MAX_EXTENSION_EMA20 = float(os.getenv("V45_CRYPTO_MAX_EXTENSION_EMA20", "0.50"))
V45_CRYPTO_ATR_STOP_MULT = float(os.getenv("V45_CRYPTO_ATR_STOP_MULT", "2.5"))
V45_CRYPTO_TRAIL_ATR_MULT = float(os.getenv("V45_CRYPTO_TRAIL_ATR_MULT", "3.5"))

V45_VALIDATION_STRATEGY_LABEL = "v4.5 hybrid crypto/growth-core-spec"
V45_ALLOCATION_LABEL = "Core 20 / Growth 50 / SPEC 20 / Crypto 10 / Long VCP 0 / Bear 0 / Options 0"

def _v45_map_crypto_to_binance(ticker: str) -> Optional[str]:
    mapping = {
        "BTCUSD": "BTCUSDT",
        "ETHUSD": "ETHUSDT",
        "SOLUSD": "SOLUSDT",
    }
    return mapping.get(str(ticker).upper())

def _v45_binance_klines(symbol: str, interval: str = "4h", limit: int = 500) -> Optional[pd.DataFrame]:
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={int(limit)}"
        data = request_json(url, timeout=(3, 10), context=f"binance {symbol} {interval}", retries=1)
        if not isinstance(data, list) or not data:
            return None
        rows = []
        for item in data:
            if not isinstance(item, list) or len(item) < 6:
                continue
            rows.append({
                "date": pd.to_datetime(int(item[0]), unit="ms", utc=True),
                "Open": float(item[1]),
                "High": float(item[2]),
                "Low": float(item[3]),
                "Close": float(item[4]),
                "Volume": float(item[5]),
            })
        df = pd.DataFrame(rows)
        if df.empty:
            return None
        return df.sort_values("date").reset_index(drop=True)
    except Exception as exc:
        print(f"[V4.5 BINANCE ERROR] {symbol} {interval}: {exc}")
        return None

def _v45_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def _v45_crypto_daily_btc_gate() -> Dict[str, Any]:
    rows = []
    ok_count_2of3 = 0
    btc_ok = False
    for sym in CRYPTO_ALPHA_INDICATORS:
        df = get_historical(sym, limit=280)
        if df is None or len(df) < 220:
            rows.append({"ticker": sym, "ok": False, "reason": "no_data"})
            continue
        close = df["Close"].dropna()
        price = float(close.iloc[-1])
        ma50 = float(close.rolling(50).mean().iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])
        ok = price > ma200
        if ok:
            ok_count_2of3 += 1
        if sym == "BTCUSD":
            btc_ok = ok
        rows.append({"ticker": sym, "price": round(price, 6), "ma50": round(ma50, 6), "ma200": round(ma200, 6), "ok": ok})
    return {"ok": btc_ok, "btc_ok": btc_ok, "ok_count": ok_count_2of3, "required": 1, "required_4h": 2, "gate2_ok": ok_count_2of3 >= 2, "rows": rows}

def crypto_indicator_gate() -> Dict[str, Any]:  # type: ignore[override]
    return _v45_crypto_daily_btc_gate()

def _v45_crypto_daily_score(ticker: str, gate_ref_ret: Optional[float] = None) -> Optional[Dict[str, Any]]:
    try:
        df = get_historical(ticker, limit=300)
        if df is None or len(df) < 220:
            return None
        close = df["Close"].dropna()
        price = float(close.iloc[-1])
        high = df["High"]
        ma20 = float(_v45_ema(close, 20).iloc[-1])
        ma50 = float(_v45_ema(close, 50).iloc[-1])
        ma200 = float(close.rolling(200).mean().iloc[-1])
        roc7 = pct_change_last(df, 7)
        roc21 = pct_change_last(df, 21)
        roc63 = pct_change_last(df, 63) or 0.0
        roc126 = pct_change_last(df, 126) or 0.0
        atr_series = atr(df, 14)
        atr_val = float(atr_series.iloc[-1])
        atr_pct = atr_val / price if price > 0 else 0.0
        rsi_val = float(rsi(close, 14).iloc[-1])
        prior_high = float(close.shift(1).rolling(CRYPTO_ALPHA_BREAKOUT_DAYS).max().iloc[-1])
        if not (price > ma50 and price > ma200):
            return None
        if roc7 is None or roc21 is None or roc7 <= 0 or roc21 <= 0:
            return None
        if not (0.02 <= atr_pct <= 0.40):
            return None
        if rsi_val > 88:
            return None
        if price > ma20 * (1 + V45_CRYPTO_MAX_EXTENSION_EMA20):
            return None
        if price <= prior_high:
            return None
        rel = roc63 - float(gate_ref_ret or 0.0)
        score = (0.38 * roc126) + (0.32 * roc63) + (0.20 * roc21) + (0.10 * rel) - (0.08 * atr_pct)
        stop = max(0.000001, price - (V45_CRYPTO_ATR_STOP_MULT * atr_val))
        trail = max(0.000001, price - (V45_CRYPTO_TRAIL_ATR_MULT * atr_val))
        return {
            "ticker": ticker,
            "sub_strategy": "DAILY_MAJOR_BREAKOUT20",
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
        print(f"[V4.5 CRYPTO DAILY SCORE ERROR] {ticker}: {exc}")
        return None

def _v45_crypto_4h_compression_score(ticker: str, gate_ref_ret: Optional[float] = None) -> Optional[Dict[str, Any]]:
    try:
        symbol = _v45_map_crypto_to_binance(ticker)
        if not symbol:
            return None
        df = _v45_binance_klines(symbol, interval="4h", limit=500)
        if df is None or len(df) < 220:
            return None
        close = df["Close"].dropna()
        price = float(close.iloc[-1])
        ema20 = float(_v45_ema(close, 20).iloc[-1])
        ema50 = float(_v45_ema(close, 50).iloc[-1])
        ema200 = float(_v45_ema(close, 200).iloc[-1])
        ma20 = close.rolling(20).mean()
        sd20 = close.rolling(20).std()
        bb_width = ((ma20 + 2 * sd20) - (ma20 - 2 * sd20)) / ma20
        width_now = float(bb_width.iloc[-1])
        width_ref = float(bb_width.tail(V45_CRYPTO_4H_COMPRESSION_LOOKBACK).median())
        if not math.isfinite(width_now) or not math.isfinite(width_ref) or width_ref <= 0:
            return None
        compressed = width_now <= width_ref * V45_CRYPTO_4H_BB_RATIO_MAX
        prior_high = float(close.shift(1).rolling(V45_CRYPTO_4H_BREAKOUT_BARS).max().iloc[-1])
        roc7 = (price / float(close.iloc[-43]) - 1) if len(close) > 44 else None  # about 7 days on 4h bars
        roc21 = (price / float(close.iloc[-127]) - 1) if len(close) > 128 else None
        atr_series = atr(df, 14)
        atr_val = float(atr_series.iloc[-1])
        atr_pct = atr_val / price if price > 0 else 0.0
        rsi_val = float(rsi(close, 14).iloc[-1])
        if not (price > ema50 and price > ema200):
            return None
        if roc7 is None or roc21 is None or roc7 <= 0 or roc21 <= 0:
            return None
        if not compressed:
            return None
        if price <= prior_high:
            return None
        if not (0.015 <= atr_pct <= 0.40):
            return None
        if rsi_val > 88:
            return None
        if price > ema20 * (1 + V45_CRYPTO_MAX_EXTENSION_EMA20):
            return None
        score = (0.45 * roc21) + (0.35 * roc7) - (0.10 * atr_pct) - (0.10 * (width_now / width_ref))
        stop = max(0.000001, price - (V45_CRYPTO_ATR_STOP_MULT * atr_val))
        trail = max(0.000001, price - (V45_CRYPTO_TRAIL_ATR_MULT * atr_val))
        return {
            "ticker": ticker,
            "sub_strategy": "FOUR_HOUR_COMPRESSION_BREAKOUT",
            "price": round(price, 8),
            "ma20": round(ema20, 8),
            "ma50": round(ema50, 8),
            "ma200": round(ema200, 8),
            "prior_high": round(prior_high, 8),
            "bb_width_ratio": round(width_now / width_ref, 4),
            "roc_1m_pct": round(roc7 * 100, 2),
            "roc_3m_pct": round(roc21 * 100, 2),
            "roc_6m_pct": None,
            "atr": round(atr_val, 8),
            "atr_pct": round(atr_pct * 100, 2),
            "rsi": round(rsi_val, 2),
            "score": round(score, 6),
            "stop": round(stop, 8),
            "trail_reference": round(trail, 8),
            "max_valid_entry": round(price * (1 + MAX_ENTRY_EXTENSION_PCT), 8),
        }
    except Exception as exc:
        print(f"[V4.5 CRYPTO 4H SCORE ERROR] {ticker}: {exc}")
        return None


def compute_crypto_alpha_plan() -> Dict[str, Any]:  # type: ignore[override]
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
        df = get_historical(str(sym), limit=120)
        ret = pct_change_last(df, 63) if df is not None else None
        if ret is not None:
            gate_ref_rets.append(ret)
    gate_ref_ret = sum(gate_ref_rets) / len(gate_ref_rets) if gate_ref_rets else 0.0

    positions = load_crypto_positions() if CRYPTO_ALPHA_LEDGER_ENABLED else {}
    details = crypto_position_market_value_details() if CRYPTO_ALPHA_LEDGER_ENABLED else {"rows": [], "value": 0.0}
    current_rows = {str(row.get("ticker", "")).upper(): row for row in details.get("rows", [])}
    actions: List[Dict[str, Any]] = []
    daily_scored: List[Dict[str, Any]] = []
    h4_scored: List[Dict[str, Any]] = []

    if CRYPTO_ALPHA_ENABLED and target_pct > 0 and not risk.get("hard_active"):
        if gate.get("btc_ok"):
            for ticker in CRYPTO_ALPHA_UNIVERSE:
                item = _v45_crypto_daily_score(ticker, gate_ref_ret=gate_ref_ret)
                if item is not None:
                    daily_scored.append(item)
        if V45_CRYPTO_4H_ENABLED and gate.get("gate2_ok"):
            for ticker in CRYPTO_ALPHA_UNIVERSE:
                item = _v45_crypto_4h_compression_score(ticker, gate_ref_ret=gate_ref_ret)
                if item is not None:
                    h4_scored.append(item)

    daily_scored = sorted(daily_scored, key=lambda x: float(x.get("score", -999)), reverse=True)
    h4_scored = sorted(h4_scored, key=lambda x: float(x.get("score", -999)), reverse=True)
    selected_modules: List[Dict[str, Any]] = []
    if daily_scored:
        item = dict(daily_scored[0])
        item["module_weight"] = V45_CRYPTO_DAILY_WEIGHT
        selected_modules.append(item)
    if h4_scored:
        item = dict(h4_scored[0])
        item["module_weight"] = V45_CRYPTO_4H_WEIGHT
        selected_modules.append(item)

    # Aggregate module targets by ticker so duplicate selections do not create ledger ambiguity.
    agg: Dict[str, Dict[str, Any]] = {}
    for item in selected_modules:
        ticker = str(item["ticker"]).upper()
        existing = agg.get(ticker)
        if existing is None:
            existing = dict(item)
            existing["module_weight"] = 0.0
            existing["sub_strategy"] = []
            agg[ticker] = existing
        existing["module_weight"] += float(item.get("module_weight", 0.0) or 0.0)
        existing["sub_strategy"].append(str(item.get("sub_strategy", "UNKNOWN")))
        existing["score"] = max(float(existing.get("score", -999)), float(item.get("score", -999)))

    selected = sorted(agg.values(), key=lambda x: float(x.get("score", -999)), reverse=True)[:CRYPTO_ALPHA_MAX_OPEN_POSITIONS]
    selected_tickers = {str(x.get("ticker", "")).upper() for x in selected}

    for item in selected:
        ticker = str(item["ticker"]).upper()
        current_value = float(current_rows.get(ticker, {}).get("market_value", 0.0) or 0.0)
        module_weight = float(item.get("module_weight", 0.0) or 0.0)
        target_dollars = target_value * min(max(module_weight, 0.0), 1.0)
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
            "sub_strategy": "+".join(item.get("sub_strategy", [])) if isinstance(item.get("sub_strategy"), list) else item.get("sub_strategy"),
            "action": action,
            "target_account_pct": round(target_pct * module_weight * 100, 2),
            "target_value": round(target_dollars, 2),
            "current_value": round(current_value, 2),
            "suggested_dollars": round(abs(drift), 2),
            "drift_dollars": round(drift, 2),
        })

    # Exit/rotation candidates for current crypto holdings. v4.5 uses MA50/gate/ATR trail.
    for ticker, row in current_rows.items():
        mark = float(row.get("mark_price", row.get("avg_entry_price", 0)) or 0)
        if ticker in selected_tickers:
            df = get_historical(ticker, limit=90)
            if df is not None and len(df) >= 60:
                ma50 = float(_v45_ema(df["Close"], 50).iloc[-1])
                atr_val = float(atr(df, 14).iloc[-1])
                highest = max(float(row.get("highest") or mark), mark)
                trail = highest - (V45_CRYPTO_TRAIL_ATR_MULT * atr_val)
                stop = float(row.get("stop") or 0.0)
                reason = None
                if not gate.get("btc_ok"):
                    reason = "BTC daily MA200 gate is off."
                elif mark < ma50:
                    reason = "Close/mark is below EMA50."
                elif stop > 0 and mark <= stop:
                    reason = "Initial stop hit."
                elif mark <= trail:
                    reason = "ATR trailing stop hit."
                if reason:
                    actions.append({"ticker": ticker, "action": "SELL", "price": mark, "target_account_pct": 0.0, "target_value": 0.0, "current_value": round(float(row.get("market_value", 0) or 0), 2), "suggested_dollars": round(float(row.get("market_value", 0) or 0), 2), "reason": reason})
            continue
        exit_reason = "Crypto gate is off or ticker is no longer selected by the v4.5 hybrid crypto model."
        actions.append({"ticker": ticker, "action": "SELL", "price": mark, "target_account_pct": 0.0, "target_value": 0.0, "current_value": round(float(row.get("market_value", 0) or 0), 2), "suggested_dollars": round(float(row.get("market_value", 0) or 0), 2), "reason": exit_reason})

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
        "daily_scored": daily_scored,
        "h4_scored": h4_scored,
        "all_scored": daily_scored + h4_scored,
        "module_weights": {"daily_breakout": V45_CRYPTO_DAILY_WEIGHT, "four_hour_compression": V45_CRYPTO_4H_WEIGHT},
    }

def crypto_target_for_ticker(plan: Dict[str, Any], ticker: str) -> Optional[Dict[str, Any]]:  # type: ignore[override]
    ticker = ticker.upper()
    for item in plan.get("actions", []) or []:
        if str(item.get("ticker", "")).upper() == ticker and str(item.get("action", "")).upper() in {"BUY", "ADD", "HOLD", "TRIM"}:
            return item
    for item in plan.get("top", []) or []:
        if str(item.get("ticker", "")).upper() == ticker:
            return item
    return None

def _V463_FINAL_OLD_CRYPTO_PLAN_FORMAT(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    gate = plan.get("gate", {}) or {}
    risk = plan.get("risk_guard", {}) or {}
    msg = (
        "🪙 CRYPTO_ALPHA HYBRID MAJOR TREND PLAN v4.5\n\n"
        "Private bot only. Execute in broker/exchange first, then record with cryptobuy/cryptosell.\n\n"
        f"🕒 NY time: {plan.get('ny_time')}\n"
        f"🛡️ Risk guard: {risk.get('recommended_action')}\n"
        f"💼 Equity estimate: {format_money(float(plan.get('account_equity', 0) or 0))}\n"
        f"🪙 Target crypto sleeve: {plan.get('target_crypto_account_pct')}% = {format_money(float(plan.get('target_crypto_value', 0) or 0))}\n"
        f"📦 Current crypto value: {format_money(float(plan.get('current_crypto_value', 0) or 0))}\n"
        f"📈 Crypto unrealized P/L: {format_money(float(plan.get('current_crypto_unrealized_profit', 0) or 0))}\n\n"
        "Strategy modules:\n"
        f"• 70% sleeve: BTC/ETH/SOL daily 20-day breakout, BTC MA200 gate.\n"
        f"• 30% sleeve: BTC/ETH/SOL 4h compression breakout, 2-of-3 MA200 gate.\n\n"
        f"🚦 Daily BTC gate: {yes_no(bool(gate.get('btc_ok')))}\n"
        f"🚦 4h module gate: {yes_no(bool(gate.get('gate2_ok')))} ({gate.get('ok_count')}/{len(CRYPTO_ALPHA_INDICATORS)} above MA200; required 2)\n"
    )
    for row in gate.get("rows", []):
        msg += f"• {row.get('ticker')}: price {row.get('price')} | MA200 {row.get('ma200')} | OK {yes_no(bool(row.get('ok')))}\n"
    msg += "\n"
    actions = plan.get("actions", []) or []
    ranked = [a for a in actions if str(a.get("action")).upper() in {"BUY", "ADD", "HOLD", "TRIM"}]
    exits = [a for a in actions if str(a.get("action")).upper() == "SELL"]
    if not actions:
        msg += "No crypto action. If gates are off, crypto sleeve stays cash.\n"
    if ranked:
        msg += "🎯 Crypto candidates\n"
        for i, item in enumerate(ranked, start=1):
            action = str(item.get("action", "HOLD")).upper()
            verb = {"BUY": "🟢 BUY", "ADD": "🟢 ADD", "HOLD": "🟡 HOLD", "TRIM": "🟠 TRIM"}.get(action, action)
            msg += (
                f"{i}) {verb} {item.get('ticker')} [{item.get('sub_strategy')}]\n"
                f"   Price: {item.get('price')} | Max entry: {item.get('max_valid_entry')} | Stop: {item.get('stop')}\n"
                f"   Target: {item.get('target_account_pct')}% acct / {format_money(float(item.get('target_value', 0) or 0))}\n"
                f"   Current: {format_money(float(item.get('current_value', 0) or 0))} | Action size: ~{format_money(float(item.get('suggested_dollars', 0) or 0))}\n"
                f"   1m {format_pct(item.get('roc_1m_pct'))} | 3m {format_pct(item.get('roc_3m_pct'))} | ATR {item.get('atr_pct')}% | Score {item.get('score')}\n"
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
        "• v4.5 trades BTC/ETH/SOL only; no cheap-alt default universe.\n"
        "• No crypto trade unless the relevant gate is active.\n"
        "• Do not use bought/sold, corebuy, growthbuy, or specbuy for crypto."
    )
    return msg[:MAX_TELEGRAM_MESSAGE]


# Suppress tactical scan noise when VCP/Bear allocations are intentionally 0.


# Validation and reporting labels.


def _v45_label_cleanup(msg: Any) -> str:
    text = _v443_label_cleanup(str(msg)) if '_v443_label_cleanup' in globals() else str(msg)
    replacements = [
        ("v4.4.3", "v4.5"),
        ("V4.4.3", "V4.5"),
        ("Core 20 / Growth 45 / SPEC 20 / Long VCP 5 / Crypto 10", V45_ALLOCATION_LABEL),
        ("Core: 20%\nGrowth Alpha: 45%\nSPEC_ALPHA: 20%\n🐂 Long VCP tactical: 5.0%\n🪙 Crypto tactical swing: 10.0%", "Core: 20%\nGrowth Alpha: 50%\nSPEC_ALPHA: 20%\n🐂 Long VCP tactical: 0.0%\n🪙 Crypto tactical swing: 10.0%"),
        ("v4.1.1-freeze-growth-crypto-swing-20-45-20-5-10-monitor", V45_VERSION),
        ("v4.4.2 partial-fill/monthly-lock/cost-aware", V45_VALIDATION_STRATEGY_LABEL),
        ("Crypto rules:\n• BTC/ETH/SOL are indicators; default buys use cheaper major crypto names.", "Crypto rules:\n• v4.5 trades BTC/ETH/SOL only using hybrid daily/4h trend logic."),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    text = text.replace("v4.5.3", "v4.5").replace("V4.5.3", "V4.5")
    return text

# Wrap key reports again for v4.5 labels.

def _V46_OLD_FORMAT_INSTITUTIONAL_STATUS() -> str:  # type: ignore[override]
    return _v45_label_cleanup(_V45_OLD_FORMAT_INSTITUTIONAL_STATUS())[:MAX_TELEGRAM_MESSAGE]

def _V46_OLD_FORMAT_DATAHEALTH_STATUS() -> str:  # type: ignore[override]
    return _v45_label_cleanup(_V45_OLD_FORMAT_DATAHEALTH_STATUS())[:MAX_TELEGRAM_MESSAGE]


def _V46_OLD_FORMAT_BROKERSTATUS() -> str:  # type: ignore[override]
    return _v45_label_cleanup(_V45_OLD_FORMAT_BROKERSTATUS())[:MAX_TELEGRAM_MESSAGE]

def _V46_OLD_FORMAT_BROKERRECONCILE() -> str:  # type: ignore[override]
    return _v45_label_cleanup(_V45_OLD_FORMAT_BROKERRECONCILE())[:MAX_TELEGRAM_MESSAGE]

def _V46_OLD_FORMAT_BROKERSYNCPREVIEW() -> str:  # type: ignore[override]
    return _v45_label_cleanup(_V45_OLD_FORMAT_BROKERSYNCPREVIEW())[:MAX_TELEGRAM_MESSAGE]

def _V463_FINAL_OLD_CORE_PLAN_FORMAT(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    return _v45_label_cleanup(_V45_OLD_FORMAT_CORE_PLAN(plan))[:MAX_TELEGRAM_MESSAGE]

def _V463_FINAL_OLD_GROWTH_PLAN_FORMAT(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    return _v45_label_cleanup(_V45_OLD_FORMAT_GROWTH_PLAN(plan))[:MAX_TELEGRAM_MESSAGE]

def _V463_FINAL_OLD_SPEC_PLAN_FORMAT(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    return _v45_label_cleanup(_V45_OLD_FORMAT_SPEC_PLAN(plan))[:MAX_TELEGRAM_MESSAGE]

# Status command.


# =============================================================================
# V4.6 FREEZE CANDIDATE - SWING ALPHA + HYBRID CRYPTO + MONTHLY ROTATION
# =============================================================================
# Final research candidate based on 2026-06-01 integrated swing/crypto tests.
# Intentional changes versus v4.5:
# - Allocation: Core 20 / Growth 45 / SPEC 15 / Swing Alpha 10 / Crypto 10.
# - Long VCP and Bear/Inverse remain allocation 0 and scans suppressed.
# - Swing Alpha replaces disabled VCP allocation with MACD + VAH reclaim logic.
# - Crypto remains BTC/ETH/SOL hybrid major trend logic from v4.5.
# - Cost-aware execution, monthly-lock, partial-fill tools, manual ledger repair,
#   read-only IBKR reconciliation, and external legacy handling remain unchanged.
# - No broker orders are placed in this version.

V46_VERSION = "v4.6-freeze-swing-alpha-20-45-15-10-10-monitor"
if os.getenv("ALLOW_STRATEGY_VERSION_OVERRIDE", "0").strip() == "1":
    STRATEGY_VERSION = os.getenv("STRATEGY_VERSION", V46_VERSION)
else:
    STRATEGY_VERSION = V46_VERSION

# Allocation policy.
V46_CORE_ALLOC = float(os.getenv("V46_CORE_ALLOC", "20"))
V46_GROWTH_ALLOC = float(os.getenv("V46_GROWTH_ALLOC", "45"))
V46_SPEC_ALLOC = float(os.getenv("V46_SPEC_ALLOC", "15"))
V46_SWING_ALLOC = float(os.getenv("V46_SWING_ALLOC", "10"))
V46_CRYPTO_ALLOC = float(os.getenv("V46_CRYPTO_ALLOC", "10"))

WEALTH_CORE_ACCOUNT_ALLOC_PCT = V46_CORE_ALLOC / 100.0
GROWTH_ALPHA_ACCOUNT_ALLOC_PCT = V46_GROWTH_ALLOC / 100.0
SPEC_ALPHA_ACCOUNT_ALLOC_PCT = V46_SPEC_ALLOC / 100.0
CRYPTO_ALPHA_ACCOUNT_ALLOC_PCT = V46_CRYPTO_ALLOC / 100.0

V45_LONG_VCP_ALLOC = 0.0
V45_BEAR_ALLOC = 0.0
V45_LONG_VCP_SIGNAL_ENGINE_ENABLED = os.getenv("V45_LONG_VCP_SIGNAL_ENGINE_ENABLED", "0") != "0"
V45_BEAR_SIGNAL_ENGINE_ENABLED = os.getenv("V45_BEAR_SIGNAL_ENGINE_ENABLED", "0") != "0"
BEAR_SLEEVE_ENABLED = False
try:
    V2_MAX_SIGNALS_PER_SCAN = 0
    V2_ALLOW_VCP = False
    V2_ALLOW_BREAKOUTS = False
    V2_ALLOW_PULLBACKS = False
    V2_ALLOW_MEDIUM = False
    V2_ALLOW_WEAK = False
    BEAR_MAX_SIGNALS_PER_SCAN = 0
except Exception:
    pass

V46_ALLOCATION_LABEL = "Core 20 / Growth 45 / SPEC 15 / Swing Alpha 10 / Crypto 10 / VCP 0 / Bear 0 / Options 0"

# ---- Swing Alpha configuration ----
SWING_ALPHA_ENABLED = os.getenv("SWING_ALPHA_ENABLED", "1") != "0"
SWING_ALPHA_LEDGER_ENABLED = os.getenv("SWING_ALPHA_LEDGER_ENABLED", "1") != "0"
SWING_ALPHA_STRATEGY_VERSION = os.getenv("SWING_ALPHA_STRATEGY_VERSION", "swing_alpha_macd_vah_reclaim_v1")
SWING_ALPHA_ACCOUNT_ALLOC_PCT = float(os.getenv("SWING_ALPHA_ACCOUNT_ALLOC_PCT", str(V46_SWING_ALLOC / 100.0)))
SWING_ALPHA_MAX_OPEN_POSITIONS = int(os.getenv("SWING_ALPHA_MAX_OPEN_POSITIONS", "2"))
SWING_ALPHA_MAX_PER_CLUSTER = int(os.getenv("SWING_ALPHA_MAX_PER_CLUSTER", "1"))
SWING_ALPHA_MIN_TRADE_DOLLARS = float(os.getenv("SWING_ALPHA_MIN_TRADE_DOLLARS", "75"))
SWING_ALPHA_QUOTE_DEVIATION_LIMIT = float(os.getenv("SWING_ALPHA_QUOTE_DEVIATION_LIMIT", "0.06"))
SWING_ALPHA_REQUIRE_LIVE_QUOTE = os.getenv("SWING_ALPHA_REQUIRE_LIVE_QUOTE", "1") != "0"
SWING_ALPHA_REQUIRE_ACTIVE_PLAN_FOR_BUY = os.getenv("SWING_ALPHA_REQUIRE_ACTIVE_PLAN_FOR_BUY", "1") != "0"
SWING_ALPHA_ALLOW_FRACTIONAL_SHARES = os.getenv("SWING_ALPHA_ALLOW_FRACTIONAL_SHARES", "1") != "0"
SWING_ALPHA_MIN_PRICE = float(os.getenv("SWING_ALPHA_MIN_PRICE", "8"))
SWING_ALPHA_MIN_AVG_DOLLAR_VOLUME = float(os.getenv("SWING_ALPHA_MIN_AVG_DOLLAR_VOLUME", "25000000"))
SWING_ALPHA_MIN_RET63 = float(os.getenv("SWING_ALPHA_MIN_RET63", "0.05"))
SWING_ALPHA_MAX_ATR_PCT = float(os.getenv("SWING_ALPHA_MAX_ATR_PCT", "0.18"))
SWING_ALPHA_MAX_RSI = float(os.getenv("SWING_ALPHA_MAX_RSI", "86"))
SWING_ALPHA_ENTRY_BUFFER_PCT = float(os.getenv("SWING_ALPHA_ENTRY_BUFFER_PCT", "0.006"))
SWING_ALPHA_ATR_STOP_MULT = float(os.getenv("SWING_ALPHA_ATR_STOP_MULT", "2.5"))
SWING_ALPHA_ATR_TRAIL_MULT = float(os.getenv("SWING_ALPHA_ATR_TRAIL_MULT", "3.5"))
SWING_ALPHA_TIME_STOP_DAYS = int(os.getenv("SWING_ALPHA_TIME_STOP_DAYS", "20"))
SWING_ALPHA_TIME_STOP_MIN_PCT = float(os.getenv("SWING_ALPHA_TIME_STOP_MIN_PCT", "0.08"))
SWING_ALPHA_AVOID_GROWTH_SPEC_DUPLICATES = os.getenv("SWING_ALPHA_AVOID_GROWTH_SPEC_DUPLICATES", "1") != "0"
SWING_ALPHA_SCORE_SLEEP_SEC = float(os.getenv("SWING_ALPHA_SCORE_SLEEP_SEC", "0.0"))

# Use the expanded growth/strong universe but remove ETFs and obvious non-common-stock proxies.
_V46_ETF_LIKE = {"SPY","QQQ","IWM","DIA","SMH","SOXX","XLK","IGV","XLF","KRE","XLE","XOP","XLV","IBB","XLI","IYT","XLP","XLY","XLC","XLB","XLU","XLRE","ITB"}
try:
    _v46_base_universe = list(dict.fromkeys(list(GROWTH_ALPHA_UNIVERSE) + list(WATCHLIST)))
except Exception:
    _v46_base_universe = list(dict.fromkeys(list(WATCHLIST)))
SWING_ALPHA_UNIVERSE = [t for t in _v46_base_universe if t not in _V46_ETF_LIKE]
# Keep universe sane in live bot. Research used a broader offline universe; live uses liquid leaders in the bot file.
SWING_ALPHA_UNIVERSE = list(dict.fromkeys(SWING_ALPHA_UNIVERSE))

def _v46_cluster(ticker: str) -> str:
    ticker = ticker.upper()
    if 'GROWTH_ALPHA_CLUSTER_MAP' in globals() and ticker in GROWTH_ALPHA_CLUSTER_MAP:
        return GROWTH_ALPHA_CLUSTER_MAP.get(ticker, "other")
    if ticker in WEALTH_ASSET_CLUSTERS:
        return WEALTH_ASSET_CLUSTERS.get(ticker, "other")
    return "other"

# ---- DB tables ----

def init_db() -> None:  # type: ignore[override]
    _V46_OLD_INIT_DB()
    conn = db_connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS swing_alpha_positions (
                ticker TEXT PRIMARY KEY,
                swing_position_id TEXT NOT NULL UNIQUE,
                strategy_version TEXT NOT NULL,
                shares REAL NOT NULL CHECK (shares > 0),
                avg_entry_price REAL NOT NULL CHECK (avg_entry_price > 0),
                cost_basis REAL NOT NULL CHECK (cost_basis >= 0),
                entry_time REAL NOT NULL,
                last_update_time REAL NOT NULL,
                highest REAL,
                stop REAL,
                sleeve TEXT NOT NULL DEFAULT 'SWING_ALPHA',
                target_account_pct REAL,
                last_plan_id TEXT,
                notes TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS swing_alpha_trades (
                id TEXT PRIMARY KEY,
                swing_position_id TEXT,
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
            CREATE INDEX IF NOT EXISTS idx_swing_alpha_trades_ticker ON swing_alpha_trades(ticker);
            CREATE INDEX IF NOT EXISTS idx_swing_alpha_trades_time ON swing_alpha_trades(time);
            CREATE TABLE IF NOT EXISTS swing_alpha_signals (
                id TEXT PRIMARY KEY,
                time REAL NOT NULL,
                plan_date TEXT NOT NULL,
                account_equity REAL NOT NULL,
                swing_target_pct REAL NOT NULL,
                plan_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            );
            CREATE INDEX IF NOT EXISTS idx_swing_alpha_signals_time ON swing_alpha_signals(time);
            CREATE TABLE IF NOT EXISTS cash_deposits (
                id TEXT PRIMARY KEY,
                time REAL NOT NULL,
                amount REAL NOT NULL CHECK (amount > 0),
                cash_before REAL NOT NULL,
                cash_after REAL NOT NULL,
                equity_before REAL NOT NULL,
                equity_after REAL NOT NULL,
                withdrawal_hwm_before REAL,
                withdrawal_hwm_after REAL NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cash_deposits_time ON cash_deposits(time);
            """
        )
        conn.commit()
    finally:
        conn.close()

def load_swing_alpha_positions() -> Dict[str, Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM swing_alpha_positions ORDER BY ticker").fetchall()
        out: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            out[r["ticker"]] = {
                "ticker": r["ticker"],
                "swing_position_id": r["swing_position_id"],
                "strategy_version": r["strategy_version"],
                "shares": float(r["shares"]),
                "avg_entry_price": float(r["avg_entry_price"]),
                "cost_basis": float(r["cost_basis"]),
                "entry_time": float(r["entry_time"]),
                "last_update_time": float(r["last_update_time"]),
                "highest": None if r["highest"] is None else float(r["highest"]),
                "stop": None if r["stop"] is None else float(r["stop"]),
                "sleeve": r["sleeve"],
                "target_account_pct": None if r["target_account_pct"] is None else float(r["target_account_pct"]),
                "last_plan_id": r["last_plan_id"],
                "notes": r["notes"],
            }
        return out
    finally:
        conn.close()

def load_swing_alpha_trades() -> List[Dict[str, Any]]:
    conn = db_connect()
    try:
        rows = conn.execute("SELECT * FROM swing_alpha_trades ORDER BY time ASC, created_at ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def _v46_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def _v46_macd_hist(close: pd.Series) -> pd.Series:
    macd = _v46_ema(close, 12) - _v46_ema(close, 26)
    sig = _v46_ema(macd, 9)
    return macd - sig

def _v46_vah_val_proxy(df: pd.DataFrame, window: int = 20) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    try:
        tmp = df.tail(window).copy()
        if len(tmp) < max(10, window // 2):
            return None, None, None
        typical = (tmp["High"] + tmp["Low"] + tmp["Close"]) / 3.0
        vol = tmp["Volume"].clip(lower=0).fillna(0)
        if float(vol.sum()) <= 0:
            return None, None, None
        order = typical.argsort()
        vals = typical.iloc[order].astype(float).to_numpy()
        weights = vol.iloc[order].astype(float).to_numpy()
        cdf = weights.cumsum() / weights.sum()
        def wq(q: float) -> float:
            idx = int((cdf >= q).argmax())
            return float(vals[min(max(idx, 0), len(vals)-1)])
        poc_idx = int(vol.values.argmax())
        poc = float(typical.iloc[poc_idx])
        return poc, wq(0.85), wq(0.15)
    except Exception:
        return None, None, None

def swing_alpha_position_market_value_details() -> Dict[str, Any]:
    positions = load_swing_alpha_positions() if SWING_ALPHA_LEDGER_ENABLED else {}
    prices = get_prices_batch(list(positions.keys()))
    rows: List[Dict[str, Any]] = []
    value = 0.0
    cost = 0.0
    for ticker, pos in positions.items():
        mark = float(prices.get(ticker, pos.get("avg_entry_price", 0)) or 0)
        shares = float(pos.get("shares", 0) or 0)
        mv = mark * shares
        cb = float(pos.get("cost_basis", shares * float(pos.get("avg_entry_price", 0) or 0)) or 0)
        pnl = mv - cb
        pct = None if cb <= 0 else (pnl / cb) * 100
        value += mv
        cost += cb
        rows.append({
            **pos,
            "mark_price": round(mark, 4),
            "market_value": round(mv, 2),
            "unrealized_profit": round(pnl, 2),
            "unrealized_pct": pct,
            "cluster": _v46_cluster(ticker),
        })
    realized = round(sum(float(t.get("realized_profit") or 0.0) for t in load_swing_alpha_trades() if str(t.get("side")).upper() == "SELL"), 2)
    return {"rows": rows, "value": round(value, 2), "cost_basis": round(cost, 2), "unrealized_profit": round(value - cost, 2), "realized_profit": realized, "total_profit": round((value - cost) + realized, 2)}

def _v46_position_symbols_in_monthly_ledgers() -> set:
    out = set()
    try:
        out.update(load_growth_positions().keys())
    except Exception:
        pass
    try:
        out.update(load_spec_positions().keys())
    except Exception:
        pass
    return {str(x).upper() for x in out}

def swing_alpha_market_filter_ok() -> Tuple[bool, str]:
    try:
        frames = {s: get_historical(s, limit=260) for s in ["SPY", "QQQ"]}
        for s, df in frames.items():
            if df is None or len(df) < 220:
                return False, f"{s} data unavailable"
            close = df["Close"].dropna()
            ma200 = close.rolling(200).mean().iloc[-1]
            if pd.isna(ma200) or float(close.iloc[-1]) <= float(ma200):
                return False, f"{s} below MA200"
        return True, "SPY and QQQ above MA200"
    except Exception as exc:
        return False, f"market filter error: {exc}"

def _v46_score_swing_candidate(ticker: str) -> Optional[Dict[str, Any]]:
    df = get_historical(ticker, limit=260)
    if df is None or len(df) < 220:
        return None
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)
    price = float(close.iloc[-1])
    if price < SWING_ALPHA_MIN_PRICE:
        return None
    avg_dv = float((close * volume).tail(50).mean())
    if avg_dv < SWING_ALPHA_MIN_AVG_DOLLAR_VOLUME:
        return None
    ema20 = float(_v46_ema(close, 20).iloc[-1])
    ema50 = float(_v46_ema(close, 50).iloc[-1])
    ema200 = float(_v46_ema(close, 200).iloc[-1])
    if not (price > ema50 > ema200):
        return None
    ret20 = pct_change_over(close, 20)
    ret63 = pct_change_over(close, 63)
    ret126 = pct_change_over(close, 126)
    if ret63 is None or ret63 < SWING_ALPHA_MIN_RET63:
        return None
    atr_series = atr(df, 14)
    atr_val = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else 0.0
    if atr_val <= 0:
        return None
    atr_pct = atr_val / price
    if atr_pct <= 0 or atr_pct > SWING_ALPHA_MAX_ATR_PCT:
        return None
    rsi_val = float(rsi(close, 14).iloc[-1]) if not pd.isna(rsi(close, 14).iloc[-1]) else 50.0
    if rsi_val > SWING_ALPHA_MAX_RSI:
        return None
    hist = _v46_macd_hist(close)
    if len(hist.dropna()) < 5:
        return None
    h0, h1, h2 = float(hist.iloc[-1]), float(hist.iloc[-2]), float(hist.iloc[-3])
    macd_turn = (h0 > h1 and h1 <= h2) or (h0 > 0 and h0 > h1)
    if not macd_turn:
        return None
    poc, vah, val = _v46_vah_val_proxy(df.iloc[:-1], window=20)
    if vah is None or price <= float(vah):
        return None
    # Avoid buying obviously extended candles while still allowing opportunistic leaders.
    if price > ema20 * 1.18:
        return None
    stop = max(price - SWING_ALPHA_ATR_STOP_MULT * atr_val, ema20 * 0.985)
    if stop >= price:
        stop = price - SWING_ALPHA_ATR_STOP_MULT * atr_val
    max_entry = price * (1.0 + SWING_ALPHA_ENTRY_BUFFER_PCT)
    score = ((ret63 or 0) * 2.0) + ((ret20 or 0) * 1.2) + ((ret126 or 0) * 0.7) + (h0 * 5.0 / max(price, 1.0)) - (atr_pct * 0.8)
    return {
        "ticker": ticker,
        "price": round(price, 4),
        "signal_price": round(price, 4),
        "max_valid_entry": round(max_entry, 4),
        "stop": round(stop, 4),
        "ema20": round(ema20, 4),
        "ema50": round(ema50, 4),
        "ema200": round(ema200, 4),
        "vah_proxy": round(float(vah), 4),
        "poc_proxy": None if poc is None else round(float(poc), 4),
        "val_proxy": None if val is None else round(float(val), 4),
        "atr": round(atr_val, 4),
        "atr_pct": round(atr_pct * 100, 2),
        "rsi": round(rsi_val, 2),
        "ret20_pct": None if ret20 is None else round(ret20 * 100, 2),
        "ret63_pct": round(ret63 * 100, 2),
        "ret126_pct": None if ret126 is None else round(ret126 * 100, 2),
        "macd_hist": round(h0, 6),
        "score": round(score, 6),
        "cluster": _v46_cluster(ticker),
        "strategy_sleeve": "SWING_ALPHA",
        "setup_type": "macd_vah_reclaim",
        "strategy_version": SWING_ALPHA_STRATEGY_VERSION,
    }

def compute_swing_alpha_plan() -> Dict[str, Any]:
    snapshot = compute_equity_snapshot_data()
    equity = float(snapshot.get("equity", 0) or 0)
    allocation = dynamic_portfolio_allocation_targets()
    target_pct = float(allocation.get("swing_alpha_pct", SWING_ALPHA_ACCOUNT_ALLOC_PCT * 100) or 0) / 100.0
    target_value = equity * target_pct
    positions = load_swing_alpha_positions() if SWING_ALPHA_LEDGER_ENABLED else {}
    details = swing_alpha_position_market_value_details()
    current_rows = {r["ticker"]: r for r in details.get("rows", [])}
    market_ok, market_reason = swing_alpha_market_filter_ok()
    risk = portfolio_risk_guard_details() if PORTFOLIO_RISK_GUARD_ENABLED else {"hard_active": False, "soft_active": False, "recommended_action": "Normal risk mode."}
    duplicates = _v46_position_symbols_in_monthly_ledgers() if SWING_ALPHA_AVOID_GROWTH_SPEC_DUPLICATES else set()
    scored: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []
    if SWING_ALPHA_ENABLED and target_pct > 0 and market_ok and not risk.get("hard_active"):
        cluster_counts: Dict[str, int] = {}
        for idx, ticker in enumerate(SWING_ALPHA_UNIVERSE, start=1):
            if ticker in duplicates and ticker not in positions:
                continue
            item = _v46_score_swing_candidate(ticker)
            if item is None:
                continue
            cluster = item.get("cluster", "other")
            if cluster_counts.get(cluster, 0) >= SWING_ALPHA_MAX_PER_CLUSTER and ticker not in positions:
                continue
            scored.append(item)
            cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
            if SWING_ALPHA_SCORE_SLEEP_SEC > 0 and idx % 40 == 0:
                time.sleep(SWING_ALPHA_SCORE_SLEEP_SEC)
    scored = sorted(scored, key=lambda x: float(x.get("score", -999)), reverse=True)
    selected = scored[:max(SWING_ALPHA_MAX_OPEN_POSITIONS, 1)]
    selected_tickers = {x["ticker"] for x in selected}
    per_pos_target = 0.0 if SWING_ALPHA_MAX_OPEN_POSITIONS <= 0 else target_value / SWING_ALPHA_MAX_OPEN_POSITIONS
    for item in selected:
        ticker = item["ticker"]
        current_value = float(current_rows.get(ticker, {}).get("market_value", 0.0) or 0.0)
        drift = per_pos_target - current_value
        if current_value <= 0 and per_pos_target >= SWING_ALPHA_MIN_TRADE_DOLLARS:
            action = "BUY"
        elif current_value > 0 and drift > max(SWING_ALPHA_MIN_TRADE_DOLLARS, equity * 0.015):
            action = "ADD"
        else:
            action = "HOLD"
        actions.append({**item, "action": action, "target_account_pct": round((per_pos_target / equity) * 100 if equity > 0 else 0, 2), "target_value": round(per_pos_target, 2), "current_value": round(current_value, 2), "suggested_dollars": round(max(0.0, drift), 2), "reason": "MACD + VAH reclaim leader swing candidate."})
    # Existing-position exits.
    for ticker, pos in positions.items():
        mark = float(current_rows.get(ticker, {}).get("mark_price", pos.get("avg_entry_price", 0)) or 0)
        df = get_historical(ticker, limit=120)
        reason = None
        if not market_ok:
            reason = f"Market filter off: {market_reason}"
        elif df is not None and len(df) >= 60:
            close = df["Close"].astype(float)
            ema20 = float(_v46_ema(close, 20).iloc[-1])
            atr_val = float(atr(df, 14).iloc[-1]) if not pd.isna(atr(df, 14).iloc[-1]) else 0.0
            highest = max(float(pos.get("highest") or mark), mark)
            trail = highest - SWING_ALPHA_ATR_TRAIL_MULT * atr_val if atr_val > 0 else 0.0
            entry_time = float(pos.get("entry_time") or now_ts())
            days_held = max(0.0, (now_ts() - entry_time) / 86400.0)
            avg = float(pos.get("avg_entry_price", mark) or mark)
            gain = (mark / avg - 1.0) if avg > 0 else 0.0
            stop = float(pos.get("stop") or 0.0)
            if mark < ema20:
                reason = "Close/mark below EMA20 swing exit."
            elif stop > 0 and mark <= stop:
                reason = "Initial stop hit."
            elif trail > 0 and mark <= trail:
                reason = "ATR trailing stop hit."
            elif days_held >= SWING_ALPHA_TIME_STOP_DAYS and gain < SWING_ALPHA_TIME_STOP_MIN_PCT:
                reason = f"Time stop: {int(days_held)} days held without +{int(SWING_ALPHA_TIME_STOP_MIN_PCT*100)}% progress."
        if ticker not in selected_tickers and reason is None:
            # Do not auto-sell only because it is not selected today; sell only on actual exit rules.
            continue
        if reason:
            actions.append({"ticker": ticker, "action": "SELL", "price": mark, "target_account_pct": 0.0, "target_value": 0.0, "current_value": float(current_rows.get(ticker, {}).get("market_value", 0) or 0), "suggested_dollars": float(current_rows.get(ticker, {}).get("market_value", 0) or 0), "reason": reason})
    return {"plan_id": uuid.uuid4().hex, "strategy_version": SWING_ALPHA_STRATEGY_VERSION, "ny_time": ny_now().strftime("%Y-%m-%d %H:%M %Z"), "account_equity": round(equity,2), "target_swing_pct": round(target_pct*100,2), "target_swing_value": round(target_value,2), "current_swing_value": float(details.get("value",0) or 0), "current_swing_unrealized_profit": float(details.get("unrealized_profit",0) or 0), "market_ok": market_ok, "market_reason": market_reason, "risk_guard": risk, "universe_size": len(SWING_ALPHA_UNIVERSE), "scored_count": len(scored), "top": selected, "actions": actions, "actionable": [a for a in actions if str(a.get("action")).upper() in {"BUY","ADD","SELL","TRIM"}], "allocation": allocation}

def save_swing_alpha_plan(plan: Dict[str, Any]) -> None:
    if not SWING_ALPHA_LEDGER_ENABLED:
        return
    with db_tx() as conn:
        conn.execute("UPDATE swing_alpha_signals SET status='INACTIVE' WHERE status='ACTIVE'")
        conn.execute("INSERT INTO swing_alpha_signals(id, time, plan_date, account_equity, swing_target_pct, plan_json, status) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE')", (plan.get("plan_id", uuid.uuid4().hex), now_ts(), ny_date_str(), float(plan.get("account_equity",0) or 0), float(plan.get("target_swing_pct",0) or 0), json_dumps(plan)))

def load_latest_swing_alpha_signal() -> Optional[Dict[str, Any]]:
    conn = db_connect()
    try:
        row = conn.execute("SELECT * FROM swing_alpha_signals WHERE status='ACTIVE' ORDER BY time DESC LIMIT 1").fetchone()
        if not row:
            return None
        return json_loads_dict(row["plan_json"])
    finally:
        conn.close()

def swing_alpha_target_for_ticker(plan: Dict[str, Any], ticker: str) -> Optional[Dict[str, Any]]:
    ticker = ticker.upper()
    for item in plan.get("actions", []) or []:
        if str(item.get("ticker", "")).upper() == ticker:
            return item
    return None

def _V463_FINAL_OLD_SWING_PLAN_FORMAT(plan: Dict[str, Any]) -> str:
    risk = plan.get("risk_guard", {}) or {}
    msg = (
        "🎯 SWING_ALPHA PLAN v4.6 — MACD + VAH RECLAIM\n\n"
        "Private bot only. Execute in broker first, then record with swingbuy/swingsell.\n\n"
        f"🕒 NY time: {plan.get('ny_time')}\n"
        f"🛡️ Risk guard: {risk.get('recommended_action')}\n"
        f"🌎 Market filter: {yes_no(bool(plan.get('market_ok')))} — {plan.get('market_reason')}\n"
        f"💼 Equity estimate: {format_money(float(plan.get('account_equity', 0) or 0))}\n"
        f"🎯 Target Swing sleeve: {plan.get('target_swing_pct')}% = {format_money(float(plan.get('target_swing_value', 0) or 0))}\n"
        f"📦 Current Swing value: {format_money(float(plan.get('current_swing_value', 0) or 0))}\n"
        f"📈 Swing unrealized P/L: {format_money(float(plan.get('current_swing_unrealized_profit', 0) or 0))}\n"
        f"🧪 Universe/scored: {plan.get('universe_size')} / {plan.get('scored_count')}\n"
        f"🎚️ Max positions: {SWING_ALPHA_MAX_OPEN_POSITIONS} | Max per cluster: {SWING_ALPHA_MAX_PER_CLUSTER}\n\n"
    )
    actions = plan.get("actions", []) or []
    ranked = [a for a in actions if str(a.get("action")).upper() in {"BUY","ADD","HOLD"}]
    exits = [a for a in actions if str(a.get("action")).upper() == "SELL"]
    if ranked:
        msg += "🎯 Ranked Swing Alpha candidates\n"
        for i, item in enumerate(ranked, start=1):
            action = str(item.get("action", "HOLD")).upper()
            verb = {"BUY":"🟢 BUY", "ADD":"🟢 ADD", "HOLD":"🟡 HOLD"}.get(action, action)
            price = float(item.get("price",0) or 0)
            suggested = float(item.get("suggested_dollars",0) or 0)
            est_qty = suggested / price if price > 0 else 0.0
            msg += (
                f"{i}) {verb} {item.get('ticker')} ({item.get('cluster')})\n"
                f"   Target: {item.get('target_account_pct')}% acct / {format_money(float(item.get('target_value',0) or 0))}\n"
                f"   Current: {format_money(float(item.get('current_value',0) or 0))} | Action size: ~{format_money(suggested)}\n"
                f"   Plan price: {item.get('price')} | Max limit guide: {item.get('max_valid_entry')} | Stop: {item.get('stop')}\n"
                f"   Est. qty: ~{round(est_qty, 4)} | RSI {item.get('rsi')} | ATR {item.get('atr_pct')}% | Score {item.get('score')}\n"
                f"   Command after fill: swingbuy {item.get('ticker')} ACTUAL_SHARES at ACTUAL_FILL_PRICE\n"
            )
        msg += "\n"
    else:
        msg += "No Swing Alpha buy/add candidates now.\n\n"
    if exits:
        msg += "🔴 Swing Alpha exit candidates\n"
        for item in exits:
            msg += f"SELL {item.get('ticker')} — current {format_money(float(item.get('current_value',0) or 0))}\nReason: {item.get('reason')}\nCommand after fill: swingsell {item.get('ticker')} ACTUAL_SHARES at ACTUAL_FILL_PRICE\n"
        msg += "\n"
    msg += (
        "Swing Alpha rules:\n"
        "• Daily tactical swing sleeve, not monthly rotation.\n"
        "• Uses MACD histogram turn/reclaim + daily VAH proxy reclaim on strong leaders.\n"
        "• Max 2 positions, cluster cap active, no old VCP/bear signals.\n"
        "• Do not chase above max limit guide.\n"
        "• Separate Swing Alpha ledger; do not use corebuy/growthbuy/specbuy/cryptobuy."
    )
    return msg[:MAX_TELEGRAM_MESSAGE]

def format_swing_alpha_portfolio_report() -> str:
    details = swing_alpha_position_market_value_details()
    rows = details.get("rows", []) or []
    snapshot = compute_equity_snapshot_data()
    msg = (f"🎯 SWING_ALPHA PORTFOLIO\n\n"
           f"💵 Shared cash: {format_money(snapshot['cash'])}\n"
           f"🎯 Swing value: {format_money(float(details.get('value', 0) or 0))}\n"
           f"📏 Cost basis: {format_money(float(details.get('cost_basis', 0) or 0))}\n"
           f"📈 Unrealized P/L: {format_money(float(details.get('unrealized_profit', 0) or 0))}\n"
           f"✅ Realized Swing Alpha P/L: {format_money(float(details.get('realized_profit', 0) or 0))}\n"
           f"💼 Total equity: {format_money(snapshot['equity'])}\n\n")
    if not rows:
        return msg + "No Swing Alpha positions recorded yet. Use swingplan, then swingbuy after broker execution."
    for row in rows:
        msg += (f"📦 {row['ticker']}\n"
                f"Shares: {format_core_shares(row['shares'])}\n"
                f"Avg: {round(float(row['avg_entry_price']), 4)} | Now: {round(float(row['mark_price']), 4)}\n"
                f"Value: {format_money(float(row['market_value']))}\n"
                f"P/L: {format_money(float(row['unrealized_profit']))} ({format_pct(row.get('unrealized_pct'))})\n"
                f"Stop: {row.get('stop')} | High: {row.get('highest')}\n\n")
    return msg[:MAX_TELEGRAM_MESSAGE]

def format_swing_alpha_pnl_report() -> str:
    details = swing_alpha_position_market_value_details()
    trades = load_swing_alpha_trades()
    buys = [t for t in trades if str(t.get("side")).upper() == "BUY"]
    sells = [t for t in trades if str(t.get("side")).upper() == "SELL"]
    return (f"🎯 SWING_ALPHA P/L\n\n"
            f"🎯 Swing value: {format_money(float(details.get('value',0) or 0))}\n"
            f"📏 Cost basis: {format_money(float(details.get('cost_basis',0) or 0))}\n"
            f"📈 Unrealized P/L: {format_money(float(details.get('unrealized_profit',0) or 0))}\n"
            f"✅ Realized P/L: {format_money(float(details.get('realized_profit',0) or 0))}\n"
            f"Buy records: {len(buys)}\nSell records: {len(sells)}")

def format_swing_alpha_exposure_report() -> str:
    snapshot = compute_equity_snapshot_data()
    details = swing_alpha_position_market_value_details()
    equity = float(snapshot.get("equity", 0) or 0)
    alloc = dynamic_portfolio_allocation_targets()
    target_pct = float(alloc.get("swing_alpha_pct", 0) or 0)
    actual_pct = 0.0 if equity <= 0 else (float(details.get("value", 0) or 0) / equity) * 100
    return (f"🎯 SWING_ALPHA EXPOSURE\n\n"
            f"💼 Total equity: {format_money(equity)}\n"
            f"🎯 Swing value: {format_money(float(details.get('value', 0) or 0))}\n"
            f"🎯 Target Swing: {round(target_pct, 2)}% of account\n"
            f"📊 Actual Swing: {round(actual_pct, 2)}% of account\n"
            f"📐 Drift: {round(actual_pct - target_pct, 2)} percentage points\n\n"
            "Use swingplan for BUY/HOLD/SELL actions.")

def _v46_validate_swing_quote(ticker: str, price: float) -> Tuple[bool, str, Optional[float]]:
    if not SWING_ALPHA_REQUIRE_LIVE_QUOTE:
        return True, "OK", None
    quote = get_prices_batch([ticker]).get(ticker)
    if quote is None or quote <= 0:
        return False, f"No valid live quote for {ticker}.", None
    deviation = abs(price - quote) / quote
    if deviation > SWING_ALPHA_QUOTE_DEVIATION_LIMIT:
        return False, f"Swing Alpha trade rejected: your price is too far from live quote. Live quote: {round(quote,4)} | Your price: {round(price,4)} | Max deviation: {round(SWING_ALPHA_QUOTE_DEVIATION_LIMIT*100,2)}%", quote
    return True, "OK", quote

def record_swing_alpha_buy(ticker: str, shares: float, price: float, update_id: Optional[int] = None, partial_ok: bool = False) -> Tuple[bool, str]:
    if not SWING_ALPHA_LEDGER_ENABLED:
        return False, "Swing Alpha ledger is disabled."
    ticker = normalize_ticker(ticker or "")
    if ticker is None:
        return False, "Invalid ticker."
    if ticker not in SWING_ALPHA_UNIVERSE:
        return False, f"{ticker} is not in the Swing Alpha universe."
    if shares <= 0 or price <= 0 or not math.isfinite(shares) or not math.isfinite(price):
        return False, "Shares and price must be positive finite numbers."
    if (not SWING_ALPHA_ALLOW_FRACTIONAL_SHARES) and abs(shares - round(shares)) > 1e-9:
        return False, "Fractional Swing Alpha shares are disabled."
    amount = shares * price
    if amount < SWING_ALPHA_MIN_TRADE_DOLLARS and not partial_ok:
        return False, f"Swing Alpha buy rejected: order {format_money(amount)} is below minimum {format_money(SWING_ALPHA_MIN_TRADE_DOLLARS)}. Use 'partial' only for real broker partial fills already filled."
    latest = load_latest_swing_alpha_signal()
    target = swing_alpha_target_for_ticker(latest or {}, ticker) if latest else None
    if SWING_ALPHA_REQUIRE_ACTIVE_PLAN_FOR_BUY and target is None:
        return False, "No active Swing Alpha plan target for this ticker. Run swingplan first."
    if target is not None:
        max_entry = float(target.get("max_valid_entry", 0) or 0)
        if max_entry > 0 and price > max_entry and not partial_ok:
            return False, f"Entry above max limit guide. Max {round(max_entry,4)}, your price {round(price,4)}."
    ok, msg, quote = _v46_validate_swing_quote(ticker, price)
    if not ok:
        return False, msg
    with db_tx() as conn:
        cash = get_cash(conn)
        if amount > cash * CASH_USAGE_BUFFER:
            return False, f"Not enough cash. Need {format_money(amount)}, available with buffer {format_money(cash * CASH_USAGE_BUFFER)}."
        row = conn.execute("SELECT * FROM swing_alpha_positions WHERE ticker = ?", (ticker,)).fetchone()
        if row:
            old_shares = float(row["shares"])
            old_cost = float(row["cost_basis"])
            new_shares = old_shares + shares
            new_cost = old_cost + amount
            avg = new_cost / new_shares
            pos_id = row["swing_position_id"]
            highest = max(float(row["highest"] or price), float(quote or price), price)
            stop = float(row["stop"] or 0) or float((target or {}).get("stop", price * 0.92) or price * 0.92)
            conn.execute("""UPDATE swing_alpha_positions SET shares=?, avg_entry_price=?, cost_basis=?, last_update_time=?, highest=?, stop=?, target_account_pct=?, last_plan_id=? WHERE ticker=?""", (round(new_shares, 8), round(avg, 8), round(new_cost, 6), now_ts(), highest, stop, None if target is None else float(target.get("target_account_pct", 0) or 0), None if latest is None else latest.get("plan_id"), ticker))
        else:
            pos_id = f"SWING_{ticker}_{int(now_ts())}_{uuid.uuid4().hex[:8]}"
            highest = float(quote or price)
            stop = float((target or {}).get("stop", price * 0.92) or price * 0.92)
            conn.execute("""INSERT INTO swing_alpha_positions(ticker, swing_position_id, strategy_version, shares, avg_entry_price, cost_basis, entry_time, last_update_time, highest, stop, sleeve, target_account_pct, last_plan_id, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'SWING_ALPHA', ?, ?, ?)""", (ticker, pos_id, SWING_ALPHA_STRATEGY_VERSION, round(shares,8), round(price,8), round(amount,6), now_ts(), now_ts(), highest, stop, None if target is None else float(target.get("target_account_pct", 0) or 0), None if latest is None else latest.get("plan_id"), "partial fill" if partial_ok else ""))
        conn.execute("""INSERT INTO swing_alpha_trades(id, swing_position_id, ticker, side, shares, price, amount, realized_profit, time, strategy_version, plan_id, reason, created_at) VALUES (?, ?, ?, 'BUY', ?, ?, ?, NULL, ?, ?, ?, ?, ?)""", (uuid.uuid4().hex, pos_id, ticker, round(shares,8), round(price,8), round(amount,6), now_ts(), SWING_ALPHA_STRATEGY_VERSION, None if latest is None else latest.get("plan_id"), "broker_partial_fill" if partial_ok else "manual_broker_fill", now_ts()))
        set_cash_tx(conn, cash - amount)
        mark_update_processed_tx(conn, update_id, "swing_alpha_buy")
    extra = "\n\n🧩 v4.6: recorded as broker partial fill below normal Swing Alpha minimum-order threshold." if partial_ok else ""
    return True, (f"🎯 SWING_ALPHA BUY RECORDED {ticker}\n\n📦 Shares: {format_core_shares(shares)}\n💵 Price: {round(price,4)}\n💰 Amount: {format_money(amount)}\n🎯 Plan action: {None if target is None else target.get('action')}\n📐 Target account weight: {None if target is None else target.get('target_account_pct')}%\n💵 Cash left: {format_money(load_portfolio()['cash'])}{extra}")

def record_swing_alpha_sell(ticker: str, shares: float, price: float, update_id: Optional[int] = None) -> Tuple[bool, str]:
    if not SWING_ALPHA_LEDGER_ENABLED:
        return False, "Swing Alpha ledger is disabled."
    ticker = normalize_ticker(ticker or "")
    if ticker is None:
        return False, "Invalid ticker."
    if shares <= 0 or price <= 0 or not math.isfinite(shares) or not math.isfinite(price):
        return False, "Shares and price must be positive finite numbers."
    ok, msg, quote = _v46_validate_swing_quote(ticker, price)
    if not ok:
        return False, msg
    with db_tx() as conn:
        row = conn.execute("SELECT * FROM swing_alpha_positions WHERE ticker = ?", (ticker,)).fetchone()
        if row is None:
            return False, f"No Swing Alpha position found for {ticker}."
        current_shares = float(row["shares"])
        if shares > current_shares + 1e-8:
            return False, f"Cannot sell {format_core_shares(shares)}; only {format_core_shares(current_shares)} shares recorded."
        avg = float(row["avg_entry_price"])
        cost_basis = float(row["cost_basis"])
        proceeds = shares * price
        cost_removed = cost_basis * (shares / current_shares)
        realized = proceeds - cost_removed
        remaining = current_shares - shares
        pos_id = row["swing_position_id"]
        if remaining <= 1e-8:
            conn.execute("DELETE FROM swing_alpha_positions WHERE ticker=?", (ticker,))
        else:
            remaining_cost = cost_basis - cost_removed
            conn.execute("UPDATE swing_alpha_positions SET shares=?, cost_basis=?, avg_entry_price=?, last_update_time=? WHERE ticker=?", (round(remaining,8), round(remaining_cost,6), round(remaining_cost/remaining,8), now_ts(), ticker))
        cash = get_cash(conn)
        set_cash_tx(conn, cash + proceeds)
        conn.execute("""INSERT INTO swing_alpha_trades(id, swing_position_id, ticker, side, shares, price, amount, realized_profit, time, strategy_version, plan_id, reason, created_at) VALUES (?, ?, ?, 'SELL', ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (uuid.uuid4().hex, pos_id, ticker, round(shares,8), round(price,8), round(proceeds,6), round(realized,6), now_ts(), SWING_ALPHA_STRATEGY_VERSION, None, "manual_broker_exit", now_ts()))
        mark_update_processed_tx(conn, update_id, "swing_alpha_sell")
    pct = None if cost_removed <= 0 else (realized / cost_removed) * 100
    return True, (f"🎯 SWING_ALPHA SELL RECORDED {ticker}\n\n📦 Shares: {format_core_shares(shares)}\n💵 Price: {round(price,4)}\n💰 Proceeds: {format_money(proceeds)}\n📊 Realized Swing Alpha P/L: {format_money(realized)} ({format_pct(pct)})\n💵 Cash now: {format_money(load_portfolio()['cash'])}")

# ---- Include Swing Alpha in equity and reports ----

def compute_equity_snapshot_data() -> Dict[str, float]:  # type: ignore[override]
    snapshot = _V46_OLD_COMPUTE_EQUITY()
    details = swing_alpha_position_market_value_details() if SWING_ALPHA_LEDGER_ENABLED else {"value": 0.0, "cost_basis": 0.0, "unrealized_profit": 0.0}
    swing_value = float(details.get("value", 0) or 0)
    snapshot["swing_alpha_positions_value"] = round(swing_value, 2)
    snapshot["swing_alpha_cost_basis"] = round(float(details.get("cost_basis", 0) or 0), 2)
    snapshot["swing_alpha_unrealized_profit"] = round(float(details.get("unrealized_profit", 0) or 0), 2)
    snapshot["positions_value"] = round(float(snapshot.get("positions_value", 0) or 0) + swing_value, 2)
    snapshot["equity"] = round(float(snapshot.get("equity", 0) or 0) + swing_value, 2)
    try:
        snapshot.update(cash_deposit_summary())
        snapshot["performance_base_capital"] = round(get_performance_base_capital(), 2)
    except Exception:
        pass
    return snapshot


def realized_performance_all_time() -> Dict[str, Any]:  # type: ignore[override]
    perf = _V46_OLD_REALIZED_PERF()
    trades = load_swing_alpha_trades() if SWING_ALPHA_LEDGER_ENABLED else []
    swing_profit = round(sum(float(t.get("realized_profit") or 0.0) for t in trades if str(t.get("side")).upper() == "SELL"), 2)
    perf["swing_alpha_realized_profit"] = swing_profit
    perf["profit"] = round(float(perf.get("profit", 0) or 0) + swing_profit, 2)
    base_cap = get_performance_base_capital()
    perf["base_capital"] = round(base_cap, 2)
    perf["pct"] = None if base_cap <= 0 else (perf["profit"] / base_cap) * 100
    perf["swing_alpha_trade_records"] = len(trades)
    perf["trade_records"] = int(perf.get("trade_records", 0) or 0) + len(trades)
    try:
        perf.update(cash_deposit_summary())
    except Exception:
        pass
    return perf


# ---- Allocation and risk reports ----


def _V461_OLD_FORMAT_RISKMATRIX_STATUS() -> str:  # type: ignore[override]
    snapshot = compute_equity_snapshot_data()
    equity = float(snapshot.get("equity", 0) or 0)
    ledgers = [
        ("cash", float(snapshot.get("cash", 0) or 0)),
        ("core", float(snapshot.get("core_positions_value", 0) or 0)),
        ("growth", float(snapshot.get("growth_alpha_positions_value", 0) or 0)),
        ("spec", float(snapshot.get("spec_positions_value", 0) or 0)),
        ("swing_alpha", float(snapshot.get("swing_alpha_positions_value", 0) or 0)),
        ("crypto", float(snapshot.get("crypto_alpha_positions_value", 0) or 0)),
        ("legacy_swing", float(snapshot.get("swing_positions_value", 0) or 0)),
    ]
    rows = []
    for src, fn in [("core", core_position_market_value_details), ("growth", growth_position_market_value_details), ("spec", spec_position_market_value_details), ("swing_alpha", swing_alpha_position_market_value_details), ("crypto", crypto_position_market_value_details)]:
        try:
            for r in fn().get("rows", []):
                ticker = r.get("ticker")
                mv = float(r.get("market_value", 0) or 0)
                rows.append({"ticker": ticker, "ledger": src, "value": mv, "cluster": r.get("cluster") or _v46_cluster(str(ticker))})
        except Exception:
            pass
    clusters: Dict[str, float] = {}
    for r in rows:
        clusters[str(r.get("cluster") or "other")] = clusters.get(str(r.get("cluster") or "other"), 0.0) + float(r.get("value", 0) or 0)
    msg = f"🧮 RISK MATRIX v4.6 RECON\n\nStatus: ✅ OK\nTotal equity: {format_money(equity)}\n\nLedger exposure:\n"
    for name, val in ledgers:
        if abs(val) > 0.01:
            pct = 0.0 if equity <= 0 else (val/equity)*100
            msg += f"• {name}: {format_money(val)} ({round(pct,2)}%)\n"
    msg += "\nTop clusters:\n"
    for cluster, val in sorted(clusters.items(), key=lambda x: x[1], reverse=True)[:10]:
        msg += f"• {cluster}: {format_money(val)} ({round((val/equity)*100 if equity>0 else 0,2)}%)\n"
    msg += "\nTop positions:\n"
    for r in sorted(rows, key=lambda x: x["value"], reverse=True)[:12]:
        msg += f"• {r['ticker']} [{r['ledger']}]: {format_money(r['value'])} ({round((r['value']/equity)*100 if equity>0 else 0,2)}%)\n"
    warnings = []
    for r in rows:
        if equity > 0 and r["value"]/equity > MAX_POSITION_EQUITY_PCT:
            warnings.append(f"{r['ticker']} exceeds max position guide")
    msg += "\n" + ("✅ No concentration warnings." if not warnings else "⚠️ Warnings:\n" + "\n".join(f"• {w}" for w in warnings))
    return msg[:MAX_TELEGRAM_MESSAGE]

def _V461_OLD_FORMAT_STRESS_STATUS() -> str:  # type: ignore[override]
    snapshot = compute_equity_snapshot_data()
    equity = float(snapshot.get("equity", 0) or 0)
    core = float(snapshot.get("core_positions_value", 0) or 0)
    growth = float(snapshot.get("growth_alpha_positions_value", 0) or 0)
    spec = float(snapshot.get("spec_positions_value", 0) or 0)
    swing = float(snapshot.get("swing_alpha_positions_value", 0) or 0)
    crypto = float(snapshot.get("crypto_alpha_positions_value", 0) or 0)
    scenarios = {
        "broad_risk_off": -(0.10*core + 0.18*growth + 0.22*spec + 0.20*swing + 0.30*crypto),
        "growth_swing_unwind": -(0.25*growth + 0.25*swing + 0.15*spec),
        "semis_ai_shock": -(0.30*growth + 0.20*swing + 0.10*core),
        "crypto_flush": -(0.45*crypto + 0.05*growth),
    }
    worst_name, worst_val = min(scenarios.items(), key=lambda x: x[1]) if scenarios else ("none", 0.0)
    msg = f"🔥 STRESS STATUS v4.6 RECON\n\nStatus: ✅ OK\nEquity: {format_money(equity)}\nWorst scenario: {worst_name} {format_money(worst_val)} ({round((worst_val/equity)*100 if equity>0 else 0,2)}%)\n\n"
    for k, v in scenarios.items():
        msg += f"• {k}: {format_money(v)} ({round((v/equity)*100 if equity>0 else 0,2)}%)\n"
    msg += "\nApproximate monitoring only. It does not block trades."
    return msg[:MAX_TELEGRAM_MESSAGE]

# Validation/reporting labels.


def _v46_label_cleanup(msg: Any) -> str:
    text = str(msg)
    for old in ["v4.5", "V4.5", "v4.4.3", "V4.4.3", "v4.3", "V4.3"]:
        text = text.replace(old, "v4.6" if old.startswith("v") else "V4.6")
    text = text.replace("Core 20 / Growth 50 / SPEC 20 / Crypto 10 / Long VCP 0 / Bear 0 / Options 0", V46_ALLOCATION_LABEL)
    text = text.replace("Core 20 / Growth 55 / SPEC 15 / Crypto 10 / Swing 0", V46_ALLOCATION_LABEL)
    return text


def _V461_OLD_FORMAT_INSTITUTIONAL_STATUS() -> str:  # type: ignore[override]
    return _v46_label_cleanup(_V46_OLD_FORMAT_INSTITUTIONAL_STATUS())[:MAX_TELEGRAM_MESSAGE]

def _V461_OLD_FORMAT_DATAHEALTH_STATUS() -> str:  # type: ignore[override]
    return _v46_label_cleanup(_V46_OLD_FORMAT_DATAHEALTH_STATUS())[:MAX_TELEGRAM_MESSAGE]

def _V461_OLD_FORMAT_BROKERSTATUS() -> str:  # type: ignore[override]
    return _v46_label_cleanup(_V46_OLD_FORMAT_BROKERSTATUS())[:MAX_TELEGRAM_MESSAGE]

def _V461_OLD_FORMAT_BROKERRECONCILE() -> str:  # type: ignore[override]
    return _v46_label_cleanup(_V46_OLD_FORMAT_BROKERRECONCILE())[:MAX_TELEGRAM_MESSAGE]

def _V461_OLD_FORMAT_BROKERSYNCPREVIEW() -> str:  # type: ignore[override]
    return _v46_label_cleanup(_V46_OLD_FORMAT_BROKERSYNCPREVIEW())[:MAX_TELEGRAM_MESSAGE]

# Export and reset integration.

def _V461_OLD_EXPORT_STATE_BUNDLE(prefix: str = "bot_state_export") -> str:  # type: ignore[override]
    zip_path = _V46_OLD_EXPORT_STATE_BUNDLE(prefix=prefix)
    try:
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("swing_alpha_positions.table.json", json.dumps(safe_convert(list(load_swing_alpha_positions().values())), indent=2))
            z.writestr("swing_alpha_trades.table.json", json.dumps(safe_convert(load_swing_alpha_trades()), indent=2))
            z.writestr("swing_alpha_latest_plan.json", json.dumps(safe_convert(load_latest_swing_alpha_signal() or {}), indent=2))
    except Exception as exc:
        print(f"[SWING ALPHA EXPORT WARNING] {exc}")
    return zip_path


def reset_all_paper_state(update_id: Optional[int] = None) -> Tuple[bool, str, Optional[str]]:  # type: ignore[override]
    ok, msg, backup_path = _V46_OLD_RESET_ALL(update_id=update_id)
    with db_tx() as conn:
        conn.execute("DELETE FROM swing_alpha_positions")
        conn.execute("DELETE FROM swing_alpha_trades")
        conn.execute("DELETE FROM swing_alpha_signals")
        conn.execute("DELETE FROM cash_deposits")
    msg = str(msg).replace("setcash", "depositcash")
    return ok, msg + "\nSwing Alpha positions/trades/signals cleared\nCash deposit ledger cleared", backup_path

# Command routing.


# =============================================================================
# V4.6.2 - SWING ALPHA LIVE SIGNAL REPLACEMENT LAYER
# =============================================================================
# Purpose:
# - Replace disabled Long VCP/Bear live tactical scans with Swing Alpha signals.
# - Keep old VCP/Bear internals inert for backward compatibility/export safety.
# - Send actionable Swing Alpha entry/exit alerts during the existing near-close
#   scan window, similar to the prior VCP entry-signal workflow.
# - Keep manual execution and separate Swing Alpha ledger: swingbuy/swingsell.
# - No broker orders are placed in this version.

V461_VERSION = "v4.6.2-deploy-ready-swing-alpha-20-45-15-10-10-monitor"
if os.getenv("ALLOW_STRATEGY_VERSION_OVERRIDE", "0").strip() == "1":
    STRATEGY_VERSION = os.getenv("STRATEGY_VERSION", V461_VERSION)
else:
    STRATEGY_VERSION = V461_VERSION

V461_ALLOCATION_LABEL = "Core 20 / Growth 45 / SPEC 15 / Swing Alpha 10 / Crypto 10 / VCP 0 / Bear 0 / Options 0"
V461_VALIDATION_STRATEGY_LABEL = "v4.6.2 Swing Alpha live-signal replacement + Hybrid Crypto + cost-aware monthly rotation"
V461_KNOWN_LIMITATIONS = [
    "v4.6.2 is aggressive and growth/swing/crypto-led; live drawdowns can exceed historical tests.",
    "Swing Alpha MACD+VAH replaces disabled VCP/Bear live tactical scans but still requires forward testing.",
    "Backtest period is limited and not broker-grade execution simulation.",
    "Crypto permission/gate required; crypto remains inactive until broker permission and signal are valid.",
    "Manual execution and read-only IBKR reconciliation are still required; no broker orders are placed.",
    "Core UCITS fee drag is controlled by cost-aware execution and minimum order rules.",
]

# Force disabled tactical legacy engines to remain inert in live v4.6.2.
V45_LONG_VCP_SIGNAL_ENGINE_ENABLED = False
V45_BEAR_SIGNAL_ENGINE_ENABLED = False
BEAR_SLEEVE_ENABLED = False
try:
    V2_MAX_SIGNALS_PER_SCAN = 0
    V2_ALLOW_VCP = False
    V2_ALLOW_BREAKOUTS = False
    V2_ALLOW_PULLBACKS = False
    V2_ALLOW_MEDIUM = False
    V2_ALLOW_WEAK = False
    BEAR_MAX_SIGNALS_PER_SCAN = 0
except Exception:
    pass

# Swing Alpha auto-signal controls.
SWING_ALPHA_AUTO_SIGNAL_ENABLED = os.getenv("SWING_ALPHA_AUTO_SIGNAL_ENABLED", "1") != "0"
SWING_ALPHA_PUBLIC_SIGNAL_ENABLED = os.getenv("SWING_ALPHA_PUBLIC_SIGNAL_ENABLED", "0") == "1"
SWING_ALPHA_MAX_SIGNALS_PER_SCAN = int(os.getenv("SWING_ALPHA_MAX_SIGNALS_PER_SCAN", str(SWING_ALPHA_MAX_OPEN_POSITIONS)))
SWING_ALPHA_AUTO_SIGNAL_REPEAT_SAME_DAY = os.getenv("SWING_ALPHA_AUTO_SIGNAL_REPEAT_SAME_DAY", "0") == "1"

# Use live synthetic daily candles near close for Swing Alpha scoring. This mirrors
# the old VCP near-close scan behavior instead of using yesterday-only EOD data.
def _v461_score_swing_candidate_live(ticker: str) -> Optional[Dict[str, Any]]:
    df = get_signal_dataframe(ticker, limit=260)
    if df is None or len(df) < 220:
        return None
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    volume = df["Volume"].astype(float)
    price = float(close.iloc[-1])
    if price < SWING_ALPHA_MIN_PRICE:
        return None
    avg_dv = float((close * volume).tail(50).mean())
    if avg_dv < SWING_ALPHA_MIN_AVG_DOLLAR_VOLUME:
        return None
    ema20 = float(_v46_ema(close, 20).iloc[-1])
    ema50 = float(_v46_ema(close, 50).iloc[-1])
    ema200 = float(_v46_ema(close, 200).iloc[-1])
    if not (price > ema50 > ema200):
        return None
    ret20 = pct_change_over(close, 20)
    ret63 = pct_change_over(close, 63)
    ret126 = pct_change_over(close, 126)
    if ret63 is None or ret63 < SWING_ALPHA_MIN_RET63:
        return None
    atr_series = atr(df, 14)
    atr_val = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else 0.0
    if atr_val <= 0:
        return None
    atr_pct = atr_val / price
    if atr_pct <= 0 or atr_pct > SWING_ALPHA_MAX_ATR_PCT:
        return None
    rsi_series = rsi(close, 14)
    rsi_val = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else 50.0
    if rsi_val > SWING_ALPHA_MAX_RSI:
        return None
    hist = _v46_macd_hist(close)
    if len(hist.dropna()) < 5:
        return None
    h0, h1, h2 = float(hist.iloc[-1]), float(hist.iloc[-2]), float(hist.iloc[-3])
    macd_turn = (h0 > h1 and h1 <= h2) or (h0 > 0 and h0 > h1)
    if not macd_turn:
        return None
    # VAH proxy deliberately uses completed bars before current signal bar.
    poc, vah, val = _v46_vah_val_proxy(df.iloc[:-1], window=20)
    if vah is None or price <= float(vah):
        return None
    if price > ema20 * 1.18:
        return None
    stop = max(price - SWING_ALPHA_ATR_STOP_MULT * atr_val, ema20 * 0.985)
    if stop >= price:
        stop = price - SWING_ALPHA_ATR_STOP_MULT * atr_val
    max_entry = price * (1.0 + SWING_ALPHA_ENTRY_BUFFER_PCT)
    score = ((ret63 or 0) * 2.0) + ((ret20 or 0) * 1.2) + ((ret126 or 0) * 0.7) + (h0 * 5.0 / max(price, 1.0)) - (atr_pct * 0.8)
    return {
        "ticker": ticker,
        "price": round(price, 4),
        "signal_price": round(price, 4),
        "max_valid_entry": round(max_entry, 4),
        "stop": round(stop, 4),
        "ema20": round(ema20, 4),
        "ema50": round(ema50, 4),
        "ema200": round(ema200, 4),
        "vah_proxy": round(float(vah), 4),
        "poc_proxy": None if poc is None else round(float(poc), 4),
        "val_proxy": None if val is None else round(float(val), 4),
        "atr": round(atr_val, 4),
        "atr_pct": round(atr_pct * 100, 2),
        "rsi": round(rsi_val, 2),
        "ret20_pct": None if ret20 is None else round(ret20 * 100, 2),
        "ret63_pct": round(ret63 * 100, 2),
        "ret126_pct": None if ret126 is None else round(ret126 * 100, 2),
        "macd_hist": round(h0, 6),
        "score": round(score, 6),
        "cluster": _v46_cluster(ticker),
        "strategy_sleeve": "SWING_ALPHA",
        "setup_type": "macd_vah_reclaim",
        "strategy_version": SWING_ALPHA_STRATEGY_VERSION,
        "signal_date_ny": ny_date_str(),
        "daily_bar_date": pd.to_datetime(df.iloc[-1]["date"]).date().isoformat() if "date" in df.columns else ny_date_str(),
        "exit_params": {
            "ema_exit": "EMA20",
            "atr_stop_mult": SWING_ALPHA_ATR_STOP_MULT,
            "atr_trail_mult": SWING_ALPHA_ATR_TRAIL_MULT,
            "time_stop_days": SWING_ALPHA_TIME_STOP_DAYS,
            "time_stop_min_pct": SWING_ALPHA_TIME_STOP_MIN_PCT,
        },
    }

# Override the v4.6 scorer so manual swingplan and auto scans see the same live-aware data.
_v46_score_swing_candidate = _v461_score_swing_candidate_live  # type: ignore[assignment]

# Clearer labels for Swing Alpha public/private formatting.


def _v461_swing_entry_message(item: Dict[str, Any]) -> str:
    ticker = str(item.get("ticker", "")).upper()
    action = str(item.get("action", "BUY")).upper()
    price = float(item.get("signal_price", item.get("price", 0)) or 0)
    max_entry = float(item.get("max_valid_entry", price) or price)
    stop = float(item.get("stop", 0) or 0)
    dollars = float(item.get("suggested_dollars", item.get("target_value", 0)) or 0)
    qty = 0.0 if price <= 0 else dollars / price
    risk_dollars = max(0.0, (price - stop) * qty) if stop > 0 else 0.0
    equity = float((compute_equity_snapshot_data() or {}).get("equity", 0) or 0)
    risk_pct = (risk_dollars / equity * 100.0) if equity > 0 else 0.0
    return (
        "📈 SWING ALPHA ENTRY SIGNAL v4.6.2\n\n"
        f"🏷️ Ticker: {ticker}\n"
        f"🧬 Sleeve: 🎯 SWING_ALPHA\n"
        f"⚙️ Setup: MACD + VAH Reclaim\n"
        f"📌 Action: {action}\n\n"
        f"🟢 ENTRY / SIGNAL PRICE: {round(price, 4)}\n"
        f"🟡 MAX ENTRY LIMIT: {round(max_entry, 4)}\n"
        f"🔴 STOP / INVALIDATION: {round(stop, 4)}\n"
        f"💵 Suggested notional: {format_money(dollars)}\n"
        f"📦 Estimated shares: {format_core_shares(qty)}\n"
        f"⚠️ Est. trade risk: {format_money(risk_dollars)} ({round(risk_pct, 2)}% equity)\n\n"
        f"📊 RSI: {item.get('rsi')} | ATR%: {item.get('atr_pct')}%\n"
        f"📈 20d: {format_pct(item.get('ret20_pct'))} | 63d: {format_pct(item.get('ret63_pct'))}\n"
        f"📍 VAH proxy: {item.get('vah_proxy')} | POC proxy: {item.get('poc_proxy')}\n"
        f"🏅 Score: {item.get('score')} | Cluster: {item.get('cluster')}\n\n"
        "How to execute after broker fill:\n"
        f"• swingbuy {ticker} ACTUAL_SHARES at ACTUAL_FILL_PRICE\n\n"
        "Rules:\n"
        "• Do not chase above max entry.\n"
        "• Use Swing Alpha ledger only; do not use bought/sold/corebuy/growthbuy/specbuy.\n"
        "• This replaces the disabled VCP/Bear tactical entry path."
    )[:MAX_TELEGRAM_MESSAGE]

def _v461_swing_exit_message(item: Dict[str, Any]) -> str:
    ticker = str(item.get("ticker", "")).upper()
    positions = load_swing_alpha_positions() if SWING_ALPHA_LEDGER_ENABLED else {}
    pos = positions.get(ticker, {})
    shares = float(pos.get("shares", 0) or 0)
    price = float(item.get("price", 0) or 0)
    reason = str(item.get("reason", "Swing Alpha exit rule triggered."))
    return (
        "📉 SWING ALPHA EXIT SIGNAL v4.6.2\n\n"
        f"🏷️ Ticker: {ticker}\n"
        f"🧬 Sleeve: 🎯 SWING_ALPHA\n"
        f"📌 Reason: {reason}\n"
        f"💵 Reference price: {round(price, 4)}\n"
        f"📦 Bot-recorded shares: {format_core_shares(shares)}\n\n"
        "How to execute after broker fill:\n"
        f"• swingsell {ticker} ACTUAL_SHARES at ACTUAL_FILL_PRICE\n\n"
        "Rules:\n"
        "• This is a tactical Swing Alpha exit, not a monthly-rotation rebalance.\n"
        "• Use Swing Alpha ledger only."
    )[:MAX_TELEGRAM_MESSAGE]

def _v461_public_swing_entry(item: Dict[str, Any]) -> str:
    ticker = str(item.get("ticker", "")).upper()
    return (
        "📈 SWING ALPHA ENTRY SIGNAL\n\n"
        f"🏷️ Ticker: {ticker}\n"
        "🧬 Sleeve: Swing Alpha\n"
        "⚙️ Setup: MACD + VAH Reclaim\n\n"
        f"🟢 Entry/reference: {fmt_public_number(item.get('signal_price', item.get('price')))}\n"
        f"🟡 Max entry limit: {fmt_public_number(item.get('max_valid_entry'))}\n"
        f"🔴 Stop/invalidation: {fmt_public_number(item.get('stop'))}\n"
        f"📐 Position guide: about {fmt_public_number(item.get('target_account_pct'), 2)}% of account target sleeve\n\n"
        f"{public_signal_footer()}"
    )[:MAX_TELEGRAM_MESSAGE]

def _v461_public_swing_exit(item: Dict[str, Any]) -> str:
    ticker = str(item.get("ticker", "")).upper()
    return (
        "📉 SWING ALPHA EXIT SIGNAL\n\n"
        f"🏷️ Ticker: {ticker}\n"
        "🧬 Sleeve: Swing Alpha\n"
        f"📌 Reason: {item.get('reason', 'Exit rule triggered')}\n"
        f"💵 Reference price: {fmt_public_number(item.get('price'))}\n\n"
        f"{public_signal_footer()}"
    )[:MAX_TELEGRAM_MESSAGE]


def scan_swing_alpha_market(force: bool = False, verbose: bool = False) -> bool:
    """Run Swing Alpha as the live tactical scan engine."""
    today = ny_date_str()
    expected_bar = expected_daily_bar_date()
    if not force and not SWING_ALPHA_AUTO_SIGNAL_REPEAT_SAME_DAY:
        if get_meta("last_swing_alpha_auto_scan_day") == today:
            print("[V4.6.2 SWING SCAN SKIP] Swing Alpha already scanned today.")
            return True
    if PANIC_MODE:
        print("[V4.6.2 SWING SCAN BLOCKED] PANIC_MODE")
        return True
    if daily_drawdown_exceeded():
        print("[V4.6.2 SWING SCAN BLOCKED] Daily loss limit")
        return True
    guard = portfolio_risk_guard_details()
    if guard.get("block_new_entries"):
        print("[V4.6.2 SWING SCAN BLOCKED] Hard drawdown guard")
        maybe_send_portfolio_risk_guard_alert(guard)
        return True
    try:
        plan = compute_swing_alpha_plan()
        save_swing_alpha_plan(plan)
    except Exception as exc:
        logger.exception(f"[V4.6.2 SWING SCAN ERROR] {exc}")
        send(f"⚠️ Swing Alpha scan failed: {exc}")
        return False

    actions = plan.get("actionable", []) or []
    exits = [a for a in actions if str(a.get("action", "")).upper() in {"SELL", "TRIM"}]
    entries_all = [a for a in actions if str(a.get("action", "")).upper() in {"BUY", "ADD"}]
    positions = load_swing_alpha_positions() if SWING_ALPHA_LEDGER_ENABLED else {}
    open_slots = max(0, SWING_ALPHA_MAX_OPEN_POSITIONS - len(positions))
    entries: List[Dict[str, Any]] = []
    for a in entries_all:
        action = str(a.get("action", "")).upper()
        ticker = str(a.get("ticker", "")).upper()
        if action == "BUY" and ticker not in positions:
            if open_slots <= 0:
                continue
            entries.append(a)
            open_slots -= 1
        else:
            entries.append(a)
        if len(entries) >= SWING_ALPHA_MAX_SIGNALS_PER_SCAN:
            break

    sent = 0
    for item in exits:
        send(_v461_swing_exit_message(item))
        sent += 1
        if PUBLIC_SIGNAL_ENABLED and SWING_ALPHA_PUBLIC_SIGNAL_ENABLED:
            send_public_signal(_v461_public_swing_exit(item))
    for item in entries:
        send(_v461_swing_entry_message(item))
        sent += 1
        if PUBLIC_SIGNAL_ENABLED and SWING_ALPHA_PUBLIC_SIGNAL_ENABLED:
            send_public_signal(_v461_public_swing_entry(item))
    if verbose:
        send(format_swing_alpha_plan(plan))
    print(
        "[V4.6.2 SWING SCAN SUMMARY] "
        f"market_ok={plan.get('market_ok')} scored={plan.get('scored_count')} "
        f"entries={len(entries)} exits={len(exits)} sent={sent}"
    )
    _v461_mark_swing_auto_scan_done(today, expected_bar)
    return True

# Replace the disabled VCP/Bear scan path with Swing Alpha. The legacy VCP/Bear
# code remains inert for compatibility but no longer owns the tactical scan.

def scan_market() -> bool:  # type: ignore[override]
    if SWING_ALPHA_AUTO_SIGNAL_ENABLED and SWING_ALPHA_ENABLED and SWING_ALPHA_ACCOUNT_ALLOC_PCT > 0:
        return scan_swing_alpha_market(force=False, verbose=False)
    print("[V4.6.2 SCAN SKIP] Swing Alpha auto signals disabled and VCP/Bear are disabled.")
    try:
        set_meta("last_scan_day", ny_date_str())
        bar = expected_daily_bar_date()
        if bar:
            set_meta("last_scan_bar_date", bar)
    except Exception:
        pass
    return True

# Remove disabled VCP/Bear clutter from the main allocation text.

def institutional_validation_snapshot() -> Dict[str, Any]:  # type: ignore[override]
    return {
        "strategy_version": STRATEGY_VERSION,
        "strategy": V461_VALIDATION_STRATEGY_LABEL,
        "allocation": V461_ALLOCATION_LABEL,
        "known_limitations": list(V461_KNOWN_LIMITATIONS),
        "live_validation_rules": [
            "Core/Growth/SPEC remain monthly rotation sleeves; daily plans are monitoring unless monthly-lock allows action.",
            "Swing Alpha replaces disabled VCP/Bear live tactical scans and uses its own ledger.",
            "Swing Alpha entries/exits are signal alerts; execute in broker first, then record with swingbuy/swingsell.",
            "Crypto is tactical and separate, using BTC/ETH/SOL hybrid trend logic.",
            "Do not chase above max entry guide.",
            "Use partial-fill commands only for real broker fills already executed.",
            "Use brokerreconcile/brokersyncpreview after manual fills and before any sync apply.",
            "Do not treat external legacy IBKR positions as bot-managed strategy positions.",
            "No broker order automation in v4.6.2; IBKR reconciliation remains read-only.",
        ],
    }


def _v461_label_cleanup(msg: Any) -> str:
    text = str(msg)
    for old in ["v4.6", "V4.6", "v4.5", "V4.5", "v4.4.3", "V4.4.3", "v4.3", "V4.3"]:
        text = text.replace(old, "v4.6.2" if old.startswith("v") else "V4.6.2")
    text = text.replace(V46_ALLOCATION_LABEL, V461_ALLOCATION_LABEL)
    text = text.replace("VCP/Bear/Options disabled.", "VCP/Bear/Options disabled; Swing Alpha owns live tactical signals.")
    return text[:MAX_TELEGRAM_MESSAGE]

# Wrap status/report labels.

def _V463_OLD_FORMAT_INSTITUTIONAL() -> str:  # type: ignore[override]
    return _v461_label_cleanup(_V461_OLD_FORMAT_INSTITUTIONAL_STATUS())

def _V463_OLD_FORMAT_DATAHEALTH() -> str:  # type: ignore[override]
    return _v461_label_cleanup(_V461_OLD_FORMAT_DATAHEALTH_STATUS())

def _V463_OLD_FORMAT_RISK() -> str:  # type: ignore[override]
    return _v461_label_cleanup(_V461_OLD_FORMAT_RISKMATRIX_STATUS())

def _V463_OLD_FORMAT_STRESS() -> str:  # type: ignore[override]
    return _v461_label_cleanup(_V461_OLD_FORMAT_STRESS_STATUS())

def _V463_OLD_FORMAT_BROKERSTATUS() -> str:  # type: ignore[override]
    return _v461_label_cleanup(_V461_OLD_FORMAT_BROKERSTATUS())

def _V463_OLD_FORMAT_BROKERRECONCILE() -> str:  # type: ignore[override]
    return _v461_label_cleanup(_V461_OLD_FORMAT_BROKERRECONCILE())

def _V463_OLD_FORMAT_BROKERSYNCPREVIEW() -> str:  # type: ignore[override]
    return _v461_label_cleanup(_V461_OLD_FORMAT_BROKERSYNCPREVIEW())

# Export metadata for the tactical replacement layer.

def _V463_OLD_EXPORT_STATE_BUNDLE(prefix: str = "bot_state_export") -> str:  # type: ignore[override]
    zip_path = _V461_OLD_EXPORT_STATE_BUNDLE(prefix=prefix)
    try:
        meta = {
            "version": V461_VERSION,
            "allocation": V461_ALLOCATION_LABEL,
            "swing_alpha_auto_signal_enabled": SWING_ALPHA_AUTO_SIGNAL_ENABLED,
            "vcp_live_signal_engine": "disabled",
            "bear_live_signal_engine": "disabled",
            "notes": "v4.6.2 replaces disabled VCP/Bear live tactical scan path with Swing Alpha entry/exit alerts.",
        }
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("v461_tactical_replacement_metadata.json", json.dumps(safe_convert(meta), indent=2))
    except Exception as exc:
        print(f"[V4.6.2 EXPORT WARNING] {exc}")
    return zip_path

# Command routing.

def _v461_status_text() -> str:
    try:
        market_ok, swing_reason = swing_alpha_market_filter_ok()
    except Exception as exc:
        market_ok, swing_reason = False, f"error: {exc}"
    try:
        growth_ok, growth_reason = growth_alpha_market_filter_ok()
    except Exception as exc:
        growth_ok, growth_reason = False, f"error: {exc}"
    try:
        win = _v44_monthly_rebalance_window_info()
    except Exception as exc:
        win = {"open": False, "reason": f"error: {exc}"}
    return (
        "🛠️ V4.6.2 SWING SIGNAL REPLACEMENT STATUS\n\n"
        f"Strategy display: {STRATEGY_VERSION}\n"
        f"Strategy logic: {V461_VALIDATION_STRATEGY_LABEL}\n"
        f"Allocation: {V461_ALLOCATION_LABEL}\n"
        f"IBKR recon enabled: {yes_no(IBKR_RECON_ENABLED)}\n"
        f"Bridge URL configured: {yes_no(bool(IBKR_BRIDGE_URL))}\n"
        f"Growth market filter: {yes_no(growth_ok)} — {growth_reason}\n"
        f"Swing market filter: {yes_no(market_ok)} — {swing_reason}\n"
        f"Swing live signal engine: {yes_no(SWING_ALPHA_AUTO_SIGNAL_ENABLED)}\n"
        f"Monthly-lock enabled: {yes_no(V44_MONTHLY_LOCK_ENABLED)}\n"
        f"Monthly rebalance window: {yes_no(bool(win.get('open')))} — {win.get('reason')}\n\n"
        "Execution controls:\n"
        f"• Core min order: {format_money(V43_CORE_MIN_ORDER_DOLLARS)} | partial-fill recording: {yes_no(V442_ALLOW_PARTIAL_FILL_RECORDING)}\n"
        f"• Growth top-{V43_GROWTH_EXECUTE_TOP_N_SMALL}, min order {format_money(V43_GROWTH_MIN_ORDER_DOLLARS)}, underfill priority {yes_no(V442_UNDERFILL_PRIORITY_ENABLED)}\n"
        f"• SPEC ranks 1-{V43_SPEC_BUY_RANK_LIMIT_SMALL}, max holdings {V43_SPEC_MAX_HOLDINGS_SMALL}, min order {format_money(V43_SPEC_MIN_ORDER_DOLLARS)}\n"
        f"• Swing Alpha auto signals max {SWING_ALPHA_MAX_SIGNALS_PER_SCAN}; max positions {SWING_ALPHA_MAX_OPEN_POSITIONS}; min order {format_money(SWING_ALPHA_MIN_TRADE_DOLLARS)}\n"
        "• Crypto: BTC/ETH/SOL hybrid major trend, 70% daily breakout + 30% 4h compression.\n"
        "• Long VCP/Bear are disabled in live allocation and no longer own the scan path.\n"
        "• Read-only IBKR reconciliation only; no broker orders are placed."
    )[:MAX_TELEGRAM_MESSAGE]


# ---- Swing Alpha live position management / exit alerts ----
def _v461_send_swing_exit_once(ticker: str, reason_key: str, message: str, public_item: Optional[Dict[str, Any]] = None) -> None:
    today = ny_date_str()
    key = f"swing_alpha_exit_alert_{ticker.upper()}_{reason_key}_{today}"
    if get_meta(key) == "1":
        return
    set_meta(key, "1")
    send(message[:MAX_TELEGRAM_MESSAGE])
    if PUBLIC_SIGNAL_ENABLED and SWING_ALPHA_PUBLIC_SIGNAL_ENABLED and public_item is not None:
        send_public_signal(_v461_public_swing_exit(public_item))

def manage_swing_alpha_positions() -> None:
    """Alert-only management for Swing Alpha positions.

    This mirrors the old tactical/VCP idea operationally: the bot can alert an
    exit condition, but the user still executes in the broker and records with
    swingsell. No broker orders are placed here.
    """
    if not (SWING_ALPHA_ENABLED and SWING_ALPHA_LEDGER_ENABLED):
        return
    try:
        positions = load_swing_alpha_positions()
    except Exception:
        return
    if not positions:
        return
    prices = get_prices_batch(list(positions.keys()))
    now = ny_now()
    minutes = now.hour * 60 + now.minute
    near_close = minutes >= (15 * 60 + 45)
    for ticker, pos in positions.items():
        try:
            mark = float(prices.get(ticker, pos.get("avg_entry_price", 0)) or 0)
            if mark <= 0:
                continue
            avg = float(pos.get("avg_entry_price", mark) or mark)
            shares = float(pos.get("shares", 0) or 0)
            if shares <= 0:
                continue
            old_highest = float(pos.get("highest") or avg or mark)
            new_highest = max(old_highest, mark)
            stored_stop = float(pos.get("stop") or 0.0)
            df = get_signal_dataframe(ticker, limit=120) if near_close else get_historical(ticker, limit=120)
            ema20 = None
            atr_val = 0.0
            if df is not None and len(df) >= 50:
                close = df["Close"].astype(float)
                ema20 = float(_v46_ema(close, 20).iloc[-1])
                atr_series = atr(df, 14)
                atr_val = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else 0.0
            trail = new_highest - SWING_ALPHA_ATR_TRAIL_MULT * atr_val if atr_val > 0 else 0.0
            effective_stop = max(stored_stop, trail) if trail > 0 else stored_stop
            exit_key = None
            exit_reason = None
            if stored_stop > 0 and mark <= stored_stop:
                exit_key = "initial_stop"
                exit_reason = "Initial stop hit."
            elif trail > 0 and mark <= trail:
                exit_key = "atr_trail"
                exit_reason = "ATR trailing stop hit."
            elif near_close and ema20 is not None and mark < ema20:
                exit_key = "ema20_exit"
                exit_reason = "Close/mark below EMA20 swing exit."
            else:
                entry_time = float(pos.get("entry_time") or now_ts())
                days_held = max(0.0, (now_ts() - entry_time) / 86400.0)
                gain = (mark / avg - 1.0) if avg > 0 else 0.0
                if near_close and days_held >= SWING_ALPHA_TIME_STOP_DAYS and gain < SWING_ALPHA_TIME_STOP_MIN_PCT:
                    exit_key = "time_stop"
                    exit_reason = f"Time stop: {int(days_held)} days held without +{int(SWING_ALPHA_TIME_STOP_MIN_PCT * 100)}% progress."
            # Persist updated highest/stop so trailing logic is not lost across loops.
            try:
                if new_highest > old_highest or (effective_stop > 0 and effective_stop != stored_stop):
                    with db_tx() as conn:
                        conn.execute(
                            "UPDATE swing_alpha_positions SET highest=?, stop=?, last_update_time=? WHERE ticker=?",
                            (round(new_highest, 6), round(effective_stop, 6) if effective_stop > 0 else stored_stop, now_ts(), ticker),
                        )
            except Exception as exc:
                print(f"[SWING ALPHA MANAGE UPDATE ERROR] {ticker}: {exc}")
            if exit_key and exit_reason:
                pnl_pct = ((mark / avg) - 1.0) * 100.0 if avg > 0 else 0.0
                item = {"ticker": ticker, "price": mark, "reason": exit_reason}
                msg = (
                    "📉 SWING ALPHA EXIT SIGNAL v4.6.2\n\n"
                    f"🏷️ Ticker: {ticker}\n"
                    "🧬 Sleeve: 🎯 SWING_ALPHA\n"
                    f"📌 Reason: {exit_reason}\n"
                    f"📦 Bot-recorded shares: {format_core_shares(shares)}\n"
                    f"💵 Reference exit price: {round(mark, 4)} ({format_pct(pnl_pct)})\n"
                    f"🔴 Stored stop: {round(stored_stop, 4) if stored_stop else 'n/a'}\n"
                    f"📈 Highest: {round(new_highest, 4)} | ATR trail: {round(trail, 4) if trail else 'n/a'}\n\n"
                    "After broker fill, record with:\n"
                    f"• swingsell {ticker} ACTUAL_SHARES at ACTUAL_FILL_PRICE\n\n"
                    "No broker order was placed by the bot."
                )
                _v461_send_swing_exit_once(ticker, exit_key, msg, item)
        except Exception as exc:
            logger.exception(f"[SWING ALPHA MANAGE ERROR] {ticker}: {exc}")


def manage_positions() -> None:  # type: ignore[override]
    # v4.8.1 active-only: legacy VCP/Bear position manager is not called.
    # Swing Alpha is the only tactical stock manager.
    manage_swing_alpha_positions()

# ---- v4.6.2 deployment-readiness cleanup ----
# Keep scanstatus aligned with the new Swing Alpha scan path. v4.6.1 marked
# only last_swing_alpha_auto_scan_day; v4.6.2 also updates the legacy
# last_scan_day / last_scan_bar_date fields used by scanstatus and exports.
def _v461_mark_swing_auto_scan_done(day: str, bar: Optional[str]) -> None:  # type: ignore[override]
    try:
        set_meta("last_swing_alpha_auto_scan_day", day)
        set_meta("last_scan_day", day)
        if bar:
            set_meta("last_swing_alpha_auto_scan_bar", bar)
            set_meta("last_scan_bar_date", bar)
    except Exception:
        pass


# =============================================================================
# V4.6.3 MONTHLY DASHBOARD + CRYPTO AUTO ALERTS
# =============================================================================
# Operational / UX patch only. No strategy scoring, allocation, or execution-rule
# changes. Purpose:
# - Send one private monthly dashboard for Core/Growth/SPEC so the user does not
#   need to remember wealthplan/growthplan/specplan.
# - Send detailed Core/Growth/SPEC plans together from that dashboard once/month.
# - Add Crypto Alpha tactical auto-checks because Crypto is not a monthly sleeve.
# - Keep Swing Alpha as the live tactical scan engine and IBKR read-only.

V463_VERSION = "v4.6.3-monthly-dashboard-crypto-alerts-20-45-15-10-10-monitor"
if os.getenv("ALLOW_STRATEGY_VERSION_OVERRIDE", "0").strip() == "1":
    STRATEGY_VERSION = os.getenv("STRATEGY_VERSION", V463_VERSION)
else:
    STRATEGY_VERSION = V463_VERSION

V463_VALIDATION_STRATEGY_LABEL = "v4.6.3 monthly dashboard + crypto auto alerts over v4.6.2 Swing Alpha/Hybrid Crypto"
V463_ALLOCATION_LABEL = "Core 20 / Growth 45 / SPEC 15 / Swing Alpha 10 / Crypto 10 / VCP 0 / Bear 0 / Options 0"
V463_MONTHLY_DASHBOARD_ENABLED = os.getenv("V463_MONTHLY_DASHBOARD_ENABLED", "1").strip() != "0"
V463_MONTHLY_SEND_DETAILED_PLANS = os.getenv("V463_MONTHLY_SEND_DETAILED_PLANS", "1").strip() != "0"
V463_MONTHLY_REVIEW_AFTER_CLOSE_MINUTE = int(os.getenv("V463_MONTHLY_REVIEW_AFTER_CLOSE_MINUTE", str(16 * 60 + 18)))
V463_MONTHLY_REQUIRE_REBALANCE_WINDOW = os.getenv("V463_MONTHLY_REQUIRE_REBALANCE_WINDOW", "1").strip() != "0"
V463_CRYPTO_AUTO_CHECK_ENABLED = os.getenv("V463_CRYPTO_AUTO_CHECK_ENABLED", "1").strip() != "0"
V463_CRYPTO_AUTO_CHECK_INTERVAL_MIN = int(os.getenv("V463_CRYPTO_AUTO_CHECK_INTERVAL_MIN", "240"))
V463_CRYPTO_DAILY_STATUS_MINUTE = int(os.getenv("V463_CRYPTO_DAILY_STATUS_MINUTE", str(16 * 60 + 25)))
V463_CRYPTO_SEND_NO_ACTION_DAILY = os.getenv("V463_CRYPTO_SEND_NO_ACTION_DAILY", "1").strip() != "0"

def _v463_after_minute(minute: int) -> bool:
    n = ny_now()
    return (n.hour * 60 + n.minute) >= int(minute)

def _v463_plan_actions(plan: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not plan:
        return []
    raw = plan.get("actionable")
    if raw is None:
        raw = plan.get("actions", []) or []
    out: List[Dict[str, Any]] = []
    for item in raw or []:
        if str(item.get("action", "")).upper() in {"BUY", "ADD", "TRIM", "SELL"}:
            out.append(item)
    return out

def _v463_summarize_plan(label: str, plan: Optional[Dict[str, Any]], error: Optional[str] = None) -> str:
    if error:
        return f"• {label}: ⚠️ error — {error}"
    if not plan:
        return f"• {label}: ⚠️ unavailable"
    acts = _v463_plan_actions(plan)
    if not acts:
        return f"• {label}: ✅ no actionable trade now"
    parts: List[str] = []
    for item in acts[:4]:
        action = str(item.get("action", "?")).upper()
        ticker = str(item.get("ticker", "?")).upper()
        amount = item.get("suggested_dollars", item.get("target_value", item.get("current_value", 0)))
        try:
            amount_txt = format_money(float(amount or 0))
        except Exception:
            amount_txt = "n/a"
        parts.append(f"{action} {ticker} ~{amount_txt}")
    if len(acts) > 4:
        parts.append(f"+{len(acts)-4} more")
    return f"• {label}: ⚠️ " + "; ".join(parts)

def _v463_crypto_gate_signature(plan: Dict[str, Any]) -> str:
    gate = plan.get("gate", {}) or {}
    rows = gate.get("rows", []) or []
    row_sig = ",".join(f"{str(r.get('ticker','')).upper()}:{1 if r.get('ok') else 0}" for r in rows)
    return f"btc={1 if gate.get('btc_ok') else 0}|gate2={1 if gate.get('gate2_ok') else 0}|rows={row_sig}"

def _v463_crypto_action_signature(plan: Dict[str, Any]) -> str:
    acts = _v463_plan_actions(plan)
    if not acts:
        return "NO_ACTION"
    parts: List[str] = []
    for item in acts:
        parts.append(
            f"{str(item.get('action','')).upper()}:{str(item.get('ticker','')).upper()}:{str(item.get('sub_strategy',''))}:"
            f"{round(float(item.get('target_value',0) or 0),2)}:{round(float(item.get('current_value',0) or 0),2)}"
        )
    return "|".join(parts)

def _v463_prepare_dashboard_plans() -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Dict[str, str]]:
    errors: Dict[str, str] = {}
    core_plan = growth_plan = spec_plan = crypto_plan = None
    try:
        core_plan = compute_wealth_core_plan()
        save_core_plan_signal(core_plan)
    except Exception as exc:
        logger.exception(f"[V4.6.3 MONTHLY CORE ERROR] {exc}")
        errors["Core"] = str(exc)
    try:
        growth_plan = compute_growth_alpha_plan()
        save_growth_plan_signal(growth_plan)
    except Exception as exc:
        logger.exception(f"[V4.6.3 MONTHLY GROWTH ERROR] {exc}")
        errors["Growth"] = str(exc)
    try:
        spec_plan = compute_spec_alpha_plan()
        save_spec_plan_signal(spec_plan)
    except Exception as exc:
        logger.exception(f"[V4.6.3 MONTHLY SPEC ERROR] {exc}")
        errors["SPEC"] = str(exc)
    try:
        crypto_plan = compute_crypto_alpha_plan()
    except Exception as exc:
        logger.exception(f"[V4.6.3 CRYPTO SUMMARY ERROR] {exc}")
        errors["Crypto"] = str(exc)
    return core_plan, growth_plan, spec_plan, crypto_plan, errors

def _v463_dashboard_text(core_plan: Optional[Dict[str, Any]], growth_plan: Optional[Dict[str, Any]], spec_plan: Optional[Dict[str, Any]], crypto_plan: Optional[Dict[str, Any]], errors: Dict[str, str]) -> str:
    try:
        win = _v44_monthly_rebalance_window_info()
        win_text = f"{yes_no(bool(win.get('open')))} — {win.get('reason')}"
    except Exception as exc:
        win_text = f"n/a — {exc}"
    return (
        "🗓️ MONTHLY ACTION DASHBOARD v4.9.7\n\n"
        "This is the monthly control message. You should not need to manually run wealthplan/growthplan/specplan.\n\n"
        f"🕒 NY time: {ny_now().strftime('%Y-%m-%d %H:%M %Z')}\n"
        f"Monthly window: {win_text}\n"
        f"Allocation: {V463_ALLOCATION_LABEL}\n\n"
        "Monthly rotation sleeves:\n"
        f"{_v463_summarize_plan('Core', core_plan, errors.get('Core'))}\n"
        f"{_v463_summarize_plan('Growth', growth_plan, errors.get('Growth'))}\n"
        f"{_v463_summarize_plan('SPEC', spec_plan, errors.get('SPEC'))}\n\n"
        "Tactical sleeves:\n"
        "• Swing Alpha: automatic near-close entries/exits; record with swingbuy/swingsell after broker fill.\n"
        f"{_v463_summarize_plan('Crypto', crypto_plan, errors.get('Crypto'))}\n\n"
        "Rules:\n"
        "• Core/Growth/SPEC are monthly sleeves; daily checks are monitoring.\n"
        "• Crypto is tactical, not monthly. It has separate auto checks.\n"
        "• Execute in IBKR first, then record using the correct ledger command.\n"
        "• IBKR remains read-only; no orders are placed by the bot.\n"
        + ("\nDetailed Core/Growth/SPEC plans follow. Crypto is action-only and is not included as a monthly plan." if V463_MONTHLY_SEND_DETAILED_PLANS else "")
    )[:MAX_TELEGRAM_MESSAGE]

def _V482_OLD_SEND_V463_MONTHLY_DASHBOARD(force: bool = False, preview_only: bool = False) -> bool:
    if not (V463_MONTHLY_DASHBOARD_ENABLED or force):
        return False
    n = ny_now()
    if not force:
        if is_market_weekday(n) and not _v463_after_minute(V463_MONTHLY_REVIEW_AFTER_CLOSE_MINUTE):
            return False
        if V463_MONTHLY_REQUIRE_REBALANCE_WINDOW:
            try:
                if not bool(_v44_monthly_rebalance_window_info().get("open")):
                    return False
            except Exception:
                return False
        month_key = n.strftime("%Y-%m")
        if get_meta("last_v463_monthly_dashboard_month") == month_key:
            return False
    else:
        month_key = n.strftime("%Y-%m")
    core_plan, growth_plan, spec_plan, crypto_plan, errors = _v463_prepare_dashboard_plans()
    send(_v463_dashboard_text(core_plan, growth_plan, spec_plan, crypto_plan, errors))
    if V463_MONTHLY_SEND_DETAILED_PLANS:
        if core_plan is not None:
            send(format_wealth_core_plan(core_plan))
        if growth_plan is not None:
            send(format_growth_alpha_plan(growth_plan))
        if spec_plan is not None:
            send(format_spec_alpha_plan(spec_plan))
        # v4.9.7: do not send the full Crypto plan as part of the monthly dashboard.
        # Crypto is tactical and sends a private/public alert only when actionable
        # through maybe_send_crypto_alpha_auto_signal(), or when manually forced.
    if not preview_only:
        set_meta("last_v463_monthly_dashboard_month", month_key)
        set_meta("last_v463_monthly_dashboard_ts", str(now_ts()))
        # Mark old monthly keys to stop older wrappers from later sending a partial/single sleeve plan.
        set_meta("last_wealth_core_month", month_key)
        set_meta("last_wealth_core_alert_ts", str(now_ts()))
        set_meta("last_growth_alpha_month", month_key)
        set_meta("last_growth_alpha_alert_ts", str(now_ts()))
        set_meta("last_spec_alpha_month", month_key)
        set_meta("last_spec_alpha_alert_ts", str(now_ts()))
        audit("V463_MONTHLY_DASHBOARD", f"month={month_key} errors={list(errors.keys())}")
    return True



# =============================================================================
# V4.9.7 MONTHLY-SLEEVE CRITICAL EXIT WATCH
# =============================================================================
# Alert/routing only. This does not place broker orders and does not change
# Core/Growth/SPEC scoring, monthly lock, allocation, ledgers, or IBKR behavior.
# It only warns after close when existing risk/allocation guards imply a hard
# exit/reduction for monthly sleeves, so the user does not need to run plans
# manually to notice a critical condition.

V497_MONTHLY_SLEEVE_CRITICAL_EXIT_MONITOR_ENABLED = os.getenv(
    "V497_MONTHLY_SLEEVE_CRITICAL_EXIT_MONITOR_ENABLED", "1"
).strip() != "0"
V497_MONTHLY_SLEEVE_CRITICAL_EXIT_AFTER_CLOSE_MINUTE = int(
    os.getenv("V497_MONTHLY_SLEEVE_CRITICAL_EXIT_AFTER_CLOSE_MINUTE", str(16 * 60 + 8))
)


def _v497_sleeve_rows_for_critical_watch(sleeve: str, target_pct: float) -> List[Dict[str, Any]]:
    sleeve_u = str(sleeve).upper()
    if sleeve_u == "CORE":
        positions = load_core_positions() if CORE_LEDGER_ENABLED else {}
        sell_cmd = "coresell"
    elif sleeve_u == "GROWTH":
        positions = load_growth_positions() if GROWTH_ALPHA_LEDGER_ENABLED else {}
        sell_cmd = "growthsell"
    elif sleeve_u == "SPEC":
        positions = load_spec_positions() if SPEC_ALPHA_LEDGER_ENABLED else {}
        sell_cmd = "specsell"
    else:
        return []
    if not positions:
        return []
    prices = get_prices_batch(list(positions.keys()))
    rows: List[Dict[str, Any]] = []
    for ticker, pos in sorted(positions.items()):
        shares = float(pos.get("shares", 0) or 0)
        if shares <= 0:
            continue
        avg = float(pos.get("avg_entry_price", 0) or 0)
        mark = float(prices.get(ticker, avg) or avg)
        value = shares * mark if mark > 0 else 0.0
        pnl_pct = ((mark / avg) - 1.0) * 100.0 if avg > 0 and mark > 0 else None
        rows.append({
            "sleeve": sleeve_u,
            "ticker": str(ticker).upper(),
            "shares": shares,
            "mark": mark,
            "avg": avg,
            "value": value,
            "pnl_pct": pnl_pct,
            "target_pct": target_pct,
            "command": sell_cmd,
        })
    return rows


def maybe_send_monthly_sleeve_critical_exit_signal(force: bool = False) -> bool:
    if not (V497_MONTHLY_SLEEVE_CRITICAL_EXIT_MONITOR_ENABLED or force):
        return False
    n = ny_now()
    if not force:
        if not is_market_weekday(n):
            return False
        minutes = n.hour * 60 + n.minute
        if minutes < V497_MONTHLY_SLEEVE_CRITICAL_EXIT_AFTER_CLOSE_MINUTE:
            return False
        today = n.date().isoformat()
        if get_meta("last_v497_monthly_sleeve_critical_exit_check_day") == today:
            return False
    else:
        today = n.date().isoformat()

    try:
        allocation = dynamic_portfolio_allocation_targets()
        risk = allocation.get("risk_guard", {}) or {}
        hard_active = bool(risk.get("hard_active")) or bool(risk.get("block_new_entries"))
        market = str(allocation.get("market", market_condition()))
        targets = {
            "CORE": float(allocation.get("core_wealth_pct", 0) or 0),
            "GROWTH": float(allocation.get("growth_alpha_pct", 0) or 0),
            "SPEC": float(allocation.get("spec_alpha_pct", 0) or 0),
        }
        reasons: List[str] = []
        if hard_active:
            reasons.append("portfolio hard-risk guard is active")
        zero_sleeves = [s for s, pct in targets.items() if pct <= 0]
        if zero_sleeves:
            reasons.append("target allocation is 0% for " + ", ".join(zero_sleeves))
        if not reasons:
            if not force:
                set_meta("last_v497_monthly_sleeve_critical_exit_check_day", today)
            return False

        rows: List[Dict[str, Any]] = []
        for sleeve, target_pct in targets.items():
            if hard_active or target_pct <= 0:
                rows.extend(_v497_sleeve_rows_for_critical_watch(sleeve, target_pct))
        if not rows:
            if not force:
                set_meta("last_v497_monthly_sleeve_critical_exit_check_day", today)
            return False

        signature = "|".join(f"{r['sleeve']}:{r['ticker']}:{round(float(r['value']), 2)}" for r in rows)
        if not force and get_meta("last_v497_monthly_sleeve_critical_exit_signature") == signature:
            set_meta("last_v497_monthly_sleeve_critical_exit_check_day", today)
            return False

        msg = (
            "🚨 CORE/GROWTH/SPEC CRITICAL EXIT WATCH v4.9.7\n\n"
            "This is a hard-risk/allocation alert, not routine monthly rotation churn.\n\n"
            f"🕒 NY time: {ny_now().strftime('%Y-%m-%d %H:%M %Z')}\n"
            f"🌎 Market regime: {market_label(market)}\n"
            f"🛡️ Reason: {'; '.join(reasons)}\n\n"
        )
        for row in rows[:18]:
            pnl = format_pct(row.get("pnl_pct")) if row.get("pnl_pct") is not None else "n/a"
            msg += (
                f"🔴 {row['sleeve']} SELL WATCH — {row['ticker']}\n"
                f"📦 Bot-recorded shares: {format_core_shares(row['shares'])}\n"
                f"💵 Reference price: {round(float(row['mark']), 4)} | P/L: {pnl}\n"
                f"💼 Value: {format_money(float(row['value']))} | Target now: {round(float(row['target_pct']), 2)}%\n"
                f"After broker fill: {row['command']} {row['ticker']} ACTUAL_SHARES at ACTUAL_FILL_PRICE\n\n"
            )
        if len(rows) > 18:
            msg += f"+{len(rows) - 18} more positions. Run portfolio/sleevestatus for details.\n\n"
        msg += "🤖 No broker order was placed by the bot."
        send(msg[:MAX_TELEGRAM_MESSAGE])
        set_meta("last_v497_monthly_sleeve_critical_exit_check_day", today)
        set_meta("last_v497_monthly_sleeve_critical_exit_signature", signature)
        audit("V497_MONTHLY_SLEEVE_CRITICAL_EXIT", f"market={market} rows={len(rows)} reasons={reasons}")
        return True
    except Exception as exc:
        logger.exception(f"[V4.9.7 MONTHLY SLEEVE CRITICAL EXIT ERROR] {exc}")
        if force:
            send(f"⚠️ Monthly-sleeve critical exit watch failed: {exc}")
        return False

# Replace the chained monthly hook with a unified user-friendly hook.
def maybe_send_wealth_core_signal() -> None:  # type: ignore[override]
    try:
        send_v463_monthly_dashboard(force=False, preview_only=False)
    except Exception as exc:
        logger.exception(f"[V4.6.3 MONTHLY DASHBOARD ERROR] {exc}")
    try:
        maybe_send_monthly_sleeve_critical_exit_signal(force=False)
    except Exception as exc:
        logger.exception(f"[V4.9.7 MONTHLY SLEEVE CRITICAL EXIT ERROR] {exc}")
    try:
        maybe_send_crypto_alpha_auto_signal(force=False)
    except Exception as exc:
        logger.exception(f"[V4.9.7 CRYPTO AUTO ERROR] {exc}")
    try:
        maybe_send_ibkr_reconcile_after_close()
    except Exception as exc:
        logger.exception(f"[V4.6.3 IBKR AUTO ERROR] {exc}")


def _V47_OLD_FORMAT_INSTITUTIONAL() -> str:  # type: ignore[override]
    return _v463_label_cleanup(_V463_OLD_FORMAT_INSTITUTIONAL())

def _V47_OLD_FORMAT_DATAHEALTH() -> str:  # type: ignore[override]
    return _v463_label_cleanup(_V463_OLD_FORMAT_DATAHEALTH())

def _V47_OLD_FORMAT_RISK() -> str:  # type: ignore[override]
    return _v463_label_cleanup(_V463_OLD_FORMAT_RISK())

def _V47_OLD_FORMAT_STRESS() -> str:  # type: ignore[override]
    return _v463_label_cleanup(_V463_OLD_FORMAT_STRESS())

def _V47_OLD_FORMAT_BROKERSTATUS() -> str:  # type: ignore[override]
    return _v463_label_cleanup(_V463_OLD_FORMAT_BROKERSTATUS())

def _V47_OLD_FORMAT_BROKERRECONCILE() -> str:  # type: ignore[override]
    return _v463_label_cleanup(_V463_OLD_FORMAT_BROKERRECONCILE())

def _V47_OLD_FORMAT_BROKERSYNCPREVIEW() -> str:  # type: ignore[override]
    return _v463_label_cleanup(_V463_OLD_FORMAT_BROKERSYNCPREVIEW())

def _v463_status_text() -> str:
    try:
        win = _v44_monthly_rebalance_window_info()
        win_txt = f"{yes_no(bool(win.get('open')))} — {win.get('reason')}"
    except Exception:
        win_txt = "n/a"
    try:
        cp = compute_crypto_alpha_plan()
        crypto_actions = len(_v463_plan_actions(cp))
        gate = cp.get("gate", {}) or {}
        crypto_txt = f"BTC gate {yes_no(bool(gate.get('btc_ok')))}, 4h gate {yes_no(bool(gate.get('gate2_ok')))}, actions {crypto_actions}"
    except Exception as exc:
        crypto_txt = f"error: {exc}"
    return (
        "🛠️ V4.6.3 MONTHLY DASHBOARD + CRYPTO ALERT STATUS\n\n"
        f"Strategy display: {STRATEGY_VERSION}\n"
        f"Strategy logic: {V463_VALIDATION_STRATEGY_LABEL}\n"
        f"Allocation: {V463_ALLOCATION_LABEL}\n"
        f"Monthly dashboard enabled: {yes_no(V463_MONTHLY_DASHBOARD_ENABLED)}\n"
        f"Monthly detailed plans: {yes_no(V463_MONTHLY_SEND_DETAILED_PLANS)}\n"
        f"Monthly window: {win_txt}\n"
        f"Last monthly dashboard: {get_meta('last_v463_monthly_dashboard_month', 'None')}\n\n"
        f"Crypto auto-check enabled: {yes_no(V463_CRYPTO_AUTO_CHECK_ENABLED)}\n"
        f"Crypto check interval: {V463_CRYPTO_AUTO_CHECK_INTERVAL_MIN} minutes\n"
        f"Crypto daily status: {yes_no(V463_CRYPTO_SEND_NO_ACTION_DAILY)} after minute {V463_CRYPTO_DAILY_STATUS_MINUTE}\n"
        f"Crypto current check: {crypto_txt}\n\n"
        "Commands:\n"
        "• monthlydashboard — force monthly dashboard preview now\n"
        "• cryptocheck — force crypto tactical check now\n"
        "• cryptoplan — manual crypto full plan\n"
        "• swingstatus/swingplan — Swing Alpha status/preview\n\n"
        "No broker orders are placed. IBKR reconciliation remains read-only."
    )[:MAX_TELEGRAM_MESSAGE]


def _V47_OLD_EXPORT_STATE_BUNDLE(prefix: str = "bot_state_export") -> str:  # type: ignore[override]
    zip_path = _V463_OLD_EXPORT_STATE_BUNDLE(prefix=prefix)
    try:
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("v463_monthly_dashboard_crypto_alerts.json", json.dumps(safe_convert({
                "version": V463_VERSION,
                "strategy_logic": V463_VALIDATION_STRATEGY_LABEL,
                "allocation": V463_ALLOCATION_LABEL,
                "monthly_dashboard_enabled": V463_MONTHLY_DASHBOARD_ENABLED,
                "monthly_send_detailed_plans": V463_MONTHLY_SEND_DETAILED_PLANS,
                "crypto_auto_check_enabled": V463_CRYPTO_AUTO_CHECK_ENABLED,
                "crypto_check_interval_min": V463_CRYPTO_AUTO_CHECK_INTERVAL_MIN,
                "crypto_daily_status_enabled": V463_CRYPTO_SEND_NO_ACTION_DAILY,
                "notes": "v4.6.3 adds a monthly dashboard and crypto tactical auto alerts. No strategy scoring changed; no broker orders are placed.",
            }), indent=2))
    except Exception as exc:
        print(f"[V4.6.3 EXPORT WARNING] {exc}")
    return zip_path

# ---- v4.6.3 final label/user-facing cleanup ----
# Keep crypto daily status closer to U.S. after-close by default; the 4h/action
# checks are still throttled separately by V463_CRYPTO_AUTO_CHECK_INTERVAL_MIN.
V463_CRYPTO_DAILY_STATUS_MINUTE = int(os.getenv("V463_CRYPTO_DAILY_STATUS_MINUTE", str(16 * 60 + 30)))

def _v463_label_cleanup(text: Any) -> str:  # type: ignore[override]
    out = str(text)
    for old, new in [
        ("v4.6.2", "v4.6.3"), ("V4.6.2", "V4.6.3"),
        ("v4.6.1", "v4.6.3"), ("V4.6.1", "V4.6.3"),
        ("v4.6 —", "v4.6.3 —"), ("v4.6 -", "v4.6.3 -"),
        ("v4.5 —", "v4.6.3 —"), ("v4.5 -", "v4.6.3 -"),
        ("v4.5:", "v4.6.3:"), (" v4.5", " v4.6.3"),
        ("v4.4.2", "v4.6.3"), ("V4.4.2", "V4.6.3"),
        ("v4.3", "v4.6.3"), ("V4.3", "V4.6.3"),
    ]:
        out = out.replace(old, new)
    out = out.replace("default buys use cheaper major crypto names", "uses BTC/ETH/SOL hybrid major-trend candidates")
    out = out.replace("BTC/ETH/SOL are indicators;", "BTC/ETH/SOL are both indicators and tradable candidates;")
    try:
        out = out.replace(V461_VALIDATION_STRATEGY_LABEL, V463_VALIDATION_STRATEGY_LABEL)
        out = out.replace(V461_ALLOCATION_LABEL, V463_ALLOCATION_LABEL)
    except Exception:
        pass
    return out[:MAX_TELEGRAM_MESSAGE]


def format_wealth_core_plan(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    return _v463_label_cleanup(_V463_FINAL_OLD_CORE_PLAN_FORMAT(plan))

def format_growth_alpha_plan(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    return _v463_label_cleanup(_V463_FINAL_OLD_GROWTH_PLAN_FORMAT(plan))

def format_spec_alpha_plan(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    return _v463_label_cleanup(_V463_FINAL_OLD_SPEC_PLAN_FORMAT(plan))

def _V483_FORMAT_CRYPTO_BASE(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    return _v463_label_cleanup(_V463_FINAL_OLD_CRYPTO_PLAN_FORMAT(plan))

def format_swing_alpha_plan(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    return _v463_label_cleanup(_V463_FINAL_OLD_SWING_PLAN_FORMAT(plan))

def _v463_cryptostatus_text() -> str:
    alloc = dynamic_portfolio_allocation_targets()
    latest = load_latest_crypto_signal()
    details = crypto_position_market_value_details()
    gate = crypto_indicator_gate()
    return (
        "🪙 CRYPTO_ALPHA STATUS v4.6.3\n\n"
        f"Enabled: {yes_no(CRYPTO_ALPHA_ENABLED)}\n"
        f"Ledger enabled: {yes_no(CRYPTO_ALPHA_LEDGER_ENABLED)}\n"
        f"Auto alerts: {yes_no(V463_CRYPTO_AUTO_CHECK_ENABLED)} | interval {V463_CRYPTO_AUTO_CHECK_INTERVAL_MIN} min\n"
        f"Target now: {alloc.get('crypto_alpha_pct')}% of account\n"
        "Strategy: BTC/ETH/SOL hybrid major trend — 70% daily breakout + 30% 4h compression\n"
        f"Universe: {', '.join(CRYPTO_ALPHA_UNIVERSE)}\n"
        f"Daily BTC gate: {yes_no(bool(gate.get('btc_ok') or gate.get('ok')))}\n"
        f"4h gate: {yes_no(bool(gate.get('gate2_ok')))} ({gate.get('ok_count')}/{len(CRYPTO_ALPHA_INDICATORS)} above MA200; required 2)\n"
        f"Crypto value: {format_money(float(details.get('value', 0) or 0))}\n"
        f"Active plan: {None if latest is None else latest.get('plan_date')}\n\n"
        "Commands:\n"
        "cryptoplan — full tactical crypto plan now\n"
        "cryptocheck — force the auto-check logic now\n"
        "cryptobuy TICKER UNITS at PRICE\n"
        "cryptosell TICKER UNITS at PRICE\n"
        "cryptoportfolio | cryptopnl | cryptoexposure"
    )[:MAX_TELEGRAM_MESSAGE]


# =============================================================================
# V4.7.1 - LIVE OUTPUT CLEANUP / ACTIVE-SLEEVE PORTFOLIO FIX
# =============================================================================
# Purpose:
# - Fix portfolio header so Swing Alpha is included in the top summary.
# - Remove disabled VCP/Bear/Options from live-facing allocation/equity/status outputs.
# - Keep legacy DB/schema/helpers only for historical exports and backward-compatible
#   state handling. No scoring/allocation/entry/exit logic changed.

V47_VERSION = "v4.7.1-active-sleeve-cleanup-compact-20-45-15-10-10-monitor"
if os.getenv("ALLOW_STRATEGY_VERSION_OVERRIDE", "0").strip() != "1":
    STRATEGY_VERSION = V47_VERSION

V47_STRATEGY_LOGIC_LABEL = "v4.7.1 active-sleeve cleanup over v4.6.3 monthly dashboard + crypto alerts"
V47_ALLOCATION_LABEL = "Core 20 / Growth 45 / SPEC 15 / Swing Alpha 10 / Crypto 10"

def _v47_label_cleanup(text: Any) -> str:
    out = str(text)
    replacements = [
        ("v4.6.3", "v4.7"), ("V4.6.3", "V4.7.1"),
        ("v4.6.2", "v4.7"), ("V4.6.2", "V4.7.1"),
        ("v4.6.1", "v4.7"), ("V4.6.1", "V4.7.1"),
        ("v4.5", "v4.7"), ("V4.5", "V4.7.1"),
    ]
    for old, new in replacements:
        out = out.replace(old, new)
    return out[:MAX_TELEGRAM_MESSAGE]

def _v47_rows_section(title: str, rows: List[Dict[str, Any]], include_stop: bool = False) -> str:
    if not rows:
        return ""
    msg = f"\n{title}\n\n"
    for row in rows:
        try:
            ticker = str(row.get("ticker", "?"))
            shares = format_core_shares(row.get("shares", row.get("units", 0)))
            avg = float(row.get("avg_entry_price", 0) or 0)
            mark = float(row.get("mark_price", avg) or avg)
            mv = float(row.get("market_value", 0) or 0)
            pnl = float(row.get("unrealized_profit", 0) or 0)
            pct = row.get("unrealized_pct")
            msg += (
                f"📦 {ticker}\n"
                f"Shares: {shares}\n"
                f"Avg: {round(avg, 4)} | Now: {round(mark, 4)}\n"
                f"Value: {format_money(mv)}\n"
                f"P/L: {format_money(pnl)} ({format_pct(pct)})\n"
            )
            if include_stop and row.get("stop") is not None:
                msg += f"Stop: {round(float(row.get('stop')), 4)} | High: {round(float(row.get('highest') or mark), 4)}\n"
            msg += "\n"
        except Exception as exc:
            msg += f"📦 {row.get('ticker', '?')} — row format error: {exc}\n\n"
    return msg

def _V48_OLD_COMBINED_PORTFOLIO_REPORT() -> str:  # type: ignore[override]
    snapshot = compute_equity_snapshot_data()
    cash = float(snapshot.get("cash", 0) or 0)
    legacy_value = float(snapshot.get("swing_positions_value", 0) or 0)
    core_value = float(snapshot.get("core_positions_value", 0) or 0)
    growth_value = float(snapshot.get("growth_alpha_positions_value", 0) or 0)
    spec_value = float(snapshot.get("spec_positions_value", 0) or 0)
    swing_alpha_value = float(snapshot.get("swing_alpha_positions_value", 0) or 0)
    crypto_value = float(snapshot.get("crypto_alpha_positions_value", 0) or 0)
    total_positions = float(snapshot.get("positions_value", 0) or 0)
    equity = float(snapshot.get("equity", 0) or 0)

    msg = (
        "PORTFOLIO v4.9.7\n\n"
        f"Cash: {format_money(cash)}\n"
        f"➕ Deposited cash: {format_money(snapshot.get('cash_deposited', 0))}\n"
        f"➖ Withdrawn cash: {format_money(snapshot.get('cash_withdrawn', 0))}\n"
        f"Net external cash: {format_money(snapshot.get('net_external_cash', 0))}\n"
        f"Core value: {format_money(core_value)}\n"
        f"Growth Alpha value: {format_money(growth_value)}\n"
        f"SPEC_ALPHA value: {format_money(spec_value)}\n"
        f"Swing Alpha value: {format_money(swing_alpha_value)}\n"
        f"Crypto Alpha value: {format_money(crypto_value)}\n"
        f"Total active bot positions: {format_money(total_positions)}\n"
        f"Total equity: {format_money(equity)}\n"
    )
    if legacy_value > 0.01:
        msg += f"\nLegacy tactical value: {format_money(legacy_value)} - inactive compatibility ledger; use cleanup/manual review.\n"
    msg += "\n"

    try:
        msg += _v47_rows_section("CORE WEALTH POSITIONS", core_position_market_value_details().get("rows", []))
    except Exception as exc:
        msg += f"\nCORE WEALTH POSITIONS\nCore section error: {exc}\n\n"
    try:
        msg += _v47_rows_section("GROWTH_ALPHA POSITIONS", growth_position_market_value_details().get("rows", []))
    except Exception as exc:
        msg += f"\nGROWTH_ALPHA POSITIONS\nGrowth section error: {exc}\n\n"
    try:
        msg += _v47_rows_section("SPEC_ALPHA POSITIONS", spec_position_market_value_details().get("rows", []))
    except Exception as exc:
        msg += f"\nSPEC_ALPHA POSITIONS\nSPEC section error: {exc}\n\n"
    try:
        msg += _v47_rows_section("SWING_ALPHA POSITIONS", swing_alpha_position_market_value_details().get("rows", []), include_stop=True)
    except Exception as exc:
        msg += f"\nSWING_ALPHA POSITIONS\nSwing Alpha section error: {exc}\n\n"
    try:
        msg += _v47_rows_section("CRYPTO_ALPHA POSITIONS", crypto_position_market_value_details().get("rows", []))
    except Exception as exc:
        msg += f"\nCRYPTO_ALPHA POSITIONS\nCrypto section error: {exc}\n\n"

    if total_positions <= 0.01:
        msg += "No open active bot-managed positions.\n"
    return msg


def open_risk_details() -> Dict[str, float]:  # type: ignore[override]
    details = _V47_OLD_OPEN_RISK_DETAILS()
    snapshot = compute_equity_snapshot_data()
    equity = float(snapshot.get("equity", 0) or 0)
    swing_initial = 0.0
    swing_current = 0.0
    try:
        rows = swing_alpha_position_market_value_details().get("rows", []) if SWING_ALPHA_LEDGER_ENABLED else []
        for row in rows:
            shares = float(row.get("shares", 0) or 0)
            avg = float(row.get("avg_entry_price", 0) or 0)
            mark = float(row.get("mark_price", avg) or avg)
            stop = row.get("stop")
            if stop is None:
                continue
            stop_f = float(stop)
            swing_initial += max(0.0, avg - stop_f) * shares
            swing_current += max(0.0, mark - stop_f) * shares
    except Exception:
        pass
    old_initial = float(details.get("initial_risk_dollars", 0) or 0)
    old_current = float(details.get("current_stop_risk_dollars", 0) or 0)
    total_initial = old_initial + swing_initial
    total_current = old_current + swing_current
    details["equity"] = round(equity, 2)
    details["initial_risk_dollars"] = round(total_initial, 2)
    details["current_stop_risk_dollars"] = round(total_current, 2)
    details["initial_risk_pct"] = 0.0 if equity <= 0 else total_initial / equity
    details["current_stop_risk_pct"] = 0.0 if equity <= 0 else total_current / equity
    details["swing_alpha_risk_dollars"] = round(swing_current, 2)
    details["swing_alpha_value"] = float(snapshot.get("swing_alpha_positions_value", 0) or 0)
    return details

def _v47_equity_text() -> str:
    snapshot = compute_equity_snapshot_data()
    legacy_value = float(snapshot.get("swing_positions_value", 0) or 0)
    lines = [
        "ACCOUNT EQUITY v4.9.7",
        "",
        f"Cash: {format_money(snapshot['cash'])}",
        f"Deposited cash: {format_money(snapshot.get('cash_deposited', 0))}",
        f"Withdrawn cash: {format_money(snapshot.get('cash_withdrawn', 0))}",
        f"Net external cash: {format_money(snapshot.get('net_external_cash', 0))}",
        f"Performance base capital: {format_money(snapshot.get('performance_base_capital', get_performance_base_capital()))}",
        f"Core wealth positions: {format_money(snapshot.get('core_positions_value', 0))}",
        f"Growth Alpha positions: {format_money(snapshot.get('growth_alpha_positions_value', 0))}",
        f"SPEC_ALPHA positions: {format_money(snapshot.get('spec_positions_value', 0))}",
        f"Swing Alpha positions: {format_money(snapshot.get('swing_alpha_positions_value', 0))}",
        f"Crypto Alpha positions: {format_money(snapshot.get('crypto_alpha_positions_value', 0))}",
        f"Total active bot positions: {format_money(snapshot['positions_value'])}",
        f"Total Equity: {format_money(snapshot['equity'])}",
    ]
    if legacy_value > 0.01:
        lines.insert(7, f"Legacy tactical positions: {format_money(legacy_value)}")
    return "\n".join(lines)[:MAX_TELEGRAM_MESSAGE]

def _v47_openrisk_text() -> str:
    details = open_risk_details()
    return (
        "🛡️ OPEN RISK v4.7.1\n\n"
        f"💼 Equity: {format_money(details['equity'])}\n"
        f"⚠️ Initial open risk: {format_money(details['initial_risk_dollars'])} "
        f"({round(details['initial_risk_pct'] * 100, 2)}%)\n"
        f"📉 Current stop risk: {format_money(details['current_stop_risk_dollars'])} "
        f"({round(details['current_stop_risk_pct'] * 100, 2)}%)\n"
        f"🎯 Swing Alpha stop risk: {format_money(details.get('swing_alpha_risk_dollars', 0))}\n"
        f"🚦 Max allowed: {round(MAX_TOTAL_RISK * 100, 2)}%"
    )[:MAX_TELEGRAM_MESSAGE]

def _v47_scanstatus_text() -> str:
    refresh_portfolio()
    last_scan_day = get_meta("last_scan_day")
    last_scan_bar = get_meta("last_scan_bar_date")
    details = open_risk_details()
    legacy_count = len(portfolio.get("positions", {}) or {})
    try:
        swing_count = len(load_swing_alpha_positions()) if SWING_ALPHA_LEDGER_ENABLED else 0
    except Exception:
        swing_count = 0
    return (
        "🧭 SCAN STATUS v4.7.1\n\n"
        f"🕒 NY time: {ny_now().strftime('%Y-%m-%d %H:%M %Z')}\n"
        f"📅 Last tactical scan day/bar: {last_scan_day} / {last_scan_bar}\n"
        f"🎯 Swing Alpha positions: {swing_count}/{SWING_ALPHA_MAX_OPEN_POSITIONS}\n"
        f"💵 Cash: {format_money(portfolio['cash'])}\n"
        f"💼 Equity: {format_money(details['equity'])}\n"
        f"⚠️ Initial open risk: {round(details['initial_risk_pct'] * 100, 2)}%\n"
        f"🛡️ Current stop risk: {round(details['current_stop_risk_pct'] * 100, 2)}%\n"
        f"🕯️ Fresh candle required: {yes_no(REQUIRE_FRESH_DAILY_CANDLE)}\n"
        f"🚨 Panic mode: {yes_no(PANIC_MODE)}"
        + (f"\n⚠️ Legacy inactive tactical positions: {legacy_count}" if legacy_count else "")
    )[:MAX_TELEGRAM_MESSAGE]

def _v47_status_text() -> str:
    try:
        win = _v44_monthly_rebalance_window_info()
        win_txt = f"{yes_no(bool(win.get('open')))} — {win.get('reason')}"
    except Exception:
        win_txt = "n/a"
    return (
        "🛠️ V4.7.1 ACTIVE-SLEEVE CLEANUP STATUS\n\n"
        f"Strategy display: {STRATEGY_VERSION}\n"
        "Strategy logic: v4.6.3 monthly dashboard + crypto alerts + Swing Alpha live signals\n"
        f"Allocation: {V47_ALLOCATION_LABEL}\n"
        f"Monthly dashboard enabled: {yes_no(V463_MONTHLY_DASHBOARD_ENABLED)}\n"
        f"Crypto auto-check enabled: {yes_no(V463_CRYPTO_AUTO_CHECK_ENABLED)}\n"
        f"Swing Alpha live signals: {yes_no(SWING_ALPHA_AUTO_SIGNAL_ENABLED)}\n"
        f"Monthly rebalance window: {win_txt}\n\n"
        "Live active sleeves:\n"
        "• Core monthly rotation\n"
        "• Growth Alpha monthly rotation\n"
        "• SPEC_ALPHA monthly rotation\n"
        "• Swing Alpha tactical signals\n"
        "• Crypto Alpha tactical signals\n\n"
        "Cleanup/fix:\n"
        "• Portfolio header now includes Swing Alpha value.\n"
        "• Equity/openrisk/scanstatus use active-sleeve wording.\n"
        "• Disabled VCP/Bear/Options removed from live-facing outputs.\n"
        "• Legacy tables/helpers remain only for historical compatibility and safe exports.\n"
        "• IBKR reconciliation remains read-only; no broker orders are placed."
    )[:MAX_TELEGRAM_MESSAGE]


def _V48_OLD_INSTITUTIONAL() -> str:  # type: ignore[override]
    return _v47_label_cleanup(_V47_OLD_FORMAT_INSTITUTIONAL()).replace("v4.6.3", "v4.7")[:MAX_TELEGRAM_MESSAGE]

def _V48_OLD_DATAHEALTH() -> str:  # type: ignore[override]
    return _v47_label_cleanup(_V47_OLD_FORMAT_DATAHEALTH())

def _V48_OLD_RISKMATRIX() -> str:  # type: ignore[override]
    return _v47_label_cleanup(_V47_OLD_FORMAT_RISK())

def _V48_OLD_STRESS() -> str:  # type: ignore[override]
    return _v47_label_cleanup(_V47_OLD_FORMAT_STRESS())

def format_brokerstatus() -> str:  # type: ignore[override]
    return _v47_label_cleanup(_V47_OLD_FORMAT_BROKERSTATUS())

def format_brokerreconcile() -> str:  # type: ignore[override]
    return _v47_label_cleanup(_V47_OLD_FORMAT_BROKERRECONCILE())

def format_brokersyncpreview() -> str:  # type: ignore[override]
    return _v47_label_cleanup(_V47_OLD_FORMAT_BROKERSYNCPREVIEW())

def _v47_sleevestatus_text() -> str:
    snapshot = compute_equity_snapshot_data()
    alloc = dynamic_portfolio_allocation_targets()
    equity = float(snapshot.get("equity", 0) or 0)
    def pct(value: Any) -> float:
        try:
            return 0.0 if equity <= 0 else float(value or 0) / equity * 100.0
        except Exception:
            return 0.0
    return (
        "🧭 ACTIVE SLEEVE STATUS v4.7.1\n\n"
        f"💼 Equity: {format_money(equity)}\n"
        f"💵 Cash: {format_money(snapshot.get('cash', 0))} ({round(pct(snapshot.get('cash', 0)), 2)}%)\n\n"
        f"🏛️ Core: {format_money(snapshot.get('core_positions_value', 0))} / target {alloc.get('core_wealth_pct')}%\n"
        f"🚀 Growth Alpha: {format_money(snapshot.get('growth_alpha_positions_value', 0))} / target {alloc.get('growth_alpha_pct')}%\n"
        f"⚡ SPEC_ALPHA: {format_money(snapshot.get('spec_positions_value', 0))} / target {alloc.get('spec_alpha_pct')}%\n"
        f"🎯 Swing Alpha: {format_money(snapshot.get('swing_alpha_positions_value', 0))} / target {alloc.get('swing_alpha_pct')}%\n"
        f"🪙 Crypto Alpha: {format_money(snapshot.get('crypto_alpha_positions_value', 0))} / target {alloc.get('crypto_alpha_pct')}%\n\n"
        "Disabled live strategies:\n"
        "• Long VCP: 0% — replaced by Swing Alpha.\n"
        "• Bear/inverse: 0% — disabled.\n"
        "• Options: 0% — research-only, not live."
    )[:MAX_TELEGRAM_MESSAGE]


def _V48_OLD_EXPORT_STATE_BUNDLE(prefix: str = "bot_state_export") -> str:  # type: ignore[override]
    zip_path = _V47_OLD_EXPORT_STATE_BUNDLE(prefix=prefix)
    try:
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("v47_active_sleeve_cleanup.json", json.dumps(safe_convert({
                "version": V47_VERSION,
                "strategy_logic": V47_STRATEGY_LOGIC_LABEL,
                "allocation": V47_ALLOCATION_LABEL,
                "portfolio_header_fix": "active Swing Alpha value included in top portfolio summary",
                "disabled_live_sleeves": ["LONG_VCP", "BEAR_INVERSE", "OPTIONS"],
                "compatibility_note": "Legacy tables/helpers retained only for historical state/export safety; live outputs use active sleeves.",
                "no_strategy_logic_changed": True,
            }), indent=2))
    except Exception as exc:
        print(f"[V4.7.1 EXPORT WARNING] {exc}")
    return zip_path


# =============================================================================
# V4.8 - ACTIVE-ONLY SINGLE-FILE CLEANUP
# =============================================================================
# Surgical cleanup over v4.7.1:
# - Legacy VCP/Bear/Options live strategy paths are removed/blocked.
# - Active sleeves remain unchanged: Core, Growth, SPEC, Swing Alpha, Crypto.
# - IBKR reconciliation remains read-only.
# - No scoring/allocation/risk/entry logic changed for active sleeves.

V48_VERSION = "v4.8.1-active-only-clean-20-45-15-10-10-monitor"
if os.getenv("ALLOW_STRATEGY_VERSION_OVERRIDE", "0").strip() != "1":
    STRATEGY_VERSION = V48_VERSION

BEAR_SLEEVE_ENABLED = False
BEAR_WATCHLIST = []
V45_LONG_VCP_ALLOC = 0.0
V45_LONG_VCP_SIGNAL_ENGINE_ENABLED = False

V48_LOGIC_LABEL = "v4.8 active-only clean over v4.7.1 active sleeves"
V48_ALLOCATION_LABEL = "Core 20 / Growth 45 / SPEC 15 / Swing Alpha 10 / Crypto 10"


def dynamic_portfolio_allocation_targets() -> Dict[str, Any]:  # type: ignore[override]
    try:
        md = market_regime_details()
    except Exception:
        md = {"condition": "UNCERTAIN", "score": None, "max_score": 8}
    try:
        risk = portfolio_risk_guard_details()
    except Exception:
        risk = {"hard_active": False, "soft_active": False, "recommended_action": "Normal risk mode."}
    market = str(md.get("condition", "UNCERTAIN")).upper()
    if risk.get("hard_active"):
        core, growth, spec, swing, crypto = 20.0, 0.0, 0.0, 0.0, 0.0
    elif market == "BULL":
        core, growth, spec, swing, crypto = V46_CORE_ALLOC, V46_GROWTH_ALLOC, V46_SPEC_ALLOC, V46_SWING_ALLOC, V46_CRYPTO_ALLOC
    elif market == "BEAR":
        core, growth, spec, swing, crypto = 20.0, 0.0, 0.0, 0.0, 0.0
    else:
        core, growth, spec, swing, crypto = 20.0, 20.0, 8.0, 5.0, 5.0
    if risk.get("soft_active") and not risk.get("hard_active"):
        growth *= 0.50
        spec *= 0.50
        swing *= 0.50
        crypto *= 0.50
    if not GROWTH_ALPHA_ENABLED:
        growth = 0.0
    if not SPEC_ALPHA_ENABLED:
        spec = 0.0
    if not SWING_ALPHA_ENABLED:
        swing = 0.0
    if not CRYPTO_ALPHA_ENABLED:
        crypto = 0.0
    cash = max(0.0, 100.0 - core - growth - spec - swing - crypto)
    return {
        "strategy_version": "v4_8_1_active_only_dynamic_allocation",
        "ny_time": ny_now().strftime("%Y-%m-%d %H:%M %Z"),
        "market": market,
        "market_score": md.get("score"),
        "max_market_score": md.get("max_score", 8),
        "risk_guard": risk,
        "core_wealth_pct": round(core, 2),
        "growth_alpha_pct": round(growth, 2),
        "spec_alpha_pct": round(spec, 2),
        "swing_alpha_pct": round(swing, 2),
        "crypto_alpha_pct": round(crypto, 2),
        "long_vcp_tactical_pct": 0.0,
        "bear_inverse_tactical_pct": 0.0,
        "cash_reserve_pct": round(cash, 2),
    }

def format_portfolio_allocation_plan() -> str:  # type: ignore[override]
    plan = dynamic_portfolio_allocation_targets()
    risk = plan.get("risk_guard", {}) or {}
    snapshot = compute_equity_snapshot_data()
    return (
        "🏛️ INSTITUTIONAL ALLOCATION PLAN v4.9.7\n\n"
        "Private bot only. This is portfolio guidance, not an automatic trade.\n\n"
        f"🕒 NY time: {plan.get('ny_time')}\n"
        f"🌎 Market: {market_label(str(plan.get('market', 'UNKNOWN')))} ({plan.get('market_score')}/8)\n"
        f"🛡️ Risk guard: {risk.get('recommended_action')}\n"
        f"📉 Current DD: {risk.get('drawdown_pct')}% from {format_money(float(risk.get('high_equity', 0) or 0))}\n\n"
        "Target account buckets:\n"
        f"🏦 Core UCITS/USD rotation: {plan.get('core_wealth_pct')}%\n"
        f"🚀 Growth Alpha rotation: {plan.get('growth_alpha_pct')}%\n"
        f"⚡ SPEC_ALPHA rotation: {plan.get('spec_alpha_pct')}%\n"
        f"🎯 Swing Alpha tactical: {plan.get('swing_alpha_pct')}%\n"
        f"🪙 Crypto Alpha tactical: {plan.get('crypto_alpha_pct')}%\n"
        f"💵 Cash reserve / unused: {plan.get('cash_reserve_pct')}%\n\n"
        "Cash-flow ledger:\n"
        f"➕ Deposited cash recorded: {format_money(float(snapshot.get('deposited_cash', 0) or 0))}\n"
        f"➖ Withdrawals recorded: {format_money(float(snapshot.get('withdrawn_cash', 0) or 0))}\n"
        f"🔁 Net external cash: {format_money(float(snapshot.get('net_external_cash_flow', 0) or 0))}\n"
        f"📏 Performance base capital: {format_money(float(snapshot.get('performance_base_capital', 0) or 0))}\n\n"
        "Removed from live bot:\n"
        "• Legacy VCP: removed/replaced by Swing Alpha.\n"
        "• Bear / inverse sleeve: removed.\n"
        "• Options: not present in live bot.\n\n"
        "Rules:\n"
        "• Core/Growth/SPEC are monthly rotation sleeves.\n"
        "• Swing Alpha and Crypto are tactical sleeves with separate ledgers.\n"
        "• Deposits raise cash, performance base, and withdrawal HWM; deposits are not profit.\n"
        "• IBKR reconciliation is read-only; no broker orders are placed."
    )[:MAX_TELEGRAM_MESSAGE]


# v4.8 visible-report label wrappers.
def _v48_label_cleanup(text: Any) -> str:
    out = str(text)
    for old in ["v4.7.1", "V4.7.1", "v4.7", "V4.7", "v4.6.3", "V4.6.3", "v4.6.2", "V4.6.2"]:
        out = out.replace(old, "v4.8" if old.startswith("v") else "V4.8")
    return out[:MAX_TELEGRAM_MESSAGE]

def _V481_COMBINED_PORTFOLIO_REPORT() -> str:  # type: ignore[override]
    return _v48_label_cleanup(_V48_OLD_COMBINED_PORTFOLIO_REPORT())

def _V481_EQUITY_TEXT_BASE() -> str:
    return _v48_label_cleanup(_v47_equity_text())

def _V481_OPENRISK_TEXT_BASE() -> str:
    return _v48_label_cleanup(_v47_openrisk_text())

def _V481_SCANSTATUS_TEXT_BASE() -> str:
    return _v48_label_cleanup(_v47_scanstatus_text())

def _V481_SLEEVE_TEXT_BASE() -> str:
    return _v48_label_cleanup(_v47_sleevestatus_text())

def _V481_RISK_BASE() -> str:  # type: ignore[override]
    return _v48_label_cleanup(_V48_OLD_RISKMATRIX())

def _V481_STRESS_BASE() -> str:  # type: ignore[override]
    return _v48_label_cleanup(_V48_OLD_STRESS())

def format_institutional_status() -> str:  # type: ignore[override]
    return _v48_label_cleanup(_V48_OLD_INSTITUTIONAL())

def format_datahealth_status() -> str:  # type: ignore[override]
    return _v48_label_cleanup(_V48_OLD_DATAHEALTH())


def _V482_EXPORT_BASE(prefix: str = "bot_state_export") -> str:  # type: ignore[override]
    zip_path = _V48_OLD_EXPORT_STATE_BUNDLE(prefix=prefix)
    try:
        summary_path = os.path.join(DATA_DIR, "v4_8_1_active_only_manifest.json")
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump({
                "strategy_version": STRATEGY_VERSION,
                "logic": V48_LOGIC_LABEL,
                "allocation": V48_ALLOCATION_LABEL,
                "removed_live_strategies": ["LONG_VCP", "BEAR_INVERSE", "OPTIONS"],
                "active_ledgers": ["CORE_WEALTH", "GROWTH_ALPHA", "SPEC_ALPHA", "SWING_ALPHA", "CRYPTO_ALPHA"],
                "ibkr_reconciliation": "read_only",
            }, f, indent=2)
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(summary_path, arcname="v4_8_1_active_only_manifest.json")
    except Exception as exc:
        print(f"[V4.8 EXPORT MANIFEST ERROR] {exc}")
    return zip_path


# ---- v4.8.1 active-only verification cleanup ----


# v4.8.1 visible label wrappers for all user-facing active reports.
def _v481_label_cleanup(text: Any) -> str:
    out = str(text)
    out = out.replace("v4.8.1.1", "v4.8.1")
    out = out.replace("V4.8.1.1", "V4.8.1")
    out = out.replace("v4.8-active-only-clean", "v4.8.1-active-only-clean")
    out = out.replace("V4.8 ACTIVE-ONLY", "V4.8.1 ACTIVE-ONLY")
    out = out.replace("v4.8 RECON", "v4.8.1 RECON")
    out = out.replace("V4.8 RECON", "V4.8.1 RECON")
    out = out.replace(" v4.8\n", " v4.8.1\n")
    out = out.replace(" v4.8 |", " v4.8.1 |")
    out = out.replace(" v4.8", " v4.8.1")
    out = out.replace("V4.8", "V4.8.1")
    out = out.replace("v4.8.1.1", "v4.8.1")
    out = out.replace("V4.8.1.1", "V4.8.1")
    out = out.replace("v4.8: Legacy", "v4.8.1: Legacy")
    return out[:MAX_TELEGRAM_MESSAGE]

def _V482_PORTFOLIO_BASE() -> str:  # type: ignore[override]
    return _v481_label_cleanup(_V481_COMBINED_PORTFOLIO_REPORT())

def _v48_equity_text() -> str:  # type: ignore[override]
    return _v481_label_cleanup(_V481_EQUITY_TEXT_BASE())

def _v48_openrisk_text() -> str:  # type: ignore[override]
    return _v481_label_cleanup(_V481_OPENRISK_TEXT_BASE())

def _v48_scanstatus_text() -> str:  # type: ignore[override]
    return _v481_label_cleanup(_V481_SCANSTATUS_TEXT_BASE())

def _v48_sleevestatus_text() -> str:  # type: ignore[override]
    snapshot = compute_equity_snapshot_data()
    alloc = dynamic_portfolio_allocation_targets()
    equity = float(snapshot.get("equity", 0) or 0)

    def pct(value: Any) -> float:
        try:
            return 0.0 if equity <= 0 else (float(value or 0) / equity) * 100
        except Exception:
            return 0.0

    return (
        "🧭 ACTIVE SLEEVE STATUS v4.9.7\n\n"
        f"💼 Equity: {format_money(equity)}\n"
        f"💵 Cash: {format_money(snapshot.get('cash', 0))} ({round(pct(snapshot.get('cash', 0)), 2)}%)\n"
        f"➕ Deposited cash recorded: {format_money(snapshot.get('deposited_cash', 0))}\n"
        f"➖ Withdrawals recorded: {format_money(snapshot.get('withdrawn_cash', 0))}\n"
        f"🔁 Net external cash: {format_money(snapshot.get('net_external_cash_flow', 0))}\n\n"
        f"🏛️ Core: {format_money(snapshot.get('core_positions_value', 0))} / target {alloc.get('core_wealth_pct')}%\n"
        f"🚀 Growth Alpha: {format_money(snapshot.get('growth_alpha_positions_value', 0))} / target {alloc.get('growth_alpha_pct')}%\n"
        f"⚡ SPEC_ALPHA: {format_money(snapshot.get('spec_positions_value', 0))} / target {alloc.get('spec_alpha_pct')}%\n"
        f"🎯 Swing Alpha: {format_money(snapshot.get('swing_alpha_positions_value', 0))} / target {alloc.get('swing_alpha_pct')}%\n"
        f"🪙 Crypto Alpha: {format_money(snapshot.get('crypto_alpha_positions_value', 0))} / target {alloc.get('crypto_alpha_pct')}%\n\n"
        "Disabled live strategies:\n"
        "• Long VCP: 0% — replaced by Swing Alpha.\n"
        "• Bear/inverse: 0% — disabled.\n"
        "• Options: 0% — research-only, not live."
    )[:MAX_TELEGRAM_MESSAGE]

def _V482_RISK_BASE() -> str:  # type: ignore[override]
    return _v481_label_cleanup(_V481_RISK_BASE())

def _V482_STRESS_BASE() -> str:  # type: ignore[override]
    return _v481_label_cleanup(_V481_STRESS_BASE())


# =============================================================================
# V4.8.2 - PUBLIC SIGNALS + CRYPTO AUTO-CHECK CLEANUP
# =============================================================================
# Purpose:
# - Stop daily no-action Crypto Alpha spam after market close.
# - Keep Crypto Alpha auto alerts event-driven: new action, action cleared, or gate change.
# - Add public-channel versions of strategic signals with percentage sizing only.
# - Keep all active trading logic from v4.8.1 unchanged.
# - No broker orders are placed; IBKR reconciliation remains read-only.

V482_VERSION = "v4.8.2-public-signals-crypto-alerts-20-45-15-10-10-monitor"
if os.getenv("ALLOW_STRATEGY_VERSION_OVERRIDE", "0").strip() != "1":
    STRATEGY_VERSION = V482_VERSION

V482_STRATEGY_LOGIC_LABEL = "v4.8.2 active-only clean + public strategic alerts + event-driven crypto checks"
V482_ALLOCATION_LABEL = "Core 20 / Growth 45 / SPEC 15 / Swing Alpha 10 / Crypto 10"

# Public-channel controls. Global PUBLIC_SIGNAL_ENABLED and SIGNAL_CHANNEL_ID remain the master switch.
# These sleeve-level controls default to ON only if the global public switch is ON.
CORE_PUBLIC_SIGNAL_ENABLED = os.getenv("CORE_PUBLIC_SIGNAL_ENABLED", "1").strip() != "0"
GROWTH_ALPHA_PUBLIC_SIGNAL_ENABLED = os.getenv("GROWTH_ALPHA_PUBLIC_SIGNAL_ENABLED", "1").strip() != "0"
SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED = os.getenv("SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED", "1").strip() != "0"
SWING_ALPHA_PUBLIC_SIGNAL_ENABLED = os.getenv("SWING_ALPHA_PUBLIC_SIGNAL_ENABLED", "1").strip() != "0"
CRYPTO_ALPHA_PUBLIC_SIGNAL_ENABLED = os.getenv("CRYPTO_ALPHA_PUBLIC_SIGNAL_ENABLED", "1").strip() != "0"
V482_PUBLIC_MONTHLY_DASHBOARD_ENABLED = os.getenv("V482_PUBLIC_MONTHLY_DASHBOARD_ENABLED", "1").strip() != "0"
V482_PUBLIC_MONTHLY_DETAIL_ENABLED = os.getenv("V482_PUBLIC_MONTHLY_DETAIL_ENABLED", "1").strip() != "0"
V482_PUBLIC_CRYPTO_ACTION_ENABLED = os.getenv("V482_PUBLIC_CRYPTO_ACTION_ENABLED", "1").strip() != "0"
V482_PUBLIC_CRYPTO_GATE_STATUS_ENABLED = os.getenv("V482_PUBLIC_CRYPTO_GATE_STATUS_ENABLED", "0").strip() == "1"

# Crypto no-action daily messages are too noisy for live operation. Keep event-driven checks.
V482_CRYPTO_SEND_NO_ACTION_DAILY = os.getenv("V482_CRYPTO_SEND_NO_ACTION_DAILY", "0").strip() == "1"
try:
    V463_CRYPTO_SEND_NO_ACTION_DAILY = V482_CRYPTO_SEND_NO_ACTION_DAILY
except Exception:
    pass


def public_signal_footer() -> str:  # type: ignore[override]
    return (
        "⚠️ Not financial advice. Automated trading-bot alert for education, monitoring, and forward testing only.\n"
        "Do your own research and due diligence. Use your own account size, risk tolerance, tax situation, and execution plan.\n"
        "Signals can be wrong, delayed, invalidated, or affected by gaps, slippage, data errors, news, and market conditions.\n"
        "I may personally hold, buy, or sell mentioned instruments.\n"
        "Any paid access or membership fee is for bot maintenance, infrastructure, data, development, and alert access only — "
        "not for personalized financial advice, account management, profit guarantees, or investment recommendations."
    )


def public_channel_terms_text() -> str:  # type: ignore[override]
    return (
        "📌 CHANNEL DISCLAIMER\n\n"
        "This channel shares automated trading-bot alerts for educational, monitoring, and forward-testing purposes only.\n\n"
        "Nothing posted here is financial advice, investment advice, personalized advice, portfolio management, or a guarantee of profit. "
        "I am not your financial adviser and I do not know your account size, risk tolerance, tax situation, goals, or execution ability.\n\n"
        "Trading stocks, ETFs/ETPs, crypto, and high-volatility assets can cause losses. Losses can happen because of gaps, slippage, "
        "delayed execution, bad data, earnings, news, market events, liquidity, or system errors.\n\n"
        "Public alerts use percentage/risk guidance instead of my private share counts. Everyone must size positions independently.\n\n"
        "I may personally hold, buy, or sell instruments mentioned here. Signals may be delayed, wrong, changed, or invalidated. "
        "Past performance, backtests, paper results, and forward tests do not guarantee future results.\n\n"
        "Any paid access or membership fee, if offered, is for bot maintenance, infrastructure, data, development, and access to automated alerts only. "
        "It is not payment for guaranteed returns, personalized advice, or account management."
    )


def _v482_pct_text(value: Any, decimals: int = 2) -> str:
    try:
        if value is None:
            return "n/a"
        v = float(value)
        if decimals <= 0:
            return f"{round(v):.0f}%"
        return f"{round(v, decimals)}%"
    except Exception:
        return "n/a"


def _v482_item_target_pct(item: Dict[str, Any], plan: Optional[Dict[str, Any]] = None) -> Optional[float]:
    for key in ("target_account_pct", "target_pct", "account_pct", "target_weight_pct"):
        try:
            if item.get(key) is not None:
                return float(item.get(key))
        except Exception:
            pass
    try:
        tv = float(item.get("target_value", 0) or 0)
        eq = float((plan or {}).get("account_equity", 0) or 0)
        if eq > 0 and tv > 0:
            return (tv / eq) * 100.0
    except Exception:
        pass
    return None


def _v482_item_price(item: Dict[str, Any]) -> Any:
    return item.get("signal_price", item.get("plan_price", item.get("price", item.get("mark_price"))))


def _v482_item_max_entry(item: Dict[str, Any]) -> Any:
    return item.get("max_valid_entry", item.get("max_entry", item.get("max_limit")))


def _v482_public_plan_lines(label: str, plan: Optional[Dict[str, Any]], limit: int = 6) -> List[str]:
    if not plan:
        return [f"• {label}: unavailable"]
    acts = _v463_plan_actions(plan)
    if not acts:
        return [f"• {label}: no actionable trade now"]
    lines = [f"• {label}:"]
    for item in acts[:limit]:
        action = str(item.get("action", "?")).upper()
        ticker = str(item.get("ticker", "?")).upper()
        pct = _v482_item_target_pct(item, plan)
        price = _v482_item_price(item)
        max_entry = _v482_item_max_entry(item)
        stop = item.get("stop")
        detail = f"  - {action} {ticker} | target guide {_v482_pct_text(pct)} of account"
        if price is not None:
            detail += f" | ref {fmt_public_number(price)}"
        if max_entry is not None:
            detail += f" | max {fmt_public_number(max_entry)}"
        if stop is not None:
            detail += f" | stop {fmt_public_number(stop)}"
        lines.append(detail)
    if len(acts) > limit:
        lines.append(f"  - +{len(acts) - limit} more actionable item(s)")
    return lines


def format_public_monthly_dashboard(core_plan: Optional[Dict[str, Any]], growth_plan: Optional[Dict[str, Any]], spec_plan: Optional[Dict[str, Any]], crypto_plan: Optional[Dict[str, Any]], errors: Dict[str, str]) -> str:
    lines: List[str] = []
    lines.append("🗓️ MONTHLY BOT ACTION DASHBOARD")
    lines.append("")
    lines.append(f"🕒 NY time: {ny_now().strftime('%Y-%m-%d %H:%M %Z')}")
    lines.append(f"Allocation model: {V482_ALLOCATION_LABEL}")
    lines.append("")
    lines.append("Monthly rotation sleeves:")
    for label, plan in (("Core", core_plan), ("Growth Alpha", growth_plan), ("SPEC Alpha", spec_plan)):
        if errors.get(label.split()[0]):
            lines.append(f"• {label}: error — {errors.get(label.split()[0])}")
        else:
            acts = _v463_plan_actions(plan)
            if acts:
                lines.append(f"• {label}: {len(acts)} actionable item(s). See public plan details below.")
            else:
                lines.append(f"• {label}: no actionable trade now.")
    lines.append("")
    lines.append("Tactical sleeves:")
    try:
        crypto_actions = _v463_plan_actions(crypto_plan)
        lines.append(f"• Crypto Alpha: {len(crypto_actions)} actionable item(s) now." if crypto_actions else "• Crypto Alpha: no actionable trade now / gate may be off.")
    except Exception:
        lines.append("• Crypto Alpha: unavailable.")
    lines.append("• Swing Alpha: tactical entries/exits are sent separately when valid setups trigger.")
    lines.append("")
    lines.append("Public sizing note: use percentage guidance only; calculate your own shares/units.")
    lines.append("")
    lines.append(public_signal_footer())
    return "\n".join(lines)[:MAX_TELEGRAM_MESSAGE]


def format_public_monthly_plan(label: str, plan: Optional[Dict[str, Any]]) -> str:
    lines = [f"📋 {label.upper()} PUBLIC PLAN", ""]
    lines.extend(_v482_public_plan_lines(label, plan, limit=8))
    lines.append("")
    lines.append("Use your own account size and execution. No exact private share counts are provided.")
    lines.append("")
    lines.append(public_signal_footer())
    return "\n".join(lines)[:MAX_TELEGRAM_MESSAGE]


def format_public_crypto_plan(plan: Dict[str, Any], reason: str = "crypto action") -> str:
    gate = plan.get("gate", {}) or {}
    lines = ["🪙 CRYPTO ALPHA ALERT", "", f"Reason: {reason}", f"🕒 NY time: {plan.get('ny_time')}"]
    lines.append(f"Daily BTC gate: {yes_no(bool(gate.get('btc_ok')))}")
    lines.append(f"4h module gate: {yes_no(bool(gate.get('gate2_ok')))} ({gate.get('ok_count')}/{len(CRYPTO_ALPHA_INDICATORS)} above MA200)")
    lines.append("")
    acts = _v463_plan_actions(plan)
    if acts:
        lines.append("Actionable crypto items:")
        for item in acts[:6]:
            action = str(item.get("action", "?")).upper()
            ticker = str(item.get("ticker", "?")).upper()
            pct = _v482_item_target_pct(item, plan)
            price = _v482_item_price(item)
            max_entry = _v482_item_max_entry(item)
            stop = item.get("stop")
            line = f"• {action} {ticker} | target guide {_v482_pct_text(pct)} of account"
            if price is not None:
                line += f" | ref {fmt_public_number(price)}"
            if max_entry is not None:
                line += f" | max {fmt_public_number(max_entry)}"
            if stop is not None:
                line += f" | stop {fmt_public_number(stop)}"
            lines.append(line)
    else:
        lines.append("No public crypto trade action now.")
    lines.append("")
    lines.append(public_signal_footer())
    return "\n".join(lines)[:MAX_TELEGRAM_MESSAGE]


def _v482_public_enabled_for_monthly() -> bool:
    return bool(PUBLIC_SIGNAL_ENABLED and SIGNAL_CHANNEL_ID != 0 and V482_PUBLIC_MONTHLY_DASHBOARD_ENABLED)


def _v482_send_public_monthly_bundle(core_plan: Optional[Dict[str, Any]], growth_plan: Optional[Dict[str, Any]], spec_plan: Optional[Dict[str, Any]], crypto_plan: Optional[Dict[str, Any]], errors: Dict[str, str]) -> None:
    if not _v482_public_enabled_for_monthly():
        return
    ok, info = send_public_signal(format_public_monthly_dashboard(core_plan, growth_plan, spec_plan, crypto_plan, errors))
    if not ok:
        send(f"⚠️ Public monthly dashboard failed: {info}")
        return
    if not V482_PUBLIC_MONTHLY_DETAIL_ENABLED:
        return
    if CORE_PUBLIC_SIGNAL_ENABLED and core_plan is not None:
        send_public_signal(format_public_monthly_plan("Core", core_plan))
    if GROWTH_ALPHA_PUBLIC_SIGNAL_ENABLED and growth_plan is not None:
        send_public_signal(format_public_monthly_plan("Growth Alpha", growth_plan))
    if SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED and spec_plan is not None:
        send_public_signal(format_public_monthly_plan("SPEC Alpha", spec_plan))


def send_v463_monthly_dashboard(force: bool = False, preview_only: bool = False) -> bool:  # type: ignore[override]
    # Preserve private behavior first. Public forwarding is added only when an actual monthly dashboard is sent.
    sent_private = _V482_OLD_SEND_V463_MONTHLY_DASHBOARD(force=force, preview_only=preview_only)
    if sent_private and not preview_only:
        try:
            core_plan, growth_plan, spec_plan, crypto_plan, errors = _v463_prepare_dashboard_plans()
            month_key = ny_now().strftime("%Y-%m")
            if get_meta("last_v482_public_monthly_dashboard_month") != month_key:
                _v482_send_public_monthly_bundle(core_plan, growth_plan, spec_plan, crypto_plan, errors)
                set_meta("last_v482_public_monthly_dashboard_month", month_key)
                set_meta("last_v482_public_monthly_dashboard_ts", str(now_ts()))
        except Exception as exc:
            logger.exception(f"[V4.8.2 PUBLIC MONTHLY DASHBOARD ERROR] {exc}")
            send(f"⚠️ Public monthly dashboard failed: {exc}")
    return sent_private


# Label cleanup for v4.8.2 on user-facing reports.
def _v482_label_cleanup(text: Any) -> str:
    out = str(text)
    out = out.replace("v4.8.1", "v4.8.2")
    out = out.replace("V4.8.1", "V4.8.2")
    out = out.replace("v4.8", "v4.8.2")
    out = out.replace("V4.8", "V4.8.2")
    out = out.replace("v4.8.2.2", "v4.8.2")
    out = out.replace("V4.8.2.2", "V4.8.2")
    return out[:MAX_TELEGRAM_MESSAGE]


def _V483_FORMAT_PORTFOLIO_BASE() -> str:  # type: ignore[override]
    return _v482_label_cleanup(_V482_PORTFOLIO_BASE())


def _V483_FORMAT_RISK_BASE() -> str:  # type: ignore[override]
    return _v482_label_cleanup(_V482_RISK_BASE())


def _V483_FORMAT_STRESS_BASE() -> str:  # type: ignore[override]
    return _v482_label_cleanup(_V482_STRESS_BASE())


def _V483_EXPORT_BASE(prefix: str = "bot_state_export") -> str:  # type: ignore[override]
    zip_path = _V482_EXPORT_BASE(prefix=prefix)
    try:
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("v482_public_crypto_alerts.json", json.dumps(safe_convert({
                "version": V482_VERSION,
                "strategy_logic": V482_STRATEGY_LOGIC_LABEL,
                "allocation": V482_ALLOCATION_LABEL,
                "public_global_enabled": PUBLIC_SIGNAL_ENABLED,
                "signal_channel_id_configured": SIGNAL_CHANNEL_ID != 0,
                "core_public_enabled": CORE_PUBLIC_SIGNAL_ENABLED,
                "growth_public_enabled": GROWTH_ALPHA_PUBLIC_SIGNAL_ENABLED,
                "spec_public_enabled": SPEC_ALPHA_PUBLIC_SIGNAL_ENABLED,
                "swing_public_enabled": SWING_ALPHA_PUBLIC_SIGNAL_ENABLED,
                "crypto_public_enabled": CRYPTO_ALPHA_PUBLIC_SIGNAL_ENABLED,
                "monthly_public_dashboard_enabled": V482_PUBLIC_MONTHLY_DASHBOARD_ENABLED,
                "crypto_no_action_daily_enabled": V482_CRYPTO_SEND_NO_ACTION_DAILY,
                "notes": "v4.8.2 stops daily no-action crypto spam and adds public strategic alerts with percentage-only sizing. No active strategy scoring/allocation changed.",
            }), indent=2))
    except Exception as exc:
        print(f"[V4.8.2 EXPORT WARNING] {exc}")
    return zip_path


# =============================================================================
# V4.8.3 - FINAL FREEZE POLISH: LABELS + EVENT-ONLY CRYPTO ALERTS
# =============================================================================
# Purpose:
# - Keep v4.8.2 strategy and public forwarding intact.
# - Remove remaining user-facing v4.8.1/v4.8.2 label noise.
# - Make crypto auto alerts strictly event/action driven: no daily no-action spam
#   and no passive gate-closed alerts.
# - Do not change active strategy scoring, allocation, ledgers, IBKR behavior, or
#   order execution behavior.

V497_VERSION = "v4.9.7-final-freeze-depositcash-20-45-15-10-10-monitor"
V497_LOGIC_LABEL = "v4.9.7: v4.9.4 review-fixed strategy with depositcash accounting, emoji restoration, and action-only crypto alerts"
V497_ALLOCATION_LABEL = "Core 20 / Growth 45 / SPEC 15 / Swing Alpha 10 / Crypto 10 / VCP 0 / Bear 0 / Options 0"
V483_VERSION = V497_VERSION
V483_LOGIC_LABEL = V497_LOGIC_LABEL
V483_ALLOCATION_LABEL = V497_ALLOCATION_LABEL
V497_CRYPTO_ALERT_ON_GATE_OPEN = os.getenv(
    "V497_CRYPTO_ALERT_ON_GATE_OPEN",
    os.getenv("V483_CRYPTO_ALERT_ON_GATE_OPEN", "0")
).strip() != "0"
V483_CRYPTO_ALERT_ON_GATE_OPEN = V497_CRYPTO_ALERT_ON_GATE_OPEN

if os.getenv("ALLOW_STRATEGY_VERSION_OVERRIDE", "0").strip() != "1":
    STRATEGY_VERSION = V483_VERSION


def _v483_label_cleanup(text: Any) -> str:
    out = str(text)
    replacements = [
        ("v4.8.3", "v4.9.7"), ("V4.8.3", "V4.9.7"),
        ("v4.8.2", "v4.9.7"), ("V4.8.2", "V4.9.7"),
        ("v4.8.1", "v4.9.7"), ("V4.8.1", "V4.9.7"),
        ("v4.8", "v4.9.7"), ("V4.8", "V4.9.7"),
        ("v4.7.1", "v4.9.7"), ("V4.7.1", "V4.9.7"),
        ("v4.7", "v4.9.7"), ("V4.7", "V4.9.7"),
        ("v4.6.3", "v4.9.7"), ("V4.6.3", "V4.9.7"),
        ("v4.6.2", "v4.9.7"), ("V4.6.2", "V4.9.7"),
        ("v4.4.3", "v4.9.7"), ("V4.4.3", "V4.9.7"),
        ("v4.3", "v4.9.7"), ("V4.3", "V4.9.7"),
        ("v3.8", "v4.9.7"), ("V3.8", "V4.9.7"),
        ("v3.7", "v4.9.7"), ("V3.7", "V4.9.7"),
        ("v3.6", "v4.9.7"), ("V3.6", "V4.9.7"),
    ]
    for old, new in replacements:
        out = out.replace(old, new)
    return out[:MAX_TELEGRAM_MESSAGE]


def maybe_send_crypto_alpha_auto_signal(force: bool = False) -> bool:  # type: ignore[override]
    """Event-only crypto auto alerts.

    Sends only for:
    - manual force checks,
    - new actionable BUY/ADD/TRIM/SELL,
    - previously actionable state clearing,
    - optional gate-open informational alert when V483_CRYPTO_ALERT_ON_GATE_OPEN=1.

    It does not send daily no-action plans and does not send passive gate-closed alerts.
    """
    if not (V463_CRYPTO_AUTO_CHECK_ENABLED or force):
        return False
    if not CRYPTO_ALPHA_ENABLED:
        return False
    if not force:
        last_ts = get_meta("last_v483_crypto_auto_check_ts") or get_meta("last_v482_crypto_auto_check_ts") or get_meta("last_v463_crypto_auto_check_ts")
        if last_ts:
            try:
                elapsed = (now_ts() - float(last_ts)) / 60.0
                if elapsed < max(15, V463_CRYPTO_AUTO_CHECK_INTERVAL_MIN):
                    return False
            except Exception:
                pass
    try:
        plan = compute_crypto_alpha_plan()
    except Exception as exc:
        logger.exception(f"[V4.9.7 CRYPTO AUTO CHECK ERROR] {exc}")
        if force:
            send(f"⚠️ Crypto auto-check failed: {exc}")
        return False

    set_meta("last_v483_crypto_auto_check_ts", str(now_ts()))
    gate_sig = _v463_crypto_gate_signature(plan)
    action_sig = _v463_crypto_action_signature(plan)
    last_gate = get_meta("last_v483_crypto_gate_signature") or get_meta("last_v482_crypto_gate_signature") or get_meta("last_v463_crypto_gate_signature")
    last_action = get_meta("last_v483_crypto_action_signature") or get_meta("last_v482_crypto_action_signature") or get_meta("last_v463_crypto_action_signature")

    reason = ""
    public_reason = ""
    if force:
        reason = "manual check"
    elif action_sig != last_action:
        if action_sig != "NO_ACTION":
            reason = "new crypto action"
            public_reason = reason
        elif last_action and last_action != "NO_ACTION":
            reason = "crypto action cleared"
    elif V483_CRYPTO_ALERT_ON_GATE_OPEN and last_gate is not None and gate_sig != last_gate:
        gate = plan.get("gate", {}) or {}
        if bool(gate.get("btc_ok")) or bool(gate.get("gate2_ok")):
            reason = "crypto gate opened"
            if V482_PUBLIC_CRYPTO_GATE_STATUS_ENABLED:
                public_reason = reason

    if not reason:
        if last_gate is None:
            set_meta("last_v483_crypto_gate_signature", gate_sig)
        if last_action is None:
            set_meta("last_v483_crypto_action_signature", action_sig)
        # Keep older keys in sync to avoid older wrappers producing duplicates.
        set_meta("last_v482_crypto_gate_signature", gate_sig)
        set_meta("last_v482_crypto_action_signature", action_sig)
        set_meta("last_v463_crypto_gate_signature", gate_sig)
        set_meta("last_v463_crypto_action_signature", action_sig)
        return False

    header = f"🪙 CRYPTO AUTO CHECK v4.9.7 — {reason}\n\n"
    send((header + format_crypto_alpha_plan(plan))[:MAX_TELEGRAM_MESSAGE])
    if public_reason and PUBLIC_SIGNAL_ENABLED and SIGNAL_CHANNEL_ID != 0 and CRYPTO_ALPHA_PUBLIC_SIGNAL_ENABLED and V482_PUBLIC_CRYPTO_ACTION_ENABLED:
        ok, info = send_public_signal(format_public_crypto_plan(plan, reason=public_reason))
        if not ok:
            send(f"⚠️ Public crypto alert failed: {info}")

    for prefix in ("last_v483", "last_v482", "last_v463"):
        set_meta(f"{prefix}_crypto_gate_signature", gate_sig)
        set_meta(f"{prefix}_crypto_action_signature", action_sig)
    audit("V497_CRYPTO_AUTO_CHECK", f"reason={reason} action={action_sig} gate={gate_sig}")
    return True


# Final label wrappers.

try:
    _V483_FORMAT_INSTITUTIONAL_BASE = format_institutional_status
except Exception:
    _V483_FORMAT_INSTITUTIONAL_BASE = None

try:
    _V483_FORMAT_DATAHEALTH_BASE = format_datahealth_status
except Exception:
    _V483_FORMAT_DATAHEALTH_BASE = None

try:
    _V483_FORMAT_EXECUTION_BASE = format_execution_status
except Exception:
    _V483_FORMAT_EXECUTION_BASE = None

try:
    _V483_FORMAT_DRIFT_BASE = format_drift_status
except Exception:
    _V483_FORMAT_DRIFT_BASE = None

try:
    _V483_FORMAT_SCANSTATUS_BASE = format_scan_status
except Exception:
    _V483_FORMAT_SCANSTATUS_BASE = None

try:
    _V483_FORMAT_OPENRISK_BASE = format_open_risk
except Exception:
    _V483_FORMAT_OPENRISK_BASE = None

try:
    _V483_FORMAT_SLEEVE_BASE = format_sleevestatus
except Exception:
    _V483_FORMAT_SLEEVE_BASE = None


def format_combined_portfolio_report() -> str:  # type: ignore[override]
    return _v483_label_cleanup(_V483_FORMAT_PORTFOLIO_BASE())


def format_riskmatrix_status() -> str:  # type: ignore[override]
    return _v483_label_cleanup(_V483_FORMAT_RISK_BASE())


def format_stress_status() -> str:  # type: ignore[override]
    return _v483_label_cleanup(_V483_FORMAT_STRESS_BASE())


if _V483_FORMAT_INSTITUTIONAL_BASE is not None:
    def format_institutional_status() -> str:  # type: ignore[override]
        return _v483_label_cleanup(_V483_FORMAT_INSTITUTIONAL_BASE())

if _V483_FORMAT_DATAHEALTH_BASE is not None:
    def format_datahealth_status() -> str:  # type: ignore[override]
        return _v483_label_cleanup(_V483_FORMAT_DATAHEALTH_BASE())

if _V483_FORMAT_EXECUTION_BASE is not None:
    def format_execution_status() -> str:  # type: ignore[override]
        return _v483_label_cleanup(_V483_FORMAT_EXECUTION_BASE())

if _V483_FORMAT_DRIFT_BASE is not None:
    def format_drift_status() -> str:  # type: ignore[override]
        return _v483_label_cleanup(_V483_FORMAT_DRIFT_BASE())

if _V483_FORMAT_SCANSTATUS_BASE is not None:
    def format_scan_status() -> str:  # type: ignore[override]
        return _v483_label_cleanup(_V483_FORMAT_SCANSTATUS_BASE())

if _V483_FORMAT_OPENRISK_BASE is not None:
    def format_open_risk() -> str:  # type: ignore[override]
        return _v483_label_cleanup(_V483_FORMAT_OPENRISK_BASE())

if _V483_FORMAT_SLEEVE_BASE is not None:
    def format_sleevestatus() -> str:  # type: ignore[override]
        return _v483_label_cleanup(_V483_FORMAT_SLEEVE_BASE())


def export_state_bundle(prefix: str = "bot_state_export") -> str:  # type: ignore[override]
    zip_path = _V483_EXPORT_BASE(prefix=prefix)
    try:
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("v497_final_freeze.json", json.dumps(safe_convert({
                "version": V497_VERSION,
                "strategy_logic": V497_LOGIC_LABEL,
                "allocation": V497_ALLOCATION_LABEL,
                "cash_accounting": "depositcash records external principal; setcash is disabled; withdrawals use deposit-adjusted HWM",
                "crypto_alerts": "event/action driven; daily no-action spam disabled; passive gate-closed alerts disabled",
                "public_channel": "enabled when PUBLIC_SIGNAL_ENABLED=1 and SIGNAL_CHANNEL_ID is configured; percentage guidance only",
                "ibkr": "read-only reconciliation only; no broker orders placed",
            }), indent=2))
            z.writestr("cash_deposits.table.json", json.dumps(safe_convert(load_cash_deposits()), indent=2))
            z.writestr("cash_flow_summary.json", json.dumps(safe_convert(cash_deposit_summary()), indent=2))
    except Exception as exc:
        print(f"[V4.9.7 EXPORT WARNING] {exc}")
    return zip_path


# Final crypto label cleanup after v4.9.7 auto-alert override.

def format_crypto_alpha_plan(plan: Dict[str, Any]) -> str:  # type: ignore[override]
    return _v483_label_cleanup(_V483_FORMAT_CRYPTO_BASE(plan))

try:
    _V483_FORMAT_CRYPTO_STATUS_BASE = format_crypto_alpha_status
    def format_crypto_alpha_status() -> str:  # type: ignore[override]
        return _v483_label_cleanup(_V483_FORMAT_CRYPTO_STATUS_BASE())
except Exception:
    pass


# =============================================================================
# V4.9.7 FINAL USER-FACING POLISH OVERRIDE
# =============================================================================
# This last block is intentionally label/alert routing only.
# It does not change strategy scoring, allocation, risk, ledgers, IBKR, or execution.

def v483_final_status_text() -> str:
    snapshot = compute_equity_snapshot_data()
    return (
        "🧊 V4.9.7 FINAL FREEZE STATUS\n\n"
        f"Strategy display: {STRATEGY_VERSION}\n"
        "Strategy logic: v4.9.4 review-fixed active-only Core/Growth/SPEC/Swing/Crypto. No scoring/allocation change.\n"
        "Allocation: Core 20 / Growth 45 / SPEC 15 / Swing Alpha 10 / Crypto 10\n"
        "Disabled from live operation: legacy VCP 0%, Bear/inverse 0%, Options 0%\n\n"
        "🧾 Cash accounting:\n"
        "➕ depositcash records external deposits as principal.\n"
        "❌ setcash is disabled. Use IBKR reconciliation for broker cash checks.\n"
        "🏔️ Withdrawal signals use a deposit-adjusted high-water mark.\n"
        f"➕ Deposited cash: {format_money(snapshot.get('cash_deposited', 0))}\n"
        f"➖ Withdrawn cash: {format_money(snapshot.get('cash_withdrawn', 0))}\n"
        f"🔁 Net external cash: {format_money(snapshot.get('net_external_cash', 0))}\n\n"
        f"Public channel enabled: {yes_no(PUBLIC_SIGNAL_ENABLED and SIGNAL_CHANNEL_ID != 0)}\n"
        f"Public monthly dashboard: {yes_no(V482_PUBLIC_MONTHLY_DASHBOARD_ENABLED)}\n"
        f"Public monthly details: {yes_no(V482_PUBLIC_MONTHLY_DETAIL_ENABLED)}\n"
        f"Public Swing Alpha signals: {yes_no(SWING_ALPHA_PUBLIC_SIGNAL_ENABLED)}\n"
        f"Public Crypto actionable alerts: {yes_no(CRYPTO_ALPHA_PUBLIC_SIGNAL_ENABLED and V482_PUBLIC_CRYPTO_ACTION_ENABLED)}\n"
        f"Crypto daily no-action alerts: {yes_no(V482_CRYPTO_SEND_NO_ACTION_DAILY)}\n"
        f"Crypto gate-open info alerts: {yes_no(V483_CRYPTO_ALERT_ON_GATE_OPEN)}\n"
        f"IBKR reconciliation: {yes_no(IBKR_RECON_ENABLED)} read-only\n\n"
        "📣 Expected automatic alerts:\n"
        "• 🗓️ Monthly Core/Growth/SPEC dashboard after market close in the monthly rebalance window.\n"
        "• 🚨 Core/Growth/SPEC critical hard-exit watch after close when portfolio/allocation risk requires it.\n"
        "• 🎯 Swing Alpha entry/exit alerts near the tactical scan/management window when valid.\n"
        "• 🪙 Crypto alerts only when actionable by default; no daily no-action or passive gate-open spam.\n\n"
        "🤖 No broker orders are placed by this bot."
    )[:MAX_TELEGRAM_MESSAGE]


def v483_final_help_text() -> str:
    return (
        "Commands v4.9.7:\n"
        "portfolio | equity | summary | pnl | scanstatus | openrisk | riskmatrix | stressstatus | validationstatus\n"
        "depositcash AMOUNT [note] | depositstatus | showdeposits | download_deposits\n"
        "withdrawplan | withdrawdone AMOUNT [note] | showwithdrawals | download_withdrawals\n"
        "wealthplan | corestatus | coreportfolio | corebuy TICKER QTY at PRICE fee FEE | coresell TICKER QTY at PRICE\n"
        "growthplan | growthstatus | growthportfolio | growthbuy TICKER QTY at PRICE | growthsell TICKER QTY at PRICE\n"
        "specplan | specstatus | specportfolio | specbuy TICKER QTY at PRICE | specsell TICKER QTY at PRICE\n"
        "swingstatus | swingplan | swingportfolio | swingbuy TICKER QTY at PRICE | swingsell TICKER QTY at PRICE\n"
        "cryptostatus | cryptoplan | cryptocheck | cryptoportfolio | cryptobuy TICKER UNITS at PRICE | cryptosell TICKER UNITS at PRICE\n"
        "brokerstatus | brokerreconcile | brokersyncpreview | brokersyncapply CONFIRM\n"
        "monthlydashboard | criticalexitcheck | publicdashboard | testpublic | postchannelterms\n"
        "download_state | download_institutional | panic | resume\n\n"
        "Removed/disabled legacy commands: setcash, bought/sold/editbuy/editsell/voidbuy. Use depositcash and sleeve-specific buy/sell commands."
    )[:MAX_TELEGRAM_MESSAGE]


def format_validation_status() -> str:  # type: ignore[override]
    return (
        "🧪 VALIDATION STATUS v4.9.7\n\n"
        "Strategy: v4.9.4 review-fixed active-only strategy with v4.9.7 depositcash accounting.\n"
        "Allocation: Core 20 / Growth 45 / SPEC 15 / Swing Alpha 10 / Crypto 10\n\n"
        "🧩 v4.9.7 change scope:\n"
        "- Strategy scoring, allocation, scan logic, ledgers, IBKR behavior, and execution remain unchanged.\n"
        "- setcash is disabled. Use depositcash to record manual external deposits.\n"
        "- Deposits increase principal/performance base and adjust withdrawal HWM upward.\n"
        "- Withdrawals are blocked unless there is profit above the deposit-adjusted HWM.\n\n"
        "⚠️ Known limitations:\n"
        "- The strategy remains aggressive and growth/swing/crypto-led; live drawdowns can exceed historical tests.\n"
        "- Swing Alpha MACD+VAH and Crypto BTC/ETH/SOL hybrid require forward validation.\n"
        "- Backtests are not broker-grade execution guarantees.\n"
        "- Manual execution and read-only IBKR reconciliation are still required.\n\n"
        "📣 Automatic alert rules:\n"
        "• Core/Growth/SPEC monthly dashboard is sent automatically in the rebalance window.\n"
        "• Core/Growth/SPEC hard-exit watch is sent automatically after close when risk/allocation is critical.\n"
        "• Crypto auto-alerts are action-only by default: BUY/ADD/TRIM/SELL or action-cleared.\n"
        "• Swing Alpha tactical entries/exits remain automatic near the scan/management windows.\n\n"
        "🤖 No broker order automation in v4.9.7; IBKR reconciliation remains read-only."
    )[:MAX_TELEGRAM_MESSAGE]


# V4.9.7 final command-output label cleanup.
# Some status commands are produced inline by the command router rather than by
# named formatter functions. Intercept them last so all visible labels are v4.8.3.

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

def handle_command(text: str, update_id: Optional[int] = None) -> None:  # type: ignore[override]
    global PANIC_MODE, last_signals, portfolio
    text_clean = (text or "").strip()
    text_lower = text_clean.lower()
    if text_lower == "cryptoportfolio":
        send(format_crypto_portfolio_report())
        return
    if text_lower == "cryptopnl":
        send(format_crypto_pnl_report())
        return
    if text_lower == "cryptoexposure":
        send(format_crypto_exposure_report())
        return

    # ---- merged from handle_command ----
    if text_lower == "equity":
        send(_v483_label_cleanup(_v48_equity_text()))
        return
    if text_lower == "scanstatus":
        send(_v483_label_cleanup(_v48_scanstatus_text()))
        return
    if text_lower == "openrisk":
        send(_v483_label_cleanup(_v48_openrisk_text()))
        return
    if text_lower == "sleevestatus":
        send(_v483_label_cleanup(_v48_sleevestatus_text()))
        return

    # ---- merged from _V483_FINAL_HANDLE_COMMAND ----
    if text_lower in {"v497status", "v496status", "v495status", "v494status", "v483status", "v482status", "v481status", "v48status", "publicstatus", "activeonlystatus", "cleanupstatus", "hotfixstatus", "freezestatus"}:
        send(v483_final_status_text())
        return
    if text_lower in {"help", "/help"}:
        send(v483_final_help_text())
        return
    if text_lower in {"testchannel", "testpublic"}:
        ok, info = send_public_signal("✅ Public channel test from v4.9.7. If you see this, public forwarding works.\n\n" + public_signal_footer())
        send(f"Public test status: {info if ok else 'FAILED - ' + info}")
        return

    # ---- merged from _V483_FINAL_OLD_HANDLE_COMMAND ----
    if text_lower in {"bearstatus", "vcpstatus"}:
        send(
            "ℹ️ V4.9.7 ACTIVE-ONLY STATUS\n\n"
            "Legacy VCP, Bear/inverse, and Options strategies are removed from live operation.\n"
            "Swing Alpha owns the tactical stock signal path.\n\n"
            "Use: swingstatus, swingplan, swingbuy, swingsell."
        )
        return
    if text_lower.split(" ")[0] in {"bought", "sold", "editbuy", "editsell", "voidbuy"}:
        send(
            "❌ Legacy VCP/Bear bought/sold commands are disabled in v4.9.7.\n\n"
            "Use the active ledger commands only:\n"
            "• corebuy / coresell\n"
            "• growthbuy / growthsell\n"
            "• specbuy / specsell\n"
            "• swingbuy / swingsell\n"
            "• cryptobuy / cryptosell"
        )
        return

    # ---- merged from _V483_OLD_HANDLE_COMMAND ----
    if text_lower in {"postchannelterms", "publicterms"}:
        ok, info = send_public_signal(public_channel_terms_text())
        send(f"Public terms status: {info if ok else 'FAILED - ' + info}")
        return
    if text_lower in {"publicdashboard", "publicmonthlydashboard", "testpublicdashboard"}:
        try:
            core_plan, growth_plan, spec_plan, crypto_plan, errors = _v463_prepare_dashboard_plans()
            _v482_send_public_monthly_bundle(core_plan, growth_plan, spec_plan, crypto_plan, errors)
            send("Public dashboard test sent if PUBLIC_SIGNAL_ENABLED=1 and SIGNAL_CHANNEL_ID is configured.")
        except Exception as exc:
            logger.exception(f"[V4.8.2 PUBLIC DASHBOARD TEST ERROR] {exc}")
            send(f"Public dashboard test failed: {exc}")
        return

    # ---- merged from _V481_OLD_HANDLE_COMMAND ----
    first = text_lower.split()[0] if text_lower else ""
    if text_lower == "portfolio":
        send(format_combined_portfolio_report())
        return
    if text_lower in {"bearstatus", "vcpstatus", "vcpscanstatus"}:
        send("ℹ️ v4.8.1: Legacy VCP/Bear/Options strategies were removed from the live bot. Swing Alpha owns the tactical stock signal path.")
        return

    # ---- merged from _V48_OLD_HANDLE_COMMAND ----
    if text_lower in {"v47status", "v471status", "cleanupstatus", "activelevers", "activeledgers"}:
        send(_v483_label_cleanup(_v47_status_text()))
        return

    # ---- merged from _V47_OLD_HANDLE_COMMAND ----
    if text_lower == "cryptostatus":
        send(_v483_label_cleanup(_v463_cryptostatus_text()))
        return
    if text_lower == "cryptoplan":
        send("🪙 Crypto tactical plan started. v4.6.3 scores BTC/ETH/SOL hybrid daily breakout + 4h compression modules.")
        plan = compute_crypto_alpha_plan()
        save_crypto_plan_signal(plan)
        send(format_crypto_alpha_plan(plan))
        return

    # ---- merged from _V463_FINAL_OLD_HANDLE_COMMAND ----
    if text_lower in {"v463status", "dashboardstatus", "v462status", "deploycheck"}:
        send(_v483_label_cleanup(_v463_status_text()))
        return
    if text_lower in {"monthlydashboard", "rebalancedashboard", "dashboard"}:
        send_v463_monthly_dashboard(force=True, preview_only=True)
        return
    if text_lower in {"cryptocheck", "cryptoalert", "cryptoscan"}:
        maybe_send_crypto_alpha_auto_signal(force=True)
        return

    if text_lower in {"monthlyexitcheck", "criticalexitcheck", "hardexitcheck"}:
        sent = maybe_send_monthly_sleeve_critical_exit_signal(force=True)
        if not sent:
            send("✅ No Core/Growth/SPEC hard-exit condition now.")
        return

    # ---- merged from _V462_OLD_HANDLE_COMMAND ----
    if text_lower in {"v461status", "v46status", "v45status", "v443status", "v442status", "v441status", "v44status", "v43status", "coststatus", "hotfixstatus", "monthlylockstatus"}:
        send(_v483_label_cleanup(_v461_status_text()))
        return
    if text_lower == "validationstatus":
        send(format_validation_status())
        return
    if text_lower == "allocationplan":
        send(format_portfolio_allocation_plan())
        return
    if text_lower == "forcescan":
        send("🔎 Manual Swing Alpha scan started. This replaces the disabled VCP/Bear tactical scan path in v4.9.7.")
        ok = scan_swing_alpha_market(force=True, verbose=True)
        send("✅ Manual Swing Alpha scan complete." if ok else "⚠️ Manual Swing Alpha scan did not complete cleanly.")
        return
    if text_lower == "swingplan":
        send("🎯 Swing Alpha plan started. This scans strong leaders for MACD + VAH reclaim swing setups. Live entries are also sent automatically during the tactical scan window.")
        plan = compute_swing_alpha_plan()
        save_swing_alpha_plan(plan)
        send(format_swing_alpha_plan(plan))
        return
    if text_lower == "swingstatus":
        alloc = dynamic_portfolio_allocation_targets()
        latest = load_latest_swing_alpha_signal()
        details = swing_alpha_position_market_value_details()
        m_ok, m_reason = swing_alpha_market_filter_ok()
        send(
            "🎯 SWING_ALPHA STATUS v4.9.7\n\n"
            f"Enabled: {yes_no(SWING_ALPHA_ENABLED)}\n"
            f"Ledger enabled: {yes_no(SWING_ALPHA_LEDGER_ENABLED)}\n"
            f"Live entry/exit signals: {yes_no(SWING_ALPHA_AUTO_SIGNAL_ENABLED)}\n"
            f"Target now: {alloc.get('swing_alpha_pct')}% of account\n"
            "Strategy: MACD + VAH reclaim\n"
            f"Universe size: {len(SWING_ALPHA_UNIVERSE)}\n"
            f"Max signals per scan: {SWING_ALPHA_MAX_SIGNALS_PER_SCAN}\n"
            f"Max positions: {SWING_ALPHA_MAX_OPEN_POSITIONS} | Max per cluster: {SWING_ALPHA_MAX_PER_CLUSTER}\n"
            f"Market filter: {yes_no(m_ok)} — {m_reason}\n"
            f"Swing value: {format_money(float(details.get('value',0) or 0))}\n"
            f"Active plan: {None if latest is None else latest.get('plan_date')}\n\n"
            "Commands:\n"
            "swingplan — manual preview/plan\n"
            "forcescan — manual Swing Alpha signal scan\n"
            "swingbuy TICKER SHARES at PRICE\n"
            "swingsell TICKER SHARES at PRICE\n"
            "swingportfolio | swingpnl | swingexposure"
        )
        return

    # ---- merged from _V461_OLD_HANDLE_COMMAND ----
    if text_lower == "swingportfolio":
        send(format_swing_alpha_portfolio_report())
        return
    if text_lower == "swingpnl":
        send(format_swing_alpha_pnl_report())
        return
    if text_lower == "swingexposure":
        send(format_swing_alpha_exposure_report())
        return
    swing_cmd = re.fullmatch(r"(?i)\s*(swingbuy|swingsell)\s+([A-Z0-9.\-]{1,15})\s+([0-9]+(?:\.[0-9]+)?)\s+(?:at|@)\s+([0-9]+(?:\.[0-9]+)?)(?:\s+(partial))?\s*", text_clean)
    if swing_cmd:
        action = swing_cmd.group(1).lower()
        ticker = normalize_ticker(swing_cmd.group(2))
        shares = float(swing_cmd.group(3))
        price = float(swing_cmd.group(4))
        partial_ok = bool(swing_cmd.group(5))
        if not ticker:
            send("Invalid ticker")
            return
        if action == "swingbuy":
            ok, msg = record_swing_alpha_buy(ticker, shares, price, update_id=update_id, partial_ok=partial_ok)
        else:
            ok, msg = record_swing_alpha_sell(ticker, shares, price, update_id=update_id)
        send(msg if ok else "❌ ERROR: " + msg)
        return

    # ---- merged from _V45_OLD_HANDLE_COMMAND ----


    if text_lower in {"brokerhelp", "ibkrhelp"}:
        send(
            "🏦 IBKR RECONCILIATION COMMANDS v4.4.3\n\n"
            "brokerstatus — fetch/store latest IBKR snapshot and show account summary\n"
            "brokerpositions — show bot-managed positions as seen by IBKR\n"
            "brokerexternal — show external legacy broker positions outside bot scope\n"
            "brokerreconcile — compare IBKR vs bot ledgers\n"
            "brokersyncpreview — preview cash/avg-cost sync for bot-managed positions\n"
            "brokersyncapply CONFIRM — supervised sync of bot cash + matching managed positions from IBKR\n\n"
            "No broker orders are placed in v4.4.3."
        )
        return

    if text_lower in {"brokerping", "bridgeping"}:
        try:
            ok, info, snap = _v42_fetch_snapshot()
            if ok and isinstance(snap, dict):
                conn = snap.get("connection") or {}
                send(
                    "🏓 IBKR BRIDGE PING v4.4.3\n\n"
                    f"Status: ✅ OK\n"
                    f"Source: {info}\n"
                    f"Account: {conn.get('account_selected') or (snap.get('managed_accounts') or ['n/a'])[0]}\n"
                    f"Created UTC: {snap.get('created_utc', 'n/a')}\n"
                    "No broker orders are placed."
                )
            else:
                send(f"🏓 IBKR BRIDGE PING v4.4.3\n\n❌ {info}")
        except Exception as exc:
            send(f"🏓 IBKR BRIDGE PING v4.4.3\n\n❌ {exc}")
        return


    # ---- merged from _V443_OLD_HANDLE_COMMAND ----


    # Confirmed partial-fill recording for monthly ledgers.
    core_partial = re.fullmatch(
        r"(?i)\s*corebuy\s+([A-Z0-9.\-]{1,15})\s+([0-9]+(?:\.[0-9]+)?)\s+(?:at|@)\s+([0-9]+(?:\.[0-9]+)?)(?:\s+fee\s+([0-9]+(?:\.[0-9]+)?))?\s+partial\s*",
        text_clean,
    )
    if core_partial:
        ticker = normalize_ticker(core_partial.group(1))
        if not ticker:
            send("Invalid ticker")
            return
        shares = float(core_partial.group(2)); price = float(core_partial.group(3)); fee = float(core_partial.group(4) or 0.0)
        ok, msg = record_core_buy(ticker, shares, price, update_id=update_id, fee=fee, partial_ok=True)
        send(msg if ok else "❌ ERROR: " + msg)
        return

    monthly_partial = re.fullmatch(
        r"(?i)\s*(growthbuy|specbuy)\s+([A-Z0-9.\-]{1,15})\s+([0-9]+(?:\.[0-9]+)?)\s+(?:at|@)\s+([0-9]+(?:\.[0-9]+)?)\s+partial\s*",
        text_clean,
    )
    if monthly_partial:
        action = monthly_partial.group(1).lower()
        ticker = normalize_ticker(monthly_partial.group(2))
        if not ticker:
            send("Invalid ticker")
            return
        shares = float(monthly_partial.group(3)); price = float(monthly_partial.group(4))
        if action == "growthbuy":
            ok, msg = record_growth_buy(ticker, shares, price, update_id=update_id, partial_ok=True)
        else:
            ok, msg = record_spec_buy(ticker, shares, price, update_id=update_id, partial_ok=True)
        send(msg if ok else "❌ ERROR: " + msg)
        return

    # Manual ledger correction commands. These do not touch cash or trade history.
    edit_cmd = re.fullmatch(
        r"(?i)\s*(editcore|editgrowth|editspec|editcrypto)\s+([A-Z0-9.\-]{1,15})\s+([0-9]+(?:\.[0-9]+)?)\s+(?:at|@)\s+([0-9]+(?:\.[0-9]+)?)\s+CONFIRM\s*",
        text_clean,
    )
    if edit_cmd:
        cmd = edit_cmd.group(1).lower()
        ticker = normalize_ticker(edit_cmd.group(2))
        qty = float(edit_cmd.group(3)); avg = float(edit_cmd.group(4))
        if not ticker:
            send("Invalid ticker")
            return
        note = f"v4.4.2 manual ledger edit at {ny_now().strftime('%Y-%m-%d %H:%M %Z')}"
        if cmd == "editcore":
            ok, msg = _v442_set_monthly_position("core_positions", "core_position_id", ticker, qty, avg, note)
        elif cmd == "editgrowth":
            ok, msg = _v442_set_monthly_position("growth_positions", "growth_position_id", ticker, qty, avg, note)
        elif cmd == "editspec":
            ok, msg = _v442_set_monthly_position("spec_positions", "spec_position_id", ticker, qty, avg, note)
        else:
            ok, msg = _v442_set_crypto_position(ticker, qty, avg, note)
        send(msg if ok else "❌ ERROR: " + msg)
        return


    # ---- merged from _V43_OLD_HANDLE_COMMAND ----

    # New optional-fee Core command:
    # corebuy CMOD.L 2.18 at 33.4225 fee 4
    # corebuyfee CMOD.L 2.18 at 33.4225 fee 4
    core_fee_cmd = re.fullmatch(
        r"(?i)\s*(corebuy|corebuyfee)\s+([A-Z0-9.\-]{1,15})\s+([0-9]+(?:\.[0-9]+)?)\s+(?:at|@)\s+([0-9]+(?:\.[0-9]+)?)\s+(?:fee|fees|commission|comm)\s+([0-9]+(?:\.[0-9]+)?)\s*",
        text_clean,
    )
    if core_fee_cmd:
        ticker = normalize_ticker(core_fee_cmd.group(2))
        shares = float(core_fee_cmd.group(3))
        price = float(core_fee_cmd.group(4))
        fee = float(core_fee_cmd.group(5))
        if not ticker:
            send("Invalid ticker")
            return
        ok, msg = record_core_buy(ticker, shares, price, update_id=update_id, fee=fee)
        send(msg if ok else "❌ ERROR: " + msg)
        return

    if text_lower in {"v42status", "brokerhotfixstatus", "hotfixstatus"}:
        market_ok, reason = growth_alpha_market_filter_ok()
        send(
            "🛠️ V4.3 COST-AWARE / IBKR RECON STATUS\n\n"
            f"Strategy display: {STRATEGY_VERSION}\n"
            f"V4.2 layer: {V42_VERSION}\n"
            f"Growth market filter: {yes_no(market_ok)} — {reason}\n"
            f"SPEC blocklist: {', '.join(sorted(SPEC_ALPHA_BLOCKLIST))}\n"
            f"IBKR recon enabled: {yes_no(IBKR_RECON_ENABLED)}\n"
            f"Bridge URL configured: {yes_no(bool(IBKR_BRIDGE_URL))}\n"
            "Core fee syntax enabled:\n"
            "corebuy TICKER SHARES at FILL_PRICE fee COMMISSION\n"
            "IBKR core symbol aliases enabled for LSE UCITS.\n"
            "Read-only reconciliation only. No broker orders are placed."
        )
        return


    # ---- merged from _V421_OLD_HANDLE_COMMAND ----
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

    # ---- merged from _V42_OLD_HANDLE_COMMAND ----
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

    # ---- merged from _V411_HOTFIX_OLD_HANDLE_COMMAND ----

    # ---- merged from _V411_HOTFIX_OLD_BEAR_STATUS_COMMAND ----


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


    # ---- merged from _V41_OLD_HANDLE_COMMAND ----

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


    # ---- merged from _V310_OLD_HANDLE_COMMAND ----


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


    # ---- merged from _V39_OLD_HANDLE_COMMAND ----

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
    if text_lower == "download_institutional":
        path = download_institutional_report()
        send_document(path, caption="institutional_snapshot.json")
        return


    # ---- merged from _V38_OLD_HANDLE_COMMAND ----
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

    if text_lower.startswith("ensembleplan") or text_lower.startswith("ensemblescan"):
        send("ℹ️ ensembleplan is not part of v4.9.7. This freeze uses v4.9.4 review-fixed strategy logic and keeps Swing Alpha as the 10% tactical sleeve.")
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

    # ---- merged from _old_handle_command ----
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


    if text_lower == "pnl":
        send(format_realized_pnl_report())
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
        send(format_summary_report())
        return

    if text_lower == "resetscan":

        with db_tx() as conn:

            conn.execute(

                "DELETE FROM meta WHERE key IN ('last_scan_day', 'last_scan_bar_date')"

            )

        send("🔄 Scan day reset.\n\nBot may scan again during the scan window.")

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
                "🏦 WITHDRAWAL PLAN v4.9.7\n\n"
                f"💼 Equity: {format_money(plan['equity'])}\n"
                f"💵 Cash: {format_money(plan['cash'])}\n"
                f"➕ Deposited cash: {format_money(plan.get('cash_deposited', plan.get('deposited_cash', 0)))}\n"
                f"➖ Withdrawals: {format_money(plan.get('cash_withdrawn', plan.get('withdrawn_cash', 0)))}\n"
                f"🔁 Net external cash: {format_money(plan.get('net_external_cash', plan.get('net_external_cash_flow', 0)))}\n\n"
                f"⚠️ {plan['reason']}"
            )
            return

        send(
            "🏦 WITHDRAWAL PLAN v4.9.7\n\n"
            f"📊 Phase: {plan['phase']}\n"
            f"💼 Equity: {format_money(plan['equity'])}\n"
            f"💵 Cash: {format_money(plan['cash'])}\n"
            f"➕ Deposited cash: {format_money(plan.get('cash_deposited', plan.get('deposited_cash', 0)))}\n"
            f"➖ Withdrawals: {format_money(plan.get('cash_withdrawn', plan.get('withdrawn_cash', 0)))}\n"
            f"🔁 Net external cash: {format_money(plan.get('net_external_cash', plan.get('net_external_cash_flow', 0)))}\n"
            f"🏔️ Deposit-adjusted high-water mark: {format_money(plan['high_water_mark'])}\n"
            f"📈 Trading profit above HWM: {format_money(plan['profit_above_hwm'])}\n\n"
            f"📤 Withdrawal rate: {round(plan['rate'] * 100, 2)}%\n"
            f"🧮 Gross suggested: {format_money(plan['gross_suggested'])}\n"
            f"💵 Cash cap: {format_money(plan['cash_cap'])}\n"
            f"✅ Suggested withdrawal: {format_money(plan['suggested'])}\n\n"
            f"🗓️ Days since withdrawal/review start: {plan['days_since_clock']}\n"
            f"🚦 Eligible: {yes_no(plan['eligible'])}\n"
            f"ℹ️ Reason: {plan['reason']}\n\n"
            "Deposits are principal, not profit. withdrawdone will reject amounts above trading profit."
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


    if text_lower in {"depositstatus", "cashstatus", "cashaccount", "showdeposits", "deposits", "cashflow", "cashflows"}:
        send(format_cash_deposit_report())
        return

    if text_lower.startswith("depositcash"):
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            send("Usage: depositcash 1000 optional note")
            return
        try:
            amount = float(parts[1])
        except ValueError:
            send("Invalid deposit amount")
            return
        note = parts[2] if len(parts) >= 3 else ""
        ok, msg = record_cash_deposit(amount, note=note, update_id=update_id)
        send(msg if ok else "ERROR: " + msg)
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

    if text_lower == "download_deposits":
        send_json_export(
            load_cash_deposits(),
            "cash_deposits_export.json",
            "cash_deposits_export.json"
        )
        return

    if text_lower == "resetall":

        send(

            "⚠️ DANGEROUS RESET COMMAND\n\n"

            "This will export a backup, then clear:\n"

            "positions, trades, signals, cooldowns, breakout memory, equity snapshots, withdrawals, and cash/deposit ledger.\n\n"

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


    if text_lower.startswith("setcash"):
        send(
            "❌ setcash is disabled in v4.9.7.\n\n"
            "Use depositcash AMOUNT optional note to record external cash deposits.\n"
            "Use brokerreconcile / brokersyncpreview for IBKR reconciliation.\n\n"
            "Reason: direct cash setting can make deposits look like trading profit and distort withdrawal logic."
        )
        return


    trade_cmd = re.fullmatch(

        r"(?i)\s*(bought|sold)\s+([A-Z0-9.\-]{1,15})\s+(\d+)\s+(?:at|@)\s+([0-9]+(?:\.[0-9]+)?)\s*",

        text,

    )

    if not trade_cmd:

        # Preserve original behavior: unknown short/invalid commands are ignored, but alert on likely trade commands.


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


if __name__ == "__main__":
    main()