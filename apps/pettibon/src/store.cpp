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

#include "store.hpp"
#include "sqlite3_min.h"

#include <iostream>
#include <sstream>

namespace {

sqlite3* g_db = nullptr;

bool exec_sql(const char* sql, std::string& err) {
    char* msg = nullptr;
    int rc = sqlite3_exec(g_db, sql, nullptr, nullptr, &msg);
    if (rc != SQLITE_OK) {
        err = msg ? msg : "sqlite exec failed";
        sqlite3_free(msg);
        return false;
    }
    return true;
}

void bind_text(sqlite3_stmt* st, int i, const std::string& s) {
    sqlite3_bind_text(st, i, s.c_str(), -1, SQLITE_TRANSIENT);
}

void bind_opt(sqlite3_stmt* st, int i, bool has, double v) {
    if (has) sqlite3_bind_double(st, i, v);
    else sqlite3_bind_null(st, i);
}

}  // namespace

bool store_open(const std::string& path, std::string& err) {
    if (g_db) return true;
    int flags = SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE;
    if (sqlite3_open_v2(path.c_str(), &g_db, flags, nullptr) != SQLITE_OK) {
        err = g_db ? sqlite3_errmsg(g_db) : "sqlite open failed";
        if (g_db) sqlite3_close(g_db);
        g_db = nullptr;
        return false;
    }
    sqlite3_busy_timeout(g_db, 4000);
    const char* schema =
        "PRAGMA journal_mode=WAL;"
        "PRAGMA synchronous=NORMAL;"
        "PRAGMA busy_timeout=4000;"
        "CREATE TABLE IF NOT EXISTS meta ("
        "  k TEXT PRIMARY KEY,"
        "  v TEXT NOT NULL"
        ");"
        "CREATE TABLE IF NOT EXISTS matrices ("
        "  id TEXT PRIMARY KEY,"
        "  name TEXT NOT NULL,"
        "  url TEXT NOT NULL,"
        "  online INTEGER NOT NULL,"
        "  seen_at TEXT NOT NULL"
        ");"
        "CREATE TABLE IF NOT EXISTS trades ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  matrix_id TEXT NOT NULL,"
        "  bot_id TEXT NOT NULL,"
        "  trade_id TEXT NOT NULL,"
        "  ts TEXT NOT NULL,"
        "  recorded_at TEXT NOT NULL,"
        "  action TEXT,"
        "  coin TEXT,"
        "  side TEXT,"
        "  amount REAL,"
        "  price REAL,"
        "  entry_price REAL,"
        "  pnl_usd REAL,"
        "  roe_pct REAL,"
        "  reason TEXT,"
        "  source TEXT,"
        "  UNIQUE(matrix_id, bot_id, trade_id)"
        ");"
        "CREATE INDEX IF NOT EXISTS trades_ts ON trades(ts);"
        "CREATE INDEX IF NOT EXISTS trades_bot ON trades(matrix_id, bot_id, ts);"
        "CREATE TABLE IF NOT EXISTS actions ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  matrix_id TEXT NOT NULL,"
        "  bot_id TEXT NOT NULL,"
        "  action_key TEXT NOT NULL,"
        "  ts TEXT NOT NULL,"
        "  recorded_at TEXT NOT NULL,"
        "  kind TEXT NOT NULL,"
        "  summary TEXT,"
        "  payload_json TEXT,"
        "  UNIQUE(matrix_id, action_key)"
        ");"
        "CREATE INDEX IF NOT EXISTS actions_ts ON actions(ts);"
        "CREATE TABLE IF NOT EXISTS prices ("
        "  matrix_id TEXT NOT NULL,"
        "  bot_id TEXT NOT NULL,"
        "  coin TEXT NOT NULL,"
        "  ts TEXT NOT NULL,"
        "  unix_s INTEGER NOT NULL,"
        "  mid REAL NOT NULL,"
        "  hi REAL,"
        "  lo REAL,"
        "  src TEXT NOT NULL,"
        "  PRIMARY KEY (matrix_id, bot_id, coin, unix_s)"
        ");"
        "CREATE INDEX IF NOT EXISTS prices_bot_time ON prices(bot_id, unix_s);"
        "CREATE TABLE IF NOT EXISTS ctrl_snaps ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  matrix_id TEXT NOT NULL,"
        "  bot_id TEXT NOT NULL,"
        "  ts TEXT NOT NULL,"
        "  unix_s INTEGER NOT NULL,"
        "  fingerprint TEXT NOT NULL,"
        "  take_profit REAL,"
        "  trail_activation REAL,"
        "  trail_delta REAL,"
        "  sl_roe_pct REAL,"
        "  time_limit_s INTEGER,"
        "  leverage_tier TEXT,"
        "  leverage_json TEXT,"
        "  bias_json TEXT,"
        "  dont_go_crazy INTEGER,"
        "  after_loss_no_rebuy INTEGER"
        ");"
        "CREATE INDEX IF NOT EXISTS ctrl_snaps_bot ON ctrl_snaps(bot_id, unix_s);"
        "CREATE TABLE IF NOT EXISTS setup_replay ("
        "  bot_id TEXT NOT NULL,"
        "  trade_id TEXT NOT NULL,"
        "  variant TEXT NOT NULL,"
        "  coin TEXT NOT NULL,"
        "  side TEXT,"
        "  entry_ts TEXT,"
        "  unix_s INTEGER NOT NULL,"
        "  exit_unix INTEGER,"
        "  tp REAL,"
        "  trail_act REAL,"
        "  trail_delta REAL,"
        "  sl REAL,"
        "  pnl_usd REAL,"
        "  roe_pct REAL,"
        "  exit_why TEXT,"
        "  actual INTEGER NOT NULL,"
        "  PRIMARY KEY (bot_id, trade_id, variant)"
        ");"
        "CREATE INDEX IF NOT EXISTS setup_replay_time ON setup_replay(bot_id, unix_s);"
        "DROP VIEW IF EXISTS trade_closes;"
        "CREATE VIEW trade_closes AS "
        "SELECT matrix_id, "
        " CASE matrix_id WHEN 'localhost' THEN 'mothership' "
        "  WHEN 'usb-stick' THEN 'MARLY' ELSE matrix_id END AS matrix, "
        " bot_id, REPLACE(bot_id, 'hummingbot-', '') AS hunter, "
        " ts, CAST(strftime('%s', replace(ts, ' UTC', '')) AS INTEGER) AS unix_s, "
        " coin, side, amount, price, entry_price, pnl_usd, roe_pct, reason, "
        " CASE WHEN pnl_usd > 0 THEN 'WIN' WHEN pnl_usd < 0 THEN 'LOSS' ELSE 'UNKNOWN' END AS result "
        "FROM trades WHERE upper(COALESCE(action, '')) = 'CLOSE';";
    if (!exec_sql(schema, err)) {
        sqlite3_close(g_db);
        g_db = nullptr;
        return false;
    }
    // The view above omits leverage so an older database can open. Add the
    // column, then create the view that Grafana reads.
    std::string alter_err;
    exec_sql("ALTER TABLE trades ADD COLUMN leverage REAL", alter_err);
    const char* view =
        "DROP VIEW IF EXISTS trade_closes;"
        "CREATE VIEW trade_closes AS "
        "SELECT matrix_id, "
        " CASE matrix_id WHEN 'localhost' THEN 'mothership' "
        "  WHEN 'usb-stick' THEN 'MARLY' ELSE matrix_id END AS matrix, "
        " bot_id, REPLACE(bot_id, 'hummingbot-', '') AS hunter, "
        " ts, CAST(strftime('%s', replace(ts, ' UTC', '')) AS INTEGER) AS unix_s, "
        " coin, side, amount, price, entry_price, pnl_usd, roe_pct, leverage, reason, "
        " CASE WHEN pnl_usd > 0 THEN 'WIN' WHEN pnl_usd < 0 THEN 'LOSS' ELSE 'UNKNOWN' END AS result "
        "FROM trades WHERE upper(COALESCE(action, '')) = 'CLOSE';";
    if (!exec_sql(view, err)) {
        sqlite3_close(g_db);
        g_db = nullptr;
        return false;
    }
    return true;
}

void store_close() {
    if (g_db) sqlite3_close(g_db);
    g_db = nullptr;
}

bool store_ready() { return g_db != nullptr; }

bool store_trade(const TradeIn& t, std::string& err) {
    const char* sql =
        "INSERT INTO trades (matrix_id, bot_id, trade_id, ts, recorded_at, action, coin, side,"
        " amount, price, entry_price, pnl_usd, roe_pct, leverage, reason, source)"
        " VALUES (?,?,?,?, datetime('now'), ?,?,?, ?,?,?,?,?,?,?,?)"
        " ON CONFLICT(matrix_id, bot_id, trade_id) DO UPDATE SET"
        "  pnl_usd = COALESCE(trades.pnl_usd, excluded.pnl_usd),"
        "  roe_pct = COALESCE(trades.roe_pct, excluded.roe_pct),"
        "  price = COALESCE(trades.price, excluded.price),"
        "  entry_price = COALESCE(trades.entry_price, excluded.entry_price),"
        "  amount = COALESCE(trades.amount, excluded.amount),"
        "  leverage = COALESCE(trades.leverage, excluded.leverage),"
        "  reason = CASE WHEN trades.reason IS NULL OR trades.reason = '' THEN excluded.reason ELSE trades.reason END"
        " WHERE (trades.pnl_usd IS NULL AND excluded.pnl_usd IS NOT NULL)"
        "    OR (trades.roe_pct IS NULL AND excluded.roe_pct IS NOT NULL)"
        "    OR (trades.price IS NULL AND excluded.price IS NOT NULL)"
        "    OR (trades.entry_price IS NULL AND excluded.entry_price IS NOT NULL)"
        "    OR (trades.amount IS NULL AND excluded.amount IS NOT NULL)"
        "    OR (trades.leverage IS NULL AND excluded.leverage IS NOT NULL)"
        "    OR ((trades.reason IS NULL OR trades.reason = '')"
        "        AND excluded.reason IS NOT NULL AND excluded.reason != '')";
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(g_db, sql, -1, &st, nullptr) != SQLITE_OK) {
        err = sqlite3_errmsg(g_db);
        return false;
    }
    bind_text(st, 1, t.matrix);
    bind_text(st, 2, t.bot);
    bind_text(st, 3, t.trade_id);
    bind_text(st, 4, t.ts);
    bind_text(st, 5, t.action);
    bind_text(st, 6, t.coin);
    bind_text(st, 7, t.side);
    bind_opt(st, 8, t.has_amount, t.amount);
    bind_opt(st, 9, t.has_price, t.price);
    bind_opt(st, 10, t.has_entry, t.entry);
    bind_opt(st, 11, t.has_pnl, t.pnl);
    bind_opt(st, 12, t.has_roe, t.roe);
    bind_opt(st, 13, t.has_lev, t.lev);
    bind_text(st, 14, t.reason);
    bind_text(st, 15, t.source);
    int rc = sqlite3_step(st);
    sqlite3_finalize(st);
    if (rc != SQLITE_DONE) {
        err = sqlite3_errmsg(g_db);
        return false;
    }
    return sqlite3_changes(g_db) > 0;
}

bool store_action(const ActionIn& a, std::string& err) {
    const char* sql =
        "INSERT INTO actions (matrix_id, bot_id, action_key, ts, recorded_at, kind, summary, payload_json)"
        " VALUES (?,?,?,?, datetime('now'), ?,?,?)"
        " ON CONFLICT(matrix_id, action_key) DO NOTHING";
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(g_db, sql, -1, &st, nullptr) != SQLITE_OK) {
        err = sqlite3_errmsg(g_db);
        return false;
    }
    bind_text(st, 1, a.matrix);
    bind_text(st, 2, a.bot);
    bind_text(st, 3, a.action_key);
    bind_text(st, 4, a.ts);
    bind_text(st, 5, a.kind);
    bind_text(st, 6, a.summary);
    bind_text(st, 7, a.payload);
    int rc = sqlite3_step(st);
    sqlite3_finalize(st);
    if (rc != SQLITE_DONE) {
        err = sqlite3_errmsg(g_db);
        return false;
    }
    return sqlite3_changes(g_db) > 0;
}

int store_matrix_online(const std::string& id) {
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(g_db, "SELECT online FROM matrices WHERE id=?", -1, &st, nullptr) != SQLITE_OK)
        return -1;
    bind_text(st, 1, id);
    int out = -1;
    if (sqlite3_step(st) == SQLITE_ROW) out = sqlite3_column_int(st, 0) ? 1 : 0;
    sqlite3_finalize(st);
    return out;
}

bool store_matrix(const std::string& id, const std::string& name, const std::string& url,
                  bool online, const std::string& ts, std::string& err) {
    const char* sql =
        "INSERT INTO matrices (id, name, url, online, seen_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT(id) DO UPDATE SET name=excluded.name, url=excluded.url,"
        " online=excluded.online, seen_at=excluded.seen_at";
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(g_db, sql, -1, &st, nullptr) != SQLITE_OK) {
        err = sqlite3_errmsg(g_db);
        return false;
    }
    bind_text(st, 1, id);
    bind_text(st, 2, name);
    bind_text(st, 3, url);
    sqlite3_bind_int64(st, 4, online ? 1 : 0);
    bind_text(st, 5, ts);
    int rc = sqlite3_step(st);
    sqlite3_finalize(st);
    if (rc != SQLITE_DONE) {
        err = sqlite3_errmsg(g_db);
        return false;
    }
    return true;
}

std::string store_meta(const std::string& key) {
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(g_db, "SELECT v FROM meta WHERE k=?", -1, &st, nullptr) != SQLITE_OK)
        return {};
    bind_text(st, 1, key);
    std::string out;
    if (sqlite3_step(st) == SQLITE_ROW) {
        auto p = sqlite3_column_text(st, 0);
        if (p) out = reinterpret_cast<const char*>(p);
    }
    sqlite3_finalize(st);
    return out;
}

bool store_meta_set(const std::string& key, const std::string& value, std::string& err) {
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(g_db,
            "INSERT INTO meta (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            -1, &st, nullptr) != SQLITE_OK) {
        err = sqlite3_errmsg(g_db);
        return false;
    }
    bind_text(st, 1, key);
    bind_text(st, 2, value);
    int rc = sqlite3_step(st);
    sqlite3_finalize(st);
    if (rc != SQLITE_DONE) {
        err = sqlite3_errmsg(g_db);
        return false;
    }
    return true;
}

bool store_exec(const std::string& sql, std::string& err) {
    return exec_sql(sql.c_str(), err);
}

sqlite3* store_handle() { return g_db; }

bool store_price(const std::string& matrix, const std::string& bot, const std::string& coin,
                 const std::string& ts, long long unix_s, double mid, double hi, double lo,
                 const std::string& src, std::string& err) {
    if (coin.empty() || unix_s <= 0 || !(mid > 0)) return false;
    const char* sql =
        "INSERT INTO prices (matrix_id, bot_id, coin, ts, unix_s, mid, hi, lo, src)"
        " VALUES (?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(matrix_id, bot_id, coin, unix_s) DO UPDATE SET"
        "  mid=excluded.mid, hi=excluded.hi, lo=excluded.lo, ts=excluded.ts, src=excluded.src"
        " WHERE prices.src != 'candle' OR excluded.src = 'candle'";
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(g_db, sql, -1, &st, nullptr) != SQLITE_OK) {
        err = sqlite3_errmsg(g_db);
        return false;
    }
    bind_text(st, 1, matrix);
    bind_text(st, 2, bot);
    bind_text(st, 3, coin);
    bind_text(st, 4, ts);
    sqlite3_bind_int64(st, 5, unix_s);
    sqlite3_bind_double(st, 6, mid);
    sqlite3_bind_double(st, 7, hi > 0 ? hi : mid);
    sqlite3_bind_double(st, 8, lo > 0 ? lo : mid);
    bind_text(st, 9, src);
    int rc = sqlite3_step(st);
    sqlite3_finalize(st);
    if (rc != SQLITE_DONE) {
        err = sqlite3_errmsg(g_db);
        return false;
    }
    return true;
}

bool store_ctrl_if_changed(const CtrlSnap& snap, std::string& err) {
    sqlite3_stmt* st = nullptr;
    const char* prev =
        "SELECT fingerprint FROM ctrl_snaps WHERE matrix_id=? AND bot_id=? "
        "ORDER BY unix_s DESC, id DESC LIMIT 1";
    if (sqlite3_prepare_v2(g_db, prev, -1, &st, nullptr) != SQLITE_OK) {
        err = sqlite3_errmsg(g_db);
        return false;
    }
    bind_text(st, 1, snap.matrix);
    bind_text(st, 2, snap.bot);
    std::string last;
    if (sqlite3_step(st) == SQLITE_ROW) {
        auto p = sqlite3_column_text(st, 0);
        if (p) last = reinterpret_cast<const char*>(p);
    }
    sqlite3_finalize(st);
    if (last == snap.fingerprint) return false;
    const char* sql =
        "INSERT INTO ctrl_snaps (matrix_id, bot_id, ts, unix_s, fingerprint, take_profit,"
        " trail_activation, trail_delta, sl_roe_pct, time_limit_s, leverage_tier,"
        " leverage_json, bias_json, dont_go_crazy, after_loss_no_rebuy)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)";
    st = nullptr;
    if (sqlite3_prepare_v2(g_db, sql, -1, &st, nullptr) != SQLITE_OK) {
        err = sqlite3_errmsg(g_db);
        return false;
    }
    bind_text(st, 1, snap.matrix);
    bind_text(st, 2, snap.bot);
    bind_text(st, 3, snap.ts);
    sqlite3_bind_int64(st, 4, snap.unix_s);
    bind_text(st, 5, snap.fingerprint);
    sqlite3_bind_double(st, 6, snap.take_profit);
    sqlite3_bind_double(st, 7, snap.trail_activation);
    sqlite3_bind_double(st, 8, snap.trail_delta);
    sqlite3_bind_double(st, 9, snap.sl_roe_pct);
    sqlite3_bind_int64(st, 10, snap.time_limit_s);
    bind_text(st, 11, snap.leverage_tier);
    bind_text(st, 12, snap.leverage_json);
    bind_text(st, 13, snap.bias_json);
    sqlite3_bind_int64(st, 14, snap.dont_go_crazy);
    sqlite3_bind_int64(st, 15, snap.after_loss_no_rebuy);
    int rc = sqlite3_step(st);
    sqlite3_finalize(st);
    if (rc != SQLITE_DONE) {
        err = sqlite3_errmsg(g_db);
        return false;
    }
    return sqlite3_changes(g_db) > 0;
}

bool store_replay_replace(const std::string& bot, const std::vector<ReplayRow>& rows, std::string& err) {
    if (!exec_sql("BEGIN", err)) return false;
    sqlite3_stmt* del = nullptr;
    if (sqlite3_prepare_v2(g_db, "DELETE FROM setup_replay WHERE bot_id=?", -1, &del, nullptr) != SQLITE_OK) {
        err = sqlite3_errmsg(g_db);
        exec_sql("ROLLBACK", err);
        return false;
    }
    bind_text(del, 1, bot);
    if (sqlite3_step(del) != SQLITE_DONE) {
        err = sqlite3_errmsg(g_db);
        sqlite3_finalize(del);
        exec_sql("ROLLBACK", err);
        return false;
    }
    sqlite3_finalize(del);
    const char* sql =
        "INSERT INTO setup_replay (bot_id, trade_id, variant, coin, side, entry_ts, unix_s,"
        " exit_unix, tp, trail_act, trail_delta, sl, pnl_usd, roe_pct, exit_why, actual)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)";
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(g_db, sql, -1, &st, nullptr) != SQLITE_OK) {
        err = sqlite3_errmsg(g_db);
        exec_sql("ROLLBACK", err);
        return false;
    }
    for (auto& r : rows) {
        sqlite3_reset(st);
        sqlite3_clear_bindings(st);
        bind_text(st, 1, bot);
        bind_text(st, 2, r.trade_id);
        bind_text(st, 3, r.variant);
        bind_text(st, 4, r.coin);
        bind_text(st, 5, r.side);
        bind_text(st, 6, r.entry_ts);
        sqlite3_bind_int64(st, 7, r.unix_s);
        sqlite3_bind_int64(st, 8, r.exit_unix);
        if (r.has_knobs) {
            sqlite3_bind_double(st, 9, r.tp);
            sqlite3_bind_double(st, 10, r.trail_act);
            sqlite3_bind_double(st, 11, r.trail_delta);
            sqlite3_bind_double(st, 12, r.sl);
        } else {
            sqlite3_bind_null(st, 9);
            sqlite3_bind_null(st, 10);
            sqlite3_bind_null(st, 11);
            sqlite3_bind_null(st, 12);
        }
        sqlite3_bind_double(st, 13, r.pnl);
        sqlite3_bind_double(st, 14, r.roe);
        bind_text(st, 15, r.exit_why);
        sqlite3_bind_int64(st, 16, r.actual);
        if (sqlite3_step(st) != SQLITE_DONE) {
            err = sqlite3_errmsg(g_db);
            sqlite3_finalize(st);
            exec_sql("ROLLBACK", err);
            return false;
        }
    }
    sqlite3_finalize(st);
    if (!exec_sql("COMMIT", err)) {
        exec_sql("ROLLBACK", err);
        return false;
    }
    return true;
}

StoreCounts store_counts() {
    StoreCounts c;
    auto one = [](const char* sql) -> long long {
        sqlite3_stmt* st = nullptr;
        if (sqlite3_prepare_v2(g_db, sql, -1, &st, nullptr) != SQLITE_OK) return 0;
        long long n = 0;
        if (sqlite3_step(st) == SQLITE_ROW) n = sqlite3_column_int64(st, 0);
        sqlite3_finalize(st);
        return n;
    };
    c.trades = one("SELECT COUNT(*) FROM trades");
    c.actions = one("SELECT COUNT(*) FROM actions");
    c.matrices = one("SELECT COUNT(*) FROM matrices");
    c.prices = one("SELECT COUNT(*) FROM prices");
    c.replays = one("SELECT COUNT(*) FROM setup_replay");
    c.pages = one("PRAGMA page_count");
    c.page_size = one("PRAGMA page_size");
    c.db_bytes = c.pages * c.page_size;
    return c;
}

static bool print_query(const char* sql, int limit, std::string& err) {
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(g_db, sql, -1, &st, nullptr) != SQLITE_OK) {
        err = sqlite3_errmsg(g_db);
        return false;
    }
    sqlite3_bind_int64(st, 1, limit);
    while (sqlite3_step(st) == SQLITE_ROW) {
        auto col = [](sqlite3_stmt* s, int i) -> std::string {
            auto p = sqlite3_column_text(s, i);
            return p ? reinterpret_cast<const char*>(p) : "";
        };
        std::cout << col(st, 0);
        for (int i = 1; i < 8; ++i) std::cout << "  " << col(st, i);
        std::cout << "\n";
    }
    sqlite3_finalize(st);
    return true;
}

bool store_print_trades(int limit, std::string& err) {
    return print_query(
        "SELECT ts, matrix_id, bot_id, action, side, coin,"
        " printf('%+.2f', COALESCE(pnl_usd, 0)), COALESCE(reason, '')"
        " FROM trades ORDER BY ts DESC, id DESC LIMIT ?",
        limit, err);
}

bool store_print_actions(int limit, std::string& err) {
    return print_query(
        "SELECT ts, matrix_id, bot_id, kind, summary, '', '', ''"
        " FROM actions ORDER BY ts DESC, id DESC LIMIT ?",
        limit, err);
}

void store_print_capacity() {
    StoreCounts c = store_counts();
    long long max_pages = 0;
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(g_db, "PRAGMA max_page_count", -1, &st, nullptr) == SQLITE_OK) {
        if (sqlite3_step(st) == SQLITE_ROW) max_pages = sqlite3_column_int64(st, 0);
        sqlite3_finalize(st);
    }
    std::string first, last;
    st = nullptr;
    if (sqlite3_prepare_v2(g_db, "SELECT MIN(ts), MAX(ts) FROM trades", -1, &st, nullptr) == SQLITE_OK) {
        if (sqlite3_step(st) == SQLITE_ROW) {
            auto col = [](sqlite3_stmt* s, int i) -> std::string {
                auto p = sqlite3_column_text(s, i);
                return p ? reinterpret_cast<const char*>(p) : "";
            };
            first = col(st, 0);
            last = col(st, 1);
        }
        sqlite3_finalize(st);
    }
    long long rows = c.trades + c.actions;
    double per = rows > 0 ? static_cast<double>(c.db_bytes) / static_cast<double>(rows) : 500.0;
    // Planner, not the truncated json window. 8 hunters, 20 closes a day,
    // 4 online matrices, plus about the same number of actions.
    const double days = 730.0;
    const double trades_2y = 4.0 * 8.0 * 20.0 * days;
    const double actions_2y = trades_2y;
    double bytes_2y = (trades_2y + actions_2y) * per;
    double max_bytes = static_cast<double>(max_pages) * static_cast<double>(c.page_size);
    std::cout << "sqlite page " << c.page_size << "  max_pages " << max_pages << "\n";
    std::cout << "sqlite max about " << (max_bytes / (1024.0 * 1024 * 1024 * 1024)) << " TiB\n";
    std::cout << "pettibon.db " << c.db_bytes << " bytes  trades " << c.trades
              << "  actions " << c.actions << "  matrices " << c.matrices << "\n";
    std::cout << "trade span " << (first.empty() ? "-" : first) << " -> " << (last.empty() ? "-" : last) << "\n";
    std::cout << "bytes/row " << static_cast<long long>(per) << "\n";
    std::cout << "2 year plan  " << static_cast<long long>(trades_2y) << " trades + "
              << static_cast<long long>(actions_2y) << " actions  about "
              << (bytes_2y / (1024.0 * 1024)) << " MiB\n";
    std::cout << "fits " << (bytes_2y < max_bytes ? "yes" : "no") << "\n";
}
