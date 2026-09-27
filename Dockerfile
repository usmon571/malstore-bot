# Bot Playwright (Chromium) ishlatadi — shuning uchun Railway'ning odatiy
# quruvchisi emas, Docker kerak: brauzer va uning tizim kutubxonalari
# shu yerda o'rnatiladi.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TZ=Asia/Tashkent

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chromium + kerakli tizim kutubxonalari (versiya requirements.txt bilan bir xil)
RUN python -m playwright install --with-deps chromium

COPY . .

# Tovar va buyurtmalar PostgreSQL (Neon) da saqlanadi.
# Bu papkada faqat tarjima keshi turadi — u yo'qolsa ham muammo yo'q.
# (Railway Dockerfile'dagi VOLUME ko'rsatmasini qabul qilmaydi, shuning
# uchun doimiy disk kerak bo'lsa Railway Volume alohida ulanadi.)
ENV DATA_DIR=/app/data

CMD ["python", "bot.py"]
