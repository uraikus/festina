// claude.md #199 Phase 5: `thread`'s own private sqlite handle, under
// real ASan/LeakSanitizer AND ThreadSanitizer pressure -- the same bar
// claude.md #195/#196/#197/#198 already set for every earlier round of
// this feature. Structured deliberately to maximize REAL overlap
// between the main program's own sqlite() traffic (against its own
// database) and a thread's own sqlite() traffic (against ITS own,
// separate database), both hitting the shared, process-wide
// prepared-statement cache registry (claude.md #113's own
// g_cached_stmts, in festina_runtime.c) from two different OS threads
// at the same time -- exactly the race festina_set_stmt_cache_hooks
// (registered by festina_register_thread_hooks, festina_runtime_
// thread.c) exists to close. `postMessage(x)` only ever ENQUEUES (it
// never blocks on the reply), so the main program's own `while` loop
// below keeps issuing its OWN cached sqlite() calls the entire time
// dbWorker is busy processing the queue that loop is filling --
// genuine concurrent sqlite traffic on two separate database FILES,
// racing on the one thing they still share.

DatabaseURL = 'main_db_churn.sqlite'
table MainCounter { n:int }

table Ping { n:int }
thread dbWorker {
    DatabaseURL = 'worker_db_churn.sqlite'
    on message(p:int) {
        sqlite('INSERT INTO Ping (n) VALUES (?)', [p])
        int total = sqliteInt('SELECT count(*) FROM Ping')
        postMessage(total)
    }
}

int DB_TOTAL = 2000
int dbRepliesSeen = 0
int dbSum = 0
int mainInserted = 0

void func maybeDone() {
    if dbRepliesSeen >= DB_TOTAL && mainInserted >= DB_TOTAL {
        log('db churn done')
        log(dbRepliesSeen)
        log(dbSum)
        log(mainInserted)
        close(0)
    }
}

void func onDbReply(x:int) {
    dbRepliesSeen = dbRepliesSeen + 1
    dbSum = dbSum + x
    maybeDone()
}
dbWorker.onMessage(void (x:int) => onDbReply(x))

int i = 0
while i < DB_TOTAL {
    dbWorker.postMessage(i)
    // Main's OWN concurrent cached sqlite() traffic, overlapping with
    // dbWorker's own -- this is the whole point of this file (see the
    // top comment).
    sqlite('INSERT INTO MainCounter (n) VALUES (?)', [i])
    int mainTotal = sqliteInt('SELECT count(*) FROM MainCounter')
    if mainTotal != i + 1 {
        log('main counter mismatch')
        close(1)
    }
    mainInserted = mainInserted + 1
    i = i + 1
}
maybeDone()
