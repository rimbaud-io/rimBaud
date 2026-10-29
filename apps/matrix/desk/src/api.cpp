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

#include "api.hpp"
#include "http.hpp"

#include <cstdio>
#include <exception>
#include <string>

using nlohmann::json;

static json parse_or(const std::string& s) {
    try {
        return json::parse(s.empty() ? "{}" : s);
    } catch (...) {
        return json::object();
    }
}

static double num(const json& j, const char* k, double d = 0) {
    if (!j.contains(k) || j[k].is_null()) return d;
    try {
        if (j[k].is_number()) return j[k].get<double>();
        if (j[k].is_string()) return std::stod(j[k].get<std::string>());
    } catch (...) {}
    return d;
}

static std::vector<std::string> str_list(const json& j, const char* k) {
    std::vector<std::string> out;
    if (!j.contains(k) || !j[k].is_array()) return out;
    for (auto& x : j[k]) if (x.is_string()) out.push_back(x.get<std::string>());
    return out;
}

double display_value(const CtrlField& f, const json& stored) {
    if (stored.is_null()) {
        // Current DMan Maker skim is on until the operator turns the box off.
        if (f.key == "sendEvery2Win") return 1;
        return f.minv;
    }
    if (f.kind == "bool") {
        if (stored.is_boolean()) return stored.get<bool>() ? 1 : 0;
        try { return stored.get<double>() != 0; } catch (...) { return 0; }
    }
    double n = 0;
    try {
        if (stored.is_number()) n = stored.get<double>();
        else if (stored.is_string()) n = std::stod(stored.get<std::string>());
        else return f.minv;
    } catch (...) {
        return f.minv;
    }
    if ((f.kind == "pct_frac" || f.kind == "price_pct") && n <= 1.0) n *= 100.0;
    return n;
}

std::string json_preview(const json& stored) {
    if (stored.is_null()) return "-";
    if (stored.is_boolean()) return stored.get<bool>() ? "on" : "off";
    if (stored.is_string()) return stored.get<std::string>();
    if (stored.is_number_integer()) return std::to_string(stored.get<long long>());
    if (stored.is_number()) {
        char b[48];
        snprintf(b, sizeof(b), "%g", stored.get<double>());
        return b;
    }
    return stored.dump();
}

static std::string str(const json& j, const char* k, const std::string& d = "") {
    if (!j.contains(k) || j[k].is_null()) return d;
    try {
        if (j[k].is_string()) return j[k].get<std::string>();
        if (j[k].is_number() || j[k].is_boolean()) return json_preview(j[k]);
    } catch (...) {}
    return d;
}

static bool flag(const json& j, const char* k, bool d = false) {
    if (!j.contains(k) || j[k].is_null()) return d;
    try {
        if (j[k].is_boolean()) return j[k].get<bool>();
        if (j[k].is_number()) return j[k].get<double>() != 0;
        if (j[k].is_string()) {
            auto s = j[k].get<std::string>();
            return s == "true" || s == "1" || s == "on";
        }
    } catch (...) {}
    return d;
}

static Bot parse_bot(const json& j) {
    Bot b;
    b.id = str(j, "id");
    b.name = str(j, "name", b.id);
    b.color = str(j, "color", "#9dffc0");
    b.address = str(j, "address");
    b.href = str(j, "href");
    b.icon = str(j, "icon");
    b.health = str(j, "health");
    b.health_reason = str(j, "health_reason");
    b.running = flag(j, "running");
    b.deactivated = flag(j, "deactivated");
    b.pool_cash = flag(j, "pool_cash");
    b.ok = flag(j, "ok");
    b.equity = num(j, "equity_usd");
    b.free_usd = num(j, "free_usd");
    b.upnl = num(j, "upnl_usd");
    b.sl_roe = num(j, "sl_roe_pct", 20);
    b.n_pos = static_cast<int>(num(j, "n_pos"));
    if (j.contains("next") && j["next"].is_object()) {
        b.next_sell = str(j["next"], "sell");
        b.next_rebuy = str(j["next"], "rebuy");
    }
    if (j.contains("bias") && j["bias"].is_object()) {
        for (auto it = j["bias"].begin(); it != j["bias"].end(); ++it) {
            if (it.value().is_string()) b.bias[it.key()] = it.value().get<std::string>();
        }
    }
    b.bias_mode = str(j, "bias_mode");
    b.coins = str_list(j, "coins");
    if (b.coins.empty() && !b.bias.empty()) {
        for (auto& kv : b.bias) b.coins.push_back(kv.first);
    }
    b.markets = str_list(j, "markets");
    b.idle_markets = str_list(j, "idle_markets");
    b.held_coins = str_list(j, "held_coins");
    b.logs = str_list(j, "logs");
    if (b.logs.size() > 48) b.logs.erase(b.logs.begin(), b.logs.end() - 48);
    if (j.contains("controller") && j["controller"].is_object()) b.controller = j["controller"];
    b.n_wins = static_cast<int>(num(j, "n_wins"));
    b.n_losses = static_cast<int>(num(j, "n_losses"));
    b.n_opens = static_cast<int>(num(j, "n_opens"));
    b.n_closes = static_cast<int>(num(j, "n_closes"));
    b.realized_pnl = num(j, "realized_pnl_usd");
    if (j.contains("trades") && j["trades"].is_array()) {
        for (auto& t : j["trades"]) {
            if (!t.is_object()) continue;
            TradeRow r;
            r.ts = str(t, "ts");
            r.action = str(t, "action");
            r.coin = str(t, "coin");
            r.side = str(t, "side");
            r.reason = str(t, "reason");
            r.amount = num(t, "amount");
            r.price = num(t, "price");
            if (t.contains("pnl_usd") && !t["pnl_usd"].is_null()) {
                r.has_pnl = true;
                r.pnl = num(t, "pnl_usd");
            }
            r.roe = num(t, "roe_pct");
            r.trader = static_cast<int>(num(t, "trader"));
            if (static_cast<int>(b.trades.size()) < 50)
                b.trades.push_back(r);
        }
        if (b.n_wins == 0 && b.n_losses == 0) {
            for (auto& r : b.trades) {
                if (r.action != "CLOSE" && r.action != "close") continue;
                if (!r.has_pnl) continue;
                if (r.pnl > 0) b.n_wins++;
                else if (r.pnl < 0) b.n_losses++;
                b.realized_pnl += r.pnl;
            }
        }
    }
    if (j.contains("copy_traders") && j["copy_traders"].is_array()) {
        for (auto& t : j["copy_traders"]) {
            if (!t.is_object()) continue;
            CopyTrader c;
            c.num = static_cast<int>(num(t, "num"));
            c.address = str(t, "address");
            c.short_addr = str(t, "short", c.address.size() > 16
                ? c.address.substr(0, 10) + "…" + c.address.substr(c.address.size() - 6)
                : c.address);
            c.muted = flag(t, "muted") || flag(t, "unfollowed");
            c.n_pos = static_cast<int>(num(t, "n_positions", num(t, "n_pos")));
            c.follow_slots = static_cast<int>(num(t, "follow_slots", 3));
            if (c.follow_slots < 1) c.follow_slots = 1;
            if (c.follow_slots > 3) c.follow_slots = 3;
            c.coins = str_list(t, "coins");
            if (c.num > 0) b.copy_traders.push_back(c);
        }
    }
    return b;
}

static void parse_home_extras(DeskSnap& snap, const json& j) {
    if (j.contains("rate") && j["rate"].is_object()) {
        auto r = j["rate"];
        snap.rate.used = num(r, "used");
        snap.rate.cap = num(r, "cap", 1200);
        snap.rate.left = num(r, "left");
        snap.rate.pct = num(r, "pct");
        snap.rate.n429 = static_cast<int>(num(r, "n429"));
        snap.rate.last_429_s = num(r, "last_429_s", -1);
        snap.rate.refill_in_s = num(r, "refill_in_s");
        snap.rate.note = str(r, "note");
    }
    if (j.contains("hl_key") && j["hl_key"].is_object()) {
        auto k = j["hl_key"];
        snap.hl_key.state = str(k, "state");
        snap.hl_key.on_exchange = str(k, "on_exchange");
        snap.hl_key.address = str(k, "address");
        snap.hl_key.agent_name = str(k, "agent_name");
        snap.hl_key.checked_at = str(k, "checked_at");
        snap.hl_key.error = str(k, "error");
    }
    if (j.contains("health") && j["health"].is_object()) {
        auto h = j["health"];
        if (h.contains("host") && h["host"].is_object()) {
            auto o = h["host"];
            snap.host.cpu_pct = num(o, "cpu_pct");
            snap.host.mem_pct = num(o, "mem_pct");
            snap.host.disk_pct = num(o, "disk_pct");
            snap.host.load1 = num(o, "load1");
            snap.host.mem_used_gb = num(o, "mem_used_gb");
            snap.host.mem_total_gb = num(o, "mem_total_gb");
            snap.host.disk_used_gb = num(o, "disk_used_gb");
            snap.host.disk_total_gb = num(o, "disk_total_gb");
        }
        if (h.contains("net") && h["net"].is_object()) {
            auto n = h["net"];
            snap.net.ok = flag(n, "ok");
            snap.net.ms = static_cast<int>(num(n, "ms"));
            snap.net.target = str(n, "target");
            snap.net.error = str(n, "error");
        }
        if (h.contains("hlip") && h["hlip"].is_object() && snap.rate.cap <= 0) {
            auto r = h["hlip"];
            snap.rate.used = num(r, "used");
            snap.rate.cap = num(r, "cap", 1200);
            snap.rate.left = num(r, "left");
            snap.rate.pct = num(r, "pct");
        }
    }
    if (j.contains("risk") && j["risk"].is_object()) {
        auto r = j["risk"];
        snap.risk.address = str(r, "address");
        snap.risk.available_usd = num(r, "available_usd");
        snap.risk.locked_usd = num(r, "locked_usd");
        snap.risk.parked_usd = num(r, "parked_usd");
        snap.risk.sent_usd = num(r, "sent_usd");
        snap.risk.onchain_usd = num(r, "onchain_usd");
        snap.risk.can_send = flag(r, "can_send");
        snap.risk.connected = static_cast<int>(num(r, "connected"));
        if (r.contains("hunters") && r["hunters"].is_array()) {
            for (auto& h : r["hunters"]) {
                if (!h.is_object()) continue;
                RiskRow row;
                row.id = str(h, "id");
                row.name = str(h, "name", row.id);
                row.mode = str(h, "mode");
                row.last_send_error = str(h, "last_send_error");
                row.last_send_ts = str(h, "last_send_ts");
                row.parked_usd = num(h, "parked_usd");
                row.available_usd = num(h, "available_usd");
                row.locked_usd = num(h, "locked_usd");
                row.sent_usd = num(h, "sent_usd");
                row.n_pos = static_cast<int>(num(h, "n_pos"));
                row.armed = flag(h, "armed");
                row.connected = flag(h, "connected");
                snap.risk.hunters.push_back(row);
            }
        }
    }
    if (j.contains("desk_bias") && j["desk_bias"].is_object()) {
        auto d = j["desk_bias"];
        snap.desk_bias.bias = str(d, "bias");
        snap.desk_bias.closes = flag(d, "closes");
        if (d.contains("hunters") && d["hunters"].is_array()) {
            for (auto& h : d["hunters"]) {
                if (!h.is_object()) continue;
                DeskBiasHunter row;
                row.id = str(h, "id");
                row.name = str(h, "name", row.id);
                row.mode = str(h, "mode");
                row.target = str(h, "target");
                row.applied = flag(h, "applied");
                row.waiting = flag(h, "waiting");
                row.held = str_list(h, "held");
                snap.desk_bias.hunters.push_back(row);
            }
        }
    }
    if (j.contains("condor") && j["condor"].is_object()) {
        auto c = j["condor"];
        snap.condor.ok = flag(c, "ok");
        snap.condor.running = flag(c, "running");
        snap.condor.regime = str(c, "regime");
        snap.condor.source = str(c, "source");
        snap.condor.title = str(c, "title");
        snap.condor.why = str(c, "why");
        snap.condor.ts = str(c, "ts");
        if (c.contains("bias") && c["bias"].is_object()) {
            for (auto it = c["bias"].begin(); it != c["bias"].end(); ++it) {
                if (it.value().is_string()) snap.condor.bias[it.key()] = it.value().get<std::string>();
            }
        }
        if (c.contains("telegram") && c["telegram"].is_object())
            snap.condor.telegram_ok = flag(c["telegram"], "ok");
        if (c.contains("grok_bot") && c["grok_bot"].is_object())
            snap.condor.grok_ok = flag(c["grok_bot"], "configured");
        if (c.contains("feed") && c["feed"].is_array()) {
            for (auto& f : c["feed"]) {
                if (!f.is_object()) continue;
                std::string line = str(f, "ts");
                if (!line.empty()) line += "  ";
                line += str(f, "src");
                line += "  ";
                line += str(f, "text");
                snap.condor.feed.push_back(line);
                if (snap.condor.feed.size() >= 16) break;
            }
        }
    }
    if (j.contains("leaders") && j["leaders"].is_array()) {
        for (auto& L : j["leaders"]) {
            if (!L.is_object()) continue;
            Leader x;
            x.id = str(L, "id");
            x.title = str(L, "title", x.id);
            x.short_addr = str(L, "short");
            if (x.short_addr.empty()) x.short_addr = str(L, "address");
            x.followed = flag(L, "followed");
            x.day_pnl = num(L, "day_pnl");
            x.week_pnl = num(L, "week_pnl");
            x.u_pnl = num(L, "u_pnl");
            x.n_positions = static_cast<int>(num(L, "n_positions"));
            snap.leaders.push_back(x);
        }
    }
    if (j.contains("writes")) snap.writes = flag(j, "writes");
    else snap.writes = true;
    snap.closes = flag(j, "closes", false);
    if (j.contains("mids") && j["mids"].is_object()) {
        for (auto it = j["mids"].begin(); it != j["mids"].end(); ++it) {
            try {
                if (it.value().is_number()) snap.mids[it.key()] = it.value().get<double>();
                else if (it.value().is_string()) snap.mids[it.key()] = std::stod(it.value().get<std::string>());
            } catch (...) {}
        }
    }
}

bool FlaskApi::can_post() const {
    if (backend == "flask" || !read_only) return true;
    return writes;
}

bool FlaskApi::ping() {
    auto r = http_request(host, port, "GET", "/api/health");
    if (r.error.empty() && r.status == 200) {
        json j = parse_or(r.body);
        if (j.contains("writes")) writes = flag(j, "writes");
        else if (backend == "flask") writes = true;
        return true;
    }
    return false;
}

std::string FlaskApi::use_flask() {
    std::string old_h = host;
    int old_p = port;
    bool old_ro = read_only;
    std::string old_b = backend;
    host = flask_host;
    port = flask_port;
    read_only = false;
    backend = "flask";
    if (ping()) return {};
    host = old_h;
    port = old_p;
    read_only = old_ro;
    backend = old_b;
    return "Flask " + flask_host + ":" + std::to_string(flask_port) + " not answering";
}

std::string FlaskApi::use_cpp() {
    std::string old_h = host;
    int old_p = port;
    bool old_ro = read_only;
    std::string old_b = backend;
    host = cpp_host;
    port = cpp_port;
    read_only = true;
    backend = "cpp-debug";
    if (ping()) return {};
    host = old_h;
    port = old_p;
    read_only = old_ro;
    backend = old_b;
    return "Spring " + cpp_host + ":" + std::to_string(cpp_port)
        + " not running  (./spring --debug)";
}

DeskSnap FlaskApi::home() {
    DeskSnap snap;
    try {
    auto r = http_request(host, port, "GET", "/api/home");
    if (!r.error.empty()) {
        snap.error = r.error;
        return snap;
    }
    if (r.status != 200) {
        snap.error = "home HTTP " + std::to_string(r.status);
        return snap;
    }
    json j = parse_or(r.body);
    snap.ts = str(j, "ts");
    if (j.contains("totals") && j["totals"].is_object()) {
        auto t = j["totals"];
        snap.totals.equity = num(t, "equity_usd");
        snap.totals.upnl = num(t, "upnl_usd");
        snap.totals.n_pos = static_cast<int>(num(t, "n_pos"));
        snap.totals.n_bots = static_cast<int>(num(t, "n_bots"));
        snap.totals.n_ok = static_cast<int>(num(t, "n_ok"));
        snap.totals.n_live = static_cast<int>(num(t, "n_live"));
    }
    if (j.contains("bots") && j["bots"].is_array()) {
        for (auto& b : j["bots"]) if (b.is_object()) snap.bots.push_back(parse_bot(b));
    }
    if (j.contains("positions") && j["positions"].is_array()) {
        for (auto& p : j["positions"]) {
            if (!p.is_object()) continue;
            Position x;
            x.bot_id = str(p, "bot_id");
            x.bot = str(p, "bot");
            x.coin = str(p, "coin");
            x.side = str(p, "side");
            x.size = num(p, "size");
            x.entry = num(p, "entry_px");
            x.mark = num(p, "mark_px");
            x.roe = num(p, "roe_pct");
            x.pnl = num(p, "pnl_usd");
            x.leverage = static_cast<int>(num(p, "leverage"));
            snap.positions.push_back(x);
        }
    }
    parse_home_extras(snap, j);
    writes = snap.writes;
    for (auto& b : snap.bots) {
        Wallet w;
        w.id = b.id;
        w.name = b.name;
        w.address = b.address;
        w.running = b.running;
        w.equity = b.equity;
        w.free_usd = b.free_usd;
        w.n_pos = b.n_pos;
        snap.wallets.push_back(w);
    }
    } catch (const std::exception& e) {
        snap.error = e.what();
    }
    return snap;
}

static ToolItem tool(const std::string& id, const std::string& cat, const std::string& type,
                     const std::string& label, const std::string& hint, const std::string& color,
                     bool unique = true, const std::string& kind = "") {
    ToolItem t;
    t.id = id; t.category = cat; t.type = type; t.label = label; t.hint = hint;
    t.color = color; t.unique = unique; t.kind = kind;
    return t;
}

void FlaskApi::load_fields(DeskSnap& snap) {
    try {
    auto r = http_request(host, port, "GET", "/api/wire/catalog");
    if (r.status != 200) return;
    json j = parse_or(r.body);
    if (j.contains("fields") && j["fields"].is_object()) {
        for (auto it = j["fields"].begin(); it != j["fields"].end(); ++it) {
            if (!it.value().is_array()) continue;
            std::vector<CtrlField> fs;
            for (auto& f : it.value()) {
                if (!f.is_object()) continue;
                CtrlField c;
                c.key = str(f, "key");
                c.label = str(f, "label", c.key);
                c.kind = str(f, "kind", "num");
                c.step = num(f, "step", 1.0);
                c.minv = num(f, "min", 0.0);
                c.maxv = num(f, "max", c.kind == "pct_frac" ? 100.0 : (c.kind == "bool" ? 1.0 : 200.0));
                if (c.kind == "bool") { c.minv = 0; c.maxv = 1; c.step = 1; }
                if (c.kind == "pct_frac" && c.maxv <= 1) c.maxv = 100;
                if (f.contains("options") && f["options"].is_array()) {
                    for (auto& o : f["options"]) if (o.is_string()) c.options.push_back(o.get<std::string>());
                }
                if (!c.key.empty()) fs.push_back(c);
            }
            snap.fields[it.key()] = fs;
        }
    }

    snap.tools.clear();
    json hunters = j.contains("hunters") && j["hunters"].is_array() ? j["hunters"] : json::array();
    if (hunters.empty()) {
        for (auto& b : snap.bots) {
            json h = {{"id", b.id}, {"name", b.name}, {"color", b.color}, {"hasBias", !b.coins.empty()}};
            hunters.push_back(h);
        }
    }
    for (auto& h : hunters) {
        if (!h.is_object()) continue;
        std::string id = str(h, "id");
        std::string name = str(h, "name", id);
        std::string col = str(h, "color", "#9dffc0");
        bool has_bias = flag(h, "hasBias", true);
        snap.tools.push_back(tool(id, "hummingbots", "bot", name, has_bias ? "BIAS hunter" : "multi · no BIAS", col));
        snap.tools.push_back(tool("ctrl-" + id, "settings", "ctrl", name + " controller", "trail / rebuy / clip", col));
        if (has_bias)
            snap.tools.push_back(tool("bias-" + id, "bias", "bias", name + " BIAS", "LONG / SHORT / FLAT", col));
    }
    if (j.contains("v2_sleepers") && j["v2_sleepers"].is_array()) {
        for (auto& h : j["v2_sleepers"]) {
            if (!h.is_object()) continue;
            std::string id = str(h, "id");
            if (id.rfind("hummingbot-v2-", 0) != 0) continue;
            std::string name = str(h, "name", id);
            std::string col = str(h, "color", "#7ecbff");
            std::string desc = str(h, "desc", "official V2 · sleeps until on WIRE");
            bool has_bias = flag(h, "hasBias", true);
            snap.tools.push_back(tool(id, "hummingbots_v2", "bot", name, desc, col));
            snap.tools.push_back(tool("ctrl-" + id, "settings", "ctrl", name + " controller", "V2 std knobs", col));
            if (has_bias)
                snap.tools.push_back(tool("bias-" + id, "bias", "bias", name + " BIAS", "LONG / SHORT / FLAT", col));
        }
    }
    if (j.contains("traders") && j["traders"].is_array()) {
        for (auto& t : j["traders"]) {
            if (!t.is_object()) continue;
            std::string id = str(t, "id");
            if (id.empty()) id = "copy-trader-" + str(t, "address").substr(0, 10);
            snap.tools.push_back(tool(id, "traders", "trader", str(t, "title", "Leader"), "wire into Copy T1/T2/T3", str(t, "color", "#7ecbff")));
        }
    }
    auto push_wallet = [&](const json& w) {
        if (!w.is_object()) return;
        std::string wid = str(w, "id");
        std::string addr = str(w, "address");
        if (wid.empty() || addr.size() < 42) return;
        for (auto& t : snap.tools) if (t.id == wid) return;
        ToolItem tw = tool(wid, "wallets", "wallet", str(w, "name", "Wallet"),
                           "subaccount", str(w, "color", "#f0b90b"));
        tw.address = addr;
        snap.tools.push_back(tw);
    };
    if (j.contains("wallets") && j["wallets"].is_array()) {
        for (auto& w : j["wallets"]) push_wallet(w);
    }
    bool any_wallet = false;
    for (auto& t : snap.tools) if (t.type == "wallet") any_wallet = true;
    bool pool_managed = flag(j, "pool_managed", false);
    if (!any_wallet) {
        auto fr = http_request(flask_host, flask_port, "GET", "/api/wire/wallets");
        if (fr.error.empty() && fr.status == 200) {
            json wj = parse_or(fr.body);
            if (flag(wj, "pool_managed", false)) pool_managed = true;
            if (wj.contains("wallets") && wj["wallets"].is_array()) {
                for (auto& w : wj["wallets"]) push_wallet(w);
            }
        }
        any_wallet = false;
        for (auto& t : snap.tools) if (t.type == "wallet") any_wallet = true;
    }
    if (!any_wallet && !pool_managed) {
        for (auto& w : snap.wallets) {
            if (w.address.size() < 42) continue;
            ToolItem tw = tool("wallet-" + w.id, "wallets", "wallet", w.name, "subaccount", "#f0b90b");
            tw.address = w.address;
            snap.tools.push_back(tw);
        }
    }
    const char* coins[][2] = {{"BTC","#f0b90b"},{"ETH","#7ecbff"},{"LTC","#9dffc0"},{"TAO","#24c3cf"},{"HBAR","#34d399"},{"POL","#c084fc"}};
    for (auto& c : coins)
        snap.tools.push_back(tool(std::string("coin-") + c[0], "coins", "coin", c[0], "wire into hunter COIN", c[1]));
    for (auto& kv : snap.mids) {
        bool have = false;
        for (auto& t : snap.tools) if (t.id == "coin-" + kv.first) have = true;
        if (!have) snap.tools.push_back(tool("coin-" + kv.first, "coins", "coin", kv.first, "wire into hunter COIN", "#9dffc0"));
    }
    snap.tools.push_back(tool("sl-pct", "settings", "pct", "Stop loss %", "wire into bot SL · ROE exit %", "#ff4d6d", false, "sl"));
    snap.tools.push_back(tool("moon-pct", "settings", "pct", "Moon %", "wire into bot MOON", "#ffd24a", false, "moon"));
    snap.tools.push_back(tool("bias-manual", "bias", "bias", "Manual BIAS", "LONG / SHORT / FLAT", "#ffc857", false));
    snap.tools.push_back(tool("desk-bias", "bias", "desk_bias", "Desk BIAS", "does not close", "#f0b90b", true, "desk"));
    snap.tools.push_back(tool("gauge-condor", "gauges", "gauge", "Condor", "Grok / heuristic BIAS", "#c084fc", true, "condor"));
    snap.tools.push_back(tool("gauge-hmm", "gauges", "gauge", "HMM / vol", "regime meter", "#c084fc", true, "hmm"));
    snap.tools.push_back(tool("gauge-vol", "gauges", "gauge", "Vol meter", "realized vol", "#a78bfa", true, "vol"));
    snap.tools.push_back(tool("gauge-bb", "gauges", "gauge", "BB width", "squeeze / expansion", "#818cf8", true, "bb"));
    snap.tools.push_back(tool("gauge-pump", "strategy", "gauge", "Post pump gauge", "quiet / pump / post", "#f0b90b", true, "pump"));
    snap.tools.push_back(tool("bias-xlate", "condor", "xlate", "Translator", "LONG SHORT FLAT CLOSE", "#34d399", false, "translator"));
    snap.tools.push_back(tool("close-xlate", "condor", "xlate", "Close translator", "CLOSE only", "#ff4d6d", false, "closer"));
    snap.tools.push_back(tool("telegram-bot", "condor", "agent", "Telegram bot", "token + chat ids", "#34d399", false, "telegram"));
    snap.tools.push_back(tool("grok-bot", "condor", "agent", "Grok bot", "xAI key + model", "#2A9FD6", false, "grok"));
    snap.tools.push_back(tool("email-bot", "condor", "agent", "Email alerts", "Health ALERT", "#ffc857", false, "email"));
    snap.tools.push_back(tool("funds", "wallets", "funds", "FUNDS bus", "all HL subs", "#f0b90b"));
    snap.tools.push_back(tool("risk-wallet", "wallets", "risk_wallet", "RISK wallet", "skim / parked", "#ff4d6d", false));
    snap.tools.push_back(tool("health-host", "system", "health", "Host CPU / RAM / disk", "LIVE + ALERT", "#9dffc0"));
    snap.tools.push_back(tool("health-net", "system", "health", "Internet", "1.1.1.1 probe", "#7ecbff"));
    snap.tools.push_back(tool("health-hlip", "system", "health", "HL REST tokens", "IP weight/min", "#f0b90b"));
    snap.tools.push_back(tool("backup-twin", "system", "backup", "1:1 backup twin", "USB / host", "#f0b90b"));
    snap.tools.push_back(tool("logs", "output", "logs", "LOGS bus", "live tail", "#7ecbff"));
    snap.tools.push_back(tool("out-xml", "output", "output", "XML file", "conf/wire_graph.xml", "#86efac", false, "xml"));
    snap.tools.push_back(tool("out-live", "output", "output", "Live stream", "positions + logs", "#7ecbff", false, "live"));
    snap.tools.push_back(tool("out-stream", "output", "output", "Stream", "Telegram / Grok feed", "#34d399", false, "stream"));
    snap.tools.push_back(tool("draw-box", "draw", "frame", "Group box", "caption to group nodes", "#3d7a55", false, "box"));
    snap.tools.push_back(tool("draw-circle", "draw", "circle", "Circle note", "caption + color", "#7ecbff", false, "circle"));
    } catch (...) {}
}

static void parse_wire_graph(DeskSnap& snap, const json& root) {
    json g = root;
    if (root.contains("graph") && root["graph"].is_object()) g = root["graph"];
    auto nodes = g.contains("nodes") ? g["nodes"] : json::array();
    auto edges = g.contains("edges") ? g["edges"] : json::array();
    snap.wire_nodes.clear();
    snap.wire_edges.clear();
    if (nodes.is_array()) {
        for (auto& n : nodes) {
            if (!n.is_object()) continue;
            WireNode w;
            w.id = str(n, "id");
            w.type = str(n, "type", "node");
            auto d = n.contains("data") && n["data"].is_object() ? n["data"] : json::object();
            w.label = str(d, "name");
            if (w.label.empty()) w.label = str(d, "label");
            if (w.label.empty()) w.label = str(d, "coin", w.id);
            w.color = str(d, "color", "#9dffc0");
            w.kind = str(d, "kind");
            w.address = str(d, "address");
            w.skim_mode = str(d, "skim_mode");
            w.armed = flag(d, "armed");
            w.bot_id = str(d, "id");
            if (w.bot_id.empty()) w.bot_id = str(d, "botId");
            if (w.type == "wallet") w.bot_id.clear();
            if (d.contains("bias") && d["bias"].is_object()) {
                for (auto it = d["bias"].begin(); it != d["bias"].end(); ++it)
                    if (it.value().is_string()) w.bias[it.key()] = it.value().get<std::string>();
            }
            auto pos = n.contains("position") && n["position"].is_object() ? n["position"] : n;
            w.x = num(pos, "x");
            w.y = num(pos, "y");
            w.extra = str(d, "extra");
            w.pct = num(d, "pct");
            if (w.extra.empty()) {
                int aw = static_cast<int>(num(d, "w"));
                int ah = static_cast<int>(num(d, "h"));
                if (aw > 0 && ah > 0) w.extra = std::to_string(aw) + "x" + std::to_string(ah);
            }
            if (!w.id.empty()) snap.wire_nodes.push_back(w);
        }
    }
    if (edges.is_array()) {
        for (auto& e : edges) {
            if (!e.is_object()) continue;
            WireEdge x;
            x.id = str(e, "id");
            x.source = str(e, "source");
            x.target = str(e, "target");
            x.source_handle = str(e, "sourceHandle");
            x.target_handle = str(e, "targetHandle");
            if (x.id.empty()) x.id = "e-" + x.source + "-" + x.target;
            if (!x.source.empty() && !x.target.empty()) snap.wire_edges.push_back(x);
        }
    }
}

void FlaskApi::load_wire(DeskSnap& snap, const std::string& board) {
    // GET only — graph / home / health JSON. No hunter writes.
    try {
    snap.wire_board = board.empty() ? "desk" : board;
    std::string path = "/api/wire/graph";
    if (snap.wire_board == "home") path = "/api/wire/board/home";
    else if (snap.wire_board == "health") path = "/api/wire/board/health";
    auto r = http_request(host, port, "GET", path);
    if (!r.error.empty()) {
        snap.wire_error = r.error;
        return;
    }
    if (r.status != 200) {
        snap.wire_error = "wire HTTP " + std::to_string(r.status);
        return;
    }
    snap.wire_error.clear();
    parse_wire_graph(snap, parse_or(r.body));
    } catch (const std::exception& e) {
        snap.wire_error = e.what();
    }
}

void FlaskApi::load_qa(DeskSnap& snap) {
    try {
    auto r = http_request(host, port, "GET", "/api/qa/week");
    if (r.status != 200) return;
    json j = parse_or(r.body);
    snap.qa_message = str(j, "message");
    snap.qa.clear();
    if (!j.contains("items") || !j["items"].is_array()) return;
    for (auto& it : j["items"]) {
        if (!it.is_object()) continue;
        QaItem q;
        q.label = str(it, "label");
        q.kind = str(it, "kind");
        q.bot_id = str(it, "bot_id");
        q.coin = str(it, "coin");
        q.value = str(it, "value");
        q.weekday = static_cast<int>(num(it, "weekday"));
        q.minute = static_cast<int>(num(it, "minute"));
        q.duration_min = static_cast<int>(num(it, "duration_min"));
        snap.qa.push_back(q);
    }
    } catch (...) {}
}

void FlaskApi::load_backup(DeskSnap& snap) {
    try {
    auto r = http_request(host, port, "GET", "/api/backup");
    if (r.status != 200) return;
    json j = parse_or(r.body);
    auto c = j.contains("check") && j["check"].is_object() ? j["check"] : j;
    snap.backup.ok = flag(c, "ok");
    snap.backup.message = str(c, "message");
    if (snap.backup.message.empty()) snap.backup.message = str(j, "message");
    snap.backup.usb_h = str(c, "usb_h");
    snap.backup.need_h = str(c, "need_h");
    } catch (...) {}
}

void FlaskApi::load_version(DeskSnap& snap) {
    try {
        auto r = http_request(host, port, "GET", "/api/versions");
        if (r.status != 200) return;
        json j = parse_or(r.body);
        snap.version.ok = flag(j, "ok");
        snap.version.up_to_date = flag(j, "up_to_date");
        snap.version.running = str(j, "running");
        snap.version.tree = str(j, "tree");
        snap.version.github = str(j, "github");
        snap.version.published = str(j, "published_at");
        snap.version.image = str(j, "image");
        snap.version.status = str(j, "status");
        snap.version.html_url = str(j, "html_url");
        snap.version.notes.clear();
        if (j.contains("notes") && j["notes"].is_array()) {
            for (auto& n : j["notes"])
                if (n.is_string()) snap.version.notes.push_back(n.get<std::string>());
        }
    } catch (...) {}
}

json FlaskApi::graph_json(const DeskSnap& snap) {
    json nodes = json::array();
    for (auto& n : snap.wire_nodes) {
        json data = {
            {"name", n.label},
            {"color", n.color},
            {"toolId", n.id},
        };
        if (!n.kind.empty()) data["kind"] = n.kind;
        if (!n.bot_id.empty() && n.type != "wallet") {
            data["botId"] = n.bot_id;
            data["id"] = n.bot_id;
        }
        if (n.type == "wallet") data["custom"] = true;
        if (!n.address.empty()) data["address"] = n.address;
        if (!n.skim_mode.empty()) data["skim_mode"] = n.skim_mode;
        if (n.type == "risk_wallet") data["armed"] = n.armed;
        if (n.type == "coin") data["coin"] = n.label;
        if (n.type == "pct") {
            data["kind"] = n.kind.empty() ? "sl" : n.kind;
            data["pct"] = n.pct > 0 ? n.pct : (n.kind == "moon" ? 50.0 : 20.0);
        }
        if (!n.bias.empty()) {
            json b = json::object();
            for (auto& kv : n.bias) b[kv.first] = kv.second;
            data["bias"] = b;
        }
        if (!n.extra.empty()) data["extra"] = n.extra;
        if (n.type == "frame" || n.type == "circle") {
            int aw = 0, ah = 0;
            if (sscanf(n.extra.c_str(), "%dx%d", &aw, &ah) == 2) {
                data["w"] = aw;
                data["h"] = ah;
            }
        }
        nodes.push_back({
            {"id", n.id},
            {"type", n.type},
            {"position", {{"x", n.x}, {"y", n.y}}},
            {"data", data},
        });
    }
    json edges = json::array();
    for (auto& e : snap.wire_edges) {
        json row = {
            {"id", e.id.empty() ? ("e-" + e.source + "-" + e.target) : e.id},
            {"source", e.source},
            {"target", e.target},
        };
        if (!e.source_handle.empty()) row["sourceHandle"] = e.source_handle;
        if (!e.target_handle.empty()) row["targetHandle"] = e.target_handle;
        edges.push_back(row);
    }
    json controllers = json::object();
    for (auto& b : snap.bots) {
        if (b.controller.is_object()) controllers[b.id] = b.controller;
    }
    json positions = json::array();
    for (auto& p : snap.positions) {
        positions.push_back({
            {"bot_id", p.bot_id},
            {"bot", p.bot},
            {"coin", p.coin},
            {"side", p.side},
            {"roe_pct", p.roe},
            {"mark_px", p.mark},
        });
    }
    return {
        {"v", 5},
        {"board", snap.wire_board.empty() ? "desk" : snap.wire_board},
        {"nodes", nodes},
        {"edges", edges},
        {"controllers", controllers},
        {"positions", positions},
        {"workaround", false},
    };
}

static std::string writes_off_msg() {
    return "enable WRITE MODE in springctl — Spring never CLOSE/OPEN positions";
}

std::string FlaskApi::post_sl(const std::string& id, double pct) {
    if (!can_post()) return writes_off_msg();
    json body = {{"id", id}, {"sl_roe_pct", pct}};
    auto r = http_request(host, port, "POST", "/api/home/sl", body.dump());
    json j = parse_or(r.body);
    if (flag(j, "closes")) return "refusing close — positions untouched";
    if (r.status != 200 || flag(j, "ok") == false) return str(j, "error", str(j, "message", "SL failed"));
    return str(j, "message", "SL saved — does not close now");
}

std::string FlaskApi::post_restart(const std::string& id) {
    if (!can_post()) return writes_off_msg();
    json body = {{"id", id}};
    auto r = http_request(host, port, "POST", "/api/home/restart", body.dump());
    json j = parse_or(r.body);
    if (flag(j, "closes")) return "refusing close — positions untouched";
    if (r.status != 200 || flag(j, "ok") == false) return str(j, "error", "restart failed");
    return str(j, "message", "restarting — positions stay on Hyperliquid");
}

std::string FlaskApi::post_hunter_gate(const std::string& id, bool active) {
    json body = {{"id", id}, {"active", active}};
    auto r = http_request(host, port, "POST", "/api/hunter_gate", body.dump());
    json j = parse_or(r.body);
    if (flag(j, "closes")) return "refusing - this control must not close";
    if (r.status != 200 || flag(j, "ok") == false)
        return str(j, "error", active ? "activate failed" : "deactivate failed");
    if (active)
        return str(j, "message", "ACTIVE green. Spring does not touch positions.");
    return str(j, "message", "DEACTIVATED red - 0 HL IP. Spring does not touch positions.");
}

std::string FlaskApi::post_desk_bias(const std::string& bias) {
    if (!can_post()) return writes_off_msg();
    json body = {{"bias", bias}};
    auto r = http_request(host, port, "POST", "/api/home/desk_bias", body.dump());
    json j = parse_or(r.body);
    if (flag(j, "closes")) return "refusing close — positions untouched";
    if (r.status != 200 || flag(j, "ok") == false) return str(j, "error", "desk BIAS failed");
    return str(j, "message", "desk BIAS " + bias + " — does not close");
}

std::string FlaskApi::post_bias(const std::string& id, const std::string& coin, const std::string& bias) {
    if (!can_post()) return writes_off_msg();
    json body = {{"id", id}, {"coin", coin}, {"bias", bias}};
    auto r = http_request(host, port, "POST", "/api/wire/bias", body.dump());
    json j = parse_or(r.body);
    if (flag(j, "closes")) return "refusing close — positions untouched";
    if (r.status != 200 || flag(j, "ok") == false) return str(j, "error", "BIAS failed");
    return str(j, "message", coin + " BIAS " + bias + " — does not close");
}

std::string FlaskApi::post_close_coin(const std::string& id, const std::string& coin) {
    if (!can_post()) return writes_off_msg();
    json body = {{"id", id}, {"coin", coin}};
    auto r = http_request(host, port, "POST", "/api/home/close", body.dump());
    json j = parse_or(r.body);
    if (r.status != 200 || flag(j, "ok") == false) return str(j, "error", "close queue failed");
    return str(j, "message", "close queued for " + coin);
}

std::string FlaskApi::post_open100(const std::string& id, const std::string& coin, const std::string& side) {
    if (!can_post()) return writes_off_msg();
    json body = {{"id", id}, {"coin", coin}, {"pct", 1}};
    std::string s = side;
    for (char& c : s) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    if (s == "LONG" || s == "SHORT") body["side"] = s;
    auto r = http_request(host, port, "POST", "/api/home/open", body.dump());
    json j = parse_or(r.body);
    if (r.status != 200 || flag(j, "ok") == false) return str(j, "error", "100% open queue failed");
    return str(j, "message", "100% leftover queued for " + coin);
}

std::string FlaskApi::post_wire_pack(const std::string& action) {
    if (!can_post()) return writes_off_msg();
    json body = {{"action", action}};
    auto r = http_request(host, port, "POST", "/api/wire/pack", body.dump());
    json j = parse_or(r.body);
    if (r.status != 200 || flag(j, "ok") == false) return str(j, "error", "wire pack failed");
    return str(j, "message", action == "restore" ? "wire pack restored" : "wire pack saved");
}

std::string FlaskApi::post_ctrl(const std::string& id, const std::string& key, const nlohmann::json& value) {
    if (!can_post()) return writes_off_msg();
    json body = {{"id", id}, {"key", key}, {"value", value}};
    auto r = http_request(host, port, "POST", "/api/wire/controller", body.dump());
    json j = parse_or(r.body);
    if (flag(j, "closes")) return "refusing close — positions untouched";
    if (r.status != 200 || flag(j, "ok") == false) return str(j, "error", "controller failed");
    return str(j, "message", key + " saved — no HL close/open");
}

std::string FlaskApi::post_ctrl_map(const std::string& id, const nlohmann::json& values) {
    if (!can_post()) return writes_off_msg();
    json body = {{"id", id}, {"values", values.is_object() ? values : json::object()}};
    auto r = http_request(host, port, "POST", "/api/wire/controller", body.dump());
    json j = parse_or(r.body);
    if (flag(j, "closes")) return "refusing close — positions untouched";
    if (r.status != 200 || flag(j, "ok") == false) return str(j, "error", "controller failed");
    return str(j, "message", "pmm knobs saved — no HL close/open");
}

static AuditTrader parse_audit_trader(const json& t, int fallback_num = 0) {
    AuditTrader at;
    at.num = static_cast<int>(num(t, "trader", fallback_num));
    at.address = str(t, "address");
    at.short_addr = str(t, "short");
    if (at.address.size() > 16)
        at.short_addr = at.address.substr(0, 10) + "..." + at.address.substr(at.address.size() - 6);
    at.audited_at = str(t, "audited_at");
    at.cap = str(t, "capacity_flag");
    at.sleep = str(t, "sleep_flag");
    std::string dir = str(t, "last_open_dir");
    std::string coin = str(t, "last_open_coin");
    std::string when = str(t, "last_open_at");
    at.last_open = dir;
    if (!coin.empty()) at.last_open += (at.last_open.empty() ? "" : " ") + coin;
    if (!when.empty()) at.last_open += (at.last_open.empty() ? "" : "  ") + when;
    at.error = str(t, "error");
    at.equity = num(t, "equity");
    at.free_margin = num(t, "free_margin");
    at.upnl = num(t, "u_pnl_total");
    at.sleep_hours = num(t, "sleep_hours");
    at.n_pos = static_cast<int>(num(t, "n_positions"));
    at.can_open = flag(t, "can_open");
    at.stale = flag(t, "stale");
    at.ok = flag(t, "ok", true);
    if (t.contains("positions") && t["positions"].is_array()) {
        int shown = 0;
        for (auto& p : t["positions"]) {
            if (!p.is_object() || shown >= 8) continue;
            AuditPos ap;
            ap.coin = str(p, "coin");
            ap.side = str(p, "side");
            ap.size = num(p, "size");
            ap.entry = num(p, "entry_px", num(p, "entry"));
            ap.upnl = num(p, "u_pnl", num(p, "u_pnl_usd"));
            at.positions.push_back(ap);
            shown++;
        }
    }
    return at;
}

static void ingest_traders(CopyAudit& a, const json& src) {
    if (src.is_array()) {
        for (auto& t : src) {
            if (t.is_object()) a.traders.push_back(parse_audit_trader(t));
        }
        return;
    }
    if (!src.is_object()) return;
    for (int i = 1; i <= 8; ++i) {
        std::string key = std::to_string(i);
        if (!src.contains(key) || !src[key].is_object()) continue;
        a.traders.push_back(parse_audit_trader(src[key], i));
    }
    if (!a.traders.empty()) return;
    for (auto it = src.begin(); it != src.end(); ++it) {
        if (!it.value().is_object()) continue;
        int n = 0;
        try { n = std::stoi(it.key()); } catch (...) { n = 0; }
        a.traders.push_back(parse_audit_trader(it.value(), n));
    }
}

static std::string fmt_hist_obj(const json& h) {
    char line[176];
    snprintf(line, sizeof(line), "T#%d  %s  %s/%s  eq $%.0f  free $%.0f  pos %d  %s",
             static_cast<int>(num(h, "trader")),
             str(h, "audited_at").c_str(),
             str(h, "capacity_flag").c_str(),
             str(h, "sleep_flag").c_str(),
             num(h, "equity"),
             num(h, "free_margin"),
             static_cast<int>(num(h, "n_positions")),
             flag(h, "can_open") ? "can_open" : "no_open");
    return line;
}

static std::string parse_audit_schedule(const json& j) {
    json s = json::object();
    if (j.contains("schedule") && j["schedule"].is_object()) s = j["schedule"];
    else if (j.contains("latest") && j["latest"].is_object()
             && j["latest"].contains("schedule") && j["latest"]["schedule"].is_object())
        s = j["latest"]["schedule"];
    auto slot = [&](const char* k, const char* fb) {
        std::string v = str(s, k, fb);
        return v.empty() ? std::string(fb) : v;
    };
    return "daily cron  T#1 " + slot("1", "01:00")
        + "  T#2 " + slot("2", "06:00")
        + "  T#3 " + slot("3", "11:00");
}

std::string FlaskApi::load_copy_audit(DeskSnap& snap) {
    auto r = http_request(host, port, "GET", "/api/trader_audits");
    if (!r.error.empty()) {
        snap.copy_audit = CopyAudit{};
        snap.copy_audit.message = r.error;
        snap.copy_audit.schedule = "daily cron  T#1 01:00  T#2 06:00  T#3 11:00";
        return r.error;
    }
    json j = parse_or(r.body);
    CopyAudit a;
    a.ok = flag(j, "ok", true);
    a.worker = flag(j, "worker_running");
    a.updated = str(j, "updated_at");
    a.timezone = str(j, "timezone");
    a.log_mtime = str(j, "log_mtime");
    a.message = str(j, "error");
    a.schedule = parse_audit_schedule(j);
    json src = json::array();
    if (j.contains("traders") && (j["traders"].is_array() || j["traders"].is_object()))
        src = j["traders"];
    else if (j.contains("latest") && j["latest"].is_object()) {
        auto& L = j["latest"];
        if (a.updated.empty()) a.updated = str(L, "updated_at");
        if (a.timezone.empty()) a.timezone = str(L, "timezone");
        if (L.contains("traders")) src = L["traders"];
    }
    ingest_traders(a, src);
    if (j.contains("history") && j["history"].is_array()) {
        for (auto& h : j["history"]) {
            if (h.is_string()) a.history.push_back(h.get<std::string>());
            else if (h.is_object()) a.history.push_back(fmt_hist_obj(h));
            if (a.history.size() >= 20) break;
        }
    }
    snap.copy_audit = a;
    if (!a.traders.empty()) {
        char msg[96];
        snprintf(msg, sizeof(msg), "audit loaded · %d leaders · daily cron",
                 static_cast<int>(a.traders.size()));
        return msg;
    }
    return a.message.empty() ? "audit empty — no latest.json rows" : a.message;
}

std::string FlaskApi::run_copy_audit(const std::string& trader) {
    json body = {{"trader", trader}};
    auto r = http_request(host, port, "POST", "/api/trader_audits/run", body.dump());
    json j = parse_or(r.body);
    if (r.status != 200 || flag(j, "ok") == false)
        return str(j, "error", "queue audit failed");
    return str(j, "message", "audit queued — no HL trades");
}

std::string FlaskApi::post_copy_mute(int trader, bool mute) {
    if (!can_post()) return writes_off_msg();
    json body = {{"trader", trader}, {"mute", mute}};
    auto r = http_request(host, port, "POST", "/api/hl_trader_follow", body.dump());
    json j = parse_or(r.body);
    if (flag(j, "closes")) return "refusing close — positions untouched";
    if (r.status != 200 || flag(j, "ok") == false)
        return str(j, "error", "mute failed");
    return str(j, "message", mute
        ? "muted — Copy flattens that trader's clips"
        : "unmuted — copy resumes");
}

std::string FlaskApi::post_copy_slots(int trader, int slots) {
    if (!can_post()) return writes_off_msg();
    json body = {{"trader", trader}, {"slots", slots}};
    auto r = http_request(host, port, "POST", "/api/hl_trader_slots", body.dump());
    json j = parse_or(r.body);
    if (r.status != 200 || flag(j, "ok") == false) return str(j, "error", "slots failed");
    return str(j, "message", "T#" + std::to_string(trader) + " follow " + std::to_string(slots));
}

void FlaskApi::post_live_knobs(const std::string& id, const nlohmann::json& ctrl) {
    json body = ctrl.is_object() ? ctrl : json::object();
    body["id"] = id;
    http_request(host, port, "POST", "/api/live/knobs", body.dump(), 80);
}

bool FlaskApi::pull_live_knobs(Bot& b) {
    if (b.id.empty()) return false;
    auto r = http_request(host, port, "GET",
                          "/api/live/knobs?id=" + b.id, "", 80);
    if (r.status != 200 || !r.error.empty()) return false;
    json j = parse_or(r.body);
    if (!j.is_object()) return false;
    if (j.contains("controller") && j["controller"].is_object()) j = j["controller"];
    bool ch = false;
    if (!b.controller.is_object()) b.controller = json::object();
    for (auto it = j.begin(); it != j.end(); ++it) {
        auto k = it.key();
        if (k == "id" || k == "ok" || k == "source" || k == "updated" || k == "message")
            continue;
        if (b.controller.contains(k) && b.controller[k].dump() == it.value().dump())
            continue;
        b.controller[k] = it.value();
        ch = true;
    }
    return ch;
}

nlohmann::json FlaskApi::load_presets(const std::string& id) {
    auto r = http_request(host, port, "GET", "/api/wire/presets?id=" + id, "", 400);
    json j = parse_or(r.body);
    if (!j.is_object()) return json::array();
    if (j.contains("presets") && j["presets"].is_array()) return j["presets"];
    return json::array();
}

std::string FlaskApi::save_preset(const std::string& id, const std::string& name,
                                  const nlohmann::json& values) {
    if (!can_post()) return writes_off_msg();
    json body = {{"id", id}, {"op", "save"}, {"name", name},
                 {"values", values.is_object() ? values : json::object()}};
    auto r = http_request(host, port, "POST", "/api/wire/presets", body.dump());
    json j = parse_or(r.body);
    if (r.status != 200 || flag(j, "ok") == false)
        return str(j, "error", "preset save failed");
    return str(j, "message", "saved " + name);
}

nlohmann::json FlaskApi::apply_preset(const std::string& id, const std::string& name) {
    json body = {{"id", id}, {"op", "apply"}, {"name", name}};
    auto r = http_request(host, port, "POST", "/api/wire/presets", body.dump());
    return parse_or(r.body);
}

std::string FlaskApi::compile_wire(DeskSnap& snap, bool workaround) {
    try {
        json body = graph_json(snap);
        body["workaround"] = workaround;
        body["board"] = snap.wire_board.empty() ? "desk" : snap.wire_board;
        auto r = http_request(flask_host, flask_port, "POST", "/api/wire/compile", body.dump());
        auto& c = snap.compile;
        c = CompileResult{};
        if (!r.error.empty()) {
            c.message = r.error;
            return r.error;
        }
        json j = parse_or(r.body);
        c.ok = flag(j, "ok");
        c.can_integrate = flag(j, "can_integrate");
        c.positions_untouched = flag(j, "positions_untouched", true);
        c.workaround_used = flag(j, "workaround_used");
        c.message = str(j, "message");
        if (c.message.empty()) c.message = str(j, "error");
        c.compile_id = str(j, "compile_id");
        c.board = str(j, "board", snap.wire_board);
        c.n_blockers = static_cast<int>(num(j, "n_blockers"));
        c.n_new = static_cast<int>(num(j, "n_new_routes"));
        c.n_rm = static_cast<int>(num(j, "n_removed_routes"));
        c.n_knobs = static_cast<int>(num(j, "n_knobs"));
        c.n_parked = static_cast<int>(num(j, "n_parked"));
        c.n_pos = static_cast<int>(num(j, "n_pos"));
        if (j.contains("desk_bias") && j["desk_bias"].is_object()) {
            auto& d = j["desk_bias"];
            c.desk_bias = "live " + str(d, "live", "unset");
            if (!str(d, "canvas").empty()) c.desk_bias += " · canvas " + str(d, "canvas");
            if (!str(d, "note").empty()) c.desk_bias += " · " + str(d, "note");
        }
        if (j.contains("bands") && j["bands"].is_object()) {
            auto join_arr = [](const json& a) {
                std::string s;
                if (!a.is_array()) return s;
                for (auto& x : a) {
                    if (!s.empty()) s += " · ";
                    if (x.is_string()) s += x.get<std::string>();
                }
                return s;
            };
            c.bands.push_back("TOP · " + join_arr(j["bands"].value("top", json::array())));
            c.bands.push_back("MIDDLE · " + join_arr(j["bands"].value("mid", json::array())));
            c.bands.push_back("BOTTOM · " + join_arr(j["bands"].value("bot", json::array())));
        }
        if (j.contains("blockers") && j["blockers"].is_array())
            for (auto& b : j["blockers"]) {
                if (!b.is_object()) continue;
                std::string s = str(b, "bot", str(b, "bot_id")) + "  " + str(b, "reason");
                if (!str(b, "workaround").empty()) s += "  · workaround: " + str(b, "workaround");
                c.blockers.push_back(s);
            }
        if (j.contains("parked") && j["parked"].is_array())
            for (auto& p : j["parked"]) {
                if (!p.is_object()) continue;
                c.parked.push_back(str(p, "bot") + " " + str(p, "key") + ": canvas "
                    + json_preview(p.contains("canvas") ? p["canvas"] : json()) + " → live "
                    + json_preview(p.contains("live") ? p["live"] : json()));
            }
        if (j.contains("hunters") && j["hunters"].is_array())
            for (auto& h : j["hunters"]) {
                if (!h.is_object()) continue;
                std::string s = str(h, "name", str(h, "id"));
                std::string clips;
                if (h.contains("positions") && h["positions"].is_array()) {
                    for (auto& p : h["positions"]) {
                        if (!p.is_object()) continue;
                        if (!clips.empty()) clips += ", ";
                        clips += str(p, "coin") + " " + str(p, "side");
                        if (p.contains("roe_pct") && !p["roe_pct"].is_null()) {
                            char r[24];
                            snprintf(r, sizeof(r), " %+.1f%%", num(p, "roe_pct"));
                            clips += r;
                        }
                    }
                }
                s += " · " + (clips.empty() ? std::string("flat") : clips);
                int nk = static_cast<int>(num(h, "n_knobs"));
                int nb = static_cast<int>(num(h, "n_block"));
                int nw = static_cast<int>(num(h, "n_warn"));
                if (nk) s += " · " + std::to_string(nk) + " knob(s)";
                if (nb) s += " · " + std::to_string(nb) + " close-risk";
                if (nw) s += " · " + std::to_string(nw) + " clip-warn";
                c.hunters.push_back(s);
            }
        if (j.contains("wiring") && j["wiring"].is_array())
            for (auto& w : j["wiring"]) {
                if (!w.is_object()) continue;
                std::string st = str(w, "status");
                c.wiring.push_back((st == "removed" ? "− " : "+ ") + str(w, "kind") + "  " + str(w, "label"));
            }
        if (j.contains("knobs") && j["knobs"].is_array())
            for (auto& k : j["knobs"]) {
                if (!k.is_object()) continue;
                c.knobs.push_back(str(k, "bot") + " " + str(k, "key") + ": "
                    + json_preview(k.contains("from") ? k["from"] : json()) + " → "
                    + json_preview(k.contains("to") ? k["to"] : json()));
            }
        if (j.contains("bias") && j["bias"].is_array())
            for (auto& b : j["bias"]) {
                if (!b.is_object()) continue;
                std::string s = str(b, "bot") + " ";
                if (b.contains("diffs") && b["diffs"].is_array()) {
                    bool first = true;
                    for (auto& d : b["diffs"]) {
                        if (!d.is_object()) continue;
                        if (!first) s += " · ";
                        first = false;
                        s += str(d, "coin") + " " + str(d, "from") + "→" + str(d, "to");
                    }
                }
                c.bias.push_back(s);
            }
        if (j.contains("risk") && j["risk"].is_array())
            for (auto& rsk : j["risk"]) {
                if (!rsk.is_object()) continue;
                std::string s = str(rsk, "hunter") + " → " + str(rsk, "wallet");
                if (!str(rsk, "address").empty()) s += " " + str(rsk, "address").substr(0, 10) + "…";
                if (str(rsk, "mode") == "two_wins") s += " · 2 wins in a row → 2nd win only";
                else {
                    if (rsk.contains("skim_pct") && !rsk["skim_pct"].is_null())
                        s += " · " + json_preview(rsk["skim_pct"]) + "%";
                    if (rsk.contains("every_h") && !rsk["every_h"].is_null())
                        s += " / " + json_preview(rsk["every_h"]) + "h";
                    if (rsk.contains("min_usd") && !rsk["min_usd"].is_null())
                        s += " min $" + json_preview(rsk["min_usd"]);
                }
                if (flag(rsk, "armed")) s += " · armed";
                c.risk.push_back(s);
            }
        if (j.contains("warnings") && j["warnings"].is_array())
            for (auto& w : j["warnings"]) {
                if (w.is_string()) c.warnings.push_back(w.get<std::string>());
                else if (w.is_object()) c.warnings.push_back(str(w, "reason", str(w, "label")));
            }
        if (r.status != 200 && c.message.empty())
            return "compile HTTP " + std::to_string(r.status);
        return c.message.empty() ? (c.can_integrate ? "COMPILE OK" : "COMPILE BLOCKED") : c.message;
    } catch (const std::exception& e) {
        return e.what();
    }
}

std::string FlaskApi::integrate_wire(DeskSnap& snap) {
    try {
        if (!snap.compile.can_integrate)
            return snap.compile.message.empty()
                ? "read the compile report first — Integrate stays off until COMPILE OK"
                : snap.compile.message;
        json body = graph_json(snap);
        body["workaround"] = snap.compile.workaround_used;
        body["board"] = snap.wire_board.empty() ? "desk" : snap.wire_board;
        if (!snap.compile.compile_id.empty()) body["compile_id"] = snap.compile.compile_id;
        // Same Flask endpoint as the website. Spring never CLOSE/OPEN.
        auto r = http_request(flask_host, flask_port, "POST", "/api/wire/integrate", body.dump(), 90000);
        if (!r.error.empty()) return r.error;
        json j = parse_or(r.body);
        if (r.status != 200 || flag(j, "ok") == false)
            return str(j, "error", str(j, "message", "integrate failed"));
        snap.compile.can_integrate = false;
        return str(j, "message", "integrated");
    } catch (const std::exception& e) {
        return e.what();
    }
}

std::string FlaskApi::save_snapshot(const DeskSnap& snap) {
    if (!can_post()) return writes_off_msg();
    try {
        json body = graph_json(snap);
        std::string path = "/api/wire/graph";
        if (snap.wire_board == "home") path = "/api/wire/board/home";
        else if (snap.wire_board == "health") path = "/api/wire/board/health";
        auto r = http_request(host, port, "POST", path, body.dump());
        if (!r.error.empty()) return r.error;
        json j = parse_or(r.body);
        if (r.status != 200 || flag(j, "ok") == false)
            return str(j, "error", "save failed");
        int nf = 0, nc = 0;
        for (auto& n : snap.wire_nodes) {
            if (n.type == "frame") nf++;
            if (n.type == "circle") nc++;
        }
        std::string msg = str(j, "message", "cosmetics saved");
        if (nf || nc)
            msg += " · " + std::to_string(nf) + " box / " + std::to_string(nc) + " circle";
        msg += " · wires not applied — Compile to change live routing";
        return msg;
    } catch (const std::exception& e) {
        return e.what();
    }
}

std::string FlaskApi::save_layout(const DeskSnap& snap) {
    if (!can_post()) return {};
    try {
        json nodes = json::array();
        for (auto& n : snap.wire_nodes) {
            json data = {{"name", n.label}, {"color", n.color}, {"toolId", n.id}};
            if (!n.kind.empty()) data["kind"] = n.kind;
            if (!n.extra.empty()) data["extra"] = n.extra;
            if (n.type == "frame" || n.type == "circle") {
                int aw = 0, ah = 0;
                if (sscanf(n.extra.c_str(), "%dx%d", &aw, &ah) == 2) {
                    data["w"] = aw;
                    data["h"] = ah;
                }
            }
            nodes.push_back({
                {"id", n.id},
                {"type", n.type},
                {"position", {{"x", n.x}, {"y", n.y}}},
                {"data", data},
            });
        }
        json body = {{"nodes", nodes}};
        auto r = http_request(host, port, "POST", "/api/wire/layout", body.dump());
        if (!r.error.empty()) return r.error;
        json j = parse_or(r.body);
        if (r.status != 200 || flag(j, "ok") == false)
            return str(j, "error", "layout save failed");
        return str(j, "message", "layout saved");
    } catch (const std::exception& e) {
        return e.what();
    }
}

std::string FlaskApi::wallet_add(const std::string& name, const std::string& address) {
    try {
        json body = {{"name", name}, {"address", address}, {"use_vault", true}};
        auto r = http_request(flask_host, flask_port, "POST", "/api/wire/wallets", body.dump());
        if (!r.error.empty()) return r.error;
        json j = parse_or(r.body);
        if (r.status != 200 || flag(j, "ok") == false)
            return str(j, "error", "wallet was not added");
        return str(j, "message", "wallet added");
    } catch (const std::exception& e) {
        return e.what();
    }
}

std::string FlaskApi::wallet_rename(const std::string& id, const std::string& name) {
    try {
        json body = {{"op", "rename"}, {"id", id}, {"name", name}};
        auto r = http_request(flask_host, flask_port, "POST", "/api/wire/wallets", body.dump());
        if (!r.error.empty()) return r.error;
        json j = parse_or(r.body);
        if (r.status != 200 || flag(j, "ok") == false)
            return str(j, "error", "wallet was not renamed");
        return str(j, "message", "renamed");
    } catch (const std::exception& e) {
        return e.what();
    }
}

std::string FlaskApi::wallet_delete(const std::string& id) {
    try {
        json body = {{"id", id}};
        auto r = http_request(flask_host, flask_port, "DELETE", "/api/wire/wallets", body.dump());
        if (!r.error.empty()) return r.error;
        json j = parse_or(r.body);
        if (r.status != 200 || flag(j, "ok") == false)
            return str(j, "error", "wallet was not removed");
        return str(j, "message", "wallet removed from the wire");
    } catch (const std::exception& e) {
        return e.what();
    }
}

std::string FlaskApi::sleeper_start(const std::string& id) {
    if (!can_post()) return writes_off_msg();
    json body = {{"id", id}, {"op", "start"}};
    auto r = http_request(host, port, "POST", "/api/wire/sleeper", body.dump(), 90000);
    json j = parse_or(r.body);
    if (r.status != 200 || flag(j, "ok") == false)
        return str(j, "error", "sleeper start failed");
    return str(j, "message", "V2 started");
}

std::string FlaskApi::sleeper_stop(const std::string& id) {
    if (!can_post()) return writes_off_msg();
    json body = {{"id", id}, {"op", "stop"}};
    auto r = http_request(host, port, "POST", "/api/wire/sleeper", body.dump(), 20000);
    json j = parse_or(r.body);
    if (r.status != 200 || flag(j, "ok") == false)
        return str(j, "error", "sleeper stop failed");
    return str(j, "message", "V2 asleep");
}
