#!/usr/bin/env python3
# Copyright 2026 Laurence Sterne
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

# One line: state, RustDesk id, detail. No passwords.
# state is online, operating, down, or absent.
import glob
import os
import socket
import subprocess
import sys


def arg(i, default=""):
    return sys.argv[i] if len(sys.argv) > i and sys.argv[i] else default


HOST = arg(1, "127.0.0.1")
try:
    ID_PORT = int(arg(2, "21126"))
    RELAY_PORT = int(arg(3, "21127"))
except ValueError:
    ID_PORT, RELAY_PORT = 21126, 21127


def emit(state, rid, detail):
    rid = "".join(ch for ch in (rid or "") if ch.isdigit())
    detail = " ".join((detail or "").split())
    print("%s\t%s\t%s" % (state, rid, detail))
    return 0


def proc_up():
    proc = "/proc"
    try:
        names = os.listdir(proc)
    except OSError:
        return False
    for name in names:
        if not name.isdigit():
            continue
        try:
            raw = open(os.path.join(proc, name, "cmdline"), "rb").read()
        except OSError:
            continue
        cmd = raw.replace(b"\0", b" ").decode("utf-8", "replace")
        if "rustdesk" in cmd and ("--server" in cmd or "--service" in cmd):
            return True
    return False


def binary_present():
    return os.path.isfile("/usr/bin/rustdesk") or os.path.isfile("/usr/share/rustdesk/rustdesk")


def rustdesk_id():
    try:
        out = subprocess.check_output(
            ["rustdesk", "--get-id"], stderr=subprocess.DEVNULL, timeout=3
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return "".join(ch for ch in out.decode("utf-8", "replace") if ch.isdigit())


def port_open(port):
    s = socket.socket()
    s.settimeout(1.2)
    try:
        s.connect((HOST, int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def server_configured():
    paths = [
        os.path.expanduser("~/.config/rustdesk/RustDesk2.toml"),
        "/root/.config/rustdesk/RustDesk2.toml",
    ]
    paths.extend(glob.glob("/home/*/.config/rustdesk/RustDesk2.toml"))
    saw = False
    for path in paths:
        try:
            text = open(path, errors="replace").read()
        except OSError:
            continue
        saw = True
        if HOST in text:
            return True
    return None if not saw else False


def main():
    up = proc_up()
    if not up and not binary_present():
        return emit("absent", "", "not installed")
    rid = rustdesk_id()
    if not up:
        return emit("down", rid, "service down")
    cfg = server_configured()
    if cfg is False:
        return emit("operating", rid, "not on our server")
    ident = port_open(ID_PORT)
    relay = port_open(RELAY_PORT)
    if not rid:
        return emit("operating", "", "no id yet")
    if ident and relay:
        return emit("online", rid, "")
    if not ident:
        return emit("operating", rid, "id server unreachable")
    return emit("operating", rid, "relay unreachable")


if __name__ == "__main__":
    sys.exit(main())
