from __future__ import annotations

import argparse
import json
import math
import os
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

# -----------------------------------------------------------------------------
# CONFIG / WATCHLIST
# -----------------------------------------------------------------------------

DATA_DIR = os.getenv("DATA_DIR", "./data")
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FMP_BASE = os.getenv("FMP_BASE", "https://financialmodelingprep.com/stable")

STRONG = [
    "SPY", "QQQ", "IWM", "SMH", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP",
    "MSFT", "NVDA", "META", "AMZN", "GOOGL", "AVGO", "AAPL",
    "JPM", "GS", "MS", "CAT", "DE", "GE", "ETN",
    "LLY", "UNH", "ABBV", "COST", "WMT", "MCD",
]

MEDIUM = [
    "PANW", "CRWD", "ZS", "NET", "NOW", "PLTR",
    "AMD", "MU", "LRCX", "ASML", "QCOM",
    "UBER", "SHOP", "BKNG", "NKE",
    "SCHW", "AXP", "COF",
    "XOM", "CVX", "SLB", "FCX",
]

WEAK = [
    "COIN", "HOOD", "AFRM", "SOFI", "MARA", "RIOT", "UPST", "AI", "ROKU", "SNOW",
]

WATCHLIST = STRONG + MEDIUM + WEAK
MARKET_TICKERS = ["SPY", "QQQ", "IWM", "SMH"]
ALL_TICKERS = sorted(set(WATCHLIST + MARKET_TICKERS))

SESSION = requests.Session()


@dataclass(frozen=True)
class StrategyConfig:
    name: str = "v2_1_candidate"
    initial_capital: float = 4000.0
    max_open_positions: int = 12
    max_total_risk: float = 0.06
    max_position_equity_pct: float = 0.20
    cash_usage_buffer: float = 0.98
    max_entry_extension_pct: float = 0.01

    # V2.1 scoring engine.
    min_market_score: int = 3
    min_score: int = 68
    breakout_min_score: int = 74
    pullback_min_score: int = 72
    weak_min_score: int = 80
    max_signals_per_scan: int = 6

    # Liquidity / volatility filters.
    min_price: float = 8.0
    min_avg_dollar_volume: float = 30_000_000.0
    min_atr_pct: float = 0.01
    max_atr_pct: float = 0.12
    max_risk_per_share_pct: float = 0.10

    # Setup behavior.
    breakout_min_volume_ratio: float = 1.15
    pullback_min_volume_ratio: float = 0.30
    breakout_max_rsi: float = 82.0
    breakout_max_daily_move_pct: float = 7.0
    pullback_max_daily_move_pct: float = 6.0
    block_pullback_in_uncertain: bool = True
    pullback_require_positive_rs: bool = True
    breakout_require_positive_rs: bool = True

    # Stop model.
    breakout_atr_stop_mult: float = 1.80
    pullback_atr_stop_mult: float = 1.80
    stop_wider_of_atr_and_structure: bool = True

    # Exit behavior.
    breakeven_r: float = 0.70
    partial_r: float = 1.0
    partial_pct: float = 0.08
    trail_mult_before_5pct: float = 2.8
    trail_mult_after_5pct: float = 2.2

    # Risk boost.
    risk_boost_enabled: bool = True
    a_plus_risk_boost: float = 1.25
    a_risk_boost: float = 1.10

    allow_weak: bool = True
    slippage_bps: float = 10.0
    commission_per_trade: float = 0.0


def ensure_dirs() -> Dict[str, str]:
    paths = {
        "data": DATA_DIR,
        "cache": os.path.join(DATA_DIR, "backtest_cache", "eod"),
        "runs": os.path.join(DATA_DIR, "backtests"),
    }
    for path in paths.values():
        os.makedirs(path, exist_ok=True)
    return paths


# -----------------------------------------------------------------------------
# UTILITIES
# -----------------------------------------------------------------------------


def safe_convert(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: safe_convert(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [safe_convert(v) for v in obj]
    if isinstance(obj, tuple):
        return [safe_convert(v) for v in obj]
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if hasattr(obj, "item"):
        return obj.item()
    return obj


def write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(safe_convert(data), f, indent=2)


def money(value: float) -> str:
    return f"${round(float(value), 2)}"


def pct(value: Optional[float]) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    sign = "+" if value >= 0 else ""
    return f"{sign}{round(value, 2)}%"


def max_drawdown(equity: pd.Series) -> Tuple[float, float]:
    if equity.empty:
        return 0.0, 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    max_dd_pct = float(drawdown.min() * 100)
    max_dd_dollars = float((equity - running_max).min())
    return round(max_dd_pct, 2), round(max_dd_dollars, 2)


def profit_factor(values: pd.Series) -> Optional[float]:
    if values.empty:
        return None
    gross_profit = float(values[values > 0].sum())
    gross_loss = abs(float(values[values < 0].sum()))
    if gross_loss == 0:
        return None if gross_profit == 0 else 999.0
    return round(gross_profit / gross_loss, 3)


def ticker_bucket(ticker: str) -> str:
    if ticker in STRONG:
        return "STRONG"
    if ticker in MEDIUM:
        return "MEDIUM"
    if ticker in WEAK:
        return "WEAK"
    return "UNKNOWN"


def risk_pct_for_ticker(ticker: str) -> Optional[float]:
    if ticker in STRONG:
        return 0.03
    if ticker in MEDIUM:
        return 0.02
    if ticker in WEAK:
        return 0.01
    return None


# -----------------------------------------------------------------------------
# FMP DATA FETCHING / CACHE
# -----------------------------------------------------------------------------


def request_json(url: str, context: str, retries: int = 2, timeout: Tuple[int, int] = (5, 30)) -> Any:
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            res = SESSION.get(url, timeout=timeout)
            res.raise_for_status()
            return res.json()
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"{context}: {last_exc}") from last_exc


def normalize_eod_payload(payload: Any, ticker: str) -> pd.DataFrame:
    if isinstance(payload, dict) and "historical" in payload:
        payload = payload["historical"]
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"No EOD rows returned for {ticker}")

    df = pd.DataFrame(payload)
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    required = ["date", "Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{ticker} missing columns: {missing}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=required)
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    return df


def fetch_eod(ticker: str, refresh_cache: bool = False) -> pd.DataFrame:
    paths = ensure_dirs()
    cache_path = os.path.join(paths["cache"], f"{ticker}.json")

    if os.path.exists(cache_path) and not refresh_cache:
        with open(cache_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return normalize_eod_payload(payload, ticker)

    url = f"{FMP_BASE}/historical-price-eod/full?symbol={ticker}&apikey={FMP_API_KEY}"
    payload = request_json(url, context=f"EOD {ticker}")

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(payload, f)

    return normalize_eod_payload(payload, ticker)


def load_all_data(start: date, end: date, refresh_cache: bool = False) -> Dict[str, pd.DataFrame]:
    if not FMP_API_KEY:
        raise RuntimeError("FMP_API_KEY is missing. Set it before running the backtest.")

    # Need extra lookback for MA50, RSI, ATR, and recent high calculations.
    hist_start = start - timedelta(days=260)
    data: Dict[str, pd.DataFrame] = {}

    for i, ticker in enumerate(ALL_TICKERS, start=1):
        print(f"[{i}/{len(ALL_TICKERS)}] loading {ticker}")
        try:
            df = fetch_eod(ticker, refresh_cache=refresh_cache)
            df = df[(df["date"] >= hist_start) & (df["date"] <= end)].copy()
            if len(df) < 80:
                print(f"[WARN] {ticker}: only {len(df)} rows after filtering")
            df = add_indicators(df)
            data[ticker] = df.set_index("date", drop=False)
        except Exception as exc:
            print(f"[DATA ERROR] {ticker}: {exc}")

    for required in MARKET_TICKERS:
        if required not in data or data[required].empty:
            raise RuntimeError(f"Required market ticker {required} not available")

    return data


# -----------------------------------------------------------------------------
# INDICATORS / STRATEGY
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


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["PrevClose"] = df["Close"].shift(1)
    df["RSI14"] = rsi(df["Close"])
    df["MA10"] = df["Close"].rolling(10).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA50"] = df["Close"].rolling(50).mean()
    df["MA100"] = df["Close"].rolling(100).mean()
    df["AvgVol20"] = df["Volume"].rolling(20).mean()
    df["ATR14"] = atr(df)
    df["RecentHigh20"] = df["Close"].shift(1).rolling(20).max()
    df["RecentHigh55"] = df["Close"].shift(1).rolling(55).max()
    df["DailyMovePct"] = ((df["Close"] - df["PrevClose"]) / df["PrevClose"]) * 100
    return df


def pct_change_on_day(df: pd.DataFrame, day: date, bars: int) -> Optional[float]:
    try:
        if day not in df.index:
            return None
        idx = df.index.get_loc(day)
        if isinstance(idx, slice) or isinstance(idx, list):
            return None
        if idx < bars:
            return None
        new = float(df.iloc[idx]["Close"])
        old = float(df.iloc[idx - bars]["Close"])
        if old <= 0:
            return None
        return (new / old) - 1
    except Exception:
        return None


def rolling_slope_positive_on_day(df: pd.DataFrame, day: date, column: str = "MA50", lookback: int = 10) -> bool:
    try:
        if day not in df.index:
            return False
        idx = df.index.get_loc(day)
        if idx < lookback:
            return False
        now = df.iloc[idx][column]
        then = df.iloc[idx - lookback][column]
        if pd.isna(now) or pd.isna(then):
            return False
        return float(now) > float(then)
    except Exception:
        return False


def close_location_row(row: pd.Series) -> float:
    try:
        high = float(row["High"])
        low = float(row["Low"])
        close = float(row["Close"])
        if high <= low:
            return 0.5
        return max(0.0, min(1.0, (close - low) / (high - low)))
    except Exception:
        return 0.5


def market_regime_details_on(day: date, data: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    score = 0
    notes: List[str] = []

    for symbol in ["SPY", "QQQ"]:
        df = data.get(symbol)
        if df is None or day not in df.index:
            notes.append(f"{symbol}:no_data")
            continue
        row = df.loc[day]
        if pd.isna(row.get("MA20")) or pd.isna(row.get("MA50")):
            notes.append(f"{symbol}:ma_nan")
            continue
        if float(row["Close"]) > float(row["MA50"]):
            score += 1
        if float(row["MA20"]) > float(row["MA50"]):
            score += 1
        if rolling_slope_positive_on_day(df, day, "MA50", 10):
            score += 1

    for symbol in ["IWM", "SMH"]:
        df = data.get(symbol)
        if df is None or day not in df.index:
            continue
        row = df.loc[day]
        if not pd.isna(row.get("MA50")) and float(row["Close"]) > float(row["MA50"]):
            score += 1

    if score >= 6:
        condition = "BULL"
    elif score <= 2:
        condition = "BEAR"
    else:
        condition = "UNCERTAIN"

    return {"condition": condition, "score": score, "max_score": 8, "notes": notes}


def market_condition_on(day: date, data: Dict[str, pd.DataFrame]) -> str:
    try:
        return str(market_regime_details_on(day, data).get("condition", "UNCERTAIN"))
    except Exception:
        return "UNCERTAIN"


def analyze_signal(ticker: str, day: date, data: Dict[str, pd.DataFrame], market: str, cfg: StrategyConfig) -> Optional[Dict[str, Any]]:
    if not cfg.allow_weak and ticker in WEAK:
        return None
    if ticker not in data or day not in data[ticker].index:
        return None

    df = data[ticker]
    row = df.loc[day]
    fields = ["Close", "PrevClose", "RSI14", "MA10", "MA20", "MA50", "AvgVol20", "ATR14", "RecentHigh20", "RecentHigh55", "Volume"]
    if any(pd.isna(row[f]) for f in fields):
        return None

    price = float(row["Close"])
    prev_close = float(row["PrevClose"])
    rsi_val = float(row["RSI14"])
    ma10 = float(row["MA10"])
    ma20 = float(row["MA20"])
    ma50 = float(row["MA50"])
    ma100 = None if pd.isna(row.get("MA100")) else float(row.get("MA100"))
    avg_vol = float(row["AvgVol20"])
    atr_val = float(row["ATR14"])
    recent_high_20 = float(row["RecentHigh20"])
    recent_high_55 = float(row["RecentHigh55"])
    volume = float(row["Volume"])
    high = float(row["High"])
    low = float(row["Low"])

    if min(price, prev_close, ma10, ma20, ma50, avg_vol, atr_val, recent_high_20, recent_high_55) <= 0:
        return None

    atr_pct = atr_val / price
    avg_dollar_volume = avg_vol * price
    daily_move_pct = ((price - prev_close) / prev_close) * 100
    volume_ratio = volume / avg_vol if avg_vol > 0 else None
    close_loc = close_location_row(row)

    if price < cfg.min_price:
        return None
    if avg_dollar_volume < cfg.min_avg_dollar_volume:
        return None
    if atr_pct < cfg.min_atr_pct or atr_pct > cfg.max_atr_pct:
        return None

    market_details = market_regime_details_on(day, data)
    market_score = int(market_details.get("score", 0) or 0)
    if market == "BEAR" or market_score < cfg.min_market_score:
        return None

    stock_ret_20 = pct_change_on_day(df, day, 20)
    stock_ret_63 = pct_change_on_day(df, day, 63)
    spy_ret_20 = pct_change_on_day(data["SPY"], day, 20) if "SPY" in data else None
    spy_ret_63 = pct_change_on_day(data["SPY"], day, 63) if "SPY" in data else None
    qqq_ret_20 = pct_change_on_day(data["QQQ"], day, 20) if "QQQ" in data else None

    rel_20 = None if stock_ret_20 is None or spy_ret_20 is None else stock_ret_20 - spy_ret_20
    rel_63 = None if stock_ret_63 is None or spy_ret_63 is None else stock_ret_63 - spy_ret_63
    rel_qqq_20 = None if stock_ret_20 is None or qqq_ret_20 is None else stock_ret_20 - qqq_ret_20

    trend_score = 0
    if price > ma50:
        trend_score += 12
    if ma20 > ma50:
        trend_score += 10
    if price > ma20:
        trend_score += 6
    if price > ma10:
        trend_score += 4
    if ma100 is not None and price > ma100:
        trend_score += 6
    if rolling_slope_positive_on_day(df, day, "MA50", 10):
        trend_score += 10
    if close_loc >= 0.55:
        trend_score += 5
    if avg_dollar_volume >= 100_000_000:
        trend_score += 5

    rs_score = 0
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

    breakout_20 = price > recent_high_20
    breakout_55 = price > recent_high_55
    pullback_distance_ma20 = (price - ma20) / ma20
    touched_ma20_area = low <= ma20 * 1.035

    breakout_score = -999
    if breakout_20 or breakout_55:
        breakout_score = trend_score + rs_score
        if breakout_20:
            breakout_score += 8
        if breakout_55:
            breakout_score += 12
        if volume_ratio is not None and volume_ratio >= cfg.breakout_min_volume_ratio:
            breakout_score += 8
        if volume_ratio is not None and volume_ratio >= 1.5:
            breakout_score += 5
        if close_loc >= 0.60:
            breakout_score += 7
        if 50 <= rsi_val <= cfg.breakout_max_rsi:
            breakout_score += 6
        if 0 < daily_move_pct <= cfg.breakout_max_daily_move_pct:
            breakout_score += 5
        if ma20 > ma50:
            breakout_score += 5
        if rsi_val > cfg.breakout_max_rsi:
            breakout_score = -999
        if daily_move_pct > cfg.breakout_max_daily_move_pct:
            breakout_score = -999
        if volume_ratio is None or volume_ratio < cfg.breakout_min_volume_ratio:
            breakout_score = -999
        if not (price > ma20 > ma50):
            breakout_score = -999

    pullback_score = -999
    pullback_candidate = (
        price > ma50
        and ma20 >= ma50 * 0.995
        and price > prev_close
        and -0.06 <= pullback_distance_ma20 <= 0.06
        and touched_ma20_area
        and daily_move_pct <= cfg.pullback_max_daily_move_pct
        and volume_ratio is not None
        and volume_ratio >= cfg.pullback_min_volume_ratio
    )

    if pullback_candidate:
        pullback_score = trend_score + rs_score
        if 32 <= rsi_val <= 58:
            pullback_score += 10
        if 35 <= rsi_val <= 50:
            pullback_score += 5
        if price > prev_close:
            pullback_score += 8
        if close_loc >= 0.45:
            pullback_score += 6
        if abs(pullback_distance_ma20) <= 0.03:
            pullback_score += 7
        if low <= ma20 * 1.02:
            pullback_score += 5
        if volume_ratio <= 1.30:
            pullback_score += 4
        if stock_ret_63 is not None and stock_ret_63 > 0:
            pullback_score += 4

    if breakout_score >= pullback_score:
        setup_type = "breakout"
        score = int(round(breakout_score))
        min_score = cfg.breakout_min_score
        is_breakout = True
        breakout_level = recent_high_55 if breakout_55 else recent_high_20
    else:
        setup_type = "pullback"
        score = int(round(pullback_score))
        min_score = cfg.pullback_min_score
        is_breakout = False
        breakout_level = recent_high_20

    min_score = max(min_score, cfg.min_score)

    if ticker in WEAK:
        if not cfg.allow_weak:
            return None
        min_score = max(min_score, cfg.weak_min_score)

    if market == "UNCERTAIN":
        min_score += 5

    if setup_type == "pullback":
        if cfg.block_pullback_in_uncertain and market != "BULL":
            return None
        if cfg.pullback_require_positive_rs:
            if not ((rel_20 is not None and rel_20 > 0) or (rel_63 is not None and rel_63 > 0)):
                return None

    if setup_type == "breakout" and cfg.breakout_require_positive_rs:
        if not ((rel_20 is not None and rel_20 > 0) or (rel_63 is not None and rel_63 > 0)):
            return None

    if score < min_score:
        return None

    if setup_type == "breakout":
        atr_stop = price - (cfg.breakout_atr_stop_mult * atr_val)
        structure_stop = breakout_level - (0.35 * atr_val)
        stop = min(atr_stop, structure_stop) if cfg.stop_wider_of_atr_and_structure else max(atr_stop, structure_stop)
        stop_model = "breakout_wider_structure_atr" if cfg.stop_wider_of_atr_and_structure else "breakout_tighter_structure_atr"
    else:
        try:
            idx = df.index.get_loc(day)
            recent_swing_low = float(df.iloc[max(0, idx - 5): idx + 1]["Low"].min())
        except Exception:
            recent_swing_low = low
        atr_stop = price - (cfg.pullback_atr_stop_mult * atr_val)
        structure_stop = recent_swing_low - (0.25 * atr_val)
        stop = min(atr_stop, structure_stop) if cfg.stop_wider_of_atr_and_structure else max(atr_stop, structure_stop)
        stop_model = "pullback_wider_swing_atr" if cfg.stop_wider_of_atr_and_structure else "pullback_tighter_swing_atr"

    if stop <= 0 or stop >= price:
        fallback_mult = cfg.breakout_atr_stop_mult if setup_type == "breakout" else cfg.pullback_atr_stop_mult
        stop = price - (fallback_mult * atr_val)
        stop_model = "fallback_v21_atr"

    risk_per_share = price - stop
    if stop <= 0 or risk_per_share <= 0:
        return None
    if risk_per_share / price > cfg.max_risk_per_share_pct:
        return None

    risk_pct_used = risk_pct_for_ticker(ticker)
    if risk_pct_used is None:
        return None
    if cfg.risk_boost_enabled:
        if score >= 88:
            risk_pct_used *= cfg.a_plus_risk_boost
        elif score >= 80:
            risk_pct_used *= cfg.a_risk_boost

    max_valid_entry = min(
        price * (1 + cfg.max_entry_extension_pct),
        price + (0.35 * atr_val),
    )

    return {
        "ticker": ticker,
        "signal_date": day.isoformat(),
        "signal_price": round(price, 4),
        "max_valid_entry": round(max_valid_entry, 4),
        "stop": round(stop, 4),
        "atr": round(atr_val, 4),
        "risk_per_share_at_signal": round(risk_per_share, 4),
        "rsi": round(rsi_val, 2),
        "score": int(score),
        "min_score_required": int(min_score),
        "market": market,
        "market_score": market_score,
        "breakout": bool(is_breakout),
        "setup_type": setup_type,
        "volume_ratio": None if volume_ratio is None else round(float(volume_ratio), 3),
        "daily_move_pct": round(daily_move_pct, 3),
        "trend_score": int(trend_score),
        "rs_score": int(rs_score),
        "rel_20_spy": None if rel_20 is None else round(rel_20 * 100, 3),
        "rel_63_spy": None if rel_63 is None else round(rel_63 * 100, 3),
        "rel_20_qqq": None if rel_qqq_20 is None else round(rel_qqq_20 * 100, 3),
        "stop_model": stop_model,
        "risk_pct_used": risk_pct_used,
    }


# -----------------------------------------------------------------------------
# BACKTEST ENGINE
# -----------------------------------------------------------------------------


def new_run_dir(prefix: str) -> str:
    paths = ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(paths["runs"], f"{prefix}_{ts}")
    os.makedirs(run_dir, exist_ok=True)
    return run_dir


def mark_price(ticker: str, day: date, data: Dict[str, pd.DataFrame], fallback: float) -> float:
    df = data.get(ticker)
    if df is not None and day in df.index:
        return float(df.loc[day]["Close"])
    return float(fallback)


def calc_equity(cash: float, positions: Dict[str, Dict[str, Any]], day: date, data: Dict[str, pd.DataFrame]) -> float:
    equity = float(cash)
    for ticker, pos in positions.items():
        equity += int(pos["shares"]) * mark_price(ticker, day, data, float(pos["entry_price"]))
    return equity


def calc_initial_open_risk(positions: Dict[str, Dict[str, Any]]) -> float:
    return sum(float(p["risk_per_share"]) * int(p["shares"]) for p in positions.values())


def record_trade(
    trades: List[Dict[str, Any]],
    pos: Dict[str, Any],
    shares: int,
    exit_date: date,
    exit_price: float,
    exit_reason: str,
    stop_at_exit: float,
) -> Dict[str, Any]:
    entry = float(pos["entry_price"])
    risk_per_share = float(pos["risk_per_share"])
    profit = (exit_price - entry) * shares - float(pos.get("commission_per_trade", 0.0))
    r_multiple = (exit_price - entry) / risk_per_share if risk_per_share > 0 else None
    profit_pct = ((exit_price - entry) / entry) * 100 if entry > 0 else None

    trade = {
        "position_id": pos["position_id"],
        "ticker": pos["ticker"],
        "bucket": pos["bucket"],
        "setup_type": pos["setup_type"],
        "entry_signal_date": pos["signal_date"],
        "entry_date": pos["entry_date"].isoformat(),
        "entry_price": round(entry, 4),
        "exit_date": exit_date.isoformat(),
        "exit_price": round(exit_price, 4),
        "exit_reason": exit_reason,
        "shares": int(shares),
        "profit": round(profit, 2),
        "profit_pct": None if profit_pct is None else round(profit_pct, 3),
        "risk_per_share": round(risk_per_share, 4),
        "r_multiple": None if r_multiple is None else round(r_multiple, 4),
        "holding_days": int((exit_date - pos["entry_date"]).days),
        "signal_price": pos["signal_price"],
        "initial_stop": round(float(pos["initial_stop"]), 4),
        "stop_at_exit": round(stop_at_exit, 4),
        "atr": pos["atr"],
        "rsi": pos["rsi"],
        "score": pos["score"],
        "volume_ratio": pos["volume_ratio"],
        "market": pos["market"],
        "mfe_r": round(float(pos.get("mfe_r", 0.0)), 4),
        "mae_r": round(float(pos.get("mae_r", 0.0)), 4),
        "partial_taken_before_exit": bool(pos.get("partial_taken", False)),
    }
    trades.append(trade)
    return trade


def manage_position_day(
    ticker: str,
    pos: Dict[str, Any],
    row: pd.Series,
    day: date,
    cfg: StrategyConfig,
    trades: List[Dict[str, Any]],
    cash: float,
) -> Tuple[float, Optional[Dict[str, Any]]]:
    open_price = float(row["Open"])
    high = float(row["High"])
    low = float(row["Low"])
    close = float(row["Close"])
    slip_sell = 1 - (cfg.slippage_bps / 10000)

    entry = float(pos["entry_price"])
    risk = float(pos["risk_per_share"])
    shares = int(pos["shares"])

    pos["mfe_r"] = max(float(pos.get("mfe_r", 0.0)), (high - entry) / risk)
    pos["mae_r"] = min(float(pos.get("mae_r", 0.0)), (low - entry) / risk)

    # Conservative daily ordering: if stop and target both occur inside the bar,
    # stop is assumed first. Gap-through stops fill at the open.
    current_stop = float(pos["stop"])
    if low <= current_stop:
        raw_exit = open_price if open_price < current_stop else current_stop
        exit_price = max(0.01, raw_exit * slip_sell)
        record_trade(trades, pos, shares, day, exit_price, "stop", current_stop)
        cash += shares * exit_price - cfg.commission_per_trade
        return cash, None

    # Partial take-profit: fill at the first reached threshold, not at the day's high.
    if not pos.get("partial_taken", False) and shares > 1:
        one_r_target = entry + (cfg.partial_r * risk)
        pct_target = entry * (1 + cfg.partial_pct)
        candidates: List[Tuple[float, str]] = []
        if high >= one_r_target:
            candidates.append((one_r_target, "partial_1r"))
        if high >= pct_target:
            candidates.append((pct_target, "partial_8pct"))

        if candidates:
            raw_partial, reason = sorted(candidates, key=lambda x: x[0])[0]
            partial_price = max(0.01, raw_partial * slip_sell)
            sell_shares = shares // 2
            record_trade(trades, pos, sell_shares, day, partial_price, reason, current_stop)
            cash += sell_shares * partial_price - cfg.commission_per_trade
            pos["shares"] = shares - sell_shares
            pos["partial_taken"] = True
            shares = int(pos["shares"])

    # End-of-day stop update for tomorrow. This avoids intraday lookahead.
    pos["highest"] = max(float(pos["highest"]), high)
    multiplier = cfg.trail_mult_after_5pct if close >= entry * 1.05 else cfg.trail_mult_before_5pct
    theoretical_trail = float(pos["highest"]) - (multiplier * float(pos["atr"]))
    new_stop = max(float(pos["stop"]), theoretical_trail)

    close_r = (close - entry) / risk
    if close_r >= cfg.breakeven_r and new_stop < entry:
        new_stop = entry

    pos["stop"] = round(new_stop, 6)
    return cash, pos


def run_backtest_core(
    data: Dict[str, pd.DataFrame],
    start: date,
    end: date,
    cfg: StrategyConfig,
    run_dir: Optional[str] = None,
    save_outputs: bool = True,
) -> Dict[str, Any]:
    spy_dates = [d for d in data["SPY"].index.tolist() if start <= d <= end]
    spy_dates = sorted(spy_dates)
    if len(spy_dates) < 60:
        raise RuntimeError("Not enough trading days in requested range")

    cash = float(cfg.initial_capital)
    positions: Dict[str, Dict[str, Any]] = {}
    pending_orders: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    equity_rows: List[Dict[str, Any]] = []

    for i, day in enumerate(spy_dates):
        # 1) Execute yesterday's pending entries at today's open.
        todays_pending = pending_orders
        pending_orders = []

        for order in todays_pending:
            ticker = order["ticker"]
            if ticker in positions:
                rejected.append({**order, "reject_date": day.isoformat(), "reason": "already_open"})
                continue
            if ticker not in data or day not in data[ticker].index:
                rejected.append({**order, "reject_date": day.isoformat(), "reason": "no_open_bar"})
                continue

            row = data[ticker].loc[day]
            entry_price = float(row["Open"]) * (1 + cfg.slippage_bps / 10000)
            if entry_price > float(order["max_valid_entry"]):
                rejected.append({**order, "reject_date": day.isoformat(), "entry_open": round(entry_price, 4), "reason": "open_above_max_entry"})
                continue

            atr_val = float(order["atr"])
            stop = float(order["stop"])
            risk_per_share = entry_price - stop
            if stop <= 0 or stop >= entry_price or risk_per_share <= 0:
                rejected.append({**order, "reject_date": day.isoformat(), "reason": "invalid_entry_risk"})
                continue

            shares = int(order["shares"])
            shares_by_cash = int((cash * cfg.cash_usage_buffer) / entry_price)
            shares = min(shares, shares_by_cash)
            if shares <= 0:
                rejected.append({**order, "reject_date": day.isoformat(), "entry_open": round(entry_price, 4), "reason": "cash_insufficient_at_open"})
                continue

            equity = calc_equity(cash, positions, day, data)
            projected_risk = calc_initial_open_risk(positions) + shares * risk_per_share
            if equity <= 0 or projected_risk / equity > cfg.max_total_risk:
                rejected.append({**order, "reject_date": day.isoformat(), "entry_open": round(entry_price, 4), "reason": "risk_cap_at_open"})
                continue

            cost = shares * entry_price + cfg.commission_per_trade
            if cost > cash:
                rejected.append({**order, "reject_date": day.isoformat(), "entry_open": round(entry_price, 4), "reason": "cost_gt_cash"})
                continue

            cash -= cost
            position_id = f"BT_{ticker}_{day.isoformat()}_{uuid.uuid4().hex[:8]}"
            positions[ticker] = {
                "position_id": position_id,
                "ticker": ticker,
                "bucket": ticker_bucket(ticker),
                "setup_type": order["setup_type"],
                "signal_date": order["signal_date"],
                "entry_date": day,
                "entry_price": round(entry_price, 6),
                "signal_price": order["signal_price"],
                "shares": shares,
                "initial_stop": round(stop, 6),
                "stop": round(stop, 6),
                "highest": round(entry_price, 6),
                "partial_taken": False,
                "atr": round(atr_val, 6),
                "risk_per_share": round(risk_per_share, 6),
                "rsi": order["rsi"],
                "score": order["score"],
                "volume_ratio": order["volume_ratio"],
                "market": order["market"],
                "mfe_r": 0.0,
                "mae_r": 0.0,
                "commission_per_trade": cfg.commission_per_trade,
            }

        # 2) Manage open positions using today's daily bar.
        for ticker in list(positions.keys()):
            if ticker not in data or day not in data[ticker].index:
                continue
            cash, updated_pos = manage_position_day(ticker, positions[ticker], data[ticker].loc[day], day, cfg, trades, cash)
            if updated_pos is None:
                del positions[ticker]
            else:
                positions[ticker] = updated_pos

        # 3) Mark equity after management.
        equity = calc_equity(cash, positions, day, data)
        equity_rows.append({
            "date": day.isoformat(),
            "cash": round(cash, 2),
            "positions": len(positions),
            "equity": round(equity, 2),
            "open_risk": round(calc_initial_open_risk(positions), 2),
            "open_risk_pct": round((calc_initial_open_risk(positions) / equity) * 100, 3) if equity > 0 else 0.0,
        })

        # 4) Generate near-close signals. These execute at next session open.
        # V2.1 mirrors the live bot more closely: collect candidates, sort by score,
        # then reserve risk/capital for the best signals only.
        if i >= len(spy_dates) - 1:
            continue

        market = market_condition_on(day, data)
        reserved_risk = 0.0
        reserved_capital = 0.0
        day_candidates: List[Dict[str, Any]] = []

        for ticker in WATCHLIST:
            if ticker in positions:
                continue
            if len(positions) + len(pending_orders) >= cfg.max_open_positions:
                break

            signal = analyze_signal(ticker, day, data, market, cfg)
            if signal is None:
                continue

            equity_at_signal = calc_equity(cash, positions, day, data)
            open_risk = calc_initial_open_risk(positions)
            if equity_at_signal <= 0:
                continue
            if (open_risk + reserved_risk) / equity_at_signal >= cfg.max_total_risk:
                rejected.append({**signal, "reason": "risk_already_at_cap"})
                continue

            price = float(signal["signal_price"])
            risk_per_share = float(signal["risk_per_share_at_signal"])
            risk_pct = float(signal.get("risk_pct_used") or risk_pct_for_ticker(ticker) or 0)
            if risk_pct <= 0:
                continue

            shares_by_risk = int((equity_at_signal * risk_pct) / risk_per_share)
            shares_by_position_cap = int((equity_at_signal * cfg.max_position_equity_pct) / price)
            shares_by_cash = int((cash * cfg.cash_usage_buffer) / price)
            shares = min(shares_by_risk, shares_by_position_cap, shares_by_cash)

            if shares <= 0:
                rejected.append({**signal, "reason": "shares_zero"})
                continue

            risk_amount = shares * risk_per_share
            capital = shares * price

            day_candidates.append({
                **signal,
                "entry_execute_date": spy_dates[i + 1].isoformat(),
                "shares": shares,
                "capital": round(capital, 2),
                "risk_amount": round(risk_amount, 2),
                "equity_at_signal": round(equity_at_signal, 2),
                "position_size_pct": round((capital / equity_at_signal) * 100, 3),
                "single_trade_risk_pct": round((risk_amount / equity_at_signal) * 100, 3),
            })

        day_candidates = sorted(day_candidates, key=lambda x: int(x.get("score", 0)), reverse=True)

        sent_count = 0
        for order in day_candidates:
            if sent_count >= cfg.max_signals_per_scan:
                break
            if len(positions) + len(pending_orders) >= cfg.max_open_positions:
                rejected.append({**order, "reason": "max_positions_after_sort"})
                continue

            equity_at_signal = calc_equity(cash, positions, day, data)
            open_risk = calc_initial_open_risk(positions)
            risk_amount = float(order["risk_amount"])
            capital = float(order["capital"])
            projected_risk_pct = (open_risk + reserved_risk + risk_amount) / equity_at_signal
            projected_capital = reserved_capital + capital

            if projected_risk_pct > cfg.max_total_risk:
                rejected.append({**order, "reason": "projected_risk_cap", "projected_risk_pct": round(projected_risk_pct * 100, 3)})
                continue
            if projected_capital > cash * cfg.cash_usage_buffer:
                rejected.append({**order, "reason": "cash_reserve", "projected_capital": round(projected_capital, 2)})
                continue

            order["projected_total_risk_pct"] = round(projected_risk_pct * 100, 3)
            pending_orders.append(order)
            reserved_risk += risk_amount
            reserved_capital += capital
            sent_count += 1

    # 5) Liquidate remaining open positions at final close for accounting.
    final_day = spy_dates[-1]
    for ticker in list(positions.keys()):
        pos = positions[ticker]
        final_price = mark_price(ticker, final_day, data, float(pos["entry_price"])) * (1 - cfg.slippage_bps / 10000)
        record_trade(trades, pos, int(pos["shares"]), final_day, final_price, "end_of_test", float(pos["stop"]))
        cash += int(pos["shares"]) * final_price - cfg.commission_per_trade
        del positions[ticker]

    final_equity = cash
    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity_rows)
    rejected_df = pd.DataFrame(rejected)

    summary = summarize_backtest(trades_df, equity_df, cfg, start, end, final_equity)

    if save_outputs:
        if run_dir is None:
            run_dir = new_run_dir(f"backtest_{cfg.name}")
        save_run_outputs(run_dir, cfg, summary, trades_df, equity_df, rejected_df)

    return {
        "summary": summary,
        "trades": trades_df,
        "equity": equity_df,
        "rejected": rejected_df,
        "run_dir": run_dir,
    }


def summarize_backtest(
    trades_df: pd.DataFrame,
    equity_df: pd.DataFrame,
    cfg: StrategyConfig,
    start: date,
    end: date,
    final_equity: float,
) -> Dict[str, Any]:
    if trades_df.empty:
        return {
            "config_name": cfg.name,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "initial_capital": cfg.initial_capital,
            "final_equity": round(final_equity, 2),
            "total_profit": round(final_equity - cfg.initial_capital, 2),
            "total_return_pct": round(((final_equity / cfg.initial_capital) - 1) * 100, 2) if cfg.initial_capital > 0 else None,
            "trade_records": 0,
            "positions": 0,
            "profit_factor": None,
            "avg_r": None,
            "median_r": None,
            "win_rate_pct": None,
            "max_drawdown_pct": None,
            "max_drawdown_dollars": None,
        }

    total_profit = float(trades_df["profit"].sum())
    r_values = pd.to_numeric(trades_df["r_multiple"], errors="coerce").dropna()
    equity_series = pd.to_numeric(equity_df["equity"], errors="coerce") if not equity_df.empty else pd.Series(dtype=float)
    dd_pct, dd_dollars = max_drawdown(equity_series)

    return {
        "config_name": cfg.name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "initial_capital": round(cfg.initial_capital, 2),
        "final_equity": round(final_equity, 2),
        "total_profit": round(final_equity - cfg.initial_capital, 2),
        "closed_trade_profit": round(total_profit, 2),
        "total_return_pct": round(((final_equity / cfg.initial_capital) - 1) * 100, 2) if cfg.initial_capital > 0 else None,
        "trade_records": int(len(trades_df)),
        "positions": int(trades_df["position_id"].nunique()),
        "win_rate_pct_trade_records": round((trades_df["profit"].gt(0).mean()) * 100, 2),
        "profit_factor": profit_factor(pd.to_numeric(trades_df["profit"], errors="coerce")),
        "avg_r": None if r_values.empty else round(float(r_values.mean()), 4),
        "median_r": None if r_values.empty else round(float(r_values.median()), 4),
        "avg_win_r": None if r_values[r_values > 0].empty else round(float(r_values[r_values > 0].mean()), 4),
        "avg_loss_r": None if r_values[r_values < 0].empty else round(float(r_values[r_values < 0].mean()), 4),
        "max_drawdown_pct": dd_pct,
        "max_drawdown_dollars": dd_dollars,
    }


def group_stats(trades_df: pd.DataFrame, by: str) -> pd.DataFrame:
    if trades_df.empty or by not in trades_df.columns:
        return pd.DataFrame()
    rows = []
    for key, g in trades_df.groupby(by):
        profit = pd.to_numeric(g["profit"], errors="coerce")
        r_mult = pd.to_numeric(g["r_multiple"], errors="coerce").dropna()
        rows.append({
            by: key,
            "trade_records": len(g),
            "positions": g["position_id"].nunique() if "position_id" in g else None,
            "profit": round(float(profit.sum()), 2),
            "win_rate_pct": round(float(profit.gt(0).mean() * 100), 2) if len(profit) else None,
            "profit_factor": profit_factor(profit),
            "avg_r": None if r_mult.empty else round(float(r_mult.mean()), 4),
            "median_r": None if r_mult.empty else round(float(r_mult.median()), 4),
        })
    return pd.DataFrame(rows).sort_values("profit", ascending=False)


def monthly_returns(equity_df: pd.DataFrame) -> pd.DataFrame:
    if equity_df.empty:
        return pd.DataFrame()
    df = equity_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["month"] = df["date"].dt.to_period("M").astype(str)
    m = df.groupby("month").agg(start_equity=("equity", "first"), end_equity=("equity", "last")).reset_index()
    m["return_pct"] = ((m["end_equity"] / m["start_equity"]) - 1) * 100
    m["return_pct"] = m["return_pct"].round(2)
    return m


def save_run_outputs(
    run_dir: str,
    cfg: StrategyConfig,
    summary: Dict[str, Any],
    trades_df: pd.DataFrame,
    equity_df: pd.DataFrame,
    rejected_df: pd.DataFrame,
) -> str:
    os.makedirs(run_dir, exist_ok=True)
    write_json(os.path.join(run_dir, "run_config.json"), asdict(cfg))
    write_json(os.path.join(run_dir, "summary.json"), summary)

    trades_df.to_csv(os.path.join(run_dir, "trades.csv"), index=False)
    write_json(os.path.join(run_dir, "trades.json"), trades_df.to_dict(orient="records"))
    equity_df.to_csv(os.path.join(run_dir, "equity_curve.csv"), index=False)
    rejected_df.to_csv(os.path.join(run_dir, "rejected_signals.csv"), index=False)

    group_stats(trades_df, "setup_type").to_csv(os.path.join(run_dir, "by_setup.csv"), index=False)
    group_stats(trades_df, "ticker").to_csv(os.path.join(run_dir, "by_ticker.csv"), index=False)
    group_stats(trades_df, "bucket").to_csv(os.path.join(run_dir, "by_bucket.csv"), index=False)
    monthly_returns(equity_df).to_csv(os.path.join(run_dir, "monthly_returns.csv"), index=False)

    zip_path = f"{run_dir}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(run_dir):
            for filename in files:
                full = os.path.join(root, filename)
                arcname = os.path.relpath(full, run_dir)
                z.write(full, arcname)
    return zip_path


# -----------------------------------------------------------------------------
# VARIANTS / WALK-FORWARD
# -----------------------------------------------------------------------------


def make_variants(base: StrategyConfig) -> List[StrategyConfig]:
    return [
        base,
        replace(base, name="v2_1_breakout_only", pullback_min_score=999),
        replace(base, name="v2_1_no_weak", allow_weak=False),
        replace(base, name="v2_1_tighter_stop", breakout_atr_stop_mult=1.60, pullback_atr_stop_mult=1.65),
        replace(base, name="v2_1_no_rs_hard_filter", breakout_require_positive_rs=False, pullback_require_positive_rs=False),
        replace(base, name="v2_1_stricter", min_score=72, breakout_min_score=78, pullback_min_score=76, weak_min_score=84),
    ]


def run_variants(data: Dict[str, pd.DataFrame], start: date, end: date, base_cfg: StrategyConfig) -> str:
    run_dir = new_run_dir("variant_compare")
    rows = []

    for cfg in make_variants(base_cfg):
        print(f"\n=== VARIANT {cfg.name} ===")
        result = run_backtest_core(data, start, end, cfg, run_dir=os.path.join(run_dir, cfg.name), save_outputs=True)
        rows.append(result["summary"])

    variants_df = pd.DataFrame(rows).sort_values(["profit_factor", "avg_r", "total_profit"], ascending=False)
    variants_df.to_csv(os.path.join(run_dir, "variant_compare.csv"), index=False)
    write_json(os.path.join(run_dir, "variant_compare.json"), rows)

    zip_path = f"{run_dir}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(run_dir):
            for filename in files:
                full = os.path.join(root, filename)
                arcname = os.path.relpath(full, run_dir)
                z.write(full, arcname)

    print("\nVARIANT SUMMARY")
    print(variants_df.to_string(index=False))
    print(f"\nSaved: {zip_path}")
    return zip_path


def add_months(d: date, months: int) -> date:
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    day = min(d.day, 28)
    return date(year, month, day)


def run_walkforward(
    data: Dict[str, pd.DataFrame],
    start: date,
    end: date,
    cfg: StrategyConfig,
    train_months: int,
    test_months: int,
) -> str:
    run_dir = new_run_dir("walkforward")
    rows = []
    window_id = 1
    test_start = add_months(start, train_months)

    while test_start < end:
        test_end = min(add_months(test_start, test_months) - timedelta(days=1), end)
        if test_end <= test_start:
            break

        print(f"\n=== WALK-FORWARD WINDOW {window_id}: {test_start} to {test_end} ===")
        # Skip incomplete final windows with too few trading days.
        sample_ticker = next(iter(data))
        window_df = data[sample_ticker]

        window_days = window_df[
            (window_df["date"] >= test_start) &
            (window_df["date"] <= test_end)
        ]

        if len(window_days) < 60:
            print(f"\nSkipping incomplete final window: {test_start} to {test_end}")
            break
        window_cfg = replace(cfg, name=f"wf_{window_id:02d}")
        result = run_backtest_core(
            data,
            test_start,
            test_end,
            window_cfg,
            run_dir=os.path.join(run_dir, f"window_{window_id:02d}"),
            save_outputs=True,
        )
        row = result["summary"]
        row["window_id"] = window_id
        row["train_start"] = start.isoformat()
        row["train_end"] = (test_start - timedelta(days=1)).isoformat()
        row["test_start"] = test_start.isoformat()
        row["test_end"] = test_end.isoformat()
        rows.append(row)

        window_id += 1
        test_start = add_months(test_start, test_months)

    wf_df = pd.DataFrame(rows)
    wf_df.to_csv(os.path.join(run_dir, "walkforward_windows.csv"), index=False)

    aggregate = {
        "config": asdict(cfg),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "train_months": train_months,
        "test_months": test_months,
        "windows": len(wf_df),
        "profitable_windows": int((wf_df["total_profit"] > 0).sum()) if not wf_df.empty else 0,
        "avg_window_return_pct": None if wf_df.empty else round(float(wf_df["total_return_pct"].mean()), 3),
        "median_window_return_pct": None if wf_df.empty else round(float(wf_df["total_return_pct"].median()), 3),
        "avg_window_profit_factor": None if wf_df.empty else round(float(pd.to_numeric(wf_df["profit_factor"], errors="coerce").replace(999.0, pd.NA).dropna().mean()), 3) if not pd.to_numeric(wf_df["profit_factor"], errors="coerce").replace(999.0, pd.NA).dropna().empty else None,
        "avg_window_r": None if wf_df.empty else round(float(pd.to_numeric(wf_df["avg_r"], errors="coerce").dropna().mean()), 4) if not pd.to_numeric(wf_df["avg_r"], errors="coerce").dropna().empty else None,
    }
    write_json(os.path.join(run_dir, "walkforward_summary.json"), aggregate)

    zip_path = f"{run_dir}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(run_dir):
            for filename in files:
                full = os.path.join(root, filename)
                arcname = os.path.relpath(full, run_dir)
                z.write(full, arcname)

    print("\nWALK-FORWARD SUMMARY")
    print(json.dumps(aggregate, indent=2))
    print(f"\nSaved: {zip_path}")
    return zip_path


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def build_config(args: argparse.Namespace) -> StrategyConfig:
    return StrategyConfig(
        name=args.name,
        initial_capital=args.capital,
        max_open_positions=args.max_open_positions,
        max_total_risk=args.max_total_risk,
        max_position_equity_pct=args.max_position_equity_pct,
        cash_usage_buffer=args.cash_usage_buffer,
        max_entry_extension_pct=args.max_entry_extension_pct,
        min_market_score=args.min_market_score,
        min_score=args.min_score,
        breakout_min_score=args.breakout_min_score,
        pullback_min_score=args.pullback_min_score,
        weak_min_score=args.weak_min_score,
        max_signals_per_scan=args.max_signals_per_scan,
        breakout_atr_stop_mult=args.breakout_atr_stop_mult,
        pullback_atr_stop_mult=args.pullback_atr_stop_mult,
        breakeven_r=args.breakeven_r,
        partial_r=args.partial_r,
        partial_pct=args.partial_pct,
        breakout_min_volume_ratio=args.breakout_min_volume_ratio,
        pullback_min_volume_ratio=args.pullback_min_volume_ratio,
        breakout_max_rsi=args.breakout_max_rsi,
        breakout_max_daily_move_pct=args.breakout_max_daily_move_pct,
        pullback_max_daily_move_pct=args.pullback_max_daily_move_pct,
        block_pullback_in_uncertain=not args.allow_pullback_uncertain,
        pullback_require_positive_rs=not args.no_pullback_rs_filter,
        breakout_require_positive_rs=not args.no_breakout_rs_filter,
        allow_weak=not args.no_weak,
        slippage_bps=args.slippage_bps,
        commission_per_trade=args.commission,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily OHLC backtest and walk-forward engine for the Telegram trading bot strategy.")
    parser.add_argument("--mode", choices=["backtest", "variants", "walkforward"], default="backtest")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=float(os.getenv("INITIAL_CASH", "4000")))
    parser.add_argument("--name", default="baseline")
    parser.add_argument("--refresh-cache", action="store_true")

    parser.add_argument("--max-open-positions", type=int, default=12)
    parser.add_argument("--max-total-risk", type=float, default=0.06)
    parser.add_argument("--max-position-equity-pct", type=float, default=0.20)
    parser.add_argument("--cash-usage-buffer", type=float, default=0.98)
    parser.add_argument("--max-entry-extension-pct", type=float, default=0.01)
    parser.add_argument("--breakout-atr-stop-mult", type=float, default=1.80)
    parser.add_argument("--pullback-atr-stop-mult", type=float, default=1.80)
    parser.add_argument("--breakeven-r", type=float, default=0.70)
    parser.add_argument("--partial-r", type=float, default=1.0)
    parser.add_argument("--partial-pct", type=float, default=0.08)
    parser.add_argument("--min-market-score", type=int, default=3)
    parser.add_argument("--min-score", type=int, default=68)
    parser.add_argument("--breakout-min-score", type=int, default=74)
    parser.add_argument("--pullback-min-score", type=int, default=72)
    parser.add_argument("--weak-min-score", type=int, default=80)
    parser.add_argument("--max-signals-per-scan", type=int, default=6)
    parser.add_argument("--breakout-min-volume-ratio", type=float, default=1.15)
    parser.add_argument("--pullback-min-volume-ratio", type=float, default=0.30)
    parser.add_argument("--breakout-max-rsi", type=float, default=82.0)
    parser.add_argument("--breakout-max-daily-move-pct", type=float, default=7.0)
    parser.add_argument("--pullback-max-daily-move-pct", type=float, default=6.0)
    parser.add_argument("--allow-pullback-uncertain", action="store_true")
    parser.add_argument("--no-pullback-rs-filter", action="store_true")
    parser.add_argument("--no-breakout-rs-filter", action="store_true")
    parser.add_argument("--no-weak", action="store_true")
    parser.add_argument("--slippage-bps", type=float, default=10.0)
    parser.add_argument("--commission", type=float, default=0.0)

    parser.add_argument("--train-months", type=int, default=12)
    parser.add_argument("--test-months", type=int, default=3)

    args = parser.parse_args()
    start = parse_date(args.start)
    end = parse_date(args.end)
    if end <= start:
        raise RuntimeError("--end must be after --start")

    ensure_dirs()
    cfg = build_config(args)
    print("CONFIG")
    print(json.dumps(asdict(cfg), indent=2))

    data = load_all_data(start, end, refresh_cache=args.refresh_cache)

    if args.mode == "backtest":
        run_dir = new_run_dir(f"backtest_{cfg.name}")
        result = run_backtest_core(data, start, end, cfg, run_dir=run_dir, save_outputs=True)
        zip_path = f"{run_dir}.zip"
        print("\nSUMMARY")
        print(json.dumps(result["summary"], indent=2))
        print(f"\nSaved: {zip_path}")

    elif args.mode == "variants":
        run_variants(data, start, end, cfg)

    elif args.mode == "walkforward":
        run_walkforward(data, start, end, cfg, train_months=args.train_months, test_months=args.test_months)


if __name__ == "__main__":
    main()
