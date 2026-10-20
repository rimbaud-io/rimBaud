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

# Remove the Spring systemd unit. Does not touch Flask, hunters, or conf/.

set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
MODE=auto
for a in "$@"; do
  case "$a" in
    --user) MODE=user ;;
    --system) MODE=system ;;
  esac
done

if [[ "$MODE" == "auto" ]]; then
  if [[ -f ${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/spring.service ]]; then MODE=user
  elif [[ -f /etc/systemd/system/spring.service ]]; then MODE=system
  else echo "no spring.service installed"; exit 0; fi
fi

if [[ "$MODE" == "system" ]]; then
  SYSTEMCTL=(systemctl)
  SUDO=()
  UNIT=/etc/systemd/system/spring.service
  if [[ ! -w /etc/systemd/system ]]; then SUDO=(sudo); fi
else
  SYSTEMCTL=(systemctl --user)
  SUDO=()
  UNIT=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/spring.service
fi

"${SYSTEMCTL[@]}" stop spring.service 2>/dev/null || true
"${SYSTEMCTL[@]}" disable spring.service 2>/dev/null || true
"${SUDO[@]}" rm -f "$UNIT"
"${SYSTEMCTL[@]}" daemon-reload
echo "Spring daemon removed ($MODE). Flask and hunters untouched."
echo "Command-mode Spring can still be started from springctl."
