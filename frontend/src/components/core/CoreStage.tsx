import { MicOff } from 'lucide-react'
import { useState } from 'react'
import { micErrorMessage, stopListening, tapToTalk } from '../../lib/browserVoice'
import { VISUALS } from '../../lib/stateVisuals'
import { effectiveState, useNexus } from '../../store/nexus'
import { AICore } from './AICore'
import { MicButton, type MicMode } from './MicButton'
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
  const continuous = useNexus((s) => s.continuous)
  const [micError, setMicError] = useState<string | null>(null)
  const canTalk = !demo && connection === 'open'
  const micMode: MicMode = !canTalk ? 'off' : browserVoice === 'recording' ? 'recording' : continuous ? 'busy' : 'idle'
  const visual = VISUALS[state]
  const voiceOn = (info?.languages.length ?? 0) > 0

  const hint =
    micError ??
    (browserVoice === 'recording'
      ? 'พูดได้เลย — หยุดพูดแล้วจะส่งเอง และจะฟังต่อหลังตอบเสร็จ'
      : continuous && browserVoice === 'speaking'
        ? 'แตะปุ่มไมค์เพื่อขัดจังหวะแล้วพูดต่อ'
        : state === 'DISCONNECTED'
          ? 'รอการเชื่อมต่อกับ NEXUS'
          : state === 'IDLE'
            ? voiceOn
              ? 'พูด "Nexus" หรือแตะปุ่มไมค์เพื่อพูดผ่านเบราว์เซอร์'
              : 'แตะปุ่มไมค์เพื่อพูด หรือพิมพ์คำสั่งในช่องสนทนา'
            : detail)

  const talk = () => {
    setMicError(null)
    tapToTalk().catch((e: unknown) => setMicError(micErrorMessage(e)))
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
      <div className={`my-3 w-full ${compact ? 'max-w-[240px]' : 'max-w-[440px] xl:max-w-[clamp(160px,calc(100dvh-43rem),500px)]'}`}>
        <AICore state={state} since={since} />
      </div>
      <Waveform state={state} />
      <MicButton mode={micMode} onTap={talk} />
      {continuous && (
        // Continuous listening is on: say so plainly, and offer the way out.
        <div className="mt-2 flex items-center gap-2 rounded-full border border-cyan/40 bg-cyan/10 py-1 pr-1 pl-3 text-xs text-cyan" role="status">
          <span className="relative flex size-2">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-cyan opacity-60 motion-reduce:animate-none" />
            <span className="relative inline-flex size-2 rounded-full bg-cyan" />
          </span>
          ฟังต่อเนื่อง — พูดคำสั่งถัดไปได้เลย
          <button
            type="button"
            onClick={stopListening}
            className="ml-1 flex min-h-8 items-center gap-1 rounded-full border border-cyan/50 px-3 text-text-1 hover:bg-cyan/20 focus-visible:outline-2 focus-visible:outline-cyan"
          >
            <MicOff className="size-3.5" aria-hidden="true" /> หยุดฟัง
          </button>
        </div>
      )}
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
