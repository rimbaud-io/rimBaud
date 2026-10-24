<!--
Copyright 2026 Freiquell e.V., Fichtestraße 1-4, 08451 Crimmitschau, GERMANY www.rimbaud.io

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

# rimBaud

rimBaud is the desk for automatic trading. Hummingbot is the engine underneath. This tree is only the rimBaud source. Wallets stay on the machine that runs the hunters.

Five applications:

```
apps/matrix/     the screen. Native desk, chart, and the web wire.
apps/spring/     the brain on port 5004. Reads and writes the hunter files.
apps/meta/       the mothership. Lists the matrices. Does not close a clip.
apps/pettibon/   the saved trade book for one hunter.
apps/archy/      the version window. Grok bumps g15, g16. The operator levels up.
```

Around them:

```
hunters/         the trader scripts that run inside Hummingbot.
docker-windows/  Windows download. Docker Desktop runs Hummingbot, the hunters, and Spring. Start with up.bat or up.sh. That asks for the risk notice first.
install/         the machine installer and the Mint USB image. Hunters start only after the risk notice is accepted.
vpn/             the WireGuard link between matrices.
services/        condor (telegram gauge) and the version page.
tools/           wallet creator and the telegram sender.
third_party/     nlohmann JSON, MIT.
website/         the public pages for rimbaud.io. The forum is not in this folder.
```

## Where it runs

This folder is the copy for GitHub. The running machine still uses the live folders. Nothing here is what the hunters execute.

| This tree | Live folder |
| --- | --- |
| `apps/matrix/desk` | `freiquell_c++` |
| `apps/matrix/web` | `dashboard` |
| `apps/spring` | `freiquell_server_c++` |
| `apps/meta` | `rimbaud_meta_c++` |
| `apps/pettibon` | `pettibon_c++` |
| `apps/archy` | `rimbaud-archy` |
| `hunters` | `scripts` (rimBaud scripts only) |
| `install` | `install_new` |
| `website` | `website/rimbaud-site` |

The installer scripts still name those live folders, because that is where a machine runs them.

## What is not in this tree

Hummingbot itself, `conf/`, wallets, secrets, logs, and databases. The install password is not in the preseed. Build output and `node_modules` are not here. The forum and the website gate hash are not in this tree.

## License

Apache License 2.0. See `LICENSE` and `NOTICE`.
