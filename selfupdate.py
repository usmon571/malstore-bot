"""Botni Telegram orqali yangilash: admin ZIP arxivni botga fayl qilib yuboradi.

Tartib: arxiv vaqtinchalik papkaga ochiladi -> u yerda hamma .py fayllar kompilyatsiya qilinadi (va tests/
bo'lsa, testlar o'tkaziladi) -> faqat hammasi joyida bo'lsa o'zgargan fayllar almashtiriladi (eskilari
backups/code-*.zip ga) -> requirements.txt o'zgargan bo'lsa kutubxonalar o'rnatiladi -> bot qayta ishga tushadi.

config.json, .env, baza (store.json), kesh va venv arxivda bo'lsa ham hech qachon almashtirilmaydi.
"""
from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

log = logging.getLogger("malstore-bot.update")
BASE = Path(__file__).resolve().parent
MAIN_FILE = "bot.py"
MAX_FILES = 300
# hech qachon almashtirilmaydi: maxfiy sozlamalar va ma'lumotlar
PROTECTED = {"config.json", ".env", "store.json", "translation_cache.json", "poizon_session.json", "bot.log",
             "login_screen.png"}
SKIP_DIRS = {"venv", "backups", ".git", "__pycache__", ".pytest_cache", "data"}
# hozir ishlayotgan .bat fayllar: cmd ularni qatorma-qator o'qiydi, ustidan yozilsa tsikl buziladi
RUNNING = {"start_bot.bat", "update.bat"}
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class UpdateError(Exception):
    pass


def on_railway() -> bool:
    """Railway'da fayllar har deployda qayta yoziladi — u yerda yangilash GitHub orqali."""
    return any(os.getenv(k) for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"))


def _members(zf: zipfile.ZipFile) -> tuple[dict[str, zipfile.ZipInfo], int]:
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if not infos:
        raise UpdateError("arxiv bo'sh")
    if len(infos) > MAX_FILES * 3:
        raise UpdateError("arxivda fayllar juda ko'p")
    names = [i.filename.replace("\\", "/") for i in infos]
    # GitHub arxivi (malstore-bot-main/...) yoki papkali arxiv — ustki papkani olib tashlaymiz
    tops = {n.split("/", 1)[0] for n in names}
    strip = len(tops) == 1 and all("/" in n for n in names)
    out, skipped = {}, 0
    for info, name in zip(infos, names):
        rel = name.split("/", 1)[1] if strip else name
        p = PurePosixPath(rel)
        if not rel or p.is_absolute() or ".." in p.parts or ":" in rel:
            raise UpdateError(f"arxivda shubhali yo'l: {name}")
        if p.parts[0] in SKIP_DIRS or "__pycache__" in p.parts or p.name in PROTECTED or \
                (p.name.startswith(".env") and p.name != ".env.example") or p.suffix in (".pyc", ".log", ".db"):
            skipped += 1
            continue
        out[rel] = info
    if MAIN_FILE not in out:
        raise UpdateError(f"bu bot arxivi emas: {MAIN_FILE} yo'q")
    if len(out) > MAX_FILES:
        raise UpdateError("arxivda fayllar juda ko'p")
    return out, skipped


def _check(folder: Path, python: str) -> tuple[bool, str]:
    """Hamma .py fayllar kompilyatsiya bo'ladimi (sintaksis xatosi yo'qmi) + tests/ bo'lsa, testlar."""
    r = subprocess.run([python, "-m", "compileall", "-q", "-x", r"[\\/](venv|backups|\.git)[\\/]", str(folder)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                       creationflags=NOWIN)
    if r.returncode != 0:
        err = [x for x in ((r.stdout or "") + (r.stderr or "")).splitlines() if x.strip()]
        return False, "sintaksis xatosi: " + " · ".join(err[-3:])
    if not (folder / "tests").is_dir():
        return True, "sintaksis tekshirildi (testlar yo'q)"
    env = dict(os.environ, PYTHONUTF8="1")
    try:
        r = subprocess.run([python, "-m", "unittest", "discover", "-s", "tests"], cwd=folder, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
                           creationflags=NOWIN)
    except subprocess.TimeoutExpired:
        return False, "testlar 10 daqiqada tugamadi"
    tail = [x for x in (r.stderr or "").splitlines() if x.strip()]
    if r.returncode != 0:
        fail = [x for x in tail if x.startswith(("FAIL:", "ERROR:"))][:3]
        return False, "testlar o'tmadi: " + (" · ".join(fail) or (tail[-1] if tail else f"kod {r.returncode}"))
    ran = next((x for x in reversed(tail) if x.startswith("Ran ")), "")
    return True, "testlar o'tdi" + (f" ({ran.split()[1]} ta)" if ran else "")


def apply(data: bytes, base: Path = BASE, python: str = sys.executable, check: bool = True) -> dict:
    """Tekshirib, yangilanishni qo'llaydi. Natija: {"changed", "kept", "skipped", "check", "backup", "deps"}.
    Tekshiruv o'tmasa — UpdateError, bot fayllariga tegilmaydi."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise UpdateError("bu ZIP arxiv emas") from None
    members, skipped = _members(zf)
    new: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="malstore-update-") as tmp:
        tmp = Path(tmp)
        for rel, info in members.items():
            body = zf.read(info)
            new[rel] = body
            dst = tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(body)
        ok, summary = _check(tmp, python) if check else (True, "tekshiruvsiz")
        if not ok:
            raise UpdateError(f"{summary}. Bot o'zgartirilmadi")

    changed, kept = [], []
    for rel, body in sorted(new.items()):
        cur = base / rel
        if cur.exists() and cur.read_bytes() == body:
            continue
        (kept if PurePosixPath(rel).name in RUNNING else changed).append(rel)
    backup = None
    if changed:
        bdir = base / "backups"
        bdir.mkdir(exist_ok=True)
        backup = bdir / f"code-{datetime.now():%Y-%m-%d-%H%M%S}.zip"
        with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as out:
            for rel in changed:
                if (base / rel).exists():
                    out.write(base / rel, rel)
        for rel in changed:
            dst = base / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            part = dst.with_name(dst.name + ".new")
            part.write_bytes(new[rel])
            os.replace(part, dst)
        log.info("yangilash: %s ta fayl almashtirildi, eskilari: %s", len(changed), backup.name)
    deps = "requirements.txt" in changed
    if deps:
        r = subprocess.run([python, "-m", "pip", "install", "-q", "-r", str(base / "requirements.txt"),
                            "--disable-pip-version-check"], capture_output=True, text=True, timeout=1200,
                           creationflags=NOWIN)
        if r.returncode != 0:
            log.warning("yangilash: pip: %s", (r.stderr or "")[-300:])
            deps = False
    return {"changed": changed, "kept": kept, "skipped": skipped, "check": summary, "backup": backup, "deps": deps}


def _loop_running(base: Path) -> bool:
    """Windows: start_bot.bat tsikli ishlayaptimi (u bot to'xtasa 10 soniyada qayta yoqadi)."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | "
                            "ForEach-Object { $_.CommandLine }"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                           creationflags=NOWIN)
    except Exception:  # noqa: BLE001
        return False
    want = str(base).rstrip("\\").lower()
    return any("start_bot.bat" in line.lower() and want in line.lower() for line in (r.stdout or "").splitlines())


def restart_plan(base: Path = BASE) -> str:
    """Bot to'xtagach uni kim qayta yoqadi: "loop" (start_bot.bat), "systemd", yoki "spawn" (o'zimiz yoqamiz)."""
    if os.name == "nt":
        return "loop" if _loop_running(base) else "spawn"
    if os.getenv("INVOCATION_ID"):   # systemd xizmati (deploy/setup.sh, Restart=always)
        return "systemd"
    return "spawn"


def spawn_new(base: Path = BASE) -> None:
    """Qayta yoqadigan tsikl yo'q — yangi nusxani 8 soniyadan keyin o'zimiz ishga tushiramiz: shu vaqtda eski
    nusxa chiqib ketadi va sayt porti hamda Telegram ulanishi bo'shaydi."""
    if os.name == "nt" and (base / "start_bot.bat").exists():
        cmd = f'timeout /t 8 /nobreak >nul & start "MalStore Bot" /min "{base / "start_bot.bat"}"'
        subprocess.Popen(["cmd.exe", "/c", cmd], cwd=base, creationflags=NOWIN)
    else:
        subprocess.Popen(["sh", "-c", f'sleep 8; exec "{sys.executable}" "{base / MAIN_FILE}"'], cwd=base,
                         start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
