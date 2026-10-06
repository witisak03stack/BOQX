import streamlit as st
import pandas as pd
import math
import io
import json
import os
import copy
import re

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import pytesseract
    from PIL import Image
except ImportError:
    pytesseract = None
    Image = None

# ---------------------------------------------------------
# 1. Page Configuration & Custom CSS
# ---------------------------------------------------------
st.set_page_config(
    page_title="AI ถอด BOQ งานโครงสร้าง & สถาปัตย์ V8.0 Drawing Reader + Accuracy",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;500;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Prompt', sans-serif;
    }
    
    .block-container {
        padding-top: 1.5rem !important;
        padding-bottom: 2rem !important;
        padding-left: 2rem !important;
        padding-right: 2rem !important;
        max-width: 100% !important;
    }

    .header-banner {
        background: linear-gradient(135deg, #1e3a8a 0%, #2563eb 50%, #3b82f6 100%);
        color: white;
        padding: 20px 24px;
        border-radius: 12px;
        margin-bottom: 20px;
        box-shadow: 0 4px 15px rgba(37, 99, 235, 0.2);
        border: 1px solid rgba(255, 255, 255, 0.15);
        width: 100%;
        box-sizing: border-box;
    }
    .header-title {
        font-size: 1.5rem;
        font-weight: 700;
        margin: 0;
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .header-subtitle {
        font-size: 0.95rem;
        opacity: 0.95;
        margin-top: 6px;
        font-weight: 400;
        background: rgba(255, 255, 255, 0.15);
        display: inline-block;
        padding: 3px 10px;
        border-radius: 6px;
    }

    .stButton>button {
        border-radius: 6px;
        font-weight: 500;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 2. Standard Dictionaries & Data Mappings
# ---------------------------------------------------------
REBAR_WEIGHT = {
    "RB6": 0.222,
    "DB10": 0.617,
    "RB9": 0.499,
    "DB12": 0.888,
    "DB16": 1.580,
    "DB20": 2.470,
    "DB25": 3.850,
    "DB28": 4.830,
    "DB32": 6.310
}
REBAR_LIST = list(REBAR_WEIGHT.keys())


# เส้นผ่านศูนย์กลางจริงของเหล็ก (มม.) ใช้สำหรับคำนวณ Cover (ระยะหุ้มคอนกรีต)/Lap/Development
REBAR_DIAMETER_MM = {
    "RB6": 6, "RB9": 9, "DB10": 10,
    "DB12": 12, "DB16": 16, "DB20": 20,
    "DB25": 25, "DB28": 28, "DB32": 32
}

# น้ำหนักมาตรฐานโดยประมาณของเหล็กรูปพรรณที่พบได้บ่อย (กก./ม.)
# ผู้ใช้กรอก น้ำหนักต่อเมตร เองได้เสมอ เพื่ออ้างอิงตารางเหล็ก/แบบโครงสร้างจริง
COMMON_STEEL_KG_M = {
    "C-75x45x15x2.3": 2.44,
    "C-100x50x20x2.3": 3.17,
    "C-125x50x20x3.2": 5.89,
    "C-150x50x20x3.2": 6.51,
    "C-200x75x20x3.2": 8.84,
    "H-150x150x7x10": 31.5,
    "H-200x150x5.5x8": 25.4,
    "H-300x150x6.5x9": 36.7,
    "Tube 75x75x3.2": 6.97,
    "Tube 100x100x3.2": 9.67,
    "Tube 125x125x4.5": 16.5,
    "Tube 150x150x4.5": 20.1,
}


def rebar_diameter_mm(rebar_type):
    return REBAR_DIAMETER_MM.get(str(rebar_type).upper(), 0.0)


def lap_or_dev_len_m(rebar_type, factor_d):
    """ระยะทาบ/ระยะพัฒนาแบบปรับค่า factor ได้ (m). เป็นค่าใช้งานประมาณการ; แบบ/Spec มีผลเหนือกว่า"""
    return rebar_diameter_mm(rebar_type) * max(0.0, safe_num(factor_d)) / 1000.0


def clear_bar_length_m(span_m, cover_mm):
    return max(0.0, safe_num(span_m) - 2.0 * max(0.0, safe_num(cover_mm)) / 1000.0)


def count_by_spacing_clear(span, spacing, cover_mm=0.0):
    """นับเหล็กจากระยะ @ โดยหัก Cover (ระยะหุ้มคอนกรีต) สองด้านก่อน"""
    clear_span = clear_bar_length_m(span, cover_mm)
    return count_by_spacing(clear_span, spacing) if clear_span > 0 else 0


def stirrup_cut_length_m(member_w, member_h, cover_mm, hook_extra_m=0.10):
    """ความยาวตัดปลอกแบบง่าย = รอบแกนปลอกภายใน + เผื่อขอ/ตะขอ"""
    clear_w = max(0.0, safe_num(member_w) - 2.0 * max(0.0, safe_num(cover_mm)) / 1000.0)
    clear_h = max(0.0, safe_num(member_h) - 2.0 * max(0.0, safe_num(cover_mm)) / 1000.0)
    return 2.0 * (clear_w + clear_h) + max(0.0, safe_num(hook_extra_m))


def steel_row_length(row, base_len_m, count_span_m=None, cover_mm=0.0,
                     lap_mode="ไม่มี", lap_ends=0, auto_geometry=False,
                     member_w=None, member_h=None, hook_extra_m=0.10,
                     lap_factor_d=40.0, dev_factor_d=40.0):
    """คำนวณความยาวรวมแถวเหล็ก โดยรองรับจำนวน, @, Cover (ระยะหุ้มคอนกรีต) และการต่อเหล็ก"""
    rtype = row.get("type", "RB9")
    mode = row.get("mode", "จำนวน (เส้น)")
    val = max(0.0, safe_num(row.get("val", 0)))

    if auto_geometry and row.get("pos") not in ["เหล็กเสริมพิเศษ", "เหล็กคอมเมนท์/เหล็กเสริมพิเศษ"]:
        base = clear_bar_length_m(base_len_m, cover_mm)
        if "ปลอก" in str(row.get("pos", "")) and member_w is not None and member_h is not None:
            base = stirrup_cut_length_m(member_w, member_h, cover_mm, hook_extra_m)
    else:
        base = max(0.0, safe_num(row.get("len", base_len_m)))

    if mode == "จำนวน (เส้น)":
        count = int(round(val)) if val > 0 else 0
    else:
        count = count_by_spacing_clear(count_span_m if count_span_m is not None else base_len_m, val, cover_mm)

    if lap_mode == "ทาบ":
        extra_per_bar = lap_or_dev_len_m(rtype, lap_factor_d)
    elif lap_mode == "พัฒนา/ฝัง":
        extra_per_bar = lap_or_dev_len_m(rtype, dev_factor_d)
    else:
        extra_per_bar = 0.0

    ends = int(max(0, min(2, round(safe_num(lap_ends)))))
    if "ปลอก" in str(row.get("pos", "")):
        ends = 0

    return max(0.0, count * (base + ends * extra_per_bar))


def item_signature(item):
    """สร้าง signature สำหรับตรวจรายการซ้ำแบบ exact/near-exact โดยไม่ใช้ยอดเงินเป็นตัวตัดสิน"""
    return (
        str(item.get("หมวด", "")).strip(),
        str(item.get("รายการ", "")).strip(),
        get_item_source(item),
        str(item.get("รายละเอียด", "")).strip(),
        round(safe_num(item.get("จำนวน", 0)), 4) if isinstance(item.get("จำนวน", 0), (int, float)) else str(item.get("จำนวน", "")),
    )


def find_duplicate_item(item_data):
    p_idx = get_current_project_index()
    if p_idx == -1:
        return None
    sig = item_signature(item_data)
    for existing in st.session_state["projects"][p_idx].get("items", []):
        if item_signature(existing) == sig:
            return existing
    return None

def find_near_duplicate_items(item_data):
    p_idx = get_current_project_index()
    if p_idx == -1:
        return []
    target = (str(item_data.get("หมวด", "")).strip(), str(item_data.get("รายการ", "")).strip(), get_item_source(item_data))
    if not target[0] or not target[1] or not target[2]:
        return []
    hits=[]
    for existing in st.session_state["projects"][p_idx].get("items", []):
        ex=(str(existing.get("หมวด", "")).strip(),str(existing.get("รายการ", "")).strip(),get_item_source(existing))
        if ex==target:
            hits.append(existing)
    return hits

def get_rebar_price(rebar_type, db_price, rb_price):
    """คืนราคาต่อกก. ตามชนิดเหล็ก"""
    return db_price if str(rebar_type).upper().startswith("DB") else rb_price

def safe_num(value, default=0.0):
    """แปลงค่าตัวเลขจากข้อมูลเดิม/JSON ให้ปลอดภัย"""
    try:
        x = float(value)
        return x if math.isfinite(x) else float(default)
    except (TypeError, ValueError):
        return float(default)

def get_item_source(item):
    return str(item.get("ที่มาในแบบ") or item.get("อ้างอิงแบบ") or "").strip()

# ---------------------------------------------------------
# Drawing Reader (PDF -> review candidates)
# ---------------------------------------------------------
STRUCTURAL_MARK_RE = re.compile(r"(?<![A-Za-z0-9])([FCBSWRD]\s*\d+[A-Za-z]?)\b", re.I)
STRUCTURAL_DASH_MARK_RE = re.compile(r"(?<![A-Za-z0-9])([FCBWRD]\s*[-.]\s*\d+[A-Za-z]?)\b", re.I)
DIM_RE = re.compile(r"(?<![A-Za-z0-9])([0-9]+(?:[.,][0-9]+)?)\s*[x*]\s*([0-9]+(?:[.,][0-9]+)?)(?:\s*[x*]\s*([0-9]+(?:[.,][0-9]+)?))?", re.I)
REBAR_COUNT_RE = re.compile(r"(?:^|\s|[,(])([0-9]{1,3})\s*[-x×]?\s*(DB\s*\d+|RB\s*\d+)\b", re.I)
REBAR_SPACING_RE = re.compile(r"\b(DB\s*\d+|RB\s*\d+|D\s*\d+)\s*@\s*([0-9]+(?:[.,][0-9]+)?)\s*(mm|cm|m)?", re.I)
LENGTH_RE = re.compile(r"(?:\bL\b|ยาว|length|span)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\s*(mm|cm|m|เมตร)?", re.I)
HEIGHT_RE = re.compile(r"(?:\bH\b|สูง|height|ลึก|หนา|th)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\s*(mm|cm|m|เมตร)?", re.I)
QTY_RE = re.compile(r"(?:จำนวน|qty|quantity|no\.?|nos\.?)\s*[:=x×]?\s*([0-9]+(?:\.[0-9]+)?)", re.I)
AREA_RE = re.compile(r"(?:พื้นที่|area)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:ตร\.?ม\.?|m2|m²|sqm)?", re.I)
SHEET_RE = re.compile(r"\b(?:S|ST|A|AR|STR|ARCH)[-\s]+\d{1,4}\b", re.I)
GRID_RE = re.compile(r"\bGrid\s*[A-Z0-9]+(?:\s*[-/]\s*[A-Z0-9]+)\b", re.I)
STEEL_PROFILE_RE = re.compile(r"\b(?:H|C|Tube)\s*[-]?\s*[0-9]+(?:\.[0-9]+)?(?:\s*[xX]\s*[0-9]+(?:\.[0-9]+)?){2,4}(?:\.[0-9]+)?\b", re.I)

def normalize_drawing_text(text):
    text = str(text or "").replace("\u00a0", " ")
    text = text.replace("×", "x").replace("✕", "x").replace("−", "-").replace("–", "-").replace("—", "-")
    # OCR มักอ่านเลข 1 ในรหัสสมาชิก เช่น F1/C1/B1 เป็นตัว l
    # แก้เฉพาะรูปแบบรหัสสั้น ๆ เพื่อไม่กระทบคำทั่วไป
    text = re.sub(r"(?<![A-Za-z0-9])([FCBSWRD])\s*l\b", r"\g<1>1", text, flags=re.I)
    text = re.sub(r"(?<![A-Za-z0-9])([FCBWRD])\s*[-.]\s*[Il]\b", r"\g<1>-1", text, flags=re.I)
    text = re.sub(r"[ \t]+", " ", text)
    return text

def _num_token(value):
    try:
        return float(str(value).replace(",", "").replace(" ", ""))
    except Exception:
        return 0.0

def token_to_m(value, unit=None):
    v = _num_token(value)
    u = str(unit or "").lower()
    if not v:
        return 0.0
    if u in ("mm", "มม"):
        return v / 1000.0
    if u in ("cm", "ซม"):
        return v / 100.0
    if u in ("m", "เมตร"):
        return v
    # Drawing convention heuristic: 200 = 200 mm, 20 = 20 cm, 0.20 = 0.20 m
    if v >= 100:
        return v / 1000.0
    if v >= 10:
        return v / 100.0
    return v

def spacing_to_m(value, unit=None):
    v = _num_token(value)
    u = str(unit or "").lower()
    if u == "mm":
        return v / 1000.0
    if u == "cm":
        return v / 100.0
    if u in ("m", "เมตร"):
        return v
    if v >= 20:
        return v / 1000.0
    if v >= 2:
        return v / 100.0
    return v

def normalize_rebar_type(raw):
    x = re.sub(r"\s+", "", str(raw or "").upper())
    x = x.replace("D", "DB", 1) if x.startswith("D") and not x.startswith("DB") else x
    return x if x in REBAR_WEIGHT else None

def parse_rebar_specs(context):
    rows = []
    seen = set()
    for m in REBAR_SPACING_RE.finditer(context):
        rtype = normalize_rebar_type(m.group(1))
        if not rtype:
            continue
        spacing = spacing_to_m(m.group(2), m.group(3))
        local = context[max(0, m.start()-70):m.start()+50]
        if re.search(r"ปลอก|stirrup|tie", local, re.I):
            pos = "เหล็กปลอก"
        elif re.search(r"บน|top", local, re.I):
            pos = "เหล็กบน"
        elif re.search(r"ล่าง|bottom", local, re.I):
            pos = "เหล็กล่าง"
        else:
            pos = "เหล็กเสริมพิเศษ"
        key = (rtype, "spacing", round(spacing, 6), pos)
        if key not in seen and spacing > 0:
            rows.append({"pos": pos, "type": rtype, "mode": "ระยะห่าง (@ ม.)", "val": spacing, "len": 0.0, "lap_mode": "ไม่มี", "lap_ends": 0})
            seen.add(key)
    for m in REBAR_COUNT_RE.finditer(context):
        rtype = normalize_rebar_type(m.group(2))
        if not rtype:
            continue
        count = max(1, int(round(_num_token(m.group(1)))))
        local = context[max(0, m.start()-70):m.start()+50]
        if re.search(r"ปลอก|stirrup|tie", local, re.I):
            pos = "เหล็กปลอก"
        elif re.search(r"บน|top", local, re.I):
            pos = "เหล็กบน"
        elif re.search(r"ล่าง|bottom", local, re.I):
            pos = "เหล็กล่าง"
        else:
            pos = "เหล็กเสริมพิเศษ"
        key = (rtype, "count", count, pos)
        if key not in seen:
            rows.append({"pos": pos, "type": rtype, "mode": "จำนวน (เส้น)", "val": float(count), "len": 0.0, "lap_mode": "ไม่มี", "lap_ends": 0})
            seen.add(key)
    return rows

def parse_drawing_candidates(page_text, page_no):
    text = normalize_drawing_text(page_text)
    candidates = []
    matches = list(STRUCTURAL_MARK_RE.finditer(text)) + list(STRUCTURAL_DASH_MARK_RE.finditer(text))
    matches.sort(key=lambda m: m.start())
    for i, mark_match in enumerate(matches):
        raw_mark = mark_match.group(1)
        raw_clean = raw_mark.strip().upper()
        # C-3 / B-2 ที่ติดกับคำว่า Grid คือพิกัดกริด ไม่ใช่รหัสสมาชิก
        if ("-" in raw_clean or "." in raw_clean):
            local_before = text[max(0, mark_match.start()-24):mark_match.start()]
            if re.search(r"Grid\s*$", local_before, re.I):
                continue
            # C-1/B-1/F-1/R-1 ใช้ได้เมื่อมีคำบริบทเชิงโครงสร้างใกล้ ๆ; ลดการชนกับรหัสงานสถาปัตย์
            structural_context = re.search(r"เสา|คาน|ฐานราก|ฐาน|column|beam|footing|foundation|rebar|เหล็ก|คอนกรีต", text[max(0, mark_match.start()-100):min(len(text), mark_match.end()+160)], re.I)
            if not structural_context:
                continue
        if raw_clean.startswith("D") and re.match(r"\s*@", text[mark_match.end():mark_match.end()+4]):
            continue
        mark = re.sub(r"\s+", "", raw_mark.upper())
        kind = mark[0]
        target = {"F": "ฐานราก", "C": "เสา", "B": "คาน", "S": "พื้น", "W": "ผนัง", "R": "หลังคา", "D": "ประตู-หน้าต่าง"}.get(kind, "อื่นๆ")
        start = max(0, mark_match.start() - 80)
        end = min(len(text), matches[i + 1].start() if i + 1 < len(matches) else mark_match.end() + 420)
        source_ctx = text[max(0, mark_match.start() - 120):min(len(text), mark_match.end() + 240)]
        ctx = text[mark_match.end():end]
        if len(re.sub(r"\s+", "", ctx)) < 12:
            ctx = text[start:end]
        dims = []
        clean_ctx = STEEL_PROFILE_RE.sub(" ", ctx)
        for dm in DIM_RE.finditer(clean_ctx):
            vals = [dm.group(1), dm.group(2)] + ([dm.group(3)] if dm.group(3) else [])
            converted = [token_to_m(v) for v in vals]
            dims.append(converted)
            if len(dims) >= 2:
                break
        dim_values = dims[0] if dims else []
        length_m = 0.0
        lm = LENGTH_RE.search(ctx)
        if lm:
            length_m = token_to_m(lm.group(1), lm.group(2))
        height_m = 0.0
        hm = HEIGHT_RE.search(ctx)
        if hm:
            height_m = token_to_m(hm.group(1), hm.group(2))
        qty = 1.0
        qm = QTY_RE.search(ctx)
        if qm:
            qty = max(1.0, _num_token(qm.group(1)))
        area = 0.0
        am = AREA_RE.search(ctx)
        if am:
            area = _num_token(am.group(1))
        rebar_rows = parse_rebar_specs(ctx)
        steel_profiles = list(dict.fromkeys(re.sub(r"\s+", "", m.group(0)) for m in STEEL_PROFILE_RE.finditer(ctx)))
        # Interpret common schedule dimensions conservatively and by member type.
        w = l = h = 0.0
        if target == "คาน":
            if len(dim_values) >= 2:
                w, h = dim_values[0], dim_values[1]
            if len(dim_values) >= 3 and length_m <= 0:
                length_m = dim_values[2]
        else:
            if len(dim_values) >= 2:
                w, l = dim_values[0], dim_values[1]
            if len(dim_values) >= 3:
                h = dim_values[2]
        if target == "เสา" and height_m > 0:
            h = height_m
        if target == "ผนัง" and length_m <= 0 and len(dim_values) >= 1:
            length_m = dim_values[0]
        if target == "ผนัง" and height_m <= 0 and len(dim_values) >= 2:
            height_m = dim_values[1]
        score = 0.55
        if dim_values:
            score += 0.20
        if length_m > 0 or height_m > 0 or area > 0:
            score += 0.10
        if qty != 1:
            score += 0.05
        if rebar_rows:
            score += 0.10
        score = min(0.90, score)
        candidates.append({
            "page": int(page_no), "mark": mark, "target": target, "confidence": round(score, 2),
            "width_m": round(w, 4), "length_m": round(l, 4), "height_m": round(h, 4), "member_length_m": round(length_m, 4),
            "qty": round(qty, 3), "area_m2": round(area, 3), "rebar_rows": rebar_rows, "steel_profiles": steel_profiles,
            "source": drawing_source_tags(source_ctx, page_no, mark), "source_text": (source_ctx + "\n" + ctx[:520]).strip()[:700],
            "level_hint": (re.search(r"ชั้น\s*([1-9][0-9]*)|floor\s*([1-9][0-9]*)", source_ctx + " " + ctx, re.I).group(0) if re.search(r"ชั้น\s*([1-9][0-9]*)|floor\s*([1-9][0-9]*)", source_ctx + " " + ctx, re.I) else "")
        })
    # De-duplicate same mark on same page, retaining the richest candidate.
    best = {}
    for c in candidates:
        key = (c["page"], c["mark"])
        old = best.get(key)
        if old is None or (len(c.get("rebar_rows", [])), c.get("confidence", 0)) > (len(old.get("rebar_rows", [])), old.get("confidence", 0)):
            best[key] = c
    return list(best.values())

def read_drawing_pdf(file_bytes, use_ocr=True, max_pages=80, dpi=160):
    if fitz is None:
        raise RuntimeError("ยังไม่มี PyMuPDF (fitz) สำหรับอ่าน PDF")
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    page_texts = []
    ocr_pages = []
    for idx in range(min(len(doc), max_pages)):
        page = doc.load_page(idx)
        text = normalize_drawing_text(page.get_text("text") or "")
        # OCR เมื่อข้อความในหน้าไม่พอ หรือยังไม่พบรหัสสมาชิกโครงสร้างที่น่าจะต้องใช้
        should_ocr = use_ocr and pytesseract is not None and Image is not None and (
            len(re.sub(r"\s+", "", text)) < 40 or not STRUCTURAL_MARK_RE.search(text)
        )
        if should_ocr:
            try:
                scale = max(1.0, dpi / 72.0)
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                try:
                    langs = pytesseract.get_languages(config="")
                except Exception:
                    langs = []
                ocr_lang = "tha+eng" if "tha" in langs and "eng" in langs else ("tha" if "tha" in langs else "eng")
                ocr_text = normalize_drawing_text(pytesseract.image_to_string(img, lang=ocr_lang, config="--psm 11"))
                if not STRUCTURAL_MARK_RE.search(ocr_text) and not STRUCTURAL_DASH_MARK_RE.search(ocr_text):
                    retry_text = normalize_drawing_text(pytesseract.image_to_string(img, lang=ocr_lang, config="--psm 6"))
                    if len(re.sub(r"\s+", "", retry_text)) > len(re.sub(r"\s+", "", ocr_text)):
                        ocr_text = retry_text
                if len(re.sub(r"\s+", "", ocr_text)) > len(re.sub(r"\s+", "", text)) or STRUCTURAL_MARK_RE.search(ocr_text) or STRUCTURAL_DASH_MARK_RE.search(ocr_text):
                    text = ocr_text
                ocr_pages.append(idx + 1)
            except Exception:
                pass
        page_texts.append(text)
    candidates = []
    for page_no, text in enumerate(page_texts, start=1):
        if text.strip():
            candidates.extend(parse_drawing_candidates(text, page_no))
    page_count = len(doc)
    doc.close()
    return {"page_count": page_count, "pages_read": len(page_texts), "ocr_pages": ocr_pages, "candidates": candidates}

def candidate_summary(c):
    dims = ""
    if c.get("width_m") and c.get("length_m"):
        dims = f"{c['width_m']:.2f} x {c['length_m']:.2f} ม."
        if c.get("height_m"):
            dims += f" x {c['height_m']:.2f} ม."
    elif c.get("member_length_m"):
        dims = f"ยาว {c['member_length_m']:.2f} ม."
    elif c.get("area_m2"):
        dims = f"พื้นที่ {c['area_m2']:.2f} ตร.ม."
    rb = ", ".join((f"{r['val']:.0f} {r['type']}" if r["mode"].startswith("จำนวน") else f"{r['type']} @{r['val']:.2f} ม.") for r in c.get("rebar_rows", []))
    steel = ", ".join(c.get("steel_profiles", []))
    return dims or "ยังอ่านขนาดไม่ได้", rb or "ยังอ่านเหล็กไม่ได้", steel or "ยังไม่พบหน้าตัดโครงเหล็ก"

def drawing_source_tags(context, page_no, mark):
    sheets = list(dict.fromkeys(m.group(0).upper().replace(" ", "") for m in SHEET_RE.finditer(context)))
    grids = list(dict.fromkeys(m.group(0) for m in GRID_RE.finditer(context)))
    parts = [f"หน้า {page_no}"]
    if sheets:
        parts.append(f"แผ่นแบบ {sheets[0]}")
    if grids:
        grid_text = re.sub(r"^Grid\s*", "", grids[0], flags=re.I)
        parts.append(f"ตำแหน่ง {grid_text}")
    parts.append(mark)
    return " | ".join(parts)

DRAWING_TAB_KEYS = {
    "ฐานราก": ("f_name", "f_w", "f_l", "f_h", "f_qty", "f_ref"),
    "เสา": ("col_name", "col_w", "col_l", "col_h", "col_qty", "col_ref"),
    "คาน": ("b_name", "b_w", "b_h", "b_l", "b_qty", "b_ref"),
    "พื้น": ("s_name", "s_w", "s_l", "s_h", "s_qty", "s_ref"),
    "ผนัง": ("wall_name", "wall_l", "wall_h", "wall_qty", "wall_ref"),
    "ประตู-หน้าต่าง": ("dw_name", "dw_qty", "dw_ref"),
    "หลังคา": ("roof_name", "roof_manual_area", "roof_ref"),
}

def queue_drawing_candidate(candidate):
    st.session_state["drawing_prefill"] = copy.deepcopy(candidate)
    st.session_state["drawing_prefill_target"] = candidate.get("target")

def maybe_apply_drawing_prefill(target):
    cand = st.session_state.get("drawing_prefill")
    if not cand or cand.get("target") != target:
        return
    dims, rb, steel = candidate_summary(cand)
    st.info(f"📖 มีข้อมูลจากแบบพร้อมใช้ใน Tab {target}: **{cand.get('mark')}** | {dims} | เหล็ก: {rb} | โครงเหล็ก: {steel}\n\nกด ‘ใช้ข้อมูลจากแบบ’ เพื่อเติมช่องให้ก่อน แล้วตรวจความถูกต้องอีกครั้ง")
    if st.button(f"✅ ใช้ข้อมูลจากแบบ → {target}", key=f"apply_drawing_{target}"):
        mapping = {}
        if target == "ฐานราก":
            mapping = {"f_name": cand.get("mark", "F1"), "f_w": cand.get("width_m") or 1.20, "f_l": cand.get("length_m") or 1.20, "f_h": cand.get("height_m") or 0.35, "f_qty": int(round(cand.get("qty", 1) or 1)), "f_ref": cand.get("source", "")}
            if cand.get("rebar_rows"):
                rows = copy.deepcopy(cand["rebar_rows"])
                for r in rows:
                    r["len"] = cand.get("length_m") or cand.get("width_m") or 0.0
                st.session_state["footing_rebars"] = rows
        elif target == "เสา":
            mapping = {"col_name": cand.get("mark", "C1"), "col_w": cand.get("width_m") or 0.20, "col_l": cand.get("length_m") or 0.20, "col_h": cand.get("height_m") or cand.get("member_length_m") or 3.00, "col_qty": int(round(cand.get("qty", 1) or 1)), "col_ref": cand.get("source", "")}
            hint = cand.get("level_hint", "")
            if "ชั้น 1" in hint or "floor 1" in hint.lower(): mapping["col_level"] = "เสาชั้น 1"
            elif "ชั้น 2" in hint or "floor 2" in hint.lower(): mapping["col_level"] = "เสาชั้น 2"
            elif "ชั้น 3" in hint or "floor 3" in hint.lower(): mapping["col_level"] = "เสาชั้น 3"
            if cand.get("rebar_rows"):
                rows = copy.deepcopy(cand["rebar_rows"])
                for r in rows:
                    r["len"] = cand.get("height_m") or cand.get("member_length_m") or 3.0
                st.session_state["column_rebars"] = rows
        elif target == "คาน":
            mapping = {"b_name": cand.get("mark", "B1"), "b_w": cand.get("width_m") or 0.20, "b_h": cand.get("height_m") or 0.40, "b_l": cand.get("member_length_m") or 4.0, "b_qty": int(round(cand.get("qty", 1) or 1)), "b_ref": cand.get("source", "")}
            hint = cand.get("level_hint", "")
            if "ชั้น 1" in hint or "floor 1" in hint.lower(): mapping["beam_level"] = "คานชั้น 1 (B1)"
            elif "ชั้น 2" in hint or "floor 2" in hint.lower(): mapping["beam_level"] = "คานชั้น 2 (B2)"
            elif "ชั้น 3" in hint or "floor 3" in hint.lower(): mapping["beam_level"] = "คานชั้น 3 (B3)"
            if cand.get("rebar_rows"):
                rows = copy.deepcopy(cand["rebar_rows"])
                for r in rows:
                    r["len"] = mapping["b_l"]
                st.session_state["beam_rebars"] = rows
        elif target == "พื้น":
            mapping = {"s_name": cand.get("mark", "S1"), "s_w": cand.get("width_m") or 3.0, "s_l": cand.get("length_m") or 4.0, "s_h": cand.get("height_m") or 0.10, "s_qty": int(round(cand.get("qty", 1) or 1)), "s_ref": cand.get("source", "")}
            if cand.get("rebar_rows"):
                rows = copy.deepcopy(cand["rebar_rows"])
                for r in rows:
                    r["len"] = mapping["s_l"] if "ทางยาว" in r.get("pos", "") else mapping["s_w"]
                st.session_state["slab_rebars"] = rows
        elif target == "ผนัง":
            mapping = {"wall_name": cand.get("mark", "W1"), "wall_l": cand.get("member_length_m") or cand.get("width_m") or 4.0, "wall_h": cand.get("height_m") or cand.get("length_m") or 2.8, "wall_qty": int(round(cand.get("qty", 1) or 1)), "wall_ref": cand.get("source", "")}
        elif target == "ประตู-หน้าต่าง":
            mapping = {"dw_name": cand.get("mark", "D1"), "dw_qty": int(round(cand.get("qty", 1) or 1)), "dw_ref": cand.get("source", ""), "dw_w": cand.get("width_m") or 0.90, "dw_h": cand.get("length_m") or cand.get("height_m") or 2.00}
        elif target == "หลังคา":
            mapping = {"roof_name": cand.get("mark", "R1"), "roof_manual_area": cand.get("area_m2") or 0.0, "roof_ref": cand.get("source", "")}
            if cand.get("area_m2"):
                mapping["roof_area_mode"] = "ระบุพื้นที่มุงจริงจากแบบ"
            if cand.get("steel_profiles"):
                detected_len = cand.get("member_length_m") or cand.get("length_m") or 0.0
                detected_qty = cand.get("qty", 1) or 1
                st.session_state["roof_members"] = [{"member":p,"desc":"อ่านจากแบบ — ตรวจตำแหน่ง/ความยาวอีกครั้ง","len":detected_len,"qty":detected_qty,"kg_m":COMMON_STEEL_KG_M.get(p,0.0)} for p in cand.get("steel_profiles", [])]
        for k, v in mapping.items():
            st.session_state[k] = v
        st.session_state["drawing_prefill"] = None
        st.rerun()

def with_waste(net_qty, waste_pct):
    """คำนวณปริมาณจัดซื้อ = ปริมาณสุทธิ x (1 + Waste)"""
    return max(0.0, safe_num(net_qty)) * (1.0 + max(0.0, safe_num(waste_pct)))

def count_by_spacing(span, spacing):
    """นับจำนวนเส้นจากระยะ @ โดยยึดปลาย 2 ด้าน: ceil(span/@)+1"""
    span = max(0.0, safe_num(span))
    spacing = safe_num(spacing)
    if spacing <= 0 or span <= 0:
        return 0
    return math.ceil(span / spacing) + 1

def sum_rebar_breakdown(items):
    """รวมเหล็กเสริมแยกชนิด (ไม่รวมโครงเหล็กหลังคา)"""
    totals = {}
    for item in items or []:
        breakdown = item.get("เหล็กแยกชนิด", {})
        if not isinstance(breakdown, dict):
            continue
        for rebar_type, weight in breakdown.items():
            if str(rebar_type) == "โครงเหล็กหลังคา":
                continue
            totals[rebar_type] = totals.get(rebar_type, 0.0) + safe_num(weight)
    return totals

def sum_structural_steel(items):
    """รวมโครงเหล็กหลังคาแยกจากเหล็กเสริม คสล."""
    total = 0.0
    for item in items or []:
        breakdown = item.get("เหล็กแยกชนิด", {})
        if isinstance(breakdown, dict):
            total += safe_num(breakdown.get("โครงเหล็กหลังคา", 0.0))
        elif item.get("หมวด") == "งานหลังคา":
            total += safe_num(item.get("เหล็ก (กก.)", 0.0))
    return total

# รายการทรงหลังคา และ Slope Factor ประเมิน
ROOF_SHAPES = [
    "หลังคาเพิงหมาแหงน (Lean-to)",
    "หลังคาจั่ว (Gable)",
    "หลังคาปั้นหยา (Hip)",
    "หลังคาปั้นหยาผสมจั่ว (Dutch/Gambrel)",
    "หลังคาเพิงหมาแหงนซ้อนชั้น (Modern Lean-to)",
    "หลังคาดาดฟ้า/พื้นคอนกรีต (Flat)"
]

# รายการวัสดุมุงหลังคา (ราคาวัสดุ/ตร.ม. ประเมิน, ค่าแรง/ตร.ม. ประเมิน, น้ำหนักโครงเหล็ก กก./ตร.ม.)
ROOF_MATERIAL_SPECS = {
    "เมทัลชีท หนา 0.35 - 0.47 mm (พร้อมบุ PE / PU Foam)": {"mat": 280.0, "lab": 120.0, "steel_factor": 18.0},
    "กระเบื้องลอนคู่ (ซีเมนต์ใยหิน / ไร้ใยหิน)": {"mat": 180.0, "lab": 100.0, "steel_factor": 20.0},
    "กระเบื้องคอนกรีตซีแพคโมเนีย (CPAC Monier)": {"mat": 320.0, "lab": 150.0, "steel_factor": 28.0},
    "กระเบื้องแผ่นเรียบเพรสทีจ (Prestige / Neoclassic)": {"mat": 450.0, "lab": 180.0, "steel_factor": 28.0},
    "กระเบื้องดินเผา / กระเบื้องสุโขทัย": {"mat": 550.0, "lab": 220.0, "steel_factor": 25.0},
    "กระเบื้องเซรามิก (Excella)": {"mat": 750.0, "lab": 250.0, "steel_factor": 28.0},
    "แผ่นหลังคาไวนิล (UPVC / Plastwood)": {"mat": 650.0, "lab": 150.0, "steel_factor": 18.0},
    "แผ่นโพลีคาร์บอเนต / ตราเพชร / แผ่นโปร่งแสง": {"mat": 400.0, "lab": 120.0, "steel_factor": 16.0},
    "หลังคาชิงเกิ้ลรูฟ (Shingle Roof / Asphalt Shingle)": {"mat": 580.0, "lab": 200.0, "steel_factor": 22.0},
    "หลังคาโซลาร์เซลล์ integrated (Solar Roof Tiles)": {"mat": 2500.0, "lab": 350.0, "steel_factor": 25.0}
}

# รายการประตู-หน้าต่างแบบรายละเอียด
DOOR_WINDOW_TYPES = [
    "ประตูไม้เนื้อแข็ง / กระจก พร้อมวงกบ & ฟิตติ้ง",
    "ประตูไม้สังเคราะห์ (UPVC / PVC)",
    "ประตูบานสไลด์อลูมิเนียม (อบขาว / ดำ / ชา / ลายไม้) พร้อมกระจก",
    "ประตูบานผลัก/บานสวิง อลูมิเนียม",
    "ประตูบานม้วนเหล็ก / ประตูบานพับอลูมิเนียมลายไม้",
    "หน้าต่างบานเลื่อนอลูมิเนียม พร้อมกระจก",
    "หน้าต่างบานกระทุ้ง / บานเปิดอลูมิเนียม",
    "หน้าต่างบานเกล็ด (พร้อมเกล็ดกระจก/อลูมิเนียม)",
    "หน้าต่างกระจกติดตาย (Fixed Window)",
    "ประตู-หน้าต่าง กระจกบานเปลือย (Frameless Glass)"
]

# ---------------------------------------------------------
# 3. Persistent Data Storage & Session State Management
# ---------------------------------------------------------
DEFAULT_FOOTING_REBARS = [
    {"pos": "เหล็กวิ่งตามยาว", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 10.0, "len": 1.50, "lap_mode": "ไม่มี", "lap_ends": 0},
    {"pos": "เหล็กวิ่งตามกว้าง", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 10.0, "len": 1.50, "lap_mode": "ไม่มี", "lap_ends": 0},
]
DEFAULT_COLUMN_REBARS = [
    {"pos": "เหล็กแกน", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 4.0, "len": 3.50, "lap_mode": "พัฒนา/ฝัง", "lap_ends": 2},
    {"pos": "เหล็กปลอก", "type": "RB6", "mode": "ระยะห่าง (@ ม.)", "val": 0.15, "len": 0.80, "lap_mode": "ไม่มี", "lap_ends": 0},
]
DEFAULT_BEAM_REBARS = [
    {"pos": "เหล็กบน", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 2.0, "len": 4.00, "lap_mode": "พัฒนา/ฝัง", "lap_ends": 2},
    {"pos": "เหล็กล่าง", "type": "DB16", "mode": "จำนวน (เส้น)", "val": 4.0, "len": 4.00, "lap_mode": "พัฒนา/ฝัง", "lap_ends": 2},
    {"pos": "เหล็กปลอก", "type": "RB6", "mode": "ระยะห่าง (@ ม.)", "val": 0.15, "len": 1.20, "lap_mode": "ไม่มี", "lap_ends": 0},
]
DEFAULT_SLAB_REBARS = [
    {"pos": "เหล็กล่าง/ตะแกรงทางยาว", "type": "RB9", "mode": "ระยะห่าง (@ ม.)", "val": 0.20, "len": 4.00, "lap_mode": "ไม่มี", "lap_ends": 0},
    {"pos": "เหล็กล่าง/ตะแกรงทางกว้าง", "type": "RB9", "mode": "ระยะห่าง (@ ม.)", "val": 0.20, "len": 3.00, "lap_mode": "ไม่มี", "lap_ends": 0},
]

def reset_draft_for_project(project_id):
    """แยกร่างเหล็กตามโครงการ เพื่อไม่ให้รายการร่างจากโครงการหนึ่งไหลไปอีกโครงการ"""
    if "_draft_project_id" not in st.session_state:
        st.session_state["_draft_project_id"] = None
    if project_id != st.session_state.get("_draft_project_id"):
        st.session_state["footing_rebars"] = copy.deepcopy(DEFAULT_FOOTING_REBARS)
        st.session_state["column_rebars"] = copy.deepcopy(DEFAULT_COLUMN_REBARS)
        st.session_state["beam_rebars"] = copy.deepcopy(DEFAULT_BEAM_REBARS)
        st.session_state["slab_rebars"] = copy.deepcopy(DEFAULT_SLAB_REBARS)
        st.session_state["roof_members"] = []
        st.session_state["_draft_project_id"] = project_id

DATA_FILE = "projects_data.json"

def load_projects():
    """โหลดข้อมูลโครงการจากไฟล์ JSON พร้อมตรวจโครงสร้างขั้นต่ำ"""
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError("ข้อมูลโครงการต้องเป็น JSON array")
        valid = []
        for p in data:
            if not isinstance(p, dict) or "id" not in p or "name" not in p:
                continue
            p = dict(p)
            p.setdefault("location", "")
            p.setdefault("items", [])
            if not isinstance(p["items"], list):
                p["items"] = []
            normalized_items = []
            for item in p["items"]:
                if not isinstance(item, dict):
                    continue
                q = dict(item)
                # รองรับชื่อช่องจาก V7.x: ย้าย “อ้างอิงแบบ” มาเป็น “อ้างอิงจากแบบ”
                if not q.get("ที่มาในแบบ") and q.get("อ้างอิงแบบ"):
                    q["ที่มาในแบบ"] = q.get("อ้างอิงแบบ")
                q.pop("อ้างอิงแบบ", None)
                # รองรับข้อมูลเก่าที่ไม่มีคอลัมน์ใหม่
                q.setdefault("คอนกรีตสุทธิ (ลบ.ม.)", None)
                q.setdefault("เหล็กสุทธิ (กก.)", None)
                q.setdefault("ไม้แบบสุทธิ (ตร.ม.)", None)
                q.setdefault("ข้อมูลปริมาณสุทธิแยกจาก Waste", False)
                q.setdefault("รวมเงิน (บาท)", safe_num(q.get("ค่าวัสดุ (บาท)", 0.0)) + safe_num(q.get("ค่าแรง (บาท)", 0.0)))
                normalized_items.append(q)
            p["items"] = normalized_items
            valid.append(p)
        return valid
    except Exception as e:
        st.error(f"ไม่สามารถโหลดไฟล์ข้อมูลเดิมได้: {e}")
        return []


def save_projects():
    """บันทึกข้อมูลโครงการทั้งหมดลงไฟล์ JSON อัตโนมัติ"""
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(st.session_state["projects"], f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"เกิดข้อผิดพลาดในการบันทึกข้อมูลลงดิสก์: {e}")

if "projects" not in st.session_state:
    st.session_state["projects"] = load_projects()

if "current_project_id" not in st.session_state:
    if st.session_state["projects"]:
        st.session_state["current_project_id"] = st.session_state["projects"][0]["id"]
    else:
        st.session_state["current_project_id"] = None

if "footing_rebars" not in st.session_state:
    st.session_state["footing_rebars"] = [
        {"pos": "เหล็กวิ่งตามยาว", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 10.0, "len": 1.50},
        {"pos": "เหล็กวิ่งตามกว้าง", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 10.0, "len": 1.50}
    ]

if "column_rebars" not in st.session_state:
    st.session_state["column_rebars"] = [
        {"pos": "เหล็กแกน", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 4.0, "len": 3.50},
        {"pos": "เหล็กปลอก", "type": "RB6", "mode": "ระยะห่าง (@ ม.)", "val": 0.15, "len": 0.80}
    ]

if "beam_rebars" not in st.session_state:
    st.session_state["beam_rebars"] = [
        {"pos": "เหล็กบน", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 2.0, "len": 4.00},
        {"pos": "เหล็กล่าง", "type": "DB16", "mode": "จำนวน (เส้น)", "val": 4.0, "len": 4.00},
        {"pos": "เหล็กปลอก", "type": "RB6", "mode": "ระยะห่าง (@ ม.)", "val": 0.15, "len": 1.20}
    ]

if "slab_rebars" not in st.session_state:
    st.session_state["slab_rebars"] = [
        {"pos": "เหล็กล่าง/ตะแกรงทางยาว", "type": "RB9", "mode": "ระยะห่าง (@ ม.)", "val": 0.20, "len": 4.00},
        {"pos": "เหล็กล่าง/ตะแกรงทางกว้าง", "type": "RB9", "mode": "ระยะห่าง (@ ม.)", "val": 0.20, "len": 3.00}
    ]

def get_current_project_index():
    projects = st.session_state.get("projects", [])
    if not projects:
        return -1
    cur_id = st.session_state.get("current_project_id")
    for idx, p in enumerate(projects):
        if p["id"] == cur_id:
            return idx
    st.session_state["current_project_id"] = projects[0]["id"]
    return 0

proj_idx = get_current_project_index()
current_proj = st.session_state["projects"][proj_idx] if proj_idx != -1 else None
active_proj_name = current_proj["name"] if current_proj else "ยังไม่ได้เลือกโครงการ (กรุณาสร้างหรือเลือกโครงการ)"
reset_draft_for_project(current_proj["id"] if current_proj else None)

if "drawing_candidates" not in st.session_state:
    st.session_state["drawing_candidates"] = []
if "drawing_pdf_name" not in st.session_state:
    st.session_state["drawing_pdf_name"] = ""
if "drawing_reader_result" not in st.session_state:
    st.session_state["drawing_reader_result"] = None
if "drawing_prefill" not in st.session_state:
    st.session_state["drawing_prefill"] = None

def safe_filename(name, default="project"):
    """ทำชื่อไฟล์ให้ปลอดภัยสำหรับ Windows/OneDrive/SharePoint"""
    name = str(name or default).strip()
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    return name[:120] or default


def add_takeoff_item(item_data, allow_duplicate=False):
    """บันทึกรายการพร้อมตรวจซ้ำ + เติมปริมาณสุทธิ/จัดซื้อให้เป็นมาตรฐานเดียวกัน"""
    p_idx = get_current_project_index()
    if p_idx == -1:
        st.error("⚠ กรุณาสร้างหรือเลือกโครงการก่อนทำการบันทึกข้อมูล!")
        return False

    item = dict(item_data)
    item.setdefault("ที่มาในแบบ", "")
    item.setdefault("แหล่งข้อมูล", "กรอกจากแบบ/รายการคำนวณ")
    item.setdefault("คอนกรีตสุทธิ (ลบ.ม.)", safe_num(item.get("คอนกรีต (ลบ.ม.)", 0.0)))
    item.setdefault("เหล็กสุทธิ (กก.)", safe_num(item.get("เหล็ก (กก.)", 0.0)))
    item.setdefault("ไม้แบบสุทธิ (ตร.ม.)", safe_num(item.get("ไม้แบบ (ตร.ม.)", 0.0)))
    item.setdefault("ข้อมูลปริมาณสุทธิแยกจาก Waste", True)
    item["ค่าวัสดุ (บาท)"] = round(safe_num(item.get("ค่าวัสดุ (บาท)", 0.0)), 2)
    item["ค่าแรง (บาท)"] = round(safe_num(item.get("ค่าแรง (บาท)", 0.0)), 2)
    item["รวมเงิน (บาท)"] = round(item["ค่าวัสดุ (บาท)"] + item["ค่าแรง (บาท)"], 2)

    if prevent_duplicates and not allow_duplicate:
        dup = find_duplicate_item(item)
        if dup is not None:
            if get_item_source(item):
                st.warning(
                    f"พบรายการซ้ำ: **{item.get('รายการ', 'ไม่ระบุ')}**"
                    + f" | อ้างอิงจากแบบ {get_item_source(item)}"
                    + " — ระบบยังไม่บันทึก เพื่อป้องกันการนับ BOQ ซ้ำ"
                )
                st.caption("ถ้าเป็นคนละตำแหน่ง ให้ใส่อ้างอิงจากแบบให้ต่างกัน")
                return False
            st.warning(f"พบรายการชื่อและรายละเอียดเหมือนกัน: **{item.get('รายการ', 'ไม่ระบุ')}** แต่ยังไม่ได้ใส่อ้างอิงจากแบบ")
            if not st.button("บันทึกต่อ — ยืนยันว่าเป็นคนละตำแหน่ง", key=f"force_exact_dup_{len(st.session_state['projects'][p_idx].get('items', []))}"):
                return False

    near_hits = find_near_duplicate_items(item)
    if near_hits and not allow_duplicate and not find_duplicate_item(item):
        st.warning("⚠ พบรายการที่ใช้ หมวด + ชื่อรายการ + อ้างอิงจากแบบ เดียวกันอยู่แล้ว — ตรวจว่าเป็นตำแหน่งเดียวกันก่อนบันทึก")
        if not st.button("บันทึกต่อ ทั้งที่อาจซ้ำ", key=f"force_near_dup_{len(st.session_state["projects"][p_idx].get("items",[]))}"):
            return False

    if "items" not in st.session_state["projects"][p_idx]:
        st.session_state["projects"][p_idx]["items"] = []
    st.session_state["projects"][p_idx]["items"].append(item)
    save_projects()
    st.success(f"บันทึกรายการ '{item.get('รายการ', 'ไม่ระบุ')}' เรียบร้อยแล้ว!")
    return True

# ---------------------------------------------------------
# 4. Sidebar Price & Material Settings
# ---------------------------------------------------------
with st.sidebar:
    st.title("⚙ ตั้งค่าราคาและค่าแรง")
    st.caption("ช่อง “อ้างอิงจากแบบ” ไม่ต้องกรอกเพื่อคำนวณ ใช้บอกว่าข้อมูลมาจากแผ่น/ตำแหน่งไหน และตัวอ่าน PDF จะใส่ให้เมื่อพบข้อมูล")
    
    with st.expander("💼 ค่าดำเนินการ กำไร & ภาษี", expanded=True):
        profit_percent = st.number_input("ค่าดำเนินการ & กำไร (%)", min_value=0.0, max_value=100.0, value=10.0, step=1.0) / 100.0
        use_vat = st.checkbox("คิดภาษีมูลค่าเพิ่ม (VAT 7%)", value=True)

    with st.expander("🧱 ราคาวัสดุ & ค่าแรงงานสถาปัตย์", expanded=False):
        st.markdown("**งานผนัง & ฉาบ**")
        p_brick_red = st.number_input("อิฐมอญครึ่งแผ่น (บาท/ตร.ม.)", min_value=0.0, value=180.0, step=10.0)
        p_brick_red_full = st.number_input("อิฐมอญก่อเต็มแผ่น (บาท/ตร.ม.)", min_value=0.0, value=360.0, step=10.0)
        p_brick_light = st.number_input("อิฐมวลเบา 7.5 ซม. (บาท/ตร.ม.)", min_value=0.0, value=220.0, step=10.0)
        p_brick_light_10 = st.number_input("อิฐมวลเบา 10 ซม. (บาท/ตร.ม.)", min_value=0.0, value=280.0, step=10.0)
        p_brick_block = st.number_input("อิฐบล็อก 7 ซม. (บาท/ตร.ม.)", min_value=0.0, value=150.0, step=10.0)
        p_brick_block_10 = st.number_input("อิฐบล็อก 10 ซม. (บาท/ตร.ม.)", min_value=0.0, value=180.0, step=10.0)
        p_plaster_mat = st.number_input("ปูนฉาบสำเร็จรูป (บาท/ตร.ม.)", min_value=0.0, value=65.0, step=5.0)
        p_paint_mat = st.number_input("สีทาผนัง (บาท/ตร.ม.)", min_value=0.0, value=50.0, step=5.0)
        labour_masonry = st.number_input("ค่าแรงก่ออิฐ (บาท/ตร.ม.)", min_value=0.0, value=90.0, step=5.0)
        labour_plastering = st.number_input("ค่าแรงฉาบปูน (บาท/ตร.ม.)", min_value=0.0, value=85.0, step=5.0)
        labour_painting = st.number_input("ค่าแรงทาสี (บาท/ตร.ม.)", min_value=0.0, value=45.0, step=5.0)

        st.markdown("**งานพื้น & ฝ้าเพดาน**")
        p_tile_mat = st.number_input("กระเบื้องแกรนิตโต้/พื้น (บาท/ตร.ม.)", min_value=0.0, value=350.0, step=20.0)
        labour_tile = st.number_input("ค่าแรงปูกระเบื้อง/พื้น (บาท/ตร.ม.)", min_value=0.0, value=180.0, step=10.0)
        p_ceiling_mat = st.number_input("ฝ้ายิปซัมฉาบเรียบ+โครง (บาท/ตร.ม.)", min_value=0.0, value=220.0, step=10.0)
        labour_ceiling = st.number_input("ค่าแรงติดตั้งฝ้า (บาท/ตร.ม.)", min_value=0.0, value=100.0, step=10.0)

    with st.expander("🚜 ค่าแรงงานดินขุด-ดินถม", expanded=False):
        cost_excavation = st.number_input("ค่าขุดดิน (บาท/ลบ.ม.)", min_value=0.0, value=120.0, step=10.0)
        cost_backfill = st.number_input("ค่าถมดินย้อนกลับ (บาท/ลบ.ม.)", min_value=0.0, value=80.0, step=10.0)

    with st.expander("🏗️ ราคาวัสดุโครงสร้าง", expanded=False):
        p_concrete = st.number_input("คอนกรีต 240 ksc (บาท/ลบ.ม.)", min_value=0.0, value=2450.0, step=50.0)
        p_db12 = st.number_input("เหล็กข้ออ้อย DB (บาท/กก.)", min_value=0.0, value=31.0, step=0.5)
        p_rb9 = st.number_input("เหล็กกลม RB (บาท/กก.)", min_value=0.0, value=33.0, step=0.5)
        p_formwork = st.number_input("ไม้แบบ (บาท/ตร.ม.)", min_value=0.0, value=380.0, step=10.0)
        p_roof_cap = st.number_input("ครอบสันหลังคา/ตะเข้สัน (บาท/เมตร)", min_value=0.0, value=180.0, step=10.0)
        p_roof_steel = st.number_input("โครงเหล็กหลังคา (บาท/กก.)", min_value=0.0, value=42.0, step=0.5)
        labour_roof_steel = st.number_input("ค่าแรงประกอบโครงเหล็กหลังคา (บาท/กก.)", min_value=0.0, value=12.0, step=0.5)

    with st.expander("📌 ราคาและค่าแรงเสาเข็ม", expanded=False):
        p_pile_hex = st.number_input("เข็มหกเหลี่ยมกลวง (บาท/ม.)", min_value=0.0, value=120.0, step=10.0)
        p_pile_i18 = st.number_input("เข็ม I-18 (บาท/ม.)", min_value=0.0, value=220.0, step=10.0)
        p_pile_i22 = st.number_input("เข็ม I-22 (บาท/ม.)", min_value=0.0, value=280.0, step=10.0)
        p_pile_i26 = st.number_input("เข็ม I-26 (บาท/ม.)", min_value=0.0, value=350.0, step=10.0)
        p_pile_bored35 = st.number_input("เข็มเจาะ Ø0.35 ม. (บาท/ม.)", min_value=0.0, value=650.0, step=20.0)
        labour_pile_press = st.number_input("ค่าแรงกด/ตอกเข็ม (บาท/ม.)", min_value=0.0, value=80.0, step=5.0)
        labour_pile_bored = st.number_input("ค่าแรงเจาะเสาเข็ม (บาท/ม.)", min_value=0.0, value=250.0, step=10.0)

    with st.expander("🔨 ค่าแรงงานโครงสร้างทั่วไป", expanded=False):
        labour_concrete = st.number_input("ค่าแรงเทคอนกรีต (บาท/ลบ.ม.)", min_value=0.0, value=350.0, step=10.0)
        labour_rebar = st.number_input("ค่าแรงผูกเหล็กเส้น (บาท/กก.)", min_value=0.0, value=8.5, step=0.5)
        labour_formwork = st.number_input("ค่าแรงประกอบไม้แบบ (บาท/ตร.ม.)", min_value=0.0, value=150.0, step=10.0)

    with st.expander("📉 เปอร์เซ็นต์สูญเสีย (% Wastage)", expanded=False):
        waste_concrete = st.number_input("เผื่อคอนกรีต (%)", min_value=0.0, max_value=100.0, value=5.0) / 100.0
        waste_rebar = st.number_input("เผื่อเหล็กเส้น/โครงสร้าง (%)", min_value=0.0, max_value=100.0, value=10.0) / 100.0
        waste_formwork = st.number_input("เผื่อไม้แบบ (%)", min_value=0.0, max_value=100.0, value=15.0) / 100.0
        waste_wall = st.number_input("เผื่ออิฐ/ปูนฉาบ (%)", min_value=0.0, max_value=100.0, value=5.0) / 100.0
        waste_roof = st.number_input("เผื่อหลังคา (%)", min_value=0.0, max_value=100.0, value=7.0) / 100.0
        waste_finishing = st.number_input("เผื่อกระเบื้อง/ฝ้า (%)", min_value=0.0, max_value=100.0, value=5.0) / 100.0


    with st.expander("📖 คำศัพท์แบบเข้าใจง่าย", expanded=False):
        st.markdown("**Cover / ระยะหุ้มคอนกรีต** = ระยะจากผิวคอนกรีตถึงผิวเหล็ก\n\n**เหล็กทาบ** = ระยะที่เหล็กซ้อนกันตอนต่อเหล็ก\n\n**ระยะพัฒนา/ฝัง** = ระยะที่ต้องฝังเหล็กเข้าเนื้อคอนกรีต\n\n**@** = ระยะห่างระหว่างเหล็ก เช่น DB12 @ 200 มม.\n\n**อ้างอิงจากแบบ** = บอกว่าข้อมูลมาจากแผ่น/ตำแหน่งไหน เช่น S-03 / Grid B-2 / C1 — ไม่ใช้คำนวณ และตัวอ่าน PDF จะใส่ให้เอง\n\n**ชนิดเหล็ก/ขนาด** = เช่น C-125x50x20x3.2 หรือ H-150x150x7x10\n\n**น้ำหนักต่อเมตร** = น้ำหนักเหล็ก 1 เมตร เช่น 6.00 กก./ม.")

    with st.expander("🎯 ความแม่นยำจากแบบ (Drawing Accuracy)", expanded=True):
        st.caption("ค่า Cover (ระยะหุ้มคอนกรีต)/การต่อเหล็กเป็นค่าเริ่มต้นสำหรับช่วยถอดแบบเท่านั้น — แบบโครงสร้าง, Detail และ Spec ของโครงการมีผลเหนือค่าเหล่านี้เสมอ")
        cover_footing_mm = st.number_input("Cover (ระยะหุ้มคอนกรีต) ฐานราก (มม.)", min_value=0.0, max_value=150.0, value=50.0, step=5.0)
        cover_column_mm = st.number_input("Cover (ระยะหุ้มคอนกรีต) เสา (มม.)", min_value=0.0, max_value=100.0, value=40.0, step=5.0)
        cover_beam_mm = st.number_input("Cover (ระยะหุ้มคอนกรีต) คาน (มม.)", min_value=0.0, max_value=100.0, value=25.0, step=5.0)
        cover_slab_mm = st.number_input("Cover (ระยะหุ้มคอนกรีต) พื้น/บันได (มม.)", min_value=0.0, max_value=100.0, value=20.0, step=5.0)
        lap_factor_d = st.number_input("ระยะทาบตั้งต้น (×d)", min_value=0.0, max_value=100.0, value=40.0, step=5.0)
        dev_factor_d = st.number_input("ระยะพัฒนา/ฝังตั้งต้น (×d)", min_value=0.0, max_value=100.0, value=40.0, step=5.0)
        stirrup_hook_extra_m = st.number_input("เผื่อปลายตะขอปลอก (ม./วง)", min_value=0.0, max_value=1.0, value=0.10, step=0.01)
        waste_roof_steel = st.number_input("เผื่อโครงเหล็กหลังคา (%)", min_value=0.0, max_value=100.0, value=5.0) / 100.0
        prevent_duplicates = st.checkbox("ป้องกันรายการ BOQ ซ้ำ", value=True, help="ระบบกันรายการซ้ำตรงกัน และเตือนรายการที่ใช้หมวด+ชื่อ+ที่มาในแบบเดียวกัน")

# แผนที่ประเภทอิฐ/วัสดุก่อผนัง
brick_price_map = {
    "อิฐมอญครึ่งแผ่น (Mon Brick 1/2)": (p_brick_red, labour_masonry),
    "อิฐมอญก่อเต็มแผ่น (Mon Brick Full)": (p_brick_red_full, labour_masonry * 1.4),
    "อิฐมวลเบา 7.5 ซม. (Lightweight Concrete 7.5 cm)": (p_brick_light, labour_masonry),
    "อิฐมวลเบา 10 ซม. (Lightweight Concrete 10 cm)": (p_brick_light_10, labour_masonry * 1.1),
    "อิฐมวลเบา 12.5 - 15 ซม. (Lightweight Concrete 12.5-15 cm)": (380.0, labour_masonry * 1.2),
    "อิฐบล็อก 7 ซม. (Concrete Block 7 cm)": (p_brick_block, labour_masonry * 0.9),
    "อิฐบล็อก 10 ซม. (Concrete Block 10 cm)": (p_brick_block_10, labour_masonry),
    "อิฐบล็อก 15 ซม. (Concrete Block 15 cm)": (240.0, labour_masonry * 1.1),
    "อิฐโชว์แนว (Facing Brick)": (450.0, 180.0),
    "บล็อกช่องลม / อิฐช่องลม (Ventilation Block)": (350.0, 120.0),
    "ผนังเบาสมาร์ทบอร์ด / ไฟเบอร์ซีเมนต์ 8 มม. (2 ด้าน)": (380.0, 120.0),
    "ผนังยิปซัมบอร์ด 12 มม. โครงคร่าวเหล็ก (2 ด้าน)": (320.0, 100.0)
}

pile_price_map = {
    "เสาเข็มหกเหลี่ยมกลวง": (p_pile_hex, labour_pile_press),
    "เสาเข็ม I-18": (p_pile_i18, labour_pile_press),
    "เสาเข็ม I-22": (p_pile_i22, labour_pile_press),
    "เสาเข็ม I-26": (p_pile_i26, labour_pile_press),
    "เสาเข็มเจาะ Ø 0.35 ม.": (p_pile_bored35, labour_pile_bored)
}

floor_price_map = {
    "กระเบื้องแกรนิตโต้ 60x60 ซม. + ปูนทรายปรับระดับ": (p_tile_mat, labour_tile),
    "กระเบื้องเซรามิก 30x30 ซม. / 40x40 ซม. (งานห้องน้ำ/ซักล้าง)": (220.0, 160.0),
    "ไม้ลามิเนต 8 มม. / 12 มม. + ปูนทรายปรับระดับ": (390.0, 120.0),
    "กระเบื้องยาง SPC 4 มม. / 5 มม. (แบบ Click Lock)": (420.0, 100.0),
    "พื้นไม้ปาร์เก้ / ไม้จริง + ขัดเงาทำสี": (950.0, 350.0),
    "พื้นคอนกรีตขัดมัน (Polished Concrete) / พื้นอีพ็อกซี่ (Epoxy)": (280.0, 150.0)
}

ceiling_price_map = {
    "ฝ้ายิปซัมบอร์ด 9 มม. ฉาบเรียบ + โครงคร่าว C-Line": (p_ceiling_mat, labour_ceiling),
    "ฝ้ายิปซัมบอร์ด ทนชื้น 9 มม. (ห้องน้ำ/ชายคา)": (260.0, 110.0),
    "ฝ้าเพดานสำเร็จรูป ทีบาร์ 60x60 ซม. (โครงคร่าวอลูมิเนียม)": (210.0, 90.0),
    "ฝ้าเพดานหลุม / ฝ้าซ่อนไฟ (คิดเพิ่มเฉพาะส่วนหลุม)": (350.0, 180.0),
    "ฝ้าไม้ระแนง / ฝ้า WPC ทนแดดทนฝน": (550.0, 220.0),
    "ฝ้าสมาร์ทบอร์ด / ไม้ฝาสำเร็จรูป (ระบายอากาศ)": (290.0, 130.0)
}

# ---------------------------------------------------------
# 5. Header Banner
# ---------------------------------------------------------
st.markdown(f"""
<div class="header-banner">
    <div class="header-title">⚙️ ระบบถอดปริมาณงานโครงสร้าง & สถาปัตย์ (Takeoff V8.0 Drawing Reader + Accuracy)</div>
    <div class="header-subtitle">📁 โครงการปัจจุบัน: <b>{active_proj_name}</b></div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 6. Navigation Tabs
# ---------------------------------------------------------
tabs = st.tabs([
    "📁 โครงการ", 
    "🦶 ฐานราก", 
    "🏛 เสา", 
    "↔ คาน", 
    "🧱 พื้น", 
    "🧱 ผนัง & ตกแต่ง",
    "☁ ฝ้าเพดาน",
    "🪜 บันได", 
    "⛺ หลังคา", 
    "🧮 คำนวณ", 
    "📋 BOQ", 
    "📊 สรุป"
])

# =========================================================
# TAB 1: 📁 โครงการ
# =========================================================
with tabs[0]:
    col_create, col_list = st.columns([0.4, 0.6])
    
    with col_create:
        st.subheader("สร้างโครงการใหม่")
        with st.form("create_project_form", clear_on_submit=True):
            new_name = st.text_input("ชื่อโครงการ *", placeholder="เช่น บ้านคุณปุ๋ย")
            new_loc = st.text_input("สถานที่ก่อสร้าง", placeholder="เช่น อ.เมือง จ.พัทลุง")
            
            btn_create = st.form_submit_button("➕ สร้างโครงการใหม่", type="primary", use_container_width=True)
            if btn_create:
                if not new_name.strip():
                    st.error("กรุณากรอกชื่อโครงการ")
                else:
                    existing_ids = [p["id"] for p in st.session_state["projects"]]
                    numeric_ids = [x for x in existing_ids if isinstance(x, int) and not isinstance(x, bool)]
                    new_id = max(numeric_ids, default=0) + 1
                    while new_id in existing_ids:
                        new_id += 1
                    st.session_state["projects"].append({
                        "id": new_id,
                        "name": new_name.strip(),
                        "location": new_loc.strip(),
                        "items": []
                    })
                    st.session_state["current_project_id"] = new_id
                    save_projects()  # บันทึกข้อมูลลงดิสก์ทันที
                    st.success(f"สร้างโครงการ '{new_name}' เรียบร้อยแล้ว!")
                    st.rerun()

        st.markdown("---")
        st.subheader("📦 สำรอง / นำเข้าข้อมูล (Backup/Restore)")
        if st.session_state["projects"]:
            json_str = json.dumps(st.session_state["projects"], ensure_ascii=False, indent=2)
            st.download_button(
                label="📥 สำรองข้อมูลทุกโครงการ (Backup JSON)",
                data=json_str,
                file_name="boq_projects_backup.json",
                mime="application/json",
                use_container_width=True
            )
        
        uploaded_file = st.file_uploader("📤 นำเข้าข้อมูลโครงการ (Restore JSON)", type=["json"])
        if uploaded_file is not None:
            try:
                data = json.load(uploaded_file)
                if not isinstance(data, list):
                    raise ValueError("รูปแบบหลักต้องเป็น JSON array")
                cleaned = []
                for p in data:
                    if not isinstance(p, dict) or "id" not in p or "name" not in p:
                        raise ValueError("แต่ละโครงการต้องมี id และ name")
                    p = dict(p)
                    p.setdefault("location", "")
                    p.setdefault("items", [])
                    if not isinstance(p["items"], list):
                        raise ValueError(f"items ของโครงการ '{p['name']}' ต้องเป็น array")
                    normalized=[]
                    for item in p["items"]:
                        if not isinstance(item, dict):
                            continue
                        item=dict(item)
                        if not item.get("ที่มาในแบบ") and item.get("อ้างอิงแบบ"):
                            item["ที่มาในแบบ"]=item.get("อ้างอิงแบบ")
                        item.pop("อ้างอิงแบบ",None)
                        normalized.append(item)
                    p["items"]=normalized
                    cleaned.append(p)
                st.session_state["projects"] = cleaned
                st.session_state["current_project_id"] = cleaned[0]["id"] if cleaned else None
                save_projects()
                st.success("นำเข้าข้อมูลเรียบร้อยแล้ว!")
                st.rerun()
            except Exception as e:
                st.error(f"ไม่สามารถนำเข้าไฟล์ JSON ได้: {e}")


    with col_list:
        st.subheader("รายการโครงการทั้งหมด")
        if not st.session_state["projects"]:
            st.info("ยังไม่มีโครงการในระบบ กรุณาสร้างโครงการใหม่ทางด้านซ้าย")
        else:
            p_cols = st.columns(2)
            for idx, proj in enumerate(st.session_state["projects"]):
                with p_cols[idx % 2]:
                    is_active = (proj["id"] == st.session_state["current_project_id"])
                    with st.container():
                        st.markdown(f"### {proj['name']}")
                        st.caption(f"📍 {proj['location'] if proj['location'] else 'ไม่ระบุสถานที่'}")
                        st.text(f"📦 รายการถอดแบบ: {len(proj.get('items', []))} รายการ")
                        
                        c_btn1, c_btn2 = st.columns([3, 1])
                        if is_active:
                            c_btn1.button("ใช้งานอยู่", key=f"act_{proj['id']}", disabled=True, use_container_width=True)
                        else:
                            if c_btn1.button("เลือกใช้งาน", key=f"sel_{proj['id']}", type="primary", use_container_width=True):
                                st.session_state["current_project_id"] = proj["id"]
                                st.rerun()
                                
                        if c_btn2.button("🗑", key=f"del_{proj['id']}"):
                            st.session_state["projects"] = [p for p in st.session_state["projects"] if p["id"] != proj["id"]]
                            if is_active:
                                st.session_state["current_project_id"] = st.session_state["projects"][0]["id"] if len(st.session_state["projects"]) > 0 else None
                            save_projects()  # บันทึกการลบลงดิสก์
                            st.rerun()
                    st.markdown("---")

    st.markdown("---")
    st.subheader("📖 อ่านแบบ PDF → ช่วยกรอกข้อมูล BOQ")
    st.info("วิธีใช้: 1) ลาก PDF → 2) กดอ่านแบบ → 3) เลือกรายการ → 4) นำไปกรอก Tab → 5) ตรวจข้อมูลและกดบันทึก BOQ")
    st.caption("ระบบจะช่วยหา F1, C1, B1, S1, W1, R1, D1 และข้อมูลขนาด/จำนวน/เหล็กที่อ่านได้ แล้วเติมลง Tab ที่เกี่ยวข้องให้ตรวจอีกครั้ง — ระบบจะไม่สร้าง BOQ จาก OCR โดยอัตโนมัติ")
    st.caption("💡 อ้างอิงจากแบบ = ข้อมูลช่วยบอกว่าเลขนั้นมาจากแผ่น/ตำแหน่งไหน ไม่ต้องกรอกเพื่อให้สูตรคำนวณทำงาน")
    pdf_file = st.file_uploader("ลาก PDF แบบมาวางที่นี่", type=["pdf"], key="drawing_pdf_upload")
    rr1, rr2, rr3 = st.columns(3)
    use_ocr = rr1.checkbox("ช่วยอ่านหน้า PDF ที่เป็นรูปภาพ (OCR)", value=True, key="drawing_use_ocr", help="ถ้า PDF มีข้อความอยู่แล้ว ระบบจะใช้ข้อความเดิมก่อน ไม่ OCR ซ้ำ")
    max_pages = rr2.number_input("จำนวนหน้าสูงสุดที่อ่าน", min_value=1, max_value=200, value=80, step=10, key="drawing_max_pages")
    run_reader = rr3.button("🔎 อ่านแบบ", type="primary", use_container_width=True, key="btn_run_drawing_reader")
    if run_reader and pdf_file is not None:
        try:
            with st.spinner("กำลังอ่าน PDF และหา C1 / B1 / S1 / F1 ที่อยู่ในแบบ..."):
                result = read_drawing_pdf(pdf_file.getvalue(), use_ocr=use_ocr, max_pages=int(max_pages))
            st.session_state["drawing_reader_result"] = result
            st.session_state["drawing_candidates"] = result.get("candidates", [])
            st.session_state["drawing_pdf_name"] = pdf_file.name
            st.success(f"อ่าน {result['pages_read']} หน้า จากทั้งหมด {result['page_count']} หน้า | พบรายการที่น่าสนใจ {len(result['candidates'])} รายการ")
            if result['pages_read'] < result['page_count']:
                st.warning(f"อ่านเฉพาะ {result['pages_read']} หน้าแรกตามที่ตั้งไว้ — ยังเหลือ {result['page_count']-result['pages_read']} หน้า")
        except Exception as e:
            st.error(f"อ่าน PDF ไม่สำเร็จ: {e}")
    elif run_reader and pdf_file is None:
        st.warning("กรุณาเลือก PDF ก่อน")

    result = st.session_state.get("drawing_reader_result")
    candidates = st.session_state.get("drawing_candidates", [])
    if result:
        if result.get("ocr_pages"):
            st.caption("OCR ใช้ในหน้า: " + ", ".join(str(x) for x in result["ocr_pages"]))
        if not candidates:
            st.warning("ยังหา F/C/B/S/W/R/D ไม่พบ ลองตรวจว่า PDF เป็นแบบสแกนหรือข้อความถูกแปลงเป็นเส้นภาพทั้งหมด")
        else:
            rows=[]
            for idx,c in enumerate(candidates):
                dims, rb, steel=candidate_summary(c)
                conf="สูง" if c["confidence"]>=0.8 else ("กลาง" if c["confidence"]>=0.65 else "ต้องตรวจ")
                rows.append({"#":idx+1,"หน้า":c["page"],"รหัส":c["mark"],"ไปที่":c["target"],"ขนาด/ความยาว":dims,"เหล็กที่อ่านได้":rb,"โครงเหล็กที่อ่านได้":steel,"ความมั่นใจ":f"{conf} ({c['confidence']:.0%})"})
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            options=[f"#{i+1} | หน้า {c['page']} | {c['mark']} → {c['target']}" for i,c in enumerate(candidates)]
            sel=st.selectbox("เลือกรายการจากแบบที่ต้องการนำไปกรอก", options, key="drawing_candidate_select")
            sel_idx=options.index(sel) if sel in options else 0
            chosen=candidates[sel_idx]
            dims,rb,steel=candidate_summary(chosen)
            st.info(f"เลือกแล้ว: **{chosen['mark']}** | {dims} | เหล็ก: {rb} | โครงเหล็ก: {steel} | อ้างอิงแบบ: {chosen['source']}")
            if st.button(f"📌 นำ {chosen['mark']} ไปกรอก Tab {chosen['target']}", key="btn_queue_drawing_candidate"):
                if chosen["target"] in DRAWING_TAB_KEYS:
                    queue_drawing_candidate(chosen)
                    st.success("ใส่ข้อมูลเข้าคิวแล้ว — ไปที่ Tab เป้าหมายและตรวจข้อมูลก่อนกดบันทึก BOQ")
                    st.rerun()
                else:
                    st.warning("รายการนี้ยังไม่มีเครื่องมือคำนวณอัตโนมัติใน 12 Tab — ให้ตรวจและกรอกด้วยตัวเอง")
            with st.expander("ดูข้อความที่ระบบอ่านเจอ", expanded=False):
                st.code(chosen.get("source_text", ""), language=None)
            if st.button("🧹 ล้างผลการอ่านแบบ", key="btn_clear_drawing_reader"):
                st.session_state["drawing_reader_result"] = None
                st.session_state["drawing_candidates"] = []
                st.session_state["drawing_pdf_name"] = ""
                st.session_state["drawing_prefill"] = None
                st.rerun()

# =========================================================
# TAB 2: 🦶 ฐานราก
# =========================================================
with tabs[1]:
    maybe_apply_drawing_prefill("ฐานราก")
    st.subheader(f"🦶 ถอดปริมาณงานฐานราก — [{active_proj_name}]")

    f1, f2, f3 = st.columns([1.4, 1, 1.6])
    f_name = f1.text_input("ชื่อ/สัญลักษณ์ฐานราก", value="F1", key="f_name")
    f_qty = f2.number_input("จำนวน (ฐาน)", min_value=1, value=1, key="f_qty")
    f_type = f3.radio("ประเภทฐานราก", ["ฐานรากแผ่ (Shallow)", "ฐานรากมีเสาเข็ม (Piled)"], key="f_type")
    f_ref = st.text_input("อ้างอิงจากแบบ (ไม่บังคับ)", value="", key="f_ref", help="เช่น S-03 / Grid B-2 / F1 — ใช้ตรวจสอบย้อนหลัง ไม่ได้เปลี่ยนสูตรคำนวณ")

    m1, m2, m3, m4 = st.columns(4)
    f_w = m1.number_input("ความกว้างฐานราก (ม.)", min_value=0.05, value=1.20, step=0.10, key="f_w")
    f_l = m2.number_input("ความยาวฐานราก (ม.)", min_value=0.05, value=1.20, step=0.10, key="f_l")
    f_h = m3.number_input("ความหนาฐานราก (ม.)", min_value=0.05, value=0.35, step=0.05, key="f_h")
    f_depth = m4.number_input("ความลึกดินขุด H (ม.)", min_value=0.05, value=1.50, step=0.10, key="f_depth")

    ex1, ex2 = st.columns(2)
    f_work_space = ex1.number_input("พื้นที่เผื่อทำงานรอบหลุม/ด้าน (ม.)", min_value=0.0, value=0.30, step=0.05, key="f_work_space")
    f_form_type = ex2.selectbox("แบบหล่อด้านข้างฐานราก", ["เทชิดดิน / ไม่คิดไม้แบบข้าง", "มีไม้แบบข้างฐานราก"], key="f_form_type")

    auto_f_geo = st.checkbox("ช่วยตั้งความยาวเหล็กจากขนาดฐาน + Cover (ระยะหุ้มคอนกรีต)", value=True, key="f_auto_geo")

    if "เสาเข็ม" in f_type:
        st.markdown("#### 📌 รายละเอียดเสาเข็ม")
        pk1, pk2, pk3 = st.columns(3)
        pile_type = pk1.selectbox("ชนิดเสาเข็ม", list(pile_price_map.keys()), key="pile_type")
        pile_len = pk2.number_input("ความยาวเสาเข็ม/ต้น (ม.)", min_value=0.1, value=6.0, step=0.5, key="pile_len")
        piles_per_footing = pk3.number_input("จำนวนเสาเข็ม/ฐาน (ต้น)", min_value=1, value=1, key="piles_per_footing")

    st.markdown("---")
    st.markdown("#### 🔩 เหล็กเสริมฐานราก")
    if st.button("➕ เพิ่มรายการเหล็ก", key="btn_add_f_rebar"):
        st.session_state["footing_rebars"].append({"pos": "เหล็กวิ่งตามยาว", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 10.0, "len": 1.50, "lap_mode": "ไม่มี", "lap_ends": 0})
        st.rerun()

    f_rebars_to_remove = []
    tot_footing_rebar_weight = 0.0
    footing_rebar_detail = {}
    footing_lengths = []
    footing_pos_options = ["เหล็กวิ่งตามยาว", "เหล็กวิ่งตามกว้าง", "เหล็กเสริมพิเศษ"]

    for idx, r in enumerate(st.session_state["footing_rebars"]):
        r.setdefault("lap_mode", "ไม่มี"); r.setdefault("lap_ends", 0)
        c1, c2, c3, c4, c5, c6, c7 = st.columns([1.35, 1.1, 1.55, 1.1, 1.25, 1.2, 0.45])
        r["pos"] = c1.selectbox(f"แนว #{idx+1}", footing_pos_options, index=footing_pos_options.index(r.get("pos", footing_pos_options[0])) if r.get("pos", footing_pos_options[0]) in footing_pos_options else 0, key=f"f_pos_{idx}")
        r["type"] = c2.selectbox(f"เหล็ก #{idx+1}", REBAR_LIST, index=REBAR_LIST.index(r.get("type", "DB12")) if r.get("type") in REBAR_LIST else 2, key=f"f_type_{idx}")
        r["mode"] = c3.selectbox(f"จำนวน หรือ @ #{idx+1}", ["จำนวน (เส้น)", "ระยะห่าง (@ ม.)"], index=0 if r.get("mode") == "จำนวน (เส้น)" else 1, key=f"f_mode_{idx}")
        r["val"] = c4.number_input(f"ค่า #{idx+1}", min_value=0.0001, value=max(0.0001, safe_num(r.get("val", 1))), key=f"f_val_{idx}")
        r["len"] = c5.number_input(f"ความยาวช่วง/เหล็ก #{idx+1}", min_value=0.0, value=max(0.0, safe_num(r.get("len", 1.5))), key=f"f_len_{idx}")
        r["lap_mode"] = c6.selectbox(f"การต่อเหล็ก #{idx+1}", ["ไม่มี", "ทาบ", "พัฒนา/ฝัง"], index=["ไม่มี", "ทาบ", "พัฒนา/ฝัง"].index(r.get("lap_mode", "ไม่มี")), key=f"f_lapmode_{idx}")
        r["lap_ends"] = c6.number_input(f"ปลาย #{idx+1}", min_value=0, max_value=2, value=int(safe_num(r.get("lap_ends", 0))), step=1, key=f"f_lapends_{idx}") if r["lap_mode"] != "ไม่มี" else 0
        if c7.button("🗑", key=f"del_f_rebar_{idx}"):
            f_rebars_to_remove.append(idx)

        base_len = f_w if "ตามกว้าง" in r["pos"] else f_l
        count_span = f_l if "ตามกว้าง" in r["pos"] else f_w
        total_len_row = steel_row_length(r, base_len, count_span, cover_footing_mm, r["lap_mode"], r["lap_ends"], auto_f_geo, lap_factor_d=lap_factor_d, dev_factor_d=dev_factor_d)
        w_row = total_len_row * REBAR_WEIGHT[r["type"]]
        tot_footing_rebar_weight += w_row
        footing_rebar_detail[r["type"]] = footing_rebar_detail.get(r["type"], 0.0) + w_row
        footing_lengths.append((idx+1, r["type"], total_len_row))

    if f_rebars_to_remove:
        st.session_state["footing_rebars"] = [item for i, item in enumerate(st.session_state["footing_rebars"]) if i not in f_rebars_to_remove]
        st.rerun()

    if st.button("➕ บันทึกงานฐานราก", type="primary", key="btn_save_footing"):
        net_concrete = f_w * f_l * f_h * f_qty
        vol_concrete = with_waste(net_concrete, waste_concrete)
        net_formwork = (2 * (f_w + f_l) * f_h * f_qty) if "มีไม้แบบ" in f_form_type else 0.0
        formwork = with_waste(net_formwork, waste_formwork)
        net_rebar_weight = tot_footing_rebar_weight * f_qty
        rebar_weight = with_waste(net_rebar_weight, waste_rebar)

        excavation_area = (f_w + 2*f_work_space) * (f_l + 2*f_work_space)
        vol_excavation = excavation_area * f_depth * f_qty
        # ดินถม = หลุมขุด - ปริมาตรวัสดุที่แทนที่ในหลุม (คอนกรีตฐานราก)
        vol_backfill = max(0.0, vol_excavation - net_concrete)

        rebar_breakdown = {k: round(with_waste(v * f_qty, waste_rebar), 2) for k, v in footing_rebar_detail.items()}
        mat_c = vol_concrete*p_concrete + sum(v*get_rebar_price(k,p_db12,p_rb9) for k,v in rebar_breakdown.items()) + formwork*p_formwork
        lab_c = net_concrete*labour_concrete + net_rebar_weight*labour_rebar + net_formwork*labour_formwork

        add_takeoff_item({
            "หมวด": "งานดินขุด-ดินถม", "รายการ": f"งานดินสำหรับฐานราก {f_name}",
            "ที่มาในแบบ": f_ref, "แหล่งข้อมูล": "จากขนาดฐาน/ระดับขุด",
            "รายละเอียด": f"หลุม {f_w:.2f}x{f_l:.2f}ม. + พื้นที่เผื่อทำงาน {f_work_space:.2f}ม./ด้าน | ลึก {f_depth:.2f}ม.",
            "จำนวน": f_qty, "ดินขุด (ลบ.ม.)": round(vol_excavation,2), "ดินถม (ลบ.ม.)": round(vol_backfill,2),
            "ค่าวัสดุ (บาท)": 0.0, "ค่าแรง (บาท)": round(vol_excavation*cost_excavation + vol_backfill*cost_backfill,2)
        })

        if "เสาเข็ม" in f_type:
            total_piles = piles_per_footing*f_qty
            total_pile_length = total_piles*pile_len
            p_mat_rate,p_lab_rate = pile_price_map.get(pile_type,(0.0,0.0))
            add_takeoff_item({
                "หมวด": "งานเสาเข็ม", "รายการ": f"เสาเข็มรองรับ {f_name}", "ที่มาในแบบ": f_ref, "แหล่งข้อมูล": "จากรายการเสาเข็ม",
                "รายละเอียด": f"{pile_type} {pile_len:.1f}ม./ต้น × {total_piles} ต้น = {total_pile_length:.1f}ม.",
                "จำนวน": total_piles, "ค่าวัสดุ (บาท)": round(total_pile_length*p_mat_rate,2), "ค่าแรง (บาท)": round(total_pile_length*p_lab_rate,2)
            })

        add_takeoff_item({
            "หมวด": "งานฐานราก", "รายการ": f_name, "ที่มาในแบบ": f_ref, "แหล่งข้อมูล": "จากแบบโครงสร้าง",
            "รายละเอียด": f"ขนาด {f_w:.2f}x{f_l:.2f}x{f_h:.2f}ม. | Cover (ระยะหุ้มคอนกรีต) {cover_footing_mm:.0f}มม. | เหล็ก: " + ", ".join(f"{k} {v:.1f}กก." for k,v in rebar_breakdown.items()),
            "จำนวน": f_qty, "คอนกรีตสุทธิ (ลบ.ม.)": round(net_concrete,2), "คอนกรีต (ลบ.ม.)": round(vol_concrete,2),
            "เหล็กสุทธิ (กก.)": round(net_rebar_weight,2), "เหล็ก (กก.)": round(rebar_weight,2), "เหล็กแยกชนิด": rebar_breakdown,
            "ไม้แบบสุทธิ (ตร.ม.)": round(net_formwork,2), "ไม้แบบ (ตร.ม.)": round(formwork,2),
            "ค่าวัสดุ (บาท)": round(mat_c,2), "ค่าแรง (บาท)": round(lab_c,2)
        })

# =========================================================
# TAB 3: 🏛 เสา
# =========================================================
with tabs[2]:
    maybe_apply_drawing_prefill("เสา")
    st.subheader(f"🏛️ ถอดปริมาณงานเสา — [{active_proj_name}]")
    c1,c2,c3 = st.columns([1.4,1.7,1])
    col_name = c1.text_input("ชื่อ/สัญลักษณ์เสา", value="C1", key="col_name")
    col_level = c2.selectbox("ตำแหน่ง/ชั้น", ["เสาตอม่อ (Stub Column)","เสาชั้น 1","เสาชั้น 2","เสาชั้น 3","เสาชั้นหลังคา"], key="col_level")
    col_qty = c3.number_input("จำนวน (ต้น)", min_value=1, value=1, key="col_qty")
    col_ref = st.text_input("อ้างอิงจากแบบ (ไม่บังคับ)", value="", key="col_ref", help="เช่น S-04 / Grid B-2 / C1 — ใช้ตรวจสอบย้อนหลัง ไม่ได้เปลี่ยนสูตรคำนวณ")
    cm1,cm2,cm3 = st.columns(3)
    col_w = cm1.number_input("กว้างเสา (ม.)", min_value=0.05, value=0.20, step=0.05, key="col_w")
    col_l = cm2.number_input("ยาวเสา (ม.)", min_value=0.05, value=0.20, step=0.05, key="col_l")
    col_h = cm3.number_input("สูงเสา (ม.)", min_value=0.05, value=3.00, step=0.10, key="col_h")
    auto_col_geo = st.checkbox("ช่วยคำนวณความยาวเหล็กจากขนาดจาก Cover (ระยะหุ้มคอนกรีต) + ความสูง/ขนาดเสา", value=True, key="auto_col_geo")

    st.markdown("#### 🔩 เหล็กเสริมเสา")
    st.caption("ถ้าเป็นเหล็กปลอกและเลือก @ ให้ใส่ ‘ความยาวช่วง/เหล็ก’ เป็นความยาวของช่วงที่ใช้ระยะห่างนั้น")
    if st.button("➕ เพิ่มเหล็กเสา", key="btn_add_col_rebar"):
        st.session_state["column_rebars"].append({"pos":"เหล็กแกน","type":"DB12","mode":"จำนวน (เส้น)","val":4.0,"len":col_h,"lap_mode":"พัฒนา/ฝัง","lap_ends":2})
        st.rerun()
    c_rebars_to_remove=[]; tot_col_rebar_weight=0.0; col_rebar_detail={}
    col_pos_options=["เหล็กแกน","เหล็กปลอก","เหล็กเสริมพิเศษ"]
    for idx,r in enumerate(st.session_state["column_rebars"]):
        r.setdefault("lap_mode","ไม่มี"); r.setdefault("lap_ends",0)
        a1,a2,a3,a4,a5,a6,a7 = st.columns([1.35,1.05,1.5,1.05,1.25,1.2,0.45])
        r["pos"] = a1.selectbox(f"ตำแหน่ง #{idx+1}",col_pos_options,index=col_pos_options.index(r.get("pos","เหล็กแกน")) if r.get("pos") in col_pos_options else 0,key=f"c_pos_{idx}")
        r["type"] = a2.selectbox(f"เหล็ก #{idx+1}",REBAR_LIST,index=REBAR_LIST.index(r.get("type","DB12")) if r.get("type") in REBAR_LIST else 2,key=f"c_type_{idx}")
        r["mode"] = a3.selectbox(f"จำนวน หรือ @ #{idx+1}",["จำนวน (เส้น)","ระยะห่าง (@ ม.)"],index=0 if r.get("mode")=="จำนวน (เส้น)" else 1,key=f"c_mode_{idx}")
        r["val"] = a4.number_input(f"ค่า #{idx+1}",min_value=0.0001,value=max(0.0001,safe_num(r.get("val",4))),key=f"c_val_{idx}")
        r["len"] = a5.number_input(f"ความยาวช่วง/เหล็ก #{idx+1}",min_value=0.0,value=max(0.0,safe_num(r.get("len",col_h))),key=f"c_len_{idx}")
        r["lap_mode"] = a6.selectbox(f"การต่อเหล็ก #{idx+1}",["ไม่มี","ทาบ","พัฒนา/ฝัง"],index=["ไม่มี","ทาบ","พัฒนา/ฝัง"].index(r.get("lap_mode","ไม่มี")),key=f"c_lapmode_{idx}")
        r["lap_ends"] = a6.number_input(f"ปลาย #{idx+1}",min_value=0,max_value=2,value=int(safe_num(r.get("lap_ends",0))),step=1,key=f"c_lapends_{idx}") if r["lap_mode"]!="ไม่มี" else 0
        if a7.button("🗑",key=f"del_c_rebar_{idx}"): c_rebars_to_remove.append(idx)
        count_span_col = max(0.0, safe_num(r.get("len", col_h))) if "ปลอก" in str(r.get("pos", "")) and r.get("mode") != "จำนวน (เส้น)" else col_h
        total_len_row = steel_row_length(r,col_h,count_span_col,cover_column_mm,r["lap_mode"],r["lap_ends"],auto_col_geo,member_w=col_w,member_h=col_l,hook_extra_m=stirrup_hook_extra_m,lap_factor_d=lap_factor_d,dev_factor_d=dev_factor_d)
        w_row = total_len_row*REBAR_WEIGHT[r["type"]]
        tot_col_rebar_weight += w_row; col_rebar_detail[r["type"]]=col_rebar_detail.get(r["type"],0.0)+w_row
    if c_rebars_to_remove:
        st.session_state["column_rebars"]=[item for i,item in enumerate(st.session_state["column_rebars"]) if i not in c_rebars_to_remove]; st.rerun()

    if st.button("➕ บันทึกงานเสา",type="primary",key="btn_save_col"):
        net_concrete=col_w*col_l*col_h*col_qty; vol=with_waste(net_concrete,waste_concrete)
        net_form=2*(col_w+col_l)*col_h*col_qty; form=with_waste(net_form,waste_formwork)
        net_rebar_weight=tot_col_rebar_weight*col_qty; rebar_weight=with_waste(net_rebar_weight,waste_rebar)
        rebar_breakdown={k:round(with_waste(v*col_qty,waste_rebar),2) for k,v in col_rebar_detail.items()}
        mat_c=vol*p_concrete+sum(v*get_rebar_price(k,p_db12,p_rb9) for k,v in rebar_breakdown.items())+form*p_formwork
        lab_c=net_concrete*labour_concrete+net_rebar_weight*labour_rebar+net_form*labour_formwork
        add_takeoff_item({
            "หมวด":"งานเสา","รายการ":f"{col_name} ({col_level})","ที่มาในแบบ":col_ref,"แหล่งข้อมูล":"จากแบบโครงสร้าง",
            "รายละเอียด":f"{col_level} ขนาด {col_w:.2f}x{col_l:.2f}ม. สูง {col_h:.2f}ม. × {col_qty} ต้น | Cover (ระยะหุ้มคอนกรีต) {cover_column_mm:.0f}มม.",
            "จำนวน":col_qty,"คอนกรีตสุทธิ (ลบ.ม.)":round(net_concrete,2),"คอนกรีต (ลบ.ม.)":round(vol,2),
            "เหล็กสุทธิ (กก.)":round(net_rebar_weight,2),"เหล็ก (กก.)":round(rebar_weight,2),"เหล็กแยกชนิด":rebar_breakdown,
            "ไม้แบบสุทธิ (ตร.ม.)":round(net_form,2),"ไม้แบบ (ตร.ม.)":round(form,2),"ค่าวัสดุ (บาท)":round(mat_c,2),"ค่าแรง (บาท)":round(lab_c,2)
        })

# =========================================================
# TAB 4: ↔ คาน
# =========================================================
with tabs[3]:
    maybe_apply_drawing_prefill("คาน")
    st.subheader(f"↔️ ถอดปริมาณงานคาน — [{active_proj_name}]")
    b1,b2,b3=st.columns([1.35,1.7,1])
    beam_name=b1.text_input("ชื่อ/สัญลักษณ์คาน",value="B1",key="b_name")
    beam_level=b2.selectbox("ตำแหน่ง/ระดับ",["คานคอดิน (GB)","คานชั้น 1 (B1)","คานชั้น 2 (B2)","คานชั้น 3 (B3)","คานหลังคา (RB)"],key="beam_level")
    beam_qty=b3.number_input("จำนวน (คาน)",min_value=1,value=1,key="b_qty")
    beam_ref=st.text_input("อ้างอิงจากแบบ (ไม่บังคับ)",value="",key="b_ref",help="เช่น S-05 / Grid B-2 / B1 — ใช้ตรวจสอบย้อนหลัง")
    bm1,bm2,bm3=st.columns(3)
    beam_w=bm1.number_input("กว้างคาน (ม.)",min_value=0.05,value=0.20,step=0.05,key="b_w")
    beam_h=bm2.number_input("สูงคานรวม (ม.)",min_value=0.05,value=0.40,step=0.05,key="b_h")
    beam_l=bm3.number_input("ยาวคาน (ม.)",min_value=0.05,value=4.00,step=0.10,key="b_l")
    beam_slab_mode=st.selectbox("คานกับพื้นซ้อนกันหรือไม่",["คานแยก — ไม่หักปริมาตรพื้น","คานหล่อรวมพื้น — หักส่วนพื้นออก"],key="beam_slab_mode")
    st.caption("ใช้ ‘หักความหนาพื้น’ เมื่อพื้นที่พื้นใน Tab พื้นรวมเขตที่คานกินพื้นที่แล้ว มิฉะนั้นให้ใช้ ‘คานแยก’ เพื่อไม่หักคอนกรีตซ้ำ")
    beam_slab_t=st.number_input("ความหนาพื้นที่ต้องหักจากคาน (ม.)",min_value=0.0,max_value=1.0,value=0.10,step=0.01,key="beam_slab_t") if "หัก" in beam_slab_mode else 0.0
    auto_beam_geo=st.checkbox("ช่วยคำนวณความยาวเหล็กจากขนาด + Cover (ระยะหุ้มคอนกรีต) + ปลอกจากขนาดคาน",value=True,key="auto_beam_geo")

    st.markdown("#### 🔩 เหล็กเสริมคาน")
    st.caption("ถ้าเป็นเหล็กปลอกและเลือก @ ให้ใส่ ‘ความยาวช่วง/เหล็ก’ เป็นความยาวของช่วงที่ใช้ระยะห่างนั้น")
    if st.button("➕ เพิ่มเหล็กคาน",key="btn_add_beam_rebar"):
        st.session_state["beam_rebars"].append({"pos":"เหล็กบน","type":"DB12","mode":"จำนวน (เส้น)","val":2.0,"len":beam_l,"lap_mode":"พัฒนา/ฝัง","lap_ends":2}); st.rerun()
    b_rebars_to_remove=[]; tot_beam_rebar_weight=0.0; beam_rebar_detail={}
    beam_pos_options=["เหล็กบน","เหล็กล่าง","เหล็กเสริมพิเศษ","เหล็กปลอก"]
    for idx,r in enumerate(st.session_state["beam_rebars"]):
        r.setdefault("lap_mode","ไม่มี");r.setdefault("lap_ends",0)
        a1,a2,a3,a4,a5,a6,a7=st.columns([1.35,1.05,1.5,1.05,1.25,1.2,0.45])
        r["pos"]=a1.selectbox(f"ตำแหน่ง #{idx+1}",beam_pos_options,index=beam_pos_options.index(r.get("pos","เหล็กบน")) if r.get("pos") in beam_pos_options else 0,key=f"b_pos_{idx}")
        r["type"]=a2.selectbox(f"เหล็ก #{idx+1}",REBAR_LIST,index=REBAR_LIST.index(r.get("type","DB12")) if r.get("type") in REBAR_LIST else 2,key=f"b_type_{idx}")
        r["mode"]=a3.selectbox(f"จำนวน หรือ @ #{idx+1}",["จำนวน (เส้น)","ระยะห่าง (@ ม.)"],index=0 if r.get("mode")=="จำนวน (เส้น)" else 1,key=f"b_mode_{idx}")
        r["val"]=a4.number_input(f"ค่า #{idx+1}",min_value=0.0001,value=max(0.0001,safe_num(r.get("val",2))),key=f"b_val_{idx}")
        r["len"]=a5.number_input(f"ความยาวช่วง/เหล็ก #{idx+1}",min_value=0.0,value=max(0.0,safe_num(r.get("len",beam_l))),key=f"b_len_{idx}")
        r["lap_mode"]=a6.selectbox(f"การต่อเหล็ก #{idx+1}",["ไม่มี","ทาบ","พัฒนา/ฝัง"],index=["ไม่มี","ทาบ","พัฒนา/ฝัง"].index(r.get("lap_mode","ไม่มี")),key=f"b_lapmode_{idx}")
        r["lap_ends"]=a6.number_input(f"ปลาย #{idx+1}",min_value=0,max_value=2,value=int(safe_num(r.get("lap_ends",0))),step=1,key=f"b_lapends_{idx}") if r["lap_mode"]!="ไม่มี" else 0
        if a7.button("🗑",key=f"del_b_rebar_{idx}"): b_rebars_to_remove.append(idx)
        count_span_beam = max(0.0, safe_num(r.get("len", beam_l))) if "ปลอก" in str(r.get("pos", "")) and r.get("mode") != "จำนวน (เส้น)" else beam_l
        total_len_row=steel_row_length(r,beam_l,count_span_beam,cover_beam_mm,r["lap_mode"],r["lap_ends"],auto_beam_geo,member_w=beam_w,member_h=beam_h,hook_extra_m=stirrup_hook_extra_m,lap_factor_d=lap_factor_d,dev_factor_d=dev_factor_d)
        w_row=total_len_row*REBAR_WEIGHT[r["type"]]; tot_beam_rebar_weight+=w_row; beam_rebar_detail[r["type"]]=beam_rebar_detail.get(r["type"],0.0)+w_row
    if b_rebars_to_remove:
        st.session_state["beam_rebars"]=[item for i,item in enumerate(st.session_state["beam_rebars"]) if i not in b_rebars_to_remove];st.rerun()

    if st.button("➕ บันทึกงานคาน",type="primary",key="btn_save_beam"):
        effective_beam_h=max(0.0,beam_h-beam_slab_t) if "หัก" in beam_slab_mode else beam_h
        net_concrete=beam_w*effective_beam_h*beam_l*beam_qty
        vol=with_waste(net_concrete,waste_concrete)
        net_form=((2*effective_beam_h+beam_w)*beam_l*beam_qty) if "หัก" in beam_slab_mode else ((2*beam_h+beam_w)*beam_l*beam_qty)
        form=with_waste(net_form,waste_formwork)
        net_rebar_weight=tot_beam_rebar_weight*beam_qty;rebar_weight=with_waste(net_rebar_weight,waste_rebar)
        rebar_breakdown={k:round(with_waste(v*beam_qty,waste_rebar),2) for k,v in beam_rebar_detail.items()}
        mat_c=vol*p_concrete+sum(v*get_rebar_price(k,p_db12,p_rb9) for k,v in rebar_breakdown.items())+form*p_formwork
        lab_c=net_concrete*labour_concrete+net_rebar_weight*labour_rebar+net_form*labour_formwork
        add_takeoff_item({
            "หมวด":"งานคาน","รายการ":f"{beam_name} ({beam_level})","ที่มาในแบบ":beam_ref,"แหล่งข้อมูล":"จากแบบโครงสร้าง",
            "รายละเอียด":f"{beam_level} {beam_w:.2f}x{beam_h:.2f}ม. ยาว {beam_l:.2f}ม. × {beam_qty} | {beam_slab_mode}" + (f" {beam_slab_t:.2f}ม." if beam_slab_t>0 else "") + f" | Cover (ระยะหุ้มคอนกรีต) {cover_beam_mm:.0f}มม.",
            "จำนวน":beam_qty,"คอนกรีตสุทธิ (ลบ.ม.)":round(net_concrete,2),"คอนกรีต (ลบ.ม.)":round(vol,2),
            "เหล็กสุทธิ (กก.)":round(net_rebar_weight,2),"เหล็ก (กก.)":round(rebar_weight,2),"เหล็กแยกชนิด":rebar_breakdown,
            "ไม้แบบสุทธิ (ตร.ม.)":round(net_form,2),"ไม้แบบ (ตร.ม.)":round(form,2),"ค่าวัสดุ (บาท)":round(mat_c,2),"ค่าแรง (บาท)":round(lab_c,2)
        })

# =========================================================
# TAB 5: 🧱 พื้น
# =========================================================
with tabs[4]:
    maybe_apply_drawing_prefill("พื้น")
    st.subheader(f"🧱 ถอดปริมาณงานพื้น — [{active_proj_name}]")
    s1,s2=st.columns(2)
    slab_name=s1.text_input("ชื่อ/สัญลักษณ์พื้น",value="S1",key="s_name")
    slab_qty=s2.number_input("จำนวน (ผืน)",min_value=1,value=1,key="s_qty")
    slab_ref=st.text_input("อ้างอิงจากแบบ (ไม่บังคับ)",value="",key="s_ref",help="เช่น S-06 / ห้องนั่งเล่น / S1 — ใช้ตรวจสอบย้อนหลัง")
    sm1,sm2,sm3=st.columns(3)
    slab_w=sm1.number_input("กว้างพื้น (ม.)",min_value=0.05,value=3.00,step=0.10,key="s_w")
    slab_l=sm2.number_input("ยาวพื้น (ม.)",min_value=0.05,value=4.00,step=0.10,key="s_l")
    slab_h=sm3.number_input("หนาพื้น (ม.)",min_value=0.03,value=0.10,step=0.01,key="s_h")
    slab_support=st.selectbox("ลักษณะพื้น",["พื้นยก/พื้น คสล. มีแบบหล่อใต้ท้องพื้น","พื้นวางบนดิน (Slab on Ground)"],key="s_support")
    slab_opening_mode=st.selectbox("วิธีหักช่องเปิด",["พื้นที่ช่องเปิดรวม (จากแบบ)","กว้าง×ยาว×จำนวน"],key="s_opening_mode")
    if slab_opening_mode=="พื้นที่ช่องเปิดรวม (จากแบบ)":
        slab_openings=st.number_input("พื้นที่ช่องเปิดรวม (ตร.ม.)",min_value=0.0,value=0.0,step=0.10,key="s_openings")
    else:
        so1,so2,so3=st.columns(3)
        op_w=so1.number_input("กว้างช่องเปิด (ม.)",min_value=0.0,value=0.0,step=0.10,key="s_opw")
        op_l=so2.number_input("ยาวช่องเปิด (ม.)",min_value=0.0,value=0.0,step=0.10,key="s_opl")
        op_q=so3.number_input("จำนวนช่องเปิดรวม",min_value=0,value=0,key="s_opq")
        slab_openings=op_w*op_l*op_q
    slab_opening_rebar_add = st.number_input("เหล็กเสริมรอบช่องเปิดจากแบบ (กก.)", min_value=0.0, value=0.0, step=1.0, key="s_opening_rebar_add")
    st.caption("ระบบหักคอนกรีตตามช่องเปิดให้แล้ว ส่วนเหล็กเสริมรอบช่องเปิดให้ยึดรายละเอียดในแบบและกรอกเพิ่มเอง")
    auto_slab_geo=st.checkbox("ช่วยตั้งความยาวเหล็กจาก Cover (ระยะหุ้มคอนกรีต) + ทิศทางพื้น",value=True,key="auto_slab_geo")

    st.markdown("#### 🔩 เหล็กเสริมพื้น")
    st.caption("ถ้าเป็นเหล็กปลอกและเลือก @ ให้ใส่ ‘ความยาวช่วง/เหล็ก’ เป็นความยาวของช่วงที่ใช้ระยะห่างนั้น")
    if st.button("➕ เพิ่มรายการเหล็กพื้น",key="btn_add_s_rebar"):
        st.session_state["slab_rebars"].append({"pos":"เหล็กล่าง/ตะแกรงทางยาว","type":"RB9","mode":"ระยะห่าง (@ ม.)","val":0.20,"len":slab_l,"lap_mode":"ไม่มี","lap_ends":0});st.rerun()
    s_rebars_to_remove=[];tot_slab_rebar_weight=0.0;slab_rebar_detail={}
    slab_pos_options=["เหล็กล่าง/ตะแกรงทางยาว","เหล็กล่าง/ตะแกรงทางกว้าง","เหล็กบน/ตะแกรงทางยาว","เหล็กบน/ตะแกรงทางกว้าง","เหล็กคอมเมนท์/เหล็กเสริมพิเศษ"]
    for idx,r in enumerate(st.session_state["slab_rebars"]):
        r.setdefault("lap_mode","ไม่มี");r.setdefault("lap_ends",0)
        a1,a2,a3,a4,a5,a6,a7=st.columns([1.55,1.05,1.5,1.05,1.2,1.2,0.45])
        r["pos"]=a1.selectbox(f"ตำแหน่ง #{idx+1}",slab_pos_options,index=slab_pos_options.index(r.get("pos",slab_pos_options[0])) if r.get("pos") in slab_pos_options else 0,key=f"s_pos_{idx}")
        r["type"]=a2.selectbox(f"เหล็ก #{idx+1}",REBAR_LIST,index=REBAR_LIST.index(r.get("type","RB9")) if r.get("type") in REBAR_LIST else 1,key=f"s_type_{idx}")
        r["mode"]=a3.selectbox(f"จำนวน หรือ @ #{idx+1}",["จำนวน (เส้น)","ระยะห่าง (@ ม.)"],index=0 if r.get("mode")=="จำนวน (เส้น)" else 1,key=f"s_mode_{idx}")
        r["val"]=a4.number_input(f"ค่า #{idx+1}",min_value=0.0001,value=max(0.0001,safe_num(r.get("val",0.2))),key=f"s_val_{idx}")
        r["len"]=a5.number_input(f"ความยาวช่วง/เหล็ก #{idx+1}",min_value=0.0,value=max(0.0,safe_num(r.get("len",slab_l))),key=f"s_len_{idx}")
        r["lap_mode"]=a6.selectbox(f"การต่อเหล็ก #{idx+1}",["ไม่มี","ทาบ","พัฒนา/ฝัง"],index=["ไม่มี","ทาบ","พัฒนา/ฝัง"].index(r.get("lap_mode","ไม่มี")),key=f"s_lapmode_{idx}")
        r["lap_ends"]=a6.number_input(f"ปลาย #{idx+1}",min_value=0,max_value=2,value=int(safe_num(r.get("lap_ends",0))),step=1,key=f"s_lapends_{idx}") if r["lap_mode"]!="ไม่มี" else 0
        if a7.button("🗑",key=f"del_s_rebar_{idx}"):s_rebars_to_remove.append(idx)
        count_span=slab_w if "ทางยาว" in r["pos"] else slab_l
        base_len=slab_l if "ทางยาว" in r["pos"] else slab_w
        total_len_row=steel_row_length(r,base_len,count_span,cover_slab_mm,r["lap_mode"],r["lap_ends"],auto_slab_geo,lap_factor_d=lap_factor_d,dev_factor_d=dev_factor_d)
        w_row=total_len_row*REBAR_WEIGHT[r["type"]];tot_slab_rebar_weight+=w_row;slab_rebar_detail[r["type"]]=slab_rebar_detail.get(r["type"],0.0)+w_row
    if s_rebars_to_remove:
        st.session_state["slab_rebars"]=[item for i,item in enumerate(st.session_state["slab_rebars"]) if i not in s_rebars_to_remove];st.rerun()

    if st.button("➕ บันทึกงานพื้น",type="primary",key="btn_save_slab"):
        gross_area=slab_w*slab_l*slab_qty; net_area=max(0.0,gross_area-slab_openings)
        net_concrete=net_area*slab_h;vol=with_waste(net_concrete,waste_concrete)
        net_form=net_area if "พื้นยก" in slab_support else 0.0;form=with_waste(net_form,waste_formwork)
        net_rebar_weight=tot_slab_rebar_weight*slab_qty + slab_opening_rebar_add
        rebar_weight=with_waste(net_rebar_weight,waste_rebar)
        rebar_breakdown={k:round(with_waste(v*slab_qty,waste_rebar),2) for k,v in slab_rebar_detail.items()}
        if slab_opening_rebar_add > 0:
            rebar_breakdown["เหล็กเสริมรอบช่องเปิด (ตามแบบ)"] = round(with_waste(slab_opening_rebar_add, waste_rebar), 2)
        mat_c=vol*p_concrete+sum(v*get_rebar_price(k,p_db12,p_rb9) if k in REBAR_WEIGHT else v*p_rb9 for k,v in rebar_breakdown.items())+form*p_formwork
        lab_c=net_concrete*labour_concrete+net_rebar_weight*labour_rebar+net_form*labour_formwork
        rebar_desc=", ".join(f"{k}: {v:.1f} กก." for k,v in rebar_breakdown.items()) if rebar_breakdown else "ไม่ใส่เหล็กเสริม"
        add_takeoff_item({
            "หมวด":"งานพื้น","รายการ":slab_name,"ที่มาในแบบ":slab_ref,"แหล่งข้อมูล":"จากแบบสถาปัตย์/โครงสร้าง",
            "รายละเอียด":f"{slab_w:.2f}x{slab_l:.2f}ม. หนา {slab_h:.2f}ม. × {slab_qty} | {slab_support} | หักช่องเปิด {slab_openings:.2f}ตร.ม. | Cover (ระยะหุ้มคอนกรีต) {cover_slab_mm:.0f}มม. | เหล็ก {rebar_desc}",
            "จำนวน":slab_qty,"คอนกรีตสุทธิ (ลบ.ม.)":round(net_concrete,2),"คอนกรีต (ลบ.ม.)":round(vol,2),"เหล็กสุทธิ (กก.)":round(net_rebar_weight,2),"เหล็ก (กก.)":round(rebar_weight,2),"เหล็กแยกชนิด":rebar_breakdown,"ไม้แบบสุทธิ (ตร.ม.)":round(net_form,2),"ไม้แบบ (ตร.ม.)":round(form,2),"ค่าวัสดุ (บาท)":round(mat_c,2),"ค่าแรง (บาท)":round(lab_c,2)
        })

# =========================================================
# TAB 6: 🧱 ผนัง & ตกแต่ง
# =========================================================
with tabs[5]:
    maybe_apply_drawing_prefill("ผนัง")
    maybe_apply_drawing_prefill("ประตู-หน้าต่าง")
    st.subheader(f"🧱 ถอดปริมาณงานผนัง ประตู-หน้าต่าง และพื้นผิวตกแต่ง — [{active_proj_name}]")
    
    with st.expander("🧱 1. งานก่ออิฐ - ฉาบปูน - เสาเอ็น-คานทับหลัง", expanded=True):
        w1, w2, w3 = st.columns([1.5, 2.0, 1])
        wall_name = w1.text_input("ชื่อ/สัญลักษณ์ผนัง", value="W1", key="wall_name")
        brick_type = w2.selectbox("ประเภทอิฐ/วัสดุก่อ", list(brick_price_map.keys()), key="brick_type")
        wall_qty = w3.number_input("จำนวนผนังชุดนี้ (ผืน)", min_value=1, value=1, key="wall_qty")
        wall_ref = st.text_input("อ้างอิงจากแบบ (ไม่บังคับ)", value="", key="wall_ref", help="เช่น A-03 / ห้องนอน 1 / W1 — ใช้ตรวจสอบย้อนหลัง")

        wm1, wm2 = st.columns(2)
        wall_l = wm1.number_input("ความยาวผนัง (เมตร)", min_value=0.05, value=4.00, step=0.10, key="wall_l")
        wall_h = wm2.number_input("ความสูงผนัง (เมตร)", min_value=0.05, value=2.80, step=0.10, key="wall_h")

        st.markdown("**🚪 ช่องเปิดเพื่อหักพื้นที่ (รวมทุกผืนของรายการนี้)**")
        open_mode = st.selectbox("วิธีหักช่องเปิด", ["กว้าง×สูง×จำนวน", "พื้นที่ช่องเปิดรวมจากแบบ"], key="wall_open_mode")
        if open_mode == "กว้าง×สูง×จำนวน":
            d1, d2, d3 = st.columns(3)
            deduct_w = d1.number_input("กว้างต่อช่อง (ม.)", min_value=0.0, value=0.90, step=0.10, key="deduct_w")
            deduct_h = d2.number_input("สูงต่อช่อง (ม.)", min_value=0.0, value=2.00, step=0.10, key="deduct_h")
            deduct_qty = d3.number_input("จำนวนช่องเปิด", min_value=0, value=1, key="deduct_qty")
            opening_area_total = deduct_w * deduct_h * deduct_qty
        else:
            opening_area_total = st.number_input("พื้นที่ช่องเปิดรวมสุทธิ (ตร.ม.)", min_value=0.0, value=0.0, step=0.10, key="opening_area_total")
            deduct_w, deduct_h, deduct_qty = 0.0, 0.0, 0

        st.markdown("**🎨 งานฉาบปูน & งานทาสี**")
        p1, p2, p3 = st.columns(3)
        plaster_sides = p1.selectbox("งานฉาบปูน", ["ฉาบปูน 2 ด้าน", "ฉาบปูน 1 ด้าน", "ไม่คิดงานฉาบ"], key="plaster_sides")
        paint_sides = p2.selectbox("งานทาสี", ["ทาสี 2 ด้าน", "ทาสี 1 ด้าน", "ไม่คิดงานทาสี"], key="paint_sides")
        lintel_rebar_type = p3.selectbox("เหล็กเสริมเสาเอ็น-คานทับหลัง", ["RB6", "RB9"], index=1, key="lintel_rebar_type")

        lm1, lm2 = st.columns(2)
        lintel_mode = lm1.selectbox("วิธีประมาณความยาวเสาเอ็น/ทับหลัง", ["ประมาณจากช่องเปิด", "ระบุความยาวรวมจากแบบ"], key="lintel_mode")
        lintel_extra_len = lm2.number_input("ความยาวเสาเอ็น/ทับหลังรวมจากแบบ (ทุกผืน, ม.)", min_value=0.0, value=0.0, step=0.10, key="lintel_extra_len")

        if st.button("➕ บันทึกงานผนังและฉาบปูน", type="primary", key="btn_save_wall"):
            gross_area = (wall_l * wall_h) * wall_qty
            deduct_area = opening_area_total
            net_wall_area = max(0.0, gross_area - deduct_area)
            proc_masonry_area = with_waste(net_wall_area, waste_wall)

            p_brick, lab_masonry_used = brick_price_map.get(brick_type, (p_brick_red, labour_masonry))
            cost_masonry_mat = proc_masonry_area * p_brick
            cost_masonry_lab = net_wall_area * lab_masonry_used

            plaster_mult = 2.0 if "2 ด้าน" in plaster_sides else (1.0 if "1 ด้าน" in plaster_sides else 0.0)
            net_plaster_area = net_wall_area * plaster_mult
            proc_plaster_area = with_waste(net_plaster_area, waste_finishing)
            cost_plaster_mat = proc_plaster_area * p_plaster_mat
            cost_plaster_lab = net_plaster_area * labour_plastering

            paint_mult = 2.0 if "2 ด้าน" in paint_sides else (1.0 if "1 ด้าน" in paint_sides else 0.0)
            net_paint_area = net_wall_area * paint_mult
            proc_paint_area = with_waste(net_paint_area, waste_finishing)
            cost_paint_mat = proc_paint_area * p_paint_mat
            cost_paint_lab = net_paint_area * labour_painting

            # งานเสาเอ็น/ทับหลังเป็นเพียงค่าประมาณ: ให้ผู้ใช้ระบุจากแบบได้โดยตรงเมื่อมีแบบโครงสร้าง
            opening_lintel_len = (deduct_w * deduct_qty) if open_mode == "กว้าง×สูง×จำนวน" and deduct_qty > 0 else 0.0
            if lintel_mode == "ระบุความยาวรวมจากแบบ":
                tot_lintel_len = lintel_extra_len
            else:
                tot_lintel_len = opening_lintel_len + lintel_extra_len

            net_lintel_concrete = 0.10 * 0.10 * tot_lintel_len
            vol_lintel_concrete = with_waste(net_lintel_concrete, waste_concrete)
            net_form_lintel = 0.30 * tot_lintel_len
            form_lintel = with_waste(net_form_lintel, waste_formwork)
            net_rebar_lintel_weight = 2 * tot_lintel_len * REBAR_WEIGHT[lintel_rebar_type]
            rebar_lintel_weight = with_waste(net_rebar_lintel_weight, waste_rebar)

            cost_lintel_mat = vol_lintel_concrete * p_concrete + rebar_lintel_weight * get_rebar_price(lintel_rebar_type, p_db12, p_rb9) + form_lintel * p_formwork
            cost_lintel_lab = net_lintel_concrete * labour_concrete + net_rebar_lintel_weight * labour_rebar + net_form_lintel * labour_formwork

            total_wall_mat = cost_masonry_mat + cost_plaster_mat + cost_paint_mat + cost_lintel_mat
            total_wall_lab = cost_masonry_lab + cost_plaster_lab + cost_paint_lab + cost_lintel_lab

            add_takeoff_item({
                "หมวด": "งานผนังและฉาบปูน",
                "รายการ": wall_name,
                "ที่มาในแบบ": wall_ref,
                "แหล่งข้อมูล": "จากแบบสถาปัตย์",
                "รายละเอียด": f"{brick_type} | ก่อสุทธิ {net_wall_area:.1f} ตร.ม. / จัดซื้อ {proc_masonry_area:.1f} | ฉาบสุทธิ {net_plaster_area:.1f} / จัดซื้อ {proc_plaster_area:.1f} | ทาสีสุทธิ {net_paint_area:.1f} / จัดซื้อ {proc_paint_area:.1f} | เสาเอ็น/ทับหลัง {tot_lintel_len:.1f} ม.",
                "จำนวน": wall_qty,
                "คอนกรีตสุทธิ (ลบ.ม.)": round(net_lintel_concrete, 2),
            "คอนกรีต (ลบ.ม.)": round(vol_lintel_concrete, 2),
            "เหล็กสุทธิ (กก.)": round(net_rebar_lintel_weight, 2),
            "เหล็ก (กก.)": round(rebar_lintel_weight, 2),
                "เหล็กแยกชนิด": {lintel_rebar_type: round(rebar_lintel_weight, 2)},
                "ไม้แบบสุทธิ (ตร.ม.)": round(net_form_lintel, 2),
                "ไม้แบบ (ตร.ม.)": round(form_lintel, 2),
                "พื้นที่ก่อสุทธิ (ตร.ม.)": round(net_wall_area, 2),
                "พื้นที่ก่อ (ตร.ม.)": round(proc_masonry_area, 2),
                "พื้นที่ฉาบสุทธิ (ตร.ม.)": round(net_plaster_area, 2),
                "พื้นที่ฉาบ (ตร.ม.)": round(proc_plaster_area, 2),
                "ค่าวัสดุ (บาท)": round(total_wall_mat, 2),
                "ค่าแรง (บาท)": round(total_wall_lab, 2)
            })

    with st.expander("🚪 2. งานประตู - หน้าต่าง (บาน/วงกบ/อุปกรณ์)", expanded=True):
        dw1, dw2, dw3 = st.columns([1.5, 2.5, 1])
        dw_name = dw1.text_input("ชื่อ/สัญลักษณ์ประตู-หน้าต่าง", value="D1", key="dw_name")
        dw_type = dw2.selectbox("ประเภทชุดประตู-หน้าต่าง", DOOR_WINDOW_TYPES, key="dw_type")
        dw_qty = dw3.number_input("จำนวน (ชุด)", min_value=1, value=1, key="dw_qty")
        dw_ref = st.text_input("อ้างอิงจากแบบ (ไม่บังคับ)", value="", key="dw_ref", help="เช่น A-04 / D1 / หน้าห้องน้ำ")

        dw_dim1, dw_dim2 = st.columns(2)
        dw_w = dw_dim1.number_input("กว้างช่อง/ชุด (ม.)", min_value=0.0, value=0.90, step=0.05, key="dw_w")
        dw_h = dw_dim2.number_input("สูงช่อง/ชุด (ม.)", min_value=0.0, value=2.00, step=0.05, key="dw_h")
        dw_c1, dw_c2 = st.columns(2)
        cost_per_set_mat = dw_c1.number_input("ราคาชุดบาน+วงกบ+อุปกรณ์ (บาท/ชุด)", min_value=0.0, value=3500.0, step=100.0, key="cost_per_set_mat")
        cost_per_set_lab = dw_c2.number_input("ค่าแรงติดตั้ง (บาท/ชุด)", min_value=0.0, value=500.0, step=50.0, key="cost_per_set_lab")

        if st.button("➕ บันทึกงานประตู-หน้าต่าง", type="primary", key="btn_save_dw"):
            add_takeoff_item({
                "หมวด": "งานประตู-หน้าต่าง",
                "รายการ": dw_name,
                "ที่มาในแบบ": dw_ref,
                "แหล่งข้อมูล": "จากแบบสถาปัตย์",
                "รายละเอียด": f"{dw_type} | ขนาด {dw_w:.2f}x{dw_h:.2f}ม. | {dw_qty} ชุด | ราคาต่อชุด {cost_per_set_mat:,.0f} บาท",
                "จำนวน": dw_qty,
                "คอนกรีต (ลบ.ม.)": 0.0,
                "เหล็ก (กก.)": 0.0,
                "ไม้แบบ (ตร.ม.)": 0.0,
                "ค่าวัสดุ (บาท)": round(cost_per_set_mat * dw_qty, 2),
                "ค่าแรง (บาท)": round(cost_per_set_lab * dw_qty, 2)
            })

    with st.expander("✨ 3. งานปูพื้นและตกแต่งผิว (ระบุ กว้าง x ยาว)", expanded=False):
        fl1, fl2 = st.columns([2, 1])
        floor_name = fl1.text_input("ชื่อ/สัญลักษณ์หมวดงานปูพื้น", value="F-01 (กระเบื้องแกรนิตโต้)", key="floor_name")
        floor_qty = fl2.number_input("จำนวนห้อง/พื้นที่ (ชุด)", min_value=1, value=1, key="floor_qty")

        fl_dim1, fl_dim2, fl_type_col = st.columns([1, 1, 2])
        floor_w = fl_dim1.number_input("ความกว้าง (เมตร)", min_value=0.05, value=5.00, step=0.10, key="floor_w")
        floor_l = fl_dim2.number_input("ความยาว (เมตร)", min_value=0.05, value=7.00, step=0.10, key="floor_l")
        
        floor_material_type = fl_type_col.selectbox("ประเภทวัสดุปูพื้น", list(floor_price_map.keys()), key="floor_material_type")
        floor_ref = st.text_input("อ้างอิงจากแบบ (ไม่บังคับ)", value="", key="floor_ref", help="เช่น A-06 / ห้องรับแขก / F-01")

        if st.button("➕ บันทึกงานปูพื้น", type="primary", key="btn_save_floor"):
            area_calculated = (floor_w * floor_l * floor_qty)
            net_area = area_calculated
            proc_area = with_waste(net_area, waste_finishing)
            
            p_mat, p_lab = floor_price_map.get(floor_material_type, (p_tile_mat, labour_tile))
            
            add_takeoff_item({
                "หมวด": "งานปูพื้นและตกแต่งผิว",
                "รายการ": floor_name,
                "ที่มาในแบบ": floor_ref,
                "แหล่งข้อมูล": "จากแบบสถาปัตย์",
                "รายละเอียด": f"{floor_material_type} ขนาด {floor_w:.2f}x{floor_l:.2f}ม. ({floor_qty} ชุด) | สุทธิ {net_area:.1f} ตร.ม. | จัดซื้อ {proc_area:.1f} ตร.ม.",
                "จำนวน": floor_qty,
                "คอนกรีต (ลบ.ม.)": 0.0,
                "เหล็ก (กก.)": 0.0,
                "ไม้แบบ (ตร.ม.)": 0.0,
                "พื้นที่ปูพื้นสุทธิ (ตร.ม.)": round(net_area, 2),
                "พื้นที่ปูพื้น (ตร.ม.)": round(proc_area, 2),
                "ค่าวัสดุ (บาท)": round(proc_area * p_mat, 2),
                "ค่าแรง (บาท)": round(net_area * p_lab, 2)
            })

# =========================================================
# TAB 7: ☁️️ ฝ้าเพดาน
# =========================================================
with tabs[6]:
    st.subheader(f"☁️ ถอดปริมาณงานฝ้าเพดาน — [{active_proj_name}]")
    
    cl1, cl2 = st.columns([2, 1])
    ceiling_name = cl1.text_input("ชื่อ/สัญลักษณ์ฝ้าเพดาน", value="C-01 (ฝ้าฉาบเรียบ)", key="ceiling_name")
    ceiling_qty = cl2.number_input("จำนวนผืนฝ้า", min_value=1, value=1, key="ceiling_qty")

    cl_dim1, cl_dim2, cl_type_col = st.columns([1, 1, 2])
    ceiling_w = cl_dim1.number_input("ความกว้างฝ้า (เมตร)", min_value=0.05, value=5.00, step=0.10, key="ceiling_w")
    ceiling_l = cl_dim2.number_input("ความยาวฝ้า (เมตร)", min_value=0.05, value=8.00, step=0.10, key="ceiling_l")
    
    ceiling_type = cl_type_col.selectbox("ประเภทฝ้าเพดาน", list(ceiling_price_map.keys()), key="ceiling_type")
    ceiling_ref = st.text_input("อ้างอิงจากแบบ (ไม่บังคับ)", value="", key="ceiling_ref", help="เช่น A-07 / ห้องนั่งเล่น / C-01")

    if st.button("➕ บันทึกงานฝ้าเพดาน", type="primary", key="btn_save_ceiling"):
        area_calculated = (ceiling_w * ceiling_l * ceiling_qty)
        net_area = area_calculated
        proc_area = with_waste(net_area, waste_finishing)
        
        c_mat, c_lab = ceiling_price_map.get(ceiling_type, (p_ceiling_mat, labour_ceiling))
        
        add_takeoff_item({
            "หมวด": "งานฝ้าเพดาน",
            "รายการ": ceiling_name,
            "ที่มาในแบบ": ceiling_ref,
            "แหล่งข้อมูล": "จากแบบสถาปัตย์",
            "รายละเอียด": f"{ceiling_type} ขนาด {ceiling_w:.2f}x{ceiling_l:.2f}ม. ({ceiling_qty} ผืน) | สุทธิ {net_area:.1f} ตร.ม. | จัดซื้อ {proc_area:.1f} ตร.ม.",
            "จำนวน": ceiling_qty,
            "คอนกรีต (ลบ.ม.)": 0.0,
            "เหล็ก (กก.)": 0.0,
            "ไม้แบบ (ตร.ม.)": 0.0,
            "พื้นที่ฝ้าสุทธิ (ตร.ม.)": round(net_area, 2),
            "พื้นที่ฝ้า (ตร.ม.)": round(proc_area, 2),
            "ค่าวัสดุ (บาท)": round(proc_area * c_mat, 2),
            "ค่าแรง (บาท)": round(net_area * c_lab, 2)
        })

# =========================================================
# TAB 8: 🪜 บันได
# =========================================================
with tabs[7]:
    maybe_apply_drawing_prefill("บันได")
    st.subheader(f"🪜 ถอดปริมาณงานบันได คสล. — [{active_proj_name}]")
    st1,st2=st.columns(2)
    stair_name=st1.text_input("ชื่อ/สัญลักษณ์บันได",value="ST1",key="stair_name")
    stair_qty=st2.number_input("จำนวนชุดบันได",min_value=1,value=1,key="stair_qty")
    stair_ref=st.text_input("อ้างอิงจากแบบ (ไม่บังคับ)",value="",key="stair_ref",help="เช่น A-05 / ST1 — ใช้ตรวจสอบย้อนหลัง")
    a1,a2,a3,a4=st.columns(4)
    stair_w=a1.number_input("กว้างบันได (ม.)",min_value=0.05,value=1.20,step=0.05,key="stair_w")
    num_risers=a2.number_input("จำนวนลูกตั้ง",min_value=1,value=10,key="num_risers")
    num_treads=a3.number_input("จำนวนลูกนอน",min_value=1,value=9,key="num_treads")
    step_r_cm=a4.number_input("ลูกตั้ง (ซม.)",min_value=1.0,value=17.5,step=0.5,key="step_r_cm")
    a5,a6=st.columns(2)
    step_t_cm=a5.number_input("ลูกนอน (ซม.)",min_value=5.0,value=25.0,step=1.0,key="step_t_cm")
    slab_th_cm=a6.number_input("ความหนาท้องบันได (ซม.)",min_value=5.0,value=12.0,step=1.0,key="slab_th_cm")
    has_landing=st.checkbox("มีชานพัก",value=True,key="has_landing")
    land_w,land_l,land_th_cm=0.0,0.0,12.0
    if has_landing:
        l1,l2,l3=st.columns(3);land_w=l1.number_input("กว้างชานพัก (ม.)",min_value=0.0,value=1.20,step=0.10,key="land_w");land_l=l2.number_input("ยาวชานพัก (ม.)",min_value=0.0,value=2.40,step=0.10,key="land_l");land_th_cm=l3.number_input("หนาชานพัก (ซม.)",min_value=5.0,value=12.0,step=1.0,key="land_th_cm")
    sr1,sr2=st.columns(2); stair_rebar_type=sr1.selectbox("ชนิดเหล็กหลัก",REBAR_LIST,index=2,key="stair_rebar_type");stair_rebar_spacing=sr2.number_input("ระยะ @ เหล็กหลัก (ม.)",min_value=0.03,value=0.15,step=0.01,key="stair_rebar_spacing")
    auto_stair_geo=st.checkbox("ช่วยคำนวณความยาวเหล็กจากขนาดจาก Cover (ระยะหุ้มคอนกรีต)",value=True,key="auto_stair_geo")

    if st.button("➕ บันทึกงานบันได",type="primary",key="btn_save_stair"):
        step_r=step_r_cm/100.0;step_t=step_t_cm/100.0;slab_th=slab_th_cm/100.0;land_th=land_th_cm/100.0
        run_len=num_treads*step_t;rise_len=num_risers*step_r;inclined_len=math.sqrt(run_len**2+rise_len**2)
        # ปริมาตรฟอร์มแบบขั้นบันได: triangular fill + waist slab ตามแนวลาด (เป็นแบบประมาณ)
        vol_steps=(0.5*step_r*step_t*stair_w)*num_treads
        vol_slab=inclined_len*stair_w*slab_th
        vol_landing=(land_w*land_l*land_th) if has_landing else 0.0
        net_vol=(vol_steps+vol_slab+vol_landing)*stair_qty;tot_vol=with_waste(net_vol,waste_concrete)
        form_bottom=inclined_len*stair_w;form_risers=num_risers*step_r*stair_w;form_sides=2*inclined_len*slab_th
        form_landing=(land_w*land_l + 2*(land_w+land_l)*land_th) if has_landing else 0.0
        net_form=(form_bottom+form_risers+form_sides+form_landing)*stair_qty;tot_form=with_waste(net_form,waste_formwork)
        if stair_rebar_spacing<=0: st.error("ระยะห่างต้องมากกว่า 0");st.stop()
        num_main=count_by_spacing_clear(stair_w,stair_rebar_spacing,cover_slab_mm)
        num_cross=count_by_spacing_clear(inclined_len,stair_rebar_spacing,cover_slab_mm)
        main_len=clear_bar_length_m(inclined_len,cover_slab_mm)+2*lap_or_dev_len_m(stair_rebar_type,dev_factor_d) if auto_stair_geo else inclined_len+0.60
        cross_len=clear_bar_length_m(stair_w,cover_slab_mm) if auto_stair_geo else stair_w
        net_rebar_len=(num_main*main_len)+(num_cross*cross_len)
        net_rebar_weight=net_rebar_len*stair_qty*REBAR_WEIGHT[stair_rebar_type];tot_rebar_weight=with_waste(net_rebar_weight,waste_rebar)
        mat_c=tot_vol*p_concrete+tot_rebar_weight*get_rebar_price(stair_rebar_type,p_db12,p_rb9)+tot_form*p_formwork
        lab_c=net_vol*labour_concrete+net_rebar_weight*labour_rebar+net_form*labour_formwork
        add_takeoff_item({
            "หมวด":"งานบันได","รายการ":stair_name,"ที่มาในแบบ":stair_ref,"แหล่งข้อมูล":"จากแบบ/มิติขั้นบันได",
            "รายละเอียด":f"{num_risers} ลูกตั้ง / {num_treads} ลูกนอน | Run {run_len:.2f}ม. Rise {rise_len:.2f}ม. | Cover (ระยะหุ้มคอนกรีต) {cover_slab_mm:.0f}มม. | {stair_rebar_type}@{stair_rebar_spacing:.2f}",
            "จำนวน":stair_qty,"คอนกรีตสุทธิ (ลบ.ม.)":round(net_vol,2),"คอนกรีต (ลบ.ม.)":round(tot_vol,2),"เหล็กสุทธิ (กก.)":round(net_rebar_weight,2),"เหล็ก (กก.)":round(tot_rebar_weight,2),"เหล็กแยกชนิด":{stair_rebar_type:round(tot_rebar_weight,2)},"ไม้แบบสุทธิ (ตร.ม.)":round(net_form,2),"ไม้แบบ (ตร.ม.)":round(tot_form,2),"ค่าวัสดุ (บาท)":round(mat_c,2),"ค่าแรง (บาท)":round(lab_c,2)
        })

# =========================================================
# TAB 9: ⛺ หลังคา
# =========================================================
with tabs[8]:
    maybe_apply_drawing_prefill("หลังคา")
    st.subheader(f"⛺ ถอดปริมาณงานหลังคา — แยกชนิดโครงเหล็กจากแบบ — [{active_proj_name}]")
    r1,r2,r3=st.columns([1,1.5,1.5])
    roof_name=r1.text_input("ชื่อ/สัญลักษณ์หลังคา",value="R1",key="roof_name")
    roof_shape=r2.selectbox("ประเภททรงหลังคา (ใช้ระบุชนิด ไม่ใช้ปรับพื้นที่)",ROOF_SHAPES,key="roof_shape")
    roof_material=r3.selectbox("วัสดุมุง",list(ROOF_MATERIAL_SPECS.keys()),key="roof_material")
    roof_ref=st.text_input("อ้างอิงจากแบบ (ไม่บังคับ)",value="",key="roof_ref",help="เช่น R-01 / R1 — ใช้ตรวจสอบย้อนหลัง")
    ra1,ra2,ra3=st.columns(3)
    roof_area_mode=ra1.selectbox("วิธีหาพื้นที่มุง",["คำนวณจากผัง + มุมลาด","ระบุพื้นที่มุงจริงจากแบบ"],key="roof_area_mode")
    roof_overhang=ra2.number_input("ชายคาเฉลี่ยด้านละ (ม.)",min_value=0.0,value=0.30,step=0.05,key="roof_overhang")
    roof_manual_area=ra3.number_input("พื้นที่มุงจริงจากแบบ (ตร.ม.)",min_value=0.0,value=0.0,step=0.5,key="roof_manual_area")
    rc1,rc2,rc3,rc4=st.columns(4)
    roof_plan_w=rc1.number_input("กว้างผังหลังคา (ม.)",min_value=0.05,value=10.0,step=0.5,key="roof_plan_w")
    roof_plan_l=rc2.number_input("ยาวผังหลังคา (ม.)",min_value=0.05,value=12.0,step=0.5,key="roof_plan_l")
    roof_pitch=rc3.number_input("ความชัน (องศา)",min_value=0.0,max_value=85.0,value=30.0,step=1.0,key="roof_pitch")
    ridge_len=rc4.number_input("ครอบสัน/ตะเข้สันรวม (ม.)",min_value=0.0,value=25.0,step=1.0,key="ridge_len")
    mat_spec=ROOF_MATERIAL_SPECS.get(roof_material,{"mat":300.0,"lab":120.0,"steel_factor":20.0})
    plan_area=max(0.0,(roof_plan_w+2*roof_overhang)*(roof_plan_l+2*roof_overhang))
    cos_val=math.cos(math.radians(roof_pitch));pitch_slope_factor=1.0/cos_val if cos_val>0.001 else 1.0
    auto_roof_area=plan_area*pitch_slope_factor
    real_roof_area=roof_manual_area if roof_area_mode=="ระบุพื้นที่มุงจริงจากแบบ" and roof_manual_area>0 else auto_roof_area
    st.info(f"พื้นที่มุงที่ใช้คำนวณ {real_roof_area:.2f} ตร.ม. | พื้นที่ฉาย {plan_area:.2f} ตร.ม. | ตัวคูณความชัน {pitch_slope_factor:.3f} | แนะนำ: ใช้พื้นที่จริงจากแบบเมื่อระบุไว้")

    st.markdown("#### 🏗️ ชนิดโครงเหล็กหลังคาจากแบบ")
    st.caption("ตรงนี้หมายถึง **ชนิดเหล็กที่ใช้ทำโครงหลังคาจริง** เช่น แป, จันทัน, อะเส, ขื่อ พร้อมขนาด ความยาว และจำนวน — ไม่ใช่ประเภททรงหลังคา")
    st.caption("ถ้ายังไม่มีรายการเหล็กจากแบบ ให้เลือก ‘ประเมินจากพื้นที่ (ตัวสำรอง)’ เพื่อไม่ให้ระบบสร้างปริมาณโครงเหล็กแบบเดาเอง")
    if "roof_members" not in st.session_state:
        st.session_state["roof_members"]=[]
    if st.button("➕ เพิ่มชนิดโครงเหล็กจากแบบ",key="btn_add_roof_member"):
        st.session_state["roof_members"].append({"member":"","desc":"","len":0.0,"qty":0.0,"kg_m":0.0})
        st.rerun()
    rm_to_remove=[];net_member_weight=0.0;member_rows=[]
    for idx,m in enumerate(st.session_state["roof_members"]):
        m.setdefault("member","");m.setdefault("desc","");m.setdefault("kg_m",0.0);m.setdefault("len",0.0);m.setdefault("qty",0.0)
        a1,a2,a3,a4,a5,a6=st.columns([1.6,1.7,1.1,1.0,1.0,0.45])
        m["member"]=a1.text_input(f"ชนิดเหล็ก/ขนาด #{idx+1}",value=str(m.get("member","")),placeholder="เช่น C-125x50x20x3.2",key=f"rm_profile_{idx}")
        m["desc"]=a2.text_input(f"ใช้เป็นอะไร #{idx+1}",value=str(m.get("desc","")),key=f"rm_desc_{idx}")
        m["len"]=a3.number_input(f"ความยาวต่อชิ้น (ม.) #{idx+1}",min_value=0.0,value=max(0.0,safe_num(m.get("len",1.0))),step=0.10,key=f"rm_len_{idx}")
        m["qty"]=a4.number_input(f"จำนวน #{idx+1}",min_value=0.0,value=max(0.0,safe_num(m.get("qty",1))),step=1.0,key=f"rm_qty_{idx}")
        default_kg=COMMON_STEEL_KG_M.get(str(m["member"]).strip(),safe_num(m.get("kg_m",0.0)))
        m["kg_m"]=a5.number_input(f"น้ำหนักต่อเมตร #{idx+1}",min_value=0.0,value=max(0.0,default_kg),step=0.01,key=f"rm_kgm_{idx}")
        if a6.button("🗑",key=f"del_rm_{idx}"):rm_to_remove.append(idx)
        w=safe_num(m["len"])*safe_num(m["qty"])*safe_num(m["kg_m"]);net_member_weight+=w
        member_rows.append({"ชนิด/ขนาดเหล็ก":m["member"],"ใช้เป็นอะไร":m["desc"],"ความยาว (ม.)":m["len"],"จำนวน":m["qty"],"น้ำหนักต่อเมตร":m["kg_m"],"น้ำหนักสุทธิ (กก.)":round(w,2)})
    if rm_to_remove:
        st.session_state["roof_members"]=[m for i,m in enumerate(st.session_state["roof_members"]) if i not in rm_to_remove];st.rerun()

    roof_steel_options=["ใช้ชนิดโครงเหล็กจากแบบ","ประเมินจากพื้นที่ (ตัวสำรอง)"]
    roof_default_index=0 if st.session_state.get("roof_members") else 1
    roof_steel_mode=st.selectbox("วิธีคำนวณน้ำหนักโครงเหล็ก",roof_steel_options,index=roof_default_index,key="roof_steel_mode")
    if roof_steel_mode.startswith("ใช้"):
        net_steel_weight=net_member_weight
    else:
        net_steel_weight=real_roof_area*mat_spec["steel_factor"]
    tot_steel_weight=with_waste(net_steel_weight,waste_roof_steel)

    if st.button("➕ บันทึกงานหลังคา",type="primary",key="btn_save_roof"):
        if roof_steel_mode.startswith("ใช้"):
            incomplete = (net_member_weight <= 0 or any(safe_num(m.get("len", 0)) <= 0 or safe_num(m.get("qty", 0)) <= 0 or safe_num(m.get("kg_m", 0)) <= 0 for m in st.session_state.get("roof_members", [])))
            if incomplete:
                st.error("ข้อมูลโครงเหล็กยังไม่ครบ — ใส่ชนิด/ขนาด + ความยาวต่อชิ้น + จำนวน + น้ำหนักต่อเมตรจากแบบ หรือเลือก ‘ประเมินจากพื้นที่’")
                st.stop()
        tile_area=with_waste(real_roof_area,waste_roof)
        mat_roof_tiles=tile_area*mat_spec["mat"];lab_roof_tiles=real_roof_area*mat_spec["lab"]
        mat_ridge=ridge_len*p_roof_cap;lab_ridge=ridge_len*60.0
        mat_steel=tot_steel_weight*p_roof_steel;lab_steel=net_steel_weight*labour_roof_steel
        add_takeoff_item({
            "หมวด":"งานหลังคา","รายการ":roof_name,"ที่มาในแบบ":roof_ref,"แหล่งข้อมูล":"จากแบบโครงหลังคา" if roof_steel_mode.startswith("ใช้") else "ประมาณจากพื้นที่",
            "รายละเอียด":f"{roof_shape} | {roof_material} | มุงสุทธิ {real_roof_area:.1f}/จัดซื้อ {tile_area:.1f}ตร.ม. | โครงเหล็กสุทธิ {net_steel_weight:.1f}/จัดซื้อ {tot_steel_weight:.1f}กก.",
            "จำนวน":1,"เหล็กสุทธิ (กก.)":round(net_steel_weight,2),"เหล็ก (กก.)":round(tot_steel_weight,2),"เหล็กแยกชนิด":{"โครงเหล็กหลังคา":round(tot_steel_weight,2)},"พื้นที่หลังคา (ตร.ม.)":round(tile_area,2),
            "ชนิดโครงเหล็กจากแบบ":member_rows,"ค่าวัสดุ (บาท)":round(mat_roof_tiles+mat_ridge+mat_steel,2),"ค่าแรง (บาท)":round(lab_roof_tiles+lab_ridge+lab_steel,2)
        })

# =========================================================
# TAB 10: 🧮 คำนวณ
# =========================================================
with tabs[9]:
    st.subheader(f"🧮 ตรวจสอบปริมาณรวม — [{active_proj_name}]")
    if not current_proj or not current_proj.get("items"):
        st.info("ยังไม่มีรายการถอดแบบในโครงการนี้")
    else:
        items=current_proj.get("items",[])
        df_items=pd.DataFrame(items)
        for col in ["คอนกรีต (ลบ.ม.)","เหล็ก (กก.)","ไม้แบบ (ตร.ม.)","ค่าวัสดุ (บาท)","ค่าแรง (บาท)"]:
            if col not in df_items.columns:df_items[col]=0.0
            df_items[col]=pd.to_numeric(df_items[col],errors="coerce").fillna(0.0)
        tot_conc=df_items["คอนกรีต (ลบ.ม.)"].sum();tot_rebar=sum(sum_rebar_breakdown(items));tot_roof_steel=sum_structural_steel(items);tot_form=df_items["ไม้แบบ (ตร.ม.)"].sum();tot_mat=df_items["ค่าวัสดุ (บาท)"].sum();tot_lab=df_items["ค่าแรง (บาท)"].sum()
        m1,m2,m3,m4,m5=st.columns(5);m1.metric("คอนกรีต",f"{tot_conc:,.2f} ลบ.ม.");m2.metric("เหล็กเสริม",f"{tot_rebar:,.2f} กก.");m3.metric("โครงเหล็กหลังคา",f"{tot_roof_steel:,.2f} กก.");m4.metric("ไม้แบบ",f"{tot_form:,.2f} ตร.ม.");m5.metric("ตรงวัสดุ+แรง",f"฿{tot_mat+tot_lab:,.0f}")
        st.dataframe(df_items,use_container_width=True,hide_index=True)
        steel_totals=sum_rebar_breakdown(items)
        if steel_totals:
            st.markdown("#### 🔩 สรุปเหล็กเส้นตามขนาด");st.dataframe(pd.DataFrame([{"ชนิดเหล็ก":k,"น้ำหนักสุทธิ+Waste (กก.)":round(v,2)} for k,v in sorted(steel_totals.items())]),use_container_width=True,hide_index=True)
        roof_steel_rows=[]
        for it in items:
            if it.get("หมวด")=="งานหลังคา":
                for row in it.get("ชนิดโครงเหล็กจากแบบ",[]) or []:
                    roof_steel_rows.append(row)
        if roof_steel_rows:
            st.markdown("#### 🏗️ สรุปโครงเหล็กหลังคาแยกชนิด/ขนาด");st.dataframe(pd.DataFrame(roof_steel_rows),use_container_width=True,hide_index=True)

# =========================================================
# TAB 11: 📋 BOQ
# =========================================================
with tabs[10]:
    st.subheader(f"📋 BOQ สำหรับเสนอราคา — [{active_proj_name}]")
    if not current_proj or not current_proj.get("items"):
        st.info("ยังไม่มีรายการถอดแบบในโครงการนี้")
    else:
        items=current_proj.get("items",[]);df=pd.DataFrame(items)
        for col in ["ค่าวัสดุ (บาท)","ค่าแรง (บาท)"]:
            if col not in df.columns:df[col]=0.0
            df[col]=pd.to_numeric(df[col],errors="coerce").fillna(0.0)
        df["รวมเงิน (บาท)"]=df["ค่าวัสดุ (บาท)"]+df["ค่าแรง (บาท)"]
        st.markdown("#### 📄 รายการ BOQ");st.dataframe(df,use_container_width=True,hide_index=True)
        grouped=df.groupby("หมวด",dropna=False)[["ค่าวัสดุ (บาท)","ค่าแรง (บาท)"]].sum().reset_index();grouped["รวมเงิน (บาท)"]=grouped["ค่าวัสดุ (บาท)"]+grouped["ค่าแรง (บาท)"]
        st.markdown("#### 💰 สรุปราคาตามหมวด");st.dataframe(grouped.style.format({"ค่าวัสดุ (บาท)":"{:,.2f}","ค่าแรง (บาท)":"{:,.2f}","รวมเงิน (บาท)":"{:,.2f}"}),use_container_width=True)
        subtotal_mat=grouped["ค่าวัสดุ (บาท)"].sum();subtotal_lab=grouped["ค่าแรง (บาท)"].sum();subtotal_direct=subtotal_mat+subtotal_lab;profit_amount=subtotal_direct*profit_percent;vat_amount=(subtotal_direct+profit_amount)*0.07 if use_vat else 0.0;grand_total=subtotal_direct+profit_amount+vat_amount
        c1,c2=st.columns(2)
        with c2:
            st.markdown(f"**ค่าวัสดุ** {subtotal_mat:,.2f} บาท")
            st.markdown(f"**ค่าแรง** {subtotal_lab:,.2f} บาท")
            st.markdown(f"**รวมต้นทุนตรง** {subtotal_direct:,.2f} บาท")
            st.markdown(f"**ดำเนินการ+กำไร ({profit_percent*100:.1f}%)** {profit_amount:,.2f} บาท")
            if use_vat:st.markdown(f"**VAT 7%** {vat_amount:,.2f} บาท")
            st.markdown(f"### **ราคาขายรวม {grand_total:,.2f} บาท**")
        # Excel export
        export_buf=io.BytesIO()
        with pd.ExcelWriter(export_buf,engine="openpyxl") as writer:
            df.to_excel(writer,index=False,sheet_name="BOQ Detail")
            grouped.to_excel(writer,index=False,sheet_name="BOQ Summary")
            pd.DataFrame([{"รายการ":"ค่าวัสดุ","บาท":subtotal_mat},{"รายการ":"ค่าแรง","บาท":subtotal_lab},{"รายการ":"ต้นทุนตรง","บาท":subtotal_direct},{"รายการ":"ดำเนินการ+กำไร","บาท":profit_amount},{"รายการ":"VAT","บาท":vat_amount},{"รายการ":"ราคาขายรวม","บาท":grand_total}]).to_excel(writer,index=False,sheet_name="Price Summary")
            # ปรับ Excel ให้พร้อมตรวจ/ส่งต่อ: freeze header, filter และความกว้างคอลัมน์
            for ws in writer.book.worksheets:
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for col_cells in ws.columns:
                    max_len = 0
                    letter = col_cells[0].column_letter
                    for cell in col_cells[:100]:
                        max_len = max(max_len, len(str(cell.value or "")))
                    ws.column_dimensions[letter].width = min(max(max_len + 2, 10), 45)
        st.download_button("📥 ดาวน์โหลด BOQ Excel",data=export_buf.getvalue(),file_name=f"BOQ_{safe_filename(active_proj_name)}.xlsx",mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",use_container_width=True)

# =========================================================
# TAB 12: 📊 สรุป
# =========================================================
with tabs[11]:
    st.subheader(f"📊 ตรวจความพร้อมก่อนเสนอราคา — [{active_proj_name}]")
    if not current_proj or not current_proj.get("items"):
        st.info("ยังไม่มีข้อมูลโครงการ")
    else:
        items=current_proj.get("items",[]);df=pd.DataFrame(items)
        for col in ["คอนกรีต (ลบ.ม.)","เหล็ก (กก.)","ไม้แบบ (ตร.ม.)","ค่าวัสดุ (บาท)","ค่าแรง (บาท)"]:
            if col not in df.columns:df[col]=0.0
            df[col]=pd.to_numeric(df[col],errors="coerce").fillna(0.0)
        # duplicate audit
        sig_map={}
        for it in items:sig_map.setdefault(item_signature(it),[]).append(it)
        dups=[v for v in sig_map.values() if len(v)>1]
        near_map={}
        for it in items:
            key=(str(it.get("หมวด","")).strip(),str(it.get("รายการ","")).strip(),get_item_source(it))
            if all(key): near_map.setdefault(key,[]).append(it)
        near_dups=[v for v in near_map.values() if len(v)>1]
        missing_refs=[it for it in items if not get_item_source(it)]
        legacy=[it for it in items if not it.get("ข้อมูลปริมาณสุทธิแยกจาก Waste",False)]
        estimated=[it for it in items if str(it.get("แหล่งข้อมูล","")).startswith("ประมาณ")]
        negative=[]
        for it in items:
            for c in ["คอนกรีต (ลบ.ม.)","เหล็ก (กก.)","ไม้แบบ (ตร.ม.)","ค่าวัสดุ (บาท)","ค่าแรง (บาท)"]:
                if safe_num(it.get(c,0))<0:negative.append((it.get("รายการ"),c))
        q1,q2,q3,q4,q5,q6=st.columns(6)
        q1.metric("รายการทั้งหมด",len(items));q2.metric("ซ้ำตรงกัน",len(dups));q3.metric("อาจซ้ำ",len(near_dups));q4.metric("ยังไม่มีที่มาจากแบบ",len(missing_refs));q5.metric("ข้อมูลเก่า",len(legacy));q6.metric("ปริมาณประมาณการ",len(estimated))
        if dups:st.warning("พบรายการที่อาจบันทึกซ้ำ — ตรวจอ้างอิงจากแบบก่อนเสนอราคา");st.dataframe(pd.DataFrame([{"รายการ":v[0].get("รายการ",""),"ที่มาในแบบ":get_item_source(v[0]) ,"จำนวนที่พบซ้ำ":len(v)} for v in dups]),use_container_width=True,hide_index=True)
        else:st.success("✅ ไม่พบรายการซ้ำแบบตรงกัน")
        if missing_refs:st.info(f"มี {len(missing_refs)} รายการที่ยังไม่มีอ้างอิงจากแบบ — แนะนำให้ใส่แผ่น/ตำแหน่ง เพื่อการตรวจสอบย้อนหลัง")
        if estimated:st.warning(f"มี {len(estimated)} รายการที่เป็นปริมาณประมาณ — ก่อนเสนอราคาให้แทนด้วยปริมาณจากแบบเมื่อมีข้อมูลจริง")
        if negative:st.error(f"พบค่าติดลบ {len(negative)} จุด ควรตรวจข้อมูลก่อนเสนอราคา")
        tot_mat=df["ค่าวัสดุ (บาท)"].sum();tot_lab=df["ค่าแรง (บาท)"].sum();
        st.markdown("#### 🧾 ตัวเลขหลัก");
        st.dataframe(pd.DataFrame([{"ตัวชี้วัด":"คอนกรีต","ปริมาณ":df["คอนกรีต (ลบ.ม.)"].sum(),"หน่วย":"ลบ.ม."},{"ตัวชี้วัด":"เหล็กเสริม","ปริมาณ":sum(sum_rebar_breakdown(items)),"หน่วย":"กก."},{"ตัวชี้วัด":"โครงเหล็กหลังคา","ปริมาณ":sum_structural_steel(items),"หน่วย":"กก."},{"ตัวชี้วัด":"ไม้แบบ","ปริมาณ":df["ไม้แบบ (ตร.ม.)"].sum(),"หน่วย":"ตร.ม."},{"ตัวชี้วัด":"ต้นทุนตรง","ปริมาณ":tot_mat+tot_lab,"หน่วย":"บาท"}]),use_container_width=True,hide_index=True)
