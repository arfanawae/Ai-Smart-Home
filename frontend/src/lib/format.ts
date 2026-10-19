import type { CommandMsg, CommandStatus, Route } from './protocol'

export function timeOf(iso: string | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString('th-TH', { hour12: false })
}

export function actionLabel(cmd: Pick<CommandMsg, 'tool' | 'args'>): string {
  switch (cmd.tool) {
    case 'turn_light_on':
      return 'เปิดไฟห้องหลัก'
    case 'turn_light_off':
      return 'ปิดไฟห้องหลัก'
    case 'set_brightness':
      return `ตั้งความสว่าง ${String(cmd.args.level ?? '?')}%`
    case 'adjust_brightness': {
      const delta = Number(cmd.args.delta ?? 0)
      return `ปรับความสว่าง ${delta > 0 ? '+' : ''}${delta}%`
    }
    case 'get_device_status':
      return 'ตรวจสถานะไฟ'
    default:
      return cmd.tool
  }
}

/** Never shows plain "สำเร็จ" for a simulation — Blueprint principle 1 (Truth First). */
export function statusLabel(status: CommandStatus, simulated: boolean): string {
  switch (status) {
    case 'succeeded':
      return simulated ? 'จำลองสำเร็จ' : 'สำเร็จ'
    case 'failed':
      return 'ล้มเหลว'
    case 'timeout':
      return 'ไม่ตอบกลับ'
    case 'rejected':
      return 'ถูกปฏิเสธ'
  }
}

export const STATUS_COLOR: Record<CommandStatus, string> = {
  succeeded: 'text-green',
  failed: 'text-red',
  timeout: 'text-amber',
  rejected: 'text-red',
}

export const ROUTE_LABEL: Record<Route, string> = {
  fastpath: 'fast path',
  llm: 'Gemini',
  manual: 'ปุ่มควบคุม',
  system: 'ระบบ',
}
