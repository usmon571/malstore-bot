"""SMS банков с оплатой через приложения: Alliance Pay (Asia Alliance Bank), Payme, Click."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import parsing as p  # noqa: E402


class Apps(unittest.TestCase):
    def check(self, text, amount, kind="exp", merchant=None, cat=None):
        r = p.parse_bank(text)
        self.assertIsNotNone(r, text)
        self.assertEqual((r.amount, r.kind), (amount, kind), text)
        if merchant is not None:
            self.assertEqual(r.merchant, merchant, text)
        if cat:
            self.assertEqual(r.category, cat, text)
        self.assertTrue(p.looks_like_bank(text), text)

    def test_payme_prefix_reveals_shop(self):
        self.check("HUMOCARD *4521: oplata 86 500.00 UZS; PAYME*KORZINKA; 22.11.26 14:30; balans: 1 250 000.00 UZS",
                   86500, merchant="KORZINKA", cat="Еда")

    def test_payme_only(self):
        self.check("UZCARD *7788 Pokupka: 15 000,00 UZS PAYCOM 22.11.2026 10:05 Dostupno: 340 000,00 UZS",
                   15000, merchant="Payme")

    def test_click_prefix(self):
        self.check("Karta *1234 To'lov 45 000 so'm CLICK*EVOS 21.11.26 13:10 Qoldiq: 900 000 so'm",
                   45000, merchant="EVOS", cat="Кафе и рестораны")

    def test_alliance_uzbek_expense(self):
        self.check("AAB: HUMO *0912 hisobdan 120 000.00 UZS yechildi. ALLIANCE PAY: Safia. Balans 2 000 000.00 UZS",
                   120000, merchant="Safia", cat="Кафе и рестораны")

    def test_alliance_income(self):
        self.check("AAB: HUMO *0912 hisobga 3 500 000.00 UZS tushdi. Balans: 5 500 000.00 UZS", 3500000, kind="inc")

    def test_manual_not_bank(self):
        self.assertFalse(p.looks_like_bank("15000 uzum"))


if __name__ == "__main__":
    unittest.main()
