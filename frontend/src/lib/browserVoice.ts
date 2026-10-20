// Voice in the dashboard itself (no laptop microphone needed): tap the core, speak, and the reply is
// read aloud by the browser's own speech synthesis — free, nothing to install.
//
//   mic → Web Audio (16 kHz mono) → WAV → NEXUS → Groq Whisper / Gemini → assistant turn → "speak" → speechSynthesis
//
// While recording, the real mic level and spectrum drive the core and the equaliser. The browser's
// speech synthesis exposes no audio samples, so while it speaks the visualiser follows its word
// boundaries instead (an approximation, not a measurement).

import { send } from './feed'
import { pushLevel } from './levels'
import { useNexus } from '../store/nexus'

const RATE = 16000
const SPEECH_RMS = 0.015 // above this a block counts as voice
const END_SILENCE_MS = 1200 // quiet this long after speaking → send
const NO_SPEECH_MS = 7000 // nothing said at all → give up quietly
const MAX_MS = 15000
const BANDS = 24
const REPLY_TIMEOUT_MS = 45000

interface Recording {
  ctx: AudioContext
  stream: MediaStream
  node: ScriptProcessorNode
  analyser: AnalyserNode
  chunks: Float32Array[]
  startedAt: number
  lastVoiceAt: number
  heard: boolean
}

let rec: Recording | null = null
let awaitingReplyUntil = 0
let speakingTimer = 0

const setVoice = (v: 'idle' | 'recording' | 'speaking') => useNexus.getState().setBrowserVoice(v)

export function isRecording(): boolean {
  return rec !== null
}

/** Tap on the core: start listening, or (if already listening) send what was said so far. */
export async function toggleRecording(): Promise<void> {
  if (rec) return stopRecording(true)
  window.speechSynthesis?.cancel() // talking over NEXUS = interrupt it
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  })
  const ctx = new AudioContext({ sampleRate: RATE }) // the browser resamples the mic to 16 kHz
  const source = ctx.createMediaStreamSource(stream)
  const analyser = ctx.createAnalyser()
  analyser.fftSize = 1024
  analyser.smoothingTimeConstant = 0.5
  const node = ctx.createScriptProcessor(2048, 1, 1)
  source.connect(analyser)
  source.connect(node)
  node.connect(ctx.destination) // required for onaudioprocess to fire; the output stays silent
  const now = performance.now()
  rec = { ctx, stream, node, analyser, chunks: [], startedAt: now, lastVoiceAt: now, heard: false }
  const spectrum = new Float32Array(analyser.frequencyBinCount)

  node.onaudioprocess = (e) => {
    const r = rec
    if (!r) return
    const block = new Float32Array(e.inputBuffer.getChannelData(0))
    r.chunks.push(block)
    const rms = Math.sqrt(block.reduce((sum, x) => sum + x * x, 0) / block.length)
    r.analyser.getFloatFrequencyData(spectrum)
    pushLevel(loudness(rms), 'mic', bands(spectrum, r.ctx.sampleRate))
    const t = performance.now()
    if (rms > SPEECH_RMS) {
      r.heard = true
      r.lastVoiceAt = t
    }
    if (r.heard && t - r.lastVoiceAt > END_SILENCE_MS) stopRecording(true)
    else if (!r.heard && t - r.startedAt > NO_SPEECH_MS) stopRecording(false)
    else if (t - r.startedAt > MAX_MS) stopRecording(true)
  }
  setVoice('recording')
}

export function stopRecording(sendIt: boolean): void {
  const r = rec
  if (!r) return
  rec = null
  r.node.onaudioprocess = null
  r.node.disconnect()
  r.stream.getTracks().forEach((t) => t.stop())
  const rate = r.ctx.sampleRate
  void r.ctx.close()
  pushLevel(0, 'mic', new Array<number>(BANDS).fill(0))
  setVoice('idle')
  if (!sendIt || !r.heard) return
  const pcm = resample(concat(r.chunks), rate, RATE)
  if (send({ type: 'audio', data: toBase64(encodeWav(pcm, RATE)) })) awaitingReplyUntil = Date.now() + REPLY_TIMEOUT_MS
}

/** The backend's "speak" message: read it aloud only if this browser asked the question. */
export function speakReply(text: string, lang: string): void {
  if (Date.now() > awaitingReplyUntil || !window.speechSynthesis) return
  awaitingReplyUntil = 0
  const synth = window.speechSynthesis
  synth.cancel()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = lang
  const voice = pickVoice(synth.getVoices(), lang)
  if (voice) utterance.voice = voice
  utterance.rate = 1.05

  // Visualiser: an envelope that jumps on each word boundary and decays between words.
  let envelope = 0
  const start = () => {
    setVoice('speaking')
    clearInterval(speakingTimer)
    speakingTimer = window.setInterval(() => {
      envelope = Math.max(0.18, envelope * 0.86)
      const t = performance.now() / 1000
      pushLevel(envelope, 'tts', speechLikeBands(envelope, t))
    }, 60)
  }
  const end = () => {
    clearInterval(speakingTimer)
    pushLevel(0, 'tts', new Array<number>(BANDS).fill(0))
    setVoice('idle')
  }
  utterance.onstart = start
  utterance.onboundary = () => {
    envelope = 0.75 + Math.random() * 0.25
  }
  utterance.onend = end
  utterance.onerror = end
  synth.speak(utterance)
}

// ── helpers (exported for tests) ──────────────────────────────────────────────────────────────

/** Same 0–1 mapping as the backend's audio_io.loudness: RMS in dBFS between -60 and -10. */
export function loudness(rms: number): number {
  const db = 20 * Math.log10(Math.max(rms, 1e-9))
  return Math.min(1, Math.max(0, (db + 60) / 50))
}

/** 24 log-spaced bands, 90 Hz – 7.6 kHz, from an AnalyserNode's dB spectrum (like audio_io.spectrum). */
export function bands(spectrumDb: Float32Array, sampleRate: number, count = BANDS): number[] {
  const binHz = sampleRate / 2 / spectrumDb.length
  const out: number[] = []
  for (let b = 0; b < count; b++) {
    const lo = 90 * Math.pow(7600 / 90, b / count)
    const hi = 90 * Math.pow(7600 / 90, (b + 1) / count)
    const from = Math.max(0, Math.floor(lo / binHz))
    const to = Math.min(spectrumDb.length - 1, Math.max(from, Math.ceil(hi / binHz) - 1))
    let sum = 0
    for (let i = from; i <= to; i++) sum += spectrumDb[i]
    const db = sum / (to - from + 1)
    out.push(Math.min(1, Math.max(0, (db + 95) / 60)))
  }
  return out
}

function speechLikeBands(level: number, t: number): number[] {
  return Array.from({ length: BANDS }, (_, b) => {
    const formant = Math.exp(-(((b - 6 - 2 * Math.sin(t * 3)) / 4) ** 2)) + 0.6 * Math.exp(-(((b - 14) / 3) ** 2))
    return Math.min(1, level * (0.3 + formant) * (0.8 + 0.2 * Math.sin(t * 13 + b)))
  })
}

export function pickVoice(voices: SpeechSynthesisVoice[], lang: string): SpeechSynthesisVoice | undefined {
  const want = lang.toLowerCase()
  const prefix = want.slice(0, 2)
  const norm = (v: SpeechSynthesisVoice) => v.lang.replace('_', '-').toLowerCase()
  return (
    voices.find((v) => norm(v) === want && /natural|online/i.test(v.name)) ?? // Edge's free neural voices
    voices.find((v) => norm(v) === want) ??
    voices.find((v) => norm(v).startsWith(prefix))
  )
}

function concat(chunks: Float32Array[]): Float32Array {
  const out = new Float32Array(chunks.reduce((n, c) => n + c.length, 0))
  let offset = 0
  for (const c of chunks) {
    out.set(c, offset)
    offset += c.length
  }
  return out
}

/** Linear resampling, in case the browser ignored the 16 kHz request. */
export function resample(input: Float32Array, from: number, to: number): Float32Array {
  if (from === to) return input
  const out = new Float32Array(Math.floor((input.length * to) / from))
  for (let i = 0; i < out.length; i++) {
    const x = (i * from) / to
    const j = Math.floor(x)
    const k = Math.min(j + 1, input.length - 1)
    out[i] = input[j] + (input[k] - input[j]) * (x - j)
  }
  return out
}

/** 16-bit mono PCM WAV. */
export function encodeWav(samples: Float32Array, rate: number): Uint8Array {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const v = new DataView(buffer)
  const text = (at: number, s: string) => [...s].forEach((ch, i) => v.setUint8(at + i, ch.charCodeAt(0)))
  text(0, 'RIFF')
  v.setUint32(4, 36 + samples.length * 2, true)
  text(8, 'WAVE')
  text(12, 'fmt ')
  v.setUint32(16, 16, true)
  v.setUint16(20, 1, true) // PCM
  v.setUint16(22, 1, true) // mono
  v.setUint32(24, rate, true)
  v.setUint32(28, rate * 2, true)
  v.setUint16(32, 2, true)
  v.setUint16(34, 16, true)
  text(36, 'data')
  v.setUint32(40, samples.length * 2, true)
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  return new Uint8Array(buffer)
}

function toBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(binary)
}
