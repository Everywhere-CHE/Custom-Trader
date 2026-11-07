/** 玻璃态页面容器：为交易终端之外的页面提供统一的玻璃面板 + 页内
 *  antd 卡片/表格半透明化（配合 global.css 的 .glass-page 规则）。 */

import type { ReactNode } from 'react'
import GlassCard from './GlassCard'

export default function GlassPage({ children }: { children: ReactNode }) {
  return (
    <GlassCard noLift className="glass-page overflow-hidden p-3">
      {children}
    </GlassCard>
  )
}
