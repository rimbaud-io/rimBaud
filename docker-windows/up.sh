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

# Start the Windows/Linux Docker bundle. The risk notice is required first.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
bash "$DIR/risk-confirm.sh"
export RIMBAUD_RISK_FILE="$(bash "$DIR/risk-confirm.sh" --path)"
if [ ! -f "$RIMBAUD_RISK_FILE" ]; then
  echo "The risk notice was not saved." >&2
  exit 1
fi
command -v docker >/dev/null 2>&1 || { echo "Docker is not installed." >&2; exit 1; }
cd "$DIR"
exec docker compose up --build "$@"
