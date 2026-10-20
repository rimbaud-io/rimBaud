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

import re
import os
import sys
import json
import shlex
import hmac
import secrets
import subprocess
import statistics
import threading
import time
import socket
import requests
import urllib.error
import urllib.request
from datetime import datetime
from flask import Flask, render_template, jsonify, request, redirect, Response, send_from_directory

try:
    import matrix_store
except ImportError:
    matrix_store = None

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.jinja_env.cache = None

CONF_DIR = "/conf"
LOGS_DIR = "/logs"
MATRIX_DB = os.path.join(CONF_DIR, "matrix.db")


def _matrix_init():
    if not matrix_store:
        return False
    try:
        matrix_store.init(MATRIX_DB)
        try:
            matrix_store.reconcile_json(CONF_DIR)
        except Exception:
            pass
        try:
            matrix_store.leader_audit_import_json(LOGS_DIR)
        except Exception:
            pass
        return True
    except Exception:
        return False


_MATRIX_OK = _matrix_init()

# Flask WRITE MODE. Default ON (live hunter brain). Off = no hunter file writes,
# no docker restart of hunters, no HL close/open from this process. Hunters keep trading.
FLASK_WRITES_FILE = os.path.join(CONF_DIR, "flask_writes.json")
_flask_writes_lock = threading.Lock()
_flask_writes = True


def _flask_writes_load():
    global _flask_writes
    raw = None
    try:
        with open(FLASK_WRITES_FILE) as f:
            raw = json.load(f)
    except Exception:
        raw = None
    with _flask_writes_lock:
        if isinstance(raw, dict) and "enabled" in raw:
            _flask_writes = bool(raw.get("enabled"))
        elif isinstance(raw, dict) and "writes" in raw:
            _flask_writes = bool(raw.get("writes"))
        else:
            _flask_writes = True


def flask_writes_on():
    with _flask_writes_lock:
        return bool(_flask_writes)


def flask_writes_set(on):
    global _flask_writes
    on = bool(on)
    with _flask_writes_lock:
        _flask_writes = on
    payload = {
        "enabled": on,
        "writes": on,
        "closes": False,
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "flask-ctl",
        "note": "WRITE MODE off blocks hunter BIAS/SL/ctrl and HL close/open. Hunters keep holding.",
    }
    try:
        tmp = FLASK_WRITES_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, FLASK_WRITES_FILE)
    except Exception:
        pass
    return on


def _flask_writes_deny():
    return jsonify({
        "ok": False,
        "error": "Flask WRITE MODE is off — hunter files not written",
        "writes": False,
        "flask_writes": False,
        "closes": False,
        "positions_untouched": True,
        "message": "Flask is read-only. Hunters keep trading. Enable WRITE MODE on Home CTL.",
    }), 403


def _flask_ctl_always(path):
    p = (path or "").rstrip("/")
    return p in ("/api/flask/writes", "/api/ctl", "/api/ctl/spring/stop") or p.startswith("/api/flask/")


def _flask_touches_hunters(path):
    """POSTs that write hunter files, restart hunters, or place/close HL orders."""
    p = (path or "").lower()
    if any(x in p for x in (
        "close", "force_entry", "force_open", "force_close", "hl_close",
        "/open", "hl_scale", "hl_full", "hl_add", "hl_follow", "hl_copy_mode",
        "hl_trader", "hl_risk", "hl_zec",
    )):
        return True
    if p.startswith("/api/home/") and p not in ("/api/home", "/api/home/"):
        return True
    if p.startswith("/api/wire/"):
        return any(x in p for x in (
            "bias", "controller", "compile", "integrate", "containers", "rebuild",
        ))
    if any(x in p for x in ("/bias", "/tune", "/hold", "/moon")):
        return True
    if p.startswith("/api/confiig/"):
        return True
    if p.startswith("/api/copy_trades"):
        return True
    if p.startswith("/api/hl_"):
        return True
    return False


@app.before_request
def _gate_flask_writes():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    path = request.path or ""
    if _flask_ctl_always(path):
        return None
    if flask_writes_on():
        return None
    if _flask_touches_hunters(path):
        return _flask_writes_deny()
    return None


def _spring_base_urls():
    hosts = []
    extra = str(os.environ.get("SPRING_HOST") or "").strip()
    if extra:
        hosts.append(extra)
    for h in ("host.docker.internal", "172.17.0.1", "127.0.0.1"):
        if h not in hosts:
            hosts.append(h)
    try:
        port = int(os.environ.get("SPRING_PORT") or 5004)
    except (TypeError, ValueError):
        port = 5004
    if port < 1 or port > 65535 or port == 5001:
        port = 5004
    return ["http://%s:%s" % (h, port) for h in hosts]


def _spring_http(path, method="GET", body=None, timeout=0.35):
    last = None
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    for base in _spring_base_urls():
        try:
            req = urllib.request.Request(
                base + path,
                data=data,
                headers={"Content-Type": "application/json"} if data is not None else {},
                method=method,
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8") or "{}"
            out = json.loads(raw)
            if isinstance(out, dict) and out.get("service") == "spring":
                out["_base"] = base
                return out
            if isinstance(out, dict) and (out.get("ok") is True or out.get("stopped") is True):
                out["_base"] = base
                return out
            last = out
        except Exception as e:
            last = {"ok": False, "error": str(e)[:160], "_base": base}
    return last if isinstance(last, dict) else {"ok": False, "up": False}


_flask_writes_load()


def _sql_kv_set(bot_id, key, value, source="wire"):
    if not _MATRIX_OK:
        return
    try:
        matrix_store.kv_set(bot_id, key, value, source)
    except Exception:
        pass


def _sql_kv_map(bot_id):
    if not _MATRIX_OK:
        return {}
    try:
        return matrix_store.kv_map(bot_id) or {}
    except Exception:
        return {}


def _sql_bias_write(bot_id, mapping):
    if not _MATRIX_OK:
        return
    try:
        matrix_store.bias_set_map(bot_id, mapping)
    except Exception:
        pass


def _sql_command(bot_id, kind, payload=None):
    if not _MATRIX_OK:
        return None
    try:
        return matrix_store.command_enqueue(bot_id, kind, payload or {}, source="dashboard")
    except Exception:
        return None


def _sql_bias_read(bot_id):
    if not _MATRIX_OK:
        return {}
    try:
        return matrix_store.bias_map(bot_id) or {}
    except Exception:
        return {}


def _sql_status_read(bot_id):
    if not _MATRIX_OK:
        return {}
    try:
        return matrix_store.status_get(bot_id) or {}
    except Exception:
        return {}
HL_COPY_RUNTIME = os.path.join(CONF_DIR, "hl_copy_runtime.json")
HL_COPY_STATUS = os.path.join(CONF_DIR, "hl_copy_status.json")
HL_RISK_OVERRIDES = os.path.join(CONF_DIR, "hl_risk_overrides.json")
HL_FORCE_RISK_TOGGLE = os.path.join(CONF_DIR, "force_risk_toggle_hl_copy.json")

BOTS = {
    "hummingbot-hl": {
        "name": "HL Copy Trader",
        "label": "Hyperliquid",
        "pair": "Multi",
        "icon": "⚡",
        "color": "#4f8cff",
        "force_entry_file": None,
        "force_close_file": None,
        "can_force_entry": False,
        "log_file": "logs_hl_copy_trader.log",
    },
    "hummingbot-sol": {
        "name": "SOL/USDC Scalper",
        "label": "Jupiter",
        "pair": "SOL-USDC",
        "icon": "◎",
        "color": "#9945ff",
        "force_entry_file": None,
        "force_close_file": os.path.join(CONF_DIR, "force_close_sol_micro.txt"),
        "can_force_entry": False,
        "log_file": "logs_sol_micro_trader.log",
    },
    "hummingbot": {
        "name": "AGENC/SOL Trader",
        "label": "Meteora",
        "pair": "AGENC-SOL",
        "icon": "🤖",
        "color": "#00d4aa",
        "force_entry_file": None,
        "force_close_file": os.path.join(CONF_DIR, "force_close_agenc.txt"),
        "can_force_entry": False,
        "log_file": "logs_agenc_sol_trader.log",
    },
    "hummingbot-bn-swing": {
        "name": "PMM Simple",
        "label": "Hyperliquid",
        "pair": "BTC / ETH / LTC",
        "icon": "▤",
        "color": "#f0b90b",
        "force_entry_file": None,
        "force_close_file": os.path.join(CONF_DIR, "force_close_bn_swing.txt"),
        "can_force_entry": False,
        "log_file": "logs_pmm_simple_trader.log",
    },
    "hummingbot-helliot": {
        "name": "Helliot",
        "label": "Hyperliquid sub",
        "pair": "BTC / ETH / LTC",
        "icon": "∿",
        "color": "#7ecbff",
        "force_entry_file": None,
        "force_close_file": os.path.join(CONF_DIR, "force_close_helliot.txt"),
        "can_force_entry": False,
        "log_file": "logs_helliot_trader.log",
    },
    "hummingbot-surfn": {
        "name": "SuperTrend",
        "label": "supertrend_v1 · Hyperliquid sub",
        "pair": "BTC / ETH / LTC / TAO",
        "icon": "≈",
        "color": "#2ee6c7",
        "force_entry_file": None,
        "force_close_file": os.path.join(CONF_DIR, "force_close_surfn.txt"),
        "can_force_entry": False,
        "log_file": "logs_surfn_trader.log",
    },
    "hummingbot-shortn": {
        "name": "Shorty",
        "label": "Hyperliquid sub",
        "pair": "BTC / ETH / LTC",
        "icon": "▾",
        "color": "#ff5d73",
        "force_entry_file": None,
        "force_close_file": os.path.join(CONF_DIR, "force_close_shortn.txt"),
        "can_force_entry": False,
        "log_file": "logs_shortn_trader.log",
    },
    "hummingbot-dman": {
        "name": "Dman",
        "label": "Hyperliquid sub",
        "pair": "BTC / ETH / LTC",
        "icon": "▦",
        "color": "#c084fc",
        "force_entry_file": None,
        "force_close_file": os.path.join(CONF_DIR, "force_close_dman.txt"),
        "can_force_entry": False,
        "log_file": "logs_dman_trader.log",
    },
    "hummingbot-onlyup": {
        "name": "Only Up",
        "label": "Hyperliquid sub",
        "pair": "BTC / LTC",
        "icon": "▲",
        "color": "#ffd166",
        "force_entry_file": None,
        "force_close_file": os.path.join(CONF_DIR, "force_close_onlyup.txt"),
        "can_force_entry": False,
        "log_file": "logs_onlyup_trader.log",
    },
    "hummingbot-dman-maker": {
        "name": "DMan Maker",
        "label": "Hyperliquid sub",
        "pair": "BTC / ETH / LTC",
        "icon": "⧉",
        "color": "#a78bfa",
        "force_entry_file": None,
        "force_close_file": os.path.join(CONF_DIR, "force_close_dman_maker.txt"),
        "can_force_entry": False,
        "log_file": "logs_dman_maker_v2_trader.log",
    },
}

BN_SWING_BIAS_FILE = os.path.join(CONF_DIR, "bn_swing_bias.json")
BN_SWING_STATUS_FILE = os.path.join(CONF_DIR, "bn_swing_status.json")
BN_SWING_FORCE_CLOSE = os.path.join(CONF_DIR, "force_close_bn_swing.txt")
BN_SWING_FORCE_OPEN = os.path.join(CONF_DIR, "force_open_bn_swing.json")
BN_SWING_TRADES_FILE = os.path.join(CONF_DIR, "bn_swing_trades.json")
HELLIOT_BIAS_FILE = os.path.join(CONF_DIR, "helliot_bias.json")
HELLIOT_STATUS_FILE = os.path.join(CONF_DIR, "helliot_status.json")
HELLIOT_FORCE_CLOSE = os.path.join(CONF_DIR, "force_close_helliot.txt")
HELLIOT_FORCE_OPEN = os.path.join(CONF_DIR, "force_open_helliot.json")
HELLIOT_TRADES_FILE = os.path.join(CONF_DIR, "helliot_trades.json")
SURFN_BIAS_FILE = os.path.join(CONF_DIR, "surfn_bias.json")
SURFN_STATUS_FILE = os.path.join(CONF_DIR, "surfn_status.json")
SURFN_FORCE_CLOSE = os.path.join(CONF_DIR, "force_close_surfn.txt")
SURFN_FORCE_OPEN = os.path.join(CONF_DIR, "force_open_surfn.json")
SURFN_TRADES_FILE = os.path.join(CONF_DIR, "surfn_trades.json")
SURFN_WALLET = ""
SHORTN_BIAS_FILE = os.path.join(CONF_DIR, "shortn_bias.json")
SHORTN_STATUS_FILE = os.path.join(CONF_DIR, "shortn_status.json")
SHORTN_FORCE_CLOSE = os.path.join(CONF_DIR, "force_close_shortn.txt")
SHORTN_FORCE_OPEN = os.path.join(CONF_DIR, "force_open_shortn.json")
SHORTN_TRADES_FILE = os.path.join(CONF_DIR, "shortn_trades.json")
DMAN_BIAS_FILE = os.path.join(CONF_DIR, "dman_bias.json")
DMAN_TUNE_FILE = os.path.join(CONF_DIR, "dman_tune.json")
DMAN_STATUS_FILE = os.path.join(CONF_DIR, "dman_status.json")
DMAN_FORCE_CLOSE = os.path.join(CONF_DIR, "force_close_dman.txt")
DMAN_FORCE_OPEN = os.path.join(CONF_DIR, "force_open_dman.json")
DMAN_TRADES_FILE = os.path.join(CONF_DIR, "dman_trades.json")
DMAN_MAKER_BIAS_FILE = os.path.join(CONF_DIR, "dman_maker_bias.json")
DMAN_MAKER_STATUS_FILE = os.path.join(CONF_DIR, "dman_maker_status.json")
DMAN_MAKER_FORCE_CLOSE = os.path.join(CONF_DIR, "force_close_dman_maker.txt")
DMAN_MAKER_TRADES_FILE = os.path.join(CONF_DIR, "dman_maker_trades.json")
ONLYUP_BIAS_FILE = os.path.join(CONF_DIR, "onlyup_bias.json")
ONLYUP_STATUS_FILE = os.path.join(CONF_DIR, "onlyup_status.json")
ONLYUP_FORCE_CLOSE = os.path.join(CONF_DIR, "force_close_onlyup.txt")
ONLYUP_FORCE_OPEN = os.path.join(CONF_DIR, "force_open_onlyup.json")
ONLYUP_TRADES_FILE = os.path.join(CONF_DIR, "onlyup_trades.json")
ONLYUP_MOON_FILE = os.path.join(CONF_DIR, "onlyup_moon.json")
ONLYUP_COINS = ("BTC", "LTC", "TAO")
ONLYUP_MOON_PCT = 50.0
BOT_ACCOUNTS_FILE = os.path.join(CONF_DIR, "bot_accounts.json")
BOT_SL_FILE = os.path.join(CONF_DIR, "bot_sl.json")
DESK_BIAS_FILE = os.path.join(CONF_DIR, "desk_bias.json")
GROK_BIAS_FILE = os.path.join(CONF_DIR, "grok_bias.json")
_DESK_BIAS_LOCK = threading.Lock()
CONFIG_PASSWORD = os.environ.get("CONFIG_PASSWORD", "Dan2Delion")
ENCRYPT_CONTAINERS = (
    "hummingbot-hl",
    "hummingbot-surfn",
    "hummingbot-helliot",
    "hummingbot-bn-swing",
    "hummingbot-shortn",
    "hummingbot-dman",
    "hummingbot-onlyup",
    "hummingbot-dman-maker",
)


def load_bot_accounts():
    try:
        with open(BOT_ACCOUNTS_FILE, "r") as f:
            data = json.load(f) or {}
        bots = data.get("bots")
        if isinstance(bots, list):
            return data
    except Exception:
        pass
    return {"bots": [], "updated": None}


def wallet_for(bot_id, fallback=None):
    for bot in load_bot_accounts().get("bots") or []:
        if bot.get("id") == bot_id:
            addr = str(bot.get("address") or "").strip()
            if addr:
                return addr
    return fallback


def _valid_hl_address(addr):
    a = str(addr or "").strip()
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", a))


def _yml_use_vault(path):
    try:
        with open(path, "r") as f:
            text = f.read()
        m = re.search(r"(?m)^use_vault:\s*(true|false)", text, re.I)
        if m:
            return m.group(1).lower() == "true"
    except Exception:
        pass
    return None


def _encrypt_hl_address(address):
    """Encrypt a 0x address the same way Hummingbot connector yml expects."""
    addr = str(address).strip()
    script = (
        "import json, os\n"
        "from hummingbot.client.config.config_crypt import _create_v3_keyfile_json\n"
        "addr = os.environ['HL_ADDR'].encode()\n"
        "pw = os.environ['HL_PW'].encode()\n"
        "kf = _create_v3_keyfile_json(addr, pw)\n"
        "print(json.dumps(kf).encode().hex())\n"
    )
    last_err = "no hummingbot container running to encrypt"
    for name in ENCRYPT_CONTAINERS:
        if not get_container_running(name):
            continue
        try:
            r = subprocess.run(
                ["docker", "exec", "-e", f"HL_ADDR={addr}", "-e", f"HL_PW={CONFIG_PASSWORD}",
                 name, "python3", "-c", script],
                capture_output=True, text=True, timeout=30,
            )
            hexout = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
            if r.returncode == 0 and re.fullmatch(r"[0-9a-fA-F]+", hexout) and len(hexout) > 80:
                return hexout, None
            last_err = (r.stderr or r.stdout or "encrypt failed")[:400]
        except Exception as e:
            last_err = str(e)
    return None, last_err


def _write_connector_yml(path, address_hex, use_vault):
    with open(path, "r") as f:
        text = f.read()
    vault = "true" if use_vault else "false"
    if re.search(r"(?m)^use_vault:\s*\S+", text):
        text = re.sub(r"(?m)^use_vault:\s*\S+", f"use_vault: {vault}", text, count=1)
    else:
        text = text.replace("connector: hyperliquid_perpetual",
                            f"connector: hyperliquid_perpetual\nuse_vault: {vault}", 1)
    if re.search(r"(?m)^hyperliquid_perpetual_address:\s*\S+", text):
        text = re.sub(
            r"(?m)^hyperliquid_perpetual_address:\s*\S+",
            f"hyperliquid_perpetual_address: {address_hex}",
            text,
            count=1,
        )
    else:
        raise RuntimeError("connector yml has no hyperliquid_perpetual_address line")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _restart_container(name):
    r = subprocess.run(
        ["docker", "restart", name],
        capture_output=True, text=True, timeout=90,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "restart failed").strip()[:400])
    return True

ANSI = re.compile(r'(\x1b|\033)\[[0-9;]*[mCDABHfJKsu]|[\x00-\x08\x0b\x0e-\x1f\x7f]'
                  r'|\[[0-9]+[ABCDHJK]|\[[\?][0-9]+[hl]')


def strip_ansi(text):
    return ANSI.sub('', text)


def get_container_state(name):
    """Docker status only. paused is frozen, not stopped. No exchange call."""
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", name],
            capture_output=True, text=True, timeout=4,
        )
        status = (r.stdout or "").strip()
    except Exception:
        status = ""
    return {
        "status": status,
        "running": status == "running",
        "paused": status == "paused",
    }


def get_container_running(name):
    return get_container_state(name)["running"]


WIRE_STACK_CONTAINERS = (
    ("hummingbot-hl", "HL Copy", "hunter"),
    ("hummingbot-bn-swing", "PMM Simple", "hunter"),
    ("hummingbot-helliot", "Helliot", "hunter"),
    ("hummingbot-surfn", "SuperTrend", "hunter"),
    ("hummingbot-shortn", "Shorty", "hunter"),
    ("hummingbot-dman", "Dman", "hunter"),
    ("hummingbot-onlyup", "Only Up", "hunter"),
    ("hummingbot-dman-maker", "DMan Maker", "hunter"),
    ("hummingbot-sol", "SOL scalper", "hunter"),
    ("hummingbot", "AGENC", "hunter"),
    ("hummingbot-dashboard", "Dashboard", "infra"),
    ("hummingbot-condor", "Condor", "infra"),
    ("hummingbot-version", "Version", "infra"),
    ("hummingbot-audit", "Audit", "infra"),
    ("hl-testnet-copy", "Testnet copy", "extra"),
    ("hl-hip3-copy", "HIP3 copy", "extra"),
    ("gateway", "Gateway", "infra"),
)
WIRE_NEVER_STOP = frozenset(("hummingbot-dashboard", "dashboard"))


def _docker_ps_map():
    """name -> {state, status} for every local container."""
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=8,
        )
    except Exception:
        return {}
    out = {}
    if r.returncode != 0:
        return out
    for line in (r.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        if not name:
            continue
        out[name] = {
            "state": (parts[1] or "").strip().lower(),
            "status": (parts[2] if len(parts) > 2 else parts[1] or "").strip(),
        }
    return out


def _stop_container(name):
    r = subprocess.run(
        ["docker", "stop", "-t", "15", name],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "stop failed").strip()[:400])
    return True


def _start_container(name):
    r = subprocess.run(
        ["docker", "start", name],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "start failed").strip()[:400])
    return True


def _container_n_pos(bot_id):
    """Open positions from status files only — no extra HL REST."""
    h = registry_hunter(bot_id)
    if h and h.get("status_file"):
        path = os.path.join(CONF_DIR, os.path.basename(h.get("status_file")))
        st = _read_json_file(path) or {}
        n = 0
        if h.get("kind") == "copy":
            for m in st.get("mirrors") or []:
                if not isinstance(m, dict):
                    continue
                try:
                    sz = abs(float(m.get("size") or m.get("amount") or 0))
                except (TypeError, ValueError):
                    sz = 0.0
                if sz > 0:
                    n += 1
            return n
        pos = st.get("positions") if isinstance(st.get("positions"), dict) else {}
        for row in pos.values():
            if not isinstance(row, dict):
                continue
            side = str(row.get("position_side") or row.get("side") or "").upper()
            if side in ("LONG", "SHORT"):
                n += 1
        return n
    paths = {
        "hummingbot-hl": HL_COPY_STATUS,
        "hummingbot-bn-swing": BN_SWING_STATUS_FILE,
        "hummingbot-helliot": HELLIOT_STATUS_FILE,
        "hummingbot-surfn": SURFN_STATUS_FILE,
        "hummingbot-shortn": SHORTN_STATUS_FILE,
        "hummingbot-dman": DMAN_STATUS_FILE,
        "hummingbot-onlyup": ONLYUP_STATUS_FILE,
        "hummingbot-dman-maker": os.path.join(CONF_DIR, "dman_maker_status.json"),
    }
    path = paths.get(bot_id)
    if not path:
        return 0
    st = _read_json_file(path) or {}
    n = 0
    if bot_id == "hummingbot-hl":
        for m in st.get("mirrors") or []:
            if not isinstance(m, dict):
                continue
            try:
                sz = abs(float(m.get("size") or m.get("amount") or 0))
            except (TypeError, ValueError):
                sz = 0.0
            if sz > 0:
                n += 1
        return n
    pos = st.get("positions") if isinstance(st.get("positions"), dict) else {}
    for row in pos.values():
        if not isinstance(row, dict):
            continue
        side = str(row.get("position_side") or row.get("side") or "").upper()
        if side in ("LONG", "SHORT"):
            n += 1
    return n


def _wire_container_rows(canvas_bots):
    canvas = {str(x) for x in (canvas_bots or []) if x}
    ps = _docker_ps_map()
    known = {row[0]: row for row in WIRE_STACK_CONTAINERS}
    for h in _load_registry():
        cid = h.get("container") or h.get("id")
        if cid and cid not in known:
            known[cid] = (cid, h.get("name") or cid, "hunter")
    names = list(known.keys())
    for name in ps:
        if name not in known and (
            name.startswith("hummingbot") or name.startswith("hl-") or name == "gateway"
        ):
            names.append(name)
    rows = []
    for name in names:
        meta = known.get(name) or (name, name, "extra")
        label, kind = meta[1], meta[2]
        info = ps.get(name) or {}
        state = info.get("state") or "missing"
        running = state == "running"
        on_canvas = name in canvas
        protected = name in WIRE_NEVER_STOP
        needed = bool(protected or on_canvas)
        n_pos = _container_n_pos(name) if kind == "hunter" else 0
        mark = "protected"
        if protected:
            mark = "protected"
        elif on_canvas and running:
            mark = "on_wire"
        elif on_canvas and not running:
            mark = "missing"
        elif running:
            mark = "not_needed"
        else:
            mark = "idle"
        rows.append({
            "id": name,
            "name": (BOTS.get(name) or {}).get("name") or label,
            "kind": kind,
            "running": running,
            "state": state,
            "status": info.get("status") or ("missing" if not info else state),
            "on_canvas": on_canvas,
            "needed": needed,
            "protected": protected,
            "mark": mark,
            "n_pos": n_pos,
            "can_stop": bool(running and not protected and not on_canvas),
            "can_start": bool((not running) and on_canvas and state != "missing"),
        })
    order = {"not_needed": 0, "missing": 1, "on_wire": 2, "idle": 3, "protected": 4}
    rows.sort(key=lambda r: (order.get(r["mark"], 9), r["name"]))
    return rows


def get_logs(name, lines=150):
    """Read from log file (clean) with docker logs as fallback."""
    meta = BOTS.get(name, {})
    log_file = meta.get("log_file")
    if log_file:
        path = os.path.join(LOGS_DIR, log_file)
        # Tail only — copy log is 50MB+; a full read hangs /api/hl_live.
        result = tail_log_lines(path, n=lines, max_bytes=min(400000, max(65536, int(lines) * 500)))
        if result:
            return result
    # Fallback: docker logs
    try:
        r = subprocess.run(
            ["docker", "logs", "--tail", str(lines), name],
            capture_output=True, text=True, timeout=6
        )
        raw = r.stdout + r.stderr
        cleaned = strip_ansi(raw)
        result = []
        for line in cleaned.split("\n"):
            line = line.strip()
            if line and len(line) > 3 and not re.match(r'^\[[\d;]+[A-Z]', line):
                result.append(line)
        return result
    except Exception:
        return []


def filter_display_logs(bot_id, logs, limit=100):
    """
    Clean logs for the dashboard UI: drop noisy EVENT_LOG dumps, truncate long
    lines, keep strategy-relevant lines for the copy trader.
    """
    out = []
    for line in logs:
        if not line or len(line) < 5:
            continue
        # Event reporter dumps are huge JSON and break the UI / drown signal
        if "EVENT_LOG" in line:
            continue
        if "html>" in line.lower() or "DOCTYPE" in line:
            continue
        if "Cap " in line and "books" in line and "not subscribing" in line:
            continue
        if "Deferring OPEN" in line:
            continue
        # Hummingbot HL WS handshake noise (not a failed trade)
        if any(n in line for n in (
            "Unexpected error occurred subscribing",
            "Traceback (most recent call last)",
            "Failed to remove ",
            "is not being tracked",
            "is not ready. Please wait",
            "[stall] connector not ready",
            "Cannot write to closing transport",
            "Cannot write to clo",
            "websocket connection was closed",
            "ClientConnectionResetError",
            "send_frame",
            "_write_websocket_frame",
            "aiohttp",
            'File "/opt/conda',
            'File "/home/hummingbot',
            "await self._writer",
        )):
            continue
        if line[:1] in (" ", "\t") and "strategy_v2_base" not in line:
            continue
        # HL copy trader: keep useful strategy lines when possible
        if bot_id == "hummingbot-hl":
            keep_kw = (
                "heartbeat", "WE COPY", "WE CLOSE", "OPENED", "CLOSED", "SKIP",
                "restore", "reconcile", "orphan", "stall", "markets",
                "HL Copy", "mirror", "DEFER", "ORPHAN",
                "Startup", "Trader", "leverage", "Balance", "Using HL",
                "Cap 2/2", "Flip ",
            )
            if " - ERROR - " in line or " - WARNING - " in line:
                continue
            if " - INFO - " in line and not any(k in line for k in keep_kw):
                if "strategy_v2_base" not in line and "hl_copy" not in line.lower():
                    continue
        # Truncate runaway lines (HTML 504 bodies etc.)
        if len(line) > 400:
            line = line[:400] + "…"
        out.append(line)
    return out[-limit:]


_HOME_ISSUE_RE = re.compile(
    r"NOT READY|HTTP status is 429|Traceback \(most recent call last\)|"
    r" - ERROR - |OSError:|blocked —|stalled=\d{3,}|Rate-limited fetching",
    re.I,
)


def tail_log_lines(path, n=50, max_bytes=65536):
    """Last n non-empty lines without reading a 50MB log whole."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = min(size, max_bytes)
            if block <= 0:
                return []
            f.seek(max(0, size - block))
            data = f.read().decode("utf-8", "replace")
        lines = [strip_ansi(l).rstrip() for l in data.splitlines() if l.strip()]
        return lines[-n:]
    except Exception:
        return []


def home_bot_logs_and_health(bot_id, running, status):
    """Lamp + last log lines for the home cards."""
    meta = BOTS.get(bot_id) or {}
    path = os.path.join(LOGS_DIR, meta.get("log_file") or "")
    raw = tail_log_lines(path, 200, max_bytes=200000)
    cleaned = []
    seen = set()
    for line in raw:
        if "EVENT_LOG" in line or "html>" in line.lower():
            continue
        if re.match(r"^\s*(File \"|raise |from )", line) or line in ("^",):
            continue
        if "not subscribing" in line or "Deferring OPEN" in line:
            continue
        if len(line) > 220:
            line = line[:217] + "…"
        key = re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ - \d+ - ", "", line)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(line)
    display = cleaned[-10:]
    # Live flags are the lamp. Stale "429" / "is not ready" in the log tail
    # used to keep every card on HL 429 even after the connector recovered.
    connector_ok = True
    if bot_id == "hummingbot-hl":
        if "connector_ready" in (status or {}):
            connector_ok = bool(status.get("connector_ready"))
    elif "ready" in (status or {}):
        connector_ok = bool(status.get("ready"))
    reason = None
    if not running:
        reason = "container down"
    elif not connector_ok:
        reason = "not ready"
    elif status.get("wallet_ok") is False:
        reason = "no subaccount"
    else:
        # Live ready-state already passed. 429/OSError/traceback in the log tail
        # is normal on a shared HL IP and must not paint the card red.
        pass
    ok = reason is None
    return {
        "ok": ok,
        "health": "ok" if ok else "issue",
        "health_reason": reason or "ok",
        "logs": display,
    }


def _find(logs, pattern, group=1, last=True):
    """Search logs (reversed if last=True) for a regex pattern, return first match group."""
    seq = reversed(logs) if last else logs
    for line in seq:
        m = re.search(pattern, line)
        if m:
            try:
                return m.group(group)
            except Exception:
                return None
    return None


def parse_hl_status(logs):
    status = {
        "btc_price": None, "bbp": None, "signal": "NEUTRAL",
        "active": False,
        "position": None,     # "LONG" or None
        "entry_price": None,
        "amount": None,
        "pnl_pct": None,
        "pnl_usd": None,
        "age_min": None,
        "tp_target": None,
        "sl_target": None,
        "leverage": None,
    }

    for line in reversed(logs):
        if "ACTIVE" in line:
            status["active"] = True
        if "leverage" in line.lower() and status["leverage"] is None:
            m = re.search(r'(\d+)x', line)
            if m:
                status["leverage"] = m.group(1)

    status["btc_price"] = _find(logs, r'BTC Price\s*:\s*\$([\d,]+\.?\d*)')
    bbp_raw = _find(logs, r'BBP\s*:\s*(0\.\d+)')
    if bbp_raw:
        try:
            status["bbp"] = float(bbp_raw)
        except Exception:
            pass

    sig = _find(logs, r'Signal\s*:\s*(LONG|SHORT|NEUTRAL)')
    if sig:
        status["signal"] = sig

    # Check most recent format_status() output first — it's the ground truth
    # "Position   : None" means no position; "── Open LONG ──" means position open
    recent_none = _find(logs, r'Position\s*:\s*None')
    recent_open = _find(logs, r'Open (LONG|SHORT)')

    # Find indices to determine which is more recent
    last_none_idx = -1
    last_open_idx = -1
    last_enter_idx = -1
    last_exit_idx = -1
    for i, line in enumerate(logs):
        if "Position" in line and "None" in line:
            last_none_idx = i
        if "Open LONG" in line or "Open SHORT" in line:
            last_open_idx = i
        if "ENTERING LONG" in line:
            last_enter_idx = i
        if "EXITING" in line:
            last_exit_idx = i

    # format_status "Position: None" overrides old ENTERING events
    if last_none_idx > last_enter_idx and last_none_idx > last_open_idx:
        status["position"] = None
    elif last_open_idx > last_none_idx or (last_enter_idx > last_exit_idx and last_none_idx == -1):
        status["position"] = "LONG"
        # Get entry details from ENTERING log line
        if last_enter_idx >= 0:
            enter_line = logs[last_enter_idx]
            m = re.search(r'Price:\s*\$([\d,]+\.?\d*)', enter_line)
            if m:
                status["entry_price"] = m.group(1)
            m = re.search(r'Amount:\s*([\d.]+)\s*BTC', enter_line)
            if m:
                status["amount"] = m.group(1) + " BTC"
            m = re.search(r'Margin:\s*\$([\d.]+)', enter_line)
            if m:
                status["margin"] = m.group(1)
            m = re.search(r'Notional:\s*\$([\d,]+\.?\d*)', enter_line)
            if m:
                status["notional"] = m.group(1).replace(",", "")

    # If position open, get latest values from format_status blocks
    if status["position"]:
        status["entry_price"] = status.get("entry_price") or _find(logs, r'Entry\s*:\s*\$([\d,]+\.?\d*)')
        status["amount"] = status.get("amount") or _find(logs, r'Amount\s*:\s*([\d.]+\s*BTC)')
        status["age_min"] = _find(logs, r'Age\s*:\s*([\d.]+)\s*min')
        status["tp_target"] = _find(logs, r'TP target\s*:\s*\$([\d,]+\.?\d*)')
        status["sl_target"] = _find(logs, r'SL target\s*:\s*\$([\d,]+\.?\d*)')
        status["pnl_raw"] = _find(logs, r'PnL\s*:\s*([+-][\d.]+%\s*→\s*[+-][\d.]+%[^\n]*)')

    return status


def parse_sol_status(logs):
    status = {
        "sol_price": None, "bbp": None, "signal": "NEUTRAL",
        "position": None, "entry_price": None, "amount": None,
        "pnl_raw": None, "age_min": None, "tp_target": None, "sl_target": None,
    }

    bbp_raw = _find(logs, r'BBP\s*:\s*(0\.\d+)')
    if bbp_raw:
        try:
            status["bbp"] = float(bbp_raw)
        except Exception:
            pass

    sig = _find(logs, r'Signal\s*:\s*(LONG|BUY|SHORT|NEUTRAL)')
    if sig:
        status["signal"] = "LONG" if sig in ("LONG", "BUY") else sig

    last_enter = None
    last_exit_idx = -1
    for i, line in enumerate(logs):
        if "ENTERING" in line or ("BUY" in line and "SOL" in line):
            last_enter = i
        if "EXITING" in line or "SELLING" in line:
            last_exit_idx = i

    if last_enter is not None and last_enter > last_exit_idx:
        status["position"] = "LONG"
        enter_line = logs[last_enter]
        m = re.search(r'\$([\d.]+)', enter_line)
        if m:
            status["entry_price"] = m.group(1)

    return status


def parse_agenc_status(logs):
    status = {
        "position": None, "entry_price": None, "amount": None,
        "pnl_raw": None, "age_min": None, "tp_target": None,
        "sl_disabled": False, "price": None, "sol_usd": None,
        "signal": "NEUTRAL", "bbp": None,
    }

    # These fields appear in format_status when a position is open
    amount = _find(logs, r'Amount\s*[:\s]+([\d,]+)\s*AGENC')
    if amount:
        status["position"] = "LONG"
        status["amount"] = amount + " AGENC"

    entry = _find(logs, r'Entry\s*[:\s]+(0\.\d+)\s*SOL')
    if entry:
        status["entry_price"] = entry + " SOL"

    pnl = _find(logs, r'PnL\s*[:\s]*([+-][\d.]+%[^\n]*)')
    if pnl:
        status["pnl_raw"] = pnl.strip()

    age = _find(logs, r'Age\s*[:\s]+([\d.]+)\s*min')
    if age:
        status["age_min"] = age

    tp = _find(logs, r'TP target\s*[:\s]+(0\.\d+\s*SOL[^\n]*)')
    if tp:
        status["tp_target"] = tp.strip()

    price = _find(logs, r'Price\s*[:\s]+(0\.\d+)\s*SOL')
    if price:
        status["price"] = price + " SOL"

    sol_usd = _find(logs, r'SOL/USD\s*[:\s]*\$([\d.]+)')
    if sol_usd:
        status["sol_usd"] = sol_usd

    if _find(logs, r'Stop\s*Loss\s*.*DISABLED'):
        status["sl_disabled"] = True

    bbp_raw = _find(logs, r'BBP\s*[:\s]+(0\.\d+)')
    if bbp_raw:
        try:
            status["bbp"] = float(bbp_raw)
        except Exception:
            pass

    sig = _find(logs, r'Signal\s*[:\s]*(LONG|SHORT|NEUTRAL|BUY)')
    if sig:
        status["signal"] = "LONG" if sig == "BUY" else sig

    return status


def get_btc_live():
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "5m", "limit": 30},
            timeout=3
        )
        closes = [float(c[4]) for c in r.json()]
        window = closes[-20:]
        sma = sum(window) / 20
        std = statistics.stdev(window)
        lower = sma - 2.0 * std
        upper = sma + 2.0 * std
        price = closes[-1]
        bbp = (price - lower) / (upper - lower) if upper != lower else 0.5
        long_trigger = lower + 0.20 * (upper - lower)
        return {
            "price": f"{price:,.2f}",
            "bbp": round(bbp, 3),
            "long_trigger": f"{long_trigger:,.2f}",
            "gap": f"{price - long_trigger:,.0f}",
            "signal": "LONG" if bbp < 0.20 else ("SHORT" if bbp > 0.80 else "NEUTRAL"),
            "lower": f"{lower:,.2f}",
            "upper": f"{upper:,.2f}",
        }
    except Exception:
        return None


def get_sol_live():
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": "SOLUSDT"},
            timeout=3
        )
        price = float(r.json()["price"])
        return {"price": f"{price:.2f}"}
    except Exception:
        return None


COPY_TRADES_LOG     = os.path.join(LOGS_DIR, "copy_trades.log")
HL_COPY_CONF        = os.path.join(CONF_DIR, "scripts", "hl_copy_trader.yml")
HL_FOLLOW_NOTES     = os.path.join(CONF_DIR, "hl_follow_notes.json")
HL_INFO_URL         = "https://api.hyperliquid.xyz/info"

try:
    from hl_ip_budget import record as _hl_ip_record, snapshot as _hl_ip_snapshot
except Exception:
    def _hl_ip_record(*args, **kwargs):
        return None
    def _hl_ip_snapshot():
        return {
            "cap": 1200, "window_s": 60, "used": 0, "left": 1200, "pct": 0,
            "calls": 0, "refill_in_s": 0, "refill_weight": 0, "last_429_s": None,
            "ws_cap": 10, "ws_new_per_min": 30,
            "note": "shared public IP · rolling 60s token window",
        }


def _hl_info_post(payload, timeout=5):
    """POST /info and count weight against the shared IP token bucket."""
    _hl_ip_record(payload, src="dash")
    r = requests.post(HL_INFO_URL, json=payload, timeout=timeout)
    if r.status_code == 429:
        _hl_ip_record(payload, src="dash", is_429=True, weight=0)
    r.raise_for_status()
    return r.json()


_CPU_SNAP = None
_NET_SNAP = {"ts": 0, "data": None}
_HEALTH_LOCK = threading.Lock()


def _cpu_pct():
    global _CPU_SNAP
    try:
        with open("/proc/stat", "r") as f:
            parts = f.readline().split()
        nums = [int(x) for x in parts[1:8]]
        idle = nums[3] + nums[4]
        total = sum(nums)
    except Exception:
        try:
            load1 = os.getloadavg()[0]
            n = max(1, os.cpu_count() or 1)
            return round(min(100.0, 100.0 * load1 / n), 1)
        except Exception:
            return None
    prev = _CPU_SNAP
    _CPU_SNAP = (total, idle)
    if not prev:
        return None
    dt = total - prev[0]
    di = idle - prev[1]
    if dt <= 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * (1.0 - (di / dt)))), 1)


def _mem_pct():
    try:
        info = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                bits = line.split()
                if len(bits) >= 2:
                    info[bits[0].rstrip(":")] = float(bits[1])
        total = info.get("MemTotal") or 0
        avail = info.get("MemAvailable") or info.get("MemFree") or 0
        if total <= 0:
            return None, None, None
        used = total - avail
        pct = round(100.0 * used / total, 1)
        return pct, round(used / 1024.0 / 1024.0, 2), round(total / 1024.0 / 1024.0, 2)
    except Exception:
        return None, None, None


def _disk_pct(path=None):
    path = path or CONF_DIR or "/"
    try:
        st = os.statvfs(path)
        total = st.f_blocks * st.f_frsize
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        if total <= 0:
            return None, None, None
        pct = round(100.0 * used / total, 1)
        return pct, round(used / 1024.0 / 1024.0 / 1024.0, 2), round(total / 1024.0 / 1024.0 / 1024.0, 2)
    except Exception:
        return None, None, None


def _net_probe():
    now = time.time()
    with _HEALTH_LOCK:
        cached = _NET_SNAP.get("data")
        if cached and now - float(_NET_SNAP.get("ts") or 0) < 10:
            return dict(cached)
    t0 = time.time()
    err = None
    ok = False
    try:
        s = socket.create_connection(("1.1.1.1", 443), 1.2)
        s.close()
        ok = True
    except Exception as exc:
        err = str(exc)[:80]
    ms = int((time.time() - t0) * 1000)
    row = {"ok": ok, "ms": ms if ok else None, "target": "1.1.1.1:443", "error": err}
    with _HEALTH_LOCK:
        _NET_SNAP["ts"] = now
        _NET_SNAP["data"] = row
    return dict(row)


def _desk_health():
    """Host / internet / HL IP tokens. No hunter writes. Net probe does not call HL."""
    cpu = _cpu_pct()
    mem_pct, mem_used, mem_total = _mem_pct()
    disk_pct, disk_used, disk_total = _disk_pct()
    load1 = None
    try:
        load1 = round(os.getloadavg()[0], 2)
    except Exception:
        pass
    return {
        "host": {
            "cpu_pct": cpu,
            "load1": load1,
            "mem_pct": mem_pct,
            "mem_used_gb": mem_used,
            "mem_total_gb": mem_total,
            "disk_pct": disk_pct,
            "disk_used_gb": disk_used,
            "disk_total_gb": disk_total,
        },
        "net": _net_probe(),
        "hlip": _hl_ip_snapshot(),
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


# Main copy-trader wallet (not the swing/helliot subaccounts).
COPY_WALLET         = wallet_for(
    "hummingbot-hl",
    os.environ.get("HL_COPY_WALLET", ""),
)

_CACHE = {}


def _ttl_get(key, ttl_sec, loader):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]) < ttl_sec:
        return hit[1]
    val = loader()
    _CACHE[key] = (now, val)
    return val


def _parse_yaml_trader_whys(text):
    """Comments immediately above each trader_addresses entry."""
    out = {}
    pending = []
    in_block = False
    for line in (text or "").splitlines():
        if re.match(r"^trader_addresses\s*:", line):
            in_block = True
            pending = []
            continue
        if not in_block:
            continue
        if re.match(r"^[A-Za-z_][\w]*\s*:", line):
            break
        stripped = line.strip()
        m = re.search(r"(0x[a-fA-F0-9]{40})", stripped)
        if m and not stripped.startswith("#"):
            why = " ".join(pending).strip()
            if why:
                out[m.group(1).lower()] = why
            pending = []
        elif stripped.startswith("#"):
            pending.append(stripped.lstrip("#").strip())
    return out


def load_follow_notes():
    data = _read_json_file(HL_FOLLOW_NOTES) or {}
    notes = data.get("notes") if isinstance(data.get("notes"), dict) else data
    if not isinstance(notes, dict):
        notes = {}
    return {str(k).lower(): v for k, v in notes.items() if k}


def save_follow_notes(notes):
    payload = {
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "chat",
        "notes": notes,
    }
    _write_json_file(HL_FOLLOW_NOTES, payload)
    return payload


def follow_why_for(addr, yaml_whys=None, notes=None):
    a = str(addr or "").lower()
    notes = notes if notes is not None else load_follow_notes()
    row = notes.get(a)
    if isinstance(row, dict) and (row.get("why") or row.get("title")):
        return {
            "title": row.get("title") or "Followed from chat",
            "why": row.get("why") or "",
            "asked": row.get("asked") or row.get("date") or "",
            "source": row.get("source") or "chat",
        }
    if isinstance(row, str) and row.strip():
        return {"title": "Followed from chat", "why": row.strip(), "asked": "", "source": "chat"}
    yaml_whys = yaml_whys if yaml_whys is not None else {}
    y = yaml_whys.get(a) or ""
    if y:
        return {"title": "From copy config", "why": y, "asked": "", "source": "yaml"}
    return {
        "title": "Copy roster",
        "why": "On the Copy trader list. No chat note stored yet.",
        "asked": "",
        "source": "roster",
    }


def load_hl_config():
    """Parse hl_copy_trader.yml without PyYAML."""
    try:
        with open(HL_COPY_CONF, "r") as f:
            text = f.read()
    except Exception:
        return {}
    addresses = re.findall(r'^\s*-\s*"(0x[0-9a-fA-F]+)"', text, re.MULTILINE)
    caps = {}
    for m in re.finditer(r'"(0x[0-9a-fA-F]+)"\s*:\s*(\d+)', text):
        caps[m.group(1)] = int(m.group(2))
    min_lev  = int(re.search(r'min_leverage\s*:\s*(\d+)', text).group(1)) if re.search(r'min_leverage\s*:\s*(\d+)', text) else 4
    max_lev  = int(re.search(r'max_leverage\s*:\s*(\d+)', text).group(1)) if re.search(r'max_leverage\s*:\s*(\d+)', text) else 10
    min_pos  = int(re.search(r'min_open_positions\s*:\s*(\d+)', text).group(1)) if re.search(r'min_open_positions\s*:\s*(\d+)', text) else 3
    mpp = re.search(r'max_positions_per_trader\s*:\s*(\d+)', text)
    max_pos_trader = int(mpp.group(1)) if mpp else min_pos
    alloc    = float(re.search(r'allocation_pct\s*:\s*([\d.]+)', text).group(1)) if re.search(r'allocation_pct\s*:\s*([\d.]+)', text) else 1.0
    # first allocation_pct is main; commodity line is named commodity_allocation_pct
    comm = re.search(r'commodity_allocation_pct\s*:\s*([\d.]+)', text)
    comm_pct = float(comm.group(1)) if comm else 0.0
    cs = re.search(r'^\s*copy_shorts\s*:\s*(true|false)', text, re.I | re.M)
    copy_shorts = (cs.group(1).lower() == "true") if cs else True

    def _float_yml(key, default):
        m = re.search(rf'^\s*{key}\s*:\s*([\d.]+)', text, re.M)
        return float(m.group(1)) if m else default

    def _bool_yml(key, default):
        m = re.search(rf'^\s*{key}\s*:\s*(true|false)', text, re.I | re.M)
        if not m:
            return default
        return m.group(1).lower() == "true"

    return {
        "trader_addresses": addresses,
        "trader_max_positions": caps,
        "min_leverage": min_lev,
        "max_leverage": max_lev,
        "min_open_positions": min_pos,
        "max_positions_per_trader": max_pos_trader,
        "allocation_pct": alloc,
        "commodity_allocation_pct": comm_pct,
        "copy_shorts": copy_shorts,
        "risk_management_enabled": _bool_yml("risk_management_enabled", True),
        "max_position_loss_pct": _float_yml("max_position_loss_pct", 0.333),
        "max_margin_per_position_pct": _float_yml("max_margin_per_position_pct", 0.333),
        "min_free_margin_pct": _float_yml("min_free_margin_pct", 0.15),
        "size_exclude_unrealized_profit": _bool_yml("size_exclude_unrealized_profit", True),
        "max_zec_short_usd": _float_yml("max_zec_short_usd", 0),
    }


def load_hl_runtime_flags():
    """Dashboard / bot shared runtime flags (longs-only, etc.)."""
    flags = {}
    try:
        with open(HL_COPY_RUNTIME, "r") as f:
            flags = json.load(f) or {}
    except Exception:
        flags = {}
    return flags


def _effective_max_zec_short_usd(cfg=None, bot_status=None):
    live = _ctrl_live_values("hummingbot-hl", bot_status)
    try:
        v = float(live.get("max_zec_short_usd"))
        if v >= 0:
            return v
    except (TypeError, ValueError):
        pass
    try:
        return max(0.0, float((cfg or {}).get("max_zec_short_usd") or 0))
    except (TypeError, ValueError):
        return 0.0


def effective_copy_shorts(cfg=None):
    """Runtime override > yml copy_shorts."""
    if cfg is None:
        cfg = load_hl_config()
    rt = load_hl_runtime_flags()
    if "copy_shorts" in rt:
        return bool(rt["copy_shorts"])
    if "longs_only" in rt:
        return not bool(rt["longs_only"])
    return bool(cfg.get("copy_shorts", True))


def effective_unfollowed_traders(cfg=None):
    """
    1-based trader numbers that we stop copy-trading (runtime flag).
    Existing mirrors stay until leader closes or manual close.
    """
    if cfg is None:
        cfg = load_hl_config()
    rt = load_hl_runtime_flags()
    n = len(cfg.get("trader_addresses") or [])
    out = set()
    raw = rt.get("unfollowed_traders")
    if raw is None:
        raw = rt.get("disabled_traders")
    if isinstance(raw, list):
        for x in raw:
            try:
                num = int(x)
                if 1 <= num <= max(n, 99):
                    out.add(num)
            except (TypeError, ValueError):
                continue
    raw_addrs = rt.get("unfollowed_addresses")
    if isinstance(raw_addrs, list):
        addrs = cfg.get("trader_addresses") or []
        for a in raw_addrs:
            try:
                al = str(a).strip().lower()
                for i, configured in enumerate(addrs, 1):
                    if str(configured).strip().lower() == al:
                        out.add(i)
                        break
            except Exception:
                pass
    return sorted(out)


def effective_trader_follow_slots(cfg=None):
    """T# → 1|2|3 trades to follow. Each open uses 1/N of that trader's pot."""
    if cfg is None:
        cfg = load_hl_config()
    rt = load_hl_runtime_flags()
    n = len(cfg.get("trader_addresses") or [])
    try:
        default = int(cfg.get("max_positions_per_trader") or cfg.get("min_open_positions") or 3)
    except (TypeError, ValueError):
        default = 3
    default = max(1, min(3, default))
    raw = rt.get("trader_follow_slots")
    if not isinstance(raw, dict):
        raw = {}
    yml_caps = cfg.get("trader_max_positions") or {}
    addrs = cfg.get("trader_addresses") or []
    out = {}
    for i in range(1, max(n, 1) + 1):
        v = default
        addr = addrs[i - 1] if 0 <= i - 1 < len(addrs) else None
        if addr and addr in yml_caps:
            try:
                v = int(yml_caps[addr])
            except (TypeError, ValueError):
                v = default
        if i in raw or str(i) in raw:
            try:
                v = int(raw.get(i, raw.get(str(i))))
            except (TypeError, ValueError):
                pass
        out[i] = max(1, min(3, v))
    return out


def save_hl_runtime_flags(updates: dict):
    """Merge keys into conf/hl_copy_runtime.json and write."""
    prev = load_hl_runtime_flags()
    prev.update(updates or {})
    prev["ts"] = datetime.utcnow().isoformat() + "Z"
    prev["source"] = "dashboard"
    with open(HL_COPY_RUNTIME, "w") as f:
        json.dump(prev, f, indent=2)
    for k, v in (updates or {}).items():
        if k in ("ts", "updated", "source"):
            continue
        _sql_kv_set("hummingbot-hl", k, v, "runtime")
    return prev


def load_hl_bot_status(max_age_sec=120):
    """
    Live mirrors written by hl_copy_trader (conf/hl_copy_status.json).
    Preferred over log parsing for Active positions.
    """
    try:
        with open(HL_COPY_STATUS, "r") as f:
            data = json.load(f) or {}
    except Exception:
        return None
    ts = data.get("ts")
    if ts:
        try:
            # accept Z or offset
            t = ts.replace("Z", "+00:00")
            from datetime import datetime as _dt
            written = _dt.fromisoformat(t)
            if written.tzinfo is None:
                from datetime import timezone as _tz
                written = written.replace(tzinfo=_tz.utc)
            from datetime import datetime as _dt2, timezone as _tz2
            age = (_dt2.now(_tz2.utc) - written).total_seconds()
            data["_age_sec"] = age
            data["_fresh"] = age <= max_age_sec
        except Exception:
            data["_fresh"] = True
            data["_age_sec"] = None
    else:
        data["_fresh"] = True
    return data


def merge_active_mirrors(log_mirrors, status):
    """Prefer bot status mirrors (even if stale) over log-parsed opens."""
    if status and isinstance(status.get("mirrors"), list):
        out = []
        for m in status["mirrors"]:
            out.append({
                "coin": m.get("coin"),
                "side": m.get("side"),
                "size": float(m.get("size") or 0),
                "entry_px": float(m.get("entry_px") or 0),
                "margin": float(m.get("margin") or 0),
                "leverage": int(m.get("leverage") or 0),
                "notional": float(m.get("notional") or 0),
                "open_time": m.get("open_time") or "",
                "source": m.get("source") or "live",
                "trader": m.get("trader"),
                "all_traders": bool(m.get("all_traders")),
                "full_allin": bool(m.get("full_allin")),
                "manual_close_only": bool(m.get("manual_close_only")),
                "add_eth": bool(m.get("add_eth")),
                "risk_auto_close_disabled": bool(m.get("risk_auto_close_disabled")),
                "pair": m.get("pair"),
            })
        return out
    return log_mirrors or []


def _parse_hl_positions(payload):
    result = []
    for entry in (payload or {}).get("assetPositions", []):
        pos = entry.get("position", {})
        coin = pos.get("coin")
        szi  = float(pos.get("szi", 0) or 0)
        if not coin or abs(szi) < 1e-9:
            continue
        lev_obj  = pos.get("leverage", {})
        leverage = int(lev_obj.get("value", 1)) if isinstance(lev_obj, dict) else 1
        result.append({
            "coin":          coin,
            "side":          "LONG" if szi > 0 else "SHORT",
            "size":          abs(szi),
            "entry_px":      float(pos.get("entryPx") or 0),
            "leverage":      leverage,
            "unrealized_pnl": float(pos.get("unrealizedPnl", 0) or 0),
            "margin_used":   float(pos.get("marginUsed", 0) or 0),
        })
    return result


def _merge_hl_positions(base, extra):
    """Net same-coin books from a master + its HL subaccounts."""
    by = {}
    for p in list(base or []) + list(extra or []):
        if not isinstance(p, dict) or not p.get("coin"):
            continue
        coin = p["coin"]
        side = str(p.get("side") or "LONG").upper()
        sz = float(p.get("size") or 0)
        signed = sz if side == "LONG" else -sz
        if coin not in by:
            by[coin] = dict(p)
            by[coin]["_signed"] = signed
            continue
        cur = by[coin]
        prev = float(cur.get("_signed") or 0)
        net = prev + signed
        tot = abs(prev) + abs(signed)
        if tot > 0:
            cur["entry_px"] = (
                float(cur.get("entry_px") or 0) * abs(prev)
                + float(p.get("entry_px") or 0) * abs(signed)
            ) / tot
        cur["_signed"] = net
        cur["unrealized_pnl"] = float(cur.get("unrealized_pnl") or 0) + float(p.get("unrealized_pnl") or 0)
        cur["margin_used"] = float(cur.get("margin_used") or 0) + float(p.get("margin_used") or 0)
        cur["leverage"] = max(int(cur.get("leverage") or 1), int(p.get("leverage") or 1))
    out = []
    for coin, p in by.items():
        net = float(p.pop("_signed", 0) or 0)
        if abs(net) < 1e-9:
            continue
        p["coin"] = coin
        p["side"] = "LONG" if net > 0 else "SHORT"
        p["size"] = abs(net)
        out.append(p)
    return out


def fetch_hl_subaccounts(address):
    """HL subaccounts of a master (nested clearinghouse when the API sends it)."""
    if not address:
        return []

    def _load():
        try:
            rows = _hl_info_post({"type": "subAccounts", "user": address}, timeout=8)
        except Exception:
            return []
        if not isinstance(rows, list):
            return []
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            u = str(row.get("subAccountUser") or "").strip()
            if not u:
                continue
            out.append({
                "address": u,
                "name": row.get("name"),
                "positions": _parse_hl_positions(row.get("clearinghouseState") or {}),
            })
        return out

    return _ttl_get(f"subs:{str(address).lower()}", 60, _load) or []


def fetch_hl_account(address):
    """Live equity / free / positions for a HL address."""
    if not address:
        return None

    def _load():
        try:
            data = _hl_info_post({"type": "clearinghouseState", "user": address}, timeout=5) or {}
            cms = data.get("crossMarginSummary") or {}
            equity = cms.get("accountValue")
            free = data.get("withdrawable")
            return {
                "equity": float(equity) if equity is not None else None,
                "free": float(free) if free is not None else None,
                "positions": _parse_hl_positions(data),
            }
        except Exception:
            return None

    return _ttl_get(f"acct:{str(address).lower()}", 20, _load)


def fetch_hl_positions(address):
    """Open positions for a leader, including Hyperliquid subaccounts."""
    acct = fetch_hl_account(address)
    if acct is None:
        return None
    pos = list(acct.get("positions") or [])
    for sub in fetch_hl_subaccounts(address) or []:
        pos = _merge_hl_positions(pos, sub.get("positions") or [])
    return pos


def fetch_hl_spot_detail(address):
    """Spot USDC total / hold / leftover on a HL address."""
    if not address:
        return None

    def _load():
        try:
            data = _hl_info_post({"type": "spotClearinghouseState", "user": address}, timeout=5) or {}
            for bal in data.get("balances") or []:
                if str(bal.get("coin") or "").upper() != "USDC":
                    continue
                total = float(bal.get("total") or 0)
                hold = float(bal.get("hold") or 0)
                return {
                    "total": total,
                    "hold": hold,
                    "free": max(total - hold, 0.0),
                }
            return {"total": 0.0, "hold": 0.0, "free": 0.0}
        except Exception:
            return None

    return _ttl_get(f"spotd:{str(address).lower()}", 20, _load)


def fetch_hl_spot_usdc(address):
    """Spot USDC on a HL address (not perp margin)."""
    detail = fetch_hl_spot_detail(address)
    if not detail:
        return None
    return float(detail.get("total") or 0)


def fetch_all_mids():
    """Return {coin: mid_price} from HL."""
    def _load():
        try:
            data = _hl_info_post({"type": "allMids"}, timeout=5) or {}
            return {k: float(v) for k, v in data.items() if v}
        except Exception:
            return {}
    return _ttl_get("allMids", 20, _load)


def overlay_live_mirrors(mirrors, live_positions):
    """Replace stale/log mirrors with live HL positions; keep trader attribution."""
    if not live_positions:
        return mirrors or []
    by_coin = {(m.get("coin") or ""): m for m in (mirrors or []) if m.get("coin")}
    out = []
    for p in live_positions:
        coin = p.get("coin")
        if not coin:
            continue
        base = dict(by_coin.get(coin) or {})
        size = float(p.get("size") or 0)
        entry = float(p.get("entry_px") or 0)
        lev = int(p.get("leverage") or base.get("leverage") or 0)
        mar = float(p.get("margin_used") or 0) or float(base.get("margin") or 0)
        base.update({
            "coin": coin,
            "side": p.get("side") or base.get("side"),
            "size": size,
            "entry_px": entry or float(base.get("entry_px") or 0),
            "leverage": lev,
            "margin": round(mar, 2) if mar else base.get("margin"),
            "notional": round(size * (entry or float(base.get("entry_px") or 0)), 2),
            "source": base.get("source") or "live",
            "pair": base.get("pair") or f"{coin}-USD",
        })
        if p.get("unrealized_pnl") is not None:
            base["pnl_usd"] = round(float(p["unrealized_pnl"]), 2)
        out.append(base)
    return out


def read_copy_trades_text():
    try:
        with open(COPY_TRADES_LOG, "r", errors="replace") as f:
            return strip_ansi(f.read())
    except Exception:
        return ""


def session_copy_trades_text(raw=None):
    """Only the latest bot session (after last start banner) — avoids ghost opens from old runs."""
    if raw is None:
        raw = read_copy_trades_text()
    if not raw:
        return ""
    # Match both dash styles used in banners
    parts = re.split(r'HL COPY TRADER\s+[—\-]+\s+started', raw)
    return parts[-1] if len(parts) > 1 else raw


_TRADER_ACT_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+TRADER #(\d+)\s+(?:OPENED|CLOSED|FLIP)\b'
)


def last_trader_activity():
    """Latest TRADER #N OPENED/CLOSED/FLIP timestamp in this copy session."""
    text = session_copy_trades_text()
    if not text:
        text = read_copy_trades_text()[-250000:]
    last = {}
    for m in _TRADER_ACT_RE.finditer(text or ""):
        last[int(m.group(2))] = m.group(1)
    return last


def get_open_mirrors():
    """
    Return WE COPY / restore positions not yet closed in the current session.
    Includes trader number (T#) when we can attribute the open to a leader
    from nearby TRADER #N OPENED / FLIP lines.
    """
    open_pos = {}
    # Last leader who opened/flipped each coin (for attributing WE COPY)
    last_leader_for_coin = {}

    open_pat = re.compile(
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+WE COPY\s+OPENED\s+(LONG|SHORT)\s+(\S+)'
        r'\s+([\d.]+)\s+\$\s*([\d,]+\.?\d*)\s+margin=\$([\d.,]+)\s+(\d+)x\s+notional=\$([\d,]+\.?\d*)'
    )
    # Full-scale add: size/margin deltas + optional total= / total_margin=
    scale_pat = re.compile(
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+WE COPY\s+SCALED\s+(LONG|SHORT)\s+(\S+)'
        r'\s+([\d.]+)\s+\$\s*([\d,]+\.?\d*)\s+margin=\$([\d.,]+)\s+(\d+)x\s+notional=\$([\d,]+\.?\d*)'
        r'(?:\s+total=([\d.]+))?(?:\s+total_margin=\$([\d.,]+))?'
    )
    restore_pat = re.compile(
        r'\[restore\]\s+Existing\s+(LONG|SHORT)\s+([\d.]+)\s+(\S+)\s+@\s+\$\s*([\d,]+\.?\d*)'
    )
    close_pat = re.compile(
        r'(?:WE CLOSE|ORPHAN)\s+(?:CLOSED|CLOSE)\s+(?:LONG|SHORT)\s+(\S+)'
    )
    # TRADER #N OPENED / CLOSED / FLIP … COIN
    trader_open_pat = re.compile(
        r'TRADER #(\d+)\s+OPENED\s+(LONG|SHORT)\s+(\S+)'
    )
    trader_flip_pat = re.compile(
        r'TRADER #(\d+)\s+FLIP\s+\S+\s+(\S+)'
    )
    trader_close_pat = re.compile(
        r'TRADER #(\d+)\s+CLOSED\s+(LONG|SHORT)\s+(\S+)'
    )
    # Dashboard manual actions include T#N on the same line
    dash_follow_pat = re.compile(
        r'DASHBOARD\s+FOLLOW\s+(LONG|SHORT)\s+(\S+)\s+T#(\d+)',
        re.I,
    )
    dash_scale_pat = re.compile(
        r'DASHBOARD\s+SCALE\s+(LONG|SHORT)\s+(\S+)\s+T#(\d+)',
        re.I,
    )
    # Historical ownership from prior sessions (full log, not only current session)
    # Used as fallback for restored mirrors after bot restart.
    hist_trader_for_coin = {}
    hist_running = {}  # coin → last T# from sequential scan

    full_text = read_copy_trades_text() or ""
    for line in full_text.splitlines():
        line = strip_ansi(line).strip()
        if not line:
            continue
        m = trader_open_pat.search(line)
        if m:
            hist_running[m.group(3)] = int(m.group(1))
            hist_trader_for_coin[m.group(3)] = int(m.group(1))
            continue
        m = trader_flip_pat.search(line)
        if m:
            hist_running[m.group(2)] = int(m.group(1))
            hist_trader_for_coin[m.group(2)] = int(m.group(1))
            continue
        m = dash_follow_pat.search(line)
        if m:
            hist_running[m.group(2)] = int(m.group(3))
            hist_trader_for_coin[m.group(2)] = int(m.group(3))
            continue
        m = dash_scale_pat.search(line)
        if m:
            hist_running[m.group(2)] = int(m.group(3))
            hist_trader_for_coin[m.group(2)] = int(m.group(3))
            continue
        m = open_pat.search(line)
        if m:
            coin = m.group(3)
            if coin in hist_running:
                hist_trader_for_coin[coin] = hist_running[coin]
            continue
        m = scale_pat.search(line)
        if m:
            coin = m.group(3)
            if coin in hist_running:
                hist_trader_for_coin[coin] = hist_running[coin]
            continue

    text = session_copy_trades_text(full_text)
    for line in text.splitlines():
        line = strip_ansi(line).strip()
        if not line:
            continue

        m = trader_open_pat.search(line)
        if m:
            last_leader_for_coin[m.group(3)] = int(m.group(1))
            continue
        m = trader_flip_pat.search(line)
        if m:
            last_leader_for_coin[m.group(2)] = int(m.group(1))
            continue
        m = trader_close_pat.search(line)
        if m:
            # leader closed — clear attribution if we never copied
            coin = m.group(3)
            if coin not in open_pos:
                last_leader_for_coin.pop(coin, None)
            continue
        m = dash_follow_pat.search(line)
        if m:
            last_leader_for_coin[m.group(2)] = int(m.group(3))
            continue
        m = dash_scale_pat.search(line)
        if m:
            last_leader_for_coin[m.group(2)] = int(m.group(3))
            # If already open (restore), stamp trader now
            coin = m.group(2)
            if coin in open_pos and open_pos[coin].get("trader") is None:
                open_pos[coin]["trader"] = int(m.group(3))
            continue

        m = open_pat.search(line)
        if m:
            t, side, coin, size, entry_px, margin, leverage, notional = m.groups()
            trader = last_leader_for_coin.get(coin) or hist_trader_for_coin.get(coin)
            open_pos[coin] = {
                "open_time": t, "side": side, "coin": coin,
                "size": float(size),
                "entry_px": float(entry_px.replace(",", "")),
                "margin":   float(margin.replace(",", "")),
                "leverage": int(leverage),
                "notional": float(notional.replace(",", "")),
                "source": "copy",
                "trader": trader,  # T# or None
            }
            continue
        m = scale_pat.search(line)
        if m:
            t, side, coin, add_size, entry_px, add_margin, leverage, notional, total, total_margin = m.groups()
            trader = last_leader_for_coin.get(coin) or hist_trader_for_coin.get(coin)
            prev = open_pos.get(coin) or {
                "open_time": t, "side": side, "coin": coin,
                "size": 0.0, "entry_px": float(entry_px.replace(",", "")),
                "margin": 0.0, "leverage": int(leverage), "notional": 0.0,
                "source": "copy", "trader": trader,
            }
            add_s = float(add_size)
            add_m = float(add_margin.replace(",", ""))
            if total is not None:
                prev["size"] = float(total)
            else:
                prev["size"] = float(prev.get("size") or 0) + add_s
            if total_margin is not None:
                prev["margin"] = float(total_margin.replace(",", ""))
            else:
                prev["margin"] = float(prev.get("margin") or 0) + add_m
            prev["side"] = side
            prev["leverage"] = int(leverage)
            prev["notional"] = float(prev.get("margin") or 0) * int(leverage)
            prev["source"] = "copy"
            # Keep original open_time; mark scaled
            prev["scaled"] = True
            if prev.get("trader") is None:
                prev["trader"] = trader
            open_pos[coin] = prev
            continue
        m = restore_pat.search(line)
        if m:
            side, size, coin, entry_px = m.groups()
            # Don't overwrite a real WE COPY open for same coin
            if coin not in open_pos:
                trader = last_leader_for_coin.get(coin) or hist_trader_for_coin.get(coin)
                open_pos[coin] = {
                    "open_time": "restored", "side": side, "coin": coin,
                    "size": float(size),
                    "entry_px": float(entry_px.replace(",", "")),
                    "margin": 0.0,
                    "leverage": 0,
                    "notional": 0.0,
                    "source": "restore",
                    "trader": trader,
                }
            continue
        m = close_pat.search(line)
        if m:
            coin = m.group(1)
            open_pos.pop(coin, None)
            # keep last_leader so a re-open same session can still attribute
    return list(open_pos.values())


# TRADER #N OPENED LONG/SHORT COIN size $price ...
_TRADER_OPEN_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+TRADER #(\d+)\s+OPENED\s+(LONG|SHORT)\s+(\S+)'
    r'(?:\s+([\d.]+)\s+\$\s*([\d,]+\.?\d*))?'
)
_WE_COPY_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)?\s*WE COPY\s+OPENED\s+(LONG|SHORT)\s+(\S+)'
)
_TRADER_CLOSE_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+TRADER #(\d+)\s+CLOSED\s+(LONG|SHORT)\s+(\S+)'
)
_TRADER_FLIP_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+TRADER #(\d+)\s+FLIP\s+\S+\s+(\S+)'
)


def parse_leader_open_events(limit=80):
    """
    All leader OPEN / FLIP events this session with open time, whether we copied,
    and our copy time if any. Newest first — for manual miss-checks.
    Always includes trader number (T#).
    """
    text = session_copy_trades_text() or read_copy_trades_text()[-300000:]
    lines = [strip_ansi(l).strip() for l in text.splitlines() if l.strip()]
    events = []

    # FLIP line: TRADER #N FLIP LONG→SHORT COIN size $price
    flip_full_re = re.compile(
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+TRADER #(\d+)\s+FLIP\s+'
        r'(?:LONG|SHORT)→(LONG|SHORT)\s+(\S+)'
        r'(?:\s+([\d.]+)\s+\$\s*([\d,]+\.?\d*))?'
    )

    def _scan_followup(i, coin, t):
        followed = False
        our_time = None
        status = "MISS"
        detail = "no WE COPY after leader open"
        for j in range(i + 1, min(i + 12, len(lines))):
            nxt = lines[j]
            if j > i and (_TRADER_OPEN_RE.search(nxt) or flip_full_re.search(nxt)):
                break
            mc = _WE_COPY_RE.search(nxt)
            if mc and mc.group(3) == coin:
                followed = True
                our_time = mc.group(1) or t
                status = "COPIED"
                detail = "followed"
                break
            if re.search(r'\b(SKIP|FAILED|DEFER|FLIP→OPEN|LOCK)\b', nxt):
                if "SKIP" in nxt or "LOCK" in nxt:
                    status = "SKIP"
                elif "FAILED" in nxt:
                    status = "FAILED"
                elif "DEFER" in nxt:
                    status = "DEFER"
                elif "FLIP→OPEN" in nxt:
                    # still waiting for WE COPY on following lines
                    detail = "flip→open attempted"
                    continue
                note_m = re.search(
                    r'(unsupported|no balance|cap|already mirrored|subscribe|not ready|'
                    r'skipped|error|trader cap|asset locked|LOCK).*',
                    nxt, re.I
                )
                detail = (note_m.group(0) if note_m else nxt[-140:])[:160]
                continue
        return followed, our_time, status, detail

    for i, line in enumerate(lines):
        m = _TRADER_OPEN_RE.search(line)
        if m:
            t, num, side, coin = m.group(1), int(m.group(2)), m.group(3), m.group(4)
            size = float(m.group(5)) if m.group(5) else None
            entry = float(m.group(6).replace(",", "")) if m.group(6) else None
            followed, our_time, status, detail = _scan_followup(i, coin, t)
            events.append({
                "time": t,
                "trader": num,
                "side": side,
                "coin": coin,
                "size": size,
                "entry_px": entry,
                "followed": followed,
                "status": status,
                "our_time": our_time,
                "detail": detail,
                "event": "OPEN",
            })
            continue

        m = flip_full_re.search(line)
        if m:
            t, num, side, coin = m.group(1), int(m.group(2)), m.group(3), m.group(4)
            size = float(m.group(5)) if m.group(5) else None
            entry = float(m.group(6).replace(",", "")) if m.group(6) else None
            followed, our_time, status, detail = _scan_followup(i, coin, t)
            events.append({
                "time": t,
                "trader": num,
                "side": side,
                "coin": coin,
                "size": size,
                "entry_px": entry,
                "followed": followed,
                "status": status,
                "our_time": our_time,
                "detail": detail if detail != "no WE COPY after leader open" else "no WE COPY after flip",
                "event": "FLIP",
            })

    return list(reversed(events))[-limit:][::-1]  # newest first


def last_leader_open_times():
    """
    Map (trader_num, coin, side) -> open time UTC from this session's trade log.
    Only tracks opens the bot saw live (TRADER #N OPENED). No historical backfill.
    """
    text = session_copy_trades_text() or ""
    last_open = {}
    for line in text.splitlines():
        line = strip_ansi(line).strip()
        if not line:
            continue
        m = _TRADER_OPEN_RE.search(line)
        if m:
            t, num, side, coin = m.group(1), int(m.group(2)), m.group(3), m.group(4)
            last_open[(num, coin, side)] = t
            continue
        m = _TRADER_CLOSE_RE.search(line)
        if m:
            _t, num, side, coin = m.group(1), int(m.group(2)), m.group(3), m.group(4)
            last_open.pop((num, coin, side), None)
            continue
        m = _TRADER_FLIP_RE.search(line)
        if m:
            _t, num, coin = m.group(1), int(m.group(2)), m.group(3)
            for side in ("LONG", "SHORT"):
                last_open.pop((num, coin, side), None)
    return last_open


def parse_follow_failures(limit=40):
    """
    Detect times we saw a leader open but did not (or could not) follow.
    Returns newest-first list of failure events (with open time).
    """
    # Reuse full open-event parse — only non-copied
    events = parse_leader_open_events(limit=120)
    failures = []
    for e in events:
        if e.get("followed"):
            continue
        failures.append({
            "time": e["time"],
            "kind": e.get("status") or "MISS",
            "side": e["side"],
            "coin": e["coin"],
            "size": e.get("size"),
            "entry_px": e.get("entry_px"),
            "detail": e.get("detail") or "",
            "trader": e.get("trader"),
            "our_time": e.get("our_time"),
        })

    # Orphans still useful
    text = session_copy_trades_text() or read_copy_trades_text()[-200000:]
    orphan_pat = re.compile(
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+ORPHAN\s+CLOSE\s+(LONG|SHORT)\s+(\S+)\s+(.*)$'
    )
    for line in text.splitlines():
        m = orphan_pat.search(strip_ansi(line))
        if m:
            t, side, coin, note = m.groups()
            failures.append({
                "time": t, "kind": "ORPHAN", "side": side, "coin": coin,
                "size": None, "entry_px": None,
                "detail": note.strip()[:160], "trader": None, "our_time": None,
            })

    # sort by time string desc (UTC format sorts lexicographically)
    failures.sort(key=lambda x: x.get("time") or "", reverse=True)
    return failures[:limit]

_COPY_ERR_NOISE = (
    "Unexpected error occurred subscribing",
    "Traceback (most recent call last)",
    "Failed to remove ",
    "is not being tracked",
    "Failed to unsubscribe",
    "Cannot unsubscribe from",
    "Cannot write to clo",
    "websocket connection was closed",
    '[stall] connector ready again',
    '[stall] connector not ready',
    "is not ready. Please wait",
    "connector=NOT READY",
    'File "',
)


def parse_strategy_errors(limit=30):
    """Recent real ERROR / stall / orphan lines. Drops WS reconnect noise."""
    logs = get_logs("hummingbot-hl", 400)
    out = []
    for line in logs:
        if "EVENT_LOG" in line or "DOCTYPE" in line:
            continue
        if any(n in line for n in _COPY_ERR_NOISE):
            continue
        if line.strip().startswith("File ") or (line.startswith("  ") and "Error" not in line):
            continue
        if "ERROR" in line or "Exception" in line:
            short = re.sub(r'^.*? - (INFO|WARNING|ERROR) - ', r'\1: ', line)
            if len(short) > 280:
                short = short[:280] + "…"
            out.append(short)
        elif "WARNING" in line and (
            "[orphan] CLOSE" in line
            or "FORCE" in line
        ):
            short = re.sub(r'^.*? - (INFO|WARNING|ERROR) - ', r'\1: ', line)
            out.append(short[:280])
    return out[-limit:]


def parse_heartbeat_snapshot(logs=None):
    """Pull latest heartbeat fields from strategy log."""
    if logs is None:
        logs = get_logs("hummingbot-hl", 80)
    for line in reversed(logs):
        if "[heartbeat]" not in line:
            continue
        # New: equity: $98.xx  free: $63.xx
        # Old: balance: $63.xx  (was free/withdrawable only)
        conn = re.search(r'connector=(\S+)', line)
        mir = re.search(r'mirrors:\s*(\d+)\s*crypto\s+(\d+)\s*commodity', line)
        mkts = re.search(r'markets:\s*(\d+)', line)
        equity = re.search(r'equity:\s*\$([\d.]+)', line)
        free = re.search(r'free:\s*\$([\d.]+)', line)
        # legacy single field
        bal = re.search(r'balance:\s*\$([\d.]+)', line)
        free_v = float(free.group(1)) if free else (float(bal.group(1)) if bal else None)
        equity_v = float(equity.group(1)) if equity else free_v
        return {
            "connector": conn.group(1) if conn else "?",
            "mirrors_crypto": int(mir.group(1)) if mir else None,
            "mirrors_commodity": int(mir.group(2)) if mir else None,
            "markets": int(mkts.group(1)) if mkts else None,
            "equity": equity_v,
            "free": free_v,
            # keep "balance" as equity for main KPI (what HL UI shows as total)
            "balance": equity_v,
            "raw": re.sub(r'^.*?\[heartbeat\]\s*', '', line)[:240],
        }
    if any("is not ready" in (line or "") for line in (logs or [])[-8:]):
        return {"connector": "NOT READY"}
    return {}


NETWORK_LOSS_FILE = os.path.join(LOGS_DIR, "network_loss.json")

# Hummingbot log timestamps: 2026-08-01 21:06:48,286
_LOG_TS_RE = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')


def _load_network_loss_store():
    try:
        with open(NETWORK_LOSS_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("events", [])
            data.setdefault("loss_count", 0)
            return data
    except Exception:
        pass
    return {"events": [], "loss_count": 0, "last_down": None, "last_up": None, "status": "?"}


def _save_network_loss_store(store):
    try:
        # keep last 100 events only
        store["events"] = (store.get("events") or [])[-100:]
        with open(NETWORK_LOSS_FILE, "w") as f:
            json.dump(store, f, indent=2)
    except Exception:
        pass


def _network_outage_count(events):
    """
    Count distinct outages: a DOWN starts an outage; further DOWNs within 3 min
    of the last event in the same outage do not increment the counter.
    """
    from datetime import datetime as _dt
    count = 0
    last_down_t = None
    for e in events:
        if e.get("kind") != "DOWN":
            continue
        try:
            t = _dt.strptime(e["ts"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            count += 1
            continue
        if last_down_t is None or (t - last_down_t).total_seconds() > 180:
            count += 1
        last_down_t = t
    return count


def parse_network_status(logs=None):
    """
    Detect network loss / recovery from HL strategy log and record transitions.
    Returns status for the matrix KPI box.

    Only true link drops count as losses:
      - NetworkStatus.NOT_CONNECTED / Stopping networking
      - Max retries / NewConnectionError (API unreachable)
    Post-reconnect 'stall not ready' is recovery re-init, not a new loss.
    """
    if logs is None:
        logs = get_logs("hummingbot-hl", 500)

    store = _load_network_loss_store()
    known = {(e.get("ts"), e.get("kind"), e.get("detail")) for e in store.get("events") or []}

    # Chronological scan — only transitions UP↔DOWN
    state = store.get("status") or "?"  # UP / DOWN / ?
    new_events = []

    for line in logs:
        ts_m = _LOG_TS_RE.search(line)
        ts = ts_m.group(1) if ts_m else None
        if not ts:
            continue

        kind = None
        detail = None

        # Real disconnects
        if "NetworkStatus.NOT_CONNECTED" in line or "Stopping networking" in line:
            kind, detail = "DOWN", "connector NOT_CONNECTED"
        elif "Max retries exceeded" in line or "NewConnectionError" in line:
            kind, detail = "DOWN", "API unreachable (connection error)"
        # Recovery
        elif "NetworkStatus.CONNECTED" in line or "Starting networking" in line:
            kind, detail = "UP", "connector CONNECTED"
        elif "[stall] connector ready again" in line:
            kind, detail = "UP", re.sub(r'^.*?\[stall\]\s*', '', line)[:120]
        # "stall not ready" / "Could not fetch account updates" are noisy mid-blip
        # signals — do not start a new outage; only keep state if already DOWN.

        if not kind:
            continue
        if (ts, kind, detail) in known:
            if kind in ("UP", "DOWN"):
                state = kind
            continue

        # Only record transitions
        if kind == "DOWN" and state == "DOWN":
            continue
        if kind == "UP" and state == "UP":
            continue

        ev = {"ts": ts, "kind": kind, "detail": detail or kind}
        new_events.append(ev)
        known.add((ts, kind, detail))
        state = kind
        if kind == "DOWN":
            store["last_down"] = ts
        elif kind == "UP":
            store["last_up"] = ts

    if new_events:
        store["events"] = (store.get("events") or []) + new_events

    # Always recompute loss_count from clustered outages (fixes inflated history)
    all_ev = sorted(store.get("events") or [], key=lambda e: e.get("ts") or "")
    store["events"] = all_ev
    store["loss_count"] = _network_outage_count(all_ev)
    store["status"] = state if state != "?" else store.get("status", "?")
    if new_events or True:
        # keep last_down/up consistent with events
        downs = [e for e in all_ev if e.get("kind") == "DOWN"]
        ups = [e for e in all_ev if e.get("kind") == "UP"]
        if downs:
            store["last_down"] = downs[-1]["ts"]
        if ups:
            store["last_up"] = ups[-1]["ts"]
        _save_network_loss_store(store)

    hb_ready = any("[heartbeat]" in l and "connector=ready" in l for l in logs[-30:])
    if store.get("status") == "DOWN" and not hb_ready:
        live = "DOWN"
    elif hb_ready or store.get("status") == "UP":
        live = "UP"
    elif store.get("status") == "DOWN":
        live = "DOWN"
    else:
        live = store.get("status") or "?"

    events = list(reversed(store.get("events") or []))[:12]

    return {
        "status": live,
        "loss_count": int(store.get("loss_count") or 0),
        "last_down": store.get("last_down"),
        "last_up": store.get("last_up"),
        "events": events,
    }

def parse_trader_history_stats(addresses):
    """
    Parse copy_trades.log TRADER #N lines to count per-trader opens, closes,
    and build a per-coin PnL summary from WE COPY / WE CLOSE lines.
    Also matches WE COPY opens to trader numbers via proximity in the log.
    """
    # Per-trader counts from TRADER #N lines
    trader_opens  = {i + 1: 0 for i in range(len(addresses))}
    trader_closes = {i + 1: 0 for i in range(len(addresses))}

    trader_open_pat  = re.compile(r'TRADER #(\d+)\s+OPENED')
    trader_close_pat = re.compile(r'TRADER #(\d+)\s+CLOSED')

    try:
        with open(COPY_TRADES_LOG, "r", errors="replace") as f:
            raw = f.read()
    except Exception:
        return {i + 1: {"opens": 0, "closes": 0} for i in range(len(addresses))}

    for line in raw.splitlines():
        line = strip_ansi(line).strip()
        m = trader_open_pat.search(line)
        if m:
            n = int(m.group(1))
            if n in trader_opens:
                trader_opens[n] += 1
        m = trader_close_pat.search(line)
        if m:
            n = int(m.group(1))
            if n in trader_closes:
                trader_closes[n] += 1

    return {
        n: {"opens": trader_opens[n], "closes": trader_closes[n]}
        for n in trader_opens
    }


def parse_copy_trades():
    """Parse copy_trades.log and return list of completed trades with PnL + trader #."""
    trades = []
    open_positions = {}   # coin -> trade dict (pending close)
    last_leader_for_coin = {}  # coin -> trader number
    pending_sent = {}  # coin -> SENT close (exit px + PnL); CLOSED confirms fill

    open_pat = re.compile(
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+WE COPY\s+OPENED\s+(LONG|SHORT)\s+(\S+)'
        r'\s+([\d.]+)\s+\$\s*([\d,]+\.?\d*)\s+margin=\$([\d.,]+)\s+(\d+)x\s+notional=\$([\d,]+\.?\d*)'
    )
    # SENT has live mid + PnL; CLOSED is fill confirm and often omits PnL
    # (ghost-clear logs entry as $ and "filled on-chain").
    sent_pat = re.compile(
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+WE CLOSE\s+SENT\s+(LONG|SHORT)\s+(\S+)'
        r'\s+([\d.]+)\s+\$\s*([\d,]+\.?\d*)\s+entry=\$\s*([\d,]+\.?\d*)'
        r'(?:.*?PnL~([+-][\d.]+)%)?'
    )
    close_pat = re.compile(
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+WE CLOSE\s+CLOSED\s+(LONG|SHORT)\s+(\S+)'
        r'\s+([\d.]+)\s+\$\s*([\d,]+\.?\d*)\s+entry=\$\s*([\d,]+\.?\d*)'
        r'(?:.*?PnL~([+-][\d.]+)%)?'
    )
    trader_open_pat = re.compile(
        r'TRADER #(\d+)\s+OPENED\s+(LONG|SHORT)\s+(\S+)'
    )
    trader_flip_pat = re.compile(
        r'TRADER #(\d+)\s+FLIP\s+\S+\s+(\S+)'
    )
    dash_follow_pat = re.compile(
        r'DASHBOARD\s+FOLLOW\s+(LONG|SHORT)\s+(\S+)\s+T#(\d+)',
        re.I,
    )
    full_btc_pat = re.compile(
        r'DASHBOARD\s+FULL\s+(BTC|ETH|[A-Z0-9]+)\s+LONG\s+\1',
        re.I,
    )

    try:
        with open(COPY_TRADES_LOG, "r", errors="replace") as f:
            raw = f.read()
    except Exception:
        return []

    lines = raw.splitlines()
    # Only count trades after the last TRADE HISTORY RESET marker
    start_i = 0
    for i, line in enumerate(lines):
        if "TRADE HISTORY RESET" in line:
            start_i = i + 1

    for line in lines[start_i:]:
        line = strip_ansi(line).strip()
        if not line:
            continue

        m = trader_open_pat.search(line)
        if m:
            last_leader_for_coin[m.group(3)] = int(m.group(1))
            continue
        m = trader_flip_pat.search(line)
        if m:
            last_leader_for_coin[m.group(2)] = int(m.group(1))
            continue
        m = dash_follow_pat.search(line)
        if m:
            last_leader_for_coin[m.group(2)] = int(m.group(3))
            continue
        m = full_btc_pat.search(line)
        if m:
            last_leader_for_coin[m.group(1).upper()] = "ALL"
            continue

        m = open_pat.search(line)
        if m:
            t, side, coin, size, entry_px, margin, leverage, notional = m.groups()
            trader = last_leader_for_coin.get(coin)
            open_positions[coin] = {
                "open_time": t,
                "side": side,
                "coin": coin,
                "size": float(size),
                "entry_px": float(entry_px.replace(",", "")),
                "margin": float(margin.replace(",", "")),
                "leverage": int(leverage),
                "notional": float(notional.replace(",", "")),
                "trader": trader,
            }
            continue

        m = sent_pat.search(line)
        if m:
            t, side, coin, size, exit_px, entry_px2, pnl_pct = m.groups()
            pending_sent[coin] = {
                "t": t,
                "side": side,
                "size": float(size),
                "exit_px": float(exit_px.replace(",", "")),
                "entry_px": float(entry_px2.replace(",", "")),
                "pnl_pct": float(pnl_pct) if pnl_pct is not None else None,
            }
            continue

        m = close_pat.search(line)
        if m:
            t, side, coin, size, exit_px, entry_px2, pnl_pct = m.groups()
            sent = pending_sent.pop(coin, None)
            if sent and sent.get("exit_px"):
                exit_px_f = float(sent["exit_px"])
            else:
                exit_px_f = float(exit_px.replace(",", ""))
            if pnl_pct is not None:
                pnl_pct_f = float(pnl_pct)
            elif sent and sent.get("pnl_pct") is not None:
                pnl_pct_f = float(sent["pnl_pct"])
            else:
                pnl_pct_f = None
            pos = open_positions.pop(coin, None)
            # DASH storm: hundreds of WE CLOSE CLOSED with no matching open,
            # reconstructed as 1x / $0 PnL. Skip those duplicates.
            if pos is None and trades:
                last = trades[-1]
                try:
                    same = (
                        last.get("coin") == coin
                        and last.get("side") == side
                        and abs(float(last.get("entry_px") or 0) - float(str(entry_px2).replace(",", ""))) < 1e-6
                    )
                    last_t = str(last.get("close_time") or "")
                    close_again = False
                    if same and last_t and t:
                        a = datetime.strptime(last_t.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S")
                        b = datetime.strptime(t.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S")
                        close_again = abs((b - a).total_seconds()) < 180
                    if same and (close_again or float(last.get("margin") or 0) == 0):
                        continue
                except Exception:
                    pass
            if pos is None:
                # Reconstructed from log only — use close line data
                pos = {
                    "open_time": "?",
                    "side": side,
                    "coin": coin,
                    "size": float(size),
                    "entry_px": float(entry_px2.replace(",", "")),
                    "margin": 0.0,
                    "leverage": 1,
                    "notional": 0.0,
                    "trader": last_leader_for_coin.get(coin),
                }
            if pnl_pct_f is None:
                entry = float(pos.get("entry_px") or 0)
                lev = float(pos.get("leverage") or 1) or 1
                if entry > 0 and exit_px_f > 0:
                    if side == "LONG":
                        pnl_pct_f = (exit_px_f - entry) / entry * 100.0 * lev
                    else:
                        pnl_pct_f = (entry - exit_px_f) / entry * 100.0 * lev
                else:
                    pnl_pct_f = 0.0
            # Dollar PnL = pnl_pct% of margin (pnl_pct already leverage-multiplied)
            pnl_usd = pnl_pct_f / 100.0 * pos["margin"] if pos["margin"] > 0 else 0.0
            # Detect bad fallback price: exit_px=1.0 on a coin that clearly wasn't $1
            bad_price = (exit_px_f == 1.0 and pos["entry_px"] != 1.0
                         and not (0.90 <= pos["entry_px"] <= 1.10))
            trader = pos.get("trader")
            if trader is None:
                trader = last_leader_for_coin.get(coin)
            trades.append({
                "open_time": pos["open_time"],
                "close_time": t,
                "coin": coin,
                "side": side,
                "size": pos["size"],
                "entry_px": pos["entry_px"],
                "exit_px": exit_px_f,
                "margin": pos["margin"],
                "leverage": pos["leverage"],
                "notional": pos["notional"],
                "pnl_pct": pnl_pct_f,
                "pnl_usd": round(pnl_usd, 2),
                "bad_price": bad_price,
                "trader": trader,  # int T#, "ALL", or None
            })

    # Sort newest first
    trades.sort(key=lambda x: x["close_time"], reverse=True)
    return trades


@app.route("/trades")
def trades_page():
    return render_template("trades.html", active="trades", nav_title="TRADES")


@app.route("/traders")
def traders_page():
    return render_template("traders.html", active="traders", nav_title="LEADERS")


@app.route("/bn-swing")
@app.route("/pmm-simple")
def bn_swing_page():
    return render_template("bn_swing.html", active="bn-swing", nav_title="PMM SIMPLE")


@app.route("/helliot")
def helliot_page():
    return render_template("helliot.html", active="helliot", nav_title="HELLIOT")


@app.route("/surfn")
def surfn_page():
    return render_template("surfn.html", active="surfn", nav_title="SURFN")


@app.route("/shorty")
@app.route("/shortn")
def shortn_page():
    return render_template("shortn.html", active="shorty", nav_title="SHORTY")


@app.route("/dman")
def dman_page():
    return render_template("dman.html", active="dman", nav_title="DMAN")


@app.route("/onlyup")
def onlyup_page():
    return render_template("onlyup.html", active="onlyup", nav_title="ONLY UP")


@app.route("/dman-maker")
@app.route("/dman_maker")
def dman_maker_page():
    return render_template("dman_maker.html", active="dman-maker", nav_title="DMAN MAKER")


@app.route("/condor")
def condor_page():
    return render_template("condor.html", active="condor", nav_title="CONDOR")


VERSION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version")
if os.path.isdir("/app/version"):
    VERSION_DIR = "/app/version"
if VERSION_DIR not in sys.path:
    sys.path.insert(0, VERSION_DIR)


@app.route("/version")
@app.route("/version/")
def version_page():
    index = os.path.join(VERSION_DIR, "index.html")
    if os.path.isfile(index):
        return send_from_directory(VERSION_DIR, "index.html")
    return Response("version site missing — mount ./version\n", status=503, mimetype="text/plain")


def _version_lib():
    try:
        import timeshift_lib as ts
        return ts
    except Exception:
        return None


@app.route("/api/version/snapshots")
def api_version_snapshots():
    ts = _version_lib()
    if not ts:
        return jsonify({"ok": False, "error": "version library missing"}), 503
    snaps = ts.list_snapshots()
    return jsonify({
        "ok": True,
        "local": True,
        "root": ts.SNAP_ROOT,
        "running": ts.timeshift_running(),
        "job": ts.job_get(),
        "snapshots": snaps,
        "n": len(snaps),
    })


@app.route("/api/version/create", methods=["POST"])
def api_version_create():
    ts = _version_lib()
    if not ts:
        return jsonify({"ok": False, "error": "version library missing"}), 503
    data = request.get_json(silent=True) or {}
    return jsonify(ts.create_snapshot(data.get("label") or "manual"))


@app.route("/api/version/restore", methods=["POST"])
def api_version_restore():
    ts = _version_lib()
    if not ts:
        return jsonify({"ok": False, "error": "version library missing"}), 503
    data = request.get_json(silent=True) or {}
    return jsonify(ts.restore_snapshot(data.get("name"), data.get("confirm")))


@app.route("/api/version/job")
def api_version_job():
    ts = _version_lib()
    if not ts:
        return jsonify({"ok": False, "error": "version library missing"}), 503
    return jsonify({"ok": True, "job": ts.job_get(), "running": ts.timeshift_running()})


WIRE_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wire", "dist")


def _wire_send(filename):
    if not os.path.isdir(WIRE_DIST):
        return Response(
            "wire UI not built — missing dashboard/wire/dist\n",
            status=503,
            mimetype="text/plain",
        )
    resp = send_from_directory(WIRE_DIST, filename)
    if filename == "index.html" or filename.endswith(".html"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/wire")
@app.route("/wire/")
def wire_root():
    return _wire_send("index.html")


@app.route("/wire/<path:path>")
def wire_assets(path):
    full = os.path.normpath(os.path.join(WIRE_DIST, path))
    root = os.path.abspath(WIRE_DIST)
    if not full.startswith(root + os.sep) and full != root:
        return Response("bad path\n", status=400, mimetype="text/plain")
    if os.path.isfile(full):
        return _wire_send(path)
    return _wire_send("index.html")


WIRE_GRAPH_FILE = os.path.join(CONF_DIR, "wire_graph.json")
WIRE_XML_FILE = os.path.join(CONF_DIR, "wire_graph.xml")
WIRE_RUNTIME_FILE = os.path.join(CONF_DIR, "wire_runtime.json")
RISK_SKIM_FILE = os.path.join(CONF_DIR, "risk_skim.json")
_RISK_TRADE_FILES = {
    "hummingbot-bn-swing": os.path.join(CONF_DIR, "bn_swing_trades.json"),
    "hummingbot-helliot": os.path.join(CONF_DIR, "helliot_trades.json"),
    "hummingbot-surfn": os.path.join(CONF_DIR, "surfn_trades.json"),
    "hummingbot-shortn": os.path.join(CONF_DIR, "shortn_trades.json"),
    "hummingbot-dman": os.path.join(CONF_DIR, "dman_trades.json"),
    "hummingbot-dman-maker": os.path.join(CONF_DIR, "dman_maker_trades.json"),
    "hummingbot-onlyup": os.path.join(CONF_DIR, "onlyup_trades.json"),
}
_RISK_LEDGER_KEYS = ("parked_usd", "streak", "last_win_usd", "last_trade_key", "last_skim_ts", "events")


def _merge_risk_ledger(skim):
    prev = _read_json_file(RISK_SKIM_FILE) or {}
    old_h = prev.get("hunters") if isinstance(prev.get("hunters"), dict) else {}
    out = {}
    for bid, row in (skim or {}).items():
        if not isinstance(row, dict):
            continue
        merged = dict(row)
        old = old_h.get(bid) if isinstance(old_h.get(bid), dict) else {}
        for k in _RISK_LEDGER_KEYS:
            if k == "events":
                merged[k] = list(old.get("events") or [])[-40:]
            elif k not in merged or merged.get(k) is None:
                merged[k] = old.get(k) if old.get(k) is not None else (0 if k in ("parked_usd", "streak", "last_win_usd") else ("" if k != "last_trade_key" else None))
        # The DMan Maker checkbox owns this row. A canvas compile must not
        # turn "send every 2 win" back on while the hunter is keeping wins.
        if bid == "hummingbot-dman-maker" and str(old.get("mode") or "") in ("keep", "off", "none"):
            for k in ("mode", "skim_pct", "streak_wins", "note", "parked_usd"):
                if k in old:
                    merged[k] = old[k]
        out[bid] = merged
    return {
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "send": False,
        "hunters": out,
    }


def _risk_file_lock():
    """Same flock as scripts/risk_park.py so a close is booked once."""
    class _Lock:
        def __enter__(self):
            self.fd = open(RISK_SKIM_FILE + ".lock", "a+")
            import fcntl
            fcntl.flock(self.fd, fcntl.LOCK_EX)
            return self

        def __exit__(self, *_exc):
            import fcntl
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self.fd.close()
            except Exception:
                pass
    return _Lock()


def _risk_trade_pnl(trade):
    """Stored dollar, or amount times the price difference when the dollar is blank."""
    if not isinstance(trade, dict):
        return None
    raw = trade.get("pnl_usd")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    try:
        amt = float(trade.get("amount"))
        exit_px = float(trade.get("price"))
        entry = float(trade.get("entry_price"))
    except (TypeError, ValueError):
        return None
    if not amt or not exit_px or not entry:
        return None
    if str(trade.get("side") or "").upper() == "SHORT":
        return amt * (entry - exit_px)
    return amt * (exit_px - entry)


def _risk_closed_trades(bot_id):
    rows = []
    if bot_id == "hummingbot-hl":
        for t in parse_copy_trades() or []:
            pnl = _risk_trade_pnl(t)
            if pnl is None:
                continue
            rows.append({
                "key": str(t.get("close_time") or "") + "|" + str(t.get("coin") or ""),
                "pnl": pnl,
                "coin": t.get("coin"),
                "ts": t.get("close_time"),
            })
        return rows
    path = _RISK_TRADE_FILES.get(bot_id)
    raw = _read_json_file(path) if path else {}
    trades = raw.get("trades") if isinstance(raw, dict) else None
    if not isinstance(trades, list):
        return rows
    for t in trades:
        if not isinstance(t, dict):
            continue
        action = str(t.get("action") or "").upper()
        if action and action != "CLOSE":
            continue
        pnl = _risk_trade_pnl(t)
        if pnl is None:
            continue
        ts = t.get("close_time") or t.get("ts") or t.get("time") or ""
        coin = t.get("coin") or ""
        rows.append({"key": str(ts) + "|" + str(coin), "pnl": pnl, "coin": coin, "ts": ts})
    return rows


def _risk_apply_trade(cfg, pnl, prev_pnl=None):
    """Update streak / parked_usd. Does not send on HL.

    two_wins: after two realized wins in a row, park only the second
    trade's profit. The first win stays on the hunter. A loss resets.
    """
    cfg = dict(cfg or {})
    parked = float(cfg.get("parked_usd") or 0)
    mode = str(cfg.get("mode") or "win_pct")
    events = list(cfg.get("events") or [])[-39:]
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    if pnl <= 0:
        cfg["streak"] = 0
        cfg["last_win_usd"] = 0.0
        return cfg
    if mode in ("keep", "off", "none"):
        return cfg
    take = 0.0
    why = ""
    if mode == "two_wins":
        # Alternate realized wins: 1st stays on the hunter (margin), 2nd parks
        # to RISK. A loss resets. Do not use prev_pnl — a just-parked 2nd win
        # would otherwise skim every following win in the streak.
        if int(cfg.get("streak") or 0) >= 1:
            take = max(0.0, float(pnl))
            cfg["streak"] = 0
            cfg["last_win_usd"] = 0.0
            why = "2nd win only"
        else:
            cfg["streak"] = 1
            cfg["last_win_usd"] = float(pnl)
    else:
        pct = float(cfg.get("skim_pct") or 0)
        if pct > 1:
            pct = pct / 100.0
        take = max(0.0, pnl * pct)
        min_usd = float(cfg.get("min_usd") or 0)
        every_h = float(cfg.get("every_h") or cfg.get("skim_every_h") or 24)
        last_ts = str(cfg.get("last_skim_ts") or "")
        due = True
        if last_ts and every_h > 0:
            try:
                prev = datetime.strptime(last_ts[:19], "%Y-%m-%d %H:%M:%S")
                due = (datetime.utcnow() - prev).total_seconds() >= every_h * 3600
            except Exception:
                due = True
        if take < min_usd or not due:
            take = 0.0
        else:
            why = "Win% " + str(int(pct * 100)) + "%"
            cfg["last_skim_ts"] = now
    if take > 0:
        parked += take
        events.append({"ts": now, "take": round(take, 4), "pnl": round(pnl, 4), "why": why})
        cfg["parked_usd"] = round(parked, 4)
        cfg["events"] = events[-40:]
    return cfg


def _risk_skim_tick():
    with _risk_file_lock():
        _risk_skim_tick_locked()


def _risk_skim_tick_locked():
    data = _read_json_file(RISK_SKIM_FILE) or {}
    hunters = data.get("hunters") if isinstance(data.get("hunters"), dict) else {}
    if not hunters:
        return
    changed = False
    for bid, cfg in list(hunters.items()):
        if not isinstance(cfg, dict):
            continue
        last = str(cfg.get("last_trade_key") or "")
        trades = _risk_closed_trades(bid)
        if not last:
            if trades:
                last_t = trades[-1]
                cfg["last_trade_key"] = last_t.get("key") or ""
                pnl = float(last_t.get("pnl") or 0)
                if pnl > 0:
                    cfg["streak"] = 1
                    cfg["last_win_usd"] = pnl
                else:
                    cfg["streak"] = 0
                    cfg["last_win_usd"] = 0.0
                hunters[bid] = cfg
                changed = True
            continue
        prev = None
        for t in trades:
            key = t.get("key") or ""
            if not key:
                continue
            if key <= last:
                prev = t
                continue
            cfg = _risk_apply_trade(
                cfg,
                t.get("pnl") or 0,
                (prev.get("pnl") if prev else None),
            )
            cfg["last_trade_key"] = key
            hunters[bid] = cfg
            changed = True
            last = key
            prev = t
    if changed:
        data["hunters"] = hunters
        data["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        try:
            _write_json_file(RISK_SKIM_FILE, data)
        except OSError:
            pass


_RISK_BAL_CACHE = {"ts": 0.0, "by": {}}


def _risk_onchain(addr: str):
    """Perp + spot USDC on the RISK HL sub. Cached 30s. Never trades."""
    a = (addr or "").strip().lower()
    if not (a.startswith("0x") and len(a) == 42):
        return {"perp_usd": 0.0, "spot_usd": 0.0, "usd": 0.0}
    now = time.time()
    if now - float(_RISK_BAL_CACHE.get("ts") or 0) < 30:
        hit = (_RISK_BAL_CACHE.get("by") or {}).get(a)
        if hit:
            return hit
    perp = 0.0
    spot = 0.0
    try:
        ch = _hl_info_post({"type": "clearinghouseState", "user": a}, timeout=6) or {}
        ms = ch.get("marginSummary") if isinstance(ch.get("marginSummary"), dict) else {}
        perp = float(ms.get("accountValue") or 0)
    except Exception:
        perp = 0.0
    try:
        sp = _hl_info_post({"type": "spotClearinghouseState", "user": a}, timeout=6) or {}
        for b in sp.get("balances") or []:
            if isinstance(b, dict) and str(b.get("coin") or "").upper() == "USDC":
                spot += float(b.get("total") or 0)
    except Exception:
        pass
    out = {"perp_usd": round(perp, 4), "spot_usd": round(spot, 4), "usd": round(perp + spot, 4)}
    by = dict(_RISK_BAL_CACHE.get("by") or {})
    by[a] = out
    _RISK_BAL_CACHE["ts"] = now
    _RISK_BAL_CACHE["by"] = by
    return out


def _risk_from_wire_graph():
    """Hunter RISK rows from Desk canvas even if the skim ledger is empty."""
    g = _read_json_file(WIRE_GRAPH_FILE) or {}
    nodes = [n for n in (g.get("nodes") or []) if isinstance(n, dict)]
    edges = [e for e in (g.get("edges") or []) if isinstance(e, dict)]
    by_id = {str(n.get("id")): n for n in nodes if n.get("id")}
    out = {}
    for e in edges:
        if str(e.get("sourceHandle") or "") != "risk" and str(e.get("targetHandle") or "") != "risk":
            continue
        src = by_id.get(str(e.get("source") or ""))
        tgt = by_id.get(str(e.get("target") or ""))
        if not src or not tgt:
            continue
        bot, wallet = src, tgt
        if tgt.get("type") == "bot" or (tgt.get("data") or {}).get("kind") == "bot":
            bot, wallet = tgt, src
        if bot.get("type") != "bot" and str(bot.get("id") or "") not in (BOTS or {}):
            continue
        bid = str(bot.get("id") or "")
        wd = wallet.get("data") if isinstance(wallet.get("data"), dict) else {}
        if not bid or bid in out:
            continue
        mode = str(wd.get("skim_mode") or "win_pct")
        if mode not in ("win_pct", "two_wins"):
            mode = "win_pct"
        out[bid] = {
            "wallet": wallet.get("id"),
            "address": wd.get("address") or "",
            "mode": mode,
            "skim_pct": 100 if mode == "two_wins" else wd.get("skim_pct"),
            "parked_usd": 0.0,
            "sent_usd": 0.0,
            "armed": bool(wd.get("armed")),
        }
    return out


def _risk_home_payload():
    data = _read_json_file(RISK_SKIM_FILE) or {}
    hunters = data.get("hunters") if isinstance(data.get("hunters"), dict) else {}
    if not hunters:
        hunters = _risk_from_wire_graph()
        data = {"hunters": hunters, "updated": data.get("updated")}
    rows = []
    total = 0.0
    sent_total = 0.0
    dest = ""
    for bid, cfg in hunters.items():
        if not isinstance(cfg, dict):
            continue
        parked = 0.0
        try:
            parked = float(cfg.get("parked_usd") or 0)
        except (TypeError, ValueError):
            parked = 0.0
        try:
            sent = float(cfg.get("sent_usd") or 0)
        except (TypeError, ValueError):
            sent = 0.0
        total += parked
        sent_total += sent
        dest = dest or (cfg.get("address") or "")
        name = (BOTS.get(bid) or {}).get("name") or bid
        rows.append({
            "id": bid,
            "name": name,
            "connected": True,
            "address": cfg.get("address") or "",
            "mode": cfg.get("mode") or "win_pct",
            "skim_pct": cfg.get("skim_pct"),
            "parked_usd": round(parked, 4),
            "sent_usd": round(sent, 4),
            "streak": int(cfg.get("streak") or 0),
            "armed": bool(cfg.get("armed")),
            "last_send_ts": cfg.get("last_send_ts") or "",
            "last_send_error": cfg.get("last_send_error") or "",
            "force_send": bool(cfg.get("force_send")),
        })
    rows.sort(key=lambda r: r["name"])
    onchain = _risk_onchain(dest) if dest else {"perp_usd": 0.0, "spot_usd": 0.0, "usd": 0.0}
    return {
        "connected": len(rows),
        "parked_usd": round(total, 4),
        "sent_usd": round(sent_total, 4),
        "onchain_usd": onchain.get("usd") or 0.0,
        "onchain_spot_usd": onchain.get("spot_usd") or 0.0,
        "onchain_perp_usd": onchain.get("perp_usd") or 0.0,
        "address": dest,
        "hunters": rows,
        "updated": data.get("updated"),
        "send": True,
        "can_send": False,
        "available_usd": 0.0,
        "locked_usd": round(total, 4),
    }


def _risk_fill_available(snap):
    """Split parked into ready-to-send vs locked in open margin. Never closes."""
    if not isinstance(snap, dict):
        return snap
    hunters = snap.get("hunters") if isinstance(snap.get("hunters"), list) else []
    accounts = {}
    try:
        acc = _read_json_file(BOT_ACCOUNTS_FILE) or {}
        for row in acc.get("bots") or []:
            if isinstance(row, dict) and row.get("id"):
                accounts[str(row["id"])] = str(row.get("address") or "").strip()
    except Exception:
        accounts = {}
    buffer = 0.50
    min_send = 1.00
    avail_tot = 0.0
    locked_tot = 0.0
    for r in hunters:
        if not isinstance(r, dict):
            continue
        try:
            parked = float(r.get("parked_usd") or 0)
        except (TypeError, ValueError):
            parked = 0.0
        wd = r.get("withdrawable_usd")
        if wd is None:
            addr = accounts.get(str(r.get("id") or ""))
            if addr:
                try:
                    ch = _hl_info_post({"type": "clearinghouseState", "user": addr}, timeout=6) or {}
                    wd = float(ch.get("withdrawable") or 0)
                    r["withdrawable_usd"] = round(wd, 4)
                except Exception:
                    wd = 0.0
            else:
                wd = 0.0
        try:
            wd = float(wd or 0)
        except (TypeError, ValueError):
            wd = 0.0
        cap = max(0.0, wd - buffer)
        avail = min(parked, cap) if parked > 0 else 0.0
        if avail < min_send:
            avail = 0.0
        locked = max(0.0, parked - avail)
        r["available_usd"] = round(avail, 4)
        r["locked_usd"] = round(locked, 4)
        avail_tot += avail
        locked_tot += locked
    snap["available_usd"] = round(avail_tot, 4)
    snap["locked_usd"] = round(locked_tot, 4)
    snap["can_send"] = avail_tot >= min_send
    return snap


def _risk_skim_loop():
    while True:
        time.sleep(5)
        try:
            _risk_skim_tick()
        except Exception:
            pass

WIRE_WALLETS_FILE = os.path.join(CONF_DIR, "wire_wallets.json")
WIRE_REGISTRY_FILE = os.path.join(CONF_DIR, "wire_registry.json")
CONDOR_GAUGE_FILE = os.path.join(CONF_DIR, "condor_gauge.json")
PUMP_TAPE_FILE = os.path.join(CONF_DIR, "pump_tape.json")
PUMP_GAUGE_FILE = os.path.join(CONF_DIR, "pump_gauge.json")
CONDOR_SETUP_FILE = os.path.join(CONF_DIR, "condor_setup.json")
CONDOR_FEED_FILE = os.path.join(CONF_DIR, "condor_feed.json")
PHONE_GROK_ID = "grok-bot-telegram"

CONDOR_HUNTERS = {
    "hummingbot-bn-swing": {"bias": BN_SWING_BIAS_FILE, "coins": ("BTC", "ETH", "LTC", "TAO"), "mode": "both"},
    "hummingbot-helliot": {"bias": HELLIOT_BIAS_FILE, "coins": ("BTC", "ETH", "LTC", "TAO"), "mode": "both"},
    "hummingbot-surfn": {"bias": SURFN_BIAS_FILE, "coins": ("BTC", "ETH", "LTC", "TAO"), "mode": "both"},
    "hummingbot-shortn": {"bias": SHORTN_BIAS_FILE, "coins": ("BTC", "ETH", "LTC", "TAO"), "mode": "both"},
    "hummingbot-dman": {"bias": DMAN_BIAS_FILE, "coins": ("BTC", "ETH", "LTC", "TAO"), "mode": "both"},
    "hummingbot-onlyup": {"bias": ONLYUP_BIAS_FILE, "coins": ONLYUP_COINS, "mode": "both"},
    "hummingbot-dman-maker": {"bias": DMAN_MAKER_BIAS_FILE, "coins": ("BTC", "ETH", "LTC", "TAO"), "mode": "both"},
}


def _write_json_file(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _load_extra_wallets():
    data = _read_json_file(WIRE_WALLETS_FILE) or {}
    rows = data.get("wallets") if isinstance(data.get("wallets"), list) else []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        wid = str(row.get("id") or "").strip()
        if not wid.startswith("wallet-extra-"):
            continue
        addr = str(row.get("address") or "").strip()
        out.append({
            "id": wid[:80],
            "name": str(row.get("name") or "Wallet")[:40],
            "address": addr,
            "use_vault": bool(row.get("use_vault", True)),
            "color": str(row.get("color") or "#f0b90b")[:20],
            "custom": True,
        })
    return out


def _save_extra_wallets(rows):
    payload = {
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "wallets": rows,
    }
    _write_json_file(WIRE_WALLETS_FILE, payload)
    if _MATRIX_OK:
        try:
            matrix_store.kv_set("wire", "extra_wallets", rows, "wire")
        except Exception:
            pass
    return payload


class _LazySeq:
    def __init__(self, fn):
        self._fn = fn

    def __iter__(self):
        return iter(self._fn())

    def __getitem__(self, i):
        return self._fn()[i]

    def __len__(self):
        return len(self._fn())


class _LazySet:
    def __init__(self, fn):
        self._fn = fn

    def __contains__(self, item):
        return item in self._fn()

    def __iter__(self):
        return iter(self._fn())


class _LazyHref:
    def get(self, bid, default=None):
        h = registry_hunter(bid)
        if h and h.get("href"):
            return h.get("href")
        return default


_DEFAULT_HUNTERS = (
    {"id": "hummingbot-hl", "name": "HL Copy", "icon": "⚡", "color": "#4f8cff", "hasBias": False, "coins": [], "kind": "copy", "href": "/copy", "script": "hl_copy_trader.yml", "status_file": "hl_copy_status.json", "bias_file": "", "log_file": "logs_hl_copy_trader.log", "controller_fields": [
        {"key": "copy_shorts", "label": "Copy shorts", "kind": "bool"},
        {"key": "max_leverage", "label": "Max lev", "kind": "num", "step": 1, "min": 1, "max": 50},
        {"key": "max_positions_per_trader", "label": "Pos / trader", "kind": "num", "step": 1, "min": 1, "max": 8},
        {"key": "max_position_loss_pct", "label": "Max loss % eq", "kind": "pct_frac", "step": 1},
        {"key": "min_free_margin_pct", "label": "Min free %", "kind": "pct_frac", "step": 1},
        {"key": "max_subscribed_markets", "label": "Max books", "kind": "num", "step": 1, "min": 3, "max": 12},
        {"key": "poll_interval_seconds", "label": "Poll sec", "kind": "num", "step": 1, "min": 2, "max": 30},
        {"key": "max_zec_short_usd", "label": "Max short ZEC $", "kind": "num", "step": 10, "min": 0, "max": 500000},
    ]},
    {"id": "hummingbot-bn-swing", "name": "PMM Simple", "icon": "▤", "color": "#f0b90b", "hasBias": True, "coins": ["BTC", "ETH", "LTC", "TAO"], "kind": "coins", "mode": "both", "href": "/bn-swing", "script": "pmm_simple_trader.yml", "status_file": "bn_swing_status.json", "bias_file": "bn_swing_bias.json", "log_file": "logs_pmm_simple_trader.log", "controller_fields": [
        {"key": "take_profit", "label": "Take profit %", "kind": "pct_frac", "step": 0.1},
        {"key": "trail_activation", "label": "Trail arm %", "kind": "pct_frac", "step": 0.1},
        {"key": "trail_delta", "label": "Trail delta %", "kind": "pct_frac", "step": 0.1},
        {"key": "executor_refresh_time", "label": "Refresh sec", "kind": "num", "step": 1, "min": 10, "max": 600},
        {"key": "sl_roe_pct", "label": "Stop loss ROE %", "kind": "num", "step": 1},
        {"key": "margin_pct", "label": "Margin % leftover", "kind": "pct_frac", "step": 1},
    ]},
    {"id": "hummingbot-helliot", "name": "Helliot", "icon": "∿", "color": "#7ecbff", "hasBias": True, "coins": ["BTC", "ETH", "LTC", "TAO"], "kind": "coins", "href": "/helliot", "script": "helliot_trader.yml", "status_file": "helliot_status.json", "bias_file": "helliot_bias.json", "log_file": "logs_helliot_trader.log", "controller_fields": [
        {"key": "tp_roe_pct", "label": "Take profit ROE %", "kind": "num", "step": 1},
        {"key": "sl_roe_pct", "label": "Stop loss ROE %", "kind": "num", "step": 1},
        {"key": "wave2_min", "label": "Wave2 min", "kind": "num", "step": 0.001},
        {"key": "wave2_max", "label": "Wave2 max", "kind": "num", "step": 0.001},
        {"key": "wave3_ext", "label": "Wave3 ext", "kind": "num", "step": 0.001},
        {"key": "margin_pct", "label": "Margin % leftover", "kind": "pct_frac", "step": 1},
    ]},
    {"id": "hummingbot-surfn", "name": "SuperTrend", "icon": "≈", "color": "#2ee6c7", "hasBias": True, "coins": ["BTC", "ETH", "LTC", "TAO"], "kind": "coins", "href": "/surfn", "script": "surfn_trader.yml", "status_file": "surfn_status.json", "bias_file": "surfn_bias.json", "log_file": "logs_surfn_trader.log", "controller_fields": [
        {"key": "st_length", "label": "ST length", "kind": "num", "step": 1},
        {"key": "st_mult", "label": "ST multiplier", "kind": "num", "step": 0.1},
        {"key": "percentage_threshold", "label": "ST distance %", "kind": "pct_frac", "step": 0.1},
        {"key": "scale_roe_pct", "label": "Clip at ROE %", "kind": "num", "step": 1},
        {"key": "scale_out_pct", "label": "Clip size %", "kind": "pct_frac", "step": 1},
        {"key": "sl_roe_pct", "label": "Stop loss ROE %", "kind": "num", "step": 1},
        {"key": "margin_pct", "label": "Margin % leftover", "kind": "pct_frac", "step": 1},
    ]},
    {"id": "hummingbot-shortn", "name": "Shorty", "icon": "▾", "color": "#ff5d73", "hasBias": True, "coins": ["BTC", "ETH", "LTC", "TAO"], "kind": "coins", "href": "/shorty", "script": "shortn_trader.yml", "status_file": "shortn_status.json", "bias_file": "shortn_bias.json", "log_file": "logs_shortn_trader.log", "controller_fields": [
        {"key": "tp_roe_pct", "label": "Take profit ROE %", "kind": "num", "step": 1},
        {"key": "sl_roe_pct", "label": "Stop loss ROE %", "kind": "num", "step": 1},
        {"key": "recover_roe_pct", "label": "Rebuy bounce ROE %", "kind": "num", "step": 1},
        {"key": "bb_short_threshold", "label": "Short BBP ≥", "kind": "num", "step": 0.01},
        {"key": "margin_pct", "label": "Margin % leftover", "kind": "pct_frac", "step": 1},
    ]},
    {"id": "hummingbot-dman", "name": "Dman", "icon": "▦", "color": "#c084fc", "hasBias": True, "coins": ["BTC", "ETH", "LTC", "TAO"], "kind": "coins", "href": "/dman", "script": "dman_trader.yml", "status_file": "dman_status.json", "bias_file": "dman_bias.json", "log_file": "logs_dman_trader.log", "controller_fields": [
        {"key": "trail_activation", "label": "Trail arm %", "kind": "pct_frac", "step": 0.1},
        {"key": "trail_delta", "label": "Trail delta %", "kind": "pct_frac", "step": 0.1},
        {"key": "bb_long_threshold", "label": "Rebuy BBP ≤", "kind": "num", "step": 0.01},
        {"key": "bb_short_threshold", "label": "Short BBP ≥", "kind": "num", "step": 0.01},
        {"key": "hold_h", "label": "Hold hours", "kind": "select", "options": ["12", "24", "68"]},
        {"key": "sl_roe_pct", "label": "Stop loss ROE %", "kind": "num", "step": 1},
    ]},
    {"id": "hummingbot-onlyup", "name": "Only Up", "icon": "▲", "color": "#ffd166", "hasBias": True, "coins": ["BTC", "LTC", "TAO"], "kind": "coins", "href": "/onlyup", "script": "onlyup_trader.yml", "status_file": "onlyup_status.json", "bias_file": "onlyup_bias.json", "log_file": "logs_onlyup_trader.log", "controller_fields": [
        {"key": "close_px", "label": "Close $", "kind": "num", "step": 0.01, "min": 0.01, "max": 1000000},
        {"key": "scale_roe_pct", "label": "Clip at ROE %", "kind": "num", "step": 1},
        {"key": "scale_out_pct", "label": "Clip size %", "kind": "pct_frac", "step": 1},
        {"key": "sl_roe_pct", "label": "Stop loss ROE %", "kind": "num", "step": 1},
        {"key": "st_max_dist", "label": "ST max dist %", "kind": "pct_frac", "step": 0.1},
        {"key": "margin_pct", "label": "Margin % leftover", "kind": "pct_frac", "step": 1},
    ]},
    {"id": "hummingbot-dman-maker", "name": "DMan Maker", "icon": "⧉", "color": "#a78bfa", "hasBias": True, "coins": ["BTC", "ETH", "LTC", "TAO"], "kind": "coins", "href": "/dman-maker", "script": "dman_maker_v2_trader.yml", "status_file": "dman_maker_status.json", "bias_file": "dman_maker_bias.json", "log_file": "logs_dman_maker_v2_trader.log", "controller_fields": [
        {"key": "dontGoCrazy", "label": "dontGoCrazy", "kind": "bool"},
        {"key": "afterLossNoRebuy", "label": "afterLossNoRebuy", "kind": "bool"},
        {"key": "take_profit", "label": "Take profit %", "kind": "pct_frac", "step": 0.1},
        {"key": "trail_activation", "label": "Trail arm %", "kind": "pct_frac", "step": 0.1},
        {"key": "trail_delta", "label": "Trail delta %", "kind": "pct_frac", "step": 0.1},
        {"key": "executor_refresh_time", "label": "Refresh sec", "kind": "num", "step": 1, "min": 10, "max": 600},
        {"key": "sl_roe_pct", "label": "Stop loss ROE %", "kind": "num", "step": 1},
        {"key": "margin_pct", "label": "Margin % leftover", "kind": "pct_frac", "step": 1},
    ]},
)


def _auto_hunter_from_account(bot):
    bid = str(bot.get("id") or "")
    slug = bid.replace("hummingbot-", "").replace("-", "_")
    meta = BOTS.get(bid) or {}
    return {
        "id": bid,
        "name": bot.get("name") or meta.get("name") or bid,
        "icon": meta.get("icon") or "⧉",
        "color": meta.get("color") or "#a78bfa",
        "hasBias": True,
        "coins": ["BTC", "ETH", "LTC", "TAO"],
        "kind": "copy" if bid == "hummingbot-hl" else "coins",
        "href": "/confiig",
        "container": bot.get("container") or bid,
        "yml": bot.get("yml") or "",
        "script": slug + "_trader.yml",
        "status_file": slug + "_status.json",
        "bias_file": slug + "_bias.json",
        "log_file": meta.get("log_file") or ("logs_" + slug + "_trader.log"),
        "controller_fields": [
            {"key": "sl_roe_pct", "label": "Stop loss ROE %", "kind": "num", "step": 1},
        ],
        "on_home": True,
        "restartable": True,
    }


def _save_registry(hunters):
    payload = {
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "hunters": hunters,
    }
    try:
        _write_json_file(WIRE_REGISTRY_FILE, payload)
    except OSError:
        pass
    if _MATRIX_OK:
        try:
            matrix_store.kv_set("wire", "registry_hunters", hunters, "wire")
        except Exception:
            pass
    return payload


def _load_registry():
    data = _read_json_file(WIRE_REGISTRY_FILE) or {}
    hunters = []
    if isinstance(data, dict) and isinstance(data.get("hunters"), list):
        hunters = [dict(h) for h in data["hunters"] if isinstance(h, dict) and h.get("id")]
    if not hunters:
        hunters = [dict(h) for h in _DEFAULT_HUNTERS]
    known = {h["id"] for h in hunters}
    changed = False
    defaults = {h["id"]: h for h in _DEFAULT_HUNTERS}
    for h in hunters:
        d = defaults.get(h.get("id"))
        if not d or not d.get("controller_fields"):
            continue
        src = [dict(x) for x in d["controller_fields"]]
        if h.get("controller_fields") != src:
            h["controller_fields"] = src
            changed = True
    for bot in load_bot_accounts().get("bots") or []:
        bid = str(bot.get("id") or "")
        if not bid or bid in known:
            continue
        hunters.append(_auto_hunter_from_account(bot))
        known.add(bid)
        changed = True
    if changed or not data.get("hunters"):
        _save_registry(hunters)
    return hunters


def registry_hunter(bot_id):
    bid = str(bot_id or "")
    for h in _load_registry():
        if h.get("id") == bid:
            return h
    return None


def _ensure_registry_hunter(bot_id):
    if registry_hunter(bot_id):
        return registry_hunter(bot_id)
    for bot in load_bot_accounts().get("bots") or []:
        if bot.get("id") == bot_id:
            hunters = _load_registry()
            row = _auto_hunter_from_account(bot)
            hunters.append(row)
            _save_registry(hunters)
            return row
    return None


def get_wire_hunter_rows():
    rows = []
    for h in _load_registry():
        rows.append((
            h["id"],
            h.get("name") or h["id"],
            h.get("icon") or "●",
            h.get("color") or "#94a3b8",
            bool(h.get("hasBias")),
            list(h.get("coins") or []),
        ))
    return rows


def get_home_brief_specs():
    out = []
    for h in _load_registry():
        if h.get("on_home") is False:
            continue
        kind = h.get("kind") or "coins"
        status = h.get("status_file") or ""
        path = status if str(status).startswith("/") else os.path.join(CONF_DIR, os.path.basename(status))
        out.append({
            "id": h["id"],
            "name": h.get("name") or h["id"],
            "status_file": path,
            "kind": kind,
            "href": h.get("href") or "/confiig",
            "icon": h.get("icon") or "",
            "color": h.get("color") or "#94a3b8",
        })
    return out


def get_home_restart_ids():
    return frozenset(
        h["id"] for h in _load_registry() if h.get("restartable", True)
    )


def _canvas_bot_ids(nodes):
    ids = []
    seen = set()
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        ntype = str(n.get("type") or "")
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        bid = ""
        if ntype == "bot":
            bid = str(n.get("id") or data.get("botId") or "")
        elif ntype in ("ctrl", "wallet", "bias") and data.get("botId"):
            bid = str(data.get("botId") or "")
        if not bid or bid in seen:
            continue
        if bid.startswith("wallet-extra-"):
            continue
        seen.add(bid)
        ids.append(bid)
    return ids


def _promote_canvas_to_home(nodes):
    """Register hummingbots on this canvas as Home cards. Does not start or close anything."""
    ids = _canvas_bot_ids(nodes)
    if not ids:
        return []
    hunters = _load_registry()
    by_id = {h["id"]: h for h in hunters}
    names = []
    dirty = False
    for bid in ids:
        row = by_id.get(bid)
        if not row:
            row = _ensure_registry_hunter(bid)
            hunters = _load_registry()
            by_id = {h["id"]: h for h in hunters}
            row = by_id.get(bid)
        if not row:
            data = {}
            for n in nodes or []:
                if isinstance(n, dict) and str(n.get("id") or "") == bid:
                    data = n.get("data") if isinstance(n.get("data"), dict) else {}
                    break
            slug = bid.replace("hummingbot-", "").replace("-", "_")
            row = {
                "id": bid,
                "name": data.get("name") or bid,
                "icon": data.get("icon") or "⧉",
                "color": data.get("color") or "#a78bfa",
                "hasBias": bool(data.get("hasBias", True)),
                "coins": list(data.get("coins") or ["BTC", "ETH", "LTC", "TAO"]),
                "kind": "coins",
                "href": data.get("href") or "/confiig",
                "container": bid,
                "script": "",
                "status_file": slug + "_status.json",
                "bias_file": slug + "_bias.json",
                "controller_fields": [
                    {"key": "sl_roe_pct", "label": "Stop loss ROE %", "kind": "num", "step": 1},
                ],
                "on_home": True,
                "restartable": True,
            }
            hunters.append(row)
            by_id[bid] = row
            dirty = True
        if row.get("on_home") is False:
            row["on_home"] = True
            dirty = True
        names.append(row.get("name") or bid)
    if dirty:
        _save_registry(hunters)
    return names


def _occupied_hl_addresses(exclude_id=None):
    occupied = {}
    for bot in load_bot_accounts().get("bots") or []:
        bid = str(bot.get("id") or "")
        if exclude_id and bid == exclude_id:
            continue
        addr = str(bot.get("address") or "").strip().lower()
        if _valid_hl_address(addr):
            occupied[addr] = bot.get("name") or bid
    for w in _load_extra_wallets():
        wid = str(w.get("id") or "")
        if exclude_id and wid == exclude_id:
            continue
        addr = str(w.get("address") or "").strip().lower()
        if _valid_hl_address(addr):
            occupied[addr] = w.get("name") or wid
    return occupied


def _hunter_wallet_slots():
    out = []
    for bot in load_bot_accounts().get("bots") or []:
        bid = str(bot.get("id") or "")
        if not bid:
            continue
        addr = str(bot.get("address") or "").strip()
        out.append({
            "id": bid,
            "name": bot.get("name") or bid,
            "address": addr,
            "empty": not _valid_hl_address(addr),
            "container": bot.get("container") or bid,
        })
    return out


def _confiig_set_address(bot_id, address, use_vault=True, restart=False):
    address = str(address or "").strip()
    if not _valid_hl_address(address):
        return {"ok": False, "error": "address must be 0x + 40 hex chars"}
    taken = _occupied_hl_addresses(exclude_id=bot_id).get(address.lower())
    if taken:
        return {"ok": False, "error": f"address already used by {taken}"}
    stored = load_bot_accounts()
    bots = list(stored.get("bots") or [])
    target = None
    for bot in bots:
        if bot.get("id") == bot_id:
            target = bot
            break
    if not target:
        return {"ok": False, "error": f"unknown bot {bot_id}"}
    _ensure_registry_hunter(bot_id)
    yml = target.get("yml")
    if not yml or not os.path.isfile(yml):
        return {"ok": False, "error": f"connector file missing: {yml}"}
    enc, err = _encrypt_hl_address(address)
    if not enc:
        return {"ok": False, "error": f"could not encrypt address: {err}"}
    try:
        _write_connector_yml(yml, enc, bool(use_vault))
    except Exception as e:
        return {"ok": False, "error": f"yml write failed: {e}"}
    target["address"] = address
    target["use_vault"] = bool(use_vault)
    stored["bots"] = bots
    stored["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    stored["source"] = "wire"
    try:
        tmp = BOT_ACCOUNTS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(stored, f, indent=2)
            f.write("\n")
        os.replace(tmp, BOT_ACCOUNTS_FILE)
    except Exception as e:
        return {"ok": False, "error": f"saved yml but accounts file failed: {e}"}
    restarted = False
    restart_error = None
    if restart:
        try:
            _restart_container(target.get("container") or bot_id)
            restarted = True
        except Exception as e:
            restart_error = str(e)
    msg = f"Saved {target.get('name')} → {address[:10]}…{address[-6:]}"
    if restarted:
        msg += " and restarted container"
    elif restart and restart_error:
        msg += f" (restart failed: {restart_error})"
    return {
        "ok": True,
        "message": msg,
        "address": address,
        "use_vault": bool(use_vault),
        "restarted": restarted,
        "restart_error": restart_error,
        "id": bot_id,
        "name": target.get("name") or bot_id,
        "bound": True,
        "wallet": {
            "id": "wallet-" + bot_id,
            "botId": bot_id,
            "name": target.get("name") or bot_id,
            "address": address,
            "use_vault": bool(use_vault),
            "custom": False,
            "color": (BOTS.get(bot_id) or {}).get("color") or "#f0b90b",
        },
    }


WIRE_HUNTERS = _LazySeq(get_wire_hunter_rows)
WIRE_HREF = _LazyHref()

WIRE_COIN_COLOR = {
    "BTC": "#f7931a", "ETH": "#627eea", "LTC": "#345d9d", "TAO": "#24c3cf",
    "SOL": "#9945ff", "LINK": "#2a5ada", "CC": "#fb7185",
    "ALGO": "#000000", "ZEC": "#f4b728", "ARB": "#28a0f0",
}


def _wire_edge(eid, source, sh, target, th):
    return {
        "id": eid,
        "source": source,
        "sourceHandle": sh,
        "target": target,
        "targetHandle": th,
    }


def build_live_wire_graph():
    """Rebuild the canvas from live wallets, positions, SL, bias, moon, DMan tune."""
    accounts = {}
    stored = load_bot_accounts()
    for b in stored.get("bots") or []:
        if isinstance(b, dict) and b.get("id"):
            accounts[b["id"]] = b.get("address")
    sl_file = _read_json_file(BOT_SL_FILE) or {}
    tune = _dman_tune_map()
    bias_map = {}
    for spec in HOME_BRIEF_SPECS:
        bid = spec["id"]
        bias_map[bid] = _home_bias_for(bid) if spec.get("kind") == "coins" else {}

    copy_coins = []
    st = _read_json_file(HL_COPY_STATUS) or {}
    for m in st.get("mirrors") or []:
        c = str((m or {}).get("coin") or "").upper()
        if c and c not in copy_coins:
            copy_coins.append(c)

    live_coin = {}
    for h in _load_registry():
        if h.get("kind") == "copy":
            continue
        bid = h["id"]
        coins = tuple(h.get("coins") or ())
        st = {}
        try:
            path = os.path.join(CONF_DIR, os.path.basename(h.get("status_file") or ""))
            st = _read_json_file(path) or {}
        except Exception:
            st = {}
        pos = st.get("positions") if isinstance(st.get("positions"), dict) else {}
        for c in coins:
            row = pos.get(c) or {}
            side = str(row.get("position_side") or row.get("side") or "").upper()
            b = str((bias_map.get(bid) or {}).get(c) or "FLAT").upper()
            if side in ("LONG", "SHORT") or b in ("LONG", "SHORT"):
                live_coin.setdefault(c, []).append(bid)

    nodes = []
    edges = []
    row_h = 250
    y0 = 40
    hunter_y = {h[0]: y0 + i * row_h for i, h in enumerate(WIRE_HUNTERS)}

    # coins column
    coin_list = ["BTC", "ETH", "LTC", "TAO"] + [c for c in copy_coins if c not in ("BTC", "ETH", "LTC", "TAO")]
    for i, c in enumerate(coin_list):
        nodes.append({
            "id": "coin-" + c,
            "type": "coin",
            "position": {"x": 24, "y": 40 + i * 150},
            "data": {"coin": c, "name": c, "color": WIRE_COIN_COLOR.get(c, "#94a3b8"), "toolId": "coin-" + c},
        })

    sl_groups = {}
    for bid, name, icon, color, has_bias, coins in WIRE_HUNTERS:
        y = hunter_y[bid]
        sl = 20.0
        row = sl_file.get(bid) if isinstance(sl_file.get(bid), dict) else {}
        try:
            if row.get("sl_roe_pct") is not None:
                sl = float(row["sl_roe_pct"])
        except (TypeError, ValueError):
            sl = 20.0
        sl_groups.setdefault(round(sl, 2), []).append(bid)

        nodes.append({
            "id": bid,
            "type": "bot",
            "position": {"x": 560, "y": y},
            "data": {
                "name": name, "icon": icon, "color": color, "hasBias": has_bias,
                "href": WIRE_HREF.get(bid), "coins": coins, "toolId": bid,
                "kind": "copy" if bid == "hummingbot-hl" else "coins",
                "id": bid,
            },
        })
        addr = accounts.get(bid)
        nodes.append({
            "id": "wallet-" + bid,
            "type": "wallet",
            "position": {"x": 260, "y": y},
            "data": {
                "botId": bid, "name": name, "icon": icon, "color": color,
                "address": addr, "toolId": "wallet-" + bid,
            },
        })
        edges.append(_wire_edge("e-wallet-" + bid, "wallet-" + bid, "wallet", bid, "wallet"))
        edges.append(_wire_edge("e-income-" + bid, bid, "income", "wallet-" + bid, "income"))
        edges.append(_wire_edge("e-logs-" + bid, bid, "logs", "logs", "in"))

        if has_bias:
            bmap = bias_map.get(bid) or {c: "FLAT" for c in coins}
            nodes.append({
                "id": "bias-" + bid,
                "type": "bias",
                "position": {"x": 260, "y": y + 145},
                "data": {
                    "botId": bid, "name": name, "icon": icon, "color": color,
                    "coins": coins, "bias": {c: bmap.get(c, "FLAT") for c in coins},
                    "toolId": "bias-" + bid,
                },
            })
            edges.append(_wire_edge("e-bias-" + bid, "bias-" + bid, "bias", bid, "bias"))

        nodes.append({
            "id": "ctrl-" + bid,
            "type": "ctrl",
            "position": {"x": 900, "y": y + 40},
            "data": {
                "botId": bid, "name": name, "icon": icon, "color": color,
                "href": WIRE_HREF.get(bid), "toolId": "ctrl-" + bid,
                "values": _ctrl_live_values(bid, None),
                "fields": (registry_hunter(bid) or {}).get("controller_fields") or [],
            },
        })
        edges.append(_wire_edge("e-ctrl-" + bid, "ctrl-" + bid, "ctrl", bid, "ctrl"))

    copy_y = hunter_y.get("hummingbot-hl", 40)
    for card in _copy_leader_cards():
        if not card.get("roster") or not card.get("num"):
            continue
        try:
            num = int(card["num"])
        except (TypeError, ValueError):
            continue
        nid = str(card.get("id") or "")
        if not nid or any(n.get("id") == nid for n in nodes):
            continue
        data = dict(card)
        data["kind"] = "trader"
        data["name"] = card.get("title") or f"T#{num}"
        data["toolId"] = nid
        nodes.append({
            "id": nid,
            "type": "trader",
            "position": {"x": 420 + (num - 1) * 300, "y": max(8, copy_y - 210)},
            "data": data,
        })
        if card.get("followed"):
            edges.append(_wire_edge(
                "e-lead-T" + str(num),
                nid,
                "trader",
                "hummingbot-hl",
                "trader-" + str(num),
            ))

    for sl_val, bots in sl_groups.items():
        nid = "sl-" + str(sl_val).replace(".", "p")
        # park SL nodes in a strip left of first bot that uses them
        y = hunter_y.get(bots[0], 40) + 175
        nodes.append({
            "id": nid,
            "type": "pct",
            "position": {"x": 560, "y": y},
            "data": {"kind": "sl", "pct": sl_val, "name": "Stop loss", "toolId": "sl-pct"},
        })
        for bid in bots:
            edges.append(_wire_edge("e-sl-" + nid + "-" + bid, nid, "sl", bid, "sl"))

    saved_graph = _read_json_file(WIRE_GRAPH_FILE) or {}
    saved_moon_bots = set()
    for e in saved_graph.get("edges") or []:
        if not isinstance(e, dict):
            continue
        if str(e.get("targetHandle") or "") == "moon":
            saved_moon_bots.add(str(e.get("target") or ""))
    for bid in _MOON_BOTS:
        if bid in saved_moon_bots:
            continue
        m = _load_hunter_moon(bid)
        on_row = None
        for c in _moon_coins_for(bid):
            row = m.get(c) if isinstance(m.get(c), dict) else {}
            if row.get("on"):
                on_row = row
                break
        if not on_row:
            continue
        try:
            mp = float(on_row.get("coin_pct") or 50)
        except (TypeError, ValueError):
            mp = 50.0
        nid = "moon-" + bid.replace("hummingbot-", "")
        nodes.append({
            "id": nid,
            "type": "pct",
            "position": {"x": 760, "y": hunter_y.get(bid, 40) + 175},
            "data": {"kind": "moon", "pct": mp, "name": "Moon", "toolId": "moon-pct"},
        })
        edges.append(_wire_edge("e-" + nid, nid, "moon", bid, "moon"))

    for c, bots in live_coin.items():
        cid = "coin-" + c
        if not any(n["id"] == cid for n in nodes):
            continue
        for bid in bots:
            edges.append(_wire_edge("e-coin-" + c + "-" + bid, cid, "coin", bid, "coin"))

    nodes.append({
        "id": "logs", "type": "logs",
        "position": {"x": 1180, "y": 40},
        "data": {"toolId": "logs"},
    })
    nodes.append({
        "id": "funds", "type": "funds",
        "position": {"x": 1180, "y": 360},
        "data": {"toolId": "funds"},
    })
    for i, w in enumerate(_load_extra_wallets()):
        wid = str(w.get("id") or "")
        if not wid or any(n.get("id") == wid for n in nodes):
            continue
        nodes.append({
            "id": wid,
            "type": "wallet",
            "position": {"x": 1180, "y": 560 + i * 190},
            "data": {
                "name": w.get("name") or "Wallet",
                "icon": "⬡",
                "color": w.get("color") or "#f0b90b",
                "address": w.get("address") or "",
                "custom": True,
                "use_vault": bool(w.get("use_vault", True)),
                "toolId": wid,
            },
        })

    return {
        "v": 5,
        "source": "live",
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "nodes": nodes,
        "edges": edges,
    }


def _parse_condor_chats(v):
    if isinstance(v, list):
        parts = v
    else:
        parts = str(v or "").replace(";", ",").split(",")
    out = []
    for p in parts:
        s = str(p).strip()
        if s:
            out.append(s)
    return out


def _condor_secret_is_new(v):
    s = str(v or "").strip()
    if not s:
        return False
    if "•" in s or "*" in s:
        return False
    if s.lower() in ("saved", "paste…", "paste...", "unchanged"):
        return False
    return True


def _load_condor_setup():
    data = _read_json_file(CONDOR_SETUP_FILE) or {}
    if not isinstance(data, dict):
        data = {}
    bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
    wired = data.get("wired") if isinstance(data.get("wired"), dict) else {}
    return data, bots, wired


def _save_condor_setup(bots, wired):
    payload = {
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "bots": bots if isinstance(bots, dict) else {},
        "wired": {
            "telegram": [str(x) for x in (wired.get("telegram") or []) if str(x).strip()],
            "grok": [str(x) for x in (wired.get("grok") or []) if str(x).strip()],
        },
    }
    _write_json_file(CONDOR_SETUP_FILE, payload)
    try:
        os.chmod(CONDOR_SETUP_FILE, 0o600)
    except OSError:
        pass
    return payload


def _public_condor_bot(nid, row):
    if not isinstance(row, dict):
        row = {}
    kind = "grok" if row.get("kind") == "grok" else "telegram"
    chats = _parse_condor_chats(row.get("chat_ids"))
    return {
        "id": str(nid),
        "kind": kind,
        "name": str(row.get("name") or ("Grok bot" if kind == "grok" else "Telegram bot"))[:60],
        "has_token": bool(str(row.get("token") or "").strip()) if kind == "telegram" else False,
        "has_key": bool(str(row.get("api_key") or "").strip()) if kind == "grok" else False,
        "chat_ids": ", ".join(chats) if kind == "telegram" else "",
        "model": str(row.get("model") or "grok-4.5")[:80] if kind == "grok" else None,
    }


def _public_condor_setup():
    _, bots, wired = _load_condor_setup()
    tg_wired = [str(x) for x in (wired.get("telegram") or [])]
    grok_wired = [str(x) for x in (wired.get("grok") or [])]
    public = []
    for nid, row in bots.items():
        item = _public_condor_bot(nid, row)
        item["wired"] = nid in tg_wired or nid in grok_wired
        public.append(item)
    return {
        "ok": True,
        "bots": public,
        "wired": {"telegram": tg_wired, "grok": grok_wired},
    }


def _upsert_condor_bot(body):
    nid = str(body.get("id") or "").strip()[:80]
    if not nid:
        return {"ok": False, "error": "id required"}
    kind = "grok" if str(body.get("kind") or "") == "grok" else "telegram"
    _, bots, wired = _load_condor_setup()
    row = bots.get(nid) if isinstance(bots.get(nid), dict) else {}
    row["id"] = nid
    row["kind"] = kind
    if body.get("name") is not None:
        row["name"] = str(body.get("name") or "")[:60]
    if kind == "telegram":
        if "chat_ids" in body:
            row["chat_ids"] = _parse_condor_chats(body.get("chat_ids"))
        if _condor_secret_is_new(body.get("token")):
            row["token"] = str(body.get("token")).strip()
    else:
        if body.get("model"):
            row["model"] = str(body.get("model") or "grok-4.5")[:80]
        if _condor_secret_is_new(body.get("api_key")):
            row["api_key"] = str(body.get("api_key")).strip()
    bots[nid] = row
    _save_condor_setup(bots, wired)
    pub = _public_condor_bot(nid, row)
    pub["wired"] = nid in (wired.get("telegram") or []) or nid in (wired.get("grok") or [])
    bits = [kind, pub.get("name") or nid]
    if kind == "telegram":
        bits.append("token saved" if pub["has_token"] else "token missing")
        if pub.get("chat_ids"):
            bits.append("chats " + pub["chat_ids"])
    else:
        bits.append("key saved" if pub["has_key"] else "key missing")
        if pub.get("model"):
            bits.append(pub["model"])
    return {"ok": True, "bot": pub, "message": " · ".join(bits)}


def _apply_condor_setup(nodes, edges):
    """Sync canvas Telegram/Grok nodes + Condor TG/GROK wires into conf/condor_setup.json.

    Secrets stay in that file; the graph only stores has_token / has_key flags.
    """
    _, bots, _ = _load_condor_setup()
    by_id = {}
    for n in nodes or []:
        if isinstance(n, dict) and n.get("id"):
            by_id[str(n["id"])] = n
    condor_ids = set()
    for nid, n in by_id.items():
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        if nid == "gauge-condor" or (n.get("type") == "gauge" and data.get("kind") == "condor"):
            condor_ids.add(nid)
    for nid, n in by_id.items():
        if n.get("type") != "agent":
            continue
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        kind = "grok" if data.get("kind") == "grok" else "telegram"
        row = bots.get(nid) if isinstance(bots.get(nid), dict) else {}
        row["id"] = nid
        row["kind"] = kind
        if data.get("name"):
            row["name"] = str(data.get("name"))[:60]
        if kind == "telegram" and "chat_ids" in data:
            row["chat_ids"] = _parse_condor_chats(data.get("chat_ids"))
        if kind == "grok" and data.get("model"):
            row["model"] = str(data.get("model"))[:80]
        bots[nid] = row
    wired_tg = []
    wired_grok = []
    for e in edges or []:
        tgt = str(e.get("target") or "")
        src = str(e.get("source") or "")
        tgt_n = by_id.get(tgt)
        tgt_data = tgt_n.get("data") if tgt_n and isinstance(tgt_n.get("data"), dict) else {}
        is_xlate = bool(tgt_n) and (
            tgt_n.get("type") == "xlate" or tgt_data.get("kind") == "translator"
        )
        is_desk = bool(tgt_n) and (
            tgt_n.get("type") == "desk_bias" or tgt_data.get("kind") == "desk"
        )
        if tgt not in condor_ids and not is_xlate and not is_desk:
            continue
        src_n = by_id.get(src)
        if not src_n or src_n.get("type") != "agent":
            continue
        th = str(e.get("targetHandle") or "")
        sh = str(e.get("sourceHandle") or "")
        src_data = src_n.get("data") if isinstance(src_n.get("data"), dict) else {}
        kind = "grok" if src_data.get("kind") == "grok" else "telegram"
        if kind == "telegram" and th in ("tg", "in", "") and sh in ("tg", "out", ""):
            if src not in wired_tg:
                wired_tg.append(src)
        elif kind == "grok" and th in ("grok", "") and sh in ("grok", "out", ""):
            if src not in wired_grok:
                wired_grok.append(src)
    phone = bots.get(PHONE_GROK_ID) if isinstance(bots.get(PHONE_GROK_ID), dict) else {}
    if str(phone.get("api_key") or "").strip() and PHONE_GROK_ID not in wired_grok:
        wired_grok.insert(0, PHONE_GROK_ID)
    try:
        _save_condor_setup(bots, {"telegram": wired_tg, "grok": wired_grok})
    except OSError:
        return []
    notes = []
    if wired_tg:
        notes.append("tg=" + ",".join(wired_tg))
    if wired_grok:
        notes.append("grok=" + ",".join(wired_grok))
    return notes


def _condor_feed_public():
    data = _read_json_file(CONDOR_FEED_FILE) or {}
    rows = data.get("lines") if isinstance(data.get("lines"), list) else []
    out = []
    for row in rows[-40:]:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "")[:280]
        if "xai-" in text.lower() and "••••" not in text:
            text = "[key hidden]"
        out.append({
            "ts": row.get("ts"),
            "kind": str(row.get("kind") or "out")[:12],
            "src": str(row.get("src") or "")[:40],
            "text": text,
        })
    return out


PUMP_COINS = ("BTC", "ETH", "LTC", "TAO")
PUMP_THRESH = {
    "BTC": (0.012, 0.025),
    "ETH": (0.018, 0.035),
    "LTC": (0.025, 0.050),
    "TAO": (0.030, 0.060),
}
_pump_lock = threading.Lock()
_pump_last_push = 0.0


def _pump_status_mids():
    """Mids + BBP from hunter status files. No extra Hyperliquid REST."""
    out = {c: {"mid": None, "bbp": None} for c in PUMP_COINS}
    files = (
        BN_SWING_STATUS_FILE,
        HELLIOT_STATUS_FILE,
        SURFN_STATUS_FILE,
        DMAN_STATUS_FILE,
        ONLYUP_STATUS_FILE,
        SHORTN_STATUS_FILE,
        os.path.join(CONF_DIR, "dman_maker_status.json"),
    )
    for path in files:
        st = _read_json_file(path)
        if not isinstance(st, dict):
            continue
        positions = st.get("positions") if isinstance(st.get("positions"), dict) else {}
        for coin in PUMP_COINS:
            pos = positions.get(coin) if isinstance(positions.get(coin), dict) else {}
            if not isinstance(pos, dict):
                pos = {}
            mid = _as_f(pos.get("mid_price") or pos.get("mark") or st.get("mid_price"))
            bbp = _as_f(pos.get("bbp"))
            if out[coin]["mid"] is None and mid:
                out[coin]["mid"] = mid
            if out[coin]["bbp"] is None and bbp is not None:
                out[coin]["bbp"] = bbp
    return out


def _pump_push_tape(now, mids):
    global _pump_last_push
    if now - _pump_last_push < 8:
        return
    row = {"t": now}
    n = 0
    for c in PUMP_COINS:
        px = (mids.get(c) or {}).get("mid")
        if px:
            row[c] = round(float(px), 6)
            n += 1
    if not n:
        return
    with _pump_lock:
        data = _read_json_file(PUMP_TAPE_FILE)
        hist = list((data or {}).get("samples") or [])
        hist.append(row)
        cutoff = now - 3 * 3600
        hist = [s for s in hist if isinstance(s, dict) and float(s.get("t") or 0) >= cutoff][-900:]
        _write_json_file(PUMP_TAPE_FILE, {"samples": hist, "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")})
        _pump_last_push = now


def _pump_px(hist, coin, ago, now):
    want = now - ago
    best = None
    best_dt = 1e9
    for row in hist:
        if not isinstance(row, dict):
            continue
        t = _as_f(row.get("t"))
        px = _as_f(row.get(coin))
        if t is None or not px:
            continue
        dt = abs(t - want)
        if dt < best_dt:
            best_dt = dt
            best = px
    if best is None or best_dt > max(45.0, ago * 0.55):
        return None
    return best


def _pump_ret(now_px, old_px):
    if not now_px or not old_px or old_px <= 0:
        return None
    return (now_px / old_px) - 1.0


def _pump_state(r5, r15, r60, bbp, t15, t1h):
    pumping = (r15 is not None and r15 >= t15) or (r5 is not None and r5 >= t15 * 0.5)
    going = r5 is not None and r5 >= 0.001
    elevated = r60 is not None and r60 >= t1h * 0.65
    stalling = r5 is not None and r5 <= 0.0012
    fading = r5 is not None and r5 < 0
    stretched = bbp is not None and bbp >= 0.82
    if pumping and going:
        why = []
        if r15 is not None:
            why.append(f"15m {r15*100:+.2f}%")
        if r5 is not None:
            why.append(f"5m {r5*100:+.2f}%")
        return "pump", " · ".join(why) or "spike"
    if elevated and (stalling or fading or stretched):
        bits = []
        if r60 is not None:
            bits.append(f"1h {r60*100:+.2f}%")
        if fading:
            bits.append("fading")
        elif stalling:
            bits.append("stalled")
        if stretched:
            bits.append(f"BBP {bbp:.2f}")
        return "post", " · ".join(bits) or "after spike"
    if r15 is not None and r15 >= t15 * 0.55 and stretched:
        return "post", f"15m {r15*100:+.2f}% · BBP {bbp:.2f}"
    return "quiet", "no recent spike"


def _safe_payload(fn):
    """Home gauges must not 500 the whole desk."""
    try:
        return fn()
    except Exception:
        return {}


def _pump_payload():
    """Recognize pumps / post-pumps. Never writes BIAS or places orders."""
    now = time.time()
    mids = _pump_status_mids()
    try:
        _pump_push_tape(now, mids)
    except Exception:
        pass
    hist = []
    try:
        hist = list((_read_json_file(PUMP_TAPE_FILE) or {}).get("samples") or [])
    except Exception:
        hist = []
    coins = {}
    rank = {"quiet": 0, "pump": 1, "post": 2}
    regime = "quiet"
    for coin in PUMP_COINS:
        px = (mids.get(coin) or {}).get("mid")
        bbp = (mids.get(coin) or {}).get("bbp")
        r5 = _pump_ret(px, _pump_px(hist, coin, 5 * 60, now))
        r15 = _pump_ret(px, _pump_px(hist, coin, 15 * 60, now))
        r60 = _pump_ret(px, _pump_px(hist, coin, 60 * 60, now))
        t15, t1h = PUMP_THRESH.get(coin) or (0.03, 0.06)
        state, why = _pump_state(r5, r15, r60, bbp, t15, t1h)
        if rank.get(state, 0) > rank.get(regime, 0):
            regime = state
        coins[coin] = {
            "state": state,
            "why": why,
            "mid": px,
            "bbp": round(bbp, 3) if bbp is not None else None,
            "ret_5m": round(r5 * 100, 3) if r5 is not None else None,
            "ret_15m": round(r15 * 100, 3) if r15 is not None else None,
            "ret_1h": round(r60 * 100, 3) if r60 is not None else None,
        }
    n_hist = len(hist)
    wait = n_hist < 8
    payload = {
        "kind": "pump",
        "title": "Post pump gauge",
        "regime": "quiet" if wait else regime,
        "coins": coins,
        "why": "need ~15m of mids — watching hunter marks" if wait else (
            "post-pump: spike already happened, now stall/fade" if regime == "post"
            else ("live spike" if regime == "pump" else "no recent spike")
        ),
        "samples": n_hist,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "orders": False,
    }
    try:
        _write_json_file(PUMP_GAUGE_FILE, payload)
    except Exception:
        pass
    return payload


def _parse_posted_bias(text):
    t = str(text or "").upper().replace(" ", "")
    if not t:
        return None
    for token in ("/FLAT", "/LONG", "/SHORT"):
        if token in t:
            return token[1:]
    for word in ("FLAT", "LONG", "SHORT"):
        if re.search(r"(?:^|[^A-Z])" + word + r"(?:[^A-Z]|$)", str(text or "").upper()):
            return word
    return None


def _grok_bias_from_feed():
    data = _read_json_file(CONDOR_FEED_FILE) or {}
    rows = data.get("lines") if isinstance(data.get("lines"), list) else []
    if not rows and isinstance(data, list):
        rows = data
    picked = None
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        src = str(row.get("src") or "")
        kind = str(row.get("kind") or "")
        text = str(row.get("text") or "")
        mode = _parse_posted_bias(text)
        if not mode:
            continue
        if kind == "tg-in":
            return {
                "bias": mode,
                "updated": row.get("ts"),
                "source": "bias-bot",
                "writes_hunters": False,
            }
        if picked:
            continue
        if kind == "tg-out" and ("BIAS BOT" in src.upper() or "condor1" in src.lower()) and "Desk BIAS" in text:
            picked = {
                "bias": mode,
                "updated": row.get("ts"),
                "source": "bias-bot",
                "writes_hunters": False,
            }
        elif kind == "grok" and not picked:
            picked = {
                "bias": mode,
                "updated": row.get("ts"),
                "source": src or "grok",
                "writes_hunters": False,
            }
    if picked:
        return picked
    xlate = _read_json_file(os.path.join(CONF_DIR, "condor_xlate.json")) or {}
    nodes = xlate.get("nodes") if isinstance(xlate.get("nodes"), dict) else {}
    desk = nodes.get("desk-bias") if isinstance(nodes.get("desk-bias"), dict) else {}
    mode = str(desk.get("cmd") or "").upper()
    if mode in ("LONG", "SHORT", "FLAT"):
        return {
            "bias": mode,
            "updated": desk.get("ts"),
            "source": "bias-bot",
            "writes_hunters": False,
        }
    return {}


def _grok_bias_read():
    raw = _read_json_file(GROK_BIAS_FILE) or {}
    if not isinstance(raw, dict):
        raw = {}
    bias = str(raw.get("bias") or "").upper()
    if bias not in ("LONG", "SHORT", "FLAT"):
        raw = _grok_bias_from_feed()
        bias = str(raw.get("bias") or "").upper()
        if bias not in ("LONG", "SHORT", "FLAT"):
            bias = None
    return {
        "bias": bias,
        "updated": raw.get("updated"),
        "source": raw.get("source") or "grok",
        "writes_hunters": False,
    }


def _condor_payload():
    data = _read_json_file(CONDOR_GAUGE_FILE) or {}
    data["running"] = get_container_running("hummingbot-condor")
    if not data.get("kind"):
        data["kind"] = "condor"
        data["title"] = "Condor"
    data["setup"] = _public_condor_setup()
    data["feed"] = _condor_feed_public()
    xlate = _read_json_file(os.path.join(CONF_DIR, "condor_xlate.json")) or {}
    data["xlate"] = xlate.get("nodes") if isinstance(xlate.get("nodes"), dict) else {}
    gb = _grok_bias_read()
    data["grok_cmd"] = gb.get("bias")
    data["grok_cmd_ts"] = gb.get("updated")
    data["grok_writes_hunters"] = False
    return data


def _clip_condor_mode(mode, hunter_mode):
    mode = str(mode or "FLAT").upper()
    if mode not in ("LONG", "SHORT", "FLAT"):
        mode = "FLAT"
    if hunter_mode == "long" and mode == "SHORT":
        return "FLAT"
    if hunter_mode == "short" and mode == "LONG":
        return "FLAT"
    return mode


def _condor_wired_hunters(nodes, edges):
    by_id = {}
    for n in nodes or []:
        if isinstance(n, dict) and n.get("id"):
            by_id[str(n["id"])] = n
    srcs = set()
    for nid, n in by_id.items():
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        if nid == "gauge-condor" or (n.get("type") == "gauge" and data.get("kind") == "condor"):
            srcs.add(nid)
    if not srcs:
        return []

    def coin_for(bot_id):
        for e in edges or []:
            if str(e.get("target") or "") == bot_id and str(e.get("targetHandle") or "") == "coin":
                src = by_id.get(str(e.get("source") or ""))
                if not src:
                    continue
                d = src.get("data") if isinstance(src.get("data"), dict) else {}
                coin = str(d.get("coin") or d.get("name") or "").upper()
                if coin:
                    return coin
        return None

    found = {}

    def add(bot_id, coin):
        if bot_id in CONDOR_HUNTERS:
            found[bot_id] = coin

    for e in edges or []:
        if str(e.get("source") or "") not in srcs:
            continue
        if str(e.get("sourceHandle") or "bias") not in ("bias", ""):
            continue
        tgt = by_id.get(str(e.get("target") or ""))
        if not tgt:
            continue
        th = str(e.get("targetHandle") or "")
        if tgt.get("type") == "bot" and th in ("bias", ""):
            bid = str(tgt.get("id") or "")
            add(bid, coin_for(bid))
        elif tgt.get("type") == "bias" and th in ("in", "bias", ""):
            data = tgt.get("data") if isinstance(tgt.get("data"), dict) else {}
            bot_id = str(data.get("botId") or "")
            if not bot_id and str(tgt.get("id") or "").startswith("bias-"):
                bot_id = str(tgt.get("id"))[5:]
            for e2 in edges or []:
                if str(e2.get("source") or "") != str(tgt.get("id") or ""):
                    continue
                t2 = by_id.get(str(e2.get("target") or ""))
                if t2 and t2.get("type") == "bot":
                    bid = str(t2.get("id") or "")
                    add(bid, coin_for(bid))
            add(bot_id, None)
    return list(found.items())


def _apply_condor_gauge(nodes, edges):
    """Write Condor per-coin bias only to hunters wired to gauge-condor."""
    gauge = _read_json_file(CONDOR_GAUGE_FILE) or {}
    bias = gauge.get("bias") if isinstance(gauge.get("bias"), dict) else {}
    if not bias:
        return []
    applied = []
    for bot_id, wired_coin in _condor_wired_hunters(nodes, edges):
        spec = CONDOR_HUNTERS.get(bot_id)
        if not spec:
            continue
        coins = [wired_coin] if wired_coin else list(spec["coins"])
        path = spec["bias"]
        current = {c: "FLAT" for c in spec["coins"]}
        raw = _read_json_file(path) or {}
        for c in spec["coins"]:
            v = str(raw.get(c) or "").upper()
            if v in ("LONG", "SHORT", "FLAT"):
                current[c] = v
        changed = False
        for coin in coins:
            if coin not in spec["coins"]:
                continue
            mode = _clip_condor_mode(bias.get(coin), spec["mode"])
            if current.get(coin) != mode:
                current[coin] = mode
                changed = True
        if not changed:
            continue
        payload = dict(current)
        payload["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        payload["source"] = "condor"
        try:
            _write_json_file(path, payload)
            _sql_bias_write(bot_id, payload)
            applied.append(f"{bot_id} " + " ".join(f"{c}={current[c]}" for c in coins if c in spec["coins"]))
        except OSError:
            pass
    return applied


def _apply_wire_graph(nodes, edges):
    """Map abstract wires onto live hunter files. Income is recorded only (no HL send)."""
    by_id = {}
    for n in nodes or []:
        if isinstance(n, dict) and n.get("id"):
            by_id[str(n["id"])] = n
    applied = []
    runtime = {"updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"), "bots": {}}
    sl_payload = _read_json_file(BOT_SL_FILE)
    if not isinstance(sl_payload, dict):
        sl_payload = {}
    sl_changed = False

    def _src(bot_id, handle):
        for e in edges or []:
            if str(e.get("target") or "") == bot_id and str(e.get("targetHandle") or "") == handle:
                return by_id.get(str(e.get("source") or ""))
        return None

    def _dst(bot_id, handle):
        for e in edges or []:
            if str(e.get("source") or "") == bot_id and str(e.get("sourceHandle") or "") in handle:
                return by_id.get(str(e.get("target") or ""))
        return None

    for n in nodes or []:
        if not isinstance(n, dict) or n.get("type") != "bot":
            continue
        bid = str(n.get("id") or "")
        if not bid:
            continue
        coin_n = _src(bid, "coin")
        sl_n = _src(bid, "sl")
        moon_n = _src(bid, "moon")
        wallet_n = _src(bid, "wallet")
        income_n = _dst(bid, ("income", "funds"))
        risk_n = _dst(bid, ("risk",))
        coins_wired = []
        for e in edges or []:
            if str(e.get("target") or "") != bid or str(e.get("targetHandle") or "") != "coin":
                continue
            src = by_id.get(str(e.get("source") or ""))
            if not isinstance(src, dict):
                continue
            sd = src.get("data") or {}
            c = str(sd.get("coin") or sd.get("name") or "").upper()
            if c and c not in coins_wired:
                coins_wired.append(c)
        coin = None
        if coin_n:
            coin = ((coin_n.get("data") or {}).get("coin") or (coin_n.get("data") or {}).get("name"))
            if coin:
                coin = str(coin).upper()
        if not coin and coins_wired:
            coin = coins_wired[0]
        sl = None
        if sl_n:
            try:
                sl = float((sl_n.get("data") or {}).get("pct"))
            except (TypeError, ValueError):
                sl = None
        moon = None
        if moon_n:
            try:
                moon = float((moon_n.get("data") or {}).get("pct"))
            except (TypeError, ValueError):
                moon = None
        win = None
        if wallet_n:
            win = (wallet_n.get("data") or {}).get("address") or (wallet_n.get("data") or {}).get("name")
        wout = None
        if income_n:
            wout = (income_n.get("data") or {}).get("address") or (income_n.get("data") or {}).get("name") or income_n.get("id")
        runtime["bots"][bid] = {
            "coin": coin,
            "coins": coins_wired,
            "sl_roe_pct": sl,
            "moon_pct": moon,
            "wallet_in": win,
            "income_wallet": wout,
            "income_transfer": False,
            "income_note": "mapped on the canvas — no automatic Hyperliquid send yet",
            "risk_skim": None,
        }
        if risk_n:
            rd = risk_n.get("data") if isinstance(risk_n.get("data"), dict) else {}
            mode = str(rd.get("skim_mode") or "win_pct")
            if mode not in ("win_pct", "two_wins"):
                mode = "win_pct"
            runtime["bots"][bid]["risk_skim"] = {
                "wallet": risk_n.get("id"),
                "address": rd.get("address") or "",
                "mode": mode,
                "skim_pct": 100 if mode == "two_wins" else rd.get("skim_pct"),
                "every_h": rd.get("skim_every_h"),
                "min_usd": rd.get("skim_min_usd"),
                "streak_wins": 2 if mode == "two_wins" else None,
                "armed": bool(rd.get("armed")),
                "send": False,
                "note": (
                    "two realized wins in a row → take 2nd win profit only"
                    if mode == "two_wins"
                    else "mapped — no automatic Hyperliquid send"
                ),
            }
        if sl is not None and 1.0 <= sl <= 90.0:
            row = sl_payload.get(bid) if isinstance(sl_payload.get(bid), dict) else {}
            if row.get("sl_roe_pct") != round(sl, 2):
                row["sl_roe_pct"] = round(sl, 2)
                row["updated"] = runtime["updated"]
                row["source"] = "wire"
                sl_payload[bid] = row
                sl_changed = True
                applied.append(f"{bid} SL −{sl:g}%")
                _sql_kv_set(bid, "sl_roe_pct", round(sl, 2), "wire")
        if moon is not None and bid in _MOON_BOTS and 1.0 <= moon <= 500.0:
            note = _arm_hunter_moon(bid, moon, coin, runtime["updated"])
            if note:
                applied.append(note)
    for bid in _MOON_BOTS:
        if _src(bid, "moon"):
            continue
        cur = _load_hunter_moon(bid)
        armed = [c for c, row in cur.items() if isinstance(row, dict) and row.get("on")]
        if not armed:
            continue
        for c in armed:
            row = dict(cur.get(c) or {})
            row["on"] = False
            cur[c] = row
        _save_hunter_moon(bid, cur)
        applied.append(bid.replace("hummingbot-", "") + " moon off")
    if sl_changed:
        try:
            _write_json_file(BOT_SL_FILE, sl_payload)
        except OSError:
            pass
    skim = {}
    for bid, row in (runtime.get("bots") or {}).items():
        if isinstance(row, dict) and row.get("risk_skim"):
            skim[bid] = row["risk_skim"]
    try:
        with _risk_file_lock():
            _write_json_file(RISK_SKIM_FILE, _merge_risk_ledger(skim))
    except OSError:
        pass
    try:
        _write_json_file(WIRE_RUNTIME_FILE, runtime)
    except OSError:
        pass
    applied.extend(_apply_condor_gauge(nodes, edges))
    applied.extend(_apply_condor_setup(nodes, edges))
    applied.extend(_apply_copy_trader_wires(nodes, edges))
    return applied


def _apply_copy_trader_wires(nodes, edges):
    """If any leader is wired into Copy TRADER, follow only those T#s. Does not close mirrors."""
    by_id = {str(n.get("id")): n for n in (nodes or []) if isinstance(n, dict) and n.get("id")}
    wired = []
    for e in edges or []:
        if not isinstance(e, dict):
            continue
        if str(e.get("target") or "") != "hummingbot-hl":
            continue
        th = str(e.get("targetHandle") or "")
        if th != "trader" and not th.startswith("trader-"):
            continue
        src = by_id.get(str(e.get("source") or ""))
        if not src:
            continue
        data = src.get("data") if isinstance(src.get("data"), dict) else {}
        try:
            num = int(data.get("num"))
        except (TypeError, ValueError):
            num = None
        if num:
            wired.append(num)
    if not wired:
        return []
    cfg = load_hl_config()
    n = len(cfg.get("trader_addresses") or [])
    want = sorted({x for x in wired if 1 <= x <= n})
    if not want:
        return []
    unfollowed = [i for i in range(1, n + 1) if i not in set(want)]
    current = set(effective_unfollowed_traders(cfg))
    if current == set(unfollowed):
        return []
    save_hl_runtime_flags({"unfollowed_traders": unfollowed})
    label = ", ".join(f"T#{i}" for i in want)
    return [f"copy follow {label}"]


@app.route("/api/wire/wallets", methods=["GET", "POST", "DELETE"])
def api_wire_wallets():
    """Canvas wallets + register unique Hyperliquid subaccounts (paste 0x from HL)."""
    if request.method == "GET":
        slots = _hunter_wallet_slots()
        occupied = [
            {"address": a, "name": n}
            for a, n in _occupied_hl_addresses().items()
        ]
        return jsonify({
            "ok": True,
            "wallets": _load_extra_wallets(),
            "hunters": slots,
            "empty_slots": [s for s in slots if s.get("empty")],
            "occupied": occupied,
        })
    if request.method == "DELETE":
        body = request.get_json(silent=True) or {}
        wid = str(body.get("id") or request.args.get("id") or "").strip()
        if not wid.startswith("wallet-extra-"):
            return jsonify({"ok": False, "error": "not an extra wallet"}), 400
        rows = [w for w in _load_extra_wallets() if w.get("id") != wid]
        _save_extra_wallets(rows)
        return jsonify({"ok": True, "wallets": rows, "message": "wallet deleted"})
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "Wallet").strip()[:40] or "Wallet"
    addr = str(body.get("address") or "").strip()
    if addr and not _valid_hl_address(addr):
        return jsonify({"ok": False, "error": "address must be 0x + 40 hex chars"}), 400
    use_vault = bool(body.get("use_vault", True))
    bind_id = str(body.get("bind_id") or "").strip()
    restart = bool(body.get("restart"))
    exclude = bind_id or str(body.get("id") or "").strip() or None
    if addr:
        taken = _occupied_hl_addresses(exclude_id=exclude).get(addr.lower())
        if taken:
            return jsonify({"ok": False, "error": f"address already used by {taken}"}), 400
    if bind_id:
        if not addr:
            return jsonify({"ok": False, "error": "subaccount address required"}), 400
        out = _confiig_set_address(bind_id, addr, use_vault=use_vault, restart=restart)
        if not out.get("ok"):
            return jsonify(out), 400
        return jsonify(out)
    color = str(body.get("color") or "#f0b90b")[:20]
    wid = str(body.get("id") or "").strip()
    rows = _load_extra_wallets()
    if wid and not wid.startswith("wallet-extra-"):
        return jsonify({"ok": False, "error": "not an extra wallet"}), 400
    if not wid:
        wid = "wallet-extra-" + secrets.token_hex(4)
    found = False
    for row in rows:
        if row.get("id") == wid:
            row["name"] = name
            row["address"] = addr
            row["use_vault"] = use_vault
            row["color"] = color
            found = True
            break
    if not found:
        rows.append({
            "id": wid,
            "name": name,
            "address": addr,
            "use_vault": use_vault,
            "color": color,
            "custom": True,
        })
    _save_extra_wallets(rows)
    row = next((w for w in rows if w.get("id") == wid), None)
    return jsonify({
        "ok": True,
        "wallet": row,
        "wallets": rows,
        "bound": False,
        "message": ("updated " if found else "added ") + name,
    })


@app.route("/api/condor/gauge", methods=["GET"])
def api_condor_gauge():
    return jsonify(_condor_payload())


@app.route("/api/condor/xlate", methods=["POST"])
def api_condor_xlate():
    """Run a translator command (LONG/SHORT/FLAT/CLOSE/OPEN100%) on wired hunters."""
    body = request.get_json(silent=True) or {}
    try:
        r = requests.post("http://hummingbot-condor:8088/xlate", json=body, timeout=20)
        data = r.json() if r.content else {}
        return jsonify(data), (200 if r.ok else r.status_code)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 502


@app.route("/api/condor/tick", methods=["POST"])
def api_condor_tick():
    """Ask the Condor container to tick now (heuristic/Grok). Does not place orders."""
    try:
        r = requests.post("http://hummingbot-condor:8088/tick", json={}, timeout=40)
        data = r.json() if r.content else {}
        return jsonify(data), (200 if r.ok else r.status_code)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "gauge": _condor_payload()}), 502


@app.route("/api/condor/setup", methods=["GET", "POST"])
def api_condor_setup():
    """Wire Telegram / Grok bots for Condor. Tokens live in conf/condor_setup.json."""
    if request.method == "GET":
        return jsonify(_public_condor_setup())
    body = request.get_json(silent=True) or {}
    out = _upsert_condor_bot(body)
    if not out.get("ok"):
        return jsonify(out), 400
    return jsonify(out)


def _nav_hunters():
    """Hummingbots on the Wire desk canvas. Falls back to the full registry."""
    hunters = _load_registry()
    by_id = {str(h.get("id")): h for h in hunters if h.get("id")}
    graph = _read_json_file(WIRE_GRAPH_FILE) or {}
    ids = []
    for n in graph.get("nodes") or []:
        if not isinstance(n, dict) or n.get("type") != "bot":
            continue
        bid = str(n.get("id") or "")
        if bid and bid in by_id and bid not in ids:
            ids.append(bid)
    if not ids:
        ids = [h["id"] for h in hunters if h.get("id")]
    ps = _docker_ps_map()
    out = []
    for bid in ids:
        h = by_id.get(bid) or {"id": bid, "name": bid, "href": "/confiig"}
        state = ((ps.get(bid) or {}).get("state") or "").lower()
        row = {
            "id": bid,
            "name": h.get("name") or bid,
            "href": h.get("href") or "/confiig",
            "icon": h.get("icon") or "",
            "color": h.get("color") or "#9dffc0",
            "running": state == "running",
        }
        if bid == "hummingbot-hl" or h.get("kind") == "copy":
            row["submenu"] = [
                {"name": "Leaders", "href": "/traders"},
                {"name": "Audit", "href": "/audit"},
                {"name": "Trade History", "href": "/trades"},
            ]
        out.append(row)
    return out


@app.route("/api/nav")
def api_nav():
    """Header hummingbot list. Follows Wire desk graph + registry."""
    return jsonify({
        "ok": True,
        "hunters": _nav_hunters(),
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


@app.route("/api/wire/catalog", methods=["GET"])
def api_wire_catalog():
    """Hunters + extra wallets for the Wire toolbox. Grows when tools add a sub/hunter."""
    hunters = _load_registry()
    v2_rows, _v2_cap = _v2_sleeper_meta()
    return jsonify({
        "ok": True,
        "hunters": hunters,
        "v2_sleepers": v2_rows,
        "wallets": _load_extra_wallets(),
        "traders": _copy_leader_cards(),
        "fields": {h["id"]: h.get("controller_fields") or [] for h in hunters},
    })


@app.route("/api/hunter_gate", methods=["POST"])
def api_hunter_gate():
    """Docker pause or unpause one hunter. Does not flatten, stop, or start."""
    data = request.get_json(silent=True) or {}
    bot_id = str(data.get("id") or "").strip()
    if bot_id not in HUNTER_GATE_IDS:
        return jsonify({
            "ok": False,
            "id": bot_id,
            "error": "not a hunter",
            "closes": False,
            "positions_untouched": True,
            "message": "not a hunter on this matrix",
        }), 400
    if not isinstance(data.get("active"), bool):
        return jsonify({
            "ok": False,
            "id": bot_id,
            "error": "active must be true or false",
            "closes": False,
            "positions_untouched": True,
            "message": "active must be true or false",
        }), 400
    active = data["active"]
    st = get_container_state(bot_id)

    def reply(ok, deactivated, message, code=200):
        body = {
            "ok": ok,
            "id": bot_id,
            "deactivated": deactivated,
            "active": not deactivated,
            "closes": False,
            "positions_untouched": True,
            "hl_ip": "none" if deactivated else "resumes",
            "message": message,
        }
        if not ok:
            body["error"] = message
        return jsonify(body), code

    if active and not st["paused"]:
        return reply(True, False, bot_id + (
            " ACTIVE green. Spring does not touch positions."
            if st["running"]
            else " is off. Activate does not start it. Spring does not touch positions."
        ))
    if not active and st["paused"]:
        return reply(True, True, bot_id + " DEACTIVATED red - 0 HL IP. Spring does not touch positions.")
    if not active and not st["running"]:
        return reply(True, False, bot_id + " already off - 0 HL IP. Spring does not touch positions.")
    verb = "unpause" if active else "pause"
    try:
        proc = subprocess.run(
            ["docker", verb, bot_id],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as exc:
        return reply(False, st["paused"], "docker " + verb + " failed: " + str(exc)[:160], 400)
    if proc.returncode != 0:
        return reply(False, st["paused"], "docker " + verb + " failed", 400)
    if active:
        return reply(True, False, bot_id + " ACTIVE green. Spring does not touch positions.")
    return reply(True, True, bot_id + " DEACTIVATED red - 0 HL IP. Spring does not touch positions.")


@app.route("/api/wire/sleeper", methods=["POST"])
def api_wire_sleeper():
    """Start or stop one hummingbot-v2- container. Live hunters stay untouched."""
    if not flask_writes_on():
        return jsonify({
            "ok": False,
            "error": "Flask WRITE MODE is off",
            "closes": False,
            "positions_untouched": True,
        }), 400
    data = request.get_json(silent=True) or {}
    bot_id = str(data.get("id") or "").strip()
    op = str(data.get("op") or "").strip()
    rows, cap = _v2_sleeper_meta()
    allowed = {str(r.get("id") or "") for r in rows}
    if not bot_id.startswith("hummingbot-v2-") or bot_id not in allowed:
        return jsonify({
            "ok": False,
            "error": "not a V2 sleeper — live hunters untouched",
            "closes": False,
            "positions_untouched": True,
        }), 400
    if op == "start":
        st = get_container_state(bot_id)
        running_n = _v2_running_count()
        if not st["running"] and not st["paused"] and running_n >= cap:
            return jsonify({
                "ok": False,
                "error": "V2 cap %d running — stop one first" % cap,
                "closes": False,
                "running": running_n,
                "cap": cap,
                "positions_untouched": True,
            }), 400
        try:
            proc = subprocess.run(
                ["docker", "start", bot_id],
                capture_output=True, text=True, timeout=20,
            )
        except Exception:
            proc = None
        ok = bool(proc and proc.returncode == 0)
        return jsonify({
            "ok": ok,
            "id": bot_id,
            "op": "start",
            "closes": False,
            "positions_untouched": True,
            "message": "V2 sleeper started (no keys until wallet wired)" if ok else "docker start failed — is the container created?",
            "error": None if ok else "docker start failed — is the container created?",
        }), (200 if ok else 400)
    if op == "stop":
        try:
            subprocess.run(
                ["docker", "stop", "-t", "2", bot_id],
                capture_output=True, text=True, timeout=20,
            )
        except Exception:
            pass
        return jsonify({
            "ok": True,
            "id": bot_id,
            "op": "stop",
            "closes": False,
            "positions_untouched": True,
            "message": "V2 sleeper asleep",
        })
    return jsonify({
        "ok": False,
        "error": "op start|stop",
        "closes": False,
        "positions_untouched": True,
    }), 400


@app.route("/api/wire/bias", methods=["POST"])
def api_wire_bias():
    """Generic BIAS write for any registered hunter."""
    data = request.get_json(silent=True) or {}
    bot_id = str(data.get("id") or data.get("bot_id") or "").strip()
    h = registry_hunter(bot_id) or _ensure_registry_hunter(bot_id)
    if not h:
        return jsonify({"ok": False, "error": "unknown hunter"}), 400
    bias = str(data.get("bias") or "").upper().strip()
    if bias not in ("LONG", "SHORT", "FLAT"):
        return jsonify({"ok": False, "error": "bias must be LONG, SHORT, or FLAT"}), 400
    mode = _hunter_bias_mode(bot_id)
    if mode == "long" and bias == "SHORT":
        return jsonify({"ok": False, "error": "this hunter is long-only — SHORT is not used"}), 400
    if mode == "short" and bias == "LONG":
        return jsonify({"ok": False, "error": "this hunter is short-only — LONG is not used"}), 400
    coins = [str(c).upper() for c in (h.get("coins") or [])]
    coin = str(data.get("coin") or "").upper().strip()
    if coin and coins and coin not in coins:
        return jsonify({"ok": False, "error": "coin not on this hunter"}), 400
    if not coins:
        coins = [coin] if coin else []
    if not coin and not coins:
        return jsonify({"ok": False, "error": "coin required"}), 400
    path = os.path.join(CONF_DIR, os.path.basename(h.get("bias_file") or (bot_id.replace("hummingbot-", "") + "_bias.json")))
    current = _hunter_bias_payload(bot_id, coins=coins)
    if coin:
        current[coin] = bias
    else:
        for c in coins:
            current[c] = bias
    payload = dict(current)
    payload.update({
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "wire",
    })
    try:
        _sql_bias_write(bot_id, payload)
        _write_json_file(path, payload)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    label = coin or ",".join(coins)
    return jsonify({"ok": True, "message": f"{label} bias set to {bias}", "bias": payload, "id": bot_id})


_WIRE_KEEP = (
    "name", "icon", "color", "hasBias", "kind", "botId", "mode", "href",
    "title", "toolId", "coin", "coins", "pct", "values", "bias",
    "address", "custom", "use_vault",
    "skim_mode", "skim_pct", "skim_every_h", "skim_min_usd", "armed",
    "top_scale",
    "chat_ids", "model", "has_token", "has_key",
    "cmd", "last_raw", "last_ts", "max", "to", "smtp", "hunter", "w", "h", "extra", "band",
    "num", "title", "why", "short", "roster", "followed", "alumni",
    "day_pnl", "week_pnl", "month_pnl", "our_wins", "our_losses", "our_net",
    "coins",
)
_OVERLAY_TYPES = ("agent", "xlate", "frame", "circle")
_MOON_BOTS = ("hummingbot-onlyup", "hummingbot-surfn")


def _slim_wire_canvas(nodes_in, edges_in):
    slim_nodes = []
    for n in (nodes_in or [])[:160]:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "")[:80]
        ntype = str(n.get("type") or "")[:40]
        if not nid or not ntype:
            continue
        if ntype == "homeband":
            continue
        pos = n.get("position") if isinstance(n.get("position"), dict) else {}
        try:
            x = float(pos.get("x") or 0)
            y = float(pos.get("y") or 0)
        except (TypeError, ValueError):
            x, y = 0.0, 0.0
        raw = n.get("data") if isinstance(n.get("data"), dict) else {}
        data = {k: raw[k] for k in _WIRE_KEEP if k in raw}
        slim_nodes.append({"id": nid, "type": ntype, "position": {"x": x, "y": y}, "data": data})
    slim_edges = []
    for e in (edges_in or [])[:200]:
        if not isinstance(e, dict):
            continue
        sid = str(e.get("source") or "")[:80]
        tid = str(e.get("target") or "")[:80]
        if not sid or not tid:
            continue
        slim_edges.append({
            "id": str(e.get("id") or "")[:120],
            "source": sid,
            "sourceHandle": e.get("sourceHandle"),
            "target": tid,
            "targetHandle": e.get("targetHandle"),
        })
    return {"v": 5, "nodes": slim_nodes, "edges": slim_edges}


def _overlay_node_keep(n):
    if not isinstance(n, dict):
        return False
    ntype = n.get("type")
    data = n.get("data") if isinstance(n.get("data"), dict) else {}
    if ntype in _OVERLAY_TYPES:
        return True
    if ntype == "output" and data.get("kind") in ("stream", "xml"):
        return True
    if ntype == "gauge" and data.get("kind") in ("condor", "hmm", "vol", "bb", "pump"):
        return True
    if ntype == "pct" and data.get("kind") == "moon":
        return True
    if ntype == "trader":
        return True
    if ntype == "health":
        return True
    if ntype in ("homebot", "homestat", "backup"):
        return True
    if ntype in ("risk_wallet", "desk_bias"):
        return True
    if ntype == "wallet" and data.get("custom"):
        return True
    return False


def _overlay_saved_canvas(live):
    """Keep Telegram / Translator / Stream (and extra gauges) across live reconstruct."""
    live = live if isinstance(live, dict) else {}
    saved = _read_json_file(WIRE_GRAPH_FILE) or {}
    live_nodes = [n for n in (live.get("nodes") or []) if isinstance(n, dict) and n.get("id")]
    live_ids = {str(n["id"]) for n in live_nodes}
    extra_nodes = []
    extra_ids = set()
    for n in saved.get("nodes") or []:
        nid = str(n.get("id") or "")
        if not nid or nid in live_ids:
            continue
        if _overlay_node_keep(n):
            extra_nodes.append(n)
            extra_ids.add(nid)
    live_ek = {_edge_key(e) for e in (live.get("edges") or []) if isinstance(e, dict)}
    extra_edges = []
    for e in saved.get("edges") or []:
        if not isinstance(e, dict):
            continue
        k = _edge_key(e)
        if k in live_ek:
            continue
        src = str(e.get("source") or "")
        tgt = str(e.get("target") or "")
        sh = str(e.get("sourceHandle") or "")
        th = str(e.get("targetHandle") or "")
        if src in extra_ids or tgt in extra_ids or sh in ("tg", "grok", "out", "moon", "trader", "eclose", "live", "alert", "risk") or th in ("tg", "grok", "moon", "trader", "eclose", "live", "alert", "risk") or str(sh).startswith("trader") or str(th).startswith("trader"):
            extra_edges.append(e)
            live_ek.add(k)
    out = dict(live)
    out["nodes"] = live_nodes + extra_nodes
    out["edges"] = list(live.get("edges") or []) + extra_edges
    return out


def _preserve_overlay_nodes(incoming):
    """Do not let a live reconstruct persist wipe Desk BIAS / RISK / Telegram extras."""
    incoming = incoming if isinstance(incoming, dict) else {}
    prev = _read_json_file(WIRE_GRAPH_FILE) or {}
    in_ids = {str(n.get("id") or "") for n in (incoming.get("nodes") or []) if isinstance(n, dict)}
    extra = []
    for n in prev.get("nodes") or []:
        if not isinstance(n, dict) or not _overlay_node_keep(n):
            continue
        nid = str(n.get("id") or "")
        if not nid or nid in in_ids:
            continue
        used = False
        for e in incoming.get("edges") or []:
            if not isinstance(e, dict):
                continue
            if str(e.get("source") or "") == nid or str(e.get("target") or "") == nid:
                used = True
                break
        if used:
            extra.append(n)
            in_ids.add(nid)
    if not extra:
        return incoming
    out = dict(incoming)
    out["nodes"] = list(incoming.get("nodes") or []) + extra
    return out


@app.route("/api/wire/graph", methods=["GET", "POST"])
def api_wire_graph():
    """Persist the /wire board (node ids, types, positions, edges). Not live equity."""
    if request.method == "GET":
        data = _read_json_file(WIRE_GRAPH_FILE)
        return jsonify(data or {"v": 3, "nodes": [], "edges": []})
    body = request.get_json(silent=True) or {}
    nodes_in = body.get("nodes") if isinstance(body.get("nodes"), list) else []
    edges_in = body.get("edges") if isinstance(body.get("edges"), list) else []
    out = _slim_wire_canvas(nodes_in, edges_in)
    out = _preserve_overlay_nodes(out)
    slim_nodes = out["nodes"]
    slim_edges = out["edges"]
    try:
        _write_json_file(WIRE_GRAPH_FILE, out)
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    if _MATRIX_OK:
        try:
            matrix_store.upsert_layout(slim_nodes)
        except Exception:
            pass
    applied = _apply_wire_graph(slim_nodes, slim_edges)
    return jsonify({"ok": True, "n": len(slim_nodes), "e": len(slim_edges), "applied": applied})


WIRE_BOARD_FILES = {
    "home": os.path.join(CONF_DIR, "wire_home.json"),
    "health": os.path.join(CONF_DIR, "wire_health.json"),
}
WIRE_BOARD_DRAFT = {
    "home": os.path.join(CONF_DIR, "wire_home_draft.json"),
}


def _home_band_for(n):
    if not isinstance(n, dict):
        return "mid"
    data = n.get("data") if isinstance(n.get("data"), dict) else {}
    forced = data.get("band")
    if forced in ("top", "mid", "bot"):
        return forced
    ntype = n.get("type")
    kind = data.get("kind") or ""
    if ntype == "homebot" or kind == "homebot":
        return "mid"
    if ntype == "homestat":
        return "mid" if kind == "book" else "top"
    if ntype == "agent":
        nid = str(n.get("id") or "")
        name = str(data.get("name") or "").upper()
        if nid == "telegram-bot-t84jlc" or "BIAS BOT" in name:
            return "top"
        return "bot"
    if ntype in ("health", "logs", "output"):
        return "bot"
    return "mid"


def _stamp_home_bands(nodes):
    out = []
    for n in nodes or []:
        if not isinstance(n, dict) or n.get("type") == "homeband":
            continue
        row = dict(n)
        data = dict(row.get("data") or {}) if isinstance(row.get("data"), dict) else {}
        data["band"] = _home_band_for({"type": row.get("type"), "data": data})
        row["data"] = data
        out.append(row)
    return out


def _home_compile_report(nodes, edges):
    """Layout-only compile for /. Never writes BIAS, never closes."""
    nodes = _stamp_home_bands(nodes)
    live = _read_json_file(WIRE_BOARD_FILES["home"]) or {}
    live_nodes = [n for n in (live.get("nodes") or []) if isinstance(n, dict) and n.get("id")]
    live_ids = {str(n.get("id")): n for n in live_nodes}
    by_id = {str(n.get("id")): n for n in nodes if n.get("id")}
    top = [n for n in nodes if (n.get("data") or {}).get("band") == "top"]
    mid = [n for n in nodes if (n.get("data") or {}).get("band") == "mid"]
    bot = [n for n in nodes if (n.get("data") or {}).get("band") == "bot"]
    bots = [n for n in mid if n.get("type") == "homebot"]
    health = [n for n in bot if n.get("type") == "health"]
    have_kinds = {
        str((n.get("data") or {}).get("kind") or "")
        for n in top if n.get("type") == "homestat"
    }
    need = ["tokens", "live", "pos", "equity", "upnl", "condor"]
    missing_top = [k for k in need if k not in have_kinds]
    warnings = []
    blockers = []
    if missing_top:
        warnings.append("TOP missing " + ", ".join(missing_top) + " — Home keeps the fallback boxes for those")
    if not bots:
        warnings.append("MIDDLE has no hummingbot cards")
    if not health:
        warnings.append("BOTTOM has no health tile — add Host / Internet / HL tokens from the toolbox")
    if not nodes:
        blockers.append({
            "reason": "Canvas is empty — integrate would wipe Home. Add stats + hunters first.",
            "kind": "layout",
        })
    added = [n for n in nodes if str(n.get("id")) not in live_ids]
    removed = [n for n in live_nodes if str(n.get("id")) not in by_id]
    layout = []
    for n in nodes:
        layout.append({
            "id": n.get("id"),
            "name": (n.get("data") or {}).get("name") or n.get("id"),
            "band": (n.get("data") or {}).get("band"),
            "type": n.get("type"),
            "status": "new" if str(n.get("id")) not in live_ids else "keep",
        })
    for n in removed:
        layout.append({
            "id": n.get("id"),
            "name": (n.get("data") or {}).get("name") or n.get("id"),
            "band": _home_band_for(n),
            "type": n.get("type"),
            "status": "removed",
        })
    hunters = [
        {
            "id": n.get("id"),
            "name": (n.get("data") or {}).get("name") or n.get("id"),
            "positions": [],
            "n_knobs": 0, "n_block": 0, "n_warn": 0,
            "knobs": [], "hits": [],
        }
        for n in bots
    ]
    wiring = [
        {
            "kind": "layout",
            "status": row["status"],
            "label": (row["band"] or "?").upper() + " · " + str(row["name"]),
        }
        for row in layout if row["status"] != "keep"
    ]
    can = len(blockers) == 0
    msg = (
        "Home layout blocked"
        if not can else
        (
            "Home compile OK · TOP "
            + str(len(top)) + " · MIDDLE "
            + str(len(bots)) + " hunter(s) · BOTTOM "
            + str(len(bot))
            + " · does not close · Integrate to push to /"
        )
    )
    return {
        "ok": True,
        "board": "home",
        "can_integrate": can,
        "workaround_used": False,
        "workaround_available": False,
        "parked": [],
        "wiring": wiring,
        "knobs": [],
        "closes": [],
        "blockers": blockers,
        "warnings": warnings,
        "bias": [],
        "hunters": hunters,
        "bands": {
            "top": [(n.get("data") or {}).get("name") or n.get("id") for n in top],
            "mid": [(n.get("data") or {}).get("name") or n.get("id") for n in mid],
            "bot": [(n.get("data") or {}).get("name") or n.get("id") for n in bot],
        },
        "n_new_routes": len(added),
        "n_removed_routes": len(removed),
        "n_knobs": 0,
        "n_pos": 0,
        "n_blockers": len(blockers),
        "n_parked": 0,
        "positions_untouched": True,
        "message": msg,
        "nodes": nodes,
    }


@app.route("/api/wire/board/<name>", methods=["GET", "POST"])
def api_wire_board(name):
    """Home / Health canvases. Layout only — never applies hunter BIAS or CLOSE.

    Home POST saves a draft. / keeps using the last integrated wire_home.json
    until Compile + Integrate on the Home canvas.
    """
    key = str(name or "").strip().lower()
    path = WIRE_BOARD_FILES.get(key)
    if not path:
        return jsonify({"ok": False, "error": "unknown canvas"}), 404
    draft_path = WIRE_BOARD_DRAFT.get(key)
    if request.method == "GET":
        live = _read_json_file(path) or {"v": 5, "board": key, "nodes": [], "edges": []}
        draft = _read_json_file(draft_path) if draft_path else None
        data = draft if (isinstance(draft, dict) and (draft.get("nodes") or [])) else live
        return jsonify({
            "ok": True,
            "board": key,
            "graph": data,
            "live": live,
            "draft": bool(draft_path and data is draft),
            "n": len(data.get("nodes") or []),
            "e": len(data.get("edges") or []),
        })
    body = request.get_json(silent=True) or {}
    nodes_in = body.get("nodes") if isinstance(body.get("nodes"), list) else []
    edges_in = body.get("edges") if isinstance(body.get("edges"), list) else []
    out = _slim_wire_canvas(nodes_in, edges_in)
    out["board"] = key
    if key == "home":
        out["nodes"] = _stamp_home_bands(out.get("nodes") or [])
        try:
            ts = float(body.get("top_scale"))
            if 0.4 <= ts <= 1.5:
                out["top_scale"] = round(ts, 2)
        except (TypeError, ValueError):
            prev = _read_json_file(draft_path or path) or {}
            if prev.get("top_scale") is not None:
                out["top_scale"] = prev.get("top_scale")
        if out.get("top_scale") is None:
            for n in out.get("nodes") or []:
                sc = ((n.get("data") or {}) if isinstance(n, dict) else {}).get("top_scale")
                if sc is not None:
                    try:
                        out["top_scale"] = round(float(sc), 2)
                        break
                    except (TypeError, ValueError):
                        pass
    dest = draft_path or path
    try:
        _write_json_file(dest, out)
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({
        "ok": True,
        "board": key,
        "n": len(out["nodes"]),
        "e": len(out["edges"]),
        "applied": [],
        "draft": dest != path,
        "positions_untouched": True,
    })


def _wire_controllers_map():
    return {h[0]: _ctrl_live_values(h[0]) for h in WIRE_HUNTERS}


def _wire_baseline(graph, controllers=None):
    controllers = controllers if isinstance(controllers, dict) else _wire_controllers_map()
    pct = {}
    bias = {}
    for n in (graph or {}).get("nodes") or []:
        if not isinstance(n, dict):
            continue
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        if n.get("type") == "pct" and data.get("pct") is not None:
            pct[str(n.get("id"))] = data.get("pct")
        if n.get("type") == "bias" and data.get("botId"):
            bias[str(data.get("botId"))] = data.get("bias") or {}
    return {"controllers": controllers, "pct": pct, "bias": bias}


def _park_copy_traders(graph):
    """Keep live Copy T# cards on-canvas, docked to hummingbot-hl T1/T2/T3.

    SQLite layout can park them at y≈−400 (off the top). T#2 may have no
    saved xy at all. Does not change follow wires or close positions.
    """
    if not graph or not isinstance(graph.get("nodes"), list):
        return graph, []
    copy_n = None
    for n in graph["nodes"]:
        if isinstance(n, dict) and str(n.get("id") or "") == "hummingbot-hl":
            copy_n = n
            break
    if not copy_n:
        return graph, []
    cpos = copy_n.get("position") if isinstance(copy_n.get("position"), dict) else {}
    try:
        cx = float(cpos.get("x") or 560)
        cy = float(cpos.get("y") or 40)
    except (TypeError, ValueError):
        cx, cy = 560.0, 40.0
    above_y = cy - 220.0
    sit_above = above_y >= 8.0

    def _target(num):
        if sit_above:
            return cx - 140.0 + (int(num) - 1) * 300.0, above_y
        ty = max(8.0, cy)
        if int(num) == 1:
            return cx - 320.0, ty
        if int(num) == 2:
            return cx + 320.0, ty
        return cx + 620.0, ty

    moved = []
    out = []
    for n in graph["nodes"]:
        if not isinstance(n, dict):
            continue
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        if n.get("type") != "trader":
            out.append(n)
            continue
        try:
            num = int(data.get("num"))
        except (TypeError, ValueError):
            num = None
        pos = n.get("position") if isinstance(n.get("position"), dict) else {}
        try:
            x = float(pos.get("x") or 0)
            y = float(pos.get("y") or 0)
        except (TypeError, ValueError):
            x, y = 0.0, 0.0
        off = y < 8 or y > 4000 or x < -120 or x > 4000
        roster = bool(data.get("roster") and num in (1, 2, 3))
        if roster:
            tx, ty = _target(num)
            far = abs(x - tx) > 480 or abs(y - ty) > 260
            overlap = (x < cx + 292 and x + 280 > cx) and (y < cy + 190 and y + 200 > cy)
            if off or far or overlap:
                n = dict(n)
                n["position"] = {"x": tx, "y": ty}
                moved.append({"id": n.get("id"), "position": n["position"]})
        elif off:
            n = dict(n)
            n["position"] = {"x": max(8.0, cx - 340.0), "y": max(8.0, cy + 170.0)}
            moved.append({"id": n.get("id"), "position": n["position"]})
        out.append(n)
    graph = dict(graph)
    graph["nodes"] = out
    return graph, moved


def _live_graph_with_layout():
    graph = build_live_wire_graph()
    graph = _overlay_saved_canvas(graph)
    if _MATRIX_OK:
        try:
            graph = matrix_store.apply_layout(graph)
        except Exception:
            pass
    graph, moved = _park_copy_traders(graph)
    if moved and _MATRIX_OK:
        try:
            matrix_store.upsert_layout(moved)
        except Exception:
            pass
    return graph


@app.route("/api/wire/live", methods=["GET"])
def api_wire_live():
    """Always the running setup. Layout overlay from SQLite, no hunter writes."""
    try:
        graph = _live_graph_with_layout()
        controllers = _wire_controllers_map()
        baseline = _wire_baseline(graph, controllers)
        sid = None
        if _MATRIX_OK:
            sid = matrix_store.save_snapshot("live", "page load", graph, controllers)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({
        "ok": True,
        "source": "live",
        "graph": graph,
        "baseline": baseline,
        "snapshot_id": sid,
        "n": len(graph.get("nodes") or []),
        "e": len(graph.get("edges") or []),
        "db": _MATRIX_OK,
    })


@app.route("/api/wire/save", methods=["POST"])
def api_wire_save():
    """Named snapshot of the current canvas + live controller knobs."""
    if not _MATRIX_OK:
        return jsonify({"ok": False, "error": "sqlite store not ready"}), 500
    body = request.get_json(silent=True) or {}
    label = str(body.get("label") or "wire save")[:160]
    nodes_in = body.get("nodes") if isinstance(body.get("nodes"), list) else None
    if nodes_in is not None:
        graph = {
            "v": 5,
            "nodes": nodes_in[:160],
            "edges": (body.get("edges") if isinstance(body.get("edges"), list) else [])[:200],
        }
        try:
            matrix_store.upsert_layout(graph["nodes"])
        except Exception:
            pass
    else:
        graph = _live_graph_with_layout()
    controllers = body.get("controllers") if isinstance(body.get("controllers"), dict) else _wire_controllers_map()
    try:
        sid = matrix_store.save_snapshot("save", label, graph, controllers)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({
        "ok": True,
        "id": sid,
        "label": label,
        "n": len(graph.get("nodes") or []),
        "message": f"saved snapshot #{sid}",
        "baseline": _wire_baseline(graph, controllers),
    })


@app.route("/api/wire/history", methods=["GET", "POST"])
def api_wire_history():
    """Last few recoverable canvas steps. Restore is client-side; never closes positions."""
    if not _MATRIX_OK:
        return jsonify({"ok": False, "error": "sqlite store not ready", "steps": []}), 500
    if request.method == "GET":
        try:
            steps = matrix_store.history_list(limit=request.args.get("limit") or 8)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True, "steps": steps})
    body = request.get_json(silent=True) or {}
    graph = {
        "v": 5,
        "nodes": body.get("nodes") if isinstance(body.get("nodes"), list) else [],
        "edges": body.get("edges") if isinstance(body.get("edges"), list) else [],
    }
    if not graph["nodes"]:
        return jsonify({"ok": False, "error": "empty graph"}), 400
    try:
        sid = matrix_store.history_push(
            body.get("label") or "step",
            graph,
            body.get("controllers") if isinstance(body.get("controllers"), dict) else {},
            body.get("bias") if isinstance(body.get("bias"), dict) else {},
            body.get("pct") if isinstance(body.get("pct"), dict) else {},
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "id": sid})


def _edge_key(e):
    return "|".join([
        str(e.get("source") or ""),
        str(e.get("sourceHandle") or ""),
        str(e.get("target") or ""),
        str(e.get("targetHandle") or ""),
    ])


def _node_label(n):
    if not isinstance(n, dict):
        return "?"
    d = n.get("data") if isinstance(n.get("data"), dict) else {}
    return d.get("name") or d.get("coin") or n.get("id") or "?"


def _compile_num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _compile_merge_canvas_ctrl(nodes, edges, controllers):
    """Fold wired SL / moon pct nodes into the controller map used for close checks."""
    by_id = {str(n.get("id")): n for n in (nodes or []) if isinstance(n, dict) and n.get("id")}
    out = {}
    for bid, vals in (controllers or {}).items():
        if isinstance(vals, dict):
            out[str(bid)] = dict(vals)
    for e in edges or []:
        if not isinstance(e, dict):
            continue
        th = str(e.get("targetHandle") or "")
        tgt = str(e.get("target") or "")
        src = by_id.get(str(e.get("source") or ""))
        if not tgt or not src:
            continue
        data = src.get("data") if isinstance(src.get("data"), dict) else {}
        pct = _compile_num(data.get("pct"))
        if th == "sl" and pct is not None:
            out.setdefault(tgt, {})["sl_roe_pct"] = pct
        if th == "moon" and pct is not None:
            out.setdefault(tgt, {})["moon_pct"] = pct
    return out


def _compile_close_hits(bot_id, values, positions, live_values=None):
    """Would applying these knobs flatten or clip a live clip right now?"""
    hits = []
    if not isinstance(values, dict):
        values = {}
    live_values = live_values if isinstance(live_values, dict) else {}
    sl = _compile_num(values.get("sl_roe_pct"))
    tp = _compile_num(values.get("tp_roe_pct"))
    scale = _compile_num(values.get("scale_roe_pct"))
    trail_d = _compile_num(values.get("trail_delta"))
    hold_h = _compile_num(values.get("hold_h"))
    if hold_h is None:
        tl = _compile_num(values.get("time_limit"))
        if tl is not None:
            hold_h = tl / 3600.0 if tl > 48 else tl
    loss_frac = _compile_num(values.get("max_position_loss_pct"))
    now = time.time()
    name = (BOTS.get(bot_id) or {}).get("name") or bot_id
    for p in positions or []:
        if not isinstance(p, dict) or p.get("bot_id") != bot_id:
            continue
        coin = p.get("coin") or "?"
        side = str(p.get("side") or "").upper()
        roe = _compile_num(p.get("roe_pct"))
        mark = _compile_num(p.get("mark_px"))
        pnl = _compile_num(p.get("pnl_usd"))
        margin = _compile_num(p.get("margin"))
        base = {
            "bot_id": bot_id,
            "bot": name,
            "coin": coin,
            "side": side,
            "roe_pct": roe,
        }

        def _changed(key, canvas_v):
            live_v = _compile_num(live_values.get(key)) if key in live_values else live_values.get(key)
            if canvas_v is None:
                return False
            if live_v is None:
                return True
            try:
                return abs(float(live_v) - float(canvas_v)) > 1e-9
            except (TypeError, ValueError):
                return live_v != canvas_v

        if sl is not None and roe is not None and roe <= -sl and _changed("sl_roe_pct", sl):
            hits.append({
                **base, "kind": "close", "key": "sl_roe_pct",
                "canvas": sl, "live": live_values.get("sl_roe_pct"),
                "reason": f"{name} {coin} {side} would SL now · ROE {roe:.1f}% ≤ −{sl:g}%",
                "workaround": f"keep live SL {live_values.get('sl_roe_pct')}",
            })
        if tp is not None and roe is not None and roe >= tp and _changed("tp_roe_pct", tp):
            hits.append({
                **base, "kind": "close", "key": "tp_roe_pct",
                "canvas": tp, "live": live_values.get("tp_roe_pct"),
                "reason": f"{name} {coin} {side} would TP now · ROE {roe:.1f}% ≥ +{tp:g}%",
                "workaround": f"keep live TP {live_values.get('tp_roe_pct')}",
            })
        if scale is not None and roe is not None and roe >= scale and _changed("scale_roe_pct", scale):
            hits.append({
                **base, "kind": "clip", "key": "scale_roe_pct",
                "canvas": scale, "live": live_values.get("scale_roe_pct"),
                "reason": f"{name} {coin} {side} would clip now · ROE {roe:.1f}% ≥ +{scale:g}%",
                "workaround": f"keep live clip {live_values.get('scale_roe_pct')}",
            })
        if trail_d is not None and trail_d > 0 and p.get("trail_active") and mark is not None and _changed("trail_delta", trail_d):
            ext = _compile_num(p.get("trail_extreme"))
            if ext is not None:
                d = trail_d / 100.0 if trail_d > 1 else trail_d
                if side == "LONG" and mark <= ext * (1 - d):
                    hits.append({
                        **base, "kind": "close", "key": "trail_delta",
                        "canvas": trail_d, "live": live_values.get("trail_delta"),
                        "reason": f"{name} {coin} LONG would trail-stop now · mark {mark:g} ≤ {ext * (1 - d):.4f}",
                        "workaround": f"keep live trail Δ {live_values.get('trail_delta')}",
                    })
                if side == "SHORT" and mark >= ext * (1 + d):
                    hits.append({
                        **base, "kind": "close", "key": "trail_delta",
                        "canvas": trail_d, "live": live_values.get("trail_delta"),
                        "reason": f"{name} {coin} SHORT would trail-stop now · mark {mark:g} ≥ {ext * (1 + d):.4f}",
                        "workaround": f"keep live trail Δ {live_values.get('trail_delta')}",
                    })
        if loss_frac is not None and pnl is not None and margin is not None and margin > 0 and _changed("max_position_loss_pct", loss_frac):
            frac = loss_frac / 100.0 if loss_frac > 1 else loss_frac
            cap = margin * frac
            if pnl <= -cap:
                hits.append({
                    **base, "kind": "close", "key": "max_position_loss_pct",
                    "canvas": loss_frac, "live": live_values.get("max_position_loss_pct"),
                    "reason": f"{name} {coin} would risk-stop now · uPnL {pnl:.2f} ≤ −{cap:.2f}",
                    "workaround": "keep live max loss %",
                })
        if hold_h is not None and p.get("entry_time") and _changed("hold_h", hold_h):
            started = None
            et = p.get("entry_time")
            if isinstance(et, (int, float)):
                started = float(et) / 1000.0 if et > 1e12 else float(et)
            else:
                try:
                    started = datetime.strptime(str(et).replace(" UTC", "")[:19], "%Y-%m-%d %H:%M:%S").timestamp()
                except Exception:
                    started = None
            if started and (now - started) >= hold_h * 3600:
                hits.append({
                    **base, "kind": "close", "key": "hold_h",
                    "canvas": hold_h, "live": live_values.get("hold_h"),
                    "reason": f"{name} {coin} would TIME-exit now · hold {hold_h:g}h elapsed",
                    "workaround": f"keep live hold {live_values.get('hold_h')}",
                })
    return hits


def _park_close_knobs(controllers, live_c, positions):
    """Replace close-causing knobs with live values. Never used to flatten."""
    parked = []
    safe = {}
    for bid, vals in (controllers or {}).items():
        if not isinstance(vals, dict):
            continue
        live_v = live_c.get(bid) or {}
        new_vals = dict(vals)
        hits = [h for h in _compile_close_hits(bid, vals, positions, live_v) if h.get("kind") == "close"]
        keys = []
        for h in hits:
            k = h.get("key")
            if k and k not in keys:
                keys.append(k)
        for key in keys:
            parked.append({
                "id": bid,
                "bot": (BOTS.get(bid) or {}).get("name") or bid,
                "key": key,
                "canvas": new_vals.get(key),
                "live": live_v.get(key),
                "reason": next((h.get("reason") for h in hits if h.get("key") == key), ""),
            })
            if key in live_v:
                new_vals[key] = live_v[key]
            else:
                new_vals.pop(key, None)
        safe[bid] = new_vals
    return safe, parked


def _compile_status_positions():
    """Fallback clip list from hunter status files — no extra HL calls."""
    rows = []
    for spec in get_home_brief_specs():
        bid = spec.get("id")
        status = _read_json_file(spec.get("status_file") or "") or {}
        if not isinstance(status, dict):
            continue
        name = spec.get("name") or bid
        if spec.get("kind") == "copy":
            for m in status.get("mirrors") or []:
                if not isinstance(m, dict):
                    continue
                coin = str(m.get("coin") or "").upper()
                side = str(m.get("side") or "").upper()
                if coin and side in ("LONG", "SHORT"):
                    rows.append({
                        "bot_id": bid, "bot": name, "coin": coin, "side": side,
                        "roe_pct": m.get("roe_pct"), "mark_px": m.get("mark_px") or m.get("px"),
                        "pnl_usd": m.get("unrealized_pnl") or m.get("pnl_usd"),
                        "margin": m.get("margin"),
                    })
            continue
        for coin, pos in (status.get("positions") or {}).items():
            if not isinstance(pos, dict):
                continue
            side = str(pos.get("position_side") or pos.get("side") or "").upper()
            if side not in ("LONG", "SHORT"):
                continue
            rows.append({
                "bot_id": bid, "bot": name, "coin": str(coin).upper(), "side": side,
                "roe_pct": pos.get("roe_pct"),
                "mark_px": pos.get("mid_price") or pos.get("mark_px"),
                "pnl_usd": pos.get("unrealized_pnl") or pos.get("pnl_usd"),
                "margin": pos.get("margin"),
                "trail_active": pos.get("trail_active"),
                "trail_extreme": pos.get("trail_extreme") or pos.get("trail_px"),
                "entry_time": pos.get("entry_time"),
            })
    return rows


def _compile_report(nodes, edges, controllers, positions, workaround=False):
    """Diff canvas vs live. Never writes hunters. Never closes."""
    nodes = nodes if isinstance(nodes, list) else []
    edges = edges if isinstance(edges, list) else []
    controllers = _compile_merge_canvas_ctrl(nodes, edges, controllers)
    if not positions:
        positions = _compile_status_positions()
    by_id = {str(n.get("id")): n for n in nodes if isinstance(n, dict) and n.get("id")}
    live_g = build_live_wire_graph()
    live_by = {str(n.get("id")): n for n in (live_g.get("nodes") or []) if isinstance(n, dict) and n.get("id")}
    live_e = {_edge_key(e) for e in (live_g.get("edges") or [])}
    canvas_e = {_edge_key(e) for e in edges}

    def _route_kind(sh, th):
        if sh in ("income", "funds") or th in ("income", "in") and sh in ("income", "funds"):
            return "income"
        if sh == "eclose" or th == "eclose":
            return "eclose"
        if sh == "bias" or th == "bias":
            return "bias"
        if sh == "tg" or th == "tg" or sh == "out":
            return "telegram"
        if sh == "coin" or th == "coin":
            return "coin"
        if sh == "wallet" or th == "wallet":
            return "wallet"
        if sh == "ctrl" or th == "ctrl":
            return "ctrl"
        if sh == "sl" or th == "sl":
            return "sl"
        if sh == "moon" or th == "moon":
            return "moon"
        if sh == "risk" or th == "risk":
            return "risk"
        return "route"

    wiring = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        k = _edge_key(e)
        if k in live_e:
            continue
        src = by_id.get(str(e.get("source") or ""))
        tgt = by_id.get(str(e.get("target") or ""))
        sh = str(e.get("sourceHandle") or "")
        th = str(e.get("targetHandle") or "")
        kind = _route_kind(sh, th)
        label = f"{_node_label(src)} {sh or 'out'} → {_node_label(tgt)} {th or 'in'}"
        if kind == "income":
            label += " (mapped · no auto HL send)"
        if kind == "risk":
            label += " (win skim mapped · no auto HL send)"
        if kind == "eclose":
            label += " (armed · idle, no flatten until CLOSE on that Telegram)"
        if kind == "telegram":
            tgt_t = (tgt or {}).get("type")
            if tgt_t == "desk_bias":
                label += " (Desk BIAS · LONG/FLAT/SHORT only, no close)"
        wiring.append({
            "kind": kind, "label": label, "status": "new",
            "source": str(e.get("source") or ""), "target": str(e.get("target") or ""),
            "from": sh, "to": th,
        })
    for e in live_g.get("edges") or []:
        if not isinstance(e, dict):
            continue
        k = _edge_key(e)
        if k in canvas_e:
            continue
        src = live_by.get(str(e.get("source") or "")) or by_id.get(str(e.get("source") or ""))
        tgt = live_by.get(str(e.get("target") or "")) or by_id.get(str(e.get("target") or ""))
        sh = str(e.get("sourceHandle") or "")
        th = str(e.get("targetHandle") or "")
        wiring.append({
            "kind": _route_kind(sh, th),
            "label": f"{_node_label(src)} {sh or 'out'} → {_node_label(tgt)} {th or 'in'}",
            "status": "removed",
            "source": str(e.get("source") or ""), "target": str(e.get("target") or ""),
            "from": sh, "to": th,
        })

    live_c = {h[0]: _ctrl_live_values(h[0]) for h in WIRE_HUNTERS}
    parked = []
    workaround_used = False
    if workaround:
        controllers, parked = _park_close_knobs(controllers, live_c, positions)
        workaround_used = True

    knobs = []
    closes = []
    for bid, vals in controllers.items():
        if not isinstance(vals, dict):
            continue
        live_v = live_c.get(bid) or {}
        name = (BOTS.get(bid) or {}).get("name") or bid
        for key, nv in vals.items():
            if key in ("updated", "source"):
                continue
            ov = live_v.get(key)
            if ov == nv:
                continue
            try:
                if ov is not None and nv is not None and abs(float(ov) - float(nv)) < 1e-9:
                    continue
            except (TypeError, ValueError):
                pass
            knobs.append({"bot": name, "id": bid, "key": key, "from": ov, "to": nv})
        closes.extend(_compile_close_hits(bid, vals, positions, live_v))

    blockers = [h for h in closes if h.get("kind") == "close"]
    clip_warn = [h for h in closes if h.get("kind") != "close"]
    warnings = list(clip_warn)
    if any(w.get("kind") == "income" for w in wiring):
        warnings.append({
            "kind": "income",
            "reason": "Income routes are mapped only — Integrate will not send funds on Hyperliquid",
        })

    bias_rows = []
    for n in nodes:
        if not isinstance(n, dict) or n.get("type") != "bias":
            continue
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        bid = str(data.get("botId") or "")
        if not bid:
            continue
        live_b = _home_bias_for(bid) or {}
        canvas_b = data.get("bias") if isinstance(data.get("bias"), dict) else {}
        diffs = []
        for coin, mode in canvas_b.items():
            lv = str(live_b.get(coin) or "FLAT").upper()
            cv = str(mode or "FLAT").upper()
            if lv != cv:
                diffs.append({"coin": coin, "from": lv, "to": cv})
        if diffs:
            bias_rows.append({
                "id": bid,
                "bot": (BOTS.get(bid) or {}).get("name") or bid,
                "diffs": diffs,
                "note": "BIAS write does not close an open clip",
            })
    desk_live = _desk_bias_read()
    desk_canvas = None
    for n in nodes:
        if not isinstance(n, dict):
            continue
        data = n.get("data") if isinstance(n.get("data"), dict) else {}
        if n.get("type") == "desk_bias" or data.get("kind") == "desk":
            desk_canvas = str(data.get("bias") or data.get("cmd") or "").upper() or None
            break
    if desk_canvas and desk_canvas not in ("LONG", "SHORT", "FLAT"):
        desk_canvas = None
    desk_row = {
        "live": str(desk_live.get("bias") or "").upper() or None,
        "canvas": desk_canvas,
        "note": "Desk BIAS does not close. Applies after each hunter autocloses.",
    }

    risk_rows = []
    by_id_all = {str(n.get("id")): n for n in nodes if isinstance(n, dict) and n.get("id")}
    for e in edges:
        if not isinstance(e, dict):
            continue
        if str(e.get("sourceHandle") or "") != "risk":
            continue
        src = by_id_all.get(str(e.get("source") or ""))
        tgt = by_id_all.get(str(e.get("target") or ""))
        if not src or not tgt:
            continue
        td = tgt.get("data") if isinstance(tgt.get("data"), dict) else {}
        risk_rows.append({
            "hunter": (src.get("data") or {}).get("name") or src.get("id"),
            "id": src.get("id"),
            "wallet": td.get("name") or tgt.get("id"),
            "address": td.get("address") or "",
            "mode": td.get("skim_mode") or "win_pct",
            "skim_pct": td.get("skim_pct"),
            "every_h": td.get("skim_every_h"),
            "min_usd": td.get("skim_min_usd"),
            "armed": bool(td.get("armed")),
            "note": "mapped · no auto HL send",
        })

    pos_by = {}
    for p in positions or []:
        if not isinstance(p, dict):
            continue
        pos_by.setdefault(p.get("bot_id"), []).append({
            "coin": p.get("coin"), "side": p.get("side"),
            "roe_pct": p.get("roe_pct"), "mark_px": p.get("mark_px"),
        })
    hunter_rows = []
    seen = set()
    for bid, vals in controllers.items():
        seen.add(bid)
        live_v = live_c.get(bid) or {}
        name = (BOTS.get(bid) or {}).get("name") or bid
        bot_knobs = [k for k in knobs if k.get("id") == bid]
        bot_hits = [h for h in closes if h.get("bot_id") == bid]
        hunter_rows.append({
            "id": bid,
            "name": name,
            "positions": pos_by.get(bid) or [],
            "n_knobs": len(bot_knobs),
            "n_block": sum(1 for h in bot_hits if h.get("kind") == "close"),
            "n_warn": sum(1 for h in bot_hits if h.get("kind") != "close"),
            "knobs": bot_knobs,
            "hits": bot_hits,
        })
    for p in positions or []:
        bid = (p or {}).get("bot_id")
        if bid and bid not in seen:
            seen.add(bid)
            hunter_rows.append({
                "id": bid,
                "name": (BOTS.get(bid) or {}).get("name") or bid,
                "positions": pos_by.get(bid) or [],
                "n_knobs": 0, "n_block": 0, "n_warn": 0,
                "knobs": [], "hits": [],
            })

    can = len(blockers) == 0
    n_new = sum(1 for w in wiring if w.get("status") == "new")
    n_rm = sum(1 for w in wiring if w.get("status") == "removed")
    if can and workaround_used:
        msg = (
            "Workaround compile OK · parked "
            + str(len(parked)) + " close-causing knob(s) at live values · "
            + str(n_new + n_rm) + " route change(s), " + str(len(knobs)) + " other knob(s)"
        )
    elif can:
        msg = "Compile OK · " + str(n_new + n_rm) + " route change(s), " + str(len(knobs)) + " knob(s) · will not close"
    else:
        msg = (
            "Compile blocked · " + str(len(blockers)) + " setting(s) would close a live clip. "
            "Workaround: park those knobs at live values and compile the rest."
        )
    return {
        "ok": True,
        "board": "desk",
        "can_integrate": can,
        "workaround_used": workaround_used,
        "workaround_available": (not can) or workaround_used,
        "parked": parked,
        "safe_controllers": controllers,
        "wiring": wiring,
        "knobs": knobs,
        "closes": closes,
        "blockers": blockers,
        "warnings": [w.get("reason") or w.get("label") for w in warnings],
        "warning_rows": warnings,
        "bias": bias_rows,
        "desk_bias": desk_row,
        "risk": risk_rows,
        "hunters": hunter_rows,
        "n_new_routes": n_new,
        "n_removed_routes": n_rm,
        "n_knobs": len(knobs),
        "n_pos": len(positions or []),
        "n_blockers": len(blockers),
        "n_parked": len(parked),
        "positions_untouched": True,
        "message": msg,
    }


@app.route("/api/wire/compile", methods=["POST"])
def api_wire_compile():
    """Offline check of the canvas vs live production. Does not write hunters."""
    body = request.get_json(silent=True) or {}
    nodes = body.get("nodes") if isinstance(body.get("nodes"), list) else []
    edges = body.get("edges") if isinstance(body.get("edges"), list) else []
    board = str(body.get("board") or "desk").strip().lower()
    if board == "home":
        report = _home_compile_report(nodes, edges)
        report["compile_id"] = None
        return jsonify(report)
    controllers = body.get("controllers") if isinstance(body.get("controllers"), dict) else {}
    positions = body.get("positions") if isinstance(body.get("positions"), list) else []
    workaround = bool(body.get("workaround"))
    report = _compile_report(nodes, edges, controllers, positions, workaround=workaround)
    graph = {"v": 5, "nodes": nodes[:160], "edges": edges[:200]}
    sid = None
    if _MATRIX_OK:
        try:
            sid = matrix_store.save_snapshot("compile", "compile check", graph, controllers)
        except Exception:
            sid = None
    report["compile_id"] = sid
    return jsonify(report)


@app.route("/api/wire/integrate", methods=["POST"])
def api_wire_integrate():
    """Apply a compiled canvas to production. Never closes or opens positions."""
    body = request.get_json(silent=True) or {}
    nodes = body.get("nodes") if isinstance(body.get("nodes"), list) else []
    edges = body.get("edges") if isinstance(body.get("edges"), list) else []
    controllers = body.get("controllers") if isinstance(body.get("controllers"), dict) else {}
    positions = body.get("positions") if isinstance(body.get("positions"), list) else []
    workaround = bool(body.get("workaround"))
    board = str(body.get("board") or "desk").strip().lower()
    if not nodes:
        return jsonify({"ok": False, "error": "empty graph"}), 400
    if board == "home":
        report = _home_compile_report(nodes, edges)
        if not report.get("can_integrate"):
            return jsonify({
                "ok": False,
                "error": report.get("message") or "home layout blocked",
                "blockers": report.get("blockers") or [],
            }), 400
        slim = _slim_wire_canvas(report.get("nodes") or nodes, edges)
        slim["board"] = "home"
        slim["nodes"] = _stamp_home_bands(slim.get("nodes") or [])
        try:
            ts = float(body.get("top_scale"))
            if 0.4 <= ts <= 1.5:
                slim["top_scale"] = round(ts, 2)
        except (TypeError, ValueError):
            pass
        try:
            _write_json_file(WIRE_BOARD_FILES["home"], slim)
            draft = WIRE_BOARD_DRAFT.get("home")
            if draft and os.path.isfile(draft):
                try:
                    os.remove(draft)
                except OSError:
                    pass
        except OSError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({
            "ok": True,
            "board": "home",
            "applied": ["Home layout pushed to /"],
            "message": "Integrated Home layout · TOP / MIDDLE / BOTTOM · positions untouched",
            "positions_untouched": True,
            "income_transfer": False,
        })
    report = _compile_report(nodes, edges, controllers, positions, workaround=workaround)
    if not report.get("can_integrate"):
        return jsonify({
            "ok": False,
            "error": "blocked: would close a live position",
            "blockers": report.get("blockers") or [],
            "workaround_available": True,
            "message": report.get("message"),
        }), 400
    controllers = report.get("safe_controllers") or controllers
    slim = _preserve_overlay_nodes(_slim_wire_canvas(nodes, edges))
    try:
        _write_json_file(WIRE_GRAPH_FILE, slim)
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    nodes = slim["nodes"]
    edges = slim["edges"]
    applied = []
    parked_note = []
    for row in report.get("parked") or []:
        parked_note.append(
            (row.get("bot") or row.get("id")) + " " + str(row.get("key")) + " kept live"
        )
    if parked_note:
        applied.append("workaround parked: " + ", ".join(parked_note))
    for bid, vals in controllers.items():
        if bid not in HOME_RESTART_IDS or not isinstance(vals, dict):
            continue
        out = _ctrl_write(bid, vals)
        if not out.get("ok"):
            return jsonify({"ok": False, "error": out.get("error") or "controller write failed", "id": bid}), 400
        if out.get("changed"):
            applied.append(out.get("message") or bid)
    graph_applied = _apply_wire_graph(nodes, edges)
    applied.extend(graph_applied or [])
    home_added = _promote_canvas_to_home(nodes)
    if home_added:
        applied.append("Home cards: " + ", ".join(home_added))
    if _MATRIX_OK:
        try:
            matrix_store.upsert_layout(nodes)
            matrix_store.save_snapshot("save", "integrated", {"v": 5, "nodes": nodes, "edges": edges}, controllers)
        except Exception:
            pass
    return jsonify({
        "ok": True,
        "applied": applied,
        "parked": report.get("parked") or [],
        "home": home_added,
        "message": "Integrated to production · " + (", ".join(applied) if applied else "wiring/income map only"),
        "income_transfer": False,
        "positions_untouched": True,
        "workaround_used": bool(report.get("workaround_used")),
    })


def _parse_canvas_bots(body=None):
    body = body if isinstance(body, dict) else {}
    raw = body.get("bots")
    if raw is None:
        raw = request.args.get("bots") or ""
    if isinstance(raw, str):
        items = [x.strip() for x in raw.split(",") if x.strip()]
    elif isinstance(raw, list):
        items = [str(x).strip() for x in raw if str(x).strip()]
    else:
        items = []
    return items


@app.route("/api/wire/containers", methods=["GET", "POST"])
def api_wire_containers():
    """List stack containers vs the current wire canvas. Does not start or stop."""
    body = request.get_json(silent=True) or {}
    rows = _wire_container_rows(_parse_canvas_bots(body))
    n_up = sum(1 for r in rows if r.get("running"))
    n_spare = sum(1 for r in rows if r.get("mark") == "not_needed")
    return jsonify({
        "ok": True,
        "containers": rows,
        "n_up": n_up,
        "n_spare": n_spare,
        "n_missing": sum(1 for r in rows if r.get("mark") == "missing"),
        "message": f"{n_up} running · {n_spare} not on this canvas",
    })


@app.route("/api/wire/containers/stop", methods=["POST"])
def api_wire_containers_stop():
    """Stop hunter/infra containers that are NOT on the current canvas. Does not close HL positions."""
    body = request.get_json(silent=True) or {}
    canvas = set(_parse_canvas_bots(body))
    ids = body.get("ids") if isinstance(body.get("ids"), list) else None
    if ids is None:
        one = str(body.get("id") or "").strip()
        ids = [one] if one else []
    ids = [str(x).strip() for x in ids if str(x).strip()]
    if not ids:
        return jsonify({"ok": False, "error": "no container id"}), 400
    confirm = bool(body.get("confirm"))
    rows = {r["id"]: r for r in _wire_container_rows(canvas)}
    blocked = []
    warned = []
    to_stop = []
    for name in ids:
        row = rows.get(name)
        if not row:
            blocked.append({"id": name, "reason": "unknown container"})
            continue
        if row.get("protected"):
            blocked.append({"id": name, "reason": "protected (Wire / dashboard)"})
            continue
        if row.get("on_canvas"):
            blocked.append({"id": name, "reason": "on this wire canvas — not unused"})
            continue
        if not row.get("running"):
            blocked.append({"id": name, "reason": "already stopped"})
            continue
        if int(row.get("n_pos") or 0) > 0 and not confirm:
            warned.append({
                "id": name,
                "name": row.get("name"),
                "n_pos": row.get("n_pos"),
                "reason": (
                    f"{row.get('name')} has {row.get('n_pos')} open HL position(s). "
                    "Stop does not close them — they stay on the exchange unmanaged."
                ),
            })
            continue
        to_stop.append(row)
    if blocked and not to_stop and not warned:
        return jsonify({"ok": False, "error": blocked[0]["reason"], "blocked": blocked}), 400
    if warned and not confirm:
        return jsonify({
            "ok": False,
            "need_confirm": True,
            "error": "open positions would be left unmanaged",
            "warnings": warned,
            "blocked": blocked,
        }), 409
    stopped = []
    errors = []
    for row in to_stop:
        try:
            _stop_container(row["id"])
            stopped.append(row["id"])
        except Exception as exc:
            errors.append({"id": row["id"], "reason": str(exc)})
    after = _wire_container_rows(canvas)
    ok = bool(stopped) and not errors
    msg = (
        ("Stopped " + ", ".join(stopped) if stopped else "Nothing stopped")
        + (" · positions left on HL" if any(r.get("n_pos") for r in to_stop) else "")
        + (" · " + errors[0]["reason"] if errors else "")
    )
    return jsonify({
        "ok": ok,
        "stopped": stopped,
        "blocked": blocked,
        "errors": errors,
        "containers": after,
        "n_up": sum(1 for r in after if r.get("running")),
        "n_spare": sum(1 for r in after if r.get("mark") == "not_needed"),
        "message": msg,
        "positions_untouched": True,
    }), (200 if ok else 500)


@app.route("/api/wire/snapshots", methods=["GET"])
def api_wire_snapshots():
    if not _MATRIX_OK:
        return jsonify({"ok": True, "snapshots": [], "audit": [], "db": False})
    kind = str(request.args.get("kind") or "").strip() or None
    try:
        snaps = matrix_store.list_snapshots(kind=kind, limit=request.args.get("limit") or 20)
        audit = matrix_store.recent_audit(limit=30)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "snapshots": snaps, "audit": audit, "db": True})


@app.route("/api/wire/layout", methods=["GET", "POST"])
def api_wire_layout():
    """Replace saved x/y for nodes. Does not write hunter config."""
    if not _MATRIX_OK:
        return jsonify({"ok": False, "error": "sqlite store not ready"}), 500
    if request.method == "GET":
        try:
            pos = matrix_store.layout_map()
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({
            "ok": True,
            "layout": {k: {"x": v[0], "y": v[1]} for k, v in pos.items()},
        })
    body = request.get_json(silent=True) or {}
    raw = body.get("nodes") if isinstance(body.get("nodes"), list) else []
    nodes = []
    for n in raw[:200]:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or n.get("node_id") or "")[:80]
        pos = n.get("position") if isinstance(n.get("position"), dict) else n
        try:
            x = float(pos.get("x"))
            y = float(pos.get("y"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not nid:
            continue
        nodes.append({"id": nid, "position": {"x": x, "y": y}})
    if not nodes:
        return jsonify({"ok": False, "error": "no positions"}), 400
    try:
        n = matrix_store.upsert_layout(nodes)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({
        "ok": True,
        "n": n,
        "replaced": [row["id"] for row in nodes],
    })


@app.route("/api/wire/layout/clear", methods=["POST"])
def api_wire_layout_clear():
    if _MATRIX_OK:
        try:
            matrix_store.clear_layout()
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "message": "layout cleared"})


@app.route("/api/wire/rebuild_live", methods=["POST", "GET"])
def api_wire_rebuild_live():
    """Rebuild the wire canvas from live wallets, positions, SL, bias, moon, controllers."""
    try:
        graph = _live_graph_with_layout()
        _write_json_file(WIRE_GRAPH_FILE, graph)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    applied = _apply_wire_graph(graph.get("nodes") or [], graph.get("edges") or [])
    controllers = _wire_controllers_map()
    if _MATRIX_OK:
        try:
            matrix_store.save_snapshot("live", "rebuild live", graph, controllers)
        except Exception:
            pass
    return jsonify({
        "ok": True,
        "graph": graph,
        "baseline": _wire_baseline(graph, controllers),
        "n": len(graph.get("nodes") or []),
        "e": len(graph.get("edges") or []),
        "applied": applied,
        "message": f"Rebuilt live canvas · {len(graph.get('nodes') or [])} nodes",
    })


CTRL_FRAC_KEYS = frozenset({
    "scale_out_pct", "st_max_dist", "max_position_loss_pct", "take_profit",
    "min_free_margin_pct", "trail_activation", "trail_delta", "margin_pct",
})

CTRL_YML_FILES = {
    "hummingbot-hl": "hl_copy_trader.yml",
    "hummingbot-bn-swing": "pmm_simple_trader.yml",
    "hummingbot-helliot": "helliot_trader.yml",
    "hummingbot-surfn": "surfn_trader.yml",
    "hummingbot-shortn": "shortn_trader.yml",
    "hummingbot-dman": "dman_trader.yml",
    "hummingbot-onlyup": "onlyup_trader.yml",
    "hummingbot-dman-maker": "dman_maker_v2_trader.yml",
}

CTRL_YML_KEYS = (
    "tp_roe_pct", "scale_roe_pct", "scale_out_pct", "recover_roe_pct",
    "bb_long_threshold", "bb_short_threshold", "bbp_min", "bbp_max",
    "wave2_min", "wave2_max", "wave3_ext", "st_max_dist", "margin_pct",
    "max_leverage", "max_positions_per_trader", "max_subscribed_markets",
    "poll_interval_seconds", "max_position_loss_pct", "min_free_margin_pct",
    "max_zec_short_usd",
    "take_profit", "trail_activation", "trail_delta", "executor_refresh_time",
)

CTRL_STATUS_FILES = {
    "hummingbot-hl": os.path.join(CONF_DIR, "hl_copy_status.json"),
    "hummingbot-bn-swing": BN_SWING_STATUS_FILE,
    "hummingbot-helliot": HELLIOT_STATUS_FILE,
    "hummingbot-surfn": SURFN_STATUS_FILE,
    "hummingbot-shortn": SHORTN_STATUS_FILE,
    "hummingbot-dman": DMAN_STATUS_FILE,
    "hummingbot-onlyup": ONLYUP_STATUS_FILE,
    "hummingbot-dman-maker": os.path.join(CONF_DIR, "dman_maker_status.json"),
}


def _ctrl_file(bot_id):
    return os.path.join(CONF_DIR, f"{bot_id}_ctrl.json")


def _ctrl_yml_name(bot_id):
    h = registry_hunter(bot_id)
    if h and h.get("script"):
        return h.get("script")
    return CTRL_YML_FILES.get(bot_id)


def _ctrl_status_path(bot_id):
    h = registry_hunter(bot_id)
    if h and h.get("status_file"):
        return os.path.join(CONF_DIR, os.path.basename(h.get("status_file")))
    return CTRL_STATUS_FILES.get(bot_id)


def _as_ctrl_bool(raw, default=True):
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return bool(raw)
    s = str(raw or "").strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    return bool(default)


def _yml_ctrl_defaults(bot_id):
    name = _ctrl_yml_name(bot_id)
    if not name:
        return {}
    path = os.path.join(CONF_DIR, "scripts", name)
    try:
        with open(path, "r") as f:
            text = f.read()
    except OSError:
        return {}
    out = {}
    for k in CTRL_YML_KEYS:
        m = re.search(rf"^\s*{re.escape(k)}\s*:\s*([-\d.]+)", text, re.M)
        if not m:
            continue
        try:
            out[k] = float(m.group(1))
        except ValueError:
            pass
    cs = re.search(r"^\s*copy_shorts\s*:\s*(true|false)", text, re.I | re.M)
    if cs:
        out["copy_shorts"] = cs.group(1).lower() == "true"
    dc = re.search(r"^\s*dontGoCrazy\s*:\s*(true|false)", text, re.I | re.M)
    if dc:
        out["dontGoCrazy"] = dc.group(1).lower() == "true"
    al = re.search(r"^\s*afterLossNoRebuy\s*:\s*(true|false)", text, re.I | re.M)
    if al:
        out["afterLossNoRebuy"] = al.group(1).lower() == "true"
    return out


def _ctrl_live_values(bot_id, status=None):
    status = status if isinstance(status, dict) else {}
    if not status:
        status = _read_json_file(_ctrl_status_path(bot_id) or "")
        if not isinstance(status, dict):
            status = {}
    out = _yml_ctrl_defaults(bot_id)
    sl = _sl_roe_for(bot_id, status.get("sl_roe_pct") if status.get("sl_roe_pct") is not None else 20.0)
    if sl is not None:
        out["sl_roe_pct"] = sl
    for k in CTRL_YML_KEYS:
        if status.get(k) is not None:
            try:
                out[k] = float(status[k])
            except (TypeError, ValueError):
                pass
    risk = status.get("risk") if isinstance(status.get("risk"), dict) else {}
    for k in ("max_position_loss_pct", "min_free_margin_pct"):
        if out.get(k) is None and risk.get(k) is not None:
            try:
                out[k] = float(risk[k])
            except (TypeError, ValueError):
                pass
    if status.get("copy_shorts") is not None:
        out["copy_shorts"] = bool(status.get("copy_shorts"))
    if bot_id == "hummingbot-dman-maker":
        for k, default in (("dontGoCrazy", True), ("afterLossNoRebuy", True)):
            if status.get(k) is not None:
                out[k] = _as_ctrl_bool(status.get(k), default)
            elif k not in out:
                out[k] = default
    if bot_id == "hummingbot-dman":
        try:
            tune = _dman_tune_map(status)
            out["trail_activation"] = tune.get("trail_activation")
            out["trail_delta"] = tune.get("trail_delta")
            out["bb_long_threshold"] = tune.get("bb_long_threshold")
            out["bb_short_threshold"] = tune.get("bb_short_threshold")
            out["hold_h"] = int(round(float(_dman_hold_s()) / 3600.0))
        except Exception:
            pass
    if bot_id == "hummingbot-hl":
        try:
            cfg = load_hl_config() or {}
        except Exception:
            cfg = {}
        try:
            rt = load_hl_runtime_flags() or {}
        except Exception:
            rt = {}
        if "copy_shorts" in rt:
            out["copy_shorts"] = bool(rt.get("copy_shorts"))
        elif "longs_only" in rt:
            out["copy_shorts"] = not bool(rt.get("longs_only"))
        else:
            out["copy_shorts"] = bool(cfg.get("copy_shorts", True))
        for k, default in (
            ("max_leverage", 20),
            ("max_positions_per_trader", 3),
            ("max_position_loss_pct", 0.333),
            ("min_free_margin_pct", 0.15),
            ("max_subscribed_markets", 8),
            ("poll_interval_seconds", 5),
        ):
            if out.get(k) is None:
                out[k] = cfg.get(k, default)
        if rt.get("poll_interval_seconds") is not None:
            out["poll_interval_seconds"] = rt.get("poll_interval_seconds")
    file_row = _read_json_file(_ctrl_file(bot_id))
    if isinstance(file_row, dict):
        for k, v in file_row.items():
            if k in ("updated", "source"):
                continue
            if v is not None:
                out[k] = v
    sql_row = _sql_kv_map(bot_id)
    for k, v in sql_row.items():
        if k in ("updated", "source", "moon", "unfollowed_traders", "disabled_traders"):
            continue
        if v is not None:
            out[k] = v
    if bot_id == "hummingbot-dman-maker":
        out["dontGoCrazy"] = _as_ctrl_bool(out.get("dontGoCrazy"), True)
        out["afterLossNoRebuy"] = _as_ctrl_bool(out.get("afterLossNoRebuy"), True)
        if "sendEvery2Win" not in out:
            on = True
            skim = _read_json_file(RISK_SKIM_FILE) or {}
            hunters = skim.get("hunters") if isinstance(skim.get("hunters"), dict) else {}
            row = hunters.get(bot_id) if isinstance(hunters.get(bot_id), dict) else {}
            mode = str(row.get("mode") or "")
            if mode in ("keep", "off", "none"):
                on = False
            out["sendEvery2Win"] = on
        else:
            out["sendEvery2Win"] = _as_ctrl_bool(out.get("sendEvery2Win"), True)
    return out


@app.route("/api/wire/controller", methods=["GET", "POST"])
def api_wire_controller():
    """Read/write hunter knobs from the wire controller node."""
    if request.method == "GET":
        bot_id = str(request.args.get("id") or "").strip()
        if bot_id:
            if bot_id not in HOME_RESTART_IDS:
                return jsonify({"ok": False, "error": "unknown bot"}), 400
            return jsonify({"ok": True, "id": bot_id, "values": _ctrl_live_values(bot_id)})
        return jsonify({
            "ok": True,
            "values": {h[0]: _ctrl_live_values(h[0]) for h in WIRE_HUNTERS},
        })

    data = request.get_json(silent=True) or {}
    bot_id = str(data.get("id") or "").strip()
    if bot_id not in HOME_RESTART_IDS:
        return jsonify({"ok": False, "error": "unknown bot"}), 400
    incoming = data.get("values") if isinstance(data.get("values"), dict) else {}
    if not incoming:
        key = data.get("key")
        if key:
            incoming = {key: data.get("value")}
    if not incoming:
        return jsonify({"ok": False, "error": "no values"}), 400
    out = _ctrl_write(bot_id, incoming)
    if not out.get("ok"):
        return jsonify(out), 400
    return jsonify(out)


def _apply_dman_every_2_win(on):
    """DMan Maker skim only. ON is two_wins. OFF is keep, skim 0, parked 0.

    Turning off releases this hunter's parked_usd. Does not send on Hyperliquid
    and does not touch other hunters.
    """
    bid = "hummingbot-dman-maker"
    with _risk_file_lock():
        data = _read_json_file(RISK_SKIM_FILE) or {}
        if not isinstance(data, dict):
            return False
        hunters = data.get("hunters") if isinstance(data.get("hunters"), dict) else None
        row = hunters.get(bid) if hunters and isinstance(hunters.get(bid), dict) else None
        if row is None:
            return False
        if on:
            row["mode"] = "two_wins"
            row["skim_pct"] = 100
            row["streak_wins"] = 2
            row["note"] = "two realized wins in a row → take 2nd win profit only"
        else:
            try:
                was = float(row.get("parked_usd") or 0)
            except (TypeError, ValueError):
                was = 0.0
            row["mode"] = "keep"
            row["skim_pct"] = 0
            row["parked_usd"] = 0
            row["note"] = "wins stay on the hunter account"
            if was > 0:
                ev = list(row.get("events") or [])
                ev.append({
                    "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "take": 0,
                    "why": "send every 2 win off — parked released to the hunter",
                })
                row["events"] = ev[-40:]
        hunters[bid] = row
        data["hunters"] = hunters
        data["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        data["send"] = False
        try:
            _write_json_file(RISK_SKIM_FILE, data)
        except OSError:
            return False
    return True


def _v2_sleeper_meta():
    data = _read_json_file(os.path.join(CONF_DIR, "v2_sleepers.json")) or {}
    rows = data.get("sleepers") if isinstance(data.get("sleepers"), list) else []
    keep = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("id") or "")
        if sid.startswith("hummingbot-v2-"):
            keep.append(row)
    try:
        cap = int(data.get("max_running") or 2)
    except (TypeError, ValueError):
        cap = 2
    cap = max(1, min(4, cap))
    return keep, cap


def _v2_running_count():
    try:
        r = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=8,
        )
    except Exception:
        return 0
    n = 0
    for line in (r.stdout or "").splitlines():
        if line.strip().startswith("hummingbot-v2-"):
            n += 1
    return n


HUNTER_GATE_IDS = (
    "hummingbot-hl",
    "hummingbot-bn-swing",
    "hummingbot-helliot",
    "hummingbot-surfn",
    "hummingbot-shortn",
    "hummingbot-dman",
    "hummingbot-onlyup",
    "hummingbot-dman-maker",
)


def _ctrl_write(bot_id, incoming):
    """Apply controller knobs. Does not close or open positions."""
    if not flask_writes_on():
        return {
            "ok": False,
            "error": "Flask WRITE MODE is off",
            "closes": False,
            "positions_untouched": True,
        }
    before_ctrl = _ctrl_live_values(bot_id)
    row = _read_json_file(_ctrl_file(bot_id))
    if not isinstance(row, dict):
        row = {}
    changed = []
    msgs = []

    for key, raw in incoming.items():
        key = str(key)
        if key in ("updated", "source"):
            continue
        val = raw
        if key == "sendEvery2Win":
            val = _as_ctrl_bool(raw, True)
        elif key == "leverage_tier":
            tier = str(raw or "").strip().upper()
            if tier not in ("MAX", "MID", "LOW"):
                return {
                    "ok": False,
                    "error": "leverage_tier must be MAX, MID, or LOW",
                    "closes": False,
                    "positions_untouched": True,
                }
            val = tier
        elif key in CTRL_FRAC_KEYS:
            try:
                val = float(raw)
            except (TypeError, ValueError):
                return {"ok": False, "error": f"{key} must be a number"}
            if val > 1:
                val = val / 100.0
        elif key == "copy_shorts":
            val = bool(raw)
        elif key in ("dontGoCrazy", "afterLossNoRebuy"):
            val = _as_ctrl_bool(raw, True)
        elif key == "hold_h":
            try:
                val = int(float(raw))
            except (TypeError, ValueError):
                return {"ok": False, "error": "hold_h must be 12, 24, or 68"}
        elif isinstance(raw, bool):
            val = raw
        else:
            try:
                val = float(raw)
            except (TypeError, ValueError):
                val = raw

        if key == "sl_roe_pct":
            try:
                sl = float(val)
            except (TypeError, ValueError):
                return {"ok": False, "error": "sl_roe_pct must be a number"}
            if sl < 1 or sl > 90:
                return {"ok": False, "error": "sl_roe_pct must be 1–90"}
            sl_payload = _read_json_file(BOT_SL_FILE)
            if not isinstance(sl_payload, dict):
                sl_payload = {}
            srow = sl_payload.get(bot_id) if isinstance(sl_payload.get(bot_id), dict) else {}
            srow["sl_roe_pct"] = round(sl, 2)
            srow["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            srow["source"] = "wire-ctrl"
            sl_payload[bot_id] = srow
            _write_json_file(BOT_SL_FILE, sl_payload)
            val = round(sl, 2)
            msgs.append(f"SL −{val:g}%")

        if bot_id == "hummingbot-hl" and key == "copy_shorts":
            try:
                save_hl_runtime_flags({"copy_shorts": bool(val), "longs_only": not bool(val)})
                msgs.append("copy shorts " + ("on" if val else "off"))
            except Exception as e:
                return {"ok": False, "error": str(e)}
        if bot_id == "hummingbot-dman-maker" and key in ("dontGoCrazy", "afterLossNoRebuy"):
            msgs.append(key + " " + ("on" if val else "off"))
        if key == "sendEvery2Win" and bot_id == "hummingbot-dman-maker":
            if not _apply_dman_every_2_win(bool(val)):
                return {
                    "ok": False,
                    "error": "risk skim not saved",
                    "closes": False,
                    "positions_untouched": True,
                }
            msgs.append("send every 2 win " + ("on" if val else "off"))
        if key == "leverage_tier":
            msgs.append("leverage " + str(val))
        if bot_id == "hummingbot-hl" and key in (
            "poll_interval_seconds", "max_leverage", "max_positions_per_trader",
            "max_subscribed_markets", "max_position_loss_pct", "min_free_margin_pct",
        ):
            try:
                save_hl_runtime_flags({key: val})
            except Exception:
                pass

        if bot_id == "hummingbot-dman" and key in (
            "trail_activation", "trail_delta", "bb_long_threshold", "bb_short_threshold"
        ):
            payload = _dman_tune_map()
            payload[key] = float(val)
            payload["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            _write_json_file(DMAN_TUNE_FILE, payload)
            msgs.append(key)
        if key == "close_px" and bot_id in _MOON_BOTS:
            try:
                px = float(val)
            except (TypeError, ValueError):
                return {"ok": False, "error": "close $ must be a number", "closes": False}
            armed = _set_manual_close_px(bot_id, px)
            if not armed.get("ok"):
                return armed
            val = round(px, 6)
            msgs.append(armed.get("message") or f"close ${val:g}")

        if bot_id == "hummingbot-dman" and key == "hold_h":
            if val not in (12, 24, 68):
                return {"ok": False, "error": "hold_h must be 12, 24, or 68"}
            sl_payload = _read_json_file(BOT_SL_FILE)
            if not isinstance(sl_payload, dict):
                sl_payload = {}
            srow = sl_payload.get(bot_id) if isinstance(sl_payload.get(bot_id), dict) else {}
            srow["time_limit_h"] = int(val)
            srow["time_limit_s"] = int(val) * 3600
            srow["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            sl_payload[bot_id] = srow
            _write_json_file(BOT_SL_FILE, sl_payload)
            msgs.append(f"hold {int(val)}h")

        row[key] = val
        changed.append(key)

    row["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    row["source"] = "wire"
    try:
        _write_json_file(_ctrl_file(bot_id), row)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    for key in changed:
        _sql_kv_set(bot_id, key, row.get(key), "wire")
    name = (BOTS.get(bot_id) or {}).get("name") or bot_id
    msg = f"{name} controller · " + (", ".join(msgs) if msgs else ", ".join(changed))
    after_ctrl = _ctrl_live_values(bot_id)
    if _MATRIX_OK:
        for key in changed:
            try:
                matrix_store.audit(
                    bot_id, key, before_ctrl.get(key), after_ctrl.get(key), "wire"
                )
            except Exception:
                pass
    return {
        "ok": True,
        "id": bot_id,
        "values": after_ctrl,
        "changed": changed,
        "message": msg,
    }


def _wire_pack_payload():
    """One JSON with desk/home/health canvases. No live clips."""
    desk = _read_json_file(WIRE_GRAPH_FILE) or {"v": 5, "nodes": [], "edges": []}
    home = _read_json_file(WIRE_BOARD_FILES.get("home") or "") or {"v": 5, "nodes": [], "edges": []}
    health = _read_json_file(WIRE_BOARD_FILES.get("health") or "") or {"v": 5, "nodes": [], "edges": []}
    return {
        "kind": "freiquell-wire-pack",
        "v": 1,
        "saved_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "positions_untouched": True,
        "note": "canvas only — restore writes graph files, does not CLOSE/OPEN Hyperliquid",
        "boards": {"desk": desk, "home": home, "health": health},
    }


def _wire_pack_restore(pack):
    if not isinstance(pack, dict) or pack.get("kind") != "freiquell-wire-pack":
        return {"ok": False, "error": "not a freiquell-wire-pack JSON"}
    boards = pack.get("boards") if isinstance(pack.get("boards"), dict) else {}
    mapping = {
        "desk": WIRE_GRAPH_FILE,
        "home": WIRE_BOARD_FILES.get("home"),
        "health": WIRE_BOARD_FILES.get("health"),
    }
    wrote = []
    for name, path in mapping.items():
        g = boards.get(name)
        if not isinstance(g, dict) or not path:
            continue
        nodes = g.get("nodes") if isinstance(g.get("nodes"), list) else []
        edges = g.get("edges") if isinstance(g.get("edges"), list) else []
        out = {"v": int(g.get("v") or 5), "nodes": nodes[:200], "edges": edges[:400], "board": name}
        try:
            _write_json_file(path, out)
            wrote.append(name)
        except OSError as exc:
            return {"ok": False, "error": str(exc), "wrote": wrote}
    return {
        "ok": True,
        "wrote": wrote,
        "closes": False,
        "positions_untouched": True,
        "message": "wire pack restored to " + ", ".join(wrote) + " — Reload WIRE; clips untouched",
    }


@app.route("/api/wire/pack", methods=["GET", "POST"])
def api_wire_pack():
    """Save or restore the whole WIRE setup as one JSON. Never closes positions."""
    pack_path = os.path.join(CONF_DIR, "wire_pack.json")
    if request.method == "GET":
        pack = _wire_pack_payload()
        if request.args.get("download"):
            body = json.dumps(pack, indent=2)
            return Response(
                body,
                mimetype="application/json",
                headers={"Content-Disposition": "attachment; filename=freiquell-wire-pack.json"},
            )
        return jsonify(pack)
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "").strip().lower()
    if action == "save" or (not action and data.get("kind") != "freiquell-wire-pack"):
        pack = _wire_pack_payload()
        try:
            _write_json_file(pack_path, pack)
        except OSError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        n = sum(len((pack.get("boards") or {}).get(b, {}).get("nodes") or []) for b in ("desk", "home", "health"))
        return jsonify({
            "ok": True,
            "path": "conf/wire_pack.json",
            "n": n,
            "closes": False,
            "positions_untouched": True,
            "message": "saved conf/wire_pack.json — one file to recover desk/home/health WIRE",
            "pack": pack,
        })
    pack = data if data.get("kind") == "freiquell-wire-pack" else (_read_json_file(pack_path) or {})
    out = _wire_pack_restore(pack)
    code = 200 if out.get("ok") else 400
    return jsonify(out), code


@app.route("/api/wire/xml", methods=["GET", "POST"])
def api_wire_xml():
    """Download or store the board snapshot as XML (conf/wire_graph.xml)."""
    if request.method == "GET":
        if not os.path.isfile(WIRE_XML_FILE):
            return Response("no xml yet\n", status=404, mimetype="text/plain")
        return send_from_directory(CONF_DIR, "wire_graph.xml", as_attachment=True)
    body = request.get_json(silent=True) or {}
    xml = body.get("xml") if isinstance(body.get("xml"), str) else ""
    xml = xml.strip()
    if not xml.startswith("<"):
        return jsonify({"ok": False, "error": "not xml"}), 400
    if len(xml) > 1_500_000:
        return jsonify({"ok": False, "error": "too large"}), 400
    try:
        tmp = WIRE_XML_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(xml)
            if not xml.endswith("\n"):
                f.write("\n")
        os.replace(tmp, WIRE_XML_FILE)
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "path": "conf/wire_graph.xml"})


@app.route("/api/brief")
@app.route("/brief")
def api_brief():
    """Read-only Freiquell desk summary for Grok Bot. Token required."""
    if not _brief_authorized():
        return Response("token required\n", status=401, mimetype="text/plain")
    md = _write_desk_brief_file()
    return Response(md, mimetype="text/plain; charset=utf-8")


@app.route("/confiig")
def confiig_page():
    return render_template("confiig.html", active="confiig", nav_title="FUNDS")


@app.route("/health")
def health_page():
    return render_template("health.html", active="health", nav_title="HEALTH")


@app.route("/janestreet")
@app.route("/qa")
@app.route("/sequencer")
def qa_page():
    return render_template("qa.html", active="janestreet", nav_title="JANESTREET", js_view="weekly")


@app.route("/janestreet/daily")
def qa_daily_page():
    return render_template("qa.html", active="janestreet-daily", nav_title="JANESTREET DAILY", js_view="daily")


@app.route("/backup")
def backup_page():
    return render_template("backup.html", active="backup", nav_title="BACKUP")


@app.route("/cpp-usb")
def cpp_usb_page():
    return render_template("cpp_usb.html", active="cpp-usb", nav_title="C++ USB")


@app.route("/update")
def update_page():
    return render_template("update.html", active="update", nav_title="UPDATE")


@app.route("/versions")
def versions_redirect():
    return redirect("/update")


@app.route("/audit")
def audit_page():
    return render_template("audit.html", active="audit", nav_title="AUDIT")


@app.route("/api/trader_audits")
def api_trader_audits():
    """Latest staggered health audits from hummingbot-audit worker."""
    data = {}
    if _MATRIX_OK:
        try:
            data = matrix_store.leader_audit_latest(
                (load_hl_config().get("trader_addresses") or [])
            ) or {}
        except Exception:
            data = {}
    if not (isinstance(data.get("traders"), dict) and data.get("traders")):
        path = os.path.join(LOGS_DIR, "trader_audits", "latest.json")
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    data = json.load(f)
            except Exception as e:
                return jsonify({"ok": False, "error": str(e)}), 500
    # Worker liveness from container + log mtime
    running = get_container_running("hummingbot-audit")
    log_path = os.path.join(LOGS_DIR, "trader_audits", "audit.log")
    log_mtime = None
    if os.path.isfile(log_path):
        try:
            log_mtime = datetime.utcfromtimestamp(os.path.getmtime(log_path)).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
        except Exception:
            pass
    history = []
    if _MATRIX_OK:
        try:
            history = matrix_store.leader_audit_history(30) or []
        except Exception:
            history = []
    if not history:
        hist_dir = os.path.join(LOGS_DIR, "trader_audits", "history")
        if os.path.isdir(hist_dir):
            try:
                names = sorted(os.listdir(hist_dir), reverse=True)
                for name in names[:30]:
                    if not name.endswith(".json"):
                        continue
                    try:
                        with open(os.path.join(hist_dir, name)) as f:
                            row = json.load(f)
                        u_pnl = row.get("u_pnl_total")
                        if u_pnl is None and row.get("positions"):
                            try:
                                u_pnl = round(sum(float(p.get("u_pnl") or 0) for p in row["positions"]), 2)
                            except Exception:
                                u_pnl = None
                        history.append({
                            "file": name,
                            "trader": row.get("trader"),
                            "audited_at": row.get("audited_at"),
                            "capacity_flag": row.get("capacity_flag"),
                            "sleep_flag": row.get("sleep_flag"),
                            "free_margin": row.get("free_margin"),
                            "equity": row.get("equity"),
                            "sleep_hours": row.get("sleep_hours"),
                            "n_positions": row.get("n_positions"),
                            "u_pnl_total": u_pnl,
                            "can_open": row.get("can_open"),
                        })
                    except Exception:
                        continue
            except Exception:
                pass
    cfg = load_hl_config()
    live_addrs = [str(a).strip() for a in (cfg.get("trader_addresses") or [])]
    traders = data.get("traders") if isinstance(data.get("traders"), dict) else {}
    synced = {}
    for i, addr in enumerate(live_addrs, 1):
        row = traders.get(str(i)) if isinstance(traders.get(str(i)), dict) else {}
        old = str(row.get("address") or "").strip().lower()
        if old == addr.lower() and row and not row.get("stale"):
            synced[str(i)] = row
        elif old == addr.lower() and row:
            synced[str(i)] = row
        else:
            short = (addr[:10] + "…" + addr[-6:]) if len(addr) >= 16 else addr
            synced[str(i)] = {
                "trader": i,
                "address": addr,
                "short": short,
                "ok": None,
                "stale": True,
                "positions": [],
                "error": "leader swapped — waiting for next audit",
            }
    if isinstance(data, dict):
        data = dict(data)
        data["traders"] = synced
    return jsonify({
        "ok": True,
        "worker_running": running,
        "log_mtime": log_mtime,
        "latest": data,
        "history": history,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


@app.route("/api/trader_audits/run", methods=["POST"])
def api_trader_audits_run():
    """Queue a force audit for the worker (writes logs/trader_audits/force.json)."""
    body = request.get_json(silent=True) or {}
    trader = body.get("trader", "all")
    if trader not in ("all", 1, 2, 3, "1", "2", "3"):
        return jsonify({"ok": False, "error": "trader must be 1|2|3|all"}), 400
    if trader != "all":
        trader = int(trader)
    if _MATRIX_OK:
        try:
            matrix_store.leader_audit_force_set(trader)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    out_dir = os.path.join(LOGS_DIR, "trader_audits")
    path = os.path.join(out_dir, "force.json")
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "trader": trader,
                "requested_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            }, f)
            f.write("\n")
    except Exception as e:
        if not _MATRIX_OK:
            return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "queued": trader, "sqlite": bool(_MATRIX_OK)})


def _followed_roster():
    """Copy roster + chat-why notes. No Hyperliquid fetches."""
    cfg = load_hl_config()
    addresses = cfg.get("trader_addresses", [])
    yaml_text = ""
    try:
        with open(HL_COPY_CONF, "r") as f:
            yaml_text = f.read()
    except Exception:
        yaml_text = ""
    yaml_whys = _parse_yaml_trader_whys(yaml_text)
    notes = load_follow_notes()
    unfollowed_nums = set(effective_unfollowed_traders(cfg))
    traders = []
    for i, addr in enumerate(addresses):
        num = i + 1
        why = follow_why_for(addr, yaml_whys, notes)
        followed = num not in unfollowed_nums
        traders.append({
            "num": num,
            "address": addr,
            "short": addr[:10] + "…" + addr[-6:],
            "followed": followed,
            "unfollowed": not followed,
            "why_title": why.get("title") or "",
            "why": why.get("why") or "",
            "why_asked": why.get("asked") or "",
            "why_source": why.get("source") or "",
        })
    return {
        "traders": traders,
        "followed": [t for t in traders if t.get("followed")],
        "config": {
            "min_leverage": cfg.get("min_leverage"),
            "max_leverage": cfg.get("max_leverage"),
            "min_open_positions": cfg.get("min_open_positions"),
            "allocation_pct": cfg.get("allocation_pct"),
        },
    }


_LEADER_COLORS = ("#f0b90b", "#7ecbff", "#c084fc", "#34d399", "#ff4d6d", "#ffc857")


def _audit_by_address():
    """Latest audit snapshot per leader address (no live HL fetch)."""
    out = {}
    if _MATRIX_OK:
        try:
            out.update(matrix_store.leader_audit_by_address() or {})
        except Exception:
            pass
    hist = os.path.join(LOGS_DIR, "trader_audits", "history")
    latest_path = os.path.join(LOGS_DIR, "trader_audits", "latest.json")
    files = []
    if os.path.isdir(hist):
        files.extend(
            os.path.join(hist, n)
            for n in sorted(os.listdir(hist))
            if n.endswith(".json")
        )
    files.append(latest_path)
    for path in files:
        try:
            raw = _read_json_file(path) or {}
        except Exception:
            continue
        rows = []
        if isinstance(raw.get("traders"), dict):
            rows.extend(raw["traders"].values())
        elif isinstance(raw, dict) and raw.get("address"):
            rows.append(raw)
        for row in rows:
            if not isinstance(row, dict):
                continue
            addr = str(row.get("address") or "").strip().lower()
            if addr.startswith("0x") and len(addr) == 42:
                out[addr] = row
    return out


def _our_copy_pnl_by_trader():
    """Realized Copy income per T# from copy_trades.log: wins / losses / net."""
    def _load():
        by_n = {}
        try:
            trades = parse_copy_trades()
        except Exception:
            trades = []
        for t in trades or []:
            n = t.get("trader")
            if not isinstance(n, int):
                continue
            usd = float(t.get("pnl_usd") or 0)
            row = by_n.setdefault(n, {"wins": 0.0, "losses": 0.0, "net": 0.0, "n": 0})
            row["net"] += usd
            row["n"] += 1
            if usd >= 0:
                row["wins"] += usd
            else:
                row["losses"] += usd
        for row in by_n.values():
            row["wins"] = round(row["wins"], 2)
            row["losses"] = round(row["losses"], 2)
            row["net"] = round(row["net"], 2)
        return by_n
    return _ttl_get("copy_pnl_by_trader", 45, _load)


def _top_coins_from_audit(row, n=4):
    pos = row.get("positions") if isinstance(row, dict) else None
    if not isinstance(pos, list):
        return []
    ranked = sorted(
        [p for p in pos if isinstance(p, dict) and p.get("coin")],
        key=lambda p: abs(float(p.get("u_pnl") or 0)),
        reverse=True,
    )
    out = []
    for p in ranked[:n]:
        side = str(p.get("side") or "")[:1].upper()
        out.append(f"{p.get('coin')} {side}".strip())
    return out


def _copy_leader_cards():
    """Toolbox + Wire cards: current Copy roster + alumni we already used."""
    cfg = load_hl_config()
    addresses = [str(a).strip().lower() for a in (cfg.get("trader_addresses") or [])]
    yaml_text = ""
    try:
        with open(HL_COPY_CONF, "r") as f:
            yaml_text = f.read()
    except Exception:
        yaml_text = ""
    yaml_whys = _parse_yaml_trader_whys(yaml_text)
    notes = load_follow_notes()
    unfollowed = set(effective_unfollowed_traders(cfg))
    audits = _audit_by_address()
    our_pnl = _our_copy_pnl_by_trader()
    cards = []

    def _card(addr, num, roster):
        addr = str(addr or "").strip().lower()
        why = follow_why_for(addr, yaml_whys, notes)
        aud = audits.get(addr) or {}
        lb = aud.get("leaderboard") if isinstance(aud.get("leaderboard"), dict) else {}
        coins = _top_coins_from_audit(aud)
        title = (why.get("title") or "").strip()
        if not roster and (not title or title == "Copy roster"):
            title = {
                "": "Sleepy shorts book (old T#2)",
                "": "HYPE / NEAR shorts",
                "": "Large ETH / BTC / HYPE longs",
                "": "BTC + HYPE longs",
            }.get(addr) or ((coins[0] + " book") if coins else "Retired leader")
        if not title:
            title = f"T#{num}" if num else "Leader"
        desc = (why.get("why") or "").strip()
        if not roster and desc.startswith("On the Copy trader list"):
            desc = "Used on this Copy desk before. Not on the live yml roster."
        if coins and "live" not in desc.lower()[:40]:
            book = " · ".join(coins)
            desc = (desc + "  Live book: " + book) if desc else ("Live book: " + book)
        pnl = our_pnl.get(num) if num else None
        color = _LEADER_COLORS[(int(num) - 1) % len(_LEADER_COLORS)] if num else "#64748b"
        return {
            "id": "copy-trader-" + addr[:10],
            "num": num,
            "address": addr,
            "short": (addr[:10] + "…" + addr[-6:]) if len(addr) > 16 else addr,
            "title": title,
            "why": desc[:280],
            "roster": bool(roster),
            "followed": bool(roster and num and num not in unfollowed),
            "unfollowed": bool(roster and num and num in unfollowed),
            "alumni": not roster,
            "day_pnl": lb.get("day_pnl"),
            "week_pnl": lb.get("week_pnl"),
            "month_pnl": lb.get("month_pnl"),
            "u_pnl": aud.get("u_pnl_total"),
            "n_positions": aud.get("n_positions"),
            "coins": coins,
            "our_wins": (pnl or {}).get("wins"),
            "our_losses": (pnl or {}).get("losses"),
            "our_net": (pnl or {}).get("net"),
            "color": color,
            "audited_at": aud.get("audited_at"),
        }

    for i, addr in enumerate(addresses, 1):
        cards.append(_card(addr, i, True))
    seen = set(addresses)
    alumni = []
    for addr, aud in audits.items():
        if addr in seen:
            continue
        alumni.append(_card(addr, None, False))
    alumni.sort(key=lambda c: str(c.get("audited_at") or ""), reverse=True)
    cards.extend(alumni)
    return cards


@app.route("/api/traders/followed")
def api_traders_followed():
    return jsonify(_followed_roster())


@app.route("/api/traders")
def api_traders():
    cfg      = load_hl_config()
    addresses = cfg.get("trader_addresses", [])
    caps     = cfg.get("trader_max_positions", {})
    mids     = fetch_all_mids()
    mirrors  = get_open_mirrors()
    history  = parse_trader_history_stats(addresses)

    # Index mirrors by coin for fast lookup
    mirror_by_coin = {m["coin"]: m for m in mirrors}

    # Enrich mirrors with current price + unrealized PnL
    for m in mirrors:
        cur = mids.get(m["coin"])
        if cur and m["entry_px"] > 0:
            if m["side"] == "LONG":
                m["pnl_pct"] = round((cur - m["entry_px"]) / m["entry_px"] * 100 * m["leverage"], 2)
            else:
                m["pnl_pct"] = round((m["entry_px"] - cur) / m["entry_px"] * 100 * m["leverage"], 2)
            m["pnl_usd"] = round(m["pnl_pct"] / 100 * m["margin"], 2)
            m["current_px"] = cur
        else:
            m["pnl_pct"]    = None
            m["pnl_usd"]    = None
            m["current_px"] = None

    # Build per-trader data
    last_act = last_trader_activity()
    yaml_text = ""
    try:
        with open(HL_COPY_CONF, "r") as f:
            yaml_text = f.read()
    except Exception:
        yaml_text = ""
    yaml_whys = _parse_yaml_trader_whys(yaml_text)
    notes = load_follow_notes()
    unfollowed_nums = set(effective_unfollowed_traders(cfg))
    traders_out = []
    for i, addr in enumerate(addresses):
        num        = i + 1
        positions  = fetch_hl_positions(addr)
        cap        = caps.get(addr)
        active_mirrors = [m for m in mirrors
                          if positions and any(p["coin"] == m["coin"] and p["side"] == m["side"]
                                               for p in (positions or []))]

        enriched_positions = []
        for p in (positions or []):
            mirrored = p["coin"] in mirror_by_coin and mirror_by_coin[p["coin"]]["side"] == p["side"]
            cur = mids.get(p["coin"])
            enriched_positions.append({**p, "mirrored": mirrored, "current_px": cur})

        why = follow_why_for(addr, yaml_whys, notes)
        followed = num not in unfollowed_nums
        traders_out.append({
            "num":       num,
            "address":   addr,
            "short":     addr[:10] + "…" + addr[-6:],
            "positions": enriched_positions,
            "error":     positions is None,
            "alive":     positions is not None,
            "last_seen": last_act.get(num),
            "cap":       cap,
            "mirroring": len(active_mirrors),
            "history":   history.get(num, {"opens": 0, "closes": 0}),
            "followed":  followed,
            "unfollowed": not followed,
            "why_title": why.get("title") or "",
            "why":       why.get("why") or "",
            "why_asked": why.get("asked") or "",
            "why_source": why.get("source") or "",
        })

    followed = [t for t in traders_out if t.get("followed")]
    return jsonify({
        "traders":  traders_out,
        "followed": followed,
        "mirrors":  mirrors,
        "config":   {
            "min_leverage":      cfg.get("min_leverage"),
            "max_leverage":      cfg.get("max_leverage"),
            "min_open_positions": cfg.get("min_open_positions"),
            "allocation_pct":    cfg.get("allocation_pct"),
        },
    })


@app.route("/api/traders/notes", methods=["POST"])
def api_traders_notes():
    """Store why we follow a leader (from chat). Does not start or stop copying."""
    data = request.get_json(silent=True) or {}
    addr = str(data.get("address") or "").strip().lower()
    if not re.fullmatch(r"0x[a-f0-9]{40}", addr):
        return jsonify({"ok": False, "error": "address must be 0x + 40 hex chars"}), 400
    notes = load_follow_notes()
    notes[addr] = {
        "title": str(data.get("title") or "Followed from chat")[:80],
        "why": str(data.get("why") or "")[:800],
        "asked": str(data.get("asked") or datetime.utcnow().strftime("%Y-%m-%d"))[:20],
        "source": "chat",
    }
    save_follow_notes(notes)
    return jsonify({"ok": True, "notes": notes[addr], "message": "follow note saved"})


def reset_copy_trades_history():
    """
    Complete trade-history reset:
      1. Archive current copy_trades.log
      2. Start a fresh log with a RESET marker
      3. Re-emit still-open mirrors so the matrix active panel stays correct
    Completed trade history becomes empty; live opens are preserved.
    """
    import shutil
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    archive_path = None

    # Capture open mirrors BEFORE truncating (from current log)
    try:
        opens = get_open_mirrors()
    except Exception:
        opens = []

    try:
        if os.path.exists(COPY_TRADES_LOG) and os.path.getsize(COPY_TRADES_LOG) > 0:
            archive_path = f"{COPY_TRADES_LOG}.archived.{ts}"
            shutil.copy2(COPY_TRADES_LOG, archive_path)
    except Exception as e:
        return {"ok": False, "error": f"archive failed: {e}"}

    try:
        with open(COPY_TRADES_LOG, "w") as f:
            f.write(f"{now_str}  TRADE HISTORY RESET  —  complete clear via dashboard\n")
            f.write(f"{now_str}  HL COPY TRADER  —  started (history reset session)\n")
            # Preserve open mirrors so Active positions + orphan logic still see them
            for m in opens:
                coin = m.get("coin", "?")
                side = m.get("side", "LONG")
                size = float(m.get("size") or 0)
                entry = float(m.get("entry_px") or 0)
                margin = float(m.get("margin") or 0)
                lev = int(m.get("leverage") or 0) or 1
                notional = float(m.get("notional") or 0)
                if notional <= 0 and size and entry:
                    notional = size * entry
                ot = m.get("open_time") if m.get("open_time") not in (None, "restored", "?") else now_str
                f.write(
                    f"{ot}  WE COPY  OPENED  {side}  {coin}  "
                    f"{size:.5f}  $ {entry:,.6f}  margin=${margin:.2f}  {lev}x  "
                    f"notional=${notional:.2f}\n"
                )
            f.write(
                f"{now_str}  [reset] History cleared — "
                f"{len(opens)} open mirror(s) carried forward\n"
            )
    except Exception as e:
        return {"ok": False, "error": f"write failed: {e}"}

    return {
        "ok": True,
        "message": f"Trade history reset — {len(opens)} open position(s) kept",
        "archived": archive_path,
        "opens_preserved": len(opens),
        "ts": now_str,
    }


@app.route("/api/copy_trades/reset", methods=["POST"])
def api_copy_trades_reset():
    """Complete reset of completed trade history (archives log first)."""
    result = reset_copy_trades_history()
    code = 200 if result.get("ok") else 500
    return jsonify(result), code


@app.route("/api/copy_trades")
def api_copy_trades():
    trades = parse_copy_trades()
    valid = [t for t in trades if not t.get("bad_price")]
    total_pnl = round(sum(t["pnl_usd"] for t in valid), 2)
    wins = sum(1 for t in valid if t["pnl_usd"] > 0)
    losses = sum(1 for t in valid if t["pnl_usd"] < 0)
    return jsonify({
        "trades": trades,
        "total_pnl": total_pnl,
        "total_trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(trades) * 100, 1) if trades else 0,
    })


@app.route("/")
def index():
    return render_template("home.html", active="home", nav_title="HOME")


@app.route("/overview")
def overview_page():
    return render_template("overview.html", active="overview", nav_title="OVERVIEW")


@app.route("/copy")
@app.route("/hl-copy")
def copy_matrix_page():
    return render_template("index.html", bots=BOTS, active="copy", nav_title="COPY")


_SQL_FILE_MAP = {
    "bn_swing_status.json": ("hummingbot-bn-swing", "status"),
    "helliot_status.json": ("hummingbot-helliot", "status"),
    "surfn_status.json": ("hummingbot-surfn", "status"),
    "shortn_status.json": ("hummingbot-shortn", "status"),
    "dman_status.json": ("hummingbot-dman", "status"),
    "onlyup_status.json": ("hummingbot-onlyup", "status"),
    "dman_maker_status.json": ("hummingbot-dman-maker", "status"),
    "hl_copy_status.json": ("hummingbot-hl", "status"),
    "bn_swing_bias.json": ("hummingbot-bn-swing", "bias"),
    "helliot_bias.json": ("hummingbot-helliot", "bias"),
    "surfn_bias.json": ("hummingbot-surfn", "bias"),
    "shortn_bias.json": ("hummingbot-shortn", "bias"),
    "dman_bias.json": ("hummingbot-dman", "bias"),
    "onlyup_bias.json": ("hummingbot-onlyup", "bias"),
    "dman_maker_bias.json": ("hummingbot-dman-maker", "bias"),
}


def _parse_stamp(s):
    t = str(s or "").replace("T", " ").replace(" UTC", "").strip().split(".")[0]
    if len(t) >= 19:
        t = t[:19]
    try:
        return datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _bias_modes(raw):
    out = {}
    coins = raw.get("coins") if isinstance(raw, dict) and isinstance(raw.get("coins"), dict) else {}
    src = dict(coins)
    if isinstance(raw, dict):
        src.update(raw)
    for k, v in src.items():
        if str(k).lower() in ("updated", "source", "coins", "bias"):
            continue
        m = str(v or "").upper()
        if m in ("LONG", "SHORT", "FLAT"):
            out[str(k).upper()] = m
    return out


def _bias_json_raw(bot_id, path=None):
    if not path:
        h = registry_hunter(bot_id) or {}
        fname = os.path.basename(h.get("bias_file") or "")
        path = os.path.join(CONF_DIR, fname) if fname else ""
    raw = {}
    ts = None
    if path:
        try:
            with open(path, "r") as f:
                raw = json.load(f) or {}
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        ts = _parse_stamp(raw.get("updated"))
        if ts is None:
            try:
                ts = datetime.utcfromtimestamp(os.path.getmtime(path))
            except Exception:
                ts = None
    return raw, ts


def _live_bias_raw(bot_id, path=None):
    """SQLite is live. JSON dump only if this hunter has no SQL bias rows."""
    sql_raw = _sql_bias_read(bot_id) or {}
    if _bias_modes(sql_raw):
        return sql_raw
    file_raw, _ts = _bias_json_raw(bot_id, path)
    return file_raw


def _read_json_file(path):
    base = os.path.basename(path or "")
    mapped = _SQL_FILE_MAP.get(base)
    if mapped:
        bot_id, kind = mapped
        if kind == "bias":
            data = _live_bias_raw(bot_id, path)
        else:
            data = _sql_status_read(bot_id)
        if isinstance(data, dict) and data:
            return data
    try:
        with open(path, "r") as f:
            raw = f.read()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = json.loads(raw, strict=False)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _sl_roe_for(bot_id, default=20.0):
    sql = _sql_kv_map(bot_id)
    if sql.get("sl_roe_pct") is not None:
        try:
            v = float(sql.get("sl_roe_pct"))
            if 1.0 <= v <= 90.0:
                return round(v, 2)
        except (TypeError, ValueError):
            pass
    data = _read_json_file(BOT_SL_FILE)
    row = data.get(bot_id) if isinstance(data, dict) else None
    if isinstance(row, dict):
        try:
            v = float(row.get("sl_roe_pct"))
            if 1.0 <= v <= 90.0:
                return round(v, 2)
        except (TypeError, ValueError):
            pass
    if default is None:
        return None
    try:
        return round(float(default), 2)
    except (TypeError, ValueError):
        return 20.0


BRIEF_TOKEN_FILE = os.path.join(CONF_DIR, "brief_token.txt")
BRIEF_MD_FILE = os.path.join(CONF_DIR, "grok_brief.md")
HOME_BRIEF_SPECS = _LazySeq(get_home_brief_specs)


def _brief_token() -> str:
    env = str(os.environ.get("BRIEF_TOKEN") or "").strip()
    if env:
        return env
    try:
        with open(BRIEF_TOKEN_FILE, "r") as f:
            tok = (f.read() or "").strip()
        if tok:
            return tok
    except Exception:
        pass
    tok = secrets.token_urlsafe(24)
    try:
        with open(BRIEF_TOKEN_FILE, "w") as f:
            f.write(tok + "\n")
    except Exception:
        pass
    return tok


def _brief_authorized() -> bool:
    expected = _brief_token()
    if not expected:
        return False
    got = (
        str(request.headers.get("X-Brief-Token") or "").strip()
        or str(request.args.get("token") or "").strip()
    )
    if not got or len(got) != len(expected):
        return False
    return hmac.compare_digest(got, expected)


def _fmt_usd_brief(n):
    try:
        x = float(n)
    except (TypeError, ValueError):
        return "—"
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


def _fmt_roe_brief(n):
    try:
        x = float(n)
    except (TypeError, ValueError):
        return ""
    return f"{x:+.1f}% ROE"


def render_desk_brief() -> str:
    """Read-only desk snapshot from local status files (no extra HL REST)."""
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    n_ok = 0
    n_bots = 0
    n_pos = 0
    eq_sum = 0.0
    n_eq = 0
    lines = []
    for spec in HOME_BRIEF_SPECS:
        n_bots += 1
        status = _read_json_file(spec["status_file"])
        running = get_container_running(spec["id"])
        hh = home_bot_logs_and_health(spec["id"], running, status)
        ok = bool(hh.get("ok"))
        if ok:
            n_ok += 1
        eq = status.get("equity_usd")
        if eq is None:
            eq = status.get("equity")
        free = status.get("free_usd")
        if free is None:
            free = status.get("free")
        try:
            if eq is not None:
                eq_sum += float(eq)
                n_eq += 1
        except (TypeError, ValueError):
            pass
        lamp = "OK" if ok else str(hh.get("health_reason") or "issue").upper()
        eq_s = _fmt_usd_brief(eq)
        free_s = _fmt_usd_brief(free)
        lines.append(f"## {spec['name']}  {lamp}  eq {eq_s}  free {free_s}")
        rows = []
        if spec["kind"] == "copy":
            for m in status.get("mirrors") or []:
                coin = str(m.get("coin") or "").upper()
                side = str(m.get("side") or "").upper()
                if not coin or not side:
                    continue
                n_pos += 1
                tnum = m.get("trader")
                tlab = f" T#{tnum}" if tnum not in (None, "", "restored") else ""
                sz = m.get("size") or m.get("amount")
                lev = m.get("leverage")
                rows.append(
                    f"- {coin} {side}{tlab}  sz={sz}  {lev or '?'}x  "
                    f"entry {m.get('entry_px') or m.get('entry_price') or '—'}"
                )
        else:
            for coin, pos in (status.get("positions") or {}).items():
                if not isinstance(pos, dict):
                    continue
                side = pos.get("position_side") or pos.get("side")
                if not side:
                    continue
                n_pos += 1
                roe = _fmt_roe_brief(pos.get("roe_pct"))
                rows.append(
                    f"- {coin} {side}  amt={pos.get('amount')}  "
                    f"{pos.get('leverage') or '?'}x  "
                    f"entry {pos.get('entry_price') or '—'}  mark {pos.get('mid_price') or '—'}  {roe}"
                )
        if not rows:
            lines.append("- (flat)")
        else:
            lines.extend(rows)
        lines.append("")
    try:
        rt = _hl_ip_snapshot()
    except Exception:
        rt = {}
    used = rt.get("used")
    cap = rt.get("cap") or 1200
    left = rt.get("left")
    refill = rt.get("refill_in_s")
    last429 = rt.get("last_429_s")
    hit = "no 429 in window" if last429 is None else f"last 429 {last429}s ago"
    head = [
        f"# Freiquell desk  {ts}",
        f"Health: {n_ok}/{n_bots} ok  ·  open positions: {n_pos}  ·  equity ~ {_fmt_usd_brief(eq_sum) if n_eq else '—'}",
        f"HL IP tokens: {used}/{cap} used, {left} left, next refill {refill}s  ·  {hit}",
        "Read-only. No keys. Do not open, close, or restart anything from this brief.",
        "",
    ]
    return "\n".join(head + lines).strip() + "\n"


def _write_desk_brief_file():
    md = render_desk_brief()
    try:
        tmp = BRIEF_MD_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(md)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, BRIEF_MD_FILE)
    except Exception:
        pass
    return md


def _brief_loop():
    while True:
        try:
            _write_desk_brief_file()
        except Exception:
            pass
        time.sleep(45)


def _roe_from_live(side, entry, mark, lev):
    try:
        entry = float(entry)
        mark = float(mark)
        lev = float(lev or 1) or 1
        if entry <= 0 or mark <= 0:
            return None
        if str(side).upper() == "SHORT":
            move = (entry - mark) / entry
        else:
            move = (mark - entry) / entry
        return round(move * lev * 100.0, 2)
    except (TypeError, ValueError):
        return None


def _home_bias_for(bot_id):
    """Read-only per-coin bias for Wire / Home. SQLite first."""
    h = registry_hunter(bot_id) or {}
    coins = tuple(h.get("coins") or ())
    if not coins:
        coins = {
            "hummingbot-bn-swing": ("BTC", "ETH", "LTC", "TAO"),
            "hummingbot-helliot": ("BTC", "ETH", "LTC", "TAO"),
            "hummingbot-surfn": ("BTC", "ETH", "LTC", "TAO"),
            "hummingbot-shortn": ("BTC", "ETH", "LTC", "TAO"),
            "hummingbot-dman": ("BTC", "ETH", "LTC", "TAO"),
            "hummingbot-dman-maker": ("BTC", "ETH", "LTC", "TAO"),
            "hummingbot-onlyup": ONLYUP_COINS,
        }.get(bot_id) or ()
    if not coins:
        return None
    return _hunter_bias_payload(bot_id, coins=coins)


def _fmt_px(px):
    try:
        x = float(px)
    except (TypeError, ValueError):
        return None
    if x >= 1000:
        return f"${x:,.0f}"
    if x >= 100:
        return f"${x:,.2f}"
    if x >= 1:
        return f"${x:,.4f}"
    return f"${x:.6f}"


def _fmt_dur(seconds):
    try:
        s = max(0, int(seconds))
    except (TypeError, ValueError):
        return None
    h, rem = divmod(s, 3600)
    m = rem // 60
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def _px_at_roe(entry, side, lev, roe_pct):
    """Mark where ROE hits roe_pct (positive = profit for that side)."""
    try:
        entry = float(entry)
        lev = float(lev) if lev else 1.0
        roe = float(roe_pct)
    except (TypeError, ValueError):
        return None
    if entry <= 0 or lev <= 0:
        return None
    frac = roe / 100.0 / lev
    if str(side or "LONG").upper() == "SHORT":
        return entry * (1.0 - frac)
    return entry * (1.0 + frac)


def _as_f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _helliot_zone_from_legacy(pos, status):
    """Rebuild W2 long fib zone from target / invalid / retrace if zone_lo missing."""
    if str((pos or {}).get("wave_dir") or "").upper() != "UP":
        return None, None
    c = _as_f((pos or {}).get("invalid") or (pos or {}).get("wave_c"))
    tgt = _as_f((pos or {}).get("target"))
    retr = _as_f((pos or {}).get("retrace"))
    ext = _as_f((status or {}).get("wave3_ext")) or 1.618
    wmin = _as_f((status or {}).get("wave2_min")) or 0.382
    wmax = _as_f((status or {}).get("wave2_max")) or 0.786
    if not c or not tgt or ext <= 0 or retr is None:
        return None, None
    w1 = (tgt - c) / ext
    if w1 <= 0:
        return None, None
    high = c + retr * w1
    lo, hi = high - wmax * w1, high - wmin * w1
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _swing_reopen_long(coin, pos, status, bias_mode, in_pos):
    pos = pos if isinstance(pos, dict) else {}
    b = str(bias_mode or "FLAT").upper()
    bbp = _as_f(pos.get("bbp"))
    thr = _as_f((status or {}).get("bb_long_threshold")) or 0.25
    trend = pos.get("trend_up")
    mid = _as_f(pos.get("mid_price"))
    trig = _as_f(pos.get("bb_long_px"))
    if trig is None:
        lo = _as_f(pos.get("bb_lower"))
        hi = _as_f(pos.get("bb_upper"))
        if lo is not None and hi is not None and hi > lo:
            trig = lo + float(thr) * (hi - lo)
    now_bbp = f"BBP {bbp:.2f}" if bbp is not None else "BBP n/a"
    sma = "SMA up" if trend is True else ("SMA not up" if trend is False else "SMA n/a")
    if in_pos:
        extra = f" ≤ {_fmt_px(trig)}" if trig else f" BBP ≤ {thr:g}"
        return f"{coin} after exit reopen LONG{extra} + 1h SMA up", False
    if b != "LONG":
        return f"{coin} {b} — no long reopen", False
    ready = bool(pos.get("long_ok")) or (
        bbp is not None and bbp <= float(thr) and trend is not False
    )
    if ready:
        mark = f" @ {_fmt_px(mid)}" if mid else ""
        return f"{coin} READY now · {now_bbp} ≤ {thr:g} · {sma}{mark}", True
    bits = [f"{coin} reopen LONG"]
    if trig:
        bits.append(f"when ≤ {_fmt_px(trig)} (BBP {thr:g})")
    else:
        bits.append(f"when BBP ≤ {thr:g}")
    bits.append(f"now {_fmt_px(mid) or '—'} {now_bbp} · {sma}")
    return " · ".join(bits), False


def _helliot_reopen_long(coin, pos, status, bias_mode, in_pos):
    pos = pos if isinstance(pos, dict) else {}
    b = str(bias_mode or "FLAT").upper()
    direction = str(pos.get("wave_dir") or "").upper()
    wave = pos.get("wave") or "wave wait"
    retr = _as_f(pos.get("retrace"))
    lo = _as_f(pos.get("zone_lo"))
    hi = _as_f(pos.get("zone_hi"))
    if lo is None or hi is None:
        lo, hi = _helliot_zone_from_legacy(pos, status)
    zone = None
    if lo is not None and hi is not None:
        a, bpx = (lo, hi) if lo <= hi else (hi, lo)
        zone = f"{_fmt_px(a)}–{_fmt_px(bpx)}"
    retr_s = f"retr {retr:.2f}" if retr is not None else ""
    if in_pos:
        extra = f" in {zone}" if zone else " · fib 38–79%"
        return f"{coin} after exit reopen LONG{extra}", False
    if b != "LONG":
        return f"{coin} {b} — no long reopen", False
    if direction == "DOWN":
        return f"{coin} wave DOWN ({wave}) — no long reopen", False
    if pos.get("long_ok"):
        extra = f" in {zone}" if zone else ""
        return f"{coin} READY now · W2 long zone{extra} ({wave})", True
    bits = [f"{coin} reopen LONG"]
    if zone:
        bits.append(f"when W2 into {zone}")
    else:
        bits.append("when W2 fib 38–79% + bounce")
    extra = wave
    if retr_s:
        extra = f"{extra} · {retr_s}"
    bits.append(extra)
    return " · ".join(bits), False


def _moon_coins_for(bot_id):
    if bot_id == "hummingbot-onlyup":
        return ONLYUP_COINS
    return ("BTC", "ETH", "LTC", "TAO")


def _load_hunter_moon(bot_id):
    coins = _moon_coins_for(bot_id)
    out = {c: {"on": False} for c in coins}
    if bot_id == "hummingbot-onlyup":
        try:
            with open(ONLYUP_MOON_FILE, "r") as f:
                raw = json.load(f) or {}
            if isinstance(raw, dict):
                for c in coins:
                    row = raw.get(c)
                    if isinstance(row, dict):
                        out[c] = row
        except Exception:
            pass
    sql_moon = _sql_kv_map(bot_id).get("moon")
    if isinstance(sql_moon, dict):
        for c in coins:
            row = sql_moon.get(c)
            if isinstance(row, dict):
                out[c] = row
    return out


def _load_onlyup_moon():
    return _load_hunter_moon("hummingbot-onlyup")


def _save_hunter_moon(bot_id, payload):
    coins = _moon_coins_for(bot_id)
    data = dict(payload or {})
    data["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    data["source"] = "dashboard"
    coin_map = {k: v for k, v in data.items() if k in coins and isinstance(v, dict)}
    _sql_kv_set(bot_id, "moon", coin_map, "moon")
    if bot_id == "hummingbot-onlyup":
        try:
            with open(ONLYUP_MOON_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass
    return data


def _save_onlyup_moon(payload):
    return _save_hunter_moon("hummingbot-onlyup", payload)


def _status_file_for(bot_id):
    h = registry_hunter(bot_id) or {}
    name = os.path.basename(h.get("status_file") or "")
    return os.path.join(CONF_DIR, name) if name else ""


def _set_manual_close_px(bot_id, px, coin=None):
    """Arm moon bag at an absolute USD price. Does not close the clip."""
    try:
        px = float(px)
    except (TypeError, ValueError):
        return {"ok": False, "error": "close $ must be a number", "closes": False}
    if px > 1e7:
        return {"ok": False, "error": "close $ out of range", "closes": False}
    if px <= 0:
        coins = _moon_coins_for(bot_id)
        cur = _load_hunter_moon(bot_id)
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        for c in coins:
            row = dict(cur.get(c) or {})
            row["on"] = False
            row.pop("close_px", None)
            cur[c] = row
        _save_hunter_moon(bot_id, cur)
        return {
            "ok": True,
            "closes": False,
            "positions_untouched": True,
            "close_px": 0,
            "message": "moon OFF — clips stay, SL/clip on again",
        }
    coins = _moon_coins_for(bot_id)
    st = _read_json_file(_status_file_for(bot_id)) or {}
    pos = st.get("positions") if isinstance(st.get("positions"), dict) else {}
    target_coin = str(coin or "").upper()
    if target_coin not in coins:
        target_coin = None
        for c in coins:
            row = pos.get(c) or {}
            if str(row.get("position_side") or "").upper() in ("LONG", "SHORT"):
                target_coin = c
                break
        if not target_coin:
            target_coin = "LTC" if "LTC" in coins else coins[0]
    prow = pos.get(target_coin) if isinstance(pos.get(target_coin), dict) else {}
    try:
        mark = float(prow.get("mid_price") or prow.get("mark_px") or 0)
    except (TypeError, ValueError):
        mark = 0.0
    side = str(prow.get("position_side") or "").upper()
    if side == "LONG" and mark > 0 and px <= mark + 1e-9:
        return {
            "ok": False,
            "error": f"close ${px:g} ≤ mark ${mark:g} — would flatten {target_coin} now",
            "closes": False,
            "positions_untouched": True,
        }
    if side == "SHORT" and mark > 0 and px >= mark - 1e-9:
        return {
            "ok": False,
            "error": f"close ${px:g} ≥ mark ${mark:g} — would flatten SHORT {target_coin} now",
            "closes": False,
            "positions_untouched": True,
        }
    cur = _load_hunter_moon(bot_id)
    row = dict(cur.get(target_coin) or {})
    try:
        anchor = float(row.get("anchor_px") or 0)
    except (TypeError, ValueError):
        anchor = 0.0
    if anchor <= 0:
        try:
            anchor = float(prow.get("entry_price") or prow.get("mid_price") or 0)
        except (TypeError, ValueError):
            anchor = 0.0
    if side == "SHORT" and anchor > 0 and px > 0:
        pct = ((anchor / px) - 1.0) * 100.0
    else:
        pct = ((px / anchor) - 1.0) * 100.0 if anchor > 0 else 0.0
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    cur[target_coin] = {
        "on": True,
        "close_px": round(px, 6),
        "target_px": round(px, 6),
        "anchor_px": anchor or None,
        "coin_pct": round(pct, 6) if pct else 50.0,
        "armed": ts,
        "source": "matrix",
    }
    _save_hunter_moon(bot_id, cur)
    verb = "cover" if side == "SHORT" else "sell"
    return {
        "ok": True,
        "closes": False,
        "positions_untouched": True,
        "coin": target_coin,
        "close_px": px,
        "message": f"{target_coin} {verb} at ${px:g} — clip stays until then",
    }


def _arm_hunter_moon(bot_id, moon, coin, ts):
    """Arm or retarget moon hold. Does not close the position."""
    coins = _moon_coins_for(bot_id)
    target_coin = coin if coin in coins else None
    cur = _load_hunter_moon(bot_id)
    if not target_coin:
        for c in coins:
            row = cur.get(c) or {}
            if isinstance(row, dict) and row.get("on"):
                target_coin = c
                break
    if not target_coin:
        st = _read_json_file(_status_file_for(bot_id)) or {}
        pos = st.get("positions") if isinstance(st.get("positions"), dict) else {}
        for c in coins:
            row = pos.get(c) or {}
            if str(row.get("position_side") or "").upper() in ("LONG", "SHORT"):
                target_coin = c
                break
    if not target_coin:
        target_coin = "LTC" if "LTC" in coins else coins[0]
    row = dict(cur.get(target_coin) or {})
    try:
        old_pct = float(row.get("coin_pct") or 0)
    except (TypeError, ValueError):
        old_pct = 0.0
    was_on = bool(row.get("on"))
    if was_on and abs(old_pct - float(moon)) < 0.01:
        return None
    try:
        anchor = float(row.get("anchor_px") or 0) if was_on else 0.0
    except (TypeError, ValueError):
        anchor = 0.0
    st = _read_json_file(_status_file_for(bot_id)) or {}
    pos = ((st.get("positions") or {}).get(target_coin) or {})
    side = str(pos.get("position_side") or "").upper()
    if anchor <= 0:
        try:
            anchor = float(pos.get("mid_price") or pos.get("entry_price") or 0)
        except (TypeError, ValueError):
            anchor = 0.0
    if side == "SHORT" and anchor:
        target = anchor * (1.0 - float(moon) / 100.0)
        if target <= 0:
            target = anchor * 0.01
    else:
        target = (anchor * (1.0 + float(moon) / 100.0)) if anchor else None
    cur[target_coin] = {
        "on": True,
        "coin_pct": float(moon),
        "anchor_px": anchor or None,
        "target_px": target,
        "armed": ts,
        "source": "wire",
    }
    _save_hunter_moon(bot_id, cur)
    short = bot_id.replace("hummingbot-", "")
    return f"{short} moon +{moon:g}% {target_coin}"


def _home_next_for(bot_id, status, bias, sl_roe):
    """When each hunter will auto-sell and auto-rebuy. Display only."""
    status = status if isinstance(status, dict) else {}
    bias = bias if isinstance(bias, dict) else {}
    sl = _as_f(sl_roe)
    pos_all = status.get("positions") if isinstance(status.get("positions"), dict) else {}
    sell_parts = []
    buy_parts = []
    reopen_ready = False

    def lev_for(coin, pos, default=10):
        v = _as_f((pos or {}).get("leverage"))
        if v and v >= 1:
            return int(v)
        lm = status.get("leverage") if isinstance(status.get("leverage"), dict) else {}
        v = _as_f(lm.get(coin))
        if v and v >= 1:
            return int(v)
        return int(default)

    if bot_id == "hummingbot-hl":
        sl_txt = f"Home SL −{sl:g}% ROE" if sl else "no Home SL until you arm it"
        sell_parts.append(f"leader close · {sl_txt} · max −1/3 equity")
        buy_parts.append("when a followed leader opens")
        return {
            "sell": " · ".join(sell_parts),
            "rebuy": " · ".join(buy_parts),
        }

    if bot_id == "hummingbot-dman-maker":
        skip = str(status.get("quote_skip") or "").strip()
        free = _as_f(status.get("free_usd")) or 0.0
        if not skip and free < 1:
            skip = "perp free $0 — fund Hyperliquid perps USD on this sub (spot USDC does not trade)"
        wired = str(status.get("wired_coin") or "").upper()
        coins = [wired] if wired in ("BTC", "ETH", "LTC", "TAO") else ("BTC", "ETH", "LTC", "TAO")
        for coin in coins:
            pos = pos_all.get(coin) if isinstance(pos_all.get(coin), dict) else {}
            side = str(pos.get("position_side") or pos.get("side") or "").upper() or None
            b = str(bias.get(coin) or pos.get("bias") or "FLAT").upper()
            mid = _as_f(pos.get("mid_price"))
            if side == "LONG":
                bits = []
                if sl:
                    bits.append(f"SL −{sl:g}% ROE")
                tp = _as_f(status.get("take_profit")) or 0.02
                bits.append(f"TP {tp * 100:g}% price")
                if pos.get("trail_active"):
                    bits.append(f"trail {_fmt_px(pos.get('trail_extreme'))}")
                sell_parts.append(f"{coin} LONG " + " · ".join(bits))
                continue
            if b != "LONG":
                buy_parts.append(f"{coin} {b or 'FLAT'} — up-only idle")
                continue
            bits = [f"{coin} LONG up-only · market buy leftover perp USD"]
            if mid:
                bits.append(f"at ~{_fmt_px(mid)}")
            if skip:
                bits.append(skip)
            buy_parts.append(" · ".join(bits))
        return {
            "sell": " · ".join(sell_parts) or "no open long — nothing to sell",
            "rebuy": " · ".join(buy_parts) or "set LONG on the wired coin",
        }

    if bot_id == "hummingbot-bn-swing":
        skip = str(status.get("quote_skip") or "").strip()
        free = _as_f(status.get("free_usd")) or 0.0
        if not skip and free < 1:
            skip = "perp free $0 — fund Hyperliquid perps USD on this sub (spot USDC does not trade)"
        wired = str(status.get("wired_coin") or "").upper()
        coins = [wired] if wired in ("BTC", "ETH", "LTC", "TAO") else ("BTC", "ETH", "LTC", "TAO")
        for coin in coins:
            pos = pos_all.get(coin) if isinstance(pos_all.get(coin), dict) else {}
            side = str(pos.get("position_side") or pos.get("side") or "").upper() or None
            b = str(bias.get(coin) or pos.get("bias") or "FLAT").upper()
            mid = _as_f(pos.get("mid_price"))
            if side == "LONG":
                bits = []
                if sl:
                    bits.append(f"SL −{sl:g}% ROE")
                tp = _as_f(status.get("take_profit")) or 0.02
                bits.append(f"TP {tp * 100:g}% price")
                if pos.get("trail_active"):
                    bits.append(f"trail {_fmt_px(pos.get('trail_extreme'))}")
                sell_parts.append(f"{coin} LONG " + " · ".join(bits))
                continue
            if b != "LONG":
                buy_parts.append(f"{coin} {b or 'FLAT'} — up-only idle")
                continue
            bits = [f"{coin} LONG pmm_simple · rest bids at buy_spreads"]
            if mid:
                bits.append(f"mid ~{_fmt_px(mid)}")
            if skip:
                bits.append(skip)
            buy_parts.append(" · ".join(bits))
        return {
            "sell": " · ".join(sell_parts) or "no open long — nothing to sell",
            "rebuy": " · ".join(buy_parts) or "set LONG on the wired coin",
        }

    if bot_id == "hummingbot-dman":
        hold_s = _dman_hold_s()
        tune = _dman_tune_map(status)
        rows = _dman_rebuy_from_status(status, bias, hold_s, sl, tune)
        for r in rows or []:
            coin = r.get("coin")
            if r.get("side"):
                bits = []
                if r.get("trail_live") and r.get("trail_stop"):
                    bits.append(f"trail {_fmt_px(r['trail_stop'])}")
                elif r.get("trail_arm"):
                    bits.append(f"trail after {_fmt_px(r['trail_arm'])}")
                if r.get("sl_px") and sl:
                    bits.append(f"SL {_fmt_px(r['sl_px'])} (−{sl:g}%)")
                if r.get("time_left_s") is not None:
                    bits.append(f"TIME {_fmt_dur(r['time_left_s'])}")
                if bits:
                    sell_parts.append(f"{coin} " + " · ".join(bits))
                if r.get("long_px") and str(r.get("bias") or "").upper() == "LONG":
                    buy_parts.append(f"{coin} next long ~{_fmt_px(r['long_px'])} (BBP)")
                elif r.get("short_px") and str(r.get("bias") or "").upper() == "SHORT":
                    buy_parts.append(f"{coin} next short ~{_fmt_px(r['short_px'])} (BBP)")
            else:
                b = str(r.get("bias") or "FLAT").upper()
                if b == "LONG" and r.get("trigger"):
                    buy_parts.append(f"{coin} long ~{_fmt_px(r['trigger'])} (BBP)")
                elif b == "SHORT" and r.get("trigger"):
                    buy_parts.append(f"{coin} short ~{_fmt_px(r['trigger'])} (BBP)")
                elif b == "FLAT":
                    buy_parts.append(f"{coin} FLAT")
        return {
            "sell": " · ".join(sell_parts) or "no open clip — nothing to sell",
            "rebuy": " · ".join(buy_parts) or "FLAT — no auto rebuy",
        }

    coins = ("BTC", "LTC", "TAO") if bot_id == "hummingbot-onlyup" else ("BTC", "ETH", "LTC", "TAO")
    tp = _as_f(status.get("tp_roe_pct"))
    scale = _as_f(status.get("scale_roe_pct"))
    recover = _as_f(status.get("recover_roe_pct")) or 20.0
    cooldown = status.get("cooldown") if isinstance(status.get("cooldown"), dict) else {}

    for coin in coins:
        pos = pos_all.get(coin) if isinstance(pos_all.get(coin), dict) else {}
        side = str(pos.get("position_side") or pos.get("side") or "").upper() or None
        b = str(bias.get(coin) or pos.get("bias") or "FLAT").upper()
        entry = _as_f(pos.get("entry_price") or pos.get("entry_px"))
        mid = _as_f(pos.get("mid_price"))
        lev = lev_for(coin, pos)
        bbp = _as_f(pos.get("bbp"))

        if side in ("LONG", "SHORT") and entry:
            if bot_id in ("hummingbot-bn-swing", "hummingbot-helliot"):
                tp_use = tp or 93.0
                bits = []
                tp_px = _px_at_roe(entry, side, lev, tp_use)
                sl_px = _px_at_roe(entry, side, lev, -(sl or 20.0))
                if tp_px:
                    bits.append(f"TP {_fmt_px(tp_px)} (+{tp_use:g}%)")
                if sl_px and sl:
                    bits.append(f"SL {_fmt_px(sl_px)} (−{sl:g}%)")
                if bot_id == "hummingbot-helliot":
                    if _as_f(pos.get("target")):
                        bits.append(f"wave {_fmt_px(pos.get('target'))}")
                    if _as_f(pos.get("invalid")):
                        bits.append(f"invalid {_fmt_px(pos.get('invalid'))}")
                sell_parts.append(f"{coin} " + " · ".join(bits))
            elif bot_id in ("hummingbot-surfn", "hummingbot-onlyup"):
                moon_row = (_load_hunter_moon(bot_id).get(coin) or {})
                if not moon_row.get("on") and pos.get("moon"):
                    moon_row = {
                        "on": True,
                        "coin_pct": pos.get("moon_pct") or ONLYUP_MOON_PCT,
                        "target_px": pos.get("moon_target_px"),
                        "anchor_px": pos.get("moon_anchor_px"),
                    }
                if moon_row.get("on"):
                    cpx = _as_f(moon_row.get("close_px"))
                    pct = _as_f(moon_row.get("coin_pct")) or ONLYUP_MOON_PCT
                    tgt = cpx or _as_f(moon_row.get("target_px")) or pos.get("moon_target_px")
                    if cpx:
                        bits = [f"sell at {_fmt_px(cpx)}"]
                    else:
                        bits = [f"MOON hold · sell when coin +{pct:g}%"]
                        if tgt:
                            bits.append(_fmt_px(tgt))
                    bits.append("ROE SL/clip/ST off")
                    sell_parts.append(f"{coin} " + " · ".join(bits))
                else:
                    sc = scale or 90.0
                    bits = []
                    if not pos.get("scaled_out"):
                        clip_px = _px_at_roe(entry, "LONG", lev, sc)
                        if clip_px:
                            bits.append(f"50% @{_fmt_px(clip_px)} (+{sc:g}%)")
                        sl_px = _px_at_roe(entry, "LONG", lev, -(sl or 20.0))
                        if sl_px and sl:
                            bits.append(f"SL {_fmt_px(sl_px)} (−{sl:g}%)")
                        bits.append("then trail runner")
                    else:
                        trail = _as_f(pos.get("trail_px"))
                        if trail:
                            bits.append(f"runner trail {_fmt_px(trail)}")
                        else:
                            bits.append("runner trail ST/EMA-21")
                        bits.append("ST/trend flip sells")
                    sell_parts.append(f"{coin} " + " · ".join(bits))
            elif bot_id == "hummingbot-shortn":
                tp_use = tp or 90.0
                bits = []
                tp_px = _px_at_roe(entry, side, lev, tp_use)
                sl_px = _px_at_roe(entry, side, lev, -(sl or 20.0))
                if tp_px:
                    bits.append(f"TP {_fmt_px(tp_px)} (+{tp_use:g}%)")
                if sl_px and sl:
                    bits.append(f"SL {_fmt_px(sl_px)} (−{sl:g}%)")
                sell_parts.append(f"{coin} " + " · ".join(bits))

        if bot_id in ("hummingbot-bn-swing", "hummingbot-helliot"):
            in_pos = side in ("LONG", "SHORT")
            if bot_id == "hummingbot-bn-swing":
                line, ready = _swing_reopen_long(coin, pos, status, b, in_pos)
            else:
                line, ready = _helliot_reopen_long(coin, pos, status, b, in_pos)
            buy_parts.append(line)
            if ready:
                reopen_ready = True
            continue

        if side:
            if bot_id == "hummingbot-surfn":
                buy_parts.append(f"{coin} add on next EMA-21 tag")
            elif bot_id == "hummingbot-onlyup":
                buy_parts.append(f"{coin} after exit · ST long + ≤1.2% from line")
            elif bot_id == "hummingbot-shortn":
                buy_parts.append(f"{coin} after TP wait +{recover:g}% ROE bounce")
            continue

        if b == "FLAT":
            buy_parts.append(f"{coin} FLAT — no auto")
            continue
        if bot_id == "hummingbot-surfn":
            if b == "LONG":
                buy_parts.append(f"{coin} EMA-21 tag / BBP 0.35–0.52 + 1h SMA up")
            else:
                buy_parts.append(f"{coin} FLAT — no auto")
        elif bot_id == "hummingbot-onlyup":
            if b == "LONG":
                sig = pos.get("signal") or "ST long + 1h SMA up"
                buy_parts.append(f"{coin} {sig}")
            else:
                buy_parts.append(f"{coin} FLAT — no auto")
        elif bot_id == "hummingbot-shortn":
            cd = cooldown.get(coin) if isinstance(cooldown.get(coin), dict) else {}
            need = _as_f(cd.get("need_roe")) or recover
            rec = _as_f(cd.get("roe") or cd.get("now_roe") or pos.get("need_bounce_roe"))
            exit_px = _as_f(cd.get("exit_px"))
            if exit_px:
                extra = f" now {rec:.0f}%" if rec is not None else ""
                buy_parts.append(
                    f"{coin} short after bounce +{need:g}% from {_fmt_px(exit_px)}{extra}"
                )
            elif b == "SHORT":
                now_bbp = f"BBP {bbp:.2f}" if bbp is not None else "BBP n/a"
                buy_parts.append(f"{coin} short when BBP high + 1h SMA not up ({now_bbp})")
            else:
                buy_parts.append(f"{coin} FLAT — no auto")

    return {
        "sell": " · ".join(sell_parts) or "no open trade — nothing to auto-sell",
        "rebuy": " · ".join(buy_parts) or "no auto rebuy",
        "reopen_ready": bool(reopen_ready),
    }


def _hunter_bias_mode(bot_id):
    """100% leftover follows LONG or SHORT on every BIAS hunter. Copy has no BIAS rail."""
    if str(bot_id or "") in ("", "hummingbot-hl"):
        return ""
    return "both"


def _clip_desk_bias(want, bot_id):
    """LONG/SHORT/FLAT clipped to what that hunter is allowed to take."""
    want = str(want or "").upper()
    if want not in ("LONG", "SHORT", "FLAT"):
        return "FLAT"
    mode = _hunter_bias_mode(bot_id)
    if mode == "long" and want == "SHORT":
        return "FLAT"
    if mode == "short" and want == "LONG":
        return "FLAT"
    return want


def _status_held_coins(status):
    """Coins the hunter itself thinks are in a clip. Never used to send closes."""
    held = []
    if not isinstance(status, dict):
        return held
    for coin, pos in (status.get("positions") or {}).items():
        if not isinstance(pos, dict):
            continue
        side = str(pos.get("position_side") or pos.get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            continue
        amt = pos.get("amount")
        if amt is None:
            amt = pos.get("size")
        try:
            if amt is not None and abs(float(amt)) < 1e-12:
                continue
        except (TypeError, ValueError):
            pass
        held.append(str(coin).upper())
    return held


def _desk_bias_status_path(bot_id):
    h = registry_hunter(bot_id) or {}
    status = h.get("status_file") or ""
    if status:
        return status if str(status).startswith("/") else os.path.join(CONF_DIR, os.path.basename(status))
    spec = CONDOR_HUNTERS.get(bot_id) or {}
    bias_path = spec.get("bias") or ""
    base = os.path.basename(bias_path).replace("_bias.json", "_status.json")
    return os.path.join(CONF_DIR, base) if base else ""


def _write_hunter_bias_map(bot_id, mapping, source="home-desk"):
    """Write live BIAS json + sqlite. Does not close, open, restart, or enqueue commands."""
    if not flask_writes_on():
        return None
    spec = CONDOR_HUNTERS.get(bot_id) or {}
    h = registry_hunter(bot_id) or {}
    coins = list(h.get("coins") or spec.get("coins") or ())
    if not coins:
        return None
    fname = os.path.basename(h.get("bias_file") or "")
    path = spec.get("bias") or (os.path.join(CONF_DIR, fname) if fname else "")
    if not path:
        return None
    payload = {}
    for c in coins:
        v = str((mapping or {}).get(c) or "FLAT").upper()
        if v not in ("LONG", "SHORT", "FLAT"):
            v = "FLAT"
        payload[c] = v
    payload["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    payload["source"] = source
    _sql_bias_write(bot_id, payload)
    _write_json_file(path, payload)
    return payload


def _desk_bias_read():
    raw = _read_json_file(DESK_BIAS_FILE)
    return raw if isinstance(raw, dict) else {}


def _apply_desk_bias():
    """Push Home desk BIAS into hunter bias files.

    Never closes or opens. A hunter in a clip keeps holding — exits stay SL/TP/trail.
    Writing the new bias now only changes what they do *after* they autoclose
    (no rebuy on the old side). Idle coins on a hunter that still has another
    clip are not armed LONG/SHORT (that would open a second coin).
    Copy is skipped (no BIAS).
    """
    if not flask_writes_on():
        return []
    with _DESK_BIAS_LOCK:
        st = _desk_bias_read()
        want = str(st.get("bias") or "").upper()
        if want not in ("LONG", "SHORT", "FLAT"):
            return []
        stamp = str(st.get("updated") or "")
        already = st.get("applied") if isinstance(st.get("applied"), dict) else {}
        already = dict(already)
        applied = []
        applied_changed = False
        for bot_id, spec in CONDOR_HUNTERS.items():
            target = _clip_desk_bias(want, bot_id)
            coins = [str(c).upper() for c in (spec.get("coins") or ())]
            if not coins:
                continue
            status = _read_json_file(_desk_bias_status_path(bot_id)) or {}
            held = set(_status_held_coins(status))
            mode = spec.get("mode")
            clip = "long" if mode == "long" else ("short" if mode == "short" else None)
            current = _hunter_bias_payload(bot_id, coins=coins, clip=clip)
            new_map = {c: current.get(c) or "FLAT" for c in coins}
            # One push per desk command. Do not restamp every 4s — that
            # forces Helliot back to LONG after the operator sets FLAT/SHORT.
            if stamp and already.get(bot_id) == stamp:
                continue
            if want == "FLAT" or not held:
                for c in coins:
                    new_map[c] = target
            else:
                for c in coins:
                    if c in held:
                        new_map[c] = target
                    elif target == "FLAT":
                        new_map[c] = "FLAT"
            if new_map != {c: current.get(c) or "FLAT" for c in coins}:
                _write_hunter_bias_map(bot_id, new_map, source="home-desk")
                applied.append(bot_id)
            if stamp:
                already[bot_id] = stamp
                applied_changed = True
        if applied_changed:
            st = dict(st)
            st["applied"] = already
            try:
                _write_json_file(DESK_BIAS_FILE, st)
            except Exception:
                pass
        return applied


def _desk_bias_snapshot():
    st = _desk_bias_read()
    want = str(st.get("bias") or "").upper()
    if want not in ("LONG", "SHORT", "FLAT"):
        want = None
    rows = []
    for bot_id, spec in CONDOR_HUNTERS.items():
        coins = [str(c).upper() for c in (spec.get("coins") or ())]
        mode = spec.get("mode")
        clip = "long" if mode == "long" else ("short" if mode == "short" else None)
        live = _hunter_bias_payload(bot_id, coins=coins, clip=clip) if coins else {}
        status = _read_json_file(_desk_bias_status_path(bot_id)) or {}
        held = _status_held_coins(status)
        target = _clip_desk_bias(want, bot_id) if want else None
        live_coins = {c: live.get(c) or "FLAT" for c in coins}
        waiting = bool(held) and want in ("LONG", "SHORT")
        fully = (not held) and want and all(live_coins.get(c) == target for c in coins)
        in_clip_ok = bool(held) and all(live_coins.get(c) == target for c in held)
        h = registry_hunter(bot_id) or {}
        rows.append({
            "id": bot_id,
            "name": h.get("name") or bot_id,
            "mode": mode,
            "target": target,
            "live": live_coins,
            "held": held,
            "applied": bool(fully or (in_clip_ok and want == "FLAT") or (in_clip_ok and not waiting)),
            "waiting": waiting and not fully,
        })
    return {
        "bias": want,
        "updated": st.get("updated"),
        "closes": False,
        "hunters": rows,
    }


def _desk_bias_loop():
    while True:
        time.sleep(4)
        try:
            _apply_desk_bias()
        except Exception:
            pass


def _home_board_for_display():
    live = _read_json_file(os.path.join(CONF_DIR, "wire_home.json")) or {"nodes": [], "edges": []}
    draft = _read_json_file(os.path.join(CONF_DIR, "wire_home_draft.json")) or {}
    out = dict(live) if isinstance(live, dict) else {"nodes": [], "edges": []}
    ts = None
    if isinstance(draft, dict) and draft.get("top_scale") is not None:
        ts = draft.get("top_scale")
    elif out.get("top_scale") is not None:
        ts = out.get("top_scale")
    else:
        for n in (out.get("nodes") or []) + (draft.get("nodes") or []):
            sc = ((n.get("data") or {}) if isinstance(n, dict) else {}).get("top_scale")
            if sc is not None:
                ts = sc
                break
    if ts is not None:
        try:
            out["top_scale"] = max(0.45, min(1.35, float(ts)))
        except (TypeError, ValueError):
            pass
    return out


@app.route("/api/flask/writes", methods=["GET", "POST"])
def api_flask_writes():
    """Toggle Flask hunter-file WRITE MODE. Never closes or opens Hyperliquid clips."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        if "enabled" in data:
            on = bool(data.get("enabled"))
        elif "writes" in data:
            on = bool(data.get("writes"))
        elif "on" in data:
            on = bool(data.get("on"))
        else:
            on = not flask_writes_on()
        flask_writes_set(on)
    on = flask_writes_on()
    return jsonify({
        "ok": True,
        "writes": on,
        "flask_writes": on,
        "closes": False,
        "positions_untouched": True,
        "message": (
            "Flask WRITE MODE on — BIAS/SL/ctrl files (never CLOSE/OPEN from this toggle)"
            if on else
            "Flask WRITE MODE off — hunters keep trading, files not written from Flask"
        ),
    })


@app.route("/api/ctl")
def api_ctl():
    """Home CTL snapshot. Does not write hunters or touch positions."""
    flask_on = flask_writes_on()
    spring = _spring_http("/api/health", "GET")
    spring_up = bool(isinstance(spring, dict) and spring.get("service") == "spring")
    spring_writes = bool(spring_up and spring.get("writes"))
    dual = bool(flask_on and spring_writes)
    return jsonify({
        "ok": True,
        "flask_writes": flask_on,
        "closes": False,
        "positions_untouched": True,
        "spring": {
            "up": spring_up,
            "port": 5004,
            "writes": spring_writes if spring_up else False,
            "mode": spring.get("mode") if spring_up else None,
            "reachable_from_flask": spring_up,
        },
        "dual_writers": dual,
        "hunters_independent": True,
        "note": (
            "Both brains WRITE MODE on — last file write wins on BIAS/SL/ctrl. "
            "Turn one off. Hunters keep their Hyperliquid clips either way."
            if dual else
            "GET coexistence is safe. Hunters poll conf files and trade on their own."
        ),
    })


@app.route("/api/ctl/spring/stop", methods=["POST"])
def api_ctl_spring_stop():
    """Ask Spring :5004 to exit. Does not stop Flask, hunters, or close positions."""
    out = _spring_http("/api/spring/stop", "POST", {}, timeout=1.2)
    if isinstance(out, dict) and (out.get("ok") or out.get("stopped")):
        return jsonify({
            "ok": True,
            "stopped": True,
            "closes": False,
            "positions_untouched": True,
            "message": out.get("message") or "Spring stopping — hunters keep running",
            "spring": out,
        })
    return jsonify({
        "ok": False,
        "stopped": False,
        "closes": False,
        "positions_untouched": True,
        "error": (out or {}).get("error") or "Spring :5004 not reachable from Flask container",
        "message": "Use STOP SPRING on Home (browser → 127.0.0.1:5004) or springctl. Hunters untouched.",
        "spring": out,
    }), 502


@app.route("/api/home")
def api_home():
    """All live bot positions for the portfolio home screen."""
    mids = fetch_all_mids() or {}
    specs = get_home_brief_specs()
    bots_out = []
    _risk_snap = _risk_home_payload()
    _risk_by = {r["id"]: r for r in (_risk_snap.get("hunters") or [])}
    positions_out = []
    total_eq = 0.0
    total_upnl = 0.0
    n_eq = 0
    n_upnl = 0

    for spec in specs:
        status = _read_json_file(spec.get("status_file") or "")
        if not isinstance(status, dict):
            status = {}
        addr = (
            status.get("wallet")
            or status.get("subaccount")
            or wallet_for(spec["id"])
        )
        live = fetch_hl_account(addr) if addr else None
        cst = get_container_state(spec["id"])
        running = cst["running"]
        paused = cst["paused"]
        equity = None
        free = None
        if live and live.get("equity") is not None:
            equity = float(live["equity"])
        elif status.get("equity_usd") is not None:
            try:
                equity = float(status["equity_usd"])
            except (TypeError, ValueError):
                equity = None
        elif status.get("equity") is not None:
            try:
                equity = float(status["equity"])
            except (TypeError, ValueError):
                equity = None
        if live and live.get("free") is not None:
            free = float(live["free"])
        elif status.get("free_usd") is not None:
            try:
                free = float(status["free_usd"])
            except (TypeError, ValueError):
                free = None
        elif status.get("free") is not None:
            try:
                free = float(status["free"])
            except (TypeError, ValueError):
                free = None
        spot_d = fetch_hl_spot_detail(addr) if addr else None
        spot_free = None
        if spot_d and spot_d.get("free") is not None:
            spot_free = float(spot_d["free"])
            if free is None:
                free = 0.0
            free = float(free) + spot_free
        if paused:
            equity = 0.0
            free = 0.0
        elif equity is not None:
            total_eq += equity
            n_eq += 1

        extra = {}
        if spec["kind"] == "copy":
            for m in status.get("mirrors") or []:
                coin = m.get("coin")
                if coin:
                    extra[str(coin).upper()] = m
        else:
            for coin, pos in (status.get("positions") or {}).items():
                if isinstance(pos, dict) and (pos.get("position_side") or pos.get("side")):
                    extra[str(coin).upper()] = pos

        live_pos = (live or {}).get("positions") or []
        bot_upnl = 0.0
        bot_npos = 0
        used_coins = set()
        meta = BOTS.get(spec["id"]) or {}
        if not meta.get("icon"):
            meta = {
                **meta,
                "icon": spec.get("icon") or meta.get("icon") or "",
                "color": spec.get("color") or meta.get("color") or "#7ecbff",
            }

        def _append(coin, side, size, entry, lev, margin, extra_row, source):
            nonlocal bot_upnl, bot_npos, total_upnl, n_upnl
            coin_u = str(coin or "").upper()
            if not coin_u or not side or abs(float(size or 0)) < 1e-12:
                return
            used_coins.add(coin_u)
            bot_npos += 1
            mark = mids.get(coin_u)
            entry_f = float(entry or 0)
            size_f = abs(float(size or 0))
            lev_i = int(lev or extra_row.get("leverage") or 0) or 1
            usd = round(size_f * float(mark or entry_f or 0), 2)
            roe = _roe_from_live(side, entry_f, mark, lev_i)
            pnl = extra_row.get("unrealized_pnl")
            if pnl is None and extra_row.get("pnl_usd") is not None:
                pnl = extra_row.get("pnl_usd")
            if pnl is None and roe is not None and margin:
                try:
                    pnl = round(float(roe) / 100.0 * float(margin), 2)
                except (TypeError, ValueError):
                    pnl = None
            if pnl is None and roe is not None and usd and lev_i:
                pnl = round(float(roe) / 100.0 * (usd / lev_i), 2)
            if pnl is not None:
                bot_upnl += float(pnl)
                total_upnl += float(pnl)
                n_upnl += 1
            trader = extra_row.get("trader")
            trail_x = extra_row.get("trail_extreme")
            if trail_x is None:
                trail_x = extra_row.get("trail_px")
            positions_out.append({
                "bot_id": spec["id"],
                "bot": spec["name"],
                "href": spec["href"],
                "coin": coin_u,
                "side": str(side).upper(),
                "size": size_f,
                "usd_size": usd,
                "entry_px": entry_f,
                "mark_px": mark,
                "leverage": lev_i,
                "margin": float(margin) if margin else None,
                "pnl_usd": round(float(pnl), 2) if pnl is not None else None,
                "roe_pct": roe,
                "trader": trader,
                "source": source,
                "trail_active": bool(extra_row.get("trail_active")),
                "trail_extreme": trail_x,
                "entry_time": extra_row.get("entry_time"),
            })

        if not paused:
            for p in live_pos:
                coin = p.get("coin")
                extra_row = extra.get(str(coin or "").upper()) or {}
                _append(
                    coin,
                    p.get("side"),
                    p.get("size"),
                    p.get("entry_px") or extra_row.get("entry_price") or extra_row.get("entry_px"),
                    p.get("leverage") or extra_row.get("leverage"),
                    p.get("margin_used") or extra_row.get("margin"),
                    {**extra_row, "unrealized_pnl": p.get("unrealized_pnl")},
                    "live",
                )
            for coin, row in extra.items():
                if coin in used_coins:
                    continue
                if live is not None:
                    continue
                side = row.get("side") or row.get("position_side")
                size = row.get("size") or row.get("amount")
                _append(
                    coin,
                    side,
                    size,
                    row.get("entry_px") or row.get("entry_price"),
                    row.get("leverage"),
                    row.get("margin") or row.get("margin_used"),
                    row,
                    "status",
                )

        hh = home_bot_logs_and_health(spec["id"], running, status)
        sl_yml = 20.0
        try:
            if status.get("sl_roe_pct") is not None:
                sl_yml = float(status.get("sl_roe_pct"))
        except (TypeError, ValueError):
            sl_yml = 20.0
        # Copy has no built-in ROE stop until a Home button is clicked.
        sl_fallback = None if spec["id"] == "hummingbot-hl" else sl_yml
        sl_val = _sl_roe_for(spec["id"], sl_fallback)
        if spec.get("kind") == "copy" or spec["id"] == "hummingbot-hl":
            hunter_coins = []
        else:
            rh = registry_hunter(spec["id"]) or {}
            hunter_coins = list(rh.get("coins") or spec.get("coins") or ["BTC", "ETH", "LTC", "TAO"])
        markets = []
        idle_markets = []
        held_coins = []
        if spec.get("kind") != "copy" and spec["id"] != "hummingbot-hl":
            held_coins = [str(c).upper() for c in extra.keys()]
        if spec["id"] == "hummingbot-hl":
            for p in status.get("markets") or []:
                if p:
                    markets.append(str(p).upper())
            for p in status.get("idle_markets") or []:
                if p:
                    idle_markets.append(str(p).upper())
            for m in status.get("mirrors") or []:
                c = str(m.get("coin") or "").upper()
                if c:
                    held_coins.append(c)
            if not idle_markets and markets:
                held_pairs = {f"{c}-USD" if not str(c).endswith("-USD") else c for c in held_coins}
                idle_markets = [p for p in markets if p not in held_pairs]
        risk_row = _risk_by.get(spec["id"])
        if risk_row:
            try:
                risk_row["withdrawable_usd"] = None if free is None else round(float(free), 4)
            except (TypeError, ValueError):
                risk_row["withdrawable_usd"] = None
            risk_row["n_pos"] = int(bot_npos or 0)
        if risk_row and free is not None:
            try:
                free = max(0.0, float(free) - float(risk_row.get("parked_usd") or 0))
            except (TypeError, ValueError):
                pass
        bots_out.append({
            "id": spec["id"],
            "name": spec["name"],
            "href": spec["href"],
            "icon": meta.get("icon") or "",
            "color": meta.get("color") or "#7ecbff",
            "running": running,
            "deactivated": paused,
            "address": addr,
            "equity_usd": equity,
            "free_usd": free,
            "risk": risk_row,
            "n_pos": bot_npos,
            "upnl_usd": round(bot_upnl, 2) if bot_npos else 0.0,
            "sl_roe_pct": sl_val,
            "ok": hh["ok"],
            "health": hh["health"],
            "health_reason": hh["health_reason"],
            "logs": hh["logs"],
            "bias": _home_bias_for(spec["id"]),
            "bias_mode": _hunter_bias_mode(spec["id"]) if hunter_coins else "",
            "has_bias": bool(hunter_coins),
            "coins": hunter_coins,
            "markets": markets,
            "idle_markets": idle_markets,
            "held_coins": held_coins,
            "next": _home_next_for(spec["id"], status, _home_bias_for(spec["id"]), sl_val),
            "controller": _ctrl_live_values(spec["id"], status),
        })

    bot_order = {s["id"]: i for i, s in enumerate(specs)}
    positions_out.sort(
        key=lambda p: (
            bot_order.get(p.get("bot_id"), 99),
            -abs(float(p.get("pnl_usd") or 0)),
            p.get("coin") or "",
        )
    )

    mids_out = {}
    for coin in ("BTC", "ETH", "LTC", "TAO"):
        if mids.get(coin) is not None:
            mids_out[coin] = mids.get(coin)
    for p in positions_out:
        coin = p.get("coin")
        if coin and mids.get(coin) is not None:
            mids_out[coin] = mids.get(coin)

    _risk_fill_available(_risk_snap)

    return jsonify({
        "bots": bots_out,
        "positions": positions_out,
        "mids": mids_out,
        "totals": {
            "equity_usd": round(total_eq, 2) if n_eq else 0.0,
            "upnl_usd": round(total_upnl, 2) if n_upnl else 0.0,
            "n_pos": len(positions_out),
            "n_bots": len(bots_out),
            "n_live": sum(1 for b in bots_out if b.get("running")),
            "n_ok": sum(1 for b in bots_out if b.get("ok")),
        },
        "condor": _safe_payload(_condor_payload),
        "pump": _safe_payload(_pump_payload),
        "leaders": _copy_leader_cards(),
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "rate": _hl_ip_snapshot(),
        "health": _desk_health(),
        "home_board": _home_board_for_display(),
        "health_board": _read_json_file(os.path.join(CONF_DIR, "wire_health.json")) or {"nodes": [], "edges": []},
        "desk_bias": _desk_bias_snapshot(),
        "risk": _risk_snap,
        "flask_writes": flask_writes_on(),
        "closes": False,
        "positions_untouched": True,
    })


HOME_RESTART_IDS = _LazySet(get_home_restart_ids)


@app.route("/api/home/restart", methods=["POST"])
def api_home_restart():
    """Restart one trading container from the home cards. Positions stay on HL."""
    data = request.get_json(silent=True) or {}
    bot_id = str(data.get("id") or "").strip()
    if bot_id not in HOME_RESTART_IDS:
        return jsonify({"ok": False, "error": "unknown or not restartable"}), 400
    name = (BOTS.get(bot_id) or {}).get("name") or bot_id
    try:
        _restart_container(bot_id)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({
        "ok": True,
        "message": f"Restarted {name} ({bot_id})",
        "id": bot_id,
    })


def _home_trade_hunter(bot_id):
    bot_id = str(bot_id or "").strip()
    if not bot_id or bot_id == "hummingbot-hl":
        return None, "Copy trades stay on /copy — not this BIAS rail"
    h = registry_hunter(bot_id) or {}
    spec = CONDOR_HUNTERS.get(bot_id) or {}
    coins = [str(c).upper() for c in (h.get("coins") or spec.get("coins") or ())]
    if not coins:
        return None, "this hunter has no BIAS coins"
    return {
        "id": bot_id,
        "name": (h.get("name") or (BOTS.get(bot_id) or {}).get("name") or bot_id),
        "coins": coins,
        "mode": _hunter_bias_mode(bot_id),
    }, None


@app.route("/api/home/close", methods=["POST"])
def api_home_close():
    """Queue a hunter close for one coin. The bot flattens that clip; other coins stay."""
    data = request.get_json(silent=True) or {}
    hunter, err = _home_trade_hunter(data.get("id") or data.get("bot_id"))
    if err:
        return jsonify({"ok": False, "error": err, "queued": False}), 400
    coin = str(data.get("coin") or "").upper().strip()
    if not coin or coin not in hunter["coins"]:
        return jsonify({"ok": False, "error": "coin required on this hunter", "queued": False}), 400
    cid = _sql_command(hunter["id"], "close", {"coin": coin, "source": "home-matrix"})
    if not cid:
        return jsonify({"ok": False, "error": "sqlite command queue failed", "queued": False}), 500
    return jsonify({
        "ok": True,
        "queued": True,
        "id": hunter["id"],
        "coin": coin,
        "command_id": cid,
        "message": "Close queued for %s on %s — hunter will flatten that clip" % (coin, hunter["name"]),
    })


@app.route("/api/home/open", methods=["POST"])
def api_home_open():
    """Queue a 100% leftover open on the coin's current BIAS. Does not run unless the hunter picks it up."""
    data = request.get_json(silent=True) or {}
    hunter, err = _home_trade_hunter(data.get("id") or data.get("bot_id"))
    if err:
        return jsonify({"ok": False, "error": err, "queued": False}), 400
    coin = str(data.get("coin") or "").upper().strip()
    if not coin or coin not in hunter["coins"]:
        return jsonify({"ok": False, "error": "coin required on this hunter", "queued": False}), 400
    try:
        raw_pct = float(data.get("pct") if data.get("pct") is not None else 1.0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "pct must be 100", "queued": False}), 400
    if raw_pct > 1:
        raw_pct = raw_pct / 100.0
    if abs(raw_pct - 1.0) > 1e-6:
        return jsonify({"ok": False, "error": "this control opens 100% leftover only", "queued": False}), 400
    side = _open_side(data, hunter["id"], coin, coins=hunter["coins"])
    if side not in ("LONG", "SHORT"):
        return jsonify({"ok": False, "error": "set %s BIAS to LONG or SHORT before 100%%" % coin, "queued": False}), 400
    cid = _sql_command(hunter["id"], "open", {
        "coin": coin, "side": side, "pct": 1.0,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "home-matrix",
    })
    if not cid:
        return jsonify({"ok": False, "error": "sqlite command queue failed", "queued": False}), 500
    return jsonify({
        "ok": True,
        "queued": True,
        "id": hunter["id"],
        "coin": coin,
        "side": side,
        "pct": 1.0,
        "command_id": cid,
        "message": "100%% leftover %s %s queued on %s" % (side, coin, hunter["name"]),
    })


def _risk_arm_send(bot_id=None):
    with _risk_file_lock():
        return _risk_arm_send_locked(bot_id)


def _risk_arm_send_locked(bot_id=None):
    data = _read_json_file(RISK_SKIM_FILE) or {}
    hunters = data.get("hunters") if isinstance(data.get("hunters"), dict) else {}
    armed = []
    for bid, row in hunters.items():
        if bot_id and bid != bot_id:
            continue
        if not isinstance(row, dict):
            continue
        try:
            parked = float(row.get("parked_usd") or 0)
        except (TypeError, ValueError):
            parked = 0.0
        if parked < 1.0:
            continue
        row["force_send"] = True
        row["last_send_try"] = 0
        hunters[bid] = row
        armed.append(bid)
    if armed:
        data["hunters"] = hunters
        data["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        _write_json_file(RISK_SKIM_FILE, data)
    return armed


def _risk_flush_copy():
    """Ask Copy to master-sign parked RISK sends. Does not close positions."""
    try:
        r = subprocess.run(
            [
                "docker", "exec",
                "-e", "HL_PW=" + str(CONFIG_PASSWORD),
                "-e", "CONFIG_PASSWORD=" + str(CONFIG_PASSWORD),
                "hummingbot-hl",
                "python3", "/home/hummingbot/scripts/risk_park.py", "flush",
            ],
            capture_output=True, text=True, timeout=45,
        )
    except Exception as e:
        return False, str(e)[:240]
    out = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()
    last = out.splitlines()[-1] if out else ""
    if r.returncode != 0:
        return False, (last or out or "flush failed")[:240]
    return True, (last or "ok")[:240]


@app.route("/api/home/risk_send", methods=["POST"])
def api_home_risk_send():
    """Send parked hunter cash to the RISK HL sub. Does not close positions.

    Body optional: {"id": "hummingbot-dman-maker"} for one hunter, else all due.
    Caps to Hyperliquid withdrawable (open margin stays).
    """
    body = request.get_json(silent=True) or {}
    bot_id = str(body.get("id") or "").strip() or None
    if bot_id and bot_id not in (BOTS or {}):
        return jsonify({"ok": False, "error": "unknown hunter"}), 400
    armed = _risk_arm_send(bot_id)
    if not armed:
        snap = _risk_fill_available(_risk_home_payload())
        return jsonify({
            "ok": False,
            "error": "nothing parked ≥ $1 to send",
            "risk": snap,
            "closes": False,
        }), 400
    ok, msg = _risk_flush_copy()
    _RISK_BAL_CACHE["ts"] = 0
    _RISK_BAL_CACHE["by"] = {}
    snap = _risk_fill_available(_risk_home_payload())
    return jsonify({
        "ok": ok,
        "message": msg if ok else ("queued · " + msg),
        "armed": armed,
        "risk": snap,
        "closes": False,
    }), (200 if ok else 502)


@app.route("/api/home/desk_bias", methods=["POST"])
def api_home_desk_bias():
    """Set desk-wide next BIAS. Does not close or open any Hyperliquid position.

    Body: {"bias": "LONG"|"FLAT"|"SHORT"}
    Writes hunter bias files/sqlite only. Hunters already in a clip keep holding
    until their own SL/TP/trail. After they go flat they follow this bias.
    Copy is unchanged. Long-only hunters clip SHORT→FLAT. Shorty clips LONG→FLAT.
    """
    data = request.get_json(silent=True) or {}
    bias = str(data.get("bias") or "").upper().strip()
    if bias not in ("LONG", "SHORT", "FLAT"):
        return jsonify({"ok": False, "error": "bias must be LONG, FLAT, or SHORT"}), 400
    payload = {
        "bias": bias,
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "home",
        "closes": False,
    }
    try:
        _write_json_file(DESK_BIAS_FILE, payload)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    applied = _apply_desk_bias()
    snap = _desk_bias_snapshot()
    n_hold = sum(1 for h in (snap.get("hunters") or []) if h.get("held"))
    msg = (
        "Desk BIAS {bias}. Does not close open clips. "
        "{n} hunter(s) still in a trade — they keep holding; after autoclose they follow {bias}."
    ).format(bias=bias, n=n_hold)
    if n_hold == 0:
        msg = "Desk BIAS {bias}. No open clips on BIAS hunters — files updated. Does not place orders.".format(bias=bias)
    return jsonify({
        "ok": True,
        "message": msg,
        "closes": False,
        "applied": applied,
        "desk_bias": snap,
    })


def _parse_koord_text(raw):
    """Grok Koordinator often writes LONG / please post short. Map to /long /short /flat."""
    t = str(raw or "").strip()
    if not t:
        return None
    low = t.lower()
    m = re.search(r"/(long|short|flat)\b", low)
    if m:
        return {"long": "LONG", "short": "SHORT", "flat": "FLAT"}[m.group(1)]
    m = re.search(r"please\s+post\s+(long|short|flat|up|down|hold)\b", low)
    if m:
        w = m.group(1)
        return {"long": "LONG", "up": "LONG", "short": "SHORT", "down": "SHORT", "flat": "FLAT", "hold": "FLAT"}[w]
    m = re.fullmatch(r"[\s]*(long|short|flat|up|down|hold)[\s.!;]*", low)
    if m:
        w = m.group(1)
        return {"long": "LONG", "up": "LONG", "short": "SHORT", "down": "SHORT", "flat": "FLAT", "hold": "FLAT"}[w]
    return None


def _notify_condor1(cmd):
    """Post /long /short /flat as BIAS BOT so it shows in Telegram. Never logs the token."""
    setup = _read_json_file(os.path.join(CONF_DIR, "condor_setup.json")) or {}
    bots = setup.get("bots") if isinstance(setup.get("bots"), dict) else {}
    row = bots.get("telegram-bot-t84jlc") if isinstance(bots.get("telegram-bot-t84jlc"), dict) else {}
    token = str(row.get("token") or "").strip()
    chats = row.get("chat_ids") or []
    if isinstance(chats, str):
        chats = [c.strip() for c in chats.split(",") if c.strip()]
    chat = str(chats[0] if chats else "").strip()
    if not token or not chat:
        return False
    try:
        body = json.dumps({"chat_id": chat, "text": cmd, "disable_web_page_preview": True}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.telegram.org/bot" + token + "/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            out = json.loads(resp.read().decode("utf-8") or "{}")
        return bool(out.get("ok"))
    except Exception:
        return False


@app.route("/api/koordinator/post", methods=["POST"])
def api_koordinator_post():
    """Ingest Grok Koordinator output. Telegram bots cannot see other bots' messages.

    Body: {"text": "/long"} or {"text": "SHORT"} or {"text": "please post long"}
    Maps to /long /short /flat and posts that slash command on BIAS BOT condor1.
    Does not write Desk BIAS and does not change any hummingbot.
    """
    data = request.get_json(silent=True) or {}
    raw = data.get("text") or data.get("telegram_text") or data.get("bias") or data.get("cmd") or ""
    if isinstance(data.get("message"), dict):
        raw = data["message"].get("text") or raw
    bias = _parse_koord_text(raw)
    if bias not in ("LONG", "SHORT", "FLAT"):
        return jsonify({
            "ok": False,
            "error": "need /long /short /flat (or LONG/SHORT/FLAT / please post short)",
            "got": str(raw)[:80],
        }), 400
    cmd = {"LONG": "/long", "SHORT": "/short", "FLAT": "/flat"}[bias]
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    payload = {
        "bias": bias,
        "updated": ts,
        "source": "koordinator",
        "closes": False,
        "writes_hunters": False,
        "raw": str(raw)[:120],
    }
    try:
        _write_json_file(GROK_BIAS_FILE, payload)
        _write_json_file(os.path.join(CONF_DIR, "koordinator_cmd.json"), {
            "id": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "bias": bias,
            "cmd": cmd,
            "source": "koordinator",
            "closes": False,
            "writes_hunters": False,
            "raw": str(raw)[:120],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    posted = _notify_condor1(cmd)
    return jsonify({
        "ok": True,
        "cmd": cmd,
        "bias": bias,
        "closes": False,
        "writes_hunters": False,
        "telegram": posted,
        "applied": [],
        "grok_bias": payload,
        "message": "GROK " + cmd + " → BIAS BOT"
        + (" posted" if posted else " (telegram notify skipped)")
        + " · hunters unchanged",
    })


@app.route("/api/home/sl", methods=["POST"])
def api_home_sl():
    """Set per-bot ROE stop-loss. Traders close when ROE <= −this %."""
    data = request.get_json(silent=True) or {}
    bot_id = str(data.get("id") or "").strip()
    if bot_id not in HOME_RESTART_IDS:
        return jsonify({"ok": False, "error": "unknown bot"}), 400
    try:
        sl = float(data.get("sl_roe_pct"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "sl_roe_pct required"}), 400
    if sl < 1.0 or sl > 90.0:
        return jsonify({"ok": False, "error": "sl_roe_pct must be 1–90"}), 400
    sl = round(sl, 2)
    payload = _read_json_file(BOT_SL_FILE)
    if not isinstance(payload, dict):
        payload = {}
    row = payload.get(bot_id) if isinstance(payload.get(bot_id), dict) else {}
    row["sl_roe_pct"] = sl
    row["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    payload[bot_id] = row
    tmp = BOT_SL_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, BOT_SL_FILE)
    _sql_kv_set(bot_id, "sl_roe_pct", sl, "home")
    name = (BOTS.get(bot_id) or {}).get("name") or bot_id
    return jsonify({
        "ok": True,
        "id": bot_id,
        "sl_roe_pct": sl,
        "message": f"{name} stop-loss set to −{sl:g}% ROE",
    })


@app.route("/api/home/copy_drop_market", methods=["POST"])
def api_home_copy_drop_market():
    """Drop Copy order-book subscriptions. Does not close Hyperliquid positions.

    Body: {"coin": "ETH"} | {"coins": ["ETH","PENDLE"]} | {"all_idle": true}
    """
    data = request.get_json(silent=True) or {}
    payload = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "source": "home",
    }
    coins = []
    raw_coins = data.get("coins") or data.get("pairs") or []
    if isinstance(raw_coins, str):
        raw_coins = [raw_coins]
    for c in list(raw_coins):
        s = str(c or "").strip().upper()
        if s:
            coins.append(s)
    one = str(data.get("coin") or data.get("pair") or "").strip().upper()
    if one:
        coins.append(one)
    coins = list(dict.fromkeys(coins))
    for s in coins:
        if not re.match(r"^[A-Za-z0-9:]{1,24}(-USD)?$", s):
            return jsonify({"ok": False, "error": f"invalid coin {s}"}), 400
    all_idle = bool(data.get("all_idle") or data.get("all"))
    if not coins and not all_idle:
        return jsonify({"ok": False, "error": "coin or all_idle required"}), 400
    if all_idle:
        payload["all_idle"] = True
        msg = "Copy: clean idle books"
    else:
        payload["coins"] = coins
        payload["coin"] = coins[0]
        msg = "Copy: drop book " + ", ".join(coins)
    cid = _sql_command("hummingbot-hl", "drop_markets", payload)
    if not cid:
        return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
    payload["id"] = cid
    return jsonify({"ok": True, "message": msg, "payload": payload})


def _copy_audit_summary():
    data = {}
    if _MATRIX_OK:
        try:
            data = matrix_store.leader_audit_latest(
                (load_hl_config().get("trader_addresses") or [])
            ) or {}
        except Exception:
            data = {}
    if not (isinstance(data.get("traders"), dict) and data.get("traders")):
        path = os.path.join(LOGS_DIR, "trader_audits", "latest.json")
        data = _read_json_file(path) or {}
    traders = data.get("traders") if isinstance(data.get("traders"), dict) else {}
    live_addrs = [str(a).strip().lower() for a in (load_hl_config().get("trader_addresses") or [])]
    rows = []
    for i, addr in enumerate(live_addrs, 1):
        t = traders.get(str(i)) if isinstance(traders.get(str(i)), dict) else {}
        old = str(t.get("address") or "").strip().lower()
        if old and old != addr:
            t = {"trader": i, "address": addr, "stale": True, "n_positions": None}
        rows.append({
            "trader": t.get("trader") or i,
            "audited_at": t.get("audited_at_local") or t.get("audited_at"),
            "equity": t.get("equity"),
            "free_margin": t.get("free_margin"),
            "n_positions": t.get("n_positions"),
            "sleep_flag": t.get("sleep_flag"),
            "capacity_flag": t.get("capacity_flag") or ("WAIT" if t.get("stale") else None),
            "can_open": t.get("can_open"),
        })
    if not rows:
        for k in sorted(traders, key=lambda x: int(x) if str(x).isdigit() else 99):
            t = traders[k] or {}
            rows.append({
                "trader": t.get("trader") or k,
                "audited_at": t.get("audited_at_local") or t.get("audited_at"),
                "equity": t.get("equity"),
                "free_margin": t.get("free_margin"),
                "n_positions": t.get("n_positions"),
                "sleep_flag": t.get("sleep_flag"),
                "capacity_flag": t.get("capacity_flag"),
                "can_open": t.get("can_open"),
            })
    times = [str(t.get("audited_at") or "") for t in rows if t.get("audited_at")]
    updated = max(times) if times else data.get("updated_at")
    return {
        "updated_at": updated,
        "worker": get_container_running("hummingbot-audit"),
        "traders": rows,
        "source": data.get("source") or "sqlite",
    }


@app.route("/api/hl_live")
def api_hl_live():
    """
    Primary dashboard payload for HL Copy Trader:
    active positions, follow failures, errors, heartbeat, leaders.
    """
    running = get_container_running("hummingbot-hl")
    logs = get_logs("hummingbot-hl", 200)
    mids = fetch_all_mids()
    bot_status = load_hl_bot_status()
    # Skip the 10MB trade-log scan when we already have status mirrors.
    log_mirrors = []
    if not (bot_status and isinstance(bot_status.get("mirrors"), list)):
        log_mirrors = get_open_mirrors()
    mirrors = merge_active_mirrors(log_mirrors, bot_status)
    cfg = load_hl_config()
    wallet = (
        (bot_status or {}).get("wallet")
        or wallet_for("hummingbot-hl", COPY_WALLET)
    )
    live_acct = fetch_hl_account(wallet)
    if live_acct and live_acct.get("positions") is not None:
        mirrors = overlay_live_mirrors(mirrors, live_acct.get("positions") or [])

    # Live PnL + real USD notional on open mirrors
    for m in mirrors:
        cur = mids.get(m["coin"])
        m["current_px"] = cur
        size = float(m.get("size") or 0)
        # Real position size in USD = coin size × live mid (fallback: entry)
        px_for_usd = cur if cur and cur > 0 else m.get("entry_px")
        if size > 0 and px_for_usd and float(px_for_usd) > 0:
            m["usd_size"] = round(size * float(px_for_usd), 2)
        else:
            # Fall back to logged notional if we have it
            n = float(m.get("notional") or 0)
            m["usd_size"] = round(n, 2) if n > 0 else None
        if cur and m.get("entry_px") and m["entry_px"] > 0:
            lev = m.get("leverage") or 1
            if m["side"] == "LONG":
                m["pnl_pct"] = round((cur - m["entry_px"]) / m["entry_px"] * 100 * lev, 2)
            else:
                m["pnl_pct"] = round((m["entry_px"] - cur) / m["entry_px"] * 100 * lev, 2)
            if m.get("margin") and m["margin"] > 0:
                m["pnl_usd"] = round(m["pnl_pct"] / 100 * m["margin"], 2)
            elif m.get("usd_size") and m["usd_size"] > 0 and lev:
                m["pnl_usd"] = round(m["pnl_pct"] / 100 * (m["usd_size"] / lev), 2)
            elif m.get("notional") and m["notional"] > 0 and lev:
                # approximate margin = notional/lev
                m["pnl_usd"] = round(m["pnl_pct"] / 100 * (m["notional"] / lev), 2)
            else:
                m["pnl_usd"] = m.get("pnl_usd")
        else:
            m["pnl_pct"] = m.get("pnl_pct")
            # keep live HL unrealized PnL when mids are missing

    failures = parse_follow_failures(50)
    leader_opens = parse_leader_open_events(60)
    open_times = last_leader_open_times()  # session log only — new opens the bot saw
    errors = parse_strategy_errors(40)
    hb = parse_heartbeat_snapshot(logs)
    # Overlay bot status equity/free (use even if stale; live HL account wins)
    if bot_status:
        if bot_status.get("equity") is not None:
            hb["equity"] = float(bot_status["equity"])
            hb["balance"] = float(bot_status["equity"])
        if bot_status.get("free") is not None:
            hb["free"] = float(bot_status["free"])
        if bot_status.get("n_mirrors") is not None:
            hb["mirrors_crypto"] = int(bot_status["n_mirrors"])
        if bot_status.get("connector_ready") is not None:
            hb["connector"] = "ready" if bot_status["connector_ready"] else "NOT READY"
        elif not bot_status.get("_fresh"):
            hb["connector"] = hb.get("connector") or "STALE"
    if live_acct:
        if live_acct.get("equity") is not None:
            hb["equity"] = float(live_acct["equity"])
            hb["balance"] = float(live_acct["equity"])
        if live_acct.get("free") is not None:
            hb["free"] = float(live_acct["free"])
        hb["mirrors_crypto"] = len(mirrors)
    if not hb.get("connector"):
        hb["connector"] = "NOT READY" if running else "DOWN"
    # Use a wider log window so network transitions are not missed between polls
    network = parse_network_status(get_logs("hummingbot-hl", 600))

    # Leader snapshot + open time when we logged TRADER #N OPENED this session
    last_act = last_trader_activity()
    leaders = []
    for i, addr in enumerate(cfg.get("trader_addresses") or []):
        positions = fetch_hl_positions(addr)
        num = i + 1
        enriched = []
        for p in (positions or []):
            ot = open_times.get((num, p["coin"], p["side"]))
            enriched.append({**p, "open_time": ot})
        err = positions is None
        leaders.append({
            "num": num,
            "address": addr,
            "short": addr[:10] + "…" + addr[-6:],
            "positions": enriched,
            "error": err,
            "alive": not err,
            "last_seen": last_act.get(num),
            "n_pos": len(enriched),
        })

    n_configured = max(1, len(cfg.get("trader_addresses") or []) or 3)
    unfollowed_early = set(effective_unfollowed_traders(cfg))
    n_traders = max(1, n_configured - sum(1 for i in range(1, n_configured + 1) if i in unfollowed_early))

    # Fill missing trader # on active mirrors from live leader books.
    # Same rule as the bot orphan audit: first configured leader who holds
    # this coin+side wins ownership attribution.
    # Exception: full all-in (BTC/ETH) is owned by ALL traders (combined pots).
    for m in mirrors:
        src = (m.get("source") or "").lower()
        if (
            src.startswith("full_")
            or m.get("full_btc")
            or m.get("full_eth")
            or m.get("full_allin")
            or m.get("all_traders")
        ):
            m["trader"] = None
            m["traders"] = list(range(1, n_traders + 1))
            m["source"] = src if src.startswith("full_") else f"full_{(m.get('coin') or 'all').lower()}"
            m["all_traders"] = True
            continue
        if m.get("trader") is not None and m.get("trader") != "":
            continue
        matches = []
        for lead in leaders:
            for p in lead.get("positions") or []:
                if p.get("coin") == m.get("coin") and p.get("side") == m.get("side"):
                    matches.append(lead["num"])
                    break
        if matches:
            m["trader"] = matches[0]
            if len(matches) > 1:
                m["traders"] = matches  # all leaders still holding same side

    # Missed follow: leader open that we don't mirror.
    # Only dump the full leader books when the copy bot is actually live;
    # otherwise 400+ leader positions flood the UI as fake misses.
    mirror_keys = {(m["coin"], m["side"]) for m in mirrors}
    unfollowed = []
    bot_live = bool(
        bot_status
        and bot_status.get("_fresh")
        and bot_status.get("connector_ready")
    )
    if bot_live:
        for lead in leaders:
            for p in lead.get("positions") or []:
                key = (p["coin"], p["side"])
                if key not in mirror_keys and ":" not in str(p.get("coin") or ""):
                    unfollowed.append({
                        "trader": lead["num"],
                        "coin": p["coin"],
                        "side": p["side"],
                        "size": p["size"],
                        "entry_px": p["entry_px"],
                        "upnl": p.get("unrealized_pnl"),
                        "open_time": p.get("open_time"),
                    })
    else:
        for e in failures:
            if e.get("kind") in ("MISS", "DEFER", "FAILED", "SKIP"):
                unfollowed.append({
                    "trader": e.get("trader"),
                    "coin": e.get("coin"),
                    "side": e.get("side"),
                    "size": e.get("size"),
                    "entry_px": e.get("entry_px"),
                    "upnl": None,
                    "open_time": e.get("time"),
                })

    # Next open sizing — equal trader pots from equity, then 1/N positions per trader
    free = hb.get("free")
    equity = hb.get("equity")
    if free is None:
        free = hb.get("balance")
    if equity is None:
        equity = free
    max_pos = int(cfg.get("max_positions_per_trader") or cfg.get("min_open_positions") or 3)
    alloc_pct = float(cfg.get("allocation_pct") or 1.0)
    comm_pct = float(cfg.get("commodity_allocation_pct") or 0.0)
    max_lev = int(cfg.get("max_leverage") or 20)
    min_lev = int(cfg.get("min_leverage") or 1)
    next_pos = None
    funds = None
    free_f = None
    trader_pot = None
    if equity is not None or free is not None:
        funds = float(equity if equity is not None else free) * (1.0 - comm_pct)
        free_f = float(free if free is not None else funds)
        trader_pot = funds / n_traders
        slot = trader_pot / max(1, max_pos)
        margin = slot * alloc_pct
        if free_f > 0 and margin > free_f:
            margin = free_f
        next_pos = {
            "free": round(free_f, 2),
            "equity": round(float(equity if equity is not None else free_f), 2),
            "n_traders": n_traders,
            "n_traders_configured": n_configured,
            "trader_pot": round(trader_pot, 2),
            "max_positions_per_trader": max_pos,
            "slot_margin": round(slot, 2),
            "margin": round(margin, 2),  # USD margin for one position of one trader
            "notional_at_max_lev": round(margin * max_lev, 2),
            "notional_at_min_lev": round(margin * min_lev, 2),
            "max_leverage": max_lev,
            "min_leverage": min_lev,
            "allocation_pct": alloc_pct,
            "active_mirrors": len(mirrors),
            "model": "equal_trader_pots",
        }

    # Full all-in emergency positions consume ALL trader pots equally (not just T#1)
    full_btc_mirrors = [
        m for m in mirrors
        if (m.get("source") or "").lower().startswith("full_")
        or m.get("all_traders")
        or m.get("full_btc")
        or m.get("full_eth")
        or m.get("full_allin")
    ]
    full_btc_mode = len(full_btc_mirrors) > 0
    full_btc_margin = 0.0
    full_btc_upnl = 0.0
    full_btc_notional = 0.0
    full_btc_coins = []
    full_btc_has_upnl = False
    for m in full_btc_mirrors:
        full_btc_coins.append(m.get("coin"))
        lev = float(m.get("leverage") or 0) or float(max_lev or 20)
        mar = float(m.get("margin") or 0)
        if mar <= 0 and m.get("usd_size"):
            mar = float(m["usd_size"]) / lev
        elif mar <= 0 and m.get("notional"):
            mar = float(m["notional"]) / lev
        full_btc_margin += mar
        if m.get("usd_size") is not None:
            full_btc_notional += float(m["usd_size"])
        elif m.get("notional"):
            full_btc_notional += float(m["notional"])
        if m.get("pnl_usd") is not None:
            full_btc_upnl += float(m["pnl_usd"])
            full_btc_has_upnl = True
    share_m = (full_btc_margin / n_traders) if full_btc_mode else 0.0
    share_u = (full_btc_upnl / n_traders) if (full_btc_mode and full_btc_has_upnl) else 0.0
    share_n = (full_btc_notional / n_traders) if full_btc_mode else 0.0

    # Per-trader pot / remaining / open uPnL
    unfollowed_nums = set(effective_unfollowed_traders(cfg))
    follow_slots = effective_trader_follow_slots(cfg)
    trader_pots = []
    pot_base = float(trader_pot) if trader_pot is not None else None
    for i, addr in enumerate(cfg.get("trader_addresses") or []):
        num = i + 1
        # Normal copy mirrors attributed to this T# (exclude full_btc)
        t_mirrors = [
            m for m in mirrors
            if m.get("trader") == num and not (
                (m.get("source") or "").lower().startswith("full_")
                or m.get("all_traders")
                or m.get("full_allin")
            )
        ]
        margin_used = 0.0
        upnl = 0.0
        usd_notional = 0.0
        has_upnl = False
        coins = []
        for m in t_mirrors:
            coins.append(m.get("coin"))
            lev = float(m.get("leverage") or 0)
            if lev <= 0:
                lev = float(max_lev or 20)
            mar = float(m.get("margin") or 0)
            if mar <= 0 and m.get("usd_size"):
                mar = float(m["usd_size"]) / lev
            elif mar <= 0 and m.get("notional"):
                mar = float(m["notional"]) / lev
            margin_used += mar
            if m.get("usd_size") is not None:
                usd_notional += float(m["usd_size"])
            if m.get("pnl_usd") is not None:
                upnl += float(m["pnl_usd"])
                has_upnl = True

        # Split full-BTC margin/uPnL across every trader pot
        if full_btc_mode:
            margin_used += share_m
            usd_notional += share_n
            if full_btc_has_upnl:
                upnl += share_u
                has_upnl = True
            for c in full_btc_coins:
                if c and c not in coins:
                    coins.append(c)

        margin_used = round(margin_used, 2)
        upnl = round(upnl, 2) if has_upnl else None
        remaining = None
        pot_after_upnl = None
        over_pot = 0.0
        muted = num in unfollowed_nums
        this_pot = 0.0 if muted else pot_base
        if this_pot is not None:
            raw_room = float(this_pot) - margin_used
            remaining = round(max(0.0, raw_room), 2)
            over_pot = round(max(0.0, -raw_room), 2)
            pot_after_upnl = round(float(this_pot) + (upnl if upnl is not None else 0.0), 2)
        # Account free is shared. HL withdrawable can be 0 with open cross positions
        # even after a deposit — estimate tradable free = max(API free, equity − margin).
        total_margin_all = 0.0
        for mm in mirrors:
            mar = float(mm.get("margin") or 0)
            if mar <= 0 and mm.get("usd_size"):
                lev = float(mm.get("leverage") or max_lev or 20) or 20
                mar = float(mm["usd_size"]) / lev
            total_margin_all += mar
        free_tradable = free_f
        if equity is not None:
            eq_f = float(equity)
            free_est = max(0.0, eq_f - total_margin_all)
            if free_tradable is None or free_est > float(free_tradable or 0):
                free_tradable = free_est
        remaining_free_capped = remaining
        if remaining is not None and free_tradable is not None and free_tradable >= 0:
            remaining_free_capped = round(min(remaining, free_tradable), 2)
        # Only force "fully committed" when there is truly no spare equity
        if full_btc_mode and free_tradable is not None and free_tradable < 1.0:
            remaining_free_capped = 0.0
            if pot_base is not None and margin_used < pot_base:
                margin_used = round(pot_base, 2)
                remaining = 0.0
        short = (addr[:10] + "…" + addr[-6:]) if addr and len(addr) > 16 else (addr or "")
        lead = next((L for L in leaders if L.get("num") == num), {}) or {}
        trader_pots.append({
            "num": num,
            "address": addr,
            "short": short,
            "alive": bool(lead.get("alive")),
            "last_seen": lead.get("last_seen"),
            "leader_n_pos": lead.get("n_pos"),
            "leader_error": bool(lead.get("error")),
            "pot": round(float(this_pot), 2) if this_pot is not None else None,
            "margin_used": margin_used,
            "remaining": remaining,
            "remaining_free_capped": remaining_free_capped,
            "upnl": upnl,
            "pot_after_upnl": pot_after_upnl,
            "n_positions": len(t_mirrors) + (1 if full_btc_mode else 0),
            "max_positions": int(follow_slots.get(num) or max_pos),
            "follow_slots": int(follow_slots.get(num) or max_pos),
            "slot_margin": (
                round(float(this_pot) / float(follow_slots.get(num) or max_pos), 2)
                if this_pot is not None and not muted and (follow_slots.get(num) or max_pos)
                else (0.0 if muted else None)
            ),
            "muted": muted,
            "usd_notional": round(usd_notional, 2),
            "coins": coins,
            "full_btc_shared": full_btc_mode,
            "followed": num not in unfollowed_nums,
            "unfollowed": num in unfollowed_nums,
            "over_pot": over_pot,
            "wallet_free": round(float(free_tradable), 2) if free_tradable is not None else None,
        })

    # Split real wallet free across unmuted traders who still have room in pot.
    # Mute cannot mint cash: leftover T#N already sitting in another T#'s book stays there.
    rooms = [t for t in trader_pots if not t.get("muted") and (t.get("remaining") or 0) > 0]
    total_room = sum(float(t.get("remaining") or 0) for t in rooms)
    wfree = None
    for t in trader_pots:
        if t.get("wallet_free") is not None:
            wfree = float(t["wallet_free"])
            break
    if wfree is None:
        wfree = float(free_tradable) if free_tradable is not None else 0.0
    for t in trader_pots:
        if t.get("muted"):
            t["remaining_free_capped"] = 0.0
            continue
        room = float(t.get("remaining") or 0)
        if total_room > 0 and room > 0:
            share = wfree * (room / total_room)
            t["remaining_free_capped"] = round(min(room, share), 2)
        else:
            t["remaining_free_capped"] = 0.0

    # Risk stop meter: % of path to force-close (loss / max_loss)
    risk_live = (bot_status or {}).get("risk") if bot_status else None
    if not isinstance(risk_live, dict):
        risk_live = {}
    loss_pct = float(
        risk_live.get("max_position_loss_pct")
        if risk_live.get("max_position_loss_pct") is not None
        else cfg.get("max_position_loss_pct") or 0.333
    )
    eq_for_risk = None
    if hb.get("equity") is not None:
        eq_for_risk = float(hb["equity"])
    elif bot_status and bot_status.get("equity") is not None:
        eq_for_risk = float(bot_status["equity"])
    max_loss_usd = risk_live.get("max_loss_usd")
    if max_loss_usd is None and eq_for_risk is not None and loss_pct > 0:
        max_loss_usd = eq_for_risk * loss_pct
    try:
        max_loss_usd = float(max_loss_usd) if max_loss_usd is not None else None
    except (TypeError, ValueError):
        max_loss_usd = None

    for m in mirrors:
        pnl = m.get("pnl_usd")
        try:
            pnl_f = float(pnl) if pnl is not None else None
        except (TypeError, ValueError):
            pnl_f = None
        loss = max(0.0, -(pnl_f if pnl_f is not None else 0.0))
        if max_loss_usd and max_loss_usd > 0 and pnl_f is not None:
            # 0% = no loss (or profit); 100% = at force-close trigger
            stop_pct = min(100.0, (loss / max_loss_usd) * 100.0)
        else:
            stop_pct = 0.0 if (pnl_f is None or pnl_f >= 0) else None
        m["risk_stop_pct"] = round(stop_pct, 1) if stop_pct is not None else None
        m["risk_max_loss_usd"] = round(max_loss_usd, 2) if max_loss_usd is not None else None
        m["risk_loss_usd"] = round(loss, 2) if pnl_f is not None else None
        coin_u = str(m.get("coin") or "").upper()
        # Prefer bot status flag; fall back to overrides file
        if m.get("risk_auto_close_disabled") is None:
            m["risk_auto_close_disabled"] = coin_u in set(_load_risk_overrides())
        else:
            m["risk_auto_close_disabled"] = bool(m.get("risk_auto_close_disabled")) or (
                coin_u in set(_load_risk_overrides())
            )

    risk_payload = {
        "disabled_risk_coins": _load_risk_overrides(),
        "enabled": bool(
            risk_live.get("enabled")
            if risk_live.get("enabled") is not None
            else cfg.get("risk_management_enabled", True)
        ),
        "max_position_loss_pct": loss_pct,
        "max_margin_per_position_pct": float(
            risk_live.get("max_margin_per_position_pct")
            if risk_live.get("max_margin_per_position_pct") is not None
            else cfg.get("max_margin_per_position_pct") or 0.333
        ),
        "min_free_margin_pct": float(
            risk_live.get("min_free_margin_pct")
            if risk_live.get("min_free_margin_pct") is not None
            else cfg.get("min_free_margin_pct") or 0.15
        ),
        "size_exclude_unrealized_profit": bool(
            risk_live.get("size_exclude_unrealized_profit")
            if risk_live.get("size_exclude_unrealized_profit") is not None
            else cfg.get("size_exclude_unrealized_profit", True)
        ),
        "max_loss_usd": round(max_loss_usd, 2) if max_loss_usd is not None else None,
        "sizing_equity": risk_live.get("sizing_equity"),
        "total_upnl": risk_live.get("total_upnl"),
    }

    return jsonify({
        "running": running,
        "heartbeat": hb,
        "network": network,
        "next_position": next_pos,
        "trader_pots": trader_pots,
        "unfollowed_traders": sorted(unfollowed_nums),
        "trader_follow_slots": follow_slots,
        "active": mirrors,
        "risk": risk_payload,
        "bot_status": {
            "fresh": bool(bot_status and bot_status.get("_fresh")),
            "age_sec": (bot_status or {}).get("_age_sec"),
            "n_mirrors": (bot_status or {}).get("n_mirrors"),
            "full_btc_phase": (bot_status or {}).get("full_btc_phase"),
            "full_allin_phase": (bot_status or {}).get("full_allin_phase"),
            "full_allin_coin": (bot_status or {}).get("full_allin_coin"),
            "full_allin_side": (bot_status or {}).get("full_allin_side") or "LONG",
            "ts": (bot_status or {}).get("ts"),
        } if bot_status else None,
        "failures": failures,
        "leader_opens": leader_opens,  # every leader open + time + copied?
        "errors": errors,
        "leaders": leaders,
        "unfollowed": unfollowed,
        "config": {
            "min_leverage": cfg.get("min_leverage"),
            "max_leverage": cfg.get("max_leverage"),
            "min_open_positions": cfg.get("min_open_positions"),
            "allocation_pct": cfg.get("allocation_pct"),
            "commodity_allocation_pct": cfg.get("commodity_allocation_pct"),
            "traders": len(cfg.get("trader_addresses") or []),
            "copy_shorts_yml": cfg.get("copy_shorts"),
            "copy_shorts": effective_copy_shorts(cfg),
            "longs_only": not effective_copy_shorts(cfg),
            "unfollowed_traders": sorted(unfollowed_nums),
            "trader_follow_slots": follow_slots,
            "max_position_loss_pct": loss_pct,
            "hl_btc_max_leverage": fetch_hl_max_leverage_coin("BTC"),
            "hl_eth_max_leverage": fetch_hl_max_leverage_coin("ETH"),
            "hl_ltc_max_leverage": fetch_hl_max_leverage_coin("LTC"),
            "hl_zec_max_leverage": fetch_hl_max_leverage_coin("ZEC"),
            "max_zec_short_usd": _effective_max_zec_short_usd(cfg, bot_status),
        },
        "logs": filter_display_logs("hummingbot-hl", logs, limit=24),
        "audits": _copy_audit_summary(),
        "sol_live": get_sol_live(),
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })

@app.route("/api/status")
def api_status():
    result = {}
    btc = get_btc_live()
    sol = get_sol_live()

    for bot_id, meta in BOTS.items():
        running = get_container_running(bot_id)
        logs = get_logs(bot_id, 150) if running else []

        if bot_id == "hummingbot-hl":
            parsed = parse_hl_status(logs)
            parsed["btc_live"] = btc
        elif bot_id == "hummingbot-sol":
            parsed = parse_sol_status(logs)
            parsed["sol_live"] = sol
        else:
            parsed = parse_agenc_status(logs)

        if bot_id == "hummingbot-hl":
            recent = [l for l in logs if any(k in l for k in
                      ["WE COPY", "WE CLOSE", "heartbeat", "ERROR", "WARNING",
                       "restore", "reconcile", "close-retry", "orphan", "stall"])][-10:]
        else:
            recent = [l for l in logs if any(k in l for k in
                      ["ENTERING", "EXITING", "BBP", "Signal", "BTC Price", "SOL",
                       "PnL", "TAKE", "STOP", "leverage", "connected", "ERROR",
                       "WARNING", "balance", "Leverage"])][-10:]

        result[bot_id] = {
            "running": running,
            "parsed": parsed,
            "recent_logs": recent,
        }

    return jsonify(result)

FORCE_CLOSE_HL_COPY = os.path.join(CONF_DIR, "force_close_hl_copy.json")
FORCE_FOLLOW_HL_COPY = os.path.join(CONF_DIR, "force_follow_hl_copy.json")
FORCE_SCALE_HL_COPY = os.path.join(CONF_DIR, "force_scale_hl_copy.json")
FORCE_FULL_BTC_HL_COPY = os.path.join(CONF_DIR, "force_full_btc_hl_copy.json")
FORCE_FULL_ALLIN_HL_COPY = os.path.join(CONF_DIR, "force_full_allin_hl_copy.json")
FORCE_ADD_ETH_HL_COPY = os.path.join(CONF_DIR, "force_add_eth_hl_copy.json")
FORCE_DROP_MARKETS_HL_COPY = os.path.join(CONF_DIR, "force_drop_markets_hl_copy.json")


def _write_hl_force_close(coin=None, close_all=False):
    """Queue a Copy close command in matrix.db. Does not place the order here."""
    if not close_all and not coin:
        return False, "Provide coin or all=true", 400
    if coin and not re.match(r'^[A-Za-z0-9]{1,20}$', coin):
        return False, "Invalid coin symbol", 400
    payload = {"source": "dashboard"}
    if close_all:
        payload["all"] = True
    else:
        payload["coin"] = coin
        payload["coins"] = [coin]
    cid = _sql_command("hummingbot-hl", "close", payload)
    if not cid:
        return False, "sqlite command queue failed", 500
    payload["id"] = cid
    msg = "CLOSE ALL signal sent" if close_all else f"Close signal sent for {coin}"
    return True, {"ok": True, "message": msg, "payload": payload}, 200


def _write_hl_force_follow(trader=None, coin=None, side=None):
    """Write conf/force_follow_hl_copy.json — open a missed leader position at live mid."""
    if not coin:
        return False, "Provide coin", 400
    if not re.match(r'^[A-Za-z0-9]{1,20}$', coin):
        return False, "Invalid coin symbol", 400
    if trader is None:
        return False, "Provide trader number (1-3)", 400
    try:
        trader = int(trader)
    except (TypeError, ValueError):
        return False, "trader must be an integer 1-3", 400
    if trader < 1 or trader > 10:
        return False, "trader out of range", 400
    side_u = None
    if side:
        side_u = str(side).strip().upper()
        if side_u not in ("LONG", "SHORT"):
            return False, "side must be LONG or SHORT", 400
    payload = {
        "source": "dashboard",
        "trader": trader,
        "coin": coin,
    }
    if side_u:
        payload["side"] = side_u
    cid = _sql_command("hummingbot-hl", "follow", payload)
    if not cid:
        return False, "sqlite command queue failed", 500
    payload["id"] = cid
    msg = f"Follow signal sent: T#{trader} {side_u or ''} {coin}".strip()
    return True, {"ok": True, "message": msg, "payload": payload}, 200


@app.route("/api/hl_close", methods=["POST"])
def api_hl_close():
    """
    Signal the HL copy trader to market-close a position immediately.
    Body JSON: {"coin": "SOL"}  or  {"all": true}
    Strategy picks up conf/force_close_hl_copy.json on next poll (~2s).
    """
    data = request.get_json(silent=True) or {}
    ok, result, code = _write_hl_force_close(
        coin=(data.get("coin") or "").strip() or None,
        close_all=bool(data.get("all")),
    )
    if not ok:
        return jsonify({"ok": False, "error": result}), code
    return jsonify(result)


def _load_risk_overrides():
    """Coins with risk auto-stop disabled (dashboard toggle)."""
    try:
        with open(HL_RISK_OVERRIDES, "r") as f:
            data = json.load(f) or {}
    except Exception:
        data = {}
    out = set()
    for c in (data.get("disabled_risk_coins") or data.get("coins") or []):
        try:
            out.add(str(c).strip().upper())
        except Exception:
            pass
    return sorted(out)


@app.route("/api/hl_risk_auto_close", methods=["GET", "POST"])
def api_hl_risk_auto_close():
    """
    Toggle risk auto-stop for a position (the 100% risk-bar force close).

    POST body:
      {"coin": "ETH", "enabled": false}  # turn auto-stop OFF (hold through 100%)
      {"coin": "ETH", "enabled": true}   # turn auto-stop ON again

    100% on the risk bar = unrealized loss reached max_position_loss_pct of equity
    (default 1/3 equity) — not 100% position ROE.
    """
    if request.method == "GET":
        disabled = _load_risk_overrides()
        return jsonify({
            "ok": True,
            "disabled_risk_coins": disabled,
            "note": "Auto-stop closes when risk bar hits 100% (loss ≥ ~1/3 equity).",
        })

    data = request.get_json(silent=True) or {}
    coin = (data.get("coin") or "").strip().upper()
    if not coin or not re.match(r"^[A-Z0-9]{1,20}$", coin):
        return jsonify({"ok": False, "error": "Provide coin (e.g. ETH)"}), 400

    if "enabled" in data:
        want_on = bool(data["enabled"])
    elif "disable" in data:
        want_on = not bool(data["disable"])
    else:
        return jsonify({"ok": False, "error": "Provide enabled true/false"}), 400

    disabled = set(_load_risk_overrides())
    if want_on:
        disabled.discard(coin)
        action = "ON"
        msg = f"{coin} risk auto-stop ON — will force-close at 100% risk bar"
    else:
        disabled.add(coin)
        action = "OFF"
        msg = f"{coin} risk auto-stop OFF — will NOT auto-close at 100% risk bar"

    # Persist immediately so UI refreshes correctly; bot also writes on signal
    try:
        with open(HL_RISK_OVERRIDES, "w") as f:
            json.dump({
                "ts": datetime.utcnow().isoformat() + "Z",
                "source": "dashboard",
                "disabled_risk_coins": sorted(disabled),
            }, f, indent=2)
    except Exception as e:
        return jsonify({"ok": False, "error": f"persist failed: {e}"}), 500

    cid = _sql_command("hummingbot-hl", "risk_toggle", {
        "coin": coin,
        "enabled": want_on,
        "source": "dashboard",
    })
    if not cid:
        return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500

    return jsonify({
        "ok": True,
        "message": msg,
        "coin": coin,
        "enabled": want_on,
        "action": action,
        "disabled_risk_coins": sorted(disabled),
    })


@app.route("/api/hl_follow", methods=["POST"])
def api_hl_follow():
    """
    Manually follow a leader position we are not in (dashboard unfollowed list).
    Body JSON: {"trader": 1, "coin": "LIT", "side": "SHORT"}
    Strategy opens at live HL mid and registers the mirror under that trader so
    when the leader closes, we auto-close.
    """
    data = request.get_json(silent=True) or {}
    ok, result, code = _write_hl_force_follow(
        trader=data.get("trader"),
        coin=(data.get("coin") or "").strip() or None,
        side=(data.get("side") or "").strip() or None,
    )
    if not ok:
        return jsonify({"ok": False, "error": result}), code
    return jsonify(result)


def _write_hl_force_scale(coin=None, trader=None):
    """
    Write conf/force_scale_hl_copy.json — add remaining trader-pot funds to an
    existing mirror (dashboard Full scale button).
    """
    if not coin:
        return False, "Provide coin", 400
    if not re.match(r'^[A-Za-z0-9]{1,20}$', coin):
        return False, "Invalid coin symbol", 400
    payload = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "source": "dashboard",
        "coin": coin,
    }
    if trader is not None and trader != "":
        try:
            t = int(trader)
        except (TypeError, ValueError):
            return False, "trader must be an integer", 400
        if t < 1 or t > 10:
            return False, "trader out of range", 400
        payload["trader"] = t
    cid = _sql_command("hummingbot-hl", "scale", payload)
    if not cid:
        return False, "sqlite command queue failed", 500
    payload["id"] = cid
    tpart = f" T#{payload['trader']}" if "trader" in payload else ""
    msg = f"Full scale signal sent:{tpart} {coin} (all tradable free → add size)"
    return True, {"ok": True, "message": msg, "payload": payload}, 200


@app.route("/api/hl_scale", methods=["POST"])
def api_hl_scale():
    """
    Full scale an open mirror: use the rest of that trader's pot as extra margin
    and market-add to the position (same side).
    Body JSON: {"coin": "SOL"}  or  {"coin": "SOL", "trader": 1}
    Strategy picks up conf/force_scale_hl_copy.json on next poll (~2s).
    """
    data = request.get_json(silent=True) or {}
    ok, result, code = _write_hl_force_scale(
        coin=(data.get("coin") or "").strip() or None,
        trader=data.get("trader"),
    )
    if not ok:
        return jsonify({"ok": False, "error": result}), code
    return jsonify(result)


def fetch_hl_max_leverage_coin(coin="BTC"):
    """Hyperliquid maxLeverage for a coin from public meta (uncapped by our yml)."""
    coin = (coin or "BTC").upper()

    def _load_meta():
        try:
            return _hl_info_post({"type": "meta"}, timeout=6) or {}
        except Exception:
            return {}

    meta = _ttl_get("hl_meta", 300, _load_meta)
    try:
        for asset in (meta or {}).get("universe", []):
            if asset.get("name") == coin and asset.get("maxLeverage"):
                return int(asset["maxLeverage"])
    except Exception:
        pass
    return None


def fetch_hl_max_leverage_btc():
    return fetch_hl_max_leverage_coin("BTC")


def _write_full_allin_signal(coin, leverage_pct, side="LONG"):
    coin = (coin or "BTC").strip().upper()
    if not re.match(r'^[A-Za-z0-9]{1,20}$', coin):
        return False, "Invalid coin", 400
    side = "SHORT" if str(side or "LONG").upper() == "SHORT" else "LONG"
    try:
        pct = float(leverage_pct)
    except (TypeError, ValueError):
        return False, "leverage_pct must be a number", 400
    pct = max(1.0, min(100.0, pct))
    cfg = load_hl_config()
    n_traders = max(1, len(cfg.get("trader_addresses") or []) or 3)
    fallback = 40 if coin == "BTC" else (10 if coin == "LTC" else (5 if coin == "ZEC" else 25))
    hl_max = fetch_hl_max_leverage_coin(coin) or fallback
    eff_lev = max(1, int(round(hl_max * pct / 100.0)))
    payload = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "source": "dashboard",
        "leverage_pct": pct,
        "coin": coin,
        "side": side,
        "use_all_traders_funds": True,
        "use_hl_max_leverage": True,
        "n_traders": n_traders,
        f"hl_{coin.lower()}_max_leverage": hl_max,
    }
    cid = _sql_command("hummingbot-hl", "full_allin", payload)
    if not cid:
        return False, "sqlite command queue failed", 500
    payload["id"] = cid
    return True, {
        "ok": True,
        "message": (
            f"FULL {coin} armed: close Copy mirrors → {side} {coin} with Copy wallet "
            f"@ ~{eff_lev}x "
            f"({pct:.0f}% of Hyperliquid {coin} max {hl_max}x)"
        ),
        "payload": payload,
        "effective_leverage": eff_lev,
        "hl_max_leverage": hl_max,
        "n_traders": n_traders,
        "coin": coin,
        "side": side,
    }, 200


@app.route("/api/hl_full_btc", methods=["POST"])
def api_hl_full_btc():
    """
    Close Copy mirrors → LONG BTC with Copy wallet funds only.
    Other hummingbots are not used. Default leverage_pct=100 of Hyperliquid BTC max.
    """
    data = request.get_json(silent=True) or {}
    ok, result, code = _write_full_allin_signal("BTC", data.get("leverage_pct", 100))
    if not ok:
        return jsonify({"ok": False, "error": result}), code
    # keep response field names used by older UI
    result["hl_btc_max_leverage"] = result.get("hl_max_leverage")
    return jsonify(result)


@app.route("/api/hl_full_eth", methods=["POST"])
def api_hl_full_eth():
    """
    Close ALL → LONG ETH with ALL traders' funds (maxi).
    Default leverage_pct=100 of Hyperliquid ETH max.
    """
    data = request.get_json(silent=True) or {}
    ok, result, code = _write_full_allin_signal("ETH", data.get("leverage_pct", 100))
    if not ok:
        return jsonify({"ok": False, "error": result}), code
    result["hl_eth_max_leverage"] = result.get("hl_max_leverage")
    return jsonify(result)


@app.route("/api/hl_full_zec_short", methods=["POST"])
def api_hl_full_zec_short():
    """
    Close Copy mirrors → SHORT ZEC with Copy wallet funds only.
    Other hummingbots are not used. Ignores longs-only for this emergency.
    Default leverage_pct=100 of Hyperliquid ZEC max.
    """
    data = request.get_json(silent=True) or {}
    ok, result, code = _write_full_allin_signal("ZEC", data.get("leverage_pct", 100), side="SHORT")
    if not ok:
        return jsonify({"ok": False, "error": result}), code
    result["hl_zec_max_leverage"] = result.get("hl_max_leverage")
    return jsonify(result)


@app.route("/api/hl_full_ltc", methods=["POST"])
def api_hl_full_ltc():
    """
    Close Copy mirrors → LONG LTC with Copy wallet funds only.
    Other hummingbots are not used. Default leverage_pct=100 of Hyperliquid LTC max.
    """
    data = request.get_json(silent=True) or {}
    ok, result, code = _write_full_allin_signal("LTC", data.get("leverage_pct", 100))
    if not ok:
        return jsonify({"ok": False, "error": result}), code
    result["hl_ltc_max_leverage"] = result.get("hl_max_leverage")
    return jsonify(result)


@app.route("/api/hl_add_eth", methods=["POST"])
def api_hl_add_eth():
    """
    Add LONG ETH using funds_pct% of free balance (does NOT close other positions).
    Body: {"funds_pct": 50} or {"funds_pct": 100}
    Position is manual-close-only (not closed by orphan / leader).
    """
    data = request.get_json(silent=True) or {}
    try:
        pct = float(data.get("funds_pct", data.get("pct", 50)))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "funds_pct must be 50 or 100"}), 400
    pct = 100.0 if pct >= 75 else 50.0
    payload = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "source": "dashboard",
        "funds_pct": pct,
        "coin": "ETH",
        "side": "LONG",
        "manual_close_only": True,
    }
    cid = _sql_command("hummingbot-hl", "add_eth", payload)
    if not cid:
        return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
    payload["id"] = cid
    return jsonify({
        "ok": True,
        "message": (
            f"ADD ETH armed: LONG ETH with {pct:.0f}% of free margin "
            f"(does not close other positions; manual close only)"
        ),
        "payload": payload,
    })


@app.route("/api/hl_full_allin", methods=["POST"])
def api_hl_full_allin():
    """Generic: {"coin":"ETH","leverage_pct":100}"""
    data = request.get_json(silent=True) or {}
    coin = (data.get("coin") or "BTC").strip().upper()
    default_pct = 100
    ok, result, code = _write_full_allin_signal(coin, data.get("leverage_pct", default_pct))
    if not ok:
        return jsonify({"ok": False, "error": result}), code
    return jsonify(result)


@app.route("/api/hl_zec_short", methods=["GET", "POST"])
def api_hl_zec_short():
    """Cap ZEC SHORT notional USD for Copy. 0 = no extra cap. Does not close existing."""
    cfg = load_hl_config()
    bot_status = load_hl_bot_status()
    if request.method == "GET":
        v = _effective_max_zec_short_usd(cfg, bot_status)
        return jsonify({"ok": True, "max_zec_short_usd": v})
    data = request.get_json(silent=True) or {}
    try:
        v = float(data.get("max_zec_short_usd"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "max_zec_short_usd must be a number"}), 400
    if v < 0 or v > 500000:
        return jsonify({"ok": False, "error": "max_zec_short_usd must be 0–500000"}), 400
    v = round(v, 2)
    try:
        save_hl_runtime_flags({"max_zec_short_usd": v})
        _ctrl_write("hummingbot-hl", {"max_zec_short_usd": v})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    msg = (
        "ZEC SHORT cap off — normal pot sizing"
        if v <= 0
        else f"ZEC SHORT capped at ${v:g} notional"
    )
    return jsonify({"ok": True, "max_zec_short_usd": v, "message": msg, "applied": False})


@app.route("/api/hl_copy_mode", methods=["GET", "POST"])
def api_hl_copy_mode():
    """
    Toggle / read copy mode: longs-only vs longs+shorts.
    POST body: {"longs_only": true}  or  {"copy_shorts": false}
    Persists to conf/hl_copy_runtime.json; bot reloads within ~2s.
    Does NOT auto-close existing short mirrors — only blocks new shorts.
    """
    cfg = load_hl_config()
    if request.method == "GET":
        cs = effective_copy_shorts(cfg)
        return jsonify({
            "ok": True,
            "copy_shorts": cs,
            "longs_only": not cs,
            "yml_copy_shorts": cfg.get("copy_shorts"),
            "unfollowed_traders": effective_unfollowed_traders(cfg),
            "runtime": load_hl_runtime_flags(),
        })

    data = request.get_json(silent=True) or {}
    if "longs_only" in data:
        longs_only = bool(data["longs_only"])
        copy_shorts = not longs_only
    elif "copy_shorts" in data:
        copy_shorts = bool(data["copy_shorts"])
        longs_only = not copy_shorts
    else:
        return jsonify({"ok": False, "error": "Provide longs_only or copy_shorts"}), 400

    payload = {
        "copy_shorts": copy_shorts,
        "longs_only": longs_only,
    }
    try:
        prev = save_hl_runtime_flags(payload)
        mode = "LONGS ONLY (no new shorts)" if longs_only else "LONGS + SHORTS"
        return jsonify({
            "ok": True,
            "message": f"Copy mode → {mode}",
            "copy_shorts": copy_shorts,
            "longs_only": longs_only,
            "payload": prev,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/hl_trader_follow", methods=["GET", "POST"])
def api_hl_trader_follow():
    """
    Follow / unfollow a copy leader immediately.

    POST body:
      {"trader": 1, "follow": false}   # unfollow T#1 — stop new copies now
      {"trader": 2, "unfollow": true}  # same
      {"trader": 1, "follow": true}    # resume copy trading T#1

    Persists to conf/hl_copy_runtime.json → unfollowed_traders.
    Bot reloads within ~2s. Mute also flattens our mirrors for that trader.
    """
    cfg = load_hl_config()
    n = len(cfg.get("trader_addresses") or [])
    if request.method == "GET":
        return jsonify({
            "ok": True,
            "unfollowed_traders": effective_unfollowed_traders(cfg),
            "followed_traders": [i for i in range(1, n + 1)
                                 if i not in set(effective_unfollowed_traders(cfg))],
            "n_traders": n,
        })

    data = request.get_json(silent=True) or {}
    try:
        trader = int(data.get("trader"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Provide trader number (1-based)"}), 400
    if trader < 1 or (n and trader > n):
        return jsonify({"ok": False, "error": f"Trader T#{trader} out of range (1..{n})"}), 400

    if "mute" in data:
        want_follow = not bool(data["mute"])
    elif "follow" in data:
        want_follow = bool(data["follow"])
    elif "unfollow" in data:
        want_follow = not bool(data["unfollow"])
    else:
        return jsonify({"ok": False, "error": "Provide mute, follow, or unfollow"}), 400

    current = set(effective_unfollowed_traders(cfg))
    if want_follow:
        current.discard(trader)
        action = "UNMUTE"
        n_left = n - len(current)
        msg = (
            f"T#{trader} unmuted — copy resumes. "
            f"Pots split across {n_left} trader(s)."
        )
    else:
        if n >= 1 and len(current) >= n - 1 and trader not in current:
            return jsonify({
                "ok": False,
                "error": f"Keep at least one trader unmuted (T#{trader} is the last live slot)",
            }), 400
        current.add(trader)
        action = "MUTE"
        n_left = max(1, n - len(current))
        others = [i for i in range(1, n + 1) if i not in current]
        who = ", ".join(f"T#{i}" for i in others) or "remaining"
        msg = (
            f"T#{trader} muted — no new copies, Copy flattens that trader's clips. "
            f"That pot goes to {who} ({n_left} live)."
        )

    try:
        payload = save_hl_runtime_flags({
            "unfollowed_traders": sorted(current),
        })
        return jsonify({
            "ok": True,
            "message": msg,
            "action": action,
            "trader": trader,
            "followed": want_follow,
            "unfollowed_traders": sorted(current),
            "payload": payload,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/hl_trader_slots", methods=["POST"])
def api_hl_trader_slots():
    """Set how many trades T#N may follow: 1, 2 or 3. Each uses 1/N of that pot."""
    cfg = load_hl_config()
    n = len(cfg.get("trader_addresses") or [])
    data = request.get_json(silent=True) or {}
    try:
        trader = int(data.get("trader"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Provide trader number (1-based)"}), 400
    if trader < 1 or (n and trader > n):
        return jsonify({"ok": False, "error": f"Trader T#{trader} out of range (1..{n})"}), 400
    try:
        slots = int(data.get("slots", data.get("max_positions")))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "slots must be 1, 2 or 3"}), 400
    if slots not in (1, 2, 3):
        return jsonify({"ok": False, "error": "slots must be 1, 2 or 3"}), 400
    current = effective_trader_follow_slots(cfg)
    current[trader] = slots
    stored = {str(k): int(v) for k, v in current.items()}
    try:
        payload = save_hl_runtime_flags({"trader_follow_slots": stored})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({
        "ok": True,
        "trader": trader,
        "slots": slots,
        "trader_follow_slots": current,
        "message": f"T#{trader} follows {slots} trade{'s' if slots != 1 else ''} — each 1/{slots} of that pot",
        "payload": payload,
    })


@app.route("/api/force_close/<bot_id>", methods=["POST"])
def force_close(bot_id):
    if bot_id not in BOTS:
        return jsonify({"ok": False, "error": "Unknown bot"}), 404
    meta = BOTS[bot_id]
    if bot_id == "hummingbot-hl":
        body = request.get_json(silent=True) or {}
        ok, result, code = _write_hl_force_close(
            coin=(body.get("coin") or "").strip() or None,
            close_all=bool(body.get("all")),
        )
        if not ok:
            return jsonify({"ok": False, "error": result}), code
        return jsonify(result)
    cid = _sql_command(bot_id, "close", {"all": True})
    if not cid:
        return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
    return jsonify({"ok": True, "message": f"Force close signal sent to {meta['name']}"})


@app.route("/api/force_entry/<bot_id>", methods=["POST"])
def force_entry(bot_id):
    if bot_id not in BOTS:
        return jsonify({"ok": False, "error": "Unknown bot"}), 404
    meta = BOTS[bot_id]
    if not meta["can_force_entry"]:
        return jsonify({"ok": False, "error": "Not supported for this bot"}), 400
    try:
        with open(meta["force_entry_file"], "w") as f:
            f.write(datetime.utcnow().isoformat())
        return jsonify({"ok": True, "message": f"Force entry signal sent to {meta['name']}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/logs/<bot_id>")
def api_logs(bot_id):
    if bot_id not in BOTS:
        return jsonify([]), 404
    # Read more raw lines so filtering still leaves a useful window
    logs = get_logs(bot_id, 500)
    return jsonify(filter_display_logs(bot_id, logs, limit=120))


SWING_COINS = ("BTC", "ETH", "LTC", "TAO")
SWING_BIAS_VALUES = ("LONG", "SHORT", "FLAT")


def _normalize_swing_bias(raw):
    out = {c: "FLAT" for c in SWING_COINS}
    if not isinstance(raw, dict):
        return out
    coins_map = raw.get("coins") if isinstance(raw.get("coins"), dict) else {}
    for c in SWING_COINS:
        v = str(raw.get(c) or coins_map.get(c) or "").upper()
        if v in SWING_BIAS_VALUES:
            out[c] = v
    coin = str(raw.get("coin") or "").upper()
    bias = str(raw.get("bias") or "").upper()
    if coin in SWING_COINS and bias in SWING_BIAS_VALUES:
        out[coin] = bias
    if raw.get("updated"):
        out["updated"] = raw.get("updated")
    if raw.get("source"):
        out["source"] = raw.get("source")
    return out


def _hunter_bias_payload(bot_id, coins=None, clip=None):
    """Live BIAS: newer of sqlite vs JSON (Spring writes JSON only)."""
    coins = tuple(coins or SWING_COINS)
    raw = _live_bias_raw(bot_id) or {}
    out = {c: "FLAT" for c in coins}
    coins_map = raw.get("coins") if isinstance(raw, dict) and isinstance(raw.get("coins"), dict) else {}
    for c in coins:
        v = str((raw or {}).get(c) or coins_map.get(c) or "").upper()
        if clip == "long" and v == "SHORT":
            v = "FLAT"
        if clip == "short" and v == "LONG":
            v = "FLAT"
        if v in ("LONG", "SHORT", "FLAT"):
            out[c] = v
    return out


def _open_side(data, bot_id, coin, coins=None):
    """100% leftover side: body LONG/SHORT wins, else live SQL/file BIAS."""
    side = str((data or {}).get("side") or "").upper().strip()
    if side in ("LONG", "SHORT"):
        return side
    bias = _hunter_bias_payload(bot_id, coins=coins)
    return str(bias.get(coin) or "FLAT").upper()


@app.route("/api/bn_swing")
def api_bn_swing():
    """Status for PMM Simple (same container/wallet as former HL Swing)."""
    running = get_container_running("hummingbot-bn-swing")
    bias = _hunter_bias_payload("hummingbot-bn-swing")
    status = {}
    for _ in range(3):
        try:
            with open(BN_SWING_STATUS_FILE, "r") as f:
                status = json.load(f)
            break
        except Exception:
            status = {}
    trades = []
    trade_summary = {}
    try:
        with open(BN_SWING_TRADES_FILE, "r") as f:
            log = json.load(f) or {}
        trades = list(log.get("trades") or [])[-80:]
        trades.reverse()
        trade_summary = {
            "realized_pnl_usd": log.get("realized_pnl_usd", 0.0),
            "n_wins": log.get("n_wins", 0),
            "n_losses": log.get("n_losses", 0),
            "n_opens": log.get("n_opens", 0),
            "n_closes": log.get("n_closes", 0),
        }
    except Exception:
        pass
    wallet = (status or {}).get("subaccount") or wallet_for("hummingbot-bn-swing", "")
    live = fetch_hl_account(wallet) if wallet else None
    spot_d = fetch_hl_spot_detail(wallet) if wallet else None
    if not isinstance(status, dict):
        status = {}
    if live:
        if live.get("equity") is not None:
            status["equity_usd"] = float(live["equity"])
            status["perp_usd"] = float(live["equity"])
        if live.get("free") is not None:
            status["free_usd"] = float(live["free"])
    if spot_d:
        status["spot_usd"] = float(spot_d.get("total") or 0)
        status["spot_hold_usd"] = float(spot_d.get("hold") or 0)
        status["spot_free_usd"] = float(spot_d.get("free") or 0)
    sl_roe = _sl_roe_for("hummingbot-bn-swing", 20.0)
    status["sl_roe_pct"] = sl_roe
    if live is not None:
        live_map = {}
        for p in live.get("positions") or []:
            c = str(p.get("coin") or "").upper()
            if c:
                live_map[c] = p
        pos_out = dict(status.get("positions") or {})
        for coin in SWING_COINS:
            row = dict(pos_out.get(coin) or {})
            lp = live_map.get(coin)
            size = abs(float(lp.get("size") or 0)) if lp else 0.0
            if not lp or size < 1e-9:
                row["position_side"] = None
                row["amount"] = None
                row["entry_price"] = None
                row["roe_pct"] = None
            else:
                row["position_side"] = str(lp.get("side") or "LONG").upper()
                row["amount"] = size
                if lp.get("entry_px"):
                    row["entry_price"] = lp.get("entry_px")
                if lp.get("leverage"):
                    row["leverage"] = lp.get("leverage")
                if lp.get("unrealized_pnl") is not None:
                    row["unrealized_pnl"] = lp.get("unrealized_pnl")
            pos_out[coin] = row
        status["positions"] = pos_out
        upnl = 0.0
        for row in pos_out.values():
            if isinstance(row, dict) and row.get("unrealized_pnl") is not None:
                try:
                    upnl += float(row.get("unrealized_pnl"))
                except (TypeError, ValueError):
                    pass
        if any(isinstance(row, dict) and row.get("unrealized_pnl") is not None for row in pos_out.values()):
            status["open_upnl_usd"] = round(upnl, 4)
    return jsonify({
        "running": running,
        "bias": bias,
        "status": status,
        "trades": trades,
        "trade_summary": trade_summary,
        "coins": list(SWING_COINS),
        "subaccount": wallet or None,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "sl_roe_pct": sl_roe,
    })


@app.route("/api/bn_swing/bias", methods=["POST"])
def api_bn_swing_bias():
    """
    Set per-coin PMM Simple bias. Body: {"bias": "LONG"|"SHORT"|"FLAT", "coin": "BTC"|"ETH"|"LTC"|"TAO"}
    100% leftover follows this BIAS. Does not close an open clip.
    """
    data = request.get_json(silent=True) or {}
    bias = str(data.get("bias") or "").upper().strip()
    if bias not in SWING_BIAS_VALUES:
        return jsonify({"ok": False, "error": "bias must be LONG, SHORT, or FLAT"}), 400
    coin = str(data.get("coin") or "").upper().strip()
    if coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    current = _hunter_bias_payload("hummingbot-bn-swing")
    current[coin] = bias
    payload = {c: current.get(c, "FLAT") for c in SWING_COINS}
    payload.update({
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    })
    try:
        _sql_bias_write("hummingbot-bn-swing", payload)
        with open(BN_SWING_BIAS_FILE, "w") as f:
            json.dump(payload, f, indent=2)
        return jsonify({
            "ok": True,
            "message": f"{coin} bias set to {bias}",
            "bias": payload,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/bn_swing/open", methods=["POST"])
def api_bn_swing_open():
    """
    Market-open PMM Simple leftover USD on the current BIAS (LONG or SHORT).
    Body: {"coin": "BTC"|"ETH"|"LTC"|"TAO", "pct": 20|50|100}
    Does not touch an already-open clip.
    """
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "").upper().strip()
    if coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    try:
        raw_pct = float(data.get("pct"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "pct must be 20, 50, or 100"}), 400
    if raw_pct > 1:
        raw_pct = raw_pct / 100.0
    pct = None
    for allowed in (0.2, 0.5, 1.0):
        if abs(raw_pct - allowed) < 1e-6:
            pct = allowed
            break
    if pct is None:
        return jsonify({"ok": False, "error": "pct must be 20, 50, or 100"}), 400

    side = _open_side(data, "hummingbot-bn-swing", coin)
    if side not in ("LONG", "SHORT"):
        return jsonify({"ok": False, "error": f"set {coin} bias to LONG or SHORT before opening"}), 400

    status = {}
    try:
        with open(BN_SWING_STATUS_FILE, "r") as f:
            status = json.load(f) or {}
    except Exception:
        pass
    pos = ((status.get("positions") or {}).get(coin) or {})
    if pos.get("position_side"):
        return jsonify({
            "ok": False,
            "error": f"{coin} already {pos.get('position_side')} — close first",
        }), 400

    payload = {
        "coin": coin,
        "side": side,
        "pct": pct,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    }
    cid = _sql_command("hummingbot-bn-swing", "open", payload)
    if not cid:
        return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
    payload["id"] = cid
    return jsonify({
        "ok": True,
        "message": f"Opening {side} {coin} at {int(pct*100)}% leftover",
        "open": payload,
    })


@app.route("/api/bn_swing/close", methods=["POST"])
def api_bn_swing_close():
    """Force-close HL swing position(s). Body optional: {"coin": "BTC"|"ETH"|"LTC"}."""
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "").upper().strip()
    if coin and coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    target = coin or "ALL"
    body = {"coin": coin} if coin else {"all": True}
    cid = _sql_command("hummingbot-bn-swing", "close", body)
    if not cid:
        return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
    label = target if target != "ALL" else "BTC, ETH and LTC"
    return jsonify({"ok": True, "message": f"Force close signal sent for {label}"})


@app.route("/api/helliot")
def api_helliot():
    running = get_container_running("hummingbot-helliot")
    bias = _hunter_bias_payload("hummingbot-helliot")
    status = {}
    for _ in range(3):
        try:
            with open(HELLIOT_STATUS_FILE, "r") as f:
                status = json.load(f)
            break
        except Exception:
            status = {}
    trades = []
    trade_summary = {}
    try:
        with open(HELLIOT_TRADES_FILE, "r") as f:
            log = json.load(f) or {}
        trades = list(log.get("trades") or [])[-80:]
        trades.reverse()
        trade_summary = {
            "realized_pnl_usd": log.get("realized_pnl_usd", 0.0),
            "n_wins": log.get("n_wins", 0),
            "n_losses": log.get("n_losses", 0),
            "n_opens": log.get("n_opens", 0),
            "n_closes": log.get("n_closes", 0),
        }
    except Exception:
        pass
    wallet = (status or {}).get("subaccount") or wallet_for(
        "hummingbot-helliot", ""
    )
    live = fetch_hl_account(wallet)
    spot = fetch_hl_spot_usdc(wallet)
    if not isinstance(status, dict):
        status = {}
    if live:
        if live.get("equity") is not None:
            status["equity_usd"] = float(live["equity"])
            status["perp_usd"] = float(live["equity"])
        if live.get("free") is not None:
            status["free_usd"] = float(live["free"])
    if spot is not None:
        status["spot_usd"] = float(spot)
    sl_roe = _sl_roe_for("hummingbot-helliot", 20.0)
    if isinstance(status, dict):
        status["sl_roe_pct"] = sl_roe
    return jsonify({
        "running": running,
        "bias": bias,
        "status": status,
        "trades": trades,
        "trade_summary": trade_summary,
        "coins": list(SWING_COINS),
        "subaccount": wallet,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "sl_roe_pct": sl_roe,
    })


@app.route("/api/helliot/bias", methods=["POST"])
def api_helliot_bias():
    data = request.get_json(silent=True) or {}
    bias = str(data.get("bias") or "").upper().strip()
    if bias not in SWING_BIAS_VALUES:
        return jsonify({"ok": False, "error": "bias must be LONG, SHORT, or FLAT"}), 400
    coin = str(data.get("coin") or "").upper().strip()
    if coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    current = _hunter_bias_payload("hummingbot-helliot")
    current[coin] = bias
    payload = {c: current.get(c, "FLAT") for c in SWING_COINS}
    payload.update({
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    })
    try:
        _sql_bias_write("hummingbot-helliot", payload)
        with open(HELLIOT_BIAS_FILE, "w") as f:
            json.dump(payload, f, indent=2)
        return jsonify({"ok": True, "message": f"{coin} bias set to {bias}", "bias": payload})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/helliot/open", methods=["POST"])
def api_helliot_open():
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "").upper().strip()
    if coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    try:
        raw_pct = float(data.get("pct"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "pct must be 20, 50, or 100"}), 400
    if raw_pct > 1:
        raw_pct = raw_pct / 100.0
    pct = None
    for allowed in (0.2, 0.5, 1.0):
        if abs(raw_pct - allowed) < 1e-6:
            pct = allowed
            break
    if pct is None:
        return jsonify({"ok": False, "error": "pct must be 20, 50, or 100"}), 400
    side = _open_side(data, "hummingbot-helliot", coin)
    if side not in ("LONG", "SHORT"):
        return jsonify({"ok": False, "error": f"set {coin} bias to LONG or SHORT before opening"}), 400
    status = {}
    try:
        with open(HELLIOT_STATUS_FILE, "r") as f:
            status = json.load(f) or {}
    except Exception:
        pass
    pos = ((status.get("positions") or {}).get(coin) or {})
    if pos.get("position_side"):
        return jsonify({"ok": False, "error": f"{coin} already {pos.get('position_side')} — close first"}), 400
    payload = {
        "coin": coin, "side": side, "pct": pct,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    }
    try:
        cid = _sql_command("hummingbot-helliot", "open", payload)
        if not cid:
            return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
        payload["id"] = cid
        return jsonify({"ok": True, "message": f"Opening {side} {coin} at {int(pct*100)}% leftover", "open": payload})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/helliot/close", methods=["POST"])
def api_helliot_close():
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "").upper().strip()
    if coin and coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    target = coin or "ALL"
    try:
        body = {"coin": target} if target != "ALL" else {"all": True}
        cid = _sql_command("hummingbot-helliot", "close", body)
        if not cid:
            return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
        label = target if target != "ALL" else "BTC, ETH and LTC"
        return jsonify({"ok": True, "message": f"Force close signal sent for {label}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/surfn")
def api_surfn():
    running = get_container_running("hummingbot-surfn")
    bias = _hunter_bias_payload("hummingbot-surfn")
    status = {}
    for _ in range(3):
        try:
            with open(SURFN_STATUS_FILE, "r") as f:
                status = json.load(f)
            break
        except Exception:
            status = {}
    trades = []
    trade_summary = {}
    try:
        with open(SURFN_TRADES_FILE, "r") as f:
            log = json.load(f) or {}
        trades = list(log.get("trades") or [])[-80:]
        trades.reverse()
        trade_summary = {
            "realized_pnl_usd": log.get("realized_pnl_usd", 0.0),
            "n_wins": log.get("n_wins", 0),
            "n_losses": log.get("n_losses", 0),
            "n_opens": log.get("n_opens", 0),
            "n_closes": log.get("n_closes", 0),
        }
    except Exception:
        pass
    wallet = (status or {}).get("subaccount") or wallet_for("hummingbot-surfn", SURFN_WALLET)
    live = fetch_hl_account(wallet)
    spot = fetch_hl_spot_usdc(wallet)
    if not isinstance(status, dict):
        status = {}
    if live:
        if live.get("equity") is not None:
            status["equity_usd"] = float(live["equity"])
            status["perp_usd"] = float(live["equity"])
        if live.get("free") is not None:
            status["free_usd"] = float(live["free"])
    if spot is not None:
        status["spot_usd"] = float(spot)
    sl_roe = _sl_roe_for("hummingbot-surfn", 20.0)
    if isinstance(status, dict):
        status["sl_roe_pct"] = sl_roe
    return jsonify({
        "running": running,
        "bias": bias,
        "status": status,
        "trades": trades,
        "trade_summary": trade_summary,
        "coins": list(SWING_COINS),
        "subaccount": wallet,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "sl_roe_pct": sl_roe,
    })


@app.route("/api/surfn/bias", methods=["POST"])
def api_surfn_bias():
    data = request.get_json(silent=True) or {}
    bias = str(data.get("bias") or "").upper().strip()
    if bias not in ("LONG", "SHORT", "FLAT"):
        return jsonify({"ok": False, "error": "bias must be LONG, SHORT, or FLAT"}), 400
    coin = str(data.get("coin") or "").upper().strip()
    if coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    current = _hunter_bias_payload("hummingbot-surfn")
    current[coin] = bias
    payload = {c: current.get(c, "FLAT") for c in SWING_COINS}
    payload.update({
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    })
    try:
        _sql_bias_write("hummingbot-surfn", payload)
        with open(SURFN_BIAS_FILE, "w") as f:
            json.dump(payload, f, indent=2)
        return jsonify({"ok": True, "message": f"{coin} bias set to {bias}", "bias": payload})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/surfn/open", methods=["POST"])
def api_surfn_open():
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "").upper().strip()
    if coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    try:
        raw_pct = float(data.get("pct"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "pct must be 20, 50, or 100"}), 400
    if raw_pct > 1:
        raw_pct = raw_pct / 100.0
    pct = None
    for allowed in (0.2, 0.5, 1.0):
        if abs(raw_pct - allowed) < 1e-6:
            pct = allowed
            break
    if pct is None:
        return jsonify({"ok": False, "error": "pct must be 20, 50, or 100"}), 400
    side = _open_side(data, "hummingbot-surfn", coin)
    if side not in ("LONG", "SHORT"):
        return jsonify({"ok": False, "error": f"set {coin} bias to LONG or SHORT before opening"}), 400
    status = {}
    try:
        with open(SURFN_STATUS_FILE, "r") as f:
            status = json.load(f) or {}
    except Exception:
        pass
    pos = ((status.get("positions") or {}).get(coin) or {})
    already = str(pos.get("position_side") or "").upper()
    if already and already != side:
        return jsonify({"ok": False, "error": f"{coin} already {already} — will not flip"}), 400
    payload = {
        "coin": coin, "side": side, "pct": pct,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    }
    try:
        cid = _sql_command("hummingbot-surfn", "open", payload)
        if not cid:
            return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
        payload["id"] = cid
        return jsonify({"ok": True, "message": f"Opening {side} {coin} at {int(pct*100)}% leftover", "open": payload})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/surfn/close", methods=["POST"])
def api_surfn_close():
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "").upper().strip()
    if coin and coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    target = coin or "ALL"
    try:
        body = {"coin": target} if target != "ALL" else {"all": True}
        cid = _sql_command("hummingbot-surfn", "close", body)
        if not cid:
            return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
        label = target if target != "ALL" else "BTC, ETH and LTC"
        return jsonify({"ok": True, "message": f"Force close signal sent for {label}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/shorty")
@app.route("/api/shortn")
def api_shortn():
    running = get_container_running("hummingbot-shortn")
    bias = _hunter_bias_payload("hummingbot-shortn")
    status = {}
    for _ in range(3):
        try:
            with open(SHORTN_STATUS_FILE, "r") as f:
                status = json.load(f)
            break
        except Exception:
            status = {}
    trades = []
    trade_summary = {}
    try:
        with open(SHORTN_TRADES_FILE, "r") as f:
            log = json.load(f) or {}
        trades = list(log.get("trades") or [])[-80:]
        trades.reverse()
        trade_summary = {
            "realized_pnl_usd": log.get("realized_pnl_usd", 0.0),
            "n_wins": log.get("n_wins", 0),
            "n_losses": log.get("n_losses", 0),
            "n_opens": log.get("n_opens", 0),
            "n_closes": log.get("n_closes", 0),
        }
    except Exception:
        pass
    wallet = (status or {}).get("subaccount") or wallet_for("hummingbot-shortn", "")
    live = fetch_hl_account(wallet) if wallet else None
    spot = fetch_hl_spot_usdc(wallet) if wallet else None
    if not isinstance(status, dict):
        status = {}
    if live:
        if live.get("equity") is not None:
            status["equity_usd"] = float(live["equity"])
            status["perp_usd"] = float(live["equity"])
        if live.get("free") is not None:
            status["free_usd"] = float(live["free"])
    if spot is not None:
        status["spot_usd"] = float(spot)
    sl_roe = _sl_roe_for("hummingbot-shortn", 20.0)
    if isinstance(status, dict):
        status["sl_roe_pct"] = sl_roe
    return jsonify({
        "running": running,
        "bias": bias,
        "status": status,
        "trades": trades,
        "trade_summary": trade_summary,
        "coins": list(SWING_COINS),
        "subaccount": wallet or None,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "sl_roe_pct": sl_roe,
    })


@app.route("/api/shorty/bias", methods=["POST"])
@app.route("/api/shortn/bias", methods=["POST"])
def api_shortn_bias():
    data = request.get_json(silent=True) or {}
    bias = str(data.get("bias") or "").upper().strip()
    if bias not in ("LONG", "SHORT", "FLAT"):
        return jsonify({"ok": False, "error": "bias must be LONG, SHORT, or FLAT"}), 400
    coin = str(data.get("coin") or "").upper().strip()
    if coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    current = _hunter_bias_payload("hummingbot-shortn")
    current[coin] = bias
    payload = {c: current.get(c, "FLAT") for c in SWING_COINS}
    payload.update({
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    })
    try:
        _sql_bias_write("hummingbot-shortn", payload)
        with open(SHORTN_BIAS_FILE, "w") as f:
            json.dump(payload, f, indent=2)
        return jsonify({"ok": True, "message": f"{coin} bias set to {bias}", "bias": payload})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/shorty/open", methods=["POST"])
@app.route("/api/shortn/open", methods=["POST"])
def api_shortn_open():
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "").upper().strip()
    if coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    try:
        raw_pct = float(data.get("pct"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "pct must be 20, 50, or 100"}), 400
    if raw_pct > 1:
        raw_pct = raw_pct / 100.0
    pct = None
    for allowed in (0.2, 0.5, 1.0):
        if abs(raw_pct - allowed) < 1e-6:
            pct = allowed
            break
    if pct is None:
        return jsonify({"ok": False, "error": "pct must be 20, 50, or 100"}), 400
    side = _open_side(data, "hummingbot-shortn", coin)
    if side not in ("LONG", "SHORT"):
        return jsonify({"ok": False, "error": f"set {coin} bias to LONG or SHORT before opening"}), 400
    status = {}
    try:
        with open(SHORTN_STATUS_FILE, "r") as f:
            status = json.load(f) or {}
    except Exception:
        pass
    pos = ((status.get("positions") or {}).get(coin) or {})
    if pos.get("position_side"):
        return jsonify({"ok": False, "error": f"{coin} already {pos.get('position_side')} — close first"}), 400
    payload = {
        "coin": coin, "side": side, "pct": pct,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    }
    try:
        cid = _sql_command("hummingbot-shortn", "open", payload)
        if not cid:
            return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
        payload["id"] = cid
        return jsonify({"ok": True, "message": f"Opening {side} {coin} at {int(pct*100)}% leftover", "open": payload})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/shorty/close", methods=["POST"])
@app.route("/api/shortn/close", methods=["POST"])
def api_shortn_close():
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "").upper().strip()
    if coin and coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    target = coin or "ALL"
    try:
        body = {"coin": target} if target != "ALL" else {"all": True}
        cid = _sql_command("hummingbot-shortn", "close", body)
        if not cid:
            return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
        label = target if target != "ALL" else "BTC, ETH and LTC"
        return jsonify({"ok": True, "message": f"Force close signal sent for {label}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/dman")
def api_dman():
    running = get_container_running("hummingbot-dman")
    bias = _hunter_bias_payload("hummingbot-dman")
    status = {}
    for _ in range(3):
        try:
            with open(DMAN_STATUS_FILE, "r") as f:
                status = json.load(f)
            break
        except Exception:
            status = {}
    trades = []
    trade_summary = {}
    try:
        with open(DMAN_TRADES_FILE, "r") as f:
            log = json.load(f) or {}
        trades = list(log.get("trades") or [])[-80:]
        trades.reverse()
        trade_summary = {
            "realized_pnl_usd": log.get("realized_pnl_usd", 0.0),
            "n_wins": log.get("n_wins", 0),
            "n_losses": log.get("n_losses", 0),
            "n_opens": log.get("n_opens", 0),
            "n_closes": log.get("n_closes", 0),
        }
    except Exception:
        pass
    wallet = (status or {}).get("subaccount") or wallet_for("hummingbot-dman", "")
    live = fetch_hl_account(wallet) if wallet else None
    spot_d = fetch_hl_spot_detail(wallet) if wallet else None
    if not isinstance(status, dict):
        status = {}
    if live:
        if live.get("equity") is not None:
            status["equity_usd"] = float(live["equity"])
            status["perp_usd"] = float(live["equity"])
        if live.get("free") is not None:
            status["free_usd"] = float(live["free"])
    if spot_d:
        status["spot_usd"] = float(spot_d.get("total") or 0)
        status["spot_hold_usd"] = float(spot_d.get("hold") or 0)
        status["spot_free_usd"] = float(spot_d.get("free") or 0)
    hold_s = _dman_hold_s()
    if isinstance(status, dict):
        status["time_limit_s"] = hold_s
        status["time_limit_h"] = round(hold_s / 3600.0, 1)
    sl_roe = _sl_roe_for("hummingbot-dman", 20.0)
    if isinstance(status, dict):
        status["sl_roe_pct"] = sl_roe
    tune = _dman_tune_map(status)
    rebuy = _dman_rebuy_from_status(status, bias, hold_s, sl_roe, tune)
    return jsonify({
        "running": running,
        "bias": bias,
        "status": status,
        "trades": trades,
        "trade_summary": trade_summary,
        "coins": list(SWING_COINS),
        "subaccount": wallet or None,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "time_limit_s": hold_s,
        "time_limit_h": round(hold_s / 3600.0, 1),
        "sl_roe_pct": sl_roe,
        "rebuy": rebuy,
        "tune": tune,
    })


def _dman_tune_defaults():
    return {
        "bb_long_threshold": 0.0,
        "bb_short_threshold": 1.0,
        "trail_activation": 0.015,
        "trail_delta": 0.005,
    }


def _dman_tune_map(status=None):
    out = _dman_tune_defaults()
    if isinstance(status, dict):
        for k in out:
            if status.get(k) is not None:
                try:
                    out[k] = float(status.get(k))
                except (TypeError, ValueError):
                    pass
        raw_st = status.get("tune")
        if isinstance(raw_st, dict):
            for k in out:
                if raw_st.get(k) is not None:
                    try:
                        out[k] = float(raw_st.get(k))
                    except (TypeError, ValueError):
                        pass
    raw = _read_json_file(DMAN_TUNE_FILE)
    if isinstance(raw, dict):
        for k in out:
            if raw.get(k) is not None:
                try:
                    out[k] = float(raw.get(k))
                except (TypeError, ValueError):
                    pass
    sql = _sql_kv_map("hummingbot-dman")
    for k in out:
        if sql.get(k) is not None:
            try:
                out[k] = float(sql.get(k))
            except (TypeError, ValueError):
                pass
    out["bb_long_threshold"] = max(-0.2, min(1.0, out["bb_long_threshold"]))
    out["bb_short_threshold"] = max(0.0, min(1.2, out["bb_short_threshold"]))
    if out["trail_activation"] > 1:
        out["trail_activation"] /= 100.0
    if out["trail_delta"] > 1:
        out["trail_delta"] /= 100.0
    out["trail_activation"] = max(0.001, min(0.20, out["trail_activation"]))
    out["trail_delta"] = max(0.001, min(0.10, out["trail_delta"]))
    return out


def _dman_rebuy_from_status(status, bias, hold_s=None, sl_roe=None, tune=None):
    """Approx 15m BB rebuy + Trail/SL sell from DMan status. No HL API token."""
    rows = []
    positions = (status or {}).get("positions") or {}
    dca_all = (status or {}).get("dca") or {}
    lev_map = (status or {}).get("leverage") or {}
    if not isinstance(tune, dict):
        tune = _dman_tune_map(status)
    trail_act = float(tune.get("trail_activation") or 0.015)
    trail_delta = float(tune.get("trail_delta") or 0.005)
    bb_long = float(tune.get("bb_long_threshold") if tune.get("bb_long_threshold") is not None else 0.0)
    bb_short = float(tune.get("bb_short_threshold") if tune.get("bb_short_threshold") is not None else 1.0)
    if sl_roe is None:
        sl_roe = _sl_roe_for("hummingbot-dman", 20.0)
    if hold_s is None:
        hold_s = _dman_hold_s()
    now = time.time()
    for coin in ("BTC", "ETH", "LTC", "TAO"):
        pos = positions.get(coin) if isinstance(positions.get(coin), dict) else {}
        plan = dca_all.get(coin) if isinstance(dca_all.get(coin), dict) else {}
        b = str((bias or {}).get(coin) or pos.get("bias") or "FLAT").upper()
        mid = pos.get("mid_price")
        bbp = pos.get("bbp")
        bbb = pos.get("bbb")
        lower = upper = None
        try:
            mid_f = float(mid)
            width = (float(bbb) / 100.0) * mid_f
            if width > 0:
                lower = mid_f - float(bbp) * width
                upper = lower + width
        except (TypeError, ValueError):
            mid_f = None
        side = pos.get("position_side")
        trigger = None
        long_px = short_px = None
        if lower is not None and upper is not None:
            width = upper - lower
            long_px = lower + bb_long * width
            short_px = lower + bb_short * width
        if side:
            action = "in_pos"
        elif b == "LONG" and long_px is not None:
            action = "wait_long"
            trigger = round(long_px, 4)
        elif b == "SHORT" and short_px is not None:
            action = "wait_short"
            trigger = round(short_px, 4)
        else:
            action = "flat"
        gap = None
        gap_pct = None
        if trigger is not None and mid_f:
            gap = round(mid_f - trigger, 4)
            gap_pct = round((mid_f - trigger) / mid_f * 100.0, 2)
        try:
            lev = int(pos.get("leverage") or lev_map.get(coin) or 10)
        except (TypeError, ValueError):
            lev = 10
        if lev < 1:
            lev = 1
        entry_px = None
        try:
            if pos.get("entry_price") is not None:
                entry_px = float(pos.get("entry_price"))
        except (TypeError, ValueError):
            entry_px = None
        hypo_side = str(side or ("LONG" if action == "wait_long" else "SHORT" if action == "wait_short" else "")).upper()
        hypo_entry = entry_px if action == "in_pos" else (trigger if action in ("wait_long", "wait_short") else None)
        sl_px = trail_arm = trail_stop = None
        trail_live = bool(plan.get("trail_active") or pos.get("trail_active"))
        extreme = plan.get("trail_extreme") if plan.get("trail_extreme") is not None else pos.get("trail_extreme")
        try:
            extreme = float(extreme) if extreme is not None else None
        except (TypeError, ValueError):
            extreme = None
        if hypo_entry and hypo_side in ("LONG", "SHORT"):
            roe_frac = float(sl_roe) / 100.0 / float(lev)
            if hypo_side == "LONG":
                sl_px = hypo_entry * (1.0 - roe_frac)
                trail_arm = hypo_entry * (1.0 + trail_act)
                if trail_live and extreme:
                    trail_stop = extreme * (1.0 - trail_delta)
                else:
                    trail_stop = trail_arm * (1.0 - trail_delta)
            else:
                sl_px = hypo_entry * (1.0 + roe_frac)
                trail_arm = hypo_entry * (1.0 - trail_act)
                if trail_live and extreme:
                    trail_stop = extreme * (1.0 + trail_delta)
                else:
                    trail_stop = trail_arm * (1.0 + trail_delta)
        time_left_s = None
        started = plan.get("started")
        try:
            started_f = float(started) if started else None
        except (TypeError, ValueError):
            started_f = None
        if action == "in_pos" and started_f and hold_s:
            time_left_s = max(0, int(hold_s - (now - started_f)))
        rows.append({
            "coin": coin,
            "bias": b,
            "side": side,
            "mid": round(mid_f, 4) if mid_f else None,
            "lower": round(lower, 4) if lower is not None else None,
            "upper": round(upper, 4) if upper is not None else None,
            "bbp": round(float(bbp), 4) if bbp is not None else None,
            "action": action,
            "trigger": trigger,
            "gap": gap,
            "gap_pct": gap_pct,
            "leverage": lev,
            "entry": round(entry_px, 4) if entry_px else None,
            "hypo_entry": round(hypo_entry, 4) if hypo_entry else None,
            "sl_roe_pct": sl_roe,
            "sl_px": round(sl_px, 4) if sl_px else None,
            "trail_act_pct": round(trail_act * 100.0, 2),
            "trail_delta_pct": round(trail_delta * 100.0, 2),
            "trail_arm": round(trail_arm, 4) if trail_arm else None,
            "trail_stop": round(trail_stop, 4) if trail_stop else None,
            "trail_live": trail_live,
            "trail_extreme": round(extreme, 4) if extreme else None,
            "hold_s": int(hold_s or 0),
            "time_left_s": time_left_s,
            "bb_long_threshold": bb_long,
            "bb_short_threshold": bb_short,
            "long_px": round(long_px, 4) if long_px is not None else None,
            "short_px": round(short_px, 4) if short_px is not None else None,
        })
    return rows


def _dman_hold_s():
    sql = _sql_kv_map("hummingbot-dman")
    try:
        if sql.get("time_limit_s") is not None:
            v = int(sql.get("time_limit_s"))
            if v in (12 * 3600, 24 * 3600, 68 * 3600):
                return v
        if sql.get("time_limit_h") is not None:
            h = int(sql.get("time_limit_h"))
            if h in (12, 24, 68):
                return h * 3600
        if sql.get("hold_h") is not None:
            h = int(sql.get("hold_h"))
            if h in (12, 24, 68):
                return h * 3600
    except (TypeError, ValueError):
        pass
    row = (_read_json_file(BOT_SL_FILE) or {}).get("hummingbot-dman") or {}
    try:
        if row.get("time_limit_s") is not None:
            v = int(row.get("time_limit_s"))
            if v in (12 * 3600, 24 * 3600, 68 * 3600):
                return v
        if row.get("time_limit_h") is not None:
            h = int(row.get("time_limit_h"))
            if h in (12, 24, 68):
                return h * 3600
    except (TypeError, ValueError):
        pass
    return 12 * 3600


@app.route("/api/dman/tune", methods=["POST"])
def api_dman_tune():
    """Live DMan strategy %: BBP long/short thresholds and trail. No dollar target."""
    data = request.get_json(silent=True) or {}
    payload = _dman_tune_map()
    fields = {
        "bb_long_threshold": (-0.2, 1.0, False),
        "bb_short_threshold": (0.0, 1.2, False),
        "trail_activation": (0.001, 0.20, True),
        "trail_delta": (0.001, 0.10, True),
    }
    changed = []
    for key, (lo, hi, pctish) in fields.items():
        if data.get(key) is None:
            continue
        try:
            v = float(data.get(key))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": f"{key} must be a number"}), 400
        if pctish and v > 1:
            v = v / 100.0
        if not pctish and v > 1.2:
            v = v / 100.0
        v = max(lo, min(hi, v))
        payload[key] = round(v, 6)
        changed.append(key)
    if not changed:
        return jsonify({"ok": False, "error": "no tune fields"}), 400
    payload["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    tmp = DMAN_TUNE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, DMAN_TUNE_FILE)
    for key in changed:
        _sql_kv_set("hummingbot-dman", key, payload.get(key), "dman-tune")
    long_th = payload["bb_long_threshold"]
    msg = (
        f"DMan long BBP<{long_th:.2f} · short BBP>{payload['bb_short_threshold']:.2f} · "
        f"trail {payload['trail_activation']*100:.2f}/{payload['trail_delta']*100:.2f}%"
    )
    return jsonify({"ok": True, "tune": payload, "message": msg})


@app.route("/api/dman/hold", methods=["POST"])
def api_dman_hold():
    """Set DMan clip hold: 12h, 24h, or 68h. Live file — no restart."""
    data = request.get_json(silent=True) or {}
    try:
        hours = int(data.get("hours"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "hours must be 12, 24, or 68"}), 400
    if hours not in (12, 24, 68):
        return jsonify({"ok": False, "error": "hours must be 12, 24, or 68"}), 400
    payload = _read_json_file(BOT_SL_FILE)
    if not isinstance(payload, dict):
        payload = {}
    row = payload.get("hummingbot-dman") if isinstance(payload.get("hummingbot-dman"), dict) else {}
    row["time_limit_h"] = hours
    row["time_limit_s"] = hours * 3600
    row["updated"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    payload["hummingbot-dman"] = row
    tmp = BOT_SL_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, BOT_SL_FILE)
    _sql_kv_set("hummingbot-dman", "hold_h", hours, "dman-hold")
    _sql_kv_set("hummingbot-dman", "time_limit_h", hours, "dman-hold")
    _sql_kv_set("hummingbot-dman", "time_limit_s", hours * 3600, "dman-hold")
    return jsonify({
        "ok": True,
        "hours": hours,
        "time_limit_s": hours * 3600,
        "message": f"Dman hold set to {hours}h (applies to the current clip clock)",
    })


@app.route("/api/dman_maker")
def api_dman_maker():
    running = get_container_running("hummingbot-dman-maker")
    bias = _hunter_bias_payload("hummingbot-dman-maker")
    status = {}
    for _ in range(3):
        try:
            with open(DMAN_MAKER_STATUS_FILE, "r") as f:
                status = json.load(f)
            break
        except Exception:
            status = {}
    trades = []
    trade_summary = {}
    try:
        with open(DMAN_MAKER_TRADES_FILE, "r") as f:
            log = json.load(f) or {}
        trades = list(log.get("trades") or [])[-80:]
        trades.reverse()
        trade_summary = {
            "realized_pnl_usd": log.get("realized_pnl_usd", 0.0),
            "n_wins": log.get("n_wins", 0),
            "n_losses": log.get("n_losses", 0),
            "n_opens": log.get("n_opens", 0),
            "n_closes": log.get("n_closes", 0),
        }
    except Exception:
        pass
    wallet = (status or {}).get("subaccount") or wallet_for("hummingbot-dman-maker", "")
    live = fetch_hl_account(wallet) if wallet else None
    spot_d = fetch_hl_spot_detail(wallet) if wallet else None
    if not isinstance(status, dict):
        status = {}
    if live:
        if live.get("equity") is not None:
            status["equity_usd"] = float(live["equity"])
            status["perp_usd"] = float(live["equity"])
        if live.get("free") is not None:
            status["free_usd"] = float(live["free"])
    if spot_d:
        status["spot_usd"] = float(spot_d.get("total") or 0)
        status["spot_hold_usd"] = float(spot_d.get("hold") or 0)
        status["spot_free_usd"] = float(spot_d.get("free") or 0)
    sl_roe = _sl_roe_for("hummingbot-dman-maker", 20.0)
    if isinstance(status, dict):
        status["sl_roe_pct"] = sl_roe
    if live is not None:
        live_map = {}
        for p in live.get("positions") or []:
            c = str(p.get("coin") or "").upper()
            if c:
                live_map[c] = p
        pos_out = dict(status.get("positions") or {})
        for coin in SWING_COINS:
            row = dict(pos_out.get(coin) or {})
            lp = live_map.get(coin)
            size = abs(float(lp.get("size") or 0)) if lp else 0.0
            if not lp or size < 1e-9:
                row["position_side"] = None
                row["amount"] = None
                row["entry_price"] = None
                row["roe_pct"] = None
            else:
                row["position_side"] = str(lp.get("side") or "LONG").upper()
                row["amount"] = size
                if lp.get("entry_px"):
                    row["entry_price"] = lp.get("entry_px")
                if lp.get("leverage"):
                    row["leverage"] = lp.get("leverage")
                if lp.get("unrealized_pnl") is not None:
                    row["unrealized_pnl"] = lp.get("unrealized_pnl")
            pos_out[coin] = row
        status["positions"] = pos_out
        upnl = 0.0
        for row in pos_out.values():
            if isinstance(row, dict) and row.get("unrealized_pnl") is not None:
                try:
                    upnl += float(row.get("unrealized_pnl"))
                except (TypeError, ValueError):
                    pass
        if any(isinstance(row, dict) and row.get("unrealized_pnl") is not None for row in pos_out.values()):
            status["open_upnl_usd"] = round(upnl, 4)
    return jsonify({
        "running": running,
        "bias": bias,
        "status": status,
        "trades": trades,
        "trade_summary": trade_summary,
        "coins": list(SWING_COINS),
        "subaccount": wallet or None,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "sl_roe_pct": sl_roe,
    })


@app.route("/api/dman_maker/bias", methods=["POST"])
def api_dman_maker_bias():
    data = request.get_json(silent=True) or {}
    bias = str(data.get("bias") or "").upper().strip()
    if bias not in SWING_BIAS_VALUES:
        return jsonify({"ok": False, "error": "bias must be LONG, SHORT, or FLAT"}), 400
    coin = str(data.get("coin") or "").upper().strip()
    if coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    current = _hunter_bias_payload("hummingbot-dman-maker")
    current[coin] = bias
    payload = {c: current.get(c, "FLAT") for c in SWING_COINS}
    payload.update({
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    })
    try:
        _sql_bias_write("hummingbot-dman-maker", payload)
        with open(DMAN_MAKER_BIAS_FILE, "w") as f:
            json.dump(payload, f, indent=2)
        return jsonify({"ok": True, "message": f"{coin} bias set to {bias}", "bias": payload})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/dman_maker/open", methods=["POST"])
def api_dman_maker_open():
    """Market-open leftover USD on current BIAS (LONG or SHORT). Does not flip an open clip."""
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "").upper().strip()
    if coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, LTC, or TAO"}), 400
    try:
        raw_pct = float(data.get("pct") if data.get("pct") is not None else 1.0)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "pct must be 20, 50, or 100"}), 400
    if raw_pct > 1:
        raw_pct = raw_pct / 100.0
    pct = None
    for allowed in (0.2, 0.5, 1.0):
        if abs(raw_pct - allowed) < 1e-6:
            pct = allowed
            break
    if pct is None:
        return jsonify({"ok": False, "error": "pct must be 20, 50, or 100"}), 400
    side = _open_side(data, "hummingbot-dman-maker", coin)
    if side not in ("LONG", "SHORT"):
        return jsonify({"ok": False, "error": f"set {coin} bias to LONG or SHORT before opening"}), 400
    status = {}
    try:
        with open(DMAN_MAKER_STATUS_FILE, "r") as f:
            status = json.load(f) or {}
    except Exception:
        pass
    pos = ((status.get("positions") or {}).get(coin) or {})
    if pos.get("position_side"):
        return jsonify({"ok": False, "error": f"{coin} already {pos.get('position_side')} — close first"}), 400
    payload = {
        "coin": coin, "side": side, "pct": pct,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    }
    cid = _sql_command("hummingbot-dman-maker", "open", payload)
    if not cid:
        return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
    payload["id"] = cid
    return jsonify({
        "ok": True,
        "queued": True,
        "message": f"Opening {side} {coin} at {int(pct*100)}% leftover",
        "open": payload,
    })


@app.route("/api/dman_maker/close", methods=["POST"])
def api_dman_maker_close():
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "").upper().strip()
    if coin and coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    target = coin or "ALL"
    body = {"coin": coin} if coin else {"all": True}
    cid = _sql_command("hummingbot-dman-maker", "close", body)
    if not cid:
        return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
    label = target if target != "ALL" else "BTC, ETH and LTC"
    return jsonify({"ok": True, "message": f"Force close signal sent for {label}"})


@app.route("/api/dman_maker/scale", methods=["POST"])
def api_dman_maker_scale():
    """Add all leftover perp USD to an open DMan Maker LONG. Does not close."""
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "").upper().strip()
    if coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    cid = _sql_command("hummingbot-dman-maker", "scale", {
        "coin": coin,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    })
    if not cid:
        return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
    return jsonify({
        "ok": True,
        "closes": False,
        "message": f"Full scale {coin}: all leftover perp USD → add size",
    })


@app.route("/api/dman/bias", methods=["POST"])
def api_dman_bias():
    data = request.get_json(silent=True) or {}
    bias = str(data.get("bias") or "").upper().strip()
    if bias not in SWING_BIAS_VALUES:
        return jsonify({"ok": False, "error": "bias must be LONG, SHORT, or FLAT"}), 400
    coin = str(data.get("coin") or "").upper().strip()
    if coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    current = _hunter_bias_payload("hummingbot-dman")
    current[coin] = bias
    payload = {c: current.get(c, "FLAT") for c in SWING_COINS}
    payload.update({
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    })
    try:
        _sql_bias_write("hummingbot-dman", payload)
        with open(DMAN_BIAS_FILE, "w") as f:
            json.dump(payload, f, indent=2)
        return jsonify({"ok": True, "message": f"{coin} bias set to {bias}", "bias": payload})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/dman/open", methods=["POST"])
def api_dman_open():
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "").upper().strip()
    if coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    try:
        raw_pct = float(data.get("pct"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "pct must be 20, 50, or 100"}), 400
    if raw_pct > 1:
        raw_pct = raw_pct / 100.0
    pct = None
    for allowed in (0.2, 0.5, 1.0):
        if abs(raw_pct - allowed) < 1e-6:
            pct = allowed
            break
    if pct is None:
        return jsonify({"ok": False, "error": "pct must be 20, 50, or 100"}), 400
    side = _open_side(data, "hummingbot-dman", coin)
    if side not in ("LONG", "SHORT"):
        return jsonify({"ok": False, "error": f"set {coin} bias to LONG or SHORT before opening"}), 400
    status = {}
    try:
        with open(DMAN_STATUS_FILE, "r") as f:
            status = json.load(f) or {}
    except Exception:
        pass
    if not status.get("wallet_ok"):
        return jsonify({"ok": False, "error": "set a unique Dman subaccount in Confiig first"}), 400
    pos = ((status.get("positions") or {}).get(coin) or {})
    if pos.get("position_side"):
        return jsonify({"ok": False, "error": f"{coin} already {pos.get('position_side')} — close first"}), 400
    payload = {
        "coin": coin, "side": side, "pct": pct,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    }
    try:
        cid = _sql_command("hummingbot-dman", "open", payload)
        if not cid:
            return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
        payload["id"] = cid
        return jsonify({"ok": True, "message": f"Opening {side} {coin} at {int(pct*100)}% leftover (one clip)", "open": payload})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/dman/close", methods=["POST"])
def api_dman_close():
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "").upper().strip()
    if coin and coin not in SWING_COINS:
        return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
    target = coin or "ALL"
    try:
        body = {"coin": target} if target != "ALL" else {"all": True}
        cid = _sql_command("hummingbot-dman", "close", body)
        if not cid:
            return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
        label = target if target != "ALL" else "BTC, ETH and LTC"
        return jsonify({"ok": True, "message": f"Force close signal sent for {label}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/onlyup")
def api_onlyup():
    running = get_container_running("hummingbot-onlyup")
    bias = _hunter_bias_payload("hummingbot-onlyup", coins=ONLYUP_COINS)
    status = {}
    for _ in range(3):
        try:
            with open(ONLYUP_STATUS_FILE, "r") as f:
                status = json.load(f)
            break
        except Exception:
            status = {}
    trades = []
    trade_summary = {}
    try:
        with open(ONLYUP_TRADES_FILE, "r") as f:
            log = json.load(f) or {}
        trades = list(log.get("trades") or [])[-80:]
        trades.reverse()
        trade_summary = {
            "realized_pnl_usd": log.get("realized_pnl_usd", 0.0),
            "n_wins": log.get("n_wins", 0),
            "n_losses": log.get("n_losses", 0),
            "n_opens": log.get("n_opens", 0),
            "n_closes": log.get("n_closes", 0),
        }
    except Exception:
        pass
    wallet = (status or {}).get("subaccount") or wallet_for("hummingbot-onlyup", "")
    live = fetch_hl_account(wallet) if wallet else None
    spot_d = fetch_hl_spot_detail(wallet) if wallet else None
    if not isinstance(status, dict):
        status = {}
    if live:
        if live.get("equity") is not None:
            status["equity_usd"] = float(live["equity"])
            status["perp_usd"] = float(live["equity"])
        if live.get("free") is not None:
            status["free_usd"] = float(live["free"])
    if spot_d:
        status["spot_usd"] = float(spot_d.get("total") or 0)
        status["spot_hold_usd"] = float(spot_d.get("hold") or 0)
        status["spot_free_usd"] = float(spot_d.get("free") or 0)
    sl_roe = _sl_roe_for("hummingbot-onlyup", 20.0)
    if isinstance(status, dict):
        status["sl_roe_pct"] = sl_roe
    if live is not None:
        live_map = {}
        for p in live.get("positions") or []:
            c = str(p.get("coin") or "").upper()
            if c:
                live_map[c] = p
        pos_out = dict(status.get("positions") or {})
        for coin in ONLYUP_COINS:
            row = dict(pos_out.get(coin) or {})
            lp = live_map.get(coin)
            size = abs(float(lp.get("size") or 0)) if lp else 0.0
            if not lp or size < 1e-9:
                row["position_side"] = None
                row["amount"] = None
                row["entry_price"] = None
                row["roe_pct"] = None
            else:
                row["position_side"] = str(lp.get("side") or "LONG").upper()
                row["amount"] = size
                if lp.get("entry_px"):
                    row["entry_price"] = lp.get("entry_px")
                if lp.get("leverage"):
                    row["leverage"] = lp.get("leverage")
            pos_out[coin] = row
        status["positions"] = pos_out
    return jsonify({
        "running": running,
        "bias": bias,
        "status": status,
        "trades": trades,
        "trade_summary": trade_summary,
        "coins": list(ONLYUP_COINS),
        "subaccount": wallet or None,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "sl_roe_pct": sl_roe,
        "moon": _load_onlyup_moon(),
    })


@app.route("/api/onlyup/bias", methods=["POST"])
def api_onlyup_bias():
    data = request.get_json(silent=True) or {}
    bias = str(data.get("bias") or "").upper().strip()
    if bias not in ("LONG", "SHORT", "FLAT"):
        return jsonify({"ok": False, "error": "bias must be LONG, SHORT, or FLAT"}), 400
    coin = str(data.get("coin") or "BTC").upper().strip()
    if coin not in ONLYUP_COINS:
        return jsonify({"ok": False, "error": "Only Up trades BTC or LTC"}), 400
    payload = _hunter_bias_payload("hummingbot-onlyup", coins=ONLYUP_COINS)
    payload[coin] = bias
    payload.update({
        "updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    })
    try:
        _sql_bias_write("hummingbot-onlyup", payload)
        with open(ONLYUP_BIAS_FILE, "w") as f:
            json.dump(payload, f, indent=2)
        return jsonify({"ok": True, "message": f"{coin} bias set to {bias}", "bias": payload})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/onlyup/open", methods=["POST"])
def api_onlyup_open():
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "BTC").upper().strip()
    if coin not in ONLYUP_COINS:
        return jsonify({"ok": False, "error": "Only Up trades BTC or LTC"}), 400
    try:
        raw_pct = float(data.get("pct"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "pct must be 20, 50, or 100"}), 400
    if raw_pct > 1:
        raw_pct = raw_pct / 100.0
    pct = None
    for allowed in (0.2, 0.5, 1.0):
        if abs(raw_pct - allowed) < 1e-6:
            pct = allowed
            break
    if pct is None:
        return jsonify({"ok": False, "error": "pct must be 20, 50, or 100"}), 400
    side = _open_side(data, "hummingbot-onlyup", coin, coins=ONLYUP_COINS)
    if side not in ("LONG", "SHORT"):
        return jsonify({"ok": False, "error": f"set {coin} bias to LONG or SHORT before opening"}), 400
    status = {}
    try:
        with open(ONLYUP_STATUS_FILE, "r") as f:
            status = json.load(f) or {}
    except Exception:
        pass
    if status.get("wallet_ok") is False:
        return jsonify({"ok": False, "error": "set a unique Only Up subaccount in Confiig first"}), 400
    pos = ((status.get("positions") or {}).get(coin) or {})
    already = str(pos.get("position_side") or "").upper()
    if already:
        return jsonify({"ok": False, "error": f"{coin} already {already} — close first"}), 400
    payload = {
        "coin": coin, "side": side, "pct": pct,
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": "dashboard",
    }
    cid = _sql_command("hummingbot-onlyup", "open", payload)
    if not cid:
        return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
    payload["id"] = cid
    return jsonify({
        "ok": True,
        "message": f"Opening {side} {coin} at {int(pct*100)}% leftover",
        "open": payload,
    })


@app.route("/api/onlyup/moon", methods=["POST"])
def api_onlyup_moon():
    """Hold for a moon: auto-sell only when the coin's own price is +90%."""
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "").upper().strip()
    if coin not in ONLYUP_COINS:
        return jsonify({"ok": False, "error": "Moon is per coin — BTC or LTC"}), 400
    on = data.get("on")
    if isinstance(on, str):
        on = on.strip().lower() in ("1", "true", "on", "yes")
    on = bool(on)
    try:
        pct = float(data.get("coin_pct") if data.get("coin_pct") is not None else ONLYUP_MOON_PCT)
    except (TypeError, ValueError):
        pct = ONLYUP_MOON_PCT
    pct = max(1.0, min(500.0, pct))
    current = _load_onlyup_moon()
    row = dict(current.get(coin) or {})
    if not on:
        row["on"] = False
        current[coin] = row
        saved = _save_onlyup_moon(current)
        return jsonify({
            "ok": True,
            "message": f"{coin} moon OFF — back to ROE SL / clip / Supertrend",
            "moon": saved,
        })
    status = {}
    try:
        with open(ONLYUP_STATUS_FILE, "r") as f:
            status = json.load(f) or {}
    except Exception:
        pass
    pos = ((status.get("positions") or {}).get(coin) or {})
    side = str(pos.get("position_side") or "").upper()
    anchor = None
    for key in ("mid_price", "entry_price", "entry_px"):
        try:
            v = float(pos.get(key) or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            anchor = v
            break
    if data.get("anchor_px") is not None:
        try:
            a = float(data.get("anchor_px"))
            if a > 0:
                anchor = a
        except (TypeError, ValueError):
            pass
    if side == "SHORT" and anchor:
        target = anchor * (1.0 - pct / 100.0)
        if target <= 0:
            target = anchor * 0.01
    else:
        target = (anchor * (1.0 + pct / 100.0)) if anchor else None
    row = {
        "on": True,
        "coin_pct": pct,
        "anchor_px": anchor,
        "target_px": target,
        "armed": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    }
    current[coin] = row
    saved = _save_onlyup_moon(current)
    verb = "cover" if side == "SHORT" else "sell"
    move = f"-{pct:.0f}%" if side == "SHORT" else f"+{pct:.0f}%"
    if target:
        msg = (
            f"{coin} MOON ON — hold, {verb} only when {coin} itself is {move} "
            f"from ${anchor:.4f} → ${target:.4f} (ROE SL/clip/ST off)"
        )
    else:
        msg = (
            f"{coin} MOON ON — hold, {verb} only when {coin} itself is {move} "
            f"(price fills when the bot sees a mark)"
        )
    return jsonify({"ok": True, "message": msg, "moon": saved})


@app.route("/api/onlyup/close", methods=["POST"])
def api_onlyup_close():
    data = request.get_json(silent=True) or {}
    coin = str(data.get("coin") or "ALL").upper().strip()
    if coin == "ALL":
        target = "ALL"
    elif coin in ONLYUP_COINS:
        target = coin
    else:
        return jsonify({"ok": False, "error": "Only Up trades BTC or LTC"}), 400
    body = {"coin": target} if target != "ALL" else {"all": True}
    cid = _sql_command("hummingbot-onlyup", "close", body)
    if not cid:
        return jsonify({"ok": False, "error": "sqlite command queue failed"}), 500
    label = target if target != "ALL" else "BTC and LTC"
    return jsonify({"ok": True, "message": f"Force close signal sent for {label}"})


@app.route("/api/confiig")
def api_confiig():
    stored = load_bot_accounts()
    out = []
    for bot in stored.get("bots") or []:
        yml = bot.get("yml") or ""
        addr = str(bot.get("address") or "").strip()
        use_vault = bool(bot.get("use_vault"))
        yml_vault = _yml_use_vault(yml) if yml else None
        if yml_vault is not None:
            use_vault = yml_vault
        live = fetch_hl_account(addr) if _valid_hl_address(addr) else None
        spot = fetch_hl_spot_usdc(addr) if _valid_hl_address(addr) else None
        row = {
            "id": bot.get("id"),
            "name": bot.get("name"),
            "container": bot.get("container") or bot.get("id"),
            "running": get_container_running(bot.get("container") or bot.get("id")),
            "address": addr,
            "use_vault": use_vault,
            "yml": yml,
            "yml_exists": bool(yml and os.path.isfile(yml)),
            "notes": bot.get("notes") or "",
            "equity_usd": (live or {}).get("equity") if live else None,
            "free_usd": (live or {}).get("free") if live else None,
            "spot_usd": spot,
            "n_pos": len((live or {}).get("positions") or []) if live else None,
        }
        out.append(row)
    return jsonify({
        "bots": out,
        "updated": stored.get("updated"),
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


@app.route("/api/confiig/save", methods=["POST"])
def api_confiig_save():
    data = request.get_json(silent=True) or {}
    bot_id = str(data.get("id") or "").strip()
    out = _confiig_set_address(
        bot_id,
        data.get("address"),
        use_vault=bool(data.get("use_vault")),
        restart=bool(data.get("restart")),
    )
    if not out.get("ok"):
        code = 404 if "unknown bot" in (out.get("error") or "") else 400
        return jsonify(out), code
    return jsonify(out)


@app.route("/api/confiig/restart", methods=["POST"])
def api_confiig_restart():
    data = request.get_json(silent=True) or {}
    bot_id = str(data.get("id") or "").strip()
    stored = load_bot_accounts()
    target = None
    for bot in stored.get("bots") or []:
        if bot.get("id") == bot_id:
            target = bot
            break
    if not target:
        return jsonify({"ok": False, "error": f"unknown bot {bot_id}"}), 404
    name = target.get("container") or bot_id
    try:
        _restart_container(name)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({
        "ok": True,
        "message": f"Restarted {target.get('name')} ({name})",
        "container": name,
    })


def _docker_inspect(name):
    try:
        r = subprocess.run(
            ["docker", "inspect", name],
            capture_output=True, text=True, timeout=8,
        )
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout or "[]")
        return data[0] if data else None
    except Exception:
        return None


def _docker_exec_out(name, args, timeout=8):
    try:
        r = subprocess.run(
            ["docker", "exec", name] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0:
            return None
        return (r.stdout or "").strip()
    except Exception:
        return None


def _short_digest(d):
    s = str(d or "")
    if s.startswith("sha256:"):
        s = s[7:]
    return (s[:12] + "…") if len(s) > 12 else s


def _fetch_github_latest(repo):
    def _load():
        try:
            r = requests.get(
                f"https://api.github.com/repos/{repo}/releases/latest",
                timeout=10,
                headers={"Accept": "application/vnd.github+json", "User-Agent": "hummingbot-dashboard"},
            )
            d = r.json() or {}
            return {
                "ok": r.status_code == 200,
                "tag": d.get("tag_name"),
                "name": d.get("name"),
                "published_at": d.get("published_at"),
                "html_url": d.get("html_url"),
                "body": (d.get("body") or "").strip().split("\n")[0][:240],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return _ttl_get(f"gh:{repo}", 300, _load)


def _fetch_dockerhub_tag(image, tag="latest"):
    def _load():
        try:
            r = requests.get(
                f"https://hub.docker.com/v2/repositories/{image}/tags/{tag}",
                timeout=10,
            )
            d = r.json() or {}
            digest = d.get("digest")
            arch_digest = None
            for img in d.get("images") or []:
                if img.get("architecture") in ("amd64", "arm64"):
                    arch_digest = img.get("digest")
                    if img.get("architecture") == "amd64":
                        break
            return {
                "ok": r.status_code == 200,
                "name": d.get("name") or tag,
                "last_updated": d.get("last_updated"),
                "digest": digest,
                "arch_digest": arch_digest,
                "full_size": d.get("full_size"),
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return _ttl_get(f"hub:{image}:{tag}", 300, _load)


BOT_PROTOCOL = {
    "hummingbot-hl": {
        "role": "Copy trader",
        "script": "hl_copy_trader.py",
        "protocol": "Hyperliquid Perps REST + WS",
        "venue": "api.hyperliquid.xyz  /  wss://api.hyperliquid.xyz/ws",
        "pairs": "dynamic (seed BTC/ETH/HYPE/SOL, then leaders)",
        "notes": "Mirrors 3 leader wallets. Connector overlay: HIP-3 off, 429 retries, tick while not ready.",
    },
    "hummingbot-bn-swing": {
        "role": "PMM Simple",
        "script": "pmm_simple_trader.py",
        "protocol": "Hyperliquid Perps",
        "venue": "api.hyperliquid.xyz",
        "pairs": "BTC-USD · ETH-USD · LTC-USD",
        "notes": "Official pmm_simple, up-only. Rest bids at buy_spreads. TP/trail/Home SL. Never shorts.",
    },
    "hummingbot-helliot": {
        "role": "Elliott structure",
        "script": "helliot_trader.py",
        "protocol": "Hyperliquid Perps",
        "venue": "api.hyperliquid.xyz",
        "pairs": "BTC-USD · ETH-USD · LTC-USD",
        "notes": "15m pivots W2 zone, W3 1.618 target, ROE 93/20.",
    },
    "hummingbot-surfn": {
        "role": "Long-only rider",
        "script": "surfn_trader.py",
        "protocol": "Hyperliquid Perps",
        "venue": "api.hyperliquid.xyz",
        "pairs": "BTC-USD · ETH-USD · LTC-USD",
        "notes": "1h SMA-50 + 15m EMA-21. Clip 50% at +90% ROE, trail runner.",
    },
    "hummingbot-shortn": {
        "role": "Short-only Bollinger",
        "script": "shortn_trader.py",
        "protocol": "Hyperliquid Perps",
        "venue": "api.hyperliquid.xyz",
        "pairs": "BTC-USD · ETH-USD · LTC-USD",
        "notes": "BBP≥0.75 + 1h not up. TP +90% ROE, wait +20% ROE bounce, short again. SL −20%.",
    },
    "hummingbot-dman": {
        "role": "DMan V3 DCA",
        "script": "dman_trader.py",
        "protocol": "Hyperliquid Perps",
        "venue": "api.hyperliquid.xyz",
        "pairs": "BTC-USD · ETH-USD · LTC-USD",
        "notes": "15m BB outside-band signal, 4-level DCA, trail 1.5/0.5%, SL 3% price.",
    },
    "hummingbot-onlyup": {
        "role": "BTC + LTC only-up Supertrend",
        "script": "onlyup_trader.py",
        "protocol": "Hyperliquid Perps",
        "venue": "api.hyperliquid.xyz",
        "pairs": "BTC-USD · LTC-USD",
        "notes": "Long-only BTC and LTC. 1h SMA-50 up + 15m Supertrend near the line. Clip 50% at +90% ROE, trail runner, flip down exits. Never shorts.",
    },
    "hummingbot-sol": {
        "role": "SOL scalper",
        "script": "sol_micro_trader.py",
        "protocol": "Jupiter (via Gateway)",
        "venue": "Solana / Jupiter",
        "pairs": "SOL-USDC",
        "notes": "Compose service exists; not the live HL stack.",
    },
    "hummingbot": {
        "role": "AGENC trader",
        "script": "agenc_sol_trader.py",
        "protocol": "Meteora (via Gateway)",
        "venue": "Solana / Meteora",
        "pairs": "AGENC-SOL",
        "notes": "Compose service exists; not the live HL stack.",
    },
}

OVERLAYS = [
    {
        "file": "hyperliquid_perpetual_derivative.py",
        "why": "HIP-3 xyz off by default, funding-info retry on 429",
        "bots": ["hummingbot-hl", "hummingbot-bn-swing", "hummingbot-helliot", "hummingbot-surfn", "hummingbot-shortn", "hummingbot-dman", "hummingbot-onlyup"],
    },
    {
        "file": "hyperliquid_perpetual_api_order_book_data_source.py",
        "why": "l2Book snapshot retry on HTTP 429",
        "bots": ["hummingbot-hl", "hummingbot-surfn", "hummingbot-shortn", "hummingbot-dman", "hummingbot-onlyup"],
    },
    {
        "file": "order_book_tracker.py",
        "why": "order-book init retries so connector.ready is not stuck",
        "bots": ["hummingbot-hl", "hummingbot-surfn", "hummingbot-shortn", "hummingbot-dman", "hummingbot-onlyup"],
    },
    {
        "file": "strategy_v2_base.py",
        "why": "on_tick still runs while connector is not ready (copy status)",
        "bots": ["hummingbot-hl", "hummingbot-surfn", "hummingbot-shortn", "hummingbot-dman", "hummingbot-onlyup"],
    },
]


@app.route("/api/update")
@app.route("/api/versions")
def api_update():
    gh = _fetch_github_latest("hummingbot/hummingbot")
    gh_gw = _fetch_github_latest("hummingbot/gateway")
    hub = _fetch_dockerhub_tag("hummingbot/hummingbot", "latest")
    hub_gw = _fetch_dockerhub_tag("hummingbot/gateway", "latest")

    online_tag = (gh.get("tag") or "").lstrip("v")
    online_digest = hub.get("digest") or ""

    containers = []
    local_versions = set()
    local_digests = set()
    for bot_id, meta in BOTS.items():
        insp = _docker_inspect(bot_id)
        running = bool(insp and (insp.get("State") or {}).get("Running"))
        cfg = (insp or {}).get("Config") or {}
        image = cfg.get("Image") or meta.get("label")
        image_id = (insp or {}).get("Image") or ""
        created = (insp or {}).get("Created")
        repo_digests = (insp or {}).get("RepoDigests") or []
        digest = ""
        if repo_digests:
            digest = str(repo_digests[0]).split("@")[-1]
        elif image_id:
            digest = image_id
        if digest:
            local_digests.add(digest)
        ver = None
        pyver = None
        if running:
            ver = _docker_exec_out(bot_id, ["cat", "/home/hummingbot/hummingbot/VERSION"])
            pyver = _docker_exec_out(
                bot_id,
                ["python3", "-c", "import sys; print(sys.version.split()[0])"],
            )
            if ver:
                local_versions.add(ver.split()[0])
        proto = BOT_PROTOCOL.get(bot_id) or {}
        acct = None
        for b in load_bot_accounts().get("bots") or []:
            if b.get("id") == bot_id:
                acct = b.get("address")
                break
        match_release = bool(ver and online_tag and ver.split()[0] == online_tag)
        match_digest = bool(digest and online_digest and digest == online_digest)
        if digest and online_digest:
            status = "match" if match_digest else "behind"
        elif ver and online_tag:
            status = "match" if match_release else "unknown"
        else:
            status = "offline" if not running else "unknown"
        containers.append({
            "id": bot_id,
            "name": meta.get("name"),
            "running": running,
            "image": image,
            "created": created,
            "digest": digest,
            "digest_short": _short_digest(digest),
            "hummingbot_version": ver,
            "python": pyver,
            "script": proto.get("script"),
            "role": proto.get("role"),
            "protocol": proto.get("protocol"),
            "venue": proto.get("venue"),
            "pairs": proto.get("pairs"),
            "notes": proto.get("notes"),
            "wallet": acct,
            "match_release": match_release,
            "match_digest": match_digest,
            "status": status,
        })

    gw = _docker_inspect("gateway")
    gateway = None
    if gw:
        digest = ""
        repos = gw.get("RepoDigests") or []
        if repos:
            digest = str(repos[0]).split("@")[-1]
        gateway = {
            "running": bool((gw.get("State") or {}).get("Running")),
            "image": ((gw.get("Config") or {}).get("Image")),
            "digest": digest,
            "digest_short": _short_digest(digest),
            "created": gw.get("Created"),
        }

    local_ver = next(iter(sorted(local_versions)), None)
    local_dig = next(iter(local_digests), None)
    compare = {
        "local_version": local_ver,
        "online_version": online_tag or None,
        "version_match": bool(local_ver and online_tag and local_ver == online_tag),
        "local_digest": local_dig,
        "online_digest": online_digest or None,
        "digest_match": bool(local_dig and online_digest and local_dig == online_digest),
    }

    return jsonify({
        "online": {
            "github": gh,
            "github_gateway": gh_gw,
            "dockerhub": hub,
            "dockerhub_gateway": hub_gw,
            "release_notes": "https://hummingbot.org/release-notes/",
            "github_url": "https://github.com/hummingbot/hummingbot/releases/latest",
            "hub_url": "https://hub.docker.com/r/hummingbot/hummingbot/tags",
        },
        "compare": compare,
        "containers": containers,
        "gateway": gateway,
        "overlays": OVERLAYS,
        "protocols": [
            {
                "name": "Hyperliquid Perps",
                "rest": "https://api.hyperliquid.xyz/info",
                "ws": "wss://api.hyperliquid.xyz/ws",
                "connector": "hyperliquid_perpetual",
                "used_by": ["HL Copy", "PMM Simple", "Helliot", "Surfn", "Shorty", "Dman", "Only Up"],
            },
            {
                "name": "Jupiter (Solana)",
                "connector": "gateway / jupiter",
                "used_by": ["SOL scalper"],
            },
            {
                "name": "Meteora (Solana)",
                "connector": "gateway / meteora",
                "used_by": ["AGENC trader"],
            },
        ],
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


HB_UPDATE_FILE = os.path.join(CONF_DIR, "hb_update.json")
REAR_JOB_FILE = os.path.join(CONF_DIR, "rear_job.json")
REAR_LOG_FILE = os.path.join(LOGS_DIR, "rear_backup.log")
_rear_lock = threading.Lock()
HB_UPDATE_SERVICES = (
    "hummingbot-hl",
    "hummingbot-bn-swing",
    "hummingbot-helliot",
    "hummingbot-surfn",
    "hummingbot-shortn",
    "hummingbot-dman",
    "hummingbot-onlyup",
)
_update_lock = threading.Lock()


def _hb_update_state(defaults=None):
    try:
        with open(HB_UPDATE_FILE, "r") as f:
            data = json.load(f) or {}
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return defaults or {
        "status": "idle",
        "log": [],
        "started": None,
        "finished": None,
        "error": None,
        "services": list(HB_UPDATE_SERVICES),
    }


def _hb_update_save(state):
    state = dict(state or {})
    state["log"] = list(state.get("log") or [])[-200:]
    tmp = HB_UPDATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    os.replace(tmp, HB_UPDATE_FILE)


def _hb_update_log(msg):
    with _update_lock:
        st = _hb_update_state()
        line = datetime.utcnow().strftime("%H:%M:%S") + "  " + str(msg)
        st.setdefault("log", []).append(line)
        _hb_update_save(st)
    return line


def _host_project_dir():
    for name in HB_UPDATE_SERVICES:
        insp = _docker_inspect(name)
        if not insp:
            continue
        for m in insp.get("Mounts") or []:
            if m.get("Destination") == "/home/hummingbot/conf":
                src = str(m.get("Source") or "")
                if src.endswith("/conf"):
                    return src[: -len("/conf")]
        labels = ((insp.get("Config") or {}).get("Labels") or {})
        wd = labels.get("com.docker.compose.project.working_dir")
        if wd:
            return wd
    return os.environ.get("HOST_PROJECT") or ""


def _compose_project_name():
    insp = _docker_inspect("hummingbot-hl") or _docker_inspect("hummingbot-surfn")
    labels = ((insp or {}).get("Config") or {}).get("Labels") or {}
    return labels.get("com.docker.compose.project") or "hummingbot"


def _run_cmd(args, timeout=600):
    _hb_update_log("$ " + " ".join(args))
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    for line in out.splitlines()[-40:]:
        if line.strip():
            _hb_update_log(line.strip()[:400])
    if r.returncode != 0:
        raise RuntimeError(out[-500:] or f"exit {r.returncode}")
    return out


def _run_hummingbot_update():
    with _update_lock:
        st = _hb_update_state()
        st.update({
            "status": "running",
            "error": None,
            "started": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "finished": None,
            "log": list(st.get("log") or [])[-20:],
        })
        _hb_update_save(st)
    try:
        _hb_update_log("Pull hummingbot/hummingbot:latest (positions stay on Hyperliquid)")
        _run_cmd(["docker", "pull", "hummingbot/hummingbot:latest"], timeout=1800)

        host = _host_project_dir()
        compose_file = "/stack/docker-compose.yml"
        if not os.path.isfile(compose_file):
            raise RuntimeError("compose file not mounted at /stack/docker-compose.yml")
        if not host:
            raise RuntimeError("could not detect host project path from container mounts")
        project = _compose_project_name()
        _hb_update_log(f"compose project={project} dir={host}")

        for svc in HB_UPDATE_SERVICES:
            _hb_update_log(f"Recreate {svc} with new image (overlays stay mounted)")
            _run_cmd([
                "docker", "compose",
                "-f", compose_file,
                "--project-directory", host,
                "-p", project,
                "up", "-d", "--no-deps", "--force-recreate", svc,
            ], timeout=180)

        for k in list(_CACHE.keys()):
            if str(k).startswith("gh:") or str(k).startswith("hub:"):
                _CACHE.pop(k, None)

        _hb_update_log("Done. Check Update page — version/digest should match Hub.")
        with _update_lock:
            st = _hb_update_state()
            st["status"] = "ok"
            st["finished"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            _hb_update_save(st)
    except Exception as e:
        _hb_update_log("FAILED: " + str(e)[:400])
        with _update_lock:
            st = _hb_update_state()
            st["status"] = "error"
            st["error"] = str(e)[:400]
            st["finished"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            _hb_update_save(st)


@app.route("/api/update/job")
def api_update_job():
    return jsonify(_hb_update_state())


@app.route("/api/update/run", methods=["POST"])
def api_update_run():
    with _update_lock:
        st = _hb_update_state()
        if st.get("status") == "running":
            return jsonify({"ok": False, "error": "update already running", "job": st}), 409
        st["status"] = "running"
        st["error"] = None
        st["started"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        st["finished"] = None
        st["log"] = list(st.get("log") or [])[-8:] + [
            datetime.utcnow().strftime("%H:%M:%S") + "  queued"
        ]
        _hb_update_save(st)
    t = threading.Thread(target=_run_hummingbot_update, daemon=True)
    t.start()
    return jsonify({"ok": True, "message": "Hummingbot update started", "job": _hb_update_state()})


def _rear_job_default():
    return {
        "status": "idle",
        "error": None,
        "device": None,
        "started": None,
        "finished": None,
        "last_ok": None,
        "last_device": None,
        "rear": None,
        "log": [],
        "spinner": "|",
    }


def _rear_job_load():
    data = _read_json_file(REAR_JOB_FILE)
    if not isinstance(data, dict):
        return _rear_job_default()
    out = _rear_job_default()
    out.update(data)
    return out


def _rear_job_save(st):
    try:
        _write_json_file(REAR_JOB_FILE, st)
    except Exception:
        pass


def _rear_log_tail(n=80):
    try:
        with open(REAR_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        return lines[-n:]
    except Exception:
        return []


def _rear_log_append(line):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    text = ts + "  " + str(line or "").rstrip()
    try:
        os.makedirs(os.path.dirname(REAR_LOG_FILE), exist_ok=True)
        with open(REAR_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass
    with _rear_lock:
        st = _rear_job_load()
        log = list(st.get("log") or [])
        log.append(text)
        st["log"] = log[-60:]
        _rear_job_save(st)


def _host_sh(script, timeout=120):
    """Run a shell snippet on the host via privileged nsenter. Does not touch hunters."""
    return subprocess.run(
        [
            "docker", "run", "--rm",
            "--privileged", "--pid=host", "--network=host",
            "alpine:3.20",
            "nsenter", "-t", "1", "-m", "-u", "-i", "-n", "--",
            "sh", "-lc", script,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _rear_safe_dev(name):
    n = str(name or "").strip().lstrip("/")
    if n.startswith("dev/"):
        n = n[4:]
    if not re.fullmatch(r"sd[a-z]", n):
        return None
    return n


def _rear_list_disks():
    r = _host_sh("lsblk -J -b -o NAME,SIZE,TRAN,RM,TYPE,MODEL,LABEL,MOUNTPOINT", timeout=30)
    disks = []
    if r.returncode != 0:
        return disks, (r.stderr or r.stdout or "lsblk failed")[:300]
    try:
        payload = json.loads(r.stdout or "{}")
    except Exception as e:
        return disks, str(e)
    for b in payload.get("blockdevices") or []:
        if not isinstance(b, dict):
            continue
        if str(b.get("type") or "") != "disk":
            continue
        name = str(b.get("name") or "")
        tran = str(b.get("tran") or "").lower()
        try:
            size = int(b.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        rm = str(b.get("rm") or "") in ("1", "true", "True")
        if tran != "usb" or not rm or size < 1 * 1024 * 1024 * 1024:
            continue
        if not re.fullmatch(r"sd[a-z]", name):
            continue
        disks.append({
            "name": name,
            "path": "/dev/" + name,
            "size": size,
            "size_h": str(b.get("size") or ""),
            "model": (b.get("model") or "USB").strip() or "USB",
            "label": b.get("label") or "",
            "tran": tran,
        })
    return disks, None


def _rear_probe():
    r = _host_sh("command -v rear >/dev/null && rear -V | head -1 || echo MISSING", timeout=20)
    out = (r.stdout or "").strip().splitlines()
    ver = out[-1] if out else ""
    installed = bool(ver) and ver != "MISSING" and r.returncode == 0
    return {"installed": installed, "version": ver if installed else None}


def _parse_docker_size(s):
    t = str(s or "0").strip().upper().replace("IB", "B").replace(" ", "")
    mul = 1.0
    for suf, m in (("TB", 1e12), ("GB", 1e9), ("MB", 1e6), ("KB", 1e3), ("B", 1)):
        if t.endswith(suf):
            t = t[: -len(suf)]
            mul = m
            break
    try:
        return int(float(t) * mul)
    except (TypeError, ValueError):
        return 0


def _docker_graph_bytes():
    try:
        r = subprocess.run(
            ["docker", "system", "df", "--format", "{{.Type}}\t{{.Size}}"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return 0
    total = 0
    for line in (r.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        total += _parse_docker_size(parts[1])
    return total


def _fmt_bytes(n):
    n = float(n or 0)
    for u, m in (("T", 1e12), ("G", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= m:
            return ("{:.1f}{}" if u in ("T", "G") else "{:.0f}{}").format(n / m, u)
    return str(int(n)) + "B"


_REAR_SPACE_CACHE = {"t": 0.0, "data": None}


def _rear_space_check(usb_size=0):
    """Estimate ReaR payload vs USB size. Excludes docker layers / tmp / timeshift."""
    now = time.time()
    base = _REAR_SPACE_CACHE.get("data")
    if not base or now - float(_REAR_SPACE_CACHE.get("t") or 0) > 90:
        r = _host_sh("df -B1 --output=used / | tail -1 | tr -d ' '", timeout=20)
        try:
            host_used = int((r.stdout or "0").strip() or 0)
        except ValueError:
            host_used = 0
        excl = _docker_graph_bytes()
        r2 = _host_sh(
            "du -sbx /timeshift /tmp /var/tmp 2>/dev/null | awk '{s+=$1} END {print s+0}'",
            timeout=30,
        )
        try:
            excl += int((r2.stdout or "0").strip() or 0)
        except ValueError:
            pass
        payload = max(0, host_used - excl)
        gzip_est = int(payload * 0.70)
        overhead = 1024 * 1024 * 1024
        need = gzip_est + overhead
        base = {
            "host_used": host_used,
            "exclude": excl,
            "payload_raw": payload,
            "payload_gzip_est": gzip_est,
            "need": need,
            "host_used_h": _fmt_bytes(host_used),
            "exclude_h": _fmt_bytes(excl),
            "payload_raw_h": _fmt_bytes(payload),
            "payload_gzip_h": _fmt_bytes(gzip_est),
            "need_h": _fmt_bytes(need),
        }
        _REAR_SPACE_CACHE["t"] = now
        _REAR_SPACE_CACHE["data"] = base
    usb_size = int(usb_size or 0)
    need = int(base["need"])
    ok = usb_size > 0 and usb_size >= need
    if usb_size <= 0:
        msg = "pick a USB stick to compare against ~%s estimated backup" % base["need_h"]
    elif ok:
        msg = "USB %s is enough for ~%s gzip estimate (raw %s, docker/tmp skipped)" % (
            _fmt_bytes(usb_size), base["need_h"], base["payload_raw_h"],
        )
    else:
        msg = "USB %s is too small. Need ~%s (gzip est. of %s payload). Use a larger stick." % (
            _fmt_bytes(usb_size), base["need_h"], base["payload_raw_h"],
        )
    out = dict(base)
    out.update({
        "usb_size": usb_size,
        "usb_h": _fmt_bytes(usb_size) if usb_size else "—",
        "ok": ok,
        "message": msg,
    })
    return out


def _run_rear_usb(dev):
    path = "/dev/" + dev
    usb_size = 0
    disks, _err = _rear_list_disks()
    for d in disks:
        if d.get("name") == dev:
            usb_size = int(d.get("size") or 0)
            break
    chk = _rear_space_check(usb_size)
    if not chk.get("ok"):
        raise RuntimeError(chk.get("message") or "USB too small for estimated backup")
    need = int(chk.get("need") or 0)
    script = r"""
set -eu
DEV='%s'
if [ ! -b "$DEV" ]; then echo "not a block device: $DEV"; exit 2; fi
TRAN=$(lsblk -dn -o TRAN "$DEV" 2>/dev/null | tr -d ' ')
RM=$(lsblk -dn -o RM "$DEV" 2>/dev/null | tr -d ' ')
SIZE=$(lsblk -dn -b -o SIZE "$DEV" 2>/dev/null | tr -d ' ')
echo "device $DEV tran=$TRAN rm=$RM size=$SIZE"
if [ "$TRAN" != "usb" ]; then echo "refusing: not USB"; exit 3; fi
if [ "$RM" != "1" ]; then echo "refusing: not removable"; exit 3; fi
if [ "${SIZE:-0}" -lt 1000000000 ]; then echo "refusing: USB smaller than 1G"; exit 3; fi
NEED='%s'
if [ "${SIZE:-0}" -lt "$NEED" ]; then
  echo "refusing: USB $SIZE bytes < estimated need $NEED bytes"
  exit 4
fi
ROOTSRC=$(findmnt -n -o SOURCE / || true)
case "$ROOTSRC" in
  ${DEV}*) echo "refusing: $DEV looks like the root disk"; exit 3 ;;
esac
export DEBIAN_FRONTEND=noninteractive
if ! command -v rear >/dev/null 2>&1; then
  echo "installing rear on host (apt)…"
  apt-get update -y
  apt-get install -y rear extlinux syslinux dosfstools binutils
fi
rear -V | head -1 || true
mkdir -p /etc/rear
cat > /etc/rear/local.conf <<'EOF'
OUTPUT=USB
BACKUP=NETFS
BACKUP_URL=usb:///dev/disk/by-label/REAR-000
USB_DEVICE=/dev/disk/by-label/REAR-000
USING_UEFI_BOOTLOADER=1
BACKUP_PROG_EXCLUDE=( "${BACKUP_PROG_EXCLUDE[@]}" '/var/lib/docker/overlay2/*' '/var/lib/docker/image/*' '/tmp/*' '/var/tmp/*' '/timeshift/*' )
EOF
echo "unmounting partitions on $DEV (if any)"
lsblk -ln -o NAME,MOUNTPOINT "$DEV" | while read -r n m; do
  [ -n "$m" ] && umount -f "/dev/$n" 2>/dev/null || true
done
echo "formatting $DEV as ReaR USB (wipes the stick, not the PC)"
printf 'Yes\n' | rear -v format -- --efi "$DEV"
echo "writing rescue image + backup to USB…"
rear -v mkbackup
echo "DONE rear mkbackup on $DEV"
""" % (path, str(need))
    _rear_log_append("starting ReaR USB write on " + path)
    proc = subprocess.Popen(
        [
            "docker", "run", "--rm",
            "--privileged", "--pid=host", "--network=host",
            "-v", LOGS_DIR + ":" + LOGS_DIR,
            "alpine:3.20",
            "nsenter", "-t", "1", "-m", "-u", "-i", "-n", "--",
            "sh", "-lc", script,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        for line in proc.stdout:
            _rear_log_append(line.strip()[:500])
        rc = proc.wait(timeout=6 * 3600)
    except Exception:
        proc.kill()
        raise
    if rc != 0:
        raise RuntimeError("rear exit " + str(rc))


def _rear_worker(dev):
    try:
        _run_rear_usb(dev)
        with _rear_lock:
            st = _rear_job_load()
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            st["status"] = "ok"
            st["error"] = None
            st["finished"] = now
            st["last_ok"] = now
            st["last_device"] = "/dev/" + dev
            st["spinner"] = "*"
            _rear_job_save(st)
        _rear_log_append("OK  rescue USB ready — boot this stick to recover the PC")
    except Exception as e:
        with _rear_lock:
            st = _rear_job_load()
            st["status"] = "error"
            st["error"] = str(e)[:400]
            st["finished"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            st["spinner"] = "!"
            _rear_job_save(st)
        _rear_log_append("FAILED  " + str(e)[:400])


QA_KINDS = ("bias", "close_all")
QA_BIAS = ("LONG", "SHORT", "FLAT")
QA_COINS = ("BTC", "ETH", "LTC", "TAO")


def _qa_ok_bot(bot_id):
    if bot_id == "*":
        return True
    ids = {h["id"] for h in _nav_hunters()} | {h["id"] for h in _DEFAULT_HUNTERS if h.get("id")}
    return bot_id in ids


@app.route("/api/qa/week")
def api_qa_week():
    """QA week grid. Read-only vs live hunters — never writes BIAS or CLOSE."""
    if not _MATRIX_OK:
        return jsonify({"ok": False, "error": "sqlite store not ready", "items": []}), 500
    try:
        items = matrix_store.qa_list()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "items": []}), 500
    hunters = _nav_hunters()
    return jsonify({
        "ok": True,
        "qa": True,
        "armed": False,
        "message": "JaneStreet · weekly overlay · dry run only · live hunters unchanged",
        "items": items,
        "hunters": hunters,
        "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


@app.route("/api/qa/item", methods=["POST"])
def api_qa_item_add():
    """Place a block on the QA week. Does not touch hunters, BIAS, or orders."""
    if not _MATRIX_OK:
        return jsonify({"ok": False, "error": "sqlite store not ready"}), 500
    data = request.get_json(silent=True) or {}
    kind = str(data.get("kind") or "").strip()
    if kind not in QA_KINDS:
        return jsonify({"ok": False, "error": "kind must be bias or close_all"}), 400
    bot_id = str(data.get("bot_id") or "*").strip()
    if not _qa_ok_bot(bot_id):
        return jsonify({"ok": False, "error": "unknown hunter"}), 400
    if kind == "close_all":
        bot_id = bot_id or "*"
        item = {
            "weekday": data.get("weekday"),
            "minute": data.get("minute"),
            "duration_min": data.get("duration_min") or 15,
            "bot_id": bot_id,
            "kind": "close_all",
            "coin": None,
            "value": "CLOSE",
            "label": data.get("label") or "CLOSE ALL",
        }
    else:
        coin = str(data.get("coin") or "").upper().strip()
        value = str(data.get("value") or "").upper().strip()
        if coin not in QA_COINS:
            return jsonify({"ok": False, "error": "coin must be BTC, ETH, or LTC"}), 400
        if value not in QA_BIAS:
            return jsonify({"ok": False, "error": "value must be LONG, SHORT, or FLAT"}), 400
        item = {
            "weekday": data.get("weekday"),
            "minute": data.get("minute"),
            "duration_min": data.get("duration_min") or 60,
            "bot_id": bot_id,
            "kind": "bias",
            "coin": coin,
            "value": value,
            "label": data.get("label") or (coin + " " + value),
        }
    try:
        item_id = matrix_store.qa_add(item)
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    item["id"] = item_id
    return jsonify({"ok": True, "item": item, "applied": False, "message": "JaneStreet overlay saved · not sent to hunters"})


@app.route("/api/qa/item/<int:item_id>", methods=["DELETE"])
def api_qa_item_del(item_id):
    if not _MATRIX_OK:
        return jsonify({"ok": False, "error": "sqlite store not ready"}), 500
    try:
        matrix_store.qa_delete(item_id)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "id": item_id, "applied": False})


@app.route("/api/health")
def api_health_page():
    """Live desk health + Wire Health canvas. No hunter writes."""
    board = _read_json_file(os.path.join(CONF_DIR, "wire_health.json")) or {"nodes": [], "edges": []}
    return jsonify({
        "ok": True,
        "health": _desk_health(),
        "board": board,
        "condor": _condor_payload(),
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


@app.route("/api/backup")
def api_backup_status():
    disks, err = _rear_list_disks()
    st = _rear_job_load()
    probe = _rear_probe()
    st["rear"] = probe
    picked = 0
    if disks:
        picked = int((disks[0] or {}).get("size") or 0)
    check = _rear_space_check(picked)
    for d in disks:
        c = _rear_space_check(int(d.get("size") or 0))
        d["fits"] = bool(c.get("ok"))
        d["need"] = c.get("need")
        d["need_h"] = c.get("need_h")
        d["note"] = "fits" if d["fits"] else ("too small · need " + (c.get("need_h") or "?"))
    return jsonify({
        "ok": True,
        "job": st,
        "disks": disks,
        "disks_error": err,
        "rear": probe,
        "check": check,
        "log_tail": _rear_log_tail(80),
        "note": "Writes a bootable ReaR rescue USB. Does not restore the running PC and does not close Hyperliquid positions.",
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


@app.route("/api/backup/job")
def api_backup_job():
    st = _rear_job_load()
    frames = "|/-\\"
    if st.get("status") == "running":
        i = int(time.time()) % 4
        st["spinner"] = frames[i]
    return jsonify({"ok": True, "job": st, "log_tail": _rear_log_tail(60)})


@app.route("/api/backup/run", methods=["POST"])
def api_backup_run():
    """Write a ReaR bare-metal rescue image onto a USB stick. Never targets NVMe/root."""
    body = request.get_json(silent=True) or {}
    dev = _rear_safe_dev(body.get("device") or body.get("dev"))
    confirm = str(body.get("confirm") or "").strip()
    if not dev:
        return jsonify({"ok": False, "error": "pick a USB disk like sdb (not nvme, not a partition)"}), 400
    if confirm != dev:
        return jsonify({"ok": False, "error": "type the device name to confirm — this wipes the USB stick only"}), 400
    disks, _err = _rear_list_disks()
    names = {d.get("name") for d in disks}
    if dev not in names:
        return jsonify({"ok": False, "error": "/dev/" + dev + " is not a removable USB disk"}), 400
    usb_size = 0
    for d in disks:
        if d.get("name") == dev:
            usb_size = int(d.get("size") or 0)
            break
    chk = _rear_space_check(usb_size)
    if not chk.get("ok"):
        return jsonify({"ok": False, "error": chk.get("message") or "USB too small", "check": chk}), 400
    with _rear_lock:
        st = _rear_job_load()
        if st.get("status") == "running":
            return jsonify({"ok": False, "error": "backup already running", "job": st}), 409
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        st.update({
            "status": "running",
            "error": None,
            "device": "/dev/" + dev,
            "started": now,
            "finished": None,
            "spinner": "/",
            "log": list(st.get("log") or [])[-12:] + [now + "  queued USB write /dev/" + dev],
        })
        _rear_job_save(st)
    threading.Thread(target=_rear_worker, args=(dev,), daemon=True, name="rear-usb").start()
    return jsonify({
        "ok": True,
        "message": "writing ReaR rescue USB to /dev/" + dev,
        "job": _rear_job_load(),
    })


_USB_FORBIDDEN_MOUNTS = frozenset({
    "/", "/boot", "/usr", "/var", "/etc", "/opt", "/root", "/home", "/snap", "/tmp",
})


def _cpp_host_root():
    """Host path of the parallel c++/ kit. Never guesses NVMe system dirs as dest."""
    r = _host_sh(
        r"""
set -e
for p in /opt/freiquell/c++ /opt/freiquell/c++ /opt/hummingbot/c++; do
  if [ -x "$p/install/install.sh" ] && [ -d "$p/server" ] && [ -d "$p/matrix" ]; then
    echo "$p"
    exit 0
  fi
done
SRC=$(docker inspect hummingbot-dashboard --format '{{range .Mounts}}{{if eq .Destination "/stack/docker-compose.yml"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)
if [ -n "$SRC" ]; then
  d=$(dirname "$SRC")
  if [ -x "$d/c++/install/install.sh" ]; then
    echo "$d/c++"
    exit 0
  fi
fi
exit 1
""",
        timeout=20,
    )
    path = (r.stdout or "").strip().splitlines()
    path = path[-1] if path else ""
    if r.returncode != 0 or not path.startswith("/"):
        return None
    return path


def _usb_mount_allowed(mp):
    mp = os.path.realpath(str(mp or ""))
    if not mp or mp in _USB_FORBIDDEN_MOUNTS:
        return False
    for b in ("/usr/", "/var/", "/etc/", "/boot/", "/opt/", "/root/", "/snap/", "/proc/", "/sys/"):
        if mp.startswith(b):
            return False
    if mp == "/home" or re.match(r"^/home/[^/]+$", mp):
        return False
    if "/hummingbird/" in mp or mp.rstrip("/").endswith("/hummingbot"):
        return False
    return True


def _usb_list_copy_targets():
    """Mounted partitions on removable USB disks only. Never NVMe/root."""
    r = _host_sh("lsblk -J -b -o NAME,SIZE,TRAN,RM,TYPE,MODEL,LABEL,MOUNTPOINT,FSAVAIL", timeout=30)
    targets = []
    err = None
    if r.returncode != 0:
        return targets, (r.stderr or r.stdout or "lsblk failed")[:300]
    try:
        payload = json.loads(r.stdout or "{}")
    except Exception as e:
        return targets, str(e)

    def walk(node, usb=False, rm=False, model=""):
        if not isinstance(node, dict):
            return
        tran = str(node.get("tran") or "").lower()
        typ = str(node.get("type") or "")
        name = str(node.get("name") or "")
        this_usb = usb or tran == "usb"
        this_rm = rm or str(node.get("rm") or "") in ("1", "true", "True")
        this_model = (node.get("model") or model or "USB")
        this_model = str(this_model).strip() or "USB"
        if tran == "nvme" or name.startswith("nvme"):
            return
        mp = node.get("mountpoint")
        if this_usb and this_rm and mp and _usb_mount_allowed(mp):
            try:
                size = int(node.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            try:
                avail = int(node.get("fsavail") or 0)
            except (TypeError, ValueError):
                avail = 0
            targets.append({
                "name": name,
                "path": "/dev/" + name,
                "mount": os.path.realpath(str(mp)),
                "size": size,
                "avail": avail,
                "model": this_model,
                "label": node.get("label") or "",
            })
        for ch in node.get("children") or []:
            walk(ch, this_usb, this_rm, this_model)

    for b in payload.get("blockdevices") or []:
        walk(b)
    return targets, err


CPP_USB_JOB_FILE = os.path.join(CONF_DIR, "cpp_usb_job.json")
CPP_USB_LOG_FILE = os.path.join(LOGS_DIR, "cpp_usb.log")
_cpp_usb_lock = threading.Lock()


def _cpp_usb_job_default():
    return {
        "status": "idle",
        "error": None,
        "mount": None,
        "dest": None,
        "size": None,
        "started": None,
        "finished": None,
        "log": [],
    }


def _cpp_usb_job_load():
    data = _read_json_file(CPP_USB_JOB_FILE)
    if not isinstance(data, dict):
        return _cpp_usb_job_default()
    out = _cpp_usb_job_default()
    out.update(data)
    return out


def _cpp_usb_job_save(st):
    try:
        _write_json_file(CPP_USB_JOB_FILE, st)
    except Exception:
        pass


def _cpp_usb_log(line):
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    text = ts + "  " + str(line or "").rstrip()
    try:
        os.makedirs(os.path.dirname(CPP_USB_LOG_FILE), exist_ok=True)
        with open(CPP_USB_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception:
        pass
    with _cpp_usb_lock:
        st = _cpp_usb_job_load()
        log = list(st.get("log") or [])
        log.append(text)
        st["log"] = log[-80:]
        _cpp_usb_job_save(st)


def _cpp_usb_copy(mount):
    """Pack C++ desk + hunters + docker image onto a mounted USB stick. No keys. No wipe."""
    src = _cpp_host_root()
    if not src:
        return {"ok": False, "error": "c++ kit not found on host (need c++/install/install.sh)"}
    targets, lserr = _usb_list_copy_targets()
    if lserr and not targets:
        return {"ok": False, "error": lserr}
    allowed = {t["mount"] for t in targets}
    dest_root = os.path.realpath(str(mount or ""))
    if dest_root not in allowed:
        return {"ok": False, "error": "mount is not a removable USB filesystem"}
    dest = os.path.join(dest_root, "freiquell-cpp")
    if not dest.startswith(dest_root + os.sep) and dest != dest_root:
        return {"ok": False, "error": "refusing destination outside USB mount"}
    avail = 0
    for t in targets:
        if t.get("mount") == dest_root:
            try:
                avail = int(t.get("avail") or 0)
            except (TypeError, ValueError):
                avail = 0
            break
    if avail and avail < 2500 * 1024 * 1024:
        return {
            "ok": False,
            "error": "USB needs about 2.5G free for the desk + hummingbot image (have %s)" % avail,
        }
    script = r"""
set -e
export PATH="/usr/bin:/usr/local/bin:/bin:$PATH"
SRC=%s
DEST=%s
test -x "$SRC/install/install.sh"
mkdir -p "$DEST"
echo "pack start $DEST"
"$SRC/install/install.sh" pack "$DEST"
echo "pack done"
du -sh "$DEST" | awk '{print $1}'
test -f "$DEST/hb/scripts/hl_copy_trader.py"
test -f "$DEST/hb/scripts/dman_maker_v2_trader.py"
test -f "$DEST/START.txt"
test -f "$DEST/container/docker-compose.yml"
# wallets must not land on the stick
test ! -f "$DEST/data/conf/bot_accounts.json"
test ! -f "$DEST/data/conf/connectors/hyperliquid_perpetual.yml"
test ! -f "$DEST/data/conf/matrix.db"
""" % (
        shlex.quote(src),
        shlex.quote(dest),
    )
    r = _host_sh(script, timeout=1200)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        return {
            "ok": False,
            "error": (err or out or "pack failed")[-800:],
            "log": out[-1200:] if out else "",
        }
    size_h = out.splitlines()[-1] if out else "?"
    return {
        "ok": True,
        "source": src,
        "dest": dest,
        "size": size_h,
        "log": out[-2000:] if out else "",
        "message": (
            "packed desk to " + dest + " (" + size_h + "). "
            "On the other machine copy the folder to disk and read START.txt. "
            "Wallets are not on the stick — unique subaccounts, see KEYS.txt."
        ),
    }


def _cpp_usb_worker(mount):
    try:
        _cpp_usb_log("packing C++ desk + hunters + docker image → " + str(mount) + "/freiquell-cpp")
        out = _cpp_usb_copy(mount)
        with _cpp_usb_lock:
            st = _cpp_usb_job_load()
            st["finished"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            if out.get("ok"):
                st["status"] = "done"
                st["error"] = None
                st["dest"] = out.get("dest")
                st["size"] = out.get("size")
            else:
                st["status"] = "error"
                st["error"] = out.get("error") or "pack failed"
            _cpp_usb_job_save(st)
        if out.get("ok"):
            _cpp_usb_log("done " + str(out.get("dest") or "") + " " + str(out.get("size") or ""))
        else:
            _cpp_usb_log("failed " + str(out.get("error") or ""))
            extra = out.get("log") or ""
            if extra:
                _cpp_usb_log(extra[-500:])
    except subprocess.TimeoutExpired:
        with _cpp_usb_lock:
            st = _cpp_usb_job_load()
            st["status"] = "error"
            st["error"] = "pack timed out (docker save + USB copy)"
            st["finished"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            _cpp_usb_job_save(st)
        _cpp_usb_log("timed out")
    except Exception as e:
        with _cpp_usb_lock:
            st = _cpp_usb_job_load()
            st["status"] = "error"
            st["error"] = str(e)[:400]
            st["finished"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            _cpp_usb_job_save(st)
        _cpp_usb_log("error " + str(e)[:400])


@app.route("/api/cpp_usb")
def api_cpp_usb():
    """List mounted USB sticks eligible for a file copy (never NVMe/root)."""
    src = _cpp_host_root()
    targets, err = _usb_list_copy_targets()
    return jsonify({
        "ok": True,
        "source": src,
        "source_ok": bool(src),
        "mounts": targets,
        "job": _cpp_usb_job_load(),
        "error": err,
        "note": (
            "Packs C++ server/matrix, eight hunter scripts, compose, and the hummingbot image. "
            "No wallets, no connector keys, no matrix.db. Does not format the stick. Does not close positions."
        ),
        "ts": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


@app.route("/api/cpp_usb/copy", methods=["POST"])
def api_cpp_usb_copy():
    """Pack the full C++ desk onto a mounted USB filesystem. Never NVMe/root. Does not wipe."""
    body = request.get_json(silent=True) or {}
    mount = str(body.get("mount") or body.get("path") or "").strip()
    if not mount:
        return jsonify({"ok": False, "error": "pick a mounted USB path"}), 400
    src = _cpp_host_root()
    if not src:
        return jsonify({"ok": False, "error": "c++ kit not found on host"}), 400
    targets, lserr = _usb_list_copy_targets()
    if lserr and not targets:
        return jsonify({"ok": False, "error": lserr}), 400
    dest_root = os.path.realpath(mount)
    if dest_root not in {t["mount"] for t in targets}:
        return jsonify({"ok": False, "error": "mount is not a removable USB filesystem"}), 400
    with _cpp_usb_lock:
        st = _cpp_usb_job_load()
        if st.get("status") == "running":
            return jsonify({"ok": False, "error": "copy already running", "job": st}), 409
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        st.update({
            "status": "running",
            "error": None,
            "mount": dest_root,
            "dest": os.path.join(dest_root, "freiquell-cpp"),
            "size": None,
            "started": now,
            "finished": None,
            "log": list(st.get("log") or [])[-12:] + [now + "  queued pack to " + dest_root],
        })
        _cpp_usb_job_save(st)
    threading.Thread(target=_cpp_usb_worker, args=(dest_root,), daemon=True, name="cpp-usb").start()
    return jsonify({
        "ok": True,
        "message": "packing desk onto USB (docker image ~1.4G — a few minutes)",
        "job": _cpp_usb_job_load(),
    })


def _run_brief_server():
    brief_app = Flask("matrix-brief")

    @brief_app.route("/")
    @brief_app.route("/brief")
    @brief_app.route("/api/brief")
    def _only_brief():
        if not _brief_authorized():
            return Response("token required\n", status=401, mimetype="text/plain")
        return Response(_write_desk_brief_file(), mimetype="text/plain; charset=utf-8")

    brief_app.run(host="0.0.0.0", port=5002, debug=False, threaded=True)


_brief_token()
threading.Thread(target=_brief_loop, daemon=True, name="desk-brief").start()
threading.Thread(target=_run_brief_server, daemon=True, name="brief-http").start()
threading.Thread(target=_desk_bias_loop, daemon=True, name="desk-bias").start()
threading.Thread(target=_risk_skim_loop, daemon=True, name="risk-skim").start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
