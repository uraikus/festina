"""claude.md #102: the leak stress suite.

The rest of this directory proves the compiler produces the right
ANSWERS. This proves nothing accumulates while producing them: each
program in tests/stress/ hammers one managed resource -- text, arrays
and maps, structs and query rows, images and audio clips, regexes and
files -- some thousands of times, under AddressSanitizer and
LeakSanitizer. At that iteration count a leak of even a few bytes per
pass is unmissable, which is the entire point; a leak that only shows up
after a million frames of a game is otherwise invisible until it isn't.

The programs are deliberately written as one long loop rather than as
many small cases: the interesting failures are the ones where a value's
ownership is right in isolation and wrong when it is aliased, returned,
stored and discarded in the same breath.

Why a shell script rather than doing this inline: `clang
-fsanitize=address -c file.ll` does NOT instrument raw LLVM IR text --
ASan's per-function opt-in is added by clang's C frontend, which is
bypassed entirely when the input is already .ll. The script stamps the
attribute onto every `define` line first. See its own header comment for
the verification of that claim, and for why it needs two compilers.
"""
import os
import shutil
import subprocess

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "leak_stress.sh")
_STRESS_DIR = os.path.join(_ROOT, "tests", "stress")

# Exit code the script uses for "this environment cannot run me" (no
# clang for the IR, or no working ASan runtime to link against), as
# distinct from "a program leaked".
_SKIP_EXIT = 77


def _run_harness(*args):
    """Invokes the harness through an explicitly resolved `bash` rather
    than relying on its shebang. `/usr/bin/env bash` fails with a bare
    127 in a stripped PATH, which is indistinguishable from a real
    failure at the call site -- and the script's own 77-means-skip guard
    never gets to run, because the shell it guards was never found."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on PATH -- the leak harness is a shell script")
    return subprocess.run([bash, _SCRIPT, *args],
                           capture_output=True, text=True, timeout=900)


def _stress_programs():
    if not os.path.isdir(_STRESS_DIR):
        return []
    return sorted(f for f in os.listdir(_STRESS_DIR) if f.endswith(".f"))


# claude.md #113: one minimal program PER DATA TYPE, in isolation. The
# churn programs above deliberately mix types, because mixed ownership is
# where the interesting bugs live -- but when one of them fails, the
# report says "something in this pile leaked". These pin each type alone,
# so a regression names the type in the test id. Every program is
# leak-free BY DESIGN: types the compiler reclaims are exercised through
# their ordinary lifecycle, and the two it cannot always reclaim
# (img/aud) are freed by hand, which is what `free` is for.
_PER_TYPE_PROGRAMS = {
    "int": """
int keep = 0
for int i = 0, i < 200, i++ {
    int a = i * 2
    a = a + 1
    free a
    keep = keep + 1
}
log(keep)
""",
    "float": """
int keep = 0
for int i = 0, i < 200, i++ {
    float f = 1.5
    f = f * 2.0
    free f
    keep = keep + 1
}
log(keep)
""",
    "bool": """
int keep = 0
for int i = 0, i < 200, i++ {
    bool b = i % 2 == 0
    b = !b
    free b
    keep = keep + 1
}
log(keep)
""",
    "text": """
text outer = ''
for int i = 0, i < 200, i++ {
    text a = `built ${i}`
    text b = a + ' and more'
    text alias = a          // copy-on-alias, both freed independently
    a = 'reassigned'        // frees the built buffer
    outer = b               // global reassignment frees the old global
    free alias
}
log(outer)
""",
    "blob": """
for int i = 0, i < 200, i++ {
    blob a = `scratch_${i % 3}.dat`
    a.write(`round ${i}`)
    blob shared = a          // refcount, not a copy
    a = 'scratch_other.dat'  // releases one reference
    if shared.toText() != `round ${i}` { log('corrupted') }
    free shared
    free a
}
log('done')
""",
    "regex": """
int hits = 0
for int i = 0, i < 200, i++ {
    regex lit = /[0-9]+/
    if lit.test(`v${i}`) { hits = hits + 1 }
    free lit                 // cached literal: free is a safe no-op
    regex dyn = regex('[a-z]+', 'g')
    text out = `A${i}b`.replace(dyn, '_')
    if out == '' { log('unreachable') }
    free dyn                 // dynamic: genuinely freed
}
log(hits)
""",
    "arr_int": """
int total = 0
for int i = 0, i < 200, i++ {
    arr[int] xs = [1, 2, 3]
    xs.push(i)
    arr[int] alias = xs
    alias.push(5)
    total = total + xs.length
    free xs                  // decrement -- alias still owns it
    free alias
}
log(total)
""",
    "arr_text": """
int total = 0
for int i = 0, i < 200, i++ {
    arr[text] xs = [`a${i}`, 'b']
    xs.push(`c${i}`)         // owned copies, released with the array
    total = total + xs.length
}
log(total)
""",
    "map_int": """
int total = 0
for int i = 0, i < 200, i++ {
    map[int] m = {'a': 1, 'b': 2}
    m[`k${i % 4}`] = i
    delete m.a
    total = total + m['b']
    free m
}
log(total)
""",
    "map_text": """
int total = 0
for int i = 0, i < 200, i++ {
    map[text] m = {'a': `v${i}`}
    m['b'] = `w${i}`
    delete m['a']            // releases the value it held
    if m['b'] != `w${i}` { log('corrupted') }
    total = total + 1
}
log(total)
""",
    "struct": """
struct P { n:int  label:text }
int total = 0
for int i = 0, i < 200, i++ {
    P p
    p.n = i
    p.label = `v${i}`
    P alias = p              // refcount
    p.label = 'reassigned'   // frees the old field buffer
    delete p.label
    total = total + alias.n
    free p
    free alias
}
log(total)
""",
    "struct_self": """
struct Node { n:int  next:Node }
int total = 0
for int i = 0, i < 200, i++ {
    Node head
    head.n = 1
    head.next.n = 2
    head.next.next.n = 3
    // claude.md #120: close the chain into a genuine reference cycle.
    // Refcounting alone can never free this; the trial deletion the
    // cyclic release wrapper runs is what keeps this program leak-free.
    head.next.next.next = head
    total = total + head.n + head.next.next.n
}
log(total)
""",
    "table_rows": """
table People { id:int  name:text }
sqlite('DELETE FROM People')
sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'row'])
int total = 0
for int i = 0, i < 200, i++ {
    arr[People] rows = sqlite('SELECT * FROM People')
    People first = rows[0]   // its own reference (claude.md #265)
    total = total + first.id
    if rows[0].undefined('name') { log('unreachable') }
    free first               // drops this binding's reference
    free rows                // drops the array's, and the row with it
}
log(total)
""",
    "struct_query": """
table People { id:int  name:text }
sqlite('DELETE FROM People')
sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'row'])
struct Landed { whatever:int  label:text }
int total = 0
for int i = 0, i < 200, i++ {
    arr[Landed] q = sqlite('SELECT id AS whatever, name AS label FROM People')
    Landed keep = q[0]
    free q                   // element survives via its own refcount
    total = total + keep.whatever
    if keep.label != 'row' { log('corrupted') }
    free keep
}
log(total)
""",
    "img": """
int total = 0
for int i = 0, i < 60, i++ {
    img sheet = 'tiles.png'
    img tile = sheet.clip(0, 0, 8, 8)
    // claude.md #118: img is refcounted -- the alias holds its own
    // reference, so `free sheet` is a decrement and reading through
    // `alias` afterwards is SAFE, not the dangling-pointer hazard this
    // program used to document. Freeing through both bindings is the
    // ordinary shape now, and scope exit reclaims what free missed.
    img alias = sheet
    free sheet
    total = total + alias.width + tile.width
    free alias
    free tile
}
log(total)
""",
    "aud": """
int total = 0
for int i = 0, i < 40, i++ {
    aud clip = 'beep.wav'
    // Same shape as the img program: refcounted since claude.md #118,
    // so the alias survives `free clip` and is freed through its own
    // binding (or scope exit) without double-free.
    aud alias = clip
    free clip
    free alias
    total = total + 1
}
log(total)
""",
    # claude.md #202: `T?` -- struct/arr/map, all through the SAME
    # `_is_manually_managed` gate in codegen.py, in one program (the
    # representation and the gating are identical across every eligible
    # type, unlike img/aud's own type-specific quirks above, so one
    # combined program is real coverage rather than three copies of the
    # same assertion). Every allocation is freed EXPLICITLY -- this
    # program is proof `free`/`delete` still fully reclaim a manually-
    # managed value; see test_manually_managed_value_leaks_when_never_
    # freed below for the opposite (and equally load-bearing) proof,
    # that skipping free() on one is a REAL leak, not silently caught by
    # the automatic system that every other type here relies on.
    "manually_managed": """
struct Point { x:int y:int }
int total = 0
for int i = 0, i < 200, i++ {
    Point? p
    p.x = i
    p.y = i * 2
    total = total + p.x + p.y
    free p

    arr[int]? xs
    xs.push(i)
    xs.push(i + 1)
    total = total + xs.length
    free xs

    map[int]? m
    m['k'] = i
    total = total + m['k']
    free m
}
log(total)
""",
}


class TestLeakStress:
    @pytest.mark.parametrize("type_name", sorted(_PER_TYPE_PROGRAMS))
    def test_each_type_is_leak_free_in_isolation(self, type_name, tmp_path):
        # claude.md #113: see _PER_TYPE_PROGRAMS -- a failure here names
        # the TYPE, where a churn-program failure names a pile.
        src = tmp_path / f"type_{type_name}.f"
        src.write_text(_PER_TYPE_PROGRAMS[type_name])
        result = _run_harness(str(src))
        if result.returncode == _SKIP_EXIT:
            pytest.skip(result.stderr.strip() or "sanitizers unavailable")
        assert result.returncode == 0, (
            f"the {type_name} type is not leak-free in isolation:\n"
            f"{result.stdout}\n{result.stderr}")

    @pytest.mark.parametrize("program", _stress_programs())
    def test_program_is_leak_free(self, program):
        # One test per program rather than one for all of them, so a
        # failure names the resource that leaked instead of just
        # "something did".
        result = _run_harness(os.path.join(_STRESS_DIR, program))
        if result.returncode == _SKIP_EXIT:
            pytest.skip(result.stderr.strip() or "sanitizers unavailable")
        assert result.returncode == 0, (
            f"{program} is not leak-free:\n{result.stdout}\n{result.stderr}")

    def test_the_harness_can_actually_fail(self, tmp_path):
        """A leak checker that cannot report a leak is worse than none.

        This is not hypothetical caution: `clang -fsanitize=address -c
        file.ll` silently produces an UNinstrumented object, so a harness
        built the obvious way passes everything and proves nothing.

        The canary leaks on purpose and the harness must say so. THREE
        previous canaries were retired because the compiler fixed them
        -- the chained call result by claude.md #108/#117, the reference
        cycle by #120, and the row-array residual (`rows()[0].name`,
        deliberate since #85/#119) by #260 -- which is exactly the
        failure mode a canary is supposed to have: it stops leaking,
        this test fails loudly, and nobody discovers months later that
        the harness had been vacuous.

        Three retirements is enough of a pattern to stop picking
        canaries from the "known bug, not yet fixed" pile. This one is a
        `text?` built in a loop and never freed: a leak by CONTRACT
        rather than by omission (claude.md #202/#257 -- `?` means the
        compiler manages nothing, and `free` is the only release), so a
        future round cannot quietly fix it out from under this test. If
        THIS ever stops leaking, `?` itself is broken and the loud
        failure is the correct outcome rather than a retirement.
        """
        canary = tmp_path / "canary.f"
        canary.write_text(
            "int i = 0\n"
            "while i < 200 {\n"
            "    text? held = `leaked ${i}`\n"
            "    i = i + 1\n"
            "}\n"
            "log('done')\n"
        )
        result = _run_harness(str(canary))
        if result.returncode == _SKIP_EXIT:
            pytest.skip(result.stderr.strip() or "sanitizers unavailable")
        assert result.returncode != 0, (
            "the leak harness reported a known-leaking program as clean, which "
            "means it is not instrumenting anything:\n" + result.stdout)
        assert "LeakSanitizer" in result.stdout

    def test_manually_managed_value_leaks_when_never_freed(self, tmp_path):
        """claude.md #202: the opposite-direction canary `T?` needs that
        no other type in this suite does. Every other per-type program
        above is proof the compiler reclaims a value it automatically
        manages; this is proof it does NOT automatically manage a `T?`
        one at all -- a manually-managed struct that's simply let go
        out of scope, with no `free`/`delete` ever called on it, MUST
        show up as a real, reported leak. If it didn't, the compiler
        would be silently retaining/releasing it after all, defeating
        the entire point of the `?` qualifier (claude.md #111's `free`
        statement would then have nothing left to be the ONLY release
        for)."""
        canary = tmp_path / "canary.f"
        canary.write_text(
            "struct Point { x:int y:int }\n"
            "Point? sink\n"
            "int i = 0\n"
            "while i < 200 {\n"
            "    Point? p\n"
            "    p.x = i\n"
            "    // Assigned into a GLOBAL, deliberately -- not just to\n"
            "    // exercise the reassignment-skip gate too (an ordinary\n"
            "    // managed struct assigned here would retain the new\n"
            "    // value and release whatever `sink` held before), but\n"
            "    // because a value that's merely READ and discarded is\n"
            "    // provably dead code once nothing auto-manages it (no\n"
            "    // retain/release call remains for LLVM's own optimizer\n"
            "    // to treat as opaque), and gets optimized away entirely\n"
            "    // -- confirmed directly: an earlier draft of this\n"
            "    // canary computed a running total and logged it instead\n"
            "    // of assigning to a global, and reported clean not\n"
            "    // because the compiler still auto-manages `T?` but\n"
            "    // because LLVM correctly proved the whole allocation had\n"
            "    // no observable effect and deleted it outright. Escaping\n"
            "    // to a global's own storage -- externally visible, so\n"
            "    // LLVM cannot prove a store to it is dead -- is what\n"
            "    // keeps each iteration's allocation from being erased\n"
            "    // before it ever gets a chance to leak. No `free p` (or\n"
            "    // `free sink`) anywhere: if the automatic system left\n"
            "    // this alone as designed, every one of the 199\n"
            "    // iterations `sink` no longer points at leaks its own\n"
            "    // allocation (the 200th is still reachable through\n"
            "    // `sink` itself at exit, so 199 is the exact expected\n"
            "    // count, not just 'more than zero').\n"
            "    sink = p\n"
            "    i = i + 1\n"
            "}\n"
            "log(sink.x)\n"
        )
        result = _run_harness(str(canary))
        if result.returncode == _SKIP_EXIT:
            pytest.skip(result.stderr.strip() or "sanitizers unavailable")
        assert result.returncode != 0, (
            "a manually-managed value that is never freed compiled clean under "
            "LeakSanitizer -- the automatic system is retaining/releasing it "
            "after all, which defeats `T?` entirely:\n" + result.stdout)
        assert "LeakSanitizer" in result.stdout

    def test_the_suite_covers_every_managed_resource(self):
        # A guard against the suite quietly shrinking: each of these
        # names a distinct ownership mechanism, and dropping one would
        # leave a whole class of leak unwatched.
        assert set(_stress_programs()) == {
            "collections_churn.f",      # arr[T]/map[T], nested and aliased
            "media_churn.f",            # img/aud/blob handles, incl. BLOB round trips
            "regex_and_files_churn.f",  # regex compilation, file and time text
            "structs_and_rows_churn.f", # structs, query rows, scope exits
            "text_churn.f",             # text, the copy-managed one
            # claude.md #256: the `ascii` type -- a REFCOUNTED string,
            # so a genuinely different ownership shape from
            # text_churn.f's copy-managed one just above, and the only
            # type whose indexing hands back an IMMORTAL value (one of
            # the 128 single-character singletons) that must never be
            # freed however many times it is dropped.
            "ascii_churn.f",
            # claude.md #130: splice's own 3rd-argument insertion --
            # element-range retain/copy into a SEPARATE array's buffer,
            # a genuinely different ownership shape than push/unshift's
            # single-value one collections_churn.f already covers.
            "splice_insert_churn.f",
            # claude.md #172: blob/img/aud's own `.callback()` -- a value
            # built on a BACKGROUND thread and mutated in place once the
            # main thread drains it, media_churn.f's synchronous loads
            # cannot exercise this at all (there is no worker thread, no
            # placeholder to alias before the real value lands, and no
            # graceful-failure-on-a-worker-thread path to hit).
            "async_io_churn.f",
            # claude.md #173: .toStruct()/.toArr() JSON parsing --
            # nested struct/arr[T]/map[T] fields/elements, each
            # recursing into its own from-json function, including a
            # self-referencing struct's own function calling itself.
            "json_parse_churn.f",
            # claude.md #173: a real, pre-existing leak this round found
            # (not introduced by it) -- a Ternary between two OWNING
            # branches (a template literal, a `+` concatenation, a
            # function call, ...) leaked whichever branch actually ran,
            # every time, since the caller's own copy/retain landed on
            # top of an already-correct +1 with nothing left to balance
            # it. Isolated on its own, independent of JSON parsing.
            "ternary_ownership_churn.f",
            # claude.md #174: amor arr[T]'s own real amortized (doubling)
            # growth -- push/pop/shift/unshift/splice (both 2- and
            # 3-argument forms) at real iteration counts, on a scalar
            # element type AND a refcounted one, plus the struct-field
            # auto-vivify path -- exactly the "far larger surface than
            # map's four operations" collections_churn.f's own arr[T]/
            # map[T] coverage doesn't exercise, since plain arr[T] has
            # no capacity field to get wrong in the first place.
            "amor_array_churn.f",
            # claude.md #176: enum's own two runtime representations --
            # a pure-struct enum's widened, self-tagged struct header
            # (repeated reassignment starting from its own null zero-
            # value, the exact case that used to segfault before the
            # release wrapper learned to null-check first) and a mixed
            # enum's independently heap-allocated {tag, value} box
            # (alternating which member type is boxed, including a
            # refcounted `text` member), plus aliasing churn through
            # two enum-typed locals sharing the same struct pointer.
            "enum_churn.f",
            # claude.md #184: .sort()'s own comparator trampoline path --
            # the indirect call back into Festina code happens on EVERY
            # comparison, thousands of times per pass, for both a text-
            # keyed struct element type (a refcounted slot the merge
            # sort's scratch-buffer copy could double-free or drop) and
            # a scalar int element type, including already-sorted and
            # single-element arrays (the zero-swap edge cases).
            "sort_churn.f",
            # claude.md #186: map[T].keys()/.values() -- .values()'s own
            # retain-or-copy ownership work on a refcounted/text value
            # type, confirmed independent of the SOURCE map's own
            # lifetime (freeing/deleting from the map right after
            # collecting must leave the returned array untouched).
            "map_keys_values_churn.f",
            # claude.md #190: the JSON-rendering optimization itself --
            # festina_sb_append_n's own length-aware append (no
            # runtime strlen() rescan) and festina_sb_append_json_
            # text's run-scanning escape loop (bulk memcpy of
            # unescaped runs). Exercises every _json_fn_for shape --
            # struct (nested and empty), arr[T], map[T], a table row --
            # with text needing real escaping (quotes, backslashes,
            # control characters, multi-byte UTF-8) so a bug in either
            # optimization surfaces as a real ASan error.
            "json_render_churn.f",
            # claude.md #192/#194: the argument-coercion and arr[handle]
            # ownership fixes -- a text literal/template passed to a
            # blob/img/aud parameter (previously an invalid free of the
            # handle payload, or a leaked handle), an escaping arr[blob]
            # cascading to release each element handle, and .push()/
            # .indexOf() of a text->handle coerced element. A leak or
            # invalid free surfaces as a real ASan error.
            "handle_arg_and_arr_churn.f",
            # claude.md #195/#196: `thread` -- an ownership shape none
            # of the above can exercise at all, since it's the only one
            # of them where Festina code runs on more than one OS
            # thread concurrently. Two message shapes (int, the raw-
            # bits box; text, the "box IS the owned buffer" path), 20
            # real kill()/live() cycles (each spawning/joining a real
            # pthread), and thousands of messages posted up front so a
            # leaked box or pthread resource is unmissable.
            "thread_churn.f",
            # claude.md #199: `thread`'s own private sqlite handle --
            # an ownership shape thread_churn.f's own workers can't
            # exercise at all, since none of them touches sqlite: a
            # SEPARATE sqlite3* handle per DatabaseURL-declared thread,
            # opened on that thread's own OS thread, with real
            # INSERT/SELECT traffic overlapping (not just interleaving
            # by luck) the main program's own concurrent sqlite() calls
            # against its own, different database file -- the specific
            # shape needed to catch a leaked/raced prepared-statement
            # cache entry, which no single-threaded sqlite test could.
            "thread_db_churn.f",
            # claude.md #207: the OTHER half of a thread's own private
            # sqlite handle thread_db_churn.f can't exercise -- that
            # file's own worker stays alive for the whole run, so it
            # never proves a kill()/live() cycle actually closes the
            # OLD handle before on_load reopens a fresh one. 500 real
            # kill()/live() cycles (each blocking: kill() joins, live()
            # spawns), so a leaked sqlite3*/fd pair per cycle is
            # unmissable.
            "thread_db_kill_live_churn.f",
            # claude.md #202 Phase 2: `T?` crossing a `thread` boundary
            # -- an ownership shape neither thread_churn.f nor
            # thread_db_churn.f exercises: the payload is the SENDER's
            # own shared raw pointer, never a clone, so the receiving
            # side must explicitly `free` it itself (nothing auto-
            # manages it on either side) -- miss that free and every
            # one of these leaks, unmissable at this volume.
            "thread_manually_managed_churn.f",
            # claude.md #209: `thread NAME[N] { ... }` -- an ownership
            # shape none of the files above exercise: N genuinely
            # independent OS threads all running the IDENTICAL
            # generated body concurrently, each its own private state/
            # handle/queue. thread_churn.f's own workers are each a
            # DIFFERENT body, one instance apiece -- this is the shape
            # most likely to expose a bug where per-instance codegen
            # accidentally shared something (a global instead of a
            # namespaced one, one instance's handle read/written by
            # another's).
            "thread_pool_churn.f",
            # claude.md #210: thread-private helper functions -- every
            # message is actually processed by a real, separately
            # mangled per-thread function call (not an inline handler
            # body), including one private func calling ANOTHER, in
            # both a plain thread AND a pool (each pool instance's own
            # private func closing over THAT instance's own state, not
            # shared with its siblings).
            "thread_private_func_churn.f",
            # claude.md #211: exec(args)/regex()/mkdir()/ls(), all
            # newly unblocked inside a thread body -- two threads each
            # calling these at their OWN call sites concurrently, at
            # real volume, so a leaked exec()'d child/fd, a leaked
            # regex compilation, or a leaked directory-listing
            # accumulator would show up here.
            "thread_wider_builtins_churn.f",
            # claude.md #212: a thread's own private HTTP context --
            # main and a thread each with their own openPort()'d
            # listener, plus the thread making real blocking CLIENT
            # requests (`req.send()`) back to main's own port on every
            # message, at real volume, so a leaked connection/listener
            # table entry (or the __thread conversion itself somehow
            # leaking across contexts) would show up here. This is
            # also the first stress program to link
            # festina_runtime_http.c (and, since it uses the client
            # form, festina_runtime_https.c) at all.
            "thread_http_context_churn.f",
            # claude.md #213: NAME.giveRequest(r) -- real, concurrent,
            # at-volume connection hand-offs (main accepts, detaches,
            # hands to a thread; a third thread drives real client
            # requests) -- so a leak in the retain/release accounting
            # festina_conn_detach's own doc comment works through (or
            # a leaked FestinaConn/FestinaGiveRequestPayload transfer
            # block itself) would show up here.
            "thread_giverequest_churn.f",
            # claude.md #217: t.reply(response)/NAME.postMessage(x).
            # callback(fn) -- real, concurrent, at-volume request/
            # response traffic in both directions (main sending with a
            # reply expected, and a worker sending to main via the bare
            # form with a reply expected), so a leaked
            # FestinaPendingCallback node (registered but never
            # dispatched/removed) or a leaked reply payload box would
            # show up here.
            "thread_reply_callback_churn.f",
            # claude.md #231 (uraikus/festina#91): NAME.drain() -- real
            # churn of the new dispatching/drained_cond state
            # FestinaThreadHandle gained for this feature, at volume,
            # both for an ordinary thread (drained after every single
            # send, the tightest interleaving) and a pool. A leaked
            # message, a double-free, or a missed/duplicate broadcast
            # would show up here.
            "thread_drain_churn.f",
            # claude.md #234 (uraikus/festina#93): an img's own transform
            # + saveState()/restoreState() stack (a malloc'd, growing
            # per-image array -- freed with the image, or leaked), the
            # clears, and img.drawImage (including the snapshot copy an
            # image drawn onto ITSELF takes, and an owning clip() source
            # passed straight in), all at volume, headless.
            "image_layer_churn.f",
            # claude.md #234: the same methods from a worker thread's own
            # `on message`, while main paints its own layers with the
            # per-call colour forms -- the race those overrides used to
            # have on the global fill/border state is what this exists
            # to keep fixed (TSan, via scripts/thread_tsan_stress.sh).
            "thread_image_layer_churn.f",
            # claude.md #236: a throw reached through intermediate
            # frames releases every local those frames hold (text,
            # stack arr/map buffers, heap and stack structs, arr of
            # structs, blob, loop-body locals, an escaping parameter, a
            # rethrowing catch, a JSON failure two frames down) -- the
            # leak claude.md #157 documented. Only runnable under ASan
            # at all since claude.md #235 (libc setjmp/longjmp).
            "throw_unwind_churn.f",
            # claude.md #259: a throw that crosses one of the RUNTIME's
            # own C frames -- the case #236's cleanup stack left open,
            # since a longjmp past festina_array_sort skips that frame's
            # own free() of its merge scratch. Churns both runtime
            # functions that call back into Festina from under a
            # reachable try (array sort, map forEach), plus a nested
            # sort inside a comparator and a comparator that catches its
            # own throw -- the shapes that unbalance the cleanup stack
            # rather than merely leak. Verified to FAIL without the fix
            # (4,000 stranded scratch buffers).
            "callback_throw_churn.f",
            # claude.md #260: a table-row column read off a CALL-RESULT
            # array (`rows()[0].name`) -- the project's own
            # longest-standing documented leak (#85/#119/#224), closed
            # by parking the array on the enclosing member chain. Every
            # position that shape appears in: a plain binding, a scalar
            # column, a discarded result, an interpolation, a
            # comparison, a call argument, plus the already-fine
            # name-bound control. A text column must be COPIED and a
            # blob column RETAINED before the array (and the row inside
            # it) dies, so getting this wrong is a use-after-free or a
            # double free, not a leak -- which is why it runs under
            # ASan, not LeakSanitizer alone. Verified to FAIL without
            # the fix.
            "row_chain_churn.f",
            # claude.md #265: a table row carries the ordinary refcount
            # header now, so it is an ordinary refcounted value
            # everywhere -- bound, aliased, passed, returned, stored in
            # a container, freed by hand, and outliving the array it
            # came from. Every shape here was broken before that: two
            # CRASHED (a row returned from a function that owned its
            # array), one leaked its array on every access, and the rest
            # only worked because the array was leaked rather than
            # reclaimed. A double-free test as much as a leak test --
            # one release too many frees a row the array is still going
            # to release. Verified to FAIL without the change, with a
            # heap-use-after-free.
            "row_ownership_churn.f",
            # claude.md #267: a struct that is a member of an enum,
            # built by the JSON parser. Every other construction site
            # tags such a struct in a WIDENED header (claude.md #176);
            # the from-JSON builder did not, so a successful parse
            # produced an untagged struct that crashed when used as its
            # enum, and a failing one released the half-built value
            # through the TAGGED release function -- freeing payload-16
            # of an allocation that only reached payload-8. A
            # memory-corruption test first: good and bad input alternate
            # so both paths run every iteration, and the parsed value is
            # used AS its enum so a missing tag cannot pass unnoticed.
            # Verified to FAIL without the fix (heap-buffer-overflow).
            "enum_json_churn.f",
            # claude.md #262: `.length` off a member chain whose
            # receiver is NOT an array -- a blob/text/ascii field of a
            # call-result struct. Those three cases dropped the chain's
            # parked bases entirely, leaking the whole object the field
            # came from. A use-after-free test first and a leak test
            # second: the drop was masking an over-release of the field
            # itself, so draining without removing that release is a
            # heap-use-after-free (confirmed under ASan before the fix
            # was written). The shared blob read back after the loop is
            # what catches that direction. Verified to FAIL without the
            # fix (66,000 allocations).
            "chain_length_churn.f",
            # claude.md #272: text.trim(), blob.byteAt(i) and
            # blob.slice(a, b). trim() and slice() both hand back a
            # FRESH OWNED text -- a malloc'd copy, never a pointer into
            # the receiver -- so both leak if the temporary is dropped
            # and double-free if something mistakes the copy for a
            # borrow into the blob's own buffer. The loop mixes a named
            # receiver read repeatedly (must NOT be released) with a
            # call-result receiver (must be), over a non-ASCII file so
            # slice() is really copying multi-byte sequences.
            "bytes_trim_churn.f",
            # claude.md #245: pool.postMessage(x) with no index --
            # main plus 3 feeder threads all auto-selecting against the
            # SAME handles array and round-robin counter at once, 12,000
            # messages total. A leak in festina_thread_pool_select's own
            # handle resolution, or in the round-robin counter's global,
            # would show up here.
            "thread_pool_auto_churn.f",
            # claude.md #246: on request use NAME -- the same real,
            # concurrent, at-volume live-connection hand-off
            # thread_giverequest_churn.f already covers, but through the
            # parser sugar and targeting a pool (so the bare, auto-
            # selecting giveRequest path above gets exercised too, not
            # just the indexed form).
            "thread_use_request_churn.f",
            # claude.md #248: req.send()'s new outbound keep-alive
            # connection pool -- four separate driver threads, each
            # with its own private `__thread` pool, all hammering the
            # same upstream host:port concurrently at volume. A leak in
            # a pool slot's own strdup'd host string, or a double-close
            # of a reused fd, would show up here.
            "http_client_pool_churn.f",
            # claude.md #248: the leak-freedom half of the SAME
            # feature that only a sanitizer run can confirm -- a pure
            # OUTBOUND-client thread (no on request/openPort of its
            # own) killed and respawned repeatedly, mid-pool-usage.
            # Before codegen.py's widened has_http_context or
            # self.uses_http condition, a client-only thread got no
            # teardown hook wired at all, so its own pooled
            # connections' strdup'd host strings would leak on every
            # single kill().
            "http_client_pool_kill_live_churn.f",
        }
