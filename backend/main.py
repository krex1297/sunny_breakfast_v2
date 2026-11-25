import os
import json
import traceback
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from backend.db import db_conn
from backend.auth import get_current_user, require_admin

load_dotenv()

TAIPEI_TZ = timezone(timedelta(hours=8))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
ai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# 點餐規格設定
MAX_QTY = 20
SINGLE_SELECT_GROUPS = {"SIZE", "ICE", "SUGAR", "EGG"}
GROUP_LABELS = {"SIZE": "杯型", "ADDON": "加料", "ICE": "冰塊", "SUGAR": "甜度", "EGG": "加蛋"}
GROUPS_BY_CATEGORY = {
    "飲料類": {"SIZE", "ICE", "SUGAR"},
    "蛋餅類": {"ADDON", "EGG"},
    "吐司類": {"ADDON", "EGG"},
    "鐵板麵": {"ADDON", "EGG"},
}

app = FastAPI(title="Sunny 陽光早餐 API v2")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def today_tw() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")


def now_tw() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")


# ==========================================
# Pydantic 資料格式
# ==========================================
class OrderItemIn(BaseModel):
    item_id: int
    quantity: int = Field(ge=1, le=MAX_QTY)
    option_ids: List[int] = []
    custom_note: Optional[str] = Field(default=None, max_length=100)


class CreateOrderIn(BaseModel):
    order_type: str = "TAKEOUT"
    payment_method: str = "CASH"
    items: List[OrderItemIn]


class AIParseIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)


class UpdateMenuItemIn(BaseModel):
    current_price: Optional[float] = None
    stock_quantity: Optional[int] = None
    is_available: Optional[bool] = None


# ==========================================
# 1. 公開 API：健康檢查、菜單、排行榜
# ==========================================
@app.get("/")
def root():
    return {"status": "ok", "message": "Sunny 陽光早餐 FastAPI v2 運作中！"}


@app.get("/api/menu")
def get_menu():
    with db_conn() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT item_id, item_name, current_price, category, stock_quantity, total_sold "
            "FROM menu_items "
            "WHERE is_available = TRUE AND (stock_quantity IS NULL OR stock_quantity > 0) "
            "ORDER BY FIELD(category, '蛋餅類','吐司類','鐵板麵','飲料類'), item_id"
        )
        menu_items = cur.fetchall()
        cur.execute(
            "SELECT option_id, option_name, option_group, extra_price "
            "FROM options WHERE is_available = TRUE"
        )
        options = cur.fetchall()
        cur.close()

    for r in menu_items:
        r["current_price"] = float(r["current_price"])
    for r in options:
        r["extra_price"] = float(r["extra_price"])

    return {
        "status": "success",
        "data": {
            "menu_items": menu_items,
            "options": options,
            "config": {
                "single_select": sorted(SINGLE_SELECT_GROUPS),
                "labels": GROUP_LABELS,
                "groups_by_category": {k: sorted(v) for k, v in GROUPS_BY_CATEGORY.items()},
                "max_qty": MAX_QTY,
            },
        },
    }


@app.get("/api/leaderboard")
def get_leaderboard(limit: int = Query(default=10, ge=1, le=50)):
    """使用 ORDER BY + 索引查詢熱銷商品榜與熟客消費榜"""
    with db_conn() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT item_id, item_name, category, current_price, total_sold "
            "FROM menu_items WHERE is_available = TRUE "
            "ORDER BY total_sold DESC LIMIT %s",
            (limit,),
        )
        top_items = cur.fetchall()

        cur.execute(
            "SELECT display_name, total_spent "
            "FROM customers "
            "ORDER BY total_spent DESC LIMIT %s",
            (limit,),
        )
        top_customers = cur.fetchall()
        cur.close()

    for r in top_items:
        r["current_price"] = float(r["current_price"])
    for r in top_customers:
        r["total_spent"] = float(r["total_spent"])

    return {"top_items": top_items, "top_customers": top_customers}


# ==========================================
# 2. 顧客 API（需 Firebase Auth 登入）
# ==========================================
@app.get("/api/me")
def get_me(user: dict = Depends(get_current_user)):
    return user


@app.post("/api/orders")
def create_order(payload: CreateOrderIn, user: dict = Depends(get_current_user)):
    if not payload.items:
        raise HTTPException(status_code=400, detail="購物車不能為空")

    with db_conn() as conn:
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT option_id, option_name, option_group, extra_price, cost FROM options WHERE is_available = TRUE")
            opt_map = {r["option_id"]: r for r in cur.fetchall()}

            order_date = today_tw()
            cur.execute("SELECT COUNT(*) AS cnt FROM orders WHERE order_date = %s", (order_date,))
            order_number = f"#{cur.fetchone()['cnt'] + 1:03d}"

            prepared_items = []
            total_amount = 0.0

            for req_item in payload.items:
                cur.execute(
                    "SELECT item_id, item_name, category, current_price, cost, stock_quantity, is_available "
                    "FROM menu_items WHERE item_id = %s FOR UPDATE",
                    (req_item.item_id,),
                )
                db_item = cur.fetchone()
                if not db_item or not db_item["is_available"]:
                    raise HTTPException(status_code=400, detail=f"品項 ID {req_item.item_id} 目前無法供應")

                stock = db_item["stock_quantity"]
                if stock is not None and stock < req_item.quantity:
                    raise HTTPException(
                        status_code=400,
                        detail=f"【{db_item['item_name']}】庫存不足（僅剩 {stock} 份）",
                    )

                extra_total = 0.0
                valid_opts = []
                seen_groups = set()
                allowed_groups = GROUPS_BY_CATEGORY.get(db_item["category"], set())

                for oid in dict.fromkeys(req_item.option_ids):
                    opt = opt_map.get(oid)
                    if not opt:
                        continue
                    grp = opt["option_group"]
                    if allowed_groups and grp not in allowed_groups:
                        continue
                    if grp in SINGLE_SELECT_GROUPS and grp in seen_groups:
                        raise HTTPException(status_code=400, detail=f"{db_item['item_name']} 的 {GROUP_LABELS.get(grp, grp)} 只能單選")
                    seen_groups.add(grp)
                    extra_total += float(opt["extra_price"])
                    valid_opts.append(opt)

                base_price = float(db_item["current_price"])
                unit_price = base_price + extra_total
                subtotal = unit_price * req_item.quantity
                total_amount += subtotal

                prepared_items.append({
                    "item_id": db_item["item_id"],
                    "item_name": db_item["item_name"],
                    "quantity": req_item.quantity,
                    "base_price": base_price,
                    "cost": float(db_item["cost"] or 0),
                    "unit_price": unit_price,
                    "subtotal": subtotal,
                    "custom_note": req_item.custom_note,
                    "options": valid_opts,
                    "has_stock": stock is not None,
                })

            cur.execute(
                "INSERT INTO orders (customer_id, order_number, order_type, status, payment_method, total_amount, order_date, created_at) "
                "VALUES (%s, %s, %s, 'PENDING', %s, %s, %s, %s)",
                (user["customer_id"], order_number, payload.order_type, payload.payment_method, total_amount, order_date, now_tw()),
            )
            order_id = cur.lastrowid

            for it in prepared_items:
                if it["has_stock"]:
                    cur.execute(
                        "UPDATE menu_items SET stock_quantity = stock_quantity - %s, total_sold = total_sold + %s WHERE item_id = %s",
                        (it["quantity"], it["quantity"], it["item_id"]),
                    )
                else:
                    cur.execute(
                        "UPDATE menu_items SET total_sold = total_sold + %s WHERE item_id = %s",
                        (it["quantity"], it["item_id"]),
                    )

                cur.execute(
                    "INSERT INTO order_details (order_id, item_id, quantity, base_price, cost_snapshot, unit_price, subtotal, custom_note) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (order_id, it["item_id"], it["quantity"], it["base_price"], it["cost"], it["unit_price"], it["subtotal"], it["custom_note"]),
                )
                detail_id = cur.lastrowid

                if it["options"]:
                    cur.executemany(
                        "INSERT INTO order_detail_options (detail_id, option_id, option_name_snapshot, extra_price_snapshot, option_cost_snapshot) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        [(detail_id, o["option_id"], o["option_name"], float(o["extra_price"]), float(o["cost"] or 0)) for o in it["options"]],
                    )

            cur.execute(
                "UPDATE customers SET total_spent = total_spent + %s WHERE customer_id = %s",
                (total_amount, user["customer_id"]),
            )
            conn.commit()
            return {"status": "success", "order_id": order_id, "order_number": order_number, "total_amount": total_amount}

        except HTTPException:
            conn.rollback()
            raise
        except Exception as e:
            conn.rollback()
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"下單失敗：{str(e)}")
        finally:
            cur.close()


@app.get("/api/orders/me")
def get_my_orders(user: dict = Depends(get_current_user)):
    with db_conn() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT order_id, order_number, status, total_amount, order_date, created_at "
            "FROM orders WHERE customer_id = %s ORDER BY created_at DESC LIMIT 10",
            (user["customer_id"],),
        )
        orders = cur.fetchall()
        if not orders:
            cur.close()
            return {"orders": []}

        order_ids = [o["order_id"] for o in orders]
        ph = ",".join(["%s"] * len(order_ids))
        cur.execute(
            f"SELECT od.order_id, mi.item_name, od.quantity, od.subtotal, od.custom_note "
            f"FROM order_details od JOIN menu_items mi ON od.item_id = mi.item_id "
            f"WHERE od.order_id IN ({ph})",
            order_ids,
        )
        details = cur.fetchall()
        cur.close()

    detail_map = {}
    for d in details:
        d["subtotal"] = float(d["subtotal"])
        detail_map.setdefault(d["order_id"], []).append(d)

    for o in orders:
        o["total_amount"] = float(o["total_amount"])
        o["order_date"] = str(o["order_date"])
        o["created_at"] = str(o["created_at"])
        o["items"] = detail_map.get(o["order_id"], [])

    return {"orders": orders}


@app.post("/api/orders/ai-parse")
def ai_parse_order(payload: AIParseIn, user: dict = Depends(get_current_user)):
    """使用 Gemini 將自然語言解析成結構化點餐清單，方便 Flet 一鍵加入購物車"""
    if not ai_client:
        raise HTTPException(status_code=503, detail="未設定 GEMINI_API_KEY")

    menu_res = get_menu()["data"]
    items = {m["item_name"]: m for m in menu_res["menu_items"]}
    options = {o["option_name"]: o for o in menu_res["options"]}

    menu_str = "、".join(f"{n}({int(v['current_price'])}元)" for n, v in items.items())
    options_str = "、".join(options.keys())
    prompt = (
        f"你是精準的台灣早餐店AI店員。目前菜單：{menu_str}。選項：{options_str}。\n"
        f"顧客說：「{payload.text}」\n"
        f"item_name 必須是菜單品項，options 只能從選項清單挑，未提到數量視為 1。非點餐內容請回傳 []。"
    )
    schema = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "item_name": {"type": "STRING"},
                "quantity": {"type": "INTEGER"},
                "options": {"type": "ARRAY", "items": {"type": "STRING"}},
                "custom_note": {"type": "STRING"},
            },
            "required": ["item_name", "quantity"],
        },
    }
    try:
        res = ai_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.1,
            ),
        )
        raw_list = json.loads(res.text)
        if isinstance(raw_list, dict):
            raw_list = [raw_list]
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"AI 解析失敗：{str(e)}")

    parsed = []
    for raw in raw_list:
        name = str(raw.get("item_name") or "").strip()
        matched = items.get(name) or next((v for k, v in items.items() if name and (name in k or k in name)), None)
        if not matched:
            continue
        opt_ids = [options[o]["option_id"] for o in (raw.get("options") or []) if o in options]
        parsed.append({
            "item_id": matched["item_id"],
            "item_name": matched["item_name"],
            "quantity": max(1, min(MAX_QTY, int(raw.get("quantity") or 1))),
            "option_ids": opt_ids,
            "custom_note": str(raw.get("custom_note") or "")[:100],
        })
    return {"items": parsed}


# ==========================================
# 3. 店長後台 API（給 Streamlit 呼叫，需 ADMIN 權限）
# ==========================================
@app.get("/api/admin/dashboard")
def admin_dashboard(target_date: Optional[str] = None, admin: dict = Depends(require_admin)):
    dt = target_date or today_tw()
    with db_conn() as conn:
        cur = conn.cursor(dictionary=True)

        cur.execute(
            "SELECT COUNT(order_id) AS orders, IFNULL(SUM(total_amount), 0) AS revenue "
            "FROM orders WHERE order_date = %s AND status != 'CANCELLED'",
            (dt,),
        )
        sales = cur.fetchone()

        cur.execute(
            "SELECT IFNULL(SUM(od.quantity * (od.cost_snapshot + IFNULL(oc.c, 0))), 0) AS cost "
            "FROM order_details od JOIN orders o ON od.order_id = o.order_id "
            "LEFT JOIN (SELECT detail_id, SUM(option_cost_snapshot) c FROM order_detail_options GROUP BY detail_id) oc "
            "ON oc.detail_id = od.detail_id "
            "WHERE o.order_date = %s AND o.status != 'CANCELLED'",
            (dt,),
        )
        cost = float(cur.fetchone()["cost"])

        cur.execute(
            "SELECT HOUR(created_at) AS hour, COUNT(order_id) AS orders, IFNULL(SUM(total_amount), 0) AS revenue "
            "FROM orders WHERE order_date = %s AND status != 'CANCELLED' "
            "GROUP BY HOUR(created_at) ORDER BY hour",
            (dt,),
        )
        hourly = cur.fetchall()

        cur.execute(
            "SELECT mi.item_name, SUM(od.quantity) AS qty, SUM(od.subtotal) AS revenue "
            "FROM order_details od JOIN orders o ON od.order_id = o.order_id "
            "JOIN menu_items mi ON od.item_id = mi.item_id "
            "WHERE o.order_date = %s AND o.status != 'CANCELLED' "
            "GROUP BY mi.item_id ORDER BY qty DESC LIMIT 10",
            (dt,),
        )
        top_items = cur.fetchall()

        cur.execute(
            "SELECT item_id, item_name, stock_quantity FROM menu_items "
            "WHERE stock_quantity IS NOT NULL AND stock_quantity <= 20 ORDER BY stock_quantity ASC"
        )
        low_stocks = cur.fetchall()
        cur.close()

    rev = float(sales["revenue"])
    orders_cnt = int(sales["orders"])
    profit = rev - cost

    for h in hourly:
        h["revenue"] = float(h["revenue"])
    for t in top_items:
        t["qty"] = int(t["qty"])
        t["revenue"] = float(t["revenue"])

    return {
        "date": dt,
        "summary": {
            "orders": orders_cnt,
            "revenue": rev,
            "cost": cost,
            "profit": profit,
            "margin": round(profit / rev * 100, 1) if rev > 0 else 0.0,
            "avg_ticket": round(rev / orders_cnt, 1) if orders_cnt > 0 else 0.0,
        },
        "hourly": hourly,
        "top_items": top_items,
        "low_stocks": low_stocks,
    }


@app.get("/api/admin/orders")
def admin_list_orders(target_date: Optional[str] = None, admin: dict = Depends(require_admin)):
    dt = target_date or today_tw()
    with db_conn() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT o.order_id, o.order_number, c.display_name, o.status, o.total_amount, o.created_at, o.completed_at "
            "FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
            "WHERE o.order_date = %s ORDER BY o.order_id DESC",
            (dt,),
        )
        orders = cur.fetchall()
        cur.close()

    for o in orders:
        o["total_amount"] = float(o["total_amount"])
        o["created_at"] = str(o["created_at"])
        o["completed_at"] = str(o["completed_at"]) if o["completed_at"] else None
    return {"orders": orders}


@app.patch("/api/admin/orders/{order_id}/complete")
def admin_complete_order(order_id: int, admin: dict = Depends(require_admin)):
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE orders SET status = 'COMPLETED', completed_at = %s "
            "WHERE order_id = %s AND status = 'PENDING'",
            (now_tw(), order_id),
        )
        if cur.rowcount == 0:
            cur.close()
            raise HTTPException(status_code=400, detail="找不到待出餐的訂單，可能已完成或已取消")
        conn.commit()
        cur.close()
    return {"status": "success", "message": f"訂單 {order_id} 已出餐"}


@app.patch("/api/admin/orders/{order_id}/cancel")
def admin_cancel_order(order_id: int, admin: dict = Depends(require_admin)):
    with db_conn() as conn:
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "SELECT order_id, customer_id, total_amount, status FROM orders WHERE order_id = %s FOR UPDATE",
                (order_id,),
            )
            order = cur.fetchone()
            if not order or order["status"] != "PENDING":
                raise HTTPException(status_code=400, detail="只能取消 PENDING 狀態的訂單")

            cur.execute("UPDATE orders SET status = 'CANCELLED' WHERE order_id = %s", (order_id,))
            cur.execute("SELECT item_id, quantity FROM order_details WHERE order_id = %s", (order_id,))
            for d in cur.fetchall():
                cur.execute(
                    "UPDATE menu_items SET "
                    "stock_quantity = IF(stock_quantity IS NULL, NULL, stock_quantity + %s), "
                    "total_sold = GREATEST(0, total_sold - %s) "
                    "WHERE item_id = %s",
                    (d["quantity"], d["quantity"], d["item_id"]),
                )
            cur.execute(
                "UPDATE customers SET total_spent = GREATEST(0, total_spent - %s) WHERE customer_id = %s",
                (float(order["total_amount"]), order["customer_id"]),
            )
            conn.commit()
            return {"status": "success", "message": f"訂單 {order_id} 已取消，庫存與排行榜已還原"}
        except HTTPException:
            conn.rollback()
            raise
        finally:
            cur.close()


@app.get("/api/admin/menu")
def admin_get_all_menu(admin: dict = Depends(require_admin)):
    with db_conn() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT item_id, category, item_name, current_price, cost, stock_quantity, total_sold, is_available FROM menu_items ORDER BY item_id")
        rows = cur.fetchall()
        cur.close()
    for r in rows:
        r["current_price"] = float(r["current_price"])
        r["cost"] = float(r["cost"] or 0)
        r["is_available"] = bool(r["is_available"])
    return {"menu_items": rows}


@app.patch("/api/admin/menu/{item_id}")
def admin_update_menu(item_id: int, payload: UpdateMenuItemIn, admin: dict = Depends(require_admin)):
    fields, values = [], []
    if payload.current_price is not None:
        fields.append("current_price = %s")
        values.append(payload.current_price)
    if payload.stock_quantity is not None:
        fields.append("stock_quantity = %s")
        values.append(payload.stock_quantity)
    if payload.is_available is not None:
        fields.append("is_available = %s")
        values.append(payload.is_available)
    if not fields:
        raise HTTPException(status_code=400, detail="沒有需要更新的欄位")

    values.append(item_id)
    with db_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE menu_items SET {', '.join(fields)} WHERE item_id = %s", values)
        conn.commit()
        cur.close()
    return {"status": "success"}