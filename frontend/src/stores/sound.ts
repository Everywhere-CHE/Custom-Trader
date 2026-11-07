/** 提示音开关状态。 */

import { create } from 'zustand'

interface SoundState {
  enabled: boolean
  toggle: () => void
  setEnabled: (v: boolean) => void
}

export const useSoundStore = create<SoundState>(set => ({
  enabled: true,
  toggle: () => set(s => ({ enabled: !s.enabled })),
  setEnabled: v => set({ enabled: v }),
}))