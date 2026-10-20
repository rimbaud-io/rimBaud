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

# Pull images, build helpers, start hunters + dashboard. Does not start SOL/testnet/gateway.
set -euo pipefail
# shellcheck source=install_new/_common.sh
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

docker_ok || die "Docker engine is down. Start Docker, then retry."
[ -f "$ROOT/docker-compose.yml" ] || die "missing docker-compose.yml"
"$INSTALL_DIR/verify.sh"

mapfile -t SVCS < <(matrix_services)
[ "${#SVCS[@]}" -gt 0 ] || die "services.list is empty"

if [ -f "$ROOT/install_new/images/hummingbot-latest.tar" ]; then
  log "Loading offline hummingbot image"
  as_root docker load -i "$ROOT/install_new/images/hummingbot-latest.tar"
else
  log "Pulling hummingbot/hummingbot:latest"
  as_root docker pull hummingbot/hummingbot:latest
fi

if [ "${RIMBAUD_BOOTSTRAP:-1}" != "0" ]; then
  log "Bootstrap (new HL account): Copy hunter + dashboard only"
  compose build dashboard
else
  log "Building dashboard, condor, version, audit"
  compose build dashboard condor hummingbot-version hummingbot-audit
fi

log "Starting: ${SVCS[*]}"
compose up -d "${SVCS[@]}"

if [ "${RIMBAUD_BOOTSTRAP:-1}" = "0" ] && [ -f "$ROOT/docker-compose.v2-sleep.yml" ]; then
  log "Creating V2 sleepers STOPPED (profile v2-sleep, no start, no --remove-orphans)"
  compose -f docker-compose.yml -f docker-compose.v2-sleep.yml --profile v2-sleep create || \
    warn "V2 sleeper create skipped (optional)"
fi

log "Waiting for dashboard :5001"
ok=0
for _ in $(seq 1 40); do
  if curl -sf -o /dev/null --max-time 2 http://127.0.0.1:5001/ 2>/dev/null; then
    ok=1
    break
  fi
  sleep 2
done
if [ "$ok" -eq 1 ]; then
  log "Dashboard up  →  http://127.0.0.1:5001/"
else
  warn "Dashboard not answering yet — docker logs hummingbot-dashboard"
fi

compose ps
