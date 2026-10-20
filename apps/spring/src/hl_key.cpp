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

#include "hl_key.hpp"
#include "brain.hpp"
#include "jsonio.hpp"

#include <dirent.h>
#include <sys/stat.h>

#include <chrono>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <fstream>
#include <iostream>
#include <sstream>
#include <thread>

using nlohmann::json;

static std::string parent_dir(const std::string& path) {
    auto slash = path.find_last_of('/');
    if (slash == std::string::npos) return ".";
    if (slash == 0) return "/";
    return path.substr(0, slash);
}

static std::string pub_path(const std::string& conf) {
    if (!conf.empty() && conf.back() == '/') return conf + "hl_api_pub.json";
    return conf + "/hl_api_pub.json";
}

static std::string stamp_of(const std::string& line) {
    if (line.size() < 19) return {};
    if (line[4] != '-' || line[7] != '-' || line[10] != ' ') return {};
    return line.substr(0, 19);
}

static std::string tail_text(const std::string& path, size_t n) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return {};
    in.seekg(0, std::ios::end);
    auto end = in.tellg();
    if (end <= 0) return {};
    auto off = static_cast<std::streamoff>(end) > static_cast<std::streamoff>(n)
        ? static_cast<std::streamoff>(end) - static_cast<std::streamoff>(n) : 0;
    in.seekg(off);
    return std::string((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
}

static std::string last_reject_line(const std::string& logs) {
    DIR* d = opendir(logs.c_str());
    if (!d) return {};
    std::string best_stamp, best_line;
    while (auto* ent = readdir(d)) {
        std::string name = ent->d_name;
        if (name.find("trader") == std::string::npos) continue;
        if (name.size() < 4 || name.substr(name.size() - 4) != ".log") continue;
        std::string body = tail_text(logs + "/" + name, 8192);
        auto pos = body.rfind("does not exist");
        if (pos == std::string::npos) continue;
        auto start = body.rfind('\n', pos);
        start = start == std::string::npos ? 0 : start + 1;
        auto stop = body.find('\n', pos);
        std::string line = body.substr(start, stop == std::string::npos ? std::string::npos : stop - start);
        if (line.size() > 220) line.resize(220);
        std::string stamp = stamp_of(line);
        if (stamp.empty()) continue;
        if (stamp >= best_stamp) {
            best_stamp = stamp;
            best_line = line;
        }
    }
    closedir(d);
    return best_line;
}

nlohmann::json hl_key_view(const std::string& conf) {
    json j = read_json(pub_path(conf));
    std::string on = jstr(j, "on_exchange");
    std::string err = jstr(j, "exchange_error");
    std::string reject = jstr(j, "reject");
    std::string state = "grey";
    if (!reject.empty() || on == "no") state = "red";
    else if (on == "named" || on == "primary") state = "green";
    json out = {
        {"state", state},
        {"on_exchange", on},
        {"address", jstr(j, "address")},
        {"agent_name", jstr(j, "agent_name")},
        {"checked_at", jstr(j, "checked_at")},
        {"error", reject.empty() ? err : reject},
    };
    if (j.contains("valid_until")) out["valid_until"] = j["valid_until"];
    return out;
}

static bool probe_due(const json& pub) {
    std::string checked = jstr(pub, "checked_at");
    if (checked.size() < 19) return true;
    std::tm tm{};
    if (!strptime(checked.c_str(), "%Y-%m-%d %H:%M:%S", &tm)) return true;
    time_t then = timegm(&tm);
    if (then <= 0) return true;
    return std::time(nullptr) - then >= 300;
}

static void run_probe(const Brain& br) {
    std::string script = parent_dir(br.conf) + "/scripts/hl_api_key.py";
    std::string cmd = "HL_IP_BUDGET_FILE='" + br.conf + "/hl_ip_budget.json' python3 '"
        + script + "' probe";
    FILE* fp = popen(cmd.c_str(), "r");
    if (!fp) {
        std::cerr << "HL KEY keep — probe did not start\n";
        return;
    }
    char buf[512];
    std::string last;
    while (fgets(buf, sizeof(buf), fp)) last = buf;
    pclose(fp);
    while (!last.empty() && (last.back() == '\n' || last.back() == '\r')) last.pop_back();
    if (!last.empty()) std::cerr << last << "\n";
}

static void note_reject(const Brain& br, const std::string& line) {
    std::string path = pub_path(br.conf);
    json j = read_json(path);
    if (!j.is_object()) j = json::object();
    if (jstr(j, "reject") == line) return;
    std::string checked = jstr(j, "checked_at");
    std::string stamp = stamp_of(line);
    if (!checked.empty() && !stamp.empty() && stamp <= checked.substr(0, 19)) return;
    j["on_exchange"] = "no";
    j["reject"] = line;
    j["exchange_error"] = "signer rejected";
    if (!write_json(path, j)) return;
    std::cerr << "HL KEY red — " << line << "\n";
}

void hl_key_start(const Brain& brain) {
    std::thread([brain] {
        std::cerr << "HL KEY probe every 5 min — extraAgents weight 20, one machine\n";
        for (;;) {
            if (spring_stop_requested()) break;
            json pub = read_json(pub_path(brain.conf));
            if (probe_due(pub)) run_probe(brain);
            auto line = last_reject_line(parent_dir(brain.conf) + "/logs");
            if (!line.empty()) note_reject(brain, line);
            for (int i = 0; i < 30; ++i) {
                if (spring_stop_requested()) return;
                std::this_thread::sleep_for(std::chrono::seconds(1));
            }
        }
    }).detach();
}
