#!/usr/bin/env python3
# Copyright 2026 Laurence Sterne www.rimbaud.io
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

"""LAN enroll: a new matrix finds the meta hub and joins 10.66.80.0/24.

The client makes its own WireGuard key. The hub never sees that private key.
AllowedIPs stays 10.66.80.0/24 so hunters keep the new box's public IP.

  python3 enroll.py listen --peers ... --meta ... --hub-conf ... --etc-conf ... --vpn-dir ... --proxy ...
  python3 enroll.py enroll [--retry-bin /path/rimbaud-vpn]
  curl -fsS http://<hub-lan>:51822/enroll.py | sudo python3 - enroll
"""
from __future__ import annotations

import json
import os
import re
import select
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

UDP_PORT = 51821
HTTP_PORT = 51822
NET = "10.66.80"
IFACE = "wg-rimbaud"
ALLOWED = NET + ".0/24"
PUB_RE = re.compile(r"^[A-Za-z0-9+/]{43}=$")
NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sh(args, timeout=20, inp=None):
    if os.environ.get("RIMBAUD_ENROLL_NO_WG") == "1" and args and args[0] in ("wg", "wg-quick", "systemctl", "ufw"):
        print("DRY", " ".join(args))
        if args[:2] == ["wg", "genkey"]:
            return subprocess.CompletedProcess(args, 0, "dryprivdryprivdryprivdryprivdryprivdry=\n", "")
        if args[:2] == ["wg", "pubkey"]:
            return subprocess.CompletedProcess(args, 0, "drypubdrypubdrypubdrypubdrypubdrypubdrypubdry=\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")
    try:
        return subprocess.run(
            args, input=inp, text=True, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(args, 1, "", str(exc))


def valid_pub(pub):
    return isinstance(pub, str) and PUB_RE.match(pub) is not None


def lan_ok(ip):
    parts = str(ip).split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(x) for x in parts]
    except ValueError:
        return False
    return nums[0] == 192 and nums[1] == 168 and all(0 <= n <= 255 for n in nums)


def vpn_ok(ip):
    parts = str(ip).split(".")
    if len(parts) != 4 or ".".join(parts[:3]) != NET:
        return False
    try:
        n = int(parts[3])
    except ValueError:
        return False
    return 2 <= n <= 254


def clean_name(name):
    name = NAME_RE.sub("", str(name or ""))[:24]
    if name.lower() in ("", "local", "hub", "localhost"):
        return "matrix"
    return name


def read_json(path, fallback):
    try:
        with open(path) as f:
            j = json.load(f)
        return j if isinstance(j, dict) else fallback
    except (OSError, json.JSONDecodeError):
        return fallback


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    st = None
    try:
        st = os.stat(path)
    except OSError:
        st = None
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    # Keep a desk-owned meta.json writable after a root enroll rewrite.
    if st is not None:
        try:
            os.chown(path, st.st_uid, st.st_gid)
            os.chmod(path, st.st_mode & 0o777)
        except OSError:
            pass


def iface_addr():
    r = sh(["ip", "-4", "-o", "addr", "show", "dev", IFACE])
    for tok in (r.stdout or "").split():
        ip = tok.split("/")[0]
        if ip.startswith(NET + "."):
            return ip
    return ""


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 9))
        ip = s.getsockname()[0]
        if lan_ok(ip):
            return ip
    except OSError:
        pass
    finally:
        s.close()
    r = sh(["ip", "-4", "route", "get", "1.1.1.1"])
    parts = (r.stdout or "").split()
    if "src" in parts:
        ip = parts[parts.index("src") + 1]
        if lan_ok(ip):
            return ip
    return ""


def alloc_ip(peers):
    used = set()
    for p in peers.get("peers") or []:
        ip = str(p.get("vpn_ip") or "")
        if vpn_ok(ip):
            used.add(int(ip.split(".")[-1]))
    n = int(peers.get("next") or 2)
    if n < 2:
        n = 2
    while n in used:
        n += 1
    if n > 254:
        raise RuntimeError("VPN full")
    return n


def child_on_lan(meta_path, src_ip):
    j = read_json(meta_path, {})
    for s in j.get("sites") or []:
        if not isinstance(s, dict):
            continue
        if s.get("id") in ("local", "hub"):
            continue
        if s.get("lan") == src_ip or s.get("host") == src_ip:
            return str(s.get("id") or ""), str(s.get("role") or "vpn"), str(s.get("name") or s.get("id") or "")
    return "", "", ""


def upsert_meta(meta_path, sid, name, host, lan, role="vpn"):
    j = read_json(meta_path, {})
    j["ok"] = True
    j["read_only"] = True
    j["closes"] = False
    j["positions_untouched"] = True
    j["lan"] = j.get("lan") or "192.168.0.0/16"
    j["vpn_net"] = ALLOWED
    j["note"] = "GET only. VPN is 10.66.80.0/24 control plane — hunters keep their own public IP (HL tokens)."
    sites = j.get("sites") if isinstance(j.get("sites"), list) else []
    found = False
    for s in sites:
        if not isinstance(s, dict):
            continue
        same = s.get("id") == sid or (lan and (s.get("lan") == lan or s.get("host") == lan))
        if not same:
            continue
        if s.get("role") == "child":
            role = "child"
            sid = str(s.get("id") or sid)
        s["id"] = sid
        s["name"] = name
        s["host"] = host
        s["port"] = 5004
        s["role"] = role
        if lan:
            s["lan"] = lan
        found = True
        break
    if not found:
        row = {"id": sid, "name": name, "host": host, "port": 5004, "role": role}
        if lan:
            row["lan"] = lan
        sites.append(row)
    j["sites"] = sites
    write_json(meta_path, j)


def append_peer_conf(path, name, pub, ip):
    try:
        with open(path) as f:
            cur = f.read()
    except OSError:
        cur = ""
    if pub in cur:
        return
    block = "\n[Peer]\n# %s\nPublicKey = %s\nAllowedIPs = %s/32\n" % (name, pub, ip)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(block)


def apply_wg(pub, ip):
    r = sh(["wg", "set", IFACE, "peer", pub, "allowed-ips", ip + "/32"])
    if r.returncode != 0:
        sh(["wg-quick", "up", IFACE], timeout=30)
        sh(["wg", "set", IFACE, "peer", pub, "allowed-ips", ip + "/32"])
    # Keep the tunnel across reboot. A bare wg-quick up leaves the unit disabled.
    sh(["systemctl", "enable", "wg-quick@%s" % IFACE])


def accept_hello(paths, msg, src_ip):
    if not lan_ok(src_ip):
        raise ValueError("not a LAN address")
    pub = msg.get("pub") or ""
    if not valid_pub(pub):
        raise ValueError("bad pubkey")
    peers = read_json(paths["peers"], {"next": 2, "peers": []})
    peers.setdefault("peers", [])
    for p in peers["peers"]:
        if p.get("public_key") == pub:
            ip = p.get("vpn_ip")
            sid = p.get("id") or "matrix"
            name = p.get("name") or sid
            if src_ip and p.get("lan") != src_ip:
                p["lan"] = src_ip
                name = "%s %s" % (sid, src_ip)
                p["name"] = name
                write_json(paths["peers"], peers)
                upsert_meta(paths["meta"], sid, name, ip, src_ip, "child" if child_on_lan(paths["meta"], src_ip)[1] == "child" else "vpn")
            return reply_for(peers, sid, ip, msg.get("nonce"))
    prefer, role, prefer_name = child_on_lan(paths["meta"], src_ip)
    taken_ids = {str(p.get("id")) for p in peers["peers"]}
    clash = next((p for p in peers["peers"] if p.get("id") == prefer), None) if prefer else None
    if clash and clash.get("public_key") != pub:
        prefer = ""
    if prefer and prefer not in taken_ids:
        sid = prefer
        name = prefer_name or sid
    else:
        base = clean_name(msg.get("name"))
        sid = base
        n = 2
        while sid in taken_ids or sid in ("local", "hub"):
            sid = "%s-%d" % (base, n)
            n += 1
        name = "%s %s" % (sid, src_ip)
        role = "vpn"
    if role != "child":
        role = "vpn"
    host = alloc_ip(peers)
    ip = "%s.%d" % (NET, host)
    if not name:
        name = "%s %s" % (sid, src_ip)
    peers["peers"].append(
        {"id": sid, "name": name, "vpn_ip": ip, "public_key": pub, "port": 5004, "lan": src_ip}
    )
    peers["next"] = host + 1
    write_json(paths["peers"], peers)
    append_peer_conf(paths["hub_conf"], sid, pub, ip)
    append_peer_conf(paths["etc_conf"], sid, pub, ip)
    apply_wg(pub, ip)
    upsert_meta(paths["meta"], sid, name, ip, src_ip, role)
    print("enrolled %s %s lan %s" % (sid, ip, src_ip), flush=True)
    return reply_for(peers, sid, ip, msg.get("nonce"))


def reply_for(peers, sid, ip, nonce):
    hub_pub = peers.get("public_key") or ""
    endpoint = peers.get("endpoint") or ""
    if not valid_pub(hub_pub) or ":" not in str(endpoint):
        raise RuntimeError("hub pubkey or endpoint missing — run rimbaud-vpn init")
    return {
        "v": 1,
        "op": "join",
        "id": sid,
        "vpn_ip": ip,
        "hub_pub": hub_pub,
        "endpoint": endpoint,
        "allowed": ALLOWED,
        "nonce": nonce or "",
    }


def allow_firewall():
    if not os.path.exists("/usr/sbin/ufw") and not sh(["which", "ufw"]).stdout:
        return
    sh(["ufw", "allow", "51820/udp", "comment", "rimbaud-wireguard"])
    sh(["ufw", "allow", "51821/udp", "comment", "rimbaud-enroll"])
    sh(["ufw", "allow", "from", "192.168.0.0/16", "to", "any", "port", "51822", "proto", "tcp", "comment", "rimbaud-enroll-script"])


def http_response(path, body):
    data = body.encode()
    head = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/x-python; charset=utf-8\r\n"
        "Connection: close\r\n"
        "Content-Length: %d\r\n\r\n" % len(data)
    )
    return (head + body).encode()


def load_self():
    path = os.path.abspath(__file__)
    with open(path) as f:
        return f.read()


def serve_http(conn, script):
    try:
        conn.settimeout(3)
        buf = b""
        while b"\r\n" not in buf and len(buf) < 2048:
            chunk = conn.recv(1024)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b"\r\n", 1)[0].decode("latin1", "replace")
        parts = line.split(" ")
        target = parts[1] if len(parts) > 1 else "/"
        peer = conn.getpeername()[0]
        if not lan_ok(peer):
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            return
        if target.split("?", 1)[0] not in ("/enroll.py", "/enroll"):
            msg = "curl -fsS http://HUB:51822/enroll.py | sudo python3 - enroll\n"
            conn.sendall(http_response(target, msg))
            return
        conn.sendall(http_response(target, script))
    except OSError:
        return
    finally:
        try:
            conn.close()
        except OSError:
            pass


def cmd_listen(paths):
    allow_firewall()
    script = load_self()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp.bind(("0.0.0.0", UDP_PORT))
    tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp.bind(("0.0.0.0", HTTP_PORT))
    tcp.listen(4)
    tcp.setblocking(False)
    udp.setblocking(False)
    print("enroll listening udp %d  http %d" % (UDP_PORT, HTTP_PORT), flush=True)
    while True:
        r, _, _ = select.select([udp, tcp], [], [], 2.0)
        for sock in r:
            if sock is tcp:
                try:
                    conn, _addr = tcp.accept()
                except OSError:
                    continue
                serve_http(conn, script)
                continue
            try:
                data, addr = udp.recvfrom(2048)
            except OSError:
                continue
            try:
                msg = json.loads(data.decode())
            except (UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(msg, dict) or msg.get("op") != "hello" or msg.get("v") != 1:
                continue
            try:
                rep = accept_hello(paths, msg, addr[0])
            except Exception as exc:
                print("hello from %s rejected: %s" % (addr[0], exc), flush=True)
                continue
            try:
                udp.sendto(json.dumps(rep).encode(), addr)
            except OSError as exc:
                print("reply failed: %s" % exc, flush=True)


def install_retry(retry_bin, vpn_ip):
    if retry_bin and os.path.isfile(retry_bin):
        unit = "/etc/systemd/system/rimbaud-enroll.service"
        with open(unit, "w") as f:
            f.write(
                "[Unit]\n"
                "Description=rimBaud join meta hub on the LAN\n"
                "After=network-online.target\n"
                "Wants=network-online.target\n\n"
                "[Service]\n"
                "Type=oneshot\n"
                "ExecStart=%s enroll\n"
                "RemainAfterExit=yes\n\n"
                "[Install]\n"
                "WantedBy=multi-user.target\n" % retry_bin
            )
        sh(["systemctl", "daemon-reload"])
        sh(["systemctl", "enable", "rimbaud-enroll.service"])
    proxy = "/opt/rimbaud/rimbaud_vpn/spring-proxy.py"
    if vpn_ip and os.path.isfile(proxy):
        unit = "/etc/systemd/system/rimbaud-spring-proxy.service"
        with open(unit, "w") as f:
            f.write(
                "[Unit]\n"
                "Description=rimBaud Spring proxy on the VPN address\n"
                "After=wg-quick@wg-rimbaud.service\n\n"
                "[Service]\n"
                "ExecStart=/usr/bin/python3 %s %s 5004 127.0.0.1 5004\n"
                "Restart=on-failure\n"
                "RestartSec=2\n\n"
                "[Install]\n"
                "WantedBy=multi-user.target\n" % (proxy, vpn_ip)
            )
        sh(["systemctl", "daemon-reload"])
        sh(["systemctl", "enable", "--now", "rimbaud-spring-proxy.service"])
        return
    if not vpn_ip or not os.path.isfile(proxy):
        return


def start_proxy_now(vpn_ip):
    proxy = "/opt/rimbaud/rimbaud_vpn/spring-proxy.py"
    if not vpn_ip or not os.path.isfile(proxy):
        return
    r = sh(["pgrep", "-f", "spring-proxy.py"])
    if r.returncode == 0 and (r.stdout or "").strip():
        return
    log = open("/var/log/rimbaud-spring-proxy.log", "a")
    subprocess.Popen(
        ["python3", proxy, vpn_ip, "5004", "127.0.0.1", "5004"],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )


def cmd_enroll(retry_bin):
    if os.geteuid() != 0:
        print("need root: sudo python3 enroll.py enroll", file=sys.stderr)
        return 1
    addr = iface_addr()
    if addr == NET + ".1":
        print("this box is the hub")
        return 0
    if addr.startswith(NET + "."):
        print("already on VPN as %s" % addr)
        start_proxy_now(addr)
        install_retry(retry_bin, addr)
        return 0
    r = sh(["wg", "genkey"])
    priv = (r.stdout or "").strip()
    if r.returncode != 0 or not priv:
        print("wg genkey failed — install wireguard-tools", file=sys.stderr)
        return 1
    pub = (sh(["wg", "pubkey"], inp=priv + "\n").stdout or "").strip()
    if not valid_pub(pub):
        print("wg pubkey failed", file=sys.stderr)
        return 1
    me = lan_ip()
    nonce = os.urandom(8).hex()
    hello = json.dumps({
        "v": 1,
        "op": "hello",
        "name": socket.gethostname().split(".")[0],
        "pub": pub,
        "lan": me,
        "nonce": nonce,
    })
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", 0))
    sock.settimeout(2)
    targets = [("255.255.255.255", UDP_PORT)]
    if lan_ok(me):
        targets.append((".".join(me.split(".")[:3] + ["255"]), UDP_PORT))
    reply = None
    for _ in range(20):
        for dest in targets:
            try:
                sock.sendto(hello.encode(), dest)
            except OSError:
                pass
        try:
            data, _src = sock.recvfrom(2048)
            msg = json.loads(data.decode())
        except (socket.timeout, OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(msg, dict) or msg.get("op") != "join" or msg.get("nonce") != nonce:
            continue
        if msg.get("allowed") != ALLOWED or not vpn_ok(msg.get("vpn_ip")) or not valid_pub(msg.get("hub_pub")):
            print("rejected hub reply", file=sys.stderr)
            continue
        ep = str(msg.get("endpoint") or "")
        if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+:\d+", ep):
            print("rejected endpoint", file=sys.stderr)
            continue
        reply = msg
        break
    if not reply:
        print("no meta hub answered on the LAN (UDP %d)" % UDP_PORT, file=sys.stderr)
        return 1
    vpn_ip = reply["vpn_ip"]
    conf = (
        "[Interface]\n"
        "# rimBaud matrix — control plane only. Do NOT set AllowedIPs = 0.0.0.0/0\n"
        "Address = %s/24\n"
        "PrivateKey = %s\n\n"
        "[Peer]\n"
        "PublicKey = %s\n"
        "Endpoint = %s\n"
        "AllowedIPs = %s\n"
        "PersistentKeepalive = 25\n" % (vpn_ip, priv, reply["hub_pub"], reply["endpoint"], ALLOWED)
    )
    os.makedirs("/etc/wireguard", exist_ok=True)
    path = "/etc/wireguard/%s.conf" % IFACE
    with open(path, "w") as f:
        f.write(conf)
    os.chmod(path, 0o600)
    sh(["wg-quick", "down", IFACE], timeout=30)
    up = sh(["wg-quick", "up", IFACE], timeout=30)
    if up.returncode != 0:
        print(up.stderr or "wg-quick up failed", file=sys.stderr)
        return 1
    sh(["systemctl", "enable", "wg-quick@%s" % IFACE])
    sh(["ufw", "allow", "in", "on", IFACE, "to", "any", "port", "5004", "proto", "tcp", "comment", "rimbaud-meta-GET"])
    start_proxy_now(vpn_ip)
    install_retry(retry_bin, vpn_ip)
    print("joined %s as %s" % (reply.get("id"), vpn_ip))
    return 0


def port_open(ip, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        s.connect((ip, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def probe_lan(ip):
    r = sh(["ping", "-c", "1", "-W", "1", ip], timeout=3)
    if r.returncode != 0:
        return None
    return {"ip": ip, "ssh": port_open(ip, 22), "spring": port_open(ip, 5004)}


def local_lan_prefixes():
    r = sh(["ip", "-4", "-o", "addr", "show", "scope", "global"])
    found = []
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if "inet" not in parts:
            continue
        cidr = parts[parts.index("inet") + 1]
        if "/" not in cidr:
            continue
        ip = cidr.split("/")[0]
        if not lan_ok(ip):
            continue
        prefix = ".".join(ip.split(".")[:3])
        found.append((prefix, ip))
    return found


def cmd_scan(out_path):
    boxes = []
    with ThreadPoolExecutor(max_workers=64) as pool:
        futs = []
        for prefix, mine in local_lan_prefixes():
            for n in range(1, 255):
                ip = "%s.%d" % (prefix, n)
                if ip == mine:
                    continue
                futs.append(pool.submit(probe_lan, ip))
        for fut in futs:
            row = fut.result()
            if row:
                boxes.append(row)
    boxes.sort(key=lambda b: (not b["spring"], not b["ssh"], b["ip"]))
    write_json(out_path, {"ok": True, "boxes": boxes})
    print("lan boxes %d" % len(boxes))


def selftest():
    import tempfile
    d = tempfile.mkdtemp(prefix="enroll-")
    paths = {
        "peers": os.path.join(d, "peers.json"),
        "meta": os.path.join(d, "meta.json"),
        "hub_conf": os.path.join(d, "hub.conf"),
        "etc_conf": os.path.join(d, "etc.conf"),
    }
    write_json(paths["peers"], {
        "role": "hub",
        "public_key": "A" * 43 + "=",
        "endpoint": "192.168.1.10:51820",
        "next": 3,
        "peers": [{"id": "usb-stick", "vpn_ip": "10.66.80.2", "public_key": "B" * 43 + "="}],
    })
    write_json(paths["meta"], {"sites": [{"id": "local", "host": "127.0.0.1", "port": 5004, "role": "local"}]})
    os.environ["RIMBAUD_ENROLL_NO_WG"] = "1"
    pub = "C" * 43 + "="
    rep = accept_hello(paths, {"v": 1, "op": "hello", "name": "rimbaud", "pub": pub, "nonce": "n1"}, "192.168.1.138")
    assert rep["vpn_ip"] == "10.66.80.3", rep
    assert rep["allowed"] == ALLOWED
    again = accept_hello(paths, {"v": 1, "op": "hello", "name": "rimbaud", "pub": pub, "nonce": "n2"}, "192.168.1.138")
    assert again["vpn_ip"] == "10.66.80.3"
    meta = read_json(paths["meta"], {})
    ids = [s.get("id") for s in meta["sites"]]
    assert ids == ["local", "rimbaud"], ids
    assert meta["sites"][1]["lan"] == "192.168.1.138"
    assert meta["sites"][1]["host"] == "10.66.80.3"
    try:
        accept_hello(paths, {"v": 1, "op": "hello", "pub": "nope"}, "192.168.1.138")
        raise SystemExit("bad key was accepted")
    except ValueError:
        pass
    try:
        accept_hello(paths, {"v": 1, "op": "hello", "pub": pub}, "8.8.8.8")
        raise SystemExit("public IP was accepted")
    except ValueError:
        pass
    d2 = tempfile.mkdtemp(prefix="enroll-child-")
    paths2 = {
        "peers": os.path.join(d2, "peers.json"),
        "meta": os.path.join(d2, "meta.json"),
        "hub_conf": os.path.join(d2, "hub.conf"),
        "etc_conf": os.path.join(d2, "etc.conf"),
    }
    write_json(paths2["peers"], {
        "role": "hub",
        "public_key": "A" * 43 + "=",
        "endpoint": "192.168.1.10:51820",
        "next": 3,
        "peers": [{"id": "usb-stick", "vpn_ip": "10.66.80.2", "public_key": "B" * 43 + "="}],
    })
    write_json(paths2["meta"], {"sites": [
        {"id": "local", "host": "127.0.0.1", "port": 5004, "role": "local"},
        {"id": "child-138", "name": "child 192.168.1.138", "host": "192.168.1.138",
         "lan": "192.168.1.138", "port": 5004, "role": "child"},
    ]})
    pub2 = "D" * 43 + "="
    rep2 = accept_hello(paths2, {"v": 1, "op": "hello", "name": "rimbaud", "pub": pub2, "nonce": "c"}, "192.168.1.138")
    assert rep2["id"] == "child-138", rep2
    assert rep2["vpn_ip"] == "10.66.80.3"
    meta2 = read_json(paths2["meta"], {})
    child = [s for s in meta2["sites"] if s.get("id") == "child-138"][0]
    assert child["role"] == "child", child
    assert child["host"] == "10.66.80.3", child
    assert child["lan"] == "192.168.1.138"
    print("selftest ok", d)


def main(argv):
    def flag(name):
        if name not in argv:
            raise SystemExit("missing " + name)
        return argv[argv.index(name) + 1]

    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "selftest":
        selftest()
        return 0
    if cmd == "scan":
        cmd_scan(flag("--out"))
        return 0
    if cmd == "enroll":
        retry = ""
        if "--retry-bin" in argv:
            retry = argv[argv.index("--retry-bin") + 1]
        return cmd_enroll(retry)
    if cmd == "listen":
        paths = {
            "peers": flag("--peers"),
            "meta": flag("--meta"),
            "hub_conf": flag("--hub-conf"),
            "etc_conf": flag("--etc-conf"),
        }
        cmd_listen(paths)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv) or 0)
