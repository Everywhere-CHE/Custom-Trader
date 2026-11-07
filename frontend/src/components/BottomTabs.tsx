/** 底部标签页：委托 / 成交 / 持仓 / 资金 / 日志（各 Tab 独立订阅，互不干扰）。 */

import { useEffect, useState } from 'react'
import { Button, Popconfirm, Table, Tabs, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { cancelOrder, clearOrderHistory, clearTradeHistory, getOrders, getPositions, getTrades, placeOrder } from '../api/http'
import { useWsStore } from '../api/ws'
import { useMarketStore } from '../stores/market'
import { useTradingStore } from '../stores/trading'
import { useSoundStore } from '../stores/sound'
import { playBeep } from '../utils/sound'
import type { Account, LogEntry, Order, Position, Trade } from '../types'

const { Text } = Typography

/** 半透明状态色标签（替代高饱和 antd Tag）。 */
const STATUS_STYLES: Record<string, string> = {
  SUBMITTING: 'bg-sky-500/10 text-sky-600 dark:text-sky-300',
  NOTTRADED: 'bg-amber-500/10 text-amber-600 dark:text-amber-300',
  PARTTRADED: 'bg-amber-500/10 text-amber-600 dark:text-amber-300',
  ALLTRADED: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300',
  CANCELLED: 'bg-slate-500/10 text-slate-500 dark:text-slate-400',
  REJECTED: 'bg-rose-500/10 text-rose-600 dark:text-rose-300',
}

function StatusTag({ value }: { value: string }) {
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-[11px] leading-tight ${
        STATUS_STYLES[value] ?? 'bg-slate-500/10 text-slate-500'
      }`}
    >
      {STATUS_LABELS[value] ?? value}
    </span>
  )
}

const ACTIVE_STATUSES = new Set(['SUBMITTING', 'NOTTRADED', 'PARTTRADED'])

function DirectionText({ value }: { value: string | null }) {
  if (!value) return null
  return (
    <span className={value === 'LONG' ? 'price-up' : 'price-down'}>
      {value === 'LONG' ? '多' : value === 'SHORT' ? '空' : value}
    </span>
  )
}

function timeOf(value: string | null): string {
  if (!value) return ''
  return value.slice(0, 16).replace('T', ' ')
}

const OFFSET_LABELS: Record<string, string> = {
  OPEN: '开仓', CLOSE: '平仓', CLOSETODAY: '平今', CLOSEYESTERDAY: '平昨', NONE: '-',
}

const STATUS_LABELS: Record<string, string> = {
  SUBMITTING: '提交中', NOTTRADED: '未成交', PARTTRADED: '部分成交',
  ALLTRADED: '全部成交', CANCELLED: '已撤销', REJECTED: '已拒绝',
}

const tableProps = {
  size: 'small' as const,
  pagination: false as const,
  scroll: { y: 160 },
  className: 'compact-table glass-table',
}

// ---- 子组件：每个 Tab 各自订阅独立数据 ----

function OrdersTab() {
  const orders = useTradingStore(s => s.orders)
  const [messageApi, contextHolder] = message.useMessage()
  const msg = (level: 'success' | 'error' | 'info' | 'warning', content: string) => {
    if (useSoundStore.getState().enabled) playBeep(level === 'error' ? 'error' : 'success')
    messageApi[level](content)
  }
  const sortByTime = <T extends { datetime: string | null }>(arr: T[]) =>
    [...arr].sort((a, b) => (b.datetime ?? '').localeCompare(a.datetime ?? ''))
  const orderList = sortByTime(Object.values(orders))

  const columns: ColumnsType<Order> = [
    { title: '时间', dataIndex: 'datetime', width: 90, render: timeOf },
    { title: '合约', dataIndex: 'vt_symbol', width: 130 },
    { title: '方向', dataIndex: 'direction', width: 60, render: (v: string | null) => <DirectionText value={v} /> },
    { title: '开平', dataIndex: 'offset', width: 60, render: (v: string) => OFFSET_LABELS[v] ?? v },
    { title: '价格', dataIndex: 'price', width: 90, className: 'mono' },
    { title: '数量', width: 90, render: (_, r) => `${r.traded}/${r.volume}`, className: 'mono' },
    { title: '状态', dataIndex: 'status', width: 90, render: (v: string) => <StatusTag value={v} /> },
    {
      title: '操作', width: 80,
      render: (_, r) => ACTIVE_STATUSES.has(r.status) ? (
        <Button size="small" danger onClick={async () => {
          try {
            await cancelOrder(r.vt_orderid)
            try { msg('success', '撤单已提交') } catch { alert('撤单已提交') }
          } catch {
            try { msg('error', '撤单失败') } catch { alert('撤单失败') }
          }
        }}>撤单</Button>
      ) : null,
    },
  ]

  return (
    <>
      {contextHolder}
      <Table {...tableProps} rowKey="vt_orderid" columns={columns} dataSource={orderList} />
    </>
  )
}

function TradesTab() {
  const trades = useTradingStore(s => s.trades)
  const sortByTime = <T extends { datetime: string | null }>(arr: T[]) =>
    [...arr].sort((a, b) => (b.datetime ?? '').localeCompare(a.datetime ?? ''))
  const tradeList = sortByTime(Object.values(trades))

  const columns: ColumnsType<Trade> = [
    { title: '时间', dataIndex: 'datetime', width: 90, render: timeOf },
    { title: '合约', dataIndex: 'vt_symbol', width: 130 },
    { title: '方向', dataIndex: 'direction', width: 60, render: (v: string | null) => <DirectionText value={v} /> },
    { title: '开平', dataIndex: 'offset', width: 60, render: (v: string) => OFFSET_LABELS[v] ?? v },
    { title: '价格', dataIndex: 'price', width: 90, className: 'mono' },
    { title: '数量', dataIndex: 'volume', width: 70, className: 'mono' },
    { title: '成交号', dataIndex: 'vt_tradeid', width: 110 },
    { title: '毛利', dataIndex: 'pnl_gross', width: 100, className: 'mono', render: (v: number | null | undefined) => {
    const val = Number(v ?? 0)
    return (
      <span className={val > 0 ? 'price-up' : val < 0 ? 'price-down' : ''}>
        {Number.isFinite(val) ? `${val >= 0 ? '+' : ''}${val.toFixed(2)}` : '-'}
      </span>
    )
  } },
    { title: '手续费', dataIndex: 'fee', width: 90, className: 'mono', render: (v: number | null | undefined) => (Number.isFinite(Number(v)) ? Number(v).toFixed(2) : '-') },
    { title: '净盈亏', dataIndex: 'pnl', width: 110, className: 'mono', render: (v: number | null | undefined) => {
    const val = Number(v ?? 0)
    return (
      <span className={val > 0 ? 'price-up' : val < 0 ? 'price-down' : ''}>
        {Number.isFinite(val) ? `${val >= 0 ? '+' : ''}${val.toFixed(2)}` : '-'}
      </span>
    )
  } },
  ]

  return (
    <>
      <Table {...tableProps} rowKey="vt_tradeid" columns={columns} dataSource={tradeList} />
    </>
  )
}

function PositionsTab() {
  const positions = useTradingStore(s => s.positions)
  const trades = useTradingStore(s => s.trades)
  const [messageApi, contextHolder] = message.useMessage()
  const [closingPos, setClosingPos] = useState<string | null>(null)
  const msg = (level: 'success' | 'error' | 'info' | 'warning', content: string) => {
    if (useSoundStore.getState().enabled) playBeep(level === 'error' ? 'error' : 'success')
    messageApi[level](content)
  }

  // Number() 防御：任何字符串数值混入只产生 NaN（显示 '-'），不再白屏
  const sumPosPnl = Object.values(positions).reduce((s, p) => s + Number(p.pnl) || s, 0)
  const sumTradePnl = Object.values(trades).reduce((s, t) => s + Number(t.pnl) || s, 0)
  const sumTradeFee = Object.values(trades).reduce((s, t) => s + Number(t.fee) || s, 0)
  const sumTradeGross = Object.values(trades).reduce((s, t) => s + Number(t.pnl_gross) || s, 0)
  const totalPnl = sumPosPnl + sumTradePnl

  const closePosition = async (pos: Position) => {
    const tick = useMarketStore.getState().ticks[pos.vt_symbol]
    if (!tick || !tick.last_price) {
      msg('error', `无法获取 ${pos.vt_symbol} 的最新价格`)
      return
    }
    setClosingPos(pos.vt_positionid)
    try {
      const isLong = pos.direction === 'LONG'
      // 直接用市场买卖价平仓（与 OrderPanel 一致，确保价格合法）
      const closePrice = isLong
        ? (tick.bid_price_1 ?? tick.last_price)
        : (tick.ask_price_1 ?? tick.last_price)
      await placeOrder({ vt_symbol: pos.vt_symbol, direction: isLong ? 'SHORT' : 'LONG', offset: 'CLOSE', type: 'LIMIT', price: closePrice, volume: pos.volume })
      msg('success', `${pos.vt_symbol} ${isLong ? '多' : '空'}单平仓已提交`)
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? String(e)
      msg('error', `平仓失败: ${detail}`)
    } finally { setClosingPos(null) }
  }

  const columns: ColumnsType<Position> = [
    { title: '开仓时间', dataIndex: 'datetime', width: 140, render: (v: string | null) => v ? v.slice(0, 16).replace('T', ' ') : '--' },
    { title: '合约', dataIndex: 'vt_symbol', width: 140 },
    { title: '方向', dataIndex: 'direction', width: 60, render: (v: string) => <DirectionText value={v} /> },
    { title: '数量', dataIndex: 'volume', width: 80, className: 'mono' },
    { title: '昨仓', dataIndex: 'yd_volume', width: 80, className: 'mono' },
    { title: '均价', dataIndex: 'price', width: 100, className: 'mono', render: (v: number | null | undefined) => (Number.isFinite(Number(v)) ? Number(v).toFixed(2) : '-') },
    { title: '盈亏', dataIndex: 'pnl', width: 110, render: (v: number | null | undefined) => {
    const val = Number(v ?? 0)
    return (
      <span className={`mono ${val >= 0 ? 'price-up' : 'price-down'}`}>
        {Number.isFinite(val) ? val.toFixed(2) : '-'}
      </span>
    )
  } },
    {
      title: '操作', width: 80,
      render: (_, pos) => (
        <Popconfirm title={`确认平仓 ${pos.vt_symbol} ${pos.direction === 'LONG' ? '多' : '空'}单 ${pos.volume} 手？`} onConfirm={() => void closePosition(pos)}>
          <Button size="small" danger loading={closingPos === pos.vt_positionid}>清仓</Button>
        </Popconfirm>
      ),
    },
  ]

  return (
    <>
      {contextHolder}
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
        <div style={{ flex: 1, overflow: 'hidden' }}>
          <Table {...tableProps} rowKey="vt_positionid" columns={columns} dataSource={Object.values(positions).filter(p => p.volume !== 0)} />
        </div>
        <div className="flex shrink-0 flex-wrap gap-x-4 gap-y-0.5 border-t border-[var(--card-border)] bg-[var(--subcard-bg)] px-3 py-1 text-xs text-slate-500 dark:bg-white/[0.04] dark:text-slate-400">
          <span>未实现盈亏: <span className={`mono ${sumPosPnl > 0 ? 'price-up' : sumPosPnl < 0 ? 'price-down' : ''}`}>{sumPosPnl >= 0 ? '+' : ''}{Number.isFinite(Number(sumPosPnl)) ? Number(sumPosPnl).toFixed(2) : '-'}</span></span>
          <span>已实现毛利: <span className={`mono ${sumTradeGross > 0 ? 'price-up' : sumTradeGross < 0 ? 'price-down' : ''}`}>{sumTradeGross >= 0 ? '+' : ''}{Number.isFinite(Number(sumTradeGross)) ? Number(sumTradeGross).toFixed(2) : '-'}</span></span>
          <span>手续费: <span className="mono">{Number.isFinite(Number(sumTradeFee)) ? Number(sumTradeFee).toFixed(2) : '-'}</span></span>
          <span>已实现净盈亏: <span className={`mono ${sumTradePnl > 0 ? 'price-up' : sumTradePnl < 0 ? 'price-down' : ''}`}>{sumTradePnl >= 0 ? '+' : ''}{Number.isFinite(Number(sumTradePnl)) ? Number(sumTradePnl).toFixed(2) : '-'}</span></span>
          <span className="font-semibold">总盈亏: <span className={`mono ${totalPnl > 0 ? 'price-up' : totalPnl < 0 ? 'price-down' : ''}`}>{totalPnl >= 0 ? '+' : ''}{Number.isFinite(Number(totalPnl)) ? Number(totalPnl).toFixed(2) : '-'}</span></span>
        </div>
      </div>
    </>
  )
}

function AccountsTab() {
  const accounts = useTradingStore(s => s.accounts)
  const columns: ColumnsType<Account> = [
    { title: '账户', dataIndex: 'vt_accountid', width: 140 },
    { title: '权益', dataIndex: 'balance', width: 140, className: 'mono', render: (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 2 }) },
    { title: '冻结', dataIndex: 'frozen', width: 120, className: 'mono', render: (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 2 }) },
    { title: '可用', dataIndex: 'available', width: 140, className: 'mono', render: (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 2 }) },
  ]
  return <Table {...tableProps} rowKey="vt_accountid" columns={columns} dataSource={Object.values(accounts)} />
}

function LogsTab() {
  const logs = useTradingStore(s => s.logs)
  return (
    <div style={{ height: 180, overflow: 'auto', padding: '0 4px' }}>
      {logs.map((log: LogEntry, index: number) => (
        <div key={`${log.time}-${log.msg.slice(0, 40)}-${index}`} className="log-line">
          <Text type="secondary">{timeOf(log.time)}</Text>{' '}
          <Text type={log.level >= 30 ? 'danger' : undefined}>[{log.gateway_name}] {log.msg}</Text>
        </div>
      ))}
    </div>
  )
}

// ---- 主组件：仅订阅 WebSocket 状态，不订阅任何交易数据 ----

// 单独的标签组件：只订阅 order/trade 计数，避免整块 BottomTabs 随行情重渲染
function OrdersLabel() {
  const count = useTradingStore(s => Object.keys(s.orders).length)
  return <span>委托 ({count})</span>
}
function TradesLabel() {
  const count = useTradingStore(s => Object.keys(s.trades).length)
  return <span>成交 ({count})</span>
}

export default function BottomTabs() {
  const wsConnected = useWsStore(s => s.connected)
  const [activeTab, setActiveTab] = useState('orders')
  const [messageApi, contextHolder] = message.useMessage()

  // 清除历史（按当前激活的 Tab 区分委托/成交）
  const clearHistory = async () => {
    if (activeTab === 'trades') {
      try {
        const result = await clearTradeHistory()
        useTradingStore.getState().bulkLoad({ trades: [] })
        messageApi.success(`已清除 ${result.deleted} 条成交记录`)
      } catch { messageApi.error('清除失败') }
    } else {
      try {
        const result = await clearOrderHistory()
        useTradingStore.getState().bulkLoad({ orders: [] })
        messageApi.success(`已清除 ${result.deleted} 条委托记录`)
      } catch { messageApi.error('清除失败') }
    }
  }

  // 仅在 WebSocket 断开时才启用兜底轮询
  useEffect(() => {
    if (wsConnected) return
    const timer = setInterval(() => {
      Promise.all([getOrders(), getTrades(), getPositions()])
        .then(([orders, trades, positions]) => {
          useTradingStore.getState().bulkLoad({
            orders: orders as unknown as Order[],
            trades: trades as unknown as Trade[],
            positions: positions as unknown as Position[],
          })
        })
        .catch(() => {})
    }, 3000)
    return () => clearInterval(timer)
  }, [wsConnected])

  return (
    <>
      {contextHolder}
      <Tabs
        size="small"
        className="glass-tabs"
        style={{ height: '100%', padding: '0 4px' }}
        activeKey={activeTab}
        onChange={setActiveTab}
        tabBarExtraContent={
          activeTab === 'orders' || activeTab === 'trades' ? (
            <Popconfirm title={`确认清除所有${activeTab === 'trades' ? '成交' : '委托'}记录？`}
              onConfirm={clearHistory}
            >
              <Button size="small" type="text" danger>清除历史</Button>
            </Popconfirm>
          ) : null
        }
        items={[
          { key: 'orders', label: <OrdersLabel />, children: <OrdersTab /> },
          { key: 'trades', label: <TradesLabel />, children: <TradesTab /> },
          { key: 'positions', label: '持仓', children: <PositionsTab /> },
          { key: 'accounts', label: '资金', children: <AccountsTab /> },
          { key: 'logs', label: '日志', children: <LogsTab /> },
        ]}
      />
    </>
  )
}