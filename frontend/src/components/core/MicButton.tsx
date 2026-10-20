import { Mic } from 'lucide-react'

export type MicMode = 'off' | 'idle' | 'recording' | 'busy'

interface Props {
  mode: MicMode
  onTap: () => void
}

const HELP: Record<MicMode, string> = {
  off: 'ยังไม่ได้เชื่อมต่อ',
  idle: 'แตะเพื่อพูด',
  recording: 'กำลังฟัง · แตะเพื่อส่ง',
  busy: 'แตะเพื่อพูดแทรก',
}

/**
 * Tap-to-talk: a glass button with a glowing cyan ring between the core and the status text.
 * One tap starts continuous listening; while listening a tap sends at once, while NEXUS answers a tap
 * interrupts it.
 */
export function MicButton({ mode, onTap }: Props) {
  const live = mode === 'recording'
  return (
    <div className="mt-3 flex flex-col items-center gap-1.5">
      <button
        type="button"
        onClick={onTap}
        disabled={mode === 'off'}
        aria-pressed={mode !== 'idle' && mode !== 'off'}
        aria-label={mode === 'recording' ? 'ส่งเสียงที่พูดทันที' : mode === 'busy' ? 'ขัดจังหวะแล้วพูด' : 'แตะเพื่อพูดกับ NEXUS'}
        className={[
          'relative grid size-[60px] touch-manipulation place-items-center rounded-full border backdrop-blur-md',
          'bg-[radial-gradient(circle_at_35%_30%,rgb(255_255_255/0.14),rgb(12_26_43/0.85)_60%)]',
          'transition-[transform,box-shadow,border-color] duration-150 ease-out active:scale-95',
          'focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-cyan',
          'disabled:cursor-not-allowed disabled:opacity-40 enabled:cursor-pointer',
          live
            ? 'border-cyan shadow-[0_0_0_3px_rgb(46_197_255/0.25),0_0_28px_rgb(46_197_255/0.75)]'
            : mode === 'busy'
              ? 'border-teal/70 shadow-[0_0_0_2px_rgb(94_234_212/0.18),0_0_18px_rgb(94_234_212/0.45)]'
              : 'border-cyan/60 shadow-[0_0_0_2px_rgb(46_197_255/0.15),0_0_18px_rgb(46_197_255/0.4)] hover:shadow-[0_0_0_2px_rgb(46_197_255/0.3),0_0_24px_rgb(46_197_255/0.6)]',
        ].join(' ')}
      >
        {live && (
          // listening: a ring keeps expanding out of the button
          <span className="pointer-events-none absolute inset-0 animate-ping rounded-full border-2 border-cyan/60 motion-reduce:animate-none" aria-hidden="true" />
        )}
        <Mic
          className={`size-6 ${live ? 'text-white' : 'text-cyan'} drop-shadow-[0_0_6px_rgb(46_197_255/0.9)]`}
          strokeWidth={2.25}
          aria-hidden="true"
        />
      </button>
      <span className={`text-xs ${live ? 'text-cyan' : 'text-text-2'}`}>{HELP[mode]}</span>
    </div>
  )
}
