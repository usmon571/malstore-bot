"""Надёжность: возврат удалённой записи, очередь чеков после перезапуска, пропущенные итоги, повторы уведомлений."""
import asyncio
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("EXPENSES_DB", os.path.join(tempfile.mkdtemp(), "test.db"))

import core  # noqa: E402
import ledger  # noqa: E402
import receipt  # noqa: E402

LINK = "https://ofd.soliq.uz/check?t=EP000000000123&r=4567&c=20261119143005&s=123456789012"


class Base(unittest.TestCase):
    def setUp(self):
        self._db = ledger.DB_PATH
        ledger.DB_PATH = Path(tempfile.mkdtemp()) / "r.db"
        ledger.init()

    def tearDown(self):
        ledger.DB_PATH = self._db


class Restore(Base):
    def test_delete_and_restore_same_id(self):
        i = ledger.add(45000, "exp", "Кафе и рестораны", "Safia", "🧾 Lavash", "receipt", "{}", "personal")
        saved = dict(ledger.get(i))
        ledger.delete(i)
        self.assertIsNone(ledger.get(i))
        self.assertEqual(ledger.restore(saved), i)
        self.assertEqual(dict(ledger.get(i)), saved)


class PendingReceipts(Base):
    def setUp(self):
        super().setUp()
        self._timer = core.threading.Timer
        self.started = []

        class FakeTimer:
            def __init__(s, delay, fn):
                s.delay, s.fn, s.daemon = delay, fn, False

            def start(s):
                self.started.append(s)

        core.threading.Timer = FakeTimer
        self._notify, core.notify = core.notify, lambda *a, **k: None

    def tearDown(self):
        core.threading.Timer = self._timer
        core.notify = self._notify
        super().tearDown()

    def test_survives_restart(self):
        core.retry_receipt(receipt.from_link(LINK), 2)
        self.assertEqual(core.pending_receipts(), {LINK: 2})
        self.started.clear()                       # «перезапуск»: таймеры пропали, база осталась
        self.assertEqual(core.resume_receipts(), 1)
        self.assertEqual(self.started[0].delay, core.RETRY_MINUTES[2] * 60)

    def test_removed_after_success(self):
        core.retry_receipt(receipt.from_link(LINK), 0)
        orig = receipt.fetch
        core.process_receipt.__defaults__ = (lambda ref: receipt.Receipt(total=5000, merchant="Test"),)
        try:
            self.started[0].fn()
        finally:
            core.process_receipt.__defaults__ = (orig,)
        self.assertEqual(core.pending_receipts(), {})


class Notify(Base):
    def test_retries_until_delivered(self):
        calls = []
        old = core.tg, core.NOTIFY_RETRY_SECONDS, core.BOT_TOKEN, core.owner_id
        core.tg = lambda method, **p: calls.append(p) or ({"ok": True, "result": {"message_id": 7}} if len(calls) >= 3 else {})
        core.NOTIFY_RETRY_SECONDS, core.BOT_TOKEN, core.owner_id = (0, 0, 0), "x", lambda: 1
        try:
            i = ledger.add(1000, "exp", "Прочее")
            core.notify("hi", ledger.get(i), background=False)
        finally:
            core.tg, core.NOTIFY_RETRY_SECONDS, core.BOT_TOKEN, core.owner_id = old
        self.assertEqual(len(calls), 3)
        self.assertEqual(ledger.meta_get("msg:7"), str(i))


class Nightly(Base):
    def setUp(self):
        super().setUp()
        try:
            import main
        except ImportError as e:  # без python-telegram-bot
            self.skipTest(str(e))
        self.main = main
        self.sent = []

        class Bot:
            async def send_message(s, chat, text, **k):
                self.sent.append(text)

            async def send_document(s, chat, doc, **k):
                self.sent.append("CSV")

        class Ctx:
            bot = Bot()

        self.ctx = Ctx()
        self._now, self._owner, self._backup = ledger.now, core.owner_id, ledger.backup
        core.owner_id, ledger.backup = (lambda: 1), (lambda: None)

    def tearDown(self):
        ledger.now, core.owner_id, ledger.backup = self._now, self._owner, self._backup
        super().tearDown()

    def run_at(self, when):
        ledger.now = lambda: when
        asyncio.run(self.main.nightly(self.ctx))

    def test_due_day(self):
        d = self.main.due_day
        self.assertEqual(d(dt.datetime(2026, 11, 20, 22, 5)).day, 20)
        self.assertEqual(d(dt.datetime(2026, 11, 21, 1, 0)).day, 20)    # ПК проснулся ночью — итог за вчера

    def test_late_summary_once(self):
        ledger.add(30000, "exp", "Еда", ts=dt.datetime(2026, 11, 20, 13, 0))
        self.run_at(dt.datetime(2026, 11, 21, 7, 30))                    # пропустили 22:00 — прислать утром
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Вчера", self.sent[0])
        self.run_at(dt.datetime(2026, 11, 21, 9, 0))                     # второй раз не присылать
        self.assertEqual(len(self.sent), 1)

    def test_first_of_month_late(self):
        self.run_at(dt.datetime(2026, 12, 2, 6, 0))                      # итог 1 декабря пришёл 2-го утром
        self.assertIn("CSV", self.sent)
        self.assertTrue(any("Итог месяца" in s for s in self.sent))


if __name__ == "__main__":
    unittest.main()
