import { useEffect, useState, useCallback } from 'react'

/**
 * Poll an async function at a given interval.
 * Returns [data, loading, error, refetch].
 */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs: number = 5000,
): [T | null, boolean, string | null, () => void] {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refetch = useCallback(() => {
    fetcher()
      .then((d) => {
        setData(d)
        setError(null)
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false))
  }, [fetcher])

  useEffect(() => {
    refetch()
    const id = setInterval(refetch, intervalMs)
    return () => clearInterval(id)
  }, [refetch, intervalMs])

  return [data, loading, error, refetch]
}
