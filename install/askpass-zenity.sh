#!/bin/sh
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

# Graphical sudo askpass. Prints the password to stdout for sudo -A.
# RIMBAUD_ASK_TITLE and RIMBAUD_ASK_TEXT override the USB-deploy wording.
TITLE="${RIMBAUD_ASK_TITLE:-rimBaud USB}"
if [ -n "${RIMBAUD_ASK_TEXT:-}" ]; then
  TEXT="$RIMBAUD_ASK_TEXT"
else
  TEXT="Sudo password to write the deploy stick.
No wallets or live keys are copied."
fi
exec zenity --password --title="$TITLE" --text="$TEXT"
