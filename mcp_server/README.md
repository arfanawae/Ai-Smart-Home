# NEXUS MCP Server — M4

ตัวกลางเดียวที่แตะอุปกรณ์ (Blueprint §12.1 / §12.3) — Backend เรียกผ่าน MCP แบบ **stdio subprocess**
จึงไม่มีพอร์ตเครือข่ายสำหรับสั่งอุปกรณ์เปิดอยู่เลย

```
Assistant ──▶ McpDevices (MCP Client) ── stdio ──▶ python -m nexus_mcp ──▶ DeviceService ──▶ MockDriver
              ตรวจ input ชั้นที่ 1                    ตรวจ input ชั้นที่ 2                   (M5: UnoQDriver)
```

| Tool | ใช้ทำอะไร |
|---|---|
| `get_device_status` | อ่านสถานะที่อุปกรณ์รายงาน |
| `turn_light_on` / `turn_light_off` | เปิด / ปิด |
| `set_brightness` | ตั้งความสว่าง 0–100% |
| `adjust_brightness` | ปรับเพิ่ม/ลดจากค่าปัจจุบัน (±50) |
| `nexus_debug_set_mock_failure` | **ทดสอบเท่านั้น** — มีเฉพาะโหมด mock และ Backend ไม่ส่งให้ LLM |

- ทุกผลลัพธ์เป็น `structuredContent` = `CommandResult.to_dict()` (status, simulated, previous, reported, error, latency)
- ระหว่างรออุปกรณ์ยืนยัน Server ส่ง **progress notification** → Backend แสดงสถานะ `WAITING_FOR_DEVICE`
- คำสั่งที่ไม่ผ่านการตรวจ ได้ `is_error: true` + `status: "rejected"` พร้อมเหตุผล (ไม่ถึง driver)

## ใช้งาน

Backend เปิด Server ให้เองเมื่อ `DEVICE_BACKEND=mcp` (ค่าเริ่มต้น) — ไม่ต้องรันแยก

```powershell
cd "D:\Ai Smart Home"
.\.venv\Scripts\python -m pip install -e .\mcp_server --no-deps   # ครั้งแรก
cd mcp_server
..\.venv\Scripts\python -m pytest        # integration tests: subprocess จริง (~35 วินาที)
```

ทดสอบด้วย MCP Inspector หรือ client อื่นได้: คำสั่ง `python -m nexus_mcp` (stdout = โปรโตคอล MCP, log ไปที่ stderr)
