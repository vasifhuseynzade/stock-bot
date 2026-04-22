import requests
import time
import json
import os
import yfinance as yf
import pandas as pd

TOKEN = "8644659149:AAFMv5XwLEsYQlP_JPrqJOxVSe4Z_teuMp8"
CHAT_ID = "1918330212"

PORTFOLIO_FILE = "portfolio.json"

# ---------------- PORTFOLIO ----------------

def load_portfolio():
    if not os.path.exists(PORTFOLIO_FILE):
        return {"cash": 4000, "positions": {}}
    with open(PORTFOLIO_FILE, "r") as f:
        return json.load(f)

def save_portfolio(data):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(data, f, indent=4)

portfolio = load_portfolio()
last_update_id = None
last_signals = {}
last_alerts = {}

# ---------------- TELEGRAM ----------------

def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=5)
    except Exception as e:
        print("Telegram error:", e)

# ---------------- COMMANDS ----------------

def handle_command(text):
    global portfolio

    parts = text.lower().split()

    if len(parts) < 5:
        return

    # -------- BUY --------
    if parts[0] == "bought":
        ticker = parts[1].upper()
        shares = int(parts[2])
        price = float(parts[4])

        cost = shares * price

        if portfolio["cash"] < cost:
            send("❌ Not enough cash")
            return

        portfolio["cash"] -= cost

        if ticker in portfolio["positions"]:
            pos = portfolio["positions"][ticker]
            total_shares = pos["shares"] + shares
            avg_price = ((pos["shares"] * pos["price"]) + (shares * price)) / total_shares

            portfolio["positions"][ticker]["shares"] = total_shares
            portfolio["positions"][ticker]["price"] = avg_price
        else:
            portfolio["positions"][ticker] = {
                "shares": shares,
                "price": price,
                "stop": price * 0.95
            }

        save_portfolio(portfolio)

        send(f"✅ BOUGHT {ticker} {shares} @ {price}\nCash: ${round(portfolio['cash'],2)}")

    # -------- SELL --------
    elif parts[0] == "sold":
        ticker = parts[1].upper()
        shares = int(parts[2])
        price = float(parts[4])

        if ticker not in portfolio["positions"]:
            send("❌ No position to sell")
            return

        shares_owned = portfolio["positions"][ticker]["shares"]

        if shares > shares_owned:
            send(f"❌ Cannot sell {shares}, only have {shares_owned}")
            return

        entry = portfolio["positions"][ticker]["price"]
        profit = (price - entry) * shares

        portfolio["cash"] += shares * price

        remaining = shares_owned - shares

        if remaining > 0:
            portfolio["positions"][ticker]["shares"] = remaining
        else:
            del portfolio["positions"][ticker]

        save_portfolio(portfolio)

        send(f"💰 SOLD {ticker} {shares}\nProfit: ${round(profit,2)}\nBalance: ${round(portfolio['cash'],2)}")

# ---------------- TELEGRAM UPDATES ----------------

def get_updates():
    global last_update_id

    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    if last_update_id:
        url += f"?offset={last_update_id + 1}"

    try:
        res = requests.get(url, timeout=5).json()
    except:
        return

    for update in res.get("result", []):
        last_update_id = update["update_id"]

        if "message" in update:
            text = update["message"].get("text", "")
            handle_command(text)

# ---------------- ANALYSIS ----------------

WATCHLIST = ["MSFT","INTC","AMPL","MGNI","PATH","INOD","SERV","PGY","CEVA","MCHP","AVAV"]

def analyze(ticker):
    df = yf.Ticker(ticker).history(period="3mo")

    if len(df) < 50:
        return None

    close = df["Close"]
    volume = df["Volume"]

    price = close.iloc[-1]
    rsi_val = rsi(close).iloc[-1]

    ma20 = close.rolling(20).mean().iloc[-1]
    ma50 = close.rolling(50).mean().iloc[-1]

    avg_vol = volume.rolling(20).mean().iloc[-1]
    vol_now = volume.iloc[-1]

    market = market_condition()

    if market == "BEAR":
        return None

    atr_val = atr(df).iloc[-1]

    score = 0
    if price > ma50:
        score += 20
    if price < ma20 and rsi_val < 40:
        score += 30
    if vol_now > avg_vol * 1.5:
        score += 20

    if score < 40:
        return None

    portfolio_cash = portfolio["cash"]

    if market == "BULL":
        allocation = 0.25 if score >= 60 else 0.15
    elif market == "UNCERTAIN":
        allocation = 0.10
    else:
        allocation = 0.05

    capital = portfolio_cash * allocation
    shares = int(capital / price)

    if shares == 0:
        return None

    stop = price - (1.5 * atr_val)
    target = price + (2 * (price - stop))

    risk_per_share = price - stop
    total_risk = risk_per_share * shares

    return ticker, price, rsi_val, shares, stop, target, total_risk, score, market

# ---------------- INDICATORS ----------------

def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def atr(df, period=14):
    high_low = df['High'] - df['Low']
    high_close = abs(df['High'] - df['Close'].shift())
    low_close = abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def get_price(ticker):
    try:
        df = yf.Ticker(ticker).history(period="1d")
        return df["Close"].iloc[-1]
    except:
        return None

# ---------------- SMART ALERTS ----------------

def smart_alerts(ticker):
    df = yf.Ticker(ticker).history(period="1mo")

    if len(df) < 20:
        return None

    close = df["Close"]
    volume = df["Volume"]

    price = close.iloc[-1]
    prev_price = close.iloc[-2]

    change_pct = (price - prev_price) / prev_price * 100

    avg_vol = volume.rolling(20).mean().iloc[-1]
    vol_now = volume.iloc[-1]

    rsi_val = rsi(close).iloc[-1]

    if change_pct > 5 and vol_now > avg_vol * 1.5:
        return f"🔥 BREAKOUT {ticker}\nMove: {round(change_pct,2)}%"

    if rsi_val > 70 and vol_now > avg_vol * 2:
        return f"⚠️ HYPE {ticker}\nRSI: {round(rsi_val,1)}"

    if change_pct < -5 and vol_now > avg_vol * 1.5:
        return f"📉 DUMP {ticker}\nDrop: {round(change_pct,2)}%"

    return None

# ---------------- MARKET ----------------

def market_condition():
    spy = yf.Ticker("SPY").history(period="3mo")
    qqq = yf.Ticker("QQQ").history(period="3mo")

    spy_price = spy["Close"].iloc[-1]
    spy_ma50 = spy["Close"].rolling(50).mean().iloc[-1]

    qqq_price = qqq["Close"].iloc[-1]
    qqq_ma50 = qqq["Close"].rolling(50).mean().iloc[-1]

    if spy_price > spy_ma50 and qqq_price > qqq_ma50:
        return "BULL"
    elif spy_price < spy_ma50 and qqq_price < qqq_ma50:
        return "BEAR"
    else:
        return "UNCERTAIN"

# ---------------- POSITION MANAGEMENT ----------------

def manage_positions():
    global portfolio

    for ticker in list(portfolio["positions"].keys()):
        pos = portfolio["positions"][ticker]

        price = get_price(ticker)
        if price is None:
            continue

        entry = pos["price"]

        if "stop" not in pos:
            pos["stop"] = entry * 0.95

        stop = pos["stop"]

        if price >= entry * 1.05 and stop < entry:
            pos["stop"] = entry
            send(f"🔵 {ticker} breakeven")

        if price >= entry * 1.10:
            new_stop = price * 0.95
            if new_stop > stop:
                pos["stop"] = new_stop
                send(f"🟢 {ticker} profit lock")

        if price < pos["stop"]:
            send(f"🔴 EXIT {ticker} @ {round(price,2)}")
            del portfolio["positions"][ticker]

            if ticker in last_signals:
                del last_signals[ticker]

            save_portfolio(portfolio)
            continue

        portfolio["positions"][ticker] = pos

    save_portfolio(portfolio)

# ---------------- MAIN LOOP ----------------

last_scan = 0

while True:
    try:
        get_updates()
        manage_positions()

        if time.time() - last_scan > 300:
            for t in WATCHLIST:

                if t in portfolio["positions"]:
                    continue

                alert = smart_alerts(t)

                if alert:
                    if t in last_alerts and last_alerts[t] == alert:
                        continue

                    last_alerts[t] = alert
                    send(alert)

                result = analyze(t)

                if result:
                    if t in last_signals:
                        continue

                    last_signals[t] = True

                    ticker, price, rsi_val, shares, stop, target, total_risk, score, market = result

                    send(f"""
🟢 ENTRY SIGNAL

{ticker}
Market: {market}

Price: {round(price,2)}
RSI: {round(rsi_val,1)}
Score: {score}

Buy: {shares} shares
Capital: ${round(shares * price,2)}

Stop: {round(stop,2)}
Target: {round(target,2)}

Risk: ${round(total_risk,2)}
""")

            last_scan = time.time()

        time.sleep(5)

    except Exception as e:
        send(f"⚠️ ERROR: {e}")
        time.sleep(10)