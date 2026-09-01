// claude.md #211: `regex()`/`mkdir()`/`ls()`/`exec(args)` unblocked
// inside a thread body -- real, concurrent, at-volume proof (not just
// citing the doc comment) that each is genuinely safe from TWO
// threads running simultaneously:
//
// - regex(): each thread's own call site gets its own memoization
//   slot (a per-CALL-SITE codegen global, confirmed by reading
//   _regex_memo_slots/festina_regex_compile_memo) -- aWorker and
//   bWorker each compile a DIFFERENT pattern, repeatedly, at real
//   volume, so a slot accidentally shared between the two call sites
//   would show up as a wrong match result, not just a crash.
// - mkdir()/ls(): thin, purely local POSIX wrappers -- each thread
//   creates and lists its OWN directory, never the other's, so this
//   is really proving "no accidental shared state", not testing
//   filesystem semantics themselves.
// - exec(args) (the 1-argument, blocking form): fork() in a
//   multithreaded process only duplicates the calling thread into the
//   child, which execvp()'s immediately after -- both threads spawn
//   real child processes CONCURRENTLY, at real volume, the exact
//   "just as safe from a worker thread as from main" claim
//   festina_run_argv's own doc comment makes. Called every 50th
//   iteration only (not every message) -- real fork()/exec()/
//   waitpid() is comparatively expensive, and this only needs to
//   happen enough times to be a genuine concurrent-fork proof, not
//   every single message.
//
// Correctness is COUNT-based (a combined reply counter, the same
// convention thread_pool_churn.f/thread_private_func_churn.f already
// established) plus a `failures` counter that must stay 0 -- a
// mismatched regex result or a non-zero exec() exit code posts back a
// failure marker instead of success, so a real cross-thread mix-up
// fails loudly (`close(1)`) instead of just silently miscounting.

int TOTAL = 500
int repliesSeen = 0
int failures = 0

on message(worker:thread, msg:int) {
    repliesSeen = repliesSeen + 1
    if msg == 0 {
        failures = failures + 1
    }
    if repliesSeen >= TOTAL * 2 {
        log('wider builtins churn done')
        log(repliesSeen)
        log(failures)
        if failures > 0 {
            close(1)
        }
        close(0)
    }
}

thread aWorker {
    int total = 500
    on load() {
        mkdir('stress_dir_a')
        int i = 0
        while i < total {
            regex r = /^apple/
            bool m1 = r.test('applesauce')
            bool m2 = r.test('banana')
            arr[text] entries = ls('.')
            bool foundDir = false
            int j = 0
            while j < entries.length {
                if entries[j] == 'stress_dir_a' {
                    foundDir = true
                }
                j = j + 1
            }
            bool ok = m1 && !m2 && foundDir
            if i % 50 == 0 {
                int code = exec(['true'])
                if code != 0 {
                    ok = false
                }
            }
            if ok {
                postMessage(1)
            } else {
                postMessage(0)
            }
            i = i + 1
        }
    }
}

thread bWorker {
    int total = 500
    on load() {
        mkdir('stress_dir_b')
        int i = 0
        while i < total {
            regex r = /^banana/
            bool m1 = r.test('bananasplit')
            bool m2 = r.test('apple')
            arr[text] entries = ls('.')
            bool foundDir = false
            int j = 0
            while j < entries.length {
                if entries[j] == 'stress_dir_b' {
                    foundDir = true
                }
                j = j + 1
            }
            bool ok = m1 && !m2 && foundDir
            if i % 50 == 0 {
                int code = exec(['true'])
                if code != 0 {
                    ok = false
                }
            }
            if ok {
                postMessage(1)
            } else {
                postMessage(0)
            }
            i = i + 1
        }
    }
}
