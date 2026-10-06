import streamlit as st
import pandas as pd
import math
import io
import json
import os

# ---------------------------------------------------------
# 1. Page Configuration & Custom CSS
# ---------------------------------------------------------
st.set_page_config(
    page_title="AI ถอด BOQ งานโครงสร้าง & สถาปัตย์ V6.8 (Accuracy Audited)",
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
    "RB9": 0.499,
    "DB12": 0.888,
    "DB16": 1.580,
    "DB20": 2.470,
    "DB25": 3.850,
    "DB28": 4.830,
    "DB32": 6.310
}
REBAR_LIST = list(REBAR_WEIGHT.keys())

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
ROOF_SHAPE_FACTORS = {
    "หลังคาเพิงหมางื่น (Lean-to)": 1.05,
    "หลังคาจั่ว (Gable)": 1.12,
    "หลังคาปั้นหยา (Hip)": 1.18,
    "หลังคาปั้นหยาผสมจั่ว (Manson/Dutch)": 1.20,
    "หลังคาหมางื่นซ้อนชั้น (Modern Lean-to)": 1.08,
    "หลังคาดาดฟ้า/สแลป (Flat Slab/Concrete)": 1.00
}

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

def add_takeoff_item(item_data):
    p_idx = get_current_project_index()
    if p_idx != -1:
        if "items" not in st.session_state["projects"][p_idx]:
            st.session_state["projects"][p_idx]["items"] = []
        item = dict(item_data)
        item.setdefault("คอนกรีตสุทธิ (ลบ.ม.)", safe_num(item.get("คอนกรีต (ลบ.ม.)", 0.0)))
        item.setdefault("เหล็กสุทธิ (กก.)", safe_num(item.get("เหล็ก (กก.)", 0.0)))
        item.setdefault("ไม้แบบสุทธิ (ตร.ม.)", safe_num(item.get("ไม้แบบ (ตร.ม.)", 0.0)))
        item["ข้อมูลปริมาณสุทธิแยกจาก Waste"] = True
        item["รวมเงิน (บาท)"] = round(
            safe_num(item.get("ค่าวัสดุ (บาท)", 0.0)) + safe_num(item.get("ค่าแรง (บาท)", 0.0)), 2
        )
        st.session_state["projects"][p_idx]["items"].append(item)
        save_projects()  # บันทึกลงดิสก์อัตโนมัติ
        st.success(f"บันทึกรายการ '{item.get('รายการ', 'ไม่ระบุ')}' เรียบร้อยแล้ว!")
    else:
        st.error("⚠ กรุณาสร้างหรือเลือกโครงการก่อนทำการบันทึกข้อมูล!")

# ---------------------------------------------------------
# 4. Sidebar Price & Material Settings
# ---------------------------------------------------------
with st.sidebar:
    st.title("⚙ ตั้งค่าราคาและค่าแรง")
    
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
        p_db12 = st.number_input("เหล็ก DB12/DB16/DB20/DB25/DB28/DB32 (บาท/กก.)", min_value=0.0, value=31.0, step=0.5)
        p_rb9 = st.number_input("เหล็ก RB6/RB9/โครงสร้าง (บาท/กก.)", min_value=0.0, value=33.0, step=0.5)
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
        labour_rebar = st.number_input("ค่าแรงผูกเหล็ก/โครงเหล็ก (บาท/กก.)", min_value=0.0, value=8.5, step=0.5)
        labour_formwork = st.number_input("ค่าแรงประกอบไม้แบบ (บาท/ตร.ม.)", min_value=0.0, value=150.0, step=10.0)

    with st.expander("📉 เปอร์เซ็นต์สูญเสีย (% Wastage)", expanded=False):
        waste_concrete = st.number_input("เผื่อคอนกรีต (%)", min_value=0.0, max_value=100.0, value=5.0) / 100.0
        waste_rebar = st.number_input("เผื่อเหล็กเส้น/โครงสร้าง (%)", min_value=0.0, max_value=100.0, value=10.0) / 100.0
        waste_formwork = st.number_input("เผื่อไม้แบบ (%)", min_value=0.0, max_value=100.0, value=15.0) / 100.0
        waste_wall = st.number_input("เผื่ออิฐ/ปูนฉาบ (%)", min_value=0.0, max_value=100.0, value=5.0) / 100.0
        waste_roof = st.number_input("เผื่อหลังคา (%)", min_value=0.0, max_value=100.0, value=7.0) / 100.0
        waste_finishing = st.number_input("เผื่อกระเบื้อง/ฝ้า (%)", min_value=0.0, max_value=100.0, value=5.0) / 100.0

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
    <div class="header-title">⚙️ ระบบถอดปริมาณงานโครงสร้าง & สถาปัตย์ (Takeoff V6.8)</div>
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

# =========================================================
# TAB 2: 🦶 ฐานราก
# =========================================================
with tabs[1]:
    st.subheader(f"🦶 ถอดปริมาณงานฐานราก — [{active_proj_name}]")
    
    f1, f2, f3 = st.columns([1.5, 1, 1.5])
    f_name = f1.text_input("ชื่อ/สัญลักษณ์ฐานราก", value="F1", key="f_name")
    f_qty = f2.number_input("จำนวน (ฐาน)", min_value=1, value=1, key="f_qty")
    f_type = f3.radio("ประเภทฐานราก", ["ฐานรากแผ่ (Shallow)", "ฐานรากมีเสาเข็ม (Piled)"], key="f_type")

    m1, m2, m3, m4 = st.columns(4)
    f_w = m1.number_input("ความกว้างฐานราก (เมตร)", min_value=0.05, value=1.20, step=0.1, key="f_w")
    f_l = m2.number_input("ความยาวฐานราก (เมตร)", min_value=0.05, value=1.20, step=0.1, key="f_l")
    f_h = m3.number_input("ความหนาฐานราก (เมตร)", min_value=0.05, value=0.35, step=0.05, key="f_h")
    f_depth = m4.number_input("ระดับความลึกดินขุด H (เมตร)", min_value=0.05, value=1.50, step=0.1, key="f_depth")

    ex1, ex2 = st.columns(2)
    f_work_space = ex1.number_input("ระยะเผื่อพื้นที่ทำงานรอบหลุมขุด / ด้าน (เมตร)", min_value=0.0, value=0.30, step=0.05, key="f_work_space")
    f_form_type = ex2.selectbox("แบบหล่อด้านข้างฐานราก", ["เทชิดดิน / ไม่คิดไม้แบบข้าง", "มีไม้แบบข้างฐานราก"], key="f_form_type")

    if "เสาเข็ม" in f_type:
        st.markdown("#### 📌 รายละเอียดเสาเข็ม")
        pk1, pk2, pk3 = st.columns(3)
        pile_type = pk1.selectbox("ชนิดเสาเข็ม", list(pile_price_map.keys()), key="pile_type")
        pile_len = pk2.number_input("ความยาวเสาเข็มต่อต้น (เมตร)", value=6.0, step=0.5, key="pile_len")
        piles_per_footing = pk3.number_input("จำนวนเสาเข็มต่อ 1 ฐานราก (ต้น)", min_value=1, value=1, key="piles_per_footing")

    st.markdown("---")
    head_col, btn_col = st.columns([3, 1])
    head_col.markdown("#### 🥞 เหล็กเสริมฐานราก")
    if btn_col.button("➕ เพิ่มรายการเหล็ก", key="btn_add_f_rebar"):
        st.session_state["footing_rebars"].append({"pos": "เหล็กวิ่งตามยาว", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 10.0, "len": 1.50})
        st.rerun()

    f_rebars_to_remove = []
    tot_footing_rebar_weight = 0.0
    footing_rebar_detail = {}

    for idx, r in enumerate(st.session_state["footing_rebars"]):
        c1, c2, c3, c4, c5, c6 = st.columns([1.5, 1.5, 2, 2, 2, 0.5])
        footing_pos_options = ["เหล็กวิ่งตามยาว", "เหล็กวิ่งตามกว้าง", "เหล็กเสริมพิเศษ"]
        r["pos"] = c1.selectbox(f"แนวเหล็ก #{idx+1}", footing_pos_options, index=footing_pos_options.index(r.get("pos", "เหล็กวิ่งตามยาว")) if r.get("pos", "เหล็กวิ่งตามยาว") in footing_pos_options else 0, key=f"f_pos_{idx}")
        r["type"] = c2.selectbox(f"ชนิดเหล็ก #{idx+1}", REBAR_LIST, index=REBAR_LIST.index(r["type"]) if r["type"] in REBAR_LIST else 2, key=f"f_type_{idx}")
        r["mode"] = c3.selectbox(f"จำนวน/ระยะห่าง #{idx+1}", ["จำนวน (เส้น)", "ระยะห่าง (@ ม.)"], index=0 if r["mode"] == "จำนวน (เส้น)" else 1, key=f"f_mode_{idx}")
        r["val"] = c4.number_input(f"ค่า #{idx+1}", value=float(r["val"]), key=f"f_val_{idx}")
        r["len"] = c5.number_input(f"ยาว (ม.) #{idx+1}", value=float(r["len"]), key=f"f_len_{idx}")
        
        if c6.button("🗑", key=f"del_f_rebar_{idx}"):
            f_rebars_to_remove.append(idx)

        if r["mode"] == "จำนวน (เส้น)":
            total_len_row = r["val"] * r["len"]
        else:
            count_span = f_w if "ตามยาว" in r.get("pos", "") else f_l
            calc_count = count_by_spacing(count_span, r["val"])
            total_len_row = calc_count * r["len"]
        
        w_row = total_len_row * REBAR_WEIGHT[r["type"]]
        tot_footing_rebar_weight += w_row
        footing_rebar_detail[r["type"]] = footing_rebar_detail.get(r["type"], 0.0) + w_row

    if f_rebars_to_remove:
        st.session_state["footing_rebars"] = [item for i, item in enumerate(st.session_state["footing_rebars"]) if i not in f_rebars_to_remove]
        st.rerun()

    st.markdown("---")
    if st.button("➕ บันทึกงานฐานราก", type="primary", key="btn_save_footing"):
        net_concrete = f_w * f_l * f_h * f_qty
        vol_concrete = with_waste(net_concrete, waste_concrete)
        net_formwork = (2 * (f_w + f_l) * f_h * f_qty) if "มีไม้แบบ" in f_form_type else 0.0
        formwork = with_waste(net_formwork, waste_formwork)
        net_rebar_weight = tot_footing_rebar_weight * f_qty
        rebar_weight = with_waste(net_rebar_weight, waste_rebar)

        excavation_area_per_footing = (f_w + 2 * f_work_space) * (f_l + 2 * f_work_space)
        vol_excavation = excavation_area_per_footing * f_depth * f_qty
        vol_backfill = max(0.0, vol_excavation - net_concrete)

        rebar_breakdown = {k: round(with_waste(v * f_qty, waste_rebar), 2) for k, v in footing_rebar_detail.items()}
        mat_c = vol_concrete * p_concrete + sum(v * get_rebar_price(k, p_db12, p_rb9) for k, v in rebar_breakdown.items()) + formwork * p_formwork
        lab_c = net_concrete * labour_concrete + net_rebar_weight * labour_rebar + net_formwork * labour_formwork

        add_takeoff_item({
            "หมวด": "งานดินขุด-ดินถม",
            "รายการ": f"งานดินสำหรับฐานราก {f_name}",
            "รายละเอียด": f"ดินขุดเผื่อพื้นที่ทำงานด้านละ {f_work_space:.2f}ม.: {vol_excavation:.2f} ลบ.ม. | ดินถมย้อนกลับ: {vol_backfill:.2f} ลบ.ม.",
            "จำนวน": f_qty,
            "คอนกรีต (ลบ.ม.)": 0.0,
            "เหล็ก (กก.)": 0.0,
            "ไม้แบบ (ตร.ม.)": 0.0,
            "ดินขุด (ลบ.ม.)": round(vol_excavation, 2),
            "ดินถม (ลบ.ม.)": round(vol_backfill, 2),
            "ค่าวัสดุ (บาท)": 0.0,
            "ค่าแรง (บาท)": round((vol_excavation * cost_excavation) + (vol_backfill * cost_backfill), 2)
        })

        detail_str = f"ขนาด {f_w:.2f}x{f_l:.2f}x{f_h:.2f} ม. (ลึก {f_depth:.2f}ม.)"
        if "เสาเข็ม" in f_type:
            total_piles = piles_per_footing * f_qty
            total_pile_length = total_piles * pile_len
            p_mat_rate, p_lab_rate = pile_price_map.get(pile_type, (0.0, 0.0))
            
            add_takeoff_item({
                "หมวด": "งานเสาเข็ม",
                "รายการ": f"เสาเข็มรองรับ {f_name}",
                "รายละเอียด": f"{pile_type} ยาว {pile_len:.1f}ม. ({total_piles} ต้น / {total_pile_length:.1f} ม.)",
                "จำนวน": total_piles,
                "คอนกรีต (ลบ.ม.)": 0.0,
                "เหล็ก (กก.)": 0.0,
                "ไม้แบบ (ตร.ม.)": 0.0,
                "ค่าวัสดุ (บาท)": round(total_pile_length * p_mat_rate, 2),
                "ค่าแรง (บาท)": round(total_pile_length * p_lab_rate, 2)
            })
            detail_str += f" | {pile_type} ({total_piles} ต้น)"

        add_takeoff_item({
            "หมวด": "งานฐานราก",
            "รายการ": f_name,
            "รายละเอียด": detail_str,
            "จำนวน": f_qty,
            "คอนกรีตสุทธิ (ลบ.ม.)": round(net_concrete, 2),
            "คอนกรีต (ลบ.ม.)": round(vol_concrete, 2),
            "เหล็กสุทธิ (กก.)": round(net_rebar_weight, 2),
            "เหล็ก (กก.)": round(rebar_weight, 2),
            "เหล็กแยกชนิด": rebar_breakdown,
            "ไม้แบบสุทธิ (ตร.ม.)": round(net_formwork, 2),
            "ไม้แบบ (ตร.ม.)": round(formwork, 2),
            "ค่าวัสดุ (บาท)": round(mat_c, 2),
            "ค่าแรง (บาท)": round(lab_c, 2)
        })

# =========================================================
# TAB 3: 🏛 เสา
# =========================================================
with tabs[2]:
    st.subheader(f"🏛️ ถอดปริมาณงานเสา — [{active_proj_name}]")
    
    c1, c2, c3 = st.columns([1.5, 1.5, 1])
    col_name = c1.text_input("ชื่อ/สัญลักษณ์เสา", value="C1", key="col_name")
    col_level = c2.selectbox("ตำแหน่ง/ชั้นของเสา", ["เสาตอม่อ (Stub Column)", "เสาชั้น 1", "เสาชั้น 2", "เสาชั้น 3", "เสาชั้นหลังคา"], key="col_level")
    col_qty = c3.number_input("จำนวน (ต้น)", min_value=1, value=1, key="col_qty")
    
    cm1, cm2, cm3 = st.columns(3)
    col_w = cm1.number_input("กว้างเสา (เมตร)", min_value=0.05, value=0.20, step=0.05, key="col_w")
    col_l = cm2.number_input("ยาวเสา (เมตร)", min_value=0.05, value=0.20, step=0.05, key="col_l")
    col_h = cm3.number_input("ความสูงเสา (เมตร)", min_value=0.05, value=3.00, step=0.10, key="col_h")

    st.markdown("---")
    head_c, btn_c = st.columns([3, 1])
    head_c.markdown("#### 🥞 เหล็กเสริมเสา")
    
    default_stirrup_len = round(2 * (col_w + col_l) + 0.15, 2)
    default_main_len = round(col_h + 0.60, 2)

    if btn_c.button("➕ เพิ่มเหล็กเสา", key="btn_add_col_rebar"):
        st.session_state["column_rebars"].append({"pos": "เหล็กแกน", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 4.0, "len": default_main_len})
        st.rerun()

    c_rebars_to_remove = []
    tot_col_rebar_weight = 0.0
    col_rebar_detail = {}
    col_pos_options = ["เหล็กแกน", "เหล็กปลอก", "เหล็กเสริมพิเศษ"]

    for idx, r in enumerate(st.session_state["column_rebars"]):
        c1, c2, c3, c4, c5, c6 = st.columns([1.5, 1.2, 1.8, 1.5, 1.5, 0.5])
        
        pos_idx = col_pos_options.index(r["pos"]) if r["pos"] in col_pos_options else 0
        r["pos"] = c1.selectbox(f"ตำแหน่ง #{idx+1}", col_pos_options, index=pos_idx, key=f"c_pos_{idx}")
        
        type_idx = REBAR_LIST.index(r["type"]) if r["type"] in REBAR_LIST else 2
        r["type"] = c2.selectbox(f"เหล็ก #{idx+1}", REBAR_LIST, index=type_idx, key=f"c_type_{idx}")
        
        mode_idx = 0 if r["mode"] == "จำนวน (เส้น)" else 1
        r["mode"] = c3.selectbox(f"จำนวน/ระยะห่าง #{idx+1}", ["จำนวน (เส้น)", "ระยะห่าง (@ ม.)"], index=mode_idx, key=f"c_mode_{idx}")
        
        r["val"] = c4.number_input(f"ค่า #{idx+1}", min_value=0.0001, value=max(float(r["val"]), 0.0001), key=f"c_val_{idx}")
        
        if r["pos"] == "เหล็กปลอก" and r["len"] < 0.2:
            r["len"] = default_stirrup_len

        r["len"] = c5.number_input(f"ยาว (ม.) #{idx+1}", value=float(r["len"]), key=f"c_len_{idx}")
        
        if c6.button("🗑", key=f"del_c_rebar_{idx}"):
            c_rebars_to_remove.append(idx)

        if r["mode"] == "จำนวน (เส้น)":
            total_len_row = r["val"] * r["len"]
        else:
            calc_count = count_by_spacing(col_h, r["val"])
            total_len_row = calc_count * r["len"]
        
        w_row = total_len_row * REBAR_WEIGHT[r["type"]]
        tot_col_rebar_weight += w_row
        col_rebar_detail[r["type"]] = col_rebar_detail.get(r["type"], 0.0) + w_row

    if c_rebars_to_remove:
        st.session_state["column_rebars"] = [item for i, item in enumerate(st.session_state["column_rebars"]) if i not in c_rebars_to_remove]
        st.rerun()

    st.markdown("---")
    if st.button("➕ บันทึกงานเสา", type="primary", key="btn_save_col"):
        net_concrete = col_w * col_l * col_h * col_qty
        vol = with_waste(net_concrete, waste_concrete)
        net_form = 2 * (col_w + col_l) * col_h * col_qty
        form = with_waste(net_form, waste_formwork)
        net_rebar_weight = tot_col_rebar_weight * col_qty
        rebar_weight = with_waste(net_rebar_weight, waste_rebar)

        rebar_breakdown = {k: round(with_waste(v * col_qty, waste_rebar), 2) for k, v in col_rebar_detail.items()}
        mat_c = vol * p_concrete + sum(v * get_rebar_price(k, p_db12, p_rb9) for k, v in rebar_breakdown.items()) + form * p_formwork
        lab_c = net_concrete * labour_concrete + net_rebar_weight * labour_rebar + net_form * labour_formwork

        add_takeoff_item({
            "หมวด": "งานเสา",
            "รายการ": f"{col_name} ({col_level})",
            "รายละเอียด": f"[{col_level}] ขนาด {col_w:.2f}x{col_l:.2f}ม. สูง {col_h:.2f}ม. ({col_qty} ต้น)",
            "จำนวน": col_qty,
            "คอนกรีตสุทธิ (ลบ.ม.)": round(net_concrete, 2),
            "คอนกรีต (ลบ.ม.)": round(vol, 2),
            "เหล็กสุทธิ (กก.)": round(net_rebar_weight, 2),
            "เหล็ก (กก.)": round(rebar_weight, 2),
            "เหล็กแยกชนิด": rebar_breakdown,
            "ไม้แบบสุทธิ (ตร.ม.)": round(net_form, 2),
            "ไม้แบบ (ตร.ม.)": round(form, 2),
            "ค่าวัสดุ (บาท)": round(mat_c, 2),
            "ค่าแรง (บาท)": round(lab_c, 2)
        })

# =========================================================
# TAB 4: ↔ คาน
# =========================================================
with tabs[3]:
    st.subheader(f"↔️ ถอดปริมาณงานคาน — [{active_proj_name}]")
    
    b1, b2, b3 = st.columns([1.5, 1.5, 1])
    beam_name = b1.text_input("ชื่อ/สัญลักษณ์คาน", value="B1", key="b_name")
    beam_level = b2.selectbox("ตำแหน่ง/ระดับชั้นของคาน", ["คานคอดิน (GB)", "คานชั้น 1 (B1)", "คานชั้น 2 (B2)", "คานชั้น 3 (B3)", "คานหลังคา (RB)"], key="beam_level")
    beam_qty = b3.number_input("จำนวน (คาน)", min_value=1, value=1, key="b_qty")
    
    bm1, bm2, bm3 = st.columns(3)
    beam_w = bm1.number_input("ความกว้างคาน (เมตร)", min_value=0.05, value=0.20, step=0.05, key="b_w")
    beam_h = bm2.number_input("ความลึก/สูงคาน (เมตร)", min_value=0.05, value=0.40, step=0.05, key="b_h")
    beam_l = bm3.number_input("ความยาวคาน (เมตร)", min_value=0.05, value=4.00, step=0.10, key="b_l")

    st.markdown("---")
    head_b, btn_b = st.columns([3, 1])
    head_b.markdown("#### 🥞 เหล็กเสริมคาน")
    
    default_beam_stirrup_len = round(2 * (beam_w + beam_h) + 0.15, 2)
    default_beam_main_len = round(beam_l + 0.60, 2)

    if btn_b.button("➕ เพิ่มเหล็กคาน", key="btn_add_beam_rebar"):
        st.session_state["beam_rebars"].append({"pos": "เหล็กบน", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 2.0, "len": default_beam_main_len})
        st.rerun()

    b_rebars_to_remove = []
    tot_beam_rebar_weight = 0.0
    beam_rebar_detail = {}
    beam_pos_options = ["เหล็กบน", "เหล็กล่าง", "เหล็กเสริมพิเศษ", "เหล็กปลอก"]

    for idx, r in enumerate(st.session_state["beam_rebars"]):
        c1, c2, c3, c4, c5, c6 = st.columns([1.5, 1.2, 1.8, 1.5, 1.5, 0.5])
        
        pos_idx = beam_pos_options.index(r["pos"]) if r["pos"] in beam_pos_options else 0
        r["pos"] = c1.selectbox(f"ตำแหน่ง #{idx+1}", beam_pos_options, index=pos_idx, key=f"b_pos_{idx}")
        
        type_idx = REBAR_LIST.index(r["type"]) if r["type"] in REBAR_LIST else 2
        r["type"] = c2.selectbox(f"เหล็ก #{idx+1}", REBAR_LIST, index=type_idx, key=f"b_type_{idx}")
        
        mode_idx = 0 if r["mode"] == "จำนวน (เส้น)" else 1
        r["mode"] = c3.selectbox(f"จำนวน/ระยะห่าง #{idx+1}", ["จำนวน (เส้น)", "ระยะห่าง (@ ม.)"], index=mode_idx, key=f"b_mode_{idx}")
        
        r["val"] = c4.number_input(f"ค่า #{idx+1}", min_value=0.0001, value=max(float(r["val"]), 0.0001), key=f"b_val_{idx}")
        
        if r["pos"] == "เหล็กปลอก" and r["len"] < 0.2:
            r["len"] = default_beam_stirrup_len

        r["len"] = c5.number_input(f"ยาว (ม.) #{idx+1}", value=float(r["len"]), key=f"b_len_{idx}")
        
        if c6.button("🗑", key=f"del_b_rebar_{idx}"):
            b_rebars_to_remove.append(idx)

        if r["mode"] == "จำนวน (เส้น)":
            total_len_row = r["val"] * r["len"]
        else:
            calc_count = count_by_spacing(beam_l, r["val"])
            total_len_row = calc_count * r["len"]
        
        w_row = total_len_row * REBAR_WEIGHT[r["type"]]
        tot_beam_rebar_weight += w_row
        beam_rebar_detail[r["type"]] = beam_rebar_detail.get(r["type"], 0.0) + w_row

    if b_rebars_to_remove:
        st.session_state["beam_rebars"] = [item for i, item in enumerate(st.session_state["beam_rebars"]) if i not in b_rebars_to_remove]
        st.rerun()

    st.markdown("---")
    if st.button("➕ บันทึกงานคาน", type="primary", key="btn_save_beam"):
        net_concrete = beam_w * beam_h * beam_l * beam_qty
        vol = with_waste(net_concrete, waste_concrete)
        net_form = (2 * beam_h + beam_w) * beam_l * beam_qty
        form = with_waste(net_form, waste_formwork)
        net_rebar_weight = tot_beam_rebar_weight * beam_qty
        rebar_weight = with_waste(net_rebar_weight, waste_rebar)

        rebar_breakdown = {k: round(with_waste(v * beam_qty, waste_rebar), 2) for k, v in beam_rebar_detail.items()}
        mat_c = vol * p_concrete + sum(v * get_rebar_price(k, p_db12, p_rb9) for k, v in rebar_breakdown.items()) + form * p_formwork
        lab_c = net_concrete * labour_concrete + net_rebar_weight * labour_rebar + net_form * labour_formwork

        add_takeoff_item({
            "หมวด": "งานคาน",
            "รายการ": f"{beam_name} ({beam_level})",
            "รายละเอียด": f"[{beam_level}] ขนาด {beam_w:.2f}x{beam_h:.2f}ม. ยาว {beam_l:.2f}ม. ({beam_qty} คาน)",
            "จำนวน": beam_qty,
            "คอนกรีตสุทธิ (ลบ.ม.)": round(net_concrete, 2),
            "คอนกรีต (ลบ.ม.)": round(vol, 2),
            "เหล็กสุทธิ (กก.)": round(net_rebar_weight, 2),
            "เหล็ก (กก.)": round(rebar_weight, 2),
            "เหล็กแยกชนิด": rebar_breakdown,
            "ไม้แบบสุทธิ (ตร.ม.)": round(net_form, 2),
            "ไม้แบบ (ตร.ม.)": round(form, 2),
            "ค่าวัสดุ (บาท)": round(mat_c, 2),
            "ค่าแรง (บาท)": round(lab_c, 2)
        })

# =========================================================
# TAB 5: 🧱 พื้น
# =========================================================
with tabs[4]:
    st.subheader(f"🧱 ถอดปริมาณงานพื้น — [{active_proj_name}]")
    s1, s2 = st.columns(2)
    slab_name = s1.text_input("ชื่อ/สัญลักษณ์พื้น", value="S1", key="s_name")
    slab_qty = s2.number_input("จำนวน (ผืน)", min_value=1, value=1, key="s_qty")
    
    sm1, sm2, sm3 = st.columns(3)
    slab_w = sm1.number_input("ความกว้างพื้น (เมตร)", min_value=0.05, value=3.00, step=0.10, key="s_w")
    slab_l = sm2.number_input("ความยาวพื้น (เมตร)", min_value=0.05, value=4.00, step=0.10, key="s_l")
    slab_h = sm3.number_input("ความหนาพื้น (เมตร)", min_value=0.03, value=0.10, step=0.01, key="s_h")
    slab_support = st.selectbox(
        "ลักษณะพื้น",
        ["พื้นยก/พื้น คสล. มีแบบหล่อใต้ท้องพื้น", "พื้นวางบนดิน (Slab on Ground)"],
        key="s_support"
    )
    slab_openings = st.number_input("หักพื้นที่ช่องเปิดรวม (ตร.ม.)", min_value=0.0, value=0.0, step=0.10, key="s_openings")

    st.markdown("---")
    head_s, btn_s = st.columns([3, 1])
    head_s.markdown("#### 🥞 เหล็กเสริมพื้น (สามารถเพิ่มรายการเหล็กเสริมได้หลายชั้น)")

    if btn_s.button("➕ เพิ่มรายการเหล็กพื้น", key="btn_add_s_rebar"):
        st.session_state["slab_rebars"].append({"pos": "เหล็กเสริม", "type": "RB9", "mode": "ระยะห่าง (@ ม.)", "val": 0.20, "len": slab_l})
        st.rerun()

    s_rebars_to_remove = []
    tot_slab_rebar_weight = 0.0
    slab_rebar_detail = {}
    slab_pos_options = [
        "เหล็กล่าง/ตะแกรงทางยาว", 
        "เหล็กล่าง/ตะแกรงทางกว้าง", 
        "เหล็กบน/ตะแกรงทางยาว", 
        "เหล็กบน/ตะแกรงทางกว้าง", 
        "เหล็กคอมเมนท์/เหล็กเสริมพิเศษ"
    ]

    for idx, r in enumerate(st.session_state["slab_rebars"]):
        c1, c2, c3, c4, c5, c6 = st.columns([1.5, 1.2, 1.8, 1.5, 1.5, 0.5])
        
        pos_idx = slab_pos_options.index(r["pos"]) if r["pos"] in slab_pos_options else 0
        r["pos"] = c1.selectbox(f"ตำแหน่ง #{idx+1}", slab_pos_options, index=pos_idx, key=f"s_pos_{idx}")
        
        type_idx = REBAR_LIST.index(r["type"]) if r["type"] in REBAR_LIST else 1
        r["type"] = c2.selectbox(f"เหล็ก #{idx+1}", REBAR_LIST, index=type_idx, key=f"s_type_{idx}")
        
        mode_idx = 0 if r["mode"] == "จำนวน (เส้น)" else 1
        r["mode"] = c3.selectbox(f"จำนวน/ระยะห่าง #{idx+1}", ["จำนวน (เส้น)", "ระยะห่าง (@ ม.)"], index=mode_idx, key=f"s_mode_{idx}")
        
        r["val"] = c4.number_input(f"ค่า #{idx+1}", min_value=0.0001, value=max(float(r["val"]), 0.0001), key=f"s_val_{idx}")
        
        if r["len"] <= 0:
            r["len"] = slab_l

        r["len"] = c5.number_input(f"ยาว (ม.) #{idx+1}", value=float(r["len"]), key=f"s_len_{idx}")
        
        if c6.button("🗑", key=f"del_s_rebar_{idx}"):
            s_rebars_to_remove.append(idx)

        if r["mode"] == "จำนวน (เส้น)":
            total_len_row = r["val"] * r["len"]
        else:
            # ทางยาวนับตามความกว้าง, ทางกว้างนับตามความยาว
            count_span = slab_w if "ทางยาว" in r["pos"] else slab_l
            calc_count = count_by_spacing(count_span, r["val"])
            total_len_row = calc_count * r["len"]
        
        w_row = total_len_row * REBAR_WEIGHT[r["type"]]
        tot_slab_rebar_weight += w_row
        slab_rebar_detail[r["type"]] = slab_rebar_detail.get(r["type"], 0.0) + w_row

    if s_rebars_to_remove:
        st.session_state["slab_rebars"] = [item for i, item in enumerate(st.session_state["slab_rebars"]) if i not in s_rebars_to_remove]
        st.rerun()

    st.markdown("---")
    if st.button("➕ บันทึกงานพื้น", type="primary", key="btn_save_slab"):
        gross_area = slab_w * slab_l * slab_qty
        net_area = max(0.0, gross_area - slab_openings)
        net_concrete = net_area * slab_h
        vol = with_waste(net_concrete, waste_concrete)
        net_form_base = net_area if "พื้นยก" in slab_support else 0.0
        net_form = net_form_base
        form = with_waste(net_form, waste_formwork)
        net_rebar_weight = tot_slab_rebar_weight * slab_qty
        rebar_weight = with_waste(net_rebar_weight, waste_rebar)

        tot_rebar_mat_cost = 0.0
        rebar_breakdown = {}

        for r_type, w_base in slab_rebar_detail.items():
            w_tot = with_waste(w_base * slab_qty, waste_rebar)
            rebar_breakdown[r_type] = round(w_tot, 2)
            tot_rebar_mat_cost += w_tot * get_rebar_price(r_type, p_db12, p_rb9)

        mat_c = vol * p_concrete + tot_rebar_mat_cost + form * p_formwork
        lab_c = net_concrete * labour_concrete + net_rebar_weight * labour_rebar + net_form * labour_formwork

        rebar_desc = ", ".join([f"{k}: {v:.1f} กก." for k, v in rebar_breakdown.items()]) if rebar_breakdown else "ไม่ใส่เหล็กเสริม"

        add_takeoff_item({
            "หมวด": "งานพื้น",
            "รายการ": slab_name,
            "รายละเอียด": f"ขนาด {slab_w:.2f}x{slab_l:.2f}ม. หนา {slab_h:.2f}ม. ({slab_qty} ผืน) | {slab_support} | หักช่องเปิด {slab_openings:.2f} ตร.ม. | เหล็กเสริม: {rebar_desc}",
            "จำนวน": slab_qty,
            "คอนกรีตสุทธิ (ลบ.ม.)": round(net_concrete, 2),
            "คอนกรีต (ลบ.ม.)": round(vol, 2),
            "เหล็กสุทธิ (กก.)": round(net_rebar_weight, 2),
            "เหล็ก (กก.)": round(rebar_weight, 2),
            "เหล็กแยกชนิด": rebar_breakdown,
            "ไม้แบบสุทธิ (ตร.ม.)": round(net_form, 2),
            "ไม้แบบ (ตร.ม.)": round(form, 2),
            "ค่าวัสดุ (บาท)": round(mat_c, 2),
            "ค่าแรง (บาท)": round(lab_c, 2)
        })

# =========================================================
# TAB 6: 🧱 ผนัง & ตกแต่ง
# =========================================================
with tabs[5]:
    st.subheader(f"🧱 ถอดปริมาณงานผนัง ประตู-หน้าต่าง และพื้นผิวตกแต่ง — [{active_proj_name}]")
    
    with st.expander("🧱 1. งานก่ออิฐ - ฉาบปูน - เสาเอ็น-คานทับหลัง", expanded=True):
        w1, w2, w3 = st.columns([1.5, 2.0, 1])
        wall_name = w1.text_input("ชื่อ/สัญลักษณ์ผนัง", value="W1", key="wall_name")
        brick_type = w2.selectbox("ประเภทอิฐ/วัสดุก่อ", list(brick_price_map.keys()), key="brick_type")
        wall_qty = w3.number_input("จำนวนผนังชุดนี้ (ผืน)", min_value=1, value=1, key="wall_qty")

        wm1, wm2 = st.columns(2)
        wall_l = wm1.number_input("ความยาวผนัง (เมตร)", min_value=0.05, value=4.00, step=0.10, key="wall_l")
        wall_h = wm2.number_input("ความสูงผนัง (เมตร)", min_value=0.05, value=2.80, step=0.10, key="wall_h")

        st.markdown("**🚪 ช่องเปิดเพื่อหักพื้นที่ (ประตู / หน้าต่าง)**")
        d1, d2, d3 = st.columns(3)
        deduct_w = d1.number_input("ความกว้างต่อช่องเปิด (เมตร)", value=0.90, step=0.1, key="deduct_w")
        deduct_h = d2.number_input("ความสูงต่อช่องเปิด (เมตร)", value=2.00, step=0.1, key="deduct_h")
        deduct_qty = d3.number_input("จำนวนช่องเปิด (ช่อง)", min_value=0, value=1, key="deduct_qty")

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
            deduct_area = (deduct_w * deduct_h * deduct_qty) * wall_qty
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
            opening_lintel_len = (deduct_w * deduct_qty) if deduct_qty > 0 else 0.0
            if lintel_mode == "ระบุความยาวรวมจากแบบ":
                tot_lintel_len = lintel_extra_len
            else:
                tot_lintel_len = (opening_lintel_len + lintel_extra_len) * wall_qty

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

        dw_c1, dw_c2 = st.columns(2)
        cost_per_set_mat = dw_c1.number_input("ราคาชุดบาน+วงกบ+อุปกรณ์ (บาท/ชุด)", value=3500.0, step=100.0, key="cost_per_set_mat")
        cost_per_set_lab = dw_c2.number_input("ค่าแรงติดตั้ง (บาท/ชุด)", value=500.0, step=50.0, key="cost_per_set_lab")

        if st.button("➕ บันทึกงานประตู-หน้าต่าง", type="primary", key="btn_save_dw"):
            add_takeoff_item({
                "หมวด": "งานประตู-หน้าต่าง",
                "รายการ": dw_name,
                "รายละเอียด": f"{dw_type} ({dw_qty} ชุด)",
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
        floor_w = fl_dim1.number_input("ความกว้าง (เมตร)", value=5.00, step=0.10, key="floor_w")
        floor_l = fl_dim2.number_input("ความยาว (เมตร)", value=7.00, step=0.10, key="floor_l")
        
        floor_material_type = fl_type_col.selectbox("ประเภทวัสดุปูพื้น", list(floor_price_map.keys()), key="floor_material_type")

        if st.button("➕ บันทึกงานปูพื้น", type="primary", key="btn_save_floor"):
            area_calculated = (floor_w * floor_l * floor_qty)
            net_area = area_calculated
            proc_area = with_waste(net_area, waste_finishing)
            
            p_mat, p_lab = floor_price_map.get(floor_material_type, (p_tile_mat, labour_tile))
            
            add_takeoff_item({
                "หมวด": "งานปูพื้นและตกแต่งผิว",
                "รายการ": floor_name,
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
    ceiling_w = cl_dim1.number_input("ความกว้างฝ้า (เมตร)", value=5.00, step=0.10, key="ceiling_w")
    ceiling_l = cl_dim2.number_input("ความยาวฝ้า (เมตร)", value=8.00, step=0.10, key="ceiling_l")
    
    ceiling_type = cl_type_col.selectbox("ประเภทฝ้าเพดาน", list(ceiling_price_map.keys()), key="ceiling_type")

    if st.button("➕ บันทึกงานฝ้าเพดาน", type="primary", key="btn_save_ceiling"):
        area_calculated = (ceiling_w * ceiling_l * ceiling_qty)
        net_area = area_calculated
        proc_area = with_waste(net_area, waste_finishing)
        
        c_mat, c_lab = ceiling_price_map.get(ceiling_type, (p_ceiling_mat, labour_ceiling))
        
        add_takeoff_item({
            "หมวด": "งานฝ้าเพดาน",
            "รายการ": ceiling_name,
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
    st.subheader(f"🪜 ถอดปริมาณงานบันได คสล. — [{active_proj_name}]")
    
    st1, st2 = st.columns(2)
    stair_name = st1.text_input("ชื่อ/สัญลักษณ์บันได", value="ST1", key="stair_name")
    stair_qty = st2.number_input("จำนวนชุดบันได", min_value=1, value=1, key="stair_qty")
    
    st_c1, st_c2, st_c3, st_c4 = st.columns(4)
    stair_w = st_c1.number_input("ความกว้างบันได (เมตร)", min_value=0.05, value=1.20, step=0.05, key="stair_w")
    num_steps = st_c2.number_input("จำนวนลูกตั้ง (ขั้น)", min_value=1, value=10, key="num_steps")
    num_treads = st_c3.number_input("จำนวนลูกนอน (ขั้น)", min_value=1, value=9, key="num_treads")
    step_r_cm = st_c4.number_input("ลูกตั้ง (ซม.)", min_value=1.0, value=17.5, step=0.5, key="step_r_cm")
    
    st_c4, st_c5 = st.columns(2)
    step_t_cm = st_c4.number_input("ลูกนอน (ซม.)", value=25.0, step=1.0, key="step_t_cm")
    slab_th_cm = st_c5.number_input("ความหนาพื้นบันได (ซม.)", value=12.0, step=1.0, key="slab_th_cm")

    st.markdown("---")
    has_landing = st.checkbox("มีชานพักบันได (Landing)", value=True, key="has_landing")
    land_w, land_l, land_th_cm = 0.0, 0.0, 12.0
    if has_landing:
        l_col1, l_col2, l_col3 = st.columns(3)
        land_w = l_col1.number_input("กว้างชานพัก (เมตร)", value=1.20, step=0.1, key="land_w")
        land_l = l_col2.number_input("ยาวชานพัก (เมตร)", value=2.40, step=0.1, key="land_l")
        land_th_cm = l_col3.number_input("หนาชานพัก (ซม.)", value=12.0, step=1.0, key="land_th_cm")

    st.markdown("#### 🥞 เหล็กเสริมบันได")
    str_re1, str_re2 = st.columns(2)
    stair_rebar_type = str_re1.selectbox("ขนาดเหล็กเสริมบันได", REBAR_LIST, index=2, key="stair_rebar_type")
    stair_rebar_spacing = str_re2.number_input("ระยะห่าง @ (เมตร)", value=0.15, step=0.01, key="stair_rebar_spacing")

    if st.button("➕ บันทึกงานบันได", type="primary", key="btn_save_stair"):
        step_r = step_r_cm / 100.0
        step_t = step_t_cm / 100.0
        slab_th = slab_th_cm / 100.0
        land_th = land_th_cm / 100.0

        run_len = num_treads * step_t
        rise_len = num_steps * step_r
        inclined_len = math.sqrt(run_len**2 + rise_len**2)

        vol_steps = (0.5 * step_r * step_t * stair_w) * num_treads
        vol_slab = inclined_len * stair_w * slab_th
        vol_landing = (land_w * land_l * land_th) if has_landing else 0.0
        net_vol = (vol_steps + vol_slab + vol_landing) * stair_qty
        tot_vol = with_waste(net_vol, waste_concrete)
        
        form_bottom = inclined_len * stair_w
        form_risers = num_treads * step_r * stair_w
        form_sides = 2 * inclined_len * slab_th
        form_landing = (land_w * land_l) + (2 * (land_w + land_l) * land_th) if has_landing else 0.0
        net_form = (form_bottom + form_risers + form_sides + form_landing) * stair_qty
        tot_form = with_waste(net_form, waste_formwork)

        if stair_rebar_spacing <= 0:
            st.error("ระยะห่างเหล็กบันไดต้องมากกว่า 0")
            st.stop()
        num_main_bars = count_by_spacing(stair_w, stair_rebar_spacing)
        num_cross_bars = count_by_spacing(inclined_len, stair_rebar_spacing)
        stair_rebar_len = (num_main_bars * (inclined_len + 0.60)) + (num_cross_bars * stair_w)
        net_rebar_weight = stair_rebar_len * stair_qty * REBAR_WEIGHT[stair_rebar_type]
        tot_rebar_weight = with_waste(net_rebar_weight, waste_rebar)

        p_stair_rebar = get_rebar_price(stair_rebar_type, p_db12, p_rb9)
        mat_c = tot_vol * p_concrete + tot_rebar_weight * p_stair_rebar + tot_form * p_formwork
        lab_c = net_vol * labour_concrete + net_rebar_weight * labour_rebar + net_form * labour_formwork

        detail_text = f"ลูกตั้ง {num_steps} ขั้น / ลูกนอน {num_treads} ขั้น (กว้าง {stair_w:.2f}ม.) | Run {run_len:.2f}ม. Rise {rise_len:.2f}ม. | เหล็ก {stair_rebar_type}@{stair_rebar_spacing:.2f}ม."
        if has_landing:
            detail_text += f" + ชานพัก {land_w:.2f}x{land_l:.2f}ม."

        add_takeoff_item({
            "หมวด": "งานบันได",
            "รายการ": stair_name,
            "รายละเอียด": detail_text,
            "จำนวน": stair_qty,
            "คอนกรีตสุทธิ (ลบ.ม.)": round(net_vol, 2),
            "คอนกรีต (ลบ.ม.)": round(tot_vol, 2),
            "เหล็กสุทธิ (กก.)": round(net_rebar_weight, 2),
            "เหล็ก (กก.)": round(tot_rebar_weight, 2),
            "เหล็กแยกชนิด": {stair_rebar_type: round(tot_rebar_weight, 2)},
            "ไม้แบบสุทธิ (ตร.ม.)": round(net_form, 2),
            "ไม้แบบ (ตร.ม.)": round(tot_form, 2),
            "ค่าวัสดุ (บาท)": round(mat_c, 2),
            "ค่าแรง (บาท)": round(lab_c, 2)
        })

# =========================================================
# TAB 9: ⛺ หลังคา
# =========================================================
with tabs[8]:
    st.subheader(f"⛺ ถอดปริมาณงานหลังคา (แยกทรงหลังคา & วัสดุมุง) — [{active_proj_name}]")
    
    r1, r2, r3 = st.columns([1, 1.5, 1.5])
    roof_name = r1.text_input("ชื่อ/สัญลักษณ์หลังคา", value="R1", key="roof_name")
    roof_shape = r2.selectbox("ประเภททรงหลังคา", list(ROOF_SHAPE_FACTORS.keys()), key="roof_shape")
    roof_material = r3.selectbox("ประเภทวัสดุมุง (Roof Material Type)", list(ROOF_MATERIAL_SPECS.keys()), key="roof_material")

    ra1, ra2, ra3 = st.columns(3)
    roof_area_mode = ra1.selectbox("วิธีหาพื้นที่มุง", ["คำนวณจากผัง + มุมลาด", "ระบุพื้นที่มุงจริงจากแบบ"], key="roof_area_mode")
    roof_overhang = ra2.number_input("ชายคายื่นเฉลี่ยด้านละ (เมตร)", min_value=0.0, value=0.30, step=0.05, key="roof_overhang")
    roof_manual_area = ra3.number_input("พื้นที่มุงจริงจากแบบ (ตร.ม.)", min_value=0.0, value=0.0, step=0.5, key="roof_manual_area")

    rs1, rs2 = st.columns(2)
    roof_steel_mode = rs1.selectbox("วิธีหาน้ำหนักโครงเหล็ก", ["ประเมินจาก kg/ตร.ม.", "ระบุจากรายการคำนวณ/แบบโครงหลังคา"], key="roof_steel_mode")
    roof_manual_steel = rs2.number_input("น้ำหนักโครงเหล็กสุทธิจากแบบ (กก.)", min_value=0.0, value=0.0, step=10.0, key="roof_manual_steel")

    rc1, rc2, rc3, rc4 = st.columns(4)
    roof_plan_w = rc1.number_input("ความกว้างผังหลังคา (เมตร)", min_value=0.05, value=10.0, step=0.5, key="roof_plan_w")
    roof_plan_l = rc2.number_input("ความยาวผังหลังคา (เมตร)", min_value=0.05, value=12.0, step=0.5, key="roof_plan_l")
    roof_pitch = rc3.number_input("ความชันหลังคา (องศา °)", min_value=0.0, max_value=85.0, value=30.0, step=1.0, key="roof_pitch")
    ridge_len = rc4.number_input("ความยาวครอบสัน/ตะเข้สันรวม (เมตร)", min_value=0.0, value=25.0, step=1.0, key="ridge_len")

    mat_spec = ROOF_MATERIAL_SPECS.get(roof_material, {"mat": 300.0, "lab": 120.0, "steel_factor": 20.0})

    plan_area = max(0.0, (roof_plan_w + 2 * roof_overhang) * (roof_plan_l + 2 * roof_overhang))
    rad = math.radians(roof_pitch)
    cos_val = math.cos(rad)
    pitch_slope_factor = 1.0 / cos_val if cos_val > 0.001 else 1.0
    auto_roof_area = plan_area * pitch_slope_factor
    real_roof_area = roof_manual_area if roof_area_mode == "ระบุพื้นที่มุงจริงจากแบบ" and roof_manual_area > 0 else auto_roof_area

    st.info(f"💡 **พื้นที่มุงที่ใช้คำนวณ**: {real_roof_area:.2f} ตร.ม. | พื้นที่ฉายรวมชายคา {plan_area:.2f} ตร.ม. | Factor ความชัน {pitch_slope_factor:.3f} | ไม่ใช้ Factor ทรงซ้ำซ้อน")

    if st.button("➕ บันทึกงานหลังคา", type="primary", key="btn_save_roof"):
        tile_area = with_waste(real_roof_area, waste_roof)
        steel_factor = mat_spec["steel_factor"]
        net_steel_weight = roof_manual_steel if roof_steel_mode == "ระบุจากรายการคำนวณ/แบบโครงหลังคา" and roof_manual_steel > 0 else real_roof_area * steel_factor
        tot_steel_weight = with_waste(net_steel_weight, waste_rebar)

        p_tile = mat_spec["mat"]
        l_tile = mat_spec["lab"]

        mat_roof_tiles = tile_area * p_tile
        lab_roof_tiles = real_roof_area * l_tile
        
        mat_ridge = ridge_len * p_roof_cap
        lab_ridge = ridge_len * 60.0

        mat_steel = tot_steel_weight * p_roof_steel
        lab_steel = net_steel_weight * labour_roof_steel

        total_roof_mat = mat_roof_tiles + mat_ridge + mat_steel
        total_roof_lab = lab_roof_tiles + lab_ridge + lab_steel

        add_takeoff_item({
            "หมวด": "งานหลังคา",
            "รายการ": roof_name,
            "รายละเอียด": f"{roof_shape} | {roof_material} | พื้นที่มุงสุทธิ {real_roof_area:.1f} ตร.ม. / จัดซื้อ {tile_area:.1f} ตร.ม. | โครงเหล็กสุทธิ {net_steel_weight:.1f} / จัดซื้อ {tot_steel_weight:.1f} กก. | {'อ้างอิงแบบ' if roof_steel_mode.startswith('ระบุ') else f'Factor {steel_factor:.1f} kg/ตร.ม.'}",
            "จำนวน": 1,
            "คอนกรีต (ลบ.ม.)": 0.0,
            "เหล็กสุทธิ (กก.)": round(net_steel_weight, 2),
            "เหล็ก (กก.)": round(tot_steel_weight, 2),
            "เหล็กแยกชนิด": {"โครงเหล็กหลังคา": round(tot_steel_weight, 2)},
            "ไม้แบบ (ตร.ม.)": 0.0,
            "พื้นที่หลังคา (ตร.ม.)": round(tile_area, 2),
            "ค่าวัสดุ (บาท)": round(total_roof_mat, 2),
            "ค่าแรง (บาท)": round(total_roof_lab, 2)
        })

# =========================================================
# TAB 10: 🧮 คำนวณ
# =========================================================
with tabs[9]:
    st.subheader(f"🧮 คำนวณสรุปปริมาณวัสดุรวม — [{active_proj_name}]")
    if not current_proj or not current_proj.get("items"):
        st.info("ยังไม่มีรายการถอดแบบในโครงการนี้")
    else:
        df_items = pd.DataFrame(current_proj["items"])
        
        for _col in ["คอนกรีต (ลบ.ม.)", "เหล็ก (กก.)", "ไม้แบบ (ตร.ม.)", "ค่าวัสดุ (บาท)", "ค่าแรง (บาท)"]:
            if _col in df_items.columns:
                df_items[_col] = pd.to_numeric(df_items[_col], errors="coerce").fillna(0.0)
        tot_conc = df_items["คอนกรีต (ลบ.ม.)"].sum() if "คอนกรีต (ลบ.ม.)" in df_items else 0.0
        tot_steel_all = df_items["เหล็ก (กก.)"].sum() if "เหล็ก (กก.)" in df_items else 0.0
        tot_rebar = sum(sum_rebar_breakdown(current_proj.get("items", [])))
        tot_roof_steel = sum_structural_steel(current_proj.get("items", []))
        tot_form = df_items["ไม้แบบ (ตร.ม.)"].sum() if "ไม้แบบ (ตร.ม.)" in df_items else 0.0
        tot_mat = df_items["ค่าวัสดุ (บาท)"].sum() if "ค่าวัสดุ (บาท)" in df_items else 0.0
        tot_lab = df_items["ค่าแรง (บาท)"].sum() if "ค่าแรง (บาท)" in df_items else 0.0

        col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
        col_m1.metric("คอนกรีตรวม", f"{tot_conc:,.2f} ลบ.ม.")
        col_m2.metric("เหล็กเสริมรวม", f"{tot_rebar:,.2f} กก.")
        col_m3.metric("โครงเหล็กหลังคา", f"{tot_roof_steel:,.2f} กก.")
        col_m4.metric("ค่าวัสดุรวม", f"฿{tot_mat:,.2f}")
        col_m5.metric("ค่าแรงรวม", f"฿{tot_lab:,.2f}")

        st.markdown("---")
        st.subheader("📋 รายการถอดแบบทั้งหมด")
        st.caption("หมายเหตุ: รายการที่สร้างจาก V6.7 หรือต่ำกว่าอาจไม่มีข้อมูลปริมาณสุทธิแยกจาก Waste; ข้อมูลใหม่ใน V6.8 จะแยกปริมาณสุทธิและปริมาณจัดซื้อให้ชัดเจน")
        
        st.dataframe(df_items, use_container_width=True)

        steel_totals = sum_rebar_breakdown(current_proj.get("items", []))
        if steel_totals:
            st.markdown("#### 🔩 สรุปน้ำหนักเหล็กแยกชนิด")
            steel_df = pd.DataFrame(
                [{"ชนิดเหล็ก": k, "น้ำหนัก (กก.)": round(v, 2)} for k, v in sorted(steel_totals.items())]
            )
            st.dataframe(steel_df, use_container_width=True, hide_index=True)

# =========================================================
# TAB 11: 📋 BOQ
# =========================================================
with tabs[10]:
    st.subheader(f"📋 สรุปรายการ BOQ แยกหมวดงาน — [{active_proj_name}]")
    if not current_proj or not current_proj.get("items"):
        st.info("ยังไม่มีรายการถอดแบบในโครงการนี้")
    else:
        df_items = pd.DataFrame(current_proj["items"])
        for _col in ["ค่าวัสดุ (บาท)", "ค่าแรง (บาท)"]:
            if _col not in df_items.columns:
                df_items[_col] = 0.0
            df_items[_col] = pd.to_numeric(df_items[_col], errors="coerce").fillna(0.0)
        
        st.markdown("#### 📄 BOQ รายการย่อย")
        st.dataframe(df_items, use_container_width=True, hide_index=True)
        
        grouped = df_items.groupby("หมวด").agg({
            "ค่าวัสดุ (บาท)": "sum",
            "ค่าแรง (บาท)": "sum"
        }).reset_index()

        grouped["รวมเงิน (บาท)"] = grouped["ค่าวัสดุ (บาท)"] + grouped["ค่าแรง (บาท)"]
        
        subtotal_mat = grouped["ค่าวัสดุ (บาท)"].sum()
        subtotal_lab = grouped["ค่าแรง (บาท)"].sum()
        subtotal_direct = subtotal_mat + subtotal_lab

        profit_amount = subtotal_direct * profit_percent
        vat_amount = (subtotal_direct + profit_amount) * 0.07 if use_vat else 0.0
        grand_total = subtotal_direct + profit_amount + vat_amount

        st.dataframe(grouped.style.format({
            "ค่าวัสดุ (บาท)": "{:,.2f}",
            "ค่าแรง (บาท)": "{:,.2f}",
            "รวมเงิน (บาท)": "{:,.2f}"
        }), use_container_width=True)

        st.markdown("---")
        b_c1, b_c2 = st.columns([2, 1])
        with b_c2:
            st.markdown(f"**รวมค่าวัสดุและค่าแรงขั้นต้น**: {subtotal_direct:,.2f} บาท")
            st.markdown(f"**ค่าดำเนินการ & กำไร ({profit_percent*100:.1f}%)**: {profit_amount:,.2f} บาท")
            if use_vat:
                st.markdown(f"**ภาษีมูลค่าเพิ่ม VAT 7%**: {vat_amount:,.2f} บาท")
            st.markdown(f"### **ราคารวมสุทธิ: {grand_total:,.2f} บาท**")

# =========================================================
# TAB 12: 📊 สรุป
# =========================================================
with tabs[11]:
    st.subheader(f"📊 ภาพรวมและสรุปสถิติโครงการ — [{active_proj_name}]")
    if not current_proj or not current_proj.get("items"):
        st.info("ยังไม่มีข้อมูลโครงการเพื่อแสดงผลภาพรวม")
    else:
        df_items = pd.DataFrame(current_proj["items"])
        for _col in ["คอนกรีต (ลบ.ม.)", "เหล็ก (กก.)", "ไม้แบบ (ตร.ม.)", "ค่าวัสดุ (บาท)", "ค่าแรง (บาท)"]:
            if _col not in df_items.columns:
                df_items[_col] = 0.0
            df_items[_col] = pd.to_numeric(df_items[_col], errors="coerce").fillna(0.0)

        tot_rebar = sum(sum_rebar_breakdown(current_proj.get("items", [])))
        tot_roof_steel = sum_structural_steel(current_proj.get("items", []))
        tot_conc = df_items["คอนกรีต (ลบ.ม.)"].sum()
        tot_form = df_items["ไม้แบบ (ตร.ม.)"].sum()
        tot_mat = df_items["ค่าวัสดุ (บาท)"].sum()
        tot_lab = df_items["ค่าแรง (บาท)"].sum()

        sm1, sm2, sm3, sm4, sm5 = st.columns(5)
        sm1.metric("คอนกรีต", f"{tot_conc:,.2f} ลบ.ม.")
        sm2.metric("เหล็กเสริม", f"{tot_rebar:,.2f} กก.")
        sm3.metric("โครงเหล็กหลังคา", f"{tot_roof_steel:,.2f} กก.")
        sm4.metric("ไม้แบบ", f"{tot_form:,.2f} ตร.ม.")
        sm5.metric("ค่าวัสดุ+แรง", f"฿{tot_mat + tot_lab:,.0f}")

        grouped_chart = df_items.groupby("หมวด")[["ค่าวัสดุ (บาท)", "ค่าแรง (บาท)"]].sum()
        st.bar_chart(grouped_chart)
        st.caption("สัดส่วนค่าวัสดุและค่าแรงเปรียบเทียบตามหมวดงาน")

        steel_totals = sum_rebar_breakdown(current_proj.get("items", []))
        if steel_totals or tot_roof_steel > 0:
            st.markdown("#### 🔩 สรุปเหล็กเพื่อจัดซื้อ")
            rows = [{"ชนิดเหล็ก": k, "น้ำหนัก (กก.)": round(v, 2)} for k, v in sorted(steel_totals.items())]
            if tot_roof_steel > 0:
                rows.append({"ชนิดเหล็ก": "โครงเหล็กหลังคา", "น้ำหนัก (กก.)": round(tot_roof_steel, 2)})
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
