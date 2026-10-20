import { Lightbulb, LightbulbOff, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { send } from '../../lib/feed'
import { mqttEnabled, publishLight } from '../../lib/mqttLight'
import { statusLabel, timeOf } from '../../lib/format'
import { useNexus } from '../../store/nexus'
import { HudPanel, MockBadge } from '../ui/HudPanel'

const PRESETS = [25, 50, 75, 100]
const CONFIRM_TIMEOUT_MS = 8000 // device timeout (3 s) × 2 attempts + margin
// Floor between two commands while dragging. The real pace is set by the board, which fetches
// commands from the relay every 300 ms: only one command is in flight, and the newest slider value
// replaces any older one still waiting here — so the relay never drops (= fails) a command.
const MIN_INTERVAL_MS = 80

type Command = { tool: string; args: Record<string, unknown>; label: string; level?: number }
type Pending = { label: string; sentAt: number } | null
type Queue = { next: Command | null; inFlight: boolean; lastSentAt: number; timer: number; dragging: boolean }

// Dimmer rule (Apple Home / Google Home / Hue): 0% = off, 1–100% = on at that level.
const levelCommand = (level: number): Command =>
  level === 0
    ? { tool: 'turn_light_off', args: {}, label: 'ปิดไฟ', level }
    : { tool: 'set_brightness', args: { level }, label: `ตั้ง ${level}%`, level }

export function LightCard({ delay = 0 }: { delay?: number }) {
  const light = useNexus((s) => s.devices.main_light)
  const connection = useNexus((s) => s.connection)
  const demo = useNexus((s) => s.demo)
  const mqtt = useNexus((s) => s.mqtt)
  const viaMqtt = mqttEnabled && mqtt === 'open' // light commands go through the broker
  const lastManual = useNexus((s) => s.activity.find((c) => c.route === 'manual' && c.device_id === 'main_light'))
  const lastAny = useNexus((s) => s.activity.find((c) => c.device_id === 'main_light'))
  const [pending, setPending] = useState<Pending>(null)
  const [draft, setDraft] = useState<number | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const seen = useRef(lastManual)

  const usable = !demo && (connection === 'open' || viaMqtt) && !!light?.online
  const on = light?.on ?? false

  // ── command queue: real-time while dragging, latest wins, one in flight ─────────────────────
  const queue = useRef<Queue>({ next: null, inFlight: false, lastSentAt: 0, timer: 0, dragging: false })
  const device = useRef({ usable, on, brightness: light?.brightness ?? 0, viaMqtt })
  const flush = useRef(() => {})
  useEffect(() => {
    device.current = { usable, on, brightness: light?.brightness ?? 0, viaMqtt }
  })

  const sendNext = () => {
    const q = queue.current
    const d = device.current
    if (q.inFlight || !q.next || !d.usable) return
    const cmd = q.next
    if (cmd.level !== undefined && (cmd.level === 0 ? !d.on : d.on && d.brightness === cmd.level)) {
      q.next = null // the light is already there
      if (!q.dragging) setDraft(null)
      return
    }
    const wait = q.lastSentAt + MIN_INTERVAL_MS - Date.now()
    if (wait > 0) {
      clearTimeout(q.timer)
      q.timer = window.setTimeout(() => flush.current(), wait)
      return
    }
    q.next = null
    const sent = d.viaMqtt
      ? publishLight(cmd.tool !== 'turn_light_off', cmd.level ?? (d.brightness > 0 ? d.brightness : 100))
      : send({ type: 'command', tool: cmd.tool, args: { device_id: 'main_light', ...cmd.args } })
    if (!sent) {
      setDraft(null) // not sent: show the device's real state again
      return
    }
    q.inFlight = true
    q.lastSentAt = Date.now()
    setNotice(null)
    setPending({ label: cmd.label, sentAt: q.lastSentAt })
  }
  useEffect(() => {
    flush.current = sendNext
  })

  const enqueue = (cmd: Command) => {
    queue.current.next = cmd // replaces anything older that has not been sent yet
    flush.current()
  }

  // Slider, keyboard and presets: the display moves at once; the light follows through the queue.
  const setLevel = (value: number) => {
    const level = Math.max(0, Math.min(100, Math.round(value)))
    setDraft(level)
    enqueue(levelCommand(level))
  }

  const startDrag = () => {
    if (queue.current.dragging) return // pointerdown and touchstart can both fire for one touch
    queue.current.dragging = true
    // Released outside the slider still counts, so listen on the window; either event ends the drag.
    const drag = new AbortController()
    const end = () => {
      drag.abort()
      const q = queue.current
      q.dragging = false
      flush.current() // the exact final position is already queued: it goes out after the one in flight
      if (!q.inFlight && !q.next) setDraft(null)
    }
    window.addEventListener('pointerup', end, { signal: drag.signal })
    window.addEventListener('pointercancel', end, { signal: drag.signal })
    // Older iOS Safari reports touches without pointer events.
    window.addEventListener('touchend', end, { signal: drag.signal })
    window.addEventListener('touchcancel', end, { signal: drag.signal })
  }

  // Turning on restores the last level the device kept (the backend uses 100% if that was 0).
  // Goes through the same queue, so it is never blocked by — or overtaken by — a slider command.
  const toggle = () => enqueue(on ? { tool: 'turn_light_off', args: {}, label: 'ปิดไฟ' } : { tool: 'turn_light_on', args: {}, label: 'เปิดไฟ' })

  useEffect(() => {
    if (lastManual && lastManual !== seen.current) {
      seen.current = lastManual
      const q = queue.current
      q.inFlight = false
      setPending(null)
      setNotice(lastManual.status === 'succeeded' ? null : `${statusLabel(lastManual.status, lastManual.simulated)}: ${lastManual.error ?? ''}`)
      if (lastManual.status === 'succeeded') flush.current() // the newest slider value, if any
      else q.next = null // stop after a failure; the notice says why
      if (!q.dragging && !q.inFlight && !q.next) setDraft(null)
    }
  }, [lastManual])

  // Latest wins across sources: a voice/text command is newer than anything still queued here.
  const seenAny = useRef(lastAny)
  useEffect(() => {
    if (!lastAny || lastAny === seenAny.current) return
    seenAny.current = lastAny
    if (lastAny.route === 'manual') return
    const q = queue.current
    q.next = null
    clearTimeout(q.timer)
    if (!q.dragging && !q.inFlight) setDraft(null)
  }, [lastAny])

  // MQTT: the board's state message is the confirmation (there is no NEXUS command result).
  useEffect(() => {
    const q = queue.current
    if (!viaMqtt || !q.inFlight || !light) return
    q.inFlight = false
    setPending(null)
    flush.current()
    if (!q.dragging && !q.inFlight && !q.next) setDraft(null)
  }, [light, viaMqtt])

  // Board went offline mid-command: nothing will confirm it, so stop waiting (the display below
  // already ignores the draft and the spinner while the card is not usable).
  useEffect(() => {
    if (light?.online === false) Object.assign(queue.current, { inFlight: false, next: null })
  }, [light?.online])

  useEffect(() => {
    if (!pending) return
    const id = setTimeout(() => {
      Object.assign(queue.current, { inFlight: false, next: null })
      setPending(null)
      setDraft(null)
      setNotice('ไม่ได้รับการยืนยันจากอุปกรณ์ — สถานะไฟอาจไม่ตรงกับที่แสดง')
    }, CONFIRM_TIMEOUT_MS)
    return () => clearTimeout(id)
  }, [pending])

  useEffect(() => {
    const q = queue.current
    return () => clearTimeout(q.timer)
  }, [])

  // Not usable (board offline / feed lost): show only what the device last reported.
  const shownDraft = usable ? draft : null
  const waiting = usable ? pending : null
  const level = shownDraft ?? light?.brightness ?? 0
  const shownLevel = on || shownDraft !== null ? level : 0

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
            disabled={!usable}
            onClick={toggle}
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
            disabled={!usable}
            aria-label="ความสว่างไฟห้องหลัก"
            className="range"
            style={{ '--fill': `${shownLevel}%` } as CSSProperties}
            onChange={(e) => setLevel(Number(e.target.value))}
            onPointerDown={startDrag}
            onTouchStart={startDrag}
          />
          <div className="grid grid-cols-4 gap-2">
            {PRESETS.map((p) => (
              <button
                key={p}
                type="button"
                disabled={!usable}
                onClick={() => setLevel(p)}
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
          {waiting ? (
            <span className="flex items-center gap-1.5 text-amber">
              <Loader2 className="size-3.5 animate-spin" /> {waiting.label} — รออุปกรณ์ยืนยัน…
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