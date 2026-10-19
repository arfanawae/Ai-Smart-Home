import type { ReactNode } from 'react'

interface Props {
  title: string
  action?: ReactNode
  className?: string
  children: ReactNode
  delay?: number
}

export function HudPanel({ title, action, className = '', children, delay = 0 }: Props) {
  return (
    <section className={`hud-panel panel-in flex min-h-0 flex-col p-4 ${className}`} style={{ animationDelay: `${delay}ms` }}>
      <header className="mb-3 flex items-center justify-between gap-2">
        <h2 className="hud-label">{title}</h2>
        {action}
      </header>
      {children}
    </section>
  )
}

export function MockBadge({ className = '' }: { className?: string }) {
  return (
    <span
      className={`rounded-full border border-amber/50 bg-amber/10 px-2 py-0.5 font-display text-[10px] tracking-[0.18em] text-amber ${className}`}
      title="ข้อมูลอุปกรณ์เป็นการจำลอง — ยังไม่ได้เชื่อมต่ออุปกรณ์จริง"
    >
      MOCK
    </span>
  )
}

type Tone = 'ok' | 'warn' | 'bad' | 'off'
const TONE: Record<Tone, string> = { ok: 'bg-green', warn: 'bg-amber', bad: 'bg-red', off: 'bg-grey' }

export function StatusDot({ tone, pulse = false }: { tone: Tone; pulse?: boolean }) {
  return <span className={`inline-block size-2 shrink-0 rounded-full ${TONE[tone]} ${pulse ? 'pulse-dot' : ''}`} />
}

export function StatusChip({ tone, label, title }: { tone: Tone; label: string; title?: string }) {
  return (
    <span
      className="flex h-7 items-center gap-1.5 rounded-full border border-line bg-bg-1/70 px-2.5 text-xs text-text-2"
      title={title}
    >
      <StatusDot tone={tone} pulse={tone === 'warn'} />
      {label}
    </span>
  )
}

export type { Tone }
