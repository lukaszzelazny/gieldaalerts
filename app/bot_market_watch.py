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
import asyncio
import threading
# from telegram.ext import Updater, CommandHandler
from telegram.ext import Application, CommandHandler
import multiprocessing

from dotenv import load_dotenv
load_dotenv()

# + twoje istniejące importy (yfinance, telegram, etc.)
# ----------------------
# KONFIGURACJA (dostosuj)
# ----------------------

import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

RATING_LABELS = {
    2: "🟢🟢 <b>Mocne kupuj</b>",
    1: "🟢 <b>Kupuj</b>",
    0: "⚪ <b>Trzymaj</b>",
    -1: "🔴 <b>Sprzedaj</b>",
    -2: "🔴🔴 <b>Mocne sprzedaj</b>",
}

TOKEN = os.getenv("TG_BOT_TOKEN")      # ustaw w ENV: TG_BOT_TOKEN
CHAT_ID = os.getenv("TG_CHAT_ID")      # ustaw w ENV: TG_CHAT_ID

TICKERS_GPW = os.getenv("TICKERS_GPW", "").split(",") if os.getenv("TICKERS_GPW") else []
TICKERS_NEWCONNECT = os.getenv("TICKERS_NEWCONNECT", "").split(",") if os.getenv("TICKERS_NEWCONNECT") else []
TICKERS_NASDAQ = os.getenv("TICKERS_NASDAQ", "").split(",") if os.getenv("TICKERS_NASDAQ") else []
TICKERS_NYSE = os.getenv("TICKERS_NYSE", "").split(",") if os.getenv("TICKERS_NYSE") else []
MY_TICKERS = os.getenv("MY_TICKERS", "").split(",") if os.getenv("MY_TICKERS") else []
OBSERVABLE_TICKERS = os.getenv("OBSERVABLE_TICKERS", "").split(",") if os.getenv("OBSERVABLE_TICKERS") else []

activeAnalize = False

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
    "czerwony": float(os.getenv("ALERT_THRESHOLD_RED", "10.0")),
    "zolty": float(os.getenv("ALERT_THRESHOLD_YELLOW", "7.0")),
    "zielony": float(os.getenv("ALERT_THRESHOLD_GREEN", "5.0"))
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

def download_with_retry_onlyAt(ticker, max_retries=3, delay=2):
    for attempt in range(max_retries):
        try:
            hist = yf.download(
                [ticker],
                period="6mo",
                interval="1d",
                prepost=False,
                threads=True,
                group_by="ticker"
            )
            print(f"df={hist[ticker]}")
            return hist[ticker]
        except Exception as e:
            print(f"Próba {attempt + 1} nie powiodła się: {e}")
            time.sleep(delay)
    raise Exception(f"AT!!!! Nie udało się pobrać danych po {max_retries} próbach")

def download_with_retry(tickers, max_retries=3, delay=2):
    """
    Pobiera 2 rodzaje danych:
    1. hist_daily - wczorajsze zamknięcie (punkt odniesienia)
    2. hist_realtime - aktualne ceny (świece 5-minutowe)
    """
    for attempt in range(max_retries):
        try:
            # 1. Wczorajsze zamknięcie (punkt odniesienia dla alertów)
            hist_daily = yf.download(
                tickers,
                period="5d",
                interval="1d",
                prepost=False,
                threads=True,
                group_by="ticker"
            )
            
            # 2. Aktualne ceny real-time (świece 5-minutowe)
            hist_realtime = yf.download(
                tickers,
                period="1d",
                interval="5m",
                prepost=False,
                threads=True,
                group_by="ticker"
            )
            
            if hist_daily is None or hist_daily.empty:
                raise Exception("Otrzymano puste dane dzienne z yfinance")
            
            if hist_realtime is None or hist_realtime.empty:
                raise Exception("Otrzymano puste dane real-time z yfinance")
            
            return hist_daily, hist_realtime
            
        except Exception as e:
            print(f"Próba {attempt+1} nie powiodła się: {e}")
            time.sleep(delay)
    
    raise Exception(f"Nie udało się pobrać danych po {max_retries} próbach")


def check_prices_for_exchange(exchange):
    global alerted_types_today  # { ticker: set(alert_type) }
    tickers_for_exchange = [t for t, ex in TICKERS.items() if ex == exchange]
    if not tickers_for_exchange:
        return

    missing_data_tickers = []

    try:
        hist_daily, hist_realtime = download_with_retry(tickers_for_exchange)
    except Exception as e:
        msg = f"❗ Błąd przy pobieraniu danych dla giełdy {exchange}: {e}"
        print(msg)
        send_telegram_message(msg)
        return

    for ticker in tickers_for_exchange:
        try:
            # Obsługa MultiIndex (wiele tickerów) vs single ticker
            if isinstance(hist_daily.columns, pd.MultiIndex):
                df_daily = hist_daily[ticker]
                df_realtime = hist_realtime[ticker]
            else:
                df_daily = hist_daily
                df_realtime = hist_realtime
            
            # Sprawdzenia poprawności danych
            if df_daily is None or df_daily.empty or df_realtime is None or df_realtime.empty:
                missing_data_tickers.append(ticker)
                continue

            # Potrzebujemy minimum 2 świec: ostatnia (dzisiejsza niekompletna) i przedostatnia (wczorajsze zamknięcie)
            if len(df_daily) < 2:
                print(f"⚠️ Za mało danych dziennych dla {ticker}: tylko {len(df_daily)} świec (wymagane: 2)")
                missing_data_tickers.append(ticker)
                continue
            
            if len(df_realtime) < 1:
                print(f"⚠️ Za mało danych real-time dla {ticker}: tylko {len(df_realtime)} świec")
                missing_data_tickers.append(ticker)
                continue

            if ticker not in alerted_types_today:
                alerted_types_today[ticker] = set()

            # === ALERT CENOWY REAL-TIME ===
            # Poprzednie zamknięcie = ostatni pełny dzień (wczoraj)
            # UWAGA: iloc[-1] to dzisiejsza niekompletna sesja, więc używamy iloc[-2] (wczoraj)
            prev_close = float(df_daily['Close'].iloc[-2])

            # Aktualna cena = ostatnia świeca 5-minutowa (teraz)
            current_price = float(df_realtime['Close'].iloc[-1])
            
            # Timestamp ostatniej aktualizacji
            last_update = df_realtime.index[-1]
            
            # Oblicz spadek względem wczorajszego zamknięcia
            spadek = ((prev_close - current_price) / prev_close) * 100

            # Debug info
            print(f"\n[ALERT CHECK] {ticker} @ {last_update.strftime('%H:%M:%S')}:")
            print(f"  Wczorajsze zamknięcie: {prev_close:.2f}")
            print(f"  Aktualna cena (real-time): {current_price:.2f}")
            print(f"  Spadek: {spadek:.2f}%")
            print(f"  Już wysłane alerty: {alerted_types_today.get(ticker, set())}")

            alert_code = alert_color_name(spadek)
            print(f"  Typ alertu: {alert_code if alert_code else 'brak (poniżej progu)'}")

            if alert_code and alert_code not in alerted_types_today[ticker]:
                alerted_types_today[ticker].add(alert_code)
                msg = (
                    f"{alert_code}: !!! <b>{ticker}</b> !!!\n"
                    f"Wczorajsze zamknięcie: {prev_close:.2f}\n"
                    f"Aktualna cena: {current_price:.2f}\n"
                    f"Spadek: {spadek:.2f}%\n"
                    f"Czas: {last_update.strftime('%H:%M:%S')}"
                )
                print(f"[SENDING ALERT] {msg}")
                send_telegram_message(msg)
            else:
                if alert_code:
                    print(f"  → Alert NIE wysłany (już był wysłany: {alert_code})")
                else:
                    print(f"  → Alert NIE wysłany (spadek {spadek:.2f}% poniżej progu)")

            # === ANALIZA TECHNICZNA (jeśli włączona) ===
            if (ticker in MY_TICKERS or ticker in OBSERVABLE_TICKERS) and activeAnalize:
                # Pobierz dane specjalnie do analizy (tutaj lub w osobnej funkcji)
                try:
                    histAT = download_with_retry_onlyAt(ticker)
                    alert_code_m, alert_code_s, msg, _details = getAnalizeMsg(histAT, ticker)

                    sendMessage = (alert_code_s not in alerted_types_today[ticker]
                                   or alert_code_m not in alerted_types_today[ticker])

                    if alert_code_s not in alerted_types_today[ticker]:
                        alerted_types_today[ticker].add(alert_code_s)

                    if alert_code_m not in alerted_types_today[ticker]:
                        alerted_types_today[ticker].add(alert_code_m)

                    if sendMessage:
                        send_telegram_message(msg)
                except Exception as e:
                    print(f"[ERROR] Błąd analizy technicznej dla {ticker}: {e}")

        except Exception as ex:
            print(f"[ERROR] Błąd dla {ticker}: {ex}")
            import traceback
            traceback.print_exc()
            missing_data_tickers.append(ticker)

    if missing_data_tickers:
        send_telegram_message(f"❗ Brak danych dla: {', '.join(missing_data_tickers)}")

def getAnalizeMsg(df, ticker):
    rate, details = getScoreWithDetails(df)
    ma_results = calculate_moving_averages_signals(df)
    movingRate = ma_results['overall_summary']['signal']
    alert_code_m = str(movingRate) + 'm'
    alert_code_s = str(rate) + 's'

    msg = f"Wskaźniki dla: <b>{ticker}</b>:\n"
    msg_s = f"Trend: {RATING_LABELS.get(rate)}\n"
    msg_m = f"Krzywe kroczące: {RATING_LABELS.get(movingRate)}"
    msg = msg + msg_s + msg_m
    return alert_code_m, alert_code_s, msg, details


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
    # scalanie w jeden string
    msg = "\n".join(str(item) for item in details)
    # dodanie code blocka
    msg = "Szczegóły to\n```\n" + msg + "\n```"
    return msg


async def showat_with_memory(update, context):
    print("=== SHOWAT FUNCTION CALLED ===")  # DEBUG
    print(f"Update: {update}")  # DEBUG
    print(f"Context: {context}")  # DEBUG
    print(f"Context args: {context.args}")  # DEBUG
    
    global activeAnalize
    """
    Wersja z przechowywaniem stanu w pamięci
    """
    
    # Sprawdź czy to w ogóle działa
    try:
        await update.message.reply_text("🔧 DEBUG: Funkcja showat została wywołana")
    except Exception as e:
        print(f"ERROR sending debug message: {e}")
        return
    
    if not context.args:
        print("No context args provided")  # DEBUG
        await update.message.reply_text(
            "❗ Użyj: /showat enable lub /showat disable", 
            parse_mode='HTML'
        )
        return
    
    command = context.args[0].lower()
    print(f"Command received: '{command}'")  # DEBUG
    
    if command == "enable":
        activeAnalize = True
        print(f"Setting activeAnalize to True. Current value: {activeAnalize}")
        try:
            await update.message.reply_text(
                "✅ Automatyczne analizy techniczne zostały <b>włączone</b>.",
                parse_mode='HTML'
            )
            print("Enable message sent successfully")
        except Exception as e:
            print(f"ERROR sending enable message: {e}")
        
    elif command == "disable":
        activeAnalize = False
        print(f"Setting activeAnalize to False. Current value: {activeAnalize}")
        try:
            await update.message.reply_text(
                "❌ Automatyczne analizy techniczne zostały <b>wyłączone</b>.",
                parse_mode='HTML'
            )
            print("Disable message sent successfully")
        except Exception as e:
            print(f"ERROR sending disable message: {e}")
        
    else:
        print(f"Invalid command: '{command}'")
        try:
            await update.message.reply_text(
                "❗ Nieprawidłowa opcja. Użyj: /showat enable lub /showat disable",
                parse_mode='HTML'
            )
        except Exception as e:
            print(f"ERROR sending invalid command message: {e}")
    
    print("=== SHOWAT FUNCTION FINISHED ===")

async def error_handler(update, context):
    """Log Errors caused by Updates."""
    print(f"ERROR HANDLER: Update {update} caused error {context.error}")
    import traceback
    traceback.print_exc()

def telegram_loop():
    print("=== STARTING TELEGRAM LOOP ===")
    
    # Sprawdź czy TOKEN istnieje
    try:
        print(f"TOKEN defined: {TOKEN}")
    except NameError:
        print("ERROR: TOKEN not defined!")
        return
    
    try:
        # Zastąpienie Updater na Application.builder()
        application = Application.builder().token(TOKEN).build()
        print("Application created successfully")

        # Register error handler
        application.add_error_handler(error_handler)

        # Dodanie handlera bezpośrednio do obiektu application
        application.add_handler(CommandHandler("at", analyze))
        
        application.add_handler(CommandHandler("showat", showat_with_memory))
        
        # Uruchomienie bota przy użyciu metody run_polling()
        application.run_polling()
        
    except Exception as e:
        print(f"ERROR in telegram_loop: {e}")
        import traceback
        traceback.print_exc()

async def analyze(update, context):
        
    if context.args:
        ticker = context.args[0]
    else:
        await update.message.reply_text("❗ Podaj ticker, np. /at AAPL")
        return

    try:
        df = download_with_retry_onlyAt(ticker)
        # if ticker not in hist:
        #     raise KeyError(f"Ticker {ticker} not found in historical data.")
        # df = hist[ticker]
    except Exception as e:
        msg = f"❗ Błąd przy pobieraniu danych dla {ticker}: {e}"
        print(msg)
        await update.message.reply_text(msg, parse_mode='HTML')
        return

    _alert_code_m, _alert_code_s, msg, details = getAnalizeMsg(df, ticker)
    await update.message.reply_text(msg, parse_mode='HTML')
    if details:
        await update.message.reply_text("\n".join(details), parse_mode="HTML")

# Test czy multiprocessing nie blokuje
if __name__ == "__main__":
    print("=== MAIN STARTING ===")
    print("Starting Telegram bot...!!!")
    
    # Test bez multiprocessing - uruchom bezpośrednio
    # telegram_loop()  # ODKOMENTUJ TO ŻEBY TESTOWAĆ BEZ MULTIPROCESSING
    
    # Lub z multiprocessing:
    try:
        import multiprocessing
        bot_process = multiprocessing.Process(target=telegram_loop)
        bot_process.start()
        print(f"Bot process started with PID: {bot_process.pid}")

        # Sprawdź czy main_loop istnieje
        print("Starting main loop process...")
        main_process = multiprocessing.Process(target=main_loop)
        main_process.start()
        print(f"Main loop process started with PID: {main_process.pid}")

    except KeyboardInterrupt:
        print("Przerwano ręcznie.")
        if 'bot_process' in locals() and bot_process.is_alive():
            bot_process.terminate()
            bot_process.join()