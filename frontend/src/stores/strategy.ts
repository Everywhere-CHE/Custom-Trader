/** 策略状态仓库：策略实例与策略日志。 */

import { create } from 'zustand'
import type { StrategyData, StrategyLogEntry } from '../types'

const MAX_LOGS = 300

interface StrategyState {
  strategies: Record<string, StrategyData>
  logs: StrategyLogEntry[]
  upsert: (s: StrategyData) => void
  remove: (name: string) => void
  setAll: (list: StrategyData[]) => void
  appendLog: (log: StrategyLogEntry) => void
}

export const useStrategyStore = create<StrategyState>(set => ({
  strategies: {},
  logs: [],
  upsert: s =>
    set(state => ({
      strategies: { ...state.strategies, [s.strategy_name]: s },
    })),
  remove: name =>
    set(state => {
      const next = { ...state.strategies }
      delete next[name]
      return { strategies: next }
    }),
  setAll: list =>
    set({
      strategies: Object.fromEntries(list.map(s => [s.strategy_name, s])),
    }),
  appendLog: log =>
    set(state => ({ logs: [log, ...state.logs].slice(0, MAX_LOGS) })),
}))
