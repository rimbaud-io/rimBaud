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

"""HL_wallet_creator — new Hyperliquid MASTER + hunter subaccounts.

Does not CLOSE/OPEN. Does not write live hunter connector yml.
Subaccounts (HL official): only after $100k lifetime volume.
Until then we wait for a USDC deposit and refuse create-subs.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

OUT = os.environ.get("HL_WALLET_OUT") or "conf/hl_wallets"
INFO = "https://api.hyperliquid.xyz/info"
EXCHANGE = "https://api.hyperliquid.xyz/exchange"
HUNTER_SUBS = (
    "Swing",
    "Helliot",
    "Surfn",
    "Shorty",
    "Dman",
    "OnlyUp",
    "DManMaker",
)
MIN_USD = float(os.environ.get("HL_MIN_DEPOSIT_USD") or "1")


def _ensure_out():
    os.makedirs(OUT, exist_ok=True)
    try:
        os.chmod(OUT, 0o700)
    except OSError:
        pass


def _log(msg: str) -> None:
    _ensure_out()
    line = time.strftime("%H:%M:%S") + "  " + msg
    path = os.path.join(OUT, "creator.log")
    with open(path, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def _state_path():
    return os.path.join(OUT, "state.json")


def load_state() -> dict:
    p = _state_path()
    if not os.path.isfile(p):
        return {}
    try:
        return json.load(open(p))
    except Exception:
        return {}


def save_state(st: dict) -> None:
    _ensure_out()
    st["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmp = _state_path() + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=2)
        f.write("\n")
    os.replace(tmp, _state_path())
    try:
        os.chmod(_state_path(), 0o600)
    except OSError:
        pass


def http_json(url: str, body: dict, timeout: float = 12) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def account_value(addr: str) -> float:
    d = http_json(INFO, {"type": "clearinghouseState", "user": addr.lower()})
    ms = (d or {}).get("marginSummary") or {}
    try:
        return float(ms.get("accountValue") or 0)
    except (TypeError, ValueError):
        return 0.0


def lifetime_volume(addr: str) -> float:
    try:
        d = http_json(INFO, {"type": "userFees", "user": addr.lower()})
    except Exception:
        return 0.0
    for k in ("userAddRate",):
        pass
    # userFees has volume fields that vary; try common keys
    for k in ("totalNtlPos",):
        pass
    vol = d.get("volume") if isinstance(d, dict) else None
    if isinstance(vol, dict):
        try:
            return float(vol.get("total") or vol.get("allTime") or 0)
        except (TypeError, ValueError):
            return 0.0
    for k in ("allTime", "totalVolume", "vlm"):
        if k in (d or {}):
            try:
                return float(d[k])
            except (TypeError, ValueError):
                pass
    return 0.0


def _need_eth():
    try:
        from eth_account import Account  # noqa: F401
        import msgpack  # noqa: F401
        from eth_account.messages import encode_typed_data  # noqa: F401
        from eth_utils import keccak, to_hex  # noqa: F401
    except ImportError:
        sys.stderr.write(
            "need eth_account+msgpack — run via HL_wallet_creator/hl_wallet_creator.sh (docker)\n"
        )
        sys.exit(2)


def generate_master() -> dict:
    _need_eth()
    from eth_account import Account

    acct = Account.create()
    st = {
        "ok": True,
        "closes": False,
        "phase": "await_deposit",
        "master": acct.address,
        "master_key": acct.key.hex() if isinstance(acct.key, bytes) else str(acct.key),
        "subs": [],
        "api_agent": None,
        "api_agent_key": None,
        "account_value": 0,
        "volume": 0,
        "note": "Transfer USDC onto Hyperliquid for this address. Subaccounts stay blocked until funded (HL also requires $100k volume to create them).",
    }
    if not str(st["master_key"]).startswith("0x"):
        st["master_key"] = "0x" + st["master_key"]
    save_state(st)
    _log("MASTER created  " + st["master"])
    _log("This is a new EVM key. It is NOT the hub desk wallet.")
    _log("Deposit USDC on Hyperliquid (app.hyperliquid.xyz) to this address.")
    _log("Arbitrum USDC bridge → Hyperliquid, or send from an existing HL account.")
    _log("Subaccounts will NOT be created until this master shows a USDC balance.")
    _log("HL official: createSubAccount needs $100k lifetime volume on the master.")
    return st


def sign_l1(wallet, action, nonce):
    import msgpack
    from eth_account.messages import encode_typed_data
    from eth_utils import keccak, to_hex

    data = msgpack.packb(action)
    data += int(nonce).to_bytes(8, "big")
    data += b"\x00"  # no vault
    h = keccak(data)
    phantom = {"source": "a", "connectionId": h}
    typed = {
        "domain": {
            "chainId": 1337,
            "name": "Exchange",
            "verifyingContract": "0x0000000000000000000000000000000000000000",
            "version": "1",
        },
        "types": {
            "Agent": [
                {"name": "source", "type": "string"},
                {"name": "connectionId", "type": "bytes32"},
            ],
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
        },
        "primaryType": "Agent",
        "message": phantom,
    }
    structured = encode_typed_data(full_message=typed)
    signed = wallet.sign_message(structured)
    return {"r": to_hex(signed["r"]), "s": to_hex(signed["s"]), "v": signed["v"]}


def post_action(wallet, action):
    nonce = int(time.time() * 1000)
    sig = sign_l1(wallet, action, nonce)
    payload = {"action": action, "nonce": nonce, "signature": sig}
    return http_json(EXCHANGE, payload)


def refresh() -> dict:
    st = load_state()
    if not st.get("master"):
        _log("no master yet — Generate first")
        return st
    addr = st["master"]
    try:
        val = account_value(addr)
        st["account_value"] = val
    except Exception as e:
        _log("balance lookup failed: " + str(e))
        save_state(st)
        return st
    try:
        st["volume"] = lifetime_volume(addr)
    except Exception:
        pass
    if val >= MIN_USD and st.get("phase") == "await_deposit":
        st["phase"] = "funded"
        _log("FUNDED  accountValue=$%.2f  — you may create subaccounts" % val)
        if float(st.get("volume") or 0) < 100000:
            _log("NOTE  lifetime volume ~$%.0f. HL requires $100k volume before createSubAccount." % float(st.get("volume") or 0))
    elif val < MIN_USD:
        st["phase"] = "await_deposit"
        _log("waiting for deposit  %s  accountValue=$%.4f  (need >= $%.0f USDC on HL)" % (addr, val, MIN_USD))
    save_state(st)
    return st


def create_subs() -> dict:
    _need_eth()
    from eth_account import Account

    st = refresh()
    if not st.get("master_key"):
        _log("no master key")
        return st
    if float(st.get("account_value") or 0) < MIN_USD:
        _log("BLOCKED  transfer funds to %s on Hyperliquid first — no subaccounts yet" % st["master"])
        st["phase"] = "await_deposit"
        save_state(st)
        return st
    wallet = Account.from_key(st["master_key"])
    existing = {s.get("name"): s for s in (st.get("subs") or []) if s.get("name")}
    _log("creating hunter subaccounts under " + st["master"])
    for name in HUNTER_SUBS:
        if name in existing and existing[name].get("address"):
            _log("skip %s (already %s)" % (name, existing[name]["address"]))
            continue
        try:
            resp = post_action(wallet, {"type": "createSubAccount", "name": name})
        except Exception as e:
            _log("FAIL %s  %s" % (name, e))
            st["phase"] = "error"
            st["error"] = str(e)
            save_state(st)
            return st
        _log("HL response %s  %s" % (name, json.dumps(resp)[:240]))
        status = (resp or {}).get("status")
        inner = (resp or {}).get("response") or {}
        err = ""
        if isinstance(inner, dict):
            err = str(inner.get("data") or inner.get("error") or "")
        blob = json.dumps(resp).lower()
        if "100000" in blob or "100,000" in blob or "volume" in blob:
            _log("HL volume gate: subaccounts need $100k lifetime volume on the master.")
            _log("Keep this master, trade until volume unlocks, then press Create subaccounts again.")
            st["phase"] = "volume_gate"
            st["error"] = "HL requires $100k volume to create subaccounts"
            save_state(st)
            return st
        addr = None
        if status == "ok":
            data = inner.get("data") if isinstance(inner, dict) else inner
            if isinstance(data, str) and data.startswith("0x"):
                addr = data
            elif isinstance(data, dict):
                addr = data.get("subAccountUser") or data.get("address")
        if not addr:
            _log("could not parse sub address for %s — stopping (no more subs)" % name)
            st["phase"] = "error"
            st["error"] = err or json.dumps(resp)[:200]
            save_state(st)
            return st
        existing[name] = {"name": name, "address": addr, "use_vault": True}
        st["subs"] = [existing[n] for n in HUNTER_SUBS if n in existing]
        save_state(st)
        _log("SUB %s  %s" % (name, addr))
        time.sleep(0.4)
    st["phase"] = "done"
    st["copy"] = {"name": "Copy", "address": st["master"], "use_vault": False}
    save_state(st)
    _log("DONE  Copy=master  + %d subs. Paste into remote wallets (s). Do not use on the hub hunters." % len(st.get("subs") or []))
    return st


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd in ("generate", "new", "master"):
        generate_master()
        return 0
    if cmd in ("status", "refresh"):
        st = refresh()
        print(json.dumps({k: st.get(k) for k in st if k not in ("master_key", "api_agent_key")}, indent=2))
        return 0
    if cmd in ("create-subs", "subs"):
        create_subs()
        return 0
    if cmd == "log":
        p = os.path.join(OUT, "creator.log")
        if os.path.isfile(p):
            sys.stdout.write(open(p).read())
        return 0
    sys.stderr.write("usage: hl_wallet_creator.py generate|status|create-subs|log\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
