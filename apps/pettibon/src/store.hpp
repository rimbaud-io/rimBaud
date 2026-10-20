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

#pragma once

#include "sqlite3_min.h"

#include <cstdint>
#include <string>
#include <vector>

// Pettibon's own SQLite file. Never opens conf/matrix.db for writing.

struct TradeIn {
    std::string matrix;
    std::string bot;
    std::string trade_id;
    std::string ts;
    std::string action;
    std::string coin;
    std::string side;
    std::string reason;
    std::string source;
    bool has_amount = false;
    bool has_price = false;
    bool has_entry = false;
    bool has_pnl = false;
    bool has_roe = false;
    bool has_lev = false;
    double amount = 0;
    double price = 0;
    double entry = 0;
    double pnl = 0;
    double roe = 0;
    double lev = 0;
};

struct ActionIn {
    std::string matrix;
    std::string bot;
    std::string action_key;
    std::string ts;
    std::string kind;
    std::string summary;
    std::string payload;
};

struct StoreCounts {
    long long trades = 0;
    long long actions = 0;
    long long matrices = 0;
    long long prices = 0;
    long long replays = 0;
    long long pages = 0;
    long long page_size = 0;
    long long db_bytes = 0;
};

struct CtrlSnap {
    std::string matrix;
    std::string bot;
    std::string ts;
    long long unix_s = 0;
    std::string fingerprint;
    double take_profit = 0;
    double trail_activation = 0;
    double trail_delta = 0;
    double sl_roe_pct = 0;
    int time_limit_s = 0;
    std::string leverage_tier;
    std::string leverage_json;
    std::string bias_json;
    int dont_go_crazy = 0;
    int after_loss_no_rebuy = 0;
};

struct ReplayRow {
    std::string trade_id;
    std::string variant;
    std::string coin;
    std::string side;
    std::string entry_ts;
    long long unix_s = 0;
    long long exit_unix = 0;
    double tp = 0;
    double trail_act = 0;
    double trail_delta = 0;
    double sl = 0;
    double pnl = 0;
    double roe = 0;
    std::string exit_why;
    int actual = 0;
    bool has_knobs = false;
};

bool store_open(const std::string& path, std::string& err);
void store_close();
bool store_ready();

// Insert. Returns true when a new row was written. Fills a null dollar
// field on a row that was stored before the hunter knew the pnl.
bool store_trade(const TradeIn& t, std::string& err);
bool store_action(const ActionIn& a, std::string& err);
bool store_matrix(const std::string& id, const std::string& name, const std::string& url,
                  bool online, const std::string& ts, std::string& err);
// -1 if this matrix has never been seen, else 0 or 1.
int store_matrix_online(const std::string& id);

std::string store_meta(const std::string& key);
bool store_meta_set(const std::string& key, const std::string& value, std::string& err);

bool store_exec(const std::string& sql, std::string& err);
sqlite3* store_handle();

// One mid per minute, or one 15-minute candle. A candle is not replaced by a later mid.
bool store_price(const std::string& matrix, const std::string& bot, const std::string& coin,
                 const std::string& ts, long long unix_s, double mid, double hi, double lo,
                 const std::string& src, std::string& err);
bool store_ctrl_if_changed(const CtrlSnap& snap, std::string& err);
bool store_replay_replace(const std::string& bot, const std::vector<ReplayRow>& rows, std::string& err);

StoreCounts store_counts();
bool store_print_trades(int limit, std::string& err);
bool store_print_actions(int limit, std::string& err);
void store_print_capacity();
