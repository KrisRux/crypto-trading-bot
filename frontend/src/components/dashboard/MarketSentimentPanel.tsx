import { NewsSentiment } from '../../api'
import { useLang } from '../../hooks/useLang'
import { sentimentColor, sentimentBadgeClass } from '../../utils/colors'

interface Props {
  sentiment: NewsSentiment
}

export default function MarketSentimentPanel({ sentiment }: Props) {
  const { l } = useLang()

  if (!sentiment.available) return null

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-gray-400">Market Sentiment</h3>
          <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${sentimentBadgeClass(sentiment.score)}`}>
            {sentiment.label}
          </span>
        </div>
        <span className="text-[10px] text-gray-600">{sentiment.headline_count} {l('notizie', 'headlines')}</span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
        <div className="bg-gray-800/50 rounded px-3 py-2">
          <p className="text-[10px] text-gray-500 uppercase">{l('Punteggio', 'Score')}</p>
          <p className={`text-lg font-bold ${sentimentColor(sentiment.score)}`}>
            {sentiment.score > 0 ? '+' : ''}{sentiment.score.toFixed(2)}
          </p>
        </div>
        {sentiment.fear_greed_label && (
          <div className="bg-gray-800/50 rounded px-3 py-2">
            <p className="text-[10px] text-gray-500 uppercase">Fear & Greed</p>
            <p className={`text-lg font-bold ${
              sentiment.fear_greed_value >= 60 ? 'text-emerald-400' :
              sentiment.fear_greed_value <= 40 ? 'text-red-400' : 'text-yellow-400'
            }`}>
              {sentiment.fear_greed_value}
            </p>
            <p className="text-[10px] text-gray-500">{sentiment.fear_greed_label}</p>
          </div>
        )}
        <div className="bg-gray-800/50 rounded px-3 py-2">
          <p className="text-[10px] text-gray-500 uppercase">Bull / Bear</p>
          <p className="text-sm font-bold text-gray-300">
            <span className="text-emerald-400">{sentiment.bullish_count}</span>
            {' / '}
            <span className="text-red-400">{sentiment.bearish_count}</span>
          </p>
        </div>
        <div className="bg-gray-800/50 rounded px-3 py-2">
          <p className="text-[10px] text-gray-500 uppercase">{l('Notizie', 'News')}</p>
          <p className="text-sm font-bold text-gray-300">{sentiment.headline_count}</p>
          <p className="text-[10px] text-gray-500">{sentiment.neutral_count} neutral</p>
        </div>
      </div>
      {sentiment.top_headlines?.length > 0 && (
        <div className="space-y-0.5">
          {sentiment.top_headlines.slice(0, 3).map((h, i) => (
            <div key={i} className="flex items-center gap-2 text-xs py-0.5">
              <span className={`w-10 text-right font-mono text-[11px] shrink-0 ${sentimentColor(h.sentiment)}`}>
                {h.sentiment > 0 ? '+' : ''}{h.sentiment.toFixed(2)}
              </span>
              <span className="text-gray-400 truncate">{h.title}</span>
              <span className="text-[9px] text-gray-600 shrink-0">{h.source}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
