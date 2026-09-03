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
    /* claude.md #217: a `t.reply(x)` delivery -- dispatched entirely
     * differently from ORDINARY: never calls `on_message`, instead
     * looked up by `txn_id` in the RECEIVING handle's own pending-
     * callback list (registered by whichever `.postMessage(x).
     * callback(fn)` call site sent the ORIGINAL message this is a
     * reply to) and delivered straight to that `fn`. See
     * festina_thread_reply and the dispatch branches in
     * festina_thread_try_dispatch_one/festina_thread_drain_impl. */
    FESTINA_THREAD_MSG_REPLY,
} FestinaThreadMsgKind;

typedef struct FestinaThreadMsg {
    FestinaThreadMsgKind kind;
    void *payload;
    /* claude.md #208: which thread SENT this one -- the main singleton
     * (claude.md #216) for main, or the sending thread's own
     * FestinaThreadHandle* when one thread messages another directly.
     * Set once, at post time (festina_thread_post's own new parameter,
     * or implicitly `h` itself in festina_thread_post_outbound, since
     * a thread's own outbound queue can only ever be filled by that
     * one thread), and handed straight through to whichever handler
     * eventually dequeues this message -- see festina_thread_main's
     * own inbound dispatch and festina_thread_drain_impl's own
     * outbound one. Always NULL for a GIVE_REQUEST message (claude.md
     * #213: legal only from main). */
    void *sender;
    /* claude.md #217: 0 for an ordinary send with no callback
     * expected; otherwise the id festina_thread_alloc_txn_id() minted
     * for a `.postMessage(x).callback(fn)` send -- set into the
     * calling thread's own ambient `g_current_reply_txn` right before
     * `on_message` runs (festina_thread_try_dispatch_one /
     * festina_thread_drain_impl), so a `t.reply(...)` call made DURING
     * that dispatch knows which pending callback slot to satisfy. Also
     * the lookup key on a REPLY-kind message's own receiving end. */
    int64_t txn_id;
    /* claude.md #218: how to release THIS message's own payload, for a
     * REPLY-kind message only (NULL for every other kind, which use
     * the receiving handle's own in_release/out_release instead -- an
     * ordinary payload always matches that ONE declared inbound type,
     * so it needs no per-message answer). A reply's payload type is
     * the SENDER's own reply_type, which the receiving side has no way
     * to know, so the sender records it here: without this, every
     * reply that arrives with nothing to dispatch to (a `.reply()`
     * called twice for one message, or one still queued when its
     * target is kill()'d) was simply leaked, since no code downstream
     * could name the right release function. Set once, at reply time,
     * from codegen's own _thread_payload_release_fn(reply_type). */
    void (*release)(void *payload);
    struct FestinaThreadMsg *next;
} FestinaThreadMsg;

/* claude.md #217: one pending `.callback(fn)` registration, made by
 * `.postMessage(x).callback(fn)` on the SENDING handle's own list
 * (self-only: written and later read/removed exclusively by that one
 * handle's own OS thread -- a thread only ever registers its OWN
 * sends and only ever dispatches its OWN queues, main included via
 * g_main_handle, so this list needs no lock at all, unlike in_head/
 * out_head which genuinely cross threads). `trampoline` is codegen-
 * generated, ONE per target (thread or main) that has a reply_type --
 * unboxes `payload` as that target's own concrete reply type and calls
 * `user_fn` with it; `user_fn` is the actual Festina `func[T]:void`
 * value passed to `.callback(fn)` at this specific call site. A reply
 * that never arrives leaves its slot here forever (a small, bounded,
 * documented characteristic -- matches this runtime's general "a
 * result nobody asked for again is nobody's problem" stance, not a
 * hazard this design tries to close). */
typedef struct FestinaPendingCallback {
    int64_t txn_id;
    void (*trampoline)(void *payload, void *user_fn);
    void *user_fn;
    /* claude.md #222: true only for a WORKER's own registration of the
     * BARE form (`postMessage(x).callback(fn)`, always targeting main
     * -- semantic.py never allows the bare form anywhere else, so this
     * is never set for a registration made on g_main_handle's own
     * list). Read by festina_thread_dispatch_reply: a reply answering
     * one of these must fire `fn` on MAIN's own OS thread, not the
     * dispatching thread's -- see that function's own doc comment for
     * why a worker's own dispatch loop otherwise runs it inline, on
     * itself. */
    int dispatch_on_main;
    struct FestinaPendingCallback *next;
} FestinaPendingCallback;

/* claude.md #218: appended at the TAIL, not prepended at the head (as
 * this list originally was). Replies overwhelmingly come back in the
 * order their sends went out -- a target thread processes its own
 * inbound queue strictly in order -- so the oldest registration is
 * almost always the one the next arriving reply is looking for.
 * Prepending put it LAST, making every lookup walk the whole list and
 * turning N outstanding sends into O(N^2) pointer chasing (5,000
 * in-flight sends, the volume this project's own stress program uses,
 * cost ~12.5M hops). Appending makes that same common case an O(1)
 * head hit. */

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
    /* claude.md #231 (uraikus/festina#91): `NAME.drain()`'s own state --
     * both guarded by in_lock, alongside kill_requested/the queue
     * itself, for the identical reason those are: the worker writes
     * `dispatching` and signals `drained_cond`, festina_thread_wait_
     * drained (called from main) reads/waits on both, and a condvar's own
     * correctness requires its predicate be read and waited on under
     * the SAME lock that guards every write to it. `dispatching` is 1
     * from the moment a message is dequeued until the worker next
     * takes in_lock looking for another (claude.md #232: which is the
     * instant after that message's own on_message/in_release, or
     * GIVE_REQUEST/REPLY dispatch, returns -- see
     * festina_thread_note_dispatch_finished_locked). "The queue is
     * empty" alone is not enough to mean "drained": a message could be
     * dequeued (in_head already NULL) but still being processed when
     * drain() checks. */
    volatile int dispatching;
    pthread_cond_t drained_cond;
    /* claude.md #232: how many festina_thread_wait_drained callers are
     * currently blocked on drained_cond (in practice 0 or 1 -- only
     * main ever calls it). Guarded by in_lock. Read by the worker on
     * every dequeue so it can SKIP the broadcast entirely when nobody
     * is waiting, which is every message in a program that never
     * calls drain() at all -- see festina_thread_try_dispatch_one. */
    int drain_waiters;

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

    /* claude.md #216: set only for the one process-wide singleton
     * below -- every REGISTERED thread (festina_thread_register) is
     * the ordinary kind, `is_main` always 0. Read-only after this
     * handle's own construction (either at static-init time for the
     * singleton, or `calloc`'s own zero-fill for an ordinary one), so
     * no lock is needed to read it from festina_thread_is_main. */
    int is_main;

    /* claude.md #217: this handle's own pending `.callback(fn)`
     * registrations -- see FestinaPendingCallback's own doc comment
     * for why this needs no lock. Singly-linked, walked linearly on
     * both insert and remove; expected volume is small (one entry per
     * message this handle has sent expecting a reply and not yet
     * gotten one), so no hash table is warranted. */
    FestinaPendingCallback *pending_callbacks;
    FestinaPendingCallback *pending_callbacks_tail;

    struct FestinaThreadHandle *next; /* registry linked list */
};

/* claude.md #216: "worker is null when sent by main" (claude.md #208)
 * is gone -- `worker:thread` is never null now, so main itself needs a
 * real, non-NULL, singleton `thread` value to pass as `sender`
 * whenever a send genuinely originates from main's own top-level code
 * (see festina_thread_get_main_handle, and the codegen call site this
 * replaces claude.md #208's literal `null` at). This is an IDENTITY
 * token only -- it is never pthread_create'd, never put in g_registry,
 * never has on_load/on_message/on_exit/in_release set, and its own
 * in_lock/in_cond/out_lock are simply never touched (every delivery
 * path that might target it checks `is_main` FIRST and routes through
 * the existing outbound-to-main queue/handler instead -- see
 * festina_thread_reply in the reply/callback work this singleton was
 * introduced alongside). Zero-initialized like any other handle would
 * be from calloc; `.is_main = 1` is the one field that differs. */
static FestinaThreadHandle g_main_handle = { .is_main = 1 };

FestinaThreadHandle *festina_thread_get_main_handle(void) {
    return &g_main_handle;
}

int8_t festina_thread_is_main(void *handle) {
    return ((FestinaThreadHandle *)handle)->is_main ? (int8_t)1 : (int8_t)0;
}

static pthread_mutex_t g_registry_lock = PTHREAD_MUTEX_INITIALIZER;
static FestinaThreadHandle *g_registry = NULL;

static void festina_thread_free_msg_list(FestinaThreadMsg *m, void (*release_fn)(void *)) {
    /* claude.md #217: `release_fn` is only ever correct for an
     * ORDINARY message -- it's this THREAD's own declared inbound_
     * type's release function, and every ordinary payload was boxed
     * to match it. A GIVE_REQUEST payload is a small
     * FestinaGiveRequestPayload*, never a boxed Festina value at all
     * (calling release_fn on it would be a genuine type-confusion
     * crash, a pre-existing latent bug this phase's own REPLY case
     * made worth fixing alongside); its own small wrapper struct is
     * freed directly instead (a documented, narrow gap: the
     * connection it names is simply never returned to any listener --
     * kill()'ing a thread mid-hand-off is rare enough not to warrant
     * reaching into festina_runtime_http.c's own machinery here). A
     * REPLY payload WAS boxed the same way an ordinary send is, but
     * its type is the SENDER's own reply_type, which this receiving
     * side has no way to name -- so the sender records the right
     * release function on the message itself (claude.md #218's own
     * `release` field), and this uses that. Before that field existed,
     * a reply still queued when its target was kill()'d simply
     * leaked. */
    while (m) {
        FestinaThreadMsg *next = m->next;
        if (m->kind == FESTINA_THREAD_MSG_ORDINARY) {
            release_fn(m->payload);
        } else if (m->kind == FESTINA_THREAD_MSG_GIVE_REQUEST) {
            free(m->payload);
        } else if (m->kind == FESTINA_THREAD_MSG_REPLY) {
            if (m->release) m->release(m->payload);
        }
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
/* claude.md #217: "which pending callback is THIS thread currently
 * answering, if any" -- 0 (no callback pending) unless the message
 * currently being dispatched on THIS calling thread was sent via
 * `.postMessage(x).callback(fn)`. Set right before `on_message` runs
 * (both dispatch points below), read by festina_thread_reply when a
 * `t.reply(...)` call happens during that SAME dispatch. `__thread`,
 * not a plain global -- every thread dispatches its own queue
 * independently and concurrently, so this must never be shared (the
 * same invariant every other piece of per-thread dispatch state in
 * this runtime already keeps, e.g. festina_runtime_http.c's own
 * connection tables after claude.md #212). */
static __thread int64_t g_current_reply_txn = 0;

/* claude.md #217: one atomic, process-wide, monotonic counter for
 * `.postMessage(x).callback(fn)`'s own txn ids -- mirrors
 * festina_runtime_http.c's own g_next_conn_id/g_next_conn_id_lock
 * exactly (mutex-protected rather than a compiler intrinsic, matching
 * this runtime's own established convention for this exact shape of
 * counter). Ids never repeat for the life of the process; 0 is
 * reserved to mean "no callback expected" (FestinaThreadMsg's own
 * default), so this starts at 1. */
static int64_t g_next_txn_id = 1;
static pthread_mutex_t g_next_txn_id_lock = PTHREAD_MUTEX_INITIALIZER;

int64_t festina_thread_alloc_txn_id(void) {
    pthread_mutex_lock(&g_next_txn_id_lock);
    int64_t id = g_next_txn_id++;
    pthread_mutex_unlock(&g_next_txn_id_lock);
    return id;
}

/* claude.md #217: records ONE pending `.callback(fn)` on `self`'s own
 * list -- `self` is always the SENDING handle (the main singleton, or
 * the calling thread's own handle), and this list is only ever
 * written and later read/removed by that SAME handle's own OS thread
 * (see FestinaPendingCallback's own doc comment for why that needs no
 * lock). Called ahead of the actual send (festina_thread_post/
 * _post_outbound), so the slot already exists by the time any reply
 * could possibly arrive. claude.md #222: `dispatch_on_main` is codegen's
 * own compile-time-known "is this the bare form" fact (1 for
 * `postMessage(x).callback(fn)`, always 0 for a NAME.postMessage one,
 * and always 0 for anything main itself registers, since the bare form
 * doesn't exist at main's own top level) -- see FestinaPendingCallback's
 * own doc comment for what it changes at dispatch time. */
void festina_thread_register_callback(FestinaThreadHandle *self, int64_t txn_id,
                                      void (*trampoline)(void *payload, void *user_fn),
                                      void *user_fn, int8_t dispatch_on_main) {
    FestinaPendingCallback *cb = malloc(sizeof(*cb));
    if (!cb) festina_fail("out of memory registering a reply callback");
    cb->txn_id = txn_id;
    cb->trampoline = trampoline;
    cb->user_fn = user_fn;
    cb->dispatch_on_main = dispatch_on_main ? 1 : 0;
    cb->next = NULL;
    /* claude.md #218: append, don't prepend -- see FestinaPendingCallback's
     * own doc comment for the O(N^2)-vs-O(1) reasoning. */
    if (self->pending_callbacks_tail) self->pending_callbacks_tail->next = cb;
    else self->pending_callbacks = cb;
    self->pending_callbacks_tail = cb;
}

/* claude.md #217: `t.reply(x)` -- `self` is the CALLING thread's own
 * handle (the main singleton, or self._current_thread_ctx's handle at
 * the codegen level), needed ONLY to route a reply-to-main through
 * THAT handle's own outbound queue (the exact path an ordinary bare
 * `postMessage(x)` already uses to reach main -- see
 * festina_thread_post_outbound); `dest` is WHERE this reply is going
 * (the `worker`/`t` value the handler was itself called with -- the
 * original sender, main's own singleton included). Reads the ambient
 * g_current_reply_txn (set by whichever dispatch point below is
 * currently running `on_message` on this SAME calling thread) to tag
 * the delivered message -- this is what lets the eventual receiver
 * find the right pending-callback slot. Does NOT invoke on_message on
 * the receiving end at all; see festina_thread_try_dispatch_one's own
 * REPLY branch and festina_thread_drain_impl's own. */
void festina_thread_reply(FestinaThreadHandle *self, FestinaThreadHandle *dest, void *payload,
                          void (*release)(void *payload)) {
    /* claude.md #218: no message is being dispatched on this thread
     * right now (g_current_reply_txn is 0), so there is no pending
     * callback anywhere that this could ever answer -- deliver nothing
     * and release the payload here instead of enqueuing a message
     * guaranteed to be dropped on arrival (which is what this used to
     * do, leaking the payload with it). Two ways to reach this, both
     * real: a handler calling `t.reply(...)` a SECOND time for the same
     * message (the first reply consumed the only pending slot), and a
     * `thread` value stashed somewhere that outlives its own dispatch
     * (a struct field, an arr[thread], a map[thread]) and replied to
     * later, from a timer or another handler entirely. */
    if (g_current_reply_txn == 0) {
        if (release) release(payload);
        return;
    }
    FestinaThreadMsg *m = malloc(sizeof(*m));
    if (!m) festina_fail("out of memory replying to a thread message");
    m->kind = FESTINA_THREAD_MSG_REPLY;
    m->payload = payload;
    m->sender = self;
    m->txn_id = g_current_reply_txn;
    m->release = release;
    m->next = NULL;
    if (dest->is_main) {
        pthread_mutex_lock(&self->out_lock);
        if (self->out_tail) self->out_tail->next = m; else self->out_head = m;
        self->out_tail = m;
        pthread_mutex_unlock(&self->out_lock);
    } else {
        pthread_mutex_lock(&dest->in_lock);
        if (dest->in_tail) dest->in_tail->next = m; else dest->in_head = m;
        dest->in_tail = m;
        pthread_mutex_unlock(&dest->in_lock);
        pthread_cond_signal(&dest->in_cond);
    }
}

/* claude.md #222: carries one reply's own (trampoline, user_fn,
 * payload) triple through festina_async_io_dispatch's generic
 * (payload, work_fn, callback, release_fn) job shape -- see
 * festina_thread_dispatch_reply's own doc comment for why a worker's
 * bare-send-to-main callback needs marshaling onto main at all. */
typedef struct FestinaPendingCallbackMarshal {
    void (*trampoline)(void *payload, void *user_fn);
    void *user_fn;
    void *reply_payload;
} FestinaPendingCallbackMarshal;

/* work_fn: runs on an async-io WORKER thread -- there is genuinely no
 * work to do here (the reply's own payload already arrived, fully
 * formed, over this thread's own inbound queue), so this exists only
 * because festina_async_io_dispatch's job shape always calls one. */
static void festina_pending_callback_marshal_noop(void *payload) {
    (void)payload;
}

/* callback: runs on MAIN's own OS thread once the (no-op) work above
 * is marked done -- this is the actual point of marshaling: `trampoline`
 * (which itself unboxes the payload, calls the user's real fn, and
 * releases the box -- see _emit_reply_callback_trampoline in
 * festina/codegen.py) now runs on main instead of on whichever thread
 * originally sent the message. */
static void festina_pending_callback_marshal_invoke(void *payload) {
    FestinaPendingCallbackMarshal *m = (FestinaPendingCallbackMarshal *)payload;
    m->trampoline(m->reply_payload, m->user_fn);
}

/* release_fn: runs on main right after callback -- frees only this
 * marshal wrapper itself; the reply's own boxed payload was already
 * released by `trampoline` above. */
static void festina_pending_callback_marshal_free(void *payload) {
    free(payload);
}

/* claude.md #217/#222: looks up and removes ONE pending callback from
 * `h`'s own list by txn_id (see FestinaPendingCallback's own doc
 * comment on why this needs no lock) -- returns 1 and runs it
 * (unboxing/releasing the payload, calling the user's own fn) if
 * found, 0 (a defensive, should-never-happen case: this runtime's own
 * static enforcement -- semantic.py's ".callback() required" check --
 * guarantees every REPLY-kind message's txn_id was genuinely
 * registered by whoever sent the original message) otherwise, in
 * which case `payload` is deliberately left unreleased rather than
 * guessed at with the wrong release function.
 *
 * claude.md #222: a worker's own registration of the BARE form
 * (`cb->dispatch_on_main`) is never invoked directly here -- this
 * function can be called from EITHER the worker's own inbound-queue
 * dispatch loop (festina_thread_try_dispatch_one, below) or main's own
 * outbound-drain loop (festina_thread_drain_impl, further down), and
 * calling `cb->trampoline` inline would run the user's `fn` on
 * whichever of those two happened to be running -- for a worker's own
 * bare send, that is the WORKER's thread, not main's, exactly the bug
 * this fixes. Marshaling through festina_async_io_dispatch (the same
 * cross-thread-safe mechanism blob/img/aud's own `.callback()` already
 * uses) guarantees `fn` runs on main regardless of which thread called
 * this function. A named send's own registration (`dispatch_on_main`
 * false) is unaffected -- it already runs on the right thread, whichever
 * called this function, so it stays a direct, unmarshaled call. */
static int festina_thread_dispatch_reply(FestinaThreadHandle *h, int64_t txn_id, void *payload) {
    FestinaPendingCallback *prev = NULL;
    FestinaPendingCallback *cb = h->pending_callbacks;
    while (cb) {
        if (cb->txn_id == txn_id) {
            /* claude.md #230: unlink via prev/cur, not just a head-
             * relative **link, specifically so removing the TAIL node
             * can correct pending_callbacks_tail too -- see this
             * function's own doc comment above for the bug this fixes
             * (a stale tail pointer left dangling after freeing `cb`
             * corrupts the very next registration: it writes into
             * already-freed memory via the stale tail and never links
             * the new node into the list `pending_callbacks` itself
             * still points at, so every reply after that is silently
             * unfindable). */
            if (prev) prev->next = cb->next; else h->pending_callbacks = cb->next;
            if (cb == h->pending_callbacks_tail) h->pending_callbacks_tail = prev;
            if (cb->dispatch_on_main) {
                FestinaPendingCallbackMarshal *m = malloc(sizeof(*m));
                if (!m) festina_fail("out of memory marshaling a reply callback onto main");
                m->trampoline = cb->trampoline;
                m->user_fn = cb->user_fn;
                m->reply_payload = payload;
                festina_async_io_dispatch(m, festina_pending_callback_marshal_noop,
                                          festina_pending_callback_marshal_invoke,
                                          festina_pending_callback_marshal_free);
            } else {
                cb->trampoline(payload, cb->user_fn);
            }
            free(cb);
            return 1;
        }
        prev = cb;
        cb = cb->next;
    }
    return 0;
}

/* claude.md #232: the other half of the `dispatching` flag
 * festina_thread_try_dispatch_one sets under in_lock right after
 * dequeuing a message -- cleared at the START of the NEXT call, under
 * the lock that call already takes, rather than by a separate
 * lock/clear/unlock/broadcast round trip after every message (which is
 * how claude.md #231 first shipped it). The worker loop calls this
 * function again the instant a dispatch returns, so "the next call's
 * own lock acquisition" IS "right after this message finished" --
 * there is no window a drain() waiter can observe between the two
 * where the flag is stale in a way that matters: it only ever stays
 * set for the few instructions between one dispatch returning and the
 * next lock being taken, during which the worker is provably not idle
 * anyway. This restores the exact one-acquisition-per-message shape
 * claude.md #218 established for this path, with the broadcast
 * skipped outright unless someone is actually waiting (drain_waiters,
 * also under this lock) -- a program that never calls drain() pays
 * one predictable `if` per message and nothing else. Also runs on the
 * kill path (kill_requested seen) so a waiter is never left holding a
 * stale flag on a thread that's about to stop. */
static inline void festina_thread_note_dispatch_finished_locked(FestinaThreadHandle *h) {
    if (h->dispatching) {
        h->dispatching = 0;
        if (h->drain_waiters) pthread_cond_broadcast(&h->drained_cond);
    }
}

static int festina_thread_try_dispatch_one(FestinaThreadHandle *h, int *killed_out, int blocking) {
    /* claude.md #218: `blocking` folds what used to be a SEPARATE
     * wait-for-work step in festina_thread_main's own non-HTTP loop
     * into this one critical section. That loop previously locked
     * in_lock, waited on the condvar, unlocked, then called this
     * function, which locked in_lock AGAIN to dequeue -- two full
     * mutex round trips for every single message, on the hottest path
     * this whole feature has. Waiting and dequeuing under ONE
     * acquisition halves that with no change in behavior: the condvar
     * predicate is the same, and a spurious wakeup simply finds
     * in_head still empty and waits again. A thread with its own HTTP
     * context passes blocking=0 and keeps polling, exactly as before
     * -- it must never block here, since its own listener needs
     * servicing on the same loop. */
    pthread_mutex_lock(&h->in_lock);
    /* claude.md #232: BEFORE the blocking wait below -- once this
     * worker goes idle on its own condvar, a drain() waiter must
     * already have been released, not left waiting on a flag nobody
     * will clear until the next message arrives. */
    festina_thread_note_dispatch_finished_locked(h);
    if (blocking) {
        while (!h->in_head && !h->kill_requested) {
            pthread_cond_wait(&h->in_cond, &h->in_lock);
        }
    }
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
        /* claude.md #231: set WHILE still holding in_lock -- the same
         * lock festina_thread_wait_drained waits on -- so there is no window
         * where a concurrent drain() could observe both "queue empty"
         * (in_head just went NULL) and "not dispatching" (this flag
         * not yet set) at once and return early while this message is
         * still genuinely in flight. */
        h->dispatching = 1;
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
    if (msg->kind == FESTINA_THREAD_MSG_REPLY) {
        /* claude.md #217: never touches on_message/in_release at all
         * -- dispatched straight to whichever `.callback(fn)` this
         * thread's own earlier send registered. claude.md #218: if
         * there is no such slot (a defensive case -- see
         * festina_thread_dispatch_reply's own doc comment), the
         * payload is released here via the message's own recorded
         * release function rather than leaked. */
        if (!festina_thread_dispatch_reply(h, msg->txn_id, msg->payload)) {
            if (msg->release) msg->release(msg->payload);
        }
        free(msg);
        return 1;
    }
    g_current_reply_txn = msg->txn_id;
    if (h->on_message) h->on_message(msg->sender, msg->payload);
    g_current_reply_txn = 0;
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
 * needs no setjmp catch-frame machinery yet) -- cloning
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
            if (festina_thread_try_dispatch_one(h, &killed, 0)) continue;
            if (killed) break;
            h->http_service_pass(FESTINA_THREAD_HTTP_POLL_MS);
        }
    } else {
        /* claude.md #218: the wait now happens INSIDE the dispatch
         * call, under the same in_lock acquisition that dequeues --
         * see its own `blocking` parameter. */
        for (;;) {
            int killed = 0;
            if (!festina_thread_try_dispatch_one(h, &killed, 1) && killed) break;
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
    /* claude.md #236: this thread's own cleanup stack buffer (grown on
     * demand by every managed local its handlers ever bound while the
     * program has a `try`) -- same placement and reasoning as the two
     * OS-level handles just above: nothing else will ever run on this
     * thread again. */
    festina_cleanup_stack_free();
    pthread_mutex_lock(&h->in_lock);
    h->alive = 0;
    /* claude.md #232: release any drain() waiter -- its predicate
     * includes `alive`, so this is what stops it sleeping forever on
     * a thread that will never dispatch again. See
     * festina_thread_wait_drained. */
    h->dispatching = 0;
    if (h->drain_waiters) pthread_cond_broadcast(&h->drained_cond);
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
    pthread_cond_init(&h->drained_cond, NULL);
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

void festina_thread_post(FestinaThreadHandle *h, void *sender, void *payload, int64_t txn_id) {
    FestinaThreadMsg *m = malloc(sizeof(*m));
    if (!m) festina_fail("out of memory posting a thread message");
    m->kind = FESTINA_THREAD_MSG_ORDINARY;
    m->payload = payload;
    m->sender = sender;
    m->txn_id = txn_id;
    m->release = NULL; /* claude.md #218: ORDINARY uses h->in_release */
    m->next = NULL;
    pthread_mutex_lock(&h->in_lock);
    if (h->in_tail) h->in_tail->next = m; else h->in_head = m;
    h->in_tail = m;
    pthread_mutex_unlock(&h->in_lock);
    pthread_cond_signal(&h->in_cond);
}

void festina_thread_post_outbound(FestinaThreadHandle *h, void *payload, int64_t txn_id) {
    /* Called from INSIDE this thread's own on_load/on_message/on_exit
     * -- i.e. always from h's own single worker thread, never
     * concurrently with itself, so out_lock only ever needs to
     * exclude the main thread's own drain, not another producer.
     * claude.md #208: `sender` is always `h` itself here -- a
     * thread's own outbound queue can only ever be filled by that
     * one thread's own bare postMessage(x), so there is no separate
     * sender argument to take. claude.md #217: `txn_id` is 0 for an
     * ordinary send, or a real allocated id when `.callback(fn)` was
     * chained -- see festina_thread_post's own identical parameter. */
    FestinaThreadMsg *m = malloc(sizeof(*m));
    if (!m) festina_fail("out of memory posting a thread outbound message");
    m->kind = FESTINA_THREAD_MSG_ORDINARY;
    m->payload = payload;
    m->sender = h;
    m->txn_id = txn_id;
    m->release = NULL; /* claude.md #218: ORDINARY uses h->out_release */
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
    /* claude.md #218: both of these were left UNINITIALIZED here (this
     * is malloc, not calloc) -- harmless only because the GIVE_REQUEST
     * dispatch branch happens to return before reading either, which is
     * exactly the kind of "true today, silently wrong the moment
     * someone adds a read" gap worth closing rather than documenting. */
    m->txn_id = 0;
    m->release = NULL;
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

    /* claude.md #217: any `.callback(fn)` THIS thread itself registered
     * (for a send it made before dying) can now never be dispatched --
     * nothing will ever deliver a matching REPLY to a dead handle's
     * queue, and a LATER live() respawning this same handle must not
     * inherit stale registrations from a previous life (a genuinely
     * unrelated future send could reuse the very same txn id -- ids
     * are only ever unique per-SENDER's own list, never checked
     * against a handle's past). Freed, never invoked -- `fn` firing
     * "after" its own thread died would be a phantom callback nobody
     * asked for. h->pending_callbacks is self-only (see
     * FestinaPendingCallback's own doc comment), and kill() has just
     * pthread_join()'d this handle's own OS thread, so nothing else
     * can be touching this list here either. */
    FestinaPendingCallback *cb = h->pending_callbacks;
    h->pending_callbacks = NULL;
    h->pending_callbacks_tail = NULL;
    while (cb) {
        FestinaPendingCallback *next = cb->next;
        free(cb);
        cb = next;
    }
}

/* claude.md #231 (uraikus/festina#91): `NAME.drain()` -- blocks until
 * `dispatching` is clear AND `in_head` is NULL, both read/waited on
 * under in_lock (the same lock festina_thread_try_dispatch_one's own
 * dequeue and festina_thread_mark_drained's own clear-and-broadcast
 * take). Not alive at all (never live()'d, or already kill()'d) means
 * nothing is running to ever drain anything -- returns immediately
 * rather than waiting on a condvar nothing will ever signal again.
 * A message posted to h AFTER this call has already begun is not
 * waited for (this drains what was queued as of the call, not a
 * moving target) -- the identical "as of now" semantics kill()'s own
 * discard already has, just waiting instead of discarding. */
void festina_thread_wait_drained(FestinaThreadHandle *h) {
    pthread_mutex_lock(&h->in_lock);
    /* claude.md #232: `alive` is part of the predicate, not just an
     * up-front check -- if the thread stops while a waiter is blocked
     * here (impossible today, since only main calls both this and
     * kill(), and kill() joins synchronously; kept correct anyway
     * rather than relied on), the waiter must wake and return rather
     * than sleep forever on a condvar a dead thread will never signal
     * again. festina_thread_main broadcasts on its way out for exactly
     * this. drain_waiters is what lets the worker skip the broadcast
     * on every message when nobody is here. */
    h->drain_waiters++;
    while (h->alive && (h->in_head != NULL || h->dispatching)) {
        pthread_cond_wait(&h->drained_cond, &h->in_lock);
    }
    h->drain_waiters--;
    pthread_mutex_unlock(&h->in_lock);
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
     * for the same reason festina_thread_outstanding_impl does.
     *
     * claude.md #217: unlike before, this can NOT early-return just
     * because g_global_message_handler is NULL (no top-level `on
     * message` declared) -- main can still legitimately send
     * `NAME.postMessage(x).callback(fn)` with no top-level `on
     * message` of its own at all, and a REPLY-kind message answering
     * one of those must still be drained and dispatched to
     * g_main_handle's own pending_callbacks regardless. */
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
            if (done->kind == FESTINA_THREAD_MSG_REPLY) {
                /* claude.md #217: a worker replying to something main
                 * sent it -- dispatched straight to g_main_handle's
                 * own pending_callbacks, never through
                 * g_global_message_handler at all. claude.md #218: an
                 * undispatchable reply releases its own payload rather
                 * than leaking it, exactly as the worker-side branch
                 * in festina_thread_try_dispatch_one does. */
                if (!festina_thread_dispatch_reply(&g_main_handle, done->txn_id, done->payload)) {
                    if (done->release) done->release(done->payload);
                }
            } else {
                if (g_global_message_handler) {
                    g_current_reply_txn = done->txn_id;
                    g_global_message_handler(done->sender, done->payload);
                    g_current_reply_txn = 0;
                }
                h->out_release(done->payload);
            }
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
