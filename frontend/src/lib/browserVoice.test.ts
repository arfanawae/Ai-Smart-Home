import { describe, expect, it } from 'vitest'
import { effectiveState, initialData } from '../store/nexus'
import { bands, encodeWav, loudness, pickVoice, resample } from './browserVoice'

describe('browser voice helpers', () => {
  it('encodes 16-bit mono WAV the backend accepts', () => {
    const wav = encodeWav(new Float32Array([0, 0.5, -0.5, 1, -1]), 16000)
    const text = (a: number, b: number) => String.fromCharCode(...wav.slice(a, b))
    expect([text(0, 4), text(8, 12), text(36, 40)]).toEqual(['RIFF', 'WAVE', 'data'])
    const v = new DataView(wav.buffer)
    expect([v.getUint16(22, true), v.getUint32(24, true), v.getUint16(34, true)]).toEqual([1, 16000, 16])
    expect(wav.length).toBe(44 + 5 * 2)
    expect(v.getInt16(44 + 3 * 2, true)).toBe(32767)
  })

  it('resamples 48 kHz to 16 kHz', () => {
    expect(resample(new Float32Array(4800), 48000, 16000).length).toBe(1600)
  })

  it('maps loudness like the backend (-60…-10 dBFS → 0…1)', () => {
    expect(loudness(0)).toBe(0)
    expect(loudness(1)).toBe(1)
    expect(loudness(10 ** (-35 / 20))).toBeCloseTo(0.5)
  })

  it('turns a dB spectrum into 24 bands, a 300 Hz peak in the low bands', () => {
    const spectrum = new Float32Array(512).fill(-120)
    spectrum[Math.round(300 / (8000 / 512))] = -40
    const b = bands(spectrum, 16000)
    expect(b).toHaveLength(24)
    expect(b.indexOf(Math.max(...b))).toBeLessThan(8)
  })

  it('prefers a free neural (Edge "Natural"/"Online") voice in the reply language', () => {
    const v = (name: string, lang: string) => ({ name, lang }) as SpeechSynthesisVoice
    const voices = [v('Microsoft David', 'en-US'), v('Microsoft Pattara', 'th-TH'), v('Microsoft Premwadee Online (Natural)', 'th-TH')]
    expect(pickVoice(voices, 'th-TH')?.name).toContain('Premwadee')
    expect(pickVoice(voices, 'en-US')?.name).toBe('Microsoft David')
    expect(pickVoice([v('x', 'fr-FR')], 'th-TH')).toBeUndefined()
  })

  it('the core shows LISTENING while this browser records', () => {
    const open = { ...initialData, connection: 'open' as const }
    expect(effectiveState({ ...open, browserVoice: 'recording' })).toBe('LISTENING')
    expect(effectiveState({ ...open, browserVoice: 'speaking' })).toBe('SPEAKING')
    expect(effectiveState({ ...open, browserVoice: 'idle' })).toBe('IDLE')
  })
})
