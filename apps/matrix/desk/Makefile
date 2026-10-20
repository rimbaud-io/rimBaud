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
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wno-unused-parameter -I../../../third_party -Isrc
LDFLAGS ?= -lX11 -lpthread

SRC = src/main.cpp src/http.cpp src/api.cpp src/ui.cpp
BIN = rimbaud-desk
CHART = rimbaud-chart

.PHONY: all clean run headless

all: $(BIN) $(CHART)

$(BIN): $(SRC) src/http.hpp src/api.hpp src/ui.hpp ../../../third_party/nlohmann/json.hpp
	$(CXX) $(CXXFLAGS) -o $@ $(SRC) $(LDFLAGS)

$(CHART): src/chart.cpp src/http.cpp src/https.cpp src/http.hpp src/https.hpp ../../../third_party/nlohmann/json.hpp
	$(CXX) $(CXXFLAGS) -o $@ src/chart.cpp src/http.cpp src/https.cpp $(LDFLAGS) -lssl -lcrypto

run: $(BIN)
	./$(BIN)

headless: $(BIN)
	./$(BIN) --headless

clean:
	rm -f $(BIN) $(CHART) freiquell-desk freiquell-chart
