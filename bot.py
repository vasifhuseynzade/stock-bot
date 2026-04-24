import requests
import time
import json
import os
import yfinance as yf
import pandas as pd

TOKEN = os.getenv("TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "0"))

if not TOKEN or CHAT_ID == 0:
    raise Exception("❌ Missing TOKEN or CHAT_ID")

PORTFOLIO_FILE = "portfolio.json"

# ---------------- GLOBAL ----------------
portfolio = None
last_update_id = None
SIGNALS_FILE = "signals.json"

def load_signals():
    if not os.path.exists(SIGNALS_FILE):
        return {}
    try:
        with open(SIGNALS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_signals():
    temp = SIGNALS_FILE + ".tmp"
    with open(temp, "w") as f:
        json.dump(last_signals, f)
    os.replace(temp, SIGNALS_FILE)

last_signals = load_signals()
cooldowns = {}
last_reset_day = None

# ---------------- PORTFOLIO ----------------
def load_portfolio():
    if not os.path.exists(PORTFOLIO_FILE):
        return {"cash": 4000, "positions": {}}
    try:
        with open(PORTFOLIO_FILE, "r") as f:
            return json.load(f)
    except:
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
    except:
        pass

# ---------------- TELEGRAM INPUT ----------------
def get_updates():
    global last_update_id

    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"

    if last_update_id:
        url += f"?offset={last_update_id + 1}"

    try:
        res = requests.get(url, timeout=5).json()
    except:
        return

    for u in res.get("result", []):
        last_update_id = u["update_id"]

        if "message" in u:
            handle_command(u["message"].get("text", ""))

# ---------------- COMMANDS ----------------
def handle_command(text):
    global portfolio

    parts = text.lower().split()

    # basic validation
    if len(parts) < 5:
        return

    try:
        action = parts[0]
        ticker = parts[1].upper()
        shares = int(parts[2])
        price = float(parts[4])
    except:
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
            portfolio["positions"][ticker] = {
                "shares": shares,
                "price": price,
                "stop": price * 0.95,
                "highest": price,
                "partial_taken": False
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
def get_price(ticker):
    try:
        df = yf.Ticker(ticker).history(period="1d", interval="1m")
        if df.empty:
            return None
        return float(df["Close"].iloc[-1])
    except:
        return None

# ---------------- MARKET ----------------
def market_condition():
    try:
        spy = yf.Ticker("SPY").history(period="3mo")
        qqq = yf.Ticker("QQQ").history(period="3mo")

        if spy.empty or qqq.empty:
            return "UNCERTAIN"

        if spy["Close"].iloc[-1] > spy["Close"].rolling(50).mean().iloc[-1] \
        and qqq["Close"].iloc[-1] > qqq["Close"].rolling(50).mean().iloc[-1]:
            return "BULL"
        elif spy["Close"].iloc[-1] < spy["Close"].rolling(50).mean().iloc[-1] \
        and qqq["Close"].iloc[-1] < qqq["Close"].rolling(50).mean().iloc[-1]:
            return "BEAR"
        return "UNCERTAIN"
    except:
        return "UNCERTAIN"

# ---------------- ANALYSIS ----------------
WATCHLIST = ["AAPL","NVDA","TSLA","META","AMD","MSFT","AMZN","PLTR","COIN"]

def analyze(ticker, market):
    try:
        df = yf.Ticker(ticker).history(period="3mo")
        if df.empty or len(df) < 50:
            return None
    except:
        return None

    close = df["Close"]
    volume = df["Volume"]

    price = close.iloc[-1]
    rsi_val = rsi(close).iloc[-1]

    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1]

    avg_vol = volume.rolling(20).mean().iloc[-1]

    if market == "BEAR" or rsi_val > 70:
        return None

    if pd.isna(ma20) or ma20 == 0:
        return None

    if (price - ma20) / ma20 > 0.05:
        return None

    if len(close) < 2:
        return None

    prev_close = close.iloc[-2]

    # require price to stop falling (basic confirmation)
    if price <= prev_close:
        return None

    score = 0
    if price > ma50: score += 20
    if price < ma20 and rsi_val < 45: score += 30
    if volume.iloc[-1] > avg_vol * 1.5:
        score += 20

    if score < 40:
        return None

    if ma20 < ma50:
        return None

    atr_val = atr(df).iloc[-1]

    if pd.isna(atr_val):
        return None

    stop = price - (1.5 * atr_val)
    risk = price - stop
    if risk <= 0:
        return None

    cash = portfolio["cash"]
    shares = int((cash * 0.02) / risk)
    shares = min(shares, int((cash * 0.2) / price))

    if shares <= 0:
        return None

    target = price + 2 * risk

    return ticker, price, shares, stop, target, score

# ---------------- POSITION MANAGEMENT ----------------
def manage_positions():
    global portfolio

    for ticker in list(portfolio["positions"].keys()):
        pos = portfolio["positions"][ticker]

        price = get_price(ticker)
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

        # -------- TRACK HIGH --------
        if price > pos["highest"]:
            pos["highest"] = price

        # -------- PARTIAL TAKE PROFIT --------
        if price >= entry * 1.05 and not pos["partial_taken"] and pos["shares"] > 1:
            sell = pos["shares"] // 2

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
        try:
            df = yf.Ticker(ticker).history(period="3mo")

            if not df.empty:
                atr_val = atr(df).iloc[-1]
                trail = pos["highest"] - (1.2 * atr_val)

                if trail > pos["stop"]:
                    pos["stop"] = trail
        except:
            pass

        # -------- STOP LOSS --------
        if price < pos["stop"]:
            portfolio["cash"] += pos["shares"] * price
            cooldowns[ticker] = time.time()

            del portfolio["positions"][ticker]

            send(f"🔴 EXIT {ticker}")
            save_portfolio(portfolio)
            continue


    save_portfolio(portfolio)

# ---------------- MAIN LOOP ----------------
send("🚀 BOT STARTED")

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
        if time.time() - last_scan > 300:
            market = market_condition()

            for t in WATCHLIST:

                # cooldown after exit
                if t in cooldowns and time.time() - cooldowns[t] < 1800:
                    continue

                # already holding
                if t in portfolio["positions"]:
                    continue

                # prevent spam signals (24h cooldown)
                if t in last_signals and time.time() - last_signals[t] < 86400:
                    continue

                result = analyze(t, market)

                if result:
                    ticker, price, shares, stop, target, score = result

                    send(f"""
🟢 ENTRY

{ticker}
Price: {round(price,2)}
Score: {score}

Buy: {shares}
Stop: {round(stop,2)}
Target: {round(target,2)}
""")

                    last_signals[t] = time.time()
                    save_signals()

            # IMPORTANT: prevent spam loop
            last_scan = time.time()

        time.sleep(5)

    except Exception as e:
        send(f"⚠️ ERROR {e}")
        time.sleep(10)