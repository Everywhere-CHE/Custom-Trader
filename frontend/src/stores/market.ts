/** 行情数据状态仓库：合约、最新 Tick 行情、当前选中合约。 */

import { create } from 'zustand'
import type { Contract, Tick } from '../types'

interface MarketState {
  contracts: Contract[]
  ticks: Record<string, Tick>
  selected: string
  setContracts: (contracts: Contract[]) => void
  setTicks: (ticks: Record<string, Tick>) => void
  updateTick: (tick: Tick) => void
  select: (vtSymbol: string) => void
}

export const useMarketStore = create<MarketState>(set => ({
  contracts: [],
  ticks: {},
  selected: '',
  setContracts: contracts => set({ contracts }),
  setTicks: ticks => set({ ticks }),
  updateTick: tick =>
    set(state => ({ ticks: { ...state.ticks, [tick.vt_symbol]: tick } })),
  select: vtSymbol => set({ selected: vtSymbol }),
}))
