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

# watch_copy_trades.sh
# Live colour-coded view of the copy trader log.
# Usage: bash watch_copy_trades.sh [log_path]

LOG="${1:-/home/operator/hummingbird/hummingbot/logs/copy_trades.log}"

if [ ! -f "$LOG" ]; then
    echo "Log not found yet: $LOG"
    echo "Waiting for bot to start and create the log..."
    while [ ! -f "$LOG" ]; do sleep 1; done
fi

echo "Watching $LOG  (Ctrl+C to stop)"
echo ""

tail -n 80 -f "$LOG"
