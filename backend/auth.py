import os
import firebase_admin
from firebase_admin import auth, credentials
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from backend.db import db_conn

load_dotenv()

if not firebase_admin._apps:
    cred = credentials.Certificate(os.getenv("FIREBASE_CRED_PATH", "serviceAccountKey.json"))
    firebase_admin.initialize_app(cred)

security = HTTPBearer()

ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.getenv("ADMIN_EMAILS", "").split(",")
    if e.strip()
}

def get_current_user(cred: HTTPAuthorizationCredentials = Depends(security)):
    try:
        # clock_skew_seconds=60 可避免 Windows 本機時間微幅誤差導致驗證失敗
        decoded = auth.verify_id_token(cred.credentials, clock_skew_seconds=60)
    except Exception as e:
        print("❌ Firebase Token 驗證失敗詳細原因：", repr(e))
        raise HTTPException(status_code=401, detail=f"登入憑證驗證失敗：{e}")

    uid = decoded["uid"]
    email = (decoded.get("email") or "").lower()
    display_name = decoded.get("name") or (email.split("@")[0] if email else "顧客")
    default_role = "ADMIN" if email in ADMIN_EMAILS else "CUSTOMER"

    with db_conn() as conn:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT customer_id, firebase_uid, email, display_name, role, total_spent "
            "FROM customers WHERE firebase_uid = %s",
            (uid,),
        )
        user = cur.fetchone()
        if not user:
            cur.execute(
                "INSERT INTO customers (firebase_uid, email, display_name, role) "
                "VALUES (%s, %s, %s, %s)",
                (uid, email or None, display_name, default_role),
            )
            conn.commit()
            user = {
                "customer_id": cur.lastrowid,
                "firebase_uid": uid,
                "email": email,
                "display_name": display_name,
                "role": default_role,
                "total_spent": 0.0,
            }
        elif email in ADMIN_EMAILS and user["role"] != "ADMIN":
            cur.execute("UPDATE customers SET role = 'ADMIN' WHERE customer_id = %s", (user["customer_id"],))
            conn.commit()
            user["role"] = "ADMIN"
        cur.close()

    user["total_spent"] = float(user["total_spent"])
    return user

def require_admin(user: dict = Depends(get_current_user)):
    if user["role"] != "ADMIN":
        raise HTTPException(status_code=403, detail="權限不足，僅限店長存取")
    return user