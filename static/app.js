// app.js - จัดการฟอร์มฝั่งเว็บ (ไม่มี framework ใช้ JS ธรรมดา)

const parseBtn = document.getElementById("parseBtn");
const parseStatus = document.getElementById("parseStatus");
const translateBtn = document.getElementById("translateBtn");
const translateEnBtn = document.getElementById("translateEnBtn");
const translateStatus = document.getElementById("translateStatus");
const generateBtn = document.getElementById("generateBtn");
const generateStatus = document.getElementById("generateStatus");
const downloadLink = document.getElementById("downloadLink");
const sdsForm = document.getElementById("sdsForm");

function setStatus(el, text, kind) {
    el.textContent = text;
    el.className = "status " + (kind || "");
}

// อ่านค่าปัจจุบันจากฟอร์มทั้งหมด คืนเป็น dict {key: value} (pictograms/ppe เป็น array แยกต่างหาก)
function collectFormData() {
    const data = {};
    const fields = sdsForm.querySelectorAll("textarea, input[type=text], select");
    fields.forEach((el) => { data[el.name] = el.value; });
    data.pictograms = Array.from(
        sdsForm.querySelectorAll("input[name=pictograms]:checked")
    ).map((el) => el.value);
    data.ppe = Array.from(
        sdsForm.querySelectorAll("input[name=ppe]:checked")
    ).map((el) => el.value);
    return data;
}

// เอา dict ข้อมูลไปใส่ในช่องฟอร์ม
// หมายเหตุ: ปิดการติ๊กสัญลักษณ์ GHS อัตโนมัติไว้ก่อน (เดาจากคำในไฟล์ยังไม่แม่นพอ) ให้ผู้ใช้ติ๊กเลือกเอง
// ทั้งหมด - ถ้าจะเปิดกลับมาทีหลัง ดึง data.pictograms ที่ backend ส่งมาแล้วก็อปมาติ๊กเหมือนฟิลด์อื่นได้เลย
// PPE ต่างจาก pictograms ตรงที่ผู้ใช้ขอให้ติ๊กอัตโนมัติจากข้อความ Section 8 เลย (ดู detect_ppe ใน
// parser.py) ผู้ใช้ยังแก้ไข/ติ๊กเพิ่ม-ถอดเองได้เสมอหลังจากนั้น
function fillFormData(data) {
    Object.keys(data).forEach((key) => {
        if (key === "pictograms" || key === "ppe") return;
        const el = document.getElementById("f_" + key);
        if (el) el.value = data[key] ?? "";
    });
    (data.ppe || []).forEach((key) => {
        const el = document.getElementById("ppe_" + key);
        if (el) el.checked = true;
    });
}

// ---------- ขั้นตอนที่ 1: ดึงข้อมูลจาก SDS ----------
parseBtn.addEventListener("click", async () => {
    const fileInput = document.getElementById("sdsFile");
    if (!fileInput.files.length) {
        setStatus(parseStatus, "กรุณาเลือกไฟล์ SDS (.pdf) ก่อน", "err");
        return;
    }
    setStatus(parseStatus, "กำลังดึงข้อมูล...", "busy");
    parseBtn.disabled = true;

    const formData = new FormData();
    formData.append("sds_file", fileInput.files[0]);

    try {
        const res = await fetch("/api/parse", { method: "POST", body: formData });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || "ดึงข้อมูลไม่สำเร็จ");
        }
        const json = await res.json();
        fillFormData(json.data);
        setStatus(parseStatus, "ดึงข้อมูลสำเร็จ – ตรวจ/แก้ไขด้านล่างก่อนสร้าง PDF", "ok");
    } catch (ex) {
        setStatus(parseStatus, "ผิดพลาด: " + ex.message, "err");
    } finally {
        parseBtn.disabled = false;
    }
});

// ---------- ขั้นตอนที่ 2: แปลเป็นไทย / แปลเป็นอังกฤษ (สลับกลับได้ทั้งสองทาง) ----------
async function runTranslate(direction, btn) {
    setStatus(translateStatus, "กำลังแปล...", "busy");
    translateBtn.disabled = true;
    translateEnBtn.disabled = true;
    try {
        const data = collectFormData();
        const res = await fetch("/api/translate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ data, direction }),
        });
        if (!res.ok) throw new Error("แปลไม่สำเร็จ");
        const json = await res.json();
        fillFormData(json.data);
        setStatus(translateStatus, "แปลเสร็จแล้ว – ตรวจคำแปลอีกครั้งก่อนสร้าง PDF", "ok");
    } catch (ex) {
        setStatus(translateStatus, "ผิดพลาด: " + ex.message, "err");
    } finally {
        translateBtn.disabled = false;
        translateEnBtn.disabled = false;
    }
}
translateBtn.addEventListener("click", () => runTranslate("en_to_th"));
translateEnBtn.addEventListener("click", () => runTranslate("th_to_en"));

// ---------- ขั้นตอนที่ 3: พรีวิวรูปภาพ ----------
function setupImagePreview(inputId, previewId) {
    const input = document.getElementById(inputId);
    const preview = document.getElementById(previewId);
    input.addEventListener("change", () => {
        if (input.files.length) {
            preview.src = URL.createObjectURL(input.files[0]);
            preview.classList.remove("hidden");
        } else {
            preview.classList.add("hidden");
        }
    });
}
setupImagePreview("labelImage", "labelImagePreview");
setupImagePreview("containerImage", "containerImagePreview");

// ---------- ช่องบังคับกรอกก่อนสร้าง PDF ได้ (ชื่อผู้จัดทำ + ตำแหน่ง) ----------
// ผู้ใช้ขอให้กั้นไว้จริงๆ เฉพาะชื่อ/ตำแหน่งผู้จัดทำเท่านั้น ส่วนรูปฉลาก/รูปภาชนะ (เดิมบังคับด้วย)
// ผู้ใช้ขอให้เปลี่ยนเป็น "แค่เตือน" แทน - ถ้ายืนยันว่าจะไม่แนบรูปก็ให้สร้าง PDF ต่อได้ (ดู confirm()
// ใน generateBtn ด้านล่าง) ยังคงไฮไลท์แดงไว้ให้เห็นว่าลืมแนบ แต่ไม่บล็อกการสร้างอีกต่อไป
const REQUIRED_TEXT_KEYS = ["prepared_name", "prepared_position"];
const OPTIONAL_IMAGE_IDS = ["labelImage", "containerImage"];

function clearFieldMissing(el) {
    el.classList.remove("field-missing");
    const wrap = el.closest(".field-row") || el.closest(".image-box");
    if (wrap) wrap.classList.remove("field-missing");
}

function markFieldMissing(el) {
    el.classList.add("field-missing");
    const wrap = el.closest(".field-row") || el.closest(".image-box");
    if (wrap) wrap.classList.add("field-missing");
}

// ลบไฮไลท์แดงทันทีที่ผู้ใช้เริ่มแก้ช่องนั้น (ไม่ต้องรอกด "สร้าง PDF" ซ้ำถึงจะรู้ว่าแก้ครบแล้ว)
REQUIRED_TEXT_KEYS.forEach((key) => {
    const el = document.getElementById("f_" + key);
    if (el) el.addEventListener("input", () => { if (el.value.trim()) clearFieldMissing(el); });
});
["labelImage", "containerImage"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", () => { if (el.files.length) clearFieldMissing(el); });
});

// เช็คเฉพาะชื่อ/ตำแหน่งผู้จัดทำ (บล็อกจริงถ้าไม่กรอก) - คืน array ของ element ที่ยังขาด
function validateRequiredFields() {
    const missing = [];
    REQUIRED_TEXT_KEYS.forEach((key) => {
        const el = document.getElementById("f_" + key);
        if (!el) return;
        if (el.value.trim()) {
            clearFieldMissing(el);
        } else {
            markFieldMissing(el);
            missing.push(el);
        }
    });
    return missing;
}

// เช็ครูปฉลาก/รูปภาชนะ (แค่ไฮไลท์เตือน ไม่บล็อก) - คืน array ของ element ที่ยังไม่แนบ
function checkOptionalImages() {
    const missing = [];
    OPTIONAL_IMAGE_IDS.forEach((id) => {
        const el = document.getElementById(id);
        if (!el) return;
        if (el.files.length) {
            clearFieldMissing(el);
        } else {
            markFieldMissing(el);
            missing.push(el);
        }
    });
    return missing;
}

// ---------- ขั้นตอนที่ 4: สร้าง + ดาวน์โหลด PDF ----------
generateBtn.addEventListener("click", async () => {
    const missing = validateRequiredFields();
    if (missing.length > 0) {
        setStatus(generateStatus, "โปรดกรอกข้อมูลให้ครบ (ช่องที่ไฮไลท์สีแดง)", "err");
        alert("โปรดกรอกข้อมูลให้ครบ: ชื่อผู้จัดทำ และตำแหน่ง");
        missing[0].scrollIntoView({ behavior: "smooth", block: "center" });
        return;
    }

    const missingImages = checkOptionalImages();
    if (missingImages.length > 0) {
        const proceed = confirm(
            "ยังไม่ได้แนบรูปฉลากสารเคมี และ/หรือ รูปภาชนะบรรจุสารเคมี\n" +
            "ต้องการสร้าง PDF ต่อโดยไม่มีรูปหรือไม่?"
        );
        if (!proceed) {
            setStatus(generateStatus, "ยังไม่ได้แนบรูป (ช่องที่ไฮไลท์สีแดง)", "err");
            missingImages[0].scrollIntoView({ behavior: "smooth", block: "center" });
            return;
        }
    }

    setStatus(generateStatus, "กำลังสร้าง PDF...", "busy");
    generateBtn.disabled = true;
    downloadLink.classList.add("hidden");

    try {
        const data = collectFormData();
        const formData = new FormData();
        // ส่งเป็น Blob (ไฟล์) แทนข้อความล้วน กัน multipart decode ภาษาไทยผิดเป็น latin-1
        const dataBlob = new Blob([JSON.stringify(data)], { type: "application/json" });
        formData.append("data", dataBlob, "data.json");

        const labelFile = document.getElementById("labelImage").files[0];
        const containerFile = document.getElementById("containerImage").files[0];
        if (labelFile) formData.append("label_image", labelFile);
        if (containerFile) formData.append("container_image", containerFile);

        const res = await fetch("/api/generate", { method: "POST", body: formData });
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || "สร้าง PDF ไม่สำเร็จ");
        }
        const json = await res.json();
        downloadLink.href = json.download_url;
        downloadLink.classList.remove("hidden");
        setStatus(generateStatus, "สร้างเสร็จแล้ว กดปุ่ม \"ดาวน์โหลด PDF\" เพื่อบันทึกลงเครื่อง", "ok");
    } catch (ex) {
        setStatus(generateStatus, "ผิดพลาด: " + ex.message, "err");
    } finally {
        generateBtn.disabled = false;
    }
});
