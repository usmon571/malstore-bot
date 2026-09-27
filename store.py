"""
Tovarlar va buyurtmalar uchun oddiy saqlagich (JSON fayl).

Botning o'zi xotirada ishlaydi va post yuborilgach hammasini unutadi —
shu modul esa tovarni ID bilan saqlab qoladi, keyin xaridor o'sha ID
orqali (kanaldagi havola bosilganda) buyurtma bera oladi.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("malstore-store")

import settings

# Railway'da bu Volume yo'li bo'ladi — aks holda fayl har deployda yo'qoladi
STORE_FILE = settings.DATA_DIR / "store.json"
FIRST_ID = 100


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
ORDER_DEFAULTS = {"price": "", "prepay": "", "status": "to'lov kutilmoqda", "receipt": None}
PRODUCT_DEFAULTS = {"caption": "", "message_ids": []}


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


def next_product_id() -> str:
    data = _load()
    data["counter"] += 1
    _save(data)
    return str(data["counter"])


def save_product(
    product_id: str, url: str, title: str, prices: dict, arrival: str,
    caption: str = "", message_ids: list[int] | None = None,
) -> None:
    data = _load()
    data["products"][product_id] = {
        "id": product_id,
        "url": url,
        "title": title,
        "prices": prices,
        "arrival": arrival,
        "caption": caption,
        "message_ids": message_ids or [],
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    _save(data)


def get_product(product_id: str) -> dict | None:
    return _load()["products"].get(str(product_id).strip().lstrip("#"))


def update_product(product_id: str, **fields) -> dict | None:
    data = _load()
    product = data["products"].get(str(product_id).strip().lstrip("#"))
    if not product:
        return None
    product.update(fields)
    _save(data)
    return product


def delete_product(product_id: str) -> dict | None:
    data = _load()
    product = data["products"].pop(str(product_id).strip().lstrip("#"), None)
    if product:
        _save(data)
    return product


def list_products(limit: int = 20) -> list[dict]:
    return list(reversed(list(_load()["products"].values())))[:limit]


def add_order(
    product_id: str, variant: str, user: dict, phone: str,
    price: str = "", prepay: str = "",
) -> int:
    """Buyurtmani saqlaydi va uning raqamini qaytaradi."""
    data = _load()
    order_no = len(data["orders"]) + 1
    data["orders"].append({
        "no": order_no,
        "product_id": product_id,
        "variant": variant,
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


def get_order(order_no: int) -> dict | None:
    for order in _load()["orders"]:
        if order["no"] == order_no:
            return order
    return None


def set_order_status(order_no: int, status: str) -> dict | None:
    data = _load()
    for order in data["orders"]:
        if order["no"] == order_no:
            order["status"] = status
            _save(data)
            return order
    return None


def mark_paid(order_no: int, receipt_file_id: str) -> dict | None:
    """Chek kelgach buyurtmani belgilaydi."""
    data = _load()
    for order in data["orders"]:
        if order["no"] == order_no:
            order["receipt"] = receipt_file_id
            order["status"] = "chek yuborilgan"
            _save(data)
            return order
    return None


def recent_orders(limit: int = 15) -> list[dict]:
    return list(reversed(_load()["orders"]))[:limit]


def product_count() -> int:
    return len(_load()["products"])


def remember_admin(user_id: int) -> None:
    """Admin botga yozganda uning ID'si eslab qolinadi — yangi buyurtma
    haqida xabar yuborish uchun (config'da ID emas, username bor)."""
    data = _load()
    if user_id not in data["admins"]:
        data["admins"].append(user_id)
        _save(data)


def admin_ids() -> list[int]:
    return _load()["admins"]
