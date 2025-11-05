#!/usr/bin/env python3
import os
import sys
import math

import io
import csv
import time
import requests
from datetime import datetime, time as dt_time, date, timedelta
import pytz
import yfinance as yf
import pandas as pd
from ticker_analizer import getScoreWithDetails
from moving_analizer import calculate_moving_averages_signals
from concurrent.futures import ThreadPoolExecutor, as_completed

from telegram.ext import Application, CommandHandler
import multiprocessing

from dotenv import load_dotenv
load_dotenv()

# + twoje istniejące importy (yfinance, telegram, etc.)
# ----------------------
# KONFIGURACJA (dostosuj)
# ----------------------


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
previous_close_cache = {}  # { ticker: {"date": date, "price": float} }

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


def get_stooq_single_ticker(ticker):
    """
    Pobiera dane ze Stooq.pl dla pojedynczego tickera.

    Args:
        ticker: ticker z .WA (np. 'SCW.WA')

    Returns:
        tuple: (ticker, data_dict) lub (ticker, None) w przypadku błędu
    """
    # Usuń .WA dla Stooq
    if '.WA' in ticker:
        stooq_ticker = ticker.replace('.WA', '').lower()
    else:
        stooq_ticker = f'{ticker}.US'.lower()
    
    
    url = f"https://stooq.pl/q/l/?s={stooq_ticker}&f=sd2t2ohlcv&h&e=json"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if 'symbols' in data and len(data['symbols']) > 0:
            symbol_data = data['symbols'][0]

            # Sprawdź czy to nie jest błędny ticker (Stooq zwraca symbol taki jaki wysłaliśmy)
            if ',' in symbol_data.get('symbol', ''):
                return ticker, None

            result = {
                'open': symbol_data.get('open'),
                'high': symbol_data.get('high'),
                'low': symbol_data.get('low'),
                'close': symbol_data.get('close'),
                'volume': symbol_data.get('volume'),
                'date': symbol_data.get('date'),
                'time': symbol_data.get('time'),
                'prev_close': None  # Domyślnie None
            }

            # Sprawdź czy mamy rzeczywiste dane (nie None)
            if result['close'] is not None:
                # Pobierz dane z poprzedniego dnia
                current_date = symbol_data.get('date')
                if current_date:
                    try:
                        # Konwertuj datę do formatu YYYYMMDD

                        date_obj = datetime.strptime(current_date, '%Y-%m-%d')
                        prev_date = date_obj - timedelta(days=1)
                        prev_date_str = prev_date.strftime('%Y%m%d')

                        # Pobierz dane historyczne z poprzedniego dnia (format CSV)
                        prev_url = f"https://stooq.pl/q/d/l/?s={stooq_ticker}&f={prev_date_str}&t={prev_date_str}&i=d"
                        prev_response = requests.get(prev_url, timeout=10)
                        prev_response.raise_for_status()

                        # Parsuj CSV
                        csv_content = prev_response.text
                        csv_reader = csv.DictReader(io.StringIO(csv_content))

                        for row in csv_reader:
                            # Pobierz wartość zamknięcia (kolumna "Zamkniecie")
                            if 'Zamkniecie' in row:
                                try:
                                    result['prev_close'] = float(row['Zamkniecie'])
                                except (ValueError, TypeError):
                                    pass
                            break  # Interesuje nas tylko pierwszy wiersz

                    except Exception as e:
                        print(f"⚠️ Nie udało się pobrać prev_close dla {ticker}: {e}")

                return ticker, result

        return ticker, None

    except Exception as e:
        print(f"⚠️ Błąd pobierania {ticker} ze Stooq: {e}")
        return ticker, None

def get_stooq_data(tickers, max_workers=5):
    """
    Pobiera dane ze Stooq.pl dla wielu tickerów (równolegle).
    
    Args:
        tickers: lista tickerów (z .WA)
        max_workers: maksymalna liczba równoległych requestów
    
    Returns:
        dict: {ticker: {'open': x, 'high': x, 'low': x, 'close': x, 'volume': x, 'date': x, 'time': x}}
    """
    result = {}
    
    # Pobieranie równoległe dla przyspieszenia
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Uruchom wszystkie requesty
        future_to_ticker = {
            executor.submit(get_stooq_single_ticker, ticker): ticker 
            for ticker in tickers
        }
        
        # Zbierz wyniki
        for future in as_completed(future_to_ticker):
            ticker, data = future.result()
            if data is not None:
                result[ticker] = data
                print(f"  ✅ {ticker}: {data['close']} PLN @ {data['time']}")
            else:
                print(f"  ❌ {ticker}: brak danych")
    
    return result

def get_previous_close(ticker_symbol):
    """
    Pobiera oficjalną cenę zamknięcia z poprzedniej sesji.
    Cache'uje wynik na dany dzień, żeby nie odpytywać wielokrotnie.
    """
    today = date.today()
    
    # Sprawdź cache
    if ticker_symbol in previous_close_cache:
        cached = previous_close_cache[ticker_symbol]
        if cached["date"] == today:
            print(f"  [CACHE HIT] {ticker_symbol} previousClose: {cached['price']:.2f}")
            return cached["price"]
    
    # Pobierz z API
    try:
        ticker = yf.Ticker(ticker_symbol)
        prev_close = ticker.info.get('previousClose')
        
        if prev_close:
            price = float(prev_close)
            # Zapisz w cache
            previous_close_cache[ticker_symbol] = {
                "date": today,
                "price": price
            }
            print(f"  [API] {ticker_symbol} previousClose: {price:.2f}")
            return price
        else:
            print(f"  [API] {ticker_symbol} - brak previousClose w info")
            
    except Exception as e:
        print(f"  [ERROR] Błąd pobierania previousClose dla {ticker_symbol}: {e}")
    
    # Fallback - zwróć None, użyjemy danych historycznych
    return None

def download_with_retry(tickers, max_retries=3, delay=2):
    """
    Pobiera 2 rodzaje danych:
    1. hist_daily - wczorajsze zamknięcie (punkt odniesienia)
    2. hist_realtime - aktualne ceny (świece 5-minutowe)
    
    Dla tickerów bez danych w Yahoo Finance próbuje pobrać ze Stooq.
    """
    failed_tickers_daily = []
    failed_tickers_realtime = []
    
    for attempt in range(max_retries):
        try:
            # 1. Wczorajsze zamknięcie (punkt odniesienia dla alertów)
            hist_daily = yf.download(
                tickers,
                period="5d",
                interval="1d",
                prepost=False,
                threads=True,
                group_by="ticker",
                auto_adjust=True,
                progress=False  # Wyłącz progress bar dla czystszych logów
            )
            
            # 2. Aktualne ceny real-time (świece 5-minutowe)
            hist_realtime = yf.download(
                tickers,
                period="1d",
                interval="5m",
                prepost=False,
                threads=True,
                group_by="ticker",
                auto_adjust=True,
                progress=False
            )
            
            if hist_daily is None or hist_daily.empty:
                raise Exception("Otrzymano puste dane dzienne z yfinance")



            # Sprawdź które tickery nie mają danych
            if isinstance(tickers, list) and len(tickers) > 1:
                # Multi-ticker: sprawdź kolumny i sprawdź czy mają rzeczywiste dane (nie tylko NaN-y)
                available_daily = set()
                available_realtime = set()

                if hasattr(hist_daily.columns, 'levels'):
                    # Sprawdź każdy ticker czy ma dane (nie tylko kolumnę)
                    for ticker in tickers:
                        if ticker in hist_daily.columns.get_level_values(0):
                            # Sprawdź czy kolumna 'Close' ma jakieś nie-NaN wartości
                            ticker_data = hist_daily[ticker]
                            df_realtime = hist_realtime[ticker]
                            current_price = float(df_realtime['Close'].iloc[-1])
                            has_yahoo_data = not math.isnan(current_price)

                            if (has_yahoo_data and 'Close' in ticker_data.columns
                                    and not ticker_data['Close'].isna().all()):
                                available_daily.add(ticker)
                elif not hist_daily.empty:
                    # Single ticker - sprawdź czy ma dane
                    if 'Close' in hist_daily.columns and not hist_daily['Close'].isna().all():
                        available_daily = set(tickers)

                if hasattr(hist_realtime.columns, 'levels'):
                    # Sprawdź każdy ticker czy ma dane (nie tylko kolumnę)
                    for ticker in tickers:
                        if ticker in hist_realtime.columns.get_level_values(0):
                            # Sprawdź czy kolumna 'Close' ma jakieś nie-NaN wartości
                            ticker_data = hist_realtime[ticker]
                            if 'Close' in ticker_data.columns and not ticker_data['Close'].isna().all():
                                available_realtime.add(ticker)
                elif not hist_realtime.empty:
                    # Single ticker - sprawdź czy ma dane
                    if 'Close' in hist_realtime.columns and not hist_realtime['Close'].isna().all():
                        available_realtime = set(tickers)

                failed_tickers_daily = [t for t in tickers if t not in available_daily]
                failed_tickers_realtime = [t for t in tickers if t not in available_realtime]
                
                print(f"📊 Yahoo Finance: {len(available_daily)}/{len(tickers)} daily, {len(available_realtime)}/{len(tickers)} realtime")
                
                # Próba pobrania brakujących danych ze Stooq
                if failed_tickers_daily or failed_tickers_realtime:
                    print(f"🔄 Próba pobrania brakujących danych ze Stooq dla {len(set(failed_tickers_daily + failed_tickers_realtime))} tickerów...")
                    
                    # Pobierz dane ze Stooq dla wszystkich brakujących tickerów
                    all_failed = list(set(failed_tickers_daily + failed_tickers_realtime))
                    stooq_data = get_stooq_data(all_failed)
                    
                    if stooq_data:
                        print(f"✅ Stooq dostarczył dane dla {len(stooq_data)}/{len(all_failed)} tickerów")
                        return hist_daily, hist_realtime, stooq_data
                    else:
                        print(f"⚠️ Stooq nie dostarczył żadnych danych")
            
            # Sprawdź czy mamy JAKIEKOLWIEK dane realtime
            if hist_realtime is None or hist_realtime.empty:
                current_time = datetime.now().time()
                market_open = datetime.strptime("09:00", "%H:%M").time()
                market_early = datetime.strptime("09:10", "%H:%M").time()
                
                # Jeśli jest tuż po otwarciu, to normalne że brak danych
                if market_open <= current_time <= market_early:
                    print(f"⏰ Początek sesji - brak danych 5-min jest normalny (próba {attempt+1}/{max_retries})")
                    if attempt < max_retries - 1:
                        print(f"⏳ Czekam {delay * 2}s na pojawienie się pierwszych świec...")
                        time.sleep(delay * 2)
                        continue
                    else:
                        # Na ostatniej próbie zwróć dane dzienne + pusty realtime + Stooq
                        print("⚠️ Używam tylko danych dziennych (brak świec 5-min)")
                        print("🔄 Próba pobrania danych ze Stooq...")
                        stooq_data = get_stooq_data(tickers)
                        return hist_daily, hist_realtime, stooq_data
                else:
                    raise Exception("Otrzymano puste dane real-time z yfinance")
            
            # Jeśli wszystko OK, zwróć dane (+ puste stooq_data jeśli nie było failów)
            return hist_daily, hist_realtime, {}
            
        except Exception as e:
            print(f"❌ Próba {attempt+1}/{max_retries} nie powiodła się: {e}")
            if attempt < max_retries - 1:
                print(f"⏳ Czekam {delay}s przed kolejną próbą...")
                time.sleep(delay)
    
    # Ostatnia deska ratunku - spróbuj tylko Stooq
    print("🆘 Ostatnia próba: pobieranie WSZYSTKICH danych ze Stooq...")
    stooq_data = get_stooq_data(tickers)
    
    if stooq_data:
        print(f"✅ Stooq dostarczył dane awaryjne dla {len(stooq_data)} tickerów")
        # Zwróć puste DataFrames + dane ze Stooq
        import pandas as pd
        return pd.DataFrame(), pd.DataFrame(), stooq_data
    
    raise Exception(f"Nie udało się pobrać danych po {max_retries} próbach (Yahoo i Stooq)")


def check_prices_for_exchange(exchange):
    global alerted_types_today  # { ticker: set(alert_type) }
    tickers_for_exchange = [t for t, ex in TICKERS.items() if ex == exchange]
    if not tickers_for_exchange:
        return

    missing_data_tickers = []

    try:
        hist_daily, hist_realtime, stooq_data = download_with_retry(tickers_for_exchange)
    except Exception as e:
        msg = f"❗ Błąd przy pobieraniu danych dla giełdy {exchange}: {e}"
        print(msg)
        print(f"stooq_data: {stooq_data}")
        send_telegram_message(msg)
        return

    print(f"stooq_data: {stooq_data}")

    for ticker in tickers_for_exchange:
        try:
            # === SPRAWDŹ CZY TICKER MA DANE W YAHOO FINANCE ===
            has_yahoo_data = False
            df_realtime = hist_realtime[ticker]
            if isinstance(hist_daily.columns, pd.MultiIndex):
                current_price = float(df_realtime['Close'].iloc[-1])
                has_yahoo_data = not math.isnan(current_price)
            elif not hist_daily.empty:
                has_yahoo_data = True
            
            # === NORMALNA OBSŁUGA YAHOO FINANCE ===
            # Obsługa MultiIndex (wiele tickerów) vs single ticker
            if isinstance(hist_daily.columns, pd.MultiIndex):
                df_daily = hist_daily[ticker]
                df_realtime = hist_realtime[ticker]
            else:
                df_daily = hist_daily
                df_realtime = hist_realtime
            

            if df_realtime is None or df_realtime.empty or not has_yahoo_data:
                if ticker in stooq_data:
                    print(f"📊 {ticker}: Yahoo brak real-time, używam Stooq")
                    stooq_ticker_data = stooq_data[ticker]
                    current_price = stooq_ticker_data['close']
                    prev_close = stooq_ticker_data['prev_close']
                    
                    # Pobierz previous close z Yahoo daily (jeśli mamy)
                    if prev_close:
                        spadek = ((prev_close - current_price) / prev_close) * 100
                        
                        last_update_str = f"{stooq_ticker_data['date']} {stooq_ticker_data['time']}"
                        
                        print(f"\n[ALERT CHECK - STOOQ] {ticker} @ {last_update_str}:")
                        print(f"  Wczorajsze zamknięcie: {prev_close:.2f}")
                        print(f"  Aktualna cena (Stooq): {current_price:.2f}")
                        print(f"  Spadek: {spadek:.2f}%")
                        
                        if ticker not in alerted_types_today:
                            alerted_types_today[ticker] = set()
                        
                        alert_code = alert_color_name(spadek)
                        
                        if alert_code and alert_code not in alerted_types_today[ticker]:
                            alerted_types_today[ticker].add(alert_code)
                            msg = (
                                f"{alert_code}: !!! <b>{ticker}</b> !!! [Stooq]\n"
                                f"Wczorajsze zamknięcie: {prev_close:.2f}\n"
                                f"Aktualna cena: {current_price:.2f}\n"
                                f"Spadek: {spadek:.2f}%\n"
                                f"Czas: {last_update_str}"
                            )
                            print(f"[SENDING ALERT - STOOQ] {msg}")
                            send_telegram_message(msg)
                    else:
                        print(f"  ⚠️ Wczorajsze zamknięcie jest NaN, brak danych w Stooq - pomijam {ticker}")

                    continue
                else:
                    missing_data_tickers.append(ticker)
                    continue

            # Potrzebujemy minimum 2 świec: ostatnia (dzisiejsza niekompletna) i przedostatnia (wczorajsze zamknięcie)
            if len(df_daily) < 2:
                print(f"⚠️ Za mało danych dziennych dla {ticker}: tylko {len(df_daily)} świec (wymagane: 2)")
                
                # Sprawdź fallback Stooq
                if ticker in stooq_data:
                    print(f"  → Próba użycia Stooq jako zamiennika")
                    continue
                else:
                    missing_data_tickers.append(ticker)
                    continue
            
            if len(df_realtime) < 1:
                print(f"⚠️ Za mało danych real-time dla {ticker}: tylko {len(df_realtime)} świec")
                
                # Sprawdź fallback Stooq
                if ticker in stooq_data:
                    print(f"  → Próba użycia Stooq jako zamiennika")
                    # Użyj logiki Stooq z powyższego bloku
                    continue
                else:
                    missing_data_tickers.append(ticker)
                    continue

            if ticker not in alerted_types_today:
                alerted_types_today[ticker] = set()

            # === ALERT CENOWY REAL-TIME (YAHOO) ===
            # Poprzednie zamknięcie = ostatni pełny dzień (wczoraj)
            prev_close = get_previous_close(ticker) #float(df_daily['Close'].iloc[-1])
            
            # **KLUCZOWE: Sprawdź czy prev_close nie jest NaN**
            if pd.isna(prev_close):
                print(f"\n⚠️ {ticker}: Wczorajsze zamknięcie jest NaN")
                
                # Sprawdź czy Stooq ma dane
                if ticker in stooq_data:
                    print(f"  → Używam Stooq jako zamiennika")
                    stooq_ticker_data = stooq_data[ticker]
                    current_price = stooq_ticker_data['close']
                    last_update_str = f"{stooq_ticker_data['date']} {stooq_ticker_data['time']}"
                    
                    print(f"  Aktualna cena (Stooq): {current_price:.2f}")
                    print(f"  Czas: {last_update_str}")
                    print(f"  ⚠️ Brak wczorajszego zamknięcia - pomijam alerty spadków")
                    continue
                else:
                    missing_data_tickers.append(ticker)
                    continue

            # Aktualna cena = ostatnia świeca 5-minutowa (teraz)
            current_price = float(df_realtime['Close'].iloc[-1])
            
            # **KLUCZOWE: Sprawdź czy current_price nie jest NaN**
            if pd.isna(current_price):
                print(f"\n⚠️ {ticker}: Aktualna cena jest NaN w Yahoo")
                print(f"\n⚠️  stooq_data: {stooq_data}")
                tickerSq = ticker.replace('.WA', '')
                # Sprawdź czy Stooq ma dane
                if tickerSq in stooq_data:
                    print(f"  → Używam Stooq jako zamiennika")
                    stooq_ticker_data = stooq_data[tickerSq]
                    current_price = stooq_ticker_data['close']
                    spadek = ((prev_close - current_price) / prev_close) * 100
                    
                    last_update_str = f"{stooq_ticker_data['date']} {stooq_ticker_data['time']}"
                    
                    print(f"\n[ALERT CHECK - HYBRID] {ticker} @ {last_update_str}:")
                    print(f"  Wczorajsze zamknięcie (Yahoo): {prev_close:.2f}")
                    print(f"  Aktualna cena (Stooq): {current_price:.2f}")
                    print(f"  Spadek: {spadek:.2f}%")
                    
                    alert_code = alert_color_name(spadek)
                    
                    if alert_code and alert_code not in alerted_types_today[ticker]:
                        alerted_types_today[ticker].add(alert_code)
                        msg = (
                            f"{alert_code}: !!! <b>{ticker}</b> !!! [Yahoo+Stooq]\n"
                            f"Wczorajsze zamknięcie: {prev_close:.2f}\n"
                            f"Aktualna cena: {current_price:.2f}\n"
                            f"Spadek: {spadek:.2f}%\n"
                            f"Czas: {last_update_str}"
                        )
                        print(f"[SENDING ALERT - HYBRID] {msg}")
                        send_telegram_message(msg)
                    
                    continue
                else:
                    missing_data_tickers.append(ticker)
                    continue
            
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

#windows
# if __name__ == "__main__":
#     main_loop()