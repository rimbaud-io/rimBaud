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

"""Refresh the GitHub tree from the live rimBaud source and commit that version.

Archy --bump-g calls this and commits locally. It does not push.
The Archy window copies, audits, then asks for a second click before a push.
It refuses a remote that points at hummingbot/hummingbot.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys

LIVE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.environ.get("RIMBAUD_GITHUB") or os.path.expanduser("~/rimbaud")
PAGES = os.path.join(LIVE, "scripts", "github_pages")

SKIP_DIRS = {
    "node_modules", "dist", "__pycache__", ".git", "copy", "certs",
    "logs", "data", "third_party",
}
SKIP_EXT = {
    ".pyc", ".pyo", ".o", ".so", ".a", ".pdf", ".db", ".sqlite",
    ".wal", ".shm", ".zip", ".gz", ".deb", ".iso",
}
SKIP_NAMES = {
    "secrets.env", "hostname", "hosts", "chats.json", "wire_board.json",
}
HUNTERS = [
    "agenc_sol_trader.py", "bn_swing_trader.py", "bot_sl_override.py",
    "copy_digest.py", "ctrl_override.py", "dman_maker_v2_trader.py",
    "dman_trader.py", "ensure_autostart.sh", "helliot_trader.py",
    "hl_api_key.py", "hl_btc_trader.py", "hl_copy_trader.py",
    "hl_copy_trader_testnet.py", "hl_ip_budget.py", "hl_top_traders.py",
    "hl_trader_audit.py", "leverage_tier.py", "matrix_sql_write.py",
    "matrix_store.py", "onlyup_trader.py", "pmm_simple_trader.py",
    "risk_park.py", "shortn_trader.py", "sol_micro_trader.py",
    "solana_bb_trader.py", "surfn_trader.py", "watch_copy_trades.sh",
]
TREES = [
    ("freiquell_c++", "apps/matrix/desk"),
    ("dashboard", "apps/matrix/web"),
    ("freiquell_server_c++", "apps/spring"),
    ("rimbaud_meta_c++", "apps/meta"),
    ("pettibon_c++", "apps/pettibon"),
    ("rimbaud-archy", "apps/archy"),
    ("install_new", "install"),
    ("rimbaud_vpn", "vpn"),
    ("condor", "services/condor"),
    ("version", "services/version"),
    ("HL_wallet_creator", "tools/wallet-creator"),
    ("docker-windows", "docker-windows"),
]


def skip_file(name: str) -> bool:
    if name in SKIP_NAMES or name.startswith("."):
        return True
    return os.path.splitext(name)[1].lower() in SKIP_EXT


def is_binary(path: str) -> bool:
    with open(path, "rb") as fh:
        head = fh.read(8)
    return head.startswith(b"\x7fELF") or b"\0" in head


def redact(rel: str, text: str) -> str:
    if rel.endswith("mint-image/preseed/rimbaud.seed"):
        out = []
        for line in text.splitlines(keepends=True):
            if "plaintext:" in line and "passwd" in line:
                out.append("# Put the crypted install password here on the machine. First boot expires it.\n")
            elif line.startswith("d-i passwd/user-password-crypted password"):
                out.append("d-i passwd/user-password-crypted password CHANGE_ME\n")
            else:
                out.append(line)
        return "".join(out)
    if rel.endswith("mint-image/config.json") and "install_password_note" in text:
        start = text.find('"install_password_note"')
        end = text.find("\n", start)
        if start >= 0 and end > start:
            comma = "," if "," in text[start:end] else ""
            text = (
                text[:start]
                + '"install_password_note": "Set the install password on the machine. First boot forces a change. Not a live HL key."'
                + comma
                + text[end:]
            )
    return text


def copy_tree(stage: str, src_rel: str, dst_rel: str) -> None:
    src = os.path.join(LIVE, src_rel)
    dst = os.path.join(stage, dst_rel)
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, src)
        out_dir = dst if rel_dir == "." else os.path.join(dst, rel_dir)
        os.makedirs(out_dir, exist_ok=True)
        for name in filenames:
            if skip_file(name):
                continue
            sp = os.path.join(dirpath, name)
            if os.path.islink(sp) or not os.path.isfile(sp) or os.path.getsize(sp) > 3_000_000:
                continue
            if is_binary(sp):
                continue
            rel = os.path.normpath(os.path.join(dst_rel, os.path.relpath(sp, src)))
            dp = os.path.join(out_dir, name)
            ext = os.path.splitext(name)[1].lower()
            if ext in {".seed", ".json", ".py", ".sh", ".md", ".html", ".yml", ".yaml", ".txt", ".cpp", ".hpp", ".h", ".js", ".jsx", ".css", ".in"}:
                with open(sp, "r", encoding="utf-8") as fh:
                    body = redact(rel, fh.read())
                with open(dp, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(body)
            else:
                shutil.copy2(sp, dp)


def patch(path: str, old: str, new: str) -> None:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if old not in text:
        raise SystemExit(f"publish patch missed {path}")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.replace(old, new))


# Login names that are the product, the container, or a public install account.
_SKIP_NAMES = {
    "hummingbot", "root", "ubuntu", "mint", "user", "usb", "cdrom",
    "share", "rimbaud", "forklifter", "operator", "remote",
}
# A real home directory, not the /api/home/ route.
_HOME_USER = r"(?<![A-Za-z0-9._-])/home/(?!hummingbot\b)[A-Za-z0-9._-]+"
_ETH = re.compile(r"(?<![0-9a-fA-F])0x[a-fA-F0-9]{40}(?![0-9a-fA-F])")
_RESOLVERS = {b"1.1.1.1", b"1.0.0.1", b"8.8.8.8", b"8.8.4.4", b"9.9.9.9"}


def _operator_names(text: str) -> set[str]:
    found = set(re.findall(
        r"(?<![A-Za-z0-9._-])/home/(?!hummingbot\b)([A-Za-z0-9._-]+)",
        text,
    ))
    return {
        n for n in found
        if n.lower() not in _SKIP_NAMES and len(n) >= 3 and set(n) != {"."}
    }


def _blank_wallets(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        addr = match.group(0)
        if set(addr[2:].lower()) <= {"0"}:
            return addr
        return ""
    return _ETH.sub(repl, text)


def public_text(text: str, names: set[str]) -> str:
    """Stage-only. Live sources keep this machine's paths and wallets."""
    text = re.sub(
        r'or "/home/(?!hummingbot\b)[^/\s"]+/hummingbird/rimbaud"',
        'or os.path.expanduser("~/rimbaud")',
        text,
    )
    rules = [
        (_HOME_USER + r"/hummingbird/hummingbot/conf/hl_wallets", "conf/hl_wallets"),
        (_HOME_USER + r"/hummingbird/hummingbot/pettibon_data", "./pettibon_data"),
        (_HOME_USER + r"/hummingbird/hummingbot/logs", "logs"),
        (_HOME_USER + r"/hummingbird/hummingbot/iphone", "phone"),
        (_HOME_USER + r"/hummingbird/hummingbot/c\+\+", "/opt/freiquell/c++"),
        (_HOME_USER + r"/hummingbird/hummingbot/conf", "conf"),
        (_HOME_USER + r"/hummingbird/hummingbot", "."),
        (_HOME_USER + r"/\.config/pettibon/grafana\.env", "./grafana.env"),
        (_HOME_USER + r"/hummingbird/rimbaud", "~/rimbaud"),
        (r"/media/(?!usb(?![A-Za-z0-9._-]))(?!cdrom(?![A-Za-z0-9._-]))[A-Za-z0-9._-]+", "/media/usb"),
        (_HOME_USER, "."),
    ]
    for pat, rep in rules:
        text = re.sub(pat, rep, text)
    # This desk's public relay and LAN hosts. A scrubbed copy of this file
    # turns the two sides into the same placeholder, so the literals do not ship.
    text = text.replace("127.0.0.1", "127.0.0.1")
    text = text.replace("192.168.1.10", "192.168.1.10")
    text = text.replace("192.168.1.20", "192.168.1.20")
    text = text.replace(
        'if [ -n "${RIMBAUD_REMOTE_HOST:-}" ] && [ "$HOST" != "$RIMBAUD_REMOTE_HOST" ]; then echo "xx run on $RIMBAUD_REMOTE_HOST (this is $HOST)"; exit 1; fi',
        'if [ -n "${RIMBAUD_REMOTE_HOST:-}" ] && [ "$HOST" != "$RIMBAUD_REMOTE_HOST" ]; then echo "xx run on $RIMBAUD_REMOTE_HOST (this is $HOST)"; exit 1; fi',
    )
    text = text.replace("REMOTE", "REMOTE")
    text = text.replace("Remote", "Remote")
    text = text.replace("remote", "remote")
    text = text.replace("password set at install", "password set at install")
    text = _blank_wallets(text)
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(name)}\b", "operator", text)
        cap = name[:1].upper() + name[1:]
        if cap != name:
            text = re.sub(rf"\b{re.escape(cap)}\b", "Operator", text)
        up = name.upper()
        if up not in {name, cap}:
            text = re.sub(rf"\b{re.escape(up)}\b", "OPERATOR", text)
    return text


def _read_text(path: str) -> str | None:
    try:
        blob = open(path, "rb").read()
    except OSError:
        return None
    if b"\0" in blob or len(blob) > 5_000_000:
        return None
    try:
        return blob.decode("utf-8")
    except UnicodeError:
        return None


def sanitize_stage(stage: str) -> None:
    names: set[str] = set()
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if user and user.lower() not in _SKIP_NAMES:
        names.add(user)
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(stage):
        dirnames[:] = [d for d in dirnames if d != "third_party" and d != ".git"]
        for name in filenames:
            path = os.path.join(dirpath, name)
            files.append(path)
            text = _read_text(path)
            if text:
                names.update(_operator_names(text))
    names = {n for n in names if n.lower() not in _SKIP_NAMES and len(n) >= 3}
    for path in files:
        text = _read_text(path)
        if text is None:
            continue
        new = public_text(text, names)
        if new != text:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(new)
    for dirpath, dirnames, filenames in os.walk(stage, topdown=False):
        for name in filenames:
            new = name.replace("REMOTE", "REMOTE").replace("Remote", "Remote").replace("remote", "remote")
            for op in names:
                new = re.sub(rf"(?i)\b{re.escape(op)}\b", "operator", new)
            if new != name:
                src = os.path.join(dirpath, name)
                dst = os.path.join(dirpath, new)
                if os.path.exists(dst):
                    raise SystemExit(f"publish rename collision {dst}")
                os.rename(src, dst)


_EMAIL_OK = (
    b"rimbaud.io", b"example.com", b"example.org", b"apache.org",
    b"github.com", b"hummingbot.io",
)
_PUBLISH_SRC = {"rimbaud_publish.py", "rimbaud-publish.py"}


def _safe_rel(stage: str, path: str) -> str:
    rel = os.path.relpath(path, stage).replace("\\", "/")
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if user and user.lower() not in _SKIP_NAMES and len(user) >= 3:
        rel = re.sub(re.escape(user), "operator", rel, flags=re.IGNORECASE)
    if len(rel) > 90:
        rel = rel[:87] + "..."
    return rel


def _add_hit(hits: dict, kind: str, rel: str) -> None:
    key = (kind, rel)
    row = hits.get(key)
    if row is None:
        hits[key] = {"kind": kind, "file": rel, "n": 1}
    else:
        row["n"] += 1


def _public_ip(addr: bytes) -> bool:
    try:
        nums = [int(part) for part in addr.split(b".")]
    except ValueError:
        return False
    if len(nums) != 4 or any(n > 255 for n in nums):
        return False
    a, b = nums[0], nums[1]
    if a in (0, 127) or addr in _RESOLVERS or addr == b"255.255.255.255":
        return False
    if a == 10 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31):
        return False
    if a == 169 and b == 254:
        return False
    return True


def audit_tree(stage: str) -> list[dict]:
    """Personal information in a publish tree. The report has kind, file, and count only."""
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    name_b = user.encode() if user and user.lower() not in _SKIP_NAMES and len(user) >= 3 else b""
    eth = re.compile(br"(?<![0-9a-fA-F])0x(?!0{40}(?![0-9a-fA-F]))[0-9a-fA-F]{40}(?![0-9a-fA-F])")
    home = re.compile(br"(?<![A-Za-z0-9._-])/home/(?!hummingbot\b)[A-Za-z0-9._-]")
    media = re.compile(br"/media/(?!usb(?![A-Za-z0-9._-]))(?!cdrom(?![A-Za-z0-9._-]))[A-Za-z0-9._-]+")
    ip = re.compile(br"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    email = re.compile(br"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
    token = re.compile(br"(?:gho_|ghp_|github_pat_)[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}")
    telegram = re.compile(br"(?<![A-Za-z0-9])\d{8,10}:[A-Za-z0-9_-]{35,}")
    wg = re.compile(br"PrivateKey\s*=\s*[A-Za-z0-9+/]{40,}")
    hits: dict = {}
    for dirpath, dirnames, filenames in os.walk(stage):
        dirnames[:] = [d for d in dirnames if d != "third_party" and d != ".git"]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                blob = open(path, "rb").read()
            except OSError:
                continue
            if b"\0" in blob or len(blob) > 5_000_000:
                continue
            rel = _safe_rel(stage, path)
            src = name in _PUBLISH_SRC
            if home.search(blob):
                _add_hit(hits, "home", rel)
            if name_b and re.search(rb"(?i)\b" + re.escape(name_b) + rb"\b", blob):
                _add_hit(hits, "name", rel)
            if eth.search(blob):
                _add_hit(hits, "wallet", rel)
            if media.search(blob):
                _add_hit(hits, "media", rel)
            for match in ip.finditer(blob):
                if _public_ip(match.group(0)):
                    _add_hit(hits, "public_ip", rel)
                    break
            if not src:
                for match in email.finditer(blob):
                    domain = match.group(1).lower()
                    if domain.endswith((b".service", b".socket", b".target", b".timer", b".mount", b".device", b".path", b".slice", b".scope")):
                        continue
                    if match.start() > 0 and blob[match.start() - 1:match.start()] == b"/":
                        continue
                    if any(domain == ok or domain.endswith(b"." + ok) for ok in _EMAIL_OK):
                        continue
                    _add_hit(hits, "email", rel)
                    break
                if token.search(blob) or telegram.search(blob):
                    _add_hit(hits, "token", rel)
                if b"BEGIN PRIVATE" in blob or b"BEGIN OPENSSH PRIVATE" in blob or wg.search(blob):
                    _add_hit(hits, "private_key", rel)
                if b"user-password-crypted password $6$" in blob or b"plaintext: rimbaud" in blob:
                    _add_hit(hits, "secret", rel)
    rows = list(hits.values())
    rows.sort(key=lambda row: (row["kind"], row["file"]))
    return rows[:40]


def assert_public(stage: str) -> None:
    findings = audit_tree(stage)
    if findings:
        row = findings[0]
        raise SystemExit(f"publish refused, {row['kind']} in {row['file']}")


def build_stage(stage: str, version: str, summary: str) -> list[dict]:
    if os.path.isdir(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)
    for src, dst in TREES:
        copy_tree(stage, src, dst)
    os.makedirs(os.path.join(stage, "hunters"), exist_ok=True)
    for name in HUNTERS:
        shutil.copy2(os.path.join(LIVE, "scripts", name), os.path.join(stage, "hunters", name))
    shutil.copy2(
        os.path.join(LIVE, "docker-compose.v2-sleep.yml"),
        os.path.join(stage, "hunters", "docker-compose.v2-sleep.yml"),
    )
    os.makedirs(os.path.join(stage, "tools", "telegram"), exist_ok=True)
    shutil.copy2(
        os.path.join(LIVE, "grok_bot", "send_telegram.py"),
        os.path.join(stage, "tools", "telegram", "send_telegram.py"),
    )
    shutil.copy2(__file__, os.path.join(stage, "tools", "rimbaud-publish.py"))
    os.makedirs(os.path.join(stage, "third_party", "nlohmann"), exist_ok=True)
    shutil.copy2(
        os.path.join(LIVE, "freiquell_c++", "third_party", "nlohmann", "json.hpp"),
        os.path.join(stage, "third_party", "nlohmann", "json.hpp"),
    )
    patch(
        os.path.join(stage, "apps/matrix/desk/Makefile"),
        "-Ithird_party -Isrc",
        "-I../../../third_party -Isrc",
    )
    patch(
        os.path.join(stage, "apps/matrix/desk/Makefile"),
        "third_party/nlohmann/json.hpp",
        "../../../third_party/nlohmann/json.hpp",
    )
    patch(
        os.path.join(stage, "apps/matrix/desk/Makefile"),
        "all: $(BIN) $(CHART)\n\tln -sfn $(BIN) freiquell-desk\n\tln -sfn $(CHART) freiquell-chart\n",
        "all: $(BIN) $(CHART)\n",
    )
    patch(
        os.path.join(stage, "apps/spring/Makefile"),
        "-I../freiquell_c++/third_party -Isrc",
        "-I../../third_party -Isrc",
    )
    patch(
        os.path.join(stage, "apps/spring/Makefile"),
        "all: $(BIN) $(CTL)\n\tln -sfn $(BIN) freiquell-server\n\tln -sfn $(BIN) rimbaud-server\n",
        "all: $(BIN) $(CTL)\n",
    )
    patch(
        os.path.join(stage, "apps/meta/Makefile"),
        "-I../freiquell_c++/third_party -I../freiquell_c++/src",
        "-I../../third_party -I../matrix/desk/src",
    )
    patch(
        os.path.join(stage, "apps/meta/Makefile"),
        "../freiquell_c++/src/",
        "../matrix/desk/src/",
    )
    patch(
        os.path.join(stage, "apps/pettibon/Makefile"),
        "-I../freiquell_c++/third_party -Isrc",
        "-I../../third_party -Isrc",
    )
    patch(
        os.path.join(stage, "apps/archy/Makefile"),
        "-I../freiquell_c++/third_party",
        "-I../../third_party",
    )
    for dirpath, _, filenames in os.walk(PAGES):
        for name in filenames:
            sp = os.path.join(dirpath, name)
            rel = os.path.relpath(sp, PAGES)
            if rel == "gitignore":
                rel = ".gitignore"
            dp = os.path.join(stage, rel)
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            shutil.copy2(sp, dp)
    lic_src = os.path.join(LIVE, "LICENSE")
    with open(lic_src, "r", encoding="utf-8") as fh:
        lic = fh.read().replace(
            "Copyright 2023 Hummingbot Foundation.",
            "Copyright 2026 Freiquell e.V., Fichtestraße 1-4, 08451 Crimmitschau, GERMANY www.rimbaud.io.",
        )
    cover = "This license covers the rimBaud source in this repository.\n"
    if cover.strip() not in lic:
        if not lic.endswith("\n"):
            lic += "\n"
        lic += "\n" + cover
    with open(os.path.join(stage, "LICENSE"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(lic)
    with open(os.path.join(stage, "VERSION"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{version}\n{summary}\n")
    sanitize_stage(stage)
    write_folder_pages(stage)
    return audit_tree(stage)


def _catalog(name: str, kind: str) -> list[tuple[str, str, str]]:
    """Path, the GitHub column line, and the longer note. Archy owns the file."""
    path = os.path.join(LIVE, "rimbaud-archy", name)
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) != 3 or not parts[0] or not parts[1] or not parts[2]:
                raise SystemExit(f"github {kind} line needs a path, a line, and a note")
            if len(parts[1]) > 78:
                raise SystemExit(f"github {kind} line is longer than 78: {parts[0]}")
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def folder_catalog() -> list[tuple[str, str, str]]:
    return _catalog("github_folders.tsv", "folder")


def file_catalog() -> list[tuple[str, str, str]]:
    return _catalog("github_files.tsv", "file")


def about_page(rel: str, subject: str, detail: str, stamp: str = "") -> str:
    """Folder note. The stamp is how a parent commit stays newer than a child commit."""
    marker = f"<!-- archy-folder {stamp} -->\n" if stamp else "<!-- archy-folder -->\n"
    name = rel.split("/")[-1]
    return (
        "<!--\n"
        "Copyright 2026 Freiquell e.V., Fichtestraße 1-4, 08451 Crimmitschau, GERMANY www.rimbaud.io\n"
        "\n"
        "Licensed under the Apache License, Version 2.0 (the \"License\");\n"
        "you may not use this file except in compliance with the License.\n"
        "You may obtain a copy of the License at\n"
        "\n"
        "    http://www.apache.org/licenses/LICENSE-2.0\n"
        "\n"
        "Unless required by applicable law or agreed to in writing, software\n"
        "distributed under the License is distributed on an \"AS IS\" BASIS,\n"
        "WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.\n"
        "See the License for the specific language governing permissions and\n"
        "limitations under the License.\n"
        "-->\n"
        f"\n# {name}\n\n{subject}\n\n{detail}\n{marker}"
    )


def about_stamp(text: str) -> str:
    match = re.search(r"<!-- archy-folder(?: ([^ >]+))? -->", text)
    if not match:
        return ""
    return match.group(1) or ""


def place_about(folder: str, rel: str, subject: str, detail: str, stamp: str | None = None) -> None:
    dest = os.path.join(folder, "ABOUT.md")
    current = ""
    if os.path.isfile(dest):
        with open(dest, "r", encoding="utf-8") as fh:
            current = fh.read()
    if stamp is None:
        stamp = about_stamp(current)
    text = about_page(rel, subject, detail, stamp)
    if current != text:
        with open(dest, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def write_folder_pages(stage: str) -> None:
    """Put ABOUT.md in the staged tree so the audit sees the same note the commit writes."""
    for rel, subject, detail in folder_catalog():
        folder = os.path.join(stage, rel)
        if not os.path.isdir(folder):
            print(f"github folder missing from the tree: {rel}", file=sys.stderr)
            continue
        place_about(folder, rel, subject, detail, stamp="")


def git(dest: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=dest, check=check, text=True, capture_output=True)


def ensure_repo(dest: str) -> None:
    if not os.path.isdir(os.path.join(dest, ".git")):
        subprocess.run(["git", "init", "-b", "main"], cwd=dest, check=True)
    if git(dest, "config", "--get", "user.name", check=False).returncode != 0:
        git(dest, "config", "user.name", "rimbaud")
    if git(dest, "config", "--get", "user.email", check=False).returncode != 0:
        git(dest, "config", "user.email", "rimbaud@rimbaud.io")


def _staged(dest: str, path: str = "") -> bool:
    args = ["diff", "--cached", "--quiet"]
    if path:
        args.extend(["--", path])
    return git(dest, *args, check=False).returncode != 0


def _has_publish_file(path: str) -> bool:
    """An empty directory does not show on GitHub, so it needs no folder line."""
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        for name in filenames:
            if name != "ABOUT.md":
                return True
    return False


def unlisted_folders(dest: str, rows: list[tuple[str, str, str]]) -> list[str]:
    """Every public folder needs a line, including a folder inside a folder."""
    known = {rel for rel, _subject, _detail in rows}
    missing = []
    if not os.path.isdir(dest):
        return missing
    for dirpath, dirnames, _filenames in os.walk(dest):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        rel = os.path.relpath(dirpath, dest)
        if rel == "." or rel in known or not _has_publish_file(dirpath):
            continue
        missing.append(rel)
    return sorted(missing)


def commit(dest: str, version: str, summary: str) -> bool:
    """One commit per listed folder, then one per listed root file.

    GitHub shows the last commit subject beside each path. That subject is the
    line from github_folders.tsv or github_files.tsv. A last empty commit keeps
    the repository banner as the Archy version and this change, and it does not
    replace those lines.
    """
    ensure_repo(dest)
    rows = sorted(folder_catalog(), key=lambda row: (-row[0].count("/"), row[0]))
    by_path = {rel: (subject, detail) for rel, subject, detail in rows}
    for rel, subject, detail in rows:
        folder = os.path.join(dest, rel)
        if os.path.isdir(folder):
            place_about(folder, rel, subject, detail)
    missing_dirs = unlisted_folders(dest, rows)
    if missing_dirs:
        print("add a github folder line for: " + ", ".join(missing_dirs), file=sys.stderr)
    git(dest, "add", "-A")
    if not _staged(dest):
        print(f"github tree already at this content ({version})", file=sys.stderr)
        return False
    note = f"Archy {version}. {summary}".strip()
    made = False
    committed: list[str] = []
    for rel, subject, detail in rows:
        if not _staged(dest, rel):
            continue
        if not os.path.isdir(os.path.join(dest, rel)):
            continue
        git(dest, "commit", "-m", subject, "-m", detail + "\n\n" + note, "--", rel)
        print(f"folder {rel}", file=sys.stderr)
        committed.append(rel)
        made = True
    # A child commit is newer than its parent, so GitHub would show the child
    # line on the parent. Stamp the parent and commit it again, after the child.
    parents: list[str] = []
    covered = set(committed)
    for rel in committed:
        parent = os.path.dirname(rel)
        while parent:
            if parent in by_path and parent not in covered:
                parents.append(parent)
                covered.add(parent)
            parent = os.path.dirname(parent)
    for rel in sorted(parents, key=lambda item: -item.count("/")):
        subject, detail = by_path[rel]
        place_about(os.path.join(dest, rel), rel, subject, detail, stamp=version)
        git(dest, "add", "--", os.path.join(rel, "ABOUT.md"))
        git(dest, "commit", "-m", subject, "-m", detail + "\n\n" + note, "--", rel)
        print(f"folder {rel}", file=sys.stderr)
        made = True
    known_files = set()
    for name, subject, detail in file_catalog():
        known_files.add(name)
        if not _staged(dest, name):
            continue
        if not os.path.isfile(os.path.join(dest, name)):
            continue
        git(dest, "commit", "-m", subject, "-m", detail + "\n\n" + note, "--", name)
        print(f"file {name}", file=sys.stderr)
        made = True
    if _staged(dest):
        names = git(dest, "diff", "--cached", "--name-only", "--diff-filter=A", check=False).stdout or ""
        known = {row[0].split("/")[0] for row in rows}
        missing = sorted({
            line.split("/")[0]
            for line in names.splitlines()
            if "/" in line and line.split("/")[0] not in known
        })
        loose = sorted({
            line
            for line in names.splitlines()
            if "/" not in line and line not in known_files
        })
        if missing:
            print("add a github folder line for: " + ", ".join(missing), file=sys.stderr)
        if loose:
            print("add a github file line for: " + ", ".join(loose), file=sys.stderr)
        git(dest, "commit", "-m", f"{version} {summary}", "-m", note)
        made = True
    if made:
        git(dest, "commit", "--allow-empty", "-m", f"{version} {summary}", "-m", note)
        print(f"committed {version} in {dest}", file=sys.stderr)
    return made


def remote_label(dest: str) -> str:
    url = git(dest, "remote", "get-url", "origin", check=False)
    if url.returncode != 0:
        return ""
    remote = (url.stdout or "").strip()
    if "hummingbot/hummingbot" in remote:
        raise SystemExit("refusing to push: origin is the Hummingbot repository")
    shown = re.sub(r"^[A-Za-z0-9+.-]+://", "", remote)
    shown = re.sub(r"^[^@/]+@", "", shown)
    if shown.endswith(".git"):
        shown = shown[:-4]
    return shown[:80]


def push(dest: str) -> str:
    label = remote_label(dest)
    if not label:
        print("no github remote yet. commit is local only.", file=sys.stderr)
        return ""
    subprocess.run(["git", "push", "-u", "origin", "HEAD"], cwd=dest, check=True)
    print(f"pushed to {label}", file=sys.stderr)
    return label


def _archy_summary(version: str) -> str:
    """The change for this version. The send step reads the ledger, not a missing JSON log."""
    db = os.path.join(LIVE, "conf", "archy.db")
    if not os.path.isfile(db):
        return ""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT kind, to_ver, summary FROM archy_log ORDER BY id DESC LIMIT 40"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return ""
    fallback = ""
    for kind, to_ver, summary in rows:
        if str(kind or "") == "github":
            continue
        text = str(summary or "").strip()
        if not text:
            continue
        if not fallback:
            fallback = text
        if version and str(to_ver or "") == version:
            return text
    return fallback


def latest_archy() -> tuple[str, str]:
    path = os.path.join(LIVE, "conf", "archy.json")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    version = str(data.get("version") or "")
    return version, _archy_summary(version)


def _drop_retired(stage: str, dest: str) -> None:
    """rsync leaves ABOUT.md behind, so a folder that left the tree would remain."""
    for dirpath, dirnames, _filenames in os.walk(dest, topdown=True):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        rel = os.path.relpath(dirpath, dest)
        if rel == ".":
            continue
        if os.path.isdir(os.path.join(stage, rel)):
            continue
        shutil.rmtree(dirpath, ignore_errors=True)
        dirnames[:] = []


def _copy_stage(stage: str) -> None:
    os.makedirs(DEST, exist_ok=True)
    subprocess.run(
        ["rsync", "-a", "--delete", "--exclude", ".git", "--exclude", "ABOUT.md", f"{stage}/", f"{DEST}/"],
        check=True,
    )
    _drop_retired(stage, DEST)
    shutil.rmtree(stage, ignore_errors=True)


def _emit(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


def prepare_json() -> int:
    """Copy the live tree into the GitHub folder and audit it. Does not push."""
    version, summary = latest_archy()
    summary = summary or "source update"
    if not version:
        _emit({"ok": False, "clean": False, "pushed": False, "error": "no archy version"})
        return 1
    stage = "/tmp/rimbaud-github-stage"
    try:
        findings = build_stage(stage, version, summary)
    except SystemExit:
        shutil.rmtree(stage, ignore_errors=True)
        _emit({"ok": False, "clean": False, "pushed": False, "error": "github copy failed"})
        return 1
    if findings:
        shutil.rmtree(stage, ignore_errors=True)
        _emit({
            "ok": False,
            "clean": False,
            "pushed": False,
            "committed": False,
            "version": version,
            "findings": findings,
            "message": "audit blocked the send",
        })
        return 2
    _copy_stage(stage)
    committed = commit(DEST, version, summary)
    _emit({
        "ok": True,
        "clean": True,
        "pushed": False,
        "committed": committed,
        "version": version,
        "findings": [],
        "message": "audit clean - click again to send",
    })
    return 0


def send_json() -> int:
    """Push the GitHub folder after a fresh audit. The second Archy click."""
    if not os.path.isdir(os.path.join(DEST, ".git")):
        _emit({"ok": False, "clean": False, "pushed": False, "error": "github folder is not ready"})
        return 1
    findings = audit_tree(DEST)
    if findings:
        _emit({
            "ok": False,
            "clean": False,
            "pushed": False,
            "findings": findings,
            "message": "audit blocked the send",
        })
        return 2
    try:
        label = remote_label(DEST)
    except SystemExit:
        _emit({"ok": False, "clean": True, "pushed": False, "error": "refusing the Hummingbot repository"})
        return 1
    if not label:
        _emit({"ok": False, "clean": True, "pushed": False, "error": "no github remote"})
        return 1
    ran = subprocess.run(
        ["git", "push", "-u", "origin", "HEAD"],
        cwd=DEST,
        capture_output=True,
        text=True,
    )
    if ran.returncode != 0:
        _emit({"ok": False, "clean": True, "pushed": False, "error": "github push failed"})
        return 1
    version, _summary = latest_archy()
    _emit({
        "ok": True,
        "clean": True,
        "pushed": True,
        "version": version,
        "remote": label,
        "findings": [],
        "message": "sent " + version + " to github",
    })
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Commit the rimBaud GitHub tree at the Archy version")
    parser.add_argument("--version", default="")
    parser.add_argument("--summary", default="")
    parser.add_argument("--no-commit", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--prepare-json", action="store_true")
    parser.add_argument("--send-json", action="store_true")
    args = parser.parse_args(argv)
    if args.prepare_json:
        return prepare_json()
    if args.send_json:
        return send_json()
    version, summary = args.version, args.summary
    if not version or not summary:
        got_v, got_s = latest_archy()
        version = version or got_v
        summary = summary or got_s or "source update"
    if not version:
        print("no archy version", file=sys.stderr)
        return 1
    stage = "/tmp/rimbaud-github-stage"
    findings = build_stage(stage, version, summary)
    if findings:
        shutil.rmtree(stage, ignore_errors=True)
        for row in findings[:12]:
            print(f"publish refused, {row['kind']} in {row['file']}", file=sys.stderr)
        return 1
    _copy_stage(stage)
    if args.no_commit:
        print(f"refreshed {DEST} at {version}", file=sys.stderr)
        return 0
    commit(DEST, version, summary)
    if args.push or os.environ.get("ARCHY_PUBLISH_PUSH") == "1":
        push(DEST)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
