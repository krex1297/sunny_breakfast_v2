import socket

# 強制使用 IPv4，避免連線 Firebase 時卡住逾時
_old_getaddrinfo = socket.getaddrinfo
def _ipv4_only_getaddrinfo(*args, **kwargs):
    res = [r for r in _old_getaddrinfo(*args, **kwargs) if r[0] == socket.AF_INET]
    return res or _old_getaddrinfo(*args, **kwargs)
socket.getaddrinfo = _ipv4_only_getaddrinfo

import os
from datetime import date
import httpx
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
FIREBASE_API_KEY = os.getenv("FIREBASE_WEB_API_KEY", "")

st.set_page_config(page_title="Sunny 陽光早餐｜店長營運後台", page_icon="🍳", layout="wide")


def firebase_login(email: str, password: str) -> str:
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_API_KEY}"
    res = httpx.post(
        url,
        json={"email": email, "password": password, "returnSecureToken": True},
        timeout=20,
    )
    data = res.json()
    if res.status_code != 200:
        raise RuntimeError(data.get("error", {}).get("message", "登入失敗"))
    return data["idToken"]


def auth_headers():
    return {"Authorization": f"Bearer {st.session_state['token']}"}


# ==========================================
# 側邊欄：店長登入驗證
# ==========================================
if "token" not in st.session_state:
    st.session_state["token"] = None
    st.session_state["user"] = None

with st.sidebar:
    st.title("🍳 Sunny 店長後台")
    if not st.session_state["token"]:
        st.info("請使用店長帳號登入")
        email = st.text_input("電子郵件 (Email)")
        password = st.text_input("密碼", type="password")
        if st.button("登入後台", type="primary", use_container_width=True):
            try:
                token = firebase_login(email.strip(), password.strip())
                me_res = httpx.get(
                    f"{API_BASE}/api/me",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=15,
                )
                me_res.raise_for_status()
                user = me_res.json()
                if user.get("role") != "ADMIN":
                    st.error("此帳號不是店長權限，無法進入後台！")
                else:
                    st.session_state["token"] = token
                    st.session_state["user"] = user
                    st.rerun()
            except Exception as ex:
                st.error(f"登入失敗：{ex}")
    else:
        st.success(f"已登入：{st.session_state['user']['display_name']} (店長)")
        target_date = st.date_input("查詢日期", value=date.today())
        if st.button("🔄 重新整理資料", use_container_width=True):
            st.rerun()
        if st.button("登出", use_container_width=True):
            st.session_state["token"] = None
            st.session_state["user"] = None
            st.rerun()

if not st.session_state["token"]:
    st.warning("👈 請先從左側欄登入店長帳號以查看營運報表與管理訂單。")
    st.stop()

dt_str = target_date.strftime("%Y-%m-%d")

# ==========================================
# 主畫面三大分頁
# ==========================================
tab1, tab2, tab3 = st.tabs(["📊 營運儀表板", "🧾 即時訂單管理", "🍽️ 菜單與庫存管理"])

# ---------- Tab 1：營運儀表板 ----------
with tab1:
    try:
        res = httpx.get(
            f"{API_BASE}/api/admin/dashboard",
            params={"target_date": dt_str},
            headers=auth_headers(),
            timeout=15,
        )
        res.raise_for_status()
        dash = res.json()
        s = dash["summary"]

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("總營收", f"${int(s['revenue']):,}")
        c2.metric("有效訂單數", f"{s['orders']} 筆")
        c3.metric("預估毛利", f"${int(s['profit']):,}")
        c4.metric("毛利率", f"{s['margin']}%")
        c5.metric("平均客單價", f"${s['avg_ticket']}")

        st.divider()
        col_left, col_right = st.columns(2)

        with col_left:
            st.subheader("⏰ 各時段營收分布")
            if dash["hourly"]:
                df_hour = pd.DataFrame(dash["hourly"])
                df_hour["時段"] = df_hour["hour"].apply(lambda h: f"{int(h):02d}:00")
                st.bar_chart(df_hour.set_index("時段")["revenue"])
            else:
                st.info("當日尚無時段資料")

        with col_right:
            st.subheader("🔥 當日熱銷商品")
            if dash["top_items"]:
                df_top = pd.DataFrame(dash["top_items"]).rename(
                    columns={"item_name": "品項", "qty": "售出份數", "revenue": "銷售額"}
                )
                st.dataframe(df_top, use_container_width=True, hide_index=True)
            else:
                st.info("當日尚無銷售紀錄")

        if dash["low_stocks"]:
            st.divider()
            st.subheader("⚠️ 低庫存警示（20 份以下）")
            df_low = pd.DataFrame(dash["low_stocks"]).rename(
                columns={"item_id": "編號", "item_name": "品項", "stock_quantity": "剩餘庫存"}
            )
            st.dataframe(df_low, use_container_width=True, hide_index=True)

    except Exception as ex:
        st.error(f"載入儀表板失敗：{ex}")

# ---------- Tab 2：即時訂單管理 ----------
with tab2:
    st.subheader(f"🧾 {dt_str} 訂單列表")
    try:
        res = httpx.get(
            f"{API_BASE}/api/admin/orders",
            params={"target_date": dt_str},
            headers=auth_headers(),
            timeout=15,
        )
        res.raise_for_status()
        orders = res.json().get("orders", [])

        if not orders:
            st.info("這天還沒有任何訂單喔！")
        else:
            for o in orders:
                with st.container(border=True):
                    c1, c2, c3, c4 = st.columns([2, 2, 1.5, 2.5])
                    c1.markdown(f"**{o['order_number']}** ｜ 顧客：{o['display_name']}")
                    c2.write(f"金額：**${int(o['total_amount'])}** ｜ 時間：{o['created_at'][11:19]}")
                    status_badge = {
                        "PENDING": "🟠 待出餐",
                        "COMPLETED": "🟢 已出餐",
                        "CANCELLED": "🔴 已取消",
                    }.get(o["status"], o["status"])
                    c3.markdown(f"**{status_badge}**")

                    if o["status"] == "PENDING":
                        b1, b2 = c4.columns(2)
                        if b1.button("✅ 出餐", key=f"comp_{o['order_id']}", type="primary", use_container_width=True):
                            r = httpx.patch(
                                f"{API_BASE}/api/admin/orders/{o['order_id']}/complete",
                                headers=auth_headers(),
                                timeout=15,
                            )
                            if r.status_code == 200:
                                st.toast(f"訂單 {o['order_number']} 已出餐！")
                                st.rerun()
                            else:
                                st.error(r.json().get("detail", "操作失敗"))

                        if b2.button("❌ 取消", key=f"canc_{o['order_id']}", use_container_width=True):
                            r = httpx.patch(
                                f"{API_BASE}/api/admin/orders/{o['order_id']}/cancel",
                                headers=auth_headers(),
                                timeout=15,
                            )
                            if r.status_code == 200:
                                st.toast(f"訂單 {o['order_number']} 已取消並還原庫存！")
                                st.rerun()
                            else:
                                st.error(r.json().get("detail", "操作失敗"))
    except Exception as ex:
        st.error(f"載入訂單失敗：{ex}")

# ---------- Tab 3：菜單與庫存管理 ----------
with tab3:
    st.subheader("🍽️ 菜單價格、庫存與上下架管理")
    try:
        res = httpx.get(f"{API_BASE}/api/admin/menu", headers=auth_headers(), timeout=15)
        res.raise_for_status()
        menu_items = res.json().get("menu_items", [])

        for it in menu_items:
            with st.container(border=True):
                c1, c2, c3, c4, c5 = st.columns([2.2, 1.5, 1.5, 1.2, 1.2])
                c1.markdown(f"**{it['item_name']}**  \n`{it['category']}` ｜ 累計已售 {it['total_sold']}")

                new_price = c2.number_input(
                    "售價",
                    min_value=1.0,
                    value=float(it["current_price"]),
                    step=5.0,
                    key=f"price_{it['item_id']}",
                )
                has_stock = it["stock_quantity"] is not None
                new_stock = c3.number_input(
                    "庫存 (-1為不限)",
                    min_value=-1,
                    value=int(it["stock_quantity"]) if has_stock else -1,
                    step=1,
                    key=f"stock_{it['item_id']}",
                )
                new_avail = c4.checkbox("上架供應", value=bool(it["is_available"]), key=f"avail_{it['item_id']}")

                if c5.button("💾 儲存", key=f"save_{it['item_id']}", use_container_width=True):
                    payload = {"current_price": new_price, "is_available": new_avail}
                    if new_stock >= 0:
                        payload["stock_quantity"] = new_stock
                    r = httpx.patch(
                        f"{API_BASE}/api/admin/menu/{it['item_id']}",
                        headers=auth_headers(),
                        json=payload,
                        timeout=15,
                    )
                    if r.status_code == 200:
                        st.toast(f"已更新【{it['item_name']}】！")
                        st.rerun()
                    else:
                        st.error("更新失敗")
    except Exception as ex:
        st.error(f"載入菜單失敗：{ex}")