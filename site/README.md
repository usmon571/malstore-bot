# MalStore — kirish sayti

`index.html` — bitta fayldan iborat landing sahifa (build kerak emas).
Videodagi uslubda, scroll bilan boshqariladigan sahnalar:

1. **Globus** — Xitoydan Toshkentga yo'nalishlar; scroll'da ufqqa aylanadi
2. **Biz haqimizda** — 100% original, 3 yetkazish turi, 2–7 kun
3. **Ombor** — kran MALSTORE konteynerini mashinaga yuklaydi, mashina Toshkentga yo'l oladi
4. **Xizmatlar** — aviapochta / avto kargo / avto pochta, Poizon tekshiruvi
5. **Bosqichlar** — quti 5 bosqichdan o'tadi (tanlash → qo'lingizda)
6. **Osmon** — bulutlar orasidan samolyot, "Siz uchun tinimsiz ishlaymiz"
7. **Mahsulotlar** — botdagi tovarlar jonli ro'yxati (rasm, narx, variantlar, buyurtma)
8. **Buyurtma** — Telegram kanal / bot tugmalari

Kutubxonalar CDN'dan: GSAP + ScrollTrigger (cdnjs), Lenis (unpkg), shrift — Google Fonts.
Globus xaritasi faylning ichiga joylangan (tashqi so'rov yo'q).

## Mahsulotlar qayerdan keladi

Bot bilan bitta jarayonda kichik API ishlaydi (`../api.py`). U bot ishlatadigan
saqlagichdan (Neon PostgreSQL yoki `store.json`) o'qiydi, shuning uchun bot
kanalga tovar joylasa yoki `/ochirish` qilsa, saytda ~30 soniyada ko'rinadi.

```
sayt  ──GET /api/products──▶  bot servisi (Railway)  ──▶  baza
      ◀── rasm ────────────   (Telegram'dan, token yashirin)
```

Tashqariga faqat ommaviy ma'lumot chiqadi: nom, narxlar, xususiyatlar, rasmlar,
buyurtma havolasi. Tannarx, Poizon havolasi va buyurtmalar berilmaydi.

### Ishga tushirish (bir marta)

1. Railway → bot servisi → **Settings → Networking → Generate Domain**.
   Railway `PORT`ni o'zi beradi, API shu portda ochiladi.
2. Shu domenni `index.html` dagi `CONFIG.api` ga yozing:
   ```js
   const CONFIG = {
     api: "https://malstore-bot-production.up.railway.app",
     ...
   ```
3. (Ixtiyoriy) Railway Variables: `SITE_ORIGIN=https://usmon571.github.io` —
   shunda API'ni faqat sizning saytingiz o'qiy oladi.
4. Botda `/rasmlar` buyrug'ini bir marta yuboring — eski tovarlarning rasmlari
   tiklanadi (avval rasmlar saqlanmagan edi). Yangi tovarlarda rasm o'zi saqlanadi.

Tekshirish: `https://<domen>/api/products` brauzerda JSON qaytarishi kerak.

API sozlanmagan yoki ishlamay qolsa, bo'limda "Mahsulotlar kanalda" degan
tugma chiqadi — sahifa buzilmaydi.

## Sozlash

`index.html` dagi `LINKS` obyekti — kanal havolasi. Bot havolasi API'dan
avtomatik olinadi; API'siz ishlatsangiz `bot` ga yozing:

```js
const LINKS = {
  channel: "https://t.me/malstoreoriginal",
  bot: "",   // bo'sh qolsa — API'dan yoki kanal
};
```

## Local ko'rish

```bash
py -m http.server 8790 --directory site
```

So'ng http://localhost:8790 ni oching. `localhost`da sayt API'ni
`http://localhost:8081` dan qidiradi; boshqa manzil uchun:
`http://localhost:8790/?api=https://<domen>`.

API'ni kompyuterda yoqish: `API_PORT=8081` bilan botni ishga tushiring.
⚠️ Railway'dagi bot ishlab turganda kompyuterda botni ishga tushirmang — bitta
token bilan ikki bot polling qilsa Telegram xato beradi.

## Joylash (GitHub Pages)

Mini App `usmon571/malstore` repo'sining ildizida turadi, shuning uchun saytni
alohida repo'ga (yoki shu repo'dagi `site/` papkaga) qo'ying va Pages'ni yoqing.
