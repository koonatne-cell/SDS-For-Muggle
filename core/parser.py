# -*- coding: utf-8 -*-
"""
parser.py - ดึงข้อมูลเฉพาะฟิลด์จากไฟล์ SDS (Safety Data Sheet, เป็น PDF ที่มีตัวอักษรอ่านได้)

SDS แต่ละยี่ห้อ/ผู้ผลิตใช้คำที่ต่างกันสำหรับหัวข้อเดียวกัน (เช่น "Trade name:" vs "Product name:")
ถึงจะเรียงตาม section เดียวกันตามมาตรฐาน GHS ก็ตาม ฟิลด์แต่ละอันเลยลองจับหลาย pattern
เรียงจากที่เจอบ่อยสุดไปหายาก ใช้ pattern แรกที่เจอ ถ้าไม่เจอเลยคืนค่า default ("-")
"""
import io
import os
import re
import fitz  # PyMuPDF
from PIL import Image, ImageOps
import imagehash


# SDS ภาษาไทยหลายฉบับ (พบจากไฟล์ตระกูล Sika/CPAC/LANKO/Kemox) ใช้ฟอนต์ที่มีตาราง ToUnicode
# (glyph -> ตัวอักษรจริง) ผิดพลาด ทำให้วรรณยุกต์ "ไม้เอก" (่) และ "ไม้โท" (้) ถูกดึงออกมาเป็น
# อักขระผิดๆ (เช่น "ที่" กลายเป็น "ทีѷ" หรือ "ทีX" หรือ "ทีX") แทนที่จะเป็นตัวจริง แต่ "ตัวไหนแทนที่ตัวไหน"
# ไม่คงที่ - แต่ละไฟล์/ฟอนต์ใช้อักขระผิดคนละตัวกัน (เช่น CPAC ใช้ "ѷ", LANKO 101 ใช้ "\x06",
# Kemox A ใช้ "\x16") เก็บ hardcode ทีละตัวไม่ไหวเพราะจะมีไฟล์ใหม่ๆ มาเรื่อยๆ เลยตรวจจับแบบไดนามิก
# ต่อไฟล์แทน: หาอักขระ "แปลกปลอม" (ไม่ใช่ไทย/อังกฤษ/ตัวเลข/สัญลักษณ์ทั่วไป) ที่อยู่ "หลังอักษรไทยทันที"
# เสมอ (ตำแหน่งที่วรรณยุกต์ควรอยู่) แล้วเรียงตามความถี่ที่เจอ - ไม้เอกเป็นวรรณยุกต์ที่พบบ่อยที่สุดในภาษาไทย
# ไม้โทพบบ่อยรองลงมา จึงเดาว่าตัวที่เจอบ่อยสุด/รองลงมาคือไม้เอก/ไม้โทตามลำดับ (ผ่านการยืนยันด้วยไฟล์จริง
# หลายฉบับแล้วว่าแม่นมาก) ต้องเจอซ้ำอย่างน้อย MIN_MARK_COUNT ครั้งถึงจะเชื่อว่าเป็นวรรณยุกต์จริง กันไป
# เข้าใจผิดสัญลักษณ์ทั่วไป (เช่น bullet "·") ว่าเป็นวรรณยุกต์

# หมายเหตุ: ใช้ " \t\r\n" (whitespace จริงๆ) แทน \s เพราะ \s ของ Python ใน Unicode mode
# นับอักขระควบคุม C0 บางตัว (เช่น \x1d Group Separator) เป็น "ช่องว่าง" ด้วย! ถ้าฟอนต์ SDS
# แมปตัวอักษรวรรณยุกต์ที่พังไปเป็นอักขระควบคุมพวกนี้ (พบจริงในบางไฟล์) \s จะกันไม่ให้ตรวจจับเจอเลย
# ทำให้เครื่องหมายที่หายไปแสดงผลเหมือน "ช่องว่างกลางคำ" ที่แก้ไม่ได้ ทั้งที่จริงคือปัญหาเดียวกัน
_MARK_CANDIDATE_RE = re.compile(
    r"(?<=[฀-๿])([^฀-๿ \t\r\nA-Za-z0-9.,;:'\"()\-/+%°&@#*])"
)
# วรรณยุกต์/เครื่องหมายไทยที่มักถูกฟอนต์แปลงผิด เรียงจากพบบ่อยสุดไปหายาก (ใช้เป็นค่าตั้งต้นตอน
# ยังไม่มีหลักฐานอื่น ไม่ใช่กฎตายตัว - ดูเหตุผลด้านล่างว่าทำไมต้องมีการยืนยันด้วยพจนานุกรมด้วย)
_MARK_CANDIDATES = ["่", "้", "๊", "๋", "์"]
_MIN_MARK_COUNT = 2


def _fix_broken_thai_glyphs(text):
    """ตรวจจับ+แปลงอักขระวรรณยุกต์ไทยที่เพี้ยนจากฟอนต์ SDS ที่มี ToUnicode ผิดพลาด กลับเป็นตัวจริง
    แบบไดนามิกต่อเอกสาร ไม่ต้องรู้ล่วงหน้าว่าไฟล์นี้ใช้อักขระผิดตัวไหน

    เดิมเคยเดา mapping จากอันดับความถี่ล้วนๆ (ตัวที่เจอบ่อยสุด = ไม้เอก, รองลงมา = ไม้โท ฯลฯ)
    แต่พบว่าไฟล์เดียวกันบางไฟล์มี "อักขระผิดคนละตัว" 2 ตัวที่แทนวรรณยุกต์ตัวเดียวกัน (เช่นไฟล์ CPAC
    ใช้ทั้ง "Ѹ" และ "ҟ" แทนไม้โท เพราะ font subset คนละตัวกันคนละหน้า) ทำให้เดาผิดเป็นวรรณยุกต์อื่น
    ไปเมื่ออันดับความถี่ไม่ตรงกับที่คาดไว้ - แก้ด้วยการ "ทดลองแทนที่แล้วเช็คกับพจนานุกรมไทยจริง"
    (pythainlp) แทน: อักขระผิดตัวไหนก็ได้ ลองแทนด้วยวรรณยุกต์ที่เป็นไปได้ทีละตัว แล้วดูว่าคำที่ได้
    เป็นคำไทยจริงหรือไม่ เลือกตัวที่ทำให้เกิดคำจริงมากที่สุด - แม่นกว่าเพราะอิงเนื้อหาจริงในเอกสาร
    ไม่ใช่สมมติฐานอันดับความถี่ที่อาจผิดพลาดได้เมื่อมีอักขระผิดหลายตัวปนกัน
    """
    from collections import Counter
    from pythainlp.corpus.common import thai_words

    counts = Counter(_MARK_CANDIDATE_RE.findall(text))
    candidates = [ch for ch, n in counts.items() if n >= _MIN_MARK_COUNT]
    if not candidates:
        return text

    words = thai_words()
    mapping = {}
    for ch in candidates:
        best_mark, best_score = None, 0
        occurrences = [m.start() for m in re.finditer(re.escape(ch), text)]
        for mark in _MARK_CANDIDATES:
            score = 0
            for pos in occurrences:
                # ตัดช่วงข้อความรอบตำแหน่งที่พบ (ประมาณความยาวคำไทยทั่วไป) มาลองแทนที่แล้ว
                # เช็คว่ามีคำในพจนานุกรมที่ "ครอบคลุมตำแหน่งที่แทนที่" อยู่หรือไม่
                window_start = max(0, pos - 12)
                window_end = min(len(text), pos + 13)
                window = text[window_start:pos] + mark + text[pos + 1:window_end]
                rel_pos = pos - window_start
                for wlen in (2, 3, 4, 5, 6, 7, 8):
                    for start in range(max(0, rel_pos - wlen + 1), rel_pos + 1):
                        if start + wlen <= len(window) and start <= rel_pos < start + wlen:
                            if window[start:start + wlen] in words:
                                score += 1
                                break
                    else:
                        continue
                    break
            if score > best_score:
                best_score, best_mark = score, mark
        # ต้องยืนยันได้อย่างน้อยครึ่งหนึ่งของจุดที่เจอ ไม่งั้นไม่มั่นใจพอ ปล่อยอักขระเดิมไว้ดีกว่าเดาผิด
        if best_mark and best_score >= max(1, len(occurrences) // 2):
            mapping[ch] = best_mark

    if not mapping:
        return text
    pattern = re.compile("|".join(re.escape(ch) for ch in mapping))
    return pattern.sub(lambda m: mapping[m.group(0)], text)


def _normalize_sara_am(text):
    """
    รวม "นิคหิต + สระอา" (ํ ตามด้วย า, 2 อักขระแยกกัน) กลับเป็น "สระอำ" ตัวเดียว (ำ, U+0E33)
    PDF จำนวนมาก (พบใน SDS ตระกูล Sika/CPAC) แยกส่วนตัว ำ ออกเป็น 2 อักขระตอน extract ข้อความ
    ซึ่ง Python's unicodedata.normalize("NFC", ...) แก้ไม่ได้ เพราะสระอำไม่มี canonical
    decomposition mapping ในมาตรฐาน Unicode (ไม่ถือเป็นอักขระที่ "แยกส่วนได้ตามหลักเกณฑ์ปกติ")
    ต้องแก้เอง ไม่งั้น pattern ที่เขียนด้วย ำ ตัวเดียว (เช่น "คำสัญญาณ") จะจับคำในเอกสารที่ใช้แบบ
    แยกส่วนไม่ได้เลย (ำ พบบ่อยมากในภาษาไทย: คำ, น้ำ, จำนวน, ทำ, กำหนด ฯลฯ กระทบหลายฟิลด์พร้อมกัน)
    """
    return text.replace("ํา", "ำ")


def _fix_broken_sara_am_space(text):
    """
    PDF บางฉบับ (พบใน SDS ตระกูล Sigma-Aldrich/Merck) ฟอนต์เพี้ยนหนักกว่า _normalize_sara_am()
    ด้านบนอีกขั้น: ส่วนนิคหิต (ครึ่งบนของ ำ) หายไปทั้งหมดกลายเป็น "ช่องว่างเปล่าๆ" แทนที่จะเป็นอักขระ
    นิคหิตที่ยังพอจับคู่ได้ ผลคือ "ำ" ถูกดึงออกมาเป็น "พยัญชนะ + ช่องว่าง + า" (เช่น "ทำ" -> "ท า")
    แก้แบบไม่มีเงื่อนไข (ไม่ต้องตรวจสอบความถี่แบบวรรณยุกต์ใน _fix_broken_thai_glyphs) ได้อย่างปลอดภัย
    เพราะภาษาไทยไม่มีคำที่ "สระอา" (U+0E32) ลอยเดี่ยวๆ ตามหลังช่องว่างอยู่แล้ว (สระอาต้องติดพยัญชนะ
    เสมอ ไม่มีทางเป็นคำเริ่มต้นด้วยตัวเอง) เจอรูปแบบนี้ = ต้องเป็นความผิดพลาดจากการดึงข้อความเท่านั้น
    """
    return re.sub(r"([ก-ฮ][่้๊๋]?) า", r"\1ำ", text)


# บรรทัดท้ายสุดของแต่ละหน้า PDF หลายฉบับมี "หัว/ท้ายกระดาษ" ซ้ำทุกหน้า (เลขเอกสาร, เลขหน้า "3 / 11",
# "เอกสารข้อมูลความปลอดภัย", ชื่อสินค้า) ถ้าไม่ตัดออกก่อน จะไปต่อท้ายเนื้อหาจริงของฟิลด์ที่บังเอิญอยู่
# ท้ายหน้าพอดี (เช่น "กรณีหกรั่วไหล" ที่ยาวจนล้นไปหน้าถัดไป) กลายเป็นข้อความปนกันอ่านไม่รู้เรื่อง
# สังเกตได้จากรูปแบบ "รหัสเอกสาร ... เลขหน้า/เลขหน้ารวม ..." ซึ่งมักอยู่บรรทัดท้ายสุดของหน้าเสมอ
_FOOTER_LINE_RE = re.compile(r"^[\w.\-]{4,25}\s+\d{1,3}\s*/\s*\d{1,3}\b.*")


def _strip_page_footer(page_text, lookback=3):
    """ตัดหัว/ท้ายกระดาษออกจากท้ายหน้า - เช็คแค่ไม่กี่บรรทัดสุดท้าย (lookback) หาบรรทัดที่มีรูปแบบ
    เลขหน้า "N / M" ถ้าเจอ ตัดตั้งแต่บรรทัดนั้นเป็นต้นไปทั้งหมด (เผื่อบรรทัดถัดจากเลขหน้ายังเป็นส่วนของ
    หัว/ท้ายกระดาษต่อ เช่น ชื่อสินค้าที่ตัดขึ้นบรรทัดใหม่ ไม่ใช่ตัวเลขหน้าเอง)"""
    lines = page_text.split("\n")
    n = len(lines)
    for i in range(max(0, n - lookback), n):
        if _FOOTER_LINE_RE.match(lines[i].strip()):
            return "\n".join(lines[:i])
    return page_text


def read_all_text(pdf_path):
    """
    อ่านตัวอักษรทั้งหมดจากทุกหน้าของ PDF รวมเป็นก้อนเดียว (ตัดหัว/ท้ายกระดาษที่ซ้ำทุกหน้าออกก่อน
    แล้วแก้อักษรไทยที่เพี้ยนจากฟอนต์ ToUnicode ผิดพลาด)
    ใช้ PyMuPDF (fitz) แทน pdfplumber เดิม เพราะพบว่า SDS ภาษาไทยหลายฉบับ (Sika/CPAC/LANKO)
    ใช้ฟอนต์แบบที่ pdfplumber ดึงตัวอักษรออกมาผิดเพี้ยนหนักมาก (ได้ "(cid:1143)" ปนอยู่ในข้อความ
    แทบทุกคำ) ในขณะที่ fitz ดึงออกมาได้ถูกต้องเกือบทั้งหมด เหลือแค่สระ/วรรณยุกต์บางตัวที่ต้อง
    แก้เพิ่มด้วย _fix_broken_thai_glyphs() ทดสอบเทียบกับไฟล์ SDS ภาษาอังกฤษเดิมแล้วว่าไม่กระทบ
    """
    doc = fitz.open(pdf_path)
    pages = [page.get_text() or "" for page in doc]
    doc.close()

    # ต้องนับความถี่อักขระเพี้ยนจาก "ทั้งเอกสาร" ไม่ใช่ทีละหน้า เพราะบางหน้าอาจมีวรรณยุกต์ที่พบ
    # ไม่บ่อย (เช่น ไม้โท) น้อยกว่า _MIN_MARK_COUNT ทั้งที่รวมทั้งเอกสารแล้วเกินเกณฑ์สบายๆ ต่อ
    # หน้าด้วย sentinel ที่ไม่มีทางเจอในข้อความจริง เพื่อยังแยกกลับเป็นรายหน้าได้หลังแก้ตัวอักษร
    # (การแทนที่เป็นแบบ 1 ตัวอักษรต่อ 1 ตัวอักษรเสมอ ไม่เพิ่ม/ลบความยาว sentinel จึงไม่มีทางขยับ)
    sentinel = "\x00PAGEBREAK\x00"
    combined = _normalize_sara_am(sentinel.join(pages))
    combined = _fix_broken_sara_am_space(combined)
    combined = _fix_broken_thai_glyphs(combined)
    parts = [_strip_page_footer(p) for p in combined.split(sentinel)]
    return "\n".join(parts)



# วลีที่แปลว่า "ไม่มีข้อมูล" ในภาษาของ SDS ถ้าค่าที่ดึงได้ขึ้นต้นด้วยวลีพวกนี้ (ไม่สนตัวพิมพ์เล็ก-ใหญ่)
# ให้ถือว่าไม่มีข้อมูลจริงๆ คืนค่า "-" แทน ไม่ใช่ก็อปข้อความ "No data available" มาใส่ในฟอร์มตรงๆ
# (คำไทยเลือกเฉพาะวลีที่ชัดเจนว่า "ไม่มีข้อมูล" เท่านั้น ไม่ใช่ "ไม่มี" เฉยๆ เพราะ "ไม่มี" เดี่ยวๆ
# อาจเป็นคำตอบจริงที่มีความหมาย เช่น "กลิ่น: ไม่มี" หมายถึงไม่มีกลิ่น ไม่ใช่ไม่มีข้อมูล)
_NO_DATA_PREFIXES = (
    "no data available", "no data", "data not available", "no information available", "no information",
    "not applicable", "not determined", "not established", "no special", "not available",
    "void", "n/a", "na", "none",
    "ไม่มีข้อมูล", "ไม่พบข้อมูล", "ไม่มีข้อกำหนดพิเศษ", "ไม่ระบุ", "ไม่ได้กำหนด", "ไม่เกี่ยวข้อง",
)


def _clean(val):
    """
    รวมช่องว่าง/ขึ้นบรรทัดใหม่ภายในค่าให้เหลือแค่ช่องว่างเดียว (กันรอยหยักจากการ wrap หลายบรรทัด/ย่อหน้า)
    แล้วกรองค่าที่บอกว่า 'ไม่มีข้อมูล' (No data available, Not applicable, ฯลฯ) คืน None ถ้าใช้ไม่ได้
    """
    val = re.sub(r"\s+", " ", val).strip()
    low = val.lower().rstrip(".")
    if low in ("", "-"):
        return None
    if any(low.startswith(p) for p in _NO_DATA_PREFIXES):
        return None
    return val


def grab(text, patterns, default="-"):
    """
    หาข้อความตาม pattern (regex) หนึ่งอันหรือหลายอัน (list) ลองทีละอันตามลำดับ
    คืนค่าที่เจอครั้งแรก ถ้าไม่เจอเลยคืนค่า default
    """
    if isinstance(patterns, str):
        patterns = [patterns]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if m:
            val = _clean(m.group(1))
            if val is not None:
                return val
    return default


# ขอบเขตที่ควร "หยุดจับ" ค่าไว้ ใช้สร้าง pattern แบบมาตรฐานผ่าน line()/block() ด้านล่าง
# LINE: หยุดที่ช่องว่างยาว 2 ตัวขึ้นไป (มักหมายถึงคอลัมน์ถัดไปในตาราง เช่น "Product AT USE DILUTION")
#       หรือขึ้นบรรทัดใหม่ หรือจบข้อความ - ใช้กับค่าสั้นๆ บรรทัดเดียว (เช่น pH, สี, กลิ่น)
# BLOCK: หยุดที่บรรทัดว่าง, บรรทัดที่ขึ้นต้นด้วยสัญลักษณ์บูลเล็ต (· • เป็นหัวข้อย่อยใหม่ พบบ่อยมากใน SDS
#        หลายฉบับที่แต่ละหัวข้อมีแค่บรรทัดเดียวไม่มีบรรทัดว่างคั่น), บรรทัดที่ดูเหมือนหัวข้อใหม่ (ขึ้นต้นด้วย
#        ตัวใหญ่ภาษาอังกฤษ หรือตัวอักษรไทย แล้วตามด้วย ":") หรือจบข้อความ - ใช้กับค่าที่อาจยาวจน wrap
#        หลายบรรทัด (เช่น วิธีกำจัด) สำคัญ: ต้องรองรับ SDS ภาษาไทยด้วย เพราะภาษาไทยไม่มีตัวพิมพ์ใหญ่-เล็ก
#        แบบอังกฤษ [A-Z] จึงจับไม่ได้ ต้องเช็คช่วง unicode ไทย (฀-๿) แยกต่างหาก
_LINE_END = r"(?=\s{2,}|\n|\Z)"
_BLOCK_END = (
    r"(?=\n[ \t]*\n"
    r"|\n\s*[·•]"
    r"|\n\s*(?:SECTION|ส่วนที่|ส่วน)\s*\d+"
    # หัวข้อย่อยเปล่าๆ ที่อยู่บรรทัดของตัวเอง เช่น "1.3" หรือ "9.1" (พบใน SDS ตระกูล Sigma-Aldrich/
    # Merck ที่ไม่มี ":" คั่นระหว่างป้ายกับค่า) ถ้าไม่หยุดตรงนี้ การจับค่าของหัวข้อก่อนหน้าจะไหลข้าม
    # หัวข้อถัดไปไปเรื่อยๆ ไม่หยุด (ยึดครองข้อความของหลายหัวข้อรวดมาปนกัน) ต้องเช็คว่า "อยู่บรรทัดของ
    # ตัวเองล้วนๆ" เท่านั้น (ตามด้วยช่องว่าง/ขึ้นบรรทัดใหม่ทันที) กันไปชนตัวเลขที่เป็นค่าจริงโดยบังเอิญ
    # เช่น "1.31 ที่ 20 °C" (มีข้อความอื่นต่อท้ายในบรรทัดเดียวกัน จึงไม่เข้าเงื่อนไขนี้)
    r"|\n[ \t]*\d{1,2}\.\d{1,2}[ \t]*\n"
    r"|\n\s*[A-Z][A-Za-z][A-Za-z /,\-]{2,40}\s*:"
    r"|\n\s*[฀-๿][฀-๿ /]{0,40}\s*:"
    r"|\Z)"
)


def line(label):
    """สร้าง pattern จับค่าบรรทัดเดียวหลังป้าย `label` หยุดที่ช่องว่างยาว (คอลัมน์ถัดไป) หรือขึ้นบรรทัดใหม่"""
    return label + r"\s*:\s*([^\n]+?)" + _LINE_END


def block(label):
    r"""สร้าง pattern จับค่าที่อาจยาวหลายบรรทัดหลังป้าย `label` หยุดที่บรรทัดว่างหรือเจอป้ายใหม่
    SDS ตามมาตรฐาน EU CLP (พบใน Cassida/Momentive และแบรนด์ยุโรปอื่นๆ) มักมีป้ายย่อย "Product:"
    คั่นระหว่างหัวข้อกับคำตอบจริงเสมอ (หมายถึง "ข้อมูลนี้เป็นของทั้งผลิตภัณฑ์ ไม่ใช่รายสารเดี่ยว")
    บางฉบับเว้นบรรทัดว่างก่อนขึ้นคำตอบจริง ("...:\nProduct:\n\nProlonged or...") บางฉบับไม่เว้นเลย
    ("...:\nProduct:\nRemarks: Expected to be non-irritating...") ใช้ \s* เฉยๆ (ไม่บังคับ \n สองที)
    ครอบคลุมทั้งสองแบบ ถ้าไม่ข้าม "Product:" ไปก่อน _BLOCK_END จะไปหยุดที่บรรทัดว่าง/ป้ายถัดไปทันที
    ได้แค่คำว่า "Product:" มาเป็นค่าแทนที่จะเป็นคำตอบจริงด้านหลัง"""
    return label + r"\s*:\s*(?:Product:\s*)?(.+?)" + _BLOCK_END


def label_next_line(label):
    """สร้าง pattern จับ "คำตอบที่อยู่บรรทัดถัดไป" ของหัวข้อที่ไม่มี ":" คั่นระหว่างป้ายกับคำตอบ
    พบใน Section 11 ของ SDS ตระกูล Sika/CPAC/LANKO ที่เขียนหัวข้อการจำแนกความเป็นอันตรายแยกคนละ
    บรรทัดกับคำตอบ เช่น "การกัดกร่อน และการระคายเคืองต่อผิวหนัง\nไม่มีการจำแนกโดยขึ้นกับข้อมูลที่มีอยู่"
    จับแค่บรรทัดถัดไปบรรทัดเดียว (ไม่ใช่ block หลายบรรทัดแบบ block()) เพราะคำตอบประเภทนี้เป็นวลีสั้นๆ
    เสมอ ถ้าจับหลายบรรทัดจะกลืนหัวข้อถัดไปเข้ามาด้วย (ไม่มีป้าย ":" ให้ _BLOCK_END ใช้หยุด)
    บาง SDS (พบใน Whisper V ช่อง Flash point) ผสมทั้งสองแบบ: ป้ายอยู่บรรทัดของตัวเอง แต่บรรทัดค่า
    ขึ้นต้นด้วย ":" อีกที เช่น "Flash point \n: Not applicable..." ต้องกิน ":" ที่นำหน้าทิ้งไป ไม่งั้น
    จะติดมาเป็นส่วนหนึ่งของค่าที่จับได้ (":  Not applicable...")"""
    return label + r"\s*\n\s*:?\s*([^\n]+)"


def _extract_leading_number(raw):
    """ตัดข้อความบรรยายที่ห่อหุ้มตัวเลขออก เหลือแค่ตัวเลข/ช่วงตัวเลข เช่น
    "โดยประมาณ 9.0," -> "9.0", "6.0 - 8.0 (25 °C (77 °F))" -> "6.0 - 8.0"
    ใช้กับ pH ที่ผู้ใช้อยากได้แค่เลข ไม่เอาคำอธิบายก่อน/หลัง (เช่น "โดยประมาณ", อุณหภูมิที่วัด)
    คืนค่าเดิมถ้าไม่มีตัวเลขให้ตัด (เช่น "-" หรือไม่เจอเลย)"""
    if not raw or raw == "-":
        return raw
    m = re.search(r"[\d.]+\s*(?:[-–~]\s*[\d.]+)?", raw)
    return m.group(0).strip() if m else raw


def extract_section(text, section_num, next_section_num=None):
    """
    ตัดข้อความมาเฉพาะช่วง "SECTION N" (หรือ "ส่วนที่ N" สำหรับ SDS ภาษาไทย) จนถึงก่อนหัวข้อถัดไป
    (SDS มาตรฐาน 16 หัวข้อมักขึ้นต้นแบบนี้ทั้งฉบับอังกฤษและไทย)
    คืน None ถ้าหาหัวข้อนี้ไม่เจอ (ใช้บอกว่าควร fallback ไปค้นทั้งไฟล์แทน)

    สำคัญ: ทำแบบนี้เพราะหลาย section ใช้ป้ายย่อยชื่อเดียวกัน (เช่น "Eyes:"/"ทางตา" ทั้งใน Section 4
    (การปฐมพยาบาล) และ Section 11 (พิษวิทยา) ความหมายคนละเรื่องกัน ถ้าค้นทั้งไฟล์เฉยๆ อาจไปจับข้อความ
    จาก section ผิดมาใส่ผิดช่องได้

    หมายเหตุ: SDS ตระกูล Sika/CPAC/LANKO/Kemox (ทดสอบจริงแล้วทั้ง 7 ไฟล์) ไม่ได้ขึ้นต้นด้วย "SECTION"
    หรือ "ส่วนที่" เลย แต่ใช้แค่ "3. องค์ประกอบและข้อมูลเกี่ยวกับส่วนผสม" (เลขหัวข้อ+จุด ต้นบรรทัด)
    เดิมไม่รองรับรูปแบบนี้เลย ทำให้ extract_section คืน None เสมอสำหรับไฟล์กลุ่มนี้ - เพิ่ม
    "^N\\." ต้นบรรทัดเป็นอีกทางเลือกหนึ่ง (ต้องอยู่ต้นบรรทัดเท่านั้น กัน false positive จากตัวเลข
    ลำดับขั้นตอนในเนื้อหา เช่น "1. ถอดคอนแทคเลนส์" ที่มักมีข้อความอื่นนำหน้าในบรรทัดเดียวกัน)

    หมายเหตุเพิ่ม: ไฟล์ตระกูลเดียวกันนี้บางฉบับ (เช่น LANKO 101/102/103/107) ใช้ "8," (จุลภาค)
    แทน "8." (จุด) เฉพาะที่ Section 8 - พิมพ์ไม่สม่ำเสมอกันเองในเอกสารชุดเดียวกัน จึงรับทั้ง . และ ,
    """
    next_num = next_section_num or (section_num + 1)
    # SDS ตระกูล Sigma-Aldrich/Merck ใช้ "ส่วน N:" (ไม่มี "ที่") แยกต่างหากจาก "ส่วนที่ N" ที่เจอในตระกูล
    # อื่น - "ส่วน" เป็นคำย่อยของ "ส่วนที่" อยู่แล้ว จึงเพิ่มเป็นอีกทางเลือกได้โดยไม่ชนกับของเดิม (ถ้าเอกสาร
    # เขียน "ส่วนที่ 4" ตัว "ส่วน" เฉยๆ จะแมตช์ไม่ได้ต่อด้วย \s*{section_num} เพราะตามด้วย "ที่" ไม่ใช่ตัวเลข)
    # SDS ตระกูลยุโรป (พบใน Interflon) ใช้ "หมวดที่ N:" แทน "ส่วนที่ N"/"ส่วน N" - เพิ่มเป็นอีกคำพ้องความหมาย
    # ต้องยึดต้นบรรทัดด้วย (?:^|\n)\s* เสมอ กันไปแมตช์ประโยคอ้างอิงกลางข้อความ เช่น "...ดูหมวดที่ 16."
    # (พบบ่อยมากใน SDS จริง อ้างอิงข้ามไปหัวข้ออื่น ไม่ใช่หัวข้อของ section นั้นเอง) ซึ่งจะทำให้ extract
    # ได้ช่วงข้อความผิดจุดไปเลย (ตัดสั้นเกินไปหรือเริ่มผิดที่)
    start = rf"(?:(?:^|\n)\s*(?:SECTION|ส่วนที่|ส่วน|หมวดที่)\s*{section_num}\b|^\s*{section_num}[.,]\s)"
    end = rf"(?:(?:^|\n)\s*(?:SECTION|ส่วนที่|ส่วน|หมวดที่)\s*{next_num}\b|^\s*{next_num}[.,]\s)"
    pattern = rf"{start}.*?(?={end}|\Z)"
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL | re.MULTILINE)
    return m.group(0) if m else None


# แถวในตารางส่วนประกอบ (Section 3) รูปแบบ "ชื่อสาร   เลข CAS   ความเข้มข้น (%)" คั่นด้วยช่องว่างยาว
# เช่น "Phosphoric acid          7664-38-2      30 - 60"
# ใช้ "[ \t]{2,}" แทน "\s{2,}" เพราะ \s รวม \n ด้วย ถ้าใช้ \s จะจับข้ามบรรทัดได้โดยไม่ได้ตั้งใจ
# (พบจริง: ไปจับตารางแบบ "แนวตั้ง" ที่แต่ละคอลัมน์อยู่คนละบรรทัดปนมาด้วยบางส่วน ทำให้บางแถวถูกข้าม
# เพราะ finditer เจอ "แถวปลอม" ก่อนแล้วเลื่อนตำแหน่งค้นหาผ่านแถวจริงไป) ต้องบังคับให้อยู่บรรทัดเดียวกันจริงๆ
_CAS_TABLE_ROW = re.compile(
    r"^(?P<name>.{2,60}?)[ \t]{2,}(?P<cas>\d{2,7}-\d{2}-\d)[ \t]{2,}(?P<conc>[\d.]+(?:\s*-\s*[\d.]+)?)\s*%?[ \t]*$",
    re.MULTILINE,
)

# ตารางส่วนผสมอีกแบบ (พบจริงในไฟล์ Sika/CPAC/LANKO/Kemox ทุกไฟล์ที่ทดสอบ) extract ออกมาเป็น
# "หัวตารางทั้งหมดก่อน แล้วค่าจริงตามมาทีละแถว คนละบรรทัด" แทนที่จะเป็นบรรทัดเดียวคั่นด้วยช่องว่างยาว
# แบบ _CAS_TABLE_ROW เช่น
#     ชื่อทางเคมี
#     หมายเลข CAS
#     ความเข้มข้น (%)
#     Limestone
#     1317-65-3
#     >= 30 - < 50
#     Distillates (petroleum), hydrotreated heavy naph-
#     thenic; Baseoil - unspecified
#     64742-52-5
#     >= 0.1 - < 1
# _CAS_TABLE_ROW จับรูปแบบนี้ไม่ได้เลย (ไม่มีช่องว่างยาวคั่นในบรรทัดเดียวกัน) ทำให้ cas/ingredient_name
# ออกมาเป็น "-" ทั้งที่จริงมีเลข CAS อยู่ในเอกสาร ต้องแยก parser อีกแบบสำหรับโครงสร้างนี้โดยเฉพาะ
_VERTICAL_CAS_LINE_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")
_VERTICAL_NO_CAS_RE = re.compile(r"^(?:not available|n\/?a|ไม่มีข้อมูล|-)$", re.IGNORECASE)
# รองรับทั้ง ">= 30 - < 50" (ไทย) และ "<= 100" (อังกฤษ, พบใน SDS ที่มีสารตัวเดียวไม่เกิน 100%)
_VERTICAL_CONC_LINE_RE = re.compile(r"^(?:[<>]=?\s*)?[\d.]+\s*(?:[-–]\s*[<>]?=?\s*[\d.]+)?\s*%?$")


def _parse_vertical_composition_rows(section3_text):
    """คืน list ของ (name, cas_or_None, score) จากตารางส่วนผสมแบบ "แนวตั้ง" (ดูตัวอย่างด้านบน)
    ไล่บรรทัดหลังหัวตาราง "ความเข้มข้น" ไปเรื่อยๆ ทุกครั้งที่เจอบรรทัดหน้าตาเป็นเลข CAS (หรือ
    "Not available"/"ไม่มีข้อมูล" สำหรับสารที่ไม่มีเลข CAS จริง เช่นพอลิเมอร์) ให้ถือว่าบรรทัดก่อนหน้า
    ที่สะสมมา (อาจมีได้หลายบรรทัดถ้าชื่อสารยาวจนตัดบรรทัด) คือชื่อสาร และบรรทัดถัดไปคือความเข้มข้น
    """
    idx = section3_text.find("ความเข้มข้น")
    if idx == -1:
        idx = section3_text.lower().find("concentration")  # SDS ภาษาอังกฤษที่ใช้ตารางแนวตั้งแบบนี้
    if idx == -1:
        return []
    lines = [l.strip() for l in section3_text[idx:].split("\n") if l.strip()]
    rows = []
    name_buffer = []
    i = 1  # ข้ามบรรทัดหัวตาราง "ความเข้มข้น (%)" เอง (index 0)
    while i < len(lines):
        line = lines[i]
        is_cas = _VERTICAL_CAS_LINE_RE.match(line)
        is_no_cas = _VERTICAL_NO_CAS_RE.match(line)
        if is_cas or is_no_cas:
            name = " ".join(name_buffer).strip(" -")
            name_buffer = []
            if i + 1 < len(lines) and _VERTICAL_CONC_LINE_RE.match(lines[i + 1]):
                nums = [float(x) for x in re.findall(r"[\d.]+", lines[i + 1])]
                if name and nums:
                    rows.append((name, line if is_cas else None, max(nums)))
                i += 1
        else:
            name_buffer.append(line)
            if len(name_buffer) > 3:  # กันชื่อยาวผิดปกติ (แปลว่าหลุดจากแถวจริงแล้ว)
                name_buffer.pop(0)
        i += 1
    return rows


def _parse_composition_rows(text):
    """คืน list ของ (name, cas_or_None, score) ทุกแถวในตาราง Section 3 ลองรูปแบบตารางแนวนอน
    (_CAS_TABLE_ROW) ก่อน ถ้าไม่เจอเลยลองแบบแนวตั้ง (_parse_vertical_composition_rows) เพราะ SDS
    แต่ละไฟล์ extract ตารางออกมาคนละโครงสร้างกัน แล้วแต่ฟอนต์/เครื่องมือสร้าง PDF ต้นทาง"""
    section3 = extract_section(text, 3)
    if not section3:
        return []
    rows = []
    for m in _CAS_TABLE_ROW.finditer(section3):
        nums = [float(x) for x in re.findall(r"[\d.]+", m.group("conc"))]
        if not nums:
            continue
        name = _clean(m.group("name")) or m.group("name").strip()
        if name:
            rows.append((name, m.group("cas"), max(nums)))
    if rows:
        return rows
    return _parse_vertical_composition_rows(section3)


def parse_cas_from_composition_table(text):
    """
    หาเลข CAS จากตาราง "ส่วนประกอบ" ใน Section 3 (Composition/Information on Ingredients)
    ถ้ามีสารเคมีหลายตัวในตาราง (เป็นส่วนผสม) เลือกเลข CAS ของสารที่มี "Concentration (%)" สูงสุด
    (ใช้ค่าบนสุดของช่วง เช่น "30 - 60" ใช้ 60) เพราะถือเป็นสารหลักของผลิตภัณฑ์
    คืน None ถ้าหาตารางแบบนี้ไม่เจอ หรือสารหลักไม่มีเลข CAS จริง (เช่นพอลิเมอร์) - ให้ผู้เรียก
    fallback ไปใช้ pattern "CAS No:" ปกติแทน
    """
    best_score, best_cas = None, None
    for name, cas, score in _parse_composition_rows(text):
        if best_score is None or score > best_score:
            best_score, best_cas = score, cas
    return best_cas


def parse_ingredient_name_from_composition_table(text):
    """
    หาชื่อสารเคมีของแถวที่มี Concentration (%) สูงสุดในตาราง Section 3 (แถวเดียวกับที่
    parse_cas_from_composition_table() เลือก CAS มา - ใช้ตรรกะ "สูงสุด" แบบเดียวกันเป๊ะ เพื่อให้
    ชื่อกับ CAS ที่แสดงคู่กันบนฟอร์มเป็นสารตัวเดียวกันเสมอ ไม่ใช่คนละแถวโดยไม่ได้ตั้งใจ)
    คืน None ถ้าหาตารางแบบนี้ไม่เจอ (SDS สารเดี่ยว ไม่มีตารางส่วนผสม)
    """
    best_score, best_name = None, None
    for name, cas, score in _parse_composition_rows(text):
        if best_score is None or score > best_score:
            best_score, best_name = score, name
    return best_name


def extract_hazardous_substances(text):
    """
    ดึงชื่อสารเคมีอันตราย (ไม่ใช่แค่เลข CAS) จากตาราง "ส่วนประกอบ" ใน Section 3 - ใช้สำหรับบรรทัด
    "Contains: ..." บนฉลากภาชนะบรรจุ (1 ในองค์ประกอบที่ GHS กำหนดให้มีบนฉลาก) เรียงจาก Concentration (%)
    สูงสุดไปต่ำสุด คืน [] ถ้าหาตารางแบบนี้ไม่เจอ (SDS สารเดี่ยว ไม่มีตารางส่วนผสม)
    """
    rows = sorted(_parse_composition_rows(text), key=lambda r: r[2], reverse=True)
    seen = []
    for name, cas, score in rows:
        if name not in seen:
            seen.append(name)
    return seen


def grab_near(text, keyword, patterns, window=300):
    """
    หาค่าเฉพาะในช่วงข้อความ `window` ตัวอักษรถัดจากคำว่า `keyword` (ไม่ใช่ค้นทั้งไฟล์)
    ใช้กับข้อมูลที่คำค้นหาสั้นและกำกวม (เช่น "Health") ซึ่งอาจไปเจอที่อื่นในเอกสารที่ไม่เกี่ยวข้อง
    คืน None ถ้าไม่เจอ keyword เลย หรือหาในช่วงนั้นไม่เจอ (ให้ผู้เรียก fallback เอง)
    """
    idx = text.lower().find(keyword.lower())
    if idx == -1:
        return None
    return grab(text[idx: idx + window], patterns, default=None)


def grab_scoped(full_text, section_text, patterns, default="-"):
    """
    หาค่าใน section_text (ข้อความเฉพาะ section ที่เกี่ยวข้อง) เท่านั้นถ้าหา section เจอ (ไม่ fallback
    ไปทั้งไฟล์ต่อ แม้จะหาในนั้นไม่เจอก็ตาม) fallback ไปหาทั้งไฟล์แทนเฉพาะกรณีหา section ไม่เจอเลยเท่านั้น
    (extract_section คืน None เช่น SDS ที่ไม่มีหัว "SECTION N" ชัดเจน)

    เดิมเคย fallback ไปทั้งไฟล์ทุกครั้งที่หาในช่วง section ไม่เจอ (ไม่ว่าจะหา section เจอหรือไม่) แต่ทำให้
    เกิดปัญหาจริง: SDS ตระกูล Sigma-Aldrich/Merck ที่หา Section 11 เจอถูกต้องแล้ว แต่ field บางตัว (เช่น
    hz_eye) ไม่มีป้ายที่ตรงกับ pattern อยู่ใน Section 11 จริงๆ ระบบเลยเผลอ fallback ไปค้นทั้งไฟล์ แล้วดัน
    ไปเจอป้ายชื่อเดียวกัน ("เมื่อเข้าตา:") ที่อยู่ใน Section 4 (การปฐมพยาบาล) แทน ทำให้ได้คำตอบปฐมพยาบาล
    มาใส่ในช่องพิษวิทยาผิดที่ไปเลย - พอหา section เจอแล้ว ควรเชื่อขอบเขตนั้นเด็ดขาด ถ้าไม่เจอค่อยคืน default
    """
    if section_text:
        return grab(section_text, patterns, default=default)
    return grab(full_text, patterns, default=default)


# ตัวเลข LD50/LC50 ดิบ (ต้องได้รับสารเท่าไรถึงเป็นพิษ) ไม่ใช่สิ่งที่ผู้ใช้อยากเห็นในช่อง "อันตรายต่อ
# สุขภาพ" (hz_eye/hz_skin/hz_oral/hz_inhale) - อยากรู้ว่า "อันตรายแบบไหน" มากกว่า ใช้กรองค่าที่ขึ้นต้น
# ด้วย LD50/LC50 ทิ้ง แล้วลองแพทเทิร์นถัดไปแทน (บาง SDS เช่นตระกูล Sigma-Aldrich มีแค่หัวข้อ "ความเป็น
# พิษเฉียบพลัน" ตามด้วยตัวเลข LD50 ทันทีโดยไม่มีคำอธิบายแยกต่างหาก ถ้าไม่กรองจะได้ตัวเลขดิบมาแทน)
_LD50_LC50_RE = re.compile(r"^(?:LD|LC)\s*50\b", re.IGNORECASE)


def grab_no_ld50(text, patterns, default="-"):
    """เหมือน grab() แต่ถือว่าค่าที่ขึ้นต้นด้วย LD50/LC50 เป็น "ไม่เจอ" แล้วลองแพทเทิร์นถัดไปต่อ"""
    if isinstance(patterns, str):
        patterns = [patterns]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if m:
            val = _clean(m.group(1))
            if val is not None and not _LD50_LC50_RE.match(val):
                return val
    return default


def grab_scoped_no_ld50(full_text, section_text, patterns, default="-"):
    """เหมือน grab_scoped() (รวมพฤติกรรม "หา section เจอแล้วไม่ fallback ต่อ" ด้วย) แต่ใช้
    grab_no_ld50() แทน grab() (ดูเหตุผลใน grab_no_ld50)"""
    if section_text:
        return grab_no_ld50(section_text, patterns, default=default)
    return grab_no_ld50(full_text, patterns, default=default)


# รายชื่อฟิลด์ทั้งหมดที่ระบบต้องใช้ (ตรงกับช่องใน Template.pdf / Template.xlsx)
FIELD_KEYS = [
    "display_name", "signal_word",
    "trade_name", "formula", "un", "cas", "usage",
    "state", "color", "odor", "boiling", "ph", "flash",
    "fa_eye", "fa_oral", "fa_skin", "fa_inhale",
    "hz_eye", "hz_skin", "hz_oral", "hz_inhale",
    "fire", "reactivity", "spill", "disposal", "storage",
    "nfpa_health", "nfpa_fire", "nfpa_react",
    "pictograms",
]

# คำ/รหัสที่มักเจอใน Section 2 (Hazards identification) ของ SDS ที่บ่งบอกว่าควรมีสัญลักษณ์ GHS ชนิดนั้น
# ใช้เดาเบื้องต้นให้เท่านั้น ผู้ใช้ต้องตรวจ/ติ๊กเลือกเองในฟอร์มอีกครั้งก่อนสร้าง PDF เสมอ
PICTOGRAM_KEYWORDS = {
    "explosive": [r"\bGHS0?1\b", r"\bexplosive\b", r"self-?reactive", r"วัตถุระเบิด", r"สารระเบิด"],
    "flammable": [r"\bGHS0?2\b", r"\bflammable\b", r"\bpyrophoric\b", r"สารไวไฟ", r"ไวไฟ"],
    "oxidizer": [r"\bGHS0?3\b", r"\boxidi[sz]ing\b", r"\boxidi[sz]er\b", r"สารออกซิไดซ์", r"ออกซิไดซ์"],
    "gas_cylinder": [r"\bGHS0?4\b", r"gas(?:es)? under pressure", r"compressed gas", r"ก๊าซภายใต้ความดัน"],
    "corrosive": [r"\bGHS0?5\b", r"skin corrosion", r"\bcorrosive\b", r"serious eye damage",
                  r"สารกัดกร่อน", r"กัดกร่อน"],
    "toxic": [r"\bGHS0?6\b", r"acute toxicity", r"\bfatal if\b", r"\btoxic if\b", r"พิษเฉียบพลัน", r"เป็นพิษ"],
    "irritant": [r"\bGHS0?7\b", r"skin irritation", r"eye irritation", r"\birritant\b",
                 r"สารระคายเคือง", r"ระคายเคือง"],
    "health_hazard": [r"\bGHS0?8\b", r"carcinogen", r"respiratory sensiti[sz]", r"reproductive toxicity",
                       r"specific target organ", r"aspiration hazard", r"สารก่อมะเร็ง", r"อันตรายต่อสุขภาพ"],
    "environment": [r"\bGHS0?9\b", r"aquatic (?:acute|chronic)", r"hazardous to the aquatic environment",
                     r"อันตรายต่อสิ่งแวดล้อม", r"เป็นพิษต่อสิ่งมีชีวิตในน้ำ"],
}


# รหัส Hazard Statement (H-code, เช่น H226) / Precautionary Statement (P-code, เช่น P210)
# ต้องขึ้นต้นบรรทัด (^) เท่านั้น (ใช้ .match() ไม่ใช่ .search()) เพราะ SDS จำนวนมากจัดหน้าเป็นตาราง
# "รหัส ... ข้อความ" คนละคอลัมน์แต่อยู่แถวเดียวกัน (บรรทัดเดียวกันหลัง pdfplumber ดึงข้อความออกมา)
# ถ้าข้อความยาวจน wrap ขึ้นบรรทัดใหม่ (เช่น P305+P351+P338 ที่มีประโยคยาว) ต้องรวมบรรทัดถัดไปเข้ามาด้วย
# จนกว่าจะเจอรหัสใหม่/หัวข้อย่อยใหม่ (เช่น "Precautionary statements - response")/บรรทัดว่าง
_H_CODE_LINE = re.compile(r"^H[2-4]\d{2}\b.*")
_P_CODE_LINE = re.compile(r"^P[1-5]\d{2}(?:\s*[/+]\s*P?[1-5]?\d{0,3})*\b.*")
# หัวข้อย่อยที่พบบ่อยใน Section 2.2 (Label elements) - เจอแล้วให้หยุดต่อบรรทัด ไม่งั้นจะเอาหัวข้อ
# ("Precautionary statements - storage") มาต่อท้ายข้อความก่อนหน้าโดยไม่ได้ตั้งใจ
_LABEL_SUBHEADING = re.compile(
    r"^(?:hazard statements?|precautionary statements?(?:\s*-\s*\w+)?|"
    r"supplemental hazard information|signal word|pictograms?|"
    r"labell?ing according to|classification)\s*:?\s*$",
    re.IGNORECASE,
)


def _extract_code_lines(text, pattern):
    """หาแต่ละข้อความที่ขึ้นต้นด้วยรหัส H/P-code ใน Section 2 ก่อน (fallback ทั้งไฟล์ถ้าหา section ไม่เจอ)
    ถ้าข้อความ wrap ต่อในบรรทัดถัดไป (ไม่ใช่รหัสใหม่/หัวข้อย่อย/บรรทัดว่าง) จะรวมเข้าเป็นข้อความเดียวกัน
    คืน list เรียงตามที่เจอ ไม่เอาข้อความซ้ำ"""
    section2 = extract_section(text, 2) or text
    entries = []
    current_idx = None
    for raw_line in section2.split("\n"):
        stripped = raw_line.strip()
        if not stripped:
            current_idx = None
            continue
        if pattern.match(stripped):
            entries.append(stripped)
            current_idx = len(entries) - 1
            continue
        if _LABEL_SUBHEADING.match(stripped):
            current_idx = None
            continue
        if current_idx is not None:
            entries[current_idx] += " " + stripped
    seen = []
    for entry in entries:
        val = _clean(entry)
        if val and val not in seen:
            seen.append(val)
    return seen


# SDS บางฉบับไม่ได้เขียนรหัส H/P-code กำกับเลย (เช่น "Hazard Statements : Harmful if swallowed.
# Causes severe skin burns and eye damage.") เขียนเป็นประโยคบรรยายล้วนๆ ใต้ป้าย "Hazard Statements"/
# "Precautionary Statements" ตรงๆ ใช้เป็น fallback ตอนหารหัส H/P ไม่เจอเลย
# ป้ายกลุ่มย่อยของ Precautionary (Prevention/Response/Storage/Disposal) ไม่นับเป็นเนื้อหา ใช้แค่แบ่งจุด
_GROUP_HEADER_RE = re.compile(r"\b(Prevention|Response|Storage|Disposal)\s*:\s*", re.IGNORECASE)
# ประโยคแบบ "IF SWALLOWED:"/"IF ON SKIN (or hair):"/"IF INHALED:"/"IF IN EYES:" คือรูปแบบมาตรฐานของ
# Precautionary (Response) ที่มักมีหลายประโยคย่อยต่อกันเป็นข้อความเดียว (ไม่แยกประโยคซ้ำในกลุ่มนี้)
_IF_STATEMENT_RE = re.compile(r"\s*(?=\bIF\s+[A-Z][A-Za-z/() ]*:)")

# ขอบเขตหยุดจับสำหรับดึงก้อนข้อความ Hazard/Precautionary Statements ทั้งบล็อก (คนละอันกับ _BLOCK_END
# ทั่วไป) เพราะป้ายกลุ่มย่อยของ Precautionary เอง (Prevention:/Response:/Storage:/Disposal:) ต้อง "ไม่"
# ถูกนับเป็นป้ายฟิลด์ใหม่แล้วหยุดจับ (มิฉะนั้นจะตัดข้อความ Response ทิ้งไปทั้งหมด) ใช้ negative lookahead
# กันไว้เฉพาะ 4 คำนี้ ป้ายฟิลด์อื่นๆ ที่แท้จริง (เช่น "Classification:", "CAS No:") ยังหยุดจับตามปกติ
_STATEMENT_BLOCK_END = (
    r"(?=\n[ \t]*\n"
    r"|\n\s*[·•]"
    r"|\n\s*(?:SECTION|ส่วนที่)\s*\d+"
    r"|\n\s*(?!(?:Prevention|Response|Storage|Disposal)\s*:|IF\s+[A-Z])[A-Z][A-Za-z][A-Za-z /,\-]{2,40}\s*:"
    r"|\n\s*[฀-๿][฀-๿ /]{0,40}\s*:"
    r"|\Z)"
)


def _raw_statement_block(text, label):
    """เหมือน _raw_block แต่ใช้ _STATEMENT_BLOCK_END (ไม่หยุดจับที่ป้ายกลุ่มย่อย Prevention/Response/...)
    สำหรับดึงก้อนข้อความ Hazard/Precautionary Statements ทั้งบล็อกโดยเฉพาะ"""
    pattern = label + r"\s*:\s*(.+?)" + _STATEMENT_BLOCK_END
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return m.group(1) if m else None


def _fallback_statements(raw_block):
    """
    แยกข้อความ Hazard/Precautionary statement ที่ไม่มีรหัส H/P-code กำกับ ออกเป็นรายการทีละข้อ
    (ใช้ตอน _extract_code_lines หารหัสไม่เจอเลย) หลักการ:
      1. รวมทุกบรรทัดเป็นข้อความเดียวก่อน (ของเดิมตัดบรรทัดตามความกว้างหน้ากระดาษ ไม่ใช่ตามประโยคจริง)
      2. ตัดป้ายกลุ่มย่อย (Prevention/Response/...) ออก ใช้เป็นจุดแบ่งเฉยๆ
      3. แบ่งก่อนแต่ละ "IF ...:" (พบบ่อยใน Precautionary - Response) เป็นข้อความใหม่
      4. ข้อความกลุ่ม "IF ...:" เก็บทั้งกลุ่มเป็นข้อเดียว (มาตรฐาน GHS มักรวมหลายประโยคเป็นข้อควรระวัง
         ข้อเดียว เช่น "IF SWALLOWED: Rinse mouth. Do NOT induce vomiting.") ข้อความอื่นแยกทีละประโยค
         ด้วยจุด+ตัวใหญ่ถัดไป (Hazard statement/Precautionary - prevention มักเป็นประโยคสั้นแยกข้อกัน)
    """
    if not raw_block:
        return []
    flat = re.sub(r"\s+", " ", raw_block).strip()
    flat = _GROUP_HEADER_RE.sub("\x00", flat)
    flat = _IF_STATEMENT_RE.sub("\x00", flat)
    chunks = [c.strip() for c in flat.split("\x00") if c.strip()]
    results = []
    for chunk in chunks:
        if re.match(r"^IF\s+[A-Z]", chunk):
            pieces = [chunk]
        else:
            pieces = re.split(r"(?<=[.])\s+(?=[A-Z])", chunk)
        for piece in pieces:
            val = _clean(piece)
            if val and val not in results:
                results.append(val)
    return results


# จัดลำดับ Hazard Statement ให้ "อันตรายต่อคน" ขึ้นก่อนเสมอ (ผู้ใช้ระบุว่าฉลากต้องเน้นอันตราย
# ต่อคนที่หยิบจับ/ใช้งานสารเคมีเป็นอย่างแรก ไม่ใช่เรียงตามลำดับที่เจอในเอกสารเฉยๆ) อิงจากเลขหมวดของ
# GHS H-code: H3xx = อันตรายต่อสุขภาพ (พิษ ระคายเคือง กัดกร่อน ฯลฯ - กระทบคนโดยตรงที่สุด) มาก่อน
# H2xx = อันตรายทางกายภาพ (ไวไฟ ระเบิด ฯลฯ - อันตรายต่อคนเหมือนกันแต่ทางอ้อมกว่า) มาเป็นอันดับสอง
# H4xx = อันตรายต่อสิ่งแวดล้อม (เช่น พิษต่อสิ่งมีชีวิตในน้ำ - ไม่ใช่อันตรายต่อคนโดยตรง) มาท้ายสุด
# ใช้ stable sort เก็บลำดับเดิมไว้ภายในกลุ่มเดียวกัน (ไม่สลับลำดับ H3xx ด้วยกันเองโดยไม่จำเป็น)
def _hazard_rank(entry):
    m = re.match(r"H([234])\d{2}", entry)
    if not m:
        return 1  # ไม่รู้หมวด (เช่น fallback ไม่มีรหัส) ให้อยู่กลางๆ ไม่เอนเอียงไปทางใด
    return {"3": 0, "2": 1, "4": 2}[m.group(1)]


def extract_hazard_statements(text):
    """ดึงรายการ Hazard Statement พร้อมข้อความ เช่น ['H226 Flammable liquid and vapour.']
    เรียงลำดับใหม่ให้อันตรายต่อคน (H3xx สุขภาพ, H2xx กายภาพ) ขึ้นก่อนอันตรายต่อสิ่งแวดล้อม (H4xx)
    เสมอ (ดู _hazard_rank) ถ้าไม่มีรหัส H-code กำกับเลย (SDS บางฉบับเขียนบรรยายเฉยๆ) fallback ไปแยก
    ประโยคจากป้าย "Hazard Statements" ตรงๆ แทน (กรณีนี้ไม่มีรหัสให้จัดลำดับ คงลำดับเดิมในเอกสาร)"""
    coded = _extract_code_lines(text, _H_CODE_LINE)
    if coded:
        return sorted(coded, key=_hazard_rank)
    section2 = extract_section(text, 2) or text
    raw = _raw_statement_block(section2, r"Hazard [Ss]tatements?")
    return _fallback_statements(raw) if raw else []


def extract_precautionary_statements(text):
    """ดึงรายการ Precautionary Statement พร้อมข้อความ เช่น ['P210 Keep away from heat.']
    ถ้าไม่มีรหัส P-code กำกับเลย fallback ไปแยกประโยคจากป้าย "Precautionary Statements" ตรงๆ แทน"""
    coded = _extract_code_lines(text, _P_CODE_LINE)
    if coded:
        return coded
    section2 = extract_section(text, 2) or text
    raw = _raw_statement_block(section2, r"Precautionary [Ss]tatements?")
    return _fallback_statements(raw) if raw else []


def _raw_block(text, label):
    """เหมือน block() แต่คืนข้อความดิบ (ไม่ผ่าน _clean ที่รวมทุกบรรทัดเป็นบรรทัดเดียว)
    ใช้ตอนต้องแยกบรรทัดเอง เช่น แยกชื่อบริษัท/ที่อยู่ที่อยู่ในก้อนเดียวกันแต่คนละบรรทัด"""
    pattern = label + r"\s*:\s*(.+?)" + _BLOCK_END
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return m.group(1) if m else None


def extract_supplier_info(text):
    """ดึงข้อมูลผู้ผลิต/ผู้จำหน่าย + เบอร์โทรฉุกเฉิน จาก Section 1 (สำหรับฉลากภาชนะบรรจุ)"""
    section1 = extract_section(text, 1) or text
    name = grab(section1, [
        line(r"Company"), line(r"Company [Nn]ame"),
        line(r"Supplier"), line(r"Manufacturer"),
        line(r"ชื่อบริษัท"), line(r"ผู้ผลิต"), line(r"ผู้จำหน่าย"),
        line(r"บริษัท"),  # ป้ายสั้นๆ แค่ "บริษัท" เจอใน SDS ตระกูล Sika/CPAC/LANKO ไว้ลองท้ายสุด
    ])
    address = grab(section1, [
        block(r"Address"), block(r"Street [Aa]ddress"),
        block(r"ที่อยู่"),
    ])
    # SDS หลายฉบับไม่มีป้าย "Address:" แยกต่างหาก แต่ที่อยู่ต่อท้ายชื่อบริษัททันทีคนละบรรทัด
    # ใต้ป้าย "Manufacturer/Supplier:" เดียวกัน เลยลองแยกบรรทัดเอาบรรทัดแรกเป็นชื่อ ที่เหลือเป็นที่อยู่
    if name == "-" or address == "-":
        raw = (_raw_block(section1, r"Manufacturer\s*/\s*Supplier")
               or _raw_block(section1, r"Supplier")
               or _raw_block(section1, r"Manufacturer")
               or _raw_block(section1, r"ผู้ผลิต")
               or _raw_block(section1, r"ผู้จำหน่าย")
               or _raw_block(section1, r"บริษัท"))
        if raw:
            lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
            if name == "-" and lines:
                name = lines[0]
            if address == "-" and len(lines) > 1:
                address = " ".join(lines[1:])
    return {
        "supplier_name": name,
        "supplier_address": address,
        "emergency_phone": grab(section1, [
            line(r"Emergency [Tt]elephone(?: [Nn]umber)?"),
            line(r"Emergency [Pp]hone"),
            line(r"เบอร์โทรฉุกเฉิน"), line(r"โทรศัพท์ฉุกเฉิน"),
        ]),
    }


def detect_pictograms(text):
    """
    เดาสัญลักษณ์ GHS ที่น่าจะเกี่ยวข้อง จากคำ/รหัสที่เจอใน SDS Section 2 (Hazard(s) identification)
    เท่านั้น คืน list ของ key ตามลำดับใน PICTOGRAM_KEYWORDS แค่ที่เจอ pattern จริง

    เดิมค้นหาทั้งไฟล์ ทำให้เกิด false positive จริง: SDS ที่ไม่มีอันตรายเลย (เช่น "This chemical is
    not considered hazardous... The product contains no substances which... are considered to be
    hazardous") กลับถูกติ๊กสัญลักษณ์อันตรายหลายตัว เพราะคำอย่าง "corrosive"/"toxic"/"oxidizer" มักโผล่
    ใน Section 9-11 (คุณสมบัติทางเคมี/พิษวิทยา/ความเข้ากันไม่ได้กับสารอื่น) แม้ตัวสารเองไม่อันตราย
    ก็ตาม จำกัดขอบเขตแค่ Section 2 (fallback ทั้งไฟล์เฉพาะกรณีหา Section 2 ไม่เจอเลย) เหมือนฟังก์ชัน
    ตรวจจับอื่นๆ ในไฟล์นี้ (เช่น extract_hazard_statements, extract_precautionary_statements) แก้ปัญหา
    นี้ได้ เพราะเรื่องนี้กระทบความปลอดภัยจริง (ติ๊กสัญลักษณ์อันตรายผิดบนฉลากหน้างาน)
    """
    section2 = extract_section(text, 2) or text
    found = []
    for key, patterns in PICTOGRAM_KEYWORDS.items():
        if any(re.search(p, section2, re.IGNORECASE) for p in patterns):
            found.append(key)
    return found


# โฟลเดอร์ไอคอน GHS อ้างอิง 9 ชนิด (ไฟล์ชื่อ <key>.png ตรงกับ key ใน PICTOGRAM_KEYWORDS) ใช้เป็น
# "ต้นแบบ" เทียบกับรูปที่ดึงออกมาจาก SDS จริงใน detect_pictograms_from_images() ด้านล่าง
_GHS_ICONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "ghs_icons"
)

# ระยะห่าง (Hamming distance) ของ perceptual hash ที่ยังถือว่า "รูปเดียวกัน" - phash แบบ 8x8 default
# ของ imagehash ให้ hash ยาว 64 บิต ค่านี้ยังไม่ได้ผ่านการทดสอบกับ SDS จริงจำนวนมาก เป็นค่าประมาณการ
# เริ่มต้นเท่านั้น (ยิ่งค่าน้อยยิ่งเข้มงวด/พลาดง่าย ยิ่งค่ามากยิ่งหลวม/false positive ง่าย) ควรเก็บ log
# ผลจริงจากการใช้งานแล้วปรับเลขนี้ทีหลัง
_PICTOGRAM_HASH_THRESHOLD = 12


def _trim_and_hash(pil_img):
    """ตัดขอบขาว/โปร่งใสรอบรูปออกก่อนคำนวณ perceptual hash เพราะรูป pictogram ที่ดึงจาก PDF จริง
    มักมี padding รอบขอบไม่เท่ากับไอคอนอ้างอิง (แม้เป็นสัญลักษณ์เดียวกัน) ถ้าไม่ตัดก่อน hash จะต่างกัน
    มากเกินจริงจน threshold ปกติจับไม่เจอ"""
    img = pil_img.convert("L")
    bbox = ImageOps.invert(img).getbbox()
    if bbox:
        img = img.crop(bbox)
    return imagehash.phash(img)


_REFERENCE_PICTOGRAM_HASHES = None


def _load_reference_pictogram_hashes():
    """โหลด+คำนวณ hash ของไอคอนอ้างอิงทั้ง 9 ชนิดแค่ครั้งแรกที่เรียก (cache ไว้ใน module-level
    variable) เพราะไฟล์ไอคอนไม่เปลี่ยนระหว่างที่แอปรันอยู่ ไม่ต้องคำนวณซ้ำทุกครั้งที่ parse SDS"""
    global _REFERENCE_PICTOGRAM_HASHES
    if _REFERENCE_PICTOGRAM_HASHES is None:
        hashes = {}
        for key in PICTOGRAM_KEYWORDS:
            path = os.path.join(_GHS_ICONS_DIR, f"{key}.png")
            if os.path.exists(path):
                hashes[key] = _trim_and_hash(Image.open(path))
        _REFERENCE_PICTOGRAM_HASHES = hashes
    return _REFERENCE_PICTOGRAM_HASHES


def detect_pictograms_from_images(pdf_path):
    """
    ตรวจจับสัญลักษณ์ GHS จาก "รูปภาพจริง" ที่ฝังอยู่ในไฟล์ PDF (ต่างจาก detect_pictograms() ด้านบน
    ที่เดาจากคำในข้อความเท่านั้น) โดยดึงรูปทุกรูปในเอกสารออกมาด้วย PyMuPDF แล้วเทียบกับไอคอน GHS
    อ้างอิงด้วย perceptual hash - ถ้าระยะห่างไม่เกิน _PICTOGRAM_HASH_THRESHOLD ถือว่าเป็นสัญลักษณ์นั้น

    ใช้ "ร่วมกับ" detect_pictograms() เสมอ ไม่ใช้แทนที่กัน เพราะแต่ละวิธีจับกรณีที่อีกวิธีจับไม่ได้
    คนละแบบ: วิธีนี้จับไม่ได้ถ้า pictogram เป็น vector/font-icon ล้วน (ไม่มีรูปภาพ raster ฝังอยู่จริง)
    ส่วน detect_pictograms() (คำในข้อความ) อาจพลาดถ้า SDS มีแต่รูปไม่มีคำอธิบายกำกับ

    คืน list ของ key (เรียงตาม PICTOGRAM_KEYWORDS) เฉพาะที่เจอจริง คืน [] เงียบๆ ถ้าอ่านรูปในไฟล์
    ไม่ได้เลย (กันพังทั้งระบบเพราะไฟล์ PDF ผิดปกติ/รูปเสีย - ให้ fallback เหลือแค่ detect_pictograms())
    """
    reference = _load_reference_pictogram_hashes()
    if not reference:
        return []
    found = set()
    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                try:
                    base = doc.extract_image(xref)
                    img_hash = _trim_and_hash(Image.open(io.BytesIO(base["image"])))
                except Exception:
                    continue
                for key, ref_hash in reference.items():
                    if key not in found and (img_hash - ref_hash) <= _PICTOGRAM_HASH_THRESHOLD:
                        found.add(key)
        doc.close()
    except Exception:
        return []
    return [key for key in PICTOGRAM_KEYWORDS if key in found]


# คำที่มักเจอใน Section 8 (การควบคุมการรับสัมผัสสาร/การป้องกันส่วนบุคคล) บ่งบอกว่าควรใช้ PPE ชนิดนั้น
# ต่างจาก PICTOGRAM_KEYWORDS ตรงที่ผู้ใช้ขอให้ "ตรวจจับจริง" (ไม่ใช่แค่คำใบ้ที่ปิดการติ๊กอัตโนมัติไว้)
# เลยให้ผลลัพธ์นี้ไปติ๊ก checkbox ให้อัตโนมัติเลยในฟอร์ม (ผู้ใช้ยังแก้ไข/ติ๊กเพิ่มเองได้เสมอ)
PPE_KEYWORDS = {
    "safety_glasses": [r"แว่นตานิรภัย", r"safety\s+glasses"],
    "safety_goggle": [r"แว่นครอบตา", r"แว่นตากันสารเคมี", r"\bgoggles?\b"],
    "mask": [r"หน้ากาก", r"\bmask\b", r"การป้องกันระบบทางเดินหายใจ", r"respiratory protection"],
    "respirator": [r"เครื่องช่วยหายใจ", r"\brespirators?\b", r"ถังอากาศ",
                   r"self[\-\s]?contained breathing"],
    "glove": [r"ถุงมือ", r"\bgloves?\b", r"การป้องกันมือ", r"hand protection"],
    "safety_shoe": [r"รองเท้านิรภัย", r"รองเท้าป้องกัน", r"safety\s+shoes?", r"protective footwear"],
    "face_shield": [r"กระบังหน้า", r"face\s+shield", r"อุปกรณ์ป้องกันใบหน้า", r"face protection"],
    "coverall": [r"ชุดหมี", r"ชุดป้องกัน", r"\bcoveralls?\b", r"protective clothing",
                 r"การป้องกันผิวหนังและลำตัว", r"skin protection", r"body protection"],
}


# คำปฏิเสธที่มักตามหลังหัวข้อ PPE ทันที บ่งบอกว่า "ไม่จำเป็นต้องใช้" ทั้งที่หัวข้อเองมีคำที่ตรงกับ
# PPE_KEYWORDS (เช่น "Skin and body protection: ... is not ordinarily required beyond standard
# work clothes.") ถ้าไม่เช็คจุดนี้จะติ๊กผิดว่าต้องใช้ทั้งที่จริงเอกสารบอกว่าไม่ต้องใช้
_PPE_NEGATION_RE = re.compile(
    r"not\s+(?:ordinarily\s+)?(?:required|necessary|needed)|no\s+special\s+(?:measures|protection)|"
    r"ไม่จำเป็น|ไม่ต้อง(?:ใช้|สวม)?|ไม่มีความจำเป็น",
    re.IGNORECASE,
)


def _ppe_keyword_found(text, pattern):
    """เหมือน re.search ธรรมดา แต่เช็คข้อความหลังจุดที่เจอ (~120 ตัวอักษร) ด้วยว่ามีคำปฏิเสธ
    (_PPE_NEGATION_RE) ตามมาไหม ถ้ามีถือว่าไม่นับ (ลองดูจุดอื่นที่ pattern เดียวกันอาจเจอต่อ)"""
    for m in re.finditer(pattern, text, re.IGNORECASE):
        window = text[m.end(): m.end() + 120]
        if not _PPE_NEGATION_RE.search(window):
            return True
    return False


def detect_ppe(text):
    """
    ตรวจจับอุปกรณ์ป้องกันส่วนบุคคล (PPE) ที่ SDS แนะนำ จากคำใน Section 8 (การควบคุมการรับสัมผัสสาร/
    การป้องกันส่วนบุคคล) เป็นหลัก เพราะเป็น section มาตรฐานที่ระบุอุปกรณ์ป้องกันจริง (ไม่ใช่แค่พูดถึง
    "ดูหัวข้อที่ 8" แบบ Section 6/7 บางฉบับ) ถ้าหา section 8 ไม่เจอเลย (บาง SDS ไม่มีเลขกำกับหัวข้อ)
    ค้นทั้งไฟล์แทน คืน list ตามลำดับใน PPE_KEYWORDS ที่เจอจริง (อาจเจอได้หลายอันพร้อมกัน)
    """
    section8 = extract_section(text, 8) or text
    found = []
    for key, patterns in PPE_KEYWORDS.items():
        if any(_ppe_keyword_found(section8, p) for p in patterns):
            found.append(key)
    return found


# GHS มีคำสัญญาณมาตรฐานแค่ 2 คำเท่านั้น (Danger/อันตราย = รุนแรงกว่า, Warning/คำเตือน) SDS บางฉบับ
# พิมพ์คำอื่นต่อท้าย/นำหน้าคำมาตรฐานปนมาด้วย (เช่น "DANGER!", "Warning - Flammable") ผู้ใช้ขอให้ตรึง
# คำมาตรฐานไว้เสมอ ไม่ให้ข้อความอื่นมาแทนที่ ถ้ามีข้อความอื่นจริงให้ต่อท้ายด้วย " + " แทนที่จะทิ้งไป
_SIGNAL_WORD_PATTERNS = [
    (r"\bdanger\b", "Danger"),
    (r"\bwarning\b", "Warning"),
    (r"อันตราย", "อันตราย"),
    (r"คำเตือน", "คำเตือน"),
]


def _normalize_signal_word(raw):
    """ตรึงคำสัญญาณให้เป็นคำมาตรฐาน GHS (Danger/Warning หรือ อันตราย/คำเตือน) เสมอ ข้อความอื่นที่
    ติดมาด้วย (ถ้ามี) ต่อท้ายด้วย " + " เช่น "Danger + Flammable liquid" แทนที่จะแสดงข้อความดิบ
    ที่อาจสับสนหรือถูกตัดคำมาตรฐานออกไปเฉยๆ คืนค่าเดิมถ้าหาคำมาตรฐานไม่เจอเลย (ให้ผู้ใช้ตรวจ/แก้เอง)"""
    raw = (raw or "").strip()
    if not raw or raw == "-":
        return raw
    for pattern, standard in _SIGNAL_WORD_PATTERNS:
        m = re.search(pattern, raw, re.IGNORECASE)
        if m:
            extra = (raw[:m.start()] + raw[m.end():]).strip(" -:,.()!।")
            return f"{standard} + {extra}" if extra else standard
    return raw


def _strip_section16_revision_log(text):
    """Section 16 (ข้อมูลอื่นๆ) ของ SDS บางฉบับ (พบใน Interflon และแบรนด์ยุโรปอื่นๆ) มีตาราง
    "ประวัติการแก้ไข" (revision history) ที่พิมพ์ป้ายชื่อฟิลด์ของ Section 1-15 ซ้ำอีกรอบ พร้อมค่าเดิม/
    ค่าใหม่ แล้วต่อท้ายด้วยคำว่า "ใช่" ทุกแถว (บอกว่าฟิลด์นั้นถูกแก้ไขจากฉบับก่อนหน้าหรือไม่) ถ้าไม่ตัด
    ทิ้งก่อน regex ที่ค้นป้ายแบบมี ":" อาจไปแมตช์ตรงนี้แทนเนื้อหาจริงตอนต้นเอกสาร เพราะตารางนี้มี ":"
    ครบทุกแถว ในขณะที่บาง SDS ตระกูลเดียวกันเขียนเนื้อหาจริงแบบไม่มี ":" เลย (ผลคือได้ค่าที่ปนคำว่า
    "ใช่" ติดท้ายมาด้วย เช่น "สารหล่อลื่น ใช่" แทนที่จะเป็น "สารหล่อลื่น" เฉยๆ)
    Section 16 เป็นหัวข้อสุดท้ายเสมอ (ไม่มี Section 17) และไม่มีฟิลด์ไหนของเราต้องการข้อมูลจาก
    Section 16 อยู่แล้ว (มีแต่ประวัติแก้ไข/อักษรย่อ/เอกสารอ้างอิง) ตัดทิ้งได้อย่างปลอดภัย

    ต้องยึดว่าอยู่ต้นบรรทัดเท่านั้น (เหมือน extract_section) กันไปแมตช์ประโยคอ้างอิงกลางข้อความ เช่น
    "...ข้อความเต็ม: ดูหมวดที่ 16." ที่พบบ่อยมากใน SDS จริง (อ้างอิงไปเนื้อหา Section 16 จากที่อื่น
    ไม่ใช่หัวข้อ Section 16 เอง) ถ้าไม่กันไว้จะตัดข้อความทิ้งไปทั้งเกือบทั้งฉบับตั้งแต่จุดอ้างอิงแรก"""
    m = re.search(r"(?:^|\n)\s*(?:SECTION|ส่วนที่|ส่วน|หมวดที่)\s*16\b", text, re.IGNORECASE)
    return text[:m.start()] if m else text


def parse_sds(pdf_path):
    """ดึงฟิลด์ทั้งหมดที่ Template ต้องการ จากไฟล์ SDS คืนเป็น dict"""
    t = read_all_text(pdf_path)
    t = _strip_section16_revision_log(t)
    d = {}

    # ตัดมาเฉพาะช่วง Section 4 (การปฐมพยาบาล) และ Section 11 (พิษวิทยา) ไว้ก่อน
    # กันป้ายย่อยชื่อเดียวกัน (Eyes/Skin/Ingestion/Inhalation) ในคนละ section ปนกัน
    section4 = extract_section(t, 4)
    section9 = extract_section(t, 9)
    section11 = extract_section(t, 11)
    section13 = extract_section(t, 13)

    # หมายเหตุ: field สั้น (บรรทัดเดียว เช่น pH, สี, กลิ่น) ใช้ line() - หยุดจับที่ช่องว่างยาว (คอลัมน์ถัดไป)
    # field ยาว (อาจ wrap หลายบรรทัด เช่น วิธีกำจัด, การปฐมพยาบาล) ใช้ block() - จับข้ามบรรทัดได้จนกว่าจะเจอ
    # บรรทัดว่างหรือหัวข้อใหม่ ทั้งสองแบบยอมให้มีช่องว่างก่อน ":" ได้เสมอ (SDS หลายฉบับจัดหน้าแบบตาราง
    # มีช่องว่าง/แท็บคั่นระหว่างชื่อหัวข้อกับ ":" เยอะ เช่น "Flash point          :   value")
    # SDS อเมริกันบางเทมเพลต (พบใน Columbus Chemical/VelocityEHS) ไม่มี ":" คั่นเลยทั้งฉบับ เขียนป้าย
    # กับค่าคนละบรรทัดตลอด (เช่น "Product Name \nUrea Crystalline Powder, USP") ต้องมี label_next_line()
    # เป็น fallback ท้ายสุดเสมอสำหรับฟิลด์ที่มักเป็นคำตอบสั้นบรรทัดเดียว ไม่งั้นทุกฟิลด์จะว่างเปล่าหมด
    d["trade_name"] = grab(t, [
        line(r"Trade name"),
        line(r"Product name"),
        line(r"Product Name"),
        line(r"Material name"),
        line(r"ชื่อทางการค้า"), line(r"ชื่อผลิตภัณฑ์"), line(r"ชื่อสินค้า"),
        label_next_line(r"Product [Nn]ame"), label_next_line(r"Trade [Nn]ame"),
        label_next_line(r"ชื่อทางการค้า"),
    ])
    d["display_name"] = d["trade_name"]  # ค่าเริ่มต้น: ใช้ชื่อเดียวกับ trade_name (แก้แยกได้ในฟอร์ม)
    d["signal_word"] = _normalize_signal_word(grab(t, [
        line(r"Signal [Ww]ord"),
        line(r"คำสัญญาณ"),
    ]))
    # ถ้า Section 3 มีตารางส่วนประกอบหลายสาร ให้เลือกเลข CAS ของสารที่ Concentration (%) สูงสุดก่อน
    # (ถือเป็นสารหลักของผลิตภัณฑ์) ถ้าไม่มีตารางแบบนี้ (สารเดี่ยว) ค่อย fallback ไปหา "CAS No:" ปกติ
    d["cas"] = parse_cas_from_composition_table(t) or grab(t, [
        r"CAS Number\s*:\s*\n?\s*([\d\-]+)",
        r"CAS[\-\s]?No\.?\s*:\s*\n?\s*([\d\-]+)",
        r"CAS Registry Number\s*:\s*\n?\s*([\d\-]+)",
        r"(?:เลขทะเบียน|หมายเลข)\s*CAS\s*:\s*\n?\s*([\d\-]+)",
        # ไม่มี ":" คั่น (ดูคอมเมนต์ trade_name ด้านบน)
        r"CAS[\-\s]?No\.?\s*\n\s*([\d\-]+)",
        r"CAS Number\s*\n\s*([\d\-]+)",
    ])
    # ชื่อสารเคมีของแถวเดียวกับ CAS ที่เลือกไว้ข้างบน (ช่อง "ชื่อทางการค้า" บนฟอร์ม ซึ่งเดิมว่างเปล่า
    # เพราะไม่เคยมีฟิลด์ไหนดึงมาใส่ - ผู้ใช้อยากให้ชื่อกับ CAS ที่โชว์คู่กันเป็นสารตัวเดียวกันจริงๆ)
    # ถ้าหาตารางส่วนผสมไม่เจอ (สารเดี่ยว ไม่มีตาราง) ปล่อยว่างไว้ ไม่ต้องเดา (trade_name ก็แสดงอยู่แล้ว)
    d["ingredient_name"] = parse_ingredient_name_from_composition_table(t) or "-"
    d["un"] = grab(t, [
        # เดิม ".*?\n.*?" ไม่จำกัดระยะ (ข้าม \n ได้ไม่จำกัดจำนวนครั้งเพราะ DOTALL) ทำให้บาง SDS ที่ตาราง
        # ขนส่งมีหัวข้อ "IATA" ซ้ำหลายจุด (ADR/RID, IMDG, IATA คนละ mode) กระโดดข้ามไปไกลเกินจำเป็น
        # จนไปเจอคำว่า "IATA" ผิดจุด แล้วดันไปจับ "หัวข้อถัดไป" (เช่น "14.1 UN number or ID number:")
        # มาเป็นค่าแทนตัวเลขจริง จำกัดระยะให้อยู่ใกล้ๆ กัน (ไม่ข้ามบรรทัดว่างเกิน 1 บรรทัด) กันไว้
        r"UN[- ]Number[^\n]{0,80}\n[^\n]{0,80}IATA\s+([^\n]+?)" + _LINE_END,
        line(r"UN[\-\s]?Number"),
        line(r"UN[\-\s]?No\.?"),
        line(r"(?:เลข|หมายเลข)\s*UN"),
    ])
    d["formula"] = grab(t, [
        line(r"Chemical formula"),
        line(r"Formula"),
        line(r"สูตรทางเคมี"), line(r"สูตรโมเลกุล"),
        # หมายเหตุ: "ไม่เพิ่ม label_next_line(Formula)" ตรงนี้เพราะตารางส่วนผสมแบบ "หัวตารางทั้งหมด
        # ก่อน แล้วค่าจริงตามมาทีละแถว" (พบใน Columbus Chemical) มีคำว่า "Formula" เป็นแค่หัวคอลัมน์
        # ถ้าจับบรรทัดถัดไปตรงๆ จะได้หัวคอลัมน์ถัดไป ("Molecular Weight") มาแทนสูตรจริง ต้องอาศัย
        # ตัวแยกตารางเฉพาะ (แบบเดียวกับ parse_cas_from_composition_table) ถ้าจะรองรับ ยังไม่มีตอนนี้
    ])
    d["usage"] = grab(t, [
        r"Application of the substance / the mixture\s*(.+?)" + _BLOCK_END,
        block(r"Recommended use"),
        block(r"Product use"),
        block(r"Uses of the [Ss]ubstance.*?"),
        # ต้องลองแบบมี "ผลิตภัณฑ์"/"วิธี" ต่อท้าย/นำหน้าก่อน (พบใน SDS ตระกูล Sika/CPAC/LANKO
        # เช่น "วิธีการใช้งานผลิตภัณฑ์ :") เพราะ pattern ทั่วไปด้านล่างต้องมี ":" ติดกับ "การใช้งาน"
        # เป๊ะ ถ้ามีคำอื่นคั่นก่อนโคลอน (เช่น "ผลิตภัณฑ์") จะไม่แมตช์
        block(r"วิธีการใช้งานผลิตภัณฑ์"),
        block(r"(?:ลักษณะ|วัตถุประสงค์)?การใช้งาน"),
        label_next_line(r"Recommended\s+use"),
        label_next_line(r"การใช้ที่เกี่ยวข้องที่ระบุ"),
    ])
    # หัวข้อ 9.1 ของ SDS ตระกูล Sigma-Aldrich/Merck เขียนเป็นลิสต์ "a) ป้าย" ตามด้วยค่าจริงบรรทัด
    # ถัดไป (ไม่มี ":" คั่นเลย) ต้องลอง label_next_line() ก่อนเสมอ (scope แค่ section9 กัน "สี"/"กลิ่น"
    # ที่เป็นคำสั้นไปแมตช์ผิดที่ในเอกสารส่วนอื่น) ถ้าไม่เจอค่อย fallback ไปแพทเทิร์นแบบ "ป้าย: ค่า" เดิม
    d["state"] = grab_scoped(t, section9, [
        label_next_line(r"[a-z]?\)?\s*สถานะทางกายภาพ"),
        line(r"Form"),
        line(r"Physical [Ss]tate"),
        line(r"Appearance"),
        line(r"สถานะ"), line(r"ลักษณะทางกายภาพ"), line(r"ลักษณะ"),
        label_next_line(r"Physical [Ss]tate"),
    ])
    d["color"] = grab_scoped(t, section9, [
        label_next_line(r"[a-z]\)\s*สี"),
        line(r"Colou?r"), line(r"สี"),
        label_next_line(r"Colou?r"),
        # SDS ยุโรป (พบใน Interflon) ไม่มี ":" คั่นแม้แต่ที่เดียวทั้งฉบับ ต่างจาก Sigma-Aldrich ที่ต้องมี
        # ป้ายหน้า "a)" เสมอ - ต้องมี fallback แบบ "สี" เดี่ยวๆ ไม่มีคำนำหน้าด้วย (scope แค่ section9
        # กันไปแมตช์ผิดที่ในเอกสารส่วนอื่น ความเสี่ยงต่ำเพราะ "สี" สั้นแต่ scope แคบพอ)
        label_next_line(r"สี"),
    ])
    d["odor"] = grab_scoped(t, section9, [
        label_next_line(r"[a-z]\)\s*กลิ่?น"),
        line(r"Odou?r"), line(r"กลิ่น"),
        label_next_line(r"Odou?r"),
        label_next_line(r"กลิ่?น"),
    ])
    d["boiling"] = grab_scoped(t, section9, [
        line(r"Boiling point/Boiling range"),
        # เดิมใช้ "initial boiling point" เป็นวลีตายตัว (เว้นวรรคปกติ) พัง ถ้าป้ายตัดขึ้นบรรทัดใหม่กลาง
        # วลีพอดี (เช่น "Boiling point, initial boiling\npoint and boiling range" ที่เจอจริงในบางฉบับ)
        # เปลี่ยนเป็น \s+ คั่นทุกคำแทน กัน \n แทรกกลางคำแล้วจับไม่ได้
        line(r"Boiling [Pp]oint,?\s*(?:initial\s+boiling\s+point\s*)?(?:and\s*)?(?:boiling\s*)?(?:range)?"),
        line(r"จุดเดือด"),
        label_next_line(r"Initial\s+boiling\s+point\s+and\s+boiling\s*range"),
        label_next_line(r"Boiling\s+[Pp]oint"),
        label_next_line(r"จุดเดือดเริ่มต้น\s*(?:และ)?\s*ช่วงของการเดือด"), label_next_line(r"จุดเดือด"),
    ])
    d["ph"] = _extract_leading_number(grab_scoped(t, section9, [
        label_next_line(r"[a-z]\)\s*ค่าความเป็นกรด[\-\s]?ด่าง"),
        line(r"pH[\-\s]?value"),
        line(r"pH"),
        line(r"ความเป็นกรด[\-\s]?ด่าง"),
        label_next_line(r"pH"),
        label_next_line(r"ค่าความเป็นกรด[\-\s]?ด่าง(?:\s*\(pH\))?"),
    ]))
    d["flash"] = grab_scoped(t, section9, [
        label_next_line(r"[a-z]\)\s*จุดวาบไฟ"),
        line(r"Flash [Pp]oint"), line(r"จุดวาบไฟ"),
        label_next_line(r"Flash\s+[Pp]oint"),
        label_next_line(r"จุดวาบไฟ"),
    ])
    # Section 4 (การปฐมพยาบาล) - ค้นในช่วง Section 4 ก่อน กันไปจับ Section 11 (พิษวิทยา) ผิด
    # ป้ายเปล่าๆ อย่าง "Eyes:"/"Skin:"/"Ingestion:"/"Inhalation:" ต้องยึดให้อยู่ต้นบรรทัดเท่านั้น
    # (ไม่งั้น "After inhalation:" จะโดนจับซ้อนเพราะมีคำว่า "inhalation:" อยู่ข้างในเป็น substring)
    # "หากหายใจเข้าไป"/"ในกรณีที่เข้าตา" ฯลฯ พบใน SDS ตระกูล Sika/CPAC/LANKO (คนละสำนวนกับ "ทางตา"
    # ที่เจอในเอกสารอื่น) เป็นการเขียนแบบ "ถ้าเกิดเหตุการณ์...ให้ทำอย่างไร" แทนการระบุอวัยวะตรงๆ
    d["fa_eye"] = grab_scoped(t, section4, [
        block(r"After eye contact"),
        block(r"Eye [Cc]ontact"),
        block(r"If in eyes"),
        block(r"(?:^|\n)\s*Eyes"),
        block(r"(?:^|\n)\s*ทางตา"),
        block(r"ในกรณีที่เข้าตา"), block(r"ในกรณีที่สัมผัสกับตา"), block(r"หากเข้าตา"),
        label_next_line(r"Eye [Cc]ontact"),
        label_next_line(r"การสัมผัสถูกดวงตา"),
    ])
    d["fa_oral"] = grab_scoped(t, section4, [
        block(r"After swallowing"),
        block(r"If [Ss]wallowed"),
        block(r"(?:^|\n)\s*Ingestion"),
        block(r"(?:^|\n)\s*ทางปาก"), block(r"(?:กรณี)?การกลืนกิน"),
        block(r"หากกลืนกิน(?:เข้าไป)?"),
        label_next_line(r"Ingestion"),
        label_next_line(r"การกลืนกิน"),
    ])
    d["fa_skin"] = grab_scoped(t, section4, [
        block(r"After skin contact"),
        block(r"Skin [Cc]ontact"),
        block(r"If on skin"),
        block(r"(?:^|\n)\s*Skin"),
        block(r"(?:^|\n)\s*ทางผิวหนัง"),
        block(r"ในกรณีที่สัมผัสกับผิวหนัง"), block(r"หากสัมผัสผิวหนัง"),
        label_next_line(r"Skin [Cc]ontact"),
        label_next_line(r"การสัมผัสทางผิวหนัง"),
    ])
    d["fa_inhale"] = grab_scoped(t, section4, [
        block(r"After inhalation"),
        block(r"If [Ii]nhaled"),
        block(r"(?:^|\n)\s*Inhalation"),
        block(r"(?:^|\n)\s*ทางการหายใจ"), block(r"การหายใจเข้าไป"), block(r"การสูดดม"),
        block(r"หากหายใจเข้าไป"),
        label_next_line(r"Inhalation"),
        label_next_line(r"การสูดดม"),
    ])
    # Section 11 (พิษวิทยา) - ค้นในช่วง Section 11 ก่อน กันไปจับ Section 4 (ปฐมพยาบาล) ผิด
    # SDS ตระกูล Sika/CPAC/LANKO ใช้สำนวน "เมื่อ..." (เมื่อเข้าตา/เมื่อสัมผัสกับผิวหนัง ฯลฯ) แทน
    # หมายเหตุ: label ไทยหลายคำที่นี่ใช้ "\s*" คั่นระหว่างพยางค์/คำ (แทนการเขียนติดกันเป๊ะ) เพราะ
    # SDS ตระกูล Sika/CPAC/LANKO มักตัดขึ้นบรรทัดใหม่กลางคำ (เช่น "เมื่อกลืน\nกิน", "เมื่อ\nหายใจเข้าไป")
    # ทำให้ label ที่เขียนติดกันแบบเดิมไม่ match เพราะมี "\n" ขั้นกลาง ("\s" ครอบคลุม "\n" ด้วยอยู่แล้ว)
    # ลำดับ pattern (กลับด้านจากเดิม): คำจำแนกความเป็นอันตราย (เช่น "การกัดกร่อนและการระคายเคืองต่อ
    # ผิวหนัง: ไม่มีการจำแนกโดยขึ้นกับข้อมูลที่มีอยู่") มาก่อนเสมอ เพราะเป็นสิ่งที่ผู้ใช้อยากรู้จริงๆ
    # ("อันตรายแบบไหน") ส่วนตัวเลข LD50/LC50 (ต้องสัมผัส/กินเท่าไรถึงเป็นพิษ) เป็นแค่ข้อมูลสนับสนุน
    # ใช้เป็น fallback ต่อท้ายแทนถ้าไม่มีคำจำแนกความเป็นอันตรายให้ดึง
    d["hz_eye"] = grab_scoped_no_ld50(t, section11, [
        label_next_line(r"การทำลายดวงตาอย่างรุนแรง\s*(?:และ)?\s*การระคายเคืองต่อดวงตา"),
        block(r"Serious eye damage[^\n:]*"),
        block(r"Eye [Ii]rritation"),
        block(r"on the eye"),
        block(r"(?:^|\n)\s*Eyes"),
        block(r"(?:^|\n)\s*ทางตา"),
        block(r"เมื่อ\s*เข้า\s*ตา"), block(r"เมื่อ\s*สัมผัส\s*กับ\s*ตา"),
    ])
    d["hz_skin"] = grab_scoped_no_ld50(t, section11, [
        label_next_line(r"การกัดกร่อน\s*(?:และ)?\s*การระคายเคืองต่อผิวหนัง"),
        block(r"Skin [Cc]orrosion[^\n:]*"),
        block(r"Skin [Ii]rritation"),
        block(r"on the skin"),
        block(r"(?:^|\n)\s*Skin"),
        block(r"(?:^|\n)\s*ทางผิวหนัง"),
        block(r"เมื่อ\s*สัมผัส\s*กับ\s*ผิวหนัง"),
        block(r"ความเป็นพิษ.{0,15}เมื่อ\s*สัมผัส\s*ผิวหนัง"),
    ])
    # หัวข้อ Section 11 บางฉบับ (ตระกูล Sika/CPAC/LANKO) ไม่มีคำจำแนกแยกตามช่องทาง (ทางปาก/ทางการ
    # หายใจ) โดยตรง มีแต่คำจำแนก "ความเป็นพิษแบบเฉียบพลัน" รวมช่องทางเดียว - ใช้ตัวนี้เป็นคำตอบแทน
    # LD50 ดิบถ้าหาคำจำแนกเฉพาะช่องทางไม่เจอ (ดีกว่าโชว์ตัวเลขที่ผู้ใช้บอกว่าไม่ใช่สิ่งที่อยากรู้)
    d["hz_oral"] = grab_scoped_no_ld50(t, section11, [
        label_next_line(r"ความเป็นพิษ\s*(?:แบบ)?\s*เฉียบพลัน"),
        block(r"Acute toxicity[^\n:]*(?:[Oo]ral|[Ii]ngestion)[^\n:]*"),
        block(r"(?:^|\n)\s*Ingestion"),
        block(r"(?:^|\n)\s*ทางปาก"),
        block(r"เมื่อ\s*กลืน\s*กิน\s*เข้าไป"), block(r"เมื่อ\s*กลืน\s*กิน"),
        block(r"ความเป็นพิษ.{0,15}เมื่อ\s*กลืน\s*กิน"),
    ])
    d["hz_inhale"] = grab_scoped_no_ld50(t, section11, [
        label_next_line(r"ความเป็นพิษ\s*(?:แบบ)?\s*เฉียบพลัน"),
        block(r"Acute toxicity[^\n:]*[Ii]nhalation[^\n:]*"),
        block(r"(?:^|\n)\s*Inhalation"),
        block(r"(?:^|\n)\s*ทางการหายใจ"), block(r"การสูดดม"),
        block(r"เมื่อ\s*หายใจ\s*เข้าไป"),
        block(r"ความเป็นพิษ.{0,15}เมื่อ\s*หายใจ\s*เข้าไป"),
    ])
    d["fire"] = grab(t, [
        block(r"Suitable extinguishing agents"),
        # เดิม "media" เป็นคำเดียวตายตัว พังถ้าฟอนต์/PDF ตัดคำกลางบรรทัดด้วยขีด "-" (พบใน Cassida:
        # "Suitable extinguishing me-\ndia:") ต้องยอม "me" + ขีด/ช่องว่างเผื่อ + "dia" แทน
        block(r"Suitable\s+extinguishing\s+me-?\s*dia"),
        block(r"Suitable extinguishing media"),
        block(r"Extinguishing media"),
        block(r"(?:วิธี)?การดับเพลิง"), block(r"สารดับเพลิงที่เหมาะสม"),
        label_next_line(r"Suitable\s+[Ee]xtinguishing\s+[Mm]edia"),
        # เดิม "Suitable extinguishing me-\ndia:" (คำถูกตัดกลางด้วยขีดที่บรรทัดใหม่ พบใน Cassida) ก็พัง
        # เพราะ regex เดิมหา "media" เป็นคำเดียวติดกัน ต้องยอม \s*-?\s* คั่นกลางคำได้ด้วย
        label_next_line(r"Suitable\s+extinguishing\s+me-?\s*dia"),
        label_next_line(r"สารดับเพลิงที่เหมาะสม"),
    ])
    d["reactivity"] = grab(t, [
        r"Possibility of hazardous reactions\s*(.+?)" + _BLOCK_END,
        block(r"Hazardous reactions"),
        block(r"Chemical stability"),
        block(r"การเกิดปฏิกิริยา"), block(r"ความเสถียรทางเคมี"), block(r"โอกาสเกิดปฏิกิริยาอันตราย"),
        label_next_line(r"Chemical\s+[Ss]tability"), label_next_line(r"Reactivity"),
        label_next_line(r"ความเสถียรทางเคมี"), label_next_line(r"การเกิดปฏิกิริยา"),
    ])
    # Section 6 (การจัดการเมื่อรั่วไหล) - "Methods for cleaning up" คือสิ่งที่ตรงกับ "กรณีหกรั่วไหล" ที่สุด
    # ถ้าไม่เจอค่อย fallback ไปที่ "Environmental precautions" (ยังพอเกี่ยวข้องแต่ไม่ตรงเป๊ะ)
    d["spill"] = grab(t, [
        block(r"Methods and material for containment and cleaning up"),
        block(r"Methods for [Cc]leaning up"),
        block(r"Methods for containment"),
        block(r"Environmental precautions"),
        block(r"วิธีปฏิบัติเมื่อ(?:มี)?การหกรั่วไหล"), block(r"(?:กรณี|การจัดการเมื่อ)สารหกรั่วไหล"),
        # พบในไฟล์ตระกูล Sika/CPAC/LANKO/Kemox: ป้ายนี้ตัดขึ้นบรรทัดใหม่กลางคำ
        # ("วิธีการและวัสดุสำหรับกักเก็บ\nและทำความสะอาด") จึงต้องใช้ \s* คั่นแทนการเว้นวรรคปกติ
        block(r"วิธีการและวัสดุ\s*สำหรับกักเก็บ\s*และทำความสะอาด"),
        label_next_line(r"Methods\s+for\s+[Cc]leaning\s+up"),
    ])
    # เดิม grab(t, ...) ค้นทั้งไฟล์แบบไม่ scope section ทำให้บางไฟล์ไปเจอ "การกำจัด:" สั้นๆ ใน
    # Section 2 (บรรทัดย่อยของ P501 ใต้ข้อควรระวัง) แทนที่จะเป็น Section 13 (ข้อพิจารณาในการกำจัด)
    # ตัวจริงตามชื่อ field - ถ้าไฟล์ไหนไม่มี P-code เลย (เช่น "ไม่ใช่สารอันตราย") เดิมจะออกมาเป็น "-"
    # ทั้งที่ Section 13 มีข้อมูลจริงอยู่ - ใช้ grab_scoped ตรง section13 ก่อนเป็นหลัก แล้วค่อย fallback
    # ไปค้นทั้งไฟล์ถ้าหา section 13 ไม่เจอเลย (SDS บางฉบับไม่มีเลขหัวข้อกำกับ)
    d["disposal"] = grab_scoped(t, section13, [
        block(r"Recommendation"),
        block(r"Disposal methods"),
        block(r"Waste treatment methods"),
        # "วิธีการกำจัด" ตามด้วยหัวข้อย่อย "บรรจุภัณฑ์ที่ปนเปื้อน" คนละบรรทัดก่อนโคลอน (พบใน Sika/LANKO)
        block(r"วิธีการกำจัด\s*บรรจุภัณฑ์ที่ปนเปื้อน"),
        block(r"บรรจุภัณฑ์ที่ปนเปื้อน"),
        block(r"(?:วิธี|คำแนะนำ)?การกำจัด"),
        # พบใน SDS เทมเพลตอเมริกัน (Columbus Chemical) ที่ไม่มี ":" คั่นเลย และมีป้ายย่อยซ้อนอีกชั้น
        # ("Disposal methods" -> "Waste from residues/unused\nproducts" -> คำตอบจริง) ต้องเจาะจงไปที่
        # ป้ายย่อยตัวในสุดโดยตรง ไม่ใช้ "Disposal methods" เฉยๆ เพราะจะได้ป้ายย่อยมาแทนคำตอบจริง
        label_next_line(r"Waste\s+from\s+residues/?unused\s+products?"),
        label_next_line(r"Waste\s+from\s+residues"),  # บางฉบับไม่มี "/unused products" ต่อท้าย
        # SDS ยุโรป (พบใน Interflon) ไม่มี ":" คั่นเลย ป้ายย่อยจริงคือ "การกำจัดของเสียของภาชนะบรรจุ/
        # บรรจุภัณฑ์" (ไม่ใช่แค่ "การกำจัด" เฉยๆ ซึ่งเป็นแค่ชื่อหัวข้อรวม ไม่มีคำตอบตามหลังตรงๆ)
        label_next_line(r"การกำจัดของเสียของภาชนะบรรจุ/?บรรจุภัณฑ์"),
    ])
    d["storage"] = grab(t, [
        block(r"Requirements to be met by storerooms and receptacles"),
        block(r"Storage conditions"),
        block(r"Conditions for safe storage"),
        block(r"การเก็บรักษา"), block(r"สภาวะการเก็บรักษาที่ปลอดภัย"),
        # พบในไฟล์ตระกูล Sika/CPAC/LANKO/Kemox: ใช้ "สภาวะการเก็บที่ปลอดภัย" (ไม่มีคำว่า "รักษา")
        block(r"สภาวะการเก็บที่ปลอดภัย"),
        label_next_line(r"Storage\s+[Cc]onditions"),
    ])
    # ดัชนี NFPA มักอยู่ในรูปไดอะแกรมเพชร ไม่ใช่ข้อความเรียงกันแบบปกติ - pdfplumber อาจดึงตัวเลข/ป้าย
    # ออกมาสลับตำแหน่งกับข้อความส่วนอื่นของเอกสาร (เช่น ตาราง HMIS ที่อยู่ใกล้กัน) ถ้าค้นทั้งไฟล์เฉยๆ
    # เสี่ยงไปจับเลขผิดจุดมาก จึงจำกัดให้ค้นเฉพาะในช่วงข้อความถัดจากคำว่า "NFPA" ก่อน (window)
    # และรับเฉพาะเลข 0-4 เท่านั้น (ตามสเกลจริงของ NFPA) ถ้าหาไม่เจอค่อย fallback ไปค้นทั้งไฟล์
    NFPA_WINDOW = 300
    d["nfpa_health"] = (
        grab_near(t, "NFPA", [r"Health\s*[:=]?\s*([0-4])"], window=NFPA_WINDOW)
        or grab(t, [r"Health\s*=\s*([0-4])", r"Health\s*[:\-]\s*([0-4])"], default="")
    )
    d["nfpa_fire"] = (
        grab_near(t, "NFPA", [r"Flammability\s*[:=]?\s*([0-4])", r"Fire\s*[:=]?\s*([0-4])"], window=NFPA_WINDOW)
        or grab(t, [r"Fire\s*=\s*([0-4])", r"Fire\s*[:\-]\s*([0-4])"], default="")
    )
    d["nfpa_react"] = (
        grab_near(t, "NFPA", [r"Instability\s*[:=]?\s*([0-4])", r"Reactivity\s*[:=]?\s*([0-4])"], window=NFPA_WINDOW)
        or grab(t, [r"Reactivity\s*=\s*([0-4])", r"Reactivity\s*[:\-]\s*([0-4])"], default="")
    )
    # รวมผลตรวจจับ 2 ทาง: จากคำในข้อความ (detect_pictograms) + จากรูปภาพจริงที่ฝังใน PDF
    # (detect_pictograms_from_images) เจอจากทางใดทางหนึ่งก็นับว่าเจอ (ดูเหตุผลใน docstring ของ
    # detect_pictograms_from_images ว่าทำไมต้องใช้ทั้งคู่ ไม่ใช้แทนกัน)
    combined_pictograms = set(detect_pictograms(t)) | set(detect_pictograms_from_images(pdf_path))
    d["pictograms"] = [key for key in PICTOGRAM_KEYWORDS if key in combined_pictograms]
    d["ppe"] = detect_ppe(t)
    # สำหรับหน้า "ฉลากภาชนะบรรจุ" (label.html) - Hazard/Precautionary statement + ข้อมูลผู้ผลิต
    # ถ้าดึงไม่ครบ ผู้ใช้แก้ไข/พิมพ์เพิ่มเองในฟอร์มได้ (ไม่บังคับต้องดึงได้ 100%)
    d["hazard_statements"] = extract_hazard_statements(t)
    d["precautionary_statements"] = extract_precautionary_statements(t)
    d["hazardous_substances"] = extract_hazardous_substances(t)
    d.update(extract_supplier_info(t))
    # ช่องขาว (Special Hazard) ในรูปเพชร NFPA - เดิมเคยเดาเบื้องต้นจากสัญลักษณ์ GHS ที่ตรวจเจอ
    # (oxidizer->OXY, corrosive->COR) แต่ผู้ใช้ขอให้เลิกเดาอัตโนมัติ เพราะ SDS ไม่ได้ระบุค่านี้ตรงๆ
    # การเดาจากสัญลักษณ์ GHS อย่างเดียวไม่น่าเชื่อถือพอ ปล่อยว่างเสมอ ("ไม่มี") ให้ผู้ใช้เลือกเองทุกครั้ง
    d["nfpa_special"] = ""
    return d


if __name__ == "__main__":
    # รันจากรากโปรเจกต์ (ไม่มี import ข้ามไฟล์ รันตรงๆ ด้วย python core/parser.py ได้เลย)
    import json
    print(json.dumps(parse_sds("getpdf_sample.pdf"), indent=2, ensure_ascii=False))
