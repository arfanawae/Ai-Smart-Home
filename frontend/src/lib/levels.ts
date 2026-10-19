// Audio levels arrive ~16×/s. They bypass React state and are read directly by the canvas loops,
// so the waveform and core can follow the voice without re-rendering the page.

const HISTORY = 48

export const levels = {
  current: 0,
  source: 'mic' as 'mic' | 'tts',
  at: 0,
  history: new Array<number>(HISTORY).fill(0),
}

export function pushLevel(rms: number, source: 'mic' | 'tts', now = performance.now()): void {
  levels.current = rms
  levels.source = source
  levels.at = now
  levels.history.push(rms)
  if (levels.history.length > HISTORY) levels.history.shift()
}

/** Level that fades out if the feed stops sending (e.g. NEXUS stopped speaking). */
export function liveLevel(now = performance.now()): number {
  const age = now - levels.at
  return age > 400 ? 0 : levels.current * (1 - age / 400)
}
