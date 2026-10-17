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

// springctl — small QjackCtl-style panel for the Spring debug brain.
// Start / Stop / Restart Spring only. Never touches Flask :5001 or hunters.

#include <nlohmann/json.hpp>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/keysym.h>

#include <arpa/inet.h>
#include <dirent.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
#include <ifaddrs.h>
#include <iterator>
#include <net/if.h>
#include <string>
#include <vector>

using nlohmann::json;

static const char* PID_PATH = "/tmp/freiquell-spring.pid";
static const char* CMD_LOG_PATH = "/tmp/freiquell-spring.log";

enum Source { SrcCommand, SrcDaemon };

static std::string home_dir() {
    const char* h = getenv("HOME");
    return h && h[0] ? h : ".";
}

static std::string source_path() {
    return home_dir() + "/.config/freiquell/springctl.source";
}

static Source load_source() {
    std::ifstream in(source_path());
    std::string s;
    if (in) std::getline(in, s);
    while (!s.empty() && (s.back() == ' ' || s.back() == '\r')) s.pop_back();
    return (s == "daemon") ? SrcDaemon : SrcCommand;
}

static void save_source(Source s) {
    std::string cfg = home_dir() + "/.config";
    mkdir(cfg.c_str(), 0755);
    std::string dir = cfg + "/freiquell";
    mkdir(dir.c_str(), 0755);
    std::ofstream out(source_path());
    if (out) out << (s == SrcDaemon ? "daemon\n" : "command\n");
}

static bool file_ok(const std::string& p) { return access(p.c_str(), R_OK) == 0; }

static bool daemon_user_installed() {
    return file_ok(home_dir() + "/.config/systemd/user/spring.service");
}

static bool daemon_sys_installed() {
    return file_ok("/etc/systemd/system/spring.service");
}

static bool daemon_installed() { return daemon_user_installed() || daemon_sys_installed(); }

static std::string daemon_log_path() {
    if (daemon_user_installed())
        return home_dir() + "/.local/share/spring/spring.log";
    return "/var/log/spring/spring.log";
}

static int sysctl(const char* verb, bool user) {
    std::string cmd = user ? "systemctl --user " : "systemctl ";
    cmd += verb;
    cmd += " spring.service >/tmp/springctl-sys.log 2>&1";
    return std::system(cmd.c_str());
}

static bool daemon_active(bool user) {
    std::string cmd = user ? "systemctl --user is-active --quiet spring.service"
                           : "systemctl is-active --quiet spring.service";
    return std::system(cmd.c_str()) == 0;
}

static std::string ascii(const std::string& s, int maxn = 0);

static bool pid_is_daemon(pid_t pid) {
    if (!pid) return false;
    char p[80];
    snprintf(p, sizeof(p), "/proc/%d/cgroup", pid);
    std::ifstream in(p);
    std::string s((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    return s.find("spring.service") != std::string::npos;
}

static std::vector<std::string> tail_file(const std::string& path, int n, int width) {
    std::ifstream in(path);
    std::vector<std::string> all, out;
    if (!in) return out;
    std::string line;
    while (std::getline(in, line)) {
        all.push_back(ascii(line, width));
        if (all.size() > 200) all.erase(all.begin(), all.begin() + 50);
    }
    int from = std::max(0, static_cast<int>(all.size()) - n);
    for (int i = from; i < static_cast<int>(all.size()); ++i) out.push_back(all[i]);
    return out;
}

struct Rect { int x, y, w, h; };
bool inside(const Rect& r, int x, int y) {
    return x >= r.x && y >= r.y && x < r.x + r.w && y < r.y + r.h;
}

static std::string ascii(const std::string& s, int maxn) {
    std::string o;
    for (unsigned char c : s) {
        if (c >= 32 && c < 127) o += static_cast<char>(c);
        else if (c == '\n' || c == '\t') o += ' ';
    }
    if (maxn > 0 && static_cast<int>(o.size()) > maxn) o.resize(static_cast<size_t>(maxn));
    return o;
}

static unsigned long rgb(Display* d, Colormap cm, int r, int g, int b) {
    XColor c{};
    c.red = r * 257; c.green = g * 257; c.blue = b * 257;
    c.flags = DoRed | DoGreen | DoBlue;
    XAllocColor(d, cm, &c);
    return c.pixel;
}

static json parse_or(const std::string& s) {
    try { return json::parse(s.empty() ? "{}" : s); } catch (...) { return json::object(); }
}

static std::string get_json(int port, const std::string& path, int timeout_s = 2) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return {};
    timeval tv{}; tv.tv_sec = timeout_s;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons(static_cast<uint16_t>(port));
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(fd, reinterpret_cast<sockaddr*>(&a), sizeof(a)) != 0) {
        close(fd);
        return {};
    }
    std::string req = "GET " + path + " HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    if (send(fd, req.data(), req.size(), 0) < 0) { close(fd); return {}; }
    std::string raw;
    char buf[4096];
    for (;;) {
        ssize_t n = recv(fd, buf, sizeof(buf), 0);
        if (n <= 0) break;
        raw.append(buf, static_cast<size_t>(n));
        if (raw.size() > 2 * 1024 * 1024) break;
    }
    close(fd);
    auto sep = raw.find("\r\n\r\n");
    if (sep == std::string::npos) return {};
    return raw.substr(sep + 4);
}

static std::string post_json(int port, const std::string& path, const std::string& body, int timeout_s = 3) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return {};
    timeval tv{}; tv.tv_sec = timeout_s;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_port = htons(static_cast<uint16_t>(port));
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(fd, reinterpret_cast<sockaddr*>(&a), sizeof(a)) != 0) {
        close(fd);
        return {};
    }
    std::string req = "POST " + path + " HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        "Content-Type: application/json\r\nConnection: close\r\n"
        "Content-Length: " + std::to_string(body.size()) + "\r\n\r\n" + body;
    if (send(fd, req.data(), req.size(), 0) < 0) { close(fd); return {}; }
    std::string raw;
    char buf[4096];
    for (;;) {
        ssize_t n = recv(fd, buf, sizeof(buf), 0);
        if (n <= 0) break;
        raw.append(buf, static_cast<size_t>(n));
        if (raw.size() > 256 * 1024) break;
    }
    close(fd);
    auto sep = raw.find("\r\n\r\n");
    if (sep == std::string::npos) return {};
    return raw.substr(sep + 4);
}

static bool flask_up() {
    auto b = get_json(5001, "/api/health", 1);
    return !b.empty();
}

static pid_t find_spring_pid() {
    DIR* d = opendir("/proc");
    if (!d) return 0;
    pid_t found = 0;
    while (auto* e = readdir(d)) {
        if (e->d_name[0] < '1' || e->d_name[0] > '9') continue;
        pid_t pid = static_cast<pid_t>(atoi(e->d_name));
        char pcomm[64];
        snprintf(pcomm, sizeof(pcomm), "/proc/%d/comm", pid);
        std::ifstream in(pcomm);
        std::string comm;
        std::getline(in, comm);
        if (comm != "spring") continue;
        char pcmd[64];
        snprintf(pcmd, sizeof(pcmd), "/proc/%d/cmdline", pid);
        std::ifstream cmd(pcmd);
        std::string c((std::istreambuf_iterator<char>(cmd)), std::istreambuf_iterator<char>());
        if (c.find("5001") != std::string::npos) continue;
        found = pid;
        break;
    }
    closedir(d);
    return found;
}

static bool wait_gone(pid_t pid, int ms) {
    for (int i = 0; i < ms / 50; ++i) {
        if (kill(pid, 0) != 0 && errno == ESRCH) return true;
        struct timespec sl{0, 50 * 1000 * 1000};
        nanosleep(&sl, nullptr);
    }
    return kill(pid, 0) != 0 && errno == ESRCH;
}

struct Ctl {
    std::string bin;
    std::string conf;
    int port = 5004;
    bool running = false;
    pid_t pid = 0;
    double ip_pct = 0;
    double ip_used = 0;
    double ip_cap = 1200;
    int n_ok = 0, n_bots = 0, n_pos = 0;
    bool flask = false;
    bool writes = false;
    bool vpn = false;
    bool vpn_up = false;
    std::string vpn_ip;
    std::string lan_ip;
    std::string hl_ip;
    double hl_ip_ts = 0;
    Source want = SrcCommand;
    Source live = SrcCommand;
    bool daemon_ok = false;
    bool daemon_user = false;
    std::string log_path;
    std::vector<std::string> log_lines;
    std::string note;
    std::string err;
    std::string hl_state = "grey";
    std::string hl_err;
    std::string hl_name;
};

static std::string vpn_flag_path(const Ctl& c) {
    return c.conf.empty() ? std::string() : (c.conf + "/vpn/use_vpn.json");
}

static bool load_vpn_flag(const Ctl& c) {
    auto p = vpn_flag_path(c);
    if (p.empty()) return false;
    std::ifstream in(p);
    if (!in) return false;
    try {
        json j;
        in >> j;
        return j.value("enabled", false);
    } catch (...) { return false; }
}

static void save_vpn_flag(const Ctl& c, bool on) {
    if (c.conf.empty()) return;
    std::string dir = c.conf + "/vpn";
    mkdir(dir.c_str(), 0700);
    json j = {
        {"enabled", on},
        {"closes", false},
        {"note", "VPN is control-plane 10.66.80.0/24. Hunters keep this box public IP (HL tokens)."},
    };
    std::ofstream out(vpn_flag_path(c));
    if (out) out << j.dump(2) << "\n";
}

static std::string ipv4_on_iface(const char* iface) {
    ifaddrs* ifa = nullptr;
    if (getifaddrs(&ifa) != 0) return {};
    std::string out;
    for (auto* p = ifa; p; p = p->ifa_next) {
        if (!p->ifa_addr || p->ifa_addr->sa_family != AF_INET) continue;
        if (iface && std::strcmp(p->ifa_name, iface) != 0) continue;
        char buf[INET_ADDRSTRLEN]{};
        auto* in = reinterpret_cast<sockaddr_in*>(p->ifa_addr);
        inet_ntop(AF_INET, &in->sin_addr, buf, sizeof(buf));
        std::string ip = buf;
        if (ip.rfind("127.", 0) == 0) continue;
        if (iface) { out = ip; break; }
        unsigned u = ntohl(in->sin_addr.s_addr);
        if ((u & 0xFFFF0000u) == 0xC0A80000u) { out = ip; break; } // 192.168
        if (out.empty()) out = ip;
    }
    freeifaddrs(ifa);
    return out;
}

static std::string fetch_hl_ip() {
    FILE* fp = popen("curl -fsS --max-time 2 https://api.ipify.org 2>/dev/null", "r");
    if (!fp) return {};
    char buf[64]{};
    if (!fgets(buf, sizeof(buf), fp)) { pclose(fp); return {}; }
    pclose(fp);
    std::string s = buf;
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r')) s.pop_back();
    for (char ch : s) {
        if (!((ch >= '0' && ch <= '9') || ch == '.')) return {};
    }
    return s;
}

static const char* load_word(double pct) {
    if (pct >= 80) return "HIGH";
    if (pct >= 50) return "MED";
    return "LOW";
}

static void refresh(Ctl& c) {
    c.pid = find_spring_pid();
    auto health = get_json(c.port, "/api/health", 1);
    auto hj = parse_or(health);
    c.running = !health.empty() && hj.value("ok", false);
    try { c.writes = hj.value("writes", false); } catch (...) { c.writes = false; }
    c.hl_state = "grey";
    c.hl_err.clear();
    c.hl_name.clear();
    if (hj.contains("hl_key") && hj["hl_key"].is_object()) {
        const auto& hk = hj["hl_key"];
        try { c.hl_state = hk.value("state", std::string("grey")); } catch (...) {}
        try { c.hl_err = hk.value("error", std::string()); } catch (...) {}
        try { c.hl_name = hk.value("agent_name", std::string()); } catch (...) {}
        if (c.hl_state != "green" && c.hl_state != "red") c.hl_state = "grey";
    }
    if (health.empty()) {
        c.ip_pct = 0; c.n_ok = 0; c.n_bots = 0; c.n_pos = 0;
        c.err = c.pid ? "pid live, port silent" : "stopped";
    } else {
        c.err.clear();
        auto home = parse_or(get_json(c.port, "/api/home", 2));
        auto rate = home.contains("rate") ? home["rate"] : json::object();
        c.ip_pct = rate.value("pct", 0.0);
        c.ip_used = rate.value("used", 0.0);
        c.ip_cap = rate.value("cap", 1200.0);
        auto tot = home.contains("totals") ? home["totals"] : json::object();
        c.n_ok = tot.value("n_ok", 0);
        c.n_bots = tot.value("n_bots", 0);
        c.n_pos = tot.value("n_pos", 0);
    }
    c.flask = flask_up();
    c.vpn = load_vpn_flag(c);
    c.vpn_ip = ipv4_on_iface("wg-rimbaud");
    c.vpn_up = !c.vpn_ip.empty();
    c.lan_ip = ipv4_on_iface(nullptr);
    if (c.vpn) {
        double t = static_cast<double>(time(nullptr));
        if (c.hl_ip.empty() || t - c.hl_ip_ts > 30) {
            auto ip = fetch_hl_ip();
            if (!ip.empty()) c.hl_ip = ip;
            c.hl_ip_ts = t;
        }
    }
    c.daemon_user = daemon_user_installed();
    c.daemon_ok = daemon_installed();
    c.live = pid_is_daemon(c.pid) ? SrcDaemon : SrcCommand;
    c.log_path = (c.live == SrcDaemon || c.want == SrcDaemon) ? daemon_log_path() : CMD_LOG_PATH;
    if (c.want == SrcCommand) c.log_path = CMD_LOG_PATH;
    if (c.running && c.live == SrcDaemon) c.log_path = daemon_log_path();
    int width = 52;
    c.log_lines = tail_file(c.log_path, 14, width);
}

static std::string stop_command() {
    pid_t pid = find_spring_pid();
    if (!pid) {
        unlink(PID_PATH);
        return "already stopped";
    }
    if (pid_is_daemon(pid))
        return "live Spring is the daemon — switch to DAEMON to stop it";
    char comm[64];
    snprintf(comm, sizeof(comm), "/proc/%d/comm", pid);
    std::ifstream in(comm);
    std::string name;
    std::getline(in, name);
    if (name != "spring") return "refusing to kill pid " + std::to_string(pid) + " (not spring)";
    kill(pid, SIGTERM);
    if (!wait_gone(pid, 1500)) {
        kill(pid, SIGKILL);
        wait_gone(pid, 500);
    }
    unlink(PID_PATH);
    return "stopped command pid " + std::to_string(pid);
}

static std::string stop_daemon(Ctl& c) {
    if (!c.daemon_ok) return "daemon not installed — ./daemon/install.sh";
    bool user = c.daemon_user;
    if (sysctl("stop", user) != 0) return "systemctl stop failed (need install --user or sudo)";
    return user ? "daemon stopped (user unit)" : "daemon stopped";
}

static std::string start_command(Ctl& c) {
    if (c.port == 5001) return "refusing port 5001";
    if (c.bin.empty() || access(c.bin.c_str(), X_OK) != 0) return "spring binary not found";
    if (c.conf.empty()) return "no conf/";
    pid_t pid = fork();
    if (pid < 0) return "fork failed";
    if (pid == 0) {
        setsid();
        int z = open("/dev/null", O_RDONLY);
        int lg = open(CMD_LOG_PATH, O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (z >= 0) { dup2(z, 0); close(z); }
        if (lg >= 0) { dup2(lg, 1); dup2(lg, 2); close(lg); }
        char port[16];
        snprintf(port, sizeof(port), "%d", c.port);
        execl(c.bin.c_str(), "spring", "--debug", "--conf", c.conf.c_str(),
              "--port", port, static_cast<char*>(nullptr));
        _exit(127);
    }
    FILE* pf = fopen(PID_PATH, "w");
    if (pf) { fprintf(pf, "%d\n", pid); fclose(pf); }
    for (int i = 0; i < 20; ++i) {
        struct timespec sl{0, 100 * 1000 * 1000};
        nanosleep(&sl, nullptr);
        auto h = parse_or(get_json(c.port, "/api/health", 1));
        if (h.value("ok", false)) return "command started pid " + std::to_string(pid);
    }
    return "command started pid " + std::to_string(pid) + " (health not up yet)";
}

static std::string start_daemon(Ctl& c) {
    if (!c.daemon_ok) return "daemon not installed — ./daemon/install.sh";
    if (sysctl("start", c.daemon_user) != 0) return "systemctl start failed";
    for (int i = 0; i < 20; ++i) {
        struct timespec sl{0, 100 * 1000 * 1000};
        nanosleep(&sl, nullptr);
        auto h = parse_or(get_json(c.port, "/api/health", 1));
        if (h.value("ok", false)) return "daemon started";
    }
    return "daemon start issued (health not up yet)";
}

static std::string stop_spring(Ctl& c) {
    refresh(c);
    if (c.want == SrcDaemon) return stop_daemon(c);
    return stop_command();
}

static std::string start_spring(Ctl& c) {
    if (c.port == 5001) return "refusing port 5001";
    refresh(c);
    if (c.running) {
        if ((c.want == SrcDaemon && c.live == SrcDaemon) ||
            (c.want == SrcCommand && c.live == SrcCommand))
            return "already running";
        // Wrong source owns the port — stop it first.
        if (c.live == SrcDaemon) stop_daemon(c);
        else stop_command();
        struct timespec sl{0, 250 * 1000 * 1000};
        nanosleep(&sl, nullptr);
    }
    if (c.want == SrcDaemon) return start_daemon(c);
    if (c.daemon_ok && daemon_active(c.daemon_user)) {
        stop_daemon(c);
        struct timespec sl{0, 250 * 1000 * 1000};
        nanosleep(&sl, nullptr);
    }
    return start_command(c);
}

static std::string switch_source(Ctl& c, Source want) {
    refresh(c);
    Source was_live = c.live;
    bool up = c.running;
    c.want = want;
    save_source(want);
    if (up && was_live != want) {
        std::string st = (was_live == SrcDaemon) ? stop_daemon(c) : stop_command();
        struct timespec sl{0, 250 * 1000 * 1000};
        nanosleep(&sl, nullptr);
        std::string st2 = start_spring(c);
        return std::string(want == SrcDaemon ? "DAEMON" : "COMMAND") + " · " + st + " · " + st2;
    }
    return want == SrcDaemon
        ? (c.daemon_ok ? "source DAEMON" : "source DAEMON — run ./daemon/install.sh")
        : "source COMMAND";
}

static bool is_conf(const std::string& p) {
    return access((p + "/wire_registry.json").c_str(), R_OK) == 0;
}

static std::string dir_of(const std::string& p) {
    auto s = p.rfind('/');
    return s == std::string::npos ? "." : p.substr(0, s);
}

int main(int argc, char** argv) {
    Ctl c;
    char self[4096]{};
    ssize_t n = readlink("/proc/self/exe", self, sizeof(self) - 1);
    std::string here = n > 0 ? dir_of(self) : ".";
    c.bin = here + "/spring";
    if (access(c.bin.c_str(), X_OK) != 0) c.bin = here + "/freiquell-server";
    std::string g1 = here + "/../conf";
    if (is_conf(g1)) c.conf = g1;
    else if (is_conf("/home/gerdy/hummingbird/hummingbot/conf"))
        c.conf = "/home/gerdy/hummingbird/hummingbot/conf";
    else if (is_conf("conf")) c.conf = "conf";
    else if (is_conf("../conf")) c.conf = "../conf";
    char resolved[4096];
    if (!c.conf.empty() && realpath(c.conf.c_str(), resolved)) c.conf = resolved;
    if (const char* e = getenv("FREIQUELL_CONF")) c.conf = e;
    if (const char* e = getenv("FREIQUELL_DEBUG_PORT")) c.port = atoi(e);
    c.want = load_source();

    bool headless = false;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--status") headless = true;
        else if (a == "--daemon" || a == "--source=daemon") { c.want = SrcDaemon; save_source(c.want); }
        else if (a == "--command" || a == "--source=command") { c.want = SrcCommand; save_source(c.want); }
        else if (a == "--start") { refresh(c); printf("%s\n", start_spring(c).c_str()); return 0; }
        else if (a == "--stop") { refresh(c); printf("%s\n", stop_spring(c).c_str()); return 0; }
        else if (a == "--restart") {
            refresh(c);
            printf("%s\n", stop_spring(c).c_str());
            struct timespec sl{0, 200 * 1000 * 1000};
            nanosleep(&sl, nullptr);
            printf("%s\n", start_spring(c).c_str());
            return 0;
        } else if ((a == "--port" || a == "-p") && i + 1 < argc) c.port = atoi(argv[++i]);
        else if (a == "--help") {
            printf("springctl — QjackCtl-style panel for Spring (debug C++ brain)\n"
                   "  Start/Stop/Restart Spring only. Flask :5001 and hunters stay.\n"
                   "  --status   print one snapshot\n"
                   "  --start --stop --restart\n"
                   "  --daemon / --command   which Spring is live (standard switch)\n");
            return 0;
        }
    }
    if (c.port == 5001) {
        fprintf(stderr, "springctl: refusing port 5001\n");
        return 1;
    }
    refresh(c);
    if (headless) {
        printf("running=%s source=%s live=%s pid=%d writes=%s vpn=%s vpn_ip=%s hl_ip=%s ip=%.0f%% %s hunters=%d/%d pos=%d flask=%s daemon=%s hl=%s\n",
               c.running ? "yes" : "no",
               c.want == SrcDaemon ? "daemon" : "command",
               c.live == SrcDaemon ? "daemon" : "command",
               c.pid, c.writes ? "on" : "off",
               c.vpn ? "on" : "off",
               c.vpn_ip.empty() ? "-" : c.vpn_ip.c_str(),
               c.hl_ip.empty() ? "-" : c.hl_ip.c_str(),
               c.ip_pct, load_word(c.ip_pct),
               c.n_ok, c.n_bots, c.n_pos, c.flask ? "live" : "down",
               c.daemon_ok ? (c.daemon_user ? "user" : "system") : "no",
               c.hl_state.c_str());
        return c.running ? 0 : 1;
    }

    Display* dpy = XOpenDisplay(nullptr);
    if (!dpy) {
        fprintf(stderr, "no DISPLAY — springctl --status\n");
        printf("running=%s pid=%d ip=%.0f%% %s\n",
               c.running ? "yes" : "no", c.pid, c.ip_pct, load_word(c.ip_pct));
        return 1;
    }
    int scr = DefaultScreen(dpy);
    Colormap cm = DefaultColormap(dpy, scr);
    unsigned long bg = rgb(dpy, cm, 18, 18, 18);
    unsigned long panel = rgb(dpy, cm, 36, 36, 36);
    unsigned long border = rgb(dpy, cm, 80, 80, 80);
    unsigned long text = rgb(dpy, cm, 220, 220, 220);
    unsigned long muted = rgb(dpy, cm, 140, 140, 140);
    unsigned long green = rgb(dpy, cm, 80, 200, 80);
    unsigned long gold = rgb(dpy, cm, 240, 185, 11);
    unsigned long rose = rgb(dpy, cm, 255, 90, 90);
    unsigned long orange = rgb(dpy, cm, 255, 118, 77);
    unsigned long cyan = rgb(dpy, cm, 80, 200, 220);
    unsigned long navy = rgb(dpy, cm, 28, 28, 28);

    int W = 560, H = 560;
    Window win = XCreateSimpleWindow(dpy, RootWindow(dpy, scr), 120, 80, W, H, 0, border, bg);
    XStoreName(dpy, win, "SPRING  (like QjackCtl)");
    XSelectInput(dpy, win, ExposureMask | StructureNotifyMask | ButtonPressMask | KeyPressMask);
    XMapWindow(dpy, win);
    GC gc = XCreateGC(dpy, win, 0, nullptr);
    XFontStruct* fs = XLoadQueryFont(dpy, "9x15");
    if (!fs) fs = XLoadQueryFont(dpy, "fixed");
    if (fs) XSetFont(dpy, gc, fs->fid);
    Pixmap buf = XCreatePixmap(dpy, win, W, H, DefaultDepth(dpy, scr));

    struct Hit { int id; Rect r; };
    std::vector<Hit> hits;
    double last = 0;
    auto now = []() {
        timespec ts{};
        clock_gettime(CLOCK_MONOTONIC, &ts);
        return ts.tv_sec + ts.tv_nsec / 1e9;
    };

    auto fill = [&](unsigned long col, Rect r) {
        XSetForeground(dpy, gc, col);
        XFillRectangle(dpy, buf, gc, r.x, r.y, r.w, r.h);
    };
    auto stroke = [&](unsigned long col, Rect r) {
        XSetForeground(dpy, gc, col);
        XDrawRectangle(dpy, buf, gc, r.x, r.y, r.w - 1, r.h - 1);
    };
    auto txt = [&](unsigned long col, int x, int y, const std::string& s) {
        auto a = ascii(s);
        if (a.empty()) return;
        XSetForeground(dpy, gc, col);
        XDrawString(dpy, buf, gc, x, y, a.c_str(), static_cast<int>(a.size()));
    };

    auto redraw = [&]() {
        hits.clear();
        fill(bg, {0, 0, W, H});
        fill(navy, {0, 0, W, 36});
        txt(orange, 12, 22, "SPRING");
        txt(muted, 80, 22, "debug brain  ·  not Flask");

        Rect lamp{12, 48, W - 24, 40};
        fill(panel, lamp);
        stroke(c.running ? green : rose, lamp);
        XSetForeground(dpy, gc, c.running ? green : rose);
        XFillArc(dpy, buf, gc, lamp.x + 10, lamp.y + 12, 16, 16, 0, 360 * 64);
        txt(c.running ? green : rose, lamp.x + 34, lamp.y + 17, c.running ? "Started" : "Stopped");
        char pidl[80];
        snprintf(pidl, sizeof(pidl), "pid %d   :%d   %s", c.pid, c.port,
                 c.running ? (c.live == SrcDaemon ? "DAEMON" : "COMMAND") : "—");
        txt(muted, lamp.x + 34, lamp.y + 33, pidl);
        {
            bool ok = c.hl_state == "green";
            bool bad = c.hl_state == "red";
            const char* word = ok ? "HL KEY  ok" : (bad ? "HL KEY  no" : "HL KEY  …");
            Rect hk{lamp.x + lamp.w - 158, lamp.y + 8, 146, 24};
            fill(ok ? rgb(dpy, cm, 8, 48, 22) : (bad ? rgb(dpy, cm, 48, 12, 16) : panel), hk);
            stroke(ok ? green : (bad ? rose : border), hk);
            int tw = static_cast<int>(strlen(word)) * 9;
            txt(ok ? green : (bad ? rose : muted), hk.x + (hk.w - tw) / 2, hk.y + 16, word);
        }

        txt(muted, 14, 108, "SOURCE");
        Rect sc{90, 94, 100, 22};
        Rect sd{198, 94, 100, 22};
        bool cmd_on = c.want == SrcCommand;
        bool dae_on = c.want == SrcDaemon;
        fill(cmd_on ? rgb(dpy, cm, 50, 28, 8) : panel, sc);
        stroke(cmd_on ? orange : border, sc);
        txt(cmd_on ? orange : muted, sc.x + 14, sc.y + 15, "COMMAND");
        fill(dae_on ? rgb(dpy, cm, 18, 40, 12) : panel, sd);
        stroke(dae_on ? green : (c.daemon_ok ? border : rose), sd);
        txt(dae_on ? green : muted, sd.x + 18, sd.y + 15, "DAEMON");
        hits.push_back({4, sc});
        hits.push_back({5, sd});
        txt(muted, 310, 108, c.daemon_ok ? (c.daemon_user ? "unit user" : "unit system") : "not installed");

        // IP load — QjackCtl DSP-load analog
        txt(muted, 14, 136, "IP LOAD");
        Rect bar{90, 124, W - 170, 14};
        fill(rgb(dpy, cm, 40, 40, 40), bar);
        int fw = static_cast<int>(std::min(1.0, std::max(0.0, c.ip_pct / 100.0)) * bar.w);
        unsigned long lc = c.ip_pct >= 80 ? rose : (c.ip_pct >= 50 ? gold : green);
        if (c.running && fw > 0) fill(lc, {bar.x, bar.y, fw, bar.h});
        stroke(border, bar);
        char lv[48];
        if (c.running) snprintf(lv, sizeof(lv), "%.0f%%  %s", c.ip_pct, load_word(c.ip_pct));
        else snprintf(lv, sizeof(lv), "n/a");
        txt(lc, bar.x + bar.w + 10, 136, lv);
        char used[64];
        snprintf(used, sizeof(used), "%.0f / %.0f  tokens / min", c.ip_used, c.ip_cap);
        txt(muted, 90, 154, c.running ? used : "start Spring to see load");

        char hun[80];
        snprintf(hun, sizeof(hun), "hunters  %d/%d     pos %d     Flask %s",
                 c.n_ok, c.n_bots, c.n_pos, c.flask ? "live :5001" : "down");
        txt(text, 14, 176, hun);

        Rect wr{16, 188, W - 32, 28};
        fill(panel, wr);
        stroke(c.writes ? gold : border, wr);
        Rect box{wr.x + 10, wr.y + 7, 14, 14};
        stroke(c.writes ? gold : muted, box);
        if (c.writes) fill(gold, {box.x + 3, box.y + 3, 8, 8});
        txt(c.writes ? gold : muted, wr.x + 32, wr.y + 18,
            c.writes ? "WRITE MODE on  (files only, never CLOSE/OPEN)"
                     : "WRITE MODE off  (read-only)");
        hits.push_back({3, wr});

        Rect vr{16, 222, W - 32, 28};
        fill(c.vpn ? rgb(dpy, cm, 8, 36, 40) : panel, vr);
        stroke(c.vpn ? cyan : border, vr);
        Rect vbox{vr.x + 10, vr.y + 7, 14, 14};
        stroke(c.vpn ? cyan : muted, vbox);
        if (c.vpn) fill(cyan, {vbox.x + 3, vbox.y + 3, 8, 8});
        txt(c.vpn ? cyan : muted, vr.x + 32, vr.y + 18,
            c.vpn ? "VPN  other machine  (control plane, hunters keep this IP)"
                  : "VPN  other machine  — tick to show new HL IP");
        hits.push_back({6, vr});

        int by = 256;
        if (c.vpn) {
            Rect hi{16, 256, W - 32, 48};
            fill(rgb(dpy, cm, 48, 36, 4), hi);
            stroke(gold, hi);
            txt(gold, hi.x + 10, hi.y + 16, "NEW  HL tokens IP");
            std::string hip = c.hl_ip.empty() ? "(lookup...)" : c.hl_ip;
            txt(gold, hi.x + 168, hi.y + 16, hip);
            char vln[96];
            if (c.vpn_up)
                snprintf(vln, sizeof(vln), "VPN Spring  %s:5004   LAN %s",
                         c.vpn_ip.c_str(), c.lan_ip.empty() ? "-" : c.lan_ip.c_str());
            else
                snprintf(vln, sizeof(vln), "VPN down  ·  sudo rimbaud-vpn join  ·  LAN %s",
                         c.lan_ip.empty() ? "-" : c.lan_ip.c_str());
            txt(c.vpn_up ? cyan : muted, hi.x + 10, hi.y + 36, vln);
            by = 312;
        }

        const char* labs[] = {"Start", "Stop", "Restart"};
        int bw = (W - 48) / 3;
        for (int i = 0; i < 3; ++i) {
            Rect r{16 + i * (bw + 8), by, bw, 28};
            bool on = (i == 0 && !c.running) || (i != 0 && c.running);
            fill(panel, r);
            stroke(on ? orange : border, r);
            int tw = static_cast<int>(strlen(labs[i])) * 9;
            txt(on ? orange : muted, r.x + (r.w - tw) / 2, r.y + 18, labs[i]);
            hits.push_back({i, r});
        }
        txt(muted, 14, by + 42, ascii(c.note.empty() ? c.err : c.note, 58));
        txt(muted, 14, by + 58, "s start  t stop  r restart  w writes  v vpn  d daemon  c command  q");

        Rect logp{12, by + 70, W - 24, H - (by + 82)};
        fill(rgb(dpy, cm, 12, 12, 12), logp);
        stroke(border, logp);
        txt(gold, logp.x + 8, logp.y + 14, "LOG");
        txt(muted, logp.x + 44, logp.y + 14, ascii(c.log_path, 48));
        int ly = logp.y + 28;
        std::vector<std::string> lines = c.log_lines;
        if (!c.hl_err.empty() && c.hl_state != "green") {
            std::string pin = (c.hl_state == "red" ? "HL KEY red — " : "HL KEY keep — ") + c.hl_err;
            bool have = false;
            for (const auto& ln : lines) {
                if (ln.find(c.hl_err) != std::string::npos) { have = true; break; }
            }
            if (!have) lines.insert(lines.begin(), pin);
        }
        if (lines.empty())
            txt(muted, logp.x + 8, ly, c.running ? "(no log lines yet)" : "(start Spring to see logs)");
        int clip = std::max(8, (logp.w - 16) / 9);
        for (auto& ln : lines) {
            if (ly + 12 > logp.y + logp.h - 4) break;
            bool good = ln.find("HL KEY green") != std::string::npos;
            bool bad = !good && (ln.find("HL KEY red") != std::string::npos
                || ln.find("does not exist") != std::string::npos
                || ln.find("HL KEY keep") != std::string::npos
                || ln.find("Traceback") != std::string::npos
                || ln.find("error") != std::string::npos
                || ln.find("Error") != std::string::npos);
            txt(good ? green : (bad ? rose : text), logp.x + 8, ly, ascii(ln, clip));
            ly += 14;
        }

        XCopyArea(dpy, buf, win, gc, 0, 0, W, H, 0, 0);
        XFlush(dpy);
    };

    last = now();
    redraw();
    bool run = true;
    while (run) {
        while (XPending(dpy)) {
            XEvent e;
            XNextEvent(dpy, &e);
            if (e.type == Expose) redraw();
            else if (e.type == ConfigureNotify && e.xconfigure.width > 200) {
                W = e.xconfigure.width;
                H = e.xconfigure.height;
                XFreePixmap(dpy, buf);
                buf = XCreatePixmap(dpy, win, W, H, DefaultDepth(dpy, scr));
                redraw();
            } else if (e.type == KeyPress) {
                KeySym ks = XLookupKeysym(&e.xkey, 0);
                if (ks == XK_q || ks == XK_Escape) run = false;
                else if (ks == XK_s) { c.note = start_spring(c); refresh(c); redraw(); }
                else if (ks == XK_t) { c.note = stop_spring(c); refresh(c); redraw(); }
                else if (ks == XK_d) { c.note = switch_source(c, SrcDaemon); refresh(c); redraw(); }
                else if (ks == XK_c) { c.note = switch_source(c, SrcCommand); refresh(c); redraw(); }
                else if (ks == XK_r) {
                    c.note = stop_spring(c);
                    struct timespec sl{0, 250 * 1000 * 1000};
                    nanosleep(&sl, nullptr);
                    c.note = start_spring(c);
                    refresh(c);
                    redraw();
                } else if (ks == XK_w) {
                    if (!c.running) { c.note = "start Spring first"; redraw(); }
                    else {
                        auto body = std::string("{\"enabled\":") + (c.writes ? "false" : "true") + "}";
                        auto resp = parse_or(post_json(c.port, "/api/spring/writes", body));
                        c.note = resp.value("message", "write mode toggled");
                        refresh(c);
                        redraw();
                    }
                } else if (ks == XK_v) {
                    c.vpn = !c.vpn;
                    save_vpn_flag(c, c.vpn);
                    c.hl_ip_ts = 0;
                    c.note = c.vpn
                        ? (c.vpn_up ? ("VPN on  ·  HL IP " + (c.hl_ip.empty() ? "lookup" : c.hl_ip))
                                    : "VPN on  ·  sudo rimbaud-vpn join  (does not steal hunter default route)")
                        : "VPN off  ·  this box is local-only";
                    refresh(c);
                    redraw();
                }
            } else if (e.type == ButtonPress && e.xbutton.button == 1) {
                int mx = e.xbutton.x, my = e.xbutton.y;
                for (auto& h : hits) {
                    if (!inside(h.r, mx, my)) continue;
                    if (h.id == 0) c.note = start_spring(c);
                    else if (h.id == 1) c.note = stop_spring(c);
                    else if (h.id == 2) {
                        c.note = stop_spring(c);
                        struct timespec sl{0, 250 * 1000 * 1000};
                        nanosleep(&sl, nullptr);
                        c.note = start_spring(c);
                    } else if (h.id == 3) {
                        if (!c.running) c.note = "start Spring first";
                        else {
                            auto body = std::string("{\"enabled\":") + (c.writes ? "false" : "true") + "}";
                            auto resp = parse_or(post_json(c.port, "/api/spring/writes", body));
                            c.note = resp.value("message", "write mode toggled");
                        }
                    } else if (h.id == 4) {
                        c.note = switch_source(c, SrcCommand);
                    } else if (h.id == 5) {
                        c.note = switch_source(c, SrcDaemon);
                    } else if (h.id == 6) {
                        c.vpn = !c.vpn;
                        save_vpn_flag(c, c.vpn);
                        c.hl_ip_ts = 0;
                        c.note = c.vpn
                            ? (c.vpn_up ? ("VPN on  ·  HL IP " + (c.hl_ip.empty() ? "lookup" : c.hl_ip))
                                        : "VPN on  ·  sudo rimbaud-vpn join  (hunters keep this public IP)")
                            : "VPN off  ·  this box is local-only";
                    }
                    refresh(c);
                    redraw();
                    break;
                }
            }
        }
        if (now() - last > 1.0) {
            last = now();
            refresh(c);
            redraw();
        }
        struct timespec slp{0, 30 * 1000 * 1000};
        nanosleep(&slp, nullptr);
    }
    XFreePixmap(dpy, buf);
    XFreeGC(dpy, gc);
    if (fs) XFreeFont(dpy, fs);
    XDestroyWindow(dpy, win);
    XCloseDisplay(dpy);
    return 0;
}
