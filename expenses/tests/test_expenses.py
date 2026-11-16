"""Тесты: python -m unittest discover expenses/tests"""
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ["EXPENSES_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
try:
    import requests  # noqa: F401
except ImportError:  # тесты не требуют сети
    sys.modules["requests"] = types.ModuleType("requests")

import core  # noqa: E402
import ledger  # noqa: E402
import parsing as p  # noqa: E402


class Manual(unittest.TestCase):
    def check(self, text, amount, kind="exp", cat=None, wallet="personal", days=0):
        r = p.parse_manual(text)
        self.assertIsNotNone(r, text)
        self.assertEqual(r.amount, amount, text)
        self.assertEqual(r.kind, kind, text)
        if cat:
            self.assertEqual(r.category, cat, text)
        self.assertEqual(r.wallet, wallet, text)
        self.assertEqual(r.days_ago, days, text)

    def test_basic(self):
        self.check("15000 такси", 15000, cat="Транспорт")
        self.check("45к обед", 45000, cat="Кафе и рестораны")
        self.check("кофе 12.5к", 12500)
        self.check("1 500 000 аренда", 1500000, cat="Коммуналка и жильё")
        self.check("15 000 корзинка 2", 15000, cat="Еда")
        self.check("1.2 млн аренда", 1200000)

    def test_income_and_flags(self):
        self.check("+3 млн зарплата", 3000000, kind="inc", cat="Доход")
        self.check("вчера 20000 бензин", 20000, cat="Транспорт", days=1)
        self.check("500000 карго", 500000, cat="Доставка и карго", wallet="business")
        self.check("200000 подарок #б", 200000, wallet="business")

    def test_no_amount(self):
        self.assertIsNone(p.parse_manual("привет"))


class Bank(unittest.TestCase):
    def check(self, text, amount, kind="exp", merchant=None, cat=None):
        r = p.parse_bank(text, 12500)
        self.assertIsNotNone(r, text)
        self.assertEqual(r.amount, amount, text)
        self.assertEqual(r.kind, kind, text)
        if merchant is not None:
            self.assertEqual(r.merchant, merchant, text)
        if cat:
            self.assertEqual(r.category, cat, text)

    def test_humo(self):
        self.check("🔴 Pokupka\n➖ 50.000,00 UZS\n📍 KORZINKA CHILANZAR\n💳 HUMO-CARD *1234\n🕓 16:45 16.11.2026\n"
                   "💰 1.234.567,89 UZS", 50000, merchant="KORZINKA CHILANZAR", cat="Еда")

    def test_uzcard_one_line(self):
        self.check("💸 Oplata ➖ 25 000,00 UZS 📍 OOO YANDEX GO 💳 ***1234 🕓 12:30 16.11.26 💵 123 456,78 UZS",
                   25000, merchant="YANDEX GO", cat="Транспорт")

    def test_income(self):
        self.check("🟢 Popolnenie\n➕ 3 000 000,00 UZS\n💳 HUMO *1234\n💰 3 100 000,00 UZS", 3000000, kind="inc")

    def test_visa_ru(self):
        self.check("Списание 120 000.00 UZS. Карта Visa *4242. MAKRO. Доступно: 900 000 UZS", 120000,
                   merchant="MAKRO", cat="Еда")
        self.check("Purchase $5.99 at NETFLIX.COM Visa *4242", 74875, merchant="NETFLIX.COM", cat="Подписки")

    def test_balance_first(self):
        self.check("Balans: 2 000 000 UZS. Pokupka: 30 000 UZS, EVOS", 30000, merchant="EVOS")

    def test_shortcuts(self):
        self.check("Оплата: Starbucks, UZS 45,000.00", 45000, merchant="Starbucks")
        self.check("Оплата: Korzinka, 15 000,00", 15000, merchant="Korzinka")
        self.check("Оплата: Korzinka 24\n15 000,00 сўм", 15000, merchant="Korzinka 24")
        self.check("Оплата: Safia\n38 000 UZS", 38000, merchant="Safia", cat="Кафе и рестораны")


class Storage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ledger.init()

    def test_flow(self):
        row, dup = core.record(p.parse_manual("15000 такси"), "manual")
        self.assertFalse(dup)
        row2, _ = core.record(p.parse_bank("Оплата: Starbucks, UZS 45,000.00"), "wallet")
        _, dup = core.record(p.parse_bank("Pokupka: 45 000.00 UZS, STARBUCKS"), "sms")
        self.assertTrue(dup, "Apple Pay + SMS одной покупки")
        # исправили категорию и кошелёк → запомнилось
        ledger.set_category(row2["id"], "Сервисы бизнеса", "business")
        row3, _ = core.record(p.parse_bank("Оплата: Starbucks, UZS 10,000.00"), "wallet")
        self.assertEqual((row3["category"], row3["wallet"]), ("Сервисы бизнеса", "business"))
        s = ledger.summary("today")
        self.assertEqual(s["expense"], 15000 + 45000 + 10000)
        self.assertEqual(ledger.summary("today", "business")["expense"], 55000)
        self.assertIn("personal", s["wallets"])
        ledger.budget_set("Транспорт", 16000)
        self.assertIn("93%", core.budget_warning("Транспорт"))
        self.assertTrue(ledger.backup().exists())
        self.assertIn("такси", ledger.export_csv("today"))

    def test_ranges(self):
        s, e = ledger.period_range("lastmonth", datetime(2026, 3, 15))
        self.assertEqual((s.date().isoformat(), e.date().isoformat()), ("2026-02-01", "2026-03-01"))
        s, e = ledger.period_range("month", datetime(2026, 12, 31))
        self.assertEqual(e.date().isoformat(), "2027-01-01")


if __name__ == "__main__":
    unittest.main()
