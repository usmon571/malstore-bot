"""
Sayt uchun ochiq (faqat o'qish) API — bot bilan bitta jarayonda ishlaydi.

Sayt kanaldagi tovarlarni shu yerdan oladi. Ma'lumot bot ishlatadigan
saqlagichdan (store.py) o'qiladi, shuning uchun bot tovar joylashi yoki
o'chirishi saytda darhol ko'rinadi.

    GET /api/health                    -> {"ok": true}
    GET /api/products?limit=24         -> {"products": [...], "channel", "bot"}
    GET /api/products/101              -> bitta tovar
    GET /api/products/101/images/0     -> tovarning 1-rasmi (JPEG)

Faqat ommaviy maydonlar beriladi: tannarx (base_prices), Poizon havolasi va
buyurtmalar tashqariga chiqmaydi. Rasmlar Telegram'dan bot tokeni bilan
olinadi — token saytga hech qachon berilmaydi, va faqat tovarga tegishli
file_id'lar beriladi (aks holda mijozlarning to'lov cheklari ham ochilib
qolardi).
"""
from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

import requests

import settings
import store

log = logging.getLogger("malstore-api")

LIST_TTL = 30          # tovarlar ro'yxati shuncha soniya keshda turadi
IMAGE_CACHE_SIZE = 150  # xotirada saqlanadigan rasmlar soni
MAX_LIMIT = 60

_db_lock = threading.Lock()   # baza ulanishi bot bilan umumiy — so'rovlarni ketma-ket qilamiz
_list_cache: dict = {"at": 0.0, "limit": 0, "items": []}
_images: "OrderedDict[str, bytes]" = OrderedDict()
_images_lock = threading.Lock()

_bot_username: Callable[[], str] = lambda: ""


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
    bot = _bot_username()
    channel = _channel_handle()
    return {
        "id": pid,
        "title": p.get("title", ""),
        "prices": p.get("prices") or {},
        "specs": _specs(p.get("caption", "")),
        "images": [f"{base_url}/api/products/{pid}/images/{i}" for i in range(len(images))],
        "created": p.get("created", ""),
        "order_url": f"https://t.me/{bot}?start={pid}" if bot else "",
        "post_url": f"https://t.me/{channel}/{message_ids[0]}" if channel and message_ids else "",
    }


def _load_products(limit: int) -> list[dict]:
    now = time.monotonic()
    cache = _list_cache
    if now - cache["at"] < LIST_TTL and cache["limit"] >= limit:
        return cache["items"][:limit]
    with _db_lock:
        items = store.list_products(MAX_LIMIT)
    cache.update(at=now, limit=MAX_LIMIT, items=items)
    return items[:limit]


def _get_product(pid: str) -> dict | None:
    with _db_lock:
        return store.get_product(pid)


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


class Handler(BaseHTTPRequestHandler):
    server_version = "MalStoreAPI/1.0"

    def log_message(self, fmt, *args):  # standart stderr logi o'rniga
        log.debug("%s %s", self.address_string(), fmt % args)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", settings.SITE_ORIGIN or "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Vary", "Origin")

    def _json(self, status: int, payload, max_age: int = 0):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", f"public, max-age={max_age}" if max_age else "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _base_url(self) -> str:
        if settings.API_PUBLIC_URL:
            return settings.API_PUBLIC_URL
        # Railway proksisi orqasida asl sxema shu sarlavhada keladi
        proto = self.headers.get("X-Forwarded-Proto", "http").split(",")[0].strip()
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
        return f"{proto}://{host}" if host else ""

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        try:
            if path in ("/", "/api", "/api/health"):
                return self._json(200, {"ok": True})

            if path == "/api/products":
                qs = parse_qs(url.query)
                try:
                    limit = max(1, min(MAX_LIMIT, int(qs.get("limit", ["24"])[0])))
                except ValueError:
                    limit = 24
                base = self._base_url()
                items = [public_product(p, base) for p in _load_products(limit)]
                bot = _bot_username()
                return self._json(200, {
                    "products": items,
                    "channel": f"https://t.me/{_channel_handle()}" if _channel_handle() else "",
                    "bot": f"https://t.me/{bot}" if bot else "",
                }, max_age=30)

            m = PRODUCT_RE.match(path)
            if m:
                p = _get_product(m.group(1))
                if not p:
                    return self._json(404, {"error": "topilmadi"})
                return self._json(200, public_product(p, self._base_url()), max_age=30)

            m = IMAGE_RE.match(path)
            if m:
                p = _get_product(m.group(1))
                images = (p or {}).get("images") or []
                index = int(m.group(2))
                if index >= len(images):
                    return self._json(404, {"error": "rasm topilmadi"})
                data = _telegram_file(images[index])
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400")
                self._cors()
                self.end_headers()
                self.wfile.write(data)
                return

            return self._json(404, {"error": "yo'q"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("API xatosi: %s", self.path)
            try:
                self._json(500, {"error": "server xatosi"})
            except Exception:
                pass


def start(port: int, bot_username: Callable[[], str]) -> ThreadingHTTPServer | None:
    """API'ni fon oqimida ishga tushiradi. port=0 bo'lsa o'chiq."""
    global _bot_username
    if not port:
        log.info("Sayt API'si o'chiq (PORT/API_PORT berilmagan)")
        return None
    _bot_username = bot_username
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="site-api", daemon=True).start()
    log.info("Sayt API'si: http://0.0.0.0:%s/api/products", port)
    return server
