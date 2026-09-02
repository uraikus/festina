/*
 * Festina native runtime -- generic async-io translation unit:
 * claude.md #165 (blob/img/aud's own `.callback()` -- a background
 * file load, the non-blocking counterpart to claude.md #163's http
 * client callback). Split out of every OTHER translation unit so a
 * program that never uses blob/img/aud's own `.callback()` form never
 * needs -pthread linked for THIS reason (blob is core, always linked,
 * so putting this machinery directly there would make pthread an
 * unconditional dependency of every Festina program -- exactly the
 * per-feature linking split graphics/audio/http already established;
 * see cli.py's per-feature object file selection, driven by
 * CodeGen.uses_async_io in festina/codegen.py).
 *
 * Deliberately a SEPARATE pool from claude.md #163's own http-specific
 * one in festina_runtime_http.c, not a shared/unified one -- the http
 * pool is already built, tested (ThreadSanitizer-clean), and stable;
 * refactoring it to be generic would risk regressing already-verified
 * code for a benefit (one fewer thread pool in memory) that doesn't
 * matter at this runtime's scale. A future unification is possible but
 * not done here.
 *
 * Unlike the http pool, nothing this file's own worker calls can ever
 * throw (festina_blob_open/the image-from-path loader/festina_load_audio
 * all follow the "test, don't fail" convention -- an unreadable/
 * malformed file yields an empty/unusable value, never festina_throw)
 * -- so this pool needs none of festina_runtime_http.c's own
 * setjmp catch-frame machinery at all. Much simpler as a
 * result: a job is just (payload, work_fn, callback, release_fn), and
 * the worker's whole body is `work_fn(payload)`.
 */
#include <stdint.h>
#include <stdlib.h>
#include <pthread.h>
#include "festina_runtime.h"

typedef struct FestinaAsyncIoJob {
    void *payload;
    void (*work_fn)(void *payload);      /* runs on the WORKER thread */
    void (*callback)(void *payload);     /* runs on the MAIN thread, once complete */
    void (*release_fn)(void *payload);   /* balances festina_retain from the dispatch
                                          * site -- runs on the main thread, right
                                          * after callback */
    struct FestinaAsyncIoJob *next;
} FestinaAsyncIoJob;

static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_work_cond = PTHREAD_COND_INITIALIZER;
static FestinaAsyncIoJob *g_queue_head = NULL;
static FestinaAsyncIoJob *g_queue_tail = NULL;
static FestinaAsyncIoJob *g_done_head = NULL;
static FestinaAsyncIoJob *g_done_tail = NULL;
static int64_t g_outstanding = 0;
static int g_pool_started = 0;

#define FESTINA_ASYNC_IO_WORKERS 4

static void *festina_async_io_worker(void *unused) {
    (void)unused;
    for (;;) {
        pthread_mutex_lock(&g_lock);
        while (!g_queue_head) pthread_cond_wait(&g_work_cond, &g_lock);
        FestinaAsyncIoJob *job = g_queue_head;
        g_queue_head = job->next;
        if (!g_queue_head) g_queue_tail = NULL;
        pthread_mutex_unlock(&g_lock);

        job->next = NULL;
        job->work_fn(job->payload);   /* never throws -- see this file's own top comment */

        pthread_mutex_lock(&g_lock);
        if (g_done_tail) g_done_tail->next = job; else g_done_head = job;
        g_done_tail = job;
        pthread_mutex_unlock(&g_lock);
    }
    return NULL; /* unreachable -- workers run for the life of the process */
}

static void festina_async_io_ensure_pool(void) {
    pthread_mutex_lock(&g_lock);
    if (g_pool_started) { pthread_mutex_unlock(&g_lock); return; }
    g_pool_started = 1;
    pthread_mutex_unlock(&g_lock);
    for (int i = 0; i < FESTINA_ASYNC_IO_WORKERS; i++) {
        pthread_t t;
        if (pthread_create(&t, NULL, festina_async_io_worker, NULL) != 0) {
            festina_fail("out of resources starting the async-io worker pool");
        }
        pthread_detach(t);
    }
}

/* claude.md #165: blob/img/aud's own dispatch functions call this once
 * they've already decided `callback` is non-NULL (the NULL case stays
 * a direct, unchanged, synchronous call to the existing loader --
 * see e.g. festina_blob_load_dispatch). Takes ownership of exactly one
 * retained reference to `payload` -- the caller must festina_retain it
 * BEFORE calling this, balanced by `release_fn` once `callback` has
 * run. No wake-pipe/poll() integration here (unlike claude.md #163's
 * http pool) -- the three possible host loops (festina_run_timer_loop,
 * festina_run_http_loop, festina_run_event_loop) each poll
 * festina_async_io_outstanding()/_drain() on a short bounded interval
 * instead, since blob/img/aud loading has no natural "loop wakes up
 * for THIS fd" moment the way a socket becoming readable does, and a
 * single generic pool needs to work from any of the three without
 * knowing which one it ended up in. */
void festina_async_io_run(void *payload, void (*work_fn)(void *),
                          void (*callback)(void *), void (*release_fn)(void *)) {
    festina_async_io_ensure_pool();
    FestinaAsyncIoJob *job = malloc(sizeof(*job));
    if (!job) festina_fail("out of memory queuing an async-io job");
    job->payload = payload;
    job->work_fn = work_fn;
    job->callback = callback;
    job->release_fn = release_fn;
    job->next = NULL;
    pthread_mutex_lock(&g_lock);
    g_outstanding++;
    if (g_queue_tail) g_queue_tail->next = job; else g_queue_head = job;
    g_queue_tail = job;
    pthread_mutex_unlock(&g_lock);
    pthread_cond_signal(&g_work_cond);
}

static int64_t festina_async_io_outstanding_impl(void) {
    /* claude.md #222: locked, unlike this runtime's other "a plain read
     * is atomic enough" counters (e.g. festina_thread_is_alive's own
     * `alive` field) -- those are all written by exactly ONE thread
     * (the owning thread itself) and read from others, where a single
     * aligned int64_t read/write genuinely can't tear. g_outstanding
     * stopped fitting that shape the moment a WORKER thread could call
     * festina_async_io_run (via a bare `.postMessage(x).callback(fn)`
     * reply marshaled onto main -- see festina_thread_dispatch_reply
     * in festina_runtime_thread.c): this is now a genuine multi-WRITER
     * counter (main's own program code, any number of worker threads),
     * so an unlocked read is a real, ThreadSanitizer-confirmed data
     * race, not just an overly cautious label -- caught directly by
     * scripts/thread_tsan_stress.sh the first time this path was ever
     * exercised from a thread other than main. Called every loop
     * iteration from main only, so the lock is uncontended in the
     * common case and cheap regardless. */
    pthread_mutex_lock(&g_lock);
    int64_t n = g_outstanding;
    pthread_mutex_unlock(&g_lock);
    return n;
}

static void festina_async_io_drain_impl(void) {
    if (!g_pool_started) return;
    pthread_mutex_lock(&g_lock);
    FestinaAsyncIoJob *done = g_done_head;
    g_done_head = g_done_tail = NULL;
    pthread_mutex_unlock(&g_lock);

    while (done) {
        FestinaAsyncIoJob *next = done->next;
        if (done->callback) done->callback(done->payload);
        if (done->release_fn) done->release_fn(done->payload);
        free(done);
        pthread_mutex_lock(&g_lock);
        g_outstanding--;
        pthread_mutex_unlock(&g_lock);
        done = next;
    }
}

/* claude.md #165: codegen's own conditional call site (uses_async_io,
 * mirroring uses_https's own festina_register_tls_hooks() call) --
 * registers this file's own outstanding/drain functions into the
 * shared hook seam festina_runtime.c declares (see that header's own
 * doc comment). */
void festina_register_async_io_hooks(void) {
    festina_set_async_io_hooks(festina_async_io_outstanding_impl, festina_async_io_drain_impl,
                                festina_async_io_run);
}
