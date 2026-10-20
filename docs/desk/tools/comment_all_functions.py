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

"""Add a visible // (C++) or # (Python) comment on EVERY function in doku/copy.

Live production sources are never touched. Idempotent.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "copy"

SKIP_NAMES = {
    "json::parse",
    "std::strftime",
    "std::rename",
    "std::strcmp",
    "std::string",
    "std::thread",
    "s",
    "in",
    "out",
    "iss",
}

# Exact comments for names we know. Prefix rules fill the rest.
KNOWN: dict[str, str] = {
    "main": "Program entry. Parse flags, then start the window or server.",
    "run_desk": "X11 matrix loop: poll Spring, draw pages, handle BIAS / Close / 100% / knobs.",
    "dump_headless": "GET /api/home once, print a summary, exit. No window.",
    "run_server": "Listen on 127.0.0.1 and dispatch each HTTP request to Brain::handle.",
    "Brain::handle": "HTTP router. GET always on. POST needs WRITE MODE. Close/100% are the flatten/open paths.",
    "handle": "HTTP router for this brain. See the .cpp definition for routes.",
    "http_request": "Blocking HTTP/1.1 GET/POST. Used for Spring on :5004.",
    "https_request": "TLS GET/POST for Binance klines and Hyperliquid candles (chart only).",
    "timed_ms": "Set socket send/recv timeout in milliseconds.",
    "timed": "Wait until the socket is ready or the timeout elapses.",
    "sock_fast": "TCP_NODELAY so small Spring GETs return quickly.",
    "connect_host": "TCP connect with timeout. Returns fd or -1.",
    "tcp_connect": "TCP connect used before SSL_connect (chart).",
    "ssl_once": "Initialize OpenSSL once per process.",
    "decode_chunked": "Decode Transfer-Encoding: chunked.",
    "inside": "Hit-test: true if pixel (x,y) is inside rectangle r.",
    "rects_overlap": "True if two rectangles overlap.",
    "norm_rect": "Build a rectangle from two corners (any order).",
    "rgb": "Allocate an X11 pixel from 8-bit R,G,B.",
    "shade": "Darken/lighten an RGB colour by factor k.",
    "parse_hex": "Parse #rrggbb into an X11 pixel.",
    "parse_hex_rgb": "Parse #rrggbb into r,g,b ints.",
    "money": "Format a USD amount for the desk ($1,234.56).",
    "signed_money": "Format USD with an explicit + or − sign.",
    "ascii": "Strip non-ASCII so X11 8x13 font can draw the string.",
    "strip_ansi": "Remove ANSI colour codes from a log line.",
    "fit_px": "Truncate a string to roughly px pixels.",
    "short_addr": "0xabc…def short wallet for Copy T# labels.",
    "pct_s": "Format a percent with sign.",
    "pct1": "Format one-decimal percent.",
    "follow_coin": "Coin Copy is currently mirroring for this hunter.",
    "hunter_tag": "Short hunter label (Copy, PMM, DMan Maker, …).",
    "pmm_from_x": "PMM SCALE x → derived TP / ARM / trail / NEXT / SL / margin.",
    "pmm_couple": "Keep PMM knobs coupled when the operator drags one pot.",
    "pmm_apply_bot": "Write coupled PMM values back onto the Bot.",
    "pmm_read": "Read PMM display knobs from the Bot controller JSON.",
    "pmm_store": "Serialize PMM display knobs to JSON for POST.",
    "pmm_clamp": "Clamp a PMM number into [lo, hi].",
    "pmm_trail_display": "Trail % of ARM as shown on the desk (not raw price %).",
    "pmm_price_delta": "Price distance implied by current PMM trail/ARM.",
    "pmm_values_for_save": "Controller map to persist for PMM Simple.",
    "hunter_values_for_save": "Controller map to persist for this hunter.",
    "preset_is_std": "True if the preset name is the locked STD pack.",
    "fill": "X11 filled rectangle.",
    "stroke": "X11 stroked rectangle.",
    "text": "X11 string at (x,y) in the current GC font.",
    "text_font": "X11 string with an explicit font.",
    "txt": "Draw a label (chart / meta helper).",
    "txt2": "Draw a second-line label.",
    "draw_home": "Paint the Home page (hunters, clips, leftover, Close / 100%).",
    "draw_wire": "Paint the Wire canvas (nodes, edges, ports).",
    "draw_condor": "Paint the Condor tape / gauge page.",
    "draw_hunters": "Paint the Hunters knob page.",
    "draw_funds": "Paint the Funds / RISK page.",
    "draw_strategy": "Paint the Strategy / QA page.",
    "draw_system": "Paint the System / backup / version page.",
    "draw_hover": "Highlight the control under the cursor.",
    "draw_sl_strip": "Home SL chips (40 / 60 / 80 / custom).",
    "draw_knob": "One hunter pot.",
    "draw_pmm_knob": "PMM SCALE-coupled pot.",
    "draw_port_tag": "Wire port label next to a node.",
    "draw_edge": "Bezier / stub between two Wire ports.",
    "node_w": "Wire node width (fat hunters vs tools).",
    "node_h": "Wire node height.",
    "node_view_box": "Screen-space box of a Wire node after pan/zoom.",
    "layout_ports": "Port dots on a Wire node.",
    "handles_ok": "True if two Wire handles may connect.",
    "best_link": "Pick the best src/tgt handles between two nodes.",
    "find_node": "Index of a Wire node by id, or -1.",
    "uniq_id": "Make a unique Wire node id from a base string.",
    "near_seg": "True if (x,y) is close to a line segment (edge hit).",
    "refresh": "Pull a fresh DeskSnap from Spring.",
    "say": "Status-line toast at the bottom of the window.",
    "now": "Monotonic seconds (for hover delays, toasts).",
    "mono_now": "Monotonic seconds (chart).",
    "goto_page": "Switch desk page (keys 1–7 / header tabs).",
    "redraw": "Full repaint of the current page.",
    "action_tip": "Status-line hint for the hit under the cursor.",
    "bot_live_roe": "Live ROE of this hunter’s open clip, if any.",
    "sl_match": "True if current SL matches a preset chip.",
    "post_knob": "POST one live knob to Spring.",
    "begin_knob_edit": "Start dragging a pot.",
    "commit_knob_edit": "Finish drag and POST the value.",
    "run_compile": "Wire compile (does not flatten clips).",
    "switch_backend": "Desk backend switch (production stays on Spring :5004).",
    "FlaskApi::can_post": "Write gate. False → POST helpers toast and skip HTTP.",
    "FlaskApi::ping": "Cheap GET to see if Spring is up.",
    "FlaskApi::use_flask": "Historical: point at Flask :5001. Production desk refuses this port.",
    "FlaskApi::use_cpp": "Point the desk at Spring (default :5004).",
    "FlaskApi::home": "GET /api/home and fill DeskSnap.",
    "FlaskApi::load_fields": "Controller knob catalog for Wire / hunter strips.",
    "FlaskApi::load_wire": "GET one Wire board snapshot.",
    "FlaskApi::load_qa": "GET Strategy / weekly QA.",
    "FlaskApi::load_backup": "GET System backup listing.",
    "FlaskApi::load_version": "GET Archy version + deploy stamps.",
    "FlaskApi::post_sl": "POST stop-loss ROE for one hunter (Spring queues SQL).",
    "FlaskApi::post_restart": "Restart a hunter container. Clips stay on Hyperliquid.",
    "FlaskApi::post_desk_bias": "Desk-wide BIAS. Does not flatten clips.",
    "FlaskApi::post_bias": "Per-hunter BIAS LONG/SHORT/FLAT. Does not flatten.",
    "FlaskApi::post_close_coin": "Operator Close — the only flatten path from the desk.",
    "FlaskApi::post_open100": "Operator 100% leftover. Same-side already-open may scale.",
    "FlaskApi::post_wire_pack": "Wire compile / integrate / save.",
    "FlaskApi::post_ctrl": "POST one live knob.",
    "FlaskApi::post_ctrl_map": "POST many knobs (PMM SCALE x).",
    "FlaskApi::post_copy_mute": "Mute a Copy leader. Open mirrors stay.",
    "FlaskApi::post_copy_slots": "How many coins that Copy leader may fill.",
    "FlaskApi::load_copy_audit": "GET read-only Copy leader audit.",
    "FlaskApi::run_copy_audit": "Trigger an audit run (read-only vs HL).",
    "FlaskApi::post_live_knobs": "Publish RAM knobs for the chart overlays.",
    "FlaskApi::pull_live_knobs": "Read RAM knobs back onto a Bot.",
    "FlaskApi::load_presets": "GET per-hunter presets (never global).",
    "FlaskApi::save_preset": "Save a named preset for one hunter.",
    "FlaskApi::apply_preset": "Apply a named preset. Flatten-guard still applies.",
    "FlaskApi::compile_wire": "Compile the Wire graph. Does not change clips.",
    "FlaskApi::integrate_wire": "Integrate routing/SL map only if compile says it will not close clips.",
    "FlaskApi::save_snapshot": "Save Wire snapshot JSON.",
    "FlaskApi::save_layout": "Save node positions.",
    "FlaskApi::sleeper_start": "Start a V2 sleeper (max 2 running).",
    "FlaskApi::sleeper_stop": "Stop a V2 sleeper. Does not flatten.",
    "FlaskApi::graph_json": "Serialize the on-screen Wire graph to JSON.",
    "parse_or": "Parse JSON or return empty object (never throw into the UI).",
    "parse_bot": "One hunter row from /api/home (bias, leftover, knobs, clip).",
    "parse_home_extras": "Totals, Condor, rate, RISK, leaders, desk BIAS from /api/home.",
    "parse_wire_graph": "Nodes + edges from /api/wire/graph.",
    "parse_home": "One matrix snapshot from GET /api/home (meta).",
    "num": "Read a JSON number with a default.",
    "str": "Read a JSON string with a default.",
    "flag": "Read a JSON bool with a default.",
    "jn": "JSON number helper (chart).",
    "jnum": "JSON number helper.",
    "jstr": "JSON string helper.",
    "jbool": "JSON bool helper.",
    "jflag": "JSON bool helper (meta).",
    "jhas": "True if JSON object has a non-null key.",
    "read_json": "Read object/array JSON; empty object on missing/corrupt file.",
    "write_json": "Atomic write: dump to path.tmp then rename. Refuses '..' in the path.",
    "slurp": "Tail-read a text file (Copy TRADE LOG, daemon log).",
    "slurp_cmd": "Run a shell command and capture stdout (bounded).",
    "utc_now": "UTC timestamp string for status JSON / logs.",
    "utc_stamp": "Compact UTC stamp for job logs.",
    "join": "Join conf dir + filename.",
    "registry": "Load conf/wire_registry.json (hunter ids, names, wallets).",
    "docker_running": "True if this container is running (cached docker inspect).",
    "wallet_of": "Subaccount address for a hunter id.",
    "sl_of": "Stop-loss ROE for a hunter, with fallback.",
    "ctrl_file": "Load this hunter’s *_ctrl.json.",
    "merge_controller": "Merge yml + *_ctrl.json + bot_sl into the knob map the desk shows.",
    "api_home": "GET /api/home: bots, positions, leftover, Condor, rate, RISK, leaders.",
    "api_catalog": "GET Wire tool catalog.",
    "api_nav": "GET page list for the desk header.",
    "api_rate": "GET HL IP weight used / 1200 (IP-load bar).",
    "api_risk": "GET RISK park rows.",
    "api_desk_health": "GET host load, docker, Spring self-check.",
    "api_versions": "GET Archy + hummingbot image versions.",
    "api_bias_post": "POST BIAS. Must not flatten an open clip.",
    "api_sl_post": "POST SL ROE. Flatten-guard if it would kill now.",
    "api_restart_post": "Restart hunter container. Clips stay on Hyperliquid.",
    "api_desk_bias_post": "Desk-wide BIAS. No flatten.",
    "api_home_close_post": "Operator Close. The only flatten Spring will queue.",
    "api_home_open_post": "Operator 100% leftover. kind=open; hunters may scale if already same-side.",
    "api_ctrl_post": "Live knobs → *_ctrl.json + sqlite. Flatten-guard on deadly values.",
    "api_presets_get": "GET per-hunter presets.",
    "api_presets_post": "Save / apply a named preset.",
    "api_live_knobs_get": "GET RAM knobs for desk ↔ chart.",
    "api_live_knobs_post": "Publish RAM knobs (chart overlays).",
    "api_sleeper": "V2 sleeper start/stop (max 2 running).",
    "api_wire_graph_post": "Save Wire graph file. Not a trade.",
    "api_wire_pack_get": "GET Wire pack.",
    "api_wire_pack_post": "Compile / integrate pack.",
    "api_hl_trader_follow": "Copy follow-list mutate (mute/unfollow). Does not flatten.",
    "api_hl_trader_slots": "Copy leader slot count.",
    "api_trader_audits": "Read-only leader audit JSON.",
    "api_trader_audits_run": "Run Copy leader audit (no orders).",
    "deny_write": "403 JSON when WRITE MODE is off.",
    "deny_close": "403 JSON for routes that would flatten without operator Close.",
    "touches_positions": "True if the path is a flatten/open route.",
    "writes_set": "Arm/disarm WRITE MODE (file /tmp/freiquell-spring-writes).",
    "writes_load": "Restore WRITE MODE from the flag file at boot.",
    "writes_on": "True if Spring WRITE MODE is on.",
    "sql_apply": "Queue a sqlite job (matrix.db) via Python. Not a Hyperliquid order.",
    "sql_status_map": "Cached hunter status from sqlite.",
    "copy_log_trades": "Tail Copy TRADE LOG (T# tags).",
    "client": "One accepted socket: parse HTTP, Brain::handle, reply, close.",
    "send_all": "Write the full HTTP response; handle short writes.",
    "reason_for": "HTTP reason phrase for 200/400/403/404/500.",
    "spring_request_stop": "Set the stop flag (springctl Stop / SIGINT).",
    "spring_stop_requested": "Server loop checks this between accepts.",
    "is_conf": "True if path looks like hummingbot conf/ (wire_registry or bot_sl).",
    "valid_id": "True if this is a known hunter container id.",
    "host_allowed": "Only loopback / LAN / VPN 10.66.80.x — no random hosts.",
    "load_sites": "Read conf/deploy_hosts.json (hub + remote …).",
    "poll_site": "GET /api/home + ping one matrix. Never POST.",
    "fetch": "HTTP GET one site snapshot. GET only.",
    "fetch_all": "GET every configured matrix.",
    "frame": "Render the console meta dashboard.",
    "list_usb": "Removable disks for USB kit / ISO write.",
    "start_usb": "DEPLOY → Create USB install media (make-usb-gui.sh) + USB LOG.",
    "poll_usb_job": "Poll USB kit child. Auto-close overlay on OK; stay open on FAIL.",
    "start_mint_build": "DEPLOY → Build hybrid ISO. Does not download Mint until this click.",
    "start_mint_write": "Write the remastered ISO to USB (>=8G) or copy onto Ventoy.",
    "poll_mint_job": "Poll Mint ISO child. Overlay stays open on FAIL.",
    "start_live": "DEPLOY → PUSH latest. Code only over SSH/VPN. No wallets, no HL close.",
    "poll_live_job": "Poll live-push child. Auto-close overlay on OK; stay open on FAIL.",
    "open_job_log": "Log overlay: kind 0 live-push, 1 USB kit, 2 Mint ISO. CMD/OK/FAIL colours.",
    "iphone_gw_start": "Spawn iphone/gateway.py if the operator turned it on.",
    "iphone_gw_stop": "Kill the iPhone gateway (Meta button).",
    "iphone_gw_up": "True if the iPhone gateway process is alive.",
    "local_archy": "Hub version from archy.json.",
    "apply_deploys": "Stamp each site with last PUSH version.",
    "is_mothership": "True for the hub row (rainbow version).",
    "version_field": "Show hub version vs last deployed (behind = gold).",
    "hsv_rgb": "Rainbow mothership version colour.",
    "archy_desk_main": "X11 LEVEL UP. Two clicks. Resets grok suffix. No Hyperliquid.",
    "py": "Locate archy_db.py next to the binary.",
    "load_status": "Current version JSON from archy_db.py --status.",
    "db_py": "Path to archy_db.py for LEVEL UP / bump-g.",
    "conf_dir": "Resolve hummingbot conf/ next to the binary or cwd.",
    "repo_root": "Resolve the rimBaud checkout (parent of conf/).",
    "find_spring_pid": "PID of the running spring process, or 0.",
    "start_command": "Spawn ./spring --debug --conf … --port 5004.",
    "stop_command": "Kill the command-mode Spring PID.",
    "start_daemon": "systemctl start the Spring unit.",
    "stop_daemon": "systemctl stop the Spring unit.",
    "start_spring": "Start Spring via current COMMAND or DAEMON mode.",
    "stop_spring": "Stop Spring via current mode. Hunters keep running.",
    "switch_source": "Flip COMMAND ↔ DAEMON without touching hunters.",
    "sysctl": "systemctl --user or system, verb start/stop/restart.",
    "refresh": "Poll Spring + IP-load bar + VPN flag for the next springctl frame.",
    "fetch_hl_ip": "Public IP Spring reports (gold bar = hunter HL IP).",
    "load_source": "COMMAND vs DAEMON mode persisted in the user home.",
    "save_source": "Persist COMMAND vs DAEMON.",
    "load_vpn_flag": "Read springctl VPN checkbox (does not reroute hunters).",
    "save_vpn_flag": "Write springctl VPN checkbox.",
    "take_chart_lock": "One chart window per coin (flock).",
    "chart_lock_path": "Lock file path for this coin’s chart.",
    "push_live_overlay": "POST overlay line prices to Spring /api/live/knobs.",
    "apply_line_price": "Move TP/ARM/trail overlay to a dragged price.",
    "apply_pmm_live": "Merge Spring live knobs onto PMM overlay lines.",
    "load_home": "GET Spring /api/home for chart overlays.",
    "load_candles": "Fetch Binance + Hyperliquid candles for the selected interval.",
    "paint": "Repaint the chart (candles + overlays + hits).",
    "overlays_for": "Build TP/ARM/trail/WIN lines for this coin.",
    "fetch_binance": "HTTPS GET Binance klines.",
    "fetch_hl": "HTTPS POST Hyperliquid candle snapshot.",
    "parse_binance": "Parse Binance kline array.",
    "parse_hl": "Parse Hyperliquid candle JSON.",
    "is_short": "True if this overlay/clip is SHORT.",
    "sl_px": "Stop-loss price in chart pixels/price.",
    "arm_px": "ARM price.",
    "win_px": "WIN / take-profit price.",
    "trail_now_px": "Current trailing-stop price.",
    "trail_price_frac": "Trail as a fraction of ARM.",
    "next_in_px": "NEXT IN trigger price.",
    "color_from_id": "Stable hunter colour from bot id.",
    "x_ignore": "X11 error handler: ignore (chart).",
    "x_die": "X11 IO error: exit the chart process.",
    "on_int": "SIGINT/SIGTERM: request a clean quit.",
    "on_winch": "SIGWINCH: console meta must redraw.",
    "restore_term": "Restore terminal settings after raw mode (console meta).",
    "raw_term": "Put stdin in raw mode for the console meta.",
    "key_ready": "True if a key is waiting (console meta).",
    "listen_tcp": "Legacy: listen on TCP.",
    "listen_unix": "Legacy: listen on a unix socket.",
    "accept_loop": "Legacy: accept connections until stop.",
}


def describe(name: str) -> str:
    base = name.split("::")[-1]
    if name in KNOWN:
        return KNOWN[name]
    if base in KNOWN:
        return KNOWN[base]
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", base).replace("_", " ")
    words = re.sub(r"\s+", " ", words).strip()
    prefixes = (
        ("api ", "Spring HTTP handler: "),
        ("post ", "POST to Spring: "),
        ("draw ", "X11 draw: "),
        ("start ", "Start: "),
        ("stop ", "Stop: "),
        ("poll ", "Poll until done: "),
        ("load ", "Load: "),
        ("save ", "Save: "),
        ("parse ", "Parse: "),
        ("fetch ", "Fetch: "),
        ("iphone gw ", "iPhone gateway: "),
        ("pmm ", "PMM Simple: "),
        ("copy ", "Copy hunter: "),
        ("sql ", "sqlite: "),
        ("writes ", "Spring WRITE MODE: "),
        ("daemon ", "systemd Spring daemon: "),
    )
    low = words.lower() + " "
    for pre, label in prefixes:
        if low.startswith(pre):
            return label + words[len(pre) :].strip() + "."
    return words[:1].upper() + words[1:] + "."


# --- C++ scanner -------------------------------------------------------------

TYPE_START = re.compile(
    r"^\s*(?:template\s*<[^;{]*>\s*)?"
    r"(?:static\s+|inline\s+|virtual\s+|explicit\s+|constexpr\s+|unsigned\s+|signed\s+|friend\s+|extern\s+)*"
    r"(?:const\s+)?"
    r"(?:"
    r"void|bool|int|long|short|char|float|double|auto|size_t|ssize_t|pid_t|off_t|time_t|"
    r"uint\d+_t|int\d+_t|json|Json|Rect|Bot|Hit|Page|Source|Ctl|Site|Snap|Usb|Overlay|Candle|"
    r"HttpResponse|HttpsResponse|DeskSnap|PmmDisp|CtrlField|nlohmann::json|Display|"
    r"unsigned(?:\s+long)?|"
    r"const\s+char|"
    r"std::\w+(?:\s*<[^;{}]*>)?|"
    r"(?:struct\s+|class\s+|enum\s+)?[A-Z][\w:]*"
    r")"
    r"(?:\s*<[^;{}]*>)?"
    r"(?:\s*[*&])*"
    r"\s+"
    r"(?P<name>~?[A-Za-z_][\w:]*)"
    r"\s*\("
)

METHOD = re.compile(
    r"^\s*(?:static\s+|inline\s+|virtual\s+|constexpr\s+)*"
    r"(?:const\s+)?"
    r"(?P<ret>[\w:<>\*& ]+?)\s+"
    r"(?P<cls>[A-Za-z_]\w*)::(?P<name>~?[A-Za-z_]\w*)\s*\("
)

LAMBDA = re.compile(r"^\s*(?:static\s+)?auto\s+(?P<name>[A-Za-z_]\w*)\s*=\s*\[")
STRUCT = re.compile(r"^\s*(struct|class|enum)\s+(?P<name>[A-Za-z_]\w*)\b")


def is_comment_line(s: str) -> bool:
    s = s.strip()
    return (not s) or s.startswith("//") or s.startswith("/*") or s.startswith("*")


def join_sig(lines: list[str], i: int, maxjoin: int = 5) -> tuple[str, int]:
    blob = lines[i].rstrip()
    j = i
    while (
        j + 1 < len(lines)
        and blob.count("(") > blob.count(")")
        and "{" not in blob
        and not blob.rstrip().endswith(";")
    ):
        j += 1
        blob += " " + lines[j].strip()
        if j - i >= maxjoin:
            break
    return blob, j


def find_cpp_items(lines: list[str], is_hpp: bool) -> list[tuple[int, str, str]]:
    """Return (line_index, name, kind) for functions/lambdas/structs."""
    items: list[tuple[int, str, str]] = []
    i = 0
    in_block = False
    while i < len(lines):
        l = lines[i]
        s = l.strip()
        if "/*" in s and "*/" not in s[s.find("/*") + 2 :]:
            in_block = True
        if in_block:
            if "*/" in s:
                in_block = False
            i += 1
            continue
        if is_comment_line(s) or s.startswith("#"):
            i += 1
            continue
        lm = LAMBDA.match(l)
        if lm:
            items.append((i, lm.group("name"), "lambda"))
            i += 1
            continue
        sm = STRUCT.match(l)
        if sm and "{" in l or (sm and i + 1 < len(lines) and "{" in lines[i + 1]):
            items.append((i, sm.group("name"), "struct"))
            i += 1
            continue
        blob, j = join_sig(lines, i)
        has_brace = "{" in blob
        ends_semi = blob.rstrip().endswith(";") and not has_brace
        if not is_hpp and ends_semi:
            i += 1
            continue
        if not is_hpp and not has_brace:
            if j + 1 < len(lines) and lines[j + 1].strip().startswith("{"):
                has_brace = True
            else:
                i += 1
                continue
        m = METHOD.match(blob)
        if m:
            name = m.group("cls") + "::" + m.group("name")
            if name not in SKIP_NAMES and not name.startswith("std::"):
                items.append((i, name, "hpp-decl" if is_hpp and ends_semi else "fn"))
            i = j + 1
            continue
        m = TYPE_START.match(blob)
        if m:
            name = m.group("name")
            if (
                name not in SKIP_NAMES
                and len(name) > 1
                and not name.startswith("std")
                and name not in {"if", "for", "while", "switch", "catch", "return"}
            ):
                items.append((i, name, "hpp-decl" if is_hpp and ends_semi else "fn"))
        i += 1
    return items


def already_has_fn_comment(lines: list[str], i: int, name: str) -> bool:
    base = name.split("::")[-1]
    for j in range(i - 1, max(-1, i - 8), -1):
        t = lines[j].strip()
        if not t:
            continue
        if t.startswith("// ---") or t.startswith("// " + name) or t.startswith("// " + base):
            return True
        if t.startswith("/*") and (name in t or base in t or "DOKU" in t):
            # file banner is not a function comment unless it's the line immediately above
            if "DOKU SNAPSHOT" in t:
                return False
            return True
        if t.startswith("//") and (" — " in t or " - " in t):
            return True
        break
    return False


def insert_above(lines: list[str], i: int, text: str) -> None:
    indent = re.match(r"^(\s*)", lines[i]).group(1) if i < len(lines) else ""
    lines.insert(i, f"{indent}{text}\n" if not text.endswith("\n") else indent + text)


def comment_cpp_file(path: Path) -> int:
    orig = path.read_text(encoding="utf-8", errors="replace")
    # Ensure file starts with visible // lines (block comments fold in many editors).
    if orig.lstrip().startswith("/* DOKU SNAPSHOT"):
        end = orig.find("*/")
        if end != -1:
            block = orig[: end + 2]
            rest = orig[end + 2 :].lstrip("\n")
            vis = []
            for raw in block.splitlines():
                t = raw.strip()
                t = t[2:].strip() if t.startswith("/*") else t
                t = t[:-2].strip() if t.endswith("*/") else t
                t = t[1:].strip() if t.startswith("*") else t
                if t:
                    vis.append("// " + t)
            orig = "\n".join(vis) + "\n\n" + rest
    if not orig.startswith("// DOKU SNAPSHOT") and "DOKU SNAPSHOT" not in orig[:400]:
        rel = str(path.relative_to(ROOT))
        orig = (
            f"// DOKU SNAPSHOT — commented copy of live {rel}\n"
            f"// Study only. Compile and run from the repo root, not from doku/copy.\n\n"
            + orig
        )
    lines = orig.splitlines(keepends=True)
    # normalize keepends
    for i, l in enumerate(lines):
        if not l.endswith("\n"):
            lines[i] = l + "\n"
    is_hpp = path.suffix in {".hpp", ".h"}
    items = find_cpp_items([ln.rstrip("\n") for ln in lines], is_hpp)
    # unique by line
    seen = set()
    uniq = []
    for i, name, kind in items:
        if i in seen:
            continue
        seen.add(i)
        uniq.append((i, name, kind))
    n = 0
    for i, name, kind in reversed(uniq):
        if already_has_fn_comment([ln.rstrip("\n") for ln in lines], i, name):
            continue
        desc = describe(name)
        if kind == "struct":
            cmt = f"// {name} — data structure used by this file. {desc}"
        elif kind == "lambda":
            cmt = f"// {name} — local function. {desc}"
        else:
            cmt = f"// {name} — {desc}"
        insert_above(lines, i, cmt)
        n += 1
    new = "".join(lines)
    if new != path.read_text(encoding="utf-8", errors="replace"):
        path.write_text(new, encoding="utf-8")
    return n


# --- Python -----------------------------------------------------------------

DEF = re.compile(r"^(\s*)def\s+(?P<name>[A-Za-z_]\w*)\s*\(")
ASYNC_DEF = re.compile(r"^(\s*)async\s+def\s+(?P<name>[A-Za-z_]\w*)\s*\(")
CLASS = re.compile(r"^(\s*)class\s+(?P<name>[A-Za-z_]\w*)\s*[:(]")


def py_already(lines: list[str], i: int) -> bool:
    j = i - 1
    while j >= 0 and (lines[j].strip().startswith("@") or not lines[j].strip()):
        j -= 1
    if j < 0:
        return False
    t = lines[j].strip()
    return t.startswith("#") or t.startswith('"""') or t.startswith("'''") or t.endswith('"""') or t.endswith("'''")


def comment_py_file(path: Path) -> int:
    orig = path.read_text(encoding="utf-8", errors="replace")
    lines = orig.splitlines(keepends=True)
    for i, l in enumerate(lines):
        if not l.endswith("\n"):
            lines[i] = l + "\n"
    # banner
    head = "".join(lines[:8])
    if "DOKU SNAPSHOT" not in head:
        rel = str(path.relative_to(ROOT))
        banner = (
            f"# DOKU SNAPSHOT — commented copy of live {rel}\n"
            f"# Study only. Do not run hunters from doku/copy on a live Hyperliquid account.\n"
        )
        # keep shebang first
        if lines and lines[0].startswith("#!"):
            lines.insert(1, banner if banner.endswith("\n") else banner + "\n")
            if len(lines) > 2 and lines[2].strip() and not lines[2].startswith("#"):
                pass
        else:
            lines.insert(0, banner)
    n = 0
    # recompute after banner insert: scan from bottom
    body = [ln.rstrip("\n") for ln in lines]
    hits: list[tuple[int, str, str]] = []
    for i, raw in enumerate(body):
        m = DEF.match(raw) or ASYNC_DEF.match(raw)
        if m:
            hits.append((i, m.group("name"), "def"))
            continue
        m = CLASS.match(raw)
        if m:
            hits.append((i, m.group("name"), "class"))
    for i, name, kind in reversed(hits):
        if py_already([ln.rstrip("\n") for ln in lines], i):
            continue
        desc = describe(name)
        indent = re.match(r"^(\s*)", lines[i]).group(1)
        # place above decorators
        at = i
        while at > 0 and lines[at - 1].strip().startswith("@"):
            at -= 1
        tag = "class" if kind == "class" else name
        lines.insert(at, f"{indent}# {tag} — {desc}\n")
        n += 1
    new = "".join(lines)
    if new != path.read_text(encoding="utf-8", errors="replace"):
        path.write_text(new, encoding="utf-8")
    return n


def main() -> int:
    if not ROOT.is_dir():
        print("missing", ROOT, file=sys.stderr)
        return 2
    cpp_n = py_n = 0
    cpp_files = py_files = 0
    for p in sorted(ROOT.rglob("*")):
        if "third_party" in p.parts or "nlohmann" in p.parts or "__pycache__" in p.parts:
            continue
        if p.suffix in {".cpp", ".hpp", ".h", ".cc"}:
            k = comment_cpp_file(p)
            cpp_n += k
            cpp_files += 1
            print(f"c++ {k:4}  {p.relative_to(ROOT)}")
        elif p.suffix == ".py":
            k = comment_py_file(p)
            py_n += k
            py_files += 1
            print(f"py  {k:4}  {p.relative_to(ROOT)}")
    print(f"done  c++ comments {cpp_n} in {cpp_files} files;  py comments {py_n} in {py_files} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
