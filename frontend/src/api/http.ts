/** Axios 客户端与 REST 接口封装。 */

import axios from 'axios'
import type {
  Account,
  BacktestTask,
  Bar,
  Contract,
  GatewayInfo,
  GuardianRecord,
  HealthInfo,
  LogEntry,
  OptionBacktestReq,
  OptionBacktestResult,
  OptionChainResult,
  OptionExpiry,
  OptionSyncStatus,
  OptionTemplateInfo,
  OptionUnderlying,
  OptionVariety,
  Order,
  AiAnalysisRecord,
  WatchDetail,
  WatchEntry,
  WatchSummaryItem,
  Position,
  StrategyClassInfo,
  StrategyData,
  Tick,
  Trade,
} from '../types'

export const http = axios.create({ baseURL: '/api', timeout: 60000 })

// 401 重定向去重标记（防止并发 401 触发多次重载）
let isRedirecting = false

// 请求拦截器：自动附加 Authorization 头
http.interceptors.request.use(config => {
  const token = localStorage.getItem('auth_token')
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截器：401 时清除 token 并跳转登录页
http.interceptors.response.use(
  res => res,
  error => {
    if (error.response?.status === 401 && !isRedirecting) {
      isRedirecting = true
      localStorage.removeItem('auth_token')
      // 重载当前页触发重新鉴权：auth gate 检测到无 token 后渲染
      // LoginPage。修复原先跳 /login 的 hack（生产 StaticFiles 下
      // /login 这类路径会 404）；HashRouter 下重载会保留 hash 路由，
      // 重新登录后可回到原页面。
      window.location.reload()
    }
    return Promise.reject(error)
  },
)

// ----- 系统 -----
export const getHealth = () => http.get<HealthInfo>('/health').then(r => r.data)
export const getLogs = (limit = 200) =>
  http.get<LogEntry[]>('/logs', { params: { limit } }).then(r => r.data)

// ----- Gateway 接口 -----
export const getGateways = () =>
  http.get<GatewayInfo[]>('/gateways').then(r => r.data)
export const connectGateway = (name: string, setting: Record<string, unknown> = {}) =>
  http.post(`/gateways/${name}/connect`, { setting }).then(r => r.data)
export const disconnectGateway = (name: string) =>
  http.post(`/gateways/${name}/disconnect`).then(r => r.data)
export const subscribeSymbols = (gateway: string, vtSymbols: string[]) =>
  http.post(`/gateways/${gateway}/subscribe`, { vt_symbols: vtSymbols }).then(r => r.data)

// ----- CTP 账户配置 -----
export interface CtpConfig {
  userid: string
  password: string
  has_password: boolean
  brokerid: string
  td_address: string
  md_address: string
  appid: string
  auth_code: string
}
export interface CtpServerPreset {
  id: string
  label: string
  note: string
  brokerid: string
  td_address: string
  md_address: string
  appid: string
  auth_code: string
}
export const getCtpConfig = () =>
  http.get<CtpConfig>('/gateways/ctp/config').then(r => r.data)
export const getCtpPresets = () =>
  http.get<CtpServerPreset[]>('/gateways/ctp/presets').then(r => r.data)
export const updateCtpConfig = (config: Partial<Record<keyof CtpConfig, string>>) =>
  http.put<CtpConfig>('/gateways/ctp/config', config).then(r => r.data)

// ----- STOCK 股票 Gateway 配置 -----
export interface StockConfig {
  poll_interval: number
}
export const getStockConfig = () =>
  http.get<StockConfig>('/gateways/stock/config').then(r => r.data)
export const updateStockConfig = (config: Partial<StockConfig>) =>
  http.put<StockConfig>('/gateways/stock/config', config).then(r => r.data)

// ----- FUYAO（同花顺金融数据 API）配置 -----
export interface FuyaoConfig {
  api_key: string // 脱敏后只暴露尾 4 位（如 ***ab12）
  poll_interval: number
  enable_quotes: boolean
}
export interface FuyaoTestCheck {
  name: string
  ok: boolean
  detail: string
  latency_ms: number
}
export interface FuyaoTestResult {
  ok: boolean
  api_key_configured: boolean
  checks: FuyaoTestCheck[]
}
export const getFuyaoConfig = () =>
  http.get<FuyaoConfig>('/gateways/fuyao/config').then(r => r.data)
export const updateFuyaoConfig = (config: Partial<FuyaoConfig>) =>
  http.put<FuyaoConfig>('/gateways/fuyao/config', config).then(r => r.data)
export const testFuyao = () =>
  http.post<FuyaoTestResult>('/gateways/fuyao/test').then(r => r.data)

// ----- 行情 -----
export const getContracts = (keyword = '', limit = 0, futures = false) =>
  http
    .get<Contract[]>('/contracts', {
      params: {
        keyword: keyword || undefined,
        limit: limit || undefined,
        futures: futures || undefined,
      },
    })
    .then(r => r.data)
export const getTicks = () => http.get<Tick[]>('/ticks').then(r => r.data)
export const getWatchlist = () =>
  http.get<{ items: WatchSummaryItem[]; as_of: string }>('/watchlist').then(r => r.data)
export const addWatchSymbol = (id: string, kind?: string, name?: string) =>
  http
    .post<{ item: WatchEntry }>('/watchlist', { id, kind, name })
    .then(r => r.data.item)
export const removeWatchSymbol = (vtSymbol: string) =>
  http.delete<{ ok: boolean }>(`/watchlist/${encodeURIComponent(vtSymbol)}`).then(r => r.data)
export const getWatchlistDetail = (id: string, kind?: string) =>
  http
    .get<WatchDetail>('/watchlist/detail', { params: { id, kind } })
    .then(r => r.data)
export const aiAnalyze = (id: string, kind?: string) =>
  http
    .post<{ id: string; kind: string; analysis: string; tool_calls: unknown[] }>(
      '/ai/analyze',
      { id, kind },
      { timeout: 180000 },
    )
    .then(r => r.data)
export const getAiAnalyzeHistory = (id?: string) =>
  http
    .get<{ items: AiAnalysisRecord[] }>('/ai/analyze/history', { params: { id } })
    .then(r => r.data.items)
export const getBars = (
  vtSymbol: string,
  interval: string,
  fromGateway = false,
) =>
  http
    .get<Bar[]>('/bars', {
      params: { vt_symbol: vtSymbol, interval, from_gateway: fromGateway },
    })
    .then(r => r.data)
export const getIntradayBars = (vtSymbol: string, interval = '1m', limit = 500) =>
  http
    .get<Bar[]>('/bars/intraday', {
      params: { vt_symbol: vtSymbol, interval, limit },
    })
    .then(r => r.data)

// ----- 关键价位 -----
export interface KeyLevel {
  value: number
  label: string
  type: string
  side: 'support' | 'resistance' | 'pivot'
  strength: number
  distance_pct: number
  rank?: number
}
export interface KeyLevelsResult {
  vt_symbol: string
  levels: KeyLevel[]
  reference: {
    close: number | null
    atr: number | null
    bars_used: number
    support_count: number
    resistance_count: number
  }
}
export const getKeyLevels = (vtSymbol: string) =>
  http
    .get<KeyLevelsResult>('/key-levels', { params: { vt_symbol: vtSymbol } })
    .then(r => r.data)

// ----- 交易 -----
export interface PlaceOrderReq {
  vt_symbol: string
  direction: string
  offset: string
  type: string
  price: number
  volume: number
}
export const placeOrder = (req: PlaceOrderReq) =>
  http.post<{ vt_orderid: string }>('/orders', req).then(r => r.data)
export const cancelOrder = (vtOrderid: string) =>
  http.delete(`/orders/${vtOrderid}`).then(r => r.data)
export const getOrders = () => http.get<Order[]>('/orders').then(r => r.data)
export const getTrades = () => http.get<Trade[]>('/trades').then(r => r.data)
export const getPositions = () =>
  http.get<Position[]>('/positions').then(r => r.data)
export const getAccounts = () =>
  http.get<Account[]>('/accounts').then(r => r.data)

// ----- 委托/成交历史管理 -----
export const deleteOrderHistory = (vtOrderid: string) =>
  http.delete(`/orders/${vtOrderid}/history`).then(r => r.data)
export const clearOrderHistory = () =>
  http.delete('/history/orders').then(r => r.data)
export const clearTradeHistory = () =>
  http.delete('/history/trades').then(r => r.data)

// ----- 策略 -----
export const getStrategies = () =>
  http.get<StrategyData[]>('/strategies').then(r => r.data)
export const getStrategyClasses = () =>
  http.get<StrategyClassInfo[]>('/strategies/classes').then(r => r.data)
export const createStrategy = (body: {
  class_name: string
  name: string
  vt_symbol?: string
  vt_symbols?: string[]
  setting: Record<string, unknown>
}) => http.post('/strategies', body).then(r => r.data)
export const editStrategy = (name: string, setting: Record<string, unknown>) =>
  http.put(`/strategies/${name}`, { setting }).then(r => r.data)
export const deleteStrategy = (name: string) =>
  http.delete(`/strategies/${name}`).then(r => r.data)
export const initStrategy = (name: string) =>
  http.post(`/strategies/${name}/init`).then(r => r.data)
export const startStrategy = (name: string) =>
  http.post(`/strategies/${name}/start`).then(r => r.data)
export const stopStrategy = (name: string) =>
  http.post(`/strategies/${name}/stop`).then(r => r.data)
export const setAutoStart = (name: string, enabled: boolean) =>
  http.put(`/strategies/${name}/auto-start`, { enabled }).then(r => r.data)

// ----- 持仓对账 -----
export interface ReconcileRow {
  strategy_name: string
  vt_symbol: string
  strategy_pos: number
  gateway_net_pos: number
  diff: number
  matched: boolean
  shared: boolean
  gateway_has_position: boolean
  trading: boolean
}
export const reconcilePositions = () =>
  http.get<ReconcileRow[]>('/strategies/reconcile').then(r => r.data)
export const syncStrategyPos = (name: string, pos?: number) =>
  http
    .post<{ strategy_name: string; old_pos: number; new_pos: number; mode?: string }>(
      `/strategies/${name}/sync-pos${pos !== undefined ? `?pos=${pos}` : ''}`,
    )
    .then(r => r.data)
export const getStrategyFiles = () =>
  http
    .get<{ filename: string; size: number; modified: number }[]>(
      '/strategies/files',
    )
    .then(r => r.data)
export const readStrategyFile = (filename: string) =>
  http
    .get<{ filename: string; code: string }>(`/strategies/files/${filename}`)
    .then(r => r.data)
export const writeStrategyFile = (
  filename: string,
  code: string,
  overwrite = false,
) =>
  http
    .post<{
      saved: string
      classes_found: string[]
      total_classes: number
    }>('/strategies/files', { filename, code, overwrite })
    .then(r => r.data)

// ----- 回测 -----
export interface PerformanceResult {
  hours: number
  since: string
  now: string
  total_trades: number
  win_count: number
  loss_count: number
  win_rate: number
  total_return: number
  realized_pnl: number
  unrealized_pnl: number
  total_pnl: number
  total_fee: number
  symbols: string[]
  trades: { vt_symbol: string; direction: string; price: number; volume: number; pnl: number; datetime: string | null }[]
  positions: { vt_symbol: string; direction: string; volume: number; price: number; pnl: number }[]
}
export const getPerformance = (hours: number) =>
  http.get<PerformanceResult>('/backtest/performance', { params: { hours } }).then(r => r.data)
export interface BacktestReq {
  class_name?: string
  class_names?: string[]
  vt_symbol?: string
  vt_symbols?: string[]
  interval: string
  start: string
  end?: string
  capital: number
  rate: number
  slippage: number
  size: number
  pricetick: number
  setting: Record<string, unknown>
}
export const runBacktest = (body: BacktestReq) =>
  http.post<BacktestTask>('/backtest', body).then(r => r.data)
export const getBacktestTask = (taskId: string) =>
  http.get<BacktestTask>(`/backtest/${taskId}`).then(r => r.data)
export const listBacktestTasks = () =>
  http.get<{ tasks: BacktestTask[] }>('/backtest').then(r => r.data)
export const downloadBacktestData = (body: {
  vt_symbol: string
  interval: string
  start: string
  end?: string
  gateway_name?: string
}) => http.post('/backtest/download-data', body).then(r => r.data)
export const getBacktestDataOverview = (vtSymbol: string, interval: string) =>
  http
    .get('/backtest/data', { params: { vt_symbol: vtSymbol, interval } })
    .then(r => r.data)

// ----- 参数优化（稳健筛选 / Walk-Forward）-----
export interface OptimizeReq {
  mode: 'robust' | 'walkforward'
  class_name: string
  vt_symbol: string
  interval: string
  start: string
  end?: string
  metric: string
  grid: Record<string, number[] | { start: number; end: number; step: number }>
  setting?: Record<string, unknown>
  capital?: number
  rate?: number
  slippage?: number
  size?: number
  pricetick?: number
  k_segments?: number
  train_days?: number
  test_days?: number
  step_days?: number
}
export const previewOptimizeGrid = (
  body: Pick<OptimizeReq, 'grid'>,
) =>
  http
    .post<{ count: number; max_combos: number; combos: Record<string, number>[] }>(
      '/backtest/optimize/preview-grid',
      body,
    )
    .then(r => r.data)
export const runOptimize = (body: OptimizeReq) =>
  http.post<BacktestTask>('/backtest/optimize', body).then(r => r.data)

// ----- 策略守护 -----
export interface GuardianAdoptReq {
  class_name: string
  vt_symbol: string
  interval: string
  setting: Record<string, unknown>
  baseline_start: string
  baseline_end: string
  capital?: number
  rate?: number
  slippage?: number
  size?: number
  pricetick?: number
}
export const getGuardians = () =>
  http
    .get<{ guardians: GuardianRecord[] }>('/guardian')
    .then(r => r.data)
export const adoptGuardian = (body: GuardianAdoptReq) =>
  http.post<GuardianRecord>('/guardian/adopt', body).then(r => r.data)
export const recheckGuardian = (guardianId: string) =>
  http.post<BacktestTask>(`/guardian/${guardianId}/recheck`).then(r => r.data)
export const recheckAllGuardians = () =>
  http.post<BacktestTask>('/guardian/recheck-all').then(r => r.data)
export const dropGuardian = (guardianId: string) =>
  http.delete<{ dropped: string }>(`/guardian/${guardianId}`).then(r => r.data)

// ----- 通知渠道 -----
export interface NotifyConfig {
  feishu_webhook: string
  feishu_secret: string
  wecom_webhook: string
  email_smtp_host: string
  email_smtp_port: number
  email_smtp_ssl: boolean
  email_username: string
  email_password: string
  email_to: string[]
  system_notify: boolean
  notify_trades: boolean
  notify_risk: boolean
  notify_strategy: boolean
  notify_guardian: boolean
}
export const getNotifyConfig = () =>
  http.get<NotifyConfig>('/notify/config').then(r => r.data)
export const updateNotifyConfig = (body: Partial<NotifyConfig>) =>
  http.put<NotifyConfig>('/notify/config', body).then(r => r.data)
export const testNotify = () =>
  http
    .post<{ results: Record<string, string> }>('/notify/test')
    .then(r => r.data)

// ----- 配置备份 -----
export interface BackupItems {
  strategies: boolean
  watchlist: boolean
  gateway_configs: boolean
  notify_config: boolean
  risk_settings: boolean
}
export const exportBackup = async (items: BackupItems) => {
  const resp = await http.post<Blob>('/backup/export', items, {
    responseType: 'blob',
  })
  // 从 Content-Disposition 提取文件名
  const disposition = (resp.headers['content-disposition'] as string) || ''
  const match = disposition.match(/filename="?([^";]+)"?/)
  const filename = match?.[1] ?? 'custom-trader-backup.zip'
  const url = URL.createObjectURL(resp.data)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
  return filename
}
export const importBackup = (file: File, allowOverwrite: boolean) => {
  const form = new FormData()
  form.append('file', file)
  return http
    .post<{ created_at: string; restored: Record<string, unknown> }>(
      '/backup/import',
      form,
      { params: { allow_overwrite: allowOverwrite } },
    )
    .then(r => r.data)
}

// ----- 风控 -----
export const getRisk = () =>
  http
    .get<{ settings: Record<string, number | boolean>; status: Record<string, unknown> }>(
      '/risk',
    )
    .then(r => r.data)
export const updateRisk = (settings: Record<string, number | boolean>) =>
  http.put('/risk', settings).then(r => r.data)
export const setTrailingStop = (key: string, pct: number) =>
  http.put('/risk/trailing-stop', { key, pct }).then(r => r.data)
export const removeTrailingStop = (key: string) =>
  http.delete(`/risk/trailing-stop/${encodeURIComponent(key)}`).then(r => r.data)

// ----- AI 助手 -----
export interface AiStatus {
  enabled: boolean
  provider: string
  model: string
  base_url: string
  has_api_key: boolean
  allow_trading: boolean
  auto_optimize: boolean
}
export interface AiPreset {
  id: string
  group: string
  label: string
  provider: string
  model: string
  base_url: string
}
export interface AiToolCall {
  name: string
  input: Record<string, unknown>
  output: string
}
export interface AiChatResponse {
  reply: string
  tool_calls: AiToolCall[]
  messages: unknown[]
}
export const getAiStatus = () =>
  http.get<AiStatus>('/ai/status').then(r => r.data)
export const getAiPresets = () =>
  http.get<AiPreset[]>('/ai/presets').then(r => r.data)
export const updateAiConfig = (config: {
  provider?: string
  model?: string
  base_url?: string
  api_key?: string
  allow_trading?: boolean
  auto_optimize?: boolean
}) => http.put<AiStatus>('/ai/config', config).then(r => r.data)
export const postAiChat = (
  messages: unknown[],
  conversationId?: string,
  displayMessages?: { role: string; text: string }[],
) =>
  http
    .post<AiChatResponse>(
      '/ai/chat',
      {
        messages,
        conversation_id: conversationId,
        display_messages: displayMessages,
      },
      { timeout: 600000 },
    )
    .then(r => r.data)

// ----- AI 会话管理 -----
export interface ConversationSummary {
  id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
}
export interface Conversation extends ConversationSummary {
  messages: unknown[]
  display_messages: { role: string; text: string }[]
}
export const listConversations = () =>
  http.get<ConversationSummary[]>('/conversations').then(r => r.data)
export const getConversation = (id: string) =>
  http.get<Conversation>(`/conversations/${id}`).then(r => r.data)
export const createConversation = () =>
  http.post<Conversation>('/conversations').then(r => r.data)
export const deleteConversation = (id: string) =>
  http.delete(`/conversations/${id}`).then(r => r.data)
export const reloadStrategies = () =>
  http.post('/strategies/reload').then(r => r.data)

// ----- MCP 配置 -----
export const getMcpToken = () =>
  http.get<{ token: string }>('/auth/mcp-token').then(r => r.data)
export const refreshMcpToken = () =>
  http.post<{ token: string }>('/auth/mcp-token/refresh').then(r => r.data)
/** 下载预置了服务器地址与 MCP Token 的专属客户端脚本。 */
export const downloadMcpScript = async (baseUrl?: string) => {
  const resp = await http.get('/mcp/script', {
    params: baseUrl ? { base: baseUrl } : undefined,
    responseType: 'blob',
  })
  const blob = new Blob([resp.data as BlobPart], { type: 'text/x-python' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'custom_trader_mcp.py'
  a.click()
  URL.revokeObjectURL(url)
}

// ----- 盘后 AI 复盘 -----
export interface AiRecapResult {
  recap: string
  snapshot_time: string
  subscribed_count: number
  tool_calls?: AiToolCall[]
}
export const postAiRecap = () =>
  http.post<AiRecapResult>('/ai/recap').then(r => r.data)

// ----- 监控中心 -----
export interface MonitorRule {
  rule_id: string
  name: string
  enabled: boolean
  scope: { type: 'symbols' | 'watchlist'; symbols: string[] }
  conditions: { field: string; op: string; value: number }[]
  logic: 'and' | 'or'
  cooldown_seconds: number
  paper_action: { direction: string; volume: number }
  created_at: string
  last_triggered_at: string | null
}
export interface MonitorAlert {
  time: string
  rule_id: string
  rule_name: string
  vt_symbol: string
  message: string
  last_price?: number
}
export const getMonitorRules = () =>
  http.get<{ rules: MonitorRule[] }>('/monitor/rules').then(r => r.data.rules)
export const createMonitorRule = (body: Partial<MonitorRule>) =>
  http.post<MonitorRule>('/monitor/rules', body).then(r => r.data)
export const updateMonitorRule = (ruleId: string, body: Partial<MonitorRule>) =>
  http.put<MonitorRule>(`/monitor/rules/${ruleId}`, body).then(r => r.data)
export const deleteMonitorRule = (ruleId: string) =>
  http.delete<{ deleted: string }>(`/monitor/rules/${ruleId}`).then(r => r.data)
export const toggleMonitorRule = (ruleId: string, enabled: boolean) =>
  http.patch<MonitorRule>(`/monitor/rules/${ruleId}/enabled`, { enabled }).then(r => r.data)
export const getMonitorAlerts = (limit = 100) =>
  http.get<{ alerts: MonitorAlert[] }>('/monitor/alerts', { params: { limit } }).then(r => r.data.alerts)
export const clearMonitorAlerts = () =>
  http.delete<{ cleared: boolean }>('/monitor/alerts').then(r => r.data)

// ----- 模拟盘 -----
export interface PaperPosition {
  vt_symbol: string
  direction: 'long' | 'short'
  volume: number
  avg_price: number
  mark_price: number
  unrealized_pnl: number
}
export interface PaperOverview {
  settings: { capital: number; rate: number; slippage: number }
  cash: number
  equity: number
  realized_pnl: number
  unrealized_pnl: number
  fees_total: number
  trade_count: number
  positions: PaperPosition[]
  total_return_pct: number
}
export interface PaperFill {
  time: string
  vt_symbol: string
  direction: 'LONG' | 'SHORT'
  price: number
  volume: number
  size: number
  fee: number
  reason: string
  source: string
}
export const getPaperOverview = () =>
  http.get<PaperOverview>('/paper/overview').then(r => r.data)
export const placePaperOrder = (body: {
  vt_symbol: string
  direction: 'buy' | 'sell'
  volume: number
  reason?: string
}) => http.post<{ ok: boolean; message: string }>('/paper/order', body).then(r => r.data)
export const getPaperFills = (limit = 100) =>
  http.get<{ fills: PaperFill[] }>('/paper/fills', { params: { limit } }).then(r => r.data.fills)
export const updatePaperSettings = (body: Partial<Pick<PaperOverview['settings'], 'capital' | 'rate' | 'slippage'>>) =>
  http.put<PaperOverview['settings']>('/paper/settings', body).then(r => r.data)
export const resetPaper = () =>
  http.post<{ reset: boolean }>('/paper/reset').then(r => r.data)

// ----- 市场环境 / 数据健康 -----
export interface RegimeResult {
  status: string
  as_of?: string
  score?: number
  label?: string
  dimensions?: { trend: number; breadth: number; momentum: number; volatility: number }
  universe_size?: number
  history?: { date: string; score: number; label: string; dimensions: Record<string, number> }[]
  hint?: string
  note?: string
}
export const getRegime = (lookback = 30) =>
  http.get<RegimeResult>('/regime', { params: { lookback } }).then(r => r.data)

export interface DataHealthIssue {
  type: 'invalid' | 'missing' | 'stale' | 'sparse'
  count: number
  detail: string
}
export interface DataHealthProblem {
  vt_symbol: string
  bars: number
  first: string | null
  last: string | null
  issues: DataHealthIssue[]
}
export interface DataHealthResult {
  checked: number
  healthy: number
  problematic: number
  problems: DataHealthProblem[]
  checked_at: string
}
export const getDataHealth = () =>
  http.get<DataHealthResult>('/data-health').then(r => r.data)
export const repairDataHealth = (symbols: string[], days = 400) =>
  http
    .post<{ results: { vt_symbol: string; ok: boolean; detail: string }[] }>(
      '/data-health/repair',
      { symbols, days },
    )
    .then(r => r.data.results)

// ----- 全市场数据同步 -----
export interface MarketSyncStatus {
  status: 'idle' | 'pending' | 'running' | 'done' | 'failed' | 'cancelled'
  mode: string
  progress: string
  total_symbols: number
  done_symbols: number
  saved_bars: number
  errors: number
  started_at: string | null
  finished_at: string | null
  error: string
  last_full_sync: string | null
  last_incremental: string | null
  symbol_count: number | null
}
export const getMarketSyncStatus = () =>
  http.get<MarketSyncStatus>('/market-sync/status').then(r => r.data)
export const startMarketSync = (mode: 'full' | 'incremental', days = 800) =>
  http
    .post<{ ok: boolean; message: string }>('/market-sync/run', null, {
      params: { mode, days },
    })
    .then(r => r.data)
export const cancelMarketSync = () =>
  http.post<{ cancelled: boolean }>('/market-sync/cancel').then(r => r.data)

// ----- 市场情绪（全市场涨停梯队 + 6 阶段）-----
export interface MarketPhaseResult {
  status: string
  as_of?: string
  universe_size?: number
  trading_days?: number
  phase?: string
  phase_sequence?: string[]
  thresholds?: Record<string, number>
  sentiment_now?: { score: number; zone: string }
  sentiment_zones?: { name: string; low: number; high: number }[]
  ladder_stats?: {
    limit_up: number
    limit_down?: number
    first_board: number
    ge2: number
    ge3: number
    ge5: number
    max_streak: number
    promotion_rate: number | null
    blast_rate: number | null
  }
  ladder_top?: { vt_symbol: string; name: string; streak: number }[]
  history?: {
    date: string
    phase: string
    limit_up: number
    max_streak: number
    ge2: number
    promotion_rate: number | null
    score?: number
    zone?: string
  }[]
  hint?: string
  note?: string
}
export const getMarketPhase = () =>
  http.get<MarketPhaseResult>('/market-phase').then(r => r.data)

// ----- 异动监控（交易所口径）-----
export interface AbnormalItem {
  vt_symbol: string
  name: string
  board: string
  close: number
  ret3_pct: number
  dev3_pct: number
  threshold_pct: number
  closeness: number
  level: string
  ret10_pct: number | null
  ret30_pct: number | null
  date: string
}
export interface AbnormalResult {
  status: string
  as_of?: string
  universe_size?: number
  baseline3_pct?: number
  counts?: { 触发: number; 边缘: number; 关注: number }
  items?: AbnormalItem[]
  hint?: string
  note?: string
}
export const getAbnormalMoves = () =>
  http.get<AbnormalResult>('/abnormal').then(r => r.data)

// ----- 全市场策略扫描 -----
export interface ScreenerStrategy {
  id: string
  name: string
  description: string
}
export interface ScreenerItem {
  vt_symbol: string
  name: string
  close: number
  detail: string
}
export interface ScreenerStrategyResult {
  id: string
  name: string
  description: string
  count: number
  items: ScreenerItem[]
}
export interface ScreenerResult {
  status: string
  as_of?: string
  universe_size?: number
  strategies?: ScreenerStrategyResult[]
  hint?: string
}
export const getScreenerStrategies = () =>
  http
    .get<{ strategies: ScreenerStrategy[] }>('/screener/strategies')
    .then(r => r.data.strategies)
export const runScreener = (strategies: string[]) =>
  http
    .post<ScreenerResult>('/screener/run', { strategies })
    .then(r => r.data)

// ----- 期权 -----
export const getOptionVarieties = () =>
  http.get<{ items: OptionVariety[] }>('/options/varieties').then(r => r.data)
export const getOptionUnderlyings = () =>
  http.get<{ items: OptionUnderlying[] }>('/options/underlyings').then(r => r.data)
export const startOptionSync = (body: { scope: string; underlying?: string }) =>
  http.post<{ ok: boolean }>('/options/sync', body).then(r => r.data)
export const getOptionSyncStatus = () =>
  http.get<OptionSyncStatus>('/options/sync/status').then(r => r.data)
export const getOptionExpiries = (underlying: string) =>
  http
    .get<{ items: OptionExpiry[] }>('/options/expiries', { params: { underlying } })
    .then(r => r.data)
export const getOptionChain = (underlying: string, expiry: string, refresh = true) =>
  http
    .get<OptionChainResult>('/options/chain', {
      params: { underlying, expiry, refresh },
      timeout: 120000,
    })
    .then(r => r.data)
export const getOptionContractsList = (underlying: string, expiry?: string, optionType?: string) =>
  http
    .get<{
      items: {
        thscode: string
        name: string
        option_type: string | null
        strike_price: number | null
        list_date: string | null
        last_trade_date: string | null
        trade_amount: number | null
        margin_rate: number | null
      }[]
    }>('/options/contracts', { params: { underlying, expiry, option_type: optionType } })
    .then(r => r.data)
export const getOptionTemplates = () =>
  http.get<{ items: OptionTemplateInfo[] }>('/options/backtest/templates').then(r => r.data)
export const runOptionBacktest = (body: OptionBacktestReq) =>
  http.post<BacktestTask>('/options/backtest', body, { timeout: 120000 }).then(r => r.data)
export type OptionBacktestTaskResult = { result?: OptionBacktestResult } & BacktestTask

export const getOptionIvTerm = (underlying: string) =>
  http
    .get<{ underlying: string; underlying_close: number | null; items: { expiry: string; call_iv: number | null; put_iv: number | null }[] }>(
      '/options/iv-term',
      { params: { underlying } },
    )
    .then(r => r.data)
export const getOptionBars = (thscode: string, days = 60) =>
  http
    .get<{ thscode: string; items: { date: string; open: number; high: number; low: number; close: number; volume: number }[] }>(
      '/options/bars',
      { params: { thscode, days }, timeout: 120000 },
    )
    .then(r => r.data)

export interface OptionOptimizeReq {
  mode: 'robust' | 'walkforward'
  class_name: string
  underlying: string
  start: string
  end?: string
  metric: string
  grid: Record<string, number[] | { start: number; end: number; step: number }>
  setting?: Record<string, unknown>
  capital?: number
  commission?: number
  slippage?: number
  iv_fallback?: number
  margin_coef?: number | null
  margin_limit_pct?: number
  strike_range_pct?: number
  k_segments?: number
  train_days?: number
  test_days?: number
  step_days?: number
}
export const runOptionOptimize = (body: OptionOptimizeReq) =>
  http.post<BacktestTask>('/options/optimize', body, { timeout: 120000 }).then(r => r.data)
