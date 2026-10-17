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

# Stop hunters + dashboard. Conf / logs / data stay. Does not flatten Hyperliquid clips.
set -euo pipefail
# shellcheck source=install_new/_common.sh
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

docker_ok || die "Docker engine is down"
mapfile -t SVCS < <(matrix_services)
log "Stopping: ${SVCS[*]}"
compose stop "${SVCS[@]}"
log "Stopped. Positions on Hyperliquid are untouched."
