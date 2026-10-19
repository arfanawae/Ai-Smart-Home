import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { useReducedMotion } from '../../hooks/useReducedMotion'
import { audioFor, liveLevel, levels, smoothBands } from '../../lib/levels'
import { follow, ripple } from '../../lib/noise'
import type { AIState } from '../../lib/protocol'
import { cssColor, VISUALS, type CoreShape } from '../../lib/stateVisuals'

const TICKS = 120
const PARTICLES = 46
const CORE_BANDS = 24 // spikes per half of the ring around the sphere (mirrored left/right)
const TIMEOUT_MS = 3000 // matches the device service's per-attempt timeout

interface Particle {
  angle: number
  radius: number
  speed: number
  size: number
}

interface Props {
  state: AIState
  /** Date.now() of the last state change (for one-shot effects and the device timeout arc). */
  since: number
}

/**
 * The hero of the dashboard (Blueprint §4 / §7.1): SVG rings outside, a canvas energy core inside.
 * One requestAnimationFrame loop drives both, reading refs only — React never re-renders per frame.
 */
export function AICore({ state, since }: Props) {
  const visual = VISUALS[state]
  const reducedMotion = useReducedMotion()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const ticksRef = useRef<SVGGElement>(null)
  const segmentsRef = useRef<SVGGElement>(null)
  const progressRef = useRef<SVGCircleElement>(null)
  const bracketsRef = useRef<SVGGElement>(null)
  const live = useRef({ state, since, visual })
  useLayoutEffect(() => {
    live.current = { state, since, visual }
  })

  const ticks = useMemo(
    () =>
      Array.from({ length: TICKS }, (_, i) => {
        const a = (i / TICKS) * Math.PI * 2
        const long = i % 10 === 0
        const r1 = long ? 184 : 189
        return { x1: 200 + Math.cos(a) * r1, y1: 200 + Math.sin(a) * r1, x2: 200 + Math.cos(a) * 196, y2: 200 + Math.sin(a) * 196, long }
      }),
    [],
  )

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const particles: Particle[] = Array.from({ length: PARTICLES }, () => ({
      angle: Math.random() * Math.PI * 2,
      radius: 0.85 + Math.random() * 0.35,
      speed: (0.05 + Math.random() * 0.15) * (Math.random() < 0.5 ? -1 : 1),
      size: 0.6 + Math.random() * 1.4,
    }))
    let color = cssColor(live.current.visual.color)
    let width = 0
    let raf = 0
    let last = performance.now()
    let outer = 0
    let inner = 0
    let smoothLevel = 0 // follows the raw level with a fast attack / slow release
    const bands: number[] = []

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      width = canvas.clientWidth
      canvas.width = Math.round(width * dpr)
      canvas.height = Math.round(width * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      render(performance.now()) // resizing clears the canvas: redraw now, even if the loop is paused
    }

    // The loop runs even with prefers-reduced-motion (Windows "Animation effects" off): a frozen core
    // hid what NEXUS was doing. In that mode nothing rotates or drifts — only the size/glow follows
    // the voice level, which is information rather than decoration.
    const frame = (now: number) => {
      render(now)
      if (!document.hidden) raf = requestAnimationFrame(frame)
    }

    const render = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.1)
      last = now
      const { state: st, since: started, visual: v } = live.current

      // Ease the colour towards the state's colour instead of snapping (status transition, §7.2).
      const target = cssColor(v.color)
      color = color.map((c, i) => c + (target[i] - c) * Math.min(1, dt * 6)) as [number, number, number]

      if (v.orbit !== 0 && !reducedMotion) {
        outer += (360 / v.orbit) * dt
        inner -= (360 / v.orbit) * dt * 1.7
      }
      ticksRef.current?.setAttribute('transform', `rotate(${outer} 200 200)`)
      segmentsRef.current?.setAttribute('transform', `rotate(${inner} 200 200)`)
      bracketsRef.current?.setAttribute('transform', `rotate(${-outer * 0.6} 200 200)`)

      const progress = progressRef.current
      if (progress) {
        const circumference = 2 * Math.PI * 158
        if (st === 'WAITING_FOR_DEVICE') {
          const left = Math.max(0, 1 - (Date.now() - started) / TIMEOUT_MS)
          progress.style.opacity = '1'
          progress.style.strokeDasharray = `${circumference}`
          progress.style.strokeDashoffset = `${circumference * (1 - left)}`
        } else if (st === 'TRANSCRIBING') {
          progress.style.opacity = '1'
          progress.style.strokeDasharray = `${circumference * 0.12} ${circumference}`
          progress.style.strokeDashoffset = `${-((now / 1200) % 1) * circumference}`
        } else {
          progress.style.opacity = '0'
        }
      }

      smoothLevel = follow(smoothLevel, levelFor(st, now, reducedMotion), dt)
      const level = smoothLevel
      // Reduced motion: no rotation and no drifting particles. The plasma stands still too — except while
      // NEXUS is listening or speaking, when it flows slowly so the core visibly reacts to the voice.
      const voiceActive = st === 'SPEAKING' || st === 'LISTENING'
      const t = reducedMotion ? (voiceActive ? (now / 1000) * 0.35 : 0) : now / 1000
      const audio = audioFor(st, now)
      smoothBands(bands, CORE_BANDS, audio, dt, now)
      drawCore(ctx, width, t, v.shape, color, level, (Date.now() - started) / 1000, particles, reducedMotion ? 0 : dt, audio ? bands : null)
    }

    const observer = new ResizeObserver(resize)
    observer.observe(canvas)
    resize()

    const onVisibility = () => {
      if (!document.hidden) {
        last = performance.now()
        cancelAnimationFrame(raf)
        raf = requestAnimationFrame(frame)
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
    raf = requestAnimationFrame(frame)
    return () => {
      cancelAnimationFrame(raf)
      observer.disconnect()
      document.removeEventListener('visibilitychange', onVisibility)
    }

  }, [reducedMotion])

  const stroke = { stroke: `var(${visual.color})`, transition: 'stroke 400ms ease' }
  const broken = visual.shape === 'broken' || visual.shape === 'dim'

  return (
    <div className="relative aspect-square w-full" role="img" aria-label={`NEXUS: ${visual.label}`}>
      <svg viewBox="0 0 400 400" className="absolute inset-0 h-full w-full overflow-visible">
        <g ref={ticksRef} style={{ ...stroke, opacity: broken ? 0.35 : 0.75 }}>
          {ticks.map((t, i) => (
            <line key={i} {...t} strokeWidth={t.long ? 2 : 1} strokeLinecap="round" />
          ))}
        </g>
        <circle cx="200" cy="200" r="178" fill="none" style={{ ...stroke, opacity: 0.35 }} strokeWidth="1" />
        <g ref={segmentsRef} style={{ ...stroke, opacity: broken ? 0.4 : 0.9 }}>
          <circle
            cx="200"
            cy="200"
            r="170"
            fill="none"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeDasharray={broken ? '4 14' : '120 40 40 60 200 80 30 498'}
          />
        </g>
        <circle
          ref={progressRef}
          cx="200"
          cy="200"
          r="158"
          fill="none"
          strokeWidth="3"
          strokeLinecap="round"
          transform="rotate(-90 200 200)"
          style={{ ...stroke, opacity: 0, filter: `drop-shadow(0 0 6px var(${visual.color}))` }}
        />
        {/* HUD brackets: four arcs that counter-rotate against the tick ring */}
        <g ref={bracketsRef} style={{ ...stroke, opacity: broken ? 0.25 : 0.7 }} fill="none" strokeWidth="1.5" strokeLinecap="round">
          {[0, 90, 180, 270].map((a) => (
            <path key={a} d={arc(200, 200, 150, a + 18, a + 62)} />
          ))}
          <circle cx="200" cy="200" r="144" strokeWidth="0.75" strokeDasharray="1 7" opacity={0.6} />
        </g>
        <path d="M193 6 L207 6 L200 16 Z" style={{ fill: `var(${visual.color})`, transition: 'fill 400ms ease' }} />
      </svg>
      <canvas ref={canvasRef} className="absolute inset-[17%] h-[66%] w-[66%]" />
    </div>
  )
}

/**
 * Real audio drives the core in the states where that audio exists. While SPEAKING the core must never
 * look frozen, so if no voice level has arrived recently (feed hiccup, quiet word) a gentle synthetic
 * pulse fills in underneath the real level.
 */
function levelFor(state: AIState, now: number, reducedMotion: boolean): number {
  if (state === 'LISTENING') return levels.source === 'mic' ? liveLevel(now) : 0
  if (state !== 'SPEAKING') return 0
  const voice = levels.source === 'tts' ? liveLevel(now) : 0
  const t = now / 1000
  const pulse = reducedMotion ? 0.3 + 0.08 * Math.sin(t * 2) : 0.3 + 0.18 * Math.sin(t * 6.1) * Math.sin(t * 2.3)
  return Math.max(voice, pulse)
}

function rgba([r, g, b]: [number, number, number], a: number): string {
  return `rgba(${r | 0}, ${g | 0}, ${b | 0}, ${a})`
}

/** SVG path of a circular arc, angles in degrees clockwise from 12 o'clock. */
function arc(cx: number, cy: number, r: number, from: number, to: number): string {
  const at = (deg: number) => {
    const a = ((deg - 90) * Math.PI) / 180
    return `${(cx + Math.cos(a) * r).toFixed(2)} ${(cy + Math.sin(a) * r).toFixed(2)}`
  }
  return `M ${at(from)} A ${r} ${r} 0 ${to - from > 180 ? 1 : 0} 1 ${at(to)}`
}

// How fast the orbiting particles travel in each shape: THINKING races, IDLE drifts.
const PARTICLE_SPEED: Partial<Record<CoreShape, number>> = { swirl: 5, stream: 3, ring: 1.6, flash: 2.2, timeout: 0.7, scan: 1.4 }

function drawCore(
  ctx: CanvasRenderingContext2D,
  size: number,
  t: number,
  shape: CoreShape,
  color: [number, number, number],
  level: number,
  age: number,
  particles: Particle[],
  dt: number,
  bands: number[] | null,
) {
  const c = size / 2
  const R = size / 2
  ctx.clearRect(0, 0, size, size)
  if (size === 0) return

  // Halo — the idle "breathing" glow
  const breathe = 0.5 + 0.5 * Math.sin(t * 1.4)
  const halo = ctx.createRadialGradient(c, c, R * 0.15, c, c, R)
  halo.addColorStop(0, rgba(color, shape === 'dim' ? 0.05 : 0.2 + breathe * 0.06 + level * 0.25))
  halo.addColorStop(1, rgba(color, 0))
  ctx.fillStyle = halo
  ctx.fillRect(0, 0, size, size)

  if (shape === 'dim' || shape === 'broken') {
    drawOffline(ctx, c, R, shape, color, age)
    return
  }

  // Orbiting particles with comet tails; while THINKING they race and spiral inwards.
  const speed = (PARTICLE_SPEED[shape] ?? 1) * (1 + level * 1.5)
  ctx.lineCap = 'round'
  for (const p of particles) {
    p.angle += p.speed * dt * speed
    if (shape === 'swirl') {
      p.radius -= dt * 0.3
      if (p.radius < 0.55) p.radius = 1.12 + Math.random() * 0.08
    }
    const pr = R * 0.86 * p.radius
    const tail = Math.min(1.1, Math.abs(p.speed) * speed * 0.9) * Math.sign(p.speed)
    ctx.strokeStyle = rgba(color, 0.18)
    ctx.lineWidth = p.size
    ctx.beginPath()
    ctx.arc(c, c, pr, p.angle - tail, p.angle, tail < 0)
    ctx.stroke()
    ctx.fillStyle = rgba(color, 0.85)
    ctx.beginPath()
    ctx.arc(c + Math.cos(p.angle) * pr, c + Math.sin(p.angle) * pr, p.size, 0, Math.PI * 2)
    ctx.fill()
  }

  if (shape === 'scan') {
    drawRing(ctx, c, R * 0.55, R * 0.05, color, t, 0)
    return
  }
  drawSphere(ctx, c, R, t, shape, color, level, age, bands)
}

function drawOffline(
  ctx: CanvasRenderingContext2D,
  c: number,
  R: number,
  shape: CoreShape,
  color: [number, number, number],
  age: number,
) {
  if (shape === 'dim') {
    ctx.setLineDash([6, 10])
    ctx.strokeStyle = rgba(color, 0.6)
    ctx.lineWidth = 2
    ctx.beginPath()
    ctx.arc(c, c, R * 0.55, 0, Math.PI * 2)
    ctx.stroke()
    ctx.setLineDash([])
    return
  }
  const shake = age < 0.3 ? Math.sin(age * 60) * 4 * (1 - age / 0.3) : 0
  ctx.strokeStyle = rgba(color, 0.9)
  ctx.lineWidth = R * 0.05
  ctx.lineCap = 'round'
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2 + 0.2
    ctx.beginPath()
    ctx.arc(c + shake, c, R * 0.55, a, a + 0.75)
    ctx.stroke()
  }
}

function drawRing(
  ctx: CanvasRenderingContext2D,
  c: number,
  radius: number,
  thickness: number,
  color: [number, number, number],
  t: number,
  level: number,
) {
  ctx.shadowColor = rgba(color, 0.9)
  ctx.shadowBlur = 24
  ctx.strokeStyle = rgba(color, 0.95)
  ctx.lineWidth = thickness
  noisyCircle(ctx, c, radius, radius * (0.008 + level * 0.05), t * 0.35, 7)
  ctx.stroke()
  ctx.shadowBlur = 0
  ctx.strokeStyle = rgba(color, 0.25 + level * 0.4)
  ctx.lineWidth = 1.5
  noisyCircle(ctx, c, radius * (1.18 + level * 0.15), radius * (0.02 + level * 0.06), t * 0.25, 11)
  ctx.stroke()
}

/** A closed circle whose radius is displaced by smooth, seamless noise. */
function noisyCircle(
  ctx: CanvasRenderingContext2D,
  c: number,
  radius: number,
  amp: number,
  time: number,
  seed: number,
  points = 96,
) {
  ctx.beginPath()
  for (let i = 0; i <= points; i++) {
    const a = (i / points) * Math.PI * 2
    const r = radius + ripple(a, time, seed) * amp
    const x = c + Math.cos(a) * r
    const y = c + Math.sin(a) * r
    if (i === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  }
  ctx.closePath()
}

function drawSphere(
  ctx: CanvasRenderingContext2D,
  c: number,
  R: number,
  t: number,
  shape: CoreShape,
  color: [number, number, number],
  level: number,
  age: number,
  bands: number[] | null,
) {
  const breathe = shape === 'timeout' ? 0.5 + 0.5 * Math.sin(t * 3) : 0.5 + 0.5 * Math.sin(t * 1.4)
  // LISTENING ('ring') swells strongly with the user's voice; other states only breathe.
  const body = R * Math.min(0.62, 0.48 + level * (shape === 'ring' ? 0.14 : 0.08) + breathe * 0.02)

  // Concentric pulse waves: drawn inwards while listening (taking in), outwards while speaking.
  const inward = shape === 'ring'
  const rate = inward ? 0.55 + level * 1.2 : bands ? 0.8 + level * 1.4 : shape === 'swirl' ? 0.9 : 0.3
  ctx.lineWidth = 1.2
  for (let k = 0; k < 4; k++) {
    let p = (((t * rate + k / 4) % 1) + 1) % 1
    if (inward) p = 1 - p
    ctx.strokeStyle = rgba(color, Math.sin(p * Math.PI) * (0.18 + level * 0.35))
    ctx.beginPath()
    ctx.arc(c, c, body * (1.08 + p * 0.78), 0, Math.PI * 2)
    ctx.stroke()
  }

  // Frequency spikes around the sphere — the real spectrum, mirrored left/right, lows at the top.
  if (bands) {
    const half = bands.length
    ctx.lineWidth = Math.max(1.5, R * 0.018)
    ctx.lineCap = 'round'
    ctx.shadowColor = rgba(color, 0.9)
    ctx.shadowBlur = 8
    for (let j = 0; j < half; j++) {
      const v = bands[j]
      const inner = body * 1.1
      const outer = inner + R * (0.015 + v * 0.2)
      ctx.strokeStyle = rgba(color, 0.45 + v * 0.55)
      for (const side of [1, -1]) {
        const a = -Math.PI / 2 + side * ((j + 0.5) / half) * Math.PI
        ctx.beginPath()
        ctx.moveTo(c + Math.cos(a) * inner, c + Math.sin(a) * inner)
        ctx.lineTo(c + Math.cos(a) * outer, c + Math.sin(a) * outer)
        ctx.stroke()
      }
    }
    ctx.shadowBlur = 0
  }

  // The sphere: white-hot centre, coloured body, lit from the upper left.
  const lx = c - body * 0.3
  const ly = c - body * 0.35
  const fill = ctx.createRadialGradient(lx, ly, body * 0.05, c, c, body)
  fill.addColorStop(0, `rgba(240, 252, 255, ${0.85 + level * 0.15})`)
  fill.addColorStop(0.3, rgba(color, 0.9))
  fill.addColorStop(0.75, rgba(color, 0.45 + level * 0.2))
  fill.addColorStop(1, rgba(color, 0.12))
  ctx.fillStyle = fill
  ctx.shadowColor = rgba(color, 0.9)
  ctx.shadowBlur = 36 + level * 40 + breathe * 10
  noisyCircle(ctx, c, body, R * (0.004 + level * 0.03), t * 0.3, 3)
  ctx.fill()
  ctx.shadowBlur = 0

  // Plasma currents inside the sphere — calm in IDLE, energetic while SPEAKING/THINKING.
  const energy = shape === 'swirl' ? 2.6 : shape === 'stream' ? 1.8 : 1 + level * 2
  ctx.lineWidth = 1.1
  for (let k = 0; k < 3; k++) {
    const amp = R * (0.01 + level * 0.04) * (k + 1) * (shape === 'swirl' ? 1.6 : 1)
    ctx.strokeStyle = `rgba(255, 255, 255, ${0.12 + k * 0.06})`
    noisyCircle(ctx, c, body * (0.45 + k * 0.17), amp, t * (0.25 + energy * 0.22) + k * 0.37, k)
    ctx.stroke()
  }

  // Specular highlight
  const spec = ctx.createRadialGradient(lx, ly, 0, lx, ly, body * 0.45)
  spec.addColorStop(0, 'rgba(255, 255, 255, 0.55)')
  spec.addColorStop(1, 'rgba(255, 255, 255, 0)')
  ctx.fillStyle = spec
  ctx.beginPath()
  ctx.arc(c, c, body, 0, Math.PI * 2)
  ctx.fill()

  if (shape === 'swirl') {
    ctx.strokeStyle = rgba(color, 0.85)
    ctx.lineWidth = 2
    for (let i = 0; i < 4; i++) {
      const a = t * (2.4 + i * 0.8) * (i % 2 ? -1 : 1)
      ctx.beginPath()
      ctx.arc(c, c, body * (1.12 + i * 0.1), a, a + 1.1)
      ctx.stroke()
    }
  }

  if (shape === 'stream') {
    // command packets flowing outward from the core
    ctx.strokeStyle = rgba(color, 0.9)
    ctx.lineWidth = 2
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * Math.PI * 2
      const p = (t * 1.5 + i * 0.37) % 1
      const r0 = body * (1.1 + p * 0.6)
      ctx.beginPath()
      ctx.moveTo(c + Math.cos(a) * r0, c + Math.sin(a) * r0)
      ctx.lineTo(c + Math.cos(a) * (r0 + R * 0.06), c + Math.sin(a) * (r0 + R * 0.06))
      ctx.stroke()
    }
  }

  if (shape === 'flash' && age < 0.9) {
    // one expanding burst right after WAKE / SUCCESS
    const p = age / 0.9
    ctx.strokeStyle = rgba(color, 1 - p)
    ctx.lineWidth = 3 * (1 - p) + 1
    ctx.beginPath()
    ctx.arc(c, c, body * (1 + p * 0.9), 0, Math.PI * 2)
    ctx.stroke()
  }
}
