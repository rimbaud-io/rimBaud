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

# User systemd unit for pettibon. Does not restart Spring or hunters.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
REPO=$(cd "$ROOT/.." && pwd)
CONF=${FREIQUELL_CONF:-$REPO/conf}
make -C "$ROOT" all -j"$(nproc 2>/dev/null || echo 2)"
BIN=$(readlink -f "$ROOT/pettibon")
CONF=$(readlink -f "$CONF")
DATA="$REPO/pettibon_data"
mkdir -p "$DATA"
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
LOG_DIR=${XDG_DATA_HOME:-$HOME/.local/share}/pettibon
mkdir -p "$UNIT_DIR" "$LOG_DIR"
cat > "$UNIT_DIR/pettibon.service" << EOF
[Unit]
Description=Pettibon trade and action log
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$BIN --conf $CONF --db $DATA/pettibon.db --interval 20
Restart=on-failure
RestartSec=3
TimeoutStopSec=8
StandardOutput=append:$LOG_DIR/pettibon.log
StandardError=append:$LOG_DIR/pettibon.log
SyslogIdentifier=pettibon

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now pettibon.service
systemctl --user --no-pager --full status pettibon.service | head -n 20
