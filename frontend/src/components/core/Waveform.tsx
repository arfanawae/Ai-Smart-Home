import { useEffect, useLayoutEffect, useRef } from 'react'
import { audioFor, smoothBands } from '../../lib/levels'
import type { AIState } from '../../lib/protocol'
import { cssColor, VISUALS } from '../../lib/stateVisuals'

const HALF = 24 // bars per side; mirrored around the centre (low frequencies in the middle)

/**
 * Equaliser under the core: real frequency bands of the mic (LISTENING) or of NEXUS's own voice
 * (SPEAKING), computed by the backend from the actual audio. Otherwise a quiet baseline — never fake audio.
 */
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
    let last = performance.now()
    const bands: number[] = []
    let color = cssColor(VISUALS[live.current].color)

    const draw = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.1)
      last = now
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const w = canvas.clientWidth
      const h = canvas.clientHeight
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr)
        canvas.height = Math.round(h * dpr)
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)

      const st = live.current
      const target = cssColor(VISUALS[st].color)
      color = color.map((c, i) => c + (target[i] - c) * Math.min(1, dt * 6)) as [number, number, number]
      const [r, g, b] = color.map((c) => c | 0)
      const active = audioFor(st, now)
      smoothBands(bands, HALF, active, dt, now)

      const mid = h * 0.62 // bars rise from here; the reflection hangs below
      const slot = w / (HALF * 2)
      const barW = Math.max(1.5, slot * 0.55)
      const grad = ctx.createLinearGradient(0, mid - h * 0.6, 0, mid)
      grad.addColorStop(0, `rgba(${r}, ${g}, ${b}, 0.95)`)
      grad.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0.35)`)

      ctx.shadowColor = `rgba(${r}, ${g}, ${b}, 0.8)`
      ctx.shadowBlur = active ? 10 : 0
      for (let side = -1; side <= 1; side += 2) {
        for (let i = 0; i < HALF; i++) {
          const value = bands[i]
          const bar = Math.max(1.5, value * mid * 0.95)
          const x = w / 2 + side * (i + 0.5) * slot - barW / 2
          ctx.fillStyle = active ? grad : `rgba(${r}, ${g}, ${b}, 0.22)`
          ctx.fillRect(x, mid - bar, barW, bar)
          // reflection
          ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${active ? 0.12 + value * 0.12 : 0.06})`
          ctx.fillRect(x, mid + 2, barW, bar * 0.35)
        }
      }
      ctx.shadowBlur = 0
      // Bars are measured audio (data, not decoration), so they keep updating under reduced motion.
      if (!document.hidden) raf = requestAnimationFrame(draw)
    }
    raf = requestAnimationFrame(draw)
    const onVisibility = () => {
      if (!document.hidden) {
        last = performance.now()
        cancelAnimationFrame(raf)
        raf = requestAnimationFrame(draw)
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  return <canvas ref={canvasRef} className="h-14 w-full max-w-[360px]" aria-hidden="true" />
}
