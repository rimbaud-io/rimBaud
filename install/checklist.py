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

"""Post-install checkboxes. SPACE = mark OK. ENTER = finish.
Probes Docker, hunters, dashboard, Spring, VPN, wallets.
Does not CLOSE/OPEN clips. Does not write hunter keys.
"""
from __future__ import annotations

import curses
import json
import os
import socket
import subprocess
import sys
import urllib.request

INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(INSTALL_DIR, ".."))


def _sh(cmd: str, timeout: int = 8) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 400
    except Exception:
        return False


def probe_docker() -> tuple[bool, str]:
    rc, out = _sh("docker info >/dev/null 2>&1")
    return rc == 0, "Docker engine" if rc == 0 else "Docker down"


def probe_hunters() -> tuple[bool, str]:
    path = os.path.join(INSTALL_DIR, "services.list")
    want = []
    if os.path.isfile(path):
        for line in open(path):
            s = line.strip()
            if s and not s.startswith("#"):
                want.append(s)
    rc, out = _sh("docker ps --format '{{.Names}}'")
    have = set(out.split())
    n = sum(1 for w in want if w in have)
    ok = n >= 8
    return ok, "Hunters %d/%d running" % (n, len(want) or 8)


def probe_dash() -> tuple[bool, str]:
    ok = _http_ok("http://127.0.0.1:5001/")
    return ok, "Dashboard :5001" + (" OK" if ok else " down")


def probe_spring() -> tuple[bool, str]:
    ok = _http_ok("http://127.0.0.1:5004/api/health")
    return ok, "Spring :5004" + (" OK" if ok else " down")


def probe_vpn() -> tuple[bool, str]:
    rc, out = _sh("ip -4 -o addr show wg-rimbaud 2>/dev/null | awk '{print $4}'")
    addr = (out or "").strip()
    if addr:
        return True, "VPN %s" % addr.split("/")[0]
    if os.path.isfile(os.path.join(ROOT, "JOIN.conf")) or os.path.isfile(
        os.path.join(ROOT, "conf/vpn/JOIN.conf")
    ):
        return False, "VPN join file present — not up yet"
    return False, "VPN not joined (optional on hub)"


def probe_wallets() -> tuple[bool, str]:
    p = os.path.join(ROOT, "conf/bot_accounts.json")
    if not os.path.isfile(p):
        return False, "Copy master 0x — press s"
    try:
        d = json.load(open(p))
    except Exception:
        return False, "Copy master 0x — bad file"
    copy = ""
    for b in d.get("bots") or []:
        if (b or {}).get("id") == "hummingbot-hl":
            copy = str((b or {}).get("address") or "").strip()
            break
    ok = copy.startswith("0x") and len(copy) == 42
    if ok:
        return True, "Copy master  " + copy[:10] + "…  (subs later, after $100k vol)"
    return False, "Copy master 0x — press s  (one ETH address only)"


ITEMS = [
    ("docker", probe_docker, False),
    ("hunters", probe_hunters, False),
    ("dash", probe_dash, False),
    ("spring", probe_spring, False),
    ("vpn", probe_vpn, True),
    ("wallets", probe_wallets, True),
]


def probe_all():
    rows = []
    for key, fn, optional in ITEMS:
        ok, label = fn()
        rows.append({"key": key, "ok": ok, "label": label, "optional": optional, "tick": ok})
    return rows


def run_curses(stdscr, rows):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_RED, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    sel = 0
    msg = "SPACE tick OK   ENTER finish   r re-probe   s wallets   q abort"
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        stdscr.addnstr(0, 1, "rimBaud install  —  checkboxes after OK", w - 2, curses.A_BOLD)
        stdscr.addnstr(1, 1, "No CLOSE/OPEN. New account: Copy master 0x only. Do not paste hub wallets.", w - 2)
        y = 3
        for i, r in enumerate(rows):
            mark = "[x]" if r["tick"] else "[ ]"
            auto = "OK" if r["ok"] else ("skip?" if r["optional"] else "NEED")
            col = curses.color_pair(1 if r["ok"] else (2 if r["optional"] else 3))
            prefix = ">" if i == sel else " "
            line = "%s %s  %s  %s" % (prefix, mark, r["label"], auto)
            attr = curses.A_REVERSE | col if i == sel else col
            stdscr.addnstr(y, 1, line, w - 2, attr)
            y += 1
        y += 1
        stdscr.addnstr(y, 1, msg, w - 2, curses.color_pair(4))
        stdscr.refresh()
        ch = stdscr.getch()
        if ch in (curses.KEY_UP, ord("k")):
            sel = (sel - 1) % len(rows)
        elif ch in (curses.KEY_DOWN, ord("j")):
            sel = (sel + 1) % len(rows)
        elif ch in (ord(" "),):
            rows[sel]["tick"] = not rows[sel]["tick"]
        elif ch in (ord("r"), ord("R")):
            rows = probe_all()
        elif ch in (ord("s"), ord("S")):
            curses.endwin()
            os.system("python3 %s" % os.path.join(INSTALL_DIR, "setup_tui.py"))
            rows = probe_all()
            stdscr.clear()
        elif ch in (ord("q"), 27):
            return 1
        elif ch in (curses.KEY_ENTER, 10, 13):
            missing = [r for r in rows if not r["optional"] and not r["tick"]]
            if missing:
                msg = "still need: " + ", ".join(r["label"] for r in missing)
                continue
            return 0


def main() -> int:
    rows = probe_all()
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("rimBaud install checklist")
        for r in rows:
            print((" [x] " if r["ok"] else " [ ] ") + r["label"])
        return 0 if all(r["ok"] or r["optional"] for r in rows) else 1
    try:
        return curses.wrapper(lambda s: run_curses(s, rows))
    except Exception:
        for r in rows:
            print((" [x] " if r["ok"] else " [ ] ") + r["label"])
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
