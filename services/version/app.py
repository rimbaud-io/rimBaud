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

"""Local Timeshift version desk. Bind loopback-only when not behind the matrix proxy."""
import os
from flask import Flask, jsonify, request, send_from_directory

import timeshift_lib as ts

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=HERE, static_url_path="")


@app.route("/")
@app.route("/version")
def page():
    return send_from_directory(HERE, "index.html")


@app.route("/api/snapshots")
@app.route("/api/version/snapshots")
def api_snapshots():
    snaps = ts.list_snapshots()
    job = ts.job_get()
    return jsonify({
        "ok": True,
        "local": True,
        "root": ts.SNAP_ROOT,
        "running": ts.timeshift_running(),
        "job": job,
        "snapshots": snaps,
        "n": len(snaps),
    })


@app.route("/api/create", methods=["POST"])
@app.route("/api/version/create", methods=["POST"])
def api_create():
    data = request.get_json(silent=True) or {}
    return jsonify(ts.create_snapshot(data.get("label") or "manual"))


@app.route("/api/restore", methods=["POST"])
@app.route("/api/version/restore", methods=["POST"])
def api_restore():
    data = request.get_json(silent=True) or {}
    return jsonify(ts.restore_snapshot(data.get("name"), data.get("confirm")))


@app.route("/api/job")
@app.route("/api/version/job")
def api_job():
    return jsonify({"ok": True, "job": ts.job_get(), "running": ts.timeshift_running()})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5003")), debug=False, threaded=True)
