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

#include "matrix_sql.hpp"
#include "jsonio.hpp"

#include "sqlite3_min.h"

#include <cctype>
#include <iostream>
#include <string>
#include <sys/stat.h>

using nlohmann::json;

static const char* kHunters[] = {
    "hummingbot-hl",
    "hummingbot-bn-swing",
    "hummingbot-helliot",
    "hummingbot-surfn",
    "hummingbot-shortn",
    "hummingbot-dman",
    "hummingbot-onlyup",
    "hummingbot-dman-maker",
};

static bool known_hunter(const std::string& id) {
    for (const char* h : kHunters)
        if (id == h) return true;
    return false;
}

static std::string clip(std::string s, size_t n) {
    if (s.size() > n) s.resize(n);
    return s;
}

static std::string upper_copy(std::string s) {
    for (char& c : s)
        c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    return s;
}

static std::string lower_copy(std::string s) {
    for (char& c : s)
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}

static void relax_mode(const std::string& path) {
    ::chmod(path.c_str(), 0666);
}

struct Db {
    sqlite3* db = nullptr;
    explicit Db(const std::string& path) {
        int rc = sqlite3_open_v2(path.c_str(), &db, SQLITE_OPEN_READWRITE, nullptr);
        if (rc != SQLITE_OK) {
            if (db) sqlite3_close(db);
            db = nullptr;
            return;
        }
        sqlite3_busy_timeout(db, 4000);
        sqlite3_exec(db, "PRAGMA busy_timeout=4000;", nullptr, nullptr, nullptr);
        relax_mode(path);
        relax_mode(path + "-wal");
        relax_mode(path + "-shm");
    }
    ~Db() {
        if (db) sqlite3_close(db);
    }
    explicit operator bool() const { return db != nullptr; }
};

static std::string db_path(const std::string& conf) {
    if (!conf.empty() && conf.back() == '/') return conf + "matrix.db";
    return conf + "/matrix.db";
}

static bool bind_text(sqlite3_stmt* st, int i, const std::string& s) {
    return sqlite3_bind_text(st, i, s.c_str(), static_cast<int>(s.size()), SQLITE_TRANSIENT) == SQLITE_OK;
}

static bool step_done(sqlite3_stmt* st) {
    int rc = sqlite3_step(st);
    return rc == SQLITE_DONE || rc == SQLITE_ROW;
}

static bool kv_set(sqlite3* db, const std::string& bot, const std::string& key, const json& value) {
    std::string b = clip(bot, 80);
    std::string k = clip(key, 80);
    if (b.empty() || k.empty() || k == "updated" || k == "source") return true;
    const char* sql =
        "INSERT INTO kv(bot_id, key, value_json, source, updated_at) "
        "VALUES (?, ?, ?, 'spring', ?) "
        "ON CONFLICT(bot_id, key) DO UPDATE SET "
        "value_json=excluded.value_json, source=excluded.source, updated_at=excluded.updated_at";
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &st, nullptr) != SQLITE_OK) return false;
    std::string body = value.dump();
    std::string ts = utc_now();
    bool ok = bind_text(st, 1, b) && bind_text(st, 2, k) && bind_text(st, 3, body) && bind_text(st, 4, ts)
        && step_done(st);
    sqlite3_finalize(st);
    return ok;
}

static bool bias_set(sqlite3* db, const std::string& bot, const std::string& coin, const std::string& mode_in) {
    std::string b = clip(bot, 80);
    std::string c = clip(upper_copy(coin), 12);
    std::string m = clip(upper_copy(mode_in), 8);
    if (b.empty() || c.empty()) return false;
    if (m != "LONG" && m != "SHORT" && m != "FLAT") return false;
    const char* sql =
        "INSERT INTO bias(bot_id, coin, mode, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(bot_id, coin) DO UPDATE SET mode=excluded.mode, updated_at=excluded.updated_at";
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &st, nullptr) != SQLITE_OK) return false;
    std::string ts = utc_now();
    bool ok = bind_text(st, 1, b) && bind_text(st, 2, c) && bind_text(st, 3, m) && bind_text(st, 4, ts)
        && step_done(st);
    sqlite3_finalize(st);
    return ok;
}

static bool command_enqueue(sqlite3* db, const std::string& bot, const std::string& kind, json payload) {
    std::string b = clip(bot, 80);
    std::string k = clip(lower_copy(kind), 40);
    // trim
    while (!k.empty() && std::isspace(static_cast<unsigned char>(k.front()))) k.erase(k.begin());
    while (!k.empty() && std::isspace(static_cast<unsigned char>(k.back()))) k.pop_back();
    if (b.empty() || k.empty()) return false;
    if (!payload.is_object()) payload = json::object();
    if (!payload.contains("source")) payload["source"] = "spring";
    const char* sql =
        "INSERT INTO commands(bot_id, kind, payload_json, status, created_at) "
        "VALUES (?, ?, ?, 'pending', ?)";
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(db, sql, -1, &st, nullptr) != SQLITE_OK) return false;
    std::string body = payload.dump();
    std::string ts = utc_now();
    bool ok = bind_text(st, 1, b) && bind_text(st, 2, k) && bind_text(st, 3, body) && bind_text(st, 4, ts)
        && sqlite3_step(st) == SQLITE_DONE;
    sqlite3_finalize(st);
    return ok;
}

static bool apply_job(sqlite3* db, const std::string& conf, const json& job) {
    std::string op;
    if (job.contains("op") && job["op"].is_string()) op = job["op"].get<std::string>();
    std::string bot;
    if (job.contains("bot_id") && job["bot_id"].is_string()) bot = job["bot_id"].get<std::string>();

    if (op == "command") {
        std::string kind;
        if (job.contains("kind") && job["kind"].is_string()) kind = job["kind"].get<std::string>();
        json payload = job.contains("payload") ? job["payload"] : json::object();
        return command_enqueue(db, bot, kind, payload);
    }
    if (op == "bias") {
        json mapping = job.contains("mapping") && job["mapping"].is_object() ? job["mapping"] : json::object();
        for (auto it = mapping.begin(); it != mapping.end(); ++it) {
            std::string key = it.key();
            std::string low = lower_copy(key);
            if (low == "updated" || low == "source" || low == "coins" || low == "bias") continue;
            if (!it.value().is_string()) continue;
            std::string mode = upper_copy(it.value().get<std::string>());
            if (mode != "LONG" && mode != "SHORT" && mode != "FLAT") continue;
            if (!bias_set(db, bot, key, mode)) return false;
        }
        return true;
    }
    if (op == "kv") {
        json values = job.contains("values") && job["values"].is_object() ? job["values"] : json::object();
        for (auto it = values.begin(); it != values.end(); ++it) {
            if (it.key() == "updated" || it.key() == "source") continue;
            if (!kv_set(db, bot, it.key(), it.value())) return false;
        }
        return true;
    }
    if (op == "hl_runtime") {
        std::string path = db_path(conf);
        auto slash = path.find_last_of('/');
        std::string dir = slash == std::string::npos ? "." : path.substr(0, slash);
        std::string rt_path = dir + "/hl_copy_runtime.json";
        json prev = read_json(rt_path);
        if (!prev.is_object()) prev = json::object();
        json updates = job.contains("updates") && job["updates"].is_object() ? job["updates"] : json::object();
        for (auto it = updates.begin(); it != updates.end(); ++it)
            prev[it.key()] = it.value();
        if (!write_json(rt_path, prev)) return false;
        for (auto it = updates.begin(); it != updates.end(); ++it) {
            if (it.key() == "ts" || it.key() == "updated" || it.key() == "source") continue;
            if (!kv_set(db, "hummingbot-hl", it.key(), it.value())) return false;
        }
        return true;
    }
    return false;
}

bool matrix_sql_apply(const std::string& conf, const json& job) {
    Db db(db_path(conf));
    if (!db) {
        std::cerr << "spring: sqlite open failed for write\n";
        return false;
    }
    char* err = nullptr;
    if (sqlite3_exec(db.db, "BEGIN IMMEDIATE", nullptr, nullptr, &err) != SQLITE_OK) {
        if (err) sqlite3_free(err);
        std::cerr << "spring: sqlite busy\n";
        return false;
    }
    bool ok = false;
    try {
        ok = apply_job(db.db, conf, job);
    } catch (const std::exception& e) {
        std::cerr << "spring: sqlite apply: " << e.what() << "\n";
        ok = false;
    }
    sqlite3_exec(db.db, ok ? "COMMIT" : "ROLLBACK", nullptr, nullptr, nullptr);
    return ok;
}

bool matrix_sql_snapshot(const std::string& conf, json& status, json& bias) {
    Db db(db_path(conf));
    if (!db) return false;
    status = json::object();
    bias = json::object();
    sqlite3_stmt* st = nullptr;
    const char* q =
        "SELECT bot_id, payload_json FROM status";
    if (sqlite3_prepare_v2(db.db, q, -1, &st, nullptr) != SQLITE_OK) return false;
    while (sqlite3_step(st) == SQLITE_ROW) {
        const char* idc = reinterpret_cast<const char*>(sqlite3_column_text(st, 0));
        const char* body = reinterpret_cast<const char*>(sqlite3_column_text(st, 1));
        if (!idc || !body) continue;
        std::string id = idc;
        if (!known_hunter(id)) continue;
        try {
            json row = json::parse(body);
            if (row.is_object() && !row.empty()) status[id] = row;
        } catch (...) {
        }
    }
    sqlite3_finalize(st);
    st = nullptr;
    if (sqlite3_prepare_v2(db.db, "SELECT bot_id, coin, mode FROM bias", -1, &st, nullptr) != SQLITE_OK)
        return false;
    while (sqlite3_step(st) == SQLITE_ROW) {
        const char* idc = reinterpret_cast<const char*>(sqlite3_column_text(st, 0));
        const char* coin = reinterpret_cast<const char*>(sqlite3_column_text(st, 1));
        const char* mode = reinterpret_cast<const char*>(sqlite3_column_text(st, 2));
        if (!idc || !coin || !mode) continue;
        std::string id = idc;
        if (!known_hunter(id)) continue;
        if (!bias.contains(id) || !bias[id].is_object()) bias[id] = json::object();
        bias[id][coin] = mode;
    }
    sqlite3_finalize(st);
    return true;
}

bool matrix_sql_kv_map(const std::string& conf, const std::string& bot, json& out) {
    out = json::object();
    if (bot.empty()) return false;
    Db db(db_path(conf));
    if (!db) return false;
    sqlite3_stmt* st = nullptr;
    const char* q = "SELECT key, value_json FROM kv WHERE bot_id=?";
    if (sqlite3_prepare_v2(db.db, q, -1, &st, nullptr) != SQLITE_OK) return false;
    if (!bind_text(st, 1, bot)) {
        sqlite3_finalize(st);
        return false;
    }
    while (sqlite3_step(st) == SQLITE_ROW) {
        const char* key = reinterpret_cast<const char*>(sqlite3_column_text(st, 0));
        const char* body = reinterpret_cast<const char*>(sqlite3_column_text(st, 1));
        if (!key || !body) continue;
        std::string k = key;
        if (k == "updated" || k == "source") continue;
        try {
            out[k] = json::parse(body);
        } catch (...) {
        }
    }
    sqlite3_finalize(st);
    return true;
}

static std::string squeeze_name(std::string s) {
    std::string o;
    bool gap = false;
    for (char c : s) {
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
            if (!o.empty()) gap = true;
            continue;
        }
        if (gap) {
            o.push_back(' ');
            gap = false;
        }
        o.push_back(c);
    }
    if (o.size() > 32) o.resize(32);
    return o;
}

bool matrix_sql_preset_list(const std::string& conf, const std::string& bot, json& list) {
    list = json::array();
    if (bot.empty()) return false;
    Db db(db_path(conf));
    if (!db) return false;
    sqlite3_stmt* st = nullptr;
    const char* q =
        "SELECT name, values_json, locked, updated_at FROM controller_preset "
        "WHERE bot_id=? ORDER BY locked DESC, name COLLATE NOCASE";
    if (sqlite3_prepare_v2(db.db, q, -1, &st, nullptr) != SQLITE_OK) return false;
    if (!bind_text(st, 1, bot)) {
        sqlite3_finalize(st);
        return false;
    }
    while (sqlite3_step(st) == SQLITE_ROW) {
        const char* name = reinterpret_cast<const char*>(sqlite3_column_text(st, 0));
        const char* body = reinterpret_cast<const char*>(sqlite3_column_text(st, 1));
        int locked = sqlite3_column_int(st, 2);
        const char* updated = reinterpret_cast<const char*>(sqlite3_column_text(st, 3));
        json values = json::object();
        if (body) {
            try {
                json parsed = json::parse(body);
                if (parsed.is_object()) values = parsed;
            } catch (...) {
            }
        }
        list.push_back({
            {"name", name ? name : ""},
            {"locked", locked != 0},
            {"updated", updated ? updated : ""},
            {"values", values},
        });
    }
    sqlite3_finalize(st);
    return true;
}

bool matrix_sql_preset_get(const std::string& conf, const std::string& bot, const std::string& name,
                           json& values, bool& found) {
    values = json::object();
    found = false;
    std::string want = squeeze_name(name);
    if (bot.empty() || want.empty()) return false;
    Db db(db_path(conf));
    if (!db) return false;
    sqlite3_stmt* st = nullptr;
    const char* q = "SELECT values_json FROM controller_preset WHERE bot_id=? AND name=?";
    if (sqlite3_prepare_v2(db.db, q, -1, &st, nullptr) != SQLITE_OK) return false;
    if (!bind_text(st, 1, bot) || !bind_text(st, 2, want)) {
        sqlite3_finalize(st);
        return false;
    }
    if (sqlite3_step(st) == SQLITE_ROW) {
        const char* body = reinterpret_cast<const char*>(sqlite3_column_text(st, 0));
        found = true;
        if (body) {
            try {
                json parsed = json::parse(body);
                if (parsed.is_object()) values = parsed;
            } catch (...) {
            }
        }
    }
    sqlite3_finalize(st);
    return true;
}

bool matrix_sql_preset_save(const std::string& conf, const std::string& bot, const std::string& name,
                            const json& values, bool locked, std::string& err) {
    err.clear();
    std::string id = bot;
    std::string label = squeeze_name(name);
    if (id.empty() || label.empty()) {
        err = "bot_id and name required";
        return false;
    }
    std::string low = lower_copy(label);
    if (low == "std" || low == "standard") {
        if (!locked) {
            err = "STD is locked — pick another name";
            return false;
        }
        label = "STD";
        locked = true;
    }
    Db db(db_path(conf));
    if (!db) {
        err = "sqlite did not open";
        return false;
    }
    sqlite3_stmt* st = nullptr;
    const char* look = "SELECT locked FROM controller_preset WHERE bot_id=? AND name=?";
    if (sqlite3_prepare_v2(db.db, look, -1, &st, nullptr) != SQLITE_OK) {
        err = "preset read failed";
        return false;
    }
    if (!bind_text(st, 1, id) || !bind_text(st, 2, label)) {
        sqlite3_finalize(st);
        err = "preset read failed";
        return false;
    }
    if (sqlite3_step(st) == SQLITE_ROW && sqlite3_column_int(st, 0) != 0 && !locked) {
        sqlite3_finalize(st);
        err = "cannot overwrite locked preset " + label;
        return false;
    }
    sqlite3_finalize(st);
    st = nullptr;
    const char* sql =
        "INSERT INTO controller_preset(bot_id, name, values_json, locked, updated_at) "
        "VALUES(?, ?, ?, ?, ?) "
        "ON CONFLICT(bot_id, name) DO UPDATE SET "
        "values_json=excluded.values_json, locked=excluded.locked, updated_at=excluded.updated_at";
    if (sqlite3_prepare_v2(db.db, sql, -1, &st, nullptr) != SQLITE_OK) {
        err = "preset save failed";
        return false;
    }
    json body = values.is_object() ? values : json::object();
    std::string dumped = body.dump();
    std::string ts = utc_now();
    bool ok = bind_text(st, 1, id) && bind_text(st, 2, label) && bind_text(st, 3, dumped)
        && sqlite3_bind_int(st, 4, locked ? 1 : 0) == SQLITE_OK
        && bind_text(st, 5, ts) && step_done(st);
    sqlite3_finalize(st);
    if (!ok) err = "preset save failed";
    return ok;
}
