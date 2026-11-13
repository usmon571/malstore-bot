# MalStore — sayt

`index.html` — bitta fayldan iborat sayt (build kerak emas): landing sahna
animatsiyalari, mahsulotlar katalogi va **saytning o'zida buyurtma berish**.

## Bitta baza

Sayt alohida bazaga ega emas. Bot bilan bitta jarayonda kichik server
ishlaydi (`../api.py`) — u saytni ham tarqatadi, va botning **o'z**
saqlagichidan (`store.json` yoki Neon PostgreSQL) o'qiydi va yozadi:

```
             ┌──────────── bot jarayoni (bot.py) ────────────┐
sayt ──HTTP──▶ api.py ──▶ store.py ──▶ store.json / Neon      │
             │    │                       ▲                    │
             │    └─ yangi buyurtma, chek ─┼─▶ Telegram: adminga│
             │                       bot ──┘   (holat tugmalari)│
             └───────────────────────────────────────────────┘
```

- Bot kanalga tovar joylasa yoki `/ochirish` qilsa — saytda ~30 soniyada ko'rinadi.
- Saytdan buyurtma kelsa — adminga Telegram'da **🌐 Saytdan yangi buyurtma #N**
  xabari keladi, bot buyurtmasi bilan bir xil holat tugmalari bilan.
  `/buyurtmalar` ro'yxatida ham ko'rinadi (🌐 sayt belgisi bilan).
- Admin holatni o'zgartirsa (to'lov tasdiqlandi, yo'lda, keldi...) — mijoz
  uni saytdagi «Buyurtmalarim»da ko'radi.

## Buyurtma jarayoni (saytda)

1. Mahsulot → variant (Avto kargo / Aviapochta / 50 ml ...) → **Buyurtma berish**.
2. Razmer (atirdan boshqa hamma narsada; poyabzalda o'lchash qo'llanmasi bilan),
   ism, telefon.
3. Oldindan to'lov va karta raqami chiqadi — **botdagi qoida bilan aynan bir xil**
   hisoblanadi (tannarx bo'lsa tannarx, bo'lmasa narxning `prepay_share` ulushi).
4. Mijoz chek rasmini (yoki PDF) yuklaydi → adminga Telegram'da rasm bilan keladi.
   Katta telefon rasmlari yuborishdan oldin brauzerda kichraytiriladi.
5. «Telegram'da kuzatish» — buyurtmani mijozning Telegram'iga ulaydi: holat
   xabarlari botdan ham keladi, chekni bot orqali ham yuborish mumkin.

Mijozning buyurtmalari brauzerida eslab qolinadi (sarlavhadagi «Buyurtmalarim»).
Buyurtmani faqat egasi ko'radi — har biriga alohida kalit (token) beriladi.

## API

| So'rov | Vazifasi |
|---|---|
| `GET /api/products` | tovarlar (tannarx va Poizon havolasi berilmaydi) |
| `GET /api/products/101/images/0` | rasm — Telegram'dan, bot tokeni yashirin |
| `POST /api/orders` | buyurtma: `{product_id, variant, size, name, phone}` |
| `GET /api/orders/7?t=TOKEN` | buyurtma holati (faqat egasiga) |
| `POST /api/orders/7/receipt?t=TOKEN` | chek (rasm yoki PDF, ≤10 MB) |

Spamdan himoya: bitta IP 10 daqiqada 5 tadan ko'p buyurtma bera olmaydi,
yashirin "robot tuzog'i" maydoni bor.

## Ishga tushirish

Hech narsa sozlash shart emas: bot ishlaganda sayt ham ishlaydi —
**http://localhost:8081** (shu kompyuterda). Port `config.json` dagi
`"api_port"` bilan o'zgaradi, `0` — sayt o'chiq. Port band bo'lsa bot
baribir ishlaydi, faqat sayt o'chiq qoladi (`bot.log` da yoziladi).

Ishlab turgan botni yangi kodga o'tkazish — `update.bat` (config, baza
va kutubxonalarga tegmaydi).

Botda `/rasmlar` buyrug'ini bir marta yuboring — eski tovarlarning rasmlari
saytga tiklanadi. Yangi tovarlarda rasm o'zi saqlanadi.

## Internetga chiqarish

Kompyuterdagi sayt tashqaridan ko'rinishi uchun unga ochiq HTTPS manzil
kerak (uy kompyuterida ochiq IP bo'lmaydi). Bepul yo'llar: Cloudflare Tunnel
yoki Tailscale Funnel — ular `http://localhost:8081` ni internetga chiqaradi.
Sayt shu manzilda to'liq ishlaydi (API bilan bitta manzilda — qo'shimcha
sozlama kerak emas).

Saytni boshqa joyda (masalan GitHub Pages) saqlamoqchi bo'lsangiz,
`index.html` dagi `CONFIG.api` ga bot manzilini yozing va
`config.json` ga `"site_origin": "https://usmon571.github.io"` qo'shing.

## Lokal sinov

Sayt bot bilan bitta manzilda ochiladi (http://localhost:8081). Faqat
dizayn ustida ishlash uchun alohida statik server ham bo'ladi:

```bash
py -m http.server 8790 --directory site
```

U holda sayt botni `http://localhost:8081` dan qidiradi; boshqa manzil:
`http://localhost:8790/?api=https://<manzil>`.

⚠️ Bitta token bilan ikki bot ishlamasin — Telegram xato beradi.
