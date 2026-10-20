#!/usr/bin/env bash
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

# Thin wrapper — VPN control plane 10.66.80.0/24. Never 0.0.0.0/0.
# Hunters keep this box's public IP (new HL 1200/60s tokens).
set -euo pipefail
# shellcheck source=install_new/_common.sh
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

VPN="$ROOT/rimbaud_vpn/rimbaud-vpn"
[ -x "$VPN" ] || die "missing $VPN — re-pack the kit"
chmod +x "$ROOT/rimbaud_vpn/spring-proxy.py" 2>/dev/null || true
exec "$VPN" "$@"
