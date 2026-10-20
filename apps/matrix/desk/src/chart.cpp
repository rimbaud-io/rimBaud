// Copyright 2026 Laurence Sterne
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

// Parallel chart window next to the rimBaud matrix.
// Candles: Hyperliquid REST (uses IP tokens) or Binance public klines (no HL tokens).
// IN / OUT overlays come from local Spring/Flask /api/home — not HL REST.

#include "http.hpp"
#include "https.hpp"

#include <nlohmann/json.hpp>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/keysym.h>
#include <X11/cursorfont.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <map>
#include <string>
#include <sys/file.h>
#include <unistd.h>
#include <vector>

using nlohmann::json;

struct Rect { int x, y, w, h; };
bool inside(const Rect& r, int x, int y) {
    return x >= r.x && y >= r.y && x < r.x + r.w && y < r.y + r.h;
}

struct Candle {
    long long t = 0;
    double o = 0, h = 0, l = 0, c = 0, v = 0;
};

struct Overlay {
    std::string bot, bot_id, coin, side, color = "#7ecbff";
    int cr = 126, cg = 203, cb = 255;
    double entry = 0, mark = 0, sl_roe = 20;
    double take_profit = 0;   // price fraction, e.g. 0.02
    double trail_act = 0;     // price fraction where trail arms
    double trail_delta = 22;  // Trail % of ARM (8–40), or legacy price fraction
    double rebuy_pct = 0.0018;  // NEXT IN price fraction (0.18% → 0.0018)
    double tp_roe = 0;        // win ROE %, e.g. 93
    double sell_px = 0;       // parsed $ from next.sell (TP / trail / moon)
    double rebuy_px = 0;      // parsed $ from next.rebuy
    int lev = 1;
    bool ghost = false;       // projected clip from knobs (no live size)
    bool is_short() const { return side == "SHORT"; }
    double frac(double v) const {
        if (v > 1.0) return v / 100.0;
        return v;
    }
    double sl_px() const {
        if (entry <= 0 || lev < 1) return 0;
        double d = (sl_roe / 100.0) / static_cast<double>(lev);
        return is_short() ? entry * (1.0 + d) : entry * (1.0 - d);
    }
    double arm_px() const {
        double a = frac(trail_act);
        if (entry <= 0 || a <= 0) return 0;
        return is_short() ? entry * (1.0 - a) : entry * (1.0 + a);
    }
    double trail_price_frac() const {
        double arm = frac(trail_act);
        double t = trail_delta;
        if (t >= 8.0) return arm * (t / 100.0);
        if (t > 0.05) return arm * t;
        return frac(t);
    }
    double trail_now_px() const {
        double d = trail_price_frac();
        double arm = arm_px();
        if (entry <= 0 || d <= 0 || mark <= 0 || arm <= 0) return 0;
        bool armed = is_short() ? (mark <= arm) : (mark >= arm);
        if (!armed) return 0;
        return is_short() ? mark * (1.0 + d) : mark * (1.0 - d);
    }
    double win_px() const {
        if (entry <= 0) return 0;
        // ROE clip (Only Up / Helliot / Surfn / Shorty) wins over a leftover price TP.
        if (tp_roe > 0 && lev > 0) {
            double d = (tp_roe / 100.0) / static_cast<double>(lev);
            return is_short() ? entry * (1.0 - d) : entry * (1.0 + d);
        }
        double tp = frac(take_profit);
        if (tp > 0 && tp < 0.5) {
            return is_short() ? entry * (1.0 - tp) : entry * (1.0 + tp);
        }
        if (sell_px > 0) {
            if (is_short() ? sell_px < entry : sell_px > entry) return sell_px;
        }
        return is_short() ? entry * 0.98 : entry * 1.02;
    }
    double next_in_from_pct() const {
        double r = frac(rebuy_pct);
        if (r <= 0 || entry <= 0) return 0;
        double ref = ghost ? entry : win_px();
        if (ref <= 0) ref = entry;
        return is_short() ? ref * (1.0 + r) : ref * (1.0 - r);
    }
    // Absolute wait price. Frozen after drag / first apply so it does not
    // trail last candle (that made the line walk down as price approached).
    double next_in_px() const {
        double computed = next_in_from_pct();
        if (rebuy_px > 0 && computed > 0 && entry > 0
            && std::fabs(rebuy_px - computed) > entry * 0.03)
            return computed;
        if (rebuy_px > 0) return rebuy_px;
        return computed;
    }
};

static double first_dollar(const std::string& s) {
    auto p = s.find('$');
    if (p == std::string::npos) return 0;
    try { return std::stod(s.substr(p + 1)); } catch (...) { return 0; }
}

static double first_win_dollar(const std::string& s, double entry, bool sh) {
    if (entry <= 0) return first_dollar(s);
    size_t p = 0;
    while ((p = s.find('$', p)) != std::string::npos) {
        double v = 0;
        try { v = std::stod(s.substr(p + 1)); } catch (...) { v = 0; }
        p++;
        if (v <= 0) continue;
        if (sh ? v < entry : v > entry) return v;
    }
    return 0;
}

static void color_from_id(const std::string& id, int& r, int& g, int& b) {
    unsigned h = 2166136261u;
    for (unsigned char c : id) h = (h ^ c) * 16777619u;
    r = 70 + (int)(h & 127);
    g = 70 + (int)((h >> 8) & 127);
    b = 70 + (int)((h >> 16) & 127);
}

static void parse_hex_rgb(const std::string& s, int& r, int& g, int& b) {
    if (s.size() >= 7 && s[0] == '#')
        sscanf(s.c_str() + 1, "%02x%02x%02x", &r, &g, &b);
}

struct Hit { int kind; int i; Rect r; }; // 0 src, 1 coin, 2 iv, 3 close, 4 hunter, 5 line

static bool apply_line_price(Overlay& o, int line, double px) {
    if (o.entry <= 0 || px <= 0) return false;
    if (line == 0) {
        double d = o.is_short() ? (px / o.entry - 1.0) : (1.0 - px / o.entry);
        double sl = d * 100.0 * static_cast<double>(o.lev < 1 ? 1 : o.lev);
        if (sl < 5) sl = 5;
        if (sl > 80) sl = 80;
        o.sl_roe = sl;
        return true;
    }
    if (line == 1) {
        o.sell_px = 0;
        if (o.tp_roe > 0 || o.take_profit <= 0) {
            if (o.entry <= 0 || o.lev < 1) return false;
            double d = o.is_short() ? (1.0 - px / o.entry) : (px / o.entry - 1.0);
            if (d < 0) d = 0;
            double roe = d * static_cast<double>(o.lev) * 100.0;
            if (roe < 1) roe = 1;
            if (roe > 2000) roe = 2000;
            o.tp_roe = roe;
            o.take_profit = 0;
        } else {
            double tp = o.is_short() ? (1.0 - px / o.entry) : (px / o.entry - 1.0);
            if (tp < 0.008) tp = 0.008;
            if (tp > 0.06) tp = 0.06;
            o.take_profit = tp;
        }
        return true;
    }
    if (line == 2) {
        double a = o.is_short() ? (1.0 - px / o.entry) : (px / o.entry - 1.0);
        if (a < 0.004) a = 0.004;
        if (a > 0.04) a = 0.04;
        o.trail_act = a;
        return true;
    }
    if (line == 3) {
        if (o.mark <= 0) return false;
        double d = o.is_short() ? (px / o.mark - 1.0) : (1.0 - px / o.mark);
        double arm = o.frac(o.trail_act);
        if (arm <= 1e-9) return false;
        double pct = d / arm * 100.0;
        if (pct < 8) pct = 8;
        if (pct > 40) pct = 40;
        o.trail_delta = pct;
        return true;
    }
    if (line == 4) {
        double ref = o.ghost ? o.entry : o.win_px();
        if (ref <= 0) ref = o.entry;
        if (ref <= 0) return false;
        double r = o.is_short() ? (px / ref - 1.0) : (1.0 - px / ref);
        if (r < 0.0005) r = 0.0005;
        if (r > 0.008) r = 0.008;
        o.rebuy_pct = r;
        o.rebuy_px = px;
        return true;
    }
    return false;
}

static double mono_now() {
    timespec ts{};
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void push_live_overlay(const Overlay& o, bool force = false) {
    if (o.bot_id.empty()) return;
    static double last = 0;
    double n = mono_now();
    if (!force && n - last < 0.008) return;
    last = n;
    json body = {
        {"id", o.bot_id},
        {"trail_activation", o.trail_act},
        {"trail_delta", o.trail_delta},
        {"sl_roe_pct", o.sl_roe},
        {"rebuy_pct", o.rebuy_pct},
        {"rebuy_px", o.rebuy_px},
    };
    if (o.tp_roe > 0) {
        body["scale_roe_pct"] = o.tp_roe;
        body["tp_roe_pct"] = o.tp_roe;
        body["take_profit"] = 0;
    } else {
        body["take_profit"] = o.take_profit;
    }
    http_request("127.0.0.1", 5004, "POST", "/api/live/knobs", body.dump(), 80);
}

static const char* line_tag(int line) {
    switch (line) {
        case 0: return "SL";
        case 1: return "WIN";
        case 2: return "ARM";
        case 3: return "TRAIL";
        case 4: return "NEXT IN";
        default: return "?";
    }
}

static int g_lock_fd = -1;
static std::string chart_lock_path(const std::string& coin) {
    std::string o;
    for (unsigned char c : coin) {
        if (c >= 'a' && c <= 'z') o += char(c - 32);
        else if ((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')) o += char(c);
    }
    if (o.empty()) o = "BTC";
    return "/tmp/rimbaud-chart-" + o + ".lock";
}
static bool take_chart_lock(const std::string& coin) {
    g_lock_fd = open(chart_lock_path(coin).c_str(), O_CREAT | O_RDWR, 0644);
    if (g_lock_fd < 0) return true;
    if (flock(g_lock_fd, LOCK_EX | LOCK_NB) != 0) {
        close(g_lock_fd);
        g_lock_fd = -1;
        return false;
    }
    return true;
}

static int x_ignore(Display*, XErrorEvent*) { return 0; }
static int x_die(Display*) { _exit(0); }

static unsigned long rgb(Display* d, Colormap cm, int r, int g, int b) {
    r = std::max(0, std::min(255, r));
    g = std::max(0, std::min(255, g));
    b = std::max(0, std::min(255, b));
    char spec[16];
    snprintf(spec, sizeof(spec), "#%02x%02x%02x", r, g, b);
    XColor xc{};
    if (XParseColor(d, cm, spec, &xc) && XAllocColor(d, cm, &xc))
        return xc.pixel;
    return WhitePixel(d, DefaultScreen(d));
}

static unsigned long shade(Display* d, Colormap cm, int r, int g, int b, double k) {
    auto clip = [](int x) { return std::max(0, std::min(255, x)); };
    if (k <= 1.0) {
        r = clip(static_cast<int>(r * k));
        g = clip(static_cast<int>(g * k));
        b = clip(static_cast<int>(b * k));
    } else {
        double t = std::min(1.0, k - 1.0);
        r = clip(static_cast<int>(r + (255 - r) * t));
        g = clip(static_cast<int>(g + (255 - g) * t));
        b = clip(static_cast<int>(b + (255 - b) * t));
    }
    return rgb(d, cm, r, g, b);
}

static std::string ascii(const std::string& s, int n = 0) {
    std::string o;
    for (unsigned char c : s) if (c >= 32 && c < 127) o += char(c);
    if (n > 0 && (int)o.size() > n) o.resize((size_t)n);
    return o;
}

static std::string fmt_px(double p) {
    if (p <= 0) return "-";
    char b[32];
    if (p >= 100) snprintf(b, sizeof(b), "%.2f", p);
    else if (p >= 1) snprintf(b, sizeof(b), "%.4f", p);
    else snprintf(b, sizeof(b), "%.6f", p);
    return b;
}

static json parse_or(const std::string& s) {
    try { return json::parse(s.empty() ? "[]" : s); } catch (...) { return json::array(); }
}
static json parse_obj(const std::string& s) {
    try {
        auto j = json::parse(s.empty() ? "{}" : s);
        return j.is_object() ? j : json::object();
    } catch (...) { return json::object(); }
}
static double jn(const json& j, const char* k, double d = 0) {
    if (!j.contains(k) || j[k].is_null()) return d;
    try {
        if (j[k].is_number()) return j[k].get<double>();
        if (j[k].is_string()) return std::stod(j[k].get<std::string>());
    } catch (...) {}
    return d;
}

static long long now_ms() {
    return static_cast<long long>(time(nullptr)) * 1000LL;
}

static int iv_ms(const std::string& iv) {
    if (iv == "1m") return 60 * 1000;
    if (iv == "5m") return 5 * 60 * 1000;
    if (iv == "15m") return 15 * 60 * 1000;
    if (iv == "1h") return 60 * 60 * 1000;
    if (iv == "4h") return 4 * 60 * 60 * 1000;
    return 15 * 60 * 1000;
}

static std::string binance_sym(const std::string& coin) {
    std::string u = coin;
    for (char& ch : u) if (ch >= 'a' && ch <= 'z') ch = char(ch - 32);
    if (u == "POL") return "POLUSDT";
    return u + "USDT";
}

static std::vector<Candle> parse_binance(const std::string& body) {
    std::vector<Candle> out;
    json j = parse_or(body);
    if (!j.is_array()) return out;
    for (auto& row : j) {
        if (!row.is_array() || row.size() < 6) continue;
        Candle c;
        try {
            auto num = [&](const json& x) -> double {
                if (x.is_number()) return x.get<double>();
                if (x.is_string()) return std::stod(x.get<std::string>());
                return 0;
            };
            if (row[0].is_number()) c.t = row[0].get<long long>();
            else if (row[0].is_string()) c.t = std::stoll(row[0].get<std::string>());
            c.o = num(row[1]); c.h = num(row[2]); c.l = num(row[3]);
            c.c = num(row[4]); c.v = num(row[5]);
        } catch (...) { continue; }
        out.push_back(c);
    }
    return out;
}

static std::vector<Candle> parse_hl(const std::string& body) {
    std::vector<Candle> out;
    json j = parse_or(body);
    if (!j.is_array()) return out;
    for (auto& row : j) {
        if (!row.is_object()) continue;
        Candle c;
        try {
            c.t = row.value("t", 0LL);
            auto num = [&](const char* k) {
                if (!row.contains(k)) return 0.0;
                if (row[k].is_number()) return row[k].get<double>();
                if (row[k].is_string()) return std::stod(row[k].get<std::string>());
                return 0.0;
            };
            c.o = num("o"); c.h = num("h"); c.l = num("l"); c.c = num("c"); c.v = num("v");
        } catch (...) { continue; }
        if (c.t) out.push_back(c);
    }
    return out;
}

static std::vector<Candle> fetch_binance(const std::string& coin, const std::string& iv, std::string& err) {
    auto path = "/api/v3/klines?symbol=" + binance_sym(coin) + "&interval=" + iv + "&limit=96";
    auto r = https_request("api.binance.com", 443, "GET", path);
    if (!r.error.empty()) { err = r.error; return {}; }
    if (r.status != 200) { err = "binance HTTP " + std::to_string(r.status); return {}; }
    err.clear();
    return parse_binance(r.body);
}

static std::vector<Candle> fetch_hl(const std::string& coin, const std::string& iv, std::string& err) {
    long long end = now_ms();
    long long start = end - 96LL * iv_ms(iv);
    json body = {
        {"type", "candleSnapshot"},
        {"req", {{"coin", coin}, {"interval", iv}, {"startTime", start}, {"endTime", end}}},
    };
    auto r = https_request("api.hyperliquid.xyz", 443, "POST", "/info", body.dump());
    if (!r.error.empty()) { err = r.error; return {}; }
    if (r.status != 200) { err = "HL HTTP " + std::to_string(r.status) + " (REST tokens)"; return {}; }
    err.clear();
    return parse_hl(r.body);
}

static bool apply_pmm_live(std::vector<Overlay>& ov, const std::string& bot_id) {
    if (bot_id.empty()) return false;
    auto r = http_request("127.0.0.1", 5004, "GET",
                          "/api/live/knobs?id=" + bot_id, "", 80);
    if (r.status != 200 || r.error.size()) return false;
    json j;
    try { j = json::parse(r.body); } catch (...) { return false; }
    if (!j.is_object()) return false;
    if (j.contains("controller") && j["controller"].is_object()) j = j["controller"];
    bool ch = false;
    auto setd = [&](double& dst, double v) {
        if (v <= 0) return;
        if (std::fabs(dst - v) > 1e-12) { dst = v; ch = true; }
    };
    for (auto& o : ov) {
        if (o.bot_id != bot_id && o.bot != bot_id) continue;
        setd(o.take_profit, jn(j, "take_profit", o.take_profit));
        setd(o.trail_act, jn(j, "trail_activation", o.trail_act));
        setd(o.trail_delta, jn(j, "trail_delta", o.trail_delta));
        {
            double nsl = jn(j, "sl_roe_pct", 0);
            if (nsl > 0) setd(o.sl_roe, nsl);
        }
        {
            double nroe = jn(j, "scale_roe_pct", 0);
            if (nroe <= 0) nroe = jn(j, "tp_roe_pct", o.tp_roe);
            setd(o.tp_roe, nroe);
        }
        double old_r = o.rebuy_pct;
        double new_r = jn(j, "rebuy_pct", o.rebuy_pct);
        double jpx = jn(j, "rebuy_px", 0);
        bool rchg = std::fabs(new_r - old_r) > 1e-8;
        setd(o.rebuy_pct, new_r);
        if (rchg) {
            o.rebuy_pct = new_r;
            if (jpx > 0) setd(o.rebuy_px, jpx);
            else {
                double frozen = o.next_in_from_pct();
                if (frozen > 0) setd(o.rebuy_px, frozen);
            }
        } else if (jpx > 0) {
            setd(o.rebuy_px, jpx);
        }
        o.sell_px = 0;
    }
    return ch;
}

static json load_home() {
    auto r = http_request("127.0.0.1", 5004, "GET", "/api/home");
    if (r.status == 200 && r.error.empty()) return parse_obj(r.body);
    return json::object();
}

static std::string norm_coin(std::string c) {
    while (!c.empty() && (c.back() == ' ' || c.back() == '\n')) c.pop_back();
    for (char& ch : c) if (ch >= 'a' && ch <= 'z') ch = static_cast<char>(ch - 32);
    auto dash = c.find('-');
    if (dash != std::string::npos) c = c.substr(0, dash);
    if (c.size() > 4 && c.substr(c.size() - 4) == "USDT") c = c.substr(0, c.size() - 4);
    if (c.size() > 3 && c.substr(c.size() - 3) == "USD") c = c.substr(0, c.size() - 3);
    return c;
}

static int lev_for_coin(const std::string& coin) {
    if (coin == "BTC") return 40;
    if (coin == "ETH") return 25;
    if (coin == "LTC") return 10;
    if (coin == "TAO") return 5;
    return 10;
}

static void fill_ctrl(Overlay& o, const json& b) {
    o.sl_roe = jn(b, "sl_roe_pct", o.sl_roe);
    o.color = b.value("color", o.color);
    if (!o.color.empty() && o.color[0] == '#')
        parse_hex_rgb(o.color, o.cr, o.cg, o.cb);
    auto ctrl = b.contains("controller") && b["controller"].is_object()
        ? b["controller"] : json::object();
    o.take_profit = jn(ctrl, "take_profit");
    o.trail_act = jn(ctrl, "trail_activation");
    o.trail_delta = jn(ctrl, "trail_delta");
    o.rebuy_pct = jn(ctrl, "rebuy_pct", o.rebuy_pct);
    o.tp_roe = jn(ctrl, "tp_roe_pct", jn(ctrl, "scale_roe_pct"));
    o.sl_roe = jn(ctrl, "sl_roe_pct", o.sl_roe);
    std::string sell, rebuy;
    if (b.contains("next") && b["next"].is_object()) {
        sell = b["next"].value("sell", std::string());
        rebuy = b["next"].value("rebuy", std::string());
    }
    // Only "COIN sell at $N" — never uPnL "$19" or another coin's moon.
    std::string needle = o.coin + " sell at $";
    auto sp = sell.find(needle);
    if (sp != std::string::npos) {
        try { o.sell_px = std::stod(sell.substr(sp + needle.size())); } catch (...) {}
    }
    double from_next = first_dollar(rebuy);
    if (from_next > 0 && rebuy.find(o.coin) != std::string::npos) o.rebuy_px = from_next;
}

static std::vector<Overlay> overlays_for(const json& home, const std::string& coin,
                                        const std::string& bot_id = {}, double last_px = 0) {
    std::vector<Overlay> out;
    if (home.contains("positions") && home["positions"].is_array()) {
        for (auto& p : home["positions"]) {
            if (!p.is_object()) continue;
            if (norm_coin(p.value("coin", std::string())) != coin) continue;
            Overlay o;
            o.bot_id = p.value("bot_id", "");
            o.bot = p.value("bot", o.bot_id);
            if (!bot_id.empty() && o.bot_id != bot_id && o.bot != bot_id) continue;
            o.coin = coin;
            o.side = p.value("side", "LONG");
            o.entry = jn(p, "entry_px");
            o.mark = jn(p, "mark_px");
            o.lev = static_cast<int>(jn(p, "leverage", 1));
            if (o.lev < 1) o.lev = 1;
            o.sl_roe = 20;
            color_from_id(o.bot_id.empty() ? o.bot : o.bot_id, o.cr, o.cg, o.cb);
            if (home.contains("bots") && home["bots"].is_array()) {
                for (auto& b : home["bots"]) {
                    if (!b.is_object()) continue;
                    if (b.value("id", "") != o.bot_id && b.value("name", "") != o.bot) continue;
                    fill_ctrl(o, b);
                    break;
                }
            }
            if (o.entry > 0) out.push_back(o);
        }
    }
    // No ghost overlays — only real open clips. Flat hunters show a clean chart.
    (void)last_px;
    return out;
}

static std::vector<std::string> coin_list(const json& home, const std::string& bot_id = {}) {
    std::vector<std::string> pos, rest = {"BTC", "ETH", "LTC", "TAO"};
    if (home.contains("positions") && home["positions"].is_array()) {
        for (auto& p : home["positions"]) {
            if (!p.is_object()) continue;
            if (!bot_id.empty()) {
                auto bid = p.value("bot_id", std::string());
                auto bn = p.value("bot", std::string());
                if (bid != bot_id && bn != bot_id) continue;
            }
            auto c = norm_coin(p.value("coin", std::string()));
            if (c.empty()) continue;
            if (std::find(pos.begin(), pos.end(), c) == pos.end()) pos.push_back(c);
        }
    }
    std::vector<std::string> coins = pos;
    for (auto& c : rest)
        if (std::find(coins.begin(), coins.end(), c) == coins.end()) coins.push_back(c);
    return coins;
}

static std::string first_pos_coin(const json& home, const std::string& bot_id = {}) {
    std::map<std::string, int> n;
    if (home.contains("positions") && home["positions"].is_array()) {
        for (auto& p : home["positions"]) {
            if (!p.is_object()) continue;
            if (!bot_id.empty()) {
                auto bid = p.value("bot_id", std::string());
                auto bn = p.value("bot", std::string());
                if (bid != bot_id && bn != bot_id) continue;
            }
            auto c = norm_coin(p.value("coin", std::string()));
            if (!c.empty()) n[c]++;
        }
    }
    std::string best;
    int bestn = 0;
    for (auto& kv : n) if (kv.second > bestn) { best = kv.first; bestn = kv.second; }
    if (!best.empty()) return best;
    auto cs = coin_list(home, bot_id);
    return cs.empty() ? "BTC" : cs[0];
}

int main(int argc, char** argv) {
    bool use_hl = false; // default Binance — no HL REST tokens
    std::string coin;
    std::string iv = "15m";
    std::string bot_id; // selected hunter id; empty = all overlays, no live knobs
    struct HRef { std::string id, name; };
    std::vector<HRef> hunters;
    bool hunter_menu = false;
    Rect hunter_btn{};
    bool want_dump = false;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--hl") use_hl = true;
        else if (a == "--binance") use_hl = false;
        else if ((a == "--coin" || a == "-c") && i + 1 < argc) coin = norm_coin(argv[++i]);
        else if ((a == "--bot") && i + 1 < argc) bot_id = argv[++i];
        else if ((a == "--iv") && i + 1 < argc) iv = argv[++i];
        else if (a == "--dump") want_dump = true;
        else if (a == "--help") {
            printf("rimbaud-chart — live candles + IN / predicted OUT\n"
                   "  --binance   public Binance klines (default, no HL tokens)\n"
                   "  --hl        Hyperliquid candleSnapshot (REST weight 20)\n"
                   "  --coin BTC  --iv 15m\n"
                   "  --bot hummingbot-bn-swing  link PMM Simple knobs (test)\n"
                   "  --dump      print fetch without a window\n");
            return 0;
        }
    }
    if (want_dump) {
        json home = load_home();
        std::string c = coin.empty() ? first_pos_coin(home, bot_id) : norm_coin(coin);
        std::string err;
        auto k = fetch_binance(c, iv, err);
        auto ovd = overlays_for(home, c, bot_id, k.empty() ? 0 : k.back().c);
        printf("home coins:");
        for (auto& x : coin_list(home, bot_id)) printf(" %s", x.c_str());
        printf("\nopen-on %s  bot=%s  overlays %zu  binance n=%zu  err=%s\n",
               c.c_str(), bot_id.empty() ? "ALL" : bot_id.c_str(), ovd.size(), k.size(), err.c_str());
        for (auto& o : ovd)
            printf("  IN %s %s %.4g  WIN %.4g  SL %.4g  ARM %.4g  TRAIL %.4g  rebuy %.4g%s\n",
                   o.bot.c_str(), o.side.c_str(), o.entry, o.win_px(), o.sl_px(),
                   o.arm_px(), o.trail_now_px(), o.rebuy_px, o.ghost ? " ghost" : "");
        if (!k.empty()) printf("  last close %.4g  t=%lld\n", k.back().c, k.back().t);
        return err.empty() && !k.empty() ? 0 : 1;
    }

    if (coin.empty()) {
        json home0 = load_home();
        coin = first_pos_coin(home0, bot_id);
    }
    coin = norm_coin(coin);
    if (coin.empty()) coin = "BTC";
    if (!take_chart_lock(coin)) {
        fprintf(stderr, "%s chart already open — CLOSE or window X that window\n", coin.c_str());
        return 0;
    }

    Display* dpy = XOpenDisplay(nullptr);
    if (!dpy) {
        fprintf(stderr, "no DISPLAY\n");
        return 1;
    }
    XSetErrorHandler(x_ignore);
    XSetIOErrorHandler(x_die);
    int scr = DefaultScreen(dpy);
    Colormap cm = DefaultColormap(dpy, scr);
    unsigned long bg = rgb(dpy, cm, 12, 28, 18);
    unsigned long panel = rgb(dpy, cm, 22, 48, 28);
    unsigned long border = rgb(dpy, cm, 70, 160, 90);
    unsigned long text = rgb(dpy, cm, 230, 255, 235);
    unsigned long muted = rgb(dpy, cm, 140, 200, 155);
    (void)text; (void)muted;
    unsigned long gold = rgb(dpy, cm, 255, 210, 40);
    unsigned long rose = rgb(dpy, cm, 255, 90, 110);
    unsigned long cyan = rgb(dpy, cm, 120, 220, 255);
    unsigned long green = rgb(dpy, cm, 40, 255, 120);
    unsigned long navy = rgb(dpy, cm, 18, 42, 26);
    unsigned long white = rgb(dpy, cm, 255, 255, 255);

    int W = 960, H = 560;
    Window win = XCreateSimpleWindow(dpy, RootWindow(dpy, scr), 60, 40, W, H, 1, gold, navy);
    XSetWindowBackground(dpy, win, navy);
    std::string wtitle = "rimBaud // CHART  " + coin;
    XStoreName(dpy, win, wtitle.c_str());
    XSizeHints hints{};
    hints.flags = PMinSize;
    hints.min_width = 480;
    hints.min_height = 320;
    XSetWMNormalHints(dpy, win, &hints);
    Atom wm_delete = XInternAtom(dpy, "WM_DELETE_WINDOW", False);
    XSetWMProtocols(dpy, win, &wm_delete, 1);
    XSelectInput(dpy, win, ExposureMask | StructureNotifyMask | ButtonPressMask
                 | ButtonReleaseMask | PointerMotionMask | KeyPressMask);
    Cursor cur_normal = XCreateFontCursor(dpy, XC_left_ptr);
    Cursor cur_ns = XCreateFontCursor(dpy, XC_sb_v_double_arrow);
    XDefineCursor(dpy, win, cur_normal);
    XMapWindow(dpy, win);
    GC gc = XCreateGC(dpy, win, 0, nullptr);
    XFontStruct* fs = XLoadQueryFont(dpy, "9x15");
    if (!fs) fs = XLoadQueryFont(dpy, "fixed");
    if (!fs) fs = XLoadQueryFont(dpy, "6x13");
    XFontStruct* fs2 = XLoadQueryFont(dpy, "10x20");
    if (!fs2) fs2 = XLoadQueryFont(dpy, "12x24");
    if (!fs2) fs2 = fs;
    if (fs) XSetFont(dpy, gc, fs->fid);
    const int FW = fs2 ? fs2->max_bounds.width : 10;
    const int FH = fs2 ? (fs2->ascent + fs2->descent) : 20;
    Drawable buf = win;

    std::vector<Candle> candles;
    std::vector<Overlay> ov;
    json home;
    std::vector<std::string> coins = {"BTC", "ETH", "LTC", "TAO"};
    std::string err, note;
    double last = 0;
    double last_meta = 0;
    double last_live = 0;
    double last_persist = 0;
    double hold_live_until = 0;
    Rect plot_r{};
    double plot_lo = 0, plot_hi = 1;
    int hover_oi = -1, hover_line = -1;
    int drag_oi = -1, drag_line = -1;
    const char* IVS[] = {"5m", "15m", "1h"};
    std::vector<Hit> hits;

    auto nowm = []() {
        timespec ts{};
        clock_gettime(CLOCK_MONOTONIC, &ts);
        return ts.tv_sec + ts.tv_nsec / 1e9;
    };
    auto fill = [&](unsigned long c, Rect r) {
        if (r.w <= 0 || r.h <= 0) return;
        XSetForeground(dpy, gc, c);
        XFillRectangle(dpy, buf, gc, r.x, r.y, r.w, r.h);
    };
    auto stroke = [&](unsigned long c, Rect r) {
        XSetForeground(dpy, gc, c);
        XDrawRectangle(dpy, buf, gc, r.x, r.y, r.w - 1, r.h - 1);
    };
    auto txt = [&](unsigned long c, int x, int y, const std::string& s) {
        auto a = ascii(s);
        if (a.empty()) return;
        if (fs) XSetFont(dpy, gc, fs->fid);
        XSetForeground(dpy, gc, c);
        XDrawString(dpy, buf, gc, x, y, a.c_str(), (int)a.size());
    };
    auto txt2 = [&](unsigned long c, int x, int y, const std::string& s) {
        auto a = ascii(s);
        if (a.empty()) return;
        if (fs2) XSetFont(dpy, gc, fs2->fid);
        XSetForeground(dpy, gc, c);
        XDrawString(dpy, buf, gc, x, y, a.c_str(), (int)a.size());
        if (fs) XSetFont(dpy, gc, fs->fid);
    };

    auto load_meta = [&]() {
        home = load_home();
        coins = coin_list(home, bot_id);
        if (coin.empty()) coin = first_pos_coin(home, bot_id);
        coin = norm_coin(coin);
        if (std::find(coins.begin(), coins.end(), coin) == coins.end())
            coins.insert(coins.begin(), coin);
        double last_px = candles.empty() ? 0 : candles.back().c;
        auto keep = ov;
        ov = overlays_for(home, coin, bot_id, last_px);
        if (!bot_id.empty()) {
            for (auto& o : ov) {
                o.sell_px = 0;
                for (auto& k : keep) {
                    if (k.bot_id != o.bot_id) continue;
                    o.take_profit = k.take_profit;
                    o.trail_act = k.trail_act;
                    o.trail_delta = k.trail_delta;
                    o.sl_roe = k.sl_roe;
                    o.rebuy_pct = k.rebuy_pct;
                    if (k.side == o.side && o.entry > 0 && k.entry > 0
                        && std::fabs(k.entry - o.entry) < o.entry * 0.01)
                        o.rebuy_px = k.rebuy_px;
                    else
                        o.rebuy_px = 0;
                    o.tp_roe = k.tp_roe;
                }
            }
            apply_pmm_live(ov, bot_id);
        }
    };
    auto load_candles = [&]() {
        if (use_hl) candles = fetch_hl(coin, iv, err);
        else candles = fetch_binance(coin, iv, err);
        note = use_hl ? "HL candles (REST tokens)" : "Binance candles (no HL tokens)";
        if (!err.empty()) note = err;
    };

    auto paint = [&]() {
        hits.clear();
        fill(bg, {0, 0, W, H});
        fill(navy, {0, 0, W, 70});
        txt(white, 12, 16, "rimBaud  //  CHART");
        txt(white, 220, 16, note.empty() ? std::string("ready") : note);

        Rect rhl{12, 22, 108, 22};
        Rect rbn{128, 22, 100, 22};
        fill(use_hl ? panel : bg, rhl);
        stroke(use_hl ? gold : border, rhl);
        txt(use_hl ? gold : white, rhl.x + 6, rhl.y + 15, "HYPERLIQUID");
        fill(!use_hl ? panel : bg, rbn);
        stroke(!use_hl ? cyan : border, rbn);
        txt(!use_hl ? cyan : white, rbn.x + 16, rbn.y + 15, "BINANCE");
        hits.push_back({0, 0, rhl});
        hits.push_back({0, 1, rbn});
        Rect rclose{W - 66, 22, 54, 22};
        fill(rgb(dpy, cm, 48, 16, 16), rclose);
        stroke(rose, rclose);
        txt(rose, rclose.x + 8, rclose.y + 15, "CLOSE");
        hits.push_back({3, 0, rclose});
        for (int i = 0; i < 3; ++i) {
            Rect r{W - 266 + i * 62, 22, 56, 22};
            bool on = iv == IVS[i];
            fill(on ? panel : bg, r);
            stroke(on ? gold : border, r);
            txt(on ? gold : white, r.x + 12, r.y + 15, IVS[i]);
            hits.push_back({2, i, r});
        }
        int x = 12;
        for (int i = 0; i < (int)coins.size() && i < 10; ++i) {
            int tw = std::max(44, (int)coins[i].size() * 9 + 18);
            Rect r{x, 46, tw, 20};
            bool on = coins[i] == coin;
            fill(on ? panel : bg, r);
            stroke(on ? gold : border, r);
            txt(on ? gold : white, r.x + 6, r.y + 14, coins[i]);
            hits.push_back({1, i, r});
            x += tw + 6;
        }
        {
            hunters.clear();
            if (home.contains("bots") && home["bots"].is_array()) {
                for (auto& b : home["bots"]) {
                    if (!b.is_object()) continue;
                    std::string id = b.value("id", std::string());
                    if (id.empty()) continue;
                    hunters.push_back({id, b.value("name", id)});
                }
            }
            std::string lab = "HUNTER";
            for (auto& h : hunters)
                if (h.id == bot_id) { lab = h.name; break; }
            if (lab.size() > 14) lab = lab.substr(0, 14);
            int tw = std::max(88, (int)lab.size() * 9 + 28);
            Rect r{std::min(x + 8, W - tw - 12), 46, tw, 20};
            hunter_btn = r;
            fill(!bot_id.empty() ? panel : bg, r);
            stroke(!bot_id.empty() ? gold : border, r);
            txt(!bot_id.empty() ? gold : white, r.x + 8, r.y + 14, ascii(lab + " v", 16));
            hits.push_back({6, -1, r});
        }

        const int axis_w = FW * 9 + 12;
        Rect plot{12, 74, std::max(40, W - 24 - axis_w), std::max(40, H - 90)};
        plot_r = plot;
        fill(rgb(dpy, cm, 8, 22, 14), plot);
        stroke(border, plot);
        fill(navy, {plot.x + plot.w + 1, plot.y, axis_w, plot.h});
        if (candles.empty())
            txt(white, plot.x + 12, plot.y + 24, err.empty() ? "no candles yet" : ascii(err, 50));

        double lo = 1e99, hi = -1e99;
        for (auto& c : candles) { lo = std::min(lo, c.l); hi = std::max(hi, c.h); }
        if (hi <= lo) {
            for (auto& o : ov) {
                if (o.entry > 0) { lo = std::min(lo, o.entry); hi = std::max(hi, o.entry); }
            }
        }
        for (auto& o : ov) {
            auto bump = [&](double p) {
                if (p > 0) { lo = std::min(lo, p); hi = std::max(hi, p); }
            };
            bump(o.entry); bump(o.win_px()); bump(o.sl_px());
            bump(o.arm_px()); bump(o.trail_now_px()); bump(o.next_in_px());
            if (o.rebuy_px > 0) bump(o.rebuy_px);
        }
        if (hi <= lo) { lo = 0; hi = 1; }
        double pad = (hi - lo) * 0.04;
        lo -= pad; hi += pad;
        plot_lo = lo;
        plot_hi = hi;
        const double cap_hi = hi * 1.10;
        const double cap_lo = lo * 0.90;
        auto ypx = [&](double p) {
            return plot.y + (int)((hi - p) / (hi - lo) * plot.h);
        };
        int top_n = 0, bot_n = 0;
        auto y_for = [&](double p) -> int {
            if (p > cap_hi) {
                int y = plot.y + 8 + (top_n++ % 5) * 12;
                return y;
            }
            if (p < cap_lo) {
                int y = plot.y + plot.h - 8 - (bot_n++ % 5) * 12;
                return y;
            }
            return ypx(p);
        };

        {
            unsigned long grid = rgb(dpy, cm, 28, 70, 40);
            XSetLineAttributes(dpy, gc, 1, LineOnOffDash, CapButt, JoinMiter);
            for (int i = 0; i <= 10; ++i) {
                int y = plot.y + (plot.h * i) / 10;
                if (y >= plot.y + plot.h) y = plot.y + plot.h - 1;
                double pxv = hi - (hi - lo) * (double)i / 10.0;
                XSetForeground(dpy, gc, grid);
                XDrawLine(dpy, buf, gc, plot.x + 1, y, plot.x + plot.w - 1, y);
                XSetForeground(dpy, gc, i == 0 || i == 10 ? white : muted);
                XSetLineAttributes(dpy, gc, 1, LineSolid, CapButt, JoinMiter);
                XDrawLine(dpy, buf, gc, plot.x + plot.w, y, plot.x + plot.w + 6, y);
                XSetLineAttributes(dpy, gc, 1, LineOnOffDash, CapButt, JoinMiter);
                int base = y + (fs2 ? fs2->ascent / 2 - fs2->descent / 2 : 6);
                if (base < plot.y + FH - 2) base = plot.y + FH - 2;
                if (base > plot.y + plot.h - 2) base = plot.y + plot.h - 2;
                txt2(i == 0 || i == 10 ? white : muted, plot.x + plot.w + 8, base, fmt_px(pxv));
            }
            XSetLineAttributes(dpy, gc, 1, LineSolid, CapButt, JoinMiter);
        }

        if (!candles.empty()) {
            double cw = std::max(2.0, (double)plot.w / (double)candles.size());
            for (int i = 0; i < (int)candles.size(); ++i) {
                auto& c = candles[i];
                int x0 = plot.x + (int)(i * cw);
                int mid = x0 + (int)(cw / 2);
                int y_h = ypx(c.h), y_l = ypx(c.l), y_o = ypx(c.o), y_c = ypx(c.c);
                unsigned long col = c.c >= c.o ? green : rose;
                XSetForeground(dpy, gc, col);
                XDrawLine(dpy, buf, gc, mid, y_h, mid, y_l);
                int top = std::min(y_o, y_c), bot = std::max(y_o, y_c);
                fill(col, {x0 + 1, top, std::max(2, (int)cw - 1), std::max(1, bot - top)});
            }
        }

        struct Mk {
            int dx, dy;
            bool right;
            unsigned long col;
            std::string lab;
        };
        std::vector<Mk> mks;
        auto add_mk = [&](int dx, double px, unsigned long col, const char* tag) {
            if (px <= 0) return;
            char b[72];
            snprintf(b, sizeof(b), "%s  %s", tag, fmt_px(px).c_str());
            mks.push_back({dx, y_for(px), dx > plot.x + plot.w / 2, col, b});
        };
        auto hline = [&](double px, unsigned long col, bool hot) {
            if (px <= 0) return;
            int y = y_for(px);
            XSetForeground(dpy, gc, hot ? gold : col);
            XSetLineAttributes(dpy, gc, hot ? 2 : 1, hot ? LineSolid : LineOnOffDash, CapButt, JoinMiter);
            XDrawLine(dpy, buf, gc, plot.x + 2, y, plot.x + plot.w - 2, y);
            XSetLineAttributes(dpy, gc, 1, LineSolid, CapButt, JoinMiter);
        };
        auto add_line_hit = [&](int oi, int line, double px) {
            if (px <= 0 || bot_id.empty()) return;
            int y = y_for(px);
            hits.push_back({5, oi * 10 + line, {plot.x, y - 6, plot.w, 13}});
        };
        unsigned long col_sl = rgb(dpy, cm, 255, 77, 109);
        unsigned long col_tp = rgb(dpy, cm, 0, 255, 106);
        unsigned long col_arm = rgb(dpy, cm, 240, 185, 11);
        unsigned long col_tr = rgb(dpy, cm, 255, 200, 87);
        unsigned long col_re = rgb(dpy, cm, 126, 203, 255);
        for (int oi = 0; oi < (int)ov.size(); ++oi) {
            auto& o = ov[oi];
            unsigned long din = shade(dpy, cm, o.cr, o.cg, o.cb, 0.55);
            unsigned long rcol = rgb(dpy, cm, o.cr, o.cg, o.cb);
            bool hot = (hover_oi == oi || drag_oi == oi);
            if (!o.ghost) {
                add_mk(plot.x + 10, o.entry, din, "IN");
                hline(o.entry, din, false);
            }
            hline(o.sl_px(), col_sl, hot && (hover_line == 0 || drag_line == 0));
            {
                char tag[40];
                if (o.sl_roe > 0) snprintf(tag, sizeof(tag), "SL %.0f%%", o.sl_roe);
                else snprintf(tag, sizeof(tag), "SL");
                add_mk(plot.x + plot.w - 12, o.sl_px(), col_sl, tag);
            }
            add_line_hit(oi, 0, o.sl_px());
            hline(o.win_px(), col_tp, hot && (hover_line == 1 || drag_line == 1));
            {
                char tag[40];
                if (o.tp_roe > 0) snprintf(tag, sizeof(tag), "WIN %.0f%%", o.tp_roe);
                else snprintf(tag, sizeof(tag), "WIN");
                add_mk(plot.x + plot.w - 12, o.win_px(), col_tp, tag);
            }
            add_line_hit(oi, 1, o.win_px());
            hline(o.arm_px(), col_arm, hot && (hover_line == 2 || drag_line == 2));
            add_mk(plot.x + 10, o.arm_px(), col_arm, "ARM");
            add_line_hit(oi, 2, o.arm_px());
            double tr = o.trail_now_px();
            if (tr > 0) {
                hline(tr, col_tr, hot && (hover_line == 3 || drag_line == 3));
                add_mk(plot.x + plot.w - 12, tr, col_tr, "TRAIL");
                add_line_hit(oi, 3, tr);
            }
            double ni = o.next_in_px();
            if (ni > 0) {
                hline(ni, col_re, hot && (hover_line == 4 || drag_line == 4));
                add_mk(plot.x + 10, ni, col_re, "NEXT IN");
                add_line_hit(oi, 4, ni);
            }
            (void)rcol;
        }
        const int gap = FH + 4;
        auto place_y = [&](int y, std::vector<int>& used) {
            if (y < plot.y + FH) y = plot.y + FH;
            if (y > plot.y + plot.h - 4) y = plot.y + plot.h - 4;
            for (int n = 0; n < 30; ++n) {
                bool ok = true;
                for (int u : used) {
                    if (std::abs(u - y) < gap) {
                        ok = false;
                        y += (y >= u) ? gap : -gap;
                        if (y < plot.y + FH) y = plot.y + FH;
                        if (y > plot.y + plot.h - 4) y = plot.y + plot.h - 4;
                        break;
                    }
                }
                if (ok) break;
            }
            used.push_back(y);
            return y;
        };
        std::vector<int> used_l, used_r;
        for (auto& m : mks) {
            int ly = place_y(m.dy, m.right ? used_r : used_l);
            int tw = (int)m.lab.size() * FW;
            int tx = m.right ? (plot.x + plot.w - 10 - tw) : (plot.x + 16);
            if (tx < plot.x + 4) tx = plot.x + 4;
            fill(m.col, {m.dx - 4, m.dy - 4, 8, 8});
            stroke(white, {m.dx - 4, m.dy - 4, 8, 8});
            if (std::abs(ly - m.dy) > 3) {
                XSetForeground(dpy, gc, m.col);
                XDrawLine(dpy, buf, gc, m.dx, m.dy, m.right ? tx + tw : tx, ly);
            }
            fill(rgb(dpy, cm, 6, 18, 10), {tx - 3, ly - FH + 4, tw + 6, FH});
            txt2(m.col, tx, ly, m.lab);
        }
        if (!bot_id.empty()) {
            if (drag_line >= 0)
                txt(gold, 12, H - 10, "dragging line — drop to set hunter knobs via Spring · no HL close/open");
            else if (hover_line >= 0)
                txt(gold, 12, H - 10, "drag this line to move SL / WIN / ARM / TRAIL / NEXT IN");
            else
                    {
                    char hud[128];
                    double sls = ov.empty() ? 0 : ov[0].sl_roe;
                    snprintf(hud, sizeof(hud),
                             "SL %.0f%% ROE  ·  drag lines  ·  Spring live  ·  clips untouched",
                             sls);
                    txt(gold, 12, H - 10, hud);
                }
        } else
            txt(white, 12, H - 10, "CLOSE or window X  ·  pick a hunter in the dropdown to edit knobs");
        if (hunter_menu && !hunters.empty()) {
            int row_h = 22;
            int mh = 10 + (int)hunters.size() * row_h;
            int mw = std::max(hunter_btn.w, 200);
            int mx0 = hunter_btn.x;
            if (mx0 + mw > W - 8) mx0 = std::max(8, W - 8 - mw);
            int my0 = hunter_btn.y + hunter_btn.h + 2;
            if (my0 + mh > H - 8) my0 = hunter_btn.y - 2 - mh;
            if (my0 < 8) my0 = 8;
            Rect menu{mx0, my0, mw, mh};
            fill(rgb(dpy, cm, 8, 22, 16), menu);
            stroke(gold, menu);
            int myy = menu.y + 18;
            for (int i = 0; i < (int)hunters.size(); ++i) {
                Rect row{menu.x + 4, myy - 14, menu.w - 8, 20};
                bool on = hunters[i].id == bot_id;
                if (on) fill(panel, row);
                txt(on ? gold : white, row.x + 8, myy, ascii(hunters[i].name, 22));
                hits.push_back({6, i, row});
                myy += row_h;
            }
        }
        XFlush(dpy);
    };

    note = "loading...";
    paint();
    load_meta();
    load_candles();
    last = nowm();
    paint();
    bool run = true;
    while (run) {
        while (XPending(dpy)) {
            XEvent e;
            XNextEvent(dpy, &e);
            if (e.type == ClientMessage) {
                if ((Atom)e.xclient.data.l[0] == wm_delete) run = false;
            } else if (e.type == DestroyNotify) {
                run = false;
            } else if (e.type == Expose) {
                paint();
            } else if (e.type == ConfigureNotify) {
                if (e.xconfigure.width >= 200) W = e.xconfigure.width;
                if (e.xconfigure.height >= 200) H = e.xconfigure.height;
                paint();
            } else if (e.type == KeyPress) {
                KeySym ks = XLookupKeysym(&e.xkey, 0);
                if (ks == XK_q || ks == XK_Escape) run = false;
                else if (ks == XK_h) { use_hl = true; paint(); load_candles(); paint(); }
                else if (ks == XK_b) { use_hl = false; paint(); load_candles(); paint(); }
                else if (ks == XK_1) { iv = "5m"; paint(); load_candles(); paint(); }
                else if (ks == XK_2) { iv = "15m"; paint(); load_candles(); paint(); }
                else if (ks == XK_3) { iv = "1h"; paint(); load_candles(); paint(); }
                else if (ks == XK_r) { load_meta(); load_candles(); paint(); }
            } else if (e.type == ButtonPress && e.xbutton.button == 1) {
                int mx = e.xbutton.x, my = e.xbutton.y;
                bool hit = false;
                int hk = -99;
                for (int hi = (int)hits.size() - 1; hi >= 0; --hi) {
                    auto& h = hits[hi];
                    if (!inside(h.r, mx, my)) continue;
                    hk = h.kind;
                    if (h.kind == 3) { run = false; hit = true; break; }
                    if (h.kind == 0) use_hl = (h.i == 0);
                    else if (h.kind == 1 && h.i >= 0 && h.i < (int)coins.size()) {
                        coin = coins[h.i];
                        std::string t = "rimBaud // CHART  " + coin;
                        XStoreName(dpy, win, t.c_str());
                    }
                    else if (h.kind == 2) iv = IVS[h.i];
                    else if (h.kind == 6) {
                        if (h.i < 0) hunter_menu = !hunter_menu;
                        else if (h.i < (int)hunters.size()) {
                            bot_id = hunters[h.i].id;
                            hunter_menu = false;
                            auto jump = first_pos_coin(home, bot_id);
                            if (!jump.empty()) coin = jump;
                            std::string t = "rimBaud // CHART  " + coin + "  " + hunters[h.i].name;
                            XStoreName(dpy, win, t.c_str());
                        }
                    } else if (h.kind == 5) {
                        drag_oi = h.i / 10;
                        drag_line = h.i % 10;
                        hover_oi = drag_oi;
                        hover_line = drag_line;
                        XDefineCursor(dpy, win, cur_ns);
                    }
                    hit = true;
                    break;
                }
                if (hk != 6) hunter_menu = false;
                if (hit && run && drag_line < 0) {
                    double last_px = candles.empty() ? 0 : candles.back().c;
                    ov = overlays_for(home, coin, bot_id, last_px);
                    if (!bot_id.empty()) apply_pmm_live(ov, bot_id);
                    paint();
                    load_candles();
                    paint();
                } else if (hit) paint();
            } else if (e.type == MotionNotify) {
                int mx = e.xmotion.x, my = e.xmotion.y;
                if (drag_line >= 0 && drag_oi >= 0 && drag_oi < (int)ov.size() && plot_r.h > 0) {
                    double t = (my - plot_r.y) / (double)plot_r.h;
                    double px = plot_hi - t * (plot_hi - plot_lo);
                    if (apply_line_price(ov[drag_oi], drag_line, px)) {
                        push_live_overlay(ov[drag_oi], false);
                        paint();
                    }
                } else {
                    int nho = -1, nhl = -1;
                    for (int hi = (int)hits.size() - 1; hi >= 0; --hi) {
                        if (hits[hi].kind != 5 || !inside(hits[hi].r, mx, my)) continue;
                        nho = hits[hi].i / 10;
                        nhl = hits[hi].i % 10;
                        break;
                    }
                    if (nho != hover_oi || nhl != hover_line) {
                        hover_oi = nho;
                        hover_line = nhl;
                        XDefineCursor(dpy, win, nhl >= 0 ? cur_ns : cur_normal);
                        paint();
                    }
                }
            } else if (e.type == ButtonRelease) {
                if (drag_line >= 0 && drag_oi >= 0 && drag_oi < (int)ov.size()) {
                    auto& o = ov[drag_oi];
                    push_live_overlay(o, true);
                    json vals = {
                        {"trail_activation", o.trail_act},
                        {"trail_delta", o.trail_delta},
                        {"sl_roe_pct", o.sl_roe},
                        {"rebuy_pct", o.rebuy_pct},
                        {"rebuy_px", o.rebuy_px},
                    };
                    if (o.tp_roe > 0) {
                        vals["scale_roe_pct"] = o.tp_roe;
                        vals["tp_roe_pct"] = o.tp_roe;
                        vals["take_profit"] = 0;
                    } else {
                        vals["take_profit"] = o.take_profit;
                    }
                    json body = {{"id", o.bot_id}, {"values", vals}};
                    http_request("127.0.0.1", 5004, "POST", "/api/wire/controller", body.dump(), 2000);
                    json slb = {{"id", o.bot_id}, {"sl_roe_pct", o.sl_roe}};
                    http_request("127.0.0.1", 5004, "POST", "/api/home/sl", slb.dump(), 2000);
                    hold_live_until = nowm() + 0.2;
                    (void)last_persist;
                    (void)line_tag;
                }
                drag_oi = -1;
                drag_line = -1;
                paint();
            }
        }
        if (drag_line < 0 && nowm() > hold_live_until
            && !bot_id.empty() && nowm() - last_live > 0.008) {
            last_live = nowm();
            if (apply_pmm_live(ov, bot_id)) paint();
        }
        if (drag_line < 0 && nowm() > hold_live_until
            && nowm() - last_meta > (!bot_id.empty() ? 4.0 : 2.0)) {
            last_meta = nowm();
            load_meta();
            paint();
        }
        if (drag_line < 0 && nowm() - last > (use_hl ? 20.0 : 8.0)) {
            last = nowm();
            load_candles();
            paint();
        }
        struct timespec sl{0, (!bot_id.empty() ? 2 : 20) * 1000 * 1000};
        nanosleep(&sl, nullptr);
    }
    XFreeCursor(dpy, cur_normal);
    XFreeCursor(dpy, cur_ns);
    XFreeGC(dpy, gc);
    if (fs2 && fs2 != fs) XFreeFont(dpy, fs2);
    if (fs) XFreeFont(dpy, fs);
    XDestroyWindow(dpy, win);
    XCloseDisplay(dpy);
    return 0;
}
