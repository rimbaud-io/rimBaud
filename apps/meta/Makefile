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

# rimbaud-meta — console viewer; rimbaud-meta-desk — X11 meta + USB deploy.
# GET only. Never POSTs. Never touches hunters or live clips.
CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -I../../third_party -I../matrix/desk/src
LDFLAGS ?= -lpthread

BIN = rimbaud-meta
DESK = rimbaud-meta-desk
BACKUP = rimbaud-backup
SRC = src/main.cpp ../matrix/desk/src/http.cpp
DESK_SRC = src/desk.cpp ../matrix/desk/src/http.cpp

.PHONY: all clean run once

all: $(BIN) $(DESK) $(BACKUP)

$(BIN): $(SRC) ../matrix/desk/src/http.hpp
	$(CXX) $(CXXFLAGS) -o $@ $(SRC) $(LDFLAGS)

$(DESK): $(DESK_SRC) ../matrix/desk/src/http.hpp
	$(CXX) $(CXXFLAGS) -o $@ $(DESK_SRC) -lX11 $(LDFLAGS)

$(BACKUP): src/backup.cpp
	$(CXX) $(CXXFLAGS) -o $@ src/backup.cpp /lib/x86_64-linux-gnu/libsqlite3.so.0 $(LDFLAGS)

run: $(BIN)
	./$(BIN)

once: $(BIN)
	./$(BIN) --once

clean:
	rm -f $(BIN) $(DESK) $(BACKUP)
