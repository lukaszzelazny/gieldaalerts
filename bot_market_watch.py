#!/usr/bin/env python3
import os
import time
import requests
from datetime import datetime, time as dt_time, date
import pytz
import yfinance as yf
import pandas as pd
from ticker_analizer import getScoreWithDetails
from moving_analizer import calculate_moving_averages_signals

from dotenv import load_dotenv
load_dotenv()

# + twoje istniejące importy (yfinance, telegram, etc.)
# ----------------------
# KONFIGURACJA (dostosuj)
# ----------------------


TOKEN = os.getenv("TG_BOT_TOKEN")      # ustaw w ENV: TG_BOT_TOKEN
CHAT_ID = os.getenv("TG_CHAT_ID")      # ustaw w ENV: TG_CHAT_ID

TICKERS_GPW = os.getenv("TICKERS_GPW", "").split(",") if os.getenv("TICKERS_GPW") else []
TICKERS_NEWCONNECT = os.getenv("TICKERS_NEWCONNECT", "").split(",") if os.getenv("TICKERS_NEWCONNECT") else []
TICKERS_NASDAQ = os.getenv("TICKERS_NASDAQ", "").split(",") if os.getenv("TICKERS_NASDAQ") else []
TICKERS_NYSE = os.getenv("TICKERS_NYSE", "").split(",") if os.getenv("TICKERS_NYSE") else []
MY_TICKERS = os.getenv("MY_TICKERS", "").split(",") if os.getenv("MY_TICKERS") else []

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
    "czerwony": 10.0,
    "zolty": 7.0,
    "zielony": 5.0
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

def send_telegram_message(text, parse_mode="HTML"):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True }
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

def download_with_retry(tickers, period="1y", max_retries=3, delay=2):
    for attempt in range(max_retries):
        try:
            hist = yf.download(tickers, period=period, group_by="ticker", threads=True)
            return hist
        except Exception as e:
            print(f"Próba {attempt+1} nie powiodła się: {e}")
            time.sleep(delay)
    raise Exception(f"Nie udało się pobrać danych po {max_retries} próbach")


def check_prices_for_exchange(exchange):
    RATING_LABELS = {
        2: "🟢🟢 <b>Mocne kupuj</b>",
        1: "🟢 <b>Kupuj</b>",
        0: "⚪ <b>Trzymaj</b>",
        -1: "🔴 <b>Sprzedaj</b>",
        -2: "🔴🔴 <b>Mocne sprzedaj</b>",
    }
    global alerted_types_today  # { ticker: set(alert_type) }
    tickers_for_exchange = [t for t, ex in TICKERS.items() if ex == exchange]
    if not tickers_for_exchange:
        return

    missing_data_tickers = []

    try:
        hist = download_with_retry(tickers_for_exchange)
    except Exception as e:
        msg = f"❗ Błąd przy pobieraniu danych dla giełdy {exchange}: {e}"
        print(msg)
        send_telegram_message(msg)
        return

    for ticker in tickers_for_exchange:
        try:
            df = hist[ticker]
            if df is None or len(df) < 240:
                missing_data_tickers.append(ticker)
                continue

            if ticker not in alerted_types_today:
                alerted_types_today[ticker] = set()

            # === ORYGINALNY ALERT CENOWY ===
            prev_close = df['Close'].iloc[-2]
            current_price = df['Close'].iloc[-1]
            spadek = ((prev_close - current_price) / prev_close) * 100

            alert_code = alert_color_name(spadek)

            if alert_code and alert_code not in alerted_types_today[ticker]:
                alerted_types_today[ticker].add(alert_code)
                msg = (
                    f"{alert_code}: !!! <b>{ticker}</b> !!!\n"
                    f"Cena poprzedniego zamknięcia: {prev_close:.2f}\n"
                    f"Aktualna cena: {current_price:.2f}\n"
                    f"Spadek: {spadek:.2f}%"
                )
                send_telegram_message(msg)

            if ticker in MY_TICKERS:
                rate, details = getScoreWithDetails(df)
                alert_code_s = str(rate) + 's'
                msg = ''
                if alert_code_s not in alerted_types_today[ticker]:
                    msg += f"Wskaźniki dla: <b>{ticker}</b>:\n"
                    msg +=f"Trend: {RATING_LABELS.get(rate)}\n"
                    alerted_types_today[ticker].add(alert_code_s)
                    #print(getDetailsText(details))
                ma_results = calculate_moving_averages_signals(df)
                movingRate = ma_results['overall_summary']['signal']
                alert_code_m = str(movingRate) + 'm'
                if alert_code_m not in alerted_types_today[ticker]:
                    msg = msg + f"Wskaźniki dla: <b>{ticker}</b>:\n" if ticker not in msg else msg
                    msg += f"Krzywe kroczące: {RATING_LABELS.get(movingRate)}"
                    alerted_types_today[ticker].add(alert_code_m)

                if msg:
                    send_telegram_message(msg)


        except Exception as ex:
            print(f"Błąd dla {ticker}: {ex}")
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


def test():
    tickers_for_exchange = ["SNT.WA"]
    try:
        hist = download_with_retry(tickers_for_exchange)
    except Exception as e:
        msg = f"❗ Błąd przy pobieraniu danych dla giełdy : {e}"
        print(msg)
        send_telegram_message(msg)
        return
    ticker = tickers_for_exchange[0]

    RATING_LABELS = {
        2: "🟢🟢 <b>Mocne kupuj</b>",
        1: "🟢 <b>Kupuj</b>",
        0: "⚪ <b>Trzymaj</b>",
        -1: "🔴 <b>Sprzedaj</b>",
        -2: "🔴🔴 <b>Mocne sprzedaj</b>",
    }

    df = hist if not isinstance(hist.columns, pd.MultiIndex) else hist[ticker]
    rate, details = getScoreWithDetails(df)
    msg = f"Wskaźniki dla {ticker} to {rate}"
    print(msg)
    msg = getDetailsText(details)
    print(msg)

    ma_results = calculate_moving_averages_signals(df)
    movingRate = ma_results['overall_summary']['signal']
    msg = f"Krzywe kroczące dla {ticker} to {RATING_LABELS.get(movingRate)}"
    print(msg)



def getDetailsText(details):
        # Łączenie elementów tablicy w tekst, każdy w nowej linii
    msg = "\n".join(str(item) for item in details)
    msg = f"Szczegóły to \n"+ f"{msg}"
    return msg


if __name__ == "__main__":
    try:
        #test()
        main_loop()
    except KeyboardInterrupt:
        print("Przerwano ręcznie.")
