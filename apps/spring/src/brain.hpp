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
#include <string>

struct Brain {
    std::string conf;
    int port = 5004;
    bool debug = false;
    std::string handle(const std::string& method, const std::string& path,
                       const std::string& query, const std::string& body,
                       int& status);
};

void spring_request_stop();
bool spring_stop_requested();
int run_server(Brain& brain);
