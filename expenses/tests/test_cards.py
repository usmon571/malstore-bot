"""Свои карты: остаток из SMS и переводы между своими картами (не расход)."""
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("EXPENSES_DB", os.path.join(tempfile.mkdtemp(), "test.db"))

import core  # noqa: E402
import ledger  # noqa: E402
import parsing as p  # noqa: E402

BUY = "HUMOCARD *4521: oplata 86 500.00 UZS; KORZINKA; balans: 1 250 000.00 UZS"
OUT = "HUMOCARD *4521: Perevod 500 000.00 UZS na kartu 8600****7788. Balans: 750 000.00 UZS"
IN = "UZCARD *7788: Popolnenie 500 000.00 UZS. Balans: 900 000.00 UZS"


class Parse(unittest.TestCase):
    def test_card_brand_balance(self):
        r = p.parse_bank(BUY)
        self.assertEqual((r.cards, r.brand, r.balance), (("4521",), "Humo", 1250000))
        self.assertEqual(p.cards_in("860012******7788 и *4521"), ("7788", "4521"))
        self.assertEqual(p.brand_in("Mirzo Ulug'bek, Visa *1111"), "Visa")   # «Mirzo» — не карта Mir
        self.assertIsNone(p.balance_in("oplata 5 000 UZS"))


class Flow(unittest.TestCase):
    def setUp(self):
        self._db = ledger.DB_PATH
        ledger.DB_PATH = Path(tempfile.mkdtemp()) / "c.db"
        ledger.init()
        self.t = dt.datetime(2026, 11, 24, 12, 0)
        self._now = ledger.now
        ledger.now = lambda: self.t

    def tearDown(self):
        ledger.DB_PATH, ledger.now = self._db, self._now

    def rec(self, text, minutes=0):
        self.t = dt.datetime(2026, 11, 24, 12, 0) + dt.timedelta(minutes=minutes)
        row, _ = core.record(p.parse_bank(text), "sms", text)
        return row

    def test_balance_tracked_even_for_duplicate(self):
        core.record(p.Parsed(86500, "exp", "Еда", "Korzinka"), "wallet")       # Apple Pay раньше SMS
        row, dup = core.record(p.parse_bank(BUY), "sms", BUY)
        self.assertTrue(dup)
        self.assertEqual(ledger.cards()[0]["balance"], 1250000)

    def test_pair_becomes_move_and_leaves_totals(self):
        self.rec(BUY)
        out = self.rec(OUT, 1)
        self.assertEqual(out["kind"], "exp")                    # вторая половина ещё не пришла
        inc = self.rec(IN, 3)
        self.assertEqual(inc["kind"], "move")
        self.assertEqual(ledger.get(out["id"])["kind"], "move")
        s = ledger.summary("today", ref=self.t)
        self.assertEqual((s["expense"], s["income"]), (86500, 0))
        self.assertIn("перевод между своими картами", core.describe(inc))
        self.assertEqual({c["last4"] for c in ledger.cards()}, {"4521", "7788"})

    def test_both_cards_known_one_sms(self):
        ledger.card_seen("4521")
        ledger.card_seen("7788")
        self.assertEqual(self.rec(OUT)["kind"], "move")

    def test_unknown_recipient_stays_expense(self):
        ledger.card_seen("4521")
        self.assertEqual(self.rec(OUT)["kind"], "exp")          # 7788 не наша — обычный перевод

    def test_manual_mark_and_unmark(self):
        out = self.rec(OUT)
        inc = self.rec("+500000 от себя".replace("+", "Popolnenie ") + " UZS", 40)   # далеко по времени — пары нет
        self.assertEqual(inc["kind"], "inc")
        row = core.mark_own_transfer(out)
        self.assertEqual(row["kind"], "move")
        self.assertEqual(ledger.get(inc["id"])["kind"], "inc")   # 40 минут — не та же операция
        self.assertEqual(core.unmark_own_transfer(row)["kind"], "exp")
        self.assertIn(("🔁 Перевод между моими картами", f"own:{out['id']}"), core.tx_buttons(ledger.get(out["id"]))[1])

    def test_unmark_restores_both_halves(self):
        out = self.rec(OUT)
        inc = self.rec(IN, 2)
        core.unmark_own_transfer(inc)
        self.assertEqual((ledger.get(out["id"])["kind"], ledger.get(inc["id"])["kind"]), ("exp", "inc"))

    def test_csv_label(self):
        self.rec(OUT)
        self.rec(IN, 2)
        self.assertIn("свой перевод", ledger.export_csv("all"))


if __name__ == "__main__":
    unittest.main()
