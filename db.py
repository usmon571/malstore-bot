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


def _reset() -> None:
    global _conn
    try:
        if _conn is not None:
            _conn.close()
    except Exception:
        pass
    _conn = None


def _exec(dsn: str, sql: str, params=None, fetch: str | None = None):
    """So'rovni bajaradi; ulanish eskirgan bo'lsa qayta ulanib takrorlaydi.

    Neon bo'sh turgan ulanishni yopadi va bu faqat so'rov paytida ma'lum
    bo'ladi ("SSL connection has been closed unexpectedly") — shuning uchun
    xatoni ulanish paytida emas, aynan shu yerda ushlash kerak.
    """
    for attempt in (1, 2):
        try:
            with _connect(dsn).cursor() as cur:
                cur.execute(sql, params)
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()
                return None
        except (psycopg.OperationalError, psycopg.InterfaceError) as e:
            _reset()
            if attempt == 2:
                raise
            log.warning("baza ulanishi eskirgan (%s) — qayta ulanamiz", type(e).__name__)


def _cursor(dsn: str):
    """Ko'p bosqichli ishlar uchun (migratsiya skripti). Odatda _exec ishlatiladi."""
    try:
        return _connect(dsn).cursor()
    except psycopg.OperationalError:
        _reset()
        return _connect(dsn).cursor()


SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id          TEXT PRIMARY KEY,
    url         TEXT DEFAULT '',
    title       TEXT DEFAULT '',
    prices      JSONB DEFAULT '{}'::jsonb,
    base_prices JSONB DEFAULT '{}'::jsonb,
    arrival     TEXT DEFAULT '',
    caption     TEXT DEFAULT '',
    message_ids JSONB DEFAULT '[]'::jsonb,
    images      JSONB DEFAULT '[]'::jsonb,
    created     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    no          INTEGER PRIMARY KEY,
    product_id  TEXT,
    variant     TEXT DEFAULT '',
    size        TEXT DEFAULT '',
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
    _exec(dsn, SCHEMA)
    # CREATE TABLE IF NOT EXISTS eski jadvalga yangi ustun qo'shmaydi —
    # shuning uchun keyin qo'shilgan maydonlar alohida yoziladi
    _exec(dsn, "ALTER TABLE orders ADD COLUMN IF NOT EXISTS size TEXT DEFAULT ''")
    _exec(
        dsn,
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "base_prices JSONB DEFAULT '{}'::jsonb",
    )
    # Kanal postidagi rasmlarning Telegram file_id'lari — sayt shu orqali ko'rsatadi
    _exec(
        dsn,
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS "
        "images JSONB DEFAULT '[]'::jsonb",
    )
    _exec(
        dsn,
        "INSERT INTO counters (name, value) VALUES ('product', %s) "
        "ON CONFLICT (name) DO NOTHING",
        (FIRST_ID,),
    )
    log.info("PostgreSQL tayyor")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean(product_id) -> str:
    return str(product_id).strip().lstrip("#")


# ---------- tovarlar ----------

def next_product_id(dsn: str) -> str:
    row = _exec(
        dsn,
        "UPDATE counters SET value = value + 1 WHERE name = 'product' RETURNING value",
        fetch="one",
    )
    return str(row["value"])


def save_product(
    dsn: str, product_id, url, title, prices, arrival, caption, message_ids,
    base_prices=None, images=None,
) -> None:
    _exec(
        dsn,
        """INSERT INTO products
             (id, url, title, prices, base_prices, arrival, caption,
              message_ids, images, created)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (id) DO UPDATE SET
             url=EXCLUDED.url, title=EXCLUDED.title, prices=EXCLUDED.prices,
             base_prices=EXCLUDED.base_prices,
             arrival=EXCLUDED.arrival, caption=EXCLUDED.caption,
             message_ids=EXCLUDED.message_ids, images=EXCLUDED.images""",
        (product_id, url, title, json.dumps(prices, ensure_ascii=False),
         json.dumps(base_prices or {}, ensure_ascii=False), arrival,
         caption, json.dumps(message_ids or [], ensure_ascii=False),
         json.dumps(images or [], ensure_ascii=False), _now()),
    )


def get_product(dsn: str, product_id: str) -> dict | None:
    return _exec(
        dsn, "SELECT * FROM products WHERE id = %s", (_clean(product_id),), fetch="one"
    )


def update_product(dsn: str, product_id: str, **fields) -> dict | None:
    allowed = {
        "url", "title", "prices", "base_prices", "arrival", "caption", "message_ids",
        "images",
    }
    sets, values = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = %s")
        values.append(
            json.dumps(value, ensure_ascii=False)
            if key in ("prices", "base_prices", "message_ids", "images") else value
        )
    if not sets:
        return get_product(dsn, product_id)
    values.append(_clean(product_id))
    return _exec(
        dsn, f"UPDATE products SET {', '.join(sets)} WHERE id = %s RETURNING *",
        values, fetch="one",
    )


def delete_product(dsn: str, product_id: str) -> dict | None:
    return _exec(
        dsn, "DELETE FROM products WHERE id = %s RETURNING *",
        (_clean(product_id),), fetch="one",
    )


def list_products(dsn: str, limit: int = 20) -> list[dict]:
    return _exec(
        dsn, "SELECT * FROM products ORDER BY created DESC LIMIT %s", (limit,), fetch="all"
    )


def product_count(dsn: str) -> int:
    return _exec(dsn, "SELECT COUNT(*) AS n FROM products", fetch="one")["n"]


# ---------- buyurtmalar ----------

def add_order(dsn: str, product_id, variant, user, phone, price, prepay, size="") -> int:
    # Raqam bitta so'rov ichida hisoblanadi — alohida SELECT kerak emas
    row = _exec(
        dsn,
        """INSERT INTO orders
             (no, product_id, variant, size, price, prepay, status, receipt,
              user_id, username, name, phone, created)
           VALUES ((SELECT COALESCE(MAX(no), 0) + 1 FROM orders),
                   %s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s,%s)
           RETURNING no""",
        (product_id, variant, size, price, prepay, "to'lov kutilmoqda",
         user.get("id"), user.get("username"), user.get("name"), phone, _now()),
        fetch="one",
    )
    return row["no"]


def get_order(dsn: str, order_no: int) -> dict | None:
    return _exec(dsn, "SELECT * FROM orders WHERE no = %s", (order_no,), fetch="one")


def set_order_status(dsn: str, order_no: int, status: str) -> dict | None:
    return _exec(
        dsn, "UPDATE orders SET status = %s WHERE no = %s RETURNING *",
        (status, order_no), fetch="one",
    )


def mark_paid(dsn: str, order_no: int, receipt_file_id: str) -> dict | None:
    return _exec(
        dsn, "UPDATE orders SET receipt = %s, status = %s WHERE no = %s RETURNING *",
        (receipt_file_id, "chek yuborilgan", order_no), fetch="one",
    )


def link_order_user(dsn: str, order_no: int, user_id: int, username: str | None) -> dict | None:
    return _exec(
        dsn, "UPDATE orders SET user_id = %s, username = %s WHERE no = %s RETURNING *",
        (user_id, username, order_no), fetch="one",
    )


def recent_orders(dsn: str, limit: int = 15) -> list[dict]:
    return _exec(
        dsn, "SELECT * FROM orders ORDER BY no DESC LIMIT %s", (limit,), fetch="all"
    )


# ---------- adminlar ----------

def remember_admin(dsn: str, user_id: int) -> None:
    _exec(dsn, "INSERT INTO admins (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))


def admin_ids(dsn: str) -> list[int]:
    return [row["user_id"] for row in _exec(dsn, "SELECT user_id FROM admins", fetch="all")]
