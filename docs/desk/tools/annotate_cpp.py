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

"""Insert documentation comments into doku/copy C++ sources only.

Never run this on the live tree. Idempotent: skips files that already have
the DOKU SNAPSHOT banner.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "copy"

# ---------------------------------------------------------------------------
# File banners (shown at the top of every compile unit we own)
# ---------------------------------------------------------------------------
BANNERS = {
    "freiquell_c++/src/main.cpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_c++/src/main.cpp
 * BINARY: rimbaud-desk  (with http.cpp + api.cpp + ui.cpp,  -lX11 -lpthread)
 * ROLE:   Entry for the native matrix window.
 *         Always talks to Spring on :5004. Refuses Flask :5001.
 * FLAGS:  --host --port --debug --cpp-server --headless --help
 *         RIMBAUD_HOST / RIMBAUD_PORT (or FREIQUELL_* aliases).
 * FLOW:   parse argv → FlaskApi {host,port,read_only} → dump_headless or run_desk.
 * SAFETY: This binary does not call Hyperliquid. Hunters in Docker execute.
 */""",
    "freiquell_c++/src/http.cpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_c++/src/http.cpp
 * USED BY: rimbaud-desk, rimbaud-chart, rimbaud-meta, rimbaud-meta-desk
 * ROLE:    Tiny blocking HTTP/1.1 client (no curl). TCP + parse status/headers/body.
 *          Timeouts via select(). Used for Spring GET/POST on 127.0.0.1:5004.
 */""",
    "freiquell_c++/src/http.hpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_c++/src/http.hpp
 * ROLE: Public HTTP client API shared by desk, chart, and meta.
 */""",
    "freiquell_c++/src/https.cpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_c++/src/https.cpp
 * BINARY: rimbaud-chart only  (-lssl -lcrypto)
 * ROLE:   TLS GET/POST for Binance klines and Hyperliquid candle REST.
 *         Desk itself stays on plain HTTP to Spring.
 */""",
    "freiquell_c++/src/https.hpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_c++/src/https.hpp
 * ROLE: TLS HTTP client used by rimbaud-chart.
 */""",
    "freiquell_c++/src/api.cpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_c++/src/api.cpp
 * BINARY: rimbaud-desk
 * ROLE:   Translate Spring JSON into DeskSnap (bots, positions, wire, audit…).
 *         POST helpers for BIAS / SL / Close / 100% / knobs / Wire pack.
 *         can_post() is the write gate; Spring still enforces Close/100% rules.
 */""",
    "freiquell_c++/src/api.hpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_c++/src/api.hpp
 * ROLE: Data model for the desk (Bot, Position, WireNode, FlaskApi, …).
 *       FlaskApi is named historically; production target is Spring :5004.
 */""",
    "freiquell_c++/src/ui.cpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_c++/src/ui.cpp
 * BINARY: rimbaud-desk
 * ROLE:   Entire X11 matrix UI: layout, hit testing, Wire canvas, draw, event loop.
 * PAGES:  Home, Wire, Condor, Hunters, Funds, Strategy, System  (keys 1–7).
 * ENTRY:  run_desk(FlaskApi&)  /  dump_headless(FlaskApi&)
 * NOTES:  Large file on purpose (one window, no Qt). Search for run_desk, draw_,
 *         and struct DeskUi. Close/100% go through FlaskApi::post_* → Spring.
 */""",
    "freiquell_c++/src/ui.hpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_c++/src/ui.hpp
 * ROLE: Desk UI entry points.
 */""",
    "freiquell_c++/src/chart.cpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_c++/src/chart.cpp
 * BINARY: rimbaud-chart  (http.cpp + https.cpp,  -lX11 -lssl -lcrypto)
 * ROLE:   Per-coin candle window. Fetches Binance + Hyperliquid klines.
 *         Overlays TP / ARM / trail / WIN from Spring /api/home + live knobs.
 *         One chart process per coin (file lock). Does not place orders.
 */""",
    "freiquell_server_c++/src/main.cpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_server_c++/src/main.cpp
 * BINARY: spring  (with server.cpp + brain.cpp,  -lpthread)
 * ROLE:   Start the C++ brain. Requires --debug. Binds :5004. Refuses :5001.
 *         Resolves conf/ (wire_registry.json). Then run_server(Brain).
 * WRITE:  Actual writes are inside brain.cpp behind WRITE MODE + Close/100% rules.
 */""",
    "freiquell_server_c++/src/server.cpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_server_c++/src/server.cpp
 * BINARY: spring
 * ROLE:   Accept TCP, parse HTTP request line + headers + body, call Brain::handle,
 *         write the response. Caps concurrent clients (kMaxLive).
 *         spring_request_stop() lets springctl / SIGINT shut the loop down.
 */""",
    "freiquell_server_c++/src/brain.cpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_server_c++/src/brain.cpp
 * BINARY: spring
 * ROLE:   All /api routes. Reads conf JSON + sqlite. WRITE MODE flag file
 *         /tmp/freiquell-spring-writes. SQL jobs via conf/.spring_sql_apply.py.
 * KEY:    api_home, api_bias_post, api_home_close_post, api_home_open_post,
 *         api_ctrl_post, Brain::handle (router).
 * SAFETY: deny_close / touches_positions. Operator Close/100% is the open/close path.
 *         Never flatten on BIAS / mute / reboot. Never talk to Hyperliquid REST here.
 */""",
    "freiquell_server_c++/src/brain.hpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_server_c++/src/brain.hpp
 * ROLE: Brain config (conf path, port, debug) + handle() + run_server().
 */""",
    "freiquell_server_c++/src/jsonio.hpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_server_c++/src/jsonio.hpp
 * ROLE: Header-only JSON file IO with flock. Used by Spring to read conf/.
 */""",
    "freiquell_server_c++/src/ctl.cpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root freiquell_server_c++/src/ctl.cpp
 * BINARY: springctl  (-lX11 -lpthread)
 * ROLE:   Start/stop/restart Spring (command PID or systemd daemon).
 *         IP-load bar from Spring /api/rate (HL 1200 weight).
 *         VPN checkbox writes a flag — does NOT change hunter routing.
 *         Controls Spring only: not Flask, not Docker hunters, not clips.
 */""",
    "rimbaud_meta_c++/src/main.cpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root rimbaud_meta_c++/src/main.cpp
 * BINARY: rimbaud-meta  (console, GET only)
 * ROLE:   Terminal dashboard of connected Spring matrices (conf/deploy_hosts.json).
 *         Never POST, never CLOSE/OPEN, never restart Docker.
 */""",
    "rimbaud_meta_c++/src/desk.cpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root rimbaud_meta_c++/src/desk.cpp
 * BINARY: rimbaud-meta-desk  (desk.cpp + ../freiquell_c++/src/http.cpp,  -lX11)
 * ROLE:   X11 mothership. Pages HOME / DEPLOY / HL MASTER.
 *         DEPLOY: live-push, USB kit (make-usb.sh), Mint ISO (mint-image/).
 *         Shared log overlay: live-push / USB / mint ISO  (CMD cyan, OK green, FAIL rose).
 *         iPhone gateway start/stop. GET toward matrices. Never CLOSE/OPEN HL.
 */""",
    "rimbaud-archy/src/main.cpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root rimbaud-archy/src/main.cpp
 * BINARY: rimbaud-archy
 * ROLE:   CLI wrapper. --bump-g / --status / --record-deploy exec archy_db.py.
 *         No args → X11 LEVEL UP (operator only; Grok must not --level-up).
 *         Never CLOSE/OPEN Hyperliquid. Never raise 0.1 → 0.2 from Grok.
 */""",
    "rimbaud-archy/src/desk.cpp": """\
/* DOKU SNAPSHOT — commented copy. Live file is repo-root rimbaud-archy/src/desk.cpp
 * BINARY: rimbaud-archy
 * ROLE:   X11 version desk. LEVEL UP two-click 0.1 → 0.2 (resets grok suffix).
 *         Reads/writes via archy_db.py → conf/archy.db + conf/archy.json.
 */""",
    "brain_c++/src/main.cpp": """\
/* DOKU SNAPSHOT — LEGACY. Do not ARM on the live desk.
 * BINARY: freiquell-brain  (port 5003)
 * ROLE:   Early C++ brain before Spring. Can write hunter JSON.
 *         Production brain is freiquell_server_c++/spring on :5004.
 */""",
    "brain_c++/src/server.cpp": """\
/* DOKU SNAPSHOT — LEGACY HTTP listener for freiquell-brain. Not production. */""",
    "brain_c++/src/brain.cpp": """\
/* DOKU SNAPSHOT — LEGACY routes for freiquell-brain. Can write hunter JSON.
 * Production equivalent: freiquell_server_c++/src/brain.cpp (Spring).
 */""",
    "brain_c++/src/brain.hpp": """\
/* DOKU SNAPSHOT — LEGACY. Brain struct for freiquell-brain. */""",
    "brain_c++/src/jsonio.hpp": """\
/* DOKU SNAPSHOT — LEGACY JSON IO for freiquell-brain. */""",
    "c++/matrix/src/main.cpp": """\
/* DOKU SNAPSHOT — LEGACY first X11 desk (freiquell-matrix).
 * Replaced by freiquell_c++/rimbaud-desk. Do not ARM on the live desk.
 */""",
    "c++/matrix/src/http.cpp": """\
/* DOKU SNAPSHOT — LEGACY HTTP client for freiquell-matrix. */""",
    "c++/matrix/src/http.hpp": """\
/* DOKU SNAPSHOT — LEGACY. */""",
    "c++/matrix/src/api.cpp": """\
/* DOKU SNAPSHOT — LEGACY snapshot parser for freiquell-matrix. */""",
    "c++/matrix/src/api.hpp": """\
/* DOKU SNAPSHOT — LEGACY data model for freiquell-matrix. */""",
    "c++/matrix/src/ui.cpp": """\
/* DOKU SNAPSHOT — LEGACY X11 UI for freiquell-matrix. */""",
    "c++/matrix/src/ui.hpp": """\
/* DOKU SNAPSHOT — LEGACY. */""",
    "c++/server/src/main.cpp": """\
/* DOKU SNAPSHOT — LEGACY server entry (freiquell-server). Do not ARM live.
 * Production: freiquell_server_c++/spring.
 */""",
    "c++/server/src/server.cpp": """\
/* DOKU SNAPSHOT — LEGACY HTTP listener. */""",
    "c++/server/src/brain.cpp": """\
/* DOKU SNAPSHOT — LEGACY routes. Can write hunter JSON. Not production. */""",
    "c++/server/src/brain.hpp": """\
/* DOKU SNAPSHOT — LEGACY. */""",
    "c++/server/src/jsonio.hpp": """\
/* DOKU SNAPSHOT — LEGACY JSON IO. */""",
}

MAKEFILE_BANNERS = {
    "freiquell_c++/Makefile": """\
# DOKU SNAPSHOT — commented copy of live freiquell_c++/Makefile
# Builds the operator front:
#   rimbaud-desk   ← src/main.cpp http.cpp api.cpp ui.cpp      -lX11 -lpthread
#   rimbaud-chart  ← src/chart.cpp http.cpp https.cpp          -lX11 -lpthread -lssl -lcrypto
# Compile from the LIVE tree to replace the running desk. This copy is for study.
""",
    "freiquell_server_c++/Makefile": """\
# DOKU SNAPSHOT — commented copy of live freiquell_server_c++/Makefile
# Builds the brain:
#   spring     ← src/main.cpp server.cpp brain.cpp    -lpthread     (:5004, --debug required)
#   springctl  ← src/ctl.cpp                          -lX11 -lpthread
# Do not ARM brain_c++ or c++/server on the live desk.
""",
    "rimbaud_meta_c++/Makefile": """\
# DOKU SNAPSHOT — commented copy of live rimbaud_meta_c++/Makefile
#   rimbaud-meta       ← src/main.cpp + ../freiquell_c++/src/http.cpp     (console, GET only)
#   rimbaud-meta-desk  ← src/desk.cpp + ../freiquell_c++/src/http.cpp -lX11
# Never CLOSE/OPEN Hyperliquid from meta.
""",
    "rimbaud-archy/Makefile": """\
# DOKU SNAPSHOT — commented copy of live rimbaud-archy/Makefile
#   rimbaud-archy  ← src/main.cpp src/desk.cpp  -lX11
# Grok: --bump-g. Operator: LEVEL UP in the window. Never CLOSE/OPEN.
""",
    "brain_c++/Makefile": """\
# DOKU SNAPSHOT — LEGACY Makefile (freiquell-brain :5003). Do not ARM on live.
""",
    "c++/Makefile": """\
# DOKU SNAPSHOT — LEGACY: builds c++/server and c++/matrix. Do not ARM on live.
""",
    "c++/matrix/Makefile": """\
# DOKU SNAPSHOT — LEGACY freiquell-matrix. Replaced by freiquell_c++.
""",
    "c++/server/Makefile": """\
# DOKU SNAPSHOT — LEGACY freiquell-server. Replaced by freiquell_server_c++/spring.
""",
}

# Function-name → comment. Matched on a line that starts a function definition.
FUNCS: dict[str, dict[str, str]] = {
    "freiquell_c++/src/main.cpp": {
        "int main": "Desk entry: parse flags, force Spring :5004, then UI or headless dump.",
    },
    "freiquell_c++/src/http.cpp": {
        "static bool timed_ms": "Wait until fd is readable/writable or timeout_ms elapses.",
        "static void sock_fast": "Disable Nagle so small Spring GETs return quickly.",
        "static int connect_host": "TCP connect with timeout. Returns fd or -1.",
        "HttpResponse http_request": "Blocking HTTP/1.1 request. Returns status + body. Used by desk/meta/chart.",
    },
    "freiquell_c++/src/https.cpp": {
        "static void ssl_once": "OpenSSL init once per process (chart only).",
        "static bool timed": "select() helper for TLS sockets.",
        "static int tcp_connect": "TCP connect used before SSL_connect.",
        "static std::string decode_chunked": "Decode Transfer-Encoding: chunked (Binance sometimes).",
        "HttpsResponse https_request": "TLS GET/POST. Chart uses this for Binance klines and HL candles.",
    },
    "freiquell_c++/src/api.cpp": {
        "static json parse_or": "Parse JSON or return empty object (never throw into the UI).",
        "static Bot parse_bot": "One hunter row from /api/home (bias, leftover, knobs, clip).",
        "static void parse_home_extras": "Totals, Condor, rate, RISK, leaders, desk BIAS from /api/home.",
        "bool FlaskApi::can_post": "Write gate. False → POST helpers return a toast, no HTTP.",
        "bool FlaskApi::ping": "GET /api/home cheap check that Spring is up.",
        "std::string FlaskApi::use_flask": "Leftover: point at Flask :5001. Production desk refuses this port.",
        "std::string FlaskApi::use_cpp": "Point at Spring (default :5004).",
        "void FlaskApi::load_fields": "Controller knob catalog for the Wire / hunter strips.",
        "static void parse_wire_graph": "Nodes + edges from /api/wire/graph.",
        "void FlaskApi::load_wire": "Fetch one Wire board snapshot.",
        "void FlaskApi::load_qa": "Strategy / weekly QA page.",
        "void FlaskApi::load_backup": "System backup listing.",
        "void FlaskApi::load_version": "Archy version + deploy stamps.",
        "std::string FlaskApi::post_sl": "POST stop-loss ROE for one hunter (Spring queues SQL).",
        "std::string FlaskApi::post_restart": "Ask Spring to restart a hunter container (clips stay on HL).",
        "std::string FlaskApi::post_desk_bias": "Desk-wide BIAS. Does not flatten clips.",
        "std::string FlaskApi::post_bias": "Per-hunter BIAS LONG/SHORT/FLAT. Does not flatten.",
        "std::string FlaskApi::post_close_coin": "Operator Close — the flatten path. Not used for mute/BIAS.",
        "std::string FlaskApi::post_open100": "Operator 100% leftover. Same-side already-open → scale (DMan Maker).",
        "std::string FlaskApi::post_wire_pack": "Wire compile / integrate / save (may still hit Flask for compile).",
        "std::string FlaskApi::post_ctrl": "One live knob.",
        "std::string FlaskApi::post_ctrl_map": "Many knobs at once (SCALE x on PMM).",
        "static AuditTrader parse_audit_trader": "Copy-leader audit row (read-only).",
    },
    "freiquell_c++/src/ui.cpp": {
        "bool inside": "Hit-test: point in rectangle.",
        "std::string money": "Format USD for the desk strips.",
        "void pmm_from_x": "PMM SCALE x → derived TP / ARM / trail / NEXT / SL / margin.",
        "void pmm_couple": "Keep PMM knobs coupled when the operator drags one pot.",
        "void pmm_apply_bot": "Write coupled PMM display back onto the Bot.",
        "std::string hunter_tag": "Short hunter label (Copy, PMM, DMan Maker, …).",
        "void fill": "X11 filled rect in desk palette.",
        "void stroke": "X11 stroked rect.",
        "void text": "X11 8x13 (or current GC font) string.",
        "bool sl_match": "Whether current SL matches a preset chip.",
        "std::string action_tip": "Status-line hint for the hit under the cursor.",
        "int draw_sl_strip": "Home SL chips (40/60/80/custom).",
        "int node_w": "Wire node width (fat hunters vs tools).",
        "int node_h": "Wire node height.",
        "std::vector<PortVis> layout_ports": "Port dots on a Wire node.",
        "bool handles_ok": "Whether two Wire handles may connect.",
        "void dump_headless": "Print /api/home JSON summary and exit (no window).",
        "int run_desk": "Main X11 loop: poll Spring, draw pages, dispatch hits (BIAS/Close/100%/knobs).",
    },
    "freiquell_c++/src/chart.cpp": {
        "static void color_from_id": "Stable hunter colour from bot id.",
        "static bool apply_line_price": "Move TP/ARM/trail overlay to a dragged price.",
        "static bool take_chart_lock": "One chart window per coin (flock).",
        "static std::vector<Candle> parse_binance": "Parse Binance kline array.",
        "static std::vector<Candle> parse_hl": "Parse Hyperliquid candle JSON.",
        "static std::vector<Candle> fetch_binance": "HTTPS GET Binance klines.",
        "static std::vector<Candle> fetch_hl": "HTTPS POST Hyperliquid candle snapshot.",
        "static bool apply_pmm_live": "Merge Spring live knobs onto PMM overlay lines.",
        "static json load_home": "GET Spring /api/home for overlays.",
        "static std::vector<Overlay> overlays_for": "Build TP/ARM/trail/WIN lines for this coin.",
        "int main": "Chart entry: pick coin, lock, fetch candles, X11 loop. No orders.",
    },
    "freiquell_server_c++/src/main.cpp": {
        "static bool is_conf": "True if path looks like hummingbot conf/ (wire_registry or bot_sl).",
        "int main": "Require --debug, refuse :5001, resolve conf/, run_server.",
    },
    "freiquell_server_c++/src/server.cpp": {
        "static bool send_all": "Write the full HTTP response; handle short writes.",
        "static const char* reason_for": "Status text for 200/400/403/404/500.",
        "static void client": "One accepted socket: parse request, Brain::handle, reply, close.",
        "int run_server": "Listen 127.0.0.1:port until spring_request_stop().",
    },
    "freiquell_server_c++/src/brain.cpp": {
        "void spring_request_stop": "Set the stop flag (springctl Stop / SIGINT).",
        "bool spring_stop_requested": "Server loop checks this between accepts.",
        "static void writes_set": "Arm/disarm WRITE MODE (file /tmp/freiquell-spring-writes).",
        "static void writes_load": "Restore WRITE MODE from the flag file at boot.",
        "static bool sql_apply": "Queue a sqlite job (matrix.db) via Python helper. Not a HL order.",
        "static json registry": "Load conf/wire_registry.json (hunter ids, names, wallets).",
        "static bool docker_running": "docker inspect running? Cached.",
        "static json merge_controller": "Merge yml + *_ctrl.json + bot_sl into the knob map the desk shows.",
        "static json copy_log_trades": "Tail Copy TRADE LOG (T# tags).",
        "static json api_versions": "Archy + hummingbot image versions for System / Meta.",
        "static json api_desk_health": "Host load, docker, Spring self-check.",
        "static json api_rate": "HL IP weight used / 1200 (IP-load bar).",
        "static json api_risk": "RISK park rows.",
        "static json api_hl_trader_follow": "Copy follow-list mutate (mute/unfollow). Does not flatten.",
        "static json api_trader_audits": "Read-only leader audit JSON.",
        "static json sql_status_map": "Cached hunter status from sqlite.",
        "static json api_home": "Big GET: bots, positions, leftover, Condor, rate, RISK, leaders.",
        "static json api_sleeper": "V2 sleeper start/stop (max 2 running).",
        "static json api_catalog": "Wire tool catalog.",
        "static json api_nav": "Page list for the desk header.",
        "static json api_wire_graph_post": "Save Wire graph (file). Not a trade.",
        "static json api_wire_pack_post": "Compile / integrate pack.",
        "static json deny_write": "403 JSON when WRITE MODE is off.",
        "static json deny_close": "403 JSON for routes that would flatten without operator Close.",
        "static bool touches_positions": "True if the path is a flatten/open route.",
        "static json apply_manual_close_px": "Record operator Close mark (does not send HL cancel here).",
        "static json api_ctrl_post": "Live knobs → *_ctrl.json + sqlite. Flatten-guard on deadly values.",
        "static json api_presets_get": "Per-hunter presets (never global).",
        "static json api_presets_post": "Save / apply a named preset.",
        "static json api_live_knobs_get": "RAM knobs for desk ↔ chart.",
        "static json api_live_knobs_post": "Publish RAM knobs (chart overlays).",
        "static json api_bias_post": "Set BIAS. Must not flatten an open clip.",
        "static json api_sl_post": "Set SL ROE. Flatten-guard if it would kill now.",
        "static json api_restart_post": "Restart hunter container. Clips stay on Hyperliquid.",
        "static json api_desk_bias_post": "Desk-wide BIAS. No flatten.",
        "static json api_home_close_post": "Operator Close path. The only flatten Spring will queue.",
        "static json api_home_open_post": "Operator 100% leftover. kind=open; hunters may scale if already same-side.",
        "std::string Brain::handle": "HTTP router: method + path → api_* . GET is always on; POST needs WRITE MODE.",
    },
    "freiquell_server_c++/src/ctl.cpp": {
        "static Source load_source": "COMMAND vs DAEMON mode persisted in the user home.",
        "static int sysctl": "systemctl --user or system, verb start/stop/restart.",
        "static pid_t find_spring_pid": "Find the running spring process.",
        "static std::string fetch_hl_ip": "Public IP Spring reports (gold bar = hunter HL IP).",
        "static void refresh": "Poll Spring + load bar + VPN flag for the next frame.",
        "static std::string stop_command": "Kill the command-mode PID.",
        "static std::string start_command": "Spawn ./spring --debug --conf … --port 5004.",
        "static std::string stop_daemon": "systemctl stop the Spring unit.",
        "static std::string start_daemon": "systemctl start the Spring unit.",
        "static std::string switch_source": "Flip COMMAND ↔ DAEMON without touching hunters.",
        "int main": "springctl entry: window or --status/--start/--stop/--restart.",
    },
    "rimbaud_meta_c++/src/main.cpp": {
        "static bool host_allowed": "Only loopback / LAN / VPN 10.66.80.x — no random hosts.",
        "static std::vector<Site> load_sites": "Read conf/deploy_hosts.json (hub + remote …).",
        "static Snap parse_home": "One matrix snapshot from GET /api/home.",
        "static Snap fetch": "HTTP GET one site. GET only.",
        "static std::string frame": "Render the console dashboard.",
        "int main": "Console meta loop. Never POST.",
    },
    "rimbaud_meta_c++/src/desk.cpp": {
        "static void hsv_rgb": "Rainbow mothership version colour.",
        "static json iphone_gw_flag": "Wanted on/off for iphone/gateway.py.",
        "static int iphone_gw_stop": "Kill the iPhone gateway (user asked it off from Meta).",
        "static bool iphone_gw_start": "Spawn iphone/gateway.py if wanted.",
        "static bool host_allowed": "Loopback / LAN / VPN only.",
        "static std::vector<Site> load_sites": "deploy_hosts.json + local Spring.",
        "static void poll_site": "GET /api/home + ping. Never POST.",
        "static std::string local_archy": "Hub version from archy.json.",
        "static void apply_deploys": "Stamp each site with last PUSH version.",
        "static bool is_mothership": "True for the hub row (rainbow version).",
        "static std::string version_field": "Show hub version vs last deployed (behind = gold).",
        "static std::vector<Usb> list_usb": "Removable disks for USB kit / ISO write.",
        "int main": "X11 meta: HOME / DEPLOY / HL MASTER, USB + Mint jobs, PUSH log overlay.",
    },
    "rimbaud-archy/src/main.cpp": {
        "static std::string py": "Locate archy_db.py next to the binary.",
        "int main": "CLI → python3 archy_db.py; no args → archy_desk_main(). Blocks --level-up from CLI.",
    },
    "rimbaud-archy/src/desk.cpp": {
        "static json load_status": "Current version JSON from archy_db.py --status.",
        "int archy_desk_main": "X11 LEVEL UP. Two clicks. Resets grok suffix. No HL.",
    },
    "brain_c++/src/main.cpp": {
        "int main": "LEGACY brain entry (port 5003). Do not ARM live.",
    },
    "brain_c++/src/server.cpp": {
        "int run_server": "LEGACY listener.",
    },
    "c++/matrix/src/main.cpp": {
        "int main": "LEGACY desk entry. Use freiquell_c++/rimbaud-desk.",
    },
    "c++/server/src/main.cpp": {
        "int main": "LEGACY server entry. Use freiquell_server_c++/spring.",
    },
}

FUNC_LINE = re.compile(
    r"^(?P<indent>\s*)(?P<sig>(?:int|void|bool|std::[A-Za-z0-9_:<>,\s\*&]+|static [^{;]+|HttpsResponse|HttpResponse|std::string))\s*$"
)
# Also match signature and `{` on the same line.
FUNC_SAME = re.compile(
    r"^(?P<indent>\s*)(?P<sig>.+?)\s*\{?\s*$"
)


def insert_banner(text: str, banner: str) -> str:
    if "DOKU SNAPSHOT" in text[:1200]:
        return text
    if text.startswith("\ufeff"):
        text = text[1:]
    return banner.rstrip() + "\n\n" + text


def insert_func_comments(text: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return text
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    used: set[str] = set()
    while i < len(lines):
        raw = lines[i]
        stripped = raw.rstrip("\n")
        # Skip if previous emitted line is already a doku func comment
        already = bool(out) and ("/* " in out[-1] and " */" in out[-1]) and "DOKU SNAPSHOT" not in out[-1]
        hit = None
        for key, comment in mapping.items():
            if key in used:
                continue
            # key is a distinctive prefix of the signature
            s = stripped.lstrip()
            if s.startswith(key) and ("(" in s) and (s.endswith("{") or s.endswith(")") or s.endswith(";")):
                # skip pure declarations in headers that end with ;
                if s.endswith(";") and "{" not in s:
                    continue
                if "/*" in s or s.strip().startswith("//"):
                    continue
                hit = (key, comment)
                break
            # signature split across lines: current line is the start, next has (
            if s.startswith(key) and "(" not in s and i + 1 < len(lines) and "(" in lines[i + 1]:
                hit = (key, comment)
                break
        if hit and not already:
            key, comment = hit
            indent = raw[: len(raw) - len(raw.lstrip())]
            out.append(f"{indent}/* {comment} */\n")
            used.add(key)
        out.append(raw)
        i += 1
    return "".join(out)


def process_file(rel: str, banner: str, funcs: dict[str, str]) -> bool:
    path = ROOT / rel
    if not path.is_file():
        print(f"MISSING {rel}", file=sys.stderr)
        return False
    orig = path.read_text(encoding="utf-8", errors="replace")
    text = insert_banner(orig, banner)
    text = insert_func_comments(text, funcs)
    if text != orig:
        path.write_text(text, encoding="utf-8")
        print(f"commented {rel}")
        return True
    print(f"unchanged {rel}")
    return False


def process_make(rel: str, banner: str) -> bool:
    path = ROOT / rel
    if not path.is_file():
        print(f"MISSING {rel}", file=sys.stderr)
        return False
    orig = path.read_text(encoding="utf-8", errors="replace")
    if "DOKU SNAPSHOT" in orig[:800]:
        print(f"unchanged {rel}")
        return False
    path.write_text(banner.rstrip() + "\n" + orig, encoding="utf-8")
    print(f"commented {rel}")
    return True


def main() -> int:
    if not ROOT.is_dir():
        print("no copy dir", ROOT, file=sys.stderr)
        return 2
    n = 0
    for rel, banner in BANNERS.items():
        if process_file(rel, banner, FUNCS.get(rel, {})):
            n += 1
    for rel, banner in MAKEFILE_BANNERS.items():
        if process_make(rel, banner):
            n += 1
    print(f"updated {n} files under {ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
