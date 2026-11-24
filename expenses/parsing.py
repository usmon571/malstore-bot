"""Разбор текста: ручной ввод ("15000 такси") и уведомлений банков (Humo, Uzcard, Visa банков Узбекистана)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

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
    # необязательные покупки «просто захотелось» — по назначению, а не по магазину
    "Хотелки": ["хотелк", "захотел", "импульс", "спонтан", "просто так", "побаловать", "xohish", "хочушк"],
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
                "кэшбэк", "перевод от", "➕", "продажа", "tushdi", "tushum", "qabul qilindi", "hisobga")
EXPENSE_WORDS = ("pokupka", "oplata", "spisanie", "списан", "оплата", "покупка", "платеж", "платёж", "purchase",
                 "payment", "debit", "chiqim", "to'lov", "tolov", "xarid", "snyatie", "снятие", "withdraw", "➖",
                 "yechildi", "yechib olindi", "hisobdan", "to'landi", "tolandi", "списание")
# отмена / возврат покупки — деньги вернулись, даже если в SMS есть «oplata»
REFUND_WORDS = ("otmena", "отмена", "vozvrat", "возврат", "refund", "reversal", "bekor qilindi", "qaytarildi")
# коды подтверждения и прочие не-операции от банка
OTP_WORDS = ("kod", "код", "code", "parol", "пароль", "tasdiq", "подтвержд", "podtverzhd", "nikomu", "никому",
             "hech kimga", "otp")
TRANSFER_WORDS = ("perevod", "перевод", "p2p", "o'tkazma", "otkazma", "o'tkazildi")
# платёжные приложения: Alliance Pay (Asia Alliance Bank), Payme, Click, Uzum, Paynet
APPS = ("alliance", "aab", "payme", "paycom", "click", "uzum", "paynet", "apelsin", "oson")
BANK_HINTS = INCOME_WORDS + EXPENSE_WORDS + TRANSFER_WORDS + APPS + (
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
    cards: tuple = ()          # последние 4 цифры всех карт в SMS
    brand: str = ""            # Humo / Uzcard / Visa / Mastercard
    balance: int | None = None # остаток на карте после операции
    ts: datetime | None = None # время операции из SMS (если указано)


def to_number(tok: str) -> float:
    s = tok.replace(" ", " ").strip()
    m = _GROUPED.fullmatch(s)
    if m:
        return float(re.sub(r"[ ,.]", "", m.group(1)) + ("." + m.group(2) if m.group(2) else ""))
    return float(s.replace(" ", "").replace(",", "."))


WANTS = "Хотелки"
# категории «по назначению»: магазин за ними не закрепляется (в Korzinka бывают и продукты, и хотелки)
NO_LEARN = {WANTS}


def guess_category(text: str) -> str | None:
    t = f" {text.lower()} "
    # «хотелка кофе» — это хотелка, а не кафе: явное слово важнее магазина
    for cat in [WANTS] + [c for c in CATEGORIES if c != WANTS]:
        if any(w in t for w in CATEGORIES[cat]):
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
_CUR = r"(UZS|SUM|СУМОВ|СУМ|СЎМ|SO['ʻ‘’]?M|SOM|USD|\$|EUR|€|RUB|₽)"
_BAL = re.compile(rf"(?:{_BAL_WORDS})[^\d\n]{{0,12}}(?:{NUM})\s*(?:{_CUR}(?!\w)|\w{{0,4}})", re.I)
_AMT_CUR = re.compile(rf"(?:{_CUR}\s*({NUM}))|(?:({NUM})\s*{_CUR}(?!\w))", re.I)
_CARD = re.compile(r"(?<!\w)(?:humo(?:card)?|uzcard|visa|mastercard|master|mir|карта|karta|card|kartadan|kartaga)(?!\w)[\s*•x\d\-]*", re.I)
_MASK = re.compile(r"[*•]{1,6}\s*\d{4}|\d{4,6}[*•x]{2,}\d{2,4}", re.I)
_PREFIX = re.compile(r"^\s*(?:pokupka|oplata|purchase|payment|оплата|покупка|платеж|платёж|списание|spisanie|xarid|"
                     r"to'lov|tolov|chiqim|merchant|магазин|do'kon|joy|место|продавец|в|v|at|to)(?!\w)\s*[:\-@]?\s*", re.I)
_DATE = re.compile(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{1,2}:\d{2}(?::\d{2})?")
_SPLIT = re.compile(r"[,;\n|]|\.\s")
_LEGAL = re.compile(r"^(?:ooo|ооо|mchj|xk|ip|ип|llc|чп)\s+|\s+(?:ooo|mchj|llc)$", re.I)
# «PAYME*KORZINKA», «CLICK*EVOS», «ALLIANCE PAY: Safia» → настоящий магазин после префикса приложения
_APP_PREFIX = re.compile(r"^(?:payme|paycom|click|uzum(?:\s*bank)?|paynet|apelsin|oson|alliance(?:\s*pay)?|aab)"
                         r"\s*[*:/\\>\-]\s*(?=\S)", re.I)
_APP_NAME = {"payme": "Payme", "paycom": "Payme", "click": "Click", "uzum": "Uzum", "uzum bank": "Uzum",
             "paynet": "Paynet", "apelsin": "Uzum", "oson": "Oson", "alliance": "Alliance Pay",
             "alliance pay": "Alliance Pay", "aab": "Alliance Pay"}
_KEYWORDS = {w for w in INCOME_WORDS + EXPENSE_WORDS + TRANSFER_WORDS} | {
    "uzs", "sum", "сум", "комиссия", "komissiya", "fee", "успешно", "muvaffaqiyatli", "approved", "summa", "сумма",
    "balans", "баланс", "остаток", "popolnenie", "pokupka", "oplata"}


def extract_merchant(text: str) -> str:
    # Humo 13131: «IYB VISA SUM P2P>Tas» — описание перевода от банка, оставляем как есть (иначе VISA вырежется как карта)
    for seg in _SPLIT.split(_symbols_to_breaks(text)):
        if re.search(r"(?<![a-z])p2p(?![a-z])", seg, re.I) and not _AMT_CUR.search(seg) and not _BAL.search(seg):
            seg = re.sub(r"\s+", " ", seg).strip(" .:*-–—#№")
            if len(re.findall(r"[^\W\d_]", seg)) >= 3:
                return seg
    t = _symbols_to_breaks(text)
    t = _BAL.sub(" ", t)
    t = _AMT_CUR.sub(" ", t)
    t = _DATE.sub(" ", t)
    t = _MASK.sub(" ", t)
    t = _CARD.sub(" ", t)
    fallback = ""
    for seg in _SPLIT.split(t):
        seg = _PREFIX.sub("", _PREFIX.sub("", seg.strip(" .:*-–—#№"))).strip(" .:*-–—#№")
        seg = _LEGAL.sub("", seg).strip(" \"'«»")
        seg = _APP_PREFIX.sub("", seg).strip()
        words = re.findall(r"[^\W\d_][\w'ʻ‘’]*", seg.lower())
        app = next((_APP_NAME[w] for w in words if w in _APP_NAME), "")
        if len(re.findall(r"[^\W\d_]", seg)) < 3 or seg.lower() in _KEYWORDS or \
                (words and all(w in _KEYWORDS or w in APPS for w in words)):
            fallback = fallback or app   # «PAYCOM» без магазина — хотя бы название приложения
            continue
        return seg
    return fallback


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


_LAST4 = re.compile(r"(?:[*•]{1,6}\s*|\d{4,6}[*•x]{2,}\d{0,4}?)(\d{4})(?!\d)", re.I)
_BRANDS = (("humo", "Humo"), ("uzcard", "Uzcard"), ("visa", "Visa"), ("mastercard", "Mastercard"),
           ("unionpay", "UnionPay"), ("mir", "Mir"))


def cards_in(text: str) -> tuple:
    """Последние 4 цифры карт в тексте: «*4521», «HUMO ****0912», «860012******7788» → ('4521', ...)."""
    seen = []
    for m in _LAST4.finditer(text or ""):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return tuple(seen)


def brand_in(text: str) -> str:
    t = (text or "").lower()
    return next((name for key, name in _BRANDS if re.search(rf"(?<![a-z]){key}(?:card)?(?![a-z])", t)), "")


def balance_in(text: str) -> int | None:
    m = _BAL.search(_symbols_to_breaks(text or ""))
    if not m:
        return None
    num = re.search(NUM, m.group(0))
    try:
        return int(round(to_number(num.group(0)))) if num else None
    except ValueError:
        return None


_TS_YMD = re.compile(r"(?<!\d)(\d{2})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})")             # 26-11-24 14:51 (Humo)
_TS_DMY = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{2}|\d{4})\s+(\d{1,2}):(\d{2})")  # 24.11.26 14:51


def sms_time(text: str, now: datetime | None = None) -> datetime | None:
    """Время операции из SMS. «26-11-24» бывает и ГГ-ММ-ДД, и ДД-ММ-ГГ — берём вариант ближе к сегодня.
    Возвращает None, если даты нет или она неправдоподобна (в будущем или старше 60 дней)."""
    now = now or datetime.now()
    found = []
    for m in _TS_YMD.finditer(text or ""):
        a, b, c, hh, mm = (int(x) for x in m.groups())
        found += [(2000 + a, b, c, hh, mm), (2000 + c, b, a, hh, mm)]
    for m in _TS_DMY.finditer(text or ""):
        d, mo, y, hh, mm = (int(x) for x in m.groups())
        found.append((y + 2000 if y < 100 else y, mo, d, hh, mm))
    best = None
    for y, mo, d, hh, mm in found:
        try:
            t = datetime(y, mo, d, hh, mm)
        except ValueError:
            continue
        if now - timedelta(days=60) <= t <= now + timedelta(hours=1) and (best is None or abs(now - t) < abs(now - best)):
            best = t
    return min(best, now) if best else None


def is_transaction(text: str) -> bool:
    """SMS банка — это операция? Коды подтверждения и реклама не записываются."""
    low = (text or "").lower()
    if not _AMT_CUR.search(_BAL.sub(" ", _symbols_to_breaks(text or ""))):
        return False
    money_word = any(w in low for w in EXPENSE_WORDS + INCOME_WORDS + TRANSFER_WORDS + REFUND_WORDS)
    if any(re.search(_word(w), low) for w in OTP_WORDS) and not money_word:
        return False
    return bool(cards_in(text)) or money_word


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
    inc = any(w in low for w in REFUND_WORDS) or (is_income(low) and not any(w in low for w in EXPENSE_WORDS))
    merchant = extract_merchant(text)
    if inc:
        cat = INCOME_CAT
    else:
        cat = guess_category(merchant) or guess_category(text) or OTHER
    return Parsed(amount, "inc" if inc else "exp", cat, merchant=merchant[:60], currency=cur, wallet=wallet_for(cat),
                  cards=cards_in(text), brand=brand_in(text), balance=balance_in(text), ts=sms_time(text))


def fmt(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")
