"""Дашборд картинкой: рисуется для любых периодов и кошельков, в том числе пустых."""
import datetime as dt
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("EXPENSES_DB", os.path.join(tempfile.mkdtemp(), "test.db"))

from PIL import Image  # noqa: E402

import chart  # noqa: E402
import ledger  # noqa: E402


class Render(unittest.TestCase):
    def setUp(self):
        self._db, self._now = ledger.DB_PATH, ledger.now
        ledger.DB_PATH = Path(tempfile.mkdtemp()) / "ch.db"
        ledger.init()
        self.t = dt.datetime(2026, 11, 24, 18, 0)
        ledger.now = lambda: self.t

    def tearDown(self):
        ledger.DB_PATH, ledger.now = self._db, self._now

    def size(self, png: bytes):
        img = Image.open(io.BytesIO(png))
        self.assertEqual(img.format, "PNG")
        return img.size

    def test_empty(self):
        w, h = self.size(chart.render("month"))
        self.assertEqual(w, chart.W)
        self.assertLess(h, 800)

    def test_full(self):
        for i, (amount, cat, merchant) in enumerate([(150000, "Здоровье", "Клиника"), (65000, "Хотелки", "пазл"),
                                                     (45000, "Еда", "Korzinka " * 10), (12000, "Транспорт", "")]):
            ledger.add(amount, "exp", cat, merchant, ts=self.t - dt.timedelta(days=i * 3))
        ledger.add(500000, "exp", "Реклама", "Instagram", wallet="business", ts=self.t)
        ledger.add(3000000, "inc", "Доход", "зарплата", ts=self.t)
        ledger.budget_set("Еда", 2000000)
        ledger.card_seen("5789", "Humo", 1230000)
        ledger.card_seen("4521", "Uzcard", 50000)
        for period in ("today", "week", "month", "lastmonth", "all"):
            for wallet in (None, "personal", "business"):
                self.size(chart.render(period, wallet))
        _, h = self.size(chart.render("month"))
        self.assertGreater(h, 2000)          # все блоки на месте: дни, категории, магазины, лимиты, карты

    def test_words(self):
        self.assertEqual([chart._ops(n) for n in (1, 2, 5, 11, 21, 104)],
                         ["операция", "операции", "операций", "операций", "операция", "операции"])


if __name__ == "__main__":
    unittest.main()
