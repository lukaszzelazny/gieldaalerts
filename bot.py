import yfinance as yf
import requests
import time

# KONFIGURACJA
TOKEN = "8263884523:AAHesqW2iJclhgbJe9rB_jh8BESPbJMynPE"
CHAT_ID = "7628431599"
TICKERS = ["PKN.OL", "CDR.WA", "PKO.WA"]  # lista spółek do monitorowania
DROP_PERCENT = 3  # próg spadku w %

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    requests.post(url, json=payload)

def check_prices():
    for ticker in TICKERS:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="2d")  # dane z dwóch ostatnich dni
        if len(hist) < 2:
            continue
        
        prev_close = hist['Close'].iloc[-2]
        current_price = hist['Close'].iloc[-1]

        change_percent = ((current_price - prev_close) / prev_close) * 100
        
        if change_percent <= -DROP_PERCENT:
            send_telegram_message(f"📉 {ticker} spadł o {change_percent:.2f}% od wczoraj! Cena: {current_price:.2f} PLN")

if __name__ == "__main__":
    while True:
        check_prices()
        time.sleep(3600)  # sprawdzaj co godzinę
