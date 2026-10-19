# M5 / H1 — Arduino UNO Q + หลอด MR16 · แผนเตรียมงาน

> สถานะ: **เตรียมการ** — รอข้อมูลฮาร์ดแวร์จากผู้ใช้ (หัวข้อ 1) ยังไม่ได้ต่อวงจรหรือเขียนโค้ดฝั่งบอร์ด
> ข้อมูลบอร์ดในเอกสารนี้มาจากฟอรัม/บทความชุมชน (ลิงก์ท้ายเอกสาร) **ต้องยืนยันกับคู่มือทางการ (UNO Q User Manual) และบอร์ดจริงก่อนต่อวงจร**

## 1. ข้อมูลที่ต้องการจากผู้ใช้

| # | ข้อมูล | ทำไมต้องรู้ |
|---|---|---|
| 1 | **รหัสรุ่นเต็มของหลอด Philips** (บนตัวหลอด/กล่อง) และรูปถ่ายฉลาก | หา Datasheet: หรี่แบบไหน (ตัดเฟส / DC / PWM) ใช้กับหม้อแปลงแบบใด |
| 2 | **แหล่งจ่ายไฟ 12 V**: เป็น Adapter **DC** หรือหม้อแปลงอิเล็กทรอนิกส์ **AC** · กระแสสูงสุด (A) · รูปฉลาก | MOSFET สลับได้เฉพาะ **DC** — ถ้าเป็นหม้อแปลง AC ต้องเปลี่ยนแผน |
| 3 | **MOSFET / โมดูลขับ**: รหัสชิป (เช่น IRLZ44N, AO3400, IRF520) หรือรูปโมดูล | ต้องนำกระแสเต็มที่ที่ Vgs ≈ 3.3 V (ขา MCU ของ UNO Q) |
| 4 | มี **มัลติมิเตอร์**, สายจัมเปอร์, ฟิวส์/กระบอกฟิวส์, เบรดบอร์ด หรือไม่ | วัดแรงดัน/กระแสระหว่างทดสอบ H1 |
| 5 | UNO Q ต่อ **Wi-Fi บ้าน** ได้แล้วหรือยัง · เข้าได้ทาง App Lab / SSH ไหม · IP ของบอร์ด | Laptop ↔ UNO Q คุยกันทาง LAN |
| 6 | ติดตั้ง **Arduino App Lab** บน Laptop แล้วหรือยัง | ใช้ส่งโค้ดทั้งฝั่ง Python (Linux) และ Sketch (MCU) |

## 2. ข้อมูลบอร์ดที่พบ (ต้องยืนยัน)

- สองสมอง: **Qualcomm QRB2210 (Debian Linux)** + **STM32U585 (Arduino/Zephyr)** สื่อสารผ่าน **Arduino Bridge (RPC)**
- Sketch: `#include <Arduino_RouterBridge.h>`, `Bridge.begin()`, ส่งฟังก์ชันให้ฝั่ง Linux เรียกด้วย `Bridge.provide(...)`
  — **ฟังก์ชันที่แตะขา (digitalWrite / analogWrite) ต้องใช้ `Bridge.provide_safe(...)`** ให้รันใน `loop()` ไม่ใช่เธรด RPC
- Python (Linux): `from arduino.app_utils import *` แล้ว `Bridge.call("ชื่อฟังก์ชัน", ...)`; App รันด้วย `App.run(...)`
- GPIO ของ MCU บนหัวต่อแบบ UNO เป็น **3.3 V** (ทน 5 V ยกเว้น A0/A1) · ขาฝั่ง MPU เป็น **1.8 V ห้ามต่อกับ 3.3/5 V ตรง ๆ**
- **ยังไม่รู้ว่าขาไหนรองรับ PWM บน UNO Q** — ชื่อ D0–D13 เหมือน UNO เดิม แต่ชิปข้างในต่างกัน ห้ามเดาว่าเป็น 3/5/6/9/10/11

## 3. H1 — ทดสอบหลอด MR16 (ก่อนต่อเข้าระบบ)

**ความปลอดภัย:** ใช้เฉพาะ 12 V **DC** · มีฟิวส์ขั้วบวก · ต่อกราวด์ร่วมระหว่างแหล่งจ่าย 12 V กับบอร์ด · ห้ามต่อ 12 V เข้าขาบอร์ดเด็ดขาด
· ถ้าเป็นหม้อแปลง AC / 220 V **หยุดและปรึกษาก่อน**

| ขั้น | ทดสอบ | ผ่านเมื่อ |
|---|---|---|
| H1.1 | หลอดกับ 12 V DC ตรง ๆ (ไม่ผ่าน MOSFET) วัดกระแส | ติดสว่างปกติ · กระแส ≈ 5 W / 12 V ≈ 0.4 A |
| H1.2 | ผ่าน MOSFET เปิด/ปิด (duty 0% / 100%) จาก UNO Q | เปิด-ปิดได้ทุกครั้ง · MOSFET ไม่ร้อน |
| H1.3 | PWM duty 10–100% ที่ ~1 kHz | หรี่ได้ราบรื่น ไม่กะพริบ (ดูผ่านกล้องมือถือ) ไม่มีเสียงหวีด |
| H1.4 | ถ้า H1.3 ไม่ผ่าน: ลองความถี่อื่น (~200 Hz / ~20 kHz) | บันทึกผลทุกค่า |
| H1.5 | เปิดค้าง 30 นาทีที่ 50% | MOSFET / หลอดไม่ร้อนผิดปกติ |

**ถ้าหรี่ไม่ได้:** ใช้หลอดเดิมแบบ **เปิด/ปิดอย่างเดียว** ไปก่อน (UI ซ่อน Slider อัตโนมัติจาก `capabilities`) และพิจารณาแผน A ใน Blueprint §12.5
(ไฟ LED 12 V แบบไม่มีวงจรขับในตัว)

## 4. สถาปัตยกรรม M5 (ข้อเสนอ)

```
Laptop                                           Arduino UNO Q (LAN)
MCP Server ── UnoQDriver ── HTTP + token ──▶  Controller (Python, Linux)  ── Bridge.call ──▶  Sketch (MCU)
              (แทน MockDriver)                  GET /state · POST /apply                      PWM + readback + heartbeat
```

- **สัญญา HTTP (ข้อเสนอ):** `GET /state` → `{online, on, brightness}` · `POST /apply {command_id, on, brightness}` → สถานะที่ MCU **อ่านกลับ** จริง
- Token ลับ + รับเฉพาะ IP ของ Laptop · ไม่เปิดสู่อินเทอร์เน็ต (Blueprint §12.7)
- **Readback:** MCU รายงาน duty ที่ตั้งจริง → "สำเร็จ" มาจากบอร์ด ไม่ใช่จากการส่งคำสั่งเฉย ๆ
- **Fail-safe:** ไม่ได้รับ heartbeat → คงสถานะเดิม · ตัวต้านทาน pull-down ที่ Gate ให้ไฟดับขณะบอร์ดบูต
- Backend ไม่ต้องแก้: `UnoQDriver` ใช้ interface เดียวกับ `MockDriver` อยู่ใน MCP Server

## 5. สิ่งที่ทำได้ทันทีโดยยังไม่ต้องมีฮาร์ดแวร์

1. เขียน `UnoQDriver` + Controller จำลอง (รันบน Laptop) ตามสัญญา HTTP ข้างบน พร้อมเทสต์ timeout / ตัดการเชื่อมต่อ / สถานะไม่ตรง
2. เตรียม Sketch + Python ฝั่งบอร์ดเป็นโครง (ยืนยันชื่อ API กับคู่มือทางการก่อนส่งขึ้นบอร์ด)

## แหล่งข้อมูล (ชุมชน — ต้องยืนยัน)

- [Let us learn Arduino UNO Q step-by-step (Arduino Forum)](https://forum.arduino.cc/t/let-us-learn-arduino-uno-q-step-by-step/1426536)
- [Sending data between MPU script and MCU sketch (Arduino Forum)](https://forum.arduino.cc/t/sending-data-back-and-forth-between-mpu-script-and-mcu-sketch-of-uno-q-platform/1430871)
- [Read MCU pin via Bridge in Python (Arduino Forum)](https://forum.arduino.cc/t/uno-q-app-lab-how-do-i-read-mcu-pin-d8-and-print-in-python-console-via-bridge/1433523)
- [Arduino Uno Q column (HackMD)](https://hackmd.io/@OmniXRI-Jack/Arduino_Uno_Q_Colum_02)
- [UNO Q overview (esp32.co.uk)](https://esp32.co.uk/?p=991487)
