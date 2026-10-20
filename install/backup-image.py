#!/usr/bin/env python3
# Copyright 2026 Freiquell e.V., Fichtestraße 1-4, 08451 Crimmitschau, GERMANY www.rimbaud.io
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# dd the data disk (filesystem label "remember") into one tar.gz.
# The system disk is never opened. Nothing is written until you run create.
#
#   python3 install_new/backup-image.py status
#   python3 install_new/backup-image.py create /media/usb/USB
#   python3 install_new/backup-image.py selftest
#
# The archive holds one member, remember.img: the raw whole-disk bytes.
# Restore from the Mint menu "Restore data disk (latest image)", or:
#   tar -xOzf remember-YYYYMMDD-HHMMSS.tar.gz remember.img | dd of=/dev/TARGET bs=4M conv=fsync
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import tarfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(ROOT, "conf")
JOB = os.path.join(CONF, "backup_job.json")
STATUS = "/tmp/rimbaud-backup-status.json"
LABEL_DEFAULT = "remember"
MEMBER = "remember.img"
MARGIN = 8 * 1024 ** 3
OK_FS = {"ext4", "exfat", "ntfs", "btrfs", "xfs"}
STATE = {"unmounted": False, "mount": "", "part": "", "partial": "", "children": []}


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return "%d B" % int(n)
            return "%.1f %s" % (n, unit) if n < 10 else "%.0f %s" % (n, unit)
        n /= 1024.0
    return "%.0f TB" % n


def fresh_job():
    return {
        "phase": "idle",
        "pct": 0,
        "step": "",
        "error": "",
        "dest": "",
        "out": "",
        "bytes_read": 0,
        "bytes_total": 0,
        "bytes_out": 0,
        "pid": 0,
        "pgrp": 0,
        "log": [],
    }


def load_job():
    try:
        with open(JOB) as f:
            j = json.load(f)
        return j if isinstance(j, dict) else fresh_job()
    except (OSError, json.JSONDecodeError):
        return fresh_job()


def give_back(path, mode):
    try:
        os.chmod(path, mode)
    except OSError:
        pass
    uid = os.environ.get("SUDO_UID")
    gid = os.environ.get("SUDO_GID")
    if not uid or not gid:
        return
    try:
        os.chown(path, int(uid), int(gid))
    except (OSError, ValueError):
        pass


def save_job(job):
    if os.environ.get("RIMBAUD_BACKUP_SELFTEST"):
        return
    os.makedirs(CONF, exist_ok=True)
    tmp = JOB + ".tmp"
    with open(tmp, "w") as f:
        json.dump(job, f, indent=2)
        f.write("\n")
    os.replace(tmp, JOB)
    give_back(JOB, 0o664)


def log(job, msg):
    line = time.strftime("%H:%M:%S", time.gmtime()) + "  " + " ".join(str(msg).split())
    lines = list(job.get("log") or [])
    lines.append(line[:240])
    job["log"] = lines[-80:]
    save_job(job)
    print(line, flush=True)


def wanted_label():
    try:
        with open(os.path.join(CONF, "backup.json")) as f:
            j = json.load(f)
        if isinstance(j, dict) and j.get("label"):
            return str(j["label"])
    except (OSError, json.JSONDecodeError):
        pass
    return os.environ.get("RIMBAUD_BACKUP_LABEL") or LABEL_DEFAULT


def lsblk_tree():
    raw = subprocess.check_output(
        ["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINT,MODEL,TRAN,PKNAME"],
        text=True,
    )
    data = json.loads(raw)
    return data.get("blockdevices") or []


def walk(nodes, parent=None):
    out = []
    for node in nodes:
        node = dict(node)
        node["_parent"] = parent
        out.append(node)
        out.extend(walk(node.get("children") or [], node))
    return out


def node_path(node):
    path = node.get("path") or ""
    if path:
        return path
    name = node.get("name") or ""
    return ("/dev/" + name) if name else ""


def top_disk(node):
    cur = node
    while cur is not None and cur.get("type") != "disk":
        cur = cur.get("_parent")
    return cur


def root_source():
    try:
        src = subprocess.check_output(["findmnt", "-n", "-o", "SOURCE", "/"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        src = ""
    if src.startswith("/dev/"):
        return src.split()[0]
    return src


def inside(path, root):
    path = os.path.realpath(path)
    root = os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def discover(nodes=None, root_src=None, label=None):
    """Describe the data disk and places an image could be saved. No root, no dd."""
    label = label or wanted_label()
    if nodes is None:
        nodes = lsblk_tree()
    flat = walk(nodes)
    if root_src is None:
        root_src = root_source()
    root_node = None
    for node in flat:
        if node_path(node) == root_src:
            root_node = node
            break
    root_disk = top_disk(root_node) if root_node else None
    root_disk_path = node_path(root_disk) if root_disk else ""

    parts = [
        n for n in flat
        if n.get("type") == "part" and (n.get("label") or "") == label
    ]
    source = {"ok": False, "label": label}
    error = ""
    if len(parts) != 1:
        error = "no disk labeled %s" % label if not parts else "more than one disk labeled %s" % label
    else:
        part = parts[0]
        disk = top_disk(part)
        disk_path = node_path(disk) if disk else ""
        mount = part.get("mountpoint") or ""
        if not disk_path:
            error = "data disk has no parent device"
        elif root_disk_path and disk_path == root_disk_path:
            error = "refusing the system disk"
        elif not mount:
            error = "data disk %s is not mounted" % disk_path
        else:
            st = os.statvfs(mount)
            used = (st.f_blocks - st.f_bfree) * st.f_frsize
            source = {
                "ok": True,
                "label": label,
                "disk": disk_path,
                "part": node_path(part),
                "mount": mount,
                "model": (disk.get("model") or "").strip(),
                "uuid": part.get("uuid") or "",
                "fstype": part.get("fstype") or "",
                "size": int(disk.get("size") or 0),
                "size_h": human(int(disk.get("size") or 0)),
                "used": int(used),
                "used_h": human(used),
                "need": int(used) + MARGIN,
                "need_h": human(int(used) + MARGIN),
            }
    if error:
        source["ok"] = False
        source["error"] = error

    dests = []
    seen = set()
    try:
        mounts = open("/proc/mounts")
    except OSError:
        mounts = []
    for line in mounts:
        bits = line.split()
        if len(bits) < 3:
            continue
        spec, mnt, fs = bits[0], bits[1].replace("\\040", " "), bits[2]
        if fs not in OK_FS and fs != "vfat":
            continue
        if mnt in seen:
            continue
        seen.add(mnt)
        try:
            st = os.statvfs(mnt)
            free = st.f_bavail * st.f_frsize
        except OSError:
            continue
        note = ""
        ok = True
        if fs == "vfat":
            ok = False
            note = "FAT32 stops at 4 GB"
        if source.get("ok") and (mnt == source["mount"] or inside(mnt, source["mount"])):
            ok = False
            note = "this is the disk being copied"
        if inside(mnt, ROOT):
            ok = False
            note = "not inside the hummingbot tree"
        need = source.get("need") or 0
        if ok and need and free < need:
            ok = False
            note = "need %s free" % source.get("need_h", human(need))
        model = ""
        tran = ""
        lab = ""
        parent_path = ""
        for node in flat:
            if (node.get("mountpoint") or "") == mnt:
                lab = node.get("label") or ""
                parent = top_disk(node)
                if parent:
                    model = (parent.get("model") or "").strip()
                    tran = parent.get("tran") or ""
                    parent_path = node_path(parent)
                break
        if root_disk_path and parent_path == root_disk_path:
            ok = False
            note = "system disk is not used for this image"
        dests.append({
            "path": mnt,
            "label": lab,
            "fstype": fs,
            "source": spec,
            "free": int(free),
            "free_h": human(free),
            "model": model,
            "tran": tran,
            "ok": ok,
            "note": note,
        })
    dests.sort(key=lambda d: (not d["ok"], -(d["free"])))

    images = find_images([d["path"] for d in dests])
    return {
        "source": source,
        "root_disk": root_disk_path,
        "destinations": dests,
        "images": images,
        "zerofree": bool(shutil_which("zerofree")),
        "error": error,
    }


def shutil_which(name):
    from shutil import which
    return which(name)


def find_images(dirs):
    import re
    good = re.compile(r"^remember-\d{8}-\d{6}\.tar\.gz$")
    partial = re.compile(r"^remember-\d{8}-\d{6}\.tar\.gz\.partial$")
    found = []
    seen = set()
    extra = []
    for d in dirs:
        extra.append(d)
        extra.append(os.path.join(d, "rimbaud-images"))
    for d in extra:
        if not d or not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            path = os.path.join(d, name)
            if not os.path.isfile(path):
                continue
            kind = "image" if good.match(name) else ("partial" if partial.match(name) else "")
            if not kind:
                continue
            real = os.path.realpath(path)
            if real in seen:
                continue
            seen.add(real)
            try:
                st = os.stat(path)
            except OSError:
                continue
            found.append({
                "path": path,
                "name": name,
                "bytes": st.st_size,
                "size_h": human(st.st_size),
                "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                "ok": kind == "image",
                "note": "" if kind == "image" else "incomplete",
            })
    found.sort(key=lambda x: x["mtime"], reverse=True)
    return found[:8]


def write_status(doc):
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    os.replace(tmp, STATUS)


def become_root():
    ask = os.path.join(ROOT, "install_new", "askpass-zenity.sh")
    env = os.environ.copy()
    env["SUDO_ASKPASS"] = ask
    env.setdefault("DISPLAY", ":0")
    env["RIMBAUD_ASK_TITLE"] = "rimBaud backup"
    env["RIMBAUD_ASK_TEXT"] = (
        "Sudo password to read the data disk into a tar.gz image.\n"
        "The system disk is not written."
    )
    os.execvpe(
        "sudo",
        ["sudo", "-A", "-E", sys.executable, os.path.abspath(__file__), *sys.argv[1:]],
        env,
    )


def remount():
    if not (STATE["unmounted"] and STATE["mount"] and STATE["part"]):
        return True
    proc = subprocess.run(["mount", STATE["part"], STATE["mount"]], check=False)
    if proc.returncode == 0:
        STATE["unmounted"] = False
        return True
    return False


def cleanup():
    for proc in list(STATE["children"]):
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
    remount()
    partial = STATE["partial"]
    if partial and os.path.exists(partial):
        try:
            os.unlink(partial)
        except OSError:
            pass
        STATE["partial"] = ""


def on_stop(signum, _frame):
    job = load_job()
    job["phase"] = "error"
    job["error"] = "stopped"
    log(job, "stopped by signal %s" % signum)
    cleanup()
    sys.exit(1)


def ensure_zerofree(job):
    if shutil_which("zerofree"):
        log(job, "zerofree present")
        return
    log(job, "installing zerofree so empty space compresses")
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"

    def run(args, timeout):
        return subprocess.run(
            args, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout
        )

    try:
        got = run(["apt-get", "install", "-y", "zerofree"], 240)
        if got.returncode != 0:
            log(job, "apt-get update")
            run(["apt-get", "update"], 240)
            got = run(["apt-get", "install", "-y", "zerofree"], 240)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("apt install zerofree timed out") from exc
    if got.returncode != 0 or not shutil_which("zerofree"):
        tail = " ".join((got.stdout or "").split())[-240:]
        raise RuntimeError("could not install zerofree. sudo apt install zerofree. " + tail)
    log(job, "zerofree installed")


def stream_image(disk, size, partial, job):
    comp = "pigz" if shutil_which("pigz") else "gzip"
    out_f = open(partial, "wb")
    proc = subprocess.Popen(
        [comp, "-1", "-c"], stdin=subprocess.PIPE, stdout=out_f, stderr=subprocess.DEVNULL
    )
    STATE["children"].append(proc)
    hasher = hashlib.sha256()
    reader_n = {"n": 0, "last": 0.0}

    class Reader:
        def read(self, n):
            buf = fh.read(n)
            if not buf:
                return b""
            hasher.update(buf)
            reader_n["n"] += len(buf)
            now = time.monotonic()
            if now - reader_n["last"] >= 0.5 or reader_n["n"] >= size:
                reader_n["last"] = now
                pct = int(reader_n["n"] * 100 / size) if size else 0
                job["phase"] = "imaging"
                job["pct"] = min(pct, 99)
                job["bytes_read"] = reader_n["n"]
                job["bytes_total"] = size
                try:
                    job["bytes_out"] = os.path.getsize(partial)
                except OSError:
                    pass
                job["step"] = "%s of %s read, archive %s" % (
                    human(reader_n["n"]), human(size), human(job.get("bytes_out") or 0)
                )
                save_job(job)
            return buf

    tf = tarfile.open(fileobj=proc.stdin, mode="w|", format=tarfile.GNU_FORMAT)
    try:
        tf.copybufsize = 8 * 1024 * 1024
    except Exception:
        pass
    info = tarfile.TarInfo(name=MEMBER)
    info.size = size
    info.mtime = int(time.time())
    info.mode = 0o600
    info.uid = 0
    info.gid = 0
    info.type = tarfile.REGTYPE
    try:
        with open(disk, "rb", buffering=0) as fh:
            tf.addfile(info, Reader())
    finally:
        try:
            tf.close()
        except Exception:
            pass
        try:
            proc.stdin.close()
        except Exception:
            pass
    rc = proc.wait()
    out_f.close()
    if rc != 0:
        raise RuntimeError("%s failed (%s)" % (comp, rc))
    if reader_n["n"] != size:
        raise RuntimeError("short image %s of %s" % (reader_n["n"], size))
    return hasher.hexdigest(), comp


def run_create(dest, job):
    doc = discover()
    src = doc["source"]
    if not src.get("ok"):
        raise RuntimeError(src.get("error") or doc.get("error") or "no data disk")
    disk = src["disk"]
    part = src["part"]
    mount = src["mount"]
    root_disk = doc.get("root_disk") or ""
    if root_disk and os.path.realpath(disk) == os.path.realpath(root_disk):
        raise RuntimeError("refusing to image the system disk")
    if not os.path.isdir(dest):
        raise RuntimeError("destination is not a directory: " + dest)
    if inside(dest, mount) or inside(dest, ROOT):
        raise RuntimeError("pick a USB disk. The image cannot sit on the data disk or in the hummingbot tree")
    st = os.statvfs(dest)
    free = st.f_bavail * st.f_frsize
    if free < src["need"]:
        raise RuntimeError("need %s free on %s, have %s" % (src["need_h"], dest, human(free)))
    # Refuse a destination that is the source device even if the path check missed it.
    if os.stat(dest).st_dev == os.stat(mount).st_dev:
        raise RuntimeError("destination is on the data disk")

    ensure_zerofree(job)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    final = os.path.join(dest, "remember-%s.tar.gz" % stamp)
    partial = final + ".partial"
    STATE["partial"] = partial
    STATE["mount"] = mount
    STATE["part"] = part
    job["dest"] = dest
    job["out"] = final
    job["bytes_total"] = src["size"]
    log(job, "source %s %s used %s" % (disk, src.get("model") or "", src["used_h"]))
    log(job, "dest %s" % final)
    log(job, "unmount %s" % mount)
    subprocess.run(["sync"], check=False)
    um = subprocess.run(["umount", mount], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if um.returncode != 0:
        raise RuntimeError(
            "data disk is busy. Close files on %s and press Create again. %s"
            % (mount, " ".join((um.stdout or "").split())[-180:])
        )
    STATE["unmounted"] = True
    log(job, "zerofree %s" % part)
    job["phase"] = "preparing"
    job["step"] = "zerofree empty space on %s" % src["label"]
    save_job(job)
    zf = subprocess.run(
        ["zerofree", part], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=7200
    )
    if zf.returncode != 0:
        raise RuntimeError("zerofree failed: " + " ".join((zf.stdout or "").split())[-240:])
    log(job, "dd %s" % disk)
    digest, comp = stream_image(disk, src["size"], partial, job)
    os.chmod(partial, 0o640)
    os.replace(partial, final)
    STATE["partial"] = ""
    give_back(final, 0o640)
    subprocess.run(["sync", "-f", final], check=False)
    manifest = {
        "kind": "rimbaud-data-disk",
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hostname": socket.gethostname(),
        "member": MEMBER,
        "disk_size": src["size"],
        "fs_used": src["used"],
        "label": src["label"],
        "uuid": src.get("uuid") or "",
        "model": src.get("model") or "",
        "sha256": digest,
        "archive": os.path.basename(final),
        "archive_bytes": os.path.getsize(final),
        "compressor": comp,
        "restore": (
            "Boot the rimBaud Mint stick and choose Restore data disk (latest image). "
            "The tar.gz should sit on a partition labeled RIMBAUD-IMG. "
            "Or: tar -xOzf ARCHIVE remember.img | dd of=/dev/TARGET_DISK bs=4M conv=fsync"
        ),
    }
    man_path = os.path.join(dest, "remember-%s.json" % stamp)
    with open(man_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    give_back(man_path, 0o640)
    job["phase"] = "done"
    job["pct"] = 100
    job["error"] = ""
    job["step"] = "%s  %s" % (os.path.basename(final), human(manifest["archive_bytes"]))
    job["sha256"] = digest
    log(job, "ready %s" % job["step"])
    log(job, "restore from the Mint menu Restore data disk, or dd the member remember.img")


def create(dest):
    dest = os.path.abspath(dest)
    if os.geteuid() != 0:
        job = fresh_job()
        job["phase"] = "preparing"
        job["step"] = "waiting for the sudo password"
        job["dest"] = dest
        save_job(job)
        become_root()
        return
    signal.signal(signal.SIGTERM, on_stop)
    signal.signal(signal.SIGINT, on_stop)
    try:
        os.nice(10)
    except OSError:
        pass
    job = load_job()
    job["phase"] = "preparing"
    job["error"] = ""
    job["pid"] = os.getpid()
    job["pgrp"] = os.getpgrp()
    job["step"] = "reading the data disk"
    save_job(job)
    try:
        run_create(dest, job)
    except Exception as exc:
        cleanup()
        job = load_job()
        job["phase"] = "error"
        job["error"] = str(exc)[:400]
        if STATE["unmounted"]:
            job["error"] += " Data disk is still unmounted."
        log(job, "FAIL " + job["error"])
        sys.exit(1)
    finally:
        if not remount() and job.get("phase") == "done":
            log(job, "image is written, but the data disk did not mount again")


def status():
    doc = discover()
    write_status(doc)
    src = doc["source"]
    if src.get("ok"):
        print("data disk %s %s used %s" % (src["disk"], src.get("model") or "", src["used_h"]))
    else:
        print(doc.get("error") or "no data disk")
    print("destinations %d" % len(doc["destinations"]))
    print("images %d" % len(doc["images"]))


def selftest():
    os.environ["RIMBAUD_BACKUP_SELFTEST"] = "1"
    nodes = [
        {
            "name": "nvme0n1",
            "path": "/dev/nvme0n1",
            "type": "disk",
            "size": 100,
            "tran": "nvme",
            "model": "SAMSUNG",
            "mountpoint": None,
            "children": [
                {
                    "name": "nvme0n1p2",
                    "path": "/dev/nvme0n1p2",
                    "type": "part",
                    "mountpoint": "/",
                    "label": None,
                    "size": 100,
                    "fstype": "ext4",
                }
            ],
        },
        {
            "name": "nvme1n1",
            "path": "/dev/nvme1n1",
            "type": "disk",
            "size": 512110190592,
            "tran": "nvme",
            "model": "WD PC SN810",
            "mountpoint": None,
            "children": [
                {
                    "name": "nvme1n1p1",
                    "path": "/dev/nvme1n1p1",
                    "type": "part",
                    "label": "remember",
                    "mountpoint": "/tmp",
                    "fstype": "ext4",
                    "uuid": "abc",
                    "size": 512109820928,
                }
            ],
        },
    ]
    # /tmp is a real mount so statvfs works. The fixture disk path is not opened.
    doc = discover(nodes, root_src="/dev/nvme0n1p2", label="remember")
    assert doc["source"]["ok"], doc
    assert doc["source"]["disk"] == "/dev/nvme1n1"
    assert doc["root_disk"] == "/dev/nvme0n1"
    assert doc["source"]["disk"] != doc["root_disk"]
    bad_nodes = [
        {
            "name": "nvme0n1",
            "path": "/dev/nvme0n1",
            "type": "disk",
            "size": 100,
            "mountpoint": None,
            "children": [
                {
                    "name": "nvme0n1p1",
                    "path": "/dev/nvme0n1p1",
                    "type": "part",
                    "label": "remember",
                    "mountpoint": "/",
                    "size": 100,
                }
            ],
        }
    ]
    refused = discover(bad_nodes, root_src="/dev/nvme0n1p1", label="remember")
    assert refused["source"]["ok"] is False
    assert "system" in refused["error"]
    assert inside("/tmp/rimbaud-images", "/tmp")
    assert not inside("/tmp", ROOT)

    info = tarfile.TarInfo(MEMBER)
    info.size = 512110190592
    buf = info.tobuf(tarfile.GNU_FORMAT, "utf-8", "surrogateescape")
    back = tarfile.TarInfo.frombuf(buf, "utf-8", "surrogateescape")
    assert back.name == MEMBER
    assert back.size == 512110190592

    import tempfile
    blob = b"RIMBAUD" + os.urandom(4096) + b"\0" * 10000
    src_path = os.path.join(tempfile.mkdtemp(prefix="rimbaud-bak-"), "src.bin")
    with open(src_path, "wb") as f:
        f.write(blob)
    out_path = src_path + ".tar.gz"
    job = fresh_job()

    class FH:
        def __init__(self):
            self.f = open(src_path, "rb")

        def read(self, n):
            return self.f.read(n)

    # stream_image opens the disk path itself, so round-trip through a real file
    # by calling it with the temp file. That path is not a system disk.
    digest, comp = stream_image(src_path, len(blob), out_path + ".partial", job)
    os.replace(out_path + ".partial", out_path)
    listed = subprocess.check_output(["tar", "-tzf", out_path], text=True).strip().splitlines()
    assert listed == [MEMBER], listed
    restored = subprocess.check_output(["tar", "-xOzf", out_path, MEMBER])
    assert restored == blob
    assert hashlib.sha256(blob).hexdigest() == digest
    assert comp in ("pigz", "gzip")
    os.unlink(out_path)
    os.unlink(src_path)

    live = discover()
    if live["source"].get("ok"):
        assert live["source"]["disk"] != live["root_disk"]
        assert live["source"]["label"] == wanted_label()
        assert not live["source"]["disk"].endswith(os.path.basename(live["root_disk"] or "NO-ROOT"))
    elif live.get("error") and "system" in live["error"]:
        raise SystemExit("selftest live discover selected the system disk")
    print("selftest ok")


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "selftest":
        selftest()
    elif cmd == "status":
        status()
    elif cmd == "create":
        if len(argv) != 3:
            sys.exit("usage: backup-image.py create DEST_DIR")
        create(argv[2])
    else:
        sys.exit("commands: status | create DEST_DIR | selftest")


if __name__ == "__main__":
    main(sys.argv)
