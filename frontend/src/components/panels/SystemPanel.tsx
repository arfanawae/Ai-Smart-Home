import { systemRows } from '../../lib/system'
import { useNexus } from '../../store/nexus'
import { HudPanel, StatusDot } from '../ui/HudPanel'

export function SystemPanel({ delay = 0 }: { delay?: number }) {
  const { connection, info, devices, demo } = useNexus()
  const rows = systemRows(connection, info, devices.main_light, demo)
  return (
    <HudPanel title="System Status" delay={delay}>
      <ul className="flex flex-col gap-2.5 text-sm">
        {rows.map((r) => (
          <li key={r.key} className="flex items-center gap-2.5" title={r.hint}>
            <StatusDot tone={r.tone} />
            <span className="w-28 shrink-0 text-text-2">{r.name}</span>
            <span className="min-w-0 truncate text-text-1">{r.value}</span>
          </li>
        ))}
      </ul>
    </HudPanel>
  )
}
