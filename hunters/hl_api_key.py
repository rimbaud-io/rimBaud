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

"""Save a Hyperliquid API wallet secret once, then install it.

The exchange shows the private key a single time. This stores the hummingbot
keystore (not the raw key) in conf/matrix.db and writes
hyperliquid_perpetual_secret_key in every hunter connector file. Vault
addresses are left as they are. Running hunters are reloaded so they pick up
the new signer. Paused hunters are left paused. No orders are sent.

  python3 scripts/hl_api_key.py status
  python3 scripts/hl_api_key.py set          # one key on stdin
  python3 scripts/hl_api_key.py selftest
  python3 scripts/hl_api_key.py probe        # public signer vs this machine's master, no secret
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HUNTERS = (
    "hummingbot-hl",
    "hummingbot-bn-swing",
    "hummingbot-helliot",
    "hummingbot-surfn",
    "hummingbot-shortn",
    "hummingbot-dman",
    "hummingbot-onlyup",
    "hummingbot-dman-maker",
)
IMAGE = "hummingbot/hummingbot:latest"
PY = "/opt/conda/envs/hummingbot/bin/python"
FIELD = "hyperliquid_perpetual_secret_key"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def db_path() -> Path:
    return repo_root() / "conf" / "matrix.db"


def pub_path() -> Path:
    return repo_root() / "conf" / "hl_api_pub.json"


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def fail(msg: str, code: int = 1) -> None:
    emit({"ok": False, "error": msg})
    raise SystemExit(code)


def password_from_container() -> str:
    out = subprocess.check_output(
        ["docker", "inspect", "-f", "{{range .Config.Env}}{{println .}}{{end}}", "hummingbot-dman-maker"],
        text=True,
    )
    for line in out.splitlines():
        if line.startswith("CONFIG_PASSWORD="):
            return line.split("=", 1)[1]
    fail("CONFIG_PASSWORD is not on hummingbot-dman-maker")
    return ""


def run_inner() -> dict:
    pw = password_from_container()
    fd, envf = tempfile.mkstemp(prefix="hl-api-env-", dir="/tmp")
    try:
        os.write(fd, f"CONFIG_PASSWORD={pw}\nHL_API_INNER=1\n".encode())
    finally:
        os.close(fd)
    os.chmod(envf, 0o600)
    root = str(repo_root())
    cmd = [
        "docker", "run", "--rm", "-i", "--user", "0",
        "--entrypoint", PY,
        "--env-file", envf,
        "-e", "PYTHONPATH=/home/hummingbot",
        "-v", f"{root}:/w", "-w", "/w",
        IMAGE, "/w/scripts/hl_api_key.py", *sys.argv[1:],
    ]
    if sys.argv[1:2] == ["set"]:
        data = sys.stdin.buffer.read() if not sys.stdin.isatty() else sys.stdin.readline().encode()
    else:
        data = b""
    try:
        r = subprocess.run(cmd, input=data, capture_output=True)
    finally:
        try:
            os.unlink(envf)
        except OSError:
            pass
    text = (r.stdout or b"").decode(errors="replace").strip()
    err = (r.stderr or b"").decode(errors="replace").strip()
    try:
        obj = json.loads(text.splitlines()[-1] if text else "{}")
    except json.JSONDecodeError:
        obj = {"ok": False, "error": err[-240:] or text[-240:] or "key tool failed"}
    if r.returncode != 0 and obj.get("ok") is not False:
        obj = {"ok": False, "error": err[-240:] or text[-240:] or "key tool failed"}
    return obj


def normalize_key(raw: str) -> str:
    s = "".join(raw.split())
    if s.lower().startswith("0x"):
        s = s[2:]
    if len(s) != 64 or any(c not in "0123456789abcdefABCDEF" for c in s):
        fail("paste the API private key once (64 hex characters)")
    return "0x" + s.lower()


def encrypt_key(secret: str, password: str) -> tuple[str, str]:
    from eth_account import Account
    from hummingbot.client.config.config_crypt import ETHKeyFileSecretManger

    acct = Account.from_key(secret)
    address = acct.address
    mgr = ETHKeyFileSecretManger(password)
    secret_hex = mgr.encrypt_secret_value(FIELD, secret)
    back = mgr.decrypt_secret_value(FIELD, secret_hex)
    if Account.from_key(back).address.lower() != address.lower():
        fail("keystore round-trip failed")
    return address, secret_hex


def decrypt_hex(secret_hex: str, password: str) -> str:
    import binascii
    from eth_account import Account

    raw = binascii.unhexlify(secret_hex.strip())
    plain = Account.decrypt(raw.decode(), password)
    if isinstance(plain, bytes):
        plain = plain.decode()
    return Account.from_key(plain).address


def connector_files() -> list[Path]:
    out = []
    root = repo_root()
    for p in sorted(root.glob("conf*/connectors/hyperliquid_perpetual.yml")):
        if "doku" in p.parts:
            continue
        out.append(p)
    return out


def read_secret_line(path: Path) -> str:
    for ln in path.read_text(errors="replace").splitlines():
        if ln.startswith(FIELD + ":"):
            return ln.split(":", 1)[1].strip()
    return ""


def write_secret_line(path: Path, secret_hex: str) -> None:
    lines = path.read_text(errors="replace").splitlines()
    found = False
    for i, ln in enumerate(lines):
        if ln.startswith(FIELD + ":"):
            lines[i] = FIELD + ": " + secret_hex
            found = True
            break
    if not found:
        fail(f"{path} has no {FIELD}")
    path.write_text("\n".join(lines) + "\n")


def ensure_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS hl_api_wallet (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            address TEXT NOT NULL,
            secret_hex TEXT NOT NULL,
            name TEXT,
            updated_at TEXT NOT NULL,
            n_files INTEGER NOT NULL
        )
        """
    )


def save_row(address: str, secret_hex: str, n_files: int, name: str = "") -> None:
    con = sqlite3.connect(str(db_path()), timeout=8)
    try:
        ensure_table(con)
        con.execute(
            """
            INSERT INTO hl_api_wallet (id, address, secret_hex, name, updated_at, n_files)
            VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                address=excluded.address,
                secret_hex=excluded.secret_hex,
                name=excluded.name,
                updated_at=excluded.updated_at,
                n_files=excluded.n_files
            """,
            (address, secret_hex, name, now_utc(), n_files),
        )
        con.commit()
    finally:
        con.close()


def load_row() -> dict | None:
    if not db_path().exists():
        return None
    con = sqlite3.connect(str(db_path()), timeout=8)
    try:
        ensure_table(con)
        con.commit()
        row = con.execute(
            "SELECT address, secret_hex, name, updated_at, n_files FROM hl_api_wallet WHERE id=1"
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return {
        "address": row[0],
        "secret_hex": row[1],
        "name": row[2] or "",
        "updated_at": row[3],
        "n_files": row[4],
    }


def hl_post(payload: dict):
    req = urllib.request.Request(
        "https://api.hyperliquid.xyz/info",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def master_address() -> str:
    """This machine's master. A child does not use the mothership account."""
    path = repo_root() / "conf" / "bot_accounts.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        data = {}
    for bot in data.get("bots") or []:
        if not isinstance(bot, dict) or bot.get("use_vault") is not False:
            continue
        addr = str(bot.get("address") or "")
        if addr.startswith("0x") and len(addr) == 42:
            return addr
    env = os.environ.get("HL_MASTER") or os.environ.get("HL_COPY_WALLET") or ""
    if env.startswith("0x") and len(env) == 42:
        return env
    return ""


def note_weight(kind: str, weight: int, is_429: bool = False) -> None:
    path = os.environ.get("HL_IP_BUDGET_FILE") or str(repo_root() / "conf" / "hl_ip_budget.json")
    os.environ["HL_IP_BUDGET_FILE"] = path
    # Hunters create the lock as root. The directory is writable, so drop a lock
    # this user cannot append to and let record() create its own.
    lock = path + ".lock"
    if lock and os.path.exists(lock) and not os.access(lock, os.W_OK):
        try:
            os.remove(lock)
        except OSError:
            try:
                subprocess.run(
                    ["docker", "exec", "hummingbot-dman-maker", "chmod", "666",
                     "/home/hummingbot/conf/hl_ip_budget.json",
                     "/home/hummingbot/conf/hl_ip_budget.json.lock"],
                    timeout=8, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.SubprocessError):
                return
    try:
        sys.path.insert(0, str(repo_root() / "scripts"))
        import hl_ip_budget
        hl_ip_budget.record(
            {"type": kind}, src="hl_key", is_429=is_429,
            path_url="https://api.hyperliquid.xyz/info", weight=weight,
        )
    except Exception:
        pass


def _hl_fail(kind: str, exc: BaseException) -> dict:
    code = getattr(exc, "code", None)
    note_weight(kind, 20, is_429=(code == 429))
    if code:
        return {"on_exchange": "unknown", "exchange_error": f"HTTP {code}"}
    return {"on_exchange": "unknown", "exchange_error": type(exc).__name__}


def exchange_match(address: str, master: str = "") -> dict:
    master = master or master_address()
    if not master:
        return {"on_exchange": "unknown", "exchange_error": "no master on this machine"}
    addr = address.lower()
    try:
        agents = hl_post({"type": "extraAgents", "user": master})
        note_weight("extraAgents", 20)
    except Exception as e:
        return _hl_fail("extraAgents", e)
    if isinstance(agents, list):
        for a in agents:
            if str(a.get("address") or "").lower() == addr:
                return {
                    "on_exchange": "named",
                    "agent_name": a.get("name") or "",
                    "valid_until": a.get("validUntil"),
                }
    try:
        web = hl_post({"type": "webData2", "user": master})
        note_weight("webData2", 20)
    except Exception as e:
        return _hl_fail("webData2", e)
    primary = str((web or {}).get("agentAddress") or "") if isinstance(web, dict) else ""
    if primary.lower() == addr:
        return {
            "on_exchange": "primary",
            "agent_name": "app.hyperliquid.xyz",
            "valid_until": (web or {}).get("agentValidUntil") if isinstance(web, dict) else None,
        }
    return {"on_exchange": "no", "primary": primary}


def load_pub() -> dict:
    try:
        data = json.loads(pub_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def merge_pub(patch: dict) -> None:
    cur = load_pub()
    cur.pop("secret_hex", None)
    cur.update(patch)
    write_pub(cur)


def do_probe() -> None:
    """One cheap acceptance check. Does not read the secret and does not restart hunters."""
    pub = load_pub()
    address = str(pub.get("address") or "")
    if not address.startswith("0x"):
        print("HL KEY grey — no signer saved", flush=True)
        return
    master = master_address()
    if not master:
        print("HL KEY grey — no master on this machine", flush=True)
        return
    match = exchange_match(address, master)
    on = str(match.get("on_exchange") or "unknown")
    if on == "unknown":
        err = str(match.get("exchange_error") or "unknown")
        merge_pub({"exchange_error": err, "checked_at": now_utc()})
        print(f"HL KEY keep — {err}", flush=True)
        return
    merge_pub({
        "on_exchange": on,
        "agent_name": match.get("agent_name") or "",
        "valid_until": match.get("valid_until"),
        "primary": match.get("primary"),
        "exchange_error": "",
        "reject": "",
        "checked_at": now_utc(),
    })
    if on in ("named", "primary"):
        name = match.get("agent_name") or on
        print(f"HL KEY green {on} {name}", flush=True)
    else:
        print("HL KEY red signer not listed on this master", flush=True)


def write_pub(info: dict) -> None:
    pub = {k: v for k, v in info.items() if k != "secret_hex"}
    pub_path().write_text(json.dumps(pub, indent=2) + "\n")
    try:
        os.chmod(pub_path(), 0o666)
    except OSError:
        pass


def installed_address(password: str) -> tuple[str, str, int]:
    files = connector_files()
    hexes = []
    for p in files:
        h = read_secret_line(p)
        if h:
            hexes.append(h)
    if not hexes:
        fail("no hunter connector files")
    address = decrypt_hex(hexes[0], password)
    same = sum(1 for h in hexes if h == hexes[0])
    return address, hexes[0], same


def do_status() -> None:
    password = os.environ["CONFIG_PASSWORD"]
    address, secret_hex, n_same = installed_address(password)
    row = load_row()
    if row is None or row.get("address", "").lower() != address.lower():
        save_row(address, secret_hex, n_same, row.get("name", "") if row else "installed")
        row = load_row()
    info = {
        "ok": True,
        "address": address,
        "updated_at": (row or {}).get("updated_at") or "",
        "n_files": n_same,
        "saved": True,
        "name": (row or {}).get("name") or "",
    }
    info.update(exchange_match(address))
    write_pub(info)
    emit(info)


def docker_state(name: str) -> tuple[bool, bool]:
    try:
        out = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.State.Running}} {{.State.Paused}}", name],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip().split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False, False
    running = bool(out) and out[0] == "true"
    paused = len(out) > 1 and out[1] == "true"
    return running, paused


def reload_running() -> tuple[list[str], list[str]]:
    start = []
    skipped = []
    for name in HUNTERS:
        running, paused = docker_state(name)
        if paused or not running:
            skipped.append(name)
            continue
        start.append(name)
    if start:
        script = "\n".join(f"docker restart {n} >/dev/null" for n in start)
        subprocess.Popen(
            ["bash", "-c", script],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return start, skipped


def do_set() -> None:
    password = os.environ["CONFIG_PASSWORD"]
    secret = normalize_key(sys.stdin.read())
    address, secret_hex = encrypt_key(secret, password)
    n = 0
    for p in connector_files():
        if not read_secret_line(p):
            continue
        write_secret_line(p, secret_hex)
        n += 1
    if n == 0:
        fail("no connector secret lines updated")
    save_row(address, secret_hex, n, "pasted")
    match = exchange_match(address)
    info = {
        "ok": True,
        "address": address,
        "updated_at": now_utc(),
        "n_files": n,
        "saved": True,
        "name": "pasted",
        "message": f"saved {address} in SQLite and {n} connector files.",
    }
    info.update(match)
    write_pub(info)
    emit(info)


def do_selftest() -> None:
    password = os.environ["CONFIG_PASSWORD"]
    from eth_account import Account

    acct = Account.create()
    secret = acct.key.hex()
    if not secret.startswith("0x"):
        secret = "0x" + secret
    address, secret_hex = encrypt_key(secret, password)
    back = decrypt_hex(secret_hex, password)
    if back.lower() != address.lower() or address.lower() != acct.address.lower():
        fail("selftest address mismatch")
    emit({"ok": True, "selftest": True, "address_match": True})


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        do_probe()
        return
    if os.environ.get("HL_API_INNER") == "1":
        if "CONFIG_PASSWORD" not in os.environ:
            fail("CONFIG_PASSWORD missing")
        cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
        if cmd == "status":
            do_status()
        elif cmd == "set":
            do_set()
        elif cmd == "selftest":
            do_selftest()
        else:
            fail("use status, set, or selftest")
        return
    obj = run_inner()
    if sys.argv[1:2] == ["set"] and obj.get("ok"):
        reloaded, skipped = reload_running()
        obj["reloaded"] = reloaded
        obj["left_paused"] = skipped
        obj["message"] = (
            str(obj.get("message") or "saved")
            + " Running hunters are reloading. Paused hunters stay paused. Positions stay."
        )
        try:
            write_pub(obj)
        except OSError:
            pass
    emit(obj)
    if not obj.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
