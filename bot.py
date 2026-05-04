import requests
import time
import json
import os
import pandas as pd

def safe_convert(obj):
    if isinstance(obj, dict):
        return {k: safe_convert(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [safe_convert(v) for v in obj]
    elif hasattr(obj, "item"):  # numpy types
        return obj.item()
    return obj

FMP_API_KEY = os.getenv("FMP_API_KEY")
FMP_BASE = "https://financialmodelingprep.com/api/v3"
SESSION = requests.Session()
TOKEN = os.getenv("TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))

if not TOKEN or CHAT_ID == 0:
    raise Exception("❌ Missing TOKEN or CHAT_ID")

if not FMP_API_KEY:
    raise Exception("❌ Missing FMP_API_KEY")

PORTFOLIO_FILE = "/data/portfolio.json"

# ---------------- GLOBAL ----------------
portfolio = None
last_update_id = None

SIGNALS_FILE = "/data/signals.json"
TRADES_FILE = "/data/trades.json"

# -------- SIGNALS --------
def load_signals():
    if not os.path.exists(SIGNALS_FILE):
        return {}
    try:
        with open(SIGNALS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[load_signals] ERROR: {e}")
        return {}

def save_signals():
    temp = SIGNALS_FILE + ".tmp"
    with open(temp, "w") as f:
        json.dump(safe_convert(last_signals), f)
    os.replace(temp, SIGNALS_FILE)

# -------- TRADES --------
def load_trades():
    if not os.path.exists(TRADES_FILE):
        return []
    try:
        with open(TRADES_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[load_trades] ERROR: {e}")
        return []

def save_trade(trade):
    trades = load_trades()
    trades.append(trade)

    temp = TRADES_FILE + ".tmp"
    with open(temp, "w") as f:
        json.dump(safe_convert(trades), f, indent=4)

    os.replace(temp, TRADES_FILE)

# -------- RUNTIME STATE --------
last_signals = load_signals()
cooldowns = {}
last_reset_day = None
breakout_memory = {}

# ---------------- ANALYTICS ----------------
def weekly_performance():
    trades = load_trades()
    now = time.time()
    week_ago = now - 7 * 86400
    return round(sum(t["profit"] for t in trades if t["exit_time"] >= week_ago), 2)

def win_rate():
    trades = load_trades()
    if not trades:
        return 0
    wins = sum(1 for t in trades if t["profit"] > 0)
    return round((wins / len(trades)) * 100, 2)

def ticker_stats():
    trades = load_trades()
    stats = {}

    for t in trades:
        stats.setdefault(t["ticker"], 0)
        stats[t["ticker"]] += t["profit"]

    best = max(stats.items(), key=lambda x: x[1], default=("None", 0))
    worst = min(stats.items(), key=lambda x: x[1], default=("None", 0))

    return best, worst

def avg_trade_duration():
    trades = load_trades()
    if not trades:
        return "0 hrs"

    avg = sum(t["duration_sec"] for t in trades) / len(trades)
    hours = avg / 3600

    if hours >= 72:
        days = hours / 24
        return f"{round(days,2)} days ({round(hours,2)} hrs)"

    return f"{round(hours,2)} hrs"

# ---------------- PORTFOLIO ----------------
def load_portfolio():
    if not os.path.exists(PORTFOLIO_FILE):
        return {"cash": 4000, "positions": {}}
    try:
        with open(PORTFOLIO_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[load_portfolio] ERROR: {e}")
        return {"cash": 4000, "positions": {}}

def save_portfolio(data):
    temp = PORTFOLIO_FILE + ".tmp"
    with open(temp, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(temp, PORTFOLIO_FILE)

portfolio = load_portfolio()

# ---------------- TELEGRAM ----------------
def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=5
        )
    except Exception as e:
        print(f"[analyze] ERROR: {e}")

# ---------------- TELEGRAM INPUT ----------------
def get_updates():
    global last_update_id

    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"

    if last_update_id:
        url += f"?offset={last_update_id + 1}"

    try:
        res = requests.get(url, timeout=5).json()
    except Exception as e:
        print(f"[context] ERROR: {e}")
        return

    for u in res.get("result", []):
        last_update_id = u["update_id"]

        if "message" in u:
            handle_command(u["message"].get("text", ""), None)

# ---------------- COMMANDS ----------------
def handle_command(text, entry_data=None):
    global portfolio

    text_lower = text.lower()

    # ----- ANALYTICS COMMANDS -----
    if text_lower == "pnl":
        send(f"📊 Weekly P/L: ${weekly_performance()}")
        return

    elif text_lower == "winrate":
        send(f"🏆 Win Rate: {win_rate()}%")
        return

    elif text_lower == "stats":
        best, worst = ticker_stats()
        send(f"📈 Best: {best[0]} (${round(best[1],2)})\n📉 Worst: {worst[0]} (${round(worst[1],2)})")
        return

    elif text_lower == "duration":
        send(f"⏱ Avg Trade Duration: {avg_trade_duration()}")
        return

    elif text_lower == "summary":
        pnl = weekly_performance()
        wr = win_rate()
        best, worst = ticker_stats()
        duration = avg_trade_duration()

        send(f"""
📊 SUMMARY

P/L (7d): ${pnl}
Win Rate: {wr}%
Avg Duration: {duration}

Best: {best[0]} (${round(best[1],2)})
Worst: {worst[0]} (${round(worst[1],2)})
""")
        return

    elif text_lower == "portfolio":
        cash = portfolio["cash"]
        positions = portfolio["positions"]

        if not positions:
            send(f"💼 PORTFOLIO\n\nCash: ${round(cash,2)}\nNo open positions")
            return

        msg = f"💼 PORTFOLIO\n\nCash: ${round(cash,2)}\n\n"

        for t, pos in positions.items():
            price = get_prices_batch([t]).get(t)
            if price is None:
                price = pos["price"]

            entry = pos["price"]
            shares = pos["shares"]
            pnl = (price - entry) * shares

            msg += (
                f"{t}\n"
                f"Shares: {shares}\n"
                f"Entry: {round(entry,2)}\n"
                f"Now: {round(price,2)}\n"
                f"P/L: ${round(pnl,2)}\n\n"
            )

        send(msg)
        return

    elif text_lower == "showportfolio_raw":
        send(json.dumps(portfolio, indent=2))
        return

    elif text_lower == "showtrades":
        data = json.dumps(load_trades(), indent=2)
        send(data[:4000])
        return

    elif text_lower == "showsignals":
        data = json.dumps(last_signals, indent=2)
        send(data[:4000])
        return

    elif text_lower == "resetsignals":
        last_signals.clear()
        save_signals()
        send("🔄 Signals reset (history cleared, trades safe)")
        return

    elif text_lower == "download_trades":
        try:
            with open(TRADES_FILE, "rb") as f:
                requests.post(
                    f"https://api.telegram.org/bot{TOKEN}/sendDocument",
                    files={"document": f},
                    data={"chat_id": CHAT_ID}
                )
        except Exception as e:
            send(f"❌ ERROR sending file: {e}")
        return

    elif text_lower.startswith("setcash"):
        parts = text_lower.split()

        if len(parts) != 2:
            send("❌ Usage: setcash 1670.15")
            return

        try:
            amount = float(parts[1])
            portfolio["cash"] = amount
            save_portfolio(portfolio)
            send(f"💰 Cash updated to ${amount}")
        except ValueError:
            send("❌ Invalid number")
        return

    # ----- ORIGINAL COMMAND LOGIC -----
    parts = text.lower().split()

    # basic validation
    if len(parts) < 5:
        return

    try:
        action = parts[0]
        ticker = parts[1].upper()
        shares = int(parts[2])
        price = float(parts[4])
    except (ValueError, IndexError) as e:
        print(f"[handle_command parse] ERROR: {e} | text={text}")
        send("❌ Invalid command format")
        return

    # -------- BUY --------
    if action == "bought":

        cost = shares * price

        if portfolio["cash"] < cost:
            send("❌ Not enough cash")
            return

        portfolio["cash"] -= cost

        if ticker in portfolio["positions"]:
            pos = portfolio["positions"][ticker]

            total_shares = pos["shares"] + shares
            avg_price = (
                (pos["shares"] * pos["price"]) + (shares * price)
            ) / total_shares

            pos["shares"] = total_shares
            pos["price"] = avg_price

        else:
            stop = None

            try:
                atr_val = None

                df = get_historical(ticker, limit=120)

                if df is not None and not df.empty:
                    atr_val = atr(df).iloc[-1]

                    if not pd.isna(atr_val):
                        stop = price - (1.5 * atr_val)
                        risk = price - stop
                        target = price + 2 * risk
                    else:
                        target = price * 1.10  # fallback
                else:
                    target = price * 1.10  # fallback

            except Exception as e:
                print(f"[buy{ticker}] ERROR: {e}")
                target = price * 1.10  # fallback

            signal = last_signals.get(ticker, {})

            if isinstance(signal, dict):
                signal_data = safe_convert(signal.get("entry_data", {}))
            else:
                signal_data = {}

            portfolio["positions"][ticker] = {
                "shares": shares,
                "price": price,
                "stop": stop if stop is not None else price * 0.95,
                "highest": price,
                "partial_taken": False,
                "entry_time": time.time(),
                "target": target,
                "atr": atr_val if atr_val is not None and not pd.isna(atr_val) else None,
                "entry_data": signal_data
            }


        save_portfolio(portfolio)

        send(
            f"✅ BOUGHT {ticker}\n"
            f"Shares: {shares} @ {price}\n"
            f"Cash left: ${round(portfolio['cash'],2)}"
        )

    # -------- SELL --------
    elif action == "sold":

        if ticker not in portfolio["positions"]:
            send("❌ No position to sell")
            return

        pos = portfolio["positions"][ticker]

        if shares > pos["shares"]:
            send(f"❌ You only have {pos['shares']} shares")
            return

        entry = pos["price"]
        profit = (price - entry) * shares

        trade = {
            "ticker": ticker,
            "entry_price": entry,
            "exit_price": price,
            "shares": shares,
            "profit": round(profit, 2),
            "entry_time": pos.get("entry_time", time.time()),
            "exit_time": time.time(),
            "duration_sec": int(time.time() - pos.get("entry_time", time.time())),
            "exit_reason": "manual",
            "entry_data": pos.get("entry_data", {}),
            "id": str(time.time())
        }

        save_trade(trade)

        portfolio["cash"] += shares * price
        pos["shares"] -= shares

        if pos["shares"] <= 0:
            del portfolio["positions"][ticker]

        save_portfolio(portfolio)

        send(
            f"💰 SOLD {ticker}\n"
            f"Shares: {shares} @ {price}\n"
            f"P/L: ${round(profit,2)}\n"
            f"Cash: ${round(portfolio['cash'],2)}"
        )

# ---------------- INDICATORS ----------------
def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def atr(df, period=14):
    tr = pd.concat([
        df['High'] - df['Low'],
        abs(df['High'] - df['Close'].shift()),
        abs(df['Low'] - df['Close'].shift())
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

# ---------------- DATA ----------------
def get_prices_batch(tickers):
    try:
        symbols = ",".join(tickers)

        url = f"{FMP_BASE}/quote/{symbols}?apikey={FMP_API_KEY}"

        r = SESSION.get(url, timeout=5)
        r.raise_for_status()

        data = r.json()

        prices = {}
        for item in data:
            prices[item["symbol"]] = item["price"]

        return prices

    except Exception as e:
        print(f"BATCH PRICE ERROR: {e}")
        return {}

def get_historical(ticker, limit=120):
    try:
        url = f"https://financialmodelingprep.com/stable/historical-price-eod/full?symbol={ticker}&apikey={FMP_API_KEY}"

        r = SESSION.get(url, timeout=10)
        r.raise_for_status()

        data = r.json()

        if not isinstance(data, list) or len(data) == 0:
            return None

        df = pd.DataFrame(data)

        df = df.rename(columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume"
        })

        df = df.iloc[::-1].tail(limit)

        print(f"[DATA OK] {ticker} | rows={len(df)} | last={df['Close'].iloc[-1]:.2f}")

        return df

    except Exception as e:
        print(f"HIST ERROR {ticker}: {e}")
        return None

# ---------------- MARKET ----------------
def market_condition():
    try:
        spy = get_historical("SPY", limit=60)
        qqq = get_historical("QQQ", limit=60)

        if spy is None or qqq is None or spy.empty or qqq.empty:
            return "UNCERTAIN"

        if spy["Close"].iloc[-1] > spy["Close"].rolling(50).mean().iloc[-1] \
        and qqq["Close"].iloc[-1] > qqq["Close"].rolling(50).mean().iloc[-1]:
            return "BULL"
        elif spy["Close"].iloc[-1] < spy["Close"].rolling(50).mean().iloc[-1] \
        and qqq["Close"].iloc[-1] < qqq["Close"].rolling(50).mean().iloc[-1]:
            return "BEAR"

        return "UNCERTAIN"

    except Exception as e:
        print(f"MARKET ERROR: {e}")
        return "UNCERTAIN"

# ---------------- WATCHLIST ----------------

STRONG = [
"AAPL","MSFT","NVDA","META","GOOGL",
"AMZN","AVGO","TSLA","CRM","ADBE",
"NOW","AMD","INTC","QCOM","MU",
"LRCX","ASML","ORCL","NFLX","PANW"
]

MEDIUM = [
"PLTR","SNOW","COIN","SHOP","UBER",
"PYPL","SQ","ROKU","ZS","DDOG","ENPH",
"NET","CRWD","OKTA","DOCU","MDB"
]

WEAK = [
"MCHP","INOD","PGY","AFRM","RIOT",
"MARA","SOFI","UPST","AI","FUBO",
"CEVA","SERV","BKKT","BKSY"
]

WATCHLIST = STRONG + MEDIUM + WEAK

# ---------------- ANALYSIS ----------------
def analyze(ticker, market, df):
    try:
        if df is None or df.empty:
            print(f"[ANALYZE SKIP] {ticker} - no data")
            return None
    except Exception as e:
        print(f"[analyze] ERROR: {e}")
        return None

    close = df["Close"]
    volume = df["Volume"]

    price = close.iloc[-1]
    rsi_val = rsi(close).iloc[-1]

    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1]

    avg_vol = volume.rolling(20).mean().iloc[-1]

    # ✅ NEW — breakout detection
    recent_high = close.iloc[-21:-1].max()
    breakout = price > recent_high

    # ✅ MODIFIED — market + RSI logic
    if market == "BEAR":
        return None

    if not breakout and rsi_val > 70:
        return None

    if pd.isna(ma20) or ma20 == 0:
        return None

    if not breakout and (price - ma20) / ma20 > 0.05:
        return None

    if len(close) < 2:
        return None

    prev_close = close.iloc[-2]

    # require price to stop falling (only for pullbacks)
    if not breakout and price <= prev_close:
        return None

    score = 0
    if price > ma50: score += 20
    if price < ma20 and rsi_val < 45: score += 30
    if volume.iloc[-1] > avg_vol * 1.5:
        score += 20

    # ✅ NEW — breakout bonus
    if breakout:
        score += 30

    if score < 40:
        return None

    # ✅ MODIFIED — allow breakout even if MA trend not perfect
    if not breakout and ma20 < ma50:
        return None

    atr_val = atr(df).iloc[-1]

    if pd.isna(atr_val):
        return None

    stop = price - (1.5 * atr_val)
    risk = price - stop
    if risk <= 0:
        return None

    cash = portfolio["cash"]
    if ticker in STRONG:
        risk_pct = 0.03
    elif ticker in MEDIUM:
        risk_pct = 0.02
    elif ticker in WEAK:
        risk_pct = 0.01
    else:
        return None
    
    shares = int((cash * risk_pct) / risk)
    shares = min(shares, int((cash * 0.2) / price))

    if shares <= 0:
        return None

    target = price + 2 * risk

    return ticker, price, shares, stop, target, score

# ---------------- POSITION MANAGEMENT ----------------
def manage_positions():
    global portfolio

    tickers = list(portfolio["positions"].keys())
    if not tickers:
        return
    prices = get_prices_batch(tickers)

    for ticker in tickers:
        pos = portfolio["positions"][ticker]
        price = prices.get(ticker)

        if price is None:
            continue

        entry = pos["price"]

        # -------- INIT FIELDS (SAFE) --------
        if "stop" not in pos:
            pos["stop"] = entry * 0.95
        if "highest" not in pos:
            pos["highest"] = entry
        if "partial_taken" not in pos:
            pos["partial_taken"] = False

        # ✅ ADD TARGET EXIT RIGHT HERE
        if "target" in pos and price >= pos["target"]:
            trade = {
                "ticker": ticker,
                "entry_price": entry,
                "exit_price": price,
                "shares": pos["shares"],
                "profit": round((price - entry) * pos["shares"], 2),
                "entry_time": pos.get("entry_time", time.time()),
                "exit_time": time.time(),
                "duration_sec": int(time.time() - pos.get("entry_time", time.time())),
                "exit_reason": "target",
                "entry_data": pos.get("entry_data", {}),
                "id": str(time.time())
            }

            save_trade(trade)

            portfolio["cash"] += pos["shares"] * price
            del portfolio["positions"][ticker]

            send(f"🎯 TARGET HIT {ticker}\nP/L: ${trade['profit']}")
            save_portfolio(portfolio)
            continue

        # -------- TRACK HIGH --------
        if price > pos["highest"]:
            pos["highest"] = price

        # -------- PARTIAL TAKE PROFIT --------
        if price >= entry * 1.05 and not pos["partial_taken"] and pos["shares"] > 1:
            sell = pos["shares"] // 2

            trade = {
                "ticker": ticker,
                "entry_price": entry,
                "exit_price": price,
                "shares": sell,
                "profit": round((price - entry) * sell, 2),
                "entry_time": pos.get("entry_time", time.time()),
                "exit_time": time.time(),
                "duration_sec": int(time.time() - pos.get("entry_time", time.time())),
                "exit_reason": "partial",
                "entry_data": pos.get("entry_data", {}),
                "id": str(time.time())
            }

            save_trade(trade)

            portfolio["cash"] += sell * price
            pos["shares"] -= sell

            if pos["shares"] <= 0:
                del portfolio["positions"][ticker]
                save_portfolio(portfolio)
                send(f"💰 FULL EXIT {ticker}")
                continue

            pos["partial_taken"] = True
            send(f"💰 PARTIAL {ticker}")

        # -------- BREAKEVEN --------
        if price >= entry * 1.03 and pos["stop"] < entry:
            pos["stop"] = entry

        # -------- TRAILING STOP --------
        atr_val = pos.get("atr")

        if atr_val is not None:
            trail = pos["highest"] - (2.5 * atr_val)

            if trail > pos["stop"] and trail < price:
                pos["stop"] = trail

        # -------- STOP LOSS --------
        if price < pos["stop"]:
            trade = {
                "ticker": ticker,
                "entry_price": entry,
                "exit_price": price,
                "shares": pos["shares"],
                "profit": round((price - entry) * pos["shares"], 2),
                "entry_time": pos.get("entry_time", time.time()),
                "exit_time": time.time(),
                "duration_sec": int(time.time() - pos.get("entry_time", time.time())),
                "exit_reason": "stop",
                "entry_data": pos.get("entry_data", {}),
                "id": str(time.time())
            }

            save_trade(trade)

            portfolio["cash"] += pos["shares"] * price
            cooldowns[ticker] = time.time()

            del portfolio["positions"][ticker]

            send(f"🔴 EXIT {ticker}\nP/L: ${trade['profit']}")
            save_portfolio(portfolio)
            continue
    save_portfolio(portfolio)

# ---------------- MAIN LOOP ----------------
send("🚀 BOT STARTED")
send(f"SERVER TIME: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}")

last_scan = 0

while True:
    try:
        # -------- DAILY RESET --------
        day = int(time.time() // 86400)
        if last_reset_day != day:
            last_reset_day = day

        # -------- TELEGRAM + POSITIONS --------
        get_updates()
        manage_positions()

        # -------- SCAN EVERY 5 MIN --------
        now = time.localtime()

        # ❌ Skip weekends
        if now.tm_wday >= 5:
            time.sleep(60)
            continue

        current_hour = now.tm_hour
        current_min = now.tm_min

        # run once near market close (example: 19:55)
        if current_hour == 19 and current_min >= 55 and time.time() - last_scan > 300:
            market = market_condition()

            for t in WATCHLIST:

                # -------- GET DATA --------
                try:
                    df = get_historical(t, limit=120)
                    if df is None or df.empty or len(df) < 2:
                        continue
                except Exception as e:
                    print(f"[context] ERROR: {e}")
                    continue

                close = df["Close"].dropna()

                if len(close) < 2:
                    continue

                price = close.iloc[-1]
                prev_close = close.iloc[-2]

                # -------- BREAKOUT LOGIC --------
                move = ((price - prev_close) / prev_close) * 100
                if prev_close == 0:
                    continue

                levels = [10, 15, 20]
                breakout_triggered = False

                for lvl in levels:
                    if move >= lvl:
                        if t not in breakout_memory:
                            breakout_memory[t] = set()

                        if lvl not in breakout_memory[t]:
                            send(f"🔥 BREAKOUT {t}\nMove: {round(move,2)}%")
                            breakout_memory[t].add(lvl)
                            breakout_triggered = True

                # -------- RESET --------
                if move < 8 and t in breakout_memory:
                    breakout_memory.pop(t)


                # -------- RSI FILTER --------
                rsi_val = rsi(close).iloc[-1]
                if pd.isna(rsi_val):
                    continue

                # -------- EXISTING LOGIC --------
                if t in cooldowns and time.time() - cooldowns[t] < 1800:
                    continue

                if t in portfolio["positions"]:
                    continue

                signal = last_signals.get(t)

                if signal:
                    if isinstance(signal, dict):
                        if time.time() - signal.get("time", 0) < 86400:
                            continue
                    else:
                        # old format (float)
                        if time.time() - signal < 86400:
                            continue

                result = analyze(t, market, df)

                if result:
                    ticker, price, shares, stop, target, score = result

                    entry_data = {
                        "rsi": round(rsi_val, 2),
                        "score": score,
                        "market": market,
                        "atr": round((price - stop) / 1.5, 4),  # reconstruct ATR
                        "breakout": bool(price > df["Close"].iloc[-21:-1].max()),
                        "volume_ratio": round(
                            df["Volume"].iloc[-1] /
                            df["Volume"].rolling(20).mean().iloc[-1], 2
                        ))
                    }

                    capital = shares * price
                    risk_amount = (price - stop) * shares

                    send(f"""
🟢 ENTRY

{ticker}
Market: {market}

Price: {round(price,2)}
RSI: {round(rsi_val,1)}
Score: {score}

Buy: {shares} shares
Capital: ${round(capital,2)}

Stop: {round(stop,2)}
Target: {round(target,2)}

Risk: ${round(risk_amount,2)}
""")

                    last_signals[t] = {
                        "time": time.time(),
                        "entry_data": safe_convert(entry_data)
                    }
                    save_signals()

            # -------- UPDATE SCAN TIMER --------
            last_scan = time.time()

        time.sleep(25)

    except Exception as e:
        send(f"⚠️ ERROR {e}")
        time.sleep(25)