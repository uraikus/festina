// claude.md #165/#171: blob/img/aud's own `.callback()` -- a background
// load, dispatched onto festina_runtime_async.c's worker pool and
// completed on the MAIN thread once festina_run_timer_loop drains it.
// Every callback here mutates a shared global rather than a local, so
// this also exercises the one thing media_churn.f's own synchronous
// loads cannot: a value built on a background thread, read back
// through a global, and reclaimed once the program has nothing left to
// wait for. Both the success path (a real file) and the graceful-
// failure path (a missing one, leaving the placeholder in place --
// exactly the case festina_image_load_worker/festina_audio_load_worker
// were built to leave un-crashed) run every pass.
//
// Unlike __festina_main()'s own top-level statements (which all run
// BEFORE festina_run_timer_loop ever gets a chance to drain a single
// job -- see claude.md #165's own "dispatched logs before loaded"
// ordering, which every other async_io test pins too), the summary
// below can only be logged from INSIDE a callback: `done` counts every
// one of the six callbacks each pass fires, and close(0) only runs once
// every single one of them -- 60 passes * 6 -- actually has.

const int PASSES = 60
const int PER_PASS = 6

int done = 0
int okBlobs = 0
int okImgs = 0
int okAuds = 0
int missingBlobs = 0
int missingImgs = 0
int missingAuds = 0

void func finishOne() {
    done = done + 1
    if done == PASSES * PER_PASS {
        log(`ok blobs=${okBlobs} imgs=${okImgs} auds=${okAuds}`)
        log(`missing blobs=${missingBlobs} imgs=${missingImgs} auds=${missingAuds}`)
        close(0)
    }
}
void func onBlob(b:blob) {
    if b.exists() { okBlobs = okBlobs + 1 } else { missingBlobs = missingBlobs + 1 }
    finishOne()
}
void func onImg(i:img) {
    if i.width == 1 { missingImgs = missingImgs + 1 } else { okImgs = okImgs + 1 }
    finishOne()
}
void func onAud(a:aud) {
    okAuds = okAuds + 1
    finishOne()
}
void func onMissingAud(a:aud) {
    missingAuds = missingAuds + 1
    finishOne()
}

int i = 0
while i < PASSES {
    // A real file, loaded three ways at once -- exercises the
    // async-io pool with several outstanding jobs of DIFFERENT
    // payload types in flight together, not just several of the same
    // one (festina_decode_mp3's own pthread_once fix is specifically
    // for this: an img load and an aud load racing each other).
    blob b = 'tiles.png'.callback(onBlob)
    img p = 'tiles.png'.callback(onImg)
    aud w = 'beep.wav'.callback(onAud)

    // The graceful-failure path -- a path that never resolves to a
    // real file, on all three types at once, every pass. This is the
    // path that used to be a hard festina_fail() exit(1) on a worker
    // thread; here it has to leave the value as an empty/placeholder
    // handle and let the program keep going.
    blob missingB = 'does/not/exist.dat'.callback(onBlob)
    img missingP = 'does/not/exist.png'.callback(onImg)
    aud missingW = 'does/not/exist.mp3'.callback(onMissingAud)

    // Aliased before the background load even finishes -- the
    // placeholder itself is refcounted exactly like the finished
    // value, so this is real double-ownership of the SAME box the
    // worker later mutates in place.
    img aliasP = p
    aud aliasW = w
    free aliasP
    free aliasW

    i = i + 1
}
