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

"""VPN Spring proxy: VPN IP:5004 → 127.0.0.1:5004.

GET is always forwarded.

POST is allowed for desk BIAS / coin BIAS / 100% leftover open / per-clip CLOSE / WRITE MODE
/ hunter gate (docker pause or unpause only — positions stay, 0 HL IP while paused).
Other writes stay 403.
"""
from __future__ import annotations

import argparse
import select
import socket
import sys

_DENY_BODY = (
    b'{"ok":false,"closes":false,"positions_untouched":true,'
    b'"error":"vpn proxy blocked unknown POST - BIAS / 100% open / per-clip close / hunter gate are allowed"}'
)
DENY = (
    b"HTTP/1.1 403 Forbidden\r\n"
    b"Content-Type: application/json\r\n"
    b"Connection: close\r\n"
    + ("Content-Length: %d\r\n\r\n" % len(_DENY_BODY)).encode()
    + _DENY_BODY
)

ALLOWED_POST = (
    b"/api/home/desk_bias",
    b"/api/wire/bias",
    b"/api/home/open",
    b"/api/home/close",
    b"/api/spring/writes",
    b"/api/hunter_gate",
)


def _req_path(head_line: bytes) -> bytes:
    parts = head_line.split(b" ")
    if len(parts) < 2:
        return b""
    return parts[1].split(b"?", 1)[0]


def _content_length(header_blob: bytes) -> int:
    for line in header_blob.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            try:
                return max(0, int(line.split(b":", 1)[1].strip() or 0))
            except ValueError:
                return 0
    return 0


def splice(a: socket.socket, b: socket.socket) -> None:
    sockets = [a, b]
    try:
        while True:
            r, _, x = select.select(sockets, [], sockets, 30)
            if x or not r:
                break
            for s in r:
                other = b if s is a else a
                data = s.recv(65536)
                if not data:
                    return
                other.sendall(data)
    except OSError:
        return


def handle(client: socket.socket, dest_host: str, dest_port: int) -> None:
    client.settimeout(8)
    buf = b""
    try:
        while b"\r\n\r\n" not in buf and len(buf) < 65536:
            chunk = client.recv(4096)
            if not chunk:
                return
            buf += chunk
    except OSError:
        return
    head = buf.split(b"\r\n", 1)[0]
    method = head.split(b" ", 1)[0].upper()
    path = _req_path(head)
    if method not in (b"GET", b"HEAD", b"OPTIONS"):
        if method != b"POST" or path not in ALLOWED_POST:
            try:
                client.sendall(DENY)
            except OSError:
                pass
            return
        header, _, rest = buf.partition(b"\r\n\r\n")
        need = _content_length(header)
        body = rest
        try:
            while len(body) < need and len(buf) < 256 * 1024:
                chunk = client.recv(min(4096, need - len(body) or 4096))
                if not chunk:
                    break
                body += chunk
                buf += chunk
        except OSError:
            return
    try:
        up = socket.create_connection((dest_host, dest_port), timeout=4)
    except OSError:
        try:
            client.sendall(
                b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            )
        except OSError:
            pass
        return
    try:
        up.sendall(buf)
        splice(client, up)
    finally:
        try:
            up.close()
        except OSError:
            pass


def main() -> int:
    p = argparse.ArgumentParser(description="Spring VPN proxy (BIAS/open allowed, CLOSE blocked)")
    p.add_argument("bind_ip")
    p.add_argument("bind_port", type=int, nargs="?", default=5004)
    p.add_argument("dest_host", nargs="?", default="127.0.0.1")
    p.add_argument("dest_port", type=int, nargs="?", default=5004)
    args = p.parse_args()
    if args.bind_port == 5001 or args.dest_port == 5001:
        print("refusing port 5001 (Flask)", file=sys.stderr)
        return 2
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.bind_ip, args.bind_port))
    srv.listen(32)
    print(
        f"rimbaud-spring-proxy BIAS/open {args.bind_ip}:{args.bind_port} -> "
        f"{args.dest_host}:{args.dest_port}",
        flush=True,
    )
    while True:
        c, _ = srv.accept()
        try:
            handle(c, args.dest_host, args.dest_port)
        finally:
            try:
                c.close()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
