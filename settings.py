"""
Sozlamalarni bir joyda yig'adi.

Ustuvorlik: environment variable > config.json > standart qiymat.

Shu tufayli loyiha ikki joyda bir xil ishlaydi:
  - kompyuterda — config.json orqali (hech narsa o'zgarmaydi)
  - Railway/serverda — environment variable orqali (maxfiy ma'lumot kodda emas)

Ma'lumot fayllari (store.json, translation_cache.json) DATA_DIR ichida
saqlanadi. Railway'da bu Volume yo'li bo'ladi, aks holda loyiha papkasi.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

# Ma'lumotlar papkasi — Railway'da Volume shu yerga ulanadi
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_file_config() -> dict:
    try:
        return json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:  # buzuq JSON bo'lsa ham ishga tushaylik
        print(f"config.json o'qilmadi: {e}")
        return {}


_FILE = _load_file_config()


def get(name: str, env: str, default=None, cast=None):
    """Avval environment variable, keyin config.json, keyin standart qiymat."""
    value = os.getenv(env)
    if value is None or value == "":
        value = _FILE.get(name, default)
    if value is None:
        return None
    if cast is not None and not isinstance(value, cast if isinstance(cast, type) else object):
        try:
            return cast(value)
        except (TypeError, ValueError):
            return default
    return value


def get_list(name: str, env: str) -> list[str]:
    """Ro'yxat: env'da vergul bilan ("ali,vali"), config.json'da massiv."""
    value = os.getenv(env)
    if value:
        return [v.strip() for v in value.split(",") if v.strip()]
    return list(_FILE.get(name, []))


BOT_TOKEN = get("bot_token", "BOT_TOKEN", "")
CHANNEL = get("channel", "CHANNEL", "")
ALLOWED_USERNAMES = get_list("allowed_usernames", "ALLOWED_USERNAMES")
def _admin_ids() -> list[int]:
    """Xabar yuboriladigan admin chat ID'lari.

    Bot admin ID'sini u botga yozganda o'zi eslab qoladi (store.remember_admin),
    lekin yangi serverda yoki baza tozalangandan keyin bu ro'yxat bo'sh bo'ladi —
    o'shanda savol va tugallanmagan buyurtma xabarlari hech kimga bormaydi.
    Shuning uchun ID'ni oldindan berib qo'yish mumkin:
        ADMIN_ID=844679956          yoki
        ADMIN_IDS=844679956,1234567
    """
    raw = get_list("admin_ids", "ADMIN_IDS")
    single = os.getenv("ADMIN_ID") or _FILE.get("admin_id")
    if single:
        raw.append(str(single))

    ids: list[int] = []
    for value in raw:
        try:
            ids.append(int(str(value).strip()))
        except ValueError:
            print(f"ADMIN_ID son emas, tashlab ketildi: {value!r}")
    return list(dict.fromkeys(ids))  # takrorlanmasin, tartib saqlansin


ADMIN_IDS = _admin_ids()

BRAND_LABEL = get("brand_label", "BRAND_LABEL", "Original")
MAX_IMAGES = int(get("max_images", "MAX_IMAGES", 12))
WEBAPP_URL = str(get("webapp_url", "WEBAPP_URL", "")).rstrip("#")

# Sayt API'si (api.py). Railway PORT'ni o'zi beradi; kompyuterda API_PORT
# yoki config.json'dagi "api_port" bilan yoqiladi. 0 — o'chiq.
API_PORT = int(os.getenv("PORT") or get("api_port", "API_PORT", 0) or 0)
# Rasm havolalari uchun tashqi manzil (bo'sh bo'lsa so'rovdagi Host olinadi)
API_PUBLIC_URL = str(get("api_public_url", "API_PUBLIC_URL", "")).rstrip("/")
# Faqat shu sayt API'dan o'qiy olsin (masalan https://usmon571.github.io).
# Bo'sh — hamma joydan (ma'lumot baribir ommaviy).
SITE_ORIGIN = str(get("site_origin", "SITE_ORIGIN", ""))

PREPAY_SHARE = float(get("prepay_share", "PREPAY_SHARE", 0.5))
CARD_NUMBER = str(get("card_number", "CARD_NUMBER", ""))
CARD_HOLDER = str(get("card_holder", "CARD_HOLDER", ""))

FALLBACK_CNY_RATE = float(get("cny_rate", "CNY_RATE", 1761.0))
ORDER_TIMEOUT_MINUTES = int(get("order_timeout_minutes", "ORDER_TIMEOUT_MINUTES", 30))

DELIVERY_RATES = _FILE.get("delivery_rates", {})
AVTO_KARGO_RATE = float(os.getenv("AVTO_KARGO_RATE") or DELIVERY_RATES.get("avto_kargo", 83_880))
AVIA_BREND_RATE = float(os.getenv("AVIA_BREND_RATE") or DELIVERY_RATES.get("avia_brend", 156_000))


def missing_required() -> list[str]:
    """Ishga tushishdan oldin tekshiriladigan majburiy qiymatlar."""
    problems = []
    if not BOT_TOKEN:
        problems.append("BOT_TOKEN")
    if not CHANNEL:
        problems.append("CHANNEL")
    if not ALLOWED_USERNAMES:
        problems.append("ALLOWED_USERNAMES")
    return problems
