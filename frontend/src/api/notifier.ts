/** 全局委托/成交通知器。
 *
 * 由 WebSocket 客户端提供实时的委托/成交推送（快照刷新
 * 绝不触发通知）。按 (vt_orderid, status) / vt_tradeid 去重，
 * 使重连后的重复推送不会造成刷屏。
 *
 * 真正的 antd 通知 api 由 ConfigProvider 内部的组件通过
 * setNotifier() 注入（感知主题）。
 */

import type { NotificationInstance } from 'antd/es/notification/interface'
import type { Order, Trade } from '../types'

let api: NotificationInstance | null = null

export function setNotifier(instance: NotificationInstance | null): void {
  api = instance
}

// ---- 去重状态（有上限）----
const seenOrderStatus = new Map<string, string>() // vt_orderid -> 上一次状态
const seenTrades = new Set<string>() // vt_tradeid
const MAX_TRACKED = 1000

function trimMaps(): void {
  while (seenOrderStatus.size > MAX_TRACKED) {
    const first = seenOrderStatus.keys().next().value
    if (first === undefined) break
    seenOrderStatus.delete(first)
  }
  while (seenTrades.size > MAX_TRACKED) {
    const first = seenTrades.keys().next().value
    if (first === undefined) break
    seenTrades.delete(first)
  }
}

const DIRECTION_TEXT: Record<string, string> = {
  LONG: '买入',
  SHORT: '卖出',
}
const OFFSET_TEXT: Record<string, string> = {
  OPEN: '开仓',
  CLOSE: '平仓',
  CLOSETODAY: '平今',
  CLOSEYESTERDAY: '平昨',
  NONE: '',
}

function describe(o: {
  vt_symbol: string
  direction: string | null
  offset: string | null
  volume: number
  price: number
}): string {
  const dir = DIRECTION_TEXT[o.direction ?? ''] ?? o.direction ?? ''
  const off = OFFSET_TEXT[o.offset ?? ''] ?? o.offset ?? ''
  const px = o.price ? ` @ ${o.price}` : ''
  return `${o.vt_symbol} ${dir}${off} ${o.volume}手${px}`
}

/** 从快照（页面加载 / WebSocket 重连）播种去重状态，
 * 使已存在的委托/成交绝不产生通知。 */
export function seedFromSnapshot(orders: Order[], trades: Trade[]): void {
  for (const o of orders) seenOrderStatus.set(o.vt_orderid, o.status)
  for (const t of trades) seenTrades.add(t.vt_tradeid)
  trimMaps()
}

/** 每次收到实时委托推送时调用。状态变化时才通知。 */
export function notifyOrder(order: Order): void {
  if (!api) return
  const prev = seenOrderStatus.get(order.vt_orderid)
  if (prev === order.status) return
  seenOrderStatus.set(order.vt_orderid, order.status)
  trimMaps()

  const key = `order-${order.vt_orderid}` // 同一委托就地更新
  const desc = describe(order)
  const source = order.reference?.startsWith('STR.')
    ? `策略 ${order.reference.slice(4)}`
    : ''

  switch (order.status) {
    case 'SUBMITTING':
    case 'NOTTRADED':
      // 只在下单时通知一次（部分 Gateway 会跳过 SUBMITTING
      // 直接推送 NOTTRADED；去重逻辑对两条路径都能处理）
      if (prev === 'SUBMITTING' && order.status === 'NOTTRADED') return
      api.info({
        key,
        message: source ? `委托已提交（${source}）` : '委托已提交',
        description: `${desc}｜${order.vt_orderid}`,
        placement: 'bottomRight',
        duration: 3,
      })
      break
    case 'PARTTRADED':
      api.warning({
        key,
        message: '部分成交',
        description: `${desc}｜已成交 ${order.traded}/${order.volume}`,
        placement: 'bottomRight',
        duration: 4,
      })
      break
    case 'ALLTRADED':
      // 全部成交由成交推送来上报（带成交价格）；
      // 此处避免对同一事件重复通知。
      break
    case 'CANCELLED':
      api.info({
        key,
        message: '委托已撤销',
        description: desc,
        placement: 'bottomRight',
        duration: 3,
      })
      break
    case 'REJECTED':
      api.error({
        key,
        message: source ? `委托被拒（${source}）` : '委托被拒',
        description: `${desc}｜${order.vt_orderid}`,
        placement: 'bottomRight',
        duration: 6,
      })
      break
    default:
      break
  }
}

/** 每次收到实时成交（成交回报）推送时调用。 */
export function notifyTrade(trade: Trade): void {
  if (!api) return
  if (seenTrades.has(trade.vt_tradeid)) return
  seenTrades.add(trade.vt_tradeid)
  trimMaps()

  api.success({
    key: `trade-${trade.vt_tradeid}`,
    message: '成交通知',
    description: describe(trade),
    placement: 'bottomRight',
    duration: 4,
  })
}
