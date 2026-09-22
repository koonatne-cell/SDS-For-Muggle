# ChemSmart — สเปกสำหรับสร้างใน Base44

เอกสารนี้สรุป data model, หน้าจอ, และ "know-how" ด้านความปลอดภัยที่เรียนรู้มาจากการพัฒนา
เวอร์ชัน Python/FastAPI เดิม (ดู `core/parser.py`, `core/fields.py`) เพื่อเอาไปสั่ง Base44 AI
builder ให้สร้างแอปตัวใหม่ได้ตรงเป้าหมาย ไม่ต้องเดา/ลองผิดลองถูกซ้ำ

อ้างอิง UI mockup 5 หน้าจอที่ออกแบบไว้แล้ว (Dashboard, Upload SDS, Review Extracted Information,
Generate Output & Preview, Chemical Detail Page) — ให้แนบภาพ mockup นี้ไปพร้อม prompt ด้วย

> **หมายเหตุเรื่อง branding:** mockup ใช้ชื่อ/โลโก้ "MARS SNACKING" — ถ้านี่คือของบริษัทที่คุณทำงาน
> อยู่จริง ใช้ต่อได้ตามปกติ (เป็นเครื่องมือใช้ภายในองค์กร) แต่ถ้าในอนาคตจะแจกโค้ด/แม่แบบนี้ให้บริษัทอื่น
> เอาไปใช้ต่อ (ตามที่คุยกันไว้ว่าจะแจกฟรีแบบ open-source) **ต้องทำให้ชื่อบริษัท/โลโก้เป็นค่าที่แก้ไขได้
> (settings) ไม่ hardcode ไว้** เพื่อไม่ให้เอาโลโก้/ชื่อบริษัทจริงของคุณไปติดในแม่แบบสาธารณะ

---

## 1. ภาพรวมแอป

**ชื่อแอป:** ChemSmart — ระบบจัดการข้อมูลสารเคมีและสร้างเอกสาร SDS ฉบับย่อ/ฉลากสารเคมีสำหรับหน้างาน

**ผู้ใช้งาน:** เจ้าหน้าที่ความปลอดภัย (EHS) ในโรงงานผลิตอาหาร อัปโหลด SDS ฉบับเต็ม (PDF) →
ระบบดึงข้อมูลอัตโนมัติด้วย AI → ผู้ใช้ตรวจสอบ/แก้ไข → สร้างเอกสาร 2 แบบ: SDS ฉบับย่อ (ติดหน้างาน)
และฉลากภาชนะบรรจุสารเคมี (GHS Label)

**หลักการสำคัญที่สุด:** ข้อมูลที่ AI ดึงมาจาก SDS **ต้องให้ผู้ใช้ตรวจสอบก่อนเสมอ** ก่อนสร้าง
เอกสารจริง (ห้ามส่งตรงจาก AI extraction ไปพิมพ์เลย) เพราะเป็นเอกสารด้านความปลอดภัยที่มีผลจริง
กับพนักงานหน้างาน — Step "Review Extracted Information" จึงบังคับผ่านเสมอ ไม่ข้ามได้

---

## 2. Data Model

### Entity: `Chemical` (1 รายการต่อสารเคมี 1 ชนิด)

| Field key | ป้ายไทย | ประเภท | หมายเหตุ |
|---|---|---|---|
| `display_name` | ชื่อสารเคมี | text | ชื่อหลักที่แสดงทุกที่ (การ์ด/หัวข้อ) |
| `trade_name` | ชื่อทางการค้า | text | **ห้ามแปลภาษา** (เป็น proper noun แปลแล้วเพี้ยน) |
| `signal_word` | Signal Word | enum | ต้องเป็นค่ามาตรฐาน GHS เท่านั้น: `Danger`/`อันตราย` หรือ `Warning`/`คำเตือน` — ถ้า SDS เขียนข้อความอื่นต่อท้าย (เช่น "Danger! Flammable") ให้แยกเก็บส่วนเกินเป็นข้อความเสริม ไม่ใช่ทับคำมาตรฐาน |
| `formula` | สูตรทางเคมี | text | |
| `un` | UN No | text | |
| `cas` | CAS No | text | ถ้า SDS มีตารางส่วนผสมหลายสาร (Section 3) ให้เลือกเลข CAS ของสารที่ **ความเข้มข้น % สูงสุด** เป็นค่าเริ่มต้น |
| `ingredient_name` | ชื่อสารเคมี (คู่กับ CAS) | text | ต้องเป็นแถวเดียวกับ `cas` เสมอ (สารตัวเดียวกัน) |
| `usage` | การใช้งาน | text | |
| `state` / `color` / `odor` | สถานะ/สี/กลิ่น | text | |
| `boiling` / `ph` / `flash` | จุดเดือด/pH/จุดวาบไฟ | text | |
| `reactivity` | การเกิดปฏิกิริยา | text | |
| `fire` | การดับเพลิง | text | |
| `hz_eye` / `hz_skin` / `hz_oral` / `hz_inhale` | อันตรายต่อสุขภาพ 4 ทาง | text | **ห้าม**เก็บตัวเลข LD50/LC50 ดิบ (เช่น "LD50 > 2000 mg/kg") — ผู้ใช้อยากรู้ "อันตรายแบบไหน" ไม่ใช่ตัวเลขพิษวิทยาดิบ ถ้า SDS มีแต่ตัวเลขไม่มีคำอธิบาย ให้เว้นว่างแทน |
| `fa_eye` / `fa_skin` / `fa_oral` / `fa_inhale` | การปฐมพยาบาล 4 ทาง | text | |
| `spill` / `disposal` / `storage` | หกรั่วไหล/การกำจัด/การเก็บรักษา | text | |
| `nfpa_health` / `nfpa_fire` / `nfpa_react` | ดัชนี NFPA (0-4) | number | |
| `nfpa_special` | Special Hazard (NFPA) | enum | ตัวเลือก: ไม่มี / OXY / ACID / ALK / COR / W — **ห้ามเดาอัตโนมัติจากสัญลักษณ์ GHS** (SDS ไม่ได้ระบุตรง ๆ) ปล่อยว่างเสมอ ให้ผู้ใช้เลือกเอง |
| `pictograms` | สัญลักษณ์ GHS | multi-select | ดูกติกาข้อ 4 |
| `ppe` | อุปกรณ์ป้องกัน | multi-select | ดูกติกาข้อ 5 |
| `hazard_statements` | H-Statement | list[text] | รหัส H2xx-H4xx + ข้อความ |
| `precautionary_statements` | P-Statement | list[text] | รหัส P1xx-P5xx + ข้อความ |
| `hazardous_substances` | รายชื่อสารอันตราย (สำหรับฉลาก) | list[text] | จากตาราง Section 3 เรียงจาก % สูงสุด |
| `supplier_name` / `supplier_address` / `emergency_phone` | ข้อมูลผู้ผลิต | text | |
| `prepared_name` / `prepared_position` | ผู้จัดทำ | text | กรอกเองทุกครั้ง (บังคับกรอกก่อนสร้างเอกสาร) |
| `approved_name` / `approved_position` | ผู้อนุมัติ | text | ค่าคงที่ตายตัว (ตั้งค่าได้ใน Settings ไม่ใช่กรอกทุกครั้ง) |
| `status` | สถานะ | enum | `extracted` (AI ดึงแล้ว) / `needs_review` (field สำคัญยังว่าง) / `generated` (สร้างเอกสารแล้ว) |
| `created_by` / `created_at` | ผู้สร้าง/วันที่ | metadata | |

**ตัวเลือก GHS pictogram (9 ชนิด):** explosive, flammable, oxidizer, gas_cylinder, corrosive, toxic,
irritant, health_hazard, environment

**ตัวเลือก PPE (8 ชนิด):** safety_glasses, safety_goggle, mask, respirator, glove, safety_shoe,
face_shield, coverall

---

## 3. Logic การดึงข้อมูลด้วย AI (สั่ง Base44 AI ตรง ๆ)

เขียน prompt สำหรับ AI extraction step ให้ครอบคลุมกติกาต่อไปนี้ (เรียนรู้จากการทดสอบกับ SDS จริง
หลายสิบฉบับ — ถ้าไม่ใส่กติกาพวกนี้ AI จะพลาดแบบเดิมที่เราเคยเจอ):

1. **จำกัดขอบเขตการตรวจ GHS pictogram ไว้แค่ Section 2 (Hazard Identification) เท่านั้น**
   ห้ามค้นทั้งเอกสาร — คำอย่าง "corrosive"/"toxic"/"oxidizer" มักโผล่ใน Section 9-11
   (คุณสมบัติ/พิษวิทยา/ความเข้ากันไม่ได้) แม้สารนั้นจะไม่อันตรายเองก็ตาม ถ้า Section 2 เขียนชัดว่า
   "not considered hazardous" / "ไม่จัดเป็นสารอันตราย" ให้ `pictograms = []` เสมอ
2. **PPE ต้องเช็คคำปฏิเสธด้วย** — ถ้าเจอคำใน Section 8 ที่บอกว่า "not required"/"ไม่จำเป็น" ตามหลัง
   ชื่ออุปกรณ์ ห้ามนับว่าต้องใช้อุปกรณ์นั้น
3. **สารหลักของผลิตภัณฑ์ = แถวที่ความเข้มข้น % สูงสุดในตาราง Section 3** ใช้เลือกทั้ง `cas` และ
   `ingredient_name` ให้เป็นแถวเดียวกันเสมอ
4. **Signal Word ต้องตรึงเป็นคำมาตรฐาน GHS** (Danger/Warning หรือ อันตราย/คำเตือน) เท่านั้น
5. **ค่าที่แปลว่า "ไม่มีข้อมูล"** (เช่น "No data available", "Not applicable", "ไม่มีข้อมูล",
   "ไม่ระบุ") **ให้เว้นว่างไว้** ไม่ใช่ copy ข้อความนั้นมาใส่ในฟอร์มตรง ๆ
6. **`trade_name` ห้ามแปลภาษา** เป็นชื่อทางการค้า/แบรนด์ (proper noun) แปลแล้วความหมายเพี้ยน
7. ทุก field ที่ดึงได้ ให้ส่งกลับพร้อม **"ความมั่นใจ" หรือ flag ว่าดึงจาก section ไหน** เพื่อให้หน้า
   Review เอาไปโชว์สถานะ (Extracted / Need Review) ตาม mockup ได้ — field ที่ AI ไม่มั่นใจ/หาไม่เจอ
   ควรถูก flag เป็น "Need Review" อัตโนมัติ ไม่ใช่ปล่อยว่างเงียบ ๆ

---

## 4. หน้าจอ (อ้างอิง mockup 5 หน้า)

### 4.1 Dashboard
- การ์ดสรุป: Total Chemicals, Pending Review, Generated, Expiring Soon
- ปุ่ม "Upload SDS" (เข้า flow หลัก) + "View All Chemicals"
- ตาราง Recent Activities (ชื่อสาร, สถานะ, วันที่, ผู้ทำรายการ)

### 4.2 Upload SDS (ขั้นตอน 1/5)
- Drag & drop / Browse file — รับเฉพาะ PDF, จำกัดขนาดไฟล์ (mockup ระบุ 20 MB)
- คำแนะนำ: "อัปโหลด SDS ฉบับเต็มครบ 16 หัวข้อ เพื่อให้ระบบดึง/แปลข้อมูลได้ครบถ้วน"

### 4.3 Review Extracted Information (ขั้นตอน 3/5)
- ตาราง field/ค่าที่ดึงได้/สถานะ (Extracted สีเขียว / Need Review สีเหลือง) + ปุ่มแก้ไขรายฟิลด์
- ตัวนับสรุปด้านบน (Extracted / Need Review / Missing)
- **ต้องแก้ Need Review ให้ครบก่อนกดปุ่ม "Confirm & Generate" ได้** (บังคับตรวจสอบตามหลักการข้อ 1)

### 4.4 Generate Output & Preview (ขั้นตอน 4/5)
- แสดง preview 2 เอกสารคู่กัน: **One-Page SDS (Mini SDS)** และ **GHS Label**
- ปุ่ม Preview / Download PDF แยกแต่ละเอกสาร + ปุ่มรวม "Print Both Documents"

### 4.5 Chemical Detail Page
- หน้ารายละเอียดสารเคมีแต่ละตัว (ถาวร ไม่ใช่แค่ตอน generate) แบ่ง tab: Overview / SDS Information /
  Documents / History / Notes
- โชว์ Basic Information, Hazard Summary (H-code + pictogram), PPE Recommendation
- ปุ่ม "Regenerate Documents" (สร้างเอกสารใหม่ถ้าแก้ข้อมูลภายหลัง)
- เก็บ **History** ของการแก้ไข/สร้างเอกสารแต่ละครั้ง (audit trail — สำคัญสำหรับงาน EHS)

### เมนูข้าง (Sidebar) เพิ่มเติมจาก mockup
- Chemicals: All Chemicals / Pending Review / Generated
- SDS: Upload SDS / Generated
- Labels: Generated Labels
- Settings: ตั้งชื่อ-โลโก้บริษัท, ผู้อนุมัติเริ่มต้น (`approved_name`/`approved_position`),
  ขนาดฉลากเริ่มต้น

---

## 5. เอกสาร PDF ที่ต้องสร้างได้

1. **One-Page SDS (Mini SDS)** — สรุปทั้ง 16 หัวข้อ (หรือเฉพาะที่จำเป็นหน้างาน) ลงหน้าเดียว
2. **GHS Label** — ฉลากภาชนะบรรจุ ขนาดเลือกได้ (มีตัวเลือกขนาดสำเร็จรูปในระบบเดิม: เล็ก/กลาง/ใหญ่/
   A6/A5/A4/กำหนดเอง)

ทั้งสองเอกสารต้องรองรับ**ฟอนต์ไทย** (ชื่อสาร/ข้อความปฐมพยาบาล/อันตราย มักเป็นภาษาไทย)

---

## 6. สิ่งที่ต้องตัดสินใจเพิ่มก่อนเริ่มสร้างใน Base44

- [ ] ต้องการฟีเจอร์ "แปลภาษา" (อังกฤษ↔ไทย) เหมือนระบบเดิมไหม หรือให้ AI แปลตอน extract เลยทีเดียว
- [ ] Auth/สิทธิ์ผู้ใช้: ทุกคนแก้ไขได้เท่ากัน หรือมีระดับ Admin แยก (เหมือนระบบเดิมที่มีหน้า Admin
      แยกจากพนักงาน)
- [ ] เก็บไฟล์ SDS ต้นฉบับ (PDF ที่อัปโหลด) ไว้ถาวรไหม หรือลบทิ้งหลัง extract เสร็จ (ระบบเดิมลบทิ้ง
      ทันทีเพื่อประหยัดพื้นที่)
