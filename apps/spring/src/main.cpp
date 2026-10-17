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

#include "brain.hpp"

#include <cstdlib>
#include <iostream>
#include <string>
#include <unistd.h>

static bool is_conf(const std::string& p) {
    return access((p + "/wire_registry.json").c_str(), R_OK) == 0
        || access((p + "/bot_sl.json").c_str(), R_OK) == 0;
}

int main(int argc, char** argv) {
    Brain br;
    br.port = 5004;
    bool debug = false;
    if (const char* e = getenv("FREIQUELL_CONF")) br.conf = e;
    if (const char* e = getenv("FREIQUELL_DEBUG_PORT")) br.port = atoi(e);

    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--debug") debug = true;
        else if ((a == "--port" || a == "-p") && i + 1 < argc) br.port = atoi(argv[++i]);
        else if ((a == "--conf" || a == "-c") && i + 1 < argc) br.conf = argv[++i];
        else if (a == "--help") {
            std::cout <<
                "spring — DEBUG read-only C++ brain (not Flask)\n"
                "  Flask :5001 stays the live brain. Hunters stay in Docker.\n"
                "  Spring only READS conf JSON. It never writes BIAS / SL / ctrl /\n"
                "  wire graph, never restarts containers, never talks to Hyperliquid.\n"
                "  Positions cannot be opened, closed, or resized from here.\n\n"
                "  Required:  --debug\n"
                "  --port 5004     refuse 5001\n"
                "  --conf PATH     hummingbot conf/ (default ../conf)\n\n"
                "  Point the matrix at it:\n"
                "    ../freiquell_c++/freiquell-desk --debug\n"
                "    or click SPRING in the matrix header.\n";
            return 0;
        }
    }

    if (!debug) {
        std::cerr << "spring: refusing to start without --debug\n"
                  << "this is a read-only debug stand-in, not production Flask.\n";
        return 1;
    }
    br.debug = true;
    if (br.port == 5001) {
        std::cerr << "spring: refusing port 5001 — that is live Flask. Use 5004.\n";
        return 1;
    }
    if (br.port < 1 || br.port > 65535) {
        std::cerr << "spring: bad port\n";
        return 1;
    }
    if (br.conf.empty()) {
        const char* guesses[] = {"../conf", "conf", "/home/gerdy/hummingbird/hummingbot/conf"};
        for (auto g : guesses) if (is_conf(g)) { br.conf = g; break; }
    }
    if (br.conf.empty() || !is_conf(br.conf)) {
        std::cerr << "spring: cannot find conf/ (wire_registry.json). Use --conf\n";
        return 1;
    }
    char resolved[4096];
    if (realpath(br.conf.c_str(), resolved)) br.conf = resolved;
    return run_server(br);
}
