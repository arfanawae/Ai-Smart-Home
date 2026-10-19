import { describe, expect, it } from 'vitest'
import type { CommandMsg, LightState } from '../lib/protocol'
import { effectiveState, initialData, MAX_CHAT, reduce } from './nexus'

const light = (on: boolean, brightness: number): LightState => ({
  online: true,
  on,
  brightness,
  updated_at: '2026-10-19T05:00:00',
  source: 'mock',
})

const command = (over: Partial<CommandMsg> = {}): CommandMsg => ({
  type: 'command',
  route: 'fastpath',
  tool: 'turn_light_on',
  args: { device_id: 'main_light' },
  status: 'succeeded',
  simulated: true,
  command_id: 'c1',
  device_id: 'main_light',
  previous: light(false, 100),
  reported: light(true, 100),
  error: null,
  latency_ms: 300,
  ts: '2026-10-19T05:00:01',
  ...over,
})

describe('reduce', () => {
  it('hello sets info, state and devices', () => {
    const s = reduce(initialData, {
      type: 'hello',
      simulated: true,
      llm: 'gemini-flash-lite-latest',
      languages: ['th-TH', 'en-US'],
      state: { type: 'state', state: 'LISTENING', label: '', detail: 'x' },
      devices: { main_light: light(false, 100) },
    })
    expect(s.info).toEqual({ simulated: true, llm: 'gemini-flash-lite-latest', languages: ['th-TH', 'en-US'] })
    expect(s.ai.state).toBe('LISTENING')
    expect(s.devices.main_light.on).toBe(false)
  })

  it('a command updates the device from the REPORTED state only', () => {
    const s = reduce(initialData, command())
    expect(s.devices.main_light).toEqual(light(true, 100))
    const failed = reduce(s, command({ status: 'timeout', reported: null }))
    expect(failed.devices.main_light).toEqual(light(true, 100)) // unknown result never overwrites known state
  })

  it('manual commands go to activity but not into the conversation', () => {
    const s = reduce(initialData, command({ route: 'manual' }))
    expect(s.activity).toHaveLength(1)
    expect(s.chat).toHaveLength(0)
  })

  it('chat is capped', () => {
    let s = initialData
    for (let i = 0; i < MAX_CHAT + 10; i++) {
      s = reduce(s, { type: 'user', text: String(i), source: 'text', language: null, ts: '' })
    }
    expect(s.chat).toHaveLength(MAX_CHAT)
    expect(s.chat.at(-1)).toMatchObject({ text: String(MAX_CHAT + 9) })
  })

  it('error messages are kept for display', () => {
    expect(reduce(initialData, { type: 'error', message: 'bad' }).lastError).toBe('bad')
  })
})

describe('effectiveState', () => {
  it('shows DISCONNECTED whenever the feed is not open', () => {
    const ai = { state: 'SPEAKING' as const, detail: '', since: 0 }
    expect(effectiveState({ connection: 'closed', ai, demo: false })).toBe('DISCONNECTED')
    expect(effectiveState({ connection: 'connecting', ai, demo: false })).toBe('DISCONNECTED')
    expect(effectiveState({ connection: 'open', ai, demo: false })).toBe('SPEAKING')
    expect(effectiveState({ connection: 'closed', ai, demo: true })).toBe('SPEAKING')
  })
})
