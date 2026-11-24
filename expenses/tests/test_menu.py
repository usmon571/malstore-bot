"""Меню в чате: запись по шагам (категория → сумма → на что), история, лимиты, поиск."""
import asyncio
import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("EXPENSES_DB", os.path.join(tempfile.mkdtemp(), "test.db"))

import ledger  # noqa: E402
import main  # noqa: E402
import parsing  # noqa: E402

UID = 777


class Msg:
    """Сообщение Telegram: запоминает ответы бота."""
    counter = 100

    def __init__(self, log, text="", reply_to=None):
        Msg.counter += 1
        self.message_id, self.chat_id, self.log = Msg.counter, UID, log
        self.text, self.caption, self.reply_to_message, self.forward_origin = text, None, reply_to, None
        self.from_user = SimpleNamespace(is_bot=True)
        self.reply_markup = None

    async def reply_text(self, text, **kw):
        m = Msg(self.log, text)
        m.reply_markup = kw.get("reply_markup")
        self.log.append(m)
        return m

    async def reply_photo(self, photo, **kw):
        return await self.reply_text("[фото]", **kw)


class Query:
    def __init__(self, data, message):
        self.data, self.message, self.from_user = data, message, SimpleNamespace(id=UID)
        self.answers = []

    async def answer(self, text=None, **kw):
        self.answers.append(text)

    async def edit_message_text(self, text, **kw):
        self.message.text, self.message.reply_markup = text, kw.get("reply_markup")

    async def edit_message_reply_markup(self, markup=None, **kw):
        self.message.reply_markup = markup


class Bot:
    async def edit_message_reply_markup(self, *a, **kw):
        pass


def buttons(m) -> dict:
    """{текст кнопки: callback_data}"""
    rows = m.reply_markup.inline_keyboard if m.reply_markup else []
    return {b.text: b.callback_data for row in rows for b in row}


class Menu(unittest.TestCase):
    def setUp(self):
        self._db, self._now = ledger.DB_PATH, ledger.now
        ledger.DB_PATH = Path(tempfile.mkdtemp()) / "m.db"
        ledger.init()
        self.t = dt.datetime(2026, 11, 24, 18, 0)
        ledger.now = lambda: self.t
        self.patches = [mock.patch.object(main.core, "is_owner", lambda uid: uid == UID),
                        mock.patch.object(main.core, "owner_id", lambda: UID)]
        for p in self.patches:
            p.start()
        self.log = []
        self.c = SimpleNamespace(user_data={}, args=None, bot=Bot())

    def tearDown(self):
        for p in self.patches:
            p.stop()
        ledger.DB_PATH, ledger.now = self._db, self._now

    def run_(self, coro):
        return asyncio.run(coro)

    def upd(self, msg=None, query=None):
        m = msg or (query.message if query else None)
        return SimpleNamespace(effective_user=SimpleNamespace(id=UID), effective_message=m, message=msg,
                               callback_query=query)

    def say(self, text, reply_to=None):
        m = Msg(self.log, text, reply_to)
        self.run_(main.text(self.upd(m), self.c))
        return self.log[-1] if self.log else None

    def press(self, message, label):
        data = buttons(message)[label]
        q = Query(data, message)
        self.run_(main.cb(self.upd(query=q), self.c))
        return q

    def test_expense_steps_with_purpose(self):
        prompt = self.say("➕ Расход")
        self.assertIn("выберите категорию", prompt.text)
        self.assertIn("✨ Хотелки", buttons(prompt))
        self.assertNotIn("💰 Доход", buttons(prompt))
        self.press(prompt, "✨ Хотелки")
        self.assertIn("Хотелки", prompt.text)
        self.say("150к")
        row = ledger.get(ledger.last_id())
        self.assertEqual((row["amount"], row["category"], row["source"]), (150000, "Хотелки", "manual"))
        self.assertIn("На что?", self.log[-1].text)
        self.say("наушники")
        row = ledger.get(row["id"])
        self.assertEqual((row["note"], row["category"]), ("наушники", "Хотелки"))   # выбранная категория осталась
        self.assertEqual(self.c.user_data, {})

    def test_amount_with_note_and_business_wallet(self):
        prompt = self.say("➕ Расход")
        self.press(prompt, "💼 Бизнес")
        self.press(prompt, "🛒 Еда")
        self.say("45000 обед для команды")
        row = ledger.get(ledger.last_id())
        self.assertEqual((row["category"], row["wallet"], row["note"]), ("Еда", "business", "обед для команды"))
        self.assertNotIn("На что?", self.log[-1].text)                              # пояснение уже есть

    def test_learned_shop_does_not_override_choice(self):
        ledger.add(10000, "exp", "Еда", "korzinka")
        ledger.set_category(ledger.last_id(), "Еда")
        prompt = self.say("➕ Расход")
        self.press(prompt, "✨ Хотелки")
        self.say("30000 korzinka")
        self.assertEqual(ledger.get(ledger.last_id())["category"], "Хотелки")

    def test_skip_cancel_and_typing_instead(self):
        prompt = self.say("➕ Расход")
        self.press(prompt, "✖️ Отмена")
        self.assertIn("Отменено", prompt.text)
        self.assertIsNone(ledger.last_id())
        self.say("➕ Расход")
        self.say("15000 такси")                                                     # написал сам, без кнопок
        self.assertEqual(ledger.get(ledger.last_id())["category"], "Транспорт")
        prompt = self.say("➕ Расход")
        self.press(prompt, "🛒 Еда")
        ask = self.say("20000")
        self.press(ask, "Пропустить")
        self.assertEqual(self.c.user_data, {})
        self.say("5000 вода")                                                       # новая запись, не пояснение
        self.assertEqual(ledger.get(ledger.last_id())["amount"], 5000)

    def test_bad_amount_and_income(self):
        prompt = self.say("➕ Расход")
        self.press(prompt, "🚕 Транспорт")
        self.assertIn("Не понял сумму", self.say("много").text)
        self.say("➕ Доход")
        self.say("3 млн зарплата")
        row = ledger.get(ledger.last_id())
        self.assertEqual((row["kind"], row["amount"], row["category"]), ("inc", 3000000, "Доход"))

    def test_history_paging_and_open(self):
        for i in range(12):
            ledger.add(1000 * (i + 1), "exp", "Еда", f"магазин {i}", ts=self.t - dt.timedelta(hours=i))
        h = self.say("📋 История")
        self.assertIn("−1 000", h.text)                                            # новые сверху
        self.assertNotIn("12 000", h.text)
        self.assertIn("◀️ Раньше", buttons(h))
        self.press(h, "◀️ Раньше")
        self.assertIn("12 000", h.text)
        self.assertIn("Новее ▶️", buttons(h))
        self.press(h, "1")
        card = self.log[-1]
        self.assertIn("Ответьте на это сообщение", card.text)
        self.say("2500", reply_to=card)                                             # правка суммы ответом
        self.assertEqual(ledger.get(int(ledger.meta_get(f"msg:{card.message_id}")))["amount"], 2500)

    def test_limits_flow(self):
        scr = self.say("🎯 Лимиты")
        self.assertIn("Пока нет", scr.text)
        self.press(scr, "➕ Задать или изменить лимит")
        self.press(scr, "🍔 Кафе и рестораны")
        self.say("800к")
        self.assertEqual(ledger.budget_get("Кафе и рестораны"), 800000)
        self.assertIn("800 000", self.log[-1].text)

    def test_more_menu_and_find(self):
        ledger.add(42000, "exp", "Еда", "Korzinka")
        m = self.say("⚙️ Ещё")
        self.press(m, "🔍 Поиск")
        res = self.say("korzinka")
        self.assertIn("42 000", res.text)
        self.press(m, "📅 Сегодня")
        self.assertIn("Сегодня", self.log[-1].text)

    def test_menu_layout(self):
        labels = [b.text if hasattr(b, "text") else b for row in main.MENU.keyboard for b in row]
        for label in labels:
            self.assertIn(label, main.BUTTONS)
        self.assertTrue(all(cat in main.CAT_ICON for cat in parsing.ALL_CATEGORIES))


if __name__ == "__main__":
    unittest.main()
