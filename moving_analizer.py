

def calculate_moving_averages_signals(df, periods=[5, 15, 30, 60]):
    """
    Oblicza sygnały SMA i EMA dla różnych okresów i zwraca sumaryczną ocenę

    Args:
        df: DataFrame z danymi OHLCV
        periods: lista okresów do obliczenia (domyślnie [5, 15, 30, 60])

    Returns:
        dict: zawiera szczegółowe wyniki i sumaryczną ocenę
    """
    close = df['Close']
    current_price = close.iloc[-1]

    sma_signals = []
    ema_signals = []

    results = {
        'current_price': round(current_price, 2),
        'sma_details': {},
        'ema_details': {},
        'sma_summary': {},
        'ema_summary': {},
        'overall_summary': {}
    }

    # Oblicz SMA dla każdego okresu
    for period in periods:
        sma = close.rolling(window=period).mean()
        sma_value = sma.iloc[-1]

        # Sygnał: cena vs SMA
        if current_price > sma_value:
            signal = 1  # kupuj
        elif current_price < sma_value:
            signal = -1  # sprzedaj
        else:
            signal = 0  # neutralnie

        sma_signals.append(signal)
        results['sma_details'][f'SMA{period}'] = {
            'value': round(sma_value, 2),
            'signal': 'kupuj' if signal == 1 else 'sprzedaj' if signal == -1 else 'neutralnie',
            'difference': round(((current_price - sma_value) / sma_value) * 100, 2)
        }

    # Oblicz EMA dla każdego okresu
    for period in periods:
        ema = close.ewm(span=period).mean()
        ema_value = ema.iloc[-1]

        # Sygnał: cena vs EMA
        if current_price > ema_value:
            signal = 1  # kupuj
        elif current_price < ema_value:
            signal = -1  # sprzedaj
        else:
            signal = 0  # neutralnie

        ema_signals.append(signal)
        results['ema_details'][f'EMA{period}'] = {
            'value': round(ema_value, 2),
            'signal': 'kupuj' if signal == 1 else 'sprzedaj' if signal == -1 else 'neutralnie',
            'difference': round(((current_price - ema_value) / ema_value) * 100, 2)
        }

    # Sumaryczne oceny
    sma_sum = sum(sma_signals)
    ema_sum = sum(ema_signals)
    total_sum = sma_sum + ema_sum

    # Funkcja do konwersji sumy na ocenę tekstową
    def sum_to_signal(signal_sum, max_signals):
        if signal_sum >= max_signals * 0.75:
            return 2 #"Mocne kupuj"
        elif signal_sum >= max_signals * 0.25:
            return 1 #"Kupuj"
        elif signal_sum <= -max_signals * 0.75:
            return -2 #"Mocne sprzedaj"
        elif signal_sum <= -max_signals * 0.25:
            return -1 #"Sprzedaj"
        else:
            return 0 #"Trzymaj"

    # Oceny sumaryczne
    max_sma_signals = len(periods)
    max_ema_signals = len(periods)
    max_total_signals = max_sma_signals + max_ema_signals

    sma_summary = sum_to_signal(sma_sum, max_sma_signals)
    ema_summary = sum_to_signal(ema_sum, max_ema_signals)
    total_summary = sum_to_signal(total_sum, max_total_signals)

    # Zliczanie sygnałów dla podsumowania
    sma_buy = sum(1 for s in sma_signals if s == 1)
    sma_sell = sum(1 for s in sma_signals if s == -1)
    sma_neutral = sum(1 for s in sma_signals if s == 0)

    ema_buy = sum(1 for s in ema_signals if s == 1)
    ema_sell = sum(1 for s in ema_signals if s == -1)
    ema_neutral = sum(1 for s in ema_signals if s == 0)

    results['sma_summary'] = {
        'signal': sma_summary,
        'score': sma_sum,
        'buy_count': sma_buy,
        'sell_count': sma_sell,
        'neutral_count': sma_neutral
    }

    results['ema_summary'] = {
        'signal': ema_summary,
        'score': ema_sum,
        'buy_count': ema_buy,
        'sell_count': ema_sell,
        'neutral_count': ema_neutral
    }

    results['overall_summary'] = {
        'signal': total_summary,
        'score': total_sum,
        'max_score': max_total_signals,
        'buy_count': sma_buy + ema_buy,
        'sell_count': sma_sell + ema_sell,
        'neutral_count': sma_neutral + ema_neutral
    }

    return results
