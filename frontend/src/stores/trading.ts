/** 交易状态仓库：委托 / 成交 / 持仓 / 账户 / 日志。 */

import { create } from 'zustand'
import type { Account, LogEntry, Order, Position, Trade } from '../types'

const MAX_LOGS = 500
const MAX_ORDERS = 1000
const MAX_TRADES = 1000

interface TradingState {
  orders: Record<string, Order>
  trades: Record<string, Trade>
  positions: Record<string, Position>
  accounts: Record<string, Account>
  logs: LogEntry[]
  upsertOrder: (order: Order) => void
  upsertTrade: (trade: Trade) => void
  upsertPosition: (position: Position) => void
  upsertAccount: (account: Account) => void
  appendLog: (log: LogEntry) => void
  bulkLoad: (data: {
    orders?: Order[]
    trades?: Trade[]
    positions?: Position[]
    accounts?: Account[]
    logs?: LogEntry[]
  }) => void
}

export const useTradingStore = create<TradingState>(set => ({
  orders: {},
  trades: {},
  positions: {},
  accounts: {},
  logs: [],
  upsertOrder: order =>
    set(state => {
      const orders = { ...state.orders, [order.vt_orderid]: order }
      const keys = Object.keys(orders)
      if (keys.length > MAX_ORDERS) {
        const sorted = keys.sort((a, b) => (orders[b].datetime ?? '').localeCompare(orders[a].datetime ?? ''))
        for (const k of sorted.slice(MAX_ORDERS)) delete orders[k]
      }
      return { orders }
    }),
  upsertTrade: trade =>
    set(state => {
      const trades = { ...state.trades, [trade.vt_tradeid]: trade }
      const keys = Object.keys(trades)
      if (keys.length > MAX_TRADES) {
        const sorted = keys.sort((a, b) => (trades[b].datetime ?? '').localeCompare(trades[a].datetime ?? ''))
        for (const k of sorted.slice(MAX_TRADES)) delete trades[k]
      }
      return { trades }
    }),
  upsertPosition: position =>
    set(state => ({
      positions: { ...state.positions, [position.vt_positionid]: position },
    })),
  upsertAccount: account =>
    set(state => ({
      accounts: { ...state.accounts, [account.vt_accountid]: account },
    })),
  appendLog: log =>
    set(state => ({ logs: [log, ...state.logs].slice(0, MAX_LOGS) })),
  bulkLoad: data =>
    set(state => {
      const next = { ...state }
      if (data.orders) {
        next.orders = Object.fromEntries(data.orders.map(o => [o.vt_orderid, o]))
        const keys = Object.keys(next.orders)
        if (keys.length > MAX_ORDERS) {
          const sorted = keys.sort((a, b) => (next.orders[b].datetime ?? '').localeCompare(next.orders[a].datetime ?? ''))
          for (const k of sorted.slice(MAX_ORDERS)) delete next.orders[k]
        }
      }
      if (data.trades) {
        next.trades = Object.fromEntries(data.trades.map(t => [t.vt_tradeid, t]))
        const keys = Object.keys(next.trades)
        if (keys.length > MAX_TRADES) {
          const sorted = keys.sort((a, b) => (next.trades[b].datetime ?? '').localeCompare(next.trades[a].datetime ?? ''))
          for (const k of sorted.slice(MAX_TRADES)) delete next.trades[k]
        }
      }
      if (data.positions) {
        next.positions = Object.fromEntries(
          data.positions.map(p => [p.vt_positionid, p]),
        )
      }
      if (data.accounts) {
        next.accounts = Object.fromEntries(
          data.accounts.map(a => [a.vt_accountid, a]),
        )
      }
      if (data.logs) next.logs = data.logs.slice(0, MAX_LOGS)
      return next
    }),
}))
