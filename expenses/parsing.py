"""Разбор текста: ручной ввод ("15000 такси") и уведомлений банков (Humo, Uzcard, Visa банков Узбекистана)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 1 500 000 | 1,500,000.00 | 50.000,00 | 15,5 | 15000
NUM = r"\d{1,3}(?:[  ,.]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?"
_GROUPED = re.compile(r"(\d{1,3}(?:[ ,.]\d{3})+)(?:[.,](\d{1,2}))?")

PERSONAL, BUSINESS = "personal", "business"
WALLET_LABEL = {PERSONAL: "👤 Личное", BUSINESS: "💼 Бизнес"}

CATEGORIES = {
    "Еда": ["еда", "продукт", "корзинка", "korzinka", "makro", "macro", "oziq", "ovqat", "bozor", "базар", "havas",
            "hayot", "carrefour", "baraka", "smart market", "супермаркет", "supermarket", "хлеб"],
    "Кафе и рестораны": ["кафе", "cafe", "ресторан", "restoran", "обед", "ужин", "завтрак", "кофе", "coffee", "starbucks",
                         "kfc", "burger", "pizza", "пицца", "evos", "oqtepa", "lavash", "лаваш", "choyxona", "чайхана",
                         "yandex eats", "wolt", "express24", "doner", "plov", "плов", "bellissimo", "safia"],
    "Транспорт": ["такси", "taxi", "yandex go", "yandex.go", "yandexgo", "mytaxi", "бензин", "заправка", "азс", "metro",
                  "метро", "автобус", "парковка", "petrol", "benzin", "propan", "метан", "uzbekneftegaz", "mustang",
                  "lukoil", "uber", "indrive", "avtobus", "atto"],
    "Связь и интернет": ["связь", "uzmobile", "beeline", "ucell", "mobiuz", "uzonline", "интернет", "internet", "sarkor",
                         "turon telecom", "comnet"],
    "Коммуналка и жильё": ["свет", " газ ", "вода", "коммунал", "hududgaz", "elektr", "энерго", "suvsoz", "аренда",
                           "квартира", "ijara", "issiqlik"],
    "Здоровье": ["аптека", "apteka", "врач", "клиника", "klinika", "лекарств", "pharm", "dori", "стоматолог"],
    "Подписки": ["netflix", "spotify", "youtube", "icloud", "apple.com", "google", "chatgpt", "openai", "claude",
                 "anthropic", "подписка", "telegram premium", "yandex plus", "kinopoisk"],
    "Развлечения": ["кино", "cinema", "kino", "игр", "game", "steam", "playstation", "театр", "концерт", "bowling", "magic city"],
    "Одежда и покупки": ["одежда", "обувь", "uzum market", "uzum.uz", "wildberries", "ozon", "temu", "aliexpress",
                         "amazon", "mediapark", "texnomart", "zara", "lc waikiki", "defacto", "mall", "samarqand darvoza"],
    "Переводы": ["perevod", "перевод", "p2p", "o'tkazma", "otkazma", "transfer", "card2card"],
    "Товары (бизнес)": ["1688", "taobao", "poizon", "alibaba", "поставщик", "закуп", "товар"],
    "Доставка и карго": ["карго", "cargo", "mkexpress", "mk express", "fargo"],
    "Реклама": ["реклама", "таргет", "target", "instagram ads", "facebook ads", "meta ads", "telegram ads", "блогер"],
    "Сервисы бизнеса": ["railway", "neon", "hosting", "хостинг", "домен", "domain", "github", "сайт"],
}
BUSINESS_CATS = {"Товары (бизнес)", "Доставка и карго", "Реклама", "Сервисы бизнеса"}
INCOME_CAT = "Доход"
OTHER = "Прочее"
ALL_CATEGORIES = list(CATEGORIES) + [OTHER, INCOME_CAT]

INCOME_WORDS = ("зарплат", "получил", "пришло", "доход", "зачисл", "пополнен", "popolnenie", "zachislenie", "kirim",
                "tushum", "received", "credit", "deposit", "поступлен", "возврат", "vozvrat", "refund", "cashback",
                "кэшбэк", "перевод от", "➕", "продажа")
EXPENSE_WORDS = ("pokupka", "oplata", "spisanie", "списан", "оплата", "покупка", "платеж", "платёж", "purchase",
                 "payment", "debit", "chiqim", "to'lov", "tolov", "xarid", "snyatie", "снятие", "withdraw", "➖")
TRANSFER_WORDS = ("perevod", "перевод", "p2p", "o'tkazma", "otkazma")
BANK_HINTS = INCOME_WORDS + EXPENSE_WORDS + TRANSFER_WORDS + (
    "balans", "balance", "баланс", "остаток", "humo", "uzcard", "visa", "карта", "karta", "uzs", "💳", "📍")
BUSINESS_TAGS = ("#бизнес", "#биз", "#б", "#biz", "#b")

_MULT = {"k": 1000, "к": 1000, "тыс": 1000, "тысяч": 1000, "тыс.": 1000, "млн": 1_000_000, "mln": 1_000_000}


@dataclass
class Parsed:
    amount: int            # в сумах, целое
    kind: str              # "exp" | "inc"
    category: str
    merchant: str = ""
    note: str = ""
    currency: str = "UZS"
    wallet: str = PERSONAL
    days_ago: int = 0


def to_number(tok: str) -> float:
    s = tok.replace(" ", " ").strip()
    m = _GROUPED.fullmatch(s)
    if m:
        return float(re.sub(r"[ ,.]", "", m.group(1)) + ("." + m.group(2) if m.group(2) else ""))
    return float(s.replace(" ", "").replace(",", "."))


def guess_category(text: str) -> str | None:
    t = f" {text.lower()} "
    for cat, words in CATEGORIES.items():
        if any(w in t for w in words):
            return cat
    return None


def wallet_for(category: str) -> str:
    return BUSINESS if category in BUSINESS_CATS else PERSONAL


def is_income(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in INCOME_WORDS)


def _word(w: str) -> str:
    return rf"(?<!\w){re.escape(w)}(?!\w)"


def parse_manual(text: str) -> Parsed | None:
    """'15000 такси', '45к обед', '+3 млн зарплата', 'вчера 20000 бензин', '500000 карго #б'."""
    text = text.strip()
    days_ago = 0
    for word, d in (("позавчера", 2), ("вчера", 1), ("kecha", 1)):
        if re.search(_word(word), text, re.I):
            days_ago = d
            text = re.sub(_word(word), " ", text, flags=re.I)
            break
    biz = False
    for t in BUSINESS_TAGS:
        if re.search(_word(t), text, re.I):
            biz = True
            text = re.sub(_word(t), " ", text, flags=re.I)
    m = re.search(rf"([+\-−–]?)\s*({NUM})\s*(тысяч|тыс\.?|млн|mln|[кk])?(?!\w)", text, re.I)
    if not m:
        return None
    try:
        val = to_number(m.group(2))
    except ValueError:
        return None
    suf = (m.group(3) or "").lower()
    if suf:
        val *= _MULT.get(suf, 1)
    amount = int(round(val))
    if amount <= 0:
        return None
    note = re.sub(r"\s+", " ", text[:m.start()] + " " + text[m.end():]).strip(" ,.:;-")
    inc = m.group(1) == "+" or is_income(note)
    cat = INCOME_CAT if inc else (guess_category(note) or OTHER)
    wallet = BUSINESS if biz else wallet_for(cat)
    return Parsed(amount, "inc" if inc else "exp", cat, merchant=note[:60], note=note, wallet=wallet, days_ago=days_ago)


def looks_like_bank(text: str) -> bool:
    t = text.lower()
    return len(text) > 45 or sum(w in t for w in BANK_HINTS) >= 2


def _symbols_to_breaks(text: str) -> str:
    """Эмодзи в уведомлениях Humo/Uzcard — разделители полей: 📍 магазин, 💳 карта, 🕓 время, 💰 баланс."""
    return "".join("\n" if unicodedata.category(ch) in ("So", "Cs") or ord(ch) > 0xFFFF else ch for ch in text)


_BAL_WORDS = r"balans|balance|баланс|остаток|ostatok|qoldiq|dostupno|доступно|available"
_BAL = re.compile(rf"(?:{_BAL_WORDS})[^\d\n]{{0,12}}(?:{NUM})\s*\w{{0,4}}", re.I)
_CUR = r"(UZS|SUM|СУМОВ|СУМ|СЎМ|SO['ʻ‘’]?M|SOM|USD|\$|EUR|€|RUB|₽)"
_AMT_CUR = re.compile(rf"(?:{_CUR}\s*({NUM}))|(?:({NUM})\s*{_CUR}(?!\w))", re.I)
_CARD = re.compile(r"(?<!\w)(?:humo|uzcard|visa|mastercard|master|mir|карта|karta|card|kartadan|kartaga)(?!\w)[\s*•x\d\-]*", re.I)
_MASK = re.compile(r"[*•]{1,6}\s*\d{4}|\d{4,6}[*•x]{2,}\d{2,4}", re.I)
_PREFIX = re.compile(r"^\s*(?:pokupka|oplata|purchase|payment|оплата|покупка|платеж|платёж|списание|spisanie|xarid|"
                     r"to'lov|tolov|chiqim|merchant|магазин|do'kon|joy|место|продавец|в|v|at|to)(?!\w)\s*[:\-@]?\s*", re.I)
_DATE = re.compile(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{1,2}:\d{2}(?::\d{2})?")
_SPLIT = re.compile(r"[,;\n|]|\.\s")
_LEGAL = re.compile(r"^(?:ooo|ооо|mchj|xk|ip|ип|llc|чп)\s+|\s+(?:ooo|mchj|llc)$", re.I)
_KEYWORDS = {w for w in INCOME_WORDS + EXPENSE_WORDS + TRANSFER_WORDS} | {
    "uzs", "sum", "сум", "комиссия", "komissiya", "fee", "успешно", "muvaffaqiyatli", "approved", "summa", "сумма",
    "balans", "баланс", "остаток", "popolnenie", "pokupka", "oplata"}


def extract_merchant(text: str) -> str:
    t = _symbols_to_breaks(text)
    t = _BAL.sub(" ", t)
    t = _AMT_CUR.sub(" ", t)
    t = _DATE.sub(" ", t)
    t = _MASK.sub(" ", t)
    t = _CARD.sub(" ", t)
    for seg in _SPLIT.split(t):
        seg = _PREFIX.sub("", _PREFIX.sub("", seg)).strip(" .:*-–—#№")
        seg = _LEGAL.sub("", seg).strip(" \"'«»")
        if len(re.findall(r"[^\W\d_]", seg)) < 3 or seg.lower() in _KEYWORDS:
            continue
        return seg
    return ""


def amount_in_text(text: str, usd_rate: float) -> tuple[int, str] | None:
    clean = _BAL.sub(" ", _symbols_to_breaks(text))
    for m in _AMT_CUR.finditer(clean):
        cur = (m.group(1) or m.group(4) or "").upper()
        tok = m.group(2) or m.group(3)
        try:
            val = to_number(tok)
        except (ValueError, TypeError):
            continue
        if val <= 0:
            continue
        if cur in ("USD", "$"):
            return int(round(val * usd_rate)), "USD"
        if cur in ("EUR", "€"):
            return int(round(val * usd_rate * 1.08)), "EUR"
        if cur in ("RUB", "₽"):
            return int(round(val * usd_rate / 90)), "RUB"
        return int(round(val)), "UZS"
    return None


def parse_bank(text: str, usd_rate: float = 12500) -> Parsed | None:
    """Уведомление банка / Apple Pay. Сумма, приход/расход, магазин. Баланс игнорируется."""
    got = amount_in_text(text, usd_rate)
    if not got:
        # Shortcuts иногда отдаёт просто "15 000.00" без валюты
        cleaned = _DATE.sub(" ", _MASK.sub(" ", _BAL.sub(" ", text)))
        m = re.search(NUM, cleaned)
        if not m:
            return None
        try:
            got = (int(round(to_number(m.group(0)))), "UZS")
        except ValueError:
            return None
    amount, cur = got
    if amount <= 0:
        return None
    low = text.lower()
    inc = is_income(low) and not any(w in low for w in EXPENSE_WORDS)
    merchant = extract_merchant(text)
    if inc:
        cat = INCOME_CAT
    else:
        cat = guess_category(merchant) or guess_category(text) or OTHER
    return Parsed(amount, "inc" if inc else "exp", cat, merchant=merchant[:60], currency=cur, wallet=wallet_for(cat))


def fmt(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")
