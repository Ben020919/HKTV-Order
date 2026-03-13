import streamlit as st
from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta, timezone
import threading
import json
import time
import os
import re
import sys
import asyncio
from dotenv import load_dotenv

# 👉 全域變數：檔案路徑
FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'order_data.json')

# 🌟 使用 Streamlit 快取來保存全域的「執行緒通訊信號」
@st.cache_resource
def get_threading_resources():
    return {
        "file_lock": threading.Lock(),
        "trigger_event": threading.Event(),
        "done_event": threading.Event(),
        "is_manual": False,
        "error_msg": None
    }

res = get_threading_resources()
file_lock = res["file_lock"]

# ==========================================
# 1. 爬蟲機器人功能區塊
# ==========================================
def extract_total_count(text):
    if not text: return "0"
    numbers = re.findall(r'\d+', text)
    return numbers[-1] if numbers else "0"

def scrape_single_date(page, date_str):
    base_url = (
        f"https://merchant.shoalter.com/zh/order-management/orders/toship"
        f"?bu=HKTV&deliveryType=STANDARD_DELIVERY&productReadyMethod=STANDARD_DELIVERY_ALL"
        f"&searchType=ORDER_ID&storefrontCodes=H0956004%2CH0956006%2CH0956007%2CH0956008%2CH0956010%2CH0956012"
        f"&dateType=PICK_UP_DATE&startDate={date_str}&endDate={date_str}"
        f"&pageSize=20&pageNumber=1&sortColumn=orderDate&waybillStatuses="
    )
    statuses = [("CONFIRMED", "已建立"), ("ACKNOWLEDGED", "已確認"), ("PICKED", "已出貨")]
    date_data = {"date": date_str}

    page.goto(base_url + "CONFIRMED") 
    page.wait_for_timeout(2500) 
    page.locator('button:has-text("商戶8小時送貨")').click(force=True)
    page.wait_for_timeout(1000) 

    for status_val, status_name in statuses:
        page.locator('div.ant-select-selector:has-text("運單狀態")').click(force=True)
        page.wait_for_timeout(600) 
        
        page.locator('button[data-testid="清除全部"]').click(force=True)
        page.wait_for_timeout(400) 
        
        checkbox = page.locator(f'input[value="{status_val}"]')
        
        if checkbox.count() > 0:
            try:
                if not checkbox.is_checked(): checkbox.click(force=True)
            except Exception:
                checkbox.check(force=True)
                
            page.wait_for_timeout(300)
            page.locator('button[data-testid="套用"]').click(force=True)
            
            page.wait_for_timeout(500)
            try:
                page.wait_for_selector('.ant-spin-spinning', state='hidden', timeout=10000)
            except Exception:
                pass
            page.wait_for_timeout(3000) 
            
            try:
                result_text = page.locator('span:has-text("結果")').last.inner_text(timeout=3000)
                date_data[status_val] = extract_total_count(result_text)
            except Exception:
                date_data[status_val] = "0"
        else:
            date_data[status_val] = "0"
            page.locator('div.ant-select-selector:has-text("運單狀態")').click(force=True)
            page.wait_for_timeout(500)
            
    return date_data

# 🌟 終極單純化：取消歷史鎖定，完全與 HKTVmall 當下狀態絕對同步！
def apply_cumulative_logic(old_data, new_data):
    if old_data.get("date") == new_data.get("date"):
        canceled = int(old_data.get("CANCELED", "0"))
    else:
        canceled = 0
        
    ack = int(new_data.get("ACKNOWLEDGED", "0"))
    picked = int(new_data.get("PICKED", "0"))
    
    # 總目標 = 當下的「已確認」+「已出貨」，不再鎖定歷史高點！
    total_target = ack + picked
        
    new_data["TOTAL_TARGET"] = str(total_target)
    new_data["CANCELED"] = str(canceled)
    return new_data

def scrape_hktvmall(username, password, is_manual=False):
    hk_tz = timezone(timedelta(hours=8))
    now = datetime.now(hk_tz)
    
    today_str = now.strftime("%Y-%m-%d")
    tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True) 
        context = browser.new_context(locale="zh-HK", timezone_id="Asia/Hong_Kong")
        page = context.new_page()
        page.route("**/*.{png,jpg,jpeg,gif,svg}", lambda route: route.abort())

        page.goto("https://merchant.shoalter.com/login") 
        page.locator('#account').fill(username)
        page.locator('#password').fill(password)
        page.locator('button[data-testid="繼續"]').click()
        page.wait_for_timeout(5000) 

        new_today = scrape_single_date(page, today_str)
        new_tomorrow = scrape_single_date(page, tomorrow_str)
        browser.close()

    with file_lock:
        try:
            with open(FILE_PATH, 'r', encoding='utf-8') as f:
                results_data = json.load(f)
        except Exception:
            results_data = {"today": {}, "tomorrow": {}}
            
        results_data["today"] = apply_cumulative_logic(results_data.get("today", {}), new_today)
        results_data["tomorrow"] = apply_cumulative_logic(results_data.get("tomorrow", {}), new_tomorrow)
        results_data["last_updated"] = now.strftime("%Y-%m-%d %H:%M:%S")
        
        if is_manual:
            results_data["status_msg"] = "🎯 最新狀態：手動更新成功！"
        else:
            results_data["status_msg"] = "⚡ 最新狀態：自動背景更新成功！"
        
        with open(FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(results_data, f, ensure_ascii=False, indent=4)

def run_scraper_loop():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    load_dotenv()
    MY_USERNAME = os.getenv("HKTV_USERNAME")
    MY_PASSWORD = os.getenv("HKTV_PASSWORD")
    
    if not MY_USERNAME or not MY_PASSWORD:
        return
        
    while True:
        try:
            scrape_hktvmall(MY_USERNAME, MY_PASSWORD, is_manual=res["is_manual"])
            res["error_msg"] = None
        except Exception as e:
            res["error_msg"] = str(e)
        finally:
            res["is_manual"] = False
            res["done_event"].set()

        res["trigger_event"].wait(timeout=180)
        res["trigger_event"].clear()

# ==========================================
# 2. 介面操作：處理手動取消訂單
# ==========================================
def adjust_cancellation(day_key, cancel_count):
    if cancel_count <= 0: return
    with file_lock:
        try:
            with open(FILE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return
            
        if day_key in data:
            day_data = data[day_key]
            current_canceled = int(day_data.get("CANCELED", "0"))
            
            # 因為現在 TOTAL_TARGET 會自動跟隨 HKTVmall 的真實數字(它會自己變少)
            # 所以我們不需要再手動去扣除 TOTAL_TARGET 了，只要單純增加取消數量即可
            day_data["CANCELED"] = str(current_canceled + cancel_count)
            
        with open(FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

# ==========================================
# 3. Streamlit 介面與背景執行緒管理
# ==========================================

st.set_page_config(page_title="HKTVmall 訂單監控", page_icon="🛍️", layout="wide")

st.markdown("""
    <style>
    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    div[data-testid="metric-container"] {
        background-color: #ffffff;
        border: 1px solid #e0e0e0;
        padding: 10px 15px; 
        border-radius: 20px;
        box-shadow: 2px 2px 5px rgba(0, 0, 0, 0.05);
        text-align: center;
    }
    @media (prefers-color-scheme: dark) {
        div[data-testid="metric-container"] {
            background-color: #1e1e1e;
            border: 1px solid #333333;
            box-shadow: 2px 2px 5px rgba(0, 0, 0, 0.4);
        }
    }
    h1 {
        color: #ff6600;
        margin-bottom: 0rem;
    }
    </style>
""", unsafe_allow_html=True)

@st.cache_resource
def install_browser_and_start_scraper():
    thread = threading.Thread(target=run_scraper_loop, daemon=True)
    thread.start()
    return thread

install_browser_and_start_scraper()

data = {}
try:
    with file_lock:
        if os.path.exists(FILE_PATH) and os.path.getsize(FILE_PATH) > 0:
            with open(FILE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
except Exception:
    data = {}

header_col1, header_col2 = st.columns([4, 1])
with header_col1:
    st.title("🛍️ HKTVmall 智慧訂單監控儀表板")
with header_col2:
    st.write("") 
    if st.button("🔄 手動立即更新", use_container_width=True, help="點擊後將立即強制爬取最新訂單資料"):
        with st.spinner("🚀 機器人出動中，為了確保數據精準，這可能需要 1~2 分鐘，請喝口水稍候..."):
            res["is_manual"] = True
            res["done_event"].clear() 
            res["trigger_event"].set() 
            
            finished = res["done_event"].wait(timeout=180)
            
            if finished:
                if res["error_msg"]:
                    st.error(f"❌ 抓取失敗: {res['error_msg']}")
                else:
                    st.success("✅ 資料更新成功！")
                    time.sleep(1)
                    st.rerun()
            else:
                st.error("❌ 抓取超時，機器人可能遇到問題卡住了。")

st.markdown("---")

def render_order_section(title_prefix, day_key, order_data):
    if not order_data: return
    
    total_target = int(order_data.get('TOTAL_TARGET', '0'))
    picked = int(order_data.get('PICKED', '0'))
    
    with st.container(border=True):
        st.subheader(f"{title_prefix} 📅 {order_data.get('date', '--')}")
        
        col1, col2, col3, col4 = st.columns(4)
        with col1: st.metric("📝 已建立", order_data.get('CONFIRMED', '--'))
        with col2: st.metric("⏳ 已確認", order_data.get('ACKNOWLEDGED', '--'))
        with col3: st.metric("📦 已出貨", f"{picked} / {total_target}")
        with col4: st.metric("❌ 已取消", order_data.get('CANCELED', '0'))
            
        picked_pct = (picked / total_target) if total_target > 0 else 0.0
        st.caption(f"**📦 出貨進度 ({int(picked_pct*100)}%)**")
        st.progress(min(picked_pct, 1.0))
            
        with st.expander(f"⚙️ 紀錄取消訂單"):
            with st.form(key=f"form_{day_key}"):
                st.caption("如果發現客人取消訂單，您可以在此手動紀錄取消的數量（總目標數會自動跟隨系統校正）：")
                input_col, btn_col = st.columns([3, 1])
                with input_col:
                    cancel_qty = st.number_input("增加取消數量：", min_value=1, step=1, key=f"input_{day_key}", label_visibility="collapsed")
                with btn_col:
                    submit = st.form_submit_button("📝 記錄", use_container_width=True)
                
                if submit:
                    adjust_cancellation(day_key, cancel_qty)
                    st.success(f"✅ 已記錄 {cancel_qty} 筆取消訂單！")
                    time.sleep(1)
                    st.rerun()

if "today" in data:
    render_order_section("今日訂單", "today", data.get("today"))
else:
    st.info("🔄 正在抓取今日訂單資料...")
    
st.markdown("<br><br>", unsafe_allow_html=True)

if "tomorrow" in data:
    render_order_section("明日訂單", "tomorrow", data.get("tomorrow"))

if "last_updated" in data:
    status_msg = data.get("status_msg", "🔄 系統初始化中...")
    st.caption(f"🕒 最後更新時間：{data['last_updated']} ｜ {status_msg}")

time.sleep(10)
st.rerun()
