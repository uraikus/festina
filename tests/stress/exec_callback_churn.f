// claude.md #177: exec(args, callback) -- the non-blocking counterpart
// to exec(args). Exercises the new argv-deep-copy/payload/trampoline
// path (runtime/festina_runtime.c's FestinaExecPayload,
// festina_process_exec_dispatch, codegen.py's own generic
// _emit_exec_callback_trampoline) under real concurrency: several
// outstanding dispatches in flight together every pass, on BOTH the
// success path (a real, quick child process) and the graceful-failure
// path (a missing executable, which must still deliver -1 through the
// callback rather than crashing or leaking the payload it already
// allocated).
//
// Each dispatch deep-copies its own arr[text] argv into an owned copy
// before this pass's own local goes out of scope -- exactly the case
// that would leak or use-after-free if festina_process_exec_dispatch
// ever borrowed the caller's strings instead of strdup'ing them.

const int PASSES = 40
const int PER_PASS = 4

int done = 0
int okCount = 0
int missingCount = 0

void func finishOne() {
    done = done + 1
    if done == PASSES * PER_PASS {
        log(`ok=${okCount} missing=${missingCount}`)
        close(0)
    }
}
void func onOk(code:int) {
    if code == 0 { okCount = okCount + 1 }
    finishOne()
}
void func onMissing(code:int) {
    if code == -1 { missingCount = missingCount + 1 }
    finishOne()
}

int i = 0
while i < PASSES {
    // Two real, quick children and two calls to a missing executable,
    // every pass -- different argv sizes each time so the deep copy
    // isn't always the same length.
    arr[text] c1 = ['/bin/true']
    arr[text] c2 = ['/bin/sh', '-c', 'exit 0']
    arr[text] c3 = ['/no/such/binary/at/all/xyz']
    arr[text] c4 = ['/no/such/binary/at/all/xyz', 'with', 'extra', 'args']

    exec(c1, onOk)
    exec(c2, onOk)
    exec(c3, onMissing)
    exec(c4, onMissing)

    i = i + 1
}
