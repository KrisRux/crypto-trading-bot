/** Returns Tailwind text class based on numeric PnL value. */
export function pnlColor(value: number): string {
  return value >= 0 ? 'text-emerald-400' : 'text-red-400'
}

/** Returns Tailwind classes for the active profile badge. */
export function profileBadgeClass(profile: string): string {
  if (profile === 'defensive') return 'bg-yellow-900/50 text-yellow-300 border border-yellow-800/50'
  if (profile === 'aggressive_trend') return 'bg-red-900/50 text-red-300 border border-red-800/50'
  return 'bg-blue-900/50 text-blue-300 border border-blue-800/50'
}

/** Returns Tailwind classes for the market regime badge. */
export function regimeBadgeClass(regime: string): string {
  if (regime === 'trend') return 'bg-emerald-900/50 text-emerald-300 border border-emerald-800/50'
  if (regime === 'volatile' || regime === 'defensive') return 'bg-red-900/50 text-red-300 border border-red-800/50'
  return 'bg-gray-800 text-gray-300 border border-gray-700'
}

/** Returns Tailwind text class for a sentiment score. */
export function sentimentColor(score: number): string {
  if (score > 0.1) return 'text-emerald-400'
  if (score < -0.1) return 'text-red-400'
  return 'text-gray-300'
}

/** Returns Tailwind classes for a sentiment label badge. */
export function sentimentBadgeClass(score: number): string {
  if (score > 0.1) return 'bg-emerald-900/60 text-emerald-300'
  if (score < -0.1) return 'bg-red-900/60 text-red-300'
  return 'bg-gray-800 text-gray-400'
}
