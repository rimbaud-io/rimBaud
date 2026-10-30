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

// rimbaud-archy desk — same look as rimBaud meta. Operator LEVEL UP only.
#include <nlohmann/json.hpp>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/keysym.h>
#include <X11/cursorfont.h>
#include <X11/Xatom.h>

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <fstream>
#include <signal.h>
#include <sstream>
#include <string>
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

static std::string ascii(const std::string& s, int maxn = 0) {
    std::string o;
    for (unsigned char c : s) {
        if (c >= 32 && c < 127) o += static_cast<char>(c);
        else if (c == '\n' || c == '\t') o += ' ';
    }
    if (maxn > 0 && (int)o.size() > maxn) o.resize((size_t)maxn);
    return o;
}

static std::string jstr(const json& j, const char* k, const std::string& d = "") {
    if (!j.contains(k) || j[k].is_null()) return d;
    try {
        if (j[k].is_string()) return j[k].get<std::string>();
        if (j[k].is_number() || j[k].is_boolean()) return json(j[k]).dump();
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
static std::string db_py() {
    return repo_root() + "/rimbaud-archy/archy_db.py";
}

static json slurp_cmd(const std::string& cmd) {
    FILE* fp = popen(cmd.c_str(), "r");
    if (!fp) return json::object();
    std::string raw;
    char buf[4096];
    while (fgets(buf, sizeof(buf), fp)) raw += buf;
    pclose(fp);
    try { return json::parse(raw.empty() ? "{}" : raw); } catch (...) { return json::object(); }
}

static json load_status() {
    return slurp_cmd("python3 \"" + db_py() + "\" --status 2>/dev/null");
}

struct Hit {
    enum Kind { Idle, Close, LevelUp, Refresh, Github } kind = Idle;
    Rect r{};
};

struct GhJob {
    pid_t pid = 0;
    int kind = 0;
};

static const char* GH_FILE = "/tmp/rimbaud-archy-github.json";

static bool spawn_github(GhJob& job, int kind, const std::string& script) {
    if (job.pid > 0) return false;
    unlink(GH_FILE);
    pid_t pid = fork();
    if (pid < 0) return false;
    if (pid == 0) {
        int fd = open(GH_FILE, O_WRONLY | O_CREAT | O_TRUNC, 0600);
        if (fd >= 0) {
            dup2(fd, STDOUT_FILENO);
            close(fd);
        }
        int err = open("/dev/null", O_WRONLY);
        if (err >= 0) {
            dup2(err, STDERR_FILENO);
            if (err != STDERR_FILENO) close(err);
        }
        setenv("ARCHY_OPERATOR", "1", 1);
        const char* flag = kind == 2 ? "--operator-send-github" : "--operator-prepare-github";
        execlp("python3", "python3", script.c_str(), flag, (char*)nullptr);
        _exit(127);
    }
    job.pid = pid;
    job.kind = kind;
    return true;
}

static int poll_github(GhJob& job, json& out) {
    if (job.pid <= 0) return 0;
    int status = 0;
    pid_t got = waitpid(job.pid, &status, WNOHANG);
    if (got == 0) return 0;
    int kind = job.kind;
    job.pid = 0;
    job.kind = 0;
    std::ifstream in(GH_FILE);
    std::stringstream ss;
    ss << in.rdbuf();
    std::string raw = ss.str();
    auto cut = raw.find_last_of('\n');
    if (cut != std::string::npos && cut + 1 < raw.size()) raw = raw.substr(cut + 1);
    try { out = json::parse(raw.empty() ? "{}" : raw); }
    catch (...) { out = json::object(); }
    return kind;
}

int archy_desk_main() {
    signal(SIGINT, on_int);
    signal(SIGTERM, on_int);
    Display* dpy = XOpenDisplay(nullptr);
    if (!dpy) { fprintf(stderr, "rimbaud-archy: no DISPLAY\n"); return 1; }
    int scr = DefaultScreen(dpy);
    Colormap cm = DefaultColormap(dpy, scr);
    unsigned long bg = rgb(dpy, cm, 2, 8, 4);
    unsigned long navy = rgb(dpy, cm, 3, 16, 10);
    unsigned long panel = rgb(dpy, cm, 4, 18, 8);
    unsigned long border = rgb(dpy, cm, 13, 59, 31);
    unsigned long green = rgb(dpy, cm, 0, 255, 106);
    unsigned long green_dim = rgb(dpy, cm, 0, 179, 71);
    unsigned long text = rgb(dpy, cm, 157, 255, 192);
    unsigned long muted = rgb(dpy, cm, 61, 122, 85);
    unsigned long gold = rgb(dpy, cm, 240, 185, 11);
    unsigned long orange = rgb(dpy, cm, 255, 140, 40);
    unsigned long cyan = rgb(dpy, cm, 126, 203, 255);
    unsigned long rose = rgb(dpy, cm, 255, 77, 109);
    unsigned long clip = rgb(dpy, cm, 44, 44, 44);

    int W = 920, H = 760;
    XSetWindowAttributes wa{};
    wa.background_pixel = bg;
    wa.event_mask = ExposureMask | StructureNotifyMask | ButtonPressMask | ButtonReleaseMask
        | KeyPressMask | PointerMotionMask | ButtonMotionMask;
    Window win = XCreateWindow(dpy, RootWindow(dpy, scr), 80, 80, W, H, 0,
                               DefaultDepth(dpy, scr), InputOutput, DefaultVisual(dpy, scr),
                               CWBackPixel | CWEventMask, &wa);
    XStoreName(dpy, win, "rimBaud  //  ARCHY");
    XClassHint ch{}; ch.res_name = (char*)"rimbaud-archy"; ch.res_class = (char*)"Rimbaud";
    XSetClassHint(dpy, win, &ch);
    Atom wm_delete = XInternAtom(dpy, "WM_DELETE_WINDOW", False);
    XSetWMProtocols(dpy, win, &wm_delete, 1);
    GC gc = XCreateGC(dpy, win, 0, nullptr);
    XFontStruct* fs = XLoadQueryFont(dpy, "-*-fixed-medium-r-*-*-13-*-*-*-*-*-*-*");
    if (!fs) fs = XLoadQueryFont(dpy, "fixed");
    if (fs) XSetFont(dpy, gc, fs->fid);
    Pixmap buf = XCreatePixmap(dpy, win, W, H, DefaultDepth(dpy, scr));
    Cursor cur_arrow = XCreateFontCursor(dpy, XC_left_ptr);
    Cursor cur_hand = XCreateFontCursor(dpy, XC_hand2);
    Cursor cur_ibeam = XCreateFontCursor(dpy, XC_xterm);
    Atom atom_clip = XInternAtom(dpy, "CLIPBOARD", False);
    Atom atom_utf8 = XInternAtom(dpy, "UTF8_STRING", False);
    Atom atom_targets = XInternAtom(dpy, "TARGETS", False);
    Atom atom_text = XInternAtom(dpy, "TEXT", False);
    unsigned long mark_bg = rgb(dpy, cm, 14, 78, 52);
    XMapWindow(dpy, win);

    auto fill = [&](unsigned long c, Rect r) {
        XSetForeground(dpy, gc, c);
        XFillRectangle(dpy, buf, gc, r.x, r.y, r.w, r.h);
    };
    auto stroke = [&](unsigned long c, Rect r) {
        XSetForeground(dpy, gc, c);
        XDrawRectangle(dpy, buf, gc, r.x, r.y, r.w - 1, r.h - 1);
    };
    auto txt = [&](unsigned long c, int x, int y, const std::string& s) {
        XSetForeground(dpy, gc, c);
        XDrawString(dpy, buf, gc, x, y, s.c_str(), (int)s.size());
    };

    json st = load_status();
    std::string toast;
    double toast_until = 0, arm_until = 0;
    GhJob gh;
    bool gh_armed = false;
    double send_until = 0;
    std::vector<std::string> gh_lines;
    auto now = []() {
        timespec ts{};
        clock_gettime(CLOCK_MONOTONIC, &ts);
        return ts.tv_sec + ts.tv_nsec / 1e9;
    };
    auto say = [&](const std::string& s) { toast = s; toast_until = now() + 4; };

    std::vector<Hit> hits;
    struct LogRow {
        std::string shown;
        std::string full;
        int x = 0;
        int base = 0;
    };
    std::vector<LogRow> log_rows;
    Rect log_box{0, 0, 0, 0};
    int mark_a_row = -1, mark_a_col = 0, mark_b_row = -1, mark_b_col = 0;
    bool marking = false;
    std::string clip_text;

    auto glyphs_width = [&](const std::string& s, int n) -> int {
        if (n <= 0) return 0;
        if (n > (int)s.size()) n = (int)s.size();
        if (!fs) return n * 6;
        return XTextWidth(fs, s.c_str(), n);
    };
    auto col_at = [&](const std::string& s, int px, int x0) -> int {
        if (!fs) {
            int col = (px - x0 + 3) / 6;
            if (col < 0) col = 0;
            if (col > (int)s.size()) col = (int)s.size();
            return col;
        }
        int prev = 0;
        for (int n = 0; n < (int)s.size(); n++) {
            int w = XTextWidth(fs, s.c_str(), n + 1);
            if (px < x0 + (prev + w) / 2) return n;
            prev = w;
        }
        return (int)s.size();
    };
    auto mark_from_pixel = [&](int px, int py) {
        if (log_rows.empty()) return;
        int row = 0;
        if (py >= log_rows.back().base) row = (int)log_rows.size() - 1;
        else if (py > log_rows.front().base - 14) {
            int bestd = 100000;
            for (int i = 0; i < (int)log_rows.size(); i++) {
                int d = py - log_rows[i].base;
                if (d < 0) d = -d;
                if (d < bestd) { bestd = d; row = i; }
            }
        }
        mark_b_row = row;
        mark_b_col = col_at(log_rows[row].shown, px, log_rows[row].x);
    };
    auto marked_text = [&]() -> std::string {
        if (mark_a_row < 0 || mark_b_row < 0 || log_rows.empty()) return "";
        int r0 = mark_a_row, c0 = mark_a_col, r1 = mark_b_row, c1 = mark_b_col;
        if (r0 > r1 || (r0 == r1 && c0 > c1)) {
            std::swap(r0, r1);
            std::swap(c0, c1);
        }
        if (r0 >= (int)log_rows.size()) return "";
        if (r1 >= (int)log_rows.size()) r1 = (int)log_rows.size() - 1;
        std::string out;
        for (int i = r0; i <= r1; i++) {
            const LogRow& row = log_rows[i];
            int a = (i == r0) ? c0 : 0;
            int b = (i == r1) ? c1 : (int)row.shown.size();
            if (a < 0) a = 0;
            if (a > (int)row.shown.size()) a = (int)row.shown.size();
            if (b < a) b = a;
            if (b > (int)row.shown.size()) b = (int)row.shown.size();
            bool whole = a == 0 && b == (int)row.shown.size() && !row.shown.empty();
            if (i > r0) out += "\n";
            out += whole ? row.full : row.shown.substr((size_t)a, (size_t)(b - a));
        }
        return out;
    };
    auto offer_clip = [&](const std::string& s) {
        if (s.empty()) return;
        clip_text = s;
        XSetSelectionOwner(dpy, XA_PRIMARY, win, CurrentTime);
        XSetSelectionOwner(dpy, atom_clip, win, CurrentTime);
    };

    auto redraw = [&]() {
        hits.clear();
        fill(bg, {0, 0, W, H});
        fill(navy, {0, 0, W, 72});
        XSetForeground(dpy, gc, border);
        XDrawLine(dpy, buf, gc, 0, 72, W, 72);
        txt(rgb(dpy, cm, 42, 159, 214), 14, 22, "rimBaud  //  ARCHY");
        txt(gold, 280, 22, "versionizing toolkit   ·   mothership ledger");
        if (now() < toast_until && !toast.empty())
            txt(cyan, 14, 62, ascii(toast, 90));
        else
            txt(rgb(dpy, cm, 255, 200, 87), 14, 62,
                "Pilot = g++ only   ·   LEVEL UP 0.1→0.2 is you   ·   never CLOSE/OPEN");

        Rect rc{W - 78, 10, 64, 22};
        fill(clip, rc); stroke(orange, rc);
        txt(orange, rc.x + 12, rc.y + 15, "CLOSE");
        hits.push_back({Hit::Close, rc});

        std::string ver = jstr(st, "version", "0.1");
        int minor = 1, pilot = 0;
        try { if (st.contains("minor")) minor = st["minor"].get<int>(); } catch (...) {}
        try { if (st.contains("pilot")) pilot = st["pilot"].get<int>(); } catch (...) {}
        char next[32];
        snprintf(next, sizeof(next), "0.%d", minor + 1);

        Rect hero{18, 88, W - 36, 88};
        fill(panel, hero); stroke(green, hero);
        txt(muted, hero.x + 16, hero.y + 22, "CURRENT  mothership");
        txt(green, hero.x + 16, hero.y + 52, ver);
        char sub[120];
        snprintf(sub, sizeof(sub), "pilot edits on this minor  g%d    updated %s",
                 pilot, ascii(jstr(st, "updated"), 22).c_str());
        txt(muted, hero.x + 16, hero.y + 74, sub);

        Rect lup{hero.x + hero.w - 210, hero.y + 22, 190, 48};
        bool armed = now() < arm_until;
        fill(armed ? rgb(dpy, cm, 50, 22, 8) : clip, lup);
        stroke(armed ? orange : gold, lup);
        txt(armed ? orange : gold, lup.x + 14, lup.y + 20, armed ? "CLICK AGAIN TO CONFIRM" : "LEVEL UP  (you only)");
        txt(armed ? orange : muted, lup.x + 14, lup.y + 38, std::string(ver) + "  ->  " + next);
        hits.push_back({Hit::LevelUp, lup});

        Rect gpan{18, 184, W - 36, 112};
        bool sending = gh_armed && now() < send_until;
        fill(panel, gpan);
        stroke(sending ? orange : border, gpan);
        txt(gold, gpan.x + 12, gpan.y + 20, "GITHUB");
        txt(muted, gpan.x + 84, gpan.y + 20, "1 copy and audit    2 click again to send");
        bool busy = gh.pid > 0;
        const char* lab = "TO GITHUB";
        if (busy && gh.kind == 2) lab = "SENDING";
        else if (busy) lab = "COPYING";
        else if (sending) lab = "CLICK AGAIN TO SEND";
        Rect gb{gpan.x + gpan.w - 236, gpan.y + 8, 220, 28};
        fill(sending ? rgb(dpy, cm, 50, 22, 8) : clip, gb);
        stroke(sending ? orange : gold, gb);
        txt(sending ? orange : gold, gb.x + 14, gb.y + 18, lab);
        if (!busy) hits.push_back({Hit::Github, gb});
        int gly = gpan.y + 48;
        if (gh_lines.empty()) {
            txt(muted, gpan.x + 12, gly, "a personal detail blocks the send");
        } else {
            unsigned long col = sending ? green : (gh.pid > 0 ? cyan : rose);
            for (auto& line : gh_lines) {
                if (gly > gpan.y + gpan.h - 10) break;
                txt(col, gpan.x + 12, gly, ascii(line, 96));
                gly += 16;
            }
        }

        txt(gold, 18, 312, "DEPLOYED MATRICES");
        json deps = st.contains("deploys") ? st["deploys"] : json::array();
        int y = 328;
        if (!deps.is_array() || deps.empty()) {
            txt(muted, 24, y + 8, "none yet  ·  Meta PUSH latest records the version on that box");
            y += 28;
        } else {
            for (auto& d : deps) {
                if (!d.is_object()) continue;
                Rect row{18, y, W - 36, 28};
                fill(panel, row); stroke(border, row);
                char line[200];
                snprintf(line, sizeof(line), "%-14s  %-18s  v%-10s  %s",
                         ascii(jstr(d, "site_id"), 14).c_str(),
                         ascii(jstr(d, "host"), 18).c_str(),
                         ascii(jstr(d, "version"), 10).c_str(),
                         ascii(jstr(d, "ts"), 22).c_str());
                txt(text, row.x + 10, row.y + 18, line);
                y += 32;
            }
        }

        y += 8;
        txt(gold, 18, y, "CHANGE LOG");
        txt(muted, 130, y, "drag to mark  ·  release copies");
        y += 16;
        Rect logp{18, y, W - 36, H - y - 16};
        log_box = logp;
        fill(rgb(dpy, cm, 2, 10, 6), logp); stroke(border, logp);
        json logs = st.contains("log") ? st["log"] : json::array();
        int ly = logp.y + 18;
        int maxl = std::max(4, (logp.h - 24) / 16);
        int n = 0;
        int text_x = logp.x + 10;
        int max_px = logp.w - 20;
        log_rows.clear();
        if (logs.is_array()) {
            for (auto& e : logs) {
                if (n >= maxl) break;
                if (!e.is_object()) continue;
                std::string kind = jstr(e, "kind");
                unsigned long col = muted;
                if (kind == "user") col = gold;
                else if (kind == "pilot") col = cyan;
                else if (kind == "deploy") col = green;
                else if (kind == "github") col = orange;
                char head[96];
                snprintf(head, sizeof(head), "%s  %-6s  %-8s -> %-8s  ",
                         ascii(jstr(e, "ts"), 19).c_str(),
                         ascii(kind, 6).c_str(),
                         ascii(jstr(e, "from_ver"), 12).c_str(),
                         ascii(jstr(e, "to_ver"), 12).c_str());
                std::string full = std::string(head) + ascii(jstr(e, "summary"), 500);
                std::string shown = full;
                while ((int)shown.size() > 8 && glyphs_width(shown, (int)shown.size()) > max_px)
                    shown.pop_back();
                int r0 = mark_a_row, c0 = mark_a_col, r1 = mark_b_row, c1 = mark_b_col;
                if (r0 > r1 || (r0 == r1 && c0 > c1)) {
                    std::swap(r0, r1);
                    std::swap(c0, c1);
                }
                if (r0 >= 0 && n >= r0 && n <= r1) {
                    int a = (n == r0) ? c0 : 0;
                    int b = (n == r1) ? c1 : (int)shown.size();
                    if (a < 0) a = 0;
                    if (b > (int)shown.size()) b = (int)shown.size();
                    if (b < a) b = a;
                    if (b > a) {
                        int x0 = text_x + glyphs_width(shown, a);
                        int x1 = text_x + glyphs_width(shown, b);
                        int top = ly - (fs ? fs->ascent : 12);
                        int h = (fs ? fs->ascent + fs->descent : 14) + 1;
                        fill(mark_bg, {x0, top, std::max(1, x1 - x0), h});
                    }
                }
                txt(col, text_x, ly, shown);
                log_rows.push_back({shown, full, text_x, ly});
                ly += 16;
                n++;
            }
        }
        if (mark_a_row >= (int)log_rows.size() || mark_b_row >= (int)log_rows.size()) {
            mark_a_row = mark_b_row = -1;
        }

        XCopyArea(dpy, buf, win, gc, 0, 0, W, H, 0, 0);
        XFlush(dpy);
    };

    auto apply_github = [&](const json& rep, int kind) {
        gh_lines.clear();
        if (rep.contains("log") && rep["log"].is_array()) st = rep;
        bool clean = false;
        bool pushed = false;
        try { if (rep.contains("clean") && rep["clean"].is_boolean()) clean = rep["clean"].get<bool>(); } catch (...) {}
        try { if (rep.contains("pushed") && rep["pushed"].is_boolean()) pushed = rep["pushed"].get<bool>(); } catch (...) {}
        std::string msg = ascii(jstr(rep, "message", jstr(rep, "error", "github copy failed")), 96);
        if (pushed && rep.contains("version")) {
            st = rep;
            gh_armed = false;
            send_until = 0;
            gh_lines.push_back(msg.empty() ? "sent to github" : msg);
            say(gh_lines.back());
            return;
        }
        if (kind == 1 && clean) {
            gh_armed = true;
            send_until = now() + 15;
            gh_lines.push_back("audit clean - no personal information");
            gh_lines.push_back(msg.empty() ? "click again to send" : msg);
            say("audit clean - click again to send");
            return;
        }
        gh_armed = false;
        send_until = 0;
        if (rep.contains("findings") && rep["findings"].is_array() && !rep["findings"].empty()) {
            int extra = (int)rep["findings"].size();
            for (auto& f : rep["findings"]) {
                if (!f.is_object() || (int)gh_lines.size() >= 3) break;
                int n = 1;
                try { if (f.contains("n") && f["n"].is_number()) n = f["n"].get<int>(); } catch (...) {}
                char line[180];
                snprintf(line, sizeof(line), "%s  %s  x%d",
                         ascii(jstr(f, "kind"), 16).c_str(),
                         ascii(jstr(f, "file"), 62).c_str(),
                         n);
                gh_lines.push_back(line);
                extra--;
            }
            if (extra > 0 && !gh_lines.empty()) gh_lines.back() += "  +";
            say("audit blocked the send");
            return;
        }
        gh_lines.push_back(msg.empty() ? "github copy failed" : msg);
        say(gh_lines.back());
    };

    redraw();
    while (g_run) {
        json gh_done;
        int gh_kind = poll_github(gh, gh_done);
        if (gh_kind) {
            apply_github(gh_done, gh_kind);
            redraw();
        } else if (gh_armed && gh.pid <= 0 && now() >= send_until) {
            gh_armed = false;
            say("send cancelled");
            redraw();
        }
        while (XPending(dpy)) {
            XEvent e;
            XNextEvent(dpy, &e);
            if (e.type == ClientMessage) g_run = 0;
            else if (e.type == Expose) redraw();
            else if (e.type == ConfigureNotify && e.xconfigure.width > 400) {
                W = e.xconfigure.width; H = e.xconfigure.height;
                XFreePixmap(dpy, buf);
                buf = XCreatePixmap(dpy, win, W, H, DefaultDepth(dpy, scr));
                redraw();
            } else if (e.type == MotionNotify) {
                if (marking) {
                    int before_r = mark_b_row, before_c = mark_b_col;
                    mark_from_pixel(e.xmotion.x, e.xmotion.y);
                    if (mark_b_row != before_r || mark_b_col != before_c) redraw();
                }
                bool hand = false;
                if (!marking) {
                    for (auto& h : hits)
                        if (inside(h.r, e.xmotion.x, e.xmotion.y) && h.kind != Hit::Idle) hand = true;
                }
                bool in_log = inside(log_box, e.xmotion.x, e.xmotion.y);
                XDefineCursor(dpy, win, hand ? cur_hand : ((in_log || marking) ? cur_ibeam : cur_arrow));
            } else if (e.type == SelectionRequest) {
                XSelectionRequestEvent* req = &e.xselectionrequest;
                XSelectionEvent sev{};
                sev.type = SelectionNotify;
                sev.display = req->display;
                sev.requestor = req->requestor;
                sev.selection = req->selection;
                sev.target = req->target;
                sev.time = req->time;
                sev.property = req->property == None ? req->target : req->property;
                bool ok = false;
                if (req->target == atom_targets) {
                    Atom list[4] = {atom_utf8, XA_STRING, atom_text, atom_targets};
                    XChangeProperty(dpy, req->requestor, sev.property, XA_ATOM, 32,
                                    PropModeReplace, reinterpret_cast<unsigned char*>(list), 4);
                    ok = true;
                } else if (req->target == XA_STRING || req->target == atom_utf8 || req->target == atom_text) {
                    XChangeProperty(dpy, req->requestor, sev.property, req->target, 8,
                                    PropModeReplace, reinterpret_cast<const unsigned char*>(clip_text.data()),
                                    (int)clip_text.size());
                    ok = true;
                }
                if (!ok) sev.property = None;
                XSendEvent(dpy, req->requestor, False, 0, reinterpret_cast<XEvent*>(&sev));
                XFlush(dpy);
            } else if (e.type == KeyPress) {
                KeySym ks = XLookupKeysym(&e.xkey, 0);
                if (ks == XK_q || ks == XK_Escape) g_run = 0;
                else if (ks == XK_r) { st = load_status(); say("refreshed"); redraw(); }
                else if ((e.xkey.state & ControlMask) && (ks == XK_c || ks == XK_C)) {
                    std::string text = marked_text();
                    if (!text.empty()) { offer_clip(text); say("copied"); redraw(); }
                }
            } else if (e.type == ButtonRelease && e.xbutton.button == 1 && marking) {
                mark_from_pixel(e.xbutton.x, e.xbutton.y);
                if (mark_a_row == mark_b_row && mark_a_col == mark_b_col && mark_a_row >= 0
                    && mark_a_row < (int)log_rows.size()) {
                    mark_a_col = 0;
                    mark_b_col = (int)log_rows[mark_a_row].shown.size();
                }
                marking = false;
                XUngrabPointer(dpy, CurrentTime);
                std::string text = marked_text();
                if (!text.empty()) { offer_clip(text); say("copied"); }
                redraw();
            } else if (e.type == ButtonPress && e.xbutton.button == 1) {
                for (auto& h : hits) {
                    if (!inside(h.r, e.xbutton.x, e.xbutton.y)) continue;
                    if (h.kind == Hit::Close) g_run = 0;
                    else if (h.kind == Hit::LevelUp) {
                        if (now() >= arm_until) {
                            arm_until = now() + 4;
                            say("LEVEL UP armed 4s — click again to raise minor (Pilot cannot do this)");
                        } else {
                            arm_until = 0;
                            setenv("ARCHY_OPERATOR", "1", 1);
                            st = slurp_cmd("ARCHY_OPERATOR=1 python3 \"" + db_py() + "\" --operator-level-up 2>/dev/null");
                            say("LEVEL UP  ->  " + jstr(st, "version", "?") + "   pilot suffix reset");
                        }
                    } else if (h.kind == Hit::Github) {
                        if (gh.pid > 0) {
                            say("github copy still running");
                        } else if (gh_armed && now() < send_until) {
                            if (!spawn_github(gh, 2, db_py())) say("could not start the send");
                            else {
                                gh_armed = false;
                                send_until = 0;
                                gh_lines = {"sending to github"};
                                say("sending to github");
                            }
                        } else {
                            gh_armed = false;
                            send_until = 0;
                            if (!spawn_github(gh, 1, db_py())) say("could not start the copy");
                            else {
                                gh_lines = {"copying the github folder and running the audit"};
                                say("copying and auditing");
                            }
                        }
                    }
                    redraw();
                    break;
                }
                bool hit = false;
                for (auto& h : hits) if (inside(h.r, e.xbutton.x, e.xbutton.y)) hit = true;
                if (!hit && inside(log_box, e.xbutton.x, e.xbutton.y) && !log_rows.empty()) {
                    mark_from_pixel(e.xbutton.x, e.xbutton.y);
                    mark_a_row = mark_b_row;
                    mark_a_col = mark_b_col;
                    marking = true;
                    XGrabPointer(dpy, win, True, ButtonReleaseMask | PointerMotionMask,
                                 GrabModeAsync, GrabModeAsync, None, cur_ibeam, CurrentTime);
                    redraw();
                }
            }
        }
        struct timespec sl{0, 40 * 1000 * 1000};
        nanosleep(&sl, nullptr);
    }
    XFreePixmap(dpy, buf);
    XFreeGC(dpy, gc);
    if (fs) XFreeFont(dpy, fs);
    XFreeCursor(dpy, cur_arrow);
    XFreeCursor(dpy, cur_hand);
    XFreeCursor(dpy, cur_ibeam);
    XDestroyWindow(dpy, win);
    XCloseDisplay(dpy);
    return 0;
}
