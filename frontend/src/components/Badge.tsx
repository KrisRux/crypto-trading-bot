interface BadgeProps {
  children: React.ReactNode
  variant?: 'success' | 'danger' | 'warning' | 'info' | 'neutral'
  size?: 'sm' | 'xs'
  className?: string
}

const variantClasses: Record<NonNullable<BadgeProps['variant']>, string> = {
  success: 'bg-emerald-900/50 text-emerald-300 border border-emerald-800/50',
  danger:  'bg-red-900/50 text-red-300 border border-red-800/50',
  warning: 'bg-yellow-900/50 text-yellow-300 border border-yellow-800/50',
  info:    'bg-blue-900/50 text-blue-300 border border-blue-800/50',
  neutral: 'bg-gray-800 text-gray-300 border border-gray-700',
}

export default function Badge({ children, variant = 'neutral', size = 'sm', className = '' }: BadgeProps) {
  const sizeClass = size === 'xs' ? 'text-[10px] px-1.5 py-0.5' : 'text-xs px-2.5 py-1'
  return (
    <span className={`inline-flex items-center rounded-full font-bold uppercase tracking-wider ${sizeClass} ${variantClasses[variant]} ${className}`}>
      {children}
    </span>
  )
}
