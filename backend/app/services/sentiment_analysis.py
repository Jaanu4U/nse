import requests
from bs4 import BeautifulSoup
import nltk
import logging
from sqlalchemy.orm import Session
from app.repositories.stock_repo import StockRepository
from app.models.models import News
import datetime
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)

# Ensure VADER lexicon is downloaded
try:
    nltk.data.find('sentiment/vader_lexicon.zip')
except LookupError:
    nltk.download('vader_lexicon', quiet=True)

from nltk.sentiment.vader import SentimentIntensityAnalyzer

class SentimentAnalysisEngine:
    def __init__(self, db: Session):
        self.db = db
        self.stock_repo = StockRepository(db)
        self.sia = SentimentIntensityAnalyzer()

    def fetch_and_analyze_news(self, symbol: str) -> int:
        """
        Fetch news for a symbol via Google News RSS and classify sentiment.
        """
        stock = self.stock_repo.get_by_symbol(symbol)
        if not stock:
            logger.error(f"Stock {symbol} not found in database.")
            return 0

        # RSS URL for Indian market news on this stock
        url = f"https://news.google.com/rss/search?q={symbol}+stock+india&hl=en-IN&gl=IN&ceid=IN:en"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        try:
            res = requests.get(url, headers=headers, timeout=10)
            res.raise_for_status()
            
            soup = BeautifulSoup(res.content, features="xml")
            items = soup.find_all("item")
            
            new_articles = 0
            for item in items[:15]:  # Process the top 15 news headlines
                title = item.title.text if item.title else ""
                link = item.link.text if item.link else ""
                pub_date_str = item.pubDate.text if item.pubDate else ""
                
                if not title or not link:
                    continue
                
                # Check if news article already exists
                exists = self.db.query(News).filter(News.url == link).first()
                if exists:
                    continue
                
                # Parse publish date
                try:
                    published_at = parsedate_to_datetime(pub_date_str)
                except Exception:
                    published_at = datetime.datetime.now(datetime.timezone.utc)

                # Analyze sentiment
                # VADER returns polarity_scores: {'neg': 0.0, 'neu': 1.0, 'pos': 0.0, 'compound': 0.0}
                scores = self.sia.polarity_scores(title)
                compound_score = scores['compound']
                
                # Classification thresholds
                if compound_score >= 0.05:
                    sentiment_class = "POSITIVE"
                elif compound_score <= -0.05:
                    sentiment_class = "NEGATIVE"
                else:
                    sentiment_class = "NEUTRAL"

                new_news = News(
                    stock_id=stock.id,
                    title=title,
                    url=link,
                    source=item.source.text if item.source else "Google News",
                    published_at=published_at,
                    sentiment_score=compound_score,
                    sentiment_class=sentiment_class
                )
                self.db.add(new_news)
                new_articles += 1
                
            if new_articles > 0:
                self.db.commit()
                logger.info(f"Analyzed and saved {new_articles} news items for {symbol}.")
                
            return new_articles
            
        except Exception as e:
            logger.error(f"Failed to fetch/analyze news for {symbol}: {e}")
            return 0

    def analyze_all_stocks(self) -> int:
        active_stocks = self.stock_repo.get_active_stocks()
        total = 0
        for stock in active_stocks:
            try:
                total += self.fetch_and_analyze_news(stock.symbol)
            except Exception as e:
                logger.error(f"News sentiment analysis failed for {stock.symbol}: {e}")
        return total
