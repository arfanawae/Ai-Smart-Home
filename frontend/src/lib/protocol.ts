// Wire format of the NEXUS UI feed — mirrors voice/nexus_voice/broadcast.py and backend nexus.events.to_dict.

export const AI_STATES = [
  'IDLE',
  'WAKE_DETECTED',
  'LISTENING',
  'TRANSCRIBING',
  'THINKING',
  'EXECUTING',
  'WAITING_FOR_DEVICE',
  'SPEAKING',
  'SUCCESS',
  'ERROR',
  'DISCONNECTED',
] as const
export type AIState = (typeof AI_STATES)[number]

export interface LightState {
  online: boolean
  on: boolean
  brightness: number
  updated_at: string
  source: 'mock' | 'device'
}

export type CommandStatus = 'succeeded' | 'failed' | 'timeout' | 'rejected'
export type Route = 'fastpath' | 'llm' | 'manual' | 'system'

export interface StateMsg {
  type: 'state'
  state: AIState
  label: string
  detail: string
  ts?: string
}

export interface UserMsg {
  type: 'user'
  text: string
  source: 'voice' | 'text'
  language: string | null
  ts: string
}

export interface ReplyMsg {
  type: 'reply'
  text: string
  route: Route
  ts: string
}

export interface CommandMsg {
  type: 'command'
  route: Route
  tool: string
  args: Record<string, unknown>
  status: CommandStatus
  simulated: boolean
  command_id: string | null
  device_id: string | null
  previous: LightState | null
  reported: LightState | null
  error: string | null
  latency_ms: number | null
  ts: string
}

export interface LevelMsg {
  type: 'level'
  rms: number
  source: 'mic' | 'tts'
}

export interface HelloMsg {
  type: 'hello'
  simulated?: boolean
  llm?: string | null
  languages?: string[]
  /** How the voice service reaches devices: through the MCP server, or in-process. */
  devices_via?: 'mcp' | 'local'
  state: StateMsg
  devices: Record<string, LightState>
}

export interface ErrorMsg {
  type: 'error'
  message: string
}

/** A device changed outside any command — e.g. the board stopped polling (heartbeat lost). */
export interface DeviceMsg {
  type: 'device'
  device_id: string
  state: LightState
  ts: string
}

export type FeedMsg = HelloMsg | StateMsg | UserMsg | ReplyMsg | CommandMsg | LevelMsg | ErrorMsg | DeviceMsg

export type ClientMsg =
  | { type: 'text'; text: string }
  | { type: 'command'; tool: string; args: Record<string, unknown> }
