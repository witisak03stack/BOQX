import streamlit as st
import pandas as pd
import math

# ---------------------------------------------------------
# 1. Page Configuration & Custom CSS
# ---------------------------------------------------------
st.set_page_config(
    page_title="AI ถอด BOQ งานโครงสร้าง V3",
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
        padding-top: 1rem !important;
        padding-bottom: 2rem !important;
        max-width: 95% !important;
    }

    /* Top Header Banner */
    .header-banner {
        background: linear-gradient(135deg, #1e3a8a 0%, #3b82f6 100%);
        color: white;
        padding: 18px 24px;
        border-radius: 12px;
        margin-bottom: 20px;
    }
    .header-title {
        font-size: 1.5rem;
        font-weight: 700;
        margin: 0;
    }
    .header-subtitle {
        font-size: 0.95rem;
        opacity: 0.9;
        margin-top: 4px;
    }

    .stButton>button {
        border-radius: 8px;
        font-weight: 500;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 2. Standard Rebar Weight Dictionary (kg/m)
# ---------------------------------------------------------
REBAR_WEIGHT = {
    "RB6": 0.222,
    "RB9": 0.499,
    "DB12": 0.888,
    "DB16": 1.580,
    "DB20": 2.470,
    "DB25": 3.850
}
REBAR_LIST = list(REBAR_WEIGHT.keys())

# ---------------------------------------------------------
# 3. Session State Management
# ---------------------------------------------------------
if "projects" not in st.session_state:
    st.session_state["projects"] = []

if "current_project_id" not in st.session_state:
    st.session_state["current_project_id"] = None

# Dynamic Rebar State Containers
if "footing_rebars" not in st.session_state:
    st.session_state["footing_rebars"] = [
        {"type": "DB12", "mode": "จำนวน (เส้น)", "val": 10.0, "len": 1.50},
        {"type": "DB12", "mode": "จำนวน (เส้น)", "val": 10.0, "len": 1.50}
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
        st.session_state["projects"][p_idx]["items"].append(item_data)
        st.success(f"บันทึกรายการ '{item_data['รายการ']}' เรียบร้อยแล้ว!")
    else:
        st.error("⚠️ กรุณาสร้างหรือเลือกโครงการก่อนทำการบันทึกข้อมูล!")

# ---------------------------------------------------------
# 4. Sidebar Price & Material Settings
# ---------------------------------------------------------
with st.sidebar:
    st.title("⚙️ ตั้งค่าราคาและค่าแรง")
    
    with st.expander("🚜 ค่าแรงงานดินขุด-ดินถม", expanded=True):
        cost_excavation = st.number_input("ค่าขุดดิน (บาท/ลบ.ม.)", value=120.0, step=10.0)
        cost_backfill = st.number_input("ค่าถมดินย้อนกลับ (บาท/ลบ.ม.)", value=80.0, step=10.0)

    with st.expander("🏗️ ราคาวัสดุโครงสร้าง", expanded=False):
        p_concrete = st.number_input("คอนกรีต 240 ksc (บาท/ลบ.ม.)", value=2450.0, step=50.0)
        p_db12 = st.number_input("เหล็ก DB12/DB16/DB20 (บาท/กก.)", value=31.0, step=0.5)
        p_rb9 = st.number_input("เหล็ก RB6/RB9/โครงสร้าง (บาท/กก.)", value=33.0, step=0.5)
        p_formwork = st.number_input("ไม้แบบ (บาท/ตร.ม.)", value=380.0, step=10.0)
        p_roof_tile = st.number_input("กระเบื้องหลังคา/เมทัลชีท (บาท/ตร.ม.)", value=280.0, step=10.0)
        p_roof_cap = st.number_input("ครอบสันหลังคา/ตะเข้สัน (บาท/เมตร)", value=180.0, step=10.0)

    with st.expander("📌 ราคาและค่าแรงเสาเข็ม", expanded=False):
        p_pile_hex = st.number_input("เข็มหกเหลี่ยมกลวง (บาท/ม.)", value=120.0, step=10.0)
        p_pile_i18 = st.number_input("เข็ม I-18 (บาท/ม.)", value=220.0, step=10.0)
        p_pile_i22 = st.number_input("เข็ม I-22 (บาท/ม.)", value=280.0, step=10.0)
        p_pile_i26 = st.number_input("เข็ม I-26 (บาท/ม.)", value=350.0, step=10.0)
        p_pile_bored35 = st.number_input("เข็มเจาะ Ø0.35 ม. (บาท/ม.)", value=650.0, step=20.0)
        labour_pile_press = st.number_input("ค่าแรงกด/ตอกเข็ม (บาท/ม.)", value=80.0, step=5.0)
        labour_pile_bored = st.number_input("ค่าแรงเจาะเสาเข็ม (บาท/ม.)", value=250.0, step=10.0)

    with st.expander("🔨 ค่าแรงงานโครงสร้างทั่วไป", expanded=False):
        labour_concrete = st.number_input("ค่าแรงเทคอนกรีต (บาท/ลบ.ม.)", value=350.0, step=10.0)
        labour_rebar = st.number_input("ค่าแรงผูกเหล็ก/โครงเหล็ก (บาท/กก.)", value=8.5, step=0.5)
        labour_formwork = st.number_input("ค่าแรงประกอบไม้แบบ (บาท/ตร.ม.)", value=150.0, step=10.0)
        labour_roof_tile = st.number_input("ค่าแรงมุงหลังคา (บาท/ตร.ม.)", value=120.0, step=10.0)

    with st.expander("📉 เปอร์เซ็นต์สูญเสีย (% Wastage)", expanded=False):
        waste_concrete = st.number_input("เผื่อคอนกรีต (%)", value=5.0) / 100.0
        waste_rebar = st.number_input("เผื่อเหล็กเส้น/โครงสร้าง (%)", value=10.0) / 100.0
        waste_formwork = st.number_input("เผื่อไม้แบบ (%)", value=15.0) / 100.0
        waste_roof = st.number_input("เผื่อหลังคา (%)", value=7.0) / 100.0

pile_price_map = {
    "เสาเข็มหกเหลี่ยมกลวง": (p_pile_hex, labour_pile_press),
    "เสาเข็ม I-18": (p_pile_i18, labour_pile_press),
    "เสาเข็ม I-22": (p_pile_i22, labour_pile_press),
    "เสาเข็ม I-26": (p_pile_i26, labour_pile_press),
    "เสาเข็มเจาะ Ø 0.35 ม.": (p_pile_bored35, labour_pile_bored)
}

# ---------------------------------------------------------
# 5. Header Banner
# ---------------------------------------------------------
st.markdown(f"""
<div class="header-banner">
    <div class="header-title">⚙️ ระบบถอดปริมาณงานโครงสร้าง (Takeoff)</div>
    <div class="header-subtitle">📁 โครงการปัจจุบัน: <b>{active_proj_name}</b></div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# 6. Navigation Tabs
# ---------------------------------------------------------
tabs = st.tabs([
    "📁 โครงการ", 
    "🦶 ฐานราก", 
    "🏛️ เสา", 
    "↔ คาน", 
    "🧱 พื้น", 
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
                    new_id = max(existing_ids, default=0) + 1
                    st.session_state["projects"].append({
                        "id": new_id,
                        "name": new_name.strip(),
                        "location": new_loc.strip(),
                        "items": []
                    })
                    st.session_state["current_project_id"] = new_id
                    st.success(f"สร้างโครงการ '{new_name}' เรียบร้อยแล้ว!")
                    st.rerun()

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
    f_w = m1.number_input("ความกว้างฐานราก (เมตร)", value=1.20, step=0.1, key="f_w")
    f_l = m2.number_input("ความยาวฐานราก (เมตร)", value=1.20, step=0.1, key="f_l")
    f_h = m3.number_input("ความหนาฐานราก (เมตร)", value=0.35, step=0.05, key="f_h")
    f_depth = m4.number_input("ระดับความลึกดินขุด H (เมตร)", value=1.50, step=0.1, key="f_depth")

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
        st.session_state["footing_rebars"].append({"type": "DB12", "mode": "จำนวน (เส้น)", "val": 1.0, "len": 1.50})
        st.rerun()

    f_rebars_to_remove = []
    tot_footing_rebar_weight = 0.0

    for idx, r in enumerate(st.session_state["footing_rebars"]):
        c1, c2, c3, c4, c5 = st.columns([1.5, 2, 2, 2, 0.5])
        r["type"] = c1.selectbox(f"ชนิดเหล็ก #{idx+1}", REBAR_LIST, index=REBAR_LIST.index(r["type"]) if r["type"] in REBAR_LIST else 2, key=f"f_type_{idx}")
        r["mode"] = c2.selectbox(f"โหมด #{idx+1}", ["จำนวน (เส้น)", "ระยะห่าง (@ ม.)"], index=0 if r["mode"] == "จำนวน (เส้น)" else 1, key=f"f_mode_{idx}")
        r["val"] = c3.number_input(f"ค่า #{idx+1}", value=float(r["val"]), key=f"f_val_{idx}")
        r["len"] = c4.number_input(f"ยาว (ม.) #{idx+1}", value=float(r["len"]), key=f"f_len_{idx}")
        
        if c5.button("🗑", key=f"del_f_rebar_{idx}"):
            f_rebars_to_remove.append(idx)

        if r["mode"] == "จำนวน (เส้น)":
            total_len_row = r["val"] * r["len"]
        else:
            calc_count = (math.ceil(f_l / r["val"]) + 1) if r["val"] > 0 else 0
            total_len_row = calc_count * r["len"]
        
        tot_footing_rebar_weight += (total_len_row * REBAR_WEIGHT[r["type"]])

    if f_rebars_to_remove:
        st.session_state["footing_rebars"] = [item for i, item in enumerate(st.session_state["footing_rebars"]) if i not in f_rebars_to_remove]
        st.rerun()

    st.markdown("---")
    if st.button("➕ บันทึกงานฐานราก", type="primary", key="btn_save_footing"):
        vol_concrete = (f_w * f_l * f_h * f_qty) * (1 + waste_concrete)
        formwork = (2 * (f_w + f_l) * f_h * f_qty) * (1 + waste_formwork)
        rebar_weight = tot_footing_rebar_weight * f_qty * (1 + waste_rebar)

        excavation_area_per_footing = (f_w * f_l) * 1.30
        vol_excavation = excavation_area_per_footing * f_depth * f_qty
        vol_backfill = max(0.0, vol_excavation - (f_w * f_l * f_h * f_qty))

        mat_c = vol_concrete * p_concrete + rebar_weight * p_db12 + formwork * p_formwork
        lab_c = vol_concrete * labour_concrete + rebar_weight * labour_rebar + formwork * labour_formwork

        add_takeoff_item({
            "หมวด": "งานดินขุด-ดินถม",
            "รายการ": f"งานดินสำหรับฐานราก {f_name}",
            "รายละเอียด": f"ดินขุดเผื่อ 30%: {vol_excavation:.2f} ลบ.ม. | ดินถมย้อนกลับ: {vol_backfill:.2f} ลบ.ม.",
            "จำนวน": f_qty,
            "คอนกรีต (ลบ.ม.)": 0.0,
            "เหล็ก (กก.)": 0.0,
            "ไม้แบบ (ตร.ม.)": 0.0,
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
            "คอนกรีต (ลบ.ม.)": round(vol_concrete, 2),
            "เหล็ก (กก.)": round(rebar_weight, 2),
            "ไม้แบบ (ตร.ม.)": round(formwork, 2),
            "ค่าวัสดุ (บาท)": round(mat_c, 2),
            "ค่าแรง (บาท)": round(lab_c, 2)
        })

# =========================================================
# TAB 3: 🏛 เสา (แก้ไข Bug f-string key)
# =========================================================
with tabs[2]:
    st.subheader(f"🏛️ ถอดปริมาณงานเสา — [{active_proj_name}]")
    
    c1, c2, c3 = st.columns([1.5, 1.5, 1])
    col_name = c1.text_input("ชื่อ/สัญลักษณ์เสา", value="C1", key="col_name")
    col_level = c2.selectbox("ตำแหน่ง/ชั้นของเสา", ["เสาตอม่อ (Stub Column)", "เสาชั้น 1", "เสาชั้น 2", "เสาชั้น 3", "เสาชั้นหลังคา"], key="col_level")
    col_qty = c3.number_input("จำนวน (ต้น)", min_value=1, value=1, key="col_qty")
    
    cm1, cm2, cm3 = st.columns(3)
    col_w = cm1.number_input("กว้างเสา (เมตร)", value=0.20, step=0.05, key="col_w")
    col_l = cm2.number_input("ยาวเสา (เมตร)", value=0.20, step=0.05, key="col_l")
    col_h = cm3.number_input("ความสูงเสา (เมตร)", value=3.00, step=0.10, key="col_h")

    st.markdown("---")
    head_c, btn_c = st.columns([3, 1])
    head_c.markdown("#### 🥞 เหล็กเสริมเสา")
    if btn_c.button("➕ เพิ่มเหล็กเสา", key="btn_add_col_rebar"):
        st.session_state["column_rebars"].append({"pos": "เหล็กแกน", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 4.0, "len": col_h + 0.5})
        st.rerun()

    c_rebars_to_remove = []
    tot_col_rebar_weight = 0.0

    col_pos_options = ["เหล็กแกน", "เหล็กปลอก", "เหล็กเสริมพิเศษ"]

    for idx, r in enumerate(st.session_state["column_rebars"]):
        c1, c2, c3, c4, c5, c6 = st.columns([1.5, 1.2, 1.8, 1.5, 1.5, 0.5])
        
        # แก้ไขจุดเกิด Bug (เติม f ด้านหน้า string)
        pos_idx = col_pos_options.index(r["pos"]) if r["pos"] in col_pos_options else 0
        r["pos"] = c1.selectbox(f"ตำแหน่ง #{idx+1}", col_pos_options, index=pos_idx, key=f"c_pos_{idx}")
        
        type_idx = REBAR_LIST.index(r["type"]) if r["type"] in REBAR_LIST else 2
        r["type"] = c2.selectbox(f"เหล็ก #{idx+1}", REBAR_LIST, index=type_idx, key=f"c_type_{idx}")
        
        mode_idx = 0 if r["mode"] == "จำนวน (เส้น)" else 1
        r["mode"] = c3.selectbox(f"โหมด #{idx+1}", ["จำนวน (เส้น)", "ระยะห่าง (@ ม.)"], index=mode_idx, key=f"c_mode_{idx}")
        
        r["val"] = c4.number_input(f"ค่า #{idx+1}", value=float(r["val"]), key=f"c_val_{idx}")
        r["len"] = c5.number_input(f"ยาว (ม.) #{idx+1}", value=float(r["len"]), key=f"c_len_{idx}")
        
        if c6.button("🗑", key=f"del_c_rebar_{idx}"):
            c_rebars_to_remove.append(idx)

        if r["mode"] == "จำนวน (เส้น)":
            total_len_row = r["val"] * r["len"]
        else:
            calc_count = (math.ceil(col_h / r["val"]) + 1) if r["val"] > 0 else 0
            total_len_row = calc_count * r["len"]
        
        tot_col_rebar_weight += (total_len_row * REBAR_WEIGHT[r["type"]])

    if c_rebars_to_remove:
        st.session_state["column_rebars"] = [item for i, item in enumerate(st.session_state["column_rebars"]) if i not in c_rebars_to_remove]
        st.rerun()

    st.markdown("---")
    if st.button("➕ บันทึกงานเสา", type="primary", key="btn_save_col"):
        vol = (col_w * col_l * col_h * col_qty) * (1 + waste_concrete)
        form = (2 * (col_w + col_l) * col_h * col_qty) * (1 + waste_formwork)
        rebar_weight = tot_col_rebar_weight * col_qty * (1 + waste_rebar)

        mat_c = vol * p_concrete + rebar_weight * p_db12 + form * p_formwork
        lab_c = vol * labour_concrete + rebar_weight * labour_rebar + form * labour_formwork

        add_takeoff_item({
            "หมวด": "งานเสา",
            "รายการ": f"{col_name} ({col_level})",
            "รายละเอียด": f"[{col_level}] ขนาด {col_w:.2f}x{col_l:.2f}ม. สูง {col_h:.2f}ม. ({col_qty} ต้น)",
            "จำนวน": col_qty,
            "คอนกรีต (ลบ.ม.)": round(vol, 2),
            "เหล็ก (กก.)": round(rebar_weight, 2),
            "ไม้แบบ (ตร.ม.)": round(form, 2),
            "ค่าวัสดุ (บาท)": round(mat_c, 2),
            "ค่าแรง (บาท)": round(lab_c, 2)
        })

# =========================================================
# TAB 4: ↔️ คาน
# =========================================================
with tabs[3]:
    st.subheader(f"↔️ ถอดปริมาณงานคาน — [{active_proj_name}]")
    
    b1, b2, b3 = st.columns([1.5, 1.5, 1])
    beam_name = b1.text_input("ชื่อ/สัญลักษณ์คาน", value="B1", key="b_name")
    beam_level = b2.selectbox("ตำแหน่ง/ระดับชั้นของคาน", ["คานคอดิน (GB)", "คานชั้น 1 (B1)", "คานชั้น 2 (B2)", "คานชั้น 3 (B3)", "คานหลังคา (RB)"], key="beam_level")
    beam_qty = b3.number_input("จำนวน (คาน)", min_value=1, value=1, key="b_qty")
    
    bm1, bm2, bm3 = st.columns(3)
    beam_w = bm1.number_input("ความกว้างคาน (เมตร)", value=0.20, step=0.05, key="b_w")
    beam_h = bm2.number_input("ความลึก/สูงคาน (เมตร)", value=0.40, step=0.05, key="b_h")
    beam_l = bm3.number_input("ความยาวคาน (เมตร)", value=4.00, step=0.10, key="b_l")

    st.markdown("---")
    head_b, btn_b = st.columns([3, 1])
    head_b.markdown("#### 🥞 เหล็กเสริมคาน")
    if btn_b.button("➕ เพิ่มเหล็กคาน", key="btn_add_beam_rebar"):
        st.session_state["beam_rebars"].append({"pos": "เหล็กบน", "type": "DB12", "mode": "จำนวน (เส้น)", "val": 2.0, "len": beam_l + 0.6})
        st.rerun()

    b_rebars_to_remove = []
    tot_beam_rebar_weight = 0.0

    beam_pos_options = ["เหล็กบน", "เหล็กล่าง", "เหล็กเสริมพิเศษ", "เหล็กปลอก"]

    for idx, r in enumerate(st.session_state["beam_rebars"]):
        c1, c2, c3, c4, c5, c6 = st.columns([1.5, 1.2, 1.8, 1.5, 1.5, 0.5])
        
        pos_idx = beam_pos_options.index(r["pos"]) if r["pos"] in beam_pos_options else 0
        r["pos"] = c1.selectbox(f"ตำแหน่ง #{idx+1}", beam_pos_options, index=pos_idx, key=f"b_pos_{idx}")
        
        type_idx = REBAR_LIST.index(r["type"]) if r["type"] in REBAR_LIST else 2
        r["type"] = c2.selectbox(f"เหล็ก #{idx+1}", REBAR_LIST, index=type_idx, key=f"b_type_{idx}")
        
        mode_idx = 0 if r["mode"] == "จำนวน (เส้น)" else 1
        r["mode"] = c3.selectbox(f"โหมด #{idx+1}", ["จำนวน (เส้น)", "ระยะห่าง (@ ม.)"], index=mode_idx, key=f"b_mode_{idx}")
        
        r["val"] = c4.number_input(f"ค่า #{idx+1}", value=float(r["val"]), key=f"b_val_{idx}")
        r["len"] = c5.number_input(f"ยาว (ม.) #{idx+1}", value=float(r["len"]), key=f"b_len_{idx}")
        
        if c6.button("🗑", key=f"del_b_rebar_{idx}"):
            b_rebars_to_remove.append(idx)

        if r["mode"] == "จำนวน (เส้น)":
            total_len_row = r["val"] * r["len"]
        else:
            calc_count = (math.ceil(beam_l / r["val"]) + 1) if r["val"] > 0 else 0
            total_len_row = calc_count * r["len"]
        
        tot_beam_rebar_weight += (total_len_row * REBAR_WEIGHT[r["type"]])

    if b_rebars_to_remove:
        st.session_state["beam_rebars"] = [item for i, item in enumerate(st.session_state["beam_rebars"]) if i not in b_rebars_to_remove]
        st.rerun()

    st.markdown("---")
    if st.button("➕ บันทึกงานคาน", type="primary", key="btn_save_beam"):
        vol = (beam_w * beam_h * beam_l * beam_qty) * (1 + waste_concrete)
        form = ((2 * beam_h + beam_w) * beam_l * beam_qty) * (1 + waste_formwork)
        rebar_weight = tot_beam_rebar_weight * beam_qty * (1 + waste_rebar)

        mat_c = vol * p_concrete + rebar_weight * p_db12 + form * p_formwork
        lab_c = vol * labour_concrete + rebar_weight * labour_rebar + form * labour_formwork

        add_takeoff_item({
            "หมวด": "งานคาน",
            "รายการ": f"{beam_name} ({beam_level})",
            "รายละเอียด": f"[{beam_level}] ขนาด {beam_w:.2f}x{beam_h:.2f}ม. ยาว {beam_l:.2f}ม. ({beam_qty} คาน)",
            "จำนวน": beam_qty,
            "คอนกรีต (ลบ.ม.)": round(vol, 2),
            "เหล็ก (กก.)": round(rebar_weight, 2),
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
    slab_w = sm1.number_input("ความกว้างพื้น (เมตร)", value=3.00, step=0.10, key="s_w")
    slab_l = sm2.number_input("ความยาวพื้น (เมตร)", value=4.00, step=0.10, key="s_l")
    slab_h = sm3.number_input("ความหนาพื้น (เมตร)", value=0.10, step=0.01, key="s_h")

    st.markdown("#### 🥞 เหล็กเสริมพื้น (ตะแกรง)")
    s_re1, s_re2 = st.columns(2)
    s_rebar_type = s_re1.selectbox("ขนาดเหล็กเสริมพื้น", REBAR_LIST, index=1, key="s_rebar_type")
    s_rebar_spacing = s_re2.number_input("ระยะห่าง @ (เมตร)", value=0.20, step=0.01, key="s_rebar_spacing")

    if st.button("➕ บันทึกงานพื้น", type="primary", key="btn_save_slab"):
        area = slab_w * slab_l * slab_qty
        vol = (area * slab_h) * (1 + waste_concrete)
        form = area * (1 + waste_formwork)

        num_bars_w = math.ceil(slab_l / s_rebar_spacing) + 1
        num_bars_l = math.ceil(slab_w / s_rebar_spacing) + 1
        total_slab_rebar_len = ((num_bars_w * slab_w) + (num_bars_l * slab_l)) * slab_qty
        rebar_weight = total_slab_rebar_len * REBAR_WEIGHT[s_rebar_type] * (1 + waste_rebar)

        p_s_rebar = p_db12 if "DB" in s_rebar_type else p_rb9
        mat_c = vol * p_concrete + rebar_weight * p_s_rebar + form * p_formwork
        lab_c = vol * labour_concrete + rebar_weight * labour_rebar + form * labour_formwork

        add_takeoff_item({
            "หมวด": "งานพื้น",
            "รายการ": slab_name,
            "รายละเอียด": f"ขนาด {slab_w:.2f}x{slab_l:.2f}ม. หนา {slab_h:.2f}ม. | เหล็ก {s_rebar_type}@{s_rebar_spacing:.2f}ม.",
            "จำนวน": slab_qty,
            "คอนกรีต (ลบ.ม.)": round(vol, 2),
            "เหล็ก (กก.)": round(rebar_weight, 2),
            "ไม้แบบ (ตร.ม.)": round(form, 2),
            "ค่าวัสดุ (บาท)": round(mat_c, 2),
            "ค่าแรง (บาท)": round(lab_c, 2)
        })

# =========================================================
# TAB 6: 🪜 บันได
# =========================================================
with tabs[5]:
    st.subheader(f"🪜 ถอดปริมาณงานบันได คสล. — [{active_proj_name}]")
    
    st1, st2 = st.columns(2)
    stair_name = st1.text_input("ชื่อ/สัญลักษณ์บันได", value="ST1", key="stair_name")
    stair_qty = st2.number_input("จำนวนชุดบันได", min_value=1, value=1, key="stair_qty")
    
    st_c1, st_c2, st_c3 = st.columns(3)
    stair_w = st_c1.number_input("ความกว้างบันได (เมตร)", value=1.20, step=0.05, key="stair_w")
    num_steps = st_c2.number_input("จำนวนขั้นบันได (ขั้น)", min_value=1, value=10, key="num_steps")
    step_r_cm = st_c3.number_input("ความสูงขั้นบันได (ซม.)", value=17.5, step=0.5, key="step_r_cm")
    
    st_c4, st_c5 = st.columns(2)
    step_t_cm = st_c4.number_input("ความกว้างเหยียบ (ซม.)", value=25.0, step=1.0, key="step_t_cm")
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

        run_len = num_steps * step_t
        rise_len = num_steps * step_r
        inclined_len = math.sqrt(run_len**2 + rise_len**2)

        vol_steps = (0.5 * step_r * step_t * stair_w) * num_steps
        vol_slab = (inclined_len * stair_w * slab_th)
        vol_landing = (land_w * land_l * land_th) if has_landing else 0.0
        tot_vol = (vol_steps + vol_slab + vol_landing) * stair_qty * (1 + waste_concrete)
        
        form_bottom = inclined_len * stair_w
        form_risers = num_steps * step_r * stair_w
        form_sides = (0.5 * run_len * rise_len) * 2
        form_landing = (land_w * land_l) + (2 * (land_w + land_l) * land_th) if has_landing else 0.0
        tot_form = (form_bottom + form_risers + form_sides + form_landing) * stair_qty * (1 + waste_formwork)

        num_main_bars = math.ceil(stair_w / stair_rebar_spacing) + 1
        num_cross_bars = math.ceil(inclined_len / stair_rebar_spacing) + 1
        stair_rebar_len = (num_main_bars * (inclined_len + 0.60)) + (num_cross_bars * stair_w)
        tot_rebar_weight = stair_rebar_len * stair_qty * REBAR_WEIGHT[stair_rebar_type] * (1 + waste_rebar)

        p_stair_rebar = p_db12 if "DB" in stair_rebar_type else p_rb9
        mat_c = tot_vol * p_concrete + tot_rebar_weight * p_stair_rebar + tot_form * p_formwork
        lab_c = tot_vol * labour_concrete + tot_rebar_weight * labour_rebar + tot_form * labour_formwork

        detail_text = f"บันได {num_steps} ขั้น (กว้าง {stair_w:.2f}ม.) | เหล็ก {stair_rebar_type}@{stair_rebar_spacing:.2f}ม."
        if has_landing:
            detail_text += f" + ชานพัก {land_w:.2f}x{land_l:.2f}ม."

        add_takeoff_item({
            "หมวด": "งานบันได",
            "รายการ": stair_name,
            "รายละเอียด": detail_text,
            "จำนวน": stair_qty,
            "คอนกรีต (ลบ.ม.)": round(tot_vol, 2),
            "เหล็ก (กก.)": round(tot_rebar_weight, 2),
            "ไม้แบบ (ตร.ม.)": round(tot_form, 2),
            "ค่าวัสดุ (บาท)": round(mat_c, 2),
            "ค่าแรง (บาท)": round(lab_c, 2)
        })

# =========================================================
# TAB 7: ⛺ หลังคา
# =========================================================
with tabs[6]:
    st.subheader(f"⛺ ถอดปริมาณงานหลังคา (รองรับปั้นหยา/หลายจั่ว) — [{active_proj_name}]")
    
    r1, r2 = st.columns(2)
    roof_name = r1.text_input("ชื่อ/สัญลักษณ์หลังคา", value="R1", key="roof_name")
    roof_type = r2.selectbox("ประเภททรงหลังคา & วัสดุมุง", [
        "หลังคาทรงปั้นหยา / หลายจั่ว (กระเบื้องซีแพค/เพรสทีจ)",
        "หลังคาทรงจั่ว / หมาแหงน (กระเบื้องลอนคู่)",
        "หลังคาทรงจั่ว / หมาแหงน (เมทัลชีท)"
    ])

    rc1, rc2, rc3 = st.columns(3)
    plan_area = rc1.number_input("พื้นที่ราบรวมจากผัง Roof Plan (ตร.ม.)", value=120.0, step=5.0, key="plan_area")
    roof_pitch = rc2.number_input("ความชันหลังคา (องศา °)", min_value=0.0, max_value=85.0, value=30.0, step=1.0, key="roof_pitch")
    ridge_len = rc3.number_input("ความยาวครอบสันหลังคา/ตะเข้สันรวม (เมตร)", value=25.0, step=1.0, key="ridge_len")

    rad = math.radians(roof_pitch)
    cos_val = math.cos(rad)
    slope_factor = 1.0 / cos_val if cos_val > 0.001 else 1.0
    real_roof_area = plan_area * slope_factor

    if st.button("➕ บันทึกงานหลังคา", type="primary", key="btn_save_roof"):
        tile_area = real_roof_area * (1 + waste_roof)
        steel_factor = 18.0 if "เมทัลชีท" in roof_type else 26.0
        tot_steel_weight = real_roof_area * steel_factor * (1 + waste_rebar)

        mat_cost = (tile_area * p_roof_tile) + (tot_steel_weight * p_rb9) + (ridge_len * p_roof_cap)
        lab_cost = (tile_area * labour_roof_tile) + (tot_steel_weight * labour_rebar)

        add_takeoff_item({
            "หมวด": "งานหลังคา",
            "รายการ": roof_name,
            "รายละเอียด": f"พื้นที่มุงเอียง {real_roof_area:.1f} ตร.ม. (ครอบ {ridge_len:.1f} ม.)",
            "จำนวน": 1,
            "คอนกรีต (ลบ.ม.)": 0.0,
            "เหล็ก (กก.)": round(tot_steel_weight, 2),
            "ไม้แบบ (ตร.ม.)": 0.0,
            "ค่าวัสดุ (บาท)": round(mat_cost, 2),
            "ค่าแรง (บาท)": round(lab_cost, 2)
        })

# =========================================================
# TAB 8: 🧮 คำนวณ
# =========================================================
with tabs[7]:
    st.subheader(f"🧮 สรุปปริมาณวัสดุแยกตามหมวด — [{active_proj_name}]")
    items = current_proj.get("items", []) if current_proj else []
    if items:
        df = pd.DataFrame(items)
        st.dataframe(df, use_container_width=True)
        
        c_del1, c_del2 = st.columns([2.5, 9.5])
        if c_del1.button("🗑 ลบรายการทั้งหมดในโครงการนี้", type="secondary"):
            p_idx = get_current_project_index()
            if p_idx != -1:
                st.session_state["projects"][p_idx]["items"] = []
                st.rerun()
    else:
        st.info("ยังไม่มีรายการถอดแบบในโครงการนี้ กรุณากรอกข้อมูลใน Tab หมวดงานต่างๆ ด้านบน")

# =========================================================
# TAB 9: 📋 BOQ
# =========================================================
with tabs[8]:
    st.subheader(f"📋 ตาราง BOQ (Bill of Quantities) — [{active_proj_name}]")
    items = current_proj.get("items", []) if current_proj else []
    if items:
        df = pd.DataFrame(items)
        df["รวมเป็นเงิน (บาท)"] = df["ค่าวัสดุ (บาท)"] + df["ค่าแรง (บาท)"]
        
        show_cols = ["หมวด", "รายการ", "รายละเอียด", "จำนวน", "ค่าวัสดุ (บาท)", "ค่าแรง (บาท)", "รวมเป็นเงิน (บาท)"]
        
        formatted_df = df[show_cols].copy()
        st.dataframe(
            formatted_df.style.format({
                "ค่าวัสดุ (บาท)": "{:,.2f}",
                "ค่าแรง (บาท)": "{:,.2f}",
                "รวมเป็นเงิน (บาท)": "{:,.2f}"
            }), 
            use_container_width=True
        )
    else:
        st.warning("ยังไม่มีรายการ BOQ ในโครงการนี้")

# =========================================================
# TAB 10: 📊 สรุป
# =========================================================
with tabs[9]:
    st.subheader(f"📊 สรุปงบประมาณรวมทั้งโครงการ — [{active_proj_name}]")
    items = current_proj.get("items", []) if current_proj else []
    if items:
        df = pd.DataFrame(items)
        tot_mat = df["ค่าวัสดุ (บาท)"].sum()
        tot_lab = df["ค่าแรง (บาท)"].sum()
        grand_total = tot_mat + tot_lab

        m1, m2, m3 = st.columns(3)
        m1.metric("รวมค่าวัสดุทั้งหมด", f"฿{tot_mat:,.2f}")
        m2.metric("รวมค่าแรงทั้งหมด", f"฿{tot_lab:,.2f}")
        m3.metric("งบประมาณรวมทั้งสิ้น", f"฿{grand_total:,.2f}")
    else:
        st.info("กรุณาเพิ่มรายการถอดแบบเพื่อดูสรุปภาพรวมงบประมาณ")
