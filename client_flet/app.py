import socket

# 強制使用 IPv4，避免連線 Firebase / Google 時卡住逾時
_old_getaddrinfo = socket.getaddrinfo
def _ipv4_only_getaddrinfo(*args, **kwargs):
    return [r for r in _old_getaddrinfo(*args, **kwargs) if r[0] == socket.AF_INET]
socket.getaddrinfo = _ipv4_only_getaddrinfo

import os
import httpx
import flet as ft
from dotenv import load_dotenv

load_dotenv()

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
FIREBASE_API_KEY = os.getenv("FIREBASE_WEB_API_KEY", "")


def firebase_auth_request(mode: str, email: str, password: str, display_name: str = ""):
    """呼叫 Firebase Auth REST API 進行註冊或登入"""
    endpoint = "signUp" if mode == "register" else "signInWithPassword"
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:{endpoint}?key={FIREBASE_API_KEY}"
    res = httpx.post(
        url,
        json={"email": email, "password": password, "returnSecureToken": True},
        timeout=20,
    )
    data = res.json()
    if res.status_code != 200:
        err = data.get("error", {}).get("message", "登入失敗")
        raise RuntimeError(err)

    id_token = data["idToken"]

    # 註冊時若有填寫暱稱，同步更新到 Firebase 並換取含暱稱的新 Token
    if mode == "register" and display_name.strip():
        upd = httpx.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:update?key={FIREBASE_API_KEY}",
            json={
                "idToken": id_token,
                "displayName": display_name.strip(),
                "returnSecureToken": True,
            },
            timeout=20,
        )
        if upd.status_code == 200 and "idToken" in upd.json():
            id_token = upd.json()["idToken"]

    return id_token


def main(page: ft.Page):
    page.title = "Sunny 陽光早餐"
    page.bgcolor = "#FAFAFA"
    page.padding = 0
    page.window.width = 460
    page.window.height = 820

    # App 狀態
    state = {
        "token": None,
        "user": None,
        "menu_items": [],
        "menu_by_id": {},
        "options_by_group": {},
        "option_by_id": {},
        "config": {},
        "cart": [],  # [{"item_id", "qty", "option_ids", "note"}]
        "tab": 0,
    }

    def notify(msg: str, color: str = "#2B2D42"):
        page.open(ft.SnackBar(ft.Text(msg, color="white"), bgcolor=color))

    def auth_headers():
        return {"Authorization": f"Bearer {state['token']}"}

    def load_menu_data():
        res = httpx.get(f"{API_BASE}/api/menu", timeout=15)
        res.raise_for_status()
        data = res.json()["data"]
        state["menu_items"] = data["menu_items"]
        state["menu_by_id"] = {it["item_id"]: it for it in data["menu_items"]}
        state["config"] = data.get("config", {})

        by_group, by_id = {}, {}
        for o in data["options"]:
            by_group.setdefault(o["option_group"], []).append(o)
            by_id[o["option_id"]] = o
        state["options_by_group"] = by_group
        state["option_by_id"] = by_id

    def calc_unit_price(line: dict) -> float:
        base = state["menu_by_id"][line["item_id"]]["current_price"]
        extra = sum(
            state["option_by_id"][oid]["extra_price"]
            for oid in line["option_ids"]
            if oid in state["option_by_id"]
        )
        return base + extra

    def add_to_cart(item_id: int, option_ids: list, note: str, qty: int):
        key_opts = sorted(option_ids)
        max_qty = state["config"].get("max_qty", 20)
        for line in state["cart"]:
            if line["item_id"] == item_id and sorted(line["option_ids"]) == key_opts and line["note"] == note:
                line["qty"] = min(max_qty, line["qty"] + qty)
                return
        state["cart"].append({"item_id": item_id, "qty": min(max_qty, qty), "option_ids": key_opts, "note": note})

    # ==========================================
    # 畫面 1：登入 / 註冊頁
    # ==========================================
    def show_login_view():
        page.clean()
        page.appbar = None
        page.navigation_bar = None

        email_field = ft.TextField(label="電子郵件 (Email)", width=340, keyboard_type=ft.KeyboardType.EMAIL)
        pwd_field = ft.TextField(label="密碼 (至少 6 碼)", width=340, password=True, can_reveal_password=True)
        name_field = ft.TextField(label="暱稱 (註冊新帳號時填寫)", width=340)
        status_text = ft.Text("", color="red", size=13)

        def do_auth(mode: str):
            email = email_field.value.strip()
            pwd = pwd_field.value.strip()
            if not email or len(pwd) < 6:
                status_text.value = "請輸入正確的 Email 與至少 6 碼密碼"
                page.update()
                return
            status_text.value = "連線驗證中..."
            status_text.color = "#FF9F1C"
            page.update()
            try:
                token = firebase_auth_request(mode, email, pwd, name_field.value)
                me_res = httpx.get(
                    f"{API_BASE}/api/me",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=20,
                )
                me_res.raise_for_status()
                state["token"] = token
                state["user"] = me_res.json()
                load_menu_data()
                state["tab"] = 0
                show_main_view()
                notify(f"歡迎回來，{state['user']['display_name']}！", "#2E7D32")
            except Exception as ex:
                status_text.value = f"失敗：{ex}"
                status_text.color = "red"
                page.update()

        page.add(
            ft.Container(
                expand=True,
                alignment=ft.alignment.center,
                padding=24,
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=14,
                    controls=[
                        ft.Text("🍳 Sunny 陽光早餐", size=30, weight=ft.FontWeight.BOLD, color="#FF9F1C"),
                        ft.Text("AI 智慧點餐 ‧ 新鮮現做", size=14, color="#8D99AE"),
                        ft.Divider(height=16, color="transparent"),
                        email_field,
                        pwd_field,
                        name_field,
                        status_text,
                        ft.Row(
                            alignment=ft.MainAxisAlignment.CENTER,
                            spacing=12,
                            controls=[
                                ft.ElevatedButton(
                                    "登入",
                                    bgcolor="#FF9F1C",
                                    color="white",
                                    width=160,
                                    on_click=lambda _: do_auth("login"),
                                ),
                                ft.OutlinedButton(
                                    "註冊新帳號",
                                    width=160,
                                    on_click=lambda _: do_auth("register"),
                                ),
                            ],
                        ),
                    ],
                ),
            )
        )
        page.update()

    # ==========================================
    # 畫面 2：主頁面（點餐 / 排行榜 / 我的訂單）
    # ==========================================
    body_container = ft.Container(expand=True, padding=16)

    def show_main_view():
        page.clean()

        def on_logout(_):
            state["token"] = None
            state["user"] = None
            state["cart"] = []
            show_login_view()

        def on_nav_change(e):
            state["tab"] = e.control.selected_index
            render_tab()

        role_badge = " (店長)" if state["user"]["role"] == "ADMIN" else ""
        page.appbar = ft.AppBar(
            title=ft.Text(
                f"🍳 Sunny 早餐｜{state['user']['display_name']}{role_badge}",
                size=16,
                weight=ft.FontWeight.BOLD,
                color="white",
            ),
            bgcolor="#FF9F1C",
            actions=[
                ft.IconButton(ft.Icons.LOGOUT, icon_color="white", tooltip="登出", on_click=on_logout),
            ],
        )
        page.navigation_bar = ft.NavigationBar(
            selected_index=state["tab"],
            on_change=on_nav_change,
            destinations=[
                ft.NavigationBarDestination(icon=ft.Icons.RESTAURANT_MENU, label="點餐"),
                ft.NavigationBarDestination(icon=ft.Icons.LEADERBOARD, label="排行榜"),
                ft.NavigationBarDestination(icon=ft.Icons.RECEIPT_LONG, label="我的訂單"),
            ],
        )
        page.add(body_container)
        render_tab()

    def render_tab():
        if state["tab"] == 0:
            render_menu_tab()
        elif state["tab"] == 1:
            render_leaderboard_tab()
        else:
            render_orders_tab()
        page.update()

    # ---------- Tab 0：點餐與 AI 快點 ----------
    def render_menu_tab():
        ai_input = ft.TextField(
            hint_text="試試 AI 點餐：原味蛋餅加起司、大杯紅茶去冰",
            expand=True,
            text_size=13,
            bgcolor="white",
            border_radius=12,
        )

        def on_ai_parse(_):
            text = ai_input.value.strip()
            if not text:
                return
            try:
                res = httpx.post(
                    f"{API_BASE}/api/orders/ai-parse",
                    headers=auth_headers(),
                    json={"text": text},
                    timeout=25,
                )
                res.raise_for_status()
                items = res.json().get("items", [])
                if not items:
                    notify("AI 沒有辨識到菜單品項，請換個方式說說看！", "#E65100")
                    return
                for it in items:
                    add_to_cart(it["item_id"], it["option_ids"], it.get("custom_note", ""), it["quantity"])
                ai_input.value = ""
                notify(f"已透過 AI 加入 {len(items)} 項餐點到購物車！", "#2E7D32")
                render_menu_tab()
                page.update()
            except Exception as ex:
                notify(f"AI 解析失敗：{ex}", "red")

        # 客製規格彈窗
        def open_customize_dialog(item: dict):
            cfg = state["config"]
            single_groups = set(cfg.get("single_select", []))
            labels = cfg.get("labels", {})
            allowed = cfg.get("groups_by_category", {}).get(item["category"], [])

            selected_single = {}
            selected_multi = {}
            qty_state = {"val": 1}
            qty_text = ft.Text("1", size=16, weight=ft.FontWeight.BOLD)
            note_field = ft.TextField(label="備註（例如：不要蔥、醬少）", max_length=50)

            controls = []
            for grp in allowed:
                opts = state["options_by_group"].get(grp, [])
                if not opts:
                    continue
                is_single = grp in single_groups
                controls.append(
                    ft.Text(
                        f"{labels.get(grp, grp)}（{'單選' if is_single else '可複選'}）",
                        weight=ft.FontWeight.BOLD,
                        size=14,
                    )
                )
                if is_single:
                    rg = ft.RadioGroup(
                        content=ft.Column(
                            spacing=2,
                            controls=[
                                ft.Radio(
                                    value=str(o["option_id"]),
                                    label=f"{o['option_name']}" + (f" +${int(o['extra_price'])}" if o["extra_price"] > 0 else ""),
                                )
                                for o in opts
                            ],
                        )
                    )
                    selected_single[grp] = rg
                    controls.append(rg)
                else:
                    cbs = []
                    for o in opts:
                        cb = ft.Checkbox(
                            label=f"{o['option_name']}" + (f" +${int(o['extra_price'])}" if o["extra_price"] > 0 else ""),
                            value=False,
                        )
                        cb.data = o["option_id"]
                        cbs.append(cb)
                    selected_multi[grp] = cbs
                    controls.append(ft.Column(spacing=2, controls=cbs))

            controls.append(note_field)

            def chg_qty(delta):
                qty_state["val"] = max(1, min(cfg.get("max_qty", 20), qty_state["val"] + delta))
                qty_text.value = str(qty_state["val"])
                page.update()

            def confirm_add(_):
                picked_ids = []
                for rg in selected_single.values():
                    if rg.value:
                        picked_ids.append(int(rg.value))
                for cbs in selected_multi.values():
                    for cb in cbs:
                        if cb.value:
                            picked_ids.append(cb.data)
                add_to_cart(item["item_id"], picked_ids, note_field.value.strip(), qty_state["val"])
                page.close(dlg)
                notify(f"已加入 {item['item_name']} x{qty_state['val']}", "#2E7D32")
                render_menu_tab()
                page.update()

            dlg = ft.AlertDialog(
                title=ft.Text(f"{item['item_name']} (${int(item['current_price'])})"),
                content=ft.Container(
                    width=360,
                    height=340,
                    content=ft.Column(scroll=ft.ScrollMode.AUTO, spacing=8, controls=controls),
                ),
                actions=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Row([
                                ft.IconButton(ft.Icons.REMOVE, on_click=lambda _: chg_qty(-1)),
                                qty_text,
                                ft.IconButton(ft.Icons.ADD, on_click=lambda _: chg_qty(1)),
                            ]),
                            ft.ElevatedButton("加入購物車", bgcolor="#FF9F1C", color="white", on_click=confirm_add),
                        ],
                    )
                ],
            )
            page.open(dlg)

        # 購物車明細與送出彈窗
        def open_cart_dialog(_):
            if not state["cart"]:
                notify("購物車還是空的喔！")
                return

            def build_cart_rows():
                rows = []
                for idx, line in enumerate(state["cart"]):
                    it = state["menu_by_id"][line["item_id"]]
                    opts_str = "、".join(
                        state["option_by_id"][oid]["option_name"]
                        for oid in line["option_ids"]
                        if oid in state["option_by_id"]
                    )
                    sub = int(calc_unit_price(line) * line["qty"])
                    sub_info = " | ".join(x for x in [opts_str, f"備註:{line['note']}" if line["note"] else ""] if x)

                    def make_del(i):
                        def _del(_):
                            state["cart"].pop(i)
                            page.close(cart_dlg)
                            render_menu_tab()
                            page.update()
                        return _del

                    rows.append(
                        ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            controls=[
                                ft.Column(
                                    expand=True,
                                    spacing=2,
                                    controls=[
                                        ft.Text(f"{it['item_name']} x{line['qty']}  (${sub})", weight=ft.FontWeight.BOLD),
                                        ft.Text(sub_info, size=12, color="#8D99AE") if sub_info else ft.Container(),
                                    ],
                                ),
                                ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color="red", on_click=make_del(idx)),
                            ],
                        )
                    )
                return rows

            total_price = int(sum(calc_unit_price(l) * l["qty"] for l in state["cart"]))

            def submit_order(_):
                payload = {
                    "order_type": "TAKEOUT",
                    "payment_method": "CASH",
                    "items": [
                        {
                            "item_id": l["item_id"],
                            "quantity": l["qty"],
                            "option_ids": l["option_ids"],
                            "custom_note": l["note"] or None,
                        }
                        for l in state["cart"]
                    ],
                }
                try:
                    res = httpx.post(
                        f"{API_BASE}/api/orders",
                        headers=auth_headers(),
                        json=payload,
                        timeout=15,
                    )
                    if res.status_code != 200:
                        raise RuntimeError(res.json().get("detail", "下單失敗"))
                    ans = res.json()
                    state["cart"] = []
                    page.close(cart_dlg)
                    notify(f"✅ 下單成功！您的取餐單號：{ans['order_number']}（總計 ${int(ans['total_amount'])}）", "#2E7D32")
                    load_menu_data()
                    render_menu_tab()
                    page.update()
                except Exception as ex:
                    notify(f"送出失敗：{ex}", "red")

            cart_dlg = ft.AlertDialog(
                title=ft.Text(f"🛒 購物車明細（總計 ${total_price}）"),
                content=ft.Container(
                    width=360,
                    height=300,
                    content=ft.Column(scroll=ft.ScrollMode.AUTO, spacing=10, controls=build_cart_rows()),
                ),
                actions=[
                    ft.TextButton("取消", on_click=lambda _: page.close(cart_dlg)),
                    ft.ElevatedButton("✅ 確認送出訂單", bgcolor="#FF9F1C", color="white", on_click=submit_order),
                ],
            )
            page.open(cart_dlg)

        # 依分類建立菜單卡片
        grouped = {}
        for it in state["menu_items"]:
            grouped.setdefault(it["category"], []).append(it)

        menu_list = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=14)
        for cat, items in grouped.items():
            rows = []
            for it in items:
                stock_txt = f" (剩 {it['stock_quantity']} 份)" if it["stock_quantity"] is not None else ""
                rows.append(
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Column(
                                expand=True,
                                spacing=2,
                                controls=[
                                    ft.Text(it["item_name"], size=15, weight=ft.FontWeight.BOLD),
                                    ft.Text(f"${int(it['current_price'])}{stock_txt}  ‧ 已售 {it['total_sold']}", size=12, color="#8D99AE"),
                                ],
                            ),
                            ft.OutlinedButton("客製", on_click=lambda _, item=it: open_customize_dialog(item)),
                            ft.IconButton(
                                ft.Icons.ADD_CIRCLE,
                                icon_color="#FF9F1C",
                                on_click=lambda _, item=it: (
                                    add_to_cart(item["item_id"], [], "", 1),
                                    notify(f"+1 {item['item_name']}"),
                                    render_menu_tab(),
                                    page.update(),
                                ),
                            ),
                        ],
                    )
                )
            menu_list.controls.append(
                ft.Container(
                    bgcolor="white",
                    border_radius=14,
                    padding=14,
                    content=ft.Column(
                        spacing=8,
                        controls=[
                            ft.Text(f"🍳 {cat}", size=17, weight=ft.FontWeight.BOLD, color="#FF9F1C"),
                            ft.Divider(height=1),
                            *rows,
                        ],
                    ),
                )
            )

        total_qty = sum(l["qty"] for l in state["cart"])
        total_amt = int(sum(calc_unit_price(l) * l["qty"] for l in state["cart"]))

        cart_bar = ft.Container(
            bgcolor="white",
            border_radius=14,
            padding=12,
            border=ft.border.all(1, "#FFE4C4"),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Text(f"🛒 已選 {total_qty} 份｜總計 ${total_amt}", size=16, weight=ft.FontWeight.BOLD),
                    ft.ElevatedButton("查看／送出", bgcolor="#FF9F1C", color="white", on_click=open_cart_dialog),
                ],
            ),
        )

        body_container.content = ft.Column(
            expand=True,
            spacing=12,
            controls=[
                ft.Row([ai_input, ft.IconButton(ft.Icons.AUTO_AWESOME, icon_color="#FF9F1C", tooltip="AI 幫我點", on_click=on_ai_parse)]),
                menu_list,
                cart_bar,
            ],
        )

    # ---------- Tab 1：排行榜（ORDER BY + 索引） ----------
    def render_leaderboard_tab():
        try:
            res = httpx.get(f"{API_BASE}/api/leaderboard?limit=10", timeout=15)
            res.raise_for_status()
            data = res.json()
        except Exception as ex:
            body_container.content = ft.Text(f"載入排行榜失敗：{ex}", color="red")
            return

        medals = ["🥇", "🥈", "🥉"]
        item_rows = [
            ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Text(f"{medals[i] if i < 3 else f'{i+1}.'} {r['item_name']}", size=15, weight=ft.FontWeight.BOLD),
                    ft.Text(f"已售 {r['total_sold']} 份", color="#FF9F1C", weight=ft.FontWeight.BOLD),
                ],
            )
            for i, r in enumerate(data["top_items"])
        ]
        cust_rows = [
            ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Text(f"{medals[i] if i < 3 else f'{i+1}.'} {r['display_name']}", size=15),
                    ft.Text(f"累計 ${int(r['total_spent'])}", color="#2E7D32", weight=ft.FontWeight.BOLD),
                ],
            )
            for i, r in enumerate(data["top_customers"])
        ]

        body_container.content = ft.Column(
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=16,
            controls=[
                ft.Container(
                    bgcolor="white",
                    border_radius=14,
                    padding=16,
                    content=ft.Column(
                        spacing=10,
                        controls=[
                            ft.Text("🔥 熱銷餐點排行榜", size=18, weight=ft.FontWeight.BOLD, color="#FF9F1C"),
                            ft.Divider(height=1),
                            *item_rows,
                        ],
                    ),
                ),
                ft.Container(
                    bgcolor="white",
                    border_radius=14,
                    padding=16,
                    content=ft.Column(
                        spacing=10,
                        controls=[
                            ft.Text("👑 早餐常客消費榜", size=18, weight=ft.FontWeight.BOLD, color="#2E7D32"),
                            ft.Divider(height=1),
                            *cust_rows,
                        ],
                    ),
                ),
            ],
        )

    # ---------- Tab 2：我的歷史訂單 ----------
    def render_orders_tab():
        try:
            res = httpx.get(f"{API_BASE}/api/orders/me", headers=auth_headers(), timeout=15)
            res.raise_for_status()
            orders = res.json().get("orders", [])
        except Exception as ex:
            body_container.content = ft.Text(f"載入訂單失敗：{ex}", color="red")
            return

        if not orders:
            body_container.content = ft.Column(
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                expand=True,
                controls=[ft.Text("您目前還沒有點餐紀錄喔！", color="#8D99AE", size=16)],
            )
            return

        status_color = {"PENDING": "#FF9F1C", "COMPLETED": "#2E7D32", "CANCELLED": "#C62828"}
        cards = []
        for o in orders:
            detail_lines = [
                ft.Text(
                    f"▪ {d['item_name']} x{d['quantity']}  (${int(d['subtotal'])})"
                    + (f" ({d['custom_note']})" if d.get("custom_note") else ""),
                    size=13,
                )
                for d in o["items"]
            ]
            cards.append(
                ft.Container(
                    bgcolor="white",
                    border_radius=14,
                    padding=14,
                    content=ft.Column(
                        spacing=6,
                        controls=[
                            ft.Row(
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                controls=[
                                    ft.Text(f"單號 {o['order_number']}  ({o['order_date']})", weight=ft.FontWeight.BOLD, size=15),
                                    ft.Text(o["status"], color=status_color.get(o["status"], "#2B2D42"), weight=ft.FontWeight.BOLD),
                                ],
                            ),
                            ft.Divider(height=1),
                            *detail_lines,
                            ft.Text(f"總計：${int(o['total_amount'])}", weight=ft.FontWeight.BOLD, color="#FF9F1C"),
                        ],
                    ),
                )
            )

        body_container.content = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, spacing=12, controls=cards)

    show_login_view()


if __name__ == "__main__":
    ft.app(target=main)