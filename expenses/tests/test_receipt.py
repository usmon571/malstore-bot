"""Тесты чеков soliq.uz (без сети): python -m unittest discover expenses/tests"""
import io
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("EXPENSES_DB", os.path.join(tempfile.mkdtemp(), "test.db"))

import core  # noqa: E402
import ledger  # noqa: E402
import receipt  # noqa: E402

LINK = "https://ofd.soliq.uz/check?t=EP000000000123&r=4567&c=20261119143005&s=123456789012"

# так API отдаёт поле data (имена полей — как на странице чека ofd.soliq.uz)
PAYLOAD = {
    "isRefund": 0,
    "cardTotal": "86500.00",
    "cashTotal": 0,
    "kkmName": "KASSA 1",
    "extraInfo": {"companyName": "\"KORZINKA\" MChJ", "address": "Toshkent sh., Chilonzor"},
    "paymentDetails": [
        {"id": 1, "name": "Non", "amount": 2, "price": "8000.00"},
        {"id": 2, "name": "Sut 1L", "amount": 1, "price": "14500.00"},
        {"id": 3, "name": "Go'sht", "amount": 1.25, "price": "64000.00"},
    ],
}


def fake_fetch(payload=PAYLOAD):
    return lambda ref: receipt.parse_payload(payload)


class Links(unittest.TestCase):
    def test_from_link(self):
        r = receipt.from_link(LINK)
        self.assertEqual((r.t, r.r, r.s, r.kind), ("EP000000000123", "4567", "123456789012", "CHECK"))
        self.assertEqual(r.when, datetime(2026, 11, 19, 14, 30, 5))

    def test_find_in_text(self):
        self.assertEqual(receipt.find_link(f"вот чек: {LINK}."), LINK)
        self.assertIsNone(receipt.find_link("15000 такси"))

    def test_epi_and_bad(self):
        self.assertEqual(receipt.from_link(LINK.replace("/check", "/epi")).kind, "CHECK_EPI")
        self.assertIsNone(receipt.from_link("https://ofd.soliq.uz/check?t=1&r=2"))      # нет даты
        self.assertIsNone(receipt.from_link("https://example.com/check?t=1&r=2&c=20261119143005"))


class Payload(unittest.TestCase):
    def test_parse(self):
        rc = receipt.parse_payload(PAYLOAD)
        self.assertEqual(rc.total, 86500)
        self.assertEqual(rc.merchant, "KORZINKA")
        self.assertEqual(len(rc.items), 3)
        p = receipt.to_parsed(rc)
        self.assertEqual((p.amount, p.kind, p.category), (86500, "exp", "Еда"))
        self.assertTrue(p.note.startswith("🧾 Non ×2"))

    def test_total_from_items_and_refund(self):
        rc = receipt.parse_payload({**PAYLOAD, "cardTotal": 0, "isRefund": 1})
        self.assertEqual(rc.total, 86500)
        self.assertEqual(receipt.to_parsed(rc).kind, "inc")

    def test_clean_name(self):
        self.assertEqual(receipt.clean_name("OOO «Safia»"), "Safia")
        self.assertEqual(receipt.clean_name("EVOS MAS'ULIYATI CHEKLANGAN JAMIYAT"), "EVOS")


class QR(unittest.TestCase):
    def test_decode_photo(self):
        try:
            import zxingcpp
            from PIL import Image
        except ImportError:
            self.skipTest("zxing-cpp/pillow не установлены")
        mv = memoryview(zxingcpp.create_barcode(LINK, zxingcpp.BarcodeFormat.QRCode).to_image(scale=5))
        img = Image.frombytes("L", (mv.shape[1], mv.shape[0]), mv.tobytes())
        canvas = Image.new("L", (900, 1200), 235)          # «чек» с QR внизу
        canvas.paste(img.rotate(8, expand=True, fillcolor=235), (300, 800))
        buf = io.BytesIO()
        canvas.save(buf, "JPEG", quality=70)
        self.assertIn(LINK, receipt.decode_qr(buf.getvalue()))


class Flow(unittest.TestCase):
    def setUp(self):
        self._db = ledger.DB_PATH
        ledger.DB_PATH = Path(tempfile.mkdtemp()) / "r.db"
        ledger.init()
        self._notify, core.notify = core.notify, lambda *a, **k: None

    def tearDown(self):
        ledger.DB_PATH = self._db
        core.notify = self._notify

    def test_new_then_already(self):
        ref = receipt.from_link(LINK)
        status, row, rc = core.process_receipt(ref, fake_fetch())
        self.assertEqual(status, "new")
        self.assertEqual((row["amount"], row["merchant"], row["source"]), (86500, "KORZINKA", "receipt"))
        self.assertEqual(row["ts"], "2026-11-19 14:30:05")      # время с чека, а не время скана
        self.assertIn("Sut 1L", core.receipt_message(status, row, rc))
        status2, row2, _ = core.process_receipt(ref, fake_fetch())
        self.assertEqual((status2, row2["id"]), ("already", row["id"]))

    def test_links_to_apple_pay(self):
        ts = datetime(2026, 11, 19, 14, 31)
        tx = ledger.add(86500, "exp", "Прочее", "", "", "wallet", "", ts=ts)
        status, row, _ = core.process_receipt(receipt.from_link(LINK), fake_fetch())
        self.assertEqual((status, row["id"]), ("linked", tx))
        self.assertEqual((row["merchant"], row["category"]), ("KORZINKA", "Еда"))
        self.assertTrue(row["note"].startswith("🧾"))
        with ledger._conn() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM tx").fetchone()[0], 1)

    def test_no_link_when_far_or_other_amount(self):
        ledger.add(86500, "exp", "Прочее", "", "", "sms", "", ts=datetime(2026, 11, 19, 14, 30) - timedelta(hours=5))
        ledger.add(86000, "exp", "Прочее", "", "", "sms", "", ts=datetime(2026, 11, 19, 14, 30))
        status, _, _ = core.process_receipt(receipt.from_link(LINK), fake_fetch())
        self.assertEqual(status, "new")

    def test_pending(self):
        def pending(ref):
            raise receipt.ReceiptPending("Chek shakllanmoqda")
        with self.assertRaises(receipt.ReceiptPending):
            core.process_receipt(receipt.from_link(LINK), pending)


if __name__ == "__main__":
    unittest.main()
