"""Бот учёта расходов (личное + бизнес). Запуск: venv\\Scripts\\python.exe expenses\\main.py"""
from __future__ import annotations

import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update  # noqa: E402
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,  # noqa: E402
                          MessageHandler, filters)

import core  # noqa: E402
import ledger  # noqa: E402
import parsing  # noqa: E402
import web  # noqa: E402
from parsing import BUSINESS, PERSONAL, WALLET_LABEL, fmt  # noqa: E402

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
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
    "• Перешлите SMS/уведомление Humo, Uzcard или банка — разберу сам\n"
    "• Apple Pay — один раз настройте /apple, дальше всё автоматически\n\n"
    "<b>Исправить</b>: кнопки под записью, или ответьте (reply) на запись новой суммой.\n\n"
    "<b>Команды</b>\n/today /week /month — отчёты\n/dash — дашборд с графиками\n"
    "/find корзинка — поиск\n/limit еда 2000000 — лимит в месяц (0 — снять), /limits\n"
    "/undo — удалить последнюю · /export — Excel/CSV\n/apple — Apple Pay · /status — адреса сервера"
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
        ledger.delete(i)
        await u.message.reply_text(f"↩️ Удалено: {core.describe(row)}", reply_markup=MENU)
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

    # ответ на запись бота новой суммой → исправить сумму
    rep = msg.reply_to_message
    if rep and rep.from_user and rep.from_user.is_bot:
        tx_id = ledger.meta_get(f"msg:{rep.message_id}")
        p = parsing.parse_manual(t)
        if tx_id and p and ledger.get(int(tx_id)):
            ledger.set_amount(int(tx_id), p.amount)
            row = ledger.get(int(tx_id))
            return await msg.reply_text("✏️ Исправлено: " + core.describe(row), reply_markup=tx_kb(row))

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
    sent = await msg.reply_text("✅ " + core.describe(row) + core.budget_warning(row["category"]), reply_markup=tx_kb(row))
    ledger.meta_set(f"msg:{sent.message_id}", str(row["id"]))


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
    row = ledger.get(tx_id)
    if not row:
        await q.edit_message_text("Запись уже удалена")
        return await q.answer()
    if act == "del":
        ledger.delete(tx_id)
        await q.edit_message_text(f"🗑 Удалено: {core.describe(row)}")
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
    elif act == "wal":
        ledger.set_wallet(tx_id, rest.split(":")[1])
        row = ledger.get(tx_id)
        await q.edit_message_text("✅ " + core.describe(row), reply_markup=tx_kb(row))
    await q.answer()


# ---------- автосводки ----------

async def nightly(c: ContextTypes.DEFAULT_TYPE):
    """22:00: итог дня (или напоминание), по воскресеньям — неделя, 1-го числа — прошлый месяц + CSV. Плюс бэкап."""
    owner = core.owner_id()
    try:
        ledger.backup()
    except Exception as e:
        log.warning("бэкап не удался: %s", e)
    if not owner:
        return
    now = ledger.now()
    if ledger.count_on(now) == 0:
        await c.bot.send_message(owner, "📝 Сегодня ничего не записано. Если были траты — просто напишите, "
                                        "например <code>30000 обед</code>. Если нет — отлично 👍",
                                 parse_mode="HTML", reply_markup=MENU)
    else:
        await c.bot.send_message(owner, "🌙 " + report("today"), parse_mode="HTML", reply_markup=report_kb("today", None))
    if now.weekday() == 6:
        await c.bot.send_message(owner, "📈 Итог недели\n" + report("week"), parse_mode="HTML",
                                 reply_markup=report_kb("week", None))
    if now.day == 1:
        await c.bot.send_message(owner, "📆 Итог месяца\n" + report("lastmonth"), parse_mode="HTML",
                                 reply_markup=report_kb("lastmonth", None))
        await c.bot.send_document(owner, ledger.export_csv("lastmonth").encode("utf-8-sig"),
                                  filename=f"rashody-{(now - dt.timedelta(days=1)):%Y-%m}.csv")


def main():
    if not core.BOT_TOKEN or "BOTFATHER" in core.BOT_TOKEN:
        sys.exit("Не задан bot_token: запустите expenses\\setup_expenses.bat или впишите токен в expenses\\config.json")
    ledger.init()
    core.secret()
    web.start()
    app = Application.builder().token(core.BOT_TOKEN).build()
    for name, fn in [("start", start), ("help", help_cmd), ("today", today), ("week", week), ("month", month),
                     ("dash", dash), ("status", status), ("undo", undo), ("export", export), ("find", find),
                     ("limit", limit), ("limits", limits), ("apple", apple)]:
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, text))
    hh, mm = (int(x) for x in core.DAILY_AT.split(":"))
    app.job_queue.run_daily(nightly, dt.time(hh, mm, tzinfo=TZ), name="nightly")
    log.info("Бот расходов запущен. Дашборд: %s", core.base_url())
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
