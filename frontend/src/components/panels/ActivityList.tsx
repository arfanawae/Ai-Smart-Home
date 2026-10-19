import { actionLabel, ROUTE_LABEL, STATUS_COLOR, statusLabel, timeOf } from '../../lib/format'
import { useNexus } from '../../store/nexus'
import { HudPanel } from '../ui/HudPanel'

export function ActivityList({ limit, className = '', delay = 0 }: { limit?: number; className?: string; delay?: number }) {
  const activity = useNexus((s) => s.activity)
  const rows = limit ? activity.slice(0, limit) : activity

  return (
    <HudPanel title="System Activity" className={className} delay={delay}>
      {rows.length === 0 ? (
        <p className="py-3 text-sm text-text-2">ยังไม่มีคำสั่งในรอบนี้</p>
      ) : (
        <ul className="min-h-0 flex-1 divide-y divide-line overflow-y-auto">
          {rows.map((c, i) => (
            <li key={`${c.ts}-${i}`} className="grid grid-cols-[4.5rem_1fr_auto] items-center gap-x-3 gap-y-0.5 py-2 text-sm sm:grid-cols-[5rem_1fr_7rem_auto]">
              <span className="font-mono text-xs text-text-2 tabular-nums">{timeOf(c.ts)}</span>
              <span className="truncate">{actionLabel(c)}</span>
              <span className="hidden text-xs text-text-2 sm:block">{ROUTE_LABEL[c.route]}</span>
              <span className={`text-right text-xs ${STATUS_COLOR[c.status]}`}>
                {statusLabel(c.status, c.simulated)}
                {c.latency_ms !== null && <span className="ml-1.5 font-mono text-text-2">{c.latency_ms}ms</span>}
              </span>
              {c.error && <span className="col-span-full text-xs text-red sm:col-start-2">{c.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </HudPanel>
  )
}
