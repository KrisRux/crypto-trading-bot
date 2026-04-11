"""
News Sentiment Service — RSS feed ingestion + VADER sentiment analysis.

Fetches headlines from free crypto news RSS feeds, scores them with
VADER (rule-based, zero GPU, instant), and provides an aggregate
sentiment signal for the tuning advisor.

Sentiment scale: -1.0 (very bearish) to +1.0 (very bullish).
Aggregate: weighted average of last N hours of headlines.

Setup:
  pip install feedparser nltk
  # VADER lexicon is downloaded automatically on first use.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Free RSS feeds (no API key needed) ──
DEFAULT_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cryptonews.com/news/feed/",
    "https://decrypt.co/feed",
]

# Keywords that boost relevance for crypto trading
CRYPTO_KEYWORDS = {
    "bitcoin", "btc", "ethereum", "eth", "crypto", "binance",
    "sec", "etf", "regulation", "ban", "hack", "crash", "rally",
    "fed", "rate", "inflation", "solana", "sol", "xrp", "bnb",
}

# VADER initialization (lazy — only when first needed)
_vader = None


def _get_vader():
    """Lazy-load VADER sentiment analyzer."""
    global _vader
    if _vader is not None:
        return _vader
    try:
        import nltk
        try:
            from nltk.sentiment.vader import SentimentIntensityAnalyzer
            _vader = SentimentIntensityAnalyzer()
        except LookupError:
            nltk.download("vader_lexicon", quiet=True)
            from nltk.sentiment.vader import SentimentIntensityAnalyzer
            _vader = SentimentIntensityAnalyzer()
        logger.info("NEWS_SENTIMENT: VADER initialized")
        return _vader
    except ImportError:
        logger.warning("NEWS_SENTIMENT: nltk not installed, sentiment disabled")
        return None


@dataclass
class Headline:
    title: str
    source: str
    published: datetime
    url: str
    sentiment: float = 0.0       # -1.0 to +1.0 (VADER compound)
    relevance: float = 0.0       # 0.0 to 1.0 (keyword match score)


@dataclass
class SentimentSnapshot:
    """Aggregate sentiment from recent headlines."""
    score: float = 0.0           # -1.0 to +1.0 weighted average
    headline_count: int = 0
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    top_headlines: list[dict] = field(default_factory=list)
    last_updated: str = ""
    available: bool = False

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 3),
            "headline_count": self.headline_count,
            "bullish_count": self.bullish_count,
            "bearish_count": self.bearish_count,
            "neutral_count": self.neutral_count,
            "label": "bullish" if self.score > 0.1 else "bearish" if self.score < -0.1 else "neutral",
            "top_headlines": self.top_headlines[:5],
            "last_updated": self.last_updated,
            "available": self.available,
        }


class NewsSentimentService:
    """Fetches and scores crypto news headlines."""

    def __init__(self, feeds: list[str] | None = None, max_age_hours: int = 6):
        self._feeds = feeds or DEFAULT_FEEDS
        self._max_age_hours = max_age_hours
        self._headlines: list[Headline] = []
        self._snapshot: SentimentSnapshot = SentimentSnapshot()
        self._last_fetch: datetime | None = None

    @property
    def snapshot(self) -> SentimentSnapshot:
        return self._snapshot

    async def fetch_and_score(self) -> SentimentSnapshot:
        """Fetch RSS feeds, score headlines, compute aggregate."""
        try:
            import feedparser
        except ImportError:
            logger.warning("NEWS_SENTIMENT: feedparser not installed")
            self._snapshot = SentimentSnapshot(available=False)
            return self._snapshot

        vader = _get_vader()
        if vader is None:
            self._snapshot = SentimentSnapshot(available=False)
            return self._snapshot

        new_headlines: list[Headline] = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._max_age_hours)

        for feed_url in self._feeds:
            try:
                # feedparser is sync but fast — RSS is small
                feed = feedparser.parse(feed_url)
                source = feed.feed.get("title", feed_url)[:30]
                for entry in feed.entries[:20]:  # max 20 per feed
                    title = entry.get("title", "").strip()
                    if not title:
                        continue

                    # Parse published date
                    published = self._parse_date(entry)
                    if published and published < cutoff:
                        continue

                    # Score with VADER
                    scores = vader.polarity_scores(title)
                    sentiment = scores["compound"]

                    # Relevance: how many crypto keywords appear
                    title_lower = title.lower()
                    keyword_hits = sum(1 for k in CRYPTO_KEYWORDS if k in title_lower)
                    relevance = min(keyword_hits / 3.0, 1.0)  # normalize to 0-1

                    new_headlines.append(Headline(
                        title=title,
                        source=source,
                        published=published or datetime.now(timezone.utc),
                        url=entry.get("link", ""),
                        sentiment=sentiment,
                        relevance=relevance,
                    ))
            except Exception:
                logger.debug("NEWS_SENTIMENT: failed to fetch %s", feed_url)

        # Merge and deduplicate by title similarity
        seen_titles = set()
        unique = []
        for h in new_headlines:
            key = re.sub(r'\W+', '', h.title.lower())[:50]
            if key not in seen_titles:
                seen_titles.add(key)
                unique.append(h)
        self._headlines = unique

        # Compute aggregate
        self._snapshot = self._compute_aggregate()
        self._last_fetch = datetime.now(timezone.utc)

        logger.info(
            "NEWS_SENTIMENT: %d headlines | score=%.3f (%s) | bull=%d bear=%d neutral=%d",
            self._snapshot.headline_count, self._snapshot.score,
            self._snapshot.to_dict()["label"],
            self._snapshot.bullish_count, self._snapshot.bearish_count,
            self._snapshot.neutral_count,
        )
        return self._snapshot

    def _compute_aggregate(self) -> SentimentSnapshot:
        """Weighted average sentiment — more relevant headlines weigh more."""
        if not self._headlines:
            return SentimentSnapshot(available=True, last_updated=datetime.now(timezone.utc).isoformat())

        total_weight = 0.0
        weighted_sum = 0.0
        bullish = bearish = neutral = 0

        for h in self._headlines:
            weight = 0.5 + h.relevance  # 0.5 base + 0-1 relevance
            weighted_sum += h.sentiment * weight
            total_weight += weight
            if h.sentiment > 0.1:
                bullish += 1
            elif h.sentiment < -0.1:
                bearish += 1
            else:
                neutral += 1

        avg_score = weighted_sum / total_weight if total_weight > 0 else 0.0

        # Top headlines sorted by abs(sentiment) * relevance
        sorted_headlines = sorted(
            self._headlines,
            key=lambda h: abs(h.sentiment) * (0.5 + h.relevance),
            reverse=True,
        )
        top = [
            {"title": h.title, "sentiment": round(h.sentiment, 2), "source": h.source}
            for h in sorted_headlines[:5]
        ]

        return SentimentSnapshot(
            score=avg_score,
            headline_count=len(self._headlines),
            bullish_count=bullish,
            bearish_count=bearish,
            neutral_count=neutral,
            top_headlines=top,
            last_updated=datetime.now(timezone.utc).isoformat(),
            available=True,
        )

    def _parse_date(self, entry) -> Optional[datetime]:
        """Try to parse published date from feed entry."""
        for attr in ("published_parsed", "updated_parsed"):
            parsed = getattr(entry, attr, None)
            if parsed:
                try:
                    from time import mktime
                    return datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
                except Exception:
                    pass
        return None

    def needs_refresh(self, interval_minutes: int = 30) -> bool:
        """Check if we should fetch again."""
        if self._last_fetch is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self._last_fetch).total_seconds()
        return elapsed >= interval_minutes * 60
