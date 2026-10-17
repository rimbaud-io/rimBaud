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

// rimbaud-archy — version desk + CLI.
// Grok:  ./rimbaud-archy --bump-g "why" [files]
// You:   ./rimbaud-archy            → X11 LEVEL UP 0.1 → 0.2
// Never CLOSE/OPEN Hyperliquid.
#include <cstdlib>
#include <cstring>
#include <string>
#include <unistd.h>

int archy_desk_main();

static std::string py() {
    if (const char* e = getenv("ARCHY_DB_PY")) return e;
    char buf[512];
    ssize_t n = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
    if (n > 0) {
        buf[n] = 0;
        std::string exe(buf);
        auto s = exe.rfind('/');
        if (s != std::string::npos) return exe.substr(0, s) + "/archy_db.py";
    }
    return "archy_db.py";
}

int main(int argc, char** argv) {
    if (argc >= 2 && argv[1] && argv[1][0] == '-' &&
        std::strcmp(argv[1], "--help") != 0 && std::strcmp(argv[1], "-h") != 0) {
        if (std::strcmp(argv[1], "--level-up") == 0 || std::strcmp(argv[1], "level-up") == 0) {
            fprintf(stderr, "LEVEL UP is operator-only in the rimbaud-archy window. Grok uses --bump-g.\n");
            return 3;
        }
        std::string script = py();
        const char* args[16];
        int n = 0;
        args[n++] = "python3";
        args[n++] = script.c_str();
        for (int i = 1; i < argc && n < 14; i++) args[n++] = argv[i];
        args[n] = nullptr;
        execvp("python3", const_cast<char* const*>(args));
        perror("python3 archy_db.py");
        return 127;
    }
    if (argc >= 2 && (std::strcmp(argv[1], "--help") == 0 || std::strcmp(argv[1], "-h") == 0)) {
        fprintf(stdout,
                "rimbaud-archy  version toolkit\n"
                "  (no args)              X11 desk — LEVEL UP is you only\n"
                "  --status               print current version JSON\n"
                "  --bump-g MSG [files]   Grok edit: 0.1 → 0.1g1 → 0.1g2\n"
                "  --record-deploy SITE   after live-push to a matrix\n"
                "Never CLOSE/OPEN. Never 0.1→0.2 from Grok.\n");
        return 0;
    }
    return archy_desk_main();
}
