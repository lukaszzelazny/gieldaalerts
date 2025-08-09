import re
import pandas as pd
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch
from textblob import TextBlob
import openai
from typing import List, Dict, Tuple
import numpy as np

class EnhancedPolishFinancialSentiment:
    def __init__(self):
        """
        Inicjalizacja z wieloma metodami analizy sentymentu
        """
        # Słowniki specjalistyczne
        self.positive_finance_terms = {
            'wzrost', 'zysk', 'sukces', 'rekorд', 'boom', 'rally', 'mooning', 
            'to the moon', 'pompuje', 'leci w górę', 'breakout', 'buy', 'kupuję',
            'hold', 'trzymam', 'bullish', 'byczo', 'plus', 'green', 'zielono',
            'ath', 'all time high', 'robi robotę', 'git', 'spoko', 'świetnie'
        }
        
        self.negative_finance_terms = {
            'spadek', 'strata', 'crash', 'dump', 'dip', 'bear', 'niedźwiedzi',
            'czerwono', 'red', 'sell', 'sprzedam', 'klapa', 'porażka', 'shit',
            'leci w dół', 'jak kamień', 'breakdown', 'resistance', 'bubble',
            'bańka', 'overvalued', 'przewartościowane', 'słabo', 'kiepsko', 'dramat'
        }
        
        self.neutral_finance_terms = {
            'consolidation', 'konsolidacja', 'sideways', 'boczny', 'wait and see',
            'czekam', 'obserwuję', 'analiza', 'technicals', 'fundamentals'
        }
        
        # Emotikony
        self.positive_emojis = {'😊', '😄', '🚀', '📈', '💰', '💵', '🔥', '👍', '✅', '🎉'}
        self.negative_emojis = {'😢', '😭', '📉', '💸', '😰', '😱', '👎', '❌', '💀', '😞'}
        
        # Wagi dla różnych metod
        self.method_weights = {
            'herbert': 0.4,      # Najwyższa waga - specjalizowany model
            'dictionary': 0.25,   # Słownik finansowy
            'emoji': 0.15,       # Emotikony
            'textblob': 0.1,     # Backup dla podstawowej analizy
            'context': 0.1       # Analiza kontekstu
        }
        
        # Próba załadowania HerBERT (jeśli dostępny)
        try:
            self.herbert_tokenizer = AutoTokenizer.from_pretrained("allegro/herbert-base-cased")
            self.herbert_model = AutoModelForSequenceClassification.from_pretrained(
                "allegro/herbert-base-cased", num_labels=3
            )
            self.use_herbert = True
            print("HerBERT model loaded successfully")
        except:
            self.use_herbert = False
            print("HerBERT not available, using alternative methods")
    
    def clean_financial_text(self, text: str) -> str:
        """Wyspecjalizowane czyszczenie tekstu finansowego"""
        # Zachowaj ważne symbole
        text = re.sub(r'http\S+|www\S+|https\S+', '', text, flags=re.MULTILINE)
        
        # Nie usuwaj całkowicie hashtagów - mogą zawierać sentiment
        text = re.sub(r'#(\w+)', r'\1', text)  # Zostaw samo słowo
        text = re.sub(r'@\w+', '', text)  # Usuń mentions
        
        # Zachowaj emotiokny i podstawowe znaki interpunkcyjne
        text = re.sub(r'[^\w\s\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF!?.,]', ' ', text)
        
        return text.strip().lower()
    
    def herbert_sentiment(self, text: str) -> Tuple[str, float]:
        """Analiza sentymentu za pomocą HerBERT"""
        if not self.use_herbert:
            return 'neutral', 0.0
            
        try:
            inputs = self.herbert_tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
            
            with torch.no_grad():
                outputs = self.herbert_model(**inputs)
                predictions = torch.nn.functional.softmax(outputs.logits, dim=-1)
                
            # Assuming labels: [negative, neutral, positive]
            confidence = torch.max(predictions).item()
            predicted_class = torch.argmax(predictions).item()
            
            sentiment_map = {0: 'negative', 1: 'neutral', 2: 'positive'}
            return sentiment_map[predicted_class], confidence
            
        except Exception as e:
            print(f"HerBERT error: {e}")
            return 'neutral', 0.0
    
    def dictionary_sentiment(self, text: str) -> Tuple[str, float]:
        """Analiza na podstawie słownika finansowego"""
        words = set(text.lower().split())
        
        positive_score = len(words.intersection(self.positive_finance_terms))
        negative_score = len(words.intersection(self.negative_finance_terms))
        neutral_score = len(words.intersection(self.neutral_finance_terms))
        
        total_score = positive_score + negative_score + neutral_score
        
        if total_score == 0:
            return 'neutral', 0.0
            
        if positive_score > negative_score and positive_score > neutral_score:
            confidence = positive_score / (total_score + len(words.split())) * 2
            return 'positive', min(confidence, 1.0)
        elif negative_score > positive_score and negative_score > neutral_score:
            confidence = negative_score / (total_score + len(words.split())) * 2
            return 'negative', min(confidence, 1.0)
        else:
            return 'neutral', 0.3
    
    def emoji_sentiment(self, text: str) -> Tuple[str, float]:
        """Analiza na podstawie emotikon"""
        positive_count = sum(1 for emoji in self.positive_emojis if emoji in text)
        negative_count = sum(1 for emoji in self.negative_emojis if emoji in text)
        
        total_emojis = positive_count + negative_count
        
        if total_emojis == 0:
            return 'neutral', 0.0
            
        if positive_count > negative_count:
            return 'positive', min(positive_count / max(total_emojis, 1), 1.0)
        elif negative_count > positive_count:
            return 'negative', min(negative_count / max(total_emojis, 1), 1.0)
        else:
            return 'neutral', 0.3
    
    def context_sentiment(self, text: str) -> Tuple[str, float]:
        """Analiza kontekstu (liczby, znaki interpunkcyjne)"""
        # Szukaj procentów - ujemne vs dodatnie
        percentages = re.findall(r'[+-]?\d+[.,]?\d*%', text)
        numbers = re.findall(r'[+-]\d+[.,]?\d*', text)
        
        positive_indicators = text.count('!') + text.count('🚀') + text.count('💰')
        negative_indicators = text.count('😢') + text.count('💸')
        
        score = 0
        confidence = 0.2
        
        # Analiza liczb
        for num in numbers + percentages:
            clean_num = re.sub(r'[%,]', '', num)
            try:
                val = float(clean_num)
                if val > 0:
                    score += 0.3
                elif val < 0:
                    score -= 0.3
            except:
                pass
        
        # Wskaźniki emocjonalne
        score += (positive_indicators - negative_indicators) * 0.2
        
        if score > 0.1:
            return 'positive', min(confidence + abs(score), 1.0)
        elif score < -0.1:
            return 'negative', min(confidence + abs(score), 1.0)
        else:
            return 'neutral', confidence
    
    def textblob_sentiment(self, text: str) -> Tuple[str, float]:
        """Fallback TextBlob sentiment"""
        blob = TextBlob(text)
        polarity = blob.sentiment.polarity
        
        if polarity > 0.1:
            return 'positive', abs(polarity)
        elif polarity < -0.1:
            return 'negative', abs(polarity)
        else:
            return 'neutral', 0.3
    
    def analyze_comprehensive_sentiment(self, text: str) -> Dict:
        """Kompleksowa analiza łącząca wszystkie metody"""
        clean_text = self.clean_financial_text(text)
        
        # Analiza różnymi metodami
        methods = {
            'herbert': self.herbert_sentiment(clean_text),
            'dictionary': self.dictionary_sentiment(clean_text),
            'emoji': self.emoji_sentiment(text),  # Oryginalny tekst dla emoji
            'context': self.context_sentiment(text),
            'textblob': self.textblob_sentiment(clean_text)
        }
        
        # Oblicz ważoną średnią
        sentiment_scores = {'positive': 0, 'negative': 0, 'neutral': 0}
        total_confidence = 0
        
        for method, (sentiment, confidence) in methods.items():
            weight = self.method_weights[method]
            weighted_confidence = confidence * weight
            sentiment_scores[sentiment] += weighted_confidence
            total_confidence += weight
        
        # Normalizacja
        for key in sentiment_scores:
            sentiment_scores[key] /= total_confidence
        
        # Wybierz dominujący sentiment
        final_sentiment = max(sentiment_scores, key=sentiment_scores.get)
        final_confidence = sentiment_scores[final_sentiment]
        
        return {
            'sentiment': final_sentiment,
            'confidence': final_confidence,
            'scores': sentiment_scores,
            'methods': methods,
            'cleaned_text': clean_text
        }
    
    def analyze_tweet_batch(self, tweets: List[str]) -> pd.DataFrame:
        """Analiza batch tweetów"""
        results = []
        
        for i, tweet in enumerate(tweets):
            if i % 50 == 0:
                print(f"Processing tweet {i+1}/{len(tweets)}")
                
            analysis = self.analyze_comprehensive_sentiment(tweet)
            
            results.append({
                'original_text': tweet,
                'cleaned_text': analysis['cleaned_text'],
                'sentiment': analysis['sentiment'],
                'confidence': analysis['confidence'],
                'positive_score': analysis['scores']['positive'],
                'negative_score': analysis['scores']['negative'],
                'neutral_score': analysis['scores']['neutral'],
                'herbert_sentiment': analysis['methods']['herbert'][0] if 'herbert' in analysis['methods'] else None,
                'dictionary_sentiment': analysis['methods']['dictionary'][0],
                'emoji_sentiment': analysis['methods']['emoji'][0]
            })
        
        return pd.DataFrame(results)

# Przykład użycia i testowania
def test_sentiment_analyzer():
    """Test analizatora na przykładowych tweetach finansowych"""
    analyzer = EnhancedPolishFinancialSentiment()
    
    test_tweets = [
        "KGHM leci w dół jak kamień 😢 Portfel czerwony jak diabli",  # Negatywny
        "PKN Orlen pompuje dzisiaj! 🚀 +5% w górę, to dopiero początek! 📈",  # Pozytywny
        "CCC konsoliduje się w okolicy 50zł. Czekam na sygnał",  # Neutralny
        "Allegro to totalna klapa... -15% w tym miesiącu 💸",  # Negatywny
        "Świetnie, kolejny -10% w portfelu 😅 #ironia",  # Negatywny (ironia)
        "Kupuję dipa na CDPROJEKT. Będzie moonshot! 💰🚀",  # Pozytywny
    ]
    
    print("=== TEST ANALIZATORA SENTYMENTU ===\n")
    
    for tweet in test_tweets:
        result = analyzer.analyze_comprehensive_sentiment(tweet)
        
        print(f"Tweet: {tweet}")
        print(f"Sentiment: {result['sentiment']} (confidence: {result['confidence']:.3f})")
        print(f"Scores: P:{result['scores']['positive']:.3f} N:{result['scores']['negative']:.3f} Ne:{result['scores']['neutral']:.3f}")
        print("---")
    
    return analyzer

# Uruchom test
if __name__ == "__main__":
    # pip install transformers torch textblob pandas
    test_analyzer = test_sentiment_analyzer()
