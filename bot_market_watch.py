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
    for stock_info in ALL_TICKERS:
        ticker = stock_info["symbol"]
        market = stock_info["market"]
        if market != exchange:
            continue

        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="2d")  # potrzeba minimum 2 dni
            if hist is None or len(hist) < 2:
                # powiadamiamy tylko raz o problemie z danym tickerem
                if ticker not in tickery_z_bledem:
                    msg = f"❗ Brak wystarczających danych dla {ticker} — możliwy błędny ticker lub brak notowań."
                    print(msg)
                    send_telegram_message(msg)
                    tickery_z_bledem.add(ticker)
                continue

            # jeśli wcześniej był błąd, a teraz dane są ok -> usuwamy z zestawu błędów
            if ticker in tickery_z_bledem:
                tickery_z_bledem.remove(ticker)

            prev_close = hist['Close'].iloc[-2]
            current_price = hist['Close'].iloc[-1]

            # obliczamy spadek względem poprzedniego zamknięcia
            spadek = ((prev_close - current_price) / prev_close) * 100

            alert_name = alert_color_name(spadek)
            if alert_name:
                message = (
                    f"{alert_name}: {ticker}\n"
                    f"Cena poprzedniego zamknięcia: {prev_close:.2f}\n"
                    f"Aktualna cena: {current_price:.2f}\n"
                    f"Spadek: {spadek:.2f}%"
                )
                send_telegram_message(message)

        except Exception as e:
            if ticker not in tickery_z_bledem:
                err = f"❗ Błąd przy pobieraniu danych dla {ticker}: {e}"
                print(err)
                send_telegram_message(err)
                tickery_z_bledem.add(ticker)


def main_loop():
    # Wyślij startowe potwierdzenie
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
