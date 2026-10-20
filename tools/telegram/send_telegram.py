#!/usr/bin/env python3
# Copyright 2026 Laurence Sterne www.rimbaud.io
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

"""Send Koordinator text to the user Telegram bot. Does not trade or close."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SECRETS = ROOT / "secrets.env"
CONF = Path(os.environ.get("CONF_DIR") or (ROOT.parent / "conf"))
KOORD_CMD = CONF / "koordinator_cmd.json"

BIAS_MAP = {
    "long": ("LONG", "/long"),
    "/long": ("LONG", "/long"),
    "up": ("LONG", "/long"),
    "short": ("SHORT", "/short"),
    "/short": ("SHORT", "/short"),
    "down": ("SHORT", "/short"),
    "flat": ("FLAT", "/flat"),
    "/flat": ("FLAT", "/flat"),
    "hold": ("FLAT", "/flat"),
    "none": ("FLAT", "/flat"),
}


def load_secrets():
    env = dict(os.environ)
    if SECRETS.is_file():
        for line in SECRETS.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and not env.get(k):
                env[k] = v
    return env


def tg_api(token, method, params=None, timeout=30):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params is None:
        req = urllib.request.Request(url, method="GET")
    else:
        data = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except Exception:
            return {"ok": False, "error": body, "status": e.code}


def parse_bias(raw):
    key = str(raw or "").strip().lower()
    if not key:
        return None
    import re
    m = re.search(r"/(long|short|flat)\b", key)
    if m:
        return BIAS_MAP.get(m.group(1))
    m = re.search(r"please\s+post\s+(long|short|flat|up|down|hold)\b", key)
    if m:
        return BIAS_MAP.get(m.group(1))
    m = re.fullmatch(r"[\s]*(long|short|flat|up|down|hold)[\s.!;]*", key)
    if m:
        return BIAS_MAP.get(m.group(1))
    key = key.splitlines()[0].split()[0]
    return BIAS_MAP.get(key)


def write_koord_cmd(mode, cmd):
    """Drop a command Condor will apply. Telegram getUpdates ignores bot-sent messages."""
    from datetime import datetime, timezone
    payload = {
        "id": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "bias": mode,
        "cmd": cmd,
        "source": "koordinator",
        "closes": False,
    }
    KOORD_CMD.write_text(json.dumps(payload) + "\n")
    return payload


def post_desk_bias(mode):
    """Apply desk BIAS the same way Home / Telegram /long does. Does not close."""
    env = load_secrets()
    url = (env.get("DASHBOARD_URL") or "http://127.0.0.1:5001").rstrip("/") + "/api/home/desk_bias"
    body = json.dumps({"bias": mode}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except Exception:
            return {"ok": False, "error": raw}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_bias(raw):
    parsed = parse_bias(raw)
    if not parsed:
        return {"ok": False, "error": "bias must be /long /short /flat (or UP/DOWN/HOLD)"}
    mode, cmd = parsed
    drop = write_koord_cmd(mode, cmd)
    # Report only: post /long /short /flat to BIAS BOT. Never Desk BIAS / hunters.
    tg = send(cmd)
    return {
        "ok": bool(tg.get("ok")),
        "cmd": cmd,
        "mode": mode,
        "writes_hunters": False,
        "drop": drop.get("id"),
        "telegram": {"ok": tg.get("ok"), "description": tg.get("description") or tg.get("error")},
    }


def send(text, chat_id=None):
    env = load_secrets()
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = str(chat_id or env.get("TELEGRAM_CHAT_ID") or env.get("TELEGRAM_CHAT_IDS") or "").split(",")[0].strip()
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN missing in grok_bot/secrets.env"}
    if not chat:
        return {"ok": False, "error": "TELEGRAM_CHAT_ID missing — write the bot /start, then run --whoami"}
    return tg_api(token, "sendMessage", {
        "chat_id": chat,
        "text": text,
        "disable_web_page_preview": True,
    })


def whoami():
    env = load_secrets()
    token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        return {"ok": False, "error": "no token"}
    me = tg_api(token, "getMe")
    updates = tg_api(token, "getUpdates", {"timeout": 0, "limit": 20})
    chats = []
    for upd in (updates.get("result") or []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None:
            continue
        row = {
            "id": cid,
            "type": chat.get("type"),
            "name": chat.get("title") or " ".join(
                x for x in (chat.get("first_name"), chat.get("username")) if x
            ),
        }
        if row not in chats:
            chats.append(row)
    return {"ok": bool(me.get("ok")), "me": me.get("result"), "chats": chats, "updates_ok": updates.get("ok")}


def main(argv):
    argv = list(argv)
    if "--whoami" in argv:
        out = whoami()
        print(json.dumps(out, indent=2))
        return 0 if out.get("ok") else 1
    if "--bias" in argv:
        i = argv.index("--bias")
        raw = " ".join(argv[i + 1:]).strip() or (argv[i + 1] if len(argv) > i + 1 else "")
        if not raw:
            print("usage: send_telegram.py --bias /long|/short|/flat")
            return 2
        out = send_bias(raw)
        print(json.dumps({k: out.get(k) for k in ("ok", "cmd", "mode", "error") if k in out}))
        desk = out.get("desk") or {}
        if desk.get("message"):
            print(desk.get("message"))
        if out.get("ok"):
            print("wired " + out.get("cmd"))
            return 0
        print(out.get("error") or desk.get("error") or out)
        return 1
    if "--test" in argv:
        text = "Koordinator posts only /long /short /flat. No trade. No close."
    elif "--text" in argv:
        i = argv.index("--text")
        text = " ".join(argv[i + 1:]).strip()
        if not text:
            print("usage: send_telegram.py --text MESSAGE")
            return 2
    else:
        print("usage: send_telegram.py --whoami | --test | --bias /long|/short|/flat | --text MESSAGE")
        return 2
    first = (text.splitlines()[0] if text else "").strip()
    if parse_bias(first) and len(first.split()) <= 2:
        out = send_bias(first)
        print(json.dumps({k: out.get(k) for k in ("ok", "cmd", "mode", "error") if k in out}))
        if out.get("ok"):
            print("wired " + out.get("cmd"))
            return 0
        print(out.get("error") or out)
        return 1
    out = send(text)
    print(json.dumps({k: out.get(k) for k in ("ok", "error", "description") if k in out or k == "ok"}))
    if out.get("ok"):
        print("sent")
        return 0
    print(out.get("description") or out.get("error") or out)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
