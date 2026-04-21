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
    "https://bitcoinmagazine.com/feed",
    "https://thedefiant.io/feed",
    "https://www.binance.com/en/feed/rss",
]

# Fear & Greed Index (free, no auth)
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"

# Keywords that boost relevance for crypto trading
CRYPTO_KEYWORDS = {
    "bitcoin", "btc", "ethereum", "eth", "crypto", "binance",
    "sec", "etf", "regulation", "ban", "hack", "crash", "rally",
    "fed", "rate", "inflation", "solana", "sol", "xrp", "bnb",
    "defi", "nft", "stablecoin", "tether", "usdt", "mining",
    "halving", "whale", "liquidation", "blackrock", "grayscale", "altcoin",
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
    """Aggregate sentiment from recent headlines + Fear & Greed Index."""
    score: float = 0.0           # -1.0 to +1.0 composite score
    headline_score: float = 0.0  # -1.0 to +1.0 from VADER only
    headline_count: int = 0
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    fear_greed_value: int = 50   # 0 (extreme fear) to 100 (extreme greed)
    fear_greed_label: str = ""   # "Extreme Fear"/"Fear"/"Neutral"/"Greed"/"Extreme Greed"
    top_headlines: list[dict] = field(default_factory=list)
    last_updated: str = ""
    available: bool = False

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 3),
            "headline_score": round(self.headline_score, 3),
            "headline_count": self.headline_count,
            "bullish_count": self.bullish_count,
            "bearish_count": self.bearish_count,
            "neutral_count": self.neutral_count,
            "fear_greed_value": self.fear_greed_value,
            "fear_greed_label": self.fear_greed_label,
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
        failed_feeds = 0
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
                failed_feeds += 1
                logger.debug("NEWS_SENTIMENT: failed to fetch %s", feed_url)

        # If most feeds failed, mark as unavailable
        if failed_feeds >= len(self._feeds) * 0.8:
            logger.warning("NEWS_SENTIMENT: %d/%d feeds failed, marking unavailable", failed_feeds, len(self._feeds))
            self._snapshot = SentimentSnapshot(available=False, last_updated=datetime.now(timezone.utc).isoformat())
            self._last_fetch = datetime.now(timezone.utc)
            return self._snapshot

        # Merge and deduplicate by title similarity
        seen_titles = set()
        unique = []
        for h in new_headlines:
            key = re.sub(r'\W+', '', h.title.lower())[:50]
            if key not in seen_titles:
                seen_titles.add(key)
                unique.append(h)
        self._headlines = unique

        # Fetch Fear & Greed Index
        fg_value, fg_label = await self._fetch_fear_greed()

        # Compute aggregate
        self._snapshot = self._compute_aggregate(fg_value, fg_label)
        self._last_fetch = datetime.now(timezone.utc)

        logger.info(
            "NEWS_SENTIMENT: %d headlines | score=%.3f (%s) | bull=%d bear=%d neutral=%d | F&G=%d (%s)",
            self._snapshot.headline_count, self._snapshot.score,
            self._snapshot.to_dict()["label"],
            self._snapshot.bullish_count, self._snapshot.bearish_count,
            self._snapshot.neutral_count, fg_value, fg_label,
        )
        return self._snapshot

    def _compute_aggregate(self, fg_value: int = 50, fg_label: str = "") -> SentimentSnapshot:
        """
        Composite sentiment: 70% VADER headline average + 30% Fear & Greed Index.
        """
        now_str = datetime.now(timezone.utc).isoformat()
        if not self._headlines:
            # No headlines — use Fear & Greed only if available
            fg_score = (fg_value - 50) / 50  # normalize 0-100 → -1.0 to +1.0
            return SentimentSnapshot(
                score=fg_score if fg_label else 0.0,
                headline_score=0.0,
                fear_greed_value=fg_value, fear_greed_label=fg_label,
                last_updated=now_str, available=bool(fg_label),
            )

        total_weight = 0.0
        weighted_sum = 0.0
        bullish = bearish = neutral = 0

        for h in self._headlines:
            weight = 0.5 + h.relevance
            weighted_sum += h.sentiment * weight
            total_weight += weight
            if h.sentiment > 0.1:
                bullish += 1
            elif h.sentiment < -0.1:
                bearish += 1
            else:
                neutral += 1

        headline_avg = weighted_sum / total_weight if total_weight > 0 else 0.0

        # Require a minimum number of headlines before trusting the sentiment.
        # With too few samples the VADER average is noisy and biases the
        # composite score — force neutral when below threshold.
        MIN_HEADLINES_FOR_SCORE = 5
        if len(self._headlines) < MIN_HEADLINES_FOR_SCORE:
            logger.warning(
                "NEWS_SENTIMENT: insufficient headlines (%d<%d) → score forced to neutral",
                len(self._headlines), MIN_HEADLINES_FOR_SCORE,
            )
            headline_avg = 0.0

        # Composite: 70% headlines + 30% Fear & Greed (normalized to -1..+1)
        fg_normalized = (fg_value - 50) / 50 if fg_label else headline_avg
        composite = headline_avg * 0.7 + fg_normalized * 0.3

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
            score=composite,
            headline_score=headline_avg,
            headline_count=len(self._headlines),
            bullish_count=bullish,
            bearish_count=bearish,
            neutral_count=neutral,
            fear_greed_value=fg_value,
            fear_greed_label=fg_label,
            top_headlines=top,
            last_updated=now_str,
            available=True,
        )

    async def _fetch_fear_greed(self) -> tuple[int, str]:
        """Fetch the Fear & Greed Index. Returns (value 0-100, label) or (50, '') on failure."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(FEAR_GREED_URL)
                if resp.status_code == 200:
                    data = resp.json()
                    entry = data.get("data", [{}])[0]
                    value = int(entry.get("value", 50))
                    label = entry.get("value_classification", "")
                    logger.debug("NEWS_SENTIMENT: Fear & Greed = %d (%s)", value, label)
                    return value, label
        except Exception:
            logger.debug("NEWS_SENTIMENT: Fear & Greed fetch failed")
        return 50, ""

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
