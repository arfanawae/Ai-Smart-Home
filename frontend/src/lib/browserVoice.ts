// Voice in the dashboard itself (no laptop microphone needed): tap the core once and talk — NEXUS
// keeps listening after each reply (continuous listening) until you turn it off, say goodbye, or stay
// quiet for a minute. Replies are read aloud by the browser's own speech synthesis — free.
//
//   mic → Web Audio (16 kHz mono) → WAV → NEXUS → Azure / Groq / Gemini STT → assistant turn
//       → "speak" → speechSynthesis → listen again
//
// The microphone stays open for the whole session (re-opening it outside a tap is blocked on iOS),
// but nothing is captured while NEXUS speaks plus a short echo tail, so it never hears itself.
// While recording, the real mic level and spectrum drive the core and the equaliser. The browser's
// speech synthesis exposes no audio samples, so while it speaks the visualiser follows its word
// boundaries instead (an approximation, not a measurement).

import { send } from './feed'
import { pushLevel } from './levels'
import { useNexus } from '../store/nexus'

const RATE = 16000
const SPEECH_RMS = 0.015 // above this a block counts as voice
const END_SILENCE_MS = 1200 // quiet this long after speaking → send
const MAX_MS = 15000 // one utterance at most
const SESSION_IDLE_MS = 60000 // continuous listening ends after a minute without speech
const PRE_ROLL_BLOCKS = 4 // ~0.5 s kept from before the voice starts, so the first syllable is not cut
const ECHO_TAIL_MS = 400 // the room still rings for a moment after the browser stops speaking
const BANDS = 24
const REPLY_TIMEOUT_MS = 45000

/** The open microphone, kept for the whole continuous-listening session. */
interface Session {
  ctx: AudioContext
  stream: MediaStream
  node: ScriptProcessorNode
  analyser: AnalyserNode
  idleSince: number
}

/** The utterance being captured right now (null while waiting for / speaking a reply). */
interface Capture {
  chunks: Float32Array[]
  startedAt: number
  lastVoiceAt: number
  heard: boolean
}

let session: Session | null = null
let capture: Capture | null = null
let replyTimer = 0
let resumeTimer = 0
let speakingTimer = 0
let speechUnlocked = false

const store = () => useNexus.getState()
const setVoice = (v: 'idle' | 'recording' | 'speaking') => store().setBrowserVoice(v)

export function isListening(): boolean {
  return session !== null
}

/**
 * Mobile browsers (iOS Safari above all) only allow audio and speech that a tap starts. The reply is
 * spoken later, from a network message — so the first tap also speaks a silent utterance, which
 * unlocks speechSynthesis for the rest of the page's life.
 */
function unlockSpeech(): void {
  const synth = window.speechSynthesis
  if (speechUnlocked || !synth) return
  const silent = new SpeechSynthesisUtterance(' ')
  silent.volume = 0
  synth.speak(silent)
  speechUnlocked = true
}

/** A Thai message for why the microphone could not be opened. */
export function micErrorMessage(e: unknown): string {
  const name = e instanceof Error ? e.name : ''
  switch (name) {
    case 'InsecureContext':
      return 'ไมค์ใช้ได้เฉพาะหน้าเว็บที่เป็น HTTPS (หรือ localhost) — เปิดผ่านลิงก์ https:// ของ Cloudflare Tunnel'
    case 'NotAllowedError':
    case 'PermissionDeniedError':
      return 'ไม่ได้รับอนุญาตให้ใช้ไมค์ — อนุญาต Microphone ในการตั้งค่าเว็บไซต์ของเบราว์เซอร์ (iPhone: Settings › Safari › Microphone) แล้วลองใหม่'
    case 'NotFoundError':
    case 'DevicesNotFoundError':
      return 'ไม่พบไมโครโฟนในอุปกรณ์นี้'
    case 'NotReadableError':
    case 'TrackStartError':
      return 'ไมค์กำลังถูกแอปอื่นใช้อยู่ (เช่น โทรศัพท์หรือแอปอัดเสียง) — ปิดแอปนั้นแล้วลองใหม่'
    case 'SecurityError':
      return 'เบราว์เซอร์บล็อกไมค์ในหน้านี้ — ตรวจว่าเปิดผ่าน HTTPS และไม่ได้อยู่ในหน้าต่างฝังของแอปอื่น'
    default:
      return `เปิดไมค์ไม่ได้: ${e instanceof Error ? e.message : String(e)}`
  }
}

function newContext(): AudioContext {
  try {
    return new AudioContext({ sampleRate: RATE }) // resampled by the browser where supported
  } catch {
    return new AudioContext() // e.g. older Safari: native rate, resampled before sending
  }
}

/**
 * Tap on the core.
 *  - not listening        → open the mic and start continuous listening
 *  - capturing speech     → send it now (no need to wait for the pause)
 *  - NEXUS is speaking    → interrupt it and listen
 */
export async function tapToTalk(): Promise<void> {
  if (session) {
    if (capture?.heard) return finishCapture()
    window.speechSynthesis?.cancel()
    return startCapture()
  }
  // Everything up to the first await runs inside the tap — that is what unlocks audio on phones.
  window.speechSynthesis?.cancel()
  unlockSpeech()
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    throw Object.assign(new Error('insecure context'), { name: 'InsecureContext' })
  }
  let ctx = newContext()
  void ctx.resume()
  let stream: MediaStream
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
  } catch (e) {
    void ctx.close()
    throw e
  }
  let source: MediaStreamAudioSourceNode
  try {
    source = ctx.createMediaStreamSource(stream)
  } catch {
    // Firefox refuses a mic at a different rate than the context: use the native rate instead.
    void ctx.close()
    ctx = new AudioContext()
    void ctx.resume()
    source = ctx.createMediaStreamSource(stream)
  }
  const analyser = ctx.createAnalyser()
  analyser.fftSize = 1024
  analyser.smoothingTimeConstant = 0.5
  const node = ctx.createScriptProcessor(2048, 1, 1)
  source.connect(analyser)
  source.connect(node)
  node.connect(ctx.destination) // required for onaudioprocess to fire; the output stays silent
  session = { ctx, stream, node, analyser, idleSince: performance.now() }
  node.onaudioprocess = onAudio(new Float32Array(analyser.frequencyBinCount))
  store().setContinuous(true)
  startCapture()
}

/** Turn continuous listening off: close the mic, stop speaking, forget any pending turn. */
export function stopListening(): void {
  clearTimeout(replyTimer)
  clearTimeout(resumeTimer)
  capture = null
  const s = session
  session = null
  if (s) {
    s.node.onaudioprocess = null
    s.node.disconnect()
    s.stream.getTracks().forEach((t) => t.stop())
    void s.ctx.close()
  }
  window.speechSynthesis?.cancel()
  clearInterval(speakingTimer)
  pushLevel(0, 'mic', new Array<number>(BANDS).fill(0))
  store().setContinuous(false)
  setVoice('idle')
}

function startCapture(): void {
  if (!session) return
  clearTimeout(resumeTimer)
  clearTimeout(replyTimer)
  const now = performance.now()
  capture = { chunks: [], startedAt: now, lastVoiceAt: now, heard: false }
  setVoice('recording')
}

function onAudio(spectrum: Float32Array<ArrayBuffer>) {
  return (e: AudioProcessingEvent) => {
    const s = session
    const c = capture
    if (!s || !c) return // not capturing: waiting for the reply or NEXUS is speaking
    const block = new Float32Array(e.inputBuffer.getChannelData(0))
    const rms = Math.sqrt(block.reduce((sum, x) => sum + x * x, 0) / block.length)
    s.analyser.getFloatFrequencyData(spectrum)
    pushLevel(loudness(rms), 'mic', bands(spectrum, s.ctx.sampleRate))
    const t = performance.now()
    c.chunks.push(block)
    if (rms > SPEECH_RMS) {
      c.heard = true
      c.lastVoiceAt = t
    } else if (!c.heard) {
      if (c.chunks.length > PRE_ROLL_BLOCKS) c.chunks.shift() // keep only a short pre-roll while silent
      if (t - s.idleSince > SESSION_IDLE_MS) stopListening() // a minute of silence ends the session
      return
    }
    if (t - c.lastVoiceAt > END_SILENCE_MS || t - c.startedAt > MAX_MS) finishCapture()
  }
}

/** Send the captured utterance; listening resumes when the reply has been spoken. */
function finishCapture(): void {
  const s = session
  const c = capture
  if (!s || !c) return
  capture = null
  pushLevel(0, 'mic', new Array<number>(BANDS).fill(0))
  setVoice('idle')
  s.idleSince = performance.now()
  if (!c.heard) return startCapture()
  const pcm = resample(concat(c.chunks), s.ctx.sampleRate, RATE)
  if (!send({ type: 'audio', data: toBase64(encodeWav(pcm, RATE)) })) return startCapture()
  // If NEXUS never answers (it was restarted, the network dropped), listen again anyway.
  replyTimer = window.setTimeout(() => resumeListening(), REPLY_TIMEOUT_MS)
}

function resumeListening(delayMs = 0): void {
  clearTimeout(resumeTimer)
  if (!session) return
  resumeTimer = window.setTimeout(() => {
    if (session && !capture) {
      session.idleSince = performance.now()
      startCapture()
    }
  }, delayMs)
}

/**
 * The backend's "speak" message ends a dashboard voice turn: read the reply aloud (if any), then
 * listen again — or stop, if the user said goodbye. Ignored unless this page is listening.
 */
export function speakReply(text: string, lang: string, end = false): void {
  if (!session) return
  clearTimeout(replyTimer)
  const synth = window.speechSynthesis
  const after = () => (end ? stopListening() : resumeListening(ECHO_TAIL_MS))
  if (!text || !synth) return after()
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
  let finished = false
  const done = () => {
    if (finished) return
    finished = true
    clearInterval(speakingTimer)
    pushLevel(0, 'tts', new Array<number>(BANDS).fill(0))
    setVoice('idle')
    after()
  }
  utterance.onstart = start
  utterance.onboundary = () => {
    envelope = 0.75 + Math.random() * 0.25
  }
  utterance.onend = done
  utterance.onerror = done
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
