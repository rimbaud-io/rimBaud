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

# Fail if the unpacked tree cannot run hunters + Spring.
set -euo pipefail
# shellcheck source=install_new/_common.sh
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

missing=0
need() {
  if [ ! -e "$ROOT/$1" ]; then
    warn "missing $1"
    missing=1
  fi
}

need docker-compose.yml
need scripts/hl_copy_trader.py
need scripts/pmm_simple_trader.py
need scripts/v2_with_controllers.py
need controllers/market_making/pmm_simple.py
need conf/wire_registry.json
need conf/scripts/hl_copy_trader.yml
need dashboard/app.py
need dashboard/Dockerfile
need dashboard/wire/dist
need condor/Dockerfile
need audit/Dockerfile
need version/Dockerfile
need freiquell_server_c++/src/brain.cpp
need freiquell_c++/src/ui.cpp
need rimbaud_meta_c++/src/main.cpp
need rimbaud_vpn/rimbaud-vpn
need rimbaud_vpn/spring-proxy.py
need install_new/make-usb.sh
need install_new/bootstrap.sh
need install_new/checklist.py
need install_new/services.bootstrap.list
need rimbaud_meta_c++/src/desk.cpp
need freiquell_server_c++/src/ctl.cpp
need docker-compose.v2-sleep.yml

while read -r f; do
  [ -n "$f" ] || continue
  need "$f"
done < <(overlay_files)

if [ "$missing" -ne 0 ]; then
  die "kit is incomplete — re-run pack.sh on the source machine"
fi
log "kit files OK"
