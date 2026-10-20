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

# rimbaud-archy — version desk. Grok --bump-g. Operator LEVEL UP in the window.
CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -I../../third_party
LDFLAGS ?= -lX11 -lpthread

BIN = rimbaud-archy
SRC = src/main.cpp src/desk.cpp

.PHONY: all clean run

all: $(BIN)

$(BIN): $(SRC)
	$(CXX) $(CXXFLAGS) -o $@ $(SRC) $(LDFLAGS)

run: $(BIN)
	./$(BIN)

clean:
	rm -f $(BIN)
