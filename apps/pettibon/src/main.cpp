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

#include "collect.hpp"
#include "dman_setup.hpp"
#include "store.hpp"

#include <atomic>
#include <csignal>
#include <iostream>
#include <string>
#include <unistd.h>

static std::atomic<int> g_stop{0};

static void on_stop(int) { g_stop.store(1); }

static bool is_conf(const std::string& p) {
    return access((p + "/wire_registry.json").c_str(), R_OK) == 0
        || access((p + "/bot_sl.json").c_str(), R_OK) == 0;
}

static void usage() {
    std::cout <<
        "pettibon — C++ log of online rimBaud matrices\n"
        "Reads Spring /api/home and the local trade files.\n"
        "Writes its sqlite file only. Does not place orders and does not\n"
        "restart hunters. The running poll does not call Hyperliquid.\n\n"
        "  --conf DIR         hummingbot conf/ (default ../conf)\n"
        "  --db PATH          sqlite file (default CONF/pettibon.db)\n"
        "  --interval SEC     poll seconds (default 20, minimum 5)\n"
        "  --once             one pass, then exit\n"
        "  --stats            row counts\n"
        "  --trades [N]       newest trades (default 20)\n"
        "  --actions [N]      newest actions (default 20)\n"
        "  --capacity         whether this sqlite file can hold 2 years\n"
        "  --backfill-dman    once: 15m candles for DMan Maker, then exit\n"
        "                     calls Hyperliquid. The daemon does not.\n";
}

int main(int argc, char** argv) {
    std::string conf, db;
    int interval = 20;
    int list_n = 20;
    bool once = false;
    bool stats = false;
    bool trades = false;
    bool actions = false;
    bool capacity = false;
    bool backfill = false;
    if (const char* e = getenv("FREIQUELL_CONF")) conf = e;

    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--help" || a == "-h") { usage(); return 0; }
        else if (a == "--once") once = true;
        else if (a == "--stats") stats = true;
        else if (a == "--capacity") capacity = true;
        else if (a == "--backfill-dman") backfill = true;
        else if (a == "--trades") {
            trades = true;
            if (i + 1 < argc && argv[i + 1][0] != '-') list_n = atoi(argv[++i]);
        } else if (a == "--actions") {
            actions = true;
            if (i + 1 < argc && argv[i + 1][0] != '-') list_n = atoi(argv[++i]);
        } else if ((a == "--conf" || a == "-c") && i + 1 < argc) conf = argv[++i];
        else if (a == "--db" && i + 1 < argc) db = argv[++i];
        else if (a == "--interval" && i + 1 < argc) interval = atoi(argv[++i]);
        else {
            std::cerr << "unknown arg " << a << "\n";
            usage();
            return 2;
        }
    }
    if (interval < 5) interval = 5;
    if (list_n < 1) list_n = 1;
    if (list_n > 500) list_n = 500;

    if (conf.empty()) {
        const char* guesses[] = {"../conf", "conf", "conf"};
        for (auto g : guesses) if (is_conf(g)) { conf = g; break; }
    }
    if (conf.empty() || !is_conf(conf)) {
        std::cerr << "pettibon: cannot find conf/. Use --conf\n";
        return 1;
    }
    char resolved[4096];
    if (realpath(conf.c_str(), resolved)) conf = resolved;
    if (db.empty()) db = conf + "/pettibon.db";

    std::string err;
    if (!store_open(db, err)) {
        std::cerr << "pettibon: " << err << "\n";
        return 1;
    }

    if (stats || trades || actions || capacity) {
        if (stats) {
            StoreCounts c = store_counts();
            std::cout << "trades " << c.trades << "\nactions " << c.actions
                      << "\nmatrices " << c.matrices
                      << "\nprices " << c.prices
                      << "\nreplays " << c.replays
                      << "\nbytes " << c.db_bytes << "\n";
        }
        if (trades && !store_print_trades(list_n, err)) std::cerr << err << "\n";
        if (actions && !store_print_actions(list_n, err)) std::cerr << err << "\n";
        if (capacity) store_print_capacity();
        store_close();
        return err.empty() ? 0 : 1;
    }

    if (backfill) {
        int observed = dman_observe(conf);
        int candles = dman_backfill_candles();
        int rows = dman_score();
        StoreCounts c = store_counts();
        std::cout << "ctrl snap " << observed
                  << "\ncandles new " << candles
                  << "\nreplay rows " << rows
                  << "\nprices " << c.prices
                  << "\nreplays " << c.replays << "\n";
        store_close();
        return 0;
    }

    std::signal(SIGINT, on_stop);
    std::signal(SIGTERM, on_stop);
    std::cout.setf(std::ios::unitbuf);
    std::cout << "pettibon db " << db << " interval " << interval << "s\n";
    do {
        PassResult r = collect_once(conf);
        if (r.trades_new || r.actions_new || !r.note.empty()) {
            std::cout << "up " << r.matrices_up << " down " << r.matrices_down
                      << " trades +" << r.trades_new << " actions +" << r.actions_new;
            if (!r.note.empty()) std::cout << "  " << r.note;
            std::cout << "\n";
        }
        if (once || g_stop.load()) break;
        for (int s = 0; s < interval && !g_stop.load(); ++s) sleep(1);
    } while (!g_stop.load());
    store_close();
    return 0;
}
