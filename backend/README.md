# NEXUS Backend — M1 ขั้น 1.1

แชทภาษาไทยกับ NEXUS ผ่าน Terminal + จำลองการควบคุมไฟห้องหลัก (ยังไม่สั่งอุปกรณ์จริง)

## ติดตั้ง (ครั้งแรก)

```powershell
cd "D:\Ai Smart Home"
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".\backend[dev]"
copy backend\.env.example backend\.env   # แล้วใส่ GEMINI_API_KEY ในไฟล์ .env
```

## ใช้งาน

```powershell
cd "D:\Ai Smart Home\backend"
..\.venv\Scripts\python -m nexus          # เริ่มแชท
..\.venv\Scripts\python -m nexus models   # ดูชื่อโมเดล Gemini ที่ใช้ได้จริง (ใส่ใน LLM_MODEL)
..\.venv\Scripts\python -m pytest         # รันชุดทดสอบ
```

ถ้ายังไม่ใส่ API key ระบบจะทำงานเฉพาะ Fast path (คำสั่งชัดเจน เช่น "เปิดไฟ", "หรี่ไฟเหลือ 30")

## การทำงาน

```
ข้อความ → fastpath.decide()
           ├─ คำสั่งชัดเจน ──────────────┐
           └─ ไม่ชัด / คุยทั่วไป → Gemini ─┤ (tool call)
                                         ▼
                      DeviceService: validate → MockDriver → ตรวจ reported state
                                         ▼
                      ตอบกลับ (บอกเสมอว่าเป็น "จำลอง")
```

| ไฟล์ | หน้าที่ |
|---|---|
| `nexus/fastpath.py` | Hybrid fast path — ทำเฉพาะคำสั่งที่ไม่กำกวม; มีคำปฏิเสธ/คำถาม/เงื่อนไข/เวลา → ส่งให้ Gemini |
| `nexus/tools.py` | สัญญา Tool 5 ตัว (จะใช้ซ้ำใน MCP Server ขั้นถัดไป) |
| `nexus/devices/service.py` | ตรวจ input, rate limit, timeout + retry, ยืนยันผลจาก reported state |
| `nexus/devices/drivers/mock.py` | ไฟจำลอง + จำลองความผิดพลาด (`/mock offline\|timeout\|mismatch\|error`) |
| `nexus/llm/` | `LLMProvider` interface + Gemini adapter (เปลี่ยนผู้ให้บริการได้) |
| `nexus/assistant.py` | ประสานทุกส่วน + กันไม่ให้ LLM อ้างว่าสำเร็จเกินจริง |
| `nexus/quota.py` | นับ request รายวัน (รีเซ็ตเที่ยงคืนเวลาแปซิฟิก) |
