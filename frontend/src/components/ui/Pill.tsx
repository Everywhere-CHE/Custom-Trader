/** 半透明状态胶囊标签：替代高饱和的 antd Tag 状态色。 */

export type PillTone = 'slate' | 'emerald' | 'rose' | 'amber' | 'sky' | 'indigo'

const TONES: Record<PillTone, string> = {
  slate: 'bg-slate-500/10 text-slate-500 dark:text-slate-400',
  emerald: 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-300',
  rose: 'bg-rose-500/10 text-rose-600 dark:text-rose-300',
  amber: 'bg-amber-500/10 text-amber-600 dark:text-amber-300',
  sky: 'bg-sky-500/10 text-sky-600 dark:text-sky-300',
  indigo: 'bg-indigo-500/10 text-indigo-600 dark:text-indigo-300',
}

export default function Pill({
  children,
  tone = 'slate',
}: {
  children: React.ReactNode
  tone?: PillTone
}) {
  return (
    <span
      className={`inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] leading-tight ${TONES[tone]}`}
    >
      {children}
    </span>
  )
}
