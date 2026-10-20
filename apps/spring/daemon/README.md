<!--
Copyright 2026 Laurence Sterne www.rimbaud.io

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Spring daemon

Keep Spring on `:5004` across logouts, reboots, and 10h chat sessions.
Flask `:5001` and the hummingbots stay independent. This unit never closes Hyperliquid clips.

## Other machines

```bash
cd freiquell_server_c++
make
./daemon/install.sh          # system unit if possible, else --user
# or:
./daemon/install.sh --user   # no root; enable lingering
./daemon/install.sh --system # /etc/systemd/system (sudo)
```

Env: `FREIQUELL_CONF`, `SPRING_PORT` (default 5004, **never 5001**), `SPRING_BIN`.

## springctl

```bash
./springctl
```

Switch **DAEMON** / **COMMAND** (only one binds `:5004`). Start / Stop / Restart follow the switch. The bottom pane tails the active log.

- DAEMON → `systemctl start/stop/restart spring`
- COMMAND → fork `./spring --debug` (dies with the parent session)

Source is saved in `~/.config/freiquell/springctl.source`.

## Logs

| install | file |
|---|---|
| `--system` | `/var/log/spring/spring.log` |
| `--user` | `~/.local/share/spring/spring.log` |
| COMMAND | `/tmp/freiquell-spring.log` |

```bash
tail -f ~/.local/share/spring/spring.log
# or
journalctl --user -u spring -f
```

## Remove

```bash
./daemon/uninstall.sh
```
