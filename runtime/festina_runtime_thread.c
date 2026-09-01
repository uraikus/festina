/*
 * Festina native runtime -- thread translation unit: claude.md #195
 * Phase 2 (`thread NAME { ... }`, the first feature that runs
 * arbitrary Festina code on more than one OS thread -- see that
 * entry's own "core safety argument": every message crossing the
 * boundary is a deep clone, so no two threads ever share a mutable
 * refcounted pointer, and every retain/release in this runtime can
 * stay the plain non-atomic increment/decrement it already is).
 *
 * Split out of core the same way festina_runtime_async.c is (see that
 * file's own top comment) -- a program with no `thread` declaration at
 * all must never need -pthread linked for THIS reason (a program using
 * only blob/img/aud's `.callback()` already needs it for a different
 * one; see festina/cli.py's per-feature object file selection, driven
 * by CodeGen.uses_threads).
 *
 * One FestinaThreadHandle per declared thread, each with its own pair
 * of plain mutex+condvar-guarded FIFOs (inbound: main -> thread,
 * blocked on by the worker's own loop; outbound: thread -> main,
 * polled/drained by the main thread) -- the identical shape
 * festina_runtime_async.c's own job queue already established and this
 * project already validated with a real ThreadSanitizer run (claude.md
 * #163). Deliberately a SEPARATE mechanism from that file's own worker
 * pool: async-io jobs are fire-and-forget, stateless C work items with
 * no Festina code running on the worker side at all; a `thread` is a
 * long-lived, stateful, ordered mailbox with real (isolated) Festina
 * code running on its own dedicated OS thread for the life of the
 * process.
 */
#include <stdint.h>
#include <stdlib.h>
#include <pthread.h>
#include "festina_runtime.h"

typedef struct FestinaThreadMsg {
    void *payload;
    struct FestinaThreadMsg *next;
} FestinaThreadMsg;

struct FestinaThreadHandle {
    pthread_t thread;

    pthread_mutex_t in_lock;
    pthread_cond_t in_cond;
    FestinaThreadMsg *in_head;
    FestinaThreadMsg *in_tail;
    /* Both guarded by in_lock -- kill_requested is only ever read/
     * written under it (so the worker's own wait loop and
     * festina_thread_kill's own signal can never race); `alive` is
     * ALSO written under it (by the worker, right before it returns)
     * but read without one everywhere else (festina_thread_is_alive,
     * festina_thread_outstanding_impl) -- the same "a plain read is
     * atomic enough for this runtime's own conventions" reasoning
     * festina_runtime_async.c's own g_outstanding doc comment already
     * gives, and every reader here only ever needs "was it 0" answered
     * approximately-promptly, never exactly. */
    volatile int kill_requested;
    volatile int alive;

    pthread_mutex_t out_lock;
    FestinaThreadMsg *out_head;
    FestinaThreadMsg *out_tail;
    /* Set at most once, from the main thread only, by
     * festina_thread_set_out_callback -- never written from the worker
     * thread, so reading it (unlocked) from festina_thread_drain_impl,
     * also main-thread-only, is race-free by construction, not by
     * accident. */
    void (*out_callback)(void *payload);

    void (*on_load)(void);
    void (*on_message)(void *payload);
    void (*on_exit)(int64_t code);
    /* claude.md #197 Phase 3: the function to call to release ONE
     * delivered payload, on each queue's own receiving side -- `free`
     * for a plain box/owned-text payload, or a real Festina release
     * cascade for a struct/arr[T]/map[T]/enum one (see
     * festina_thread_register's own doc comment in festina_runtime.h).
     * Set once, at registration, and never NULL. */
    void (*in_release)(void *payload);
    void (*out_release)(void *payload);

    struct FestinaThreadHandle *next; /* registry linked list */
};

static pthread_mutex_t g_registry_lock = PTHREAD_MUTEX_INITIALIZER;
static FestinaThreadHandle *g_registry = NULL;

static void festina_thread_free_msg_list(FestinaThreadMsg *m, void (*release_fn)(void *)) {
    while (m) {
        FestinaThreadMsg *next = m->next;
        release_fn(m->payload);
        free(m);
        m = next;
    }
}

/* claude.md #195/#197: the worker's own whole life, one function --
 * on_load() once, then block-and-dispatch messages forever until
 * killed. An uncaught throw inside on_load/on_message/on_exit is not
 * separately guarded here (unlike festina_runtime_http.c's own worker,
 * this file needs no __builtin_setjmp catch-frame machinery yet) --
 * cloning itself (codegen's own _clone_fn_for_*) can't throw for any
 * message type this runtime accepts today, and an uncaught
 * festina_throw from Festina code currently terminates the whole
 * process (see festina_throw's own comment) regardless of which
 * thread it happens on, so "only this thread dies" containment is
 * intentionally deferred rather than silently assumed here --
 * flagged as a real, documented follow-up, not a silent gap. */
static void *festina_thread_main(void *arg) {
    FestinaThreadHandle *h = (FestinaThreadHandle *)arg;
    if (h->on_load) h->on_load();
    for (;;) {
        pthread_mutex_lock(&h->in_lock);
        while (!h->in_head && !h->kill_requested) {
            pthread_cond_wait(&h->in_cond, &h->in_lock);
        }
        if (h->kill_requested) {
            /* claude.md #195 Phase 2: "kill" means stop now -- any
             * message still queued is left for festina_thread_kill's
             * own cleanup (below) to discard, not drained first. */
            pthread_mutex_unlock(&h->in_lock);
            break;
        }
        FestinaThreadMsg *msg = h->in_head;
        h->in_head = msg->next;
        if (!h->in_head) h->in_tail = NULL;
        pthread_mutex_unlock(&h->in_lock);

        msg->next = NULL;
        if (h->on_message) h->on_message(msg->payload);
        h->in_release(msg->payload);
        free(msg);
    }
    if (h->on_exit) h->on_exit(0);
    pthread_mutex_lock(&h->in_lock);
    h->alive = 0;
    pthread_mutex_unlock(&h->in_lock);
    return NULL;
}

FestinaThreadHandle *festina_thread_register(void (*on_load)(void),
                                             void (*on_message)(void *payload),
                                             void (*on_exit)(int64_t code),
                                             void (*in_release)(void *payload),
                                             void (*out_release)(void *payload)) {
    FestinaThreadHandle *h = calloc(1, sizeof(*h));
    if (!h) festina_fail("out of memory registering a thread");
    pthread_mutex_init(&h->in_lock, NULL);
    pthread_cond_init(&h->in_cond, NULL);
    pthread_mutex_init(&h->out_lock, NULL);
    h->on_load = on_load;
    h->on_message = on_message;
    h->on_exit = on_exit;
    h->in_release = in_release;
    h->out_release = out_release;
    pthread_mutex_lock(&g_registry_lock);
    h->next = g_registry;
    g_registry = h;
    pthread_mutex_unlock(&g_registry_lock);
    return h;
}

void festina_thread_spawn(FestinaThreadHandle *h) {
    pthread_mutex_lock(&h->in_lock);
    h->kill_requested = 0;
    h->alive = 1;
    pthread_mutex_unlock(&h->in_lock);
    if (pthread_create(&h->thread, NULL, festina_thread_main, h) != 0) {
        festina_fail("out of resources starting a thread");
    }
}

void festina_thread_post(FestinaThreadHandle *h, void *payload) {
    FestinaThreadMsg *m = malloc(sizeof(*m));
    if (!m) festina_fail("out of memory posting a thread message");
    m->payload = payload;
    m->next = NULL;
    pthread_mutex_lock(&h->in_lock);
    if (h->in_tail) h->in_tail->next = m; else h->in_head = m;
    h->in_tail = m;
    pthread_mutex_unlock(&h->in_lock);
    pthread_cond_signal(&h->in_cond);
}

void festina_thread_post_outbound(FestinaThreadHandle *h, void *payload) {
    /* Called from INSIDE this thread's own on_load/on_message/on_exit
     * -- i.e. always from h's own single worker thread, never
     * concurrently with itself, so out_lock only ever needs to
     * exclude the main thread's own drain, not another producer. */
    FestinaThreadMsg *m = malloc(sizeof(*m));
    if (!m) festina_fail("out of memory posting a thread outbound message");
    m->payload = payload;
    m->next = NULL;
    pthread_mutex_lock(&h->out_lock);
    if (h->out_tail) h->out_tail->next = m; else h->out_head = m;
    h->out_tail = m;
    pthread_mutex_unlock(&h->out_lock);
}

void festina_thread_set_out_callback(FestinaThreadHandle *h, void (*out_callback)(void *payload)) {
    h->out_callback = out_callback;
}

void festina_thread_kill(FestinaThreadHandle *h) {
    pthread_mutex_lock(&h->in_lock);
    if (!h->alive) {
        pthread_mutex_unlock(&h->in_lock);
        return; /* already dead -- a no-op, matching kill()'s own idempotent shape */
    }
    h->kill_requested = 1;
    pthread_mutex_unlock(&h->in_lock);
    pthread_cond_signal(&h->in_cond);
    pthread_join(h->thread, NULL);

    pthread_mutex_lock(&h->in_lock);
    FestinaThreadMsg *leftover = h->in_head;
    h->in_head = h->in_tail = NULL;
    h->kill_requested = 0; /* reset so a later festina_thread_live can reuse this handle */
    pthread_mutex_unlock(&h->in_lock);
    festina_thread_free_msg_list(leftover, h->in_release);
}

void festina_thread_live(FestinaThreadHandle *h, void (*callback)(int8_t alive)) {
    pthread_mutex_lock(&h->in_lock);
    int already_alive = h->alive;
    pthread_mutex_unlock(&h->in_lock);
    if (!already_alive) {
        festina_thread_spawn(h);
    }
    if (callback) callback(1);
}

int8_t festina_thread_is_alive(FestinaThreadHandle *h) {
    return h->alive ? (int8_t)1 : (int8_t)0;
}

static int64_t festina_thread_outstanding_impl(void) {
    int64_t count = 0;
    pthread_mutex_lock(&g_registry_lock);
    for (FestinaThreadHandle *h = g_registry; h; h = h->next) {
        if (h->alive) count++;
    }
    pthread_mutex_unlock(&g_registry_lock);
    return count;
}

static void festina_thread_drain_impl(void) {
    /* claude.md #195 Phase 2: the registry only ever GROWS (every
     * declared thread registers once, in main()'s own prologue, before
     * __festina_main() runs) and is only ever walked from the main
     * thread, so a lock here only needs to protect against a
     * (nonexistent, in practice) concurrent register -- taken anyway
     * for the same reason festina_thread_outstanding_impl does. */
    pthread_mutex_lock(&g_registry_lock);
    FestinaThreadHandle *list = g_registry;
    pthread_mutex_unlock(&g_registry_lock);
    for (FestinaThreadHandle *h = list; h; h = h->next) {
        if (!h->out_callback) continue; /* see festina_thread_drain's own doc comment */
        pthread_mutex_lock(&h->out_lock);
        FestinaThreadMsg *done = h->out_head;
        h->out_head = h->out_tail = NULL;
        pthread_mutex_unlock(&h->out_lock);
        while (done) {
            FestinaThreadMsg *next = done->next;
            h->out_callback(done->payload);
            h->out_release(done->payload);
            free(done);
            done = next;
        }
    }
}

static void festina_thread_kill_all_impl(void) {
    pthread_mutex_lock(&g_registry_lock);
    FestinaThreadHandle *list = g_registry;
    pthread_mutex_unlock(&g_registry_lock);
    for (FestinaThreadHandle *h = list; h; h = h->next) {
        festina_thread_kill(h);
    }
}

/* claude.md #199 Phase 5: guards festina_runtime.c's own shared
 * g_cached_stmts/g_cached_stmt_count/g_cached_stmt_cap (claude.md #113's
 * literal-SQL prepared-statement cache) -- a single process-wide array,
 * fine as long as sqlite() only ever ran on the one main thread, but a
 * real cross-thread data race once a `thread` with its own DatabaseURL
 * can call sqlite() too, concurrently with main's own queries (or
 * another such thread's). Deliberately NOT touched by the *slot fast
 * path itself (festina_sqlite_prepare_cached's own per-CALL-SITE cache
 * global) -- that global is lexically private to whichever ONE thread's
 * generated code contains that call site, so it's never actually shared,
 * needing no lock. Only the registry array core itself is. Lives here,
 * not in festina_runtime.c, for the identical "a program with no
 * `thread` declaration must never need -pthread linked" reason every
 * other pthread-touching symbol in this file does -- see
 * festina_set_stmt_cache_hooks's own doc comment in festina_runtime.c
 * for the hook-seam shape this mirrors (festina_set_thread_hooks/
 * _async_io_hooks/_audio_decoder, all the same "core declares a
 * NULL-by-default function pointer pair, an optional translation unit
 * registers the real one" pattern). */
static pthread_mutex_t g_stmt_cache_mutex = PTHREAD_MUTEX_INITIALIZER;

static void festina_stmt_cache_lock(void) {
    pthread_mutex_lock(&g_stmt_cache_mutex);
}

static void festina_stmt_cache_unlock(void) {
    pthread_mutex_unlock(&g_stmt_cache_mutex);
}

/* claude.md #195 Phase 2 (mirrors festina_register_async_io_hooks
 * exactly) -- codegen's own conditional call site, main()'s prologue,
 * whenever CodeGen.uses_threads is set. claude.md #199 Phase 5 widens
 * this to also register the statement-cache lock/unlock pair just
 * above -- correct to do unconditionally here (not gated on whether any
 * declared thread actually uses its own DatabaseURL): every program
 * reaching this function at all already declares at least one `thread`,
 * so main's own top-level code is ALREADY running concurrently with at
 * least one other OS thread from this point on, which is the only fact
 * that actually matters for whether the shared statement-cache registry
 * needs locking -- whether that OTHER thread happens to touch sqlite
 * itself is irrelevant to whether MAIN's own queries need to be made
 * safe against a hypothetical one that does. */
void festina_register_thread_hooks(void) {
    festina_set_thread_hooks(festina_thread_outstanding_impl, festina_thread_drain_impl,
                             festina_thread_kill_all_impl);
    festina_set_stmt_cache_hooks(festina_stmt_cache_lock, festina_stmt_cache_unlock);
}
