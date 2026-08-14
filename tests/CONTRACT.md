# Test contract for the Festina language spec (`claude.md`)

## Status

The `festina/` package at the repository root implements the front end
of the spec in `claude.md` (lexing, parsing, type resolution, semantic
analysis) **and** now a real LLVM codegen backend + native C runtime:
`bin/festina program.f -o program` produces a standalone executable that
needs neither Python nor `festina/` to run. Automatic SQLite table
creation and schema synchronization (claude.md #28-31) is implemented
for real against `festina.sqlite`, including the temp-table rebuild path
for dropped/retyped columns, with data preservation verified by the
`claude.md #31` worked examples as tests. `sqlite()` queries (claude.md
#32-34) are implemented too -- `SELECT` into a declared `arr[Table]`
with field access on the resulting rows, and parameterized
`INSERT`/`UPDATE`/`DELETE`/`SELECT` via a literal params array (see
festina/codegen.py's module docstring's "Query rows" note for the row
representation and the params-must-be-a-literal-array restriction).
Arrays (claude.md #26) are
implemented too -- literals, indexed get/set, nesting, function
params/return values, and (claude.md #63) `.length` -- though claude.md
still doesn't specify bounds checking or array growth, so neither of
those exist (see festina/codegen.py's module docstring). claude.md
#55-58 (added after a design review of the first codegen pass turned up
real bugs -- see below) are implemented too: int/float never convert
implicitly in any operator, `int.toFloat()`/`Math.floor/ceil/round/trunc`
are the only conversions, division/modulo by zero returns `null` instead
of crashing, and struct/table names live in their own namespace. So are
claude.md #60/#61 (`for`/`while` loops, including the loop-variable
scoping rule and `while true`) and #66 (postfix `++`/`--` on mutable
`int` variables) -- there's still no `break`/`continue` (claude.md
doesn't define either). Multi-file compilation (claude.md #5-6) is
implemented too: `festina.imports.build_program` resolves the full
import graph and merges every file into one compilation unit, and
`bin/festina`/`festina.cli.compile_file` actually call it now (they
used to only ever compile the single entry file). So is claude.md
#67/#68 (regex, string match/replace): `regex()` is a builtin function
(like `sqlite()`/`loadImage()`, not a dedicated `/pattern/` literal),
`.test()`, `.match()`, and `.replace()`/`.replaceAll()` are recognized
Call-on-Member patterns the same way `Math.floor`/`int.toFloat()`
already are, and the whole feature is built on POSIX extended regular
expressions (`<regex.h>`, already part of libc) rather than a bundled
or external regex engine, per claude.md #59.
claude.md #37/#39/#40 (`img`/`loadImage()`, `drawRect`/`drawCircle`/
`drawText`/`drawImage`, `on click`/`on mouse`/`on key`/`on resize`/`on
close`, `clientWidth`/`clientHeight`) are implemented too, per the
user's own clarification that this means a real on-screen window --
"not a file" -- showing only the drawing canvas ("a canvas renderer",
not a GUI with any chrome): X11 (Xlib) + Cairo's Xlib surface backend,
not a GUI toolkit (GTK/SDL2/Qt), since claude.md #59 favors the smallest
dependency that does the job and both X11 and Cairo were already
available in the dev environment. The window is undecorated (Motif WM
hints), starts at 800x600, and is opened lazily -- `CodeGen.uses_graphics`
(mirroring the pre-existing `uses_sqlite` flag) is set by any `draw*`
call, a bare `clientWidth`/`clientHeight` reference, or an `on
click`/`mouse`/`key`/`resize`/`close` handler declaration, gating
`festina_graphics_init()`/`festina_run_event_loop()` calls in `main()` --
*except* `loadImage()` alone, which deliberately does NOT set it: Cairo
decodes PNGs from its own in-memory decoder, needing no X server at all,
so a program that only loads an image (never drawing it or opening a
window) shouldn't be forced to have a display. click/mouse/key/resize/
close are the only five event names with a real runtime source (matching
claude.md #40's own worked examples) and each is required to declare a
fixed signature (`(x:int, y:int)` for click/mouse, `(key:text)` for key,
no parameters for resize/close) -- the C runtime registers each through
a fixed function-pointer type per event, so a mismatched signature would
be a silent ABI mismatch rather than just an unusual choice; any other
event name still compiles (it's ordinary code) but is simply dead, since
nothing ever fires it. `on resize` fires on a genuine window size change
(X11's ConfigureNotify) and clears the canvas back to white at the new
size (matching how resizing a browser's `<canvas>` element also clears
it -- `clientWidth`/`clientHeight` are themselves named after that DOM
API); `on close` fires right before the window actually closes, on the
same WM_DELETE_WINDOW ClientMessage the window's own standard
close-button handling already used, and cannot cancel the close.
`clientWidth`/`clientHeight` are read-only global ints reporting the
canvas's *current* size (not a compile-time constant, since a resize can
change it) -- pre-registered directly into semantic.py's `global_scope`
so `Scope.define`'s own duplicate-declaration check rejects a user
variable/function/struct/table with either name for free, and blocked
from assignment the same way claude.md #63's `.length` already is.
Verified against a real (virtual) X server, not just reasoned about --
see "Why the tests are structured this way" below for
`tests/test_graphics.py`/`TestGraphics`.

claude.md #69 (setTimeout/setInterval/clearTimeout/clearInterval) is
implemented too -- JS-style timers, added because Festina otherwise had
no way to schedule work after the fact. Since Festina has no
first-class functions or closures, the callback can only be the bare
name of an already-declared, zero-parameter, void-returning function --
semantic.py's `_infer_call` checks this structurally (an
`ast.Identifier` resolving to a `Symbol` with `kind == "function"`,
`type is None`, and an empty `node.params`), not through the normal
expression-typing path, since the identifier isn't being used as a
*value* here, its *declaration* is what's being validated. That
function's own LLVM symbol (`@<name>`) is already exactly the `void
(*)(void)` function pointer the runtime's `festina_set_timeout`/
`_set_interval` expect -- the same convention `on resize`/`on close`
handlers already use, so `_emit_timer_call` needs no IR machinery of
its own beyond that. `CodeGen.uses_timers` (set only by setTimeout/
setInterval, not clearTimeout/clearInterval alone) is a separate flag
from `uses_graphics` -- a timers-only program never opens a window, and
a graphics-only program never touches the timer machinery -- but both
gate the same call in `main()`, now named `festina_run_event_loop`
(renamed from `festina_graphics_run`, which is what it was called back
when graphics was the only thing that could ever block there): with
graphics in use, it multiplexes X11 events and timer deadlines on one
`select()` call so both an `on click` handler and a `setInterval`
callback stay responsive together, not just one or the other; without
graphics, it sleeps until the next timer deadline and fires it, for as
long as there's still an active timer to wait for, exactly matching
Node's "exits once the event loop is empty" behavior -- a program that
only calls setTimeout() exits once every one-shot timeout has fired,
while one that calls setInterval() and never clears it runs forever,
exactly like an uncleared setInterval() would in a real JS runtime.
Verified end to end, including the combined graphics-and-timers case
(a `setInterval` callback and an `on click` handler both firing while
the same window stays open) against a real (virtual) X server -- see
"Why the tests are structured this way" below for
`tests/test_timers.py`/`TestTimers`.

claude.md #38 (aud, loadAudio(), .play()/.stop()/.isPlaying()) is
implemented too -- a loaded clip is `ptr` to an opaque `FestinaAudio`
(decoded PCM plus playback state, same lower-to-`ptr` convention as
img/regex/table values), and play()/stop()/isPlaying() are ordinary
Call-on-Member patterns in both semantic.py and codegen.py, the same
family as Math.floor/the regex methods above -- claude.md enumerates
exactly these three methods for `aud`, so (unlike log()/fail()/
sqlite()'s deliberately open shape) any other method call on an `aud`
value is now a compile error rather than the permissive fallthrough
`aud`/`img` used to share back when neither had any real methods
modeled. `loadAudio()` only supports WAV (16-bit PCM) -- claude.md's
own example names a `.mp3`, but unlike Cairo (which decodes PNG on its
own) nothing this project already depends on can decode MP3 without a
real new library, so WAV -- parsed directly in festina_runtime.c with
zero decoder dependencies at all -- is the implementation-defined
choice, the same kind of call PNG-only images already made for a
different reason. Playback runs through a real ALSA ("default") output
device -- picked for the same "smallest dependency that does the job"
reasoning that picked Xlib over a GUI toolkit for graphics -- opened
*synchronously* inside `festina_audio_play()` itself (so a missing or
unusable device fails loudly and immediately, festina_fail(), the same
as "could not open the X display" for graphics) before the actual PCM
writing moves to a background pthread, so a playing clip doesn't block
the rest of the program the way having a separate `isPlaying()` to
poll implies it shouldn't. Two guarantees make the test suite below
deterministic rather than timing-dependent: `isPlaying()` is true the
instant `play()` returns (the flag is set synchronously, before the
thread is even spawned) and false the instant `stop()` returns (`stop()`
joins the thread before returning, rather than merely signaling it).
Calling `play()` again while already playing restarts from the
beginning (stopping the previous thread first) -- claude.md doesn't say
what play()-while-playing should do; this is the least surprising
choice, matching a browser's own `<audio>` element. Audio does not keep
a program running the way an uncleared `setInterval` does -- if the
program reaches its natural end while a clip is still playing, the
process exits anyway. Verified end to end against a real (virtual) ALSA
device -- see "Why the tests are structured this way" below for
`tests/test_audio.py`/`TestAudio`.

A spec-compliance/security/robustness audit (prompted directly, not
something the earlier per-feature work happened to already cover) found
and fixed eight real bugs, all in `festina/semantic.py` unless noted,
each with its own regression test (mostly in `tests/test_semantic_errors.py`;
see individual test names below and each fix's own code comment for
the full reasoning):

- claude.md #36's own only worked example (`blob data =
  'path/to/file'`) failed semantic analysis outright -- a string
  literal infers as `text`, and `text`/`blob` were fully incompatible
  types with no exception, meaning blob could never actually hold a
  value at all (nothing else in the language constructs one either).
  Fixed by allowing `text -> blob` assignment specifically, in
  `check_assignable` -- the only direction claude.md's example ever
  shows, and safe since both share the identical `ptr` runtime
  representation. `log()` on the one blob value that could then exist
  crashed the *compiler itself* with a bare Python `KeyError`
  (`festina/codegen.py`; blob passed the "is this a PrimitiveType"
  check in `log()`'s dispatch but had no entry in the type->runtime-
  function dict) -- previously unreachable for the same reason, but a
  real crash risk the instant blob became constructible. See
  `tests/test_types.py::TestBlobImgAud` and
  `tests/test_codegen.py::TestBlob`.
- `==`/`!=`/`<`/`>`/`<=`/`>=` had no general type-compatibility check
  at all beyond the existing int/float-mixing rule -- `5 == 'x'`,
  `'a' < 'b'`, `bool == int`, all passed semantic analysis. The
  equality/ordering cases reached codegen with no general fallback for
  a genuine mismatch (only the text-specific branch of `_emit_binop`
  even inspects operand types) and produced *invalid LLVM IR* --
  `festina_str_eq(ptr 5, ...)`, a raw integer constant where a pointer
  was required -- surfacing as a confusing internal "LLVM object
  emission failed" error instead of a clear compile-time one. Fixed
  with two new checks in the `BinOp` branch: `==`/`!=` require the
  same type (NULL valid against anything per #25; blob/text mutually
  comparable for the same reason as above), `<`/`>`/`<=`/`>=` require
  both operands be int or float (matching what codegen actually
  implements -- text already raised its own clean `CodegenError` for
  this, but nothing stopped e.g. two `bool` operands from silently
  reaching codegen's numeric `icmp` path).
- Array indexing had the identical shape of bug: `_infer_member`'s
  computed-index branch inferred the index expression's type and then
  simply discarded it, never checking it against anything, despite
  claude.md #65 explicitly saying "The index expression must resolve
  to int." `a[1.5]`/`a['x']` both passed semantic analysis and reached
  codegen, which emits a `getelementptr` using the index value's own
  LLVM representation regardless of its Festina type -- a raw
  `double`, or a `ptr`, where an `i64` GEP index is required -- again
  invalid IR, again a confusing internal codegen error rather than a
  clean one. Covers both a read (`a[i]`) and a write target
  (`a[i] = v`), since `Assign`'s `target_type` goes through the same
  `_infer_member` call.
- A `const`-declared variable could be reassigned with plain `=`
  (`const int x = 5; x = 10` compiled and silently changed `x`) --
  postfix `++`/`--` already rejected a constant operand, but plain
  assignment shared no such check. Undermines claude.md #22's own
  "Constants should be available for compiler optimization": that
  guarantee only holds if a const genuinely never changes. Fixed with
  a `Symbol.kind == "constant"` check in the `Assign` branch, mirroring
  the existing `.length`/`clientWidth` read-only checks' placement.
- `void func f() { return 5 }` compiled -- the `5` was evaluated (so a
  side-effecting return expression's side effects still happened) but
  its result was silently discarded, `ret void` emitted regardless of
  what was returned. `int func f() { return }` (bare return, no value,
  in a non-void function) also compiled, with nothing checked in
  either direction. claude.md #23's own void-vs-non-void distinction
  ("A function that does not return a value uses void") directly
  implies both directions are supposed to be errors; fixed in the
  `Return` statement handling. The *other* direction claude.md is
  silent on -- a non-void function whose body doesn't return on every
  path, which still compiles and falls off the end returning that
  type's zero value -- was deliberately left alone (per #54's
  ambiguity rule: an undetermined case is not something to invent a
  new restriction for) but is now explicitly documented as a
  considered choice in `festina/codegen.py`'s `_emit_func`, where it
  previously had no comment explaining it at all.
- `drawRect`/`drawCircle`/`drawText`/`drawImage`/`loadImage`/
  `loadAudio`/`regex`/`setTimeout`/`setInterval`/`clearTimeout`/
  `clearInterval` aren't lexer keywords (unlike `log`/`fail`/`sqlite`),
  so a user could declare `void func drawRect(...) { ... }` -- it
  compiled fine, but every *call* to `drawRect(...)` still resolved to
  the builtin (codegen's Call dispatch always checks the builtin name
  first), making the user's own function permanently unreachable,
  silently. README.md used to excuse this specifically because
  graphics/audio/timers weren't implemented yet, so the collision
  couldn't actually bite -- now that they are, it can, so `analyze_func`
  now rejects declaring a function with any of these names
  (`category="duplicate declaration"`). A *variable* with one of these
  names is unaffected (only a function declaration collides, since only
  `name(...)` call syntax is what builtin dispatch intercepts).
- The one genuine security finding: `festina_sync_table`
  (`runtime/festina_runtime.c`, #28-31) built several SQL statements
  incrementally across a loop over the declared columns, in the
  textbook-looking-but-actually-unsafe form
  `pos += snprintf(buf + pos, sizeof(buf) - pos, ...)`. snprintf's
  return value is how many bytes *would* have been written if the
  buffer were big enough, not how many actually fit -- so once
  accumulated output exceeds the buffer, the *next* iteration's
  `sizeof(buf) - pos` is unsigned arithmetic between a smaller and
  larger value, silently underflowing to a number near `SIZE_MAX`.
  snprintf is then told it has ~18 exabytes of room and gladly writes
  straight past the real (2048-or-so-byte) stack array. Verified as a
  genuine, reproducible stack-smashing crash under AddressSanitizer: a
  `table` declaration with enough columns, or long enough column/table
  names, that the generated SQL exceeds one of four affected fixed-size
  buffers (`sql`/`create_sql`, both 2048 bytes, plus `dest_cols`/
  `src_cols`, both 1024 bytes, in the column-add and column-drop/retype
  rebuild paths respectively) reliably corrupted the stack -- something
  any Festina program with a sufficiently wide table could trigger, not
  a contrived adversarial input. Fixed with a new
  `festina_check_sql_buffer` helper, called at the top of every loop
  iteration that accumulates into one of these buffers (and once more
  after each loop, before any final fixed-text append) -- guaranteeing
  `sizeof(buf) - pos` is never computed once `pos` has already reached
  or passed the buffer size, turning what would be undetected memory
  corruption into a clear `festina_fail()` naming the actual condition
  ("too many columns, or column/table names too long") instead. Fix
  verified the same way the bug was found: rebuilt against
  AddressSanitizer, a 40-long-column table that used to crash now fails
  cleanly with no ASAN report, and a normal small table still succeeds.
  See `tests/test_codegen.py::TestAutomaticSqliteSchemaSync::test_a_table_too_wide_for_the_runtimes_sql_buffer_fails_cleanly`
  for the (non-ASAN, since the normal test pipeline doesn't build with
  it) regression coverage: it locks in the clean-failure behavior, not
  memory safety itself, which was checked by hand at fix time instead.

Two things this same audit pass confirmed were *not* bugs, worth
recording so they aren't re-litigated: table/column names embedded
directly into SQL text (unavoidable -- SQL can't parameterize
identifiers) can't carry a SQL-injection payload through legitimate
Festina syntax, since the lexer's own `IDENT` token
(`[A-Za-z_][A-Za-z0-9_]*`) can't produce a quote, semicolon, or any
other SQL metacharacter in the first place; and every `log()`/
`festina_fail()` call in the runtime passes untrusted text as a `%s`
*argument*, never as the format string itself, so there's no
format-string vulnerability despite text values flowing directly into
`printf`-family calls throughout.

"Real compilation, minimal setup" stages 1, 2, and 3 -- claude.md #59,
added alongside these stages to make the requirement explicit rather
than implicit in the implementation -- are also done (see README.md's
"Deployment"/"Setup" sections for the full staged plan and the current
dependency list): sqlite3 is statically linked into compiled programs
(no libsqlite3.so needed to *run* one), `scripts/package_compiler.sh`
packages `festina/` into a standalone binary via PyInstaller (no Python
install needed to *use the compiler* with that binary -- verified by
running it with every `python`/`python3*` on `PATH` replaced by a
command that always fails), and `festina/llvm_backend.py` compiles the
generated LLVM IR to an object file itself via libLLVM's C API rather
than handing a .ll file to clang -- clang is no longer specifically
required to *use* Festina, just some working C compiler (gcc verified
working end to end). Per #59's fourth point, `festina/cli.py`'s
`_run_tool` also turns a genuinely missing dependency (pkg-config, or
any C compiler) into a specific, actionable error naming it and how to
install it, rather than a raw exception -- verified directly by hiding
each tool from PATH in turn; `_pkg_config` got the same treatment for a
genuinely missing `.pc` package (a pre-existing gap that already applied
to `sqlite3`, caught and fixed while wiring up graphics's own
`cairo-xlib` pkg-config dependency, not something graphics introduced).
All 430 tests in this directory pass against it: 422 given a working C
compiler, plus 2 more (`tests/test_packaging.py`) given `pyinstaller`
too, plus 6 more (`tests/test_codegen.py::TestGraphics`'s interactive
click/mouse/key/resize tests, the one confirming the initial
`clientWidth`/`clientHeight` values, and `TestTimers`'s combined
graphics-and-timers test) given `Xvfb`+`xdotool` too -- all skip
cleanly, independently, without any of those three (see below).
`tests/test_audio.py`/`TestAudio` need none of the three either --
the null-device technique they use (see conftest.py's
`audio_null_env`) needs no extra tool install, only the C compiler
`compile_and_run` already requires.

claude.md #55-58 exist because of bugs a design review found by actually
running compiled programs, not just reading the code: returning a struct
by value handed the caller a pointer into an already-popped stack frame
(silently printed garbage); `int x = null` / `float x = null` failed to
link (`null` is only valid IR for a pointer type, and i64/double have no
spare bit pattern for it); and a float literal small/large enough that
Python's `repr()` used scientific notation (e.g. `0.0000001`) also failed
to link, for the same "not valid float-literal syntax" reason. Fixing
the null representation properly created an opening to also resolve a
pre-existing inconsistency (assignment strictly rejected mixed int/float,
but arithmetic silently allowed it) -- claude.md #55-56 close that gap by
making the stricter behavior the rule everywhere, with `Math`/`.toFloat()`
as the escape hatch, rather than picking a side ad hoc in code with no
spec backing either way.

See README.md's "Implementation Status" section for the current
implemented-vs-not matrix; the short version: nothing is left
unimplemented anymore -- every claude.md construct this compiler
targets generates real code now (audio was the last one; see above).

(An earlier, unrelated JS-subset prototype -- `compiler/`, plus
`build.sh`/`jit_run.py`/`run_jit.sh` and `runtime/runtime.c` for
building/running it -- used to live alongside `festina/` in this repo.
It predated `festina/` and was never exercised by these tests; removed
as unrelated clutter once `festina/llvm_backend.py` made its
JIT-without-clang trick (`jit_run.py`'s whole reason for existing)
redundant for the real language too.)

## Why the tests are structured this way

Every test module gets its `festina.*` submodule through a conftest.py
fixture (`import_spec_module`) rather than a plain `import festina.x`. If
a module is later removed or renamed, tests against it skip with a clear
reason instead of erroring the whole session -- but a real bug *inside*
an existing module still fails loudly (verified directly: a deliberately
broken stub module raises, it doesn't skip). Each test's docstring/comment
cites the `claude.md` section it encodes, so a failure points straight at
the rule in question.

`tests/test_codegen.py` additionally uses a `compile_and_run` fixture
that actually compiles+links generated IR against the Festina runtime
and runs the resulting binary. It prefers `clang` but accepts `gcc`/`cc`
too (stage 3 means the C compiler no longer needs an LLVM-IR-text
frontend, just the ability to compile festina_runtime.c and link object
files -- see festina/cli.py and festina/llvm_backend.py's docstrings).
It skips (with a distinct, toolchain-specific reason) if no C compiler
is on `PATH` at all -- unlike the `SPEC_UNIMPLEMENTED_REASON` skips
above, this isn't "the feature doesn't exist," it's "this environment
can't link native code." Nothing codegen genuinely doesn't support
remains (audio was the last one -- see "Status" above), so this tier
is nearly empty now; what's left in `TestUnrecognizedEventName` just
inspects generated IR text directly, no C compiler needed either way.
The one exception on the sqlite() side is
`test_non_literal_params_argument_is_a_clear_error`, which checks a
compile-time restriction (params must be a literal array) the same
no-C-compiler way, even though `sqlite()` itself is otherwise fully
implemented.

`tests/test_llvm_backend.py` tests `festina.llvm_backend` directly and
only needs libLLVM itself (via its own `llvm_backend` fixture's
`available()` check) -- a narrower requirement than `compile_and_run`'s
full C-compiler skip, since this module doesn't touch a C compiler.
`test_codegen.py`'s `TestMinimalBuildDependencies` covers the two ends
of stage 3 concretely: gcc actually producing a working binary when
libLLVM is available, and the original clang-only pipeline still
working (via `monkeypatch`) when it isn't. `TestMissingDependencyErrors`
covers claude.md #59's fourth point (a missing dependency must fail
clearly) by actually hiding pkg-config/cc from a synthetic PATH (a
`path_without` fixture, via `monkeypatch.setenv`) and asserting on the
resulting error message, rather than just testing `_run_tool` in
isolation.

`tests/test_numeric_conversion.py` covers claude.md #55-58 at the
parser/semantic level only (same `parser`/`semantic`/`errors` fixtures as
the rest of the front-end suite, no `clang` needed); the matching runtime
behavior (Math/`.toFloat()`'s actual output, division-by-zero surviving
and producing *something* rather than crashing) is tested end-to-end in
`test_codegen.py`'s `TestNumericConversion`, plus regression coverage
there for the three bugs #55-58 were written in response to (see
"Status" above): a struct returned by value, and `null`/scientific-notation
float literals compiling and linking successfully.

`tests/test_loops.py` covers claude.md #60/#61/#63/#66 (for/while
loops, `.length`, postfix `++`/--) at the parser/semantic level, same
split as `test_numeric_conversion.py`; the matching end-to-end runtime
behavior (a compiled loop actually iterating the right number of times,
loop-variable scoping surviving a real function frame, an iterative
Fibonacci) lives in `test_codegen.py`'s `TestLoops` and
`TestArrayLength`.

`tests/test_packaging.py` covers stage 2 (claude.md #59) -- it actually
runs `scripts/package_compiler.sh`, a real PyInstaller build (tens of
seconds, via a session-scoped fixture so every test in the file reuses
one build rather than re-packaging per test), and then runs the
resulting binary with `python`/`python3*` shadowed on `PATH` by a
command that always fails, the same "prove it, don't just reason about
it" standard the rest of this test suite holds itself to. Skips cleanly
if `pyinstaller` isn't installed -- deliberately not in
requirements-dev.txt, since nothing about developing or testing
`festina/` itself should need it (claude.md #59's own principle,
applied to this repo's own tooling, not just what it generates).

`tests/test_imports.py`'s `TestBuildProgram` covers claude.md #5-6's
multi-file compilation at the parser/semantic level (`build_program`
merging files, cross-file struct/function resolution, duplicate
declarations across files, and -- the one that would be easy to get
wrong silently -- an error inside an *imported* file naming that file,
not the entry file, in the resulting message). `test_codegen.py`'s
`TestMultiFileCompilation` covers the same feature end to end via a new
`compile_multi_and_run` fixture (conftest.py; a multi-file sibling of
`compile_and_run`, sharing its C-compiler-availability skip logic
through a `_require_c_compiler()` helper rather than duplicating it):
struct/function sharing across files, transitive imports, a diamond
import graph actually compiling once rather than emitting duplicate
LLVM globals for a table declared in the commonly-imported file, and
schema sync still firing for a table declared in an imported file.

`tests/test_regex.py` covers claude.md #67/#68 (regex, string
match/replace) at the parser/semantic level, same split as
`test_loops.py`/`test_numeric_conversion.py` -- argument-count and
argument-type checking for `.test()`/`.match()`/`.replace()`/
`.replaceAll()`, and that calling any of them on the wrong receiver
type (e.g. `.match()` on `int`) is rejected the same way an undefined
struct field access already is. `test_codegen.py`'s `TestRegex` covers
the same feature end to end, including two cases that are easy to get
subtly wrong in the runtime rather than the compiler: a pattern that
can match zero-width (`x*` against text with no `x`) must not hang --
verified by actually letting `compile_and_run`'s subprocess timeout be
the judge, not just eyeballing the output -- and an invalid pattern
must fail at *runtime* with a clear message (claude.md #67 says so
explicitly), not at compile time, since nothing in this pipeline
parses regex syntax itself before handing it to `regcomp()`.

`tests/test_graphics.py` covers claude.md #37/#39/#40 (image, graphics,
events) at the parser/semantic level, same split as `test_regex.py` --
argument-count/type checking for `drawRect`/`drawCircle`/`drawText`/
`drawImage`/`loadImage` against the fixed signature each one's own
claude.md example uses; the fixed-signature restriction on `on
click`/`mouse`/`key`/`resize`/`close` (while an unrecognized event name
stays unconstrained, since only those five have a runtime source at
all); and `clientWidth`/`clientHeight` -- usable as a plain int
identifier (including inside a template literal, e.g. in an `on resize`
body), rejected on assignment (`category="invalid assignment"`, the
same as claude.md #63's `.length`), and rejected on redeclaration
(`Scope.define`'s own "already declared" check, exercised for free
since these two are pre-registered into `global_scope` -- see
semantic.py's `_CLIENT_SIZE_GLOBALS`). `test_codegen.py`'s `TestGraphics`
covers the same feature end to end, in three tiers: (1)
`test_compiles_and_links_successfully`, which needs only a C compiler
(no display) since it never runs the binary; (2)
`test_missing_display_is_a_clear_runtime_error`,
`test_invalid_image_path_is_a_clear_runtime_error`, and
`test_program_without_graphics_never_opens_a_window`, which run the
compiled binary with `DISPLAY` deliberately unset via `compile_and_run`
-- the last of these is what actually proves `loadImage()` alone and a
graphics-free program never require a display, not just what the code
comments claim; and (3) five interactive tests --
`test_click_dispatches_to_handler_with_correct_coordinates`,
`test_mouse_move_dispatches_to_handler_with_correct_coordinates`,
`test_key_dispatches_printable_and_named_keys` (a printable key like
"a" comes back as itself, a non-printable one like Escape falls back to
X11's own key name -- both asserted in one test, in the order the keys
were sent, since a compiled program's `log()` output is itself ordered),
`test_client_size_matches_the_initial_canvas_before_any_resize` (finding
the window at all is itself part of what's being proved here: that a
bare `clientWidth`/`clientHeight` reference opens one), and
`test_resize_dispatches_to_handler_and_updates_client_size` (drives an
actual `xdotool windowsize` and checks the handler saw the new size) --
which use two new conftest.py fixtures -- `x_display` (an existing
`DISPLAY` if set, otherwise a throwaway `Xvfb` instance, polled for real
readiness rather than a fixed sleep, since a fixed sleep proved flaky
under full-suite load) and `run_graphics_program` (compiles and starts
the binary in the background, line-buffered via `stdbuf -oL` since a
graphics program blocks in its event loop rather than exiting) -- and
drive the real rendered window with real simulated input via `xdotool`
(finding the window by its title, "Festina", via `xdotool search`),
asserting each handler actually ran with the right data by reading the
program's own `log()` output back -- polled for (via a `_wait_for_output`
helper), not just slept-then-read-once-and-asserted, for the same
"flaky in isolation vs. under full-suite load" reason `x_display`
itself polls for Xvfb readiness rather than sleeping a fixed amount (the
`test_resize_...` test above is what actually surfaced this: reliable
alone, occasionally failed as part of the full suite before the fix).
All five skip cleanly and independently if `Xvfb`/`xdotool` aren't
installed, the same opt-in/environment-dependent tier as
`compile_and_run`'s C-compiler skip and `test_packaging.py`'s
`pyinstaller` skip. Pixel-level rendering correctness (that
`drawRect`/`drawCircle`/`drawText`/`drawImage` paint at the right
position, not just that the program doesn't crash) was verified
manually via `xwd`+`netpbm` screenshots of a real running window rather
than automated, a deliberate choice to avoid pulling in more
image-comparison tooling for a check the interactive dispatch tests
above don't need; `on close` is the one handler NOT covered by an
automated dispatch test, for the same reason the close-button/
`WM_DELETE_WINDOW` path itself already wasn't before `on close` existed
-- both fire off the identical ClientMessage, and a bare Xvfb instance
runs no window manager to translate `xdotool windowclose` into it
(verified directly: it leaves the process running rather than firing
anything) -- an environment limitation of the test setup, not a gap in
the app's own (standard) handling of that protocol.

`tests/test_timers.py` covers claude.md #69 (setTimeout/setInterval/
clearTimeout/clearInterval) at the parser/semantic level: the callback
must be a bare identifier naming an already-declared, zero-parameter,
void-returning function (rejecting a variable name, an undeclared name,
a function with parameters, and a function with a return value, each as
their own test); the delay argument must be `int`; clearTimeout/
clearInterval each take exactly one `int` argument and return nothing;
both setTimeout and setInterval return `int` (a timer id). Almost all of
`test_codegen.py`'s `TestTimers` needs no display at all -- a
timers-only program never opens a window (`CodeGen.uses_timers` is
separate from `uses_graphics`) -- so it mostly uses `compile_and_run`
like any other runtime-behavior test: a timeout firing once,
an interval firing repeatedly until `clearInterval`, `clearTimeout`
cancelling a still-pending callback, a callback scheduling *another*
timer from inside itself (proving `festina_run_event_loop` recomputes
the earliest deadline fresh every pass rather than fixing it once at
loop entry, and that growing the runtime's timer array via `realloc`
from inside a callback that's itself being called from a loop iterating
that same array is safe), and a timers-only program actually exiting
once every timeout has fired. `test_uncleared_interval_keeps_the_program_running`
is the one case `compile_and_run`'s fixed 15s subprocess timeout can't
cleanly demonstrate (it would only make the test slow, not prove
anything a shorter wait doesn't already show), so it drives the
compiled binary directly instead -- start it in the background, confirm
it's still alive and still logging after a short wait, then kill it
directly, the same backgrounded-process pattern `run_graphics_program`
uses for graphics. `test_timers_and_graphics_work_together` is the one
test in the class that needs a real display (`x_display`/
`run_graphics_program`, reusing the `_find_window`/`_wait_for_output`
helpers this file promoted from `TestGraphics` methods to module-level
functions once `TestTimers` also needed them): it opens a window with
both a `setInterval` and an `on click` handler, confirms the interval
fires on its own, then confirms a real simulated click still dispatches
correctly *and* the interval keeps firing both before and after it --
proving `festina_run_event_loop`'s `select()` call is genuinely
multiplexing both event sources, not just alternating between them or
starving one.

`tests/test_audio.py` covers claude.md #38 (aud, loadAudio(), .play()/
.stop()/.isPlaying()) at the parser/semantic level, same split as
`test_timers.py` -- argument-count/type checking for loadAudio();
play()/stop()/isPlaying() taking no arguments and isPlaying() returning
bool; an unrecognized method or a non-`aud` receiver both being compile
errors (claude.md enumerates exactly three methods for `aud`, so this
isn't the permissive fallthrough `log()`/`fail()`/`sqlite()` get).
`test_codegen.py`'s `TestAudio` covers the same feature end to end, and
notably needs no opt-in skip tier the way `TestGraphics` does: the
null-device technique it uses (`audio_null_env` in conftest.py -- a
`$HOME/.asoundrc` redirecting ALSA's "default" PCM device to ALSA's own
built-in null plugin, the real ALSA config mechanism, not something
festina-specific, the audio equivalent of pointing `DISPLAY` at a
throwaway Xvfb) needs no extra tool install, since the null plugin
ships inside alsa-lib itself, which festina_runtime.c already links
against unconditionally -- so every test in this class only needs what
`compile_and_run` already requires, a working C compiler. Verified this
way rather than assumed: `snd_pcm_open(..., "default", ...)` genuinely
fails in this project's own dev environment without the override
(no `/dev/snd` node exists at all), and genuinely succeeds with it.
Coverage includes the two guarantees the rest of `TestAudio` leans on
to stay deterministic instead of timing-dependent (`isPlaying()` true
immediately after `play()`, false immediately after `stop()` --
verified directly, not just against a comment claiming it), `stop()` on
an already-idle clip being a safe no-op, calling `play()` again while
already playing not crashing or hanging, and the clear-error path for
a missing device, an unreadable path, and a non-WAV file each getting
their own test. `test_timers_and_audio_work_together` is the one test
that reuses claude.md #69's `setTimeout` to poll for a clip finishing
on its own (no `stop()` call) -- proof that the background playback
thread and the main-thread timer event loop coexist correctly, neither
blocking the other. `_write_wav` (a plain module-level function in
test_codegen.py, using the stdlib `wave` module -- no new test
dependency) generates the minimal valid 16-bit PCM WAV fixture every
playback-success test needs; the error-path tests need either no file
at all or deliberately invalid bytes instead.

## Public API implemented

```
festina/
    errors.py
        class CompileError(Exception):
            file, line, column, category, message
            # str(err) == "{file}:{line}:{column}: error: {message}"
        class CircularImportError(CompileError): ...

    lexer.py
        KEYWORDS: frozenset[str]           # claude.md #51 (+ a few
                                            # internal-only control words:
                                            # return/var/let/throw)
        SOURCE_EXTENSION = ".f"            # claude.md #4
        class Token: type, value, line, column
        def tokenize(source, filename="<string>") -> list[Token]
        # backtick templates with ${...} splice the interpolated
        # expression's own tokens into the stream (TSTRING_START/MID/END
        # bracket them); `import <path>` reads the rest of the line as a
        # single PATH token rather than tokenizing it as an expression.

    ast.py
        Program, ImportDecl, VarDecl, Param, FieldDecl, FuncDecl,
        StructDecl, TableDecl, EventHandler, Block, IfStmt, WhileStmt,
        ForStmt, Return, ExprStmt, Identifier, NumberLit, StringLit,
        BoolLit, NullLit, TemplateLit, ArrayLit, Assign, Ternary,
        LogicalOp, BinOp, UnaryOp, PostfixOp, Member, Call, ArrayTypeExpr

    types.py
        PrimitiveType(name) / StructType(name) / TableType(name) /
        ArrayType(element) / ImageType() / AudioType() / RegexType()
        -- frozen dataclasses, so equality/hashing work out of the box.
        RegexType() has no fields (claude.md #67: created only via the
        regex() builtin, never a dedicated literal, so there's only one
        shape of it -- unlike StructType/TableType). Likewise ImageType()
        (claude.md #37: `img`, created only via loadImage()) has no
        fields -- codegen.py lowers it to `ptr` (an opaque Cairo surface),
        the same convention as StructType/TableType/RegexType.
        type_name(t) -> str   # for error messages, e.g. "arr[int]"

    parser.py
        def parse(source, filename="<string>") -> ast.Program
        # raises festina.errors.CompileError for invalid syntax,
        # var/let/throw, ===/ !==, missing return types, untyped
        # params/fields, malformed imports, etc.

    imports.py
        def resolve_imports(entry_path: str) -> list[str]
        # canonical (os.path.realpath), deduplicated, dependency-first
        # order; raises CircularImportError on cycles (including
        # self-imports) without recursing infinitely.
        def build_program(entry_path: str) -> ast.Program
        # claude.md #5: resolves the full import graph and parses every
        # file into one merged ast.Program, in dependency order -- a
        # single-file program (no imports) is the degenerate case.
        # Each top-level statement is tagged `.file = <the path it came
        # from>`; semantic.analyze and codegen.CodeGen both re-read that
        # tag once per top-level statement (a single reassignment point,
        # not a change to every individual error site -- filename is a
        # free variable closed over by every nested function in
        # semantic.py, and self.filename in codegen.py is never cached
        # into a local, so both are resolved fresh on every access) so
        # errors from a merged multi-file program still name the file
        # they actually came from.

    semantic.py
        def analyze(program, filename="<string>") -> AnalyzedProgram
        # AnalyzedProgram: .symbols (name -> Symbol, global scope),
        # .structs (name -> {field: Type}), .tables (name -> {field:
        # festina-type-name-str}), .imports (list of raw import paths).
        # Single left-to-right pass; every fixture in this repo declares
        # structs/tables/functions before use, so no forward-reference
        # resolution was needed. structs/tables are never cross-checked
        # against Scope (claude.md #58: separate namespace by design, not
        # an accidental gap). claude.md #55/#56: BinOp rejects int/float
        # operands that differ (any operator, not just arithmetic);
        # Math.floor/ceil/round/trunc(x:float) -> int and
        # int_value.toFloat() -> float are recognized as Call-on-Member
        # patterns, not real declarations (no "Math" symbol exists).
        # claude.md #60/#61: WhileStmt/ForStmt conditions must be bool
        # (check_condition_bool, same helper if/ternary use); a ForStmt's
        # init variable gets its own child Scope so it's visible in the
        # condition/update/body but nothing analyzed after the loop can
        # see it. claude.md #63: `.length` on an ArrayType resolves to
        # int and is the *only* valid non-computed field an array has;
        # assigning to it is rejected before the generic Assign
        # type-check runs (that check alone can't tell a read from a
        # write target). claude.md #66: PostfixOp requires its operand be
        # an Identifier resolving to a non-constant int. claude.md #67/
        # #68: regex() is a BUILTIN_FUNCTIONS entry (like sqlite()/
        # loadImage(), returning RegexType()); pattern.test(text)/
        # value.match(regex)/value.replace(text-or-regex, text)/
        # .replaceAll(...) are recognized Call-on-Member patterns, same
        # family as Math.floor/int.toFloat() above -- checked by name
        # against the receiver's inferred type, not a real method table
        # (Festina has no general concept of methods on primitives).
        # claude.md #37/#39: _BUILTIN_SIGNATURES maps drawRect/drawCircle/
        # drawText/drawImage/loadImage to the fixed argument-type tuple
        # each one's own claude.md example uses (checked in _infer_call's
        # builtin dispatch branch); builtins with no entry there --
        # log/fail/sqlite/loadAudio -- stay fully permissive, unchanged
        # from before. claude.md #40: analyze_event_handler requires an
        # `on click`/`mouse`/`key`/`resize`/`close` handler to declare
        # exactly the signature _EVENT_SIGNATURES has for it
        # (`(x:int, y:int)` for click/mouse, `(key:text)` for key, no
        # parameters for resize/close) -- any other event name is
        # unconstrained, since only those five have a runtime event
        # source at all (see codegen.py below). claude.md #39:
        # clientWidth/clientHeight (_CLIENT_SIZE_GLOBALS) are
        # pre-registered directly into global_scope as read-only `int`
        # Symbols before any user code is analyzed, so a plain reference
        # resolves through the same Identifier/Scope.lookup path a real
        # global variable would, Scope.define's own "already declared"
        # check rejects a colliding user declaration for free, and the
        # Assign branch above rejects assigning to either one the same
        # way it already rejects assigning to `.length`. claude.md #69:
        # setTimeout/setInterval get their own branch in _infer_call,
        # checked *before* the generic BUILTIN_FUNCTIONS dispatch below
        # (not through it), since validating their first argument (a
        # callback) is structural, not type-based -- it must be an
        # ast.Identifier resolving to a Symbol with kind == "function",
        # type is None (void), and an empty node.params (Festina has no
        # first-class functions/closures, so nothing else was ever a
        # candidate); both return int (a timer id). clearTimeout/
        # clearInterval get a simpler adjacent branch: exactly one int
        # argument, returning nothing. claude.md #38: loadAudio(text) ->
        # aud is a _BUILTIN_SIGNATURES entry, same as loadImage();
        # play()/stop()/isPlaying() are recognized Call-on-Member
        # patterns on an AudioType receiver, same family as
        # Math.floor/the regex methods -- claude.md enumerates exactly
        # these three methods for aud, so (unlike log/fail/sqlite's
        # deliberately open shape) any other method call on an aud
        # value falls through to the generic "unknown member" path and
        # fails there, the same way an unknown struct field does
        # (AudioType used to share ImageType's fully-permissive
        # _infer_member fallback, back when neither had any real
        # methods modeled -- img still does, since claude.md #37
        # defines no methods on img at all, but aud's fallback is now a
        # hard error like everything else in _infer_member).

    sqlite_schema.py
        TYPE_MAP: dict[str, str]                        # claude.md #30
        def plan_sync(declared, existing) -> SchemaSyncPlan
        class SchemaSyncPlan: create, add_columns, drop_columns,
                               alter_columns
        create_table_ddl(...) / sync_ddl(...)  # best-effort SQL, not
        exercised by the tests

    compiler.py
        def compile_source(source, filename="main.f") -> CompileResult
        class CompileResult: ast, symbols, tables, structs, imports,
                              entry_function_name
        # front-end only (parse + analyze); does not invoke codegen.

    codegen.py
        def generate_ir(program, analyzed, filename="main.f") -> str
        # emits opaque-pointer LLVM IR text. Supports: primitives,
        # global/local vars & consts, functions, if/else, for/while loops
        # (_emit_for/_emit_while -- ordinary structured control flow, the
        # same _start_block/label pattern _emit_if uses; a for-loop's
        # init variable lives in a child Env scoped to just that
        # statement), the full expression grammar including postfix
        # ++/-- (_emit_postfix -- load/add-or-sub-1/store on the
        # operand's slot, returns the pre-increment value), structs
        # (heap-allocated via calloc, GEP field access -- see the module
        # docstring's "Struct storage" note for why not a stack alloca),
        # arrays (arr[T] literals + indexed get/set + nesting + .length,
        # all arr[T] lowered to one fixed
        # `%struct._FestinaArray = type { i64, ptr }` -- see the module
        # docstring; .length is `extractvalue` on the array's own value,
        # bypassing the pointer-based field-access path entirely since
        # not every array-typed expression is addressable), automatic
        # table schema sync via the festina_runtime
        # C helpers, sqlite() queries (SELECT into a declared arr[Table],
        # parameterized INSERT/UPDATE/DELETE/SELECT via a literal params
        # array -- table-typed values are `ptr`-to-row like structs, field
        # access is a flat `field_index * 8` byte GEP rather than a named
        # struct type; see the module docstring's "Query rows" note and
        # CodeGen.table_fields/table_field_index/_emit_sqlite_call/
        # _emit_sqlite_bind_params/_emit_sqlite_collect), Math.floor/ceil/
        # round/trunc (LLVM intrinsics) and int.toFloat() (sitofp),
        # division/modulo by zero returning a reserved null sentinel
        # (INT_NULL_CONST / FLOAT_NULL_CONST) via real control flow rather
        # than a trapping instruction, and regex()/.test()/.match()/
        # .replace()/.replaceAll() (_emit_regex_call plus the same
        # Member-call dispatch Math.floor/int.toFloat() use -- a regex
        # value is `ptr` to an opaque, POSIX regex_t compiled fresh at
        # every regex() call site via the festina_runtime C helpers; no
        # IR-level machinery of its own, it's all in the runtime), and
        # claude.md #37/#39/#40 (image/graphics/events -- see "Status"
        # above for the design): _emit_graphics_call handles drawRect/
        # drawCircle/drawText/drawImage/loadImage (an `img` value is
        # `ptr` to an opaque Cairo surface, same convention as
        # struct/table/regex values); a bare `clientWidth`/`clientHeight`
        # reference is special-cased at the top of the Identifier branch
        # in _emit_expr (there's no "property access without a call"
        # concept anywhere else in this file, so this couldn't reuse the
        # Call-based builtin dispatch _emit_graphics_call itself uses),
        # emitting a call to festina_client_width()/_height() and
        # setting self.uses_graphics; _emit_event_handler emits an `on
        # click`/`mouse`/`key`/`resize`/`close`/... handler as an
        # ordinary internal function (`@__festina_on_<name>`, never in
        # func_decls/global_env since it's not user-callable) and, only
        # for those five names specifically, records it in
        # self.event_handlers and sets self.uses_graphics; self.uses_graphics
        # (mirroring the pre-existing self.uses_sqlite) gates emitting
        # festina_graphics_init()/festina_run_event_loop() calls around
        # __festina_main() in _emit_main_and_entry, and
        # self.event_handlers drives emitting
        # festina_register_click_handler/_register_mouse_handler/
        # _register_key_handler/_register_resize_handler/
        # _register_close_handler calls there too -- loadImage() alone
        # deliberately does not set uses_graphics (see
        # _emit_graphics_call's docstring). claude.md #69:
        # _emit_timer_call handles setTimeout/setInterval/clearTimeout/
        # clearInterval -- setTimeout/setInterval's callback argument is
        # always an ast.Identifier at this point (semantic.py already
        # checked it structurally), so its LLVM symbol is simply
        # `@<name>` (from _emit_func), passed straight through as the
        # `ptr` argument to festina_set_timeout/_set_interval; sets
        # self.uses_timers, a separate flag from self.uses_graphics
        # (set only by setTimeout/setInterval, not clearTimeout/
        # clearInterval alone) since a timers-only program never opens a
        # window and a graphics-only program never touches the timer
        # machinery -- but self.uses_graphics *or* self.uses_timers both
        # gate the same festina_run_event_loop() call in
        # _emit_main_and_entry. claude.md #38: loadAudio(path) emits
        # `call ptr @festina_load_audio(ptr path)`, returning an
        # AudioType value (`ptr`, same lower-to-`ptr` convention as
        # img/regex/table); play()/stop()/isPlaying() are Member-call
        # dispatch branches (same family as Math.floor/the regex
        # methods) emitting a single call each to
        # festina_audio_play/_stop/_is_playing -- no IR-level machinery
        # of its own, unlike graphics/timers there's no CodeGen flag
        # gating anything for audio, since play()/stop()/isPlaying()
        # need no lazy setup the way opening a window or entering the
        # event loop does. Raises CodegenError (a CompileError
        # subclass, category="not implemented") now only for a genuine
        # compile-time restriction, not a missing feature: when
        # sqlite()'s second argument isn't a literal array expression
        # -- every claude.md construct this compiler targets otherwise
        # generates real code (audio was the last thing that didn't).

    llvm_backend.py
        def available() -> bool           # libLLVM found+loaded in this process?
        def emit_object_file(ir_text, out_path, filename="<ir>") -> None
        class LLVMBackendError(Exception): ...
        # ctypes bindings against libLLVM's C API for ahead-of-time
        # object emission (LLVMTargetMachineEmitToFile), not JIT
        # execution (MCJIT).
        # RelocMode is pinned to PIC to match this system's PIE-by-default
        # linking (verified: LLVMRelocDefault produces relocations `ld`
        # rejects for a PIE). available() is False (never raises) if
        # libLLVM can't be found/loaded or the process architecture isn't
        # one of the target-init symbol names this module knows.

    cli.py
        def compile_file(entry_path, output_path=None, emit_llvm=False,
                          cc="clang") -> str
        # drives imports.build_program (claude.md #5-6: resolves and
        # merges entry_path's whole import graph into one ast.Program;
        # a single-file program is the degenerate case) -> analyze ->
        # generate_ir, then:
        #   llvm_backend.available() -> compile IR to an object file via
        #     llvm_backend directly (stage 3), cc only compiles the
        #     (cached) runtime and links plain object files -- gcc works.
        #   otherwise -> original fallback: hand the .ll file straight to
        #     cc, which must then actually be clang.
        # def main(argv) -> int is the `bin/festina` entry point.
        # _run_tool(cmd) -> subprocess.CompletedProcess: claude.md #59 --
        #   wraps every pkg-config/cc invocation so a genuinely missing
        #   tool raises CompileError("'<tool>' is not installed or not
        #   on PATH -- <install hint>", category="missing dependency")
        #   instead of a raw FileNotFoundError (check=False alone does
        #   NOT catch this case -- it only suppresses a nonzero exit
        #   code, not a failure to launch the binary at all).
        # _data_root() -> str: stage 2 -- resolves runtime/ against
        #   sys._MEIPASS when running as PyInstaller's packaged binary
        #   (this module isn't loaded from a real on-disk .py file at
        #   that point) instead of this file's own on-disk location.
```

Packaging (stage 2, claude.md #59): `packaging/festina_entry.py` is the
PyInstaller entry point (`from festina.cli import main`) --
`scripts/package_compiler.sh` bundles it plus `runtime/festina_runtime.c/h`
(via `--add-data`, so `_data_root()` above can find them post-packaging)
into a single binary at `dist/festina` by default. Not part of
`festina/`'s own public API -- these are repo-level build tooling, same
category as `bin/festina` (which remains the normal dev-from-source
entry point, unchanged).

Runtime ABI: `runtime/festina_runtime.h`/`.c` implement the C side
codegen's `declare`s call into -- `festina_log_*`/`festina_fail` (#41,
#42), `festina_str_*` (string interpolation, #9/#45),
`festina_db_open`/`festina_sync_table` (#8, #28-31: schema
create/add-column/rebuild-with-CAST, using the same declared-vs-existing
diff `sqlite_schema.py` computes, reimplemented in C since the compiled
executable can't depend on Python at runtime), and
`festina_sqlite_prepare`/`_bind_int`/`_bind_float`/`_bind_text`/
`_bind_null`/`_exec`/`_collect_rows` (#32-34: sqlite() queries --
`_collect_rows` packs each result row as `col_count` consecutive 8-byte
slots, exactly the layout codegen's flat `field_index * 8` byte GEP
reads back, so no struct-alignment rule needs to be kept in sync between
the two languages; see festina_runtime.h's doc comment on
`festina_sqlite_collect_rows` and codegen.py's module docstring's "Query
rows" note, which describe the same design from each side), and
`festina_regex_compile`/`_test`/`_match`, `festina_str_replace`,
`festina_regex_replace` (#67/#68: regex, string match/replace -- POSIX
`<regex.h>`, no bundled or external regex engine; see
festina_runtime.h's doc comment for why, and for how "no match" is
represented -- a plain NULL `char*`, since that's already exactly how
Festina represents a null `text` value, no separate sentinel needed
the way int/float require one). `_str_replace`/`_regex_replace` both
guard against a zero-length match looping forever (a pattern like
`x*` can match zero-width at every position) by forcing progress one
byte at a time when that happens -- verified directly in
`tests/test_codegen.py::TestRegex::test_replace_all_zero_width_match_does_not_hang`,
which would time out (not just assert wrong output) if this were ever
broken again. `festina_graphics_init`/`_run`, `festina_draw_rect`/
`_draw_circle`/`_draw_text`/`_draw_image`, `festina_load_image`,
`festina_register_click_handler`/`_register_mouse_handler`/
`_register_key_handler`/`_register_resize_handler`/
`_register_close_handler`, and `festina_client_width`/`_height`
(#37/#39/#40: image/graphics/events) open a real X11 window via Xlib and
render onto it via Cairo's Xlib surface backend -- see
festina_runtime.h's doc comment for the full design (undecorated via
Motif WM hints, an 800x600 starting canvas size that can change after a
resize, solid-black-only fill, PNG-only `loadImage` since that's Cairo's
own built-in decoder, the backing-store-plus-blit-on-Expose strategy a
bare Cairo Xlib surface needs since it has no memory of prior drawing,
and the fixed function-pointer signature per event -- `void
(*)(int64_t, int64_t)` for click/mouse, `void (*)(const char *)` for
key, `void (*)(void)` for resize/close -- that's the whole reason
claude.md #40's five event handlers are each signature-restricted at
the semantic.py level above). Event dispatch itself lives in a helper,
`festina_handle_graphics_event` (one already-read `XEvent` in, `0`/`1`
out signaling whether this was the window-close request) -- factored
out of what used to be `festina_graphics_run`'s own `while(1)` loop body
so `festina_run_event_loop` (below) can drive it either as-is or
interleaved with timer processing: `Expose` -> re-blit, `ButtonPress`/
`MotionNotify` -> call the registered click/mouse handler if any,
`KeyPress` -> call the registered key handler if any, with the pressed
key's text from `XLookupString` for an ordinary printable character or
`XKeysymToString`'s X11 key name otherwise (e.g. "Escape", "Return",
"Left") for anything else, `ConfigureNotify` with a genuine size change
-> recreate the backing store at the new size (clearing the canvas back
to white, same as resizing a browser's `<canvas>` element) and call the
registered resize handler if any, `WM_DELETE_WINDOW` `ClientMessage` ->
call the registered close handler if any and report "stop." Cleanup on
stop is its own helper too, `festina_graphics_teardown`. `festina_graphics_init`
also calls `XSetInputFocus` right after mapping the window -- needed for
`KeyPress` events to reach it at all under a bare Xvfb instance, which
(like the `WM_DELETE_WINDOW`-forwarding gap noted above) runs no window
manager to hand focus over the way a real desktop would; harmless either
way, since a real desktop's WM normally does the same thing itself on
click/map.

`festina_set_timeout`/`_set_interval` (claude.md #69) each add an entry
to a dynamic `FestinaTimer` array (`g_timers`, growing via `realloc`,
compacting out inactive entries first so a program that creates and
clears many one-shot timeouts over time doesn't grow it unboundedly)
and return a monotonically increasing `int64_t` id;
`festina_clear_timeout`/`_clear_interval` are both simply "deactivate
this id if it exists" (interchangeable, like in JS -- neither throws on
an unknown or already-fired id). `festina_fire_expired_timers` walks
that array by index (never caching a `FestinaTimer*` across a
`callback()` call, since a callback is ordinary Festina code and can
itself grow/reallocate `g_timers` via another `setTimeout`/`setInterval`,
or deactivate an entry -- including its own -- via `clearTimeout`/
`clearInterval`) firing anything whose deadline (`CLOCK_MONOTONIC`,
via `clock_gettime`) has passed, rescheduling an interval from *now*
rather than its missed deadline (avoiding a burst of catch-up calls
after a stall). `festina_run_event_loop` (renamed from
`festina_graphics_run`, which is what it was called back when graphics
was the only thing that could ever block there) is what `main()` blocks
in whenever `CodeGen.uses_graphics` or `CodeGen.uses_timers` -- with a
window open, it multiplexes X11 events and timer deadlines on one
`select()` call (`ConnectionNumber(g_display)`, with a timeout computed
from the earliest pending timer or none at all) so both stay responsive
together, exiting (and tearing down the window) on close, timers or
not; without a window, it sleeps until the next deadline (`nanosleep`)
and fires it, for as long as there's still an active timer to wait for,
returning once there genuinely isn't one left -- matching Node's "exits
once the event loop is empty" behavior, including that an uncleared
`setInterval` keeps a graphics-free program running forever, exactly
like a real JS runtime, until it's stopped externally or via
`clearInterval()`.

`festina_load_audio`, `festina_audio_play`/`_stop`/`_is_playing`
(#38: aud, loadAudio(), .play()/.stop()/.isPlaying()) play a clip
through a real ALSA output device. `festina_load_audio` parses a WAV
file's RIFF/`fmt `/`data` chunks directly (no decoder dependency at
all -- claude.md's own example names a `.mp3`, but WAV is the
implementation-defined choice here, the same kind of call PNG-only
images already made for a different reason), rejecting anything that
isn't 16-bit PCM with a clear error, and returns an opaque
`FestinaAudio*` (decoded samples plus playback state, behind a mutex).
`festina_audio_play` opens and configures the ALSA "default" device
*synchronously*, right there in the call itself (a missing or unusable
device fails loudly and immediately via `festina_fail()`, the same as
"could not open the X display" for graphics) before spawning a
background pthread that streams the PCM in small chunks, checking a
stop flag between each one so `festina_audio_stop` gets a prompt
response rather than waiting out the whole clip; the "is this clip
playing" flag is set *before* that thread is even spawned, so
`festina_audio_is_playing` is guaranteed true immediately after
`festina_audio_play` returns, not just "usually true by then."
`festina_audio_stop` signals the thread and *joins* it before
returning, so `festina_audio_is_playing` is equally guaranteed false
the instant `festina_audio_stop` returns; calling it when nothing is
playing is a safe no-op. Calling `festina_audio_play` again while
already playing restarts from the beginning, stopping the previous
thread first. A clip that reaches its own natural end also clears the
flag, but its thread is never explicitly joined by anything in that
case -- an accepted, documented tradeoff for a typically short-lived
compiled program (the OS reclaims everything at process exit
regardless), not machinery this runtime bothers building. See
festina_runtime.h's doc comment on `festina_load_audio` for the full
design, verified against a real (virtual) ALSA device via
`$HOME/.asoundrc`'s null plugin, not just reasoned about -- see
`tests/test_codegen.py::TestAudio`.

## Running

```
pip install -r requirements-dev.txt   # pytest
pytest tests/                          # 422 passed, 8 skipped (needs a C compiler; 2 of
                                        # the skips need `pip install pyinstaller` too,
                                        # the other 6 need Xvfb + xdotool installed)
```
