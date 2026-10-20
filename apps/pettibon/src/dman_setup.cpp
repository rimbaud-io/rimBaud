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

#include "dman_setup.hpp"
#include "store.hpp"

#include <nlohmann/json.hpp>

#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <vector>

using json = nlohmann::json;

namespace {

const char* kBot = "hummingbot-dman-maker";
const char* kCoins[] = {"BTC", "ETH", "LTC", "TAO"};

long long parse_utc(const std::string& ts) {
    if (ts.size() < 19) return 0;
    std::tm tm{};
    if (!strptime(ts.c_str(), "%Y-%m-%d %H:%M:%S", &tm)) return 0;
    return timegm(&tm);
}

std::string fmt_utc(long long unix_s) {
    if (unix_s <= 0) return {};
    std::time_t t = static_cast<std::time_t>(unix_s);
    std::tm tm{};
    gmtime_r(&t, &tm);
    char buf[40];
    strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S UTC", &tm);
    return buf;
}

json read_json(const std::string& path) {
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

double jnum(const json& j, const char* k, double fallback) {
    if (!j.is_object() || !j.contains(k) || j[k].is_null()) return fallback;
    try {
        if (j[k].is_number()) return j[k].get<double>();
        if (j[k].is_string()) return std::stod(j[k].get<std::string>());
    } catch (...) {}
    return fallback;
}

long long jll(const json& j, const char* k) {
    if (!j.is_object() || !j.contains(k) || j[k].is_null()) return 0;
    try {
        if (j[k].is_number()) return j[k].get<long long>();
        if (j[k].is_string()) return std::stoll(j[k].get<std::string>());
    } catch (...) {}
    return 0;
}

// The ctrl file is the desk's saved knobs. The status file may omit them.
int jflag(const json& ctrl, const json& st, const char* key) {
    const json* src = nullptr;
    if (ctrl.is_object() && ctrl.contains(key)) src = &ctrl;
    else if (st.is_object() && st.contains(key)) src = &st;
    if (!src) return 1;
    const json& v = (*src)[key];
    if (v.is_boolean()) return v.get<bool>() ? 1 : 0;
    if (v.is_number()) return v.get<double>() != 0.0 ? 1 : 0;
    return 1;
}

int coin_lev(const std::string& coin, double from_trade) {
    if (from_trade >= 1.0) return static_cast<int>(from_trade + 0.5);
    if (coin == "BTC") return 40;
    if (coin == "ETH") return 25;
    if (coin == "LTC") return 10;
    if (coin == "TAO") return 5;
    return 10;
}

struct Bar {
    long long t = 0;
    double hi = 0;
    double lo = 0;
    double cl = 0;
};

struct Exit {
    bool hit = false;
    double px = 0;
    long long t = 0;
    const char* why = "";
};

// Walk bars that open at or after the entry. A 15-minute wick from before
// the fill is not used. Same-bar trail arm-and-stop waits for the next bar.
Exit walk_bars(const std::vector<Bar>& bars, long long entry_unix, double entry, double lev,
               bool long_side, double tp, double act, double delta, double sl) {
    Exit out;
    bool trail = false;
    double extreme = entry;
    long long horizon = entry_unix + 7 * 86400;
    double last_px = 0;
    long long last_t = 0;
    double sl_move = (sl / 100.0) / std::max(1.0, lev);
    for (auto& b : bars) {
        if (b.t < entry_unix) continue;
        if (b.t > horizon) break;
        double hi = b.hi > 0 ? b.hi : b.cl;
        double lo = b.lo > 0 ? b.lo : b.cl;
        double cl = b.cl > 0 ? b.cl : hi;
        if (!(hi > 0) || !(lo > 0)) continue;
        if (long_side) {
            double sl_px = entry * (1.0 - sl_move);
            if (lo <= sl_px) return Exit{true, sl_px, b.t, "SL"};
            if (trail && lo <= extreme * (1.0 - delta))
                return Exit{true, extreme * (1.0 - delta), b.t, "TRAIL"};
            if (hi >= entry * (1.0 + tp))
                return Exit{true, entry * (1.0 + tp), b.t, "TP"};
            if (hi >= entry * (1.0 + act)) {
                trail = true;
                if (hi > extreme) extreme = hi;
            }
        } else {
            double sl_px = entry * (1.0 + sl_move);
            if (hi >= sl_px) return Exit{true, sl_px, b.t, "SL"};
            if (trail && hi >= extreme * (1.0 + delta))
                return Exit{true, extreme * (1.0 + delta), b.t, "TRAIL"};
            if (lo <= entry * (1.0 - tp))
                return Exit{true, entry * (1.0 - tp), b.t, "TP"};
            if (lo <= entry * (1.0 - act)) {
                trail = true;
                if (lo < extreme) extreme = lo;
            }
        }
        last_px = cl;
        last_t = b.t;
    }
    if (last_px > 0) return Exit{true, last_px, last_t, "open at price end"};
    return out;
}

std::string variant_name(double tp, double act, double delta, double sl, bool live) {
    char buf[96];
    std::snprintf(buf, sizeof(buf), "TP %.0f%%  trail %.1f/%.2f  SL %.0f%s",
                  tp * 100.0, act * 100.0, delta * 100.0, sl, live ? "  live" : "");
    return buf;
}

bool col_null(sqlite3_stmt* st, int i) {
    return sqlite3_column_type(st, i) == SQLITE_NULL;
}

std::string col_text(sqlite3_stmt* st, int i) {
    auto p = sqlite3_column_text(st, i);
    return p ? reinterpret_cast<const char*>(p) : "";
}

}  // namespace

int dman_observe(const std::string& conf) {
    json st = read_json(conf + "/dman_maker_status.json");
    if (!st.is_object()) return 0;
    std::string ts = st.value("ts", std::string{});
    long long unix_s = parse_utc(ts);
    if (unix_s <= 0) {
        unix_s = time(nullptr);
        ts = fmt_utc(unix_s);
    }
    long long minute = unix_s / 60 * 60;
    std::string err;
    json positions = st.value("positions", json::object());
    if (positions.is_object()) {
        for (auto it = positions.begin(); it != positions.end(); ++it) {
            if (!it.value().is_object()) continue;
            double mid = jnum(it.value(), "mid_price", 0);
            if (!(mid > 0)) continue;
            store_price("localhost", kBot, it.key(), fmt_utc(minute), minute, mid, mid, mid, "status", err);
        }
    }
    json ctrl = read_json(conf + "/hummingbot-dman-maker_ctrl.json");
    std::string tier = "MAX";
    if (ctrl.is_object() && ctrl.contains("leverage_tier") && ctrl["leverage_tier"].is_string()) {
        tier = ctrl["leverage_tier"].get<std::string>();
        for (char& c : tier) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    }
    if (tier != "MAX" && tier != "MID" && tier != "LOW") tier = "MAX";
    CtrlSnap snap;
    snap.matrix = "localhost";
    snap.bot = kBot;
    snap.ts = ts;
    snap.unix_s = unix_s;
    snap.take_profit = jnum(st, "take_profit", 0.02);
    snap.trail_activation = jnum(st, "trail_activation", 0.015);
    snap.trail_delta = jnum(st, "trail_delta", 0.001);
    snap.sl_roe_pct = jnum(st, "sl_roe_pct", 20);
    snap.time_limit_s = static_cast<int>(jnum(st, "time_limit_s", 0));
    snap.leverage_tier = tier;
    snap.leverage_json = st.contains("leverage") ? st["leverage"].dump() : "{}";
    snap.bias_json = st.contains("bias") ? st["bias"].dump() : "{}";
    snap.dont_go_crazy = jflag(ctrl, st, "dontGoCrazy");
    snap.after_loss_no_rebuy = jflag(ctrl, st, "afterLossNoRebuy");
    char fp[512];
    std::snprintf(fp, sizeof(fp), "%s|%.6f|%.6f|%.6f|%.4f|%d|%s|%s|%d|%d",
                  tier.c_str(), snap.take_profit, snap.trail_activation, snap.trail_delta,
                  snap.sl_roe_pct, snap.time_limit_s, snap.leverage_json.c_str(),
                  snap.bias_json.c_str(), snap.dont_go_crazy, snap.after_loss_no_rebuy);
    snap.fingerprint = fp;
    return store_ctrl_if_changed(snap, err) ? 1 : 0;
}

int dman_score() {
    sqlite3* db = store_handle();
    if (!db) return 0;
    long long closes = 0;
    long long candles = 0;
    std::string fp;
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(db,
            "SELECT COUNT(*) FROM trades WHERE matrix_id='localhost' AND bot_id=? "
            "AND upper(action)='CLOSE'",
            -1, &st, nullptr) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, kBot, -1, SQLITE_TRANSIENT);
        if (sqlite3_step(st) == SQLITE_ROW) closes = sqlite3_column_int64(st, 0);
        sqlite3_finalize(st);
    }
    st = nullptr;
    if (sqlite3_prepare_v2(db,
            "SELECT COUNT(*) FROM prices WHERE matrix_id='localhost' AND bot_id=? AND src='candle'",
            -1, &st, nullptr) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, kBot, -1, SQLITE_TRANSIENT);
        if (sqlite3_step(st) == SQLITE_ROW) candles = sqlite3_column_int64(st, 0);
        sqlite3_finalize(st);
    }
    st = nullptr;
    if (sqlite3_prepare_v2(db,
            "SELECT fingerprint FROM ctrl_snaps WHERE bot_id=? ORDER BY unix_s DESC, id DESC LIMIT 1",
            -1, &st, nullptr) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, kBot, -1, SQLITE_TRANSIENT);
        if (sqlite3_step(st) == SQLITE_ROW) fp = col_text(st, 0);
        sqlite3_finalize(st);
    }
    std::string sig = std::to_string(closes) + "|" + std::to_string(candles) + "|" + fp;
    long long now = time(nullptr);
    long long scored_at = 0;
    try { scored_at = std::stoll(store_meta("dman_score_at")); } catch (...) { scored_at = 0; }
    if (sig == store_meta("dman_score_sig") && now - scored_at < 600) return 0;

    struct Ev {
        std::string id, action, coin, side, ts, reason;
        long long unix_s = 0;
        double amount = 0, price = 0, entry = 0, pnl = 0, roe = 0, lev = 0;
        bool has_amount = false, has_price = false, has_entry = false, has_pnl = false, has_lev = false;
    };
    std::vector<Ev> evs;
    st = nullptr;
    const char* q =
        "SELECT trade_id, upper(action), coin, side, ts, "
        "CAST(strftime('%s', replace(ts, ' UTC', '')) AS INTEGER), "
        "amount, price, entry_price, pnl_usd, roe_pct, leverage, reason "
        "FROM trades WHERE matrix_id='localhost' AND bot_id=? ORDER BY ts, id";
    if (sqlite3_prepare_v2(db, q, -1, &st, nullptr) != SQLITE_OK) return 0;
    sqlite3_bind_text(st, 1, kBot, -1, SQLITE_TRANSIENT);
    while (sqlite3_step(st) == SQLITE_ROW) {
        Ev e;
        e.id = col_text(st, 0);
        e.action = col_text(st, 1);
        e.coin = col_text(st, 2);
        e.side = col_text(st, 3);
        e.ts = col_text(st, 4);
        e.unix_s = sqlite3_column_int64(st, 5);
        e.has_amount = !col_null(st, 6);
        e.has_price = !col_null(st, 7);
        e.has_entry = !col_null(st, 8);
        e.has_pnl = !col_null(st, 9);
        e.has_lev = !col_null(st, 11);
        if (e.has_amount) e.amount = sqlite3_column_double(st, 6);
        if (e.has_price) e.price = sqlite3_column_double(st, 7);
        if (e.has_entry) e.entry = sqlite3_column_double(st, 8);
        if (e.has_pnl) e.pnl = sqlite3_column_double(st, 9);
        e.roe = col_null(st, 10) ? 0 : sqlite3_column_double(st, 10);
        if (e.has_lev) e.lev = sqlite3_column_double(st, 11);
        e.reason = col_text(st, 12);
        evs.push_back(e);
    }
    sqlite3_finalize(st);

    std::map<std::string, std::vector<Bar>> bars;
    st = nullptr;
    if (sqlite3_prepare_v2(db,
            "SELECT coin, unix_s, hi, lo, mid FROM prices "
            "WHERE matrix_id='localhost' AND bot_id=? ORDER BY unix_s",
            -1, &st, nullptr) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, kBot, -1, SQLITE_TRANSIENT);
        while (sqlite3_step(st) == SQLITE_ROW) {
            Bar b;
            b.t = sqlite3_column_int64(st, 1);
            b.hi = sqlite3_column_double(st, 2);
            b.lo = sqlite3_column_double(st, 3);
            b.cl = sqlite3_column_double(st, 4);
            bars[col_text(st, 0)].push_back(b);
        }
        sqlite3_finalize(st);
    }

    double live_tp = 0.02, live_act = 0.015, live_delta = 0.001, live_sl = 20;
    bool have_live = false;
    st = nullptr;
    if (sqlite3_prepare_v2(db,
            "SELECT take_profit, trail_activation, trail_delta, sl_roe_pct FROM ctrl_snaps "
            "WHERE bot_id=? ORDER BY unix_s DESC, id DESC LIMIT 1",
            -1, &st, nullptr) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, kBot, -1, SQLITE_TRANSIENT);
        if (sqlite3_step(st) == SQLITE_ROW) {
            live_tp = sqlite3_column_double(st, 0);
            live_act = sqlite3_column_double(st, 1);
            live_delta = sqlite3_column_double(st, 2);
            live_sl = sqlite3_column_double(st, 3);
            have_live = true;
        }
        sqlite3_finalize(st);
    }

    const double tps[] = {0.01, 0.02, 0.03};
    const double acts[] = {0.008, 0.015, 0.025};
    const double deltas[] = {0.001, 0.005, 0.010};
    const double sls[] = {10, 20, 40};

    std::map<std::string, Ev> open_of;
    std::vector<ReplayRow> rows;
    for (auto& e : evs) {
        if (e.action == "OPEN") {
            open_of[e.coin] = e;
            continue;
        }
        if (e.action != "CLOSE" || !e.has_pnl) continue;
        Ev opened = open_of[e.coin];
        open_of.erase(e.coin);
        long long entry_unix = opened.unix_s > 0 ? opened.unix_s : e.unix_s;
        double entry = e.has_entry && e.entry > 0 ? e.entry : opened.price;
        if (!(entry > 0) || !(e.amount > 0) || entry_unix <= 0) continue;
        ReplayRow actual;
        actual.trade_id = e.id.empty() ? e.ts + "|" + e.coin : e.id;
        actual.variant = "as traded";
        actual.coin = e.coin;
        actual.side = e.side;
        actual.entry_ts = opened.ts.empty() ? e.ts : opened.ts;
        actual.unix_s = entry_unix;
        actual.exit_unix = e.unix_s;
        actual.pnl = e.pnl;
        actual.roe = e.roe;
        actual.exit_why = e.reason.empty() ? "recorded close" : e.reason;
        actual.actual = 1;
        rows.push_back(actual);

        bool long_side = e.side != "SHORT";
        int lev = coin_lev(e.coin, e.has_lev ? e.lev : 0);
        auto bit = bars.find(e.coin);
        if (bit == bars.end()) continue;
        for (double tp : tps) {
            for (double act : acts) {
                for (double delta : deltas) {
                    for (double sl : sls) {
                        Exit x = walk_bars(bit->second, entry_unix, entry, lev, long_side,
                                           tp, act, delta, sl);
                        if (!x.hit || !(x.px > 0)) continue;
                        ReplayRow r;
                        r.trade_id = actual.trade_id;
                        bool live = have_live && std::fabs(tp - live_tp) < 1e-9
                            && std::fabs(act - live_act) < 1e-9
                            && std::fabs(delta - live_delta) < 1e-9
                            && std::fabs(sl - live_sl) < 1e-6;
                        r.variant = variant_name(tp, act, delta, sl, live);
                        r.coin = e.coin;
                        r.side = e.side;
                        r.entry_ts = actual.entry_ts;
                        r.unix_s = entry_unix;
                        r.exit_unix = x.t;
                        r.tp = tp;
                        r.trail_act = act;
                        r.trail_delta = delta;
                        r.sl = sl;
                        r.has_knobs = true;
                        r.pnl = long_side ? e.amount * (x.px - entry) : e.amount * (entry - x.px);
                        r.roe = ((long_side ? (x.px - entry) : (entry - x.px)) / entry) * lev * 100.0;
                        r.exit_why = x.why;
                        r.actual = 0;
                        rows.push_back(r);
                    }
                }
            }
        }
    }
    std::string err;
    if (!store_replay_replace(kBot, rows, err)) {
        std::cerr << "pettibon dman replay: " << err << "\n";
        return 0;
    }
    store_meta_set("dman_score_sig", sig, err);
    store_meta_set("dman_score_at", std::to_string(now), err);
    return static_cast<int>(rows.size());
}

int dman_backfill_candles() {
    sqlite3* db = store_handle();
    if (!db) return 0;
    long long start = 0;
    sqlite3_stmt* st = nullptr;
    if (sqlite3_prepare_v2(db,
            "SELECT MIN(CAST(strftime('%s', replace(ts, ' UTC', '')) AS INTEGER)) "
            "FROM trades WHERE matrix_id='localhost' AND bot_id=?",
            -1, &st, nullptr) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, kBot, -1, SQLITE_TRANSIENT);
        if (sqlite3_step(st) == SQLITE_ROW && sqlite3_column_type(st, 0) != SQLITE_NULL)
            start = sqlite3_column_int64(st, 0);
        sqlite3_finalize(st);
    }
    long long now = time(nullptr);
    if (start <= 0) start = now - 30 * 86400;
    start -= 86400;
    long long end_ms = now * 1000;
    int stored = 0;
    std::string err;
    for (auto coin : kCoins) {
        long long cursor = start * 1000;
        long long prev_newest = 0;
        int pages = 0;
        int calls = 0;
        int n = 0;
        while (pages < 4 && cursor < end_ms) {
            pages++;
            calls++;
            std::ostringstream cmd;
            cmd << "curl -sS --max-time 25 -H 'Content-Type: application/json' --data '"
                << "{\"type\":\"candleSnapshot\",\"req\":{\"coin\":\"" << coin
                << "\",\"interval\":\"15m\",\"startTime\":" << cursor
                << ",\"endTime\":" << end_ms << "}}' https://api.hyperliquid.xyz/info";
            FILE* fp = popen(cmd.str().c_str(), "r");
            if (!fp) {
                std::cerr << "pettibon candle " << coin << ": curl failed\n";
                break;
            }
            std::string body;
            char buf[8192];
            while (fgets(buf, sizeof(buf), fp)) body.append(buf);
            int rc = pclose(fp);
            if (rc != 0 || body.empty()) {
                std::cerr << "pettibon candle " << coin << ": no body\n";
                break;
            }
            json candles;
            try { candles = json::parse(body); }
            catch (...) {
                std::cerr << "pettibon candle " << coin << ": bad json\n";
                break;
            }
            if (!candles.is_array()) {
                std::cerr << "pettibon candle " << coin << ": " << body.substr(0, 160) << "\n";
                break;
            }
            long long newest = 0;
            int got = 0;
            for (auto& c : candles) {
                if (!c.is_object()) continue;
                long long tms = jll(c, "t");
                long long unix_s = tms / 1000;
                double hi = jnum(c, "h", 0);
                double lo = jnum(c, "l", 0);
                double cl = jnum(c, "c", 0);
                if (unix_s <= 0 || !(cl > 0)) continue;
                got++;
                if (tms > newest) newest = tms;
                if (store_price("localhost", kBot, coin, fmt_utc(unix_s), unix_s, cl, hi, lo, "candle", err)
                    && sqlite3_changes(db) > 0)
                    n++;
            }
            if (got == 0 || newest <= prev_newest) break;
            prev_newest = newest;
            // One reply holds the whole window unless the exchange truncated it.
            if (newest + 15LL * 60 * 1000 >= end_ms - 60 * 1000) break;
            if (got < 5000) break;
            cursor = newest + 1;
        }
        std::cout << "candle " << coin << " " << n << "  calls " << calls << "  weight " << (calls * 20) << "\n";
        stored += n;
    }
    return stored;
}
