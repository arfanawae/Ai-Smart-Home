import { describe, expect, it } from 'vitest'
import { follow, noise3, ripple } from './noise'

describe('noise3', () => {
  it('stays in [0, 1] and is deterministic', () => {
    for (let i = 0; i < 500; i++) {
      const v = noise3(i * 0.37, i * 0.11, i * 0.07)
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThanOrEqual(1)
      expect(noise3(i * 0.37, i * 0.11, i * 0.07)).toBe(v)
    }
  })

  it('is smooth: a tiny step never jumps (no jagged jitter)', () => {
    let worst = 0
    for (let i = 0; i < 2000; i++) {
      const x = i * 0.013
      worst = Math.max(worst, Math.abs(noise3(x, 0.5, 1.2) - noise3(x + 0.001, 0.5, 1.2)))
    }
    expect(worst).toBeLessThan(0.01)
  })
})

describe('ripple', () => {
  it('has no seam where the ring closes (θ = 0 and θ = 2π meet)', () => {
    for (const t of [0, 0.7, 3.3]) {
      expect(ripple(0, t, 1)).toBeCloseTo(ripple(Math.PI * 2, t, 1), 9)
    }
  })

  it('changes gradually over time', () => {
    const a = ripple(1, 2.0, 0)
    const b = ripple(1, 2.016, 0) // one frame later at 60 fps
    expect(Math.abs(a - b)).toBeLessThan(0.05)
  })
})

describe('follow', () => {
  it('rises faster than it falls', () => {
    const up = follow(0, 1, 1 / 60)
    const down = 1 - follow(1, 0, 1 / 60)
    expect(up).toBeGreaterThan(down)
  })

  it('converges to the target', () => {
    let v = 0
    for (let i = 0; i < 120; i++) v = follow(v, 0.8, 1 / 60)
    expect(v).toBeCloseTo(0.8, 2)
  })
})

describe('liveLevel', () => {
  it('never exceeds 1, even when the frame time is older than the last level', async () => {
    const { levels, liveLevel, pushLevel } = await import('./levels')
    pushLevel(0.8, 'mic', undefined, 1000)
    expect(liveLevel(600)).toBeLessThanOrEqual(1) // negative age used to amplify the level
    expect(liveLevel(1000)).toBeCloseTo(0.8)
    expect(liveLevel(2000)).toBe(0)
    expect(levels.source).toBe('mic')
  })
})
