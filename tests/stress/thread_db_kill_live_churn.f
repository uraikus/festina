// claude.md #207: a thread's own private sqlite handle, across MANY
// kill()/live() cycles in a row, under real ASan/LeakSanitizer
// pressure -- the actual leak todo.md described: on_load
// unconditionally opens a fresh sqlite3* on every respawn, and until
// this fix nothing ever closed the PREVIOUS one first, so each cycle
// leaked one sqlite3* plus its own open fd. tests/test_codegen.py's
// own test_kill_then_live_reopens_a_genuinely_working_database_handle
// pins the behavioral half (a reopened handle is genuinely usable);
// this file is the leak-freedom half, the one only a sanitizer run can
// actually confirm. kill()/live() are both BLOCKING (kill() joins;
// live() spawns and only then calls back), so this whole loop is
// deterministic, exactly like the compile-and-run test above -- no
// message-passing race to reason about.

thread worker {
    DatabaseURL = 'kill_live_db_churn.sqlite'
}

int CYCLES = 500
int cycle = 0
while cycle < CYCLES {
    worker.kill()
    worker.live(void (ok:bool) => log(''))
    cycle = cycle + 1
}
log('done')
close(0)
