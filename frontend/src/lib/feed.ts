import { useNexus } from '../store/nexus'
import { speakReply } from './browserVoice'
import { pushLevel } from './levels'
import type { ClientMsg, FeedMsg } from './protocol'

export const FEED_URL: string = import.meta.env.VITE_NEXUS_WS ?? 'ws://127.0.0.1:8765'
const BACKOFF_S = [1, 2, 4, 8, 16, 30]

let socket: WebSocket | null = null
let timer: ReturnType<typeof setTimeout> | null = null
let attempt = 0
let stopped = true

function handle(raw: string): void {
  let msg: FeedMsg
  try {
    msg = JSON.parse(raw) as FeedMsg
  } catch {
    return
  }
  if (msg.type === 'level') pushLevel(msg.rms, msg.source, msg.bands)
  else if (msg.type === 'speak') speakReply(msg.text, msg.lang)
  else useNexus.getState().apply(msg)
}

function connect(): void {
  const { setConnection } = useNexus.getState()
  setConnection('connecting')
  const ws = new WebSocket(FEED_URL)
  socket = ws
  ws.onopen = () => {
    attempt = 0
    setConnection('open')
  }
  ws.onmessage = (e) => handle(String(e.data))
  ws.onclose = () => {
    if (socket !== ws) return
    socket = null
    if (stopped) return
    const delay = BACKOFF_S[Math.min(attempt, BACKOFF_S.length - 1)]
    attempt += 1
    setConnection('closed', Date.now() + delay * 1000)
    timer = setTimeout(connect, delay * 1000)
  }
}

export function startFeed(): () => void {
  stopped = false
  connect()
  return () => {
    stopped = true
    if (timer) clearTimeout(timer)
    socket?.close()
    socket = null
  }
}

/** Skip the backoff wait ("ลองตอนนี้" button). */
export function reconnectNow(): void {
  if (timer) clearTimeout(timer)
  attempt = 0
  if (!socket) connect()
}

export function send(msg: ClientMsg): boolean {
  if (socket?.readyState !== WebSocket.OPEN) return false
  socket.send(JSON.stringify(msg))
  return true
}
