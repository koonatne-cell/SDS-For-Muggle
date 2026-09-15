// label.js - หน้าสร้างฉลากภาชนะบรรจุสารเคมี (แยกจาก app.js ของหน้า SDS ติดหน้างาน)

const parseBtn = document.getElementById("parseBtn");
const parseStatus = document.getElementById("parseStatus");
const translateBtn = document.getElementById("translateBtn");
const translateEnBtn = document.getElementById("translateEnBtn");
const translateStatus = document.getElementById("translateStatus");
const generateBtn = document.getElementById("generateBtn");
const generateStatus = document.getElementById("generateStatus");
const downloadLink = document.getElementById("downloadLink");
const labelForm = document.getElementById("labelForm");
const labelSize = document.getElementById("labelSize");
const customSizeRow = document.getElementById("customSizeRow");

// แสดงช่องกรอกกว้าง/สูงเองเฉพาะตอนเลือก "กำหนดขนาดเอง" เท่านั้น
labelSize.addEventListener("change", () => {
    customSizeRow.classList.toggle("hidden", labelSize.value !== "custom");
});

function setStatus(el, text, kind) {
    el.textContent = text;
    el.className = "status " + (kind || "");
}

// แปลง list ของข้อความ (เช่น hazard_statements จาก /api/parse) เป็นข้อความหลายบรรทัดใส่ใน textarea
function listToLines(list) {
    return Array.isArray(list) ? list.join("\n") : (list || "");
}

// แปลงกลับจาก textarea (บรรทัดละ 1 ข้อ) เป็น list ตอนส่งไปสร้าง PDF
function linesToList(text) {
    return (text || "").split("\n").map((s) => s.trim()).filter((s) => s.length > 0);
}

// เอาข้อมูลที่ /api/parse ดึงมาได้ (SDS เดิม) มาเติมในฟอร์มฉลาก - คนละชุดฟิลด์กับฟอร์ม SDS ติดหน้างาน
function fillLabelForm(data) {
    const productName = (data.display_name && data.display_name !== "-") ? data.display_name : (data.trade_name || "");
    document.getElementById("f_product_name").value = productName === "-" ? "" : productName;
    document.getElementById("f_cas").value = (data.cas && data.cas !== "-") ? data.cas : "";
    document.getElementById("f_un").value = (data.un && data.un !== "-") ? data.un : "";
    // ถ้าดึงจาก SDS ไม่ได้ ให้คงค่าเริ่มต้น "อันตราย" ไว้ (ผู้ใช้ขอให้เติมไว้ให้ก่อนเสมอ แก้เป็น
    // "คำเตือน"/Warning เองได้ในฟอร์มถ้าสารนั้นไม่ใช่ระดับอันตราย)
    document.getElementById("f_signal_word").value = (data.signal_word && data.signal_word !== "-") ? data.signal_word : "อันตราย";
    document.getElementById("f_hazardous_substances").value = listToLines(data.hazardous_substances);
    document.getElementById("f_hazard_statements").value = listToLines(data.hazard_statements);
    document.getElementById("f_precautionary_statements").value = listToLines(data.precautionary_statements);
    document.getElementById("f_supplier_name").value = (data.supplier_name && data.supplier_name !== "-") ? data.supplier_name : "";
    // หมายเหตุ: ปิดการติ๊กสัญลักษณ์ GHS อัตโนมัติไว้ก่อน (เดาจากคำในไฟล์ยังไม่แม่นพอ) ให้ผู้ใช้ติ๊กเลือกเอง
    // ทั้งหมด - ถ้าจะเปิดกลับมาทีหลัง ดึง data.pictograms ที่ backend ส่งมาแล้วก็อปมาติ๊กเหมือน app.js ได้เลย
}

function collectLabelData() {
    const data = {
        product_name: document.getElementById("f_product_name").value,
        cas: document.getElementById("f_cas").value,
        un: document.getElementById("f_un").value,
        signal_word: document.getElementById("f_signal_word").value,
        hazardous_substances: linesToList(document.getElementById("f_hazardous_substances").value),
        hazard_statements: linesToList(document.getElementById("f_hazard_statements").value),
        precautionary_statements: linesToList(document.getElementById("f_precautionary_statements").value),
        supplier_name: document.getElementById("f_supplier_name").value,
        size_key: labelSize.value,
        custom_width_mm: document.getElementById("customWidth").value,
        custom_height_mm: document.getElementById("customHeight").value,
    };
    data.pictograms = Array.from(
        labelForm.querySelectorAll("input[name=pictograms]:checked")
    ).map((el) => el.value);
    return data;
}

// ---------- ขั้นตอนที่ 1: ดึงข้อมูลจาก SDS (ใช้ /api/parse ตัวเดียวกับหน้า SDS ติดหน้างาน) ----------
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
        fillLabelForm(json.data);
        setStatus(parseStatus, "ดึงข้อมูลสำเร็จ – ตรวจ/แก้ไขด้านล่างก่อนสร้างฉลาก (Hazard/Precautionary statement " +
            "อาจดึงมาไม่ครบ ถ้า SDS ไม่ได้ระบุตรงๆ ให้พิมพ์เพิ่มเอง)", "ok");
    } catch (ex) {
        setStatus(parseStatus, "ผิดพลาด: " + ex.message, "err");
    } finally {
        parseBtn.disabled = false;
    }
});

// ---------- แปลเป็นไทย / แปลเป็นอังกฤษ (ไม่แปล product_name/cas/un/supplier_name/emergency_phone
// เพราะเป็นชื่อเฉพาะ/รหัส/เบอร์โทร ดู LABEL_TRANSLATABLE_KEYS ใน fields.py) ----------
async function runTranslate(direction) {
    setStatus(translateStatus, "กำลังแปล...", "busy");
    translateBtn.disabled = true;
    translateEnBtn.disabled = true;
    try {
        const data = collectLabelData();
        const res = await fetch("/api/translate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ data, keys: LABEL_TRANSLATABLE_KEYS, direction }),
        });
        if (!res.ok) throw new Error("แปลไม่สำเร็จ");
        const json = await res.json();
        fillLabelForm(json.data);
        setStatus(translateStatus, "แปลเสร็จแล้ว – ตรวจคำแปลอีกครั้งก่อนสร้างฉลาก", "ok");
    } catch (ex) {
        setStatus(translateStatus, "ผิดพลาด: " + ex.message, "err");
    } finally {
        translateBtn.disabled = false;
        translateEnBtn.disabled = false;
    }
}
translateBtn.addEventListener("click", () => runTranslate("en_to_th"));
translateEnBtn.addEventListener("click", () => runTranslate("th_to_en"));

// ---------- ขั้นตอนที่ 3: สร้าง + ดาวน์โหลด PDF ฉลาก ----------
generateBtn.addEventListener("click", async () => {
    setStatus(generateStatus, "กำลังสร้าง PDF...", "busy");
    generateBtn.disabled = true;
    downloadLink.classList.add("hidden");

    try {
        const data = collectLabelData();
        const formData = new FormData();
        // ส่งเป็น Blob (ไฟล์) แทนข้อความล้วน กัน multipart decode ภาษาไทยผิดเป็น latin-1
        const dataBlob = new Blob([JSON.stringify(data)], { type: "application/json" });
        formData.append("data", dataBlob, "data.json");

        const res = await fetch("/api/label/generate", { method: "POST", body: formData });
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
