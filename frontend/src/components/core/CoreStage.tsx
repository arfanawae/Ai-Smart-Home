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
  const visual = VISUALS[state]
  const voiceOn = (info?.languages.length ?? 0) > 0

  const hint =
    state === 'DISCONNECTED'
      ? 'รอการเชื่อมต่อกับ NEXUS'
      : state === 'IDLE'
        ? voiceOn
          ? 'พูด "Nexus" แล้วเว้นจังหวะสั้น ๆ ก่อนสั่งงาน'
          : 'พิมพ์คำสั่งในช่องสนทนา (ระบบเสียงปิดอยู่)'
        : detail

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
        <AICore state={state} since={since} />
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
