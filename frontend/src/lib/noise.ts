// Smooth 3D value noise (quintic-interpolated lattice) for the AI Core's plasma.
// Sampling at (cos θ, sin θ, time) gives a ring outline with no seam that flows instead of jittering.

function hash(x: number, y: number, z: number): number {
  let h = (x * 374761393 + y * 668265263 + z * 2147483647) | 0
  h = Math.imul(h ^ (h >>> 13), 1274126177)
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295
}

const fade = (t: number) => t * t * t * (t * (t * 6 - 15) + 10) // C2-continuous: no visible creases
const lerp = (a: number, b: number, t: number) => a + (b - a) * t

/** Smooth noise in [0, 1]. */
export function noise3(x: number, y: number, z: number): number {
  const xi = Math.floor(x)
  const yi = Math.floor(y)
  const zi = Math.floor(z)
  const u = fade(x - xi)
  const v = fade(y - yi)
  const w = fade(z - zi)
  const c = (dx: number, dy: number, dz: number) => hash(xi + dx, yi + dy, zi + dz)
  return lerp(
    lerp(lerp(c(0, 0, 0), c(1, 0, 0), u), lerp(c(0, 1, 0), c(1, 1, 0), u), v),
    lerp(lerp(c(0, 0, 1), c(1, 0, 1), u), lerp(c(0, 1, 1), c(1, 1, 1), u), v),
    w,
  )
}

/** Two octaves, centred on 0, roughly in [-1, 1]: a softer, more organic ripple than one octave. */
export function ripple(angle: number, time: number, seed: number, frequency = 1.6): number {
  const x = Math.cos(angle) * frequency + seed * 17.3
  const y = Math.sin(angle) * frequency + seed * 5.1
  const a = noise3(x, y, time)
  const b = noise3(x * 2.1 + 31, y * 2.1 + 11, time * 1.7)
  return (a * 0.7 + b * 0.3) * 2 - 1
}

/**
 * Exponential smoothing with a fast attack and a slow release: the core swells with each syllable
 * and settles gently, instead of snapping to every 60 ms level message.
 */
export function follow(current: number, target: number, dt: number, attack = 14, release = 4): number {
  const rate = target > current ? attack : release
  return current + (target - current) * (1 - Math.exp(-rate * dt))
}
