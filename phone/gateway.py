#!/usr/bin/env python3
# Copyright 2026 Freiquell e.V., Fichtestraße 1-4, 08451 Crimmitschau, GERMANY www.rimbaud.io
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""rimBaud iPhone gateway — static-IP front door.

Serves the PWA and:
  GET  /api/fleet   META seats (hub Spring + VPN peers). Read-only.
  POST /api/bias    {"bias":"LONG"|"FLAT"|"SHORT"} → hub Spring desk_bias.
                    closes: false. Never CLOSE/OPEN.

Env:
  GATEWAY_TOKEN     required for POST (and GET if GATEWAY_AUTH_GET=1)
  HUB_SPRING        default http://127.0.0.1:5004
  META_JSON         default ../conf/meta.json
  GATEWAY_BIND      default 0.0.0.0
  GATEWAY_PORT      default 8787
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONF = Path(os.environ.get("META_JSON") or (ROOT / "conf" / "meta.json"))
HUB = (os.environ.get("HUB_SPRING") or "http://127.0.0.1:5004").rstrip("/")
TOKEN = (os.environ.get("GATEWAY_TOKEN") or "").strip()
AUTH_GET = os.environ.get("GATEWAY_AUTH_GET", "").strip().lower() in ("1", "true", "yes")
BIND = os.environ.get("GATEWAY_BIND") or "0.0.0.0"
PORT = int(os.environ.get("GATEWAY_PORT") or "8787")
VALID = ("LONG", "SHORT", "FLAT")


def _http(method: str, url: str, body: dict | None = None, timeout: float = 4.0) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw or "{}")
            except Exception:
                return r.status, {"ok": False, "raw": raw[:200]}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except Exception:
            return e.code, {"ok": False, "error": str(e)}
    except Exception as e:
        return 0, {"ok": False, "error": str(e)}


def load_sites() -> list[dict]:
    sites = [{"id": "hub", "name": "hub", "host": "127.0.0.1", "port": 5004, "role": "hub"}]
    try:
        j = json.loads(CONF.read_text())
        extra = j.get("sites") or []
        out = []
        for s in extra:
            if not isinstance(s, dict):
                continue
            host = str(s.get("host") or "")
            port = int(s.get("port") or 5004)
            if port == 5001:
                continue
            out.append({
                "id": str(s.get("id") or host),
                "name": str(s.get("name") or s.get("id") or host),
                "host": host,
                "port": port,
                "role": str(s.get("role") or "matrix"),
            })
        if out:
            return out
    except Exception:
        pass
    return sites


def snapshot(site: dict) -> dict:
    host, port = site["host"], site["port"]
    if host in ("127.0.0.1", "localhost"):
        url = f"{HUB}/api/home"
    else:
        url = f"http://{host}:{port}/api/home"
    code, d = _http("GET", url, timeout=3.5)
    card = {
        "id": site["id"],
        "name": site["name"],
        "host": host,
        "port": port,
        "role": site["role"],
        "ok": False,
        "vpn_up": code == 200,
        "bias": "-",
        "eq": 0,
        "upnl": 0,
        "n_pos": 0,
        "n_live": 0,
        "n_bots": 0,
        "ip_left": 0,
        "ip_cap": 1200,
        "error": "",
        "copy": "",
        "closes": False,
    }
    if code != 200 or not isinstance(d, dict):
        card["error"] = (d or {}).get("error") or f"HTTP {code}" or "down"
        return card
    card["ok"] = True
    card["bias"] = str((d.get("desk_bias") or {}).get("bias") or "-")
    t = d.get("totals") or {}
    card["eq"] = float(t.get("equity_usd") or 0)
    card["upnl"] = float(t.get("upnl_usd") or 0)
    card["n_pos"] = int(t.get("n_pos") or 0)
    card["n_live"] = int(t.get("n_live") or 0)
    card["n_bots"] = int(t.get("n_bots") or 0)
    r = d.get("rate") or {}
    card["ip_left"] = float(r.get("left") or 0)
    card["ip_cap"] = float(r.get("cap") or 1200)
    for b in d.get("bots") or []:
        if isinstance(b, dict) and b.get("id") == "hummingbot-hl":
            card["copy"] = str(b.get("address") or "")
            break
    return card


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(HERE), **k)

    def log_message(self, fmt, *args):
        sys_stderr = __import__("sys").stderr
        sys_stderr.write("[iphone-gw] " + (fmt % args) + "\n")

    def _auth_ok(self, need: bool) -> bool:
        if not need:
            return True
        if not TOKEN:
            return False
        h = self.headers.get("Authorization") or ""
        if h.startswith("Bearer "):
            return h[7:].strip() == TOKEN
        q = self.path.split("?", 1)
        if len(q) == 2 and f"token={TOKEN}" in q[1]:
            return True
        return False

    def _json(self, code: int, obj: dict):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/api/fleet", "/api/status"):
            if AUTH_GET and not self._auth_ok(True):
                return self._json(401, {"ok": False, "error": "auth"})
            seats = [snapshot(s) for s in load_sites()]
            n_up = sum(1 for s in seats if s["ok"])
            return self._json(200, {
                "ok": True,
                "closes": False,
                "n_up": n_up,
                "n": len(seats),
                "seats": seats,
                "note": "GET only. BIAS via POST /api/bias. Never CLOSE/OPEN.",
            })
        if path == "/api/health":
            return self._json(200, {"ok": True, "service": "rimbaud-iphone-gateway", "closes": False})
        if path == "/config.json":
            cfg = {
                "apiBase": "",
                "token": "",
                "pollMs": 4000,
            }
            p = HERE / "config.json"
            if p.is_file():
                try:
                    cfg.update(json.loads(p.read_text()))
                except Exception:
                    pass
            # never leak gateway token to the page by default
            cfg["token"] = cfg.get("publicToken") or ""
            return self._json(200, cfg)
        return super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/bias":
            return self._json(404, {"ok": False, "error": "no POST " + path, "closes": False})
        if not self._auth_ok(True):
            return self._json(401, {"ok": False, "error": "auth", "closes": False})
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return self._json(400, {"ok": False, "error": "bad json", "closes": False})
        bias = str(body.get("bias") or "").upper()
        if bias not in VALID:
            return self._json(400, {"ok": False, "error": "bias must be LONG, FLAT or SHORT", "closes": False})
        code, resp = _http("POST", f"{HUB}/api/home/desk_bias", {"bias": bias}, timeout=6)
        ok = code == 200 and (resp.get("ok") is True or resp.get("bias") == bias)
        return self._json(200 if ok else 502, {
            "ok": bool(ok),
            "closes": False,
            "bias": bias,
            "positions_untouched": True,
            "upstream": code,
            "error": None if ok else (resp.get("error") or "spring desk_bias failed"),
        })

    def do_PUT(self):
        self._json(403, {"ok": False, "error": "no PUT", "closes": False})

    def do_DELETE(self):
        self._json(403, {"ok": False, "error": "no DELETE", "closes": False})


def main():
    os.chdir(HERE)
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"rimBaud iPhone gateway  http://{BIND}:{PORT}/", flush=True)
    print("  GET  /api/fleet   POST /api/bias  (never CLOSE)", flush=True)
    if not TOKEN:
        print("  WARN  GATEWAY_TOKEN empty — POST /api/bias will 401", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
