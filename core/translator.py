# -*- coding: utf-8 -*-
"""
translator.py - แปลข้อความอังกฤษ -> ไทย

ถ้าตั้งค่า GOOGLE_TRANSLATE_API_KEY ไว้ใน .env (หรือ environment variable บน Render) ระบบจะเรียก
Google Cloud Translation API v2 (translate_with_google_cloud) เป็นตัวหลักก่อนเสมอ เพราะเสถียรกว่า
deep-translator มาก (deep-translator เป็นการ "ยืม" หน้าเว็บ Google Translate มาใช้แบบไม่เป็นทางการ
ไม่ใช่ API จริง จึงล่มๆ ดับๆ เป็นระยะแม้แต่ตอนรันในเครื่องเปล่าๆ ก็ยังเจอ) ถ้าเรียก Google Cloud API
แล้วพัง (เช่น key หมดโควต้า, เน็ตหลุด) จะ fallback ไปใช้ deep-translator (ฟรี) แทนอัตโนมัติ ไม่ต้อง
ตั้งค่าอะไรเพิ่ม ถ้าไม่มี GOOGLE_TRANSLATE_API_KEY เลย ก็ใช้ deep-translator เป็นตัวหลักเหมือนเดิม
"""
import os
import re
import sys
from deep_translator import GoogleTranslator, MyMemoryTranslator

# อ่านค่าจาก environment variable (ตั้งค่าใน .env) ไม่ hardcode คีย์ในโค้ด
GOOGLE_TRANSLATE_API_KEY = os.environ.get("GOOGLE_TRANSLATE_API_KEY", "")

# MyMemory (บริการแปลฟรีอีกเจ้า ไม่ต้องมี API key) ต้องการรหัสภาษาแบบ locale เต็ม ("en-US"/"th-TH")
# ไม่ใช่รหัสสั้น ("en"/"th") แบบที่ใช้กันทั้งแอป แปลงให้ตรงนี้ทีเดียว ไม่ต้องเปลี่ยนโค้ดจุดอื่น
_MYMEMORY_LOCALE = {"en": "en-US", "th": "th-TH"}


def _log(msg):
    """print ไป stderr ตรงๆ (Render จับ stdout/stderr ไปโชว์ในแท็บ Logs อัตโนมัติ) ใช้ debug ตอนแปล
    พังแบบเงียบๆ บน production ที่เราเข้าไปดูโค้ด/เน็ตเวิร์กจริงไม่ได้ ต้องอาศัย log แทน"""
    print(f"[translator] {msg}", file=sys.stderr, flush=True)

_THAI_CHAR_RE = re.compile(r"[฀-๿]")
_LATIN_CHAR_RE = re.compile(r"[A-Za-z]")
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


def _already_in_target_language(text, target):
    """เดาไวๆ ว่าข้อความนี้ "เป็นภาษาปลายทางอยู่แล้ว" หรือไม่ (นับสัดส่วนตัวอักษรไทย/อังกฤษ)
    ใช้กันเคส SDS ที่บางหัวข้อเป็นภาษาไทยอยู่แล้วบางส่วน (พบบ่อยใน SDS ตระกูล Sika/CPAC/LANKO)
    เพราะ Google Translate ที่ถูกบังคับ source="en"/"th" กับข้อความที่เป็นภาษาปลายทางอยู่แล้ว
    มักจะ throw TranslationNotFound (ซึ่งโค้ดข้างล่างจับไว้แล้ว คืนค่าเดิม) แต่บางครั้งก็ "แปลสำเร็จ"
    เป็นข้อความมั่วๆ ที่ไม่เกี่ยวข้องกับต้นฉบับเลยแทน (เคยเจอ "ไม่มีการจำแนกโดยขึ้นกับข้อมูลที่มีอยู่"
    ถูกแปลงเป็นข้อความอื่นทั้งที่ถูกอยู่แล้ว) - ทางที่ปลอดภัยที่สุดคือไม่ส่งไปแปลซ้ำตั้งแต่แรกเลย"""
    letters = _LETTER_RE.findall(text)
    if not letters:
        return False
    if target == "th":
        thai_count = sum(1 for c in letters if _THAI_CHAR_RE.match(c))
        return thai_count / len(letters) > 0.5
    latin_count = sum(1 for c in letters if _LATIN_CHAR_RE.match(c))
    return latin_count / len(letters) > 0.5


def translate_text(text, source="en", target="th"):
    """แปลข้อความ 1 ก้อน คืนค่าเดิมถ้าแปลไม่ได้หรือค่าว่าง/ไม่มีความหมาย หรือถ้าเป็นภาษาปลายทาง
    อยู่แล้ว (ไม่ต้องส่งไปแปลซ้ำ ดู _already_in_target_language ด้านบน)

    ลองตามลำดับนี้ ใช้ผลลัพธ์แรกที่สำเร็จ:
    1. Google Cloud Translation API (ถ้ามี key ตั้งไว้ - เสถียรที่สุด)
    2. deep-translator/GoogleTranslator (ฟรี ไม่ต้อง key แต่ล่มๆ ดับๆ เป็นระยะเพราะไม่ใช่ API จริง)
    3. MyMemoryTranslator (ฟรีอีกเจ้า ไม่ต้อง key เหมือนกัน ใช้เป็นไม้ตายสุดท้ายถ้าสองตัวบนพังหมด)
    """
    if not text or text.strip() in ("-", ""):
        return text
    if _already_in_target_language(text, target):
        return text
    if GOOGLE_TRANSLATE_API_KEY:
        try:
            result = translate_with_google_cloud(text, source=source, target=target)
            if result:
                return result
            _log("Google Cloud API returned an empty result, trying deep-translator next")
        except Exception as e:
            # key หมดโควต้า/เน็ตหลุด/ยังไม่ enable Cloud Translation API/ฯลฯ - log ไว้ให้เช็คได้จาก
            # Render Logs tab (มองจากภายนอกแยกไม่ออกว่าทำไมพัง ถ้าไม่ log ตรงนี้ไว้) แล้วไปลอง
            # deep-translator ต่อด้านล่างแทน
            _log(f"Google Cloud Translation API failed ({type(e).__name__}: {e}), trying deep-translator next")
    else:
        _log("GOOGLE_TRANSLATE_API_KEY is not set, trying deep-translator first")
    try:
        result = GoogleTranslator(source=source, target=target).translate(text)
        if result:
            return result
        _log("deep-translator returned an empty result, trying MyMemory next")
    except Exception as e:
        _log(f"deep-translator failed ({type(e).__name__}: {e}), trying MyMemory next")
    try:
        mm_source = _MYMEMORY_LOCALE.get(source, source)
        mm_target = _MYMEMORY_LOCALE.get(target, target)
        result = MyMemoryTranslator(source=mm_source, target=mm_target).translate(text)
        if not result:
            _log("MyMemory returned an empty result, keeping original text")
        return result if result else text
    except Exception as e:
        # ทั้ง 3 ตัวพังหมดแล้ว (หรือ 2 ตัวถ้าไม่มี Google Cloud key) - คืนค่าเดิมไว้ก่อน ไม่ทำให้แอปพัง
        _log(f"MyMemory failed too ({type(e).__name__}: {e}), keeping original text")
        return text


def _safe_translated(original, candidate):
    """คืนค่าแปลถ้าใช้ได้จริง (ไม่ว่างเปล่า) ไม่งั้นคืนค่าเดิม กันกรณี GoogleTranslator คืนค่าว่าง/
    เว้นวรรคล้วนโดยไม่ throw exception (เช่นโดน rate-limit จาก IP ของ hosting แล้ว Google ตอบกลับ
    หน้า error/captcha แทนคำแปลจริง - translate_text() เช็คแค่ผลลัพธ์ว่าง("")ตรงๆ ไม่ครอบคลุมกรณีนี้)
    ถ้าต้นฉบับเองว่างอยู่แล้วตั้งแต่แรก ก็ไม่ต้องเช็ค (ไม่มีอะไรให้เสียหาย)"""
    if original and str(original).strip() and not (candidate and str(candidate).strip()):
        return original
    return candidate


def translate_fields(data, keys, source="en", target="th"):
    """แปลหลายฟิลด์พร้อมกัน รับ dict ข้อมูล + list ของ key ที่ต้องการแปล คืน dict ใหม่
    ถ้าค่าของ key นั้นเป็น list (เช่น hazard_statements ของหน้าฉลาก) แปลทีละข้อความในลิสต์
    ทุกค่าที่แปลแล้วผ่าน _safe_translated() ก่อนเสมอ กันข้อมูลที่ผู้ใช้กรอก/ดึงมาแล้วหายไปเฉยๆ
    ถ้าการแปลล้มเหลวแบบเงียบๆ (ไม่ throw exception แต่คืนค่าว่าง)
    source/target สลับได้ (เช่น "th"->"en" ตอนผู้ใช้กด "แปลเป็นอังกฤษ" ย้อนกลับ)"""
    translated = dict(data)
    for k in keys:
        val = data.get(k, "")
        if isinstance(val, list):
            translated[k] = [_safe_translated(v, translate_text(v, source=source, target=target)) for v in val]
        else:
            translated[k] = _safe_translated(val, translate_text(val, source=source, target=target))
    return translated


def translate_with_google_cloud(text, source="en", target="th"):
    """
    ทางเลือกสำหรับอนาคต: เรียก Google Cloud Translation API v2 (REST) ด้วย API key จริง
    ต้องตั้ง GOOGLE_TRANSLATE_API_KEY ใน .env ก่อนใช้งาน
    """
    import requests
    if not GOOGLE_TRANSLATE_API_KEY:
        raise RuntimeError("ยังไม่ได้ตั้งค่า GOOGLE_TRANSLATE_API_KEY ใน .env")
    url = "https://translation.googleapis.com/language/translate/v2"
    params = {
        "q": text,
        "source": source,
        "target": target,
        "key": GOOGLE_TRANSLATE_API_KEY,
    }
    resp = requests.post(url, data=params, timeout=10)
    resp.raise_for_status()
    return resp.json()["data"]["translations"][0]["translatedText"]
