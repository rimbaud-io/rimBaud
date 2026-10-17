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

"""Condor bias gauge — measures tape, never places Hyperliquid orders.

Writes conf/condor_gauge.json. If Wire has gauge-condor REGIME → hunter BIAS,
also writes that hunter's bias JSON (and SQLite if matrix_store is present).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONF_DIR = os.environ.get("CONF_DIR", "/conf")
LOGS_DIR = os.environ.get("LOGS_DIR", "/logs")
GAUGE_FILE = os.path.join(CONF_DIR, "condor_gauge.json")
GRAPH_FILE = os.path.join(CONF_DIR, "wire_graph.json")
SETUP_FILE = os.path.join(CONF_DIR, "condor_setup.json")
FEED_FILE = os.path.join(CONF_DIR, "condor_feed.json")
XLATE_FILE = os.path.join(CONF_DIR, "condor_xlate.json")
DESK_BIAS_FILE = os.path.join(CONF_DIR, "desk_bias.json")
GROK_BIAS_FILE = os.path.join(CONF_DIR, "grok_bias.json")
KOORD_CMD_FILE = os.path.join(CONF_DIR, "koordinator_cmd.json")
DASHBOARD_URL = (os.environ.get("DASHBOARD_URL") or "http://hummingbot-dashboard:5000").rstrip("/")
REGISTRY_FILE = os.path.join(CONF_DIR, "wire_registry.json")
LOG_FILE = os.path.join(LOGS_DIR, "condor_gauge.log")
INTERVAL = max(60, int(os.environ.get("CONDOR_INTERVAL", "600")))
PORT = int(os.environ.get("CONDOR_PORT", "8088"))
XAI_KEY = (os.environ.get("XAI_API_KEY") or "").strip()
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4.5")
XAI_URL = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1/chat/completions")
TG_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("CONDOR_TELEGRAM_TOKEN") or "").strip()
TG_CHATS = [
    x.strip()
    for x in (os.environ.get("TELEGRAM_CHAT_IDS") or os.environ.get("TELEGRAM_CHAT_ID") or "").split(",")
    if x.strip()
]
# Unsolicited CONDOR CALM / BIAS posts. Off by default — hunters do not read Telegram.
# /status and /tick still reply. Set CONDOR_TG_GAUGE=1 to push on each regime change.
TG_GAUGE = os.environ.get("CONDOR_TG_GAUGE", "").strip().lower() in ("1", "true", "yes", "on")

COINS = ("BTC", "ETH", "LTC", "TAO")
VALID = ("LONG", "SHORT", "FLAT")
REGIMES = ("calm", "trend", "shock")
PHONE_GROK_ID = "grok-bot-telegram"
GROK_MODELS = ("grok-4.5", "grok-4.6", "grok-4-fast-non-reasoning")

HUNTERS = {
    "hummingbot-bn-swing": {"bias": "bn_swing_bias.json", "coins": ["BTC", "ETH", "LTC", "TAO"], "mode": "both", "status": "bn_swing_status.json"},
    "hummingbot-helliot": {"bias": "helliot_bias.json", "coins": ["BTC", "ETH", "LTC", "TAO"], "mode": "both", "status": "helliot_status.json"},
    "hummingbot-surfn": {"bias": "surfn_bias.json", "coins": ["BTC", "ETH", "LTC", "TAO"], "mode": "both", "status": "surfn_status.json"},
    "hummingbot-shortn": {"bias": "shortn_bias.json", "coins": ["BTC", "ETH", "LTC", "TAO"], "mode": "both", "status": "shortn_status.json"},
    "hummingbot-dman": {"bias": "dman_bias.json", "coins": ["BTC", "ETH", "LTC", "TAO"], "mode": "both", "status": "dman_status.json"},
    "hummingbot-onlyup": {"bias": "onlyup_bias.json", "coins": ["BTC", "LTC", "TAO"], "mode": "both", "status": "onlyup_status.json"},
    "hummingbot-dman-maker": {"bias": "dman_maker_bias.json", "coins": ["BTC", "ETH", "LTC", "TAO"], "mode": "both", "status": "dman_maker_status.json"},
}

TRADE_FILES = {
    "hummingbot-bn-swing": "bn_swing_trades.json",
    "hummingbot-helliot": "helliot_trades.json",
    "hummingbot-surfn": "surfn_trades.json",
    "hummingbot-shortn": "shortn_trades.json",
    "hummingbot-dman": "dman_trades.json",
    "hummingbot-onlyup": "onlyup_trades.json",
    "hummingbot-dman-maker": "dman_maker_trades.json",
    "hummingbot-hl": "copy_trades.log",
}
HUNTER_NAMES = {
    "hummingbot-bn-swing": "PMM Simple",
    "hummingbot-helliot": "Helliot",
    "hummingbot-surfn": "Surfn",
    "hummingbot-shortn": "Shorty",
    "hummingbot-dman": "Dman",
    "hummingbot-onlyup": "Only Up",
    "hummingbot-dman-maker": "DMan Maker",
    "hummingbot-hl": "HL Copy",
}
POSITIONS_CURSOR = os.path.join(CONF_DIR, "condor_positions_cursor.json")
POSITIONS_POLL_S = 2

_lock = threading.Lock()
_tick_lock = threading.Lock()
_last = {}
_tg_state = {"ok": False, "configured": bool(TG_TOKEN), "chats": list(TG_CHATS), "last_error": None, "last_send": None}
_tg_workers = {}
_tg_off_logged = False
_feed_lock = threading.Lock()
FEED_KEEP = 80


def _utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(msg):
    line = f"{_utc()}  {msg}"
    print(line, flush=True)
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _read_json(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    if path == SETUP_FILE:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _feed_plain(s):
    t = re.sub(r"<[^>]+>", "", str(s or ""))
    t = re.sub(r"xai-[A-Za-z0-9_-]+", "xai-••••", t, flags=re.I)
    bits = [x.strip() for x in t.splitlines() if x.strip()]
    return " · ".join(bits)[:280]


def feed_append(kind, text, src=""):
    plain = _feed_plain(text)
    if not plain:
        return
    row = {
        "ts": _utc(),
        "kind": str(kind or "out")[:12],
        "src": str(src or "")[:40],
        "text": plain,
    }
    with _feed_lock:
        data = _read_json(FEED_FILE)
        lines = data.get("lines") if isinstance(data.get("lines"), list) else []
        lines.append(row)
        try:
            _write_json(FEED_FILE, {"lines": lines[-FEED_KEEP:]})
        except OSError:
            pass


def _f(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def collect_tape():
    tape = {c: {"mid": None, "bbp": None, "trend_up": None, "src": None} for c in COINS}
    order = (
        "bn_swing_status.json",
        "helliot_status.json",
        "surfn_status.json",
        "dman_status.json",
        "onlyup_status.json",
        "shortn_status.json",
        "dman_maker_status.json",
    )
    for fname in order:
        st = _read_json(os.path.join(CONF_DIR, fname))
        positions = st.get("positions") if isinstance(st.get("positions"), dict) else {}
        for coin in COINS:
            pos = positions.get(coin) if isinstance(positions.get(coin), dict) else {}
            row = tape[coin]
            mid = _f(pos.get("mid_price") or pos.get("mark") or st.get("mid_price"))
            bbp = _f(pos.get("bbp"))
            tu = pos.get("trend_up")
            if tu is None:
                tu = st.get("trend_up")
            if row["mid"] is None and mid is not None:
                row["mid"] = mid
            if row["bbp"] is None and bbp is not None:
                row["bbp"] = bbp
                row["src"] = fname.replace("_status.json", "")
            if row["trend_up"] is None and tu is not None:
                row["trend_up"] = bool(tu)
                if not row["src"]:
                    row["src"] = fname.replace("_status.json", "")
    return tape


def heuristic(tape):
    bbps = [tape[c]["bbp"] for c in COINS if tape[c]["bbp"] is not None]
    trends = [tape[c]["trend_up"] for c in COINS if tape[c]["trend_up"] is not None]
    avg_bbp = sum(bbps) / len(bbps) if bbps else 0.5
    up_n = sum(1 for t in trends if t)
    down_n = sum(1 for t in trends if t is False)
    if down_n >= 2 and avg_bbp >= 0.7:
        regime = "shock"
        why = f"dump tape: {down_n}/3 not up, avg BBP {avg_bbp:.2f}"
    elif up_n >= 2 and 0.28 <= avg_bbp <= 0.72:
        regime = "trend"
        why = f"1h up + quiet: {up_n}/3 up, avg BBP {avg_bbp:.2f}"
    else:
        regime = "calm"
        why = f"chop: avg BBP {avg_bbp:.2f}, trend up {up_n} down {down_n}"

    bias = {}
    reasons = {}
    for coin in COINS:
        bbp = tape[coin]["bbp"]
        tu = tape[coin]["trend_up"]
        if regime == "shock":
            mode = "SHORT"
            reasons[coin] = "panic / dump"
        elif regime == "trend":
            if tu is False:
                mode = "SHORT"
                reasons[coin] = "trend but this coin not up"
            else:
                mode = "LONG"
                reasons[coin] = "trend up"
        else:
            if bbp is not None and bbp <= 0.28:
                mode = "LONG"
                reasons[coin] = f"stretched down BBP {bbp:.2f}"
            elif bbp is not None and bbp >= 0.72:
                mode = "SHORT"
                reasons[coin] = f"stretched up BBP {bbp:.2f}"
            else:
                mode = "FLAT"
                reasons[coin] = "mid-band chop"
        bias[coin] = mode
    return regime, bias, why, reasons


def _parse_chats(v):
    if isinstance(v, list):
        parts = v
    else:
        parts = str(v or "").replace(";", ",").split(",")
    return [str(p).strip() for p in parts if str(p).strip()]


def load_setup():
    """Wired Telegram / Grok bots from Wire. Env is fallback when none are ready."""
    data = _read_json(SETUP_FILE)
    bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
    wired = data.get("wired") if isinstance(data.get("wired"), dict) else {}
    telegram = []
    tg_ids = [str(x) for x in (wired.get("telegram") or [])]
    if not tg_ids:
        tg_ids = [
            nid for nid, row in bots.items()
            if isinstance(row, dict)
            and str(row.get("kind") or "telegram") != "grok"
            and str(row.get("token") or "").strip()
        ]
    for nid in tg_ids:
        row = bots.get(nid) if isinstance(bots.get(nid), dict) else {}
        token = str(row.get("token") or "").strip()
        if not token:
            continue
        telegram.append({
            "id": nid,
            "name": str(row.get("name") or "Telegram")[:60],
            "token": token,
            "chats": _parse_chats(row.get("chat_ids")),
        })
    grok = None
    for nid in [str(x) for x in (wired.get("grok") or [])]:
        row = bots.get(nid) if isinstance(bots.get(nid), dict) else {}
        key = str(row.get("api_key") or "").strip()
        if not key:
            continue
        grok = {
            "id": nid,
            "name": str(row.get("name") or "Grok")[:60],
            "api_key": key,
            "model": str(row.get("model") or XAI_MODEL or "grok-4.5").strip() or "grok-4.5",
        }
        break
    if not telegram and TG_TOKEN:
        telegram = [{
            "id": "env",
            "name": "env",
            "token": TG_TOKEN,
            "chats": list(TG_CHATS),
        }]
    if grok is None and XAI_KEY:
        grok = {
            "id": "env",
            "name": "env",
            "api_key": XAI_KEY,
            "model": XAI_MODEL or "grok-4.5",
        }
    return telegram, grok


def _looks_like_xai_key(s):
    t = str(s or "").strip().strip("`").strip()
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    if t.lower().startswith("xai-") and len(t) >= 16:
        return t
    return ""


def _mask_key(s):
    s = str(s or "")
    if len(s) < 10:
        return "••••"
    return s[:4] + "••••" + s[-4:]


def upsert_phone_grok(api_key=None, model=None, clear=False):
    """Save a Grok key sent from Telegram. Survives Wire canvas saves."""
    data = _read_json(SETUP_FILE)
    bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
    wired = data.get("wired") if isinstance(data.get("wired"), dict) else {}
    grok_ids = [str(x) for x in (wired.get("grok") or []) if str(x).strip()]
    tg_ids = [str(x) for x in (wired.get("telegram") or []) if str(x).strip()]
    nid = PHONE_GROK_ID
    if clear:
        bots.pop(nid, None)
        grok_ids = [x for x in grok_ids if x != nid]
        row = None
    else:
        row = bots.get(nid) if isinstance(bots.get(nid), dict) else {}
        row["id"] = nid
        row["kind"] = "grok"
        row["name"] = str(row.get("name") or "Grok (Telegram)")[:60]
        if api_key:
            row["api_key"] = str(api_key).strip()
        if model:
            row["model"] = str(model).strip()[:80]
        if not row.get("model"):
            row["model"] = XAI_MODEL or "grok-4.5"
        bots[nid] = row
        grok_ids = [x for x in grok_ids if x != nid]
        grok_ids.insert(0, nid)
    payload = {
        "updated": _utc(),
        "bots": bots,
        "wired": {"telegram": tg_ids, "grok": grok_ids},
    }
    _write_json(SETUP_FILE, payload)
    return row


def grok_chat(question, api_key=None, model=None):
    """Plain-language Grok reply about the desk. Never places orders."""
    api_key = (api_key or "").strip()
    model = (model or XAI_MODEL or "grok-4.5").strip() or "grok-4.5"
    if not api_key:
        raise RuntimeError("no grok api key")
    with _lock:
        last = dict(_last) if _last else _read_json(GAUGE_FILE)
    context = {
        "regime": last.get("regime"),
        "bias": last.get("bias"),
        "why": last.get("why"),
        "source": last.get("source"),
        "tape": last.get("tape") or collect_tape(),
        "wired": last.get("wired"),
        "ts": last.get("ts"),
    }
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 500,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Condor, a bias gauge for a Hyperliquid desk. "
                    "You never place orders and never give wallet keys. "
                    "Answer in short plain text for Telegram (no markdown tables). "
                    "Bias is LONG/SHORT/FLAT for hunter bots, not an order."
                ),
            },
            {
                "role": "user",
                "content": "Desk now:\n" + json.dumps(context, separators=(",", ":"))
                + "\n\nQuestion:\n" + str(question or "")[:800],
            },
        ],
    }
    req = urllib.request.Request(
        XAI_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    text = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return text[:3500] or "(empty grok reply)"


def grok_predict(tape, api_key=None, model=None):
    api_key = (api_key or XAI_KEY or "").strip()
    model = (model or XAI_MODEL or "grok-4.5").strip() or "grok-4.5"
    if not api_key:
        raise RuntimeError("no grok api key")
    payload = {
        "model": model,
        "temperature": 0.1,
        "max_tokens": 400,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are Condor, a regime gauge for Hyperliquid perps. "
                    "You never place orders. Reply with JSON only: "
                    '{"regime":"calm|trend|shock","bias":{"BTC":"LONG|SHORT|FLAT",'
                    '"ETH":"...","LTC":"..."},"why":"one short sentence",'
                    '"reasons":{"BTC":"...","ETH":"...","LTC":"..."}}. '
                    "calm=chop/mean-revert, trend=directional quiet, shock=dump/vol spike. "
                    "Bias is the door for hunter bots (LONG/SHORT/FLAT), not an order."
                ),
            },
            {
                "role": "user",
                "content": "Tape now:\n" + json.dumps(tape, separators=(",", ":")),
            },
        ],
    }
    req = urllib.request.Request(
        XAI_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    text = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    data = json.loads(text)
    regime = str(data.get("regime") or "calm").lower()
    if regime not in REGIMES:
        regime = "calm"
    bias = {}
    for coin in COINS:
        v = str((data.get("bias") or {}).get(coin) or "FLAT").upper()
        bias[coin] = v if v in VALID else "FLAT"
    why = str(data.get("why") or "grok")[:240]
    reasons = data.get("reasons") if isinstance(data.get("reasons"), dict) else {}
    return regime, bias, why, {c: str(reasons.get(c) or "")[:80] for c in COINS}


def clip_mode(mode, hunter_mode):
    mode = str(mode or "FLAT").upper()
    if mode not in VALID:
        mode = "FLAT"
    if hunter_mode == "long" and mode == "SHORT":
        return "FLAT"
    if hunter_mode == "short" and mode == "LONG":
        return "FLAT"
    return mode


def _hunter_coin_from_graph(bot_id, nodes, edges):
    by_id = {str(n.get("id")): n for n in nodes if isinstance(n, dict) and n.get("id")}
    for e in edges:
        if str(e.get("target") or "") != bot_id:
            continue
        if str(e.get("targetHandle") or "") != "coin":
            continue
        src = by_id.get(str(e.get("source") or ""))
        if not src:
            continue
        d = src.get("data") if isinstance(src.get("data"), dict) else {}
        coin = str(d.get("coin") or d.get("name") or "").upper()
        if coin:
            return coin
    return None


def condor_node_ids(nodes):
    ids = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "")
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        if nid == "gauge-condor" or (n.get("type") == "gauge" and data.get("kind") == "condor"):
            ids.add(nid)
    return ids


def hunters_from_eclose_sources(graph, srcs):
    """Hunters whose E-CLOSE port is wired from these Close-translator nodes. Includes Copy."""
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    by_id = {str(n.get("id")): n for n in nodes if n.get("id")}
    srcs = {str(x) for x in (srcs or []) if str(x)}
    found = []
    if not srcs:
        return []
    for e in edges:
        if str(e.get("source") or "") not in srcs:
            continue
        if str(e.get("sourceHandle") or "") not in ("eclose", "kill"):
            continue
        tgt = by_id.get(str(e.get("target") or ""))
        if not tgt or tgt.get("type") != "bot":
            continue
        if str(e.get("targetHandle") or "") not in ("eclose", "kill"):
            continue
        bid = str(tgt.get("id") or "")
        if bid in HUNTERS or bid == "hummingbot-hl":
            if bid not in found:
                found.append(bid)
    return [(bid, None) for bid in found]


def hunters_from_sources(graph, srcs):
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    by_id = {str(n.get("id")): n for n in nodes if n.get("id")}
    srcs = {str(x) for x in (srcs or []) if str(x)}
    if not srcs:
        return []
    found = []

    def add(bot_id, coin):
        if not bot_id or bot_id not in HUNTERS or bot_id == "hummingbot-hl":
            return
        found.append((bot_id, coin))

    for e in edges:
        if str(e.get("source") or "") not in srcs:
            continue
        sh = str(e.get("sourceHandle") or "bias")
        if sh not in ("bias", ""):
            continue
        tgt = by_id.get(str(e.get("target") or ""))
        if not tgt:
            continue
        th = str(e.get("targetHandle") or "")
        if tgt.get("type") == "bot" and th in ("bias", ""):
            add(str(tgt.get("id")), _hunter_coin_from_graph(str(tgt.get("id")), nodes, edges))
        elif tgt.get("type") == "bias" and th in ("in", "bias", ""):
            data = tgt.get("data") if isinstance(tgt.get("data"), dict) else {}
            bot_id = str(data.get("botId") or "")
            if not bot_id and str(tgt.get("id") or "").startswith("bias-"):
                bot_id = str(tgt.get("id"))[5:]
            coin = None
            for e2 in edges:
                if str(e2.get("source") or "") != str(tgt.get("id")):
                    continue
                t2 = by_id.get(str(e2.get("target") or ""))
                if t2 and t2.get("type") == "bot":
                    add(str(t2.get("id")), _hunter_coin_from_graph(str(t2.get("id")), nodes, edges))
            add(bot_id, coin)
    # unique, last coin wins
    out = {}
    for bid, coin in found:
        out[bid] = coin
    return [(bid, coin) for bid, coin in out.items()]


def wired_hunters(graph):
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    return hunters_from_sources(graph, condor_node_ids(nodes))


def _xlate_kind(n):
    if not isinstance(n, dict):
        return None
    data = n.get("data") if isinstance(n.get("data"), dict) else {}
    k = str(data.get("kind") or "")
    if k == "closer":
        return "closer"
    if n.get("type") == "xlate" or k == "translator":
        return "translator"
    return None


def _is_xlate(n):
    return _xlate_kind(n) is not None


def _xlate_node(graph, node_id):
    nid = str(node_id or "")
    for n in (graph.get("nodes") or []) if isinstance(graph, dict) else []:
        if isinstance(n, dict) and str(n.get("id") or "") == nid:
            return n
    return None


def translators_for_telegram(graph, tg_id):
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    by_id = {str(n.get("id")): n for n in nodes if n.get("id")}
    tg_id = str(tg_id or "")
    out = []
    for e in edges:
        if str(e.get("source") or "") != tg_id:
            continue
        tgt = by_id.get(str(e.get("target") or ""))
        if tgt and _is_xlate(tgt):
            nid = str(tgt.get("id") or "")
            if nid and nid not in out:
                out.append(nid)
    return out


def _is_desk_bias(n):
    if not isinstance(n, dict):
        return False
    data = n.get("data") if isinstance(n.get("data"), dict) else {}
    return n.get("type") == "desk_bias" or str(data.get("kind") or "") == "desk"


def desk_bias_nodes_for_telegram(graph, tg_id):
    """Telegram OUT → Desk BIAS TG. LONG/FLAT/SHORT only — never CLOSE."""
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    by_id = {str(n.get("id")): n for n in nodes if n.get("id")}
    tg_id = str(tg_id or "")
    out = []
    for e in edges:
        if str(e.get("source") or "") != tg_id:
            continue
        sh = str(e.get("sourceHandle") or "")
        th = str(e.get("targetHandle") or "")
        if sh not in ("out", "tg", ""):
            continue
        if th not in ("tg", "in", ""):
            continue
        tgt = by_id.get(str(e.get("target") or ""))
        if tgt and _is_desk_bias(tgt):
            nid = str(tgt.get("id") or "")
            if nid and nid not in out:
                out.append(nid)
    return out


def write_desk_bias(mode, source="telegram"):
    """Queue desk-wide next BIAS. Does not close or open positions."""
    mode = str(mode or "").upper()
    if mode not in VALID:
        return "desk BIAS must be LONG, FLAT, or SHORT"
    payload = {
        "bias": mode,
        "updated": _utc(),
        "source": source,
        "closes": False,
    }
    try:
        _write_json(DESK_BIAS_FILE, payload)
    except Exception as e:
        return "desk BIAS file failed: " + str(e)
    try:
        body = json.dumps({"bias": mode}).encode("utf-8")
        req = urllib.request.Request(
            DASHBOARD_URL + "/api/home/desk_bias",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            out = json.loads(resp.read().decode("utf-8") or "{}")
            if out.get("closes"):
                return "refusing dashboard result that reported a close"
            return out.get("message") or ("Desk BIAS " + mode + " — does not close")
    except Exception as e:
        _log(f"desk bias dashboard: {e}")
        return (
            "Desk BIAS " + mode + " saved (does not close). "
            "Flask will apply after hunters autoclose."
        )


def xlate_save(node_id, cmd, coins, notes, raw=""):
    data = _read_json(XLATE_FILE)
    nodes = data.get("nodes") if isinstance(data.get("nodes"), dict) else {}
    nodes[str(node_id)] = {
        "cmd": str(cmd or "")[:12],
        "coins": list(coins or []),
        "ts": _utc(),
        "raw": str(raw or "")[:80],
        "notes": list(notes or [])[:12],
    }
    _write_json(XLATE_FILE, {"nodes": nodes})


def _sql_write(bot_id, mapping):
    try:
        import matrix_store
        matrix_store.ensure()
        matrix_store.bias_set_map(bot_id, mapping)
    except Exception:
        pass


def write_hunter_bias(bot_id, coin_bias):
    spec = HUNTERS.get(bot_id)
    if not spec:
        return None
    coins = list(spec["coins"])
    current = {c: "FLAT" for c in coins}
    sql_map = {}
    try:
        import matrix_store
        matrix_store.ensure()
        sql_map = matrix_store.bias_map(bot_id) or {}
    except Exception:
        sql_map = {}
    raw = sql_map or _read_json(os.path.join(CONF_DIR, spec["bias"]))
    for c in coins:
        v = str(raw.get(c) or "").upper()
        if v in VALID:
            current[c] = v
    changed = []
    for coin, mode in coin_bias.items():
        coin = str(coin).upper()
        if coin not in coins:
            continue
        mode = clip_mode(mode, spec["mode"])
        if current.get(coin) != mode:
            current[coin] = mode
            changed.append(f"{coin}={mode}")
    if not changed:
        return None
    payload = dict(current)
    payload["updated"] = _utc()
    payload["source"] = "condor"
    _sql_write(bot_id, payload)
    try:
        _write_json(os.path.join(CONF_DIR, spec["bias"]), payload)
    except Exception as e:
        _log(f"bias json {bot_id}: {e}")
    return ",".join(changed)


def apply_wired(bias):
    graph = _read_json(GRAPH_FILE)
    applied = []
    for bot_id, wired_coin in wired_hunters(graph):
        spec = HUNTERS.get(bot_id) or {}
        coins = [wired_coin] if wired_coin else list(spec.get("coins") or COINS)
        mapping = {c: bias.get(c, "FLAT") for c in coins if c}
        note = write_hunter_bias(bot_id, mapping)
        if note:
            applied.append(f"{bot_id} {note}")
    return applied


def _tg_esc(s):
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _tg_api(token, method, params=None, timeout=30):
    token = (token or "").strip()
    if not token:
        raise RuntimeError("telegram token empty")
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params is not None:
        data = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
    else:
        req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def tg_send_bot(bot, text, chat_id=None):
    token = str((bot or {}).get("token") or "").strip()
    if not token:
        return False
    if chat_id is not None:
        targets = [str(chat_id)]
    else:
        targets = [str(c) for c in ((bot or {}).get("chats") or [])]
    if not targets:
        return False
    ok_any = False
    for cid in targets:
        try:
            out = _tg_api(token, "sendMessage", {
                "chat_id": cid,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }, timeout=20)
            if out.get("ok"):
                ok_any = True
                with _lock:
                    _tg_state["ok"] = True
                    _tg_state["configured"] = True
                    _tg_state["last_send"] = _utc()
                    _tg_state["last_error"] = None
            else:
                _log(f"telegram send {bot.get('id')} {cid} not ok: {out}")
        except Exception as e:
            err = str(e)[:200]
            _log(f"telegram send {bot.get('id')} {cid} failed: {err}")
            with _lock:
                _tg_state["last_error"] = err
    if ok_any:
        feed_append("tg-out", text, (bot or {}).get("name") or (bot or {}).get("id") or "telegram")
    return ok_any


def tg_delete(bot, chat_id, message_id):
    token = str((bot or {}).get("token") or "").strip()
    if not token or chat_id is None or message_id is None:
        return False
    try:
        _tg_api(token, "deleteMessage", {
            "chat_id": chat_id,
            "message_id": message_id,
        }, timeout=10)
        return True
    except Exception:
        return False


def tg_send_all(text):
    telegram, _ = load_setup()
    ok_any = False
    for bot in telegram:
        if tg_send_bot(bot, text):
            ok_any = True
    return ok_any


def format_status(payload, title=None):
    payload = payload or {}
    regime = str(payload.get("regime") or "—").upper()
    src = payload.get("source") or "—"
    bias = payload.get("bias") or {}
    lines = [
        f"<b>CONDOR</b>  {title or regime}",
        f"source { _tg_esc(src) }  ·  {_tg_esc(payload.get('ts') or '')}",
        "",
    ]
    for coin in COINS:
        mode = str(bias.get(coin) or "—")
        extra = (payload.get("reasons") or {}).get(coin) or ""
        bit = f"{coin} <b>{_tg_esc(mode)}</b>"
        if extra:
            bit += f"  <i>{_tg_esc(extra)}</i>"
        lines.append(bit)
    why = payload.get("why")
    if why:
        lines.append("")
        lines.append(_tg_esc(why))
    wired = payload.get("wired") or []
    applied = payload.get("applied") or []
    lines.append("")
    if wired:
        short = [w.replace("hummingbot-", "") for w in wired]
        lines.append("wired: " + _tg_esc(", ".join(short)))
    else:
        lines.append("wired: none — display only")
    if applied:
        lines.append("wrote: " + _tg_esc("; ".join(applied)))
    return "\n".join(lines)


def _maybe_notify(payload, previous):
    """Push gauge digest to Telegram. Not required for hunters (file/SQL only)."""
    if not TG_GAUGE:
        return
    telegram, _ = load_setup()
    if not any((bot.get("chats") or []) for bot in telegram):
        return
    cur = (payload.get("regime"), json.dumps(payload.get("bias") or {}, sort_keys=True))
    prev = None
    if previous:
        prev = (previous.get("regime"), json.dumps(previous.get("bias") or {}, sort_keys=True))
    changed = prev is None or cur != prev or bool(payload.get("applied"))
    if not changed:
        return
    title = str(payload.get("regime") or "").upper()
    if prev and prev[0] != cur[0]:
        title = f"{str(prev[0]).upper()} → {title}"
    text = format_status(payload, title=title)
    for bot in telegram:
        if is_positions_bot(bot.get("id")):
            continue
        if bot.get("chats"):
            tg_send_bot(bot, text)


HELP = (
    "<b>You message this bot.</b> Condor has no Telegram account.\n"
    "Chat ids = <i>your</i> phone id (allow-list), not Condor.\n\n"
    "/long /flat /short — Desk BIAS (if Telegram OUT → Desk BIAS TG).\n"
    "Does not close. Hunters keep holding until their own SL/TP; after they go flat they follow it.\n"
    "Desk BIAS ignores CLOSE and OPEN100%.\n"
    "/long LTC — Translator → hunter BIAS LONG (if wired to a Translator instead)\n"
    "/flat /short — same, FLAT or SHORT\n"
    "/close LTC — close on the wired hunter only (Translator / E-CLOSE)\n"
    "/open100 LTC — open 100% leftover (needs LONG/SHORT first)\n"
    "Wire Telegram OUT → Desk BIAS TG  or  Translator TG.\n"
    "Emergency: extra Telegram → Close translator → hunter E-CLOSE. Idle does nothing.\n"
    "E-CLOSE also writes BIAS FLAT on that hunter (Copy has no BIAS).\n"
    "/status /tick /wired /help\n"
    "/grok — Grok status  ·  /ask … — ask Grok\n\n"
    "You can also type: <code>long</code>  <code>flat</code>  <code>close all</code>"
)


FORCE_TXT = {
    "hummingbot-bn-swing": "force_close_bn_swing.txt",
    "hummingbot-helliot": "force_close_helliot.txt",
    "hummingbot-surfn": "force_close_surfn.txt",
    "hummingbot-shortn": "force_close_shortn.txt",
    "hummingbot-dman": "force_close_dman.txt",
    "hummingbot-onlyup": "force_close_onlyup.txt",
    "hummingbot-dman-maker": "force_close_dman_maker.txt",
}
COPY_CLOSE = "force_close_hl_copy.json"
# Positions-bot emergency close. Allowlist is DMan Maker only.
# Never map onlyup / dman / copy here.
POS_CLOSE_ALLOW = {
    "dman_maker": "hummingbot-dman-maker",
    "dman-maker": "hummingbot-dman-maker",
    "dmanmaker": "hummingbot-dman-maker",
    "hummingbot-dman-maker": "hummingbot-dman-maker",
    "hummingbot_dman_maker": "hummingbot-dman-maker",
    "daman_maker": "hummingbot-dman-maker",
    "daman-maker": "hummingbot-dman-maker",
}
POS_CLOSE_REFUSE = {
    "all", "*", "everything", "copy", "hl", "hl_copy", "hummingbot-hl",
    "onlyup", "only_up", "only-up", "hummingbot-onlyup", "hummingbot_onlyup",
}
# Positions-bot BIAS / OPEN: every matrix hunter except Copy.
POS_HUNTER_ALIASES = {
    "hummingbot-bn-swing": ("pmm", "pmm_simple", "pmm-simple", "bn_swing", "bn-swing", "swing"),
    "hummingbot-helliot": ("helliot",),
    "hummingbot-surfn": ("surfn", "supertrend", "surf"),
    "hummingbot-shortn": ("shortn", "shorty"),
    "hummingbot-dman": ("dman",),
    "hummingbot-onlyup": ("onlyup", "only_up", "only-up"),
    "hummingbot-dman-maker": ("dman_maker", "dman-maker", "dmanmaker", "daman_maker", "daman-maker"),
}
POS_BIAS_REFUSE = {
    "all", "*", "everything", "copy", "hl", "hl_copy", "hummingbot-hl",
}


def _hunter_token_map():
    out = {}
    for hid, aliases in POS_HUNTER_ALIASES.items():
        out[_normalize_hunter_token(hid)] = hid
        out[_normalize_hunter_token(hid.replace("hummingbot-", ""))] = hid
        for a in aliases:
            out[_normalize_hunter_token(a)] = hid
    return out


POS_BIAS_ALLOW = None  # filled after _normalize_hunter_token exists
FORCE_OPEN = {
    "hummingbot-bn-swing": "force_open_bn_swing.json",
    "hummingbot-helliot": "force_open_helliot.json",
    "hummingbot-surfn": "force_open_surfn.json",
    "hummingbot-shortn": "force_open_shortn.json",
    "hummingbot-dman": "force_open_dman.json",
    "hummingbot-onlyup": "force_open_onlyup.json",
}


def _parse_coins(rest):
    raw = str(rest or "").strip()
    if not raw or raw.lower() in ("all", "*", "everything"):
        return list(COINS)
    out = []
    for p in re.split(r"[\s,]+", raw.upper()):
        if p in COINS:
            out.append(p)
    return out or list(COINS)


def _parse_desk_cmd(raw):
    text = (raw or "").strip()
    if not text:
        return None
    parts = text.split()
    w = parts[0].lstrip("/").split("@", 1)[0].lower()
    rest = " ".join(parts[1:])
    if w in ("long", "short", "flat"):
        return ("bias", w.upper(), _parse_coins(rest))
    # Grok Koordinator: "LONG" / "please post short" / "SIGNAL LTC DOWN"
    low = text.lower()
    m = re.search(r"/(long|short|flat)\b", low)
    if m:
        return ("bias", m.group(1).upper(), _parse_coins(text))
    m = re.search(r"please\s+post\s+(long|short|flat|up|down|hold)\b", low)
    if m:
        w = m.group(1)
        mode = {"long": "LONG", "up": "LONG", "short": "SHORT", "down": "SHORT", "flat": "FLAT", "hold": "FLAT"}[w]
        return ("bias", mode, _parse_coins(text))
    m = re.fullmatch(r"[\s]*(long|short|flat|up|down|hold)[\s.!;]*", low)
    if m:
        w = m.group(1)
        mode = {"long": "LONG", "up": "LONG", "short": "SHORT", "down": "SHORT", "flat": "FLAT", "hold": "FLAT"}[w]
        return ("bias", mode, [])
    if w in ("close", "flatten", "exit"):
        return ("close", None, _parse_coins(rest))
    compact = w.replace("%", "").replace("-", "")
    rest_c = rest.replace("%", "").replace(" ", "")
    if compact in ("open100",) or (compact == "open" and "100" in rest_c):
        cleaned = re.sub(r"100%?", " ", rest, flags=re.I)
        return ("open100", None, _parse_coins(cleaned))
    return None


def upsert_telegram_chat(bot_id, chat_id, add_wired=None):
    data = _read_json(SETUP_FILE)
    bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
    wired = data.get("wired") if isinstance(data.get("wired"), dict) else {}
    nid = str(bot_id or "").strip()
    if not nid:
        return []
    row = bots.get(nid) if isinstance(bots.get(nid), dict) else {"id": nid, "kind": "telegram"}
    chats = _parse_chats(row.get("chat_ids"))
    cid = str(chat_id)
    if cid not in chats:
        chats.append(cid)
    row["id"] = nid
    row["kind"] = "telegram"
    row["chat_ids"] = chats
    bots[nid] = row
    tg_ids = [str(x) for x in (wired.get("telegram") or [])]
    if add_wired is None:
        add_wired = not is_positions_bot(nid)
    if add_wired and nid not in tg_ids:
        tg_ids.append(nid)
    _write_json(SETUP_FILE, {
        "updated": _utc(),
        "bots": bots,
        "wired": {"telegram": tg_ids, "grok": [str(x) for x in (wired.get("grok") or [])]},
    })
    return chats


def _graph_nodes_edges():
    g = _read_json(GRAPH_FILE)
    nodes = [n for n in (g.get("nodes") or []) if isinstance(n, dict) and n.get("id")]
    edges = [e for e in (g.get("edges") or []) if isinstance(e, dict)]
    return nodes, edges


def positions_wires():
    """Hunter LOG → Telegram IN. Returns (telegram_bot_id, hunter_id) pairs."""
    nodes, edges = _graph_nodes_edges()
    by_id = {str(n["id"]): n for n in nodes}
    hunter_ids = set(TRADE_FILES)
    for nid, n in by_id.items():
        if n.get("type") in ("bot", "homebot") and str(nid).startswith("hummingbot-"):
            hunter_ids.add(nid)
    out = []
    seen = set()
    for e in edges:
        if str(e.get("sourceHandle") or "") != "logs":
            continue
        if str(e.get("targetHandle") or "") not in ("in", "logs", ""):
            continue
        tgt = by_id.get(str(e.get("target") or ""))
        if not tgt or tgt.get("type") != "agent":
            continue
        td = tgt.get("data") if isinstance(tgt.get("data"), dict) else {}
        if str(td.get("kind") or "telegram") == "grok":
            continue
        bot_id = str(tgt.get("id"))
        src = by_id.get(str(e.get("source") or ""))
        if not src:
            continue
        sid = str(src.get("id"))
        targets = []
        if src.get("type") == "logs" or sid == "logs":
            targets = [h for h in hunter_ids if h in TRADE_FILES]
        elif sid in TRADE_FILES or sid in hunter_ids:
            targets = [sid]
        for hid in targets:
            key = (bot_id, hid)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def wired_hunters_for_bot(bot_id):
    """Hunter ids whose LOG port is wired into this Telegram bot."""
    bid = str(bot_id or "")
    out = []
    for bot, hunter in positions_wires():
        if bot == bid and hunter not in out:
            out.append(hunter)
    return out


def dman_only_positions_bot(bot):
    """True when this bot's log wires are DMan Maker and nothing else.

    Gerdy's Positionen node is wired to every hunter, so bare close stays refused
    there. A bot wired only to hummingbot-dman-maker accepts bare close.
    """
    return set(wired_hunters_for_bot((bot or {}).get("id"))) == {"hummingbot-dman-maker"}


def is_positions_bot(bot_id):
    nid = str(bot_id or "")
    if any(b == nid for b, _ in positions_wires()):
        return True
    data = _read_json(SETUP_FILE)
    bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
    row = bots.get(nid) if isinstance(bots.get(nid), dict) else {}
    return "position" in str(row.get("name") or "").lower()


def load_positions_bots():
    data = _read_json(SETUP_FILE)
    bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
    fallback = []
    for row in bots.values():
        if isinstance(row, dict):
            fallback.extend(_parse_chats(row.get("chat_ids")))
    fallback = list(dict.fromkeys(fallback))
    by_bot = {}
    pairs = list(positions_wires())
    extra = []
    for nid, row in bots.items():
        if not isinstance(row, dict):
            continue
        if "position" not in str(row.get("name") or "").lower():
            continue
        if not str(row.get("token") or "").strip():
            continue
        extra.append(nid)
    all_hunters = list(TRADE_FILES.keys())
    for bot_id, hunter_id in pairs:
        row = bots.get(bot_id) if isinstance(bots.get(bot_id), dict) else {}
        token = str(row.get("token") or "").strip()
        if not token:
            continue
        pack = by_bot.get(bot_id)
        if not pack:
            chats = _parse_chats(row.get("chat_ids")) or list(fallback)
            pack = {
                "id": bot_id,
                "name": str(row.get("name") or "Positions")[:60],
                "token": token,
                "chats": chats,
                "hunters": [],
            }
            by_bot[bot_id] = pack
        if hunter_id not in pack["hunters"]:
            pack["hunters"].append(hunter_id)
    for nid in extra:
        row = bots.get(nid) or {}
        token = str(row.get("token") or "").strip()
        if not token:
            continue
        pack = by_bot.get(nid)
        if not pack:
            chats = _parse_chats(row.get("chat_ids")) or list(fallback)
            pack = {
                "id": nid,
                "name": str(row.get("name") or "Positions")[:60],
                "token": token,
                "chats": chats,
                "hunters": [],
            }
            by_bot[nid] = pack
        for hid in all_hunters:
            if hid not in pack["hunters"]:
                pack["hunters"].append(hid)
    for pack in by_bot.values():
        if "position" in str(pack.get("name") or "").lower():
            for hid in all_hunters:
                if hid not in pack["hunters"]:
                    pack["hunters"].append(hid)
    return list(by_bot.values())


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_COPY_OPEN_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+WE COPY\s+OPENED\s+"
    r"(LONG|SHORT)\s+(\S+)\s+([\d.]+)\s+\$\s*([\d.,]+)",
    re.I,
)
_COPY_CLOSE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+WE CLOSE\s+CLOSED\s+"
    r"(LONG|SHORT)\s+(\S+)\s+([\d.]+)\s+\$\s*([\d.,]+)",
    re.I,
)
_COPY_MUTE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+DASHBOARD\s+UNFOLLOW\s+(.*)$",
    re.I,
)


def _tail_text(path, max_bytes=4 * 1024 * 1024):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = min(size, max_bytes)
            if block <= 0:
                return ""
            f.seek(max(0, size - block))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _read_copy_trades():
    raw = _tail_text(os.path.join(LOGS_DIR, "copy_trades.log"))
    out = []
    for line in raw.splitlines():
        s = _ANSI_RE.sub("", line)
        m = _COPY_OPEN_RE.search(s)
        if m:
            ts, side, coin, amt, px = m.groups()
            try:
                amt_f, px_f = float(amt), float(px.replace(",", ""))
            except ValueError:
                continue
            out.append({
                "id": f"{ts}-OPEN-{coin}-{amt}",
                "action": "OPEN",
                "coin": coin,
                "side": side,
                "amount": amt_f,
                "price": px_f,
                "ts": ts,
            })
            continue
        m = _COPY_MUTE_RE.search(s)
        if m:
            ts, rest = m.groups()
            out.append({
                "id": f"{ts}-MUTE-{rest.strip()[:40]}",
                "action": "MUTE",
                "coin": "",
                "side": "",
                "reason": rest.strip()[:120],
                "ts": ts,
            })
            continue
        m = _COPY_CLOSE_RE.search(s)
        if not m:
            continue
        ts, side, coin, amt, px = m.groups()
        try:
            amt_f, px_f = float(amt), float(px.replace(",", ""))
        except ValueError:
            continue
        entry = None
        em = re.search(r"entry=\$?([\d.,]+)", s, re.I)
        if em:
            try:
                entry = float(em.group(1).replace(",", ""))
            except ValueError:
                entry = None
        pnl = None
        if entry and amt_f:
            if side.upper() == "SHORT":
                pnl = amt_f * (entry - px_f)
            else:
                pnl = amt_f * (px_f - entry)
        out.append({
            "id": f"{ts}-CLOSE-{coin}-{amt}",
            "action": "CLOSE",
            "coin": coin,
            "side": side,
            "amount": amt_f,
            "price": px_f,
            "entry_price": entry,
            "pnl_usd": round(pnl, 4) if pnl is not None else None,
            "ts": ts,
        })
    return out


def _read_trades(hunter_id):
    if hunter_id == "hummingbot-hl":
        return _read_copy_trades()
    name = TRADE_FILES.get(hunter_id)
    if not name or name.endswith(".log"):
        return []
    data = _read_json(os.path.join(CONF_DIR, name))
    trades = data.get("trades") if isinstance(data.get("trades"), list) else []
    return [t for t in trades if isinstance(t, dict)]


def _format_position_alert(hunter_id, trade):
    hunter = HUNTER_NAMES.get(hunter_id) or hunter_id.replace("hummingbot-", "")
    action = str(trade.get("action") or "").upper()
    coin = str(trade.get("coin") or "").upper()
    side = str(trade.get("side") or "").upper()
    if action == "MUTE":
        return (
            f"<b>{_tg_esc(hunter)}</b>  MUTE\n"
            + _tg_esc(str(trade.get("reason") or "leader muted"))
        )
    tag = "OPEN" if action in ("OPEN", "ADD") else "CLOSE"
    amt = trade.get("amount")
    px = trade.get("price")
    try:
        amt_s = f"{float(amt):g}" if amt is not None else "—"
    except (TypeError, ValueError):
        amt_s = str(amt)
    try:
        px_s = f"{float(px):.4f}" if px is not None else "—"
    except (TypeError, ValueError):
        px_s = str(px)
    lines = [
        f"<b>{_tg_esc(hunter)}</b>  {tag}  {_tg_esc(coin)} {_tg_esc(side)}",
        f"{_tg_esc(amt_s)} @ {_tg_esc(px_s)}",
    ]
    if tag == "CLOSE":
        pnl = trade.get("pnl_usd")
        roe = trade.get("roe_pct")
        bits = []
        try:
            if pnl is not None:
                p = float(pnl)
                label = "WIN" if p >= 0 else "LOSS"
                bits.append(f"{label}  {('+' if p >= 0 else '')}${abs(p):.2f}")
        except (TypeError, ValueError):
            pass
        try:
            if roe is not None:
                r = float(roe)
                bits.append(("+" if r >= 0 else "") + f"{r:.2f}% ROE")
        except (TypeError, ValueError):
            pass
        if bits:
            lines.append(_tg_esc("  ·  ".join(bits)))
        elif pnl is None:
            lines.append("win/loss n/a")
    return "\n".join(lines)


def _seed_positions_cursor(cursor, hunter_id, trades):
    ids = []
    for t in trades:
        tid = str(t.get("id") or "")
        if tid:
            ids.append(tid)
    if not ids:
        return cursor
    cursor[hunter_id] = ids[-1]
    return cursor


def positions_loop():
    """Post OPEN/CLOSE from hunter trade logs to Telegram bots with hunter LOG → IN."""
    while True:
        try:
            bots = load_positions_bots()
            cursor = _read_json(POSITIONS_CURSOR)
            if not isinstance(cursor, dict):
                cursor = {}
            dirty = False
            for bot in bots:
                for hunter_id in bot.get("hunters") or []:
                    trades = _read_trades(hunter_id)
                    last = str(cursor.get(hunter_id) or "")
                    if not last:
                        _seed_positions_cursor(cursor, hunter_id, trades)
                        dirty = True
                        continue
                    seen = False
                    fresh = []
                    for t in trades:
                        tid = str(t.get("id") or "")
                        if not seen:
                            if tid == last:
                                seen = True
                            continue
                        act = str(t.get("action") or "").upper()
                        if act in ("OPEN", "CLOSE", "ADD", "MUTE"):
                            fresh.append(t)
                    if not seen and trades:
                        # log rotated or id missing — send only the newest
                        t = trades[-1]
                        act = str(t.get("action") or "").upper()
                        if act in ("OPEN", "CLOSE", "ADD", "MUTE"):
                            fresh = [t]
                    for t in fresh:
                        text = _format_position_alert(hunter_id, t)
                        if bot.get("chats") and tg_send_bot(bot, text):
                            _log(f"positions {bot.get('name')} {hunter_id} {t.get('action')} {t.get('coin')}")
                        elif not bot.get("chats"):
                            _log(f"positions skip {bot.get('id')} — no chat id (message the bot once)")
                    if trades:
                        nid = str(trades[-1].get("id") or last)
                        if cursor.get(hunter_id) != nid:
                            cursor[hunter_id] = nid
                            dirty = True
            if dirty:
                cursor["updated"] = _utc()
                _write_json(POSITIONS_CURSOR, cursor)
        except Exception:
            _log("positions loop failed\n" + traceback.format_exc())
        time.sleep(POSITIONS_POLL_S)


def telegram_set_bias(mode, coins, targets=None):
    mode = str(mode or "FLAT").upper()
    if mode not in VALID:
        mode = "FLAT"
    coins = [c for c in (coins or list(COINS)) if c in COINS] or list(COINS)
    if targets is None:
        targets = wired_hunters(_read_json(GRAPH_FILE))
    notes = []
    for bot_id, wired_coin in targets:
        spec = HUNTERS.get(bot_id) or {}
        allowed = list(spec.get("coins") or COINS)
        use = [wired_coin] if wired_coin in allowed else [c for c in coins if c in allowed]
        if not use:
            continue
        mapping = {c: clip_mode(mode, spec.get("mode")) for c in use}
        note = write_hunter_bias(bot_id, mapping)
        bits = " ".join(f"{c}={mapping[c]}" for c in use)
        short = bot_id.replace("hummingbot-", "")
        notes.append(f"{short} {bits}" + (f" ({note})" if note else ""))
    return notes


def _normalize_hunter_token(raw):
    s = str(raw or "").strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


POS_BIAS_ALLOW = _hunter_token_map()


def parse_positions_close_cmd(raw):
    """Positions bot: 'close dman_maker' only. Never 'close' alone or onlyup."""
    text = (raw or "").strip()
    if not text:
        return None
    parts = text.split()
    w = parts[0].lstrip("/").split("@", 1)[0].lower()
    if w not in ("close", "flatten", "exit"):
        return None
    token = _normalize_hunter_token(" ".join(parts[1:]))
    if not token:
        return ("need_name", "")
    if token in POS_CLOSE_REFUSE:
        return ("refuse", token)
    hid = POS_CLOSE_ALLOW.get(token)
    if hid:
        return ("ok", hid)
    return ("unknown", token)


def parse_positions_bias_cmd(raw):
    """Positions bot BIAS. Never closes. Coin is required — never all coins.

    bias dman-maker long ltc
    bias onlyup short btc
    long ltc dman_maker
    """
    text = (raw or "").strip()
    if not text:
        return None
    parts = text.split()
    if not parts:
        return None
    w = parts[0].lstrip("/").split("@", 1)[0].lower()
    mode_map = {"long": "LONG", "short": "SHORT", "flat": "FLAT"}

    def _split_rest(tokens):
        coins = []
        hunter_bits = []
        mode = None
        for p in tokens:
            u = str(p).upper().rstrip(",;")
            low = str(p).lower().rstrip(",;")
            if low in mode_map and mode is None:
                mode = mode_map[low]
            elif u in COINS:
                coins.append(u)
            else:
                hunter_bits.append(p)
        return mode, hunter_bits, coins

    def _resolve_hunter(hunter_bits, mode, coins):
        token = _normalize_hunter_token(" ".join(hunter_bits))
        if not token:
            return ("need_hunter", mode, None, coins)
        if token in POS_BIAS_REFUSE:
            return ("refuse", token, mode, coins)
        hid = POS_BIAS_ALLOW.get(token)
        if not hid:
            return ("unknown", token, mode, coins)
        allowed = list((HUNTERS.get(hid) or {}).get("coins") or [])
        use = [c for c in (coins or []) if c in allowed]
        if not coins:
            return ("need_coin", hid, mode, [])
        if not use:
            return ("bad_coin", hid, mode, coins)
        return ("ok", hid, mode, use)

    if w == "bias":
        if len(parts) < 2:
            return ("need_args", None, None, [])
        mode, hunter_bits, coins = _split_rest(parts[1:])
        if not mode:
            return ("need_mode", None, None, coins)
        return _resolve_hunter(hunter_bits, mode, coins)

    if w not in mode_map:
        return None
    mode = mode_map[w]
    rest = parts[1:]
    if not rest:
        return ("need_args", mode, None, [])
    coins = []
    hunter_bits = []
    for p in rest:
        u = str(p).upper().rstrip(",;")
        if u in COINS:
            coins.append(u)
        else:
            hunter_bits.append(p)
    return _resolve_hunter(hunter_bits, mode, coins)


def positions_set_hunter_bias(bot_id, mode, coins, source="telegram-positions"):
    """Set one hunter, named coins only. Does not close, does not open."""
    if bot_id not in HUNTERS:
        return ["refused — hunter not allowed"]
    spec = HUNTERS.get(bot_id) or {}
    allowed = list(spec.get("coins") or [])
    use = [c for c in (coins or []) if c in allowed]
    if not use:
        return ["coin required — not applied to all coins"]
    mapping = {c: clip_mode(mode, spec.get("mode")) for c in use}
    note = write_hunter_bias(bot_id, mapping)
    bits = " ".join(f"{c}={mapping[c]}" for c in use)
    label = HUNTER_NAMES.get(bot_id) or bot_id.replace("hummingbot-", "")
    notes = [
        label + " BIAS " + bits + ((" (" + note + ")") if note else " (already)"),
        "no close queued",
    ]
    _log("positions BIAS " + bot_id + " · " + "; ".join(notes) + " src=" + str(source))
    feed_append("xlate", "positions BIAS " + bot_id + " " + bits, "telegram-positions")
    return notes


def _live_bias_coin(bot_id, coin):
    spec = HUNTERS.get(bot_id) or {}
    coin = str(coin or "").upper()
    v = ""
    try:
        import matrix_store
        matrix_store.ensure()
        v = str((matrix_store.bias_map(bot_id) or {}).get(coin) or "").upper()
    except Exception:
        v = ""
    if v not in VALID:
        raw = _read_json(os.path.join(CONF_DIR, spec.get("bias") or ""))
        v = str(raw.get(coin) or "").upper()
    if v not in VALID:
        return "FLAT"
    return clip_mode(v, spec.get("mode"))


def parse_positions_open_cmd(raw):
    """open dman-maker ltc  |  dman-maker open ltc"""
    text = (raw or "").strip()
    if not text:
        return None
    parts = text.split()
    if not parts:
        return None
    low = [p.lstrip("/").split("@", 1)[0].lower() for p in parts]
    if "open" not in low and "open100" not in low:
        return None
    coins = []
    hunter_bits = []
    for p in parts:
        u = str(p).upper().rstrip(",;")
        w = p.lstrip("/").split("@", 1)[0].lower().rstrip(",;")
        if w in ("open", "open100"):
            continue
        if u in COINS:
            coins.append(u)
        else:
            hunter_bits.append(p)
    token = _normalize_hunter_token(" ".join(hunter_bits))
    if not token:
        return ("need_hunter", None, coins)
    if token in POS_BIAS_REFUSE:
        return ("refuse", token, coins)
    hid = POS_BIAS_ALLOW.get(token)
    if not hid:
        return ("unknown", token, coins)
    allowed = list((HUNTERS.get(hid) or {}).get("coins") or [])
    use = [c for c in coins if c in allowed]
    if not coins:
        return ("need_coin", hid, [])
    if not use:
        return ("bad_coin", hid, coins)
    return ("ok", hid, use)


def positions_open_hunter(bot_id, coins, source="telegram-positions"):
    """Queue 100% leftover on current BIAS. Does not flip an open clip. FLAT refused."""
    if bot_id not in HUNTERS:
        return ["refused — hunter not allowed"]
    spec = HUNTERS.get(bot_id) or {}
    allowed = list(spec.get("coins") or [])
    use = [c for c in (coins or []) if c in allowed]
    if not use:
        return ["coin required"]
    label = HUNTER_NAMES.get(bot_id) or bot_id.replace("hummingbot-", "")
    notes = []
    try:
        import matrix_store
        matrix_store.ensure()
    except Exception as e:
        return ["sqlite unavailable: " + str(e)[:80]]
    for coin in use:
        side = _live_bias_coin(bot_id, coin)
        if side not in ("LONG", "SHORT"):
            notes.append(f"{coin} BIAS is {side or 'FLAT'} — set LONG or SHORT first")
            continue
        payload = {
            "coin": coin, "side": side, "pct": 1.0,
            "source": str(source)[:40],
        }
        cid = matrix_store.command_enqueue(bot_id, "open", payload, source=source)
        if cid:
            notes.append(f"{label} OPEN {side} {coin} 100% leftover queued id={cid}")
        else:
            notes.append(f"{label} OPEN {coin} queue failed")
    _log("positions OPEN " + bot_id + " · " + "; ".join(notes))
    feed_append("xlate", "positions OPEN " + bot_id + " · " + "; ".join(notes), "telegram-positions")
    return notes or ["nothing queued"]


def positions_set_dman_maker_bias(mode, coins, source="telegram-positions"):
    """Back-compat wrapper. Bias only — never close."""
    return positions_set_hunter_bias("hummingbot-dman-maker", mode, coins, source=source)


def positions_close_dman_maker(source="telegram-positions"):
    """Flatten DMan Maker only + BIAS FLAT. Does not touch Only Up or desk BIAS."""
    bot_id = "hummingbot-dman-maker"
    if bot_id != "hummingbot-dman-maker":
        return ["refused — hard lock"]
    spec = HUNTERS.get(bot_id) or {}
    coins = list(spec.get("coins") or [])
    notes = []
    payload = {"all": True, "source": str(source)[:40], "hunter": bot_id}
    try:
        import matrix_store
        matrix_store.ensure()
        cid = matrix_store.command_enqueue(bot_id, "close", payload, source=source)
        if cid:
            notes.append(f"DMan Maker CLOSE queued id={cid} (all coins)")
        else:
            notes.append("DMan Maker CLOSE queue failed")
    except Exception as e:
        notes.append("DMan Maker CLOSE queue error: " + str(e)[:120])
        _log("positions close dman-maker enqueue failed: " + str(e))
    mapping = {c: "FLAT" for c in coins}
    bias_note = write_hunter_bias(bot_id, mapping)
    notes.append("DMan Maker BIAS FLAT" + ((" " + bias_note) if bias_note else " (already)"))
    notes.append("Only Up / other hunters / desk BIAS untouched")
    _log("positions CLOSE dman-maker only · " + "; ".join(notes))
    feed_append("xlate", "positions CLOSE dman-maker · " + "; ".join(notes), "telegram-positions")
    return notes


def force_close_hunters(targets, coins, source="translator"):
    coins = [c for c in (coins or list(COINS)) if c in COINS] or list(COINS)
    src = str(source or "translator")[:24]
    all_coins = set(coins) >= set(COINS)
    if all_coins:
        payload = {"all": True, "source": src}
        body = "ALL"
    elif len(coins) == 1:
        payload = {"coin": coins[0], "source": src}
        body = coins[0]
    else:
        payload = {"coins": coins, "source": src}
        body = ",".join(coins)
    notes = []
    bot_ids = {b for b, _ in (targets or [])}
    for bot_id in bot_ids:
        if bot_id not in HUNTERS and bot_id != "hummingbot-hl":
            continue
        try:
            import matrix_store
            matrix_store.ensure()
            cid = matrix_store.command_enqueue(bot_id, "close", payload, source="translator")
            if cid:
                notes.append(bot_id.replace("hummingbot-", "") + " CLOSE " + body)
            else:
                notes.append(bot_id.replace("hummingbot-", "") + " CLOSE queue failed")
        except Exception as e:
            _log(f"force close sql {bot_id}: {e}")
    return notes


def force_open100_hunters(targets, coins):
    coins = [c for c in (coins or list(COINS)) if c in COINS] or list(COINS)
    notes = []
    for bot_id, wired_coin in targets or []:
        fname = FORCE_OPEN.get(bot_id)
        if not fname:
            notes.append(bot_id.replace("hummingbot-", "") + " no OPEN100")
            continue
        spec = HUNTERS.get(bot_id) or {}
        allowed = list(spec.get("coins") or COINS)
        use = [wired_coin] if wired_coin in allowed else [c for c in coins if c in allowed]
        if not use:
            use = allowed[:1]
        if not use:
            continue
        coin = use[0]
        cur = ""
        try:
            import matrix_store
            matrix_store.ensure()
            cur = str((matrix_store.bias_map(bot_id) or {}).get(coin) or "").upper()
        except Exception:
            cur = ""
        if not cur:
            raw = _read_json(os.path.join(CONF_DIR, spec.get("bias") or ""))
            cur = str(raw.get(coin) or "").upper()
        side = cur if cur in ("LONG", "SHORT") else ("SHORT" if spec.get("mode") == "short" else "LONG")
        payload = {"coin": coin, "pct": 100, "side": side, "source": "translator"}
        try:
            import matrix_store
            matrix_store.ensure()
            cid = matrix_store.command_enqueue(bot_id, "open", payload, source="translator")
            if cid:
                notes.append(f"{bot_id.replace('hummingbot-', '')} OPEN100 {side} {coin}")
            else:
                notes.append(bot_id.replace("hummingbot-", "") + " OPEN100 queue failed")
        except Exception as e:
            _log(f"force open sql {bot_id}: {e}")
    return notes


def apply_xlate(node_id, kind, mode, coins, raw="", xlate_kind=None):
    graph = _read_json(GRAPH_FILE)
    node = _xlate_node(graph, node_id)
    closer = _xlate_kind(node) == "closer" or str(xlate_kind or "") == "closer"
    if kind == "bias":
        cmd = str(mode or "FLAT").upper()
    elif kind == "close":
        cmd = "CLOSE"
    else:
        cmd = "OPEN100%"
    if closer:
        if kind != "close":
            note = "close-only · ignored " + cmd + " · idle does not flatten"
            xlate_save(node_id, cmd, coins, [note], raw)
            feed_append("xlate", note, str(node_id))
            return {
                "ok": True,
                "cmd": cmd,
                "notes": [note],
                "message": note,
            }
        targets = hunters_from_eclose_sources(graph, {str(node_id)})
        if not targets:
            xlate_save(node_id, "CLOSE", coins, ["not wired to hunter E-CLOSE"], raw)
            feed_append("xlate", "CLOSE waiting — wire E-CLOSE into a hunter", str(node_id))
            return {
                "ok": False,
                "cmd": "CLOSE",
                "error": "close translator not wired to a hunter E-CLOSE",
                "notes": [],
                "message": "CLOSE is on the Close translator — wire E-CLOSE into a hunter E-CLOSE to flatten",
            }
        notes = force_close_hunters(targets, list(COINS), source="eclose")
        bias_targets = [(bid, coin) for bid, coin in targets if bid in HUNTERS]
        if bias_targets:
            notes.extend(telegram_set_bias("FLAT", list(COINS), bias_targets))
        xlate_save(node_id, "CLOSE", coins, notes, raw)
        feed_append("xlate", "E-CLOSE " + "; ".join(notes), str(node_id))
        msg = "E-CLOSE " + ("; ".join(notes) if notes else "no hunter wrote")
        return {"ok": True, "cmd": "CLOSE", "notes": notes, "message": msg}
    targets = hunters_from_sources(graph, {str(node_id)})
    if not targets:
        xlate_save(node_id, cmd, coins, ["not wired to hunter"], raw)
        feed_append("xlate", f"{cmd} waiting — wire BIAS into a hunter", str(node_id))
        return {
            "ok": False,
            "cmd": cmd,
            "error": "translator not wired to a hunter BIAS",
            "notes": [],
            "message": f"{cmd} is on the Translator — drag BIAS onto a hunter BIAS to apply",
        }
    if kind == "bias":
        notes = telegram_set_bias(cmd, coins, targets)
    elif kind == "close":
        notes = force_close_hunters(targets, coins)
    else:
        notes = force_open100_hunters(targets, coins)
    xlate_save(node_id, cmd, coins, notes, raw)
    feed_append("xlate", f"{cmd} {' '.join(coins or [])} → {'; '.join(notes)}", str(node_id))
    msg = f"{cmd} " + ("; ".join(notes) if notes else "no hunter wrote")
    return {"ok": True, "cmd": cmd, "notes": notes, "message": msg}


def run_xlate(body):
    body = body if isinstance(body, dict) else {}
    node_id = str(body.get("id") or body.get("node_id") or "").strip()
    cmd = str(body.get("cmd") or "").strip()
    coins = body.get("coins")
    if isinstance(coins, str):
        coins = _parse_coins(coins)
    elif isinstance(coins, list):
        coins = [str(c).upper() for c in coins if str(c).upper() in COINS] or list(COINS)
    else:
        coins = list(COINS)
    if not node_id:
        return {"ok": False, "error": "translator id required"}
    desk = _parse_desk_cmd(cmd)
    up = cmd.upper().replace(" ", "")
    if up in ("OPEN100%", "OPEN100"):
        desk = ("open100", None, coins)
    elif up == "CLOSE":
        desk = ("close", None, coins)
    elif up in VALID:
        desk = ("bias", up, coins)
    if not desk:
        return {"ok": False, "error": "cmd must be LONG SHORT FLAT CLOSE OPEN100%"}
    kind, mode, c2 = desk
    return apply_xlate(
        node_id, kind, mode, c2 or coins, raw=cmd,
        xlate_kind=body.get("xlate_kind"),
    )


def telegram_desk(bot, kind, mode, coins, raw):
    graph = _read_json(GRAPH_FILE)
    desks = desk_bias_nodes_for_telegram(graph, (bot or {}).get("id"))
    xlates = translators_for_telegram(graph, (bot or {}).get("id"))
    lines = []
    if desks:
        if kind == "bias":
            msg = write_desk_bias(mode, source="telegram")
            for did in desks:
                xlate_save(did, str(mode or "").upper(), [], [msg], raw)
            feed_append("xlate", "desk " + str(mode or "").upper(), "desk-bias")
            lines.append(msg)
            # Keep the Translator lamp on the same command so Wire does not
            # still show an old FLAT after Desk BIAS took /short or /long.
            for xid in xlates:
                xlate_save(
                    xid,
                    str(mode or "").upper(),
                    coins,
                    ["desk BIAS " + str(mode or "").upper() + " — not applied on this Translator (Surfn is long-only; SHORT would clip to FLAT)"],
                    raw,
                )
        else:
            note = "Desk BIAS ignores CLOSE/OPEN — does not flatten"
            for did in desks:
                xlate_save(did, str(kind or "").upper(), [], [note], raw)
            lines.append(note)
    if xlates and not (desks and kind == "bias"):
        for xid in xlates:
            out = apply_xlate(xid, kind, mode, coins, raw=raw)
            lines.append(out.get("message") or out.get("error") or "")
    if not desks and not xlates:
        return (
            "wire this Telegram bot OUT into Desk BIAS (LONG/FLAT/SHORT, no close) "
            "or a Translator"
        )
    return "\n".join([x for x in lines if x]) or "no desk BIAS or translator applied"


def grok_status_text():
    _, grok = load_setup()
    if not grok:
        return (
            "Grok <b>off</b> — Condor is on heuristic.\n"
            "From this chat send:\n"
            "<code>/grok key xai-your-key</code>\n"
            "or paste the key as its own message.\n"
            "Key from console.x.ai — then /tick uses Grok."
        )
    src = grok.get("id") or ""
    via = "Telegram" if src == PHONE_GROK_ID else ("env" if src == "env" else "Wire")
    return (
        f"Grok <b>on</b> via { _tg_esc(via) }\n"
        f"model {_tg_esc(grok.get('model') or 'grok-4.5')}\n"
        f"key {_tg_esc(_mask_key(grok.get('api_key')))}\n"
        "/ask why is ETH short — or just type a question."
    )


def _connect_grok_key(bot, chat_id, key, message_id=None):
    key = _looks_like_xai_key(key) or str(key or "").strip()
    if not (key.lower().startswith("xai-") and len(key) >= 16):
        tg_send_bot(
            bot,
            "need an xAI key starting with <code>xai-</code>.\n"
            "create one at console.x.ai then:\n"
            "<code>/grok key xai-…</code>",
            chat_id=chat_id,
        )
        return
    row = upsert_phone_grok(api_key=key)
    tg_delete(bot, chat_id, message_id)
    _log(f"grok key saved via telegram chat={chat_id}")
    model = (row or {}).get("model") or "grok-4.5"
    try:
        grok_chat("Say OK in one word.", key, model)
        ping = "key works"
    except Exception as e:
        ping = "saved, ping failed: " + _tg_esc(str(e)[:160])
    tg_send_bot(
        bot,
        "Grok connected from Telegram.\n"
        f"key {_tg_esc(_mask_key(key))}  ·  model {_tg_esc(model)}\n"
        f"{ping}\n"
        "I deleted your key message if Telegram allowed it.\n"
        "/tick to read the tape with Grok. /ask … to chat.",
        chat_id=chat_id,
    )


def _ask_grok(bot, chat_id, question):
    q = str(question or "").strip()
    if not q:
        tg_send_bot(bot, "ask what?  e.g. /ask why is ETH short", chat_id=chat_id)
        return
    _, grok = load_setup()
    if not grok or not grok.get("api_key"):
        tg_send_bot(bot, grok_status_text(), chat_id=chat_id)
        return
    tg_send_bot(bot, "asking Grok…", chat_id=chat_id)
    try:
        answer = grok_chat(q, grok.get("api_key"), grok.get("model"))
        feed_append("grok", "Q: " + q, "grok")
        feed_append("grok", answer, grok.get("name") or "grok")
        tg_send_bot(bot, _tg_esc(answer), chat_id=chat_id)
    except Exception as e:
        tg_send_bot(bot, "grok failed: " + _tg_esc(e), chat_id=chat_id)


def _handle_grok_cmd(bot, chat_id, raw, message_id=None):
    rest = raw.split(None, 1)[1].strip() if " " in raw.strip() else ""
    low = rest.lower()
    if not rest:
        tg_send_bot(bot, grok_status_text(), chat_id=chat_id)
        return
    if low in ("off", "clear", "disconnect"):
        upsert_phone_grok(clear=True)
        _log("grok phone key cleared via telegram")
        tg_send_bot(bot, "Telegram Grok disconnected. /tick will use Wire/env Grok or heuristic.", chat_id=chat_id)
        return
    if low.startswith("model"):
        model = rest.split(None, 1)[1].strip() if " " in rest else ""
        if model not in GROK_MODELS:
            tg_send_bot(
                bot,
                "models: " + ", ".join(GROK_MODELS),
                chat_id=chat_id,
            )
            return
        upsert_phone_grok(model=model)
        tg_send_bot(bot, "Grok model " + _tg_esc(model), chat_id=chat_id)
        return
    if low.startswith("key"):
        rest = rest.split(None, 1)[1].strip() if " " in rest else ""
    if rest.lower().startswith("grok "):
        rest = rest[5:].strip()
    key = _looks_like_xai_key(rest) or rest
    _connect_grok_key(bot, chat_id, key, message_id)


def _allowed_chat(bot, chat_id):
    chats = [str(c) for c in ((bot or {}).get("chats") or [])]
    if not chats:
        return False
    return str(chat_id) in chats


def _is_bias_bot(bot):
    nid = str((bot or {}).get("id") or "")
    if nid == "telegram-bot-t84jlc":
        return True
    graph = _read_json(GRAPH_FILE)
    return bool(desk_bias_nodes_for_telegram(graph, nid))


def positions_info_text():
    """All Positions-bot commands for every matrix hunter."""
    lines = [
        "<b>Positionen — commands</b>",
        "Copy has no BIAS. Emergency flatten is DMan Maker only.",
        "",
        "<b>Hunters</b>  (name → coins)",
    ]
    alias_by = {}
    for hid, aliases in POS_HUNTER_ALIASES.items():
        alias_by[hid] = aliases
    for hid, spec in HUNTERS.items():
        label = HUNTER_NAMES.get(hid) or hid.replace("hummingbot-", "")
        names = list(alias_by.get(hid) or ())
        short = hid.replace("hummingbot-", "").replace("_", "-")
        if short not in names:
            names = [short] + names
        coins = " ".join(spec.get("coins") or [])
        lines.append(
            "· <code>" + _tg_esc(names[0]) + "</code>  "
            + _tg_esc(label) + "  " + _tg_esc(coins)
        )
        extra = [n for n in names[1:] if n != names[0]]
        if extra:
            lines.append("  aka " + _tg_esc(" ".join(extra)))
    lines += [
        "",
        "<b>BIAS</b>  (does not close, coin required)",
        "<code>bias &lt;hunter&gt; long|short|flat &lt;coin&gt;</code>",
        "<code>bias dman-maker long ltc</code>",
        "<code>bias onlyup short btc</code>",
        "<code>bias pmm flat eth</code>",
        "<code>bias helliot long btc</code>",
        "<code>bias surfn short ltc</code>",
        "<code>bias shorty short tao</code>",
        "<code>bias dman long tao</code>",
        "",
        "<b>OPEN</b>  100% leftover if BIAS is LONG or SHORT",
        "<code>open &lt;hunter&gt; &lt;coin&gt;</code>",
        "<code>open dman-maker ltc</code>",
        "<code>dman-maker open ltc</code>",
        "<code>open onlyup ltc</code>",
        "<code>open shorty tao</code>",
        "<code>open pmm btc</code>",
        "",
        "<b>CLOSE</b>",
        "<code>close dman_maker</code>  flatten DMan Maker + BIAS FLAT",
        "Only Up / Copy / others are never closed from here.",
        "",
        "<b>Info</b>",
        "<code>info</code>  this list",
        "This bot also posts every hunter OPEN / CLOSE / MUTE.",
    ]
    return "\n".join(lines)


def _handle_tg_text(bot, chat_id, text, message_id=None, chat_type=None, from_bot=False):
    raw = (text or "").strip()
    cmd = raw.split()
    if not cmd:
        return
    name = cmd[0].split("@", 1)[0].lower()
    ctype = str(chat_type or "").lower()
    is_group = ctype in ("group", "supergroup")
    if not _looks_like_xai_key(raw):
        feed_append("tg-in", raw[:80], str(chat_id) + (" ·bot" if from_bot else "") + (" ·grp" if is_group else ""))
    if not _allowed_chat(bot, chat_id):
        existing = [str(c) for c in ((bot or {}).get("chats") or [])]
        pos = is_positions_bot(bot.get("id"))
        # BIAS BOT: auto-allow the COINDESK group so Koordinator posts there are not dropped.
        allow_group = is_group and _is_bias_bot(bot) and not pos
        if not existing or allow_group:
            upsert_telegram_chat(bot.get("id"), chat_id, add_wired=not pos)
            bot = dict(bot or {})
            chats = [str(c) for c in (bot.get("chats") or [])]
            if str(chat_id) not in chats:
                chats.append(str(chat_id))
            bot["chats"] = chats
            if pos:
                if dman_only_positions_bot(bot):
                    welcome = (
                        "this chat is now allowed.\n"
                        "<b>DMan Maker</b> posts OPEN and CLOSE.\n"
                        "<code>close</code> market-closes every DMan Maker position and sets BIAS FLAT."
                    )
                else:
                    welcome = (
                        f"this chat is now allowed.\n"
                        f"<b>{_tg_esc(bot.get('name') or 'Positions')}</b> posts every hunter OPEN / CLOSE.\n"
                        "<code>bias dman-maker long ltc</code>  ·  <code>open dman-maker ltc</code>"
                    )
                tg_send_bot(bot, welcome, chat_id=chat_id)
            elif allow_group:
                _log(f"telegram auto-allow GROUP {chat_id} bot={bot.get('id')}")
            else:
                tg_send_bot(
                    bot,
                    f"this chat is now allowed.\n"
                    f"your chat id is <code>{_tg_esc(chat_id)}</code>\n"
                    "you message <b>this bot</b>. Condor has no Telegram account.\n"
                    "/long LTC  ·  /flat all  ·  /close LTC  ·  /help",
                    chat_id=chat_id,
                )
            if not allow_group:
                _log(f"telegram auto-allow chat {chat_id} bot={bot.get('id')} positions={pos}")
        else:
            tg_send_bot(
                bot,
                f"not authorized. your chat id is <code>{_tg_esc(chat_id)}</code>\n"
                "paste it into the Wire Telegram node (chat ids), then save.",
                chat_id=chat_id,
            )
            _log(f"telegram unauthorized chat {chat_id} bot={bot.get('id')}")
            return
    pasted = _looks_like_xai_key(raw)
    if pasted:
        _connect_grok_key(bot, chat_id, pasted, message_id)
        return
    if is_positions_bot(bot.get("id")):
        if name in ("info", "/info", "/help", "/start", "help"):
            text = positions_info_text()
            if dman_only_positions_bot(bot):
                text += (
                    "\n\n<b>This wire</b>\n"
                    "Only DMan Maker is connected.\n"
                    "<code>close</code> market-closes it and sets BIAS FLAT."
                )
            tg_send_bot(bot, text, chat_id=chat_id)
            return
        bias_parsed = parse_positions_bias_cmd(raw)
        if bias_parsed:
            kind = bias_parsed[0]
            if kind == "need_args":
                tg_send_bot(
                    bot,
                    "type hunter, side and coin:\n"
                    "<code>bias dman-maker long ltc</code>\n"
                    "<code>bias dman-maker short btc</code>\n"
                    "<code>bias dman-maker flat eth</code>\n"
                    "<code>bias onlyup long ltc</code>\n"
                    "coin is required. does not close. FLAT is BIAS only.",
                    chat_id=chat_id,
                )
                return
            if kind == "need_mode":
                tg_send_bot(
                    bot,
                    "missing side. type <code>bias dman-maker long ltc</code> "
                    "or <code>bias onlyup short btc</code>",
                    chat_id=chat_id,
                )
                return
            if kind == "need_hunter":
                tg_send_bot(
                    bot,
                    "missing hunter. type <code>bias dman-maker long ltc</code> "
                    "or <code>bias onlyup short btc</code>",
                    chat_id=chat_id,
                )
                return
            if kind == "need_coin":
                tg_send_bot(
                    bot,
                    "missing coin — BIAS is not applied to all coins.\n"
                    "coin required. e.g. <code>bias dman-maker long ltc</code>\n"
                    "BTC ETH LTC TAO · Only Up: BTC LTC TAO",
                    chat_id=chat_id,
                )
                return
            if kind == "bad_coin":
                hid = bias_parsed[1]
                coins = bias_parsed[3] or []
                allowed = list((HUNTERS.get(hid) or {}).get("coins") or [])
                label = "DMan Maker" if hid == "hummingbot-dman-maker" else "Only Up"
                tg_send_bot(
                    bot,
                    "bad coin <code>" + _tg_esc(" ".join(coins) or "?") + "</code> for "
                    + _tg_esc(label) + ".\n"
                    "use " + _tg_esc(" ".join(allowed)),
                    chat_id=chat_id,
                )
                return
            if kind == "refuse":
                tg_send_bot(
                    bot,
                    "refused <code>" + _tg_esc(bias_parsed[1]) + "</code>.\n"
                    "Positions BIAS is not Copy. Use pmm helliot surfn shorty dman onlyup dman-maker.",
                    chat_id=chat_id,
                )
                _log(f"positions bias refused token={bias_parsed[1]} chat={chat_id}")
                return
            if kind == "unknown":
                tg_send_bot(
                    bot,
                    "unknown hunter <code>" + _tg_esc(bias_parsed[1]) + "</code>.\n"
                    "use <code>bias dman-maker long ltc</code> · pmm helliot surfn shorty dman onlyup",
                    chat_id=chat_id,
                )
                return
            hid, mode, coins = bias_parsed[1], bias_parsed[2], bias_parsed[3]
            if hid not in HUNTERS:
                tg_send_bot(bot, "refused — hunter not allowed.", chat_id=chat_id)
                return
            notes = positions_set_hunter_bias(hid, mode, coins, source="telegram-positions")
            title = HUNTER_NAMES.get(hid) or hid.replace("hummingbot-", "")
            tg_send_bot(
                bot,
                "<b>" + _tg_esc(title) + "</b> BIAS " + _tg_esc(str(mode or "")) + "\n"
                + _tg_esc(" · ".join(notes)),
                chat_id=chat_id,
            )
            return
        open_parsed = parse_positions_open_cmd(raw)
        if open_parsed:
            kind = open_parsed[0]
            if kind == "need_hunter":
                tg_send_bot(
                    bot,
                    "missing hunter. type <code>open dman-maker ltc</code> "
                    "or <code>dman-maker open ltc</code>",
                    chat_id=chat_id,
                )
                return
            if kind == "need_coin":
                hid = open_parsed[1]
                allowed = list((HUNTERS.get(hid) or {}).get("coins") or [])
                tg_send_bot(
                    bot,
                    "missing coin. type <code>open dman-maker ltc</code>\n"
                    "coins: " + _tg_esc(" ".join(allowed)),
                    chat_id=chat_id,
                )
                return
            if kind == "bad_coin":
                hid = open_parsed[1]
                allowed = list((HUNTERS.get(hid) or {}).get("coins") or [])
                tg_send_bot(
                    bot,
                    "bad coin. use " + _tg_esc(" ".join(allowed)),
                    chat_id=chat_id,
                )
                return
            if kind == "refuse":
                tg_send_bot(
                    bot,
                    "refused <code>" + _tg_esc(str(open_parsed[1])) + "</code>. Copy has no BIAS open.",
                    chat_id=chat_id,
                )
                return
            if kind == "unknown":
                tg_send_bot(
                    bot,
                    "unknown hunter <code>" + _tg_esc(str(open_parsed[1])) + "</code>.\n"
                    "pmm helliot surfn shorty dman onlyup dman-maker",
                    chat_id=chat_id,
                )
                return
            hid, coins = open_parsed[1], open_parsed[2]
            notes = positions_open_hunter(hid, coins, source="telegram-positions")
            title = HUNTER_NAMES.get(hid) or hid.replace("hummingbot-", "")
            tg_send_bot(
                bot,
                "<b>" + _tg_esc(title) + "</b> OPEN\n" + _tg_esc(" · ".join(notes)),
                chat_id=chat_id,
            )
            return
        parsed = parse_positions_close_cmd(raw)
        if parsed:
            kind, token = parsed
            if kind == "need_name":
                if dman_only_positions_bot(bot):
                    notes = positions_close_dman_maker(source="telegram-positions")
                    tg_send_bot(
                        bot,
                        "<b>DMan Maker</b> CLOSE + BIAS FLAT\n"
                        + _tg_esc(" · ".join(notes)),
                        chat_id=chat_id,
                    )
                    return
                tg_send_bot(
                    bot,
                    "type the hunter: <code>close dman_maker</code>\n"
                    "bare <code>close</code> does nothing. Only Up is never closed from here.",
                    chat_id=chat_id,
                )
                return
            if kind == "refuse":
                tg_send_bot(
                    bot,
                    "refused <code>" + _tg_esc(token) + "</code>.\n"
                    "Positions emergency close is <code>close dman_maker</code> only.\n"
                    "Only Up and other hunters stay open.",
                    chat_id=chat_id,
                )
                _log(f"positions close refused token={token} chat={chat_id}")
                return
            if kind == "unknown":
                tg_send_bot(
                    bot,
                    "unknown hunter <code>" + _tg_esc(token) + "</code>.\n"
                    "only <code>close dman_maker</code> is allowed here.",
                    chat_id=chat_id,
                )
                return
            if token != "hummingbot-dman-maker":
                tg_send_bot(bot, "refused — not DMan Maker.", chat_id=chat_id)
                return
            notes = positions_close_dman_maker(source="telegram-positions")
            tg_send_bot(
                bot,
                "<b>DMan Maker</b> CLOSE + BIAS FLAT\n"
                + _tg_esc(" · ".join(notes)),
                chat_id=chat_id,
            )
            return
        if name in ("/start", "/help", "info", "/info", "help"):
            tg_send_bot(bot, positions_info_text(), chat_id=chat_id)
            return
        return
    if name in ("/start", "/help"):
        tg_send_bot(bot, HELP, chat_id=chat_id)
        with _lock:
            last = dict(_last) if _last else _read_json(GAUGE_FILE)
        if last:
            tg_send_bot(bot, format_status(last), chat_id=chat_id)
        return
    if name == "/status":
        with _lock:
            last = dict(_last) if _last else _read_json(GAUGE_FILE)
        tg_send_bot(bot, format_status(last or {"ok": False}), chat_id=chat_id)
        return
    if name == "/wired":
        names = [b.replace("hummingbot-", "") for b, _ in wired_hunters(_read_json(GRAPH_FILE))]
        tg_send_bot(bot, "wired: " + (", ".join(names) if names else "none"), chat_id=chat_id)
        return
    if name == "/tick":
        tg_send_bot(bot, "ticking…", chat_id=chat_id)
        try:
            payload = tick()
            tg_send_bot(bot, format_status(payload), chat_id=chat_id)
        except Exception as e:
            tg_send_bot(bot, "tick failed: " + _tg_esc(e), chat_id=chat_id)
        return
    if name in ("/grok", "/connect"):
        _handle_grok_cmd(bot, chat_id, raw, message_id)
        return
    if name == "/ask":
        q = raw.split(None, 1)[1] if " " in raw else ""
        _ask_grok(bot, chat_id, q)
        return
    desk = _parse_desk_cmd(raw)
    if desk:
        kind, mode, coins = desk
        if _is_bias_bot(bot) and kind == "bias":
            try:
                _write_json(GROK_BIAS_FILE, {
                    "bias": str(mode or "").upper(),
                    "updated": _utc(),
                    "source": "bias-bot",
                    "closes": False,
                    "writes_hunters": False,
                    "raw": str(raw)[:80],
                })
            except Exception:
                pass
            feed_append("grok", "GROK " + str(mode or "") + " → BIAS BOT · hunters unchanged", "grok-bias")
            tg_send_bot(
                bot,
                "GROK BIAS <b>" + _tg_esc(str(mode or "").upper()) + "</b> · report only, hunters unchanged.",
                chat_id=chat_id,
            )
            return
        msg = telegram_desk(bot, kind, mode, coins, raw)
        tg_send_bot(bot, _tg_esc(msg), chat_id=chat_id)
        return
    if not name.startswith("/"):
        _ask_grok(bot, chat_id, raw)
        return
    tg_send_bot(bot, "unknown. /help", chat_id=chat_id)


def telegram_loop_bot(bot, stop):
    token = str((bot or {}).get("token") or "").strip()
    if not token:
        return
    offset = 0
    bid = bot.get("id")
    _log(f"telegram on id={bid} chats={bot.get('chats') or 'set on Wire'}")
    if bot.get("chats") and not is_positions_bot(bid):
        tg_send_bot(bot, "<b>Condor online</b> — message this bot. /long LTC  /flat  /close  /help")
    while not stop.is_set():
        try:
            out = _tg_api(token, "getUpdates", {
                "timeout": 25,
                "offset": offset,
                "allowed_updates": ["message"],
            }, timeout=35)
            if not out.get("ok"):
                stop.wait(4)
                continue
            for upd in out.get("result") or []:
                offset = max(offset, int(upd.get("update_id") or 0) + 1)
                msg = upd.get("message") or {}
                chat = (msg.get("chat") or {})
                cid = chat.get("id")
                text = msg.get("text") or ""
                if cid is None or not text:
                    continue
                try:
                    telegram, _ = load_setup()
                    live = next((b for b in telegram if b.get("id") == bid), bot)
                    _handle_tg_text(
                        live, cid, text,
                        message_id=msg.get("message_id"),
                        chat_type=chat.get("type"),
                        from_bot=bool((msg.get("from") or {}).get("is_bot")),
                    )
                except Exception:
                    _log("telegram handle failed\n" + traceback.format_exc())
        except Exception as e:
            if stop.is_set():
                break
            with _lock:
                _tg_state["last_error"] = str(e)[:200]
            _log(f"telegram poll {bid}: {e}")
            stop.wait(5)


def _koordinator_bot():
    """BIAS BOT condor1 if present, else first wired telegram with a chat."""
    graph = _read_json(GRAPH_FILE)
    desks = []
    for n in (graph.get("nodes") or []):
        if isinstance(n, dict) and n.get("id") == "telegram-bot-t84jlc":
            desks.append(str(n.get("id")))
            break
    telegram, _ = load_setup()
    by_id = {b.get("id"): b for b in telegram if b.get("id")}
    for nid in desks:
        bot = by_id.get(nid)
        if bot and bot.get("token"):
            return bot
    for bot in telegram:
        if bot.get("token") and (bot.get("chats") or []):
            return bot
    return None


def koordinator_cmd_loop():
    """Report Grok /long /short /flat on BIAS BOT. Never writes hunter BIAS.

    Telegram getUpdates never sees messages the bot itself sent, so Grok
    FIRE lands in conf/koordinator_cmd.json. This loop logs it. Flask already
    posts to BIAS BOT. Does not call telegram_desk / Desk BIAS / Translator.
    """
    last = None
    while True:
        time.sleep(1)
        try:
            data = _read_json(KOORD_CMD_FILE)
            if not isinstance(data, dict):
                continue
            cid = str(data.get("id") or data.get("ts") or "")
            if not cid or cid == last:
                continue
            raw = str(data.get("cmd") or data.get("bias") or "").strip()
            desk = _parse_desk_cmd(raw)
            if not desk or desk[0] != "bias":
                last = cid
                continue
            kind, mode, coins = desk
            last = cid
            feed_append("grok", "GROK " + str(mode or "") + " → BIAS BOT · hunters unchanged", "grok-bias")
            _log("koordinator report " + str(mode) + " (no hunter BIAS)")
        except Exception:
            _log("koordinator cmd loop failed\n" + traceback.format_exc())


def telegram_supervisor():
    global _tg_off_logged
    while True:
        try:
            telegram, _ = load_setup()
            live = {b["id"]: b for b in telegram if b.get("token")}
            for b in load_positions_bots():
                if b.get("id") and b.get("token"):
                    live.setdefault(b["id"], b)
            with _lock:
                _tg_state["configured"] = bool(live)
                _tg_state["chats"] = [c for b in live.values() for c in (b.get("chats") or [])]
            for bid in list(_tg_workers):
                w = _tg_workers[bid]
                nxt = live.get(bid)
                if not nxt or w.get("token") != nxt.get("token"):
                    w["stop"].set()
                    _tg_workers.pop(bid, None)
                    _log(f"telegram stop {bid}")
            for bid, bot in live.items():
                if bid in _tg_workers:
                    continue
                stop = threading.Event()
                t = threading.Thread(
                    target=telegram_loop_bot,
                    args=(bot, stop),
                    name=f"condor-tg-{bid}",
                    daemon=True,
                )
                _tg_workers[bid] = {"stop": stop, "thread": t, "token": bot.get("token")}
                t.start()
            if not live:
                if not _tg_off_logged:
                    _log("telegram off — drop a Telegram bot on Wire, paste token, wire TG into Condor")
                    _tg_off_logged = True
            else:
                _tg_off_logged = False
        except Exception as e:
            _log(f"telegram supervisor: {e}")
        time.sleep(5)


def tick():
    with _tick_lock:
        return _tick_unlocked()


def _tick_unlocked():
    global _last
    tape = collect_tape()
    telegram, grok = load_setup()
    source = "heuristic"
    err = None
    grok_model = (grok or {}).get("model") if grok else None
    if grok and grok.get("api_key"):
        try:
            regime, bias, why, reasons = grok_predict(tape, grok.get("api_key"), grok.get("model"))
            source = "grok"
        except Exception as e:
            err = str(e)[:240]
            _log(f"grok failed, heuristic: {err}")
            regime, bias, why, reasons = heuristic(tape)
            source = "heuristic"
    else:
        regime, bias, why, reasons = heuristic(tape)
        why = "no grok bot wired · " + why

    applied = apply_wired(bias)
    with _lock:
        previous = dict(_last) if _last else _read_json(GAUGE_FILE)
        tg = dict(_tg_state)
    payload = {
        "ts": _utc(),
        "ok": True,
        "kind": "condor",
        "title": "Condor",
        "regime": regime,
        "bias": bias,
        "why": why,
        "reasons": reasons,
        "tape": tape,
        "source": source,
        "model": grok_model if source == "grok" else None,
        "error": err,
        "interval_sec": INTERVAL,
        "applied": applied,
        "wired": [b for b, _ in wired_hunters(_read_json(GRAPH_FILE))],
        "running": True,
        "telegram": {
            "configured": bool(telegram),
            "bots": len(telegram),
            "chats": sum(len(b.get("chats") or []) for b in telegram),
            "ok": tg.get("ok"),
            "last_send": tg.get("last_send"),
            "last_error": tg.get("last_error"),
            "source": "wire" if any(b.get("id") != "env" for b in telegram) else ("env" if telegram else None),
        },
        "grok_bot": {
            "configured": bool(grok),
            "name": (grok or {}).get("name"),
            "model": grok_model,
            "source": "wire" if grok and grok.get("id") != "env" else ("env" if grok else None),
        },
    }
    _write_json(GAUGE_FILE, payload)
    with _lock:
        _last = payload
    _log(f"regime={regime} bias={bias} src={source} applied={applied or '-'}")
    try:
        bits = [str(regime or "")] + [f"{c} {bias.get(c)}" for c in COINS]
        feed_append("tick", " ".join(bits) + ((" · " + why) if why else ""), source)
    except Exception:
        pass
    try:
        _maybe_notify(payload, previous)
    except Exception as e:
        _log(f"telegram notify: {e}")
    return payload


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/"):
            with _lock:
                last = dict(_last)
                tg = dict(_tg_state)
            self._send(200, {
                "ok": True,
                "service": "condor-gauge",
                "last": last.get("ts"),
                "regime": last.get("regime"),
                "telegram": tg,
            })
            return
        if path in ("/gauge", "/api/condor/gauge"):
            with _lock:
                last = dict(_last) if _last else _read_json(GAUGE_FILE)
            self._send(200, last or {"ok": False, "error": "no tick yet"})
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path in ("/tick", "/api/condor/tick"):
            try:
                self._send(200, tick())
            except Exception as e:
                self._send(500, {"ok": False, "error": str(e)})
            return
        if path in ("/xlate", "/api/condor/xlate"):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                body = {}
            try:
                self._send(200, run_xlate(body))
            except Exception as e:
                self._send(500, {"ok": False, "error": str(e)})
            return
        self._send(404, {"ok": False, "error": "not found"})


def loop():
    while True:
        try:
            tick()
        except Exception:
            _log("tick failed\n" + traceback.format_exc())
        time.sleep(INTERVAL)


def main():
    os.makedirs(CONF_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    telegram, grok = load_setup()
    _log(
        f"condor gauge start interval={INTERVAL}s port={PORT} "
        f"grok={'yes' if grok else 'no'} telegram={len(telegram)}"
    )
    threading.Thread(target=loop, name="condor-tick", daemon=True).start()
    threading.Thread(target=telegram_supervisor, name="condor-tg-sup", daemon=True).start()
    threading.Thread(target=positions_loop, name="condor-positions", daemon=True).start()
    threading.Thread(target=koordinator_cmd_loop, name="condor-koord", daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
