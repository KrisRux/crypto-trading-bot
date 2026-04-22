import { useEffect, useRef, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'

export interface NavGroupItem {
  to: string
  label: string
  badge?: number
}

interface Props {
  label: string
  items: NavGroupItem[]
  mobile: boolean
  onNavigate?: () => void
}

export default function NavGroup({ label, items, mobile, onNavigate }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const location = useLocation()

  const badgeTotal = items.reduce((sum, it) => sum + (it.badge || 0), 0)
  const isActive = items.some(it => location.pathname === it.to)

  // Close on route change (desktop dropdown only — mobile accordion stays open-by-choice)
  useEffect(() => {
    if (!mobile) setOpen(false)
  }, [location.pathname, mobile])

  // Close on click-outside and Escape (desktop only)
  useEffect(() => {
    if (mobile || !open) return
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', handleClick)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('mousedown', handleClick)
      document.removeEventListener('keydown', handleKey)
    }
  }, [open, mobile])

  const buttonBase = mobile
    ? 'flex items-center justify-between w-full px-3 py-2.5 rounded-md text-sm font-medium transition-colors'
    : 'flex items-center gap-1 px-3 py-2 rounded-md text-sm font-medium transition-colors'
  const buttonState = isActive || open
    ? 'bg-gray-700 text-white'
    : 'text-gray-300 hover:bg-gray-700 hover:text-white'

  const linkClass = ({ isActive: active }: { isActive: boolean }) =>
    `${mobile ? 'block w-full px-4 py-2 text-sm' : 'block px-3 py-2 text-sm'} rounded-md transition-colors ${
      active ? 'bg-gray-700 text-white' : 'text-gray-300 hover:bg-gray-700 hover:text-white'
    }`

  return (
    <div ref={ref} className={mobile ? '' : 'relative'}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        aria-haspopup="menu"
        className={`${buttonBase} ${buttonState}`}
      >
        <span className="flex items-center gap-1.5">
          {label}
          {badgeTotal > 0 && (
            <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-900/70 text-amber-200 border border-amber-800/50">
              {badgeTotal}
            </span>
          )}
        </span>
        <svg
          className={`w-3.5 h-3.5 transition-transform ${open ? 'rotate-180' : ''} ${mobile ? '' : 'ml-0.5'}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div
          role="menu"
          className={
            mobile
              ? 'mt-1 ml-2 pl-2 border-l border-gray-800 space-y-0.5'
              : 'absolute left-0 mt-1 min-w-[180px] bg-gray-900 border border-gray-800 rounded-md shadow-lg py-1 z-50'
          }
        >
          {items.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              className={linkClass}
              onClick={() => {
                if (mobile) onNavigate?.()
                else setOpen(false)
              }}
            >
              <span className="flex items-center justify-between gap-2">
                <span>{item.label}</span>
                {item.badge && item.badge > 0 ? (
                  <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-900/70 text-amber-200 border border-amber-800/50">
                    {item.badge}
                  </span>
                ) : null}
              </span>
            </NavLink>
          ))}
        </div>
      )}
    </div>
  )
}
