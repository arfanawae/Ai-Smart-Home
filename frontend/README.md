# NEXUS Web UI — M2

แดชบอร์ด AI Core (React + Vite + TypeScript + Tailwind) ที่แสดงสถานะจริงจาก NEXUS ผ่าน WebSocket `ws://127.0.0.1:8765`

## รัน

```powershell
# 1) เปิด NEXUS (โฟลเดอร์ voice) — เลือกอย่างใดอย่างหนึ่ง
cd "D:\Ai Smart Home\voice"
..\.venv\Scripts\python -m nexus_voice          # เสียงจริง + ฟีดหน้าเว็บ
..\.venv\Scripts\python -m nexus_voice serve    # ไม่ใช้ไมค์/Azure: พิมพ์คุยและกดควบคุมไฟจากเว็บ

# 2) เปิดหน้าเว็บ
cd "D:\Ai Smart Home\frontend"
npm install        # ครั้งแรก
npm run dev        # http://127.0.0.1:5173
```

| URL | ใช้ทำอะไร |
|---|---|
| `/` | ใช้งานจริง — ถ้ายังไม่ได้เปิด NEXUS จะแสดงแถบ "ไม่ได้เชื่อมต่อ" และลองเชื่อมใหม่อัตโนมัติ |
| `/?demo` | โหมดสาธิต — วนทุกสถานะของ AI Core โดยไม่ต้องมี backend (มีป้าย DEMO, ปุ่มควบคุมปิด) |
| `/?demo&state=THINKING` | ค้างไว้ที่สถานะเดียว (IDLE, LISTENING, SPEAKING, THINKING, EXECUTING, WAITING_FOR_DEVICE, SUCCESS, ERROR, …) |

## คำสั่ง

```powershell
npm run build   # type-check (strict) + build
npm test        # Vitest (store / reducer)
npm run lint    # oxlint
```

## โครงสร้าง

| ไฟล์ | หน้าที่ |
|---|---|
| `src/lib/protocol.ts` | รูปแบบข้อความ WebSocket (ตรงกับ `voice/nexus_voice/broadcast.py`) |
| `src/lib/feed.ts` | เชื่อมต่อ + reconnect (1→30 วินาที) + ส่ง `text` / `command` |
| `src/store/nexus.ts` | Zustand store + `reduce()` (pure, มีเทสต์) |
| `src/lib/levels.ts` | ระดับเสียงไมค์/เสียง NEXUS (~16 ครั้ง/วินาที) อ่านตรงจาก canvas ไม่ผ่าน React |
| `src/components/core/AICore.tsx` | วงแหวน SVG + Energy core (canvas) — rAF loop เดียว, หยุดเมื่อซ่อนแท็บ, รองรับ reduced-motion |
| `src/lib/stateVisuals.ts` | สี/รูปทรง/ข้อความของแต่ละสถานะ (Blueprint §7.1) |
| `src/components/panels/` | LightCard (ควบคุมด้วยมือ, รอยืนยันจากอุปกรณ์), Conversation, Activity, System Status |
| `src/lib/demo.ts` | โหมดสาธิต |

หลักการ: สถานะที่ "เชื่อมต่อแล้ว" มาจากฟีดจริงเท่านั้น, ไม่แสดง "สำเร็จ" กับการจำลอง (แสดง "จำลองสำเร็จ"),
ปุ่มควบคุมไม่เปลี่ยนสถานะจนกว่าอุปกรณ์รายงานกลับ, คลื่นเสียงแสดงเฉพาะเสียงจริง
