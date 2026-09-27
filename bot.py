"""
MalStore Original bot.

Ish jarayoni:
  1. Ruxsat berilgan foydalanuvchi Poizon (Dewu) linkini yuboradi.
  2. Bot sahifani ochib, rasmlar va xarakteristikalarni oladi.
  3. Bot narxni so'raydi.
  4. Tayyor post (rasmlar + xarakteristika + narx + "Original" belgisi)
     tasdiq uchun ko'rsatiladi.
  5. Tasdiqlansa — sozlangan kanalga yuboriladi.
"""
from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
    WebAppInfo,
)
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import settings
import store
from scraper import extract_poizon_url, fetch_product

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# httpx har bir so'rov manzilini yozadi, manzil ichida esa bot tokeni bor —
# ya'ni token log fayliga va ekranga tushadi. Shuning uchun uni jimlatamiz.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("malstore-bot")

ALLOWED = {u.lstrip("@").lower() for u in settings.ALLOWED_USERNAMES}
CHANNEL = settings.CHANNEL
BRAND_LABEL = settings.BRAND_LABEL
MAX_IMAGES = settings.MAX_IMAGES
# Telegram Mini App manzili (ochiq HTTPS bo'lishi shart). Bo'sh bo'lsa —
# narxlar oddiy xabar orqali so'raladi.
WEBAPP_URL = settings.WEBAPP_URL
TG_ALBUM_LIMIT = 10  # Telegram sendMediaGroup bir xabarda ko'pi bilan 10 ta qabul qiladi

(
    WAITING_CNY_PRICES,
    WAITING_DELIVERY,
    WAITING_MARKUP,
    WAITING_CONFIRM,
    WAITING_EDIT,
    ORDER_CONTACT,
    ORDER_RECEIPT,
    PRODUCT_EDIT,
) = range(8)

# Xaridor buyurtmani tashlab ketsa, shuncha daqiqadan keyin holat tozalanadi
ORDER_TIMEOUT_MINUTES = settings.ORDER_TIMEOUT_MINUTES

# Oldindan to'lov ulushi va karta ma'lumotlari
PREPAY_SHARE = settings.PREPAY_SHARE
CARD_NUMBER = settings.CARD_NUMBER
CARD_HOLDER = settings.CARD_HOLDER

# Bot username — deep link uchun kerak, ishga tushganda aniqlanadi
BOT_USERNAME = ""


def order_link(product_id: str) -> str:
    return f"https://t.me/{BOT_USERNAME}?start={product_id}"


def with_order_footer(caption: str, product_id: str) -> str:
    """Post oxiriga tovar ID'si va buyurtma havolasini qo'shadi."""
    return (
        f"{caption}\n\n"
        f"🆔 Tovar raqami: <b>{product_id}</b>\n"
        f"🛒 <a href=\"{order_link(product_id)}\">Buyurtma berish</a>"
    )

# "50=570", "100ml : 770", "150-970"
VARIANT_PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:ml)?\s*[=:\-–]\s*(\d+(?:\.\d+)?)", re.I)


def detect_variants(*titles: str) -> list[float]:
    """Sarlavhadagi hajmlar: "50ml/100ml/150ml" → [50.0, 100.0, 150.0]"""
    for title in titles:
        if not title:
            continue
        sizes = sorted({float(v) for v in ML_RE.findall(title)})
        if sizes:
            return sizes
    return []

ML_RE = re.compile(r"(\d+(?:\.\d+)?)\s*ml", re.I)
GRAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:g|gr|gram|克)\b", re.I)
KG_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kg|kilo|公斤|千克)\b", re.I)
# Shisha + quti + himoya upakovkasi uchun qo'shimcha (gramm)
PACKAGING_OVERHEAD_G = 250

# MKExpress tariflari (mkexpress.uz, 26.09.2026). Narx o'zgarsa —
# config.json ichida "delivery_rates" orqali yangilash mumkin.
# Minimal vazn yo'q: 0.4 kg yuk uchun 0.4 kg to'lanadi.
AVTO_KARGO_RATE = settings.AVTO_KARGO_RATE
AVIA_BREND_RATE = settings.AVIA_BREND_RATE

# Atir/suyuqlik aviada qabul qilinmaydi — shuning uchun bunday mahsulotlar
# uchun faqat avto yo'nalishi taklif qilinadi.
LIQUID_HINTS = (
    "香水", "香精", "喷雾", "花露水", "化妆水",
    "eau de", "edt", "edp", "parfum", "perfume", "cologne", "atir",
)


def is_liquid_product(product) -> bool:
    text = f"{product.title_zh} {product.title}".lower()
    return any(hint.lower() in text for hint in LIQUID_HINTS)


MKEXPRESS_URL = "https://mkexpress.uz/en"
# Reys kodining boshi yo'nalishni bildiradi
FLIGHT_PREFIXES = {"KM": "Aviapochta", "AK": "Avto Kargo", "AP": "Avto Pochta"}
# Mahsulot Xitoy omboriga yetib borishi uchun kerakli kunlar
WAREHOUSE_BUFFER_DAYS = 3
# Qadoqlash tugagach yo'ldagi kunlar (mkexpress.uz tariflaridan)
TRANSIT_DAYS = {"Aviapochta": (2, 7), "Avto Kargo": (10, 20), "Avto Pochta": (15, 25)}

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_flight_date(text: str, today: date) -> date | None:
    """"2 Oct" → date. Saytda yil yozilmaydi, shuning uchun uni o'zimiz topamiz."""
    match = re.match(r"(\d{1,2})\s+([A-Za-z]+)", text.strip())
    if not match:
        return None
    month = MONTHS.get(match.group(2)[:3].lower())
    if not month:
        return None
    day = int(match.group(1))
    try:
        parsed = date(today.year, month, day)
    except ValueError:
        return None
    # Yil almashgan bo'lsa (masalan dekabr → yanvar) keyingi yilga o'tkazamiz
    if parsed < today - timedelta(days=60):
        parsed = date(today.year + 1, month, day)
    return parsed


def fetch_flights() -> list[dict]:
    """mkexpress.uz dan reyslar jadvali: kod, yo'nalish, oxirgi qadoqlash kuni."""
    today = date.today()
    response = requests.get(
        MKEXPRESS_URL, timeout=15,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0"},
    )
    section = BeautifulSoup(response.text, "lxml").select_one("#reyslar")
    if not section:
        return []

    flights = []
    for article in section.select("article"):
        code_el = article.select_one("span")
        if not code_el:
            continue
        code = code_el.get_text(strip=True)
        route = FLIGHT_PREFIXES.get(code[:2].upper())
        paragraphs = [p.get_text(" ", strip=True) for p in article.select("p")]
        # ["27 Sept", "packing starts", "28 Sept", "last packing day", ...]
        last_packing = None
        for i, text in enumerate(paragraphs):
            if "last packing" in text.lower() and i > 0:
                last_packing = _parse_flight_date(paragraphs[i - 1], today)
        if route and last_packing:
            flights.append({"code": code, "route": route, "last_packing": last_packing})
    return flights


def pick_flight(route: str) -> tuple[dict | None, str]:
    """(reys, holat). Holat: "ok" | "ulgurmaydi" | "xato"."""
    try:
        flights = fetch_flights()
    except Exception as e:
        log.warning("reyslar jadvalini olib bo'lmadi: %s", e)
        return None, "xato"

    deadline = date.today() + timedelta(days=WAREHOUSE_BUFFER_DAYS)
    candidates = [f for f in flights if f["route"] == route and f["last_packing"] >= deadline]
    if not candidates:
        return None, "ulgurmaydi"

    flight = min(candidates, key=lambda f: f["last_packing"])
    low, high = TRANSIT_DAYS.get(route, (10, 20))
    flight["arrival_from"] = flight["last_packing"] + timedelta(days=low)
    flight["arrival_to"] = flight["last_packing"] + timedelta(days=high)
    return flight, "ok"


def estimate_weight(title: str, specs: dict[str, str]) -> tuple[float | None, str]:
    """Upakovka bilan taxminiy vazn (kg) va uning izohi.

    Google qidiruvi ishonchsiz (natijalar kod ichidan soxta raqam beradi),
    shuning uchun avval sahifadagi ochiq vazn, keyin hajmdan (ml) taxmin.
    """
    for key, value in specs.items():
        text = f"{key} {value}"
        kg_match = KG_RE.search(text)
        if kg_match:
            return float(kg_match.group(1)), f"sahifadagi ma'lumot ({value})"
        gram_match = GRAM_RE.search(text)
        if gram_match:
            return float(gram_match.group(1)) / 1000, f"sahifadagi ma'lumot ({value})"

    volumes = sorted({float(v) for v in ML_RE.findall(title)})
    if volumes:
        biggest = volumes[-1]
        kg = (biggest + PACKAGING_OVERHEAD_G) / 1000
        if len(volumes) > 1:
            sizes = ", ".join(f"{v:g}ml→{(v + PACKAGING_OVERHEAD_G) / 1000:.2f}kg" for v in volumes)
            return kg, f"hajmdan taxmin ({sizes}); eng kattasi olindi"
        return kg, f"{biggest:g}ml hajmdan taxmin"

    return None, "aniqlanmadi"

CBU_CNY_URL = "https://cbu.uz/uz/arkhiv-kursov-valyut/json/CNY/"
# CBU'ga ulanib bo'lmasa shu kurs ishlatiladi (config.json orqali o'zgartiriladi)
FALLBACK_CNY_RATE = settings.FALLBACK_CNY_RATE


def fetch_cny_rate() -> tuple[float, str]:
    """(kurs, sana) — O'zbekiston Markaziy bankidan CNY→UZS kursi."""
    try:
        data = requests.get(CBU_CNY_URL, timeout=10).json()
        return float(data[0]["Rate"]), data[0]["Date"]
    except Exception as e:
        log.warning("CBU kursini olib bo'lmadi: %s", e)
        return FALLBACK_CNY_RATE, "zaxira kurs"


def parse_amount(text: str) -> float | None:
    """Matndan sonni ajratadi: "¥770", "300 000", "1,250.5" → float."""
    cleaned = text.replace(" ", "").replace(",", "").replace(" ", "")
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    return float(match.group(0)) if match else None


def format_uzs(amount: float) -> str:
    """So'mni eng yaqin 1000 ga yaxlitlab, chiroyli ko'rinishda qaytaradi."""
    rounded = int(round(amount / 1000.0)) * 1000
    return f"{rounded:,}".replace(",", " ") + " so'm"


def apply_markup(base_uzs: float, markup_text: str) -> float | None:
    """Ustamani qo'llaydi: "20%" → foiz, "300000" → so'mda qo'shimcha."""
    text = markup_text.strip().replace(" ", "").replace(",", "")
    if text.endswith("%"):
        percent = parse_amount(text[:-1])
        return None if percent is None else base_uzs * (1 + percent / 100)
    added = parse_amount(text)
    return None if added is None else base_uzs + added


def is_allowed(update: Update) -> bool:
    user = update.effective_user
    if not user or not user.username:
        return False
    if user.username.lower() in ALLOWED:
        # Buyurtma xabarlarini yuborish uchun ID'sini eslab qolamiz
        store.remember_admin(user.id)
        return True
    return False


async def deny(update: Update) -> None:
    await update.effective_message.reply_text(
        "Kechirasiz, sizda bu botdan foydalanish huquqi yo'q."
    )


SPEC_EMOJI = {
    "Xushbo'y hid turi": "🌳",
    "Eng sezilarli hid": "👃",
    "Yuqori notalar": "🔝",
    "O'rta notalar": "💫",
    "Asosiy notalar": "🌙",
    "Versiya": "🔖",
    "Model": "🏷",
    "Chiqarilgan sana": "📅",
    "Hajmi": "🧴",
    "O'lchami": "📏",
    "Rangi": "🎨",
    "Materiali": "🧵",
    "Brend": "™️",
    "Asosiy rang": "🎨",
    "Qo'shimcha rang": "🖌",
    "Xususiyatlari": "⚡",
    "Taglik materiali": "👟",
    "Burun shakli": "👞",
    "Poshna turi": "📐",
    "Mavsum": "🍃",
    "Kimga mo'ljallangan": "👤",
    "Bog'lash usuli": "🔗",
}
DIVIDER = "➖➖➖➖➖➖➖➖➖➖"
# Telegram rasm ostidagi matn uchun chegara
TG_CAPTION_LIMIT = 1024


def build_caption(
    title: str, specs: dict[str, str], prices: dict[str, str], arrival: str = ""
) -> str:
    def blocks(spec_items: list[tuple[str, str]]) -> str:
        lines = []
        if title:
            lines.append(f"✨ <b>{html.escape(title)}</b>")
            lines.append("")
        lines.append(DIVIDER)
        lines.append(f"💎 <b>100% {html.escape(BRAND_LABEL.upper())}</b>")
        lines.append("🔍 Poizon (得物) rasmiy tekshiruvidan o'tgan")
        lines.append("📦 Original quti va hujjatlari bilan")
        lines.append(DIVIDER)
        if spec_items:
            lines.append("")
            lines.append("📋 <b>Xususiyatlari:</b>")
            for key, value in spec_items:
                emoji = SPEC_EMOJI.get(key, "▪️")
                lines.append(f"{emoji} <b>{html.escape(key)}:</b> {html.escape(value)}")
        lines.append("")
        if len(prices) == 1:
            only_price = next(iter(prices.values()))
            lines.append(f"💰 <b>Narxi:</b> {html.escape(only_price)}")
        else:
            lines.append("💰 <b>Narxlari:</b>")
            for label, value in prices.items():
                lines.append(f"🧴 <b>{html.escape(label)}</b> — {html.escape(value)}")
        if arrival:
            lines.append(f"🚚 <b>Yetkazib berish:</b> {html.escape(arrival)}")
        lines.append("")
        lines.append(html.escape(CHANNEL))
        return "\n".join(lines)

    # Chegaraga sig'masa, xususiyatlarni oxiridan boshlab kamaytiramiz
    items = list(specs.items())
    while True:
        caption = blocks(items)
        if len(caption) <= TG_CAPTION_LIMIT or not items:
            return caption
        items.pop()


def build_media_chunks(images: list[bytes], caption: str) -> list[list[InputMediaPhoto]]:
    """Rasmlarni Telegram albom limiti (10 ta)ga bo'lib, birinchi bo'lakning
    birinchi rasmiga caption qo'shib qaytaradi."""
    chunks = [images[i : i + TG_ALBUM_LIMIT] for i in range(0, len(images), TG_ALBUM_LIMIT)]
    result = []
    for i, chunk in enumerate(chunks):
        media = [
            InputMediaPhoto(
                b,
                caption=caption if i == 0 and j == 0 else None,
                parse_mode="HTML" if i == 0 and j == 0 else None,
            )
            for j, b in enumerate(chunk)
        ]
        result.append(media)
    return result


async def send_product_post(
    send_photo, send_media_group, send_text, images: list[bytes], caption: str
) -> list:
    """images soniga qarab: 0 -> matn, 1 -> bitta rasm, 2+ -> albom(lar).

    Yuborilgan xabarlarni qaytaradi — keyin tovarni tahrirlash/o'chirish
    uchun ularning raqamlari kerak bo'ladi.
    """
    sent: list = []
    if not images:
        sent.append(await send_text(caption))
        return sent
    if len(images) == 1:
        sent.append(await send_photo(images[0], caption))
        return sent
    for media in build_media_chunks(images, caption):
        result = await send_media_group(media)
        sent.extend(result if isinstance(result, (list, tuple)) else [result])
    return sent


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/start — xaridor uchun buyurtma, admin uchun tovar joylash."""
    # Kanaldagi havola bosilganda: /start 101
    if context.args:
        return await show_product_for_order(update, context, context.args[0])

    if is_allowed(update):
        await update.message.reply_text(
            "Salom! Poizon (Dewu) mahsulot linkini yuboring — men rasmlar va "
            "xarakteristikalarini olib, narxni so'rayman."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"Assalomu alaykum! 👋\n\n"
        f"Buyurtma berish uchun {CHANNEL} kanalimizdan tovarni tanlang va "
        "post ostidagi «🛒 Buyurtma berish» havolasini bosing.\n\n"
        "Yoki tovar raqamini yozing (masalan: 101)"
    )
    return ConversationHandler.END


async def show_product_for_order(
    update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: str
) -> int:
    product = store.get_product(product_id)
    if not product:
        await update.message.reply_text(
            f"❌ {product_id} raqamli tovar topilmadi.\n"
            f"Raqamni tekshiring yoki {CHANNEL} kanalidan tanlang."
        )
        return ConversationHandler.END

    context.user_data["order_product"] = product

    lines = [f"🛍 <b>{html.escape(product['title'])}</b>", ""]
    if product.get("arrival"):
        lines.append(f"🚚 Yetkazib berish: {html.escape(product['arrival'])}")
    lines.append(f"🆔 Tovar raqami: <b>{product['id']}</b>")
    lines.append("")
    lines.append("Qaysi variantni olasiz?")

    prices = product.get("prices", {})
    buttons = [
        [InlineKeyboardButton(f"{label} — {price}", callback_data=f"buy:{i}")]
        for i, (label, price) in enumerate(prices.items())
    ]
    buttons.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="buy:cancel")])

    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return ORDER_CONTACT


async def handle_order_variant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "buy:cancel":
        await query.edit_message_text("Buyurtma bekor qilindi.")
        context.user_data.clear()
        return ConversationHandler.END

    product = context.user_data.get("order_product", {})
    labels = list(product.get("prices", {}).keys())
    index = int(query.data.split(":")[1])
    if index >= len(labels):
        await query.edit_message_text("Variant topilmadi. Qaytadan urinib ko'ring.")
        return ConversationHandler.END

    context.user_data["order_variant"] = labels[index]
    await query.edit_message_text(
        f"Tanlandi: <b>{html.escape(labels[index])}</b> — "
        f"{html.escape(product['prices'][labels[index]])}",
        parse_mode="HTML",
    )
    await query.message.reply_text(
        "📞 Telefon raqamingizni yuboring — operator siz bilan bog'lanadi.",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📱 Raqamni yuborish", request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True,
        ),
    )
    return ORDER_CONTACT


async def handle_order_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    phone = message.contact.phone_number if message.contact else (message.text or "").strip()
    if len(phone) < 7:
        await message.reply_text("Raqam to'g'ri ko'rinmayapti. Masalan: +998901234567")
        return ORDER_CONTACT

    product = context.user_data.get("order_product", {})
    variant = context.user_data.get("order_variant", "")
    if not product:
        await message.reply_text("Buyurtma ma'lumoti yo'qoldi. Qaytadan boshlang.")
        return ConversationHandler.END

    user = update.effective_user
    price_text = product.get("prices", {}).get(variant, "")
    price_value = parse_amount(price_text) or 0.0
    prepay_text = format_uzs(price_value * PREPAY_SHARE) if price_value else "—"

    order_no = store.add_order(
        product_id=product["id"],
        variant=variant,
        user={"id": user.id, "username": user.username, "name": user.full_name},
        phone=phone,
        price=price_text,
        prepay=prepay_text,
    )
    context.user_data["order_no"] = order_no
    context.user_data["prepay"] = prepay_text

    lines = [
        f"📋 Buyurtma <b>#{order_no}</b> rasmiylashtirildi",
        "",
        f"🛍 {html.escape(product['title'][:60])}",
        f"📦 {html.escape(variant)} — {html.escape(price_text)}",
        "",
        f"💳 <b>Oldindan to'lov ({PREPAY_SHARE:.0%}): {html.escape(prepay_text)}</b>",
        f"   Qolgani tovar kelganda to'lanadi.",
        "",
    ]
    if CARD_NUMBER:
        lines.append("Quyidagi kartaga o'tkazing:")
        lines.append(f"💳 <code>{html.escape(CARD_NUMBER)}</code>")
        if CARD_HOLDER:
            lines.append(f"👤 {html.escape(CARD_HOLDER)}")
        lines.append("")
        lines.append("To'lovdan so'ng <b>chek rasmini</b> shu yerga yuboring 📸")
    else:
        lines.append("⚠️ To'lov ma'lumotlari hali sozlanmagan — operator bog'lanadi.")

    await message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
    )
    return ORDER_RECEIPT if CARD_NUMBER else ConversationHandler.END


async def handle_order_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    if not (message.photo or message.document):
        await message.reply_text(
            "📸 Iltimos, to'lov chekining <b>rasmini</b> yuboring.\n"
            "Muammo bo'lsa /cancel yozing — operator bog'lanadi.",
            parse_mode="HTML",
        )
        return ORDER_RECEIPT

    order_no = context.user_data.get("order_no")
    product = context.user_data.get("order_product", {})
    variant = context.user_data.get("order_variant", "")
    file_id = message.photo[-1].file_id if message.photo else message.document.file_id
    order = store.mark_paid(order_no, file_id)

    await message.reply_text(
        f"✅ Chek qabul qilindi, rahmat!\n\n"
        f"📋 Buyurtma #{order_no}\n"
        "To'lov tekshirilgach siz bilan bog'lanamiz. 🙏",
    )

    user = update.effective_user
    caption = (
        f"🔔 <b>Yangi buyurtma #{order_no}</b> — to'lov cheki\n\n"
        f"🆔 Tovar: {product.get('id', '')} — {html.escape(product.get('title', '')[:55])}\n"
        f"📦 {html.escape(variant)} — {html.escape(order.get('price', '—') if order else '—')}\n"
        f"💳 Oldindan to'lov: {html.escape(context.user_data.get('prepay', '—'))}\n"
        f"📞 {html.escape(order.get('phone', '—') if order else '—')}\n"
        f"👤 {html.escape(user.full_name)}"
        + (f" (@{user.username})" if user.username else "")
        + f"\n🔗 {product.get('url', '')}"
    )
    keyboard = status_keyboard(order_no)
    for admin in store.admin_ids():
        try:
            if message.photo:
                await context.bot.send_photo(
                    chat_id=admin, photo=file_id, caption=caption,
                    parse_mode="HTML", reply_markup=keyboard,
                )
            else:
                await context.bot.send_document(
                    chat_id=admin, document=file_id, caption=caption,
                    parse_mode="HTML", reply_markup=keyboard,
                )
        except Exception as e:
            log.warning("adminga chek yuborilmadi (%s): %s", admin, e)

    context.user_data.clear()
    return ConversationHandler.END


# Buyurtma holatlari: kalit → (admin tugmasi, bazadagi holat, xaridorga xabar)
ORDER_STATUSES = {
    "tasdiq": (
        "✅ To'lov tasdiqlandi", "to'lov tasdiqlandi",
        "✅ To'lovingiz tasdiqlandi!\nTovar Poizon'dan buyurtma qilindi.",
    ),
    "yolda": (
        "🚚 Yo'lda", "yo'lda",
        "🚚 Buyurtmangiz yo'lga chiqdi!\nTaxminan 10–20 kun ichida yetib keladi.",
    ),
    "keldi": (
        "📦 Keldi", "keldi",
        "📦 Buyurtmangiz Toshkentga yetib keldi!\n"
        "Olib ketish uchun siz bilan bog'lanamiz.",
    ),
    "topshirildi": (
        "🎉 Topshirildi", "topshirildi",
        "🎉 Buyurtmangiz topshirildi. Xaridingiz muborak!\nYana kutib qolamiz 🙏",
    ),
    "bekor": (
        "❌ Bekor qilish", "bekor qilindi",
        "❌ Buyurtmangiz bekor qilindi.\nSavollar bo'lsa shu yerga yozing.",
    ),
}


def status_keyboard(order_no: int) -> InlineKeyboardMarkup:
    keys = list(ORDER_STATUSES)
    rows = [
        [
            InlineKeyboardButton(
                ORDER_STATUSES[key][0], callback_data=f"st:{order_no}:{key}"
            )
            for key in keys[i : i + 2]
        ]
        for i in range(0, len(keys), 2)
    ]
    return InlineKeyboardMarkup(rows)


def status_label(key: str) -> str:
    return ORDER_STATUSES[key][0]


async def handle_status_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin buyurtma holatini o'zgartirdi — xaridorga xabar yuboramiz."""
    query = update.callback_query
    _, order_no_text, key = query.data.split(":")
    order_no = int(order_no_text)
    label, status_text, buyer_text = ORDER_STATUSES[key]

    order = store.set_order_status(order_no, status_text)
    if not order:
        await query.answer("Buyurtma topilmadi", show_alert=True)
        return
    await query.answer(f"Holat: {label}")

    delivered = False
    if order.get("user_id"):
        try:
            await context.bot.send_message(
                chat_id=order["user_id"],
                text=f"📋 Buyurtma #{order_no}\n\n{buyer_text}",
            )
            delivered = True
        except Exception as e:
            log.warning("xaridorga xabar yetmadi (#%s): %s", order_no, e)

    note = "xaridorga xabar yuborildi" if delivered else "⚠️ xaridorga xabar yetmadi"
    try:
        await query.edit_message_reply_markup(reply_markup=status_keyboard(order_no))
        await query.message.reply_text(f"#{order_no} → {label} · {note}")
    except Exception:
        pass


async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update):
        await deny(update)
        return

    orders = store.recent_orders(15)
    if not orders:
        await update.message.reply_text("Hozircha buyurtma yo'q.")
        return

    lines = [f"📋 Oxirgi {len(orders)} ta buyurtma:\n"]
    for order in orders:
        product = store.get_product(order["product_id"]) or {}
        when = order["created"].replace("T", " ")[5:16]
        mark = "✅" if order.get("receipt") else "⏳"
        lines.append(
            f"{mark} <b>#{order['no']}</b> · {when} · {html.escape(order.get('status', ''))}\n"
            f"🆔 {order['product_id']} {html.escape(product.get('title', '')[:40])}\n"
            f"📦 {html.escape(order['variant'])} — {html.escape(order.get('price', '—'))}"
            f" · 💳 {html.escape(order.get('prepay', '—'))}\n"
            f"📞 {html.escape(order['phone'])}\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_allowed(update):
        await deny(update)
        return ConversationHandler.END

    url = extract_poizon_url(update.message.text.strip())
    if not url:
        await update.message.reply_text(
            "Bu Poizon (Dewu) linkiga o'xshamayapti. Iltimos, to'g'ri link yuboring "
            "(butun 'ulashish' xabarini yuborsangiz ham bo'ladi)."
        )
        return ConversationHandler.END

    context.user_data["source_url"] = url
    msg = await update.message.reply_text("⏳ Sahifadan ma'lumot olinmoqda...")
    # Playwright'ning sync API'si asyncio tsikli ichida ishlamaydi —
    # shuning uchun alohida thread'da ishga tushiramiz.
    product = await asyncio.to_thread(fetch_product, url, MAX_IMAGES)

    if not product.ok:
        await msg.edit_text(
            "❌ Ma'lumot topilmadi yoki sahifa ochilmadi.\n"
            f"Xato: {product.error or 'aniqlanmagan'}\n"
            "Boshqa link bilan urinib ko'ring."
        )
        return ConversationHandler.END

    context.user_data["product"] = {
        "title": product.title,
        "images": product.images,
        "specs": product.specs,
    }

    summary = [f"✅ Topildi: {product.title or '(sarlavha topilmadi)'}"]
    summary.append(f"🖼 Rasmlar: {len(product.images)} ta")
    summary.append(f"📋 Xarakteristika: {len(product.specs)} ta band" if product.specs else "📋 Xarakteristika: topilmadi")
    summary.append("")

    rate, rate_date = await asyncio.to_thread(fetch_cny_rate)
    context.user_data["rate"] = rate
    context.user_data["variants"] = detect_variants(product.title, product.title_zh)
    variants = context.user_data["variants"]

    # Poizon sahifada haqiqiy sotuv narxini ko'rsatmaydi ("¥ --" — faqat
    # ilovada ko'rinadi). 发售价格 esa rasmiy chiqarish narxi, ya'ni biz
    # to'laydigan narx emas — shuning uchun narxni adminning o'zi kiritadi.
    if product.price_cny:
        summary.append(
            f"ℹ️ Rasmiy chiqarish narxi: {product.price_cny} "
            "(bu Poizon sotuv narxi emas)"
        )
    summary.append(f"💱 Kurs ({rate_date}): 1 ¥ = {rate:,.2f} so'm".replace(",", " "))
    summary.append("")

    flight, status = await asyncio.to_thread(pick_flight, "Avto Kargo")
    if status == "ok":
        context.user_data["arrival"] = (
            f"{flight['arrival_from'].strftime('%d.%m')}–"
            f"{flight['arrival_to'].strftime('%d.%m.%Y')}"
        )
        summary.append(
            f"🛫 Reys: {flight['code']} (oxirgi qadoqlash "
            f"{flight['last_packing'].strftime('%d.%m')}) → kelish "
            f"{context.user_data['arrival']}"
        )
    elif status == "ulgurmaydi":
        summary.append(
            f"🛫 Yaqin reysga ulgurmaydi ({WAREHOUSE_BUFFER_DAYS} kun kerak)"
        )
    if is_liquid_product(product):
        summary.append("🚚 Avto Kargo (atir aviada olinmaydi)")
    summary.append("")

    # Link bilan bitta xabarda narx ham yuborilgan bo'lishi mumkin:
    # oxirgi qatorda faqat sonlar bo'lsa — bu narxlar
    tail = update.message.text.strip().splitlines()[-1].strip()
    if variants and re.fullmatch(r"[\d\s,.]+", tail):
        prefilled = parse_prices_text(tail, variants)
        if prefilled:
            await msg.edit_text("\n".join(summary))
            return await compute_costs_and_ask_markup(update, context, prefilled)

    if len(variants) > 1:
        sizes = ", ".join(f"{v:g}ml" for v in variants)
        summary.append(f"📦 {len(variants)} xil hajm topildi: {sizes}")
        summary.append("")
        summary.append("👇 Ilovada narxlarni ko'rib, shu yerga yozing:")
        summary.append(f"hajmlar tartibida — {' '.join(['770'] * len(variants))}")
    else:
        summary.append("👇 Ilovada narxni ko'rib, shu yerga yozing (masalan: 770):")

    # Telefonda bosilganda Dewu ilovasi shu mahsulotda ochiladi
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📱 Poizon ilovasida ochish", url=url)]]
    )
    await msg.edit_text("\n".join(summary), reply_markup=keyboard)

    if WEBAPP_URL:
        await update.message.reply_text(
            "yoki quyidagi tugma orqali ochiladigan ilovada to'ldiring 👇",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton(
                    "🧮 Narxlarni hisoblash",
                    web_app=WebAppInfo(url=build_webapp_url(product, context, url)),
                )]],
                resize_keyboard=True,
            ),
        )
    return WAITING_CNY_PRICES


def build_variant_list(product, context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    """Mini App uchun variantlar: nomi va (bilsak) vazni.

    Atirda hajm (ml) sarlavhadan topiladi. Krossovka/kiyimda esa variant
    topilmaydi — u holda bitta umumiy narx qatori bo'ladi.
    """
    sizes = context.user_data.get("variants") or []
    if sizes:
        return [
            {
                "label": f"{size:g} ml",
                "weightKg": round((size + PACKAGING_OVERHEAD_G) / 1000, 3),
            }
            for size in sizes
        ]

    weight_kg, _ = estimate_weight(product.title, product.specs)
    return [{"label": "Narx", "weightKg": weight_kg}]


def build_webapp_url(product, context: ContextTypes.DEFAULT_TYPE, source_url: str = "") -> str:
    """Mahsulot ma'lumotini Mini App manziliga joylaydi."""
    payload = {
        "productUrl": source_url,
        "title": product.title,
        "variants": build_variant_list(product, context),
        "rate": context.user_data.get("rate", FALLBACK_CNY_RATE),
        "kargoRate": AVTO_KARGO_RATE,
        "arrival": context.user_data.get("arrival", ""),
        "specs": product.specs,
        "channel": CHANNEL,
        "brand": BRAND_LABEL,
    }
    # Telegram URL'ning "#" qismini o'zi ishlatadi (tgWebAppData) — shuning
    # uchun ma'lumot query orqali, URL-xavfsiz base64 ko'rinishida uzatiladi.
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ).decode().rstrip("=")
    separator = "&" if "?" in WEBAPP_URL else "?"
    return f"{WEBAPP_URL}{separator}d={encoded}"


async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Mini App'dan kelgan tayyor narxlar."""
    try:
        data = json.loads(update.message.web_app_data.data)
    except Exception as e:
        log.warning("web_app_data o'qilmadi: %s", e)
        await update.message.reply_text("Ilovadan kelgan ma'lumot buzuq. Qaytadan urinib ko'ring.")
        return WAITING_CNY_PRICES

    product = context.user_data.get("product", {})
    if data.get("title"):
        product["title"] = data["title"]
    # Ilovada tayyorlangan (va kerak bo'lsa tahrirlangan) post matni
    if data.get("caption"):
        context.user_data["custom_caption"] = data["caption"]

    await update.message.reply_text(
        f"✅ Ilovadan qabul qilindi (ustama: {data.get('markup', '—')})",
        reply_markup=ReplyKeyboardRemove(),
    )
    return await send_preview(update, context, data.get("prices", {}))


async def compute_costs_and_ask_markup(
    update: Update, context: ContextTypes.DEFAULT_TYPE, prices_cny: dict[float, float]
) -> int:
    """Har bir hajm uchun tannarx + o'z vazniga qarab yetkazib berishni hisoblaydi."""
    rate = context.user_data.get("rate", FALLBACK_CNY_RATE)
    costs: dict[str, dict] = {}
    lines = []
    for size, cny in prices_cny.items():
        label = f"{size:g} ml" if size else "Narx"
        base = cny * rate
        weight = (size + PACKAGING_OVERHEAD_G) / 1000 if size else None
        delivery = weight * AVTO_KARGO_RATE if weight else None
        costs[label] = {"base": base, "delivery": delivery, "weight": weight}
        if weight:
            lines.append(
                f"🧴 <b>{label}</b> — ¥{cny:g} → {format_uzs(base)}\n"
                f"    📦 ~{weight:.2f} kg · 🚚 {format_uzs(delivery)} "
                f"= {format_uzs(base + delivery)}"
            )
        else:
            lines.append(f"🧴 <b>{label}</b> — ¥{cny:g} → {format_uzs(base)}")

    context.user_data["costs"] = costs
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    if all(c["delivery"] is not None for c in costs.values()):
        await update.message.reply_text("Endi ustama qancha? (masalan: 20% yoki 300000)")
        return WAITING_MARKUP

    await update.message.reply_text(
        "Vaznni aniqlay olmadim. Yetkazib berish narxi qancha? (masalan: 150000)"
    )
    return WAITING_DELIVERY


def parse_prices_text(text: str, variants: list[float]) -> dict[float, float] | None:
    """"570 770 970" yoki "50=570, 100=770" → {hajm: narx}. Topilmasa None."""
    entered = {float(s): float(p) for s, p in VARIANT_PRICE_RE.findall(text)}
    if not entered:
        numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)]
        if len(numbers) == len(variants):
            entered = dict(zip(variants, numbers))
    missing = [v for v in variants if v not in entered]
    return None if missing else {v: entered[v] for v in variants}


async def handle_cny_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    variants = context.user_data.get("variants", [])
    text = update.message.text

    if len(variants) > 1:
        prices_cny = parse_prices_text(text, variants)
        if prices_cny is None:
            example_ordered = " ".join(["770"] * len(variants))
            example_exact = ", ".join(f"{v:g}=770" for v in variants)
            await update.message.reply_text(
                "Narxlarni tushunmadim.\n"
                f"Hajmlar tartibida: {example_ordered}\n"
                f"yoki aniq: {example_exact}"
            )
            return WAITING_CNY_PRICES
    else:
        price = parse_amount(text)
        if price is None:
            await update.message.reply_text("Narxni tushunmadim. Masalan: 770")
            return WAITING_CNY_PRICES
        prices_cny = {variants[0] if variants else 0.0: price}

    return await compute_costs_and_ask_markup(update, context, prices_cny)


async def handle_delivery(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Vazn aniqlanmagan hollarda — bitta yetkazib berish narxi hammasiga."""
    delivery = parse_amount(update.message.text)
    if delivery is None:
        await update.message.reply_text(
            "Yetkazib berish narxini tushunmadim. Masalan: 150000"
        )
        return WAITING_DELIVERY

    costs = context.user_data.get("costs", {})
    for cost in costs.values():
        cost["delivery"] = delivery

    lines = [f"🚚 Yetkazib berish: {format_uzs(delivery)}", ""]
    for label, cost in costs.items():
        lines.append(f"🧴 {label}: jami xarajat {format_uzs(cost['base'] + delivery)}")
    lines.append("")
    lines.append("Endi ustama qancha? (masalan: 20% yoki 300000)")

    await update.message.reply_text("\n".join(lines))
    return WAITING_MARKUP


async def send_preview(
    update: Update, context: ContextTypes.DEFAULT_TYPE, prices: dict[str, str]
) -> int:
    """Tayyor postni tasdiq uchun ko'rsatadi."""
    context.user_data["prices"] = prices
    product = context.user_data.get("product", {})

    # Tovar raqami shu yerda ajratiladi — post ko'rinishida ham ko'rinsin
    if not context.user_data.get("product_id"):
        context.user_data["product_id"] = store.next_product_id()

    caption = with_order_footer(
        build_caption(
            product.get("title", ""),
            product.get("specs", {}),
            prices,
            context.user_data.get("arrival", ""),
        ),
        context.user_data["product_id"],
    )
    images = product.get("images", [])

    try:
        await send_product_post(
            send_photo=lambda b, c: update.message.reply_photo(b, caption=c, parse_mode="HTML"),
            send_media_group=lambda media: update.message.reply_media_group(media=media),
            send_text=lambda t: update.message.reply_text(t, parse_mode="HTML"),
            images=images,
            caption=caption,
        )
    except Exception as e:
        log.warning("preview send failed: %s", e)
        await update.message.reply_text(caption)

    # Boshida hamma rasm tanlangan bo'ladi
    context.user_data["selected"] = set(range(len(images)))

    await update.message.reply_text(
        f"Rasmlar yuqorida tartib bo'yicha ({len(images)} ta).\n"
        "Keraksizlarini o'chirib, keyin kanalga yuboring:",
        reply_markup=build_confirm_keyboard(len(images), context.user_data["selected"]),
    )
    return WAITING_CONFIRM


def build_confirm_keyboard(total: int, selected: set[int]) -> InlineKeyboardMarkup:
    """Rasm raqamlari (belgilangan/belgilanmagan) va tasdiq tugmalari."""
    rows, row = [], []
    for i in range(total):
        mark = "✅" if i in selected else "⬜️"
        row.append(InlineKeyboardButton(f"{mark}{i + 1}", callback_data=f"img:{i}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    if total:
        rows.append([
            InlineKeyboardButton("Hammasi", callback_data="pick:all"),
            InlineKeyboardButton("Hech qaysi", callback_data="pick:none"),
        ])
    rows.append([
        InlineKeyboardButton("✏️ Matnni tahrirlash", callback_data="edit"),
        InlineKeyboardButton(f"✅ Yuborish ({len(selected)})", callback_data="confirm"),
    ])
    rows.append([InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel")])
    return InlineKeyboardMarkup(rows)


def current_caption(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Tahrirlangan matn bo'lsa o'sha, bo'lmasa avtomatik yig'ilgani.

    Tovar raqami va buyurtma havolasi doim oxiriga qo'shiladi — hatto
    matn qo'lda tahrirlangan bo'lsa ham (aks holda xaridor buyurtma
    bera olmay qoladi).
    """
    product_id = context.user_data.get("product_id", "")
    custom = context.user_data.get("custom_caption")
    if custom:
        body = custom.split("\n\n🆔 Tovar raqami:")[0]  # eski footerni takrorlamaymiz
    else:
        product = context.user_data.get("product", {})
        body = build_caption(
            product.get("title", ""),
            product.get("specs", {}),
            context.user_data.get("prices", {}),
            context.user_data.get("arrival", ""),
        )
    return with_order_footer(body, product_id) if product_id else body


async def handle_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Foydalanuvchi yuborgan matn postning yangi matni bo'ladi."""
    context.user_data["custom_caption"] = update.message.text
    images = context.user_data.get("product", {}).get("images", [])
    selected = context.user_data.get("selected", set(range(len(images))))

    await update.message.reply_text("✏️ Matn yangilandi. Yangi ko'rinishi:")
    try:
        await update.message.reply_text(update.message.text, parse_mode="HTML")
    except Exception:
        await update.message.reply_text(update.message.text)

    await update.message.reply_text(
        "Shu holida yuborilsinmi?",
        reply_markup=build_confirm_keyboard(len(images), selected),
    )
    return WAITING_CONFIRM


async def handle_markup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    costs = context.user_data.get("costs", {})

    # Ustama har bir hajmning jami xarajatiga (tannarx + yetkazib berish) qo'llanadi
    finals: dict[str, str] = {}
    report = []
    for label, cost in costs.items():
        total = cost["base"] + (cost["delivery"] or 0.0)
        final = apply_markup(total, update.message.text)
        if final is None:
            await update.message.reply_text(
                "Ustamani tushunmadim. Masalan: 20%  yoki  300000"
            )
            return WAITING_MARKUP
        finals[label] = format_uzs(final)
        report.append(
            f"🧴 {label}: {format_uzs(cost['base'])} + "
            f"{format_uzs(cost['delivery'] or 0)} → 💰 {format_uzs(final)}"
        )

    await update.message.reply_text(
        f"➕ Ustama: {update.message.text.strip()}\n\n" + "\n".join(report)
    )
    return await send_preview(update, context, finals)


async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel":
        await query.edit_message_text("Bekor qilindi.")
        context.user_data.clear()
        return ConversationHandler.END

    product = context.user_data.get("product", {})
    all_images = product.get("images", [])
    selected = context.user_data.setdefault("selected", set(range(len(all_images))))

    if query.data == "edit":
        await query.edit_message_text(
            "✏️ Quyidagi matnni nusxalab, tahrirlab menga qaytaring.\n"
            "(qalin yozuv uchun <b>...</b> ishlatishingiz mumkin)",
            parse_mode="HTML",
        )
        await query.message.reply_text(current_caption(context))
        return WAITING_EDIT

    # Rasm tanlash tugmalari — yuborilmaydi, faqat belgilashni yangilaydi
    if query.data.startswith("img:") or query.data.startswith("pick:"):
        if query.data == "pick:all":
            selected = set(range(len(all_images)))
        elif query.data == "pick:none":
            selected = set()
        else:
            index = int(query.data.split(":")[1])
            selected.symmetric_difference_update({index})
        context.user_data["selected"] = selected
        await query.edit_message_reply_markup(
            reply_markup=build_confirm_keyboard(len(all_images), selected)
        )
        return WAITING_CONFIRM

    caption = current_caption(context)
    images = [img for i, img in enumerate(all_images) if i in selected]

    async def post_to_channel(mode: str | None):
        return await send_product_post(
            send_photo=lambda b, c: context.bot.send_photo(
                chat_id=CHANNEL, photo=b, caption=c, parse_mode=mode
            ),
            send_media_group=lambda media: context.bot.send_media_group(chat_id=CHANNEL, media=media),
            send_text=lambda t: context.bot.send_message(chat_id=CHANNEL, text=t, parse_mode=mode),
            images=images,
            caption=caption,
        )

    try:
        try:
            sent = await post_to_channel("HTML")
        except BadRequest as e:
            # Tahrirlangan matnda "<" yoki "&" bo'lsa HTML rejimi xato beradi —
            # bunday holda oddiy matn sifatida yuboramiz
            log.warning("HTML rejimida yuborilmadi (%s), oddiy matn bilan urinamiz", e)
            sent = await post_to_channel(None)

        product_id = context.user_data.get("product_id", "")
        if product_id:
            store.save_product(
                product_id=product_id,
                url=context.user_data.get("source_url", ""),
                title=product.get("title", ""),
                prices=context.user_data.get("prices", {}),
                arrival=context.user_data.get("arrival", ""),
                caption=caption,
                message_ids=[m.message_id for m in sent if m],
            )
        await query.edit_message_text(
            f"✅ Kanalga yuborildi: {CHANNEL} ({len(images)} ta rasm)\n"
            f"🆔 Tovar raqami: {product_id}"
        )
    except Exception as e:
        log.exception("channel send failed")
        await query.edit_message_text(f"❌ Kanalga yuborishda xato: {e}")

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Bekor qilindi.")
    return ConversationHandler.END


async def products_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tovarlar — joylangan tovarlar ro'yxati."""
    if not is_allowed(update):
        await deny(update)
        return

    products = store.list_products(20)
    if not products:
        await update.message.reply_text("Hozircha tovar yo'q.")
        return

    lines = [f"🛍 Oxirgi {len(products)} ta tovar:\n"]
    for product in products:
        lines.append(
            f"🆔 <b>{product['id']}</b> · {product['created'][5:10]}\n"
            f"{html.escape(product.get('title', '')[:45])}\n"
            f"{' · '.join(product.get('prices', {}).values()) or '—'}\n"
        )
    lines.append("O'chirish: /ochirish 101")
    lines.append("Matnni tahrirlash: /tahrirlash 101")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def delete_product_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ochirish 101 — kanaldagi postni va yozuvni o'chiradi."""
    if not is_allowed(update):
        await deny(update)
        return
    if not context.args:
        await update.message.reply_text("Qaysi tovar? Masalan: /ochirish 101")
        return

    product_id = context.args[0].lstrip("#")
    product = store.get_product(product_id)
    if not product:
        await update.message.reply_text(f"🆔 {product_id} topilmadi.")
        return

    await update.message.reply_text(
        f"🆔 <b>{product_id}</b> — {html.escape(product.get('title', '')[:60])}\n\n"
        "Kanaldagi post ham o'chiriladi. Davom etamizmi?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑 Ha, o'chirilsin", callback_data=f"del:{product_id}"),
            InlineKeyboardButton("❌ Yo'q", callback_data="del:cancel"),
        ]]),
    )


async def handle_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    product_id = query.data.split(":", 1)[1]
    if product_id == "cancel":
        await query.edit_message_text("Bekor qilindi.")
        return

    product = store.get_product(product_id)
    if not product:
        await query.edit_message_text(f"🆔 {product_id} topilmadi.")
        return

    removed, failed = 0, 0
    for message_id in product.get("message_ids", []):
        try:
            await context.bot.delete_message(chat_id=CHANNEL, message_id=message_id)
            removed += 1
        except Exception as e:
            failed += 1
            log.warning("kanaldagi post o'chmadi (%s): %s", message_id, e)

    store.delete_product(product_id)
    note = f"Kanaldan {removed} ta xabar o'chirildi."
    if failed:
        note += (
            f"\n⚠️ {failed} tasi o'chmadi — Telegram botga 48 soatdan eski "
            "xabarlarni o'chirishga ruxsat bermaydi, ularni qo'lda o'chiring."
        )
    await query.edit_message_text(f"🗑 🆔 {product_id} o'chirildi.\n{note}")


async def edit_product_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/tahrirlash 101 — kanaldagi post matnini o'zgartirish."""
    if not is_allowed(update):
        await deny(update)
        return ConversationHandler.END
    if not context.args:
        await update.message.reply_text("Qaysi tovar? Masalan: /tahrirlash 101")
        return ConversationHandler.END

    product_id = context.args[0].lstrip("#")
    product = store.get_product(product_id)
    if not product:
        await update.message.reply_text(f"🆔 {product_id} topilmadi.")
        return ConversationHandler.END
    if not product.get("message_ids"):
        await update.message.reply_text(
            f"🆔 {product_id} uchun kanaldagi post raqami saqlanmagan "
            "(eski tovar) — matnni o'zgartira olmayman."
        )
        return ConversationHandler.END

    context.user_data["edit_product_id"] = product_id
    await update.message.reply_text(
        "✏️ Quyidagi matnni nusxalab, tahrirlab menga qaytaring.\n"
        "Bekor qilish: /cancel"
    )
    await update.message.reply_text(product.get("caption", ""))
    return PRODUCT_EDIT


async def handle_product_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    product_id = context.user_data.get("edit_product_id")
    product = store.get_product(product_id) if product_id else None
    if not product:
        await update.message.reply_text("Tovar topilmadi.")
        return ConversationHandler.END

    new_caption = update.message.text
    message_id = product["message_ids"][0]  # caption albomning birinchi rasmida

    async def apply(mode: str | None):
        await context.bot.edit_message_caption(
            chat_id=CHANNEL, message_id=message_id,
            caption=new_caption, parse_mode=mode,
        )

    try:
        try:
            await apply("HTML")
        except BadRequest as e:
            if "no text in the message" in str(e).lower():
                await context.bot.edit_message_text(
                    chat_id=CHANNEL, message_id=message_id,
                    text=new_caption, parse_mode="HTML",
                )
            else:
                log.warning("HTML bilan tahrirlanmadi (%s), oddiy matn", e)
                await apply(None)
        store.update_product(product_id, caption=new_caption)
        await update.message.reply_text(f"✅ 🆔 {product_id} matni yangilandi.")
    except Exception as e:
        log.exception("post tahrirlanmadi")
        await update.message.reply_text(
            f"❌ Tahrirlab bo'lmadi: {e}\n"
            "(Telegram 48 soatdan eski postlarni tahrirlashga ruxsat bermasligi mumkin)"
        )

    context.user_data.clear()
    return ConversationHandler.END


async def order_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Buyurtma yarim yo'lda qolib ketgan — holatni tozalaymiz."""
    context.user_data.clear()
    if update.effective_message:
        await update.effective_message.reply_text(
            "⏳ Buyurtma vaqti tugadi.\n\n"
            f"Qaytadan boshlash uchun {CHANNEL} kanalidagi «🛒 Buyurtma berish» "
            "havolasini bosing yoki tovar raqamini yozing.",
            reply_markup=ReplyKeyboardRemove(),
        )
    return ConversationHandler.END


async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Hech bir oqimga tushmagan xabar — javobsiz qoldirmaymiz."""
    if is_allowed(update):
        await update.message.reply_text(
            "Tovar joylash uchun Poizon (Dewu) linkini yuboring.\n"
            "Buyurtmalarni ko'rish: /buyurtmalar"
        )
        return
    await update.message.reply_text(
        f"Buyurtma berish uchun {CHANNEL} kanalimizdan tovarni tanlang va "
        "post ostidagi «🛒 Buyurtma berish» havolasini bosing.\n\n"
        "Yoki tovar raqamini yozing (masalan: 101)"
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kutilmagan xato botni to'xtatib qo'ymasin — yozib qo'yamiz va davom etamiz."""
    log.exception("Ishlov berishda xato", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "❌ Kutilmagan xato yuz berdi. Qaytadan urinib ko'ring yoki /start bosing."
            )
    except Exception:
        pass


def main() -> None:
    missing = settings.missing_required()
    if missing:
        raise SystemExit(
            "Sozlamalar yetishmayapti: " + ", ".join(missing) + "\n"
            "Ularni environment variable sifatida bering yoki config.json ga yozing "
            "(namuna: .env.example)."
        )

    app = Application.builder().token(settings.BOT_TOKEN).build()
    app.add_error_handler(on_error)

    # Mini App tugmasi klaviaturada turgani uchun undan kelgan ma'lumot
    # suhbatning istalgan bosqichida qabul qilinishi kerak
    webapp_handler = MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data)

    conv = ConversationHandler(
        # Faqat havola bor xabar suhbatni boshlaydi — shunda xaridorning
        # oddiy "101" raqami bu yerga tushib qolmaydi
        entry_points=[
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Regex(r"https?://"),
                handle_link,
            )
        ],
        states={
            WAITING_CNY_PRICES: [
                webapp_handler,
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cny_prices),
            ],
            WAITING_DELIVERY: [
                webapp_handler,
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_delivery),
            ],
            WAITING_MARKUP: [
                webapp_handler,
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_markup),
            ],
            WAITING_CONFIRM: [
                webapp_handler,
                CallbackQueryHandler(handle_confirm, pattern=r"^(?!buy:)"),
            ],
            WAITING_EDIT: [
                webapp_handler,
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Xaridor oqimi: kanaldagi havola (/start 101) yoki shunchaki raqam
    order_conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex(r"^#?\d{1,6}$"), start_order_by_id),
        ],
        states={
            ORDER_CONTACT: [
                CallbackQueryHandler(handle_order_variant, pattern=r"^buy:"),
                MessageHandler(
                    filters.CONTACT | (filters.TEXT & ~filters.COMMAND),
                    handle_order_contact,
                ),
            ],
            ORDER_RECEIPT: [
                MessageHandler(
                    filters.PHOTO | filters.Document.ALL | (filters.TEXT & ~filters.COMMAND),
                    handle_order_receipt,
                ),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, order_timeout),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        # Xaridor yarim yo'lda tashlab ketsa, holat osilib qolmasin —
        # aks holda keyingi xabari telefon raqami deb qabul qilinadi
        conversation_timeout=ORDER_TIMEOUT_MINUTES * 60,
    )

    # Tovarni tahrirlash — /tahrirlash 101 dan keyin yangi matn kutiladi
    edit_conv = ConversationHandler(
        entry_points=[CommandHandler("tahrirlash", edit_product_command)],
        states={
            PRODUCT_EDIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_product_edit)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=ORDER_TIMEOUT_MINUTES * 60,
    )

    app.add_handler(CommandHandler("buyurtmalar", orders_command))
    app.add_handler(CommandHandler("tovarlar", products_command))
    app.add_handler(CommandHandler("ochirish", delete_product_command))
    app.add_handler(CallbackQueryHandler(handle_status_change, pattern=r"^st:"))
    app.add_handler(CallbackQueryHandler(handle_delete_confirm, pattern=r"^del:"))
    app.add_handler(edit_conv)
    # Tartib muhim: admin suhbati birinchi tekshirilsin, aks holda u narx
    # kiritayotganda yozgan "770" tovar raqami deb qabul qilinadi
    app.add_handler(conv)
    app.add_handler(order_conv)
    # Eng oxirida: yuqoridagilarga tushmagan xabarlarga javob beramiz
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_message))

    app.post_init = remember_bot_username
    log.info("Bot ishga tushmoqda...")
    app.run_polling()


async def remember_bot_username(app: Application) -> None:
    """Deep link uchun bot username'ini aniqlab olamiz."""
    global BOT_USERNAME
    me = await app.bot.get_me()
    BOT_USERNAME = me.username
    log.info("Bot: @%s | tovarlar: %s", BOT_USERNAME, store.product_count())


async def start_order_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Xaridor shunchaki tovar raqamini yozsa."""
    return await show_product_for_order(update, context, update.message.text)


if __name__ == "__main__":
    main()
