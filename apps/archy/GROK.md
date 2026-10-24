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

# Grok rule — rimbaud-archy

Grok loads the repo-root `AGENTS.md` at the start of every session. This file is not loaded by itself.

After any code edit in this repo, bump the grok suffix. Never LEVEL UP.

```bash
./rimbaud-archy --bump-g "short why" "file1,file2"
```

`0.1` → `0.1g1` → `0.1g2`. Operator raises `0.1` → `0.2` only in the Archy window (two-click LEVEL UP).

Do not call `--operator-prepare-github` or `--operator-send-github`. The TO GITHUB button copies, audits, and sends. A bump does not push.

`github_folders.tsv` is the GitHub folder column. `github_files.tsv` is the column beside each root file. Add a row for a new public folder or a new root file. The publisher writes `ABOUT.md` and commits that folder with the row's line. A root file is committed with its own line. The repository banner keeps the Archy version and the change. Do not put the bump sentence on every folder or root file.

