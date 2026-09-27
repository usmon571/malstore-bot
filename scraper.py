"""
Poizon (Dewu / 得物) mahsulot sahifasidan rasmlar va xarakteristikalarni oladi.

Mahsulot sahifalari login'siz ochiladi (Dewu'da web login umuman yo'q —
u faqat ilovada bor), shuning uchun sessiya saqlash talab qilinmaydi.

Qaytariladigan rasmlar allaqachon baytlar (bytes) ko'rinishida — sahifa bilan
bir xil brauzer sessiyasi orqali yuklab olinadi, shuning uchun Telegram'ga
to'g'ridan-to'g'ri URL sifatida berilganda hotlink-himoya tufayli yuklanmay
qolish muammosi bo'lmaydi.

Sarlavha va xarakteristikalar xitoy tilidan o'zbek tiliga tarjima qilinadi.
"""
from __future__ import annotations

import html
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from deep_translator import MyMemoryTranslator
from playwright.sync_api import sync_playwright

import settings

log = logging.getLogger("malstore-scraper")

# Tarjima limitini tejash uchun: bir marta tarjima qilingan matn shu faylda
# saqlanadi. Xarakteristika nomlari ("香调", "前调" ...) har bir mahsulotda
# takrorlangani uchun bu juda ko'p so'rovni tejaydi.
TRANSLATION_CACHE_FILE = settings.DATA_DIR / "translation_cache.json"

CJK_RE = re.compile(r"[一-鿿]")

# Takrorlanadigan atamalar — mashina tarjimasi bularni buzib yuboradi
# (masalan 中调 → "O'rta masofa"), shuning uchun qo'lda yozilgan.
SPEC_GLOSSARY = {
    "香调": "Xushbo'y hid turi",
    "前调": "Yuqori notalar",
    "中调": "O'rta notalar",
    "后调": "Asosiy notalar",
    "最明显气味": "Eng sezilarli hid",
    "版本": "Versiya",
    "主货号": "Model",
    "发售日期": "Chiqarilgan sana",
    "净含量": "Hajmi",
    "规格": "O'lchami",
    "产地": "Ishlab chiqarilgan joy",
    "品牌": "Brend",
    "材质": "Materiali",
    "颜色": "Rangi",
    "尺码": "O'lchami",
    # Poyabzal/kiyim uchun
    "主色": "Asosiy rang",
    "辅色": "Qo'shimcha rang",
    "功能分类": "Xususiyatlari",
    "鞋底材料": "Taglik materiali",
    "鞋头款式": "Burun shakli",
    "鞋跟类型": "Poshna turi",
    "适用季节": "Mavsum",
    "适用人群": "Kimga mo'ljallangan",
    "闭合方式": "Bog'lash usuli",
    # Ranglar — mashina tarjimasi bularni tez-tez buzadi
    # (masalan 黑色 → "Yoriq" deb tarjima qilgan edi)
    "黑色": "Qora",
    "白色": "Oq",
    "红色": "Qizil",
    "蓝色": "Ko'k",
    "绿色": "Yashil",
    "黄色": "Sariq",
    "灰色": "Kulrang",
    "粉色": "Pushti",
    "紫色": "Binafsha",
    "棕色": "Jigarrang",
    "咖啡色": "Jigarrang",
    "米色": "Bej",
    "银色": "Kumush",
    "金色": "Oltin",
    "橙色": "To'q sariq",
    "彩色": "Rang-barang",
}


def _load_translation_cache() -> dict[str, str]:
    try:
        return json.loads(TRANSLATION_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_translation_cache(cache: dict[str, str]) -> None:
    try:
        TRANSLATION_CACHE_FILE.write_text(
            json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception as e:
        log.warning("tarjima keshini saqlab bo'lmadi: %s", e)

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

POIZON_HOSTS = (
    "dewu.com",
    "poizon.com",
    "dw4.co",  # ulashish (share) qisqa linklari
)

ICON_KEYWORDS = ("icon", "logo", "avatar", "arrow", "btn_", "sprite", "placeholder")

# CDN URL'lardagi o'lcham prefiksi/suffiksini olib tashlab, bir xil rasmning
# turli o'lchamdagi nusxalarini bitta deb hisoblash uchun.
SIZE_SUFFIX_RE = re.compile(r"\.(\d+x\d+|\d+_\d+)(?=\.\w+$)")
SIZE_QUERY_RE = re.compile(r"[?&](x-oss-process|imageView2|imageMogr2)[^&]*")


@dataclass
class ProductData:
    title: str = ""
    title_zh: str = ""
    images: list[bytes] = field(default_factory=list)
    specs: dict[str, str] = field(default_factory=dict)
    price_cny: str = ""
    final_url: str = ""
    ok: bool = False
    error: str = ""


def is_poizon_link(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    return any(h in host for h in POIZON_HOSTS)


URL_RE = re.compile(r"https?://\S+")


def extract_poizon_url(text: str) -> str | None:
    """Matndan Poizon linkini topib qaytaradi — masalan, "得物"ning to'liq
    ulashish (share) xabari kabi link atrofida boshqa matn bo'lsa ham ishlaydi."""
    for match in URL_RE.finditer(text):
        candidate = match.group(0).rstrip("，,。.!！?？、）)】\"'")
        if is_poizon_link(candidate):
            return candidate
    return None


def _dedupe_key(url: str) -> str:
    key = url.split("?")[0] if "x-oss-process" not in url and "imageView2" not in url else SIZE_QUERY_RE.sub("", url)
    key = SIZE_SUFFIX_RE.sub("", key)
    return key


def _looks_like_icon(url: str) -> bool:
    low = url.lower()
    return any(k in low for k in ICON_KEYWORDS)


def _collect_gallery_urls(page, max_images: int) -> list[str]:
    # 1) Dewu'ga xos: mahsulot galereyasi (carousel) rasmlari — aniqroq va
    # ikonka/rasm-bo'lmagan elementlardan xoli natija beradi.
    try:
        specific = page.eval_on_selector_all(
            "[class*='carouselImg' i]",
            "els => els.map(e => e.currentSrc || e.src || '').filter(u => u.startsWith('http'))",
        )
        seen: dict[str, str] = {}
        for u in specific:
            key = _dedupe_key(u)
            if key not in seen or len(u) > len(seen[key]):
                seen[key] = u
        if seen:
            return list(seen.values())[:max_images]
    except Exception:
        pass

    # 2) Umumiy fallback: sahifadagi barcha <img>'larni skanerlash
    # (Dewu maxsus selektori topilmasa yoki boshqa sahifa turi bo'lsa).
    raw = page.eval_on_selector_all(
        "img",
        """els => els.map(e => ({
            src: e.currentSrc || e.src || '',
            dataSrc: e.getAttribute('data-src') || e.getAttribute('data-original') || e.getAttribute('data-lazy-src') || '',
            w: e.naturalWidth || e.width || 0,
        }))""",
    )
    candidates: list[str] = []
    try:
        og_image = page.eval_on_selector("meta[property='og:image']", "el => el.content")
        if og_image:
            candidates.append(og_image)
    except Exception:
        pass

    for item in raw:
        for u in (item.get("src"), item.get("dataSrc")):
            if u and u.startswith("http") and not _looks_like_icon(u):
                candidates.append(u)

    seen_keys: dict[str, str] = {}
    for u in candidates:
        key = _dedupe_key(u)
        # bir xil rasmning bir nechta o'lchamidan eng "kattasi"ni saqlaymiz
        # (URL uzunroq/parametrliroq bo'lgani ko'pincha asl sifatli variant)
        if key not in seen_keys or len(u) > len(seen_keys[key]):
            seen_keys[key] = u

    return list(seen_keys.values())[:max_images]


def _collect_specs(page) -> dict[str, str]:
    specs: dict[str, str] = {}

    # 1) Dewu'ga xos: "参数" (parametrlar) jadvalidagi itemTitle/itemValue
    # juftliklari — eng aniq natija beradi.
    try:
        pairs = page.eval_on_selector_all(
            "[class*='itemTitle' i]",
            """els => els.map(e => {
                const parent = e.parentElement;
                const valueEl = parent ? parent.querySelector('[class*="itemValue" i]') : null;
                return [e.innerText ? e.innerText.trim() : '', valueEl && valueEl.innerText ? valueEl.innerText.trim() : ''];
            })""",
        )
        for k, v in pairs:
            k, v = (k or "").strip(), (v or "").strip()
            if k and v and len(k) < 30 and len(v) < 200:
                specs[k] = v
    except Exception:
        pass
    if specs:
        return specs

    # 2) Umumiy fallback: "key: value" ko'rinishidagi qatorlar
    try:
        rows = page.eval_on_selector_all(
            "[class*='param'], [class*='spec'], [class*='attr'], [class*='detail'] li, "
            "[class*='detail'] tr, [class*='property']",
            "els => els.map(e => e.innerText).filter(t => t)",
        )
        for row in rows[:60]:
            for line in row.split("\n"):
                if ":" in line or "：" in line:
                    k, _, v = re.split("[:：]", line, maxsplit=1) if ("：" in line) else line.partition(":")
                    k, v = k.strip(), v.strip()
                    if k and v and len(k) < 30 and len(v) < 120:
                        specs[k] = v
    except Exception:
        pass

    # 3) O'lcham/rang kabi tanlov chiplari (masalan SKU variantlari)
    try:
        sku_texts = page.eval_on_selector_all(
            "[class*='sku'] [class*='item'], [class*='sku-value'], [class*='spec-value']",
            "els => els.map(e => e.innerText.trim()).filter(t => t && t.length < 20)",
        )
        sizes = sorted(set(t for t in sku_texts if t))
        if sizes:
            specs["O'lcham/variant"] = ", ".join(sizes)
    except Exception:
        pass

    return specs


def _extract_title(page) -> str:
    # Dewu'da <title> va og:title umumiy ("商品详情" — "Mahsulot tafsilotlari")
    # bo'lgani uchun sahifadagi eng uzun "title" klassli matndan foydalanamiz —
    # mahsulot nomi doim boshqa bo'lim sarlavhalaridan (masalan "参数", "服务")
    # ancha uzunroq bo'ladi.
    try:
        candidates = page.eval_on_selector_all(
            "[class*='title' i]",
            "els => els.map(e => e.innerText ? e.innerText.trim() : '')",
        )
        candidates = [c for c in candidates if c]
        if candidates:
            best = max(candidates, key=len)
            if len(best) >= 10:
                return best
    except Exception:
        pass

    try:
        og_title = page.eval_on_selector("meta[property='og:title']", "el => el.content")
        if og_title:
            return og_title.strip()
    except Exception:
        pass

    try:
        return (page.title() or "").strip()
    except Exception:
        return ""


def fetch_product(url: str, max_images: int = 12, timeout_ms: int = 30000) -> ProductData:
    result = ProductData(final_url=url)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(
                user_agent=MOBILE_UA, viewport={"width": 390, "height": 844}
            )
            page = context.new_page()
            try:
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                result.final_url = page.url

                result.title_zh = _extract_title(page)

                image_urls = _collect_gallery_urls(page, max_images)
                result.specs = _collect_specs(page)

                # Poizon narxi (发售价格) kanalga chiqmaydi — uni alohida
                # ajratib olamiz, keyin bot undan so'mdagi narxni hisoblaydi.
                for key in list(result.specs):
                    if "价格" in key:
                        result.price_cny = result.specs.pop(key)

                # rasmlarni bayt sifatida, sahifaning o'z sessiyasi (cookie/referer) bilan yuklaymiz.
                # Dewu CDN'i ba'zan katta rasmlarni sekin uzatadi — shuning uchun
                # timeout kattaroq va bitta marta qayta urinish qo'shilgan.
                images: list[bytes] = []
                for img_url in image_urls:
                    for attempt in range(2):
                        try:
                            resp = context.request.get(
                                img_url,
                                headers={"Referer": result.final_url, "User-Agent": MOBILE_UA},
                                timeout=30000,
                            )
                            if resp.ok:
                                images.append(resp.body())
                            break
                        except Exception:
                            if attempt == 1:
                                break
                            continue
                result.images = images
            finally:
                browser.close()
    except Exception as e:
        result.error = str(e)
        result.ok = False
        return result

    # Google'ning bepul tarjima nuqtasi (deep_translator orqali) bloklangan —
    # har bir so'rov "TooManyRequests" qaytaradi. MyMemory esa ishlaydi,
    # lekin til kodi "uz" emas, "uz-UZ" bo'lishi shart.
    translator = MyMemoryTranslator(source="zh-CN", target="uz-UZ")
    cache = _load_translation_cache()

    def _translate(text: str) -> str:
        # Xitoycha belgi bo'lmasa (masalan "¥770", "50ml") tarjima shart emas —
        # bu ham limitni tejaydi, ham matnni buzilishdan saqlaydi.
        if not CJK_RE.search(text):
            return text
        if text in SPEC_GLOSSARY:
            return SPEC_GLOSSARY[text]
        if text in cache:
            return cache[text]

        time.sleep(0.3)
        try:
            out = translator.translate(text)
        except Exception as e:
            log.warning("tarjima ishlamadi (%s): %s", type(e).__name__, e)
            return text
        if not out or "MYMEMORY WARNING" in out.upper():
            log.warning("tarjima limiti tugagan bo'lishi mumkin: %s", out)
            return text
        # MyMemory javobida HTML belgilari bo'ladi (masalan &#39; → ')
        translated_text = html.unescape(out)
        cache[text] = translated_text
        return translated_text

    if result.title_zh:
        result.title = _translate(result.title_zh)
    if result.specs:
        translated: dict[str, str] = {}
        for k, v in result.specs.items():
            tk = _translate(k)
            tv = _translate(v)
            translated[tk or k] = tv or v
        result.specs = translated

    _save_translation_cache(cache)

    result.ok = bool(result.title or result.images)
    return result
