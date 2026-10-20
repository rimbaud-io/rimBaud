#!/usr/bin/env python3
# Copyright 2026 rimbaud.io
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

Archy --bump-g calls this. It does not push unless --push is passed.
It refuses a remote that points at hummingbot/hummingbot.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
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
    ("website", "website"),
    ("iphone", "phone"),
    ("rimbaud_vpn", "vpn"),
    ("condor", "services/condor"),
    ("version", "services/version"),
    ("docs/print", "docs/print"),
    ("doku", "docs/desk"),
    ("HL_wallet_creator", "tools/wallet-creator"),
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


def assert_public(stage: str) -> None:
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    name_b = user.encode() if user and user.lower() not in _SKIP_NAMES else b""
    eth = re.compile(br"(?<![0-9a-fA-F])0x(?!0{40}(?![0-9a-fA-F]))[0-9a-fA-F]{40}(?![0-9a-fA-F])")
    home = re.compile(br"(?<![A-Za-z0-9._-])/home/(?!hummingbot\b)[A-Za-z0-9._-]")
    media = re.compile(br"/media/(?!usb(?![A-Za-z0-9._-]))(?!cdrom(?![A-Za-z0-9._-]))[A-Za-z0-9._-]+")
    ip = re.compile(br"\b(?:\d{1,3}\.){3}\d{1,3}\b")
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
            rel = os.path.relpath(path, stage)
            if home.search(blob):
                raise SystemExit(f"publish refused, home path in {rel}")
            if name_b and re.search(rb"(?i)\b" + re.escape(name_b) + rb"\b", blob):
                raise SystemExit(f"publish refused, operator name in {rel}")
            if eth.search(blob):
                raise SystemExit(f"publish refused, wallet in {rel}")
            if media.search(blob):
                raise SystemExit(f"publish refused, media user in {rel}")
            for match in ip.finditer(blob):
                addr = match.group(0)
                try:
                    nums = [int(part) for part in addr.split(b".")]
                except ValueError:
                    continue
                if len(nums) != 4 or any(n > 255 for n in nums):
                    continue
                a, b = nums[0], nums[1]
                if a in (0, 127) or addr in _RESOLVERS or addr == b"255.255.255.255":
                    continue
                if a == 10 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31):
                    continue
                if a == 169 and b == 254:
                    continue
                raise SystemExit(f"publish refused, public address in {rel}")


def build_stage(stage: str, version: str, summary: str) -> None:
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
            "Copyright 2026 rimbaud.io.",
        )
    with open(os.path.join(stage, "LICENSE"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(lic)
    with open(os.path.join(stage, "VERSION"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{version}\n{summary}\n")
    sanitize_stage(stage)
    assert_public(stage)
    forbidden = ("BEGIN PRIVATE", "user-password-crypted password $6$", "plaintext: rimbaud")
    for dirpath, _, filenames in os.walk(stage):
        for name in filenames:
            path = os.path.join(dirpath, name)
            if os.path.getsize(path) > 5_000_000:
                continue
            try:
                blob = open(path, "rb").read()
            except OSError:
                continue
            if name in {"rimbaud-publish.py", "rimbaud_publish.py"}:
                continue
            for needle in forbidden:
                if needle.encode() in blob:
                    raise SystemExit(f"publish refused, secret-shaped text in {path}")


def git(dest: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=dest, check=check, text=True, capture_output=True)


def ensure_repo(dest: str) -> None:
    if not os.path.isdir(os.path.join(dest, ".git")):
        subprocess.run(["git", "init", "-b", "main"], cwd=dest, check=True)
    if git(dest, "config", "--get", "user.name", check=False).returncode != 0:
        git(dest, "config", "user.name", "rimbaud")
    if git(dest, "config", "--get", "user.email", check=False).returncode != 0:
        git(dest, "config", "user.email", "rimbaud@rimbaud.io")


def commit(dest: str, version: str, summary: str) -> bool:
    ensure_repo(dest)
    git(dest, "add", "-A")
    quiet = git(dest, "diff", "--cached", "--quiet", check=False)
    if quiet.returncode == 0:
        print(f"github tree already at this content ({version})", file=sys.stderr)
        return False
    msg = f"{version} {summary}\n\nArchy {version}\n"
    git(dest, "commit", "-m", msg)
    print(f"committed {version} in {dest}", file=sys.stderr)
    return True


def push(dest: str) -> None:
    url = git(dest, "remote", "get-url", "origin", check=False)
    if url.returncode != 0:
        print("no github remote yet. commit is local only.", file=sys.stderr)
        return
    remote = (url.stdout or "").strip()
    if "hummingbot/hummingbot" in remote:
        raise SystemExit("refusing to push: origin is the Hummingbot repository")
    subprocess.run(["git", "push", "-u", "origin", "HEAD"], cwd=dest, check=True)
    print(f"pushed to {remote}", file=sys.stderr)


def latest_archy() -> tuple[str, str]:
    path = os.path.join(LIVE, "conf", "archy.json")
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    version = str(data.get("version") or "")
    log = data.get("log") or []
    summary = ""
    if log:
        summary = str(log[0].get("summary") or "")
    return version, summary


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Commit the rimBaud GitHub tree at the Archy version")
    parser.add_argument("--version", default="")
    parser.add_argument("--summary", default="")
    parser.add_argument("--no-commit", action="store_true")
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args(argv)
    version, summary = args.version, args.summary
    if not version or not summary:
        got_v, got_s = latest_archy()
        version = version or got_v
        summary = summary or got_s or "source update"
    if not version:
        print("no archy version", file=sys.stderr)
        return 1
    stage = "/tmp/rimbaud-github-stage"
    build_stage(stage, version, summary)
    os.makedirs(DEST, exist_ok=True)
    subprocess.run(
        ["rsync", "-a", "--delete", "--exclude", ".git", f"{stage}/", f"{DEST}/"],
        check=True,
    )
    shutil.rmtree(stage, ignore_errors=True)
    if args.no_commit:
        print(f"refreshed {DEST} at {version}", file=sys.stderr)
        return 0
    commit(DEST, version, summary)
    if args.push or os.environ.get("ARCHY_PUBLISH_PUSH") == "1":
        push(DEST)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
