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

#include "brain.hpp"
#include "hl_key.hpp"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

#include <atomic>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>

static std::atomic<int> g_live{0};
static const int kMaxLive = 64;

static bool send_all(int fd, const std::string& s) {
    size_t off = 0;
    while (off < s.size()) {
        ssize_t n = send(fd, s.data() + off, s.size() - off, 0);
        if (n <= 0) return false;
        off += static_cast<size_t>(n);
    }
    return true;
}

static const char* reason_for(int status) {
    switch (status) {
        case 200: return "OK";
        case 403: return "Forbidden";
        case 404: return "Not Found";
        case 500: return "Internal Server Error";
        default: return "Error";
    }
}

static void client(int fd, Brain brain) {
    timeval tv{};
    tv.tv_sec = 8;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    std::string raw;
    char buf[4096];
    for (;;) {
        ssize_t n = recv(fd, buf, sizeof(buf), 0);
        if (n <= 0) break;
        raw.append(buf, static_cast<size_t>(n));
        if (raw.size() > 2 * 1024 * 1024) break;
        auto sep = raw.find("\r\n\r\n");
        if (sep == std::string::npos) continue;
        size_t clen = 0;
        auto cl = raw.find("Content-Length:");
        if (cl == std::string::npos) cl = raw.find("content-length:");
        if (cl != std::string::npos && cl < sep)
            clen = static_cast<size_t>(atoi(raw.c_str() + cl + 15));
        if (clen > 1024 * 1024) clen = 1024 * 1024;
        if (raw.size() >= sep + 4 + clen) break;
    }
    auto sep = raw.find("\r\n\r\n");
    std::string method = "GET", target = "/", body;
    if (sep != std::string::npos) {
        auto line = raw.substr(0, raw.find("\r\n"));
        std::istringstream ls(line);
        ls >> method >> target;
        body = raw.substr(sep + 4);
    }
    std::string path = target, query;
    auto q = target.find('?');
    if (q != std::string::npos) {
        path = target.substr(0, q);
        query = target.substr(q + 1);
    }
    int status = 200;
    auto json = brain.handle(method, path, query, body, status);
    std::ostringstream res;
    res << "HTTP/1.1 " << status << " " << reason_for(status) << "\r\n"
        << "Content-Type: application/json; charset=utf-8\r\n"
        << "Access-Control-Allow-Origin: *\r\n"
        << "Access-Control-Allow-Headers: Content-Type\r\n"
        << "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
        << "X-Rimbaud-Brain: spring\r\n"
        << "Connection: close\r\n"
        << "Content-Length: " << json.size() << "\r\n\r\n"
        << json;
    send_all(fd, res.str());
    close(fd);
}

int run_server(Brain& brain) {
    signal(SIGPIPE, SIG_IGN);
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return 1;
    int yes = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(static_cast<uint16_t>(brain.port));
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    // The desk on this machine stays on localhost. A container sets SPRING_BIND
    // so Docker can publish the port. An unreadable value stays on localhost.
    if (const char* bind_to = std::getenv("SPRING_BIND")) {
        in_addr chosen{};
        if (inet_pton(AF_INET, bind_to, &chosen) == 1) addr.sin_addr = chosen;
    }
    if (bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        perror("bind");
        close(fd);
        return 1;
    }
    listen(fd, 32);
    hl_key_start(brain);
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags >= 0) fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    std::cerr << "SPRING  debug read-only brain  :" << brain.port
              << "  conf=" << brain.conf << "\n"
              << "  GET only  ·  no hunter writes  ·  no HL orders  ·  Flask :5001 is live\n";
    for (;;) {
        if (spring_stop_requested()) break;
        int c = accept(fd, nullptr, nullptr);
        if (c < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) {
                timespec sl{0, 50 * 1000 * 1000};
                nanosleep(&sl, nullptr);
                continue;
            }
            continue;
        }
        if (g_live.load() >= kMaxLive) {
            close(c);
            continue;
        }
        g_live++;
        std::thread([c, brain] {
            try {
                client(c, brain);
            } catch (const std::exception& e) {
                std::cerr << "SPRING  worker exception: " << e.what() << "\n";
            } catch (...) {
                std::cerr << "SPRING  worker exception\n";
            }
            g_live--;
        }).detach();
    }
    close(fd);
    std::cerr << "SPRING  stopped  ·  hunters and Flask :5001 untouched\n";
    return 0;
}
