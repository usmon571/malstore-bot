"""Мини-веб-сервер (stdlib): дашборд + API. Запускается в потоке рядом с ботом."""
from __future__ import annotations

import hmac
import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import core
import ledger

log = logging.getLogger("expenses.web")
HTML = (core.BASE / "dashboard.html").read_bytes()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # тишина в логах (в URL ключ!)
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _auth(self, qs) -> bool:
        key = self.headers.get("X-Key") or (qs.get("key") or [""])[0]
        return hmac.compare_digest(key.encode(), core.secret().encode())

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path == "/health":
            return self._send(200, b'{"ok":true}')
        if not self._auth(qs):
            return self._send(401, b'{"error":"unauthorized"}')
        if u.path == "/":
            return self._send(200, HTML, "text/html; charset=utf-8")
        if u.path == "/api/data":
            period = (qs.get("period") or ["month"])[0]
            if period not in ("today", "yesterday", "week", "month", "lastmonth", "all") and not period.isdigit():
                period = "month"
            wallet = (qs.get("wallet") or [""])[0]
            wallet = wallet if wallet in ("personal", "business") else None
            return self._send(200, json.dumps(ledger.summary(period, wallet), ensure_ascii=False).encode())
        if u.path == "/api/export.csv":
            return self._send(200, ledger.export_csv().encode("utf-8-sig"), "text/csv; charset=utf-8")
        self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if not self._auth(qs):
            return self._send(401, b'{"error":"unauthorized"}')
        n = min(int(self.headers.get("Content-Length") or 0), 20000)
        body = self.rfile.read(n).decode("utf-8", "replace")
        if u.path == "/api/ingest":
            text, source = body, (qs.get("source") or ["wallet"])[0][:12]
            try:
                d = json.loads(body)
                if isinstance(d, dict):
                    # Shortcuts: {"amount": "UZS 15,000.00", "merchant": "Korzinka"}
                    if d.get("amount"):
                        amount = str(d["amount"]).strip()
                        if not re.search(r"[A-Za-zА-Яа-яЎў$€₽]", amount):
                            amount += " UZS"  # Shortcuts иногда отдаёт голое число
                        # сумма отдельной строкой — чтобы цифры в названии магазина не спутались с суммой
                        text = f"Оплата: {d.get('merchant') or ''}\n{amount}"
                    else:
                        text = " ".join(str(d.get(k, "")) for k in ("merchant", "amount", "text") if d.get(k))
                    source = str(d.get("source") or source)[:12]
            except (ValueError, TypeError):
                pass
            ok, msg = core.ingest_text(text, source)
            return self._send(200 if ok else 422, json.dumps({"ok": ok, "result": msg}, ensure_ascii=False).encode())
        self._send(404, b'{"error":"not found"}')


def start() -> None:
    srv = ThreadingHTTPServer(("0.0.0.0", core.PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True, name="expenses-web").start()
    log.info("Веб-сервер: порт %s", core.PORT)
