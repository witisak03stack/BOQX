import streamlit as st
import pandas as pd
import math
import google.generativeai as genai
from PIL import Image

# ---------------------------------------------------------
# 1. Page Configuration & Custom CSS
# ---------------------------------------------------------
st.set_page_config(
    page_title="BOQX - AI-Powered Construction Takeoff",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Kanit:wght@300;400;500;600;700&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Kanit', sans-serif;
    }
    
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 1rem !important;
    }

    .brand-badge {
        background: linear-gradient(90deg, #00d2ff, #0047ab);
        color: white;
        padding: 3px 10px;
        border-radius: 15px;
        font-size: 0.75rem;
        font-weight: 600;
        display: inline-block;
    }

    .sub-title {
        margin-top: 4px;
        color: #475569;
        font-size: 0.95rem;
        font-weight: 400;
        margin-bottom: 0px;
    }

    .item-card {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 10px 14px;
        margin-bottom: 8px;
    }
    
    .item-card-title {
        font-weight: 600;
        font-size: 1rem;
        color: #1e293b;
    }
    
    .item-card-sub {
        font-size: 0.85rem;
        color: #64748b;
    }
</style>
""", unsafe_allow_html=True)

# Session States Initialize
if "items" not in st.session_state or not isinstance(st.session_state["items"], list):
    st.session_state["items"] = []

if "projects" not in st.session_state or not isinstance(st.session_state["projects"], dict):
    st.session_state["projects"] = {}

if "editing_index" not in st.session_state:
    st.session_state["editing_index"] = None

# ---------------------------------------------------------
# 2. Header Bar
# ---------------------------------------------------------
col_logo, col_info = st.columns([0.25, 0.75], vertical_alignment="center")

with col_logo:
    try:
        st.image("logo.png", width=220)
    except:
        st.title("🏗️ BOQX")

with col_info:
    st.markdown("""
        <div>
            <span class="brand-badge">PRO VERSION 4.5 (STABLE & BUGFIXED)</span>
            <p class="sub-title">ระบบคำนวณถอดแบบและประมาณราคา BOQ อัตโนมัติ</p>
        </div>
    """, unsafe_allow_html=True)

# ---------------------------------------------------------
# 3. Sidebar Settings
# ---------------------------------------------------------
st.sidebar.markdown("### 🔑 API Authentication")
api_key_input = st.sidebar.text_input("Gemini API Key", type="password")

st.sidebar.markdown("---")
st.sidebar.markdown("### ⚙️ Unit Cost & Rates Settings")

with st.sidebar.expander("⛏️ ค่าวัสดุ & งานดินฐานราก (Earthwork)", expanded=False):
    p_sand = st.number_input("ทรายหยาบถมรองพื้น (บาท/ลบ.ม.)", value=550.0)
    p_lean = st.number_input("คอนกรีตหยาบ Lean 1:3:6 (บาท/ลบ.ม.)", value=1800.0)
    labour_excavate = st.number_input("ค่าแรงขุดดินฐานราก (บาท/ลบ.ม.)", value=120.0)
    labour_backfill = st.number_input("ค่าแรงถมกลับและบดอัด (บาท/ลบ.ม.)", value=80.0)
    labour_sand = st.number_input("ค่าแรงปรับระดับทรายรองพื้น (บาท/ลบ.ม.)", value=100.0)

with st.sidebar.expander("🧱 ค่าวัสดุโครงสร้าง & วัสดุย่อย", expanded=False):
    p_concrete = st.number_input("คอนกรีต 240-320 ksc (บาท/ลบ.ม.)", value=2450.0)
    p_rb6 = st.number_input("เหล็กเส้นกลม RB6 SR24 (บาท/กก.)", value=33.5)
    p_rb9 = st.number_input("เหล็กเส้นกลม RB9 SR24 (บาท/กก.)", value=33.0)
    p_db12 = st.number_input("เหล็กข้ออ้อย DB12 SD40 (บาท/กก.)", value=31.5)
    p_db16 = st.number_input("เหล็กข้ออ้อย DB16 SD40 (บาท/กก.)", value=31.0)
    p_db20 = st.number_input("เหล็กข้ออ้อย DB20 SD40 (บาท/กก.)", value=31.0)
    p_formwork = st.number_input("ไม้แบบหล่อคอนกรีต (บาท/ตร.ม.)", value=380.0)
    p_tie_wire = st.number_input("ลวดผูกเหล็ก #18 (บาท/กก.)", value=45.0)
    p_nails = st.number_input("ตะปูตอกไม้แบบ (บาท/กก.)", value=55.0)

with st.sidebar.expander("🔨 ค่าแรงงานมาตรฐาน (Labour Rate)", expanded=False):
    labour_concrete = st.number_input("ค่าแรงเทคอนกรีต (บาท/ลบ.ม.)", value=450.0)
    labour_rebar = st.number_input("ค่าแรงดัด/ผูกเหล็ก (บาท/กก.)", value=4.5)
    labour_formwork = st.number_input("ค่าแรงประกอบไม้แบบ (บาท/ตร.ม.)", value=150.0)
    labour_tile = st.number_input("ค่าแรงปูกระเบื้อง (บาท/แผ่น)", value=15.0)
    labour_brick = st.number_input("ค่าแรงก่ออิฐ (บาท/ก้อน)", value=2.0)
    labour_plaster = st.number_input("ค่าแรงฉาบปูน (บาท/ตร.ม.)", value=90.0)

with st.sidebar.expander("🏠 ราคาวัสดุงานสถาปัตยกรรม", expanded=False):
    price_clay_brick = st.number_input("เฉลี่ยราคาอิฐมอญ (บาท/ก้อน)", value=1.5)
    price_aac_block = st.number_input("ราคาอิฐมวลเบา (บาท/ก้อน)", value=22.0)
    price_concrete_block = st.number_input("ราคาอิฐบล็อก (บาท/ก้อน)", value=8.5)
    price_masonry_mortar = st.number_input("ราคาปูนก่อสำเร็จรูป 50กก. (บาท/ถุง)", value=105.0)
    price_plaster_bag = st.number_input("ราคาปูนฉาบสำเร็จรูป 50กก. (บาท/ถุง)", value=110.0)

with st.sidebar.expander("📉 เปอร์เซ็นต์สูญเสีย (% Wastage)", expanded=False):
    waste_concrete = st.number_input("เผื่อเทคอนกรีต (%)", value=5.0) / 100.0
    waste_rebar = st.number_input("เผื่อดัด/ทาบเหล็ก (%)", value=12.0) / 100.0
    waste_formwork = st.number_input("เผื่อตัดไม้แบบ (%)", value=15.0) / 100.0

# ---------------------------------------------------------
# 4. Helper Functions & Logic
# ---------------------------------------------------------
def get_rebar_unit_weight(rebar_str: str) -> float:
    r_str = str(rebar_str).upper()
    if "RB6" in r_str: return 0.222
    if "RB9" in r_str: return 0.499
    if "DB12" in r_str: return 0.888
    if "DB16" in r_str: return 1.580
    if "DB20" in r_str: return 2.470
    if "DB25" in r_str: return 3.850
    return 1.580

def get_rebar_price(rebar_str: str) -> float:
    r_str = str(rebar_str).upper()
    if "RB6" in r_str: return p_rb6
    if "RB9" in r_str: return p_rb9
    if "DB12" in r_str: return p_db12
    if "DB16" in r_str: return p_db16
    if "DB20" in r_str: return p_db20
    return p_db16

def calculate_advanced_boq(e_type, w, l, h, qty, main_size="DB16 (SD40)", main_qty=4, stirrup_size="RB6 (SR24)", stirrup_spacing=0.15, has_special=False, spec_size="DB16 (SD40)", spec_qty=0, spec_len=0.0):
    try:
        w, l, h, qty = float(w), float(l), float(h), int(qty)
    except (ValueError, TypeError):
        w, l, h, qty = 0.2, 0.2, 3.0, 1

    vol_net = w * l * h * qty
    vol_total = vol_net * (1 + waste_concrete)
    
    w_main = get_rebar_unit_weight(main_size)
    length_per_bar = (h if "คาน" not in str(e_type) else l) * 1.15
    main_weight = main_qty * length_per_bar * w_main * qty
    
    stirrup_perimeter = 2 * (w + l)
    span = (h if "คาน" not in str(e_type) else l)
    
    try:
        s_spacing = float(stirrup_spacing)
        num_stirrups = math.ceil(span / s_spacing) if s_spacing > 0 else 0
    except (ValueError, TypeError):
        num_stirrups = 0
        
    w_stirrup = get_rebar_unit_weight(stirrup_size)
    stirrup_weight = num_stirrups * stirrup_perimeter * w_stirrup * qty
    
    special_weight = 0.0
    spec_desc = ""
    if has_special and spec_qty > 0 and spec_len > 0:
        w_spec = get_rebar_unit_weight(spec_size)
        special_weight = spec_qty * spec_len * w_spec * qty
        spec_desc = f" + เหล็กพิเศษ {spec_size} ({spec_qty}เส้น @{spec_len}ม.)"
    
    rebar_total = (main_weight + stirrup_weight + special_weight) * (1 + waste_rebar)
    tie_wire_kg = rebar_total * 0.03
    
    if "เสา" in str(e_type):
        form_net = 2 * (w + l) * h * qty
    elif "คาน" in str(e_type):
        form_net = (2 * h + w) * l * qty
    elif "พื้น" in str(e_type):
        form_net = (w * l) * qty
    else: # ฐานราก
        form_net = 2 * (w + l) * h * qty
        
    form_total = form_net * (1 + waste_formwork)
    nails_kg = form_total * 0.25
    
    rebar_desc = f"เหล็กเมน {main_size} ({main_qty}เส้น) + ปลอก {stirrup_size}@{stirrup_spacing}ม.{spec_desc}"
    
    return round(vol_total, 2), round(rebar_total, 2), round(tie_wire_kg, 2), round(form_total, 2), round(nails_kg, 2), rebar_desc

def calculate_footing_earthwork(w, l, h, excavation_depth, sand_thick, lean_thick, qty):
    base_excavation = w * l * excavation_depth * qty
    excavation_vol = base_excavation * 1.30
    
    sand_vol = w * l * sand_thick * qty * 1.15
    lean_vol = w * l * lean_thick * qty * 1.05
    
    footing_vol = w * l * h * qty
    backfill_vol = max(0.0, excavation_vol - (footing_vol + (w * l * sand_thick * qty) + (w * l * lean_thick * qty)))
    
    return round(excavation_vol, 2), round(sand_vol, 2), round(lean_vol, 2), round(backfill_vol, 2)

def calculate_flooring(area_sqm, screed_thick_m, tile_size):
    screed_vol = area_sqm * screed_thick_m * 1.05
    tile_dim_map = {
        "20x20 ซม.": 0.2 * 0.2, "30x30 ซม.": 0.3 * 0.3,
        "40x40 ซม.": 0.4 * 0.4, "60x60 ซม.": 0.6 * 0.6,
        "60x120 ซม.": 0.6 * 1.2, "กระเบื้องยาง 15x90 ซม.": 0.15 * 0.90
    }
    tile_area = tile_dim_map.get(tile_size, 0.36)
    num_tiles = math.ceil((area_sqm / tile_area) * 1.07)
    return round(screed_vol, 2), num_tiles

def calculate_wall_with_lintel(width_m, height_m, brick_spec, plaster_thick_m=0.015, add_lintels=True, both_sides=True):
    wall_area = width_m * height_m
    plaster_area = wall_area * (2 if both_sides else 1)
    
    brick_data_map = {
        "อิฐมอญ 2 รู ก้อนเล็ก (3x6x14 ซม.)": {"qty_sqm": 120, "price_key": "clay"},
        "อิฐมอญ 2 รู ก้อนใหญ่ (5x6x15 ซม.)": {"qty_sqm": 90, "price_key": "clay"},
        "อิฐมวลเบา 7.5x20x60 ซม.": {"qty_sqm": 8.33, "price_key": "aac"},
        "อิฐบล็อก 7x19x39 ซม. (มาตรฐาน)": {"qty_sqm": 12.5, "price_key": "block"}
    }
    spec_info = brick_data_map.get(brick_spec, {"qty_sqm": 100, "price_key": "clay"})
    num_bricks = math.ceil(wall_area * spec_info["qty_sqm"] * 1.05)
    masonry_bags = math.ceil((wall_area * 30) / 50.0)
    plaster_bags = math.ceil(plaster_area * (plaster_thick_m / 0.015) / 2.0)
    
    lintel_vol, lintel_rebar, lintel_form = 0.0, 0.0, 0.0
    if add_lintels:
        lintel_len = (height_m * 2) + width_m
        lintel_vol = lintel_len * 0.10 * 0.10 * 1.05
        lintel_rebar = (lintel_len * 2 * 0.222) * (1 + waste_rebar)
        lintel_form = (lintel_len * 0.10 * 2) * (1 + waste_formwork)

    return (wall_area, num_bricks, masonry_bags, plaster_bags, 
            round(lintel_vol, 2), round(lintel_rebar, 2), round(lintel_form, 2), spec_info["price_key"])

def render_item_cards(category_filter):
    filtered_items = [(idx, item) for idx, item in enumerate(st.session_state["items"]) if category_filter in item["หมวด"]]
    st.markdown(f"##### 📋 รายการที่บันทึกแล้ว ({len(filtered_items)})")
    
    for real_idx, item in filtered_items:
        card_col1, card_col2, card_col3 = st.columns([0.7, 0.15, 0.15])
        with card_col1:
            st.markdown(f"""
            <div class="item-card">
                <div class="item-card-title">{item['ชื่อ/สัญลักษณ์']} ({item['จำนวน']} รายการ)</div>
                <div class="item-card-sub">{item['รายละเอียด']}</div>
            </div>
            """, unsafe_allow_html=True)
        with card_col2:
            if st.button("✏", key=f"edit_{real_idx}", help="แก้ไขรายการนี้"):
                st.session_state["editing_index"] = real_idx
                st.rerun()
        with card_col3:
            if st.button("🗑", key=f"del_{real_idx}", help="ลบรายการนี้"):
                st.session_state["items"].pop(real_idx)
                if st.session_state["editing_index"] == real_idx:
                    st.session_state["editing_index"] = None
                elif st.session_state["editing_index"] is not None and st.session_state["editing_index"] > real_idx:
                    st.session_state["editing_index"] -= 1
                st.rerun()

# ---------------------------------------------------------
# 5. UI Navigation Tabs
# ---------------------------------------------------------
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🏠 งานสถาปัตย์", 
    "✍️ งานโครงสร้าง", 
    "📷 Vision AI Scan", 
    "📊 สรุปตาราง BOQ", 
    "💾 บันทึกโครงการ"
])

# --- TAB 1: Architectural ---
with tab1:
    st.markdown("### 🏠 งานสถาปัตยกรรม (Architectural Items)")
    sub_tab1, sub_tab2 = st.tabs(["🪵 งานพื้น (Flooring)", "🧱 งานผนัง & เสาเอ็น-คานทับหลัง"])
    
    with sub_tab1:
        col_form, col_list = st.columns([0.6, 0.4])
        edit_idx = st.session_state["editing_index"]
        is_editing_floor = (edit_idx is not None and edit_idx < len(st.session_state["items"]) and st.session_state["items"][edit_idx]["หมวด"] == "งานสถาปัตย์-พื้น")
        edit_item = st.session_state["items"][edit_idx] if is_editing_floor else {}

        with col_form:
            if is_editing_floor:
                st.info(f"✏️ กำลังแก้ไขรายการ: **{edit_item['ชื่อ/สัญลักษณ์']}**")
            
            floor_name = st.text_input("ชื่อห้อง / บริเวณ", value=edit_item.get("ชื่อ/สัญลักษณ์", "ห้องนอน 1"), key="f_name")
            f_w = st.number_input("ความกว้าง (ม.)", min_value=0.1, value=float(edit_item.get("f_w", 4.0)), key="f_w")
            f_l = st.number_input("ความยาว (ม.)", min_value=0.1, value=float(edit_item.get("f_l", 5.0)), key="f_l")
            floor_area = round(f_w * f_l, 2)
            screed_thick = st.number_input("ความหนาปูนปรับระดับ (ม.)", min_value=0.01, value=float(edit_item.get("screed_thick", 0.03)), step=0.01, key="f_screed")
            
            tile_options = ["20x20 ซม.", "30x30 ซม.", "40x40 ซม.", "60x60 ซม.", "60x120 ซม.", "กระเบื้องยาง 15x90 ซม."]
            tile_idx = tile_options.index(edit_item.get("tile_size")) if edit_item.get("tile_size") in tile_options else 3
            tile_size = st.selectbox("ขนาดกระเบื้อง", tile_options, index=tile_idx, key="f_tile")
            
            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                btn_label = "💾 อัปเดตรายการ" if is_editing_floor else "➕ บันทึกงานพื้น"
                if st.button(btn_label, type="primary", use_container_width=True, key="btn_floor"):
                    screed_v, tiles_count = calculate_flooring(floor_area, screed_thick, tile_size)
                    saved_data = {
                        "หมวด": "งานสถาปัตย์-พื้น",
                        "ชื่อ/สัญลักษณ์": floor_name,
                        "รายละเอียด": f"พื้นที่ {floor_area} ตร.ม. (กระเบื้อง {tile_size})",
                        "จำนวน": 1,
                        "f_w": f_w, "f_l": f_l, "screed_thick": screed_thick, "tile_size": tile_size,
                        "คอนกรีต/ปูนปรับระดับ (ลบ.ม.)": screed_v,
                        "กระเบื้อง (แผ่น)": tiles_count,
                        "เหล็กเสริม (กก.)": 0.0, "ลวดผูกเหล็ก (กก.)": 0.0, "ไม้แบบ (ตร.ม.)": 0.0, "ตะปูตอกไม้แบบ (กก.)": 0.0,
                        "เสาเข็ม (ต้น/ม.)": 0.0, "จำนวนอิฐ (ก้อน)": 0, "ปูนก่อ (ถุง)": 0, "ปูนฉาบ (ถุง)": 0, "พื้นที่ทาสี (ตร.ม.)": 0.0,
                        "ดินขุด (ลบ.ม.)": 0.0, "ทรายรองพื้น (ลบ.ม.)": 0.0, "คอนกรีตหยาบ (ลบ.ม.)": 0.0, "ดินถมกลับ (ลบ.ม.)": 0.0
                    }
                    if is_editing_floor:
                        st.session_state["items"][edit_idx] = saved_data
                        st.session_state["editing_index"] = None
                    else:
                        st.session_state["items"].append(saved_data)
                    st.rerun()

            with btn_col2:
                if is_editing_floor:
                    if st.button("❌ ยกเลิกการแก้ไข", use_container_width=True, key="cancel_floor"):
                        st.session_state["editing_index"] = None
                        st.rerun()

        with col_list:
            render_item_cards("งานสถาปัตย์-พื้น")

    with sub_tab2:
        col_form, col_list = st.columns([0.6, 0.4])
        is_editing_wall = (edit_idx is not None and edit_idx < len(st.session_state["items"]) and st.session_state["items"][edit_idx]["หมวด"] == "งานสถาปัตย์-ผนัง")
        edit_item_wall = st.session_state["items"][edit_idx] if is_editing_wall else {}

        with col_form:
            if is_editing_wall:
                st.info(f"✏️ กำลังแก้ไขรายการ: **{edit_item_wall['ชื่อ/สัญลักษณ์']}**")
                
            wall_name = st.text_input("ชื่อผนัง / บริเวณ", value=edit_item_wall.get("ชื่อ/สัญลักษณ์", "ผนังห้องรับแขก"), key="w_name")
            wall_w = st.number_input("ความกว้าง W (ม.)", min_value=0.1, value=float(edit_item_wall.get("wall_w", 4.0)), key="w_w")
            wall_h = st.number_input("ความสูง H (ม.)", min_value=0.1, value=float(edit_item_wall.get("wall_h", 2.8)), key="w_h")
            
            brick_options = [
                "อิฐมอญ 2 รู ก้อนเล็ก (3x6x14 ซม.)", 
                "อิฐมอญ 2 รู ก้อนใหญ่ (5x6x15 ซม.)", 
                "อิฐมวลเบา 7.5x20x60 ซม.", 
                "อิฐบล็อก 7x19x39 ซม. (มาตรฐาน)"
            ]
            brick_idx = brick_options.index(edit_item_wall.get("brick_spec")) if edit_item_wall.get("brick_spec") in brick_options else 0
            brick_spec = st.selectbox("ชนิดอิฐ", brick_options, index=brick_idx, key="w_spec")
            
            add_lintels = st.checkbox("ถอดปริมาณเสาเอ็น & คานทับหลัง (พร้อมไม้แบบขอบ)", value=edit_item_wall.get("add_lintels", True), key="w_lintel")
            both_sides = st.checkbox("ฉาบปูนทั้ง 2 ด้าน", value=edit_item_wall.get("both_sides", True), key="w_both")

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                btn_label = "💾 อัปเดตรายการ" if is_editing_wall else "➕ บันทึกงานผนัง"
                if st.button(btn_label, type="primary", use_container_width=True, key="btn_wall"):
                    w_area, bricks, mason_bags, plasters, l_vol, l_rebar, l_form, p_key = calculate_wall_with_lintel(
                        wall_w, wall_h, brick_spec, 0.015, add_lintels, both_sides
                    )
                    lintel_str = f" | เสาเอ็น-คานทับหลัง: คอนกรีต {l_vol}ลบ.ม." if add_lintels else ""
                    
                    saved_data = {
                        "หมวด": "งานสถาปัตย์-ผนัง",
                        "ชื่อ/สัญลักษณ์": wall_name,
                        "รายละเอียด": f"พื้นที่ {w_area:.2f} ตร.ม. ({brick_spec}){lintel_str}",
                        "จำนวน": 1,
                        "wall_w": wall_w, "wall_h": wall_h, "brick_spec": brick_spec, 
                        "price_key": p_key, "add_lintels": add_lintels, "both_sides": both_sides,
                        "คอนกรีต/ปูนปรับระดับ (ลบ.ม.)": l_vol, 
                        "เหล็กเสริม (กก.)": l_rebar, 
                        "ลวดผูกเหล็ก (กก.)": round(l_rebar * 0.03, 2) if l_rebar > 0 else 0.0,
                        "ไม้แบบ (ตร.ม.)": l_form, 
                        "ตะปูตอกไม้แบบ (กก.)": round(l_form * 0.25, 2) if l_form > 0 else 0.0, 
                        "จำนวนอิฐ (ก้อน)": bricks, "ปูนก่อ (ถุง)": mason_bags, "ปูนฉาบ (ถุง)": plasters,
                        "พื้นที่ทาสี (ตร.ม.)": w_area * 2 if both_sides else w_area,
                        "เสาเข็ม (ต้น/ม.)": 0.0, "กระเบื้อง (แผ่น)": 0,
                        "ดินขุด (ลบ.ม.)": 0.0, "ทรายรองพื้น (ลบ.ม.)": 0.0, "คอนกรีตหยาบ (ลบ.ม.)": 0.0, "ดินถมกลับ (ลบ.ม.)": 0.0
                    }
                    if is_editing_wall:
                        st.session_state["items"][edit_idx] = saved_data
                        st.session_state["editing_index"] = None
                    else:
                        st.session_state["items"].append(saved_data)
                    st.rerun()

            with btn_col2:
                if is_editing_wall:
                    if st.button("❌ ยกเลิกการแก้ไข", use_container_width=True, key="cancel_wall"):
                        st.session_state["editing_index"] = None
                        st.rerun()

        with col_list:
            render_item_cards("งานสถาปัตย์-ผนัง")

# --- TAB 2: Structural ---
with tab2:
    st.markdown("### ✍ งานโครงสร้าง (Structural Elements)")
    col_form, col_list = st.columns([0.65, 0.35])
    
    edit_idx = st.session_state["editing_index"]
    is_editing_struct = (edit_idx is not None and edit_idx < len(st.session_state["items"]) and "งานโครงสร้าง" in st.session_state["items"][edit_idx]["หมวด"])
    edit_item_struct = st.session_state["items"][edit_idx] if is_editing_struct else {}

    with col_form:
        if is_editing_struct:
            st.info(f"✏️ กำลังแก้ไขรายการ: **{edit_item_struct['ชื่อ/สัญลักษณ์']}** ({edit_item_struct['หมวด']})")
            
        e_type = st.selectbox("ประเภทโครงสร้าง", [
            "ฐานรากแผ่ (Isolated Footing)", "ฐานรากมีเสาเข็ม (Piled Footing)",
            "เสา (Column)", "คาน (Beam)", "พื้น (Slab)"
        ], key="s_type")
        
        if "เสา" in e_type:
            level_option = st.selectbox("ระดับชั้น / ตำแหน่งเสา", ["เสาตอม่อ", "เสาชั้น 1", "เสาชั้น 2", "เสาชั้น 3", "ระบุเอง..."], key="s_lvl_col")
        elif "คาน" in e_type:
            level_option = st.selectbox("ระดับชั้น / ตำแหน่งคาน", ["คานคอดิน (GB)", "คานชั้น 1", "คานชั้น 2", "คานชั้น 3", "ระบุเอง..."], key="s_lvl_bm")
        elif "พื้น" in e_type:
            level_option = st.selectbox("ระดับชั้น / ตำแหน่งพื้น", ["พื้นชั้น 1", "พื้นชั้น 2", "พื้นชั้น 3", "ระบุเอง..."], key="s_lvl_slb")
        else:
            level_option = "งานฐานราก"

        struct_group_name = f"งานโครงสร้าง ({level_option})"

        c1, c2 = st.columns(2)
        e_name = c1.text_input("ชื่อ/สัญลักษณ์", value=edit_item_struct.get("ชื่อ/สัญลักษณ์", "F1" if "ฐานราก" in e_type else "C1"), key="s_name")
        e_qty = c2.number_input("จำนวน (ชิ้น/ต้น)", min_value=1, value=int(edit_item_struct.get("จำนวน", 1)), key="s_qty")
        
        d1, d2, d3 = st.columns(3)
        e_w = d1.number_input("กว้าง W (ม.)", min_value=0.05, value=float(edit_item_struct.get("e_w", 1.20 if "ฐานราก" in e_type else 0.20)), step=0.05, key="s_w")
        e_l = d2.number_input("ยาว L (ม.)", min_value=0.05, value=float(edit_item_struct.get("e_l", 1.20 if "ฐานราก" in e_type else 0.20)), step=0.05, key="s_l")
        e_h = d3.number_input("หนา/สูง H (ม.)", min_value=0.05, value=float(edit_item_struct.get("e_h", 0.30 if "ฐานราก" in e_type else 3.00)), step=0.05, key="s_h")

        excavation_depth, sand_thick, lean_thick = 0.0, 0.0, 0.0
        if "ฐานราก" in e_type:
            st.markdown("##### ⛏️ งานดินขุด, งานทรายรองพื้น และคอนกรีตหยาบ")
            ex1, ex2, ex3 = st.columns(3)
            excavation_depth = ex1.number_input("ความลึกหลุมขุด (ม.)", min_value=0.1, value=float(edit_item_struct.get("excavation_depth", 1.50)), step=0.1, key="s_ex_depth")
            sand_thick = ex2.number_input("ทรายหยาบรองพื้น (ม.)", min_value=0.0, value=float(edit_item_struct.get("sand_thick", 0.05)), step=0.01, key="s_sand_t")
            lean_thick = ex3.number_input("คอนกรีตหยาบ Lean (ม.)", min_value=0.0, value=float(edit_item_struct.get("lean_thick", 0.05)), step=0.01, key="s_lean_t")

        st.markdown("##### 🔩 เหล็กเสริมหลัก & เหล็กปลอก")
        r1, r2 = st.columns(2)
        main_rebar_choice = r1.selectbox("เหล็กเมน", ["DB12 (SD40)", "DB16 (SD40)", "DB20 (SD40)"], index=1, key="s_main")
        main_qty = r2.number_input("จำนวนเหล็กเมน (เส้น)", min_value=1, value=int(edit_item_struct.get("main_qty", 8 if "ฐานราก" in e_type else 4)), key="s_mqty")
        
        s1, s2 = st.columns(2)
        stirrup_choice = s1.selectbox("เหล็กปลอก", ["RB6 (SR24)", "RB9 (SR24)"], index=0, key="s_stirrup")
        stirrup_spacing = s2.number_input("ระยะปลอก @ (ม.)", min_value=0.05, value=float(edit_item_struct.get("stirrup_spacing", 0.15)), step=0.02, key="s_space")

        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            btn_label = "💾 อัปเดตรายการโครงสร้าง" if is_editing_struct else "➕ เพิ่มรายการโครงสร้าง"
            if st.button(btn_label, type="primary", use_container_width=True, key="btn_struct"):
                v, r, tie_wire, f, nails, r_desc = calculate_advanced_boq(
                    e_type, e_w, e_l, e_h, e_qty, 
                    main_size=main_rebar_choice, main_qty=main_qty, 
                    stirrup_size=stirrup_choice, stirrup_spacing=stirrup_spacing
                )
                
                ex_v, sand_v, lean_v, backfill_v = 0.0, 0.0, 0.0, 0.0
                earth_desc = ""
                if "ฐานราก" in e_type:
                    ex_v, sand_v, lean_v, backfill_v = calculate_footing_earthwork(
                        e_w, e_l, e_h, excavation_depth, sand_thick, lean_thick, e_qty
                    )
                    earth_desc = f" | ดินขุด(+30%): {ex_v}ลบ.ม. | ทรายรอง: {sand_v}ลบ.ม. | คอนกรีตหยาบ: {lean_v}ลบ.ม. | ดินถมกลับ: {backfill_v}ลบ.ม."

                saved_data = {
                    "หมวด": struct_group_name,
                    "ชื่อ/สัญลักษณ์": e_name,
                    "รายละเอียด": f"{e_type} ({e_w}x{e_l}x{e_h}ม.) | {r_desc}{earth_desc}",
                    "จำนวน": e_qty,
                    "e_w": e_w, "e_l": e_l, "e_h": e_h, "main_size": main_rebar_choice, "main_qty": main_qty,
                    "stirrup_size": stirrup_choice, "stirrup_spacing": stirrup_spacing,
                    "excavation_depth": excavation_depth, "sand_thick": sand_thick, "lean_thick": lean_thick,
                    "คอนกรีต/ปูนปรับระดับ (ลบ.ม.)": v,
                    "เหล็กเสริม (กก.)": r,
                    "ลวดผูกเหล็ก (กก.)": tie_wire,
                    "ไม้แบบ (ตร.ม.)": f,
                    "ตะปูตอกไม้แบบ (กก.)": nails,
                    "ดินขุด (ลบ.ม.)": ex_v,
                    "ทรายรองพื้น (ลบ.ม.)": sand_v,
                    "คอนกรีตหยาบ (ลบ.ม.)": lean_v,
                    "ดินถมกลับ (ลบ.ม.)": backfill_v,
                    "เสาเข็ม (ต้น/ม.)": 0.0, "กระเบื้อง (แผ่น)": 0, "จำนวนอิฐ (ก้อน)": 0, "ปูนก่อ (ถุง)": 0, "ปูนฉาบ (ถุง)": 0, "พื้นที่ทาสี (ตร.ม.)": 0.0
                }
                if is_editing_struct:
                    st.session_state["items"][edit_idx] = saved_data
                    st.session_state["editing_index"] = None
                else:
                    st.session_state["items"].append(saved_data)
                st.rerun()

        with btn_col2:
            if is_editing_struct:
                if st.button("❌ ยกเลิกการแก้ไข", use_container_width=True, key="cancel_struct"):
                    st.session_state["editing_index"] = None
                    st.rerun()

    with col_list:
        render_item_cards("งานโครงสร้าง")

# --- TAB 3: Vision AI Scan (รองรับไฟล์ PDF & รูปภาพ) ---
with tab3:
    st.markdown("### 📷 สแกนอ่านแบบวิศวกรรมด้วย Vision AI")
    uploaded_file = st.file_uploader("เลือกไฟล์แบบขยายโครงสร้าง (รองรับ PDF, PNG, JPG, JPEG)", type=["pdf", "png", "jpg", "jpeg"])
    
    if uploaded_file:
        file_type = uploaded_file.name.split(".")[-1].lower()
        
        if file_type == "pdf":
            st.info(f"📄 อัปโหลดไฟล์ PDF ({uploaded_file.name}) สำเร็จแล้ว พร้อมสำหรับการสแกนด้วย Vision AI")
        else:
            st.image(uploaded_file, caption="ไฟล์แบบที่อัปโหลด", width=400)
        
        if st.button("🚀 สั่ง AI สแกนถอดแบบ", type="primary"):
            if not api_key_input:
                st.error("⚠️ กรุณากรอก Gemini API Key ในแถบด้านข้าง (Sidebar) ก่อนสแกนครับ")
            else:
                try:
                    with st.spinner("🤖 Vision AI กำลังอ่านมิติและรายละเอียดโครงสร้างจากแบบ..."):
                        genai.configure(api_key=api_key_input)
                        
                        # ✅ FIXED: อัปเดตชื่อโมเดลเป็นรุ่นปัจจุบัน gemini-3.8-flash
                        model = genai.GenerativeModel('gemini-3.8-flash')
                        
                        prompt = """
                        คุณคือวิศวกรถอดแบบโครงสร้าง กรุณาอ่านแบบวิศวกรรมในไฟล์และสรุปรายละเอียดออกมาเป็นข้อๆ:
                        1. ประเภทโครงสร้าง (ฐานราก / เสา / คาน / พื้น)
                        2. ขนาดความกว้าง ความยาว ความหนา/ความสูง (เมตร)
                        3. รายละเอียดเหล็กเสริมหลัก และเหล็กปลอก
                        4. จำนวนองค์อาคารที่พบ
                        """
                        
                        if file_type == "pdf":
                            uploaded_file.seek(0)
                            pdf_bytes = uploaded_file.getvalue()
                            contents = [
                                prompt,
                                {
                                    "mime_type": "application/pdf",
                                    "data": pdf_bytes
                                }
                            ]
                            response = model.generate_content(contents)
                        else:
                            uploaded_file.seek(0)
                            image = Image.open(uploaded_file)
                            response = model.generate_content([prompt, image])
                            
                        st.success("✅ ประมวลผลสำเร็จ!")
                        st.markdown(response.text)
                except Exception as e:
                    st.error(f"❌ เกิดข้อผิดพลาดในการเชื่อมต่อ AI: {str(e)}")

# --- TAB 4: Summary BOQ & Material Takeoff ---
with tab4:
    st.markdown("### 📊 ตารางสรุปรายการ BOQX และประมาณราคา (Real-time Dynamic Pricing)")
    current_items = st.session_state.get("items", [])
    
    if not current_items:
        st.info("💡 ยังไม่มีรายการในระบบ กรุณาบันทึกรายการใน Tab ด้านบนก่อน")
    else:
        # --- ส่วนสรุปปริมาณวัสดุรวม (Material Takeoff Summary) ---
        st.markdown("#### 📦 สรุปปริมาณวัสดุก่อสร้างรวมทั้งโครงการ (Material Takeoff)")
        
        tot_concrete = sum(float(item.get("คอนกรีต/ปูนปรับระดับ (ลบ.ม.)", 0) or 0) for item in current_items)
        tot_rebar = sum(float(item.get("เหล็กเสริม (กก.)", 0) or 0) for item in current_items)
        tot_wire = sum(float(item.get("ลวดผูกเหล็ก (กก.)", 0) or 0) for item in current_items)
        tot_formwork = sum(float(item.get("ไม้แบบ (ตร.ม.)", 0) or 0) for item in current_items)
        tot_nails = sum(float(item.get("ตะปูตอกไม้แบบ (กก.)", 0) or 0) for item in current_items)
        tot_excavation = sum(float(item.get("ดินขุด (ลบ.ม.)", 0) or 0) for item in current_items)
        tot_sand = sum(float(item.get("ทรายรองพื้น (ลบ.ม.)", 0) or 0) for item in current_items)
        tot_lean = sum(float(item.get("คอนกรีตหยาบ (ลบ.ม.)", 0) or 0) for item in current_items)
        tot_backfill = sum(float(item.get("ดินถมกลับ (ลบ.ม.)", 0) or 0) for item in current_items)
        tot_bricks = sum(int(item.get("จำนวนอิฐ (ก้อน)", 0) or 0) for item in current_items)
        tot_masonry = sum(int(item.get("ปูนก่อ (ถุง)", 0) or 0) for item in current_items)
        tot_plaster = sum(int(item.get("ปูนฉาบ (ถุง)", 0) or 0) for item in current_items)
        tot_tiles = sum(int(item.get("กระเบื้อง (แผ่น)", 0) or 0) for item in current_items)

        mat_col1, mat_col2, mat_col3, mat_col4 = st.columns(4)
        mat_col1.metric("🧱 คอนกรีตโครงสร้างรวม", f"{tot_concrete:,.2f} ลบ.ม.")
        mat_col2.metric("🔩 เหล็กเสริมรวม", f"{tot_rebar:,.2f} กก.")
        mat_col3.metric("🪵 ไม้แบบรวม", f"{tot_formwork:,.2f} ตร.ม.")
        mat_col4.metric("🪢 ลวดผูกเหล็ก", f"{tot_wire:,.2f} กก.")

        with st.expander("🔍 ดูตารางแสดงปริมาณวัสดุก่อสร้างแยกประเภททั้งหมด", expanded=False):
            mat_summary_data = [
                {"รายการวัสดุ": "คอนกรีตโครงสร้าง", "ปริมาณรวม": f"{tot_concrete:,.2f}", "หน่วย": "ลบ.ม."},
                {"รายการวัสดุ": "เหล็กเสริมโครงสร้าง (รวมเผื่อทาบ)", "ปริมาณรวม": f"{tot_rebar:,.2f}", "หน่วย": "กก."},
                {"รายการวัสดุ": "ลวดผูกเหล็ก #18", "ปริมาณรวม": f"{tot_wire:,.2f}", "หน่วย": "กก."},
                {"รายการวัสดุ": "ไม้แบบหล่อคอนกรีต", "ปริมาณรวม": f"{tot_formwork:,.2f}", "หน่วย": "ตร.ม."},
                {"รายการวัสดุ": "ตะปูตอกไม้แบบ", "ปริมาณรวม": f"{tot_nails:,.2f}", "หน่วย": "กก."},
                {"รายการวัสดุ": "ดินขุดงานฐานราก", "ปริมาณรวม": f"{tot_excavation:,.2f}", "หน่วย": "ลบ.ม."},
                {"รายการวัสดุ": "ทรายหยาบรองพื้น", "ปริมาณรวม": f"{tot_sand:,.2f}", "หน่วย": "ลบ.ม."},
                {"รายการวัสดุ": "คอนกรีตหยาบ Lean 1:3:6", "ปริมาณรวม": f"{tot_lean:,.2f}", "หน่วย": "ลบ.ม."},
                {"รายการวัสดุ": "ดินถมกลับฐานราก", "ปริมาณรวม": f"{tot_backfill:,.2f}", "หน่วย": "ลบ.ม."},
                {"รายการวัสดุ": "อิฐก่อผนัง (รวมเผื่อ)", "ปริมาณรวม": f"{tot_bricks:,}", "หน่วย": "ก้อน"},
                {"รายการวัสดุ": "ปูนก่อสำเร็จรูป (50 กก.)", "ปริมาณรวม": f"{tot_masonry:,}", "หน่วย": "ถุง"},
                {"รายการวัสดุ": "ปูนฉาบสำเร็จรูป (50 กก.)", "ปริมาณรวม": f"{tot_plaster:,}", "หน่วย": "ถุง"},
                {"รายการวัสดุ": "กระเบื้องปูพื้น (รวมเผื่อ)", "ปริมาณรวม": f"{tot_tiles:,}", "หน่วย": "แผ่น"},
            ]
            df_mat_summary = pd.DataFrame(mat_summary_data)
            st.dataframe(df_mat_summary, use_container_width=True)

        st.markdown("---")

        st.markdown("##### 💵 เงื่อนไขการคำนวณราคาประมาณการ")
        opt_col1, opt_col2, opt_col3 = st.columns(3)
        
        boq_type = opt_col1.radio("รูปแบบตาราง BOQ", ["คิดทั้งค่าวัสดุ + ค่าแรง", "คิดเฉพาะค่าวัสดุอย่างเดียว"])
        profit_rate = opt_col2.number_input("เปอร์เซ็นต์กำไร (% Profit)", min_value=0.0, value=15.0, step=1.0) / 100.0
        apply_vat = opt_col3.checkbox("รวมภาษีมูลค่าเพิ่ม (VAT 7%)", value=True)
        
        st.markdown("---")
        
        updated_items = []
        for item in current_items:
            item_copy = item.copy()
            mat_cost = 0.0
            lab_cost = 0.0
            
            ex_vol = float(item_copy.get("ดินขุด (ลบ.ม.)", 0) or 0)
            if ex_vol > 0:
                lab_cost += ex_vol * labour_excavate

            s_vol = float(item_copy.get("ทรายรองพื้น (ลบ.ม.)", 0) or 0)
            if s_vol > 0:
                mat_cost += s_vol * p_sand
                lab_cost += s_vol * labour_sand

            l_vol = float(item_copy.get("คอนกรีตหยาบ (ลบ.ม.)", 0) or 0)
            if l_vol > 0:
                mat_cost += l_vol * p_lean
                lab_cost += l_vol * labour_concrete

            bf_vol = float(item_copy.get("ดินถมกลับ (ลบ.ม.)", 0) or 0)
            if bf_vol > 0:
                lab_cost += bf_vol * labour_backfill

            v = float(item_copy.get("คอนกรีต/ปูนปรับระดับ (ลบ.ม.)", 0) or 0)
            if v > 0:
                mat_cost += v * p_concrete
                lab_cost += v * labour_concrete
                
            r = float(item_copy.get("เหล็กเสริม (กก.)", 0) or 0)
            if r > 0:
                r_price = get_rebar_price(item_copy.get("main_size", "DB16"))
                mat_cost += r * r_price
                lab_cost += r * labour_rebar

            w_kg = float(item_copy.get("ลวดผูกเหล็ก (กก.)", 0) or 0)
            if w_kg > 0:
                mat_cost += w_kg * p_tie_wire

            f = float(item_copy.get("ไม้แบบ (ตร.ม.)", 0) or 0)
            if f > 0:
                mat_cost += f * p_formwork
                lab_cost += f * labour_formwork

            n_kg = float(item_copy.get("ตะปูตอกไม้แบบ (กก.)", 0) or 0)
            if n_kg > 0:
                mat_cost += n_kg * p_nails

            t_count = float(item_copy.get("กระเบื้อง (แผ่น)", 0) or 0)
            if t_count > 0:
                mat_cost += t_count * 35.0
                lab_cost += t_count * labour_tile

            b_count = float(item_copy.get("จำนวนอิฐ (ก้อน)", 0) or 0)
            if b_count > 0:
                p_key = item_copy.get("price_key", "clay")
                b_price = price_clay_brick if p_key == "clay" else (price_aac_block if p_key == "aac" else price_concrete_block)
                mat_cost += b_count * b_price
                lab_cost += b_count * labour_brick

            m_bags = float(item_copy.get("ปูนก่อ (ถุง)", 0) or 0)
            if m_bags > 0:
                mat_cost += m_bags * price_masonry_mortar

            p_bags = float(item_copy.get("ปูนฉาบ (ถุง)", 0) or 0)
            if p_bags > 0:
                mat_cost += p_bags * price_plaster_bag
                p_area = float(item_copy.get("พื้นที่ทาสี (ตร.ม.)", 0) or 0)
                if p_area > 0:
                    lab_cost += p_area * labour_plaster

            item_copy["ค่าวัสดุ (บาท)"] = round(mat_cost, 2)
            item_copy["ค่าแรง (บาท)"] = round(lab_cost, 2)
            updated_items.append(item_copy)

        df_items = pd.DataFrame(updated_items)
        display_cols = ["หมวด", "ชื่อ/สัญลักษณ์", "รายละเอียด", "จำนวน", "ค่าวัสดุ (บาท)", "ค่าแรง (บาท)"]
        
        categories = df_items["หมวด"].unique()
        total_mat_all, total_lab_all = 0.0, 0.0
        
        for cat in categories:
            st.markdown(f"#### 📂 หมวด: {cat}")
            df_cat = df_items[df_items["หมวด"] == cat].copy()
            if "คิดเฉพาะค่าวัสดุ" in boq_type:
                df_cat["รวมเงิน (บาท)"] = df_cat["ค่าวัสดุ (บาท)"]
            else:
                df_cat["รวมเงิน (บาท)"] = df_cat["ค่าวัสดุ (บาท)"] + df_cat["ค่าแรง (บาท)"]
                
            total_mat_all += df_cat["ค่าวัสดุ (บาท)"].sum()
            total_lab_all += df_cat["ค่าแรง (บาท)"].sum()
            
            show_df = df_cat[display_cols + ["รวมเงิน (บาท)"]]
            st.dataframe(show_df, use_container_width=True)

        base_cost = total_mat_all if "คิดเฉพาะค่าวัสดุ" in boq_type else (total_mat_all + total_lab_all)
        profit_amt = base_cost * profit_rate
        subtotal = base_cost + profit_amt
        vat_amt = subtotal * 0.07 if apply_vat else 0.0
        grand_total = subtotal + vat_amt

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("รวมค่าวัสดุ", f"{total_mat_all:,.2f} บาท")
        m2.metric("รวมค่าแรง", f"{total_lab_all:,.2f} บาท" if "คิดทั้งค่าวัสดุ" in boq_type else "ไม่คิดค่าแรง")
        m3.metric(f"กำไร ({profit_rate*100:.0f}%)", f"{profit_amt:,.2f} บาท")
        m4.metric("สุทธิรวมทั้งสิ้น", f"{grand_total:,.2f} บาท")
        
        if st.button("🗑 ล้างรายการทั้งหมด"):
            st.session_state["items"] = []
            st.session_state["editing_index"] = None
            st.rerun()

# --- TAB 5: Project History ---
with tab5:
    st.markdown("### 💾 จัดการบันทึกประวัติโครงการ")
    proj_name = st.text_input("ระบุชื่อโครงการ", value="โครงการบ้านพักอาศัย 2 ชั้น")
    if st.button("💾 บันทึกโครงการนี้"):
        st.session_state["projects"][proj_name] = list(st.session_state.get("items", []))
        st.success(f"บันทึกโครงการ '{proj_name}' สำเร็จแล้ว!")