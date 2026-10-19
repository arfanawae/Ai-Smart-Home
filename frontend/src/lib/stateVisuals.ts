import type { AIState } from './protocol'

/** How the AI Core draws each state (Blueprint §7.1). Shape + label must differ, not just colour. */
export type CoreShape = 'orb' | 'ring' | 'scan' | 'swirl' | 'stream' | 'timeout' | 'flash' | 'broken' | 'dim'

export interface StateVisual {
  /** CSS custom property holding the colour (read by the canvas too). */
  color: string
  shape: CoreShape
  /** Seconds per revolution of the outer tick ring; 0 = still. Negative = counter-rotate. */
  orbit: number
  label: string
  title: string
}

export const VISUALS: Record<AIState, StateVisual> = {
  IDLE: { color: '--color-cyan', shape: 'orb', orbit: 60, label: 'พร้อมรับคำสั่ง', title: 'STANDBY' },
  WAKE_DETECTED: { color: '--color-cyan', shape: 'flash', orbit: 12, label: 'ว่าไงครับ', title: 'AWAKE' },
  LISTENING: { color: '--color-cyan', shape: 'ring', orbit: 30, label: 'กำลังฟัง…', title: 'LISTENING' },
  TRANSCRIBING: { color: '--color-blue', shape: 'scan', orbit: 20, label: 'กำลังแปลงเสียง…', title: 'TRANSCRIBING' },
  THINKING: { color: '--color-violet', shape: 'swirl', orbit: 8, label: 'กำลังคิด…', title: 'THINKING' },
  EXECUTING: { color: '--color-blue', shape: 'stream', orbit: 10, label: 'กำลังส่งคำสั่ง…', title: 'EXECUTING' },
  WAITING_FOR_DEVICE: {
    color: '--color-amber',
    shape: 'timeout',
    orbit: 24,
    label: 'รออุปกรณ์ยืนยัน…',
    title: 'AWAITING DEVICE',
  },
  SPEAKING: { color: '--color-teal', shape: 'orb', orbit: 18, label: 'กำลังพูด…', title: 'SPEAKING' },
  SUCCESS: { color: '--color-green', shape: 'flash', orbit: 40, label: 'สำเร็จ', title: 'CONFIRMED' },
  ERROR: { color: '--color-red', shape: 'broken', orbit: 0, label: 'เกิดข้อผิดพลาด', title: 'ERROR' },
  DISCONNECTED: { color: '--color-grey', shape: 'dim', orbit: 0, label: 'ขาดการเชื่อมต่อ', title: 'OFFLINE' },
}

const colorCache = new Map<string, [number, number, number]>()

/** Resolve a CSS colour variable to RGB for canvas drawing (cached: called every animation frame). */
export function cssColor(variable: string): [number, number, number] {
  const cached = colorCache.get(variable)
  if (cached) return cached
  const value = getComputedStyle(document.documentElement).getPropertyValue(variable).trim()
  const hex = value.startsWith('#') ? value.slice(1) : ''
  const rgb: [number, number, number] =
    hex.length === 6 ? [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16)) as [number, number, number] : [46, 197, 255]
  if (hex.length === 6) colorCache.set(variable, rgb)
  return rgb
}
