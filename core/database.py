# -*- coding: utf-8 -*-
"""
database.py - จัดการฐานข้อมูล SQLite สำหรับ "ทะเบียนสารเคมี"
ใช้ sqlite3 มาตรฐานของ Python ไม่ต้องติดตั้ง library เพิ่ม ไม่ต้องตั้ง server แยก
เก็บทุกครั้งที่มีการ "สร้าง PDF" สำเร็จ ไว้เป็นประวัติให้ Admin ดู/แก้/ลบได้
"""
import os
import sqlite3
from datetime import datetime, timedelta

# PROJECT_ROOT ต้องขึ้นไปอีก 1 ชั้นจากไฟล์นี้ เพราะย้ายเข้ามาอยู่ใน core/ แล้ว
# แต่โฟลเดอร์ data/ (runtime, ไม่ใช่โค้ด) ยังอยู่ที่รากโปรเจกต์เหมือนเดิม
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "chemsafe.db")


def get_connection():
    """เปิดการเชื่อมต่อฐานข้อมูล คืนแถวเป็น dict-like (sqlite3.Row) เพื่อใช้งานง่าย"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """สร้างตาราง records/label_records ถ้ายังไม่มี (เรียกตอนแอปเริ่มทำงาน)"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_name TEXT,
            cas TEXT,
            un TEXT,
            data_json TEXT NOT NULL,      -- ข้อมูลทุกฟิลด์ (แก้ไขแล้ว) เก็บเป็น JSON ก้อนเดียว
            output_filename TEXT,            -- ชื่อไฟล์ .xlsx ที่สร้างเสร็จ (อยู่ใน data/generated/)
            label_image_filename TEXT,    -- ชื่อไฟล์รูปสลากสารเคมี (อยู่ใน data/uploads/)
            container_image_filename TEXT,
            created_at TEXT NOT NULL
        )
    """)
    # แยกตารางต่างหากสำหรับฉลากภาชนะบรรจุ (หน้า /label) เพราะโครงสร้างข้อมูลคนละแบบกับฟอร์ม SDS
    # ติดหน้างาน (ไม่มี un ที่จำเป็น, มี size_key) ใช้สำหรับ dashboard สถิติการใช้งานในหน้า Admin
    conn.execute("""
        CREATE TABLE IF NOT EXISTS label_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT,
            cas TEXT,
            un TEXT,
            size_key TEXT,
            output_filename TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def add_record(data, output_filename, label_image_filename=None, container_image_filename=None):
    """บันทึกทะเบียนสารเคมี 1 รายการ (output_filename คือไฟล์ .xlsx ที่สร้างเสร็จ) คืนค่า id ที่สร้าง"""
    import json
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO records
           (trade_name, cas, un, data_json, output_filename,
            label_image_filename, container_image_filename, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data.get("trade_name", "-"),
            data.get("cas", "-"),
            data.get("un", "-"),
            json.dumps(data, ensure_ascii=False),
            output_filename,
            label_image_filename,
            container_image_filename,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def add_label_record(data, size_key, output_filename):
    """บันทึกการสร้างฉลากภาชนะบรรจุ 1 ครั้ง (สำหรับ dashboard สถิติ) คืนค่า id ที่สร้าง"""
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO label_records (product_name, cas, un, size_key, output_filename, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            data.get("product_name", "-"),
            data.get("cas", "-"),
            data.get("un", "-"),
            size_key,
            output_filename,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def get_dashboard_stats():
    """
    สรุปสถิติการใช้งานสำหรับ dashboard หน้า Admin: จำนวนที่สร้างทั้งหมด/วันนี้/7 วันล่าสุด
    (แยกฟอร์ม SDS ติดหน้างาน กับฉลากภาชนะบรรจุ), สารเคมีที่สร้างบ่อยที่สุด 5 อันดับ (นับรวมทั้ง 2 แบบ),
    และกิจกรรมรายวันย้อนหลัง 14 วัน (นับรวมทั้ง 2 แบบ) สำหรับวาดกราฟแท่ง
    หมายเหตุ: นี่คือสถิติการ "สร้าง PDF สำเร็จ" ไม่ใช่จำนวนคนเข้าเว็บ (ระบบไม่ได้เก็บ log การเข้าเว็บ)
    และถ้า deploy บน Render free tier ข้อมูลจะหายทุกครั้งที่ redeploy (ดู README หัวข้อ Deploy)
    """
    conn = get_connection()
    now = datetime.now()
    today_prefix = now.strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).isoformat(timespec="seconds")

    def count(table, where="", params=()):
        sql = f"SELECT COUNT(*) AS c FROM {table}" + (f" WHERE {where}" if where else "")
        return conn.execute(sql, params).fetchone()["c"]

    stats = {
        "total_sds": count("records"),
        "total_labels": count("label_records"),
        "today_sds": count("records", "created_at LIKE ?", (today_prefix + "%",)),
        "today_labels": count("label_records", "created_at LIKE ?", (today_prefix + "%",)),
        "week_sds": count("records", "created_at >= ?", (week_ago,)),
        "week_labels": count("label_records", "created_at >= ?", (week_ago,)),
    }

    top_rows = conn.execute("""
        SELECT name, COUNT(*) as cnt FROM (
            SELECT trade_name AS name FROM records
            WHERE trade_name IS NOT NULL AND trade_name NOT IN ('-', '')
            UNION ALL
            SELECT product_name AS name FROM label_records
            WHERE product_name IS NOT NULL AND product_name NOT IN ('-', '')
        )
        GROUP BY name ORDER BY cnt DESC LIMIT 5
    """).fetchall()
    stats["top_chemicals"] = [dict(r) for r in top_rows]

    days_ago_13 = (now - timedelta(days=13)).strftime("%Y-%m-%d")
    daily_rows = conn.execute("""
        SELECT day, SUM(cnt) AS total FROM (
            SELECT strftime('%Y-%m-%d', created_at) AS day, COUNT(*) AS cnt
            FROM records WHERE created_at >= ? GROUP BY day
            UNION ALL
            SELECT strftime('%Y-%m-%d', created_at) AS day, COUNT(*) AS cnt
            FROM label_records WHERE created_at >= ? GROUP BY day
        )
        GROUP BY day ORDER BY day
    """, (days_ago_13, days_ago_13)).fetchall()
    daily_map = {r["day"]: r["total"] for r in daily_rows}
    # เติมวันที่ไม่มีข้อมูลด้วย 0 ให้กราฟต่อเนื่องครบ 14 วัน (ไม่ใช่แค่วันที่มีข้อมูลจริง)
    stats["daily_activity"] = [
        {"date": (now - timedelta(days=i)).strftime("%d/%m"), "count": daily_map.get((now - timedelta(days=i)).strftime("%Y-%m-%d"), 0)}
        for i in range(13, -1, -1)
    ]
    # ค่าสูงสุดใน 14 วัน ใช้คำนวณสัดส่วนความสูงแท่งกราฟฝั่ง template (กัน max=0 หารด้วยศูนย์)
    stats["max_daily"] = max((d["count"] for d in stats["daily_activity"]), default=0) or 1

    conn.close()
    return stats


def list_records():
    """คืนรายการทะเบียนสารเคมีทั้งหมด เรียงใหม่สุดก่อน"""
    conn = get_connection()
    rows = conn.execute("SELECT * FROM records ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_record(record_id):
    """คืนทะเบียนสารเคมี 1 รายการตาม id หรือ None ถ้าไม่เจอ"""
    conn = get_connection()
    row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_record(record_id, data):
    """แก้ไขข้อมูลของทะเบียนสารเคมี (ไม่แตะไฟล์ PDF/รูปเดิม)"""
    import json
    conn = get_connection()
    conn.execute(
        """UPDATE records SET trade_name=?, cas=?, un=?, data_json=? WHERE id=?""",
        (
            data.get("trade_name", "-"),
            data.get("cas", "-"),
            data.get("un", "-"),
            json.dumps(data, ensure_ascii=False),
            record_id,
        ),
    )
    conn.commit()
    conn.close()


def delete_record(record_id):
    """ลบทะเบียนสารเคมี 1 รายการ (ไม่ลบไฟล์ PDF/รูปบนดิสก์ ให้ Admin ลบเองถ้าต้องการ)"""
    conn = get_connection()
    conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
    conn.commit()
    conn.close()
