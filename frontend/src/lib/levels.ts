// Audio levels arrive ~16×/s. They bypass React state and are read directly by the canvas loops,
// so the waveform and core can follow the voice without re-rendering the page.

const HISTORY = 48

export const levels = {
  current: 0,
  source: 'mic' as 'mic' | 'tts',
  at: 0,
  history: new Array<number>(HISTORY).fill(0),
  /** Latest per-band energies (low → high) from the backend's FFT; empty if it sends none. */
  bands: [] as number[],
}

export function pushLevel(rms: number, source: 'mic' | 'tts', bands?: number[], now = performance.now()): void {
  levels.current = rms
  levels.source = source
  levels.at = now
  levels.history.push(rms)
  if (levels.history.length > HISTORY) levels.history.shift()
  if (bands) levels.bands = bands
}

/** Level that fades out if the feed stops sending (e.g. NEXUS stopped speaking). */
export function liveLevel(now = performance.now()): number {
  // A frame's timestamp can be slightly older than the last level (negative age): never amplify.
  const age = Math.max(0, now - levels.at)
  return age > 400 ? 0 : Math.min(1, Math.max(0, levels.current)) * (1 - age / 400)
}

/** Whether the audio being reported belongs to this state (mic while listening, voice while speaking). */
export function audioFor(state: string, now = performance.now()): boolean {
  const fresh = now - levels.at < 500
  return fresh && ((state === 'LISTENING' && levels.source === 'mic') || (state === 'SPEAKING' && levels.source === 'tts'))
}

/**
 * Band energies smoothed for drawing: fast attack, slower release, faded out when the feed goes quiet.
 * `into` keeps its own state between frames.
 */
export function smoothBands(into: number[], count: number, active: boolean, dt: number, now = performance.now()): number[] {
  const age = Math.max(0, now - levels.at)
  const fade = active ? Math.max(0, 1 - Math.max(0, age - 120) / 380) : 0
  const src = levels.bands
  if (into.length !== count) into.splice(0, into.length, ...new Array<number>(count).fill(0))
  for (let i = 0; i < count; i++) {
    // resample the backend's bands onto `count` slots
    const x = src.length ? (i / Math.max(1, count - 1)) * (src.length - 1) : 0
    const lo = Math.floor(x)
    const raw = src.length ? src[lo] + (src[Math.min(lo + 1, src.length - 1)] - src[lo]) * (x - lo) : liveLevel(now)
    const target = Math.min(1, Math.max(0, raw)) * fade
    const rate = target > into[i] ? 22 : 6
    into[i] += (target - into[i]) * Math.min(1, dt * rate)
  }
  return into
}
