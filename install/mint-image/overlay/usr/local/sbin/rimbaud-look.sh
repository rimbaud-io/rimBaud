#!/bin/bash
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

# Apply rimBaud look. "theme" = chroot/root (clone Mint-Y-Dark). "user" = session gsettings.
# Does not CLOSE/OPEN HL.
set -euo pipefail

theme_clone() {
  local src dst
  src=""
  for c in /usr/share/themes/Mint-Y-Dark /usr/share/themes/Mint-Y-Darker \
           /usr/share/themes/Mint-Y /usr/share/themes/Adwaita-dark; do
    [ -d "$c" ] && src="$c" && break
  done
  [ -n "$src" ] || { echo "no dark GTK theme to clone"; return 0; }
  dst=/usr/share/themes/rimBaud
  mkdir -p "$dst"
  # Keep our override files, clone the rest from Mint-Y-Dark.
  local keep
  keep="$(mktemp -d)"
  if [ -d "$dst/cinnamon" ] || [ -d "$dst/gtk-3.0" ]; then
    cp -a "$dst/cinnamon" "$keep/" 2>/dev/null || true
    cp -a "$dst/gtk-3.0" "$keep/" 2>/dev/null || true
  fi
  rsync -aL --delete --exclude 'rimbaud-overrides.css' --exclude 'gtk-overrides.css' "$src/" "$dst/"
  if [ -f "$dst/index.theme" ]; then
    sed -i \
      -e 's/^Name=.*/Name=rimBaud/' \
      -e 's/^GtkTheme=.*/GtkTheme=rimBaud/' \
      -e 's/^MetacityTheme=.*/MetacityTheme=rimBaud/' \
      -e 's/^Comment=.*/Comment=internal rimBaud appliance/' \
      "$dst/index.theme" || true
  else
    cat >"$dst/index.theme" <<'EOF'
[Desktop Entry]
Type=X-GNOME-Metatheme
Name=rimBaud
Comment=internal rimBaud appliance
Encoding=UTF-8

[X-GNOME-Metatheme]
GtkTheme=rimBaud
MetacityTheme=rimBaud
IconTheme=Mint-Y-Dark
CursorTheme=Bibata-Modern-Classic
ButtonLayout=close,minimize,maximize:
EOF
  fi
  mkdir -p "$dst/cinnamon" "$dst/gtk-3.0" "$dst/gtk-4.0"
  [ -d "$keep/cinnamon" ] && cp -a "$keep/cinnamon/." "$dst/cinnamon/" || true
  [ -d "$keep/gtk-3.0" ] && cp -a "$keep/gtk-3.0/." "$dst/gtk-3.0/" || true
  rm -rf "$keep"
  local ov=/usr/share/themes/rimBaud/cinnamon/rimbaud-overrides.css
  if [ -f "$ov" ]; then
    for css in "$dst/cinnamon/cinnamon.css"; do
      [ -f "$css" ] || continue
      if ! grep -q 'rimBaud chrome' "$css" 2>/dev/null; then
        printf '\n/* --- rimBaud --- */\n' >>"$css"
        cat "$ov" >>"$css"
      fi
    done
  fi
  local gov=/usr/share/themes/rimBaud/gtk-3.0/gtk-overrides.css
  if [ -f "$gov" ]; then
    for css in "$dst/gtk-3.0/gtk.css" "$dst/gtk-3.0/gtk-dark.css" "$dst/gtk-4.0/gtk.css"; do
      [ -f "$css" ] || continue
      if ! grep -q 'rimBaud GTK' "$css" 2>/dev/null; then
        printf '\n/* --- rimBaud --- */\n' >>"$css"
        cat "$gov" >>"$css"
      fi
    done
    [ -f "$dst/gtk-4.0/gtk.css" ] || cp -a "$gov" "$dst/gtk-4.0/gtk.css"
  fi
  echo "rimBaud theme cloned from $src"
}

user_look() {
  command -v gsettings >/dev/null 2>&1 || exit 0
  local flag="${HOME:-/tmp}/.config/rimbaud/look-applied"
  mkdir -p "$(dirname "$flag")"
  gsettings set org.cinnamon.desktop.background picture-uri 'file:///usr/share/backgrounds/rimbaud/desktop.png' 2>/dev/null || true
  gsettings set org.cinnamon.desktop.background picture-options 'zoom' 2>/dev/null || true
  gsettings set org.cinnamon.desktop.background primary-color '#020804' 2>/dev/null || true
  gsettings set org.cinnamon.desktop.background secondary-color '#020804' 2>/dev/null || true
  gsettings set org.cinnamon.desktop.screensaver picture-uri 'file:///usr/share/backgrounds/rimbaud/desktop.png' 2>/dev/null || true
  if [ -d /usr/share/themes/rimBaud ]; then
    gsettings set org.cinnamon.theme name 'rimBaud' 2>/dev/null || true
    gsettings set org.cinnamon.desktop.interface gtk-theme 'rimBaud' 2>/dev/null || true
    gsettings set org.cinnamon.desktop.wm.preferences theme 'rimBaud' 2>/dev/null || \
      gsettings set org.cinnamon.desktop.wm.preferences theme 'Mint-Y-Dark' 2>/dev/null || true
  else
    gsettings set org.cinnamon.theme name 'Mint-Y-Dark' 2>/dev/null || true
    gsettings set org.cinnamon.desktop.interface gtk-theme 'Mint-Y-Dark' 2>/dev/null || true
  fi
  gsettings set org.cinnamon.desktop.interface icon-theme 'Mint-Y-Dark' 2>/dev/null || true
  gsettings set org.cinnamon.desktop.interface gtk-color-scheme 'prefer-dark' 2>/dev/null || true
  gsettings set org.gnome.desktop.interface color-scheme 'prefer-dark' 2>/dev/null || true
  gsettings set org.gnome.desktop.interface gtk-theme 'rimBaud' 2>/dev/null || true
  gsettings set org.cinnamon panels-enabled "['1:0:top']" 2>/dev/null || true
  gsettings set org.cinnamon.desktop.interface clock-show-date true 2>/dev/null || true
  gsettings set org.nemo.desktop computer-icon-visible false 2>/dev/null || true
  gsettings set org.nemo.desktop home-icon-visible false 2>/dev/null || true
  gsettings set org.nemo.desktop trash-icon-visible false 2>/dev/null || true
  gsettings set org.nemo.desktop network-icon-visible false 2>/dev/null || true
  gsettings set org.nemo.desktop volumes-visible false 2>/dev/null || true
  gsettings set org.cinnamon favorite-apps "['rimbaud-meta.desktop', 'nemo.desktop', 'org.gnome.Terminal.desktop', 'cinnamon-settings.desktop']" 2>/dev/null || true
  python3 - <<'PY' 2>/dev/null || true
import glob, json, os
home = os.path.expanduser("~")
for p in glob.glob(os.path.join(home, ".cinnamon/configs/menu@cinnamon.org/*.json")):
    try:
        j = json.load(open(p))
    except Exception:
        continue
    changed = False
    for key, val in (("menu-custom", True), ("menu-label", "rimBaud")):
        if key in j and isinstance(j[key], dict) and "value" in j[key]:
            if j[key]["value"] != val:
                j[key]["value"] = val
                changed = True
    if changed:
        json.dump(j, open(p, "w"), indent=2)
PY
  date -u +'%Y-%m-%d %H:%M:%S UTC' >"$flag"
}

case "${1:-user}" in
  theme) theme_clone ;;
  user) user_look ;;
  both) theme_clone; user_look ;;
  *) echo "usage: $0 theme|user|both" >&2; exit 2 ;;
esac
