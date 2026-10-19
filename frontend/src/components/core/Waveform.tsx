import { useEffect, useLayoutEffect, useRef } from 'react'
import { levels } from '../../lib/levels'
import type { AIState } from '../../lib/protocol'
import { cssColor, VISUALS } from '../../lib/stateVisuals'

const BARS = 40

/** Bars from the real mic (LISTENING) or NEXUS's real voice (SPEAKING); a flat line otherwise — never fake audio. */
export function Waveform({ state }: { state: AIState }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const live = useRef(state)
  useLayoutEffect(() => {
    live.current = state
  })

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    let raf = 0
    const draw = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const w = canvas.clientWidth
      const h = canvas.clientHeight
      if (canvas.width !== Math.round(w * dpr)) {
        canvas.width = Math.round(w * dpr)
        canvas.height = Math.round(h * dpr)
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)
      const st = live.current
      const active =
        (st === 'LISTENING' && levels.source === 'mic') || (st === 'SPEAKING' && levels.source === 'tts')
      const fresh = performance.now() - levels.at < 500
      const [r, g, b] = cssColor(VISUALS[st].color)
      const history = levels.history.slice(-BARS)
      const gap = w / BARS
      for (let i = 0; i < BARS; i++) {
        const value = active && fresh ? (history[i] ?? 0) : 0
        const bar = Math.max(2, value * h * 0.9)
        ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${active ? 0.35 + value * 0.65 : 0.25})`
        ctx.fillRect(i * gap + gap * 0.25, (h - bar) / 2, gap * 0.5, bar)
      }
      // Bars are measured audio (data, not decoration), so they keep updating under reduced motion.
      if (!document.hidden) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    const onVisibility = () => {
      if (!document.hidden) raf = requestAnimationFrame(draw)
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  return <canvas ref={canvasRef} className="h-10 w-full max-w-[320px]" aria-hidden="true" />
}
