// Copyright 2026 Laurence Sterne www.rimbaud.io
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

struct PassResult {
    int matrices_up = 0;
    int matrices_down = 0;
    int trades_new = 0;
    int actions_new = 0;
    std::string note;
};

// One pass. Reads Spring /api/home for each configured matrix and the local
// trade files. Does not call Hyperliquid and does not write hunter files.
PassResult collect_once(const std::string& conf);
