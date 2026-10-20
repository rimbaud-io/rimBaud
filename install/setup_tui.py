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

"""Freiquell setup TUI — Hyperliquid wallets, password, optional Telegram.

Run from the kit:
  python3 install/setup_tui.py
  ./install/setup.sh

Writes .env, conf/bot_accounts.json, and encrypted connector YAML
(same v3 keyfile Hummingbot / Confiig uses). Needs a running hunter
container to encrypt. Does not place orders.
"""
from __future__ import annotations

import curses
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(INSTALL_DIR, ".."))
ENV_FILE = os.path.join(ROOT, ".env")
ENV_EXAMPLE = os.path.join(INSTALL_DIR, "env.example")
ACCOUNTS = os.path.join(ROOT, "conf", "bot_accounts.json")
TEMPLATE = os.path.join(INSTALL_DIR, "templates", "hyperliquid_perpetual.yml")

HUNTERS = (
    ("hummingbot-hl", "Copy", "conf/connectors/hyperliquid_perpetual.yml", False),
    ("hummingbot-bn-swing", "Swing", "conf-swing/connectors/hyperliquid_perpetual.yml", True),
    ("hummingbot-helliot", "Helliot", "conf-helliot/connectors/hyperliquid_perpetual.yml", True),
    ("hummingbot-surfn", "Surfn", "conf-surfn/connectors/hyperliquid_perpetual.yml", True),
    ("hummingbot-shortn", "Shorty", "conf-shortn/connectors/hyperliquid_perpetual.yml", True),
    ("hummingbot-dman", "Dman", "conf-dman/connectors/hyperliquid_perpetual.yml", True),
    ("hummingbot-onlyup", "Only Up", "conf-onlyup/connectors/hyperliquid_perpetual.yml", True),
    ("hummingbot-dman-maker", "DMan Maker", "conf-dman-maker/connectors/hyperliquid_perpetual.yml", True),
)
ENCRYPT_CONTAINERS = [h[0] for h in HUNTERS]
ADDR_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
KEY_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


def _utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _read_env(path):
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _write_env(path, updates):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if os.path.isfile(path):
        with open(path) as f:
            lines = f.read().splitlines()
    elif os.path.isfile(ENV_EXAMPLE):
        with open(ENV_EXAMPLE) as f:
            lines = f.read().splitlines()
    else:
        lines = []
    seen = set()
    new = []
    for line in lines:
        raw = line.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            k = raw.split("=", 1)[0].strip()
            if k in updates:
                new.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        new.append(line)
    for k, v in updates.items():
        if k not in seen:
            new.append(f"{k}={v}")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(new).rstrip() + "\n")
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _load_accounts():
    try:
        with open(ACCOUNTS) as f:
            data = json.load(f) or {}
        if isinstance(data.get("bots"), list):
            return data
    except Exception:
        pass
    return {"bots": [], "updated": None, "source": "install"}


def _host_yml(rel):
    return os.path.join(ROOT, rel)


def _ensure_yml(rel):
    path = _host_yml(rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.isfile(path):
        return path
    if not os.path.isfile(TEMPLATE):
        raise RuntimeError(f"missing template {TEMPLATE}")
    with open(TEMPLATE) as f:
        text = f.read()
    with open(path, "w") as f:
        f.write(text)
    return path


def _valid_addr(s):
    return bool(ADDR_RE.fullmatch(str(s or "").strip()))


def _valid_key(s):
    k = str(s or "").strip()
    return (not k) or bool(KEY_RE.fullmatch(k))


def encrypt_value(plain, password):
    script = (
        "import json, os\n"
        "from hummingbot.client.config.config_crypt import _create_v3_keyfile_json\n"
        "val = os.environ['HL_VAL'].encode()\n"
        "pw = os.environ['HL_PW'].encode()\n"
        "kf = _create_v3_keyfile_json(val, pw)\n"
        "print(json.dumps(kf).encode().hex())\n"
    )
    last = "no hummingbot container running to encrypt"
    env_base = os.environ.copy()
    for name in ENCRYPT_CONTAINERS:
        try:
            r = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", name],
                capture_output=True, text=True, timeout=8,
            )
            if r.returncode != 0 or (r.stdout or "").strip() != "true":
                continue
        except Exception as e:
            last = str(e)
            continue
        try:
            r = subprocess.run(
                ["docker", "exec", "-e", f"HL_VAL={plain}", "-e", f"HL_PW={password}",
                 name, "python3", "-c", script],
                capture_output=True, text=True, timeout=30,
                env=env_base,
            )
            hexout = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else ""
            if r.returncode == 0 and re.fullmatch(r"[0-9a-fA-F]+", hexout) and len(hexout) > 80:
                return hexout, None
            last = (r.stderr or r.stdout or "encrypt failed")[:400]
        except Exception as e:
            last = str(e)
    # USB / new box: hunter may be restarting — encrypt in a one-shot image.
    try:
        r = subprocess.run(
            ["docker", "run", "--rm", "-e", f"HL_VAL={plain}", "-e", f"HL_PW={password}",
             "--entrypoint", "python3", "hummingbot/hummingbot:latest", "-c", script],
            capture_output=True, text=True, timeout=90, env=env_base,
        )
        hexout = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else ""
        if r.returncode == 0 and re.fullmatch(r"[0-9a-fA-F]+", hexout) and len(hexout) > 80:
            return hexout, None
        last = (r.stderr or r.stdout or last or "encrypt failed")[:400]
    except Exception as e:
        last = str(e)
    return None, last


def _set_yml_line(text, key, value):
    if re.search(rf"(?m)^{re.escape(key)}:\s*\S+", text):
        return re.sub(rf"(?m)^{re.escape(key)}:\s*\S+", f"{key}: {value}", text, count=1)
    return text.rstrip() + f"\n{key}: {value}\n"


def write_connector_yml(path, address_hex, secret_hex, use_vault, mode="api_wallet"):
    with open(path) as f:
        text = f.read()
    vault = "true" if use_vault else "false"
    text = _set_yml_line(text, "hyperliquid_perpetual_mode", mode)
    text = _set_yml_line(text, "use_vault", vault)
    if address_hex:
        text = _set_yml_line(text, "hyperliquid_perpetual_address", address_hex)
    if secret_hex:
        text = _set_yml_line(text, "hyperliquid_perpetual_secret_key", secret_hex)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def restart_container(name):
    # Recreate so CONFIG_PASSWORD from .env is injected (plain docker restart keeps old env).
    r = subprocess.run(
        ["docker", "compose", "--project-directory", ROOT, "up", "-d", "--force-recreate", name],
        capture_output=True, text=True, timeout=180,
    )
    if r.returncode != 0:
        r2 = subprocess.run(["docker", "restart", name], capture_output=True, text=True, timeout=90)
        if r2.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or r2.stderr or "restart failed").strip()[:400])


def apply_setup(state):
    pw = (state.get("password") or "").strip()
    if not pw or pw == "change-me":
        return False, "set CONFIG_PASSWORD (not change-me)"
    secret = (state.get("api_key") or "").strip()
    addrs = state.get("addr") or {}
    vaults = state.get("vault") or {}
    filled = {hid: addrs.get(hid, "").strip() for hid, *_ in HUNTERS if _valid_addr(addrs.get(hid, "").strip())}
    if not filled:
        return False, "paste the Copy master 0x (one ETH address)"
    if "hummingbot-hl" not in filled:
        return False, "Copy (master) 0x is required — other hunters can wait weeks"
    seen = {}
    for hid, addr in filled.items():
        a = addr.lower()
        if a in seen:
            return False, f"duplicate address: {seen[a]} and {hid}"
        seen[a] = hid
    if secret and not _valid_key(secret):
        return False, "API secret must be 0x + 64 hex chars (or empty to keep existing)"
    need_secret = False
    for hid, _n, rel, _v in HUNTERS:
        if hid not in filled:
            continue
        path = _host_yml(rel)
        if not os.path.isfile(path):
            need_secret = True
            continue
        with open(path) as f:
            body = f.read()
        if "PLACEHOLDER" in body or "hyperliquid_perpetual_secret_key:" not in body:
            need_secret = True
    if need_secret and not secret:
        return False, "HL API secret required (0x + 64 hex) for new connector files"

    env_upd = {
        "CONFIG_PASSWORD": pw,
        "HL_COPY_WALLET": filled.get("hummingbot-hl", ""),
        "TELEGRAM_BOT_TOKEN": (state.get("tg_token") or "").strip(),
        "TELEGRAM_CHAT_IDS": (state.get("tg_chats") or "").strip(),
        "XAI_API_KEY": (state.get("xai") or "").strip(),
    }
    _write_env(ENV_FILE, env_upd)

    secret_hex = None
    if secret:
        secret_hex, err = encrypt_value(secret, pw)
        if not secret_hex:
            return False, f"encrypt API key failed: {err}"

    accounts = _load_accounts()
    by_id = {b.get("id"): b for b in (accounts.get("bots") or []) if b.get("id")}
    restarted = []
    errors = []
    for hid, name, rel, default_vault in HUNTERS:
        addr = filled.get(hid)
        if not addr:
            continue
        use_vault = bool(vaults.get(hid, default_vault))
        path = _ensure_yml(rel)
        enc, err = encrypt_value(addr, pw)
        if not enc:
            errors.append(f"{name}: encrypt address failed ({err})")
            continue
        try:
            mode = "arb_wallet" if use_vault else "api_wallet"
            write_connector_yml(path, enc, secret_hex, use_vault, mode=mode)
        except Exception as e:
            errors.append(f"{name}: yml write {e}")
            continue
        bot = by_id.get(hid) or {
            "id": hid, "name": name, "container": hid,
            "yml": "/" + rel, "notes": "",
        }
        bot["address"] = addr
        bot["use_vault"] = use_vault
        bot["yml"] = "/" + rel
        bot["container"] = hid
        by_id[hid] = bot
        try:
            restart_container(hid)
            restarted.append(name)
        except Exception as e:
            errors.append(f"{name}: saved but restart failed ({e})")

    accounts["bots"] = [by_id[h[0]] for h in HUNTERS if h[0] in by_id] + [
        b for b in (accounts.get("bots") or []) if b.get("id") not in {h[0] for h in HUNTERS}
    ]
    accounts["updated"] = _utc()
    accounts["source"] = "setup-tui"
    os.makedirs(os.path.dirname(ACCOUNTS), exist_ok=True)
    tmp = ACCOUNTS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(accounts, f, indent=2)
        f.write("\n")
    os.replace(tmp, ACCOUNTS)

    if os.environ.get("RIMBAUD_BOOTSTRAP", "1") != "0":
        extras = [h[0] for h in HUNTERS if h[0] != "hummingbot-hl"]
        subprocess.run(
            ["docker", "compose", "--project-directory", ROOT, "stop", *extras],
            capture_output=True, text=True, timeout=120,
        )

    msg = f"saved {len(filled)} wallet(s) — Copy MASTER 0x (not API agent). Transfer USDC Spot→Perps on HL."
    if restarted:
        msg += "; recreated " + ", ".join(restarted)
    if errors:
        msg += " | " + "; ".join(errors[:3])
        return False, msg
    return True, msg


def load_state():
    env = _read_env(ENV_FILE)
    accounts = _load_accounts()
    by_id = {b.get("id"): b for b in (accounts.get("bots") or [])}
    addr = {}
    vault = {}
    for hid, _n, _r, default_vault in HUNTERS:
        bot = by_id.get(hid) or {}
        addr[hid] = str(bot.get("address") or "")
        vault[hid] = bool(bot["use_vault"]) if "use_vault" in bot else default_vault
    return {
        "password": env.get("CONFIG_PASSWORD") or "",
        "api_key": "",
        "tg_token": env.get("TELEGRAM_BOT_TOKEN") or "",
        "tg_chats": env.get("TELEGRAM_CHAT_IDS") or "",
        "xai": env.get("XAI_API_KEY") or "",
        "addr": addr,
        "vault": vault,
    }


def fallback_prompts():
    st = load_state()
    print("FREIQUELL setup (plain prompts — no curses)")
    print("Empty line keeps the current value. Unique 0x per hunter.")
    pw = input(f"CONFIG_PASSWORD [{'(set)' if st['password'] else 'empty'}]: ").strip()
    if pw:
        st["password"] = pw
    key = input("HL API secret key 0x…64 hex (empty = keep): ").strip()
    if key:
        st["api_key"] = key
    for hid, name, _r, default_vault in HUNTERS:
        cur = st["addr"].get(hid) or ""
        raw = input(f"{name:12} address [{cur[:10] + '…' if len(cur) > 12 else cur or 'empty'}]: ").strip()
        if raw:
            st["addr"][hid] = raw
        v = input(f"{name:12} use vault [{'Y' if st['vault'].get(hid, default_vault) else 'n'}] (y/n/empty): ").strip().lower()
        if v in ("y", "yes"):
            st["vault"][hid] = True
        elif v in ("n", "no"):
            st["vault"][hid] = False
    tg = input(f"Telegram bot token [{'(set)' if st['tg_token'] else 'empty'}]: ").strip()
    if tg:
        st["tg_token"] = tg
    chats = input(f"Telegram chat ids [{st['tg_chats'] or 'empty'}]: ").strip()
    if chats:
        st["tg_chats"] = chats
    xai = input(f"XAI_API_KEY [{'(set)' if st['xai'] else 'empty'}]: ").strip()
    if xai:
        st["xai"] = xai
    ok, msg = apply_setup(st)
    print(("OK " if ok else "ERR ") + msg)
    return 0 if ok else 1


# Field ids for the form
def field_list():
    fields = [
        ("password", "CONFIG_PASSWORD", "pw"),
        ("api_key", "HL API agent key 0x+64 (not the master)", "pw"),
    ]
    for hid, name, _r, _v in HUNTERS:
        if hid == "hummingbot-hl":
            fields.append((f"vault:{hid}", f"{name} vault (leave OFF)", "chk"))
            fields.append((f"addr:{hid}", f"{name} MASTER 0x (not API agent addr)", "addr"))
        else:
            fields.append((f"vault:{hid}", f"{name} vault", "chk"))
            fields.append((f"addr:{hid}", f"{name} 0x (empty for now)", "addr"))
    fields.extend([
        ("tg_token", "Telegram bot token", "pw"),
        ("tg_chats", "Telegram chat ids", "text"),
        ("xai", "XAI_API_KEY", "pw"),
        ("save", "SAVE & APPLY", "btn"),
        ("skip", "SKIP (Confiig later)", "btn"),
    ])
    return fields


def get_val(st, fid):
    if fid == "password":
        return st["password"]
    if fid == "api_key":
        return st["api_key"]
    if fid == "tg_token":
        return st["tg_token"]
    if fid == "tg_chats":
        return st["tg_chats"]
    if fid == "xai":
        return st["xai"]
    if fid.startswith("addr:"):
        return st["addr"].get(fid.split(":", 1)[1], "")
    if fid.startswith("vault:"):
        return "1" if st["vault"].get(fid.split(":", 1)[1]) else ""
    return ""


def set_val(st, fid, val):
    if fid == "password":
        st["password"] = val
    elif fid == "api_key":
        st["api_key"] = val
    elif fid == "tg_token":
        st["tg_token"] = val
    elif fid == "tg_chats":
        st["tg_chats"] = val
    elif fid == "xai":
        st["xai"] = val
    elif fid.startswith("addr:"):
        st["addr"][fid.split(":", 1)[1]] = val
    elif fid.startswith("vault:"):
        st["vault"][fid.split(":", 1)[1]] = bool(val)


def show_val(kind, raw, focused):
    if kind == "chk":
        return "[x]" if raw else "[ ]"
    if kind == "pw":
        if not raw:
            return ""
        return "*" * min(len(raw), 24)
    if not focused and kind == "addr" and len(raw) > 16:
        return raw[:10] + "…" + raw[-6:]
    return raw


def run_curses(stdscr):
    curses.curs_set(1)
    curses.use_default_colors()
    if curses.has_colors():
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(3, curses.COLOR_GREEN, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
        curses.init_pair(5, curses.COLOR_YELLOW, -1)
    st = load_state()
    fields = field_list()
    idx = 0
    status = "Tab/arrows move · Space toggles vault · F10 save · Esc skip"
    status_ok = True
    stdscr.keypad(True)
    curses.cbreak()

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        title = " FREIQUELL  //  SETUP "
        try:
            stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
            stdscr.addstr(0, max(0, (w - len(title)) // 2), title[: w - 1])
            stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
            stdscr.addstr(1, 2, "New HL account: paste Copy MASTER 0x only. Other hunters stay empty until $100k volume / subaccounts."[: w - 3])
        except curses.error:
            pass
        row = 3
        positions = []
        for i, (fid, label, kind) in enumerate(fields):
            if row >= h - 3:
                break
            raw = get_val(st, fid)
            shown = show_val(kind, raw, i == idx)
            prefix = ">" if i == idx else " "
            line_l = f"{prefix} {label:<22}"
            try:
                if i == idx:
                    stdscr.attron(curses.color_pair(2) | curses.A_BOLD)
                stdscr.addstr(row, 1, line_l[:28])
                if i == idx:
                    stdscr.attroff(curses.color_pair(2) | curses.A_BOLD)
                val_x = 30
                room = max(8, w - val_x - 2)
                if kind == "chk":
                    stdscr.addstr(row, val_x, shown)
                elif kind == "btn":
                    stdscr.addstr(row, val_x, "[Enter]")
                else:
                    bad = (kind == "addr" and raw and not _valid_addr(raw)) or (
                        kind == "pw" and fid == "api_key" and raw and not _valid_key(raw)
                    )
                    if bad:
                        stdscr.attron(curses.color_pair(4))
                    stdscr.addstr(row, val_x, shown[:room])
                    if bad:
                        stdscr.attroff(curses.color_pair(4))
            except curses.error:
                pass
            positions.append((row, 30))
            row += 1

        try:
            stdscr.addstr(h - 2, 2, "F10 save & apply (encrypt + restart)   Esc skip"[: w - 3])
            col = curses.color_pair(3) if status_ok else curses.color_pair(4)
            stdscr.attron(col)
            stdscr.addstr(h - 1, 2, status[: w - 3])
            stdscr.attroff(col)
        except curses.error:
            pass

        fid, label, kind = fields[idx]
        cur = get_val(st, fid)
        if kind != "chk" and idx < len(positions):
            y, x = positions[idx]
            shown = show_val(kind, cur, True)
            curses.curs_set(1)
            try:
                stdscr.move(y, min(w - 2, x + len(shown)))
            except curses.error:
                pass
        else:
            curses.curs_set(0)

        stdscr.refresh()
        ch = stdscr.get_wch() if hasattr(stdscr, "get_wch") else stdscr.getch()

        def next_field(delta):
            nonlocal idx
            idx = (idx + delta) % len(fields)

        if ch in (curses.KEY_F10, 19):  # F10 or Ctrl+S
            status_ok, status = apply_setup(st)
            if status_ok:
                return 0
            continue
        if ch in (27,):  # Esc
            return 2
        if kind == "btn" and ch in (" ", "\n", 10, 13):
            if fid == "save":
                status = "encrypting…"
                status_ok = True
                stdscr.refresh()
                status_ok, status = apply_setup(st)
                if status_ok:
                    return 0
            else:
                return 2
            continue
        if ch in (curses.KEY_UP,):
            next_field(-1)
            continue
        if ch in (curses.KEY_DOWN, "\t", 9):
            next_field(1)
            continue
        if ch in (curses.KEY_BTAB,):
            next_field(-1)
            continue
        if kind == "chk" and ch in (" ", "\n", 10, 13):
            set_val(st, fid, "" if get_val(st, fid) else "1")
            continue
        if ch in ("\n", 10, 13):
            next_field(1)
            continue
        if kind == "chk":
            continue
        if ch in (curses.KEY_BACKSPACE, 127, 8, "\b"):
            set_val(st, fid, cur[:-1])
            continue
        if isinstance(ch, str) and ch.isprintable():
            set_val(st, fid, cur + ch)
            continue
        if isinstance(ch, int) and 32 <= ch < 127:
            set_val(st, fid, cur + chr(ch))
            continue
    return 2


def main():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("No TTY — use Confiig in the browser: http://127.0.0.1:5001/confiig", file=sys.stderr)
        return 2
    if os.environ.get("FREIQUELL_SETUP_PLAIN") == "1":
        return fallback_prompts()
    try:
        return curses.wrapper(run_curses)
    except curses.error:
        return fallback_prompts()


if __name__ == "__main__":
    sys.exit(main() or 0)
