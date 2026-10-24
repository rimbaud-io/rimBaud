// Copyright 2026 Freiquell e.V., Fichtestraße 1-4, 08451 Crimmitschau, GERMANY www.rimbaud.io
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

#include "ui.hpp"

#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/Xatom.h>
#include <X11/keysym.h>
#include <X11/cursorfont.h>

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <map>
#include <set>
#include <string>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/wait.h>
#include <vector>

namespace {

struct Rect { int x, y, w, h; };
bool inside(const Rect& r, int x, int y) {
    return x >= r.x && y >= r.y && x < r.x + r.w && y < r.y + r.h;
}
bool rects_overlap(const Rect& a, const Rect& b) {
    return a.w > 0 && a.h > 0 && b.w > 0 && b.h > 0
        && a.x < b.x + b.w && a.x + a.w > b.x
        && a.y < b.y + b.h && a.y + a.h > b.y;
}
Rect norm_rect(int x0, int y0, int x1, int y1) {
    int x = std::min(x0, x1), y = std::min(y0, y1);
    return {x, y, std::abs(x1 - x0), std::abs(y1 - y0)};
}

// A paused hunter is frozen. Its status file still holds the last equity, so the
// matrix would keep showing that money. Drop it from the cards and the header.
// The exchange position stays. An unpaused hunter publishes its balance again.
std::set<std::string> paused_hunter_ids() {
    static std::set<std::string> cached;
    static time_t at = 0;
    time_t now = time(nullptr);
    if (at != 0 && now - at < 2) return cached;
    const char* names[] = {
        "hummingbot-hl", "hummingbot-bn-swing", "hummingbot-helliot", "hummingbot-surfn",
        "hummingbot-shortn", "hummingbot-dman", "hummingbot-onlyup", "hummingbot-dman-maker",
    };
    std::string cmd = "docker inspect -f '{{.Name}} {{.State.Paused}}'";
    for (auto* n : names) {
        cmd += " ";
        cmd += n;
    }
    cmd += " 2>/dev/null";
    FILE* fp = popen(cmd.c_str(), "r");
    if (!fp) return cached;
    std::set<std::string> found;
    char line[256];
    while (fgets(line, sizeof(line), fp)) {
        std::string row = line;
        while (!row.empty() && (row.back() == '\n' || row.back() == '\r')) row.pop_back();
        auto sp = row.find(' ');
        if (sp == std::string::npos) continue;
        std::string name = row.substr(0, sp);
        if (!name.empty() && name[0] == '/') name.erase(0, 1);
        if (row.substr(sp + 1) == "true") found.insert(name);
    }
    pclose(fp);
    cached.swap(found);
    at = now;
    return cached;
}

static std::string fold_addr(std::string a) {
    for (char& c : a)
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return a;
}

void hide_paused_funds(DeskSnap& snap) {
    auto paused = paused_hunter_ids();
    for (auto& b : snap.bots) {
        if (paused.count(b.id)) {
            b.deactivated = true;
            b.ok = false;
            b.equity = 0;
            b.free_usd = 0;
            b.upnl = 0;
            b.n_pos = 0;
            b.health = "deactivated";
            b.health_reason = "deactivated - 0 Hyperliquid IP tokens, positions stay on the exchange";
        } else if (!b.running) {
            b.equity = 0;
            b.free_usd = 0;
            b.upnl = 0;
            b.n_pos = 0;
        }
    }
    // One address is one balance. A down hunter is not counted.
    std::map<std::string, int> winner;
    std::vector<char> count(snap.bots.size(), 0);
    for (size_t i = 0; i < snap.bots.size(); ++i) {
        auto& b = snap.bots[i];
        if (!b.running || b.deactivated) continue;
        std::string addr = fold_addr(b.address);
        if (addr.size() < 8) {
            count[i] = 1;
            continue;
        }
        auto it = winner.find(addr);
        if (it == winner.end() || b.equity > snap.bots[static_cast<size_t>(it->second)].equity)
            winner[addr] = static_cast<int>(i);
    }
    for (auto& kv : winner) count[static_cast<size_t>(kv.second)] = 1;
    std::set<std::string> keep;
    double eq = 0, up = 0;
    int npos = 0, nok = 0, nlive = 0;
    for (size_t i = 0; i < snap.bots.size(); ++i) {
        auto& b = snap.bots[i];
        if (b.ok) nok++;
        if (b.running && !b.deactivated) nlive++;
        if (!count[i]) continue;
        eq += b.equity;
        up += b.upnl;
        npos += b.n_pos;
        keep.insert(b.id);
    }
    std::vector<Position> kept;
    kept.reserve(snap.positions.size());
    for (auto& p : snap.positions)
        if (keep.count(p.bot_id)) kept.push_back(p);
    snap.positions.swap(kept);
    snap.totals.equity = eq;
    snap.totals.upnl = up;
    snap.totals.n_pos = npos;
    snap.totals.n_ok = nok;
    snap.totals.n_live = nlive;
}

int node_w(const WireNode& n, bool expand = false);
int node_h(const WireNode& n, bool expand = false);
Rect node_view_box(const WireNode& n, bool exp, double zoom, int ox, int oy);

struct Colors {
    unsigned long bg, panel, border, green, green_dim, text, muted, rose, cyan, gold, white, amber, navy;
    unsigned long ab_bg, ab_panel, ab_grid, ab_orange, ab_clip, ab_text, ab_muted, ab_line, ab_head;
};

enum Page { PgHome, PgWire, PgCondor, PgHunters, PgFunds, PgStrategy, PgSystem, PgCount };
const char* PAGE_NAME[] = {"HOME", "WIRE", "CONDOR", "HUNTERS", "FUNDS", "STRATEGY", "SYSTEM"};

struct Hit {
    enum Kind { Idle, Nav, Hunter, NotWired, WireNode, WireEdge, WirePort, ToolCat, ToolItem, WireBoard, WireAct, Ctx, Backend, Chart, Sl, Lev, Restart, HunterGate, DeskBias, CoinBias, CoinClose, CoinOpen100, CtrlBool, CtrlKnob, CtrlSel, CtrlStd, CopyMute, CopySlots, CopyAudit, AuditClose, AuditRun, Quit, PackSave, PackRestore, WireSnap, AnnotColor, AnnotSize, InspectBack, CompileClose, CompileWork, CompileGo, PresetDrop, PresetPick, PresetSave, ApiKey, ReplYes, ReplNo } kind = Idle;
    int i = -1;
    Rect r{};
    std::string key;
    bool src = false;
};

unsigned long rgb(Display* d, Colormap cm, int r, int g, int b) {
    XColor c{};
    c.red = r * 257;
    c.green = g * 257;
    c.blue = b * 257;
    c.flags = DoRed | DoGreen | DoBlue;
    XAllocColor(d, cm, &c);
    return c.pixel;
}

unsigned long parse_hex(Display* d, Colormap cm, const std::string& s, unsigned long fallback) {
    if (s.size() < 7 || s[0] != '#') return fallback;
    int r = 0, g = 0, b = 0;
    if (sscanf(s.c_str() + 1, "%02x%02x%02x", &r, &g, &b) != 3) return fallback;
    return rgb(d, cm, r, g, b);
}

unsigned long hover_fill_col(Display* d, Colormap cm, const std::string& s, unsigned long fallback) {
    int r = 12, g = 24, b = 16;
    int cr = 0, cg = 0, cb = 0;
    if (s.size() >= 7 && s[0] == '#' && sscanf(s.c_str() + 1, "%02x%02x%02x", &cr, &cg, &cb) == 3) {
        r = 10 + cr * 72 / 255;
        g = 16 + cg * 72 / 255;
        b = 12 + cb * 72 / 255;
        return rgb(d, cm, r, g, b);
    }
    return fallback;
}

std::string money(double n) {
    char buf[48];
    snprintf(buf, sizeof(buf), "%s$%.2f", n < 0 ? "-" : "", std::fabs(n));
    return buf;
}

std::string ascii(const std::string& s, int maxn = 0) {
    std::string o;
    o.reserve(s.size());
    for (unsigned char c : s) {
        if (c >= 32 && c < 127) o += static_cast<char>(c);
        else if (c == '\n' || c == '\t' || c == '\r') o += ' ';
    }
    if (maxn > 0 && static_cast<int>(o.size()) > maxn) o.resize(static_cast<size_t>(maxn));
    return o;
}

static void wipe_secret(std::string& s) {
    for (char& c : s) c = '\0';
    s.clear();
}

static std::string desk_repo() {
    char exe[4096];
    ssize_t n = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
    std::string fallback = ".";
    if (n <= 0) return fallback;
    exe[n] = 0;
    std::string p(exe);
    auto s1 = p.rfind('/');
    if (s1 == std::string::npos || s1 == 0) return fallback;
    auto s2 = p.rfind('/', s1 - 1);
    if (s2 == std::string::npos) return fallback;
    return p.substr(0, s2);
}

// Public address only. The keystore stays in SQLite and is never drawn.
struct ApiPub {
    std::string address;
    std::string on_exchange;
    std::string updated_at;
    int n_files = 0;
};

static ApiPub read_api_pub() {
    ApiPub p;
    std::ifstream in(desk_repo() + "/conf/hl_api_pub.json");
    if (!in) return p;
    try {
        nlohmann::json j;
        in >> j;
        if (!j.is_object()) return p;
        auto gs = [&](const char* k) -> std::string {
            if (!j.contains(k) || !j[k].is_string()) return "";
            return j[k].get<std::string>();
        };
        p.address = gs("address");
        p.on_exchange = gs("on_exchange");
        p.updated_at = gs("updated_at");
        if (j.contains("n_files") && j["n_files"].is_number()) p.n_files = j["n_files"].get<int>();
    } catch (...) {}
    return p;
}

static std::string norm_api_secret(std::string raw) {
    std::string s;
    for (unsigned char c : raw) {
        if (c == ' ' || c == '\n' || c == '\r' || c == '\t') continue;
        s.push_back(static_cast<char>(c));
    }
    wipe_secret(raw);
    if (s.size() >= 2 && s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s.erase(0, 2);
    if (s.size() != 64) {
        wipe_secret(s);
        return "";
    }
    for (char& c : s) {
        if (c >= 'A' && c <= 'F') c = static_cast<char>(c - 'A' + 'a');
        else if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
            wipe_secret(s);
            return "";
        }
    }
    return "0x" + s;
}

static std::string api_result_message(const std::string& blob) {
    std::string line, cur;
    for (char c : blob) {
        if (c == '\n') {
            if (!cur.empty()) line = cur;
            cur.clear();
        } else if (c != '\r') cur.push_back(c);
    }
    if (!cur.empty()) line = cur;
    nlohmann::json j;
    try { j = nlohmann::json::parse(line.empty() ? "{}" : line); } catch (...) { j = nlohmann::json::object(); }
    if (!j.is_object() || j.empty()) return "API key was not saved — paste it again";
    bool ok = false;
    if (j.contains("ok") && j["ok"].is_boolean()) ok = j["ok"].get<bool>();
    if (ok) {
        if (j.contains("message") && j["message"].is_string()) {
            std::string m = j["message"].get<std::string>();
            if (!m.empty()) return m;
        }
        return "API key saved";
    }
    std::string err;
    if (j.contains("error") && j["error"].is_string()) err = j["error"].get<std::string>();
    if (err.empty()) return "API key was not saved — paste it again";
    auto p = err.rfind('\n');
    if (p != std::string::npos) err = err.substr(p + 1);
    if (err.size() > 180) err.resize(180);
    return err;
}

const int CHAR_W = 9;
std::string fit_px(const std::string& s, int px) {
    return ascii(s, std::max(1, px / CHAR_W));
}

// Missing sendEvery2Win means the skim is still on. A missing other bool is off.
bool ctrl_flag(const nlohmann::json& controller, const std::string& key) {
    bool missing_on = (key == "sendEvery2Win");
    if (!controller.is_object() || !controller.contains(key) || controller[key].is_null())
        return missing_on;
    const auto& v = controller[key];
    if (v.is_boolean()) return v.get<bool>();
    if (v.is_number()) return v.get<double>() != 0;
    if (v.is_string()) {
        std::string s = v.get<std::string>();
        for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        return s == "1" || s == "true" || s == "on" || s == "yes";
    }
    return missing_on;
}

void split_label(const std::string& s, int chars, std::string& a, std::string& b) {
    if (chars < 8) chars = 8;
    if (static_cast<int>(s.size()) <= chars) {
        a = s;
        b.clear();
        return;
    }
    int cut = chars;
    auto sp = s.rfind(' ', static_cast<size_t>(chars));
    if (sp != std::string::npos && sp >= 8) cut = static_cast<int>(sp);
    a = s.substr(0, static_cast<size_t>(cut));
    b = s.substr(static_cast<size_t>(cut));
    while (!b.empty() && b[0] == ' ') b.erase(b.begin());
}

std::string follow_coin(const Bot& b, const DeskSnap& snap) {
    for (auto& p : snap.positions) {
        if (p.bot_id == b.id && !p.coin.empty() && p.size != 0) return p.coin;
    }
    if (!b.held_coins.empty()) return b.held_coins[0];
    for (auto& c : b.coins) {
        auto it = b.bias.find(c);
        if (it != b.bias.end() && (it->second == "LONG" || it->second == "SHORT"))
            return c;
    }
    if (!b.coins.empty()) return b.coins[0];
    return "BTC";
}

std::string short_addr(const std::string& a) {
    if (a.size() < 14) return a.empty() ? "-" : a;
    return a.substr(0, 8) + "..." + a.substr(a.size() - 6);
}

std::string pct_s(double n) {
    char b[32];
    snprintf(b, sizeof(b), "%+.2f%%", n);
    return b;
}

int find_node(const DeskSnap& s, const std::string& id);
std::string uniq_id(const DeskSnap& s, const std::string& base);
bool best_link(const WireNode& a, const WireNode& b, std::string& sh, std::string& th, bool& rev);
bool handles_ok(const std::string& src, const std::string& tgt);

const double WIRE_GRID = 48.0;
double snap_grid(double v) {
    return std::round(v / WIRE_GRID) * WIRE_GRID;
}

CtrlField mkf(const char* key, const char* label, const char* kind,
              double step = 1, double minv = 0, double maxv = 100) {
    CtrlField f;
    f.key = key;
    f.label = label;
    f.kind = kind;
    f.step = step;
    f.minv = minv;
    f.maxv = maxv;
    return f;
}

void tune_field_range(CtrlField& f) {
    if (f.kind == "bool") { f.minv = 0; f.maxv = 1; f.step = 1; return; }
    if (f.kind == "select" || f.kind == "price_pct") return;
    if (f.maxv > 0 && f.maxv != 100 && f.maxv != 200 && f.maxv != 1) return;
    if (f.kind == "pct_frac" && f.maxv <= 1) f.maxv = 100;
    const std::string& k = f.key;
    auto has = [&](const char* s) { return k.find(s) != std::string::npos; };
    if (has("bbp") || has("bb_")) { if (f.maxv > 10) { f.minv = 0; f.maxv = 1.2; } }
    else if (has("wave")) { if (f.maxv > 10) { f.minv = 0; f.maxv = 2; } }
    else if (has("roe") || has("sl_")) { if (f.minv == 0 && f.maxv >= 100) { f.minv = 1; f.maxv = 200; } }
    else if (has("lev")) { f.minv = 1; f.maxv = 50; }
    else if (has("poll")) { f.minv = 2; f.maxv = 30; }
    else if (has("refresh")) { f.minv = 10; f.maxv = 600; }
    else if (has("books") || has("positions_per")) { f.minv = 1; f.maxv = 12; }
}

std::vector<CtrlField> standard_fields(const std::string& id) {
    std::vector<CtrlField> fs;
    if (id == "hummingbot-hl") {
        fs.push_back(mkf("copy_shorts", "Copy shorts", "bool"));
        fs.push_back(mkf("max_leverage", "Max lev", "num", 1, 1, 50));
        fs.push_back(mkf("max_positions_per_trader", "Pos / trader", "num", 1, 1, 8));
        fs.push_back(mkf("max_position_loss_pct", "Max loss % eq", "pct_frac", 1, 0, 100));
        fs.push_back(mkf("min_free_margin_pct", "Min free %", "pct_frac", 1, 0, 100));
        fs.push_back(mkf("max_subscribed_markets", "Max books", "num", 1, 3, 12));
        fs.push_back(mkf("poll_interval_seconds", "Poll sec", "num", 1, 2, 30));
        fs.push_back(mkf("max_zec_short_usd", "Max short ZEC $", "num", 10, 0, 500000));
    } else if (id == "hummingbot-bn-swing") {
        fs.push_back(mkf("open_on_bias", "Open on BIAS", "bool"));
        fs.push_back(mkf("go_in_after_close", "Go in after close", "bool"));
        fs.push_back(mkf("scale_x", "SCALE x", "num", 0.01, 0, 1));
        fs.push_back(mkf("take_profit", "TP price %", "price_pct", 0.01, 0.80, 6.00));
        fs.push_back(mkf("trail_activation", "Trail ARM %", "price_pct", 0.01, 0.40, 4.00));
        fs.push_back(mkf("trail_delta", "Trail %", "num", 1, 8, 40));
        fs.push_back(mkf("rebuy_pct", "NEXT IN %", "price_pct", 0.01, 0.05, 0.80));
        fs.push_back(mkf("executor_refresh_time", "Refresh s", "num", 1, 8, 90));
        fs.push_back(mkf("sl_roe_pct", "SL ROE %", "num", 1, 15, 70));
        fs.push_back(mkf("margin_pct", "Margin %", "pct_frac", 1, 20, 100));
    } else if (id == "hummingbot-helliot") {
        fs.push_back(mkf("tp_roe_pct", "Take profit ROE %", "num", 1, 1, 200));
        fs.push_back(mkf("sl_roe_pct", "Stop loss ROE %", "num", 1, 1, 200));
        fs.push_back(mkf("wave2_min", "Wave2 min", "num", 0.001, 0, 2));
        fs.push_back(mkf("wave2_max", "Wave2 max", "num", 0.001, 0, 2));
        fs.push_back(mkf("wave3_ext", "Wave3 ext", "num", 0.001, 0, 2));
        fs.push_back(mkf("margin_pct", "Margin % leftover", "pct_frac", 1, 0, 100));
    } else if (id == "hummingbot-surfn") {
        fs.push_back(mkf("st_length", "ST length", "num", 1, 5, 80));
        fs.push_back(mkf("st_mult", "ST multiplier", "num", 0.1, 1, 10));
        fs.push_back(mkf("percentage_threshold", "ST distance %", "pct_frac", 0.1, 0, 10));
        fs.push_back(mkf("scale_roe_pct", "Clip at ROE %", "num", 1, 1, 200));
        fs.push_back(mkf("scale_out_pct", "Clip size %", "pct_frac", 1, 0, 100));
        fs.push_back(mkf("sl_roe_pct", "Stop loss ROE %", "num", 1, 1, 200));
        fs.push_back(mkf("margin_pct", "Margin % leftover", "pct_frac", 1, 0, 100));
    } else if (id == "hummingbot-shortn") {
        fs.push_back(mkf("tp_roe_pct", "Take profit ROE %", "num", 1, 1, 200));
        fs.push_back(mkf("sl_roe_pct", "Stop loss ROE %", "num", 1, 1, 200));
        fs.push_back(mkf("recover_roe_pct", "Rebuy bounce ROE %", "num", 1, 1, 200));
        fs.push_back(mkf("bb_short_threshold", "Short BBP ≥", "num", 0.01, 0, 1.2));
        fs.push_back(mkf("margin_pct", "Margin % leftover", "pct_frac", 1, 0, 100));
    } else if (id == "hummingbot-dman") {
        fs.push_back(mkf("trail_activation", "Trail arm %", "pct_frac", 0.1, 0, 100));
        fs.push_back(mkf("trail_delta", "Trail delta %", "pct_frac", 0.1, 0, 100));
        fs.push_back(mkf("bb_long_threshold", "Rebuy BBP ≤", "num", 0.01, 0, 1.2));
        fs.push_back(mkf("bb_short_threshold", "Short BBP ≥", "num", 0.01, 0, 1.2));
        CtrlField h = mkf("hold_h", "Hold hours", "select", 1, 12, 68);
        h.options = {"12", "24", "68"};
        fs.push_back(h);
        fs.push_back(mkf("sl_roe_pct", "Stop loss ROE %", "num", 1, 1, 200));
        fs.push_back(mkf("margin_pct", "Margin % leftover", "pct_frac", 1, 0, 100));
    } else if (id == "hummingbot-onlyup") {
        fs.push_back(mkf("close_px", "Close $", "num", 0.01, 0, 1000000));
        fs.push_back(mkf("scale_roe_pct", "Clip at ROE %", "num", 1, 1, 2000));
        fs.push_back(mkf("scale_out_pct", "Clip size %", "pct_frac", 1, 0, 100));
        fs.push_back(mkf("sl_roe_pct", "Stop loss ROE %", "num", 1, 1, 200));
        fs.push_back(mkf("st_max_dist", "ST max dist %", "pct_frac", 0.1, 0, 100));
        fs.push_back(mkf("margin_pct", "Margin % leftover", "pct_frac", 1, 0, 100));
    } else if (id == "hummingbot-dman-maker") {
        fs.push_back(mkf("sendEvery2Win", "send every 2 win to risk wallet", "bool"));
        fs.push_back(mkf("dontGoCrazy", "dontGoCrazy", "bool"));
        fs.push_back(mkf("afterLossNoRebuy", "afterLossNoRebuy", "bool"));
        fs.push_back(mkf("take_profit", "Take profit %", "pct_frac", 0.1, 0, 100));
        fs.push_back(mkf("trail_activation", "Trail arm %", "pct_frac", 0.1, 0, 100));
        fs.push_back(mkf("trail_delta", "Trail delta %", "pct_frac", 0.1, 0, 100));
        fs.push_back(mkf("executor_refresh_time", "Refresh sec", "num", 1, 10, 600));
        fs.push_back(mkf("sl_roe_pct", "Stop loss ROE %", "num", 1, 1, 200));
        fs.push_back(mkf("margin_pct", "Margin % leftover", "pct_frac", 1, 0, 100));
    }
    return fs;
}

double snap_ctrl(double v, double minv, double maxv, double step) {
    if (step <= 0) step = 1;
    v = std::round(v / step) * step;
    if (v < minv) v = minv;
    if (v > maxv) v = maxv;
    return v;
}

nlohmann::json store_ctrl(const CtrlField& f, double display) {
    double v = snap_ctrl(display, f.minv, f.maxv, f.step);
    if (f.kind == "bool") return v >= 0.5;
    if (f.kind == "select") {
        if (f.options.empty()) return v;
        int i = 0;
        for (int k = 0; k < (int)f.options.size(); ++k) {
            try { if (std::fabs(std::stod(f.options[k]) - v) < 0.001) i = k; } catch (...) {}
        }
        try { return std::stod(f.options[i]); } catch (...) { return f.options[i]; }
    }
    // pct_frac: 60 leftover → 0.60. price_pct: 2.40 TP → 0.024 always.
    if (f.kind == "price_pct") v = v / 100.0;
    else if (f.kind == "pct_frac" && v > 1.0) v = v / 100.0;
    return v;
}

struct PmmDisp {
    double x = 0.3, tp = 2.40, arm = 1.40, trail = 22, next = 0.18;
    double refresh = 20, sl = 40, margin = 60;
};

double pmm_clamp(double v, double lo, double hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

double pmm_trail_display(double stored, double arm_disp) {
    if (stored >= 8) return stored;
    if (stored > 0.05) return stored * 100.0;
    double arm_frac = arm_disp / 100.0;
    if (arm_frac <= 1e-9) return 22;
    return stored / arm_frac * 100.0;
}

double pmm_price_delta(const PmmDisp& d) {
    return d.arm * d.trail / 100.0;
}

void pmm_from_x(PmmDisp& d) {
    double x = pmm_clamp(d.x, 0, 1);
    d.x = x;
    d.tp = 0.80 + x * (6.00 - 0.80);
    d.arm = 0.40 + x * (4.00 - 0.40);
    d.trail = 8 + x * (40 - 8);
    d.next = 0.05 + x * (0.80 - 0.05);
    d.sl = 15 + std::pow(x, 0.75) * (70 - 15);
    d.margin = 20 + x * (100 - 20);
    d.refresh = 90 - x * (90 - 8);
}

void pmm_couple(PmmDisp& d, double equity, double leftover, const std::string& edited) {
    d.tp = pmm_clamp(d.tp, 0.80, 6.00);
    d.arm = pmm_clamp(d.arm, 0.40, 4.00);
    d.trail = std::round(pmm_clamp(d.trail, 8, 40));
    d.next = pmm_clamp(d.next, 0.05, 0.80);
    d.refresh = std::round(pmm_clamp(d.refresh, 8, 90));
    d.sl = std::round(pmm_clamp(d.sl, 15, 70));
    d.margin = std::round(pmm_clamp(d.margin, 20, 100));
    d.x = pmm_clamp(d.x, 0, 1);
    if (d.arm > d.tp) d.arm = d.tp;
    if (pmm_price_delta(d) + 1e-9 < d.next) {
        if (d.arm > 1e-9) d.trail = std::ceil(d.next / d.arm * 100.0 - 1e-12);
        if (d.trail > 40) d.trail = 40;
        if (pmm_price_delta(d) + 1e-9 < d.next && d.trail >= 8) {
            d.arm = pmm_clamp(d.next * 100.0 / d.trail, 0.40, d.tp);
        }
        if (pmm_price_delta(d) + 1e-9 < d.next && edited != "rebuy_pct")
            d.next = pmm_clamp(pmm_price_delta(d), 0.05, 0.80);
    }
    if (d.next > pmm_price_delta(d) + 1e-9) d.next = pmm_clamp(pmm_price_delta(d), 0.05, 0.80);
    if (d.next > d.arm) d.next = pmm_clamp(d.arm, 0.05, 0.80);
    if (edited == "scale_x") {
        double soft = 0.5 * (d.sl / 10.0);
        if (soft < 0.80) soft = 0.80;
        if (d.tp > soft) d.tp = std::min(d.tp, std::max(soft, 2.40));
        if (d.arm > d.tp) d.arm = d.tp;
    }
    if (d.next < 0.08) d.refresh = std::max(d.refresh, 30.0);
    else if (d.next < 0.12) d.refresh = std::max(d.refresh, 20.0);
    // Don't override an explicit Margin knob / SAVE. Red ring still flags 100% on tiny leftover.
    if (edited != "margin_pct" && edited != "save"
        && equity > 0 && leftover / equity <= 0.30 && d.margin > 70)
        d.margin = 70;
}

PmmDisp pmm_read(const Bot& b) {
    PmmDisp d;
    auto get = [&](const char* k, const CtrlField& f, double fb) {
        if (!b.controller.contains(k)) return fb;
        return display_value(f, b.controller[k]);
    };
    CtrlField fx = mkf("scale_x", "SCALE x", "num", 0.01, 0, 1);
    CtrlField ftp = mkf("take_profit", "TP", "price_pct", 0.01, 0.80, 6);
    CtrlField fa = mkf("trail_activation", "ARM", "price_pct", 0.01, 0.40, 4);
    CtrlField ftr = mkf("trail_delta", "Trail", "num", 1, 8, 40);
    CtrlField fn = mkf("rebuy_pct", "NEXT", "price_pct", 0.01, 0.05, 0.80);
    CtrlField fr = mkf("executor_refresh_time", "R", "num", 1, 8, 90);
    CtrlField fs = mkf("sl_roe_pct", "SL", "num", 1, 15, 70);
    CtrlField fm = mkf("margin_pct", "M", "pct_frac", 1, 20, 100);
    d.x = get("scale_x", fx, 0.3);
    d.tp = get("take_profit", ftp, 2.40);
    d.arm = get("trail_activation", fa, 1.40);
    d.trail = pmm_trail_display(get("trail_delta", ftr, 22), d.arm);
    d.next = get("rebuy_pct", fn, 0.18);
    d.refresh = get("executor_refresh_time", fr, 20);
    d.sl = get("sl_roe_pct", fs, 40);
    d.margin = get("margin_pct", fm, 60);
    return d;
}

nlohmann::json pmm_store(const PmmDisp& d) {
    return {
        {"scale_x", d.x},
        {"take_profit", d.tp / 100.0},
        {"trail_activation", d.arm / 100.0},
        {"trail_delta", d.trail},
        {"rebuy_pct", d.next / 100.0},
        {"executor_refresh_time", d.refresh},
        {"sl_roe_pct", d.sl},
        {"margin_pct", d.margin / 100.0},
    };
}

void pmm_apply_bot(Bot& b, const PmmDisp& d) {
    auto j = pmm_store(d);
    for (auto it = j.begin(); it != j.end(); ++it) b.controller[it.key()] = it.value();
    b.controller.erase("rebuy_px");
}

std::string hunter_tag(const std::string& id, const std::string& name) {
    if (id == "hummingbot-bn-swing") return "PMM";
    if (id == "hummingbot-hl") return "Copy";
    if (id == "hummingbot-helliot") return "Helliot";
    if (id == "hummingbot-surfn") return "Surfn";
    if (id == "hummingbot-shortn") return "Shorty";
    if (id == "hummingbot-dman") return "Dman";
    if (id == "hummingbot-onlyup") return "OnlyUp";
    if (id == "hummingbot-dman-maker") return "DManM";
    if (!name.empty()) return ascii(name, 8);
    return ascii(id, 8);
}

bool preset_is_std(const std::string& n) {
    std::string s;
    for (unsigned char c : n) s.push_back(static_cast<char>(std::tolower(c)));
    return s.empty() || s == "std" || s == "standard";
}

nlohmann::json pmm_values_for_save(const Bot& b) {
    PmmDisp d = pmm_read(b);
    auto vals = pmm_store(d);
    if (b.controller.contains("open_on_bias"))
        vals["open_on_bias"] = b.controller["open_on_bias"];
    if (b.controller.contains("go_in_after_close"))
        vals["go_in_after_close"] = b.controller["go_in_after_close"];
    return vals;
}

nlohmann::json hunter_values_for_save(const Bot& b) {
    if (b.id == "hummingbot-bn-swing") return pmm_values_for_save(b);
    nlohmann::json vals = nlohmann::json::object();
    if (!b.controller.is_object()) return vals;
    for (auto it = b.controller.begin(); it != b.controller.end(); ++it) {
        if (it.key() == "updated" || it.key() == "source") continue;
        vals[it.key()] = it.value();
    }
    return vals;
}

struct DeskUi {
    FlaskApi* api = nullptr;
    DeskSnap snap;
    Page page = PgHome;
    int sel = 0;
    int scroll = 0;
    int content_h = 0;
    int wire_sel = -1;
    std::string tool_pick_id;
    int wire_inspect = -1;
    std::set<std::string> wire_marked;
    std::set<std::string> marquee_keep;
    bool marquee_on = false;
    int marquee_x0 = 0, marquee_y0 = 0;
    Rect marquee_r{};
    struct DragOrig { int i = -1; double x = 0, y = 0; };
    std::vector<DragOrig> drag_orig;
    int last_click_i = -1;
    double last_click_t = 0;
    bool compile_win = false;
    int compile_scroll = 0;
    int compile_h = 0;
    int compile_body_h = 0;
    Rect compile_win_r{};
    bool audit_win = false;
    int audit_scroll = 0;
    int audit_h = 0;
    int audit_body_h = 0;
    Rect audit_win_r{};
    int wire_board = 0;
    int tb_scroll = 0;
    unsigned tb_fold = ~1u; // Hummingbots open; other toolbox groups start folded
    Rect canvas_r{}, tb_r{}, insp_r{}, np_r{}, hunter_main_r{};
    int hunter_log_scroll = 0;
    int hunter_log_h = 0;
    int hunter_log_body = 0;
    Rect hunter_logs_r{};
    int hunter_logs_scroll = 0;
    int hunter_logs_h = 0;
    int hunter_logs_body = 0;
    int hunter_sel_was = -1;
    double pan_x = 0, pan_y = 0, zoom = 0.55;
    bool wire_fitted = false;
    bool wire_snap = true;
    bool panning = false;
    int pan_mx = 0, pan_my = 0;
    int mx_last = 0, my_last = 0;
    double pan_sx = 0, pan_sy = 0;
    bool dirty = false;
    bool node_drag = false;
    bool annot_size_drag = false;
    bool drag_moved = false;
    int drag_i = -1;
    int annot_sw = 0, annot_sh = 0;
    int drag_mx = 0, drag_my = 0;
    double drag_nx = 0, drag_ny = 0;
    bool tool_drag = false;
    bool tool_drag_moved = false;
    int tool_drag_i = -1;
    bool repl_on = false;
    std::string repl_tool_id;
    std::string repl_target_id;
    Rect repl_win_r{};
    int wire_from = -1;
    std::string wire_handle;
    bool wire_from_src = true;
    int edge_sel = -1;
    bool ctx_on = false;
    int ctx_x = 0, ctx_y = 0, ctx_kind = 0, ctx_i = -1, ctx_scroll = 0;
    double ctx_wx = 0, ctx_wy = 0;
    std::vector<std::pair<std::vector<WireNode>, std::vector<WireEdge>>> undo, redo;
    std::string toast;
    double toast_until = 0;
    std::string arm_key;
    double arm_until = 0;
    std::string hover_tip;
    Rect hover_r{};
    int hover_bot = -1;
    std::vector<Hit> hits;
    double last_poll = 0;
    double last_wire = 0;
    bool fields_ok = false;
    bool qa_ok = false;
    bool backup_ok = false;
    int inspect_scroll = 0;
    int inspect_h = 0;
    bool knob_drag = false;
    int knob_bot = -1;
    std::string knob_key;
    double knob_start = 0;
    double knob_live = 0;
    double knob_min = 0;
    double knob_max = 100;
    double knob_step = 1;
    std::string knob_kind;
    int knob_mx = 0, knob_my = 0;
    double knob_posted = 0;
    double knob_live_sent = 0;
    double live_knob_pull = 0;
    nlohmann::json presets = nlohmann::json::array();
    std::string preset_cur = "STD";
    std::string preset_bot;
    std::map<std::string, std::string> preset_by_bot;
    bool preset_open = false;
    bool preset_edit = false;
    std::string preset_buf;
    Rect preset_drop_r{};
    int knob_focus_bot = -1;
    std::string knob_focus_key;
    bool knob_edit = false;
    int knob_edit_bot = -1;
    std::string knob_edit_key;
    std::string knob_edit_buf;
    bool knob_edit_sel = false;
    int last_knob_bot = -1;
    std::string last_knob_key;
    double last_knob_t = 0;
    bool api_edit = false;
    bool api_busy = false;
    std::string api_buf;
    std::string api_addr;
    std::string api_exch;
    std::string api_note;

    double now() {
        timespec ts{};
        clock_gettime(CLOCK_MONOTONIC, &ts);
        return ts.tv_sec + ts.tv_nsec / 1e9;
    }

    void say(const std::string& s) {
        toast = s;
        toast_until = now() + 3.5;
    }

    bool arm(const std::string& key, const std::string& prompt) {
        if (arm_key == key && now() < arm_until) {
            arm_key.clear();
            return true;
        }
        arm_key = key;
        arm_until = now() + 5;
        say(prompt);
        return false;
    }

    void not_wired() {
        say("not wired — live hunters / positions untouched");
    }

    void push_hist() {
        undo.push_back({snap.wire_nodes, snap.wire_edges});
        if (undo.size() > 40) undo.erase(undo.begin());
        redo.clear();
        dirty = true;
        snap.compile.can_integrate = false;
        snap.compile.ok = false;
    }

    void do_undo() {
        if (undo.empty()) { say("nothing to undo"); return; }
        redo.push_back({snap.wire_nodes, snap.wire_edges});
        snap.wire_nodes = undo.back().first;
        snap.wire_edges = undo.back().second;
        undo.pop_back();
        dirty = true;
        say("undo");
    }

    void do_redo() {
        if (redo.empty()) { say("nothing to redo"); return; }
        undo.push_back({snap.wire_nodes, snap.wire_edges});
        snap.wire_nodes = redo.back().first;
        snap.wire_edges = redo.back().second;
        redo.pop_back();
        dirty = true;
        say("redo");
    }

    void add_auto_edges(const WireNode& n) {
        auto has = [&](const std::string& id) { return find_node(snap, id) >= 0; };
        auto push_e = [&](const std::string& id, const std::string& s, const std::string& sh,
                          const std::string& t, const std::string& th) {
            for (auto& e : snap.wire_edges)
                if (e.source == s && e.target == t && e.source_handle == sh && e.target_handle == th) return;
            WireEdge e;
            e.id = id; e.source = s; e.target = t; e.source_handle = sh; e.target_handle = th;
            snap.wire_edges.push_back(e);
        };
        if (n.type == "bot") {
            std::string bid = n.id;
            if (has("bias-" + bid)) push_e("e-bias-" + bid, "bias-" + bid, "bias", bid, "bias");
            if (has("wallet-" + bid)) push_e("e-wallet-" + bid, "wallet-" + bid, "wallet", bid, "wallet");
            if (has("ctrl-" + bid)) push_e("e-ctrl-" + bid, "ctrl-" + bid, "ctrl", bid, "ctrl");
            if (has("logs")) push_e("e-logs-" + bid, bid, "logs", "logs", "in");
        }
        if (n.type == "bias" && !n.bot_id.empty() && has(n.bot_id))
            push_e("e-bias-" + n.bot_id, n.id, "bias", n.bot_id, "bias");
        if (n.type == "ctrl" && !n.bot_id.empty() && has(n.bot_id))
            push_e("e-ctrl-" + n.bot_id, n.id, "ctrl", n.bot_id, "ctrl");
        if (n.type == "wallet" && !n.bot_id.empty() && has(n.bot_id))
            push_e("e-wallet-" + n.bot_id, n.id, "wallet", n.bot_id, "wallet");
        if (n.type == "logs") {
            for (auto& o : snap.wire_nodes)
                if (o.type == "bot") push_e("e-logs-" + o.id, o.id, "logs", n.id, "in");
        }
    }

    static bool is_v2_sleeper_id(const std::string& id) {
        return id.rfind("hummingbot-v2-", 0) == 0;
    }

    void spawn_v2_io(WireNode& bot) {
        auto spawn = [&](const std::string& sid, const std::string& typ, const std::string& lab, double dx, double dy) {
            if (find_node(snap, sid) >= 0) return;
            WireNode s;
            s.id = sid;
            s.type = typ;
            s.label = lab;
            s.color = bot.color;
            s.bot_id = bot.id;
            s.x = bot.x + dx;
            s.y = bot.y + dy;
            snap.wire_nodes.push_back(s);
            add_auto_edges(s);
        };
        bool has_bias = false;
        for (auto& t : snap.tools)
            if (t.id == "bias-" + bot.id) { has_bias = true; break; }
        spawn("wallet-" + bot.id, "wallet", bot.label + " wallet", -170, 0);
        spawn("ctrl-" + bot.id, "ctrl", bot.label + " controller", -170, 90);
        if (has_bias)
            spawn("bias-" + bot.id, "bias", bot.label + " BIAS", -170, 180);
        add_auto_edges(bot);
    }

    void strip_v2_io(const std::string& bot_id) {
        std::set<std::string> drop{
            "wallet-" + bot_id, "ctrl-" + bot_id, "bias-" + bot_id
        };
        snap.wire_nodes.erase(std::remove_if(snap.wire_nodes.begin(), snap.wire_nodes.end(),
            [&](const WireNode& wn) { return drop.count(wn.id); }), snap.wire_nodes.end());
        snap.wire_edges.erase(std::remove_if(snap.wire_edges.begin(), snap.wire_edges.end(),
            [&](const WireEdge& e) { return drop.count(e.source) || drop.count(e.target); }),
            snap.wire_edges.end());
        for (auto& d : drop) wire_marked.erase(d);
    }

    bool add_tool_at(const ToolItem& t, double wx, double wy) {
        if (t.unique && find_node(snap, t.id) >= 0) {
            wire_sel = find_node(snap, t.id);
            say(t.label + " already on canvas");
            return false;
        }
        push_hist();
        WireNode n;
        n.id = t.unique ? t.id : uniq_id(snap, t.id);
        n.type = t.type;
        n.label = t.label;
        n.color = t.color;
        n.kind = t.kind;
        n.x = wire_snap ? snap_grid(wx) : wx;
        n.y = wire_snap ? snap_grid(wy) : wy;
        if (n.type == "frame") {
            n.extra = "384x240";
            if (n.label.empty() || n.label == t.label) n.label = "GROUP";
        }
        if (n.type == "circle") {
            n.extra = "192x192";
            if (n.label.empty() || n.label == t.label) n.label = "NOTE";
        }
        if (n.id.rfind("ctrl-", 0) == 0) n.bot_id = n.id.substr(5);
        else if (n.id.rfind("wallet-", 0) == 0) n.bot_id = n.id.substr(7);
        else if (n.id.rfind("bias-", 0) == 0) n.bot_id = n.id.substr(5);
        else if (n.type == "bot") n.bot_id = n.id;
        snap.wire_nodes.push_back(n);
        add_auto_edges(n);
        if (n.type == "bot" && is_v2_sleeper_id(n.id)) {
            spawn_v2_io(n);
            if (api) say(n.label + " · " + api->sleeper_start(n.id));
        }
        wire_sel = find_node(snap, n.id);
        wire_marked = {n.id};
        if ((n.type == "frame" || n.type == "circle") && api) {
            auto msg = api->save_snapshot(snap);
            if (msg.find("saved") != std::string::npos) dirty = false;
            say("added " + n.label + " · " + msg);
        } else if (!(n.type == "bot" && is_v2_sleeper_id(n.id))) {
            say("added " + n.label);
        }
        return true;
    }

    void retarget_id(const std::string& oid, const std::string& nid) {
        if (oid.empty() || nid.empty() || oid == nid) return;
        std::vector<WireEdge> kept;
        kept.reserve(snap.wire_edges.size());
        for (auto& e : snap.wire_edges) {
            WireEdge ne = e;
            if (ne.source == oid) ne.source = nid;
            if (ne.target == oid) ne.target = nid;
            if (ne.source.empty() || ne.target.empty() || ne.source == ne.target) continue;
            bool dup = false;
            for (auto& k : kept) {
                if (k.source == ne.source && k.target == ne.target
                    && k.source_handle == ne.source_handle && k.target_handle == ne.target_handle) {
                    dup = true;
                    break;
                }
            }
            if (!dup) kept.push_back(ne);
        }
        snap.wire_edges.swap(kept);
    }

    // Topmost card under the pointer. Frames sit behind the other nodes.
    int node_under_canvas(int mx, int my) const {
        if (!inside(canvas_r, mx, my)) return -1;
        int found = -1;
        auto consider = [&](int i) {
            const auto& n = snap.wire_nodes[i];
            int ox = canvas_r.x + static_cast<int>((n.x - pan_x) * zoom);
            int oy = canvas_r.y + static_cast<int>((n.y - pan_y) * zoom);
            Rect nr = node_view_box(n, false, zoom, ox, oy);
            if (inside(nr, mx, my)) found = i;
        };
        for (int i = 0; i < static_cast<int>(snap.wire_nodes.size()); ++i)
            if (snap.wire_nodes[i].type == "frame" || snap.wire_nodes[i].type == "circle") consider(i);
        for (int i = 0; i < static_cast<int>(snap.wire_nodes.size()); ++i)
            if (snap.wire_nodes[i].type != "frame" && snap.wire_nodes[i].type != "circle") consider(i);
        return found;
    }

    static std::string ctrl_parent_id(const WireNode& n) {
        if (!n.bot_id.empty()) return n.bot_id;
        if (n.id.rfind("ctrl-", 0) == 0) return n.id.substr(5);
        if (n.type == "bot") return n.id;
        return "";
    }

    // Bot or ctrl under the cursor. A v2 bot dropped on its ctrl card targets that bot.
    int drop_replace_target(const ToolItem& t, int hit) const {
        if (hit < 0 || hit >= static_cast<int>(snap.wire_nodes.size())) return -1;
        if (t.type != "bot" && t.type != "ctrl") return -1;
        const auto& n = snap.wire_nodes[hit];
        if (n.type != "bot" && n.type != "ctrl") return -1;
        if (t.type == "bot" && is_v2_sleeper_id(t.id) && n.type == "ctrl") {
            std::string parent = ctrl_parent_id(n);
            if (!is_v2_sleeper_id(parent)) return -1;
            int bi = find_node(snap, parent);
            if (bi < 0 || snap.wire_nodes[bi].type != "bot") return -1;
            if (snap.wire_nodes[bi].id == t.id) return -1;
            return bi;
        }
        if (n.type != t.type || n.id == t.id) return -1;
        return hit;
    }

    void open_replace(const ToolItem& t, int target) {
        if (target < 0 || target >= static_cast<int>(snap.wire_nodes.size())) return;
        int here = find_node(snap, t.id);
        if (t.unique && here >= 0) {
            wire_sel = here;
            say(t.label + " already on canvas");
            return;
        }
        repl_on = true;
        repl_tool_id = t.id;
        repl_target_id = snap.wire_nodes[target].id;
        ctx_on = false;
    }

    // Right-click. A standard hummingbots_v2 row uses the v2 controller, not a live hunter.
    void ask_replace(const ToolItem& t) {
        if (t.type != "bot" && t.type != "ctrl") {
            say("right-click replaces a controller");
            return;
        }
        int here = find_node(snap, t.id);
        if (t.unique && here >= 0) {
            wire_sel = here;
            say(t.label + " already on canvas");
            return;
        }
        int target = -1;
        if (t.type == "bot" && is_v2_sleeper_id(t.id)) {
            if (wire_sel >= 0 && wire_sel < static_cast<int>(snap.wire_nodes.size())) {
                auto& sel = snap.wire_nodes[wire_sel];
                if (sel.type == "bot" && is_v2_sleeper_id(sel.id)) target = wire_sel;
                else if (sel.type == "ctrl") {
                    std::string parent = ctrl_parent_id(sel);
                    if (is_v2_sleeper_id(parent)) {
                        int bi = find_node(snap, parent);
                        if (bi >= 0 && snap.wire_nodes[bi].type == "bot") target = bi;
                    }
                }
            }
            if (target < 0) {
                int nmatch = 0, only = -1;
                for (int i = 0; i < static_cast<int>(snap.wire_nodes.size()); ++i) {
                    if (snap.wire_nodes[i].type == "bot" && is_v2_sleeper_id(snap.wire_nodes[i].id)) {
                        nmatch++;
                        only = i;
                    }
                }
                if (nmatch == 1) target = only;
                else if (nmatch == 0) {
                    say("no controller on the canvas — drag one on first");
                    return;
                } else {
                    say("drop it on the controller to replace");
                    return;
                }
            }
        } else {
            if (wire_sel >= 0 && wire_sel < static_cast<int>(snap.wire_nodes.size())
                && snap.wire_nodes[wire_sel].type == t.type)
                target = wire_sel;
            if (target < 0) {
                int nmatch = 0, only = -1;
                for (int i = 0; i < static_cast<int>(snap.wire_nodes.size()); ++i) {
                    if (snap.wire_nodes[i].type != t.type) continue;
                    nmatch++;
                    only = i;
                }
                if (nmatch == 1) target = only;
                else if (nmatch == 0) {
                    say("no controller on the canvas — drag one on first");
                    return;
                } else {
                    say("select the controller on the canvas, then right-click the new one");
                    return;
                }
            }
        }
        open_replace(t, target);
    }

    // Position stays. Wires follow. The ctrl card becomes the new controller.
    // Wallet, log, risk, and bias nodes are not removed.
    void commit_replace() {
        repl_on = false;
        std::string tid = repl_tool_id;
        std::string want = repl_target_id;
        repl_tool_id.clear();
        repl_target_id.clear();
        const ToolItem* tp = nullptr;
        for (auto& tool : snap.tools)
            if (tool.id == tid) { tp = &tool; break; }
        int target = find_node(snap, want);
        if (!tp || target < 0) {
            say("no controller on the canvas — drag one on first");
            return;
        }
        ToolItem t = *tp;
        if (t.type == "bot" && is_v2_sleeper_id(t.id) && snap.wire_nodes[target].type == "ctrl") {
            std::string parent = ctrl_parent_id(snap.wire_nodes[target]);
            if (is_v2_sleeper_id(parent)) {
                int bi = find_node(snap, parent);
                if (bi >= 0 && snap.wire_nodes[bi].type == "bot") target = bi;
            }
        }
        int here = find_node(snap, t.id);
        if (t.unique && here >= 0) {
            wire_sel = here;
            say(t.label + " already on canvas");
            return;
        }
        push_hist();
        WireNode old = snap.wire_nodes[target];
        WireNode n;
        n.id = t.unique ? t.id : uniq_id(snap, t.id);
        n.type = t.type;
        n.label = t.label;
        n.color = t.color;
        n.kind = t.kind;
        n.x = old.x;
        n.y = old.y;
        if (n.id.rfind("ctrl-", 0) == 0) n.bot_id = n.id.substr(5);
        else if (n.type == "bot") n.bot_id = n.id;
        std::string oid = old.id;
        snap.wire_nodes[target] = n;
        retarget_id(oid, n.id);
        if (old.type == "bot" && n.type == "bot" && oid != n.id) {
            std::string coid = "ctrl-" + oid;
            std::string cnid = "ctrl-" + n.id;
            int cs = find_node(snap, coid);
            if (cs >= 0 && coid != cnid && find_node(snap, cnid) < 0) {
                const ToolItem* ct = nullptr;
                for (auto& tool : snap.tools)
                    if (tool.id == cnid) { ct = &tool; break; }
                if (ct) {
                    WireNode cold = snap.wire_nodes[cs];
                    WireNode cn;
                    cn.id = cnid;
                    cn.type = "ctrl";
                    cn.label = ct->label;
                    cn.color = ct->color.empty() ? n.color : ct->color;
                    cn.kind = ct->kind;
                    cn.x = cold.x;
                    cn.y = cold.y;
                    cn.bot_id = n.id;
                    snap.wire_nodes[cs] = cn;
                    retarget_id(coid, cnid);
                    wire_marked.erase(coid);
                    wire_marked.insert(cnid);
                }
            }
        }
        wire_marked.erase(oid);
        wire_marked.insert(n.id);
        wire_sel = find_node(snap, n.id);
        edge_sel = -1;
        std::string extra;
        if (old.type == "bot" && is_v2_sleeper_id(oid) && oid != n.id && api)
            extra = api->sleeper_stop(oid);
        if (n.type == "bot" && is_v2_sleeper_id(n.id) && api) {
            std::string st = api->sleeper_start(n.id);
            extra = extra.empty() ? st : (extra + " · " + st);
        }
        say("replaced " + old.label + " with " + n.label + " · wires kept"
            + (extra.empty() ? "" : (" · " + extra)));
    }

    void snap_loaded_nodes() {
        if (!wire_snap) return;
        for (auto& n : snap.wire_nodes) {
            n.x = snap_grid(n.x);
            n.y = snap_grid(n.y);
        }
    }

    bool is_marked(int i) const {
        if (i < 0 || i >= static_cast<int>(snap.wire_nodes.size())) return false;
        return wire_marked.count(snap.wire_nodes[i].id) != 0;
    }
    void prune_marks() {
        std::set<std::string> have;
        for (auto& n : snap.wire_nodes) have.insert(n.id);
        for (auto it = wire_marked.begin(); it != wire_marked.end(); ) {
            if (!have.count(*it)) it = wire_marked.erase(it);
            else ++it;
        }
    }
    void clear_marks() {
        wire_marked.clear();
        marquee_on = false;
    }
    void mark_apply_box(const Rect& box, bool additive) {
        if (!additive) wire_marked.clear();
        for (int i = 0; i < static_cast<int>(snap.wire_nodes.size()); ++i) {
            auto& n = snap.wire_nodes[i];
            int ox = canvas_r.x + static_cast<int>((n.x - pan_x) * zoom);
            int oy = canvas_r.y + static_cast<int>((n.y - pan_y) * zoom);
            Rect nr = node_view_box(n, false, zoom, ox, oy);
            if (rects_overlap(box, nr)) wire_marked.insert(n.id);
        }
        wire_sel = -1;
        for (int i = 0; i < static_cast<int>(snap.wire_nodes.size()); ++i)
            if (wire_marked.count(snap.wire_nodes[i].id)) wire_sel = i;
    }
    void remove_node(int i) {
        if (i < 0 || i >= static_cast<int>(snap.wire_nodes.size())) return;
        push_hist();
        std::string id = snap.wire_nodes[i].id;
        std::string typ = snap.wire_nodes[i].type;
        std::string stop_msg;
        if (typ == "bot" && is_v2_sleeper_id(id) && api)
            stop_msg = api->sleeper_stop(id);
        if (typ == "bot" && is_v2_sleeper_id(id))
            strip_v2_io(id);
        snap.wire_nodes.erase(std::remove_if(snap.wire_nodes.begin(), snap.wire_nodes.end(),
            [&](const WireNode& wn) { return wn.id == id; }), snap.wire_nodes.end());
        snap.wire_edges.erase(std::remove_if(snap.wire_edges.begin(), snap.wire_edges.end(),
            [&](const WireEdge& e) { return e.source == id || e.target == id; }), snap.wire_edges.end());
        wire_marked.erase(id);
        wire_sel = -1;
        wire_inspect = -1;
        edge_sel = -1;
        say(stop_msg.empty() ? ("removed " + id) : (stop_msg + " · removed"));
    }
    void remove_marked() {
        prune_marks();
        if (wire_marked.empty()) {
            if (wire_sel >= 0) remove_node(wire_sel);
            return;
        }
        push_hist();
        auto keep = wire_marked;
        int nrem = static_cast<int>(keep.size());
        std::vector<std::string> stop_ids;
        for (auto& wn : snap.wire_nodes) {
            if (keep.count(wn.id) && wn.type == "bot" && is_v2_sleeper_id(wn.id)) {
                stop_ids.push_back(wn.id);
                keep.insert("wallet-" + wn.id);
                keep.insert("ctrl-" + wn.id);
                keep.insert("bias-" + wn.id);
            }
        }
        if (api) {
            for (auto& id : stop_ids)
                api->sleeper_stop(id);
        }
        snap.wire_nodes.erase(std::remove_if(snap.wire_nodes.begin(), snap.wire_nodes.end(),
            [&](const WireNode& wn) { return keep.count(wn.id); }), snap.wire_nodes.end());
        snap.wire_edges.erase(std::remove_if(snap.wire_edges.begin(), snap.wire_edges.end(),
            [&](const WireEdge& e) { return keep.count(e.source) || keep.count(e.target); }),
            snap.wire_edges.end());
        wire_marked.clear();
        wire_sel = -1;
        wire_inspect = -1;
        edge_sel = -1;
        say("removed " + std::to_string(nrem) + " node(s)");
    }

    void remove_edge(int i) {
        if (i < 0 || i >= static_cast<int>(snap.wire_edges.size())) return;
        push_hist();
        snap.wire_edges.erase(snap.wire_edges.begin() + i);
        edge_sel = -1;
        say("wire deleted");
    }

    bool connect_nodes(int ia, int ib) {
        if (ia < 0 || ib < 0 || ia == ib) return false;
        if (ia >= static_cast<int>(snap.wire_nodes.size()) || ib >= static_cast<int>(snap.wire_nodes.size()))
            return false;
        auto& a = snap.wire_nodes[ia];
        auto& b = snap.wire_nodes[ib];
        std::string sh, th;
        bool rev = false;
        if (!best_link(a, b, sh, th, rev)) {
            say("no valid port between " + a.label + " and " + b.label);
            return false;
        }
        const WireNode& src = rev ? b : a;
        const WireNode& tgt = rev ? a : b;
        for (auto& e : snap.wire_edges) {
            if (e.source == src.id && e.target == tgt.id && e.source_handle == sh && e.target_handle == th) {
                say("wire already exists");
                return false;
            }
        }
        push_hist();
        WireEdge e;
        e.id = "e-" + src.id + "-" + sh + "-" + tgt.id;
        e.source = src.id;
        e.target = tgt.id;
        e.source_handle = sh;
        e.target_handle = th;
        snap.wire_edges.push_back(e);
        say("wired " + src.label + " " + sh + " -> " + tgt.label + " " + th);
        return true;
    }

    bool connect_ports(int ia, const std::string& ha, bool a_src, int ib, const std::string& hb, bool b_src) {
        if (ia < 0 || ib < 0 || ia == ib) return false;
        if (ia >= static_cast<int>(snap.wire_nodes.size()) || ib >= static_cast<int>(snap.wire_nodes.size()))
            return false;
        std::string sh, th;
        int si = ia, ti = ib;
        if (a_src && !b_src) { sh = ha; th = hb; }
        else if (!a_src && b_src) { si = ib; ti = ia; sh = hb; th = ha; }
        else if (handles_ok(ha, hb)) { sh = ha; th = hb; }
        else if (handles_ok(hb, ha)) { si = ib; ti = ia; sh = hb; th = ha; }
        else {
            say("ports " + ha + " and " + hb + " do not connect");
            return false;
        }
        if (!handles_ok(sh, th)) {
            say("ports " + sh + " -> " + th + " not a valid wire");
            return false;
        }
        auto& src = snap.wire_nodes[si];
        auto& tgt = snap.wire_nodes[ti];
        for (auto& e : snap.wire_edges) {
            if (e.source == src.id && e.target == tgt.id && e.source_handle == sh && e.target_handle == th) {
                say("wire already exists");
                return false;
            }
        }
        push_hist();
        WireEdge e;
        e.id = "e-" + src.id + "-" + sh + "-" + tgt.id;
        e.source = src.id;
        e.target = tgt.id;
        e.source_handle = sh;
        e.target_handle = th;
        snap.wire_edges.push_back(e);
        say("wired " + src.label + "." + sh + " -> " + tgt.label + "." + th);
        return true;
    }

    void refresh(bool catalog) {
        auto keep_wire_n = snap.wire_nodes;
        auto keep_wire_e = snap.wire_edges;
        auto keep_fields = snap.fields;
        auto keep_tools = snap.tools;
        auto keep_board = snap.wire_board;
        auto keep_qa = snap.qa;
        auto keep_qa_msg = snap.qa_message;
        auto keep_backup = snap.backup;
        auto keep_version = snap.version;
        auto keep_werr = snap.wire_error;
        auto keep_compile = snap.compile;
        auto keep_audit = snap.copy_audit;
        auto n = api->home();
        if (!n.error.empty()) {
            snap.error = n.error;
            say(n.error);
            return;
        }
        snap = n;
        snap.wire_nodes = keep_wire_n;
        snap.wire_edges = keep_wire_e;
        snap.fields = keep_fields;
        snap.tools = keep_tools;
        snap.wire_board = keep_board;
        snap.qa = keep_qa;
        snap.qa_message = keep_qa_msg;
        snap.backup = keep_backup;
        snap.version = keep_version;
        snap.wire_error = keep_werr;
        snap.compile = keep_compile;
        snap.copy_audit = keep_audit;
        hide_paused_funds(snap);
        if (page == PgSystem && snap.version.running.empty())
            api->load_version(snap);
        if (catalog || !fields_ok) {
            api->load_fields(snap);
            fields_ok = true;
        }
        if (sel >= static_cast<int>(snap.bots.size())) sel = 0;
    }

    Bot* selected() {
        if (sel < 0 || sel >= static_cast<int>(snap.bots.size())) return nullptr;
        return &snap.bots[sel];
    }

    std::vector<CtrlField> fields_for(const Bot& b) {
        std::vector<CtrlField> fs;
        auto it = snap.fields.find(b.id);
        if (it != snap.fields.end() && !it->second.empty()) fs = it->second;
        auto stdf = standard_fields(b.id);
        if (fs.empty()) fs = stdf;
        else if (!stdf.empty()) {
            std::set<std::string> have;
            for (auto& f : fs) have.insert(f.key);
            std::vector<CtrlField> front, extra;
            for (auto& f : stdf) {
                if (have.count(f.key)) continue;
                if (f.key == "sendEvery2Win") front.push_back(f);
                else extra.push_back(f);
            }
            if (!front.empty())
                fs.insert(fs.begin(), front.begin(), front.end());
            if (!extra.empty()) {
                int ins = (int)fs.size();
                for (int i = 0; i < (int)fs.size(); ++i)
                    if (fs[i].key == "trail_delta") { ins = i + 1; break; }
                fs.insert(fs.begin() + ins, extra.begin(), extra.end());
            }
        }
        if (fs.empty() && b.controller.is_object()) {
            for (auto jt = b.controller.begin(); jt != b.controller.end(); ++jt) {
                if (jt.key() == "updated" || jt.key() == "source") continue;
                CtrlField f;
                f.key = jt.key();
                f.label = jt.key();
                f.kind = jt.value().is_boolean() ? "bool" : "num";
                fs.push_back(f);
            }
        }
        for (auto& f : fs) tune_field_range(f);
        return fs;
    }

    void fit_wire(const Rect& canvas) {
        if (snap.wire_nodes.empty() || canvas.w < 40 || canvas.h < 40) return;
        double minx = 1e12, miny = 1e12, maxx = -1e12, maxy = -1e12;
        for (auto& n : snap.wire_nodes) {
            double nw = 180, nh = 70;
            if (n.type == "bot" || n.type == "homebot") { nw = 420; nh = 280; }
            else if (n.type == "coin") { nw = 96; nh = 40; }
            else if (n.type == "frame") { nw = 384; nh = 240; }
            else if (n.type == "circle") { nw = 192; nh = 192; }
            minx = std::min(minx, n.x);
            miny = std::min(miny, n.y);
            maxx = std::max(maxx, n.x + nw);
            maxy = std::max(maxy, n.y + nh);
        }
        double bw = std::max(80.0, maxx - minx);
        double bh = std::max(80.0, maxy - miny);
        zoom = std::min(canvas.w / bw, canvas.h / bh) * 0.92;
        zoom = std::min(1.4, std::max(0.12, zoom));
        pan_x = minx - (canvas.w / zoom - bw) * 0.5;
        pan_y = miny - (canvas.h / zoom - bh) * 0.5;
        wire_fitted = true;
    }
};

void fill(Display* d, Drawable dr, GC gc, unsigned long c, const Rect& r) {
    if (r.w <= 0 || r.h <= 0) return;
    XSetForeground(d, gc, c);
    XFillRectangle(d, dr, gc, r.x, r.y, r.w, r.h);
}

void stroke(Display* d, Drawable dr, GC gc, unsigned long c, const Rect& r) {
    if (r.w <= 0 || r.h <= 0) return;
    XSetForeground(d, gc, c);
    XDrawRectangle(d, dr, gc, r.x, r.y, r.w - 1, r.h - 1);
}

void text(Display* d, Drawable dr, GC gc, unsigned long c, int x, int y, const std::string& s) {
    auto a = ascii(s);
    if (a.empty()) return;
    XSetForeground(d, gc, c);
    XDrawString(d, dr, gc, x, y, a.c_str(), static_cast<int>(a.size()));
}

// State view on the hunter button. Red = deactivated (0 HL IP). Green = active.
// words=false keeps the color and the click, and draws no DEACTIVATED / ACTIVE label.
void draw_ip_btn(Display* d, Drawable dr, GC gc, const Colors& C, const Rect& gate,
                 bool deactivated, bool running, std::vector<Hit>& hits, int bot_i,
                 bool words = true) {
    const char* gl = deactivated ? "DEACTIVATED" : (running ? "ACTIVE" : "OFF");
    unsigned long col = deactivated ? C.rose : (running ? C.green : C.muted);
    if (deactivated || running) fill(d, dr, gc, col, gate);
    stroke(d, dr, gc, col, gate);
    if (words) {
        int tw = static_cast<int>(strlen(gl)) * CHAR_W;
        int tx = gate.x + std::max(2, (gate.w - tw) / 2);
        text(d, dr, gc, (deactivated || running) ? C.bg : C.muted, tx, gate.y + gate.h - 4, gl);
    }
    hits.push_back({Hit::HunterGate, bot_i, gate, deactivated ? "on" : "off"});
}

void text_font(Display* d, Drawable dr, GC gc, XFontStruct* fnt, Font restore,
               unsigned long c, int x, int y, const std::string& s) {
    auto a = ascii(s);
    if (a.empty() || !fnt) return;
    XSetFont(d, gc, fnt->fid);
    XSetForeground(d, gc, c);
    XDrawString(d, dr, gc, x, y, a.c_str(), static_cast<int>(a.size()));
    if (restore) XSetFont(d, gc, restore);
}

unsigned long pos_pnl_col(const Colors& C, const Position& p) {
    if (p.roe < -0.05 || p.pnl < -0.005) return C.rose;
    if (p.roe > 0.05 || p.pnl > 0.005) return C.green;
    return C.muted;
}

void fmt_knob_lim(const CtrlField& f, double v, char* b, size_t n) {
    if (f.key == "close_px" || f.kind == "price_pct" || f.key == "scale_x") snprintf(b, n, "%.2f", v);
    else if (f.kind == "pct_frac" && f.step < 1) snprintf(b, n, "%.2f", v);
    else if (f.step < 1) snprintf(b, n, "%.1f", v);
    else snprintf(b, n, "%.0f", v);
}

void draw_port_tag(Display* d, Drawable dr, GC gc, unsigned long bg, unsigned long fg,
                   int ax, int ay, const std::string& lab) {
    if (lab.empty()) return;
    int tw = static_cast<int>(lab.size()) * CHAR_W;
    int pad = 3;
    Rect fr{ax, ay, tw + pad * 2, 13};
    fill(d, dr, gc, bg, fr);
    stroke(d, dr, gc, fg, fr);
    text(d, dr, gc, fg, ax + pad, ay + 10, lab);
}

// Mixer-style rotary: drag right/up increases. Same -135°..+135° sweep as the website knobs.
// face=0 normal dark fill; hover/press pass a strong fill color.
Rect draw_knob(Display* d, Drawable dr, GC gc, const Colors& C, int cx, int cy, int r,
               double t, unsigned long col, unsigned long face = 0) {
    t = std::min(1.0, std::max(0.0, t));
    int d0 = 2 * r;
    bool lit = face != 0;
    XSetForeground(d, gc, lit ? col : C.border);
    XSetLineAttributes(d, gc, lit ? 3 : 1, LineSolid, CapRound, JoinRound);
    XDrawArc(d, dr, gc, cx - r, cy - r, d0, d0, 0, 360 * 64);
    XSetLineAttributes(d, gc, 1, LineSolid, CapButt, JoinMiter);
    XSetForeground(d, gc, lit ? face : C.ab_bg);
    XFillArc(d, dr, gc, cx - r + 3, cy - r + 3, std::max(2, d0 - 6), std::max(2, d0 - 6), 0, 360 * 64);
    double deg = (-135.0 + t * 270.0) * 3.141592653589793 / 180.0;
    int x2 = cx + static_cast<int>(std::cos(deg) * (r - 4));
    int y2 = cy + static_cast<int>(std::sin(deg) * (r - 4));
    unsigned long needle = lit ? C.white : col;
    XSetForeground(d, gc, needle);
    XSetLineAttributes(d, gc, lit ? 3 : 1, LineSolid, CapRound, JoinRound);
    XDrawLine(d, dr, gc, cx, cy, x2, y2);
    XFillArc(d, dr, gc, x2 - 3, y2 - 3, 7, 7, 0, 360 * 64);
    XSetLineAttributes(d, gc, 1, LineSolid, CapButt, JoinMiter);
    return {cx - r - 2, cy - r - 2, d0 + 4, d0 + 4};
}

void set_clip(Display* d, GC gc, const Rect& r) {
    XRectangle xr{};
    xr.x = static_cast<short>(r.x);
    xr.y = static_cast<short>(r.y);
    xr.width = static_cast<unsigned short>(std::max(0, r.w));
    xr.height = static_cast<unsigned short>(std::max(0, r.h));
    XSetClipRectangles(d, gc, 0, 0, &xr, 1, Unsorted);
}

void clear_clip(Display* d, GC gc) { XSetClipMask(d, gc, None); }

unsigned long bias_col(const Colors& C, const std::string& b) {
    if (b == "LONG") return C.cyan;
    if (b == "SHORT") return C.rose;
    if (b == "FLAT") return C.gold;
    return C.muted;
}

bool sl_match(double sl, int preset) {
    return std::fabs(sl - preset) < 0.51;
}

unsigned long sl_level_col(const Colors& C, double sl) {
    if (sl <= 0) return C.muted;
    if (sl <= 25) return C.green;
    if (sl <= 45) return C.amber;
    return C.rose;
}

unsigned long sl_preset_col(const Colors& C, int pct) {
    if (pct <= 25) return C.green;
    if (pct <= 45) return C.amber;
    return C.rose;
}

bool bot_live_roe(const Bot& b, const DeskSnap& snap, double& roe, std::string& coin) {
    bool any = false;
    for (auto& p : snap.positions) {
        if (p.bot_id != b.id || p.coin.empty()) continue;
        if (!any || p.roe < roe) {
            roe = p.roe;
            coin = p.coin;
            any = true;
        }
    }
    return any;
}

int hl_cap_for(const std::string& coin) {
    if (coin == "BTC") return 40;
    if (coin == "ETH") return 25;
    if (coin == "LTC") return 10;
    if (coin == "TAO") return 5;
    return 1;
}

std::string lev_tier_of(const Bot& b) {
    std::string s = "MAX";
    if (b.controller.contains("leverage_tier") && b.controller["leverage_tier"].is_string())
        s = b.controller["leverage_tier"].get<std::string>();
    for (char& c : s) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    if (s != "MAX" && s != "MID" && s != "LOW") return "MAX";
    return s;
}

int lev_for_tier(const std::string& coin, const std::string& tier) {
    int cap = hl_cap_for(coin);
    if (tier == "LOW") return std::max(1, cap / 4);
    if (tier == "MID") return std::max(1, cap / 2);
    return std::max(1, cap);
}

unsigned long lev_tier_col(const Colors& C, const std::string& tier) {
    if (tier == "MID") return C.amber;
    if (tier == "LOW") return C.cyan;
    return C.green;
}

std::string lev_effect_text(const DeskSnap& snap, const Bot& b, const std::string& coin) {
    int target = lev_for_tier(coin, lev_tier_of(b));
    int live = 0;
    for (auto& p : snap.positions) {
        if (p.bot_id != b.id || p.coin != coin) continue;
        live = p.leverage;
        break;
    }
    char buf[40];
    if (live > 0 && live != target)
        snprintf(buf, sizeof(buf), "%dx now %dx", target, live);
    else
        snprintf(buf, sizeof(buf), "%dx", target);
    return buf;
}

bool shows_lev_row(const std::string& id) {
    return id != "hummingbot-hl";
}

std::string lev_toast(const std::string& tier, const std::vector<std::string>& coins) {
    static const char* fallback[] = {"BTC", "ETH", "LTC", "TAO"};
    std::string s = tier + " —";
    if (coins.empty()) {
        for (int i = 0; i < 4; ++i) {
            s += " ";
            s += fallback[i];
            s += " ";
            s += std::to_string(lev_for_tier(fallback[i], tier));
            s += "x";
        }
    } else {
        for (auto& coin : coins) {
            s += " ";
            s += coin;
            s += " ";
            s += std::to_string(lev_for_tier(coin, tier));
            s += "x";
        }
    }
    s += " — open coins stay until flat";
    return s;
}

std::string action_tip(const DeskUi& ui, const Hit& h) {
    switch (h.kind) {
        case Hit::Nav:
            if (h.i >= 0 && h.i < PgCount) return std::string("go to ") + PAGE_NAME[h.i];
            return {};
        case Hit::Chart:
            return h.key.empty() ? "open price chart window" : ("open " + h.key + " chart");
        case Hit::Quit:
            return "quit matrix (CLOSE or window X)";
        case Hit::PackSave:
            return "save whole WIRE (desk/home/health) to conf/wire_pack.json — no HL close";
        case Hit::PackRestore:
            return "restore WIRE from conf/wire_pack.json — canvas only, clips untouched";
        case Hit::WireSnap:
            return "snap boxes to the canvas grid when dragging";
        case Hit::AnnotColor:
            return "set caption color";
        case Hit::AnnotSize:
            return "drag corner to resize group/circle";
        case Hit::InspectBack:
            return "back to toolbox";
        case Hit::CompileClose:
            return "close compile report — nothing applied";
        case Hit::CompileWork:
            return "workaround: park close knobs at live, compile the rest — does not close clips";
        case Hit::CompileGo:
            return "apply compiled canvas after you read this report — does not close clips";
        case Hit::Backend:
            return h.i == 1 ? "connect matrix to Spring :5004" : "connect matrix to Flask :5001";
        case Hit::DeskBias:
            return "set desk BIAS " + h.key + " — does not close open clips";
        case Hit::Sl:
            return "set stop-loss −" + h.key + "% ROE (20 green / 40 orange / 60 red) — does not close now";
        case Hit::Lev: {
            std::string tier = h.key.empty() ? "MAX" : h.key;
            if (h.i >= 0 && h.i < (int)ui.snap.bots.size())
                return lev_toast(tier, ui.snap.bots[h.i].coins);
            return lev_toast(tier, {});
        }
        case Hit::Restart: {
            std::string n = (h.i >= 0 && h.i < (int)ui.snap.bots.size()) ? ui.snap.bots[h.i].name : "hunter";
            return "restart " + n + " — positions stay on Hyperliquid";
        }
        case Hit::HunterGate: {
            std::string n = (h.i >= 0 && h.i < (int)ui.snap.bots.size()) ? ui.snap.bots[h.i].name : "hunter";
            if (h.key == "on")
                return "turn " + n + " green ACTIVE - Hyperliquid calls resume. Spring does not touch positions.";
            return "turn " + n + " red DEACTIVATED - 0 Hyperliquid IP. Spring does not touch positions.";
        }
        case Hit::CoinBias: {
            auto bar = h.key.find('|');
            std::string coin = bar == std::string::npos ? h.key : h.key.substr(0, bar);
            std::string mode = bar == std::string::npos ? "FLAT" : h.key.substr(bar + 1);
            std::string n = (h.i >= 0 && h.i < (int)ui.snap.bots.size()) ? ui.snap.bots[h.i].name : "hunter";
            return "set " + n + " " + coin + " BIAS " + mode + " — does not close";
        }
        case Hit::CoinClose: {
            std::string n = (h.i >= 0 && h.i < (int)ui.snap.bots.size()) ? ui.snap.bots[h.i].name : "hunter";
            return "queue flatten " + h.key + " on " + n + " (LONG or SHORT)";
        }
        case Hit::CoinOpen100: {
            std::string n = (h.i >= 0 && h.i < (int)ui.snap.bots.size()) ? ui.snap.bots[h.i].name : "hunter";
            std::string side;
            if (h.i >= 0 && h.i < (int)ui.snap.bots.size()) {
                auto& b = ui.snap.bots[h.i];
                if (b.bias.count(h.key)) side = b.bias.at(h.key);
            }
            if (side == "LONG" || side == "SHORT")
                return "queue 100% leftover " + side + " " + h.key + " on " + n;
            return "queue 100% leftover " + h.key + " on " + n + " — set LONG or SHORT first";
        }
        case Hit::CtrlBool:
            return "toggle " + h.key + " — files only, does not close clips";
        case Hit::CtrlKnob:
            return "turn " + h.key + "  ·  double-click to type  ·  drag · arrows — does not close clips";
        case Hit::PresetDrop:
            return "load a saved knob set for this hunter only (sqlite)";
        case Hit::PresetPick:
            return h.key == "STD" ? "restore standard PMM controller" : ("load preset " + h.key);
        case Hit::PresetSave:
            return preset_is_std(ui.preset_cur)
                ? "SAVE — STD stays locked, type a new name"
                : ("update '" + ui.preset_cur + "' with current knobs — STD stays locked");
        case Hit::CtrlSel:
            return "cycle " + h.key;
        case Hit::CtrlStd:
            return "reset PMM Simple knobs to yml defaults (2% TP, 1.5/0.5 trail, 30s, SL 20, 100% leftover)";
        case Hit::CopyMute:
            return h.key == "unmute"
                ? "unmute T#" + std::to_string(h.i) + " — resume new copies"
                : "mute T#" + std::to_string(h.i) + " — stop copies and flatten that trader's clips";
        case Hit::CopySlots:
            return "T#" + std::to_string(h.i) + " follow max " + h.key + " trade(s)";
        case Hit::CopyAudit:
            return "open Copy leader audit — read only, no HL trades";
        case Hit::AuditClose:
            return "close audit";
        case Hit::AuditRun:
            return "queue audit run — worker only, does not trade";
        case Hit::Hunter:
            if (h.i < 0 || h.i >= (int)ui.snap.bots.size()) return {};
            if (ui.page == PgHome) return "open " + ui.snap.bots[h.i].name + " on Hunters";
            return "select " + ui.snap.bots[h.i].name;
        case Hit::NotWired:
            return "not wired in this build — live positions untouched";
        case Hit::WireBoard: {
            const char* b[] = {"desk", "home", "health"};
            if (h.i >= 0 && h.i < 3) return std::string("show wire board ") + b[h.i];
            return {};
        }
        case Hit::WireAct: {
            const char* acts[] = {
                "undo last wire edit", "redo", "fit canvas",
                "COMPILE real wires vs live — report first, does not close clips",
                "open compile report — Integrate only after you read it",
                "SAVE cosmetics only — boxes, captions, layout. Does not apply wires",
                "export XML of this canvas — still cosmetics, no apply",
                "reload canvas from disk",
            };
            if (h.i >= 0 && h.i < 8) return acts[h.i];
            return {};
        }
        case Hit::ToolCat:
            return "fold / unfold this toolbox group";
        case Hit::ToolItem:
            if (h.i >= 0 && h.i < (int)ui.snap.tools.size()) {
                auto& t = ui.snap.tools[h.i];
                std::string s = "click to read  -  drag onto the canvas to place";
                if (!t.hint.empty() && t.hint.size() < 42) s += "  -  " + t.hint;
                return s;
            }
            return {};
        case Hit::WirePort:
            return (h.src ? "drag a wire from " : "drop a wire on ") + h.key;
        case Hit::Ctx:
            if (h.i == 0) return "close this menu";
            if (h.i == 1) return "remove this node";
            if (h.i == 2) return "remove this wire";
            if (h.i == 3) return "start a wire from this node";
            return "add tool from menu";
        default:
            return {};
    }
}

void draw_hover(Display* d, Drawable dr, GC gc, const Colors& C, const Rect& hover) {
    if (hover.w > 0 && hover.h > 0 && hover.w < 420 && hover.h < 90)
        stroke(d, dr, gc, C.gold, hover);
}

void sl_now_label(double sl, char* buf, size_t n) {
    if (sl <= 0) snprintf(buf, n, "n/a");
    else snprintf(buf, n, "-%.0f%%", sl);
}

// SL chips: 20 green, 40 orange, 60 red. Live ROE of the open clip sits next to SL.
int draw_sl_strip(Display* d, Drawable dr, GC gc, const Colors& C,
                  int x, int y, double sl, std::vector<Hit>* hits, int bot_i,
                  double live_roe, bool has_roe, const Rect* hover) {
    unsigned long nowc = sl_level_col(C, sl);
    text(d, dr, gc, nowc, x, y + 14, "SL");
    char now[24];
    sl_now_label(sl, now, sizeof(now));
    text(d, dr, gc, nowc, x + 22, y + 14, now);
    if (has_roe) {
        char rs[40];
        snprintf(rs, sizeof(rs), "ROE %+.1f%%", live_roe);
        unsigned long rc = live_roe < 0 ? C.rose : C.green;
        if (sl > 0 && live_roe <= -sl + 5.0) rc = C.amber;
        text(d, dr, gc, rc, x + 78, y + 14, rs);
    } else {
        text(d, dr, gc, C.muted, x + 78, y + 14, "ROE flat");
    }
    const int presets[] = {20, 40, 60};
    bool matched = false;
    int cx = x + 200;
    for (int k = 0; k < 3; ++k) {
        Rect br{cx + k * 52, y, 48, 20};
        bool on = sl_match(sl, presets[k]);
        if (on) matched = true;
        unsigned long pc = sl_preset_col(C, presets[k]);
        bool hot = hover && rects_overlap(*hover, br);
        fill(d, dr, gc, on || hot ? pc : C.bg, br);
        stroke(d, dr, gc, hot ? C.white : pc, br);
        char lb[8];
        snprintf(lb, sizeof(lb), "%d%%", presets[k]);
        text(d, dr, gc, on || hot ? C.bg : pc, br.x + 10, br.y + 14, lb);
        if (hits) {
            char key[8];
            snprintf(key, sizeof(key), "%d", presets[k]);
            hits->push_back({Hit::Sl, bot_i, br, key});
        }
    }
    if (!matched && sl > 0) {
        Rect br{cx + 3 * 52, y, 54, 20};
        unsigned long pc = sl_level_col(C, sl);
        fill(d, dr, gc, pc, br);
        stroke(d, dr, gc, C.white, br);
        char lb[12];
        snprintf(lb, sizeof(lb), "%.0f%%", sl);
        text(d, dr, gc, C.bg, br.x + 8, br.y + 14, lb);
    }
    return 24;
}

// Every hunter except HL Copy. MAX = Hyperliquid cap, MID = cap/2, LOW = cap/4.
int draw_lev_strip(Display* d, Drawable dr, GC gc, const Colors& C,
                   int x, int y, const Bot& b, std::vector<Hit>* hits, int bot_i,
                   const Rect* hover) {
    std::string tier = lev_tier_of(b);
    text(d, dr, gc, C.gold, x, y + 14, "LEV");
    const char* tiers[] = {"MAX", "MID", "LOW"};
    int cx = x + 40;
    for (int k = 0; k < 3; ++k) {
        Rect br{cx + k * 56, y, 52, 20};
        bool on = tier == tiers[k];
        unsigned long pc = lev_tier_col(C, tiers[k]);
        bool hot = hover && rects_overlap(*hover, br);
        fill(d, dr, gc, on || hot ? pc : C.bg, br);
        stroke(d, dr, gc, hot ? C.white : pc, br);
        int tx = br.x + (br.w - 3 * CHAR_W) / 2;
        text(d, dr, gc, on || hot ? C.bg : pc, tx, br.y + 14, tiers[k]);
        if (hits) hits->push_back({Hit::Lev, bot_i, br, tiers[k]});
    }
    return 26;
}

void annot_wh(const WireNode& n, int& w, int& h) {
    w = (n.type == "circle") ? 192 : 384;
    h = (n.type == "circle") ? 192 : 240;
    int aw = 0, ah = 0;
    if (sscanf(n.extra.c_str(), "%dx%d", &aw, &ah) == 2 && aw >= 64 && ah >= 48) {
        w = aw;
        h = ah;
    }
}

std::vector<std::string> src_ports(const WireNode& n);
std::vector<std::string> tgt_ports(const WireNode& n);

int port_count(const WireNode& n) {
    if (n.type == "frame" || n.type == "circle") return 0;
    if (n.type == "bot" || n.type == "homebot") {
        int c = 10;
        if (n.id == "hummingbot-hl" || n.kind == "copy") c += 3;
        return c;
    }
    return static_cast<int>(src_ports(n).size() + tgt_ports(n).size());
}
bool is_hunter_node(const WireNode& n) {
    return n.type == "bot" || n.type == "homebot";
}
bool fat_node(const WireNode& n) {
    return port_count(n) > 4;
}

int node_w(const WireNode& n, bool expand) {
    if (n.type == "frame" || n.type == "circle") {
        int w = 0, h = 0;
        annot_wh(n, w, h);
        return expand ? w + 24 : w;
    }
    if (fat_node(n)) {
        int w = (n.id == "hummingbot-hl" || n.kind == "copy") ? 428 : 408;
        return expand ? w + 20 : w;
    }
    if (expand) {
        if (n.type == "ctrl" || n.type == "bias" || n.type == "desk_bias") return 300;
        if (n.type == "wallet" || n.type == "trader" || n.type == "gauge" || n.type == "health") return 260;
        if (n.type == "coin") return 200;
        if (n.type == "pct") return 200;
        return 260;
    }
    if (n.type == "coin") return 96;
    if (n.type == "trader" || n.type == "wallet" || n.type == "ctrl" || n.type == "bias") return 200;
    if (n.type == "risk_wallet") return 228;
    if (n.type == "pct") return 120;
    return 176;
}
int node_h(const WireNode& n, bool expand) {
    if (n.type == "frame" || n.type == "circle") {
        int w = 0, h = 0;
        annot_wh(n, w, h);
        return expand ? h + 28 : h;
    }
    if (fat_node(n)) {
        int h = (n.id == "hummingbot-hl" || n.kind == "copy") ? 280 : 256;
        return expand ? h + 16 : h;
    }
    if (expand) {
        if (n.type == "ctrl" || n.type == "bias" || n.type == "desk_bias") return 168;
        if (n.type == "wallet" || n.type == "trader") return 132;
        if (n.type == "gauge" || n.type == "health" || n.type == "funds") return 120;
        if (n.type == "coin") return 108;
        if (n.type == "pct") return 80;
        return 120;
    }
    if (n.type == "ctrl" || n.type == "bias" || n.type == "desk_bias") return 86;
    if (n.type == "wallet" || n.type == "trader" || n.type == "gauge") return 70;
    if (n.type == "risk_wallet") return 86;
    if (n.type == "coin") return 40;
    if (n.type == "pct") return 48;
    return 60;
}

const Bot* live_bot(const DeskSnap& s, const WireNode& n) {
    std::string id = n.bot_id.empty() ? n.id : n.bot_id;
    const char* pfx[] = {"ctrl-", "wallet-", "bias-", "homebot-"};
    for (auto p : pfx) {
        auto plen = std::strlen(p);
        if (id.compare(0, plen, p) == 0) { id = id.substr(plen); break; }
    }
    for (auto& b : s.bots) if (b.id == id || b.id == n.id) return &b;
    return nullptr;
}

struct CatDef { const char* id; const char* label; };
const CatDef WIRE_CATS[] = {
    {"hummingbots", "Hummingbots"},
    {"hummingbots_v2", "standard hummingbots_v2"},
    {"coins", "Coins"},
    {"wallets", "Wallets"},
    {"settings", "Settings"},
    {"bias", "BIAS"},
    {"gauges", "Gauges"},
    {"strategy", "Strategy"},
    {"condor", "Condor"},
    {"traders", "Traders"},
    {"system", "System health"},
    {"output", "Output"},
    {"draw", "Draw"},
};
const int WIRE_NCAT = 13;

std::vector<std::string> src_ports(const WireNode& n) {
    if (n.type == "frame" || n.type == "circle") return {};
    if (n.type == "bot" || n.type == "homebot") return {"income", "risk", "logs"};
    if (n.type == "wallet") return {"wallet"};
    if (n.type == "ctrl") return {"ctrl"};
    if (n.type == "bias" || n.type == "desk_bias") return {"bias"};
    if (n.type == "coin") return {"coin"};
    if (n.type == "pct") return {n.kind == "moon" ? "moon" : "sl"};
    if (n.type == "trader") return {"trader"};
    if (n.type == "gauge") return {"bias", "live"};
    if (n.type == "xlate") return {n.kind == "closer" ? "eclose" : "bias"};
    if (n.type == "agent") return {n.kind == "grok" ? "grok" : "out"};
    if (n.type == "health") return {"live", "alert"};
    if (n.type == "funds") return {"funds"};
    return {"out"};
}

std::vector<std::string> tgt_ports(const WireNode& n) {
    if (n.type == "frame" || n.type == "circle") return {};
    if (n.type == "bot" || n.type == "homebot")
        return {"wallet", "coin", "sl", "bias", "moon", "ctrl", "eclose", "trader-1", "trader-2", "trader-3"};
    if (n.type == "wallet") return {"income"};
    if (n.type == "risk_wallet") return {"risk"};
    if (n.type == "logs" || n.type == "output" || n.type == "funds") return {"in"};
    if (n.type == "bias" || n.type == "desk_bias") return {"in"};
    if (n.type == "xlate") return {"tg"};
    if (n.type == "gauge") return {"tg", "grok"};
    if (n.type == "agent") return {"in"};
    return {"in"};
}

struct PortVis {
    std::string handle;
    bool source = false;
    Rect r{};
    int cx = 0, cy = 0;
};

std::vector<PortVis> layout_ports(const WireNode& n, const Rect& box) {
    std::vector<PortVis> out;
    int ps = fat_node(n) ? 14 : 12;
    auto put = [&](const std::string& h, bool src, int x, int y) {
        PortVis p;
        p.handle = h;
        p.source = src;
        p.cx = x;
        p.cy = y;
        p.r = {x - ps / 2, y - ps / 2, ps, ps};
        out.push_back(p);
    };
    if (n.type == "bot" || n.type == "homebot") {
        put("wallet", false, box.x, box.y + box.h * 30 / 100);
        put("coin", false, box.x, box.y + box.h * 68 / 100);
        put("income", true, box.x + box.w, box.y + box.h * 22 / 100);
        put("risk", true, box.x + box.w, box.y + box.h * 50 / 100);
        put("logs", true, box.x + box.w, box.y + box.h * 78 / 100);
        const char* bot[] = {"sl", "bias", "moon", "ctrl", "eclose"};
        int nbot = 5;
        for (int i = 0; i < nbot; ++i)
            put(bot[i], false, box.x + box.w * (12 + i * 18) / 100, box.y + box.h);
        if (n.id == "hummingbot-hl" || n.kind == "copy") {
            put("trader-1", false, box.x + box.w * 20 / 100, box.y);
            put("trader-2", false, box.x + box.w * 50 / 100, box.y);
            put("trader-3", false, box.x + box.w * 80 / 100, box.y);
        }
        return out;
    }
    auto srcs = src_ports(n);
    auto tgts = tgt_ports(n);
    for (int i = 0; i < static_cast<int>(tgts.size()); ++i) {
        int y = box.y + box.h * (i + 1) / (static_cast<int>(tgts.size()) + 1);
        put(tgts[i], false, box.x, y);
    }
    for (int i = 0; i < static_cast<int>(srcs.size()); ++i) {
        int y = box.y + box.h * (i + 1) / (static_cast<int>(srcs.size()) + 1);
        put(srcs[i], true, box.x + box.w, y);
    }
    return out;
}

Rect node_view_box(const WireNode& n, bool exp, double zoom, int ox, int oy) {
    double z = exp ? std::max(zoom, 0.82) : zoom;
    int nw = std::max(48, static_cast<int>(node_w(n, exp) * z));
    int nh = std::max(22, static_cast<int>(node_h(n, exp) * z));
    return {ox, oy, nw, nh};
}

bool find_port_xy(const WireNode& n, const Rect& box, const std::string& handle, bool src, int& x, int& y) {
    if (!handle.empty()) {
        for (auto& p : layout_ports(n, box)) {
            if (p.handle == handle && p.source == src) { x = p.cx; y = p.cy; return true; }
        }
        for (auto& p : layout_ports(n, box)) {
            if (p.handle == handle) { x = p.cx; y = p.cy; return true; }
        }
    }
    auto want = src ? src_ports(n) : tgt_ports(n);
    if (!want.empty()) {
        for (auto& p : layout_ports(n, box)) {
            if (p.source == src && p.handle == want[0]) { x = p.cx; y = p.cy; return true; }
        }
        for (auto& p : layout_ports(n, box)) {
            if (p.source == src) { x = p.cx; y = p.cy; return true; }
        }
    }
    x = box.x + box.w / 2;
    y = box.y + box.h / 2;
    return false;
}

void port_stub(const Rect& box, int x, int y, int& dx, int& dy) {
    dx = 0; dy = 0;
    int stub = 12;
    if (x <= box.x + 3) dx = -stub;
    else if (x >= box.x + box.w - 3) dx = stub;
    else if (y <= box.y + 3) dy = -stub;
    else dy = stub;
}

std::string port_tag_text(const std::string& handle) {
    if (handle.rfind("trader-", 0) == 0 && handle.size() > 7) return "T" + handle.substr(7);
    if (handle == "eclose") return "eclose";
    if (handle == "income") return "income";
    if (handle == "wallet") return "wallet";
    if (handle.size() > 6) return handle.substr(0, 6);
    return handle;
}

bool handles_ok(const std::string& src, const std::string& tgt) {
    if (src.empty() || tgt.empty() || src == tgt) {
        if (src == tgt && !src.empty()) return true;
    }
    if (src == "coin" && tgt == "coin") return true;
    if (src == "wallet" && tgt == "wallet") return true;
    if ((src == "income" || src == "funds") && (tgt == "income" || tgt == "in")) return true;
    if (src == "risk" && tgt == "risk") return true;
    if (src == "logs" && (tgt == "in" || tgt == "logs")) return true;
    if (src == "sl" && tgt == "sl") return true;
    if (src == "moon" && tgt == "moon") return true;
    if (src == "trader" && (tgt == "trader" || tgt == "trader-1" || tgt == "trader-2" || tgt == "trader-3")) return true;
    if (src == "eclose" && tgt == "eclose") return true;
    if (src == "bias" && tgt == "bias") return true;
    if (src == "ctrl" && tgt == "ctrl") return true;
    if ((src == "tg" || src == "out") && tgt == "tg") return true;
    if (src == "grok" && tgt == "grok") return true;
    if (tgt == "in" && (src == "logs" || src == "income" || src == "funds" || src == "bias" ||
                        src == "tg" || src == "grok" || src == "out" || src == "live" || src == "alert"))
        return true;
    return src == tgt;
}

bool best_link(const WireNode& a, const WireNode& b, std::string& sh, std::string& th, bool& rev) {
    auto sa = src_ports(a), tb = tgt_ports(b);
    for (auto& s : sa) for (auto& t : tb) if (handles_ok(s, t)) { sh = s; th = t; rev = false; return true; }
    auto sb = src_ports(b), ta = tgt_ports(a);
    for (auto& s : sb) for (auto& t : ta) if (handles_ok(s, t)) { sh = s; th = t; rev = true; return true; }
    return false;
}

int find_node(const DeskSnap& s, const std::string& id) {
    for (int i = 0; i < static_cast<int>(s.wire_nodes.size()); ++i)
        if (s.wire_nodes[i].id == id) return i;
    return -1;
}

std::string uniq_id(const DeskSnap& s, const std::string& base) {
    if (find_node(s, base) < 0) return base;
    for (int i = 1; i < 9999; ++i) {
        char b[96];
        snprintf(b, sizeof(b), "%s-%d", base.c_str(), i);
        if (find_node(s, b) < 0) return b;
    }
    return base + "-x";
}

bool near_seg(int x, int y, int x1, int y1, int x2, int y2, int maxd = 7) {
    double dx = x2 - x1, dy = y2 - y1;
    double len2 = dx * dx + dy * dy;
    if (len2 < 1) return std::abs(x - x1) + std::abs(y - y1) < maxd;
    double t = ((x - x1) * dx + (y - y1) * dy) / len2;
    t = std::max(0.0, std::min(1.0, t));
    double px = x1 + t * dx, py = y1 + t * dy;
    double d = std::hypot(x - px, y - py);
    return d <= maxd;
}

unsigned long handle_col(const Colors& C, const std::string& h) {
    if (h == "bias") return C.gold;
    if (h == "coin") return C.muted;
    if (h == "wallet") return C.gold;
    if (h == "income" || h == "funds") return C.green;
    if (h == "risk" || h == "sl" || h == "eclose" || h == "alert") return C.rose;
    if (h == "logs" || h == "live") return C.cyan;
    if (h == "moon") return C.amber;
    if (h == "ctrl" || h == "tg" || h == "grok" || h == "out") return C.ab_orange;
    if (h == "trader") return C.gold;
    return C.ab_line;
}

std::string port_role(const std::string& handle, bool source) {
    if (handle.rfind("trader-", 0) == 0) {
        std::string n = handle.size() > 7 ? handle.substr(7) : "?";
        return "leader " + n;
    }
    if (handle == "wallet") return source ? "address" : "signs";
    if (handle == "coin") return "market";
    if (handle == "sl") return source ? "stop %" : "ROE stop";
    if (handle == "bias") return source ? "side" : "side gate";
    if (handle == "moon") return source ? "target %" : "close $";
    if (handle == "ctrl") return "knobs";
    if (handle == "eclose") return "flatten sig";
    if (handle == "income") return "cash";
    if (handle == "risk") return "2nd win";
    if (handle == "logs") return "trade log";
    if (handle == "live") return "pulse";
    if (handle == "alert") return "alarm";
    if (handle == "funds") return "balances";
    if (handle == "grok") return "model";
    if (handle == "tg") return "signal";
    if (handle == "out") return "message";
    if (handle == "in") return "inlet";
    return source ? "out" : "in";
}

std::vector<std::string> wrap_px(const std::string& src, int px, int max_lines) {
    int cols = std::max(16, px / CHAR_W);
    if (max_lines < 1) max_lines = 1;
    std::vector<std::string> lines;
    std::string cur;
    bool more = false;
    auto push_line = [&]() {
        if ((int)lines.size() >= max_lines) {
            more = true;
            cur.clear();
            return;
        }
        lines.push_back(cur);
        cur.clear();
    };
    auto take = [&](const std::string& w) {
        if (w.empty() || more) return;
        if (cur.empty()) {
            if ((int)w.size() <= cols) cur = w;
            else {
                cur = w.substr(0, static_cast<size_t>(cols));
                more = true;
            }
            return;
        }
        if ((int)cur.size() + 1 + (int)w.size() <= cols) {
            cur += " ";
            cur += w;
            return;
        }
        push_line();
        if (more || (int)lines.size() >= max_lines) {
            more = true;
            return;
        }
        if ((int)w.size() <= cols) cur = w;
        else {
            cur = w.substr(0, static_cast<size_t>(cols));
            more = true;
        }
    };
    std::string word;
    for (size_t i = 0; i <= src.size(); ++i) {
        char c = (i < src.size()) ? src[i] : ' ';
        if (c == ' ') {
            take(word);
            word.clear();
            if (more) break;
        } else word.push_back(c);
    }
    if (!cur.empty() && (int)lines.size() < max_lines) lines.push_back(cur);
    else if (!cur.empty()) more = true;
    if (more && !lines.empty()) {
        std::string& last = lines.back();
        int keep = std::max(0, cols - 3);
        if ((int)last.size() > keep) last.resize(static_cast<size_t>(keep));
        while (!last.empty() && last.back() == ' ') last.pop_back();
        last += "...";
    }
    return lines;
}

bool known_hunter_id(const DeskSnap& s, const std::string& id) {
    if (id.rfind("hummingbot-", 0) == 0) return true;
    for (auto& b : s.bots) if (b.id == id) return true;
    for (auto& t : s.tools) if (t.type == "bot" && t.id == id) return true;
    return false;
}

std::string tool_hunter_id(const DeskSnap& s, const ToolItem& t) {
    if (t.type == "bot" || t.type == "homebot") return t.id;
    const char* pfx = nullptr;
    if (t.type == "ctrl") pfx = "ctrl-";
    else if (t.type == "wallet") pfx = "wallet-";
    else if (t.type == "bias") pfx = "bias-";
    if (!pfx) return {};
    size_t n = std::strlen(pfx);
    if (t.id.compare(0, n, pfx) != 0) return {};
    std::string rest = t.id.substr(n);
    if (rest == "manual") return {};
    if (!known_hunter_id(s, rest)) return {};
    return rest;
}

WireNode tool_wire_node(const ToolItem& t) {
    WireNode n;
    n.id = t.id;
    n.type = t.type;
    n.label = t.label;
    n.color = t.color;
    n.kind = t.kind;
    if (n.id.rfind("ctrl-", 0) == 0) n.bot_id = n.id.substr(5);
    else if (n.id.rfind("wallet-", 0) == 0) n.bot_id = n.id.substr(7);
    else if (n.id.rfind("bias-", 0) == 0 && n.id != "bias-manual") n.bot_id = n.id.substr(5);
    else if (n.type == "bot" || n.type == "homebot") n.bot_id = n.id;
    if (n.id == "hummingbot-hl" && n.kind.empty()) n.kind = "copy";
    return n;
}

std::vector<std::string> sheet_ports(const WireNode& n, bool source) {
    auto p = source ? src_ports(n) : tgt_ports(n);
    bool copy = n.id == "hummingbot-hl" || n.kind == "copy" || n.bot_id == "hummingbot-hl";
    if (n.type != "bot" && n.type != "homebot") copy = false;
    if (copy) return p;
    std::vector<std::string> keep;
    for (auto& h : p)
        if (h.rfind("trader-", 0) != 0) keep.push_back(h);
    return keep;
}

std::string hunter_what(const std::string& id) {
    if (id == "hummingbot-dman-maker")
        return "Hummingbot calls D-Man Maker V2 a market maker that moves spread and size with indicators. "
               "On this desk it is a BIAS gate: LONG and flat market-buys leftover USD, SHORT and flat market-sells it, then holds. "
               "Exit is take-profit, trail, or the ROE stop, then the same BIAS re-enters. The open clip stays on its side. "
               "dontGoCrazy waits after two +20% ROE wins (30 min or a -30% ROE dip). "
               "afterLossNoRebuy waits after a stop or an exchange-flat close until LONG or SHORT is set again. "
               "Checked, send every 2 win parks that second win on the risk wallet. Unchecked, every win stays on this account.";
    if (id == "hummingbot-bn-swing")
        return "Hummingbot PMM Simple quotes both sides around the mid price and uses no forecast. "
               "On this desk, LONG and flat rests one buy limit for leftover USD. "
               "While in, it exits on take-profit, trail, or the ROE stop. "
               "SHORT waits until a 100% open. The open clip stays on its side.";
    if (id == "hummingbot-dman")
        return "Hummingbot D-Man V3 is Bollinger mean reversion. "
               "On this desk, 15 minute BBP below 0 opens LONG and BBP above 1 opens SHORT, one clip of leftover USD after the RISK park. "
               "Trail arms at 1.5% of price and the trail distance is 0.5% of price, together with the Home ROE stop. "
               "The open clip is a single entry.";
    if (id == "hummingbot-surfn")
        return "Official SuperTrend V1 on this desk. LONG when the trend is up and price is within 1% of the line. "
               "SHORT when the trend is down and price is that close. Length 20, multiplier 4, on the 3 minute chart. "
               "BIAS still has to agree. The open clip stays on its side when the trend reverses. "
               "Exit is the Home stop, Close, a 100% open, or a scale-out.";
    if (id == "hummingbot-helliot")
        return "Elliott 3-wave on 15 minute swings. LONG in the 38.2% to 78.6% retrace after an up impulse. "
               "SHORT in that retrace after a down impulse. Target is the 1.618 extension. "
               "A dashboard 100%, 50%, or 20% open skips the wave wait. "
               "Exits are +93% ROE take-profit, -20% ROE stop, and the structure target.";
    if (id == "hummingbot-shortn")
        return "Auto entry stays short when 15 minute Bollinger is high and the 1 hour average is not rising. "
               "At +90% ROE it closes and waits for a bounce of at least +20% ROE before shorting again. "
               "Open shorts have a -20% ROE stop. A 100% leftover open follows the current BIAS. "
               "The open clip stays on its side.";
    if (id == "hummingbot-onlyup")
        return "Supertrend rider for BTC, LTC, and TAO. Auto entry is long only: the 1 hour average is rising, "
               "the 15 minute Supertrend is long, and price is within 1.2% of the line. "
               "Near +90% ROE it sells half and trails the rest under Supertrend or EMA-21. "
               "A Supertrend flip down closes the runner. A 100% leftover open follows the current BIAS. "
               "The open clip stays on its side. Home stop applies on new risk before the first scale-out.";
    if (id == "hummingbot-hl")
        return "A wallet mirror, outside the Hummingbot controller scripts. About every 5 seconds it reads up to three leader wallets. "
               "A new leader position opens the same side here. A leader close closes the mirror. "
               "A side flip closes the old clip and opens the new one. Two leaders on one coin: the first one wins. "
               "The open log is written when the order is sent. Hyperliquid notional floor is $10.";
    return {};
}

// Official Hummingbot V2 controllers (hummingbot.org + the controllers/ tree).
// Menu lines wrap to two rows of about 23 characters. The body is the detail box.
struct V2Blurb {
    const char* id;
    const char* menu;
    const char* body;
};

const V2Blurb* v2_blurb(const std::string& id) {
    static const V2Blurb rows[] = {
        {"hummingbot-v2-bollinger-v1",
         "BBP long below the band, short above it",
         "Official directional controller. Bollinger percent says where price sits in the band. "
         "Below the long threshold it opens long. Above the short threshold it opens short. "
         "Exit is the stop, take-profit, or time limit."},
        {"hummingbot-v2-bollinger-v2",
         "Newer BBP. Same long and short thresholds",
         "Same band-percent rule as Bollinger V1, in the newer controller file. "
         "Long below the long threshold. Short above the short threshold. "
         "Length, deviation, and both thresholds are the knobs."},
        {"hummingbot-v2-bollingrid",
         "Bollinger side, then a grid inside the band",
         "Bollinger percent picks the side, then a grid of limits is placed inside the band. "
         "Start, end, and the limit past the range are fractions of the band width."},
        {"hummingbot-v2-macd-bb",
         "MACD and the band must both agree",
         "Long only when the band percent is under its long threshold, the MACD line is below zero, "
         "and the histogram is rising. Short is the mirror of that. "
         "Exit is the stop, take-profit, or time limit."},
        {"hummingbot-v2-dman-v3",
         "Official DMan. Band side, many maker levels",
         "Official mean-reversion controller, not the Dman hunter on this desk. "
         "Bollinger percent picks the side, then several maker levels step away from the price. "
         "The band width can stretch those steps, and each level has its own stop and trail."},
        {"hummingbot-v2-supertrend",
         "Trend up and near the line, else short",
         "Official trend controller, not the Surfn hunter. "
         "Long when the trend is up and price is within the percent threshold of the line. "
         "Short when the trend is down and price is that close. Length and multiplier are knobs."},
        {"hummingbot-v2-ai-livestream",
         "Outside long or short chance, over MQTT",
         "No candle math. It listens on MQTT for an outside short, neutral, and long probability. "
         "Short above its threshold opens short. Otherwise long above its threshold opens long. "
         "Otherwise it waits."},
        {"hummingbot-v2-pmm-dynamic",
         "Both sides. Spread follows NATR volatility",
         "Two-sided market maker, not the PMM Simple hunter. "
         "NATR volatility widens and tightens the spreads. MACD can shift the mid. "
         "It quotes both sides around that mid and does not take a long or short signal."},
        {"hummingbot-v2-grid-strike",
         "One long or short grid across a price band",
         "One long grid or one short grid of limits between a start price and an end price, "
         "with a limit price past the range. It places the grid while price is inside that range. "
         "A filled level closes on take-profit, stop, or time."},
        {"hummingbot-v2-multi-grid",
         "Several price bands, each its own grid",
         "Several Grid Strike ranges in one controller. "
         "Each range has its own start, end, side, and share of the quote. "
         "A filled level still closes on take-profit, stop, or time."},
        {"hummingbot-v2-stat-arb",
         "High z-score: long one pair, short the other",
         "Two markets that move together. The spread is the hedge versus a line fit through the dominant. "
         "A high z-score buys the dominant and shorts the hedge. A low z-score swaps those sides. "
         "Lookback and the entry threshold are knobs."},
        {"hummingbot-v2-hedge",
         "Perp short sized from a spot balance",
         "Reads a spot balance and holds a perpetual short of that size times a hedge ratio from 0 to 1. "
         "It changes the hedge only when the gap is large enough and enough time has passed. "
         "It does not guess direction."},
        {"hummingbot-v2-pmm-v1",
         "Older maker. Fixed spreads, inventory skew",
         "Older pure market maker, not the PMM Simple hunter. "
         "Buy and sell limits sit at fixed spreads on several levels, and inventory can skew them. "
         "Refresh time and a price band are knobs."},
        {"hummingbot-v2-pmm-mister",
         "Holds a base share and protects the profit",
         "Market maker that holds inventory between a minimum and a maximum base share. "
         "It skips a new order when one is already close in price. "
         "An optional take-profit and stop can protect the whole position."},
        {"hummingbot-v2-qgrid",
         "Splits quote across grids and can reverse",
         "Splits the quote across the assets in its allocation. Each grid range follows the Bollinger width. "
         "It can open a grid on the weak side and keep that position for a reversal."},
        {"hummingbot-v2-arb",
         "Buy the cheap book, sell the rich one",
         "Cross-exchange. One side buys the first book and sells the second when the gap covers the minimum profit. "
         "The other side runs the opposite way. It pauses if the two sides get too far out of balance. No BIAS node."},
        {"hummingbot-v2-xemm",
         "Maker quotes, hedged on a second exchange",
         "Cross-exchange market maker. Several bid and ask levels on the maker exchange aim at a profit versus the taker book. "
         "A maker fill is hedged on the taker. No BIAS node."},
        {"hummingbot-v2-lp",
         "CLMM liquidity. Needs Gateway, no key yet",
         "Concentrated-liquidity maker for one CLMM pool. It keeps a single position and moves it when price leaves the range. "
         "It needs Gateway. No Hyperliquid key until a wallet is wired. No BIAS node."},
    };
    for (auto& r : rows)
        if (id == r.id) return &r;
    return nullptr;
}

struct ToolCopy {
    std::string role;
    std::string body;
};

ToolCopy tool_copy(const DeskSnap& s, const ToolItem& t) {
    ToolCopy c;
    std::string hid = tool_hunter_id(s, t);
    bool v2 = hid.rfind("hummingbot-v2-", 0) == 0;
    auto v2_line = [&](const std::string& hint) {
        const V2Blurb* b = v2_blurb(hid);
        std::string h = (b && b->body && b->body[0]) ? b->body
                        : (hint.empty() ? "Official Hummingbot V2 controller." : hint);
        return h + " The controller stays asleep until the hunter is on the wire. Placing the hunter starts it. Erasing it sleeps it again.";
    };
    if (t.type == "bot" || t.type == "homebot") {
        if (v2) c.body = v2_line(t.hint);
        else c.body = hunter_what(t.id);
        if (c.body.empty())
            c.body = t.hint.empty()
                ? "Hummingbot hunter. Wire a wallet, a coin, and a controller. The script reads those each tick."
                : t.hint;
        return c;
    }
    if (t.type == "ctrl" && !hid.empty()) {
        c.role = "Wire ctrl into the hunter. Knobs are read each tick. They do not send orders by themselves.";
        c.body = v2 ? v2_line(t.hint) : hunter_what(hid);
        if (c.body.empty() && !t.hint.empty()) c.body = t.hint;
        return c;
    }
    if (t.type == "wallet" && !hid.empty()) {
        c.body = "Wallet node. Output is this hunter's subaccount. Wire it into the hunter wallet input. This node does not move funds.";
        return c;
    }
    if (t.type == "bias" && !hid.empty()) {
        c.body = "BIAS node. LONG, SHORT, or FLAT per coin. The hunter treats it as a gate. FLAT stops new entries and leaves an open clip in place.";
        return c;
    }
    if (t.id == "bias-manual")
        c.body = "Manual BIAS. LONG, SHORT, or FLAT. Wire bias into a hunter. FLAT stops new entries and leaves an open clip in place.";
    else if (t.id == "desk-bias")
        c.body = "Desk BIAS. One side for the desk. It leaves an open clip in place.";
    else if (t.type == "coin")
        c.body = "Coin. Wire coin into the hunter. That is the market the script trades.";
    else if (t.id == "sl-pct")
        c.body = "Stop loss percent. Wire sl into the hunter. The script uses it as the ROE stop.";
    else if (t.id == "moon-pct")
        c.body = "Moon percent. Wire moon into the hunter. That is the close target.";
    else if (t.id == "risk-wallet")
        c.body = "RISK wallet. A hunter parks the second win here when that skim is on. Wire risk from the hunter into this inlet.";
    else if (t.id == "funds")
        c.body = "FUNDS bus. Balances from the Hyperliquid subaccounts. This node does not move funds.";
    else if (t.type == "trader")
        c.body = "Leader wallet. Wire trader into Copy trader-1, trader-2, or trader-3. The copy hunter mirrors this address.";
    else if (t.id == "logs")
        c.body = "LOGS bus. A hunter's trade log lands here. Wire logs from the hunter into this inlet.";
    else if (t.type == "gauge")
        c.body = t.hint.empty() ? "Gauge. It publishes a live pulse and a bias reading." : t.hint;
    else if (t.type == "xlate")
        c.body = t.kind == "closer"
            ? "Close translator. It turns a CLOSE signal into the flatten inlet on a hunter."
            : "Translator. LONG, SHORT, FLAT, and CLOSE become a bias the hunter can read.";
    else if (t.type == "agent")
        c.body = t.hint.empty() ? "Output agent. Wire a signal into it." : t.hint;
    else if (t.type == "frame" || t.type == "circle")
        c.body = t.hint.empty() ? "Draw note. Caption and color only. It has no wires." : t.hint;
    else if (!t.hint.empty())
        c.body = t.hint;
    else
        c.body = t.label;
    return c;
}

std::string tool_knobs(DeskUi& ui, const std::string& hid) {
    if (hid.empty()) return {};
    std::vector<CtrlField> fs;
    for (auto& b : ui.snap.bots)
        if (b.id == hid) { fs = ui.fields_for(b); break; }
    if (fs.empty()) {
        auto it = ui.snap.fields.find(hid);
        if (it != ui.snap.fields.end()) fs = it->second;
    }
    if (fs.empty()) fs = standard_fields(hid);
    std::string s;
    for (auto& f : fs) {
        if (f.label.empty()) continue;
        if (!s.empty()) s += "   ";
        s += f.label;
    }
    if (s.empty()) return {};
    return "knobs  " + s;
}

int paint_port_row(Display* d, Drawable dr, GC gc, const Colors& C,
                   int x0, int y, int x1, int max_rows,
                   const char* tag, const std::vector<std::string>& ports, bool source) {
    if (ports.empty() || max_rows < 1) return y;
    text(d, dr, gc, C.gold, x0, y + 12, tag);
    int x = x0 + 40;
    int row_y = y;
    int row = 0;
    for (auto& h : ports) {
        std::string chip = h + "  " + port_role(h, source);
        int w = static_cast<int>(chip.size()) * CHAR_W + 12;
        if (x > x0 + 40 && x + w > x1) {
            if (row + 1 >= max_rows) break;
            row++;
            row_y += 18;
            x = x0 + 40;
        }
        Rect r{x, row_y, w, 16};
        std::string key = h.rfind("trader-", 0) == 0 ? "trader" : h;
        stroke(d, dr, gc, handle_col(C, key), r);
        text(d, dr, gc, C.ab_text, r.x + 6, r.y + 12, chip);
        x += w + 6;
    }
    return row_y + 20;
}

void draw_tool_sheet(Display* d, Drawable dr, GC gc, const Colors& C,
                     const Rect& insp, const ToolItem& t, DeskUi& ui) {
    set_clip(d, gc, {insp.x, insp.y + 22, insp.w, std::max(0, insp.h - 22)});
    int x0 = insp.x + 12;
    int x1 = insp.x + insp.w - 10;
    int limit = insp.y + insp.h - 4;
    int y = insp.y + 34;
    text(d, dr, gc, C.ab_orange, x0, y + 12, ascii(t.type, 14));
    text(d, dr, gc, C.ab_text, x0 + 120, y + 12, fit_px(t.label, std::max(40, insp.w - 150)));
    y += 18;
    ToolCopy copy = tool_copy(ui.snap, t);
    if (!copy.role.empty() && y + 14 < limit) {
        text(d, dr, gc, C.ab_muted, x0, y + 12, fit_px(copy.role, std::max(40, insp.w - 24)));
        y += 16;
    }
    bool want_knobs = (t.type == "bot" || t.type == "ctrl" || t.type == "homebot");
    int reserve = 40 + (want_knobs ? 16 : 0);
    int room = limit - y - reserve;
    int max_lines = (insp.h >= 180) ? 4 : 2;
    if (room < max_lines * 15) max_lines = std::max(1, room / 15);
    for (auto& line : wrap_px(copy.body, std::max(40, insp.w - 24), max_lines)) {
        if (y + 14 > limit) break;
        text(d, dr, gc, C.ab_text, x0, y + 12, line);
        y += 15;
    }
    y += 2;
    WireNode n = tool_wire_node(t);
    auto ins = sheet_ports(n, false);
    auto outs = sheet_ports(n, true);
    if (!ins.empty() && y + 16 < limit) {
        int rows = (limit - y > 40) ? 2 : 1;
        y = paint_port_row(d, dr, gc, C, x0, y, x1, rows, "IN", ins, false);
    }
    if (!outs.empty() && y + 16 < limit) {
        int rows = (limit - y > 40) ? 2 : 1;
        y = paint_port_row(d, dr, gc, C, x0, y, x1, rows, "OUT", outs, true);
    }
    if ((t.type == "bot" || t.type == "ctrl" || t.type == "homebot") && y + 14 < limit) {
        std::string knobs = tool_knobs(ui, tool_hunter_id(ui.snap, t));
        if (!knobs.empty())
            text(d, dr, gc, C.ab_muted, x0, y + 12, fit_px(knobs, std::max(40, insp.w - 24)));
    }
    clear_clip(d, gc);
}

} // namespace

void dump_headless(FlaskApi& api) {
    auto snap = api.home();
    if (!snap.error.empty()) {
        fprintf(stderr, "error: %s\n", snap.error.c_str());
        return;
    }
    api.load_fields(snap);
    api.load_wire(snap);
    printf("rimBaud desk  backend=%s  %s  ts=%s  equity=%s  pos=%d  bots=%d/%d\n",
           api.backend.c_str(), api.read_only ? "READ-ONLY-DEBUG" : "live-flask",
           snap.ts.c_str(), money(snap.totals.equity).c_str(),
           snap.totals.n_pos, snap.totals.n_ok, snap.totals.n_bots);
    printf("  desk BIAS %s  condor %s/%s  wire nodes=%zu edges=%zu  tools=%zu\n",
           snap.desk_bias.bias.empty() ? "-" : snap.desk_bias.bias.c_str(),
           snap.condor.regime.c_str(), snap.condor.source.c_str(),
           snap.wire_nodes.size(), snap.wire_edges.size(), snap.tools.size());
    printf("  no POSTs — live hunters untouched\n");
    for (auto& b : snap.bots) {
        printf("  %s  %s  eq=%s  pos=%d  sl=%.0f  %s\n",
               b.ok ? "ok" : "!!", b.name.c_str(), money(b.equity).c_str(),
               b.n_pos, b.sl_roe,
               b.deactivated ? "DEACTIVATED 0 HL IP" : (b.running ? "run" : "off"));
    }
}

int run_desk(FlaskApi& api) {
    signal(SIGCHLD, SIG_IGN);
    std::map<std::string, pid_t> chart_pids;
    Display* dpy = XOpenDisplay(nullptr);
    if (!dpy) {
        fprintf(stderr, "no DISPLAY — run with a screen, or: ./rimbaud-desk --headless\n");
        dump_headless(api);
        return 1;
    }
    int scr = DefaultScreen(dpy);
    Colormap cm = DefaultColormap(dpy, scr);
    Colors C;
    C.bg = rgb(dpy, cm, 2, 8, 4);
    C.panel = rgb(dpy, cm, 4, 18, 8);
    C.border = rgb(dpy, cm, 13, 59, 31);
    C.green = rgb(dpy, cm, 0, 255, 106);
    C.green_dim = rgb(dpy, cm, 0, 179, 71);
    C.text = rgb(dpy, cm, 157, 255, 192);
    C.muted = rgb(dpy, cm, 61, 122, 85);
    C.rose = rgb(dpy, cm, 255, 77, 109);
    C.cyan = rgb(dpy, cm, 126, 203, 255);
    C.gold = rgb(dpy, cm, 240, 185, 11);
    C.white = rgb(dpy, cm, 255, 255, 255);
    C.amber = rgb(dpy, cm, 255, 200, 87);
    C.navy = rgb(dpy, cm, 3, 22, 10);
    C.ab_bg = rgb(dpy, cm, 18, 18, 18);
    C.ab_panel = rgb(dpy, cm, 36, 36, 36);
    C.ab_grid = rgb(dpy, cm, 48, 48, 48);
    C.ab_orange = rgb(dpy, cm, 255, 118, 77);
    C.ab_clip = rgb(dpy, cm, 44, 44, 44);
    C.ab_text = rgb(dpy, cm, 220, 220, 220);
    C.ab_muted = rgb(dpy, cm, 140, 140, 140);
    C.ab_line = rgb(dpy, cm, 90, 70, 60);
    C.ab_head = rgb(dpy, cm, 28, 28, 28);

    int W = 1440, H = 900;
    Window win = XCreateSimpleWindow(dpy, RootWindow(dpy, scr), 80, 40, W, H, 0, C.border, C.bg);
    XStoreName(dpy, win, "rimBaud // MATRIX  (Spring daemon)");
    Atom wm_delete = XInternAtom(dpy, "WM_DELETE_WINDOW", False);
    XSetWMProtocols(dpy, win, &wm_delete, 1);
    XSetErrorHandler([](Display*, XErrorEvent*) -> int { return 0; });
    XSetIOErrorHandler([](Display*) -> int { _exit(0); });
    XSelectInput(dpy, win, ExposureMask | StructureNotifyMask | ButtonPressMask | ButtonReleaseMask |
                               PointerMotionMask | KeyPressMask | ButtonMotionMask | LeaveWindowMask);
    Atom atom_clip = XInternAtom(dpy, "CLIPBOARD", False);
    Atom atom_utf8 = XInternAtom(dpy, "UTF8_STRING", False);
    Atom atom_paste = XInternAtom(dpy, "RIMBAUD_API_PASTE", False);
    int api_fd = -1;
    std::string api_out;
    bool api_paste_wait = false;
    double api_blink = 0;
    XMapWindow(dpy, win);
    Cursor cur_arrow = XCreateFontCursor(dpy, XC_left_ptr);
    Cursor cur_hand = XCreateFontCursor(dpy, XC_hand2);
    XDefineCursor(dpy, win, cur_arrow);
    GC gc = XCreateGC(dpy, win, 0, nullptr);
    XFontStruct* fs = XLoadQueryFont(dpy, "9x15");
    if (!fs) fs = XLoadQueryFont(dpy, "fixed");
    if (!fs) fs = XLoadQueryFont(dpy, "6x13");
    XFontStruct* fs_tiny = XLoadQueryFont(dpy, "6x13");
    if (!fs_tiny) fs_tiny = XLoadQueryFont(dpy, "6x10");
    if (!fs_tiny) fs_tiny = XLoadQueryFont(dpy, "5x7");
    Font font = fs ? fs->fid : None;
    if (font) XSetFont(dpy, gc, font);
    const int TINY_W = fs_tiny ? fs_tiny->max_bounds.width : 5;
    Pixmap buf = XCreatePixmap(dpy, win, W, H, DefaultDepth(dpy, scr));

    DeskUi ui;
    ui.api = &api;
    ui.refresh(true);

    auto goto_page = [&](Page p) {
        ui.page = p;
        ui.scroll = 0;
        if (p == PgWire) {
            const char* boards[] = {"desk", "home", "health"};
            ui.api->load_wire(ui.snap, boards[ui.wire_board]);
            ui.snap_loaded_nodes();
            if (ui.snap.tools.empty()) ui.api->load_fields(ui.snap);
            ui.wire_fitted = false;
            ui.last_wire = ui.now();
        }
        if (p == PgStrategy && !ui.qa_ok) {
            ui.api->load_qa(ui.snap);
            ui.qa_ok = true;
        }
        if (p == PgSystem && !ui.backup_ok) {
            ui.api->load_backup(ui.snap);
            ui.api->load_version(ui.snap);
            ui.backup_ok = true;
        }
    };

    auto redraw = [&]() {
        ui.hits.clear();
        fill(dpy, buf, gc, C.bg, {0, 0, W, H});

        const int NAV_H = 76;
        fill(dpy, buf, gc, C.navy, {0, 0, W, NAV_H});
        XSetForeground(dpy, gc, C.border);
        XDrawLine(dpy, buf, gc, 0, NAV_H, W, NAV_H);
        text(dpy, buf, gc, rgb(dpy, cm, 42, 159, 214), 14, 18, "rimBaud  //  MATRIX");
        {
            const auto& hk = ui.snap.hl_key;
            bool ok = hk.state == "green";
            bool bad = hk.state == "red";
            unsigned long col = ok ? C.green : (bad ? C.rose : C.muted);
            const char* word = ok ? "HL KEY  ok" : (bad ? "HL KEY  no" : "HL KEY  …");
            int bw = static_cast<int>(strlen(word)) * 9 + 16;
            Rect box{W - 16 - bw, 4, bw, 20};
            fill(dpy, buf, gc, ok ? rgb(dpy, cm, 8, 48, 22) : (bad ? rgb(dpy, cm, 48, 12, 16) : C.bg), box);
            stroke(dpy, buf, gc, col, box);
            text(dpy, buf, gc, col, box.x + 8, box.y + 14, word);
        }
        char sub[200];
        snprintf(sub, sizeof(sub), "DAEMON %s:%d   live %d/%d   pos %d   eq %s   uPnL %s",
                 api.host.c_str(), api.port,
                 ui.snap.totals.n_ok, ui.snap.totals.n_bots, ui.snap.totals.n_pos,
                 money(ui.snap.totals.equity).c_str(), money(ui.snap.totals.upnl).c_str());
        text(dpy, buf, gc, C.gold, 280, 18, sub);
        if (ui.now() < ui.toast_until && !ui.toast.empty())
            text(dpy, buf, gc, C.cyan, 14, 70, ui.toast);
        else
            text(dpy, buf, gc, C.amber, 14, 70,
                 api.writes
                     ? "SPRING DAEMON  ·  WRITE MODE  ·  files only  ·  never CLOSE/OPEN live clips"
                     : "SPRING DAEMON  ·  GET only  ·  enable WRITE MODE in springctl");
        if (!ui.snap.error.empty())
            text(dpy, buf, gc, C.rose, 720, 70, ui.snap.error);

        int nx = 14;
        for (int i = 0; i < PgCount; ++i) {
            int tw = static_cast<int>(strlen(PAGE_NAME[i])) * 9 + 18;
            Rect r{nx, 28, tw, 22};
            bool on = ui.page == i;
            fill(dpy, buf, gc, on ? rgb(dpy, cm, 18, 40, 12) : C.bg, r);
            stroke(dpy, buf, gc, on ? C.gold : C.border, r);
            text(dpy, buf, gc, on ? C.gold : C.green_dim, r.x + 8, r.y + 15, PAGE_NAME[i]);
            ui.hits.push_back({Hit::Nav, i, r});
            nx += tw + 8;
        }

        {
            char clab[40];
            snprintf(clab, sizeof(clab), "SPRING DAEMON :%d", api.cpp_port);
            int cw = static_cast<int>(strlen(clab)) * 9 + 18;
            Rect rc{W - 14 - cw, 28, cw, 22};
            Rect rch{rc.x - 8 - 70, 28, 64, 22};
            Rect rquit{rch.x - 8 - 58, 28, 54, 22};
            fill(dpy, buf, gc, rgb(dpy, cm, 48, 16, 16), rquit);
            stroke(dpy, buf, gc, C.rose, rquit);
            text(dpy, buf, gc, C.rose, rquit.x + 8, rquit.y + 15, "CLOSE");
            fill(dpy, buf, gc, C.bg, rch);
            stroke(dpy, buf, gc, C.cyan, rch);
            text(dpy, buf, gc, C.cyan, rch.x + 10, rch.y + 15, "CHART");
            fill(dpy, buf, gc, rgb(dpy, cm, 50, 28, 8), rc);
            stroke(dpy, buf, gc, C.ab_orange, rc);
            text(dpy, buf, gc, C.ab_orange, rc.x + 8, rc.y + 15, clab);
            ui.hits.push_back({Hit::Quit, 0, rquit});
            ui.hits.push_back({Hit::Chart, -1, rch});
        }

        Rect body{0, NAV_H + 4, W, H - NAV_H - 4};

        auto draw_home = [&]() {
            int y = body.y + 8 - ui.scroll;
            int x = 14;
            int inner = W - 28;

            // Condor / Grok chips
            text(dpy, buf, gc, C.gold, x, y + 14, "CONDOR / GROK");
            int cx = x + 130;
            for (auto& kv : ui.snap.condor.bias) {
                Rect chip{cx, y, 86, 22};
                unsigned long fc = bias_col(C, kv.second);
                stroke(dpy, buf, gc, fc, chip);
                text(dpy, buf, gc, C.muted, chip.x + 6, chip.y + 15, kv.first);
                text(dpy, buf, gc, fc, chip.x + 40, chip.y + 15, kv.second);
                cx += 94;
            }
            char src[96];
            snprintf(src, sizeof(src), "%s  %s  tg %s  grok %s",
                     ascii(ui.snap.condor.source, 12).c_str(),
                     ascii(ui.snap.condor.regime, 12).c_str(),
                     ui.snap.condor.telegram_ok ? "ok" : "off",
                     ui.snap.condor.grok_ok ? "ok" : "off");
            text(dpy, buf, gc, C.muted, cx + 8, y + 15, src);
            y += 32;

            // Desk BIAS (display only)
            text(dpy, buf, gc, C.gold, x, y + 14, "DESK BIAS");
            const char* modes[] = {"LONG", "FLAT", "SHORT"};
            int bx = x + 100;
            for (int m = 0; m < 3; ++m) {
                Rect br{bx + m * 72, y, 66, 22};
                bool on = ui.snap.desk_bias.bias == modes[m];
                unsigned long fc = bias_col(C, modes[m]);
                fill(dpy, buf, gc, on ? fc : C.bg, br);
                stroke(dpy, buf, gc, fc, br);
                text(dpy, buf, gc, on ? C.bg : fc, br.x + 12, br.y + 15, modes[m]);
                ui.hits.push_back({Hit::DeskBias, 0, br, modes[m]});
            }
            std::string held;
            for (auto& h : ui.snap.desk_bias.hunters) {
                if (h.held.empty()) continue;
                if (!held.empty()) held += "  ";
                held += h.name + " ";
                for (size_t i = 0; i < h.held.size(); ++i) {
                    if (i) held += "/";
                    held += h.held[i];
                }
            }
            text(dpy, buf, gc, C.muted, x + 330, y + 15,
                 held.empty() ? "does not close open clips" : ascii("still in clip: " + held, 90));
            y += 34;

            // IP tokens
            text(dpy, buf, gc, C.gold, x, y + 14, "IP TOKENS");
            char rt[80];
            snprintf(rt, sizeof(rt), "%.0f / %.0f   %.0f left   %.0f%%",
                     ui.snap.rate.used, ui.snap.rate.cap, ui.snap.rate.left, ui.snap.rate.pct);
            text(dpy, buf, gc, ui.snap.rate.pct >= 90 ? C.rose : (ui.snap.rate.pct >= 70 ? C.amber : C.green),
                 x + 100, y + 14, rt);
            Rect bar{x + 360, y + 4, std::min(420, inner - 360), 12};
            fill(dpy, buf, gc, rgb(dpy, cm, 10, 40, 20), bar);
            int fillw = static_cast<int>(std::min(1.0, std::max(0.0, ui.snap.rate.pct / 100.0)) * bar.w);
            fill(dpy, buf, gc, ui.snap.rate.pct >= 90 ? C.rose : (ui.snap.rate.pct >= 70 ? C.amber : C.green),
                 {bar.x, bar.y, std::max(1, fillw), bar.h});
            y += 28;
            int n_deact = 0;
            for (auto& b : ui.snap.bots) if (b.deactivated) n_deact++;
            if (n_deact > 0) {
                char ipn[160];
                snprintf(ipn, sizeof(ipn),
                         "%d deactivated - those hunters spend 0 Hyperliquid IP tokens. Positions stay.",
                         n_deact);
                text(dpy, buf, gc, C.rose, x, y + 12, ipn);
                y += 18;
            }

            // KPIs
            struct K { const char* l; std::string v; unsigned long c; };
            char b1[32], b2[32], b3[32], b4[32];
            snprintf(b1, sizeof(b1), "%d/%d", ui.snap.totals.n_ok, ui.snap.totals.n_bots);
            snprintf(b2, sizeof(b2), "%d", ui.snap.totals.n_pos);
            K kpis[] = {
                {"LIVE", b1, C.green},
                {"POSITIONS", b2, C.cyan},
                {"EQUITY", money(ui.snap.totals.equity), C.gold},
                {"uPnL", money(ui.snap.totals.upnl), ui.snap.totals.upnl < 0 ? C.rose : C.green},
            };
            int kw = (inner - 24) / 4;
            for (int i = 0; i < 4; ++i) {
                Rect r{x + i * (kw + 8), y, kw, 48};
                fill(dpy, buf, gc, C.panel, r);
                stroke(dpy, buf, gc, C.border, r);
                text(dpy, buf, gc, C.muted, r.x + 10, r.y + 16, kpis[i].l);
                text(dpy, buf, gc, kpis[i].c, r.x + 10, r.y + 36, kpis[i].v);
            }
            y += 60;

            text(dpy, buf, gc, C.green_dim, x, y + 4, "HUMMINGBOTS");
            y += 16;
            int cols = inner > 1100 ? 2 : 1;
            int cw = cols == 2 ? (inner - 10) / 2 : inner;
            auto bot_coins = [&](const Bot& b) {
                std::vector<std::string> coins = b.coins;
                if (coins.empty()) {
                    for (auto& kv : b.bias) coins.push_back(kv.first);
                }
                return coins;
            };
            int max_coins = 1;
            for (auto& b : ui.snap.bots)
                max_coins = std::max(max_coins, (int)bot_coins(b).size());
            const int foot_h = 22;
            bool any_lev = false;
            for (auto& b : ui.snap.bots)
                if (shows_lev_row(b.id)) any_lev = true;
            int ch = 58 + 26 + max_coins * 24 + 18 + foot_h;
            if (any_lev) ch += 26;
            for (int i = 0; i < static_cast<int>(ui.snap.bots.size()); ++i) {
                auto& b = ui.snap.bots[i];
                int col = i % cols;
                int row = i / cols;
                Rect r{x + col * (cw + 10), y + row * (ch + 10), cw, ch};
                ui.hits.push_back({Hit::Hunter, i, r});
                unsigned long colr = parse_hex(dpy, cm, b.color, C.green);
                bool hot = (i == ui.hover_bot);
                unsigned long bgc = hot ? hover_fill_col(dpy, cm, b.color, C.panel)
                                        : (i == ui.sel ? rgb(dpy, cm, 8, 32, 16) : C.panel);
                fill(dpy, buf, gc, bgc, r);
                stroke(dpy, buf, gc, b.deactivated ? C.rose
                        : (hot ? colr : (i == ui.sel ? C.gold : (b.ok ? C.border : C.rose))), r);
                fill(dpy, buf, gc, colr, {r.x, r.y, 4, r.h});
                XSetForeground(dpy, gc, b.deactivated ? C.rose : (b.running ? C.green : C.rose));
                XFillArc(dpy, buf, gc, r.x + 12, r.y + 8, 10, 10, 0, 360 * 64);
                const char* stlab = b.deactivated ? "0 HL IP" : (b.running ? "ACTIVE" : "OFF");
                unsigned long stc = b.deactivated ? C.rose : (b.running ? C.green : C.rose);
                int stw = static_cast<int>(strlen(stlab)) * CHAR_W;
                int st_x = r.x + r.w - 8 - stw;
                text(dpy, buf, gc, stc, st_x, r.y + 18, stlab);
                char slb[16];
                sl_now_label(b.sl_roe, slb, sizeof(slb));
                unsigned long slc = sl_level_col(C, b.sl_roe);
                Rect badge{st_x - 8 - 70, r.y + 6, 70, 18};
                fill(dpy, buf, gc, b.sl_roe <= 25 ? rgb(dpy, cm, 8, 36, 14)
                                 : (b.sl_roe <= 45 ? rgb(dpy, cm, 48, 28, 8) : rgb(dpy, cm, 50, 10, 18)), badge);
                stroke(dpy, buf, gc, slc, badge);
                text(dpy, buf, gc, slc, badge.x + 8, badge.y + 13, std::string("SL ") + slb);
                text(dpy, buf, gc, colr, r.x + 28, r.y + 18,
                     fit_px(b.name, badge.x - (r.x + 32)));
                text(dpy, buf, gc, C.muted, r.x + 12, r.y + 36,
                     fit_px(short_addr(b.address), r.w - 24));
                char line[160];
                snprintf(line, sizeof(line), "eq %s   leftover %s   pos %d",
                         money(b.equity).c_str(), money(b.free_usd).c_str(), b.n_pos);
                text(dpy, buf, gc, C.cyan, r.x + 12, r.y + 52, fit_px(line, r.w - 24));
                double live_roe = 0;
                std::string live_coin;
                bool has_roe = bot_live_roe(b, ui.snap, live_roe, live_coin);
                int row_y = r.y + 58;
                if (shows_lev_row(b.id))
                    row_y += draw_lev_strip(dpy, buf, gc, C, r.x + 12, row_y, b, &ui.hits, i, &ui.hover_r);
                int slh = draw_sl_strip(dpy, buf, gc, C, r.x + 12, row_y, b.sl_roe, &ui.hits, i,
                                        live_roe, has_roe, &ui.hover_r);
                auto coins = bot_coins(b);
                int by = row_y + slh + 6;
                int foot_y = r.y + ch - foot_h;
                std::string mode = b.bias_mode.empty() ? "both" : b.bias_mode;
                int right = r.x + r.w - 8;
                if (coins.empty()) {
                    text(dpy, buf, gc, C.muted, r.x + 12, by + 12, "no BIAS rail");
                    by += 18;
                } else {
                    for (auto& coin : coins) {
                        if (by + 22 > foot_y - 2) break;
                        text(dpy, buf, gc, C.muted, r.x + 10, by + 14, fit_px(coin, 44));
                        const char* modes[] = {"LONG", "FLAT", "SHORT"};
                        std::string cur = b.bias.count(coin) ? b.bias.at(coin) : "FLAT";
                        int bx0 = r.x + 56;
                        for (int m = 0; m < 3; ++m) {
                            bool allowed = !(mode == "long" && m == 2) && !(mode == "short" && m == 0);
                            Rect br{bx0 + m * 58, by, 54, 18};
                            if (br.x + br.w > right) break;
                            bool on = allowed && cur == modes[m];
                            unsigned long fc = bias_col(C, modes[m]);
                            fill(dpy, buf, gc, on ? fc : C.bg, br);
                            stroke(dpy, buf, gc, allowed ? fc : C.border, br);
                            int tx = br.x + (br.w - (int)strlen(modes[m]) * CHAR_W) / 2;
                            text(dpy, buf, gc, on ? C.bg : (allowed ? fc : C.muted), tx, br.y + 13, modes[m]);
                            if (allowed)
                                ui.hits.push_back({Hit::CoinBias, i, br, coin + "|" + modes[m]});
                        }
                        Rect cl{bx0 + 3 * 58 + 6, by, 48, 18};
                        Rect op{cl.x + 52, by, 48, 18};
                        Rect chb{op.x + 52, by, 48, 18};
                        if (cl.x + cl.w <= right) {
                            stroke(dpy, buf, gc, C.rose, cl);
                            text(dpy, buf, gc, C.rose, cl.x + 6, cl.y + 13, "Close");
                            ui.hits.push_back({Hit::CoinClose, i, cl, coin});
                        }
                        if (op.x + op.w <= right) {
                            stroke(dpy, buf, gc, C.cyan, op);
                            text(dpy, buf, gc, C.cyan, op.x + 6, op.y + 13, "100%");
                            ui.hits.push_back({Hit::CoinOpen100, i, op, coin});
                        }
                        if (chb.x + chb.w <= right) {
                            stroke(dpy, buf, gc, C.gold, chb);
                            text(dpy, buf, gc, C.gold, chb.x + 6, chb.y + 13, "Chart");
                            ui.hits.push_back({Hit::Chart, i, chb, coin});
                        }
                        int clip_x = chb.x + chb.w + 8;
                        if (shows_lev_row(b.id) && chb.x + chb.w <= right && clip_x < right - 18) {
                            std::string fx = lev_effect_text(ui.snap, b, coin);
                            text(dpy, buf, gc, lev_tier_col(C, lev_tier_of(b)), clip_x, by + 13,
                                 fit_px(fx, right - clip_x));
                            clip_x += static_cast<int>(fx.size()) * CHAR_W + 8;
                        }
                        if (clip_x < right - 40) {
                            for (auto& p : ui.snap.positions) {
                                if (p.bot_id != b.id || p.coin != coin) continue;
                                char clip[96];
                                snprintf(clip, sizeof(clip), "%s %.4g @ %.2f %+.1f%%",
                                         p.side.c_str(), p.size, p.entry, p.roe);
                                text(dpy, buf, gc, pos_pnl_col(C, p), clip_x, by + 13,
                                     fit_px(clip, right - clip_x));
                                break;
                            }
                        }
                        by += 24;
                    }
                }
                if (by + 14 <= foot_y) {
                    text(dpy, buf, gc, C.rose, r.x + 12, by + 12,
                         fit_px(b.next_sell.empty() ? "sell  -" : "sell  " + b.next_sell, r.w - 150));
                }

                Rect rst{r.x + r.w - 70, foot_y, 58, 16};
                Rect hch{rst.x - 66, foot_y, 58, 16};
                {
                    Rect gate{r.x + 8, foot_y, 120, 16};
                    if (gate.x + gate.w < hch.x - 4)
                        draw_ip_btn(dpy, buf, gc, C, gate, b.deactivated, b.running, ui.hits, i);
                }
                stroke(dpy, buf, gc, C.gold, hch);
                text(dpy, buf, gc, C.gold, hch.x + 8, hch.y + 12, "Chart");
                ui.hits.push_back({Hit::Chart, i, hch, follow_coin(b, ui.snap)});
                stroke(dpy, buf, gc, C.amber, rst);
                text(dpy, buf, gc, C.amber, rst.x + 4, rst.y + 12, "Restart");
                ui.hits.push_back({Hit::Restart, i, rst});
            }
            int rows = (static_cast<int>(ui.snap.bots.size()) + cols - 1) / std::max(1, cols);
            y += rows * (ch + 10) + 8;

            // positions
            text(dpy, buf, gc, C.muted, x, y + 12, "OPEN POSITIONS  (view only)");
            y += 22;
            text(dpy, buf, gc, C.muted, x, y, "hunter");
            text(dpy, buf, gc, C.muted, x + 150, y, "coin");
            text(dpy, buf, gc, C.muted, x + 210, y, "side");
            text(dpy, buf, gc, C.muted, x + 280, y, "size");
            text(dpy, buf, gc, C.muted, x + 380, y, "entry");
            text(dpy, buf, gc, C.muted, x + 480, y, "mark");
            text(dpy, buf, gc, C.muted, x + 580, y, "ROE");
            text(dpy, buf, gc, C.muted, x + 670, y, "uPnL");
            y += 16;
            if (ui.snap.positions.empty()) {
                text(dpy, buf, gc, C.muted, x, y, "no open positions");
                y += 18;
            }
            for (auto& p : ui.snap.positions) {
                unsigned long pc = pos_pnl_col(C, p);
                text(dpy, buf, gc, C.text, x, y, ascii(p.bot, 16));
                text(dpy, buf, gc, C.cyan, x + 150, y, p.coin);
                text(dpy, buf, gc, pc, x + 210, y, p.side);
                char b[32];
                snprintf(b, sizeof(b), "%.4g", p.size);
                text(dpy, buf, gc, C.text, x + 280, y, b);
                snprintf(b, sizeof(b), "%.4f", p.entry);
                text(dpy, buf, gc, C.text, x + 380, y, b);
                snprintf(b, sizeof(b), "%.4f", p.mark);
                text(dpy, buf, gc, C.text, x + 480, y, b);
                text(dpy, buf, gc, pc, x + 580, y, pct_s(p.roe));
                text(dpy, buf, gc, pc, x + 670, y, money(p.pnl));
                y += 16;
            }
            y += 10;

            // RISK
            text(dpy, buf, gc, C.gold, x, y + 12, "RISK WALLET");
            Rect send{x + 130, y, 90, 20};
            stroke(dpy, buf, gc, C.rose, send);
            text(dpy, buf, gc, C.rose, send.x + 8, send.y + 14, "Send ready");
            ui.hits.push_back({Hit::NotWired, 0, send});
            text(dpy, buf, gc, C.muted, x + 230, y + 14,
                 ascii("parked " + money(ui.snap.risk.parked_usd) +
                       "  ready " + money(ui.snap.risk.available_usd) +
                       "  locked " + money(ui.snap.risk.locked_usd) +
                       "  ·  send not wired", 90));
            y += 26;
            for (auto& r : ui.snap.risk.hunters) {
                char line[200];
                snprintf(line, sizeof(line), "%-14s  parked %s  ready %s  locked %s  %s",
                         ascii(r.name, 14).c_str(), money(r.parked_usd).c_str(),
                         money(r.available_usd).c_str(), money(r.locked_usd).c_str(),
                         r.n_pos ? "open" : (r.available_usd >= 1 ? "ready" : "idle"));
                text(dpy, buf, gc, C.text, x, y, line);
                y += 16;
            }
            y += 8;

            // health
            text(dpy, buf, gc, C.gold, x, y + 12, "HEALTH");
            snprintf(b3, sizeof(b3), "CPU %.0f%%", ui.snap.host.cpu_pct);
            snprintf(b4, sizeof(b4), "RAM %.0f%%", ui.snap.host.mem_pct);
            char b5[32], b6[48];
            snprintf(b5, sizeof(b5), "disk %.0f%%", ui.snap.host.disk_pct);
            snprintf(b6, sizeof(b6), "net %s %dms", ui.snap.net.ok ? "up" : "DOWN", ui.snap.net.ms);
            text(dpy, buf, gc, C.text, x + 80, y + 12, std::string(b3) + "   " + b4 + "   " + b5 + "   " + b6);
            y += 28;

            // leaders
            if (!ui.snap.leaders.empty()) {
                text(dpy, buf, gc, C.gold, x, y + 12, "COPY LEADERS  (view)");
                y += 22;
                for (auto& L : ui.snap.leaders) {
                    char line[200];
                    snprintf(line, sizeof(line), "%s  %s  day %s  week %s  pos %d",
                             L.followed ? "ON " : "off",
                             ascii(L.title, 36).c_str(),
                             money(L.day_pnl).c_str(), money(L.week_pnl).c_str(), L.n_positions);
                    text(dpy, buf, gc, L.followed ? C.cyan : C.muted, x, y, line);
                    y += 16;
                }
            }
            ui.content_h = y + ui.scroll - body.y + 24;
        };

        auto draw_wire = [&]() {
            ui.prune_marks();
            const int TB = 248;
            const int HUD = 50;
            const int INSP = (body.h < 520) ? 132 : 200;
            bool inspecting = ui.wire_inspect >= 0 && ui.wire_inspect < (int)ui.snap.wire_nodes.size();
            const int NP = inspecting ? 308 : 0;
            Rect hud{0, body.y, W, HUD};
            Rect tb{0, body.y + HUD, TB, body.h - HUD - INSP};
            Rect np{W - NP, body.y + HUD, NP, body.h - HUD - INSP};
            Rect canvas{TB, body.y + HUD, W - TB - NP, body.h - HUD - INSP};
            Rect insp{0, body.y + HUD + tb.h, W, INSP};
            ui.tb_r = tb;
            ui.canvas_r = canvas;
            ui.insp_r = insp;
            ui.np_r = inspecting ? np : Rect{};

            fill(dpy, buf, gc, C.ab_bg, body);
            fill(dpy, buf, gc, C.ab_head, hud);
            XSetForeground(dpy, gc, C.ab_orange);
            XDrawLine(dpy, buf, gc, 0, hud.y + hud.h, W, hud.y + hud.h);

            // HUD — same KPIs + canvas board + Ableton-style actions as web Wire
            const char* boards[] = {"DESK", "HOME", "HEALTH"};
            int hx = 10;
            for (int i = 0; i < 3; ++i) {
                int tw = static_cast<int>(strlen(boards[i])) * 9 + 16;
                Rect r{hx, hud.y + 14, tw, 22};
                bool on = ui.wire_board == i;
                fill(dpy, buf, gc, on ? C.ab_orange : C.ab_panel, r);
                stroke(dpy, buf, gc, C.ab_orange, r);
                text(dpy, buf, gc, on ? C.ab_bg : C.ab_orange, r.x + 8, r.y + 15, boards[i]);
                ui.hits.push_back({Hit::WireBoard, i, r});
                hx += tw + 6;
            }
            Rect snapr{hx + 4, hud.y + 14, 86, 22};
            fill(dpy, buf, gc, ui.wire_snap ? rgb(dpy, cm, 40, 28, 8) : C.ab_panel, snapr);
            stroke(dpy, buf, gc, ui.wire_snap ? C.gold : C.ab_muted, snapr);
            text(dpy, buf, gc, ui.wire_snap ? C.gold : C.ab_muted, snapr.x + 8, snapr.y + 15,
                 ui.wire_snap ? "[x] SNAP" : "[ ] SNAP");
            ui.hits.push_back({Hit::WireSnap, 0, snapr});
            hx += 96;
            char kpi[96];
            snprintf(kpi, sizeof(kpi), "live %d/%d   pos %d   eq %s   uPnL %s   IP %.0f/%.0f",
                     ui.snap.totals.n_ok, ui.snap.totals.n_bots, ui.snap.totals.n_pos,
                     money(ui.snap.totals.equity).c_str(), money(ui.snap.totals.upnl).c_str(),
                     ui.snap.rate.used, ui.snap.rate.cap);
            text(dpy, buf, gc, C.ab_text, hx + 8, hud.y + 20, kpi);
            text(dpy, buf, gc, C.ab_muted, hx + 8, hud.y + 38,
                 ascii((ui.dirty ? "EDITED  " : "") +
                       ("n " + std::to_string(ui.snap.wire_nodes.size()) + "  e " +
                        std::to_string(ui.snap.wire_edges.size()) +
                        (ui.wire_marked.size() > 1
                             ? ("  marked " + std::to_string((int)ui.wire_marked.size()))
                             : "") +
                        "  " + ui.snap.wire_board +
                        "  ·  Save = cosmetics  ·  Compile = wires"), 90));
            std::string undol = ui.undo.empty() ? "Undo" : ("Undo " + std::to_string((int)ui.undo.size()));
            std::string redol = ui.redo.empty() ? "Redo" : ("Redo " + std::to_string((int)ui.redo.size()));
            std::string acts[] = {undol, redol, "Fit", "Compile", "Integrate", "Save", "XML", "Reload"};
            int ax = W - 8;
            for (int i = 7; i >= 0; --i) {
                int tw = static_cast<int>(acts[i].size()) * 9 + 14;
                ax -= tw + 5;
                Rect r{ax, hud.y + 14, tw, 22};
                unsigned long ac = C.ab_muted;
                if (i == 2) ac = C.ab_orange;
                if (i == 3) ac = C.gold;
                if (i == 4 && ui.snap.compile.can_integrate) ac = C.green;
                stroke(dpy, buf, gc, ac, r);
                text(dpy, buf, gc, ac, r.x + 6, r.y + 15, acts[i].c_str());
                ui.hits.push_back({Hit::WireAct, i, r});
            }

            // Toolbox (left) always
            fill(dpy, buf, gc, C.ab_panel, tb);
            XSetForeground(dpy, gc, C.ab_orange);
            XDrawLine(dpy, buf, gc, tb.x + tb.w, tb.y, tb.x + tb.w, tb.y + tb.h);
            text(dpy, buf, gc, C.ab_orange, tb.x + 10, tb.y + 16, "BROWSER");
            text(dpy, buf, gc, C.ab_muted, tb.x + 90, tb.y + 16, "toolbox");
            set_clip(dpy, gc, {tb.x, tb.y + 24, tb.w, tb.h - 24});
            std::set<std::string> on_canvas;
            for (auto& n : ui.snap.wire_nodes) on_canvas.insert(n.id);
            int ty = tb.y + 28 - ui.tb_scroll;
            for (int ci = 0; ci < WIRE_NCAT; ++ci) {
                bool folded = (ui.tb_fold & (1u << ci)) != 0;
                Rect ch{tb.x + 4, ty, tb.w - 10, 18};
                text(dpy, buf, gc, C.ab_orange, ch.x + 6, ch.y + 13,
                     std::string(folded ? "> " : "v ") + WIRE_CATS[ci].label);
                ui.hits.push_back({Hit::ToolCat, ci, ch});
                ty += 20;
                if (folded) continue;
                for (int ti = 0; ti < static_cast<int>(ui.snap.tools.size()); ++ti) {
                    auto& t = ui.snap.tools[ti];
                    if (t.category != WIRE_CATS[ci].id) continue;
                    const V2Blurb* v2b = (t.category == std::string("hummingbots_v2")) ? v2_blurb(t.id) : nullptr;
                    bool v2desc = v2b && v2b->menu && v2b->menu[0];
                    int row_h = v2desc ? 46 : 18;
                    int step = v2desc ? 48 : 20;
                    if (ty + row_h < tb.y + 24 || ty > tb.y + tb.h) { ty += step; continue; }
                    bool on = t.unique && on_canvas.count(t.id);
                    bool reading = (t.id == ui.tool_pick_id);
                    Rect row{tb.x + 8, ty, tb.w - 16, row_h};
                    if (reading) fill(dpy, buf, gc, rgb(dpy, cm, 48, 36, 8), row);
                    else if (on) fill(dpy, buf, gc, rgb(dpy, cm, 55, 40, 32), row);
                    unsigned long dotc = parse_hex(dpy, cm, t.color, C.ab_orange);
                    XSetForeground(dpy, gc, dotc);
                    XFillArc(dpy, buf, gc, row.x + 4, row.y + 5, 8, 8, 0, 360 * 64);
                    text(dpy, buf, gc, C.ab_text, row.x + 18, row.y + 13, ascii(t.label, 18));
                    text(dpy, buf, gc, on ? C.ab_orange : C.ab_muted, row.x + row.w - 28, row.y + 13,
                         on ? "on" : "+");
                    if (reading) stroke(dpy, buf, gc, C.gold, row);
                    if (v2desc) {
                        int ly = row.y + 26;
                        for (auto& line : wrap_px(v2b->menu, std::max(40, row.w - 22), 2)) {
                            text(dpy, buf, gc, C.ab_muted, row.x + 18, ly, line);
                            ly += 13;
                        }
                    }
                    ui.hits.push_back({Hit::ToolItem, ti, row});
                    ty += step;
                }
            }
            ui.content_h = 0;
            clear_clip(dpy, gc);

            if (inspecting && NP > 0) {
                fill(dpy, buf, gc, C.ab_panel, np);
                XSetForeground(dpy, gc, C.ab_orange);
                XDrawLine(dpy, buf, gc, np.x, np.y, np.x, np.y + np.h);
                auto& n = ui.snap.wire_nodes[ui.wire_inspect];
                unsigned long ncol = parse_hex(dpy, cm, n.color, C.ab_orange);
                text(dpy, buf, gc, C.gold, np.x + 10, np.y + 16, "NODE");
                Rect back{np.x + np.w - 78, np.y + 4, 70, 18};
                stroke(dpy, buf, gc, C.ab_orange, back);
                text(dpy, buf, gc, C.ab_orange, back.x + 10, back.y + 13, "Close");
                ui.hits.push_back({Hit::InspectBack, 0, back});
                set_clip(dpy, gc, {np.x, np.y + 24, np.w, np.h - 24});
                int iy = np.y + 32 - ui.inspect_scroll;
                text(dpy, buf, gc, ncol, np.x + 10, iy, ascii(n.type, 22));
                iy += 16;
                text(dpy, buf, gc, C.ab_text, np.x + 10, iy, ascii(n.label + (n.type=="frame"||n.type=="circle"?"_":""), 22));
                iy += 16;
                text(dpy, buf, gc, C.ab_muted, np.x + 10, iy, ascii(n.id, 24));
                iy += 20;
                Rect mini{np.x + 10, iy, np.w - 20, n.type == "circle" ? 72 : 48};
                fill(dpy, buf, gc, rgb(dpy, cm, 8, 16, 12), mini);
                if (n.type == "circle") {
                    XSetForeground(dpy, gc, ncol);
                    XDrawArc(dpy, buf, gc, mini.x + 8, mini.y + 4, mini.w - 16, mini.h - 8, 0, 360 * 64);
                } else {
                    stroke(dpy, buf, gc, ncol, mini);
                }
                text(dpy, buf, gc, ncol, mini.x + 10, mini.y + mini.h / 2 + 4, ascii(n.label, 18));
                iy += mini.h + 12;
                if (n.type == "frame" || n.type == "circle") {
                    text(dpy, buf, gc, C.ab_muted, np.x + 10, iy, "caption  (type)");
                    iy += 16;
                    text(dpy, buf, gc, C.ab_muted, np.x + 10, iy, "color");
                    iy += 6;
                    const char* ANNOT_COLS2[] = {
                        "#3d7a55", "#00ff6a", "#7ecbff", "#f0b90b", "#ffc857", "#ff4d6d", "#a78bfa", "#ffffff"
                    };
                    for (int ci = 0; ci < 8; ++ci) {
                        Rect cr{np.x + 10 + ci * 26, iy, 22, 16};
                        unsigned long cc = parse_hex(dpy, cm, ANNOT_COLS2[ci], C.gold);
                        fill(dpy, buf, gc, cc, cr);
                        stroke(dpy, buf, gc, n.color == ANNOT_COLS2[ci] ? C.white : C.border, cr);
                        ui.hits.push_back({Hit::AnnotColor, ui.wire_inspect, cr, ANNOT_COLS2[ci]});
                    }
                    iy += 24;
                    text(dpy, buf, gc, C.ab_muted, np.x + 10, iy, "drag gold corner to resize");
                    iy += 20;
                }
                text(dpy, buf, gc, C.gold, np.x + 10, iy, "PLUGS");
                iy += 16;
                auto outs = src_ports(n), ins = tgt_ports(n);
                if (outs.empty() && ins.empty()) {
                    text(dpy, buf, gc, C.ab_muted, np.x + 10, iy, "no plugs (draw tool)");
                    iy += 16;
                }
                for (auto& h : outs) {
                    Rect pr{np.x + 10, iy, np.w - 20, 18};
                    unsigned long pc = handle_col(C, h);
                    fill(dpy, buf, gc, pc, {pr.x, pr.y + 5, 8, 8});
                    text(dpy, buf, gc, C.ab_text, pr.x + 14, pr.y + 13, ascii("out  " + h, 20));
                    ui.hits.push_back({Hit::WirePort, ui.wire_inspect, pr, h, true});
                    iy += 18;
                }
                for (auto& h : ins) {
                    Rect pr{np.x + 10, iy, np.w - 20, 18};
                    unsigned long pc = handle_col(C, h);
                    stroke(dpy, buf, gc, pc, {pr.x, pr.y + 5, 8, 8});
                    text(dpy, buf, gc, C.ab_text, pr.x + 14, pr.y + 13, ascii("in   " + h, 20));
                    ui.hits.push_back({Hit::WirePort, ui.wire_inspect, pr, h, false});
                    iy += 18;
                }
                iy += 8;
                text(dpy, buf, gc, C.gold, np.x + 10, iy, "VARIABLES");
                iy += 16;
                const Bot* lb = live_bot(ui.snap, n);
                int bix = -1;
                if (lb) {
                    for (int bi = 0; bi < (int)ui.snap.bots.size(); ++bi)
                        if (ui.snap.bots[bi].id == lb->id) { bix = bi; break; }
                    char line[96];
                    snprintf(line, sizeof(line), "%s  eq %s", lb->running ? "LIVE" : "DOWN", money(lb->equity).c_str());
                    text(dpy, buf, gc, lb->running ? C.ab_orange : C.rose, np.x + 10, iy, ascii(line, 24));
                    iy += 16;
                    snprintf(line, sizeof(line), "leftover %s  pos %d", money(lb->free_usd).c_str(), lb->n_pos);
                    text(dpy, buf, gc, C.ab_text, np.x + 10, iy, ascii(line, 24));
                    iy += 18;
                    if (bix >= 0) {
                        char slb[16];
                        sl_now_label(lb->sl_roe, slb, sizeof(slb));
                        text(dpy, buf, gc, sl_level_col(C, lb->sl_roe), np.x + 10, iy + 13,
                             std::string("SL ") + slb);
                        const int presets[] = {20, 40, 60};
                        for (int k = 0; k < 3; ++k) {
                            Rect br{np.x + 78 + k * 50, iy, 46, 18};
                            unsigned long pc = sl_preset_col(C, presets[k]);
                            bool on = sl_match(lb->sl_roe, presets[k]);
                            fill(dpy, buf, gc, on ? pc : C.ab_bg, br);
                            stroke(dpy, buf, gc, pc, br);
                            char lb2[8];
                            snprintf(lb2, sizeof(lb2), "%d%%", presets[k]);
                            text(dpy, buf, gc, on ? C.ab_bg : pc, br.x + 8, br.y + 13, lb2);
                            char key[8];
                            snprintf(key, sizeof(key), "%d", presets[k]);
                            ui.hits.push_back({Hit::Sl, bix, br, key});
                        }
                        iy += 22;
                        std::string mode = lb->bias_mode.empty() ? "both" : lb->bias_mode;
                        for (auto& coin : lb->coins) {
                            if (iy + 20 > np.y + np.h) break;
                            text(dpy, buf, gc, C.ab_muted, np.x + 10, iy + 13, ascii(coin, 4));
                            const char* modes[] = {"LONG", "FLAT", "SHORT"};
                            std::string cur = lb->bias.count(coin) ? lb->bias.at(coin) : "FLAT";
                            for (int m = 0; m < 3; ++m) {
                                Rect br{np.x + 48 + m * 58, iy, 54, 18};
                                unsigned long fc = bias_col(C, modes[m]);
                                bool on = cur == modes[m];
                                fill(dpy, buf, gc, on ? fc : C.ab_bg, br);
                                stroke(dpy, buf, gc, fc, br);
                                text(dpy, buf, gc, on ? C.ab_bg : fc, br.x + 6, br.y + 13, modes[m]);
                                ui.hits.push_back({Hit::CoinBias, bix, br, coin + std::string("|") + modes[m]});
                            }
                            iy += 22;
                        }
                    }
                    double roe = 0; std::string rc;
                    if (bot_live_roe(*lb, ui.snap, roe, rc)) {
                        char rs[48];
                        snprintf(rs, sizeof(rs), "ROE %s %+.1f%%", rc.c_str(), roe);
                        text(dpy, buf, gc, roe < 0 ? C.rose : C.green, np.x + 10, iy, ascii(rs, 24));
                        iy += 16;
                    }
                } else if (n.type == "coin") {
                    auto it = ui.snap.mids.find(n.label);
                    char line[48];
                    if (it != ui.snap.mids.end()) snprintf(line, sizeof(line), "mid %.4g", it->second);
                    else snprintf(line, sizeof(line), "coin %s", n.label.c_str());
                    text(dpy, buf, gc, C.ab_text, np.x + 10, iy, line);
                    iy += 16;
                } else if (n.type == "pct") {
                    text(dpy, buf, gc, C.ab_text, np.x + 10, iy, n.kind == "moon" ? "MOON / TP" : "SL ROE");
                    iy += 16;
                } else if (n.type == "gauge") {
                    text(dpy, buf, gc, C.ab_text, np.x + 10, iy, ascii(ui.snap.condor.regime + " / " + ui.snap.condor.source, 24));
                    iy += 16;
                } else if (!lb) {
                    text(dpy, buf, gc, C.ab_muted, np.x + 10, iy, "no live hunter vars");
                    iy += 16;
                }
                if (lb) {
                    auto fields = ui.fields_for(*lb);
                    if (!fields.empty()) {
                        text(dpy, buf, gc, C.gold, np.x + 10, iy, "KNOBS");
                        iy += 14;
                        text(dpy, buf, gc, C.ab_muted, np.x + 10, iy, "drag  ·  Shift fine  ·  no HL close");
                        iy += 18;
                        const int cols = 2;
                        const int cellw = (np.w - 20) / cols;
                        const int cellh = 84;
                        int colx = 0;
                        unsigned long kcol = ncol;
                        for (auto& f : fields) {
                            auto stored = lb->controller.contains(f.key) ? lb->controller[f.key] : nlohmann::json();
                            double dv = display_value(f, stored);
                            if ((ui.knob_drag || ui.knob_focus_bot == bix)
                                && ui.knob_focus_key == f.key)
                                dv = ui.knob_live;
                            if (f.kind == "bool") {
                                if (colx) { colx = 0; iy += cellh; }
                                bool on = dv >= 0.5;
                                if (f.key == "sendEvery2Win") {
                                    Rect row{np.x + 10, iy, np.w - 20, 36};
                                    Rect ck{row.x, row.y + 2, 16, 16};
                                    stroke(dpy, buf, gc, C.gold, ck);
                                    if (on) fill(dpy, buf, gc, C.gold, {ck.x + 3, ck.y + 3, 10, 10});
                                    int tx = ck.x + 22;
                                    int chars = std::max(8, (row.x + row.w - tx) / CHAR_W);
                                    std::string la, lb;
                                    split_label(f.label, chars, la, lb);
                                    text(dpy, buf, gc, on ? C.gold : C.ab_muted, tx, row.y + 14, la);
                                    if (!lb.empty())
                                        text(dpy, buf, gc, on ? C.gold : C.ab_muted, tx, row.y + 28, lb);
                                    if (bix >= 0) ui.hits.push_back({Hit::CtrlBool, bix, row, f.key});
                                    iy += 40;
                                    continue;
                                }
                                Rect row{np.x + 10, iy, np.w - 20, 20};
                                text(dpy, buf, gc, C.ab_muted, row.x, row.y + 13, ascii(f.label, 18));
                                Rect ck{row.x + row.w - 28, row.y + 2, 22, 16};
                                stroke(dpy, buf, gc, C.gold, ck);
                                if (on) fill(dpy, buf, gc, C.gold, {ck.x + 3, ck.y + 3, 16, 10});
                                if (bix >= 0) ui.hits.push_back({Hit::CtrlBool, bix, row, f.key});
                                iy += 22;
                                continue;
                            }
                            if (f.kind == "select") {
                                if (colx) { colx = 0; iy += cellh; }
                                Rect row{np.x + 10, iy, np.w - 20, 20};
                                text(dpy, buf, gc, C.ab_muted, row.x, row.y + 13, ascii(f.label, 12));
                                std::string cur = json_preview(stored);
                                Rect sv{row.x + row.w - 54, row.y, 50, 18};
                                stroke(dpy, buf, gc, C.gold, sv);
                                text(dpy, buf, gc, C.gold, sv.x + 8, sv.y + 13, ascii(cur, 5));
                                if (bix >= 0) ui.hits.push_back({Hit::CtrlSel, bix, row, f.key});
                                iy += 22;
                                continue;
                            }
                            int kx = np.x + 10 + colx * cellw;
                            int kcx = kx + cellw / 2;
                            int kcy = iy + 22;
                            bool editing = ui.knob_edit && ui.knob_edit_bot == bix
                                && ui.knob_edit_key == f.key;
                            if (f.key == "close_px") {
                                if (colx) { colx = 0; iy += cellh; kx = np.x + 10; }
                                Rect box{kx, iy + 4, np.w - 20, 56};
                                fill(dpy, buf, gc, C.navy, box);
                                stroke(dpy, buf, gc, C.gold, box);
                                text(dpy, buf, gc, C.gold, box.x + 8, box.y + 14, "Close $");
                                if (editing) {
                                    std::string show = ui.knob_edit_buf;
                                    if (!ui.knob_edit_sel) show += "|";
                                    if (ui.knob_edit_sel)
                                        fill(dpy, buf, gc, C.gold, {box.x + 8, box.y + 20, box.w - 16, 18});
                                    text(dpy, buf, gc, ui.knob_edit_sel ? C.bg : C.green,
                                         box.x + 12, box.y + 34, ascii(show, 12));
                                } else {
                                    char vb[32];
                                    snprintf(vb, sizeof(vb), "%.2f", dv);
                                    text(dpy, buf, gc, C.green, box.x + 12, box.y + 34, vb);
                                }
                                text(dpy, buf, gc, C.ab_muted, box.x + 8, box.y + 50,
                                     "dbl-click  type  Enter");
                                if (bix >= 0) ui.hits.push_back({Hit::CtrlKnob, bix, box, f.key});
                                iy += 64;
                                colx = 0;
                                continue;
                            }
                            double span = (f.maxv > f.minv) ? (f.maxv - f.minv) : 1;
                            double t = (dv - f.minv) / span;
                            Rect dial = draw_knob(dpy, buf, gc, C, kcx, kcy, 16, t, kcol);
                            if (editing) {
                                Rect box{kcx - 34, iy + 40, 68, 16};
                                fill(dpy, buf, gc, C.navy, box);
                                stroke(dpy, buf, gc, C.gold, box);
                                if (ui.knob_edit_sel)
                                    fill(dpy, buf, gc, C.gold, {box.x + 2, box.y + 2, box.w - 4, box.h - 4});
                                std::string show = ui.knob_edit_buf;
                                if (!ui.knob_edit_sel) show += "|";
                                int tw = static_cast<int>(show.size()) * CHAR_W;
                                text(dpy, buf, gc, ui.knob_edit_sel ? C.bg : C.green,
                                     kcx - tw / 2, iy + 52, ascii(show, 7));
                            } else {
                                char vb[32];
                                if (f.step < 0.01) snprintf(vb, sizeof(vb), "%.3f", dv);
                                else if (f.step < 1) snprintf(vb, sizeof(vb), "%.2f", dv);
                                else snprintf(vb, sizeof(vb), "%.0f", dv);
                                int tw = static_cast<int>(std::strlen(vb)) * CHAR_W;
                                text(dpy, buf, gc, C.green, kcx - tw / 2, iy + 48, vb);
                            }
                            text(dpy, buf, gc, C.ab_muted, kx, iy + 64, ascii(f.label, cellw / CHAR_W - 1));
                            if (bix >= 0) {
                                Rect khit{kx, iy, cellw, cellh - 8};
                                ui.hits.push_back({Hit::CtrlKnob, bix, khit, f.key});
                            }
                            (void)dial;
                            colx++;
                            if (colx >= cols) { colx = 0; iy += cellh; }
                        }
                        if (colx) iy += cellh;
                    }
                }
                ui.inspect_h = iy + ui.inspect_scroll - (np.y + 24) + 12;
                ui.content_h = 0;
                clear_clip(dpy, gc);
            }

            // Session canvas
            fill(dpy, buf, gc, C.ab_bg, canvas);
            if (!ui.wire_fitted) ui.fit_wire(canvas);
            auto sx = [&](double x) { return canvas.x + static_cast<int>((x - ui.pan_x) * ui.zoom); };
            auto sy = [&](double y) { return canvas.y + static_cast<int>((y - ui.pan_y) * ui.zoom); };
            // A card panned under the toolbox is clipped when drawn. Its hit box
            // must be clipped too, or the hidden part steals the toolbox click.
            auto hit_on_canvas = [&](Hit::Kind kind, int idx, Rect r, const std::string& key = "", bool src = false) {
                int x1 = std::max(r.x, canvas.x);
                int y1 = std::max(r.y, canvas.y);
                int x2 = std::min(r.x + r.w, canvas.x + canvas.w);
                int y2 = std::min(r.y + r.h, canvas.y + canvas.h);
                if (x2 - x1 < 1 || y2 - y1 < 1) return;
                ui.hits.push_back({kind, idx, {x1, y1, x2 - x1, y2 - y1}, key, src});
            };
            set_clip(dpy, gc, canvas);
            int step = std::max(12, static_cast<int>(48 * ui.zoom));
            XSetForeground(dpy, gc, C.ab_grid);
            for (int gx = canvas.x + ((-static_cast<int>(ui.pan_x * ui.zoom)) % step + step) % step; gx < canvas.x + canvas.w; gx += step)
                XDrawLine(dpy, buf, gc, gx, canvas.y, gx, canvas.y + canvas.h);
            for (int gy = canvas.y + ((-static_cast<int>(ui.pan_y * ui.zoom)) % step + step) % step; gy < canvas.y + canvas.h; gy += step)
                XDrawLine(dpy, buf, gc, canvas.x, gy, canvas.x + canvas.w, gy);

            std::map<std::string, int> ix;
            for (int i = 0; i < static_cast<int>(ui.snap.wire_nodes.size()); ++i)
                ix[ui.snap.wire_nodes[i].id] = i;
            auto draw_edge = [&](int ei, bool hit) {
                auto& e = ui.snap.wire_edges[ei];
                auto a = ix.find(e.source);
                auto b = ix.find(e.target);
                if (a == ix.end() || b == ix.end()) return;
                auto& na = ui.snap.wire_nodes[a->second];
                auto& nb = ui.snap.wire_nodes[b->second];
                bool ea = false, eb = false;
                Rect ra = node_view_box(na, ea, ui.zoom, sx(na.x), sy(na.y));
                Rect rb = node_view_box(nb, eb, ui.zoom, sx(nb.x), sy(nb.y));
                int x1 = 0, y1 = 0, x2 = 0, y2 = 0;
                find_port_xy(na, ra, e.source_handle, true, x1, y1);
                find_port_xy(nb, rb, e.target_handle, false, x2, y2);
                int dx1 = 0, dy1 = 0, dx2 = 0, dy2 = 0;
                port_stub(ra, x1, y1, dx1, dy1);
                port_stub(rb, x2, y2, dx2, dy2);
                unsigned long wc = ei == ui.edge_sel ? C.ab_orange : handle_col(C, e.source_handle);
                XSetForeground(dpy, gc, wc);
                XDrawLine(dpy, buf, gc, x1, y1, x1 + dx1, y1 + dy1);
                XDrawLine(dpy, buf, gc, x1 + dx1, y1 + dy1, x2 + dx2, y2 + dy2);
                XDrawLine(dpy, buf, gc, x2 + dx2, y2 + dy2, x2, y2);
                XFillArc(dpy, buf, gc, x1 - 3, y1 - 3, 6, 6, 0, 360 * 64);
                XFillArc(dpy, buf, gc, x2 - 3, y2 - 3, 6, 6, 0, 360 * 64);
                if (hit) {
                    int mx = (x1 + dx1 + x2 + dx2) / 2, my = (y1 + dy1 + y2 + dy2) / 2;
                    hit_on_canvas(Hit::WireEdge, ei, {mx - 8, my - 8, 16, 16});
                }
            };
            for (int ei = 0; ei < static_cast<int>(ui.snap.wire_edges.size()); ++ei)
                draw_edge(ei, true);
            if (ui.wire_from >= 0 && ui.wire_from < static_cast<int>(ui.snap.wire_nodes.size())) {
                auto& n = ui.snap.wire_nodes[ui.wire_from];
                bool exp = false;
                Rect nr = node_view_box(n, exp, ui.zoom, sx(n.x), sy(n.y));
                int x1 = nr.x + nr.w / 2, y1 = nr.y + nr.h / 2;
                find_port_xy(n, nr, ui.wire_handle, ui.wire_from_src, x1, y1);
                int dx1 = 0, dy1 = 0;
                port_stub(nr, x1, y1, dx1, dy1);
                XSetForeground(dpy, gc, C.ab_orange);
                XDrawLine(dpy, buf, gc, x1, y1, x1 + dx1, y1 + dy1);
                XDrawLine(dpy, buf, gc, x1 + dx1, y1 + dy1, ui.mx_last, ui.my_last);
                XFillArc(dpy, buf, gc, x1 - 3, y1 - 3, 6, 6, 0, 360 * 64);
            }
            std::vector<int> nord;
            for (int i = 0; i < static_cast<int>(ui.snap.wire_nodes.size()); ++i) {
                auto& n = ui.snap.wire_nodes[i];
                if (n.type == "frame" || n.type == "circle") nord.push_back(i);
            }
            for (int i = 0; i < static_cast<int>(ui.snap.wire_nodes.size()); ++i) {
                auto& n = ui.snap.wire_nodes[i];
                if (n.type != "frame" && n.type != "circle") nord.push_back(i);
            }
            for (int i : nord) {
                auto& n = ui.snap.wire_nodes[i];
                bool exp = false;
                double z = exp ? std::max(ui.zoom, 0.82) : ui.zoom;
                int nw = std::max(48, static_cast<int>(node_w(n, exp) * z));
                int nh = std::max(22, static_cast<int>(node_h(n, exp) * z));
                Rect r{sx(n.x), sy(n.y), nw, nh};
                unsigned long col = parse_hex(dpy, cm, n.color, C.ab_orange);
                if (n.type == "frame" || n.type == "circle") {
                    if (n.type == "circle") {
                        fill(dpy, buf, gc, rgb(dpy, cm, 8, 16, 12), r);
                        XSetForeground(dpy, gc, rgb(dpy, cm, 12, 28, 20));
                        XFillArc(dpy, buf, gc, r.x, r.y, r.w, r.h, 0, 360 * 64);
                        XSetForeground(dpy, gc, col);
                        XDrawArc(dpy, buf, gc, r.x, r.y, r.w, r.h, 0, 360 * 64);
                        if (exp) XDrawArc(dpy, buf, gc, r.x + 1, r.y + 1, std::max(2, r.w - 2), std::max(2, r.h - 2), 0, 360 * 64);
                    } else {
                        fill(dpy, buf, gc, rgb(dpy, cm, 6, 18, 10), r);
                        stroke(dpy, buf, gc, col, r);
                        if (exp) {
                            Rect inner{r.x + 2, r.y + 2, r.w - 4, r.h - 4};
                            stroke(dpy, buf, gc, col, inner);
                        }
                    }
                    hit_on_canvas(Hit::WireNode, i, r);
                    std::string cap = n.label.empty() ? (n.type == "circle" ? "NOTE" : "GROUP") : n.label;
                    if (i == ui.wire_inspect) cap += "_";
                    int tw = static_cast<int>(cap.size()) * CHAR_W;
                    int tx = r.x + std::max(8, (r.w - tw) / 2);
                    int ty = n.type == "circle" ? r.y + r.h / 2 : r.y + 18;
                    text(dpy, buf, gc, col, tx, ty, ascii(cap, std::max(4, r.w / CHAR_W - 2)));
                    if (i == ui.wire_sel || i == ui.wire_inspect || ui.is_marked(i)) {
                        Rect sz{r.x + r.w - 14, r.y + r.h - 14, 12, 12};
                        fill(dpy, buf, gc, C.gold, sz);
                        hit_on_canvas(Hit::AnnotSize, i, sz);
                    }
                    continue;
                }
                bool hunter = is_hunter_node(n);
                bool fat = fat_node(n);
                bool picked = (i == ui.wire_sel) || ui.is_marked(i);
                fill(dpy, buf, gc, hunter ? C.navy : (exp ? rgb(dpy, cm, 18, 22, 16) : C.ab_clip), r);
                if (hunter) {
                    stroke(dpy, buf, gc, picked ? C.gold : C.green, r);
                    Rect inner{r.x + 1, r.y + 1, r.w - 2, r.h - 2};
                    stroke(dpy, buf, gc, picked ? C.gold : C.green, inner);
                    fill(dpy, buf, gc, C.green, {r.x, r.y, 4, r.h});
                } else {
                    stroke(dpy, buf, gc, (exp || picked) ? C.gold : col, r);
                    if (picked) {
                        Rect inner{r.x + 1, r.y + 1, r.w - 2, r.h - 2};
                        stroke(dpy, buf, gc, C.gold, inner);
                    }
                }
                fill(dpy, buf, gc, hunter ? C.green_dim : col, {r.x, r.y, r.w, std::min(exp || fat ? 20 : 16, nh)});
                if (hunter) fill(dpy, buf, gc, C.green, {r.x, r.y, 4, std::min(exp || fat ? 20 : 16, nh)});
                int maxc = std::max(4, (nw - 12) / CHAR_W);
                std::string head;
                if (hunter) head = "HUM  " + n.label;
                else if (n.type == "risk_wallet") head = n.label.empty() ? "RISK" : n.label;
                else head = n.type + "  " + n.label;
                text(dpy, buf, gc, C.ab_bg, r.x + 8, r.y + 14, ascii(head, maxc));
                const Bot* lb = live_bot(ui.snap, n);
                int ly = r.y + (exp || fat ? 36 : 28);
                int lh = exp || fat ? 15 : 14;
                auto line_ok = [&](int need) { return ly + need < r.y + r.h - 6; };
                auto put = [&](unsigned long c, const std::string& s) {
                    if (!line_ok(0)) return;
                    text(dpy, buf, gc, c, r.x + 10, ly, ascii(s, maxc));
                    ly += lh;
                };
                if (lb && (hunter || n.type == "wallet" || n.type == "ctrl")) {
                    char line[128];
                    snprintf(line, sizeof(line), "%s  eq %s  pos %d  SL -%.0f%%",
                             lb->running ? "LIVE" : "DOWN", money(lb->equity).c_str(), lb->n_pos, lb->sl_roe);
                    put(lb->running ? C.green : C.rose, line);
                    if (exp || fat) {
                        put(C.ab_muted, short_addr(lb->address));
                        snprintf(line, sizeof(line), "leftover %s   uPnL %s",
                                 money(lb->free_usd).c_str(), money(lb->upnl).c_str());
                        put(C.ab_text, line);
                        double roe = 0;
                        std::string rc;
                        if (bot_live_roe(*lb, ui.snap, roe, rc)) {
                            snprintf(line, sizeof(line), "clip %s  ROE %+.1f%%  (SL -%.0f%%)",
                                     rc.c_str(), roe, lb->sl_roe);
                            put(roe < 0 ? C.rose : C.green, line);
                        } else {
                            put(C.ab_muted, "clip  flat");
                        }
                        std::string bs;
                        for (auto& kv : lb->bias) {
                            if (!bs.empty()) bs += "  ";
                            bs += kv.first + "=" + kv.second;
                        }
                        if (!bs.empty()) put(C.ab_text, bs);
                        int shown = 0;
                        int cap = (lb->id == "hummingbot-hl") ? 8 : 3;
                        for (auto& p : ui.snap.positions) {
                            if (p.bot_id != lb->id || shown >= cap) continue;
                            snprintf(line, sizeof(line), "%s %s  %s  ROE %+.1f%%",
                                     p.coin.c_str(), p.side.c_str(), money(p.pnl).c_str(), p.roe);
                            put(pos_pnl_col(C, p), line);
                            shown++;
                        }
                        if (!lb->logs.empty()) put(C.ab_muted, lb->logs.back());
                        if (!lb->next_sell.empty()) put(C.rose, "sell  " + lb->next_sell);
                    }
                } else if (n.type == "bias" || n.type == "desk_bias") {
                    std::string bs;
                    auto bias = n.bias;
                    if (lb) bias = lb->bias;
                    if (n.type == "desk_bias") bs = "desk " + (ui.snap.desk_bias.bias.empty() ? std::string("-") : ui.snap.desk_bias.bias);
                    else {
                        for (auto& kv : bias) {
                            if (!bs.empty()) bs += "  ";
                            bs += kv.first + "=" + kv.second;
                        }
                    }
                    put(C.ab_text, bs.empty() ? "BIAS" : bs);
                    if (exp && lb) {
                        put(C.ab_muted, "wire into hunter BIAS port");
                        put(C.ab_muted, short_addr(lb->address));
                    }
                } else if (n.type == "coin") {
                    auto it = ui.snap.mids.find(n.label);
                    char line[64];
                    if (it != ui.snap.mids.end()) snprintf(line, sizeof(line), "mid %.4g", it->second);
                    else snprintf(line, sizeof(line), "%s", n.label.c_str());
                    put(C.ab_text, line);
                    if (exp) {
                        int np = 0;
                        for (auto& p : ui.snap.positions) {
                            if (p.coin != n.label) continue;
                            char row[80];
                            snprintf(row, sizeof(row), "%s %s  ROE %+.1f%%",
                                     ascii(p.bot, 12).c_str(), p.side.c_str(), p.roe);
                            put(pos_pnl_col(C, p), row);
                            np++;
                            if (np >= 4) break;
                        }
                        if (!np) put(C.ab_muted, "no open clip on this coin");
                    }
                } else if (n.type == "trader") {
                    std::string fol = "leader";
                    for (auto& L : ui.snap.leaders) if (L.id == n.id) fol = L.followed ? "FOLLOW" : "off";
                    put(C.ab_text, fol);
                    if (exp) {
                        put(C.ab_muted, "wire into Copy T1 / T2 / T3");
                        put(C.ab_muted, n.id);
                    }
                } else if (n.type == "gauge") {
                    put(C.ab_text, ui.snap.condor.regime + " / " + ui.snap.condor.source);
                    if (exp) {
                        put(C.ab_muted, ui.snap.condor.why.empty() ? "condor gauge" : ui.snap.condor.why);
                        put(C.ab_muted, ui.snap.condor.telegram_ok ? "telegram ok" : "telegram off");
                    }
                } else if (n.type == "pct") {
                    put(C.ab_text, n.kind == "moon" ? "MOON / TP" : "SL ROE");
                    if (exp) put(C.ab_muted, "wire into hunter SL or MOON port");
                } else if (n.type == "risk_wallet") {
                    if (n.skim_mode == "two_wins") put(C.rose, "every 2nd win");
                    else if (!n.skim_mode.empty()) put(C.ab_text, n.skim_mode);
                    else put(C.ab_muted, "set skim mode");
                    if (!n.address.empty()) put(C.gold, short_addr(n.address));
                    else put(C.rose, "no dest 0x");
                    if (exp) put(C.ab_muted, n.armed ? "armed" : "park only until Copy flush");
                } else if (n.type == "health") {
                    char line[80];
                    snprintf(line, sizeof(line), "CPU %.0f%%  RAM %.0f%%  disk %.0f%%",
                             ui.snap.host.cpu_pct, ui.snap.host.mem_pct, ui.snap.host.disk_pct);
                    put(C.ab_text, line);
                    if (exp) {
                        snprintf(line, sizeof(line), "HL IP %.0f / %.0f", ui.snap.rate.used, ui.snap.rate.cap);
                        put(C.gold, line);
                    }
                } else if (nh > 36) {
                    put(C.ab_muted, n.kind.empty() ? n.type : n.kind);
                    if (exp) put(C.ab_muted, "click another node to collapse this one");
                }
                if (exp && line_ok(0))
                    put(C.ab_muted, "click canvas to shrink  ·  ports stay for wiring");
                hit_on_canvas(Hit::WireNode, i, r);
                if (ui.zoom >= 0.18 || fat || exp) {
                    for (auto& p : layout_ports(n, r)) {
                        unsigned long pc = handle_col(C, p.handle);
                        fill(dpy, buf, gc, p.source ? pc : C.ab_bg, p.r);
                        stroke(dpy, buf, gc, pc, p.r);
                        if (ui.zoom >= 0.32 || fat || exp) {
                            std::string lab = port_tag_text(p.handle);
                            int fw = static_cast<int>(lab.size()) * CHAR_W + 6;
                            int fh = 13;
                            bool left = p.cx <= r.x + 3;
                            bool right = p.cx >= r.x + r.w - 3;
                            bool top = p.cy <= r.y + 3;
                            bool bottom = p.cy >= r.y + r.h - 3;
                            int gap = (hunter && bottom) ? 10 : 5;
                            int ax, ay;
                            if (bottom) {
                                ax = p.cx - fw / 2;
                                ay = p.r.y + p.r.h + gap;
                            } else if (top) {
                                ax = p.cx - fw / 2;
                                ay = p.r.y - fh - gap;
                            } else if (right) {
                                ax = p.r.x + p.r.w + 4;
                                ay = p.cy - fh / 2;
                            } else if (left) {
                                ax = p.r.x - fw - 4;
                                ay = p.cy - fh / 2;
                            } else {
                                ax = p.r.x + p.r.w + 4;
                                ay = p.cy - fh / 2;
                            }
                            draw_port_tag(dpy, buf, gc, C.ab_bg, pc, ax, ay, lab);
                        }
                        hit_on_canvas(Hit::WirePort, i, p.r, p.handle, p.source);
                    }
                }
            }
            for (int ei = 0; ei < static_cast<int>(ui.snap.wire_edges.size()); ++ei)
                draw_edge(ei, false);
            if (ui.wire_from >= 0 && ui.wire_from < static_cast<int>(ui.snap.wire_nodes.size())) {
                auto& n = ui.snap.wire_nodes[ui.wire_from];
                bool exp = false;
                Rect nr = node_view_box(n, exp, ui.zoom, sx(n.x), sy(n.y));
                int x1 = nr.x + nr.w / 2, y1 = nr.y + nr.h / 2;
                find_port_xy(n, nr, ui.wire_handle, ui.wire_from_src, x1, y1);
                int dx1 = 0, dy1 = 0;
                port_stub(nr, x1, y1, dx1, dy1);
                XSetForeground(dpy, gc, C.ab_orange);
                XDrawLine(dpy, buf, gc, x1, y1, x1 + dx1, y1 + dy1);
                XDrawLine(dpy, buf, gc, x1 + dx1, y1 + dy1, ui.mx_last, ui.my_last);
                XFillArc(dpy, buf, gc, x1 - 3, y1 - 3, 6, 6, 0, 360 * 64);
            }
            if (ui.marquee_on && ui.marquee_r.w > 2 && ui.marquee_r.h > 2) {
                XSetForeground(dpy, gc, C.gold);
                XSetLineAttributes(dpy, gc, 1, LineOnOffDash, CapButt, JoinMiter);
                XDrawRectangle(dpy, buf, gc, ui.marquee_r.x, ui.marquee_r.y,
                               std::max(0, ui.marquee_r.w - 1), std::max(0, ui.marquee_r.h - 1));
                XSetLineAttributes(dpy, gc, 1, LineSolid, CapButt, JoinMiter);
            }
            clear_clip(dpy, gc);

            if (ui.tool_drag && ui.tool_drag_i >= 0 && ui.tool_drag_i < static_cast<int>(ui.snap.tools.size())) {
                auto& t = ui.snap.tools[ui.tool_drag_i];
                unsigned long col = parse_hex(dpy, cm, t.color, C.ab_orange);
                bool on_canvas = inside(canvas, ui.mx_last, ui.my_last);
                int under = on_canvas ? ui.node_under_canvas(ui.mx_last, ui.my_last) : -1;
                bool repl = on_canvas && ui.drop_replace_target(t, under) >= 0;
                Rect g{ui.mx_last - 70, ui.my_last - 16, 140, 36};
                fill(dpy, buf, gc, C.ab_clip, g);
                stroke(dpy, buf, gc, repl ? C.gold : (on_canvas ? C.ab_orange : C.rose), g);
                fill(dpy, buf, gc, col, {g.x, g.y, 6, g.h});
                text(dpy, buf, gc, C.ab_text, g.x + 12, g.y + 22, ascii(t.label, 14));
            }

            // Bottom inspector (Ableton detail view)
            fill(dpy, buf, gc, C.ab_panel, insp);
            XSetForeground(dpy, gc, C.ab_orange);
            XDrawLine(dpy, buf, gc, 0, insp.y, W, insp.y);
            const ToolItem* picked = nullptr;
            if (!ui.tool_pick_id.empty()) {
                for (auto& t : ui.snap.tools)
                    if (t.id == ui.tool_pick_id) { picked = &t; break; }
            }
            text(dpy, buf, gc, C.ab_orange, insp.x + 12, insp.y + 16, "DETAIL");
            if (picked && ui.wire_marked.size() <= 1 && !ui.snap.compile.message.empty())
                text(dpy, buf, gc, ui.snap.compile.can_integrate ? C.green : C.amber,
                     insp.x + 80, insp.y + 16, ascii(ui.snap.compile.message, 90));
            else
                text(dpy, buf, gc, C.ab_muted, insp.x + 80, insp.y + 16,
                     ui.wire_marked.size() > 1
                         ? ascii("MARKED " + std::to_string((int)ui.wire_marked.size())
                                 + "  -  drag any marked node to move the group  -  click empty to clear", 90)
                         : (picked
                                ? "drag onto a controller — the window asks before replace"
                                : "Save = boxes/layout only  -  Compile = real wires (confirm)  -  clips untouched"));
            if (!picked && !ui.snap.compile.message.empty())
                text(dpy, buf, gc, ui.snap.compile.can_integrate ? C.green : C.amber,
                     insp.x + 12, insp.y + insp.h - 14, ascii(ui.snap.compile.message, 110));
            if (!ui.snap.wire_error.empty())
                text(dpy, buf, gc, C.rose, insp.x + 520, insp.y + 16, ascii(ui.snap.wire_error, 40));
            if (picked) {
                draw_tool_sheet(dpy, buf, gc, C, insp, *picked, ui);
            } else if (ui.wire_sel >= 0 && ui.wire_sel < static_cast<int>(ui.snap.wire_nodes.size())) {
                auto& n = ui.snap.wire_nodes[ui.wire_sel];
                const Bot* lb = live_bot(ui.snap, n);
                text(dpy, buf, gc, C.ab_orange, insp.x + 12, insp.y + 36, ascii(n.type, 16));
                text(dpy, buf, gc, C.ab_text, insp.x + 120, insp.y + 36, ascii(n.label, 40));
                text(dpy, buf, gc, C.ab_muted, insp.x + 12, insp.y + 54, ascii(n.id, 60));
                if (lb) {
                    char line[160];
                    snprintf(line, sizeof(line), "%s  eq %s  leftover %s  pos %d  SL -%.0f%%  %s",
                             lb->running ? "LIVE" : "DOWN", money(lb->equity).c_str(), money(lb->free_usd).c_str(),
                             lb->n_pos, lb->sl_roe, short_addr(lb->address).c_str());
                    text(dpy, buf, gc, C.ab_text, insp.x + 12, insp.y + 72, line);
                    std::string bias;
                    for (auto& kv : lb->bias) { if (!bias.empty()) bias += "  "; bias += kv.first + "=" + kv.second; }
                    if (!bias.empty()) text(dpy, buf, gc, C.ab_text, insp.x + 12, insp.y + 90, ascii(bias, 80));
                    if (!lb->next_sell.empty())
                        text(dpy, buf, gc, C.rose, insp.x + 12, insp.y + 108, ascii("sell  " + lb->next_sell, 90));
                } else if (n.type == "gauge") {
                    text(dpy, buf, gc, C.ab_text, insp.x + 12, insp.y + 72,
                         ascii("regime " + ui.snap.condor.regime + "  src " + ui.snap.condor.source, 80));
                    text(dpy, buf, gc, C.ab_muted, insp.x + 12, insp.y + 90, ascii(ui.snap.condor.why, 90));
                } else {
                    text(dpy, buf, gc, C.ab_muted, insp.x + 12, insp.y + 72, "right-click to remove  ·  Connect wire then click another clip");
                }
            } else if (ui.wire_from >= 0) {
                text(dpy, buf, gc, C.ab_orange, insp.x + 12, insp.y + 40, "click a target clip to finish the wire  (Esc cancels)");
            } else {
                text(dpy, buf, gc, C.ab_muted, insp.x + 12, insp.y + 40, "drag a controller onto the one on the canvas — the window asks");
            }

            if (ui.ctx_on) {
                int mw = 260, mh = 280;
                int mx0 = std::min(ui.ctx_x, W - mw - 4);
                int my0 = std::min(ui.ctx_y, H - mh - 4);
                if (mx0 < 4) mx0 = 4;
                if (my0 < 4) my0 = 4;
                Rect box{mx0, my0, mw, mh};
                fill(dpy, buf, gc, C.ab_panel, box);
                stroke(dpy, buf, gc, C.ab_orange, box);
                int cy = box.y + 8;
                auto item = [&](int id, const std::string& lab, unsigned long col) {
                    Rect r{box.x + 6, cy, box.w - 12, 18};
                    text(dpy, buf, gc, col, r.x + 6, r.y + 13, ascii(lab, 26));
                    if (id >= 0) ui.hits.push_back({Hit::Ctx, id, r});
                    cy += 18;
                };
                if (ui.ctx_kind == 1 && ui.ctx_i >= 0 && ui.ctx_i < static_cast<int>(ui.snap.wire_nodes.size())) {
                    item(-1, ui.snap.wire_nodes[ui.ctx_i].label, C.ab_orange);
                    item(1, "Remove from canvas", C.rose);
                    item(3, "Connect wire...", C.ab_orange);
                    cy += 4;
                } else if (ui.ctx_kind == 2 && ui.ctx_i >= 0 && ui.ctx_i < static_cast<int>(ui.snap.wire_edges.size())) {
                    item(-1, "wire", C.ab_orange);
                    item(2, "Delete wire", C.rose);
                    cy += 4;
                }
                item(-1, "Add node", C.ab_orange);
                int shown = 0;
                for (int ti = 0; ti < static_cast<int>(ui.snap.tools.size()); ++ti) {
                    if (cy > box.y + box.h - 22) break;
                    if (shown < ui.ctx_scroll) { shown++; continue; }
                    auto& t = ui.snap.tools[ti];
                    bool on = t.unique && find_node(ui.snap, t.id) >= 0;
                    item(100 + ti, (on ? "on  " : "+   ") + t.label, on ? C.ab_muted : C.ab_text);
                    shown++;
                }
                item(0, "close", C.ab_muted);
            }

            if (ui.repl_on) {
                const ToolItem* tool = nullptr;
                for (auto& t : ui.snap.tools)
                    if (t.id == ui.repl_tool_id) { tool = &t; break; }
                int ti = find_node(ui.snap, ui.repl_target_id);
                if (!tool || ti < 0) {
                    ui.repl_on = false;
                } else {
                    auto& old = ui.snap.wire_nodes[ti];
                    int ww = std::min(480, std::max(280, canvas.w - 16));
                    int hh = 132;
                    int x = canvas.x + std::max(4, (canvas.w - ww) / 2);
                    int y = canvas.y + std::max(4, (canvas.h - hh) / 2);
                    Rect box{x, y, ww, hh};
                    ui.repl_win_r = box;
                    fill(dpy, buf, gc, C.ab_bg, box);
                    stroke(dpy, buf, gc, C.gold, box);
                    text(dpy, buf, gc, C.gold, box.x + 16, box.y + 24, "Replace the active controller?");
                    text(dpy, buf, gc, C.ab_text, box.x + 16, box.y + 48,
                         ascii(old.label + " -> " + tool->label, std::max(8, (ww - 32) / CHAR_W)));
                    text(dpy, buf, gc, C.ab_muted, box.x + 16, box.y + 70,
                         "Wallet, log, risk, and the other wires stay.");
                    Rect no{box.x + 16, box.y + box.h - 36, 100, 24};
                    Rect yes{box.x + box.w - 120, box.y + box.h - 36, 104, 24};
                    stroke(dpy, buf, gc, C.ab_muted, no);
                    text(dpy, buf, gc, C.ab_muted, no.x + 22, no.y + 16, "Cancel");
                    stroke(dpy, buf, gc, C.gold, yes);
                    text(dpy, buf, gc, C.gold, yes.x + 18, yes.y + 16, "Replace");
                    ui.hits.push_back({Hit::ReplNo, 0, no});
                    ui.hits.push_back({Hit::ReplYes, 0, yes});
                }
            }
        };

        auto draw_condor = [&]() {
            int y = body.y + 16 - ui.scroll;
            int x = 18;
            text(dpy, buf, gc, C.gold, x, y, "CONDOR");
            text(dpy, buf, gc, C.muted, x + 80, y, "measures BTC/ETH/LTC/TAO  ·  does not trade  ·  tick not wired");
            y += 24;
            struct K { const char* l; std::string v; unsigned long c; };
            K k[] = {
                {"REGIME", ascii(ui.snap.condor.regime, 16), C.cyan},
                {"SOURCE", ascii(ui.snap.condor.source, 16), C.gold},
                {"CONTAINER", ui.snap.condor.running ? "running" : "off", ui.snap.condor.running ? C.green : C.rose},
                {"TELEGRAM", ui.snap.condor.telegram_ok ? "ok" : "off", ui.snap.condor.telegram_ok ? C.green : C.muted},
                {"GROK", ui.snap.condor.grok_ok ? "ok" : "off", ui.snap.condor.grok_ok ? C.green : C.muted},
            };
            for (int i = 0; i < 5; ++i) {
                Rect r{x + i * 210, y, 200, 52};
                fill(dpy, buf, gc, C.panel, r);
                stroke(dpy, buf, gc, C.border, r);
                text(dpy, buf, gc, C.muted, r.x + 10, r.y + 16, k[i].l);
                text(dpy, buf, gc, k[i].c, r.x + 10, r.y + 36, k[i].v);
            }
            y += 68;
            text(dpy, buf, gc, C.gold, x, y, "BIAS (from gauge, not applied here)");
            y += 18;
            for (auto& kv : ui.snap.condor.bias) {
                unsigned long fc = bias_col(C, kv.second);
                Rect chip{x, y, 120, 28};
                stroke(dpy, buf, gc, fc, chip);
                text(dpy, buf, gc, C.muted, chip.x + 8, chip.y + 18, kv.first);
                text(dpy, buf, gc, fc, chip.x + 50, chip.y + 18, kv.second);
                x += 132;
            }
            x = 18;
            y += 44;
            text(dpy, buf, gc, C.muted, x, y, ascii(ui.snap.condor.why, 120));
            y += 20;
            text(dpy, buf, gc, C.muted, x, y, ascii(ui.snap.condor.ts, 40));
            y += 24;
            Rect tick{x, y, 90, 22};
            stroke(dpy, buf, gc, C.gold, tick);
            text(dpy, buf, gc, C.gold, tick.x + 22, tick.y + 15, "Tick");
            ui.hits.push_back({Hit::NotWired, 0, tick});
            text(dpy, buf, gc, C.muted, x + 110, y + 15, "would POST /api/condor/tick  ·  not wired");
            y += 36;
            text(dpy, buf, gc, C.gold, x, y, "FEED");
            y += 18;
            for (auto& line : ui.snap.condor.feed) {
                text(dpy, buf, gc, C.text, x, y, ascii(line, 140));
                y += 16;
            }
            ui.content_h = y + ui.scroll - body.y + 24;
        };

        auto draw_hunters = [&]() {
            const int lw = 300;
            Rect list{12, body.y + 8, lw, body.h - 16};
            fill(dpy, buf, gc, C.bg, list);
            stroke(dpy, buf, gc, C.border, list);
            set_clip(dpy, gc, list);
            int y = list.y + 2;
            if (ui.snap.bots.empty())
                text(dpy, buf, gc, C.rose, list.x + 10, y + 20, "no hunters in snapshot");
            for (int i = 0; i < static_cast<int>(ui.snap.bots.size()); ++i) {
                const int card_h = 76;
                if (y + card_h > list.y + list.h) break;
                auto& b = ui.snap.bots[i];
                Rect r{list.x + 1, y, lw - 2, card_h - 2};
                unsigned long col = parse_hex(dpy, cm, b.color, C.green);
                bool hot = (i == ui.hover_bot);
                unsigned long bgc = hot ? hover_fill_col(dpy, cm, b.color, C.panel)
                                        : (i == ui.sel ? rgb(dpy, cm, 8, 32, 16) : C.panel);
                fill(dpy, buf, gc, bgc, r);
                stroke(dpy, buf, gc, b.deactivated ? C.rose
                        : (hot ? col : (i == ui.sel ? C.gold : C.border)), r);
                fill(dpy, buf, gc, col, {r.x, r.y, 4, r.h});
                XSetForeground(dpy, gc, b.deactivated ? C.rose : (b.running ? C.green : C.rose));
                XFillArc(dpy, buf, gc, r.x + 12, r.y + 8, 10, 10, 0, 360 * 64);
                char slb[16];
                sl_now_label(b.sl_roe, slb, sizeof(slb));
                unsigned long slc = sl_level_col(C, b.sl_roe);
                Rect badge{r.x + r.w - 76, r.y + 6, 68, 16};
                fill(dpy, buf, gc, b.sl_roe <= 25 ? rgb(dpy, cm, 8, 36, 14)
                                 : (b.sl_roe <= 45 ? rgb(dpy, cm, 48, 28, 8) : rgb(dpy, cm, 50, 10, 18)), badge);
                stroke(dpy, buf, gc, slc, badge);
                text(dpy, buf, gc, slc, badge.x + 6, badge.y + 12, std::string("SL ") + slb);
                text(dpy, buf, gc, col, r.x + 28, r.y + 18, fit_px(b.name, badge.x - (r.x + 32)));
                double live_roe = 0;
                std::string live_coin;
                bool has_roe = bot_live_roe(b, ui.snap, live_roe, live_coin);
                char eline[80];
                if (has_roe)
                    snprintf(eline, sizeof(eline), "%s  ROE %+.1f%%", money(b.equity).c_str(), live_roe);
                else
                    snprintf(eline, sizeof(eline), "%s   pos %d", money(b.equity).c_str(), b.n_pos);
                text(dpy, buf, gc, has_roe && live_roe < 0 ? C.rose : C.cyan, r.x + 28, r.y + 40,
                     fit_px(eline, r.w - 36));
                ui.hits.push_back({Hit::Hunter, i, r});
                Rect lch{r.x + 28, r.y + 50, 62, 18};
                if (lch.x + lch.w <= r.x + r.w - 4) {
                    stroke(dpy, buf, gc, C.gold, lch);
                    text(dpy, buf, gc, C.gold, lch.x + 8, lch.y + 13, "Chart");
                    ui.hits.push_back({Hit::Chart, i, lch, follow_coin(b, ui.snap)});
                }
                y += card_h;
            }
            clear_clip(dpy, gc);

            Rect main{12 + lw + 10, body.y + 8, std::max(220, W - (lw + 34)), body.h - 16};
            fill(dpy, buf, gc, C.panel, main);
            stroke(dpy, buf, gc, C.border, main);
            Bot* b = ui.selected();
            if (!b) {
                text(dpy, buf, gc, C.muted, main.x + 16, main.y + 24, "no hunter selected");
                ui.content_h = 0;
                return;
            }
            set_clip(dpy, gc, main);
            unsigned long col = parse_hex(dpy, cm, b->color, C.green);
            int x = main.x + 14;
            int right = main.x + main.w - 12;
            int inner = std::max(80, right - x);
            int yy = main.y + 20;

            const char* health = b->deactivated ? "0 HL IP" : (b->ok ? "HEALTH ok" : "HEALTH issue");
            const char* run = b->deactivated ? "DEACTIVATED" : (b->running ? "ACTIVE" : "OFF");
            int health_w = static_cast<int>(strlen(health)) * CHAR_W;
            int run_w = static_cast<int>(strlen(run)) * CHAR_W;
            unsigned long run_c = b->deactivated ? C.rose : (b->running ? C.green : C.rose);
            text(dpy, buf, gc, col, x, yy, fit_px(b->name, inner - health_w - run_w - 24));
            text(dpy, buf, gc, b->deactivated ? C.rose : (b->ok ? C.green : C.rose),
                 right - run_w - health_w - 8, yy, health);
            text(dpy, buf, gc, run_c, right - run_w, yy, run);
            {
                std::string fc = follow_coin(*b, ui.snap);
                Rect hch{right - run_w - health_w - 8 - 70, yy - 14, 62, 18};
                if (hch.x > x + 80) {
                    stroke(dpy, buf, gc, C.gold, hch);
                    text(dpy, buf, gc, C.gold, hch.x + 8, hch.y + 13, "Chart");
                    ui.hits.push_back({Hit::Chart, ui.sel, hch, fc});
                }
            }
            yy += 18;
            text(dpy, buf, gc, C.muted, x, yy, fit_px(short_addr(b->address) + "  " + b->href, inner));
            yy += 16;
            if (b->deactivated) {
                Rect ban{x, yy, inner, 34};
                fill(dpy, buf, gc, rgb(dpy, cm, 50, 10, 18), ban);
                stroke(dpy, buf, gc, C.rose, ban);
                text(dpy, buf, gc, C.rose, ban.x + 8, ban.y + 14,
                     fit_px("DEACTIVATED - red - 0 Hyperliquid IP tokens", inner - 16));
                text(dpy, buf, gc, C.rose, ban.x + 8, ban.y + 28,
                     fit_px("Spring does not touch positions. Stops wait until ACTIVE.", inner - 16));
                yy += 40;
            }
            {
                bool de = b->deactivated;
                Rect gate{x, yy, 132, 22};
                draw_ip_btn(dpy, buf, gc, C, gate, de, b->running, ui.hits, ui.sel);
                const char* note = de
                    ? "red - 0 HL IP. Spring does not touch positions."
                    : (b->running
                        ? "green - active. Spring does not touch positions."
                        : "off - container is down. Spring does not touch positions.");
                text(dpy, buf, gc, de ? C.rose : (b->running ? C.green : C.muted),
                     gate.x + gate.w + 10, gate.y + 15,
                     fit_px(note, inner - gate.w - 12));
                yy += 30;
            }

            struct Kpi { const char* l; std::string v; unsigned long c; };
            Kpi kpis[] = {
                {"EQUITY", money(b->equity), C.gold},
                {"LEFTOVER", money(b->free_usd), C.cyan},
                {"uPnL", money(b->upnl), b->upnl < 0 ? C.rose : C.green},
                {"POS", std::to_string(b->n_pos), C.text},
            };
            int nbox = inner >= 520 ? 4 : 2;
            int gap = 8;
            int bw = std::max(90, (inner - (nbox - 1) * gap) / nbox);
            int bh = 38;
            for (int i = 0; i < 4; ++i) {
                int coln = i % nbox;
                int rown = i / nbox;
                Rect kr{x + coln * (bw + gap), yy + rown * (bh + 6), bw, bh};
                fill(dpy, buf, gc, C.bg, kr);
                stroke(dpy, buf, gc, C.border, kr);
                text(dpy, buf, gc, C.muted, kr.x + 8, kr.y + 14, kpis[i].l);
                text(dpy, buf, gc, kpis[i].c, kr.x + 8, kr.y + 30, fit_px(kpis[i].v, kr.w - 14));
            }
            yy += ((4 + nbox - 1) / nbox) * (bh + 6) + 8;

            text(dpy, buf, gc, C.rose, x, yy,
                 fit_px("sell   " + (b->next_sell.empty() ? std::string("-") : b->next_sell), inner));
            yy += 16;
            text(dpy, buf, gc, C.cyan, x, yy,
                 fit_px("rebuy  " + (b->next_rebuy.empty() ? std::string("-") : b->next_rebuy), inner));
            yy += 18;

            if (shows_lev_row(b->id)) {
                draw_lev_strip(dpy, buf, gc, C, x, yy, *b, &ui.hits, ui.sel, &ui.hover_r);
                yy += 26;
            }
            Rect slband{x, yy, std::min(inner, 640), 28};
            unsigned long slc = sl_level_col(C, b->sl_roe);
            fill(dpy, buf, gc, b->sl_roe <= 25 ? rgb(dpy, cm, 8, 36, 14)
                             : (b->sl_roe <= 45 ? rgb(dpy, cm, 48, 28, 8) : rgb(dpy, cm, 40, 8, 14)), slband);
            stroke(dpy, buf, gc, slc, slband);
            set_clip(dpy, gc, slband);
            double live_roe = 0;
            std::string live_coin;
            bool has_roe = bot_live_roe(*b, ui.snap, live_roe, live_coin);
            draw_sl_strip(dpy, buf, gc, C, slband.x + 8, slband.y + 4, b->sl_roe, &ui.hits, ui.sel,
                          live_roe, has_roe, &ui.hover_r);
            set_clip(dpy, gc, main);
            yy += slband.h + 12;

            text(dpy, buf, gc, C.gold, x, yy, "BIAS");
            yy += 18;
            std::vector<std::string> coins = b->coins;
            if (coins.empty()) {
                for (auto& kv : b->bias) coins.push_back(kv.first);
            }
            std::string mode = b->bias_mode.empty() ? "both" : b->bias_mode;
            if (coins.empty()) {
                text(dpy, buf, gc, C.muted, x, yy, "no BIAS rail");
                yy += 18;
            } else {
                for (auto& coin : coins) {
                    if (yy + 24 > main.y + main.h - 8) break;
                    std::string cur = b->bias.count(coin) ? b->bias.at(coin) : "FLAT";
                    text(dpy, buf, gc, C.muted, x, yy + 14, fit_px(coin, 40));
                    const char* modes[] = {"LONG", "FLAT", "SHORT"};
                    int btn_w = 66;
                    int btn_gap = 8;
                    int bx0 = x + 48;
                    for (int m = 0; m < 3; ++m) {
                        bool allowed = !(mode == "long" && m == 2) && !(mode == "short" && m == 0);
                        Rect br{bx0 + m * (btn_w + btn_gap), yy, btn_w, 20};
                        if (br.x + br.w > right) break;
                        bool on = allowed && cur == modes[m];
                        unsigned long fc = bias_col(C, modes[m]);
                        fill(dpy, buf, gc, on ? fc : C.bg, br);
                        stroke(dpy, buf, gc, allowed ? fc : C.border, br);
                        int tx = br.x + (br.w - static_cast<int>(strlen(modes[m])) * CHAR_W) / 2;
                        text(dpy, buf, gc, on ? C.bg : (allowed ? fc : C.muted), tx, br.y + 14, modes[m]);
                        if (allowed)
                            ui.hits.push_back({Hit::CoinBias, ui.sel, br, coin + "|" + std::string(modes[m])});
                    }
                    Rect cl{bx0 + 3 * (btn_w + btn_gap), yy, 56, 20};
                    Rect op{cl.x + 64, yy, 48, 20};
                    if (cl.x + cl.w <= right) {
                        stroke(dpy, buf, gc, C.rose, cl);
                        text(dpy, buf, gc, C.rose, cl.x + 8, cl.y + 14, "Close");
                        ui.hits.push_back({Hit::CoinClose, ui.sel, cl, coin});
                    }
                    if (op.x + op.w <= right) {
                        stroke(dpy, buf, gc, C.cyan, op);
                        text(dpy, buf, gc, C.cyan, op.x + 8, op.y + 14, "100%");
                        ui.hits.push_back({Hit::CoinOpen100, ui.sel, op, coin});
                    }
                    Rect chb{op.x + 56, yy, 52, 20};
                    if (chb.x + chb.w <= right) {
                        stroke(dpy, buf, gc, C.gold, chb);
                        text(dpy, buf, gc, C.gold, chb.x + 6, chb.y + 14, "Chart");
                        ui.hits.push_back({Hit::Chart, ui.sel, chb, coin});
                        if (shows_lev_row(b->id)) {
                            int fx_x = chb.x + chb.w + 8;
                            if (fx_x < right - 18) {
                                std::string fx = lev_effect_text(ui.snap, *b, coin);
                                text(dpy, buf, gc, lev_tier_col(C, lev_tier_of(*b)), fx_x, chb.y + 14,
                                     fit_px(fx, right - fx_x));
                            }
                        }
                    }
                    yy += 26;
                }
            }

            yy += 6;
            text(dpy, buf, gc, C.gold, x, yy, "OPEN POSITIONS");
            yy += 18;
            int shown_pos = 0;
            for (auto& p : ui.snap.positions) {
                if (p.bot_id != b->id && p.bot != b->name) continue;
                if (yy + 36 > main.y + main.h - 8) break;
                unsigned long pc = pos_pnl_col(C, p);
                char l1[80], l2[96];
                snprintf(l1, sizeof(l1), "%s  %s   sz %.4g   %dx",
                         p.coin.c_str(), p.side.c_str(), p.size, p.leverage);
                snprintf(l2, sizeof(l2), "en %.4g   mk %.4g   %s   %s",
                         p.entry, p.mark, pct_s(p.roe).c_str(), money(p.pnl).c_str());
                text(dpy, buf, gc, pc, x, yy, fit_px(l1, inner - 72));
                Rect cl{right - 60, yy - 2, 56, 20};
                stroke(dpy, buf, gc, C.rose, cl);
                text(dpy, buf, gc, C.rose, cl.x + 8, cl.y + 14, "Close");
                ui.hits.push_back({Hit::CoinClose, ui.sel, cl, p.coin});
                yy += 14;
                text(dpy, buf, gc, pc, x + 16, yy, fit_px(l2, inner - 16));
                yy += 16;
                shown_pos++;
            }
            if (!shown_pos) {
                text(dpy, buf, gc, C.muted, x, yy, "none");
                yy += 16;
            }
            yy += 8;

            if (b->id == "hummingbot-hl" || !b->copy_traders.empty()) {
                text(dpy, buf, gc, C.gold, x, yy, "FOLLOWING");
                Rect abtn{right - 76, yy - 14, 72, 20};
                stroke(dpy, buf, gc, C.gold, abtn);
                text(dpy, buf, gc, C.gold, abtn.x + 14, abtn.y + 14, "AUDIT");
                ui.hits.push_back({Hit::CopyAudit, 0, abtn});
                yy += 16;
                text(dpy, buf, gc, C.ab_muted, x, yy,
                     "Mute flattens that trader's clips  ·  1/2/3 = max trades we follow");
                yy += 16;
                if (b->copy_traders.empty()) {
                    text(dpy, buf, gc, C.muted, x, yy, "no leaders in copy yaml");
                    yy += 16;
                }
                for (auto& t : b->copy_traders) {
                    if (yy + 48 > main.y + main.h - 8) break;
                    char head[96];
                    std::string coins;
                    for (size_t ci = 0; ci < t.coins.size(); ++ci) {
                        if (ci) coins += " · ";
                        coins += t.coins[ci];
                    }
                    if (coins.empty()) coins = "flat";
                    snprintf(head, sizeof(head), "T#%d  %s  %s  %d pos%s",
                             t.num, t.short_addr.c_str(), coins.c_str(), t.n_pos,
                             t.muted ? "  MUTED" : "");
                    text(dpy, buf, gc, t.muted ? C.rose : C.cyan, x, yy + 12,
                         fit_px(head, inner - 200));
                    Rect mb{right - 76, yy, 72, 22};
                    stroke(dpy, buf, gc, t.muted ? C.green : C.amber, mb);
                    text(dpy, buf, gc, t.muted ? C.green : C.amber, mb.x + 8, mb.y + 15,
                         t.muted ? "Unmute" : "Mute");
                    ui.hits.push_back({Hit::CopyMute, t.num, mb, t.muted ? "unmute" : "mute"});
                    yy += 24;
                    text(dpy, buf, gc, C.ab_muted, x, yy + 14, "follow max");
                    int sx = x + 88;
                    for (int s = 1; s <= 3; ++s) {
                        Rect sr{sx + (s - 1) * 36, yy, 32, 20};
                        bool on = (t.follow_slots == s);
                        fill(dpy, buf, gc, on ? C.gold : C.bg, sr);
                        stroke(dpy, buf, gc, C.gold, sr);
                        char lb[4];
                        snprintf(lb, sizeof(lb), "%d", s);
                        text(dpy, buf, gc, on ? C.bg : C.gold, sr.x + 12, sr.y + 14, lb);
                        ui.hits.push_back({Hit::CopySlots, t.num, sr, std::to_string(s)});
                    }
                    yy += 24;
                }
                yy += 8;
            }

            bool pmm_knobs = (b->id == "hummingbot-bn-swing");
            if (true) {
            if (pmm_knobs) {
                bool open_now = false;
                if (b->controller.contains("open_on_bias")) {
                    auto v = b->controller["open_on_bias"];
                    open_now = v.is_boolean() ? v.get<bool>()
                        : (v.is_number() && v.get<double>() != 0);
                }
                Rect brow{x, yy, std::min(inner, 520), 24};
                Rect ck{brow.x, brow.y + 4, 16, 16};
                stroke(dpy, buf, gc, C.gold, ck);
                if (open_now) fill(dpy, buf, gc, C.gold, {ck.x + 3, ck.y + 3, 10, 10});
                text(dpy, buf, gc, open_now ? C.gold : C.ab_muted, ck.x + 24, brow.y + 16,
                     "Open on BIAS   market fill now when LONG/SHORT   skip NEXT IN wait");
                ui.hits.push_back({Hit::CtrlBool, ui.sel, brow, "open_on_bias"});
                yy += 28;
                text(dpy, buf, gc, C.gold, x, yy, "CONTROLLER  ·  Ableton knobs  ·  SCALE x writes all  ·  chart follows");
                yy += 16;
                text(dpy, buf, gc, C.ab_muted, x, yy,
                     "Trail % of ARM  ·  double-click type  ·  arrows  ·  red ring = invalid geometry");
                yy += 16;
                } else {
                text(dpy, buf, gc, C.gold, x, yy, "CONTROLLER  ·  Ableton knobs  ·  chart follows");
                yy += 16;
                text(dpy, buf, gc, C.ab_muted, x, yy,
                     "double-click type  ·  arrows  ·  SAVE per this hunter only  ·  STD = standard");
                yy += 16;
                }
                auto fields = ui.fields_for(*b);
                if (!pmm_knobs) {
                    for (auto& f : fields) {
                        if (f.key != "sendEvery2Win") continue;
                        bool on = ctrl_flag(b->controller, f.key);
                        int bw = std::min(inner, 460);
                        Rect brow{x, yy, bw, 22};
                        Rect ck{brow.x, brow.y + 3, 16, 16};
                        stroke(dpy, buf, gc, C.gold, ck);
                        if (on) fill(dpy, buf, gc, C.gold, {ck.x + 3, ck.y + 3, 10, 10});
                        text(dpy, buf, gc, on ? C.gold : C.ab_muted, ck.x + 22, brow.y + 16,
                             fit_px(f.label, bw - 28));
                        ui.hits.push_back({Hit::CtrlBool, ui.sel, brow, f.key});
                        yy += 26;
                        break;
                    }
                }
                const int cellw = 124;
                const int cellh = 132;
                const int kr = 32;
                int kx = x;
                int ky = yy;
                int next_in_kx = -1;
                unsigned long kcol = parse_hex(dpy, cm, b->color, C.green);
                auto pmm_primary = [](const std::string& k) {
                    return k == "scale_x" || k == "take_profit"
                        || k == "rebuy_pct" || k == "margin_pct";
                };
                auto draw_pmm_knob = [&](CtrlField& f) {
                    if (f.key == "close_px") return;
                    if (!pmm_knobs && kx + cellw > right) { kx = x; ky += cellh; }
                    auto stored = b->controller.contains(f.key) ? b->controller[f.key] : nlohmann::json();
                    double dv = display_value(f, stored);
                    if (f.key == "trail_delta") {
                        PmmDisp pd = pmm_read(*b);
                        dv = pmm_trail_display(dv, pd.arm);
                    }
                    if ((ui.knob_focus_bot == ui.sel && ui.knob_focus_key == f.key)
                        || (ui.knob_drag && ui.knob_bot == ui.sel && ui.knob_key == f.key))
                        dv = ui.knob_live;
                    double span = (f.maxv > f.minv) ? (f.maxv - f.minv) : 1;
                    double t = (dv - f.minv) / span;
                    int kcx = kx + cellw / 2;
                    int kcy = ky + 42;
                    bool pressed = ui.knob_drag && ui.knob_bot == ui.sel && ui.knob_key == f.key;
                    bool hover = !pressed && inside({kx, ky, cellw, cellh - 8}, ui.mx_last, ui.my_last);
                    unsigned long face = 0;
                    if (pressed) face = C.gold;
                    else if (hover) face = C.green;
                    Rect dial = draw_knob(dpy, buf, gc, C, kcx, kcy, kr, t,
                                          pressed ? C.gold : kcol, face);
                    char mn[24], mx[24];
                    fmt_knob_lim(f, f.minv, mn, sizeof(mn));
                    fmt_knob_lim(f, f.maxv, mx, sizeof(mx));
                    int mnw = static_cast<int>(std::strlen(mn)) * TINY_W;
                    int mxw = static_cast<int>(std::strlen(mx)) * TINY_W;
                    text_font(dpy, buf, gc, fs_tiny, font, C.ab_muted,
                              kx + 2, kcy + 3, mn);
                    text_font(dpy, buf, gc, fs_tiny, font, C.ab_muted,
                              kx + cellw - 4 - mxw, kcy + 3, mx);
                    (void)mnw;
                    PmmDisp geo = pmm_read(*b);
                    if (ui.knob_drag && ui.knob_bot == ui.sel) {
                        if (ui.knob_key == "take_profit") geo.tp = ui.knob_live;
                        if (ui.knob_key == "trail_activation") geo.arm = ui.knob_live;
                        if (ui.knob_key == "trail_delta") geo.trail = ui.knob_live;
                        if (ui.knob_key == "rebuy_pct") geo.next = ui.knob_live;
                        if (ui.knob_key == "margin_pct") geo.margin = ui.knob_live;
                    }
                    bool bad = false;
                    if (f.key == "trail_activation" && geo.arm > geo.tp + 1e-9) bad = true;
                    if (f.key == "take_profit" && geo.arm > geo.tp + 1e-9) bad = true;
                    if ((f.key == "trail_delta" || f.key == "rebuy_pct")
                        && pmm_price_delta(geo) + 1e-9 < geo.next) bad = true;
                    if (f.key == "margin_pct" && geo.margin >= 99.5 && b->equity > 0
                        && b->free_usd / b->equity <= 0.30) bad = true;
                    bool foc = (ui.knob_focus_bot == ui.sel && ui.knob_focus_key == f.key);
                    if (foc || bad) {
                        Rect ring{dial.x - 3, dial.y - 3, dial.w + 6, dial.h + 6};
                        stroke(dpy, buf, gc, bad ? C.rose : C.gold, ring);
                    }
                    bool editing = ui.knob_edit && ui.knob_edit_bot == ui.sel
                        && ui.knob_edit_key == f.key;
                    if (editing) {
                        Rect box{kcx - 42, kcy + kr + 4, 84, 18};
                        fill(dpy, buf, gc, C.navy, box);
                        stroke(dpy, buf, gc, C.gold, box);
                        if (ui.knob_edit_sel)
                            fill(dpy, buf, gc, C.gold, {box.x + 2, box.y + 2, box.w - 4, box.h - 4});
                        std::string show = ui.knob_edit_buf;
                        if (!ui.knob_edit_sel) show += "|";
                        int tw = static_cast<int>(show.size()) * CHAR_W;
                        text(dpy, buf, gc, ui.knob_edit_sel ? C.bg : C.green,
                             kcx - tw / 2, box.y + 13, ascii(show, 8));
                    } else {
                        char vb[32];
                        if (f.kind == "price_pct" || f.key == "scale_x") snprintf(vb, sizeof(vb), "%.2f", dv);
                        else if (foc || f.kind == "pct_frac") snprintf(vb, sizeof(vb), "%.2f", dv);
                        else if (f.step < 1) snprintf(vb, sizeof(vb), "%.1f", dv);
                        else snprintf(vb, sizeof(vb), "%.0f", dv);
                        int tw = static_cast<int>(std::strlen(vb)) * CHAR_W;
                        text(dpy, buf, gc, C.green, kcx - tw / 2, kcy + kr + 16, vb);
                    }
                    text(dpy, buf, gc, C.ab_muted, kx + 4, kcy + kr + 32, ascii(f.label, cellw / CHAR_W));
                    if (f.key == "rebuy_pct") next_in_kx = kx;
                    Rect khit{kx, ky, cellw, cellh - 8};
                    ui.hits.push_back({Hit::CtrlKnob, ui.sel, khit, f.key});
                    (void)dial;
                    kx += cellw;
                };
                for (auto& f : fields) {
                    if (f.kind == "bool" || f.kind == "select") continue;
                    if (pmm_knobs && !pmm_primary(f.key)) continue;
                    draw_pmm_knob(f);
                }
                if (pmm_knobs && next_in_kx >= 0) {
                    bool gon = false;
                    if (b->controller.contains("go_in_after_close")) {
                        auto v = b->controller["go_in_after_close"];
                        gon = v.is_boolean() ? v.get<bool>()
                            : (v.is_number() && v.get<double>() != 0);
                    }
                    Rect brow{next_in_kx, ky + cellh - 6, cellw + 80, 20};
                    Rect ck{brow.x, brow.y + 2, 14, 14};
                    stroke(dpy, buf, gc, C.gold, ck);
                    if (gon) fill(dpy, buf, gc, C.gold, {ck.x + 3, ck.y + 3, 8, 8});
                    text(dpy, buf, gc, gon ? C.gold : C.ab_muted, ck.x + 20, brow.y + 14,
                         "after close  100% BIAS");
                    ui.hits.push_back({Hit::CtrlBool, ui.sel, brow, "go_in_after_close"});
                }
                if (pmm_knobs) {
                    kx = x;
                    ky += cellh + 22;
                    for (auto& f : fields) {
                        if (f.kind == "bool" || f.kind == "select") continue;
                        if (pmm_primary(f.key)) continue;
                        draw_pmm_knob(f);
                    }
                } else {
                    int byy = ky + cellh + 4;
                    int bx = x;
                    for (auto& f : fields) {
                        if (f.kind != "bool") continue;
                        if (f.key == "sendEvery2Win") continue;
                        bool on = ctrl_flag(b->controller, f.key);
                        Rect brow{bx, byy, 200, 20};
                        Rect ck{brow.x, brow.y + 3, 14, 14};
                        stroke(dpy, buf, gc, C.gold, ck);
                        if (on) fill(dpy, buf, gc, C.gold, {ck.x + 3, ck.y + 3, 8, 8});
                        text(dpy, buf, gc, on ? C.gold : C.ab_muted, ck.x + 20, brow.y + 14,
                             ascii(f.label, 20));
                        ui.hits.push_back({Hit::CtrlBool, ui.sel, brow, f.key});
                        bx += 210;
                        if (bx + 200 > right) { bx = x; byy += 22; }
                    }
                    ky = byy + 8;
                    kx = x;
                }
                {
                    CtrlField* cpx = nullptr;
                    for (auto& f : fields)
                        if (f.key == "close_px") { cpx = &f; break; }
                    if (cpx) {
                        auto stored = b->controller.contains("close_px")
                            ? b->controller["close_px"] : nlohmann::json();
                        double dv = display_value(*cpx, stored);
                        if (ui.knob_focus_bot == ui.sel && ui.knob_focus_key == "close_px"
                            && ui.knob_live >= cpx->minv)
                            dv = ui.knob_live;
                        int bw = std::min(std::max(inner, 360), 580);
                        Rect box{x, ky + 4, bw, 58};
                        fill(dpy, buf, gc, C.navy, box);
                        stroke(dpy, buf, gc, dv > 0 ? C.gold : C.ab_muted, box);
                        text(dpy, buf, gc, C.gold, box.x + 12, box.y + 20, "Close $");
                        text(dpy, buf, gc, dv > 0 ? C.gold : C.ab_muted, box.x + 88, box.y + 20,
                             dv > 0 ? "moon ON" : "moon OFF");
                        Rect val{box.x + 188, box.y + 8, 148, 26};
                        fill(dpy, buf, gc, C.bg, val);
                        stroke(dpy, buf, gc, C.gold, val);
                        bool editing = ui.knob_edit && ui.knob_edit_bot == ui.sel
                            && ui.knob_edit_key == "close_px";
                        if (editing) {
                            if (ui.knob_edit_sel)
                                fill(dpy, buf, gc, C.gold, {val.x + 2, val.y + 2, val.w - 4, val.h - 4});
                            std::string show = ui.knob_edit_buf;
                            if (!ui.knob_edit_sel) show += "|";
                            text(dpy, buf, gc, ui.knob_edit_sel ? C.bg : C.green,
                                 val.x + 10, val.y + 18, ascii(show, 14));
                        } else {
                            char vb[32];
                            snprintf(vb, sizeof(vb), "%.2f", dv);
                            text(dpy, buf, gc, C.green, val.x + 10, val.y + 18, vb);
                        }
                        Rect off{val.x + 156, box.y + 8, 52, 26};
                        fill(dpy, buf, gc, C.bg, off);
                        stroke(dpy, buf, gc, C.rose, off);
                        text(dpy, buf, gc, C.rose, off.x + 10, off.y + 18, "OFF");
                        text(dpy, buf, gc, C.ab_muted, box.x + 12, box.y + 46,
                             "dbl-click the $ box  ·  Enter set  ·  0 or OFF = moon off, clip stays");
                        ui.hits.push_back({Hit::CtrlKnob, ui.sel, off, "moon_off"});
                        ui.hits.push_back({Hit::CtrlKnob, ui.sel, val, "close_px"});
                        ky += 66;
                    }
                }
                if (ui.preset_bot != b->id) {
                    if (!ui.preset_bot.empty())
                        ui.preset_by_bot[ui.preset_bot] = ui.preset_cur;
                    ui.presets = ui.api->load_presets(b->id);
                    ui.preset_bot = b->id;
                    auto it = ui.preset_by_bot.find(b->id);
                    ui.preset_cur = (it != ui.preset_by_bot.end() && !it->second.empty())
                        ? it->second : "STD";
                    ui.preset_open = false;
                    ui.preset_edit = false;
                }
                std::string tag = hunter_tag(b->id, b->name);
                Rect drop{kx + 4, ky + 10, 196, 24};
                ui.preset_drop_r = drop;
                fill(dpy, buf, gc, C.navy, drop);
                stroke(dpy, buf, gc, C.gold, drop);
                std::string dlab = tag + " · " + (ui.preset_cur.empty() ? "STD" : ui.preset_cur);
                text(dpy, buf, gc, C.gold, drop.x + 8, drop.y + 16, ascii(dlab + "  v", 22));
                ui.hits.push_back({Hit::PresetDrop, ui.sel, drop, b->id});
                Rect savb{kx + 204, ky + 10, 56, 24};
                fill(dpy, buf, gc, C.navy, savb);
                stroke(dpy, buf, gc, C.gold, savb);
                text(dpy, buf, gc, C.gold, savb.x + 10, savb.y + 16, "SAVE");
                ui.hits.push_back({Hit::PresetSave, ui.sel, savb, b->id});
                if (ui.preset_edit) {
                    Rect box{kx + 4, ky + 38, 228, 22};
                    fill(dpy, buf, gc, C.navy, box);
                    stroke(dpy, buf, gc, C.gold, box);
                    std::string show = ui.preset_buf.empty() ? "type name, Enter" : (ui.preset_buf + "|");
                    text(dpy, buf, gc, C.green, box.x + 6, box.y + 16, ascii(show, 24));
                }
                Rect stdb{kx + 4, ky + (ui.preset_edit ? 66 : 42), 80, 48};
                fill(dpy, buf, gc, C.navy, stdb);
                stroke(dpy, buf, gc, C.gold, stdb);
                text(dpy, buf, gc, C.gold, stdb.x + 18, stdb.y + 20, "STD");
                text(dpy, buf, gc, C.ab_muted, stdb.x + 8, stdb.y + 36, "defaults");
                ui.hits.push_back({Hit::CtrlStd, ui.sel, stdb, b->id});
                if (ui.preset_open && ui.presets.is_array()) {
                    int n = std::max(1, (int)ui.presets.size());
                    Rect menu{drop.x, drop.y + drop.h + 2, std::max(drop.w, 220), 24 + n * 18};
                    fill(dpy, buf, gc, C.ab_panel, menu);
                    stroke(dpy, buf, gc, C.gold, menu);
                    text(dpy, buf, gc, C.gold, menu.x + 6, menu.y + 14,
                         ascii(tag + " saves only", 24));
                    int myy = menu.y + 32;
                    for (auto& p : ui.presets) {
                        if (!p.is_object()) continue;
                        std::string nm = p.value("name", std::string());
                        if (nm.empty()) continue;
                        bool lk = p.value("locked", false);
                        Rect row{menu.x + 2, myy - 12, menu.w - 4, 16};
                        if (nm == ui.preset_cur) fill(dpy, buf, gc, C.navy, row);
                        text(dpy, buf, gc, lk ? C.gold : C.ab_text, row.x + 6, myy,
                             ascii(lk ? (nm + "  (std)") : nm, 22));
                        ui.hits.push_back({Hit::PresetPick, ui.sel, row, nm});
                        myy += 18;
                    }
                }
                yy = ky + cellh + 8 + (ui.preset_open ? 8 + (int)ui.presets.size() * 18 : 0)
                    + (ui.preset_edit ? 24 : 0);
            }

            if (ui.sel != ui.hunter_sel_was) {
                ui.hunter_log_scroll = 0;
                ui.hunter_logs_scroll = 0;
                ui.hunter_sel_was = ui.sel;
            }
            ui.hunter_main_r = main;
            // Refusal lines sit in their own box on the bottom of this pane.
            // Keep the close list short so that box stays on screen.
            int logs_reserve = b->logs.empty() ? 0 : 168;
            text(dpy, buf, gc, C.gold, x, yy, "TRADE LOG  (last 2 closes · all-time W/L)");
            yy += 16;
            {
                char wl[96];
                snprintf(wl, sizeof(wl), "%dW / %dL   all-time %s   %d closes",
                         b->n_wins, b->n_losses, money(b->realized_pnl).c_str(),
                         b->n_closes);
                text(dpy, buf, gc, b->realized_pnl < 0 ? C.rose : C.green, x, yy, fit_px(wl, inner));
                yy += 16;
                int log_top = yy;
                int log_h = 52;
                int bottom_lim = main.y + main.h - 8 - logs_reserve;
                if (log_top + log_h > bottom_lim)
                    log_h = std::max(36, bottom_lim - log_top);
                Rect logc{x, log_top, inner, log_h};
                stroke(dpy, buf, gc, C.border, logc);
                set_clip(dpy, gc, logc);
                int ly = logc.y + 6 - ui.hunter_log_scroll;
                if (b->trades.empty()) {
                    text(dpy, buf, gc, C.muted, logc.x + 8, ly + 12, "no closes yet");
                    ly += 18;
                }
                int shown = 0;
                for (auto& t : b->trades) {
                    if (shown >= 2) break;
                    std::string act = t.action;
                    bool is_open = (act == "OPEN" || act == "ADD" || act == "open" || act == "add");
                    if (is_open) continue;
                    bool win = (t.has_pnl && t.pnl > 0) || (!t.has_pnl && t.roe > 0 && !is_open);
                    bool loss = (t.has_pnl && t.pnl < 0) || (!t.has_pnl && t.roe < 0 && !is_open);
                    unsigned long tc = is_open ? C.cyan : (loss ? C.rose : C.green);
                    std::string ts = t.ts;
                    if (ts.size() > 19) ts = ts.substr(0, 19);
                    char row[180];
                    char tlab[16] = "";
                    if (t.trader > 0) snprintf(tlab, sizeof(tlab), "T#%d  ", t.trader);
                    if (is_open) {
                        snprintf(row, sizeof(row), "%s  %s%s  %s %s  %.4g @ %.4g",
                                 ts.c_str(), tlab, act.c_str(), t.coin.c_str(), t.side.c_str(),
                                 t.amount, t.price);
                    } else if (t.has_pnl) {
                        snprintf(row, sizeof(row), "%s  %s%s  %s %s  %s  %+.2f  %+.1f%%",
                                 ts.c_str(), tlab, act.c_str(), t.coin.c_str(), t.side.c_str(),
                                 win ? "WIN" : (loss ? "LOSS" : "FLAT"),
                                 t.pnl, t.roe);
                    } else if (t.roe != 0) {
                        snprintf(row, sizeof(row), "%s  %s%s  %s %s  %s  %+.1f%%",
                                 ts.c_str(), tlab, act.c_str(), t.coin.c_str(), t.side.c_str(),
                                 win ? "WIN" : (loss ? "LOSS" : "FLAT"), t.roe);
                    } else {
                        snprintf(row, sizeof(row), "%s  %s%s  %s %s",
                                 ts.c_str(), tlab, act.c_str(), t.coin.c_str(), t.side.c_str());
                    }
                    text(dpy, buf, gc, tc, logc.x + 8, ly + 12, fit_px(row, inner - 16));
                    ly += 14;
                    if (!t.reason.empty() && !is_open) {
                        text(dpy, buf, gc, C.muted, logc.x + 16, ly + 12, fit_px(t.reason, inner - 24));
                        ly += 14;
                    }
                    shown++;
                }
                ui.hunter_log_h = ly + ui.hunter_log_scroll - logc.y + 8;
                ui.hunter_log_body = logc.h;
                set_clip(dpy, gc, main);
                yy = log_top + log_h + 8;
            }

            auto fields = ui.fields_for(*b);
            int log_block = logs_reserve;
            int ky = yy;
            if (!pmm_knobs && yy < main.y + main.h - 8 - logs_reserve) {
            text(dpy, buf, gc, C.gold, x, yy, "CONTROLLER");
            yy += 18;
            int knob_bottom = main.y + main.h - 8 - log_block;
            int kx = x;
            ky = yy;
            const int boxw = 152;
            const int boxh = 50;
            const int boxgap = 10;
            int drawn = 0;
            for (auto& f : fields) {
                if (f.key == "sendEvery2Win") continue;
                if (kx + boxw > right) {
                    kx = x;
                    ky += boxh + boxgap;
                }
                if (ky + boxh > knob_bottom) break;
                Rect box{kx, ky, boxw, boxh};
                fill(dpy, buf, gc, C.bg, box);
                stroke(dpy, buf, gc, C.border, box);
                auto stored = b->controller.contains(f.key) ? b->controller[f.key] : nlohmann::json();
                double dv = display_value(f, stored);
                text(dpy, buf, gc, C.muted, kx + 8, ky + 14, fit_px(f.label, boxw - 14));
                if (f.kind == "bool") {
                    Rect ck{kx + 8, ky + 24, 12, 12};
                    stroke(dpy, buf, gc, C.gold, ck);
                    if (dv >= 0.5) fill(dpy, buf, gc, C.gold, {ck.x + 2, ck.y + 2, 8, 8});
                    text(dpy, buf, gc, C.text, kx + 28, ky + 34, dv >= 0.5 ? "on" : "off");
                    ui.hits.push_back({Hit::CtrlBool, ui.sel, box, f.key});
                } else {
                    Rect sl{kx + 8, ky + 22, boxw - 16, 7};
                    fill(dpy, buf, gc, rgb(dpy, cm, 10, 40, 20), sl);
                    double span = (f.maxv > f.minv) ? (f.maxv - f.minv) : 1;
                    double t = (dv - f.minv) / span;
                    t = std::min(1.0, std::max(0.0, t));
                    fill(dpy, buf, gc, col, {sl.x, sl.y, std::max(2, static_cast<int>(t * sl.w)), sl.h});
                    text(dpy, buf, gc, C.green, kx + 8, ky + 42, fit_px(json_preview(stored), boxw - 16));
                }
                kx += boxw + boxgap;
                drawn++;
            }
            if (fields.empty()) {
                text(dpy, buf, gc, C.muted, x, yy, "no controller fields in snapshot");
                ky = yy + 16;
            } else if (drawn) {
                ky += boxh + boxgap;
            }
            }

            ui.hunter_logs_r = {};
            if (!b->logs.empty()) {
                int box_h = logs_reserve;
                int top = main.y + main.h - 4 - box_h;
                if (top < main.y + 28) {
                    box_h = std::max(48, main.y + main.h - 4 - (main.y + 28));
                    top = main.y + main.h - 4 - box_h;
                }
                text(dpy, buf, gc, C.gold, x, top + 12, "LOGS");
                text(dpy, buf, gc, C.muted, x + 54, top + 12, "wheel scrolls");
                Rect logc{x, top + 18, inner, std::max(24, box_h - 22)};
                ui.hunter_logs_r = logc;
                fill(dpy, buf, gc, C.bg, logc);
                stroke(dpy, buf, gc, C.border, logc);
                int maxs = 0;
                int body_h = 8 + static_cast<int>(b->logs.size()) * 14;
                maxs = std::max(0, body_h - logc.h);
                if (ui.hunter_logs_scroll > maxs) ui.hunter_logs_scroll = maxs;
                set_clip(dpy, gc, logc);
                int ly = logc.y + 4 - ui.hunter_logs_scroll;
                for (auto it = b->logs.rbegin(); it != b->logs.rend(); ++it) {
                    bool refused = it->find("REFUSED") != std::string::npos;
                    text(dpy, buf, gc, refused ? C.rose : C.muted, logc.x + 8, ly + 12,
                         fit_px(*it, inner - 16));
                    ly += 14;
                }
                ui.hunter_logs_h = body_h;
                ui.hunter_logs_body = logc.h;
                set_clip(dpy, gc, main);
            }
            clear_clip(dpy, gc);
            ui.content_h = 0;
        };

        auto draw_funds = [&]() {
            int y = body.y + 16 - ui.scroll;
            int x = 18;
            text(dpy, buf, gc, C.gold, x, y, "HYPERLIQUID WALLETS");
            text(dpy, buf, gc, C.muted, x + 220, y, "from /api/home  ·  address edit / restart not wired");
            y += 24;
            text(dpy, buf, gc, C.muted, x, y, "hunter");
            text(dpy, buf, gc, C.muted, x + 160, y, "address");
            text(dpy, buf, gc, C.muted, x + 520, y, "equity");
            text(dpy, buf, gc, C.muted, x + 640, y, "free");
            text(dpy, buf, gc, C.muted, x + 760, y, "pos");
            text(dpy, buf, gc, C.muted, x + 820, y, "run");
            y += 18;
            for (auto& w : ui.snap.wallets) {
                text(dpy, buf, gc, C.text, x, y, ascii(w.name, 16));
                text(dpy, buf, gc, C.cyan, x + 160, y, w.address.empty() ? "-" : w.address);
                text(dpy, buf, gc, C.gold, x + 520, y, money(w.equity));
                text(dpy, buf, gc, C.green, x + 640, y, money(w.free_usd));
                char n[8];
                snprintf(n, sizeof(n), "%d", w.n_pos);
                text(dpy, buf, gc, C.text, x + 760, y, n);
                text(dpy, buf, gc, w.running ? C.green : C.rose, x + 820, y, w.running ? "RUN" : "OFF");
                y += 18;
            }
            y += 16;
            text(dpy, buf, gc, C.muted, x, y, "Paste / save wallet keys stays on Flask /confiig until this page is wired.");
            ui.content_h = y + ui.scroll - body.y + 40;
        };

        auto draw_strategy = [&]() {
            int y = body.y + 16 - ui.scroll;
            int x = 18;
            text(dpy, buf, gc, C.gold, x, y, "JANESTREET");
            text(dpy, buf, gc, C.muted, x + 130, y, ascii(ui.snap.qa_message.empty()
                ? "weekly overlay  ·  dry run  ·  add/delete not wired"
                : ui.snap.qa_message, 90));
            y += 24;
            const char* days[] = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"};
            text(dpy, buf, gc, C.muted, x, y, "day");
            text(dpy, buf, gc, C.muted, x + 60, y, "min");
            text(dpy, buf, gc, C.muted, x + 120, y, "dur");
            text(dpy, buf, gc, C.muted, x + 180, y, "kind");
            text(dpy, buf, gc, C.muted, x + 280, y, "bot");
            text(dpy, buf, gc, C.muted, x + 460, y, "label");
            y += 18;
            if (ui.snap.qa.empty()) {
                text(dpy, buf, gc, C.muted, x, y, "no JaneStreet blocks  ·  overlay is not armed");
                y += 18;
            }
            for (auto& q : ui.snap.qa) {
                const char* day = (q.weekday >= 0 && q.weekday < 7) ? days[q.weekday] : "?";
                text(dpy, buf, gc, C.gold, x, y, day);
                char b[32];
                snprintf(b, sizeof(b), "%d", q.minute);
                text(dpy, buf, gc, C.text, x + 60, y, b);
                snprintf(b, sizeof(b), "%d", q.duration_min);
                text(dpy, buf, gc, C.text, x + 120, y, b);
                text(dpy, buf, gc, q.kind == "close_all" ? C.rose : C.cyan, x + 180, y, ascii(q.kind, 10));
                text(dpy, buf, gc, C.muted, x + 280, y, ascii(q.bot_id, 20));
                text(dpy, buf, gc, C.text, x + 460, y, ascii(q.label.empty() ? q.coin + " " + q.value : q.label, 40));
                y += 16;
            }
            y += 16;
            text(dpy, buf, gc, C.amber, x, y, "armed = false on Flask  ·  this UI will not POST /api/qa/item");
            ui.content_h = y + ui.scroll - body.y + 40;
        };

        auto draw_system = [&]() {
            int y = body.y + 16 - ui.scroll;
            int x = 18;
            text(dpy, buf, gc, C.gold, x, y, "CONNECTION");
            y += 8;
            char clab[48];
            snprintf(clab, sizeof(clab), "SPRING DAEMON  %s:%d", api.cpp_host.c_str(), api.cpp_port);
            int cw = static_cast<int>(strlen(clab)) * 9 + 20;
            Rect rc{x, y, cw, 24};
            fill(dpy, buf, gc, rgb(dpy, cm, 50, 28, 8), rc);
            stroke(dpy, buf, gc, C.ab_orange, rc);
            text(dpy, buf, gc, C.ab_orange, rc.x + 10, rc.y + 16, clab);
            y += 32;
            text(dpy, buf, gc, C.muted, x, y,
                 "matrix locked to Spring daemon  ·  start/stop in springctl  ·  Flask :5001 is website only");
            y += 24;
            text(dpy, buf, gc, C.gold, x, y, "HEALTH");
            y += 20;
            auto kpi = [&](int col, const char* l, const std::string& v, unsigned long c, double pct) {
                Rect r{x + col * 250, y, 236, 70};
                fill(dpy, buf, gc, C.panel, r);
                stroke(dpy, buf, gc, pct >= 90 ? C.rose : C.border, r);
                text(dpy, buf, gc, C.muted, r.x + 10, r.y + 16, l);
                text(dpy, buf, gc, c, r.x + 10, r.y + 36, v);
                Rect bar{r.x + 10, r.y + 48, r.w - 20, 8};
                fill(dpy, buf, gc, rgb(dpy, cm, 10, 40, 20), bar);
                int fw = static_cast<int>(std::min(1.0, std::max(0.0, pct / 100.0)) * bar.w);
                fill(dpy, buf, gc, pct >= 90 ? C.rose : C.green, {bar.x, bar.y, std::max(1, fw), bar.h});
            };
            char c1[48], c2[48], c3[48], c4[48];
            snprintf(c1, sizeof(c1), "%.1f%%", ui.snap.host.cpu_pct);
            snprintf(c2, sizeof(c2), "%.1f%%  %.1f / %.1f G", ui.snap.host.mem_pct,
                     ui.snap.host.mem_used_gb, ui.snap.host.mem_total_gb);
            snprintf(c3, sizeof(c3), "%.1f%%  %.0f / %.0f G", ui.snap.host.disk_pct,
                     ui.snap.host.disk_used_gb, ui.snap.host.disk_total_gb);
            snprintf(c4, sizeof(c4), "%s  %d ms  %s", ui.snap.net.ok ? "up" : "DOWN",
                     ui.snap.net.ms, ascii(ui.snap.net.target, 16).c_str());
            kpi(0, "CPU", c1, C.green, ui.snap.host.cpu_pct);
            kpi(1, "RAM", c2, C.cyan, ui.snap.host.mem_pct);
            kpi(2, "DISK", c3, C.gold, ui.snap.host.disk_pct);
            kpi(3, "NET", c4, ui.snap.net.ok ? C.green : C.rose, ui.snap.net.ok ? 10 : 100);
            y += 90;
            char ip[80];
            snprintf(ip, sizeof(ip), "HL IP  %.0f / %.0f used   %.0f left",
                     ui.snap.rate.used, ui.snap.rate.cap, ui.snap.rate.left);
            text(dpy, buf, gc, C.text, x, y, ip);
            y += 28;
            text(dpy, buf, gc, C.gold, x, y, "HUMMINGBOT VERSION");
            y += 18;
            {
                auto& v = ui.snap.version;
                Rect box{x, y, std::min(W - 40, 980), 118};
                unsigned long stc = v.up_to_date ? C.green : (v.status == "behind" ? C.rose : C.amber);
                fill(dpy, buf, gc, C.panel, box);
                stroke(dpy, buf, gc, stc, box);
                char line[220];
                snprintf(line, sizeof(line), "running  %s    github  %s    %s",
                         ascii(v.running.empty() ? "?" : v.running, 16).c_str(),
                         ascii(v.github.empty() ? "?" : v.github, 16).c_str(),
                         ascii(v.status.empty() ? "unknown" : v.status, 20).c_str());
                text(dpy, buf, gc, stc, box.x + 12, box.y + 18, line);
                snprintf(line, sizeof(line), "image  %s    host tree  %s",
                         ascii(v.image.empty() ? "hummingbot/hummingbot:latest" : v.image, 40).c_str(),
                         ascii(v.tree.empty() ? "-" : v.tree, 16).c_str());
                text(dpy, buf, gc, C.cyan, box.x + 12, box.y + 36, line);
                text(dpy, buf, gc, C.muted, box.x + 12, box.y + 54,
                     ascii(v.published.empty() ? "github.com/hummingbot/hummingbot" : ("published " + v.published), 90));
                text(dpy, buf, gc, C.muted, box.x + 12, box.y + 72,
                     "compare only — this box never pulls, restarts, or closes clips");
                text(dpy, buf, gc, C.amber, box.x + 12, box.y + 90,
                     ascii(v.notes.empty() ? "open SYSTEM to load GitHub tag" : v.notes[0], 100));
                y += box.h + 12;
                int nshow = std::min(4, (int)v.notes.size());
                for (int i = 1; i < nshow; ++i) {
                    text(dpy, buf, gc, C.muted, x, y, ascii("· " + v.notes[i], 120));
                    y += 16;
                }
                if ((int)v.notes.size() > nshow) y += 4;
            }
            text(dpy, buf, gc, C.gold, x, y, "HYPERLIQUID API KEY");
            y += 18;
            {
                int bh = ui.api_edit ? 132 : 112;
                Rect box{x, y, std::min(W - 40, 980), bh};
                unsigned long ec = C.amber;
                if (ui.api_exch == "primary" || ui.api_exch == "named") ec = C.green;
                else if (ui.api_exch == "no") ec = C.rose;
                fill(dpy, buf, gc, C.panel, box);
                stroke(dpy, buf, gc, ui.api_busy ? C.amber : C.gold, box);
                std::string who = ui.api_addr.empty() ? "no API key in SQLite yet" : ui.api_addr;
                text(dpy, buf, gc, C.cyan, box.x + 12, box.y + 20, ascii(who, 70));
                std::string line2 = ui.api_edit
                    ? "paste once with Ctrl+V  ·  Enter saves  ·  Esc cancels"
                    : (ui.api_note.empty() ? "Hyperliquid shows the secret one time" : ui.api_note);
                text(dpy, buf, gc, ui.api_edit ? C.gold : ec, box.x + 12, box.y + 40, ascii(line2, 110));
                text(dpy, buf, gc, C.amber, box.x + 12, box.y + 58,
                     "Running hunters reload. Paused hunters stay paused. Positions stay.");
                if (ui.api_busy) {
                    text(dpy, buf, gc, C.amber, box.x + 12, box.y + 84, "saving into SQLite and the connector files…");
                } else if (ui.api_edit) {
                    Rect field{box.x + 12, box.y + 72, std::min(560, box.w - 24), 26};
                    fill(dpy, buf, gc, C.navy, field);
                    stroke(dpy, buf, gc, C.gold, field);
                    std::string stars(std::min(ui.api_buf.size(), static_cast<size_t>(36)), '*');
                    if (ui.api_buf.size() > 36) stars += " " + std::to_string(ui.api_buf.size());
                    if ((int)(ui.now() * 2.0) % 2) stars += "_";
                    text(dpy, buf, gc, C.gold, field.x + 8, field.y + 17, stars);
                    ui.hits.push_back({Hit::ApiKey, 3, field});
                    Rect save{box.x + 12, box.y + 104, 88, 22};
                    stroke(dpy, buf, gc, C.green, save);
                    text(dpy, buf, gc, C.green, save.x + 24, save.y + 15, "Save");
                    ui.hits.push_back({Hit::ApiKey, 1, save});
                    Rect cancel{box.x + 108, box.y + 104, 96, 22};
                    stroke(dpy, buf, gc, C.rose, cancel);
                    text(dpy, buf, gc, C.rose, cancel.x + 16, cancel.y + 15, "Cancel");
                    ui.hits.push_back({Hit::ApiKey, 2, cancel});
                } else {
                    Rect ch{box.x + 12, box.y + 74, 110, 24};
                    fill(dpy, buf, gc, rgb(dpy, cm, 40, 28, 8), ch);
                    stroke(dpy, buf, gc, C.gold, ch);
                    text(dpy, buf, gc, C.gold, ch.x + 22, ch.y + 16, "Change");
                    ui.hits.push_back({Hit::ApiKey, 0, ch});
                }
                y += box.h + 16;
            }
            text(dpy, buf, gc, C.gold, x, y, "BACKUP / USB / UPDATE");
            y += 20;
            text(dpy, buf, gc, ui.snap.backup.ok ? C.green : C.amber, x, y,
                 ascii(ui.snap.backup.message.empty() ? "open System pages in Flask to run jobs" : ui.snap.backup.message, 120));
            y += 20;
            if (!ui.snap.backup.usb_h.empty()) {
                text(dpy, buf, gc, C.muted, x, y,
                     "USB " + ui.snap.backup.usb_h + "   need " + ui.snap.backup.need_h);
                y += 20;
            }
            text(dpy, buf, gc, C.muted, x, y, "Backup run, Update, Version restore, Copy-to-USB  ·  not wired from this window");
            y += 24;
            Rect a{x, y, 140, 22};
            stroke(dpy, buf, gc, C.gold, a);
            text(dpy, buf, gc, C.gold, a.x + 28, a.y + 15, "Run backup");
            ui.hits.push_back({Hit::NotWired, 0, a});
            Rect b{x + 156, y, 140, 22};
            stroke(dpy, buf, gc, C.gold, b);
            text(dpy, buf, gc, C.gold, b.x + 18, b.y + 15, "Copy to USB");
            ui.hits.push_back({Hit::NotWired, 0, b});
            y += 36;
            text(dpy, buf, gc, C.gold, x, y, "WIRE PACK  (one JSON)");
            y += 18;
            text(dpy, buf, gc, C.muted, x, y,
                 "saves desk + home + health canvases to conf/wire_pack.json  ·  restore does not close clips");
            y += 22;
            Rect ps{x, y, 168, 22};
            stroke(dpy, buf, gc, C.cyan, ps);
            text(dpy, buf, gc, C.cyan, ps.x + 12, ps.y + 15, "Save wire JSON");
            ui.hits.push_back({Hit::PackSave, 0, ps});
            Rect pr{x + 180, y, 188, 22};
            stroke(dpy, buf, gc, C.amber, pr);
            text(dpy, buf, gc, C.amber, pr.x + 10, pr.y + 15, "Restore wire JSON");
            ui.hits.push_back({Hit::PackRestore, 0, pr});
            ui.content_h = y + ui.scroll - body.y + 60;
        };

        set_clip(dpy, gc, body);
        if (ui.page == PgHome) draw_home();
        else if (ui.page == PgWire) draw_wire();
        else if (ui.page == PgCondor) draw_condor();
        else if (ui.page == PgHunters) draw_hunters();
        else if (ui.page == PgFunds) draw_funds();
        else if (ui.page == PgStrategy) draw_strategy();
        else draw_system();
        clear_clip(dpy, gc);

        if (ui.content_h > body.h && ui.page != PgWire && ui.page != PgHunters) {
            int track = body.h - 20;
            int thumb = std::max(24, body.h * body.h / std::max(1, ui.content_h));
            int maxs = std::max(0, ui.content_h - body.h);
            int ty = body.y + (maxs ? (ui.scroll * (track - thumb) / maxs) : 0);
            fill(dpy, buf, gc, C.border, {W - 8, body.y, 6, body.h});
            fill(dpy, buf, gc, C.gold, {W - 8, ty, 6, thumb});
        }

        if (ui.compile_win) {
            ui.hits.clear();
            fill(dpy, buf, gc, C.ab_bg, {0, 0, W, H});
            int ww = std::min(920, W - 40), hh = std::min(560, H - 40);
            Rect winr{(W - ww) / 2, (H - hh) / 2, ww, hh};
            ui.compile_win_r = winr;
            fill(dpy, buf, gc, C.ab_panel, winr);
            auto& c = ui.snap.compile;
            unsigned long hc = c.can_integrate ? C.green : C.rose;
            stroke(dpy, buf, gc, hc, winr);
            const char* title = c.message.empty() && !c.ok ? "COMPILE"
                : (c.can_integrate ? (c.workaround_used ? "COMPILE OK · WORKAROUND" : "COMPILE OK") : "COMPILE BLOCKED");
            text(dpy, buf, gc, hc, winr.x + 16, winr.y + 22, title);
            text(dpy, buf, gc, C.ab_text, winr.x + 16, winr.y + 40, ascii(c.message, 92));
            Rect clbtn{winr.x + winr.w - 80, winr.y + 8, 64, 20};
            stroke(dpy, buf, gc, C.ab_muted, clbtn);
            text(dpy, buf, gc, C.ab_muted, clbtn.x + 12, clbtn.y + 14, "Close");
            ui.hits.push_back({Hit::CompileClose, 0, clbtn});
            std::string sum = std::to_string(c.n_pos) + " open clip(s)  ·  "
                + std::to_string(c.n_new) + " new route(s)  ·  "
                + std::to_string(c.n_rm) + " removed  ·  "
                + std::to_string(c.n_knobs) + " knob change(s)";
            if (c.n_blockers) sum += "  ·  " + std::to_string(c.n_blockers) + " would close";
            if (c.n_parked) sum += "  ·  " + std::to_string(c.n_parked) + " parked at live";
            sum += "  ·  positions untouched";
            text(dpy, buf, gc, C.ab_muted, winr.x + 16, winr.y + 58, ascii(sum, 100));
            int by = winr.y + hh - 36;
            if (!c.can_integrate && !c.blockers.empty()) {
                Rect wb{winr.x + 16, by, 400, 22};
                stroke(dpy, buf, gc, C.amber, wb);
                text(dpy, buf, gc, C.amber, wb.x + 8, wb.y + 15, "Workaround: park close knobs, compile the rest");
                ui.hits.push_back({Hit::CompileWork, 0, wb});
            }
            if (c.can_integrate) {
                Rect ib{winr.x + 16, by, 340, 22};
                stroke(dpy, buf, gc, C.green, ib);
                text(dpy, buf, gc, C.green, ib.x + 8, ib.y + 15,
                     c.workaround_used ? "Integrate workaround to production" : "Integrate to production");
                ui.hits.push_back({Hit::CompileGo, 0, ib});
            } else {
                text(dpy, buf, gc, C.ab_muted, winr.x + 430, by + 15,
                     "Integrate stays off until COMPILE OK");
            }
            Rect bodyc{winr.x + 8, winr.y + 70, winr.w - 16, by - (winr.y + 78)};
            ui.compile_body_h = bodyc.h;
            set_clip(dpy, gc, bodyc);
            int y = bodyc.y + 4 - ui.compile_scroll;
            auto sec = [&](const char* h, const std::vector<std::string>& lines, unsigned long col, const char* empty) {
                text(dpy, buf, gc, C.gold, bodyc.x + 8, y + 12, h);
                y += 18;
                if (lines.empty()) {
                    text(dpy, buf, gc, C.ab_muted, bodyc.x + 8, y + 12, empty);
                    y += 16;
                    return;
                }
                for (auto& ln : lines) {
                    text(dpy, buf, gc, col, bodyc.x + 8, y + 12, ascii(ln, 96));
                    y += 15;
                }
                y += 6;
            };
            if (!c.bands.empty()) sec("HOME BANDS (live / after Integrate)", c.bands, C.ab_text, "—");
            if (!c.blockers.empty()) {
                sec("WOULD CLOSE NOW — BLOCKED", c.blockers, C.rose, "");
                text(dpy, buf, gc, C.ab_muted, bodyc.x + 8, y + 12,
                     "Integrate stays off until those knobs would not flatten a live clip.");
                y += 16;
                text(dpy, buf, gc, C.ab_muted, bodyc.x + 8, y + 12,
                     "Workaround parks only those knobs at live values and compiles the rest.");
                y += 20;
            }
            if (!c.parked.empty()) sec("PARKED AT LIVE (workaround)", c.parked, C.amber, "none");
            sec("HUNTERS", c.hunters, C.ab_text, "No hunter rows in this compile.");
            sec("WIRING VS LIVE", c.wiring, C.cyan, "No wiring changes vs live reconstruct.");
            sec("CONTROLLER KNOBS", c.knobs, C.ab_text, "Controller knobs match live.");
            if (!c.bias.empty()) sec("HUNTER BIAS (does not close)", c.bias, C.ab_text, "");
            if (!c.risk.empty()) sec("RISK SKIM (no auto HL send)", c.risk, C.ab_text, "");
            if (!c.desk_bias.empty()) {
                text(dpy, buf, gc, C.gold, bodyc.x + 8, y + 12, "DESK BIAS (does not close)");
                y += 18;
                text(dpy, buf, gc, C.ab_text, bodyc.x + 8, y + 12, ascii(c.desk_bias, 96));
                y += 20;
            }
            if (!c.warnings.empty()) sec("WARNINGS", c.warnings, C.amber, "none");
            ui.compile_h = y + ui.compile_scroll - bodyc.y + 8;
            clear_clip(dpy, gc);
            if (ui.compile_h > bodyc.h) {
                int track = bodyc.h;
                int thumb = std::max(16, bodyc.h * bodyc.h / std::max(1, ui.compile_h));
                int maxs = std::max(0, ui.compile_h - bodyc.h);
                int ty = bodyc.y + (maxs ? (ui.compile_scroll * (track - thumb) / maxs) : 0);
                fill(dpy, buf, gc, C.border, {winr.x + winr.w - 10, bodyc.y, 5, bodyc.h});
                fill(dpy, buf, gc, C.gold, {winr.x + winr.w - 10, ty, 5, thumb});
            }
            if (ui.now() < ui.toast_until && !ui.toast.empty())
                text(dpy, buf, gc, C.cyan, winr.x + 16, winr.y + hh - 8, ascii(ui.toast, 90));
        }

        if (ui.audit_win) {
            ui.hits.clear();
            fill(dpy, buf, gc, C.ab_bg, {0, 0, W, H});
            int ww = std::min(920, W - 40), hh = std::min(580, H - 40);
            Rect winr{(W - ww) / 2, (H - hh) / 2, ww, hh};
            ui.audit_win_r = winr;
            fill(dpy, buf, gc, C.ab_panel, winr);
            stroke(dpy, buf, gc, C.gold, winr);
            auto& a = ui.snap.copy_audit;
            text(dpy, buf, gc, C.gold, winr.x + 16, winr.y + 22, "COPY LEADER AUDIT");
            text(dpy, buf, gc, a.worker ? C.green : C.rose, winr.x + 280, winr.y + 22,
                 a.worker ? "WORKER LIVE" : "WORKER OFF");
            std::string sched = a.schedule.empty()
                ? "daily cron  T#1 01:00  T#2 06:00  T#3 11:00"
                : a.schedule;
            if (!a.timezone.empty()) sched += "  " + a.timezone;
            text(dpy, buf, gc, C.cyan, winr.x + 16, winr.y + 40, ascii(sched, 92));
            text(dpy, buf, gc, C.ab_muted, winr.x + 16, winr.y + 56,
                 ascii((a.updated.empty() ? std::string("no latest.json") : ("latest " + a.updated))
                       + "  ·  read only  ·  no HL trades", 92));
            Rect clbtn{winr.x + winr.w - 80, winr.y + 8, 64, 20};
            stroke(dpy, buf, gc, C.ab_muted, clbtn);
            text(dpy, buf, gc, C.ab_muted, clbtn.x + 12, clbtn.y + 14, "Close");
            ui.hits.push_back({Hit::AuditClose, 0, clbtn});
            Rect runb{winr.x + winr.w - 168, winr.y + 8, 80, 20};
            stroke(dpy, buf, gc, C.amber, runb);
            text(dpy, buf, gc, C.amber, runb.x + 10, runb.y + 14, "Run all");
            ui.hits.push_back({Hit::AuditRun, 0, runb, "all"});
            int by = winr.y + hh - 28;
            Rect bodyc{winr.x + 8, winr.y + 68, winr.w - 16, by - (winr.y + 74)};
            ui.audit_body_h = bodyc.h;
            set_clip(dpy, gc, bodyc);
            int y = bodyc.y + 4 - ui.audit_scroll;
            if (a.traders.empty()) {
                text(dpy, buf, gc, C.rose, bodyc.x + 8, y + 14,
                     a.message.empty() ? "no daily cron rows in latest.json" : ascii(a.message, 90));
                y += 20;
            }
            for (auto& t : a.traders) {
                unsigned long capc = t.cap == "OK" ? C.green : (t.stale ? C.amber : C.rose);
                char h1[120];
                snprintf(h1, sizeof(h1), "T#%d  %s  %s / %s  %s",
                         t.num, t.short_addr.c_str(), t.cap.c_str(), t.sleep.c_str(),
                         t.can_open ? "can open" : "no new");
                text(dpy, buf, gc, capc, bodyc.x + 8, y + 12, ascii(h1, 96));
                y += 16;
                char h2[140];
                snprintf(h2, sizeof(h2), "eq %s  free %s  uPnL %s  pos %d  sleep %.1fh",
                         money(t.equity).c_str(), money(t.free_margin).c_str(),
                         money(t.upnl).c_str(), t.n_pos, t.sleep_hours);
                text(dpy, buf, gc, C.ab_text, bodyc.x + 8, y + 12, ascii(h2, 96));
                y += 16;
                text(dpy, buf, gc, C.ab_muted, bodyc.x + 8, y + 12,
                     ascii("cron " + t.audited_at + "  last " + t.last_open, 96));
                y += 16;
                if (!t.error.empty()) {
                    text(dpy, buf, gc, C.rose, bodyc.x + 8, y + 12, ascii(t.error, 96));
                    y += 16;
                }
                int shown = 0;
                for (auto& p : t.positions) {
                    if (shown >= 6) break;
                    char pl[96];
                    snprintf(pl, sizeof(pl), "  %s %s  sz %.4g  en %.4g  %s",
                             p.side.c_str(), p.coin.c_str(), p.size, p.entry, money(p.upnl).c_str());
                    text(dpy, buf, gc, p.upnl < 0 ? C.rose : C.cyan, bodyc.x + 8, y + 12, ascii(pl, 94));
                    y += 15;
                    shown++;
                }
                y += 8;
            }
            if (!a.history.empty()) {
                text(dpy, buf, gc, C.gold, bodyc.x + 8, y + 12, "DAILY HISTORY");
                y += 18;
                for (auto& ln : a.history) {
                    text(dpy, buf, gc, C.ab_muted, bodyc.x + 8, y + 12, ascii(ln, 96));
                    y += 15;
                }
            }
            ui.audit_h = y + ui.audit_scroll - bodyc.y + 8;
            clear_clip(dpy, gc);
            if (ui.audit_h > bodyc.h) {
                int track = bodyc.h;
                int thumb = std::max(16, bodyc.h * bodyc.h / std::max(1, ui.audit_h));
                int maxs = std::max(0, ui.audit_h - bodyc.h);
                int ty = bodyc.y + (maxs ? (ui.audit_scroll * (track - thumb) / maxs) : 0);
                fill(dpy, buf, gc, C.border, {winr.x + winr.w - 10, bodyc.y, 5, bodyc.h});
                fill(dpy, buf, gc, C.gold, {winr.x + winr.w - 10, ty, 5, thumb});
            }
        }

        draw_hover(dpy, buf, gc, C, ui.hover_r);

        XCopyArea(dpy, buf, win, gc, 0, 0, W, H, 0, 0);
        XFlush(dpy);
    };

    redraw();

    auto switch_backend = [&](bool cpp) {
        bool already = (api.backend == "cpp-debug") == cpp;
        if (already) {
            ui.say(cpp ? "already on Spring" : "already on Flask");
            redraw();
            return;
        }
        auto err = cpp ? api.use_cpp() : api.use_flask();
        if (!err.empty()) {
            ui.say(err);
            redraw();
            return;
        }
        ui.dirty = false;
        ui.fields_ok = false;
        ui.qa_ok = false;
        ui.backup_ok = false;
        ui.wire_fitted = false;
        ui.wire_sel = -1;
        ui.wire_marked.clear();
        ui.refresh(true);
        if (ui.page == PgWire) {
            const char* boards[] = {"desk", "home", "health"};
            ui.api->load_wire(ui.snap, boards[ui.wire_board]);
            ui.snap_loaded_nodes();
        }
        XStoreName(dpy, win, api.read_only
                       ? "rimBaud // MATRIX  (DEBUG C++ read-only)"
                       : "rimBaud // MATRIX  (read-only)");
        ui.say(cpp ? (api.writes
                          ? "connected Spring WRITE MODE · files only · never CLOSE/OPEN"
                          : "connected Spring GET only · enable WRITE MODE in springctl")
                   : "connected live Flask");
        redraw();
    };

    auto clamp_compile_scroll = [&]() {
        int maxs = std::max(0, ui.compile_h - ui.compile_body_h);
        ui.compile_scroll = std::max(0, std::min(maxs, ui.compile_scroll));
    };
    auto run_compile = [&](bool workaround) {
        ui.compile_win = true;
        ui.compile_scroll = 0;
        ui.ctx_on = false;
        ui.snap.compile = CompileResult{};
        ui.snap.compile.message = workaround
            ? "workaround compile — parking close knobs at live…"
            : "compiling canvas vs live…";
        redraw();
        XFlush(dpy);
        ui.say(ui.api->compile_wire(ui.snap, workaround));
        ui.compile_scroll = 0;
    };
    auto field_of = [&](int bix, const std::string& key) {
        CtrlField empty;
        if (bix < 0 || bix >= (int)ui.snap.bots.size() || key.empty()) return empty;
        for (auto& f : ui.fields_for(ui.snap.bots[bix]))
            if (f.key == key) return f;
        return empty;
    };
    auto post_knob = [&](int bix, const CtrlField& f, double display, bool quiet = false) {
        if (bix < 0 || bix >= (int)ui.snap.bots.size() || f.key.empty()) return;
        auto& b = ui.snap.bots[bix];
        auto val = store_ctrl(f, display);
        const std::string& k = f.key;
        if (k == "sl_roe_pct" || k == "tp_roe_pct" || k == "take_profit" || k == "scale_roe_pct" || k == "close_px") {
            for (auto& p : ui.snap.positions) {
                if (p.bot_id != b.id || p.size == 0) continue;
                bool hit = false;
                if (k == "sl_roe_pct" && p.roe <= -display) hit = true;
                if ((k == "tp_roe_pct" || k == "scale_roe_pct") && p.roe >= display) hit = true;
                if (k == "take_profit" && p.entry > 0 && p.mark > 0) {
                    double tp = display > 1.0 ? display / 100.0 : display;
                    if (p.side == "LONG" && p.mark >= p.entry * (1.0 + tp)) hit = true;
                    if (p.side == "SHORT" && p.mark <= p.entry * (1.0 - tp)) hit = true;
                }
                if (k == "close_px" && display > 0 && p.mark > 0) {
                    if (p.side == "LONG" && display <= p.mark) hit = true;
                    if (p.side == "SHORT" && display >= p.mark) hit = true;
                }
                if (hit) {
                    ui.say("refusing " + k + " — would flatten " + p.coin + " now · clips untouched");
                    return;
                }
            }
        }
        if (k == "close_px") {
            b.controller[f.key] = display;
            ui.say(ui.api->post_ctrl(b.id, "close_px", display));
            return;
        }
        b.controller[f.key] = val;
        if (b.id == "hummingbot-bn-swing") {
            PmmDisp d = pmm_read(b);
            if (f.key == "scale_x") {
                d.x = display;
                pmm_from_x(d);
                pmm_couple(d, b.equity, b.free_usd, "scale_x");
            } else {
                pmm_couple(d, b.equity, b.free_usd, f.key);
            }
            pmm_apply_bot(b, d);
            ui.knob_live = (f.key == "scale_x") ? d.x
                : (f.key == "take_profit") ? d.tp
                : (f.key == "trail_activation") ? d.arm
                : (f.key == "trail_delta") ? d.trail
                : (f.key == "rebuy_pct") ? d.next
                : (f.key == "executor_refresh_time") ? d.refresh
                : (f.key == "sl_roe_pct") ? d.sl
                : (f.key == "margin_pct") ? d.margin : display;
            ui.api->post_live_knobs(b.id, b.controller);
            auto vals = pmm_store(d);
            if (!quiet) {
                ui.say(ui.api->post_ctrl_map(b.id, vals));
                ui.knob_posted = ui.now();
            } else if (ui.now() - ui.knob_posted > 0.35) {
                ui.knob_posted = ui.now();
                ui.api->post_ctrl_map(b.id, vals);
            }
            if (f.key == "sl_roe_pct" || f.key == "scale_x") {
                ui.api->post_sl(b.id, d.sl);
                ui.api->post_live_knobs(b.id, b.controller);
            }
        } else {
            ui.api->post_live_knobs(b.id, b.controller);
            if (f.key == "sl_roe_pct" && val.is_number())
                ui.api->post_sl(b.id, val.get<double>());
            if (!quiet) {
                ui.say(ui.api->post_ctrl(b.id, f.key, val));
                ui.knob_posted = ui.now();
            } else if (ui.now() - ui.knob_posted > 0.35) {
                ui.knob_posted = ui.now();
                ui.api->post_ctrl(b.id, f.key, val);
            }
        }
    };
    auto begin_knob_edit = [&](int bix, const std::string& key) {
        auto f = field_of(bix, key);
        if (f.key.empty() || bix < 0 || bix >= (int)ui.snap.bots.size()) return;
        auto stored = ui.snap.bots[bix].controller.contains(f.key)
            ? ui.snap.bots[bix].controller[f.key] : nlohmann::json();
        double dv = display_value(f, stored);
        if (ui.knob_focus_bot == bix && ui.knob_focus_key == f.key
            && ui.knob_live >= f.minv && ui.knob_live <= f.maxv + 1e-9)
            dv = ui.knob_live;
        char vb[32];
        if (f.key == "close_px" || f.kind == "price_pct" || f.key == "scale_x" || f.kind == "pct_frac" || f.step < 1)
            snprintf(vb, sizeof(vb), "%.2f", dv);
        else snprintf(vb, sizeof(vb), "%.0f", dv);
        ui.knob_edit = true;
        ui.knob_edit_bot = bix;
        ui.knob_edit_key = f.key;
        ui.knob_edit_buf = vb;
        ui.knob_edit_sel = true;
        ui.knob_drag = false;
        ui.knob_focus_bot = bix;
        ui.knob_focus_key = f.key;
        ui.knob_live = dv;
        ui.knob_min = f.minv;
        ui.knob_max = f.maxv;
        ui.knob_step = f.step > 0 ? f.step : 1;
        ui.knob_kind = f.kind;
        char msg[96];
        snprintf(msg, sizeof(msg), "type %s  ·  Enter apply  ·  Esc cancel  ·  range %.2g–%.2g",
                 f.label.c_str(), f.minv, f.maxv);
        ui.say(msg);
    };
    auto commit_knob_edit = [&](bool apply) {
        if (!ui.knob_edit) return;
        int bix = ui.knob_edit_bot;
        std::string key = ui.knob_edit_key;
        std::string raw = ui.knob_edit_buf;
        ui.knob_edit = false;
        ui.knob_edit_buf.clear();
        ui.knob_edit_sel = false;
        if (!apply || raw.empty() || raw == "-" || raw == ".") {
            ui.say("knob edit cancelled");
            return;
        }
        auto f = field_of(bix, key);
        if (f.key.empty()) return;
        double v = 0;
        try { v = std::stod(raw); } catch (...) {
            ui.say("invalid number — knob unchanged");
            return;
        }
        if (v < f.minv) v = f.minv;
        if (v > f.maxv) v = f.maxv;
        post_knob(bix, f, v);
        ui.knob_live = v;
        ui.knob_focus_bot = bix;
        ui.knob_focus_key = key;
        char msg[96];
        snprintf(msg, sizeof(msg), "%s  %.2f  ·  typed  ·  chart follows", f.label.c_str(), v);
        ui.say(msg);
    };

    auto show_pub = [&]() {
        auto p = read_api_pub();
        ui.api_addr = p.address;
        ui.api_exch = p.on_exchange;
        if (p.address.empty()) ui.api_note = "not saved in SQLite yet";
        else {
            ui.api_note = "on exchange: " + (p.on_exchange.empty() ? "?" : p.on_exchange);
            if (!p.updated_at.empty()) ui.api_note += "  ·  " + p.updated_at;
            if (p.n_files) ui.api_note += "  ·  " + std::to_string(p.n_files) + " connector files";
        }
    };
    auto cancel_api = [&]() {
        wipe_secret(ui.api_buf);
        ui.api_edit = false;
        api_paste_wait = false;
    };
    auto submit_api = [&]() {
        if (ui.api_busy) return;
        std::string secret = norm_api_secret(ui.api_buf);
        wipe_secret(ui.api_buf);
        if (secret.empty()) {
            ui.api_edit = true;
            ui.say("paste the API private key once (64 hex characters)");
            return;
        }
        int in[2], out[2];
        if (pipe(in) != 0 || pipe(out) != 0) {
            wipe_secret(secret);
            ui.say("could not start the key save");
            return;
        }
        pid_t pid = fork();
        if (pid < 0) {
            close(in[0]); close(in[1]); close(out[0]); close(out[1]);
            wipe_secret(secret);
            ui.say("could not start the key save");
            return;
        }
        if (pid == 0) {
            dup2(in[0], STDIN_FILENO);
            dup2(out[1], STDOUT_FILENO);
            int dn = open("/dev/null", O_WRONLY);
            if (dn >= 0) dup2(dn, STDERR_FILENO);
            close(in[0]); close(in[1]); close(out[0]); close(out[1]);
            if (dn > 2) close(dn);
            std::string script = desk_repo() + "/scripts/hl_api_key.py";
            execl("/usr/bin/python3", "python3", script.c_str(), "set", static_cast<char*>(nullptr));
            _exit(127);
        }
        close(in[0]);
        close(out[1]);
        secret.push_back('\n');
        size_t off = 0;
        while (off < secret.size()) {
            ssize_t n = ::write(in[1], secret.data() + off, secret.size() - off);
            if (n < 0) {
                if (errno == EINTR) continue;
                break;
            }
            off += static_cast<size_t>(n);
        }
        close(in[1]);
        wipe_secret(secret);
        int flags = fcntl(out[0], F_GETFL, 0);
        if (flags >= 0) fcntl(out[0], F_SETFL, flags | O_NONBLOCK);
        if (api_fd >= 0) close(api_fd);
        api_fd = out[0];
        api_out.clear();
        ui.api_busy = true;
        ui.api_edit = false;
        api_paste_wait = false;
        ui.say("saving the API key into SQLite — running hunters reload, paused stay paused, positions stay");
        ui.toast_until = ui.now() + 8;
    };
    auto poll_api = [&]() {
        if (!ui.api_busy || api_fd < 0) return;
        char tmp[2048];
        for (;;) {
            ssize_t n = ::read(api_fd, tmp, sizeof(tmp));
            if (n > 0) {
                api_out.append(tmp, tmp + n);
                if (api_out.size() > 8000) api_out.erase(0, api_out.size() - 4000);
                continue;
            }
            if (n < 0 && errno == EINTR) continue;
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return;
            close(api_fd);
            api_fd = -1;
            ui.api_busy = false;
            std::string msg = api_result_message(api_out);
            wipe_secret(api_out);
            show_pub();
            if (msg.find("saved") != std::string::npos) ui.api_note = msg;
            ui.say(msg);
            ui.toast_until = ui.now() + 8;
            redraw();
            return;
        }
    };
    auto accept_api_chars = [&](const std::string& chunk) {
        if (!ui.api_edit) return;
        for (unsigned char c : chunk) {
            if (ui.api_buf.size() >= 80) break;
            if ((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F') || c == 'x' || c == 'X')
                ui.api_buf.push_back(static_cast<char>(c));
        }
    };
    show_pub();

    bool run = true;
    while (run) {
        while (XPending(dpy)) {
            XEvent e;
            XNextEvent(dpy, &e);
            if (e.type == ClientMessage) {
                if ((Atom)e.xclient.data.l[0] == wm_delete) run = false;
            } else if (e.type == DestroyNotify) {
                run = false;
            } else if (e.type == SelectionNotify) {
                api_paste_wait = false;
                if (e.xselection.property == None) {
                    if (ui.api_edit) ui.say("clipboard empty — copy the API key, then Ctrl+V");
                } else {
                    Atom type = None;
                    int fmt = 0;
                    unsigned long n = 0, rem = 0;
                    unsigned char* data = nullptr;
                    if (XGetWindowProperty(dpy, win, e.xselection.property, 0, 256, True,
                                           AnyPropertyType, &type, &fmt, &n, &rem, &data) == Success && data) {
                        std::string chunk(reinterpret_cast<char*>(data), static_cast<size_t>(n));
                        XFree(data);
                        accept_api_chars(chunk);
                        wipe_secret(chunk);
                    }
                }
                redraw();
            } else if (e.type == Expose) redraw();
            else if (e.type == ConfigureNotify) {
                if (e.xconfigure.width > 200 && e.xconfigure.height > 200) {
                    W = e.xconfigure.width;
                    H = e.xconfigure.height;
                    XFreePixmap(dpy, buf);
                    buf = XCreatePixmap(dpy, win, W, H, DefaultDepth(dpy, scr));
                    ui.wire_fitted = false;
                    redraw();
                }
            } else if (e.type == KeyPress) {
                KeySym ks = XLookupKeysym(&e.xkey, 0);
                if (ui.api_edit) {
                    char chs[8]{};
                    int nch = XLookupString(&e.xkey, chs, 7, &ks, nullptr);
                    bool ctrl = (e.xkey.state & ControlMask) != 0;
                    bool shift = (e.xkey.state & ShiftMask) != 0;
                    if (ks == XK_Escape) {
                        cancel_api();
                        ui.say("API key change cancelled");
                    } else if (ks == XK_Return || ks == XK_KP_Enter) {
                        submit_api();
                    } else if (ks == XK_BackSpace || ks == XK_Delete) {
                        if (!ui.api_buf.empty()) {
                            ui.api_buf.back() = '\0';
                            ui.api_buf.pop_back();
                        }
                    } else if ((ctrl && (ks == XK_v || ks == XK_V)) || (nch == 1 && chs[0] == 22)) {
                        api_paste_wait = true;
                        XConvertSelection(dpy, atom_clip, atom_utf8, atom_paste, win, CurrentTime);
                    } else if (shift && ks == XK_Insert) {
                        api_paste_wait = true;
                        XConvertSelection(dpy, XA_PRIMARY, atom_utf8, atom_paste, win, CurrentTime);
                    } else if (!ctrl && nch == 1) {
                        unsigned char c = static_cast<unsigned char>(chs[0]);
                        if (ui.api_buf.size() < 80 &&
                            ((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') ||
                             (c >= 'A' && c <= 'F') || c == 'x' || c == 'X'))
                            ui.api_buf.push_back(static_cast<char>(c));
                    }
                    redraw();
                } else {
                bool annot_keys = false;
                if (ui.page == PgWire && ui.wire_inspect >= 0 && ui.wire_inspect < (int)ui.snap.wire_nodes.size()) {
                    auto& an = ui.snap.wire_nodes[ui.wire_inspect];
                    if (an.type == "frame" || an.type == "circle") {
                        annot_keys = true;
                        char chs[8]{};
                        int nch = XLookupString(&e.xkey, chs, 7, &ks, nullptr);
                        if (ks == XK_Escape) { ui.wire_inspect = -1; ui.wire_sel = -1; redraw(); }
                        else if (ks == XK_Delete) { ui.remove_node(ui.wire_inspect); redraw(); }
                        else if (ks == XK_BackSpace) {
                            if (!an.label.empty()) an.label.pop_back();
                            ui.dirty = true;
                            redraw();
                        } else if (ks == XK_Return) { redraw(); }
                        else if (nch == 1 && (unsigned char)chs[0] >= 32 && (unsigned char)chs[0] < 127) {
                            if (an.label.size() < 48) an.label.push_back(chs[0]);
                            ui.dirty = true;
                            redraw();
                        }
                    }
                }
                if (annot_keys) { /* caption edit owns keys */ }
                else if (ui.preset_edit) {
                    char chs[8]{};
                    int nch = XLookupString(&e.xkey, chs, 7, &ks, nullptr);
                    if (ks == XK_Escape) {
                        ui.preset_edit = false;
                        ui.preset_buf.clear();
                    } else if (ks == XK_Return || ks == XK_KP_Enter) {
                        std::string nm = ui.preset_buf;
                        ui.preset_edit = false;
                        ui.preset_buf.clear();
                        if (!nm.empty() && ui.sel >= 0 && ui.sel < (int)ui.snap.bots.size()) {
                            auto& bsv = ui.snap.bots[ui.sel];
                            if (preset_is_std(nm)) {
                                ui.say("STD stays locked — pick another name");
                            } else {
                                ui.say(ui.api->save_preset(bsv.id, nm, hunter_values_for_save(bsv)));
                                ui.presets = ui.api->load_presets(bsv.id);
                                ui.preset_cur = nm;
                                ui.preset_bot = bsv.id;
                                ui.preset_by_bot[bsv.id] = nm;
                            }
                        }
                    } else if (ks == XK_BackSpace || ks == XK_Delete) {
                        if (!ui.preset_buf.empty()) ui.preset_buf.pop_back();
                    } else if (nch == 1) {
                        char c = chs[0];
                        bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')
                            || (c >= '0' && c <= '9') || c == ' ' || c == '-' || c == '_';
                        if (ok && ui.preset_buf.size() < 24) ui.preset_buf.push_back(c);
                    }
                    redraw();
                }
                else if (ui.knob_edit) {
                    char chs[8]{};
                    int nch = XLookupString(&e.xkey, chs, 7, &ks, nullptr);
                    if (ks == XK_Escape) {
                        commit_knob_edit(false);
                    } else if (ks == XK_Return || ks == XK_KP_Enter) {
                        commit_knob_edit(true);
                    } else if (ks == XK_BackSpace || ks == XK_Delete) {
                        if (ui.knob_edit_sel) {
                            ui.knob_edit_buf.clear();
                            ui.knob_edit_sel = false;
                        } else if (!ui.knob_edit_buf.empty()) {
                            ui.knob_edit_buf.pop_back();
                        }
                    } else if (nch == 1) {
                        char c = chs[0];
                        bool ok = (c >= '0' && c <= '9')
                            || (c == '.' && ui.knob_edit_buf.find('.') == std::string::npos)
                            || (c == '-' && ui.knob_edit_buf.empty());
                        if (ok) {
                            if (ui.knob_edit_sel) {
                                ui.knob_edit_buf.clear();
                                ui.knob_edit_sel = false;
                                if (c == '.' ) ui.knob_edit_buf = "0";
                            }
                            if (ui.knob_edit_buf.size() < 12)
                                ui.knob_edit_buf.push_back(c);
                        }
                    }
                    redraw();
                }
                else if (ks == XK_q) run = false;
                else if (ks == XK_Escape) {
                    if (ui.repl_on) {
                        ui.repl_on = false;
                        ui.repl_tool_id.clear();
                        ui.repl_target_id.clear();
                        redraw();
                    } else if (ui.compile_win) {
                        ui.compile_win = false;
                        redraw();
                    } else if (ui.audit_win) {
                        ui.audit_win = false;
                        redraw();
                    } else if (ui.knob_focus_bot >= 0) {
                        ui.knob_focus_bot = -1;
                        ui.knob_focus_key.clear();
                        redraw();
                    } else if (ui.marquee_on || !ui.wire_marked.empty()) {
                        ui.marquee_on = false;
                        ui.wire_marked.clear();
                        redraw();
                    } else if (ui.wire_inspect >= 0) {
                        ui.wire_inspect = -1;
                        redraw();
                    } else if (ui.ctx_on || ui.wire_from >= 0 || ui.tool_drag) {
                        ui.ctx_on = false;
                        ui.wire_from = -1;
                        ui.wire_handle.clear();
                        ui.tool_drag = false;
                        redraw();
                    } else run = false;
                } else if (ks == XK_r) { ui.refresh(false); redraw(); }
                else if (ks == XK_f && ui.page == PgWire) { ui.wire_fitted = false; redraw(); }
                else if (ks == XK_u && ui.page == PgWire) { ui.do_undo(); redraw(); }
                else if ((ks == XK_Delete || ks == XK_BackSpace) && ui.page == PgWire) {
                    if (!ui.wire_marked.empty()) ui.remove_marked();
                    else if (ui.wire_sel >= 0) ui.remove_node(ui.wire_sel);
                    else if (ui.edge_sel >= 0) ui.remove_edge(ui.edge_sel);
                    redraw();
                } else if (ks == XK_c && ui.page == PgWire) {
                    run_compile(false);
                    redraw();
                } else if (ks == XK_d) {
                    ui.say("matrix is locked to Spring daemon — use springctl");
                    redraw();
                } else if ((ks == XK_Up || ks == XK_Down) && ui.knob_focus_bot >= 0
                           && !ui.knob_focus_key.empty()) {
                    auto f = field_of(ui.knob_focus_bot, ui.knob_focus_key);
                    if (!f.key.empty() && ui.knob_focus_bot < (int)ui.snap.bots.size()) {
                        auto& b = ui.snap.bots[ui.knob_focus_bot];
                        auto stored = b.controller.contains(f.key) ? b.controller[f.key] : nlohmann::json();
                        double dv = display_value(f, stored);
                        if (ui.knob_focus_key == f.key && ui.knob_live >= f.minv && ui.knob_live <= f.maxv + 1e-9)
                            dv = ui.knob_live;
                        bool sh = (e.xkey.state & ShiftMask) != 0;
                        double st = 1;
                        if (f.kind == "price_pct" || f.key == "scale_x") st = sh ? 0.1 : 0.01;
                        else if (f.kind == "pct_frac" && f.maxv <= 1.01) st = sh ? 0.1 : 0.01;
                        else if (f.kind == "pct_frac") st = sh ? 5 : 1;
                        else if (f.step >= 1) st = sh ? 5 : 1;
                        else st = sh ? f.step : std::max(0.01, f.step * 0.1);
                        double next = dv + (ks == XK_Up ? st : -st);
                        if (next < f.minv) next = f.minv;
                        if (next > f.maxv) next = f.maxv;
                        post_knob(ui.knob_focus_bot, f, next, true);
                        ui.knob_live = next;
                        ui.knob_bot = ui.knob_focus_bot;
                        ui.knob_key = ui.knob_focus_key;
                        char msg[112];
                        snprintf(msg, sizeof(msg), "%s  %.2f  ·  arrows ±%.2g  ·  chart follows",
                                 f.label.c_str(), next, st);
                        ui.say(msg);
                    }
                    redraw();
                } else if (ks >= XK_1 && ks <= XK_7) { goto_page(static_cast<Page>(ks - XK_1)); redraw(); }
                else if (ks == XK_Left) {
                    int p = static_cast<int>(ui.page) - 1;
                    if (p < 0) p = PgCount - 1;
                    goto_page(static_cast<Page>(p));
                    redraw();
                } else if (ks == XK_Right) {
                    int p = (static_cast<int>(ui.page) + 1) % PgCount;
                    goto_page(static_cast<Page>(p));
                    redraw();
                } else if (ks == XK_Next || ks == XK_Page_Down) {
                    if (ui.compile_win) {
                        ui.compile_scroll += 80;
                        clamp_compile_scroll();
                    } else {
                        ui.scroll = std::min(std::max(0, ui.content_h - (H - 80)), ui.scroll + 80);
                    }
                    redraw();
                } else if (ks == XK_Prior || ks == XK_Page_Up) {
                    if (ui.compile_win) {
                        ui.compile_scroll = std::max(0, ui.compile_scroll - 80);
                    } else {
                        ui.scroll = std::max(0, ui.scroll - 80);
                    }
                    redraw();
                }
                }
            } else if (e.type == ButtonPress) {
                int mx = e.xbutton.x, my = e.xbutton.y;
                if (e.xbutton.button == 4) {
                    if (ui.compile_win) {
                        ui.compile_scroll = std::max(0, ui.compile_scroll - 48);
                    } else if (ui.audit_win) {
                        ui.audit_scroll = std::max(0, ui.audit_scroll - 36);
                    } else if (ui.page == PgHunters && inside(ui.hunter_logs_r, mx, my)) {
                        ui.hunter_logs_scroll = std::max(0, ui.hunter_logs_scroll - 28);
                    } else if (ui.page == PgHunters && inside(ui.hunter_main_r, mx, my)) {
                        bool kn = false;
                        for (int hi = static_cast<int>(ui.hits.size()) - 1; hi >= 0; --hi) {
                            auto& h = ui.hits[hi];
                            if (h.kind != Hit::CtrlKnob || !inside(h.r, mx, my)) continue;
                            auto f = field_of(h.i, h.key);
                            if (f.key.empty()) break;
                            auto stored = ui.snap.bots[h.i].controller.contains(f.key)
                                ? ui.snap.bots[h.i].controller[f.key] : nlohmann::json();
                            double dv = display_value(f, stored);
                            double st = f.step > 0 ? f.step : 1;
                            if (e.xbutton.state & ShiftMask) st *= 0.1;
                            post_knob(h.i, f, dv + st);
                            kn = true;
                            break;
                        }
                        if (!kn)
                            ui.hunter_log_scroll = std::max(0, ui.hunter_log_scroll - 36);
                    } else if (ui.page == PgWire && ui.ctx_on) {
                        ui.ctx_scroll = std::max(0, ui.ctx_scroll - 3);
                    } else if (ui.page == PgWire && ui.wire_inspect >= 0 && inside(ui.np_r, mx, my)) {
                        bool kn = false;
                        for (int hi = static_cast<int>(ui.hits.size()) - 1; hi >= 0; --hi) {
                            auto& h = ui.hits[hi];
                            if (h.kind != Hit::CtrlKnob || !inside(h.r, mx, my)) continue;
                            auto f = field_of(h.i, h.key);
                            if (f.key.empty()) break;
                            auto stored = ui.snap.bots[h.i].controller.contains(f.key)
                                ? ui.snap.bots[h.i].controller[f.key] : nlohmann::json();
                            double dv = display_value(f, stored);
                            double st = f.step > 0 ? f.step : 1;
                            if (e.xbutton.state & ShiftMask) st *= 0.1;
                            post_knob(h.i, f, dv + st);
                            kn = true;
                            break;
                        }
                        if (!kn) {
                            ui.inspect_scroll = std::max(0, ui.inspect_scroll - 36);
                        }
                    } else if (ui.page == PgWire && inside(ui.tb_r, mx, my)) {
                        ui.tb_scroll = std::max(0, ui.tb_scroll - 36);
                    } else if (ui.page == PgWire && inside(ui.canvas_r, mx, my)) {
                        double wx = ui.pan_x + (mx - ui.canvas_r.x) / ui.zoom;
                        double wy = ui.pan_y + (my - ui.canvas_r.y) / ui.zoom;
                        ui.zoom = std::min(2.2, ui.zoom * 1.12);
                        ui.pan_x = wx - (mx - ui.canvas_r.x) / ui.zoom;
                        ui.pan_y = wy - (my - ui.canvas_r.y) / ui.zoom;
                    } else {
                        ui.scroll = std::max(0, ui.scroll - 48);
                    }
                    redraw();
                } else if (e.xbutton.button == 5) {
                    if (ui.compile_win) {
                        ui.compile_scroll += 48;
                        clamp_compile_scroll();
                    } else if (ui.audit_win) {
                        int maxs = std::max(0, ui.audit_h - ui.audit_body_h);
                        ui.audit_scroll = std::min(maxs, ui.audit_scroll + 36);
                    } else if (ui.page == PgHunters && inside(ui.hunter_logs_r, mx, my)) {
                        int maxs = std::max(0, ui.hunter_logs_h - ui.hunter_logs_body);
                        ui.hunter_logs_scroll = std::min(maxs, ui.hunter_logs_scroll + 28);
                    } else if (ui.page == PgHunters && inside(ui.hunter_main_r, mx, my)) {
                        bool kn = false;
                        for (int hi = static_cast<int>(ui.hits.size()) - 1; hi >= 0; --hi) {
                            auto& h = ui.hits[hi];
                            if (h.kind != Hit::CtrlKnob || !inside(h.r, mx, my)) continue;
                            auto f = field_of(h.i, h.key);
                            if (f.key.empty()) break;
                            auto stored = ui.snap.bots[h.i].controller.contains(f.key)
                                ? ui.snap.bots[h.i].controller[f.key] : nlohmann::json();
                            double dv = display_value(f, stored);
                            double st = f.step > 0 ? f.step : 1;
                            if (e.xbutton.state & ShiftMask) st *= 0.1;
                            post_knob(h.i, f, dv - st);
                            kn = true;
                            break;
                        }
                        if (!kn) {
                            int maxs = std::max(0, ui.hunter_log_h - ui.hunter_log_body);
                            ui.hunter_log_scroll = std::min(maxs, ui.hunter_log_scroll + 36);
                        }
                    } else if (ui.page == PgWire && ui.ctx_on) {
                        ui.ctx_scroll += 3;
                    } else if (ui.page == PgWire && ui.wire_inspect >= 0 && inside(ui.np_r, mx, my)) {
                        bool kn = false;
                        for (int hi = static_cast<int>(ui.hits.size()) - 1; hi >= 0; --hi) {
                            auto& h = ui.hits[hi];
                            if (h.kind != Hit::CtrlKnob || !inside(h.r, mx, my)) continue;
                            auto f = field_of(h.i, h.key);
                            if (f.key.empty()) break;
                            auto stored = ui.snap.bots[h.i].controller.contains(f.key)
                                ? ui.snap.bots[h.i].controller[f.key] : nlohmann::json();
                            double dv = display_value(f, stored);
                            double st = f.step > 0 ? f.step : 1;
                            if (e.xbutton.state & ShiftMask) st *= 0.1;
                            post_knob(h.i, f, dv - st);
                            kn = true;
                            break;
                        }
                        if (!kn) {
                            int maxs = std::max(0, ui.inspect_h - std::max(40, ui.np_r.h - 24));
                            ui.inspect_scroll = std::min(maxs, ui.inspect_scroll + 36);
                        }
                    } else if (ui.page == PgWire && inside(ui.tb_r, mx, my)) {
                        ui.tb_scroll += 36;
                    } else if (ui.page == PgWire && inside(ui.canvas_r, mx, my)) {
                        double wx = ui.pan_x + (mx - ui.canvas_r.x) / ui.zoom;
                        double wy = ui.pan_y + (my - ui.canvas_r.y) / ui.zoom;
                        ui.zoom = std::max(0.08, ui.zoom / 1.12);
                        ui.pan_x = wx - (mx - ui.canvas_r.x) / ui.zoom;
                        ui.pan_y = wy - (my - ui.canvas_r.y) / ui.zoom;
                    } else {
                        ui.scroll = std::min(std::max(0, ui.content_h - (H - 80)), ui.scroll + 48);
                    }
                    redraw();
                } else if (e.xbutton.button == 2 && ui.page == PgWire && !ui.compile_win && !ui.repl_on) {
                    ui.panning = true;
                    ui.pan_mx = mx;
                    ui.pan_my = my;
                    ui.pan_sx = ui.pan_x;
                    ui.pan_sy = ui.pan_y;
                    redraw();
                } else if (e.xbutton.button == 3 && ui.compile_win) {
                    redraw();
                } else if (e.xbutton.button == 3 && ui.page == PgWire) {
                    if (ui.repl_on) {
                        ui.repl_on = false;
                        ui.repl_tool_id.clear();
                        ui.repl_target_id.clear();
                        redraw();
                    } else {
                    int tool_i = -1;
                    bool canvas_top = false;
                    for (int hi = static_cast<int>(ui.hits.size()) - 1; hi >= 0; --hi) {
                        auto& h = ui.hits[hi];
                        if (!inside(h.r, mx, my)) continue;
                        if (h.kind == Hit::ToolItem) { tool_i = h.i; break; }
                        if (h.kind == Hit::WireNode || h.kind == Hit::WirePort || h.kind == Hit::WireEdge
                            || h.kind == Hit::AnnotSize) {
                            canvas_top = true;
                            break;
                        }
                    }
                    if (tool_i >= 0 && !canvas_top && tool_i < static_cast<int>(ui.snap.tools.size())) {
                        auto& t = ui.snap.tools[tool_i];
                        ui.tool_pick_id = t.id;
                        if (t.type == "bot" || t.type == "ctrl") ui.ask_replace(t);
                        else ui.say("right-click a controller to replace the one on the wire");
                        redraw();
                    } else if (inside(ui.tb_r, mx, my) && !canvas_top) {
                        ui.say("right-click a controller to replace the one on the wire");
                        redraw();
                    } else {
                    ui.ctx_on = true;
                    ui.ctx_x = mx;
                    ui.ctx_y = my;
                    ui.ctx_scroll = 0;
                    ui.ctx_wx = ui.pan_x + (mx - ui.canvas_r.x) / std::max(0.08, ui.zoom);
                    ui.ctx_wy = ui.pan_y + (my - ui.canvas_r.y) / std::max(0.08, ui.zoom);
                    ui.ctx_kind = 0;
                    ui.ctx_i = -1;
                    for (int hi = static_cast<int>(ui.hits.size()) - 1; hi >= 0; --hi) {
                        auto& h = ui.hits[hi];
                        if (!inside(h.r, mx, my)) continue;
                        if (h.kind == Hit::WireNode) {
                            ui.ctx_kind = 1; ui.ctx_i = h.i; ui.wire_sel = h.i;
                            ui.tool_pick_id.clear();
                            break;
                        }
                        if (h.kind == Hit::WireEdge) { ui.ctx_kind = 2; ui.ctx_i = h.i; ui.edge_sel = h.i; break; }
                    }
                    if (ui.ctx_kind == 0 && ui.zoom > 0.01) {
                        auto sx = [&](double x) { return ui.canvas_r.x + static_cast<int>((x - ui.pan_x) * ui.zoom); };
                        auto sy = [&](double y) { return ui.canvas_r.y + static_cast<int>((y - ui.pan_y) * ui.zoom); };
                        std::map<std::string, int> ix;
                        for (int i = 0; i < static_cast<int>(ui.snap.wire_nodes.size()); ++i)
                            ix[ui.snap.wire_nodes[i].id] = i;
                        for (int ei = 0; ei < static_cast<int>(ui.snap.wire_edges.size()); ++ei) {
                            auto& ed = ui.snap.wire_edges[ei];
                            auto a = ix.find(ed.source), b = ix.find(ed.target);
                            if (a == ix.end() || b == ix.end()) continue;
                            auto& na = ui.snap.wire_nodes[a->second];
                            auto& nb = ui.snap.wire_nodes[b->second];
                            if (near_seg(mx, my,
                                         sx(na.x) + node_w(na) * ui.zoom / 2, sy(na.y) + node_h(na) * ui.zoom / 2,
                                         sx(nb.x) + node_w(nb) * ui.zoom / 2, sy(nb.y) + node_h(nb) * ui.zoom / 2, 8)) {
                                ui.ctx_kind = 2; ui.ctx_i = ei; ui.edge_sel = ei; break;
                            }
                        }
                    }
                    redraw();
                    }
                    }
                } else if (e.xbutton.button == 1) {
                    bool hit = false;
                    bool shift = (e.xbutton.state & ShiftMask) || (e.xbutton.state & ControlMask);
                    bool alt = (e.xbutton.state & Mod1Mask) != 0;
                    if (ui.preset_open) {
                        bool on_menu = false;
                        for (auto& hh : ui.hits) {
                            if ((hh.kind == Hit::PresetDrop || hh.kind == Hit::PresetPick)
                                && inside(hh.r, mx, my)) { on_menu = true; break; }
                        }
                        if (!on_menu) ui.preset_open = false;
                    }
                    if (ui.knob_edit) {
                        bool on_same = false;
                        for (auto& hh : ui.hits) {
                            if (hh.kind != Hit::CtrlKnob || !inside(hh.r, mx, my)) continue;
                            if (hh.i == ui.knob_edit_bot && hh.key == ui.knob_edit_key) {
                                on_same = true;
                                break;
                            }
                        }
                        if (!on_same) commit_knob_edit(true);
                    }
                    if (!ui.knob_drag) {
                        bool on_knob = false;
                        for (auto& hh : ui.hits) {
                            if (hh.kind == Hit::CtrlKnob && inside(hh.r, mx, my)) { on_knob = true; break; }
                        }
                        if (!on_knob) {
                            ui.knob_focus_bot = -1;
                            ui.knob_focus_key.clear();
                        }
                    }
                    if (ui.repl_on) {
                        bool yes = false;
                        bool no = false;
                        for (int hi = static_cast<int>(ui.hits.size()) - 1; hi >= 0; --hi) {
                            auto& h = ui.hits[hi];
                            if (!inside(h.r, mx, my)) continue;
                            if (h.kind == Hit::ReplYes) { yes = true; break; }
                            if (h.kind == Hit::ReplNo) { no = true; break; }
                        }
                        if (yes) ui.commit_replace();
                        else if (no || !inside(ui.repl_win_r, mx, my)) {
                            ui.repl_on = false;
                            ui.repl_tool_id.clear();
                            ui.repl_target_id.clear();
                        }
                        redraw();
                    } else if (ui.audit_win) {
                        bool on_btn = false;
                        for (int hi = static_cast<int>(ui.hits.size()) - 1; hi >= 0; --hi) {
                            auto& h = ui.hits[hi];
                            if (!inside(h.r, mx, my)) continue;
                            if (h.kind == Hit::AuditClose) {
                                ui.audit_win = false;
                                on_btn = true;
                                break;
                            }
                            if (h.kind == Hit::AuditRun) {
                                ui.say(ui.api->run_copy_audit("all"));
                                ui.api->load_copy_audit(ui.snap);
                                on_btn = true;
                                break;
                            }
                        }
                        if (!on_btn && !inside(ui.audit_win_r, mx, my))
                            ui.audit_win = false;
                        redraw();
                    } else if (ui.compile_win) {
                        bool on_btn = false;
                        for (int hi = static_cast<int>(ui.hits.size()) - 1; hi >= 0; --hi) {
                            auto& h = ui.hits[hi];
                            if (!inside(h.r, mx, my)) continue;
                            if (h.kind == Hit::CompileClose) {
                                ui.compile_win = false;
                                on_btn = true;
                                break;
                            }
                            if (h.kind == Hit::CompileWork) {
                                run_compile(true);
                                on_btn = true;
                                break;
                            }
                            if (h.kind == Hit::CompileGo) {
                                bool could = ui.snap.compile.can_integrate;
                                auto msg = ui.api->integrate_wire(ui.snap);
                                ui.say(msg);
                                if (could && !ui.snap.compile.can_integrate) {
                                    ui.compile_win = false;
                                    ui.dirty = false;
                                }
                                on_btn = true;
                                break;
                            }
                        }
                        if (!on_btn && !inside(ui.compile_win_r, mx, my))
                            ui.compile_win = false;
                        redraw();
                    } else if (ui.ctx_on) {
                        for (int hi = static_cast<int>(ui.hits.size()) - 1; hi >= 0; --hi) {
                            auto& h = ui.hits[hi];
                            if (h.kind != Hit::Ctx || !inside(h.r, mx, my)) continue;
                            hit = true;
                            if (h.i == 0) ui.ctx_on = false;
                            else if (h.i == 1) { ui.remove_node(ui.ctx_i); ui.ctx_on = false; }
                            else if (h.i == 2) { ui.remove_edge(ui.ctx_i); ui.ctx_on = false; }
                            else if (h.i == 3) { ui.wire_from = ui.ctx_i; ui.ctx_on = false; ui.say("click a target clip"); }
                            else if (h.i >= 100) {
                                int ti = h.i - 100;
                                if (ti >= 0 && ti < static_cast<int>(ui.snap.tools.size()))
                                    ui.add_tool_at(ui.snap.tools[ti], ui.ctx_wx, ui.ctx_wy);
                                ui.ctx_on = false;
                            }
                            break;
                        }
                        if (!hit) ui.ctx_on = false;
                        redraw();
                    } else {
                    bool api_click = false;
                    for (int hi = static_cast<int>(ui.hits.size()) - 1; hi >= 0; --hi) {
                        auto& h = ui.hits[hi];
                        if (!inside(h.r, mx, my)) continue;
                        hit = true;
                        if (h.kind == Hit::Nav) {
                            goto_page(static_cast<Page>(h.i));
                            redraw();
                        } else if (h.kind == Hit::Quit) {
                            run = false;
                        } else if (h.kind == Hit::InspectBack) {
                            ui.wire_inspect = -1;
                            ui.inspect_scroll = 0;
                            redraw();
                        } else if (h.kind == Hit::ApiKey) {
                            api_click = true;
                            if (h.i == 3) {
                                /* the paste field stays open */
                            } else if (ui.api_busy) {
                                ui.say("still saving the API key");
                            } else if (h.i == 1) {
                                submit_api();
                            } else if (h.i == 2) {
                                cancel_api();
                                ui.say("API key change cancelled");
                            } else {
                                wipe_secret(ui.api_buf);
                                ui.api_edit = true;
                                ui.say("paste the key once · Enter saves · Esc cancels · running hunters reload, paused stay paused, positions stay");
                                ui.toast_until = ui.now() + 8;
                            }
                            redraw();
                            break;
                        } else if (h.kind == Hit::PackSave) {
                            ui.say(ui.api->post_wire_pack("save"));
                            redraw();
                        } else if (h.kind == Hit::PackRestore) {
                            ui.say(ui.api->post_wire_pack("restore"));
                            redraw();
                        } else if (h.kind == Hit::AnnotColor) {
                            if (h.i >= 0 && h.i < (int)ui.snap.wire_nodes.size() && !h.key.empty()) {
                                ui.push_hist();
                                ui.snap.wire_nodes[h.i].color = h.key;
                                ui.dirty = true;
                                ui.say("color " + h.key);
                            }
                            redraw();
                        } else if (h.kind == Hit::AnnotSize) {
                            if (h.i >= 0 && h.i < (int)ui.snap.wire_nodes.size()) {
                                ui.annot_size_drag = true;
                                ui.drag_moved = false;
                                ui.drag_i = h.i;
                                ui.drag_mx = mx;
                                ui.drag_my = my;
                                int aw = 0, ah = 0;
                                annot_wh(ui.snap.wire_nodes[h.i], aw, ah);
                                ui.annot_sw = aw;
                                ui.annot_sh = ah;
                                ui.wire_sel = h.i;
                            }
                            redraw();
                        } else if (h.kind == Hit::WireSnap) {
                            ui.wire_snap = !ui.wire_snap;
                            if (ui.wire_snap) {
                                ui.push_hist();
                                for (auto& n : ui.snap.wire_nodes) {
                                    n.x = snap_grid(n.x);
                                    n.y = snap_grid(n.y);
                                }
                                ui.say("SNAP on — boxes aligned to grid; drag stays on grid");
                            } else {
                                ui.say("SNAP off — free drag");
                            }
                            redraw();
                        } else if (h.kind == Hit::Chart) {
                            std::string coin = h.key;
                            if (coin.empty() && h.i >= 0 && h.i < (int)ui.snap.bots.size())
                                coin = follow_coin(ui.snap.bots[h.i], ui.snap);
                            if (coin.empty()) {
                                for (auto& p : ui.snap.positions)
                                    if (!p.coin.empty() && p.size != 0) { coin = p.coin; break; }
                            }
                            if (coin.empty()) coin = "BTC";
                            auto alive = chart_pids.find(coin);
                            if (alive != chart_pids.end() && alive->second > 0 && kill(alive->second, 0) == 0) {
                                ui.say(coin + " chart already open — CLOSE or window X");
                                redraw();
                                break;
                            }
                            char self[4096]{};
                            ssize_t n = readlink("/proc/self/exe", self, sizeof(self) - 1);
                            std::string dir = ".";
                            if (n > 0) {
                                self[n] = 0;
                                std::string p = self;
                                auto s = p.rfind('/');
                                if (s != std::string::npos) dir = p.substr(0, s);
                            }
                            std::string bin = dir + "/rimbaud-chart";
                            if (access(bin.c_str(), X_OK) != 0)
                                bin = dir + "/freiquell-chart";
                            std::string bot;
                            if (h.i >= 0 && h.i < (int)ui.snap.bots.size())
                                bot = ui.snap.bots[h.i].id;
                            pid_t pid = fork();
                            if (pid == 0) {
                                int z = open("/dev/null", O_RDWR);
                                if (z >= 0) { dup2(z, 0); if (z > 2) close(z); }
                                if (!bot.empty())
                                    execl(bin.c_str(), "rimbaud-chart", "--coin", coin.c_str(),
                                          "--bot", bot.c_str(), static_cast<char*>(nullptr));
                                else
                                    execl(bin.c_str(), "rimbaud-chart", "--coin", coin.c_str(),
                                          static_cast<char*>(nullptr));
                                _exit(127);
                            }
                            if (pid > 0) {
                                chart_pids[coin] = pid;
                                ui.say(coin + " chart  ·  CLOSE or window X quits this coin");
                            } else ui.say("could not launch rimbaud-chart");
                            redraw();
                        } else if (h.kind == Hit::Backend) {
                            switch_backend(h.i == 1);
                        } else if (h.kind == Hit::Hunter) {
                            ui.sel = h.i;
                            if (ui.page == PgHome) goto_page(PgHunters);
                            redraw();
                        } else if (h.kind == Hit::NotWired) {
                            ui.not_wired();
                            redraw();
                        } else if (h.kind == Hit::Sl) {
                            if (h.i >= 0 && h.i < (int)ui.snap.bots.size()) {
                                double pct = std::atof(h.key.c_str());
                                ui.say(ui.api->post_sl(ui.snap.bots[h.i].id, pct));
                                ui.refresh(false);
                            }
                            redraw();
                        } else if (h.kind == Hit::Lev) {
                            if (h.i >= 0 && h.i < (int)ui.snap.bots.size()) {
                                auto& b = ui.snap.bots[h.i];
                                std::string tier = h.key;
                                if (tier != "MAX" && tier != "MID" && tier != "LOW") tier = "MAX";
                                std::string msg = ui.api->post_ctrl(b.id, "leverage_tier", nlohmann::json(tier));
                                if (msg.find("saved") != std::string::npos) {
                                    b.controller["leverage_tier"] = tier;
                                    ui.say(lev_toast(tier, b.coins));
                                } else {
                                    ui.say(msg);
                                }
                                ui.refresh(false);
                            }
                            redraw();
                        } else if (h.kind == Hit::Restart) {
                            if (h.i >= 0 && h.i < (int)ui.snap.bots.size()) {
                                ui.say(ui.api->post_restart(ui.snap.bots[h.i].id));
                                ui.refresh(false);
                            }
                            redraw();
                        } else if (h.kind == Hit::HunterGate) {
                            if (h.i >= 0 && h.i < (int)ui.snap.bots.size()) {
                                bool active = (h.key == "on");
                                ui.say(ui.api->post_hunter_gate(ui.snap.bots[h.i].id, active));
                                ui.refresh(false);
                            }
                            redraw();
                        } else if (h.kind == Hit::DeskBias) {
                            ui.say(ui.api->post_desk_bias(h.key));
                            ui.refresh(false);
                            redraw();
                        } else if (h.kind == Hit::CoinBias) {
                            auto bar = h.key.find('|');
                            std::string coin = bar == std::string::npos ? h.key : h.key.substr(0, bar);
                            std::string mode = bar == std::string::npos ? "FLAT" : h.key.substr(bar + 1);
                            if (h.i >= 0 && h.i < (int)ui.snap.bots.size())
                                ui.say(ui.api->post_bias(ui.snap.bots[h.i].id, coin, mode));
                            ui.refresh(false);
                            redraw();
                        } else if (h.kind == Hit::CoinClose) {
                            if (h.i >= 0 && h.i < (int)ui.snap.bots.size()) {
                                auto& b = ui.snap.bots[h.i];
                                ui.say(ui.api->post_close_coin(b.id, h.key));
                                ui.refresh(false);
                            }
                            redraw();
                        } else if (h.kind == Hit::CoinOpen100) {
                            if (h.i >= 0 && h.i < (int)ui.snap.bots.size()) {
                                auto& b = ui.snap.bots[h.i];
                                std::string side = b.bias.count(h.key) ? b.bias.at(h.key) : "";
                                ui.say(ui.api->post_open100(b.id, h.key, side));
                                ui.refresh(false);
                            }
                            redraw();
                        } else if (h.kind == Hit::CtrlBool) {
                            if (h.i >= 0 && h.i < (int)ui.snap.bots.size()) {
                                auto& b = ui.snap.bots[h.i];
                                bool on = ctrl_flag(b.controller, h.key);
                                bool nxt = !on;
                                b.controller[h.key] = nxt;
                                ui.say(ui.api->post_ctrl(b.id, h.key, nlohmann::json(nxt)));
                                ui.api->post_live_knobs(b.id, b.controller);
                                ui.refresh(false);
                            }
                            redraw();
                        } else if (h.kind == Hit::CtrlSel) {
                            if (h.i >= 0 && h.i < (int)ui.snap.bots.size()) {
                                auto f = field_of(h.i, h.key);
                                auto& b = ui.snap.bots[h.i];
                                if (!f.options.empty()) {
                                    std::string cur = json_preview(b.controller.contains(h.key) ? b.controller[h.key] : nlohmann::json());
                                    int idx = 0;
                                    for (int oi = 0; oi < (int)f.options.size(); ++oi)
                                        if (f.options[oi] == cur) { idx = oi; break; }
                                    idx = (idx + 1) % (int)f.options.size();
                                    nlohmann::json val = f.options[idx];
                                    try { val = std::stod(f.options[idx]); } catch (...) {}
                                    ui.say(ui.api->post_ctrl(b.id, h.key, val));
                                    ui.refresh(false);
                                }
                            }
                            redraw();
                        } else if (h.kind == Hit::CopyAudit) {
                            ui.audit_scroll = 0;
                            ui.audit_win = true;
                            ui.last_poll = ui.now();
                            ui.say(ui.api->load_copy_audit(ui.snap));
                            redraw();
                        } else if (h.kind == Hit::CopyMute) {
                            bool unmute = (h.key == "unmute");
                            ui.say(ui.api->post_copy_mute(h.i, !unmute));
                            ui.refresh(false);
                            redraw();
                        } else if (h.kind == Hit::CopySlots) {
                            int slots = 1;
                            try { slots = std::stoi(h.key); } catch (...) { slots = 1; }
                            ui.say(ui.api->post_copy_slots(h.i, slots));
                            ui.refresh(false);
                            redraw();
                        } else if (h.kind == Hit::PresetDrop) {
                            ui.preset_open = !ui.preset_open;
                            ui.preset_edit = false;
                            if (h.i >= 0 && h.i < (int)ui.snap.bots.size()) {
                                ui.presets = ui.api->load_presets(ui.snap.bots[h.i].id);
                                ui.preset_bot = ui.snap.bots[h.i].id;
                            }
                            redraw();
                        } else if (h.kind == Hit::PresetSave) {
                            ui.preset_open = false;
                            if (h.i >= 0 && h.i < (int)ui.snap.bots.size()
                                && !preset_is_std(ui.preset_cur)) {
                                auto& bsv = ui.snap.bots[h.i];
                                ui.say(ui.api->save_preset(bsv.id, ui.preset_cur,
                                                           hunter_values_for_save(bsv)));
                                ui.presets = ui.api->load_presets(bsv.id);
                                ui.preset_bot = bsv.id;
                                ui.preset_by_bot[bsv.id] = ui.preset_cur;
                            } else {
                                ui.preset_edit = true;
                                ui.preset_buf.clear();
                                ui.say("STD stays locked — type a new name  ·  Enter  ·  Esc");
                            }
                            redraw();
                        } else if (h.kind == Hit::PresetPick) {
                            ui.preset_open = false;
                            if (h.i >= 0 && h.i < (int)ui.snap.bots.size() && !h.key.empty()) {
                                auto& bp = ui.snap.bots[h.i];
                                auto jr = ui.api->apply_preset(bp.id, h.key);
                                bool ok = jr.is_object() && jr.value("ok", false);
                                if (!ok) {
                                    ui.say(jr.is_object() ? jr.value("error", "load failed") : "load failed");
                                } else {
                                    if (jr.contains("values") && jr["values"].is_object()) {
                                        for (auto it = jr["values"].begin(); it != jr["values"].end(); ++it) {
                                            if (it.key() == "updated" || it.key() == "source") continue;
                                            bp.controller[it.key()] = it.value();
                                        }
                                        bp.controller.erase("rebuy_px");
                                    }
                                    ui.preset_cur = h.key;
                                    ui.preset_by_bot[bp.id] = h.key;
                                    ui.preset_bot = bp.id;
                                    double slv = 40;
                                    if (bp.controller.contains("sl_roe_pct") && bp.controller["sl_roe_pct"].is_number())
                                        slv = bp.controller["sl_roe_pct"].get<double>();
                                    ui.api->post_live_knobs(bp.id, bp.controller);
                                    ui.api->post_sl(bp.id, slv);
                                    ui.say(jr.value("message", "loaded " + h.key + " — this hunter only"));
                                }
                            }
                            redraw();
                        } else if (h.kind == Hit::CtrlStd) {
                            if (h.i >= 0 && h.i < (int)ui.snap.bots.size()
                                && ui.snap.bots[h.i].id == "hummingbot-bn-swing") {
                                PmmDisp d;
                                d.x = 0.3; d.tp = 2.40; d.arm = 1.40; d.trail = 22; d.next = 0.18;
                                d.refresh = 20; d.sl = 40; d.margin = 60;
                                auto& bstd = ui.snap.bots[h.i];
                                pmm_couple(d, bstd.equity, bstd.free_usd, "std");
                                pmm_apply_bot(bstd, d);
                                ui.api->post_live_knobs(bstd.id, bstd.controller);
                                ui.say(ui.api->post_ctrl_map(bstd.id, pmm_store(d)));
                                ui.api->post_sl(bstd.id, d.sl);
                                ui.preset_cur = "STD";
                                ui.say("PMM STD · TP 2.4 · ARM 1.4 · Trail 22 · NEXT 0.18 · 20s · SL 40 · M 60");
                                ui.refresh(false);
                            } else if (h.i >= 0 && h.i < (int)ui.snap.bots.size()) {
                                auto& bstd = ui.snap.bots[h.i];
                                auto jr = ui.api->apply_preset(bstd.id, "STD");
                                if (jr.is_object() && jr.value("ok", false)
                                    && jr.contains("values") && jr["values"].is_object()) {
                                    for (auto it = jr["values"].begin(); it != jr["values"].end(); ++it) {
                                        if (it.key() == "updated" || it.key() == "source") continue;
                                        bstd.controller[it.key()] = it.value();
                                    }
                                    ui.api->post_live_knobs(bstd.id, bstd.controller);
                                    if (bstd.controller.contains("sl_roe_pct")
                                        && bstd.controller["sl_roe_pct"].is_number())
                                        ui.api->post_sl(bstd.id, bstd.controller["sl_roe_pct"].get<double>());
                                    ui.preset_cur = "STD";
                                    ui.say(bstd.name + " STD restored — this hunter only · clips untouched");
                                } else {
                                    ui.say(jr.is_object() ? jr.value("error", "STD load failed") : "STD load failed");
                                }
                            }
                            redraw();
                        } else if (h.kind == Hit::CtrlKnob) {
                            auto f = field_of(h.i, h.key);
                            if (h.key == "moon_off" && h.i >= 0 && h.i < (int)ui.snap.bots.size()) {
                                auto& b = ui.snap.bots[h.i];
                                b.controller["close_px"] = 0.0;
                                ui.say(ui.api->post_ctrl(b.id, "close_px", 0.0));
                                redraw();
                                break;
                            }
                            if (!f.key.empty() && h.i >= 0 && h.i < (int)ui.snap.bots.size()) {
                                if (f.key == "close_px") {
                                    double tnow = ui.now();
                                    bool dbl = (h.i == ui.last_knob_bot && h.key == ui.last_knob_key
                                                && (tnow - ui.last_knob_t) < 0.55);
                                    ui.last_knob_bot = h.i;
                                    ui.last_knob_key = h.key;
                                    ui.last_knob_t = tnow;
                                    ui.knob_drag = false;
                                    if (dbl || !ui.knob_edit)
                                        begin_knob_edit(h.i, h.key);
                                    redraw();
                                    break;
                                }
                                double tnow = ui.now();
                                bool dbl = (h.i == ui.last_knob_bot && h.key == ui.last_knob_key
                                            && (tnow - ui.last_knob_t) < 0.45);
                                ui.last_knob_bot = h.i;
                                ui.last_knob_key = h.key;
                                ui.last_knob_t = tnow;
                                if (dbl) {
                                    begin_knob_edit(h.i, h.key);
                                } else {
                                    if (ui.knob_edit) commit_knob_edit(true);
                                    auto stored = ui.snap.bots[h.i].controller.contains(f.key)
                                        ? ui.snap.bots[h.i].controller[f.key] : nlohmann::json();
                                    ui.knob_drag = true;
                                    ui.knob_bot = h.i;
                                    ui.knob_key = f.key;
                                    ui.knob_kind = f.kind;
                                    ui.knob_min = f.minv;
                                    ui.knob_max = f.maxv;
                                    ui.knob_step = f.step > 0 ? f.step : 1;
                                    ui.knob_start = display_value(f, stored);
                                    ui.knob_live = ui.knob_start;
                                    ui.knob_mx = mx;
                                    ui.knob_my = my;
                                    ui.knob_focus_bot = h.i;
                                    ui.knob_focus_key = f.key;
                                    ui.knob_posted = ui.now();
                                }
                            }
                            redraw();
                        } else if (h.kind == Hit::WirePort) {
                            if (ui.wire_from >= 0) {
                                ui.connect_ports(ui.wire_from, ui.wire_handle, ui.wire_from_src,
                                                 h.i, h.key, h.src);
                                ui.wire_from = -1;
                                ui.wire_handle.clear();
                            } else {
                                ui.wire_from = h.i;
                                ui.wire_handle = h.key;
                                ui.wire_from_src = h.src;
                                ui.wire_sel = h.i;
                                ui.drag_mx = mx;
                                ui.drag_my = my;
                                ui.say("drag to a connector  (" + h.key + ")");
                            }
                            redraw();
                        } else if (h.kind == Hit::WireNode) {
                            ui.tool_pick_id.clear();
                            if (ui.wire_from >= 0) {
                                if (!ui.wire_handle.empty()) {
                                    auto& tgt = ui.snap.wire_nodes[h.i];
                                    Rect nr{0, 0, node_w(tgt), node_h(tgt)};
                                    std::string hb;
                                    bool hs = false;
                                    bool found = false;
                                    for (auto& p : layout_ports(tgt, nr)) {
                                        if (ui.wire_from_src && !p.source && handles_ok(ui.wire_handle, p.handle)) {
                                            hb = p.handle; hs = false; found = true; break;
                                        }
                                        if (!ui.wire_from_src && p.source && handles_ok(p.handle, ui.wire_handle)) {
                                            hb = p.handle; hs = true; found = true; break;
                                        }
                                    }
                                    if (found) ui.connect_ports(ui.wire_from, ui.wire_handle, ui.wire_from_src, h.i, hb, hs);
                                    else ui.connect_nodes(ui.wire_from, h.i);
                                } else {
                                    ui.connect_nodes(ui.wire_from, h.i);
                                }
                                ui.wire_from = -1;
                                ui.wire_handle.clear();
                            } else {
                                double tnow = ui.now();
                                bool dbl = (h.i == ui.last_click_i && (tnow - ui.last_click_t) < 0.45);
                                ui.last_click_i = h.i;
                                ui.last_click_t = tnow;
                                auto& nd = ui.snap.wire_nodes[h.i];
                                bool already = ui.wire_marked.count(nd.id) != 0;
                                if (shift) {
                                    if (already) ui.wire_marked.erase(nd.id);
                                    else ui.wire_marked.insert(nd.id);
                                } else if (!already) {
                                    ui.wire_marked.clear();
                                    ui.wire_marked.insert(nd.id);
                                }
                                ui.wire_sel = h.i;
                                ui.edge_sel = -1;
                                if (dbl) {
                                    ui.wire_inspect = h.i;
                                    ui.inspect_scroll = 0;
                                    ui.say("node editor on the right — knobs turn like the website");
                                }
                                if (!shift || ui.wire_marked.count(nd.id)) {
                                    ui.node_drag = true;
                                    ui.drag_moved = false;
                                    ui.drag_i = h.i;
                                    ui.drag_mx = mx;
                                    ui.drag_my = my;
                                    ui.drag_nx = nd.x;
                                    ui.drag_ny = nd.y;
                                    ui.drag_orig.clear();
                                    for (int gi = 0; gi < static_cast<int>(ui.snap.wire_nodes.size()); ++gi) {
                                        if (!ui.wire_marked.count(ui.snap.wire_nodes[gi].id)) continue;
                                        ui.drag_orig.push_back({gi, ui.snap.wire_nodes[gi].x, ui.snap.wire_nodes[gi].y});
                                    }
                                    if (ui.drag_orig.size() > 1)
                                        ui.say("moving " + std::to_string((int)ui.drag_orig.size()) + " marked nodes");
                                }
                            }
                            redraw();
                        } else if (h.kind == Hit::WireEdge) {
                            ui.edge_sel = h.i;
                            ui.wire_sel = -1;
                            redraw();
                        } else if (h.kind == Hit::ToolCat) {
                            ui.tb_fold ^= (1u << h.i);
                            redraw();
                        } else if (h.kind == Hit::ToolItem) {
                            ui.tool_drag = true;
                            ui.tool_drag_moved = false;
                            ui.tool_drag_i = h.i;
                            ui.drag_mx = mx;
                            ui.drag_my = my;
                            ui.mx_last = mx;
                            ui.my_last = my;
                            if (h.i >= 0 && h.i < (int)ui.snap.tools.size())
                                ui.tool_pick_id = ui.snap.tools[h.i].id;
                            redraw();
                        } else if (h.kind == Hit::WireBoard) {
                            ui.wire_board = h.i;
                            const char* boards[] = {"desk", "home", "health"};
                            ui.api->load_wire(ui.snap, boards[ui.wire_board]);
                            ui.snap_loaded_nodes();
                            ui.dirty = false;
                            ui.wire_fitted = false;
                            ui.wire_sel = -1;
                            ui.wire_marked.clear();
                            redraw();
                        } else if (h.kind == Hit::WireAct) {
                            if (h.i == 0) ui.do_undo();
                            else if (h.i == 1) ui.do_redo();
                            else if (h.i == 2) ui.wire_fitted = false;
                            else if (h.i == 3) {
                                run_compile(false);
                            } else if (h.i == 4) {
                                if (ui.snap.compile.message.empty())
                                    run_compile(false);
                                else {
                                    ui.compile_win = true;
                                    ui.compile_scroll = 0;
                                    ui.say("read this report — Integrate is at the bottom if COMPILE OK");
                                }
                            } else if (h.i == 5) {
                                auto msg = ui.api->save_snapshot(ui.snap);
                                ui.say(msg);
                                if (msg.find("saved") != std::string::npos) {
                                    ui.dirty = false;
                                    ui.last_wire = ui.now();
                                }
                            } else if (h.i == 6) {
                                auto msg = ui.api->save_snapshot(ui.snap);
                                ui.say(msg + "  ·  XML download stays on website /wire");
                                if (msg.find("saved") != std::string::npos) {
                                    ui.dirty = false;
                                    ui.last_wire = ui.now();
                                }
                            }
                            else if (h.i == 7) {
                                const char* boards[] = {"desk", "home", "health"};
                                ui.api->load_wire(ui.snap, boards[ui.wire_board]);
                                ui.snap_loaded_nodes();
                                ui.dirty = false;
                                ui.wire_marked.clear();
                                ui.wire_sel = -1;
                                ui.say("reloaded live canvas");
                            }
                            redraw();
                        }
                        break;
                    }
                    if (ui.api_edit && !api_click) {
                        cancel_api();
                        redraw();
                    }
                    if (!hit && ui.page == PgWire && inside(ui.canvas_r, mx, my)) {
                        if (ui.wire_from >= 0) {
                            ui.wire_from = -1;
                            ui.say("wire cancelled");
                            redraw();
                        } else if (alt) {
                            ui.panning = true;
                            ui.pan_mx = mx;
                            ui.pan_my = my;
                            ui.pan_sx = ui.pan_x;
                            ui.pan_sy = ui.pan_y;
                        } else {
                            ui.marquee_on = true;
                            ui.marquee_x0 = mx;
                            ui.marquee_y0 = my;
                            ui.marquee_r = {mx, my, 1, 1};
                            if (!shift) {
                                ui.wire_marked.clear();
                                ui.wire_sel = -1;
                                ui.edge_sel = -1;
                            }
                            ui.marquee_keep = ui.wire_marked;
                        }
                    }
                    }
                }
            } else if (e.type == MotionNotify) {
                ui.mx_last = e.xmotion.x;
                ui.my_last = e.xmotion.y;
                if (ui.knob_drag) {
                    double px = (e.xmotion.state & ShiftMask) ? 4.2 : 1.15;
                    double delta = (e.xmotion.x - ui.knob_mx - (e.xmotion.y - ui.knob_my)) / px;
                    double span = (ui.knob_max > ui.knob_min) ? (ui.knob_max - ui.knob_min) : 1;
                    ui.knob_live = snap_ctrl(ui.knob_start + (delta / 140.0) * span,
                                             ui.knob_min, ui.knob_max, ui.knob_step);
                    if (ui.knob_bot >= 0 && ui.knob_bot < (int)ui.snap.bots.size() && !ui.knob_key.empty()) {
                        CtrlField fk;
                        fk.key = ui.knob_key;
                        fk.kind = ui.knob_kind;
                        fk.minv = ui.knob_min;
                        fk.maxv = ui.knob_max;
                        fk.step = ui.knob_step;
                        auto val = store_ctrl(fk, ui.knob_live);
                        ui.snap.bots[ui.knob_bot].controller[ui.knob_key] = val;
                        if (ui.knob_key == "rebuy_pct")
                            ui.snap.bots[ui.knob_bot].controller.erase("rebuy_px");
                        // RAM only while dragging — persist on release. File/sql
                        // writes here made the chart overlay lag the knob.
                        if (ui.now() - ui.knob_live_sent >= 0.008) {
                            ui.knob_live_sent = ui.now();
                            ui.api->post_live_knobs(ui.snap.bots[ui.knob_bot].id,
                                                    ui.snap.bots[ui.knob_bot].controller);
                        }
                    }
                    redraw();
                } else if (ui.tool_drag) {
                    if (std::abs(e.xmotion.x - ui.drag_mx) + std::abs(e.xmotion.y - ui.drag_my) > 4)
                        ui.tool_drag_moved = true;
                    redraw();
                } else if (ui.annot_size_drag && ui.drag_i >= 0 && ui.drag_i < static_cast<int>(ui.snap.wire_nodes.size())) {
                    double dx = (e.xmotion.x - ui.drag_mx) / std::max(0.08, ui.zoom);
                    double dy = (e.xmotion.y - ui.drag_my) / std::max(0.08, ui.zoom);
                    if (std::fabs(dx) + std::fabs(dy) > 1) {
                        if (!ui.drag_moved) ui.push_hist();
                        ui.drag_moved = true;
                    }
                    int nw = std::max(64, ui.annot_sw + (int)dx);
                    int nh = std::max(48, ui.annot_sh + (int)dy);
                    if (ui.wire_snap) {
                        nw = (int)snap_grid(nw);
                        nh = (int)snap_grid(nh);
                        if (nw < 64) nw = 96;
                        if (nh < 48) nh = 96;
                    }
                    ui.snap.wire_nodes[ui.drag_i].extra = std::to_string(nw) + "x" + std::to_string(nh);
                    redraw();
                } else if (ui.marquee_on) {
                    ui.marquee_r = norm_rect(ui.marquee_x0, ui.marquee_y0, e.xmotion.x, e.xmotion.y);
                    ui.wire_marked = ui.marquee_keep;
                    if (ui.marquee_r.w > 4 || ui.marquee_r.h > 4)
                        ui.mark_apply_box(ui.marquee_r, true);
                    redraw();
                } else if (ui.node_drag && ui.drag_i >= 0 && ui.drag_i < static_cast<int>(ui.snap.wire_nodes.size())) {
                    double dx = (e.xmotion.x - ui.drag_mx) / std::max(0.08, ui.zoom);
                    double dy = (e.xmotion.y - ui.drag_my) / std::max(0.08, ui.zoom);
                    if (std::fabs(dx) + std::fabs(dy) > 1) {
                        if (!ui.drag_moved) ui.push_hist();
                        ui.drag_moved = true;
                    }
                    double nx = ui.drag_nx + dx;
                    double ny = ui.drag_ny + dy;
                    if (ui.wire_snap) {
                        nx = snap_grid(nx);
                        ny = snap_grid(ny);
                    }
                    double odx = nx - ui.drag_nx;
                    double ody = ny - ui.drag_ny;
                    if (ui.drag_orig.empty()) {
                        ui.snap.wire_nodes[ui.drag_i].x = nx;
                        ui.snap.wire_nodes[ui.drag_i].y = ny;
                    } else {
                        int nn = static_cast<int>(ui.snap.wire_nodes.size());
                        for (auto& g : ui.drag_orig) {
                            if (g.i < 0 || g.i >= nn) continue;
                            ui.snap.wire_nodes[g.i].x = g.x + odx;
                            ui.snap.wire_nodes[g.i].y = g.y + ody;
                        }
                    }
                    redraw();
                } else if (ui.panning) {
                    ui.pan_x = ui.pan_sx - (e.xmotion.x - ui.pan_mx) / ui.zoom;
                    ui.pan_y = ui.pan_sy - (e.xmotion.y - ui.pan_my) / ui.zoom;
                    redraw();
                } else if (ui.wire_from >= 0) {
                    redraw();
                } else {
                    std::string tip;
                    Rect hr{};
                    int hb = -1;
                    for (int hi = static_cast<int>(ui.hits.size()) - 1; hi >= 0; --hi) {
                        auto& h = ui.hits[hi];
                        if (!inside(h.r, ui.mx_last, ui.my_last)) continue;
                        if (h.kind == Hit::WireNode || h.kind == Hit::WireEdge) continue;
                        if (hb < 0 && h.i >= 0 &&
                            (h.kind == Hit::Hunter || h.kind == Hit::CoinBias ||
                             h.kind == Hit::CoinClose || h.kind == Hit::CoinOpen100 ||
                             h.kind == Hit::Restart || h.kind == Hit::HunterGate || h.kind == Hit::Sl ||
                             h.kind == Hit::Lev ||
                             (h.kind == Hit::Chart && h.i >= 0)))
                            hb = h.i;
                        if (tip.empty()) {
                            tip = action_tip(ui, h);
                            if (!tip.empty()) hr = h.r;
                        }
                    }
                    bool changed = tip != ui.hover_tip || hb != ui.hover_bot
                        || hr.x != ui.hover_r.x || hr.y != ui.hover_r.y
                        || hr.w != ui.hover_r.w || hr.h != ui.hover_r.h;
                    if (changed) {
                        ui.hover_tip = tip;
                        ui.hover_r = hr;
                        ui.hover_bot = hb;
                        XDefineCursor(dpy, win, tip.empty() ? cur_arrow : cur_hand);
                        redraw();
                    }
                }
            } else if (e.type == LeaveNotify) {
                if (!ui.hover_tip.empty() || ui.hover_r.w || ui.hover_bot >= 0) {
                    ui.hover_tip.clear();
                    ui.hover_r = {};
                    ui.hover_bot = -1;
                    XDefineCursor(dpy, win, cur_arrow);
                    redraw();
                }
            } else if (e.type == ButtonRelease) {
                int mx = e.xbutton.x, my = e.xbutton.y;
                if (ui.knob_drag) {
                    auto f = field_of(ui.knob_bot, ui.knob_key);
                    if (!f.key.empty()) post_knob(ui.knob_bot, f, ui.knob_live);
                    if (std::fabs(ui.knob_live - ui.knob_start) > 1e-6)
                        ui.last_knob_t = 0;
                    ui.knob_drag = false;
                    ui.knob_key.clear();
                    ui.knob_bot = -1;
                    redraw();
                } else if (ui.tool_drag) {
                    if (ui.tool_drag_i >= 0 && ui.tool_drag_i < static_cast<int>(ui.snap.tools.size())) {
                        auto& t = ui.snap.tools[ui.tool_drag_i];
                        ui.tool_pick_id = t.id;
                        int found = find_node(ui.snap, t.id);
                        // A still click reads the tool. A drag onto a controller asks.
                        // A drag onto empty canvas places it.
                        if (inside(ui.canvas_r, mx, my) && ui.tool_drag_moved) {
                            if (t.unique && found >= 0) {
                                ui.wire_sel = found;
                                ui.say(t.label + " already on canvas");
                            } else {
                                int under = ui.node_under_canvas(mx, my);
                                int tgt = ui.drop_replace_target(t, under);
                                if (tgt >= 0) ui.open_replace(t, tgt);
                                else {
                                    double wx = ui.pan_x + (mx - ui.canvas_r.x) / std::max(0.08, ui.zoom);
                                    double wy = ui.pan_y + (my - ui.canvas_r.y) / std::max(0.08, ui.zoom);
                                    ui.add_tool_at(t, wx, wy);
                                }
                            }
                        } else if (!ui.tool_drag_moved && found >= 0) {
                            ui.wire_sel = found;
                            auto& n = ui.snap.wire_nodes[found];
                            ui.pan_x = n.x - (ui.canvas_r.w / ui.zoom) * 0.35;
                            ui.pan_y = n.y - (ui.canvas_r.h / ui.zoom) * 0.35;
                        } else if (!ui.tool_drag_moved) {
                            ui.say(t.label + "  -  drag onto the canvas to place");
                        } else {
                            ui.say("drop on the canvas to add");
                        }
                    }
                    ui.tool_drag = false;
                    ui.tool_drag_i = -1;
                    redraw();
                } else if (ui.wire_from >= 0) {
                    bool moved = std::abs(mx - ui.drag_mx) + std::abs(my - ui.drag_my) > 12;
                    if (moved) {
                        bool done = false;
                        for (int hi = static_cast<int>(ui.hits.size()) - 1; hi >= 0; --hi) {
                            auto& h = ui.hits[hi];
                            if (!inside(h.r, mx, my)) continue;
                            if (h.kind == Hit::WirePort) {
                                ui.connect_ports(ui.wire_from, ui.wire_handle, ui.wire_from_src, h.i, h.key, h.src);
                                done = true;
                            } else if (h.kind == Hit::WireNode) {
                                ui.connect_nodes(ui.wire_from, h.i);
                                done = true;
                            }
                            if (done) break;
                        }
                        ui.wire_from = -1;
                        ui.wire_handle.clear();
                        if (!done) ui.say("wire cancelled");
                        redraw();
                    }
                }
                if (ui.marquee_on) {
                    ui.marquee_r = norm_rect(ui.marquee_x0, ui.marquee_y0, mx, my);
                    bool dragged = ui.marquee_r.w > 6 || ui.marquee_r.h > 6;
                    if (dragged) {
                        ui.wire_marked = ui.marquee_keep;
                        ui.mark_apply_box(ui.marquee_r, true);
                        if (ui.wire_marked.size() > 1)
                            ui.say(std::to_string((int)ui.wire_marked.size())
                                   + " marked — drag any of them to move the group");
                        else if (ui.wire_marked.size() == 1)
                            ui.say("1 marked");
                        else
                            ui.say("nothing in that box");
                    } else if (ui.marquee_keep.empty()) {
                        ui.wire_marked.clear();
                        ui.wire_sel = -1;
                    }
                    ui.marquee_on = false;
                    ui.marquee_r = {};
                    redraw();
                }
                if (ui.node_drag && ui.drag_moved && ui.drag_i >= 0) {
                    ui.dirty = true;
                    ui.snap.compile.can_integrate = false;
                    ui.api->save_layout(ui.snap);
                    if (ui.drag_orig.size() > 1)
                        ui.say("moved " + std::to_string((int)ui.drag_orig.size()) + " nodes");
                }
                if (ui.annot_size_drag && ui.drag_moved && ui.drag_i >= 0) {
                    ui.dirty = true;
                    auto& an = ui.snap.wire_nodes[ui.drag_i];
                    if (an.type == "frame" || an.type == "circle") {
                        auto msg = ui.api->save_snapshot(ui.snap);
                        if (msg.find("saved") != std::string::npos) {
                            ui.dirty = false;
                            ui.last_wire = ui.now();
                        }
                        ui.say(msg);
                    }
                }
                ui.node_drag = false;
                ui.annot_size_drag = false;
                ui.panning = false;
                ui.drag_i = -1;
                ui.drag_orig.clear();
            }
        }
        if (ui.page == PgHunters && !ui.knob_drag && !ui.knob_edit
            && ui.now() - ui.live_knob_pull > 0.05) {
            ui.live_knob_pull = ui.now();
            if (ui.sel >= 0 && ui.sel < (int)ui.snap.bots.size()) {
                auto& pb = ui.snap.bots[ui.sel];
                if (ui.api->pull_live_knobs(pb))
                    redraw();
            }
        }
        poll_api();
        if (ui.api_edit && ui.page == PgSystem && ui.now() - api_blink > 0.4) {
            api_blink = ui.now();
            redraw();
        }
        if (ui.now() - ui.last_poll > 2.5) {
            ui.last_poll = ui.now();
            if (!ui.api_edit && !ui.api_busy) show_pub();
            if (ui.audit_win || ui.compile_win) {
                /* overlay owns the snap — home poll would wipe audit/compile rows */
            } else if (!ui.knob_drag && !ui.knob_edit) {
                ui.refresh(false);
                redraw();
            }
        }
        if (ui.page == PgWire && !ui.dirty && ui.now() - ui.last_wire > 8.0) {
            ui.last_wire = ui.now();
            const char* boards[] = {"desk", "home", "health"};
            ui.api->load_wire(ui.snap, boards[ui.wire_board]);
            ui.snap_loaded_nodes();
            redraw();
        }
        struct timespec slp{0, 16 * 1000 * 1000};
        nanosleep(&slp, nullptr);
    }

    for (auto& kv : chart_pids)
        if (kv.second > 0) kill(kv.second, SIGTERM);

    XFreePixmap(dpy, buf);
    XFreeGC(dpy, gc);
    if (fs_tiny && fs_tiny != fs) XFreeFont(dpy, fs_tiny);
    if (fs) XFreeFont(dpy, fs);
    XDestroyWindow(dpy, win);
    XCloseDisplay(dpy);
    return 0;
}
