#!/usr/bin/env python3
# Copyright 2026 rimbaud.io
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

"""rimBaud Archy — version sqlite. Grok may only --bump-g. Operator LEVEL UP is the C++ desk."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONF = os.environ.get("RIMBAUD_CONF") or os.environ.get("FREIQUELL_CONF") or os.path.join(ROOT, "conf")
DB = os.environ.get("ARCHY_DB") or os.path.join(CONF, "archy.db")
JSON = os.path.join(CONF, "archy.json")
DEPLOY_JSON = os.path.join(CONF, "archy_deploys.json")


def _utc():
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def _display(major: int, minor: int, grok: int) -> str:
    if grok and grok > 0:
        return f"{major}.{minor}g{grok}"
    return f"{major}.{minor}"


def _con():
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    c = sqlite3.connect(DB, timeout=8)
    c.row_factory = sqlite3.Row
    c.execute(
        """CREATE TABLE IF NOT EXISTS archy_state (
            id INTEGER PRIMARY KEY CHECK (id=1),
            major INTEGER NOT NULL,
            minor INTEGER NOT NULL,
            grok INTEGER NOT NULL DEFAULT 0,
            display TEXT NOT NULL,
            updated TEXT NOT NULL
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS archy_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            who TEXT NOT NULL,
            from_ver TEXT,
            to_ver TEXT,
            summary TEXT,
            files TEXT
        )"""
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS archy_deploy (
            site_id TEXT PRIMARY KEY,
            name TEXT,
            host TEXT,
            version TEXT NOT NULL,
            ts TEXT NOT NULL
        )"""
    )
    row = c.execute("SELECT * FROM archy_state WHERE id=1").fetchone()
    if not row:
        disp = _display(0, 1, 0)
        c.execute(
            "INSERT INTO archy_state (id, major, minor, grok, display, updated) VALUES (1,0,1,0,?,?)",
            (disp, _utc()),
        )
        c.execute(
            "INSERT INTO archy_log (ts, kind, who, from_ver, to_ver, summary, files) VALUES (?,?,?,?,?,?,?)",
            (_utc(), "seed", "operator", "", disp, "rimBaud Archy born at 0.1", ""),
        )
        c.commit()
    return c


def _write_json(state, deploys=None):
    os.makedirs(CONF, exist_ok=True)
    payload = {
        "ok": True,
        "version": state["display"],
        "major": state["major"],
        "minor": state["minor"],
        "grok": state["grok"],
        "updated": state["updated"],
        "note": "gN = Grok edits on this minor. LEVEL UP (0.1→0.2) is operator-only in rimbaud-archy.",
    }
    tmp = JSON + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    os.replace(tmp, JSON)
    if deploys is not None:
        tmp = DEPLOY_JSON + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"ok": True, "deploys": deploys, "updated": _utc()}, f, indent=2)
            f.write("\n")
        os.replace(tmp, DEPLOY_JSON)
    return payload


def state_dict(c=None):
    own = c is None
    if own:
        c = _con()
    row = c.execute("SELECT * FROM archy_state WHERE id=1").fetchone()
    d = dict(row)
    if own:
        c.close()
    return d


def deploys_list(c=None):
    own = c is None
    if own:
        c = _con()
    rows = [dict(r) for r in c.execute("SELECT * FROM archy_deploy ORDER BY ts DESC")]
    if own:
        c.close()
    return rows


def log_list(limit=40, c=None):
    own = c is None
    if own:
        c = _con()
    rows = [
        dict(r)
        for r in c.execute("SELECT * FROM archy_log ORDER BY id DESC LIMIT ?", (int(limit),))
    ]
    if own:
        c.close()
    return rows


def dump_status():
    c = _con()
    st = state_dict(c)
    deps = deploys_list(c)
    logs = log_list(24, c)
    c.close()
    payload = _write_json(st, deps)
    payload["deploys"] = deps
    payload["log"] = logs
    payload["db"] = DB
    return payload


def publish_github(version: str, summary: str) -> None:
    """Refresh the GitHub tree and commit this Archy version. Does not push."""
    if os.environ.get("ARCHY_NO_PUBLISH") == "1":
        return
    script = os.path.join(ROOT, "scripts", "rimbaud_publish.py")
    if not os.path.isfile(script):
        return
    subprocess.run(
        [sys.executable, script, "--version", version, "--summary", summary or "code edit"],
        check=False,
    )


def bump_g(summary: str, files: str = "", who: str = "grok"):
    """Grok-only: 0.1 → 0.1g1 → 0.1g2. Never raises 0.1 to 0.2."""
    c = _con()
    st = state_dict(c)
    old = st["display"]
    grok = int(st["grok"] or 0) + 1
    disp = _display(int(st["major"]), int(st["minor"]), grok)
    now = _utc()
    c.execute(
        "UPDATE archy_state SET grok=?, display=?, updated=? WHERE id=1",
        (grok, disp, now),
    )
    c.execute(
        "INSERT INTO archy_log (ts, kind, who, from_ver, to_ver, summary, files) VALUES (?,?,?,?,?,?,?)",
        (now, "grok", who, old, disp, summary or "code edit", files or ""),
    )
    c.commit()
    st = state_dict(c)
    deps = deploys_list(c)
    c.close()
    payload = _write_json(st, deps)
    publish_github(disp, summary or "code edit")
    return payload


def level_up(summary: str = "", who: str = "operator"):
    """Operator-only (C++ desk). 0.1g12 → 0.2 (g counter resets)."""
    c = _con()
    st = state_dict(c)
    old = st["display"]
    minor = int(st["minor"]) + 1
    disp = _display(int(st["major"]), minor, 0)
    now = _utc()
    c.execute(
        "UPDATE archy_state SET minor=?, grok=0, display=?, updated=? WHERE id=1",
        (minor, disp, now),
    )
    c.execute(
        "INSERT INTO archy_log (ts, kind, who, from_ver, to_ver, summary, files) VALUES (?,?,?,?,?,?,?)",
        (now, "user", who, old, disp, summary or f"LEVEL UP {old} → {disp}", ""),
    )
    c.commit()
    st = state_dict(c)
    deps = deploys_list(c)
    c.close()
    return _write_json(st, deps)


def record_deploy(site_id: str, name: str = "", host: str = "", version: str = ""):
    c = _con()
    st = state_dict(c)
    ver = version or st["display"]
    now = _utc()
    c.execute(
        """INSERT INTO archy_deploy (site_id, name, host, version, ts)
           VALUES (?,?,?,?,?)
           ON CONFLICT(site_id) DO UPDATE SET name=excluded.name, host=excluded.host,
             version=excluded.version, ts=excluded.ts""",
        (site_id, name or site_id, host, ver, now),
    )
    c.execute(
        "INSERT INTO archy_log (ts, kind, who, from_ver, to_ver, summary, files) VALUES (?,?,?,?,?,?,?)",
        (now, "deploy", "meta", st["display"], ver, f"deployed {ver} → {site_id} {host}", site_id),
    )
    c.commit()
    st = state_dict(c)
    deps = deploys_list(c)
    c.close()
    return _write_json(st, deps)


def log_edit(summary: str, files: str = ""):
    c = _con()
    st = state_dict(c)
    c.execute(
        "INSERT INTO archy_log (ts, kind, who, from_ver, to_ver, summary, files) VALUES (?,?,?,?,?,?,?)",
        (_utc(), "edit", "grok", st["display"], st["display"], summary, files),
    )
    c.commit()
    c.close()
    return dump_status()


def main(argv):
    args = list(argv[1:])
    if not args or args[0] in ("--status", "status"):
        print(json.dumps(dump_status(), indent=2))
        return 0
    cmd = args[0]
    if cmd in ("--bump-g", "bump-g"):
        summary = args[1] if len(args) > 1 else "code edit"
        files = args[2] if len(args) > 2 else ""
        print(json.dumps(bump_g(summary, files), indent=2))
        return 0
    if cmd in ("--record-deploy", "record-deploy"):
        if len(args) < 2:
            print("usage: --record-deploy SITE [name] [host] [version]", file=sys.stderr)
            return 2
        site = args[1]
        name = args[2] if len(args) > 2 else site
        host = args[3] if len(args) > 3 else ""
        ver = args[4] if len(args) > 4 else ""
        print(json.dumps(record_deploy(site, name, host, ver), indent=2))
        return 0
    if cmd in ("--log-edit", "log-edit"):
        print(json.dumps(log_edit(args[1] if len(args) > 1 else "edit", args[2] if len(args) > 2 else ""), indent=2))
        return 0
    if cmd in ("--level-up", "level-up"):
        print("LEVEL UP is operator-only in the rimbaud-archy desk. Grok cannot raise 0.1 → 0.2.", file=sys.stderr)
        return 3
    if cmd == "--operator-level-up":
        if os.environ.get("ARCHY_OPERATOR") != "1":
            print("LEVEL UP is operator-only in the rimbaud-archy desk.", file=sys.stderr)
            return 3
        print(json.dumps(level_up(), indent=2))
        return 0
    print("rimbaud-archy db: --status | --bump-g SUMMARY [files] | --record-deploy SITE", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
