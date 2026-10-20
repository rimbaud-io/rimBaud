#!/usr/bin/env python3
# Copyright 2026 Laurence Sterne
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

"""SQLite store for the wire control plane.

Keeps live snapshots, user saves, node layout, and controller audit so the
canvas can always load the running setup and still remember what changed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA_VERSION = 6
_LOCK = threading.Lock()
_DB_PATH = None

HUNTER_IDS = (
    "hummingbot-hl",
    "hummingbot-bn-swing",
    "hummingbot-helliot",
    "hummingbot-surfn",
    "hummingbot-shortn",
    "hummingbot-dman",
    "hummingbot-onlyup",
    "hummingbot-dman-maker",
)

BIAS_BOTS = {
    "hummingbot-bn-swing": "bn_swing_bias.json",
    "hummingbot-helliot": "helliot_bias.json",
    "hummingbot-surfn": "surfn_bias.json",
    "hummingbot-shortn": "shortn_bias.json",
    "hummingbot-dman": "dman_bias.json",
    "hummingbot-onlyup": "onlyup_bias.json",
    "hummingbot-dman-maker": "dman_maker_bias.json",
}

STATUS_FILES = {
    "hummingbot-hl": "hl_copy_status.json",
    "hummingbot-bn-swing": "bn_swing_status.json",
    "hummingbot-helliot": "helliot_status.json",
    "hummingbot-surfn": "surfn_status.json",
    "hummingbot-shortn": "shortn_status.json",
    "hummingbot-dman": "dman_status.json",
    "hummingbot-onlyup": "onlyup_status.json",
    "hummingbot-dman-maker": "dman_maker_status.json",
}


def _utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _json(obj):
    return json.dumps(obj, separators=(",", ":"), default=str)


def _loads(raw, default=None):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def default_db_path():
    for p in ("/conf/matrix.db", "/home/hummingbot/conf/matrix.db"):
        if os.path.isdir(os.path.dirname(p)):
            return p
    return os.path.abspath("matrix.db")


def ensure():
    if _DB_PATH:
        return _DB_PATH
    return init(default_db_path())


def init(path):
    """Open (or create) the DB. Safe to call more than once."""
    global _DB_PATH
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    imported = False
    with _LOCK:
        _DB_PATH = path
        con = sqlite3.connect(_DB_PATH, timeout=8, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            con.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA busy_timeout=4000;
                CREATE TABLE IF NOT EXISTS meta (
                    k TEXT PRIMARY KEY,
                    v TEXT
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    label TEXT,
                    graph_json TEXT NOT NULL,
                    controllers_json TEXT,
                    n_nodes INTEGER,
                    n_edges INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS snapshots_kind_ts
                    ON snapshots(kind, created_at);
                CREATE TABLE IF NOT EXISTS layout (
                    node_id TEXT PRIMARY KEY,
                    x REAL NOT NULL,
                    y REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS controller_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    old_json TEXT,
                    new_json TEXT,
                    source TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS audit_bot_ts
                    ON controller_audit(bot_id, created_at);
                CREATE TABLE IF NOT EXISTS kv (
                    bot_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value_json TEXT,
                    source TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (bot_id, key)
                );
                CREATE TABLE IF NOT EXISTS bias (
                    bot_id TEXT NOT NULL,
                    coin TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (bot_id, coin)
                );
                CREATE TABLE IF NOT EXISTS status (
                    bot_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS commands (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    claimed_at TEXT,
                    done_at TEXT
                );
                CREATE INDEX IF NOT EXISTS commands_bot_st
                    ON commands(bot_id, status, id);
                CREATE TABLE IF NOT EXISTS wire_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT,
                    graph_json TEXT NOT NULL,
                    controllers_json TEXT,
                    bias_json TEXT,
                    pct_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS qa_week (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    weekday INTEGER NOT NULL,
                    minute INTEGER NOT NULL,
                    duration_min INTEGER NOT NULL DEFAULT 60,
                    bot_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    coin TEXT,
                    value TEXT,
                    label TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS qa_week_when
                    ON qa_week(weekday, minute);
                CREATE TABLE IF NOT EXISTS leader_audits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trader_num INTEGER NOT NULL,
                    address TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    capacity_flag TEXT,
                    sleep_flag TEXT,
                    equity REAL,
                    free_margin REAL,
                    n_positions INTEGER,
                    u_pnl_total REAL,
                    can_open INTEGER,
                    ok INTEGER,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS leader_audits_slot_ts
                    ON leader_audits(trader_num, created_at);
                CREATE INDEX IF NOT EXISTS leader_audits_addr_ts
                    ON leader_audits(address, created_at);
                CREATE TABLE IF NOT EXISTS controller_spec (
                    bot_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    label TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    minv REAL,
                    maxv REAL,
                    step REAL,
                    default_json TEXT,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    role TEXT NOT NULL DEFAULT 'other',
                    live INTEGER NOT NULL DEFAULT 1,
                    flatten_guard INTEGER NOT NULL DEFAULT 0,
                    chart_line TEXT,
                    hint TEXT,
                    options_json TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (bot_id, key)
                );
                CREATE INDEX IF NOT EXISTS controller_spec_bot
                    ON controller_spec(bot_id, sort_order, key);
                CREATE TABLE IF NOT EXISTS controller_preset (
                    bot_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    values_json TEXT NOT NULL,
                    locked INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (bot_id, name)
                );
                CREATE INDEX IF NOT EXISTS controller_preset_bot
                    ON controller_preset(bot_id, name);
                CREATE TABLE IF NOT EXISTS account (
                    bot_id TEXT PRIMARY KEY,
                    name TEXT,
                    container TEXT,
                    yml TEXT,
                    address TEXT,
                    use_vault INTEGER NOT NULL DEFAULT 0,
                    notes TEXT,
                    source TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS desk_bias (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    mode TEXT NOT NULL,
                    closes INTEGER NOT NULL DEFAULT 0,
                    applied INTEGER NOT NULL DEFAULT 0,
                    source TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS grok_bias (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    mode TEXT NOT NULL,
                    closes INTEGER NOT NULL DEFAULT 0,
                    writes_hunters INTEGER NOT NULL DEFAULT 0,
                    source TEXT,
                    raw TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wire_node (
                    board TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    x REAL NOT NULL DEFAULT 0,
                    y REAL NOT NULL DEFAULT 0,
                    name TEXT,
                    kind TEXT,
                    color TEXT,
                    coin TEXT,
                    tool_id TEXT,
                    bot_id TEXT,
                    address TEXT,
                    bias TEXT,
                    pct REAL,
                    skim_mode TEXT,
                    armed INTEGER,
                    extra TEXT,
                    w REAL,
                    h REAL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (board, node_id)
                );
                CREATE TABLE IF NOT EXISTS wire_edge (
                    board TEXT NOT NULL,
                    edge_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_handle TEXT,
                    target TEXT NOT NULL,
                    target_handle TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (board, edge_id)
                );
                CREATE INDEX IF NOT EXISTS wire_edge_board
                    ON wire_edge(board, source, target);
                CREATE TABLE IF NOT EXISTS risk_skim (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    send INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_skim_hunter (
                    bot_id TEXT PRIMARY KEY,
                    wallet TEXT,
                    address TEXT,
                    mode TEXT,
                    skim_pct REAL,
                    every_h REAL,
                    min_usd REAL,
                    streak_wins INTEGER,
                    streak INTEGER,
                    armed INTEGER NOT NULL DEFAULT 0,
                    send INTEGER NOT NULL DEFAULT 0,
                    parked_usd REAL,
                    last_win_usd REAL,
                    last_skim_ts TEXT,
                    last_trade_key TEXT,
                    last_send_try TEXT,
                    last_send_error TEXT,
                    note TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS risk_skim_event (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id TEXT NOT NULL,
                    ts TEXT,
                    dest TEXT,
                    from_sub TEXT,
                    send INTEGER,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS risk_skim_event_bot
                    ON risk_skim_event(bot_id, id);
                """
            )
            con.execute(
                "INSERT OR REPLACE INTO meta(k, v) VALUES('schema', ?)",
                (str(SCHEMA_VERSION),),
            )
            row = con.execute("SELECT v FROM meta WHERE k='imported_json'").fetchone()
            imported = bool(row and row["v"] == "1")
            con.commit()
        finally:
            con.close()
    if not imported:
        try:
            n = import_conf(os.path.dirname(path))
            with _LOCK:
                con = sqlite3.connect(_DB_PATH, timeout=8, check_same_thread=False)
                try:
                    con.execute(
                        "INSERT OR REPLACE INTO meta(k, v) VALUES('imported_json', '1')"
                    )
                    con.execute(
                        "INSERT OR REPLACE INTO meta(k, v) VALUES('imported_n', ?)",
                        (str(n),),
                    )
                    con.commit()
                finally:
                    con.close()
        except Exception:
            pass
    try:
        seed_controller_specs()
    except Exception:
        pass
    try:
        seed_controller_presets()
    except Exception:
        pass
    return path


def _relax_db_mode(path):
    """Spring runs as the desk user. Hunters run as root. Keep the files writable by both."""
    for suffix in ("", "-wal", "-shm"):
        try:
            os.chmod(path + suffix, 0o666)
        except OSError:
            pass


def _connect():
    if not _DB_PATH:
        ensure()
    con = sqlite3.connect(_DB_PATH, timeout=8, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=4000")
    _relax_db_mode(_DB_PATH)
    return con


def save_snapshot(kind, label, graph, controllers=None):
    """Persist a graph (+ controller map). kind is live | save."""
    kind = str(kind or "save")[:32]
    label = (label or "")[:160]
    nodes = (graph or {}).get("nodes") or []
    edges = (graph or {}).get("edges") or []
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                """
                INSERT INTO snapshots(
                    kind, label, graph_json, controllers_json,
                    n_nodes, n_edges, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kind,
                    label,
                    _json(graph or {}),
                    _json(controllers or {}),
                    len(nodes),
                    len(edges),
                    _utc(),
                ),
            )
            sid = cur.lastrowid
            if kind == "live":
                con.execute(
                    """
                    DELETE FROM snapshots WHERE kind='live' AND id NOT IN (
                        SELECT id FROM (
                            SELECT id FROM snapshots WHERE kind='live'
                            ORDER BY id DESC LIMIT 40
                        )
                    )
                    """
                )
            elif kind == "save":
                con.execute(
                    """
                    DELETE FROM snapshots WHERE kind='save' AND id NOT IN (
                        SELECT id FROM (
                            SELECT id FROM snapshots WHERE kind='save'
                            ORDER BY id DESC LIMIT 80
                        )
                    )
                    """
                )
            con.commit()
            return sid
        finally:
            con.close()


def list_snapshots(kind=None, limit=20):
    limit = max(1, min(int(limit or 20), 100))
    sql = """
        SELECT id, kind, label, n_nodes, n_edges, created_at
        FROM snapshots
    """
    args = []
    if kind:
        sql += " WHERE kind=?"
        args.append(kind)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(sql, args).fetchall()
        finally:
            con.close()
    return [dict(r) for r in rows]


def get_snapshot(sid):
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT * FROM snapshots WHERE id=?",
                (int(sid),),
            ).fetchone()
        finally:
            con.close()
    if not row:
        return None
    out = dict(row)
    out["graph"] = _loads(out.pop("graph_json"), {"nodes": [], "edges": []})
    out["controllers"] = _loads(out.pop("controllers_json"), {})
    return out


def upsert_layout(nodes):
    """Remember node positions so a live rebuild keeps the user's drag."""
    rows = []
    now = _utc()
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "")[:80]
        pos = n.get("position") if isinstance(n.get("position"), dict) else {}
        if not nid:
            continue
        try:
            x = float(pos.get("x") or 0)
            y = float(pos.get("y") or 0)
        except (TypeError, ValueError):
            continue
        rows.append((nid, x, y, now))
    if not rows:
        return 0
    with _LOCK:
        con = _connect()
        try:
            con.executemany(
                """
                INSERT INTO layout(node_id, x, y, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    x=excluded.x, y=excluded.y, updated_at=excluded.updated_at
                """,
                rows,
            )
            con.commit()
        finally:
            con.close()
    return len(rows)


def layout_map():
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute("SELECT node_id, x, y FROM layout").fetchall()
        finally:
            con.close()
    return {r["node_id"]: (r["x"], r["y"]) for r in rows}


def apply_layout(graph):
    """Overlay saved positions onto a live reconstruct. Unknown nodes keep live xy."""
    if not graph or not isinstance(graph.get("nodes"), list):
        return graph
    pos = layout_map()
    if not pos:
        return graph
    nodes = []
    for n in graph["nodes"]:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "")
        xy = pos.get(nid)
        if not xy:
            nodes.append(n)
            continue
        n = dict(n)
        n["position"] = {"x": xy[0], "y": xy[1]}
        nodes.append(n)
    out = dict(graph)
    out["nodes"] = nodes
    return out


def clear_layout():
    with _LOCK:
        con = _connect()
        try:
            con.execute("DELETE FROM layout")
            con.commit()
        finally:
            con.close()


def qa_list():
    """QA sequencer blocks. Never applied to hunters from here."""
    ensure()
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                """
                SELECT id, weekday, minute, duration_min, bot_id, kind, coin, value, label, created_at
                FROM qa_week
                ORDER BY weekday, minute, id
                """
            ).fetchall()
        finally:
            con.close()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "weekday": int(r["weekday"]),
            "minute": int(r["minute"]),
            "duration_min": int(r["duration_min"] or 60),
            "bot_id": r["bot_id"],
            "kind": r["kind"],
            "coin": r["coin"],
            "value": r["value"],
            "label": r["label"],
            "created_at": r["created_at"],
        })
    return out


def qa_add(item):
    ensure()
    weekday = int(item.get("weekday"))
    minute = int(item.get("minute"))
    duration = int(item.get("duration_min") or 60)
    if weekday < 0 or weekday > 6:
        raise ValueError("weekday must be 0–6 (Mon–Sun)")
    if minute < 0 or minute > 1439:
        raise ValueError("minute must be 0–1439")
    if duration < 1:
        duration = 1
    if duration > 24 * 60:
        duration = 24 * 60
    bot_id = str(item.get("bot_id") or "*")[:80]
    kind = str(item.get("kind") or "").strip()[:24]
    if kind not in ("bias", "close_all"):
        raise ValueError("kind must be bias or close_all")
    coin = str(item.get("coin") or "").upper().strip()[:8] or None
    value = str(item.get("value") or "").strip()[:24] or None
    label = str(item.get("label") or "").strip()[:80] or None
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                """
                INSERT INTO qa_week(
                    weekday, minute, duration_min, bot_id, kind, coin, value, label, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (weekday, minute, duration, bot_id, kind, coin, value, label, _utc()),
            )
            con.commit()
            return cur.lastrowid
        finally:
            con.close()


def qa_delete(item_id):
    ensure()
    with _LOCK:
        con = _connect()
        try:
            con.execute("DELETE FROM qa_week WHERE id=?", (int(item_id),))
            con.commit()
        finally:
            con.close()
    return True


def audit(bot_id, key, old, new, source="wire"):
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                """
                INSERT INTO controller_audit(
                    bot_id, key, old_json, new_json, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(bot_id or "")[:80],
                    str(key or "")[:80],
                    _json(old),
                    _json(new),
                    str(source or "wire")[:40],
                    _utc(),
                ),
            )
            con.execute(
                """
                DELETE FROM controller_audit WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id FROM controller_audit ORDER BY id DESC LIMIT 2000
                    )
                )
                """
            )
            con.commit()
        finally:
            con.close()


def leader_audit_put(result):
    """Store one Copy-leader health audit. Keeps last 80 rows per T# slot."""
    ensure()
    if not isinstance(result, dict):
        return None
    try:
        num = int(result.get("trader") or 0)
    except (TypeError, ValueError):
        num = 0
    addr = str(result.get("address") or "").strip().lower()
    created = str(result.get("audited_at") or _utc())
    can = result.get("can_open")
    ok = result.get("ok")
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                """
                INSERT INTO leader_audits(
                    trader_num, address, payload_json, capacity_flag, sleep_flag,
                    equity, free_margin, n_positions, u_pnl_total, can_open, ok, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    num,
                    addr,
                    _json(result),
                    str(result.get("capacity_flag") or "")[:24] or None,
                    str(result.get("sleep_flag") or "")[:24] or None,
                    result.get("equity"),
                    result.get("free_margin"),
                    result.get("n_positions"),
                    result.get("u_pnl_total"),
                    None if can is None else (1 if can else 0),
                    None if ok is None else (1 if ok else 0),
                    created,
                ),
            )
            con.execute(
                """
                DELETE FROM leader_audits WHERE trader_num=? AND id NOT IN (
                    SELECT id FROM (
                        SELECT id FROM leader_audits WHERE trader_num=?
                        ORDER BY id DESC LIMIT 80
                    )
                )
                """,
                (num, num),
            )
            con.commit()
            return cur.lastrowid
        finally:
            con.close()


def leader_audit_latest(addresses=None):
    """Newest audit per live roster address (not a stale T# slot)."""
    ensure()
    addrs = []
    if addresses:
        addrs = [str(a or "").strip().lower() for a in addresses if str(a or "").strip()]
    traders = {}
    updated = None
    with _LOCK:
        con = _connect()
        try:
            if addrs:
                rows = []
                for i, addr in enumerate(addrs, 1):
                    row = con.execute(
                        """
                        SELECT * FROM leader_audits
                        WHERE lower(address)=?
                        ORDER BY id DESC LIMIT 1
                        """,
                        (addr,),
                    ).fetchone()
                    if row is None:
                        row = con.execute(
                            """
                            SELECT * FROM leader_audits
                            WHERE trader_num=? AND lower(address)=?
                            ORDER BY id DESC LIMIT 1
                            """,
                            (i, addr),
                        ).fetchone()
                    if row is not None:
                        rows.append(row)
            else:
                rows = con.execute(
                    """
                    SELECT a.* FROM leader_audits a
                    JOIN (
                        SELECT trader_num, MAX(id) AS max_id
                        FROM leader_audits
                        GROUP BY trader_num
                    ) m ON a.id = m.max_id
                    ORDER BY a.trader_num
                    """
                ).fetchall()
        finally:
            con.close()
    for r in rows:
        payload = _loads(r["payload_json"], {}) or {}
        if not isinstance(payload, dict):
            payload = {}
        num = r["trader_num"]
        if addrs:
            try:
                num = addrs.index(str(r["address"] or "").lower()) + 1
            except ValueError:
                num = r["trader_num"]
        payload["trader"] = num
        payload.setdefault("address", r["address"])
        payload["stale"] = False
        traders[str(num)] = payload
        ts = r["created_at"] or payload.get("audited_at")
        if ts and (updated is None or str(ts) > str(updated)):
            updated = ts
    return {
        "traders": traders,
        "updated_at": updated,
        "timezone": "Europe/Berlin",
        "schedule": {"1": "01:00", "2": "06:00", "3": "11:00"},
        "source": "sqlite",
    }


def leader_audit_history(limit=30):
    ensure()
    limit = max(1, min(int(limit or 30), 200))
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                """
                SELECT id, trader_num, address, payload_json, capacity_flag, sleep_flag,
                       equity, free_margin, n_positions, u_pnl_total, can_open, ok, created_at
                FROM leader_audits
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            con.close()
    out = []
    for r in rows:
        payload = _loads(r["payload_json"], {}) or {}
        u_pnl = r["u_pnl_total"]
        if u_pnl is None and isinstance(payload, dict):
            u_pnl = payload.get("u_pnl_total")
        out.append({
            "id": r["id"],
            "file": "sqlite:" + str(r["id"]),
            "trader": r["trader_num"],
            "address": r["address"],
            "audited_at": r["created_at"] or (payload.get("audited_at") if isinstance(payload, dict) else None),
            "capacity_flag": r["capacity_flag"] or (payload.get("capacity_flag") if isinstance(payload, dict) else None),
            "sleep_flag": r["sleep_flag"] or (payload.get("sleep_flag") if isinstance(payload, dict) else None),
            "free_margin": r["free_margin"],
            "equity": r["equity"],
            "sleep_hours": payload.get("sleep_hours") if isinstance(payload, dict) else None,
            "n_positions": r["n_positions"],
            "u_pnl_total": u_pnl,
            "can_open": None if r["can_open"] is None else bool(r["can_open"]),
        })
    return out


def leader_audit_by_address():
    ensure()
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                """
                SELECT a.* FROM leader_audits a
                JOIN (
                    SELECT address, MAX(id) AS max_id
                    FROM leader_audits
                    WHERE address IS NOT NULL AND address != ''
                    GROUP BY address
                ) m ON a.id = m.max_id
                """
            ).fetchall()
        finally:
            con.close()
    out = {}
    for r in rows:
        payload = _loads(r["payload_json"], {}) or {}
        if isinstance(payload, dict):
            out[str(r["address"] or "").lower()] = payload
    return out


def leader_audit_force_set(trader="all"):
    ensure()
    kv_set("hummingbot-audit", "force", {"trader": trader, "requested_at": _utc()}, source="dashboard")
    return True


def leader_audit_force_take():
    """Pop a pending force request. None if none queued."""
    ensure()
    raw = kv_get("hummingbot-audit", "force")
    if not raw:
        return None
    kv_set("hummingbot-audit", "force", None, source="audit-worker")
    if isinstance(raw, dict):
        return raw.get("trader", "all")
    return raw


def leader_audit_state_get():
    ensure()
    raw = kv_get("hummingbot-audit", "state")
    return raw if isinstance(raw, dict) else {"last_run_date": {}}


def leader_audit_state_set(state):
    ensure()
    kv_set("hummingbot-audit", "state", state if isinstance(state, dict) else {}, source="audit-worker")
    return True


def leader_audit_import_json(logs_dir):
    """One-shot import of logs/trader_audits JSON into sqlite."""
    ensure()
    with _LOCK:
        con = _connect()
        try:
            row = con.execute("SELECT v FROM meta WHERE k='imported_leader_audits'").fetchone()
            if row and row["v"] == "1":
                return 0
        finally:
            con.close()
    n = 0
    seen = set()
    paths = []
    latest = os.path.join(logs_dir, "trader_audits", "latest.json")
    hist = os.path.join(logs_dir, "trader_audits", "history")
    if os.path.isfile(latest):
        paths.append(latest)
    if os.path.isdir(hist):
        for name in sorted(os.listdir(hist)):
            if name.endswith(".json"):
                paths.append(os.path.join(hist, name))
    for path in paths:
        try:
            with open(path) as f:
                raw = json.load(f)
        except Exception:
            continue
        rows = []
        if isinstance(raw, dict) and isinstance(raw.get("traders"), dict):
            rows.extend(raw["traders"].values())
        elif isinstance(raw, dict) and raw.get("address"):
            rows.append(raw)
        for result in rows:
            if not isinstance(result, dict):
                continue
            key = (
                str(result.get("trader")),
                str(result.get("address") or "").lower(),
                str(result.get("audited_at") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            try:
                leader_audit_put(result)
                n += 1
            except Exception:
                continue
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                "INSERT OR REPLACE INTO meta(k, v) VALUES('imported_leader_audits', '1')"
            )
            con.execute(
                "INSERT OR REPLACE INTO meta(k, v) VALUES('imported_leader_audits_n', ?)",
                (str(n),),
            )
            con.commit()
        finally:
            con.close()
    return n


def recent_audit(limit=40):
    limit = max(1, min(int(limit or 40), 200))
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                """
                SELECT id, bot_id, key, old_json, new_json, source, created_at
                FROM controller_audit
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            con.close()
    out = []
    for r in rows:
        row = dict(r)
        row["old"] = _loads(row.pop("old_json"))
        row["new"] = _loads(row.pop("new_json"))
        out.append(row)
    return out


def _file_json(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _parse_stamp(s):
    t = str(s or "").replace("T", " ").replace(" UTC", "").strip().split(".")[0]
    if len(t) >= 19:
        t = t[:19]
    try:
        return datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def kv_updated_at(bot_id, key=None):
    with _LOCK:
        con = _connect()
        try:
            if key:
                row = con.execute(
                    "SELECT updated_at FROM kv WHERE bot_id=? AND key=?",
                    (str(bot_id), str(key)),
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT MAX(updated_at) AS updated_at FROM kv WHERE bot_id=?",
                    (str(bot_id),),
                ).fetchone()
        finally:
            con.close()
    return row["updated_at"] if row and row["updated_at"] else None


def has_kv(bot_id, key):
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT 1 FROM kv WHERE bot_id=? AND key=?",
                (str(bot_id), str(key)),
            ).fetchone()
        finally:
            con.close()
    return bool(row)


def kv_get(bot_id, key, default=None):
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT value_json FROM kv WHERE bot_id=? AND key=?",
                (str(bot_id), str(key)),
            ).fetchone()
        finally:
            con.close()
    if not row:
        return default
    val = _loads(row["value_json"], default)
    return default if val is None else val


def kv_get_typed(bot_id, key, default):
    val = kv_get(bot_id, key, None)
    if val is None:
        return default
    try:
        if isinstance(default, bool):
            return bool(val)
        if isinstance(default, int) and not isinstance(default, bool):
            return int(float(val))
        if isinstance(default, float):
            return float(val)
    except (TypeError, ValueError):
        return default
    return val


def kv_set(bot_id, key, value, source="sql", only_if_missing=False):
    bot_id = str(bot_id or "")[:80]
    key = str(key or "")[:80]
    if not bot_id or not key or key in ("updated", "source"):
        return False
    if only_if_missing and has_kv(bot_id, key):
        return False
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                """
                INSERT INTO kv(bot_id, key, value_json, source, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(bot_id, key) DO UPDATE SET
                    value_json=excluded.value_json,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                (bot_id, key, _json(value), str(source or "sql")[:40], _utc()),
            )
            con.commit()
        finally:
            con.close()
    return True


def kv_map(bot_id):
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT key, value_json FROM kv WHERE bot_id=?",
                (str(bot_id),),
            ).fetchall()
        finally:
            con.close()
    out = {}
    for r in rows:
        if r["key"] in ("updated", "source"):
            continue
        out[r["key"]] = _loads(r["value_json"])
    return out


# ---------------------------------------------------------------------------
# Controller specs — abstract knob pack per hunter.
# Values stay in kv / *_ctrl.json. This table is the fixed UI + role map
# (exit / wait / size / timing) so desk, chart, and website share one setup.
# Roll out by adding a pack to CONTROLLER_SPECS; other hunters stay on
# wire_registry until their pack exists.
# ---------------------------------------------------------------------------
CONTROLLER_SPEC_PACK = 5
CONTROLLER_SPECS = {
    "hummingbot-bn-swing": [
        {
            "key": "open_on_bias", "label": "Open on BIAS", "kind": "bool",
            "min": 0, "max": 1, "step": 1, "default": False, "sort": -10,
            "role": "entry", "live": True, "flatten_guard": False, "chart_line": "",
            "hint": "when BIAS is LONG/SHORT, market-open now (skip NEXT IN wait)",
        },
        {
            "key": "scale_x", "label": "SCALE x", "kind": "num",
            "min": 0, "max": 1, "step": 0.01, "default": 0.3, "sort": 0,
            "role": "timing", "live": True, "flatten_guard": False, "chart_line": "",
            "hint": "0 quiet/tight · 1 swing/wide · writes all seven then couples",
        },
        {
            "key": "take_profit", "label": "TP price %", "kind": "price_pct",
            "min": 0.80, "max": 6.00, "step": 0.01, "default": 0.024, "sort": 10,
            "role": "exit", "live": True, "flatten_guard": True, "chart_line": "WIN",
            "hint": "hard TP cap — trailing is the main exit",
        },
        {
            "key": "trail_activation", "label": "Trail ARM %", "kind": "price_pct",
            "min": 0.40, "max": 4.00, "step": 0.01, "default": 0.014, "sort": 20,
            "role": "exit", "live": True, "flatten_guard": False, "chart_line": "ARM",
            "hint": "arm trail after this price % in profit",
        },
        {
            "key": "trail_delta", "label": "Trail %", "kind": "num",
            "min": 8, "max": 40, "step": 1, "default": 22, "sort": 30,
            "role": "exit", "live": True, "flatten_guard": False, "chart_line": "TRAIL",
            "hint": "percent of ARM — price delta = ARM * Trail%/100",
        },
        {
            "key": "rebuy_pct", "label": "NEXT IN %", "kind": "price_pct",
            "min": 0.05, "max": 0.80, "step": 0.01, "default": 0.0018, "sort": 40,
            "role": "wait", "live": True, "flatten_guard": False, "chart_line": "NEXT IN",
            "hint": "after WIN, wait this price % then open on BIAS",
        },
        {
            "key": "go_in_after_close", "label": "Go in after close", "kind": "bool",
            "min": 0, "max": 1, "step": 1, "default": False, "sort": 41,
            "role": "entry", "live": True, "flatten_guard": False, "chart_line": "",
            "hint": "after clip closes, market 100% leftover on current BIAS (skip NEXT IN)",
        },
        {
            "key": "executor_refresh_time", "label": "Refresh s", "kind": "num",
            "min": 8, "max": 90, "step": 1, "default": 20, "sort": 50,
            "role": "timing", "live": False, "flatten_guard": False, "chart_line": "",
            "hint": "wider scale → faster refresh, never below 8s",
        },
        {
            "key": "sl_roe_pct", "label": "SL ROE %", "kind": "num",
            "min": 15, "max": 70, "step": 1, "default": 40, "sort": 60,
            "role": "exit", "live": True, "flatten_guard": True, "chart_line": "SL",
            "hint": "emergency ROE cap — do not scale as hard as ARM/TP",
        },
        {
            "key": "margin_pct", "label": "Margin %", "kind": "pct_frac",
            "min": 20, "max": 100, "step": 1, "default": 0.60, "sort": 70,
            "role": "size", "live": False, "flatten_guard": False, "chart_line": "",
            "hint": "fraction of leftover — 100 only if leftover > 30% equity",
        },
    ],
    "hummingbot-hl": [
        {"key": "copy_shorts", "label": "Copy shorts", "kind": "bool", "min": 0, "max": 1, "step": 1, "default": True, "sort": 10, "role": "entry", "live": True, "flatten_guard": False, "chart_line": "", "hint": "copy leader shorts; clips stay"},
        {"key": "max_leverage", "label": "Max lev", "kind": "num", "min": 1, "max": 50, "step": 1, "default": 20, "sort": 20, "role": "size", "live": True, "flatten_guard": False, "chart_line": "", "hint": "cap copy leverage"},
        {"key": "max_positions_per_trader", "label": "Pos / trader", "kind": "num", "min": 1, "max": 8, "step": 1, "default": 3, "sort": 30, "role": "size", "live": True, "flatten_guard": False, "chart_line": "", "hint": "open clips per leader"},
        {"key": "max_position_loss_pct", "label": "Max loss % eq", "kind": "pct_frac", "min": 0, "max": 100, "step": 1, "default": 0.333, "sort": 40, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "", "hint": "skip new copy if loss vs equity"},
        {"key": "min_free_margin_pct", "label": "Min free %", "kind": "pct_frac", "min": 0, "max": 100, "step": 1, "default": 0.15, "sort": 50, "role": "safety", "live": True, "flatten_guard": False, "chart_line": "", "hint": "keep this free margin"},
        {"key": "max_subscribed_markets", "label": "Max books", "kind": "num", "min": 3, "max": 12, "step": 1, "default": 8, "sort": 60, "role": "timing", "live": True, "flatten_guard": False, "chart_line": "", "hint": "idle books burn IP tokens"},
        {"key": "poll_interval_seconds", "label": "Poll sec", "kind": "num", "min": 2, "max": 30, "step": 1, "default": 5, "sort": 70, "role": "timing", "live": True, "flatten_guard": False, "chart_line": "", "hint": "leader poll interval"},
        {"key": "max_zec_short_usd", "label": "Max short ZEC $", "kind": "num", "min": 0, "max": 500000, "step": 10, "default": 90, "sort": 80, "role": "size", "live": True, "flatten_guard": False, "chart_line": "", "hint": "ZEC short notional cap"},
        {"key": "sl_roe_pct", "label": "SL ROE %", "kind": "num", "min": 1, "max": 80, "step": 1, "default": 20, "sort": 90, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "SL", "hint": "emergency ROE stop"},
    ],
    "hummingbot-helliot": [
        {"key": "tp_roe_pct", "label": "TP ROE %", "kind": "num", "min": 1, "max": 200, "step": 1, "default": 93, "sort": 10, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "WIN", "hint": "take profit at this ROE"},
        {"key": "sl_roe_pct", "label": "SL ROE %", "kind": "num", "min": 1, "max": 200, "step": 1, "default": 20, "sort": 20, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "SL", "hint": "stop at -ROE"},
        {"key": "wave2_min", "label": "Wave2 min", "kind": "num", "min": 0, "max": 2, "step": 0.001, "default": 0.382, "sort": 30, "role": "entry", "live": True, "flatten_guard": False, "chart_line": "", "hint": "Elliot wave 2 min"},
        {"key": "wave2_max", "label": "Wave2 max", "kind": "num", "min": 0, "max": 2, "step": 0.001, "default": 0.786, "sort": 40, "role": "entry", "live": True, "flatten_guard": False, "chart_line": "", "hint": "Elliot wave 2 max"},
        {"key": "wave3_ext", "label": "Wave3 ext", "kind": "num", "min": 0, "max": 2, "step": 0.001, "default": 1.618, "sort": 50, "role": "entry", "live": True, "flatten_guard": False, "chart_line": "", "hint": "Elliot wave 3 extension"},
        {"key": "margin_pct", "label": "Margin %", "kind": "pct_frac", "min": 0, "max": 100, "step": 1, "default": 1.0, "sort": 60, "role": "size", "live": True, "flatten_guard": False, "chart_line": "", "hint": "leftover used on next open"},
    ],
    "hummingbot-surfn": [
        {"key": "st_length", "label": "ST length", "kind": "num", "min": 5, "max": 80, "step": 1, "default": 20, "sort": 10, "role": "entry", "live": True, "flatten_guard": False, "chart_line": "", "hint": "SuperTrend length"},
        {"key": "st_mult", "label": "ST mult", "kind": "num", "min": 1, "max": 10, "step": 0.1, "default": 4, "sort": 20, "role": "entry", "live": True, "flatten_guard": False, "chart_line": "", "hint": "SuperTrend multiplier"},
        {"key": "percentage_threshold", "label": "ST dist %", "kind": "pct_frac", "min": 0, "max": 10, "step": 0.1, "default": 0.01, "sort": 30, "role": "entry", "live": True, "flatten_guard": False, "chart_line": "", "hint": "min distance to ST"},
        {"key": "scale_roe_pct", "label": "Clip ROE %", "kind": "num", "min": 1, "max": 200, "step": 1, "default": 90, "sort": 40, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "WIN", "hint": "scale-out at this ROE"},
        {"key": "scale_out_pct", "label": "Clip size %", "kind": "pct_frac", "min": 0, "max": 100, "step": 1, "default": 0.5, "sort": 50, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "", "hint": "fraction of clip to take"},
        {"key": "sl_roe_pct", "label": "SL ROE %", "kind": "num", "min": 1, "max": 200, "step": 1, "default": 20, "sort": 60, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "SL", "hint": "stop at -ROE"},
        {"key": "margin_pct", "label": "Margin %", "kind": "pct_frac", "min": 0, "max": 100, "step": 1, "default": 1.0, "sort": 70, "role": "size", "live": True, "flatten_guard": False, "chart_line": "", "hint": "leftover on next open"},
    ],
    "hummingbot-shortn": [
        {"key": "tp_roe_pct", "label": "TP ROE %", "kind": "num", "min": 1, "max": 200, "step": 1, "default": 90, "sort": 10, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "WIN", "hint": "take profit ROE"},
        {"key": "sl_roe_pct", "label": "SL ROE %", "kind": "num", "min": 1, "max": 200, "step": 1, "default": 20, "sort": 20, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "SL", "hint": "stop ROE"},
        {"key": "recover_roe_pct", "label": "Rebuy bounce ROE %", "kind": "num", "min": 1, "max": 200, "step": 1, "default": 20, "sort": 30, "role": "wait", "live": True, "flatten_guard": False, "chart_line": "NEXT IN", "hint": "rebuy after bounce"},
        {"key": "bb_short_threshold", "label": "Short BBP ≥", "kind": "num", "min": 0, "max": 1.2, "step": 0.01, "default": 0.75, "sort": 40, "role": "entry", "live": True, "flatten_guard": False, "chart_line": "", "hint": "BBP to allow shorts"},
        {"key": "margin_pct", "label": "Margin %", "kind": "pct_frac", "min": 0, "max": 100, "step": 1, "default": 1.0, "sort": 50, "role": "size", "live": True, "flatten_guard": False, "chart_line": "", "hint": "leftover on next open"},
    ],
    "hummingbot-dman": [
        {"key": "trail_activation", "label": "Trail ARM %", "kind": "pct_frac", "min": 0, "max": 100, "step": 0.1, "default": 0.03, "sort": 10, "role": "exit", "live": True, "flatten_guard": False, "chart_line": "ARM", "hint": "arm trail after this %"},
        {"key": "trail_delta", "label": "Trail Δ %", "kind": "pct_frac", "min": 0, "max": 100, "step": 0.1, "default": 0.5, "sort": 20, "role": "exit", "live": True, "flatten_guard": False, "chart_line": "TRAIL", "hint": "trail retrace %"},
        {"key": "bb_long_threshold", "label": "Rebuy BBP ≤", "kind": "num", "min": 0, "max": 1.2, "step": 0.01, "default": 1.0, "sort": 30, "role": "wait", "live": True, "flatten_guard": False, "chart_line": "NEXT IN", "hint": "BBP to rebuy"},
        {"key": "bb_short_threshold", "label": "Short BBP ≥", "kind": "num", "min": 0, "max": 1.2, "step": 0.01, "default": 1.0, "sort": 40, "role": "entry", "live": True, "flatten_guard": False, "chart_line": "", "hint": "BBP to short"},
        {"key": "hold_h", "label": "Hold hours", "kind": "select", "min": 12, "max": 68, "step": 1, "default": 68, "sort": 50, "role": "timing", "live": True, "flatten_guard": False, "chart_line": "", "hint": "12 / 24 / 68", "options": ["12", "24", "68"]},
        {"key": "sl_roe_pct", "label": "SL ROE %", "kind": "num", "min": 1, "max": 200, "step": 1, "default": 40, "sort": 60, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "SL", "hint": "stop ROE"},
        {"key": "margin_pct", "label": "Margin %", "kind": "pct_frac", "min": 0, "max": 100, "step": 1, "default": 1.0, "sort": 70, "role": "size", "live": True, "flatten_guard": False, "chart_line": "", "hint": "leftover on next open"},
    ],
    "hummingbot-onlyup": [
        {"key": "scale_roe_pct", "label": "Clip ROE %", "kind": "num", "min": 1, "max": 2000, "step": 1, "default": 90, "sort": 10, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "WIN", "hint": "scale-out ROE (max 2000%)"},
        {"key": "scale_out_pct", "label": "Clip size %", "kind": "pct_frac", "min": 0, "max": 100, "step": 1, "default": 0.5, "sort": 20, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "", "hint": "fraction to take"},
        {"key": "sl_roe_pct", "label": "SL ROE %", "kind": "num", "min": 1, "max": 200, "step": 1, "default": 60, "sort": 30, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "SL", "hint": "stop ROE"},
        {"key": "st_max_dist", "label": "ST max dist %", "kind": "pct_frac", "min": 0, "max": 100, "step": 0.1, "default": 0.012, "sort": 40, "role": "entry", "live": True, "flatten_guard": False, "chart_line": "", "hint": "max SuperTrend distance"},
        {"key": "margin_pct", "label": "Margin %", "kind": "pct_frac", "min": 0, "max": 100, "step": 1, "default": 1.0, "sort": 50, "role": "size", "live": True, "flatten_guard": False, "chart_line": "", "hint": "leftover on next open"},
    ],
    "hummingbot-dman-maker": [
        {"key": "dontGoCrazy", "label": "dontGoCrazy", "kind": "bool", "min": 0, "max": 1, "step": 1, "default": False, "sort": 10, "role": "safety", "live": True, "flatten_guard": False, "chart_line": "", "hint": "cap wild entries"},
        {"key": "afterLossNoRebuy", "label": "afterLossNoRebuy", "kind": "bool", "min": 0, "max": 1, "step": 1, "default": False, "sort": 20, "role": "wait", "live": True, "flatten_guard": False, "chart_line": "", "hint": "pause rebuy after a loss"},
        {"key": "take_profit", "label": "TP %", "kind": "pct_frac", "min": 0, "max": 100, "step": 0.1, "default": 0.02, "sort": 30, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "WIN", "hint": "take profit price %"},
        {"key": "trail_activation", "label": "Trail ARM %", "kind": "pct_frac", "min": 0, "max": 100, "step": 0.1, "default": 0.015, "sort": 40, "role": "exit", "live": True, "flatten_guard": False, "chart_line": "ARM", "hint": "arm trail"},
        {"key": "trail_delta", "label": "Trail Δ %", "kind": "pct_frac", "min": 0, "max": 100, "step": 0.1, "default": 0.005, "sort": 50, "role": "exit", "live": True, "flatten_guard": False, "chart_line": "TRAIL", "hint": "trail retrace"},
        {"key": "executor_refresh_time", "label": "Refresh s", "kind": "num", "min": 10, "max": 600, "step": 1, "default": 30, "sort": 60, "role": "timing", "live": True, "flatten_guard": False, "chart_line": "", "hint": "quote refresh"},
        {"key": "sl_roe_pct", "label": "SL ROE %", "kind": "num", "min": 1, "max": 200, "step": 1, "default": 40, "sort": 70, "role": "exit", "live": True, "flatten_guard": True, "chart_line": "SL", "hint": "stop ROE"},
        {"key": "margin_pct", "label": "Margin %", "kind": "pct_frac", "min": 0, "max": 100, "step": 1, "default": 1.0, "sort": 80, "role": "size", "live": True, "flatten_guard": False, "chart_line": "", "hint": "leftover on next open"},
    ],
}


def _spec_row(spec):
    return {
        "key": spec["key"],
        "label": spec.get("label") or spec["key"],
        "kind": spec.get("kind") or "num",
        "min": spec.get("min"),
        "max": spec.get("max"),
        "step": spec.get("step") if spec.get("step") is not None else 1,
        "default": spec.get("default"),
        "sort": int(spec.get("sort") or 0),
        "role": spec.get("role") or "other",
        "live": bool(spec.get("live", True)),
        "flatten_guard": bool(spec.get("flatten_guard")),
        "chart_line": spec.get("chart_line") or "",
        "hint": spec.get("hint") or "",
        "options": spec.get("options") or [],
        "enabled": True,
    }


def spec_catalog_field(spec):
    """Shape Spring / desk /api/wire/catalog expects."""
    row = _spec_row(spec) if "sort" in spec or "default" in spec else dict(spec)
    out = {
        "key": row["key"],
        "label": row.get("label") or row["key"],
        "kind": row.get("kind") or "num",
        "step": row.get("step") if row.get("step") is not None else 1,
        "role": row.get("role") or "other",
        "live": bool(row.get("live", True)),
        "flatten_guard": bool(row.get("flatten_guard")),
        "chart_line": row.get("chart_line") or "",
        "hint": row.get("hint") or "",
    }
    if row.get("min") is not None:
        out["min"] = row["min"]
    if row.get("max") is not None:
        out["max"] = row["max"]
    if row.get("default") is not None:
        out["default"] = row["default"]
    opts = row.get("options") or []
    if opts:
        out["options"] = opts
    return out


def specs_for(bot_id):
    ensure()
    bot_id = str(bot_id or "")
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                """
                SELECT bot_id, key, label, kind, minv, maxv, step, default_json,
                       sort_order, role, live, flatten_guard, chart_line, hint,
                       options_json, enabled
                FROM controller_spec
                WHERE bot_id=? AND enabled=1
                ORDER BY sort_order, key
                """,
                (bot_id,),
            ).fetchall()
        finally:
            con.close()
    out = []
    for r in rows:
        item = {
            "key": r["key"],
            "label": r["label"],
            "kind": r["kind"],
            "min": r["minv"],
            "max": r["maxv"],
            "step": r["step"],
            "default": _loads(r["default_json"]),
            "sort": r["sort_order"],
            "role": r["role"] or "other",
            "live": bool(r["live"]),
            "flatten_guard": bool(r["flatten_guard"]),
            "chart_line": r["chart_line"] or "",
            "hint": r["hint"] or "",
            "options": _loads(r["options_json"], []) or [],
            "enabled": bool(r["enabled"]),
        }
        out.append(spec_catalog_field(item))
    return out


def specs_catalog():
    """All rolled-out hunter packs: {bot_id: [catalog fields]}."""
    ensure()
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT DISTINCT bot_id FROM controller_spec WHERE enabled=1"
            ).fetchall()
        finally:
            con.close()
    return {r["bot_id"]: specs_for(r["bot_id"]) for r in rows}


def export_controller_specs(path=None):
    ensure()
    path = path or os.path.join(os.path.dirname(_DB_PATH or default_db_path()), "controller_specs.json")
    payload = {
        "ok": True,
        "pack": CONTROLLER_SPEC_PACK,
        "updated": _utc(),
        "closes": False,
        "positions_untouched": True,
        "fields": specs_catalog(),
    }
    raw = json.dumps(payload, indent=2, default=str)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def seed_controller_specs():
    """Upsert packs. Does not overwrite live kv values. PMM Simple only for now."""
    ensure()
    now = _utc()
    with _LOCK:
        con = _connect()
        try:
            for bot_id, rows in CONTROLLER_SPECS.items():
                for spec in rows:
                    con.execute(
                        """
                        INSERT INTO controller_spec(
                            bot_id, key, label, kind, minv, maxv, step, default_json,
                            sort_order, role, live, flatten_guard, chart_line, hint,
                            options_json, enabled, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                        ON CONFLICT(bot_id, key) DO UPDATE SET
                            label=excluded.label,
                            kind=excluded.kind,
                            minv=excluded.minv,
                            maxv=excluded.maxv,
                            step=excluded.step,
                            default_json=excluded.default_json,
                            sort_order=excluded.sort_order,
                            role=excluded.role,
                            live=excluded.live,
                            flatten_guard=excluded.flatten_guard,
                            chart_line=excluded.chart_line,
                            hint=excluded.hint,
                            options_json=excluded.options_json,
                            enabled=1,
                            updated_at=excluded.updated_at
                        """,
                        (
                            bot_id,
                            spec["key"],
                            spec.get("label") or spec["key"],
                            spec.get("kind") or "num",
                            spec.get("min"),
                            spec.get("max"),
                            spec.get("step") if spec.get("step") is not None else 1,
                            _json(spec.get("default")),
                            int(spec.get("sort") or 0),
                            spec.get("role") or "other",
                            1 if spec.get("live", True) else 0,
                            1 if spec.get("flatten_guard") else 0,
                            spec.get("chart_line") or "",
                            spec.get("hint") or "",
                            _json(spec.get("options") or []),
                            now,
                        ),
                    )
            con.execute(
                "INSERT OR REPLACE INTO meta(k, v) VALUES('controller_spec_pack', ?)",
                (str(CONTROLLER_SPEC_PACK),),
            )
            con.commit()
        finally:
            con.close()
    for bot_id, rows in CONTROLLER_SPECS.items():
        for spec in rows:
            if spec.get("default") is None:
                continue
            kv_set(bot_id, spec["key"], spec["default"], "spec-seed", only_if_missing=True)
    try:
        export_controller_specs()
    except Exception:
        pass
    return True


# Per-hunter named knob snapshots. Scoped by bot_id so hummingbot-bn-swing
# presets never apply to other containers. STD is locked.
PMM_STD_PRESET = {
    "scale_x": 0.3,
    "take_profit": 0.024,
    "trail_activation": 0.014,
    "trail_delta": 22,
    "rebuy_pct": 0.0018,
    "executor_refresh_time": 20,
    "sl_roe_pct": 40,
    "margin_pct": 0.60,
    "open_on_bias": False,
    "go_in_after_close": False,
}


def export_controller_presets(path=None):
    ensure()
    path = path or os.path.join(os.path.dirname(_DB_PATH or default_db_path()), "controller_presets.json")
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                """
                SELECT bot_id, name, values_json, locked, updated_at
                FROM controller_preset
                ORDER BY bot_id, locked DESC, name COLLATE NOCASE
                """
            ).fetchall()
        finally:
            con.close()
    by = {}
    for r in rows:
        by.setdefault(r["bot_id"], []).append({
            "name": r["name"],
            "locked": bool(r["locked"]),
            "updated": r["updated_at"],
            "values": _loads(r["values_json"], {}) or {},
        })
    payload = {
        "ok": True,
        "closes": False,
        "positions_untouched": True,
        "presets": by,
    }
    raw = json.dumps(payload, indent=2, default=str)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def preset_list(bot_id):
    ensure()
    bot_id = str(bot_id or "")
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                """
                SELECT name, values_json, locked, updated_at
                FROM controller_preset
                WHERE bot_id=?
                ORDER BY locked DESC, name COLLATE NOCASE
                """,
                (bot_id,),
            ).fetchall()
        finally:
            con.close()
    out = []
    for r in rows:
        out.append({
            "name": r["name"],
            "locked": bool(r["locked"]),
            "updated": r["updated_at"],
            "values": _loads(r["values_json"], {}) or {},
        })
    return out


def preset_get(bot_id, name):
    ensure()
    name = str(name or "").strip()
    with _LOCK:
        con = _connect()
        try:
            r = con.execute(
                "SELECT name, values_json, locked FROM controller_preset WHERE bot_id=? AND name=?",
                (str(bot_id or ""), name),
            ).fetchone()
        finally:
            con.close()
    if not r:
        return None
    return {
        "name": r["name"],
        "locked": bool(r["locked"]),
        "values": _loads(r["values_json"], {}) or {},
    }


def preset_save(bot_id, name, values, locked=False):
    """Save knobs for this hunter only. STD/standard is locked."""
    ensure()
    bot_id = str(bot_id or "").strip()
    name = " ".join(str(name or "").split())
    if not bot_id or not name:
        raise ValueError("bot_id and name required")
    if len(name) > 32:
        name = name[:32]
    low = name.lower()
    if low in ("std", "standard") and not locked:
        raise ValueError("STD is locked — pick another name")
    if low in ("std", "standard"):
        name = "STD"
        locked = True
    prev = preset_get(bot_id, name)
    if prev and prev.get("locked") and not locked:
        raise ValueError("cannot overwrite locked preset " + name)
    if not isinstance(values, dict):
        values = {}
    now = _utc()
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                """
                INSERT INTO controller_preset(bot_id, name, values_json, locked, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(bot_id, name) DO UPDATE SET
                    values_json=excluded.values_json,
                    locked=excluded.locked,
                    updated_at=excluded.updated_at
                """,
                (bot_id, name, _json(values), 1 if locked else 0, now),
            )
            con.commit()
        finally:
            con.close()
    try:
        export_controller_presets()
    except Exception:
        pass
    return name


def seed_controller_presets():
    """Locked STD pack per hunter. PMM uses PMM_STD_PRESET; others use spec defaults."""
    ensure()
    for bot_id, rows in CONTROLLER_SPECS.items():
        if bot_id == "hummingbot-bn-swing":
            preset_save(bot_id, "STD", dict(PMM_STD_PRESET), locked=True)
            continue
        vals = {}
        for spec in rows:
            if spec.get("default") is not None:
                vals[spec["key"]] = spec["default"]
        if vals:
            preset_save(bot_id, "STD", vals, locked=True)
    try:
        export_controller_presets()
    except Exception:
        pass
    return True


def bias_map(bot_id):
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                "SELECT coin, mode FROM bias WHERE bot_id=?",
                (str(bot_id),),
            ).fetchall()
        finally:
            con.close()
    return {r["coin"]: r["mode"] for r in rows}


def bias_updated_at(bot_id):
    """Newest sqlite bias stamp for this hunter, or None."""
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT MAX(updated_at) AS u FROM bias WHERE bot_id=?",
                (str(bot_id),),
            ).fetchone()
        finally:
            con.close()
    return row["u"] if row and row["u"] else None


def bias_set(bot_id, coin, mode, source="sql"):
    bot_id = str(bot_id or "")[:80]
    coin = str(coin or "").upper()[:12]
    mode = str(mode or "FLAT").upper()[:8]
    if mode not in ("LONG", "SHORT", "FLAT") or not bot_id or not coin:
        return False
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                """
                INSERT INTO bias(bot_id, coin, mode, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(bot_id, coin) DO UPDATE SET
                    mode=excluded.mode, updated_at=excluded.updated_at
                """,
                (bot_id, coin, mode, _utc()),
            )
            con.commit()
        finally:
            con.close()
    return True


def live_bias(bot_id, coins, valid=("LONG", "SHORT", "FLAT"), short_to_flat=False):
    """SQLite first. JSON file only if this hunter has no SQL bias rows."""
    coins = list(coins or ())
    m = bias_map(bot_id) or {}
    if not m:
        fname = BIAS_BOTS.get(str(bot_id) or "")
        for root in ("/conf", "/home/hummingbot/conf"):
            path = os.path.join(root, fname) if fname else ""
            if not path or not os.path.isfile(path):
                continue
            raw = _file_json(path)
            for c in coins:
                v = str(raw.get(c) or "").upper()
                if v in ("LONG", "SHORT", "FLAT"):
                    m[c] = v
            break
    if not m:
        return None
    out = {}
    for c in coins:
        v = str(m.get(c) or "FLAT").upper()
        if short_to_flat and v == "SHORT":
            v = "FLAT"
        out[c] = v if v in valid else "FLAT"
    return out


def bias_set_map(bot_id, mapping):
    n = 0
    for coin, mode in (mapping or {}).items():
        if str(coin).lower() in ("updated", "source", "coins", "bias"):
            continue
        if bias_set(bot_id, coin, mode):
            n += 1
    return n


def status_put(bot_id, payload):
    with _LOCK:
        con = _connect()
        try:
            con.execute(
                """
                INSERT INTO status(bot_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(bot_id) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (str(bot_id), _json(payload or {}), _utc()),
            )
            con.commit()
        finally:
            con.close()


def status_get(bot_id):
    with _LOCK:
        con = _connect()
        try:
            row = con.execute(
                "SELECT payload_json FROM status WHERE bot_id=?",
                (str(bot_id),),
            ).fetchone()
        finally:
            con.close()
    return _loads(row["payload_json"], {}) if row else {}


def try_status_put(bot_id, payload):
    try:
        ensure()
        status_put(bot_id, payload)
        return True
    except Exception:
        return False


def command_enqueue(bot_id, kind, payload=None, source="sql"):
    """Queue a hunter command (close, open, follow, …). Does not place orders."""
    bot_id = str(bot_id or "")[:80]
    kind = str(kind or "").strip().lower()[:40]
    if not bot_id or not kind:
        return None
    body = payload if isinstance(payload, dict) else {}
    if source and "source" not in body:
        body = dict(body)
        body["source"] = str(source)[:40]
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                """
                INSERT INTO commands(bot_id, kind, payload_json, status, created_at)
                VALUES (?, ?, ?, 'pending', ?)
                """,
                (bot_id, kind, _json(body), _utc()),
            )
            cid = cur.lastrowid
            con.commit()
            return cid
        finally:
            con.close()


def command_next(bot_id, kinds=None):
    """Oldest pending command for this hunter. Does not mark it done."""
    bot_id = str(bot_id or "")
    kinds = [str(k).strip().lower() for k in (kinds or []) if str(k).strip()]
    with _LOCK:
        con = _connect()
        try:
            if kinds:
                qmarks = ",".join("?" * len(kinds))
                row = con.execute(
                    f"""
                    SELECT id, bot_id, kind, payload_json, created_at
                    FROM commands
                    WHERE bot_id=? AND status='pending' AND kind IN ({qmarks})
                    ORDER BY id
                    LIMIT 1
                    """,
                    (bot_id, *kinds),
                ).fetchone()
            else:
                row = con.execute(
                    """
                    SELECT id, bot_id, kind, payload_json, created_at
                    FROM commands
                    WHERE bot_id=? AND status='pending'
                    ORDER BY id
                    LIMIT 1
                    """,
                    (bot_id,),
                ).fetchone()
        finally:
            con.close()
    if not row:
        return None
    return {
        "id": row["id"],
        "bot_id": row["bot_id"],
        "kind": row["kind"],
        "payload": _loads(row["payload_json"], {}) or {},
        "created_at": row["created_at"],
    }


def command_finish(cmd_id, note=None):
    if not cmd_id:
        return False
    extra = {"note": str(note)[:200]} if note else None
    with _LOCK:
        con = _connect()
        try:
            if extra:
                row = con.execute(
                    "SELECT payload_json FROM commands WHERE id=?",
                    (int(cmd_id),),
                ).fetchone()
                payload = _loads(row["payload_json"], {}) if row else {}
                if not isinstance(payload, dict):
                    payload = {}
                payload.update(extra)
                con.execute(
                    """
                    UPDATE commands
                    SET status='done', done_at=?, payload_json=?
                    WHERE id=? AND status='pending'
                    """,
                    (_utc(), _json(payload), int(cmd_id)),
                )
            else:
                con.execute(
                    """
                    UPDATE commands
                    SET status='done', done_at=?
                    WHERE id=? AND status='pending'
                    """,
                    (_utc(), int(cmd_id)),
                )
            con.commit()
        finally:
            con.close()
    return True


def close_coin_list(payload, coins):
    """Coins a close command applies to. Empty payload = all hunter coins."""
    coins = [str(c).upper() for c in (coins or [])]
    allowed = set(coins)
    if not isinstance(payload, dict):
        raw = str(payload or "").strip().upper()
        if raw in allowed:
            return [raw]
        return list(coins)
    if payload.get("all") is True:
        return list(coins)
    one = str(payload.get("coin") or "").upper()
    if one in allowed:
        return [one]
    many = payload.get("coins")
    if isinstance(many, list):
        picked = [str(c).upper() for c in many if str(c).upper() in allowed]
        if picked:
            return picked
    raw = str(payload.get("raw") or payload.get("target") or "").strip().upper()
    if raw in allowed:
        return [raw]
    if raw in ("ALL", "*", "EVERYTHING", "BOTH", ""):
        return list(coins)
    return list(coins)


HISTORY_KEEP = 8


def history_push(label, graph, controllers=None, bias=None, pct=None):
    """Keep the last few recoverable canvas states. Does not change hunter positions."""
    with _LOCK:
        con = _connect()
        try:
            cur = con.execute(
                """
                INSERT INTO wire_history(
                    label, graph_json, controllers_json, bias_json, pct_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(label or "step")[:120],
                    _json(graph or {}),
                    _json(controllers or {}),
                    _json(bias or {}),
                    _json(pct or {}),
                    _utc(),
                ),
            )
            sid = cur.lastrowid
            con.execute(
                """
                DELETE FROM wire_history WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id FROM wire_history ORDER BY id DESC LIMIT ?
                    )
                )
                """,
                (HISTORY_KEEP,),
            )
            con.commit()
            return sid
        finally:
            con.close()


def history_list(limit=8):
    limit = max(1, min(int(limit or 8), HISTORY_KEEP))
    with _LOCK:
        con = _connect()
        try:
            rows = con.execute(
                """
                SELECT id, label, graph_json, controllers_json, bias_json, pct_json, created_at
                FROM wire_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            con.close()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "label": r["label"],
            "graph": _loads(r["graph_json"], {"nodes": [], "edges": []}),
            "controllers": _loads(r["controllers_json"], {}),
            "bias": _loads(r["bias_json"], {}),
            "pct": _loads(r["pct_json"], {}),
            "created_at": r["created_at"],
        })
    return out


def import_conf(conf_dir):
    """Load existing JSON into empty SQL slots. Never overwrites a key already in SQL."""
    conf_dir = conf_dir or os.path.dirname(default_db_path())
    n = 0
    sl = _file_json(os.path.join(conf_dir, "bot_sl.json"))
    for bot_id, row in sl.items():
        if not isinstance(row, dict):
            continue
        if row.get("sl_roe_pct") is not None and kv_set(bot_id, "sl_roe_pct", row.get("sl_roe_pct"), "import", True):
            n += 1
        if row.get("time_limit_h") is not None and kv_set(bot_id, "time_limit_h", row.get("time_limit_h"), "import", True):
            n += 1
        if row.get("time_limit_s") is not None and kv_set(bot_id, "time_limit_s", row.get("time_limit_s"), "import", True):
            n += 1
        if row.get("hold_h") is not None and kv_set(bot_id, "hold_h", row.get("hold_h"), "import", True):
            n += 1

    for bot_id in HUNTER_IDS:
        ctrl = _file_json(os.path.join(conf_dir, f"{bot_id}_ctrl.json"))
        for k, v in ctrl.items():
            if k in ("updated", "source") or v is None:
                continue
            if kv_set(bot_id, k, v, "import", True):
                n += 1

    tune = _file_json(os.path.join(conf_dir, "dman_tune.json"))
    for k in ("trail_activation", "trail_delta", "bb_long_threshold", "bb_short_threshold"):
        if tune.get(k) is not None and kv_set("hummingbot-dman", k, tune.get(k), "import", True):
            n += 1

    rt = _file_json(os.path.join(conf_dir, "hl_copy_runtime.json"))
    for k, v in rt.items():
        if k in ("ts", "updated", "source") or v is None:
            continue
        if kv_set("hummingbot-hl", k, v, "import", True):
            n += 1

    moon = _file_json(os.path.join(conf_dir, "onlyup_moon.json"))
    coins = {k: v for k, v in moon.items() if k not in ("updated", "source") and isinstance(v, dict)}
    if coins and kv_set("hummingbot-onlyup", "moon", coins, "import", True):
        n += 1

    for bot_id, fname in BIAS_BOTS.items():
        if bias_map(bot_id):
            continue
        raw = _file_json(os.path.join(conf_dir, fname))
        added = bias_set_map(bot_id, raw)
        n += added
    return n


def reconcile_json(conf_dir):
    """If a JSON dump is newer than sqlite, copy it in. Does not close or open."""
    conf_dir = conf_dir or os.path.dirname(default_db_path())
    n = 0
    for bot_id, fname in BIAS_BOTS.items():
        path = os.path.join(conf_dir, fname)
        raw = _file_json(path)
        modes = {}
        for k, v in (raw or {}).items():
            if str(k).lower() in ("updated", "source", "coins", "bias"):
                continue
            m = str(v or "").upper()
            if m in ("LONG", "SHORT", "FLAT"):
                modes[str(k).upper()] = m
        if not modes:
            continue
        file_ts = _parse_stamp(raw.get("updated"))
        if file_ts is None and os.path.isfile(path):
            try:
                file_ts = datetime.utcfromtimestamp(os.path.getmtime(path))
            except Exception:
                file_ts = None
        sql = {str(k).upper(): str(v).upper() for k, v in (bias_map(bot_id) or {}).items()}
        sql_ts = _parse_stamp(bias_updated_at(bot_id))
        if sql and (file_ts is None or (sql_ts and file_ts <= sql_ts)):
            continue
        if modes != sql:
            n += bias_set_map(bot_id, modes)
    sl = _file_json(os.path.join(conf_dir, "bot_sl.json"))
    for bot_id, row in (sl or {}).items():
        if not isinstance(row, dict):
            continue
        file_ts = _parse_stamp(row.get("updated"))
        if row.get("sl_roe_pct") is not None:
            sql_ts = _parse_stamp(kv_updated_at(bot_id, "sl_roe_pct"))
            if file_ts and (sql_ts is None or file_ts > sql_ts):
                kv_set(bot_id, "sl_roe_pct", row.get("sl_roe_pct"), "reconcile")
                n += 1
        if row.get("time_limit_h") is not None:
            sql_ts = _parse_stamp(kv_updated_at(bot_id, "time_limit_h"))
            if file_ts and (sql_ts is None or file_ts > sql_ts):
                kv_set(bot_id, "time_limit_h", row.get("time_limit_h"), "reconcile")
                n += 1
    for bot_id in HUNTER_IDS:
        path = os.path.join(conf_dir, bot_id + "_ctrl.json")
        ctrl = _file_json(path)
        if not ctrl:
            continue
        file_ts = _parse_stamp(ctrl.get("updated"))
        sql_ts = _parse_stamp(kv_updated_at(bot_id))
        if not file_ts or (sql_ts and file_ts <= sql_ts):
            continue
        for k, v in ctrl.items():
            if k in ("updated", "source") or v is None:
                continue
            kv_set(bot_id, k, v, "reconcile")
            n += 1
    return n

