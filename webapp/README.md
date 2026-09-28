# Mini App (Telegram WebApp)

`index.html` — botdagi «🧮 Narxlarni hisoblash» tugmasi ochadigan sahifa.

## ⚠️ Bu papka o'z-o'zidan tarqatilmaydi

Bot sahifani `WEBAPP_URL` manzilidan ochadi, ya'ni
**https://usmon571.github.io/malstore/** — u esa alohida repo'da turadi:
**https://github.com/usmon571/malstore** (`main` branch, `index.html`).

Shu tufayli bu faylni o'zgartirish yetarli emas. O'zgartirgandan keyin
**o'sha repo'ga ham ko'chirish shart**:

```bash
git clone https://github.com/usmon571/malstore.git
cp malstore-bot/webapp/index.html malstore/index.html
cd malstore && git add index.html && git commit -m "Mini App yangilandi" && git push
```

GitHub Pages ~1 daqiqada tarqatadi.

Bir marta shu qadam tashlab ketilgani uchun xato chiqqan edi: bot mahsulot
ma'lumotini `{variants:[{label, weightKg}]}` ko'rinishida yubora boshlagan,
tarqatilgan sahifa esa eski `{sizes:[50,100]}` ko'rinishini kutardi. Natijada
`D.sizes.forEach()` `undefined` da yiqilib, butun skript to'xtagan va narx
kiritish maydonlari umuman chiqmagan.

## Bot nima yuboradi

`bot.py` → `build_webapp_url()` ma'lumotni URL-xavfsiz base64 ko'rinishida
`?d=` parametri orqali uzatadi (`#` qismini Telegram o'zi egallaydi):

| Maydon       | Izoh                                                            |
|--------------|-----------------------------------------------------------------|
| `productUrl` | Poizon havolasi — «📱 Poizon ilovasida ochish» tugmasi uchun     |
| `title`      | Tarjima qilingan nom                                            |
| `variants`   | `[{label, weightKg}]` — `weightKg` `null` bo'lsa vazn noma'lum  |
| `rate`       | CNY→UZS kursi (CBU)                                             |
| `kargoRate`  | Avto Kargo tarifi, so'm/kg                                      |
| `arrival`    | Yetkazib berish sanasi oralig'i                                 |
| `specs`      | Xarakteristikalar                                               |
| `channel`    | Kanal nomi (post oxirida)                                       |
| `brand`      | «100% ORIGINAL» yozuvi uchun belgi                              |

Sahifa eski `sizes` ko'rinishini ham tushunadi — shuning uchun eski
tarqatilgan nusxa bilan ham yiqilmaydi.

## Qaytariladigan ma'lumot

`tg.sendData()` orqali (4096 baytdan oshmasin):

```json
{ "title": "...", "prices": {"50 ml": "1 132 000 so'm"},
  "markup": "10%", "caption": "tayyor post matni" }
```

Botda buni `handle_webapp_data()` qabul qiladi.

## Local sinash

```bash
cd webapp && python -m http.server 8777
```

So'ng `bot.build_webapp_url()` bergan `?d=...` qismini
`http://127.0.0.1:8777/` ga qo'shib brauzerda ochish kifoya. Telegram
ichida bo'lmagani uchun faqat «Botga yuborish» ishlamaydi — qolgan
hisob-kitob va ko'rinish tekshirib ko'rish uchun yetarli.
