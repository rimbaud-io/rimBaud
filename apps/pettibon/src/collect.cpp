// Copyright 2026 Laurence Sterne www.rimbaud.io
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

#include "collect.hpp"
#include "dman_setup.hpp"
#include "sqlite3_min.h"
#include "store.hpp"

#include <nlohmann/json.hpp>

#include <arpa/inet.h>
#include <fcntl.h>
#include <netdb.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>
#include <vector>

using json = nlohmann::json;

namespace {

std::string utc_now() {
    time_t t = time(nullptr);
    struct tm tm{};
    gmtime_r(&t, &tm);
    char buf[40];
    strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S UTC", &tm);
    return buf;
}

std::string jstr(const json& j, const char* k) {
    if (!j.is_object() || !j.contains(k) || j[k].is_null()) return {};
    try {
        if (j[k].is_string()) return j[k].get<std::string>();
        if (j[k].is_number()) return j[k].dump();
        if (j[k].is_boolean()) return j[k].get<bool>() ? "true" : "false";
    } catch (...) {}
    return {};
}

bool jnum(const json& j, const char* k, double& out) {
    if (!j.is_object() || !j.contains(k) || j[k].is_null()) return false;
    try {
        if (j[k].is_number()) {
            out = j[k].get<double>();
            return true;
        }
        if (j[k].is_string()) {
            out = std::stod(j[k].get<std::string>());
            return true;
        }
    } catch (...) {}
    return false;
}

std::string num_key(double v) {
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%.8g", v);
    return buf;
}

struct Matrix {
    std::string id;
    std::string name;
    std::string url;
};

bool http_get(const std::string& url, std::string& body, std::string& err) {
    if (url.compare(0, 7, "http://") != 0) {
        err = "only http:// springs";
        return false;
    }
    std::string rest = url.substr(7);
    auto slash = rest.find('/');
    std::string hostport = slash == std::string::npos ? rest : rest.substr(0, slash);
    std::string path = slash == std::string::npos ? "/" : rest.substr(slash);
    std::string host = hostport;
    std::string port = "80";
    auto colon = hostport.rfind(':');
    if (colon != std::string::npos) {
        host = hostport.substr(0, colon);
        port = hostport.substr(colon + 1);
    }
    addrinfo hints{};
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_family = AF_UNSPEC;
    addrinfo* res = nullptr;
    if (getaddrinfo(host.c_str(), port.c_str(), &hints, &res) != 0 || !res) {
        err = "dns " + host;
        return false;
    }
    int fd = -1;
    for (auto* ai = res; ai; ai = ai->ai_next) {
        fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (fd < 0) continue;
        int flags = fcntl(fd, F_GETFL, 0);
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
        int rc = connect(fd, ai->ai_addr, ai->ai_addrlen);
        if (rc < 0 && errno != EINPROGRESS) {
            close(fd);
            fd = -1;
            continue;
        }
        pollfd pfd{fd, POLLOUT, 0};
        if (poll(&pfd, 1, 3000) <= 0) {
            close(fd);
            fd = -1;
            continue;
        }
        int soerr = 0;
        socklen_t sl = sizeof(soerr);
        getsockopt(fd, SOL_SOCKET, SO_ERROR, &soerr, &sl);
        if (soerr != 0) {
            close(fd);
            fd = -1;
            continue;
        }
        fcntl(fd, F_SETFL, flags);
        break;
    }
    freeaddrinfo(res);
    if (fd < 0) {
        err = "connect " + hostport;
        return false;
    }
    std::string req = "GET " + path + " HTTP/1.0\r\nHost: " + hostport +
                      "\r\nConnection: close\r\nAccept: application/json\r\n\r\n";
    if (send(fd, req.data(), req.size(), MSG_NOSIGNAL) < 0) {
        close(fd);
        err = "send";
        return false;
    }
    std::string raw;
    raw.reserve(65536);
    char buf[8192];
    while (raw.size() < 2 * 1024 * 1024) {
        pollfd pfd{fd, POLLIN, 0};
        if (poll(&pfd, 1, 3000) <= 0) break;
        ssize_t n = recv(fd, buf, sizeof(buf), 0);
        if (n <= 0) break;
        raw.append(buf, buf + n);
    }
    close(fd);
    auto sep = raw.find("\r\n\r\n");
    if (sep == std::string::npos) {
        err = "short http";
        return false;
    }
    std::string head = raw.substr(0, sep);
    body = raw.substr(sep + 4);
    if (head.find(" 200 ") == std::string::npos && head.find(" 200\r") == std::string::npos) {
        err = head.substr(0, head.find("\r\n"));
        return false;
    }
    return true;
}

json read_json_file(const std::string& path) {
    std::ifstream in(path);
    if (!in) return {};
    try {
        json j;
        in >> j;
        return j;
    } catch (...) {
        return {};
    }
}

std::vector<Matrix> load_matrices(const std::string& conf) {
    std::vector<Matrix> out;
    json cfg = read_json_file(conf + "/pettibon.json");
    if (cfg.is_object() && cfg.contains("matrices") && cfg["matrices"].is_array()) {
        for (auto& m : cfg["matrices"]) {
            if (!m.is_object()) continue;
            Matrix row;
            row.id = jstr(m, "id");
            row.name = jstr(m, "name");
            row.url = jstr(m, "url");
            if (row.id.empty() || row.url.empty()) continue;
            if (row.name.empty()) row.name = row.id;
            out.push_back(row);
        }
    }
    if (!out.empty()) return out;
    out.push_back({"localhost", "mothership", "http://127.0.0.1:5004"});
    json hosts = read_json_file(conf + "/deploy_hosts.json");
    if (hosts.is_object()) {
        for (auto it = hosts.begin(); it != hosts.end(); ++it) {
            if (!it.value().is_object()) continue;
            std::string ssh = jstr(it.value(), "ssh");
            auto at = ssh.rfind('@');
            if (at == std::string::npos || at + 1 >= ssh.size()) continue;
            std::string host = ssh.substr(at + 1);
            Matrix row;
            row.id = it.key();
            row.name = jstr(it.value(), "name");
            if (row.name.empty()) row.name = row.id;
            row.url = "http://" + host + ":5004";
            out.push_back(row);
        }
    }
    return out;
}

int ingest_trades(const std::string& matrix, const std::string& bot, const json& arr,
                  const std::string& source) {
    if (!arr.is_array()) return 0;
    int n = 0;
    std::string err;
    for (auto& t : arr) {
        if (!t.is_object()) continue;
        TradeIn row;
        row.matrix = matrix;
        row.bot = bot;
        row.ts = jstr(t, "ts");
        if (row.ts.empty()) row.ts = jstr(t, "time");
        if (row.ts.empty()) continue;
        row.action = jstr(t, "action");
        for (char& c : row.action) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        row.coin = jstr(t, "coin");
        row.side = jstr(t, "side");
        for (char& c : row.side) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        row.reason = jstr(t, "reason");
        if (row.reason.empty()) row.reason = jstr(t, "result");
        row.source = source;
        row.has_amount = jnum(t, "amount", row.amount);
        if (!row.has_amount) row.has_amount = jnum(t, "size", row.amount);
        row.has_price = jnum(t, "price", row.price);
        row.has_entry = jnum(t, "entry_price", row.entry);
        if (!row.has_entry) row.has_entry = jnum(t, "entry_px", row.entry);
        row.has_pnl = jnum(t, "pnl_usd", row.pnl);
        row.has_roe = jnum(t, "roe_pct", row.roe);
        row.has_lev = jnum(t, "leverage", row.lev);
        row.trade_id = jstr(t, "id");
        if (row.trade_id.empty()) {
            row.trade_id = row.ts + "|" + row.action + "|" + row.coin + "|" + row.side + "|"
                + (row.has_amount ? num_key(row.amount) : "") + "|"
                + (row.has_price ? num_key(row.price) : "");
        }
        if (store_trade(row, err)) n++;
    }
    return n;
}

const std::map<std::string, std::string> kTradeFiles = {
    {"dman_maker_trades.json", "hummingbot-dman-maker"},
    {"dman_trades.json", "hummingbot-dman"},
    {"onlyup_trades.json", "hummingbot-onlyup"},
    {"surfn_trades.json", "hummingbot-surfn"},
    {"shortn_trades.json", "hummingbot-shortn"},
    {"helliot_trades.json", "hummingbot-helliot"},
    {"bn_swing_trades.json", "hummingbot-bn-swing"},
    {"hl_copy_pnl.json", "hummingbot-hl"},
};

int ingest_local_files(const std::string& conf) {
    int n = 0;
    for (auto& kv : kTradeFiles) {
        json doc = read_json_file(conf + "/" + kv.first);
        if (!doc.is_object()) continue;
        if (doc.contains("trades")) n += ingest_trades("localhost", kv.second, doc["trades"], "file");
        if (doc.contains("last_closes"))
            n += ingest_trades("localhost", kv.second, doc["last_closes"], "file");
    }
    return n;
}

// Read-only. The live command queue stays in matrix.db; Pettibon copies rows out.
int ingest_commands(const std::string& conf) {
    std::string path = "file:" + conf + "/matrix.db?mode=ro";
    sqlite3* db = nullptr;
    int flags = SQLITE_OPEN_READONLY | SQLITE_OPEN_URI;
    if (sqlite3_open_v2(path.c_str(), &db, flags, nullptr) != SQLITE_OK) {
        if (db) sqlite3_close(db);
        return 0;
    }
    sqlite3_busy_timeout(db, 1500);
    long long last = 0;
    try { last = std::stoll(store_meta("last_command_id")); } catch (...) { last = 0; }
    sqlite3_stmt* st = nullptr;
    const char* sql =
        "SELECT id, bot_id, kind, payload_json, status, created_at FROM commands WHERE id > ? ORDER BY id";
    if (sqlite3_prepare_v2(db, sql, -1, &st, nullptr) != SQLITE_OK) {
        sqlite3_close(db);
        return 0;
    }
    sqlite3_bind_int64(st, 1, last);
    int n = 0;
    long long max_id = last;
    std::string err;
    while (sqlite3_step(st) == SQLITE_ROW) {
        long long id = sqlite3_column_int64(st, 0);
        auto text = [](sqlite3_stmt* s, int i) -> std::string {
            auto p = sqlite3_column_text(s, i);
            return p ? reinterpret_cast<const char*>(p) : "";
        };
        std::string bot = text(st, 1);
        std::string kind = text(st, 2);
        std::string payload = text(st, 3);
        std::string status = text(st, 4);
        std::string ts = text(st, 5);
        if (payload.find("secret") != std::string::npos || payload.find("private") != std::string::npos)
            payload.clear();
        if (payload.size() > 2000) payload.resize(2000);
        ActionIn a;
        a.matrix = "localhost";
        a.bot = bot;
        a.action_key = "cmd:" + std::to_string(id);
        a.ts = ts.empty() ? utc_now() : ts;
        a.kind = kind.empty() ? "command" : kind;
        a.summary = status;
        a.payload = payload;
        if (store_action(a, err)) n++;
        if (id > max_id) max_id = id;
    }
    sqlite3_finalize(st);
    sqlite3_close(db);
    if (max_id > last) store_meta_set("last_command_id", std::to_string(max_id), err);
    return n;
}

struct Added { int trades = 0; int actions = 0; };

Added ingest_home(const Matrix& m, const json& home) {
    Added n;
    std::string now = utc_now();
    std::string err;
    json bots = home.value("bots", json::array());
    if (!bots.is_array()) return n;
    for (auto& b : bots) {
        if (!b.is_object()) continue;
        std::string id = jstr(b, "id");
        if (id.empty()) continue;
        n.trades += ingest_trades(m.id, id, b.value("trades", json::array()), "home");
        if (b.contains("bias") && b["bias"].is_object()) {
            for (auto it = b["bias"].begin(); it != b["bias"].end(); ++it) {
                if (!it.value().is_string()) continue;
                std::string mode = it.value().get<std::string>();
                ActionIn a;
                a.matrix = m.id;
                a.bot = id;
                a.action_key = "bias:" + it.key() + ":" + mode;
                a.ts = now;
                a.kind = "bias";
                a.summary = it.key() + " " + mode;
                if (store_action(a, err)) n.actions++;
            }
        }
        std::string health = jstr(b, "health");
        std::string run = b.value("running", false) ? "true" : "false";
        ActionIn h;
        h.matrix = m.id;
        h.bot = id;
        h.action_key = "health:" + health + ":" + run;
        h.ts = now;
        h.kind = "health";
        h.summary = health + " running=" + run;
        if (store_action(h, err)) n.actions++;
    }
    json positions = home.value("positions", json::array());
    if (positions.is_array()) {
        for (auto& p : positions) {
            if (!p.is_object()) continue;
            std::string bot = jstr(p, "bot_id");
            if (bot.empty()) bot = jstr(p, "bot");
            std::string coin = jstr(p, "coin");
            std::string side = jstr(p, "side");
            double sz = 0, entry = 0;
            bool has_sz = jnum(p, "size", sz);
            if (!has_sz) has_sz = jnum(p, "amount", sz);
            jnum(p, "entry_px", entry);
            if (!entry) jnum(p, "entry_price", entry);
            if (bot.empty() || coin.empty() || !has_sz) continue;
            ActionIn a;
            a.matrix = m.id;
            a.bot = bot;
            a.action_key = "pos:" + coin + ":" + side + ":" + num_key(sz) + ":" + num_key(entry);
            a.ts = now;
            a.kind = "position";
            a.summary = side + " " + coin + " " + num_key(sz) + " @ " + num_key(entry);
            if (store_action(a, err)) n.actions++;
        }
    }
    return n;
}

}  // namespace

PassResult collect_once(const std::string& conf) {
    PassResult r;
    std::string now = utc_now();
    std::string err;
    r.trades_new += ingest_local_files(conf);
    r.actions_new += ingest_commands(conf);
    for (auto& m : load_matrices(conf)) {
        std::string body, herr;
        bool up = http_get(m.url + "/api/home", body, herr);
        int was = store_matrix_online(m.id);
        store_matrix(m.id, m.name, m.url, up, now, err);
        if (was < 0 || was != (up ? 1 : 0)) {
            ActionIn a;
            a.matrix = m.id;
            a.bot = "";
            a.action_key = std::string("presence:") + (up ? "up:" : "down:") + now;
            a.ts = now;
            a.kind = "presence";
            a.summary = up ? "online" : ("offline " + herr);
            if (store_action(a, err)) r.actions_new++;
        }
        if (!up) {
            r.matrices_down++;
            if (!r.note.empty()) r.note += "; ";
            r.note += m.name + " offline";
            continue;
        }
        r.matrices_up++;
        json home;
        try { home = json::parse(body); }
        catch (...) {
            if (!r.note.empty()) r.note += "; ";
            r.note += m.name + " bad json";
            continue;
        }
        Added got = ingest_home(m, home);
        r.trades_new += got.trades;
        r.actions_new += got.actions;
    }
    int priced = dman_observe(conf);
    int scored = dman_score();
    if (priced || scored) {
        if (!r.note.empty()) r.note += "; ";
        r.note += "dman setup +" + std::to_string(priced) + " replay " + std::to_string(scored);
    }
    return r;
}
