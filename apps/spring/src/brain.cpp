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

#include "brain.hpp"
#include "jsonio.hpp"
#include "matrix_sql.hpp"
#include "hl_key.hpp"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <iostream>
#include <map>
#include <mutex>
#include <set>
#include <sstream>
#include <unistd.h>
#include <dirent.h>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <vector>

using nlohmann::json;

static std::mutex g_mu;
static std::mutex g_write_mu;
static std::mutex g_live_mu;
static json g_live_knobs = json::object();
static std::set<std::string> g_running;
static std::set<std::string> g_paused;
static double g_docker_ts = 0;
static std::atomic<bool> g_writes{false};
static std::atomic<bool> g_stop{false};
static const char* WRITES_PATH = "/tmp/freiquell-spring-writes";

static bool writes_on() { return g_writes.load(); }

void spring_request_stop() { g_stop.store(true); }
bool spring_stop_requested() { return g_stop.load(); }

static void writes_set(bool on) {
    g_writes.store(on);
    FILE* f = std::fopen(WRITES_PATH, "w");
    if (f) { std::fputc(on ? '1' : '0', f); std::fclose(f); }
}

static void writes_load() {
    FILE* f = std::fopen(WRITES_PATH, "r");
    if (!f) return;
    int c = std::fgetc(f);
    std::fclose(f);
    g_writes.store(c == '1');
}

static int _writes_boot = (writes_load(), 0);

static std::string join(const std::string& a, const std::string& b) {
    if (a.empty()) return b;
    if (a.back() == '/') return a + b;
    return a + "/" + b;
}

static bool valid_id(const std::string& id) {
    if (id.empty() || id.size() > 80) return false;
    for (unsigned char c : id) {
        if (!std::isalnum(c) && c != '-' && c != '_') return false;
    }
    return true;
}

static std::string dirname_of(const std::string& path) {
    auto slash = path.find_last_of('/');
    if (slash == std::string::npos) return ".";
    if (slash == 0) return "/";
    return path.substr(0, slash);
}

static const char* SQL_APPLY_PY =
    "import json, os, sys\n"
    "conf = os.environ.get('SPRING_CONF') or '/conf'\n"
    "py = os.environ.get('SPRING_PY') or '/app'\n"
    "for p in (py, '/app', '/home/hummingbot/scripts',\n"
    "          os.path.join(os.path.dirname(conf), 'scripts'),\n"
    "          os.path.join(os.path.dirname(conf), 'dashboard')):\n"
    "    if p and p not in sys.path:\n"
    "        sys.path.insert(0, p)\n"
    "import matrix_store\n"
    "matrix_store.init(os.path.join(conf, 'matrix.db'))\n"
    "j = json.load(open(os.path.join(conf, '.spring_sql_job.json')))\n"
    "op = j.get('op')\n"
    "bot = str(j.get('bot_id') or '')\n"
    "if op == 'bias':\n"
    "    matrix_store.bias_set_map(bot, j.get('mapping') or {})\n"
    "elif op == 'kv':\n"
    "    for k, v in (j.get('values') or {}).items():\n"
    "        if k in ('updated', 'source'):\n"
    "            continue\n"
    "        matrix_store.kv_set(bot, str(k), v, 'spring')\n"
    "elif op == 'command':\n"
    "    matrix_store.command_enqueue(bot, str(j.get('kind') or ''), j.get('payload') or {}, 'spring')\n"
    "elif op == 'hl_runtime':\n"
    "    p = os.path.join(conf, 'hl_copy_runtime.json')\n"
    "    prev = {}\n"
    "    if os.path.exists(p):\n"
    "        try:\n"
    "            prev = json.load(open(p)) or {}\n"
    "        except Exception:\n"
    "            prev = {}\n"
    "    if not isinstance(prev, dict):\n"
    "        prev = {}\n"
    "    prev.update(j.get('updates') or {})\n"
    "    json.dump(prev, open(p, 'w'), indent=2)\n"
    "    for k, v in (j.get('updates') or {}).items():\n"
    "        if k in ('ts', 'updated', 'source'):\n"
    "            continue\n"
    "        matrix_store.kv_set('hummingbot-hl', str(k), v, 'spring')\n"
    "elif op == 'preset_save':\n"
    "    matrix_store.preset_save(bot, j.get('name'), j.get('values') or {}, bool(j.get('locked')))\n"
    "elif op == 'preset_seed':\n"
    "    matrix_store.seed_controller_presets()\n"
    "elif op == 'status_dump':\n"
    "    out = {}\n"
    "    bias = {}\n"
    "    for hid in getattr(matrix_store, 'HUNTER_IDS', ()):\n"
    "        row = matrix_store.status_get(hid)\n"
    "        if isinstance(row, dict) and row:\n"
    "            out[hid] = row\n"
    "        b = matrix_store.bias_map(hid)\n"
    "        if isinstance(b, dict) and b:\n"
    "            bias[hid] = b\n"
    "    json.dump(out, open(os.path.join(conf, '.spring_sql_status.json'), 'w'))\n"
    "    json.dump(bias, open(os.path.join(conf, '.spring_sql_bias.json'), 'w'))\n"
    "else:\n"
    "    sys.exit(2)\n";

// Preset seed still lives in Python. Clicks, bias, kv, and the home
// snapshot stay in-process. Python is only the fallback if SQLite will not open.
static bool sql_apply_py(const Brain& br, const json& job) {
    auto jobp = join(br.conf, ".spring_sql_job.json");
    auto pyp = join(br.conf, ".spring_sql_apply.py");
    if (!write_json(jobp, job)) return false;
    {
        std::ofstream out(pyp);
        if (!out) return false;
        out << SQL_APPLY_PY;
    }
    const std::string conf = br.conf;
    const std::string root = dirname_of(conf);
    const std::string scripts = join(root, "scripts");
    const std::string log = "/tmp/spring_sql_write.log";
    // Host python first — Close/Open must not depend on hummingbot-dashboard.
    const std::string cmds[] = {
        "SPRING_CONF='" + conf + "' SPRING_PY='" + scripts + "' python3 '" + pyp + "' >" + log + " 2>&1",
        "docker exec -e SPRING_CONF=/conf -e SPRING_PY=/app -w /app hummingbot-dashboard python3 /conf/.spring_sql_apply.py >>" + log + " 2>&1",
        "docker exec -e SPRING_CONF=/home/hummingbot/conf -e SPRING_PY=/home/hummingbot/scripts hummingbot-dman-maker python3 /home/hummingbot/conf/.spring_sql_apply.py >>" + log + " 2>&1",
    };
    for (const auto& cmd : cmds) {
        int rc = std::system(cmd.c_str());
        if (rc == 0) return true;
    }
    std::cerr << "spring: sqlite write failed — JSON fallback still written (see "
              << log << ")\n";
    return false;
}

static bool sql_apply(const Brain& br, const json& job) {
    std::string op = jstr(job, "op");
    if (op == "preset_save" || op == "preset_seed")
        return sql_apply_py(br, job);
    if (matrix_sql_apply(br.conf, job)) return true;
    std::cerr << "spring: in-process sqlite " << op << " failed — python fallback\n";
    return sql_apply_py(br, job);
}

static json bias_mapping(const json& cur) {
    json m = json::object();
    if (!cur.is_object()) return m;
    for (auto it = cur.begin(); it != cur.end(); ++it) {
        std::string k = it.key();
        if (k == "updated" || k == "source" || k == "coins" || k == "bias") continue;
        if (!it.value().is_string()) continue;
        std::string v = it.value().get<std::string>();
        for (char& c : v) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        if (v == "LONG" || v == "SHORT" || v == "FLAT") m[k] = v;
    }
    return m;
}

static std::string basename_only(const std::string& path) {
    std::string name = path;
    auto slash = name.find_last_of("/\\");
    if (slash != std::string::npos) name = name.substr(slash + 1);
    if (name.find("..") != std::string::npos) return {};
    return name;
}

static json registry(const Brain& br) {
    auto j = read_json(join(br.conf, "wire_registry.json"));
    if (j.contains("hunters") && j["hunters"].is_array()) return j["hunters"];
    return json::array();
}

// Caller holds g_mu. One docker ps fills both sets.
// Paused containers stay in `docker ps` as "Up … (Paused)" and spend no HL IP.
static void docker_refresh_locked(double now) {
    if (g_docker_ts != 0 && now - g_docker_ts < 2.0) return;
    FILE* fp = popen("docker ps --format '{{.Names}}|{{.Status}}' 2>/dev/null", "r");
    if (!fp) return;
    std::set<std::string> running;
    std::set<std::string> paused;
    char line[512];
    while (fgets(line, sizeof(line), fp)) {
        std::string row = line;
        while (!row.empty() && (row.back() == '\n' || row.back() == '\r')) row.pop_back();
        auto bar = row.find('|');
        std::string n = bar == std::string::npos ? row : row.substr(0, bar);
        std::string st = bar == std::string::npos ? std::string() : row.substr(bar + 1);
        if (!valid_id(n)) continue;
        running.insert(n);
        if (st.find("Paused") != std::string::npos || st.find("paused") != std::string::npos)
            paused.insert(n);
    }
    int rc = pclose(fp);
    if (rc != 0 && running.empty()) return;
    g_running.swap(running);
    g_paused.swap(paused);
    g_docker_ts = now;
}

static bool docker_running(const std::string& name) {
    if (!valid_id(name)) return false;
    double now = static_cast<double>(std::time(nullptr));
    std::lock_guard<std::mutex> lk(g_mu);
    docker_refresh_locked(now);
    return g_running.count(name) != 0;
}

static bool docker_paused(const std::string& name) {
    if (!valid_id(name)) return false;
    double now = static_cast<double>(std::time(nullptr));
    std::lock_guard<std::mutex> lk(g_mu);
    docker_refresh_locked(now);
    return g_paused.count(name) != 0;
}

// Hunters on this matrix whose Hyperliquid connector spends IP weight.
// Dashboard, gateway, audit, version, and condor are not in this list.
static bool hunter_pausable(const std::string& id) {
    if (!valid_id(id)) return false;
    static const char* kAllow[] = {
        "hummingbot-hl",
        "hummingbot-bn-swing",
        "hummingbot-helliot",
        "hummingbot-surfn",
        "hummingbot-shortn",
        "hummingbot-dman",
        "hummingbot-onlyup",
        "hummingbot-dman-maker",
        nullptr,
    };
    for (int i = 0; kAllow[i]; ++i)
        if (id == kAllow[i]) return true;
    return false;
}

static std::string wallet_of(const json& accounts, const std::string& id, const json& status) {
    if (jhas(status, "subaccount")) {
        auto s = jstr(status, "subaccount");
        if (!s.empty()) return s;
    }
    if (jhas(status, "wallet")) {
        auto s = jstr(status, "wallet");
        if (!s.empty()) return s;
    }
    for (auto& b : accounts.value("bots", json::array())) {
        if (jstr(b, "id") == id) return jstr(b, "address");
    }
    return {};
}

static std::string norm_addr(std::string a) {
    for (char& c : a)
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return a;
}

// A stopped or paused hunter's last file is not money. One Hyperliquid
// address is one balance, so hunters that share it are counted once.
static void fold_live_funds(const json& bots, json& positions, double& eq_sum, double& upnl_sum) {
    std::map<std::string, int> winner;
    std::vector<char> count(bots.size(), 0);
    for (size_t i = 0; i < bots.size(); ++i) {
        const json& b = bots[i];
        if (!b.is_object()) continue;
        if (!jbool(b, "running") || jbool(b, "deactivated")) continue;
        std::string addr = norm_addr(jstr(b, "address"));
        if (addr.size() < 8) {
            count[i] = 1;
            continue;
        }
        auto it = winner.find(addr);
        if (it == winner.end() || jnum(b, "equity_usd") > jnum(bots[it->second], "equity_usd"))
            winner[addr] = static_cast<int>(i);
    }
    for (auto& kv : winner) count[static_cast<size_t>(kv.second)] = 1;
    eq_sum = 0;
    upnl_sum = 0;
    std::set<std::string> keep;
    for (size_t i = 0; i < bots.size(); ++i) {
        if (!count[i]) continue;
        eq_sum += jnum(bots[i], "equity_usd");
        upnl_sum += jnum(bots[i], "upnl_usd");
        keep.insert(jstr(bots[i], "id"));
    }
    json kept = json::array();
    for (auto& p : positions) {
        if (!p.is_object()) continue;
        if (!keep.count(jstr(p, "bot_id"))) continue;
        kept.push_back(p);
    }
    positions = std::move(kept);
}

static double sl_of(const json& sl, const std::string& id, double fallback) {
    if (sl.contains(id) && sl[id].is_object()) return jnum(sl[id], "sl_roe_pct", fallback);
    return fallback;
}

static json ctrl_file(const Brain& br, const std::string& id) {
    if (!valid_id(id)) return json::object();
    return read_json(join(br.conf, id + "_ctrl.json"));
}

static json merge_controller(const Brain& br, const std::string& id, const json& status, const json& sl) {
    json out = json::object();
    if (status.is_object()) {
        for (auto& k : {"take_profit", "trail_activation", "trail_delta", "rebuy_pct",
                        "executor_refresh_time",
                        "margin_pct", "sl_roe_pct", "dontGoCrazy", "afterLossNoRebuy", "copy_shorts",
                        "tp_roe_pct", "scale_roe_pct", "hold_h", "close_px"}) {
            if (status.contains(k)) out[k] = status[k];
        }
    }
    auto file = ctrl_file(br, id);
    if (file.is_object()) {
        for (auto it = file.begin(); it != file.end(); ++it) {
            if (it.key() == "updated" || it.key() == "source") continue;
            if (!it.value().is_null()) out[it.key()] = it.value();
        }
    }
    out["sl_roe_pct"] = sl_of(sl, id, jnum(out, "sl_roe_pct", 20));
    if (id == "hummingbot-dman-maker" && !out.contains("sendEvery2Win")) {
        bool on = true;
        auto skim = read_json(join(br.conf, "risk_skim.json"));
        if (skim.is_object() && skim.contains("hunters") && skim["hunters"].is_object()
            && skim["hunters"].contains(id) && skim["hunters"][id].is_object()) {
            std::string mode = jstr(skim["hunters"][id], "mode");
            if (mode == "keep" || mode == "off" || mode == "none") on = false;
        }
        out["sendEvery2Win"] = on;
    }
    if (id == "hummingbot-onlyup" || id == "hummingbot-surfn") {
        auto moon = read_json(join(br.conf, id == "hummingbot-onlyup" ? "onlyup_moon.json" : "surfn_moon.json"));
        if (moon.is_object()) {
            for (auto it = moon.begin(); it != moon.end(); ++it) {
                if (!it.value().is_object() || !jbool(it.value(), "on")) continue;
                double cpx = jnum(it.value(), "close_px", jnum(it.value(), "target_px"));
                if (cpx > 0) out["close_px"] = cpx;
            }
        }
    }
    return out;
}

static json slice_trades_json(const json& tj, int keep) {
    json arr = tj.value("trades", json::array());
    json last = json::array();
    int n = static_cast<int>(arr.size());
    for (int i = n - 1; i >= 0 && static_cast<int>(last.size()) < keep; --i) {
        if (!arr[i].is_object()) continue;
        std::string act = jstr(arr[i], "action");
        for (char& c : act) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        if (act != "CLOSE") continue;
        last.push_back(arr[i]);
    }
    return {
        {"trades", last},
        {"n_wins", jnum(tj, "n_wins")},
        {"n_losses", jnum(tj, "n_losses")},
        {"n_opens", jnum(tj, "n_opens")},
        {"n_closes", jnum(tj, "n_closes")},
        {"realized_pnl_usd", jnum(tj, "realized_pnl_usd")},
    };
}

static int copy_trader_num_in(const std::string& L) {
    auto p = L.find("TRADER #");
    if (p != std::string::npos)
        return std::atoi(L.c_str() + p + 8);
    p = L.find("T#");
    if (p != std::string::npos && p + 2 < L.size() && std::isdigit(static_cast<unsigned char>(L[p + 2])))
        return std::atoi(L.c_str() + p + 2);
    return 0;
}

static std::string copy_coin_in(const std::string& L) {
    auto pick = [&](const char* side) -> std::string {
        auto pos = L.find(side);
        if (pos == std::string::npos) return {};
        pos += std::strlen(side);
        while (pos < L.size() && std::isspace(static_cast<unsigned char>(L[pos]))) pos++;
        if (pos + 2 < L.size() && static_cast<unsigned char>(L[pos]) == 0xE2) {
            while (pos < L.size() && !std::isspace(static_cast<unsigned char>(L[pos]))) pos++;
            while (pos < L.size() && std::isspace(static_cast<unsigned char>(L[pos]))) pos++;
        }
        size_t e = pos;
        while (e < L.size() && !std::isspace(static_cast<unsigned char>(L[e])) && L[e] != ',') e++;
        if (e > pos) return L.substr(pos, e - pos);
        return {};
    };
    std::string c = pick("SHORT");
    if (c.empty()) c = pick("LONG");
    if (c.size() > 12) c = c.substr(0, 12);
    return c;
}

static json copy_log_trades(const Brain& br, int keep) {
    auto raw = slurp(join(br.conf, "../logs/copy_trades.log"), 96000);
    std::istringstream iss(raw);
    std::string line;
    int last_t = 0;
    std::map<std::string, int> by_coin;
    struct Ev { std::string L; int trader; };
    std::vector<Ev> evs;
    while (std::getline(iss, line)) {
        int tn = copy_trader_num_in(line);
        std::string coin = copy_coin_in(line);
        if (line.find("TRADER #") != std::string::npos && tn > 0) {
            last_t = tn;
            if (!coin.empty()) by_coin[coin] = tn;
            continue;
        }
        bool opened = line.find("WE COPY") != std::string::npos && line.find("OPENED") != std::string::npos;
        bool closed = line.find("WE CLOSE") != std::string::npos && line.find("CLOSED") != std::string::npos;
        if (!opened && !closed) continue;
        int t = tn;
        if (t <= 0 && !coin.empty() && by_coin.count(coin)) t = by_coin[coin];
        if (t <= 0) t = last_t;
        if (t > 0 && !coin.empty()) by_coin[coin] = t;
        evs.push_back({line, t});
    }
    json last = json::array();
    int wins = 0, losses = 0, opens = 0, closes = 0;
    int n = static_cast<int>(evs.size());
    int start = std::max(0, n - keep);
    for (int i = n - 1; i >= start; --i) {
        const std::string& L = evs[i].L;
        json row = json::object();
        {
            auto tp = L.find("20");
            while (tp != std::string::npos && tp + 19 <= L.size()) {
                if (L[tp + 4] == '-' && L[tp + 7] == '-' && L[tp + 10] == ' ') {
                    row["ts"] = L.substr(tp, 19);
                    break;
                }
                tp = L.find("20", tp + 1);
            }
        }
        bool is_close = L.find("CLOSED") != std::string::npos;
        row["action"] = is_close ? "CLOSE" : "OPEN";
        if (L.find("LONG") != std::string::npos) row["side"] = "LONG";
        else if (L.find("SHORT") != std::string::npos) row["side"] = "SHORT";
        std::string coin = copy_coin_in(L);
        if (!coin.empty()) row["coin"] = coin;
        if (evs[i].trader > 0) row["trader"] = evs[i].trader;
        auto p = L.find("PnL~");
        if (p != std::string::npos) {
            try {
                double v = std::stod(L.substr(p + 4));
                row["roe_pct"] = v;
                if (v > 0) wins++;
                else if (v < 0) losses++;
            } catch (...) {}
        }
        std::string why = evs[i].trader > 0 ? ("T#" + std::to_string(evs[i].trader) + " ") : "";
        why += is_close ? "leader close/flip" : "leader open";
        row["reason"] = why;
        last.push_back(row);
        if (is_close) closes++; else opens++;
    }
    return {
        {"trades", last},
        {"n_wins", wins},
        {"n_losses", losses},
        {"n_opens", opens},
        {"n_closes", closes},
        {"realized_pnl_usd", 0.0},
    };
}

static json last_trades_for(const Brain& br, const std::string& id) {
    const int KEEP = 2;
    if (id == "hummingbot-hl") {
        auto cached = read_json(join(br.conf, "hl_copy_pnl.json"));
        if (cached.is_object() && cached.contains("n_closes")) {
            json last = json::array();
            json arr = cached.value("last_closes", json::array());
            int n = static_cast<int>(arr.size());
            for (int i = n - 1; i >= 0 && static_cast<int>(last.size()) < KEEP; --i)
                if (arr[i].is_object()) last.push_back(arr[i]);
            return {
                {"trades", last},
                {"n_wins", jnum(cached, "n_wins")},
                {"n_losses", jnum(cached, "n_losses")},
                {"n_opens", jnum(cached, "n_opens")},
                {"n_closes", jnum(cached, "n_closes")},
                {"realized_pnl_usd", jnum(cached, "realized_pnl_usd")},
            };
        }
        return copy_log_trades(br, KEEP);
    }
    const char* file = nullptr;
    if (id == "hummingbot-bn-swing") file = "bn_swing_trades.json";
    else if (id == "hummingbot-helliot") file = "helliot_trades.json";
    else if (id == "hummingbot-surfn") file = "surfn_trades.json";
    else if (id == "hummingbot-shortn") file = "shortn_trades.json";
    else if (id == "hummingbot-dman") file = "dman_trades.json";
    else if (id == "hummingbot-onlyup") file = "onlyup_trades.json";
    else if (id == "hummingbot-dman-maker") file = "dman_maker_trades.json";
    if (!file) return {{"trades", json::array()}, {"n_wins", 0}, {"n_losses", 0},
                       {"n_opens", 0}, {"n_closes", 0}, {"realized_pnl_usd", 0.0}};
    return slice_trades_json(read_json(join(br.conf, file)), KEEP);
}

static const char* hunter_log_name(const std::string& id) {
    if (id == "hummingbot-hl") return "logs_hl_copy_trader.log";
    if (id == "hummingbot-bn-swing") return "logs_pmm_simple_trader.log";
    if (id == "hummingbot-helliot") return "logs_helliot_trader.log";
    if (id == "hummingbot-surfn") return "logs_surfn_trader.log";
    if (id == "hummingbot-shortn") return "logs_shortn_trader.log";
    if (id == "hummingbot-dman") return "logs_dman_trader.log";
    if (id == "hummingbot-onlyup") return "logs_onlyup_trader.log";
    if (id == "hummingbot-dman-maker") return "logs_dman_maker_v2_trader.log";
    return nullptr;
}

// Hyperliquid drops a perp order under $10. The connector logs that and
// never creates the order, so the matrix stays flat. Surface the reason.
// The desk polls /api/home every few seconds from several clients. Hold the
// parsed rows so a cold read of the hunter log does not run on every poll.
static json refuse_log_lines(const Brain& br, const std::string& id) {
    json out = json::array();
    const char* name = hunter_log_name(id);
    if (!name) return out;
    struct Slot { std::time_t ts = 0; json rows = json::array(); };
    static std::mutex cache_mu;
    static std::map<std::string, Slot> cache;
    {
        std::lock_guard<std::mutex> lk(cache_mu);
        auto it = cache.find(id);
        if (it != cache.end() && std::time(nullptr) - it->second.ts < 12)
            return it->second.rows;
    }
    std::string raw = slurp(join(br.conf, std::string("../logs/") + name), 8 * 1024 * 1024);
    if (id == "hummingbot-hl") {
        auto older = slurp(join(br.conf, "../logs/logs_hl_copy_trader.log.2026-10-09"), 3 * 1024 * 1024);
        if (!older.empty()) raw = older + "\n" + raw;
    }
    if (raw.empty()) {
        std::lock_guard<std::mutex> lk(cache_mu);
        cache[id] = Slot{std::time(nullptr), out};
        return out;
    }
    std::map<std::string, std::string> side_of;
    std::vector<std::string> rows;
    std::istringstream iss(raw);
    std::string line;
    while (std::getline(iss, line)) {
        auto op = line.find("Opening ");
        auto mir = line.find(" mirror: ");
        if (op != std::string::npos && mir != std::string::npos && mir > op) {
            std::string mid = line.substr(op + 8, mir - (op + 8));
            std::string side;
            if (mid.find("LONG") != std::string::npos) side = "LONG";
            else if (mid.find("SHORT") != std::string::npos) side = "SHORT";
            std::string pair = line.substr(mir + std::strlen(" mirror: "));
            auto sp = pair.find_first_of(" \t");
            if (sp != std::string::npos) pair = pair.substr(0, sp);
            if (!side.empty() && !pair.empty()) side_of[pair] = side;
        }
        bool under_notional = line.find("lower than minimum notional") != std::string::npos;
        bool under_size = line.find("lower than minimum order size") != std::string::npos;
        if (!under_notional && !under_size) continue;
        auto num_at = [&](size_t np) {
            size_t e = np;
            while (e < line.size() &&
                   (std::isdigit(static_cast<unsigned char>(line[e])) || line[e] == '.'))
                e++;
            if (e == np) return std::string("?");
            return line.substr(np, e - np);
        };
        std::string detail = "under exchange minimum";
        if (under_notional) {
            std::string ntl = "?";
            auto np = line.find("Order notional ");
            if (np != std::string::npos) {
                try {
                    double v = std::stod(num_at(np + std::strlen("Order notional ")));
                    char b[32];
                    std::snprintf(b, sizeof(b), "$%.2f", v);
                    ntl = b;
                } catch (...) {}
            }
            detail = ntl + " under $10 minimum";
        } else {
            std::string amt = "?";
            std::string mn = "?";
            auto ap = line.find("Order amount ");
            if (ap != std::string::npos) amt = num_at(ap + std::strlen("Order amount "));
            auto mp = line.find("minimum order size ");
            if (mp != std::string::npos) mn = num_at(mp + std::strlen("minimum order size "));
            detail = "size " + amt + " under minimum " + mn;
        }
        std::string pair;
        auto pp = line.find("for the pair ");
        if (pp != std::string::npos) {
            pp += std::strlen("for the pair ");
            size_t e = pp;
            while (e < line.size() && line[e] != '.' && line[e] != '"' &&
                   !std::isspace(static_cast<unsigned char>(line[e])))
                e++;
            pair = line.substr(pp, e - pp);
        }
        std::string coin = pair;
        auto usd = coin.rfind("-USD");
        if (usd != std::string::npos) coin = coin.substr(0, usd);
        std::string side;
        auto sit = side_of.find(pair);
        if (sit != side_of.end()) side = sit->second;
        std::string ts = "        ";
        if (line.size() >= 19 && line[4] == '-' && line[13] == ':') ts = line.substr(11, 8);
        char row[220];
        std::snprintf(row, sizeof(row), "%s UTC  REFUSED  %s%s%s  %s",
                      ts.c_str(), side.c_str(), side.empty() ? "" : " ",
                      coin.c_str(), detail.c_str());
        if (!rows.empty() && rows.back() == row) continue;
        rows.push_back(row);
    }
    int n = static_cast<int>(rows.size());
    int from = n > 30 ? n - 30 : 0;
    for (int i = from; i < n; ++i) out.push_back(rows[static_cast<size_t>(i)]);
    {
        std::lock_guard<std::mutex> lk(cache_mu);
        cache[id] = Slot{std::time(nullptr), out};
    }
    return out;
}

static json basename_json(const Brain& br, const std::string& path) {
    auto name = basename_only(path);
    if (name.empty()) return json::object();
    return read_json(join(br.conf, name));
}

static std::string slurp_cmd(const char* cmd, size_t maxn = 8000) {
    FILE* fp = popen(cmd, "r");
    if (!fp) return {};
    std::string out;
    char buf[512];
    while (fgets(buf, sizeof(buf), fp)) {
        out += buf;
        if (out.size() > maxn) break;
    }
    pclose(fp);
    while (!out.empty() && (out.back() == '\n' || out.back() == '\r' || out.back() == ' '))
        out.pop_back();
    return out;
}

static json github_hb_latest() {
    static json cached;
    static std::time_t ts = 0;
    std::time_t now = std::time(nullptr);
    if (ts && now - ts < 300 && cached.is_object() && cached.value("ok", false))
        return cached;
    auto body = slurp_cmd(
        "curl -sm8 -H 'Accept: application/vnd.github+json' "
        "-H 'User-Agent: freiquell-spring' "
        "https://api.github.com/repos/hummingbot/hummingbot/releases/latest 2>/dev/null");
    json j = json::object();
    try { j = json::parse(body.empty() ? "{}" : body); } catch (...) { j = json::object(); }
    std::string tag = jstr(j, "tag_name");
    cached = {
        {"ok", !tag.empty()},
        {"tag", tag},
        {"name", jstr(j, "name", tag)},
        {"published_at", jstr(j, "published_at")},
        {"html_url", jstr(j, "html_url")},
    };
    ts = now;
    return cached;
}

static json matrix_stamp(const Brain& br) {
    std::string root = br.conf;
    auto slash = root.find_last_of('/');
    if (slash != std::string::npos) root = root.substr(0, slash);
    json o = {{"root", root}};
    auto add_bin = [&](const char* rel, const char* key) {
        std::string p = join(root, rel);
        struct stat st{};
        if (::stat(p.c_str(), &st) == 0) {
            o[std::string(key) + "_mtime"] = static_cast<long long>(st.st_mtime);
            o[std::string(key) + "_size"] = static_cast<long long>(st.st_size);
        }
    };
    add_bin("freiquell_server_c++/spring", "spring");
    add_bin("freiquell_c++/rimbaud-desk", "desk");
    add_bin("rimbaud_meta_c++/rimbaud-meta-desk", "meta_desk");
    add_bin("scripts/dman_maker_v2_trader.py", "dman_maker");
    add_bin("scripts/hl_copy_trader.py", "copy");
    add_bin("condor/gauge.py", "condor");
    {
        std::ifstream in(join(br.conf, "archy.json"));
        if (in) {
            json a;
            try { in >> a; } catch (...) { a = json::object(); }
            o["archy"] = a.value("version", "");
            o["archy_updated"] = a.value("updated", "");
        }
        std::ifstream dep(join(br.conf, "archy_deployed.json"));
        if (dep) {
            json a;
            try { dep >> a; } catch (...) { a = json::object(); }
            o["archy_deployed"] = a.value("version", a.value("deployed", ""));
        }
    }
    return o;
}

static json api_versions(const Brain& br) {
    std::string root = br.conf;
    auto slash = root.find_last_of('/');
    if (slash != std::string::npos) root = root.substr(0, slash);
    std::string tree;
    {
        std::ifstream in(join(root, "hummingbot/VERSION"));
        if (in) std::getline(in, tree);
        while (!tree.empty() && (tree.back() == '\n' || tree.back() == '\r' || tree.back() == ' '))
            tree.pop_back();
    }
    // A paused hunter refuses docker exec. Read VERSION from the first container that answers.
    std::string running = slurp_cmd(
        "for c in hummingbot-dman-maker hummingbot-hl hummingbot-onlyup "
        "hummingbot-bn-swing hummingbot-helliot hummingbot-surfn "
        "hummingbot-shortn hummingbot-dman; do "
        "v=$(docker exec \"$c\" cat /home/hummingbot/hummingbot/VERSION 2>/dev/null) || continue; "
        "[ -n \"$v\" ] && printf '%s' \"$v\" && break; "
        "done");
    auto gh = github_hb_latest();
    std::string ghtag = jstr(gh, "tag");
    if (!ghtag.empty() && (ghtag[0] == 'v' || ghtag[0] == 'V')) ghtag = ghtag.substr(1);
    std::string status = "unknown";
    if (!running.empty() && !ghtag.empty())
        status = (running == ghtag) ? "up to date" : "behind";
    json notes = json::array({
        "hunters run hummingbot/hummingbot:latest in Docker — GitHub compare is that image VERSION, not the host git tree",
        "custom scripts in ./scripts (pmm_simple_trader, helliot, surfn, shortn, dman, onlyup, dman_maker_v2, hl_copy) are local — docker pull does not replace them",
        "./controllers is bind-mounted over the image controllers — official pmm_simple / dman_maker_v2 in that folder will not pick up GitHub controller diffs until you merge",
        "overlay bind-mounts persist across pull: hyperliquid_perpetual_derivative.py, candles_base.py, hyperliquid_perpetual_api_order_book_data_source.py, order_book_tracker.py, strategy_v2_base.py — re-test after an image bump; do not copy stock files over them",
        "upgrade path: docker compose pull hummingbot/hummingbot:latest then recreate hunters. Overlays + scripts stay. Positions live on Hyperliquid — do not flatten to upgrade",
    });
    return {
        {"ok", true},
        {"running", running},
        {"tree", tree},
        {"github", jstr(gh, "tag")},
        {"github_name", jstr(gh, "name")},
        {"published_at", jstr(gh, "published_at")},
        {"html_url", jstr(gh, "html_url")},
        {"image", "hummingbot/hummingbot:latest"},
        {"status", status},
        {"up_to_date", status == "up to date"},
        {"github_ok", gh.value("ok", false)},
        {"notes", notes},
        {"stamp", matrix_stamp(br)},
        {"ts", utc_now()},
    };
}

static json api_desk_health() {
    json host = json::object();
    double load1 = 0;
    {
        double l[3] = {0, 0, 0};
        if (getloadavg(l, 1) > 0) load1 = l[0];
        host["load1"] = std::round(load1 * 100.0) / 100.0;
        long ncpu = sysconf(_SC_NPROCESSORS_ONLN);
        if (ncpu < 1) ncpu = 1;
        static unsigned long long prev_tot = 0, prev_idle = 0;
        FILE* f = std::fopen("/proc/stat", "r");
        double cpu = 0;
        if (f) {
            char line[256];
            if (std::fgets(line, sizeof(line), f)) {
                unsigned long long u = 0, n = 0, s = 0, idl = 0, iow = 0, irq = 0, sirq = 0;
                if (std::sscanf(line, "cpu %llu %llu %llu %llu %llu %llu %llu",
                                &u, &n, &s, &idl, &iow, &irq, &sirq) >= 4) {
                    unsigned long long tot = u + n + s + idl + iow + irq + sirq;
                    unsigned long long idle = idl + iow;
                    if (prev_tot && tot > prev_tot) {
                        double dt = static_cast<double>(tot - prev_tot);
                        double di = static_cast<double>(idle - prev_idle);
                        cpu = 100.0 * (1.0 - di / dt);
                    } else {
                        cpu = 100.0 * load1 / static_cast<double>(ncpu);
                    }
                    prev_tot = tot;
                    prev_idle = idle;
                }
            }
            std::fclose(f);
        } else {
            cpu = 100.0 * load1 / static_cast<double>(ncpu);
        }
        if (cpu < 0) cpu = 0;
        if (cpu > 100) cpu = 100;
        host["cpu_pct"] = std::round(cpu * 10.0) / 10.0;
    }
    {
        FILE* f = std::fopen("/proc/meminfo", "r");
        double total_kb = 0, avail_kb = 0, free_kb = 0;
        if (f) {
            char key[64];
            unsigned long val = 0;
            char unit[16];
            while (std::fscanf(f, "%63s %lu %15s", key, &val, unit) == 3) {
                if (std::strcmp(key, "MemTotal:") == 0) total_kb = static_cast<double>(val);
                else if (std::strcmp(key, "MemAvailable:") == 0) avail_kb = static_cast<double>(val);
                else if (std::strcmp(key, "MemFree:") == 0) free_kb = static_cast<double>(val);
            }
            std::fclose(f);
        }
        if (avail_kb <= 0) avail_kb = free_kb;
        if (total_kb > 0) {
            double used_kb = total_kb - avail_kb;
            host["mem_pct"] = std::round(1000.0 * used_kb / total_kb) / 10.0;
            host["mem_used_gb"] = std::round(used_kb / 1024.0 / 1024.0 * 100.0) / 100.0;
            host["mem_total_gb"] = std::round(total_kb / 1024.0 / 1024.0 * 100.0) / 100.0;
        }
    }
    {
        struct statvfs st {};
        if (statvfs("/", &st) == 0 && st.f_blocks > 0) {
            double total = static_cast<double>(st.f_blocks) * static_cast<double>(st.f_frsize);
            double used = static_cast<double>(st.f_blocks - st.f_bfree) * static_cast<double>(st.f_frsize);
            host["disk_pct"] = std::round(1000.0 * used / total) / 10.0;
            host["disk_used_gb"] = std::round(used / 1e9 * 100.0) / 100.0;
            host["disk_total_gb"] = std::round(total / 1e9 * 100.0) / 100.0;
        }
    }
    json net = {{"ok", true}, {"ms", 0}, {"target", "1.1.1.1:443"}, {"error", nullptr}};
    return {{"host", host}, {"net", net}, {"ts", utc_now()}};
}

static json api_rate(const Brain& br) {
    auto j = read_json(join(br.conf, "hl_ip_budget.json"));
    double cap = jnum(j, "cap", 1200);
    double win = jnum(j, "window_s", 60);
    double now = static_cast<double>(std::time(nullptr));
    double used = 0;
    if (j.contains("events") && j["events"].is_array()) {
        for (auto& e : j["events"]) {
            if (!e.is_array() || e.empty() || !e[0].is_number()) continue;
            double ts = e[0].get<double>();
            if (now - ts <= win) {
                double w = 1;
                if (e.size() > 1 && e[1].is_number()) w = e[1].get<double>();
                used += w;
            }
        }
    }
    double left = std::max(0.0, cap - used);
    double pct = cap > 0 ? used / cap * 100.0 : 0;
    double last429 = jnum(j, "last_429", -1);
    double last429_s = last429 > 0 ? now - last429 : -1;
    return {
        {"used", used}, {"cap", cap}, {"left", left}, {"pct", pct},
        {"window_s", win},
        {"n429", last429_s >= 0 && last429_s < 3600 ? 1 : 0},
        {"last_429_s", last429_s},
        {"note", "spring snapshot from conf/hl_ip_budget.json"},
    };
}

static json api_risk(const Brain& br) {
    auto j = read_json(join(br.conf, "risk_skim.json"));
    json hunters = json::array();
    double parked = 0, ready = 0, locked = 0, sent = 0;
    if (j.contains("hunters") && j["hunters"].is_object()) {
        for (auto it = j["hunters"].begin(); it != j["hunters"].end(); ++it) {
            if (!it.value().is_object()) continue;
            auto h = it.value();
            double p = jnum(h, "parked_usd");
            double a = jnum(h, "available_usd");
            double l = jnum(h, "locked_usd");
            parked += p;
            ready += a;
            locked += l;
            sent += jnum(h, "sent_usd", jnum(h, "last_win_usd"));
            hunters.push_back({
                {"id", it.key()},
                {"name", it.key()},
                {"mode", jstr(h, "mode")},
                {"parked_usd", p},
                {"available_usd", a},
                {"locked_usd", l},
                {"sent_usd", jnum(h, "last_win_usd")},
                {"n_pos", 0},
                {"armed", jbool(h, "armed")},
                {"connected", !jstr(h, "address").empty()},
            });
        }
    }
    return {
        {"address", ""},
        {"parked_usd", parked},
        {"available_usd", ready},
        {"locked_usd", locked},
        {"sent_usd", sent},
        {"can_send", false},
        {"connected", hunters.size()},
        {"hunters", hunters},
    };
}

static std::vector<std::string> copy_trader_addrs(const Brain& br) {
    std::ifstream in(join(br.conf, "scripts/hl_copy_trader.yml"));
    std::vector<std::string> out;
    if (!in) return out;
    std::string line;
    bool in_list = false;
    while (std::getline(in, line)) {
        std::string t = line;
        while (!t.empty() && (t[0] == ' ' || t[0] == '\t')) t.erase(t.begin());
        if (t.rfind("trader_addresses:", 0) == 0) { in_list = true; continue; }
        if (!in_list) continue;
        if (t.empty() || t[0] == '#') continue;
        if (t[0] != '-') {
            if (t.find(':') != std::string::npos) break;
            continue;
        }
        auto p = t.find("0x");
        if (p == std::string::npos) p = t.find("0X");
        if (p == std::string::npos) continue;
        if (p + 42 <= t.size()) out.push_back(t.substr(p, 42));
    }
    return out;
}

static json copy_follow_list(const Brain& br, const json& st) {
    auto addrs = copy_trader_addrs(br);
    auto rt = read_json(join(br.conf, "hl_copy_runtime.json"));
    json unf = json::array();
    if (rt.is_object() && rt.contains("unfollowed_traders") && rt["unfollowed_traders"].is_array())
        unf = rt["unfollowed_traders"];
    else if (st.contains("unfollowed_traders") && st["unfollowed_traders"].is_array())
        unf = st["unfollowed_traders"];
    std::set<int> muted;
    for (auto& x : unf) {
        if (x.is_number()) muted.insert(static_cast<int>(x.get<double>()));
        else if (x.is_string()) {
            try { muted.insert(std::stoi(x.get<std::string>())); } catch (...) {}
        }
    }
    json out = json::array();
    int n = static_cast<int>(addrs.size());
    if (n <= 0) n = static_cast<int>(jnum(st, "n_traders_configured", jnum(st, "n_traders", 0)));
    for (int i = 1; i <= n; ++i) {
        std::string addr = (i - 1 < (int)addrs.size()) ? addrs[i - 1] : "";
        std::string sh = addr;
        if (addr.size() > 16) sh = addr.substr(0, 10) + "…" + addr.substr(addr.size() - 6);
        json coins = json::array();
        int npos = 0;
        for (auto& m : st.value("mirrors", json::array())) {
            if (!m.is_object()) continue;
            if (static_cast<int>(jnum(m, "trader")) != i) continue;
            npos++;
            std::string c = jstr(m, "coin");
            if (!c.empty()) coins.push_back(c);
        }
        bool is_muted = muted.count(i) > 0;
        int slots = 3;
        json raw_slots = json::object();
        if (rt.is_object() && rt.contains("trader_follow_slots") && rt["trader_follow_slots"].is_object())
            raw_slots = rt["trader_follow_slots"];
        std::string sk = std::to_string(i);
        if (raw_slots.contains(sk) && raw_slots[sk].is_number())
            slots = static_cast<int>(raw_slots[sk].get<double>());
        if (slots < 1) slots = 1;
        if (slots > 3) slots = 3;
        out.push_back({
            {"num", i},
            {"address", addr},
            {"short", sh},
            {"muted", is_muted},
            {"followed", !is_muted},
            {"unfollowed", is_muted},
            {"n_positions", npos},
            {"n_pos", npos},
            {"follow_slots", slots},
            {"coins", coins},
        });
    }
    return out;
}

static json api_hl_trader_follow(const Brain& br, const json& data) {
    auto addrs = copy_trader_addrs(br);
    int n = static_cast<int>(addrs.size());
    if (n <= 0) n = 3;
    int trader = static_cast<int>(jnum(data, "trader"));
    if (trader < 1 || trader > n)
        return {{"ok", false}, {"error", "trader out of range"}, {"closes", false}};
    bool want_follow = true;
    if (data.contains("mute")) want_follow = !jbool(data, "mute");
    else if (data.contains("follow")) want_follow = jbool(data, "follow");
    else if (data.contains("unfollow")) want_follow = !jbool(data, "unfollow");
    auto rt = read_json(join(br.conf, "hl_copy_runtime.json"));
    if (!rt.is_object()) rt = json::object();
    std::set<int> current;
    if (rt.contains("unfollowed_traders") && rt["unfollowed_traders"].is_array()) {
        for (auto& x : rt["unfollowed_traders"])
            if (x.is_number()) current.insert(static_cast<int>(x.get<double>()));
    }
    if (want_follow) current.erase(trader);
    else {
        if ((int)current.size() >= n - 1 && !current.count(trader))
            return {{"ok", false}, {"error", "Keep at least one trader unmuted"},
                    {"closes", false}, {"positions_untouched", true}};
        current.insert(trader);
    }
    json arr = json::array();
    for (int i : current) arr.push_back(i);
    json updates = {
        {"unfollowed_traders", arr},
        {"ts", utc_now()},
        {"source", "spring"},
    };
    if (!sql_apply(br, {{"op", "hl_runtime"}, {"bot_id", "hummingbot-hl"}, {"updates", updates}})) {
        rt["unfollowed_traders"] = arr;
        rt["ts"] = utc_now();
        rt["source"] = "spring";
        if (!write_json(join(br.conf, "hl_copy_runtime.json"), rt))
            return {{"ok", false}, {"error", "write runtime failed"}, {"closes", false},
                    {"positions_untouched", true}};
    }
    std::string msg = want_follow
        ? ("T#" + std::to_string(trader) + " unmuted — copy resumes")
        : ("T#" + std::to_string(trader) + " muted — Copy flattens that trader's clips");
    return {
        {"ok", true}, {"trader", trader}, {"followed", want_follow},
        {"unfollowed_traders", arr}, {"closes", false}, {"positions_untouched", true},
        {"message", msg},
    };
}

static json api_hl_trader_slots(const Brain& br, const json& data) {
    auto addrs = copy_trader_addrs(br);
    int n = static_cast<int>(addrs.size());
    if (n <= 0) n = 3;
    int trader = static_cast<int>(jnum(data, "trader"));
    int slots = static_cast<int>(jnum(data, "slots", jnum(data, "max_positions")));
    if (trader < 1 || trader > n)
        return {{"ok", false}, {"error", "trader out of range"}};
    if (slots < 1 || slots > 3)
        return {{"ok", false}, {"error", "slots must be 1, 2 or 3"}};
    auto rt = read_json(join(br.conf, "hl_copy_runtime.json"));
    if (!rt.is_object()) rt = json::object();
    json cur = json::object();
    if (rt.contains("trader_follow_slots") && rt["trader_follow_slots"].is_object())
        cur = rt["trader_follow_slots"];
    cur[std::to_string(trader)] = slots;
    json updates = {
        {"trader_follow_slots", cur},
        {"ts", utc_now()},
        {"source", "spring"},
    };
    if (!sql_apply(br, {{"op", "hl_runtime"}, {"bot_id", "hummingbot-hl"}, {"updates", updates}})) {
        rt["trader_follow_slots"] = cur;
        rt["ts"] = utc_now();
        rt["source"] = "spring";
        if (!write_json(join(br.conf, "hl_copy_runtime.json"), rt))
            return {{"ok", false}, {"error", "write runtime failed"}};
    }
    return {
        {"ok", true}, {"trader", trader}, {"slots", slots},
        {"trader_follow_slots", cur},
        {"message", "T#" + std::to_string(trader) + " follows " + std::to_string(slots)
            + " trade" + (slots == 1 ? "" : "s") + " (1/" + std::to_string(slots) + " of that pot)"},
    };
}

static json api_trader_audits(const Brain& br) {
    auto latest = read_json(join(br.conf, "../logs/trader_audits/latest.json"));
    json traders_in = json::object();
    if (latest.is_object() && latest.contains("traders") && latest["traders"].is_object())
        traders_in = latest["traders"];
    auto addrs = copy_trader_addrs(br);
    json traders = json::array();
    int n = static_cast<int>(addrs.size());
    if (n <= 0) {
        for (auto it = traders_in.begin(); it != traders_in.end(); ++it) {
            try { n = std::max(n, std::stoi(it.key())); } catch (...) {}
        }
    }
    for (int i = 1; i <= std::max(n, 1); ++i) {
        std::string key = std::to_string(i);
        json row = traders_in.contains(key) && traders_in[key].is_object()
            ? traders_in[key] : json::object();
        std::string addr = (i - 1 < (int)addrs.size()) ? addrs[i - 1] : jstr(row, "address");
        std::string old = jstr(row, "address");
        bool stale = !old.empty() && !addr.empty()
            && old.size() >= 8 && addr.size() >= 8
            && old.substr(0, 10) != addr.substr(0, 10)
            && old != addr;
        json pos = json::array();
        if (row.contains("positions") && row["positions"].is_array()) {
            int k = 0;
            for (auto& p : row["positions"]) {
                if (!p.is_object() || k >= 8) continue;
                pos.push_back({
                    {"coin", jstr(p, "coin")},
                    {"side", jstr(p, "side")},
                    {"size", jnum(p, "size")},
                    {"entry_px", jnum(p, "entry_px", jnum(p, "entry"))},
                    {"u_pnl", jnum(p, "u_pnl", jnum(p, "u_pnl_usd"))},
                });
                k++;
            }
        }
        json lb = row.value("leaderboard", json::object());
        std::string sh = jstr(row, "short");
        if (sh.empty() && addr.size() > 16)
            sh = addr.substr(0, 10) + "…" + addr.substr(addr.size() - 6);
        traders.push_back({
            {"trader", i},
            {"address", addr},
            {"short", sh},
            {"audited_at", jstr(row, "audited_at_local", jstr(row, "audited_at"))},
            {"equity", jnum(row, "equity")},
            {"free_margin", jnum(row, "free_margin")},
            {"margin_used", jnum(row, "margin_used")},
            {"u_pnl_total", jnum(row, "u_pnl_total")},
            {"n_positions", static_cast<int>(jnum(row, "n_positions"))},
            {"capacity_flag", stale ? "WAIT" : jstr(row, "capacity_flag")},
            {"sleep_flag", jstr(row, "sleep_flag")},
            {"sleep_hours", jnum(row, "sleep_hours")},
            {"can_open", jbool(row, "can_open")},
            {"ok", jbool(row, "ok", !stale)},
            {"stale", stale},
            {"last_open_at", jstr(row, "last_open_at")},
            {"last_open_coin", jstr(row, "last_open_coin")},
            {"last_open_dir", jstr(row, "last_open_dir")},
            {"day_pnl", jnum(lb, "day_pnl")},
            {"week_pnl", jnum(lb, "week_pnl")},
            {"rank", jnum(lb, "rank")},
            {"error", stale ? "leader swapped — waiting for next audit" : jstr(row, "error")},
            {"positions", pos},
        });
        if (addrs.empty() && i >= (int)traders_in.size()) break;
    }
    json history = json::array();
    std::string hist_dir = join(br.conf, "../logs/trader_audits/history");
    DIR* dp = opendir(hist_dir.c_str());
    if (dp) {
        std::vector<std::string> names;
        while (auto* ent = readdir(dp)) {
            std::string n = ent->d_name;
            if (n.size() > 5 && n.rfind(".json") == n.size() - 5) names.push_back(n);
        }
        closedir(dp);
        std::sort(names.rbegin(), names.rend());
        for (size_t i = 0; i < names.size() && i < 20; ++i) {
            auto row = read_json(join(hist_dir, names[i]));
            if (!row.is_object()) continue;
            char line[160];
            snprintf(line, sizeof(line), "T#%d  %s  %s/%s  eq $%.0f  free $%.0f  pos %d  %s",
                     static_cast<int>(jnum(row, "trader")),
                     jstr(row, "audited_at").c_str(),
                     jstr(row, "capacity_flag").c_str(),
                     jstr(row, "sleep_flag").c_str(),
                     jnum(row, "equity"),
                     jnum(row, "free_margin"),
                     static_cast<int>(jnum(row, "n_positions")),
                     jbool(row, "can_open") ? "can_open" : "no_open");
            history.push_back(std::string(line));
        }
    }
    std::string log_mtime;
    auto logp = join(br.conf, "../logs/trader_audits/audit.log");
    struct stat st{};
    if (stat(logp.c_str(), &st) == 0) {
        char buf[32];
        std::time_t t = st.st_mtime;
        std::strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M UTC", std::gmtime(&t));
        log_mtime = buf;
    }
    json sched = latest.value("schedule", json::object());
    if (!sched.is_object() || sched.empty())
        sched = {{"1", "01:00"}, {"2", "06:00"}, {"3", "11:00"}};
    return {
        {"ok", true},
        {"worker_running", docker_running("hummingbot-audit")},
        {"updated_at", jstr(latest, "updated_at")},
        {"timezone", jstr(latest, "timezone")},
        {"log_mtime", log_mtime},
        {"schedule", sched},
        {"closes", false},
        {"positions_untouched", true},
        {"traders", traders},
        {"history", history},
    };
}

static json api_trader_audits_run(const Brain& br, const json& data) {
    json trader = data.contains("trader") ? data["trader"] : json("all");
    json force = {{"trader", trader}, {"requested_at", utc_now()}};
    if (!write_json(join(br.conf, "../logs/trader_audits/force.json"), force))
        return {{"ok", false}, {"error", "cannot write force.json"}, {"closes", false}};
    return {{"ok", true}, {"message", "audit queued — worker will run, no HL trades"},
            {"closes", false}, {"positions_untouched", true}};
}

static json g_sql_status = json::object();
static json g_sql_bias = json::object();
static std::chrono::steady_clock::time_point g_sql_status_t{};

static json sql_status_map(const Brain& br) {
    auto now = std::chrono::steady_clock::now();
    if (g_sql_status.is_object() && !g_sql_status.empty()
        && now - g_sql_status_t < std::chrono::milliseconds(200))
        return g_sql_status;
    json st;
    json bias;
    if (matrix_sql_snapshot(br.conf, st, bias)) {
        if (st.is_object()) g_sql_status = st;
        if (bias.is_object()) g_sql_bias = bias;
        g_sql_status_t = now;
        return g_sql_status;
    }
    if (g_sql_status.empty()) {
        sql_apply_py(br, {{"op", "status_dump"}});
        auto dumped = read_json(join(br.conf, ".spring_sql_status.json"));
        if (dumped.is_object()) g_sql_status = dumped;
        auto bd = read_json(join(br.conf, ".spring_sql_bias.json"));
        if (bd.is_object()) g_sql_bias = bd;
        g_sql_status_t = now;
    }
    return g_sql_status;
}

// A controller with no status balance still spends the pool wallet wired into it.
// wallet_in is the subaccount name. The address stays in wire_wallets.json.
struct HlCash {
    double equity = 0;
    double free = 0;
    bool ok = false;
    std::time_t ts = 0;
};
static std::mutex g_hl_cash_mu;
static std::map<std::string, HlCash> g_hl_cash;

static bool hl_addr_ok(const std::string& a) {
    if (a.size() != 42) return false;
    if (a[0] != '0' || (a[1] != 'x' && a[1] != 'X')) return false;
    for (size_t i = 2; i < a.size(); ++i) {
        if (!std::isxdigit(static_cast<unsigned char>(a[i]))) return false;
    }
    return true;
}

static std::string fold_label(const std::string& s) {
    std::string o;
    bool gap = false;
    for (char c : s) {
        unsigned char u = static_cast<unsigned char>(c);
        if (std::isspace(u)) {
            gap = !o.empty();
            continue;
        }
        if (gap) {
            o.push_back(' ');
            gap = false;
        }
        o.push_back(static_cast<char>(std::tolower(u)));
    }
    return o;
}

static void pool_by_name(const Brain& br, std::map<std::string, std::string>& out) {
    out.clear();
    auto wallets = read_json(join(br.conf, "wire_wallets.json"));
    json rows = json::array();
    if (wallets.is_array()) rows = wallets;
    else if (wallets.contains("wallets") && wallets["wallets"].is_array())
        rows = wallets["wallets"];
    for (auto& w : rows) {
        if (!w.is_object()) continue;
        std::string name = fold_label(jstr(w, "name"));
        std::string addr = jstr(w, "address");
        if (name.empty() || !hl_addr_ok(addr)) continue;
        out[name] = addr;
    }
}

static void wired_names(const Brain& br, std::map<std::string, std::string>& out) {
    out.clear();
    auto rt = read_json(join(br.conf, "wire_runtime.json"));
    if (!rt.is_object() || !rt.contains("bots") || !rt["bots"].is_object()) return;
    for (auto it = rt["bots"].begin(); it != rt["bots"].end(); ++it) {
        if (!it.value().is_object()) continue;
        std::string name = jstr(it.value(), "wallet_in");
        if (!name.empty()) out[it.key()] = name;
    }
}

// clearinghouseState is weight 2. Cache 25s so the desk poll does not refill the bucket.
static bool hl_cash_of(const std::string& addr, double& equity, double& free) {
    if (!hl_addr_ok(addr)) return false;
    std::string key = norm_addr(addr);
    std::time_t now = std::time(nullptr);
    {
        std::lock_guard<std::mutex> lk(g_hl_cash_mu);
        auto it = g_hl_cash.find(key);
        if (it != g_hl_cash.end() && now - it->second.ts < (it->second.ok ? 25 : 15)) {
            if (!it->second.ok) return false;
            equity = it->second.equity;
            free = it->second.free;
            return true;
        }
    }
    std::string cmd =
        "curl -sm5 -sS -H 'Content-Type: application/json' "
        "-d '{\"type\":\"clearinghouseState\",\"user\":\"" + addr + "\"}' "
        "https://api.hyperliquid.xyz/info 2>/dev/null";
    std::string body = slurp_cmd(cmd.c_str(), 262144);
    json j = json::object();
    try { j = json::parse(body.empty() ? "{}" : body); } catch (...) { j = json::object(); }
    double eq = -1, fr = -1;
    if (j.is_object()) {
        if (j.contains("crossMarginSummary") && j["crossMarginSummary"].is_object())
            eq = jnum(j["crossMarginSummary"], "accountValue", -1);
        if (eq < 0 && j.contains("marginSummary") && j["marginSummary"].is_object())
            eq = jnum(j["marginSummary"], "accountValue", -1);
        fr = jnum(j, "withdrawable", -1);
    }
    bool ok = eq >= 0;
    if (fr < 0) fr = ok ? eq : 0;
    {
        std::lock_guard<std::mutex> lk(g_hl_cash_mu);
        g_hl_cash[key] = HlCash{ok ? eq : 0, ok ? fr : 0, ok, now};
    }
    if (!ok) return false;
    equity = eq;
    free = fr;
    return true;
}

static json api_home(const Brain& br) {
    json bots = json::array();
    json positions = json::array();
    json sqlst = sql_status_map(br);
    double eq_sum = 0, upnl_sum = 0;
    int n_ok = 0, n_live = 0;
    auto sl = read_json(join(br.conf, "bot_sl.json"));
    auto accounts = read_json(join(br.conf, "bot_accounts.json"));
    std::map<std::string, std::string> pool;
    std::map<std::string, std::string> wired;
    pool_by_name(br, pool);
    wired_names(br, wired);

    for (auto& h : registry(br)) {
        if (!h.is_object()) continue;
        if (jbool(h, "on_home", true) == false) continue;
        std::string id = jstr(h, "id");
        if (!valid_id(id)) continue;
        json st = basename_json(br, jstr(h, "status_file"));
        if (sqlst.contains(id) && sqlst[id].is_object()) {
            std::string sql_ts = jstr(sqlst[id], "ts");
            std::string file_ts = jstr(st, "ts");
            if (!st.is_object() || st.empty() || sql_ts >= file_ts)
                st = sqlst[id];
        }
        bool running = docker_running(id);
        bool deactivated = docker_paused(id);
        bool ready = jbool(st, "ready", true);
        bool ok = running && !deactivated && ready && jbool(st, "wallet_ok", true);
        if (jstr(h, "kind") == "copy" && st.contains("connector_ready"))
            ok = running && !deactivated && jbool(st, "connector_ready", false);

        double equity = jhas(st, "equity_usd") ? jnum(st, "equity_usd") : jnum(st, "equity");
        double free = jhas(st, "free_usd") ? jnum(st, "free_usd") : jnum(st, "free");
        bool have_stated_upnl = jhas(st, "open_upnl_usd") || jhas(st, "total_upnl")
            || (st.contains("risk") && st["risk"].is_object() && jhas(st["risk"], "total_upnl"));
        double stated_upnl = jnum(st, "open_upnl_usd", jnum(st, "total_upnl"));
        if (st.contains("risk") && st["risk"].is_object())
            stated_upnl = jnum(st["risk"], "total_upnl", stated_upnl);

        std::string addr = wallet_of(accounts, id, st);
        int npos = 0;
        double summed_upnl = 0;
        bool copy = (id == "hummingbot-hl" || jstr(h, "kind") == "copy");
        bool live = running && !deactivated;

        if (live && copy) {
            for (auto& m : st.value("mirrors", json::array())) {
                if (!m.is_object()) continue;
                double sz = std::fabs(jnum(m, "size", jnum(m, "amount")));
                if (sz < 1e-12) continue;
                npos++;
                std::string side = jstr(m, "side");
                double entry = jnum(m, "entry_px", jnum(m, "entry_price"));
                double mark = jnum(m, "mark_px");
                int lev = static_cast<int>(jnum(m, "leverage"));
                double pnl = jnum(m, "unrealized_pnl", jnum(m, "pnl_usd"));
                double roe = jnum(m, "roe_pct");
                if (mark <= 0 && entry > 0 && std::fabs(pnl) > 1e-12 && sz > 0) {
                    if (side == "SHORT") mark = entry - pnl / sz;
                    else mark = entry + pnl / sz;
                }
                if (std::fabs(roe) < 1e-9 && entry > 0 && mark > 0 && lev > 0) {
                    if (side == "SHORT") roe = (entry - mark) / entry * lev * 100.0;
                    else roe = (mark - entry) / entry * lev * 100.0;
                }
                if (std::fabs(pnl) < 1e-12 && entry > 0 && mark > 0 && sz > 0) {
                    pnl = (side == "SHORT") ? sz * (entry - mark) : sz * (mark - entry);
                }
                summed_upnl += pnl;
                positions.push_back({
                    {"bot_id", id}, {"bot", jstr(h, "name", id)}, {"href", jstr(h, "href")},
                    {"coin", jstr(m, "coin")}, {"side", side},
                    {"size", sz}, {"entry_px", entry},
                    {"mark_px", mark}, {"leverage", lev},
                    {"pnl_usd", pnl}, {"roe_pct", roe}, {"source", "status-file"},
                });
            }
        } else if (live) {
            auto pos = st.value("positions", json::object());
            if (pos.is_object()) {
                for (auto it = pos.begin(); it != pos.end(); ++it) {
                    auto row = it.value();
                    if (!row.is_object()) continue;
                    std::string side = jstr(row, "position_side", jstr(row, "side"));
                    if (side != "LONG" && side != "SHORT") continue;
                    double sz = std::fabs(jnum(row, "amount", jnum(row, "size")));
                    if (sz < 1e-12) continue;
                    npos++;
                    double entry = jnum(row, "entry_price", jnum(row, "entry_px"));
                    double mark = jnum(row, "mid_price", jnum(row, "mark_px"));
                    int lev = static_cast<int>(jnum(row, "leverage"));
                    double pnl = jnum(row, "unrealized_pnl", jnum(row, "pnl_usd"));
                    double roe = jnum(row, "roe_pct");
                    if (std::fabs(pnl) < 1e-12 && entry > 0 && mark > 0 && sz > 0) {
                        pnl = (side == "SHORT") ? sz * (entry - mark) : sz * (mark - entry);
                    }
                    if (std::fabs(roe) < 1e-9 && entry > 0 && mark > 0 && lev > 0) {
                        if (side == "SHORT") roe = (entry - mark) / entry * lev * 100.0;
                        else roe = (mark - entry) / entry * lev * 100.0;
                    }
                    summed_upnl += pnl;
                    positions.push_back({
                        {"bot_id", id}, {"bot", jstr(h, "name", id)}, {"href", jstr(h, "href")},
                        {"coin", it.key()}, {"side", side}, {"size", sz},
                        {"entry_px", entry},
                        {"mark_px", mark},
                        {"leverage", lev},
                        {"pnl_usd", pnl}, {"roe_pct", roe}, {"source", "sql-status"},
                    });
                }
            }
        }

        double upnl = (npos > 0) ? summed_upnl : stated_upnl;
        if (have_stated_upnl && npos == 0) upnl = stated_upnl;
        if (!live) {
            equity = 0;
            free = 0;
            upnl = 0;
            npos = 0;
        } else if (!jhas(st, "equity_usd") && !jhas(st, "equity")) {
            auto nit = wired.find(id);
            if (nit != wired.end()) {
                auto pit = pool.find(fold_label(nit->second));
                if (pit != pool.end()) {
                    double eq = 0, fr = 0;
                    if (hl_cash_of(pit->second, eq, fr)) {
                        addr = pit->second;
                        equity = eq;
                        free = fr;
                    }
                }
            }
        }
        if (ok) n_ok++;
        if (running && !deactivated) n_live++;

        json bias = json::object();
        auto bj = basename_json(br, jstr(h, "bias_file"));
        if (bj.is_object()) {
            for (auto it = bj.begin(); it != bj.end(); ++it) {
                if (it.value().is_string() && it.key() != "updated" && it.key() != "source")
                    bias[it.key()] = it.value();
            }
        }
        if (g_sql_bias.contains(id) && g_sql_bias[id].is_object()) {
            for (auto it = g_sql_bias[id].begin(); it != g_sql_bias[id].end(); ++it) {
                if (!it.value().is_string()) continue;
                std::string v = it.value().get<std::string>();
                for (char& c : v) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
                if (v == "LONG" || v == "SHORT" || v == "FLAT")
                    bias[it.key()] = v;
            }
        }

        json trade_pack = last_trades_for(br, id);
        json logs = json::array();
        {
            char sum[192];
            std::snprintf(sum, sizeof(sum), "all-time %dW / %dL  earned %+.2f USD  (%d closes)",
                          static_cast<int>(jnum(trade_pack, "n_wins")),
                          static_cast<int>(jnum(trade_pack, "n_losses")),
                          jnum(trade_pack, "realized_pnl_usd"),
                          static_cast<int>(jnum(trade_pack, "n_closes")));
            logs.push_back(std::string(sum));
            json arr = trade_pack.value("trades", json::array());
            for (auto& t : arr) {
                if (!t.is_object()) continue;
                double pnl = jnum(t, "pnl_usd");
                double roe = jnum(t, "roe_pct");
                const char* wl = pnl > 1e-9 ? "WIN" : (pnl < -1e-9 ? "LOSS" : (roe > 0 ? "WIN" : (roe < 0 ? "LOSS" : "FLAT")));
                char row[192];
                std::snprintf(row, sizeof(row), "%s  %s  %s %s  %+0.2f  %+0.1f%%",
                              jstr(t, "ts").c_str(), wl,
                              jstr(t, "side").c_str(), jstr(t, "coin").c_str(),
                              pnl, roe);
                logs.push_back(std::string(row));
            }
            for (auto& row : refuse_log_lines(br, id)) logs.push_back(row);
        }

        bots.push_back({
            {"id", id},
            {"name", jstr(h, "name", id)},
            {"href", jstr(h, "href")},
            {"icon", jstr(h, "icon")},
            {"color", jstr(h, "color", "#9dffc0")},
            {"running", running},
            {"deactivated", deactivated},
            {"hl_ip", (deactivated || !running) ? "none" : "spending"},
            {"address", addr},
            {"equity_usd", equity},
            {"free_usd", free},
            {"n_pos", npos},
            {"upnl_usd", upnl},
            {"sl_roe_pct", sl_of(sl, id, jnum(st, "sl_roe_pct", 20))},
            {"ok", ok},
            {"health", deactivated ? "deactivated" : (ok ? "ok" : "issue")},
            {"health_reason", deactivated
                ? "deactivated - 0 Hyperliquid IP tokens, positions stay on the exchange"
                : (ok ? "ok" : (running ? "issue" : "container down"))},
            {"logs", logs},
            {"bias", bias},
            {"bias_mode", h.value("coins", json::array()).empty() ? "" : "both"},
            {"coins", h.value("coins", json::array())},
            {"markets", st.value("markets", json::array())},
            {"idle_markets", st.value("idle_markets", json::array())},
            {"controller", merge_controller(br, id, st, sl)},
            {"next", [&]() {
                json nx = {{"sell", "spring snapshot — no live HL overlay"}, {"rebuy", ""}};
                if (id == "hummingbot-onlyup" || id == "hummingbot-surfn") {
                    std::string mf = (id == "hummingbot-onlyup") ? "onlyup_moon.json" : "surfn_moon.json";
                    json moon = read_json(join(br.conf, mf));
                    std::string sell;
                    if (moon.is_object()) {
                        for (auto it = moon.begin(); it != moon.end(); ++it) {
                            if (!it.value().is_object()) continue;
                            if (!jbool(it.value(), "on")) continue;
                            double cpx = jnum(it.value(), "close_px", jnum(it.value(), "target_px"));
                            char buf[96];
                            if (cpx > 0)
                                std::snprintf(buf, sizeof(buf), "%s sell at $%.4g · ROE SL/clip off",
                                              it.key().c_str(), cpx);
                            else
                                std::snprintf(buf, sizeof(buf), "%s moon hold", it.key().c_str());
                            if (!sell.empty()) sell += " · ";
                            sell += buf;
                        }
                    }
                    if (!sell.empty()) nx["sell"] = sell;
                    json pos = st.value("positions", json::object());
                    if (pos.is_object()) {
                        std::string live;
                        for (auto it = pos.begin(); it != pos.end(); ++it) {
                            if (!it.value().is_object()) continue;
                            std::string side = jstr(it.value(), "position_side", jstr(it.value(), "side"));
                            if (side != "LONG" && side != "SHORT") continue;
                            double roe = jnum(it.value(), "roe_pct");
                            double pnl = jnum(it.value(), "unrealized_pnl", jnum(it.value(), "pnl_usd"));
                            char buf[96];
                            std::snprintf(buf, sizeof(buf), "%s %s ROE %+.1f%% uPnL $%.2f",
                                          it.key().c_str(), side.c_str(), roe, pnl);
                            if (!live.empty()) live += " · ";
                            live += buf;
                        }
                        if (!live.empty())
                            nx["sell"] = sell.empty() ? live : (live + " · " + sell);
                    }
                }
                return nx;
            }()},
            {"trades", trade_pack.value("trades", json::array())},
            {"n_wins", trade_pack.value("n_wins", 0.0)},
            {"n_losses", trade_pack.value("n_losses", 0.0)},
            {"n_opens", trade_pack.value("n_opens", 0.0)},
            {"n_closes", trade_pack.value("n_closes", 0.0)},
            {"realized_pnl_usd", trade_pack.value("realized_pnl_usd", 0.0)},
            {"copy_traders", copy ? copy_follow_list(br, st) : json::array()},
        });
    }

    fold_live_funds(bots, positions, eq_sum, upnl_sum);

    auto gauge = read_json(join(br.conf, "condor_gauge.json"));
    json condor = {
        {"ok", jbool(gauge, "ok")},
        {"running", docker_running("hummingbot-condor") || docker_running("condor")},
        {"regime", jstr(gauge, "regime")},
        {"source", jstr(gauge, "source")},
        {"title", jstr(gauge, "title", "Condor")},
        {"why", jstr(gauge, "why")},
        {"ts", jstr(gauge, "ts")},
        {"bias", gauge.value("bias", json::object())},
        {"feed", json::array()},
        {"telegram", {{"ok", false}}},
        {"grok_bot", {{"configured", false}}},
    };
    auto feed = read_json(join(br.conf, "condor_feed.json"));
    if (feed.contains("lines") && feed["lines"].is_array()) {
        json lines = json::array();
        auto arr = feed["lines"];
        int start = std::max(0, static_cast<int>(arr.size()) - 16);
        for (int i = start; i < static_cast<int>(arr.size()); ++i) lines.push_back(arr[i]);
        condor["feed"] = lines;
    }

    json mids = json::object();
    if (gauge.contains("tape") && gauge["tape"].is_object()) {
        for (auto it = gauge["tape"].begin(); it != gauge["tape"].end(); ++it)
            if (it.value().is_object()) mids[it.key()] = jnum(it.value(), "mid");
    }

    auto db = read_json(join(br.conf, "desk_bias.json"));
    json desk_hunters = json::array();
    for (auto& b : bots) {
        desk_hunters.push_back({
            {"id", b.value("id", "")},
            {"name", b.value("name", "")},
            {"applied", true},
            {"waiting", false},
            {"held", json::array()},
            {"target", jstr(db, "bias")},
        });
    }

    return {
        {"bots", bots},
        {"positions", positions},
        {"mids", mids},
        {"totals", {
            {"equity_usd", eq_sum},
            {"upnl_usd", upnl_sum},
            {"n_pos", static_cast<int>(positions.size())},
            {"n_bots", static_cast<int>(bots.size())},
            {"n_live", n_live},
            {"n_ok", n_ok},
        }},
        {"ts", utc_now()},
        {"desk_bias", {
            {"bias", jstr(db, "bias")},
            {"closes", false},
            {"hunters", desk_hunters},
        }},
        {"condor", condor},
        {"rate", api_rate(br)},
        {"hl_key", hl_key_view(br.conf)},
        {"risk", api_risk(br)},
        {"health", api_desk_health()},
        {"leaders", json::array()},
        {"brain", "spring"},
        {"debug", true},
        {"writes", writes_on()},
        {"closes", false},
        {"positions_untouched", true},
        {"note", writes_on()
            ? "Spring writes ON — config files only, never CLOSE/OPEN HL clips"
            : "Spring read-only — live orders stay on Flask hunters"},
    };
}

static bool is_v2_sleeper(const std::string& id) {
    return id.size() > 14 && id.compare(0, 14, "hummingbot-v2-") == 0 && valid_id(id);
}

static json v2_sleeper_list(const Brain& br) {
    auto j = read_json(join(br.conf, "v2_sleepers.json"));
    if (j.contains("sleepers") && j["sleepers"].is_array()) return j["sleepers"];
    return json::array();
}

static int v2_running_count() {
    int n = 0;
    FILE* fp = popen("docker ps --format '{{.Names}}' 2>/dev/null", "r");
    if (!fp) return 0;
    char line[256];
    while (fgets(line, sizeof(line), fp)) {
        std::string s = line;
        while (!s.empty() && (s.back() == '\n' || s.back() == '\r')) s.pop_back();
        if (is_v2_sleeper(s)) n++;
    }
    pclose(fp);
    return n;
}

static json api_sleeper(const Brain& br, const json& data) {
    std::string id = jstr(data, "id");
    std::string op = jstr(data, "op");
    if (!is_v2_sleeper(id))
        return {{"ok", false}, {"error", "not a V2 sleeper — live hunters untouched"}, {"closes", false}};
    int cap = 2;
    auto meta = read_json(join(br.conf, "v2_sleepers.json"));
    if (meta.contains("max_running") && meta["max_running"].is_number())
        cap = std::max(1, std::min(4, meta["max_running"].get<int>()));
    if (op == "start") {
        int n = v2_running_count();
        if (!docker_running(id) && n >= cap)
            return {{"ok", false}, {"error", "V2 cap " + std::to_string(cap) + " running — stop one first"},
                    {"closes", false}, {"running", n}, {"cap", cap}};
        std::string cmd = "docker start " + id + " >/dev/null 2>&1";
        int rc = std::system(cmd.c_str());
        g_docker_ts = 0;
        return {{"ok", rc == 0}, {"id", id}, {"op", "start"}, {"closes", false},
                {"positions_untouched", true},
                {"message", rc == 0 ? "V2 sleeper started (no keys until wallet wired)" : "docker start failed — is the container created?"}};
    }
    if (op == "stop") {
        std::string cmd = "docker stop -t 2 " + id + " >/dev/null 2>&1";
        std::system(cmd.c_str());
        g_docker_ts = 0;
        return {{"ok", true}, {"id", id}, {"op", "stop"}, {"closes", false},
                {"positions_untouched", true},
                {"message", "V2 sleeper asleep"}};
    }
    return {{"ok", false}, {"error", "op start|stop"}, {"closes", false}};
}

static json api_catalog(const Brain& br) {
    json hunters = registry(br);
    json spec_file = read_json(join(br.conf, "controller_specs.json"));
    json spec_fields = json::object();
    if (spec_file.is_object() && spec_file.contains("fields") && spec_file["fields"].is_object())
        spec_fields = spec_file["fields"];
    json fields = json::object();
    for (auto& h : hunters) {
        if (!h.is_object()) continue;
        std::string id = jstr(h, "id");
        if (spec_fields.contains(id) && spec_fields[id].is_array() && !spec_fields[id].empty())
            fields[id] = spec_fields[id];
        else
            fields[id] = h.value("controller_fields", json::array());
    }
    auto wallets = read_json(join(br.conf, "wire_wallets.json"));
    json extra = json::array();
    if (wallets.is_array()) extra = wallets;
    else if (wallets.contains("wallets") && wallets["wallets"].is_array()) extra = wallets["wallets"];
    json v2 = v2_sleeper_list(br);
    return {{"ok", true}, {"hunters", hunters}, {"v2_sleepers", v2}, {"wallets", extra}, {"traders", json::array()}, {"fields", fields}};
}

static json api_nav(const Brain& br) {
    json hunters = json::array();
    for (auto& h : registry(br)) {
        if (!h.is_object()) continue;
        std::string id = jstr(h, "id");
        if (!valid_id(id)) continue;
        hunters.push_back({
            {"id", id},
            {"name", jstr(h, "name", id)},
            {"href", jstr(h, "href")},
            {"icon", jstr(h, "icon")},
            {"color", jstr(h, "color")},
            {"running", docker_running(id)},
            {"deactivated", docker_paused(id)},
        });
    }
    return {{"ok", true}, {"hunters", hunters}, {"ts", utc_now()}};
}

static json file_or_empty_graph(const Brain& br, const std::string& name) {
    auto j = read_json(join(br.conf, name));
    if (!j.contains("nodes")) j = {{"v", 5}, {"nodes", json::array()}, {"edges", json::array()}};
    return j;
}

static json api_wire_graph_post(const Brain& br, const std::string& path, const json& data) {
    std::string file = "wire_graph.json";
    std::string board = "desk";
    if (path.find("home") != std::string::npos) { file = "wire_home.json"; board = "home"; }
    else if (path.find("health") != std::string::npos) { file = "wire_health.json"; board = "health"; }
    auto fp = join(br.conf, file);
    json cur = file_or_empty_graph(br, file);
    json nodes = data.contains("nodes") && data["nodes"].is_array() ? data["nodes"] : json::array();
    json edges = data.contains("edges") && data["edges"].is_array() ? data["edges"] : json::array();
    if (path.find("layout") != std::string::npos) {
        if (!nodes.is_array() || nodes.empty())
            return {{"ok", false}, {"error", "no positions"}};
        json incoming = json::object();
        for (auto& n : nodes) {
            if (!n.is_object()) continue;
            std::string id = jstr(n, "id");
            if (!id.empty()) incoming[id] = n;
        }
        json have = cur.value("nodes", json::array());
        std::set<std::string> have_ids;
        for (auto& n : have) {
            if (!n.is_object()) continue;
            std::string id = jstr(n, "id");
            if (id.empty() || !incoming.contains(id)) continue;
            have_ids.insert(id);
            auto& in = incoming[id];
            json pos = in.contains("position") && in["position"].is_object() ? in["position"] : in;
            double ox = 0, oy = 0;
            if (n.contains("position") && n["position"].is_object()) {
                ox = n["position"].value("x", 0.0);
                oy = n["position"].value("y", 0.0);
            }
            n["position"] = {{"x", pos.value("x", ox)}, {"y", pos.value("y", oy)}};
            if (in.contains("type") && in["type"].is_string()) n["type"] = in["type"];
            if (in.contains("data") && in["data"].is_object()) {
                if (!n.contains("data") || !n["data"].is_object()) n["data"] = json::object();
                for (auto it = in["data"].begin(); it != in["data"].end(); ++it) {
                    if (it.key() == "updated" || it.key() == "source") continue;
                    n["data"][it.key()] = it.value();
                }
            }
        }
        int added = 0;
        for (auto& n : nodes) {
            if (!n.is_object()) continue;
            std::string id = jstr(n, "id");
            if (id.empty() || have_ids.count(id)) continue;
            have.push_back(n);
            have_ids.insert(id);
            added++;
        }
        cur["nodes"] = have;
        cur["updated"] = utc_now();
        if (!write_json(fp, cur))
            return {{"ok", false}, {"error", "layout write failed"}};
        return {{"ok", true}, {"n", have.size()}, {"added", added}, {"closes", false},
                {"positions_untouched", true},
                {"message", added ? "layout saved · new group box written" : "layout saved to " + file}};
    }
    json graph = data.contains("graph") && data["graph"].is_object() ? data["graph"] : data;
    if (graph.contains("nodes") && graph["nodes"].is_array()) nodes = graph["nodes"];
    if (graph.contains("edges") && graph["edges"].is_array()) edges = graph["edges"];
    json outg = {{"v", graph.value("v", 5)}, {"board", board}, {"nodes", nodes}, {"edges", edges}, {"updated", utc_now()}};
    if (!write_json(fp, outg))
        return {{"ok", false}, {"error", "could not write " + file}};
    return {
        {"ok", true},
        {"path", "conf/" + file},
        {"n", nodes.size()},
        {"e", edges.size()},
        {"closes", false},
        {"positions_untouched", true},
        {"message", "cosmetics saved (" + board + ") — boxes/layout only, wires not applied, clips untouched"},
    };
}

static json api_wire_pack_get(const Brain& br) {
    json desk = file_or_empty_graph(br, "wire_graph.json");
    json home = file_or_empty_graph(br, "wire_home.json");
    json health = file_or_empty_graph(br, "wire_health.json");
    return {
        {"kind", "freiquell-wire-pack"},
        {"v", 1},
        {"saved_at", utc_now()},
        {"positions_untouched", true},
        {"ok", true},
        {"note", "canvas only — restore writes graph files, does not CLOSE/OPEN Hyperliquid"},
        {"boards", {{"desk", desk}, {"home", home}, {"health", health}}},
    };
}

static json api_wire_pack_post(const Brain& br, const json& data) {
    std::string action = jstr(data, "action");
    auto pack_path = join(br.conf, "wire_pack.json");
    if (action == "save" || (action.empty() && jstr(data, "kind") != "freiquell-wire-pack")) {
        json pack = api_wire_pack_get(br);
        if (!write_json(pack_path, pack))
            return {{"ok", false}, {"error", "could not write conf/wire_pack.json"}};
        return {
            {"ok", true},
            {"path", "conf/wire_pack.json"},
            {"closes", false},
            {"positions_untouched", true},
            {"message", "saved conf/wire_pack.json — one file to recover desk/home/health WIRE"},
        };
    }
    json pack = data;
    if (jstr(pack, "kind") != "freiquell-wire-pack")
        pack = read_json(pack_path);
    if (jstr(pack, "kind") != "freiquell-wire-pack")
        return {{"ok", false}, {"error", "not a freiquell-wire-pack JSON (need conf/wire_pack.json or upload)"}};
    json boards = pack.contains("boards") && pack["boards"].is_object() ? pack["boards"] : json::object();
    struct Map { const char* key; const char* file; };
    Map maps[] = {{"desk", "wire_graph.json"}, {"home", "wire_home.json"}, {"health", "wire_health.json"}};
    json wrote = json::array();
    for (auto& m : maps) {
        if (!boards.contains(m.key) || !boards[m.key].is_object()) continue;
        json g = boards[m.key];
        json out = {
            {"v", g.value("v", 5)},
            {"board", m.key},
            {"nodes", g.value("nodes", json::array())},
            {"edges", g.value("edges", json::array())},
        };
        if (!write_json(join(br.conf, m.file), out))
            return {{"ok", false}, {"error", std::string("write failed ") + m.file}, {"wrote", wrote}};
        wrote.push_back(m.key);
    }
    return {
        {"ok", true},
        {"wrote", wrote},
        {"closes", false},
        {"positions_untouched", true},
        {"message", "wire pack restored — Reload WIRE; clips untouched"},
    };
}

static json deny_write(const std::string& path) {
    return {
        {"ok", false},
        {"error", "Spring writes are OFF — " + path + " not applied"},
        {"writes", false},
        {"closes", false},
        {"positions_untouched", true},
        {"message", "enable WRITE MODE in springctl to save config files. Spring never closes or opens HL clips."},
    };
}

static json deny_close(const std::string& path) {
    return {
        {"ok", false},
        {"error", "Spring will not touch live positions — " + path + " blocked"},
        {"writes", writes_on()},
        {"closes", false},
        {"positions_untouched", true},
        {"message", "no CLOSE / OPEN / force_entry from Spring"},
    };
}

static bool touches_positions(const std::string& path) {
    auto p = path;
    for (char& c : p) if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
    return p.find("close") != std::string::npos
        || p.find("force_entry") != std::string::npos
        || p.find("force_open") != std::string::npos
        || p.find("hl_close") != std::string::npos
        || p.find("/open") != std::string::npos;
}

static json parse_body(const std::string& body) {
    try { return json::parse(body.empty() ? "{}" : body); }
    catch (...) { return json::object(); }
}

static json apply_manual_close_px(const Brain& br, const std::string& id, double px) {
    std::string moon_name;
    std::string st_name;
    if (id == "hummingbot-onlyup") { moon_name = "onlyup_moon.json"; st_name = "onlyup_status.json"; }
    else if (id == "hummingbot-surfn") { moon_name = "surfn_moon.json"; st_name = "surfn_status.json"; }
    else
        return {{"ok", false}, {"error", "close $ is Only Up / SuperTrend"}, {"closes", false}};
    if (px <= 0) {
        json moon = read_json(join(br.conf, moon_name));
        if (!moon.is_object()) moon = json::object();
        for (auto it = moon.begin(); it != moon.end(); ++it) {
            if (!it.value().is_object()) continue;
            if (it.key() == "updated" || it.key() == "source") continue;
            it.value()["on"] = false;
            it.value().erase("close_px");
        }
        moon["updated"] = utc_now();
        moon["source"] = "matrix";
        if (!write_json(join(br.conf, moon_name), moon))
            return {{"ok", false}, {"error", "write moon failed"}, {"closes", false}};
        json coins = json::object();
        for (auto it = moon.begin(); it != moon.end(); ++it) {
            if (it.value().is_object() && it.key() != "updated" && it.key() != "source")
                coins[it.key()] = it.value();
        }
        sql_apply(br, {{"op", "kv"}, {"bot_id", id}, {"values", {{"moon", coins}, {"close_px", 0}}}});
        return {
            {"ok", true}, {"id", id}, {"close_px", 0}, {"moon", false},
            {"closes", false}, {"positions_untouched", true},
            {"message", "moon OFF — clips stay, SL/clip on again"},
        };
    }
    if (px > 1e7)
        return {{"ok", false}, {"error", "close $ out of range"}, {"closes", false}};
    json st = read_json(join(br.conf, st_name));
    json pos = st.is_object() ? st.value("positions", json::object()) : json::object();
    std::string coin = "LTC";
    double mark = 0, entry = 0;
    if (pos.is_object()) {
        for (auto it = pos.begin(); it != pos.end(); ++it) {
            if (!it.value().is_object()) continue;
            std::string side = jstr(it.value(), "position_side", jstr(it.value(), "side"));
            if (side != "LONG") continue;
            coin = it.key();
            mark = jnum(it.value(), "mid_price", jnum(it.value(), "mark_px"));
            entry = jnum(it.value(), "entry_price", jnum(it.value(), "entry_px"));
            break;
        }
    }
    if (mark > 0 && px <= mark + 1e-9)
        return {
            {"ok", false},
            {"error", "close $ at or below mark — would flatten now"},
            {"closes", false}, {"positions_untouched", true},
        };
    json moon = read_json(join(br.conf, moon_name));
    if (!moon.is_object()) moon = json::object();
    json row = moon.contains(coin) && moon[coin].is_object() ? moon[coin] : json::object();
    double anchor = jnum(row, "anchor_px", entry > 0 ? entry : mark);
    double pct = (anchor > 0) ? (px / anchor - 1.0) * 100.0 : 50.0;
    row["on"] = true;
    row["close_px"] = px;
    row["target_px"] = px;
    row["anchor_px"] = anchor;
    row["coin_pct"] = pct;
    row["armed"] = utc_now();
    row["source"] = "matrix";
    moon[coin] = row;
    moon["updated"] = utc_now();
    moon["source"] = "matrix";
    if (!write_json(join(br.conf, moon_name), moon))
        return {{"ok", false}, {"error", "write moon failed"}, {"closes", false}};
    json coins = json::object();
    for (auto it = moon.begin(); it != moon.end(); ++it) {
        if (it.value().is_object() && it.key() != "updated" && it.key() != "source")
            coins[it.key()] = it.value();
    }
    sql_apply(br, {{"op", "kv"}, {"bot_id", id}, {"values", {{"moon", coins}, {"close_px", px}}}});
    return {
        {"ok", true}, {"id", id}, {"coin", coin}, {"close_px", px},
        {"closes", false}, {"positions_untouched", true},
        {"message", coin + " sell at $" + std::to_string(px) + " — clip stays until then"},
    };
}

static bool as_bool(const json& val) {
    if (val.is_boolean()) return val.get<bool>();
    if (val.is_number()) return val.get<double>() != 0.0;
    if (val.is_string()) {
        std::string s = val.get<std::string>();
        for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        return s == "1" || s == "true" || s == "on" || s == "yes";
    }
    return false;
}

// ON is the live two_wins skim. OFF is mode keep plus skim_pct 0 and parked 0,
// which the already-loaded hunter honors without a restart. Does not send.
static bool apply_dman_every_2_win(const Brain& br, bool on) {
    const char* id = "hummingbot-dman-maker";
    std::string path = join(br.conf, "risk_skim.json");
    std::string lockp = path + ".lock";
    int fd = ::open(lockp.c_str(), O_CREAT | O_RDWR, 0666);
    if (fd >= 0) ::flock(fd, LOCK_EX);
    auto unlock = [&]() {
        if (fd >= 0) {
            ::flock(fd, LOCK_UN);
            ::close(fd);
            fd = -1;
        }
    };
    json data = read_json(path);
    if (!data.is_object() || !data.contains("hunters") || !data["hunters"].is_object()
        || !data["hunters"].contains(id) || !data["hunters"][id].is_object()) {
        unlock();
        return false;
    }
    json row = data["hunters"][id];
    if (on) {
        row["mode"] = "two_wins";
        row["skim_pct"] = 100;
        row["streak_wins"] = 2;
        row["note"] = "two realized wins in a row → take 2nd win profit only";
    } else {
        double was = jnum(row, "parked_usd", 0);
        row["mode"] = "keep";
        row["skim_pct"] = 0;
        row["parked_usd"] = 0;
        row["note"] = "wins stay on the hunter account";
        if (was > 0) {
            json ev = row.contains("events") && row["events"].is_array() ? row["events"] : json::array();
            ev.push_back({
                {"ts", utc_now()},
                {"take", 0},
                {"why", "send every 2 win off — parked released to the hunter"},
            });
            row["events"] = ev;
        }
    }
    data["hunters"][id] = row;
    data["updated"] = utc_now();
    bool ok = write_json(path, data);
    unlock();
    return ok;
}

static json api_ctrl_post(const Brain& br, const json& data) {
    std::string id = jstr(data, "id", jstr(data, "bot_id"));
    if (!valid_id(id)) return {{"ok", false}, {"error", "unknown hunter"}};
    json values = data.contains("values") && data["values"].is_object() ? data["values"] : json::object();
    if (values.empty() && data.contains("key"))
        values[jstr(data, "key")] = data.contains("value") ? data["value"] : json();
    if (values.empty()) {
        for (auto it = data.begin(); it != data.end(); ++it) {
            const auto& k = it.key();
            if (k == "id" || k == "bot_id" || k == "key" || k == "value" || k == "ok" || k == "source")
                continue;
            values[k] = it.value();
        }
    }
    std::lock_guard<std::mutex> lk(g_write_mu);
    json row = ctrl_file(br, id);
    if (!row.is_object()) row = json::object();
    json changed = json::array();
    std::string msgs;
    for (auto it = values.begin(); it != values.end(); ++it) {
        std::string key = it.key();
        if (key.empty() || key == "updated" || key == "source") continue;
        json val = it.value();
        if ((key == "take_profit" || key == "trail_activation" || key == "rebuy_pct")
            && val.is_number() && val.get<double>() > 1.0)
            val = val.get<double>() / 100.0;
        if (key == "dontGoCrazy" || key == "afterLossNoRebuy" || key == "copy_shorts"
            || key == "sendEvery2Win") {
            val = as_bool(val);
        }
        if (key == "sendEvery2Win" && id == "hummingbot-dman-maker") {
            if (!apply_dman_every_2_win(br, val.get<bool>()))
                return {{"ok", false}, {"error", "risk skim not saved"},
                        {"closes", false}, {"positions_untouched", true}};
        }
        if (key == "sl_roe_pct") {
            double sl = val.is_number() ? val.get<double>() : 20;
            if (sl < 1 || sl > 90) return {{"ok", false}, {"error", "sl_roe_pct must be 1–90"}};
            auto slp = read_json(join(br.conf, "bot_sl.json"));
            if (!slp.is_object()) slp = json::object();
            json srow = slp.contains(id) && slp[id].is_object() ? slp[id] : json::object();
            srow["sl_roe_pct"] = sl;
            srow["updated"] = utc_now();
            srow["source"] = "spring";
            slp[id] = srow;
            write_json(join(br.conf, "bot_sl.json"), slp);
            val = sl;
        }
        row[key] = val;
        changed.push_back(key);
        if (key == "close_px" && val.is_number()) {
            auto armed = apply_manual_close_px(br, id, val.get<double>());
            if (!jbool(armed, "ok", false))
                return armed;
            if (!msgs.empty()) msgs += " · ";
            msgs += jstr(armed, "message");
        }
    }
    row["updated"] = utc_now();
    row["source"] = "spring";
    json kv = json::object();
    for (auto& k : changed) {
        if (k.is_string() && row.contains(k.get<std::string>()))
            kv[k.get<std::string>()] = row[k.get<std::string>()];
    }
    sql_apply(br, {{"op", "kv"}, {"bot_id", id}, {"values", kv}});
    if (changed.empty())
        return {{"ok", false}, {"error", "no controller keys"}, {"closes", false}};
    if (!write_json(join(br.conf, id + "_ctrl.json"), row))
        return {{"ok", false}, {"error", "write ctrl failed"}};
    {
        std::lock_guard<std::mutex> lk(g_live_mu);
        json live = g_live_knobs.contains(id) && g_live_knobs[id].is_object()
            ? g_live_knobs[id] : json::object();
        for (auto& k : changed) {
            if (k.is_string() && row.contains(k.get<std::string>()))
                live[k.get<std::string>()] = row[k.get<std::string>()];
        }
        live["updated"] = row["updated"];
        g_live_knobs[id] = live;
    }
    return {
        {"ok", true}, {"id", id}, {"changed", changed},
        {"closes", false}, {"positions_untouched", true},
        {"message", "controller saved — no HL close/open"},
    };
}

static std::string qget(const std::string& q, const std::string& key) {
    auto needle = key + "=";
    auto p = q.find(needle);
    if (p == std::string::npos) return {};
    auto v = q.substr(p + needle.size());
    auto amp = v.find('&');
    if (amp != std::string::npos) v.resize(amp);
    return v;
}

static json api_presets_get(const Brain& br, const std::string& query) {
    std::string id = qget(query, "id");
    if (id.empty())
        return {{"ok", false}, {"error", "id required — presets are per hunter"}, {"closes", false}};
    if (!valid_id(id)) return {{"ok", false}, {"error", "unknown bot"}, {"closes", false}};
    json all = read_json(join(br.conf, "controller_presets.json"));
    json list = json::array();
    if (all.is_object() && all.contains("presets") && all["presets"].is_object()
        && all["presets"].contains(id) && all["presets"][id].is_array())
        list = all["presets"][id];
    if (!list.is_array() || list.empty()) {
        sql_apply(br, {{"op", "preset_seed"}});
        all = read_json(join(br.conf, "controller_presets.json"));
        if (all.is_object() && all.contains("presets") && all["presets"].is_object()
            && all["presets"].contains(id) && all["presets"][id].is_array())
            list = all["presets"][id];
    }
    return {
        {"ok", true}, {"id", id}, {"presets", list.is_array() ? list : json::array()},
        {"closes", false}, {"positions_untouched", true},
    };
}

static json api_presets_post(Brain& br, const json& data) {
    if (!data.is_object()) return {{"ok", false}, {"error", "object required"}, {"closes", false}};
    std::string id = jstr(data, "id");
    if (!valid_id(id)) return {{"ok", false}, {"error", "unknown bot"}, {"closes", false}};
    std::string op = jstr(data, "op", "save");
    std::string name = jstr(data, "name");
    if (name.empty()) return {{"ok", false}, {"error", "name required"}, {"closes", false}};
    if (op == "save") {
        json values = data.contains("values") && data["values"].is_object()
            ? data["values"] : json::object();
        if (values.empty()) {
            auto row = read_json(join(br.conf, id + "_ctrl.json"));
            if (row.is_object()) values = row;
        }
        if (!sql_apply(br, {
                {"op", "preset_save"}, {"bot_id", id}, {"name", name},
                {"values", values}, {"locked", false},
            })) {
            return {
                {"ok", false}, {"closes", false},
                {"error", "save failed — STD is locked, pick another name"},
            };
        }
        return {
            {"ok", true}, {"id", id}, {"name", name}, {"closes", false},
            {"positions_untouched", true},
            {"message", "saved '" + name + "' for this hunter only"},
        };
    }
    if (op == "apply") {
        json all = read_json(join(br.conf, "controller_presets.json"));
        json vals = json::object();
        if (all.is_object() && all.contains("presets") && all["presets"].is_object()
            && all["presets"].contains(id) && all["presets"][id].is_array()) {
            for (auto& p : all["presets"][id]) {
                if (p.is_object() && jstr(p, "name") == name) {
                    if (p.contains("values") && p["values"].is_object()) vals = p["values"];
                    break;
                }
            }
        }
        if (vals.empty())
            return {{"ok", false}, {"error", "unknown preset"}, {"closes", false}};
        json body = {{"id", id}, {"values", vals}};
        auto out = api_ctrl_post(br, body);
        {
            std::lock_guard<std::mutex> lk(g_live_mu);
            json live = g_live_knobs.contains(id) && g_live_knobs[id].is_object()
                ? g_live_knobs[id] : json::object();
            for (auto it = vals.begin(); it != vals.end(); ++it) {
                if (it.key() == "updated" || it.key() == "source") continue;
                live[it.key()] = it.value();
            }
            g_live_knobs[id] = live;
        }
        out["preset"] = name;
        out["values"] = vals;
        out["closes"] = false;
        if (!out.contains("message"))
            out["message"] = "loaded '" + name + "' — no HL close/open";
        return out;
    }
    return {{"ok", false}, {"error", "op must be save or apply"}, {"closes", false}};
}

static json api_live_knobs_get(const Brain& br, const std::string& query) {
    std::string id = qget(query, "id");
    if (id.empty()) id = "hummingbot-bn-swing";
    json row;
    {
        std::lock_guard<std::mutex> lk(g_live_mu);
        if (g_live_knobs.contains(id) && g_live_knobs[id].is_object())
            row = g_live_knobs[id];
    }
    if (!row.is_object() || row.empty()) {
        row = read_json(join(br.conf, id + "_ctrl.json"));
        if (!row.is_object()) row = json::object();
        auto slp = read_json(join(br.conf, "bot_sl.json"));
        if (slp.is_object() && slp.contains(id) && slp[id].is_object())
            row["sl_roe_pct"] = jnum(slp[id], "sl_roe_pct", jnum(row, "sl_roe_pct", 20));
        std::lock_guard<std::mutex> lk(g_live_mu);
        g_live_knobs[id] = row;
    }
    if (jnum(row, "sl_roe_pct", 0) <= 0) {
        auto slp = read_json(join(br.conf, "bot_sl.json"));
        if (slp.is_object() && slp.contains(id) && slp[id].is_object()) {
            double slv = jnum(slp[id], "sl_roe_pct", 0);
            if (slv > 0) row["sl_roe_pct"] = slv;
        }
    }
    row["ok"] = true;
    row["id"] = id;
    row["source"] = "spring-live";
    return row;
}

static json api_live_knobs_post(const json& data) {
    if (!data.is_object()) return {{"ok", false}, {"error", "object required"}};
    std::string id = jstr(data, "id");
    if (!valid_id(id)) return {{"ok", false}, {"error", "unknown bot"}};
    json row = data.contains("controller") && data["controller"].is_object()
        ? data["controller"] : data;
    std::lock_guard<std::mutex> lk(g_live_mu);
    json cur = g_live_knobs.contains(id) && g_live_knobs[id].is_object()
        ? g_live_knobs[id] : json::object();
    for (auto it = row.begin(); it != row.end(); ++it) {
        if (it.key() == "id" || it.key() == "ok" || it.key() == "source") continue;
        cur[it.key()] = it.value();
    }
    g_live_knobs[id] = cur;
    return {{"ok", true}, {"id", id}, {"closes", false}, {"positions_untouched", true}};
}

static json api_bias_post(const Brain& br, const json& data) {
    std::string id = jstr(data, "id", jstr(data, "bot_id"));
    std::string bias = jstr(data, "bias");
    for (char& c : bias) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    if (!valid_id(id) || (bias != "LONG" && bias != "SHORT" && bias != "FLAT"))
        return {{"ok", false}, {"error", "bias must be LONG, SHORT, or FLAT"}};
    json h;
    for (auto& x : registry(br)) if (jstr(x, "id") == id) h = x;
    if (!h.is_object()) return {{"ok", false}, {"error", "unknown hunter"}};
    std::string bf = basename_only(jstr(h, "bias_file"));
    if (bf.empty()) return {{"ok", false}, {"error", "no bias file"}};
    std::lock_guard<std::mutex> lk(g_write_mu);
    json cur = read_json(join(br.conf, bf));
    if (!cur.is_object()) cur = json::object();
    std::string coin = jstr(data, "coin");
    for (char& c : coin) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    if (!coin.empty()) cur[coin] = bias;
    else {
        for (auto& c : h.value("coins", json::array()))
            if (c.is_string()) cur[c.get<std::string>()] = bias;
    }
    cur["updated"] = utc_now();
    cur["source"] = "spring";
    sql_apply(br, {{"op", "bias"}, {"bot_id", id}, {"mapping", bias_mapping(cur)}});
    if (!write_json(join(br.conf, bf), cur))
        return {{"ok", false}, {"error", "write bias failed"}};
    return {
        {"ok", true}, {"id", id}, {"bias", cur},
        {"closes", false}, {"positions_untouched", true},
        {"message", (coin.empty() ? std::string("all") : coin) + " BIAS " + bias + " — does not close"},
    };
}

static json api_sl_post(const Brain& br, const json& data) {
    std::string id = jstr(data, "id");
    double sl = jnum(data, "sl_roe_pct");
    if (!valid_id(id) || sl < 1 || sl > 90)
        return {{"ok", false}, {"error", "sl_roe_pct must be 1–90"}};
    std::lock_guard<std::mutex> lk(g_write_mu);
    auto slp = read_json(join(br.conf, "bot_sl.json"));
    if (!slp.is_object()) slp = json::object();
    json row = slp.contains(id) && slp[id].is_object() ? slp[id] : json::object();
    row["sl_roe_pct"] = sl;
    row["updated"] = utc_now();
    row["source"] = "spring";
    slp[id] = row;
    sql_apply(br, {{"op", "kv"}, {"bot_id", id}, {"values", {{"sl_roe_pct", sl}}}});
    if (!write_json(join(br.conf, "bot_sl.json"), slp))
        return {{"ok", false}, {"error", "write SL failed"}};
    {
        std::lock_guard<std::mutex> lk(g_live_mu);
        json live = g_live_knobs.contains(id) && g_live_knobs[id].is_object()
            ? g_live_knobs[id] : json::object();
        live["sl_roe_pct"] = sl;
        live["updated"] = row["updated"];
        g_live_knobs[id] = live;
    }
    char msg[96];
    snprintf(msg, sizeof(msg), "stop-loss −%.0f%% ROE — does not close now", sl);
    return {{"ok", true}, {"id", id}, {"sl_roe_pct", sl},
            {"closes", false}, {"positions_untouched", true}, {"message", msg}};
}

static json gate_reply(bool ok, const std::string& id, bool deactivated, const std::string& message,
                        const std::string& error = {}) {
    json j = {
        {"ok", ok},
        {"id", id},
        {"deactivated", deactivated},
        {"active", !deactivated},
        {"closes", false},
        {"positions_untouched", true},
        {"hl_ip", deactivated ? "none" : "resumes"},
        {"message", message},
    };
    if (!error.empty()) j["error"] = error;
    return j;
}

// Pause freezes the hunter process. No SIGTERM, no cancel-on-exit, no exchange call.
// Unpause continues the same process. Never stop, kill, restart, or start.
static json api_hunter_gate_post(const Brain& br, const json& data) {
    std::string id = jstr(data, "id");
    if (!hunter_pausable(id))
        return gate_reply(false, id, false, "not a hunter on this matrix", "not a hunter");
    bool listed = false;
    for (auto& h : registry(br)) {
        if (h.is_object() && jstr(h, "id") == id) { listed = true; break; }
    }
    if (!listed)
        return gate_reply(false, id, false, "hunter is not in the registry", "hunter not on this matrix");
    if (!data.contains("active") || !data["active"].is_boolean())
        return gate_reply(false, id, false, "active must be true or false", "active must be true or false");
    bool active = data["active"].get<bool>();
    {
        std::lock_guard<std::mutex> lk(g_mu);
        g_docker_ts = 0;
    }
    bool paused = docker_paused(id);
    bool running = docker_running(id);
    if (active) {
        if (!paused) {
            return gate_reply(true, id, false, running
                ? id + " ACTIVE green. Spring does not touch positions."
                : id + " is off. Activate does not start it. Spring does not touch positions.");
        }
    } else if (paused) {
        return gate_reply(true, id, true,
                          id + " DEACTIVATED red - 0 HL IP. Spring does not touch positions.");
    } else if (!running) {
        return gate_reply(true, id, false,
                          id + " already off - 0 HL IP. Spring does not touch positions.");
    }
    const char* verb = active ? "unpause" : "pause";
    std::string cmd = std::string("docker ") + verb + " " + id + " >/dev/null 2>&1";
    int rc = std::system(cmd.c_str());
    if (rc != 0)
        return gate_reply(false, id, paused,
                          std::string("docker ") + verb + " failed",
                          std::string("docker ") + verb + " failed");
    {
        std::lock_guard<std::mutex> lk(g_mu);
        if (active) g_paused.erase(id);
        else {
            g_paused.insert(id);
            g_running.insert(id);
        }
        g_docker_ts = static_cast<double>(std::time(nullptr));
    }
    if (active)
        return gate_reply(true, id, false,
                          id + " ACTIVE green. Spring does not touch positions.");
    return gate_reply(true, id, true,
                      id + " DEACTIVATED red - 0 HL IP. Spring does not touch positions.");
}

static json api_restart_post(const json& data) {
    std::string id = jstr(data, "id");
    if (!valid_id(id)) return {{"ok", false}, {"error", "unknown bot"}};
    std::string cmd = "docker restart " + id + " >/dev/null 2>&1";
    int rc = std::system(cmd.c_str());
    if (rc != 0) return {{"ok", false}, {"error", "docker restart failed"}};
    return {{"ok", true}, {"id", id}, {"closes", false}, {"positions_untouched", true},
            {"message", id + " restarting — positions stay on Hyperliquid"}};
}

static json api_desk_bias_post(const Brain& br, const json& data) {
    std::string bias = jstr(data, "bias");
    for (char& c : bias) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    if (bias != "LONG" && bias != "SHORT" && bias != "FLAT")
        return {{"ok", false}, {"error", "bias must be LONG, FLAT, or SHORT"}};
    std::lock_guard<std::mutex> lk(g_write_mu);
    json st = {
        {"bias", bias}, {"closes", false}, {"updated", utc_now()}, {"source", "spring"},
    };
    if (!write_json(join(br.conf, "desk_bias.json"), st))
        return {{"ok", false}, {"error", "write desk_bias failed"}};
    for (auto& h : registry(br)) {
        if (!h.is_object()) continue;
        std::string id = jstr(h, "id");
        std::string bf = basename_only(jstr(h, "bias_file"));
        if (!valid_id(id) || bf.empty()) continue;
        json cur = read_json(join(br.conf, bf));
        if (!cur.is_object()) cur = json::object();
        json coins = h.value("coins", json::array());
        if (!coins.is_array() || coins.empty()) continue;
        for (auto& c : coins) if (c.is_string()) cur[c.get<std::string>()] = bias;
        cur["updated"] = utc_now();
        cur["source"] = "spring-desk";
        sql_apply(br, {{"op", "bias"}, {"bot_id", id}, {"mapping", bias_mapping(cur)}});
        write_json(join(br.conf, bf), cur);
    }
    return {
        {"ok", true}, {"desk_bias", st}, {"closes", false}, {"positions_untouched", true},
        {"message", "desk BIAS " + bias + " — does not close open clips"},
    };
}

static json api_home_close_post(const Brain& br, const json& data) {
    std::string id = jstr(data, "id", jstr(data, "bot_id", jstr(data, "bot")));
    std::string coin = jstr(data, "coin");
    for (char& c : coin) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    if (!valid_id(id))
        return {{"ok", false}, {"queued", false}, {"error", "bad hunter"}};
    if (coin.empty())
        return {{"ok", false}, {"queued", false}, {"error", "coin required"}};
    json payload = {{"coin", coin}, {"source", "spring"}};
    if (id == "hummingbot-hl")
        payload["coins"] = json::array({coin});
    if (!sql_apply(br, {{"op", "command"}, {"bot_id", id}, {"kind", "close"}, {"payload", payload}}))
        return {{"ok", false}, {"queued", false}, {"error", "sqlite command queue failed"}};
    return {
        {"ok", true}, {"queued", true}, {"id", id}, {"coin", coin},
        {"message", "Close queued for " + coin + " — hunter will flatten that clip"},
    };
}

static json api_home_open_post(const Brain& br, const json& data) {
    std::string id = jstr(data, "id", jstr(data, "bot_id", jstr(data, "bot")));
    std::string coin = jstr(data, "coin");
    for (char& c : coin) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    if (!valid_id(id) || id == "hummingbot-hl")
        return {{"ok", false}, {"queued", false}, {"error", "Copy open stays on /copy"}};
    if (coin.empty())
        return {{"ok", false}, {"queued", false}, {"error", "coin required"}};
    json h;
    for (auto& x : registry(br)) if (jstr(x, "id") == id) h = x;
    std::string bf = basename_only(jstr(h, "bias_file"));
    json cur = bf.empty() ? json::object() : read_json(join(br.conf, bf));
    auto norm = [](std::string s) {
        for (char& c : s) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        return s;
    };
    std::string side = norm(jstr(data, "side"));
    if (side != "LONG" && side != "SHORT") {
        if (cur.is_object() && cur.contains(coin) && cur[coin].is_string())
            side = norm(cur[coin].get<std::string>());
    }
    if (side != "LONG" && side != "SHORT") {
        sql_status_map(br);
        if (g_sql_bias.contains(id) && g_sql_bias[id].is_object()
            && g_sql_bias[id].contains(coin) && g_sql_bias[id][coin].is_string())
            side = norm(g_sql_bias[id][coin].get<std::string>());
    }
    if (side != "LONG" && side != "SHORT")
        return {{"ok", false}, {"queued", false}, {"error", "set " + coin + " BIAS to LONG or SHORT before 100%"}};
    json payload = {{"coin", coin}, {"side", side}, {"pct", 1.0}, {"source", "spring"}};
    if (!sql_apply(br, {{"op", "command"}, {"bot_id", id}, {"kind", "open"}, {"payload", payload}}))
        return {{"ok", false}, {"queued", false}, {"error", "sqlite command queue failed"}};
    return {
        {"ok", true}, {"queued", true}, {"id", id}, {"coin", coin}, {"side", side}, {"pct", 1.0},
        {"message", "100% leftover " + side + " " + coin + " queued"},
    };
}

std::string Brain::handle(const std::string& method, const std::string& path,
                          const std::string& query, const std::string& body, int& status) {
    status = 200;
    json out;
    try {
        if (method == "OPTIONS") {
            out = json::object();
        } else if (method == "POST" && path == "/api/spring/writes") {
            auto d = parse_body(body);
            bool on = jbool(d, "enabled", jbool(d, "writes", jbool(d, "on")));
            writes_set(on);
            out = {
                {"ok", true}, {"writes", writes_on()}, {"closes", false},
                {"positions_untouched", true},
                {"message", on ? "WRITE MODE on — config files only, never CLOSE/OPEN"
                               : "WRITE MODE off — Spring is read-only"},
            };
        } else if (method == "GET" && path == "/api/spring/writes") {
            out = {{"ok", true}, {"writes", writes_on()}, {"closes", false}, {"positions_untouched", true}};
        } else if (method == "POST" && path == "/api/live/knobs") {
            out = api_live_knobs_post(parse_body(body));
        } else if (method == "POST" && path == "/api/spring/stop") {
            spring_request_stop();
            out = {
                {"ok", true}, {"stopped", true}, {"writes", writes_on()}, {"closes", false},
                {"positions_untouched", true},
                {"message", "Spring stopping — Flask :5001 and hunters keep running"},
            };
        } else if (method == "POST" && path == "/api/hunter_gate") {
            // Works with WRITE MODE off. Pause/unpause only. Never a close.
            out = api_hunter_gate_post(*this, parse_body(body));
            if (!out.value("ok", false)) status = 400;
        } else if (method != "GET") {
            if (path == "/api/home/close" || path == "/api/home/open") {
                if (!writes_on()) {
                    status = 403;
                    out = deny_write(method + " " + path);
                } else if (path == "/api/home/close") {
                    out = api_home_close_post(*this, parse_body(body));
                } else {
                    out = api_home_open_post(*this, parse_body(body));
                }
            } else if (touches_positions(path)) {
                status = 403;
                out = deny_close(method + " " + path);
            } else if (!writes_on()) {
                status = 403;
                out = deny_write(method + " " + path);
            } else if (path == "/api/trader_audits/run") {
                out = api_trader_audits_run(*this, parse_body(body));
            } else if (path == "/api/hl_trader_follow") {
                out = api_hl_trader_follow(*this, parse_body(body));
            } else if (path == "/api/hl_trader_slots") {
                out = api_hl_trader_slots(*this, parse_body(body));
            } else if (path == "/api/wire/presets") {
                out = api_presets_post(*this, parse_body(body));
            } else if (path == "/api/wire/sleeper") {
                out = api_sleeper(*this, parse_body(body));
            } else if (path == "/api/wire/controller") {
                out = api_ctrl_post(*this, parse_body(body));
            } else if (path == "/api/wire/bias") {
                out = api_bias_post(*this, parse_body(body));
            } else if (path == "/api/home/sl") {
                out = api_sl_post(*this, parse_body(body));
            } else if (path == "/api/home/restart") {
                out = api_restart_post(parse_body(body));
            } else if (path == "/api/home/desk_bias") {
                out = api_desk_bias_post(*this, parse_body(body));
            } else if (path == "/api/wire/pack") {
                out = api_wire_pack_post(*this, parse_body(body));
            } else if (path == "/api/wire/graph" || path == "/api/wire/board/home"
                       || path == "/api/wire/board/health" || path == "/api/wire/layout") {
                out = api_wire_graph_post(*this, path, parse_body(body));
            } else {
                status = 403;
                out = deny_close(method + " " + path);
            }
        } else if (path == "/api/health" || path == "/") {
            auto stamp = matrix_stamp(*this);
            out = {
                {"ok", true},
                {"service", "spring"},
                {"mode", writes_on() ? "debug-write-files" : "debug-readonly"},
                {"port", port},
                {"conf", conf},
                {"writes", writes_on()},
                {"closes", false},
                {"positions_untouched", true},
                {"stamp", stamp},
                {"hl_key", hl_key_view(conf)},
                {"archy", stamp.value("archy", "")},
                {"archy_deployed", stamp.value("archy_deployed", "")},
                {"note", writes_on()
                    ? "Spring writes ON — hunter files only, never CLOSE/OPEN HL"
                    : "Spring read-only — Flask :5001 is live"},
            };
        } else if (path == "/api/live/knobs") {
            out = api_live_knobs_get(*this, query);
        } else if (path == "/api/wire/presets") {
            out = api_presets_get(*this, query);
        } else if (path == "/api/trader_audits") {
            out = api_trader_audits(*this);
        } else if (path == "/api/home") {
            out = api_home(*this);
        } else if (path == "/api/hunter_gate") {
            json rows = json::array();
            for (auto& h : registry(*this)) {
                if (!h.is_object()) continue;
                std::string id = jstr(h, "id");
                if (!hunter_pausable(id)) continue;
                bool paused = docker_paused(id);
                bool running = docker_running(id);
                rows.push_back({
                    {"id", id},
                    {"name", jstr(h, "name", id)},
                    {"running", running},
                    {"deactivated", paused},
                    {"hl_ip", (paused || !running) ? "none" : "spending"},
                });
            }
            out = {
                {"ok", true},
                {"closes", false},
                {"positions_untouched", true},
                {"hunters", rows},
                {"note", "deactivated = docker pause. 0 Hyperliquid IP. Positions stay. Activate is docker unpause only."},
            };
        } else if (path == "/api/nav") {
            out = api_nav(*this);
        } else if (path == "/api/wire/catalog") {
            out = api_catalog(*this);
        } else if (path == "/api/wire/graph") {
            out = file_or_empty_graph(*this, "wire_graph.json");
        } else if (path == "/api/wire/board/home") {
            auto g = file_or_empty_graph(*this, "wire_home.json");
            out = {{"ok", true}, {"board", "home"}, {"graph", g},
                   {"n", g.value("nodes", json::array()).size()},
                   {"e", g.value("edges", json::array()).size()}};
        } else if (path == "/api/wire/board/health") {
            auto g = file_or_empty_graph(*this, "wire_health.json");
            out = {{"ok", true}, {"board", "health"}, {"graph", g},
                   {"n", g.value("nodes", json::array()).size()},
                   {"e", g.value("edges", json::array()).size()}};
        } else if (path == "/api/condor/gauge") {
            out = read_json(join(conf, "condor_gauge.json"));
            if (!out.is_object()) out = json::object();
            out["ok"] = true;
        } else if (path == "/api/qa/week") {
            out = {
                {"ok", true}, {"qa", true}, {"armed", false},
                {"items", json::array()},
                {"message", "JaneStreet overlay not loaded in Spring"},
                {"ts", utc_now()},
            };
        } else if (path == "/api/backup") {
            out = {
                {"ok", true},
                {"check", {{"ok", false}, {"message", "backup jobs stay on Flask — Spring will not run them"}}},
            };
        } else if (path == "/api/versions") {
            out = api_versions(*this);
        } else if (path == "/api/wire/pack") {
            out = api_wire_pack_get(*this);
        } else {
            status = 404;
            out = {{"ok", false}, {"error", "Spring has no GET " + path}};
        }
    } catch (const std::exception& e) {
        status = 500;
        out = {{"ok", false}, {"error", e.what()}};
    }
    if (out.contains("ok") && out["ok"] == false && status == 200) status = 400;
    try {
        return out.dump(-1, ' ', false, nlohmann::json::error_handler_t::replace);
    } catch (const std::exception& e) {
        status = 500;
        return std::string("{\"ok\":false,\"closes\":false,\"error\":\"json dump failed\"}");
    }
}
