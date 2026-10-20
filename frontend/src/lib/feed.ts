import { useNexus } from '../store/nexus'
import { speakReply } from './browserVoice'
import { pushLevel } from './levels'
import type { ClientMsg, FeedMsg } from './protocol'

// Same origin as the page: Vite forwards /nexus-ws to NEXUS (ws://127.0.0.1:8765). That works on the
// laptop and through an HTTPS tunnel (Cloudflare) from a phone — where 127.0.0.1 would be the phone
// itself, and a plain ws:// from an https:// page would be blocked as mixed content.
// A ?token=… in the page URL is remembered and sent along (NEXUS_WEB_TOKEN on the backend).
export const FEED_URL: string = import.meta.env.VITE_NEXUS_WS ?? sameOriginFeed()

function sameOriginFeed(): string {
  if (typeof window === 'undefined') return 'ws://127.0.0.1:8765'
  const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = rememberToken()
  return `${scheme}//${window.location.host}/nexus-ws${token ? `?token=${encodeURIComponent(token)}` : ''}`
}

function rememberToken(): string | null {
  const fromUrl = new URLSearchParams(window.location.search).get('token')
  try {
    if (fromUrl) localStorage.setItem('nexus-token', fromUrl)
    return fromUrl ?? localStorage.getItem('nexus-token')
  } catch {
    return fromUrl // private mode: storage unavailable
  }
}
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
  else if (msg.type === 'speak') speakReply(msg.text, msg.lang, msg.end)
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
