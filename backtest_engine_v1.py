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
MARKET_TICKERS = ["SPY", "QQQ"]
ALL_TICKERS = sorted(set(WATCHLIST + MARKET_TICKERS))

SESSION = requests.Session()


@dataclass(frozen=True)
class StrategyConfig:
    name: str = "baseline_v1"
    initial_capital: float = 4000.0
    max_open_positions: int = 12
    max_total_risk: float = 0.06
    max_position_equity_pct: float = 0.20
    cash_usage_buffer: float = 0.98
    max_entry_extension_pct: float = 0.01
    atr_stop_mult: float = 1.5
    breakeven_r: float = 0.70
    partial_r: float = 1.0
    partial_pct: float = 0.08
    trail_mult_before_5pct: float = 2.5
    trail_mult_after_5pct: float = 2.0
    min_score: int = 40
    breakout_min_volume_ratio: float = 1.20
    breakout_max_rsi: float = 85.0
    breakout_max_daily_move_pct: float = 8.0
    pullback_max_rsi: float = 70.0
    pullback_max_extension_over_ma20: float = 0.05
    reject_weak_in_uncertain_market: bool = True
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
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA50"] = df["Close"].rolling(50).mean()
    df["AvgVol20"] = df["Volume"].rolling(20).mean()
    df["ATR14"] = atr(df)
    df["RecentHigh20"] = df["Close"].shift(1).rolling(20).max()
    df["DailyMovePct"] = ((df["Close"] - df["PrevClose"]) / df["PrevClose"]) * 100
    return df


def market_condition_on(day: date, data: Dict[str, pd.DataFrame]) -> str:
    try:
        spy = data["SPY"]
        qqq = data["QQQ"]
        if day not in spy.index or day not in qqq.index:
            return "UNCERTAIN"
        s = spy.loc[day]
        q = qqq.loc[day]
        if pd.isna(s["MA50"]) or pd.isna(q["MA50"]):
            return "UNCERTAIN"
        if float(s["Close"]) > float(s["MA50"]) and float(q["Close"]) > float(q["MA50"]):
            return "BULL"
        if float(s["Close"]) < float(s["MA50"]) and float(q["Close"]) < float(q["MA50"]):
            return "BEAR"
        return "UNCERTAIN"
    except Exception:
        return "UNCERTAIN"


def analyze_signal(ticker: str, day: date, data: Dict[str, pd.DataFrame], market: str, cfg: StrategyConfig) -> Optional[Dict[str, Any]]:
    if not cfg.allow_weak and ticker in WEAK:
        return None
    if ticker not in data or day not in data[ticker].index:
        return None

    row = data[ticker].loc[day]
    fields = ["Close", "PrevClose", "RSI14", "MA20", "MA50", "AvgVol20", "ATR14", "RecentHigh20", "Volume"]
    if any(pd.isna(row[f]) for f in fields):
        return None

    price = float(row["Close"])
    prev_close = float(row["PrevClose"])
    rsi_val = float(row["RSI14"])
    ma20 = float(row["MA20"])
    ma50 = float(row["MA50"])
    avg_vol = float(row["AvgVol20"])
    atr_val = float(row["ATR14"])
    recent_high = float(row["RecentHigh20"])
    volume = float(row["Volume"])

    if min(price, prev_close, ma20, ma50, avg_vol, atr_val, recent_high) <= 0:
        return None

    breakout = price > recent_high
    daily_move_pct = ((price - prev_close) / prev_close) * 100
    volume_ratio = volume / avg_vol if avg_vol > 0 else None

    if market == "BEAR":
        return None
    if market == "UNCERTAIN" and cfg.reject_weak_in_uncertain_market and ticker in WEAK:
        return None
    if not breakout and rsi_val > cfg.pullback_max_rsi:
        return None
    if not breakout and (price - ma20) / ma20 > cfg.pullback_max_extension_over_ma20:
        return None
    if breakout and daily_move_pct > cfg.breakout_max_daily_move_pct:
        return None
    if not breakout and price <= prev_close:
        return None

    score = 0
    if price > ma50:
        score += 20
    if price < ma20 and rsi_val < 45:
        score += 30
    if volume > avg_vol * 1.5:
        score += 20
    if breakout:
        score += 20
        if price > ma20:
            score += 10
        if ma20 > ma50:
            score += 10

    if breakout and volume < avg_vol * cfg.breakout_min_volume_ratio:
        return None
    if breakout and rsi_val > cfg.breakout_max_rsi:
        return None
    if score < cfg.min_score:
        return None
    if not breakout and ma20 < ma50:
        return None

    stop = price - (cfg.atr_stop_mult * atr_val)
    risk_per_share = price - stop
    if stop <= 0 or risk_per_share <= 0:
        return None

    return {
        "ticker": ticker,
        "signal_date": day.isoformat(),
        "signal_price": round(price, 4),
        "max_valid_entry": round(price * (1 + cfg.max_entry_extension_pct), 4),
        "stop": round(stop, 4),
        "atr": round(atr_val, 4),
        "risk_per_share_at_signal": round(risk_per_share, 4),
        "rsi": round(rsi_val, 2),
        "score": int(score),
        "market": market,
        "breakout": bool(breakout),
        "setup_type": "breakout" if breakout else "pullback",
        "volume_ratio": None if volume_ratio is None else round(float(volume_ratio), 3),
        "daily_move_pct": round(daily_move_pct, 3),
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
            stop = entry_price - cfg.atr_stop_mult * atr_val
            risk_per_share = entry_price - stop
            if stop <= 0 or risk_per_share <= 0:
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
        if i >= len(spy_dates) - 1:
            continue

        market = market_condition_on(day, data)
        reserved_risk = 0.0
        reserved_capital = 0.0

        for ticker in WATCHLIST:
            if not cfg.allow_weak and ticker in WEAK:
                continue
            if ticker in positions:
                continue
            if len(positions) + len(pending_orders) >= cfg.max_open_positions:
                break

            risk_pct = risk_pct_for_ticker(ticker)
            if risk_pct is None:
                continue

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
            shares_by_risk = int((equity_at_signal * risk_pct) / risk_per_share)
            shares_by_position_cap = int((equity_at_signal * cfg.max_position_equity_pct) / price)
            shares_by_cash = int((cash * cfg.cash_usage_buffer) / price)
            shares = min(shares_by_risk, shares_by_position_cap, shares_by_cash)

            if shares <= 0:
                rejected.append({**signal, "reason": "shares_zero"})
                continue

            risk_amount = shares * risk_per_share
            capital = shares * price
            projected_risk_pct = (open_risk + reserved_risk + risk_amount) / equity_at_signal
            projected_capital = reserved_capital + capital

            if projected_risk_pct > cfg.max_total_risk:
                rejected.append({**signal, "reason": "projected_risk_cap", "projected_risk_pct": round(projected_risk_pct * 100, 3)})
                continue
            if projected_capital > cash * cfg.cash_usage_buffer:
                rejected.append({**signal, "reason": "cash_reserve", "projected_capital": round(projected_capital, 2)})
                continue

            order = {
                **signal,
                "entry_execute_date": spy_dates[i + 1].isoformat(),
                "shares": shares,
                "capital": round(capital, 2),
                "risk_amount": round(risk_amount, 2),
                "equity_at_signal": round(equity_at_signal, 2),
                "position_size_pct": round((capital / equity_at_signal) * 100, 3),
                "single_trade_risk_pct": round((risk_amount / equity_at_signal) * 100, 3),
                "projected_total_risk_pct": round(projected_risk_pct * 100, 3),
            }
            pending_orders.append(order)
            reserved_risk += risk_amount
            reserved_capital += capital

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
        replace(base, name="baseline"),
        replace(base, name="score50", min_score=50),
        replace(base, name="score60", min_score=60),
        replace(base, name="breakout_vol_150", breakout_min_volume_ratio=1.50),
        replace(base, name="partial_125r", partial_r=1.25),
        replace(base, name="partial_150r", partial_r=1.50),
        replace(base, name="no_weak", allow_weak=False),
        replace(base, name="stop_180atr", atr_stop_mult=1.80),
        replace(base, name="stop_130atr", atr_stop_mult=1.30),
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

        # skip tiny final windows
        trading_days = [
            d for d in data["SPY"].index.tolist()
            if test_start <= d <= test_end
        ]

        if len(trading_days) < 60:
            print(f"\nSkipping incomplete final window: {test_start} to {test_end}")
            break

        print(f"\n=== WALK-FORWARD WINDOW {window_id}: {test_start} to {test_end} ===")
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
        atr_stop_mult=args.atr_stop_mult,
        breakeven_r=args.breakeven_r,
        partial_r=args.partial_r,
        partial_pct=args.partial_pct,
        min_score=args.min_score,
        breakout_min_volume_ratio=args.breakout_min_volume_ratio,
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
    parser.add_argument("--atr-stop-mult", type=float, default=1.5)
    parser.add_argument("--breakeven-r", type=float, default=0.70)
    parser.add_argument("--partial-r", type=float, default=1.0)
    parser.add_argument("--partial-pct", type=float, default=0.08)
    parser.add_argument("--min-score", type=int, default=40)
    parser.add_argument("--breakout-min-volume-ratio", type=float, default=1.20)
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
