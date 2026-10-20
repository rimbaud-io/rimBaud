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

# First graphical login on a rimBaud Mint install.
# Headless first-boot has no terminal, so it cannot accept the risk notice.
set -euo pipefail

INSTALL=/opt/rimbaud/install_new
MARK=/var/lib/rimbaud/risk-login.done

if [ ! -f "$INSTALL/risk-confirm.sh" ]; then
  exit 0
fi
if [ -f "$MARK" ]; then
  exit 0
fi

if ! bash "$INSTALL/risk-confirm.sh"; then
  echo "The risk notice was not accepted. Hunters were not started."
  read -r _ || true
  exit 1
fi

if [ -f "$INSTALL/install.sh" ]; then
  if [ "$(id -u)" -eq 0 ]; then
    bash "$INSTALL/install.sh" all || failed=1
  else
    sudo -E bash "$INSTALL/install.sh" all || failed=1
  fi
  if [ "${failed:-0}" = 1 ]; then
    echo "rimBaud install did not finish. Run: sudo bash $INSTALL/install.sh all"
    read -r _ || true
    exit 1
  fi
fi

if [ "$(id -u)" -eq 0 ]; then
  mkdir -p /var/lib/rimbaud
  touch "$MARK"
else
  sudo mkdir -p /var/lib/rimbaud
  sudo touch "$MARK"
fi
