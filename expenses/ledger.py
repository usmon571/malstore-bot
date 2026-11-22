"""SQLite-хранилище расходов. Один файл expenses.db рядом с ботом, без внешних сервисов."""
from __future__ import annotations

import csv
import io
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(os.getenv("EXPENSES_DB", Path(__file__).parent / "expenses.db"))
BACKUP_DIR = DB_PATH.parent / "backups"
_lock = threading.RLock()
FMT = "%Y-%m-%d %H:%M:%S"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with _lock, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tx(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            amount INTEGER NOT NULL,
            kind TEXT NOT NULL,              -- exp | inc
            category TEXT NOT NULL,
            merchant TEXT DEFAULT '',
            note TEXT DEFAULT '',
            source TEXT DEFAULT 'manual',    -- manual | sms | wallet
            raw TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS tx_ts ON tx(ts);
        CREATE TABLE IF NOT EXISTS merchant_cat(merchant TEXT PRIMARY KEY, category TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS budget(category TEXT PRIMARY KEY, monthly INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
        """)
        cols = {r["name"] for r in c.execute("PRAGMA table_info(tx)")}
        if "wallet" not in cols:  # миграция со старой версии
            c.execute("ALTER TABLE tx ADD COLUMN wallet TEXT NOT NULL DEFAULT 'personal'")
        mcols = {r["name"] for r in c.execute("PRAGMA table_info(merchant_cat)")}
        if "wallet" not in mcols:
            c.execute("ALTER TABLE merchant_cat ADD COLUMN wallet TEXT")


def now() -> datetime:
    return datetime.now()


def meta_get(k: str, default: str | None = None) -> str | None:
    with _lock, _conn() as c:
        r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default


def meta_set(k: str, v: str) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def learned(merchant: str) -> tuple[str | None, str | None]:
    """(категория, кошелёк), которые вы ранее выбрали для этого магазина."""
    if not merchant:
        return None, None
    with _lock, _conn() as c:
        r = c.execute("SELECT category, wallet FROM merchant_cat WHERE merchant=?", (merchant.lower(),)).fetchone()
        return (r["category"], r["wallet"]) if r else (None, None)


def _remember(c, merchant: str, category: str | None = None, wallet: str | None = None) -> None:
    if not merchant:
        return
    m = merchant.lower()
    c.execute("INSERT OR IGNORE INTO merchant_cat(merchant, category) VALUES(?, ?)", (m, category or "Прочее"))
    if category:
        c.execute("UPDATE merchant_cat SET category=? WHERE merchant=?", (category, m))
    if wallet:
        c.execute("UPDATE merchant_cat SET wallet=? WHERE merchant=?", (wallet, m))


def is_duplicate(amount: int, kind: str, source: str, minutes: int = 10) -> bool:
    """Apple Pay-шорткат и SMS банка присылают одну и ту же покупку — не считаем дважды."""
    if source == "manual":
        return False
    since = (now() - timedelta(minutes=minutes)).strftime(FMT)
    with _lock, _conn() as c:
        return c.execute("SELECT 1 FROM tx WHERE amount=? AND kind=? AND ts>=? AND source!='manual' AND source!=? LIMIT 1",
                         (amount, kind, since, source)).fetchone() is not None


def add(amount: int, kind: str, category: str, merchant: str = "", note: str = "", source: str = "manual",
        raw: str = "", wallet: str = "personal", days_ago: int = 0, ts: datetime | None = None) -> int:
    if ts is None:
        ts = now() - timedelta(days=days_ago)
        if days_ago:
            ts = ts.replace(hour=12, minute=0, second=0)
    with _lock, _conn() as c:
        cur = c.execute(
            "INSERT INTO tx(ts,amount,kind,category,merchant,note,source,raw,wallet) VALUES(?,?,?,?,?,?,?,?,?)",
            (ts.strftime(FMT), amount, kind, category, merchant, note, source, raw[:1000], wallet))
        return cur.lastrowid


def find_match(amount: int, kind: str, ts: datetime, minutes: int = 180):
    """Запись Apple Pay/SMS на ту же сумму около времени чека, к которой ещё не привязан чек."""
    lo, hi = (ts - timedelta(minutes=minutes)).strftime(FMT), (ts + timedelta(minutes=minutes)).strftime(FMT)
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM tx WHERE amount=? AND kind=? AND ts BETWEEN ? AND ? "
                         "AND source NOT IN ('manual','receipt') AND COALESCE(note,'') NOT LIKE '🧾%'", (amount, kind, lo, hi)).fetchall()
    return min(rows, key=lambda r: abs(datetime.strptime(r["ts"], FMT) - ts), default=None)


def attach_receipt(tx_id: int, merchant: str, note: str, raw: str, category: str | None = None) -> None:
    """Дописывает к существующей записи товары из чека; магазин и категорию — только если их не было."""
    with _lock, _conn() as c:
        r = c.execute("SELECT merchant, category FROM tx WHERE id=?", (tx_id,)).fetchone()
        if not r:
            return
        new_merchant = r["merchant"] or merchant
        new_cat = category if category and r["category"] == "Прочее" else r["category"]
        c.execute("UPDATE tx SET merchant=?, note=?, category=?, raw=? WHERE id=?",
                  (new_merchant, note, new_cat, raw[:1000], tx_id))


def get(tx_id: int):
    with _lock, _conn() as c:
        return c.execute("SELECT * FROM tx WHERE id=?", (tx_id,)).fetchone()


def delete(tx_id: int) -> bool:
    with _lock, _conn() as c:
        return c.execute("DELETE FROM tx WHERE id=?", (tx_id,)).rowcount > 0


TX_COLS = ("id", "ts", "amount", "kind", "category", "merchant", "note", "source", "raw", "wallet")


def restore(row: dict) -> int:
    """Вернуть удалённую запись (кнопка «↩️ Вернуть») с тем же id."""
    vals = [row.get(k, "") for k in TX_COLS]
    with _lock, _conn() as c:
        c.execute(f"INSERT OR REPLACE INTO tx({','.join(TX_COLS)}) VALUES({','.join('?' * len(TX_COLS))})", vals)
    return int(row["id"])


def last_id() -> int | None:
    with _lock, _conn() as c:
        r = c.execute("SELECT id FROM tx ORDER BY id DESC LIMIT 1").fetchone()
        return r["id"] if r else None


def set_category(tx_id: int, category: str, wallet: str | None = None) -> None:
    with _lock, _conn() as c:
        if wallet:
            c.execute("UPDATE tx SET category=?, wallet=? WHERE id=?", (category, wallet, tx_id))
        else:
            c.execute("UPDATE tx SET category=? WHERE id=?", (category, tx_id))
        r = c.execute("SELECT merchant FROM tx WHERE id=?", (tx_id,)).fetchone()
        if r:
            _remember(c, r["merchant"], category, wallet)


def set_wallet(tx_id: int, wallet: str) -> None:
    with _lock, _conn() as c:
        c.execute("UPDATE tx SET wallet=? WHERE id=?", (wallet, tx_id))
        r = c.execute("SELECT merchant, category FROM tx WHERE id=?", (tx_id,)).fetchone()
        if r:
            _remember(c, r["merchant"], None, wallet)


def set_amount(tx_id: int, amount: int) -> None:
    with _lock, _conn() as c:
        c.execute("UPDATE tx SET amount=? WHERE id=?", (amount, tx_id))


# ---------- периоды и отчёты ----------

def _day(d: datetime) -> datetime:
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def period_range(period: str, ref: datetime | None = None) -> tuple[datetime, datetime]:
    """[начало, конец) периода. today | yesterday | week | lastweek | month | lastmonth | N (дней) | all."""
    n = ref or now()
    t = _day(n)
    if period == "today":
        return t, t + timedelta(days=1)
    if period == "yesterday":
        return t - timedelta(days=1), t
    if period == "week":  # последние 7 дней, включая сегодня
        return t - timedelta(days=6), t + timedelta(days=1)
    if period == "lastweek":
        return t - timedelta(days=13), t - timedelta(days=6)
    if period == "month":
        s = t.replace(day=1)
        return s, (s + timedelta(days=32)).replace(day=1)
    if period == "lastmonth":
        e = t.replace(day=1)
        return (e - timedelta(days=1)).replace(day=1), e
    if period.isdigit():
        return t - timedelta(days=int(period) - 1), t + timedelta(days=1)
    return datetime(2000, 1, 1), datetime(2100, 1, 1)


def summary(period: str, wallet: str | None = None, ref: datetime | None = None) -> dict:
    s, e = period_range(period, ref)
    since, until = s.strftime(FMT), e.strftime(FMT)
    w = " AND wallet=?" if wallet else ""
    args = (since, until) + ((wallet,) if wallet else ())
    with _lock, _conn() as c:
        q = lambda sql: c.execute(sql, args)  # noqa: E731
        exp = q(f"SELECT COALESCE(SUM(amount),0) s, COUNT(*) n FROM tx WHERE kind='exp' AND ts>=? AND ts<?{w}").fetchone()
        inc = q(f"SELECT COALESCE(SUM(amount),0) s FROM tx WHERE kind='inc' AND ts>=? AND ts<?{w}").fetchone()
        cats = q(f"SELECT category, SUM(amount) s, COUNT(*) n FROM tx WHERE kind='exp' AND ts>=? AND ts<?{w} "
                 "GROUP BY category ORDER BY s DESC").fetchall()
        days = q(f"SELECT substr(ts,1,10) d, SUM(amount) s FROM tx WHERE kind='exp' AND ts>=? AND ts<?{w} "
                 "GROUP BY d ORDER BY d").fetchall()
        top = q(f"SELECT merchant, SUM(amount) s, COUNT(*) n FROM tx WHERE kind='exp' AND ts>=? AND ts<?{w} "
                "AND merchant!='' GROUP BY lower(merchant) ORDER BY s DESC LIMIT 8").fetchall()
        wallets = c.execute("SELECT wallet, kind, SUM(amount) s FROM tx WHERE ts>=? AND ts<? GROUP BY wallet, kind",
                            (since, until)).fetchall()
        recent = c.execute(f"SELECT id, ts, amount, kind, category, merchant, note, source, wallet FROM tx "
                           f"WHERE 1=1{w} ORDER BY ts DESC, id DESC LIMIT 30",
                           ((wallet,) if wallet else ())).fetchall()
        ms, me = period_range("month", ref)
        spent_m = {r["category"]: r["s"] for r in c.execute(
            "SELECT category, SUM(amount) s FROM tx WHERE kind='exp' AND ts>=? AND ts<? GROUP BY category",
            (ms.strftime(FMT), me.strftime(FMT)))}
        bud = c.execute("SELECT category, monthly FROM budget ORDER BY category").fetchall()
    wsum: dict = {}
    for r in wallets:
        wsum.setdefault(r["wallet"], {"exp": 0, "inc": 0})[r["kind"]] = r["s"]
    return {
        "period": period, "wallet": wallet, "since": since[:10], "until": until[:10],
        "expense": exp["s"], "count": exp["n"], "income": inc["s"],
        "by_category": [dict(r) for r in cats],
        "by_day": [dict(r) for r in days],
        "top_merchants": [dict(r) for r in top],
        "wallets": wsum,
        "recent": [dict(r) for r in recent],
        "budgets": [{"category": b["category"], "limit": b["monthly"], "spent": spent_m.get(b["category"], 0)} for b in bud],
    }


def count_on(day: datetime) -> int:
    s = _day(day)
    with _lock, _conn() as c:
        return c.execute("SELECT COUNT(*) n FROM tx WHERE ts>=? AND ts<?",
                         (s.strftime(FMT), (s + timedelta(days=1)).strftime(FMT))).fetchone()["n"]


def month_spent(category: str) -> int:
    s, e = period_range("month")
    with _lock, _conn() as c:
        return c.execute("SELECT COALESCE(SUM(amount),0) s FROM tx WHERE kind='exp' AND category=? AND ts>=? AND ts<?",
                         (category, s.strftime(FMT), e.strftime(FMT))).fetchone()["s"]


def budget_get(category: str) -> int | None:
    with _lock, _conn() as c:
        r = c.execute("SELECT monthly FROM budget WHERE category=?", (category,)).fetchone()
        return r["monthly"] if r else None


def budget_set(category: str, monthly: int) -> None:
    with _lock, _conn() as c:
        if monthly <= 0:
            c.execute("DELETE FROM budget WHERE category=?", (category,))
        else:
            c.execute("INSERT INTO budget(category,monthly) VALUES(?,?) "
                      "ON CONFLICT(category) DO UPDATE SET monthly=excluded.monthly", (category, monthly))


def search(q: str, limit: int = 15) -> list:
    like = f"%{q.lower()}%"
    with _lock, _conn() as c:
        return c.execute("SELECT * FROM tx WHERE lower(merchant) LIKE ? OR lower(note) LIKE ? OR lower(category) LIKE ? "
                         "ORDER BY ts DESC LIMIT ?", (like, like, like, limit)).fetchall()


def export_csv(period: str = "all") -> str:
    s, e = period_range(period)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")  # ; — чтобы Excel с русской локалью открыл по столбцам
    w.writerow(["id", "дата", "сумма", "тип", "кошелёк", "категория", "магазин", "заметка", "источник"])
    with _lock, _conn() as c:
        for r in c.execute("SELECT * FROM tx WHERE ts>=? AND ts<? ORDER BY ts", (s.strftime(FMT), e.strftime(FMT))):
            w.writerow([r["id"], r["ts"], r["amount"], "расход" if r["kind"] == "exp" else "доход",
                        "бизнес" if r["wallet"] == "business" else "личное",
                        r["category"], r["merchant"], r["note"], r["source"]])
    return buf.getvalue()


def backup(keep: int = 14) -> Path:
    """Ежедневная копия базы (безопасно даже во время записи) и удаление старых."""
    BACKUP_DIR.mkdir(exist_ok=True)
    dst = BACKUP_DIR / f"expenses-{now():%Y-%m-%d}.db"
    with _lock:
        src = sqlite3.connect(DB_PATH)
        out = sqlite3.connect(dst)
        src.backup(out)
        out.close()
        src.close()
    for old in sorted(BACKUP_DIR.glob("expenses-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return dst
