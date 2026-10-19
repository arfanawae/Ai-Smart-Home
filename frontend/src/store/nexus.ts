import { create } from 'zustand'
import type { AIState, CommandMsg, FeedMsg, LightState, ReplyMsg, UserMsg } from '../lib/protocol'

export type Connection = 'connecting' | 'open' | 'closed'

export type ChatItem =
  | ({ id: number } & UserMsg)
  | ({ id: number } & ReplyMsg)
  | ({ id: number } & CommandMsg)

export interface Info {
  simulated: boolean
  llm: string | null
  /** Empty in `serve` mode: no microphone / wake word running. */
  languages: string[]
  devicesVia: 'mcp' | 'local' | null
}

export interface NexusData {
  connection: Connection
  retryAt: number | null
  demo: boolean
  info: Info | null
  ai: { state: AIState; detail: string; since: number }
  devices: Record<string, LightState>
  chat: ChatItem[]
  activity: CommandMsg[]
  lastError: string | null
  nextId: number
}

export const MAX_CHAT = 60
export const MAX_ACTIVITY = 50

export const initialData: NexusData = {
  connection: 'connecting',
  retryAt: null,
  demo: false,
  info: null,
  ai: { state: 'IDLE', detail: '', since: 0 },
  devices: {},
  chat: [],
  activity: [],
  lastError: null,
  nextId: 1,
}

/** Pure state transition for one feed message ("level" is handled outside the store — see levels.ts). */
export function reduce(s: NexusData, msg: FeedMsg, now = Date.now()): NexusData {
  switch (msg.type) {
    case 'hello':
      return {
        ...s,
        info: {
          simulated: msg.simulated ?? true,
          llm: msg.llm ?? null,
          languages: msg.languages ?? [],
          devicesVia: msg.devices_via ?? null,
        },
        ai: { state: msg.state.state, detail: msg.state.detail, since: now },
        devices: { ...s.devices, ...msg.devices },
        lastError: null,
      }
    case 'state':
      return { ...s, ai: { state: msg.state, detail: msg.detail, since: now } }
    case 'user':
    case 'reply':
      return { ...s, chat: [...s.chat, { ...msg, id: s.nextId }].slice(-MAX_CHAT), nextId: s.nextId + 1 }
    case 'command': {
      const devices =
        msg.reported && msg.device_id ? { ...s.devices, [msg.device_id]: msg.reported } : s.devices
      // Manual button presses don't clutter the conversation; AI-issued commands appear inline with the reply.
      const chat = msg.route === 'manual' ? s.chat : [...s.chat, { ...msg, id: s.nextId }].slice(-MAX_CHAT)
      return {
        ...s,
        devices,
        chat,
        activity: [msg, ...s.activity].slice(0, MAX_ACTIVITY),
        nextId: s.nextId + 1,
      }
    }
    case 'device':
      return { ...s, devices: { ...s.devices, [msg.device_id]: msg.state } }
    case 'error':
      return { ...s, lastError: msg.message }
    case 'level':
      return s
  }
}

interface NexusStore extends NexusData {
  apply: (msg: FeedMsg) => void
  setConnection: (connection: Connection, retryAt?: number | null) => void
  setDemo: (demo: boolean) => void
}

export const useNexus = create<NexusStore>()((set) => ({
  ...initialData,
  apply: (msg) => set((s) => reduce(s, msg)),
  setConnection: (connection, retryAt = null) => set({ connection, retryAt }),
  setDemo: (demo) => set({ demo }),
}))

/** What the AI Core shows: a lost feed always wins, so the UI never pretends to be connected. */
export function effectiveState(s: Pick<NexusData, 'connection' | 'ai' | 'demo'>): AIState {
  if (!s.demo && s.connection !== 'open') return 'DISCONNECTED'
  return s.ai.state
}
