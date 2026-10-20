// Copyright 2026 Laurence Sterne www.rimbaud.io
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

#include "http.hpp"

#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <sstream>

static bool timed_ms(int fd, int ms) {
    if (ms < 20) ms = 20;
    timeval tv{};
    tv.tv_sec = ms / 1000;
    tv.tv_usec = (ms % 1000) * 1000;
    return setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv)) == 0
        && setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv)) == 0;
}

static void sock_fast(int fd) {
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
}

static int connect_host(const std::string& host, int port, int timeout_ms) {
    if (host == "127.0.0.1" || host == "localhost" || host == "::1") {
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0) return -1;
        timed_ms(fd, timeout_ms);
        sock_fast(fd);
        sockaddr_in a{};
        a.sin_family = AF_INET;
        a.sin_port = htons(static_cast<uint16_t>(port));
        inet_pton(AF_INET, "127.0.0.1", &a.sin_addr);
        if (connect(fd, reinterpret_cast<sockaddr*>(&a), sizeof(a)) == 0) return fd;
        close(fd);
        return -1;
    }
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo* res = nullptr;
    std::string port_s = std::to_string(port);
    if (getaddrinfo(host.c_str(), port_s.c_str(), &hints, &res) != 0 || !res) return -1;
    int fd = -1;
    for (auto* p = res; p; p = p->ai_next) {
        fd = socket(p->ai_family, p->ai_socktype, p->ai_protocol);
        if (fd < 0) continue;
        timed_ms(fd, timeout_ms);
        sock_fast(fd);
        if (connect(fd, p->ai_addr, p->ai_addrlen) == 0) break;
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    return fd;
}

HttpResponse http_request(const std::string& host, int port,
                          const std::string& method, const std::string& path,
                          const std::string& json_body,
                          int timeout_ms) {
    HttpResponse out;
    int fd = connect_host(host, port, timeout_ms);
    if (fd < 0) {
        out.error = "cannot connect " + host + ":" + std::to_string(port);
        return out;
    }

    std::ostringstream req;
    req << method << " " << path << " HTTP/1.1\r\n"
        << "Host: " << host << ":" << port << "\r\n"
        << "Accept: application/json\r\n"
        << "Connection: close\r\n";
    if (!json_body.empty()) {
        req << "Content-Type: application/json\r\n"
            << "Content-Length: " << json_body.size() << "\r\n";
    }
    req << "\r\n" << json_body;
    auto s = req.str();
    if (send(fd, s.data(), s.size(), 0) < 0) {
        out.error = "send failed";
        close(fd);
        return out;
    }

    std::string raw;
    char buf[4096];
    for (;;) {
        ssize_t n = recv(fd, buf, sizeof(buf), 0);
        if (n <= 0) break;
        raw.append(buf, static_cast<size_t>(n));
        if (raw.size() > 4 * 1024 * 1024) break;
    }
    close(fd);

    auto sep = raw.find("\r\n\r\n");
    if (sep == std::string::npos) {
        out.error = "bad http";
        return out;
    }
    auto head = raw.substr(0, sep);
    out.body = raw.substr(sep + 4);
    if (head.compare(0, 5, "HTTP/") == 0) {
        auto sp = head.find(' ');
        if (sp != std::string::npos) out.status = atoi(head.c_str() + sp + 1);
    }
    return out;
}
