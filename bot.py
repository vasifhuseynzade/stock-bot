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

 

STRATEGY_VERSION = os.getenv("STRATEGY_VERSION", "v1.4")

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

 

# Telegram safety.

MAX_TELEGRAM_MESSAGE = 3900

 

# -----------------------------------------------------------------------------

# WATCHLIST

# -----------------------------------------------------------------------------

 
STRONG = [
    # Broad / sector ETFs
    "SPY", "QQQ", "IWM",
    "SMH", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP",

    # Mega-cap / institutional leaders
    "MSFT", "NVDA", "META", "AMZN", "GOOGL",
    "AVGO", "AAPL",

    # Financial leaders
    "JPM", "GS", "MS",

    # Industrials / cyclicals
    "CAT", "DE", "GE", "ETN",

    # Health care / defensive growth
    "LLY", "UNH", "ABBV",

    # Consumer quality
    "COST", "WMT", "MCD",
]
 

MEDIUM = [
    # Software / cybersecurity / cloud
    "PANW", "CRWD", "ZS", "NET", "NOW", "PLTR",

    # Semis / hardware beyond mega leaders
    "AMD", "MU", "LRCX", "ASML", "QCOM",

    # Consumer / platforms / cyclicals
    "UBER", "SHOP", "BKNG", "NKE",

    # Financial / fintech / market-sensitive
    "SCHW", "AXP", "COF",

    # Energy / materials
    "XOM", "CVX", "SLB", "FCX",
]
 

WEAK = [
    "COIN", "HOOD", "AFRM", "SOFI",
    "MARA", "RIOT",
    "UPST", "AI",
    "ROKU", "SNOW",
]
 

WATCHLIST = STRONG + MEDIUM + WEAK

 

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
        return "🚀 Breakout"

    if setup == "pullback":
        return "🔁 Pullback"

    return f"⚙️ {setup_type}"


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

    return (
        "📈 ENTRY SIGNAL\n\n"
        f"🏷️ Ticker: {ticker}\n"
        f"🌎 Market: {market_label(market)}\n"
        f"⚙️ Setup: {setup_label(setup)}\n\n"
        f"🟢 ENTRY: {fmt_public_number(entry_data.get('signal_price'))}\n"
        f"🟡 MAX ENTRY LIMIT: {fmt_public_number(entry_data.get('max_valid_entry'))}\n"
        f"🔴 STOP/LOSS: {fmt_public_number(entry_data.get('stop'))}\n\n"
        f"📊 RSI: {fmt_public_number(entry_data.get('rsi'), 1)}\n"
        f"⭐ Score: {entry_data.get('score')}\n"
        f"📊 Volume ratio: {fmt_public_number(entry_data.get('volume_ratio'))}\n\n"
        f"📐 Position size guide: {fmt_public_number(entry_data.get('position_size_pct'))}% of account\n"
        f"⚠️ Trade risk guide: {fmt_public_number(entry_data.get('single_trade_risk_pct'))}% of account\n\n"
        f"{public_signal_footer()}"
    )


def format_public_partial_signal(
    ticker: str,
    price: float,
    trade: Dict[str, Any]
) -> str:
    return (
        "💰 PARTIAL TAKE-PROFIT\n\n"
        f"🏷️ Ticker: {ticker}\n"
        f"💵 Partial exit price: {fmt_public_number(price)}\n"
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
    }.get(str(reason).lower(), str(reason))

    return (
        "📉 EXIT SIGNAL\n\n"
        f"🏷️ Ticker: {ticker}\n"
        f"📌 Reason: {reason_label}\n"
        f"💵 Exit price: {fmt_public_number(price)}\n"
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

 

def market_condition() -> str:

    try:

        spy = get_signal_dataframe("SPY", limit=60)

        qqq = get_signal_dataframe("QQQ", limit=60)

 

        if spy is None or qqq is None or spy.empty or qqq.empty:

            return "UNCERTAIN"

 

        spy_ma50 = spy["Close"].rolling(50).mean().iloc[-1]

        qqq_ma50 = qqq["Close"].rolling(50).mean().iloc[-1]

 

        if pd.isna(spy_ma50) or pd.isna(qqq_ma50):

            return "UNCERTAIN"

 

        if spy["Close"].iloc[-1] > spy_ma50 and qqq["Close"].iloc[-1] > qqq_ma50:

            return "BULL"

        if spy["Close"].iloc[-1] < spy_ma50 and qqq["Close"].iloc[-1] < qqq_ma50:

            return "BEAR"

 

        return "UNCERTAIN"

 

    except Exception as exc:

        print(f"[MARKET ERROR] {exc}")

        return "UNCERTAIN"

 

 

def compute_equity_snapshot_data() -> Dict[str, float]:

    refresh_portfolio()

    positions = portfolio["positions"]

    prices = get_prices_batch(list(positions.keys()))

 

    market_value = 0.0

    for ticker, pos in positions.items():

        price = prices.get(ticker, pos["price"])

        market_value += price * pos["shares"]

 

    equity = portfolio["cash"] + market_value

    return {

        "cash": round(portfolio["cash"], 2),

        "positions_value": round(market_value, 2),

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

 

    equity = portfolio["cash"]

    initial_risk_dollars = 0.0

    current_stop_risk_dollars = 0.0

 

    for ticker, pos in positions.items():

        current_price = prices.get(ticker, pos["price"])

        equity += current_price * pos["shares"]

 

        risk_per_share = pos.get("risk_per_share")

        if isinstance(risk_per_share, (int, float)) and risk_per_share > 0:

            initial_risk_dollars += risk_per_share * pos["shares"]

 

        current_stop_risk = max(0.0, current_price - pos.get("stop", current_price))

        current_stop_risk_dollars += current_stop_risk * pos["shares"]

 

    if equity <= 0:

        return {

            "equity": 0.0,

            "initial_risk_dollars": 0.0,

            "current_stop_risk_dollars": 0.0,

            "initial_risk_pct": 0.0,

            "current_stop_risk_pct": 0.0,

        }

 

    return {

        "equity": round(equity, 2),

        "initial_risk_dollars": round(initial_risk_dollars, 2),

        "current_stop_risk_dollars": round(current_stop_risk_dollars, 2),

        "initial_risk_pct": initial_risk_dollars / equity,

        "current_stop_risk_pct": current_stop_risk_dollars / equity,

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
 

def risk_pct_for_ticker(ticker: str) -> Optional[float]:

    if ticker in STRONG:

        return 0.03

    if ticker in MEDIUM:

        return 0.02

    if ticker in WEAK:

        return 0.01

    return None

 

 

def approximate_equity_from_portfolio() -> float:

    refresh_portfolio()

    positions = portfolio["positions"]

    prices = get_prices_batch(list(positions.keys()))

    equity = float(portfolio["cash"])

    for ticker, pos in positions.items():
        mark = prices.get(ticker, pos["price"])
        equity += float(mark) * int(pos["shares"])

    return equity
 
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

    if isinstance(signal_price, (int, float)) and signal_price > 0:
        max_allowed_price = float(signal_price) * (1 + MAX_ENTRY_EXTENSION_PCT)

        if price > max_allowed_price:
            return (
                False,
                f"Entry rejected: price too extended above signal.\n"
                f"Entry signal: {round(float(signal_price), 2)}\n"
                f"Your price: {round(price, 2)}\n"
                f"Max entry limit: {round(max_allowed_price, 2)} "
                f"({round(MAX_ENTRY_EXTENSION_PCT * 100, 2)}% above signal)"
            )

    atr_val: Optional[float] = None
    stop: Optional[float] = None

    signal_atr = signal_data.get("atr")

    if isinstance(signal_atr, (int, float)) and signal_atr > 0:
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

        "strategy_version": STRATEGY_VERSION,

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


def write_json_file(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(safe_convert(data), f, indent=2)


def table_rows(table: str) -> List[Dict[str, Any]]:
    allowed = {
        "positions",
        "trades",
        "signals",
        "equity_snapshots",
        "withdrawals",
        "cooldowns",
        "breakout_memory",
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
            "cash": portfolio["cash"],
            "panic_mode": PANIC_MODE,
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
                'last_withdrawal_alert_ts'
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
        f"📦 Positions: {len(portfolio['positions'])}\n\n"
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

            "pnl | equity | openrisk | winrate | expectancy | stats | duration | summary | portfolio | scanstatus\n"
            "setupstats | showtrades | showsignals | resetsignals | resetscan | forcescan | download_trades\n"
            "testchannel | postchannelterms\n"
            "download_state | download_portfolio | download_signals | download_withdrawals\n"
            "withdrawinit | withdrawplan | withdrawdone AMOUNT | showwithdrawals\n"
            "resetall  (then resetall CONFIRM-LIVE)\n"
            "setcash AMOUNT\n"
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

        send(f"📊 Weekly P/L: {format_money(weekly_performance())}")

        return

 

    if text_lower == "equity":

        snapshot = compute_equity_snapshot_data()

        send(
            "💼 ACCOUNT EQUITY\n\n"
            f"💵 Cash: {format_money(snapshot['cash'])}\n"
            f"📦 Positions: {format_money(snapshot['positions_value'])}\n"
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

        pnl = weekly_performance()

        wr = win_rate()

        best, worst = ticker_stats()

        duration = avg_trade_duration()

        e = expectancy_summary()

        send(
            "📋 SUMMARY\n\n"
            f"📊 P/L (7d): {format_money(pnl)}\n"
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

        refresh_portfolio()

        cash = portfolio["cash"]

        positions = portfolio["positions"]

        if not positions:

            send(f"📋 PORTFOLIO\n\n💵 Cash: {format_money(cash)}\nNo open positions")

            return


        prices = get_prices_batch(list(positions.keys()))

        msg = f"PORTFOLIO\n\nCash: {format_money(cash)}\n\n"

        for ticker, pos in positions.items():

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

        send(msg)

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

 

def analyze(ticker: str, market: str, df: pd.DataFrame) -> Optional[Tuple[str, float, int, float, int]]:

    try:

        if df is None or df.empty:

            print(f"[ANALYZE SKIP] {ticker} - no data")

            return None

    except Exception as exc:

        print(f"[ANALYZE ERROR] {ticker}: {exc}")

        return None

 

    close = df["Close"]

    volume = df["Volume"]

 

    if len(close) < 51:

        return None

 

    price = float(close.iloc[-1])

    rsi_val = rsi(close).iloc[-1]

    ma20 = close.rolling(20).mean().iloc[-1]

    ma50 = close.rolling(50).mean().iloc[-1]

    avg_vol = volume.rolling(20).mean().iloc[-1]

 

    if pd.isna(rsi_val) or pd.isna(ma20) or pd.isna(ma50) or pd.isna(avg_vol):

        return None

    if ma20 == 0 or avg_vol <= 0:

        return None

 

    recent_high = close.iloc[-21:-1].max()

    breakout = price > recent_high

 

    # Original market logic, with risk-control improvement for weak names in uncertain market.

    if market == "BEAR":

        return None

 

    if market == "UNCERTAIN" and ticker in WEAK:

        return None

 

    if not breakout and rsi_val > 70:

        return None

 

    if not breakout and (price - ma20) / ma20 > 0.05:

        return None

 

    prev_close = float(close.iloc[-2])

    daily_move_pct = ((price - prev_close) / prev_close) * 100

    if breakout and daily_move_pct > 8:
        return None

    if not breakout and price <= prev_close:

        return None

 

    score = 0

    if price > ma50:

        score += 20

    if price < ma20 and rsi_val < 45:

        score += 30

    if volume.iloc[-1] > avg_vol * 1.5:

        score += 20

 

    if breakout:

        score += 20

        if price > ma20:

            score += 10

        if ma20 > ma50:

            score += 10

 

    if breakout and volume.iloc[-1] < avg_vol * 1.2:

        return None

 

    if breakout and rsi_val > 85:

        return None

 

    if score < 40:

        return None

 

    if not breakout and ma20 < ma50:

        return None

 

    atr_val = atr(df).iloc[-1]

    if pd.isna(atr_val) or not is_finite_positive(float(atr_val)):

        return None

 

    stop = price - (1.5 * float(atr_val))

    risk = price - stop

    if risk <= 0 or stop <= 0:

        return None

 

    risk_pct = risk_pct_for_ticker(ticker)

    if risk_pct is None:

        return None

 

    refresh_portfolio()

    account_equity = approximate_equity_from_portfolio()

    available_cash = float(portfolio["cash"])

 

    shares_by_risk = int((account_equity * risk_pct) / risk)

    shares_by_position_cap = int((account_equity * MAX_POSITION_EQUITY_PCT) / price)

    shares_by_cash = int((available_cash * CASH_USAGE_BUFFER) / price)

    shares = min(shares_by_risk, shares_by_position_cap, shares_by_cash)

 

    if shares <= 0:

        return None

 

    return ticker, price, shares, stop, score

 

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

 

        # Compute effective stop first. This fixes the gap-through-trailing-stop bug.

        effective_stop = float(pos["stop"])

        atr_val = pos.get("atr")

        if isinstance(atr_val, (int, float)) and atr_val > 0:

            # Preserve original intent: tighter trail once price is up 5%.

            multiplier = 2.0 if price >= entry * 1.05 else 2.5

            theoretical_trail = float(pos["highest"]) - (multiplier * float(atr_val))

            if theoretical_trail > effective_stop:

                effective_stop = theoretical_trail

 

        # Breakeven rule, upgraded to R-based trigger from the prior version.

        if trade_r is not None and trade_r >= 0.7 and effective_stop < entry:

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
                send(
                    f"📉 EXIT {ticker}\n"
                    f"💵 Exit price: {round(fill_price, 2)}\n"
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

 

        # Partial take-profit logic. Core behavior preserved: +8% OR +1R.

        partial_trigger = (price >= entry * 1.08) or (trade_r is not None and trade_r >= 1.0)

        if partial_trigger and not pos.get("partial_taken", False) and int(pos["shares"]) > 1:

            sell_shares = int(pos["shares"]) // 2

            trade = record_auto_exit_or_partial(

                ticker=ticker,

                shares=sell_shares,

                price=price,

                exit_reason="partial",

                updated_fields={"highest": pos["highest"], "stop": pos["stop"]},

            )

            if trade:

                send(
                    f"💰 PARTIAL {ticker}\n"
                    f"Shares: {sell_shares}\n"
                    f"💵 Partial exit price: {round(price, 2)}\n"
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
        "signals_sent": 0,
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

        # Prefer bar-date cleanup. This keeps Saturday signals valid for Monday
        # if both are based on the same Friday daily candle.
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

    market = market_condition()

    market_emoji = "🟡"

    if market == "BULL":
        market_emoji = "🐂"

    elif market == "BEAR":
        market_emoji = "🐻"

    print(f"{market_emoji} MARKET | {market}")


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

    for ticker in WATCHLIST:

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
 

            earnings = earnings_status(ticker, days=7)

            if earnings == "SOON":
                skip_counts["earnings_soon"] += 1
                print(f"[SKIP EARNINGS] {ticker}")
                continue

            if earnings == "UNKNOWN" and FAIL_CLOSED_ON_EARNINGS_UNKNOWN:
                skip_counts["earnings_unknown"] += 1
                print(f"[SKIP EARNINGS UNKNOWN] {ticker}")
                continue
 

            attempted_historical += 1

            df = get_signal_dataframe(ticker, limit=120)

            if df is None or df.empty or len(df) < 51:
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
 

            rsi_val = rsi(close).iloc[-1]

            if pd.isna(rsi_val):

                continue

 

            risk_details = base_risk_details

            if risk_details["equity"] <= 0:

                continue

            if (risk_details["initial_risk_dollars"] + reserved_signal_risk) / risk_details["equity"] >= MAX_TOTAL_RISK:
                skip_counts["risk_cap"] += 1
                continue
 

            result = analyze(ticker, market, df)

            if not result:
                skip_counts["strategy_filter"] += 1
                continue
 

            ticker, price, shares, stop, score = result

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

            max_valid_entry = price * (1 + MAX_ENTRY_EXTENSION_PCT)

            projected_risk_pct = (risk_details["initial_risk_dollars"] + reserved_signal_risk + risk_amount) / risk_details["equity"]
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
 

            avg_vol = df["Volume"].rolling(20).mean().iloc[-1]

            volume_ratio = None

            if avg_vol and avg_vol > 0:

                volume_ratio = df["Volume"].iloc[-1] / avg_vol

 

            recent_high = df["Close"].iloc[-21:-1].max()

            is_breakout = bool(price > recent_high)

            entry_data = {

                "rsi": round(float(rsi_val), 2),
                "score": int(score),
                "market": market,
                "atr": round((price - stop) / 1.5, 4),
                "stop": round(stop, 2),
                "breakout": is_breakout,
                "setup_type": "breakout" if is_breakout else "pullback",
                "volume_ratio": None if volume_ratio is None else round(float(volume_ratio), 2),
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
                "projected_total_risk_pct": round(projected_risk_pct * 100, 2)

            }

 

            send(
                "📈 ENTRY SIGNAL\n\n"
                f"🏷️ Ticker: {ticker}\n"
                f"🌎 Market: {market_label(market)}\n"
                f"⚙️ Setup: {setup_label(entry_data['setup_type'])}\n\n"
                f"🟢 ENTRY: {round(price, 2)}\n"
                f"🟡 MAX ENTRY LIMIT: {round(max_valid_entry, 2)}\n"
                f"🔴 STOP/LOSS: {round(stop, 2)}\n\n"
                f"📊 RSI: {round(float(rsi_val), 1)}\n"
                f"⭐ Score: {score}\n\n"
                f"🛒 Bot buy size: {shares} shares\n"
                f"💰 Capital: {format_money(capital)}\n"
                f"📐 Position size: {round(position_size_pct, 2)}% of equity\n"
                f"🧭 Sizing guide: use about {round(position_size_pct, 2)}% of your own account\n\n"
                f"⚠️ Trade risk: {format_money(risk_amount)} "
                f"({round(single_trade_risk_pct, 2)}% of equity)\n"
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

            reserved_signal_risk += risk_amount

            reserved_signal_capital += capital

 

        except Exception as exc:

            logger.exception(f"[❌ SCAN ERROR] {ticker}: {exc}")

            traceback.print_exc()

            send(f"WARNING: scan error for {ticker}: {exc}")

    refresh_portfolio()

    print(
        "[SCAN SUMMARY] "
        f"usable_data_found={usable_data_found} | "
        f"attempted_historical={attempted_historical} | "
        f"positions={len(portfolio['positions'])}/{MAX_OPEN_POSITIONS} | "
        f"cash={round(portfolio['cash'], 2)} | "
        f"skips={skip_counts}"
    )

    # If at least one ticker reached usable historical data, the scan worked.
    if usable_data_found:
        return True

    # If no ticker even reached historical data, this was not a data freshness problem.
    # It was blocked before data fetch, usually by full portfolio, cash, cooldown, or existing signals.
    if attempted_historical == 0:
        print("[SCAN COMPLETE] No eligible tickers reached historical-data check.")
        return True

    # Historical data was attempted but unusable. Retry later.
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

 

 

if __name__ == "__main__":

    main()