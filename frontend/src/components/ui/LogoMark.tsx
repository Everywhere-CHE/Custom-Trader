/** 品牌 Logo：主题色渐变圆角底 + 三根上升 K 线（与 favicon 同一设计语言，
 *  在侧边栏深蓝底上用白色蜡烛保证对比度）。 */

interface Props {
  /** 渲染尺寸（px），图形按 32 视窗等比缩放 */
  size?: number
}

export default function LogoMark({ size = 32 }: Props) {
  return (
    <svg
      viewBox="0 0 32 32"
      width={size}
      height={size}
      aria-hidden="true"
      style={{ display: 'block', flexShrink: 0 }}
    >
      <defs>
        <linearGradient id="lm-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style={{ stopColor: 'var(--accent)' }} />
          <stop
            offset="1"
            style={{
              stopColor: 'color-mix(in srgb, var(--accent) 70%, #000)',
            }}
          />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill="url(#lm-bg)" />
      {/* 三根上升 K 线（影线在实体后面） */}
      <rect x="7.45" y="14.5" width="1.6" height="11.5" rx="0.8" fill="#fff" opacity="0.5" />
      <rect x="5.5" y="17" width="5.5" height="6.5" rx="1.2" fill="#fff" opacity="0.8" />

      <rect x="15.2" y="10.5" width="1.6" height="12.5" rx="0.8" fill="#fff" opacity="0.6" />
      <rect x="13.25" y="13" width="5.5" height="7.5" rx="1.2" fill="#fff" opacity="0.92" />

      <rect x="22.95" y="6" width="1.6" height="14" rx="0.8" fill="#fff" opacity="0.75" />
      <rect x="21" y="8.5" width="5.5" height="9" rx="1.2" fill="#fff" />
    </svg>
  )
}
