/**
 * API client for communicating with the FastAPI backend.
 */

const BASE = '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`API error ${res.status}: ${text}`)
  }
  // Handle CSV / plain text responses
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('text/csv') || ct.includes('text/plain')) {
    return (await res.text()) as unknown as T
  }
  return res.json()
}

// -- Types --
export interface Balance {
  mode: string
  cash_balance: number
  total_equity: number
  total_pnl: number
  total_trades: number
  winning_trades: number
  losing_trades: number
}

export interface Position {
  id: number
  symbol: string
  side: string
  quantity: number
  entry_price: number
  current_price: number | null
  unrealized_pnl: number
  stop_loss: number | null
  take_profit: number | null
  opened_at: string | null
}

export interface OrderItem {
  id: number
  symbol: string
  side: string
  order_type: string
  quantity: number
  price: number | null
  filled_price: number | null
  status: string
  mode: string
  error_message: string | null
  created_at: string | null
}

export interface TradeItem {
  id: number
  symbol: string
  side: string
  entry_price: number
  exit_price: number | null
  quantity: number
  stop_loss: number | null
  take_profit: number | null
  pnl: number | null
  pnl_pct: number | null
  status: string
  mode: string
  strategy: string | null
  opened_at: string | null
  closed_at: string | null
}

export interface StrategyInfo {
  name: string
  enabled: boolean
  params: Record<string, unknown>
}

export interface RiskParams {
  max_position_pct: number
  default_sl_pct: number
  default_tp_pct: number
}

export interface SignalItem {
  time: string
  type: string
  symbol: string
  price: number
  strategy: string
  reason: string
}

export interface EngineStatus {
  running: boolean
  mode: string
  symbols: string[]
  last_prices: Record<string, number>
  strategies_count: number
}

export interface PriceData {
  symbol: string
  price: number
}

export interface SkillItem {
  name: string
  description: string
  category: string
  version: string
  author: string
  body: string
  key_rules: string[]
}

export interface SkillsSummary {
  total_skills: number
  categories: Record<string, number>
}

// -- API calls --
export const api = {
  getMode: () => request<{ mode: string }>('/mode'),
  switchMode: (mode: string) =>
    request<{ mode: string }>('/mode', {
      method: 'POST',
      body: JSON.stringify({ mode }),
    }),
  getBalance: () => request<Balance>('/balance'),
  getPositions: () => request<Position[]>('/positions'),
  getOrders: () => request<OrderItem[]>('/orders'),
  getTrades: () => request<TradeItem[]>('/trades'),
  getPrice: (symbol: string) => request<PriceData>(`/price/${symbol}`),
  getStrategies: () => request<StrategyInfo[]>('/strategies'),
  updateStrategy: (data: { name: string; enabled?: boolean; params?: Record<string, unknown> }) =>
    request('/strategies', { method: 'PUT', body: JSON.stringify(data) }),
  getRisk: () => request<RiskParams>('/risk'),
  updateRisk: (params: RiskParams) =>
    request<RiskParams>('/risk', { method: 'PUT', body: JSON.stringify(params) }),
  getSignals: () => request<SignalItem[]>('/signals'),
  getEngineStatus: () => request<EngineStatus>('/engine/status'),
  resetPaperPortfolio: () => request('/paper/reset', { method: 'POST' }),
  exportTrades: () => request<string>('/paper/export'),
  addSymbol: (symbol: string) =>
    request<{ symbols: string[] }>('/symbols/add', {
      method: 'POST', body: JSON.stringify({ symbol }),
    }),
  removeSymbol: (symbol: string) =>
    request<{ symbols: string[] }>('/symbols/remove', {
      method: 'POST', body: JSON.stringify({ symbol }),
    }),
  getSkillsSummary: () => request<SkillsSummary>('/skills/summary'),
  getSkills: (category?: string) =>
    request<SkillItem[]>(category ? `/skills?category=${category}` : '/skills'),
  getSkill: (name: string) => request<SkillItem>(`/skills/${name}`),
}
