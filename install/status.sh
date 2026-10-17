#!/usr/bin/env bash
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

set -euo pipefail
# shellcheck source=install_new/_common.sh
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

echo "ROOT  $ROOT"
echo "user  $REAL_USER"
echo

if docker_ok; then
  mapfile -t SVCS < <(matrix_services)
  compose ps "${SVCS[@]}" 2>/dev/null || compose ps
  echo
  if curl -sf -o /dev/null --max-time 3 http://127.0.0.1:5001/; then
    echo "dashboard  http://127.0.0.1:5001/   OK"
  else
    echo "dashboard  http://127.0.0.1:5001/   down"
  fi
else
  warn "Docker engine is down"
fi

echo
if curl -sf --max-time 2 http://127.0.0.1:5004/api/health >/dev/null; then
  echo "spring     http://127.0.0.1:5004/api/health   OK"
  curl -sS --max-time 2 http://127.0.0.1:5004/api/health || true
  echo
else
  echo "spring     :5004   down  (./install_new/spring.sh)"
fi

echo
if ip link show wg-rimbaud >/dev/null 2>&1; then
  echo "vpn       wg-rimbaud UP"
  ip -4 -o addr show wg-rimbaud | awk '{print "vpn addr  "$4}'
else
  echo "vpn       down  (sudo ./install_new/vpn.sh join FILE   or   init on the hub)"
fi
if [ -x "$ROOT/freiquell_server_c++/springctl" ]; then
  "$ROOT/freiquell_server_c++/springctl" --status 2>/dev/null || true
fi
echo
command -v pgrep >/dev/null && pgrep -af 'rimbaud-desk|spring --debug|springctl' | grep -v grep || true
