"""Фискальные чеки soliq.uz: QR с фото или ссылка → магазин, сумма, товары.

QR на чеке — ссылка вида https://ofd.soliq.uz/check?t=<ККМ>&r=<номер>&c=<ГГГГММДДччммсс>&s=<фиск. признак>.
Суммы в ссылке нет: её отдаёт API налоговой, которым пользуется сама страница чека.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import requests

import parsing

API = "https://new-ofd.soliq.uz/api/payment"
API_MOBILE = API + "/mobile-dashboard"
# Ключ подписи из открытого кода страницы ofd.soliq.uz (браузер подписывает им каждый запрос чека).
# Если налоговая его сменит, чеки перестанут читаться — тогда обновите значение здесь.
SIGN_KEY = b"thisIsPaymentSecretKey123@#"

LINK_RE = re.compile(r"https?://(?:new-)?ofd\.soliq\.uz/[^\s<>\"']+", re.I)
_LEGAL = re.compile(
    r"\b(?:mas['ʼ‘’`]?uliyati\s+cheklangan\s+jamiyat|mchj|mch|xk|qk|aj|ooo|ооо|llc|ltd|чп|ип|yatt|ятт|xf|ф/х|f/x)\b\.?",
    re.I)


class ReceiptError(Exception):
    """Чек не прочитан; текст — для пользователя."""


class ReceiptPending(ReceiptError):
    """Чек ещё не дошёл до налоговой (обычно появляется в течение часа)."""


@dataclass
class Ref:
    t: str          # ККМ / терминал
    r: str          # номер чека
    c: str          # дата-время ГГГГММДДччммсс
    s: str = ""     # фискальный признак
    h: str = ""     # хэш (мобильные чеки)
    kind: str = "CHECK"
    link: str = ""

    @property
    def key(self) -> str:
        return f"receipt:{self.t}:{self.r}:{self.c}"

    @property
    def when(self) -> datetime | None:
        try:
            return datetime.strptime(self.c[:14], "%Y%m%d%H%M%S")
        except ValueError:
            return None


@dataclass
class Receipt:
    total: int
    merchant: str = ""
    address: str = ""
    items: list = field(default_factory=list)   # [(название, кол-во, сумма)]
    refund: bool = False
    card: int = 0
    cash: int = 0


def find_link(text: str) -> str | None:
    m = LINK_RE.search(text or "")
    return m.group(0).rstrip(").,;") if m else None


def from_link(url: str) -> Ref | None:
    u = urlparse(url.replace("&amp;", "&"))
    if "soliq.uz" not in u.netloc.lower():
        return None
    q = {k: v[0].strip() for k, v in parse_qs(u.query).items() if v}
    t, r, c = q.get("t", ""), q.get("r", ""), q.get("c", "")
    if not (t and r and re.fullmatch(r"\d{14}", c)):
        return None
    path = u.path.lower()
    kind = "CHECK"
    if "/epi" in path:
        kind = "AVANS" if "avans" in path else "CREDIT" if "kredit" in path else "CHECK_EPI"
    return Ref(t=t, r=r, c=c, s=q.get("s", ""), h=q.get("h", ""), kind=kind, link=url)


def decode_qr(image: bytes) -> list[str]:
    """Все QR/штрихкоды на фото (текстом). Пробует несколько вариантов — чеки часто снимают мелко и криво."""
    import zxingcpp
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image))).convert("L")
    tries = [img, ImageOps.autocontrast(img)]
    if max(img.size) < 1600:
        tries.append(img.resize((img.width * 2, img.height * 2)))
    for im in tries:
        found = [b.text for b in zxingcpp.read_barcodes(im, try_rotate=True, try_downscale=True) if b.text]
        if found:
            return found
    return []


def _num(v) -> float:
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def clean_name(name: str) -> str:
    s = _LEGAL.sub(" ", name or "")
    s = re.sub(r"[\"«»“”„']", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -,.")
    return s[:60]


def parse_payload(data: dict) -> Receipt:
    """data — поле `data` ответа API (как его показывает страница чека)."""
    extra = data.get("extraInfo") or {}
    items = []
    for d in data.get("paymentDetails") or []:
        name = str(d.get("name") or d.get("productName") or "").strip()
        items.append((name, _num(d.get("amount")), round(_num(d.get("price")))))
    card, cash = round(_num(data.get("cardTotal"))), round(_num(data.get("cashTotal")))
    total = card + cash or sum(p for _, _, p in items)
    return Receipt(total=total, merchant=clean_name(str(extra.get("companyName") or data.get("kkmName") or "")),
                   address=str(extra.get("address") or "").strip(), items=items,
                   refund=str(data.get("isRefund")) == "1", card=card, cash=cash)


def fetch(ref: Ref, timeout: int = 20) -> Receipt:
    ts = str(int(time.time()))
    sig = hmac.new(SIGN_KEY, f"{ref.t}:{ref.r}:{ts}".encode(), hashlib.sha256).hexdigest()
    body = {"terminalId": ref.t, "paymentNo": ref.r, "paymentDate": ref.c, "paymentType": ref.kind}
    if ref.s:
        body["fiscalSign"] = ref.s
    if ref.h:
        body["fiscalSignHash"] = ref.h
    try:
        resp = requests.post(API_MOBILE if ref.h else API, json=body, timeout=timeout,
                             headers={"Accept": "application/json", "X-Timestamp": ts, "X-Signature": sig})
    except requests.RequestException:
        raise ReceiptError("сайт налоговой не отвечает")
    try:
        js = resp.json()
    except ValueError:
        js = {}
    data = js.get("data") if isinstance(js, dict) else None
    if resp.ok and isinstance(data, dict):
        rc = parse_payload(data)
        if rc.total <= 0:
            raise ReceiptError("в чеке нет суммы")
        return rc
    msg = str((js or {}).get("message") or "")
    if resp.status_code == 404 or "shakllan" in msg.lower():
        raise ReceiptPending(msg or "чек ещё не дошёл до налоговой")
    raise ReceiptError(msg or f"ошибка сайта налоговой ({resp.status_code})")


def to_parsed(rc: Receipt) -> parsing.Parsed:
    names = " ".join(n for n, _, _ in rc.items)
    cat = parsing.guess_category(rc.merchant) or parsing.guess_category(names) or parsing.OTHER
    if rc.refund:
        return parsing.Parsed(rc.total, "inc", parsing.INCOME_CAT, rc.merchant, note_for(rc))
    return parsing.Parsed(rc.total, "exp", cat, rc.merchant, note_for(rc), wallet=parsing.wallet_for(cat))


def note_for(rc: Receipt, limit: int = 300) -> str:
    parts = []
    for n, q, _ in rc.items:
        qty = f" ×{q:g}" if q and q != 1 else ""
        parts.append(f"{n[:40]}{qty}")
    s = "🧾 " + ", ".join(parts)
    return s if len(s) <= limit else s[:limit - 1] + "…"


def raw_for(ref: Ref, rc: Receipt) -> str:
    return json.dumps({"link": ref.link, "items": rc.items[:40], "address": rc.address}, ensure_ascii=False)
