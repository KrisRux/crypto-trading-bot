/** Animated shimmer placeholder for a stat card. */
export function SkeletonCard() {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 animate-pulse">
      <div className="h-3 bg-gray-800 rounded w-2/3 mb-3" />
      <div className="h-7 bg-gray-800 rounded w-1/2 mb-2" />
      <div className="h-2.5 bg-gray-800 rounded w-1/3" />
    </div>
  )
}

/** Animated shimmer placeholder for a table row. */
export function SkeletonRow({ cols = 5 }: { cols?: number }) {
  return (
    <tr className="border-b border-gray-800/50">
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="py-3 px-3">
          <div className="h-3 bg-gray-800 rounded animate-pulse" style={{ width: `${60 + (i % 3) * 15}%` }} />
        </td>
      ))}
    </tr>
  )
}
