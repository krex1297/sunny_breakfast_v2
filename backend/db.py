import os
from contextlib import contextmanager
import certifi
from mysql.connector import pooling
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = dict(
    host=os.getenv("TIDB_HOST"),
    port=int(os.getenv("TIDB_PORT", 4000)),
    user=os.getenv("TIDB_USER"),
    password=os.getenv("TIDB_PASSWORD"),
    database=os.getenv("TIDB_DATABASE", "breakfast_pos"),
    charset="utf8mb4",
    ssl_ca=certifi.where(),
    ssl_verify_cert=True,
    use_pure=True,
    connection_timeout=30,
)

db_pool = pooling.MySQLConnectionPool(
    pool_name="tidb_pool",
    pool_size=2,
    **DB_CONFIG,
)

@contextmanager
def db_conn():
    conn = db_pool.get_connection()
    try:
        conn.ping(reconnect=True, attempts=3, delay=1)
        yield conn
    finally:
        conn.close()