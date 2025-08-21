import pandas as pd
import numpy as np
import yfinance as yf
import time


def download_with_retry(tickers, period="1y", max_retries=3, delay=2):
    for attempt in range(max_retries):
        try:
            hist = yf.download(tickers, period=period, group_by="ticker", threads=True)
            return hist
        except Exception as e:
            print(f"Próba {attempt + 1} nie powiodła się: {e}")
            time.sleep(delay)
    raise Exception(f"Nie udało się pobrać danych po {max_retries} próbach")


def calculate_rsi(df, period=14):
    """RSI - Relative Strength Index"""
    close = df['Close']
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    # Ocena
    latest_rsi = rsi.iloc[-1]
    if latest_rsi > 70:
        signal = "sprzedaj"
    elif latest_rsi < 30:
        signal = "kupuj"
    else:
        signal = "neutralny"

    return rsi, signal, latest_rsi


def calculate_stochastic(df, k_period=14, d_period=3):
    """Stochastic Oscillator"""
    high = df['High']
    low = df['Low']
    close = df['Close']

    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()

    k_percent = 100 * ((close - lowest_low) / (highest_high - lowest_low))
    d_percent = k_percent.rolling(window=d_period).mean()

    # Ocena
    latest_k = k_percent.iloc[-1]
    latest_d = d_percent.iloc[-1]

    if latest_k > 80 and latest_d > 80:
        signal = "sprzedaj"
    elif latest_k < 20 and latest_d < 20:
        signal = "kupuj"
    else:
        signal = "neutralny"

    return k_percent, d_percent, signal, latest_k, latest_d


def calculate_macd(df, fast=12, slow=26, signal_period=9):
    """MACD - Moving Average Convergence Divergence"""
    close = df['Close']
    ema_fast = close.ewm(span=fast).mean()
    ema_slow = close.ewm(span=slow).mean()

    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period).mean()
    histogram = macd_line - signal_line

    # Ocena
    latest_macd = macd_line.iloc[-1]
    latest_signal = signal_line.iloc[-1]
    latest_hist = histogram.iloc[-1]
    prev_hist = histogram.iloc[-2]

    if latest_macd > latest_signal and latest_hist > prev_hist:
        signal = "kupuj"
    elif latest_macd < latest_signal and latest_hist < prev_hist:
        signal = "sprzedaj"
    else:
        signal = "neutralny"

    return macd_line, signal_line, histogram, signal, latest_macd


def calculate_trix(df, period=14, signal_period=9):
    """TRIX - Triple Exponential Average"""
    close = df['Close']
    ema1 = close.ewm(span=period).mean()
    ema2 = ema1.ewm(span=period).mean()
    ema3 = ema2.ewm(span=period).mean()

    trix = ema3.pct_change() * 10000
    trix_signal = trix.ewm(span=signal_period).mean()

    # Ocena
    latest_trix = trix.iloc[-1]
    latest_signal = trix_signal.iloc[-1]

    if latest_trix > latest_signal and latest_trix > 0:
        signal = "kupuj"
    elif latest_trix < latest_signal and latest_trix < 0:
        signal = "sprzedaj"
    else:
        signal = "neutralny"

    return trix, trix_signal, signal, latest_trix


def calculate_williams_r(df, period=10):
    """Williams %R"""
    high = df['High']
    low = df['Low']
    close = df['Close']

    highest_high = high.rolling(window=period).max()
    lowest_low = low.rolling(window=period).min()

    williams_r = -100 * ((highest_high - close) / (highest_high - lowest_low))

    # Ocena
    latest_wr = williams_r.iloc[-1]
    if latest_wr > -20:
        signal = "sprzedaj"
    elif latest_wr < -80:
        signal = "kupuj"
    else:
        signal = "neutralny"

    return williams_r, signal, latest_wr


def calculate_cci(df, period=14):
    """Commodity Channel Index"""
    high = df['High']
    low = df['Low']
    close = df['Close']

    typical_price = (high + low + close) / 3
    sma = typical_price.rolling(window=period).mean()
    mad = typical_price.rolling(window=period).apply(lambda x: np.mean(np.abs(x - x.mean())))

    cci = (typical_price - sma) / (0.015 * mad)

    # Ocena
    latest_cci = cci.iloc[-1]
    if latest_cci > 100:
        signal = "sprzedaj"
    elif latest_cci < -100:
        signal = "kupuj"
    else:
        signal = "neutralny"

    return cci, signal, latest_cci


def calculate_roc(df, period=15):
    """Rate of Change"""
    close = df['Close']
    roc = ((close - close.shift(period)) / close.shift(period)) * 100

    # Ocena
    latest_roc = roc.iloc[-1]
    if latest_roc > 5:
        signal = "kupuj"
    elif latest_roc < -5:
        signal = "sprzedaj"
    else:
        signal = "neutralny"

    return roc, signal, latest_roc


def calculate_ultimate_oscillator(df, period1=7, period2=14, period3=28):
    """Ultimate Oscillator"""
    high = df['High']
    low = df['Low']
    close = df['Close']

    true_low = np.minimum(low, close.shift(1))
    buying_pressure = close - true_low
    true_range = np.maximum(high, close.shift(1)) - true_low

    bp_sum1 = buying_pressure.rolling(window=period1).sum()
    tr_sum1 = true_range.rolling(window=period1).sum()

    bp_sum2 = buying_pressure.rolling(window=period2).sum()
    tr_sum2 = true_range.rolling(window=period2).sum()

    bp_sum3 = buying_pressure.rolling(window=period3).sum()
    tr_sum3 = true_range.rolling(window=period3).sum()

    ult_osc = 100 * ((4 * (bp_sum1 / tr_sum1)) + (2 * (bp_sum2 / tr_sum2)) + (bp_sum3 / tr_sum3)) / 7

    # Ocena
    latest_ult = ult_osc.iloc[-1]
    if latest_ult > 70:
        signal = "sprzedaj"
    elif latest_ult < 30:
        signal = "kupuj"
    else:
        signal = "neutralny"

    return ult_osc, signal, latest_ult


def calculate_force_index(df, period=13):
    """Force Index"""
    close = df['Close']
    volume = df['Volume']

    force_index = (close - close.shift(1)) * volume
    fi_ema = force_index.ewm(span=period).mean()

    # Ocena
    latest_fi = fi_ema.iloc[-1]
    if latest_fi > 0:
        signal = "kupuj"
    elif latest_fi < 0:
        signal = "sprzedaj"
    else:
        signal = "neutralny"

    return fi_ema, signal, latest_fi


def calculate_mfi(df, period=14):
    """Money Flow Index"""
    high = df['High']
    low = df['Low']
    close = df['Close']
    volume = df['Volume']

    typical_price = (high + low + close) / 3
    money_flow = typical_price * volume

    positive_mf = money_flow.where(typical_price > typical_price.shift(1), 0)
    negative_mf = money_flow.where(typical_price < typical_price.shift(1), 0)

    positive_mf_sum = positive_mf.rolling(window=period).sum()
    negative_mf_sum = negative_mf.rolling(window=period).sum()

    money_ratio = positive_mf_sum / negative_mf_sum
    mfi = 100 - (100 / (1 + money_ratio))

    # Ocena
    latest_mfi = mfi.iloc[-1]
    if latest_mfi > 80:
        signal = "sprzedaj"
    elif latest_mfi < 20:
        signal = "kupuj"
    else:
        signal = "neutralny"

    return mfi, signal, latest_mfi


def calculate_bop(df, period=14):
    """Balance of Power"""
    open_price = df['Open']
    high = df['High']
    low = df['Low']
    close = df['Close']

    bop = (close - open_price) / (high - low)
    bop_sma = bop.rolling(window=period).mean()

    # Ocena
    latest_bop = bop_sma.iloc[-1]
    if latest_bop > 0.1:
        signal = "kupuj"
    elif latest_bop < -0.1:
        signal = "sprzedaj"
    else:
        signal = "neutralny"

    return bop_sma, signal, latest_bop


def calculate_emv(df, period=14):
    """Ease of Movement"""
    high = df['High']
    low = df['Low']
    volume = df['Volume']

    distance_moved = ((high + low) / 2) - ((high.shift(1) + low.shift(1)) / 2)
    box_height = (volume / 1000000) / (high - low)  # Skalowanie wolumenu

    emv = distance_moved / box_height
    emv_sma = emv.rolling(window=period).mean()

    # Ocena
    latest_emv = emv_sma.iloc[-1]
    if latest_emv > 1:
        signal = "kupuj"
    elif latest_emv < -1:
        signal = "sprzedaj"
    else:
        signal = "neutralny"

    return emv_sma, signal, latest_emv


def analyze_stock_df(df):
    """Główna funkcja analizująca wszystkie wskaźniki dla podanego DataFrame"""
    try:
        trends = {}
        osc = {}
        result_type = {'trends': trends,'osc': osc}
        # results = {}

        # Oblicz wszystkie wskaźniki
        rsi, rsi_signal, rsi_val = calculate_rsi(df)
        osc['RSI(14)'] = {'signal': rsi_signal, 'value': round(rsi_val, 2)}

        k, d, sts_signal, k_val, d_val = calculate_stochastic(df)
        osc['STS(14,3)'] = {'signal': sts_signal, 'value': f'K:{round(k_val, 2)}, D:{round(d_val, 2)}'}

        macd, signal_line, hist, macd_signal, macd_val = calculate_macd(df)
        trends['MACD(12,26,9)'] = {'signal': macd_signal, 'value': round(macd_val, 4)}

        trix, trix_sig, trix_signal, trix_val = calculate_trix(df)
        trends['TRIX(14,9)'] = {'signal': trix_signal, 'value': round(trix_val, 4)}

        wr, wr_signal, wr_val = calculate_williams_r(df)
        osc['Williams %R(10)'] = {'signal': wr_signal, 'value': round(wr_val, 2)}

        cci, cci_signal, cci_val = calculate_cci(df)
        osc['CCI(14)'] = {'signal': cci_signal, 'value': round(cci_val, 2)}

        roc, roc_signal, roc_val = calculate_roc(df)
        trends['ROC(15)'] = {'signal': roc_signal, 'value': round(roc_val, 2)}

        ult, ult_signal, ult_val = calculate_ultimate_oscillator(df)
        trends['ULT(7,14,28)'] = {'signal': ult_signal, 'value': round(ult_val, 2)}

        fi, fi_signal, fi_val = calculate_force_index(df)
        trends['FI(13)'] = {'signal': fi_signal, 'value': round(fi_val, 2)}

        mfi, mfi_signal, mfi_val = calculate_mfi(df)
        osc['MFI(14)'] = {'signal': mfi_signal, 'value': round(mfi_val, 2)}

        bop, bop_signal, bop_val = calculate_bop(df)
        trends['BOP(14)'] = {'signal': bop_signal, 'value': round(bop_val, 4)}

        emv, emv_signal, emv_val = calculate_emv(df)
        trends['EMV(14)'] = {'signal': emv_signal, 'value': round(emv_val, 4)}

        return result_type

    except Exception as e:
        print(f"Błąd podczas analizy: {e}")
        return None


def analyze_stock(ticker, period="1y"):
    """Funkcja analizująca wskaźniki dla danego tickera (dla kompatybilności wstecznej)"""
    try:
        data = download_with_retry(ticker, period=period)

        if isinstance(data.columns, pd.MultiIndex):
            df = data[ticker]
        else:
            df = data

        return analyze_stock_df(df)

    except Exception as e:
        print(f"Błąd podczas analizy {ticker}: {e}")
        return None


def addcount(signal):
    if signal == "kupuj":
        return 1
    elif signal == "sprzedaj":
        return -1
    else:
        return 0


def getScoreWithDetails(df):
    results_all = analyze_stock_df(df)
    oscCount = []
    trendCount = []
    details = []
    for indtype, results in results_all.items():
        for indicator, data in results.items():
            signal = data['signal']
            value = data['value']
            printer = f"{indicator:15} | {signal:10} | {value}"
            details.append(printer)

            if indtype == 'osc':
                oscCount.append(addcount(signal))
            else:
                trendCount.append(addcount(signal))

    trendsRate = sum(trendCount) / len(trendCount)
    oscCountRate = sum(oscCount) / len(oscCount)
    score = 0.7 * trendsRate + 0.3 * oscCountRate

    if score >= 1.5:
        rate = 2 #"Mocne kupuj"
    elif score >= 0.5:
        rate = 1 #"Kupuj"
    elif score > -0.5:
        rate = 0 #"Trzymaj"
    elif score > -1.5:
        rate = -1 #"Sprzedaj"
    else:
        rate = -2 #"Mocne sprzedaj"
    return rate, details


