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

// rimbaud-meta-desk — X11 meta matrix. Same look as rimBaud desk.
// HOME: connected matrices (this box + VPN 10.66.80.x). GET only.
// DEPLOY: LIVE PUSH code to a client (remote) + USB kit or Mint Cinnamon ISO.
// Never CLOSE/OPEN. Never copies wallets / conf / .compose.env.
#include "http.hpp"
#include <nlohmann/json.hpp>

#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/Xatom.h>
#include <X11/keysym.h>
#include <X11/cursorfont.h>

#include <algorithm>
#include <arpa/inet.h>
#include <cctype>
#include <cerrno>
#include <utility>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <dirent.h>
#include <fcntl.h>
#include <fstream>
#include <iterator>
#include <map>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <sys/socket.h>
#include <string>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

using nlohmann::json;

static volatile sig_atomic_t g_run = 1;
static void on_int(int) { g_run = 0; }

struct Rect { int x, y, w, h; };
static bool inside(const Rect& r, int x, int y) {
    return x >= r.x && y >= r.y && x < r.x + r.w && y < r.y + r.h;
}

static unsigned long rgb(Display* d, Colormap cm, int r, int g, int b) {
    XColor c{};
    c.red = r * 257; c.green = g * 257; c.blue = b * 257;
    c.flags = DoRed | DoGreen | DoBlue;
    XAllocColor(d, cm, &c);
    return c.pixel;
}

static void hsv_rgb(double h, double s, double v, int& r, int& g, int& b) {
    if (h < 0) h = 0;
    h = fmod(h, 360.0);
    if (h < 0) h += 360.0;
    double c = v * s;
    double x = c * (1.0 - std::fabs(fmod(h / 60.0, 2.0) - 1.0));
    double m = v - c;
    double rf = 0, gf = 0, bf = 0;
    if (h < 60) { rf = c; gf = x; }
    else if (h < 120) { rf = x; gf = c; }
    else if (h < 180) { gf = c; bf = x; }
    else if (h < 240) { gf = x; bf = c; }
    else if (h < 300) { rf = x; bf = c; }
    else { rf = c; bf = x; }
    r = (int)std::lround((rf + m) * 255.0);
    g = (int)std::lround((gf + m) * 255.0);
    b = (int)std::lround((bf + m) * 255.0);
    if (r < 0) r = 0; if (r > 255) r = 255;
    if (g < 0) g = 0; if (g > 255) g = 255;
    if (b < 0) b = 0; if (b > 255) b = 255;
}

static std::string ascii(const std::string& s, int maxn = 0) {
    std::string o;
    for (unsigned char c : s) {
        if (c >= 32 && c < 127) o += static_cast<char>(c);
        else if (c == '\n' || c == '\t') o += ' ';
    }
    if (maxn > 0 && static_cast<int>(o.size()) > maxn) o.resize(static_cast<size_t>(maxn));
    return o;
}

static std::string strip_ansi(const std::string& s, int maxn = 0) {
    std::string o;
    for (size_t i = 0; i < s.size(); i++) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        if (c == 0x1b && i + 1 < s.size() && s[i + 1] == '[') {
            i += 2;
            while (i < s.size() && s[i] != 'm') i++;
            continue;
        }
        if (c == '\r') continue;
        if (c >= 32 && c < 127) o += static_cast<char>(c);
        else if (c == '\t') o += ' ';
    }
    if (maxn > 0 && static_cast<int>(o.size()) > maxn) o.resize(static_cast<size_t>(maxn));
    return o;
}

static std::string money(double n) {
    char b[48];
    snprintf(b, sizeof(b), "%s$%.2f", n < 0 ? "-" : "", std::fabs(n));
    return b;
}

static double jnum(const json& j, const char* k, double d = 0) {
    if (!j.contains(k) || j[k].is_null()) return d;
    try {
        if (j[k].is_number()) return j[k].get<double>();
        if (j[k].is_string()) return std::stod(j[k].get<std::string>());
    } catch (...) {}
    return d;
}
static std::string jstr(const json& j, const char* k, const std::string& d = "") {
    if (!j.contains(k) || j[k].is_null()) return d;
    try {
        if (j[k].is_string()) return j[k].get<std::string>();
        if (j[k].is_number() || j[k].is_boolean()) return json(j[k]).dump();
    } catch (...) {}
    return d;
}
static bool jflag(const json& j, const char* k, bool d = false) {
    if (!j.contains(k) || j[k].is_null()) return d;
    try {
        if (j[k].is_boolean()) return j[k].get<bool>();
        if (j[k].is_number()) return j[k].get<double>() != 0;
    } catch (...) {}
    return d;
}

static std::string conf_dir() {
    if (const char* e = getenv("RIMBAUD_CONF")) return e;
    if (const char* e = getenv("FREIQUELL_CONF")) return e;
    return "conf";
}
static std::string repo_root() {
    auto c = conf_dir();
    auto s = c.rfind('/');
    return s == std::string::npos ? "." : c.substr(0, s);
}

static std::string utc_stamp() {
    char b[40];
    time_t t = time(nullptr);
    strftime(b, sizeof(b), "%Y-%m-%d %H:%M:%S UTC", gmtime(&t));
    return b;
}

static std::string iphone_gw_flag_path() {
    return conf_dir() + "/iphone_gateway.json";
}

static json iphone_gw_flag() {
    std::ifstream in(iphone_gw_flag_path());
    if (!in) return json{{"enabled", false}, {"closes", false}};
    try {
        json j;
        in >> j;
        if (!j.is_object()) return json{{"enabled", false}, {"closes", false}};
        return j;
    } catch (...) {
        return json{{"enabled", false}, {"closes", false}};
    }
}

static void iphone_gw_set_wanted(bool on) {
    json j = iphone_gw_flag();
    j["enabled"] = on;
    j["closes"] = false;
    j["port"] = 8787;
    j["updated"] = utc_stamp();
    j["source"] = "meta";
    std::ofstream out(iphone_gw_flag_path());
    if (out) out << j.dump(2) << "\n";
}

static std::vector<pid_t> iphone_gw_pids() {
    std::vector<pid_t> out;
    DIR* d = opendir("/proc");
    if (!d) return out;
    pid_t self = getpid();
    while (dirent* e = readdir(d)) {
        if (e->d_name[0] < '1' || e->d_name[0] > '9') continue;
        pid_t pid = static_cast<pid_t>(atoi(e->d_name));
        if (pid <= 1 || pid == self) continue;
        std::ifstream in(std::string("/proc/") + e->d_name + "/cmdline", std::ios::binary);
        if (!in) continue;
        std::string raw((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
        if (raw.empty()) continue;
        std::vector<std::string> args;
        std::string cur;
        for (unsigned char c : raw) {
            if (c == 0) {
                if (!cur.empty()) args.push_back(cur);
                cur.clear();
            } else {
                cur += static_cast<char>(c);
            }
        }
        if (!cur.empty()) args.push_back(cur);
        if (args.empty()) continue;
        auto slash = args[0].rfind('/');
        std::string exe = slash == std::string::npos ? args[0] : args[0].substr(slash + 1);
        if (exe.find("python") == std::string::npos) continue;
        bool hit = false;
        for (auto& a : args) {
            if (a.find("iphone/gateway.py") != std::string::npos) { hit = true; break; }
        }
        if (hit) out.push_back(pid);
    }
    closedir(d);
    return out;
}

static bool iphone_gw_listening() {
    auto r = http_request("127.0.0.1", 8787, "GET", "/", "", 350);
    return r.status == 200 || r.status == 401;
}

static bool iphone_gw_up() {
    if (iphone_gw_listening()) return true;
    return !iphone_gw_pids().empty();
}

static int iphone_gw_stop() {
    auto pids = iphone_gw_pids();
    for (pid_t pid : pids) kill(pid, SIGTERM);
    usleep(250000);
    pids = iphone_gw_pids();
    for (pid_t pid : pids) kill(pid, SIGKILL);
    usleep(80000);
    iphone_gw_set_wanted(false);
    return static_cast<int>(iphone_gw_pids().size());
}

static bool iphone_gw_start() {
    std::string script = repo_root() + "/iphone/gateway.py";
    if (access(script.c_str(), R_OK) != 0) return false;
    iphone_gw_set_wanted(true);
    pid_t pid = fork();
    if (pid < 0) return false;
    if (pid == 0) {
        setsid();
        if (fork() > 0) _exit(0);
        int lg = open("/tmp/rimbaud-iphone-gateway.log", O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (lg >= 0) { dup2(lg, 1); dup2(lg, 2); close(lg); }
        setenv("GATEWAY_BIND", "127.0.0.1", 1);
        setenv("GATEWAY_PORT", "8787", 1);
        if (!getenv("GATEWAY_TOKEN")) setenv("GATEWAY_TOKEN", "devtest", 1);
        execlp("python3", "python3", script.c_str(), static_cast<char*>(nullptr));
        _exit(127);
    }
    int st = 0;
    waitpid(pid, &st, 0);
    return true;
}

static bool host_allowed(const std::string& host) {
    if (host == "127.0.0.1" || host == "localhost" || host == "::1") return true;
    in_addr a{};
    if (inet_pton(AF_INET, host.c_str(), &a) != 1) return false;
    unsigned u = ntohl(a.s_addr);
    if ((u & 0xFFFF0000u) == 0xC0A80000u) return true;
    if ((u & 0xFFFFFF00u) == 0x0A425000u) return true;
    return false;
}

struct Site {
    std::string id, name, host, role;
    int port = 5004;
    bool ok = false;
    bool vpn_up = false;
    std::string err, bias, vpn;
    double eq = 0, upnl = 0, ip_left = 0, ip_cap = 1200;
    std::string hl_state, hl_note, hl_name, hl_addr;
    int n_pos = 0, n_live = 0, n_bots = 0;
    long long spring_mtime = 0;
    long long spring_size = 0;
    long long desk_mtime = 0;
    long long desk_size = 0;
    std::string archy;
    std::string archy_deployed;
    std::string deploy_ver;
    std::string deploy_ts;
    std::string lan;
    bool main_only = false; // fresh Hyperliquid account: one main, no subaccounts
};

struct Usb {
    std::string name, path, model, size_h, mount, note;
    double size = 0;
    bool ready = false;
};

struct BootMedia {
    bool on = false;
    int pct = 0;
    std::string step;
    std::string tool;
};

static bool cmdline_has(const char* pid, const char* needle) {
    char path[80];
    snprintf(path, sizeof(path), "/proc/%s/cmdline", pid);
    std::ifstream in(path, std::ios::binary);
    if (!in) return false;
    std::string s((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    for (char& c : s) if (c == '\0') c = ' ';
    return s.find(needle) != std::string::npos;
}

static std::string boot_tool_name() {
    static std::string cached;
    static time_t at = 0;
    time_t t = time(nullptr);
    if (t == at) return cached;
    at = t;
    DIR* d = opendir("/proc");
    if (!d) { cached.clear(); return cached; }
    std::string found;
    while (dirent* e = readdir(d)) {
        if (!e->d_name[0] || !std::isdigit(static_cast<unsigned char>(e->d_name[0]))) continue;
        if (cmdline_has(e->d_name, "mksquashfs")) { found = "mksquashfs"; break; }
        if (cmdline_has(e->d_name, "unsquashfs")) { found = "unsquashfs"; break; }
        if (cmdline_has(e->d_name, "xorriso")) { found = "xorriso"; break; }
        if (found.empty() && cmdline_has(e->d_name, "of=/dev/sd")) found = "dd";
        if (found.empty() && cmdline_has(e->d_name, "build-iso.sh")) found = "build-iso";
        if (found.empty() && cmdline_has(e->d_name, "gui-build.sh")) found = "build-iso";
        if (found.empty() && cmdline_has(e->d_name, "write-usb.sh")) found = "write-usb";
    }
    closedir(d);
    cached = found;
    return cached;
}

static long long file_bytes(const std::string& p) {
    struct stat st{};
    if (stat(p.c_str(), &st) != 0) return 0;
    return static_cast<long long>(st.st_size);
}

// Last 0–100 figure in a job log. unsquashfs and dd rewrite the same line with \r.
static int last_log_pct(const std::string& path) {
    std::ifstream in(path);
    if (!in) return -1;
    std::string s((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    int pct = -1;
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] != '%') continue;
        int j = static_cast<int>(i) - 1;
        if (j < 0 || !std::isdigit(static_cast<unsigned char>(s[static_cast<size_t>(j)]))) continue;
        int v = 0;
        int place = 1;
        int digits = 0;
        while (j >= 0 && std::isdigit(static_cast<unsigned char>(s[static_cast<size_t>(j)])) && digits < 3) {
            v += (s[static_cast<size_t>(j)] - '0') * place;
            place *= 10;
            --j;
            ++digits;
        }
        if (v >= 0 && v <= 100) pct = v;
    }
    return pct;
}

static long long last_bytes_copied(const std::string& path) {
    std::ifstream in(path);
    if (!in) return -1;
    std::string s((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    long long best = -1;
    const std::string key = " bytes";
    size_t pos = 0;
    while ((pos = s.find(key, pos)) != std::string::npos) {
        int j = static_cast<int>(pos) - 1;
        if (j >= 0 && std::isdigit(static_cast<unsigned char>(s[static_cast<size_t>(j)]))) {
            long long v = 0;
            long long place = 1;
            int digits = 0;
            while (j >= 0 && std::isdigit(static_cast<unsigned char>(s[static_cast<size_t>(j)])) && digits < 13) {
                v += static_cast<long long>(s[static_cast<size_t>(j)] - '0') * place;
                place *= 10;
                --j;
                ++digits;
            }
            best = v;
        }
        pos += key.size();
    }
    return best;
}

static BootMedia sample_boot_media(const json& st, bool mint_busy, int mint_job) {
    BootMedia b;
    std::string phase = jstr(st, "phase", "none");
    std::string msg = jstr(st, "msg");
    std::string label = jstr(st, "label_de");
    int pct = static_cast<int>(jnum(st, "pct", -1));
    std::string tool = boot_tool_name();
    bool running = phase == "running" || mint_busy || !tool.empty();
    if (!running) return b;
    b.on = true;
    b.tool = tool;
    auto set_step = [&](const char* de) {
        b.step = label.empty() ? de : label;
    };
    // A "Write ISO" click rebuilds first when the image is old. That is not a stick write.
    bool build_running = phase == "running"
        || tool == "mksquashfs" || tool == "unsquashfs" || tool == "xorriso" || tool == "build-iso";
    if (!build_running && (tool == "dd" || (mint_busy && mint_job == 2))) {
        b.step = "USB-Stick wird beschrieben";
        if (b.tool.empty() || b.tool == "write-usb") b.tool = "dd";
        long long copied = last_bytes_copied("/tmp/rimbaud-mint-iso.log");
        long long total = static_cast<long long>(jnum(st, "bytes", 0));
        if (copied > 0 && total > 0) {
            b.pct = static_cast<int>(copied * 100 / total);
            if (b.pct < 1) b.pct = 1;
            if (b.pct > 99) b.pct = 99;
        } else {
            b.pct = 5;
        }
    } else if (tool == "mksquashfs" || msg.find("mksquash") != std::string::npos) {
        set_step("Abbild wird gepackt");
        long long sz = file_bytes(repo_root() + "/dist/mint-image/work/iso/casper/filesystem.squashfs");
        int est = 55;
        if (sz > 1000000) {
            est = 55 + static_cast<int>(sz * 32 / 3000000000LL);
            if (est > 87) est = 87;
            if (est < 55) est = 55;
        }
        b.pct = pct > 0 ? std::max(pct, est) : est;
        b.tool = "mksquashfs";
    } else if (tool == "unsquashfs" || msg.find("unsquash") != std::string::npos) {
        set_step("System wird entpackt");
        int lp = last_log_pct("/tmp/rimbaud-mint-iso.log");
        int est = lp >= 0 ? 22 + lp * 32 / 100 : (pct > 0 ? pct : 28);
        if (est > 54) est = 54;
        if (est < 22) est = 22;
        b.pct = est;
        b.tool = "unsquashfs";
    } else if ((tool == "xorriso" || msg.find("xorriso") != std::string::npos)
               && msg.find("extract") == std::string::npos) {
        set_step("Hybrid-ISO wird geschrieben");
        b.pct = pct > 0 ? pct : 92;
        b.tool = "xorriso";
    } else if (msg.find("download") != std::string::npos) {
        set_step("ISO wird geladen");
        b.pct = pct > 0 ? pct : 6;
    } else if (msg.find("checksum") != std::string::npos) {
        set_step("ISO wird geprueft");
        b.pct = pct > 0 ? pct : 12;
    } else if (msg.find("extract") != std::string::npos) {
        set_step("ISO wird entpackt");
        b.pct = pct > 0 ? pct : 16;
    } else if (msg.find("resume") != std::string::npos) {
        set_step("System liegt bereit");
        b.pct = pct > 0 ? pct : 20;
    } else if (msg.find(".deb") != std::string::npos) {
        set_step("rimBaud-Paket wird gebaut");
        b.pct = pct > 0 ? pct : 42;
    } else if (msg.find("chroot") != std::string::npos || msg.find("apt") != std::string::npos) {
        set_step("Pakete werden eingespielt");
        b.pct = pct > 0 ? pct : 50;
    } else if (msg.find("host tools") != std::string::npos) {
        set_step("Host-Werkzeuge");
        b.pct = pct > 0 ? pct : 4;
    } else {
        set_step(msg.empty() ? "Startmedium wird erstellt" : msg.c_str());
        b.pct = pct > 0 ? pct : 15;
    }
    if (b.pct < 1) b.pct = 1;
    if (b.pct > 99) b.pct = 99;
    return b;
}

struct UsbMedia {
    bool on = false;
    int pct = 0;
    std::string step;
};

// Step percent from make-usb.sh OK lines. The script has no byte counter.
static UsbMedia sample_usb_media(const std::string& path, bool busy, int exit_code) {
    UsbMedia u;
    if (!busy && exit_code < 0) return u;
    static const struct {
        const char* key;
        int pct;
    } stages[] = {
        {"check stick", 12},
        {"mount", 22},
        {"pack kit", 55},
        {"copy installer", 66},
        {"JOIN.conf", 76},
        {"hub files", 84},
        {"verify", 92},
        {"sync", 97},
    };
    std::ifstream in(path);
    std::string line, last_cmd, last_fail;
    int pct = 0;
    int mask = 0;
    while (std::getline(in, line)) {
        auto cr = line.rfind('\r');
        if (cr != std::string::npos) line = line.substr(cr + 1);
        if (line.empty()) continue;
        if (line.rfind("OK", 0) == 0) {
            if (line.find("USB kit ready") != std::string::npos) {
                pct = 100;
                last_cmd = "USB kit ready";
                continue;
            }
            for (int i = 0; i < 8; i++) {
                if ((mask & (1 << i)) == 0 && line.find(stages[i].key) != std::string::npos) {
                    mask |= 1 << i;
                    if (stages[i].pct > pct) pct = stages[i].pct;
                }
            }
        } else if (line.rfind("CMD", 0) == 0) {
            last_cmd = line.size() > 5 ? line.substr(5) : line;
        } else if (line.rfind("FAIL", 0) == 0 || line.rfind("xx", 0) == 0) {
            last_fail = line;
        }
    }
    if (!busy && exit_code == 0) pct = 100;
    if (busy && pct > 99) pct = 99;
    if (busy && pct < 1) pct = 4;
    u.on = true;
    u.pct = pct;
    if (!last_fail.empty() && !busy && exit_code > 0)
        u.step = last_fail;
    else if (!last_cmd.empty())
        u.step = last_cmd;
    else
        u.step = busy ? "USB-Stick wird vorbereitet" : "USB kit";
    return u;
}

enum Page { PgHome, PgDeploy, PgMaster, PgSystem, PgBackup };
static const char* PAGE_NAME[] = {"HOME", "DEPLOY", "HL MASTER", "SYSTEM", "BACKUP"};

struct LanBox {
    std::string ip;
    bool ssh = false;
    bool spring = false;
};

static std::vector<LanBox> read_lan_scan() {
    std::vector<LanBox> out;
    std::ifstream in("/tmp/rimbaud-lan-scan.json");
    if (!in) return out;
    json j;
    try { in >> j; } catch (...) { return out; }
    if (!j.contains("boxes") || !j["boxes"].is_array()) return out;
    for (auto& b : j["boxes"]) {
        if (!b.is_object()) continue;
        LanBox x;
        x.ip = jstr(b, "ip");
        x.ssh = jflag(b, "ssh");
        x.spring = jflag(b, "spring");
        if (!x.ip.empty() && host_allowed(x.ip)) out.push_back(x);
    }
    return out;
}

static pid_t spawn_lan_scan() {
    pid_t p = fork();
    if (p < 0) return 0;
    if (p == 0) {
        std::string py = repo_root() + "/rimbaud_vpn/enroll.py";
        execl("/usr/bin/python3", "python3", py.c_str(), "scan", "--out",
              "/tmp/rimbaud-lan-scan.json", static_cast<char*>(nullptr));
        _exit(127);
    }
    return p;
}

static bool add_matrix_child(const std::string& ip, std::string& err) {
    if (!host_allowed(ip) || ip == "127.0.0.1") {
        err = "not a LAN address";
        return false;
    }
    std::string path = conf_dir() + "/meta.json";
    json j = json::object();
    {
        std::ifstream in(path);
        if (in) {
            try { in >> j; } catch (...) { j = json::object(); }
        }
    }
    if (!j.is_object()) j = json::object();
    if (!j.contains("sites") || !j["sites"].is_array()) j["sites"] = json::array();
    for (auto& s : j["sites"]) {
        if (!s.is_object()) continue;
        if (jstr(s, "host") == ip || jstr(s, "lan") == ip) {
            err = ip + " is already a matrix";
            return false;
        }
    }
    std::string base = "child-" + ip.substr(ip.find_last_of('.') + 1);
    std::string id = base;
    for (int n = 2; n < 30; n++) {
        bool clash = false;
        for (auto& s : j["sites"])
            if (s.is_object() && jstr(s, "id") == id) clash = true;
        if (!clash) break;
        id = base + "-" + std::to_string(n);
    }
    j["ok"] = true;
    j["read_only"] = true;
    j["closes"] = false;
    j["positions_untouched"] = true;
    if (!j.contains("lan")) j["lan"] = "192.168.0.0/16";
    j["vpn_net"] = "10.66.80.0/24";
    j["sites"].push_back(json{{"id", id},
                              {"name", "child " + ip},
                              {"host", ip},
                              {"port", 5004},
                              {"role", "child"},
                              {"lan", ip}});
    std::string tmp = path + ".tmp";
    std::ofstream out(tmp);
    if (!out) {
        err = "cannot write meta.json";
        return false;
    }
    out << j.dump(2) << "\n";
    out.close();
    if (std::rename(tmp.c_str(), path.c_str()) != 0) {
        err = "cannot replace meta.json";
        return false;
    }
    return true;
}

static std::string clean_matrix_name(const std::string& raw) {
    std::string o;
    for (unsigned char c : raw) {
        bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                  (c >= '0' && c <= '9') || c == ' ' || c == '.' || c == '_' || c == '-';
        if (!ok) continue;
        if (o.size() >= 20) break;
        o.push_back(static_cast<char>(c));
    }
    while (!o.empty() && o.front() == ' ') o.erase(o.begin());
    while (!o.empty() && o.back() == ' ') o.pop_back();
    return o;
}

static bool write_json_atomic(const std::string& path, const json& j, std::string& err) {
    std::string tmp = path + ".tmp";
    {
        std::ofstream out(tmp);
        if (!out) {
            err = "cannot write " + path;
            return false;
        }
        out << j.dump(2) << "\n";
        if (!out) {
            err = "cannot write " + path;
            return false;
        }
    }
    if (std::rename(tmp.c_str(), path.c_str()) != 0) {
        err = "cannot replace " + path;
        return false;
    }
    return true;
}

// Display name only. id, host, and SSH stay put.
static bool rename_matrix(const std::string& id, const std::string& raw, std::string& saved, std::string& err) {
    if (id.empty() || id == "local") {
        err = "the mothership name stays";
        return false;
    }
    saved = clean_matrix_name(raw);
    if (saved.empty()) {
        err = "name is empty — use letters, digits, space, . _ -";
        return false;
    }
    std::string path = conf_dir() + "/meta.json";
    json j;
    {
        std::ifstream in(path);
        if (!in) {
            err = "no meta.json";
            return false;
        }
        try { in >> j; } catch (...) {
            err = "meta.json is not json";
            return false;
        }
    }
    if (!j.is_object() || !j.contains("sites") || !j["sites"].is_array()) {
        err = "meta.json has no sites";
        return false;
    }
    bool found = false;
    for (auto& s : j["sites"]) {
        if (!s.is_object() || jstr(s, "id") != id) continue;
        if (jstr(s, "role") == "local" || jstr(s, "host") == "127.0.0.1") {
            err = "the mothership name stays";
            return false;
        }
        s["name"] = saved;
        found = true;
        break;
    }
    if (!found) {
        err = "that matrix is not in meta.json";
        return false;
    }
    if (!write_json_atomic(path, j, err)) return false;

    std::string hosts = conf_dir() + "/deploy_hosts.json";
    std::ifstream hin(hosts);
    if (hin) {
        json h;
        try { hin >> h; } catch (...) { h = json::object(); }
        if (h.is_object() && h.contains(id) && h[id].is_object()) {
            h[id]["name"] = saved;
            std::string herr;
            write_json_atomic(hosts, h, herr);
        }
    }
    return true;
}

struct Hit {
    enum Kind { Idle, Nav, Close, IphoneGw, UsbRow, MakeUsb, RefreshUsb, GenMaster, CreateSubs,
                LivePush, LiveSamba, LiveSite, PushLogClose, PushLogOpen, LogCopy,
                DeployMode, BuildMintIso, WriteMintIso, RebuildWrite, AddChild, RefreshLan,
                RenameName, HbDrop, HbPick, HbAll, HbDownload, HbExecute,
                BkDest, BkEdit, BkCreate, BkCancel, BkRefresh, ApiKey, Ssh } kind = Idle;
    int i = -1;
    Rect r{};
    int j = -1;
};

struct LogPos {
    int line = 0;
    int col = 0;
};
static bool logpos_lt(LogPos a, LogPos b) {
    return a.line < b.line || (a.line == b.line && a.col < b.col);
}

static json parse_or(const std::string& s) {
    try { return json::parse(s.empty() ? "{}" : s); } catch (...) { return json::object(); }
}

static void wipe_secret(std::string& s) {
    for (char& c : s) c = '\0';
    s.clear();
}

static bool api_hex_char(unsigned char c) {
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F') || c == 'x' || c == 'X';
}

static std::string norm_api_secret(std::string raw) {
    std::string s;
    for (unsigned char c : raw) {
        if (c == ' ' || c == '\n' || c == '\r' || c == '\t') continue;
        s.push_back(static_cast<char>(c));
    }
    wipe_secret(raw);
    if (s.size() >= 2 && s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s.erase(0, 2);
    if (s.size() != 64) {
        wipe_secret(s);
        return "";
    }
    for (char& c : s) {
        if (c >= 'A' && c <= 'F') c = static_cast<char>(c - 'A' + 'a');
        else if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
            wipe_secret(s);
            return "";
        }
    }
    return "0x" + s;
}

static void load_api_view(std::string& addr, std::string& exch, std::string& note) {
    addr.clear();
    exch.clear();
    std::ifstream in(conf_dir() + "/hl_api_pub.json");
    if (!in) {
        note = "not saved in SQLite yet";
        return;
    }
    json j;
    try { in >> j; } catch (...) {
        note = "not saved in SQLite yet";
        return;
    }
    addr = jstr(j, "address");
    exch = jstr(j, "on_exchange");
    if (addr.empty()) {
        note = "not saved in SQLite yet";
        return;
    }
    note = "on exchange: " + (exch.empty() ? "?" : exch);
    std::string upd = jstr(j, "updated_at");
    if (!upd.empty()) note += "  ·  " + upd;
    if (j.contains("n_files") && j["n_files"].is_number())
        note += "  ·  " + std::to_string(j["n_files"].get<int>()) + " files";
}

static std::string api_result_message(const std::string& blob) {
    std::string line, cur;
    for (char c : blob) {
        if (c == '\n') {
            if (!cur.empty()) line = cur;
            cur.clear();
        } else if (c != '\r') cur.push_back(c);
    }
    if (!cur.empty()) line = cur;
    json j = parse_or(line);
    if (!j.is_object() || j.empty()) return "API key was not saved — paste it again";
    if (jflag(j, "ok")) {
        std::string m = jstr(j, "message");
        return m.empty() ? "API key saved" : m;
    }
    std::string err = jstr(j, "error");
    if (err.empty()) return "API key was not saved — paste it again";
    auto p = err.rfind('\n');
    if (p != std::string::npos) err = err.substr(p + 1);
    if (err.size() > 180) err.resize(180);
    return err;
}

static json get_json(const std::string& host, int port, const std::string& path) {
    auto r = http_request(host, port, "GET", path, "", 2500);
    if (r.status != 200 || r.body.empty()) return json{{"_err", r.error.empty() ? "down" : r.error}};
    return parse_or(r.body);
}

static std::vector<Site> load_sites() {
    std::vector<Site> out;
    Site local;
    local.id = "local";
    local.name = "this matrix";
    local.host = "127.0.0.1";
    local.role = "local";
    local.port = 5004;
    out.push_back(local);
    std::ifstream in(conf_dir() + "/meta.json");
    if (!in) return out;
    json j;
    try { in >> j; } catch (...) { return out; }
    if (!j.contains("sites") || !j["sites"].is_array()) return out;
    for (auto& s : j["sites"]) {
        if (!s.is_object()) continue;
        Site x;
        x.id = jstr(s, "id");
        x.name = jstr(s, "name", x.id);
        x.host = jstr(s, "host");
        x.role = jstr(s, "role");
        x.lan = jstr(s, "lan");
        x.main_only = jstr(s, "hl_accounts") == "main";
        x.port = static_cast<int>(jnum(s, "port", 5004));
        if (x.host.empty() || x.port == 5001 || !host_allowed(x.host)) continue;
        if (x.host == "127.0.0.1" && x.port == 5004) continue;
        out.push_back(x);
    }
    return out;
}

static std::string lower_copy(std::string a) {
    for (char& c : a)
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return a;
}

// Live hunters only. The same address counts once (one main account, no subaccounts).
// Returns false when the home payload has no bots array.
static bool funds_once(const json& home, double& eq, double& upnl, int& n_pos) {
    if (!home.contains("bots") || !home["bots"].is_array()) return false;
    eq = 0;
    upnl = 0;
    n_pos = 0;
    std::map<std::string, std::pair<double, double>> best;
    std::map<std::string, int> best_n;
    for (auto& b : home["bots"]) {
        if (!b.is_object()) continue;
        if (!jflag(b, "running") || jflag(b, "deactivated")) continue;
        double e = jnum(b, "equity_usd");
        double u = jnum(b, "upnl_usd");
        int np = static_cast<int>(jnum(b, "n_pos"));
        std::string addr = lower_copy(jstr(b, "address"));
        if (addr.size() < 8) {
            eq += e;
            upnl += u;
            n_pos += np;
            continue;
        }
        auto it = best.find(addr);
        if (it == best.end() || e > it->second.first) {
            best[addr] = {e, u};
            best_n[addr] = np;
        }
    }
    for (auto& kv : best) {
        eq += kv.second.first;
        upnl += kv.second.second;
        n_pos += best_n[kv.first];
    }
    return true;
}

static bool ping_host(const std::string& host) {
    if (host == "127.0.0.1" || host == "localhost") return true;
    std::string cmd = "ping -c 1 -W 1 " + host + " >/dev/null 2>&1";
    return std::system(cmd.c_str()) == 0;
}

static void poll_site(Site& s) {
    s.vpn_up = ping_host(s.host);
    if (s.role == "child" && s.host.rfind("10.66.80.", 0) != 0)
        s.vpn = s.vpn_up ? "LAN seen — not on VPN yet" : "child reserved";
    else if (s.host.rfind("10.66.80.", 0) == 0)
        s.vpn = s.vpn_up ? "VPN tunnel UP" : "VPN reserved — waiting for join";
    else
        s.vpn = "local (no tunnel)";
    auto h = get_json(s.host, s.port, "/api/health");
    if (h.contains("_err") || !jflag(h, "ok")) {
        s.ok = false;
        s.err = jstr(h, "_err", "down");
        return;
    }
    auto st = h.contains("stamp") && h["stamp"].is_object() ? h["stamp"] : json::object();
    s.spring_mtime = static_cast<long long>(jnum(st, "spring_mtime"));
    s.spring_size = static_cast<long long>(jnum(st, "spring_size"));
    s.desk_mtime = static_cast<long long>(jnum(st, "desk_mtime"));
    s.desk_size = static_cast<long long>(jnum(st, "desk_size"));
    s.archy = jstr(h, "archy");
    if (s.archy.empty()) s.archy = jstr(st, "archy");
    s.archy_deployed = jstr(h, "archy_deployed");
    if (s.archy_deployed.empty()) s.archy_deployed = jstr(st, "archy_deployed");
    auto home = get_json(s.host, s.port, "/api/home");
    if (home.contains("_err")) {
        s.ok = true;
        s.err = "health only";
        return;
    }
    s.ok = true;
    s.err.clear();
    s.bias = jstr(home.contains("desk_bias") ? home["desk_bias"] : json::object(), "bias", "-");
    auto t = home.contains("totals") ? home["totals"] : json::object();
    double folded_eq = 0, folded_up = 0;
    int folded_pos = 0;
    if (funds_once(home, folded_eq, folded_up, folded_pos)) {
        s.eq = folded_eq;
        s.upnl = folded_up;
        s.n_pos = folded_pos;
    } else {
        s.eq = jnum(t, "equity_usd");
        s.upnl = jnum(t, "upnl_usd");
        s.n_pos = static_cast<int>(jnum(t, "n_pos"));
    }
    s.n_bots = static_cast<int>(jnum(t, "n_bots"));
    s.n_live = static_cast<int>(jnum(t, "n_live"));
    auto r = home.contains("rate") ? home["rate"] : json::object();
    s.ip_left = jnum(r, "left");
    s.ip_cap = jnum(r, "cap", 1200);
    auto hk = home.contains("hl_key") && home["hl_key"].is_object() ? home["hl_key"] : json::object();
    s.hl_state = jstr(hk, "state");
    s.hl_name = jstr(hk, "agent_name");
    s.hl_note = jstr(hk, "error");
    s.hl_addr = jstr(hk, "address");
}

static std::string local_archy() {
    std::ifstream in(conf_dir() + "/archy.json");
    if (!in) return "";
    json j;
    try { in >> j; } catch (...) { return ""; }
    return jstr(j, "version");
}

static std::map<std::string, std::pair<std::string, std::string>> load_deploys() {
    std::map<std::string, std::pair<std::string, std::string>> m;
    std::ifstream in(conf_dir() + "/archy_deploys.json");
    if (!in) return m;
    json j;
    try { in >> j; } catch (...) { return m; }
    if (!j.contains("deploys") || !j["deploys"].is_array()) return m;
    for (auto& d : j["deploys"]) {
        if (!d.is_object()) continue;
        std::string id = jstr(d, "site_id");
        if (id.empty()) id = jstr(d, "id");
        if (id.empty()) continue;
        m[id] = {jstr(d, "version"), jstr(d, "ts")};
    }
    return m;
}

static void apply_deploys(std::vector<Site>& sites) {
    auto deps = load_deploys();
    for (auto& s : sites) {
        auto it = deps.find(s.id);
        if (it == deps.end()) continue;
        s.deploy_ver = it->second.first;
        s.deploy_ts = it->second.second;
        if (s.archy_deployed.empty()) s.archy_deployed = s.deploy_ver;
    }
}

struct IsoStamp {
    bool have = false;
    bool current = false;
    std::string iso;
    std::string newer;
};

static long long file_mtime(const std::string& path) {
    struct stat st{};
    if (stat(path.c_str(), &st) != 0) return 0;
    return static_cast<long long>(st.st_mtime);
}

static IsoStamp iso_stamp() {
    IsoStamp s;
    std::string root = repo_root();
    std::string dir = root + "/dist/mint-image";
    std::string best;
    long long best_m = 0;
    {
        std::ifstream in(dir + "/STATUS.json");
        if (in) {
            json j;
            try { in >> j; } catch (...) { j = json::object(); }
            std::string p = jstr(j, "iso");
            long long m = file_mtime(p);
            if (m > 0) { best = p; best_m = m; }
        }
    }
    if (best.empty()) {
        DIR* d = opendir(dir.c_str());
        if (d) {
            while (auto* e = readdir(d)) {
                std::string n = e->d_name;
                if (n.rfind("rimbaud-mint-", 0) != 0) continue;
                if (n.size() < 4 || n.compare(n.size() - 4, 4, ".iso") != 0) continue;
                std::string p = dir + "/" + n;
                long long m = file_mtime(p);
                if (m >= best_m) { best_m = m; best = p; }
            }
            closedir(d);
        }
    }
    const char* srcs[] = {
        "/install_new/install.sh",
        "/install_new/bootstrap.sh",
        "/install_new/pack.sh",
        "/install_new/mint-image/build-iso.sh",
        "/install_new/mint-image/build-deb.sh",
        "/install_new/mint-image/make-look.py",
        "/install_new/mint-image/overlay/usr/share/grub/rimbaud.png",
        "/install_new/mint-image/firstboot/rimbaud-firstboot.sh",
        "/install_new/mint-image/preseed/rimbaud.seed",
        "/install_new/rustdesk.sh",
        "/install_new/backup-restore.py",
        "/install_new/mint-image/overlay/etc/xdg/autostart/rimbaud-restore.desktop",
        "/rimbaud_vpn/rimbaud-vpn",
        "/rimbaud_vpn/enroll.py",
    };
    if (best.empty()) {
        s.newer = "no image yet";
        return s;
    }
    s.have = true;
    s.iso = best;
    s.current = true;
    long long newest = best_m;
    for (const char* rel : srcs) {
        long long m = file_mtime(root + rel);
        if (m > newest) {
            newest = m;
            s.current = false;
            std::string name = rel;
            auto sl = name.rfind('/');
            s.newer = sl == std::string::npos ? name : name.substr(sl + 1);
        }
    }
    return s;
}

static bool is_mothership(const Site& s) {
    return s.host == "127.0.0.1" || s.host == "localhost" || s.id == "local" || s.role == "local";
}

// RustDesk seat on each matrix. The probe prints one tab line and no secrets.
struct RdView {
    std::string state;
    std::string id;
    std::string detail;
    double at = 0;
};

struct RdJob {
    pid_t pid = 0;
    int fd = -1;
    std::string site;
    std::string buf;
    double started = 0;
};

static RdJob rd_job;
static std::map<std::string, RdView> rd_cache;

static double rd_now() {
    return static_cast<double>(time(nullptr));
}

struct RdNet {
    std::string host = "127.0.0.1";
    std::string id_port = "21126";
    std::string relay_port = "21127";
};

static RdNet rd_net() {
    RdNet n;
    std::ifstream in(conf_dir() + "/rustdesk.json");
    if (!in) return n;
    json j;
    try { in >> j; } catch (...) { return n; }
    std::string ident = jstr(j, "id_server");
    std::string relay = jstr(j, "relay_server");
    auto colon = ident.find(':');
    if (colon != std::string::npos && colon > 0) {
        n.host = ident.substr(0, colon);
        n.id_port = ident.substr(colon + 1);
    } else if (!ident.empty()) {
        n.host = ident;
    }
    auto rc = relay.rfind(':');
    if (rc != std::string::npos && rc + 1 < relay.size()) n.relay_port = relay.substr(rc + 1);
    in_addr a{};
    if (inet_pton(AF_INET, n.host.c_str(), &a) != 1) n.host = "127.0.0.1";
    if (n.id_port.find_first_not_of("0123456789") != std::string::npos) n.id_port = "21126";
    if (n.relay_port.find_first_not_of("0123456789") != std::string::npos) n.relay_port = "21127";
    return n;
}

static bool rd_safe_target(const std::string& s) {
    if (s.size() < 3 || s.size() > 80) return false;
    auto at = s.find('@');
    if (at == std::string::npos || at == 0 || at + 1 >= s.size()) return false;
    if (s.find('@', at + 1) != std::string::npos) return false;
    for (char c : s) {
        unsigned char u = static_cast<unsigned char>(c);
        if (std::isalnum(u) || c == '@' || c == '.' || c == '_' || c == '-') continue;
        return false;
    }
    return true;
}

struct SshCred {
    bool present = false;
    std::string vpn, lan, root, live;
};

static bool ssh_path_ok(const std::string& s) {
    if (s.size() < 2 || s.size() > 180 || s[0] != '/') return false;
    if (s.find("..") != std::string::npos) return false;
    for (unsigned char c : s) {
        if (std::isalnum(c) || c == '/' || c == '.' || c == '_' || c == '-') continue;
        return false;
    }
    return true;
}

static std::string trim_slash(std::string s) {
    while (s.size() > 1 && s.back() == '/') s.pop_back();
    return s;
}

static SshCred load_ssh_cred(const std::string& id) {
    SshCred c;
    if (id.empty()) return c;
    std::ifstream in(conf_dir() + "/deploy_hosts.json");
    if (!in) return c;
    json j;
    try { in >> j; } catch (...) { return c; }
    if (!j.is_object() || !j.contains(id) || !j[id].is_object()) return c;
    const json& row = j[id];
    c.vpn = jstr(row, "ssh");
    c.lan = jstr(row, "ssh_lan");
    c.root = jstr(row, "root");
    c.live = jstr(row, "live_root");
    c.present = !c.vpn.empty() || !c.lan.empty() || !c.root.empty();
    return c;
}

// Writes ssh, ssh_lan, root, and optional live_root. Keeps the deploy identity.
// No password and no private key.
static bool save_ssh_cred(const std::string& id, const std::string& name,
                          std::string vpn, std::string lan, std::string root, std::string live,
                          std::string& err) {
    if (id.empty() || id == "local") {
        err = "the mothership has no SSH row";
        return false;
    }
    vpn = trim_slash(vpn);
    lan = trim_slash(lan);
    root = trim_slash(root);
    live = trim_slash(live);
    if (!vpn.empty() && !rd_safe_target(vpn)) {
        err = "VPN must be user@host";
        return false;
    }
    if (!lan.empty() && !rd_safe_target(lan)) {
        err = "LAN must be user@host";
        return false;
    }
    if (vpn.empty() && lan.empty()) {
        err = "need a VPN or a LAN user@host";
        return false;
    }
    if (!ssh_path_ok(root)) {
        err = "remote root must be an absolute path";
        return false;
    }
    if (!live.empty() && !ssh_path_ok(live)) {
        err = "live root must be an absolute path, or empty";
        return false;
    }
    std::string path = conf_dir() + "/deploy_hosts.json";
    json j = json::object();
    {
        std::ifstream in(path);
        if (in) {
            try { in >> j; } catch (...) { j = json::object(); }
        }
    }
    if (!j.is_object()) j = json::object();
    json row = json::object();
    if (j.contains(id) && j[id].is_object()) row = j[id];
    row["id"] = id;
    if (!name.empty()) row["name"] = name;
    row["ssh"] = vpn;
    row["ssh_lan"] = lan;
    row["root"] = root;
    if (live.empty()) row.erase("live_root");
    else row["live_root"] = live;
    if (!row.contains("identity") || !row["identity"].is_string() || jstr(row, "identity").empty()) {
        const char* home = getenv("HOME");
        if (!home) home = ".";
        row["identity"] = std::string(home) + "/.ssh/id_ed25519_rimbaud_deploy";
    }
    if (!row.contains("note") || !row["note"].is_string())
        row["note"] = "Code only — never wallets or HL close. No password stored.";
    j[id] = row;
    return write_json_atomic(path, j, err);
}

static RdView rd_parse(const std::string& raw) {
    RdView v;
    v.at = rd_now();
    std::string line;
    std::string cur;
    for (char c : raw) {
        if (c == '\n' || c == '\r') {
            if (!cur.empty()) line = cur;
            cur.clear();
        } else {
            cur.push_back(c);
        }
    }
    if (!cur.empty()) line = cur;
    std::string f[3];
    int n = 0;
    std::string tok;
    for (size_t i = 0; i <= line.size() && n < 3; i++) {
        if (i == line.size() || line[i] == '\t') {
            f[n++] = tok;
            tok.clear();
        } else {
            tok.push_back(line[i]);
        }
    }
    v.state = f[0];
    v.id = f[1];
    v.detail = f[2];
    if (v.state != "online" && v.state != "operating" && v.state != "down" && v.state != "absent") {
        v.state = "unknown";
        if (v.detail.empty()) v.detail = line.empty() ? "no answer" : line;
    }
    std::string id;
    for (char c : v.id) if (std::isdigit(static_cast<unsigned char>(c))) id.push_back(c);
    v.id.swap(id);
    return v;
}

static bool tcp_port_open(const std::string& host, int port) {
    in_addr a{};
    if (inet_pton(AF_INET, host.c_str(), &a) != 1) return false;
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return false;
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags >= 0) fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    sockaddr_in sa{};
    sa.sin_family = AF_INET;
    sa.sin_port = htons(static_cast<uint16_t>(port));
    sa.sin_addr = a;
    bool ok = false;
    int rc = connect(fd, reinterpret_cast<sockaddr*>(&sa), sizeof(sa));
    if (rc == 0) ok = true;
    else if (errno == EINPROGRESS) {
        pollfd p{};
        p.fd = fd;
        p.events = POLLOUT;
        if (poll(&p, 1, 800) > 0) {
            int err = 0;
            socklen_t len = sizeof(err);
            if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len) == 0) ok = err == 0;
        }
    }
    close(fd);
    return ok;
}

static std::string host_of_target(const std::string& target) {
    auto at = target.find('@');
    if (at == std::string::npos) return target;
    return target.substr(at + 1);
}

static bool rd_ssh_for(const Site& s, std::string& target, std::string& identity, std::string& why) {
    target.clear();
    identity.clear();
    why = "no ssh for this matrix";
    std::ifstream in(conf_dir() + "/deploy_hosts.json");
    if (!in) return false;
    json j;
    try { in >> j; } catch (...) { return false; }
    const json* row = nullptr;
    if (j.contains(s.id) && j[s.id].is_object()) row = &j[s.id];
    if (!row) {
        for (auto it = j.begin(); it != j.end(); ++it) {
            if (!it.value().is_object()) continue;
            if (jstr(it.value(), "host") == s.host || jstr(it.value(), "id") == s.id) {
                row = &it.value();
                break;
            }
        }
    }
    if (!row) return false;
    std::string vpn = jstr(*row, "ssh");
    std::string lan = jstr(*row, "ssh_lan");
    why = "ssh unreachable";
    if (rd_safe_target(vpn) && tcp_port_open(host_of_target(vpn), 22)) target = vpn;
    else if (rd_safe_target(lan) && tcp_port_open(host_of_target(lan), 22)) target = lan;
    else return false;
    identity = jstr(*row, "identity");
    if (identity.empty()) {
        const char* home = getenv("HOME");
        if (!home) home = ".";
        identity = std::string(home) + "/.ssh/id_ed25519_rimbaud_deploy";
    } else if (identity[0] == '~') {
        const char* home = getenv("HOME");
        if (home) identity = std::string(home) + identity.substr(1);
    }
    if (!rd_safe_target(target)) return false;
    struct stat st{};
    if (stat(identity.c_str(), &st) != 0 || !S_ISREG(st.st_mode)) {
        why = "no deploy key";
        return false;
    }
    return true;
}

static void rd_finish_job() {
    if (!rd_job.site.empty()) rd_cache[rd_job.site] = rd_parse(rd_job.buf);
    if (rd_job.fd >= 0) close(rd_job.fd);
    rd_job.fd = -1;
    rd_job.pid = 0;
    rd_job.buf.clear();
    rd_job.site.clear();
    rd_job.started = 0;
}

static void rd_pump() {
    if (rd_job.pid <= 0 && rd_job.fd < 0) return;
    if (rd_job.fd >= 0) {
        char b[512];
        for (;;) {
            ssize_t n = read(rd_job.fd, b, sizeof(b));
            if (n > 0) {
                rd_job.buf.append(b, b + n);
                continue;
            }
            if (n == 0) {
                close(rd_job.fd);
                rd_job.fd = -1;
            }
            break;
        }
    }
    if (rd_job.pid > 0) {
        int st = 0;
        pid_t r = waitpid(rd_job.pid, &st, WNOHANG);
        if (r == rd_job.pid || (r < 0 && errno == ECHILD)) {
            if (rd_job.fd >= 0) {
                char b[512];
                ssize_t n = read(rd_job.fd, b, sizeof(b));
                if (n > 0) rd_job.buf.append(b, b + n);
            }
            rd_finish_job();
            return;
        }
        if (rd_job.started > 0 && rd_now() - rd_job.started > 8) {
            kill(-rd_job.pid, SIGTERM);
            waitpid(rd_job.pid, &st, WNOHANG);
            rd_job.buf = "unknown\t\tprobe timed out";
            rd_finish_job();
        }
    }
}

static bool rd_spawn(const Site& s) {
    if (rd_job.pid > 0) return false;
    RdNet net = rd_net();
    std::string script = repo_root() + "/install_new/rustdesk-status.py";
    struct stat st{};
    if (stat(script.c_str(), &st) != 0) {
        RdView v;
        v.state = "unknown";
        v.detail = "status script missing";
        v.at = rd_now();
        rd_cache[s.id] = v;
        return false;
    }
    bool local = is_mothership(s);
    std::string target, identity, why;
    int in_fd = -1;
    if (!local) {
        if (!rd_ssh_for(s, target, identity, why)) {
            RdView v;
            v.state = "unknown";
            v.detail = why.empty() ? "no ssh for this matrix" : why;
            v.at = rd_now();
            rd_cache[s.id] = v;
            return false;
        }
        in_fd = open(script.c_str(), O_RDONLY);
        if (in_fd < 0) return false;
    }
    int fds[2];
    if (pipe(fds) != 0) {
        if (in_fd >= 0) close(in_fd);
        return false;
    }
    pid_t p = fork();
    if (p < 0) {
        close(fds[0]);
        close(fds[1]);
        if (in_fd >= 0) close(in_fd);
        return false;
    }
    if (p == 0) {
        setpgid(0, 0);
        dup2(fds[1], STDOUT_FILENO);
        close(fds[0]);
        close(fds[1]);
        if (local) {
            execlp("python3", "python3", script.c_str(),
                   net.host.c_str(), net.id_port.c_str(), net.relay_port.c_str(),
                   static_cast<char*>(nullptr));
        } else {
            dup2(in_fd, STDIN_FILENO);
            close(in_fd);
            execlp("ssh", "ssh",
                   "-i", identity.c_str(),
                   "-o", "BatchMode=yes",
                   "-o", "IdentitiesOnly=yes",
                   "-o", "ConnectTimeout=3",
                   "-o", "StrictHostKeyChecking=accept-new",
                   target.c_str(),
                   "python3", "-",
                   net.host.c_str(), net.id_port.c_str(), net.relay_port.c_str(),
                   static_cast<char*>(nullptr));
        }
        _exit(127);
    }
    setpgid(p, p);
    close(fds[1]);
    if (in_fd >= 0) close(in_fd);
    int flags = fcntl(fds[0], F_GETFL, 0);
    if (flags >= 0) fcntl(fds[0], F_SETFL, flags | O_NONBLOCK);
    rd_job.pid = p;
    rd_job.fd = fds[0];
    rd_job.site = s.id;
    rd_job.buf.clear();
    rd_job.started = rd_now();
    return true;
}

static void rd_kick(const std::vector<Site>& sites) {
    rd_pump();
    if (rd_job.pid > 0 || sites.empty()) return;
    static size_t cursor = 0;
    if (cursor >= sites.size()) cursor = 0;
    for (size_t n = 0; n < sites.size(); n++) {
        size_t i = (cursor + n) % sites.size();
        const Site& s = sites[i];
        auto it = rd_cache.find(s.id);
        if (it != rd_cache.end() && rd_now() - it->second.at < 15) continue;
        cursor = i + 1;
        rd_spawn(s);
        return;
    }
}

// Public signer for a child whose Spring does not send hl_key yet.
// The remote script prints one 0x address, "none", or "unread". Never a secret.
struct CkView {
    std::string addr;
    std::string state;
    double at = 0;
};

struct CkJob {
    pid_t pid = 0;
    int fd = -1;
    int in_fd = -1;
    std::string site;
    std::string buf;
    double started = 0;
};

static CkJob ck_job;
static std::map<std::string, CkView> ck_cache;

static bool is_pub_addr(const std::string& s) {
    if (s.size() != 42 || s[0] != '0' || (s[1] != 'x' && s[1] != 'X')) return false;
    for (size_t i = 2; i < s.size(); ++i)
        if (!std::isxdigit(static_cast<unsigned char>(s[i]))) return false;
    return true;
}

static const char* CK_SCRIPT = R"PY(
import json, os, re, subprocess
ADDR = re.compile(r"0x[0-9a-fA-F]{40}")

def pub(path):
    try:
        j = json.load(open(path))
    except Exception:
        return ""
    if not isinstance(j, dict):
        return ""
    a = str(j.get("address") or "")
    return a if ADDR.fullmatch(a) else ""

for p in ("/opt/rimbaud/conf/hl_api_pub.json",
          os.path.expanduser("~/hummingbird/hummingbot/conf/hl_api_pub.json")):
    a = pub(p)
    if a:
        print(a)
        raise SystemExit(0)

code = r"""
import os, re, binascii
from eth_account import Account
pw = os.environ.get("CONFIG_PASSWORD") or ""
sec = ""
try:
    for ln in open("/home/hummingbot/conf/connectors/hyperliquid_perpetual.yml", errors="replace"):
        if ln.startswith("hyperliquid_perpetual_secret_key:"):
            sec = ln.split(":", 1)[1].strip()
            break
except OSError:
    sec = ""
addr = ""
if sec and pw:
    try:
        plain = Account.decrypt(binascii.unhexlify(sec).decode(), pw)
        if isinstance(plain, bytes):
            plain = plain.decode()
        addr = Account.from_key(plain).address
    except Exception:
        addr = ""
print(addr if re.fullmatch(r"0x[0-9a-fA-F]{40}", addr or "") else "none")
"""
try:
    names = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True, timeout=8)
except Exception:
    print("unread")
    raise SystemExit(0)
running = set(names.split())
order = ["hummingbot-dman-maker", "hummingbot-hl", "hummingbot-onlyup", "hummingbot-dman",
         "hummingbot-helliot", "hummingbot-surfn", "hummingbot-shortn", "hummingbot-bn-swing"]
name = next((n for n in order if n in running), "")
if not name:
    print("none")
    raise SystemExit(0)
r = subprocess.run(
    ["docker", "exec", name, "/opt/conda/envs/hummingbot/bin/python", "-c", code],
    capture_output=True, text=True, timeout=25,
)
line = ""
for ln in (r.stdout or "").splitlines():
    if ADDR.fullmatch(ln.strip()):
        line = ln.strip()
print(line or "none")
)PY";

static void ck_finish() {
    std::string last, cur;
    for (char c : ck_job.buf) {
        if (c == '\n') {
            if (!cur.empty()) last = cur;
            cur.clear();
        } else if (c != '\r') cur.push_back(c);
    }
    if (!cur.empty()) last = cur;
    CkView v;
    v.at = rd_now();
    if (is_pub_addr(last)) {
        v.addr = last;
        v.state = "addr";
    } else if (last == "none") {
        v.state = "none";
    } else {
        v.state = "unread";
    }
    if (!ck_job.site.empty()) ck_cache[ck_job.site] = v;
    if (ck_job.fd >= 0) close(ck_job.fd);
    if (ck_job.in_fd >= 0) close(ck_job.in_fd);
    ck_job.fd = -1;
    ck_job.in_fd = -1;
    ck_job.pid = 0;
    ck_job.buf.clear();
    ck_job.site.clear();
    ck_job.started = 0;
}

static void ck_pump() {
    if (ck_job.pid <= 0 && ck_job.fd < 0) return;
    if (ck_job.fd >= 0) {
        char b[256];
        for (;;) {
            ssize_t n = read(ck_job.fd, b, sizeof(b));
            if (n > 0) {
                ck_job.buf.append(b, b + n);
                if (ck_job.buf.size() > 200) ck_job.buf.resize(200);
                continue;
            }
            if (n == 0) {
                close(ck_job.fd);
                ck_job.fd = -1;
            }
            break;
        }
    }
    if (ck_job.pid > 0) {
        int st = 0;
        pid_t r = waitpid(ck_job.pid, &st, WNOHANG);
        if (r == ck_job.pid || (r < 0 && errno == ECHILD)) {
            if (ck_job.fd >= 0) {
                char b[256];
                ssize_t n = read(ck_job.fd, b, sizeof(b));
                if (n > 0) ck_job.buf.append(b, b + n);
            }
            ck_finish();
            return;
        }
        if (ck_job.started > 0 && rd_now() - ck_job.started > 20) {
            kill(-ck_job.pid, SIGTERM);
            waitpid(ck_job.pid, &st, WNOHANG);
            ck_job.buf = "unread";
            ck_finish();
        }
    }
}

static bool ck_spawn(const Site& s) {
    if (ck_job.pid > 0) return false;
    std::string target, identity, why;
    if (!rd_ssh_for(s, target, identity, why)) {
        CkView v;
        v.state = "unread";
        v.at = rd_now();
        ck_cache[s.id] = v;
        return false;
    }
    int in_fd[2], out_fd[2];
    if (pipe(in_fd) != 0) return false;
    if (pipe(out_fd) != 0) {
        close(in_fd[0]);
        close(in_fd[1]);
        return false;
    }
    pid_t p = fork();
    if (p < 0) {
        close(in_fd[0]); close(in_fd[1]);
        close(out_fd[0]); close(out_fd[1]);
        return false;
    }
    if (p == 0) {
        setpgid(0, 0);
        dup2(in_fd[0], STDIN_FILENO);
        dup2(out_fd[1], STDOUT_FILENO);
        int devnull = open("/dev/null", O_WRONLY);
        if (devnull >= 0) dup2(devnull, STDERR_FILENO);
        close(in_fd[0]); close(in_fd[1]);
        close(out_fd[0]); close(out_fd[1]);
        if (devnull >= 0) close(devnull);
        execlp("ssh", "ssh",
               "-i", identity.c_str(),
               "-o", "BatchMode=yes",
               "-o", "IdentitiesOnly=yes",
               "-o", "ConnectTimeout=3",
               "-o", "StrictHostKeyChecking=accept-new",
               target.c_str(),
               "python3", "-",
               static_cast<char*>(nullptr));
        _exit(127);
    }
    setpgid(p, p);
    close(in_fd[0]);
    close(out_fd[1]);
    std::string script = CK_SCRIPT;
    size_t off = 0;
    while (off < script.size()) {
        ssize_t n = write(in_fd[1], script.data() + off, script.size() - off);
        if (n < 0) break;
        off += static_cast<size_t>(n);
    }
    close(in_fd[1]);
    int flags = fcntl(out_fd[0], F_GETFL, 0);
    if (flags >= 0) fcntl(out_fd[0], F_SETFL, flags | O_NONBLOCK);
    ck_job.pid = p;
    ck_job.fd = out_fd[0];
    ck_job.in_fd = -1;
    ck_job.site = s.id;
    ck_job.buf.clear();
    ck_job.started = rd_now();
    return true;
}

static void ck_kick(const std::vector<Site>& sites) {
    ck_pump();
    if (ck_job.pid > 0) return;
    for (const Site& s : sites) {
        if (is_mothership(s) || !s.hl_addr.empty()) continue;
        auto it = ck_cache.find(s.id);
        if (it != ck_cache.end() && rd_now() - it->second.at < 180) continue;
        ck_spawn(s);
        return;
    }
}

static RdView rd_view(const Site& s) {
    auto it = rd_cache.find(s.id);
    if (it == rd_cache.end()) {
        RdView v;
        v.state = "unknown";
        v.detail = "checking";
        return v;
    }
    return it->second;
}

static std::string rd_line(const RdView& v) {
    if (v.state == "online") return "RustDesk online   id " + v.id;
    if (v.state == "operating") {
        std::string s = "RustDesk operating";
        if (!v.id.empty()) s += "   id " + v.id;
        if (!v.detail.empty()) s += "   " + v.detail;
        return s;
    }
    if (v.state == "down") return v.detail.empty() ? "RustDesk down" : "RustDesk down   " + v.detail;
    if (v.state == "absent") return "RustDesk not installed";
    if (!v.detail.empty()) return "RustDesk " + v.detail;
    return "RustDesk checking";
}

struct HbVer {
    std::string running, github, status, published, err;
};

static std::map<std::string, HbVer> hb_vers;

static void hb_fetch(const std::vector<Site>& sites) {
    for (const auto& s : sites) {
        HbVer v;
        json j = get_json(s.host, s.port, "/api/versions");
        if (j.contains("_err")) {
            v.err = jstr(j, "_err", "down");
            v.status = "down";
        } else {
            v.running = jstr(j, "running");
            v.github = jstr(j, "github");
            v.status = jstr(j, "status", "unknown");
            v.published = jstr(j, "published_at");
        }
        hb_vers[s.id] = v;
    }
}

static json hb_job_read() {
    std::ifstream in(conf_dir() + "/hb_upgrade_job.json");
    if (!in) return json::object();
    json j;
    try { in >> j; } catch (...) { return json::object(); }
    return j.is_object() ? j : json::object();
}

static json bk_job_read() {
    std::ifstream in(conf_dir() + "/backup_job.json");
    if (!in) return json::object();
    json j;
    try { in >> j; } catch (...) { return json::object(); }
    return j.is_object() ? j : json::object();
}

static json bk_status_read() {
    std::ifstream in("/tmp/rimbaud-backup-status.json");
    if (!in) return json::object();
    json j;
    try { in >> j; } catch (...) { return json::object(); }
    return j.is_object() ? j : json::object();
}

static std::string load_bk_dest() {
    std::ifstream in(conf_dir() + "/backup.json");
    if (!in) return {};
    json j;
    try { in >> j; } catch (...) { return {}; }
    return j.is_object() ? jstr(j, "dest") : std::string();
}

static void save_bk_dest(const std::string& dest) {
    json j = {{"dest", dest}, {"source_dev", "/dev/nvme0n1p2"}, {"tree", repo_root()}};
    std::string err;
    write_json_atomic(conf_dir() + "/backup.json", j, err);
}

static const Site* mothership_of(const std::vector<Site>& sites) {
    for (auto& s : sites)
        if (is_mothership(s)) return &s;
    return nullptr;
}

// Archy string 0.1g15 can match while the process on :5004 is an older binary.
// Compare the Spring health stamp (size and mtime) when both sides report it.
static bool spring_binary_differs(const Site& s, const Site* hub_site) {
    if (!hub_site || is_mothership(s) || !s.ok || !hub_site->ok) return false;
    bool spring = s.spring_size > 0 && hub_site->spring_size > 0
        && (s.spring_size != hub_site->spring_size || s.spring_mtime != hub_site->spring_mtime);
    bool desk = s.desk_size > 0 && hub_site->desk_size > 0
        && (s.desk_size != hub_site->desk_size || s.desk_mtime != hub_site->desk_mtime);
    return spring || desk;
}

// File size can match after a copy while the process on :5004 is still the old binary.
// The lamp build always sends hl_key.state. An older Spring omits it.
static bool spring_process_old(const Site& s, const Site* hub_site) {
    if (!hub_site || is_mothership(s) || !s.ok || !hub_site->ok) return false;
    if (s.err == "health only" || hub_site->err == "health only") return false;
    return !hub_site->hl_state.empty() && s.hl_state.empty();
}

static std::string version_field(const Site& s, const std::string& hub, const Site* hub_site) {
    if (is_mothership(s)) {
        std::string av = s.archy.empty() ? hub : s.archy;
        if (av.empty()) return "VERSION  (no archy.json yet)";
        return "VERSION  " + av + "   mothership code";
    }
    std::string running;
    if (s.ok) {
        running = s.archy.empty() ? s.archy_deployed : s.archy;
    }
    std::string pushed = s.archy_deployed.empty() ? s.deploy_ver : s.archy_deployed;
    if (pushed.empty()) pushed = s.deploy_ver;
    bool archy_behind = !hub.empty() && !running.empty() && running != hub;
    bool push_pending = !hub.empty() && running.empty() && (pushed.empty() || pushed != hub);
    bool bins_behind = spring_binary_differs(s, hub_site);
    bool proc_old = spring_process_old(s, hub_site);
    std::string line = "VERSION  ";
    if (archy_behind || bins_behind || proc_old) line += "BEHIND  ";
    else if (push_pending) line += "PUSH pending  ";
    if (proc_old) line += "process old  ";
    if (!running.empty()) line += "running " + running + "   ";
    else if (!s.ok) line += "Spring down   ";
    if (!pushed.empty()) {
        line += "last push " + pushed;
        if (!s.deploy_ts.empty()) line += "  " + s.deploy_ts;
    } else {
        line += "never pushed";
    }
    if (!hub.empty()) line += "   hub " + hub;
    if (bins_behind) line += "   Spring binary differs";
    return line;
}

// What is installed on a child: its Spring report, else the last push ledger.
static std::string child_installed(const Site& s) {
    if (!s.archy.empty()) return s.archy;
    if (!s.archy_deployed.empty()) return s.archy_deployed;
    return s.deploy_ver;
}

static bool child_has_latest(const Site& s, const Site* hub_site, const std::string& hub) {
    if (hub.empty() || is_mothership(s)) return false;
    if (spring_binary_differs(s, hub_site) || spring_process_old(s, hub_site)) return false;
    std::string inst = child_installed(s);
    return !inst.empty() && inst == hub;
}

static std::vector<Usb> list_usb() {
    std::vector<Usb> out;
    std::string script = repo_root() + "/install_new/make-usb.sh";
    if (access(script.c_str(), X_OK) != 0) return out;
    FILE* fp = popen((script + " --list 2>/dev/null").c_str(), "r");
    if (!fp) return out;
    std::string raw;
    char buf[4096];
    while (fgets(buf, sizeof(buf), fp)) raw += buf;
    pclose(fp);
    auto brace = raw.find('{');
    auto j = parse_or(brace == std::string::npos ? raw : raw.substr(brace));
    if (!j.contains("disks") || !j["disks"].is_array()) return out;
    for (auto& d : j["disks"]) {
        if (!d.is_object()) continue;
        Usb u;
        u.name = jstr(d, "name");
        u.path = jstr(d, "path");
        u.model = jstr(d, "model", "USB");
        u.size_h = jstr(d, "size_h");
        u.mount = jstr(d, "mount");
        u.note = jstr(d, "note");
        u.size = jnum(d, "size");
        u.ready = jflag(d, "ready") || u.size >= 2e9;
        if (u.note.empty()) u.note = u.ready ? "ready" : (u.size <= 0 ? "no media" : "too small");
        if (!u.path.empty()) out.push_back(u);
    }
    return out;
}

static int pick_usb_sel(const std::vector<Usb>& usbs, int cur) {
    if (cur >= 0 && cur < (int)usbs.size() && usbs[static_cast<size_t>(cur)].ready) return cur;
    for (int i = 0; i < (int)usbs.size(); i++)
        if (usbs[static_cast<size_t>(i)].ready) return i;
    return usbs.empty() ? -1 : 0;
}

int main(int argc, char** argv) {
    bool headless = false;
    for (int i = 1; i < argc; i++) {
        std::string a = argv[i];
        if (a == "--headless" || a == "--once") headless = true;
        else if (a == "--help") {
            fprintf(stdout,
                "rimbaud-meta-desk — meta matrix (X11)\n"
                "  HOME    connected matrices (this box + VPN)\n"
                "  DEPLOY  LIVE PUSH, USB kit (existing Mint), or Mint Cinnamon ISO\n"
                "  SYSTEM  Hummingbot image compare, download, then Execute\n"
                "  BACKUP  Copy to USB — reinstall this setup on fresh Linux Mint\n"
                "  GET only. Never CLOSE/OPEN. Push copies code, not wallets.\n");
            return 0;
        }
    }

    auto sites = load_sites();
    for (auto& s : sites) poll_site(s);
    apply_deploys(sites);
    auto usbs = list_usb();
    int usb_sel_boot = pick_usb_sel(usbs, 0);

    if (headless) {
        printf("rimBaud // META DESK\n");
        printf("matrices %zu   hub %s\n", sites.size(), local_archy().c_str());
        for (auto& s : sites) {
            double deadline = rd_now() + 8;
            while (rd_now() < deadline) {
                rd_kick(sites);
                auto it = rd_cache.find(s.id);
                if (it != rd_cache.end() && it->second.at > 0) break;
                timespec sl{0, 50 * 1000 * 1000};
                nanosleep(&sl, nullptr);
            }
            rd_pump();
        }
        for (auto& s : sites)
            printf("  %s  %s:%d  %s  %s  %s  eq %s  IP %.0f/%.0f%s\n",
                   s.name.c_str(), s.host.c_str(), s.port,
                   s.ok ? "UP" : "DOWN", rd_line(rd_view(s)).c_str(),
                   version_field(s, local_archy(), mothership_of(sites)).c_str(),
                   money(s.eq).c_str(), s.ip_left, s.ip_cap,
                   s.main_only ? "  MAIN ACCOUNT  fresh  no subaccounts" : "");
        printf("usb sticks %zu\n", usbs.size());
        for (auto& u : usbs)
            printf("  %s  %s  %s  %s\n", u.path.c_str(), u.size_h.c_str(),
                   u.model.c_str(), u.mount.empty() ? "unmounted" : u.mount.c_str());
        {
            std::ifstream in(repo_root() + "/dist/mint-image/STATUS.json");
            if (in) {
                json st;
                try { in >> st; } catch (...) { st = json::object(); }
                printf("mint iso  phase %s  %s\n",
                       jstr(st, "phase", "none").c_str(),
                       jstr(st, "iso", jstr(st, "msg")).c_str());
            } else {
                printf("mint iso  not built\n");
            }
        }
        return 0;
    }

    Display* dpy = XOpenDisplay(nullptr);
    if (!dpy) {
        fprintf(stderr, "no DISPLAY — rimbaud-meta-desk --headless\n");
        return 1;
    }
    signal(SIGINT, on_int);
    signal(SIGTERM, on_int);
    int scr = DefaultScreen(dpy);
    Colormap cm = DefaultColormap(dpy, scr);
    unsigned long bg = rgb(dpy, cm, 2, 8, 4);
    unsigned long panel = rgb(dpy, cm, 4, 18, 8);
    unsigned long border = rgb(dpy, cm, 13, 59, 31);
    unsigned long green = rgb(dpy, cm, 0, 255, 106);
    unsigned long green_dim = rgb(dpy, cm, 0, 179, 71);
    unsigned long text = rgb(dpy, cm, 157, 255, 192);
    unsigned long muted = rgb(dpy, cm, 61, 122, 85);
    unsigned long rose = rgb(dpy, cm, 255, 77, 109);
    unsigned long cyan = rgb(dpy, cm, 126, 203, 255);
    unsigned long gold = rgb(dpy, cm, 240, 185, 11);
    unsigned long navy = rgb(dpy, cm, 3, 22, 10);
    unsigned long orange = rgb(dpy, cm, 255, 118, 77);
    unsigned long ab_panel = rgb(dpy, cm, 36, 36, 36);
    unsigned long ab_text = rgb(dpy, cm, 220, 220, 220);
    unsigned long ab_muted = rgb(dpy, cm, 140, 140, 140);
    unsigned long clip = rgb(dpy, cm, 44, 44, 44);
    unsigned long rainbow[24];
    for (int i = 0; i < 24; i++) {
        int rr, gg, bb;
        hsv_rgb(i * 15.0, 1.0, 1.0, rr, gg, bb);
        rainbow[i] = rgb(dpy, cm, rr, gg, bb);
    }

    int W = 1280, H = 800;
    Window win = XCreateSimpleWindow(dpy, RootWindow(dpy, scr), 70, 40, W, H, 0, border, bg);
    XStoreName(dpy, win, "rimBaud // META");
    Atom wm_delete = XInternAtom(dpy, "WM_DELETE_WINDOW", False);
    Atom atom_clipboard = XInternAtom(dpy, "CLIPBOARD", False);
    Atom atom_utf8 = XInternAtom(dpy, "UTF8_STRING", False);
    Atom atom_paste = XInternAtom(dpy, "RIMBAUD_API_PASTE", False);
    Atom atom_targets = XInternAtom(dpy, "TARGETS", False);
    Atom atom_text = XInternAtom(dpy, "TEXT", False);
    XSetWMProtocols(dpy, win, &wm_delete, 1);
    XSelectInput(dpy, win, ExposureMask | StructureNotifyMask | ButtonPressMask |
                               ButtonReleaseMask | PointerMotionMask | KeyPressMask);
    XMapWindow(dpy, win);
    Cursor cur_arrow = XCreateFontCursor(dpy, XC_left_ptr);
    Cursor cur_hand = XCreateFontCursor(dpy, XC_hand2);
    Cursor cur_xterm = XCreateFontCursor(dpy, XC_xterm);
    XDefineCursor(dpy, win, cur_arrow);
    GC gc = XCreateGC(dpy, win, 0, nullptr);
    XFontStruct* fs = XLoadQueryFont(dpy, "9x15");
    if (!fs) fs = XLoadQueryFont(dpy, "fixed");
    if (fs) XSetFont(dpy, gc, fs->fid);
    Pixmap buf = XCreatePixmap(dpy, win, W, H, DefaultDepth(dpy, scr));

    Page page = PgHome;
    int usb_sel = usb_sel_boot;
    int deploy_mode = 0; // 0 USB kit (existing Mint), 1 Mint Cinnamon ISO
    std::string toast;
    double toast_until = 0;
    bool usb_busy = false;
    pid_t usb_pid = 0;
    std::string usb_log = "/tmp/rimbaud-usb.log";
    int usb_exit = -1;
    bool mint_busy = false;
    pid_t mint_pid = 0;
    std::string mint_log = "/tmp/rimbaud-mint-iso.log";
    int mint_job = 0; // 1 build, 2 write
    bool mint_then_write = false;
    int mint_exit = -1;
    BootMedia boot_cache;
    bool live_busy = false;
    bool hb_all = false;
    bool hb_menu = false;
    std::string hb_sel = "local";
    pid_t hb_pid = 0;
    double hb_fetch_at = 0;
    std::string bk_dest = load_bk_dest();
    if (bk_dest.empty() && access("/media/usb/USB-STICK", R_OK) == 0)
        bk_dest = "/media/usb/USB-STICK";
    bool bk_picked = !bk_dest.empty();
    bool bk_edit = false;
    std::string bk_buf;
    bool api_edit = false;
    bool api_busy = false;
    bool api_paste_wait = false;
    std::string api_buf;
    std::string api_addr;
    std::string api_exch;
    std::string api_note = "not saved in SQLite yet";
    int api_fd = -1;
    std::string api_out;
    load_api_view(api_addr, api_exch, api_note);
    std::vector<std::string> bk_rows;
    pid_t bk_pid = 0;
    pid_t bk_stat_pid = 0;
    double bk_stat_at = 0;
    pid_t live_pid = 0;
    std::string live_log = "/tmp/rimbaud-live-push.log";
    int live_sel = -1;
    std::vector<LanBox> lan_boxes;
    std::vector<std::string> add_targets;
    pid_t lan_pid = 0;
    bool lan_scanned = false;
    double lan_started = 0;
    bool show_push_log = false;
    int live_exit = -1;
    int log_kind = 0; // 0 live-push, 1 usb kit, 2 mint iso
    double log_close_at = 0;
    std::string live_site_label;
    std::string job_label;
    std::vector<std::string> log_lines;
    Rect log_body{0, 0, 0, 0};
    int log_view_start = 0;
    int log_cw = 9;
    int log_lh = 16;
    int log_tx = 0;
    int log_ty = 0;
    LogPos sel_a, sel_b;
    bool has_sel = false;
    bool selecting = false;
    std::string clip_store;
    double last_log_click = 0;
    int last_log_click_line = -1;
    std::string rename_id;
    std::string rename_buf;
    std::string ssh_id;
    int ssh_field = 0;
    bool ssh_paste_wait = false;
    std::string ssh_vpn, ssh_lan, ssh_root, ssh_live;
    double last_name_click = 0;
    std::string last_name_click_id;
    bool gw_cached_up = false;
    double gw_probe_at = 0;
    unsigned long sel_bg = rgb(dpy, cm, 24, 80, 160);
    unsigned long sel_fg = rgb(dpy, cm, 255, 255, 255);

    auto now = []() {
        timespec ts{};
        clock_gettime(CLOCK_MONOTONIC, &ts);
        return ts.tv_sec + ts.tv_nsec / 1e9;
    };
    auto say = [&](const std::string& s) {
        toast = s;
        toast_until = now() + 6;
    };
    auto probe_gw = [&]() {
        if (now() - gw_probe_at < 1.0) return gw_cached_up;
        gw_probe_at = now();
        gw_cached_up = iphone_gw_up();
        return gw_cached_up;
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
    // Same halo as the mothership HOME card. The Mint boot screen uses it too.
    auto rainbow_frame = [&](Rect card) {
        int phase = (int)(now() * 18.0) % 24;
        auto band = [&](int x, int yy, int ww, int hh, int along) {
            XSetForeground(dpy, gc, rainbow[(phase + along + 24) % 24]);
            XFillRectangle(dpy, buf, gc, x, yy, ww, hh);
        };
        const int t = 4;
        Rect halo{card.x - 4, card.y - 4, card.w + 8, card.h + 8};
        int step = 10;
        for (int x = 0; x < halo.w; x += step)
            band(halo.x + x, halo.y, step, t, x / 2);
        for (int x = 0; x < halo.w; x += step)
            band(halo.x + x, halo.y + halo.h - t, step, t, (halo.w + halo.h + x) / 2);
        for (int yy = 0; yy < halo.h; yy += step)
            band(halo.x, halo.y + yy, t, step, (halo.w + yy) / 2);
        for (int yy = 0; yy < halo.h; yy += step)
            band(halo.x + halo.w - t, halo.y + yy, t, step, yy / 2);
    };

    std::vector<Hit> hits;

    auto wallet_sh = repo_root() + "/HL_wallet_creator/hl_wallet_creator.sh";
    auto run_wallet = [&](const std::string& args) {
        if (access(wallet_sh.c_str(), X_OK) != 0) {
            say("missing HL_wallet_creator/hl_wallet_creator.sh");
            return;
        }
        pid_t pid = fork();
        if (pid < 0) { say("fork failed"); return; }
        if (pid == 0) {
            std::string cmd = wallet_sh + " " + args;
            execl("/bin/bash", "bash", "-lc", cmd.c_str(), static_cast<char*>(nullptr));
            _exit(127);
        }
        say("HL_wallet_creator  " + args);
    };

    auto open_job_log = [&](int kind, const std::string& label) {
        log_kind = kind;
        job_label = label;
        show_push_log = true;
        log_close_at = 0;
        has_sel = false;
        selecting = false;
    };

    auto start_usb = [&]() {
        if (usb_busy || mint_busy) { say("deploy job already running"); return; }
        if (usbs.empty() || usb_sel < 0 || usb_sel >= (int)usbs.size()) {
            say("plug a USB stick (>= 2G) then Refresh — empty card readers are not a stick");
            return;
        }
        auto& u = usbs[static_cast<size_t>(usb_sel)];
        if (!u.ready) {
            say(u.note.empty() ? "that USB is not ready (no media or < 2G) — pick the Flash Disk" : u.note);
            return;
        }
        std::string gui = repo_root() + "/install_new/make-usb-gui.sh";
        std::string script = repo_root() + "/install_new/make-usb.sh";
        if (access(gui.c_str(), X_OK) != 0 && access(script.c_str(), X_OK) != 0) {
            say("missing install_new/make-usb.sh");
            return;
        }
        std::string target = u.mount.empty() ? u.path : u.mount;
        usb_exit = -1;
        pid_t pid = fork();
        if (pid < 0) { say("fork failed"); return; }
        if (pid == 0) {
            int lg = open(usb_log.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
            if (lg >= 0) {
                std::string head = std::string("CMD  bash install_new/make-usb.sh --write ") +
                                   target + "\n";
                ssize_t _w = write(lg, head.data(), head.size());
                (void)_w;
                dup2(lg, 1);
                dup2(lg, 2);
                close(lg);
            }
            setenv("USB_FROM_META", "1", 1);
            if (access(gui.c_str(), X_OK) == 0)
                execl(gui.c_str(), "make-usb-gui.sh", target.c_str(), static_cast<char*>(nullptr));
            else
                execl("/bin/bash", "bash", "-lc",
                      (std::string("sudo ") + script + " --write '" + target + "'").c_str(),
                      static_cast<char*>(nullptr));
            _exit(127);
        }
        usb_pid = pid;
        usb_busy = true;
        open_job_log(1, target);
        say("USB kit  " + target + "  ·  log open  ·  no wallets");
    };

    auto poll_usb_job = [&]() {
        if (!usb_busy || !usb_pid) return;
        int st = 0;
        pid_t r = waitpid(usb_pid, &st, WNOHANG);
        if (r == 0) return;
        usb_busy = false;
        usb_pid = 0;
        usb_exit = WIFEXITED(st) ? WEXITSTATUS(st) : 1;
        show_push_log = true;
        log_kind = 1;
        std::ifstream in(usb_log);
        std::string last, line;
        while (std::getline(in, line)) if (!line.empty()) last = strip_ansi(line, 72);
        if (usb_exit == 0) {
            say(last.empty() ? "USB kit ready — on the new box:  sudo bash install.sh" : last);
            log_close_at = now() + 2.5;
        } else {
            say(last.empty()
                    ? "USB write failed — sudo in the dialog, or: sudo ./install_new/make-usb.sh --write /dev/sdX"
                    : last);
            log_close_at = 0;
        }
        usbs = list_usb();
        usb_sel = pick_usb_sel(usbs, usb_sel);
    };

    auto mint_status = [&]() {
        std::ifstream in(repo_root() + "/dist/mint-image/STATUS.json");
        if (!in) return json{{"phase", "none"}, {"msg", "no ISO built yet"}};
        json j;
        try { in >> j; } catch (...) { return json{{"phase", "none"}, {"msg", "bad STATUS.json"}}; }
        return j.is_object() ? j : json{{"phase", "none"}};
    };
    auto mint_tail = [&]() {
        std::ifstream in(mint_log);
        std::string last, line;
        while (std::getline(in, line)) if (!line.empty()) last = line;
        return ascii(last, 78);
    };

    auto start_mint_build = [&]() {
        if (mint_busy || usb_busy) { say("deploy job already running"); return; }
        {
            json st = mint_status();
            BootMedia bg = sample_boot_media(st, false, 0);
            if (bg.on) {
                say("Startmedium laeuft schon im Hintergrund — Ladebalken auf DEPLOY");
                return;
            }
        }
        std::string gui = repo_root() + "/install_new/mint-image/gui-build.sh";
        std::string script = repo_root() + "/install_new/mint-image/build-iso.sh";
        if (access(gui.c_str(), X_OK) != 0 && access(script.c_str(), X_OK) != 0) {
            say("missing install_new/mint-image/build-iso.sh");
            return;
        }
        pid_t pid = fork();
        if (pid < 0) { say("fork failed"); return; }
        if (pid == 0) {
            int lg = open(mint_log.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
            if (lg >= 0) { dup2(lg, 1); dup2(lg, 2); close(lg); }
            if (access(gui.c_str(), X_OK) == 0)
                execl(gui.c_str(), "gui-build.sh", static_cast<char*>(nullptr));
            else
                execl("/bin/bash", "bash", "-lc",
                      (std::string("sudo ") + script).c_str(), static_cast<char*>(nullptr));
            _exit(127);
        }
        mint_pid = pid;
        mint_busy = true;
        mint_job = 1;
        mint_exit = -1;
        open_job_log(2, "build hybrid ISO");
        say("Mint ISO build started — official 22.3 download + remaster, ~20 min");
    };

    auto start_mint_write = [&]() {
        if (mint_busy || usb_busy) { say("deploy job already running"); return; }
        {
            json bgst = mint_status();
            BootMedia bg = sample_boot_media(bgst, false, 0);
            if (bg.on) {
                say("image is already being rebuilt — the stick is formatted when that finishes");
                return;
            }
        }
        json st = mint_status();
        if (jstr(st, "phase") != "done" && jstr(st, "iso").empty()) {
            say("build the Mint ISO first");
            return;
        }
        if (usbs.empty() || usb_sel < 0 || usb_sel >= (int)usbs.size()) {
            say("plug a USB stick (>= 8G) then Refresh");
            return;
        }
        auto& u = usbs[static_cast<size_t>(usb_sel)];
        if (!u.ready) {
            say(u.note.empty() ? "that USB is not ready — pick a stick >= 8G" : u.note);
            return;
        }
        if (u.size > 0 && u.size < 8e9) {
            say("stick too small for Mint ISO — need >= 8G");
            return;
        }
        std::string gui = repo_root() + "/install_new/mint-image/gui-write.sh";
        std::string script = repo_root() + "/install_new/mint-image/write-usb.sh";
        if (access(gui.c_str(), X_OK) != 0 && access(script.c_str(), X_OK) != 0) {
            say("missing install_new/mint-image/write-usb.sh");
            return;
        }
        bool ventoy = false;
        {
            std::string m = u.mount;
            for (char& c : m) if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
            ventoy = m.find("ventoy") != std::string::npos;
        }
        std::string target = ventoy ? u.mount : u.path;
        pid_t pid = fork();
        if (pid < 0) { say("fork failed"); return; }
        if (pid == 0) {
            int lg = open(mint_log.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
            if (lg >= 0) { dup2(lg, 1); dup2(lg, 2); close(lg); }
            if (access(gui.c_str(), X_OK) == 0)
                execl(gui.c_str(), "gui-write.sh", target.c_str(), static_cast<char*>(nullptr));
            else
                execl("/bin/bash", "bash", "-lc",
                      (std::string("sudo ") + script + (ventoy ? " --copy '" : " --dd '") +
                       target + "'").c_str(),
                      static_cast<char*>(nullptr));
            _exit(127);
        }
        mint_pid = pid;
        mint_busy = true;
        mint_job = 2;
        mint_exit = -1;
        open_job_log(2, target);
        say(ventoy ? ("copy current ISO onto Ventoy " + target)
                   : ("Mint ISO to " + target + "  ·  rebuilds first if the image is older than the installer"));
    };

    auto start_hb = [&](const char* mode) {
        if (hb_pid) { say("Hummingbot job already running"); return; }
        if (std::string(mode) == "execute") {
            json j = hb_job_read();
            if (jstr(j, "phase") != "ready") {
                say("download the image first — Execute stays off until the pull finishes");
                return;
            }
        }
        std::string script = repo_root() + "/install_new/hb-upgrade.py";
        if (access(script.c_str(), R_OK) != 0) { say("missing install_new/hb-upgrade.py"); return; }
        std::vector<std::string> ids;
        if (hb_all) ids.push_back("all");
        else ids.push_back(hb_sel.empty() ? "local" : hb_sel);
        pid_t pid = fork();
        if (pid < 0) { say("fork failed"); return; }
        if (pid == 0) {
            std::vector<std::string> args = {"python3", script, mode};
            args.insert(args.end(), ids.begin(), ids.end());
            std::vector<char*> av;
            for (auto& a : args) av.push_back(const_cast<char*>(a.c_str()));
            av.push_back(nullptr);
            execvp("python3", av.data());
            _exit(127);
        }
        hb_pid = pid;
        hb_menu = false;
        say(std::string(mode) == "execute"
                ? "Execute started — hunters recreate on the new image"
                : "Download started — hunters keep running until you press Execute");
    };

    auto poll_hb_job = [&]() {
        if (!hb_pid) return;
        int st = 0;
        pid_t r = waitpid(hb_pid, &st, WNOHANG);
        if (r == 0) return;
        hb_pid = 0;
        json j = hb_job_read();
        std::string phase = jstr(j, "phase", "idle");
        std::string err = jstr(j, "error");
        if (!err.empty()) say(err);
        else if (phase == "ready") say("image downloaded — press Execute when you want the hunters recreated");
        else if (phase == "done") say("Hummingbot hunters recreated");
        else say("Hummingbot job finished (" + phase + ")");
    };

    auto spawn_bk_status = [&]() {
        if (bk_stat_pid) return;
        std::string tool = repo_root() + "/rimbaud_meta_c++/rimbaud-backup";
        if (access(tool.c_str(), X_OK) != 0) return;
        pid_t p = fork();
        if (p < 0) return;
        if (p == 0) {
            int lg = open("/tmp/rimbaud-backup-status.log", O_WRONLY | O_CREAT | O_APPEND, 0644);
            if (lg >= 0) { dup2(lg, 1); dup2(lg, 2); close(lg); }
            execl(tool.c_str(), "rimbaud-backup", "status", static_cast<char*>(nullptr));
            _exit(127);
        }
        bk_stat_pid = p;
        bk_stat_at = now();
    };

    auto start_bk = [&]() {
        if (bk_pid) { say("copy already running"); return; }
        if (bk_dest.empty()) { say("choose the USB folder"); return; }
        struct stat ds{};
        if (stat(bk_dest.c_str(), &ds) != 0 || !S_ISDIR(ds.st_mode)) {
            say("that folder is not there — plug in the stick");
            return;
        }
        std::string tool = repo_root() + "/rimbaud_meta_c++/rimbaud-backup";
        if (access(tool.c_str(), X_OK) != 0) { say("missing rimbaud-backup"); return; }
        save_bk_dest(bk_dest);
        pid_t pid = fork();
        if (pid < 0) { say("fork failed"); return; }
        if (pid == 0) {
            setpgid(0, 0);
            std::string ask = repo_root() + "/install_new/askpass-zenity.sh";
            setenv("SUDO_ASKPASS", ask.c_str(), 1);
            setenv("RIMBAUD_ASK_TITLE", "rimBaud backup", 1);
            setenv("RIMBAUD_ASK_TEXT",
                   "Sudo password to read root-only VPN and Condor files into the USB copy.",
                   1);
            if (!getenv("DISPLAY")) setenv("DISPLAY", ":0", 1);
            execl(tool.c_str(), "rimbaud-backup", "copy", bk_dest.c_str(), static_cast<char*>(nullptr));
            _exit(127);
        }
        setpgid(pid, pid);
        bk_pid = pid;
        say("Copy to USB started — hunters keep running");
    };

    auto stop_bk = [&]() {
        json j = bk_job_read();
        pid_t g = bk_pid > 1 ? bk_pid : static_cast<pid_t>(jnum(j, "pgrp", 0));
        if (g <= 1) { say("no backup process"); return; }
        kill(-g, SIGTERM);
        say("stopping copy — the partial archive will be removed");
    };

    auto poll_bk_job = [&]() {
        if (!bk_pid) return;
        int st = 0;
        pid_t r = waitpid(bk_pid, &st, WNOHANG);
        if (r == 0) return;
        if (r < 0 && errno != ECHILD) return;
        int code = (r > 0 && WIFEXITED(st)) ? WEXITSTATUS(st) : 1;
        bk_pid = 0;
        json j = bk_job_read();
        std::string phase = jstr(j, "phase", "idle");
        std::string err = jstr(j, "error");
        if (!err.empty()) say(err);
        else if (phase == "done") say("Copy to USB ready — on fresh Mint run bash install.sh");
        else if (code != 0) say("copy failed — archive was not kept");
        else say("copy finished");
    };

    auto poll_bk_stat = [&]() {
        if (!bk_stat_pid) return;
        int st = 0;
        pid_t r = waitpid(bk_stat_pid, &st, WNOHANG);
        if (r == 0) return;
        if (r < 0 && errno != ECHILD) return;
        bk_stat_pid = 0;
    };

    auto poll_mint_job = [&]() {
        if (!mint_busy || !mint_pid) return;
        int st = 0;
        pid_t r = waitpid(mint_pid, &st, WNOHANG);
        if (r == 0) return;
        mint_busy = false;
        mint_pid = 0;
        int kind = mint_job;
        mint_job = 0;
        mint_exit = WIFEXITED(st) ? WEXITSTATUS(st) : 1;
        show_push_log = true;
        log_kind = 2;
        if (mint_exit == 0 && kind == 1 && mint_then_write) {
            mint_then_write = false;
            say("new image ready — formatting the selected stick with it");
            start_mint_write();
            return;
        }
        mint_then_write = false;
        if (mint_exit == 0) {
            json js = mint_status();
            if (kind == 1)
                say("Mint ISO ready — " + ascii(jstr(js, "iso", "dist/mint-image/*.iso"), 50));
            else
                say("Mint USB ready — boot: Start rimBaud live / Install unattended");
            log_close_at = now() + 2.5;
        } else {
            std::string last = mint_tail();
            say(last.empty()
                    ? "Mint ISO job failed — sudo ./install_new/mint-image/build-iso.sh"
                    : last);
            log_close_at = 0;
        }
        usbs = list_usb();
        usb_sel = pick_usb_sel(usbs, usb_sel);
    };

    auto start_live = [&](bool samba_only) {
        if (live_busy) {
            show_push_log = true;
            say("LIVE PUSH already running — log open");
            return;
        }
        std::string site_id;
        std::string site_name;
        for (size_t i = 0; i < sites.size(); i++) {
            if (sites[i].host == "127.0.0.1" || sites[i].host == "localhost") continue;
            if (live_sel < 0) live_sel = static_cast<int>(i);
            if (static_cast<int>(i) == live_sel) {
                site_id = sites[i].id;
                site_name = sites[i].name;
                break;
            }
        }
        if (site_id.empty()) {
            for (auto& s : sites)
                if (s.host != "127.0.0.1" && s.host != "localhost") {
                    site_id = s.id;
                    site_name = s.name;
                    break;
                }
        }
        if (site_id.empty()) { say("no remote matrix (remote) in meta.json"); return; }
        std::string script = repo_root() + "/install_new/live-push.sh";
        if (access(script.c_str(), X_OK) != 0) {
            say("missing install_new/live-push.sh");
            return;
        }
        live_exit = -1;
        live_site_label = site_name.empty() ? site_id : (site_name + "  (" + site_id + ")");
        pid_t pid = fork();
        if (pid < 0) { say("fork failed"); return; }
        if (pid == 0) {
            int lg = open(live_log.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
            if (lg >= 0) {
                std::string head = std::string("CMD  bash install_new/live-push.sh ") + site_id +
                                   (samba_only ? " --samba-only" : "") + "\n";
                ssize_t _w = write(lg, head.data(), head.size());
                (void)_w;
                dup2(lg, 1);
                dup2(lg, 2);
                close(lg);
            }
            setenv("LIVE_PUSH_LOG", live_log.c_str(), 1);
            setenv("LIVE_PUSH_FROM_META", "1", 1);
            if (samba_only)
                execl("/bin/bash", "bash", script.c_str(), site_id.c_str(), "--samba-only",
                      static_cast<char*>(nullptr));
            else
                execl("/bin/bash", "bash", script.c_str(), site_id.c_str(),
                      static_cast<char*>(nullptr));
            _exit(127);
        }
        live_pid = pid;
        live_busy = true;
        open_job_log(0, live_site_label);
        say(std::string("LIVE PUSH ") + site_id + (samba_only ? "  via share" : "  via SSH/VPN") +
            "  ·  log window  ·  code only, no wallets, no HL close");
    };

    auto poll_live_job = [&]() {
        if (!live_busy || !live_pid) return;
        int st = 0;
        pid_t r = waitpid(live_pid, &st, WNOHANG);
        if (r == 0) return;
        live_busy = false;
        live_pid = 0;
        live_exit = WIFEXITED(st) ? WEXITSTATUS(st) : 1;
        show_push_log = true;
        log_kind = 0;
        std::ifstream in(live_log);
        std::string last, line;
        while (std::getline(in, line)) if (!line.empty()) last = strip_ansi(line, 72);
        if (live_exit == 0) {
            say(last.empty() ? "LIVE PUSH ok — Spring/hunters restarted, clips stay" : last);
            log_close_at = now() + 1.8;
        } else if (live_exit == 2) {
            say(last.empty() ? "no SSH — staged share, run REMOTE-SSH.sh on remote" : last);
            log_close_at = 0;
        } else {
            say(last.empty() ? "LIVE PUSH failed — log stays open" : last);
            log_close_at = 0;
        }
    };

    auto reload_log_lines = [&]() {
        log_lines.clear();
        const std::string& p = log_kind == 1 ? usb_log : (log_kind == 2 ? mint_log : live_log);
        std::ifstream lf(p);
        std::string ln;
        while (lf && std::getline(lf, ln)) {
            auto t = strip_ansi(ln);
            if (!t.empty()) log_lines.push_back(t);
        }
    };

    auto pos_at = [&](int x, int y) {
        LogPos p;
        if (log_lines.empty()) return p;
        int row = (y - log_ty) / (log_lh > 0 ? log_lh : 16);
        if (row < 0) row = 0;
        int vis = log_view_start + row;
        if (vis >= (int)log_lines.size()) vis = (int)log_lines.size() - 1;
        if (vis < 0) vis = 0;
        int col = (x - log_tx + (log_cw / 2)) / (log_cw > 0 ? log_cw : 9);
        if (col < 0) col = 0;
        int linelen = (int)log_lines[static_cast<size_t>(vis)].size();
        if (col > linelen) col = linelen;
        p.line = vis;
        p.col = col;
        return p;
    };

    auto sel_span = [&]() {
        LogPos a = sel_a, b = sel_b;
        if (logpos_lt(b, a)) { auto t = a; a = b; b = t; }
        return std::pair<LogPos, LogPos>{a, b};
    };

    auto sel_text = [&]() {
        if (log_lines.empty()) return std::string();
        if (!has_sel) {
            std::string o;
            for (size_t i = 0; i < log_lines.size(); i++) {
                if (i) o += '\n';
                o += log_lines[i];
            }
            return o;
        }
        auto sp = sel_span();
        LogPos a = sp.first, b = sp.second;
        if (a.line < 0) a.line = 0;
        if (b.line >= (int)log_lines.size()) b.line = (int)log_lines.size() - 1;
        if (a.line == b.line) {
            const auto& s = log_lines[static_cast<size_t>(a.line)];
            int lo = std::max(0, std::min(a.col, (int)s.size()));
            int hi = std::max(0, std::min(b.col, (int)s.size()));
            if (hi < lo) { int t = lo; lo = hi; hi = t; }
            return s.substr(static_cast<size_t>(lo), static_cast<size_t>(hi - lo));
        }
        std::string o;
        {
            const auto& s = log_lines[static_cast<size_t>(a.line)];
            int lo = std::max(0, std::min(a.col, (int)s.size()));
            o += s.substr(static_cast<size_t>(lo));
            o += '\n';
        }
        for (int i = a.line + 1; i < b.line; i++) {
            o += log_lines[static_cast<size_t>(i)];
            o += '\n';
        }
        {
            const auto& s = log_lines[static_cast<size_t>(b.line)];
            int hi = std::max(0, std::min(b.col, (int)s.size()));
            o += s.substr(0, static_cast<size_t>(hi));
        }
        return o;
    };

    auto claim_clip = [&](const std::string& s) {
        clip_store = s;
        XSetSelectionOwner(dpy, atom_clipboard, win, CurrentTime);
        XSetSelectionOwner(dpy, XA_PRIMARY, win, CurrentTime);
        XFlush(dpy);
        bool ok = XGetSelectionOwner(dpy, atom_clipboard) == win;
        char msg[96];
        snprintf(msg, sizeof(msg), ok ? "copied %d chars  ·  Strg-C / Ctrl-C" : "clipboard claim failed",
                 (int)clip_store.size());
        say(msg);
        return ok;
    };

    auto copy_log = [&]() {
        reload_log_lines();
        bool whole = !has_sel || (sel_a.line == sel_b.line && sel_a.col == sel_b.col);
        bool old = has_sel;
        if (whole) has_sel = false;
        std::string s = sel_text();
        has_sel = old;
        if (s.empty()) { say("log is empty"); return; }
        claim_clip(s);
    };

    auto handle_sel_req = [&](const XSelectionRequestEvent& req) {
        XEvent ev{};
        ev.xselection.type = SelectionNotify;
        ev.xselection.display = req.display;
        ev.xselection.requestor = req.requestor;
        ev.xselection.selection = req.selection;
        ev.xselection.target = req.target;
        ev.xselection.time = req.time;
        ev.xselection.property = None;
        Atom prop = req.property != None ? req.property : req.target;
        if (req.target == atom_targets) {
            Atom list[4] = {atom_targets, XA_STRING, atom_utf8, atom_text};
            XChangeProperty(dpy, req.requestor, prop, XA_ATOM, 32, PropModeReplace,
                            reinterpret_cast<unsigned char*>(list), 4);
            ev.xselection.property = prop;
        } else if (req.target == XA_STRING || req.target == atom_utf8 || req.target == atom_text) {
            Atom type = req.target == atom_utf8 ? atom_utf8 : XA_STRING;
            XChangeProperty(dpy, req.requestor, prop, type, 8, PropModeReplace,
                            const_cast<unsigned char*>(
                                reinterpret_cast<const unsigned char*>(clip_store.data())),
                            static_cast<int>(clip_store.size()));
            ev.xselection.property = prop;
        }
        XSendEvent(dpy, req.requestor, True, NoEventMask, &ev);
        XFlush(dpy);
    };

    auto redraw = [&]() {
        hits.clear();
        add_targets.clear();
        fill(bg, {0, 0, W, H});
        const int NAV_H = 76;
        fill(navy, {0, 0, W, NAV_H});
        XSetForeground(dpy, gc, border);
        XDrawLine(dpy, buf, gc, 0, NAV_H, W, NAV_H);
        txt(rgb(dpy, cm, 42, 159, 214), 14, 18, "rimBaud  //  META");
        int n_up = 0;
        for (auto& s : sites) if (s.ok) n_up++;
        std::string hub_ver = local_archy();
        auto push_target = [&]() -> int {
            if (live_sel >= 0 && live_sel < (int)sites.size()
                && !is_mothership(sites[static_cast<size_t>(live_sel)]))
                return live_sel;
            for (int i = 0; i < (int)sites.size(); i++)
                if (!is_mothership(sites[static_cast<size_t>(i)])) return i;
            return -1;
        };
        const Site* hub_site = mothership_of(sites);
        auto show_push_for = [&](int idx) {
            if (live_busy && (idx < 0 || live_sel < 0 || idx == live_sel)) return true;
            if (idx < 0 || idx >= (int)sites.size()) return true;
            return !child_has_latest(sites[static_cast<size_t>(idx)], hub_site, hub_ver);
        };
        char sub[200];
        snprintf(sub, sizeof(sub), "matrices  %d/%zu    hub v%s    VPN 10.66.80.0/24    GET only",
                 n_up, sites.size(), hub_ver.empty() ? "-" : hub_ver.c_str());
        txt(gold, 280, 18, sub);
        if (now() < toast_until && !toast.empty())
            txt(cyan, 14, 70, toast);
        else
            txt(rgb(dpy, cm, 255, 200, 87), 14, 70,
                "never CLOSE/OPEN from meta   ·   USB has no wallets   ·   hunters keep each box public IP");

        int nx = 14;
        for (int i = 0; i < 5; i++) {
            int tw = static_cast<int>(strlen(PAGE_NAME[i])) * 9 + 22;
            Rect r{nx, 28, tw, 22};
            bool on = page == (Page)i;
            fill(on ? rgb(dpy, cm, 40, 22, 8) : clip, r);
            stroke(on ? orange : border, r);
            txt(on ? gold : green_dim, r.x + 8, r.y + 15, PAGE_NAME[i]);
            hits.push_back({Hit::Nav, i, r});
            nx += tw + 8;
        }
        bool gw_on = probe_gw();
        Rect gw{W - 78 - 8 - 148, 10, 148, 22};
        fill(gw_on ? rgb(dpy, cm, 8, 40, 18) : clip, gw);
        stroke(gw_on ? green : muted, gw);
        txt(gw_on ? green : muted, gw.x + 10, gw.y + 15,
            gw_on ? "iPhone GW  ON" : "iPhone GW  OFF");
        hits.push_back({Hit::IphoneGw, 0, gw});

        auto draw_ladebalken = [&](int x, int y, int w, const char* title, int pct,
                                   const std::string& step, const std::string& tool, bool waiting) {
            if (w < 80) w = 80;
            if (pct < 0) pct = 0;
            if (pct > 100) pct = 100;
            Rect box{x, y, w, 76};
            fill(rgb(dpy, cm, 8, 8, 8), box);
            stroke(gold, box);
            txt(gold, box.x + 12, box.y + 18, title);
            char pcts[16];
            snprintf(pcts, sizeof(pcts), "%3d%%", pct);
            txt(green, box.x + box.w - 56, box.y + 18, pcts);

            const int n = 44;
            int k = n * pct / 100;
            if (k < 0) k = 0;
            if (k > n) k = n;
            char bar[64];
            bar[0] = '[';
            for (int i = 0; i < n; i++) {
                char ch = '.';
                if (i < k) ch = '#';
                else if (i == k && waiting && (static_cast<int>(now() * 2.0) % 2) == 0) ch = '>';
                bar[1 + i] = ch;
            }
            bar[1 + n] = ']';
            bar[2 + n] = 0;
            txt(green, box.x + 12, box.y + 40, bar);

            int tw = n * 9;
            if (tw > box.w - 24) tw = box.w - 24;
            if (tw < 8) tw = 8;
            Rect trough{box.x + 12, box.y + 48, tw, 5};
            fill(rgb(dpy, cm, 20, 20, 20), trough);
            int fw = trough.w * pct / 100;
            if (fw > 0) {
                XSetForeground(dpy, gc, green);
                XFillRectangle(dpy, buf, gc, trough.x, trough.y, fw, trough.h);
            }

            std::string sub = step;
            if (!tool.empty()) sub += "  ·  " + tool;
            if (waiting) sub += "  ·  bitte warten";
            txt(ab_text, box.x + 12, box.y + 68, ascii(sub, (box.w - 24) / 9));
        };

        auto draw_add_children = [&](int& y) {
            txt(gold, 18, y, "ADD MATRIX CHILD");
            y += 16;
            txt(muted, 18, y, "Puts a LAN box under this mothership. It stays down until that box joins 10.66.80.x.");
            y += 20;
            auto known = [&](const std::string& ip) {
                for (auto& s : sites)
                    if (s.host == ip || s.lan == ip) return true;
                return false;
            };
            int shown = 0;
            int hidden = 0;
            for (auto& b : lan_boxes) {
                if (b.ip.empty() || known(b.ip)) continue;
                if (shown >= 6) { hidden++; continue; }
                Rect row{18, y, W - 36, 32};
                fill(ab_panel, row);
                stroke(border, row);
                std::string flags = b.spring ? "Spring open" : (b.ssh ? "SSH open" : "ping only");
                txt(ab_text, row.x + 12, row.y + 21, ascii(b.ip + "    " + flags, 48));
                Rect btn{row.x + row.w - 132, row.y + 4, 120, 24};
                fill(rgb(dpy, cm, 8, 40, 18), btn);
                stroke(green, btn);
                txt(green, btn.x + 18, btn.y + 16, "Add child");
                hits.push_back({Hit::AddChild, static_cast<int>(add_targets.size()), btn});
                add_targets.push_back(b.ip);
                y += 36;
                shown++;
            }
            if (shown == 0) {
                const char* msg = lan_pid ? "scanning the LAN…"
                                  : (lan_scanned ? "no new LAN box on this subnet"
                                                 : "scanning the LAN…");
                txt(muted, 18, y, msg);
                y += 20;
            } else if (hidden > 0) {
                char more[64];
                snprintf(more, sizeof(more), "%d more not shown", hidden);
                txt(muted, 18, y, more);
                y += 18;
            }
            Rect ref{18, y, 150, 28};
            fill(clip, ref);
            stroke(border, ref);
            txt(green_dim, ref.x + 28, ref.y + 18, lan_pid ? "Scanning…" : "Scan LAN");
            hits.push_back({Hit::RefreshLan, 0, ref});
            y += 40;
        };

        Rect rc{W - 78, 10, 64, 22};
        fill(clip, rc);
        stroke(orange, rc);
        txt(orange, rc.x + 12, rc.y + 15, "CLOSE");
        hits.push_back({Hit::Close, 0, rc});

        Rect body{0, NAV_H + 8, W, H - NAV_H - 8};
        if (page == PgHome) {
            txt(orange, 18, body.y + 18, "CONNECTED MATRICES");
            txt(muted, 220, body.y + 18, "mothership plus child matrices  ·  double-click a name to rename");
            {
                Rect logH{W - 36 - 200 - 72, body.y + 4, 64, 26};
                fill(show_push_log ? rgb(dpy, cm, 40, 22, 8) : clip, logH);
                stroke(show_push_log ? orange : border, logH);
                txt(show_push_log ? gold : green_dim, logH.x + 16, logH.y + 18, "LOG");
                hits.push_back({Hit::PushLogOpen, 0, logH});
                int pidx = push_target();
                Rect btnH{W - 36 - 200, body.y + 4, 200, 26};
                if (show_push_for(pidx)) {
                    fill(live_busy ? clip : rgb(dpy, cm, 8, 40, 18), btnH);
                    stroke(live_busy ? border : green, btnH);
                    txt(live_busy ? muted : green, btnH.x + 14, btnH.y + 18,
                        live_busy ? "Pushing…" : "PUSH latest to remote");
                    hits.push_back({Hit::LivePush, pidx < 0 ? 0 : pidx, btnH});
                } else {
                    txt(muted, btnH.x + 14, btnH.y + 18, "already latest");
                }
            }
            int y = body.y + 36;
            if (sites.empty())
                txt(muted, 24, y + 20, "no sites — start Spring on this box");
            std::vector<int> order;
            for (int i = 0; i < (int)sites.size(); i++)
                if (is_mothership(sites[static_cast<size_t>(i)])) order.push_back(i);
            for (int i = 0; i < (int)sites.size(); i++)
                if (!is_mothership(sites[static_cast<size_t>(i)]) && sites[static_cast<size_t>(i)].role == "child")
                    order.push_back(i);
            for (int i = 0; i < (int)sites.size(); i++)
                if (!is_mothership(sites[static_cast<size_t>(i)]) && sites[static_cast<size_t>(i)].role != "child")
                    order.push_back(i);
            for (int i : order) {
                auto& s = sites[static_cast<size_t>(i)];
                bool mothership = is_mothership(s);
                bool child = s.role == "child";
                int xoff = child ? 36 : 0;
                int extra = (mothership && api_edit) ? 26 : 0;
                int acct = s.main_only ? 22 : 0;
                int ssh_h = (!mothership && ssh_id == s.id) ? 156 : 0;
                Rect card{18 + xoff, y, W - 36 - xoff, 164 + extra + acct + ssh_h};
                fill(s.ok ? panel : rgb(dpy, cm, 18, 8, 8), card);
                stroke(s.ok ? green : rose, card);
                if (mothership) rainbow_frame(card);
                XSetForeground(dpy, gc, s.ok ? green : rose);
                XFillArc(dpy, buf, gc, card.x + 14, card.y + 16, 16, 16, 0, 360 * 64);
                txt(s.ok ? green : rose, card.x + 40, card.y + 22, s.ok ? "UP" : "DOWN");
                Rect nameR{card.x + 84, card.y + 4, 188, 24};
                bool editing = !mothership && !rename_id.empty() && rename_id == s.id;
                if (editing) {
                    fill(navy, nameR);
                    stroke(gold, nameR);
                    std::string shown = rename_buf;
                    if ((int)(now() * 2.0) % 2) shown += "_";
                    txt(gold, nameR.x + 6, nameR.y + 17, ascii(shown, 21));
                } else {
                    txt(text, card.x + 90, card.y + 22, ascii(s.name, 20));
                }
                if (!mothership)
                    hits.push_back({Hit::RenameName, static_cast<int>(i), nameR});
                std::string where = s.host + ":" + std::to_string(s.port);
                if (!s.lan.empty()) where += "   LAN " + s.lan;
                txt(muted, card.x + 280, card.y + 22, ascii(where, 42));
                if (mothership)
                    txt(rainbow[(int)(now() * 8.0) % 24], card.x + 480, card.y + 22, "MOTHERSHIP");
                else if (s.main_only)
                    txt(gold, card.x + 480, card.y + 22, "MAIN ACCOUNT");
                else if (child)
                    txt(gold, card.x + 480, card.y + 22, "CHILD");
                else
                    txt(muted, card.x + 480, card.y + 22, s.role.empty() ? "matrix" : s.role);
                bool remote = s.host != "127.0.0.1" && s.host != "localhost";
                if (remote && show_push_for(static_cast<int>(i))) {
                    Rect btn{card.x + card.w - 168, card.y + 10, 152, 26};
                    fill(live_busy ? clip : rgb(dpy, cm, 8, 40, 18), btn);
                    stroke(live_busy ? border : green, btn);
                    txt(live_busy ? muted : green, btn.x + 10, btn.y + 18,
                        live_busy ? "Pushing…" : "PUSH latest");
                    hits.push_back({Hit::LivePush, static_cast<int>(i), btn});
                }
                if (!mothership) {
                    SshCred cred = load_ssh_cred(s.id);
                    Rect sshb{card.x + card.w - 272, card.y + 10, 96, 26};
                    fill(rgb(dpy, cm, 40, 28, 8), sshb);
                    stroke(gold, sshb);
                    txt(gold, sshb.x + (cred.present ? 32 : 16), sshb.y + 18,
                        cred.present ? "SSH" : "add SSH");
                    hits.push_back({Hit::Ssh, static_cast<int>(i), sshb, 0});
                }
                {
                    int right = card.x + card.w - 8;
                    if (s.ok) {
                        bool hl_ok = s.hl_state == "green";
                        bool hl_bad = s.hl_state == "red";
                        unsigned long hl_col = hl_ok ? green : (hl_bad ? rose : muted);
                        const char* hl_word = hl_ok ? "HL KEY ok" : (hl_bad ? "HL KEY no" : "HL KEY …");
                        Rect hlb{card.x + card.w - 168, card.y + 40, 152, 22};
                        fill(hl_ok ? rgb(dpy, cm, 8, 48, 22) : (hl_bad ? rgb(dpy, cm, 48, 12, 16) : clip), hlb);
                        stroke(hl_col, hlb);
                        txt(hl_col, hlb.x + 10, hlb.y + 16, hl_word);
                        right = hlb.x - 8;
                    }
                    txt(gold, card.x + 40, card.y + 56, "API-KEY");
                    int tx = card.x + 112;
                    if (mothership && api_busy) {
                        txt(orange, tx, card.y + 56, "saving…");
                    } else if (mothership && api_edit) {
                        Rect field{tx, card.y + 40, std::min(360, std::max(120, right - tx - 200)), 24};
                        fill(navy, field);
                        stroke(gold, field);
                        std::string stars(std::min(api_buf.size(), static_cast<size_t>(24)), '*');
                        if (api_buf.size() > 24) stars += " " + std::to_string(api_buf.size());
                        if ((int)(now() * 2.0) % 2) stars += "_";
                        txt(gold, field.x + 8, field.y + 16, stars);
                        hits.push_back({Hit::ApiKey, 3, field});
                        Rect save{field.x + field.w + 8, card.y + 40, 80, 24};
                        stroke(green, save);
                        txt(green, save.x + 20, save.y + 16, "Save");
                        hits.push_back({Hit::ApiKey, 1, save});
                        Rect cancel{save.x + 88, card.y + 40, 96, 24};
                        stroke(rose, cancel);
                        txt(rose, cancel.x + 14, cancel.y + 16, "Cancel");
                        hits.push_back({Hit::ApiKey, 2, cancel});
                        txt(muted, card.x + 40, card.y + 80,
                            ascii("paste once · Ctrl+V · Enter saves · Esc cancels · running hunters reload · paused stay · positions stay",
                                  std::max(20, (card.w - 56) / 9)));
                    } else {
                        std::string shown;
                        std::string note;
                        unsigned long ac = cyan;
                        if (mothership) {
                            shown = api_addr.empty() ? "not saved yet" : api_addr;
                            note = api_note;
                            if (api_exch == "primary" || api_exch == "named") ac = green;
                            else if (api_exch == "no") ac = rose;
                        } else if (is_pub_addr(s.hl_addr)) {
                            shown = s.hl_addr;
                            note = s.hl_name;
                            ac = s.hl_state == "green" ? green : (s.hl_state == "red" ? rose : cyan);
                        } else {
                            auto it = ck_cache.find(s.id);
                            if (it != ck_cache.end() && it->second.state == "addr" && is_pub_addr(it->second.addr)) {
                                shown = it->second.addr;
                            } else if (it != ck_cache.end() && it->second.state == "none") {
                                shown = "not saved on this machine";
                                ac = muted;
                            } else if (it != ck_cache.end() && it->second.state == "unread") {
                                shown = "not readable from here";
                                ac = muted;
                            } else {
                                shown = "reading this machine…";
                                ac = muted;
                            }
                        }
                        int maxch = std::max(8, (right - tx) / 9);
                        std::string draw = ascii(shown, maxch);
                        txt(ac, tx, card.y + 56, draw);
                        int after = tx + static_cast<int>(draw.size()) * 9 + 12;
                        if (mothership) {
                            Rect btn{after, card.y + 40, 98, 24};
                            if (btn.x + btn.w < right) {
                                fill(rgb(dpy, cm, 40, 28, 8), btn);
                                stroke(gold, btn);
                                txt(gold, btn.x + 16, btn.y + 16, "Change");
                                hits.push_back({Hit::ApiKey, 0, btn});
                                after = btn.x + btn.w + 12;
                            }
                        }
                        int room = (right - after) / 9;
                        if (!note.empty() && room > 8)
                            txt(muted, after, card.y + 56, ascii(note, room));
                    }
                }
                if (s.ok) {
                    char line[200];
                    snprintf(line, sizeof(line),
                             "eq %s    uPnL %s    pos %d    hunters %d/%d    BIAS %s",
                             money(s.eq).c_str(), money(s.upnl).c_str(), s.n_pos,
                             s.n_live, s.n_bots, s.bias.empty() ? "-" : s.bias.c_str());
                    txt(ab_text, card.x + 40, card.y + 80 + extra, line);
                    char ip[140];
                    snprintf(ip, sizeof(ip), "HL tokens  leftover %.0f / %.0f   Spring GET OK   %s",
                             s.ip_left, s.ip_cap, s.vpn.c_str());
                    txt(gold, card.x + 40, card.y + 102 + extra, ip);
                } else {
                    txt(rose, card.x + 40, card.y + 80 + extra, s.err.empty() ? "Spring not answering" : s.err);
                    std::string why;
                    bool on_vpn = s.host.rfind("10.66.80.", 0) == 0;
                    if (child && !on_vpn)
                        why = "child of mothership · waiting for WireGuard · " + (s.lan.empty() ? s.host : s.lan);
                    else if (!s.lan.empty() && s.vpn_up)
                        why = "LAN " + s.lan + " — VPN up, Spring not answering yet";
                    else if (!s.lan.empty())
                        why = "LAN " + s.lan + " — waiting for WireGuard";
                    else if (s.vpn_up)
                        why = "VPN tunnel is up — Spring on that box not answering yet";
                    else
                        why = "USB seat reserved · tunnel UP after that box: sudo bash install.sh";
                    txt(s.vpn_up || child ? cyan : muted, card.x + 40, card.y + 102 + extra, ascii(why, 78));
                }
                RdView rd = rd_view(s);
                unsigned long rd_col = muted;
                if (rd.state == "online") rd_col = green;
                else if (rd.state == "operating") rd_col = gold;
                else if (rd.state == "down" || rd.state == "absent") rd_col = rose;
                txt(rd_col, card.x + 40, card.y + 124 + extra, ascii(rd_line(rd), (card.w - 56) / 9));
                std::string vf = version_field(s, hub_ver, hub_site);
                bool behind = vf.find("BEHIND") != std::string::npos ||
                              vf.find("PUSH pending") != std::string::npos;
                Rect vbox{card.x + 36, card.y + 136 + extra, card.w - 52, 22};
                fill(behind ? rgb(dpy, cm, 40, 12, 12) : rgb(dpy, cm, 8, 28, 14), vbox);
                stroke(behind ? rose : gold, vbox);
                txt(behind ? rose : gold, vbox.x + 8, vbox.y + 16, ascii(vf, (vbox.w - 16) / 9));
                if (s.main_only) {
                    txt(gold, card.x + 40, card.y + 164 + extra,
                        ascii("fresh account  ·  HL Copy first  ·  one main  ·  no subaccounts  ·  volume before subaccounts",
                              std::max(20, (card.w - 56) / 9)));
                }
                if (!mothership && ssh_id == s.id) {
                    int base = card.y + 164 + extra + acct;
                    txt(gold, card.x + 40, base + 18, "SSH credentials");
                    txt(muted, card.x + 196, base + 18, "deploy key stays  ·  no password");
                    Rect save{card.x + card.w - 200, base + 2, 88, 24};
                    fill(rgb(dpy, cm, 8, 40, 18), save);
                    stroke(green, save);
                    txt(green, save.x + 24, save.y + 16, "Save");
                    hits.push_back({Hit::Ssh, static_cast<int>(i), save, 1});
                    Rect cancel{card.x + card.w - 104, base + 2, 88, 24};
                    stroke(rose, cancel);
                    txt(rose, cancel.x + 12, cancel.y + 16, "Cancel");
                    hits.push_back({Hit::Ssh, static_cast<int>(i), cancel, 2});
                    const char* labs[4] = {"VPN", "LAN", "root", "live"};
                    const std::string* vals[4] = {&ssh_vpn, &ssh_lan, &ssh_root, &ssh_live};
                    for (int f = 0; f < 4; f++) {
                        int fy = base + 30 + f * 26;
                        txt(muted, card.x + 40, fy + 16, labs[f]);
                        Rect field{card.x + 96, fy, card.w - 116, 22};
                        bool on = ssh_field == f;
                        fill(navy, field);
                        stroke(on ? gold : border, field);
                        std::string shown = *vals[f];
                        if (on && ((int)(now() * 2.0) % 2)) shown += "_";
                        txt(on ? gold : text, field.x + 6, fy + 16,
                            ascii(shown, std::max(8, (field.w - 16) / 9)));
                        hits.push_back({Hit::Ssh, static_cast<int>(i), field, 10 + f});
                    }
                    txt(muted, card.x + 40, base + 142,
                        "live empty = folder of the running Spring    Enter saves    Esc cancels    Tab next");
                }
                y += card.h + (mothership ? 22 : 14);
            }
            y += 8;
            draw_add_children(y);
        } else if (page == PgDeploy) {
            txt(orange, 18, body.y + 18, "DEPLOY");
            txt(muted, 110, body.y + 18, "LIVE PUSH   ·   USB kit (existing Mint)   ·   Mint Cinnamon ISO (new box)");
            txt(ab_muted, 18, body.y + 38,
                "Push copies code only. No wallets. Never CLOSE/OPEN. ISO is not a Linux Mint fork.");

            int y = body.y + 56;
            txt(gold, 18, y, "LIVE  —  connected matrices");
            txt(muted, 340, y, "double-click a name to rename");
            y += 18;
            int n_remote = 0;
            for (int i = 0; i < (int)sites.size(); i++) {
                auto& s = sites[static_cast<size_t>(i)];
                if (s.host == "127.0.0.1" || s.host == "localhost") continue;
                n_remote++;
                if (live_sel < 0) live_sel = i;
                int row_h = s.main_only ? 70 : 52;
                Rect row{18, y, W - 36, row_h};
                bool on = i == live_sel;
                fill(on ? rgb(dpy, cm, 40, 22, 8) : ab_panel, row);
                stroke(on ? orange : border, row);
                char head[180];
                snprintf(head, sizeof(head), "%s   %s:%d   %s",
                         s.name.c_str(), s.host.c_str(), s.port,
                         s.ok ? "Spring UP" : (s.vpn_up ? "VPN up, Spring down" : "DOWN"));
                Rect nameR{row.x + 8, row.y + 4, 168, 22};
                bool editing = !rename_id.empty() && rename_id == s.id;
                if (editing) {
                    fill(navy, nameR);
                    stroke(gold, nameR);
                    std::string shown = rename_buf;
                    if ((int)(now() * 2.0) % 2) shown += "_";
                    txt(gold, nameR.x + 6, nameR.y + 16, ascii(shown, 18));
                    char rest[120];
                    snprintf(rest, sizeof(rest), "%s:%d   %s", s.host.c_str(), s.port,
                             s.ok ? "Spring UP" : (s.vpn_up ? "VPN up, Spring down" : "DOWN"));
                    txt(on ? gold : ab_text, row.x + 186, row.y + 20, ascii(rest, 48));
                } else {
                    txt(on ? gold : ab_text, row.x + 14, row.y + 20, ascii(head, 70));
                }
                txt(gold, row.x + 14, row.y + 40, ascii(version_field(s, hub_ver, hub_site), 78));
                if (s.main_only) {
                    txt(gold, row.x + 14, row.y + 58,
                        "HL COPY first  ·  uptrade this main account  ·  subaccounts after volume");
                }
                hits.push_back({Hit::RenameName, i, nameR});
                hits.push_back({Hit::LiveSite, i, row});
                y += row_h + 6;
            }
            if (n_remote == 0) {
                Rect empty{18, y, W - 36, 36};
                fill(clip, empty);
                stroke(border, empty);
                txt(muted, empty.x + 14, empty.y + 22, "no remote matrix in meta.json (usb-stick / remote)");
                y += 42;
            }

            int pidx = push_target();
            Rect btnL{18, y, 260, 34};
            if (show_push_for(pidx)) {
                fill(live_busy ? clip : rgb(dpy, cm, 8, 40, 18), btnL);
                stroke(live_busy ? border : green, btnL);
                txt(live_busy ? muted : green, btnL.x + 16, btnL.y + 22,
                    live_busy ? "Pushing…" : "PUSH latest to remote");
                hits.push_back({Hit::LivePush, pidx < 0 ? 0 : pidx, btnL});
            } else if (pidx >= 0) {
                std::string note = sites[static_cast<size_t>(pidx)].name + " already on "
                                   + child_installed(sites[static_cast<size_t>(pidx)]);
                txt(muted, btnL.x + 8, btnL.y + 22, ascii(note, 36));
            }

            Rect btnLog{290, y, 72, 34};
            fill(show_push_log ? rgb(dpy, cm, 40, 22, 8) : clip, btnLog);
            stroke(show_push_log ? orange : border, btnLog);
            txt(show_push_log ? gold : green_dim, btnLog.x + 22, btnLog.y + 22, "LOG");
            hits.push_back({Hit::PushLogOpen, 0, btnLog});

            Rect btnS{374, y, 220, 34};
            fill(clip, btnS);
            stroke(border, btnS);
            txt(green_dim, btnS.x + 12, btnS.y + 22, "Stage to share only");
            hits.push_back({Hit::LiveSamba, 0, btnS});
            y += 48;
            txt(muted, 18, y, "SSH key: ~/.ssh/id_ed25519_rimbaud_deploy  ·  pubkey on share/rimbaud-deploy.pub");
            y += 28;

            draw_add_children(y);

            txt(gold, 18, y, "NEW MACHINE");
            y += 18;
            Rect modeKit{18, y, 300, 30};
            fill(deploy_mode == 0 ? rgb(dpy, cm, 40, 22, 8) : clip, modeKit);
            stroke(deploy_mode == 0 ? orange : border, modeKit);
            txt(deploy_mode == 0 ? gold : green_dim, modeKit.x + 12, modeKit.y + 20,
                "USB kit  (>= 2G, existing Mint)");
            hits.push_back({Hit::DeployMode, 0, modeKit});
            Rect modeMint{330, y, 360, 30};
            fill(deploy_mode == 1 ? rgb(dpy, cm, 40, 22, 8) : clip, modeMint);
            stroke(deploy_mode == 1 ? orange : border, modeMint);
            txt(deploy_mode == 1 ? gold : green_dim, modeMint.x + 12, modeMint.y + 20,
                "Mint ISO  (>= 8G, new Cinnamon box)");
            hits.push_back({Hit::DeployMode, 1, modeMint});
            y += 40;

            json boot_st = mint_status();
            BootMedia boot = sample_boot_media(boot_st, mint_busy, mint_job);
            boot_cache = boot;
            UsbMedia usbbar = sample_usb_media(usb_log, usb_busy, usb_exit);
            if (boot.on) {
                draw_ladebalken(18, y, W - 36, "LADEBALKEN   STARTMEDIUM WIRD ERSTELLT",
                                boot.pct, boot.step, boot.tool, true);
                y += 88;
            } else if (usb_busy && usbbar.on) {
                draw_ladebalken(18, y, W - 36, "LADEBALKEN   USB INSTALL MEDIA",
                                usbbar.pct, usbbar.step, "make-usb", true);
                y += 88;
            }

            if (deploy_mode == 0) {
                txt(ab_muted, 18, y, "New machine starts HL Copy only. One main account. Volume before subaccounts.");
                y += 18;

                Rect btn{18, y, 280, 36};
                fill(usb_busy ? clip : rgb(dpy, cm, 50, 28, 8), btn);
                stroke(orange, btn);
                txt(orange, btn.x + 18, btn.y + 23,
                    usb_busy ? "Writing USB…" : "Create USB install media");
                hits.push_back({Hit::MakeUsb, 0, btn});

                Rect ref{310, y, 120, 36};
                fill(clip, ref);
                stroke(border, ref);
                txt(green_dim, ref.x + 22, ref.y + 23, "Refresh");
                hits.push_back({Hit::RefreshUsb, 0, ref});

                Rect usbLog{442, y, 72, 36};
                fill((show_push_log && log_kind == 1) ? rgb(dpy, cm, 40, 22, 8) : clip, usbLog);
                stroke((show_push_log && log_kind == 1) ? orange : border, usbLog);
                txt((show_push_log && log_kind == 1) ? gold : green_dim, usbLog.x + 22, usbLog.y + 23, "LOG");
                hits.push_back({Hit::PushLogOpen, 1, usbLog});
                y += 56;

                txt(muted, 18, y, "USB STICKS  (>= 2G, removable)   ·   gold READY   ·   empty readers = no media");
                y += 16;
                if (usbs.empty()) {
                    Rect empty{18, y, W - 36, 48};
                    fill(clip, empty);
                    stroke(border, empty);
                    txt(muted, empty.x + 16, empty.y + 28, "no USB stick seen — plug one in, then Refresh");
                }
                for (int i = 0; i < (int)usbs.size(); i++) {
                    auto& u = usbs[static_cast<size_t>(i)];
                    Rect row{18, y, W - 36, 36};
                    bool on = i == usb_sel;
                    fill(on ? rgb(dpy, cm, 40, 22, 8) : ab_panel, row);
                    stroke(on ? orange : border, row);
                    unsigned long rowc = u.ready ? (on ? gold : ab_text) : rose;
                    txt(rowc, row.x + 14, row.y + 22,
                        ascii(u.path + "   " + u.size_h + "   " + u.model + "   " +
                              (u.mount.empty() ? "unmounted" : u.mount) + "   " +
                              (u.ready ? "READY" : u.note), 88));
                    hits.push_back({Hit::UsbRow, i, row});
                    y += 42;
                }

                y += 12;
                txt(gold, 18, y, "What the stick does on the new machine");
                y += 22;
                txt(text, 18, y, "1  unpacks install_new to /opt/rimbaud and runs install.sh");
                y += 18;
                txt(text, 18, y, "2  WireGuard join to this hub (10.66.80.x)  GET-only Spring proxy");
                y += 18;
                txt(text, 18, y, "3  hunters keep the NEW box public IP  =  new Hyperliquid 1200/60s tokens");
                y += 18;
                txt(gold, 18, y, "4  HL Copy starts alone on the main account. Subaccounts only after volume.");
                y += 18;
                txt(muted, 18, y, "Do not clone JOIN.conf to two boxes. Other hunters stay off until subaccounts exist.");
                y += 36;
                txt(muted, 18, y, "A sudo password dialog appears (pkexec / zenity). Stick gets kit + JOIN.conf, no wallets.");
            } else {
                json mst = mint_status();
                std::string phase = jstr(mst, "phase", "none");
                std::string iso = jstr(mst, "iso");
                std::string msg = jstr(mst, "msg");
                txt(ab_muted, 18, y,
                    "Official Mint 22.3 Cinnamon + rimBaud. First boot starts HL Copy only, on the main account.");
                y += 18;
                {
                    Rect screen{18, y, W - 36, 108};
                    fill(rgb(dpy, cm, 2, 8, 4), screen);
                    rainbow_frame(screen);
                    XSetForeground(dpy, gc, green);
                    XFillArc(dpy, buf, gc, screen.x + 16, screen.y + 14, 12, 12, 0, 360 * 64);
                    txt(text, screen.x + 36, screen.y + 24, "rimBaud");
                    txt(gold, screen.x + 118, screen.y + 24, "boot");
                    txt(text, screen.x + 28, screen.y + 52, "rimBaud");
                    fill(gold, {screen.x + 28, screen.y + 58, 120, 2});
                    txt(muted, screen.x + 28, screen.y + 80,
                        "Start rimBaud live     Install rimBaud (unattended)     Restore data disk");
                    y += screen.h + 18;
                }
                txt(muted, 18, y,
                    "Boot screen. Same rainbow as the mothership card. A stick shows it after the next ISO build.");
                y += 16;
                char stline[220];
                snprintf(stline, sizeof(stline), "ISO  %s  %s  %s",
                         phase.c_str(),
                         jstr(mst, "size_h").c_str(),
                         iso.empty() ? msg.c_str() : iso.c_str());
                txt(gold, 18, y, ascii(stline, 90));
                y += 18;
                IsoStamp fresh = iso_stamp();
                if (!fresh.have) {
                    txt(rose, 18, y, "IMAGE  none yet — not safe to write a stick");
                } else if (fresh.current) {
                    txt(green, 18, y, "IMAGE  UP TO DATE  — matches the installer");
                } else {
                    txt(rose, 18, y, ascii("IMAGE  OUT OF DATE  — " + fresh.newer + " is newer than this ISO", 90));
                }
                y += 22;
                if (!fresh.current) {
                    Rect bRedo{18, y, 520, 36};
                    bool redo_busy = mint_busy || usb_busy || boot.on;
                    fill(redo_busy ? clip : rgb(dpy, cm, 60, 16, 12), bRedo);
                    stroke(redo_busy ? border : rose, bRedo);
                    const char* redo_label = "Rebuild image and format stick";
                    if (mint_then_write) redo_label = "Rebuilding, then formatting the stick…";
                    else if (redo_busy && boot.tool == "dd") redo_label = "Wait — the stick is being formatted";
                    else if (redo_busy) redo_label = "Rebuilding image, then the stick is formatted";
                    txt(redo_busy ? muted : rose, bRedo.x + 14, bRedo.y + 23, redo_label);
                    if (!redo_busy) hits.push_back({Hit::RebuildWrite, 0, bRedo});
                    y += 44;
                }
                if (mint_busy) {
                    std::string tail = mint_tail();
                    txt(cyan, 18, y, tail.empty() ? "building…" : tail);
                    y += 18;
                }

                bool iso_busy = mint_busy || boot.on;
                Rect bBuild{18, y, 280, 36};
                fill(iso_busy ? clip : rgb(dpy, cm, 50, 28, 8), bBuild);
                stroke(orange, bBuild);
                txt(orange, bBuild.x + 16, bBuild.y + 23,
                    boot.on && boot.tool != "dd" ? "Rebuilding image…"
                    : (iso_busy && mint_job != 2 ? "Building ISO…" : "Build hybrid ISO"));
                if (!iso_busy) hits.push_back({Hit::BuildMintIso, 0, bBuild});

                Rect bWrite{310, y, 280, 36};
                bool can_write = phase == "done" || !iso.empty();
                fill((iso_busy || !can_write) ? clip : rgb(dpy, cm, 8, 40, 18), bWrite);
                stroke((iso_busy || !can_write) ? border : green, bWrite);
                const char* write_label = "Write ISO to USB";
                if (boot.tool == "dd") write_label = "Writing the stick…";
                else if (boot.on) write_label = "Rebuilding image…";
                else if (mint_busy && mint_job == 2) write_label = "Writing the stick…";
                txt((iso_busy || !can_write) ? muted : green, bWrite.x + 16, bWrite.y + 23, write_label);
                if (!iso_busy && can_write) hits.push_back({Hit::WriteMintIso, 0, bWrite});

                Rect ref{602, y, 120, 36};
                fill(clip, ref);
                stroke(border, ref);
                txt(green_dim, ref.x + 22, ref.y + 23, "Refresh");
                hits.push_back({Hit::RefreshUsb, 0, ref});
                y += 52;

                txt(muted, 18, y, "USB STICKS  (>= 8G for dd, or a Ventoy mount to copy the ISO)");
                y += 16;
                if (usbs.empty()) {
                    Rect empty{18, y, W - 36, 48};
                    fill(clip, empty);
                    stroke(border, empty);
                    txt(muted, empty.x + 16, empty.y + 28, "no USB stick seen — plug one in, then Refresh");
                }
                for (int i = 0; i < (int)usbs.size(); i++) {
                    auto& u = usbs[static_cast<size_t>(i)];
                    Rect row{18, y, W - 36, 36};
                    bool on = i == usb_sel;
                    bool small = u.size > 0 && u.size < 8e9;
                    bool ok = u.ready && !small;
                    fill(on ? rgb(dpy, cm, 40, 22, 8) : ab_panel, row);
                    stroke(on ? orange : border, row);
                    txt(ok ? (on ? gold : ab_text) : rose, row.x + 14, row.y + 22,
                        ascii(u.path + "   " + u.size_h + "   " + u.model + "   " +
                              (u.mount.empty() ? "unmounted" : u.mount) + "   " +
                              (!u.ready ? u.note : (small ? "< 8G for ISO" : "READY")), 88));
                    hits.push_back({Hit::UsbRow, i, row});
                    y += 42;
                }

                y += 8;
                txt(muted, 18, y,
                    "First boot expires the password, then install.sh. No wallets on the ISO. BIOS text menu stays plain.");
            }
        } else if (page == PgMaster) {
            txt(orange, 18, body.y + 18, "CREATE HL MASTER");
            txt(muted, 200, body.y + 18, "new EVM key  ·  wait for USDC  ·  then subaccounts  ·  not the hub hunters");
            json wst = parse_or("");
            {
                std::ifstream in(conf_dir() + "/hl_wallets/state.json");
                if (in) {
                    try { in >> wst; } catch (...) {}
                }
            }
            std::string phase = jstr(wst, "phase", "idle");
            std::string master = jstr(wst, "master");
            double aval = jnum(wst, "account_value");
            bool funded = aval >= 1.0 || phase == "funded" || phase == "done";

            Rect b1{18, body.y + 48, 240, 34};
            fill(rgb(dpy, cm, 50, 28, 8), b1);
            stroke(orange, b1);
            txt(orange, b1.x + 16, b1.y + 22, "1  Generate MASTER");
            hits.push_back({Hit::GenMaster, 0, b1});

            Rect b2{270, body.y + 48, 280, 34};
            fill(funded ? rgb(dpy, cm, 8, 40, 18) : clip, b2);
            stroke(funded ? green : border, b2);
            txt(funded ? green : muted, b2.x + 12, b2.y + 22, "2  Create subaccounts");
            hits.push_back({Hit::CreateSubs, 0, b2});

            char sum[200];
            snprintf(sum, sizeof(sum), "phase %s    value $%.2f    %s",
                     phase.c_str(), aval,
                     master.empty() ? "no master yet" : master.c_str());
            txt(gold, 18, body.y + 100, ascii(sum, 90));
            txt(muted, 18, body.y + 118,
                "Transfer USDC on Hyperliquid to the master address. Subaccounts stay off until funded.");
            txt(muted, 18, body.y + 134,
                "HL rule: $100k lifetime volume before createSubAccount. Keys are NOT written to this desk's hunters.");

            Rect logp{18, body.y + 156, W - 36, H - (body.y + 176)};
            fill(clip, logp);
            stroke(border, logp);
            txt(orange, logp.x + 10, logp.y + 16, "LOG");
            std::ifstream lf(conf_dir() + "/hl_wallets/creator.log");
            std::vector<std::string> lines;
            std::string ln;
            while (lf && std::getline(lf, ln)) lines.push_back(ln);
            int maxl = std::max(4, (logp.h - 28) / 16);
            int start = std::max(0, (int)lines.size() - maxl);
            int ly = logp.y + 34;
            for (int i = start; i < (int)lines.size(); i++) {
                txt(ab_text, logp.x + 10, ly, ascii(lines[static_cast<size_t>(i)], (logp.w - 20) / 9));
                ly += 16;
            }
        } else if (page == PgSystem) {
            txt(orange, 18, body.y + 18, "SYSTEM");
            txt(muted, 110, body.y + 18, "Hummingbot image  ·  download first  ·  you press Execute");
            int y = body.y + 44;
            txt(gold, 18, y, "HUMMINGBOT VERSION");
            y += 18;
            if (sites.empty()) {
                txt(muted, 18, y, "no matrix in meta.json");
                y += 20;
            }
            for (auto& s : sites) {
                HbVer v;
                auto it = hb_vers.find(s.id);
                if (it != hb_vers.end()) v = it->second;
                std::string run = v.running.empty() ? "?" : v.running;
                std::string gh = v.github.empty() ? "?" : v.github;
                std::string st = v.status.empty() ? (v.err.empty() ? "…" : v.err) : v.status;
                unsigned long col = muted;
                if (st == "up to date") col = green;
                else if (st == "behind") col = rose;
                else if (st == "down") col = rose;
                Rect row{18, y, W - 36, 28};
                fill(ab_panel, row);
                stroke(col == muted ? border : col, row);
                std::string line = s.name + "    running " + run + "    github " + gh + "    " + st;
                txt(col, row.x + 10, row.y + 18, ascii(line, (row.w - 20) / 9));
                y += 32;
            }
            txt(muted, 18, y, "Same compare as the matrix System page. GitHub tag vs the hunter image VERSION.");
            y += 16;
            txt(muted, 18, y, "Custom scripts and overlay mounts stay. Positions stay on Hyperliquid.");
            y += 28;

            std::string pick = "this matrix";
            for (auto& s : sites) if (s.id == hb_sel) pick = s.name;
            if (hb_all) pick = "all matrices";
            txt(gold, 18, y, "DEPLOY UPGRADE TO");
            y += 18;
            Rect drop{18, y, 280, 28};
            fill(navy, drop);
            stroke(hb_menu ? gold : border, drop);
            txt(text, drop.x + 10, drop.y + 18, ascii(pick + (hb_menu ? "  ^" : "  v"), 28));
            hits.push_back({Hit::HbDrop, 0, drop});
            Rect box{312, y + 4, 18, 18};
            fill(hb_all ? rgb(dpy, cm, 8, 40, 18) : clip, box);
            stroke(hb_all ? green : border, box);
            if (hb_all) txt(green, box.x + 4, box.y + 14, "x");
            txt(hb_all ? green : text, 338, y + 18, "all matrices");
            hits.push_back({Hit::HbAll, 0, {312, y, 220, 28}});
            y += 32;
            if (hb_menu && !hb_all) {
                for (int i = 0; i < (int)sites.size(); i++) {
                    auto& s = sites[static_cast<size_t>(i)];
                    Rect item{18, y, 280, 24};
                    bool on = s.id == hb_sel;
                    fill(on ? rgb(dpy, cm, 40, 22, 8) : clip, item);
                    stroke(on ? gold : border, item);
                    txt(on ? gold : text, item.x + 10, item.y + 16, ascii(s.name, 28));
                    hits.push_back({Hit::HbPick, i, item});
                    y += 26;
                }
            }

            json hj = hb_job_read();
            std::string phase = jstr(hj, "phase", "idle");
            int pct = static_cast<int>(jnum(hj, "pct", 0));
            bool hb_run = hb_pid != 0 || phase == "downloading" || phase == "executing";
            bool can_exec = phase == "ready" && !hb_run;
            Rect bDl{18, y, 280, 34};
            fill(hb_run ? clip : rgb(dpy, cm, 50, 28, 8), bDl);
            stroke(hb_run ? border : orange, bDl);
            txt(hb_run ? muted : orange, bDl.x + 14, bDl.y + 22,
                hb_run ? "Working…" : "Download newest image");
            hits.push_back({Hit::HbDownload, 0, bDl});
            Rect bEx{310, y, 240, 34};
            fill(can_exec ? rgb(dpy, cm, 60, 16, 12) : clip, bEx);
            stroke(can_exec ? rose : border, bEx);
            txt(can_exec ? rose : muted, bEx.x + 14, bEx.y + 22, "Execute upgrade");
            hits.push_back({Hit::HbExecute, 0, bEx});
            y += 42;
            txt(muted, 18, y, "Download pulls the image and stops. Execute recreates hunters only after that.");
            y += 22;
            if (phase == "downloading" || phase == "executing" || phase == "ready" || phase == "error" || phase == "done") {
                draw_ladebalken(18, y, W - 36,
                                phase == "executing" ? "LADEBALKEN   HUMMINGBOT EXECUTE"
                                                     : "LADEBALKEN   HUMMINGBOT DOWNLOAD",
                                pct, jstr(hj, "step", phase), "docker pull",
                                phase == "downloading" || phase == "executing");
                y += 88;
            }
            Rect logb{18, y, W - 36, std::max(80, H - y - 16)};
            fill(clip, logb);
            stroke(phase == "error" ? rose : (phase == "ready" ? gold : border), logb);
            txt(orange, logb.x + 10, logb.y + 16, "LOG");
            std::vector<std::string> lines;
            if (hj.contains("log") && hj["log"].is_array()) {
                for (auto& ln : hj["log"]) if (ln.is_string()) lines.push_back(ln.get<std::string>());
            }
            int maxl = std::max(3, (logb.h - 28) / 16);
            int start = std::max(0, (int)lines.size() - maxl);
            int ly = logb.y + 34;
            for (int i = start; i < (int)lines.size(); i++) {
                txt(ab_text, logb.x + 10, ly, ascii(lines[static_cast<size_t>(i)], (logb.w - 20) / 9));
                ly += 16;
            }
            if (lines.empty())
                txt(muted, logb.x + 10, logb.y + 36, "no upgrade yet — Download does not touch running hunters");
        } else {
            txt(orange, 18, body.y + 18, "BACKUP");
            txt(muted, 110, body.y + 18, "Copy to USB  —  reinstall this setup on fresh Linux Mint");
            int y = body.y + 40;
            json st = bk_status_read();
            json src = st.contains("source") && st["source"].is_object() ? st["source"] : json::object();
            if (!bk_picked && !bk_edit) {
                std::string sug = jstr(st, "suggested");
                if (!sug.empty()) { bk_dest = sug; bk_picked = true; }
            }
            std::string line1 = "THIS SETUP   /dev/nvme0n1p2   hummingbot project   about 2 GB";
            std::string line2 = jstr(src, "tree", repo_root()) + "   hunters keep running   dist and doku stay behind";
            Rect card{18, y, W - 36, 44};
            fill(ab_panel, card);
            stroke(green, card);
            txt(text, card.x + 10, card.y + 18, ascii(line1, (card.w - 20) / 9));
            txt(muted, card.x + 10, card.y + 36, ascii(line2, (card.w - 20) / 9));
            y += 52;
            txt(gold, 18, y, "COPY TO USB");
            y += 16;
            txt(muted, 18, y, "Click the folder to edit it. Enter saves. Esc cancels. The stick is not erased.");
            y += 18;
            Rect pathb{18, y, W - 36, 28};
            fill(bk_edit ? rgb(dpy, cm, 40, 22, 8) : navy, pathb);
            stroke(bk_edit ? gold : border, pathb);
            std::string shown_path = bk_edit ? bk_buf : (bk_dest.empty() ? "/media/usb/USB-STICK" : bk_dest);
            if (bk_edit) shown_path += "_";
            txt(bk_edit ? gold : text, pathb.x + 10, pathb.y + 19, ascii(shown_path, (pathb.w - 20) / 9));
            hits.push_back({Hit::BkEdit, 0, pathb});
            y += 34;
            bk_rows.clear();
            int shown = 0;
            if (st.contains("destinations") && st["destinations"].is_array()) {
                for (auto& d : st["destinations"]) {
                    if (!d.is_object() || shown >= 4) break;
                    bool ok = jflag(d, "ok");
                    std::string path = jstr(d, "path");
                    if (path.empty()) continue;
                    int idx = static_cast<int>(bk_rows.size());
                    bk_rows.push_back(path);
                    Rect row{18, y, W - 36, 24};
                    bool on = path == bk_dest;
                    fill(on ? rgb(dpy, cm, 40, 22, 8) : clip, row);
                    stroke(on ? gold : (ok ? border : muted), row);
                    std::string note = jstr(d, "note");
                    std::string textline = path + "   " + jstr(d, "free_h") + " free";
                    if (!jstr(d, "model").empty()) textline += "   " + jstr(d, "model");
                    if (!note.empty()) textline += "   " + note;
                    txt(ok ? (on ? gold : text) : muted, row.x + 10, row.y + 16,
                        ascii(textline, (row.w - 20) / 9));
                    if (ok) hits.push_back({Hit::BkDest, idx, row});
                    shown++;
                    y += 26;
                }
            }
            bool busy = bk_pid != 0;
            bool can = !bk_dest.empty() && !busy;
            Rect bGo{18, y, 220, 34};
            fill(can ? rgb(dpy, cm, 50, 28, 8) : clip, bGo);
            stroke(can ? orange : border, bGo);
            txt(can ? orange : muted, bGo.x + 14, bGo.y + 22, busy ? "Working..." : "Copy to USB");
            hits.push_back({Hit::BkCreate, 0, bGo});
            if (busy) {
                Rect bStop{250, y, 120, 34};
                fill(rgb(dpy, cm, 60, 16, 12), bStop);
                stroke(rose, bStop);
                txt(rose, bStop.x + 14, bStop.y + 22, "Stop");
                hits.push_back({Hit::BkCancel, 0, bStop});
            }
            Rect bRef{busy ? 382 : 250, y, 130, 34};
            fill(clip, bRef);
            stroke(border, bRef);
            txt(text, bRef.x + 14, bRef.y + 22, "Refresh");
            hits.push_back({Hit::BkRefresh, 0, bRef});
            y += 42;
            txt(muted, 18, y, "Copy to USB writes install.sh and one rimbaud-DATE.tar.gz of this hummingbot setup.");
            y += 16;
            txt(muted, 18, y, "On a fresh Linux Mint, open the stick and run:  bash install.sh");
            y += 16;
            txt(muted, 18, y, "That reinstalls this setup, wallets included. Hunters on this PC keep running.");
            y += 16;
            txt(muted, 18, y, "FAT32 sticks keep the archive under 4 GB. Power the old PC off before the new one trades.");
            y += 18;
            json hj = bk_job_read();
            std::string phase = jstr(hj, "phase", "idle");
            int pct = static_cast<int>(jnum(hj, "pct", 0));
            if (phase == "preparing" || phase == "copying" || phase == "imaging" || phase == "done" || phase == "error") {
                draw_ladebalken(18, y, W - 36, "LADEBALKEN   COPY TO USB",
                                pct, jstr(hj, "step", phase), "tar.gz + install.sh", busy);
                y += 84;
            }
            if (st.contains("images") && st["images"].is_array() && !st["images"].empty()) {
                auto& im = st["images"][0];
                if (im.is_object()) {
                    txt(gold, 18, y, ascii("latest  " + jstr(im, "name") + "   " + jstr(im, "size_h")
                                           + "   " + jstr(im, "mtime"), 120));
                    y += 18;
                }
            }
            Rect logb{18, y, W - 36, std::max(64, H - y - 16)};
            fill(clip, logb);
            stroke(phase == "error" ? rose : (phase == "done" ? green : border), logb);
            txt(orange, logb.x + 10, logb.y + 16, "LOG");
            std::vector<std::string> lines;
            if (hj.contains("log") && hj["log"].is_array()) {
                for (auto& ln : hj["log"]) if (ln.is_string()) lines.push_back(ln.get<std::string>());
            }
            int maxl = std::max(2, (logb.h - 28) / 16);
            int start = std::max(0, (int)lines.size() - maxl);
            int ly = logb.y + 34;
            for (int i = start; i < (int)lines.size(); i++) {
                txt(ab_text, logb.x + 10, ly, ascii(lines[static_cast<size_t>(i)], (logb.w - 20) / 9));
                ly += 16;
            }
            if (lines.empty())
                txt(muted, logb.x + 10, logb.y + 36, "no copy yet — Copy to USB does not run until you press it");
        }

        if (show_push_log) {
            std::vector<Hit> keep;
            for (auto& h : hits)
                if (h.kind == Hit::Nav || h.kind == Hit::Close || h.kind == Hit::IphoneGw)
                    keep.push_back(h);
            hits.swap(keep);

            Rect shade{0, NAV_H, W, H - NAV_H};
            fill(rgb(dpy, cm, 2, 8, 4), shade);
            Rect logp{28, NAV_H + 16, W - 56, H - NAV_H - 32};
            fill(clip, logp);
            bool jbusy = log_kind == 1 ? usb_busy : (log_kind == 2 ? mint_busy : live_busy);
            int jexit = log_kind == 1 ? usb_exit : (log_kind == 2 ? mint_exit : live_exit);
            stroke(jbusy ? gold : (jexit == 0 ? green : (jexit > 0 ? rose : orange)), logp);

            const char* stword = jbusy ? "RUNNING"
                                 : (jexit == 0 ? "OK"
                                    : (log_kind == 0 && jexit == 2 ? "NO SSH — staged share"
                                       : (jexit > 0 ? "FAILED" : "LOG")));
            unsigned long stcol = jbusy ? gold
                                  : (jexit == 0 ? green
                                     : (log_kind == 0 && jexit == 2 ? orange
                                        : (jexit > 0 ? rose : gold)));
            const char* kindn = log_kind == 1 ? "USB KIT LOG"
                                : (log_kind == 2 ? "MINT ISO LOG" : "LIVE PUSH LOG");
            std::string who = job_label.empty()
                                  ? (log_kind == 1 ? "stick" : (log_kind == 2 ? "iso" : (live_site_label.empty() ? "remote" : live_site_label)))
                                  : job_label;
            std::string title = std::string(kindn) + "   " + who + "   ·   " + stword;
            txt(stcol, logp.x + 14, logp.y + 20, ascii(title, (logp.w - 250) / 9));
            if (log_close_at > 0 && !jbusy) {
                int left = (int)(log_close_at - now() + 0.99);
                if (left < 0) left = 0;
                char cd[80];
                snprintf(cd, sizeof(cd), "ok — log closes in %ds", left);
                txt(gold, logp.x + 14, logp.y + 38, cd);
            } else {
                txt(muted, logp.x + 14, logp.y + 38,
                    "CMD / OK / FAIL    mark with mouse    Strg-C copy    double-click line");
            }

            int bar_h = 0;
            if (log_kind == 1 || log_kind == 2) {
                json boot_st = mint_status();
                BootMedia boot = sample_boot_media(boot_st, mint_busy, mint_job);
                boot_cache = boot;
                UsbMedia usbbar = sample_usb_media(usb_log, usb_busy, usb_exit);
                bool usb_show = log_kind == 1 && usbbar.on && (usb_busy || usb_exit >= 0);
                if (log_kind == 2 && boot.on) {
                    draw_ladebalken(logp.x + 12, logp.y + 46, logp.w - 24,
                                    "LADEBALKEN   STARTMEDIUM WIRD ERSTELLT",
                                    boot.pct, boot.step, boot.tool, true);
                    bar_h = 88;
                } else if (usb_show) {
                    draw_ladebalken(logp.x + 12, logp.y + 46, logp.w - 24,
                                    "LADEBALKEN   USB INSTALL MEDIA",
                                    usbbar.pct, usbbar.step, "make-usb",
                                    usb_busy && usbbar.pct < 100);
                    bar_h = 88;
                }
            }

            Rect copyL{logp.x + logp.w - 226, logp.y + 8, 96, 24};
            fill(rgb(dpy, cm, 8, 40, 18), copyL);
            stroke(green, copyL);
            txt(green, copyL.x + 22, copyL.y + 16, "COPY");
            hits.push_back({Hit::LogCopy, 0, copyL});

            Rect closeL{logp.x + logp.w - 118, logp.y + 8, 104, 24};
            fill(rgb(dpy, cm, 40, 22, 8), closeL);
            stroke(orange, closeL);
            txt(orange, closeL.x + 10, closeL.y + 16, "CLOSE LOG");
            hits.push_back({Hit::PushLogClose, 0, closeL});

            reload_log_lines();
            int body_y = logp.y + 48 + bar_h;
            int body_h = logp.h - 58 - bar_h;
            if (body_h < 48) body_h = 48;
            Rect bodyL{logp.x + 8, body_y, logp.w - 16, body_h};
            fill(rgb(dpy, cm, 8, 8, 8), bodyL);
            stroke(border, bodyL);
            log_body = bodyL;
            log_cw = (fs && fs->max_bounds.width > 0) ? fs->max_bounds.width : 9;
            log_lh = 16;
            log_tx = bodyL.x + 8;
            log_ty = bodyL.y + 6;
            int maxl = std::max(6, (bodyL.h - 10) / log_lh);
            log_view_start = std::max(0, (int)log_lines.size() - maxl);
            int ly = bodyL.y + 18;
            int maxc = std::max(20, (bodyL.w - 16) / log_cw);
            if (log_lines.empty())
                txt(muted, bodyL.x + 10, ly,
                    jbusy ? "waiting for first command…"
                          : (log_kind == 1 ? "no log yet — click Create USB install media"
                             : (log_kind == 2 ? "no log yet — click Build hybrid ISO"
                                : "no log yet — click PUSH latest")));
            LogPos sa = sel_a, sb = sel_b;
            if (has_sel && logpos_lt(sb, sa)) { auto t = sa; sa = sb; sb = t; }
            for (int i = log_view_start; i < (int)log_lines.size(); i++) {
                const std::string& raw = log_lines[static_cast<size_t>(i)];
                int shown = std::min((int)raw.size(), maxc);
                if (has_sel && i >= sa.line && i <= sb.line) {
                    int lo = 0, hi = shown;
                    if (i == sa.line) lo = std::max(0, std::min(sa.col, shown));
                    if (i == sb.line) hi = std::max(0, std::min(sb.col, shown));
                    if (hi > lo) {
                        fill(sel_bg, {log_tx + lo * log_cw, log_ty + (i - log_view_start) * log_lh,
                                      (hi - lo) * log_cw, log_lh});
                    }
                }
                unsigned long col = ab_text;
                if (raw.rfind("CMD", 0) == 0) col = cyan;
                else if (raw.rfind("OK", 0) == 0) col = green;
                else if (raw.rfind("FAIL", 0) == 0 || raw.rfind("xx", 0) == 0) col = rose;
                else if (raw.rfind("!!", 0) == 0 || raw.rfind("==>", 0) == 0) col = gold;
                else if (raw.find("[ ]") != std::string::npos) col = muted;
                if (has_sel && i >= sa.line && i <= sb.line && sa.line == sb.line)
                    col = sel_fg;
                txt(col, log_tx, ly, ascii(raw, maxc));
                ly += log_lh;
            }
        }

        XCopyArea(dpy, buf, win, gc, 0, 0, W, H, 0, 0);
        XFlush(dpy);
    };

    auto enroll_listening = []() {
        int fd = socket(AF_INET, SOCK_DGRAM, 0);
        if (fd < 0) return false;
        sockaddr_in a{};
        a.sin_family = AF_INET;
        a.sin_port = htons(51821);
        a.sin_addr.s_addr = htonl(INADDR_ANY);
        int rc = bind(fd, reinterpret_cast<sockaddr*>(&a), sizeof(a));
        int err = errno;
        close(fd);
        return rc != 0 && err == EADDRINUSE;
    };
    if (!enroll_listening()) {
        std::string script = repo_root() + "/rimbaud_vpn/rimbaud-vpn";
        pid_t p = fork();
        if (p == 0) {
            execlp("sudo", "sudo", "-n", script.c_str(), "listen-install", static_cast<char*>(nullptr));
            _exit(127);
        }
    }
    bool enroll_note = false;
    lan_pid = spawn_lan_scan();
    lan_started = now();
    if (sample_boot_media(mint_status(), false, 0).on) {
        page = PgDeploy;
        deploy_mode = 1;
    }

    auto commit_rename = [&]() {
        if (rename_id.empty()) return;
        std::string id = rename_id;
        std::string buf = rename_buf;
        rename_id.clear();
        rename_buf.clear();
        std::string saved, err;
        if (rename_matrix(id, buf, saved, err)) {
            sites = load_sites();
            for (auto& s : sites) poll_site(s);
            apply_deploys(sites);
            say("renamed to " + saved);
        } else {
            say(err.empty() ? "could not rename" : err);
        }
    };
    auto cancel_rename = [&]() {
        if (rename_id.empty()) return;
        rename_id.clear();
        rename_buf.clear();
        say("rename cancelled");
    };

    auto cancel_api = [&]() {
        wipe_secret(api_buf);
        api_edit = false;
        api_paste_wait = false;
    };
    auto cancel_ssh = [&]() {
        ssh_id.clear();
        ssh_vpn.clear();
        ssh_lan.clear();
        ssh_root.clear();
        ssh_live.clear();
        ssh_field = 0;
        ssh_paste_wait = false;
    };
    auto commit_ssh = [&]() {
        if (ssh_id.empty()) return;
        std::string id = ssh_id;
        std::string name;
        for (auto& s : sites)
            if (s.id == id) name = s.name;
        std::string err;
        if (save_ssh_cred(id, name, ssh_vpn, ssh_lan, ssh_root, ssh_live, err)) {
            cancel_ssh();
            say("SSH credentials saved");
        } else {
            say(err.empty() ? "could not save SSH credentials" : err);
        }
    };
    auto submit_api = [&]() {
        if (api_busy) return;
        std::string secret = norm_api_secret(api_buf);
        wipe_secret(api_buf);
        if (secret.empty()) {
            api_edit = true;
            say("paste the API private key once (64 hex characters)");
            return;
        }
        int in[2], out[2];
        if (pipe(in) != 0 || pipe(out) != 0) {
            wipe_secret(secret);
            say("could not start the key save");
            return;
        }
        pid_t pid = fork();
        if (pid < 0) {
            close(in[0]); close(in[1]); close(out[0]); close(out[1]);
            wipe_secret(secret);
            say("could not start the key save");
            return;
        }
        if (pid == 0) {
            dup2(in[0], STDIN_FILENO);
            dup2(out[1], STDOUT_FILENO);
            int dn = open("/dev/null", O_WRONLY);
            if (dn >= 0) dup2(dn, STDERR_FILENO);
            close(in[0]); close(in[1]); close(out[0]); close(out[1]);
            if (dn > 2) close(dn);
            std::string script = repo_root() + "/scripts/hl_api_key.py";
            execl("/usr/bin/python3", "python3", script.c_str(), "set", static_cast<char*>(nullptr));
            _exit(127);
        }
        close(in[0]);
        close(out[1]);
        secret.push_back('\n');
        size_t off = 0;
        while (off < secret.size()) {
            ssize_t n = ::write(in[1], secret.data() + off, secret.size() - off);
            if (n < 0) {
                if (errno == EINTR) continue;
                break;
            }
            off += static_cast<size_t>(n);
        }
        close(in[1]);
        wipe_secret(secret);
        int flags = fcntl(out[0], F_GETFL, 0);
        if (flags >= 0) fcntl(out[0], F_SETFL, flags | O_NONBLOCK);
        if (api_fd >= 0) close(api_fd);
        api_fd = out[0];
        api_out.clear();
        api_busy = true;
        api_edit = false;
        api_paste_wait = false;
        say("saving the API key into SQLite — running hunters reload, paused stay paused, positions stay");
    };
    auto poll_api = [&]() {
        if (!api_busy || api_fd < 0) return;
        char tmp[2048];
        for (;;) {
            ssize_t n = ::read(api_fd, tmp, sizeof(tmp));
            if (n > 0) {
                api_out.append(tmp, tmp + n);
                if (api_out.size() > 8000) api_out.erase(0, api_out.size() - 4000);
                continue;
            }
            if (n < 0 && errno == EINTR) continue;
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return;
            close(api_fd);
            api_fd = -1;
            api_busy = false;
            std::string msg = api_result_message(api_out);
            wipe_secret(api_out);
            load_api_view(api_addr, api_exch, api_note);
            if (msg.find("saved") != std::string::npos) api_note = msg;
            say(msg);
            redraw();
            return;
        }
    };
    auto accept_api_chars = [&](const std::string& chunk) {
        if (!api_edit) return;
        for (unsigned char c : chunk) {
            if (api_buf.size() >= 80) break;
            if (api_hex_char(c)) api_buf.push_back(static_cast<char>(c));
        }
    };
    auto accept_ssh_chars = [&](const std::string& chunk) {
        if (ssh_id.empty()) return;
        std::string* buf = &ssh_vpn;
        if (ssh_field == 1) buf = &ssh_lan;
        else if (ssh_field == 2) buf = &ssh_root;
        else if (ssh_field == 3) buf = &ssh_live;
        bool path = ssh_field >= 2;
        int cap = path ? 180 : 80;
        for (unsigned char c : chunk) {
            if (static_cast<int>(buf->size()) >= cap) break;
            if (c == '\n' || c == '\r') break;
            bool ok = std::isalnum(c) || c == '.' || c == '_' || c == '-' || (path ? c == '/' : c == '@');
            if (ok) buf->push_back(static_cast<char>(c));
        }
    };

    double last = 0;
    double last_anim = 0;
    last = now();
    last_anim = last;
    redraw();
    int mx = 0, my = 0;
    while (g_run) {
        while (XPending(dpy)) {
            XEvent e;
            XNextEvent(dpy, &e);
            if (e.type == ClientMessage) g_run = 0;
            else if (e.type == SelectionRequest) handle_sel_req(e.xselectionrequest);
            else if (e.type == SelectionNotify) {
                bool for_ssh = ssh_paste_wait;
                ssh_paste_wait = false;
                api_paste_wait = false;
                if (e.xselection.property == None) {
                    if (for_ssh) say("clipboard empty");
                    else if (api_edit) say("clipboard empty — copy the API key, then Ctrl+V");
                } else {
                    Atom type = None;
                    int fmt = 0;
                    unsigned long n = 0, rem = 0;
                    unsigned char* data = nullptr;
                    if (XGetWindowProperty(dpy, win, e.xselection.property, 0, 256, True,
                                           AnyPropertyType, &type, &fmt, &n, &rem, &data) == Success && data) {
                        std::string chunk(reinterpret_cast<char*>(data), static_cast<size_t>(n));
                        XFree(data);
                        if (for_ssh) accept_ssh_chars(chunk);
                        else accept_api_chars(chunk);
                        wipe_secret(chunk);
                    }
                }
                redraw();
            }
            else if (e.type == Expose) redraw();
            else if (e.type == ConfigureNotify && e.xconfigure.width > 400) {
                W = e.xconfigure.width;
                H = e.xconfigure.height;
                XFreePixmap(dpy, buf);
                buf = XCreatePixmap(dpy, win, W, H, DefaultDepth(dpy, scr));
                redraw();
            } else if (e.type == MotionNotify) {
                mx = e.xmotion.x;
                my = e.xmotion.y;
                if (selecting && show_push_log) {
                    sel_b = pos_at(mx, my);
                    has_sel = true;
                    redraw();
                }
                bool hand = false;
                bool ibeam = false;
                for (auto& h : hits) {
                    if (!inside(h.r, mx, my) || h.kind == Hit::Idle) continue;
                    if (h.kind == Hit::RenameName || h.kind == Hit::BkEdit || (h.kind == Hit::ApiKey && h.i == 3)
                        || (h.kind == Hit::Ssh && h.j >= 10)) ibeam = true;
                    else hand = true;
                }
                if ((show_push_log && inside(log_body, mx, my)) || ibeam)
                    XDefineCursor(dpy, win, cur_xterm);
                else
                    XDefineCursor(dpy, win, hand ? cur_hand : cur_arrow);
            } else if (e.type == KeyPress) {
                char kbuf[8] = {};
                KeySym ks = NoSymbol;
                int nks = XLookupString(&e.xkey, kbuf, (int)sizeof(kbuf) - 1, &ks, nullptr);
                unsigned st = e.xkey.state;
                bool ctrl = (st & ControlMask) != 0;
                if (!ssh_id.empty()) {
                    std::string* buf = &ssh_vpn;
                    if (ssh_field == 1) buf = &ssh_lan;
                    else if (ssh_field == 2) buf = &ssh_root;
                    else if (ssh_field == 3) buf = &ssh_live;
                    bool path = ssh_field >= 2;
                    int cap = path ? 180 : 80;
                    if (ks == XK_Escape) {
                        cancel_ssh();
                        say("SSH edit cancelled");
                    } else if (ks == XK_Return || ks == XK_KP_Enter) {
                        commit_ssh();
                    } else if (ks == XK_Tab || ks == XK_ISO_Left_Tab) {
                        if (st & ShiftMask) ssh_field = (ssh_field + 3) % 4;
                        else ssh_field = (ssh_field + 1) % 4;
                    } else if (ks == XK_BackSpace || ks == XK_Delete) {
                        if (!buf->empty()) buf->pop_back();
                    } else if ((ctrl && (ks == XK_v || ks == XK_V)) || (nks == 1 && kbuf[0] == 22)) {
                        ssh_paste_wait = true;
                        XConvertSelection(dpy, atom_clipboard, atom_utf8, atom_paste, win, CurrentTime);
                    } else if ((st & ShiftMask) && ks == XK_Insert) {
                        ssh_paste_wait = true;
                        XConvertSelection(dpy, XA_PRIMARY, atom_utf8, atom_paste, win, CurrentTime);
                    } else if (!ctrl && nks == 1) {
                        unsigned char c = static_cast<unsigned char>(kbuf[0]);
                        bool ok = std::isalnum(c) || c == '.' || c == '_' || c == '-' || (path ? c == '/' : c == '@');
                        if (ok && static_cast<int>(buf->size()) < cap) buf->push_back(static_cast<char>(c));
                    }
                    redraw();
                } else if (api_edit) {
                    if (ks == XK_Escape) {
                        cancel_api();
                        say("API key change cancelled");
                    } else if (ks == XK_Return || ks == XK_KP_Enter) {
                        submit_api();
                    } else if (ks == XK_BackSpace || ks == XK_Delete) {
                        if (!api_buf.empty()) {
                            api_buf.back() = '\0';
                            api_buf.pop_back();
                        }
                    } else if ((ctrl && (ks == XK_v || ks == XK_V)) || (nks == 1 && kbuf[0] == 22)) {
                        api_paste_wait = true;
                        XConvertSelection(dpy, atom_clipboard, atom_utf8, atom_paste, win, CurrentTime);
                    } else if ((st & ShiftMask) && ks == XK_Insert) {
                        api_paste_wait = true;
                        XConvertSelection(dpy, XA_PRIMARY, atom_utf8, atom_paste, win, CurrentTime);
                    } else if (!ctrl && nks == 1 && api_hex_char(static_cast<unsigned char>(kbuf[0])) && api_buf.size() < 80) {
                        api_buf.push_back(kbuf[0]);
                    }
                    redraw();
                } else if (bk_edit) {
                    if (ks == XK_Escape) { bk_edit = false; bk_buf.clear(); say("path edit cancelled"); }
                    else if (ks == XK_Return || ks == XK_KP_Enter) {
                        std::string p = bk_buf;
                        while (!p.empty() && p.back() == ' ') p.pop_back();
                        bk_edit = false;
                        struct stat ds{};
                        if (p.empty() || p[0] != '/' || stat(p.c_str(), &ds) != 0 || !S_ISDIR(ds.st_mode))
                            say("that folder is not there");
                        else {
                            bk_dest = p;
                            bk_picked = true;
                            save_bk_dest(p);
                            say("backup folder saved");
                        }
                    } else if (ks == XK_BackSpace) {
                        if (!bk_buf.empty()) bk_buf.pop_back();
                    } else if (!ctrl && nks == 1) {
                        unsigned char c = static_cast<unsigned char>(kbuf[0]);
                        bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                                  (c >= '0' && c <= '9') || c == '/' || c == '.' || c == '_' || c == '-';
                        if (ok && bk_buf.size() < 180) bk_buf.push_back(static_cast<char>(c));
                    }
                    redraw();
                } else if (!rename_id.empty()) {
                    if (ks == XK_Escape) cancel_rename();
                    else if (ks == XK_Return || ks == XK_KP_Enter) commit_rename();
                    else if (ks == XK_BackSpace) {
                        if (!rename_buf.empty()) rename_buf.pop_back();
                    } else if (!ctrl && nks == 1) {
                        unsigned char c = static_cast<unsigned char>(kbuf[0]);
                        bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                                  (c >= '0' && c <= '9') || c == ' ' || c == '.' || c == '_' || c == '-';
                        if (ok && rename_buf.size() < 20) rename_buf.push_back(static_cast<char>(c));
                    }
                    redraw();
                } else if (show_push_log &&
                    ((ctrl && (ks == XK_c || ks == XK_C || ks == XK_Insert)) ||
                     (nks == 1 && kbuf[0] == 3))) {
                    log_close_at = 0;
                    copy_log();
                    redraw();
                } else if (show_push_log && ctrl && (ks == XK_a || ks == XK_A)) {
                    reload_log_lines();
                    if (!log_lines.empty()) {
                        sel_a = {0, 0};
                        sel_b = {(int)log_lines.size() - 1,
                                 (int)log_lines.back().size()};
                        has_sel = true;
                        say("log selected — Strg-C to copy");
                    }
                    redraw();
                } else if (ks == XK_Escape && show_push_log) { show_push_log = false; selecting = false; redraw(); }
                else if (ks == XK_q || ks == XK_Escape) g_run = 0;
                else if (ks == XK_1) { page = PgHome; redraw(); }
                else if (ks == XK_2) { page = PgDeploy; usbs = list_usb(); usb_sel = pick_usb_sel(usbs, usb_sel); redraw(); }
                else if (ks == XK_3) { page = PgMaster; redraw(); }
                else if (ks == XK_4) { page = PgSystem; hb_menu = false; hb_fetch(sites); hb_fetch_at = now(); redraw(); }
                else if (ks == XK_5) { page = PgBackup; spawn_bk_status(); redraw(); }
                else if (ks == XK_l || ks == XK_L) { show_push_log = true; redraw(); }
                else if (ks == XK_r) {
                    sites = load_sites();
                    for (auto& s : sites) poll_site(s);
                    apply_deploys(sites);
                    usbs = list_usb();
                    usb_sel = pick_usb_sel(usbs, usb_sel);
                    say("refreshed");
                    redraw();
                }
            } else if (e.type == ButtonPress && e.xbutton.button == 1) {
                int x = e.xbutton.x, y = e.xbutton.y;
                bool consumed = false;
                bool on_edit_field = false;
                bool on_bk_field = false;
                bool on_api = false;
                bool on_ssh = false;
                std::string editing_before = rename_id;
                std::string ssh_before = ssh_id;
                for (auto& h : hits) {
                    if (!inside(h.r, x, y)) continue;
                    consumed = true;
                    if (h.kind == Hit::Close) g_run = 0;
                    else if (h.kind == Hit::IphoneGw) {
                        if (iphone_gw_up()) {
                            int left = iphone_gw_stop();
                            gw_probe_at = 0;
                            gw_cached_up = left != 0;
                            say(left ? "iPhone gateway still running — click again"
                                     : "iPhone gateway OFF — PWA :8787 stopped");
                        } else if (iphone_gw_start()) {
                            usleep(250000);
                            gw_probe_at = 0;
                            gw_cached_up = iphone_gw_up();
                            say(gw_cached_up ? "iPhone gateway ON  ·  127.0.0.1:8787"
                                             : "iPhone gateway start sent — wait a beat");
                        } else {
                            say("iPhone gateway start failed — missing iphone/gateway.py");
                        }
                    }
                    else if (h.kind == Hit::Nav) {
                        page = (Page)h.i;
                        hb_menu = false;
                        if (page == PgSystem) { hb_fetch(sites); hb_fetch_at = now(); }
                        if (page == PgBackup) spawn_bk_status();
                    }
                    else if (h.kind == Hit::HbDrop) hb_menu = !hb_menu;
                    else if (h.kind == Hit::HbPick) {
                        if (h.i >= 0 && h.i < (int)sites.size()) hb_sel = sites[static_cast<size_t>(h.i)].id;
                        hb_all = false;
                        hb_menu = false;
                    }
                    else if (h.kind == Hit::HbAll) { hb_all = !hb_all; hb_menu = false; }
                    else if (h.kind == Hit::HbDownload) start_hb("download");
                    else if (h.kind == Hit::HbExecute) start_hb("execute");
                    else if (h.kind == Hit::BkDest) {
                        if (h.i >= 0 && h.i < (int)bk_rows.size()) {
                            bk_dest = bk_rows[static_cast<size_t>(h.i)];
                            bk_picked = true;
                            bk_edit = false;
                            save_bk_dest(bk_dest);
                        }
                    }
                    else if (h.kind == Hit::BkEdit) {
                        bk_edit = true;
                        bk_buf = bk_dest;
                        on_bk_field = true;
                        say("edit folder  ·  Enter saves  ·  Esc cancels");
                    }
                    else if (h.kind == Hit::BkCreate) {
                        if (bk_edit) {
                            bk_dest = bk_buf;
                            bk_edit = false;
                            bk_picked = true;
                            save_bk_dest(bk_dest);
                        }
                        start_bk();
                    }
                    else if (h.kind == Hit::BkCancel) stop_bk();
                    else if (h.kind == Hit::BkRefresh) spawn_bk_status();
                    else if (h.kind == Hit::ApiKey) {
                        on_api = true;
                        if (h.i == 3) {
                            /* paste field stays open */
                        } else if (api_busy) {
                            say("still saving the API key");
                        } else if (h.i == 1) {
                            submit_api();
                        } else if (h.i == 2) {
                            cancel_api();
                            say("API key change cancelled");
                        } else {
                            wipe_secret(api_buf);
                            api_edit = true;
                            say("paste the key once · Enter saves · Esc cancels · running hunters reload, paused stay paused, positions stay");
                        }
                    }
                    else if (h.kind == Hit::Ssh) {
                        on_ssh = true;
                        if (h.i < 0 || h.i >= (int)sites.size() || is_mothership(sites[static_cast<size_t>(h.i)])) {
                            say("SSH credentials are for a child matrix");
                        } else if (h.j == 2) {
                            cancel_ssh();
                            say("SSH edit cancelled");
                        } else if (h.j == 1) {
                            commit_ssh();
                        } else if (h.j >= 10 && h.j < 14) {
                            ssh_field = h.j - 10;
                        } else if (h.j == 0) {
                            std::string sid = sites[static_cast<size_t>(h.i)].id;
                            if (ssh_id != sid) {
                                SshCred c = load_ssh_cred(sid);
                                ssh_id = sid;
                                ssh_vpn = c.vpn;
                                ssh_lan = c.lan;
                                ssh_root = c.root;
                                ssh_live = c.live;
                                ssh_field = 0;
                                say("SSH  ·  Enter saves  ·  Esc cancels  ·  Tab next field");
                            }
                        }
                    }
                    else if (h.kind == Hit::UsbRow) usb_sel = h.i;
                    else if (h.kind == Hit::DeployMode) deploy_mode = h.i;
                    else if (h.kind == Hit::RefreshUsb) {
                        usbs = list_usb();
                        usb_sel = pick_usb_sel(usbs, usb_sel);
                        int nready = 0;
                        for (auto& u : usbs) if (u.ready) nready++;
                        say(nready ? (std::to_string(nready) + " USB ready") : "no USB stick ready — empty reader is not a stick");
                    } else if (h.kind == Hit::RefreshLan) {
                        if (lan_pid) say("LAN scan already running");
                        else {
                            lan_pid = spawn_lan_scan();
                            lan_started = now();
                            say(lan_pid ? "scanning LAN for a new matrix" : "could not start LAN scan");
                        }
                    } else if (h.kind == Hit::AddChild) {
                        if (h.i < 0 || h.i >= (int)add_targets.size()) say("that LAN box is gone — scan again");
                        else {
                            std::string ip = add_targets[static_cast<size_t>(h.i)];
                            std::string err;
                            if (add_matrix_child(ip, err)) {
                                sites = load_sites();
                                for (auto& s : sites) poll_site(s);
                                apply_deploys(sites);
                                say("child added  " + ip + "  — under the mothership");
                            } else {
                                say(err.empty() ? "could not add child" : err);
                            }
                        }
                    } else if (h.kind == Hit::MakeUsb) start_usb();
                    else if (h.kind == Hit::BuildMintIso) start_mint_build();
                    else if (h.kind == Hit::WriteMintIso) start_mint_write();
                    else if (h.kind == Hit::RebuildWrite) {
                        if (mint_busy || usb_busy || boot_cache.on)
                            say("a deploy job is already running — wait until it finishes");
                        else {
                            mint_then_write = true;
                            start_mint_build();
                            if (!mint_busy) mint_then_write = false;
                            else say("rebuilding the image, then formatting the selected stick");
                        }
                    }
                    else if (h.kind == Hit::RenameName) {
                        if (h.i >= 0 && h.i < (int)sites.size() && !is_mothership(sites[static_cast<size_t>(h.i)])) {
                            std::string sid = sites[static_cast<size_t>(h.i)].id;
                            std::string sname = sites[static_cast<size_t>(h.i)].name;
                            live_sel = h.i;
                            if (editing_before == sid) on_edit_field = true;
                            double t = now();
                            bool again = t - last_name_click < 0.45 && last_name_click_id == sid;
                            last_name_click = t;
                            last_name_click_id = sid;
                            if (again) {
                                if (!rename_id.empty() && rename_id != sid) commit_rename();
                                rename_id = sid;
                                rename_buf = sname;
                                on_edit_field = true;
                                say("rename  ·  Enter saves  ·  Esc cancels");
                            }
                        }
                    }
                    else if (h.kind == Hit::LiveSite) live_sel = h.i;
                    else if (h.kind == Hit::LivePush) {
                        if (h.i >= 0 && h.i < (int)sites.size()
                            && !is_mothership(sites[static_cast<size_t>(h.i)]))
                            live_sel = h.i;
                        int idx = live_sel;
                        if (idx < 0 || idx >= (int)sites.size()
                            || is_mothership(sites[static_cast<size_t>(idx)])) {
                            for (int i = 0; i < (int)sites.size(); i++)
                                if (!is_mothership(sites[static_cast<size_t>(i)])) { idx = i; break; }
                        }
                        if (!live_busy && idx >= 0 && idx < (int)sites.size()
                            && child_has_latest(sites[static_cast<size_t>(idx)], mothership_of(sites), local_archy()))
                            say(sites[static_cast<size_t>(idx)].name + " already on "
                                + child_installed(sites[static_cast<size_t>(idx)]));
                        else
                            start_live(false);
                    }
                    else if (h.kind == Hit::LiveSamba) start_live(true);
                    else if (h.kind == Hit::PushLogClose) {
                        show_push_log = false;
                        selecting = false;
                        log_close_at = 0;
                    }
                    else if (h.kind == Hit::PushLogOpen) {
                        if (h.i >= 0) log_kind = h.i;
                        show_push_log = true;
                        log_close_at = 0;
                    }
                    else if (h.kind == Hit::LogCopy) { log_close_at = 0; copy_log(); }
                    else if (h.kind == Hit::GenMaster) run_wallet("generate");
                    else if (h.kind == Hit::CreateSubs) run_wallet("create-subs");
                    redraw();
                    break;
                }
                if (!editing_before.empty() && !on_edit_field && rename_id == editing_before) {
                    commit_rename();
                    redraw();
                }
                if (api_edit && !on_api) {
                    cancel_api();
                    redraw();
                }
                if (!ssh_before.empty() && !on_ssh && ssh_id == ssh_before) {
                    cancel_ssh();
                    redraw();
                }
                if (bk_edit && !on_bk_field && !consumed) {
                    std::string p = bk_buf;
                    while (!p.empty() && p.back() == ' ') p.pop_back();
                    struct stat ds{};
                    bk_edit = false;
                    if (!p.empty() && p[0] == '/' && stat(p.c_str(), &ds) == 0 && S_ISDIR(ds.st_mode)) {
                        bk_dest = p;
                        bk_picked = true;
                        save_bk_dest(p);
                        say("backup folder saved");
                    }
                    redraw();
                }
                if (!consumed && show_push_log && inside(log_body, x, y)) {
                    log_close_at = 0;
                    reload_log_lines();
                    LogPos p = pos_at(x, y);
                    double t = now();
                    if (t - last_log_click < 0.4 && last_log_click_line == p.line &&
                        p.line >= 0 && p.line < (int)log_lines.size()) {
                        sel_a = {p.line, 0};
                        sel_b = {p.line, (int)log_lines[static_cast<size_t>(p.line)].size()};
                        has_sel = true;
                        selecting = false;
                    } else {
                        sel_a = sel_b = p;
                        has_sel = true;
                        selecting = true;
                    }
                    last_log_click = t;
                    last_log_click_line = p.line;
                    redraw();
                }
            } else if (e.type == ButtonRelease && e.xbutton.button == 1) {
                if (selecting) {
                    selecting = false;
                    sel_b = pos_at(e.xbutton.x, e.xbutton.y);
                    if (sel_a.line != sel_b.line || sel_a.col != sel_b.col) {
                        clip_store = sel_text();
                        XSetSelectionOwner(dpy, XA_PRIMARY, win, CurrentTime);
                    }
                    redraw();
                }
            }
        }
        poll_api();
        poll_usb_job();
        poll_mint_job();
        poll_live_job();
        poll_hb_job();
        poll_bk_job();
        poll_bk_stat();
        ck_pump();
        if (page == PgHome || page == PgDeploy) rd_kick(sites);
        if (page == PgHome) ck_kick(sites);
        if (log_close_at > 0 && now() >= log_close_at) {
            if (selecting || has_sel) {
                log_close_at = now() + 1.5;
            } else {
                show_push_log = false;
                log_close_at = 0;
                redraw();
            }
        }
        if (lan_pid) {
            int st = 0;
            pid_t r = waitpid(lan_pid, &st, WNOHANG);
            if (r == lan_pid || (r < 0 && errno == ECHILD)) {
                lan_pid = 0;
                lan_boxes = read_lan_scan();
                lan_scanned = true;
                redraw();
            }
        }
        while (waitpid(-1, nullptr, WNOHANG) > 0) {}
        if ((page == PgHome || page == PgSystem || page == PgBackup || show_push_log || mint_busy || usb_busy ||
             hb_pid || bk_pid || (page == PgDeploy && (boot_cache.on || deploy_mode == 1))) &&
            now() - last_anim > 0.05) {
            last_anim = now();
            redraw();
        }
        if (now() - last > 2.5) {
            last = now();
            if (!enroll_note && !enroll_listening()) {
                enroll_note = true;
                say("new boxes stay hidden until:  sudo ./rimbaud_vpn/rimbaud-vpn listen-install");
            }
            if (!lan_pid && now() - lan_started > 20) {
                lan_pid = spawn_lan_scan();
                lan_started = now();
            }
            if (page == PgHome && !api_edit && !api_busy)
                load_api_view(api_addr, api_exch, api_note);
            if (page == PgHome || page == PgDeploy) {
                sites = load_sites();
                for (auto& s : sites) poll_site(s);
                apply_deploys(sites);
            }
            if (page == PgDeploy && !usb_busy && !mint_busy) {
                usbs = list_usb();
                usb_sel = pick_usb_sel(usbs, usb_sel);
            }
            if (page == PgSystem && now() - hb_fetch_at > 20) {
                hb_fetch(sites);
                hb_fetch_at = now();
            }
            if (page == PgBackup && !bk_stat_pid && now() - bk_stat_at > 8) spawn_bk_status();
            if (page == PgMaster) {
                pid_t p = fork();
                if (p == 0) {
                    std::string py = repo_root() + "/HL_wallet_creator/hl_wallet_creator.py";
                    setenv("HL_WALLET_OUT", (conf_dir() + "/hl_wallets").c_str(), 1);
                    execl("/usr/bin/python3", "python3", py.c_str(), "status",
                          static_cast<char*>(nullptr));
                    _exit(127);
                }
            }
            redraw();
        }
        struct timespec sl{0, 30 * 1000 * 1000};
        nanosleep(&sl, nullptr);
    }
    XFreePixmap(dpy, buf);
    XFreeGC(dpy, gc);
    if (fs) XFreeFont(dpy, fs);
    XFreeCursor(dpy, cur_arrow);
    XFreeCursor(dpy, cur_hand);
    XFreeCursor(dpy, cur_xterm);
    XDestroyWindow(dpy, win);
    XCloseDisplay(dpy);
    return 0;
}
