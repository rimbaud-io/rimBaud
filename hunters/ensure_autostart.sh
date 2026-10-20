#!/usr/bin/env bash
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

# Wire boot/login autostart for Docker Desktop (GUI) + hummingbot stack.
#
# How this machine works:
#   - Docker only starts reliably via Docker Desktop (app menu / AutoStart /
#     `docker desktop start`). Headless `com.docker.backend -unattended` fails
#     unless the user is in the kvm group.
#   - hummingbot-stack waits for the Docker engine, then `compose up`s the
#     production services. It is WantedBy=graphical-session.target (after login).
set -euo pipefail

echo "Linger: $(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo '?')"

if id -nG 2>/dev/null | tr ' ' '\n' | grep -qx kvm; then
  echo "kvm group: yes (optional; helps headless Docker Desktop)"
else
  echo "kvm group: no (OK if you always start Docker Desktop from the GUI)"
fi

systemctl --user daemon-reload
# Stock Docker Desktop unit (starts with graphical session)
systemctl --user enable docker-desktop.service 2>/dev/null || true
systemctl --user enable hummingbot-stack.service

# Docker Desktop settings: start with session
SETTINGS="$HOME/.docker/desktop/settings-store.json"
if [[ -f "$SETTINGS" ]]; then
  python3 - <<'PY'
import json
from pathlib import Path
p = Path.home() / ".docker" / "desktop" / "settings-store.json"
d = json.loads(p.read_text())
d["AutoStart"] = True
p.write_text(json.dumps(d, indent=2) + "\n")
print("settings-store.json AutoStart=true")
PY
fi

echo "Enabled units:"
systemctl --user is-enabled docker-desktop.service hummingbot-stack.service || true

if docker info >/dev/null 2>&1; then
  echo "Docker engine: up"
  systemctl --user start hummingbot-stack.service || true
else
  echo "Docker engine: down — open Docker Desktop (or: docker desktop start),"
  echo "  then: systemctl --user start hummingbot-stack.service"
fi

echo "Done. After login, Docker Desktop AutoStart + hummingbot-stack should bring the bots up."
