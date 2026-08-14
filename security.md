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
- **Known, accepted memory-management gap (not a vulnerability):**
  arrays and struct storage are heap-allocated and never freed —
  `claude.md #43` promises automatic memory management this compiler
  doesn't implement yet (no GC, no refcounting). This is a resource leak
  in a long-running process, not a safety issue (no use-after-free, no
  double-free, since nothing is ever freed at all) — tracked as a known
  limitation, not something this document treats as a finding.
