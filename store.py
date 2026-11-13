"""
Tovarlar va buyurtmalar saqlagichi.

Ikki rejimda ishlaydi:
  - DATABASE_URL berilgan bo'lsa  -> PostgreSQL (Neon), db.py orqali
  - berilmagan bo'lsa             -> JSON fayl (kompyuterda ishlash uchun)

Ikkalasi ham bir xil ko'rinishdagi lug'at qaytaradi, shuning uchun bot
kodi qaysi saqlagich ishlayotganini bilmaydi.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger("malstore-store")

import settings

# Railway'da bu Volume yo'li bo'ladi — aks holda fayl har deployda yo'qoladi
STORE_FILE = settings.DATA_DIR / "store.json"
FIRST_ID = 100

DSN = os.getenv("DATABASE_URL", "").strip()
USE_DB = bool(DSN)

if USE_DB:
    import db
    try:
        db.init(DSN)
    except Exception as e:
        # Jimgina JSON'ga qaytmaymiz — aks holda ma'lumot ikki joyga bo'linib,
        # bazadagi buyurtmalar ko'rinmay qoladi.
        raise SystemExit(
            f"PostgreSQL'ga ulanib bo'lmadi: {type(e).__name__}: {e}\n"
            "DATABASE_URL to'g'riligini va baza ishlayotganini tekshiring."
        ) from e
    log.info("Saqlagich: PostgreSQL")
else:
    log.info("Saqlagich: JSON fayl (%s)", STORE_FILE)


# Bot va sayt API'si (api.py, alohida oqim) bitta saqlagichni ishlatadi.
# JSON'da har bir amal "o'qi → o'zgartir → yoz" — ikkalasi bir vaqtda
# yozsa, biri ikkinchisining o'zgarishini o'chirib yuborardi. Bazada esa
# ulanish umumiy. Shuning uchun hamma amallar navbat bilan bajariladi.
_lock = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return wrapper


def _empty() -> dict:
    return {"counter": FIRST_ID, "products": {}, "orders": [], "admins": []}


def _load() -> dict:
    try:
        data = json.loads(STORE_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except Exception as e:
        log.warning("store.json o'qilmadi (%s) — bo'sh baza bilan davom etamiz", e)
        return _empty()
    # Eski/yarim fayl bo'lsa ham yiqilmasin
    base = _empty()
    base.update({k: v for k, v in data.items() if k in base})
    return _migrate(base)


# Yangi imkoniyatlar qo'shilganda eski yozuvlarda bu maydonlar bo'lmaydi
ORDER_DEFAULTS = {
    "price": "", "prepay": "", "size": "",
    "status": "to'lov kutilmoqda", "receipt": None,
}
PRODUCT_DEFAULTS = {"caption": "", "message_ids": [], "base_prices": {}, "images": []}


def _migrate(data: dict) -> dict:
    for order in data["orders"]:
        for key, value in ORDER_DEFAULTS.items():
            order.setdefault(key, value)
    for product in data["products"].values():
        for key, value in PRODUCT_DEFAULTS.items():
            product.setdefault(key, value)
    return data


def _save(data: dict) -> None:
    tmp = STORE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STORE_FILE)  # yozish yarmida uzilib qolmasligi uchun


@_locked
def next_product_id() -> str:
    if USE_DB:
        return db.next_product_id(DSN)
    data = _load()
    data["counter"] += 1
    _save(data)
    return str(data["counter"])


@_locked
def save_product(
    product_id: str, url: str, title: str, prices: dict, arrival: str,
    caption: str = "", message_ids: list[int] | None = None,
    base_prices: dict | None = None, images: list[str] | None = None,
) -> None:
    """base_prices — yetkazib berish va ustamasiz tannarx (oldindan to'lov shundan).
    images — kanal postidagi rasmlarning Telegram file_id'lari (sayt uchun)."""
    if USE_DB:
        db.save_product(
            DSN, product_id, url, title, prices, arrival, caption,
            message_ids, base_prices, images,
        )
        return
    data = _load()
    data["products"][product_id] = {
        "id": product_id,
        "url": url,
        "title": title,
        "prices": prices,
        "base_prices": base_prices or {},
        "arrival": arrival,
        "caption": caption,
        "message_ids": message_ids or [],
        "images": images or [],
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    _save(data)


@_locked
def get_product(product_id: str) -> dict | None:
    if USE_DB:
        return db.get_product(DSN, product_id)
    return _load()["products"].get(str(product_id).strip().lstrip("#"))


@_locked
def update_product(product_id: str, **fields) -> dict | None:
    if USE_DB:
        return db.update_product(DSN, product_id, **fields)
    data = _load()
    product = data["products"].get(str(product_id).strip().lstrip("#"))
    if not product:
        return None
    product.update(fields)
    _save(data)
    return product


@_locked
def delete_product(product_id: str) -> dict | None:
    if USE_DB:
        return db.delete_product(DSN, product_id)
    data = _load()
    product = data["products"].pop(str(product_id).strip().lstrip("#"), None)
    if product:
        _save(data)
    return product


@_locked
def list_products(limit: int = 20) -> list[dict]:
    if USE_DB:
        return db.list_products(DSN, limit)
    return list(reversed(list(_load()["products"].values())))[:limit]


@_locked
def add_order(
    product_id: str, variant: str, user: dict, phone: str,
    price: str = "", prepay: str = "", size: str = "",
) -> int:
    """Buyurtmani saqlaydi va uning raqamini qaytaradi."""
    if USE_DB:
        return db.add_order(DSN, product_id, variant, user, phone, price, prepay, size)
    data = _load()
    order_no = len(data["orders"]) + 1
    data["orders"].append({
        "no": order_no,
        "product_id": product_id,
        "variant": variant,
        "size": size,
        "price": price,
        "prepay": prepay,
        "status": "to'lov kutilmoqda",
        "receipt": None,
        "user_id": user.get("id"),
        "username": user.get("username"),
        "name": user.get("name"),
        "phone": phone,
        "created": datetime.now().isoformat(timespec="seconds"),
    })
    _save(data)
    return order_no


@_locked
def get_order(order_no: int) -> dict | None:
    if USE_DB:
        return db.get_order(DSN, order_no)
    for order in _load()["orders"]:
        if order["no"] == order_no:
            return order
    return None


@_locked
def set_order_status(order_no: int, status: str) -> dict | None:
    if USE_DB:
        return db.set_order_status(DSN, order_no, status)
    data = _load()
    for order in data["orders"]:
        if order["no"] == order_no:
            order["status"] = status
            _save(data)
            return order
    return None


@_locked
def mark_paid(order_no: int, receipt_file_id: str) -> dict | None:
    if USE_DB:
        return db.mark_paid(DSN, order_no, receipt_file_id)
    """Chek kelgach buyurtmani belgilaydi."""
    data = _load()
    for order in data["orders"]:
        if order["no"] == order_no:
            order["receipt"] = receipt_file_id
            order["status"] = "chek yuborilgan"
            _save(data)
            return order
    return None


@_locked
def link_order_user(order_no: int, user_id: int, username: str | None) -> dict | None:
    """Saytdan berilgan buyurtmani mijozning Telegram'iga ulaydi — shunda
    holat o'zgarganda xabar unga ham boradi."""
    if USE_DB:
        return db.link_order_user(DSN, order_no, user_id, username)
    data = _load()
    for order in data["orders"]:
        if order["no"] == order_no:
            order["user_id"] = user_id
            order["username"] = username
            _save(data)
            return order
    return None


@_locked
def recent_orders(limit: int = 15) -> list[dict]:
    if USE_DB:
        return db.recent_orders(DSN, limit)
    return list(reversed(_load()["orders"]))[:limit]


@_locked
def product_count() -> int:
    if USE_DB:
        return db.product_count(DSN)
    return len(_load()["products"])


@_locked
def remember_admin(user_id: int) -> None:
    if USE_DB:
        db.remember_admin(DSN, user_id)
        return
    """Admin botga yozganda uning ID'si eslab qolinadi — yangi buyurtma
    haqida xabar yuborish uchun (config'da ID emas, username bor)."""
    data = _load()
    if user_id not in data["admins"]:
        data["admins"].append(user_id)
        _save(data)


@_locked
def admin_ids() -> list[int]:
    if USE_DB:
        return db.admin_ids(DSN)
    return _load()["admins"]
