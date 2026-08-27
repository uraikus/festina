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
    People first = rows[0]   // borrowed
    total = total + first.id
    if rows[0].undefined('name') { log('unreachable') }
    free first               // drops the binding only
    free rows
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

        The canary leaks on purpose and the harness must say so. It is
        the row-array residual claude.md #119 documents as deliberate: a
        table-row element off a call-result array (`rows()[0]`) cannot
        retain its row past the array (rows have no header of their
        own), so the array is knowingly leaked -- see todo.md. Two
        previous canaries were retired because the compiler fixed them
        (the chained call result by claude.md #108/#117, the reference
        cycle by claude.md #120), which is exactly the failure mode a
        canary is supposed to have: it stops leaking, this test fails
        loudly, and nobody discovers months later that the harness had
        been vacuous.
        """
        canary = tmp_path / "canary.f"
        canary.write_text(
            "table People { id:int name:text }\n"
            "sqlite('DELETE FROM People')\n"
            "sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'row'])\n"
            "arr[People] func rows() {\n"
            "    arr[People] r = sqlite('SELECT * FROM People')\n"
            "    return r\n"
            "}\n"
            "int i = 0\n"
            "while i < 200 { text got = rows()[0].name i = i + 1 }\n"
            "log('done')\n"
        )
        result = _run_harness(str(canary))
        if result.returncode == _SKIP_EXIT:
            pytest.skip(result.stderr.strip() or "sanitizers unavailable")
        assert result.returncode != 0, (
            "the leak harness reported a known-leaking program as clean, which "
            "means it is not instrumenting anything:\n" + result.stdout)
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
            # claude.md #177: exec(args, callback)'s own new argv-deep-
            # copy/payload/trampoline path -- concurrent dispatches in
            # flight together every pass, on both the success path (a
            # real, quick child process) and the graceful-failure path
            # (a missing executable, which must still deliver -1 through
            # the callback rather than leaking the payload it already
            # allocated), with argv length varying pass to pass.
            "exec_callback_churn.f",
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
        }
