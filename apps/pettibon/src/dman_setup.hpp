// Copyright 2026 rimbaud.io
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#pragma once

#include <string>

// DMan Maker only. The daemon reads the status file. It does not call Hyperliquid.
// Returns 1 when the controller snapshot changed.
int dman_observe(const std::string& conf);

// Replay exit knobs on the entries DMan Maker already took.
// Returns the number of replay rows written, or 0 when nothing needed a rewrite.
int dman_score();

// One-shot 15-minute candles for BTC, ETH, LTC, and TAO. Not used by the daemon.
// Returns the number of candles stored.
int dman_backfill_candles();
