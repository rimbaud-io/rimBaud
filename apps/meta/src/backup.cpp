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

// Copy this hummingbot tree to a folder (the USB stick) as one tar.gz plus install.sh.
// Fresh Linux Mint then runs: bash install.sh
// The system disk is not imaged. Hunters are not stopped. Nothing is deleted on the stick.
#include <nlohmann/json.hpp>

// libsqlite3 is installed. The development header is not, so the calls used here are declared.
extern "C" {
struct sqlite3;
struct sqlite3_backup;
int sqlite3_open(const char*, sqlite3**);
int sqlite3_open_v2(const char*, sqlite3**, int, const char*);
int sqlite3_close(sqlite3*);
int sqlite3_exec(sqlite3*, const char*, int (*)(void*, int, char**, char**), void*, char**);
const char* sqlite3_errmsg(const sqlite3*);
sqlite3_backup* sqlite3_backup_init(sqlite3*, const char*, sqlite3*, const char*);
int sqlite3_backup_step(sqlite3_backup*, int);
int sqlite3_backup_finish(sqlite3_backup*);
}
enum { SQLITE_OK = 0, SQLITE_DONE = 101, SQLITE_OPEN_READONLY = 1, SQLITE_OPEN_READWRITE = 2, SQLITE_OPEN_CREATE = 4 };

#include <atomic>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fcntl.h>
#include <fstream>
#include <functional>
#include <iostream>
#include <signal.h>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

using json = nlohmann::json;
namespace fs = std::filesystem;

static const char* kTree = ".";
static std::atomic<int> g_stop{0};
static fs::path g_stage;
static fs::path g_partial;

static void on_stop(int) { g_stop.store(1); }

static std::string repo_root() {
    char buf[4096];
    ssize_t n = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
    if (n > 0) {
        buf[n] = 0;
        fs::path p = fs::path(buf).parent_path().parent_path();
        if (fs::exists(p / "docker-compose.yml")) return p.string();
    }
    return kTree;
}

static std::string conf_dir(const std::string& root) { return root + "/conf"; }

static std::string human(uint64_t n) {
    double v = static_cast<double>(n);
    const char* u = "B";
    if (v >= 1024) { v /= 1024; u = "KB"; }
    if (v >= 1024) { v /= 1024; u = "MB"; }
    if (v >= 1024) { v /= 1024; u = "GB"; }
    char b[32];
    snprintf(b, sizeof(b), v < 10 ? "%.1f %s" : "%.0f %s", v, u);
    return b;
}

static std::string stamp() {
    time_t t = time(nullptr);
    struct tm tm{};
    gmtime_r(&t, &tm);
    char b[32];
    strftime(b, sizeof(b), "%Y%m%d-%H%M%S", &tm);
    return b;
}

static std::string iso_now() {
    time_t t = time(nullptr);
    struct tm tm{};
    gmtime_r(&t, &tm);
    char b[40];
    strftime(b, sizeof(b), "%Y-%m-%dT%H:%M:%SZ", &tm);
    return b;
}

struct Job {
    json j;
    std::string path;
    explicit Job(std::string p) : path(std::move(p)) {
        j = json::object();
        j["phase"] = "preparing";
        j["pct"] = 0;
        j["step"] = "";
        j["error"] = "";
        j["log"] = json::array();
        j["pid"] = static_cast<int>(getpid());
        j["pgrp"] = static_cast<int>(getpgrp());
    }
    void save() {
        std::string tmp = path + ".tmp";
        std::ofstream out(tmp);
        if (!out) return;
        out << j.dump(2) << "\n";
        out.close();
        rename(tmp.c_str(), path.c_str());
    }
    void say(const std::string& s) {
        std::cerr << s << "\n";
        j["step"] = s;
        j["log"].push_back(s);
        if (j["log"].size() > 40) j["log"].erase(j["log"].begin());
        save();
    }
};

static bool ends_with(const std::string& s, const char* suf) {
    size_t n = std::strlen(suf);
    return s.size() >= n && s.compare(s.size() - n, n, suf) == 0;
}

static bool excluded_name(const std::string& name) {
    return name == "dist" || name == "doku" || name == "__pycache__" || name == ".rimbaud-backup-stage";
}

static bool is_sqlite(const std::string& name) {
    if (ends_with(name, "-wal") || ends_with(name, "-shm")) return false;
    return ends_with(name, ".sqlite") || ends_with(name, ".sqlite3") || ends_with(name, ".db");
}

static bool is_sqlite_sidecar(const std::string& name) {
    return ends_with(name, ".sqlite-wal") || ends_with(name, ".sqlite-shm") ||
           ends_with(name, ".sqlite3-wal") || ends_with(name, ".sqlite3-shm") ||
           ends_with(name, ".db-wal") || ends_with(name, ".db-shm");
}

static bool snapshot_sqlite(const fs::path& src, const fs::path& dst, std::string& err) {
    sqlite3* in = nullptr;
    sqlite3* out = nullptr;
    if (sqlite3_open_v2(src.c_str(), &in, SQLITE_OPEN_READONLY, nullptr) != SQLITE_OK) {
        err = in ? sqlite3_errmsg(in) : "open";
        if (in) sqlite3_close(in);
        return false;
    }
    if (sqlite3_open_v2(dst.c_str(), &out, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nullptr) != SQLITE_OK) {
        err = out ? sqlite3_errmsg(out) : "create";
        if (out) sqlite3_close(out);
        sqlite3_close(in);
        return false;
    }
    sqlite3_backup* b = sqlite3_backup_init(out, "main", in, "main");
    if (!b) {
        err = sqlite3_errmsg(out);
        sqlite3_close(out);
        sqlite3_close(in);
        return false;
    }
    int rc;
    do {
        rc = sqlite3_backup_step(b, 64);
    } while (rc == SQLITE_OK && !g_stop.load());
    sqlite3_backup_finish(b);
    sqlite3_close(out);
    sqlite3_close(in);
    if (rc != SQLITE_DONE) {
        err = "sqlite copy stopped";
        fs::remove(dst);
        return false;
    }
    return true;
}

static void cleanup() {
    std::error_code ec;
    if (!g_partial.empty()) fs::remove(g_partial, ec);
    if (!g_stage.empty()) fs::remove_all(g_stage, ec);
}

static std::string mount_source(const fs::path& path) {
    std::ifstream in("/proc/mounts");
    std::string best_src, line;
    size_t best = 0;
    std::string want = fs::weakly_canonical(path).string();
    while (std::getline(in, line)) {
        std::istringstream ls(line);
        std::string src, mnt, fsname;
        if (!(ls >> src >> mnt >> fsname)) continue;
        for (size_t i = 0; (i = mnt.find("\\040", i)) != std::string::npos; ) mnt.replace(i, 4, " ");
        if (want == mnt || (want.size() > mnt.size() && want.compare(0, mnt.size(), mnt) == 0 &&
                            (mnt == "/" || want[mnt.size()] == '/'))) {
            if (mnt.size() >= best) {
                best = mnt.size();
                best_src = src + " " + fsname;
            }
        }
    }
    return best_src;
}

static bool same_device(const fs::path& a, const fs::path& b) {
    struct stat sa{}, sb{};
    if (stat(a.c_str(), &sa) != 0 || stat(b.c_str(), &sb) != 0) return false;
    return sa.st_dev == sb.st_dev;
}

static uint64_t free_bytes(const fs::path& p) {
    struct statvfs v{};
    if (statvfs(p.c_str(), &v) != 0) return 0;
    return static_cast<uint64_t>(v.f_bavail) * static_cast<uint64_t>(v.f_frsize);
}

struct Staged {
    uint64_t bytes = 0;
    int files = 0;
    std::vector<std::string> locked;
};

static void stage_tree(const fs::path& root, const fs::path& rel, const fs::path& stage, Staged& st, Job& job) {
    fs::path dir = rel.empty() ? root : root / rel;
    std::error_code ec;
    for (fs::directory_iterator it(dir, ec), end; !ec && it != end; it.increment(ec)) {
        if (g_stop.load()) return;
        fs::directory_entry ent = *it;
        std::string name = ent.path().filename().string();
        if (excluded_name(name) || ends_with(name, ".pyc")) continue;
        fs::path r = rel.empty() ? fs::path(name) : rel / name;
        std::error_code sec;
        auto lst = ent.symlink_status(sec);
        if (sec) {
            st.locked.push_back(r.generic_string());
            continue;
        }
        fs::path dest = stage / r;
        if (access(ent.path().c_str(), R_OK) != 0) {
            st.locked.push_back(r.generic_string());
            continue;
        }
        if (fs::is_directory(lst)) {
            fs::create_directory(dest, sec);
            stage_tree(root, r, stage, st, job);
            continue;
        }
        if (fs::is_symlink(lst)) {
            fs::copy_symlink(ent.path(), dest, sec);
            if (!sec) st.files++;
            continue;
        }
        if (!fs::is_regular_file(lst)) continue;
        if (is_sqlite_sidecar(name)) continue;
        if (is_sqlite(name)) {
            std::string err;
            if (!snapshot_sqlite(ent.path(), dest, err)) {
                job.say("plain copy " + r.generic_string() + " (" + err + ")");
                fs::copy_file(ent.path(), dest, fs::copy_options::overwrite_existing, sec);
                if (sec) {
                    st.locked.push_back(r.generic_string());
                    continue;
                }
            }
            std::error_code sz;
            st.bytes += fs::file_size(dest, sz);
            st.files++;
            continue;
        }
        fs::create_hard_link(ent.path(), dest, sec);
        if (sec) {
            if (sec == std::errc::permission_denied) st.locked.push_back(r.generic_string());
            else job.say("skip " + r.generic_string() + " (" + sec.message() + ")");
            continue;
        }
        std::error_code sz;
        st.bytes += fs::file_size(ent.path(), sz);
        st.files++;
    }
    if (ec == std::errc::permission_denied) st.locked.push_back(rel.generic_string());
}

static bool sudo_copy_locked(const fs::path& root, const fs::path& stage, const std::vector<std::string>& locked, Job& job) {
    if (locked.empty()) return true;
    fs::path script = "/tmp/rimbaud-backup-root.sh";
    {
        std::ofstream out(script);
        if (!out) return false;
        out << "#!/bin/bash\nset -euo pipefail\n";
        for (auto& rel : locked) {
            fs::path src = root / rel;
            fs::path dst = stage / rel;
            std::error_code ec;
            fs::remove_all(dst, ec);
            fs::create_directories(dst.parent_path(), ec);
            out << "cp -a " << src << " " << dst << "\n";
            out << "chmod -R a+rX " << dst << "\n";
        }
    }
    chmod(script.c_str(), 0700);
    std::string ask = root.string() + "/install_new/askpass-zenity.sh";
    pid_t pid = fork();
    if (pid == 0) {
        if (fs::exists(ask)) {
            setenv("SUDO_ASKPASS", ask.c_str(), 1);
            setenv("RIMBAUD_ASK_TITLE", "rimBaud backup", 1);
            setenv("RIMBAUD_ASK_TEXT",
                   "Sudo password to read root-only VPN and Condor files into the USB copy.", 1);
            if (!getenv("DISPLAY")) setenv("DISPLAY", ":0", 1);
            execlp("sudo", "sudo", "-A", "bash", script.c_str(), (char*)nullptr);
        }
        execlp("sudo", "sudo", "bash", script.c_str(), (char*)nullptr);
        _exit(127);
    }
    int rc = 0;
    waitpid(pid, &rc, 0);
    fs::remove(script);
    if (!WIFEXITED(rc) || WEXITSTATUS(rc) != 0) {
        for (auto& rel : locked) job.say("skipped root-only " + rel);
        return false;
    }
    for (auto& rel : locked) job.say("added root-only " + rel);
    return true;
}

static int run_tar(const fs::path& stage, const fs::path& partial, uint64_t total, Job& job) {
    int errpipe[2];
    if (pipe(errpipe) != 0) return 1;
    pid_t pid = fork();
    if (pid < 0) return 1;
    if (pid == 0) {
        dup2(errpipe[1], 2);
        close(errpipe[0]);
        close(errpipe[1]);
        execlp("tar", "tar", "-C", stage.c_str(), "--blocking-factor=20", "--checkpoint=40",
               "--checkpoint-action=echo=CK %u", "--ignore-failed-read",
               "--use-compress-program=gzip -1", "-cf", partial.c_str(), ".",
               (char*)nullptr);
        _exit(127);
    }
    close(errpipe[1]);
    FILE* fp = fdopen(errpipe[0], "r");
    char line[512];
    const uint64_t tick = 40ull * 20ull * 512ull;
    while (fp && fgets(line, sizeof(line), fp)) {
        if (g_stop.load()) {
            kill(pid, SIGTERM);
            break;
        }
        if (std::strncmp(line, "CK ", 3) == 0) {
            uint64_t n = std::strtoull(line + 3, nullptr, 10);
            uint64_t done = n * tick;
            int pct = total ? static_cast<int>(done * 100 / total) : 0;
            if (pct > 99) pct = 99;
            job.j["phase"] = "copying";
            job.j["pct"] = pct;
            job.j["step"] = "packing " + human(done);
            struct stat st{};
            if (stat(partial.c_str(), &st) == 0 && st.st_size > 3900ll * 1024 * 1024) {
                job.say("archive passed 3.9 GB — FAT32 cannot store it");
                kill(pid, SIGTERM);
                g_stop.store(1);
            }
            job.save();
        }
    }
    if (fp) fclose(fp);
    int rc = 0;
    waitpid(pid, &rc, 0);
    if (g_stop.load()) return 2;
    if (!WIFEXITED(rc) || WEXITSTATUS(rc) != 0) return 1;
    return 0;
}

static bool copy_file_out(const fs::path& src, const fs::path& dst) {
    std::ifstream in(src, std::ios::binary);
    std::ofstream out(dst, std::ios::binary);
    if (!in || !out) return false;
    out << in.rdbuf();
    out.close();
    chmod(dst.c_str(), 0755);
    int fd = open(dst.c_str(), O_RDONLY);
    if (fd >= 0) { fsync(fd); close(fd); }
    return true;
}

static int do_copy(const std::string& root, const fs::path& dest) {
    Job job(conf_dir(root) + "/backup_job.json");
    signal(SIGTERM, on_stop);
    signal(SIGINT, on_stop);
    std::error_code ec;
    if (!fs::is_directory(dest, ec)) {
        job.j["phase"] = "error";
        job.say("folder is not there: " + dest.string());
        return 1;
    }
    fs::path canon = fs::weakly_canonical(dest, ec);
    fs::path rootc = fs::weakly_canonical(root, ec);
    auto inside = rootc.string();
    auto dests = canon.string();
    if (dests == inside || (dests.size() > inside.size() && dests.compare(0, inside.size(), inside) == 0 &&
                            dests[inside.size()] == '/')) {
        job.j["phase"] = "error";
        job.say("refusing to copy the project into itself");
        return 1;
    }
    if (same_device(rootc, canon)) {
        job.j["phase"] = "error";
        job.say("that folder is on the system disk /dev/nvme0n1p2");
        return 1;
    }
    uint64_t freeb = free_bytes(canon);
    if (freeb < 4ull * 1024 * 1024 * 1024) {
        job.j["phase"] = "error";
        job.say("need at least 4 GB free, " + human(freeb) + " is there");
        return 1;
    }
    fs::path stage = "/tmp/rimbaud-backup-stage";
    fs::remove_all(stage, ec);
    fs::create_directories(stage, ec);
    if (same_device(stage, rootc) == false) {
        fs::remove_all(stage, ec);
        stage = fs::path(root).parent_path() / ".rimbaud-backup-stage";
        fs::remove_all(stage, ec);
        fs::create_directories(stage, ec);
    }
    g_stage = stage;
    job.say("reading the hummingbot tree on /dev/nvme0n1p2");
    Staged st;
    stage_tree(root, {}, stage, st, job);
    if (g_stop.load()) {
        cleanup();
        job.j["phase"] = "error";
        job.say("stopped");
        return 2;
    }
    if (!st.locked.empty()) {
        job.say("reading root-only files (VPN, Condor)");
        sudo_copy_locked(root, stage, st.locked, job);
    }
    std::string id = stamp();
    fs::path partial = canon / ("rimbaud-" + id + ".tar.gz.partial");
    fs::path final = canon / ("rimbaud-" + id + ".tar.gz");
    g_partial = partial;
    job.j["phase"] = "copying";
    job.j["pct"] = 0;
    job.say("packing " + std::to_string(st.files) + " files, " + human(st.bytes));
    int tr = run_tar(stage, partial, st.bytes ? st.bytes : 1, job);
    if (tr != 0) {
        cleanup();
        job.j["phase"] = "error";
        job.j["error"] = tr == 2 ? "stopped" : "tar failed — archive was not kept";
        job.say(job.j["error"]);
        return tr;
    }
    fs::rename(partial, final, ec);
    g_partial.clear();
    if (ec) {
        cleanup();
        job.j["phase"] = "error";
        job.say("could not finish the archive on the stick");
        return 1;
    }
    int fd = open(final.c_str(), O_RDONLY);
    if (fd >= 0) { fsync(fd); close(fd); }
    uint64_t outsz = fs::file_size(final, ec);
    fs::path install = canon / "install.sh";
    if (!copy_file_out(fs::path(root) / "install.sh", install)) {
        job.say("archive is there, install.sh was not copied");
    }
    json side = {
        {"kind", "rimbaud-project"},
        {"created", iso_now()},
        {"source_dev", "/dev/nvme0n1p2"},
        {"tree", root},
        {"archive", final.filename().string()},
        {"archive_bytes", outsz},
        {"files", st.files},
        {"skipped", "dist doku __pycache__"},
        {"restore", "On fresh Linux Mint, open this folder and run: bash install.sh"},
    };
    std::ofstream man((canon / ("rimbaud-" + id + ".json")).string());
    if (man) man << side.dump(2) << "\n";
    fs::remove_all(stage, ec);
    g_stage.clear();
    job.j["phase"] = "done";
    job.j["pct"] = 100;
    job.j["error"] = "";
    job.say(final.filename().string() + "  " + human(outsz));
    job.say("install.sh is next to it. On fresh Mint: bash install.sh");
    return 0;
}

static std::string lsblk_json() {
    FILE* fp = popen("lsblk -J -b -o NAME,PATH,SIZE,FSTYPE,LABEL,MOUNTPOINT,MODEL,TRAN 2>/dev/null", "r");
    if (!fp) return "{}";
    std::string raw;
    char b[1024];
    while (fgets(b, sizeof(b), fp)) raw += b;
    pclose(fp);
    return raw.empty() ? "{}" : raw;
}

static void add_dest(json& dests, const std::string& path, const std::string& fsname, uint64_t freeb,
                     const std::string& model, bool vfat) {
    json d;
    d["path"] = path;
    d["fstype"] = fsname;
    d["free"] = freeb;
    d["free_h"] = human(freeb);
    d["model"] = model;
    d["ok"] = freeb >= 4ull * 1024 * 1024 * 1024;
    d["note"] = !d["ok"] ? "need 4 GB free" : (vfat ? "FAT32, file stays under 4 GB" : "");
    dests.push_back(d);
}

static int do_status(const std::string& root) {
    json st;
    st["source"] = {
        {"ok", true},
        {"disk", "/dev/nvme0n1p2"},
        {"mount", "/"},
        {"tree", root},
        {"label", "hummingbot project"},
        {"model", "this setup"},
        {"size_h", "project"},
        {"used_h", "about 2 GB"},
        {"note", "dist and doku are left out"},
    };
    st["root_disk"] = "/dev/nvme0n1p2";
    st["error"] = "";
    json dests = json::array();
    std::string suggested;
    json blk = json::parse(lsblk_json(), nullptr, false);
    if (blk.is_discarded()) blk = json::object();
    std::string root_src = mount_source("/");
    auto src_dev = root_src.substr(0, root_src.find(' '));
    if (blk.contains("blockdevices")) {
        std::function<void(const json&)> walk = [&](const json& node) {
            if (node.is_array()) {
                for (auto& c : node) walk(c);
                return;
            }
            if (!node.is_object()) return;
            auto strf = [](const json& n, const char* k) {
                return n.contains(k) && n[k].is_string() ? n[k].get<std::string>() : std::string();
            };
            std::string mnt = strf(node, "mountpoint");
            std::string path = strf(node, "path");
            std::string fsname = strf(node, "fstype");
            std::string model = strf(node, "model");
            if (model.empty()) model = strf(node, "label");
            std::string tran = strf(node, "tran");
            if (!mnt.empty() && mnt != "/" && path != src_dev &&
                (fsname == "vfat" || fsname == "exfat" || fsname == "ntfs" || fsname == "ext4" || fsname == "btrfs")) {
                bool vfat = fsname == "vfat";
                uint64_t freeb = free_bytes(mnt);
                if (mnt != "/boot/efi") add_dest(dests, mnt, fsname, freeb, model, vfat);
                if (suggested.empty() && tran == "usb" && freeb >= 8ull * 1024 * 1024 * 1024)
                    suggested = mnt;
            }
            if (node.contains("children")) walk(node["children"]);
        };
        walk(blk["blockdevices"]);
    }
    fs::path safe = "/media/usb/remember/rimbaud_safe";
    if (fs::is_directory(safe)) add_dest(dests, safe.string(), "ext4", free_bytes(safe), "remember", false);
    if (suggested.empty()) {
        for (auto& d : dests)
            if (d.value("ok", false) && d.value("fstype", "") == "vfat") suggested = d.value("path", "");
    }
    st["suggested"] = suggested;
    st["destinations"] = dests;
    json images = json::array();
    std::vector<fs::path> look;
    if (!suggested.empty()) look.push_back(suggested);
    look.push_back(safe);
    for (auto& dir : look) {
        std::error_code ec;
        if (!fs::is_directory(dir, ec)) continue;
        fs::path newest;
        for (auto& ent : fs::directory_iterator(dir, ec)) {
            auto n = ent.path().filename().string();
            if (n.size() > 7 && n.find("rimbaud-") == 0 && ends_with(n, ".tar.gz")) {
                if (newest.empty() || ent.last_write_time(ec) > fs::last_write_time(newest, ec)) newest = ent.path();
            }
        }
        if (!newest.empty()) {
            images.push_back({
                {"name", newest.filename().string()},
                {"path", newest.string()},
                {"size_h", human(fs::file_size(newest, ec))},
                {"install", fs::exists(dir / "install.sh") ? "install.sh" : ""},
            });
        }
    }
    st["images"] = images;
    std::ofstream out("/tmp/rimbaud-backup-status.json");
    if (out) out << st.dump(2) << "\n";
    std::cout << st.dump(2) << "\n";
    return 0;
}

static int selftest() {
    fs::path tmp = fs::temp_directory_path() / "rimbaud-backup-selftest";
    fs::remove_all(tmp);
    fs::create_directories(tmp / "src" / "conf");
    fs::create_directories(tmp / "dest");
    std::ofstream(tmp / "src" / "docker-compose.yml") << "services: {}\n";
    std::ofstream(tmp / "src" / "install.sh") << "#!/bin/bash\necho restore\n";
    std::ofstream(tmp / "src" / "keep.txt") << "hello-rimbaud\n";
    fs::create_directories(tmp / "src" / "dist");
    std::ofstream(tmp / "src" / "dist" / "skip.txt") << "nope\n";
    sqlite3* db = nullptr;
    fs::path dbp = tmp / "src" / "conf" / "matrix.db";
    if (sqlite3_open(dbp.c_str(), &db) != SQLITE_OK) return 1;
    char* err = nullptr;
    sqlite3_exec(db, "CREATE TABLE t(v); INSERT INTO t VALUES('wallet-row');", nullptr, nullptr, &err);
    sqlite3_close(db);
    // Stage + tar the tiny tree with the same filters, then read it back.
    fs::path stage = tmp / "stage";
    fs::create_directories(stage);
    Job job((tmp / "job.json").string());
    Staged st;
    stage_tree(tmp / "src", {}, stage, st, job);
    if (!fs::exists(stage / "keep.txt")) return 2;
    if (fs::exists(stage / "dist")) return 3;
    if (!fs::exists(stage / "conf" / "matrix.db")) return 4;
    sqlite3* check = nullptr;
    sqlite3_open_v2((stage / "conf" / "matrix.db").c_str(), &check, SQLITE_OPEN_READONLY, nullptr);
    char* got = nullptr;
    sqlite3_exec(check, "SELECT v FROM t",
                 [](void* p, int, char** col, char**) { *static_cast<char**>(p) = strdup(col[0]); return 0; }, &got,
                 &err);
    sqlite3_close(check);
    if (!got || std::string(got) != "wallet-row") return 5;
    free(got);
    fs::path arc = tmp / "dest" / "rimbaud-self.tar.gz";
    int tr = run_tar(stage, arc, st.bytes ? st.bytes : 1, job);
    if (tr != 0 || !fs::exists(arc)) return 6;
    fs::remove_all(tmp);
    std::cout << "selftest ok\n";
    return 0;
}

int main(int argc, char** argv) {
    std::string root = repo_root();
    std::string cmd = argc > 1 ? argv[1] : "status";
    if (cmd == "selftest") return selftest();
    if (cmd == "status") return do_status(root);
    if (cmd == "copy") {
        if (argc != 3) {
            std::cerr << "usage: rimbaud-backup copy DEST_DIR\n";
            return 2;
        }
        return do_copy(root, argv[2]);
    }
    std::cerr << "commands: status | copy DEST_DIR | selftest\n";
    return 2;
}
