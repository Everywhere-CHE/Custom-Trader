/** 极光背景：3-4 个缓慢漂浮的彩色光团（径向渐变 + 大半径模糊），
 * 亮色为淡蓝/淡紫/淡粉马卡龙色，暗色为同色系低透明度版本。 */

import { motion } from 'framer-motion'

interface Blob {
  className: string
  color: string
  darkColor: string
  drift: { x: number[]; y: number[] }
  duration: number
}

const BLOBS: Blob[] = [
  {
    className: 'left-[-6%] top-[-12%] h-[480px] w-[480px]',
    color: 'rgba(199, 210, 254, 0.95)',
    darkColor: 'rgba(99, 102, 241, 0.22)',
    drift: { x: [0, 70, -20, 0], y: [0, 50, 90, 0] },
    duration: 26,
  },
  {
    className: 'right-[-8%] top-[18%] h-[540px] w-[540px]',
    color: 'rgba(233, 213, 255, 0.95)',
    darkColor: 'rgba(167, 139, 250, 0.2)',
    drift: { x: [0, -80, -30, 0], y: [0, 60, -40, 0] },
    duration: 32,
  },
  {
    className: 'left-[28%] bottom-[-18%] h-[500px] w-[500px]',
    color: 'rgba(254, 205, 211, 0.9)',
    darkColor: 'rgba(244, 63, 94, 0.14)',
    drift: { x: [0, 60, 110, 0], y: [0, -50, -10, 0] },
    duration: 28,
  },
  {
    className: 'left-[6%] top-[52%] h-[360px] w-[360px]',
    color: 'rgba(191, 219, 254, 0.9)',
    darkColor: 'rgba(56, 189, 248, 0.16)',
    drift: { x: [0, 40, -60, 0], y: [0, -60, -20, 0] },
    duration: 24,
  },
]

export default function AuroraBackground() {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      {BLOBS.map((blob, i) => (
        <motion.div
          key={i}
          className={`absolute rounded-full blur-[100px] dark:hidden ${blob.className}`}
          style={{ background: `radial-gradient(circle, ${blob.color} 0%, transparent 70%)` }}
          animate={blob.drift}
          transition={{ duration: blob.duration, repeat: Infinity, ease: 'easeInOut' }}
        />
      ))}
      {BLOBS.map((blob, i) => (
        <motion.div
          key={`dark-${i}`}
          className={`absolute hidden rounded-full blur-[110px] dark:block ${blob.className}`}
          style={{ background: `radial-gradient(circle, ${blob.darkColor} 0%, transparent 70%)` }}
          animate={blob.drift}
          transition={{ duration: blob.duration, repeat: Infinity, ease: 'easeInOut' }}
        />
      ))}
    </div>
  )
}
