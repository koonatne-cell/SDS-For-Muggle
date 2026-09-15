# -*- coding: utf-8 -*-
"""
label_template.py - สร้างฉลากสำหรับติดบนภาชนะบรรจุสารเคมี (secondary container label)

เวอร์ชันนี้ทำตามบรีฟ "PROMPT_ChemSafe_Label" ของผู้ใช้ + เทมเพลตอ้างอิง Landscrape_Label.pdf:
  - แนวนอนอย่างเดียว (ตัดแนวตั้งออกทั้งหมด)
  - เลย์เอาต์ป้ายหัวข้อ "ชิดซ้าย" ของแต่ละกล่อง (คอลัมน์ซ้าย) + กล่องข้อมูล (คอลัมน์ขวา) แทนแบบเดิม
    ที่วางป้ายไว้เหนือกล่อง - สัดส่วนคอลัมน์/แถวด้านล่าง (LABEL_COL_FRAC ฯลฯ) วัดจริงจากไฟล์ต้นแบบ
    ด้วย fitz (page.get_drawings()) แล้วแปลงเป็นสัดส่วน % ของหน้า เพื่อให้ปรับขนาดได้ (small/medium/
    large/A4/A5/A6/custom) แต่คงหน้าตาเดิมของเทมเพลตไว้
  - ข้อความในทุกกล่อง "ชิดซ้ายบน" (top-left) ห้ามจัดกึ่งกลาง ตามที่บรีฟกำหนดไว้ชัดเจน
  - ตัดบรรทัดอัตโนมัติ + ลดขนาดฟอนต์อัตโนมัติถ้ายาวเกินกล่อง (auto-fit/shrink to fit)
"""
import io
import os
import re
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth

from pdf_gen.fill_template import (
    register_thai_fonts, FONT_REGULAR, FONT_BOLD,
    wrap_thai, PICTOGRAM_ICON_DIR, PICTOGRAM_ORDER,
    _fix_sara_am_order,
)

SIGNAL_COLOR = (0.75, 0.05, 0.05)  # สีแดงสำหรับคำสัญญาณ (Signal word) เหมือนฉลากเคมีทั่วไป

# รหัส Hazard/Precautionary statement ที่ขึ้นต้นบรรทัด (เช่น "H314", "P305+P351+P338")
_CODE_PREFIX_RE = re.compile(r"^([HP]\d{3}(?:\s*[/+]\s*[HP]?\d{3})*)[:\s]*(.*)$")

# 7 หัวข้อตามเทมเพลตอ้างอิง (เลขข้อ, ป้ายไทย, ป้ายอังกฤษ, น้ำหนักความสูงกล่อง) - น้ำหนักวัดสัดส่วนจริง
# จากไฟล์ Landscrape_Label.pdf (สูงใกล้เคียงกันทุกกล่อง ต่างจากเทมเพลตเดิมที่กล่องผู้ผลิตสูงกว่ามาก)
BOX_ITEMS = [
    ("1", "ชื่อผลิตภัณฑ์", "Product Name", 1.03),
    ("2", "ชื่อสารเคมีอันตราย", "Hazardous Substances", 1.09),
    ("3", "รูปสัญลักษณ์", "GHS Pictograms", 1.11),
    ("4", "คำสัญญาณ", "Signal Words", 1.00),
    ("5", "ข้อความแสดงอันตราย", "Hazard Statements", 1.13),
    ("6", "ข้อความระวัง", "Precautionary Statements", 1.13),
    ("7", "ผู้ผลิต", "Manufacturing", 1.13),
]

# สัดส่วน % ของหน้า วัดจาก Landscrape_Label.pdf (841.92 x 595.32pt, A4 แนวนอน) ด้วย fitz
# คอลัมน์ซ้าย = ป้ายหัวข้อ, คอลัมน์ขวา = กล่องข้อมูล, เว้นช่องว่างเล็กน้อยระหว่างคอลัมน์
LEFT_MARGIN_FRAC = 0.0273
LABEL_COL_FRAC = 0.2744
COL_GAP_FRAC = 0.0103
BOX_COL_FRAC = 0.6355
TOP_MARGIN_FRAC = 0.0649
BOTTOM_MARGIN_FRAC = 0.0494
ROW_GAP_FRAC = 0.0126  # ต่อช่องว่างระหว่างแถว (มี 6 ช่องระหว่าง 7 แถว)


def _split_statement(text):
    """แยกข้อความ Hazard/Precautionary 1 ข้อ ออกเป็น (รหัสตัวหนา+colon, ข้อความที่เหลือ) ถ้ามีรหัส
    (H226, P305+P351+P338 ฯลฯ) นำหน้า คืน (None, text) ถ้าไม่มีรหัส (ข้อความบรรยายล้วนๆ)"""
    text = (text or "").strip()
    if not text or text == "-":
        return None, ""
    m = _CODE_PREFIX_RE.match(text)
    if m and m.group(1):
        code = re.sub(r"\s+", "", m.group(1))
        rest = m.group(2).strip()
        return code + ": ", rest
    return None, text


def _fit_paragraph(text, box_w, box_h, base_size, min_size=5, line_gap=1.8):
    """ลดฟอนต์จนข้อความ (ตัดบรรทัดแล้ว) พอดีกับกล่อง คืน (font_size, line_height, lines)
    ถ้าเล็กสุดแล้วยังไม่พอ ตัดบรรทัดส่วนเกินออกแล้วใส่ '…' ท้ายบรรทัดสุดท้าย"""
    size = base_size
    lines, line_h = [], base_size + line_gap
    while size >= min_size:
        lines = wrap_thai(text, box_w, font_size=size)
        line_h = size + line_gap
        used_h = size + max(0, len(lines) - 1) * line_h
        if used_h <= box_h:
            return size, line_h, lines
        size -= 0.5
    max_lines = max(1, int((box_h - min_size) // line_h) + 1)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + "…"
    return min_size, line_h, lines


def _fit_statement_list(statements, box_w, box_h, base_size, min_size=5, line_gap=1.8):
    """หาขนาดฟอนต์ที่ใหญ่สุดที่ทำให้รายการข้อความทั้งหมด (รวมกัน) พอดีความสูงกล่อง"""
    size = base_size
    while size >= min_size:
        total = 0
        for stmt in statements:
            code, rest = _split_statement(stmt)
            full = (code + rest) if code else rest
            if full:
                total += len(wrap_thai(full, box_w, font_size=size))
        line_h = size + line_gap
        used_h = size + max(0, total - 1) * line_h
        if used_h <= box_h:
            return size
        size -= 0.5
    return min_size


def _draw_statements_in_box(c, statements, x, y_top, box_w, box_h, page_h, base_size):
    """วาดรายการ Hazard/Precautionary statement ในกล่อง ชิดซ้ายบน (รหัสตัวหนา+colon นำหน้าข้อความปกติ)
    หยุดวาดถ้าเกินความสูงกล่อง คืนตำแหน่ง y ของบรรทัดสุดท้ายที่วาดจริง"""
    statements = [s for s in (statements or []) if (s or "").strip() and s.strip() != "-"]
    if not statements or box_h < 5:
        return y_top
    size = _fit_statement_list(statements, box_w, box_h, base_size)
    line_h = size + 1.8
    y = y_top + size
    last_drawn_y = y
    bottom = y_top + box_h
    for stmt in statements:
        code, rest = _split_statement(stmt)
        full = (code + rest) if code else rest
        lines = wrap_thai(full, box_w, font_size=size)
        for i, ln in enumerate(lines):
            if y > bottom:
                return last_drawn_y
            if i == 0 and code and ln.startswith(code.strip()):
                code_txt = code.strip()
                rest_txt = ln[len(code_txt):].strip()
                c.setFont(FONT_BOLD, size)
                c.drawString(x, page_h - y, code_txt)
                code_w = stringWidth(code_txt + " ", FONT_BOLD, size)
                c.setFont(FONT_REGULAR, size)
                c.drawString(x + code_w, page_h - y, rest_txt)
            else:
                c.setFont(FONT_REGULAR, size)
                c.drawString(x, page_h - y, ln)
            last_drawn_y = y
            y += line_h
    return last_drawn_y


def _draw_paragraph_in_box(c, text, x, y_top, box_w, box_h, page_h, base_size,
                            bold=False, color=None, upper=False):
    """วาดข้อความยาว 1 ก้อน (ตัดบรรทัดอัตโนมัติ) ให้พอดีกล่อง ชิดซ้ายบนเสมอ (บรีฟห้ามจัดกึ่งกลาง)
    คืนตำแหน่ง y ของบรรทัดสุดท้ายที่วาดจริง"""
    text = (text or "").strip()
    if not text or text == "-" or box_h < 5:
        return y_top
    if upper:
        text = text.upper()
    size, line_h, lines = _fit_paragraph(text, box_w, box_h, base_size)
    c.setFont(FONT_BOLD if bold else FONT_REGULAR, size)
    if color:
        c.setFillColorRGB(*color)
    y = y_top + size
    for ln in lines:
        c.drawString(x, page_h - y, ln)
        last_drawn_y = y
        y += line_h
    y = last_drawn_y + line_h * 0.35
    if color:
        c.setFillColorRGB(0, 0, 0)
    return y


def _draw_pictograms_in_box(c, pictogram_keys, x, y_top, box_w, box_h, page_h):
    """วาดไอคอน GHS เรียงแถวเดียว ชิดซ้ายของกล่อง (ไม่กึ่งกลาง ตามกฎ top-left ของบรีฟ)"""
    keys = [k for k in PICTOGRAM_ORDER if k in (pictogram_keys or [])]
    if not keys:
        return
    n = len(keys)
    gap = box_w * 0.02
    max_icon_w = (box_w - (n - 1) * gap) / n
    icon_size = min(box_h * 0.9, max_icon_w)
    for i, key in enumerate(keys):
        icon_path = os.path.join(PICTOGRAM_ICON_DIR, f"{key}.png")
        if os.path.exists(icon_path):
            icon_x = x + i * (icon_size + gap)
            c.drawImage(icon_path, icon_x, page_h - y_top - icon_size, width=icon_size, height=icon_size,
                        preserveAspectRatio=True, mask="auto")


def _draw_box_label(c, x, y_top, w, num, label_th, label_en, base_size, page_h):
    """วาดป้ายหัวข้อ 2 บรรทัด (เลข+ไทย, (English)) ชิดซ้ายบนของคอลัมน์ป้าย ตามสไตล์เทมเพลตอ้างอิง
    y_top เป็นระยะจากขอบบนของหน้า (top-down) เหมือนฟังก์ชันวาดกล่องอื่นๆ ในไฟล์นี้"""
    size, line_h, lines = _fit_paragraph(f"{num}.{label_th}", w, base_size * 2.6, base_size)
    c.setFont(FONT_BOLD, size)
    y = y_top + size
    for ln in lines:
        c.drawString(x, page_h - y, ln)
        y += line_h
    en_size, _, en_lines = _fit_paragraph(f"({label_en})", w, base_size * 1.6, base_size)
    c.setFont(FONT_BOLD, en_size)
    for ln in en_lines:
        c.drawString(x, page_h - y, ln)
        y += en_size + 1.8


def build_label_pdf(data, size_key, size_presets, out_path):
    """
    สร้างฉลากภาชนะบรรจุ 1 หน้า แนวนอนเสมอ (ตัดแนวตั้งออกตามที่ผู้ใช้ขอ) ขนาดตาม size_key
    เป็นกล่อง 7 หัวข้อ เลย์เอาต์ "ป้ายซ้าย + กล่องขวา" ตามเทมเพลตอ้างอิง Landscrape_Label.pdf
    size_presets: list ของ (key, label, width_mm, height_mm) เหมือนใน fields.LABEL_SIZE_PRESETS
    (เก็บเป็นแนวตั้งใน preset เดิม แต่ที่นี่สลับ width/height ให้เป็นแนวนอนเสมอ เพราะตัดตัวเลือก
    แนวตั้ง/แนวนอนออกจากฟอร์มแล้ว)
    """
    register_thai_fonts()

    # กันตัวอักษร "น้ำ"-แบบ (วรรณยุกต์+สระอำ) วาดทับกันตอนไม่มี text-shaping engine (ดูคอมเมนต์เต็ม
    # ใน fill_template.py ตรง _fix_sara_am_order) ทำครั้งเดียวตรงนี้ให้ครอบคลุมทุกช่องของฉลาก
    data = {
        k: (
            [_fix_sara_am_order(v) if isinstance(v, str) else v for v in val]
            if isinstance(val, list)
            else _fix_sara_am_order(val) if isinstance(val, str) else val
        )
        for k, val in data.items()
    }

    preset = next((p for p in size_presets if p[0] == size_key), size_presets[0])
    _, _, width_mm, height_mm = preset
    if width_mm is None or height_mm is None:
        try:
            width_mm = float(data.get("custom_width_mm") or 100)
        except (TypeError, ValueError):
            width_mm = 100
        try:
            height_mm = float(data.get("custom_height_mm") or 150)
        except (TypeError, ValueError):
            height_mm = 150
        width_mm = max(20, width_mm)
        height_mm = max(20, height_mm)
    # บังคับแนวนอนเสมอ (กว้าง > สูง) - preset เดิมเก็บเป็นแนวตั้ง ต้องสลับกัน
    if width_mm < height_mm:
        width_mm, height_mm = height_mm, width_mm
    scale = height_mm / 148.0  # อ้างอิงความสูง A5 แนวนอน (ใกล้เคียงขนาดที่ใช้บ่อยสุด)

    width_pt, height_pt = width_mm * mm, height_mm * mm
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width_pt, height_pt))

    # เส้นขอบรอบนอกทั้งหน้า (เหมือนเทมเพลตอ้างอิง)
    c.setLineWidth(0.75)
    c.rect(1, 1, width_pt - 2, height_pt - 2, stroke=1, fill=0)

    left_margin = width_pt * LEFT_MARGIN_FRAC
    label_col_w = width_pt * LABEL_COL_FRAC
    box_x = left_margin + label_col_w + width_pt * COL_GAP_FRAC
    box_w = width_pt * BOX_COL_FRAC
    top_margin = height_pt * TOP_MARGIN_FRAC
    bottom_margin = height_pt * BOTTOM_MARGIN_FRAC
    row_gap = height_pt * ROW_GAP_FRAC

    total_weight = sum(w for *_, w in BOX_ITEMS)
    available_h = height_pt - top_margin - bottom_margin - row_gap * (len(BOX_ITEMS) - 1)

    label_size = max(6.5, 8 * scale)
    body_size = max(6, 8 * scale)
    inner_pad = 3 * scale

    y = top_margin
    for num, label_th, label_en, weight in BOX_ITEMS:
        this_box_h = available_h * (weight / total_weight)

        c.setLineWidth(0.75)
        c.rect(box_x, height_pt - y - this_box_h, box_w, this_box_h, stroke=1, fill=0)

        _draw_box_label(c, left_margin, y, label_col_w, num, label_th, label_en, label_size, height_pt)

        inner_x = box_x + inner_pad
        inner_y = y + inner_pad
        inner_w = box_w - 2 * inner_pad
        inner_h = this_box_h - 2 * inner_pad

        if num == "1":
            # ชื่อผลิตภัณฑ์บรรทัดบน + CAS No. บรรทัดล่าง (2 บรรทัดในกรอบเดียวกัน ตามบรีฟ) ชิดซ้ายบน
            cas = (data.get("cas") or "").strip()
            cas_line = f"CAS No: {cas}" if cas and cas != "-" else ""
            cas_size = max(6, 7 * scale)
            cas_reserved_h = (cas_size + 3) if cas_line else 0
            name_h = max(inner_h * 0.5, inner_h - cas_reserved_h)
            ny = _draw_paragraph_in_box(c, data.get("product_name"), inner_x, inner_y, inner_w, name_h,
                                        height_pt, max(9, 12 * scale), bold=True)
            if cas_line:
                _draw_paragraph_in_box(c, cas_line, inner_x, ny, inner_w,
                                       inner_h - (ny - inner_y), height_pt, cas_size)
        elif num == "2":
            substances = [s.strip() for s in (data.get("hazardous_substances") or []) if s and s.strip()]
            _draw_paragraph_in_box(c, ", ".join(substances), inner_x, inner_y, inner_w, inner_h,
                                   height_pt, body_size)
        elif num == "3":
            _draw_pictograms_in_box(c, data.get("pictograms"), inner_x, inner_y, inner_w, inner_h, height_pt)
        elif num == "4":
            _draw_paragraph_in_box(c, data.get("signal_word"), inner_x, inner_y, inner_w, inner_h, height_pt,
                                   max(9, 12 * scale), bold=True, color=SIGNAL_COLOR, upper=True)
        elif num == "5":
            _draw_statements_in_box(c, data.get("hazard_statements"), inner_x, inner_y, inner_w, inner_h,
                                    height_pt, body_size)
        elif num == "6":
            _draw_statements_in_box(c, data.get("precautionary_statements"), inner_x, inner_y, inner_w, inner_h,
                                    height_pt, body_size)
        elif num == "7":
            _draw_paragraph_in_box(c, data.get("supplier_name"), inner_x, inner_y, inner_w, inner_h,
                                   height_pt, max(9, 10 * scale), bold=True)

        y += this_box_h + row_gap

    c.showPage()
    c.save()
    buf.seek(0)
    return buf


def fill_label(data, size_key, size_presets, out_path):
    """สร้างไฟล์ PDF ฉลาก แล้วเซฟลง out_path"""
    buf = build_label_pdf(data, size_key, size_presets, out_path)
    with open(out_path, "wb") as f:
        f.write(buf.read())
    return out_path


if __name__ == "__main__":
    # รันจากรากโปรเจกต์ด้วย: python -m pdf_gen.label_template (import แบบ package ต้องใช้ -m)
    from core.fields import LABEL_SIZE_PRESETS
    sample = {
        "product_name": "Sodium Hydroxide 50%",
        "cas": "1310-73-2",
        "signal_word": "Danger",
        "pictograms": ["corrosive", "toxic"],
        "hazardous_substances": ["Sodium hydroxide", "Water"],
        "hazard_statements": [
            "H314 Causes severe skin burns and eye damage.",
            "H318 Causes serious eye damage.",
        ],
        "precautionary_statements": [
            "P260/P280 Do not breathe mist/vapours. Wear PPE (gloves/clothing/eye/face protection).",
            "P301+P310+P330+P338 IF SWALLOWED: Rinse mouth. No vomiting. IF IN EYES: Rinse continuously. "
            "Seek emergency medical aid immediately.",
        ],
        "supplier_name": "Apex Chemical Corp.",
    }
    fill_label(sample, "medium", LABEL_SIZE_PRESETS, "data/generated/test_label.pdf")
    print("saved test_label.pdf")
