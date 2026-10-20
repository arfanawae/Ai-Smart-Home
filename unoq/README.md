# NEXUS ↔ Arduino UNO Q — Outbound Polling (M5 ขั้น 1: LED บนบอร์ด)

Linux container บน UNO Q บล็อก inbound port → **บอร์ดเป็นฝ่ายเรียกออกมาหา Laptop เสมอ**

```
Laptop                                                               Arduino UNO Q
NEXUS ─ MCP Server ─ UnoQDriver ── POST /api/v1/control/led ──▶ ┌────────────┐
        (DEVICE_MODE=unoq)       ◀─ GET  /api/v1/control/status ─│ relay      │◀─ GET  /api/v1/mcu/poll?wait=1000 (long poll) ─ python/main.py ─ Bridge.call("set_led") ─▶ sketch.ino
                                                                 │ server.js  │◀─ POST /api/v1/mcu/report ─────────────               ◀─ "on,brightness"
                                                                 └ :8770 ─────┘
```

| ไฟล์ | ทำอะไร |
|---|---|
| `relay/server.js` | Node.js/Express บน Laptop · คิวคำสั่ง LED (เก็บได้ 1 คำสั่ง — คำสั่งใหม่แทนคำสั่งที่บอร์ดยังไม่ได้รับ) |
| `unoq/app/python/main.py` | บนบอร์ด · long poll (relay ค้างคำขอไว้จนมีคำสั่ง) → `Bridge.call("set_led", "<0-100>")` → POST report (standard library เท่านั้น) |
| `unoq/app/sketch/sketch.ino` | บน MCU · `set_led("0")` = ปิด (จำระดับเดิมไว้), `"1"`–`"100"` = เปิดที่ระดับนั้น · `get_led()` |
| `unoq/simulate.py` | บอร์ดจำลองบน Laptop (รัน `main.py` ตัวจริงกับ MCU ปลอม) |

## API (ทุก endpoint ต้องมี `Authorization: Bearer <token>`)

| Endpoint | ใครเรียก | Body / ผลลัพธ์ |
|---|---|---|
| `GET /api/v1/mcu/poll?wait=ms` | บอร์ด | `{"command": {"id","action":"set_led","value":0-100}}` หรือ `{"command": null}` · มี `wait` → ค้างไว้จนมีคำสั่ง (ส่งทันทีที่เข้าคิว, ~20 ms) หรือครบเวลา |
| `POST /api/v1/mcu/report` | บอร์ด | `{"id","ok":true,"on":true,"brightness":60}` หรือ `{"id","ok":false,"error":"..."}` |
| `POST /api/v1/control/led` | NEXUS | `{"on":true,"brightness":60}` หรือ `{"value":0-100}` · ไม่มี `?wait` → `202` เข้าคิว · `?wait=2000` → รอจนบอร์ดรายงาน |
| `GET /api/v1/control/status` | NEXUS | `online` (มี poll ค้างอยู่ หรือ poll ภายใน 2 วิ), `known`, `on`, `brightness`, `pending` |

ผลของ `?wait=`: `200` บอร์ดรายงานแล้ว · `503` บอร์ดไม่ได้ต่อ/ไม่ได้รับคำสั่ง (**ไม่เข้าคิว** — ไฟไม่ติดเองทีหลัง) · `502` MCU error · `504` บอร์ดรับแล้วแต่ไม่รายงาน (สถานะไม่แน่นอน) · `409` ถูกคำสั่งใหม่แทน
คำสั่งที่บอร์ดไม่มารับภายใน 5 วิจะถูกทิ้ง · สถานะทุกค่ามาจาก report ของบอร์ดเท่านั้น

## 1. ติดตั้งบน Laptop (Command Prompt)

ต้องมี Node.js ≥ 20.12 (เครื่องนี้ v24) — `node --version`

```bat
cd /d "D:\Ai Smart Home\relay"
npm install
copy .env.example .env
notepad .env
```
ใส่ `NEXUS_TOKEN=<token>` (≥ 16 ตัวอักษร) แล้วรัน:
```bat
npm start
```
ควรเห็น `NEXUS relay listening on http://0.0.0.0:8770`

**เปิด Firewall ของ Windows ให้บอร์ดเข้ามาได้** (Command Prompt แบบ *Run as administrator*, ครั้งเดียว):
```bat
netsh advfirewall firewall add rule name="NEXUS relay 8770" dir=in action=allow protocol=TCP localport=8770 remoteip=localsubnet
```
`remoteip=localsubnet` = รับเฉพาะเครื่องในวง LAN เดียวกัน · **ห้าม port-forward 8770 ออกอินเทอร์เน็ต**

หา IP ของ Laptop: `ipconfig` → ดู IPv4 ของ Wi-Fi ที่บอร์ดต่ออยู่ (เช่น `172.20.10.3`)

## 2. ติดตั้งบนบอร์ด (Arduino App Lab)

1. ใน App Lab: App ใหม่ →
   - `python/main.py` ← จาก `unoq/app/python/main.py` แล้วแก้บรรทัด `LAPTOP_URL = "http://<IP ของ Laptop>:8770"`
   - `python/token.txt` ← token เดียวกับ `NEXUS_TOKEN` บรรทัดเดียว (**อย่า commit**)
   - Sketch ← `unoq/app/sketch/sketch.ino` + library **Arduino_RouterBridge**
   - ไม่ต้อง `pip install` อะไรบนบอร์ด (ใช้ `urllib` ของ Python)
2. กด Run → Python console ควรเห็น `connected to NEXUS relay at http://...:8770`
   และหน้าต่าง relay ขึ้น `board online` + `report: off 100%`

## 3. ทดสอบด้วย curl (Command Prompt)

```bat
set T=<token>
set R=http://127.0.0.1:8770

:: สถานะ (online ต้องเป็น true เมื่อบอร์ดหรือ simulate.py กำลัง poll)
curl -H "Authorization: Bearer %T%" %R%/api/v1/control/status

:: เปิด 60% แล้วรอผลจากบอร์ด
curl -X POST -H "Authorization: Bearer %T%" -H "Content-Type: application/json" -d "{\"on\":true,\"brightness\":60}" "%R%/api/v1/control/led?wait=2000"

:: ปิด
curl -X POST -H "Authorization: Bearer %T%" -H "Content-Type: application/json" -d "{\"value\":0}" "%R%/api/v1/control/led?wait=2000"

:: เข้าคิวโดยไม่รอ (ได้ 202)
curl -X POST -H "Authorization: Bearer %T%" -H "Content-Type: application/json" -d "{\"value\":100}" %R%/api/v1/control/led

:: ทำตัวเป็นบอร์ดเอง: ดึงคำสั่ง แล้วรายงานผล
curl -H "Authorization: Bearer %T%" %R%/api/v1/mcu/poll
curl -X POST -H "Authorization: Bearer %T%" -H "Content-Type: application/json" -d "{\"ok\":true,\"on\":true,\"brightness\":100}" %R%/api/v1/mcu/report

:: ไม่มี token → 401
curl -i %R%/api/v1/control/status
```

ทดสอบจาก **เครื่องอื่นในวง LAN** (เช็ก firewall): เปลี่ยน `R` เป็น `http://<IP ของ Laptop>:8770`

ไม่มีบอร์ด? เปิดบอร์ดจำลองในอีกหน้าต่าง: `"D:\Ai Smart Home\.venv\Scripts\python" "D:\Ai Smart Home\unoq\simulate.py"`
(อ่าน token จาก `relay\.env` · `--fail error|hang|mismatch` เพื่อดูว่า NEXUS รายงานความผิดพลาดถูกต้อง)

## 4. ต่อกับ NEXUS

`backend/.env`:
```
DEVICE_MODE=unoq
UNOQ_URL=http://127.0.0.1:8770
UNOQ_TOKEN=<token เดียวกับ relay/.env>
```
เปิด relay ก่อน → เปิด NEXUS ตามปกติ → พูด "เปิดไฟ" → LED บนบอร์ดติด และคำตอบไม่มีคำว่า "จำลอง"

## ยังไม่ได้ยืนยันกับบอร์ดจริง

| สิ่งที่ต้องดู | ถ้าไม่ตรง |
|---|---|
| `App.run(user_loop=loop)` — ตามตัวอย่าง App Lab ที่พบ (`@App.loop` ยังหาเอกสารยืนยันไม่ได้) | แก้ 3 บรรทัดท้าย `main.py` |
| `Bridge.call("set_led", "60")` ส่ง String ได้ และคืนค่าเป็นสตริง `"1,60"` | `parse_reading()` ใน `main.py` |
| `provide_safe` รับฟังก์ชัน `String f(String)` | `sketch.ino` |
| `LED_BUILTIN` active-high หรือไม่ / ขานั้นทำ PWM ได้หรือไม่ | กลับ HIGH/LOW ใน sketch · ถ้าไม่มี PWM จะเปิด/ปิดอย่างเดียว |

## เทสต์

```bat
cd /d "D:\Ai Smart Home"
.venv\Scripts\python -m pytest -q unoq\tests backend\tests\test_unoq_driver.py
```
(สตาร์ต relay ตัวจริงบนพอร์ตสุ่ม + `main.py` ตัวจริงกับ MCU จำลอง — ต้อง `npm install` ใน `relay\` ก่อน)

## MQTT (ทดลอง) — สั่งไฟจากหน้าเว็บผ่าน broker

```
หน้าเว็บ ── {"state": true, "value": 60} ──▶ <topic>/led   ──▶ บอร์ด (mqtt_link.py)
หน้าเว็บ ◀── {"online", "state", "value"} ── <topic>/state ◀── บอร์ด (retained + last will เมื่อหลุด)
```

- เปิดใช้เมื่อมี `python/mqtt.json` บนบอร์ด และ `VITE_MQTT_TOPIC` ใน `frontend/.env.local` — **topic ต้องตรงกัน** (สุ่มยาว ห้าม commit ทั้งสองไฟล์)
- broker `broker.emqx.io` เป็น**สาธารณะ ไม่มีรหัสผ่าน** — topic สุ่มคือการป้องกันเดียว ใช้ทดลองกับ LED บนบอร์ดเท่านั้น ห้ามใช้กับไฟบ้าน
- บอร์ดรับเฉพาะ JSON ที่ถูกต้อง (value 1–100 เมื่อเปิด), ไม่เล่นคำสั่งเก่าที่ค้างใน broker (retained), จำกัด 15 คำสั่ง/วินาที
- คำสั่งเสียงยังผ่าน NEXUS → relay ตามเดิม และบอร์ดส่งสถานะใหม่ออก MQTT ด้วย หน้าเว็บจึงเห็นตรงกันเสมอ
- ใช้แค่ standard library ของ Python (MQTT client เล็ก ๆ ใน `mqtt_link.py`) — ไม่ต้อง pip install บนบอร์ด
