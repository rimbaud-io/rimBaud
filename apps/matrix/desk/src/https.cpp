// Copyright 2026 Laurence Sterne
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

#include "https.hpp"

#include <openssl/err.h>
#include <openssl/ssl.h>

#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <cstring>
#include <sstream>

static void ssl_once() {
    static bool done = false;
    if (done) return;
    SSL_load_error_strings();
    OpenSSL_add_ssl_algorithms();
    done = true;
}

static bool timed(int fd, int sec) {
    timeval tv{};
    tv.tv_sec = sec;
    return setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv)) == 0
        && setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv)) == 0;
}

static int tcp_connect(const std::string& host, int port, int timeout_s) {
    addrinfo hints{};
    hints.ai_family = AF_INET; // skip IPv6 — it hangs on this host
    hints.ai_socktype = SOCK_STREAM;
    addrinfo* res = nullptr;
    std::string ps = std::to_string(port);
    if (getaddrinfo(host.c_str(), ps.c_str(), &hints, &res) != 0 || !res) return -1;
    int fd = -1;
    for (auto* p = res; p; p = p->ai_next) {
        fd = socket(p->ai_family, p->ai_socktype, p->ai_protocol);
        if (fd < 0) continue;
        timed(fd, timeout_s);
        if (connect(fd, p->ai_addr, p->ai_addrlen) == 0) break;
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    return fd;
}

static std::string decode_chunked(const std::string& in) {
    std::string out;
    size_t i = 0;
    while (i < in.size()) {
        auto nl = in.find("\r\n", i);
        if (nl == std::string::npos) break;
        unsigned long n = 0;
        try { n = std::stoul(in.substr(i, nl - i), nullptr, 16); } catch (...) { break; }
        i = nl + 2;
        if (n == 0) break;
        if (i + n > in.size()) break;
        out.append(in, i, n);
        i += n;
        if (i + 1 < in.size() && in[i] == '\r') i += 2;
    }
    return out;
}

HttpsResponse https_request(const std::string& host, int port,
                            const std::string& method, const std::string& path,
                            const std::string& body,
                            const std::string& content_type) {
    HttpsResponse out;
    ssl_once();
    int fd = tcp_connect(host, port, 5);
    if (fd < 0) {
        out.error = "connect " + host;
        return out;
    }
    SSL_CTX* ctx = SSL_CTX_new(TLS_client_method());
    if (!ctx) { close(fd); out.error = "ssl ctx"; return out; }
    SSL_CTX_set_min_proto_version(ctx, TLS1_2_VERSION);
    SSL_CTX_set_default_verify_paths(ctx);
    SSL* ssl = SSL_new(ctx);
    SSL_set_fd(ssl, fd);
    SSL_set_tlsext_host_name(ssl, host.c_str());
    SSL_set_verify(ssl, SSL_VERIFY_NONE, nullptr);
    if (SSL_connect(ssl) != 1) {
        out.error = "tls " + host;
        SSL_free(ssl);
        SSL_CTX_free(ctx);
        close(fd);
        return out;
    }
    std::ostringstream req;
    req << method << " " << path << " HTTP/1.1\r\n"
        << "Host: " << host << "\r\n"
        << "User-Agent: freiquell-chart/1\r\n"
        << "Accept: application/json\r\n"
        << "Connection: close\r\n";
    if (!body.empty()) {
        req << "Content-Type: " << content_type << "\r\n"
            << "Content-Length: " << body.size() << "\r\n";
    }
    req << "\r\n" << body;
    auto s = req.str();
    if (SSL_write(ssl, s.data(), static_cast<int>(s.size())) <= 0) {
        out.error = "ssl write";
        SSL_free(ssl); SSL_CTX_free(ctx); close(fd);
        return out;
    }
    std::string raw;
    char buf[4096];
    for (;;) {
        int n = SSL_read(ssl, buf, sizeof(buf));
        if (n <= 0) break;
        raw.append(buf, static_cast<size_t>(n));
        if (raw.size() > 4 * 1024 * 1024) break;
    }
    SSL_shutdown(ssl);
    SSL_free(ssl);
    SSL_CTX_free(ctx);
    close(fd);

    auto sep = raw.find("\r\n\r\n");
    if (sep == std::string::npos) { out.error = "bad http"; return out; }
    auto head = raw.substr(0, sep);
    out.body = raw.substr(sep + 4);
    if (head.compare(0, 5, "HTTP/") == 0) {
        auto sp = head.find(' ');
        if (sp != std::string::npos) out.status = atoi(head.c_str() + sp + 1);
    }
    auto low = head;
    for (char& ch : low) if (ch >= 'A' && ch <= 'Z') ch = static_cast<char>(ch + 32);
    if (low.find("transfer-encoding: chunked") != std::string::npos)
        out.body = decode_chunked(out.body);
    return out;
}
