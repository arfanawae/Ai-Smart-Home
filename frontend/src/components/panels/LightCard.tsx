import { Lightbulb, LightbulbOff, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { send } from '../../lib/feed'
import { statusLabel, timeOf } from '../../lib/format'
import { useNexus } from '../../store/nexus'
import { HudPanel, MockBadge } from '../ui/HudPanel'

const PRESETS = [25, 50, 75, 100]
const CONFIRM_TIMEOUT_MS = 8000 // device timeout (3 s) × 2 attempts + margin

type Pending = { label: string; sentAt: number } | null

/**
 * Manual control of the main light. The switch never flips on click: it shows "รอยืนยัน" until the
 * device reports its new state back through the feed (Blueprint principle 3 — confirmed, not optimistic).
 */
export function LightCard({ delay = 0 }: { delay?: number }) {
  const light = useNexus((s) => s.devices.main_light)
  const connection = useNexus((s) => s.connection)
  const demo = useNexus((s) => s.demo)
  const lastManual = useNexus((s) => s.activity.find((c) => c.route === 'manual' && c.device_id === 'main_light'))
  const [pending, setPending] = useState<Pending>(null)
  const [draft, setDraft] = useState<number | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const seen = useRef(lastManual)

  // A new manual command result arrived → the pending action is settled (succeeded or not).
  useEffect(() => {
    if (lastManual && lastManual !== seen.current) {
      seen.current = lastManual
      setPending(null)
      setDraft(null)
      setNotice(lastManual.status === 'succeeded' ? null : `${statusLabel(lastManual.status, lastManual.simulated)}: ${lastManual.error ?? ''}`)
    }
  }, [lastManual])

  useEffect(() => {
    if (!pending) return
    const id = setTimeout(() => {
      setPending(null)
      setDraft(null)
      setNotice('ไม่ได้รับการยืนยันจากอุปกรณ์ — สถานะไฟอาจไม่ตรงกับที่แสดง')
    }, CONFIRM_TIMEOUT_MS)
    return () => clearTimeout(id)
  }, [pending])

  const usable = !demo && connection === 'open' && !!light?.online
  const command = (tool: string, args: Record<string, unknown>, label: string) => {
    if (!usable || pending) return
    if (send({ type: 'command', tool, args: { device_id: 'main_light', ...args } })) {
      setNotice(null)
      setPending({ label, sentAt: Date.now() })
    }
  }

  const on = light?.on ?? false
  const level = draft ?? light?.brightness ?? 0
  const shownLevel = on || draft !== null ? level : 0

  return (
    <HudPanel
      title="Device Control"
      delay={delay}
      action={light?.source === 'mock' ? <MockBadge /> : undefined}
    >
      <div className={`flex flex-col gap-3 ${usable ? '' : 'opacity-60'}`}>
        <div className="flex items-center gap-3">
          <div
            className={`grid size-11 place-items-center rounded-xl border ${on ? 'border-cyan/50 bg-cyan/15 text-cyan shadow-[0_0_18px_rgb(46_197_255/0.35)]' : 'border-line bg-bg-2 text-text-2'}`}
          >
            {on ? <Lightbulb className="size-5" /> : <LightbulbOff className="size-5" />}
          </div>
          <div className="min-w-0 flex-1">
            <div className="font-medium">ไฟห้องหลัก</div>
            <div className="text-xs text-text-2">
              {!light ? 'ไม่ทราบสถานะ' : light.online ? (on ? `เปิด · ${light.brightness}%` : 'ปิด') : 'ออฟไลน์'}
            </div>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={on}
            aria-label="เปิด/ปิดไฟห้องหลัก"
            disabled={!usable || !!pending}
            onClick={() => command(on ? 'turn_light_off' : 'turn_light_on', {}, on ? 'ปิดไฟ' : 'เปิดไฟ')}
            className={`relative h-8 w-14 shrink-0 rounded-full border transition-colors disabled:cursor-not-allowed ${on ? 'border-cyan bg-cyan/30' : 'border-line bg-bg-2'}`}
          >
            <span
              className={`absolute top-1 size-6 rounded-full transition-all ${on ? 'left-7 bg-cyan shadow-[0_0_10px_var(--color-cyan)]' : 'left-1 bg-text-2'}`}
            />
          </button>
        </div>

        <div>
          <div className="flex items-baseline justify-between">
            <span className="text-sm text-text-2">ความสว่าง</span>
            <span className="font-mono text-2xl text-text-1 tabular-nums">
              {light ? shownLevel : "—"}
              <span className="text-sm text-text-2">%</span>
            </span>
          </div>
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={shownLevel}
            disabled={!usable || !!pending}
            aria-label="ความสว่างไฟห้องหลัก"
            className="range"
            style={{ '--fill': `${shownLevel}%` } as CSSProperties}
            onChange={(e) => setDraft(Number(e.target.value))}
            onPointerUp={() => draft !== null && command('set_brightness', { level: draft }, `ตั้ง ${draft}%`)}
            onKeyUp={(e) =>
              ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(e.key) &&
              draft !== null &&
              command('set_brightness', { level: draft }, `ตั้ง ${draft}%`)
            }
          />
          <div className="grid grid-cols-4 gap-2">
            {PRESETS.map((p) => (
              <button
                key={p}
                type="button"
                disabled={!usable || !!pending}
                onClick={() => command('set_brightness', { level: p }, `ตั้ง ${p}%`)}
                className={`h-10 rounded-lg border font-mono text-sm transition-colors disabled:cursor-not-allowed ${
                  on && light?.brightness === p ? 'border-cyan/60 bg-cyan/15 text-cyan' : 'border-line text-text-2 hover:border-line-strong hover:text-text-1'
                }`}
              >
                {p}
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-5 text-xs" aria-live="polite">
          {pending ? (
            <span className="flex items-center gap-1.5 text-amber">
              <Loader2 className="size-3.5 animate-spin" /> {pending.label} — รออุปกรณ์ยืนยัน…
            </span>
          ) : notice ? (
            <span className="text-red">{notice}</span>
          ) : !usable ? (
            <span className="text-text-2">
              {demo ? 'โหมดสาธิต — ปุ่มควบคุมปิดอยู่' : light && !light.online ? 'อุปกรณ์ออฟไลน์' : `ค่าล่าสุดที่ทราบ · ${timeOf(light?.updated_at)}`}
            </span>
          ) : (
            <span className="text-text-2">อัปเดตล่าสุด {timeOf(light?.updated_at)}</span>
          )}
        </div>
      </div>
    </HudPanel>
  )
}
