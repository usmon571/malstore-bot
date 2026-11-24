"""Дашборд картинкой для Telegram (Pillow): работает без Tailscale и без браузера."""
from __future__ import annotations

import io
import os
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import ledger
from parsing import fmt

W = 1080
PAD = 44
BG, CARD, LINE = (15, 17, 21), (24, 27, 34), (38, 42, 51)
TX, MUT = (232, 234, 240), (139, 147, 163)
RED, GRN, ACC, AMB = (248, 113, 113), (74, 222, 128), (96, 165, 250), (251, 191, 36)
COL = [(37, 99, 235), (22, 163, 74), (217, 119, 6), (220, 38, 38), (124, 58, 237), (8, 145, 178),
       (219, 39, 119), (101, 163, 13), (234, 88, 12), (79, 70, 229), (148, 163, 184)]

TITLES = {"today": "Сегодня", "yesterday": "Вчера", "week": "Последние 7 дней", "month": "Этот месяц",
          "lastmonth": "Прошлый месяц", "all": "Всё время"}
WALLETS = {None: "Личное + бизнес", "personal": "Личное", "business": "Бизнес"}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]

_FONT_DIRS = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts", Path("/usr/share/fonts/truetype/dejavu"),
              Path("/Library/Fonts"), Path("/System/Library/Fonts/Supplemental")]
_REGULAR = ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Arial.ttf")
_BOLD = ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf")
_cache: dict = {}


def font(size: int, bold: bool = False):
    key = (size, bold)
    if key not in _cache:
        for name in (_BOLD if bold else ()) + _REGULAR:
            for d in _FONT_DIRS:
                if (d / name).exists():
                    _cache[key] = ImageFont.truetype(str(d / name), size)
                    break
            if key in _cache:
                break
        else:
            _cache[key] = ImageFont.load_default(size)
    return _cache[key]


def _short(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + " млн"
    if n >= 1000:
        return f"{round(n / 1000):g}к"
    return str(n)


def _fit(d: ImageDraw.ImageDraw, text: str, f, width: int) -> str:
    if d.textlength(text, font=f) <= width:
        return text
    while text and d.textlength(text + "…", font=f) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _ops(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "операция"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "операции"
    return "операций"


def _days_passed(data: dict) -> int:
    """Сколько дней периода уже прошло (для «в день»): у текущего месяца — до сегодня включительно."""
    s = datetime.strptime(data["since"], "%Y-%m-%d")
    e = min(datetime.strptime(data["until"], "%Y-%m-%d"), ledger.now() + timedelta(days=1))
    if data["period"] == "all" and data["by_day"]:
        s = datetime.strptime(data["by_day"][0]["d"], "%Y-%m-%d")
    return max(1, (e.replace(hour=0, minute=0, second=0, microsecond=0) - s).days)


def _range_label(data: dict) -> str:
    s = datetime.strptime(data["since"], "%Y-%m-%d")
    e = datetime.strptime(data["until"], "%Y-%m-%d") - timedelta(days=1)
    if data["period"] == "all":
        return "за всё время"
    if s == e:
        return f"{s.day} {MONTHS[s.month - 1]}"
    return f"{s.day} {MONTHS[s.month - 1]} — {e.day} {MONTHS[e.month - 1]}"


class Canvas:
    """Высота заранее неизвестна: рисуем на высоком холсте и обрезаем в конце."""

    def __init__(self):
        self.img = Image.new("RGB", (W, 5000), BG)
        self.d = ImageDraw.Draw(self.img)
        self.y = PAD

    def card(self, height: int, title: str | None = None) -> tuple[int, int]:
        """Рисует карточку, возвращает (y верхнего края содержимого, y нижнего края)."""
        top = self.y
        self.d.rounded_rectangle((PAD, top, W - PAD, top + height), radius=28, fill=CARD, outline=LINE, width=2)
        inner = top + 28
        if title:
            self.d.text((PAD + 32, inner), title, font=font(30), fill=MUT)
            inner += 52
        self.y = top + height + 24
        return inner, top + height

    def png(self) -> bytes:
        out = io.BytesIO()
        self.img.crop((0, 0, W, self.y + PAD - 24)).save(out, "PNG", optimize=True)
        return out.getvalue()


def _donut(cats: list, size: int) -> Image.Image:
    """Кольцо категорий: рисуем в 4 раза крупнее и уменьшаем — так края гладкие."""
    k = 4
    big = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    total = sum(c["s"] for c in cats) or 1
    start = -90.0
    for i, c in enumerate(cats):
        sweep = c["s"] / total * 360
        d.pieslice((0, 0, size * k - 1, size * k - 1), start, start + sweep, fill=COL[i % len(COL)])
        start += sweep
    hole = int(size * k * 0.30)
    d.ellipse((hole, hole, size * k - hole, size * k - hole), fill=CARD)
    return big.resize((size, size), Image.LANCZOS)


def render(period: str = "month", wallet: str | None = None, ref: datetime | None = None) -> bytes:
    data = ledger.summary(period, wallet, ref)
    c = Canvas()
    d = c.d
    x0, x1 = PAD + 32, W - PAD - 32

    # заголовок
    d.text((PAD, c.y), TITLES.get(period, f"{period} дней"), font=font(52, True), fill=TX)
    c.y += 70
    d.text((PAD, c.y), f"{WALLETS.get(wallet, '')} · {_range_label(data)} · сумы", font=font(30), fill=MUT)
    c.y += 64

    # главные цифры
    top, _ = c.card(230)
    col = (x1 - x0) // 3
    net = data["income"] - data["expense"]
    ws = data.get("wallets") or {}
    kpis = [("Потрачено", fmt(data["expense"]), RED, f"{data['count']} {_ops(data['count'])}"),
            ("Доход", fmt(data["income"]), GRN, ("итог +" if net >= 0 else "итог −") + fmt(abs(net)))]
    if wallet:
        kpis.append(("В день", fmt(data["expense"] // _days_passed(data)), TX, "в среднем"))
    else:
        kpis.append(("Личное / Бизнес", f"{_short(ws.get('personal', {}).get('exp', 0))} / "
                     f"{_short(ws.get('business', {}).get('exp', 0))}", TX, "расходы"))
    for i, (label, value, color, sub) in enumerate(kpis):
        x = x0 + i * col
        d.text((x, top), label, font=font(28), fill=MUT)
        d.text((x, top + 42), _fit(d, value, font(54, True), col - 16), font=font(54, True), fill=color)
        d.text((x, top + 120), sub, font=font(26), fill=MUT)

    if not data["count"] and not data["income"]:
        inner, _ = c.card(150)
        d.text((x0, inner + 20), "За этот период записей нет", font=font(34), fill=MUT)
        return c.png()

    # расходы по дням
    days = data["by_day"]
    if len(days) > 1:
        inner, bottom = c.card(360, "Расходы по дням")
        s = datetime.strptime(data["since"], "%Y-%m-%d")
        e = min(datetime.strptime(data["until"], "%Y-%m-%d"), ledger.now() + timedelta(days=1))
        if period == "all":
            s = datetime.strptime(days[0]["d"], "%Y-%m-%d")
        by = {r["d"]: r["s"] for r in days}
        span = [(s + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(max(1, (e - s).days))][-62:]
        mx = max(by.values()) or 1
        h = bottom - inner - 60
        step = (x1 - x0) / max(len(span), 7)
        for i, day in enumerate(span):
            v = by.get(day, 0)
            bh = max(4, int(v / mx * h)) if v else 2
            bx = x0 + i * step + step * 0.14
            d.rounded_rectangle((bx, inner + h - bh, bx + max(3, step * 0.72), inner + h), radius=4,
                                fill=ACC if v else LINE)
        peak = max(days, key=lambda r: r["s"])
        pd = datetime.strptime(peak["d"], "%Y-%m-%d")
        d.text((x0, inner + h + 16), f"{span[0][8:10]}.{span[0][5:7]} — {span[-1][8:10]}.{span[-1][5:7]}",
               font=font(26), fill=MUT)
        right = f"макс {fmt(peak['s'])} · {pd.day} {MONTHS[pd.month - 1]}"
        d.text((x1 - d.textlength(right, font=font(26)), inner + h + 16), right, font=font(26), fill=MUT)

    # категории
    cats = data["by_category"][:9]
    rest = sum(r["s"] for r in data["by_category"][9:])
    if rest:
        cats = cats + [{"category": "Остальное", "s": rest, "n": 0}]
    if cats:
        size = 300
        rows_h = len(cats) * 52
        inner, _ = c.card(max(size, rows_h) + 100, "Куда уходят деньги")
        ring = _donut(cats, size)
        c.img.paste(ring, (x0, inner + 8), ring)
        total = sum(r["s"] for r in cats) or 1
        lx = x0 + size + 44
        amount_w = max(d.textlength(fmt(r["s"]), font=font(30, True)) for r in cats)
        pct_right = x1 - amount_w - 24                       # проценты — ровной колонкой
        ly = inner + 8 + max(0, (size - rows_h) // 2)        # короткую легенду — по центру кольца
        for i, r in enumerate(cats):
            y = ly + i * 52
            d.rounded_rectangle((lx, y + 10, lx + 22, y + 32), radius=6, fill=COL[i % len(COL)])
            amount = fmt(r["s"])
            pct = f"{r['s'] * 100 / total:.0f}%"
            d.text((x1 - d.textlength(amount, font=font(30, True)), y), amount, font=font(30, True), fill=TX)
            d.text((pct_right - d.textlength(pct, font=font(28)), y + 2), pct, font=font(28), fill=MUT)
            name_w = pct_right - 70 - (lx + 38)
            d.text((lx + 38, y), _fit(d, r["category"], font(30), name_w), font=font(30), fill=TX)

    # магазины
    top_m = data["top_merchants"][:6]
    if top_m:
        inner, _ = c.card(len(top_m) * 54 + 100, "Топ магазинов")
        for i, r in enumerate(top_m):
            y = inner + i * 54
            amount = fmt(r["s"])
            aw = d.textlength(amount, font=font(30, True))
            d.text((x1 - aw, y), amount, font=font(30, True), fill=TX)
            name = _fit(d, r["merchant"], font(30), x1 - aw - x0 - 120)
            d.text((x0, y), name, font=font(30), fill=TX)
            d.text((x0 + d.textlength(name, font=font(30)) + 12, y + 2), f"×{r['n']}", font=font(26), fill=MUT)

    # лимиты (за текущий месяц)
    buds = data["budgets"]
    if buds:
        inner, _ = c.card(len(buds) * 86 + 100, "Лимиты на месяц")
        for i, b in enumerate(buds):
            y = inner + i * 86
            p = min(1.0, b["spent"] / b["limit"]) if b["limit"] else 0
            color = RED if p >= 1 else AMB if p >= 0.8 else ACC
            d.text((x0, y), b["category"], font=font(30), fill=TX)
            t = f"{fmt(b['spent'])} / {fmt(b['limit'])}"
            d.text((x1 - d.textlength(t, font=font(28)), y + 2), t, font=font(28), fill=MUT)
            d.rounded_rectangle((x0, y + 48, x1, y + 62), radius=7, fill=LINE)
            if p:
                d.rounded_rectangle((x0, y + 48, x0 + max(14, int((x1 - x0) * p)), y + 62), radius=7, fill=color)

    # карты
    cards = [r for r in ledger.cards() if r["balance"] is not None]
    if cards:
        inner, _ = c.card(len(cards) * 54 + (54 if len(cards) > 1 else 0) + 100, "Карты · остаток из последней SMS")
        for i, r in enumerate(cards):
            y = inner + i * 54
            d.text((x0, y), f"{r['brand'] or 'Карта'} *{r['last4']}", font=font(30), fill=TX)
            amount = fmt(r["balance"])
            d.text((x1 - d.textlength(amount, font=font(30, True)), y), amount, font=font(30, True), fill=TX)
        if len(cards) > 1:
            y = inner + len(cards) * 54
            total = fmt(sum(r["balance"] for r in cards))
            d.text((x0, y), "Всего", font=font(30, True), fill=MUT)
            d.text((x1 - d.textlength(total, font=font(30, True)), y), total, font=font(30, True), fill=TX)

    now = ledger.now()
    d.text((PAD, c.y), f"обновлено {now:%d.%m %H:%M}", font=font(24), fill=MUT)
    c.y += 30
    return c.png()
