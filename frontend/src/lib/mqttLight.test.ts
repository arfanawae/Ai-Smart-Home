import { describe, expect, it } from 'vitest'
import { commandPayload, parseState } from './mqttLight'

const now = new Date('2026-10-20T08:00:00Z')

describe('MQTT light messages', () => {
  it('commands: off is value 0, on is clamped to 1–100', () => {
    expect(JSON.parse(commandPayload(true, 60))).toEqual({ state: true, value: 60 })
    expect(JSON.parse(commandPayload(false, 60))).toEqual({ state: false, value: 0 })
    expect(JSON.parse(commandPayload(true, 0))).toEqual({ state: true, value: 1 })
    expect(JSON.parse(commandPayload(true, 250))).toEqual({ state: true, value: 100 })
  })

  it('reads the board state', () => {
    expect(parseState('{"online":true,"state":true,"value":60,"mcu_ok":true}', undefined, now)).toEqual({
      online: true, on: true, brightness: 60, updated_at: now.toISOString(), source: 'device',
    })
  })

  it('last will / MCU failure → offline, keeping the last known values', () => {
    const prev = { online: true, on: true, brightness: 40, updated_at: '', source: 'device' as const }
    expect(parseState('{"online":false}', prev, now)).toMatchObject({ online: false, on: true, brightness: 40 })
    expect(parseState('{"online":true,"mcu_ok":false,"error":"x"}', prev, now)?.online).toBe(false)
  })

  it('ignores anything else', () => {
    for (const bad of ['not json', '[]', '{"state":"on","value":1}', '{"state":true,"value":101}', 'null']) {
      expect(parseState(bad, undefined, now)).toBeNull()
    }
  })
})
