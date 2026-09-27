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

# Ma'lumotlar Volume'ga yoziladi (Railway'da /data ga ulanadi)
ENV DATA_DIR=/data
VOLUME ["/data"]

CMD ["python", "bot.py"]
