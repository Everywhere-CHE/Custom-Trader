/** WebSocket 客户端：单条连接、带退避的自动重连、
 * 订阅管理、以及将消息分发到各 zustand 状态仓库。 */

import { create } from 'zustand'
import type {
  Account,
  LogEntry,
  Order,
  Position,
  StrategyData,
  StrategyLogEntry,
  Tick,
  Trade,
  WsMessage,
} from '../types'
import { useMarketStore } from '../stores/market'
import { useStrategyStore } from '../stores/strategy'
import { useTradingStore } from '../stores/trading'
import { notifyOrder, notifyTrade, seedFromSnapshot } from './notifier'
import { getAccounts, getOrders, getPositions, getTrades } from './http'

const ALL_CHANNELS = [
  'tick',
  'order',
  'trade',
  'position',
  'account',
  'log',
  'gateway_status',
  'strategy',
  'strategy_log',
]

interface WsState {
  connected: boolean
  setConnected: (v: boolean) => void
}

export const useWsStore = create<WsState>(set => ({
  connected: false,
  setConnected: v => set({ connected: v }),
}))

class WsClient {
  private ws: WebSocket | null = null
  private retry = 0
  private started = false
  private closedByUser = false
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null

  start(): void {
    if (this.started) return
    this.started = true
    this.closedByUser = false
    this.connect()
  }

  /** 停止客户端：关闭 socket 并取消挂起的重连定时器。 */
  stop(): void {
    this.closedByUser = true
    this.started = false
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.ws?.close()
    this.ws = null
  }

  private connect(): void {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const token = localStorage.getItem('auth_token') || ''
    this.ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`)

    this.ws.onopen = () => {
      this.retry = 0
      useWsStore.getState().setConnected(true)
      this.subscribeAll()
      // 连接（重连）后做一次全量刷新，确保不遗漏任何数据
      void this.refreshSnapshots()
    }

    this.ws.onmessage = event => {
      try {
        const message = JSON.parse(event.data as string) as WsMessage
        this.dispatch(message)
      } catch {
        // 忽略格式错误的帧
      }
    }

    this.ws.onclose = (event: CloseEvent) => {
      useWsStore.getState().setConnected(false)
      if (this.closedByUser) return
      // 1008 = 认证失败（token 无效/过期），停止重连，等用户重新登录
      if (event.code === 1008) return
      const delay = Math.min(1000 * 2 ** this.retry, 15000)
      this.retry += 1
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null
        this.connect()
      }, delay)
    }

    this.ws.onerror = () => {
      this.ws?.close()
    }
  }

  subscribeAll(): void {
    this.send({ action: 'subscribe', channels: ALL_CHANNELS })
  }

  send(payload: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(payload))
    }
  }

  private async refreshSnapshots(): Promise<void> {
    try {
      const [orders, trades, positions, accounts] = await Promise.all([
        getOrders(),
        getTrades(),
        getPositions(),
        getAccounts(),
      ])
      // 在加载状态之前先给通知器播种（去重），这样历史记录
      // 在连接（重连）后绝不会触发“新委托/新成交”的弹窗提醒。
      seedFromSnapshot(
        orders as unknown as Order[],
        trades as unknown as Trade[],
      )
      useTradingStore.getState().bulkLoad({ orders, trades, positions, accounts })
    } catch {
      // 后端可能仍在启动中；后续 WebSocket 推送会补齐数据
    }
  }

  private dispatch(message: WsMessage): void {
    const data = message.data
    switch (message.type) {
      case 'tick':
        useMarketStore.getState().updateTick(data as unknown as Tick)
        break
      case 'order': {
        const order = data as unknown as Order
        useTradingStore.getState().upsertOrder(order)
        notifyOrder(order)
        break
      }
      case 'trade': {
        const trade = data as unknown as Trade
        useTradingStore.getState().upsertTrade(trade)
        notifyTrade(trade)
        break
      }
      case 'position':
        useTradingStore.getState().upsertPosition(data as unknown as Position)
        break
      case 'account':
        useTradingStore.getState().upsertAccount(data as unknown as Account)
        break
      case 'log':
        useTradingStore.getState().appendLog(data as unknown as LogEntry)
        break
      case 'strategy':
        useStrategyStore.getState().upsert(data as unknown as StrategyData)
        break
      case 'strategy_log':
        useStrategyStore
          .getState()
          .appendLog(data as unknown as StrategyLogEntry)
        break
      default:
        break
    }
  }
}

export const wsClient = new WsClient()
