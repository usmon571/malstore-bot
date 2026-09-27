r"""
store.json dagi tovar va buyurtmalarni PostgreSQL (Neon) ga ko'chiradi.

Ishga tushirish (PowerShell):
    $env:DATABASE_URL="postgresql://...neon.tech/...?sslmode=require"
    .\venv\Scripts\python.exe migrate_to_db.py

Bir necha marta ishga tushirsa ham xavfsiz: mavjud yozuvlar ustiga
yozilmaydi, faqat yetishmayotganlari qo'shiladi.
"""
import json
import os
import sys
from pathlib import Path

DSN = os.getenv("DATABASE_URL", "").strip()
if not DSN:
    sys.exit("DATABASE_URL berilmagan. Avval uni o'rnating.")

import db

SOURCE = Path(__file__).parent / "store.json"
if not SOURCE.exists():
    sys.exit(f"{SOURCE} topilmadi — ko'chiriladigan ma'lumot yo'q.")

data = json.loads(SOURCE.read_text(encoding="utf-8"))
db.init(DSN)

products = data.get("products", {})
orders = data.get("orders", [])
admins = data.get("admins", [])

print(f"Manbada: {len(products)} tovar, {len(orders)} buyurtma, {len(admins)} admin")

added_p = 0
for pid, p in products.items():
    if db.get_product(DSN, pid):
        print(f"  tovar {pid} — allaqachon bor, o'tkazib yuborildi")
        continue
    db.save_product(
        DSN, pid, p.get("url", ""), p.get("title", ""), p.get("prices", {}),
        p.get("arrival", ""), p.get("caption", ""), p.get("message_ids", []),
    )
    added_p += 1

added_o = 0
with db._cursor(DSN) as cur:
    for o in orders:
        cur.execute("SELECT 1 FROM orders WHERE no = %s", (o["no"],))
        if cur.fetchone():
            print(f"  buyurtma #{o['no']} — allaqachon bor")
            continue
        cur.execute(
            """INSERT INTO orders (no, product_id, variant, price, prepay, status,
                                   receipt, user_id, username, name, phone, created)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (o["no"], o.get("product_id"), o.get("variant", ""), o.get("price", ""),
             o.get("prepay", ""), o.get("status", "to'lov kutilmoqda"), o.get("receipt"),
             o.get("user_id"), o.get("username"), o.get("name"), o.get("phone", ""),
             o.get("created", "")),
        )
        added_o += 1

for admin_id in admins:
    db.remember_admin(DSN, admin_id)

# Tovar raqamlari hisoblagichi eng katta ID dan past qolmasin
if products:
    biggest = max(int(pid) for pid in products if str(pid).isdigit())
    with db._cursor(DSN) as cur:
        cur.execute(
            "UPDATE counters SET value = GREATEST(value, %s) WHERE name = 'product'",
            (biggest,),
        )

print(f"\nKo'chirildi: {added_p} tovar, {added_o} buyurtma, {len(admins)} admin")
print(f"Bazada hozir: {db.product_count(DSN)} tovar, "
      f"{len(db.recent_orders(DSN, 1000))} buyurtma")
print("\nTayyor. Endi DATABASE_URL ni Railway > Variables ga qo'shing.")
