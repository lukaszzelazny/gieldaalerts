#!/usr/bin/env python3
import os
import time
import requests
from datetime import datetime, time as dt_time, date
import pytz
import yfinance as yf

# ----------------------
# KONFIGURACJA (dostosuj)
# ----------------------
TOKEN = os.getenv("TG_BOT_TOKEN")      # ustaw w ENV: TG_BOT_TOKEN
CHAT_ID = os.getenv("TG_CHAT_ID")      # ustaw w ENV: TG_CHAT_ID

TICKERS_GPW = os.getenv("TICKERS_GPW", "").split(",") if os.getenv("TICKERS_GPW") else []
TICKERS_NEWCONNECT = os.getenv("TICKERS_NEWCONNECT", "").split(",") if os.getenv("TICKERS_NEWCONNECT") else []
TICKERS_NASDAQ = os.getenv("TICKERS_NASDAQ", "").split(",") if os.getenv("TICKERS_NASDAQ") else []
TICKERS_NYSE = os.getenv("TICKERS_NYSE", "").split(",") if os.getenv("TICKERS_NYSE") else []

# Łączna lista z info o giełdzie
ALL_TICKERS = []
for t in TICKERS_GPW:
    if t.strip():
        ALL_TICKERS.append({"symbol": t.strip(), "market": "GPW"})
for t in TICKERS_NEWCONNECT:
    if t.strip():
        ALL_TICKERS.append({"symbol": t.strip(), "market": "NEWCONNECT"})
for t in TICKERS_NASDAQ:
    if t.strip():
        ALL_TICKERS.append({"symbol": t.strip(), "market": "NASDAQ"})

for t in TICKERS_NYSE:
    if t.strip():
        ALL_TICKERS.append({"symbol": t.strip(), "market": "NYSE"})


# Interwały (sekundy)
PRICE_CHECK_INTERVAL = 5 * 60    # 5 minut dla cen
OPEN_CHECK_INTERVAL = 30         # co 30s sprawdzamy czy giełda się otworzyła (real-time)
OFF_HOURS_SLEEP = 30 * 60        # jak giełda zamknięta to dłuższy sleep (tylko gdy wszystkie zamknięte)

# Progi alertów (w procentach)
DROP_THRESHOLDS = {
    "czerwony": 7.0,
    "zolty": 5.0,
    "zielony": 3.0
}
# ----------------------

if not TOKEN or not CHAT_ID:
    raise SystemExit("Ustaw zmienne środowiskowe TG_BOT_TOKEN i TG_CHAT_ID przed uruchomieniem.")

# Strefy czasowe
warsaw_tz = pytz.timezone("Europe/Warsaw")
us_tz = pytz.timezone("US/Eastern")

# Stan: zapobiega powtarzaniu powiadomień o błędach
tickery_z_bledem = set()

# Czy dana giełda była już (wczoraj/ostatnio) otwarta — by wysłać alert o otwarciu raz dziennie
last_open_date = { "GPW": None, "NYSE": None, "NASDAQ": None }

# Ostatni czas sprawdzenia cen dla giełdy (timestamp)
last_price_check_ts = { "GPW": 0, "NYSE": 0, "NASDAQ": 0 }

alerted_types_today = {}

def load_tickers():
    tickers = {}
    for ticker in os.getenv("TICKERS_GPW", "").split(","):
        t = ticker.strip()
        if t:
            tickers[t] = "GPW"
    for ticker in os.getenv("TICKERS_NEWCONNECT", "").split(","):
        t = ticker.strip()
        if t:
            tickers[t] = "NEWCONNECT"
    for ticker in os.getenv("TICKERS_NASDAQ", "").split(","):
        t = ticker.strip()
        if t:
            tickers[t] = "NASDAQ"
    for ticker in os.getenv("TICKERS_NYSE", "").split(","):
        t = ticker.strip()
        if t:
            tickers[t] = "NYSE"

    return tickers

TICKERS = load_tickers()

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if not resp.ok:
            print(f"[TG] Błąd wysyłki: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[TG] Wyjątek przy wysyłce: {e}")


def is_exchange_open(exchange):
    """Zwraca True jeżeli dana giełda jest otwarta teraz (proste reguły: dni robocze i godziny)."""
    if exchange == "GPW":
        now = datetime.now(warsaw_tz)
        if now.weekday() >= 5:  # sobota/niedziela
            return False
        return dt_time(9, 0) <= now.time() <= dt_time(17, 0)

    if exchange in ("NYSE", "NASDAQ"):
        now = datetime.now(us_tz)
        if now.weekday() >= 5:
            return False
        return dt_time(9, 30) <= now.time() <= dt_time(16, 0)

    # domyślnie: otwarte
    return True


def market_open_watch():
    """Sprawdza otwarcie giełd i wysyła powiadomienie raz dziennie o ich otwarciu."""
    global last_open_date
    exchanges = set(TICKERS.values())
    for ex in exchanges:
        if ex not in last_open_date:
            last_open_date[ex] = None

        open_now = is_exchange_open(ex)
        today = date.today()

        if open_now:
            # jeśli jeszcze dziś nie wysłaliśmy powiadomienia o otwarciu -> wyślij
            if last_open_date[ex] != today:
                alerted_types_today.clear()
                send_telegram_message(f"🟢 {ex} — otwarta. Bot działa i będzie monitorował tickery na tej giełdzie.")
                last_open_date[ex] = today
        else:
            # jeśli giełda zamknięta, resetujemy flagę, ale tylko gdy dzień się zmienił
            # (zapobiega wysyłaniu otwarcia kilka razy za jeden dzień)
            if last_open_date[ex] is not None and last_open_date[ex] != today:
                last_open_date[ex] = None
            # nie ruszamy jeśli last_open_date == None


def alert_color_name(spadek):
    """Zwraca nagłówek alertu wg progów lub None."""
    if spadek >= DROP_THRESHOLDS["czerwony"]:
        return "🔴 CZERWONY ALERT"
    if DROP_THRESHOLDS["zolty"] <= spadek < DROP_THRESHOLDS["czerwony"]:
        return "🟡 ŻÓŁTY ALERT"
    if DROP_THRESHOLDS["zielony"] <= spadek < DROP_THRESHOLDS["zolty"]:
        return "🟢 ZIELONY ALERT"
    return None


def check_prices_for_exchange(exchange):
    global alerted_types_today  # { ticker: set(alert_type) }
    tickers_for_exchange = [t for t, ex in TICKERS.items() if ex == exchange]
    if not tickers_for_exchange:
        return

    missing_data_tickers = []

    try:
        hist = yf.download(tickers_for_exchange, period="1y", group_by="ticker", threads=True)
    except Exception as e:
        msg = f"❗ Błąd przy pobieraniu danych dla giełdy {exchange}: {e}"
        print(msg)
        send_telegram_message(msg)
        return

    for ticker in tickers_for_exchange:
        try:
            df = hist[ticker] if len(tickers_for_exchange) > 1 else hist
            if df is None or len(df) < 252:
                missing_data_tickers.append(ticker)
                continue

            if ticker not in alerted_types_today:
                alerted_types_today[ticker] = set()

            prev_close = df['Close'].iloc[-2]
            current_price = df['Close'].iloc[-1]
            spadek = ((prev_close - current_price) / prev_close) * 100

            alert_code = alert_color_name(spadek)

            if alert_code not in alerted_types_today[ticker]:
                alerted_types_today[ticker].add(alert_code)
                msg = (
                    f"{alert_code}: {ticker}\n"
                    f"Cena poprzedniego zamknięcia: {prev_close:.2f}\n"
                    f"Aktualna cena: {current_price:.2f}\n"
                    f"Spadek: {spadek:.2f}%"
                )
                send_telegram_message(msg)

            # MA i RSI
            df['MA50'] = df['Close'].rolling(window=50).mean()
            df['MA200'] = df['Close'].rolling(window=200).mean()

            delta = df['Close'].diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            avg_gain = gain.rolling(window=14).mean()
            avg_loss = loss.rolling(window=14).mean()
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            last_rsi = rsi.iloc[-1]

            def send_rsi_alert(msg, alert_code):
                if alert_code not in alerted_types_today[ticker]:
                    send_telegram_message(msg)
                    alerted_types_today[ticker].add(alert_code)

            # RSI + Trend (MA50 vs MA200) - umiarkowane sygnały
            if df['MA50'].iloc[-1] > df['MA200'].iloc[-1] and last_rsi < 40:
                send_rsi_alert(f"📈 {ticker}: Trend wzrostowy + RSI < 40 (potencjalna okazja kupna)", "rsi_buy_moderate")
            if df['MA50'].iloc[-1] < df['MA200'].iloc[-1] and last_rsi > 60:
                send_rsi_alert(f"📉 {ticker}: Trend spadkowy + RSI > 60 (potencjalny sygnał sprzedaży)", "rsi_sell_moderate")

            # RSI + Trend (MA50 vs MA200) - ekstremalne sygnały
            if df['MA50'].iloc[-1] > df['MA200'].iloc[-1] and last_rsi < 30:
                send_rsi_alert(f"💎 {ticker}: Trend wzrostowy + RSI < 30 (silny sygnał kupna)", "rsi_buy_strong")
            if df['MA50'].iloc[-1] < df['MA200'].iloc[-1] and last_rsi > 70:
                send_rsi_alert(f"🔥 {ticker}: Trend spadkowy + RSI > 70 (silny sygnał sprzedaży)", "rsi_sell_strong")

            # 52 tyg. high/low z wolumenem
            avg_volume = df['Volume'].tail(20).mean()
            high_52w = df['Close'].max()
            low_52w = df['Close'].min()
            if current_price >= high_52w * 0.999 and df['Volume'].iloc[-1] > 1.5 * avg_volume:
                send_rsi_alert(f"🚀 {ticker}: Nowe 52-week High z dużym wolumenem", "volume_high")
            if current_price <= low_52w * 1.001 and df['Volume'].iloc[-1] > 1.5 * avg_volume:
                send_rsi_alert(f"⚠️ {ticker}: Nowe 52-week Low z dużym wolumenem", "volume_low")

        except Exception as ex:
            print(ex)
            missing_data_tickers.append(ticker)

    if missing_data_tickers:
        send_telegram_message(f"❗ Brak danych dla: {', '.join(missing_data_tickers)}")

def main_loop():
    send_telegram_message("🚀 Bot giełdowy wystartował. Będę monitorował otwarcia giełd i ceny tam, gdzie giełdy są otwarte.")

    # Zainicjuj last_price_check_ts
    for ex in set(TICKERS.values()):
        last_price_check_ts[ex] = 0

    while True:
        # 1) Sprawdź otwarcia giełd często (np. co OPEN_CHECK_INTERVAL)
        market_open_watch()

        # 2) Dla każdej giełdy, jeśli jest otwarta i minął interwał, sprawdź ceny
        now_ts = time.time()
        any_exchange_open = False
        for ex in set(TICKERS.values()):
            if is_exchange_open(ex):
                any_exchange_open = True
                # jeżeli minął interwał od ostatniego sprawdzenia tej giełdy
                if now_ts - last_price_check_ts.get(ex, 0) >= PRICE_CHECK_INTERVAL:
                    print(f"[{datetime.now()}] Sprawdzam ceny dla giełdy {ex}")
                    check_prices_for_exchange(ex)
                    last_price_check_ts[ex] = now_ts
            else:
                # giełda zamknięta -> nic nie robimy
                pass

        # 3) Sleep: jeśli wszystkie giełdy zamknięte możemy spać dłużej (oszczędność)
        if not any_exchange_open:
            time.sleep(OFF_HOURS_SLEEP)
        else:
            time.sleep(OPEN_CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("Przerwano ręcznie.")
