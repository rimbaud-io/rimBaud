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

# Prepare a Hummingbot image upgrade. Download does not recreate hunters.
# Execute recreates only the official-image hunters. Positions stay on Hyperliquid.
#   python3 install_new/hb-upgrade.py download local
#   python3 install_new/hb-upgrade.py download all
#   python3 install_new/hb-upgrade.py execute local
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(ROOT, "conf")
JOB = os.path.join(CONF, "hb_upgrade_job.json")
META = os.path.join(CONF, "meta.json")
HOSTS = os.path.join(CONF, "deploy_hosts.json")
COMPOSE = os.path.join(ROOT, "docker-compose.yml")
IMAGE = "hummingbot/hummingbot:latest"
# Official image only. Not dashboard, condor, version, audit, gateway, or the SOL sleeper.
HUNTERS = [
    "hummingbot-hl",
    "hummingbot-bn-swing",
    "hummingbot-helliot",
    "hummingbot-surfn",
    "hummingbot-shortn",
    "hummingbot-dman",
    "hummingbot-onlyup",
    "hummingbot-dman-maker",
]
LAYER_RE = re.compile(r"^([0-9a-f]+):\s+(.*)$")


def now_stamp():
    return time.strftime("%H:%M:%S", time.gmtime())


def load_job():
    try:
        with open(JOB) as f:
            j = json.load(f)
        return j if isinstance(j, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_job(job):
    os.makedirs(CONF, exist_ok=True)
    tmp = JOB + ".tmp"
    with open(tmp, "w") as f:
        json.dump(job, f, indent=2)
        f.write("\n")
    os.replace(tmp, JOB)


def log(job, msg):
    line = now_stamp() + "  " + " ".join(str(msg).split())
    lines = list(job.get("log") or [])
    lines.append(line[:240])
    job["log"] = lines[-80:]
    save_job(job)
    print(line, flush=True)


def read_json(path):
    try:
        with open(path) as f:
            j = json.load(f)
        return j if isinstance(j, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def safe_target(text):
    if not text or len(text) > 80 or "@" not in text:
        return False
    return all(c.isalnum() or c in "@._-" for c in text)


def safe_path(text):
    return bool(text) and text.startswith("/") and all(c.isalnum() or c in "/._-" for c in text)


def sites():
    meta = read_json(META)
    rows = [{"id": "local", "name": "this matrix", "host": "127.0.0.1"}]
    for s in meta.get("sites") or []:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "")
        host = str(s.get("host") or "")
        if not sid or sid == "local" or host in ("127.0.0.1", "localhost"):
            continue
        rows.append({"id": sid, "name": str(s.get("name") or sid), "host": host})
    return rows


def ssh_for(sid):
    hosts = read_json(HOSTS)
    row = hosts.get(sid) if isinstance(hosts.get(sid), dict) else None
    if not row:
        return None
    identity = str(row.get("identity") or os.path.expanduser("~/.ssh/id_ed25519_rimbaud_deploy"))
    if identity.startswith("~"):
        identity = os.path.expanduser(identity)
    root = str(row.get("root") or "")
    targets = []
    for key in ("ssh", "ssh_lan"):
        t = str(row.get(key) or "")
        if safe_target(t):
            targets.append(t)
    if not targets or not os.path.isfile(identity) or not safe_path(root):
        return None
    return {"identity": identity, "targets": targets, "root": root, "name": str(row.get("name") or sid)}


def note_layer(layers, line):
    text = line.strip()
    if "Image is up to date" in text or "Downloaded newer image" in text:
        return 100
    m = LAYER_RE.match(text)
    if not m:
        return None
    lid, msg = m.group(1), m.group(2)
    if "Pulling fs layer" in msg or msg.startswith("Waiting"):
        layers.setdefault(lid, "wait")
    elif "Downloading" in msg or "Extracting" in msg:
        layers[lid] = "down"
    elif "Download complete" in msg or "Pull complete" in msg or "Already exists" in msg:
        layers[lid] = "done"
    else:
        return None
    total = max(1, len(layers))
    done = sum(1 for v in layers.values() if v == "done")
    return int(100 * done / total)


def run_stream(job, argv, label, base, span):
    log(job, label + "  " + " ".join(argv[:6]))
    try:
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
    except OSError as exc:
        log(job, "cannot start: " + str(exc))
        return 1
    layers = {}
    buf = ""
    while True:
        chunk = proc.stdout.read(256) if proc.stdout else ""
        if not chunk:
            break
        buf += chunk
        while True:
            cut = len(buf)
            for sep in ("\n", "\r"):
                i = buf.find(sep)
                if i >= 0:
                    cut = min(cut, i)
            if cut == len(buf):
                break
            line = buf[:cut]
            buf = buf[cut + 1:]
            if not line.strip():
                continue
            pct = note_layer(layers, line)
            if pct is not None:
                job["pct"] = min(99, base + int(span * pct / 100))
                job["step"] = label + "  " + line.strip()[:80]
                save_job(job)
            elif "Status:" in line or "error" in line.lower() or "denied" in line.lower():
                log(job, line.strip()[:200])
    code = proc.wait()
    if buf.strip():
        log(job, buf.strip()[:200])
    if code != 0:
        log(job, label + " failed (" + str(code) + ")")
    return code


def pull_one(job, row, base, span):
    sid = row["id"]
    name = row.get("name") or sid
    if sid == "local" or row.get("host") in ("127.0.0.1", "localhost"):
        return run_stream(job, ["docker", "pull", IMAGE], name + " pull", base, span)
    info = ssh_for(sid)
    if not info:
        log(job, name + " has no ssh target in deploy_hosts.json")
        return 1
    last = 1
    for target in info["targets"]:
        argv = [
            "ssh", "-i", info["identity"],
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new",
            target, "docker", "pull", IMAGE,
        ]
        last = run_stream(job, argv, name + " pull via " + target, base, span)
        if last == 0:
            return 0
        log(job, name + " ssh " + target + " did not pull — trying the next address")
    return last


def selected(argv):
    if not argv or argv[0] == "all":
        return sites()
    want = set(argv)
    rows = [s for s in sites() if s["id"] in want]
    if not rows:
        raise SystemExit("unknown matrix id: " + " ".join(argv))
    return rows


def cmd_download(ids):
    rows = selected(ids)
    job = load_job()
    job.update({
        "phase": "downloading",
        "pct": 0,
        "step": "starting download",
        "targets": [r["id"] for r in rows],
        "error": "",
        "image": IMAGE,
    })
    log(job, "download only — hunters are not recreated")
    n = max(1, len(rows))
    failed = []
    for i, row in enumerate(rows):
        code = pull_one(job, row, int(100 * i / n), int(100 / n))
        if code != 0:
            failed.append(row["id"])
    if failed:
        job["phase"] = "error"
        job["error"] = "pull failed: " + ", ".join(failed)
        job["step"] = job["error"]
        log(job, job["error"])
        save_job(job)
        return 1
    job["phase"] = "ready"
    job["pct"] = 100
    job["step"] = "image downloaded — execute is still waiting for you"
    log(job, "ready. Press Execute on SYSTEM to recreate the hunters.")
    save_job(job)
    return 0


def recreate_one(job, row):
    sid = row["id"]
    name = row.get("name") or sid
    if sid == "local" or row.get("host") in ("127.0.0.1", "localhost"):
        argv = [
            "docker", "compose", "-f", COMPOSE, "--project-directory", ROOT,
            "up", "-d", "--no-deps", "--force-recreate", *HUNTERS,
        ]
        return run_stream(job, argv, name + " recreate", 0, 100)
    info = ssh_for(sid)
    if not info:
        log(job, name + " has no ssh target")
        return 1
    remote = [
        "docker", "compose", "-f", info["root"] + "/docker-compose.yml",
        "--project-directory", info["root"],
        "up", "-d", "--no-deps", "--force-recreate", *HUNTERS,
    ]
    last = 1
    for target in info["targets"]:
        argv = [
            "ssh", "-i", info["identity"],
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new",
            target, *remote,
        ]
        last = run_stream(job, argv, name + " recreate via " + target, 0, 100)
        if last == 0:
            return 0
    return last


def cmd_execute(ids):
    job = load_job()
    if job.get("phase") != "ready":
        log(job, "refusing execute — download the image first (phase is %s)" % (job.get("phase") or "idle"))
        job["phase"] = job.get("phase") or "idle"
        job["error"] = "download the image first"
        save_job(job)
        return 2
    rows = selected(ids)
    want = [r["id"] for r in rows]
    got = list(job.get("targets") or [])
    if got and set(want) != set(got):
        log(job, "refusing execute — selection changed since the download")
        job["error"] = "selection changed — download again"
        save_job(job)
        return 2
    job["phase"] = "executing"
    job["pct"] = 0
    job["error"] = ""
    log(job, "recreate hunters on the new image — positions stay on Hyperliquid")
    failed = []
    n = max(1, len(rows))
    for i, row in enumerate(rows):
        job["pct"] = int(100 * i / n)
        job["step"] = "recreate " + (row.get("name") or row["id"])
        save_job(job)
        if recreate_one(job, row) != 0:
            failed.append(row["id"])
    if failed:
        job["phase"] = "error"
        job["error"] = "recreate failed: " + ", ".join(failed)
        job["step"] = job["error"]
        log(job, job["error"])
        save_job(job)
        return 1
    job["phase"] = "done"
    job["pct"] = 100
    job["step"] = "hunters recreated"
    log(job, "done")
    save_job(job)
    return 0


def selftest():
    layers = {}
    assert note_layer(layers, "abc: Pulling fs layer") == 0
    assert note_layer(layers, "abc: Downloading [=> ] 1MB/2MB") == 0
    assert note_layer(layers, "abc: Download complete") == 100
    assert note_layer(layers, "Status: Image is up to date for hummingbot/hummingbot:latest") == 100
    assert safe_target("forklifter@10.66.80.2")
    assert not safe_target("forklifter@10.66.80.2;rm")
    print("selftest ok")
    return 0


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        return 2
    cmd, rest = argv[1], argv[2:]
    if cmd == "selftest":
        return selftest()
    if cmd == "download":
        return cmd_download(rest or ["local"])
    if cmd == "execute":
        return cmd_execute(rest or ["local"])
    print("unknown command: " + cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
