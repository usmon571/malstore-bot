"""Настройки и общая логика записи (используется и ботом, и веб-сервером)."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets as _secrets
import socket
import subprocess
import threading
import time
from pathlib import Path

import requests

import ledger
import parsing
import receipt
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
    if row["kind"] == "move":
        return [[("↩️ Это не мой перевод", f"notown:{row['id']}"), ("🗑", f"del:{row['id']}")]]
    other = parsing.BUSINESS if row["wallet"] == parsing.PERSONAL else parsing.PERSONAL
    rows = [[("🏷 Категория", f"cats:{row['id']}"), (f"→ {WALLET_LABEL[other]}", f"wal:{row['id']}:{other}"),
             ("🗑", f"del:{row['id']}")]]
    if is_transferish(row):
        rows.append([("🔁 Перевод между моими картами", f"own:{row['id']}")])
    return rows


def tx_keyboard_json(row) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in line] for line in tx_buttons(row)]}


def describe(r) -> str:
    day = "" if r["ts"][:10] == ledger.now().strftime("%Y-%m-%d") else f" · {r['ts'][8:10]}.{r['ts'][5:7]}"
    if r["kind"] == "move":
        return f"🔁 {fmt(r['amount'])} сум · перевод между своими картами (не расход){day}"
    sign = "−" if r["kind"] == "exp" else "+"
    who = f" · {r['merchant']}" if r["merchant"] else ""
    note = purpose_of(r)
    why = f" · «{note[:40]}»" if note and note != r["merchant"] else ""
    icon = "💼" if r["wallet"] == parsing.BUSINESS else "👤"
    return f"{icon} {sign}{fmt(r['amount'])} сум · {r['category']}{who}{why}{day}"


# ---------- «на что потратили?» ----------

ASK = "❓ На что? Ответьте на это сообщение: «обед», «долг Акмалю», «подарок маме»…"
ASK_SOURCES = ("sms", "wallet")   # автоматические записи: из SMS и Apple Pay назначение неизвестно


def purpose_of(r) -> str:
    """Пояснение владельца (note без списка товаров из чека)."""
    try:
        note = r["note"] or ""
    except (KeyError, IndexError):
        return ""
    return "" if note.startswith("🧾") else note.split(" · 🧾")[0]


def needs_purpose(r) -> bool:
    return r["kind"] == "exp" and r["source"] in ASK_SOURCES and not (r["note"] or "")  # чек с товарами — уже ясно


def set_purpose(row, text: str):
    """Ответ на «на что?»: сохранить пояснение, а если по нему понятна категория — поменять её только у этой записи."""
    text = re.sub(r"\s+", " ", text).strip()
    biz = any(re.search(parsing._word(t), text, re.I) for t in parsing.BUSINESS_TAGS)
    for t in parsing.BUSINESS_TAGS:
        text = re.sub(parsing._word(t), " ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" ,.;")[:100]
    cat = parsing.guess_category(text) if row["kind"] == "exp" else None
    wallet = parsing.BUSINESS if biz else (parsing.wallet_for(cat) if cat else None)
    old = row["note"] or ""
    items = old if old.startswith("🧾") else (old.split(" · ", 1)[1] if " · 🧾" in old else "")
    ledger.set_purpose(row["id"], f"{text} · {items}" if items else text, cat, wallet)
    return ledger.get(row["id"])


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


def is_transferish(row) -> bool:
    """Может быть половиной перевода между своими картами: перевод-расход или любой приход."""
    return row["kind"] == "inc" or (row["kind"] == "exp" and row["category"] == "Переводы")


def mark_own_transfer(row) -> object:
    """Пометить запись (и вторую половину перевода, если есть) как перевод между своими картами."""
    ledger.set_kind(row["id"], "move")
    pair = ledger.transfer_pair(row)
    if pair:
        ledger.set_kind(pair["id"], "move")
    for last4 in parsing.cards_in(row["raw"] or ""):  # карты из такого SMS — тоже свои
        ledger.card_seen(last4, parsing.brand_in(row["raw"]))
    return ledger.get(row["id"])


def unmark_own_transfer(row) -> object:
    """«Это не мой перевод»: вернуть обе половины в расход/доход."""
    for r in [row] + ledger.move_partners(row):
        ledger.set_kind(r["id"], "inc" if r["category"] == parsing.INCOME_CAT else "exp")
    return ledger.get(row["id"])


def _remember_cards(p: parsing.Parsed) -> None:
    """Карта из SMS — своя (SMS приходит владельцу). Если карт в SMS несколько (перевод) — не гадаем, чья."""
    if len(p.cards) == 1:
        ledger.card_seen(p.cards[0], p.brand, p.balance)


def record(p: parsing.Parsed, source: str, raw: str = "", ts=None, check_dup: bool = True):
    """Сохраняет запись. Возвращает (row | None, duplicate: bool)."""
    _remember_cards(p)  # остаток обновляем даже для дубликата (Apple Pay уже записал покупку, SMS принесла баланс)
    if check_dup and ledger.is_duplicate(p.amount, p.kind, source):
        return None, True
    cat, wallet = p.category, p.wallet
    l_cat, l_wal = ledger.learned(p.merchant)
    if p.kind == "exp":
        if l_cat:
            cat = l_cat
            wallet = parsing.wallet_for(cat) if wallet == parsing.PERSONAL else wallet
        if l_wal:
            wallet = l_wal
    tx_id = ledger.add(p.amount, p.kind, cat, p.merchant, p.note, source, raw, wallet, p.days_ago, ts=ts or p.ts)
    row = ledger.get(tx_id)
    if source != "manual" and is_transferish(row):
        own = ledger.own_cards()
        # 1) в SMS обе карты свои; 2) пришла вторая половина перевода (та же сумма, ±15 минут)
        if (len(p.cards) >= 2 and all(c in own for c in p.cards)) or ledger.transfer_pair(row):
            row = mark_own_transfer(row)
    return row, False


# ---------- чеки soliq.uz ----------

RETRY_MINUTES = (10, 30, 60, 180, 360, 720)  # чек обычно доходит до налоговой за минуты, иногда — за часы


def process_receipt(ref: receipt.Ref, fetcher=receipt.fetch) -> tuple[str, object, object]:
    """Возвращает (статус, row, Receipt): new | linked | already. Ошибки — receipt.ReceiptError."""
    known = ledger.meta_get(ref.key)
    if known and ledger.get(int(known)):
        return "already", ledger.get(int(known)), None
    rc = fetcher(ref)
    p = receipt.to_parsed(rc)
    when = ref.when or ledger.now()
    raw = receipt.raw_for(ref, rc)
    match = ledger.find_match(p.amount, p.kind, when)
    if match:
        ledger.attach_receipt(match["id"], p.merchant, p.note, raw,
                              p.category if p.category != parsing.OTHER else None)
        row, status = ledger.get(match["id"]), "linked"
    else:
        row, _ = record(p, "receipt", raw, ts=when, check_dup=False)
        status = "new"
    ledger.meta_set(ref.key, str(row["id"]))
    return status, row, rc


def receipt_message(status: str, row, rc) -> str:
    if status == "already":
        return "🧾 Этот чек уже записан: " + describe(row)
    head = "🧾 Чек привязан к покупке: " if status == "linked" else "🧾 ✅ "
    lines = [head + describe(row)]
    if rc and rc.items:
        for name, qty, price in rc.items[:8]:
            q = f" ×{qty:g}" if qty and qty != 1 else ""
            lines.append(f"• {name[:45]}{q} — {fmt(price)}")
        if len(rc.items) > 8:
            lines.append(f"… и ещё {len(rc.items) - 8}")
    if status == "new":
        lines[0] += budget_warning(row["category"])
    return "\n".join(lines)


NOTIFY_RETRY_SECONDS = (5, 15, 30, 60, 120, 300)  # интернет на ПК пропадает — не теряем уведомления


def _send(payload: dict, row) -> bool:
    res = tg("sendMessage", **payload)
    if not (isinstance(res, dict) and res.get("ok")):
        return False
    mid = (res.get("result") or {}).get("message_id")
    if mid and row is not None:
        ledger.meta_set(f"msg:{mid}", str(row["id"]))
    return True


def notify(text: str, row=None, silent: bool = False, background: bool = True) -> None:
    """Сообщение владельцу. В фоне и с повторами: Apple Pay не ждёт, а сообщение дойдёт, когда вернётся интернет."""
    owner = owner_id()
    if not (owner and BOT_TOKEN):
        return
    payload = {"chat_id": owner, "text": text, "disable_web_page_preview": True, "disable_notification": silent}
    if row is not None:
        payload["reply_markup"] = tx_keyboard_json(row)

    def run():
        if _send(payload, row):
            return
        for pause in NOTIFY_RETRY_SECONDS:
            time.sleep(pause)
            if _send(payload, row):
                return
        log.warning("уведомление не доставлено: %s", text[:80])

    if background:
        threading.Thread(target=run, daemon=True).start()
    else:
        run()


# Чеки, которых ещё нет в налоговой, хранятся в базе — после перезапуска бот продолжает их проверять.
_pending_lock = threading.Lock()


def pending_receipts() -> dict:
    try:
        return json.loads(ledger.meta_get("pending_receipts") or "{}")
    except ValueError:
        return {}


def _set_pending(link: str, attempt: int | None) -> None:
    with _pending_lock:
        p = pending_receipts()
        if attempt is None:
            p.pop(link, None)
        else:
            p[link] = attempt
        ledger.meta_set("pending_receipts", json.dumps(p))


def retry_receipt(ref: receipt.Ref, attempt: int = 0) -> None:
    """Чек ещё не в базе налоговой — пробуем позже в фоне и пишем в Telegram, когда получится."""
    if attempt >= len(RETRY_MINUTES):
        _set_pending(ref.link, None)
        return notify(f"🧾 Чек №{ref.r} так и не появился на сайте налоговой. Запишите сумму вручную.")
    _set_pending(ref.link, attempt)

    def run():
        try:
            status, row, rc = process_receipt(ref)
            _set_pending(ref.link, None)
            notify(receipt_message(status, row, rc), row)
        except receipt.ReceiptPending:
            retry_receipt(ref, attempt + 1)
        except receipt.ReceiptError as e:
            if attempt + 1 < len(RETRY_MINUTES) and "не отвечает" in str(e):
                return retry_receipt(ref, attempt + 1)
            _set_pending(ref.link, None)
            notify(f"🧾 Чек №{ref.r} не прочитан: {e}")
        except Exception as e:  # noqa: BLE001
            log.exception("чек: %s", e)

    t = threading.Timer(RETRY_MINUTES[attempt] * 60, run)
    t.daemon = True
    t.start()


def resume_receipts() -> int:
    """При запуске: снова поставить в очередь чеки, которые ждали налоговую до перезапуска."""
    n = 0
    for link, attempt in pending_receipts().items():
        ref = receipt.from_link(link)
        if ref:
            retry_receipt(ref, int(attempt))
            n += 1
        else:
            _set_pending(link, None)
    return n


def ingest_receipt(link: str) -> tuple[bool, str]:
    """Чек из Shortcuts / веба: обработать сейчас или поставить в очередь, уведомить владельца."""
    ref = receipt.from_link(link)
    if not ref:
        return False, "это не ссылка чека soliq.uz"
    try:
        status, row, rc = process_receipt(ref)
    except receipt.ReceiptPending:
        retry_receipt(ref)
        notify(f"🧾 Чек №{ref.r} ещё не дошёл до налоговой — проверю через {RETRY_MINUTES[0]} минут.")
        return True, "в очереди"
    except receipt.ReceiptError as e:
        return False, str(e)
    notify(receipt_message(status, row, rc), row)
    return True, describe(row)


def ingest_text(text: str, source: str) -> tuple[bool, str]:
    """Автоматический вход (Shortcuts Apple Pay / SMS): парсит, пишет, уведомляет владельца в Telegram."""
    if source == "sms" and not parsing.is_transaction(text):
        log.info("SMS пропущена: не операция (код, реклама)")
        return True, "не операция — пропущено"
    p = parsing.parse_bank(text, usd_rate())
    if not p:
        return False, "сумма не найдена"
    row, dup = record(p, source, text)
    if dup:
        return True, "дубликат пропущен"
    ask = needs_purpose(row)
    notify(f"✅ {describe(row)}" + budget_warning(row["category"]) + (f"\n{ASK}" if ask else ""), row, silent=not ask)
    return True, describe(row)
