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
- **The network**, for a program that calls `openPort()` (claude.md
  #151) — the one genuinely new external interface this language has,
  and the first that lets *remote, untrusted* input reach a compiled
  program at all (`environment`/the filesystem above are both local-
  attacker-only). A program that never calls `openPort()` gets none of
  this: `festina_runtime_http.c` (the HTTP/WebSocket implementation)
  is linked only when it's actually used, the same per-feature
  splitting graphics/audio already get (see *Slim binaries* below).
  claude.md #162 adds the reverse direction too: `req.send()` (zero
  arguments) makes a real *outbound* HTTP/HTTPS connection to whatever
  `req.url` names — a program that builds that URL (or the body/
  headers sent with it) from untrusted input has the same SSRF/
  data-exfiltration exposure any other language's own outbound-HTTP
  client would; this runtime does no allowlisting of hosts on its own.

This is a real, structural change from every other builtin: `req.url`/
`req.method`/`req.headers`/a WebSocket frame's own payload are the
*first* values in this language that originate entirely from an
untrusted, remote party by design, not merely something a local user
could feed a program that also happens to read stdin/argv. Concretely:

- **No TLS.** `openPort()` is plain HTTP/WebSocket, no certificate, no
  encryption — traffic is inspectable and modifiable by anything on the
  network path. A program handling anything sensitive needs a TLS-
  terminating reverse proxy in front of it; this is not, and does not
  claim to be, a hardened public-facing server on its own.
  `req.headers`/`req.toText()`/etc. are exactly as trustworthy as
  whatever sent them — this language does no authentication, does no
  input validation of its own, and never will (that's the PROGRAM'S
  job, using the ordinary building blocks — `regex`, `.replace()`,
  string comparison — every other kind of untrusted text already has).
- **Single-threaded, one request at a time** (see
  [api.md](api.md#http-and-websocket-servers)) — a slow or hung `on
  request`/`on message` handler denies service to every OTHER
  connection for as long as it runs. This is a real, structural
  availability property of the design (not a bug to be fixed later),
  and matters most for a program whose handler does slow work
  (a large `sqlite()` query, a big JSON render) in the request path.
  claude.md #163's non-blocking `req.send()` (a `callback`-carrying
  outbound request) and claude.md #165's non-blocking `blob` loading
  (`.callback()` on a text path expression) are both narrow, deliberate
  exceptions at the OS-process level — small background thread pools do
  the actual network/file I/O — but every piece of GENERATED FESTINA
  CODE, including either callback, still runs on that same single main
  thread; this bullet's own claim about `on request`/`on message`
  handlers is unaffected by either, and no Festina-visible
  global/refcount ever becomes something a program has to reason about
  concurrently. claude.md #166 lifts the earlier restriction against
  combining `openPort()` with graphics — a program that does both blocks
  in the graphics event loop, which now also services the open port, so
  a slow `on mouseDown`/`on request` handler denies service to
  EITHER side while it runs, not just its own; and Ctrl-C/SIGTERM on
  such a program skips the standalone server's own graceful-shutdown
  grace period entirely (see api.md's own [http
  limitations](api.md#http-limitations)) — a documented gap, not a
  silent one.
- **An 8MB per-connection buffer cap** (request line + headers + body,
  or one WebSocket frame's payload) bounds a single connection's own
  memory use, but this runtime does not limit the NUMBER of concurrent
  connections at all — many simultaneous connections, each near the
  cap, could still exhaust memory. No rate limiting of any kind exists;
  a program facing genuinely hostile traffic needs that in front of it
  (a reverse proxy, a firewall), the same way any other minimal server
  implementation would. claude.md #167's keep-alive means a connection
  can now legitimately stay open with no request in flight at all
  (waiting to be reused) — the cap above still applies to whatever a
  connection is actually buffering, and an idle keep-alive connection is
  closed automatically after ~15 seconds of nobody using it (see api.md's
  own Keep-alive section), so this doesn't add an unbounded-lifetime
  connection to the list above; it does mean a client that opens many
  connections and sends just enough on each to avoid the idle timeout
  could hold more connections open for longer than the previous
  one-request-then-close model ever allowed — the same reverse-proxy/
  firewall answer above still applies to a client doing that on purpose.
- **The request parser (HTTP/1.1 headers, WebSocket frames) is new,
  hand-written C parsing untrusted bytes** — the single largest new
  category of memory-unsafety risk this language has ever taken on,
  audited and stress-tested (ASan + LeakSanitizer, including abrupt-
  disconnect and malformed-input cases) but, unlike SQLite/Cairo/
  libjpeg/libmpg123 elsewhere in this runtime, not a widely-deployed,
  independently-hardened third-party implementation. Treat it with the
  same caution any new, from-scratch network-facing parser deserves.

Every other builtin's own external interface (filesystem,
`environment`, X11/ALSA) is unchanged: still local-attacker-only, still
covered by everything below.

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
cycle collection by trial deletion for the types that can form one
(claude.md #120) and `free`/`delete` as explicit overrides — built in
verified stages and
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
- **`free` is a decrement on every managed pointer type** — `struct`,
  `arr`, `map`, `blob`, and (since claude.md #118) `img`, `aud` and
  `regex` all carry the same refcount header, so a shared value
  survives a `free` through one binding and an alias never dangles. (A
  `text` binding always owns its buffer exclusively — copy-on-alias —
  so freeing it outright is equally safe.) Every runtime release is
  null-safe, so double-`free` through a binding is a no-op. This
  retired the one documented dangling-alias hazard the language had:
  before #118, `free` on an aliased `img`/`aud` freed outright and the
  alias dangled, as a stated manual contract.
- **One row-array chain shape leaks** (`rows()[0]` on a call-result
  array of query rows) — see [todo.md](todo.md#memory-model). A leak,
  never use-after-free; tests pin that distinction so an
  "optimization" cannot silently trade one for the other. Reference
  cycles, formerly on this list, are collected by trial deletion since
  claude.md #120 — a reachable cycle is provably restored intact
  (verified under ASan), so the collector cannot be tricked into
  freeing live data by a cycle that is still held.

## Slim binaries

A compiled program links only what it uses. The runtime is split into
core / graphics (Cairo, X11, libjpeg) / audio (ALSA, libmpg123) / http
(claude.md #151 — plain POSIX sockets on Linux/macOS, winsock2 on
Windows; no third-party library on any platform) / https (claude.md
#160 — `openSecurePort()` only, mbedTLS) translation units,
and the compiler puts a feature's object file and
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
- **A remote client could silently kill any `openPort()` program**
  (claude.md #151): `send()`/`write()` on a connection the peer has
  already reset or closed early raises `SIGPIPE`, whose default
  disposition terminates the *whole process* with no error message at
  all — trivially triggerable by any client that opens a connection
  and disconnects mid-response, and indistinguishable from a plain
  hang until traced. This is the most severe class of finding this
  runtime has (a genuinely remote, unauthenticated denial of service,
  one line of client behavior away), caught by an actual multi-request
  stress test rather than reasoned about in advance. Fixed with
  `signal(SIGPIPE, SIG_IGN)` at `openPort()`'s own entry point — every
  write already checks its own return value the POSIX way (`-1`,
  `errno == EPIPE`) wherever it matters, so the signal itself was pure
  noise once ignored, not something needing a handler. Windows never
  had this exposure in the first place: winsock2 has no `SIGPIPE` for
  a broken socket at all — `send()` just returns an error — so the
  Windows port (windows.md) needed no equivalent fix, only the same
  return-value check every platform already does.
- **Every `map[text]` this runtime ever built directly in C leaked its
  own values** (claude.md #167, found while verifying keep-alive under
  Valgrind, then confirmed pre-existing and unrelated to it): a request
  header, a socket's own `state`, a URL's own `searchParams` — every one
  of those maps' VALUES is owned, heap-allocated text, but all four were
  released through the generic, deliberately value-blind
  `festina_release_map` (correct for `map[int]`/`map[bool]`, wrong for
  `map[text]`) instead of the value-aware release codegen already
  generates for every Festina-visible `map[text]` variable. A leak, not
  a use-after-free or corruption — confirmed with a debug-symbol build
  under Valgrind, isolated from keep-alive by reproducing byte-for-byte
  on a single plain request against the code exactly as it stood before
  that entry. Fixed with `festina_release_text_map`, a C-side equivalent
  of codegen's own wrapper, used at all four sites.
