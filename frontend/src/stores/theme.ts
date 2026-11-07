/** 主题状态仓库：仅亮 / 暗两种主题，localStorage 持久化。 */

import { create } from 'zustand'

export type ThemeMode = 'light' | 'dark'

/** 全站固定主色（科技蓝），亮暗主题共用 */
export const DEFAULT_ACCENT = '#1677ff'

const STORAGE_KEY = 'custom-trader-theme'
/** 项目改名前的旧存储键，读取后迁移并清理 */
const LEGACY_KEY = 'quant-trader-theme'

function loadInitial(): ThemeMode {
  let saved = localStorage.getItem(STORAGE_KEY)
  if (saved === null) {
    // 旧版本存储键迁移
    saved = localStorage.getItem(LEGACY_KEY)
    if (saved !== null) {
      try {
        localStorage.setItem(STORAGE_KEY, saved)
        localStorage.removeItem(LEGACY_KEY)
      } catch {
        // ignore
      }
    }
  }
  return saved === 'light' ? 'light' : 'dark'
}

interface ThemeState {
  mode: ThemeMode
  toggle: () => void
  setMode: (mode: ThemeMode) => void
}

export const useThemeStore = create<ThemeState>(set => ({
  mode: loadInitial(),
  toggle: () =>
    set(state => {
      const mode: ThemeMode = state.mode === 'dark' ? 'light' : 'dark'
      localStorage.setItem(STORAGE_KEY, mode)
      document.body.dataset.theme = mode
      return { mode }
    }),
  setMode: mode => {
    localStorage.setItem(STORAGE_KEY, mode)
    document.body.dataset.theme = mode
    set({ mode })
  },
}))

// 模块加载时立即应用主题；清理早期多主题方案的遗留存储键
document.body.dataset.theme = loadInitial()
try {
  localStorage.removeItem('quant-trader-accent')
  localStorage.removeItem('quant-trader-wallpaper')
} catch {
  // ignore
}
