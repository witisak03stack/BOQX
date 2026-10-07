import streamlit as st
import pandas as pd
import math
import io
import json
import os
import copy
import re
import hashlib

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
    page_title="AI ถอด BOQ งานโครงสร้าง & สถาปัตย์ V8.3.1 Reliability + Drawing Reader",
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
    """ระยะทาบ/ระยะฝาก/ฝังปลายเหล็กแบบปรับค่า factor ได้ (m). เป็นค่าใช้งานประมาณการ; แบบ/Spec มีผลเหนือกว่า"""
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
    lap_mode = normalize_lap_mode(lap_mode)
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
    elif lap_mode == "ฝาก/ฝังปลายเหล็ก":
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

CURRENT_REBAR_PRICES = {}

def get_rebar_price(rebar_type, db_price, rb_price):
    """คืนราคาต่อกก. ตามชนิดเหล็ก โดยใช้ราคาที่ตั้งแยกตามขนาดก่อน ถ้าไม่มีจึงใช้ราคา fallback"""
    r = str(rebar_type or "").upper()
    if r in CURRENT_REBAR_PRICES:
        return safe_num(CURRENT_REBAR_PRICES[r])
    return safe_num(db_price if r.startswith("DB") else rb_price)


# ค่าแรงเหล็กตามขนาดจาก ว480 พ.ศ. 2569 (บาท/กก.)
REBAR_LABOR_W480 = {
    "<10": 4.90,
    "10-16": 3.90,
    ">16": 3.50,
}

def get_rebar_labor_rate(rebar_type, fallback_rate=3.90):
    d = rebar_diameter_mm(rebar_type)
    if d <= 0:
        return safe_num(fallback_rate)
    if d < 10:
        return REBAR_LABOR_W480["<10"]
    if d <= 16:
        return REBAR_LABOR_W480["10-16"]
    return REBAR_LABOR_W480[">16"]

def calc_rebar_labor_from_breakdown(weight_by_type, fallback_rate=3.90, use_w480=True):
    """คำนวณค่าแรงผูกเหล็ก: ใช้ ว480 ตามขนาดเมื่อเปิดใช้; ปิดเพื่อใช้อัตราที่ผู้ใช้กรอกเอง"""
    total = 0.0
    for rtype, wt in (weight_by_type or {}).items():
        rate = get_rebar_labor_rate(rtype, fallback_rate) if use_w480 and rtype in REBAR_WEIGHT else safe_num(fallback_rate)
        total += safe_num(wt) * rate
    return total

def excavation_labor_w480_rate(volume_m3, depth_m, manual_rate=153.0, auto=True):
    """อัตราค่าแรงขุดดินทั่วไป ว480: >100 ลบ.ม. หรือ <=1.0ม. =121, 25-100 หรือ 1.0-1.5ม.=153, ต่ำกว่า25 หรือ >1.5ม.=181"""
    if not auto:
        return max(0.0, safe_num(manual_rate))
    v = max(0.0, safe_num(volume_m3)); d = max(0.0, safe_num(depth_m))
    if v > 100 or d <= 1.0:
        return 121.0
    if v >= 25 and d <= 1.5:
        return 153.0
    return 181.0


def safe_num(value, default=0.0):
    """แปลงค่าตัวเลขจากข้อมูลเดิม/JSON ให้ปลอดภัย"""
    try:
        x = float(value)
        return x if math.isfinite(x) else float(default)
    except (TypeError, ValueError):
        return float(default)

def normalize_lap_mode(value):
    """รองรับข้อมูลเก่าจาก V8.1 และลดปัญหา selectbox หา index ไม่เจอ"""
    x=str(value or "").strip()
    if x in ("พัฒนา/ฝัง", "พัฒนา", "ฝัง", "ระยะพัฒนา/ฝัง", "Development", "Develop"):
        return "ฝาก/ฝังปลายเหล็ก"
    if x in ("ทาบ", "Lap"):
        return "ทาบ"
    return "ไม่มี" if x not in ("ฝาก/ฝังปลายเหล็ก",) else x


def get_item_source(item):
    return str(item.get("ที่มาในแบบ") or item.get("อ้างอิงแบบ") or "").strip()

# ---------------------------------------------------------
# Drawing Reader (PDF -> review candidates)
# ---------------------------------------------------------
STRUCTURAL_MARK_RE = re.compile(r"(?<![A-Za-z0-9])([FCBSWRD]\s*\d+[A-Za-z]?)\b", re.I)
STRUCTURAL_DASH_MARK_RE = re.compile(r"(?<![A-Za-z0-9])([FCBWRD]\s*[-.]\s*\d+[A-Za-z]?)\b", re.I)
DIM_RE = re.compile(r"(?<![A-Za-z0-9])([0-9]+(?:[.,][0-9]+)?)\s*[x*]\s*([0-9]+(?:[.,][0-9]+)?)(?:\s*[x*]\s*([0-9]+(?:[.,][0-9]+)?))?", re.I)
DIM_UNIT_RE = re.compile(r"(?<![A-Za-z0-9])([0-9]+(?:[.,][0-9]+)?)\s*[x*]\s*([0-9]+(?:[.,][0-9]+)?)(?:\s*[x*]\s*([0-9]+(?:[.,][0-9]+)?))?\s*(mm|cm|m|เมตร)\b", re.I)
REBAR_COUNT_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9])([0-9]{1,3})\s*[-x×]?\s*(DB\s*\d+|RB\s*\d+)\b", re.I),
    re.compile(r"(?<![A-Za-z0-9])(DB\s*\d+|RB\s*\d+)\s*[-x×]?\s*([0-9]{1,3})\s*(?:เส้น|bars?|ea)?\b", re.I),
    re.compile(r"(?<![A-Za-z0-9])(DB\s*\d+|RB\s*\d+)\s*จำนวน\s*([0-9]{1,3})\b", re.I),
]
REBAR_SPACING_RE = re.compile(r"\b(DB\s*\d+|RB\s*\d+|D\s*\d+)\s*@\s*([0-9]+(?:[.,][0-9]+)?)\s*(mm|cm|m)?", re.I)
LENGTH_RE = re.compile(r"(?:\bL\b|ยาว|length|span)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\s*(mm|cm|m|เมตร)?", re.I)
HEIGHT_RE = re.compile(r"(?:\bH\b|สูง|height|ลึก|หนา|th)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\s*(mm|cm|m|เมตร)?", re.I)
QTY_RE = re.compile(r"(?:จำนวน|qty|quantity|no\.?|nos\.?)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)", re.I)
QTY_SUFFIX_RE = re.compile(r"\b([0-9]+(?:\.[0-9]+)?)\s*(?:EA|EACH|ต้น|ชุด|ผืน|บาน|ชิ้น)\b", re.I)
AREA_RE = re.compile(r"(?:พื้นที่|area)\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:ตร\.?ม\.?|m2|m²|sqm)?", re.I)
SHEET_RE = re.compile(r"\b(?:S|ST|A|AR|STR|ARCH)[-\s]+\d{1,4}\b", re.I)
GRID_RE = re.compile(r"\bGrid\s*[A-Z0-9]+(?:\s*[-/]\s*[A-Z0-9]+)\b", re.I)
STEEL_PROFILE_RE = re.compile(r"\b(?:H|C|Tube|RHS|SHS|Angle|L)\s*[-]?\s*[0-9]+(?:\.[0-9]+)?(?:\s*[xX]\s*[0-9]+(?:\.[0-9]+)?){2,4}(?:\.[0-9]+)?\b", re.I)

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

def infer_rebar_position(text, start, end):
    """หาตำแหน่งเหล็กจากคำบอกตำแหน่งที่ใกล้กับเหล็กชุดนั้นที่สุด เพื่อลดการนำคำว่า ‘เหล็กปลอก’ ไปติดกับเหล็กหลักอีกชุด"""
    t = normalize_drawing_text(text)
    role_patterns = [
        ("เหล็กปลอก", "เหล็กปลอก"), ("stirrup", "เหล็กปลอก"), ("tie", "เหล็กปลอก"),
        ("เหล็กบน", "เหล็กบน"), ("top", "เหล็กบน"),
        ("เหล็กล่าง", "เหล็กล่าง"), ("bottom", "เหล็กล่าง"),
    ]
    best = None
    for pattern, label in role_patterns:
        for m in re.finditer(pattern, t[max(0, start-24):min(len(t), end+24)], re.I):
            base = max(0, start-24)
            abs_start = base + m.start()
            abs_end = base + m.end()
            # ถ้ามีเหล็กอีกตัวคั่นระหว่างเหล็กเป้าหมายกับคำบอกตำแหน่ง
            # จะไม่ใช้คำบอกตำแหน่งนั้นกับเหล็กตัวแรก
            if abs_start > end:
                between = t[end:abs_start]
            elif abs_end < start:
                between = t[abs_end:start]
            else:
                between = ""
            if re.search(r"(?:DB|RB)\s*\d+", between, re.I):
                continue
            if abs_end < start:
                dist = start - abs_end
            elif abs_start > end:
                dist = abs_start - end
            else:
                dist = 0
            if dist <= 18 and (best is None or dist < best[0]):
                best = (dist, label)
    return best[1] if best else "เหล็กเสริมพิเศษ"


def parse_rebar_specs(context):
    rows = []
    seen = set()
    text = normalize_drawing_text(context)

    for m in REBAR_SPACING_RE.finditer(text):
        rtype = normalize_rebar_type(m.group(1))
        if not rtype:
            continue
        spacing = spacing_to_m(m.group(2), m.group(3))
        pos = infer_rebar_position(text, m.start(), m.end())
        key = (rtype, "spacing", round(spacing, 6), pos)
        if key not in seen and spacing > 0:
            rows.append({"pos": pos, "type": rtype, "mode": "ระยะห่าง (@ ม.)", "val": spacing, "len": 0.0, "lap_mode": "ไม่มี", "lap_ends": 0})
            seen.add(key)

    for pattern_index, pat in enumerate(REBAR_COUNT_PATTERNS):
        for m in pat.finditer(text):
            if pattern_index == 0:
                count_raw, type_raw = m.group(1), m.group(2)
            else:
                type_raw, count_raw = m.group(1), m.group(2)
            rtype = normalize_rebar_type(type_raw)
            if not rtype:
                continue
            count = max(1, int(round(_num_token(count_raw))))
            pos = infer_rebar_position(text, m.start(), m.end())
            key = (rtype, "count", count, pos)
            if key not in seen:
                rows.append({"pos": pos, "type": rtype, "mode": "จำนวน (เส้น)", "val": float(count), "len": 0.0, "lap_mode": "ไม่มี", "lap_ends": 0})
                seen.add(key)
    return rows

def dimension_values_to_m(values, target, unit=None):
    """แปลงมิติจากแบบเป็นเมตรแบบอนุรักษ์นิยม โดยคำนึงถึงประเภทสมาชิกเพื่อลดการตีความ 12x16 ผิดหน่วย"""
    vals=[_num_token(v) for v in values]
    if unit:
        return [token_to_m(v, unit) for v in vals]
    t=str(target or "")
    if t in ("ผนัง", "ประตู-หน้าต่าง", "หลังคา"):
        # งานสถาปัตย์/ผัง: ทศนิยมและค่าขนาดไม่เกิน 50 มักเป็นเมตร
        if any("." in str(v) or "," in str(v) for v in values) or all(0 < v <= 50 for v in vals):
            return vals
    # งานโครงสร้าง: 200 -> 0.20m, 20 -> 0.20m, 0.20 -> 0.20m
    out=[]
    for v in vals:
        if v >= 100:
            out.append(v/1000.0)
        elif v >= 10:
            out.append(v/100.0)
        else:
            out.append(v)
    return out


def _sheet_tags_from_context(context, page_no):
    explicit = []
    for m in re.finditer(r"(?:sheet|drawing\s*no\.?|แผ่นแบบ|เลขที่แบบ)\s*[:#]?\s*((?:S|ST|A|AR|STR|ARCH)[-\s]*\d{1,4})\b", normalize_drawing_text(context), re.I):
        explicit.append(re.sub(r"\s+", "", m.group(1)).upper())
    if explicit:
        return list(dict.fromkeys(explicit))
    # fallback เฉพาะรหัสที่มีขีดและเลข 2 หลักขึ้นไป เพื่อลดชนกับสมาชิก S-1/C-1
    return list(dict.fromkeys(m.group(0).upper().replace(" ", "") for m in re.finditer(r"\b(?:S|ST|A|AR|STR|ARCH)-\d{2,4}\b", normalize_drawing_text(context), re.I)))


DRAWING_CONTEXT_KEYWORDS = {
    "F": ("ฐานราก", "ฐาน", "footing", "foundation", "pile cap"),
    "C": ("เสา", "column", "col.", "reinforced concrete"),
    "B": ("คาน", "beam", "girder"),
    "S": ("พื้น", "slab", "floor", "deck"),
    "W": ("ผนัง", "wall", "partition"),
    "R": ("หลังคา", "roof", "rafter", "purlin", "truss"),
    "D": ("ประตู", "หน้าต่าง", "door", "window", "frame", "วงกบ"),
}

def mark_context_supported(kind, text):
    src = normalize_drawing_text(text).lower()
    terms = DRAWING_CONTEXT_KEYWORDS.get(kind, ())
    return any(term.lower() in src for term in terms)


def _candidate_from_context(mark_match, context, page_no, surrounding_context=""):
    raw_mark = mark_match.group(1) if mark_match.lastindex else mark_match.group(0)
    raw_clean = raw_mark.strip().upper()
    if ("-" in raw_clean or "." in raw_clean):
        local_before = surrounding_context[max(0, mark_match.start()-40):mark_match.start()]
        if re.search(r"Grid\s*$", local_before, re.I):
            return None
        structural_context = re.search(r"เสา|คาน|ฐานราก|ฐาน|column|beam|footing|foundation|rebar|เหล็ก|คอนกรีต|foundation", surrounding_context[max(0, mark_match.start()-120):min(len(surrounding_context), mark_match.end()+180)], re.I)
        if not structural_context:
            return None
    if raw_clean.startswith("D") and re.match(r"\s*@", surrounding_context[mark_match.end():mark_match.end()+4]):
        return None
    mark = re.sub(r"\s+", "", raw_mark.upper())
    kind = mark[0]
    target = {"F":"ฐานราก","C":"เสา","B":"คาน","S":"พื้น","W":"ผนัง","R":"หลังคา","D":"ประตู-หน้าต่าง"}.get(kind,"อื่นๆ")
    ctx = normalize_drawing_text(context)
    full_context = normalize_drawing_text((surrounding_context or "") + " " + ctx)
    # ลดรหัสหลงจาก Drawing/Architecture เช่น D1 ที่เป็น detail ไม่ใช่ประตู และ R1/S1 ที่ไม่ได้หมายถึงสมาชิกนั้น
    # ยอมให้ผ่านเมื่อมีหลักฐานทางมิติ/เหล็ก/โปรไฟล์เพียงพอ แต่ยังคงติดสถานะต้องตรวจเสมอ
    context_supported = mark_context_supported(kind, full_context)
    if not context_supported and kind in ("D", "R", "W"):
        has_structural_evidence = bool(DIM_RE.search(ctx) or REBAR_SPACING_RE.search(ctx) or STEEL_PROFILE_RE.search(ctx))
        if kind == "D" and not (DIM_RE.search(ctx) and re.search(r"door|window|ประตู|หน้าต่าง|บาน|วงกบ", full_context, re.I)):
            return None
        if kind == "R" and not (STEEL_PROFILE_RE.search(ctx) or re.search(r"roof|หลังคา|rafter|purlin|truss", full_context, re.I)):
            return None
        if kind == "W" and not (DIM_RE.search(ctx) and re.search(r"wall|ผนัง|partition", full_context, re.I)):
            return None
    dims=[]
    clean_ctx=STEEL_PROFILE_RE.sub(" ", ctx)
    for dm in DIM_RE.finditer(clean_ctx):
        vals=[dm.group(1), dm.group(2)] + ([dm.group(3)] if dm.group(3) else [])
        # ตรวจ unit ที่ต่อท้าย pair เช่น 200x400 mm
        unit_m=re.match(r"\s*(mm|cm|m|เมตร)\b", clean_ctx[dm.end():dm.end()+12], re.I)
        dims.append(dimension_values_to_m(vals,target,unit_m.group(1) if unit_m else None))
        if len(dims)>=2: break
    dim_values=dims[0] if dims else []
    length_m=0.0; lm=LENGTH_RE.search(ctx)
    if lm: length_m=token_to_m(lm.group(1),lm.group(2))
    height_m=0.0; hm=HEIGHT_RE.search(ctx)
    if hm: height_m=token_to_m(hm.group(1),hm.group(2))
    qty=1.0
    qm=QTY_RE.search(ctx)
    if qm: qty=max(1.0,_num_token(qm.group(1)))
    else:
        qsm=QTY_SUFFIX_RE.search(ctx)
        if qsm: qty=max(1.0,_num_token(qsm.group(1)))
    area=0.0; am=AREA_RE.search(ctx)
    if am: area=_num_token(am.group(1))
    rebar_rows=parse_rebar_specs(ctx)
    steel_profiles=list(dict.fromkeys(re.sub(r"\s+","",m.group(0)) for m in STEEL_PROFILE_RE.finditer(ctx)))
    w=l=h=0.0
    if target=="คาน":
        if len(dim_values)>=2: w,h=dim_values[0],dim_values[1]
        if len(dim_values)>=3 and length_m<=0: length_m=dim_values[2]
    else:
        if len(dim_values)>=2: w,l=dim_values[0],dim_values[1]
        if len(dim_values)>=3: h=dim_values[2]
    if target=="เสา" and height_m>0: h=height_m
    if target=="ผนัง" and length_m<=0 and len(dim_values)>=1: length_m=dim_values[0]
    if target=="ผนัง" and height_m<=0 and len(dim_values)>=2: height_m=dim_values[1]
    if target=="ประตู-หน้าต่าง":
        if len(dim_values)>=2: w,l=dim_values[0],dim_values[1]
    evidence=sum([bool(dim_values), bool(length_m or height_m or area), bool(rebar_rows), bool(steel_profiles)])
    score=0.50 + 0.12*evidence
    if qty!=1: score+=0.04
    # confidence จำกัดไว้ต่ำกว่า 0.90 และถือว่า "ต้องตรวจ" เสมอ
    score=min(0.88,score)
    src_ctx=(surrounding_context or ctx)[:900]
    return {
        "page":int(page_no),"mark":mark,"target":target,"confidence":round(score,2),"needs_review":True,
        "width_m":round(w,4),"length_m":round(l,4),"height_m":round(h,4),"member_length_m":round(length_m,4),
        "qty":round(qty,3),"area_m2":round(area,3),"rebar_rows":rebar_rows,"steel_profiles":steel_profiles,
        "source":drawing_source_tags(src_ctx,page_no,mark),"source_text":(src_ctx+"\n"+ctx[:620]).strip()[:1000],
        "evidence": {"ขนาด":bool(dim_values),"ความยาว/สูง":bool(length_m or height_m),"จำนวน":qty!=1,"เหล็ก":bool(rebar_rows),"โครงเหล็ก":bool(steel_profiles)},
        "level_hint":((m.group(0)) if (m:=re.search(r"ชั้น\s*([1-9][0-9]*)|floor\s*([1-9][0-9]*)",src_ctx+" "+ctx,re.I)) else "")
    }


def _block_nearby_texts(blocks, idx):
    x0,y0,x1,y1,*_=blocks[idx]
    cx=(x0+x1)/2; cy=(y0+y1)/2
    scored=[]
    for j,b in enumerate(blocks):
        if j==idx or len(b)<5: continue
        bx0,by0,bx1,by1,*_=b; bcx=(bx0+bx1)/2; bcy=(by0+by1)/2
        x_overlap=max(0,min(x1,bx1)-max(x0,bx0)); y_overlap=max(0,min(y1,by1)-max(y0,by0))
        dist=((bcx-cx)**2+(bcy-cy)**2)**0.5
        related=(x_overlap>0 and abs(bcy-cy)<180) or (y_overlap>0 and abs(bcx-cx)<180)
        if related and dist<320:
            scored.append((dist,str(b[4] or "")))
    scored.sort(key=lambda x:x[0])
    return str(blocks[idx][4] or ""), [t for _,t in scored[:3] if t]


def parse_drawing_candidates(page_text, page_no, blocks=None):
    text=normalize_drawing_text(page_text)
    candidates=[]
    if blocks:
        for i,b in enumerate(blocks):
            if len(b)<5 or not str(b[4]).strip(): continue
            block_text=normalize_drawing_text(str(b[4]))
            matches=list(STRUCTURAL_MARK_RE.finditer(block_text))+list(STRUCTURAL_DASH_MARK_RE.finditer(block_text))
            matches.sort(key=lambda m:m.start())
            if not matches: continue
            own_text, nearby=_block_nearby_texts(blocks,i)
            for mi,mm in enumerate(matches):
                # C-3 / B-2 ที่อยู่หลังคำว่า Grid เป็นพิกัด ไม่ใช่รหัสสมาชิก
                if ("-" in mm.group(0) or "." in mm.group(0)) and re.search(r"Grid\s*$", block_text[max(0,mm.start()-24):mm.start()], re.I):
                    continue
                next_pos=matches[mi+1].start() if mi+1<len(matches) else len(block_text)
                # กันข้อมูลของ C1 รายการถัดไปไหลย้อนเข้ารายการปัจจุบัน
                local_start=max(0,mm.start()-90)
                local_end=min(len(block_text),next_pos if mi+1<len(matches) else len(block_text))
                local_segment=block_text[local_start:local_end]
                ctx=local_segment
                # เพิ่มบล็อกข้างเคียงเฉพาะเมื่อยังขาดหลักฐาน
                if (len(parse_rebar_specs(ctx))==0 and not DIM_RE.search(ctx) and not LENGTH_RE.search(ctx) and not AREA_RE.search(ctx)):
                    safe_nearby=[t for t in nearby if not STRUCTURAL_MARK_RE.search(t) and not STRUCTURAL_DASH_MARK_RE.search(t) and (DIM_RE.search(t) or REBAR_SPACING_RE.search(t) or any(p.search(t) for p in REBAR_COUNT_PATTERNS))]
                    if safe_nearby:
                        ctx += "\n" + safe_nearby[0]
                local_match=re.search(re.escape(mm.group(0)),ctx)
                if not local_match: continue
                c=_candidate_from_context(local_match,ctx,page_no,ctx)
                if c:
                    c["location_key"]=f"{page_no}:{i}:{round(float(b[0]),1)}:{round(float(b[1]),1)}:{mi}"
                    candidates.append(c)
    # ใช้ parser ทั้งหน้าเฉพาะกับรหัสที่ยังไม่พบจาก block เพื่อหลีกเลี่ยง candidate ซ้ำและ context ปนกัน
    block_marks={c.get("mark") for c in candidates if c.get("location_key","").startswith(f"{page_no}:") and ":full:" not in c.get("location_key","")}
    if not blocks or not block_marks:
        matches=list(STRUCTURAL_MARK_RE.finditer(text))+list(STRUCTURAL_DASH_MARK_RE.finditer(text))
        matches.sort(key=lambda m:m.start())
        for i,mm in enumerate(matches):
            raw_mark=mm.group(1) if mm.lastindex else mm.group(0)
            norm_mark=re.sub(r"\s+","",raw_mark.upper())
            if ("-" in mm.group(0) or "." in mm.group(0)) and re.search(r"Grid\s*$", text[max(0,mm.start()-24):mm.start()], re.I):
                continue
            if norm_mark in block_marks:
                continue
            start=mm.start(); end=min(len(text),matches[i+1].start() if i+1<len(matches) else mm.end()+420)
            segment=text[start:end]
            local_match=re.search(re.escape(mm.group(0)), segment)
            if not local_match:
                continue
            source_context=text[max(0,mm.start()-140):end]
            c=_candidate_from_context(local_match,segment,page_no,source_context)
            if c:
                c["location_key"]=f"{page_no}:full:{i}"
                candidates.append(c)
    else:
        # ยังเติมเฉพาะรหัสที่ block parser หาไม่เจอ เช่น กรณี text order ผิดปกติ
        matches=list(STRUCTURAL_MARK_RE.finditer(text))+list(STRUCTURAL_DASH_MARK_RE.finditer(text))
        matches.sort(key=lambda m:m.start())
        for i,mm in enumerate(matches):
            raw_mark=mm.group(1) if mm.lastindex else mm.group(0)
            norm_mark=re.sub(r"\s+","",raw_mark.upper())
            if ("-" in mm.group(0) or "." in mm.group(0)) and re.search(r"Grid\s*$", text[max(0,mm.start()-24):mm.start()], re.I):
                continue
            if norm_mark in block_marks:
                continue
            start=mm.start(); end=min(len(text),matches[i+1].start() if i+1<len(matches) else mm.end()+420)
            segment=text[start:end]
            local_match=re.search(re.escape(mm.group(0)), segment)
            if not local_match:
                continue
            source_context=text[max(0,mm.start()-140):end]
            c=_candidate_from_context(local_match,segment,page_no,source_context)
            if c:
                c["location_key"]=f"{page_no}:full:{i}"
                candidates.append(c)
    best={}
    for c in candidates:
        key=(c["page"],c["mark"],c.get("location_key",""))
        old=best.get(key)
        score=lambda x:(sum(bool(v) for v in x.get("evidence",{}).values()), x.get("confidence",0), len(x.get("rebar_rows",[])), len(x.get("steel_profiles",[])))
        if old is None or score(c)>score(old):
            best[key]=c
    return list(best.values())

def drawing_text_quality_score(text):
    t=normalize_drawing_text(text)
    compact=re.sub(r"\s+","",t)
    marks=len(list(STRUCTURAL_MARK_RE.finditer(t)))+len(list(STRUCTURAL_DASH_MARK_RE.finditer(t)))
    rebars=len(parse_rebar_specs(t))
    dims=len(list(DIM_RE.finditer(t)))
    return marks*5 + rebars*4 + dims*2 + min(len(compact),3000)/10000.0


def read_drawing_pdf(file_bytes, use_ocr=True, max_pages=80, dpi=160):
    if fitz is None:
        raise RuntimeError("ยังไม่มี PyMuPDF (fitz) สำหรับอ่าน PDF")
    doc=fitz.open(stream=file_bytes,filetype="pdf")
    page_texts=[]; page_blocks=[]; ocr_pages=[]; ocr_languages=[]
    for idx in range(min(len(doc),int(max_pages))):
        page=doc.load_page(idx)
        native_text=normalize_drawing_text(page.get_text("text",sort=True) or "")
        try:
            blocks=page.get_text("blocks",sort=True)
        except Exception:
            blocks=[]
        text=native_text
        should_ocr=use_ocr and pytesseract is not None and Image is not None and (
            len(re.sub(r"\s+","",native_text))<40 or drawing_text_quality_score(native_text)<8
        )
        if should_ocr:
            try:
                scale=max(1.0,dpi/72.0); pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
                img=Image.frombytes("RGB",[pix.width,pix.height],pix.samples)
                try: langs=pytesseract.get_languages(config="")
                except Exception: langs=[]
                lang="tha+eng" if "tha" in langs and "eng" in langs else ("tha" if "tha" in langs else "eng")
                ocr_languages.append(lang)
                ocr_text=normalize_drawing_text(pytesseract.image_to_string(img,lang=lang,config="--psm 11"))
                if drawing_text_quality_score(ocr_text)<drawing_text_quality_score(native_text):
                    retry=normalize_drawing_text(pytesseract.image_to_string(img,lang=lang,config="--psm 6"))
                    if drawing_text_quality_score(retry)>drawing_text_quality_score(ocr_text): ocr_text=retry
                if drawing_text_quality_score(ocr_text)>drawing_text_quality_score(native_text): text=ocr_text
                elif native_text.strip(): text=native_text
                else: text=ocr_text
                ocr_pages.append(idx+1)
            except Exception:
                pass
        page_texts.append(text);page_blocks.append(blocks)
    candidates=[]
    for page_no,(text,blocks) in enumerate(zip(page_texts,page_blocks),start=1):
        if not text.strip():
            continue
        # ใช้ทั้ง spatial blocks และข้อความทั้งหน้า แล้วคัดตัวที่มีหลักฐานมากกว่า
        if blocks:
            candidates.extend(parse_drawing_candidates(text,page_no,blocks=blocks))
        else:
            candidates.extend(parse_drawing_candidates(text,page_no,blocks=None))
    page_count=len(doc);doc.close()
    return {"page_count":page_count,"pages_read":len(page_texts),"ocr_pages":ocr_pages,"ocr_languages":list(dict.fromkeys(ocr_languages)),"ocr_available":pytesseract is not None and Image is not None,"candidates":candidates,"reader_mode":"ข้อความใน PDF + OCR เฉพาะหน้าที่จำเป็น"}

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
    sheets = _sheet_tags_from_context(context, page_no)
    grids = list(dict.fromkeys(m.group(0) for m in GRID_RE.finditer(normalize_drawing_text(context))))
    parts = [f"หน้า {page_no}"]
    if sheets:
        parts.append(f"แผ่นแบบ {sheets[-1]}")
    if grids:
        grid_text = re.sub(r"^Grid\s*", "", grids[-1], flags=re.I)
        parts.append(f"ตำแหน่ง {grid_text}")
    parts.append(f"รหัส {mark}")
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
            mapping = {"f_name": cand.get("mark", "F1"), "f_qty": int(round(cand.get("qty", 1) or 1)), "f_ref": cand.get("source", "")}
            if safe_num(cand.get("width_m")) > 0: mapping["f_w"] = cand.get("width_m")
            if safe_num(cand.get("length_m")) > 0: mapping["f_l"] = cand.get("length_m")
            if safe_num(cand.get("height_m")) > 0: mapping["f_h"] = cand.get("height_m")
            if cand.get("rebar_rows"):
                rows = copy.deepcopy(cand["rebar_rows"])
                for r in rows:
                    r["len"] = cand.get("length_m") or cand.get("width_m") or 0.0
                st.session_state["footing_rebars"] = rows
        elif target == "เสา":
            mapping = {"col_name": cand.get("mark", "C1"), "col_qty": int(round(cand.get("qty", 1) or 1)), "col_ref": cand.get("source", "")}
            if safe_num(cand.get("width_m")) > 0: mapping["col_w"] = cand.get("width_m")
            if safe_num(cand.get("length_m")) > 0: mapping["col_l"] = cand.get("length_m")
            col_height = cand.get("height_m") or cand.get("member_length_m")
            if safe_num(col_height) > 0: mapping["col_h"] = col_height
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
            mapping = {"b_name": cand.get("mark", "B1"), "b_qty": int(round(cand.get("qty", 1) or 1)), "b_ref": cand.get("source", "")}
            if safe_num(cand.get("width_m")) > 0: mapping["b_w"] = cand.get("width_m")
            if safe_num(cand.get("height_m")) > 0: mapping["b_h"] = cand.get("height_m")
            if safe_num(cand.get("member_length_m")) > 0: mapping["b_l"] = cand.get("member_length_m")
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
            mapping = {"s_name": cand.get("mark", "S1"), "s_qty": int(round(cand.get("qty", 1) or 1)), "s_ref": cand.get("source", "")}
            if safe_num(cand.get("width_m")) > 0: mapping["s_w"] = cand.get("width_m")
            if safe_num(cand.get("length_m")) > 0: mapping["s_l"] = cand.get("length_m")
            if safe_num(cand.get("height_m")) > 0: mapping["s_h"] = cand.get("height_m")
            if cand.get("rebar_rows"):
                rows = copy.deepcopy(cand["rebar_rows"])
                for r in rows:
                    r["len"] = mapping["s_l"] if "ทางยาว" in r.get("pos", "") else mapping["s_w"]
                st.session_state["slab_rebars"] = rows
        elif target == "ผนัง":
            mapping = {"wall_name": cand.get("mark", "W1"), "wall_qty": int(round(cand.get("qty", 1) or 1)), "wall_ref": cand.get("source", "")}
            wall_length = cand.get("member_length_m") or cand.get("width_m")
            wall_height = cand.get("height_m") or cand.get("length_m")
            if safe_num(wall_length) > 0: mapping["wall_l"] = wall_length
            if safe_num(wall_height) > 0: mapping["wall_h"] = wall_height
        elif target == "ประตู-หน้าต่าง":
            mapping = {"dw_name": cand.get("mark", "D1"), "dw_qty": int(round(cand.get("qty", 1) or 1)), "dw_ref": cand.get("source", "")}
            if safe_num(cand.get("width_m")) > 0: mapping["dw_w"] = cand.get("width_m")
            dw_height = cand.get("length_m") or cand.get("height_m")
            if safe_num(dw_height) > 0: mapping["dw_h"] = dw_height
        elif target == "หลังคา":
            mapping = {"roof_name": cand.get("mark", "R1"), "roof_ref": cand.get("source", "")}
            if safe_num(cand.get("area_m2")) > 0:
                mapping["roof_manual_area"] = cand.get("area_m2")
                mapping["roof_area_mode"] = "ระบุพื้นที่มุงจริงจากแบบ"
            if cand.get("steel_profiles"):
                detected_len = cand.get("member_length_m") or cand.get("length_m") or 0.0
                detected_qty = cand.get("qty", 0) if cand.get("evidence", {}).get("จำนวน") else 0
                st.session_state["roof_members"] = [{"member":p,"desc":"อ่านชนิด/ขนาดจากแบบ — ตรวจตำแหน่ง ความยาว และจำนวนก่อนใช้","len":detected_len,"qty":detected_qty,"kg_m":COMMON_STEEL_KG_M.get(p,0.0)} for p in cand.get("steel_profiles", [])]
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

def roof_labor_rate_w480(roof_material, roof_shape, fallback, use_w480=True):
    """เลือกค่าแรงมุงหลังคาตามประเภท/ทรงเมื่อมีอัตราแยกใน W480; นอกเหนือจากนั้นใช้ค่าที่แก้ได้ในตาราง"""
    mat = str(roof_material or "")
    shape = str(roof_shape or "")
    if not use_w480:
        return safe_num(fallback)
    if "กระเบื้องลอนคู่" in mat:
        if "ปั้นหยา" in shape:
            return 51.0
        if "ทรงไทย" in shape:
            return 56.0
        return 46.0
    if "กระเบื้องดินเผา" in mat:
        return 82.0 if "ปั้นหยา" in shape else 91.0
    if "เมทัลชีท" in mat or "Metal Sheet" in mat:
        return 72.0
    return safe_num(fallback)

# รายการวัสดุมุงหลังคา (ราคาวัสดุ/ตร.ม. ประเมิน, ค่าแรง/ตร.ม. ประเมิน, น้ำหนักโครงเหล็ก กก./ตร.ม.)
ROOF_MATERIAL_SPECS = {
    "เมทัลชีท หนา 0.35 - 0.47 mm (พร้อมบุ PE / PU Foam)": {"mat": 300.0, "lab": 46.0, "steel_factor": 18.0},
    "กระเบื้องลอนคู่ (ซีเมนต์ใยหิน / ไร้ใยหิน)": {"mat": 130.0, "lab": 46.0, "steel_factor": 20.0},
    "กระเบื้องคอนกรีตซีแพคโมเนีย (CPAC Monier)": {"mat": 350.0, "lab": 76.0, "steel_factor": 28.0},
    "กระเบื้องแผ่นเรียบเพรสทีจ (Prestige / Neoclassic)": {"mat": 470.0, "lab": 82.0, "steel_factor": 28.0},
    "กระเบื้องดินเผา / กระเบื้องสุโขทัย": {"mat": 600.0, "lab": 91.0, "steel_factor": 25.0},
    "กระเบื้องเซรามิก (Excella)": {"mat": 780.0, "lab": 82.0, "steel_factor": 28.0},
    "แผ่นหลังคาไวนิล (UPVC / Plastwood)": {"mat": 700.0, "lab": 100.0, "steel_factor": 18.0},
    "แผ่นโพลีคาร์บอเนต / ตราเพชร / แผ่นโปร่งแสง": {"mat": 450.0, "lab": 80.0, "steel_factor": 16.0},
    "หลังคาชิงเกิ้ลรูฟ (Shingle Roof / Asphalt Shingle)": {"mat": 600.0, "lab": 100.0, "steel_factor": 22.0},
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

# ราคาอ้างอิงประตู/หน้าต่าง: รายการไม้มีฐานจากข้อมูลพาณิชย์จังหวัด ส.ค. 2569
# รายการอลูมิเนียม/Frameless เป็น benchmark เพื่อประมาณการและต้องตรวจใบเสนอราคาจริง
DOOR_WINDOW_REFERENCE_PRICES = {
    "ประตูไม้เนื้อแข็ง / กระจก พร้อมวงกบ & ฟิตติ้ง": (1550.0, 340.0, "อ้างอิงวัสดุประตู+วงกบไม้จาก MOC 2569; ฟิตติ้งเป็นค่าประมาณ"),
    "ประตูไม้สังเคราะห์ (UPVC / PVC)": (3500.0, 350.0, "ราคาอ้างอิงตลาด 2569 — ควรตรวจยี่ห้อ/ขนาดจริง"),
    "ประตูบานสไลด์อลูมิเนียม (อบขาว / ดำ / ชา / ลายไม้) พร้อมกระจก": (4800.0, 450.0, "ราคาอ้างอิงตลาด 2569 — ขึ้นกับขนาด/กระจก"),
    "ประตูบานผลัก/บานสวิง อลูมิเนียม": (3600.0, 400.0, "ราคาอ้างอิงตลาด 2569 — ขึ้นกับขนาด/กระจก"),
    "ประตูบานม้วนเหล็ก / ประตูบานพับอลูมิเนียมลายไม้": (6500.0, 500.0, "ราคาอ้างอิงตลาด 2569 — ขึ้นกับระบบและขนาด"),
    "หน้าต่างบานเลื่อนอลูมิเนียม พร้อมกระจก": (3200.0, 350.0, "ราคาอ้างอิงตลาด 2569 — ขึ้นกับขนาด/กระจก"),
    "หน้าต่างบานกระทุ้ง / บานเปิดอลูมิเนียม": (3500.0, 380.0, "ราคาอ้างอิงตลาด 2569 — ขึ้นกับขนาด/กระจก"),
    "หน้าต่างบานเกล็ด (พร้อมเกล็ดกระจก/อลูมิเนียม)": (2600.0, 320.0, "ราคาอ้างอิงตลาด 2569 — ขึ้นกับชนิดเกล็ด"),
    "หน้าต่างกระจกติดตาย (Fixed Window)": (3000.0, 300.0, "ราคาอ้างอิงตลาด 2569 — ขึ้นกับขนาด/กระจก"),
    "ประตู-หน้าต่าง กระจกบานเปลือย (Frameless Glass)": (6500.0, 500.0, "ราคาอ้างอิงตลาด 2569 — ขึ้นกับความหนากระจกและฮาร์ดแวร์"),
}

# ---------------------------------------------------------
# 3. Persistent Data Storage & Session State Management
# ---------------------------------------------------------
DEFAULT_FOOTING_REBARS = []
DEFAULT_COLUMN_REBARS = []
DEFAULT_BEAM_REBARS = []
DEFAULT_SLAB_REBARS = []

def reset_draft_for_project(project_id):
    """แยกร่างตามโครงการ และเริ่มต้นแบบว่าง เพื่อไม่ให้โปรแกรมเดาเหล็กจากแบบที่ยังไม่ได้อ่าน"""
    if "_draft_project_id" not in st.session_state:
        st.session_state["_draft_project_id"] = None
    if project_id != st.session_state.get("_draft_project_id"):
        st.session_state["footing_rebars"] = []
        st.session_state["column_rebars"] = []
        st.session_state["beam_rebars"] = []
        st.session_state["slab_rebars"] = []
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
                if isinstance(q.get("วิธีเผื่อปลาย/ต่อเหล็ก"), str):
                    q["วิธีเผื่อปลาย/ต่อเหล็ก"] = normalize_lap_mode(q.get("วิธีเผื่อปลาย/ต่อเหล็ก"))
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
    st.session_state["footing_rebars"] = copy.deepcopy(DEFAULT_FOOTING_REBARS)

if "column_rebars" not in st.session_state:
    st.session_state["column_rebars"] = copy.deepcopy(DEFAULT_COLUMN_REBARS)

if "beam_rebars" not in st.session_state:
    st.session_state["beam_rebars"] = copy.deepcopy(DEFAULT_BEAM_REBARS)

if "slab_rebars" not in st.session_state:
    st.session_state["slab_rebars"] = copy.deepcopy(DEFAULT_SLAB_REBARS)

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
if "pending_duplicate_item" not in st.session_state:
    st.session_state["pending_duplicate_item"] = None
if "pending_duplicate_reason" not in st.session_state:
    st.session_state["pending_duplicate_reason"] = ""
if "pending_duplicate_project_id" not in st.session_state:
    st.session_state["pending_duplicate_project_id"] = None

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
            st.session_state["pending_duplicate_item"] = copy.deepcopy(item)
            st.session_state["pending_duplicate_project_id"] = st.session_state["projects"][p_idx].get("id")
            if get_item_source(item):
                st.session_state["pending_duplicate_reason"] = (
                    f"พบรายการซ้ำ: {item.get('รายการ', 'ไม่ระบุ')} | อ้างอิงจากแบบ {get_item_source(item)}"
                )
            else:
                st.session_state["pending_duplicate_reason"] = (
                    f"พบรายการชื่อและรายละเอียดเหมือนกัน: {item.get('รายการ', 'ไม่ระบุ')} แต่ยังไม่ได้ใส่อ้างอิงจากแบบ"
                )
            st.warning("⚠️ พบรายการซ้ำ ระบบยังไม่บันทึก — ไปที่แถบแจ้งเตือนด้านบนเพื่อยืนยันหากเป็นคนละตำแหน่ง")
            return False

    near_hits = find_near_duplicate_items(item)
    if near_hits and not allow_duplicate and not find_duplicate_item(item):
        st.session_state["pending_duplicate_item"] = copy.deepcopy(item)
        st.session_state["pending_duplicate_project_id"] = st.session_state["projects"][p_idx].get("id")
        st.session_state["pending_duplicate_reason"] = (
            f"พบรายการหมวด + ชื่อรายการ + อ้างอิงจากแบบเหมือนกันอยู่แล้ว: {item.get('รายการ', 'ไม่ระบุ')}"
        )
        st.warning("⚠️ พบรายการที่อาจซ้ำ ระบบยังไม่บันทึก — ตรวจรายการก่อนยืนยัน")
        return False

    if "items" not in st.session_state["projects"][p_idx]:
        st.session_state["projects"][p_idx]["items"] = []
    st.session_state["projects"][p_idx]["items"].append(item)
    save_projects()
    st.success(f"บันทึกรายการ '{item.get('รายการ', 'ไม่ระบุ')}' เรียบร้อยแล้ว!")
    return True


def render_pending_duplicate_confirmation():
    pending = st.session_state.get("pending_duplicate_item")
    if not pending:
        return
    if st.session_state.get("pending_duplicate_project_id") != st.session_state.get("current_project_id"):
        st.session_state["pending_duplicate_item"] = None
        st.session_state["pending_duplicate_reason"] = ""
        st.session_state["pending_duplicate_project_id"] = None
        return
    st.warning("⚠️ **ตรวจรายการซ้ำก่อนบันทึก** — " + st.session_state.get("pending_duplicate_reason", "พบรายการที่อาจซ้ำ"))
    st.caption("ตรวจว่าเป็นคนละตำแหน่ง/คนละช่วงงานจริงก่อนยืนยัน เพราะการยืนยันจะบันทึกรายการซ้ำได้")
    c1, c2 = st.columns(2)
    if c1.button("✅ ยืนยันว่าเป็นรายการคนละตำแหน่งและบันทึก", key="confirm_pending_duplicate", type="primary"):
        item = copy.deepcopy(pending)
        st.session_state["pending_duplicate_item"] = None
        st.session_state["pending_duplicate_reason"] = ""
        st.session_state["pending_duplicate_project_id"] = None
        add_takeoff_item(item, allow_duplicate=True)
        st.rerun()
    if c2.button("ยกเลิก", key="cancel_pending_duplicate"):
        st.session_state["pending_duplicate_item"] = None
        st.session_state["pending_duplicate_reason"] = ""
        st.session_state["pending_duplicate_project_id"] = None
        st.rerun()

def rebar_save_guard(rows, key, label):
    """กันการเผลอบันทึกงาน คสล. โดยไม่มีข้อมูลเหล็กจากแบบ; ผู้ใช้ยืนยันได้เมื่อต้องการบันทึกแบบชั่วคราว"""
    if rows:
        return True
    st.warning(f"⚠️ {label}: ยังไม่มีเหล็กจากแบบ — ระบบไม่เดาเหล็กให้")
    return st.checkbox("ยืนยันว่าตอนนี้ยังไม่ระบุเหล็กจากแบบ", value=False, key=key,
                       help="ใช้เมื่อต้องการบันทึกปริมาณคอนกรีต/ไม้แบบก่อน แล้วค่อยกลับมาเติมเหล็กภายหลัง")

# ราคาตั้งต้นตามขนาดเหล็กเส้น: ค่าเฉลี่ยประเทศจาก MOC ส.ค. 2569 เมื่อมีข้อมูลตรง; ขนาดอื่นเป็นราคาอ้างอิงตลาด/ประมาณการ
CURRENT_REBAR_PRICES.update({
    "RB6":22.36, "RB9":21.32, "DB10":21.00, "DB12":20.65, "DB16":20.54,
    "DB20":20.77, "DB25":21.00, "DB28":21.00, "DB32":21.00})

# ราคาเริ่มต้นสำหรับตารางประเภทงานสถาปัตย์ (แก้ได้ในตารางด้านล่าง)
p_brick_red = 180.0
p_brick_red_full = 360.0
p_brick_light = 350.0
p_brick_light_10 = 409.0
p_brick_block = 100.0
p_brick_block_10 = 120.0
p_plaster_mat = 120.0
p_paint_mat = 65.0
labour_masonry = 104.0
labour_plastering = 96.0
labour_painting = 31.0
p_tile_mat = 350.0
labour_tile = 188.0
p_ceiling_mat = 326.0
labour_ceiling = 110.0

# ---------------------------------------------------------
# 4. Sidebar Price & Material Settings
# ---------------------------------------------------------
with st.sidebar:
    st.title("⚙ ตั้งค่าราคาและค่าแรง")
    st.info("**ชุดราคาอ้างอิงในระบบ**: ราคาวัสดุอ้างอิงข้อมูลกระทรวงพาณิชย์ที่บันทึกล่าสุดในระบบ (ส.ค. 2569) และค่าแรงจากบัญชี ว480 ลงวันที่ 26 มิ.ย. 2569 — ราคาแต่ละจังหวัด/ผู้ขายอาจต่างกัน และแก้ราคาเองได้ทุกช่อง")
    st.caption("📅 วันที่ราคาอ้างอิงที่บันทึกในระบบ: ส.ค. 2569 | **แก้ราคาเองได้ทุกช่อง**")
    st.caption("💡 **ใช้งานง่าย:** ถ้าไม่แน่ใจ ให้ใช้ค่าเริ่มต้นได้เลย แล้วแก้เฉพาะ ราคา ตามใบเสนอราคาจริงของคุณ — ราคาด้านล่างเป็นราคาอ้างอิงและยังแก้เองได้ทุกช่อง")
    
    with st.expander("💼 ค่าดำเนินการ กำไร & ภาษี", expanded=True):
        profit_percent = st.number_input("ค่าดำเนินการ & กำไร (%)", min_value=0.0, max_value=100.0, value=10.0, step=1.0) / 100.0
        use_vat = st.checkbox("คิดภาษีมูลค่าเพิ่ม (VAT 7%)", value=True)

    with st.expander("🧱 ราคางานสถาปัตย์", expanded=False):
        st.info("ราคาแต่ละชนิดของผนัง พื้น ฝ้า หลังคา และประตู-หน้าต่าง แก้ได้ในตาราง ‘ราคาประเภทงานเพิ่มเติม’ ด้านล่าง เพื่อไม่ให้มีช่องราคาซ้ำกันหลายจุด")

    with st.expander("🚜 ค่าแรงงานดินขุด-ดินถม", expanded=False):
        excavation_labor_mode = st.selectbox("วิธีคิดค่าแรงงานดิน", ["ขุดหลุม + ถมคืน (คิดรวมตาม ว480)", "ขุดและถมแยกกัน"], index=0, key="excavation_labor_mode", help="ว480 หมวดขุดหลุมฐานรากและถมคืนเป็นงานรวม หากเลือกโหมดนี้จะไม่บวกค่าแรงถมคืนซ้ำ")
        auto_excavation_labor = st.checkbox("ใช้ค่าแรงขุดอัตโนมัติตามปริมาณ/ความลึก (ว480)", value=True, help="ระบบจะเลือก 121 / 153 / 181 บาท/ลบ.ม. ตามเงื่อนไขของ ว480")
        cost_excavation = st.number_input("ค่าแรงขุดดินที่กรอกเอง (บาท/ลบ.ม.)", min_value=0.0, value=153.0, step=10.0)
        cost_backfill = st.number_input("ค่าแรงถมดินย้อนกลับ (บาท/ลบ.ม.) เมื่อแยกงาน", min_value=0.0, value=121.0, step=10.0)

    with st.expander("🏗️ ราคาวัสดุโครงสร้าง", expanded=False):
        st.caption("คอนกรีต/เหล็กเส้นด้านล่างมีฐานราคา MOC ส.ค. 2569; ไม้แบบ ครอบสัน และเหล็กรูปพรรณทั่วไปเป็นค่าอ้างอิงตลาด/ประมาณการ และแก้เองได้")
        p_concrete = st.number_input("คอนกรีต 240 cube / 180 cylinder (บาท/ลบ.ม.)", min_value=0.0, value=2466.59, step=10.0)
        p_formwork = st.number_input("วัสดุไม้แบบ (บาท/ตร.ม.)", min_value=0.0, value=350.0, step=10.0)
        p_roof_cap = st.number_input("วัสดุครอบสัน/ตะเข้สัน (บาท/เมตร)", min_value=0.0, value=180.0, step=10.0)
        labour_roof_cap = st.number_input("ค่าแรงติดตั้งครอบสัน/ตะเข้สัน (บาท/เมตร)", min_value=0.0, value=60.0, step=5.0)
        p_roof_steel = st.number_input("ราคาเหล็กรูปพรรณหลังคา (บาท/กก.)", min_value=0.0, value=24.4, step=0.1)
        labour_roof_steel = st.number_input("ค่าแรงประกอบโครงเหล็กหลังคา (บาท/กก.)", min_value=0.0, value=12.0, step=0.5)
        use_w480_roof_labor = st.checkbox("ค่าแรงมุงหลังคา: ใช้ ว480 ตามทรง (แนะนำ)", value=True, help="ถ้าปิด ระบบจะใช้ค่าแรงที่แก้ในตารางราคางานหลังคา")
        st.markdown("**ราคาเหล็กเส้นแยกตามขนาด**")
        _rc = st.columns(3)
        for i, _rtype in enumerate(REBAR_LIST):
            _default = float(CURRENT_REBAR_PRICES.get(_rtype, 21.0))
            CURRENT_REBAR_PRICES[_rtype] = _rc[i % 3].number_input(
                f"{_rtype} (บาท/กก.)", min_value=0.0, value=_default, step=0.1, key=f"ref_rebar_price_{_rtype}"
            )
        p_db12 = safe_num(CURRENT_REBAR_PRICES.get("DB12", 20.65))
        p_rb9 = safe_num(CURRENT_REBAR_PRICES.get("RB9", 21.32))
        st.caption("RB6/RB9/DB12/DB16/DB20 ใช้ค่าอ้างอิงที่บันทึกจาก MOC ส.ค. 2569; ขนาดอื่นเป็นราคาอ้างอิงตลาด/ประมาณการ — แก้เองได้")

    with st.expander("📌 ราคาและค่าแรงเสาเข็ม", expanded=False):
        p_pile_hex = st.number_input("เข็มหกเหลี่ยมกลวง (บาท/ม.)", min_value=0.0, value=120.0, step=10.0)
        p_pile_i18 = st.number_input("เข็ม I-18 (บาท/ม.)", min_value=0.0, value=220.0, step=10.0)
        p_pile_i22 = st.number_input("เข็ม I-22 (บาท/ม.)", min_value=0.0, value=280.0, step=10.0)
        p_pile_i26 = st.number_input("เข็ม I-26 (บาท/ม.)", min_value=0.0, value=350.0, step=10.0)
        p_pile_bored35 = st.number_input("เข็มเจาะ Ø0.35 ม. (บาท/ม.)", min_value=0.0, value=650.0, step=20.0)
        labour_pile_press = st.number_input("ค่าแรงกด/ตอกเข็ม (บาท/ม.)", min_value=0.0, value=80.0, step=5.0)
        labour_pile_bored = st.number_input("ค่าแรงเจาะเสาเข็ม (บาท/ม.)", min_value=0.0, value=250.0, step=10.0)

    with st.expander("🔨 ค่าแรงงานโครงสร้างทั่วไป", expanded=False):
        concrete_labor_basis = st.selectbox("ค่าแรงเทคอนกรีต", ["อาคารชั้นเดียว (ว480)", "อาคารหลายชั้น (ว480)", "กำหนดราคาเอง"], index=0, key="concrete_labor_basis", help="ว480: 421 บาท/ลบ.ม. สำหรับอาคารชั้นเดียว และ 522 บาท/ลบ.ม. สำหรับอาคารหลายชั้น")
        labour_concrete_manual = st.number_input("ราคาเทคอนกรีตที่กำหนดเอง (บาท/ลบ.ม.)", min_value=0.0, value=421.0, step=10.0, key="labour_concrete_manual")
        labour_concrete = 421.0 if concrete_labor_basis.startswith("อาคารชั้นเดียว") else (522.0 if concrete_labor_basis.startswith("อาคารหลายชั้น") else labour_concrete_manual)
        st.caption(f"อัตราที่ระบบใช้ตอนนี้: {labour_concrete:,.0f} บาท/ลบ.ม.")
        use_w480_rebar_labor = st.checkbox("ค่าแรงผูกเหล็ก: ใช้ ว480 แยกตามขนาด (แนะนำ)", value=True, help="ถ้าปิด ระบบจะใช้อัตราที่คุณกรอกด้านล่างกับเหล็กทุกขนาด")
        labour_rebar = st.number_input("ค่าแรงผูกเหล็กเส้น (ใช้เมื่อไม่ใช้ ว480) (บาท/กก.)", min_value=0.0, value=3.9, step=0.5)
        labour_formwork = st.number_input("ค่าแรงประกอบไม้แบบ (บาท/ตร.ม.)", min_value=0.0, value=163.0, step=10.0)

    with st.expander("📉 เผื่อจัดซื้อ (Waste)", expanded=False):
        waste_concrete = st.number_input("เผื่อคอนกรีต (%)", min_value=0.0, max_value=100.0, value=5.0) / 100.0
        waste_rebar = st.number_input("เผื่อเหล็กเส้น/โครงสร้างสำหรับจัดซื้อ (%)", min_value=0.0, max_value=100.0, value=5.0) / 100.0
        waste_formwork = st.number_input("เผื่อไม้แบบ (%)", min_value=0.0, max_value=100.0, value=5.0) / 100.0
        waste_wall = st.number_input("เผื่ออิฐ/ปูนฉาบ (%)", min_value=0.0, max_value=100.0, value=5.0) / 100.0
        waste_roof = st.number_input("เผื่อหลังคา (%)", min_value=0.0, max_value=100.0, value=7.0) / 100.0
        waste_finishing = st.number_input("เผื่อกระเบื้อง/ฝ้า (%)", min_value=0.0, max_value=100.0, value=5.0) / 100.0


    with st.expander("📖 คำศัพท์แบบเข้าใจง่าย", expanded=False):
        st.markdown("**ระยะหุ้มคอนกรีต (Cover)** = ระยะจากผิวคอนกรีตถึงผิวเหล็ก\n\n**เหล็กทาบ** = ระยะที่เหล็กซ้อนกันตอนต่อเหล็ก\n\n**ระยะฝาก/ฝังปลายเหล็ก** = ระยะที่ต้องฝังเหล็กเข้าเนื้อคอนกรีต\n\n**@** = ระยะห่างระหว่างเหล็ก เช่น DB12 @ 200 มม. (เท่ากับ 0.20 ม.)\n\n**อ้างอิงจากแบบ** = บอกว่าข้อมูลมาจากแผ่น/ตำแหน่งไหน เช่น S-03 / Grid B-2 / C1 — ไม่ใช้คำนวณ และตัวอ่าน PDF จะใส่ให้เอง\n\n**ชนิดเหล็ก/ขนาด** = เช่น C-125x50x20x3.2 หรือ H-150x150x7x10\n\n**น้ำหนักต่อเมตร** = น้ำหนักเหล็ก 1 เมตร เช่น 6.00 กก./ม.")

    with st.expander("🎯 ตั้งค่าขั้นสูงสำหรับคนทำ BOQ (ไม่ต้องแก้ก็ได้)", expanded=False):
        st.caption("ค่า Cover (ระยะหุ้มคอนกรีต)/การต่อ/เผื่อปลายเหล็กเป็นค่าเริ่มต้นสำหรับช่วยถอดแบบเท่านั้น — แบบโครงสร้าง, Detail และ Spec ของโครงการมีผลเหนือค่าเหล่านี้เสมอ")
        st.caption("⚠️ ระยะทาบ/ฝาก/ฝังและความยาวตัดเหล็กปลอกเป็นสูตรช่วยประมาณ หากแบบกำหนดค่าหรือรายละเอียดเฉพาะ ให้ยึดแบบเป็นหลัก")
        cover_footing_mm = st.number_input("Cover (ระยะหุ้มคอนกรีต) ฐานราก (มม.)", min_value=0.0, max_value=150.0, value=50.0, step=5.0)
        cover_column_mm = st.number_input("Cover (ระยะหุ้มคอนกรีต) เสา (มม.)", min_value=0.0, max_value=100.0, value=40.0, step=5.0, help="ใช้เป็นค่าประมาณเมื่อแบบไม่ได้ระบุ; ถ้าแบบมีรายละเอียด ให้ยึดแบบก่อน")
        cover_beam_mm = st.number_input("Cover (ระยะหุ้มคอนกรีต) คาน (มม.)", min_value=0.0, max_value=100.0, value=25.0, step=5.0)
        cover_slab_mm = st.number_input("Cover (ระยะหุ้มคอนกรีต) พื้น/บันได (มม.)", min_value=0.0, max_value=100.0, value=20.0, step=5.0)
        lap_factor_d = st.number_input("ระยะทาบตั้งต้น (×d)", min_value=0.0, max_value=100.0, value=40.0, step=5.0)
        dev_factor_d = st.number_input("ระยะฝาก/ฝังปลายเหล็กตั้งต้น (×d)", min_value=0.0, max_value=100.0, value=40.0, step=5.0)
        stirrup_hook_extra_m = st.number_input("เผื่อความยาวตะขอเหล็กปลอก (ม./วง)", min_value=0.0, max_value=1.0, value=0.10, step=0.01)
        waste_roof_steel = st.number_input("เผื่อโครงเหล็กหลังคา (%)", min_value=0.0, max_value=100.0, value=5.0) / 100.0
        prevent_duplicates = st.checkbox("ป้องกันรายการ BOQ ซ้ำ", value=True, help="ระบบกันรายการซ้ำตรงกัน และเตือนรายการที่ใช้หมวด+ชื่อ+ที่มาในแบบเดียวกัน")

render_pending_duplicate_confirmation()

# ---------------------------------------------------------
# ราคาประเภทงานเพิ่มเติม: แก้ได้ทุกตัว (reference / benchmark)
# ---------------------------------------------------------
def _editable_price_table(title, records, key, include_factor=False):
    st.markdown(f"**{title}**")
    df = pd.DataFrame(records)
    if df.empty:
        return df
    number_cols = [c for c in ["วัสดุ (บาท/หน่วย)", "ค่าแรง (บาท/หน่วย)", "น้ำหนักโครง (กก./ตร.ม.)"] if c in df.columns]
    cfg = {}
    for c in number_cols:
        cfg[c] = st.column_config.NumberColumn(c, min_value=0.0, step=0.1, format="%.2f")
    edited = st.data_editor(
        df,
        key=key,
        hide_index=True,
        use_container_width=True,
        disabled=[c for c in ["รายการ", "หน่วย", "หมายเหตุ"] if c in df.columns],
        column_config=cfg,
    )
    return edited

with st.sidebar:
    with st.expander("💰 ตารางราคางานสถาปัตย์ (แก้ได้ทุกตัว)", expanded=False):
        st.caption("ราคาอ้างอิงวัสดุ: ข้อมูลที่ตรวจสอบได้ล่าสุดจากกระทรวงพาณิชย์ ส.ค. 2569; ค่าแรงบางหมวดอ้าง ว480. ถ้าปิดโหมด ว480 ให้ใช้ค่าที่แก้ในตารางได้เต็มที่")
        wall_records = [
            {"รายการ":"อิฐมอญครึ่งแผ่น", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":p_brick_red, "ค่าแรง (บาท/หน่วย)":104.0, "หมายเหตุ":"วัสดุเป็นราคาอ้างอิง/แปลงจากวัสดุรายชิ้น; ตรวจปูนก่อจริง"},
            {"รายการ":"อิฐมอญก่อเต็มแผ่น", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":p_brick_red_full, "ค่าแรง (บาท/หน่วย)":195.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ ตามงานหนาเต็ม"},
            {"รายการ":"อิฐมวลเบา 7.5 ซม.", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":p_brick_light, "ค่าแรง (บาท/หน่วย)":73.0, "หมายเหตุ":"วัสดุฐาน MOC: 23.36 บาท/ก้อน (ส.ค. 2569) แล้วเผื่อวัสดุประกอบ"},
            {"รายการ":"อิฐมวลเบา 10 ซม.", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":p_brick_light_10, "ค่าแรง (บาท/หน่วย)":76.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ งานผนัง"},
            {"รายการ":"อิฐมวลเบา 12.5–15 ซม.", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":450.0, "ค่าแรง (บาท/หน่วย)":80.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ; ตรวจยี่ห้อ/ความหนา"},
            {"รายการ":"อิฐบล็อก 7 ซม.", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":p_brick_block, "ค่าแรง (บาท/หน่วย)":76.0, "หมายเหตุ":"วัสดุฐาน MOC: 5.84 บาท/ก้อน (ส.ค. 2569) + วัสดุประกอบ"},
            {"รายการ":"อิฐบล็อก 10 ซม.", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":p_brick_block_10, "ค่าแรง (บาท/หน่วย)":85.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ งานผนัง"},
            {"รายการ":"อิฐบล็อก 15 ซม.", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":150.0, "ค่าแรง (บาท/หน่วย)":114.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ; MOC มีบล็อกหลายความหนา"},
            {"รายการ":"อิฐโชว์แนว", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":500.0, "ค่าแรง (บาท/หน่วย)":124.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ ตามลาย/ผิว"},
            {"รายการ":"บล็อกช่องลม", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":380.0, "ค่าแรง (บาท/หน่วย)":117.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ ตามลวดลาย"},
            {"รายการ":"สมาร์ทบอร์ด/ไฟเบอร์ซีเมนต์ 8 มม. 2 ด้าน", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":400.0, "ค่าแรง (บาท/หน่วย)":149.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ งานผนังเบา"},
            {"รายการ":"ยิปซัมบอร์ด 12 มม. 2 ด้าน", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":330.0, "ค่าแรง (บาท/หน่วย)":133.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ งานผนังเบา"},
            {"รายการ":"ปูนฉาบสำเร็จรูป", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":p_plaster_mat, "ค่าแรง (บาท/หน่วย)":labour_plastering, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ วัสดุ+ค่าแรง; ตรวจระบบฉาบภายใน/ภายนอก"},
            {"รายการ":"สีทาผนัง", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":p_paint_mat, "ค่าแรง (บาท/หน่วย)":labour_painting, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ; ระบบสี/จำนวนเที่ยวมีผลต่อราคา"},
        ]
        floor_records = [
            {"รายการ":"กระเบื้องแกรนิตโต้ 60x60 + ปูนทราย", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":350.0, "ค่าแรง (บาท/หน่วย)":188.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ; ราคาเปลี่ยนตามเกรด/ยี่ห้อ"},
            {"รายการ":"กระเบื้องเซรามิก 30x30 / 40x40", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":220.0, "ค่าแรง (บาท/หน่วย)":161.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ วัสดุ+ปูนทราย"},
            {"รายการ":"ไม้ลามิเนต 8–12 มม.", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":450.0, "ค่าแรง (บาท/หน่วย)":92.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ; ปริมาณงาน <100 ตร.ม."},
            {"รายการ":"SPC 4–5 มม.", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":450.0, "ค่าแรง (บาท/หน่วย)":100.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ ตลาด"},
            {"รายการ":"ไม้ปาร์เก้/ไม้จริง", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":1000.0, "ค่าแรง (บาท/หน่วย)":179.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ; ขัดทำสีอาจเพิ่ม"},
            {"รายการ":"คอนกรีตขัดมัน/อีพ็อกซี่", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":320.0, "ค่าแรง (บาท/หน่วย)":166.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ ตามระบบผิว"},
        ]
        ceiling_records = [
            {"รายการ":"ยิปซัมฉาบเรียบ + โครง C-Line", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":p_ceiling_mat, "ค่าแรง (บาท/หน่วย)":labour_ceiling, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ; แผ่นยิปซัม MOC ส.ค. 2569 เริ่ม ~111–140 บาท/แผ่น ตามเกรด"},
            {"รายการ":"ยิปซัมทนชื้น 9 มม.", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":350.0, "ค่าแรง (บาท/หน่วย)":110.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ"},
            {"รายการ":"ทีบาร์ 60x60", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":326.0, "ค่าแรง (บาท/หน่วย)":110.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ"},
            {"รายการ":"ฝ้าหลุม/ซ่อนไฟ", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":450.0, "ค่าแรง (บาท/หน่วย)":180.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ; คิดเพิ่มเฉพาะส่วนรายละเอียด"},
            {"รายการ":"ฝ้าไม้ระแนง/WPC", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":600.0, "ค่าแรง (บาท/หน่วย)":220.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ"},
            {"รายการ":"ฝ้าสมาร์ทบอร์ด/ไม้ฝา", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":320.0, "ค่าแรง (บาท/หน่วย)":130.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ"},
        ]
        roof_records = [
            {"รายการ":"เมทัลชีท", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":300.0, "ค่าแรง (บาท/หน่วย)":72.0, "น้ำหนักโครง (กก./ตร.ม.)":18.0, "หมายเหตุ":"ค่าแรงมุงเมทัลชีทอ้างอิง ว480 = 72 บาท/ตร.ม.; วัสดุเป็นราคาอ้างอิงตลาด/ประมาณการ"},
            {"รายการ":"กระเบื้องลอนคู่", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":130.0, "ค่าแรง (บาท/หน่วย)":46.0, "น้ำหนักโครง (กก./ตร.ม.)":20.0, "หมายเหตุ":"ค่าแรง W480 ทรงจั่ว/เพิง; ปั้นหยา/ทรงไทยต่างกัน"},
            {"รายการ":"กระเบื้องคอนกรีต CPAC", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":350.0, "ค่าแรง (บาท/หน่วย)":76.0, "น้ำหนักโครง (กก./ตร.ม.)":28.0, "หมายเหตุ":"ค่าแรง W480; วัสดุเป็นราคาอ้างอิงตลาด/ประมาณการ"},
            {"รายการ":"กระเบื้องแผ่นเรียบ Prestige/Neoclassic", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":470.0, "ค่าแรง (บาท/หน่วย)":82.0, "น้ำหนักโครง (กก./ตร.ม.)":28.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ"},
            {"รายการ":"กระเบื้องดินเผา", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":600.0, "ค่าแรง (บาท/หน่วย)":91.0, "น้ำหนักโครง (กก./ตร.ม.)":25.0, "หมายเหตุ":"ค่าแรง W480 ใกล้เคียงงานกระเบื้องดินเผา"},
            {"รายการ":"กระเบื้องเซรามิก", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":780.0, "ค่าแรง (บาท/หน่วย)":82.0, "น้ำหนักโครง (กก./ตร.ม.)":28.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ"},
            {"รายการ":"แผ่นหลังคา UPVC", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":700.0, "ค่าแรง (บาท/หน่วย)":100.0, "น้ำหนักโครง (กก./ตร.ม.)":18.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ"},
            {"รายการ":"โพลีคาร์บอเนต/แผ่นโปร่งแสง", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":450.0, "ค่าแรง (บาท/หน่วย)":80.0, "น้ำหนักโครง (กก./ตร.ม.)":16.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ"},
            {"รายการ":"ชิงเกิ้ลรูฟ", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":600.0, "ค่าแรง (บาท/หน่วย)":100.0, "น้ำหนักโครง (กก./ตร.ม.)":22.0, "หมายเหตุ":"ราคาอ้างอิงตลาด/ประมาณการ"},
            {"รายการ":"Solar Roof Tiles", "หน่วย":"ตร.ม.", "วัสดุ (บาท/หน่วย)":2500.0, "ค่าแรง (บาท/หน่วย)":350.0, "น้ำหนักโครง (กก./ตร.ม.)":25.0, "หมายเหตุ":"ราคาอ้างอิงตลาดสูง; ต้องอ้าง BOQ/vendor"},
        ]
        door_records = []
        for _name in DOOR_WINDOW_TYPES:
            _m, _l, _note = DOOR_WINDOW_REFERENCE_PRICES.get(_name, (3500.0, 500.0, "ราคาอ้างอิงตลาด — ตรวจราคาจริง"))
            door_records.append({"รายการ":_name, "หน่วย":"ชุด", "วัสดุ (บาท/หน่วย)":_m, "ค่าแรง (บาท/หน่วย)":_l, "หมายเหตุ":_note})

        wall_edit = _editable_price_table("งานก่อผนัง/ฉาบ", wall_records, "price_table_wall")
        floor_edit = _editable_price_table("งานปูพื้น", floor_records, "price_table_floor")
        ceiling_edit = _editable_price_table("งานฝ้า", ceiling_records, "price_table_ceiling")
        roof_edit = _editable_price_table("งานหลังคา", roof_records, "price_table_roof", include_factor=True)
        door_edit = _editable_price_table("งานประตู-หน้าต่าง", door_records, "price_table_door")

# ค่า plaster/paint ถูกแก้ได้จากตารางงานผนังโดยตรง
if not wall_edit.empty:
    _wall_rate_rows = {str(r["รายการ"]): r for _, r in wall_edit.iterrows()}
    _plaster_row = _wall_rate_rows.get("ปูนฉาบสำเร็จรูป")
    _paint_row = _wall_rate_rows.get("สีทาผนัง")
    if _plaster_row is not None:
        p_plaster_mat = safe_num(_plaster_row["วัสดุ (บาท/หน่วย)"])
        labour_plastering = safe_num(_plaster_row["ค่าแรง (บาท/หน่วย)"])
    if _paint_row is not None:
        p_paint_mat = safe_num(_paint_row["วัสดุ (บาท/หน่วย)"])
        labour_painting = safe_num(_paint_row["ค่าแรง (บาท/หน่วย)"])

# สร้างแผนที่ราคาจากตารางที่ผู้ใช้แก้ไขได้ โดยอ้างจากชื่อรายการ ไม่พึ่งตำแหน่งแถว
# จึงไม่เพี้ยนหากมีการจัดเรียงข้อมูลในตาราง

def _table_rate(df, label, default_mat=0.0, default_lab=0.0):
    if df is None or df.empty or "รายการ" not in df.columns:
        return safe_num(default_mat), safe_num(default_lab)
    hits = df[df["รายการ"].astype(str).str.strip() == str(label).strip()]
    if hits.empty:
        return safe_num(default_mat), safe_num(default_lab)
    row = hits.iloc[0]
    return safe_num(row.get("วัสดุ (บาท/หน่วย)", default_mat)), safe_num(row.get("ค่าแรง (บาท/หน่วย)", default_lab))

brick_price_map = {
    "อิฐมอญครึ่งแผ่น (Mon Brick 1/2)": _table_rate(wall_edit, "อิฐมอญครึ่งแผ่น", p_brick_red, 104.0),
    "อิฐมอญก่อเต็มแผ่น (Mon Brick Full)": _table_rate(wall_edit, "อิฐมอญก่อเต็มแผ่น", p_brick_red_full, 195.0),
    "อิฐมวลเบา 7.5 ซม. (Lightweight Concrete 7.5 cm)": _table_rate(wall_edit, "อิฐมวลเบา 7.5 ซม.", p_brick_light, 73.0),
    "อิฐมวลเบา 10 ซม. (Lightweight Concrete 10 cm)": _table_rate(wall_edit, "อิฐมวลเบา 10 ซม.", p_brick_light_10, 76.0),
    "อิฐมวลเบา 12.5 - 15 ซม. (Lightweight Concrete 12.5-15 cm)": _table_rate(wall_edit, "อิฐมวลเบา 12.5–15 ซม.", 450.0, 80.0),
    "อิฐบล็อก 7 ซม. (Concrete Block 7 cm)": _table_rate(wall_edit, "อิฐบล็อก 7 ซม.", p_brick_block, 76.0),
    "อิฐบล็อก 10 ซม. (Concrete Block 10 cm)": _table_rate(wall_edit, "อิฐบล็อก 10 ซม.", p_brick_block_10, 85.0),
    "อิฐบล็อก 15 ซม. (Concrete Block 15 cm)": _table_rate(wall_edit, "อิฐบล็อก 15 ซม.", 150.0, 114.0),
    "อิฐโชว์แนว (Facing Brick)": _table_rate(wall_edit, "อิฐโชว์แนว", 500.0, 124.0),
    "บล็อกช่องลม / อิฐช่องลม (Ventilation Block)": _table_rate(wall_edit, "บล็อกช่องลม", 380.0, 117.0),
    "ผนังเบาสมาร์ทบอร์ด / ไฟเบอร์ซีเมนต์ 8 มม. (2 ด้าน)": _table_rate(wall_edit, "สมาร์ทบอร์ด/ไฟเบอร์ซีเมนต์ 8 มม. 2 ด้าน", 400.0, 149.0),
    "ผนังยิปซัมบอร์ด 12 มม. โครงคร่าวเหล็ก (2 ด้าน)": _table_rate(wall_edit, "ยิปซัมบอร์ด 12 มม. 2 ด้าน", 330.0, 133.0),
}
p_plaster_mat, labour_plastering = _table_rate(wall_edit, "ปูนฉาบสำเร็จรูป", p_plaster_mat, labour_plastering)
p_paint_mat, labour_painting = _table_rate(wall_edit, "สีทาผนัง", p_paint_mat, labour_painting)

floor_price_map = {
    "กระเบื้องแกรนิตโต้ 60x60 ซม. + ปูนทรายปรับระดับ": _table_rate(floor_edit, "กระเบื้องแกรนิตโต้ 60x60 + ปูนทราย", 350.0, 188.0),
    "กระเบื้องเซรามิก 30x30 ซม. / 40x40 ซม. (งานห้องน้ำ/ซักล้าง)": _table_rate(floor_edit, "กระเบื้องเซรามิก 30x30 / 40x40", 220.0, 161.0),
    "ไม้ลามิเนต 8 มม. / 12 มม. + ปูนทรายปรับระดับ": _table_rate(floor_edit, "ไม้ลามิเนต 8–12 มม.", 450.0, 92.0),
    "กระเบื้องยาง SPC 4 มม. / 5 มม. (แบบ Click Lock)": _table_rate(floor_edit, "SPC 4–5 มม.", 450.0, 100.0),
    "พื้นไม้ปาร์เก้ / ไม้จริง + ขัดเงาทำสี": _table_rate(floor_edit, "ไม้ปาร์เก้/ไม้จริง", 1000.0, 179.0),
    "พื้นคอนกรีตขัดมัน (Polished Concrete) / พื้นอีพ็อกซี่ (Epoxy)": _table_rate(floor_edit, "คอนกรีตขัดมัน/อีพ็อกซี่", 320.0, 166.0),
}

ceiling_price_map = {
    "ฝ้ายิปซัมบอร์ด 9 มม. ฉาบเรียบ + โครงคร่าว C-Line": _table_rate(ceiling_edit, "ยิปซัมฉาบเรียบ + โครง C-Line", p_ceiling_mat, labour_ceiling),
    "ฝ้ายิปซัมบอร์ด ทนชื้น 9 มม. (ห้องน้ำ/ชายคา)": _table_rate(ceiling_edit, "ยิปซัมทนชื้น 9 มม.", 350.0, 110.0),
    "ฝ้าเพดานสำเร็จรูป ทีบาร์ 60x60 ซม. (โครงคร่าวอลูมิเนียม)": _table_rate(ceiling_edit, "ทีบาร์ 60x60", 326.0, 110.0),
    "ฝ้าเพดานหลุม / ฝ้าซ่อนไฟ (คิดเพิ่มเฉพาะส่วนหลุม)": _table_rate(ceiling_edit, "ฝ้าหลุม/ซ่อนไฟ", 450.0, 180.0),
    "ฝ้าไม้ระแนง / ฝ้า WPC ทนแดดทนฝน": _table_rate(ceiling_edit, "ฝ้าไม้ระแนง/WPC", 600.0, 220.0),
    "ฝ้าสมาร์ทบอร์ด / ไม้ฝาสำเร็จรูป (ระบายอากาศ)": _table_rate(ceiling_edit, "ฝ้าสมาร์ทบอร์ด/ไม้ฝา", 320.0, 130.0),
}

roof_key_map = [
    ("เมทัลชีท หนา 0.35 - 0.47 mm (พร้อมบุ PE / PU Foam)", "เมทัลชีท"),
    ("กระเบื้องลอนคู่ (ซีเมนต์ใยหิน / ไร้ใยหิน)", "กระเบื้องลอนคู่"),
    ("กระเบื้องคอนกรีตซีแพคโมเนีย (CPAC Monier)", "กระเบื้องคอนกรีต CPAC"),
    ("กระเบื้องแผ่นเรียบเพรสทีจ (Prestige / Neoclassic)", "กระเบื้องแผ่นเรียบ Prestige/Neoclassic"),
    ("กระเบื้องดินเผา / กระเบื้องสุโขทัย", "กระเบื้องดินเผา"),
    ("กระเบื้องเซรามิก (Excella)", "กระเบื้องเซรามิก"),
    ("แผ่นหลังคาไวนิล (UPVC / Plastwood)", "แผ่นหลังคา UPVC"),
    ("แผ่นโพลีคาร์บอเนต / ตราเพชร / แผ่นโปร่งแสง", "โพลีคาร์บอเนต/แผ่นโปร่งแสง"),
    ("หลังคาชิงเกิ้ลรูฟ (Shingle Roof / Asphalt Shingle)", "ชิงเกิ้ลรูฟ"),
    ("หลังคาโซลาร์เซลล์ integrated (Solar Roof Tiles)", "Solar Roof Tiles"),
]
ROOF_MATERIAL_SPECS = {}
for key, label in roof_key_map:
    mat, lab = _table_rate(roof_edit, label, 300.0, 100.0)
    hits = roof_edit[roof_edit["รายการ"].astype(str).str.strip() == label] if not roof_edit.empty else pd.DataFrame()
    steel_factor = safe_num(hits.iloc[0].get("น้ำหนักโครง (กก./ตร.ม.)", 20.0)) if not hits.empty else 20.0
    ROOF_MATERIAL_SPECS[key] = {"mat": mat, "lab": lab, "steel_factor": steel_factor}

DOOR_WINDOW_REFERENCE_PRICES = {}
for k in DOOR_WINDOW_TYPES:
    mat, lab = _table_rate(door_edit, k, 3500.0, 500.0)
    hits = door_edit[door_edit["รายการ"].astype(str).str.strip() == k] if not door_edit.empty else pd.DataFrame()
    note = str(hits.iloc[0].get("หมายเหตุ", "ราคาอ้างอิงตลาด — ตรวจราคาจริง")) if not hits.empty else "ราคาอ้างอิงตลาด — ตรวจราคาจริง"
    DOOR_WINDOW_REFERENCE_PRICES[k] = (mat, lab, note)

# ---------------------------------------------------------
# 5. Header Banner
# ---------------------------------------------------------
st.markdown(f"""
<div class="header-banner">
    <div class="header-title">⚙️ ระบบถอดปริมาณงานโครงสร้าง & สถาปัตย์ (Takeoff V8.3.1 Reliability + Drawing Reader)</div>
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
        
        uploaded_file = st.file_uploader("📤 นำเข้าข้อมูลโครงการ (Restore JSON)", type=["json"], key="restore_json_upload")
        if uploaded_file is not None:
            try:
                upload_bytes = uploaded_file.getvalue()
                upload_hash = hashlib.sha256(upload_bytes).hexdigest()
                if upload_hash == st.session_state.get("last_restore_hash"):
                    st.info("ไฟล์นี้ถูกนำเข้าแล้ว — หากต้องการนำเข้าอีกครั้ง ให้เลือกไฟล์ใหม่หรือเปลี่ยนไฟล์")
                else:
                    data = json.loads(upload_bytes.decode("utf-8"))
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
                    st.session_state["last_restore_hash"] = upload_hash
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
    st.subheader("📖 ช่วยอ่านแบบ PDF → ช่วยกรอก BOQ")
    st.info("วิธีใช้: 1) ลาก PDF → 2) กดอ่านแบบ → 3) เลือกรายการ → 4) นำไปกรอก Tab → 5) ตรวจข้อมูลเทียบแบบ → 6) กดบันทึก BOQ")
    st.caption("หมายเหตุ: PDF ที่มีข้อความฝังอยู่จะอ่านได้ดีกว่า PDF สแกน; OCR ช่วยอ่านตัวหนังสือในภาพ แต่ไม่สามารถยืนยันเส้น/สัญลักษณ์จากแบบแทนผู้ถอดแบบได้")
    st.caption("ระบบจะช่วยหา F1, C1, B1, S1, W1, R1, D1 และข้อมูลที่อ่านได้ แล้วส่งเป็น “ข้อมูลตั้งต้น” ให้คุณตรวจอีกครั้ง — ระบบจะไม่ถือว่าข้อมูลจาก PDF ถูกต้อง 100% และจะไม่สร้าง BOQ จาก OCR โดยอัตโนมัติ")
    st.caption("💡 อ้างอิงจากแบบ = ข้อมูลช่วยบอกว่าเลขนั้นมาจากแผ่น/ตำแหน่งไหน ไม่ต้องกรอกเพื่อให้สูตรคำนวณทำงาน")
    st.caption("⚠️ Reader อ่านตัวอักษรและตัวเลขที่พบใน PDF เป็นหลัก — เส้นบอกระยะ ลูกศร สัญลักษณ์ และรายละเอียดกราฟิกบางชนิดอาจอ่านไม่ครบ จึงต้องตรวจเทียบแบบจริงเสมอ")
    pdf_file = st.file_uploader("ลาก PDF แบบมาวางที่นี่", type=["pdf"], key="drawing_pdf_upload")
    rr1, rr2, rr3 = st.columns(3)
    use_ocr = rr1.checkbox("ช่วยอ่านหน้า PDF ที่เป็นรูปภาพ (OCR)", value=True, key="drawing_use_ocr", help="ถ้า PDF มีข้อความอยู่แล้ว ระบบจะใช้ข้อความเดิมก่อน ไม่ OCR ซ้ำ")
    max_pages = rr2.number_input("จำนวนหน้าสูงสุดที่อ่าน", min_value=1, max_value=200, value=80, step=10, key="drawing_max_pages")
    run_reader = rr3.button("🔎 อ่านแบบและดึงข้อมูล", type="primary", use_container_width=True, key="btn_run_drawing_reader")
    if run_reader and pdf_file is not None:
        try:
            with st.spinner("กำลังอ่าน PDF และหา C1 / B1 / S1 / F1 ที่อยู่ในแบบ..."):
                result = read_drawing_pdf(pdf_file.getvalue(), use_ocr=use_ocr, max_pages=int(max_pages))
            st.session_state["drawing_reader_result"] = result
            st.session_state["drawing_candidates"] = result.get("candidates", [])
            st.session_state["drawing_pdf_name"] = pdf_file.name
            st.success(f"อ่าน {result['pages_read']} หน้า จากทั้งหมด {result['page_count']} หน้า | พบรายการที่ระบบอ่านได้ {len(result['candidates'])} รายการ")
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
        if result.get("ocr_available") is False and use_ocr:
            st.warning("เครื่องนี้ยังไม่มี Tesseract/Pillow ที่พร้อมใช้ — อ่านได้เฉพาะข้อความที่ฝังอยู่ใน PDF")
        if not candidates:
            st.warning("ยังหา F/C/B/S/W/R/D ไม่พบ ลองตรวจว่า PDF เป็นแบบสแกนหรือข้อความถูกแปลงเป็นเส้นภาพทั้งหมด")
        else:
            rows=[]
            for idx,c in enumerate(candidates):
                dims, rb, steel=candidate_summary(c)
                evidence=c.get("evidence",{})
                missing=[]
                if c.get("target") in ("ฐานราก","เสา","คาน","พื้น") and not evidence.get("ขนาด"): missing.append("ขนาด")
                if c.get("target") in ("เสา","คาน") and not evidence.get("ความยาว/สูง"): missing.append("สูง/ยาว")
                if c.get("target") in ("ฐานราก","เสา","คาน","พื้น") and not evidence.get("เหล็ก"): missing.append("เหล็ก")
                conf="ข้อมูลค่อนข้างครบ" if c["confidence"]>=0.8 else ("ข้อมูลบางส่วน" if c["confidence"]>=0.65 else "ต้องตรวจมาก")
                review="ต้องตรวจ — " + ", ".join(missing) if missing else "ต้องตรวจเทียบแบบ"
                rows.append({"#":idx+1,"หน้า":c["page"],"รหัส":c["mark"],"ไปที่":c["target"],"ขนาด/ความยาว":dims,"เหล็กที่อ่านได้":rb,"โครงเหล็กที่อ่านได้":steel,"ระดับข้อมูลที่อ่านได้":conf,"สถานะ":review})
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            options=[f"#{i+1} | หน้า {c['page']} | {c['mark']} → {c['target']}" for i,c in enumerate(candidates)]
            sel=st.selectbox("เลือกรายการจากแบบที่ต้องการนำไปใช้", options, key="drawing_candidate_select")
            sel_idx=options.index(sel) if sel in options else 0
            chosen=candidates[sel_idx]
            dims,rb,steel=candidate_summary(chosen)
            ev=chosen.get("evidence",{})
            missing=[]
            if chosen.get("target") in ("ฐานราก","เสา","คาน","พื้น") and not ev.get("ขนาด"): missing.append("ขนาด")
            if chosen.get("target") in ("เสา","คาน") and not ev.get("ความยาว/สูง"): missing.append("สูง/ยาว")
            if chosen.get("target") in ("ฐานราก","เสา","คาน","พื้น") and not ev.get("เหล็ก"): missing.append("เหล็ก")
            if missing:
                st.warning("⚠️ ระบบอ่านได้ไม่ครบ: " + ", ".join(missing) + " — ระบบจะไม่เดาค่าให้ กรุณาตรวจจากแบบก่อนใช้")
            else:
                st.warning("🔍 ระบบอ่านข้อมูลได้หลายส่วน แต่ยังต้องตรวจเทียบกับแบบจริงก่อนบันทึก BOQ")
            st.info(f"เลือกแล้ว: **{chosen['mark']}** | {dims} | เหล็ก: {rb} | โครงเหล็ก: {steel} | {chosen['source']}\n\n⚠️ ข้อมูลจาก PDF เป็นข้อมูลช่วยกรอก ไม่ใช่การยืนยันแบบ — ตรวจเทียบแบบจริงก่อนบันทึก BOQ ทุกครั้ง")
            if pdf_file is not None and fitz is not None:
                if st.button(f"👁 ดูหน้าแบบที่พบ {chosen['mark']}", key="btn_preview_drawing_page"):
                    try:
                        preview_doc=fitz.open(stream=pdf_file.getvalue(),filetype="pdf")
                        preview_page=preview_doc.load_page(max(0,int(chosen.get("page",1))-1))
                        preview_pix=preview_page.get_pixmap(matrix=fitz.Matrix(1.15,1.15),alpha=False)
                        preview_img=Image.frombytes("RGB",[preview_pix.width,preview_pix.height],preview_pix.samples) if Image is not None else None
                        if preview_img is not None:
                            st.image(preview_img,caption=f"หน้า {chosen.get('page')} — ใช้ตรวจเทียบแบบจริง",use_container_width=True)
                        preview_doc.close()
                    except Exception as preview_exc:
                        st.warning(f"ไม่สามารถแสดงหน้าแบบได้: {preview_exc}")
            if st.button(f"📌 นำ {chosen['mark']} ไปใช้ในงาน{chosen['target']}", key="btn_queue_drawing_candidate"):
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
    if not st.session_state["footing_rebars"]:
        st.info("ยังไม่ได้ระบุเหล็กจากแบบ — ระบบจะยังไม่เดาเหล็กให้ | กด ‘เพิ่มรายการเหล็ก’ หรือใช้ข้อมูลจาก Drawing Reader")
    if st.button("➕ เพิ่มรายการเหล็ก", key="btn_add_f_rebar"):
        st.session_state["footing_rebars"].append({"pos": "เหล็กวิ่งตามยาว", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 10.0, "len": 1.50, "lap_mode": "ไม่มี", "lap_ends": 0})
        st.rerun()

    f_rebars_to_remove = []
    tot_footing_rebar_weight = 0.0
    footing_rebar_detail = {}
    footing_lengths = []
    footing_pos_options = ["เหล็กวิ่งตามยาว", "เหล็กวิ่งตามกว้าง", "เหล็กเสริมพิเศษ"]

    for idx, r in enumerate(st.session_state["footing_rebars"]):
        r["lap_mode"] = normalize_lap_mode(r.get("lap_mode")); r.setdefault("lap_mode", "ไม่มี"); r.setdefault("lap_ends", 0)
        c1, c2, c3, c4, c5, c6, c7 = st.columns([1.35, 1.1, 1.55, 1.1, 1.25, 1.2, 0.45])
        r["pos"] = c1.selectbox(f"แนว #{idx+1}", footing_pos_options, index=footing_pos_options.index(r.get("pos", footing_pos_options[0])) if r.get("pos", footing_pos_options[0]) in footing_pos_options else 0, key=f"f_pos_{idx}")
        r["type"] = c2.selectbox(f"เหล็ก #{idx+1}", REBAR_LIST, index=REBAR_LIST.index(r.get("type", "DB12")) if r.get("type") in REBAR_LIST else 2, key=f"f_type_{idx}")
        r["mode"] = c3.selectbox(f"วิธีระบุเหล็ก #{idx+1}", ["จำนวน (เส้น)", "ระยะห่าง (@ ม.)"], index=0 if r.get("mode") == "จำนวน (เส้น)" else 1, key=f"f_mode_{idx}")
        r["val"] = c4.number_input(f"จำนวน / ระยะห่าง #{idx+1}", min_value=0.0001, value=max(0.0001, safe_num(r.get("val", 1))), key=f"f_val_{idx}")
        r["len"] = c5.number_input(f"ความยาวเหล็ก/ช่วงนับ #{idx+1}", min_value=0.0, value=max(0.0, safe_num(r.get("len", 1.5))), key=f"f_len_{idx}")
        r["lap_mode"] = c6.selectbox(f"วิธีเผื่อปลาย/ต่อเหล็ก #{idx+1}", ["ไม่มี", "ทาบ", "ฝาก/ฝังปลายเหล็ก"], index=["ไม่มี", "ทาบ", "ฝาก/ฝังปลายเหล็ก"].index(r.get("lap_mode", "ไม่มี")), key=f"f_lapmode_{idx}")
        r["lap_ends"] = c6.number_input(f"จำนวนปลายที่เผื่อ #{idx+1}", min_value=0, max_value=2, value=int(safe_num(r.get("lap_ends", 0))), step=1, key=f"f_lapends_{idx}") if r["lap_mode"] != "ไม่มี" else 0
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

    allow_empty_footing_rebar = rebar_save_guard(st.session_state["footing_rebars"], "allow_empty_footing_rebar", "ฐานราก")
    if st.button("➕ บันทึกงานฐานราก", type="primary", key="btn_save_footing"):
        if not allow_empty_footing_rebar:
            st.stop()
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
        lab_c = net_concrete*labour_concrete + calc_rebar_labor_from_breakdown({k:v*f_qty for k,v in footing_rebar_detail.items()}, labour_rebar, use_w480_rebar_labor) + net_formwork*labour_formwork

        if "คิดรวมตาม ว480" in excavation_labor_mode:
            excavation_rate_used = excavation_labor_w480_rate(vol_excavation, f_depth, cost_excavation, auto_excavation_labor)
            excavation_labor_total = vol_excavation * excavation_rate_used
            excavation_labor_note = f"ว480 งานขุดหลุมฐานราก+ถมคืนรวม | {excavation_rate_used:.0f} บาท/ลบ.ม."
        else:
            excavation_rate_used = excavation_labor_w480_rate(vol_excavation, f_depth, cost_excavation, auto_excavation_labor)
            excavation_labor_total = vol_excavation * excavation_rate_used + vol_backfill * cost_backfill
            excavation_labor_note = f"แยกงาน: ขุด {excavation_rate_used:.0f} + ถม {cost_backfill:.0f} บาท/ลบ.ม."
        add_takeoff_item({
            "หมวด": "งานดินขุด-ดินถม", "รายการ": f"งานดินสำหรับฐานราก {f_name}",
            "ที่มาในแบบ": f_ref, "แหล่งข้อมูล": "จากขนาดฐาน/ระดับขุด",
            "รายละเอียด": f"หลุม {f_w:.2f}x{f_l:.2f}ม. + พื้นที่เผื่อทำงาน {f_work_space:.2f}ม./ด้าน | ลึก {f_depth:.2f}ม. | {excavation_labor_note}",
            "จำนวน": f_qty, "ดินขุด (ลบ.ม.)": round(vol_excavation,2), "ดินถม (ลบ.ม.)": round(vol_backfill,2),
            "ค่าวัสดุ (บาท)": 0.0, "ค่าแรง (บาท)": round(excavation_labor_total,2)
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
    auto_col_geo = st.checkbox("ช่วยคำนวณความยาวเหล็กจากขนาด + Cover (ระยะหุ้มคอนกรีต) + ความสูง/ขนาดเสา", value=True, key="auto_col_geo")

    st.markdown("#### 🔩 เหล็กเสริมเสา")
    if not st.session_state["column_rebars"]:
        st.info("ยังไม่ได้ระบุเหล็กจากแบบ — ระบบจะยังไม่เดาเหล็กให้ | กด ‘เพิ่มเหล็กเสา’ หรือใช้ข้อมูลจาก Drawing Reader")
    st.caption("ถ้าเป็นเหล็กปลอกและเลือก @ ให้ใส่ ‘ความยาวช่วง/เหล็ก’ เป็นความยาวของช่วงที่ใช้ระยะห่างนั้น")
    if st.button("➕ เพิ่มเหล็กเสา", key="btn_add_col_rebar"):
        st.session_state["column_rebars"].append({"pos":"เหล็กแกน","type":"DB12","mode":"จำนวน (เส้น)","val":4.0,"len":col_h,"lap_mode":"ไม่มี","lap_ends":0})
        st.rerun()
    c_rebars_to_remove=[]; tot_col_rebar_weight=0.0; col_rebar_detail={}
    col_pos_options=["เหล็กแกน","เหล็กปลอก","เหล็กเสริมพิเศษ"]
    for idx,r in enumerate(st.session_state["column_rebars"]):
        r.setdefault("lap_mode","ไม่มี"); r.setdefault("lap_ends",0)
        a1,a2,a3,a4,a5,a6,a7 = st.columns([1.35,1.05,1.5,1.05,1.25,1.2,0.45])
        r["pos"] = a1.selectbox(f"ตำแหน่ง #{idx+1}",col_pos_options,index=col_pos_options.index(r.get("pos","เหล็กแกน")) if r.get("pos") in col_pos_options else 0,key=f"c_pos_{idx}")
        r["type"] = a2.selectbox(f"เหล็ก #{idx+1}",REBAR_LIST,index=REBAR_LIST.index(r.get("type","DB12")) if r.get("type") in REBAR_LIST else 2,key=f"c_type_{idx}")
        r["mode"] = a3.selectbox(f"วิธีระบุเหล็ก #{idx+1}",["จำนวน (เส้น)","ระยะห่าง (@ ม.)"],index=0 if r.get("mode")=="จำนวน (เส้น)" else 1,key=f"c_mode_{idx}")
        r["val"] = a4.number_input(f"จำนวน / ระยะห่าง #{idx+1}",min_value=0.0001,value=max(0.0001,safe_num(r.get("val",4))),key=f"c_val_{idx}")
        r["len"] = a5.number_input(f"ความยาวเหล็ก/ช่วงนับ #{idx+1}",min_value=0.0,value=max(0.0,safe_num(r.get("len",col_h))),key=f"c_len_{idx}")
        r["lap_mode"] = a6.selectbox(f"วิธีเผื่อปลาย/ต่อเหล็ก #{idx+1}",["ไม่มี","ทาบ","ฝาก/ฝังปลายเหล็ก"],index=["ไม่มี","ทาบ","ฝาก/ฝังปลายเหล็ก"].index(r.get("lap_mode","ไม่มี")),key=f"c_lapmode_{idx}")
        r["lap_ends"] = a6.number_input(f"จำนวนปลายที่เผื่อ #{idx+1}",min_value=0,max_value=2,value=int(safe_num(r.get("lap_ends",0))),step=1,key=f"c_lapends_{idx}") if r["lap_mode"]!="ไม่มี" else 0
        if a7.button("🗑",key=f"del_c_rebar_{idx}"): c_rebars_to_remove.append(idx)
        count_span_col = max(0.0, safe_num(r.get("len", col_h))) if "ปลอก" in str(r.get("pos", "")) and r.get("mode") != "จำนวน (เส้น)" else col_h
        total_len_row = steel_row_length(r,col_h,count_span_col,cover_column_mm,r["lap_mode"],r["lap_ends"],auto_col_geo,member_w=col_w,member_h=col_l,hook_extra_m=stirrup_hook_extra_m,lap_factor_d=lap_factor_d,dev_factor_d=dev_factor_d)
        w_row = total_len_row*REBAR_WEIGHT[r["type"]]
        tot_col_rebar_weight += w_row; col_rebar_detail[r["type"]]=col_rebar_detail.get(r["type"],0.0)+w_row
    if c_rebars_to_remove:
        st.session_state["column_rebars"]=[item for i,item in enumerate(st.session_state["column_rebars"]) if i not in c_rebars_to_remove]; st.rerun()

    allow_empty_column_rebar = rebar_save_guard(st.session_state["column_rebars"], "allow_empty_column_rebar", "เสา")
    if st.button("➕ บันทึกงานเสา",type="primary",key="btn_save_col"):
        if not allow_empty_column_rebar:
            st.stop()
        net_concrete=col_w*col_l*col_h*col_qty; vol=with_waste(net_concrete,waste_concrete)
        net_form=2*(col_w+col_l)*col_h*col_qty; form=with_waste(net_form,waste_formwork)
        net_rebar_weight=tot_col_rebar_weight*col_qty; rebar_weight=with_waste(net_rebar_weight,waste_rebar)
        rebar_breakdown={k:round(with_waste(v*col_qty,waste_rebar),2) for k,v in col_rebar_detail.items()}
        mat_c=vol*p_concrete+sum(v*get_rebar_price(k,p_db12,p_rb9) for k,v in rebar_breakdown.items())+form*p_formwork
        lab_c=net_concrete*labour_concrete+calc_rebar_labor_from_breakdown({k:v*col_qty for k,v in col_rebar_detail.items()}, labour_rebar, use_w480_rebar_labor)+net_form*labour_formwork
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
    if not st.session_state["beam_rebars"]:
        st.info("ยังไม่ได้ระบุเหล็กจากแบบ — ระบบจะยังไม่เดาเหล็กให้ | กด ‘เพิ่มเหล็กคาน’ หรือใช้ข้อมูลจาก Drawing Reader")
    st.caption("ถ้าเป็นเหล็กปลอกและเลือก @ ให้ใส่ ‘ความยาวช่วง/เหล็ก’ เป็นความยาวของช่วงที่ใช้ระยะห่างนั้น")
    if st.button("➕ เพิ่มเหล็กคาน",key="btn_add_beam_rebar"):
        st.session_state["beam_rebars"].append({"pos":"เหล็กบน","type":"DB12","mode":"จำนวน (เส้น)","val":2.0,"len":beam_l,"lap_mode":"ไม่มี","lap_ends":0}); st.rerun()
    b_rebars_to_remove=[]; tot_beam_rebar_weight=0.0; beam_rebar_detail={}
    beam_pos_options=["เหล็กบน","เหล็กล่าง","เหล็กเสริมพิเศษ","เหล็กปลอก"]
    for idx,r in enumerate(st.session_state["beam_rebars"]):
        r["lap_mode"]=normalize_lap_mode(r.get("lap_mode"));r.setdefault("lap_mode","ไม่มี");r.setdefault("lap_ends",0)
        a1,a2,a3,a4,a5,a6,a7=st.columns([1.35,1.05,1.5,1.05,1.25,1.2,0.45])
        r["pos"]=a1.selectbox(f"ตำแหน่ง #{idx+1}",beam_pos_options,index=beam_pos_options.index(r.get("pos","เหล็กบน")) if r.get("pos") in beam_pos_options else 0,key=f"b_pos_{idx}")
        r["type"]=a2.selectbox(f"เหล็ก #{idx+1}",REBAR_LIST,index=REBAR_LIST.index(r.get("type","DB12")) if r.get("type") in REBAR_LIST else 2,key=f"b_type_{idx}")
        r["mode"]=a3.selectbox(f"วิธีระบุเหล็ก #{idx+1}",["จำนวน (เส้น)","ระยะห่าง (@ ม.)"],index=0 if r.get("mode")=="จำนวน (เส้น)" else 1,key=f"b_mode_{idx}")
        r["val"]=a4.number_input(f"จำนวน / ระยะห่าง #{idx+1}",min_value=0.0001,value=max(0.0001,safe_num(r.get("val",2))),key=f"b_val_{idx}")
        r["len"]=a5.number_input(f"ความยาวเหล็ก/ช่วงนับ #{idx+1}",min_value=0.0,value=max(0.0,safe_num(r.get("len",beam_l))),key=f"b_len_{idx}")
        r["lap_mode"]=a6.selectbox(f"วิธีเผื่อปลาย/ต่อเหล็ก #{idx+1}",["ไม่มี","ทาบ","ฝาก/ฝังปลายเหล็ก"],index=["ไม่มี","ทาบ","ฝาก/ฝังปลายเหล็ก"].index(r.get("lap_mode","ไม่มี")),key=f"b_lapmode_{idx}")
        r["lap_ends"]=a6.number_input(f"จำนวนปลายที่เผื่อ #{idx+1}",min_value=0,max_value=2,value=int(safe_num(r.get("lap_ends",0))),step=1,key=f"b_lapends_{idx}") if r["lap_mode"]!="ไม่มี" else 0
        if a7.button("🗑",key=f"del_b_rebar_{idx}"): b_rebars_to_remove.append(idx)
        count_span_beam = max(0.0, safe_num(r.get("len", beam_l))) if "ปลอก" in str(r.get("pos", "")) and r.get("mode") != "จำนวน (เส้น)" else beam_l
        total_len_row=steel_row_length(r,beam_l,count_span_beam,cover_beam_mm,r["lap_mode"],r["lap_ends"],auto_beam_geo,member_w=beam_w,member_h=beam_h,hook_extra_m=stirrup_hook_extra_m,lap_factor_d=lap_factor_d,dev_factor_d=dev_factor_d)
        w_row=total_len_row*REBAR_WEIGHT[r["type"]]; tot_beam_rebar_weight+=w_row; beam_rebar_detail[r["type"]]=beam_rebar_detail.get(r["type"],0.0)+w_row
    if b_rebars_to_remove:
        st.session_state["beam_rebars"]=[item for i,item in enumerate(st.session_state["beam_rebars"]) if i not in b_rebars_to_remove];st.rerun()

    allow_empty_beam_rebar = rebar_save_guard(st.session_state["beam_rebars"], "allow_empty_beam_rebar", "คาน")
    if st.button("➕ บันทึกงานคาน",type="primary",key="btn_save_beam"):
        if not allow_empty_beam_rebar:
            st.stop()
        effective_beam_h=max(0.0,beam_h-beam_slab_t) if "หัก" in beam_slab_mode else beam_h
        net_concrete=beam_w*effective_beam_h*beam_l*beam_qty
        vol=with_waste(net_concrete,waste_concrete)
        net_form=((2*effective_beam_h+beam_w)*beam_l*beam_qty) if "หัก" in beam_slab_mode else ((2*beam_h+beam_w)*beam_l*beam_qty)
        form=with_waste(net_form,waste_formwork)
        net_rebar_weight=tot_beam_rebar_weight*beam_qty;rebar_weight=with_waste(net_rebar_weight,waste_rebar)
        rebar_breakdown={k:round(with_waste(v*beam_qty,waste_rebar),2) for k,v in beam_rebar_detail.items()}
        mat_c=vol*p_concrete+sum(v*get_rebar_price(k,p_db12,p_rb9) for k,v in rebar_breakdown.items())+form*p_formwork
        lab_c=net_concrete*labour_concrete+calc_rebar_labor_from_breakdown({k:v*beam_qty for k,v in beam_rebar_detail.items()}, labour_rebar, use_w480_rebar_labor)+net_form*labour_formwork
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
    slab_support=st.selectbox("ลักษณะพื้น",["พื้นยก/พื้น คสล. มีแบบหล่อใต้ท้องพื้น","พื้นวางบนดิน"],key="s_support")
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
    slab_opening_rebar_deduct = st.number_input("เหล็กที่ต้องหักบริเวณช่องเปิดจากแบบ (กก.)", min_value=0.0, value=0.0, step=1.0, key="s_opening_rebar_deduct")
    st.caption("คอนกรีตหักตามช่องเปิดให้อัตโนมัติ ส่วนเหล็กให้ยึดแบบจริง: กรอกทั้ง ‘เพิ่มรอบช่องเปิด’ และ ‘หักเหล็กที่หายไป’ เมื่อแบบระบุ")
    auto_slab_geo=st.checkbox("ช่วยตั้งความยาวเหล็กจาก Cover (ระยะหุ้มคอนกรีต) + ทิศทางพื้น",value=True,key="auto_slab_geo")

    st.markdown("#### 🔩 เหล็กเสริมพื้น")
    if not st.session_state["slab_rebars"]:
        st.info("ยังไม่ได้ระบุเหล็กจากแบบ — ระบบจะยังไม่เดาเหล็กให้ | กด ‘เพิ่มรายการเหล็กพื้น’ หรือใช้ข้อมูลจาก Drawing Reader")
    st.caption("ถ้าเป็นเหล็กปลอกและเลือก @ ให้ใส่ ‘ความยาวช่วง/เหล็ก’ เป็นความยาวของช่วงที่ใช้ระยะห่างนั้น")
    if st.button("➕ เพิ่มรายการเหล็กพื้น",key="btn_add_s_rebar"):
        st.session_state["slab_rebars"].append({"pos":"เหล็กล่าง/ตะแกรงทางยาว","type":"RB9","mode":"ระยะห่าง (@ ม.)","val":0.20,"len":slab_l,"lap_mode":"ไม่มี","lap_ends":0});st.rerun()
    s_rebars_to_remove=[];tot_slab_rebar_weight=0.0;slab_rebar_detail={}
    slab_pos_options=["เหล็กล่าง/ตะแกรงทางยาว","เหล็กล่าง/ตะแกรงทางกว้าง","เหล็กบน/ตะแกรงทางยาว","เหล็กบน/ตะแกรงทางกว้าง","เหล็กคอมเมนท์/เหล็กเสริมพิเศษ"]
    for idx,r in enumerate(st.session_state["slab_rebars"]):
        r["lap_mode"]=normalize_lap_mode(r.get("lap_mode"));r.setdefault("lap_mode","ไม่มี");r.setdefault("lap_ends",0)
        a1,a2,a3,a4,a5,a6,a7=st.columns([1.55,1.05,1.5,1.05,1.2,1.2,0.45])
        r["pos"]=a1.selectbox(f"ตำแหน่ง #{idx+1}",slab_pos_options,index=slab_pos_options.index(r.get("pos",slab_pos_options[0])) if r.get("pos") in slab_pos_options else 0,key=f"s_pos_{idx}")
        r["type"]=a2.selectbox(f"เหล็ก #{idx+1}",REBAR_LIST,index=REBAR_LIST.index(r.get("type","RB9")) if r.get("type") in REBAR_LIST else 1,key=f"s_type_{idx}")
        r["mode"]=a3.selectbox(f"วิธีระบุเหล็ก #{idx+1}",["จำนวน (เส้น)","ระยะห่าง (@ ม.)"],index=0 if r.get("mode")=="จำนวน (เส้น)" else 1,key=f"s_mode_{idx}")
        r["val"]=a4.number_input(f"จำนวน / ระยะห่าง #{idx+1}",min_value=0.0001,value=max(0.0001,safe_num(r.get("val",0.2))),key=f"s_val_{idx}")
        r["len"]=a5.number_input(f"ความยาวเหล็ก/ช่วงนับ #{idx+1}",min_value=0.0,value=max(0.0,safe_num(r.get("len",slab_l))),key=f"s_len_{idx}")
        r["lap_mode"]=a6.selectbox(f"วิธีเผื่อปลาย/ต่อเหล็ก #{idx+1}",["ไม่มี","ทาบ","ฝาก/ฝังปลายเหล็ก"],index=["ไม่มี","ทาบ","ฝาก/ฝังปลายเหล็ก"].index(r.get("lap_mode","ไม่มี")),key=f"s_lapmode_{idx}")
        r["lap_ends"]=a6.number_input(f"จำนวนปลายที่เผื่อ #{idx+1}",min_value=0,max_value=2,value=int(safe_num(r.get("lap_ends",0))),step=1,key=f"s_lapends_{idx}") if r["lap_mode"]!="ไม่มี" else 0
        if a7.button("🗑",key=f"del_s_rebar_{idx}"):s_rebars_to_remove.append(idx)
        count_span=slab_w if "ทางยาว" in r["pos"] else slab_l
        base_len=slab_l if "ทางยาว" in r["pos"] else slab_w
        total_len_row=steel_row_length(r,base_len,count_span,cover_slab_mm,r["lap_mode"],r["lap_ends"],auto_slab_geo,lap_factor_d=lap_factor_d,dev_factor_d=dev_factor_d)
        w_row=total_len_row*REBAR_WEIGHT[r["type"]];tot_slab_rebar_weight+=w_row;slab_rebar_detail[r["type"]]=slab_rebar_detail.get(r["type"],0.0)+w_row
    if s_rebars_to_remove:
        st.session_state["slab_rebars"]=[item for i,item in enumerate(st.session_state["slab_rebars"]) if i not in s_rebars_to_remove];st.rerun()

    allow_empty_slab_rebar = rebar_save_guard(st.session_state["slab_rebars"], "allow_empty_slab_rebar", "พื้น")
    if st.button("➕ บันทึกงานพื้น",type="primary",key="btn_save_slab"):
        if not allow_empty_slab_rebar:
            st.stop()
        gross_area=slab_w*slab_l*slab_qty; net_area=max(0.0,gross_area-slab_openings)
        net_concrete=net_area*slab_h;vol=with_waste(net_concrete,waste_concrete)
        net_form=net_area if "พื้นยก" in slab_support else 0.0;form=with_waste(net_form,waste_formwork)
        net_rebar_weight=max(0.0, tot_slab_rebar_weight*slab_qty + slab_opening_rebar_add - slab_opening_rebar_deduct)
        rebar_weight=with_waste(net_rebar_weight,waste_rebar)
        rebar_breakdown={k:round(with_waste(v*slab_qty,waste_rebar),2) for k,v in slab_rebar_detail.items()}
        if slab_opening_rebar_add > 0:
            rebar_breakdown["เหล็กเสริมรอบช่องเปิด (ตามแบบ)"] = round(with_waste(slab_opening_rebar_add, waste_rebar), 2)
        if slab_opening_rebar_deduct > 0:
            rebar_breakdown["หักเหล็กช่องเปิด (ตามแบบ)"] = -round(with_waste(slab_opening_rebar_deduct, waste_rebar), 2)
        mat_c=vol*p_concrete+sum(v*get_rebar_price(k,p_db12,p_rb9) if k in REBAR_WEIGHT else v*p_rb9 for k,v in rebar_breakdown.items())+form*p_formwork
        lab_c=net_concrete*labour_concrete+calc_rebar_labor_from_breakdown({k:v*slab_qty for k,v in slab_rebar_detail.items()}, labour_rebar, use_w480_rebar_labor)+max(0.0,slab_opening_rebar_add-slab_opening_rebar_deduct)*labour_rebar+net_form*labour_formwork
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
        open_mode = st.selectbox("วิธีหักช่องเปิด", ["ไม่มีช่องเปิด", "กว้าง×สูง×จำนวน", "พื้นที่ช่องเปิดรวมจากแบบ"], key="wall_open_mode")
        if open_mode == "ไม่มีช่องเปิด":
            opening_area_total = 0.0
            deduct_w, deduct_h, deduct_qty = 0.0, 0.0, 0
        elif open_mode == "กว้าง×สูง×จำนวน":
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

            # ทับหลัง: ถ้าเลือก “ประมาณจากช่องเปิด” จะประมาณจากความกว้างช่องเปิด;
            # หากกรอกพื้นที่ช่องเปิดรวมเพียงอย่างเดียว ระบบจะไม่เดาความยาวทับหลังให้เป็นศูนย์ เพื่อป้องกัน BOQ ต่ำกว่าจริง
            opening_lintel_len = (deduct_w * deduct_qty) if open_mode == "กว้าง×สูง×จำนวน" and deduct_qty > 0 else 0.0
            if lintel_mode == "ประมาณจากช่องเปิด" and open_mode == "พื้นที่ช่องเปิดรวมจากแบบ" and lintel_extra_len <= 0:
                st.warning("⚠ ช่องเปิดถูกกรอกเป็นพื้นที่รวม จึงยังคำนวณความยาวทับหลังจากช่องเปิดไม่ได้ — แนะนำกรอกความยาวทับหลังรวมจากแบบ")
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
            cost_lintel_lab = net_lintel_concrete * labour_concrete + net_rebar_lintel_weight * (get_rebar_labor_rate(lintel_rebar_type, labour_rebar) if use_w480_rebar_labor else labour_rebar) + net_form_lintel * labour_formwork

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
        dw_ref_mat, dw_ref_lab, dw_ref_note = DOOR_WINDOW_REFERENCE_PRICES.get(dw_type, (3500.0, 500.0, "ราคาอ้างอิงตลาด — ควรตรวจราคาจริง"))
        st.caption(f"ราคาอ้างอิงตั้งต้น: วัสดุ {dw_ref_mat:,.0f} บาท/ชุด | ค่าแรง {dw_ref_lab:,.0f} บาท/ชุด | {dw_ref_note} | แก้ราคาเองได้")
        dw_c1, dw_c2 = st.columns(2)
        dw_price_key = "dw_mat_" + re.sub(r"\W+", "_", dw_type)
        dw_lab_key = "dw_lab_" + re.sub(r"\W+", "_", dw_type)
        cost_per_set_mat = dw_c1.number_input(f"ราคาชุดบาน+วงกบ+อุปกรณ์ (บาท/ชุด) — {dw_type}", min_value=0.0, value=float(dw_ref_mat), step=100.0, key=dw_price_key)
        cost_per_set_lab = dw_c2.number_input(f"ค่าแรงติดตั้ง (บาท/ชุด) — {dw_type}", min_value=0.0, value=float(dw_ref_lab), step=50.0, key=dw_lab_key)

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
        floor_deduct_area = st.number_input("พื้นที่ที่ไม่ปู / ช่องหัก (ตร.ม.)", min_value=0.0, value=0.0, step=0.10, key="floor_deduct_area")
        floor_ref = st.text_input("อ้างอิงจากแบบ (ไม่บังคับ)", value="", key="floor_ref", help="เช่น A-06 / ห้องรับแขก / F-01")

        if st.button("➕ บันทึกงานปูพื้น", type="primary", key="btn_save_floor"):
            area_calculated = (floor_w * floor_l * floor_qty)
            net_area = max(0.0, area_calculated - floor_deduct_area)
            proc_area = with_waste(net_area, waste_finishing)
            
            p_mat, p_lab = floor_price_map.get(floor_material_type, (p_tile_mat, labour_tile))
            
            add_takeoff_item({
                "หมวด": "งานปูพื้นและตกแต่งผิว",
                "รายการ": floor_name,
                "ที่มาในแบบ": floor_ref,
                "แหล่งข้อมูล": "จากแบบสถาปัตย์",
                "รายละเอียด": f"{floor_material_type} ขนาด {floor_w:.2f}x{floor_l:.2f}ม. ({floor_qty} พื้นที่) | หัก {floor_deduct_area:.1f} ตร.ม. | สุทธิ {net_area:.1f} ตร.ม. | จัดซื้อ {proc_area:.1f} ตร.ม.",
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
    ceiling_qty = cl2.number_input("จำนวนพื้นที่", min_value=1, value=1, key="ceiling_qty")

    cl_dim1, cl_dim2, cl_type_col = st.columns([1, 1, 2])
    ceiling_w = cl_dim1.number_input("ความกว้างฝ้า (เมตร)", min_value=0.05, value=5.00, step=0.10, key="ceiling_w")
    ceiling_l = cl_dim2.number_input("ความยาวฝ้า (เมตร)", min_value=0.05, value=8.00, step=0.10, key="ceiling_l")
    
    ceiling_type = cl_type_col.selectbox("ประเภทฝ้าเพดาน", list(ceiling_price_map.keys()), key="ceiling_type")
    ceiling_deduct_area = st.number_input("พื้นที่ที่ไม่ทำฝ้า / ช่องหัก (ตร.ม.)", min_value=0.0, value=0.0, step=0.10, key="ceiling_deduct_area")
    ceiling_ref = st.text_input("อ้างอิงจากแบบ (ไม่บังคับ)", value="", key="ceiling_ref", help="เช่น A-07 / ห้องนั่งเล่น / C-01")

    if st.button("➕ บันทึกงานฝ้าเพดาน", type="primary", key="btn_save_ceiling"):
        area_calculated = (ceiling_w * ceiling_l * ceiling_qty)
        net_area = max(0.0, area_calculated - ceiling_deduct_area)
        proc_area = with_waste(net_area, waste_finishing)
        
        c_mat, c_lab = ceiling_price_map.get(ceiling_type, (p_ceiling_mat, labour_ceiling))
        
        add_takeoff_item({
            "หมวด": "งานฝ้าเพดาน",
            "รายการ": ceiling_name,
            "ที่มาในแบบ": ceiling_ref,
            "แหล่งข้อมูล": "จากแบบสถาปัตย์",
            "รายละเอียด": f"{ceiling_type} ขนาด {ceiling_w:.2f}x{ceiling_l:.2f}ม. ({ceiling_qty} พื้นที่) | หัก {ceiling_deduct_area:.1f} ตร.ม. | สุทธิ {net_area:.1f} ตร.ม. | จัดซื้อ {proc_area:.1f} ตร.ม.",
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
    auto_stair_geo=st.checkbox("ช่วยคำนวณความยาวเหล็กจากขนาด + Cover (ระยะหุ้มคอนกรีต)",value=True,key="auto_stair_geo")

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
        lab_c=net_vol*labour_concrete+net_rebar_weight*(get_rebar_labor_rate(stair_rebar_type, labour_rebar) if use_w480_rebar_labor else labour_rebar)+net_form*labour_formwork
        add_takeoff_item({
            "หมวด":"งานบันได","รายการ":stair_name,"ที่มาในแบบ":stair_ref,"แหล่งข้อมูล":"จากแบบ/มิติขั้นบันได",
            "รายละเอียด":f"{num_risers} ลูกตั้ง / {num_treads} ลูกนอน | ระยะวิ่ง {run_len:.2f}ม. | ระยะขึ้น {rise_len:.2f}ม. | Cover (ระยะหุ้มคอนกรีต) {cover_slab_mm:.0f}มม. | {stair_rebar_type}@{stair_rebar_spacing:.2f}",
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
        member_rows.append({"ชนิด/ขนาดเหล็ก":m["member"],"ใช้เป็นอะไร":m["desc"],"ความยาว/ชิ้น (ม.)":m["len"],"จำนวน":m["qty"],"น้ำหนักต่อเมตร (กก./ม.)":m["kg_m"],"น้ำหนักสุทธิ (กก.)":round(w,2),"น้ำหนักจัดซื้อ (กก.)":round(with_waste(w,waste_roof_steel),2)})
    if rm_to_remove:
        st.session_state["roof_members"]=[m for i,m in enumerate(st.session_state["roof_members"]) if i not in rm_to_remove];st.rerun()

    roof_steel_options=["ใช้ชนิดโครงเหล็กจากแบบ","ประเมินจากพื้นที่ (ตัวสำรอง)"]
    roof_default_index=0 if st.session_state.get("roof_members") else 1
    roof_steel_mode=st.selectbox("วิธีคำนวณน้ำหนักเหล็กโครงหลังคา",roof_steel_options,index=roof_default_index,key="roof_steel_mode")
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
        mat_roof_tiles=tile_area*mat_spec["mat"];lab_roof_tiles=real_roof_area*roof_labor_rate_w480(roof_material, roof_shape, mat_spec["lab"], use_w480_roof_labor)
        mat_ridge=ridge_len*p_roof_cap;lab_ridge=ridge_len*labour_roof_cap
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
        tot_conc=df_items["คอนกรีต (ลบ.ม.)"].sum();tot_rebar=sum(sum_rebar_breakdown(items).values());tot_roof_steel=sum_structural_steel(items);tot_form=df_items["ไม้แบบ (ตร.ม.)"].sum();tot_mat=df_items["ค่าวัสดุ (บาท)"].sum();tot_lab=df_items["ค่าแรง (บาท)"].sum()
        net_conc_total=sum(safe_num(it.get("คอนกรีตสุทธิ (ลบ.ม.)",0.0)) for it in items)
        net_steel_total=sum(safe_num(it.get("เหล็กสุทธิ (กก.)",0.0)) for it in items if it.get("หมวด")!="งานหลังคา")
        net_form_total=sum(safe_num(it.get("ไม้แบบสุทธิ (ตร.ม.)",0.0)) for it in items)
        m1,m2,m3,m4,m5=st.columns(5);m1.metric("คอนกรีตจัดซื้อ",f"{tot_conc:,.2f} ลบ.ม.",delta=f"สุทธิ {net_conc_total:,.2f}");m2.metric("เหล็กเสริมจัดซื้อ",f"{tot_rebar:,.2f} กก.",delta=f"สุทธิ {net_steel_total:,.2f}");m3.metric("โครงเหล็กหลังคา",f"{tot_roof_steel:,.2f} กก.");m4.metric("ไม้แบบจัดซื้อ",f"{tot_form:,.2f} ตร.ม.",delta=f"สุทธิ {net_form_total:,.2f}");m5.metric("ต้นทุนวัสดุ+แรง",f"฿{tot_mat+tot_lab:,.0f}")
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
            st.markdown("#### 🏗️ สรุปโครงเหล็กหลังคาแยกชนิด/ขนาดจากแบบ");st.dataframe(pd.DataFrame(roof_steel_rows),use_container_width=True,hide_index=True)

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
            pd.DataFrame([{"รายการ":"ค่าวัสดุ","บาท":subtotal_mat},{"รายการ":"ค่าแรง","บาท":subtotal_lab},{"รายการ":"ต้นทุนตรง","บาท":subtotal_direct},{"รายการ":"ดำเนินการ+กำไร","บาท":profit_amount},{"รายการ":"VAT","บาท":vat_amount},{"รายการ":"ราคาขายรวม","บาท":grand_total},{"รายการ":"ฐานราคาวัสดุ","บาท":"MOC ล่าสุดที่ตรวจสอบได้: ส.ค. 2569 / ราคาไม่รวม VAT"},{"รายการ":"ฐานค่าแรง","บาท":"ว480: 26 มิ.ย. 2569"}]).to_excel(writer,index=False,sheet_name="Price Summary")
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
        st.info("💰 **ฐานราคาปัจจุบันในระบบ:** วัสดุอ้างอิงจากข้อมูลกระทรวงพาณิชย์ที่ตรวจสอบได้ล่าสุดในระบบนี้ (ส.ค. 2569, ไม่รวม VAT) และค่าแรงตาม ว480 ลงวันที่ 26 มิ.ย. 2569 — รายการที่สเปกแตกต่างกันมาก เช่น ประตู/หน้าต่าง/เสาเข็ม ควรแก้เป็นราคาผู้ขายหรือใบเสนอราคาจริงก่อนเสนอราคา")
        q1,q2,q3,q4,q5,q6=st.columns(6)
        q1.metric("รายการทั้งหมด",len(items));q2.metric("ซ้ำตรงกัน",len(dups));q3.metric("อาจซ้ำ",len(near_dups));q4.metric("ยังไม่มีที่มาจากแบบ",len(missing_refs));q5.metric("ข้อมูลเก่า",len(legacy));q6.metric("ปริมาณประมาณการ",len(estimated))
        if dups:st.warning("พบรายการที่อาจบันทึกซ้ำ — ตรวจอ้างอิงจากแบบก่อนเสนอราคา");st.dataframe(pd.DataFrame([{"รายการ":v[0].get("รายการ",""),"ที่มาในแบบ":get_item_source(v[0]) ,"จำนวนที่พบซ้ำ":len(v)} for v in dups]),use_container_width=True,hide_index=True)
        else:st.success("✅ ไม่พบรายการซ้ำแบบตรงกัน")
        if missing_refs:st.info(f"มี {len(missing_refs)} รายการที่ยังไม่มีอ้างอิงจากแบบ — แนะนำให้ใส่แผ่น/ตำแหน่ง เพื่อการตรวจสอบย้อนหลัง")
        if estimated:st.warning(f"มี {len(estimated)} รายการที่เป็นปริมาณประมาณ — ก่อนเสนอราคาให้แทนด้วยปริมาณจากแบบเมื่อมีข้อมูลจริง")
        if negative:st.error(f"พบค่าติดลบ {len(negative)} จุด ควรตรวจข้อมูลก่อนเสนอราคา")
        tot_mat=df["ค่าวัสดุ (บาท)"].sum();tot_lab=df["ค่าแรง (บาท)"].sum();
        st.markdown("#### 🧾 ตัวเลขหลัก");
        st.dataframe(pd.DataFrame([{"ตัวชี้วัด":"คอนกรีต","ปริมาณ":df["คอนกรีต (ลบ.ม.)"].sum(),"หน่วย":"ลบ.ม."},{"ตัวชี้วัด":"เหล็กเสริม","ปริมาณ":sum(sum_rebar_breakdown(items).values()),"หน่วย":"กก."},{"ตัวชี้วัด":"โครงเหล็กหลังคา","ปริมาณ":sum_structural_steel(items),"หน่วย":"กก."},{"ตัวชี้วัด":"ไม้แบบ","ปริมาณ":df["ไม้แบบ (ตร.ม.)"].sum(),"หน่วย":"ตร.ม."},{"ตัวชี้วัด":"ต้นทุนตรง","ปริมาณ":tot_mat+tot_lab,"หน่วย":"บาท"}]),use_container_width=True,hide_index=True)
