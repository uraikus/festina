#ifndef SQLITE3_SHIM_H
#define SQLITE3_SHIM_H
/* Minimal hand-written declarations matching the stable sqlite3 C ABI.
   libsqlite3-dev headers aren't installed in this environment but the
   shared library (libsqlite3.so.0) is present and its C ABI has been
   stable for two decades, so we declare just what we use. */

typedef struct sqlite3 sqlite3;
typedef struct sqlite3_stmt sqlite3_stmt;
typedef long long sqlite3_int64;
typedef void (*sqlite3_destructor_type)(void*);
#define SQLITE_TRANSIENT ((sqlite3_destructor_type)-1)

#define SQLITE_OK 0
#define SQLITE_ROW 100
#define SQLITE_DONE 101

#define SQLITE_INTEGER 1
#define SQLITE_FLOAT   2
#define SQLITE_TEXT    3
#define SQLITE_BLOB    4
#define SQLITE_NULL    5

extern int sqlite3_open(const char* filename, sqlite3** db);
extern int sqlite3_close(sqlite3* db);
extern int sqlite3_prepare_v2(sqlite3* db, const char* sql, int nbyte,
                               sqlite3_stmt** stmt, const char** tail);
extern int sqlite3_step(sqlite3_stmt* stmt);
extern int sqlite3_finalize(sqlite3_stmt* stmt);
extern int sqlite3_reset(sqlite3_stmt* stmt);
extern int sqlite3_bind_double(sqlite3_stmt*, int, double);
extern int sqlite3_bind_int64(sqlite3_stmt*, int, sqlite3_int64);
extern int sqlite3_bind_text(sqlite3_stmt*, int, const char*, int, sqlite3_destructor_type);
extern int sqlite3_bind_null(sqlite3_stmt*, int);
extern int sqlite3_column_count(sqlite3_stmt*);
extern int sqlite3_column_type(sqlite3_stmt*, int);
extern double sqlite3_column_double(sqlite3_stmt*, int);
extern const unsigned char* sqlite3_column_text(sqlite3_stmt*, int);
extern const char* sqlite3_column_name(sqlite3_stmt*, int);
extern int sqlite3_changes(sqlite3*);
extern sqlite3_int64 sqlite3_last_insert_rowid(sqlite3*);
extern const char* sqlite3_errmsg(sqlite3*);
extern int sqlite3_exec(sqlite3*, const char* sql,
                         int (*callback)(void*, int, char**, char**),
                         void* arg, char** errmsg);

#endif
