# Security

Festina compiles to native executables and links a C runtime — the two
places a memory-safety or supply-chain issue would actually bite. This
page describes the current security posture: the attack surface a
compiled program has, the properties the implementation maintains, and
how they are verified. Historical audit narratives live in
[claude.md](claude.md) and [tests/CONTRACT.md](tests/CONTRACT.md); this
page is the state of the product.

## Reporting a vulnerability

Open an issue on the [repository](https://github.com/uraikus/festina)
describing the problem and, if possible, a minimal Festina program (or
runtime input) that reproduces it. There is no dedicated security
contact or embargoed-disclosure program; the project is young, and that
process will grow if it needs to.

## Attack surface of a compiled program

A Festina program's external interfaces are exactly:

- **The local filesystem** — its SQLite database (`festina.sqlite`, or
  `DatabaseURL`), and any path a `blob`/`img`/`aud` is declared from or
  `save`d to. Paths are ordinary text expressions; a program that lets
  untrusted input choose them has the same exposure as any native
  program doing the same.
- **The process environment** — `environment.NAME` is a read-only
  `getenv()` wrapper with no parsing of the result.
- **The local X server and ALSA device**, only for programs that
  actually use graphics or audio — and only those programs link the
  libraries at all (see *Slim binaries* below).

There is **no networking**. Until HTTP support exists (see
[todo.md](todo.md)), no Festina program can be reached by, or reach,
remote input. That single fact narrows most of what "attack surface"
means here.

## Standing properties

Verified properties of the implementation, with the reasoning that makes
each structural rather than incidental:

- **No SQL injection via identifiers.** Table and column names are
  embedded into generated SQL (SQL cannot parameterize identifiers),
  but the lexer's `IDENT` grammar (`[A-Za-z_][A-Za-z0-9_]*`) cannot
  produce a quote, semicolon, or any other metacharacter — there is no
  Festina syntax that could smuggle a payload. Query **values** are
  always parameterized through `sqlite3_bind_*`, including the byte
  buffers of `blob`/`img`/`aud` parameters.
- **No format-string vulnerabilities.** Untrusted text flows into
  `printf`-family calls throughout the runtime (`log()`, `fail()`,
  error messages), and every one passes it as a `%s` *argument*, never
  as the format string.
- **No hand-rolled deserialization.** Query results are read through
  SQLite's typed column API; media decoding is delegated to Cairo,
  libjpeg, and libmpg123 rather than reimplemented.
- **Regex is POSIX ERE from libc** (`<regex.h>`) — no bundled engine to
  maintain, and pattern compilation failures are clean runtime errors.
- **Database durability model**: the database opens in WAL mode with
  `synchronous=NORMAL` (claude.md #113) — transactions survive an
  application crash unconditionally; an OS crash or power loss can lose
  the most recent commits but can never corrupt the file. This is the
  standard application-embedded SQLite configuration.

## Memory safety

The memory model is escape analysis plus reference counting, with
`free`/`delete` as explicit overrides — built in verified stages and
continuously exercised under AddressSanitizer, LeakSanitizer and (for
the audio thread pool) ThreadSanitizer. `scripts/leak_stress.sh` runs
five mixed churn programs plus one isolation program per data type on
every test run, and a canary test proves the harness itself can fail.

The knowingly accepted gaps, none of which is remotely triggerable and
each of which is a **leak or a documented manual contract, not
corruption**:

- **Array indexing is not bounds-checked** — a deliberate performance
  choice, documented with its rationale in
  [api.md](api.md#indexing-is-not-bounds-checked). An out-of-range
  index in Festina source can read or write out of bounds in the
  compiled program. This is the one place where a bug in *your* Festina
  code has C-like consequences; the index is never attacker-supplied
  unless your program makes it so.
- **`free` on an aliased `img`/`aud` dangles the alias** — the manual
  contract, stated at the feature (claude.md #111): those two handle
  types have no refcount, so `free` frees outright. The freed *binding*
  reads null; a second binding does not. No other type has this hazard:
  refcounted values (`struct`, `arr`, `map`, `blob`) treat `free` as a
  decrement, so a shared value survives, and a `text` binding always
  owns its buffer exclusively (copy-on-alias). Every runtime release is
  null-safe, so double-`free` through a binding is a no-op.
- **Reference cycles and escaping `img`/`aud` handles leak** — see
  [todo.md](todo.md#memory-model). Leaks, never use-after-free; tests
  pin that distinction so an "optimization" cannot silently trade one
  for the other.

## Slim binaries

A compiled program links only what it uses. The runtime is split into
core / graphics (Cairo, X11, libjpeg) / audio (ALSA, libmpg123)
translation units, and the compiler puts a feature's object file and
libraries on the link line only when the program actually exercises it —
a `log('hello')` program links none of them. Fewer resident libraries is
a smaller patch surface for any deployment, independent of whether a
specific library has a known issue today. Regression-tested via `ldd`
on real compiled binaries for all four graphics/audio combinations
(`tests/test_codegen.py::TestSlimBinaries`).

## Notable fixed findings

Each was found by a directed audit or a real report, fixed, and pinned
by a regression test — kept here as a summary; the full narratives are
in claude.md and tests/CONTRACT.md:

- **Stack buffer overflow in schema sync** (high severity, not remotely
  reachable): `festina_sync_table` built SQL with an unchecked
  `snprintf` accumulation pattern; a sufficiently wide `table`
  declaration overflowed a stack buffer. Every accumulation step is now
  overflow-checked and an oversized schema fails loudly
  (`festina_check_sql_buffer`) instead of corrupting the stack;
  regression-tested with a pathologically wide table.
- **Query columns were matched by position** (claude.md #111): a
  partial or reordered `SELECT` silently misaligned values into the
  wrong columns — wrong *answers*, type confusion in program logic.
  Columns match by name now, case-insensitively.
- **Window-manager crash on startup** (`BadMatch` on `XSetInputFocus`):
  a race with the WM's reparenting killed any graphics program under a
  real desktop. Fixed with a narrowly scoped X error handler around
  that one call; reproduced and regression-tested under a real window
  manager (`openbox` over Xvfb).
- **A transient X connection failure killed graphics programs**:
  `XOpenDisplay` is now retried (10 × 100ms) so a busy machine's
  refused connection is not misreported as a missing display.
