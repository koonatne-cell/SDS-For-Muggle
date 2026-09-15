# -*- coding: utf-8 -*-
"""
fill_template.py - วางค่าที่ดึงมา (และแปลไทยแล้ว) ลงบน Template.pdf แล้วเซฟไฟล์ใหม่

ต่อยอดจาก prototype เดิม เพิ่ม:
  1. ฝังฟอนต์ไทย (Tahoma) เพราะ Helvetica ของ reportlab แสดงภาษาไทยไม่ได้
  2. วางรูปภาพ 2 ช่อง (รูปสลากสารเคมี / รูปภาชนะบรรจุสารเคมี)
  3. ตัดบรรทัดแบบภาษาไทย (ไม่ตัดกลางคำ เช่น "นาที" ไม่กลายเป็น "นา" + "ที") ด้วย pythainlp
  4. คำนวณพื้นที่ว่างจริงของแต่ละช่อง (จากช่องถัดไป) แล้วลดขนาดฟอนต์อัตโนมัติถ้าข้อความยาวเกิน
     กันไม่ให้ข้อความล้นไปทับช่องถัดไป
  5. ช่อง "จัดทำโดย" / "อนุมัติโดย" (ชื่อ-สกุล, ตำแหน่ง)
  6. สัญลักษณ์ GHS (pictogram) ที่ผู้ใช้ติ๊กเลือก วางในกล่องสี่เหลี่ยมมุมซ้ายบน

วิธี: สร้างชั้น "overlay" ด้วย reportlab แล้ววางทับเทมเพลตด้วย pypdf
พิกัด reportlab นับจากมุมล่างซ้าย  ->  y = ความสูงหน้า - top(จาก pdfplumber)
"""
import io
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader, PdfWriter
from pythainlp.tokenize import word_tokenize
from reportlab.pdfbase.pdfmetrics import stringWidth

# PROJECT_ROOT ต้องขึ้นไปอีก 1 ชั้นจากไฟล์นี้ เพราะย้ายเข้ามาอยู่ใน pdf_gen/ แล้ว
# แต่โฟลเดอร์ assets/ (ฟอนต์, ไอคอน, เทมเพลต) ยังอยู่ที่รากโปรเจกต์เหมือนเดิม
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_W, PAGE_H = 595.32, 841.92   # ขนาดหน้าเทมเพลต (points)

# เวลาข้อความมีคำแบบ "น้ำ" (พยัญชนะ + วรรณยุกต์ + สระอำ ตามลำดับ Unicode มาตรฐาน) reportlab ไม่มี
# text-shaping engine (ไม่ใช่ HarfBuzz แบบเบราว์เซอร์/Word) จึงวาดวรรณยุกต์กับสระอำทับกันเป็นรูปม้วน
# งออ่านไม่ออก (ทดสอบ render จริงยืนยันแล้ว) ต้องสลับเป็น "พยัญชนะ + สระอำ + วรรณยุกต์" ก่อนวาดเสมอ
# ถึงจะแยกกันชัดเจนไม่ทับกัน - นี่คือลำดับตรงข้ามกับที่ pythainlp.util.reorder_vowels() มองว่า "ถูกต้อง"
# (นั่นคือลำดับที่ถูกต้องสำหรับการวิเคราะห์ข้อความ ไม่ใช่ลำดับที่ฟอนต์นี้วาดออกมาสวยที่สุด) ข้อความที่ดึงจาก
# PDF ต้นฉบับส่วนใหญ่มาในลำดับ "มาตรฐาน" (วรรณยุกต์ก่อนสระอำ) อยู่แล้ว จึงต้องสลับก่อนวาดแทบทุกครั้ง
_THAI_TONEMARKS = "่้๊๋"  # ไม้เอก ไม้โท ไม้ตรี ไม้จัตวา
_TONEMARK_BEFORE_SARA_AM_RE = re.compile(f"([{_THAI_TONEMARKS}])(ำ)")


def _fix_sara_am_order(text):
    """สลับ 'วรรณยุกต์ + สระอำ' -> 'สระอำ + วรรณยุกต์' กันตัวอักษรทับกันตอนวาดลง PDF (ดูคอมเมนต์ด้านบน)"""
    if not text or "ำ" not in text:
        return text
    return _TONEMARK_BEFORE_SARA_AM_RE.sub(r"\2\1", text)

# ---------- ฟอนต์ไทย ----------
# ใช้ Sarabun (ฟรี, สัญญาอนุญาต SIL Open Font License เผยแพร่ในโค้ด public ได้ถูกกฎหมาย - ดู
# assets/fonts/OFL-Sarabun.txt) ผู้ใช้ขอฟอนต์ที่ดูเป็นทางการแบบ Angsana New แต่ฟอนต์นั้นเป็นของ
# Microsoft ห้ามแจกจ่ายต่อ (เหมือน Tahoma) จึงใช้ Sarabun แทน เป็นฟอนต์มาตรฐานเอกสารราชการไทย
# (ตระกูลเดียวกับ TH Sarabun New) หน้าตาเป็นทางการใกล้เคียงกัน และฟรีถูกกฎหมาย 100%
FONT_REGULAR_PATH = os.path.join(HERE, "assets", "fonts", "Sarabun-Regular.ttf")
FONT_BOLD_PATH = os.path.join(HERE, "assets", "fonts", "Sarabun-Bold.ttf")
FONT_REGULAR = "THFont"
FONT_BOLD = "THFont-Bold"

_fonts_registered = False


def register_thai_fonts():
    """ลงทะเบียนฟอนต์ไทยกับ reportlab (ทำครั้งเดียวพอ)"""
    global _fonts_registered
    if _fonts_registered:
        return
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, FONT_REGULAR_PATH))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, FONT_BOLD_PATH))
    _fonts_registered = True


def Y(top):
    """แปลงพิกัด top (จากด้านบน) เป็น y ของ reportlab (จากด้านล่าง)"""
    return PAGE_H - top

# แถบเหลือง (ชื่อสารเคมี ตัวใหญ่) และแถบแดง (Signal Word) บนหัวฟอร์ม
# หาตำแหน่งจริงจาก Template.pdf ด้วย pdfplumber (สี่เหลี่ยมพื้นสีที่ x0=125.5-472.7)
# วางกึ่งกลางแถบ (drawCentredString) เพราะเป็นข้อความเด่นตรงกลาง ไม่ใช่ "ป้าย: ค่า" แบบช่องอื่น
HEADER_BAR_X_CENTER = (125.5 + 472.7) / 2  # 299.1
HEADER_BAR_MAX_WIDTH = 472.7 - 125.5 - 12  # เผื่อขอบซ้ายขวาเล็กน้อย
HEADER = {
    "display_name": {"top_center": (158.8 + 181.7) / 2, "size": 16},
    "signal_word":  {"top_center": (181.6 + 200.2) / 2, "size": 13},
}

# ช่อง "ISSUE DATE:" หัวเอกสาร (x0=391.3, top=71.2 วัดจาก "ISSUE DATE:" label ที่ x1=353.1 กับป้าย
# "NUMBER:" ถัดไปที่ x0=443.4 ใน Template.pdf) ไม่ใช่ค่าที่ผู้ใช้กรอกในฟอร์มเว็บ แต่คำนวณสดทุกครั้งที่
# กด "สร้าง PDF" เป็นวันที่ปัจจุบัน รูปแบบ DD-MM-YYYY (ผู้ใช้กำหนดรูปแบบนี้เอง) ใช้โซนเวลา Asia/Bangkok
# ให้ตรงกับ _pretty_download_name() ใน main.py ที่ใช้โซนเวลาเดียวกันตอนตั้งชื่อไฟล์ดาวน์โหลด
ISSUE_DATE_POS = (391, 71)
ISSUE_DATE_MAX_WIDTH = 443 - 391 - 6

# ตำแหน่งช่องบรรทัดเดียว (x, top)  วางค่าเริ่มหลัง ":" นิดหน่อย
SHORT = {
    "trade_name": (52,  212),  "formula": (352, 211),
    "un":         (441, 211),  "cas":     (519, 211),
    # ช่อง "ชื่อทางการค้า" (colon อยู่ที่ x=238.7) เดิมไม่เคยมีฟิลด์ไหนวางเลย
    "ingredient_name": (244, 211),
    "state":      (124, 226),  "color":   (233, 226),
    "odor":       (285, 226),  "boiling": (345, 226),
    "ph":         (433, 226),  # ต้องปิดขีด "-" ตัวอย่างของเทมเพลตก่อน (ดู SHORT_COVER)
    "flash":      (523, 226),
}
# ช่อง pH ในเทมเพลตมีขีด "-" พิมพ์ไว้เป็นค่าเริ่มต้นอยู่แล้ว (x0=430.3-432.4, top~225.6)
# ถ้ามีค่า pH จริงมากรอก ต้องปิดขีดเดิมด้วยสี่เหลี่ยมสีขาวก่อน ไม่งั้นจะซ้อนทับกัน
SHORT_COVER = {
    "ph": {"x0": 428, "x1": 436, "top": 220, "bottom": 234},
}
# ตำแหน่ง x ที่ป้ายของช่องถัดไป (ในแถวเดียวกัน) เริ่มพิมพ์จริงในเทมเพลต (วัดด้วย pdfplumber)
# ใช้จำกัดความกว้างของค่าที่วาง กันข้อความยาวทับป้ายช่องถัดไป (ป้ายเริ่มก่อนตำแหน่งค่าของช่องนั้นมาก)
# None = ไม่มีป้ายถัดไปในแถวนี้แล้ว (ช่องสุดท้ายของแถว) ใช้ขอบกระดาษแทน
_SHORT_NEXT_LABEL_X = {
    "trade_name": 208.0, "formula": 416.6, "un": 489.2, "cas": None,
    "ingredient_name": 315.1,
    "state": 223.4, "color": 264.1, "odor": 315.1, "boiling": 416.6, "ph": 489.2, "flash": None,
}
# ตำแหน่งช่องข้อความยาว (x, top)  ต้องตัดบรรทัด (wrap)
# วัดตำแหน่งเส้นกรอบจริงในเทมเพลตด้วย pdfplumber (page.lines/page.rects ที่ยาวเต็มความกว้างหน้า) พบว่า:
#   - "การเกิดปฏิกิริยา" มีกรอบจริงสูง 45pt (250.7-295.7) แต่โค้ดเดิมวางข้อความที่ top=269 ซึ่งลึกจากขอบบน
#     ของกรอบไป 18pt ทำให้ข้อความ "ลอย" อยู่กลางกรอบ มีที่ว่างเยอะทั้งบน-ล่าง แทนที่จะชิดขอบบนซ้าย
#   - "ทางตา/ทางปาก/ทางผิวหนัง/ทางการหายใจ" ทั้งฝั่งปฐมพยาบาลและอันตรายต่อสุขภาพ แท้จริงแล้วแชร์กรอบเดียวกัน
#     4 แถวย่อยไม่มีเส้นแบ่งจริงในเทมเพลต (กรอบสุขภาพ 310.7-385.7, กรอบปฐมพยาบาล 385.7-460.7 กรอบละ 75pt)
# ปรับพิกัดทั้งหมดให้ตรงกับกรอบจริง (ชิดขอบบนซ้ายของแต่ละกรอบ + เผื่อระยะขอบเล็กน้อย ~3-4pt)
# ตำแหน่งด้านล่างนี้วัดใหม่จริงด้วย fitz page.search_for() ต่อป้ายแต่ละอัน (y0 ของตัวอักษรป้ายเอง)
# ของเดิมกะประมาณจากขอบกรอบ (box top) ไม่ใช่ตำแหน่งจริงที่ป้ายพิมพ์อยู่ ทำให้ค่าที่วางเยื้องขึ้นไป
# 3-14pt แล้วแต่แถว (ผิดมากสุดที่แถวบนของกรอบ ผิดน้อยสุดที่แถวล่าง) ผู้ใช้เห็นปัญหานี้ตรงที่ข้อความ
# "ทางตา/ทางปาก/ทางผิวหนัง/ทางการหายใจ" (ปฐมพยาบาล+พิษวิทยา แชร์กรอบเดียวกัน 8 แถวย่อย) ไม่ตรงแถว
# กับป้ายของมันเอง (เลื่อนขึ้นไปทับแถวก่อนหน้า) แก้โดยใช้ y0 จริงของป้ายเป็น top ตรงๆ ทุกแถว
BLOCK = {
    "usage":     (100, 237),  "reactivity": (100, 266),
    "fire":      (100, 297),
    "hz_eye":    (100, 328),  "hz_oral": (100, 343),
    "hz_skin":   (100, 358),  "hz_inhale":(100, 373),
    "fa_eye":    (100, 403),  "fa_oral": (100, 418),
    "fa_skin":   (100, 433),  "fa_inhale":(100, 448),
    "spill":     (100, 476),  "disposal":(100, 521),
    "storage":   (100, 574),
}
# ความกว้างจริงที่มีให้ค่าแต่ละช่องวางได้ (pt) วัดจากเส้นแบ่งจริงในเทมเพลต
# มีเส้นแบ่งแนวตั้งที่ x=360.7 กั้นกล่อง PPE/NFPA/legend/รูปภาชนะ ทางขวาไว้ยาวตั้งแต่ top=251.4 ถึง 722.8
# (เกือบทุกแถวยกเว้น usage ที่อยู่เหนือเส้นแบ่งนี้) แถวที่ชนเส้นแบ่งเลยต้องจำกัดความกว้างไม่ให้ทับ
_NARROW_DIVIDER_TOP = 251.4
_NARROW_DIVIDER_X = 360.7
BLOCK_MAX_WIDTH = {
    k: (_NARROW_DIVIDER_X - x - 5 if top >= _NARROW_DIVIDER_TOP else PAGE_W - 20 - x)
    for k, (x, top) in BLOCK.items()
}
# NFPA สี่เหลี่ยมข้าวหลามตัด (x_center, top_center) หาตำแหน่งจริงจาก Template.pdf ด้วย pdfplumber
# (วัดจาก path ของแต่ละสี่เหลี่ยมย่อยในรูปเพชรโดยตรง ไม่ใช่กะประมาณ)
NFPA = {
    "nfpa_fire":   (528.05, 299.1),   # บน (แดง)
    "nfpa_health": (510.1,  316.25),  # ซ้าย (น้ำเงิน)
    "nfpa_react":  (545.85, 316.25),  # ขวา (เหลือง)
}
# ช่องขาว (Special Hazard) ด้านล่างรูปเพชร - พื้นขาวจึงใช้ตัวหนังสือสีดำ (ไม่ใช่ขาวเหมือน 3 ช่องบน)
# และเป็นตัวอักษรย่อ (OXY/ACID/ALK/COR/W) ไม่ใช่ตัวเลขเดี่ยว เลยแยกวาดต่างหากจาก NFPA ด้านบน
NFPA_SPECIAL_X_CENTER, NFPA_SPECIAL_TOP_CENTER = 528.0, 333.4
# ช่อง "จัดทำโดย" / "อนุมัติโดย" (ชื่อ-สกุล, ตำแหน่ง) หาตำแหน่งจริงจาก Template.pdf ด้วย pdfplumber
# วางต่อท้ายป้าย "ชื่อ-สกุล" / "ตำแหน่ง" ที่มีอยู่แล้วในเทมเพลต ถ้าไม่กรอกค่า
# ป้ายเดิมของเทมเพลต (เช่น "(EHS Manager)") จะยังอยู่เหมือนเดิม เพราะเราวาดทับเฉพาะตอนมีค่าเท่านั้น
# ขยับลงมาจากตำแหน่งเดิม (762/777 -> 765/780) เพราะตอนนี้มีชื่อไปโชว์อีกจุดบนบรรทัด "จัดทำโดย :"
# ด้านบนด้วย (ดู SIGNATURE_HEADER) เลยเว้นระยะห่างเพิ่มอีกนิดกันดูอัดแน่นเกินไป
SIGNATURE = {
    "prepared_name":     (95,  765),  # จัดทำโดย - ชื่อ-สกุล
    "prepared_position": (95,  780),  # จัดทำโดย - ตำแหน่ง
    "approved_name":     (443, 765),  # อนุมัติโดย - ชื่อ-สกุล
    "approved_position": (444, 780),  # อนุมัติโดย - ตำแหน่ง
}
# ชื่อผู้จัดทำ/ผู้อนุมัติ อีกจุดหนึ่ง วางต่อท้ายป้าย "จัดทำโดย :" / "อนุมัติโดย :" ตรงๆ (ตัวหนา)
# ซ้ำกับชื่อที่โชว์อยู่แล้วในบรรทัด "ชื่อ-สกุล" ด้านล่าง ตามที่ผู้ใช้อยากให้เห็นชื่อคนเดียวกันทั้งสองจุด
SIGNATURE_HEADER = {
    "prepared_name": (100, 746.5),
    "approved_name": (438, 746.5),
}
SIGNATURE_HEADER_MAX_WIDTH = {
    "prepared_name": 290,
    "approved_name": 145,
}
# ความกว้างจริงที่มีก่อนชนขอบกระดาษ/คอลัมน์ถัดไป (กันชื่อ-ตำแหน่งยาวๆ ทับข้อความอื่น)
SIGNATURE_MAX_WIDTH = {
    "prepared_name": 290, "prepared_position": 290,
    "approved_name": 145, "approved_position": 145,
}
# ฝั่ง "อนุมัติโดย" เทมเพลตมีข้อความตัวอย่างพิมพ์ไว้แล้ว เช่น "(______)" และ "(EHS Manager)"
# ถ้าผู้ใช้กรอกค่าเอง ต้องปิดข้อความเดิมด้วยสี่เหลี่ยมสีขาวก่อน ไม่งั้นจะซ้อนทับกันอ่านไม่ออก
SIGNATURE_COVER = {
    "approved_name":     {"x0": 440, "x1": 546, "top": 758, "bottom": 770},
    "approved_position": {"x0": 440, "x1": 546, "top": 773, "bottom": 786},
}

# กรอบรูปภาพ 2 ช่อง หาตำแหน่งจริงจาก Template.pdf ด้วย pdfplumber
# (เส้นขอบกรอบอยู่ที่ top=610.7 ถึง top=722.0 แบ่งซ้าย/ขวาที่ x=360.7)
IMAGE_BOXES = {
    "label_image":     {"x0": 28,  "x1": 352, "top": 634, "bottom": 720},  # รูปสลากสารเคมี (ซ้าย)
    "container_image": {"x0": 369, "x1": 566, "top": 634, "bottom": 720},  # รูปภาชนะบรรจุสารเคมี (ขวา)
}

# กล่องสี่เหลี่ยมมุมซ้ายบน (เดิมว่างเปล่า) สำหรับวางสัญลักษณ์ GHS (pictogram) ที่ผู้ใช้ติ๊กเลือก
# หาตำแหน่งจริงจาก Template.pdf ด้วย pdfplumber (กรอบเส้นขอบ stroke จริง)
PICTOGRAM_BOX = {"x0": 31.4, "x1": 108.7, "top": 129.8, "bottom": 199.2}
PICTOGRAM_ICON_DIR = os.path.join(HERE, "assets", "ghs_icons")
PICTOGRAM_ORDER = [
    "explosive", "flammable", "oxidizer",
    "gas_cylinder", "corrosive", "toxic",
    "irritant", "health_hazard", "environment",
]

# กล่อง "อุปกรณ์คุ้มครองความปลอดภัยส่วนบุคคล" (เดิมว่างเปล่า อยู่ติดกับกล่อง NFPA ทางขวา) วัดตำแหน่งจริง
# จาก Template.pdf ด้วย fitz: ป้ายหัวข้อ top=252.1, กล่องขวา (ก่อนถึงเพชร NFPA) แบ่งด้วยเส้นขอบที่
# x=360.7 (ดู _NARROW_DIVIDER_X) ทางขวาไปจนถึงจุดเริ่มเพชร NFPA (~x=490) ด้านล่างจรดเส้นแบ่งก่อนตาราง
# legend สี/ตัวเลข NFPA ที่ top=458
# bottom เดิมเคยตั้งไว้ที่ 452 ซึ่งผิด - วัดเส้นขอบกล่องจริงใน Template.pdf ด้วย fitz (get_drawings())
# แล้วพบว่าเส้นขอบซ้ายของกล่องนี้ลากจาก top=251.4 ถึง bottom=386.4 เท่านั้น (452 ล้ำเข้าไปในแถวถัดไป
# "การดับเพลิง" ไปถึง 66pt) เป็นสาเหตุที่ไอคอนดูล้นกรอบซ้ำแล้วซ้ำเล่าแม้จะแก้ปัญหาการจัดวางไปหลายรอบแล้ว
# เพราะกรอบที่ใช้คำนวณเองมันใหญ่เกินกรอบจริงบนกระดาษตั้งแต่แรก ไม่ใช่ปัญหาการจัดวางไอคอน
PPE_BOX = {"x0": 363, "x1": 485, "top": 270, "bottom": 384}
PPE_ICON_DIR = os.path.join(HERE, "assets", "ppe_icons")
PPE_ORDER = [
    "safety_glasses", "safety_goggle", "mask", "respirator",
    "glove", "safety_shoe", "face_shield", "coverall",
]

# ขอบเขตล่างสุดของพื้นที่ข้อมูล SHORT/BLOCK (เส้นขอบกรอบรูปภาพ) ใช้คำนวณพื้นที่ว่างของช่องสุดท้าย
DATA_AREA_BOTTOM = 610

# หาตำแหน่ง "top" ที่ไม่ซ้ำกันทั้งหมดของ SHORT+BLOCK เรียงจากบนลงล่าง
# ใช้คำนวณว่าแต่ละช่องมีพื้นที่ว่างแนวตั้งได้แค่ไหนก่อนจะชนช่องถัดไป (กันข้อความล้น)
_ALL_ROWS = {**SHORT, **BLOCK}
_UNIQUE_TOPS = sorted(set(top for _, top in _ALL_ROWS.values()))

# แผนที่ top -> รายชื่อ key ที่อยู่แถวนั้น (ใช้เช็คว่าแถวถัดไปว่างหรือไม่)
_TOP_TO_KEYS = {}
for _k, (_x, _top) in _ALL_ROWS.items():
    _TOP_TO_KEYS.setdefault(_top, []).append(_k)


def _is_empty(data, key):
    val = (data.get(key) or "").strip()
    return val in ("", "-")


def _available_height(top, data=None):
    """
    คืนพื้นที่ว่างแนวตั้ง (pt) ก่อนจะถึงแถวถัดไป (หรือขอบเขตล่างสุดถ้าเป็นแถวสุดท้าย)

    หลายช่อง (เช่น ทางตา/ทางปาก/ทางผิวหนัง/ทางการหายใจ) อยู่ใน "กล่องรวม" เดียวกันของเทมเพลต
    ไม่มีเส้นแบ่งแถวจริงๆ ถ้าแถวถัดไปไม่มีข้อมูล (ว่างเปล่าหรือ "-") ให้ขยายพื้นที่ยืมมาใช้ได้
    (พบบ่อยมาก เช่น hz_oral/hz_inhale แทบไม่มีข้อมูลจาก SDS เกือบทุกครั้ง)
    """
    idx = _UNIQUE_TOPS.index(top)
    boundary = DATA_AREA_BOTTOM
    while idx + 1 < len(_UNIQUE_TOPS):
        next_top = _UNIQUE_TOPS[idx + 1]
        keys_at_next = _TOP_TO_KEYS.get(next_top, [])
        if data is not None and keys_at_next and all(_is_empty(data, k) for k in keys_at_next):
            idx += 1  # แถวถัดไปว่างทั้งหมด ยืมพื้นที่ต่อไปได้
            continue
        boundary = next_top
        break
    return max(boundary - top - 2, 8)  # กันพื้นที่ขั้นต่ำไว้ 8pt เผื่อบรรทัดเดียว


# ช่องบรรทัดเดียว (SHORT) หลายช่องอยู่ "แถวเดียวกัน" ติดกันตามแนวนอน (เช่น สี/กลิ่น/จุดเดือด)
# คำนวณความกว้างจริงที่มีก่อนจะชนช่องถัดไปทางขวา กันข้อความยาวทับป้ายช่องถัดไป
# สำคัญ: ต้องวัดถึงจุดที่ "ป้าย" ของช่องถัดไปเริ่มพิมพ์ (_SHORT_NEXT_LABEL_X) ไม่ใช่ตำแหน่งค่า
# เพราะป้ายจะเริ่มก่อนตำแหน่งค่าของช่องนั้นเสมอ (เช่น ป้าย "จุดเดือด" อยู่ก่อนตำแหน่งค่าของมันเองเยอะ)
PAGE_RIGHT_MARGIN = 590
_SHORT_MAX_WIDTH = {}
for _k, (_x, _top) in SHORT.items():
    _boundary = _SHORT_NEXT_LABEL_X.get(_k)
    if _boundary is None:
        _boundary = PAGE_RIGHT_MARGIN
    _SHORT_MAX_WIDTH[_k] = max(_boundary - _x - 6, 20)


def _fit_single_line(val, max_width, base_size=7, min_size=5.5, font=None):
    """
    หาขนาดฟอนต์ + ข้อความ 1 บรรทัด ที่พอดีกับความกว้างจริง (max_width) โดยวัดความกว้างจริง
    ด้วย stringWidth (ไม่ใช่การนับตัวอักษรเดา) ลดฟอนต์ก่อน ถ้ายังไม่พอค่อยตัดข้อความแล้วใส่ "…"
    """
    font = font or FONT_REGULAR
    size = base_size
    while size >= min_size:
        if stringWidth(val, font, size) <= max_width:
            return size, val
        size -= 0.5
    # เล็กสุดแล้วยังไม่พอ ตัดตัวอักษรออกทีละตัวจนกว่าจะพอดี (คำนวณความกว้างจริงทุกครั้ง)
    text = val
    while len(text) > 1 and stringWidth(text + "…", font, min_size) > max_width:
        text = text[:-1]
    return min_size, text.rstrip() + "…"


def wrap_thai(text, max_width, font_size=7):
    """
    ตัดบรรทัดข้อความให้กว้างไม่เกิน `max_width` (pt) จริงๆ (วัดด้วย stringWidth ไม่ใช่นับตัวอักษรเดา)
    ใช้ pythainlp ตัดคำภาษาไทยก่อน แล้วค่อยรวมเป็นบรรทัด กันไม่ให้ตัดกลางคำ
    (ภาษาไทยไม่มีเว้นวรรคระหว่างคำ ถ้าใช้ textwrap ธรรมดาจะตัดกลางคำ เช่น "นาที" กลายเป็น "นา"+"ที")
    เน้นใช้ความกว้างที่มีให้เต็มที่ก่อนตัดบรรทัด (บรรทัดยาวดีกว่าตัดถี่ๆ)
    """
    text = text.strip()
    if not text:
        return []
    tokens = word_tokenize(text, engine="newmm", keep_whitespace=True)
    lines = []
    current = ""
    for tok in tokens:
        candidate = current + tok
        if not current or stringWidth(candidate, FONT_REGULAR, font_size) <= max_width:
            current = candidate
        else:
            lines.append(current.strip())
            current = tok
        # คำเดี่ยวที่ยาวเกิน max_width เอง (เช่น URL หรือคำอังกฤษยาวๆ) ตัดตามตัวอักษรตรงๆ
        while stringWidth(current, FONT_REGULAR, font_size) > max_width and len(current) > 1:
            cut = len(current)
            while cut > 1 and stringWidth(current[:cut], FONT_REGULAR, font_size) > max_width:
                cut -= 1
            lines.append(current[:cut])
            current = current[cut:]
    if current.strip():
        lines.append(current.strip())
    return lines


def _fit_lines(val, avail_height, max_width, base_size=7, min_size=4.5, allow_overflow=False):
    """
    หาขนาดฟอนต์ + บรรทัดที่ตัดแล้ว ที่พอดีกับพื้นที่ว่าง (avail_height) และความกว้างจริง (max_width)
    ลดขนาดฟอนต์ทีละ 0.5pt จนกว่าจะพอดี ถ้ายังไม่พอดีที่ขนาดเล็กสุด จะตัดบรรทัดที่เกินออกและใส่ "…" ต่อท้าย
    คืนค่า (font_size, line_height, lines)

    allow_overflow=True (ผู้ใช้ขอเฉพาะช่อง "การปฐมพยาบาล" - ทางตา/ปาก/ผิวหนัง/หายใจ): ใช้ base_size
    คงที่เสมอ ไม่ลดขนาดฟอนต์ และไม่ตัดบรรทัดทิ้งเลย แม้ข้อความจะยาวเกิน avail_height จนล้นลงไปทับ
    แถวถัดไปในกล่องเดียวกันก็ตาม (ผู้ใช้เลือกแบบนี้เอง เพราะกล่องแชร์กันจริงมีที่ทางกายภาพจำกัดมาก
    ยอมรับความเสี่ยงซ้อนทับ ดีกว่าฟอนต์เล็กจนอ่านยาก/ข้อความปฐมพยาบาลถูกตัดหาย)
    """
    if allow_overflow:
        lines = wrap_thai(val, max_width, font_size=base_size)
        return base_size, base_size + 1.3, lines
    size = base_size
    lines = []
    line_height = size + 1.3
    while size >= min_size:
        lines = wrap_thai(val, max_width, font_size=size)
        line_height = size + 1.3
        if len(lines) * line_height <= avail_height:
            return size, line_height, lines
        size -= 0.5
    # ถึงขนาดเล็กสุดแล้วยังไม่พอ ตัดจำนวนบรรทัดให้พอดีพื้นที่ แล้วใส่ "…" ท้ายบรรทัดสุดท้าย
    max_lines = max(1, int(avail_height // line_height))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + "…"
    return min_size, line_height, lines


BLOCK_TOP_PAD = 1.0  # ระยะห่างเพิ่มเติมเล็กน้อยใต้ตำแหน่ง baseline ที่ชดเชย ascent แล้ว (กันชิดขอบบนพอดีเป๊ะ)


def draw_text(c, val, avail_height, max_width, size=7, single_line=False, center=False, allow_overflow=False,
              no_wrap=False):
    """เขียนข้อความลงตำแหน่งที่ translate ไว้แล้ว (จุดเริ่ม 0,0) พอดีกับพื้นที่ว่าง/ความกว้างจริงที่มี
    center=True ใช้กับช่องแคบๆ บรรทัดเดียว (เช่น สี/กลิ่น/pH) ให้ข้อความอยู่กึ่งกลางช่องแทนชิดซ้าย
    allow_overflow=True: คงขนาดฟอนต์ไว้เท่า size เสมอ ไม่ลด/ไม่ตัดบรรทัดทิ้ง แม้ข้อความจะยาวเกิน
    avail_height (ดู _fit_lines)
    no_wrap=True: ไม่ตัดขึ้นบรรทัดใหม่เลย วาดทั้งข้อความเป็นบรรทัดเดียวที่ขนาดฟอนต์คงที่ (size) แม้จะ
    ยาวเกินความกว้างกล่องก็ตาม ยอมให้ไหลล้นออกทางขวา - ใช้กับ "การปฐมพยาบาล" ตามที่ผู้ใช้ขอ (ต่างจาก
    allow_overflow ตรงที่ allow_overflow ยังตัดขึ้นบรรทัดใหม่ตามความกว้างอยู่ แค่ไม่ลดฟอนต์/ไม่ตัดทิ้ง)"""
    if not val or val == "-":
        return
    val = _fix_sara_am_order(val)
    if no_wrap:
        c.setFont(FONT_REGULAR, size)
        baseline_offset = size * 0.75 + BLOCK_TOP_PAD
        c.drawString(0, -baseline_offset, val.strip())
        return
    if single_line:
        # ช่องบรรทัดเดียว (เช่น ชื่อสาร, CAS No, ชื่อผู้จัดทำ) ไม่ยอมให้ขึ้นบรรทัดใหม่
        # พิกัด "top" ของแต่ละช่องวัดจากขอบบนตัวอักษรของป้าย (label) ด้วย pdfplumber แต่ drawString
        # ใช้ y เป็นเส้น baseline ตรงๆ ไม่ใช่ขอบบนตัวอักษร ถ้าไม่ชดเชยค่านี้ ข้อความจะลอยสูงกว่าป้ายเสมอ
        # (baseline อยู่ต่ำกว่าขอบบนตัวอักษรประมาณ ~75% ของขนาดฟอนต์ ตามค่า ascent ทั่วไป)
        font_size, text = _fit_single_line(val, max_width, base_size=size)
        c.setFont(FONT_REGULAR, font_size)
        y = -font_size * 0.75
        if center:
            c.drawCentredString(max_width / 2, y, text)
        else:
            c.drawString(0, y, text)
        return
    font_size, line_height, lines = _fit_lines(val, avail_height - BLOCK_TOP_PAD, max_width, base_size=size,
                                                allow_overflow=allow_overflow)
    c.setFont(FONT_REGULAR, font_size)
    # "top" ที่ผูกไว้ตอน translate() คือขอบบนตัวอักษรของป้าย (label) แต่ drawString ใช้ y เป็น baseline
    # ตรงๆ เหมือนที่ทำใน single_line ด้านบน - เดิมใช้แค่ BLOCK_TOP_PAD (2.5pt) คงที่ ไม่ได้ชดเชย ascent
    # จริง (~75% ของ font size) ทำให้ตัวอักษรบรรทัดแรกลอยสูงเกิน "top" ไปทับแถว/เส้นขอบด้านบนแทน
    baseline_offset = font_size * 0.75 + BLOCK_TOP_PAD
    for i, ln in enumerate(lines):
        c.drawString(0, -baseline_offset - i * line_height, ln)


def draw_header_bar(c, val, top_center, base_size):
    """เขียนข้อความกึ่งกลางแถบเหลือง/แดงบนหัวฟอร์ม (ชื่อสารตัวใหญ่ / Signal Word)"""
    val = (val or "").strip()
    if not val or val == "-":
        return
    val = _fix_sara_am_order(val)
    size, text = _fit_single_line(val, HEADER_BAR_MAX_WIDTH, base_size=base_size, min_size=7)
    c.setFont(FONT_BOLD, size)
    y = Y(top_center) - size * 0.35
    c.drawCentredString(HEADER_BAR_X_CENTER, y, text)


def draw_image_in_box(c, image_path, box):
    """วางรูปภาพให้พอดีกรอบ (box) แบบรักษาสัดส่วน (aspect ratio) และอยู่กึ่งกลาง"""
    if not image_path or not os.path.exists(image_path):
        return
    try:
        img = ImageReader(image_path)
        iw, ih = img.getSize()
    except Exception:
        return  # ไฟล์รูปเสียหรืออ่านไม่ได้ ข้ามไปเฉยๆ ไม่ทำให้สร้าง PDF พัง

    box_w = box["x1"] - box["x0"]
    box_h = box["bottom"] - box["top"]
    scale = min(box_w / iw, box_h / ih)
    draw_w, draw_h = iw * scale, ih * scale
    # จัดกึ่งกลางในกรอบ
    x = box["x0"] + (box_w - draw_w) / 2
    top_y = box["top"] + (box_h - draw_h) / 2
    y = Y(top_y) - draw_h
    c.drawImage(image_path, x, y, width=draw_w, height=draw_h,
                preserveAspectRatio=True, mask="auto")


def draw_pictograms(c, selected_keys):
    """
    วางสัญลักษณ์ GHS ที่เลือกไว้ในกล่องมุมซ้ายบน (PICTOGRAM_BOX)
    ถ้ามีหลายอัน จัดเรียงเป็นตาราง (grid) แบ่งพื้นที่กล่องเท่าๆ กัน เรียงตามลำดับมาตรฐานเสมอ
    (ไม่ใช่ตามลำดับที่ผู้ใช้ติ๊ก) เพื่อให้หน้าตาเหมือนกันทุกครั้ง ตัดพื้นที่วาด (clip) ให้อยู่แค่ใน
    ขอบเขตกรอบเสมอ (ดูเหตุผลใน draw_ppe_icons - รับประกันไม่ล้นด้วย PDF clip ไม่ใช่แค่คำนวณตำแหน่ง)
    """
    keys = [k for k in PICTOGRAM_ORDER if k in (selected_keys or [])]
    if not keys:
        return
    n = len(keys)
    cols = 1 if n == 1 else 2 if n <= 4 else 3
    rows = -(-n // cols)  # ceil division

    box_w = PICTOGRAM_BOX["x1"] - PICTOGRAM_BOX["x0"]
    box_h = PICTOGRAM_BOX["bottom"] - PICTOGRAM_BOX["top"]
    cell_w = box_w / cols
    cell_h = box_h / rows

    c.saveState()
    clip = c.beginPath()
    clip.rect(PICTOGRAM_BOX["x0"], Y(PICTOGRAM_BOX["bottom"]), box_w, box_h)
    c.clipPath(clip, stroke=0, fill=0)
    for i, key in enumerate(keys):
        icon_path = os.path.join(PICTOGRAM_ICON_DIR, f"{key}.png")
        row, col = divmod(i, cols)
        cell = {
            "x0": PICTOGRAM_BOX["x0"] + col * cell_w,
            "x1": PICTOGRAM_BOX["x0"] + (col + 1) * cell_w,
            "top": PICTOGRAM_BOX["top"] + row * cell_h,
            "bottom": PICTOGRAM_BOX["top"] + (row + 1) * cell_h,
        }
        draw_image_in_box(c, icon_path, cell)
    c.restoreState()


PPE_ICON_MAX_SIZE = 30  # เพดานขนาดไอคอน (pt) กันไม่ให้ไอคอนถูกยืดใหญ่/ห่างกันตอนมีแค่ 1-3 อัน
                         # ลดจาก 42 ตามที่ผู้ใช้ขอ (พร้อมกับแก้ PPE_BOX["bottom"] ที่ผิดด้านบน)


def draw_ppe_icons(c, selected_keys):
    """
    วางไอคอน PPE ที่เลือก/ตรวจจับได้ในกล่อง "อุปกรณ์คุ้มครองความปลอดภัยส่วนบุคคล" (PPE_BOX)

    ต่างจาก draw_pictograms 3 จุด (ผู้ใช้รายงานปัญหาซ้ำหลายรอบ ทั้งไอคอนล้นออกนอกกรอบ และตอนแก้ล้น
    แล้วไอคอนน้อยชิ้นกลับถูกยืดห่างกันจนดูหลวม - แก้ให้ "แน่น กะทัดรัด และรับประกันไม่ล้น" ไปพร้อมกัน):
      1. ใช้ขนาดไอคอนคงที่ (ไม่เกิน PPE_ICON_MAX_SIZE) + ช่องว่างคงที่เล็กๆ ระหว่างไอคอน แทนการแบ่ง
         พื้นที่กรอบทั้งหมดเท่าๆ กันตามจำนวนแถว/คอลัมน์ - ไอคอนจะ "ชิดกัน" เสมอไม่ว่าจะมีกี่อัน แทนที่
         จะถูกยืดใหญ่ขึ้นเรื่อยๆ จนมีช่องว่างเยอะเมื่อมีไอคอนน้อย (เช่น 1 อันในแถวสุดท้ายที่ไม่เต็ม)
      2. หาจำนวนคอลัมน์ที่ทำให้ตารางทั้งก้อน (ใหญ่สุดเท่าที่ยังพอดีทั้งกว้าง/สูงกรอบ) กะทัดรัดที่สุด
      3. จัดตารางทั้งก้อนให้อยู่กึ่งกลางแนวนอนของกรอบ ชิดขอบบนกรอบ (ไม่กึ่งกลางแนวตั้ง กันไอคอนลอย
         ไปกลางกรอบเวลามีน้อยชิ้น) แล้ว "ตัดพื้นที่วาด" (PDF clip) ให้อยู่แค่ในขอบเขต PPE_BOX เสมอ
         เป็นการรับประกันไม่ล้นจริง ต่อให้สูตรคำนวณข้างบนพลาดยังไงก็ไม่มีพิกเซลไหนหลุดออกนอกกรอบได้
    """
    keys = [k for k in PPE_ORDER if k in (selected_keys or [])]
    if not keys:
        return
    n = len(keys)
    box_w = PPE_BOX["x1"] - PPE_BOX["x0"]
    box_h = PPE_BOX["bottom"] - PPE_BOX["top"]
    gap = 2  # ช่องว่างคงที่ระหว่างไอคอน (ไม่ใช่สัดส่วนของกรอบ) ให้ไอคอนชิดกันเสมอไม่ว่ากรอบจะใหญ่แค่ไหน
             # ลดจาก 4 ตามที่ผู้ใช้ขอ ให้ไอคอนชิดกันมากขึ้นอีก

    best_cols, best_size = 1, 0
    for cols_try in range(1, n + 1):
        rows_try = -(-n // cols_try)
        size_w = (box_w - (cols_try - 1) * gap) / cols_try
        size_h = (box_h - (rows_try - 1) * gap) / rows_try
        size = min(size_w, size_h)
        if size > best_size:
            best_size, best_cols = size, cols_try
    cols = best_cols
    rows = -(-n // cols)
    icon_size = min(best_size, PPE_ICON_MAX_SIZE)

    grid_w = cols * icon_size + (cols - 1) * gap
    grid_h = rows * icon_size + (rows - 1) * gap
    start_x = PPE_BOX["x0"] + (box_w - grid_w) / 2  # จัดกึ่งกลางแนวนอนทั้งตาราง
    start_y = PPE_BOX["top"]  # ชิดขอบบนกรอบ

    c.saveState()
    clip = c.beginPath()
    clip.rect(PPE_BOX["x0"], Y(PPE_BOX["bottom"]), box_w, box_h)
    c.clipPath(clip, stroke=0, fill=0)
    for i, key in enumerate(keys):
        icon_path = os.path.join(PPE_ICON_DIR, f"{key}.png")
        row, col = divmod(i, cols)
        x0 = start_x + col * (icon_size + gap)
        top = start_y + row * (icon_size + gap)
        cell = {"x0": x0, "x1": x0 + icon_size, "top": top, "bottom": top + icon_size}
        draw_image_in_box(c, icon_path, cell)
    c.restoreState()


def _cover_box(c, box):
    """วาดสี่เหลี่ยมสีขาวปิดข้อความตัวอย่างเดิมของเทมเพลต (เช่น '-' หรือ '(EHS Manager)')"""
    c.saveState()
    c.setFillColorRGB(1, 1, 1)
    c.rect(box["x0"], Y(box["bottom"]), box["x1"] - box["x0"],
           box["bottom"] - box["top"], fill=1, stroke=0)
    c.restoreState()


def build_overlay(data, label_image_path=None, container_image_path=None):
    register_thai_fonts()
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    # วันที่จัดทำ (Issue Date) - คำนวณสดทุกครั้งที่สร้าง PDF (ไม่ใช่ค่าจาก data ที่ผู้ใช้กรอก)
    issue_date = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%d-%m-%Y")
    c.saveState(); c.translate(ISSUE_DATE_POS[0], Y(ISSUE_DATE_POS[1]))
    draw_text(c, issue_date, 12, ISSUE_DATE_MAX_WIDTH, size=9, single_line=True)
    c.restoreState()
    # แถบเหลือง (ชื่อสารเคมี ตัวใหญ่) / แถบแดง (Signal Word) บนหัวฟอร์ม
    draw_header_bar(c, data.get("display_name", ""), HEADER["display_name"]["top_center"], HEADER["display_name"]["size"])
    draw_header_bar(c, data.get("signal_word", ""), HEADER["signal_word"]["top_center"], HEADER["signal_word"]["size"])
    # ช่องบรรทัดเดียว (บังคับบรรทัดเดียวเสมอ กันล้นไปทับแถวถัดไปที่ติดกันมาก) - วางกึ่งกลางช่อง
    # (center=True) เพราะเป็นช่องแคบ ไม่ใช่ช่อง "ป้าย: ค่ายาว" แบบ BLOCK ชิดซ้ายแล้วดูไม่สมดุล
    for k, (x, top) in SHORT.items():
        val = (data.get(k) or "").strip()
        if val and val != "-" and k in SHORT_COVER:
            # เช่นช่อง pH เทมเพลตมีขีด "-" พิมพ์ไว้แล้ว ต้องปิดก่อนถ้ามีค่าจริงมาแทน
            _cover_box(c, SHORT_COVER[k])
        c.saveState(); c.translate(x, Y(top))
        draw_text(c, data.get(k, ""), _available_height(top, data), size=7,
                  single_line=True, max_width=_SHORT_MAX_WIDTH.get(k), center=True)
        c.restoreState()
    # ช่องข้อความยาว (ตัดบรรทัดแบบไทย + ลดฟอนต์อัตโนมัติถ้าจำเป็น) - ฟอนต์เริ่มต้นใหญ่ขึ้นเล็กน้อย
    # (7 -> 8) ยังลดอัตโนมัติได้ถ้าพื้นที่ไม่พอเหมือนเดิม แต่ข้อความสั้นๆ จะได้ตัวใหญ่ขึ้นเห็นชัดกว่าเดิม
    # ยกเว้น "การปฐมพยาบาล" (fa_eye/fa_oral/fa_skin/fa_inhale) ที่ผู้ใช้ขอให้ไม่ตัดขึ้นบรรทัดใหม่เลย
    # (no_wrap) เขียนยาวเป็นบรรทัดเดียวที่ขนาดฟอนต์คงที่ ยอมรับข้อความไหลล้นออกทางขวาถ้ายาวเกินกล่อง
    _FA_NO_WRAP_KEYS = {"fa_eye", "fa_oral", "fa_skin", "fa_inhale"}
    for k, (x, top) in BLOCK.items():
        c.saveState(); c.translate(x, Y(top))
        draw_text(c, data.get(k, ""), _available_height(top, data), BLOCK_MAX_WIDTH.get(k), size=8,
                  no_wrap=(k in _FA_NO_WRAP_KEYS))
        c.restoreState()
    # ช่องจัดทำโดย/อนุมัติโดย (บรรทัดเดียว) - ใส่วงเล็บครอบชื่อ/ตำแหน่ง "( ... )" ให้เหมือนตัวอย่าง
    # ที่พิมพ์ไว้แล้วในเทมเพลตฝั่ง "อนุมัติโดย" เช่น "(EHS Manager)" ให้ทั้งสองฝั่งดูสม่ำเสมอกัน
    for k, (x, top) in SIGNATURE.items():
        val = (data.get(k) or "").strip()
        if val and val != "-" and k in SIGNATURE_COVER:
            # ปิดข้อความตัวอย่างเดิมของเทมเพลตก่อน (เฉพาะตอนมีค่าจริงมาแทน)
            _cover_box(c, SIGNATURE_COVER[k])
        display_val = f"( {val} )" if val and val != "-" else val
        c.saveState(); c.translate(x, Y(top))
        draw_text(c, display_val, 12, size=7, single_line=True,
                  max_width=SIGNATURE_MAX_WIDTH.get(k))
        c.restoreState()
    # ชื่อผู้จัดทำ/ผู้อนุมัติ อีกจุด ต่อท้ายป้าย "จัดทำโดย :" / "อนุมัติโดย :" ตรงๆ (ตัวหนา) ให้เห็น
    # ชื่อคนเดียวกันซ้ำทั้งสองจุด (หัวบรรทัด + บรรทัด "ชื่อ-สกุล" ด้านล่าง)
    for k, (x, top) in SIGNATURE_HEADER.items():
        val = (data.get(k) or "").strip()
        if not val or val == "-":
            continue
        val = _fix_sara_am_order(val)
        c.saveState(); c.translate(x, Y(top))
        font_size, text = _fit_single_line(val, SIGNATURE_HEADER_MAX_WIDTH.get(k, 200), base_size=8, font=FONT_BOLD)
        c.setFont(FONT_BOLD, font_size)
        c.drawString(0, 0, text)
        c.restoreState()
    # NFPA เลขเดี่ยว วางกึ่งกลางสี่เหลี่ยมย่อยแต่ละสี ใช้สีขาวเพราะพื้นหลังเป็นสีเข้ม (แดง/น้ำเงิน)
    NFPA_SIZE = 11
    c.setFont(FONT_BOLD, NFPA_SIZE)
    c.setFillColorRGB(1, 1, 1)
    for k, (x_center, top_center) in NFPA.items():
        v = (data.get(k) or "").strip()
        if v:
            c.drawCentredString(x_center, Y(top_center) - NFPA_SIZE * 0.35, v)
    c.setFillColorRGB(0, 0, 0)
    # ช่องขาว (Special Hazard) - ตัวหนังสือสีดำ เพราะพื้นหลังเป็นสีขาว
    special_v = (data.get("nfpa_special") or "").strip()
    if special_v:
        special_size = 9
        c.setFont(FONT_BOLD, special_size)
        c.drawCentredString(NFPA_SPECIAL_X_CENTER, Y(NFPA_SPECIAL_TOP_CENTER) - special_size * 0.35, special_v)
    # รูปภาพ 2 ช่อง
    draw_image_in_box(c, label_image_path, IMAGE_BOXES["label_image"])
    draw_image_in_box(c, container_image_path, IMAGE_BOXES["container_image"])
    # สัญลักษณ์ GHS ที่ผู้ใช้ติ๊กเลือก (กล่องมุมซ้ายบน)
    draw_pictograms(c, data.get("pictograms"))
    draw_ppe_icons(c, data.get("ppe"))
    c.showPage(); c.save(); buf.seek(0)
    return buf


def fill_from_data(data, template_path, out_path,
                    label_image_path=None, container_image_path=None):
    """รับ data dict ที่แก้ไขแล้วจาก UI + รูป (ถ้ามี) วางลงเทมเพลตแล้วเซฟไฟล์ PDF ใหม่"""
    overlay = PdfReader(build_overlay(data, label_image_path, container_image_path))
    base = PdfReader(template_path)
    writer = PdfWriter()
    page = base.pages[0]
    page.merge_page(overlay.pages[0])
    writer.add_page(page)
    with open(out_path, "wb") as f:
        writer.write(f)
    return out_path


if __name__ == "__main__":
    # รันจากรากโปรเจกต์ด้วย: python -m pdf_gen.fill_template (import แบบ package ต้องใช้ -m)
    from core.parser import parse_sds
    data = parse_sds("getpdf_sample.pdf")
    fill_from_data(data, "assets/Template.pdf", "data/generated/test_output.pdf")
    print("saved test_output.pdf")
