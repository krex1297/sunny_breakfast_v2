import os
import certifi
import mysql.connector
import firebase_admin
from firebase_admin import credentials
from dotenv import load_dotenv

load_dotenv()

# 1. 測試 TiDB Cloud 連線與排行榜索引查詢
conn = mysql.connector.connect(
    host=os.getenv("TIDB_HOST"),
    port=int(os.getenv("TIDB_PORT", 4000)),
    user=os.getenv("TIDB_USER"),
    password=os.getenv("TIDB_PASSWORD"),
    database=os.getenv("TIDB_DATABASE"),
    ssl_ca=certifi.where(),
    ssl_verify_cert=True,
    use_pure=True,
)
cur = conn.cursor(dictionary=True)
cur.execute(
    "SELECT item_name, total_sold FROM menu_items "
    "WHERE is_available = TRUE ORDER BY total_sold DESC LIMIT 3"
)
print("✅ TiDB 連線成功！熱銷 Top 3：", cur.fetchall())
cur.close()
conn.close()

# 2. 測試 Firebase Admin 金鑰載入
cred = credentials.Certificate(os.getenv("FIREBASE_CRED_PATH"))
firebase_admin.initialize_app(cred)
print("✅ Firebase Admin 初始化成功！")