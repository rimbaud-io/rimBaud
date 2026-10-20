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

"""Copy 6h digest → Positions Telegram bot. Does not trade or flatten.

Window: last 6 hours (aligned to 00/06/12/18 Europe/Berlin).
Sends WIN/LOSS, open book, HL funding, REST IP budget.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

CONF_DIR = os.environ.get("CONF_DIR", "/conf")
LOGS_DIR = os.environ.get("LOGS_DIR", "/logs")
STATE_FILE = os.path.join(CONF_DIR, "copy_digest_state.json")
SETUP_FILE = os.path.join(CONF_DIR, "condor_setup.json")
GRAPH_FILE = os.path.join(CONF_DIR, "wire_graph.json")
ACCOUNTS_FILE = os.path.join(CONF_DIR, "bot_accounts.json")
STATUS_FILE = os.path.join(CONF_DIR, "hl_copy_status.json")
BUDGET_FILE = os.path.join(CONF_DIR, "hl_ip_budget.json")
PNL_FILE = os.path.join(CONF_DIR, "hl_copy_pnl.json")
COPY_LOG = os.path.join(LOGS_DIR, "copy_trades.log")
TZ = ZoneInfo("Europe/Berlin")
WINDOW_H = 6
COPY_ID = "hummingbot-hl"
HL = "https://api.hyperliquid.xyz/info"
POSITIONS_NAME = "positionen"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _berlin_now() -> datetime:
    return datetime.now(TZ)


def _window(now: Optional[datetime] = None) -> Tuple[datetime, datetime, str]:
    now = now or _berlin_now()
    hour = (now.hour // WINDOW_H) * WINDOW_H
    aligned = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    start = aligned - timedelta(hours=WINDOW_H)
    slot = aligned.strftime("%Y-%m-%d") + f"-{aligned.hour:02d}"
    return start, now, slot


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _esc(s: Any) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _hl(payload: dict, timeout: int = 25) -> Any:
    req = urllib.request.Request(
        HL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def copy_wallet() -> str:
    data = _read_json(ACCOUNTS_FILE)
    bots = data.get("bots") or data.get("accounts") or []
    if isinstance(bots, dict):
        row = bots.get(COPY_ID) or {}
        addr = str(row.get("address") or "").strip()
        if addr:
            return addr
        bots = list(bots.values())
    if isinstance(bots, list):
        for row in bots:
            if isinstance(row, dict) and str(row.get("id") or "") == COPY_ID:
                return str(row.get("address") or "").strip()
    st = _read_json(STATUS_FILE)
    return str(st.get("wallet") or st.get("subaccount") or "").strip()


def _parse_chats(v) -> List[str]:
    if isinstance(v, list):
        parts = v
    else:
        parts = str(v or "").replace(";", ",").split(",")
    return [str(p).strip() for p in parts if str(p).strip()]


def positions_bots() -> List[dict]:
    data = _read_json(SETUP_FILE)
    bots = data.get("bots") if isinstance(data.get("bots"), dict) else {}
    fallback: List[str] = []
    for row in bots.values():
        if isinstance(row, dict):
            fallback.extend(_parse_chats(row.get("chat_ids")))
    fallback = list(dict.fromkeys(fallback))
    graph = _read_json(GRAPH_FILE)
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    by_id = {str(n.get("id")): n for n in nodes if n.get("id")}
    pos_ids = set()
    for e in edges:
        if str(e.get("sourceHandle") or "") != "logs":
            continue
        tgt = by_id.get(str(e.get("target") or ""))
        if not tgt:
            continue
        td = tgt.get("data") if isinstance(tgt.get("data"), dict) else {}
        if str(td.get("kind") or "telegram") == "grok":
            continue
        if tgt.get("type") == "agent" or str(td.get("kind") or "") == "telegram":
            pos_ids.add(str(tgt.get("id")))
    out = []
    seen = set()
    for nid in list(pos_ids) + [k for k in bots if POSITIONS_NAME in str((bots.get(k) or {}).get("name") or "").lower()]:
        if nid in seen:
            continue
        row = bots.get(nid) if isinstance(bots.get(nid), dict) else {}
        token = str(row.get("token") or "").strip()
        if not token:
            continue
        name = str(row.get("name") or nid)
        if POSITIONS_NAME not in name.lower() and nid not in pos_ids:
            continue
        chats = _parse_chats(row.get("chat_ids")) or list(fallback)
        if not chats:
            continue
        seen.add(nid)
        out.append({"id": nid, "name": name, "token": token, "chats": chats})
    if not out:
        for nid, row in bots.items():
            if not isinstance(row, dict):
                continue
            if POSITIONS_NAME not in str(row.get("name") or "").lower():
                continue
            token = str(row.get("token") or "").strip()
            chats = _parse_chats(row.get("chat_ids")) or list(fallback)
            if token and chats:
                out.append({"id": nid, "name": str(row.get("name") or nid), "token": token, "chats": chats})
    return out


def tg_send(bot: dict, text: str) -> bool:
    token = str(bot.get("token") or "").strip()
    ok_any = False
    for cid in bot.get("chats") or []:
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=json.dumps({
                    "chat_id": cid,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                out = json.loads(r.read().decode() or "{}")
            if out.get("ok"):
                ok_any = True
        except Exception as e:
            sys.stderr.write(f"tg send {bot.get('id')} {cid} failed: {e}\n")
    return ok_any


def fills_pnl(user: str, start_ms: int, end_ms: int) -> dict:
    fills = _hl({
        "type": "userFillsByTime",
        "user": user,
        "startTime": start_ms,
        "endTime": end_ms,
    })
    if not isinstance(fills, list):
        fills = []
    win = loss = fee = 0.0
    n_win = n_loss = 0
    by: Dict[str, float] = {}
    for f in fills:
        pnl = float(f.get("closedPnl") or 0)
        fee += float(f.get("fee") or 0)
        coin = str(f.get("coin") or "?")
        by[coin] = by.get(coin, 0.0) + pnl
        if pnl > 1e-9:
            win += pnl
            n_win += 1
        elif pnl < -1e-9:
            loss += pnl
            n_loss += 1
    return {
        "n_fills": len(fills),
        "win": win,
        "loss": loss,
        "n_win": n_win,
        "n_loss": n_loss,
        "fee": fee,
        "net": win + loss,
        "net_after_fee": win + loss - fee,
        "by_coin": by,
    }


def funding_pnl(user: str, start_ms: int) -> dict:
    rows = _hl({"type": "userFunding", "user": user, "startTime": start_ms})
    if not isinstance(rows, list):
        rows = []
    paid = recvd = 0.0
    n = 0
    for r in rows:
        delta = r.get("delta") if isinstance(r.get("delta"), dict) else r
        try:
            usdc = float((delta or {}).get("usdc") or r.get("usdc") or 0)
        except (TypeError, ValueError):
            continue
        n += 1
        if usdc < 0:
            paid += usdc
        else:
            recvd += usdc
    return {"n": n, "paid": paid, "received": recvd, "net": paid + recvd}


def book(user: str) -> dict:
    st = _hl({"type": "clearinghouseState", "user": user})
    ms = st.get("marginSummary") or {}
    clips = []
    upnl = 0.0
    for ap in st.get("assetPositions") or []:
        p = (ap or {}).get("position") or {}
        try:
            szi = float(p.get("szi") or 0)
        except (TypeError, ValueError):
            continue
        if abs(szi) < 1e-12:
            continue
        u = float(p.get("unrealizedPnl") or 0)
        upnl += u
        clips.append({
            "coin": p.get("coin"),
            "szi": szi,
            "side": "LONG" if szi > 0 else "SHORT",
            "u": u,
            "roe": float(p.get("returnOnEquity") or 0) * 100.0 if p.get("returnOnEquity") is not None else None,
        })
    clips.sort(key=lambda x: abs(x["u"]), reverse=True)
    return {
        "equity": float(ms.get("accountValue") or 0),
        "margin": float(ms.get("totalMarginUsed") or 0),
        "ntl": float(ms.get("totalNtlPos") or 0),
        "free": float(st.get("withdrawable") or 0),
        "upnl": upnl,
        "n": len(clips),
        "clips": clips[:12],
    }


def ip_snap() -> dict:
    now = time.time()
    try:
        data = json.load(open(BUDGET_FILE))
    except Exception:
        data = {}
    cap = int(data.get("cap") or 1200)
    used = 0
    by: Dict[str, int] = {}
    last_429 = data.get("last_429")
    cut = now - 60.0
    for item in data.get("events") or []:
        try:
            t, w = float(item[0]), int(item[1])
            src = str(item[2]) if len(item) > 2 else "?"
        except (TypeError, ValueError, IndexError):
            continue
        if t < cut:
            continue
        used += w
        by[src] = by.get(src, 0) + w
    left = max(0, cap - used)
    last_429_s = None
    try:
        if last_429:
            last_429_s = max(0, int(now - float(last_429)))
    except (TypeError, ValueError):
        pass
    return {
        "used": used,
        "left": left,
        "cap": cap,
        "pct": round(min(100.0, used / cap * 100.0), 1) if cap else 0,
        "by": by,
        "last_429_s": last_429_s,
        "note": "shared public IP · 1200 weight / 60s — Copy needs this headroom to poll fills and keep margin live",
    }


def refresh_copy_pnl_cache() -> dict:
    """All-time Copy W/L from WE CLOSE CLOSED lines. Last two closes for the desk."""
    import re
    path = COPY_LOG
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    prev = _read_json(PNL_FILE)
    try:
        if prev and float(prev.get("log_mtime") or 0) >= mtime - 0.01:
            return prev
    except (TypeError, ValueError):
        pass
    ansi = re.compile(r"\x1b\[[0-9;]*m")
    pat = re.compile(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s+WE CLOSE\s+CLOSED\s+"
        r"(LONG|SHORT)\s+(\S+)\s+([\d.]+)\s+\$\s*([\d.]+).*?entry=\$([\d.]+)",
        re.I,
    )
    roe_pat = re.compile(r"PnL~([+\-0-9.]+)%", re.I)
    n_win = n_loss = n_close = 0
    realized = 0.0
    last: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = ansi.sub("", raw)
                m = pat.search(line)
                if not m:
                    continue
                ts, side, coin, amt_s, px_s, ent_s = m.groups()
                amt = float(amt_s)
                px = float(px_s)
                ent = float(ent_s)
                rm = roe_pat.search(line)
                roe = float(rm.group(1)) if rm else 0.0
                if side.upper() == "SHORT":
                    usd = amt * (ent - px)
                else:
                    usd = amt * (px - ent)
                n_close += 1
                realized += usd
                if usd > 1e-9 or roe > 1e-9:
                    n_win += 1
                    kind = "WIN"
                elif usd < -1e-9 or roe < -1e-9:
                    n_loss += 1
                    kind = "LOSS"
                else:
                    kind = "FLAT"
                last.append({
                    "ts": ts, "action": "CLOSE", "side": side.upper(), "coin": coin,
                    "amount": amt, "price": px, "entry_price": ent,
                    "pnl_usd": round(usd, 4), "roe_pct": roe, "result": kind,
                })
                if len(last) > 8:
                    last = last[-8:]
    except OSError:
        pass
    pack = {
        "log_mtime": mtime,
        "updated": _utc_now().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "n_wins": n_win,
        "n_losses": n_loss,
        "n_closes": n_close,
        "n_opens": 0,
        "realized_pnl_usd": round(realized, 4),
        "last_closes": last[-2:],
    }
    try:
        _write_json(PNL_FILE, pack)
    except Exception:
        pass
    try:
        from matrix_store import ensure, kv_set
        ensure()
        kv_set(COPY_ID, "copy_pnl", {
            "n_wins": n_win, "n_losses": n_loss, "n_closes": n_close,
            "realized_pnl_usd": pack["realized_pnl_usd"],
            "updated": pack["updated"],
        }, source="copy_digest")
    except Exception:
        pass
    return pack


def fmt_usd(v: Optional[float], signed: bool = True) -> str:
    if v is None:
        return "n/a"
    if signed:
        return f"{v:+,.2f}"
    return f"{v:,.2f}"


def build_text(start: datetime, end: datetime, pnl: dict, fund: dict, acc: dict, ip: dict, st: dict, all_time: dict) -> str:
    lines = [
        f"<b>Copy</b> {start.strftime('%d.%m %H:%M')}–{end.strftime('%H:%M')} {_esc(end.tzname())}",
        "",
        "<b>All-time</b>",
        f"{all_time.get('n_wins', 0)}W / {all_time.get('n_losses', 0)}L  "
        f"earned {_esc(fmt_usd(all_time.get('realized_pnl_usd')))} USDC  "
        f"({all_time.get('n_closes', 0)} closes)",
        "",
        "<b>This 6h WIN / LOSS</b>",
        f"WIN  {_esc(fmt_usd(pnl['win']))}  ({pnl['n_win']})",
        f"LOSS {_esc(fmt_usd(pnl['loss']))}  ({pnl['n_loss']})",
        f"NET  {_esc(fmt_usd(pnl['net_after_fee']))} after fees",
        "",
        "<b>Last 2 closes</b>",
    ]
    closes = list(all_time.get("last_closes") or [])[-2:]
    if not closes:
        lines.append("none")
    for c in closes:
        lines.append(
            f"{_esc(c.get('ts',''))}  {_esc(c.get('result') or 'CLOSE')}  "
            f"{_esc(c.get('side'))} {_esc(c.get('coin'))}  "
            f"{_esc(fmt_usd(c.get('pnl_usd')))}  {c.get('roe_pct') or 0:+.1f}%"
        )
    lines += [
        "",
        f"open {acc['n']} clips  uPnL {_esc(fmt_usd(acc['upnl']))}  "
        f"equity {_esc(fmt_usd(acc['equity'], False))}",
        f"funding net {_esc(fmt_usd(fund['net']))}  "
        f"REST {ip['used']}/{ip['cap']} ({ip['pct']}%)",
    ]
    return "\n".join(lines)


def already_sent(slot: str) -> bool:
    st = _read_json(STATE_FILE)
    return str(st.get("last_slot") or "") == slot


def mark_sent(slot: str, ok: bool) -> None:
    _write_json(STATE_FILE, {
        "last_slot": slot,
        "ok": ok,
        "sent_at": _utc_now().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


def main() -> int:
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    start, end, slot = _window()
    if (not dry) and (not force) and already_sent(slot):
        print("skip already sent", slot)
        return 0
    user = copy_wallet()
    if not user:
        sys.stderr.write("no copy wallet\n")
        return 2
    start_ms = int(start.astimezone(timezone.utc).timestamp() * 1000)
    end_ms = int(end.astimezone(timezone.utc).timestamp() * 1000)
    pnl = fills_pnl(user, start_ms, end_ms)
    fund = funding_pnl(user, start_ms)
    acc = book(user)
    ip = ip_snap()
    st = _read_json(STATUS_FILE)
    all_time = refresh_copy_pnl_cache()
    text = build_text(start, end, pnl, fund, acc, ip, st, all_time)
    bots = positions_bots()
    if dry:
        print(text.replace("<b>", "").replace("</b>", ""))
        print("--- bots", [(b["id"], b["name"], b["chats"]) for b in bots])
        return 0
    if not bots:
        sys.stderr.write("no Positions telegram bot\n")
        return 3
    ok = False
    for b in bots:
        if tg_send(b, text):
            ok = True
    mark_sent(slot, ok)
    print("sent" if ok else "send failed", slot, "bots", len(bots))
    return 0 if ok else 4


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
