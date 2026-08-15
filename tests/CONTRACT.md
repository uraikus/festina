# Test contract for the Festina language spec (`claude.md`)

## Status

The `festina/` package at the repository root implements the front end
of the spec in `claude.md` (lexing, parsing, type resolution, semantic
analysis) **and** now a real LLVM codegen backend + native C runtime:
`bin/festina compile program.f -o program` produces a standalone executable that
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
#67/#68 (regex, string match/replace) -- `.test()`, `.match()`, and
`.replace()`/`.replaceAll()` are recognized Call-on-Member patterns the
same way `Math.floor`/`int.toFloat()` already are, and the whole feature
is built on POSIX extended regular expressions (`<regex.h>`, already
part of libc) rather than a bundled or external regex engine, per
claude.md #59. A regex value was originally constructible only via a
`regex()` builtin function call (like `sqlite()`/`loadImage()`), with no
dedicated literal syntax at all -- see this section's later "JS-style
regex literal syntax" paragraph below for why and how that changed.
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

Binary slimming (also claude.md #59: "if a canvas isn't used, keep the
binary slim") split the single `runtime/festina_runtime.c` into three
translation units -- `festina_runtime.c` (core: log/fail/string
interpolation/sqlite/regex/timers), `festina_runtime_graphics.c` (Cairo/
X11), and `festina_runtime_audio.c` (ALSA), sharing declarations through
`festina_runtime.h` (public) and a new `festina_runtime_internal.h`
(timer bookkeeping shared between core and graphics only). Before the
split, a compiled program that never used graphics or audio still linked
against `libcairo`/`libX11`/`libasound` -- confirmed directly: a trivial
`log('hello')` program pulled in 24 shared libraries and Xlib/Cairo/ALSA
transitively at ~1.5MB. The obvious-looking fix
(`-ffunction-sections -fdata-sections` at compile time,
`-Wl,--gc-sections -Wl,--as-needed` at link time) was tried first and
disproven empirically: it shrank the binary correctly (dead code really
was eliminated -- `readelf --dyn-syms` showed zero remaining undefined
references to any Cairo/X11/ALSA symbol) but `readelf -d`/`ldd` still
showed all three libraries as `NEEDED` regardless, because `--as-needed`
decides whether a library is needed against the *whole translation unit*
any live symbol pulls in, before `--gc-sections` has pruned anything out
of it -- with everything in one `.o`, that whole-file decision always
came out "needed." Splitting into separate object files sidesteps the
problem entirely: `festina/cli.py`'s `_runtime_objects_and_link_libs`
now only ever passes the graphics/audio object files (and their
pkg-config cflags/libs) to `cc` when `CodeGen.uses_graphics_code`/
`uses_audio` say the program actually calls something from them, so an
unused feature's library is never on the link line to begin with, not
merely dead-code-eliminated from it. `CodeGen.uses_graphics_code` is
deliberately a separate, broader flag than the pre-existing
`uses_graphics` (which still only gates lazily opening a canvas window):
`loadImage()` alone doesn't open a window (see its own doc comment above)
but `festina_load_image()` still lives in the graphics object file, so
linking needs the broader signal even though window-opening doesn't. The
previously-unified `festina_run_event_loop` (X11 `select()`-multiplexed
timers+graphics) is now graphics-only, calling back into a new
`festina_next_timer_deadline()`/`festina_fire_expired_timers()` seam
(`festina_runtime_internal.h`) for timer state that still lives entirely
in core; a timers-only program (no graphics) now calls a new
`festina_run_timer_loop()` instead, staying pure-POSIX with no X11
dependency at all. Verified end to end via `ldd`/`readelf -d` on real
compiled binaries for all four combinations (neither/graphics-only/
audio-only/both) -- see `tests/test_codegen.py::TestSlimBinaries`.

"Real compilation, minimal setup" stages 1, 2, and 3 -- claude.md #59,
added alongside these stages to make the requirement explicit rather
than implicit in the implementation -- are also done (see setup.md for
the full staged plan and the current dependency list): sqlite3 is
statically linked into compiled programs
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
All 761 tests in this directory pass against it: 522 need no external
tool at all (parser/semantic/IR-level tests, no compile-and-run step),
229 more need a working C compiler, plus 2 more
(`tests/test_packaging.py`) given `pyinstaller` too, plus 8 more given
`Xvfb`+`xdotool` too (522 + 229 + 2 + 8 = 761 -- re-verified directly
by hiding each tool from PATH in turn, not just derived by counting
`compile_and_run` call sites, since the true number had drifted well
past a much older, since-inaccurate count of "694 given a working C
compiler" left over from before this suite's `compile_and_run`-based
end-to-end tests grew to their current share of it)
(`tests/test_codegen.py::TestGraphics`'s interactive click/mouse/key/
resize tests, the one confirming the initial `clientWidth`/
`clientHeight` values, `TestTimers`'s combined graphics-and-timers test,
and `TestExampleGraphicsAndGame`'s two example-driven tests below) --
all skip cleanly, independently, without any of those three (see
below). `tests/test_audio.py`/`TestAudio` need none of the three
either -- the null-device technique they use (see conftest.py's
`audio_null_env`) needs no extra tool install, only the C compiler
`compile_and_run` already requires.

`examples/` grew beyond the original hello/basic/arrays/geometry/
multifile/regex set: `timers.f` (setTimeout/setInterval), `graphics.f`
(drawing + all five event handlers), `audio.f` (loadAudio/play/stop/
isPlaying, with a small generated `beep.wav` fixture), `fizzbuzz.f` (a
dependency-free loops/modulo tour), and `tic_tac_toe.f` -- a real,
playable two-player game (click a cell, alternating X/O, win detection
across all eight lines) built entirely around this runtime's actual
drawing model: every draw call paints in solid black and there's no
"clear"/erase function (see festina_runtime.h's Graphics doc comment),
so the game deliberately never needs to undraw anything, marks just
accumulate the way a real pen-and-paper game would. Verified against a
real (virtual) X server, including the win-detection path (three clicks
completing a line), not just reasoned about. `maps.f` (map[T] literals/
indexing/`.forEach()`) and `config.f` (`DatabaseURL`/`environment.NAME`,
the latter's own doc comment explaining why it has to be the file's
very first line) rounded the set out further. `tests/test_examples.py`
compiles every file in `examples/` and checks the deterministic ones'
exact stdout; `graphics.f`/`tic_tac_toe.f` (the two needing a display)
get their own interactive coverage in
`tests/test_codegen.py::TestExampleGraphicsAndGame` instead, next to
`TestGraphics`'s own Xvfb helpers.

JS-style regex literal syntax (`claude.md #67`, requested directly, not
found by an audit): `/pattern/flags` is now a real grammar construct
(`ast.RegexLit`) alongside the pre-existing `regex(pattern, flags)`
function -- mirroring JS's own split between a `/pattern/` literal and
`new RegExp(...)`, since a literal's pattern/flags are fixed at compile
time (no interpolation, unlike a template string) while `regex()`
remains the only way to build a pattern that isn't known until runtime.
claude.md #67 used to explicitly rule this out ("No regex literal syntax
... is used") specifically because of the classic JS lexical ambiguity
between a leading `/` starting a regex literal and `/` as the division
operator -- resolved here the same way real JS lexers resolve it, not
by inventing a different rule: `festina/lexer.py`'s
`_regex_literal_may_start_here` treats a `/` as trying to open a regex
literal everywhere *except* immediately after a token that could itself
end an expression (an identifier, a literal, `)`/`]`, postfix `++`/`--`)
-- a denylist, deliberately permissive by default, checked against a
comment always winning (`//`/`/*` never even attempt a regex-literal
parse) and an unterminated attempt (no closing `/` before a newline)
falling back to plain division rather than raising. Flags are validated
for real at parse time (`Parser.parse_primary`'s `REGEX` handling) --
only `i` (case-insensitive, matching `regex()`'s own flag) and `g`
(accepted for familiarity, but a deliberate no-op: `.replace()`/
`.replaceAll()` already say first-vs-every-match explicitly, the same
distinction JS's `g` flag controls implicitly) are accepted; any other
letter, or a repeated flag, is a clear compile error -- something
`regex()`'s flags *argument* can never offer, since it's an arbitrary
runtime `text` expression the compiler can't inspect. Escaping: `\/`
inside a literal unescapes to a literal `/` (JS's own delimiter-escape
convention, meaningless to POSIX `regcomp()`, which never requires `/`
escaped at all); every other backslash sequence (`\w`, `\d`, `\s`, `\.`,
`\\`, ...) passes through untouched to `regcomp()` -- verified directly
that glibc's `regcomp()` accepts `\w`/`\d`/`\s`/`\b` etc. as GNU
extensions even in `REG_EXTENDED` mode, so the familiar JS shorthand
classes work in practice, not just POSIX ERE's own narrower official
escape set. Verified end to end (lexer disambiguation matrix, parser
flag validation, and real compiled programs) -- see
`tests/test_lexer.py::TestRegexLiterals`,
`tests/test_regex.py::TestRegexLiteral`, and
`tests/test_codegen.py::TestRegexLiteral`.

Three more directly-requested features, all new claude.md territory
(#70-72), landed together:

- **`DatabaseURL = <expr>`** (claude.md #70) overrides `festina.sqlite`'s
  default location, but only when it's the entry file's own literal
  first statement, before any other code or `import` -- enforced in
  `festina.imports.build_program` (`_extract_database_url`), not
  semantic.py, since it's fundamentally about *that file's own
  statement order before multi-file merging*: by the time semantic.py
  sees the merged Program, the entry file's statements are no longer
  contiguous or first (dependencies come before the entry file in
  `resolve_imports`'s own order). The extracted value expression is
  threaded through as `ast.Program.database_url` (`None` if absent) and
  evaluated by codegen.py's `_emit_main_and_entry` directly in `main()`'s
  prologue, before `festina_db_open()` -- never as an ordinary top-level
  statement, which would run far too late (inside `__festina_main()`,
  after the database is already open). `festina_db_open` itself grew a
  `path` parameter (was `void`), falling back to `"festina.sqlite"` for
  a NULL/empty one so a DatabaseURL expression that evaluates to unset
  environment data doesn't crash. Written entirely with syntax the
  parser already supported (`DatabaseURL = expr` is an ordinary
  assignment-expression-statement -- there's no dedicated grammar for
  it at all), recognized purely by matching that exact AST shape. See
  `tests/test_database_url.py` (extraction/position, real files on
  disk) and `tests/test_codegen.py::TestDatabaseURL` (the actual
  database file used, including from `environment.DATABASE_URL`).
- **`environment.NAME` / `environment[keyExpr]`** (claude.md #71) wraps
  `getenv()`, returning `text` or `null` (already exactly `getenv`'s own
  NULL-if-unset return, no translation needed). Unlike
  `clientWidth`/`clientHeight`, `environment` is never a valid bare
  value (only `.NAME`/`[keyExpr]` mean anything, and NAME is arbitrary),
  so it's pre-registered into `global_scope` purely for
  `Scope.define`'s collision protection (a user can't redeclare it) --
  actual dispatch (`semantic.py`'s `_infer_member`, `codegen.py`'s
  `_emit_expr`) special-cases the AST shape (an `Identifier` literally
  named `"environment"`) directly, before ever treating it as a real
  variable reference, precisely so `environment.NAME` still resolves
  correctly even though a bare `environment` reference is deliberately
  rejected with a specific error. Read-only, same placement/reasoning as
  `.length`/`clientWidth`'s own read-only checks. See
  `tests/test_environment.py` and
  `tests/test_codegen.py::TestEnvironment`.
- **`map[T]`** (claude.md #72) -- `{ key: value, ... }` literals
  (`ast.MapLit`), `npcHealths[key]` read/write (reusing the existing
  computed-`Member` grammar array indexing already has, dispatched on
  the receiver's type), and `.forEach(callback)`. Keys are always text
  -- an unquoted identifier key is a reference to that variable, not
  bareword-as-string-name shorthand the way a plain JS object literal
  has (semantic.py checks every key's type explicitly, not just the
  last one the way `ArrayLit`'s value-type inference currently does --
  a key genuinely needs checking every time, since nothing else ever
  constrains it the way a declared `map[T]`'s value slot constrains
  values). A missing key reads back as `null` -- `festina_map_get`
  returns a `default_value` argument outright when the key isn't found,
  computed by codegen per the map's *value* type (int/float/pointer
  each have their own null encoding -- see the "Null for int/float"
  note below -- the runtime function itself has no idea what T is, only
  ever seeing raw i64 payloads).

  Runtime representation: a map value is `{ i64 count, ptr entries }`
  at the LLVM level (`FESTINA_MAP_LLVM_TYPE`) -- the *identical* shape
  to `arr[T]`'s own `{ i64 length, ptr data }`, but kept as a distinct
  named type rather than reusing `_FestinaArray` outright, so an
  accidental mix-up is caught in the IR itself. `entries` points to a
  flat, linearly-scanned array of `FestinaMapEntry { key, value }`
  pairs (`runtime/festina_runtime.c`) -- not a hash table, a deliberate,
  documented tradeoff (`festina_map_find`'s own comment): maps are
  meant for small, game/config-shaped key sets (claude.md #72's own
  worked example is a handful of NPC health/name entries), and this
  runtime already favors simple, obviously-correct implementations over
  algorithmic sophistication elsewhere too (`arr[T]` has no hashing or
  ordering either). `map[T]`'s `T` may be anything `arr[T]`'s own
  element type can be *except* `arr[...]`/`map[...]` itself -- a 16-byte
  array value (or another map value) doesn't fit in the one 8-byte slot
  every map value is stored in, the same slot size
  `festina_sqlite_collect_rows`'s row layout already uses. Every value
  type's payload -- int, float, bool, text, blob, struct, table, img,
  aud, regex -- travels through `festina_map_set`/`_get`/`_for_each` as
  a raw `i64` regardless of T (this runtime has no idea what T a given
  map's values are), reinterpreted to/from each value's real LLVM
  representation at every call site (`codegen.py`'s
  `_map_value_to_i64`/`_i64_to_map_value`) -- including inside a small,
  per-`.forEach()`-call synthesized trampoline function
  (`_emit_map_foreach_trampoline`), needed because the user's callback's
  own LLVM signature depends on T (e.g. `double` for a `map[float]`) and
  can't be called through an `i64`-typed function pointer directly
  without a genuine calling-convention mismatch on real ABIs -- verified
  for all four reinterpretation shapes (`i64` passthrough, `double`
  bitcast, `i1` zext/trunc, `ptr` inttoptr/ptrtoint, the last covering
  both `text` and a `struct`-valued map) with real compiled programs.
  `festina_map_set` takes `count`/`entries` *by address* (a pointer into
  the map value's own storage slot -- a plain variable's alloca/global,
  a struct field's GEP, or a literal's own scratch header alloca during
  construction), since adding a new key may grow the backing array and
  the caller needs to see that change; assigning into a map whose own
  storage isn't addressable this way (e.g. `someFunc()['key'] = v`,
  where `someFunc()` returns a `map[T]`) is a specific compile-time
  error rather than silently doing nothing or corrupting memory. Grows
  by exactly one entry per insert (no capacity doubling, no separate
  capacity field tracked) -- simpler to reason about and, given maps
  are expected to stay small, not a real performance cost in practice.

  Also added alongside this (not itself claude.md #72, but discovered
  to be needed for a natural `.forEach()` callback body, per the
  request that motivated it): `int`/`float`/`bool.toText()`, an
  explicit spelling of the exact same stringification template
  interpolation (`_to_text`) already does implicitly for these three
  types -- `log(x.toText())` and `` log(`${x}`) `` always produce
  identical output.

  See `tests/test_maps.py` (parser/semantic),
  `tests/test_codegen.py::TestMaps` (real compile-and-run, including
  every value-type reinterpretation case and the struct-field/
  non-addressable-assignment edge cases), and
  `tests/test_numeric_conversion.py::TestToText` /
  `tests/test_codegen.py::TestNumericConversion`'s `toText` tests.

A follow-up sanity-check pass over the claude.md #70-72 work above found
one more genuine, verifiable bug plus three smaller improvements, all in
the same "catch it at compile time / don't do wasted runtime work"
spirit as the rest of this file:

- **`arr[T]` literals with mismatched element types reached codegen
  instead of failing cleanly.** `ArrayLit`'s type inference walked every
  element but only ever kept the *last* one's type (see the `map[T]`
  paragraph above, which deliberately does NOT have this weakness for
  keys) -- so `arr[bool] a = [1, 'x', true]` passed semantic analysis
  and failed at LLVM object emission with a raw, confusing "floating
  point constant invalid" / "global variable reference must have
  pointer type"-shaped error instead of a `CompileError`, the exact
  class of gap security.md's original audit fixed for comparisons/array
  indices/`const` reassignment/etc. but happened to miss for array
  literals themselves. Fixed by checking every element's type against
  the first concrete (non-`null`) one seen, raising immediately on a
  mismatch -- deliberately *not* changing what the overall inferred type
  ends up being (still the last element's type, since a couple of
  existing `null`-element corner cases, e.g. `arr[int] a = [5, null]`
  failing against a declared `arr[int]`, already depend on that exact
  value and weren't in scope to also fix here). One genuine, spec-
  mandated exception: `sqlite()`'s parameter-list argument (claude.md
  #33's own worked example is `[1, 'Patrick']`, deliberately mixed
  int/text) is NOT a real `arr[T]` value at all -- it's a heterogeneous
  bound-parameter list, one independently-typed value per `?`
  placeholder -- so `_infer_call` infers each of its elements
  individually when the argument is a literal written directly at the
  `sqlite(...)` call site, bypassing the new homogeneity check rather
  than wrongly rejecting the one construct claude.md itself requires to
  mix types in an array literal.
- **`/pattern/flags` regex literals now compile once, not on every
  execution.** The pattern and flags of an `ast.RegexLit` are fixed at
  parse time, so the exact same literal always produces the exact same
  `regcomp()` result -- recompiling it on every reach (the previous
  behavior, and still todo.md's documented gap for the *dynamic*
  `regex(pattern, flags)` builtin, where pattern is a general runtime
  expression and caching by call site would be a correctness bug, not
  an optimization) was pure wasted work for something entirely knowable
  ahead of time. Cached per-AST-node behind a private `ptr` global
  initialized to `null` and the standard lazy-init null-check + `phi`
  shape `_emit_ternary`/`_emit_logical` already use for conditional
  control flow (`_emit_cached_regex_lit`) -- still only compiles on the
  first runtime reach of *that* literal (a literal inside an untaken
  branch, or a function never called, still costs nothing), with every
  later reach just a load. Verified directly against the emitted IR: a
  `/pattern/` literal inside a 5-iteration loop now emits exactly one
  `call ptr @festina_regex_compile` site, guarded by the null check,
  versus one `call` per textually distinct literal in the source, not
  per execution.
- **A map literal with two *literal* string keys that collide is now a
  compile error instead of silent "last value wins."** claude.md #72's
  own "last value wins" rule still holds -- and still has to, since
  there's no way to know at compile time whether two arbitrary text
  *expressions* produce the same string -- but when both keys in a pair
  are plain `ast.StringLit`s (not a variable, template, or other
  expression), the collision is knowable for free, right now, and is
  essentially always a typo rather than something intentional. Same
  "catch it before it runs" instinct as `const` reassignment or a
  `void` function returning a value. Tracked by literal string value
  within one `MapLit`, first occurrence only, so the error points at
  the *second*, redundant entry.
- **`environment`'s reserved-name collision now gets a specific
  message.** Previously `int environment = 5` reported the same generic
  `'environment' is already declared` every ordinary duplicate
  declaration gets -- misleading here, since (unlike a real duplicate)
  there's no earlier `environment` declaration in the program to point
  a user back to. `Scope.define` now special-cases this one reserved
  name the same way `analyze_func`'s builtin-function-name collision
  already gets its own specific, explanatory message.

See `tests/test_maps.py` (the two new `TestMapLiteral` cases),
`tests/test_codegen.py::TestMaps`'s updated
`test_duplicate_key_in_a_literal_last_one_wins` (now exercises the
still-legal runtime-only collision via a variable key, since the
literal-literal case it used to use is the newly-rejected one), and
`tests/test_environment.py::TestEnvironmentIsReserved`'s updated
message assertions.

A follow-up pass revisited todo.md's own "smaller, not yet tracked"
list and closed two of its four items outright, found and fixed one
more bug while doing so, and deliberately did NOT attempt a third
(memory management) despite being asked -- explained below.

- **`break`/`continue` (claude.md #73, new).** claude.md never defined
  either before this, so (per #54's ambiguity rule) they didn't exist --
  `return` from the enclosing function was the only documented way out
  of a loop body early. Added as a genuine spec addition (a new
  section, not just an implementation gap being closed), because the
  semantics are unambiguous and standard for a JS-inspired language --
  `break` exits the nearest enclosing loop immediately; `continue`
  skips to the next iteration, still running a `for` loop's update
  expression first (same as a normal iteration would). Both are a
  compile error outside any loop (`semantic.py` threads a `loop_depth`
  counter through statement analysis, incremented entering a
  while/for's body, reset to 0 -- not inherited -- entering a nested
  function's own body, since a function is its own boundary regardless
  of where it happens to be lexically declared; an `if`/plain `{ }`
  block does NOT reset it, since those aren't loop boundaries).
  Codegen (`CodeGen._loop_targets`, a stack of `(continue_label,
  break_label)` pairs pushed/popped around each loop body's own
  emission in `_emit_while`/`_emit_for`) needed no new control-flow
  machinery beyond what if/while/for already use -- `break`/`continue`
  just emit an unconditional `br` to the nearest loop's targets and set
  `ctx["terminated"] = True`, the exact same "nothing after this in the
  block gets emitted" mechanism `return` already uses. Reaches through
  arbitrarily nested if/block statements to the nearest *loop*
  specifically because `_loop_targets` is a plain instance-level stack,
  not threaded through `ctx` the way `terminated` is. See
  `tests/test_loops.py::TestBreakAndContinue` (parser/semantic) and
  `tests/test_codegen.py::TestBreakAndContinue` (real compile-and-run,
  including claude.md #73's own worked example verbatim, nested loops,
  and dead-code-after-break not being emitted).

- **`bool x = null` (claude.md #10/#25/#57, closing a documented gap).**
  Same root cause as `int x = null`/`float x = null`'s own original fix
  (`null` is only valid LLVM IR for a pointer type -- storing it into a
  non-pointer slot is a link error) but needed one extra step those
  didn't: i64/double have plenty of spare bit patterns to reserve as a
  sentinel, but bool's natural representation, i1, has exactly two
  possible values, both already meaningful (0=false, 1=true) -- no room
  for a third. Fixed by widening `_llvm_type(BOOL)` from `i1` to `i8`
  (`BOOL_NULL_CONST = 2`), while keeping every place LLVM itself
  requires a genuine i1 (branch conditions, `icmp`/`fcmp`/`xor`) working
  through a new `_bool_cond` helper (`icmp ne i8 ..., 0`) and immediate
  `zext`s at the point a comparison/logical-op's i1 result becomes a
  "BOOL value" the rest of codegen deals with -- `_emit_if`/`_emit_while`/
  `_emit_for`/`_emit_ternary`'s conditions, `_emit_logical`'s short-
  circuit test (its phi itself stays over the actual i8 operand values,
  same JS-style "returns whichever operand won" semantics as before,
  just at the new width), and `_emit_unary`'s `!` (narrow, xor, widen
  back). An already-null bool used as a condition, or as an operand to
  `!`/`&&`/`||`, is unresolved -- `_bool_cond` treats it as truthy
  (nonzero), a documented, consistent choice, not a claim it's
  meaningful, same allowance claude.md #57 already gives int/float's own
  null sentinels in further arithmetic. This widening is uniform
  everywhere a bool value is stored or passed (variables, struct/table
  fields, array/map elements, function params/returns, sqlite binding,
  `map[bool]`'s missing-key default) since it all flows through the one
  `_llvm_type` source of truth -- verified directly for every one of
  those, not just plain variables. Bonus find while doing this: the five
  runtime functions that take/return a Festina bool
  (`festina_log_bool`, `festina_str_from_bool`, `festina_str_eq`,
  `festina_regex_test`, `festina_audio_is_playing`) already declared
  their C-side signature as `int8_t`, not `_Bool`/`int` -- their LLVM
  `declare`s said `i1` anyway, a latent (if mostly harmless, since only
  0/1 were ever produced) ABI mismatch predating this fix entirely;
  moving those five declares to `i8` alongside the null work corrects
  that too.

- **`x == null` for a concretely-typed int/float/bool `x` (bug, found
  while testing the bool-null fix above, not itself new scope).**
  `_emit_binop` used to emit both operands via plain `_emit_expr`,
  which -- for a bare `null` literal on either side -- always produces
  the generic LLVM `null` keyword (correct for text/struct/array, the
  only context `_emit_expr`'s own `NullLit` branch has to work with, no
  declared-type context available). For an int/float/bool operand this
  produced literally invalid IR (`icmp eq i64 %x, null` -- verified as a
  pre-existing bug, independent of bool-null entirely: `int a = 1 / 0
  \n log(a == null)` already failed identically before any of this
  session's bool work). Fixed by having `_emit_binop` route a `NullLit`
  operand through `_emit_value_for` with the *other* (already-emitted)
  operand's type as context -- the same "does this position have a
  declared type to pick a null encoding from" pattern `_emit_value_for`
  already exists for (var decls, params, returns), a binary comparison
  is just one more such position. Deliberately does NOT handle `null ==
  null` (neither side has any type context to infer a representation
  from) -- confirmed that already failed identically before this fix
  too, an exceedingly rare, not obviously meaningful expression to
  write in the first place (claude.md #54's ambiguity rule, same as
  this file's other deliberately-left corner cases, e.g. `arr[int] a =
  [5, null]`'s trailing-null quirk). One more wrinkle specific to float:
  comparing a null float against `null` is `false`, not `true` --
  `FLOAT_NULL_CONST` is a real quiet NaN, and IEEE-754's *ordered*
  compares (`oeq`/`one`, what claude.md's `==`/`!=` already lower to)
  are false for any comparison involving NaN, including a NaN against
  itself. Not something this fix changes or could reasonably fix as a
  side effect -- float null-checking via `==` structurally can't be as
  reliable as int/bool's without changing what `==`/`!=` mean for every
  float comparison, a separate, unrelated design decision -- documented
  directly in the regression test rather than quietly asserted around.

  See `tests/test_codegen.py::TestNumericConversion`'s new
  `test_null_bool_assignment` and its neighbors (function-call round
  trip, struct field, ordinary non-null bool logic unaffected by the
  width change) and the four new `test_comparing_a_null_*_against_the_
  null_literal`/`test_comparing_a_non_null_*` cases.

- **Memory management (claude.md #43), stage 1: automatic reclamation
  of provably non-escaping locals (claude.md #74, new).** A dedicated
  `claude.md` addition preceded this (the project's own established
  pattern, followed deliberately here -- an earlier pass declined to
  implement any of this without one, given the risk of getting it
  wrong: unlike a type error or a missing null representation, a wrong
  answer here doesn't fail loudly, it silently corrupts memory or
  frees something still reachable at some later, disconnected point in
  the program). A local struct/`arr[T]`/`map[T]` declared directly in a
  function or event handler's own top-level body (not yet nested inside
  an `if`/`while`/`for` -- see the stated stage-1 limitations below) is
  now freed automatically at every `return` the function/handler
  reaches, when `festina/escape_analysis.py`'s `find_escaping_names` can
  prove -- from the syntax of that function/handler alone -- that the
  variable's name never appears anywhere except as the immediate `.obj`
  of a `Member` access (`v.field`, `v.field = x`, `v[i]`, `v[i] = x`,
  `v.someMethod(...)`). That one rule turns out to correctly exclude
  every way a value's address can escape in Festina today: a bare
  `return v`, a call argument (`foo(v)`), the value or target of a
  plain assignment (`x = v`, `v = other` -- the latter matters because
  `v`'s *old* value may still be aliased elsewhere, and freeing
  whatever `v` holds at the end of the function would free the wrong
  thing), an array/map literal element, an operand of any operator --
  in every one of those, the bare `Identifier` node sits somewhere
  other than immediately under a `Member.obj`. The rule is deliberately
  name-based, not a real scope-resolving analysis: an inner block's own,
  unrelated local shadowing the outer candidate's name can only ever
  make the candidate look *more* escaping than it really is (a missed
  optimization opportunity), never less (never an unsafe free) -- see
  `escape_analysis.py`'s own module docstring for the full reasoning,
  including why it raises on any expression node type it doesn't
  recognize rather than silently treating an unrecognized future AST
  addition as non-escaping.

  Wiring this into codegen needed one genuinely new piece of state
  beyond the escaping-name set itself: `CodeGen._active_free_locals`, a
  stack of "frames" (one per currently-being-emitted function/handler
  body, mirroring `_loop_targets`' own "instance-level stack, not
  threaded through `ctx`" shape for the same reason -- it has to keep
  working through arbitrary `if`/block nesting inside the tracked body)
  where each frame accumulates `(storage ref, Type)` for every
  non-escaping candidate *as its own declaration is actually reached* in
  program order (`_emit_func_body`, replacing the plain `_emit_block`
  call `_emit_func`/`_emit_event_handler` used to make for their own
  top-level body only -- nested `if`/`while`/`for` bodies still go
  through the unmodified `_emit_block`). This is what makes an early
  return -- nested inside an `if` that appears *before* a later
  candidate's declaration in the same function -- correctly never try to
  free something that was never allocated on that path: the candidate
  is only added to the active frame once the top-level walk actually
  finishes emitting its own `VarDecl`, so a `Return` reached earlier
  simply never sees it. Verified directly, not just reasoned about
  (`test_early_return_before_the_declaration_has_no_free_on_that_path`
  inspects the generated IR to confirm the first `ret` has no `free()`
  before it and the second does). `_emit_free_active_locals` (called
  from `_emit_stmt`'s `Return` handling, after the return value if any
  has already been computed -- so anything it reads through one of
  those locals' own fields, itself a safe use, still sees live memory --
  and once more from `_emit_func_body` itself for a function/handler
  that falls off its own end) frees each active candidate according to
  its actual representation: a struct local is `alloca ptr` pointing at
  its own calloc'd backing storage, so freeing it means loading that
  pointer and freeing it directly; an `arr[T]`/`map[T]` local is the
  `{i64, ptr}` header itself, stack-allocated inline (never separately
  heap-allocated on its own -- see `FESTINA_ARRAY_LLVM_TYPE`'s own
  module docstring note), so freeing one means loading the header value,
  `extractvalue`-ing its second field (the data/entries pointer), and
  freeing *that*, not the local itself.

  Verified three separate ways, deliberately more rigorously than most
  fixes in this file given the stakes: (1) `tests/test_escape_analysis.py`
  (36 tests) exercises `find_escaping_names` directly and exhaustively
  -- every safe pattern (field/element read and write, a deep member
  chain, use only inside a nested `if`/`while`/`for`, a method call on
  the variable itself) and every escaping pattern (return, call
  argument at any nesting depth, assignment value/target, array/map
  literal element, ternary/logical/binary/unary/postfix operand, a
  computed index expression) -- entirely at the parser level, no C
  compiler needed. (2) `tests/test_codegen.py::TestAutomaticMemoryReclamation`
  (19 tests) checks both the generated IR directly (a `free()` call
  lands exactly where and only where expected, including the early-
  return-ordering case above, and an event handler's own locals are
  covered too) and real compiled-and-run programs, including the exact
  "return a struct by value" pattern that broke the earlier naive
  stack-allocation attempt this project already tried once and reverted
  (see `festina/codegen.py`'s module docstring) -- every escaping case
  (returned, passed as a call argument, assigned into a global,
  reassigned) is confirmed to still produce the *correct value*
  afterward, not just "no crash." (3) A real AddressSanitizer/
  LeakSanitizer run (`gcc -fsanitize=address`, since this environment's
  clang lacks the static ASan runtime archives but gcc's dynamic one
  works) against a combined program exercising every escaping/non-
  escaping pattern together across 1000+ calls: zero ASan errors (no
  use-after-free, no double-free, no invalid free), and running the same
  binary with leak detection enabled found *exactly* the expected
  leaks and nothing more -- 1000 objects from the one still-escaping
  (call-argument) pattern, plus two small, separately-explained leaks
  from `festina_map_set`'s own per-entry `strdup`'d keys (not something
  freeing a map's `entries` buffer touches -- a genuine, distinct,
  now-documented stage-1 limitation of its own, found via this exact
  ASan run, not anticipated in advance). Global variables holding
  otherwise-unfreed struct pointers (`globalStash`, top-level `Point
  q1/q2` receiving a `makePoint()` return) were correctly NOT reported
  as leaks by LeakSanitizer, since they remain reachable at program
  exit -- exactly the expected, standard leak-detector distinction
  between "never explicitly freed" and "genuinely unreachable."

  (At the time of the paragraph above, before the nested-block/loop
  widening described next, this stage still did NOT cover a value
  declared inside a nested `if`/`while`/`for` block or a loop body at
  all -- both closed by the follow-up increment below, in the same
  session.) Still explicitly NOT covered (claude.md #74 itself states
  each of these, so a later stage's own test suite has a clear
  before/after line): whether a value passed as a call argument is
  actually retained by the callee (treated as escaping unconditionally
  -- no interprocedural analysis yet); struct/array/map *fields* within
  an otherwise-freed struct; and a freed map's own per-entry keys. None
  of these are safety gaps -- each one only means less memory is
  reclaimed automatically than a more complete implementation would
  reclaim.

- **Memory management stage 1, widened: nested `if`/`while`/`for`
  bodies and per-iteration loop freeing (claude.md #74, extended in the
  same session).** The immediate follow-up to the paragraph above,
  closing exactly the two limitations it called out. The meaningful
  half of this isn't "nested blocks are now covered" in the abstract --
  it's that a value declared inside a `for`/`while` loop's body is now
  freed at the end of *every iteration that reaches the end of that
  body*, not deferred until the loop finishes or the enclosing function
  eventually returns. Without this, a loop-local struct/array/map would
  leak once per iteration, unboundedly, regardless of how tightly the
  function-level version of this same idea was scoped -- exactly the
  highest-value gap flagged when stage 1 first shipped.

  `escape_analysis.py` itself needed zero changes: `find_escaping_names`
  was always whole-function-scoped (it already walked into nested `if`/
  `while`/`for` bodies looking for escaping *uses*, regardless of where
  a candidate happens to be *declared*), so the exact same analysis
  result already answered "is this name safe" correctly no matter which
  block declares it. Everything new is in `codegen.py`: `_emit_block`
  (previously only used for a nested body, with `_emit_func_body` as a
  separate top-level-only variant) now IS the one mechanism used
  everywhere -- a function/handler's own top-level body included, via
  the new small wrapper `_emit_analyzed_func_body` that computes
  `find_escaping_names` once and exposes it through `self.
  _current_escaping_names` for every `_emit_block` call reached while
  that one function/handler is being emitted. `_emit_block` pushes its
  own frame onto `self._active_free_locals` (now genuinely a stack of
  per-block frames, not one frame per function) and frees just that
  frame at its own natural, non-terminated exit.

  The real new mechanism is `_emit_free_active_locals`'s `down_to`
  parameter, and what calls it with which value: a `Return` frees
  `down_to=0` -- every open frame at once, since returning exits every
  nested scope simultaneously (a value declared at the top level *and*
  one declared in a currently-open `if` both need freeing on the same
  `ret`). A `Break`/`Continue` frees only down to the frame index
  recorded in `self._loop_targets` (extended from `(continue_label,
  break_label)` pairs to `(continue_label, break_label, free_depth)`
  triples -- `free_depth` is `len(self._active_free_locals)` recorded
  right before `_emit_while`/`_emit_for` push the loop body's own
  frame) -- freeing the loop body's own frame and anything nested
  deeper inside it (e.g. an `if` inside the loop containing the actual
  `break`), but never anything below that index: a struct declared
  *outside* the loop and merely read/written through its own fields
  *inside* it (always safe under claude.md #74's own rule, regardless
  of where the struct itself was declared) is correctly left alone by
  that loop's own `break`/`continue`. Verified directly, not just
  reasoned about
  (`test_break_frees_the_loop_local_but_not_an_outer_scope_local`,
  `test_outer_scope_struct_survives_break_and_continue_from_a_nested_loop`).
  No double-free risk between a `Break`'s explicit free and
  `_emit_block`'s own trailing natural-exit free either: the latter is
  gated on `not ctx["terminated"]`, and `Break`/`Continue`/`Return` all
  set `ctx["terminated"] = True` on their own path before returning, so
  whichever one actually fires on a given execution is the only one
  that ever runs -- different control-flow paths through the generated
  IR, never both on the same path.

  Verified the same three ways stage 1's own initial shipment was,
  including a fresh AddressSanitizer/LeakSanitizer pass specifically
  because the new machinery (multi-frame stack, `break`/`continue`
  interaction) genuinely warranted re-verification, not just reusing
  the earlier confidence: a combined program mixing loop-local structs,
  `if`-nested-inside-loop locals, and both `break` and `continue` firing
  repeatedly across many outer iterations -- zero ASAN errors, and (with
  leak detection on) a genuine 499-object leak was found and traced
  precisely to a *different*, already-understood, pre-existing leak
  class entirely unrelated to this increment: a global reassigned
  inside a loop (`globalStash = q`, `q` correctly treated as escaping
  and never freed by this feature either way) orphans its *previous*
  value on every reassignment but the last -- confirmed by removing
  that one line and re-running to exactly zero leaks, zero errors, not
  waved away as "probably fine."

  See `tests/test_codegen.py::TestAutomaticMemoryReclamation`'s
  `test_a_loop_local_struct_is_freed_inside_the_loop_body` (exactly one
  `free()` call, and it's inside the loop body's own block, not just
  once after the loop as a whole),
  `test_break_frees_the_loop_local_but_not_an_outer_scope_local`,
  `test_continue_frees_locals_declared_since_the_loop_body_began`,
  `test_nested_if_inside_a_loop_frees_correctly_on_both_the_break_and_fallthrough_paths`,
  and the matching compile-and-run correctness tests immediately after
  them in the same class (including
  `test_many_loop_iterations_with_nested_if_and_break_continue_does_not_crash`,
  a heavier robustness check than stage 1's own equivalent, specifically
  exercising the new per-iteration free + nested-if + break/continue
  combination together, not just repeated function calls).

  See `todo.md`'s "Memory management" section for the full picture,
  including exactly what's still ahead (interprocedural analysis,
  nested struct/array/map fields, map per-entry keys) and what
  reference counting for genuinely-escaping values would still require.

- **Memory management stage 1, follow-up robustness pass (same
  session, no new codegen.py behavior).** After the nested-block
  extension above shipped, a dedicated pass specifically hunted for
  combinations its own tests didn't already spell out: `break` inside a
  loop nested inside another loop (must free only the inner loop's own
  frame, never reach down into the outer loop's -- free_depth is
  captured fresh by each `_emit_for`/`_emit_while` call, so this was
  already correct by construction, now pinned down by a test); `return`
  from a nested `if` inside a loop, both *after* the loop-local's own
  declaration (must free it, since returning exits the loop and
  function together) and *before* it (must not, since that VarDecl's
  frame entry is only appended once program-order actually reaches it);
  an `else if` chain (each arm is its own nested `IfStmt`, `parser.py`'s
  `parse_if`, so each arm's own struct local needed confirming it's
  freed independently of its siblings); a bare, standalone `{ }` block
  (routed through the same `_emit_block` as everything else, per its
  own docstring, but never previously exercised on its own); and two
  sibling blocks (an `if`'s then/else arms, or two bare blocks in a
  row) declaring a local with the *same name* -- each is its own `Env`/
  frame with its own uniquely-suffixed storage ref, so this was already
  safe, now confirmed rather than assumed. All nine new tests found
  zero bugs -- every case was already correct by the original design
  (frame-per-block, `down_to`-scoped freeing, per-frame program-order
  tracking) -- see the block of tests after
  `test_many_loop_iterations_with_nested_if_and_break_continue_does_not_crash`
  in `tests/test_codegen.py::TestAutomaticMemoryReclamation`, plus a
  combined 5000-iteration program exercising all of the above together
  (nested for-in-for with an inner `break`, both return-vs-declaration
  orderings, a 4-way `else if` chain, bare blocks with a shadowed name,
  and `if`/`else` sibling shadowing) run under a fresh AddressSanitizer/
  LeakSanitizer pass -- zero ASan errors, zero leaks with the loop's own
  intentional-leak line removed, confirming (not just reasoning) that
  the new combinations don't interact badly with each other.

  One real, if minor, finding *did* come out of this pass, in
  `_emit_free_active_locals` itself rather than in a missing test: the
  `arr[T]`/`map[T]` free path was loading the whole `{i64, ptr}` header
  value and then `extractvalue`-ing field 1 back out of it, instead of
  a direct `getelementptr` to field 1 + `load` the way every other
  array/map data-pointer read in `codegen.py` already gets there (see
  e.g. `_emit_array_length`, `_try_addressable`) -- harmless once
  through clang/gcc's own `-O2` (this compiler already leans on that
  pass for exactly this kind of cleanup, per the module docstring's own
  "always still followed by a real `calloc` + `free`" note), but
  needlessly larger pre-optimization IR and the one place in the file
  not matching the rest of its own convention. Switched to match; see
  `test_non_escaping_array_local_frees_its_data_pointer` and
  `test_non_escaping_map_local_frees_its_entries_pointer`, updated to
  assert the `getelementptr` shape directly instead of the old
  `extractvalue`-anywhere-in-the-IR check (which had stopped actually
  testing the free path specifically, since array/map *construction*
  emits its own unrelated `extractvalue` elsewhere in the same
  function).

  Also used this pass to re-verify (not re-derive) this file's own
  "Running" example and the tool-dependency breakdown just above --
  see that paragraph's own note on the stale count it replaces.

- **Memory management stage 2: interprocedural call-argument analysis
  (claude.md #75, same session).** Stage 1's own stated limitation: a
  value passed as a call argument was *always* treated as escaping,
  even when the called function provably never retains it -- explicitly
  named, in stage 1's own module docstring, as "interprocedural
  analysis, a later stage." This is that stage, for calls to any
  function declared in the same program (a call to a builtin, or
  through a field/element access rather than a plain function name,
  is unaffected and stays exactly as conservative as before).

  The core mechanism: `escape_analysis.find_escaping_names` gained one
  new optional parameter, `escaping_params` -- a `{func_name: set[int]}`
  map. Its `Call`-handling branch now looks up the callee's name in
  that map (only when the callee is a plain `ast.Identifier`, never for
  a Member-based method call); if found, only the argument positions
  listed in that function's own set are still treated as escaping via
  *this specific call site* -- every other position is exempted from
  the default rule there (the argument may still end up escaping some
  other, unrelated way elsewhere in the same function; this only stops
  this one call site from being the *reason* it does). A callee not in
  the map -- because it's a builtin, or because it hasn't been analyzed
  yet -- leaves the lookup returning `None`, which falls back to the
  exact original unconditional behavior with no special-casing needed
  (the `is not None` guard around the whole exemption makes a missing
  entry behave identically to `escaping_params` never being passed at
  all, which is also exactly what every one of stage 1's own 36
  existing unit tests still exercises unchanged, proving this is
  strictly additive, not a rewrite of the base rule).

  Building the map itself turned out to need no fixpoint, no
  topological sort, and no separate whole-program pre-pass -- the
  single most important design insight of this stage, and the reason
  it stayed a same-session increment rather than becoming its own
  multi-session design effort. Festina already rejects a call to a
  function before its own declaration (semantic.py's "unknown
  function" error, claude.md #48) -- verified directly, not assumed,
  by writing exactly that program and confirming the compile error
  before designing around it. That guarantee means: (1) the only way a
  function can call itself is directly, by its own name, and (2) every
  *other* possible callee of any function F is necessarily declared,
  and therefore necessarily already fully analyzed, before F is. Since
  `CodeGen` already emits every function body in one single pass, in
  source order (`generate()` -> `_toplevel` -> `_emit_func`), stage 2
  needed only: compute `escaping_params[F]` immediately after F's own
  body is walked (`_emit_analyzed_func_body`, right before returning),
  and pass the whole `self.escaping_params` dict (built up
  incrementally, one function at a time, never cleared) into
  `find_escaping_names` for *every* function's analysis, including its
  own. A self-recursive call inside F's own body looks up F's own name
  in a dict that doesn't have F's entry yet -- an ordinary miss, not a
  special case -- so it automatically gets the same conservative
  fallback a call to an unanalyzed builtin gets. No graph, no
  worklist, no cycle detection: the language's own forward-reference
  rule makes the call graph (excluding self-recursion) a DAG that
  happens to already be topologically sorted by source order, for
  free.

  Verified the same three ways as every stage before it, with the
  leak-count check given deliberately more weight here than stage 1's
  own got: a bug in stage 1's reasoning could only ever *under-free*
  (leak a bit more than a perfect analysis would); a bug in stage 2's
  reasoning is the first one in this whole feature that could plausibly
  *over-free* -- mark something safe that a real caller elsewhere still
  needed -- which is a correctness regression, not just a missed
  optimization, exactly the distinction claude.md #74's own docstring
  draws between "leaks but is memory-safe" and an early free. A
  combined program exercising a 3-function non-retaining chain (A calls
  B calls C, C only reads its own parameter -- A's own local must end
  up freed), a 3-function retaining chain (C stores its parameter into
  a global -- nothing anywhere in the chain should be freed), a
  self-recursive function passed a struct parameter (must stay
  conservative), and a two-parameter function where only one position
  actually escapes (must free exactly the safe one, never both, never
  neither), all wrapped inside its own function -- deliberately, so its
  own locals are subject to analysis at all, rather than accidentally
  exercising the separate, pre-existing "`__festina_main`'s own
  top-level statements are never analyzed" limitation instead of this
  one -- run for hundreds of iterations with each iteration's own
  hand-computed expected values checked via `fail(...)` inside the
  program itself: **zero** AddressSanitizer errors (no use-after-free,
  no double-free, no heap-buffer-overflow) and not one `fail()` fired,
  across every combination, every iteration.

  One methodology wrinkle surfaced and resolved during this
  verification, worth recording since it very nearly looked like a
  real bug: the first run (leak detection on) came back with *empty*
  stdout despite the program clearly having executed correctly (no
  `fail()`, clean-looking leak report). Traced to AddressSanitizer's
  own leak-report exit path calling `_exit()` directly when leaks are
  found, which -- unlike a normal `exit()`/`return` from `main` --
  skips flushing libc's stdio buffers; since this program's real stdout
  was redirected to a file (fully buffered, not line-buffered the way a
  terminal would be) and produced meaningfully more leaked bytes than
  the earlier nested-block increment's own equivalent check happened
  to, the entire buffered-but-unflushed output was lost. Confirmed by
  re-running the identical binary with leak detection off
  (`ASAN_OPTIONS=detect_leaks=0`, which disables only the leak *report*
  step -- every other AddressSanitizer check, including everything
  this verification actually needed, stays fully active): full, correct
  401-line output, exit code 0. This is a property of AddressSanitizer
  itself, unrelated to anything this feature or Festina's runtime does
  -- documented here rather than left as a confusing footnote, since it
  also retroactively explains why the earlier nested-block increment's
  own leaky-case ASAN run (tests/CONTRACT.md's own note on it, above)
  never actually showed its own trailing "done" line either, without
  that having been a problem at the time (only the leak *count* was
  being checked there, not the full output).

  With leak detection back on for a *separate*, count-only run: 1198
  leaked allocations, against a hand-derived expectation of 1199 (400
  iterations each contributing one struct passed into the retaining
  chain, one passed into the mixed function's own retaining parameter
  position minus the very last iteration's since it's still reachable
  through the global at exit, and one passed into the self-recursive
  function) -- matching to within LeakSanitizer's own known
  conservative-stack-scanning under-count of a small few (the same
  under-by-one-ish behavior already independently observed and
  explained in this file's own nested-block-increment entry above),
  and critically, *not* anywhere near what it would have been had the
  two provably-safe values in the same program (the pass-through chain
  and the read-only argument position of the two-parameter function)
  also been leaking -- which is exactly the comparison that would have
  caught a bug marking something safe that wasn't, had there been one.

  See `tests/test_escape_analysis.py::TestInterproceduralEscapingParams`
  for the analysis in isolation (no C compiler, no codegen -- a
  hand-built `escaping_params` table exercising every documented rule:
  exemption, non-exemption, unknown callee, position-specific exemption
  in a multi-argument call, a Member-callee method call never
  consulting the table at all, a non-Identifier argument at an exempted
  position still walked normally, and a name still ending up escaping
  through some *other*, unrelated use despite one exempted call site)
  and the new "interprocedural" section of
  `tests/test_codegen.py::TestAutomaticMemoryReclamation` for the same
  properties against real generated IR and real compiled output,
  including the two existing stage-1 tests whose own assertions
  deliberately flipped as a direct, expected consequence of this stage
  (a struct passed to a function that only reads it is now freed; the
  matching "still not freed when the callee actually retains it" case
  was added as its own explicit companion test rather than only
  relying on the flipped one's absence of freeing to imply it).

  See `todo.md`'s "Memory management" section for the full picture,
  including exactly what's still ahead (nested struct/array/map fields
  and reference counting for the values escape analysis can't reach at
  all).

- **Memory management stage 3: stack allocation and map entry keys
  (claude.md #76, same session).** Two closures shipped together, since
  neither widens what stages 1/2 prove safe -- both only change what
  happens once something already is, or close an incomplete
  implementation of a promise stage 1 already made.

  **Stack allocation:** a struct local proven safe by stage 1 or 2 now
  gets `alloca %struct.T` + `store %struct.T zeroinitializer` (the
  explicit zero-init matches `calloc`'s own behavior -- the two
  allocation strategies must stay indistinguishable to any Festina
  program) instead of `calloc(1, sizeof)` + a scheduled `free()`.
  `_emit_stmt`'s own VarDecl handling makes this choice using the exact
  same `self._current_escaping_names` set `_emit_block` already
  consults for freeing -- literally the same boolean condition, reused
  for a different purpose, not a new analysis. `_emit_block`'s own
  per-VarDecl tracking (which builds `_active_free_locals`, the list
  `_emit_free_active_locals` later walks) simply stopped adding
  `StructType` to that list at all -- there's nothing left to free for
  one, so `_emit_free_active_locals`'s own former `StructType` branch
  became dead code and was removed rather than left unreachable.
  `arr[T]`/`map[T]` locals are untouched: their data/entries buffer can
  grow after declaration (`.push()`, a map literal past its initial
  size), so a fixed-size `alloca` isn't safe for it regardless of
  escaping-ness -- they still always `calloc`+free their buffer.

  Soundness argument (not just "it compiles and runs"): a stack
  alloca's own lifetime already matches exactly the points
  `_emit_free_active_locals` would otherwise have freed it at -- LLVM's
  `alloca` reserves one fixed address for the whole enclosing function
  regardless of which basic block textually contains it, so a
  loop-body-declared one is simply reused, address and all, on the next
  iteration (zero allocator traffic, not just leak-free sooner), and a
  recursive call still gets its own genuinely distinct address (a
  stack frame is per call, ordinary calling-convention behavior
  unrelated to this choice). That last claim was verified directly, not
  just assumed correct because it's "how C works": a hand-traced
  recursive accumulator
  (`test_recursive_function_with_a_non_escaping_struct_local_keeps_each_calls_own_value`
  -- `recur(n)` builds `n*100 + recur(n-1)` by reading its own `p.x`
  *after* its own recursive call returns, which would read a corrupted
  value if calls shared one slot) produces the exact hand-derived
  values (`recur(3)` = 600, `recur(5)` = 1500) -- the single most
  important correctness question this change raises, answered with a
  program specifically designed to fail loudly if the assumption were
  wrong, not just one that happens to pass.

  **Map entry keys:** `festina_map_free_entries` (new, in
  `runtime/festina_runtime.c` and declared in `festina_runtime.h`)
  frees each entry's own `strdup`'d key before freeing the entries
  buffer itself, replacing a plain `free(entries)` that leaked every
  key. No new soundness question here at all -- unlike a struct/array/
  map *value* stored into another value's field (see the "nested
  fields" gap below, deliberately not attempted), a map entry's key was
  never a Festina-visible value; `festina_map_set`'s own comment already
  established it as a private copy this runtime made for its own
  internal bookkeeping, never aliased with anything else.

  Rewiring existing IR-shape tests, not just adding new ones: every
  test in `TestAutomaticMemoryReclamation` that asserted `"call void
  @free(" in ir` for a *struct* local no longer has anything true to
  assert -- rewritten to assert the `alloca`/`zeroinitializer` shape
  instead (`test_non_escaping_struct_local_is_stack_allocated`, and
  others), or, where the test's real purpose was exercising the
  loop/break/continue *scheduling* machinery rather than structs
  specifically, rewritten to use `arr[int]` instead (which still goes
  through that exact machinery unchanged) so the same control-flow
  shape stays meaningfully tested
  (`test_a_loop_local_array_is_freed_inside_the_loop_body` and its
  siblings). Every *compile-and-run* (correctness-only) test in the
  same class needed **zero** changes and passed unmodified on the first
  try -- direct, automatic confirmation that the swap is exactly as
  semantically transparent as the design intended, not something
  inferred after the fact.

  Verified under AddressSanitizer/LeakSanitizer across four combined
  programs: two written fresh for this stage (a recursion-focused one
  -- deep and wide recursive struct locals, thousands of loop-local
  structs reused across iterations with `break`/`continue` mixed in,
  nested-if struct locals, each with its own embedded `fail()`
  correctness check -- and a map-key-focused one, thousands of map
  creations with several keys each, some escaping loop iterations) and
  two re-run unchanged from stages 1/2's own earlier verification (to
  confirm this stage didn't regress anything already proven). Zero
  ASan errors across all four. Leak-detection-on runs: zero leaks for
  both fresh programs (nothing in either one escapes, so there's
  nothing left to leak once structs stop going through calloc at all);
  the two stage-1/2 programs' own leak counts stayed the same, give or
  take LeakSanitizer's own already-documented conservative
  under-count (1198 -> 1197 on one, matching the expectation that the
  escaping/leaking *set* is unchanged by this stage -- only the
  mechanism for the *non*-escaping set is).

  **Investigated and deliberately not attempted**, worth recording as
  its own finding rather than a silent omission: freeing a struct/
  array/map-typed *field* of an otherwise-freed struct. The only way to
  populate such a field is `outer.field = someLocal` (no struct-literal
  initializer syntax exists), and inspecting the generated IR for
  exactly that program confirms this stores `someLocal`'s own pointer
  into the field (`store ptr %t11, ptr %t10`) -- an alias, not a copy.
  `someLocal` is already correctly marked escaping by the existing
  assignment-value rule (never freed under its own name), but freeing
  `outer.field`'s value when `outer` itself goes out of scope would
  free that *same* memory reachable through `someLocal`'s still-live
  name, and a later read through `someLocal` -- entirely legal Festina
  code -- would be a genuine use-after-free. This needs real
  aliasing/ownership analysis (does anything else still reference this
  field's value when the struct holding it stops existing), not a small
  syntactic extension of the existing "does this name appear outside a
  safe position" rule -- see todo.md's own note on why this is likely
  the same open design problem as reference counting, from the other
  direction.

  See `todo.md`'s "Memory management" section for the full picture,
  including that nested-field finding in full and reference counting
  for the values escape analysis can't reach at all.

- **Memory management stage 4: reference counting for escaping struct
  globals and locals (claude.md #77, same session).** The remainder
  stages 1-3 can never reach on their own: a value proven to genuinely
  escape has nothing for pure escape analysis to do, since something
  else might still need it when its own declaring scope ends. This
  stage tracks reference counts for struct values specifically and
  frees a value once its count reaches zero.

  **Why this is complete, not the usual "everything but cycles"
  answer:** Festina's type system makes reference cycles structurally
  impossible -- a struct field's type must always be declared *before*
  the struct containing it, the identical rule that already governs
  function forward references. Verified directly, not assumed: `struct
  Node { next:Node }` fails to compile ("unknown type 'Node'"), and so
  does either declaration order of two mutually-referencing structs.
  So the set of types any value could ever transitively reference is
  always a strict subset of what's declared earlier in the same
  program -- a DAG by construction. No cycle detector, no tracing
  collector, needed at all.

  **Representation:** every refcounted struct allocation has a single
  `i64` refcount immediately before the pointer Festina code itself
  sees -- a fixed 8-byte offset regardless of the struct's own field
  layout, since every Festina field type (int/float/bool/text/blob/
  struct/table/image/audio/regex, all lowering to i64/double/i8/ptr)
  has natural alignment no greater than 8 bytes, so no extra padding is
  ever needed between the header and a struct's own fields. A NEGATIVE
  refcount is a sentinel for "immortal, retain/release always a
  no-op" -- used for a struct-typed global's own untouched static
  initial storage (`@Name.header = global {i64, %struct.Point} {i64 -1,
  %struct.Point zeroinitializer}`, `@Name = global ptr
  getelementptr({i64, %struct.Point}, ptr @Name.header, i32 0, i32
  1)`), which was never heap-allocated and must never reach `free()`.
  This means a global's very first-ever assignment needs zero special-
  casing anywhere in codegen: `festina_retain`/`festina_release` are
  always safe to call unconditionally, whatever a global currently
  points to.

  **Scope, deliberately the narrowest slice that's actually sound, not
  the full design originally sketched:** a struct-typed GLOBAL's value
  is always fully reference counted -- `_emit_global_struct_retain_release`
  (one shared helper, used by both `_emit_assign`'s Identifier branch
  for ordinary reassignment and `_emit_toplevel_stmt`'s VarDecl-with-
  initializer branch for a global's own declaration, so the two sites
  can't drift apart) retains the new value and releases the old one on
  every single change. A struct-typed LOCAL is only scheduled for
  release at its own scope-exit (the exact frame/`down_to` machinery
  stages 1-3 already built, completely unchanged) when THREE conditions
  all hold: declared without an initializer, never itself returned
  (`escape_analysis.find_returned_names`, unchanged from when it was
  added for this exact purpose), and never itself the target of a
  plain reassignment (`escape_analysis.find_reassigned_names`, new this
  stage). A local failing any of the three simply keeps leaking,
  exactly as before this stage -- the same "leaks but is memory-safe"
  fallback every not-yet-covered case in this whole effort already
  gets, chosen deliberately over guessing.

  **Two real bugs found and fixed while building this, both traced to
  their exact mechanism before fixing, not patched blind from a
  symptom:**

  1. *Pre-existing, not introduced by this stage*: `Point r =
     someFunc()` for a LOCAL `r` silently discarded the call's return
     value, leaving `r` at its stack-allocated zero value --
     `_emit_stmt`'s own struct VarDecl handling never checked
     `stmt.init` at all (comment literally claimed "no struct-literal
     initializer syntax exists yet, so stmt.init is always None here" --
     true for *literal* syntax, false for a call-result initializer, a
     distinction the comment's author -- this same session -- had
     conflated). A top-level `Point r = someFunc()` already worked
     correctly, through a completely different code path
     (`_emit_toplevel_stmt`) untouched by the bug -- which is exactly
     why this had never been noticed: every existing test exercising
     this pattern happened to use a global. Found while writing this
     stage's own combined verification program and noticing an
     embedded `fail()` fire that had no business firing; confirmed by
     reading the generated IR directly (`%r.storage = alloca
     %struct.Point; store %struct.Point zeroinitializer, ...` with the
     call's own return value computed and then never referenced again)
     before writing the fix. Fixed by making a struct VarDecl-with-
     initializer alias whatever its initializer evaluates to -- no
     allocation of its own at all, the same as a plain `r = expr`
     assignment already does.
  2. *Introduced by this stage's own first pass*: `Point q; q = p;`
     (`q` reassigned, after its own declaration, to alias `p`'s
     storage), with `p` and `q` later assigned to two *different*
     globals, scheduled BOTH for release at their own independent
     scope-exits -- with nothing retaining the extra reference `q = p`
     itself created, the second release decremented a refcount the
     first had already brought to its true value, undercounting by
     one; one more reassignment of either global later, this actually
     freed memory the OTHER global still pointed to, and reading
     through it produced garbage in a plain (non-ASan) build. Found
     the same way -- written into the verification program, then
     hand-traced with a debug build of the runtime that printed every
     retain/release/free call, confirming the exact refcount sequence
     (1 -> 2 -> 3 -> 2 -> 1 -> 0, freed, while a second global still
     held the same pointer) before writing the fix, not just re-running
     until the symptom went away. Fixed by excluding any struct local
     ever found in `find_reassigned_names` from scope-exit release
     scheduling entirely -- the third of the three local-scope
     conditions above.

  **A significant methodology finding, independent of either bug,
  surfaced while chasing bug 2:** the very first attempt to reproduce
  bug 2 under AddressSanitizer produced *zero errors* despite the
  plain build unambiguously printing garbage -- a real red flag, not
  something to shrug off as "ASan just didn't catch this one." Isolated
  with a minimal, hand-written 11-line `.ll` file doing an unambiguous
  calloc+free+read-after-free: `clang -fsanitize=address -c file.ll`
  produced a binary with **zero** `sanitize_address`/`__asan_report*`
  symbols anywhere in it (verified by grepping the `-S -emit-llvm`
  output of that exact compile step) and did not catch the bug; the
  byte-for-byte-equivalent pattern written as `.c` source produced 28
  such symbols and caught it immediately, with a full, correct
  use-after-free report. Root cause: ASan's per-function opt-in (the
  `sanitize_address` LLVM function attribute) is normally added by
  clang's own C frontend during Sema/CodeGen -- a step that's bypassed
  entirely when the input handed to `clang -c` is already `.ll` text,
  so `-fsanitize=address` at the driver level has nothing to attach
  instrumentation to. The fix, once found: add `sanitize_address` to
  every `define` line in the generated `.ll` file before compiling
  (`sed -E 's/^(define [^{]+) \{/\1 sanitize_address {/'` against the
  file, verified afterward by re-checking the instrumentation-symbol
  count went from 0 to a real number) -- confirmed to both catch the
  known bug 2 reproduction reliably and to change nothing about
  already-passing programs' behavior otherwise.

  This means every AddressSanitizer claim made earlier in this same
  session, for stages 1 through 3 and interprocedural analysis, was
  only ever checking two things correctly: linking success, and
  LeakSanitizer's own allocation-tracking (which intercepts the
  allocator's own calls directly and is unaffected by instrumentation
  -- this is *why* every leak-count claim from those earlier stages
  remained trustworthy even though the corruption-detection half never
  actually ran against the generated program's own code). It was never
  actually exercising ASan's heap-buffer-overflow/use-after-free/
  double-free detection against anything except the hand-written
  runtime `.c` file linked alongside each test binary. Rather than
  letting that stand as an open question, every earlier stage's own
  combined verification program (stage 1/2's nested-block and
  interprocedural stress tests, stage 3's recursion-focused and
  map-key-focused stress tests) was re-run through the corrected,
  properly-instrumented pipeline specifically to check for anything the
  flawed methodology might have silently missed -- all came back clean,
  zero ASan errors, confirming those stages' own underlying *design*
  reasoning was sound all along; the two bugs above (both found through
  this same corrected pipeline) were the only real findings from the
  entire retrospective check, and both are specific to this stage's own
  new retain/release logic, not inherited from anything earlier.

  Final verification, with the corrected pipeline from the start: new
  unit tests (`tests/test_escape_analysis.py::TestFindReturnedNames`,
  plus `find_reassigned_names` covered the same way), new IR-level and
  compile-and-run tests in
  `tests/test_codegen.py::TestAutomaticMemoryReclamation` (51 -> 67,
  including one dedicated regression test per bug above, each
  reproducing the exact scenario that found it), and a properly-
  instrumented AddressSanitizer/LeakSanitizer run against a combined
  program exercising: the original global-reassignment-in-a-loop leak
  scenario from when stage 1 first shipped (now correctly freed, not
  leaked -- the concrete payoff this whole stage exists to deliver), a
  sometimes-returned local (correctly still leaks, matching this
  stage's own stated scope), a reassigned local aliasing another
  tracked local (correctly no longer double-released), self-assignment
  of a global struct, and two independently-tracked globals, all run
  for hundreds of iterations with embedded `fail()` correctness checks
  -- zero ASan errors, and LeakSanitizer's only reported leak matches
  exactly the one value this stage's own documented scope says should
  still leak, not a byte more or less.

  See `todo.md`'s "Memory management" section for the full picture,
  including the nested-field nesting problem stage 4's own scope
  deliberately sidesteps, and exactly what widening this stage's own
  scope (retaining on every local assignment, not just a global's)
  would still require.

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

See api.md for the current language/standard library reference and this
file's own "Status" section above for the implemented-vs-not matrix; the
short version: nothing is left
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

`tests/test_loops.py` covers claude.md #60/#61/#63/#66/#73 (for/while
loops, `.length`, postfix `++`/--, `break`/`continue`) at the parser/
semantic level, same split as `test_numeric_conversion.py`; the matching
end-to-end runtime behavior (a compiled loop actually iterating the
right number of times, loop-variable scoping surviving a real function
frame, an iterative Fibonacci, `break`/`continue` actually altering
control flow at runtime) lives in `test_codegen.py`'s `TestLoops`,
`TestArrayLength`, and `TestBreakAndContinue`.

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
parses regex syntax itself before handing it to `regcomp()`. The
`TestRegexLiteral` classes alongside `test_regex.py`/`TestRegex` (see
this section's own "JS-style regex literal syntax" paragraph above)
cover the same ground for the `/pattern/flags` literal form.

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
        ForStmt, BreakStmt, ContinueStmt, Return, ExprStmt, Identifier,
        NumberLit, StringLit, BoolLit, NullLit, TemplateLit, ArrayLit,
        Assign, Ternary, LogicalOp, BinOp, UnaryOp, PostfixOp, Member,
        Call, ArrayTypeExpr
        # (this snapshot predates MapLit/MapTypeExpr/RegexLit too --
        # kept here as a rough orientation sketch, not re-derived from
        # ast.py on every change; ast.py itself is the source of truth)

    types.py
        PrimitiveType(name) / StructType(name) / TableType(name) /
        ArrayType(element) / ImageType() / AudioType() / RegexType()
        -- frozen dataclasses, so equality/hashing work out of the box.
        RegexType() has no fields (claude.md #67: a regex value's
        pattern/flags live in the runtime pointer value, not the static
        type, whether it was created via a /pattern/flags literal --
        ast.RegexLit -- or the regex() builtin, so there's only one
        shape of it either way -- unlike StructType/TableType). Likewise ImageType()
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

`festina/cli.py`'s `main()` is now four subcommands
(`festina compile|run|doctor|help`), not a single bare `festina file.f`
-- deliberate, not incidental: a bare-file form would leave `run`
(executes the compiled result) and `compile` (never does) distinguished
only by which flags happen to be present, which is exactly the kind of
implicit-intent-from-flags design this project avoids elsewhere too. An
unrecognized/missing subcommand falls through to argparse's own usage
error or `main()`'s help-and-exit-1 handling, matching `git`/`cargo`'s
own "no command" vs. "explicitly asked for help" (`festina help`, exit
0) distinction.

- `festina run entry.f` (`run_program`) is `compile_file` into a
  `tempfile.TemporaryDirectory` executable, executed with `subprocess.run`
  and no `capture_output` -- stdin/stdout/stderr inherited directly from
  this process, so an interactive program (graphics/audio/timers) behaves
  identically to one compiled with `festina compile` and run by hand. The
  temp binary is always cleaned up (the `TemporaryDirectory` context
  manager), compile failure or not. Returns the *compiled program's own*
  exit code, not festina's -- `festina run x.f && y` composes the same
  way a real compile-then-execute pair would, matching `go run`/`cargo
  run`.
- `festina doctor` (`_doctor_report`) reuses the exact same
  `_INSTALL_HINTS`/`_PKG_INSTALL_HINTS` dictionaries `_run_tool`/
  `_pkg_config` already raise `CompileError` with on an actual compile
  failure, so a `doctor` report and a real failure always name the same
  fix for the same missing tool -- checked proactively (via
  `shutil.which`/`pkg-config --exists`, non-fatal) rather than only
  reactively. graphics (`cairo-xlib`)/audio (`alsa`) are reported as
  MISSING but NOT required -- claude.md #59/security.md's binary-slimming
  split means a compiler that can't build a graphics program is still a
  fully working compiler for everything else (a program that never uses
  graphics/audio never even asks pkg-config for those flags -- see
  `_RUNTIME_FEATURES` above). `libLLVM` missing is also non-fatal on its
  own (the clang-IR-frontend fallback covers it) UNLESS `clang` itself is
  *also* missing, in which case neither pipeline can finish a compile at
  all -- that combination genuinely is required, and is reported as such.
  Also checks whether `festina` itself resolves on `PATH`
  (`shutil.which("festina")`) -- if not, prints a concrete fix: the
  checkout's `bin/` directory to add to `PATH` (with a copy-pasteable
  `export PATH=...` line) when running from source, or (detected via
  `sys._MEIPASS`, the same signal `_data_root()` uses) a `ln -s` command
  pointing at the packaged binary's own real path when running the
  PyInstaller-packaged `festina` directly. This check never affects the
  exit code either way -- it's a convenience note, not something that
  stops the compiler from working (you can always invoke it via
  `bin/festina`/`python3 -m festina.cli` instead).

See `tests/test_cli.py` -- `TestRun` (including exit-code propagation and
that a compile error still produces a clean `CompileError`, not a raw
`OSError`, for a nonexistent temp binary), `TestDoctor` (every check,
in both the OK and MISSING states, including the two-tool-hidden-at-once
case for the `libLLVM`-and-no-`clang` combination, reusing
`TestMissingDependencyErrors`'s own `path_without` fixture pattern from
`test_codegen.py`), and `TestHelpAndNoCommand` for the bare-invocation/
`help` exit-code distinction.

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
pytest tests/                          # 522 passed, 239 skipped (needs a C compiler; 2 of
                                        # those skips need `pip install pyinstaller` too,
                                        # 8 need Xvfb + xdotool installed too) given a
                                        # working C compiler, all 761 pass
```
