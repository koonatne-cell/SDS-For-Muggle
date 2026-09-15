# -*- coding: utf-8 -*-
"""
auth.py - เช็คสิทธิ์ผู้ดูแล (Admin)
พนักงาน (Employee) ใช้งานได้ทันทีไม่ต้อง login
Admin ต้องกรอก "รหัสผู้ดูแล" ให้ตรงกับค่าที่ตั้งไว้ใน environment variable ADMIN_PASSCODE
เมื่อกรอกถูก ระบบจะสร้าง session cookie ให้ (ผ่าน SessionMiddleware ที่ตั้งใน main.py)
"""
import os

ADMIN_PASSCODE = os.environ.get("ADMIN_PASSCODE", "")


def check_passcode(passcode):
    """เทียบรหัสที่ผู้ใช้กรอกกับรหัสจริงใน .env คืน True/False"""
    if not ADMIN_PASSCODE:
        # ถ้ายังไม่ได้ตั้งรหัสไว้เลย ป้องกันไว้ก่อนโดยให้ล็อกอินไม่ผ่านเสมอ
        return False
    return passcode == ADMIN_PASSCODE


def is_admin(request):
    """เช็คจาก session ว่าผู้ใช้คนนี้ login เป็น admin ไว้แล้วหรือยัง"""
    return bool(request.session.get("is_admin"))
