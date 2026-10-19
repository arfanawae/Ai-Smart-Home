import type { Tone } from '../components/ui/HudPanel'
import type { Connection, Info } from '../store/nexus'
import type { LightState } from './protocol'

export interface SystemRow {
  key: string
  name: string
  tone: Tone
  value: string
  hint?: string
}

/**
 * Status of each part of the chain (Blueprint §3.A). Only facts the feed actually reports are shown as
 * connected — parts that do not exist yet say so instead of pretending.
 */
export function systemRows(connection: Connection, info: Info | null, light: LightState | undefined, demo: boolean): SystemRow[] {
  const open = connection === 'open'
  const feed: SystemRow = demo
    ? { key: 'feed', name: 'NEXUS', tone: 'warn', value: 'โหมดสาธิต (DEMO)', hint: 'ข้อมูลทั้งหมดในหน้านี้สร้างขึ้นเพื่อแสดงผล' }
    : {
        key: 'feed',
        name: 'NEXUS',
        tone: open ? 'ok' : connection === 'connecting' ? 'warn' : 'bad',
        value: open ? 'เชื่อมต่อแล้ว' : connection === 'connecting' ? 'กำลังเชื่อมต่อ…' : 'ขาดการเชื่อมต่อ',
      }
  if (!open && !demo) return [feed]
  const voiceOn = (info?.languages.length ?? 0) > 0
  return [
    feed,
    {
      key: 'voice',
      name: 'ระบบเสียง',
      tone: voiceOn ? 'ok' : 'off',
      value: voiceOn ? `Wake word · ${info?.languages.join(' + ')}` : 'ปิด (โหมด serve)',
    },
    {
      key: 'llm',
      name: 'AI (LLM)',
      tone: info?.llm ? 'ok' : 'off',
      value: info?.llm ? `Gemini · ${info.llm}` : 'ยังไม่ได้ตั้งค่า',
      hint: info?.llm ? 'ตั้งค่าไว้แล้ว (สถานะจริงดูได้จากผลการตอบ)' : undefined,
    },
    {
      key: 'device',
      name: 'ไฟห้องหลัก',
      tone: !light ? 'off' : light.online ? (light.source === 'mock' ? 'warn' : 'ok') : 'bad',
      value: !light ? 'ไม่ทราบสถานะ' : `${light.online ? 'ออนไลน์' : 'ออฟไลน์'}${light.source === 'mock' ? ' · จำลอง' : ''}`,
    },
    info?.devicesVia === 'mcp'
      ? { key: 'mcp', name: 'MCP Server', tone: 'ok', value: 'เชื่อมต่อแล้ว · stdio', hint: 'ทุกคำสั่งไปอุปกรณ์ผ่าน MCP Server' }
      : { key: 'mcp', name: 'MCP Server', tone: 'off', value: info?.devicesVia === 'local' ? 'ไม่ได้ใช้ (local)' : 'ไม่ทราบ' },
    { key: 'unoq', name: 'Arduino UNO Q', tone: 'off', value: 'ยังไม่เชื่อมต่อ', hint: 'จะเพิ่มใน M5' },
  ]
}
