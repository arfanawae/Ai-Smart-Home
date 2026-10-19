import { describe, expect, it } from 'vitest'
import type { Info } from '../store/nexus'
import type { LightState } from './protocol'
import { systemRows } from './system'

const info = (simulated: boolean): Info => ({ simulated, llm: null, languages: [], devicesVia: 'mcp' })
const light = (online: boolean): LightState =>
  ({ online, on: true, brightness: 50, source: 'device', updated_at: '' }) as LightState
const board = (i: Info | null, l?: LightState) => systemRows('open', i, l, false).find((r) => r.key === 'unoq')!

describe('Arduino UNO Q row', () => {
  it('is connected when the real device reports the light online', () => {
    expect(board(info(false), light(true))).toMatchObject({ tone: 'ok', value: expect.stringContaining('เชื่อมต่อแล้ว') })
  })
  it('is disconnected when the board stops reporting', () => {
    expect(board(info(false), light(false)).tone).toBe('bad')
  })
  it('is not used in mock mode', () => {
    expect(board(info(true), light(true)).tone).toBe('off')
  })
})
