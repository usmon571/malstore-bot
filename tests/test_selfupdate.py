"""Telegram orqali yangilash: sozlama va baza tegilmaydi, xavfli yo'llar rad etiladi, buzuq kod qo'yilmaydi.
Ishga tushirish: python -m unittest discover -s tests"""
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import selfupdate  # noqa: E402


def make_zip(files: dict, top: str = "") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in files.items():
            z.writestr(top + name, body)
    return buf.getvalue()


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        (self.base / "bot.py").write_bytes(b"x = 1\n")      # baytma-bayt: Windows \n ni \r\n ga aylantirmasin
        (self.base / "store.py").write_bytes(b"same = 1\n")
        (self.base / "config.json").write_text('{"bot_token": "SECRET"}')
        (self.base / "store.json").write_text('{"products": [1]}')
        (self.base / ".env").write_text("BOT_TOKEN=SECRET")
        (self.base / "start_bot.bat").write_text("old loop")

    def apply(self, files, top="", check=False):
        return selfupdate.apply(make_zip(files, top), base=self.base, check=check)

    def test_replaces_changed_keeps_secrets_and_data(self):
        res = self.apply({"bot.py": "x = 2\n", "store.py": "same = 1\n", "api.py": "y = 1\n",
                          "config.json": "{}", "store.json": "{}", ".env": "BOT_TOKEN=OTHER",
                          ".env.example": "BOT_TOKEN=", "venv/x.py": "", "start_bot.bat": "new loop"})
        self.assertEqual(res["changed"], [".env.example", "api.py", "bot.py"])   # store.py o'zgarmagan
        self.assertEqual(res["kept"], ["start_bot.bat"])                         # ishlayotgan .bat tegilmaydi
        self.assertEqual(res["skipped"], 4)
        self.assertEqual((self.base / "bot.py").read_text(), "x = 2\n")
        self.assertIn("SECRET", (self.base / "config.json").read_text())
        self.assertIn("SECRET", (self.base / ".env").read_text())
        self.assertIn("[1]", (self.base / "store.json").read_text())
        with zipfile.ZipFile(res["backup"]) as z:                                # eski versiya saqlangan
            self.assertEqual(z.read("bot.py"), b"x = 1\n")

    def test_github_archive_folder(self):
        res = self.apply({"bot.py": "x = 3\n"}, top="malstore-bot-main/")
        self.assertEqual(res["changed"], ["bot.py"])

    def test_rejects(self):
        for files in ({"bot.py": "x", "../evil.py": "x"}, {"store.py": "x"}):
            with self.assertRaises(selfupdate.UpdateError):
                self.apply(files)
        with self.assertRaises(selfupdate.UpdateError):
            selfupdate.apply(b"not a zip", base=self.base)
        self.assertEqual((self.base / "bot.py").read_text(), "x = 1\n")

    def test_syntax_error_blocks_update(self):
        with self.assertRaises(selfupdate.UpdateError) as e:
            self.apply({"bot.py": "def broken(:\n"}, check=True)
        self.assertIn("sintaksis", str(e.exception))
        self.assertEqual((self.base / "bot.py").read_text(), "x = 1\n")

    def test_failing_tests_block_update(self):
        bad = {"bot.py": "x = 2\n", "tests/test_x.py":
               "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertEqual(1, 2)\n"}
        with self.assertRaises(selfupdate.UpdateError) as e:
            self.apply(bad, check=True)
        self.assertIn("testlar o'tmadi", str(e.exception))
        self.assertEqual((self.base / "bot.py").read_text(), "x = 1\n")

    def test_good_update_with_tests(self):
        good = {"bot.py": "x = 2\n", "tests/test_x.py":
                "import unittest\nclass T(unittest.TestCase):\n    def test_a(self):\n        self.assertTrue(True)\n"}
        res = self.apply(good, check=True)
        self.assertIn("testlar o'tdi", res["check"])


class RailwayTest(unittest.TestCase):
    def test_detect(self):
        with mock.patch.dict(os.environ, {"RAILWAY_ENVIRONMENT": "production"}):
            self.assertTrue(selfupdate.on_railway())


if __name__ == "__main__":
    unittest.main()
