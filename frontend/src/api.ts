/**
 * API client for communicating with the FastAPI backend.
 *
 * Authentication uses an httpOnly cookie (auth_token) set by the server on
 * login. The cookie is sent automatically by the browser via
 * `credentials: 'include'` — it is never accessible to JavaScript, which
 * protects it from XSS attacks.
 */

const BASE = '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options?.headers as Record<string, string> || {}),
  }

  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers,
    credentials: 'include', // send httpOnly cookie automatically
  })

  if (res.status === 401) {
    // Notify the app that the session is expired/missing.
    // App.tsx listens for this event and sets isAuthenticated(false),
    // which lets React Router redirect to /login without a full page reload.
    window.dispatchEvent(new CustomEvent('auth:expired'))
    throw new Error('Session expired')
  }
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`API error ${res.status}: ${text}`)
  }
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('text/csv') || ct.includes('text/plain')) {
    return (await res.text()) as unknown as T
  }
  return res.json()
}

export interface LoginResponse {
  token_type: string
  expires_in: number
  session_timeout_minutes: number
  role: string
  display_name: string
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
  position_value_usdt: number
  unrealized_pnl: number
  unrealized_pnl_pct: number
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
  symbols: string[]
  last_prices: Record<string, number>
  strategies_count: number
}

export interface UserItem {
  id: number
  username: string
  display_name: string | null
  role: string
  is_active: boolean
  created_at: string | null
  last_login: string | null
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

export interface AssetItem {
  asset: string
  free: number
  locked: number
  total: number
  price_usdt: number
  value_usdt: number
}

export interface NewsSentiment {
  score: number
  headline_score: number
  headline_count: number
  bullish_count: number
  bearish_count: number
  neutral_count: number
  fear_greed_value: number
  fear_greed_label: string
  label: string
  top_headlines: { title: string; sentiment: number; source: string }[]
  last_updated: string
  available: boolean
}

export interface TuningSuggestionItem {
  id: number
  status: string
  created_at: string
  global_regime: string
  active_profile: string
  consecutive_losses: number
  win_rate: number
  drawdown: number
  trades_per_hour: number
  total_blocked: number
  total_passed: number
  changes: { path: string; from: unknown; to: unknown; reason: string }[]
  reasoning: string
  confidence: number
  risk_level: string
  resolved_at: string | null
  resolved_by: string | null
}

export interface DiagEvent {
  ts: string | null
  type: string
  symbol: string
  level: string
  reason?: string
  source?: string
  regime?: string
  adx?: number
  atr?: number
  bb?: number
  vol?: number
  pnl1h?: number
  pnl6h?: number
  pnl24h?: number
  wr?: number
  dd?: number
  consec?: number
  tph?: number
  side?: string
  price?: number
  strategy?: string
  from?: string
  to?: string
  action?: string
  multiplier?: number
}

export interface DiagnosticsData {
  status: AdaptiveStatus & { guardrails?: { kill_switch?: { active: boolean }; stats?: Record<string, number>; risk_multiplier?: number; dynamic_score_min?: number; symbol_cooldowns?: Record<string, unknown> } }
  events: DiagEvent[]
}

export interface AdaptiveStatus {
  active_profile: string
  regime: {
    global_regime: string
    symbols: Record<string, { regime: string; adx: number; atr_pct: number; bb_width_pct: number; volume_ratio: number }>
  }
  performance: {
    pnl_1h: number; pnl_6h: number; pnl_24h: number
    win_rate_last_10: number; drawdown_intraday: number
    consecutive_losses: number; trades_per_hour: number
  }
  advisor: {
    suggested_profile: string | null
    explanation: string
    confidence: number
  }
}

// -- API calls --
export const api = {
  login: (username: string, password: string) =>
    request<LoginResponse>('/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  logout: () => request<{ ok: boolean }>('/logout', { method: 'POST' }),
  getBalance: () => request<Balance>('/balance'),
  getPositions: () => request<Position[]>('/positions'),
  closePosition: (tradeId: number) =>
    request<{ ok: boolean; closed_at_price: number }>(`/positions/${tradeId}/close`, { method: 'POST' }),
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
  getMe: () => request<{ username: string; display_name: string; role: string }>('/me'),
  getUsers: () => request<UserItem[]>('/users'),
  createUser: (data: { username: string; password: string; display_name?: string; role: string }) =>
    request<UserItem>('/users', { method: 'POST', body: JSON.stringify(data) }),
  updateUser: (id: number, data: { display_name?: string; role?: string; password?: string; is_active?: boolean }) =>
    request<UserItem>(`/users/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteUser: (id: number) =>
    request(`/users/${id}`, { method: 'DELETE' }),
  addSymbol: (symbol: string) =>
    request<{ symbols: string[] }>('/symbols/add', {
      method: 'POST', body: JSON.stringify({ symbol }),
    }),
  removeSymbol: (symbol: string) =>
    request<{ symbols: string[] }>('/symbols/remove', {
      method: 'POST', body: JSON.stringify({ symbol }),
    }),
  clearApiKeys: (type: 'live' | 'testnet' | 'all') =>
    request<{ ok: boolean }>(`/settings/keys?key_type=${type}`, { method: 'DELETE' }),
  getAssets: () => request<AssetItem[]>('/assets'),
  getSkillsSummary: () => request<SkillsSummary>('/skills/summary'),
  getSkills: (category?: string) =>
    request<SkillItem[]>(category ? `/skills?category=${category}` : '/skills'),
  getSkill: (name: string) => request<SkillItem>(`/skills/${name}`),
  getAdaptiveStatus: () => request<AdaptiveStatus>('/adaptive/status'),
  getNewsSentiment: () => request<NewsSentiment>('/adaptive/news-sentiment'),
  generateTuningSuggestion: () =>
    request<{ ok: boolean; suggestion: TuningSuggestionItem | null; reasoning?: string }>('/adaptive/tuning/generate', { method: 'POST' }),
  getTuningHistory: () => request<TuningSuggestionItem[]>('/adaptive/tuning/history'),
  applyTuningSuggestion: (id: number) =>
    request<{ ok: boolean; applied_changes: unknown[] }>(`/adaptive/tuning/suggestions/${id}/apply`, { method: 'POST' }),
  rejectTuningSuggestion: (id: number) =>
    request<{ ok: boolean }>(`/adaptive/tuning/suggestions/${id}/reject`, { method: 'POST' }),
  getDiagnostics: () => request<DiagnosticsData>('/diagnostics'),
  getGuardrailsConfig: () => request<Record<string, unknown>>('/adaptive/guardrails/config'),
  updateGuardrailsConfig: (config: Record<string, unknown>) =>
    request<{ ok: boolean }>('/adaptive/guardrails/config', { method: 'PUT', body: JSON.stringify(config) }),
  resetGuardrailsConfig: () =>
    request<{ ok: boolean; config: Record<string, unknown> }>('/adaptive/guardrails/config/reset', { method: 'POST' }),
}
