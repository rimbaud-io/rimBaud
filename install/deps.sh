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

# Linux Mint / Ubuntu: Docker Engine + Compose + C++ toolchain for Spring/desk.
# Mint has no Docker apt repo — use Ubuntu's with UBUNTU_CODENAME.
set -euo pipefail
# shellcheck source=install_new/_common.sh
. "$(cd "$(dirname "$0")" && pwd)/_common.sh"

need_linux() {
  [ "$(uname -s)" = Linux ] || die "rimBaud install is for Linux (got $(uname -s))"
}

apt_install() {
  local pkgs=("$@")
  [ "${#pkgs[@]}" -gt 0 ] || return 0
  log "apt install ${pkgs[*]}"
  export DEBIAN_FRONTEND=noninteractive
  as_root apt-get update -y
  as_root apt-get install -y "${pkgs[@]}"
}

docker_ubuntu_codename() {
  # shellcheck disable=SC1091
  . /etc/os-release
  if [ -n "${UBUNTU_CODENAME:-}" ]; then
    echo "$UBUNTU_CODENAME"
    return
  fi
  case "${VERSION_CODENAME:-}" in
    virginia|wilma|xia|zara) echo noble ;;
    vanessa|vera|victoria) echo jammy ;;
    una|uma) echo jammy ;;
    jammy|noble|focal|bookworm|bullseye) echo "${VERSION_CODENAME}" ;;
    *) echo "${VERSION_CODENAME:-jammy}" ;;
  esac
}

install_docker_mint() {
  # shellcheck disable=SC1091
  . /etc/os-release
  local codename arch
  codename="$(docker_ubuntu_codename)"
  arch="$(dpkg --print-architecture)"
  log "Docker apt repo = Ubuntu $codename (host ID=${ID:-?} VERSION_CODENAME=${VERSION_CODENAME:-?})"
  export DEBIAN_FRONTEND=noninteractive
  as_root apt-get update -y
  as_root apt-get install -y ca-certificates curl gnupg
  as_root install -m 0755 -d /etc/apt/keyrings
  if [ ! -f /etc/apt/keyrings/docker.asc ]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | as_root tee /etc/apt/keyrings/docker.asc >/dev/null
    as_root chmod a+r /etc/apt/keyrings/docker.asc
  fi
  echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable" \
    | as_root tee /etc/apt/sources.list.d/docker.list >/dev/null
  as_root apt-get update -y
  as_root apt-get install -y \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  as_root systemctl enable --now docker
}

need_linux

pkgs=()
command -v curl >/dev/null 2>&1 || pkgs+=(curl ca-certificates)
command -v python3 >/dev/null 2>&1 || pkgs+=(python3)
command -v rsync >/dev/null 2>&1 || pkgs+=(rsync)
command -v make >/dev/null 2>&1 || pkgs+=(build-essential)
command -v g++ >/dev/null 2>&1 || pkgs+=(build-essential)
dpkg -s libx11-dev >/dev/null 2>&1 || pkgs+=(libx11-dev)
dpkg -s libssl-dev >/dev/null 2>&1 || pkgs+=(libssl-dev)
dpkg -s pkg-config >/dev/null 2>&1 || pkgs+=(pkg-config)
if ! command -v wg >/dev/null 2>&1 || ! command -v wg-quick >/dev/null 2>&1; then
  pkgs+=(wireguard wireguard-tools)
fi
if [ "${#pkgs[@]}" -gt 0 ] && command -v apt-get >/dev/null 2>&1; then
  apt_install "${pkgs[@]}"
fi

if docker_ok; then
  log "Docker already running"
else
  if ! command -v docker >/dev/null 2>&1; then
    [ -f /etc/os-release ] || die "need /etc/os-release"
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}:${ID_LIKE:-}" in
      linuxmint:*|ubuntu:*|debian:*|pop:*|*ubuntu*)
        install_docker_mint
        ;;
      *)
        die "no automatic Docker install for ID=${ID:-?} — install Docker Engine + compose plugin, then retry"
        ;;
    esac
  else
    as_root systemctl enable --now docker 2>/dev/null || true
  fi
fi

if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
  if ! id -nG "$SUDO_USER" | tr ' ' '\n' | grep -qx docker; then
    log "Adding $SUDO_USER to docker group"
    usermod -aG docker "$SUDO_USER"
    warn "log out and back in (or: newgrp docker) so $SUDO_USER can run docker without sudo"
  fi
elif [ "$(id -u)" -ne 0 ]; then
  if ! id -nG | tr ' ' '\n' | grep -qx docker; then
    log "Adding $REAL_USER to docker group"
    as_root usermod -aG docker "$REAL_USER"
    warn "log out and back in (or: newgrp docker) then re-run install"
  fi
fi

if docker_ok; then
  as_root docker version --format 'docker {{.Server.Version}}' || true
  compose version || true
  log "Docker is up"
else
  die "Docker installed but this shell cannot talk to the engine yet (re-login, then: $INSTALL_DIR/install.sh up)"
fi
