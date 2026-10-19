// NEXUS relay — the laptop side of the link to the Arduino UNO Q.
//
// The board cannot accept inbound connections (its Linux container's firewall blocks them), so it
// polls this server instead:
//
//   NEXUS (MCP server) ── POST /api/v1/control/led ──▶ queue ◀── GET  /api/v1/mcu/poll   ── UNO Q
//                                                               (long poll: held until a command)
//                      ◀─ GET  /api/v1/control/status ─ state ◀── POST /api/v1/mcu/report ──
//
// Only one LED exists, so the queue holds at most one command: a newer command replaces one the
// board has not picked up yet. Every state the relay reports comes from the board's own report.
//
// Config (relay/.env or environment): NEXUS_TOKEN (required, ≥ 16 chars), RELAY_PORT, RELAY_HOST.

'use strict';

const crypto = require('node:crypto');
const path = require('node:path');
const express = require('express');

try {
  process.loadEnvFile(path.join(__dirname, '.env'));
} catch (e) {
  if (e.code !== 'ENOENT') throw e;
}

const PORT = Number(process.env.RELAY_PORT ?? 8770);
const HOST = process.env.RELAY_HOST || '0.0.0.0';
const TOKEN = (process.env.NEXUS_TOKEN || '').trim();

const ONLINE_WINDOW_MS = 2000; // no poll open and none in the last 2 s → offline
const MAX_POLL_WAIT_MS = 5000; // a long poll (?wait=) is held at most this long
const REDELIVER_MS = 1500; // handed out but not reported → hand it out again (set_led is idempotent)
const MAX_DELIVERIES = 3;
const COMMAND_TTL_MS = 5000; // any command older than this is dropped, so a stale "on" cannot fire later
const MAX_WAIT_MS = 5000;

if (TOKEN.length < 16) {
  console.error('NEXUS_TOKEN is missing or shorter than 16 characters — set it in relay/.env');
  process.exit(2);
}

// ── state ─────────────────────────────────────────────────────────────────────────────────────
const led = { known: false, on: false, brightness: 0, mcuOk: true, updatedAt: null, error: null };
let lastPollAt = 0;
let pending = null; // { id, value, createdAt, deliveredAt, deliveries }
const waiters = new Map(); // command id → [settle(outcome)]
let heldPoll = null; // the board's open long poll: { finish(command) }
let openPolls = 0;

const boardOnline = () => openPolls > 0 || Date.now() - lastPollAt <= ONLINE_WINDOW_MS;
const log = (msg) => console.log(`${new Date().toISOString().slice(11, 23)}  ${msg}`);

function settle(id, outcome) {
  for (const done of waiters.get(id) ?? []) done(outcome);
  waiters.delete(id);
}

function dropPending(reason) {
  if (!pending) return;
  const { id } = pending;
  pending = null;
  log(`command ${id} dropped: ${reason}`);
  settle(id, { kind: 'dropped', reason });
}

function expirePending() {
  if (!pending) return;
  const age = Date.now() - pending.createdAt;
  if (age > COMMAND_TTL_MS) {
    // Covers a board that took the command and then hung: after Stop/Run it must not get it again.
    dropPending(pending.deliveredAt ? 'no report from the board in time' : 'the board did not pick it up');
  } else if (pending.deliveries >= MAX_DELIVERIES && Date.now() - pending.deliveredAt > REDELIVER_MS)
    dropPending(`no report after ${MAX_DELIVERIES} deliveries`);
}
setInterval(expirePending, 250).unref();

function statusBody() {
  return {
    online: boardOnline(),
    known: led.known,
    on: led.on,
    brightness: led.brightness,
    mcu_ok: led.mcuOk,
    error: led.error,
    updated_at: led.updatedAt,
    last_poll_ms_ago: lastPollAt ? Date.now() - lastPollAt : null,
    pending: pending && { id: pending.id, value: pending.value, delivered: Boolean(pending.deliveredAt) },
  };
}

// ── validation ────────────────────────────────────────────────────────────────────────────────
const isLevel = (v) => Number.isInteger(v) && v >= 0 && v <= 100;
const toBool = (v) => (v === true || v === 1 || v === 'true' ? true : v === false || v === 0 || v === 'false' ? false : null);

/** {on, brightness} or {value} → the single 0–100 value the sketch takes (0 = off). */
function commandValue(body) {
  if (body.value !== undefined) {
    const val = Number(body.value);
    return isLevel(val) ? val : null;
  }
  const on = toBool(body.on);
  if (on === null) return null;
  if (!on) return 0;
  
  // สั่ง on: true ให้ดึงค่า brightness (ถ้าไม่ระบุให้เป็น 100%)
  // ค่าที่ไม่ถูกต้อง (เช่น 101, "abc", หรือ 0 คู่กับ on: true) ต้องถูกปฏิเสธ — ห้ามเดาเป็นเปิดไฟ 100%
  const rawBrightness = body.brightness !== undefined ? Number(body.brightness) : 100;
  return isLevel(rawBrightness) && rawBrightness > 0 ? rawBrightness : null;
}

// ── app ───────────────────────────────────────────────────────────────────────────────────────
const app = express();
app.disable('x-powered-by');
app.use(express.json({ limit: '1kb' }));

const tokenDigest = crypto.createHash('sha256').update(TOKEN).digest();
app.use('/api/v1', (req, res, next) => {
  const match = /^Bearer (.+)$/.exec(req.get('authorization') || '');
  const given = crypto.createHash('sha256').update(match ? match[1].trim() : '').digest();
  if (!match || !crypto.timingSafeEqual(given, tokenDigest)) {
    return res.status(401).json({ error: 'unauthorized' });
  }
  next();
});

/** The pending command if it is due for (re)delivery — marked as delivered — else null. */
function takeCommand() {
  expirePending();
  const now = Date.now();
  if (!pending || (pending.deliveredAt && now - pending.deliveredAt <= REDELIVER_MS)) return null;
  pending.deliveredAt = now;
  pending.deliveries += 1;
  if (pending.deliveries > 1) log(`command ${pending.id} re-sent (${pending.deliveries})`);
  return { id: pending.id, action: 'set_led', value: pending.value };
}

// Board → relay: the next command, if any. With ?wait=<ms> the request is held open until a command
// arrives (sent the moment it is queued) or the wait ends — near-instant delivery, and an open poll
// is itself the heartbeat.
app.get('/api/v1/mcu/poll', (req, res) => {
  const wasOnline = boardOnline();
  lastPollAt = Date.now();
  if (!wasOnline) log(`board online (${req.ip})`);
  const command = takeCommand();
  const waitMs = Math.min(Number(req.query.wait) || 0, MAX_POLL_WAIT_MS);
  if (command || waitMs <= 0) return res.json({ command });

  heldPoll?.finish(null); // one board: an older open poll is superseded
  let done = false;
  const close = () => {
    done = true;
    clearTimeout(timer);
    openPolls -= 1;
    if (heldPoll === hold) heldPoll = null;
  };
  const hold = {
    finish(cmd) {
      if (done) return;
      close();
      lastPollAt = Date.now();
      res.json({ command: cmd });
    },
  };
  const timer = setTimeout(() => hold.finish(null), waitMs);
  openPolls += 1;
  heldPoll = hold;
  res.on('close', () => done || close()); // the board went away mid-poll: not a heartbeat
});

// Board → relay: what the MCU says it applied (after a command, and once at board start-up).
app.post('/api/v1/mcu/report', (req, res) => {
  const body = req.body ?? {};
  const id = typeof body.id === 'string' ? body.id : null;
  lastPollAt = Date.now();

  if (body.ok === false) {
    led.mcuOk = false;
    led.error = String(body.error ?? 'MCU error').slice(0, 200);
    log(`MCU error${id ? ` for ${id}` : ''}: ${led.error}`);
    if (id && pending?.id === id) {
      pending = null;
      settle(id, { kind: 'mcu_error', reason: led.error });
    }
    return res.json({ ok: true });
  }

  const on = toBool(body.on);
  const brightness = Number(body.brightness);
  if (on === null || !isLevel(brightness)) {
    return res.status(400).json({ error: 'on (bool) and brightness (0-100) are required' });
  }
  Object.assign(led, { known: true, on, brightness, mcuOk: true, error: null });
  led.updatedAt = new Date().toISOString();
  log(`report${id ? ` ${id}` : ''}: ${on ? 'on' : 'off'} ${brightness}%`);
  if (id && pending?.id === id) {
    pending = null;
    settle(id, { kind: 'reported' });
  }
  res.json({ ok: true });
});

// NEXUS → relay: queue an LED command. ?wait=<ms> holds the reply until the board reports.
app.post('/api/v1/control/led', async (req, res) => {
  const body = req.body ?? {};
  const value = commandValue(body);
  if (value === null) {
    return res.status(400).json({
      error: 'send {"on": true|false, "brightness": 1-100} or {"value": 0-100}',
    });
  }
  const waitMs = Math.min(Number(req.query.wait) || 0, MAX_WAIT_MS);
  if (waitMs > 0 && !boardOnline()) {
    // Refuse instead of queueing: the caller is told nothing changed, and nothing fires later.
    return res.status(503).json({ ...statusBody(), error: 'board offline (no poll in the last 2 s)' });
  }

  const id = typeof body.command_id === 'string' && body.command_id ? body.command_id.slice(0, 64) : crypto.randomUUID();
  if (pending) dropPending(`replaced by ${id}`);
  pending = { id, value, createdAt: Date.now(), deliveredAt: 0, deliveries: 0 };
  log(`command ${id}: set_led ${value}`);
  if (heldPoll) {
    const command = takeCommand();
    if (command) heldPoll.finish(command); // the board is waiting on an open poll: deliver now
  }

  if (waitMs === 0) {
    return res.status(202).json({ queued: true, command_id: id, board_online: boardOnline() });
  }

  const outcome = await new Promise((resolve) => {
    const timer = setTimeout(() => resolve({ kind: 'timeout' }), waitMs);
    const list = waiters.get(id) ?? [];
    list.push((o) => {
      clearTimeout(timer);
      resolve(o);
    });
    waiters.set(id, list);
  });

  switch (outcome.kind) {
    case 'reported':
      return res.json({ ok: true, command_id: id, ...statusBody() });
    case 'mcu_error':
      return res.status(502).json({ ...statusBody(), error: `MCU: ${outcome.reason}`, command_id: id });
    case 'dropped':
      return res.status(409).json({ ...statusBody(), error: outcome.reason, command_id: id });
    default: {
      const delivered = Boolean(pending?.id === id && pending.deliveredAt);
      if (!delivered && pending?.id === id) {
        pending = null; // never reached the board: cancel it, so its state is known (unchanged)
        return res.status(503).json({ ...statusBody(), error: 'the board did not pick up the command', command_id: id });
      }
      // Reached the board, but no report: it may or may not have acted.
      return res.status(504).json({ ...statusBody(), error: 'no report from the board', command_id: id });
    }
  }
});

app.get('/api/v1/control/status', (req, res) => res.json(statusBody()));

app.use((req, res) => res.status(404).json({ error: 'not found' }));
app.use((err, req, res, _next) => {
  const status = err.status || err.statusCode || 500;
  res.status(status).json({ error: status === 500 ? 'internal error' : err.type || err.message });
});

const server = app.listen(PORT, HOST, () => {
  console.log(`NEXUS relay listening on http://${HOST}:${server.address().port}`);
});