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

"""Park realized wins and move them to the RISK HL subaccount.

deduct() keeps parked USD off the hunter's tradable free.

maybe_send() only signs with the desk MASTER (Copy, use_vault=false).
Hunter API/vault agents cannot sendAsset ("Vault may not perform this
action"). Unified HL accounts reject subAccountTransfer / usdSend —
Copy flushes with master-signed sendAsset only (fromSubAccount = hunter).

Does not close or open positions. Amount is capped to HL withdrawable.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime
from contextlib import contextmanager
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, Optional, Set

CONF = os.environ.get("CONF_DIR") or (
    "/home/hummingbot/conf" if os.path.isdir("/home/hummingbot/conf") else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "conf"
    )
)
PATH = os.path.join(CONF, "risk_skim.json")
ACCOUNTS = os.path.join(CONF, "bot_accounts.json")
HL_EXCHANGE = "https://api.hyperliquid.xyz/exchange"
HL_INFO = "https://api.hyperliquid.xyz/info"
MIN_SEND = Decimal("1.00")
USDC_PERP = "USDC"
USDC_SPOT = "USDC:0x6d1e7cde53ba9467b783cb7c530ce054"


@contextmanager
def _locked():
    fd = open(PATH + ".lock", "a+")
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        fd.close()


def _load() -> Dict[str, Any]:
    try:
        with open(PATH, "r") as f:
            raw = f.read()
        try:
            data = json.loads(raw) or {}
        except json.JSONDecodeError:
            data = json.loads(raw, strict=False) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: Dict[str, Any]) -> None:
    hunters = data.get("hunters") if isinstance(data.get("hunters"), dict) else {}
    for row in hunters.values():
        if isinstance(row, dict) and isinstance(row.get("events"), list):
            row["events"] = row["events"][-16:]
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, PATH)


def cfg(bot_id: str) -> Optional[Dict[str, Any]]:
    data = _load()
    hunters = data.get("hunters") if isinstance(data.get("hunters"), dict) else {}
    row = hunters.get(bot_id)
    return row if isinstance(row, dict) else None


def parked_usd(bot_id: str) -> float:
    row = cfg(bot_id)
    if not row:
        return 0.0
    try:
        return max(0.0, float(row.get("parked_usd") or 0))
    except (TypeError, ValueError):
        return 0.0


def _keeps_wins(row: Optional[Dict[str, Any]]) -> bool:
    """Wins stay on the hunter. The loaded script treats any other mode as a
    percent skim, so OFF is also skim_pct 0 and parked_usd 0."""
    mode = str((row or {}).get("mode") or "").strip().lower()
    return mode in ("keep", "off", "none")


def deduct(bot_id: str, free) -> Decimal:
    """Subtract unsent parked RISK USD from free trading cash."""
    raw = Decimal(str(free or 0))
    if raw < 0:
        raw = Decimal("0")
    with _locked():
        row = cfg(bot_id)
        if _keeps_wins(row):
            return raw
        park = Decimal("0")
        if row:
            try:
                park = Decimal(str(row.get("parked_usd") or 0))
            except Exception:
                park = Decimal("0")
    out = raw - park
    return out if out > 0 else Decimal("0")


def _apply_trade(cfg: Dict[str, Any], pnl: float) -> Dict[str, Any]:
    """Same streak rules as the dashboard skim. Does not send.

    two_wins parks only the second win in a row. A loss resets the streak
    and leaves parked_usd alone.
    """
    cfg = dict(cfg or {})
    parked = float(cfg.get("parked_usd") or 0)
    mode = str(cfg.get("mode") or "win_pct")
    events = list(cfg.get("events") or [])[-39:]
    now = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    if pnl <= 0:
        cfg["streak"] = 0
        cfg["last_win_usd"] = 0.0
        return cfg
    if _keeps_wins(cfg):
        return cfg
    take = 0.0
    why = ""
    if mode == "two_wins":
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
        events.append({"ts": now, "take": round(take, 4), "pnl": round(float(pnl), 4), "why": why})
        cfg["parked_usd"] = round(parked, 4)
        cfg["events"] = events[-40:]
    return cfg


def apply_closes(bot_id: str, rows) -> bool:
    """Book closes newer than last_trade_key. Idempotent. Does not send."""
    if not bot_id or not rows:
        return False
    with _locked():
        data = _load()
        hunters = data.get("hunters") if isinstance(data.get("hunters"), dict) else {}
        cfg = hunters.get(bot_id)
        if not isinstance(cfg, dict):
            return False
        usable = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or "")
            if not key:
                continue
            try:
                pnl = float(row.get("pnl"))
            except (TypeError, ValueError):
                continue
            usable.append((key, pnl))
        if not usable:
            return False
        last = str(cfg.get("last_trade_key") or "")
        changed = False
        if not last:
            key, pnl = usable[-1]
            cfg["last_trade_key"] = key
            if pnl > 0:
                cfg["streak"] = 1
                cfg["last_win_usd"] = pnl
            else:
                cfg["streak"] = 0
                cfg["last_win_usd"] = 0.0
            changed = True
        else:
            for key, pnl in usable:
                if key <= last:
                    continue
                cfg = _apply_trade(cfg, pnl)
                cfg["last_trade_key"] = key
                last = key
                changed = True
        if not changed:
            return False
        hunters[bot_id] = cfg
        data["hunters"] = hunters
        data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        _save(data)
        return True


def _valid_addr(s: str) -> bool:
    s = (s or "").strip()
    if not s.startswith("0x") or len(s) != 42:
        return False
    try:
        int(s[2:], 16)
        return True
    except ValueError:
        return False


def _accounts() -> Dict[str, Dict[str, Any]]:
    try:
        with open(ACCOUNTS, "r") as f:
            data = json.load(f) or {}
        out = {}
        for row in data.get("bots") or []:
            if isinstance(row, dict) and row.get("id"):
                out[str(row["id"])] = row
        return out
    except Exception:
        return {}


def _hl_info(payload: dict) -> Any:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        HL_INFO, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8") or "null"
    return json.loads(raw)


def _sub_set(master: str) -> Set[str]:
    out: Set[str] = set()
    try:
        rows = _hl_info({"type": "subAccounts", "user": master})
    except Exception:
        return out
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        u = str(row.get("subAccountUser") or "").strip().lower()
        if u.startswith("0x") and len(u) == 42:
            out.add(u)
    return out


def _withdrawable(user: str) -> Decimal:
    try:
        data = _hl_info({"type": "clearinghouseState", "user": user}) or {}
    except Exception:
        return Decimal("0")
    try:
        return Decimal(str(data.get("withdrawable") or 0))
    except Exception:
        return Decimal("0")


def _ok(result) -> bool:
    return str((result or {}).get("status") or "").lower() in ("ok", "success")


def _err_text(result, prefix: str = "") -> str:
    raw = str(result)[:200]
    return ((prefix + " " if prefix else "") + raw).strip()[:240]


def _sign_usd_send(wallet, destination: str, amount: str, is_mainnet: bool = True):
    from eth_account.messages import encode_typed_data
    from eth_utils import to_hex

    nonce = int(time.time() * 1000)
    action = {
        "type": "usdSend",
        "destination": destination.lower(),
        "amount": amount,
        "time": nonce,
        "signatureChainId": "0x66eee",
        "hyperliquidChain": "Mainnet" if is_mainnet else "Testnet",
    }
    data = {
        "domain": {
            "name": "HyperliquidSignTransaction",
            "version": "1",
            "chainId": 0x66EEE,
            "verifyingContract": "0x0000000000000000000000000000000000000000",
        },
        "types": {
            "HyperliquidTransaction:UsdSend": [
                {"name": "hyperliquidChain", "type": "string"},
                {"name": "destination", "type": "string"},
                {"name": "amount", "type": "string"},
                {"name": "time", "type": "uint64"},
            ],
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
        },
        "primaryType": "HyperliquidTransaction:UsdSend",
        "message": {
            "hyperliquidChain": action["hyperliquidChain"],
            "destination": action["destination"],
            "amount": action["amount"],
            "time": nonce,
        },
    }
    signed = wallet.sign_message(encode_typed_data(full_message=data))
    sig = {"r": to_hex(signed["r"]), "s": to_hex(signed["s"]), "v": signed["v"]}
    return action, sig, nonce


def _sign_send_asset(wallet, destination: str, amount: str, from_sub: str, token: str, is_mainnet: bool = True):
    from eth_account.messages import encode_typed_data
    from eth_utils import to_hex

    nonce = int(time.time() * 1000)
    action = {
        "type": "sendAsset",
        "destination": destination.lower(),
        "sourceDex": "",
        "destinationDex": "",
        "token": token,
        "amount": amount,
        "fromSubAccount": (from_sub or "").lower(),
        "nonce": nonce,
        "signatureChainId": "0x66eee",
        "hyperliquidChain": "Mainnet" if is_mainnet else "Testnet",
    }
    data = {
        "domain": {
            "name": "HyperliquidSignTransaction",
            "version": "1",
            "chainId": 0x66EEE,
            "verifyingContract": "0x0000000000000000000000000000000000000000",
        },
        "types": {
            "HyperliquidTransaction:SendAsset": [
                {"name": "hyperliquidChain", "type": "string"},
                {"name": "destination", "type": "string"},
                {"name": "sourceDex", "type": "string"},
                {"name": "destinationDex", "type": "string"},
                {"name": "token", "type": "string"},
                {"name": "amount", "type": "string"},
                {"name": "fromSubAccount", "type": "string"},
                {"name": "nonce", "type": "uint64"},
            ],
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
        },
        "primaryType": "HyperliquidTransaction:SendAsset",
        "message": {
            "hyperliquidChain": action["hyperliquidChain"],
            "destination": action["destination"],
            "sourceDex": action["sourceDex"],
            "destinationDex": action["destinationDex"],
            "token": action["token"],
            "amount": action["amount"],
            "fromSubAccount": action["fromSubAccount"],
            "nonce": nonce,
        },
    }
    signed = wallet.sign_message(encode_typed_data(full_message=data))
    sig = {"r": to_hex(signed["r"]), "s": to_hex(signed["s"]), "v": signed["v"]}
    return action, sig, nonce


def _sign_l1(wallet, action, nonce, auth=None):
    if auth is not None and hasattr(auth, "sign_l1_action"):
        return auth.sign_l1_action(wallet, action, None, nonce, True)
    import msgpack
    from eth_account.messages import encode_typed_data
    from eth_utils import keccak, to_hex

    packed = msgpack.packb(action)
    packed += int(nonce).to_bytes(8, "big")
    packed += b"\x00"
    phantom = {"source": "a", "connectionId": keccak(packed)}
    data = {
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
    signed = wallet.sign_message(encode_typed_data(full_message=data))
    return {"r": to_hex(signed["r"]), "s": to_hex(signed["s"]), "v": signed["v"]}


def _post_hl(action, signature, nonce) -> dict:
    payload = {
        "action": action,
        "nonce": nonce,
        "signature": signature,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        HL_EXCHANGE, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8") or "{}"
    try:
        return json.loads(raw)
    except Exception:
        return {"status": "err", "response": raw[:300]}


def _auth_from_connector(connector):
    auth = None
    if connector is None:
        return None, None, None, None
    if hasattr(connector, "authenticator"):
        try:
            auth = connector.authenticator
        except Exception:
            auth = None
    if auth is None:
        auth = getattr(connector, "_auth", None)
    if auth is None:
        secret = getattr(connector, "hyperliquid_perpetual_secret_key", None)
        addr = getattr(connector, "hyperliquid_perpetual_address", None)
        use_vault = bool(getattr(connector, "_use_vault", False))
        if secret:
            import eth_account
            wallet = eth_account.Account.from_key(secret)
            vault = addr if use_vault else None
            return wallet, vault, addr, None
        return None, None, None, None
    wallet = getattr(auth, "wallet", None)
    vault = getattr(auth, "_vault_address", None)
    addr = getattr(auth, "_api_address", None)
    return wallet, vault, addr, auth


def _usd_int(amt: Decimal) -> int:
    return int((amt * Decimal("1000000")).to_integral_value(rounding=ROUND_DOWN))


def _mark_err(data, hunters, bot_id, row, amt, dest, err, now, ts, extra=None):
    events = list(row.get("events") or [])[-39:]
    ev = {"ts": ts, "send": float(amt), "error": err, "dest": dest}
    if extra:
        ev.update(extra)
    events.append(ev)
    row["last_send_try"] = now
    row["last_send_error"] = err
    row["events"] = events
    hunters[bot_id] = row
    data["hunters"] = hunters
    _save(data)
    return err


def _mark_ok(data, hunters, bot_id, row, amt, dest, why, now, ts, extra=None):
    parked = Decimal(str(row.get("parked_usd") or 0))
    sent = Decimal(str(row.get("sent_usd") or 0))
    events = list(row.get("events") or [])[-39:]
    ev = {"ts": ts, "send": float(amt), "dest": dest, "why": why}
    if extra:
        ev.update(extra)
    events.append(ev)
    row["sent_usd"] = float(sent + amt)
    row["parked_usd"] = float(max(Decimal("0"), parked - amt))
    row["last_send_error"] = ""
    row["last_send_ts"] = ts
    row["last_send_try"] = now
    row.pop("send_inflight", None)
    row["events"] = events
    hunters[bot_id] = row
    data["hunters"] = hunters
    data["updated"] = ts
    _save(data)
    return "sent " + format(amt, "f") + " to " + dest[:10] + " via " + why


def _try_send_asset(wallet, dest: str, amount: str, from_sub: str, token: str):
    action, sig, nonce = _sign_send_asset(wallet, dest, amount, from_sub, token)
    return _post_hl(action, sig, nonce)


def _try_agent_send_asset(wallet, dest: str, amount: str, from_sub: str, token: str, auth):
    nonce = int(time.time() * 1000)
    action = {
        "type": "agentSendAsset",
        "destination": dest.lower(),
        "sourceDex": "",
        "destinationDex": "",
        "token": token,
        "amount": amount,
        "fromSubAccount": (from_sub or "").lower() if from_sub else "",
        "nonce": nonce,
    }
    sig = _sign_l1(wallet, action, nonce, auth)
    return _post_hl(action, sig, nonce)


def _send_asset_only(wallet, dest: str, amount: str, from_sub: str, auth=None):
    """Unified accounts: agentSendAsset (Copy API wallet) then sendAsset.
    Never subAccountTransfer / usdSend. Does not close positions."""
    errors = []
    for token in (USDC_PERP, USDC_SPOT):
        try:
            result = _try_agent_send_asset(wallet, dest, amount, from_sub, token, auth)
            if _ok(result):
                return None, "agentSendAsset " + token
            errors.append(_err_text(result, "agentSendAsset " + token))
        except Exception as e:
            errors.append(("agentSendAsset " + token + " " + str(e))[:200])
    for token in (USDC_PERP, USDC_SPOT):
        try:
            result = _try_send_asset(wallet, dest, amount, from_sub, token)
            if _ok(result):
                return None, "sendAsset " + token
            errors.append(_err_text(result, "sendAsset " + token))
        except Exception as e:
            errors.append(("sendAsset " + token + " " + str(e))[:200])
    return " | ".join(errors)[:240], None


def _cap_amt(wd: Decimal, want: Decimal) -> Decimal:
    buffer = Decimal("0.50")
    cap = (wd - buffer) if wd > buffer else Decimal("0")
    cap = cap.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    amt = min(want, cap).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return amt


def _send_one(wallet, master: str, auth, bot_id: str, row: dict, hunters: dict, data: dict, subs: Set[str]) -> Optional[str]:
    if _keeps_wins(row):
        return None
    dest = str(row.get("address") or "").strip()
    if not _valid_addr(dest):
        return None
    dest_l = dest.lower()
    master_l = (master or "").lower()
    now = time.time()
    ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    inflight = row.get("send_inflight") if isinstance(row.get("send_inflight"), dict) else None

    try:
        parked = Decimal(str(row.get("parked_usd") or 0))
    except Exception:
        return None
    last_try = float(row.get("last_send_try") or 0)
    last_err = str(row.get("last_send_error") or "")
    unified = "unified account" in last_err.lower() or "inflight deposit" in last_err.lower()
    vault_fail = "Vault may not" in last_err or "Must deposit" in last_err
    force = bool(row.get("force_send"))
    if force:
        row["force_send"] = False
    if not inflight and now - last_try < 60 and not vault_fail and not unified and not force:
        return None
    if dest_l == master_l:
        return _mark_err(data, hunters, bot_id, row, parked, dest, "RISK dest is the master wallet, not a sub", now, ts)

    acc = _accounts().get(bot_id) or {}
    hunter_is_master = not bool(acc.get("use_vault", True))
    hunter_sub = str(acc.get("address") or "").strip().lower()
    if hunter_is_master:
        hunter_sub = ""
    elif not _valid_addr(hunter_sub):
        return _mark_err(data, hunters, bot_id, row, parked, dest, "no hunter sub address in bot_accounts.json", now, ts)
    if hunter_sub and dest_l == hunter_sub:
        return _mark_err(data, hunters, bot_id, row, parked, dest, "RISK address is this hunter's own sub", now, ts)

    dest_is_sub = dest_l in subs if subs else True
    if subs and not dest_is_sub:
        return _mark_err(
            data, hunters, bot_id, row, parked, dest,
            "RISK dest is not a listed HL sub of master " + (master[:10] if master else "?"),
            now, ts,
        )

    msgs = []

    # Stuck two-step: USDC already on Copy master. Finish with sendAsset, not subAccountTransfer.
    if inflight and inflight.get("stage") == "to_master":
        try:
            want = Decimal(str(inflight.get("usd") or 0)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        except Exception:
            want = Decimal("0")
        wd_m = _withdrawable(master_l)
        amt = _cap_amt(wd_m, want)
        if amt < MIN_SEND:
            row.pop("send_inflight", None)
            hunters[bot_id] = row
            data["hunters"] = hunters
            _save(data)
            inflight = None
        else:
            extra = {"from_sub": "master", "usd_int": _usd_int(amt)}
            err, why = _send_asset_only(wallet, dest, format(amt, "f"), "", auth)
            if why:
                msgs.append(_mark_ok(data, hunters, bot_id, row, amt, dest, why + " (inflight master)", now, ts, extra))
                row = hunters.get(bot_id) or row
                inflight = None
                try:
                    parked = Decimal(str(row.get("parked_usd") or 0))
                except Exception:
                    parked = Decimal("0")
            else:
                return _mark_err(data, hunters, bot_id, row, amt, dest, err or "sendAsset inflight failed", now, ts, extra)

    due = parked
    if due < MIN_SEND:
        if force:
            hunters[bot_id] = row
            data["hunters"] = hunters
            _save(data)
        return "; ".join(msgs) if msgs else None

    src = hunter_sub or master_l
    wd = _withdrawable(src)
    amt = _cap_amt(wd, due)
    if amt < MIN_SEND:
        return _mark_err(
            data, hunters, bot_id, row, due, dest,
            "sub withdrawable $" + format(wd, "f") + " — not sending (open margin stays)",
            now, ts, {"from_sub": hunter_sub or "master"},
        )

    extra = {"from_sub": hunter_sub or "master", "usd_int": _usd_int(amt)}
    err, why = _send_asset_only(wallet, dest, format(amt, "f"), hunter_sub, auth)
    if why:
        msgs.append(_mark_ok(data, hunters, bot_id, row, amt, dest, why, now, ts, extra))
        return "; ".join(msgs)
    return _mark_err(data, hunters, bot_id, row, amt, dest, err or "sendAsset failed", now, ts, extra)


def maybe_send(bot_id: str, connector) -> Optional[str]:
    """Move parked RISK USD. Vault hunters no-op; Copy master flushes every hunter."""
    wallet, vault, src_addr, auth = _auth_from_connector(connector)
    if wallet is None:
        return None
    if vault:
        return None
    with _locked():
        return _maybe_send_locked(bot_id, wallet, vault, src_addr, auth)


def _maybe_send_locked(bot_id: str, wallet, vault, src_addr, auth) -> Optional[str]:
    data = _load()
    hunters = data.get("hunters") if isinstance(data.get("hunters"), dict) else {}
    if not hunters:
        return None
    master = str(src_addr or "").strip()
    if not _valid_addr(master):
        acc = _accounts().get("hummingbot-hl") or {}
        master = str(acc.get("address") or "").strip()
    subs = _sub_set(master) if _valid_addr(master) else set()
    msgs = []
    for bid, row in list(hunters.items()):
        if not isinstance(row, dict):
            continue
        try:
            msg = _send_one(wallet, master, auth, bid, row, hunters, data, subs)
        except Exception as e:
            msg = str(e)[:200]
            try:
                row["last_send_error"] = msg
                row["last_send_try"] = time.time()
                hunters[bid] = row
                data["hunters"] = hunters
                _save(data)
            except Exception:
                pass
        if msg:
            msgs.append(str(bid) + ": " + str(msg))
    return "; ".join(msgs) if msgs else None


def _wallet_from_copy_yml():
    """Decrypt Copy's master key from the connector yml. Copy container only."""
    import re
    from eth_account import Account

    yml = os.path.join(CONF, "connectors", "hyperliquid_perpetual.yml")
    with open(yml, "r") as f:
        text = f.read()
    m_addr = re.search(r"(?m)^hyperliquid_perpetual_address:\s*(\S+)", text)
    m_key = re.search(r"(?m)^hyperliquid_perpetual_secret_key:\s*(\S+)", text)
    if not m_addr or not m_key:
        raise RuntimeError("Copy connector yml missing address/secret")
    pw = os.environ.get("CONFIG_PASSWORD") or os.environ.get("HL_PW") or "Dan2Delion"

    def _dec(hexstr: str) -> bytes:
        raw = bytes.fromhex(hexstr.strip())
        kf = json.loads(raw.decode("utf-8"))
        return Account.decrypt(kf, pw)

    addr = _dec(m_addr.group(1)).decode("utf-8")
    secret = _dec(m_key.group(1)).decode("utf-8")
    wallet = Account.from_key(secret)
    return wallet, addr


def flush_from_copy_yml() -> str:
    """Master-signed send of parked RISK USD. Does not close positions."""
    wallet, addr = _wallet_from_copy_yml()

    class _Auth:
        pass

    auth = _Auth()
    auth.wallet = wallet
    auth._vault_address = None
    auth._api_address = addr

    class _Conn:
        pass

    conn = _Conn()
    conn.authenticator = auth
    return maybe_send("hummingbot-hl", conn) or "nothing due"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "flush":
        print(flush_from_copy_yml() or "ok")
