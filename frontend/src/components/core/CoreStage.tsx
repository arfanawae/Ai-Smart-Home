import { Mic } from 'lucide-react'
import { useState } from 'react'
import { toggleRecording } from '../../lib/browserVoice'
import { VISUALS } from '../../lib/stateVisuals'
import { effectiveState, useNexus } from '../../store/nexus'
import { AICore } from './AICore'
import { Waveform } from './Waveform'

/** Centre of the dashboard: name, status line, the core, the waveform and the latest words. */
export function CoreStage({ compact = false }: { compact?: boolean }) {
  const state = useNexus(effectiveState)
  const since = useNexus((s) => s.ai.since)
  const detail = useNexus((s) => s.ai.detail)
  const info = useNexus((s) => s.info)
  const lastUser = useNexus((s) => s.chat.findLast((c) => c.type === 'user'))
  const connection = useNexus((s) => s.connection)
  const demo = useNexus((s) => s.demo)
  const browserVoice = useNexus((s) => s.browserVoice)
  const [micError, setMicError] = useState<string | null>(null)
  const canTalk = !demo && connection === 'open'
  const visual = VISUALS[state]
  const voiceOn = (info?.languages.length ?? 0) > 0

  const hint =
    micError ??
    (browserVoice === 'recording'
      ? 'กำลังฟังผ่านไมค์ของเบราว์เซอร์ — หยุดพูดแล้วจะส่งเอง (แตะอีกครั้งเพื่อส่งทันที)'
      : state === 'DISCONNECTED'
        ? 'รอการเชื่อมต่อกับ NEXUS'
        : state === 'IDLE'
          ? voiceOn
            ? 'พูด "Nexus" หรือแตะวงกลมเพื่อพูดผ่านเบราว์เซอร์'
            : 'แตะวงกลมเพื่อพูด หรือพิมพ์คำสั่งในช่องสนทนา'
          : detail)

  const talk = () => {
    setMicError(null)
    toggleRecording().catch((e: unknown) => {
      const denied = e instanceof DOMException && (e.name === 'NotAllowedError' || e.name === 'SecurityError')
      setMicError(denied ? 'เบราว์เซอร์ไม่ได้รับอนุญาตให้ใช้ไมค์ — กดไอคอนกุญแจข้าง URL แล้วอนุญาต Microphone' : `เปิดไมค์ไม่ได้: ${String(e)}`)
    })
  }

  return (
    // w-full: the core size must come from the column, not from how long the hint text happens to be
    <section className="panel-in flex w-full flex-col items-center text-center" aria-live="polite">
      <h1 className={`font-display font-bold tracking-[0.5em] text-text-1 drop-shadow-[0_0_14px_rgb(46_197_255/0.45)] ${compact ? 'text-2xl' : 'text-3xl xl:text-4xl'}`}>
        NEXUS
      </h1>
      <p className="mt-2 font-display text-[11px] tracking-[0.3em] text-text-2">
        CORE STATUS:{' '}
        <span style={{ color: `var(${visual.color})` }} className="transition-colors duration-300">
          {visual.title}
        </span>
      </p>
      {/* xl: also bounded by screen height, so the dashboard fits a 1440×900 / 1366×768 laptop without scrolling */}
      <div className={`my-3 w-full ${compact ? 'max-w-[240px]' : 'max-w-[440px] xl:max-w-[clamp(220px,calc(100dvh-37rem),500px)]'}`}>
        <button
          type="button"
          onClick={talk}
          disabled={!canTalk}
          aria-pressed={browserVoice === 'recording'}
          aria-label={browserVoice === 'recording' ? 'ส่งเสียงที่พูดให้ NEXUS' : 'แตะเพื่อพูดกับ NEXUS'}
          className="group relative block w-full rounded-full focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-cyan disabled:cursor-default enabled:cursor-pointer"
        >
          <AICore state={state} since={since} />
          {canTalk && (
            <span
              className={`pointer-events-none absolute bottom-[6%] left-1/2 flex size-9 -translate-x-1/2 items-center justify-center rounded-full border backdrop-blur transition-all ${
                browserVoice === 'recording'
                  ? 'border-cyan bg-cyan/25 text-cyan shadow-[0_0_16px_var(--color-cyan)]'
                  : 'border-line bg-bg-1/70 text-text-2 opacity-70 group-hover:opacity-100'
              }`}
              aria-hidden="true"
            >
              <Mic className="size-4" />
            </span>
          )}
        </button>
      </div>
      <Waveform state={state} />
      <p className="mt-3 text-lg font-medium" style={{ color: `var(${visual.color})` }}>
        {visual.label}
      </p>
      <p className="mt-1 min-h-5 max-w-md truncate text-sm text-text-2">{hint}</p>
      {!compact && (
        // Always rendered (min-h) so the first message doesn't re-centre and nudge the core.
        <p className="mt-2 min-h-5 max-w-md truncate text-sm text-text-1/80">{lastUser ? `“${lastUser.text}”` : ''}</p>
      )}
    </section>
  )
}
