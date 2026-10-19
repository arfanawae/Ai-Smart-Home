import { Bell } from 'lucide-react'
import { useEffect, useState } from 'react'
import { systemRows } from '../../lib/system'
import { useNexus } from '../../store/nexus'
import { MockBadge, StatusChip, StatusDot } from '../ui/HudPanel'

function useClock(): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return now
}

export function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <svg viewBox="0 0 40 40" className="size-8 text-cyan" aria-hidden="true">
        <polygon points="20,2 36,11 36,29 20,38 4,29 4,11" fill="none" stroke="currentColor" strokeWidth="2" />
        <circle cx="20" cy="20" r="6" fill="currentColor" />
      </svg>
      <div className="leading-none">
        <div className="font-display text-base font-bold tracking-[0.32em] text-text-1">NEXUS</div>
        <div className="mt-1 hidden font-display text-[9px] tracking-[0.25em] text-text-2 sm:block">SMART HOME AI</div>
      </div>
    </div>
  )
}

export function TopBar() {
  const now = useClock()
  const { connection, info, devices, demo, activity } = useNexus()
  const rows = systemRows(connection, info, devices.main_light, demo)
  const problems = activity.filter((c) => c.status !== 'succeeded').length

  return (
    <header className="sticky top-0 z-20 flex h-14 items-center gap-2 border-b border-line bg-bg-0/80 px-3 backdrop-blur-md sm:gap-3 sm:px-4">
      <Logo />
      <div className="ml-4 hidden font-mono text-sm text-text-2 lg:block">
        {now.toLocaleDateString('th-TH', { weekday: 'short', day: 'numeric', month: 'short' })}{' '}
        <span className="text-text-1 tabular-nums">{now.toLocaleTimeString('th-TH', { hour12: false })}</span>
      </div>
      <div className="ml-auto hidden items-center gap-2 xl:flex">
        {rows.map((r) => (
          <StatusChip key={r.key} tone={r.tone} label={r.name} title={`${r.name}: ${r.value}${r.hint ? ` — ${r.hint}` : ''}`} />
        ))}
      </div>
      <div className="ml-auto flex items-center gap-1.5 xl:hidden" aria-label="สถานะระบบ">
        {rows.map((r) => (
          <span key={r.key} title={`${r.name}: ${r.value}`}>
            <StatusDot tone={r.tone} />
          </span>
        ))}
      </div>
      {(info?.simulated ?? true) && <MockBadge className={demo ? "hidden sm:inline" : ""} />}
      {demo && (
        <span className="rounded-full border border-violet/60 bg-violet/15 px-2 py-0.5 font-display text-[10px] tracking-[0.18em] text-violet">
          DEMO
        </span>
      )}
      <button
        type="button"
        className="relative grid size-9 place-items-center rounded-lg text-text-2 hover:bg-bg-2 hover:text-text-1"
        aria-label={`การแจ้งเตือน${problems ? ` ${problems} รายการ` : ''}`}
        title={problems ? `มีคำสั่งไม่สำเร็จ ${problems} รายการในรอบนี้` : 'ไม่มีการแจ้งเตือน'}
      >
        <Bell className="size-5" />
        {problems > 0 && (
          <span className="absolute -top-0.5 -right-0.5 grid min-w-4 place-items-center rounded-full bg-red px-1 text-[10px] font-bold text-bg-0">
            {problems}
          </span>
        )}
      </button>
    </header>
  )
}
