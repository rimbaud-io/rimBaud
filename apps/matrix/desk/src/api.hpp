// Copyright 2026 rimbaud.io
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

#include <nlohmann/json.hpp>
#include <map>
#include <string>
#include <vector>

// Read-only snapshot of the Flask desk. This client never POSTs.
// No BIAS / SL / restart / CLOSE / controller writes — live positions stay.

struct CtrlField {
    std::string key;
    std::string label;
    std::string kind; // num | pct_frac | bool | select
    double step = 1;
    double minv = 0;
    double maxv = 100;
    std::vector<std::string> options;
};

struct TradeRow {
    std::string ts;
    std::string action;
    std::string coin;
    std::string side;
    std::string reason;
    double amount = 0;
    double price = 0;
    double pnl = 0;
    double roe = 0;
    bool has_pnl = false;
    int trader = 0;
};

struct CopyTrader {
    int num = 0;
    std::string address;
    std::string short_addr;
    bool muted = false;
    int n_pos = 0;
    int follow_slots = 3;
    std::vector<std::string> coins;
};

struct Bot {
    std::string id;
    std::string name;
    std::string color = "#9dffc0";
    std::string address;
    std::string href;
    std::string icon;
    std::string health;
    std::string health_reason;
    std::string next_sell;
    std::string next_rebuy;
    bool running = false;
    bool deactivated = false; // docker pause: process frozen, 0 Hyperliquid IP, positions stay
    bool ok = false;
    double equity = 0;
    double free_usd = 0;
    double upnl = 0;
    double sl_roe = 20;
    int n_pos = 0;
    std::map<std::string, std::string> bias;
    std::string bias_mode; // long | short | both
    std::vector<std::string> coins;
    std::vector<std::string> markets;
    std::vector<std::string> idle_markets;
    std::vector<std::string> held_coins;
    std::vector<std::string> logs;
    std::vector<TradeRow> trades;
    int n_wins = 0;
    int n_losses = 0;
    int n_opens = 0;
    int n_closes = 0;
    double realized_pnl = 0;
    nlohmann::json controller = nlohmann::json::object();
    std::vector<CopyTrader> copy_traders;
};

struct Position {
    std::string bot_id;
    std::string bot;
    std::string coin;
    std::string side;
    double size = 0;
    double entry = 0;
    double mark = 0;
    double roe = 0;
    double pnl = 0;
    int leverage = 0;
};

struct Totals {
    double equity = 0;
    double upnl = 0;
    int n_pos = 0;
    int n_bots = 0;
    int n_ok = 0;
    int n_live = 0;
};

struct HlKey {
    std::string state;       // green, red, grey
    std::string on_exchange;
    std::string address;
    std::string agent_name;
    std::string checked_at;
    std::string error;
};

struct RateInfo {
    double used = 0;
    double cap = 1200;
    double left = 0;
    double pct = 0;
    int n429 = 0;
    double last_429_s = -1;
    double refill_in_s = 0;
    std::string note;
};

struct HostHealth {
    double cpu_pct = 0;
    double mem_pct = 0;
    double disk_pct = 0;
    double load1 = 0;
    double mem_used_gb = 0;
    double mem_total_gb = 0;
    double disk_used_gb = 0;
    double disk_total_gb = 0;
};

struct NetHealth {
    bool ok = false;
    int ms = 0;
    std::string target;
    std::string error;
};

struct RiskRow {
    std::string id;
    std::string name;
    std::string mode;
    std::string last_send_error;
    std::string last_send_ts;
    double parked_usd = 0;
    double available_usd = 0;
    double locked_usd = 0;
    double sent_usd = 0;
    int n_pos = 0;
    bool armed = false;
    bool connected = false;
};

struct RiskInfo {
    std::string address;
    double available_usd = 0;
    double locked_usd = 0;
    double parked_usd = 0;
    double sent_usd = 0;
    double onchain_usd = 0;
    bool can_send = false;
    int connected = 0;
    std::vector<RiskRow> hunters;
};

struct DeskBiasHunter {
    std::string id;
    std::string name;
    std::string mode;
    std::string target;
    bool applied = false;
    bool waiting = false;
    std::vector<std::string> held;
};

struct DeskBiasInfo {
    std::string bias;
    bool closes = false;
    std::vector<DeskBiasHunter> hunters;
};

struct CondorInfo {
    bool ok = false;
    bool running = false;
    std::string regime;
    std::string source;
    std::string title;
    std::string why;
    std::string ts;
    std::map<std::string, std::string> bias;
    std::vector<std::string> feed;
    bool telegram_ok = false;
    bool grok_ok = false;
};

struct Leader {
    std::string id;
    std::string title;
    std::string short_addr;
    bool followed = false;
    double day_pnl = 0;
    double week_pnl = 0;
    double u_pnl = 0;
    int n_positions = 0;
};

struct WireNode {
    std::string id;
    std::string type;
    std::string label;
    std::string color;
    std::string bot_id;
    std::string kind;
    std::string address;
    std::string extra;
    std::string skim_mode;
    bool armed = false;
    std::map<std::string, std::string> bias;
    double x = 0;
    double y = 0;
    double pct = 0;
};

struct WireEdge {
    std::string id;
    std::string source;
    std::string target;
    std::string source_handle;
    std::string target_handle;
};

struct ToolItem {
    std::string id;
    std::string category;
    std::string type;
    std::string label;
    std::string hint;
    std::string color;
    std::string kind;
    bool unique = true;
};

struct CompileResult {
    bool ok = false;
    bool can_integrate = false;
    bool positions_untouched = true;
    bool workaround_used = false;
    std::string message;
    std::string compile_id;
    std::string board;
    int n_blockers = 0;
    int n_new = 0;
    int n_rm = 0;
    int n_knobs = 0;
    int n_parked = 0;
    int n_pos = 0;
    std::string desk_bias;
    std::vector<std::string> blockers;
    std::vector<std::string> parked;
    std::vector<std::string> hunters;
    std::vector<std::string> wiring;
    std::vector<std::string> knobs;
    std::vector<std::string> bias;
    std::vector<std::string> risk;
    std::vector<std::string> warnings;
    std::vector<std::string> bands;
};

struct Wallet {
    std::string id;
    std::string name;
    std::string address;
    std::string notes;
    bool running = false;
    double equity = 0;
    double free_usd = 0;
    int n_pos = 0;
};

struct QaItem {
    std::string label;
    std::string kind;
    std::string bot_id;
    std::string coin;
    std::string value;
    int weekday = 0;
    int minute = 0;
    int duration_min = 0;
};

struct BackupInfo {
    bool ok = false;
    std::string message;
    std::string usb_h;
    std::string need_h;
};

struct VersionInfo {
    bool ok = false;
    bool up_to_date = false;
    std::string running;
    std::string tree;
    std::string github;
    std::string published;
    std::string image;
    std::string status;
    std::string html_url;
    std::vector<std::string> notes;
};

struct AuditPos {
    std::string coin;
    std::string side;
    double size = 0;
    double entry = 0;
    double upnl = 0;
};

struct AuditTrader {
    int num = 0;
    std::string address;
    std::string short_addr;
    std::string audited_at;
    std::string cap;
    std::string sleep;
    std::string last_open;
    std::string error;
    double equity = 0;
    double free_margin = 0;
    double upnl = 0;
    double sleep_hours = 0;
    int n_pos = 0;
    bool can_open = false;
    bool stale = false;
    bool ok = true;
    std::vector<AuditPos> positions;
};

struct CopyAudit {
    bool ok = false;
    bool worker = false;
    std::string updated;
    std::string timezone;
    std::string log_mtime;
    std::string message;
    std::string schedule;
    std::vector<AuditTrader> traders;
    std::vector<std::string> history;
};

struct DeskSnap {
    std::vector<Bot> bots;
    std::vector<Position> positions;
    Totals totals;
    std::string ts;
    std::string error;
    std::map<std::string, std::vector<CtrlField>> fields;
    RateInfo rate;
    HlKey hl_key;
    HostHealth host;
    NetHealth net;
    RiskInfo risk;
    DeskBiasInfo desk_bias;
    CondorInfo condor;
    std::vector<Leader> leaders;
    std::map<std::string, double> mids;
    std::vector<WireNode> wire_nodes;
    std::vector<WireEdge> wire_edges;
    std::vector<ToolItem> tools;
    std::string wire_board = "desk";
    std::vector<Wallet> wallets;
    std::vector<QaItem> qa;
    BackupInfo backup;
    VersionInfo version;
    std::string wire_error;
    std::string qa_message;
    CompileResult compile;
    CopyAudit copy_audit;
    bool writes = true;              // Flask true; Spring from health
    bool closes = false;
};

class FlaskApi {
public:
    std::string host = "127.0.0.1";
    int port = 5004;
    bool read_only = true;           // matrix talks to Spring daemon
    bool writes = false;             // Spring WRITE MODE (from /api/health)
    std::string backend = "cpp-debug";   // flask | cpp-debug
    std::string flask_host = "127.0.0.1";
    int flask_port = 5001;
    std::string cpp_host = "127.0.0.1";
    int cpp_port = 5004;

    bool ping();                     // GET /api/health on current host:port
    bool can_post() const;           // Flask, or Spring with WRITE MODE
    std::string use_flask();         // live Flask — empty on ok, else error
    std::string use_cpp();           // Spring — empty on ok, else error
    std::string post_sl(const std::string& id, double pct);
    std::string post_restart(const std::string& id);
    // active false = docker pause (0 HL IP). active true = docker unpause only.
    // Does not need WRITE MODE. Never closes or starts a stopped hunter.
    std::string post_hunter_gate(const std::string& id, bool active);
    std::string post_desk_bias(const std::string& bias);
    std::string post_bias(const std::string& id, const std::string& coin, const std::string& bias);
    std::string post_close_coin(const std::string& id, const std::string& coin);
    std::string post_open100(const std::string& id, const std::string& coin, const std::string& side = {});
    std::string post_ctrl(const std::string& id, const std::string& key, const nlohmann::json& value);
    std::string post_ctrl_map(const std::string& id, const nlohmann::json& values);
    std::string post_copy_mute(int trader, bool mute);
    std::string post_copy_slots(int trader, int slots);
    std::string load_copy_audit(DeskSnap& snap);
    std::string run_copy_audit(const std::string& trader = "all");
    void post_live_knobs(const std::string& id, const nlohmann::json& ctrl);
    bool pull_live_knobs(Bot& b);
    nlohmann::json load_presets(const std::string& id);
    std::string save_preset(const std::string& id, const std::string& name, const nlohmann::json& values);
    nlohmann::json apply_preset(const std::string& id, const std::string& name);
    std::string post_wire_pack(const std::string& action);

    DeskSnap home();
    void load_fields(DeskSnap& snap);
    void load_wire(DeskSnap& snap, const std::string& board = "desk");
    void load_qa(DeskSnap& snap);
    void load_backup(DeskSnap& snap);
    void load_version(DeskSnap& snap);

    // Wire canvas writes. Compile does not change hunters.
    // Integrate applies routing/SL map only if compile says it will not close clips.
    nlohmann::json graph_json(const DeskSnap& snap);
    std::string compile_wire(DeskSnap& snap, bool workaround = false);
    std::string integrate_wire(DeskSnap& snap);
    std::string save_snapshot(const DeskSnap& snap);
    std::string save_layout(const DeskSnap& snap);
    std::string sleeper_start(const std::string& id);
    std::string sleeper_stop(const std::string& id);
};

double display_value(const CtrlField& f, const nlohmann::json& stored);
std::string json_preview(const nlohmann::json& stored);
