/** 领域类型定义，与后端 JSON 序列化格式保持一致。 */

export interface Tick {
  vt_symbol: string
  symbol: string
  exchange: string
  datetime: string | null
  name: string
  volume: number
  turnover: number
  open_interest: number
  last_price: number
  limit_up: number
  limit_down: number
  open_price: number
  high_price: number
  low_price: number
  pre_close: number
  gateway_name: string
  bid_price_1: number
  bid_price_2: number
  bid_price_3: number
  bid_price_4: number
  bid_price_5: number
  ask_price_1: number
  ask_price_2: number
  ask_price_3: number
  ask_price_4: number
  ask_price_5: number
  bid_volume_1: number
  bid_volume_2: number
  bid_volume_3: number
  bid_volume_4: number
  bid_volume_5: number
  ask_volume_1: number
  ask_volume_2: number
  ask_volume_3: number
  ask_volume_4: number
  ask_volume_5: number
}

export interface Order {
  vt_orderid: string
  vt_symbol: string
  symbol: string
  exchange: string
  orderid: string
  type: string
  direction: string | null
  offset: string
  price: number
  volume: number
  traded: number
  status: string
  datetime: string | null
  reference: string
  gateway_name: string
}

export interface Trade {
  vt_tradeid: string
  vt_orderid: string
  vt_symbol: string
  symbol: string
  exchange: string
  orderid: string
  tradeid: string
  direction: string | null
  offset: string
  price: number
  volume: number
  datetime: string | null
  gateway_name: string
  pnl: number
  pnl_gross: number
  fee: number
  entry_price: number
}

export interface Position {
  vt_positionid: string
  vt_symbol: string
  symbol: string
  exchange: string
  direction: string
  volume: number
  frozen: number
  price: number
  pnl: number
  yd_volume: number
  gateway_name: string
  datetime: string | null
}

export interface Account {
  vt_accountid: string
  accountid: string
  balance: number
  frozen: number
  available: number
  gateway_name: string
}

export interface Contract {
  vt_symbol: string
  symbol: string
  exchange: string
  name: string
  product: string
  size: number
  pricetick: number
  min_volume: number
  stop_supported: boolean
  net_position: boolean
  history_data: boolean
  gateway_name: string
}

export interface LogEntry {
  time: string | null
  level: number
  msg: string
  gateway_name: string
}

export interface Bar {
  vt_symbol?: string
  symbol?: string
  exchange?: string
  datetime: string | null
  interval?: string | null
  volume: number
  turnover?: number
  open_interest?: number
  open_price: number
  high_price: number
  low_price: number
  close_price: number
}

export interface GatewayInfo {
  name: string
  connected: boolean
  default_setting: Record<string, string | number | boolean>
  exchanges: string[]
}

export interface StrategyData {
  strategy_name: string
  class_name: string
  vt_symbol: string
  author: string
  parameters: Record<string, unknown>
  variables: Record<string, unknown> & {
    inited?: boolean
    trading?: boolean
    pos?: number
  }
  auto_start?: boolean
}

export interface StrategyClassInfo {
  class_name: string
  display_name?: string
  author: string
  description: string
  parameters: Record<string, unknown>
  param_descriptions: Record<string, string>
  variable_descriptions: Record<string, string>
  variables: string[]
}

export interface StrategyFileInfo {
  filename: string
  size: number
  modified: number
}

export interface StrategyLogEntry {
  strategy_name: string
  msg: string
  time: string
}

export interface BacktestStatistics {
  start_date: string
  end_date: string
  total_days: number
  profit_days: number
  loss_days: number
  capital: number
  end_balance: number
  total_return: number
  annual_return: number
  max_drawdown: number
  max_ddpercent: number
  max_drawdown_duration: number
  total_net_pnl: number
  total_commission: number
  total_slippage: number
  total_turnover: number
  total_trade_count: number
  daily_return: number
  return_std: number
  sharpe_ratio: number
  return_drawdown_ratio: number
  win_rate: number
  profit_factor: number
}

export interface DailyResult {
  date: string
  close_price: number
  pre_close: number
  trade_count: number
  start_pos: number
  end_pos: number
  turnover: number
  commission: number
  slippage: number
  trading_pnl: number
  holding_pnl: number
  total_pnl: number
  net_pnl: number
}

export interface BacktestResult {
  statistics: BacktestStatistics | BacktestStatistics[]
  daily_results: DailyResult[]
  trades: Trade[]
  logs: string[]
}

export interface BacktestTask {
  task_id: string
  status: 'pending' | 'running' | 'done' | 'failed'
  message: string
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  error: string
  kind?: string
  result?: BacktestResult | Record<string, unknown> | null
}

// ----- 参数优化 -----
export interface OptimizeSegment {
  start: string
  end: string
}
export interface OptimizeResultRow {
  rank: number
  params: Record<string, number>
  params_text: string
  segment_scores: number[]
  segment_detail: {
    segment: OptimizeSegment
    score?: number
    trades?: number
    total_return?: number
    max_ddpercent?: number
    error?: string
  }[]
  median: number
  worst: number
  mean: number
  robust_score: number
  valid_segments: number
  errors: string[]
}
export interface OptimizeChampion {
  params: Record<string, number>
  params_text: string
  robust_score: number
  median: number
  worst: number
  valid_segments: number
  convergence: 'unanimous' | 'majority' | 'scattered'
  argmax_per_segment: string[]
  plateau: 'plateau' | 'peak' | 'ridge' | 'unknown'
  plateau_ratio: number | null
  neighbor_scores: { params_text: string; robust_score: number }[]
  full_window_score: number | null
  full_window_stats: Record<string, number> | null
  in_sample_bias: number | null
  notes: string[]
}
export interface RobustResult {
  mode: 'robust'
  metric: string
  segments: OptimizeSegment[]
  k_segments: number
  total_combos: number
  total_runs: number
  results: OptimizeResultRow[]
  valid_count: number
  error_count: number
  champion: OptimizeChampion | null
  robust_formula: string
}
export interface WalkForwardFold {
  fold: number
  train: OptimizeSegment
  test: OptimizeSegment
  status: 'ok' | 'train_failed' | 'oos_failed'
  best_params?: Record<string, number>
  best_params_text?: string
  is_score?: number
  oos_score?: number
  oos_total_return?: number
  oos_sharpe?: number
  oos_max_ddpercent?: number
  oos_trades?: number
  errors?: string[]
}
export interface WalkForwardResult {
  mode: 'walkforward'
  metric: string
  train_days: number
  test_days: number
  step_days: number
  total_combos: number
  folds: WalkForwardFold[]
  valid_folds: number
  failed_folds: number
  is_mean: number | null
  oos_mean: number | null
  degradation: number | null
  consistency: number | null
  oos_avg_return: number | null
  oos_total_trades: number
  notes: string[]
}

// ----- 策略守护 -----
export interface GuardianCheck {
  checked_at: string
  new_bars: number
  new_data_start: string | null
  new_data_end: string | null
  k_segments: number
  segment_scores: number[]
  segment_detail: {
    start: string
    end: string
    score: number
    trades: number | null
    total_return: number | null
    max_ddpercent: number | null
  }[]
  robust_score?: number
  pct_degradation?: number
  trade_count?: number
  status_after: string
  reasons?: string[]
  errors?: string[]
  error?: string
}
export interface GuardianRecord {
  guardian_id: string
  class_name: string
  vt_symbol: string
  interval: string
  setting: Record<string, unknown>
  cost: Record<string, number>
  metric: string
  status: 'healthy' | 'watch' | 'retired'
  consecutive_watch: number
  baseline: {
    kind: string
    start: string
    end: string
    data_as_of: string | null
    score: number
    stats: Record<string, unknown>
    created_at: string
  }
  checks: GuardianCheck[]
  created_at: string
  updated_at: string
}

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

export interface RiskStatus {
  active: boolean
  settings: Record<string, number | boolean>
  status: Record<string, number | boolean | string>
}

export interface HealthInfo {
  status: string
  app: string
  version: string
  engine_state: string
  gateways: Record<string, boolean>
  ws_clients: number
  event_queue_size: number
  dropped_ws_msgs: number
}

/** WebSocket 消息信封。 */
export interface WsMessage {
  type: string
  data: Record<string, unknown>
}

// ----- 期权 -----

export interface OptionUnderlying {
  underlying: string
  variety_code: string
  exchange_code: string
  name: string
  contract_count: number
}

export interface OptionExpiry {
  expiry: string
  calls: number
  puts: number
}

export interface OptionVariety {
  variety_code: string
  name: string
  exchange_code: string
  contract_multiplier: number | null
  transaction_fee: number | null
  settlement_type: string | null
}

export interface OptionSyncStatus {
  status: string
  scope: string
  progress: string
  total_contracts: number
  saved_contracts: number
  detail_done: number
  detail_total: number
  errors: number
  started_at: string | null
  finished_at: string | null
  error: string
  last_sync: string | null
  contract_count: number | null
}

export interface OptionQuoteSide {
  thscode: string
  name: string
  option_type: string
  price: number | null
  prev_close: number | null
  change_pct: number | null
  volume: number | null
  oi: number | null
  iv: number | null
  delta: number | null
  list_date: string | null
  trade_amount: number | null
}

export interface OptionChainRow {
  strike: number
  call?: OptionQuoteSide
  put?: OptionQuoteSide
}

export interface OptionChainResult {
  underlying: string
  name: string
  expiry: string
  underlying_close: number
  underlying_prev_close: number
  items: OptionChainRow[]
  fetched_contracts: number
}

/** 期权组合模板信息（参数 schema 用于动态渲染表单）。 */
export interface OptionTemplateInfo {
  class_name: string
  display_name: string
  description: string
  author: string
  parameters: Record<string, unknown>
  param_descriptions: Record<string, string>
  variables: string[]
}

export interface OptionBacktestReq {
  class_name: string
  underlying: string
  start: string
  end?: string
  capital?: number
  commission?: number
  slippage?: number
  risk_free?: number
  iv_fallback?: number
  margin_coef?: number | null
  margin_limit_pct?: number
  strike_range_pct?: number
  setting?: Record<string, unknown>
}

export interface OptionBacktestStatistics {
  underlying: string
  class_name: string
  start_date: string
  end_date: string
  total_days: number
  profit_days: number
  loss_days: number
  capital: number
  end_balance: number
  total_return: number
  annual_return: number
  max_drawdown: number
  max_ddpercent: number
  max_drawdown_duration: number
  total_net_pnl: number
  total_commission: number
  total_trade_count: number
  daily_return: number
  return_std: number
  sharpe_ratio: number
  return_drawdown_ratio: number
  win_rate: number
  profit_factor: number
  premium_paid: number
  premium_received: number
  max_margin_used: number
  iv_fallback: number
  risk_free: number
  busted: boolean
}

export interface OptionDailyResult {
  date: string
  underlying_close: number
  equity: number
  net_pnl: number
  commission: number
  margin_used: number
  delta: number
  gamma: number
  theta: number
  vega: number
  leg_count: number
}

export interface OptionTradeRecord {
  date: string
  thscode: string
  name: string
  action: 'open' | 'close' | 'settle' | 'force_close'
  option_type: string
  strike: number
  expiry: string
  direction: number
  volume: number
  price: number
  premium: number
  fee: number
  margin: number
  pnl: number
}

export interface OptionPayoffPoint {
  price: number
  pnl: number
}

export interface OptionPayoff {
  underlying_close: number
  date: string
  points: OptionPayoffPoint[]
  legs: {
    thscode: string
    name: string
    option_type: string
    strike: number
    expiry: string
    direction: number
    volume: number
    open_price: number
  }[]
}

export interface OptionBacktestResult {
  statistics: OptionBacktestStatistics
  daily_results: OptionDailyResult[]
  trades: OptionTradeRecord[]
  logs: string[]
  payoff: OptionPayoff
  legs: OptionPayoff['legs']
}

// ----- 关注（watchlist） -----

export type WatchKind = 'stock' | 'future' | 'option_underlying' | 'option_contract'

export interface WatchEntry {
  id: string
  kind: WatchKind
  name: string
  added_at: string
}

export interface WatchQuote {
  last: number | null
  prev_close: number | null
  change_pct: number | null
  source: 'tick' | 'daily' | null
  as_of?: string | null
}

export interface WatchSummaryItem extends WatchEntry {
  quote: WatchQuote
  abnormal: { level: string; closeness: number; ret3_pct: number } | null
}

export interface KeyLevelItem {
  value: number
  label: string
  type: string
  side: string
  strength: number
  distance_pct: number
  rank?: number
}

export interface WatchDetail {
  id: string
  kind: WatchKind
  name: string
  quote: WatchQuote
  abnormal: Record<string, unknown> | null
  alerts: { time: string; rule_name: string; vt_symbol: string; message: string; last_price?: number }[]
  key_levels?: { levels: KeyLevelItem[]; reference?: Record<string, unknown>; error?: string }
  closes?: { date: string; close: number }[]
  contract?: {
    name: string
    option_type: string
    strike_price: number | null
    last_trade_date: string | null
    list_date: string | null
    trade_amount: number | null
    margin_rate: number | null
  }
  iv_snapshot?: {
    price: number | null
    as_of: string | null
    iv: number | null
    delta: number | null
    underlying_close?: number
    days_to_expiry?: number
    note?: string
  }
  iv_term?: { expiry: string; call_iv: number | null; put_iv: number | null }[]
}

export interface AiAnalysisRecord {
  id: string
  kind: string
  time: string
  reply: string
  tool_calls: number
}
