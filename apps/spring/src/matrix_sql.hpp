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

#include <nlohmann/json.hpp>
#include <string>

// In-process SQLite for the hunter queue. No Python and no docker exec.
// Reads and writes conf/matrix.db. Does not place orders.

bool matrix_sql_apply(const std::string& conf, const nlohmann::json& job);
bool matrix_sql_snapshot(const std::string& conf, nlohmann::json& status, nlohmann::json& bias);
// Controller knobs and presets. The caller does not write a JSON mirror.
bool matrix_sql_kv_map(const std::string& conf, const std::string& bot, nlohmann::json& out);
bool matrix_sql_preset_list(const std::string& conf, const std::string& bot, nlohmann::json& list);
bool matrix_sql_preset_get(const std::string& conf, const std::string& bot, const std::string& name,
                           nlohmann::json& values, bool& found);
bool matrix_sql_preset_save(const std::string& conf, const std::string& bot, const std::string& name,
                            const nlohmann::json& values, bool locked, std::string& err);
