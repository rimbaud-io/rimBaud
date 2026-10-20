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

# Live Mint app: write the newest data-disk image onto a disk you confirm.
#
# Boot line (added by install_new/mint-image/build-iso.sh on the next image build):
#   Restore data disk (latest image)
# which sets rimbaud-restore=1. A normal live boot and an installed system exit
# immediately. This does not run unless that flag is on the kernel command line,
# or you pass --force.
#
# The image is a tar.gz made by backup-image.py. Put it on the stick's free
# space in a partition labeled RIMBAUD-IMG (or anywhere under /media). The app
# picks the newest remember-YYYYMMDD-HHMMSS.tar.gz, then dd's member
# remember.img onto an internal disk. USB disks are not targets, so the stick
# that holds the backup is not overwritten.
import json
import os
import subprocess
import sys
import tarfile
import time

MEMBER = "remember.img"
LOG = "/tmp/rimbaud-restore.log"


def say(msg):
    line = time.strftime("%H:%M:%S") + "  " + " ".join(str(msg).split())
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def cmdline():
    try:
        return open("/proc/cmdline").read().split()
    except OSError:
        return []


def restore_requested(argv):
    if "--force" in argv:
        return True
    return "rimbaud-restore=1" in cmdline()


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            if unit == "B":
                return "%d B" % int(n)
            return "%.1f %s" % (n, unit) if n < 10 else "%.0f %s" % (n, unit)
        n /= 1024.0
    return "%.0f TB" % n


def lsblk():
    raw = subprocess.check_output(
        ["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINT,MODEL,TRAN"],
        text=True,
    )
    return json.loads(raw).get("blockdevices") or []


def node_path(node):
    return node.get("path") or (("/dev/" + node["name"]) if node.get("name") else "")


def mount_backup_parts(nodes):
    """Mount a RIMBAUD-IMG partition read-only so the newest archive can be found."""
    os.makedirs("/mnt/rimbaud-img", exist_ok=True)
    for disk in nodes:
        if disk.get("type") != "disk":
            continue
        for part in disk.get("children") or []:
            if (part.get("label") or "") != "RIMBAUD-IMG":
                continue
            if part.get("mountpoint"):
                continue
            dev = node_path(part)
            if not dev:
                continue
            subprocess.run(
                ["mount", "-o", "ro", dev, "/mnt/rimbaud-img"],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )


def find_images():
    import re
    good = re.compile(r"^remember-\d{8}-\d{6}\.tar\.gz$")
    roots = ["/mnt/rimbaud-img", "/media", "/mnt", "/run/media", "/cdrom"]
    try:
        for line in open("/proc/mounts"):
            bits = line.split()
            if len(bits) >= 2:
                roots.append(bits[1].replace("\\040", " "))
    except OSError:
        pass
    found = []
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        dirs = [root, os.path.join(root, "rimbaud-images")]
        for d in dirs:
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for name in names:
                if not good.match(name):
                    continue
                path = os.path.join(d, name)
                if not os.path.isfile(path):
                    continue
                real = os.path.realpath(path)
                if real in seen:
                    continue
                seen.add(real)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                found.append({"path": path, "name": name, "mtime": st.st_mtime, "bytes": st.st_size})
    found.sort(key=lambda x: x["mtime"], reverse=True)
    return found


def member_header(path):
    """Read the first tar member header. Does not scan the whole archive."""
    import gzip
    with gzip.open(path, "rb") as f:
        block = f.read(512)
    if len(block) < 512:
        raise RuntimeError("archive header is short")
    info = tarfile.TarInfo.frombuf(block, "utf-8", "surrogateescape")
    if info.name != MEMBER:
        raise RuntimeError("archive member is %s, want %s" % (info.name, MEMBER))
    return info.size


def disk_of_file(path, nodes):
    try:
        dev = os.stat(path).st_dev
    except OSError:
        return ""
    best = ""
    best_len = -1
    try:
        lines = open("/proc/mounts")
    except OSError:
        return ""
    for line in lines:
        bits = line.split()
        if len(bits) < 2:
            continue
        mnt = bits[1].replace("\\040", " ")
        try:
            if os.stat(mnt).st_dev != dev or len(mnt) <= best_len:
                continue
        except OSError:
            continue
        best = bits[0]
        best_len = len(mnt)
    if not best:
        return ""
    for disk in nodes:
        if node_path(disk) == best:
            return node_path(disk)
        for part in disk.get("children") or []:
            if node_path(part) == best:
                return node_path(disk)
    return ""


def looks_like_system(disk):
    for part in disk.get("children") or []:
        if (part.get("fstype") or "") == "vfat":
            return True
        label = (part.get("label") or "").upper()
        if label in ("EFI", "ESP", "BOOT", "SYSTEM"):
            return True
        if (part.get("mountpoint") or "") in ("/boot", "/boot/efi"):
            return True
    return False


def looks_like_data(disk):
    for part in disk.get("children") or []:
        if (part.get("label") or "") == "remember":
            return True
    return False


def candidates(nodes, image_path, need_size):
    hold = disk_of_file(image_path, nodes) if image_path else ""
    cdrom = ""
    try:
        src = subprocess.check_output(["findmnt", "-n", "-o", "SOURCE", "/cdrom"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        src = ""
    if src:
        cdrom = disk_of_file("/cdrom", nodes) if os.path.isdir("/cdrom") else ""
        if not cdrom:
            for disk in nodes:
                if node_path(disk) == src:
                    cdrom = node_path(disk)
                for part in disk.get("children") or []:
                    if node_path(part) == src:
                        cdrom = node_path(disk)
    out = []
    for disk in nodes:
        if disk.get("type") != "disk":
            continue
        path = node_path(disk)
        size = int(disk.get("size") or 0)
        tran = disk.get("tran") or ""
        model = (disk.get("model") or "").strip() or tran or "disk"
        why = ""
        if tran == "usb":
            why = "usb stick is not a target"
        elif path and path == hold:
            why = "this disk holds the backup"
        elif path and path == cdrom:
            why = "this is the live stick"
        elif size < need_size:
            why = "smaller than the image"
        system = looks_like_system(disk)
        data = looks_like_data(disk)
        what = "DATA remember" if data else ("SYSTEM" if system else "internal disk")
        out.append({
            "path": path,
            "size": size,
            "size_h": human(size),
            "model": model,
            "what": what,
            "system": system,
            "offer": not why,
            "why": why,
            "preselect": bool(data and not why),
        })
    return out


def zenity(args):
    if not os.environ.get("DISPLAY"):
        say("no DISPLAY: " + " ".join(args))
        return 1, ""
    proc = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, (proc.stdout or "").strip()


def sudo_prefix():
    if os.geteuid() == 0:
        return []
    if subprocess.run(["sudo", "-n", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        return ["sudo", "-n"]
    for ask in (
        "/opt/rimbaud/install_new/askpass-zenity.sh",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "install_new", "askpass-zenity.sh"),
    ):
        if os.path.isfile(ask):
            os.environ["SUDO_ASKPASS"] = ask
            os.environ["RIMBAUD_ASK_TITLE"] = "rimBaud restore"
            os.environ["RIMBAUD_ASK_TEXT"] = (
                "Sudo password to write the backup onto the disk you confirmed."
            )
            return ["sudo", "-A"]
    return ["sudo"]


def unmount_disk(disk):
    prefix = sudo_prefix()
    for part in disk.get("children") or []:
        mnt = part.get("mountpoint") or ""
        if not mnt or mnt == "/":
            continue
        proc = subprocess.run(prefix + ["umount", mnt], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if proc.returncode != 0:
            raise RuntimeError("could not unmount %s. %s" % (mnt, " ".join((proc.stdout or "").split())[-180:]))


def write_image(archive, target, total):
    prefix = sudo_prefix()
    tar = subprocess.Popen(["tar", "-xOzf", archive, MEMBER], stdout=subprocess.PIPE)
    dd = subprocess.Popen(
        prefix + ["dd", "of=" + target, "bs=4M", "conv=fsync", "status=progress"],
        stdin=tar.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    tar.stdout.close()
    zen = None
    if os.environ.get("DISPLAY"):
        zen = subprocess.Popen(
            ["zenity", "--progress", "--auto-close", "--no-cancel",
             "--title=rimBaud restore", "--width=480",
             "--text=Writing %s onto %s" % (os.path.basename(archive), target),
             "--percentage=0"],
            stdin=subprocess.PIPE, text=True,
        )
    copied = 0
    assert dd.stderr is not None
    for line in dd.stderr:
        say(line.strip())
        # dd: "1234 bytes (1.2 kB, 1.2 KiB) copied"
        head = line.strip().split()
        if head and head[0].isdigit():
            copied = int(head[0])
            if zen and total:
                pct = min(99, int(copied * 100 / total))
                try:
                    zen.stdin.write("%d\n" % pct)
                    zen.stdin.flush()
                except (OSError, BrokenPipeError):
                    zen = None
    rc_dd = dd.wait()
    rc_tar = tar.wait()
    if zen:
        try:
            zen.stdin.write("100\n")
            zen.stdin.close()
        except (OSError, BrokenPipeError):
            pass
        zen.wait()
    if rc_tar != 0 or rc_dd != 0:
        raise RuntimeError(
            "restore failed (tar %s, dd %s). %s is incomplete — run restore again before using it."
            % (rc_tar, rc_dd, target)
        )


def restore(argv):
    if not restore_requested(argv):
        return 0
    if "--dry-run" in argv:
        nodes = lsblk()
        mount_backup_parts(nodes)
        images = find_images()
        print(json.dumps({"images": images, "dry_run": True}, indent=2))
        return 0
    time.sleep(2)
    while True:
        nodes = lsblk()
        # mount needs root; ignore failure and let Retry cover a late plug-in
        if os.geteuid() == 0 or subprocess.run(
            ["sudo", "-n", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode == 0:
            mount_backup_parts(nodes)
        images = find_images()
        if images:
            break
        code, _ = zenity([
            "zenity", "--question", "--title=rimBaud restore", "--width=460",
            "--text=No remember-YYYYMMDD-HHMMSS.tar.gz found.\n"
                    "Plug in the backup disk, or a partition labeled RIMBAUD-IMG.",
            "--ok-label=Retry", "--cancel-label=Cancel",
        ])
        if code != 0:
            say("cancelled, no image")
            return 0
    image = images[0]
    try:
        need = member_header(image["path"])
    except Exception as exc:
        zenity(["zenity", "--error", "--title=rimBaud restore", "--text=" + str(exc)])
        return 1
    nodes = lsblk()
    rows = [c for c in candidates(nodes, image["path"], need) if c["offer"]]
    if not rows:
        zenity([
            "zenity", "--error", "--title=rimBaud restore", "--width=460",
            "--text=No internal disk is large enough for this image.\nUSB sticks are not targets.",
        ])
        return 1
    cmd = [
        "zenity", "--list", "--radiolist", "--title=rimBaud restore",
        "--width=760", "--height=360",
        "--text=Newest image: %s (%s).\nThis erases the internal disk you pick. The USB stick is not offered."
               % (image["name"], human(image["bytes"])),
        "--column=pick", "--column=disk", "--column=what", "--column=size", "--column=model",
    ]
    picked_default = False
    for row in rows:
        mark = "FALSE"
        if row["preselect"] and not picked_default:
            mark = "TRUE"
            picked_default = True
        cmd += [mark, row["path"], row["what"], row["size_h"], row["model"][:40]]
    code, choice = zenity(cmd)
    if code != 0 or not choice:
        say("cancelled at disk list")
        return 0
    chosen = next((r for r in rows if r["path"] == choice), None)
    if not chosen:
        zenity(["zenity", "--error", "--title=rimBaud restore", "--text=That disk is not in the list."])
        return 1
    warn = "Erase %s (%s, %s) and write %s onto it?" % (
        chosen["path"], chosen["what"], chosen["model"], image["name"]
    )
    if chosen["system"]:
        warn = "This looks like a SYSTEM disk (it has an EFI partition).\n" + warn
    code, _ = zenity([
        "zenity", "--question", "--title=rimBaud restore", "--width=480",
        "--text=" + warn, "--ok-label=Erase and write", "--cancel-label=Cancel",
    ])
    if code != 0:
        say("cancelled confirm")
        return 0
    disk_node = None
    for disk in nodes:
        if node_path(disk) == chosen["path"]:
            disk_node = disk
            break
    try:
        if disk_node:
            unmount_disk(disk_node)
        say("writing %s onto %s" % (image["path"], chosen["path"]))
        write_image(image["path"], chosen["path"], need)
    except Exception as exc:
        say("FAIL " + str(exc))
        zenity(["zenity", "--error", "--title=rimBaud restore", "--width=480", "--text=" + str(exc)])
        return 1
    say("restore finished %s" % chosen["path"])
    zenity([
        "zenity", "--info", "--title=rimBaud restore", "--width=420",
        "--text=Data disk image is on %s.\nYou can reboot when you want. This stick was not changed."
               % chosen["path"],
    ])
    return 0


def selftest():
    nodes = [
        {
            "name": "sda",
            "path": "/dev/sda",
            "type": "disk",
            "size": 1000 * 1024 ** 3,
            "tran": "usb",
            "model": "USB DISK",
            "children": [
                {"name": "sda1", "path": "/dev/sda1", "type": "part", "label": "RIMBAUD-IMG",
                 "fstype": "ext4", "mountpoint": "/media/usb", "size": 999 * 1024 ** 3}
            ],
        },
        {
            "name": "nvme0n1",
            "path": "/dev/nvme0n1",
            "type": "disk",
            "size": 512110190592,
            "tran": "nvme",
            "model": "SAMSUNG",
            "children": [
                {"name": "nvme0n1p1", "path": "/dev/nvme0n1p1", "type": "part", "fstype": "vfat",
                 "label": "EFI", "mountpoint": None, "size": 512 * 1024 ** 2},
                {"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "type": "part", "fstype": "ext4",
                 "mountpoint": None, "size": 500 * 1024 ** 3},
            ],
        },
        {
            "name": "nvme1n1",
            "path": "/dev/nvme1n1",
            "type": "disk",
            "size": 512110190592,
            "tran": "nvme",
            "model": "WD",
            "children": [
                {"name": "nvme1n1p1", "path": "/dev/nvme1n1p1", "type": "part", "fstype": "ext4",
                 "label": "remember", "mountpoint": None, "size": 512109820928}
            ],
        },
    ]
    rows = candidates(nodes, "/no/such/image.tar.gz", 512110190592)
    by = {r["path"]: r for r in rows}
    assert by["/dev/sda"]["offer"] is False
    assert "usb" in by["/dev/sda"]["why"]
    assert by["/dev/nvme0n1"]["offer"] is True
    assert by["/dev/nvme0n1"]["system"] is True
    assert by["/dev/nvme1n1"]["offer"] is True
    assert by["/dev/nvme1n1"]["preselect"] is True
    assert by["/dev/nvme1n1"]["what"].startswith("DATA")
    small = candidates(nodes, "/no/such/image.tar.gz", 900 * 1024 ** 4)
    assert all(not r["offer"] for r in small)
    info = tarfile.TarInfo(MEMBER)
    info.size = 512110190592
    block = info.tobuf(tarfile.GNU_FORMAT, "utf-8", "surrogateescape")
    back = tarfile.TarInfo.frombuf(block, "utf-8", "surrogateescape")
    assert back.name == MEMBER and back.size == 512110190592
    assert restore_requested(["--force"]) is True
    assert restore_requested(["backup-restore.py"]) is False or "rimbaud-restore=1" in cmdline()
    print("selftest ok")


def main(argv):
    if len(argv) > 1 and argv[1] == "selftest":
        selftest()
        return
    sys.exit(restore(argv))


if __name__ == "__main__":
    main(sys.argv)
