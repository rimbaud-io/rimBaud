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

# LAN test: http://THIS_LAN_IP:8787/  — iPhone on same Wi-Fi.
# Production: set GATEWAY_TOKEN, put nginx/caddy on the static IP in front.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export GATEWAY_TOKEN="${GATEWAY_TOKEN:-}"
export HUB_SPRING="${HUB_SPRING:-http://127.0.0.1:5004}"
export GATEWAY_BIND="${GATEWAY_BIND:-0.0.0.0}"
export GATEWAY_PORT="${GATEWAY_PORT:-8787}"
exec python3 "$HERE/gateway.py"
