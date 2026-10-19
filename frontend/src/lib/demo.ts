// ?demo — preview every AI Core state without a backend (Blueprint M2: "Dev panel สำหรับสลับ state").
// ?demo&state=THINKING freezes one state (used for screenshots). Everything is labelled DEMO in the UI
// and the controls are disabled, so demo data can never be mistaken for the real system.

import { useNexus } from '../store/nexus'
import { pushLevel } from './levels'
import { AI_STATES, type AIState } from './protocol'

const CYCLE: AIState[] = AI_STATES.filter((s) => s !== 'DISCONNECTED')

export function demoParams(search = window.location.search): { enabled: boolean; frozen: AIState | null } {
  const params = new URLSearchParams(search)
  const state = params.get('state')
  return {
    enabled: params.has('demo'),
    frozen: state && (AI_STATES as readonly string[]).includes(state) ? (state as AIState) : null,
  }
}

export function startDemo(frozen: AIState | null): () => void {
  const store = useNexus.getState()
  store.setDemo(true)
  store.setConnection('open')
  const ts = new Date().toISOString()
  store.apply({
    type: 'hello',
    simulated: true,
    llm: 'demo',
    languages: ['th-TH', 'en-US'],
    devices_via: 'mcp',
    state: { type: 'state', state: frozen ?? 'IDLE', label: '', detail: '' },
    devices: { main_light: { online: true, on: true, brightness: 80, updated_at: ts, source: 'mock' } },
  })
  store.apply({ type: 'user', text: 'ไฟสว่างเกินไป', source: 'voice', language: 'th-TH', ts })
  store.apply({
    type: 'command',
    route: 'llm',
    tool: 'adjust_brightness',
    args: { device_id: 'main_light', delta: -20 },
    status: 'succeeded',
    simulated: true,
    command_id: 'demo',
    device_id: 'main_light',
    previous: { online: true, on: true, brightness: 100, updated_at: ts, source: 'mock' },
    reported: { online: true, on: true, brightness: 80, updated_at: ts, source: 'mock' },
    error: null,
    latency_ms: 312,
    ts,
  })
  store.apply({ type: 'reply', text: 'ลดความสว่างเหลือ 80% แล้วครับ (การจำลอง) ต้องการลดอีกไหมครับ', route: 'llm', ts })

  let i = 0
  const cycle = frozen
    ? null
    : setInterval(() => {
        i = (i + 1) % CYCLE.length
        store.apply({ type: 'state', state: CYCLE[i], label: '', detail: 'โหมดสาธิต' })
      }, 2200)
  // Synthetic loudness so LISTENING/SPEAKING can be previewed (labelled DEMO like everything else here).
  const level = setInterval(() => {
    const state = useNexus.getState().ai.state
    const source = state === 'SPEAKING' ? 'tts' : 'mic'
    const t = performance.now() / 1000
    pushLevel(Math.max(0, 0.45 + 0.35 * Math.sin(t * 7) * Math.sin(t * 2.3)), source)
  }, 60)

  return () => {
    if (cycle) clearInterval(cycle)
    clearInterval(level)
  }
}
