"""Настройки и общая логика записи (используется и ботом, и веб-сервером)."""
from __future__ import annotations

import json
import logging
import os
import secrets as _secrets
import socket
import subprocess
import time
from pathlib import Path

import requests

import ledger
import parsing
from parsing import WALLET_LABEL, fmt

log = logging.getLogger("expenses")
BASE = Path(__file__).parent


def _file() -> dict:
    try:
        return json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"config.json не прочитан: {e}")
        return {}


_F = _file()


def cfg(name: str, env: str, default=None):
    v = os.getenv(env)
    return v if v not in (None, "") else _F.get(name, default)


BOT_TOKEN = str(cfg("bot_token", "EXP_BOT_TOKEN", "") or "")
PORT = int(cfg("port", "EXP_PORT", 8090))
PUBLIC_URL = str(cfg("public_url", "EXP_PUBLIC_URL", "") or "").rstrip("/")
OWNERS = {int(x) for x in (_F.get("owner_ids") or [])} | {
    int(x) for x in os.getenv("EXP_OWNER_IDS", "").split(",") if x.strip()}
DAILY_AT = str(cfg("daily_report_time", "EXP_DAILY_TIME", "22:00"))


def secret() -> str:
    s = cfg("secret", "EXP_SECRET", "") or ledger.meta_get("secret")
    if not s:
        s = _secrets.token_urlsafe(18)
        ledger.meta_set("secret", s)
    return s


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"


_ts_cache: dict = {"v": None, "t": 0.0}


def tailscale_ip() -> str | None:
    """IP компьютера в сети Tailscale (100.x.x.x), если Tailscale установлен и включён."""
    if time.time() - _ts_cache["t"] < 300:
        return _ts_cache["v"]
    ip = None
    for exe in ("tailscale", r"C:\Program Files\Tailscale\tailscale.exe"):
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=4,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            cand = out.stdout.strip().splitlines()[0] if out.returncode == 0 and out.stdout.strip() else ""
            if cand.startswith("100."):
                ip = cand
                break
        except Exception:
            continue
    _ts_cache.update(v=ip, t=time.time())
    return ip


def urls() -> dict:
    """Адреса сервера: public_url из конфига > Tailscale (работает везде) > домашняя сеть."""
    out = {}
    if PUBLIC_URL:
        out["public"] = PUBLIC_URL
    ts = tailscale_ip()
    if ts:
        out["tailscale"] = f"http://{ts}:{PORT}"
    out["lan"] = f"http://{lan_ip()}:{PORT}"
    return out


def base_url() -> str:
    u = urls()
    return u.get("public") or u.get("tailscale") or u["lan"]


def owner_id() -> int | None:
    if OWNERS:
        return next(iter(OWNERS))
    v = ledger.meta_get("owner")
    return int(v) if v else None


def is_owner(uid: int) -> bool:
    if OWNERS:
        return uid in OWNERS
    o = ledger.meta_get("owner")
    return bool(o) and int(o) == uid


_rate = {"v": float(cfg("usd_rate", "EXP_USD_RATE", 12500)), "t": 0.0}


def usd_rate() -> float:
    """Курс USD→UZS из ЦБ Узбекистана (бесплатно), раз в сутки; при ошибке — прошлое значение."""
    if time.time() - _rate["t"] > 86400:
        try:
            r = requests.get("https://cbu.uz/ru/arkhiv-kursov-valyut/json/USD/", timeout=8).json()
            _rate["v"] = float(r[0]["Rate"])
        except Exception as e:
            log.warning("курс ЦБ недоступен: %s", e)
        _rate["t"] = time.time()
    return _rate["v"]


def tg(method: str, **payload):
    try:
        return requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload, timeout=15).json()
    except Exception as e:
        log.warning("telegram %s: %s", method, e)
        return {}


def tx_buttons(row) -> list[list[tuple[str, str]]]:
    """Кнопки под записью: (текст, callback_data)."""
    other = parsing.BUSINESS if row["wallet"] == parsing.PERSONAL else parsing.PERSONAL
    return [[("🏷 Категория", f"cats:{row['id']}"), (f"→ {WALLET_LABEL[other]}", f"wal:{row['id']}:{other}"),
             ("🗑", f"del:{row['id']}")]]


def tx_keyboard_json(row) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in line] for line in tx_buttons(row)]}


def describe(r) -> str:
    sign = "−" if r["kind"] == "exp" else "+"
    who = f" · {r['merchant']}" if r["merchant"] else ""
    day = "" if r["ts"][:10] == ledger.now().strftime("%Y-%m-%d") else f" · {r['ts'][8:10]}.{r['ts'][5:7]}"
    icon = "💼" if r["wallet"] == parsing.BUSINESS else "👤"
    return f"{icon} {sign}{fmt(r['amount'])} сум · {r['category']}{who}{day}"


def budget_warning(category: str) -> str:
    lim = ledger.budget_get(category)
    if not lim:
        return ""
    spent = ledger.month_spent(category)
    pct = spent * 100 // lim
    if pct >= 100:
        return f"\n🚨 Лимит «{category}» превышен: {fmt(spent)} из {fmt(lim)}"
    if pct >= 80:
        return f"\n⚠️ «{category}»: {pct}% месячного лимита ({fmt(spent)} из {fmt(lim)})"
    return ""


def record(p: parsing.Parsed, source: str, raw: str = ""):
    """Сохраняет запись. Возвращает (row | None, duplicate: bool)."""
    if ledger.is_duplicate(p.amount, p.kind, source):
        return None, True
    cat, wallet = p.category, p.wallet
    l_cat, l_wal = ledger.learned(p.merchant)
    if p.kind == "exp":
        if l_cat:
            cat = l_cat
            wallet = parsing.wallet_for(cat) if wallet == parsing.PERSONAL else wallet
        if l_wal:
            wallet = l_wal
    tx_id = ledger.add(p.amount, p.kind, cat, p.merchant, p.note, source, raw, wallet, p.days_ago)
    return ledger.get(tx_id), False


def ingest_text(text: str, source: str) -> tuple[bool, str]:
    """Автоматический вход (Shortcuts Apple Pay / SMS): парсит, пишет, уведомляет владельца в Telegram."""
    p = parsing.parse_bank(text, usd_rate())
    if not p:
        return False, "сумма не найдена"
    row, dup = record(p, source, text)
    if dup:
        return True, "дубликат пропущен"
    owner = owner_id()
    if owner and BOT_TOKEN:
        tg("sendMessage", chat_id=owner, text=f"✅ {describe(row)}" + budget_warning(row["category"]),
           reply_markup=tx_keyboard_json(row), disable_notification=True)
    return True, describe(row)
