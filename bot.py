import yfinance as yf
import requests
import time

# KONFIGURACJA
TOKEN = "8263884523:AAHesqW2iJclhgbJe9rB_jh8BESPbJMynPE"
CHAT_ID = "7628431599"
TICKERS = ["PKN.WA", "CDR.WA", "PKO.WA", "ZLYTICKER"]  # przykładowo dodałem błędny ticker
SLEEP_TIME = 3600  # czas w sekundach między sprawdzeniami (np. 3600 = 1 godzina)

DROP_THRESHOLDS = {
    "czerwony": 7,
    "zolty": 5,
    "zielony": 3
}

# Zbiór tickerów z błędem, dla których już wysłaliśmy alert
tickery_z_bledem = set()

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        response = requests.post(url, json=payload)
        if not response.ok:
            print(f"❗ Błąd wysyłania wiadomości: {response.text}")
    except Exception as e:
        print(f"❗ Wyjątek przy wysyłaniu wiadomości: {e}")

def alert_color(procent_spadku):
    if procent_spadku >= DROP_THRESHOLDS["czerwony"]:
        return "🔴 CZERWONY ALERT"
    elif DROP_THRESHOLDS["zolty"] <= procent_spadku < DROP_THRESHOLDS["czerwony"]:
        return "🟡 ŻÓŁTY ALERT"
    elif DROP_THRESHOLDS["zielony"] <= procent_spadku < DROP_THRESHOLDS["zolty"]:
        return "🟢 ZIELONY ALERT"
    else:
        return None

def check_prices():
    global tickery_z_bledem

    for ticker in TICKERS:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="2d")
            if len(hist) < 2:
                if ticker not in tickery_z_bledem:
                    msg = f"❗ Za mało danych dla {ticker} - możliwy błędny ticker."
                    print(msg)
                    send_telegram_message(msg)
                    tickery_z_bledem.add(ticker)
                continue

            # Jeśli wcześniej był błąd, a teraz jest OK — usuwamy ticker z błędem
            if ticker in tickery_z_bledem:
                tickery_z_bledem.remove(ticker)

            prev_close = hist['Close'].iloc[-2]
            current_price = hist['Close'].iloc[-1]

            spadek = ((prev_close - current_price) / prev_close) * 100

            alert = alert_color(spadek)
            if alert:
                message = (
                    f"{alert}: {ticker}\n"
                    f"Cena poprzedniego zamknięcia: {prev_close:.2f} PLN\n"
                    f"Aktualna cena: {current_price:.2f} PLN\n"
                    f"Spadek: {spadek:.2f}%"
                )
                send_telegram_message(message)

        except Exception as e:
            if ticker not in tickery_z_bledem:
                error_msg = f"❗ Błąd przy pobieraniu danych dla {ticker}: {e}"
                print(error_msg)
                send_telegram_message(error_msg)
                tickery_z_bledem.add(ticker)

def send_startup_message():
    send_telegram_message("🚀 Bot giełdowy właśnie wystartował i działa poprawnie!")

if __name__ == "__main__":
    send_startup_message()
    while True:
        check_prices()
        time.sleep(SLEEP_TIME)
