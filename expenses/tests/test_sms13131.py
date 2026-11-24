"""SMS от 13131 (Humo): время из SMS, P2P, коды подтверждения; вопрос «на что?» и ответ на него."""
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("EXPENSES_DB", os.path.join(tempfile.mkdtemp(), "test.db"))

import core  # noqa: E402
import ledger  # noqa: E402
import parsing as p  # noqa: E402

NOW = dt.datetime(2026, 11, 24, 15, 10)
P2P = "HUMOCARD *5789: oplata 20000.00 UZS; IYB VISA SUM P2P>Tas; 26-11-24 14:51; balans: 1 230 000.00 UZS"
SHOP = "HUMOCARD *5789: oplata 45 000.00 UZS; KORZINKA YUNUSOBOD; 26-11-24 13:05; balans: 1 185 000.00 UZS"
OTP = "Kod podtverzhdeniya: 482913. Nikomu ne soobshchayte. 13131"
PROMO = "Humo: kartangizni to'ldiring va 50 000 so'm yutib oling! Batafsil: humocard.uz"
REFUND = "HUMOCARD *5789: otmena oplaty 45 000.00 UZS; KORZINKA YUNUSOBOD; 26-11-24 14:00"


class Parse(unittest.TestCase):
    def test_p2p(self):
        r = p.parse_bank(P2P)
        self.assertEqual((r.amount, r.kind, r.category), (20000, "exp", "Переводы"))
        self.assertEqual(r.merchant, "IYB VISA SUM P2P>Tas")
        self.assertEqual((r.cards, r.balance), (("5789",), 1230000))

    def test_time_from_sms(self):
        self.assertEqual(p.sms_time(P2P, NOW), dt.datetime(2026, 11, 24, 14, 51))
        self.assertEqual(p.sms_time("24.11.26 09:30", NOW), dt.datetime(2026, 11, 24, 9, 30))
        self.assertEqual(p.sms_time("24-11-26 09:30", NOW), dt.datetime(2026, 11, 24, 9, 30))   # ДД-ММ-ГГ тоже
        self.assertIsNone(p.sms_time("26-11-24 23:59", NOW))       # в будущем — не верим
        self.assertIsNone(p.sms_time("oplata 5000 UZS", NOW))

    def test_not_transactions(self):
        self.assertTrue(p.is_transaction(P2P))
        self.assertFalse(p.is_transaction(OTP))
        self.assertFalse(p.is_transaction(PROMO))

    def test_refund_is_income(self):
        self.assertEqual(p.parse_bank(REFUND).kind, "inc")


class Flow(unittest.TestCase):
    def setUp(self):
        self._db, self._now = ledger.DB_PATH, ledger.now
        ledger.DB_PATH = Path(tempfile.mkdtemp()) / "s.db"
        ledger.init()
        ledger.now = lambda: NOW
        self.sent = []
        self._notify = core.notify
        core.notify = lambda text, row=None, silent=False, background=True: self.sent.append((text, silent))

    def tearDown(self):
        ledger.DB_PATH, ledger.now, core.notify = self._db, self._now, self._notify

    def test_ingest_asks_and_uses_sms_time(self):
        with mock.patch.object(p, "datetime", wraps=dt.datetime) as m:
            m.now.return_value = NOW
            ok, _ = core.ingest_text(P2P, "sms")
        self.assertTrue(ok)
        row = ledger.get(ledger.last_id())
        self.assertEqual(row["ts"], "2026-11-24 14:51:00")
        text, silent = self.sent[-1]
        self.assertIn("На что?", text)
        self.assertFalse(silent)                       # вопрос — со звуком

    def test_otp_skipped(self):
        ok, msg = core.ingest_text(OTP, "sms")
        self.assertTrue(ok)
        self.assertIsNone(ledger.last_id())
        self.assertEqual(self.sent, [])

    def test_purpose_sets_note_and_category_only_here(self):
        core.ingest_text(P2P, "sms")
        row = ledger.get(ledger.last_id())
        self.assertTrue(core.needs_purpose(row))
        row = core.set_purpose(row, "обед с коллегами")
        self.assertEqual((row["note"], row["category"]), ("обед с коллегами", "Кафе и рестораны"))
        self.assertFalse(core.needs_purpose(row))
        self.assertIn("«обед с коллегами»", core.describe(row))
        self.assertEqual(ledger.learned(row["merchant"]), (None, None))   # P2P не запоминается как «кафе»
        self.assertEqual(ledger.unexplained(NOW - dt.timedelta(days=1)), [])

    def test_purpose_unknown_keeps_category_and_business_tag(self):
        core.ingest_text(P2P, "sms")
        row = core.set_purpose(ledger.get(ledger.last_id()), "долг Акмалю")
        self.assertEqual(row["category"], "Переводы")
        row = core.set_purpose(row, "оплата поставщику #б")
        self.assertEqual((row["note"], row["wallet"]), ("оплата поставщику", "business"))

    def test_manual_not_asked(self):
        row, _ = core.record(p.parse_manual("15000 такси"), "manual")
        self.assertFalse(core.needs_purpose(row))

    def test_unexplained_lists_auto_expenses(self):
        core.ingest_text(P2P, "sms")
        core.ingest_text(SHOP, "sms")
        self.assertEqual(len(ledger.unexplained(NOW - dt.timedelta(days=1))), 2)


class AmountOnly(unittest.TestCase):
    def test_reply_patterns(self):
        import importlib
        main = importlib.import_module("main")
        for t in ("25000", "25к", "25 000 сум", "1.2 млн", "25 000 so'm"):
            self.assertTrue(main.AMOUNT_ONLY.fullmatch(t), t)
        for t in ("2 кофе", "обед", "долг 20000"):
            self.assertFalse(main.AMOUNT_ONLY.fullmatch(t), t)


if __name__ == "__main__":
    unittest.main()
