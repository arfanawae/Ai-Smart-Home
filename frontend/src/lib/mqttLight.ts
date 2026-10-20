// Light control through an MQTT broker — for trying the dashboard without the laptop in the loop.
//
//   page ── {"state": true, "value": 60} ──▶ <topic>/led    ──▶ board (main.py + mqtt_link.py)
//   page ◀── {"online", "state", "value"} ── <topic>/state ◀── board (retained; "last will" when it drops)
//
// Enabled only when VITE_MQTT_TOPIC is set (frontend/.env.local, never committed). The public EMQX
// broker has no password: the long random topic is the only protection, so this is for experiments
// with the on-board LED. Voice commands still go through NEXUS → relay; the board reports the
// resulting state here too.

import type { MqttClient } from 'mqtt'
import type { LightState } from './protocol'
import { useNexus } from '../store/nexus'

export const MQTT_URL: string = import.meta.env.VITE_MQTT_URL || 'wss://broker.emqx.io:8084/mqtt'
export const MQTT_TOPIC: string | undefined = import.meta.env.VITE_MQTT_TOPIC || undefined
export const mqttEnabled = Boolean(MQTT_TOPIC)

let client: MqttClient | null = null

/** Board state message → the light's state for the store (null = not a state we understand). */
export function parseState(payload: string, previous: LightState | undefined, now = new Date()): LightState | null {
  let msg: unknown
  try {
    msg = JSON.parse(payload)
  } catch {
    return null
  }
  if (typeof msg !== 'object' || msg === null) return null
  const m = msg as { online?: unknown; state?: unknown; value?: unknown; mcu_ok?: unknown }
  const base: LightState = previous ?? { online: false, on: false, brightness: 0, updated_at: now.toISOString(), source: 'device' }
  if (m.online === false || m.mcu_ok === false) return { ...base, online: false, updated_at: now.toISOString() }
  if (typeof m.state !== 'boolean' || typeof m.value !== 'number' || m.value < 0 || m.value > 100) return null
  return { online: true, on: m.state, brightness: Math.round(m.value), updated_at: now.toISOString(), source: 'device' }
}

/** {"state", "value"} for the board: off = value 0; on = the requested level (1–100). */
export function commandPayload(on: boolean, level: number): string {
  const value = on ? Math.max(1, Math.min(100, Math.round(level))) : 0
  return JSON.stringify({ state: on, value })
}

export function startMqtt(): () => void {
  if (!MQTT_TOPIC) return () => {}
  useNexus.getState().setMqtt('connecting')
  let stopped = false
  let c: MqttClient | null = null
  // Loaded only when MQTT is configured, so the library is not in everyone's main bundle.
  void import('mqtt').then(({ default: mqtt }) => {
    if (stopped) return
    c = mqtt.connect(MQTT_URL, {
      clientId: `NEXUS-Web-${Math.random().toString(16).slice(2, 10)}`,
      clean: true,
      reconnectPeriod: 3000,
      connectTimeout: 10_000,
    })
    client = c
    const conn = c
    conn.on('connect', () => {
      useNexus.getState().setMqtt('open')
      conn.subscribe(`${MQTT_TOPIC}/state`, { qos: 0 })
    })
    conn.on('reconnect', () => useNexus.getState().setMqtt('connecting'))
    conn.on('offline', () => useNexus.getState().setMqtt('closed'))
    conn.on('close', () => useNexus.getState().setMqtt('closed'))
    conn.on('message', (topic, payload) => {
      if (topic !== `${MQTT_TOPIC}/state`) return
      const s = useNexus.getState()
      const state = parseState(payload.toString(), s.devices.main_light)
      if (state) s.apply({ type: 'device', device_id: 'main_light', state, ts: state.updated_at })
    })
  })
  return () => {
    stopped = true
    client = null
    c?.end(true)
  }
}

/** Publish a light command; false if the broker is not connected (nothing was sent). */
export function publishLight(on: boolean, level: number): boolean {
  if (!client?.connected || !MQTT_TOPIC) return false
  client.publish(`${MQTT_TOPIC}/led`, commandPayload(on, level), { qos: 0, retain: false })
  return true
}
