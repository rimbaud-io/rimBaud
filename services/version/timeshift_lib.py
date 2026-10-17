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

"""Local Timeshift helpers. Snapshots stay on this machine."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime

SNAP_ROOT = os.environ.get("TIMESHIFT_SNAPS", "/timeshift/snapshots")
JOB_FILE = os.environ.get("VERSION_JOB", "/conf/version_job.json")
DEVICE = os.environ.get("TIMESHIFT_DEVICE", "/dev/nvme0n1p2")


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def _read_json(path):
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json(path, data):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def job_get():
    row = _read_json(JOB_FILE)
    return row if isinstance(row, dict) else {}


def job_set(**kw):
    row = job_get()
    row.update(kw)
    row["updated"] = _now()
    try:
        _write_json(JOB_FILE, row)
    except OSError:
        pass
    return row


def timeshift_running():
    try:
        r = subprocess.run(["pgrep", "-af", "timeshift"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and "timeshift --" in (r.stdout or ""):
            return True
    except Exception:
        pass
    try:
        for name in os.listdir(SNAP_ROOT):
            d = os.path.join(SNAP_ROOT, name)
            if os.path.isdir(os.path.join(d, "localhost")) and not os.path.isfile(os.path.join(d, "info.json")):
                return True
    except OSError:
        pass
    return False


def host_timeshift(args, timeout=120):
    """Run host timeshift via nsenter (privileged pid-host) or docker nsenter."""
    ts = "/usr/bin/timeshift"
    if os.path.exists("/usr/sbin/timeshift"):
        ts = "/usr/sbin/timeshift"
    nsenter = os.path.exists("/usr/bin/nsenter") and os.path.exists("/proc/1/ns/mnt")
    if nsenter:
        cmd = ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "--", ts] + list(args)
    elif os.path.exists(ts):
        cmd = [ts] + list(args)
    else:
        cmd = [
            "docker", "run", "--rm", "--privileged", "--pid=host", "--network", "host",
            "alpine:3.20",
            "nsenter", "-t", "1", "-m", "-u", "-i", "-n", "--",
            "/usr/bin/timeshift",
        ] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _du_bytes(path):
    try:
        r = subprocess.run(["du", "-sb", path], capture_output=True, text=True, timeout=20)
        return int((r.stdout or "0").split()[0])
    except Exception:
        return 0


def list_snapshots():
    out = []
    if not os.path.isdir(SNAP_ROOT):
        return out
    try:
        names = sorted(os.listdir(SNAP_ROOT), reverse=True)
    except OSError:
        return out
    for name in names:
        if name.startswith("."):
            continue
        d = os.path.join(SNAP_ROOT, name)
        if not os.path.isdir(d):
            continue
        info = _read_json(os.path.join(d, "info.json"))
        comments = str(info.get("comments") or info.get("description") or "")
        tags = str(info.get("tags") or "")
        created = str(info.get("created") or "")
        complete = os.path.isfile(os.path.join(d, "info.json"))
        live = os.path.isdir(os.path.join(d, "localhost")) and not complete
        size_b = 0
        try:
            st = os.stat(d)
            size_b = st.st_size
        except OSError:
            pass
        logp = os.path.join(d, "rsync-log")
        log_tail = ""
        try:
            with open(logp, "rb") as f:
                f.seek(0, os.SEEK_END)
                n = f.tell()
                f.seek(max(0, n - 400))
                log_tail = f.read().decode("utf-8", "replace").strip().splitlines()[-1] if n else ""
        except Exception:
            pass
        out.append({
            "name": name,
            "comments": comments or ("copying…" if live else ""),
            "tags": tags,
            "created": created or name.replace("_", " "),
            "complete": complete,
            "live": live or (not complete and timeshift_running()),
            "has_tree": os.path.isdir(os.path.join(d, "localhost")),
            "log_tail": log_tail[-180:],
            "size_hint": size_b,
        })
    return out


def create_snapshot(label):
    label = str(label or "manual").strip()[:80] or "manual"
    if job_get().get("status") == "running":
        return {"ok": False, "error": "a snapshot job is already running"}
    if timeshift_running():
        return {"ok": False, "error": "Timeshift is already copying on this machine"}
    job_set(status="running", action="create", label=label, error=None, started=_now(), finished=None, log="")

    def _run():
        try:
            r = host_timeshift(["--create", "--rsync", "--yes", "--comments", label], timeout=4 * 3600)
            ok = r.returncode == 0
            log = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()[-4000:]
            job_set(
                status="ok" if ok else "error",
                error=None if ok else (log[-400:] or "timeshift create failed"),
                finished=_now(),
                log=log,
            )
        except Exception as e:
            job_set(status="error", error=str(e), finished=_now())

    threading.Thread(target=_run, daemon=True, name="timeshift-create").start()
    return {"ok": True, "message": f"creating snapshot “{label}” — leave it running"}


def restore_snapshot(name, confirm):
    name = str(name or "").strip()
    confirm = str(confirm or "").strip()
    if not name:
        return {"ok": False, "error": "snapshot name required"}
    if confirm != name:
        return {"ok": False, "error": "type the snapshot name to confirm switch-back"}
    path = os.path.join(SNAP_ROOT, name)
    if not os.path.isdir(path):
        return {"ok": False, "error": "no such snapshot"}
    if not os.path.isfile(os.path.join(path, "info.json")):
        return {"ok": False, "error": "snapshot is still copying — cannot restore yet"}
    if job_get().get("status") == "running":
        return {"ok": False, "error": "a snapshot job is already running"}
    job_set(status="running", action="restore", label=name, error=None, started=_now(), finished=None, log="")

    def _run():
        try:
            r = host_timeshift(
                ["--restore", "--rsync", "--yes", "--skip-grub",
                 "--snapshot", name, "--target-device", DEVICE],
                timeout=4 * 3600,
            )
            ok = r.returncode == 0
            log = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()[-4000:]
            job_set(
                status="ok" if ok else "error",
                error=None if ok else (log[-400:] or "timeshift restore failed"),
                finished=_now(),
                log=log,
                reboot=ok,
            )
        except Exception as e:
            job_set(status="error", error=str(e), finished=_now())

    threading.Thread(target=_run, daemon=True, name="timeshift-restore").start()
    return {
        "ok": True,
        "message": f"switching to {name} — when Timeshift finishes, reboot this machine",
        "reboot": True,
    }
