"""Бот учёта расходов (личное + бизнес). Запуск: venv\\Scripts\\python.exe expenses\\main.py"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update  # noqa: E402
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,  # noqa: E402
                          MessageHandler, filters)

import core  # noqa: E402
import ledger  # noqa: E402
import parsing  # noqa: E402
import receipt  # noqa: E402
import web  # noqa: E402
from parsing import BUSINESS, PERSONAL, WALLET_LABEL, fmt  # noqa: E402

# EXP_LOG_FILE (задаёт start_expenses.bat) — лог в файл с ротацией: 3 файла по 2 МБ, в UTF-8
_log_file = os.getenv("EXP_LOG_FILE")
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO,
                    handlers=[RotatingFileHandler(_log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")]
                    if _log_file else None)
log = logging.getLogger("expenses")
logging.getLogger("httpx").setLevel(logging.WARNING)

TZ = dt.timezone(dt.timedelta(hours=5))  # Ташкент, без перехода на летнее время

MENU = ReplyKeyboardMarkup(
    [["📅 Сегодня", "🗓 Неделя", "📆 Месяц"], ["📊 Дашборд", "↩️ Отменить", "❓ Помощь"]],
    resize_keyboard=True, input_field_placeholder="15000 такси")

HELP = (
    "<b>Как записывать</b>\n"
    "• <code>15000 такси</code> · <code>45к обед</code> · <code>1.2 млн аренда</code>\n"
    "• <code>+3 млн зарплата</code> — доход\n"
    "• <code>вчера 20000 бензин</code> — задним числом\n"
    "• <code>500000 карго #б</code> — в 💼 бизнес (карго, товары, реклама попадают туда сами)\n"
    "• Перешлите SMS/уведомление Humo, Uzcard или банка — разберу сам. Автоматически (Alliance Pay, Payme) — /sms\n"
    "• Apple Pay — один раз настройте /apple, дальше всё автоматически\n"
    "• 🧾 Фото QR-кода с чека (soliq.uz) или ссылка из него — запишу магазин, сумму и товары\n\n"
    "<b>Исправить</b>: кнопки под записью, или ответьте (reply) на запись новой суммой.\n"
    "<b>На что?</b> На расход из SMS / Apple Pay бот спросит — ответьте (reply) парой слов: "
    "<code>обед</code>, <code>долг Акмалю</code>. Пропущенные — /why\n\n"
    "<b>Команды</b>\n/today /week /month — отчёты\n/dash — дашборд с графиками\n"
    "/find корзинка — поиск\n/limit еда 2000000 — лимит в месяц (0 — снять), /limits\n"
    "/cards — карты и остатки (переводы между своими картами — не расход)\n"
    "/why — расходы без пояснения «на что»\n"
    "/undo — удалить последнюю · /export — Excel/CSV\n/apple — Apple Pay · /sms — SMS банка · /status — адреса сервера"
)

TITLES = {"today": "Сегодня", "week": "Последние 7 дней", "month": "Этот месяц",
          "yesterday": "Вчера", "lastmonth": "Прошлый месяц"}


# ---------- общее ----------

async def guard(update: Update) -> bool:
    uid = update.effective_user.id
    if not core.owner_id():  # первый написавший становится владельцем
        ledger.meta_set("owner", str(uid))
        log.info("Владелец: %s", uid)
    if core.is_owner(uid):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("Это личный бот.")
    return False


def kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in line] for line in rows])


def tx_kb(row) -> InlineKeyboardMarkup:
    return kb(core.tx_buttons(row))


def bar(v: int, mx: int, w: int = 8) -> str:
    n = round(w * v / mx) if mx else 0
    return "▰" * n + "▱" * (w - n)


def delta(cur: int, prev: int) -> str:
    if not prev:
        return ""
    d = (cur - prev) * 100 // prev
    return f" ({'+' if d >= 0 else ''}{d}% к прошлому)"


def report(period: str, wallet: str | None = None, ref: dt.datetime | None = None) -> str:
    s = ledger.summary(period, wallet, ref)
    head = TITLES.get(period, period) + (f" · {WALLET_LABEL[wallet]}" if wallet else "")
    lines = [f"<b>{head}</b>"]
    prev = {"week": "lastweek", "month": "lastmonth"}.get(period)
    cmp = ""
    if prev:
        cmp = delta(s["expense"], ledger.summary(prev, wallet, ref)["expense"]) if period == "week" else ""
    lines.append(f"Потрачено: <b>{fmt(s['expense'])}</b> сум{cmp}")
    if s["income"]:
        lines.append(f"Доход: {fmt(s['income'])} · итог {'+' if s['income'] >= s['expense'] else '−'}"
                     f"{fmt(abs(s['income'] - s['expense']))}")
    if not wallet and len(s["wallets"]) > 1:
        lines.append(" · ".join(f"{WALLET_LABEL[w]} {fmt(v['exp'])}" for w, v in sorted(s["wallets"].items(), reverse=True)))
    if s["by_category"]:
        mx = s["by_category"][0]["s"]
        lines.append("")
        for c in s["by_category"][:10]:
            pct = c["s"] * 100 // s["expense"] if s["expense"] else 0
            lines.append(f"{bar(c['s'], mx)} {c['category']} — {fmt(c['s'])} · {pct}%")
    else:
        lines.append("\nЗаписей нет.")
    if s["top_merchants"] and period != "today":
        lines.append("\nЧаще всего: " + ", ".join(f"{m['merchant']} {fmt(m['s'])}" for m in s["top_merchants"][:3]))
    if period == "month" and s["expense"]:
        today = (ref or ledger.now()).day
        per_day = s["expense"] // today
        days_in = (ledger.period_range("month", ref)[1] - dt.timedelta(days=1)).day
        lines.append(f"\nВ среднем {fmt(per_day)}/день → прогноз на месяц ≈ {fmt(per_day * days_in)}")
        last = ledger.summary("lastmonth", wallet, ref)["expense"]
        if last:
            lines.append(f"Прошлый месяц целиком: {fmt(last)}")
    over = [b for b in s["budgets"] if b["spent"] >= b["limit"] * 0.8]
    if over and period in ("month", "week"):
        lines.append("\n" + "\n".join(f"{'🚨' if b['spent'] >= b['limit'] else '⚠️'} {b['category']}: "
                                      f"{fmt(b['spent'])} / {fmt(b['limit'])}" for b in over))
    return "\n".join(lines)


def delete_tx(row) -> InlineKeyboardMarkup:
    """Удаляет запись, но помнит её — под сообщением кнопка «↩️ Вернуть»."""
    ledger.meta_set(f"del:{row['id']}", json.dumps(dict(row), ensure_ascii=False))
    ledger.delete(row["id"])
    return kb([[("↩️ Вернуть", f"undel:{row['id']}")]])


def report_kb(period: str, wallet: str | None) -> InlineKeyboardMarkup:
    opts = [("Всё", "all"), (WALLET_LABEL[PERSONAL], PERSONAL), (WALLET_LABEL[BUSINESS], BUSINESS)]
    return kb([[(("• " if (wallet or "all") == w else "") + t, f"rep:{period}:{w}") for t, w in opts]])


async def send_report(u: Update, period: str):
    if await guard(u):
        await u.message.reply_text(report(period), parse_mode="HTML", reply_markup=report_kb(period, None))


# ---------- команды ----------

async def start(u: Update, c: ContextTypes.DEFAULT_TYPE):
    if await guard(u):
        await u.message.reply_text("Привет! Я считаю ваши деньги: личные и бизнес отдельно.\n\n" + HELP,
                                   parse_mode="HTML", reply_markup=MENU)


async def help_cmd(u: Update, c):
    if await guard(u):
        await u.message.reply_text(HELP, parse_mode="HTML", reply_markup=MENU)


async def today(u, c):
    await send_report(u, "today")


async def week(u, c):
    await send_report(u, "week")


async def month(u, c):
    await send_report(u, "month")


async def dash(u, c):
    if not await guard(u):
        return
    url = f"{core.base_url()}/?key={core.secret()}"
    note = "" if core.tailscale_ip() or core.PUBLIC_URL else \
        "\n\n(Сейчас работает только в домашнем Wi-Fi. Для доступа отовсюду включите Tailscale — /status)"
    await u.message.reply_text(f"📊 Дашборд:\n{url}{note}", disable_web_page_preview=True)


async def status(u, c):
    if not await guard(u):
        return
    us = core.urls()
    lines = ["<b>Сервер</b> работает ✅"]
    if "public" in us:
        lines.append(f"Публичный адрес: {us['public']}")
    if "tailscale" in us:
        lines.append(f"Tailscale (работает отовсюду): {us['tailscale']}")
    else:
        lines.append("Tailscale: не найден. Бесплатно: tailscale.com/download → установить на ПК и iPhone, "
                     "войти одним аккаунтом. После этого перезапустите бота.")
    lines.append(f"Домашняя сеть: {us['lan']}")
    lines.append(f"Курс USD: {fmt(core.usd_rate())}")
    await u.message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


async def undo(u, c):
    if not await guard(u):
        return
    i = ledger.last_id()
    row = ledger.get(i) if i else None
    if row:
        await u.message.reply_text(f"🗑 Удалено: {core.describe(row)}", reply_markup=delete_tx(row))
    else:
        await u.message.reply_text("Нечего удалять.")


async def export(u, c):
    if not await guard(u):
        return
    period = (c.args[0] if c.args else "all")
    await u.message.reply_document(ledger.export_csv(period).encode("utf-8-sig"),
                                   filename=f"rashody-{ledger.now():%Y-%m-%d}.csv",
                                   caption="Откройте в Excel или Google Таблицах")


async def find(u, c: ContextTypes.DEFAULT_TYPE):
    if not await guard(u):
        return
    q = " ".join(c.args).strip()
    if not q:
        return await u.message.reply_text("Пример: /find корзинка")
    rows = ledger.search(q)
    if not rows:
        return await u.message.reply_text("Ничего не нашёл.")
    total = sum(r["amount"] for r in rows if r["kind"] == "exp")
    await u.message.reply_text("\n".join(f"{r['ts'][8:10]}.{r['ts'][5:7]} {core.describe(r)}" for r in rows)
                               + f"\n\nИтого расходов: {fmt(total)}")


async def limit(u, c: ContextTypes.DEFAULT_TYPE):
    if not await guard(u):
        return
    if len(c.args) < 2:
        return await u.message.reply_text("Пример: /limit еда 2000000\nКатегории: " + ", ".join(parsing.ALL_CATEGORIES))
    name = " ".join(c.args[:-1]).lower()
    cat = next((x for x in parsing.ALL_CATEGORIES if x.lower().startswith(name)), None) or \
        next((x for x in parsing.ALL_CATEGORIES if name in x.lower()), None)
    off = c.args[-1] == "0"
    p = None if off else parsing.parse_manual(c.args[-1])
    if not cat or (not off and not p):
        return await u.message.reply_text("Не понял категорию или сумму. Пример: /limit кафе 800к")
    ledger.budget_set(cat, 0 if off else p.amount)
    await u.message.reply_text(f"Лимит «{cat}»: {'снят' if off else fmt(p.amount) + ' сум в месяц'}")


async def limits(u, c):
    if not await guard(u):
        return
    b = ledger.summary("month")["budgets"]
    if not b:
        return await u.message.reply_text("Лимитов нет. Пример: /limit кафе 800к")
    await u.message.reply_text("\n".join(
        f"{bar(min(x['spent'], x['limit']), x['limit'])} {x['category']}: {fmt(x['spent'])} / {fmt(x['limit'])}"
        for x in b))


async def apple(u, c):
    if not await guard(u):
        return
    url = f"{core.base_url()}/api/ingest?key={core.secret()}"
    warn = "" if core.tailscale_ip() or core.PUBLIC_URL else \
        "\n⚠️ Tailscale не найден — покупки вне дома не дойдут. Сначала /status.\n"
    await u.message.reply_text(
        "🍎 <b>Apple Pay → бот</b> (один раз, 3 минуты)\n"
        f"{warn}\n"
        "1. iPhone → <b>Команды</b> → <b>Автоматизация</b> → «+» → <b>Транзакция</b>.\n"
        "2. Выберите все карты в Wallet, «Запускать сразу» ✅, «Уведомлять» ❌.\n"
        "3. Добавьте действие <b>«Получить содержимое URL»</b>:\n"
        f"   URL: <code>{url}</code>\n"
        "   Метод: <b>POST</b>, Тело запроса: <b>JSON</b>, поля:\n"
        "   • <code>merchant</code> → переменная «Торговец»\n"
        "   • <code>amount</code> → переменная «Сумма»\n"
        "4. Готово. Каждая оплата из Wallet сразу появится здесь с кнопками.\n\n"
        "Если та же покупка придёт ещё и SMS — второй раз не посчитается.",
        parse_mode="HTML", disable_web_page_preview=True)


def cards_text() -> str:
    rows = [r for r in ledger.cards()]
    if not rows:
        return ""
    lines, total = [], 0
    for r in rows:
        name = f"{r['brand'] or 'Карта'} *{r['last4']}"
        if r["balance"] is not None:
            total += r["balance"]
            lines.append(f"💳 {name} — <b>{fmt(r['balance'])}</b> сум · {r['balance_ts'][8:10]}.{r['balance_ts'][5:7]} "
                         f"{r['balance_ts'][11:16]}")
        else:
            lines.append(f"💳 {name} — остаток ещё не приходил")
    if total and len(lines) > 1:
        lines.append(f"Всего на картах: <b>{fmt(total)}</b> сум")
    return "\n".join(lines)


async def cards_cmd(u, c):
    if not await guard(u):
        return
    t = cards_text()
    await u.message.reply_text(
        (f"<b>Мои карты</b> (остаток из последней SMS банка)\n{t}\n\n" if t else
         "Карт пока нет — они появятся сами из SMS банка (/sms).\n\n") +
        "Переводы между своими картами не считаются расходом: бот находит обе SMS (та же сумма, ±15 минут) "
        "или кнопка «🔁 Перевод между моими картами» под записью.",
        parse_mode="HTML")


async def sms(u, c):
    """SMS банка (Alliance Pay / Asia Alliance Bank, Humo, Uzcard — и всё, что оплачено через Payme/Click с карты)."""
    if not await guard(u):
        return
    url = f"{core.base_url()}/api/ingest?key={core.secret()}&source=sms"
    await u.message.reply_text(
        "📩 <b>SMS банка → бот автоматически</b> (Alliance Pay, Payme, Click…)\n\n"
        "У Alliance Pay и Payme нет открытого API, но каждая оплата с карты Humo/Uzcard приходит SMS от банка — "
        "её и будем пересылать. Платежи через Payme тоже приходят этой SMS.\n\n"
        "1. iPhone → <b>Команды</b> → <b>Автоматизация</b> → «+» → <b>Сообщение</b>.\n"
        "2. <b>Отправитель</b> → номер, с которого приходят SMS о картах: <b>13131</b> (Humo) "
        "и другие (<code>AAB</code>, <code>UZCARD</code>). Если номера нет в списке — сначала в «Контактах» "
        "создайте контакт «Humo» с номером <code>13131</code>. "
        "«Запускать сразу» ✅ → <b>Далее</b> → <b>Новая пустая автоматизация</b>.\n"
        "3. Действие <b>«Получить содержимое URL»</b>:\n"
        f"   URL: <code>{url}</code>\n"
        "   Метод <b>POST</b>, Тело запроса <b>JSON</b>, поле:\n"
        "   • <code>text</code> → «Входные данные команды» → <b>Содержимое</b>\n"
        "4. Готово. Повторите для каждого отправителя (13131, банк, UZCARD).\n\n"
        "Бот читает всё от этого номера: коды подтверждения и рекламу пропускает, время берёт из SMS. "
        "На каждый расход спросит «на что?» — ответьте (reply) парой слов, по ним он поправит категорию.\n\n"
        "Покупка из Apple Pay и та же SMS не посчитаются дважды. "
        "Без автоматизации SMS можно просто переслать сюда. Чек из Payme — скриншот QR или ссылка, как обычный чек.",
        parse_mode="HTML", disable_web_page_preview=True)


BUTTONS = {"📅 Сегодня": today, "🗓 Неделя": week, "📆 Месяц": month,
           "📊 Дашборд": dash, "↩️ Отменить": undo, "↩️ Отменить последнее": undo, "❓ Помощь": help_cmd}


# ---------- текст ----------

async def text(u: Update, c):
    if not await guard(u):
        return
    msg = u.message
    t = (msg.text or msg.caption or "").strip()
    if t in BUTTONS:
        return await BUTTONS[t](u, c)

    # ответ на запись бота: только сумма → исправить сумму, слова → «на что потрачено»
    rep = msg.reply_to_message
    if rep and rep.from_user and rep.from_user.is_bot:
        tx_id = ledger.meta_get(f"msg:{rep.message_id}")
        row = ledger.get(int(tx_id)) if tx_id else None
        p = parsing.parse_manual(t) if row and AMOUNT_ONLY.fullmatch(t) else None
        if p:
            ledger.set_amount(row["id"], p.amount)
            row = ledger.get(row["id"])
            return await msg.reply_text("✏️ Исправлено: " + core.describe(row), reply_markup=tx_kb(row))
        if row and re.search(r"[^\W\d_]", t):
            row = core.set_purpose(row, t)
            sent = await msg.reply_text("📝 Записал: " + core.describe(row), reply_markup=tx_kb(row))
            ledger.meta_set(f"msg:{sent.message_id}", str(row["id"]))
            return

    link = receipt.find_link(t)
    if link:
        return await handle_receipt(msg, link)

    forwarded = msg.forward_origin is not None
    if forwarded or parsing.looks_like_bank(t):
        p, source = parsing.parse_bank(t, core.usd_rate()), "sms"
    else:
        p, source = parsing.parse_manual(t), "manual"
    if not p:
        return await msg.reply_text("Не нашёл сумму 🤔 Пример: <code>15000 такси</code>", parse_mode="HTML")
    row, dup = core.record(p, source, t)
    if dup:
        return await msg.reply_text("♻️ Эта покупка уже пришла из Apple Pay — второй раз не считаю.")
    ask = f"\n{core.ASK}" if core.needs_purpose(row) else ""
    sent = await msg.reply_text("✅ " + core.describe(row) + core.budget_warning(row["category"]) + ask,
                                reply_markup=tx_kb(row))
    ledger.meta_set(f"msg:{sent.message_id}", str(row["id"]))


# ответ только суммой («25000», «25к», «25 000 сум») — исправить сумму; «2 кофе» — это уже пояснение
AMOUNT_ONLY = re.compile(r"[+\-−]?\s*\d[\d\s.,]*\s*(?:к|k|тыс\.?|тысяч|млн|mln|сум|so['ʻ‘’]?m|uzs)?\.?", re.I)


async def why(u, c):
    """Расходы из SMS / Apple Pay за 7 дней без пояснения — каждый отдельным сообщением, чтобы ответить на него."""
    if not await guard(u):
        return
    rows = ledger.unexplained(ledger.now() - dt.timedelta(days=7), limit=15)
    if not rows:
        return await u.message.reply_text("👍 У всех расходов за неделю есть пояснение.")
    await u.message.reply_text(f"❓ Без пояснения: {len(rows)}. Ответьте (reply) на каждое — на что потрачено.")
    for r in rows:
        sent = await u.message.reply_text(core.describe(r), reply_markup=tx_kb(r))
        ledger.meta_set(f"msg:{sent.message_id}", str(r["id"]))


# ---------- чеки soliq.uz ----------

RECEIPT_HELP = ("Сфотографируйте QR-код на чеке поближе и пришлите сюда. "
                "Или наведите камеру iPhone на QR, откройте ссылку и нажмите «Поделиться» → этот бот.")


async def handle_receipt(msg, link: str):
    ref = receipt.from_link(link)
    if not ref:
        return await msg.reply_text("Это ссылка soliq.uz, но не чек (нет номера или даты).")
    wait = await msg.reply_text("🧾 Читаю чек…")
    try:
        status, row, rc = await asyncio.to_thread(core.process_receipt, ref)
        log.info("чек %s/%s: %s, %s сум", ref.t, ref.r, status, row["amount"])
    except receipt.ReceiptPending:
        log.info("чек %s/%s: ещё нет в налоговой, повторю", ref.t, ref.r)
        core.retry_receipt(ref)
        return await wait.edit_text(f"🧾 Чек ещё не дошёл до налоговой (так бывает первые минуты-часы). "
                                    f"Проверю сам через {core.RETRY_MINUTES[0]} минут и напишу.")
    except receipt.ReceiptError as e:
        log.warning("чек %s/%s: %s", ref.t, ref.r, e)
        return await wait.edit_text(f"🧾 Не получилось прочитать чек: {e}")
    await wait.edit_text(core.receipt_message(status, row, rc), reply_markup=tx_kb(row))
    ledger.meta_set(f"msg:{wait.message_id}", str(row["id"]))


async def photo(u: Update, c):
    if not await guard(u):
        return
    msg = u.message
    f = msg.photo[-1] if msg.photo else msg.document
    data = None
    for attempt in range(3):  # интернет на ПК бывает медленным — фото качаем с повтором
        try:
            data = bytes(await (await f.get_file(read_timeout=60)).download_as_bytearray(read_timeout=60))
            break
        except Exception as e:  # noqa: BLE001
            log.warning("фото не скачалось (%s/3): %s", attempt + 1, e)
            await asyncio.sleep(2)
    if data is None:
        return await msg.reply_text("Не смог скачать фото — на сервере плохой интернет. Пришлите ещё раз через минуту.")
    try:
        codes = await asyncio.to_thread(receipt.decode_qr, data)
    except Exception as e:  # noqa: BLE001 — битое фото, не картинка и т.п.
        log.warning("QR: %s", e)
        codes = []
    link = next((x for x in map(receipt.find_link, codes) if x), None)
    log.info("фото: QR %s, чек %s", len(codes), "да" if link else "нет")
    if link:
        return await handle_receipt(msg, link)
    if codes:
        return await msg.reply_text("Нашёл QR, но это не чек soliq.uz.\n" + RECEIPT_HELP)
    if msg.caption:  # фото с подписью «45000 обед» — как обычный текст
        return await text(u, c)
    await msg.reply_text("Не нашёл QR-код на фото 🤔\n" + RECEIPT_HELP)


# ---------- кнопки ----------

async def cb(u: Update, c):
    q = u.callback_query
    if not core.is_owner(q.from_user.id):
        return await q.answer("Нет доступа")
    act, _, rest = q.data.partition(":")
    if act == "rep":
        period, w = rest.split(":")
        wallet = None if w == "all" else w
        await q.edit_message_text(report(period, wallet), parse_mode="HTML", reply_markup=report_kb(period, wallet))
        return await q.answer()

    tx_id = int(rest.split(":")[0])
    if act == "undel":
        saved = ledger.meta_get(f"del:{tx_id}")
        if saved and not ledger.get(tx_id):
            ledger.restore(json.loads(saved))
        row = ledger.get(tx_id)
        if row:
            await q.edit_message_text("↩️ Возвращено: " + core.describe(row), reply_markup=tx_kb(row))
        return await q.answer()
    row = ledger.get(tx_id)
    if not row:
        await q.edit_message_text("Запись уже удалена")
        return await q.answer()
    if act == "del":
        await q.edit_message_text(f"🗑 Удалено: {core.describe(row)}", reply_markup=delete_tx(row))
    elif act == "cats":
        cats = parsing.ALL_CATEGORIES
        rows = [[(cats[i], f"set:{tx_id}:{i}") for i in range(r, min(r + 2, len(cats)))] for r in range(0, len(cats), 2)]
        rows.append([("← Назад", f"back:{tx_id}")])
        await q.edit_message_reply_markup(kb(rows))
    elif act == "back":
        await q.edit_message_reply_markup(tx_kb(row))
    elif act == "set":
        cat = parsing.ALL_CATEGORIES[int(rest.split(":")[1])]
        kind_fix = cat == parsing.INCOME_CAT
        wallet = BUSINESS if cat in parsing.BUSINESS_CATS else None
        ledger.set_category(tx_id, cat, wallet)
        if kind_fix != (row["kind"] == "inc"):
            with ledger._lock, ledger._conn() as con:
                con.execute("UPDATE tx SET kind=? WHERE id=?", ("inc" if kind_fix else "exp", tx_id))
        row = ledger.get(tx_id)
        extra = "\n(запомнил для этого магазина)" if row["merchant"] else ""
        await q.edit_message_text("✅ " + core.describe(row) + extra, reply_markup=tx_kb(row))
    elif act == "own":
        row = core.mark_own_transfer(row)
        await q.edit_message_text("✅ " + core.describe(row) + "\n(вторая половина перевода — тоже, если нашлась)",
                                  reply_markup=tx_kb(row))
    elif act == "notown":
        row = core.unmark_own_transfer(row)
        await q.edit_message_text("✅ " + core.describe(row), reply_markup=tx_kb(row))
    elif act == "wal":
        ledger.set_wallet(tx_id, rest.split(":")[1])
        row = ledger.get(tx_id)
        await q.edit_message_text("✅ " + core.describe(row), reply_markup=tx_kb(row))
    await q.answer()


# ---------- автосводки ----------

def daily_time() -> dt.time:
    hh, mm = (int(x) for x in core.DAILY_AT.split(":"))
    return dt.time(hh, mm)


def due_day(now: dt.datetime) -> dt.datetime:
    """За какой день положен итог: после 22:00 — за сегодня, раньше — за вчера (если ПК спал или был выключен)."""
    return now if now.time() >= daily_time() else now - dt.timedelta(days=1)


async def nightly(c: ContextTypes.DEFAULT_TYPE):
    """22:00: итог дня (или напоминание), по воскресеньям — неделя, 1-го числа — прошлый месяц + CSV. Плюс бэкап.
    Если ПК спал в 22:00, итог придёт, когда он проснётся (и не дважды)."""
    now = ledger.now()
    day = due_day(now)
    key = day.strftime("%Y-%m-%d")
    if ledger.meta_get("last_nightly") == key:
        return
    ledger.meta_set("last_nightly", key)
    try:
        ledger.backup()
    except Exception as e:
        log.warning("бэкап не удался: %s", e)
    owner = core.owner_id()
    if not owner:
        return
    late = day.date() != now.date()
    when = "Вчера" if late else "Сегодня"
    if ledger.count_on(day) == 0:
        await c.bot.send_message(owner, f"📝 {when} ничего не записано. Если были траты — просто напишите, "
                                        "например <code>30000 обед</code>. Если нет — отлично 👍",
                                 parse_mode="HTML", reply_markup=MENU)
    else:
        period = "yesterday" if late else "today"
        bal = cards_text()
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        n = sum(1 for r in ledger.unexplained(start, 100) if r["ts"][:10] == key)
        why_line = f"\n\n❓ Без пояснения «на что»: {n} — /why" if n else ""
        await c.bot.send_message(owner, "🌙 " + report(period) + (f"\n\n{bal}" if bal else "") + why_line,
                                 parse_mode="HTML", reply_markup=report_kb(period, None))
    if day.weekday() == 6:
        await c.bot.send_message(owner, "📈 Итог недели\n" + report("week", ref=day), parse_mode="HTML",
                                 reply_markup=report_kb("week", None))
    if day.day == 1:
        await c.bot.send_message(owner, "📆 Итог месяца\n" + report("lastmonth", ref=day), parse_mode="HTML",
                                 reply_markup=report_kb("lastmonth", None))
        await c.bot.send_document(owner, ledger.export_csv("lastmonth").encode("utf-8-sig"),
                                  filename=f"rashody-{(day - dt.timedelta(days=1)):%Y-%m}.csv")


async def catch_up(c: ContextTypes.DEFAULT_TYPE):
    """После запуска: прислать итог, пропущенный пока бот был выключен. При самом первом запуске — не спамить."""
    if ledger.meta_get("last_nightly") is None:
        return ledger.meta_set("last_nightly", due_day(ledger.now()).strftime("%Y-%m-%d"))
    await nightly(c)


COMMANDS = [("today", "Итог за сегодня"), ("week", "Последние 7 дней"), ("month", "Этот месяц"),
            ("dash", "Дашборд с графиками"), ("cards", "Карты и остатки"), ("why", "Расходы без пояснения"),("find", "Поиск: /find корзинка"), ("limit", "Лимит: /limit кафе 800к"),
            ("limits", "Все лимиты"), ("undo", "Удалить последнюю запись"), ("export", "Скачать CSV для Excel"),
            ("apple", "Настроить Apple Pay"), ("sms", "SMS банка, Alliance Pay, Payme"),("status", "Адреса сервера"), ("help", "Как пользоваться")]


async def post_init(app: Application):
    try:  # меню команд по кнопке «/» в Telegram
        await app.bot.set_my_commands([BotCommand(n, d) for n, d in COMMANDS])
    except Exception as e:  # noqa: BLE001 — нет интернета при старте не должно мешать запуску
        log.warning("меню команд не обновлено: %s", e)


async def trace(u: Update, c):
    """Короткая строка в логе на каждое входящее сообщение (без текста — только тип)."""
    m = u.effective_message
    if m:
        kind = next((k for k in ("photo", "document", "text", "caption", "sticker", "voice", "video") if getattr(m, k, None)),
                    "other")
        mime = f" {m.document.mime_type}" if m.document else ""
        log.info("входящее: %s%s от %s", kind, mime, u.effective_user.id if u.effective_user else "?")


async def on_error(update, c: ContextTypes.DEFAULT_TYPE):
    """Пропал интернет — короткая строка в логе вместо простыни; остальное — с трассировкой."""
    from telegram.error import NetworkError
    if isinstance(c.error, NetworkError):
        return log.warning("нет связи с Telegram: %s", c.error)
    log.error("ошибка: %s", c.error, exc_info=c.error)


def main():
    if not core.BOT_TOKEN or "BOTFATHER" in core.BOT_TOKEN:
        sys.exit("Не задан bot_token: запустите expenses\\setup_expenses.bat или впишите токен в expenses\\config.json")
    ledger.init()
    core.secret()
    web.start()
    # таймауты больше стандартных 5 с: на медленном интернете ответы и фото иначе обрываются
    app = (Application.builder().token(core.BOT_TOKEN).connect_timeout(20).read_timeout(30).write_timeout(30)
           .media_write_timeout(60).pool_timeout(10).post_init(post_init).build())
    for name, fn in [("start", start), ("help", help_cmd), ("today", today), ("week", week), ("month", month),
                     ("dash", dash), ("status", status), ("undo", undo), ("export", export), ("find", find),
                     ("limit", limit), ("limits", limits), ("apple", apple), ("sms", sms), ("cards", cards_cmd), ("why", why)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler(filters.ALL, trace), group=-1)
    app.add_handler(CallbackQueryHandler(cb))
    app.add_error_handler(on_error)
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, photo))
    app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, text))
    t = daily_time()
    # misfire_grace_time: ПК спал в 22:00 — итог всё равно придёт, когда проснётся (в пределах 12 ч)
    app.job_queue.run_daily(nightly, dt.time(t.hour, t.minute, tzinfo=TZ), name="nightly",
                            job_kwargs={"misfire_grace_time": 12 * 3600, "coalesce": True})
    app.job_queue.run_once(catch_up, 30, name="catch_up")
    n = core.resume_receipts()
    log.info("Бот расходов запущен. Дашборд: %s%s", core.base_url(), f" · чеков в очереди: {n}" if n else "")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("бот упал")  # start_expenses.bat перезапустит через 10 секунд
        raise
