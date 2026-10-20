// Copyright 2026 Freiquell e.V., Fichtestraße 1-4, 08451 Crimmitschau, GERMANY www.rimbaud.io
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
/* Just the SQLite calls Spring uses. The system has libsqlite3.so.0 and no dev headers. */
#ifdef __cplusplus
extern "C" {
#endif

typedef struct sqlite3 sqlite3;
typedef struct sqlite3_stmt sqlite3_stmt;
typedef void (*sqlite3_destructor_type)(void*);

#define SQLITE_OK 0
#define SQLITE_ROW 100
#define SQLITE_DONE 101
#define SQLITE_OPEN_READWRITE 0x00000002
#define SQLITE_TRANSIENT ((sqlite3_destructor_type)-1)

int sqlite3_open_v2(const char* filename, sqlite3** ppDb, int flags, const char* zVfs);
int sqlite3_close(sqlite3*);
int sqlite3_exec(sqlite3*, const char* sql,
                 int (*callback)(void*, int, char**, char**), void*, char** errmsg);
void sqlite3_free(void*);
int sqlite3_busy_timeout(sqlite3*, int ms);
int sqlite3_prepare_v2(sqlite3*, const char* sql, int nByte, sqlite3_stmt** ppStmt, const char** pzTail);
int sqlite3_bind_text(sqlite3_stmt*, int, const char*, int, sqlite3_destructor_type);
int sqlite3_step(sqlite3_stmt*);
int sqlite3_finalize(sqlite3_stmt*);
const unsigned char* sqlite3_column_text(sqlite3_stmt*, int iCol);

#ifdef __cplusplus
}
#endif
