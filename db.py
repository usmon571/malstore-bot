"""
PostgreSQL saqlagich (Neon uchun).

`store.py` ning API'si bilan bir xil ishlaydi — qaytaradigan lug'atlar
ham aynan o'sha ko'rinishda, shuning uchun bot kodi o'zgarmaydi.

Sanalar matn (ISO) ko'rinishida saqlanadi, chunki bot ularni shunday
kesib ishlatadi (masalan created[5:16]).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger("malstore-db")

FIRST_ID = 100
_conn: psycopg.Connection | None = None


def _connect(dsn: str) -> psycopg.Connection:
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
    return _conn


def _cursor(dsn: str):
    """Ulanish uzilgan bo'lsa bir marta qayta ulanadi (Neon uyqudan uyg'onadi)."""
    try:
        return _connect(dsn).cursor()
    except psycopg.OperationalError:
        global _conn
        _conn = None
        return _connect(dsn).cursor()


SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id          TEXT PRIMARY KEY,
    url         TEXT DEFAULT '',
    title       TEXT DEFAULT '',
    prices      JSONB DEFAULT '{}'::jsonb,
    arrival     TEXT DEFAULT '',
    caption     TEXT DEFAULT '',
    message_ids JSONB DEFAULT '[]'::jsonb,
    created     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    no          INTEGER PRIMARY KEY,
    product_id  TEXT,
    variant     TEXT DEFAULT '',
    price       TEXT DEFAULT '',
    prepay      TEXT DEFAULT '',
    status      TEXT DEFAULT '',
    receipt     TEXT,
    user_id     BIGINT,
    username    TEXT,
    name        TEXT,
    phone       TEXT DEFAULT '',
    created     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admins (
    user_id BIGINT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS counters (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""


def init(dsn: str) -> None:
    with _cursor(dsn) as cur:
        cur.execute(SCHEMA)
        cur.execute(
            "INSERT INTO counters (name, value) VALUES ('product', %s) "
            "ON CONFLICT (name) DO NOTHING",
            (FIRST_ID,),
        )
    log.info("PostgreSQL tayyor")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- tovarlar ----------

def next_product_id(dsn: str) -> str:
    with _cursor(dsn) as cur:
        cur.execute(
            "UPDATE counters SET value = value + 1 WHERE name = 'product' RETURNING value"
        )
        return str(cur.fetchone()["value"])


def save_product(dsn: str, product_id, url, title, prices, arrival, caption, message_ids) -> None:
    with _cursor(dsn) as cur:
        cur.execute(
            """INSERT INTO products (id, url, title, prices, arrival, caption, message_ids, created)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO UPDATE SET
                 url=EXCLUDED.url, title=EXCLUDED.title, prices=EXCLUDED.prices,
                 arrival=EXCLUDED.arrival, caption=EXCLUDED.caption,
                 message_ids=EXCLUDED.message_ids""",
            (product_id, url, title, json.dumps(prices, ensure_ascii=False), arrival,
             caption, json.dumps(message_ids or [], ensure_ascii=False), _now()),
        )


def get_product(dsn: str, product_id: str) -> dict | None:
    with _cursor(dsn) as cur:
        cur.execute("SELECT * FROM products WHERE id = %s", (str(product_id).strip().lstrip("#"),))
        return cur.fetchone()


def update_product(dsn: str, product_id: str, **fields) -> dict | None:
    if not fields:
        return get_product(dsn, product_id)
    allowed = {"url", "title", "prices", "arrival", "caption", "message_ids"}
    sets, values = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = %s")
        values.append(json.dumps(value, ensure_ascii=False) if key in ("prices", "message_ids") else value)
    if not sets:
        return get_product(dsn, product_id)
    values.append(str(product_id).strip().lstrip("#"))
    with _cursor(dsn) as cur:
        cur.execute(f"UPDATE products SET {', '.join(sets)} WHERE id = %s RETURNING *", values)
        return cur.fetchone()


def delete_product(dsn: str, product_id: str) -> dict | None:
    with _cursor(dsn) as cur:
        cur.execute(
            "DELETE FROM products WHERE id = %s RETURNING *",
            (str(product_id).strip().lstrip("#"),),
        )
        return cur.fetchone()


def list_products(dsn: str, limit: int = 20) -> list[dict]:
    with _cursor(dsn) as cur:
        cur.execute("SELECT * FROM products ORDER BY created DESC LIMIT %s", (limit,))
        return cur.fetchall()


def product_count(dsn: str) -> int:
    with _cursor(dsn) as cur:
        cur.execute("SELECT COUNT(*) AS n FROM products")
        return cur.fetchone()["n"]


# ---------- buyurtmalar ----------

def add_order(dsn: str, product_id, variant, user, phone, price, prepay) -> int:
    with _cursor(dsn) as cur:
        cur.execute("SELECT COALESCE(MAX(no), 0) + 1 AS next FROM orders")
        order_no = cur.fetchone()["next"]
        cur.execute(
            """INSERT INTO orders
               (no, product_id, variant, price, prepay, status, receipt,
                user_id, username, name, phone, created)
               VALUES (%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s)""",
            (order_no, product_id, variant, price, prepay, "to'lov kutilmoqda",
             user.get("id"), user.get("username"), user.get("name"), phone, _now()),
        )
        return order_no


def get_order(dsn: str, order_no: int) -> dict | None:
    with _cursor(dsn) as cur:
        cur.execute("SELECT * FROM orders WHERE no = %s", (order_no,))
        return cur.fetchone()


def set_order_status(dsn: str, order_no: int, status: str) -> dict | None:
    with _cursor(dsn) as cur:
        cur.execute(
            "UPDATE orders SET status = %s WHERE no = %s RETURNING *", (status, order_no)
        )
        return cur.fetchone()


def mark_paid(dsn: str, order_no: int, receipt_file_id: str) -> dict | None:
    with _cursor(dsn) as cur:
        cur.execute(
            "UPDATE orders SET receipt = %s, status = %s WHERE no = %s RETURNING *",
            (receipt_file_id, "chek yuborilgan", order_no),
        )
        return cur.fetchone()


def recent_orders(dsn: str, limit: int = 15) -> list[dict]:
    with _cursor(dsn) as cur:
        cur.execute("SELECT * FROM orders ORDER BY no DESC LIMIT %s", (limit,))
        return cur.fetchall()


# ---------- adminlar ----------

def remember_admin(dsn: str, user_id: int) -> None:
    with _cursor(dsn) as cur:
        cur.execute(
            "INSERT INTO admins (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,)
        )


def admin_ids(dsn: str) -> list[int]:
    with _cursor(dsn) as cur:
        cur.execute("SELECT user_id FROM admins")
        return [row["user_id"] for row in cur.fetchall()]
