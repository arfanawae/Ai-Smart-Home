import { Bot, LayoutDashboard, Lightbulb, ScrollText, Settings, Workflow, type LucideIcon } from 'lucide-react'

export type Page = 'dashboard' | 'devices' | 'assistant' | 'activity' | 'automation' | 'settings'

export const PAGES: { id: Page; label: string; icon: LucideIcon }[] = [
  { id: 'dashboard', label: 'แดชบอร์ด', icon: LayoutDashboard },
  { id: 'devices', label: 'อุปกรณ์', icon: Lightbulb },
  { id: 'assistant', label: 'ผู้ช่วย AI', icon: Bot },
  { id: 'activity', label: 'บันทึกกิจกรรม', icon: ScrollText },
  { id: 'automation', label: 'อัตโนมัติ', icon: Workflow },
  { id: 'settings', label: 'ตั้งค่าระบบ', icon: Settings },
]
