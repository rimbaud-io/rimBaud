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

#include "api.hpp"
#include "ui.hpp"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

int main(int argc, char** argv) {
    FlaskApi api;
    api.host = "127.0.0.1";
    api.port = 5004;
    api.read_only = true;
    api.backend = "cpp-debug";
    api.cpp_host = "127.0.0.1";
    api.cpp_port = 5004;
    bool headless = false;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--headless") headless = true;
        else if ((a == "--host" || a == "-h") && i + 1 < argc) api.host = argv[++i];
        else if ((a == "--port" || a == "-p") && i + 1 < argc) api.port = atoi(argv[++i]);
        else if (a == "--debug" || a == "--cpp-debug") {
            api.host = "127.0.0.1";
            api.port = 5004;
            api.read_only = true;
            api.backend = "cpp-debug";
        } else if (a == "--cpp-server" && i + 1 < argc) {
            std::string spec = argv[++i];
            auto c = spec.find(':');
            if (c != std::string::npos) {
                api.host = spec.substr(0, c);
                api.port = atoi(spec.c_str() + c + 1);
            } else {
                api.host = spec;
                api.port = 5004;
            }
            api.read_only = true;
            api.backend = "cpp-debug";
        } else if (a == "--help") {
            fprintf(stdout,
                    "rimbaud-desk — native C++ matrix (Spring daemon :5004 only)\n"
                    "  Hunters stay in Docker. Flask :5001 still serves the website.\n\n"
                    "  --host 127.0.0.1   Spring host (or RIMBAUD_HOST / FREIQUELL_HOST)\n"
                    "  --port 5004        Spring daemon port (or RIMBAUD_PORT / FREIQUELL_PORT)\n"
                    "  --debug            same as default (Spring :5004)\n"
                    "  --cpp-server H:P   Spring host:port\n"
                    "  --headless         print /api/home and exit\n\n"
                    "  Matrix does not switch to Flask. Start/stop Spring in springctl.\n"
                    "  q / Esc            quit\n");
            return 0;
        }
    }
    if (const char* h = getenv("RIMBAUD_HOST")) api.host = h;
    else if (const char* h = getenv("FREIQUELL_HOST")) api.host = h;
    if (const char* p = getenv("RIMBAUD_PORT")) api.port = atoi(p);
    else if (const char* p = getenv("FREIQUELL_PORT")) api.port = atoi(p);
    if (api.port == 5001) {
        fprintf(stderr, "rimbaud-desk: refusing Flask :5001 — matrix uses Spring daemon :5004\n");
        api.port = 5004;
    }
    api.read_only = true;
    api.backend = "cpp-debug";
    api.cpp_host = api.host;
    api.cpp_port = api.port;

    if (headless) {
        dump_headless(api);
        return 0;
    }
    return run_desk(api);
}
