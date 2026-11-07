/** ARL 风格卡片：主题表面色 + 细边框 + 弥散阴影，hover 上浮；
 *  深/浅色与壁纸半透明全部由 CSS 变量（--card-bg 等）驱动。 */

import type { ReactNode } from 'react'

interface Props {
  children: ReactNode
  className?: string
  /** 禁用 hover 上浮（适用于大面积面板，如 K 线区） */
  noLift?: boolean
}

export default function GlassCard({ children, className = '', noLift = false }: Props) {
  return (
    <div
      className={`rounded-xl border bg-[var(--card-bg)] shadow-[var(--card-shadow)] transition-all duration-300 ${
        noLift
          ? 'border-[var(--card-border)]'
          : 'border-[var(--card-border)] hover:-translate-y-1 hover:shadow-[var(--card-shadow-hover)]'
      } ${className}`}
    >
      {children}
    </div>
  )
}
