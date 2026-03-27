import { useEffect, useRef, useCallback } from 'react'

/**
 * Logs the user out after a period of inactivity.
 * Resets the timer on mouse, keyboard, touch, and scroll events.
 */
export function useIdleTimeout(
  timeoutMinutes: number,
  onTimeout: () => void,
  enabled: boolean = true,
) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const resetTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
    }
    if (enabled && timeoutMinutes > 0) {
      timerRef.current = setTimeout(onTimeout, timeoutMinutes * 60 * 1000)
    }
  }, [timeoutMinutes, onTimeout, enabled])

  useEffect(() => {
    if (!enabled || timeoutMinutes <= 0) return

    const events = ['mousedown', 'mousemove', 'keydown', 'touchstart', 'scroll', 'click']
    events.forEach((e) => window.addEventListener(e, resetTimer, { passive: true }))

    // Start the initial timer
    resetTimer()

    return () => {
      events.forEach((e) => window.removeEventListener(e, resetTimer))
      if (timerRef.current) {
        clearTimeout(timerRef.current)
      }
    }
  }, [resetTimer, enabled, timeoutMinutes])
}
