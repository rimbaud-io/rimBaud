# Copyright 2026 Laurence Sterne
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

CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -I../../third_party -Isrc
LDFLAGS ?= -lpthread /lib/x86_64-linux-gnu/libsqlite3.so.0

SRC = src/main.cpp src/store.cpp src/collect.cpp src/dman_setup.cpp
BIN = pettibon

.PHONY: all clean

all: $(BIN)

$(BIN): $(SRC) src/store.hpp src/collect.hpp src/dman_setup.hpp src/sqlite3_min.h
	$(CXX) $(CXXFLAGS) -o $@ $(SRC) $(LDFLAGS)

clean:
	rm -f $(BIN)
