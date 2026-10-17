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
#include <chrono>
#include <cstdio>
#include <ctime>
#include <fstream>
#include <iterator>
#include <string>

// JSON helpers. Config writes are opt-in and never send HL close/open.

inline std::string utc_now() {
    auto t = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    std::tm tm{};
    gmtime_r(&t, &tm);
    char buf[40];
    std::strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S UTC", &tm);
    return buf;
}

inline bool write_json(const std::string& path, const nlohmann::json& j) {
    if (path.find("..") != std::string::npos) return false;
    std::string tmp = path + ".tmp";
    {
        std::ofstream out(tmp);
        if (!out) return false;
        out << j.dump(2, ' ', false, nlohmann::json::error_handler_t::replace) << "\n";
        out.flush();
        if (!out) return false;
    }
    return std::rename(tmp.c_str(), path.c_str()) == 0;
}

inline nlohmann::json read_json(const std::string& path) {
    std::ifstream in(path);
    if (!in) return nlohmann::json::object();
    try {
        nlohmann::json j;
        in >> j;
        return j.is_object() || j.is_array() ? j : nlohmann::json::object();
    } catch (...) {
        return nlohmann::json::object();
    }
}

inline std::string slurp(const std::string& path, size_t max_bytes = 65536) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return {};
    in.seekg(0, std::ios::end);
    auto end = in.tellg();
    if (end < 0) return {};
    auto sz = static_cast<size_t>(end);
    size_t off = sz > max_bytes ? sz - max_bytes : 0;
    in.seekg(static_cast<std::streamoff>(off));
    if (!in) return {};
    return std::string((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
}

inline double jnum(const nlohmann::json& j, const char* k, double d = 0) {
    if (!j.contains(k) || j[k].is_null()) return d;
    try {
        if (j[k].is_number()) return j[k].get<double>();
        if (j[k].is_string()) return std::stod(j[k].get<std::string>());
    } catch (...) {}
    return d;
}

inline std::string jstr(const nlohmann::json& j, const char* k, const std::string& d = {}) {
    if (!j.contains(k) || j[k].is_null()) return d;
    try {
        if (j[k].is_string()) return j[k].get<std::string>();
        if (j[k].is_number()) return std::to_string(j[k].get<double>());
        if (j[k].is_boolean()) return j[k].get<bool>() ? "true" : "false";
    } catch (...) {}
    return d;
}

inline bool jbool(const nlohmann::json& j, const char* k, bool d = false) {
    if (!j.contains(k) || j[k].is_null()) return d;
    try {
        if (j[k].is_boolean()) return j[k].get<bool>();
        if (j[k].is_number()) return j[k].get<double>() != 0;
    } catch (...) {}
    return d;
}

inline bool jhas(const nlohmann::json& j, const char* k) {
    return j.contains(k) && !j[k].is_null();
}
