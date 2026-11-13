"""
Sayt API'si — bot bilan bitta jarayonda, botning o'z saqlagichi ustida.

Alohida baza yo'q: sayt ko'rsatadigan tovarlar ham, saytdan berilgan
buyurtmalar ham bot ishlatadigan saqlagichda (Neon PostgreSQL yoki
store.json). Bot tovar joylasa — saytda ko'rinadi; saytdan buyurtma
kelsa — admin uni Telegram'da bot buyurtmasi bilan bir xil ko'radi.

    GET  /                               -> sayt (site/index.html)
    GET  /api/health                     -> {"ok": true}
    GET  /api/products?limit=24          -> {"products": [...], "channel", "bot"}
    GET  /api/products/101               -> bitta tovar
    GET  /api/products/101/images/0      -> tovarning 1-rasmi (JPEG)
    POST /api/orders                     -> yangi buyurtma (JSON)
    GET  /api/orders/7?t=TOKEN           -> buyurtma holati
    POST /api/orders/7/receipt?t=TOKEN   -> to'lov cheki (rasm yoki PDF, xom tana)

Tashqariga faqat ommaviy maydonlar chiqadi: tannarx (base_prices), Poizon
havolasi va boshqalarning buyurtmalari berilmaydi. Buyurtmani faqat uning
egasi ko'radi — yaratilganda beriladigan TOKEN bilan. Rasmlar Telegram'dan
bot tokeni bilan olinadi; token saytga hech qachon berilmaydi.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import re
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

import requests

import settings
import store

log = logging.getLogger("malstore-api")

LIST_TTL = 30          # tovarlar ro'yxati shuncha soniya keshda turadi
IMAGE_CACHE_SIZE = 150  # xotirada saqlanadigan rasmlar soni
MAX_LIMIT = 60
MAX_JSON_BODY = 8 * 1024
MAX_RECEIPT = 10 * 1024 * 1024

SITE_DIR = Path(__file__).parent / "site"
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}
RECEIPT_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    "application/pdf",
}
# Chek faqat to'lov bosqichidagi buyurtmaga qabul qilinadi
RECEIPT_STATUSES = {"to'lov kutilmoqda", "chek yuborilgan"}

_list_cache: dict = {"at": 0.0, "limit": 0, "items": []}
_images: "OrderedDict[str, bytes]" = OrderedDict()
_images_lock = threading.Lock()


@dataclass
class Hooks:
    """Bot beradigan funksiyalar. Buyurtma qoidalari (razmer, oldindan
    to'lov) va Telegram xabarlari botda yashaydi — API ularni takrorlamaydi,
    shuning uchun saytdagi buyurtma botdagisi bilan aynan bir xil hisoblanadi."""
    bot_username: Callable[[], str] = lambda: ""
    needs_size: Callable[[dict], bool] = lambda product: False
    foot_guide: Callable[[dict], bool] = lambda product: False
    clean_size: Callable[[str], "str | None"] = lambda text: text.strip() or None
    prepay: Callable[[dict, str], "tuple[str, bool]"] = lambda product, variant: ("", False)
    # Yangi buyurtma haqida adminlarga xabar
    order_created: Callable[[dict, dict], None] = lambda order, product: None
    # Chekni adminlarga yuborib, Telegram file_id'sini qaytaradi (xato bo'lsa — exception)
    receipt: Callable[[dict, dict, bytes, str], str] = lambda order, product, data, ctype: ""


_hooks = Hooks()


# ---------- buyurtma tokeni ----------

def order_token(order_no: int) -> str:
    """Buyurtma egasining kaliti. Bazada saqlanmaydi — bot tokenidan hisoblanadi,
    shuning uchun qayta ishga tushganda ham o'zgarmaydi va sxema kengaymaydi."""
    key = (settings.BOT_TOKEN or "").encode()
    return hmac.new(key, f"order:{order_no}".encode(), hashlib.sha256).hexdigest()[:20]


def check_order_token(order_no: int, token: str) -> bool:
    return hmac.compare_digest(order_token(order_no), str(token or ""))


def telegram_link(order_no: int) -> str:
    """Saytdagi buyurtmani mijozning Telegram'iga ulaydigan havola."""
    bot = _hooks.bot_username()
    return f"https://t.me/{bot}?start=o{order_no}_{order_token(order_no)}" if bot else ""


# ---------- spamdan himoya ----------

class RateLimit:
    """Oddiy sirpanuvchi oyna: kalit (IP) bo'yicha `count` ta so'rov `per` soniyada."""

    def __init__(self, count: int, per: float):
        self.count, self.per = count, per
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > 5000:  # xotira o'smasin
                self._hits = {k: q for k, q in self._hits.items() if q and now - q[-1] < self.per}
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.per:
                q.popleft()
            if len(q) >= self.count:
                return False
            q.append(now)
            return True


ORDER_LIMIT = RateLimit(5, 600)        # bitta IP — 10 daqiqada 5 ta buyurtma
ORDER_GLOBAL = RateLimit(60, 3600)     # hammasi bo'lib — soatiga 60 ta
RECEIPT_LIMIT = RateLimit(10, 600)


# ---------- kiritilgan ma'lumotni tekshirish ----------

def clean_name(text: str) -> str | None:
    name = " ".join(str(text or "").split())
    name = "".join(ch for ch in name if ch.isprintable())
    return name if 2 <= len(name) <= 60 else None


def clean_phone(text: str) -> str | None:
    """"90 123 45 67", "+998 (90) 123-45-67" → "+998901234567"."""
    digits = re.sub(r"\D", "", str(text or ""))
    if len(digits) == 9:  # mahalliy raqam, kodsiz
        digits = "998" + digits
    return "+" + digits if 10 <= len(digits) <= 15 else None


# ---------- tovarni ommaviy ko'rinishga o'tkazish ----------

TAG_RE = re.compile(r"<[^>]+>")
LEAD_SYMBOLS_RE = re.compile(r"^[^\w]+", re.UNICODE)


def _specs(caption: str) -> list[list[str]]:
    """Post matnidan "📋 Xususiyatlari:" blokini ajratib oladi."""
    text = html.unescape(TAG_RE.sub("", caption or ""))
    specs, inside = [], False
    for raw in text.splitlines():
        line = raw.strip()
        if "Xususiyatlari" in line:
            inside = True
            continue
        if not inside:
            continue
        if not line or line.startswith("💰"):
            break
        line = LEAD_SYMBOLS_RE.sub("", line)
        key, sep, value = line.partition(":")
        if sep and key.strip() and value.strip():
            specs.append([key.strip(), value.strip()])
    return specs


def _channel_handle() -> str:
    return str(settings.CHANNEL or "").lstrip("@")


def public_product(p: dict, base_url: str = "") -> dict:
    pid = str(p["id"])
    images = p.get("images") or []
    message_ids = p.get("message_ids") or []
    bot = _hooks.bot_username()
    channel = _channel_handle()
    needs_size = bool(_hooks.needs_size(p))
    return {
        "id": pid,
        "title": p.get("title", ""),
        "prices": p.get("prices") or {},
        "specs": _specs(p.get("caption", "")),
        "images": [f"{base_url}/api/products/{pid}/images/{i}" for i in range(len(images))],
        "created": p.get("created", ""),
        "needs_size": needs_size,
        "foot_guide": needs_size and bool(_hooks.foot_guide(p)),
        "order_url": f"https://t.me/{bot}?start={pid}" if bot else "",
        "post_url": f"https://t.me/{channel}/{message_ids[0]}" if channel and message_ids else "",
    }


def public_order(order: dict, product: dict | None = None) -> dict:
    """Buyurtma egasiga ko'rsatiladigan qism (telefon va ism unga allaqachon ma'lum)."""
    product = product or {}
    variant = order.get("variant", "")
    by_cost = bool((product.get("base_prices") or {}).get(variant))
    payment = None
    if settings.CARD_NUMBER:
        payment = {
            "card_number": settings.CARD_NUMBER,
            "card_holder": settings.CARD_HOLDER,
            "by_cost": by_cost,
            "share": settings.PREPAY_SHARE,
        }
    return {
        "no": order["no"],
        "status": order.get("status", ""),
        "product": {"id": str(order.get("product_id", "")), "title": product.get("title", "")},
        "variant": variant,
        "size": order.get("size", ""),
        "price": order.get("price", ""),
        "prepay": order.get("prepay", ""),
        "receipt": bool(order.get("receipt")),
        "created": order.get("created", ""),
        "payment": payment,
        "telegram_linked": bool(order.get("user_id")),
        "telegram_url": telegram_link(order["no"]),
    }


def _load_products(limit: int) -> list[dict]:
    now = time.monotonic()
    cache = _list_cache
    if now - cache["at"] < LIST_TTL and cache["limit"] >= limit:
        return cache["items"][:limit]
    items = store.list_products(MAX_LIMIT)
    cache.update(at=now, limit=MAX_LIMIT, items=items)
    return items[:limit]


# ---------- rasmlar (Telegram'dan, token yashirin) ----------

def _telegram_file(file_id: str) -> bytes:
    with _images_lock:
        if file_id in _images:
            _images.move_to_end(file_id)
            return _images[file_id]
    token = settings.BOT_TOKEN
    info = requests.get(
        f"https://api.telegram.org/bot{token}/getFile",
        params={"file_id": file_id}, timeout=15,
    ).json()
    if not info.get("ok"):
        raise LookupError(info.get("description", "getFile xatosi"))
    data = requests.get(
        f"https://api.telegram.org/file/bot{token}/{info['result']['file_path']}",
        timeout=30,
    ).content
    with _images_lock:
        _images[file_id] = data
        while len(_images) > IMAGE_CACHE_SIZE:
            _images.popitem(last=False)
    return data


# ---------- HTTP ----------

PRODUCT_RE = re.compile(r"^/api/products/(\d+)$")
IMAGE_RE = re.compile(r"^/api/products/(\d+)/images/(\d+)$")
ORDER_RE = re.compile(r"^/api/orders/(\d+)$")
RECEIPT_RE = re.compile(r"^/api/orders/(\d+)/receipt$")


class Handler(BaseHTTPRequestHandler):
    server_version = "MalStoreAPI/1.1"

    def log_message(self, fmt, *args):  # standart stderr logi o'rniga
        log.debug("%s %s", self.address_string(), fmt % args)

    # --- javob yordamchilari

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", settings.SITE_ORIGIN or "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Vary", "Origin")

    def _send(self, status: int, body: bytes, ctype: str, cache: str = "no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload, max_age: int = 0):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        cache = f"public, max-age={max_age}" if max_age else "no-store"
        self._send(status, body, "application/json; charset=utf-8", cache)

    def _error(self, status: int, message: str, **extra):
        self._json(status, {"error": message, **extra})

    def _base_url(self) -> str:
        if settings.API_PUBLIC_URL:
            return settings.API_PUBLIC_URL
        # Proksi (tunnel, Railway) orqasida asl sxema shu sarlavhada keladi
        proto = self.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip()
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
        return f"{proto}://{host}" if host else ""

    def _client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[0].strip() or self.client_address[0]

    def _content_length(self) -> int:
        try:
            return int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return -1

    def _read_json(self) -> dict | None:
        length = self._content_length()
        if length <= 0 or length > MAX_JSON_BODY:
            return None
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _guard(self, fn):
        try:
            return fn()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("API xatosi: %s %s", self.command, self.path)
            try:
                self._error(500, "Server xatosi. Birozdan keyin qayta urinib ko'ring.")
            except Exception:
                pass

    # --- marshrutlar

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        self._guard(self._get)

    def do_POST(self):
        self._guard(self._post)

    def _get(self):
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        qs = parse_qs(url.query)

        if path in ("/api", "/api/health"):
            return self._json(200, {"ok": True})

        if path == "/api/products":
            try:
                limit = max(1, min(MAX_LIMIT, int(qs.get("limit", ["24"])[0])))
            except ValueError:
                limit = 24
            base = self._base_url()
            items = [public_product(p, base) for p in _load_products(limit)]
            bot = _hooks.bot_username()
            return self._json(200, {
                "products": items,
                "channel": f"https://t.me/{_channel_handle()}" if _channel_handle() else "",
                "bot": f"https://t.me/{bot}" if bot else "",
            }, max_age=30)

        m = PRODUCT_RE.match(path)
        if m:
            p = store.get_product(m.group(1))
            if not p:
                return self._error(404, "Tovar topilmadi")
            return self._json(200, public_product(p, self._base_url()), max_age=30)

        m = IMAGE_RE.match(path)
        if m:
            p = store.get_product(m.group(1))
            images = (p or {}).get("images") or []
            index = int(m.group(2))
            if index >= len(images):
                return self._error(404, "Rasm topilmadi")
            data = _telegram_file(images[index])
            return self._send(200, data, "image/jpeg", "public, max-age=86400")

        m = ORDER_RE.match(path)
        if m:
            order_no = int(m.group(1))
            if not check_order_token(order_no, qs.get("t", [""])[0]):
                return self._error(403, "Buyurtma topilmadi")
            order = store.get_order(order_no)
            if not order:
                return self._error(404, "Buyurtma topilmadi")
            return self._json(200, public_order(order, store.get_product(order["product_id"])))

        if not path.startswith("/api"):
            return self._static(path)
        return self._error(404, "Yo'q")

    def _post(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/api/orders":
            return self._create_order()
        m = RECEIPT_RE.match(path)
        if m:
            return self._upload_receipt(int(m.group(1)))
        return self._error(404, "Yo'q")

    # --- sayt fayllari

    def _static(self, path: str):
        rel = "index.html" if path == "/" else path.lstrip("/")
        root = SITE_DIR.resolve()
        target = (root / rel).resolve()
        if root not in target.parents or not target.is_file():
            return self._error(404, "Sahifa topilmadi")
        ctype = STATIC_TYPES.get(target.suffix.lower())
        if not ctype:
            return self._error(404, "Sahifa topilmadi")
        # HTML har safar tekshirilsin — sayt yangilansa darhol ko'rinsin
        cache = "no-cache" if target.suffix == ".html" else "public, max-age=3600"
        return self._send(200, target.read_bytes(), ctype, cache)

    # --- buyurtma

    def _create_order(self):
        ip = self._client_ip()
        if not ORDER_LIMIT.hit(ip) or not ORDER_GLOBAL.hit("*"):
            return self._error(429, "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring.")

        body = self._read_json()
        if body is None:
            return self._error(400, "Noto'g'ri so'rov")
        if body.get("website"):  # yashirin maydon — odam uni to'ldirmaydi, robot to'ldiradi
            return self._error(400, "Noto'g'ri so'rov")

        product = store.get_product(str(body.get("product_id", "")))
        if not product:
            return self._error(404, "Tovar topilmadi — u sotuvdan olingan bo'lishi mumkin.")

        prices = product.get("prices") or {}
        variant = str(body.get("variant", ""))
        if variant not in prices:
            if len(prices) == 1 and not variant:
                variant = next(iter(prices))
            else:
                return self._error(400, "Variantni tanlang.", field="variant")

        size = ""
        if _hooks.needs_size(product):
            size = _hooks.clean_size(str(body.get("size", ""))) or ""
            if not size:
                return self._error(
                    400, "Razmerni kiriting — masalan 42, 26.5 sm yoki M.", field="size"
                )

        name = clean_name(body.get("name", ""))
        if not name:
            return self._error(400, "Ismingizni kiriting.", field="name")
        phone = clean_phone(body.get("phone", ""))
        if not phone:
            return self._error(400, "Telefon raqam noto'g'ri. Masalan: +998 90 123 45 67", field="phone")

        prepay, _by_cost = _hooks.prepay(product, variant)
        order_no = store.add_order(
            product_id=product["id"],
            variant=variant,
            user={"id": None, "username": None, "name": name},
            phone=phone,
            price=prices[variant],
            prepay=prepay,
            size=size,
        )
        order = store.get_order(order_no)
        log.info("Saytdan buyurtma #%s: tovar %s, %s", order_no, product["id"], variant)

        try:
            _hooks.order_created(order, product)
        except Exception:
            # Buyurtma saqlandi — xabar ketmagani uni bekor qilmasin
            log.exception("sayt buyurtmasi #%s haqida adminga xabar ketmadi", order_no)

        return self._json(201, {**public_order(order, product), "token": order_token(order_no)})

    def _upload_receipt(self, order_no: int):
        qs = parse_qs(urlparse(self.path).query)
        if not check_order_token(order_no, qs.get("t", [""])[0]):
            return self._error(403, "Buyurtma topilmadi")
        if not RECEIPT_LIMIT.hit(self._client_ip()):
            return self._error(429, "Juda ko'p urinish. Birozdan keyin qayta urinib ko'ring.")

        order = store.get_order(order_no)
        if not order:
            return self._error(404, "Buyurtma topilmadi")
        if order.get("status") not in RECEIPT_STATUSES:
            return self._error(409, "Bu buyurtma uchun chek endi qabul qilinmaydi.")

        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype not in RECEIPT_TYPES:
            return self._error(415, "Chek rasm (JPG, PNG) yoki PDF bo'lsin.")
        length = self._content_length()
        if length <= 0:
            return self._error(400, "Fayl bo'sh.")
        if length > MAX_RECEIPT:
            return self._error(413, "Fayl juda katta (ko'pi bilan 10 MB).")
        data = self.rfile.read(length)

        product = store.get_product(order["product_id"]) or {}
        try:
            file_id = _hooks.receipt(order, product, data, ctype)
        except Exception:
            log.exception("sayt cheki #%s adminga yuborilmadi", order_no)
            return self._error(
                503, "Chekni hozir yuborib bo'lmadi. Telegram orqali yuboring.",
                telegram_url=telegram_link(order_no),
            )
        order = store.mark_paid(order_no, file_id) or order
        log.info("Saytdan chek: buyurtma #%s", order_no)
        return self._json(200, public_order(order, product))


def start(port: int, hooks: Hooks | None = None) -> ThreadingHTTPServer | None:
    """API'ni fon oqimida ishga tushiradi. port=0 bo'lsa o'chiq."""
    global _hooks
    if hooks is not None:
        _hooks = hooks
    if not port:
        log.info("Sayt API'si o'chiq (PORT/API_PORT berilmagan)")
        return None
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    except OSError as e:
        # Port band bo'lsa bot baribir ishlasin — faqat sayt o'chiq qoladi
        log.error("Sayt API'si ishga tushmadi (port %s): %s", port, e)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="site-api", daemon=True).start()
    log.info("Sayt: http://localhost:%s  ·  API: /api/products", port)
    return server
