# -*- coding: utf-8 -*-
"""
main.py - ChemSmart เว็บแอปพลิเคชัน (FastAPI)

รันด้วยคำสั่ง:
    uvicorn main:app --reload

โครงหน้าที่:
  - หน้าเว็บหลัก (พนักงาน): อัปโหลด SDS -> ดึงข้อมูล -> แปลไทย -> แก้ไข -> แนบรูป -> สร้าง/ดาวน์โหลด PDF
  - หน้า Admin: login ด้วยรหัสผู้ดูแล -> ดู/แก้/ลบทะเบียนสารเคมี

หมายเหตุ: เคยลองสลับไปสร้างเป็น .xlsx (เติมลง Template.xlsx จริงของบริษัทตรงๆ) แต่ผู้ใช้ตัดสินใจ
กลับมาใช้ PDF เหมือนเดิม (ได้ไฟล์ที่ดาวน์โหลด+ปริ้นได้ทันที ไม่ต้องพึ่ง Microsoft Excel) โค้ดฝั่ง Excel
(fill_excel.py + assets/Template.xlsx) ยังเก็บไว้เผื่ออยากกลับไปใช้อีก แต่ตอนนี้ไม่ได้เรียกจากหน้าเว็บแล้ว
"""
import os
import re
import shutil
import uuid
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
load_dotenv()  # อ่านค่าจากไฟล์ .env (เช่น ADMIN_PASSCODE, SECRET_KEY) ก่อนอย่างอื่นทั้งหมด

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from core import database
from core import auth
from core.parser import parse_sds
from core.translator import translate_fields
from pdf_gen.fill_template import fill_from_data
from pdf_gen.label_template import fill_label
from core.fields import (FIELD_GROUPS, SIGNATURE_FIELDS, PICTOGRAM_FIELDS, PPE_FIELDS,
                     NFPA_SPECIAL_OPTIONS, TRANSLATABLE_KEYS, ALL_KEYS,
                     LABEL_SIZE_PRESETS, LABEL_TRANSLATABLE_KEYS,
                     APPROVED_NAME, APPROVED_POSITION)

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PDF = os.path.join(HERE, "assets", "Template.pdf")
TEMPLATE_BACKUP_DIR = os.path.join(HERE, "assets", "template_backups")
UPLOAD_DIR = os.path.join(HERE, "data", "uploads")
GENERATED_DIR = os.path.join(HERE, "data", "generated")
SDS_TMP_DIR = os.path.join(HERE, "data", "sds_uploads")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(GENERATED_DIR, exist_ok=True)
os.makedirs(SDS_TMP_DIR, exist_ok=True)
os.makedirs(TEMPLATE_BACKUP_DIR, exist_ok=True)

database.init_db()

app = FastAPI(title="ChemSmart")

# session ใช้เก็บสถานะ "login เป็น admin แล้ว" ไว้ใน cookie (เข้ารหัสด้วย SECRET_KEY)
SECRET_KEY = os.environ.get("SECRET_KEY", "chemsafe-dev-secret-change-me")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))


def _safe_filename(original_name, prefix):
    """สร้างชื่อไฟล์ที่ไม่ซ้ำกัน (กัน path traversal / ชื่อไฟล์ชนกัน) - ใช้เป็นชื่อไฟล์จริงบนดิสก์เท่านั้น
    ชื่อที่ผู้ใช้เห็นตอนดาวน์โหลดมาจาก _pretty_download_name() แยกต่างหาก (ดูด้านล่าง)"""
    ext = os.path.splitext(original_name or "")[1].lower()
    return f"{prefix}_{uuid.uuid4().hex}{ext}"


def _pretty_download_name(prefix, chemical_name):
    """ชื่อไฟล์ที่ผู้ใช้เห็นตอนดาวน์โหลด รูปแบบ "Label_ชื่อสารเคมี_ddmmyyyy.pdf" (หรือ "SDS_...")
    ตามที่ผู้ใช้ขอ ใช้วันที่ปัจจุบันตามเวลาไทย (Asia/Bangkok) ไม่ใช่เวลาเซิร์ฟเวอร์ (Render ใช้ UTC
    ซึ่งอาจเพี้ยนไป 1 วันช่วงใกล้เที่ยงคืนถ้าไม่ระบุ timezone ตรงๆ)"""
    name = (chemical_name or "").strip() or "Unknown"
    name = re.sub(r'[\\/:*?"<>|]', "", name)  # ตัดอักขระที่ใช้เป็นชื่อไฟล์ไม่ได้ (Windows/Unix)
    date_str = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%d%m%Y")
    return f"{prefix}_{name}_{date_str}.pdf"


# ---------------------------------------------------------------------------
# หน้าเว็บหลัก (พนักงาน)
# ---------------------------------------------------------------------------

@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html",
        {"field_groups": FIELD_GROUPS, "signature_fields": SIGNATURE_FIELDS,
         "pictogram_fields": PICTOGRAM_FIELDS, "ppe_fields": PPE_FIELDS,
         "nfpa_special_options": NFPA_SPECIAL_OPTIONS}
    )


@app.post("/api/parse")
async def api_parse(sds_file: UploadFile = File(...)):
    """รับไฟล์ SDS (PDF) แล้วดึงข้อมูลออกมา คืนเป็น JSON ให้ผู้ใช้ตรวจ/แก้"""
    if not sds_file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="กรุณาอัปโหลดไฟล์ PDF เท่านั้น")

    tmp_name = _safe_filename(sds_file.filename, "sds")
    tmp_path = os.path.join(SDS_TMP_DIR, tmp_name)
    with open(tmp_path, "wb") as f:
        shutil.copyfileobj(sds_file.file, f)

    try:
        data = parse_sds(tmp_path)
    except Exception as ex:
        raise HTTPException(status_code=422, detail=f"อ่านไฟล์ SDS ไม่ได้: {ex}")
    finally:
        # ไม่เก็บไฟล์ SDS ต้นฉบับไว้ถาวร (ใช้แค่ดึงข้อมูลตอนนี้)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return JSONResponse({"data": data})


@app.post("/api/translate")
async def api_translate(payload: dict):
    """รับ dict ข้อมูล (อังกฤษ) แปลเฉพาะฟิลด์ที่ควรแปล คืนข้อมูลชุดใหม่
    ส่ง "keys" มาเองได้ (เช่นหน้าฉลากใช้ LABEL_TRANSLATABLE_KEYS คนละชุดกับฟอร์ม SDS ติดหน้างาน)
    ถ้าไม่ส่งมา ใช้ TRANSLATABLE_KEYS เดิม (ฟอร์ม SDS ติดหน้างาน)

    translate_text() ดักข้อผิดพลาดของแต่ละคำแปลไว้แล้ว (คืนค่าเดิมถ้าแปลไม่ได้) แต่กันเผื่อไว้อีกชั้น
    ตรงนี้ด้วย เพราะ deep-translator เรียก Google Translate แบบไม่เป็นทางการ (web scraping ไม่ใช่ API
    จริง) บาง hosting (เช่น Render) อาจโดนบล็อก/limit เป็นพักๆ ถ้าเกิด exception ที่ไม่คาดคิดขึ้นระดับนี้
    (นอกเหนือจาก translate_text) ต้องคืนข้อมูลเดิมของผู้ใช้กลับไปเสมอ ห้ามให้ข้อมูลหายไปเด็ดขาด
    """
    data = payload.get("data", {})
    keys = payload.get("keys") or TRANSLATABLE_KEYS
    # direction: "en_to_th" (ค่าเริ่มต้น, ปุ่ม "แปลเป็นไทย") หรือ "th_to_en" (ปุ่ม "แปลเป็นอังกฤษ")
    direction = payload.get("direction", "en_to_th")
    source, target = ("th", "en") if direction == "th_to_en" else ("en", "th")
    try:
        translated = translate_fields(data, keys, source=source, target=target)
    except Exception:
        translated = data
    return JSONResponse({"data": translated})


@app.post("/api/generate")
async def api_generate(
    # รับ "data" เป็นไฟล์ (Blob) แทนที่จะเป็น Form(str) ธรรมดา
    # เพราะ python-multipart จะ decode ฟิลด์ข้อความล้วนด้วย latin-1 (ทำให้ภาษาไทยเพี้ยน)
    # แต่ไฟล์ (UploadFile) เราอ่าน bytes เองแล้ว decode utf-8 ตรงๆ ได้ถูกต้องเสมอ
    data: UploadFile = File(...),  # JSON (utf-8) ของข้อมูลทุกฟิลด์ที่แก้ไขแล้วจากผู้ใช้
    label_image: UploadFile = File(None),
    container_image: UploadFile = File(None),
):
    """สร้าง PDF จากข้อมูลที่ผู้ใช้ตรวจ/แก้แล้ว + รูปภาพ (ถ้ามี) บันทึกลงทะเบียน แล้วคืนลิงก์ดาวน์โหลด"""
    import json
    raw = await data.read()
    try:
        field_data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="ข้อมูลฟอร์มไม่ถูกต้อง")

    # บังคับกรอกชื่อผู้จัดทำ + ตำแหน่ง ก่อนสร้าง SDS หน้างานได้เสมอ (ผู้ใช้ขอไว้ตรงๆ) เช็คซ้ำฝั่ง server
    # ด้วย ไม่ใช่พึ่งแค่ JS ฝั่งหน้าเว็บ กันเลี่ยงเช็คด้วยการยิง API ตรงๆ - ข้อความ error ให้ตรงกับที่
    # frontend แจ้งเตือน (โปรดกรอกข้อมูลให้ครบ)
    # รูปฉลากสารเคมี/รูปภาชนะบรรจุ เดิมบังคับด้วย แต่ผู้ใช้ขอเปลี่ยนเป็น "แค่เตือน" ฝั่ง frontend แทน
    # (ยืนยันจะไม่แนบก็สร้างได้) จึงไม่บล็อกฝั่ง server ตรงนี้อีกต่อไปเช่นกัน ให้สอดคล้องกัน
    missing = []
    if not (field_data.get("prepared_name") or "").strip():
        missing.append("ชื่อผู้จัดทำ")
    if not (field_data.get("prepared_position") or "").strip():
        missing.append("ตำแหน่งผู้จัดทำ")
    if missing:
        raise HTTPException(status_code=400, detail="โปรดกรอกข้อมูลให้ครบ: " + ", ".join(missing))

    # ให้ทุกฟิลด์ที่ระบบรู้จักมีค่าเสมอ (กันแอปพังถ้าฝั่ง frontend ส่งมาไม่ครบ)
    for key in ALL_KEYS:
        field_data.setdefault(key, "-")

    # "อนุมัติโดย" เป็นคนเดิมทุกครั้ง (ตัดออกจากฟอร์มแล้ว) บังคับค่านี้เสมอไม่ว่า frontend จะส่งอะไรมา
    field_data["approved_name"] = APPROVED_NAME
    field_data["approved_position"] = APPROVED_POSITION

    label_image_filename = None
    container_image_filename = None

    if label_image is not None and label_image.filename:
        label_image_filename = _safe_filename(label_image.filename, "label")
        with open(os.path.join(UPLOAD_DIR, label_image_filename), "wb") as f:
            shutil.copyfileobj(label_image.file, f)

    if container_image is not None and container_image.filename:
        container_image_filename = _safe_filename(container_image.filename, "container")
        with open(os.path.join(UPLOAD_DIR, container_image_filename), "wb") as f:
            shutil.copyfileobj(container_image.file, f)

    out_filename = _safe_filename("sds_form.pdf", "sds_form")
    out_path = os.path.join(GENERATED_DIR, out_filename)

    label_path = os.path.join(UPLOAD_DIR, label_image_filename) if label_image_filename else None
    container_path = os.path.join(UPLOAD_DIR, container_image_filename) if container_image_filename else None

    try:
        fill_from_data(field_data, TEMPLATE_PDF, out_path, label_path, container_path)
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"สร้าง PDF ไม่ได้: {ex}")

    database.add_record(field_data, out_filename, label_image_filename, container_image_filename)

    chemical_name = field_data.get("display_name") or field_data.get("trade_name")
    pretty_name = _pretty_download_name("SDS", chemical_name)
    return JSONResponse({"download_url": f"/download/{out_filename}?name={quote(pretty_name)}"})


@app.get("/download/{filename}")
def download(filename: str, name: str = None):
    """name (query param, optional) = ชื่อไฟล์ที่อยากให้ผู้ใช้เห็นตอนดาวน์โหลด (ดู
    _pretty_download_name) ต่างจาก filename (path) ซึ่งเป็นชื่อไฟล์สุ่มจริงบนดิสก์เสมอ"""
    path = os.path.join(GENERATED_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์")
    return FileResponse(path, media_type="application/pdf", filename=name or filename)


# ---------------------------------------------------------------------------
# หน้าฉลากภาชนะบรรจุสารเคมี (แยกจากฟอร์ม SDS ติดหน้างาน ใช้ /api/parse ตัวเดียวกันดึงข้อมูล)
# ---------------------------------------------------------------------------

@app.get("/label")
def label_page(request: Request):
    return templates.TemplateResponse(
        request, "label.html",
        {"pictogram_fields": PICTOGRAM_FIELDS, "label_size_presets": LABEL_SIZE_PRESETS,
         "label_translatable_keys": LABEL_TRANSLATABLE_KEYS}
    )


@app.post("/api/label/generate")
async def api_label_generate(data: UploadFile = File(...)):
    """สร้างฉลากภาชนะบรรจุ (PDF ขนาดพอดีฉลากที่เลือก) จากข้อมูลที่ผู้ใช้ตรวจ/แก้แล้ว"""
    import json
    raw = await data.read()
    try:
        field_data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="ข้อมูลฟอร์มไม่ถูกต้อง")

    size_key = field_data.get("size_key", LABEL_SIZE_PRESETS[0][0])
    out_filename = _safe_filename("label.pdf", "label")
    out_path = os.path.join(GENERATED_DIR, out_filename)

    try:
        # ตัดตัวเลือกแนวตั้ง/แนวนอนออกแล้ว - ฉลากเป็นแนวนอนเสมอตามเทมเพลตอ้างอิงใหม่
        fill_label(field_data, size_key, LABEL_SIZE_PRESETS, out_path)
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"สร้างฉลากไม่ได้: {ex}")

    database.add_label_record(field_data, size_key, out_filename)

    pretty_name = _pretty_download_name("Label", field_data.get("product_name"))
    return JSONResponse({"download_url": f"/download/{out_filename}?name={quote(pretty_name)}"})


# ---------------------------------------------------------------------------
# หน้า Admin
# ---------------------------------------------------------------------------

@app.get("/admin")
def admin_page(request: Request):
    if not auth.is_admin(request):
        return RedirectResponse("/admin/login")
    records = database.list_records()
    stats = database.get_dashboard_stats()
    return templates.TemplateResponse(
        request, "admin.html", {"records": records, "stats": stats}
    )


@app.get("/admin/login")
def admin_login_page(request: Request):
    return templates.TemplateResponse(request, "admin_login.html", {"error": None})


@app.post("/admin/login")
def admin_login_submit(request: Request, passcode: str = Form(...)):
    if auth.check_passcode(passcode):
        request.session["is_admin"] = True
        return RedirectResponse("/admin", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin_login.html",
        {"error": "รหัสผู้ดูแลไม่ถูกต้อง"},
        status_code=401,
    )


@app.post("/admin/logout")
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


def _require_admin(request: Request):
    if not auth.is_admin(request):
        raise HTTPException(status_code=403, detail="ต้องเข้าสู่ระบบ Admin ก่อน")


@app.get("/admin/api/records")
def admin_list_records(request: Request):
    _require_admin(request)
    return JSONResponse({"records": database.list_records()})


@app.put("/admin/api/records/{record_id}")
async def admin_update_record(request: Request, record_id: int, payload: dict):
    _require_admin(request)
    existing = database.get_record(record_id)
    if not existing:
        raise HTTPException(status_code=404, detail="ไม่พบรายการนี้")
    database.update_record(record_id, payload.get("data", {}))
    return JSONResponse({"ok": True})


@app.delete("/admin/api/records/{record_id}")
def admin_delete_record(request: Request, record_id: int):
    _require_admin(request)
    existing = database.get_record(record_id)
    if not existing:
        raise HTTPException(status_code=404, detail="ไม่พบรายการนี้")
    database.delete_record(record_id)
    return JSONResponse({"ok": True})


@app.post("/admin/api/template")
async def admin_replace_template(request: Request, template_file: UploadFile = File(...)):
    """
    อัปโหลด Template.pdf ใหม่มาแทนที่ไฟล์เดิม (ใช้ได้เฉพาะเทมเพลตที่โครงสร้าง/ตำแหน่งช่องเหมือนเดิม
    เช่น เปลี่ยนแค่ชื่อบริษัท โลโก้ เลขเอกสาร ถ้าตำแหน่งช่องเปลี่ยนไป ต้องแก้พิกัดใน fill_template.py ด้วย)
    เก็บไฟล์เดิมสำรองไว้ก่อนเสมอ กันเผลอเปลี่ยนแล้วเทมเพลตพัง
    """
    import datetime
    _require_admin(request)
    if not template_file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="กรุณาอัปโหลดไฟล์ PDF เท่านั้น")

    if os.path.exists(TEMPLATE_PDF):
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(TEMPLATE_BACKUP_DIR, f"Template_{stamp}.pdf")
        shutil.copyfile(TEMPLATE_PDF, backup_path)

    with open(TEMPLATE_PDF, "wb") as f:
        shutil.copyfileobj(template_file.file, f)

    return JSONResponse({"ok": True})
