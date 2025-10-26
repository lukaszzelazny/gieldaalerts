import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import os


class FinancialReportsMonitor:
    def __init__(self, storage_file="financial_reports_cache.json"):
        self.storage_file = storage_file
        self.cache = self.load_cache()

    def load_cache(self):
        """Ładuje cache z poprzednimi raportami"""
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {}

    def save_cache(self):
        """Zapisuje cache do pliku"""
        with open(self.storage_file, 'w') as f:
            json.dump(self.cache, f, indent=2, default=str)

    def check_for_new_report(self, ticker_symbol):
        """
        Sprawdza czy pojawił się nowy raport finansowy dla danego tickera.

        Returns:
            dict: {'has_new_report': bool, 'report_type': str, 'report_date': str}
        """
        try:
            ticker = yf.Ticker(ticker_symbol)

            # Pobieramy najnowsze daty raportów
            quarterly_financials = ticker.quarterly_financials
            annual_financials = ticker.financials

            if quarterly_financials.empty and annual_financials.empty:
                return {'has_new_report': False, 'error': 'Brak danych finansowych'}

            # Znajdź najnowszą datę raportu
            latest_quarterly_date = None
            latest_annual_date = None

            if not quarterly_financials.empty:
                latest_quarterly_date = quarterly_financials.columns[0]

            if not annual_financials.empty:
                latest_annual_date = annual_financials.columns[0]

            # Określ który raport jest najnowszy
            latest_date = None
            report_type = None

            if latest_quarterly_date and latest_annual_date:
                if latest_quarterly_date > latest_annual_date:
                    latest_date = latest_quarterly_date
                    report_type = "quarterly"
                else:
                    latest_date = latest_annual_date
                    report_type = "annual"
            elif latest_quarterly_date:
                latest_date = latest_quarterly_date
                report_type = "quarterly"
            elif latest_annual_date:
                latest_date = latest_annual_date
                report_type = "annual"

            if not latest_date:
                return {'has_new_report': False, 'error': 'Nie można określić daty raportu'}

            # Sprawdź w cache
            cache_key = f"{ticker_symbol}_last_report"
            cached_date = self.cache.get(cache_key)

            # Konwertuj daty do porównania
            latest_date_str = latest_date.strftime('%Y-%m-%d')

            has_new_report = cached_date != latest_date_str

            # Zaktualizuj cache jeśli jest nowy raport
            if has_new_report:
                self.cache[cache_key] = latest_date_str
                self.cache[f"{cache_key}_type"] = report_type
                self.save_cache()

            return {
                'has_new_report': has_new_report,
                'report_type': report_type,
                'report_date': latest_date_str,
                'previous_date': cached_date
            }

        except Exception as e:
            return {'has_new_report': False, 'error': str(e)}

    def analyze_latest_report_changes(self, ticker_symbol):
        """
        Analizuje różnice między ostatnim raportem a analogicznym okresem rok wcześniej (year-over-year).
        """
        try:
            ticker = yf.Ticker(ticker_symbol)
            info = ticker.info

            # Sprawdzamy który typ raportu jest najnowszy
            check_result = self.check_for_new_report(ticker_symbol)
            if not check_result['has_new_report'] and check_result.get('error'):
                # Sprawdzamy nawet jeśli nie ma nowego raportu
                pass

            report_type = check_result.get('report_type', 'quarterly')

            # Wybieramy odpowiednie dane
            if report_type == 'quarterly':
                financials = ticker.quarterly_financials
                balance_sheet = ticker.quarterly_balance_sheet
                cash_flow = ticker.quarterly_cashflow
            else:
                financials = ticker.financials
                balance_sheet = ticker.balance_sheet
                cash_flow = ticker.cashflow

            if financials.empty:
                return {'error': 'Brak danych finansowych'}

            # Podstawowe informacje
            company_name = info.get('longName', ticker_symbol)
            currency = info.get('currency', 'USD')

            analysis = {
                'podstawowe_info': {
                    'symbol': ticker_symbol,
                    'nazwa_firmy': company_name,
                    'typ_raportu': 'Kwartalny' if report_type == 'quarterly' else 'Roczny',
                    'data_raportu': check_result.get('report_date', 'Nieznana'),
                    'waluta': currency
                },
                'zmiany_vs_rok_poprzedni': {},  # Zmieniona nazwa dla jasności
                'kluczowe_wskazniki': {},
                'alerty': []
            }

            # === ANALIZA ZMIAN YEAR-OVER-YEAR (KLUCZOWE METRYKI) ===

            # 1. Przychody (Revenue) - porównanie YoY
            revenue_data = self.get_financial_metric_yoy(financials, ['Total Revenue', 'Revenue', 'Net Sales'],
                                                         report_type)
            if revenue_data:
                current, year_ago, change_pct = revenue_data
                analysis['zmiany_vs_rok_poprzedni']['przychody'] = {
                    'aktualny': self.format_currency(current, currency),
                    'rok_poprzedni': self.format_currency(year_ago, currency),
                    'zmiana_procent': round(change_pct, 2),
                    'zmiana_yoy': round(change_pct, 2),
                    'trend': 'Wzrost' if change_pct > 0 else 'Spadek' if change_pct < 0 else 'Bez zmian'
                }

                # Alert dla znaczących zmian przychodów
                if abs(change_pct) > 15:
                    alert_type = 'POZYTYWNY' if change_pct > 0 else 'NEGATYWNY'
                    analysis['alerty'].append(f"{alert_type}: Znacząca zmiana przychodów YoY o {change_pct:.1f}%")

            # 2. Zysk operacyjny (Operating Income) - YoY
            operating_income_data = self.get_financial_metric_yoy(financials, [
                'Operating Income', 'Operating Revenue', 'Earnings Before Interest and Tax'
            ], report_type)
            if operating_income_data:
                current, year_ago, change_pct = operating_income_data
                analysis['zmiany_vs_rok_poprzedni']['zysk_operacyjny'] = {
                    'aktualny': self.format_currency(current, currency),
                    'rok_poprzedni': self.format_currency(year_ago, currency),
                    'zmiana_procent': round(change_pct, 2),
                    'zmiana_yoy': round(change_pct, 2),
                    'trend': 'Wzrost' if change_pct > 0 else 'Spadek' if change_pct < 0 else 'Bez zmian'
                }

                # Alert dla zmian zysku operacyjnego
                if abs(change_pct) > 20:
                    alert_type = 'POZYTYWNY' if change_pct > 0 else 'NEGATYWNY'
                    analysis['alerty'].append(
                        f"{alert_type}: Znacząca zmiana zysku operacyjnego YoY o {change_pct:.1f}%")

            # 3. Zysk netto (Net Income) - YoY
            net_income_data = self.get_financial_metric_yoy(financials,
                                                            ['Net Income', 'Net Income Common Stockholders'],
                                                            report_type)
            shares_outstanding = info.get('sharesOutstanding', info.get('impliedSharesOutstanding'))

            if net_income_data:
                current_ni, year_ago_ni, ni_change = net_income_data

                analysis['zmiany_vs_rok_poprzedni']['zysk_netto'] = {
                    'aktualny': self.format_currency(current_ni, currency),
                    'rok_poprzedni': self.format_currency(year_ago_ni, currency),
                    'zmiana_procent': round(ni_change, 2),
                    'zmiana_yoy': round(ni_change, 2),
                    'trend': 'Wzrost' if ni_change > 0 else 'Spadek' if ni_change < 0 else 'Bez zmian'
                }

                # Oblicz EPS jeśli możliwe
                if shares_outstanding:
                    current_eps = current_ni / shares_outstanding if shares_outstanding > 0 else 0
                    year_ago_eps = year_ago_ni / shares_outstanding if shares_outstanding > 0 else 0
                    eps_change = ((current_eps - year_ago_eps) / abs(year_ago_eps) * 100) if year_ago_eps != 0 else 0

                    analysis['zmiany_vs_rok_poprzedni']['eps'] = {
                        'aktualny': f"{current_eps:.2f}",
                        'rok_poprzedni': f"{year_ago_eps:.2f}",
                        'zmiana_procent': round(eps_change, 2),
                        'zmiana_yoy': round(eps_change, 2),
                        'trailing_eps': info.get('trailingEps'),
                        'forward_eps': info.get('forwardEps')
                    }

                    # Alert dla zmian EPS
                    if abs(eps_change) > 25:
                        alert_type = 'POZYTYWNY' if eps_change > 0 else 'NEGATYWNY'
                        analysis['alerty'].append(f"{alert_type}: Znacząca zmiana EPS YoY o {eps_change:.1f}%")

            # 4. EBITDA - YoY
            ebitda_data = self.get_financial_metric_yoy(financials, ['EBITDA'], report_type)
            if ebitda_data:
                current, year_ago, change_pct = ebitda_data
                analysis['zmiany_vs_rok_poprzedni']['ebitda'] = {
                    'aktualny': self.format_currency(current, currency),
                    'rok_poprzedni': self.format_currency(year_ago, currency),
                    'zmiana_procent': round(change_pct, 2),
                    'zmiana_yoy': round(change_pct, 2),
                    'trend': 'Wzrost' if change_pct > 0 else 'Spadek' if change_pct < 0 else 'Bez zmian'
                }

                # Alert dla zmian EBITDA
                if abs(change_pct) > 20:
                    alert_type = 'POZYTYWNY' if change_pct > 0 else 'NEGATYWNY'
                    analysis['alerty'].append(f"{alert_type}: Znacząca zmiana EBITDA YoY o {change_pct:.1f}%")

            # Sprawdź trendy i alerty dla wszystkich kluczowych metryk
            self.check_cross_metric_alerts(analysis)

            # Marża operacyjna (obliczamy ją) - YoY
            if revenue_data and operating_income_data:
                current_margin = (operating_income_data[0] / revenue_data[0]) * 100 if revenue_data[0] != 0 else 0
                year_ago_margin = (operating_income_data[1] / revenue_data[1]) * 100 if revenue_data[1] != 0 else 0
                margin_change = current_margin - year_ago_margin

                analysis['zmiany_vs_rok_poprzedni']['marza_operacyjna'] = {
                    'aktualna_procent': round(current_margin, 2),
                    'rok_poprzedni_procent': round(year_ago_margin, 2),
                    'zmiana_pp': round(margin_change, 2)  # punkty procentowe
                }

                # Alert dla znaczącej zmiany marży
                if abs(margin_change) > 2:
                    alert_type = 'POZYTYWNY' if margin_change > 0 else 'NEGATYWNY'
                    analysis['alerty'].append(f"{alert_type}: Zmiana marży operacyjnej YoY o {margin_change:.1f} p.p.")

            # Marża netto - YoY
            if revenue_data and net_income_data:
                current_margin = (net_income_data[0] / revenue_data[0]) * 100 if revenue_data[0] != 0 else 0
                year_ago_margin = (net_income_data[1] / revenue_data[1]) * 100 if revenue_data[1] != 0 else 0
                margin_change = current_margin - year_ago_margin

                analysis['zmiany_vs_rok_poprzedni']['marza_netto'] = {
                    'aktualna_procent': round(current_margin, 2),
                    'rok_poprzedni_procent': round(year_ago_margin, 2),
                    'zmiana_pp': round(margin_change, 2)  # punkty procentowe
                }

            # Dług (z bilansu) - YoY
            debt_data = self.get_financial_metric_yoy(balance_sheet, ['Total Debt', 'Long Term Debt', 'Net Debt'],
                                                      report_type)
            if debt_data:
                current, year_ago, change_pct = debt_data
                analysis['zmiany_vs_rok_poprzedni']['zadluzenie'] = {
                    'aktualny': self.format_currency(current, currency),
                    'rok_poprzedni': self.format_currency(year_ago, currency),
                    'zmiana_procent': round(change_pct, 2)
                }

                # Alert dla znaczącej zmiany zadłużenia
                if abs(change_pct) > 25:
                    alert_type = 'UWAGA' if change_pct > 0 else 'POZYTYWNY'
                    analysis['alerty'].append(f"{alert_type}: Znacząca zmiana zadłużenia YoY o {change_pct:.1f}%")

            # Wolne przepływy pieniężne - YoY
            fcf_data = self.get_financial_metric_yoy(cash_flow, ['Free Cash Flow'], report_type)
            if fcf_data:
                current, year_ago, change_pct = fcf_data
                analysis['zmiany_vs_rok_poprzedni']['wolne_przeplywy'] = {
                    'aktualny': self.format_currency(current, currency),
                    'rok_poprzedni': self.format_currency(year_ago, currency),
                    'zmiana_procent': round(change_pct, 2)
                }

            # === KLUCZOWE WSKAŹNIKI ===
            analysis['kluczowe_wskazniki'] = {
                'pe_ratio': round(info.get('trailingPE', 0), 2) if info.get('trailingPE') else None,
                'pb_ratio': round(info.get('priceToBook', 0), 2) if info.get('priceToBook') else None,
                'dywidenda_procent': round(info.get('dividendYield', 0) * 100, 2) if info.get('dividendYield') else 0
            }

            # === OGÓLNA OCENA ===
            analysis['ocena_ogolna'] = self.generate_overall_assessment(analysis)

            return analysis

        except Exception as e:
            return {'error': f"Błąd podczas analizy: {str(e)}"}

    def get_financial_metric_yoy(self, data_frame, possible_keys, report_type):
        """
        Pobiera metrykę finansową i oblicza zmianę year-over-year
        Dla raportów kwartalnych: Q3 2024 vs Q3 2023
        Dla raportów rocznych: 2024 vs 2023
        """
        if data_frame.empty:
            return None

        for key in possible_keys:
            if key in data_frame.index:
                series = data_frame.loc[key].dropna()

                if report_type == 'quarterly':
                    # Dla kwartalnych: szukamy analogicznego kwartału rok wcześniej
                    # Zwykle potrzebujemy 4 okresy wstecz (4 kwartały = 1 rok)
                    if len(series) >= 5:  # Potrzebujemy co najmniej 5 punktów danych
                        current = series.iloc[0]  # Najnowszy kwartał
                        year_ago = series.iloc[4]  # Ten sam kwartał rok wcześniej
                    else:
                        return None
                else:
                    # Dla rocznych: porównujemy rok do roku
                    if len(series) >= 2:
                        current = series.iloc[0]  # Najnowszy rok
                        year_ago = series.iloc[1]  # Poprzedni rok
                    else:
                        return None

                if year_ago != 0:
                    change_pct = ((current - year_ago) / abs(year_ago)) * 100
                else:
                    change_pct = 0

                return current, year_ago, change_pct
        return None

    def format_currency(self, amount, currency='USD'):
        """Formatuje kwotę z walutą"""
        if pd.isna(amount) or amount == 0:
            return f"0 {currency}"

        abs_amount = abs(amount)
        if abs_amount >= 1e12:
            return f"{amount / 1e12:.2f}T {currency}"
        elif abs_amount >= 1e9:
            return f"{amount / 1e9:.2f}B {currency}"
        elif abs_amount >= 1e6:
            return f"{amount / 1e6:.2f}M {currency}"
        elif abs_amount >= 1e3:
            return f"{amount / 1e3:.2f}K {currency}"
        else:
            return f"{amount:.2f} {currency}"

    def check_cross_metric_alerts(self, analysis):
        """Sprawdza alerty na podstawie wielu metryk jednocześnie"""
        changes = analysis['zmiany_vs_rok_poprzedni']

        # Sprawdź czy wszystkie kluczowe metryki rosną
        revenue_growth = changes.get('przychody', {}).get('zmiana_yoy', 0)
        operating_growth = changes.get('zysk_operacyjny', {}).get('zmiana_yoy', 0)
        ebitda_growth = changes.get('ebitda', {}).get('zmiana_yoy', 0)
        eps_growth = changes.get('eps', {}).get('zmiana_yoy', 0)

        positive_metrics = sum([
            1 for growth in [revenue_growth, operating_growth, ebitda_growth, eps_growth]
            if growth > 0
        ])

        if positive_metrics >= 3:
            analysis['alerty'].append("POZYTYWNY: Wzrost YoY w większości kluczowych metryk")
        elif positive_metrics <= 1:
            analysis['alerty'].append("NEGATYWNY: Spadek YoY w większości kluczowych metryk")

        # Sprawdź rozbieżności (przychody rosną ale zysk operacyjny spada)
        if revenue_growth > 5 and operating_growth < -5:
            analysis['alerty'].append(
                "UWAGA: Przychody rosną YoY ale zysk operacyjny spada - możliwe problemy z kosztami")

    def generate_overall_assessment(self, analysis):
        """Generuje ogólną ocenę wyników"""
        assessment = []

        changes = analysis['zmiany_vs_rok_poprzedni']
        revenue_change = changes.get('przychody', {}).get('zmiana_procent', 0)
        profit_change = changes.get('zysk_operacyjny', {}).get('zmiana_procent', 0)
        margin_change = changes.get('marza_operacyjna', {}).get('zmiana_pp', 0)

        # Ocena przychodów YoY
        if revenue_change > 10:
            assessment.append("Silny wzrost sprzedaży YoY")
        elif revenue_change > 0:
            assessment.append("Wzrost sprzedaży YoY")
        elif revenue_change < -10:
            assessment.append("Znaczący spadek sprzedaży YoY")
        else:
            assessment.append("Stagnacja sprzedaży YoY")

        # Ocena rentowności YoY
        if profit_change > 15:
            assessment.append("Znacząca poprawa rentowności YoY")
        elif profit_change > 0:
            assessment.append("Poprawa wyników YoY")
        elif profit_change < -15:
            assessment.append("Pogorszenie rentowności YoY")

        # Ocena efektywności YoY
        if margin_change > 1:
            assessment.append("Wzrost efektywności operacyjnej YoY")
        elif margin_change < -1:
            assessment.append("Spadek efektywności operacyjnej YoY")

        return assessment

    def get_key_metrics_summary(self, ticker_symbol):
        """
        Zwraca skoncentrowane podsumowanie 4 kluczowych metryk z porównaniem YoY.
        Idealne do szybkiego przeglądu w aplikacji.
        """
        analysis = self.analyze_latest_report_changes(ticker_symbol)

        if 'error' in analysis:
            return analysis

        changes = analysis['zmiany_vs_rok_poprzedni']

        key_metrics = {
            'ticker': ticker_symbol,
            'firma': analysis['podstawowe_info']['nazwa_firmy'],
            'data_raportu': analysis['podstawowe_info']['data_raportu'],
            'typ_raportu': analysis['podstawowe_info']['typ_raportu'],
            'metryki': {
                'przychody': {
                    'wartosc': changes.get('przychody', {}).get('aktualny', 'Brak danych'),
                    'zmiana_yoy': changes.get('przychody', {}).get('zmiana_yoy', 0),
                    'status': self.get_metric_status(changes.get('przychody', {}).get('zmiana_yoy', 0))
                },
                'zysk_operacyjny': {
                    'wartosc': changes.get('zysk_operacyjny', {}).get('aktualny', 'Brak danych'),
                    'zmiana_yoy': changes.get('zysk_operacyjny', {}).get('zmiana_yoy', 0),
                    'status': self.get_metric_status(changes.get('zysk_operacyjny', {}).get('zmiana_yoy', 0))
                },
                'eps': {
                    'wartosc': changes.get('eps', {}).get('aktualny',
                                                          changes.get('eps', {}).get('trailing_eps', 'Brak danych')),
                    'zmiana_yoy': changes.get('eps', {}).get('zmiana_yoy', 0),
                    'status': self.get_metric_status(changes.get('eps', {}).get('zmiana_yoy', 0))
                },
                'ebitda': {
                    'wartosc': changes.get('ebitda', {}).get('aktualny', 'Brak danych'),
                    'zmiana_yoy': changes.get('ebitda', {}).get('zmiana_yoy', 0),
                    'status': self.get_metric_status(changes.get('ebitda', {}).get('zmiana_yoy', 0))
                }
            },
            'marza_operacyjna': changes.get('marza_operacyjna', {}),
            'ogolna_ocena': self.get_quick_assessment(changes),
            'najwazniejsze_alerty': analysis.get('alerty', [])[:3]  # Tylko 3 najważniejsze
        }

        return key_metrics

    def get_metric_status(self, change_pct):
        """Zwraca status metryki na podstawie zmiany % YoY"""
        if change_pct > 15:
            return "📈 Silny wzrost"
        elif change_pct > 5:
            return "↗️ Wzrost"
        elif change_pct > -5:
            return "➡️ Stabilna"
        elif change_pct > -15:
            return "↘️ Spadek"
        else:
            return "📉 Silny spadek"

    def get_quick_assessment(self, changes):
        """Szybka ocena na podstawie kluczowych metryk YoY"""
        metrics = ['przychody', 'zysk_operacyjny', 'eps', 'ebitda']
        positive_count = 0

        for metric in metrics:
            change = changes.get(metric, {}).get('zmiana_yoy', 0)
            if change > 0:
                positive_count += 1

        if positive_count >= 3:
            return "🟢 Bardzo dobre wyniki YoY"
        elif positive_count == 2:
            return "🟡 Mieszane wyniki YoY"
        elif positive_count == 1:
            return "🟠 Słabe wyniki YoY"
        else:
            return "🔴 Bardzo słabe wyniki YoY"

    def get_earnings_alert(self, ticker_symbol):
        """
        Główna funkcja do sprawdzania alertów o nowych raportach.
        Zwraca analizę tylko jeśli pojawił się nowy raport.
        """
        check_result = self.check_for_new_report(ticker_symbol)

        if check_result.get('has_new_report'):
            print(f"🚨 NOWY RAPORT FINANSOWY - {ticker_symbol}")
            print(f"Typ: {check_result['report_type']}")
            print(f"Data: {check_result['report_date']}")
            print("-" * 50)

            analysis = self.analyze_latest_report_changes(ticker_symbol)
            return {
                'new_report_detected': True,
                'analysis': analysis
            }
        else:
            return {
                'new_report_detected': False,
                'message': f"Brak nowego raportu dla {ticker_symbol}",
                'last_report_date': check_result.get('report_date')
            }

    def force_analysis(self, ticker_symbol):
        """
        Wymusza analizę niezależnie od tego czy raport jest nowy.
        """
        return self.analyze_latest_report_changes(ticker_symbol)


# Funkcje pomocnicze do użycia w aplikacji
def check_new_reports_for_portfolio(tickers_list):
    """Sprawdza nowe raporty dla całego portfela"""
    monitor = FinancialReportsMonitor()
    new_reports = []

    for ticker in tickers_list:
        result = monitor.get_earnings_alert(ticker)
        if result['new_report_detected']:
            new_reports.append({
                'ticker': ticker,
                'analysis': result['analysis']
            })

    return new_reports


def print_financial_analysis(analysis):
    """Wyświetla analizę finansową w czytelny sposób"""
    if 'error' in analysis:
        print(f"Błąd: {analysis['error']}")
        return

    info = analysis['podstawowe_info']
    print(f"\n=== {info['nazwa_firmy']} ({info['symbol']}) ===")
    print(f"Raport: {info['typ_raportu']} z {info['data_raportu']}")

    # Zmiany vs rok poprzedni (YoY)
    changes = analysis['zmiany_vs_rok_poprzedni']
    print(f"\n--- ZMIANY YEAR-OVER-YEAR ---")

    for metric, data in changes.items():
        if metric in ['marza_operacyjna', 'marza_netto']:
            current_key = 'aktualna_procent'
            previous_key = 'rok_poprzedni_procent'
            if current_key in data and previous_key in data:
                print(
                    f"{metric.replace('_', ' ').title()}: {data[current_key]}% (zmiana: {data['zmiana_pp']:+.1f} p.p.)")
        else:
            if 'zmiana_procent' in data:
                print(f"{metric.replace('_', ' ').title()}: {data['aktualny']} (YoY: {data['zmiana_procent']:+.1f}%)")

    # Alerty
    if analysis['alerty']:
        print(f"\n🚨 ALERTY:")
        for alert in analysis['alerty']:
            print(f"  • {alert}")

    # Ocena ogólna
    print(f"\n--- PODSUMOWANIE ---")
    for point in analysis['ocena_ogolna']:
        print(f"• {point}")


import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import os


class FinancialReportsMonitor:
    def __init__(self, storage_file="financial_reports_cache.json"):
        self.storage_file = storage_file
        self.cache = self.load_cache()

    def load_cache(self):
        """Ładuje cache z poprzednimi raportami"""
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {}

    def save_cache(self):
        """Zapisuje cache do pliku"""
        with open(self.storage_file, 'w') as f:
            json.dump(self.cache, f, indent=2, default=str)

    def check_for_new_report(self, ticker_symbol):
        """
        Sprawdza czy pojawił się nowy raport finansowy dla danego tickera.

        Returns:
            dict: {'has_new_report': bool, 'report_type': str, 'report_date': str}
        """
        try:
            ticker = yf.Ticker(ticker_symbol)

            # Pobieramy najnowsze daty raportów
            quarterly_financials = ticker.quarterly_financials
            annual_financials = ticker.financials

            if quarterly_financials.empty and annual_financials.empty:
                return {'has_new_report': False, 'error': 'Brak danych finansowych'}

            # Znajdź najnowszą datę raportu
            latest_quarterly_date = None
            latest_annual_date = None

            if not quarterly_financials.empty:
                latest_quarterly_date = quarterly_financials.columns[0]

            if not annual_financials.empty:
                latest_annual_date = annual_financials.columns[0]

            # Określ który raport jest najnowszy
            latest_date = None
            report_type = None

            if latest_quarterly_date and latest_annual_date:
                if latest_quarterly_date > latest_annual_date:
                    latest_date = latest_quarterly_date
                    report_type = "quarterly"
                else:
                    latest_date = latest_annual_date
                    report_type = "annual"
            elif latest_quarterly_date:
                latest_date = latest_quarterly_date
                report_type = "quarterly"
            elif latest_annual_date:
                latest_date = latest_annual_date
                report_type = "annual"

            if not latest_date:
                return {'has_new_report': False, 'error': 'Nie można określić daty raportu'}

            # Sprawdź w cache
            cache_key = f"{ticker_symbol}_last_report"
            cached_date = self.cache.get(cache_key)

            # Konwertuj daty do porównania
            latest_date_str = latest_date.strftime('%Y-%m-%d')

            has_new_report = cached_date != latest_date_str

            # Zaktualizuj cache jeśli jest nowy raport
            if has_new_report:
                self.cache[cache_key] = latest_date_str
                self.cache[f"{cache_key}_type"] = report_type
                self.save_cache()

            return {
                'has_new_report': has_new_report,
                'report_type': report_type,
                'report_date': latest_date_str,
                'previous_date': cached_date
            }

        except Exception as e:
            return {'has_new_report': False, 'error': str(e)}

    def analyze_latest_report_changes(self, ticker_symbol):
        """
        Analizuje różnice między ostatnim raportem a analogicznym okresem rok wcześniej (year-over-year).
        """
        try:
            ticker = yf.Ticker(ticker_symbol)
            info = ticker.info

            # Sprawdzamy który typ raportu jest najnowszy
            check_result = self.check_for_new_report(ticker_symbol)
            if not check_result['has_new_report'] and check_result.get('error'):
                # Sprawdzamy nawet jeśli nie ma nowego raportu
                pass

            report_type = check_result.get('report_type', 'quarterly')

            # Wybieramy odpowiednie dane
            if report_type == 'quarterly':
                financials = ticker.quarterly_financials
                balance_sheet = ticker.quarterly_balance_sheet
                cash_flow = ticker.quarterly_cashflow
            else:
                financials = ticker.financials
                balance_sheet = ticker.balance_sheet
                cash_flow = ticker.cashflow

            if financials.empty:
                return {'error': 'Brak danych finansowych'}

            # Podstawowe informacje
            company_name = info.get('longName', ticker_symbol)
            currency = info.get('currency', 'USD')

            analysis = {
                'podstawowe_info': {
                    'symbol': ticker_symbol,
                    'nazwa_firmy': company_name,
                    'typ_raportu': 'Kwartalny' if report_type == 'quarterly' else 'Roczny',
                    'data_raportu': check_result.get('report_date', 'Nieznana'),
                    'waluta': currency
                },
                'zmiany_vs_rok_poprzedni': {},  # Zmieniona nazwa dla jasności
                'kluczowe_wskazniki': {},
                'alerty': []
            }

            # === ANALIZA ZMIAN YEAR-OVER-YEAR (KLUCZOWE METRYKI) ===

            # 1. Przychody (Revenue) - porównanie YoY
            revenue_data = self.get_financial_metric_yoy(financials, ['Total Revenue', 'Revenue', 'Net Sales'],
                                                         report_type)
            if revenue_data:
                current, year_ago, change_pct = revenue_data
                analysis['zmiany_vs_rok_poprzedni']['przychody'] = {
                    'aktualny': self.format_currency(current, currency),
                    'rok_poprzedni': self.format_currency(year_ago, currency),
                    'zmiana_procent': round(change_pct, 2),
                    'zmiana_yoy': round(change_pct, 2),
                    'trend': 'Wzrost' if change_pct > 0 else 'Spadek' if change_pct < 0 else 'Bez zmian'
                }

                # Alert dla znaczących zmian przychodów
                if abs(change_pct) > 15:
                    alert_type = 'POZYTYWNY' if change_pct > 0 else 'NEGATYWNY'
                    analysis['alerty'].append(f"{alert_type}: Znacząca zmiana przychodów YoY o {change_pct:.1f}%")

            # 2. Zysk operacyjny (Operating Income) - YoY
            operating_income_data = self.get_financial_metric_yoy(financials, [
                'Operating Income', 'Operating Revenue', 'Earnings Before Interest and Tax'
            ], report_type)
            if operating_income_data:
                current, year_ago, change_pct = operating_income_data
                analysis['zmiany_vs_rok_poprzedni']['zysk_operacyjny'] = {
                    'aktualny': self.format_currency(current, currency),
                    'rok_poprzedni': self.format_currency(year_ago, currency),
                    'zmiana_procent': round(change_pct, 2),
                    'zmiana_yoy': round(change_pct, 2),
                    'trend': 'Wzrost' if change_pct > 0 else 'Spadek' if change_pct < 0 else 'Bez zmian'
                }

                # Alert dla zmian zysku operacyjnego
                if abs(change_pct) > 20:
                    alert_type = 'POZYTYWNY' if change_pct > 0 else 'NEGATYWNY'
                    analysis['alerty'].append(
                        f"{alert_type}: Znacząca zmiana zysku operacyjnego YoY o {change_pct:.1f}%")

            # 3. Zysk netto (Net Income) - YoY
            net_income_data = self.get_financial_metric_yoy(financials,
                                                            ['Net Income', 'Net Income Common Stockholders'],
                                                            report_type)
            shares_outstanding = info.get('sharesOutstanding', info.get('impliedSharesOutstanding'))

            if net_income_data:
                current_ni, year_ago_ni, ni_change = net_income_data

                analysis['zmiany_vs_rok_poprzedni']['zysk_netto'] = {
                    'aktualny': self.format_currency(current_ni, currency),
                    'rok_poprzedni': self.format_currency(year_ago_ni, currency),
                    'zmiana_procent': round(ni_change, 2),
                    'zmiana_yoy': round(ni_change, 2),
                    'trend': 'Wzrost' if ni_change > 0 else 'Spadek' if ni_change < 0 else 'Bez zmian'
                }

                # Oblicz EPS jeśli możliwe
                if shares_outstanding:
                    current_eps = current_ni / shares_outstanding if shares_outstanding > 0 else 0
                    year_ago_eps = year_ago_ni / shares_outstanding if shares_outstanding > 0 else 0
                    eps_change = ((current_eps - year_ago_eps) / abs(year_ago_eps) * 100) if year_ago_eps != 0 else 0

                    analysis['zmiany_vs_rok_poprzedni']['eps'] = {
                        'aktualny': f"{current_eps:.2f}",
                        'rok_poprzedni': f"{year_ago_eps:.2f}",
                        'zmiana_procent': round(eps_change, 2),
                        'zmiana_yoy': round(eps_change, 2),
                        'trailing_eps': info.get('trailingEps'),
                        'forward_eps': info.get('forwardEps')
                    }

                    # Alert dla zmian EPS
                    if abs(eps_change) > 25:
                        alert_type = 'POZYTYWNY' if eps_change > 0 else 'NEGATYWNY'
                        analysis['alerty'].append(f"{alert_type}: Znacząca zmiana EPS YoY o {eps_change:.1f}%")

            # 4. EBITDA - YoY
            ebitda_data = self.get_financial_metric_yoy(financials, ['EBITDA'], report_type)
            if ebitda_data:
                current, year_ago, change_pct = ebitda_data
                analysis['zmiany_vs_rok_poprzedni']['ebitda'] = {
                    'aktualny': self.format_currency(current, currency),
                    'rok_poprzedni': self.format_currency(year_ago, currency),
                    'zmiana_procent': round(change_pct, 2),
                    'zmiana_yoy': round(change_pct, 2),
                    'trend': 'Wzrost' if change_pct > 0 else 'Spadek' if change_pct < 0 else 'Bez zmian'
                }

                # Alert dla zmian EBITDA
                if abs(change_pct) > 20:
                    alert_type = 'POZYTYWNY' if change_pct > 0 else 'NEGATYWNY'
                    analysis['alerty'].append(f"{alert_type}: Znacząca zmiana EBITDA YoY o {change_pct:.1f}%")

            # Sprawdź trendy i alerty dla wszystkich kluczowych metryk
            self.check_cross_metric_alerts(analysis)

            # Marża operacyjna (obliczamy ją) - YoY
            if revenue_data and operating_income_data:
                current_margin = (operating_income_data[0] / revenue_data[0]) * 100 if revenue_data[0] != 0 else 0
                year_ago_margin = (operating_income_data[1] / revenue_data[1]) * 100 if revenue_data[1] != 0 else 0
                margin_change = current_margin - year_ago_margin

                analysis['zmiany_vs_rok_poprzedni']['marza_operacyjna'] = {
                    'aktualna_procent': round(current_margin, 2),
                    'rok_poprzedni_procent': round(year_ago_margin, 2),
                    'zmiana_pp': round(margin_change, 2)  # punkty procentowe
                }

                # Alert dla znaczącej zmiany marży
                if abs(margin_change) > 2:
                    alert_type = 'POZYTYWNY' if margin_change > 0 else 'NEGATYWNY'
                    analysis['alerty'].append(f"{alert_type}: Zmiana marży operacyjnej YoY o {margin_change:.1f} p.p.")

            # Marża netto - YoY
            if revenue_data and net_income_data:
                current_margin = (net_income_data[0] / revenue_data[0]) * 100 if revenue_data[0] != 0 else 0
                year_ago_margin = (net_income_data[1] / revenue_data[1]) * 100 if revenue_data[1] != 0 else 0
                margin_change = current_margin - year_ago_margin

                analysis['zmiany_vs_rok_poprzedni']['marza_netto'] = {
                    'aktualna_procent': round(current_margin, 2),
                    'rok_poprzedni_procent': round(year_ago_margin, 2),
                    'zmiana_pp': round(margin_change, 2)  # punkty procentowe
                }

            # Dług (z bilansu) - YoY
            debt_data = self.get_financial_metric_yoy(balance_sheet, ['Total Debt', 'Long Term Debt', 'Net Debt'],
                                                      report_type)
            if debt_data:
                current, year_ago, change_pct = debt_data
                analysis['zmiany_vs_rok_poprzedni']['zadluzenie'] = {
                    'aktualny': self.format_currency(current, currency),
                    'rok_poprzedni': self.format_currency(year_ago, currency),
                    'zmiana_procent': round(change_pct, 2)
                }

                # Alert dla znaczącej zmiany zadłużenia
                if abs(change_pct) > 25:
                    alert_type = 'UWAGA' if change_pct > 0 else 'POZYTYWNY'
                    analysis['alerty'].append(f"{alert_type}: Znacząca zmiana zadłużenia YoY o {change_pct:.1f}%")

            # Wolne przepływy pieniężne - YoY
            fcf_data = self.get_financial_metric_yoy(cash_flow, ['Free Cash Flow'], report_type)
            if fcf_data:
                current, year_ago, change_pct = fcf_data
                analysis['zmiany_vs_rok_poprzedni']['wolne_przeplywy'] = {
                    'aktualny': self.format_currency(current, currency),
                    'rok_poprzedni': self.format_currency(year_ago, currency),
                    'zmiana_procent': round(change_pct, 2)
                }

            # === KLUCZOWE WSKAŹNIKI ===
            analysis['kluczowe_wskazniki'] = {
                'pe_ratio': round(info.get('trailingPE', 0), 2) if info.get('trailingPE') else None,
                'pb_ratio': round(info.get('priceToBook', 0), 2) if info.get('priceToBook') else None,
                'dywidenda_procent': round(info.get('dividendYield', 0) * 100, 2) if info.get('dividendYield') else 0
            }

            # === OGÓLNA OCENA ===
            analysis['ocena_ogolna'] = self.generate_overall_assessment(analysis)

            return analysis

        except Exception as e:
            return {'error': f"Błąd podczas analizy: {str(e)}"}

    def get_financial_metric_yoy(self, data_frame, possible_keys, report_type):
        """
        Pobiera metrykę finansową i oblicza zmianę year-over-year
        Dla raportów kwartalnych: Q3 2024 vs Q3 2023
        Dla raportów rocznych: 2024 vs 2023
        """
        if data_frame.empty:
            return None

        for key in possible_keys:
            if key in data_frame.index:
                series = data_frame.loc[key].dropna()

                if report_type == 'quarterly':
                    # Dla kwartalnych: szukamy analogicznego kwartału rok wcześniej
                    # Zwykle potrzebujemy 4 okresy wstecz (4 kwartały = 1 rok)
                    if len(series) >= 5:  # Potrzebujemy co najmniej 5 punktów danych
                        current = series.iloc[0]  # Najnowszy kwartał
                        year_ago = series.iloc[4]  # Ten sam kwartał rok wcześniej
                    else:
                        return None
                else:
                    # Dla rocznych: porównujemy rok do roku
                    if len(series) >= 2:
                        current = series.iloc[0]  # Najnowszy rok
                        year_ago = series.iloc[1]  # Poprzedni rok
                    else:
                        return None

                if year_ago != 0:
                    change_pct = ((current - year_ago) / abs(year_ago)) * 100
                else:
                    change_pct = 0

                return current, year_ago, change_pct
        return None

    def format_currency(self, amount, currency='USD'):
        """Formatuje kwotę z walutą"""
        if pd.isna(amount) or amount == 0:
            return f"0 {currency}"

        abs_amount = abs(amount)
        if abs_amount >= 1e12:
            return f"{amount / 1e12:.2f}T {currency}"
        elif abs_amount >= 1e9:
            return f"{amount / 1e9:.2f}B {currency}"
        elif abs_amount >= 1e6:
            return f"{amount / 1e6:.2f}M {currency}"
        elif abs_amount >= 1e3:
            return f"{amount / 1e3:.2f}K {currency}"
        else:
            return f"{amount:.2f} {currency}"

    def check_cross_metric_alerts(self, analysis):
        """Sprawdza alerty na podstawie wielu metryk jednocześnie"""
        changes = analysis['zmiany_vs_rok_poprzedni']

        # Sprawdź czy wszystkie kluczowe metryki rosną
        revenue_growth = changes.get('przychody', {}).get('zmiana_yoy', 0)
        operating_growth = changes.get('zysk_operacyjny', {}).get('zmiana_yoy', 0)
        ebitda_growth = changes.get('ebitda', {}).get('zmiana_yoy', 0)
        eps_growth = changes.get('eps', {}).get('zmiana_yoy', 0)

        positive_metrics = sum([
            1 for growth in [revenue_growth, operating_growth, ebitda_growth, eps_growth]
            if growth > 0
        ])

        if positive_metrics >= 3:
            analysis['alerty'].append("POZYTYWNY: Wzrost YoY w większości kluczowych metryk")
        elif positive_metrics <= 1:
            analysis['alerty'].append("NEGATYWNY: Spadek YoY w większości kluczowych metryk")

        # Sprawdź rozbieżności (przychody rosną ale zysk operacyjny spada)
        if revenue_growth > 5 and operating_growth < -5:
            analysis['alerty'].append(
                "UWAGA: Przychody rosną YoY ale zysk operacyjny spada - możliwe problemy z kosztami")

    def generate_overall_assessment(self, analysis):
        """Generuje ogólną ocenę wyników"""
        assessment = []

        changes = analysis['zmiany_vs_rok_poprzedni']
        revenue_change = changes.get('przychody', {}).get('zmiana_procent', 0)
        profit_change = changes.get('zysk_operacyjny', {}).get('zmiana_procent', 0)
        margin_change = changes.get('marza_operacyjna', {}).get('zmiana_pp', 0)

        # Ocena przychodów YoY
        if revenue_change > 10:
            assessment.append("Silny wzrost sprzedaży YoY")
        elif revenue_change > 0:
            assessment.append("Wzrost sprzedaży YoY")
        elif revenue_change < -10:
            assessment.append("Znaczący spadek sprzedaży YoY")
        else:
            assessment.append("Stagnacja sprzedaży YoY")

        # Ocena rentowności YoY
        if profit_change > 15:
            assessment.append("Znacząca poprawa rentowności YoY")
        elif profit_change > 0:
            assessment.append("Poprawa wyników YoY")
        elif profit_change < -15:
            assessment.append("Pogorszenie rentowności YoY")

        # Ocena efektywności YoY
        if margin_change > 1:
            assessment.append("Wzrost efektywności operacyjnej YoY")
        elif margin_change < -1:
            assessment.append("Spadek efektywności operacyjnej YoY")

        return assessment

    def get_key_metrics_summary(self, ticker_symbol):
        """
        Zwraca skoncentrowane podsumowanie 4 kluczowych metryk z porównaniem YoY.
        Idealne do szybkiego przeglądu w aplikacji.
        """
        analysis = self.analyze_latest_report_changes(ticker_symbol)

        if 'error' in analysis:
            return analysis

        changes = analysis['zmiany_vs_rok_poprzedni']

        key_metrics = {
            'ticker': ticker_symbol,
            'firma': analysis['podstawowe_info']['nazwa_firmy'],
            'data_raportu': analysis['podstawowe_info']['data_raportu'],
            'typ_raportu': analysis['podstawowe_info']['typ_raportu'],
            'metryki': {
                'przychody': {
                    'wartosc': changes.get('przychody', {}).get('aktualny', 'Brak danych'),
                    'zmiana_yoy': changes.get('przychody', {}).get('zmiana_yoy', 0),
                    'status': self.get_metric_status(changes.get('przychody', {}).get('zmiana_yoy', 0))
                },
                'zysk_operacyjny': {
                    'wartosc': changes.get('zysk_operacyjny', {}).get('aktualny', 'Brak danych'),
                    'zmiana_yoy': changes.get('zysk_operacyjny', {}).get('zmiana_yoy', 0),
                    'status': self.get_metric_status(changes.get('zysk_operacyjny', {}).get('zmiana_yoy', 0))
                },
                'eps': {
                    'wartosc': changes.get('eps', {}).get('aktualny',
                                                          changes.get('eps', {}).get('trailing_eps', 'Brak danych')),
                    'zmiana_yoy': changes.get('eps', {}).get('zmiana_yoy', 0),
                    'status': self.get_metric_status(changes.get('eps', {}).get('zmiana_yoy', 0))
                },
                'ebitda': {
                    'wartosc': changes.get('ebitda', {}).get('aktualny', 'Brak danych'),
                    'zmiana_yoy': changes.get('ebitda', {}).get('zmiana_yoy', 0),
                    'status': self.get_metric_status(changes.get('ebitda', {}).get('zmiana_yoy', 0))
                }
            },
            'marza_operacyjna': changes.get('marza_operacyjna', {}),
            'ogolna_ocena': self.get_quick_assessment(changes),
            'najwazniejsze_alerty': analysis.get('alerty', [])[:3]  # Tylko 3 najważniejsze
        }

        return key_metrics

    def get_metric_status(self, change_pct):
        """Zwraca status metryki na podstawie zmiany % YoY"""
        if change_pct > 15:
            return "📈 Silny wzrost"
        elif change_pct > 5:
            return "↗️ Wzrost"
        elif change_pct > -5:
            return "➡️ Stabilna"
        elif change_pct > -15:
            return "↘️ Spadek"
        else:
            return "📉 Silny spadek"

    def get_quick_assessment(self, changes):
        """Szybka ocena na podstawie kluczowych metryk YoY"""
        metrics = ['przychody', 'zysk_operacyjny', 'eps', 'ebitda']
        positive_count = 0

        for metric in metrics:
            change = changes.get(metric, {}).get('zmiana_yoy', 0)
            if change > 0:
                positive_count += 1

        if positive_count >= 3:
            return "🟢 Bardzo dobre wyniki YoY"
        elif positive_count == 2:
            return "🟡 Mieszane wyniki YoY"
        elif positive_count == 1:
            return "🟠 Słabe wyniki YoY"
        else:
            return "🔴 Bardzo słabe wyniki YoY"

    def get_earnings_alert(self, ticker_symbol):
        """
        Główna funkcja do sprawdzania alertów o nowych raportach.
        Zwraca analizę tylko jeśli pojawił się nowy raport.
        """
        check_result = self.check_for_new_report(ticker_symbol)

        if check_result.get('has_new_report'):
            print(f"🚨 NOWY RAPORT FINANSOWY - {ticker_symbol}")
            print(f"Typ: {check_result['report_type']}")
            print(f"Data: {check_result['report_date']}")
            print("-" * 50)

            analysis = self.analyze_latest_report_changes(ticker_symbol)
            return {
                'new_report_detected': True,
                'analysis': analysis
            }
        else:
            return {
                'new_report_detected': False,
                'message': f"Brak nowego raportu dla {ticker_symbol}",
                'last_report_date': check_result.get('report_date')
            }

    def force_analysis(self, ticker_symbol):
        """
        Wymusza analizę niezależnie od tego czy raport jest nowy.
        """
        return self.analyze_latest_report_changes(ticker_symbol)


# Funkcje pomocnicze do użycia w aplikacji
def check_new_reports_for_portfolio(tickers_list):
    """Sprawdza nowe raporty dla całego portfela"""
    monitor = FinancialReportsMonitor()
    new_reports = []

    for ticker in tickers_list:
        result = monitor.get_earnings_alert(ticker)
        if result['new_report_detected']:
            new_reports.append({
                'ticker': ticker,
                'analysis': result['analysis']
            })

    return new_reports


def print_financial_analysis(analysis):
    """Wyświetla analizę finansową w czytelny sposób"""
    if 'error' in analysis:
        print(f"Błąd: {analysis['error']}")
        return

    info = analysis['podstawowe_info']
    print(f"\n=== {info['nazwa_firmy']} ({info['symbol']}) ===")
    print(f"Raport: {info['typ_raportu']} z {info['data_raportu']}")

    # Zmiany vs rok poprzedni (YoY)
    changes = analysis['zmiany_vs_rok_poprzedni']
    print(f"\n--- ZMIANY YEAR-OVER-YEAR ---")

    for metric, data in changes.items():
        if metric in ['marza_operacyjna', 'marza_netto']:
            current_key = 'aktualna_procent'
            previous_key = 'rok_poprzedni_procent'
            if current_key in data and previous_key in data:
                print(
                    f"{metric.replace('_', ' ').title()}: {data[current_key]}% (zmiana: {data['zmiana_pp']:+.1f} p.p.)")
        else:
            if 'zmiana_procent' in data:
                print(f"{metric.replace('_', ' ').title()}: {data['aktualny']} (YoY: {data['zmiana_procent']:+.1f}%)")

    # Alerty
    if analysis['alerty']:
        print(f"\n🚨 ALERTY:")
        for alert in analysis['alerty']:
            print(f"  • {alert}")

    # Ocena ogólna
    print(f"\n--- PODSUMOWANIE ---")
    for point in analysis['ocena_ogolna']:
        print(f"• {point}")


def print_key_metrics(key_metrics):
    """Wyświetla kluczowe metryki w zwartej formie z porównaniem YoY"""
    
    retVal = []
    
    if 'error' in key_metrics:
        print(f"Błąd: {key_metrics['error']}")
        return

    print(f"\n📊 {key_metrics['firma']} ({key_metrics['ticker']})")
    print(f"📅 {key_metrics['typ_raportu']} - {key_metrics['data_raportu']}")
    print(f"🎯 Ocena: {key_metrics['ogolna_ocena']}")

    print(f"\n--- KLUCZOWE METRYKI (zmiana YoY) ---")

    for metric_name, data in key_metrics['metryki'].items():
        name = metric_name.replace('_', ' ').upper()
        value = data['wartosc']
        change = data['zmiana_yoy']
        status = data['status']

        if change != 0:
            print(f"{name:15}: {value:>12} ({change:+6.1f}%) {status}")
        else:
            print(f"{name:15}: {value:>12} {status}")

    # Marża operacyjna jeśli dostępna
    if key_metrics.get('marza_operacyjna'):
        margin_data = key_metrics['marza_operacyjna']
        current = margin_data.get('aktualna_procent', 0)
        change = margin_data.get('zmiana_pp', 0)
        print(f"{'MARŻA OPER.':15}: {current:>9.1f}% ({change:+6.1f} p.p.)")

    # Alerty
    if key_metrics['najwazniejsze_alerty']:
        print(f"\n🚨 NAJWAŻNIEJSZE ALERTY:")
        for alert in key_metrics['najwazniejsze_alerty']:
            print(f"   • {alert}")


# Przykład użycia - zaktualizowany
if __name__ == "__main__":
    monitor = FinancialReportsMonitor()

    print("=== TRYB 1: Alert o nowym raporcie ===")
    ticker = "CRM"
    result = monitor.get_earnings_alert(ticker)
    if result['new_report_detected']:
        key_metrics = monitor.get_key_metrics_summary(ticker)
        print_key_metrics(key_metrics)
    else:
        print(result['message'])

    print("\n" + "=" * 60)
    print("=== TRYB 2: Wymuszona analiza kluczowych metryk ===")
    key_metrics = monitor.get_key_metrics_summary(ticker)
    print_key_metrics(key_metrics)