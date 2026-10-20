// Copyright 2026 rimbaud.io
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// rimbaud-meta — Linux console viewer of Spring matrices.
// GET only. Never CLOSE/OPEN. Does not bind Spring, restart Docker, or write hunter files.
#include "http.hpp"

#include <nlohmann/json.hpp>

#include <arpa/inet.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <termios.h>
#include <unistd.h>

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <string>
#include <vector>

using nlohmann::json;

static volatile sig_atomic_t g_run = 1;
static volatile sig_atomic_t g_winch = 1;
static termios g_old_term{};
static bool g_raw = false;

static void on_int(int) { g_run = 0; }
static void on_winch(int) { g_winch = 1; }

static void restore_term() {
    if (!g_raw) return;
    tcsetattr(STDIN_FILENO, TCSANOW, &g_old_term);
    fputs("\x1b[?25h\x1b[0m\x1b[?1049l", stdout);
    fflush(stdout);
    g_raw = false;
}

static const char* RST = "\x1b[0m";
static const char* BOLD = "\x1b[1m";
static const char* DIM = "\x1b[2m";
static const char* GREEN = "\x1b[38;5;48m";
static const char* GOLD = "\x1b[38;5;220m";
static const char* ORANGE = "\x1b[38;5;209m";
static const char* ROSE = "\x1b[38;5;204m";
static const char* CYAN = "\x1b[38;5;117m";
static const char* MUTED = "\x1b[38;5;65m";
static const char* TEXT = "\x1b[38;5;157m";
static const char* WHITE = "\x1b[38;5;252m";
static const char* PANEL = "\x1b[48;5;16m";

static std::string ascii(const std::string& s, int maxn = 0) {
    std::string o;
    o.reserve(s.size());
    for (unsigned char c : s) {
        if (c >= 32 && c < 127) o += static_cast<char>(c);
        else if (c == '\n' || c == '\t' || c == '\r') o += ' ';
        else o += '.';
    }
    if (maxn > 0 && static_cast<int>(o.size()) > maxn) o.resize(static_cast<size_t>(maxn));
    return o;
}

static std::string money(double n) {
    char b[48];
    snprintf(b, sizeof(b), "%s$%.2f", n < 0 ? "-" : "", std::fabs(n));
    return b;
}

static std::string signed_money(double n) {
    char b[48];
    snprintf(b, sizeof(b), "%s$%.2f", n < 0 ? "-" : "+", std::fabs(n));
    return b;
}

static std::string pct1(double n) {
    char b[32];
    snprintf(b, sizeof(b), "%+.1f%%", n);
    return b;
}

static double num(const json& j, const char* k, double d = 0) {
    if (!j.contains(k) || j[k].is_null()) return d;
    try {
        if (j[k].is_number()) return j[k].get<double>();
        if (j[k].is_string()) return std::stod(j[k].get<std::string>());
    } catch (...) {}
    return d;
}

static std::string str(const json& j, const char* k, const std::string& d = "") {
    if (!j.contains(k) || j[k].is_null()) return d;
    try {
        if (j[k].is_string()) return j[k].get<std::string>();
        if (j[k].is_number() || j[k].is_boolean()) return json(j[k]).dump();
    } catch (...) {}
    return d;
}

static bool flag(const json& j, const char* k, bool d = false) {
    if (!j.contains(k) || j[k].is_null()) return d;
    try {
        if (j[k].is_boolean()) return j[k].get<bool>();
        if (j[k].is_number()) return j[k].get<double>() != 0;
    } catch (...) {}
    return d;
}

static bool host_allowed(const std::string& host) {
    if (host == "127.0.0.1" || host == "localhost" || host == "::1") return true;
    in_addr a{};
    if (inet_pton(AF_INET, host.c_str(), &a) != 1) return false;
    unsigned u = ntohl(a.s_addr);
    if ((u & 0xFFFF0000u) == 0xC0A80000u) return true; // 192.168.0.0/16 LAN
    if ((u & 0xFFFFFF00u) == 0x0A425000u) return true; // 10.66.80.0/24 rimBaud VPN
    return false;
}

struct Site {
    std::string id, name, host, role;
    int port = 5004;
};

struct Pos {
    std::string bot, coin, side;
    double size = 0, roe = 0, pnl = 0, entry = 0;
};

struct Hunter {
    std::string id, name, health;
    bool running = false, ok = false;
    double eq = 0, free = 0, upnl = 0, sl = 0;
    int n_pos = 0;
    std::string bias;
};

struct Leader {
    int num = 0;
    std::string short_addr;
    bool muted = false;
    int n_pos = 0;
};

struct Snap {
    bool ok = false;
    std::string error, ts, bias, note;
    bool writes = false;
    double eq = 0, upnl = 0, parked = 0;
    int n_pos = 0, n_bots = 0, n_ok = 0, n_live = 0;
    double ip_left = 0, ip_used = 0, ip_cap = 1200, ip_pct = 0;
    std::vector<Hunter> hunters;
    std::vector<Pos> positions;
    std::vector<Leader> leaders;
};

struct Winsize {
    int rows = 24, cols = 80;
};

static Winsize winsz() {
    Winsize w;
    winsize z{};
    if (ioctl(STDOUT_FILENO, TIOCGWINSZ, &z) == 0 && z.ws_row > 0 && z.ws_col > 0) {
        w.rows = z.ws_row;
        w.cols = z.ws_col;
    }
    if (w.cols < 72) w.cols = 72;
    if (w.rows < 20) w.rows = 20;
    return w;
}

static std::string conf_dir() {
    if (const char* e = getenv("RIMBAUD_CONF")) return e;
    if (const char* e = getenv("FREIQUELL_CONF")) return e;
    return "conf";
}

static std::vector<Site> load_sites() {
    std::vector<Site> out;
    Site local;
    local.id = "local";
    local.name = "this matrix";
    local.host = "127.0.0.1";
    local.role = "local-readonly";
    local.port = 5004;
    out.push_back(local);

    std::ifstream in(conf_dir() + "/meta.json");
    if (!in) return out;
    json j;
    try { in >> j; } catch (...) { return out; }
    if (!j.contains("sites") || !j["sites"].is_array()) return out;
    std::vector<Site> extra;
    extra.push_back(local);
    for (auto& s : j["sites"]) {
        if (!s.is_object()) continue;
        Site x;
        x.id = str(s, "id");
        x.name = str(s, "name", x.id);
        x.host = str(s, "host");
        x.role = str(s, "role");
        x.port = static_cast<int>(num(s, "port", 5004));
        if (x.host.empty() || x.port == 5001) continue;
        if (!host_allowed(x.host)) continue;
        if (x.host == "127.0.0.1" && x.port == 5004) continue;
        extra.push_back(x);
    }
    return extra.empty() ? out : extra;
}

static std::string hunter_bias(const json& b) {
    if (!b.contains("bias") || !b["bias"].is_object()) return "-";
    std::string out;
    for (auto it = b["bias"].begin(); it != b["bias"].end(); ++it) {
        if (!it.value().is_string()) continue;
        std::string v = it.value().get<std::string>();
        if (v.empty() || it.key() == "updated" || it.key() == "source") continue;
        if (!out.empty() && out != v) return "MIX";
        out = v;
    }
    return out.empty() ? "-" : out;
}

static Snap parse_home(const json& j) {
    Snap s;
    s.ok = true;
    s.ts = str(j, "ts");
    s.note = str(j, "note");
    s.writes = flag(j, "writes");
    s.bias = str(j.contains("desk_bias") ? j["desk_bias"] : json::object(), "bias", "-");
    if (j.contains("totals") && j["totals"].is_object()) {
        auto t = j["totals"];
        s.eq = num(t, "equity_usd");
        s.upnl = num(t, "upnl_usd");
        s.n_pos = static_cast<int>(num(t, "n_pos"));
        s.n_bots = static_cast<int>(num(t, "n_bots"));
        s.n_ok = static_cast<int>(num(t, "n_ok"));
        s.n_live = static_cast<int>(num(t, "n_live"));
    }
    if (j.contains("rate") && j["rate"].is_object()) {
        auto r = j["rate"];
        s.ip_left = num(r, "left");
        s.ip_used = num(r, "used");
        s.ip_cap = num(r, "cap", 1200);
        s.ip_pct = num(r, "pct");
    }
    if (j.contains("risk") && j["risk"].is_object())
        s.parked = num(j["risk"], "parked_usd");
    if (j.contains("bots") && j["bots"].is_array()) {
        for (auto& b : j["bots"]) {
            if (!b.is_object()) continue;
            Hunter h;
            h.id = str(b, "id");
            h.name = str(b, "name", h.id);
            h.running = flag(b, "running");
            h.ok = flag(b, "ok", h.running);
            h.health = str(b, "health", h.ok ? "ok" : "issue");
            h.eq = num(b, "equity_usd");
            h.free = num(b, "free_usd");
            h.upnl = num(b, "upnl_usd");
            h.sl = num(b, "sl_roe_pct");
            h.n_pos = static_cast<int>(num(b, "n_pos"));
            h.bias = hunter_bias(b);
            if (h.id == "hummingbot-hl") h.bias = "copy";
            s.hunters.push_back(h);
            if (b.contains("copy_traders") && b["copy_traders"].is_array()) {
                for (auto& t : b["copy_traders"]) {
                    if (!t.is_object()) continue;
                    Leader L;
                    L.num = static_cast<int>(num(t, "num"));
                    L.short_addr = str(t, "short");
                    L.muted = flag(t, "muted") || flag(t, "unfollowed");
                    L.n_pos = static_cast<int>(num(t, "n_pos", num(t, "n_positions")));
                    s.leaders.push_back(L);
                }
            }
        }
    }
    if (j.contains("positions") && j["positions"].is_array()) {
        for (auto& p : j["positions"]) {
            if (!p.is_object()) continue;
            Pos x;
            x.bot = str(p, "bot");
            x.coin = str(p, "coin");
            x.side = str(p, "side");
            x.size = num(p, "size");
            x.roe = num(p, "roe_pct");
            x.pnl = num(p, "pnl_usd");
            x.entry = num(p, "entry_px");
            s.positions.push_back(x);
        }
    }
    return s;
}

static Snap fetch(const Site& site) {
    Snap s;
    if (!host_allowed(site.host)) {
        s.error = "host not on 127.0.0.1 / 192.168.x.x";
        return s;
    }
    auto r = http_request(site.host, site.port, "GET", "/api/home", "", 4000);
    if (!r.error.empty()) {
        s.error = r.error;
        return s;
    }
    if (r.status != 200) {
        s.error = "HTTP " + std::to_string(r.status);
        return s;
    }
    json j;
    try { j = json::parse(r.body.empty() ? "{}" : r.body); }
    catch (...) { s.error = "bad json"; return s; }
    return parse_home(j);
}

static const char* bias_color(const std::string& b) {
    if (b == "LONG") return GREEN;
    if (b == "SHORT") return ROSE;
    if (b == "FLAT") return GOLD;
    return MUTED;
}

static std::string pad(const std::string& s, int w, bool right = false) {
    std::string a = ascii(s, w);
    if (static_cast<int>(a.size()) >= w) return a;
    if (right) return std::string(static_cast<size_t>(w - a.size()), ' ') + a;
    return a + std::string(static_cast<size_t>(w - a.size()), ' ');
}

static std::string ip_bar(double pct, int w) {
    if (w < 4) w = 4;
    int fill = static_cast<int>(std::round(std::max(0.0, std::min(100.0, pct)) / 100.0 * w));
    std::string s = "[";
    for (int i = 0; i < w; i++) s += (i < fill) ? "#" : ".";
    s += "]";
    return s;
}

static std::string mute_label(const Snap& s) {
    if (s.leaders.empty()) return "n/a";
    int nmut = 0;
    std::string who;
    for (auto& L : s.leaders) if (L.muted) {
        nmut++;
        if (!who.empty()) who += ",";
        who += "T" + std::to_string(L.num);
    }
    if (!nmut) return "copy live";
    return "MUTE " + who;
}

static void line(std::string& o, const std::string& s) {
    o += s;
    o += "\x1b[0m\x1b[K\n";
}

static std::string frame(const std::vector<Site>& sites, const std::vector<Snap>& snaps,
                         int sel, const Winsize& W) {
    std::string o;
    o.reserve(8192);
    o += PANEL;
    o += "\x1b[H";
    const Site& site = sites[static_cast<size_t>(sel)];
    const Snap& snap = snaps[static_cast<size_t>(sel)];

    line(o, std::string(BOLD) + ORANGE + " rimBaud  //  META MATRIX" + RST + PANEL + "    "
         + GOLD + "READ-ONLY" + RST + PANEL + "    " + MUTED + "LAN + VPN 10.66.80.0/24  ·  GET only  ·  never CLOSE/OPEN");
    line(o, std::string(MUTED) + " selected  " + site.host + ":" + std::to_string(site.port)
         + "   VPN is control-plane only (hunters keep their own HL IP)  ·  j/k  q quit");
    line(o, "");

    double fleet_left = 0, fleet_cap = 0;
    int n_up = 0;
    for (auto& s : snaps) {
        if (s.ok) {
            n_up++;
            fleet_left += s.ip_left;
            fleet_cap += s.ip_cap > 0 ? s.ip_cap : 1200;
        }
    }
    char fleet[96];
    snprintf(fleet, sizeof(fleet), "%d/%zu up  fleet IP left %.0f/%.0f",
             n_up, sites.size(), fleet_left, fleet_cap);
    line(o, std::string(ORANGE) + " SITES  " + RST + PANEL + MUTED + fleet
         + "   (each UP matrix = +1200 HL tokens on its public IP)");
    for (int i = 0; i < static_cast<int>(sites.size()); i++) {
        const Site& si = sites[static_cast<size_t>(i)];
        const Snap& sn = snaps[static_cast<size_t>(i)];
        const char* mark = (i == sel) ? "*" : " ";
        const char* lamp = sn.ok ? GREEN : ROSE;
        std::string st = sn.ok ? "UP" : "DOWN";
        char ipb[40];
        if (sn.ok)
            snprintf(ipb, sizeof(ipb), "IP %.0f/%.0f", sn.ip_left, sn.ip_cap);
        else
            snprintf(ipb, sizeof(ipb), "IP  —");
        std::string role = si.role.empty() ? "" : si.role;
        std::string row = std::string(" ") + (i == sel ? GOLD : WHITE) + mark + RST + PANEL + " "
            + pad(si.name, 12) + "  " + pad(si.host + ":" + std::to_string(si.port), 18)
            + "  " + lamp + BOLD + pad(st, 4) + RST + PANEL
            + "  BIAS " + bias_color(sn.bias) + pad(sn.ok && !sn.bias.empty() ? sn.bias : "-", 6) + RST + PANEL
            + "  " + (sn.ok ? CYAN : MUTED) + pad(ipb, 14) + RST + PANEL
            + MUTED + " " + pad(role, 10);
        line(o, row);
    }
    if (snap.ok) {
        char ip[80];
        snprintf(ip, sizeof(ip), "IP %s %.0f/%.0f  left %.0f",
                 ip_bar(snap.ip_pct, 14).c_str(), snap.ip_used, snap.ip_cap, snap.ip_left);
        std::string row = std::string("   ") + TEXT + "eq " + BOLD + money(snap.eq) + RST + PANEL + TEXT
            + "   uPnL " + (snap.upnl >= 0 ? GREEN : ROSE) + signed_money(snap.upnl) + RST + PANEL + TEXT
            + "   pos " + std::to_string(snap.n_pos)
            + "   hunters " + std::to_string(snap.n_live) + "/" + std::to_string(snap.n_bots)
            + "   RISK parked " + money(snap.parked)
            + "   WRITE " + (snap.writes ? std::string(GOLD) + "files" : std::string(MUTED) + "off");
        line(o, row);
        line(o, std::string("   ") + MUTED + ip + "   " + ascii(snap.note, 64));
    } else {
        line(o, std::string("   ") + ROSE + ascii(snap.error.empty() ? "no snapshot" : snap.error, 70));
        line(o, std::string("   ") + MUTED + "Spring must be on 127.0.0.1:5004. Meta does not start it.");
    }
    line(o, "");

    line(o, std::string(ORANGE) + " HUNTERS" + RST + PANEL + MUTED + "  run  name            equity     free   pos     uPnL    SL   bias");
    int hmax = std::max(4, W.rows - 16);
    int shown = 0;
    for (auto& h : snap.hunters) {
        if (shown >= hmax) break;
        const char* run = h.running ? (h.ok ? GREEN : GOLD) : ROSE;
        const char* dot = h.running ? "●" : "○";
        std::string row = std::string("  ") + run + dot + " " + RST + PANEL + TEXT
            + pad(h.name, 14) + " "
            + pad(money(h.eq), 10, true) + " "
            + pad(money(h.free), 9, true) + " "
            + pad(std::to_string(h.n_pos), 4, true) + " "
            + (h.upnl >= 0 ? GREEN : ROSE) + pad(signed_money(h.upnl), 9, true) + RST + PANEL + TEXT + " "
            + pad(h.sl > 0 ? ("-" + std::to_string(static_cast<int>(h.sl)) + "%") : "n/a", 5, true) + "  "
            + bias_color(h.bias) + pad(h.bias, 6);
        line(o, row);
        shown++;
    }
    if (snap.hunters.empty())
        line(o, std::string("  ") + MUTED + "(no hunters in snapshot)");
    line(o, "");

    int split = std::max(36, W.cols / 2);
    line(o, std::string(ORANGE) + " POSITIONS" + std::string(static_cast<size_t>(std::max(0, split - 12)), ' ')
         + "COPY MUTE");
    size_t n = std::max(snap.positions.size(), snap.leaders.size());
    int pmax = std::max(3, W.rows - shown - 12);
    if (static_cast<int>(n) > pmax) n = static_cast<size_t>(pmax);
    if (n == 0)
        line(o, std::string("  ") + MUTED + "(flat / no copy leaders in snapshot)");
    for (size_t i = 0; i < n; i++) {
        std::string left = "  ";
        if (i < snap.positions.size()) {
            auto& p = snap.positions[i];
            const char* sc = (p.side == "SHORT") ? ROSE : GREEN;
            char sz[32];
            snprintf(sz, sizeof(sz), "%.4g", p.size);
            left += TEXT + pad(p.bot, 11) + " " + pad(p.coin, 6) + " " + sc + pad(p.side, 5) + RST + PANEL
                + TEXT + pad(sz, 8, true) + " " + (p.roe >= 0 ? GREEN : ROSE) + pad(pct1(p.roe), 8, true)
                + RST + PANEL;
        } else {
            left += std::string(40, ' ');
        }
        std::string right;
        if (i < snap.leaders.size()) {
            auto& L = snap.leaders[i];
            right = std::string(L.muted ? ROSE : GREEN) + (L.muted ? "MUTE " : " ON  ") + RST + PANEL + TEXT
                + "T" + std::to_string(L.num) + " " + pad(L.short_addr, 16)
                + " pos " + std::to_string(L.n_pos);
        }
        line(o, left + "   " + right);
    }

    line(o, "");
    line(o, std::string(DIM) + MUTED + " " + (snap.ts.empty() ? "" : snap.ts)
         + "   GET-only  ·  vpn: sudo rimbaud_vpn/rimbaud-vpn  ·  clips stay  " + CYAN + "q quit");
    o += RST;
    return o;
}

static std::vector<Snap> fetch_all(const std::vector<Site>& sites) {
    std::vector<Snap> out;
    out.reserve(sites.size());
    for (auto& s : sites) out.push_back(fetch(s));
    return out;
}

static std::string once_text(const std::vector<Site>& sites, const std::vector<Snap>& snaps, int sel) {
    std::string o;
    o += "rimBaud // META MATRIX  READ-ONLY  VPN 10.66.80.0/24 control-plane\n";
    for (size_t i = 0; i < sites.size(); i++) {
        const Site& site = sites[i];
        const Snap& sn = snaps[i];
        o += (static_cast<int>(i) == sel ? "* " : "  ");
        o += pad(site.name, 12) + " " + pad(site.host + ":" + std::to_string(site.port), 18);
        if (!sn.ok) o += " DOWN  " + sn.error + "\n";
        else o += " UP  BIAS " + sn.bias + "  IP " + std::to_string(static_cast<int>(sn.ip_left))
                + "/" + std::to_string(static_cast<int>(sn.ip_cap)) + "\n";
    }
    const Site& site = sites[static_cast<size_t>(sel)];
    const Snap& snap = snaps[static_cast<size_t>(sel)];
    o += "selected " + site.host + ":" + std::to_string(site.port) + "\n";
    if (!snap.ok) {
        o += "DOWN  " + snap.error + "\n";
        return o;
    }
    o += "UP  BIAS " + snap.bias + "  " + mute_label(snap)
       + "  eq " + money(snap.eq) + "  uPnL " + signed_money(snap.upnl)
       + "  pos " + std::to_string(snap.n_pos)
       + "  hunters " + std::to_string(snap.n_live) + "/" + std::to_string(snap.n_bots)
       + "  IP left " + std::to_string(static_cast<int>(snap.ip_left)) + "/"
       + std::to_string(static_cast<int>(snap.ip_cap))
       + "  RISK parked " + money(snap.parked) + "\n";
    o += "HUNTERS\n";
    for (auto& h : snap.hunters) {
        o += std::string(h.running ? " run " : " off ") + pad(h.name, 14) + " "
           + pad(money(h.eq), 10, true) + " pos " + std::to_string(h.n_pos)
           + "  " + signed_money(h.upnl) + "  bias " + h.bias + "\n";
    }
    o += "POSITIONS\n";
    if (snap.positions.empty()) o += "  (none)\n";
    for (auto& p : snap.positions)
        o += "  " + pad(p.bot, 12) + " " + pad(p.coin, 6) + " " + pad(p.side, 5)
           + " " + pct1(p.roe) + "\n";
    o += "COPY\n";
    if (snap.leaders.empty()) o += "  (n/a)\n";
    for (auto& L : snap.leaders)
        o += std::string(L.muted ? "  MUTE " : "  ON   ") + "T" + std::to_string(L.num)
           + " " + L.short_addr + "\n";
    o += "never CLOSE/OPEN from meta\n";
    return o;
}

static bool raw_term() {
    if (!isatty(STDIN_FILENO) || !isatty(STDOUT_FILENO)) return false;
    if (tcgetattr(STDIN_FILENO, &g_old_term) < 0) return false;
    termios t = g_old_term;
    t.c_lflag &= static_cast<tcflag_t>(~(ICANON | ECHO));
    t.c_cc[VMIN] = 0;
    t.c_cc[VTIME] = 0;
    if (tcsetattr(STDIN_FILENO, TCSANOW, &t) < 0) return false;
    g_raw = true;
    atexit(restore_term);
    fputs("\x1b[?1049h\x1b[?25l\x1b[0m", stdout);
    fflush(stdout);
    return true;
}

static int key_ready() {
    fd_set rf;
    FD_ZERO(&rf);
    FD_SET(STDIN_FILENO, &rf);
    timeval tv{0, 0};
    if (select(STDIN_FILENO + 1, &rf, nullptr, nullptr, &tv) <= 0) return 0;
    unsigned char c = 0;
    if (read(STDIN_FILENO, &c, 1) != 1) return 0;
    return c;
}

int main(int argc, char** argv) {
    bool once = false;
    for (int i = 1; i < argc; i++) {
        std::string a = argv[i];
        if (a == "--once" || a == "--headless") once = true;
        else if (a == "--help") {
            fprintf(stdout,
                "rimbaud-meta — read-only console for Spring matrices\n"
                "  GET 127.0.0.1:5004, 192.168.x.x, and VPN 10.66.80.x:5004.\n"
                "  Never POSTs. Never CLOSE/OPEN. VPN is control-plane only.\n\n"
                "  --once     print snapshots and exit\n"
                "  q          quit   j/k select site   r refresh\n"
                "  vpn:       sudo ../rimbaud_vpn/rimbaud-vpn init|add|join|status\n");
            return 0;
        }
    }

    auto sites = load_sites();
    int sel = 0;
    auto snaps = fetch_all(sites);

    if (once || !isatty(STDOUT_FILENO)) {
        fputs(once_text(sites, snaps, sel).c_str(), stdout);
        return snaps[0].ok ? 0 : 2;
    }
    if (!raw_term()) {
        fputs(once_text(sites, snaps, sel).c_str(), stdout);
        return snaps[0].ok ? 0 : 2;
    }

    signal(SIGINT, on_int);
    signal(SIGTERM, on_int);
    signal(SIGWINCH, on_winch);

    double last = 0;
    auto now = []() {
        timespec ts{};
        clock_gettime(CLOCK_MONOTONIC, &ts);
        return ts.tv_sec + ts.tv_nsec / 1e9;
    };

    Winsize W = winsz();
    fputs(frame(sites, snaps, sel, W).c_str(), stdout);
    fflush(stdout);
    last = now();

    while (g_run) {
        int k = key_ready();
        bool redraw = false;
        if (k == 'q' || k == 'Q' || k == 27) break;
        if (k == 'r' || k == 'R' || k == ' ') {
            snaps = fetch_all(sites);
            redraw = true;
        }
        if ((k == 'j' || k == 'J' || k == 14) && sel + 1 < static_cast<int>(sites.size())) {
            sel++;
            redraw = true;
        }
        if ((k == 'k' || k == 'K' || k == 16) && sel > 0) {
            sel--;
            redraw = true;
        }
        if (g_winch) {
            g_winch = 0;
            W = winsz();
            redraw = true;
        }
        if (now() - last >= 2.5) {
            snaps = fetch_all(sites);
            last = now();
            redraw = true;
        }
        if (redraw) {
            W = winsz();
            fputs(frame(sites, snaps, sel, W).c_str(), stdout);
            fflush(stdout);
        }
        usleep(40000);
    }
    restore_term();
    return 0;
}
