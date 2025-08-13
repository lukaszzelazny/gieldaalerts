#!/usr/bin/env python3
import os
import time
import requests
from datetime import datetime, time as dt_time, date
import pytz
import yfinance as yf
import pandas as pd
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
                    f"{alert_code}: {ticker}\n"
                    f"Cena poprzedniego zamknięcia: {prev_close:.2f}\n"
                    f"Aktualna cena: {current_price:.2f}\n"
                    f"Spadek: {spadek:.2f}%"
                )
                send_telegram_message(msg)

            # === OBLICZENIE WSKAŹNIKÓW TECHNICZNYCH ===
            # Moving Averages
            df['SMA15'] = df['Close'].rolling(window=15).mean()
            df['SMA30'] = df['Close'].rolling(window=30).mean()
            df['SMA50'] = df['Close'].rolling(window=50).mean()
            df['SMA200'] = df['Close'].rolling(window=200).mean()

            # RSI
            delta = df['Close'].diff()
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            avg_gain = gain.rolling(window=14).mean()
            avg_loss = loss.rolling(window=14).mean()
            rs = avg_gain / avg_loss
            df['RSI'] = 100 - (100 / (1 + rs))

            # Volume analysis
            df['Volume_MA20'] = df['Volume'].rolling(window=20).mean()

            # High/Low 20-period
            df['High_20'] = df['High'].rolling(window=20).max()
            df['Low_20'] = df['Low'].rolling(window=20).min()

            # Ostatnie wartości
            sma15_current = df['SMA15'].iloc[-1]
            sma30_current = df['SMA30'].iloc[-1]
            sma50_current = df['SMA50'].iloc[-1]
            sma200_current = df['SMA200'].iloc[-1]
            rsi_current = df['RSI'].iloc[-1]
            volume_current = df['Volume'].iloc[-1]
            volume_avg = df['Volume_MA20'].iloc[-1]
            high_20 = df['High_20'].iloc[-1]

            # Sprawdzenie czy mamy wystarczająco danych
            if pd.isna(sma30_current) or pd.isna(rsi_current):
                continue

            def send_technical_alert(msg, alert_code):
                if alert_code not in alerted_types_today[ticker]:
                    send_telegram_message(msg)
                    alerted_types_today[ticker].add(alert_code)

            # === IMPLEMENTACJA KOMBINACJI SYGNAŁÓW LONG ===

            # COMBO A - "Trend Breakout" (★★★★★)
            trend_signal = sma15_current > sma30_current
            momentum_signal = 45 <= rsi_current <= 65
            breakout_signal = current_price > high_20
            volume_signal = volume_current > 1.5 * volume_avg
            broader_trend = sma15_current > sma50_current if not pd.isna(
                sma50_current) else False

            signals_combo_a = sum([trend_signal, momentum_signal, breakout_signal])
            enhancing_signals_a = sum([volume_signal, broader_trend])

            if signals_combo_a >= 2 and enhancing_signals_a >= 1:
                strength = "🔥🔥🔥" if signals_combo_a == 3 and enhancing_signals_a >= 2 else "🔥🔥"
                send_technical_alert(
                    f"{strength} COMBO A - Trend Breakout: {ticker}\n"
                    f"📊 SMA15({sma15_current:.2f}) > SMA30({sma30_current:.2f}): {'✅' if trend_signal else '❌'}\n"
                    f"📈 RSI({rsi_current:.1f}) 45-65: {'✅' if momentum_signal else '❌'}\n"
                    f"🚀 Breakout 20H({high_20:.2f}): {'✅' if breakout_signal else '❌'}\n"
                    f"📊 Volume >150%: {'✅' if volume_signal else '❌'}\n"
                    f"💰 Cena: {current_price:.2f} | Target: {current_price * 1.06:.2f}-{current_price * 1.09:.2f}",
                    f"combo_a_{ticker}"
                )

            # COMBO B - "RSI Recovery" (★★★★☆)
            rsi_recovery = 35 < rsi_current <= 50 and df['RSI'].iloc[-2] < 40
            rsi_momentum = rsi_current > 40
            volume_confirm = volume_current > volume_avg

            # Sprawdzenie dywergencji (uproszczona)
            price_lower = current_price < df['Close'].iloc[
                -5]  # Cena niższa niż 5 sesji temu
            rsi_higher = rsi_current > df['RSI'].iloc[-5]  # RSI wyższe niż 5 sesji temu
            divergence = price_lower and rsi_higher

            signals_combo_b = sum([trend_signal, rsi_recovery, rsi_momentum])
            enhancing_signals_b = sum([volume_confirm, divergence])

            if signals_combo_b >= 2 and enhancing_signals_b >= 1:
                strength = "🔥🔥🔥" if signals_combo_b == 3 and enhancing_signals_b >= 2 else "🔥🔥"
                send_technical_alert(
                    f"{strength} COMBO B - RSI Recovery: {ticker}\n"
                    f"📊 Trend SMA15>SMA30: {'✅' if trend_signal else '❌'}\n"
                    f"📈 RSI Recovery({rsi_current:.1f}): {'✅' if rsi_recovery else '❌'}\n"
                    f"🔄 RSI >40: {'✅' if rsi_momentum else '❌'}\n"
                    f"📊 Volume powyżej średniej: {'✅' if volume_confirm else '❌'}\n"
                    f"🔄 Pozytywna dywergencja: {'✅' if divergence else '❌'}\n"
                    f"💰 Cena: {current_price:.2f} | Target: {current_price * 1.04:.2f}-{current_price * 1.08:.2f}",
                    f"combo_b_{ticker}"
                )

            # COMBO C - "Pullback Buy" (★★★★☆)
            strong_trend = sma15_current > sma30_current > sma50_current if not pd.isna(
                sma50_current) else trend_signal
            pullback_rsi = 35 <= rsi_current <= 50
            support_test = abs(
                current_price - sma30_current) / sma30_current < 0.02  # W odległości 2% od SMA30

            # Sprawdzenie formacji świecowej (uproszczone)
            open_price = df['Open'].iloc[-1]
            high_price = df['High'].iloc[-1]
            low_price = df['Low'].iloc[-1]
            body_size = abs(current_price - open_price)
            total_range = high_price - low_price
            hammer_like = body_size < total_range * 0.3 and current_price > open_price  # Uproszczona świeca młotkowa

            signals_combo_c = sum([strong_trend, pullback_rsi, support_test])
            enhancing_signals_c = sum([volume_confirm, hammer_like])

            if signals_combo_c >= 2 and enhancing_signals_c >= 1:
                strength = "🔥🔥🔥" if signals_combo_c == 3 and enhancing_signals_c >= 2 else "🔥🔥"
                send_technical_alert(
                    f"{strength} COMBO C - Pullback Buy: {ticker}\n"
                    f"📊 Silny trend SMA15>SMA30>SMA50: {'✅' if strong_trend else '❌'}\n"
                    f"📈 RSI pullback({rsi_current:.1f}): {'✅' if pullback_rsi else '❌'}\n"
                    f"🎯 Test SMA30 support: {'✅' if support_test else '❌'}\n"
                    f"📊 Volume: {'✅' if volume_confirm else '❌'}\n"
                    f"🕯️ Bullish candle: {'✅' if hammer_like else '❌'}\n"
                    f"💰 Cena: {current_price:.2f} | Target: {current_price * 1.03:.2f}-{current_price * 1.06:.2f}",
                    f"combo_c_{ticker}"
                )

            # === ORYGINALNY SYSTEM RSI + TREND (zachowany) ===
            if not pd.isna(sma50_current) and not pd.isna(sma200_current):
                # RSI + Trend (MA50 vs MA200) - ekstremalne sygnały
                if sma50_current > sma200_current and rsi_current < 30:
                    send_technical_alert(
                        f"💎 {ticker}: Trend wzrostowy + RSI < 30 (silny sygnał kupna)",
                        "rsi_buy_strong")
                elif sma50_current > sma200_current and rsi_current < 40:
                    send_technical_alert(
                        f"📈 {ticker}: Trend wzrostowy + RSI < 40 (potencjalna okazja kupna)",
                        "rsi_buy_moderate")

                if sma50_current < sma200_current and rsi_current > 70:
                    send_technical_alert(
                        f"🔥 {ticker}: Trend spadkowy + RSI > 70 (silny sygnał sprzedaży)",
                        "rsi_sell_strong")
                elif sma50_current < sma200_current and rsi_current > 60:
                    send_technical_alert(
                        f"📉 {ticker}: Trend spadkowy + RSI > 60 (potencjalny sygnał sprzedaży)",
                        "rsi_sell_moderate")

            # === SYSTEM 52-WEEK HIGH/LOW (zachowany) ===
            high_52w = df['Close'].max()
            low_52w = df['Close'].min()
            if current_price >= high_52w * 0.999 and volume_current > 1.5 * volume_avg:
                send_technical_alert(f"🚀 {ticker}: Nowe 52-week High z dużym wolumenem",
                                     "volume_high")
            if current_price <= low_52w * 1.001 and volume_current > 1.5 * volume_avg:
                send_technical_alert(f"⚠️ {ticker}: Nowe 52-week Low z dużym wolumenem",
                                     "volume_low")

            # === DODATKOWE ALERTY RYZYKA ===
            # Alert o przegrzaniu w trendzie wzrostowym
            if trend_signal and rsi_current > 75:
                send_technical_alert(
                    f"⚠️ {ticker}: Trend wzrostowy ale RSI > 75 (przegrzanie)",
                    "overheated")

            # Alert o zmianie trendu
            if df['SMA15'].iloc[-2] > df['SMA30'].iloc[
                -2] and sma15_current < sma30_current:
                send_technical_alert(
                    f"🔄 {ticker}: Zmiana trendu - SMA15 przecięła SMA30 w dół",
                    "trend_change_down")

            # Alert o silnym wolumenie bez ruchu ceny
            daily_change = abs(spadek)
            if volume_current > 2 * volume_avg and daily_change < 1:
                send_technical_alert(
                    f"👀 {ticker}: Bardzo wysoki wolumen (+{volume_current / volume_avg:.1f}x) przy małym ruchu ceny",
                    "volume_accumulation")

        except Exception as ex:
            print(f"Błąd dla {ticker}: {ex}")
            missing_data_tickers.append(ticker)

    if missing_data_tickers:
        send_telegram_message(f"❗ Brak danych dla: {', '.join(missing_data_tickers)}")


# === FUNKCJA POMOCNICZA DO ANALIZY PORTFOLIO ===
def analyze_portfolio_signals():
    """
    Dodatkowa funkcja do wysłania podsumowania sygnałów z całego portfolio
    """
    try:
        # Można wywołać raz dziennie jako podsumowanie
        summary_msg = "📊 PODSUMOWANIE SYGNAŁÓW TECHNICZNYCH\n\n"

        # Tutaj można dodać logikę zliczającą ile sygnałów każdego typu wystąpiło
        # i wysłać podsumowanie na koniec dnia

        send_telegram_message(summary_msg)
    except Exception as e:
        print(f"Błąd w analyze_portfolio_signals: {e}")


# === DODATKOWA FUNKCJA DO SPRAWDZENIA KONKRETNYCH WARUNKÓW ===
def check_specific_conditions(df, ticker):
    """
    Funkcja do sprawdzenia bardziej zaawansowanych warunków technicznych
    """
    conditions = {}

    try:
        # VIX-like indicator (można zastąpić prawdziwym VIX jeśli dostępny)
        conditions['market_calm'] = True  # Placeholder

        # Sector strength (można dodać porównanie z sektorowym ETF)
        conditions['sector_strength'] = True  # Placeholder

        # Gap analysis
        prev_close = df['Close'].iloc[-2]
        today_open = df['Open'].iloc[-1]
        gap_percent = ((today_open - prev_close) / prev_close) * 100
        conditions['gap_up'] = gap_percent > 2
        conditions['gap_down'] = gap_percent < -2

        return conditions
    except:
        return {}
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
