# Security

Festina compiles to native executables and links a C runtime — the two
places a memory-safety or dependency-supply-chain issue would actually
bite. This document tracks what's been audited, what was found and
fixed, and how to report something new.

## Reporting a vulnerability

Open an issue on the [repository](https://github.com/uraikus/festina)
describing the problem and, if possible, a minimal Festina program (or
runtime input) that reproduces it. There's no dedicated security contact
or disclosure program yet — this is a young project, and that process
will grow if/when it needs to.

## Latest audit (this pass)

A dedicated spec-compliance/security/robustness audit — prompted
directly, not incidental to any one feature landing — reviewed
`festina/semantic.py`'s type checking and `runtime/festina_runtime.c`'s
C code end to end and found eight real bugs, seven compile-time
correctness gaps and one genuine runtime memory-safety issue. Every fix
below has its own regression test (mostly in
`tests/test_semantic_errors.py`) and is described in full, including the
exact reasoning, in [`tests/CONTRACT.md`](tests/CONTRACT.md)'s "Status"
section — this page is the summary.

### The memory-safety finding: a stack buffer overflow in schema sync

**Severity: high (crash / memory corruption), but not remotely
exploitable** — triggering it requires writing Festina source with a
sufficiently wide `table` declaration; there's no way to reach it from
untrusted network input or file content, since table schemas are part
of the compiled program, not data it reads at runtime.

`festina_sync_table` (`runtime/festina_runtime.c`, implementing `claude.md
#28-31`'s automatic schema migration) built several `CREATE
TABLE`/`ALTER TABLE`/rebuild SQL statements incrementally across a loop
over the declared columns, using the textbook-looking pattern:

```c
pos += snprintf(buf + pos, sizeof(buf) - pos, ...);
```

This looks bounds-safe but isn't: `snprintf`'s return value is how many
bytes *would* have been written if the buffer were big enough, not how
many actually fit. Once accumulated output exceeds the buffer, `pos`
exceeds `sizeof(buf)`, and the next iteration computes `sizeof(buf) -
pos` as unsigned arithmetic between a smaller value and a larger one —
silently underflowing to a number near `SIZE_MAX`. `snprintf` is then
told it has ~18 exabytes of room and gladly writes straight past the
real (2048-or-so-byte) stack array.

**Verified as a real, reproducible crash**, not a theoretical concern: a
`table` declaration with enough columns, or long enough column/table
names, that the generated SQL exceeded any of four affected fixed-size
buffers (`sql`/`create_sql`, both 2048 bytes; `dest_cols`/`src_cols`,
both 1024 bytes, in the column-drop/retype rebuild path) reliably
corrupted the stack under AddressSanitizer. Not a contrived adversarial
input either — any Festina program with a sufficiently wide table could
trigger it by accident.

**Fix:** a new `festina_check_sql_buffer` helper, called at the top of
every loop iteration that accumulates into one of these buffers (and
once more after each loop, before any final fixed-text append) —
guaranteeing `sizeof(buf) - pos` is never computed once `pos` has
already reached or passed the buffer size. A table that genuinely can't
fit now fails cleanly with `festina_fail()` naming the actual condition
("too many columns, or column/table names too long") instead of
corrupting memory. Re-verified the same way the bug was found: rebuilt
against AddressSanitizer, the same 40-column table that used to crash
now fails cleanly with no ASAN report, and an ordinary small table still
succeeds. Regression test:
`tests/test_codegen.py::TestAutomaticSqliteSchemaSync::test_a_table_too_wide_for_the_runtimes_sql_buffer_fails_cleanly`
locks in the clean-failure *behavior*; the memory-safety fix itself was
checked by hand against ASAN, since the normal test pipeline doesn't
build with it.

### Seven further compile-time/robustness fixes

Each of these let a program that should have been rejected at compile
time reach codegen instead, in every case surfacing as either silently
wrong generated behavior or a confusing internal "LLVM object emission
failed" error rather than a clear, actionable one — never memory
corruption, but all real spec-compliance and diagnosability gaps:

1. **`claude.md #36`'s own only worked example for `blob` failed to
   compile at all** (`blob data = 'path/to/file'` — a string literal
   infers as `text`, and `text`/`blob` were fully incompatible types
   with no exception) — nothing else in the language could construct a
   blob value either. Fixed by allowing `text -> blob` assignment
   specifically (the one direction the spec's own example needs, and
   safe since both share an identical `ptr` runtime representation).
2. **`log()` on the one blob value that could then exist crashed the
   *compiler itself*** with a bare Python `KeyError` (blob passed the
   "is this a primitive type" check in `log()`'s dispatch but had no
   entry in the type-to-runtime-function table) — previously
   unreachable for the same reason issue 1 was, but a real crash risk
   the instant blob became constructible, fixed alongside it.
3. **`==`/`!=`/`<`/`>`/`<=`/`>=` had no general type-compatibility
   check** — `5 == 'x'`, `'a' < 'b'`, `bool == int` all passed semantic
   analysis and reached codegen, which emitted invalid LLVM IR (a raw
   integer constant where a pointer was required).
4. **A non-`int` array index** (`a[1.5]`, `a['x']`) had the identical
   shape of bug — `claude.md #65` explicitly requires an int index, but
   the computed type was inferred and simply discarded, never checked,
   for both a read and a write target.
5. **A `const` could be reassigned with plain `=`** — only postfix
   `++`/`--` was ever protected, undermining `claude.md #22`'s
   "constants should be available for compiler optimization" guarantee
   (which only holds if a const genuinely never changes).
6. **A `void` function could return a value** (silently discarded) **and
   a non-`void` function could bare-`return` with none** — `claude.md
   #23`'s void/non-void distinction directly implies both directions
   should be compile errors; neither was checked.
7. **Declaring a function with the same name as a builtin**
   (`drawRect`, `setTimeout`, `regex`, ...) compiled fine but made that
   function permanently uncallable, silently — codegen's builtin-name
   dispatch always wins over a same-named user function. README used to
   excuse this because graphics/audio/timers weren't implemented yet;
   now that they are, the collision can actually bite, so declaring a
   function with any of these eleven names is now a compile-time error.

Full technical detail (exact reasoning, code locations, before/after
behavior) for all eight is in `tests/CONTRACT.md`.

### Two things confirmed *not* to be bugs

Worth recording so they aren't re-litigated by a future audit pass:

- **No SQL injection via table/column names.** Table and column names
  are embedded directly into generated SQL text (unavoidable — SQL
  can't parameterize identifiers), but the lexer's own `IDENT` token
  grammar (`[A-Za-z_][A-Za-z0-9_]*`) can't produce a quote, semicolon,
  or any other SQL metacharacter, so there's no legitimate Festina
  syntax that could smuggle a payload through. (Query *values* were
  already fully parameterized via `sqlite3_bind_*` — `claude.md #32-34`
  — this only concerns identifiers, which are structurally different.)
- **No format-string vulnerability**, despite untrusted text values
  flowing into `printf`-family calls throughout the runtime (`log()`,
  `fail()`, error messages built from user data) — every one of them
  passes that text as a `%s` *argument*, never as the format string
  itself.

## Graphics: a real window-manager crash (BadMatch on XSetInputFocus)

**Severity: low (crash, not corruption or exploitable)**, but a real,
user-reported bug affecting any graphics program (`claude.md #39`) run
under an actual desktop window manager, not just the Xvfb-based test
setup this project's own test suite runs against.

`festina_graphics_init` (`runtime/festina_runtime_graphics.c`) asks for
keyboard focus directly right after mapping its window
(`XSetInputFocus`) — necessary since a bare Xvfb instance runs no window
manager to hand focus over on its own, and without it `on key` would
never fire under test. Under a *real* window manager, though, this is a
genuine race: the WM can still be reparenting the just-mapped window at
the exact moment this request reaches the server, so the window is
transiently not yet "viewable" — a real, reproducible `BadMatch` (major
opcode 42, `X_SetInputFocus`), and Xlib's *default* error handler prints
that and then `exit()`s the whole program, crashing any graphics program
(including the `tic_tac_toe.f` example) the instant it opened its
window, on a real desktop.

**Confirmed directly, not assumed from reading the X11 spec**: reproduced
reliably by running a compiled Festina graphics program against Xvfb
with a real window manager attached (`openbox`) instead of the bare,
WM-less Xvfb the test suite otherwise uses — the exact
`X Error of failed request: BadMatch ... X_SetInputFocus` the reporting
user saw. A separate, unrelated hang was found and ruled out while
narrowing this down: an early attempt to reproduce this under `twm` (a
much older, rarely-used WM) surfaced its own, apparently
grab-related deadlock deep inside `cairo_xlib_surface_create` that
has nothing to do with this bug (confirmed by testing under `openbox`,
where it doesn't occur at all) — not chased further, since `twm` isn't
a WM any real user is likely running.

**Fix:** the one `XSetInputFocus` call is now wrapped in a narrowly
scoped, temporary error handler (`festina_ignore_focus_error`) that
tolerates exactly this failure — installed immediately before the call,
restored immediately after an explicit `XSync` forces any pending error
to be delivered first. Every other X11 error the program might hit
still goes through Xlib's own default (fatal) handler, unchanged — this
only silences the one call already documented as "harmless either way"
if it fails.

Regression test:
`tests/test_codegen.py::TestGraphics::test_graphics_init_does_not_crash_under_a_real_window_manager`,
via a new `x_display_with_wm` fixture (`tests/conftest.py`) that layers
a real `openbox` instance on top of the existing Xvfb-based `x_display`
fixture — confirmed to both reproduce the original crash (fails cleanly
without the fix, the window never appears) and pass cleanly with it.

## Binary slimming

Not a vulnerability fix, but a real reduction in attack surface / supply
chain exposure, done alongside this audit pass (`claude.md #59`: "if a
canvas isn't used, keep the binary remains slim"): a compiled Festina
program used to dynamically link `libcairo`, `libX11`, and `libasound`
(plus their own transitive dependencies — fontconfig, freetype, libpng,
the X11 client-side stack, ...) *unconditionally*, even for a program
that never calls a single graphics or audio function. Confirmed directly:
a trivial `log('hello')` program pulled in 24 shared libraries at
runtime.

Every one of those is now something a fully audited, security-conscious
deployment has to account for in its threat model and keep patched —
regardless of whether the compiled program ever exercises that code
path. Fewer unnecessary runtime dependencies is a smaller, more
auditable footprint, independent of whether any specific library has a
known issue today.

**The fix:** `runtime/festina_runtime.c` was split into three
translation units — core (always linked: `log`/`fail`/string
interpolation/sqlite/regex/timers), `festina_runtime_graphics.c`
(Cairo/X11), and `festina_runtime_audio.c` (ALSA) — so `festina/cli.py`
only ever passes the graphics/audio object files, and their pkg-config
libraries, to the linker when a specific compiled program actually calls
something from them (`CodeGen.uses_graphics_code`/`uses_audio` in
`festina/codegen.py`). A first attempt at this used only
`-ffunction-sections -fdata-sections`/`--gc-sections`/`--as-needed`
compiler/linker flags, with everything still in one object file —
verified empirically that this does **not** work: it correctly
eliminates unused *code* (confirmed via `readelf --dyn-syms` showing
zero remaining undefined references to any Cairo/X11/ALSA symbol) but
the linked binary still listed all three libraries as `NEEDED` (`readelf
-d`/`ldd`) regardless, because `--as-needed`'s decision is made against
the whole translation unit any live symbol pulls in, before
`--gc-sections` has pruned anything out of it. Splitting into genuinely
separate object files sidesteps that: an unused feature's library is
never on the linker's command line to begin with. See
`tests/test_codegen.py::TestSlimBinaries` for the automated regression
coverage (verified via `ldd` on real compiled binaries, for all four
graphics/audio combinations) and `tests/CONTRACT.md`'s "Status" section
for the full design writeup.

## Design-level security notes (not audit findings, standing properties)

- **No network exposure.** Festina has no networking support yet (see
  [todo.md](todo.md)) — a compiled program's only external interfaces
  today are the local filesystem (`festina.sqlite`, `loadImage()`/
  `loadAudio()` paths, and — since `DatabaseURL`/`environment.NAME`
  (`claude.md #70/#71`) landed — the process's own environment
  variables), and, if it uses graphics, the local X server. This
  significantly narrows what "attack surface" even means for a Festina
  program right now; that will change once HTTP support lands, and this
  document will grow accordingly. `environment.NAME` is a direct,
  read-only `getenv()` wrapper (`festina_getenv`) with no parsing or
  interpretation of the result — a compiled program that does something
  security-sensitive with an environment variable's value (including
  `DatabaseURL = environment.NAME` picking the database file itself) is
  exactly as exposed as any other program trusting its own environment,
  no more and no less; this runtime adds no additional risk on top of
  that.
- **No unsafe deserialization.** `sqlite()` query results are read
  through SQLite's own typed column API (`sqlite3_column_*`), not parsed
  from an untrusted byte stream by hand.
- **Known, accepted memory-management gap, partially closed
  (not a vulnerability):** arrays and struct storage were originally
  always heap-allocated and never freed; `claude.md #43` promises
  automatic memory management this compiler is implementing in stages,
  not all at once — the exact opposite of the "wrong answer here is a
  genuine memory-safety regression" risk this document already flags
  below applies just as much to shipping the whole thing in one
  uncareful pass as it does to shipping nothing.
  `claude.md #74` (stage 1) frees a local struct/`arr[T]`/`map[T]`
  automatically whenever `festina/escape_analysis.py` can prove, from
  the syntax of its declaring function/handler alone, that its address
  never left it — as soon as control leaves the block it was declared
  in (a function/handler's own top-level body, or any nested `if`/
  `while`/`for` body), not deferred until the function eventually
  returns: a loop-body-declared value is freed every iteration, and
  `break`/`continue` free everything declared since that loop's own
  body began before actually leaving. `claude.md #75` (stage 2) closes
  stage 1's own stated call-argument limitation for calls between
  functions declared in the same program: a value passed as a call
  argument is no longer *unconditionally* treated as escaping —
  each function's own parameters are analyzed the same way locals
  always were, once, in declaration order (Festina requires a function
  be declared before it's called, so every possible callee is already
  fully analyzed by the time a caller needs to consult it; a
  self-recursive call is the one exception, and falls back to the
  original conservative rule automatically, with no special-casing
  needed), and a caller's argument is only exempted from the default
  rule at a call site whose corresponding parameter position that
  callee's own analysis already proved safe — composing transitively
  across any number of calls for free. A call to a builtin, or through
  a field/element access rather than a plain function name, is
  unaffected and stays exactly as conservative as before.
  `claude.md #76` (stage 3) changes *how* a value already proven safe
  by stage 1 or 2 is handled, not what counts as proven safe: a struct
  is now a real stack allocation instead of a `calloc`+`free` pair
  (sound for the identical reason freeing early was sound — an address
  that never escapes needs nothing beyond its own function's stack
  frame, and a stack alloca's own lifetime already matches exactly the
  points the old `free()` would have fired at, including a fresh
  address per recursive call — verified directly with a hand-traced
  recursive-accumulator program, not just reasoned about), and a freed
  `map[T]`'s own per-entry keys are now freed alongside its entries
  buffer (previously only the buffer itself was, leaking each entry's
  own `strdup`'d key — closing stage 1's own originally-stated gap on
  this, not a new capability; a map entry's key was never a
  Festina-visible value to begin with, just a private copy this
  runtime made for its own bookkeeping, so this needed no new
  aliasing reasoning the way the item below did). `arr[T]`/`map[T]`
  locals are deliberately unaffected by the stack-allocation half of
  this — their data/entries buffer can grow after declaration, so its
  size isn't known in advance, and a stack allocation needs a size
  that is.

  `claude.md #77` (stage 4) covers the remainder stages 1-3 can never
  reach on their own: a struct value proven to genuinely escape now has
  its own reference count (a struct-typed global, always; a struct-
  typed local at its own scope-exit, including one that's returned --
  `return` itself now retains the value first whenever its source might
  be aliased, the same treatment every other struct-producing site in
  this stage gets), freed once nothing references it anymore. This is
  sound specifically because Festina's type system makes reference
  cycles structurally impossible — a struct field's type must always be
  declared *before* the struct containing it, verified directly by
  confirming `struct Node { next:Node }` (and two mutually-referencing
  structs in either declaration order) fail to compile — so plain
  reference counting is a *complete* answer here, not the usual
  "handles everything but cycles" partial one.

  Stage 4 initially shipped with a narrower local-scope carve-out (also
  excluding a local declared with an initializer, or ever itself
  reassigned) before widening to the scope above in the same stage: a
  new `_is_owning_struct_source` classification retains a local's new
  value whenever its source expression isn't a plain function call
  (reading an existing identifier, a struct field, a ternary, ...,
  since any of those might alias a value some other tracked binding
  already references) and skips the retain only when the source is a
  fresh call result nothing else yet references — the same
  conservative "unprovable means retain" bias used everywhere else in
  this feature. Unlike stage 4's first pass, this widening's own
  verification (unit, IR-level, compile-and-run, and real
  AddressSanitizer/LeakSanitizer runs, properly instrumented from the
  start) found no new bugs.

  Still in the same stage, `return` itself got the identical treatment
  next: retain the value being returned first, whenever its source
  isn't a plain function call, then release every active local exactly
  as every other function exit already does -- no more excluding a
  name that's ever returned from that release entirely. This is a real
  correctness fix, not just a leak-closing one, caught by deliberately
  testing the case the old name-based exclusion could never reach: a
  struct-typed *parameter* returned directly aliases the *caller's own*
  storage, not a fresh value, so without retaining it there, the
  caller's own local would be left as the sole holder of a refcount
  that never accounted for the return value's own new binding also
  pointing at it -- the caller's own local going out of scope first
  would free memory the return-value binding still pointed to, a
  genuine use-after-free on the next read through it. A second gap the
  old exclusion could never have closed either: it only recognized a
  *bare* Return value (`return p`), so `return cond ? a : b` -- neither
  branch a bare Identifier -- was invisible to it, meaning whichever
  branch actually ran on a given call could have been released out from
  under the caller before this fix, a genuine soundness hole, not
  merely an over-conservative one. Verified with dedicated tests for
  both: the parameter-aliasing case (reading the caller's own local and
  the return value well past where the old code would have released the
  caller's copy) and the Ternary case (confirming the untaken branch is
  freed and the taken one survives correctly), plus a combined stress
  program folding this together with the earlier local-scope widening.
  A real AddressSanitizer/LeakSanitizer run against that combined
  program came back with zero ASan errors and a leak count matching
  exactly the number of deliberately-*discarded* return values in it,
  one-for-one -- confirming every other case is now correctly freed.

  Still in the same stage, that one remaining leak (a discarded return
  value) was closed next: `_emit_stmt`'s handling of a bare-expression
  statement now checks whether it's a struct-returning `Call` with its
  result never bound to anything, and if so, releases the value right
  there. This needed no new analysis to justify, unlike most of this
  stage's other decisions -- a function call's own return value is
  always the "owning," freshly-produced kind this stage already treats
  specially, so a call site that never binds it to anything is *by
  construction* that value's only reference; releasing it there can
  never free something another binding still needs, because no other
  binding could possibly exist. Verified the same way as everything
  above: a real AddressSanitizer/LeakSanitizer run against the exact
  program that previously leaked 2000 objects (one per discarded call,
  documented when the local-scope widening first shipped) now reports
  zero leaks, and the retain-on-Return fix's own combined verification
  program -- previously leaking 2000 objects for the identical reason
  -- is now fully leak-free too.

  A nested case was investigated during stage 3 and *deliberately not
  attempted* at the time, after finding a real soundness hazard, not
  simply left alone: freeing a struct-typed **field** of an
  otherwise-freed struct. The only way to populate such a field is
  assigning an existing local's value into it (`outer.field =
  someLocal` — there's no struct-literal initializer syntax), and that
  assignment stores `someLocal`'s own pointer into the field — an
  alias, not a copy (confirmed directly in generated IR, not assumed).
  `someLocal` was already correctly marked escaping by the existing
  assignment-value rule, so it was never at risk of being double-freed
  under its own name — but without retaining that field's own
  reference, `someLocal`'s own ordinary scope-exit release could free
  memory `outer.field` still pointed to, and a later read through
  `outer.field` (still entirely legal Festina code) would be a genuine
  use-after-free.

  `claude.md #78` (stage 5, later in the same session) closes this,
  treating a struct-typed field exactly like any other binding stage 4
  already retains and releases for: `outer.field = value` now retains
  the new value first (skipped only for a fresh call result, the
  identical rule stage 4 already applies everywhere else) and releases
  whatever the field previously held (always safe -- a struct's own
  fields start out null). Symmetrically, when a struct's own refcount
  reaches zero, each of its own struct-typed fields is now released
  too, *before* its own storage is freed -- recursively, through a new
  per-struct-type release function generated at compile time (the
  runtime itself is entirely type-blind, so cascading into a struct's
  own fields needs the compiler's own knowledge of that struct's
  layout, not a small runtime addition), with the plain, unchanged
  generic release kept for the overwhelming majority of structs that
  have no struct-typed field of their own -- no new function, no extra
  indirection, for those. Sound for the identical reason stage 4's own
  reference-cycle argument already established: a struct field's type
  must always be declared before the struct containing it, so this
  recursion always terminates.

  Building this surfaced one real bug, found and fixed before shipping
  as part of this stage's own verification, not after: a struct local
  provably safe to live on the *stack* (never heap-allocated,
  never itself refcounted) can still have a struct-typed field written
  into it, and the retain above fires regardless of whether the
  *container* is stack- or heap-allocated. For a heap-allocated
  container, releasing the container itself (stage 4) already released
  that reference too. For a stack-allocated one, nothing did --
  producing a genuine new leak (not a corruption bug: an
  over-retained reference can only delay a free indefinitely, never
  free something too early), confirmed directly via a real
  AddressSanitizer/LeakSanitizer run before being fixed. Closed the
  same way an `arr[T]`/`map[T]` local's own data/entries buffer is
  already handled despite its header being stack-allocated: release
  *only* the struct's own field references at scope-exit, never
  its own (nonexistent) refcount header.

  Stage 4's own build process found two real bugs, both fixed and
  covered by regression tests before shipping — worth naming precisely
  here, not just "bugs were fixed": (1) a *pre-existing* bug, unrelated
  to reference counting itself, where a local `Point r = someFunc()`
  silently discarded the call's return value and left `r` zeroed,
  because the struct VarDecl codegen path never looked at its own
  initializer expression at all; and (2) a bug stage 4's own first pass
  introduced, where reassigning a local struct to alias another
  (`Point q; q = p;`) followed by both ending up independently tracked
  for release meant the same underlying value could be released twice
  — fixed by excluding any struct local that's ever reassigned from
  release tracking entirely (the third of the three local-scope
  conditions above). Finding bug (2) also surfaced a significant,
  independent methodology finding: `clang -fsanitize=address -c
  file.ll` does not actually instrument raw LLVM IR text the way it
  instruments C source (verified directly — a hand-written
  calloc+free+read-after-free `.ll` file compiled this way produced
  zero ASan instrumentation symbols and didn't catch the bug; the
  identical pattern as `.c` source did). This means every earlier
  AddressSanitizer claim in this document, for stages 1 through 3 and
  interprocedural analysis, only ever exercised LeakSanitizer's
  allocation tracking (unaffected by instrumentation) and the
  hand-written runtime's own `.c` code, never the *generated program's*
  own memory accesses for corruption. Re-running those earlier stages'
  own combined verification programs through the corrected pipeline
  (adding the `sanitize_address` attribute to every generated function
  before compiling) found nothing else wrong — their own design
  reasoning holds up; only stage 4's own new logic had never actually
  been checked for corruption before, and both bugs it turned up are
  now fixed. See [todo.md](todo.md#memory-management) for the complete
  writeup and reproduction.

  `claude.md #79` (stage 6) closes the last of the three remaining
  gaps: an escaping `arr[T]`/`map[T]` value now gets the identical
  reference-counting treatment stage 4 gave structs. This needed a real
  representation change first, not just a new tracking rule --
  `arr[T]`/`map[T]` used to be a plain `{length, data}`/`{count,
  entries}` *value*, copied by value on every assignment, so two
  bindings made to alias each other each got their own independent
  copy, sharing the same data/entries pointer only until one of them
  changed. This was merely imprecise for `arr[T]` (arrays never grow
  after construction), but a **real, pre-existing memory-safety bug**
  for `map[T]` specifically, found and confirmed directly while
  designing this stage, not assumed: growing a map through one alias
  (`b['newkey'] = v`, reallocating the entries buffer) never updated
  any *other* alias's own independent copy of the entries pointer,
  leaving it stale -- a dedicated reproduction (`map[int] b = a;
  b['y'] = 2; log(a['y'])`) **segfaults** on the code as it stood before
  this stage, unrelated to anything else in this stage's own work (it
  never touches map assignment or `festina_map_set` at all). Making
  `arr[T]`/`map[T]` a single `ptr` to its own heap-allocated header --
  the identical representation a struct value already has, needed
  anyway for refcounting to mean anything precise -- fixes this as a
  direct consequence: two aliased bindings now share the exact same
  header, so a growth through either is correctly visible through both,
  confirmed by re-running the exact reproduction above and getting `2`,
  not a crash. Release itself needed no per-type generated wrapper the
  way a struct's own field cascade does -- every `arr[T]`'s header has
  the identical shape regardless of T (same for `map[T]`), so two fixed
  runtime functions (`festina_release_array`/`festina_release_map`)
  cover every case, dispatched through the same `_release_fn_for` that
  also routes to a struct's own per-type release function.

  Stage 6's own verification surfaced one more real, precisely
  characterized use-after-free, deliberately left open rather than
  patched blind: a struct-typed value stored as an array *element*
  (not a struct *field*, which stage 5 already closed) can still be
  read after the local it came from has gone out of scope and been
  released -- confirmed directly with a dedicated reproduction (a fresh
  struct stored as an array's sole element, the array escaping through
  a global while the struct's own local function returns), caught by
  AddressSanitizer as a genuine heap-use-after-free. This stage only
  ever refcounts an arr[T]/map[T]'s own *header*, never what's stored
  *inside* it, so this hazard is exactly as open after this stage as
  before it -- a dynamically-sized, runtime-indexed collection needs a
  materially different fix than the fixed-field-list walk stage 5's own
  cascade already does, not attempted here; see
  [todo.md](todo.md#memory-management) for the full reproduction and
  why it's a separate design problem.

  `claude.md #80` (stage 7) closes exactly that gap: an array element
  or map value whose own type is itself refcounted (struct, `arr[T]`,
  or `map[T]`) is now retained when stored and released when
  overwritten or when the container holding it is freed. Confirmed
  directly by re-running stage 6's own reproduction above -- it now
  prints the correct value on every iteration of a 2000-iteration run
  instead of crashing a genuine heap-use-after-free, and a fresh
  AddressSanitizer/LeakSanitizer build of it reports zero errors and
  zero leaks. Sound for the same structural reason stage 4's own
  reference-cycle argument already gives, one level down: Festina's
  grammar gives every `arr[T]`/`map[T]` type a syntactically fresh,
  finite type expression at each nesting level, so releasing a nested
  `arr[arr[T]]`'s own elements always terminates. `map[T]` needed a
  different mechanism from `arr[T]` for both directions, since a
  `FestinaMapEntry`'s own layout is deliberately opaque outside the C
  runtime: releasing every value in a map being freed reuses the
  existing `festina_map_for_each` iteration `.forEach()` already relies
  on, passing a release-flavored callback instead of a user one, rather
  than exposing any new C-side structure access. See
  [todo.md](todo.md#memory-management) for the full writeup.

  Everything not covered by any stage (whether a value stored into a
  field of a call argument is itself retained) still leaks exactly as
  before — a resource leak in a long-running process, not a safety
  issue on its own, no different in kind from the gap this note already
  accepted. With stage 7, every genuine memory-*safety* gap this whole
  effort ever found and confirmed (as opposed to a mere resource leak)
  is closed.

  `claude.md #83` (stage 9) closes the largest remaining *resource*
  leak, and the one no audit had looked at: stages 1-7 covered
  `struct`/`arr[T]`/`map[T]` and left `text` out of all of it, never
  freeing a text value anywhere in generated code, at any binding site,
  under any circumstance. Found by profiling rather than auditing —
  `benchmark.md`'s own `string_concat` was spending essentially its
  whole runtime growing the heap (816 `brk()` calls against 3 for
  equivalent leak-free C). Text deliberately does *not* get the
  refcount-header representation stages 4-7 use, since its payload is a
  plain `char*` that sqlite, the regex engine, and `festina_log_text`
  all take directly; it gets exclusivity by copying instead
  (`festina_text_own`, a NULL-safe `strdup`), so that every text
  binding always holds either NULL or a buffer it owns exclusively.
  That invariant is what lets text be freed with *no* escape analysis:
  copying happens at each consuming site rather than by draining the
  source, so no number of other readers can make freeing a text local
  unsafe. Two safety-relevant prerequisites came out of it — an
  uninitialized `text s` local previously got an alloca and no store at
  all, which was harmless only while text was never freed and an
  immediate wild-pointer `free()` afterward, and `claude.md #82`'s
  empty-piece template optimization had to be revised so that a
  template concatenating nothing (`` `${name}` ``) no longer hands back
  an interpolated variable's own buffer to a caller that believes it
  owns it.

  `claude.md #84` (stage 10) closes a **real, pre-existing
  use-after-free** that this effort had not previously found — not
  introduced by stage 9, but discovered while designing text's own
  parameter handling, which has the identical shape. A
  `struct`/`arr[T]`/`map[T]` parameter is passed as the caller's raw,
  unretained pointer (a deliberate borrowed convention), but a callee
  that *reassigns* its own parameter runs stage 4's ordinary
  local-reassignment path, releasing a refcount it never incremented
  and freeing the caller's live value out from under it. Worth
  recording for any future audit: the two most obvious reproductions
  both hide the bug — an unassigned global still carries the immortal
  negative-refcount sentinel on which retain/release are no-ops, and an
  assigned global's own unconditional retain leaves the count at 2 so
  the symptom degrades to a leak. Only a heap-allocated **local**
  passed to a parameter-reassigning callee exposes it, and under
  AddressSanitizer it is an unambiguous heap-use-after-free with both
  the freeing and allocating stacks recorded. Closed by giving any
  parameter the callee assigns to its own reference at binding time,
  released at the callee's own scope exit.

  `claude.md #85` (stage 11) closes two further pre-existing leak
  classes that the text work surfaced but did not cause, both unbounded
  rather than one-off. A sqlite result row is built by
  `festina_sqlite_collect_rows` as a plain `malloc` with its text
  columns strdup'd in and **no refcount header**, and `TableType` is a
  separate type class from `StructType`, so every
  `isinstance(t, (StructType, ArrayType, MapType))` check in codegen
  missed it and nothing ever freed a row or its text columns — meaning
  `arr[People] rows = sqlite(...)`, this language's most central idiom,
  leaked its entire row set on every query. The container itself was
  always freed correctly; only the rows hanging off it were not.
  Because a row has no header it cannot go through `festina_release`,
  and because the array owns its rows outright a `People p = rows[0]`
  local is only borrowing one — so the per-row free is reached solely
  from the array's own element cascade and deliberately not exposed
  through `_release_fn_for`, which would otherwise let an arbitrary
  TableType binding free a row the array still owns. Separately, every
  runtime `regex(...)` call compiled a `regex_t` nothing ever freed, so
  a `regex(...)` inside a loop leaked a full automaton per iteration;
  those are now released via `festina_regex_free`, while a `/pattern/`
  literal — compiled once into a process-lifetime cache — is
  deliberately left alone, since freeing one would leave every later
  evaluation running against a dangling `regex_t`.

  Two resource leaks remain deliberately open and are tracked rather
  than claimed closed: a regex bound to a variable (bounded by the
  number of such declarations, not by how often they run — regex has no
  binding-level ownership story the way text now does), and text
  globals at process exit, matching how every other global already
  behaves. See [todo.md](todo.md#memory-management).

  What changed across all seven stages is that each one's own fix, at
  every step of building it out, was verified with the same rigor the
  rest of this document's findings were: exhaustive unit tests of the
  analysis itself, end-to-end compile-and-run tests including the exact
  "return a struct by value" pattern the earlier naive stack-allocation
  attempt below got wrong and the critical "a value merely used inside
  a loop, not declared inside it, survives that loop's own break/
  continue" case, and real AddressSanitizer/LeakSanitizer runs (zero
  ASAN errors every time, with stage 4's own verification specifically
  using the corrected, properly-instrumented pipeline described above;
  LeakSanitizer's reported leaks matched the hand-derived expected
  count almost exactly every time it was checked, including one run
  where the "extra" leak turned out to be a real, separately-explained,
  structurally different gap -- a global repeatedly reassigned
  orphaning its previous value -- confirmed by removing that one line
  and re-running to zero leaks, not waved away, and stage 4 itself
  closing that exact original leak scenario for good; several stages'
  own combined verification programs specifically ran with leak
  detection off for their correctness check, since AddressSanitizer's
  own leak-report exit path can skip flushing already-buffered stdout
  -- unrelated to this feature -- and separately with leak detection on
  to confirm only the values genuinely proven to escape were the ones
  that leaked)
  — see [todo.md](todo.md#memory-management) for the full writeup,
  including the naive stack-allocation attempt that was tried before
  stage 1 existed and reverted after being verified to corrupt memory,
  and exactly what the nested-field case and widening stage 4's own
  scope would each still require.
