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

/* claude.md #213 (Phase 5 -- giveRequest): what kind of thing
 * `payload` actually is -- ORDINARY is every message this runtime has
 * ever had before this phase (a boxed Festina value, released via
 * `in_release` once the handler returns); GIVE_REQUEST is new: a
 * connection handed off via `NAME.giveRequest(r)`, whose own payload
 * is a small FestinaGiveRequestPayload (festina_runtime_http.c, never
 * looked inside from this file) needing an entirely different
 * dispatch -- re-attach the connection, then call a registered hook,
 * never the ordinary in_release/on_message pair at all. Kept on the
 * QUEUE ITEM rather than inferred from anything else, since a plain
 * `void*` payload carries no type information of its own to dispatch
 * on. */
typedef enum {
    FESTINA_THREAD_MSG_ORDINARY,
    FESTINA_THREAD_MSG_GIVE_REQUEST,
} FestinaThreadMsgKind;

typedef struct FestinaThreadMsg {
    FestinaThreadMsgKind kind;
    void *payload;
    /* claude.md #208: which thread SENT this one -- NULL for main,
     * or the sending thread's own FestinaThreadHandle* when one
     * thread messages another directly. Set once, at post time
     * (festina_thread_post's own new parameter, or implicitly `h`
     * itself in festina_thread_post_outbound, since a thread's own
     * outbound queue can only ever be filled by that one thread),
     * and handed straight through to whichever handler eventually
     * dequeues this message -- see festina_thread_main's own inbound
     * dispatch and festina_thread_drain_impl's own outbound one.
     * Always NULL for a GIVE_REQUEST message (claude.md #213: legal
     * only from main). */
    void *sender;
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

    void (*on_load)(void);
    /* claude.md #208: `sender` is the FIRST argument now (NULL for
     * main, or another thread's own handle) -- see FestinaThreadMsg's
     * own `sender` field. */
    void (*on_message)(void *sender, void *payload);
    void (*on_exit)(int64_t code);
    /* claude.md #197 Phase 3: the function to call to release ONE
     * delivered payload, on each queue's own receiving side -- `free`
     * for a plain box/owned-text payload, or a real Festina release
     * cascade for a struct/arr[T]/map[T]/enum one (see
     * festina_thread_register's own doc comment in festina_runtime.h).
     * Set once, at registration, and never NULL. */
    void (*in_release)(void *payload);
    void (*out_release)(void *payload);
    /* claude.md #207: closes this thread's own private sqlite handle,
     * if it declared one -- NULL for a thread with no DatabaseURL
     * (festina_thread_set_db_close is simply never called for one),
     * so festina_thread_main's own check below is a plain no-op for
     * it, same shape on_load/on_message/on_exit already have. Set at
     * most once, right after registration, never from the worker. */
    void (*db_close)(void);
    /* claude.md #212 (Phase 4 -- private per-thread HTTP context): the
     * db_close pair's own shape, for a thread that declared its own
     * on request/on upgrade/on socketMessage/on socketClose --
     * festina_thread_set_http_context sets both together, at most
     * once, right after registration, the same "set once before
     * spawn, never touched again" timing db_close already has (so
     * there is no window after the OS thread starts where either
     * could still be NULL for a thread that DOES have an HTTP
     * context). `http_service_pass` non-NULL is exactly what tells
     * festina_thread_main to run the bounded-poll combined loop below
     * instead of blocking forever on this thread's own inbound
     * condvar -- see that function's own comment. */
    void (*http_service_pass)(int timeout_ms);
    void (*http_teardown)(void);
    /* claude.md #213 (Phase 5): dispatches one GIVE_REQUEST message --
     * see festina_thread_set_http_context's own doc comment in
     * festina_runtime.h. Set alongside the two above, at the same
     * time, always non-NULL whenever they are (this thread has an
     * HTTP context) and NULL otherwise. */
    void (*give_request_deliver)(void *payload);

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

/* claude.md #212: the dequeue half of one inbound message's dispatch,
 * factored out of festina_thread_main so both loop shapes below (the
 * plain blocking wait, and the http-context combined poll loop) share
 * the exact same lock/dequeue/dispatch/release sequence -- previously
 * only the plain loop existed, so this is a pure refactor of that
 * loop's own body, not a behavior change for a thread with no HTTP
 * context. Returns 1 if a message was dequeued and dispatched (the
 * caller should immediately look for another rather than wait/poll --
 * draining a backlog fast), 0 if the queue was empty (nothing to do
 * this call), and sets *killed_out if kill_requested was seen while
 * holding the lock (the caller must stop, exactly like the old
 * inline check did). */
static int festina_thread_try_dispatch_one(FestinaThreadHandle *h, int *killed_out) {
    pthread_mutex_lock(&h->in_lock);
    if (h->kill_requested) {
        /* claude.md #195 Phase 2: "kill" means stop now -- any message
         * still queued is left for festina_thread_kill's own cleanup
         * to discard, not drained first. */
        pthread_mutex_unlock(&h->in_lock);
        *killed_out = 1;
        return 0;
    }
    FestinaThreadMsg *msg = h->in_head;
    if (msg) {
        h->in_head = msg->next;
        if (!h->in_head) h->in_tail = NULL;
    }
    pthread_mutex_unlock(&h->in_lock);
    if (!msg) return 0;

    msg->next = NULL;
    if (msg->kind == FESTINA_THREAD_MSG_GIVE_REQUEST) {
        /* claude.md #213: a connection handed off via
         * NAME.giveRequest(r) -- an entirely different dispatch from
         * the ordinary on_message/in_release pair just below: no
         * `sender` (always NULL, see FestinaThreadMsg's own doc
         * comment), and the payload is never released through
         * in_release at all (give_request_deliver owns that decision
         * itself, freeing its own small wrapper struct once it's
         * handed the unwrapped http value off to on_request -- see
         * festina_thread_deliver_given_request, festina_runtime_http.c). */
        if (h->give_request_deliver) h->give_request_deliver(msg->payload);
        free(msg);
        return 1;
    }
    if (h->on_message) h->on_message(msg->sender, msg->payload);
    h->in_release(msg->payload);
    free(msg);
    return 1;
}

/* claude.md #212: how long a bounded poll(), or nanosleep-equivalent
 * (festina_thread_http_service_pass's own poll() call handles both --
 * see its doc comment) may block before this loop comes back around
 * to check for a newly posted message -- this thread never waits on
 * its own inbound condvar while it has an HTTP context (see
 * festina_thread_main below), so this bound is what stands in for
 * that wakeup. Same 20ms granularity festina_run_timer_loop/
 * festina_run_http_loop already use for their own "bounded, not
 * instant" wakes elsewhere in this runtime. */
#define FESTINA_THREAD_HTTP_POLL_MS 20

/* claude.md #195/#197: the worker's own whole life, one function --
 * on_load() once, then dispatch messages forever until killed. An
 * uncaught throw inside on_load/on_message/on_exit is not separately
 * guarded here (unlike festina_runtime_http.c's own worker, this file
 * needs no __builtin_setjmp catch-frame machinery yet) -- cloning
 * itself (codegen's own _clone_fn_for_*) can't throw for any message
 * type this runtime accepts today, and an uncaught festina_throw from
 * Festina code currently terminates the whole process (see
 * festina_throw's own comment) regardless of which thread it happens
 * on, so "only this thread dies" containment is intentionally
 * deferred rather than silently assumed here -- flagged as a real,
 * documented follow-up, not a silent gap.
 *
 * claude.md #212 (Phase 4): a thread with its own HTTP context
 * (http_service_pass non-NULL -- see festina_thread_set_http_context)
 * takes a DIFFERENT loop shape here: rather than blocking forever on
 * this thread's own inbound condvar (festina_thread_post's own
 * pthread_cond_signal would never be able to interrupt that thread
 * out of a concurrent poll() anyway), it dispatches anything already
 * queued, then gives its own private http_service_pass a short,
 * bounded timeout to service its own listeners/connections, then
 * loops back around to check messages again -- the same "poll each
 * source per-iteration with a short bound" shape this project's own
 * main-thread combined graphics+http+timers loop already uses
 * (festina_run_timer_loop/festina_run_http_loop, festina_runtime.c/
 * festina_runtime_http.c), applied here to one thread's own private
 * pair of event sources instead of main's several. A thread with NO
 * HTTP context keeps the original, simpler blocking-condvar shape
 * completely unchanged. */
static void *festina_thread_main(void *arg) {
    FestinaThreadHandle *h = (FestinaThreadHandle *)arg;
    if (h->on_load) h->on_load();
    if (h->http_service_pass) {
        for (;;) {
            int killed = 0;
            if (festina_thread_try_dispatch_one(h, &killed)) continue;
            if (killed) break;
            h->http_service_pass(FESTINA_THREAD_HTTP_POLL_MS);
        }
    } else {
        for (;;) {
            pthread_mutex_lock(&h->in_lock);
            while (!h->in_head && !h->kill_requested) {
                pthread_cond_wait(&h->in_cond, &h->in_lock);
            }
            pthread_mutex_unlock(&h->in_lock);
            int killed = 0;
            if (!festina_thread_try_dispatch_one(h, &killed) && killed) break;
        }
    }
    if (h->on_exit) h->on_exit(0);
    /* claude.md #207: this thread's own worker is genuinely stopping
     * now -- whether that's an explicit NAME.kill() or
     * festina_thread_kill_all() at process teardown, either way
     * nothing on this thread will touch its own private sqlite handle
     * again, so this is the one guaranteed point to close it. Runs
     * AFTER on_exit(0) (the user's own handler may still want to
     * query its thread's database on the way out) and BEFORE `alive`
     * flips to 0 (so a concurrent festina_thread_live on another
     * thread can never observe "alive" while the handle it's about to
     * reopen via on_load is still mid-close). A later NAME.live()
     * calls on_load again, which reopens a genuinely fresh handle --
     * this is what makes that kill()/live() cycle no longer leak the
     * old one. claude.md #212: http_teardown gets the identical
     * placement/reasoning, right alongside db_close, for the same
     * class of resource (an OS-level handle nothing else in this
     * runtime will ever close on this thread's behalf). */
    if (h->db_close) h->db_close();
    if (h->http_teardown) h->http_teardown();
    pthread_mutex_lock(&h->in_lock);
    h->alive = 0;
    pthread_mutex_unlock(&h->in_lock);
    return NULL;
}

FestinaThreadHandle *festina_thread_register(void (*on_load)(void),
                                             void (*on_message)(void *sender, void *payload),
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

void festina_thread_post(FestinaThreadHandle *h, void *sender, void *payload) {
    FestinaThreadMsg *m = malloc(sizeof(*m));
    if (!m) festina_fail("out of memory posting a thread message");
    m->kind = FESTINA_THREAD_MSG_ORDINARY;
    m->payload = payload;
    m->sender = sender;
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
     * exclude the main thread's own drain, not another producer.
     * claude.md #208: `sender` is always `h` itself here -- a
     * thread's own outbound queue can only ever be filled by that
     * one thread's own bare postMessage(x), so there is no separate
     * sender argument to take. */
    FestinaThreadMsg *m = malloc(sizeof(*m));
    if (!m) festina_fail("out of memory posting a thread outbound message");
    m->kind = FESTINA_THREAD_MSG_ORDINARY;
    m->payload = payload;
    m->sender = h;
    m->next = NULL;
    pthread_mutex_lock(&h->out_lock);
    if (h->out_tail) h->out_tail->next = m; else h->out_head = m;
    h->out_tail = m;
    pthread_mutex_unlock(&h->out_lock);
}

void festina_thread_set_db_close(FestinaThreadHandle *h, void (*db_close)(void)) {
    h->db_close = db_close;
}

void festina_thread_set_http_context(FestinaThreadHandle *h,
                                     void (*service_pass)(int timeout_ms),
                                     void (*teardown)(void),
                                     void (*give_request_deliver)(void *payload)) {
    h->http_service_pass = service_pass;
    h->http_teardown = teardown;
    h->give_request_deliver = give_request_deliver;
}

void festina_thread_give_request(FestinaThreadHandle *h, void *conn, void *http_value) {
    /* claude.md #213: mirrors festina_thread_post's own lock/enqueue/
     * signal shape exactly, just building a GIVE_REQUEST-kind message
     * instead of an ORDINARY one, with `sender` always NULL (legal
     * only from main -- semantic.py's own gate) and `payload` NOT a
     * boxed Festina value at all (it's a small
     * FestinaGiveRequestPayload, festina_runtime_http.c's own, built
     * right here so that struct's layout never needs to be known in
     * this file). festina_thread_try_dispatch_one's own kind check is
     * what keeps this from ever being mistaken for an ordinary
     * message needing on_message/in_release. */
    FestinaGiveRequestPayload *p = malloc(sizeof(*p));
    if (!p) festina_fail("out of memory handing off a request");
    p->conn = conn;
    p->http_value = http_value;
    FestinaThreadMsg *m = malloc(sizeof(*m));
    if (!m) festina_fail("out of memory handing off a request");
    m->kind = FESTINA_THREAD_MSG_GIVE_REQUEST;
    m->payload = p;
    m->sender = NULL;
    m->next = NULL;
    pthread_mutex_lock(&h->in_lock);
    if (h->in_tail) h->in_tail->next = m; else h->in_head = m;
    h->in_tail = m;
    pthread_mutex_unlock(&h->in_lock);
    pthread_cond_signal(&h->in_cond);
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

/* claude.md #208: the ONE handler for everything sent to main, from
 * ANY thread -- replaces the old per-handle out_callback (set via the
 * now-removed festina_thread_set_out_callback, one dynamic
 * registration per thread) with a single, statically-known function
 * pointer, registered once via festina_set_global_message_handler,
 * mirroring the exact hook-seam shape festina_set_thread_hooks/
 * festina_set_stmt_cache_hooks already use (a NULL-by-default global,
 * a plain setter, no locking needed since it's set at most once,
 * before any thread's own worker starts, and never written again).
 * Read (unlocked) only from festina_thread_drain_impl, main-thread-
 * only, so this is race-free by the same construction those other
 * hooks already rely on. */
static void (*g_global_message_handler)(void *sender, void *payload) = NULL;

void festina_set_global_message_handler(void (*handler)(void *sender, void *payload)) {
    g_global_message_handler = handler;
}

static void festina_thread_drain_impl(void) {
    /* claude.md #195 Phase 2: the registry only ever GROWS (every
     * declared thread registers once, in main()'s own prologue, before
     * __festina_main() runs) and is only ever walked from the main
     * thread, so a lock here only needs to protect against a
     * (nonexistent, in practice) concurrent register -- taken anyway
     * for the same reason festina_thread_outstanding_impl does. */
    if (!g_global_message_handler) return; /* claude.md #208: nothing registered at all -- nothing to drain to */
    pthread_mutex_lock(&g_registry_lock);
    FestinaThreadHandle *list = g_registry;
    pthread_mutex_unlock(&g_registry_lock);
    for (FestinaThreadHandle *h = list; h; h = h->next) {
        pthread_mutex_lock(&h->out_lock);
        FestinaThreadMsg *done = h->out_head;
        h->out_head = h->out_tail = NULL;
        pthread_mutex_unlock(&h->out_lock);
        while (done) {
            FestinaThreadMsg *next = done->next;
            g_global_message_handler(done->sender, done->payload);
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
