# Changelog

All notable changes to Festina are tracked here, starting from version
0.1. Versions follow `major.minor` (a `major.minor.patch` form is used if
a patch-only release is ever needed); dates are in `YYYY-MM-DD`.

This changelog starts from the point version tracking was introduced —
it is not a reconstruction of the project's earlier history. The full
round-by-round design and implementation record predating 0.1 lives in
[decisions.md](decisions.md) (the numbered decision log, cited as
`claude.md #N` below and throughout the repository).

## [Unreleased]

### Changed

- **The language specification now lives in
  [specification.md](specification.md),** organized by topic in the
  manner of the ECMAScript standard — scope and conformance, lexical
  grammar, types, expressions, statements, declarations, execution and
  memory models, errors, the built-in database, the standard library,
  graphics, audio, HTTP, threads, compilation targets, and annexes for
  the grammar, reserved words, removed features, non-goals and the
  mapping from the original numbered sections. It consolidates the
  original specification and every later decision-log change into one
  current, normative document. The numbered decision log formerly
  named `claude.md` is now [decisions.md](decisions.md), unchanged in
  content and numbering; a citation of the form `claude.md #N` anywhere
  in the repository still means entry N of that file. `claude.md`
  itself is now a short set of working instructions for agents that
  points at the specification instead of containing it (decisions.md
  #277).
- **A language change is now written specification first, then tests,
  then code.** Anything that adds to or changes the language surface
  starts as a normative clause in specification.md, beside the clauses
  it interacts with; the tests are written against that clause and
  watched failing for the right reason; the implementation comes last.
  A bug fix, where the specification already says what should happen
  and the code disagrees, instead confirms the clause, adds the failing
  test and fixes — but an addition dressed as a fix follows the full
  order. Recorded in claude.md §2 (decisions.md #278).
- **Documentation describes the present, and only the present** — not
  what the project used to be, and not what it is going to be. Exactly
  three documents are exempt because recording time is their purpose:
  CHANGELOG.md and decisions.md for the past, todo.md for the future,
  plus Annex C of specification.md for removed features. Recorded in
  claude.md §2 (decisions.md #278).

### Added

- **Struct literals** — `User u = {'id': 1, 'name': 'Patrick'}`
  (specification.md §8.9.4). The same `{...}` builds a map or a struct
  depending on what the position expects, so a struct with a `map[T]`
  field reads naturally with the outer braces the struct and the inner
  ones the map. Fields left out keep their zero values, so a literal is
  always a complete fresh instance — assigning one replaces the whole
  value rather than updating the fields it names. Field names are
  written as string literals, because an unquoted name is a variable
  reference here exactly as it is in a map literal; writing
  `{name: 'Brad'}` reports what to do about it. Literals nest into
  struct-typed fields, `arr[Struct]` elements and `map[Struct]` values,
  and work at declarations and assignments — the two positions where
  the expected type is known. Arguments and `return` are deliberately
  not literal positions (Annex D).
- **`clear x`** — `free x` that overwrites the bytes with zero before
  releasing them, for a value whose contents should not outlive it
  (specification.md §10.11). The write goes through a volatile pointer
  so it cannot be optimized away as a store to dead memory. Zeroing
  follows the release cascade: clearing a struct wipes its own storage
  and every field released with it, and clearing an `arr[T]`/`map[T]`
  covers the elements released with it. It happens only where storage
  is actually released, so a value another binding still holds is
  neither freed nor zeroed (decisions.md #283, #284).

- **`bootstrap/semdump.py`** — the canonical dump the Festina port of
  semantic analysis will be checked against. Because `analyze()` is a
  checker rather than an annotator, diffing its return value would say
  nothing about the inside of a function body; the dump instead wraps
  `Scope.define` and records the resolved type of every name the
  program binds anywhere, including locals nested arbitrarily deep.
  3,175 records over 78 analyzed corpus files.
  `tests/test_bootstrap_semantic.py` pins its discriminating power
  (decisions.md #280).

- **`bootstrap/semantic.f`** — `festina/semantic.py` ported to Festina,
  the third step of bootstrapping the compiler in its own language.
  **all 93 corpus files produce the same dump as the Python analyzer,
  0 differ, 0 unported** — so all three stages of the front end now
  agree with their originals. It resolves declarations, merges imports
  in dependency order, walks thread bodies in their own isolated scope,
  descends into expressions, infers types, and rejects the programs the
  original rejects. The type checker is conservative by construction:
  anything it does not understand infers no type and is checked
  against nothing, so it can miss an error but never invent one.
  `bootstrap/semdiff.py` runs the comparison (decisions.md #281, #282,
  #285, #286).

- **`bootstrap/codegen.f`** — `festina/codegen.py` ported to Festina,
  the fourth and last stage, **in progress**: **13 of 100 corpus files
  emit byte-identical LLVM IR, 0 differ, 76 not yet ported, 11 rejected
  by both** — 1,554 of 180,340 file-specific IR lines. In are
  expressions (arithmetic and comparison with int/float mixing,
  `&&`/`||`, unary, the ternary, `/` and `%` with claude.md #57's
  divide-by-zero control flow, template literals, and `+` and `==`/`!=`
  on `text` including claude.md #243's in-place append), statements
  (`log`, assignment, postfix, `if`/`else`, `while`, `for`, `return`),
  functions with parameters and locals, struct type definitions, scalar
  and `text` globals and locals, `arr[T]`/`map[T]`/struct globals,
  struct field reads and writes including claude.md #97's
  lazily-created field storage, and imports merged before either stage
  runs. Not in: container and struct *locals* and `text` parameters
  (all of which need `festina/escape_analysis.py`, a fifth module),
  other non-scalar parameters and returns, method calls, indexing, and
  the graphics/audio/HTTP/thread/sqlite/table subsystems.
  `bootstrap/irdump.py` and `bootstrap/irdiff.py` run the comparison;
  the oracle is the IR text itself, so it needed no canonical form of
  its own (decisions.md #289–#296).

- **Every bootstrap differential harness now runs in CI** — on Linux
  only. They compare two implementations of the lexer, parser, analyzer
  and code generator against each other,
  which is compiler-development tooling rather than platform coverage,
  and nothing in them is platform-specific. Windows stops paying the
  roughly four minutes the lexer and parser harnesses cost it, which is
  what makes room for the semantic and codegen ones to run at all (on
  Linux the front-end three together cost about 15 seconds).
  `FESTINA_BOOTSTRAP_EVERYWHERE=1` runs them anywhere. The cheap pure-Python tests in the same modules
  keep running on every platform (decisions.md #287).

### Fixed

- **A local that shadowed a function's name silently read back as that
  function.** `codegen.py` consulted its flat, program-wide function
  table before the scope chain when resolving a plain name, so any
  local sharing a name with any function anywhere in the program stored
  to its own slot and then read the function's global symbol instead. A
  fifteen-line program passing a shadowing `map[int]` to a function
  printed 0 for a one-entry map; the same shape inside a larger program
  segfaulted in the runtime with a function pointer where a map header
  belonged. Nothing reported it: semantic analysis resolves the name
  correctly, so it type-checked, and a function symbol is a valid
  pointer, so the IR was valid. The scope chain is now consulted first,
  and passing a function by name where nothing shadows it is unaffected
  (decisions.md #298).

### Unchanged

- **`T?` keeps the meaning it has** — a self-managed binding, never
  retained on alias and never released by anyone but the program. The
  uniform "pointer to a cell" model, and a `view`/`alias` borrowing
  syntax considered alongside it, are both decided against; no
  specification clause and no code changed. The bootstrap ports turn
  out to use no `T?`, `free` or `delete` at all, so nothing was waiting
  on this (decisions.md #279).

## [0.44] - 2026-09-02

### Removed

- **The `\0` string escape.** A `text` is NUL-terminated and cannot hold
  a NUL, so `'a\0b'` lexed to a three-character value the language could
  never represent and silently truncated to `'a'` — `.length` answered
  `1`. It is a compile error now, naming the truncation. `'a\\0b'` (an
  escaped backslash followed by an ordinary `0`) is unaffected.

### Added

- **`text.trim()`** — leading and trailing whitespace removed (space,
  tab, newline, carriage return, vertical tab, form feed). Byte-oriented
  and so safe on UTF-8: `'  café  '.trim()` is `'café'`.
- **`blob.byteAt(i)` and `blob.slice(start, end)`** — the read half of a
  byte buffer, on the type that already holds a file's bytes. `byteAt`
  is an O(1) raw byte (`0`–`255`, `null` out of range); `slice` is the
  half-open byte range as `text`, clamped rather than checked. Answering
  `text` and not another blob is deliberate: a blob carries the path it
  was loaded from, and a slice of one has no path of its own. Together
  they let a scanner read a UTF-8 file the compiler never has to
  validate first, which `ascii` cannot do.
- **`bootstrap/` — Festina's lexer and parser, written in Festina.**
  `bootstrap/lexer.f` reproduces `festina/lexer.py`'s token stream
  exactly; `bootstrap/difftest.py` and `tests/test_bootstrap_lexer.py`
  prove it by diffing both lexers over every `.f` file in the
  repository — 89 files, all matching. Nothing in the shipped compiler
  depends on it: this is the first step of self-hosting, and a real
  consumer that surfaced four concrete limits of the language itself
  (see `bootstrap/README.md` and claude.md #271/#272). Three are fixed
  above — `blob.byteAt`/`blob.slice` for reading non-ASCII source,
  `text.trim()`, and the rejected `\0` escape. The fourth (`int / int`
  promotes to float, so there is no integer midpoint to binary-search
  with) is #61's rule working as designed and is left alone. The
  differential test also found a bug neither lexer showed alone: a
  column is a *character* offset, and counting bytes misplaces the caret
  in every compile error on a line containing non-ASCII text.
  `bootstrap/parser.f` follows (claude.md #273/#275), checked the same
  way against a canonical AST dump: **all 89 corpus files parse to a
  byte-identical AST.** While the port was partial it stayed honest by
  construction — an unimplemented construct produces an `UNPORTED` node
  the harness counts separately, never as a match — and that machinery
  is kept so the next construct the grammar grows announces itself
  rather than mis-parsing.
- **`ascii` — a one-byte-per-character string type,** alongside `text`
  rather than replacing it. Because a character is a byte, the
  character count *is* the byte count, so it lives in the value's own
  header and `.length`, `s[i]` and `charCodeAt(i)` are all O(1) reads
  instead of the UTF-8 walks `text` needs. Indexing allocates nothing
  at all — a one-character `ascii` comes from a table of 128 immortal
  singletons. Supports `.length`, `s[i]`, `.charCodeAt(i)`,
  `.slice(start, end)`, `+`, `==`/`!=`, interpolation, `.toText()` and
  `text.toAscii()`. A quoted literal assigned to an `ascii` is
  converted at compile time, so a non-ASCII literal fails to build;
  `toAscii()` answers `null` at runtime for text that isn't
  representable. Measured on a character-by-character scan: `text` is
  quadratic (50 ms at 10.4 KB, 201 ms at 20.8 KB, 800 ms at 41.6 KB),
  `ascii` linear (21.5 ms for 4.16 MB — a hundred times more input than
  `text` needed 800 ms for).

- **`pool.postMessage(x)` — no index — auto-selects an idle instance.**
  Routes to whichever pool instance currently has nothing queued and
  isn't mid-handler, falling back to plain round-robin when every
  instance is busy rather than ever blocking the caller. `.callback(fn)`
  chains onto it exactly as it does on an indexed `pool[i].postMessage`.
- **`pool.giveRequest(r)` — no index — same auto-selection as
  `postMessage`.** Hands the live connection to whichever instance is
  idle right now, falling back to round-robin, exactly like bare
  `postMessage` above.
- **`on request use NAME`.** Sugar for
  `on request(req:http?) { NAME.giveRequest(req) }` — `NAME` can be a
  singleton thread or a pool (in which case the bare, auto-selecting
  `giveRequest` above applies). Desugars entirely at parse time; `use`
  is not a reserved word anywhere else.
  Every other pool method still requires an index.
- **`drawImage` accepts an `img?` source.** Every form of the canvas
  `drawImage(...)` and of `img.drawImage(...)` takes a manually-managed
  `img?` where it says `img` — compositing only reads the source for
  the duration of the call and keeps no reference, so nothing changes
  hands. A layer painted by a worker thread can now be drawn straight
  onto the canvas with no `clip()` copy first. This is the one
  read-only exception to `T?` being a distinct type; assignment and
  every other call site are unchanged.
- **`req.send()` reuses a keep-alive connection to the same host:port**
  instead of opening a fresh one every call (plain `http://`, POSIX
  only). One small connection cache per OS thread, no locking needed;
  entirely transparent otherwise, including a dead reused connection
  being silently replaced rather than surfaced as a request failure.
- **`text.charCodeAt(i)` and `int.toChar()`.** `charCodeAt` reads the
  Unicode scalar value of the `i`-th UTF-8 code point (not byte, and
  not a UTF-16 code unit the way JavaScript's own `charCodeAt`
  sometimes is — matching how `s[i]` already indexes by code point);
  `toChar` is the inverse, UTF-8 encoding a code point into a
  one-character `text`. Both follow `s[i]`'s own "test, don't fail"
  rule: an out-of-range/negative `charCodeAt` index, or a `toChar`
  code point with no valid encoding (negative, above `0x10FFFF`, or in
  the UTF-16 surrogate range `0xD800`–`0xDFFF`), answers `null` rather
  than crashing.
- **`festina update`.** Pulls the latest source into this
  installation's own git checkout and fast-forwards to it (`git fetch`
  + `git merge --ff-only`) — there's no separate release pipeline, the
  running `festina` *is* this checkout. Refuses, with no changes made,
  on a dirty working tree, a detached `HEAD`, genuinely diverged
  history, or an installation that isn't a git checkout at all (e.g. a
  packaged binary) — it never force-resets over local work.
- **`text.length` and `blob.length`.** `text.length` is the number of
  UTF-8 code points (the same unit `s[i]`/`charCodeAt`/`split('')`
  already use, not bytes) — a real scan, since UTF-8 is variable-width.
  `blob.length` is the exact byte count, an O(1) stored-field read.
  Both read-only, like `arr[T].length`.
- **`match EXPR { 'Tag' { ... } ... default { ... } }`.** Exhaustiveness-
  checked dispatch on an enum's member (or any expression's own static
  type) — every tag string is the exact one `typeof` already returns,
  and leaving one uncovered with no `default` is a compile error naming
  it. Pure sugar: desugars entirely, at compile time, into the
  equivalent `typeof`/`if`/`else if` chain, so a compiled program pays
  nothing for it beyond what a hand-written chain already costs. The
  subject must be a plain variable or field access (not a call or any
  other expression that could run code), so it's only ever evaluated
  once regardless of arm count.
- **A disk-persisted cache for the lex/parse step of `festina
  compile`,** keyed by each imported file's exact content (not mtime)
  plus a hash of the compiler's own grammar — an unchanged file across
  two compiles is loaded from cache instead of re-lexed and re-parsed;
  changing one file's content invalidates only that file's own entry.
  Any cache failure (missing, corrupt, a festina upgrade) degrades
  silently to an ordinary fresh parse — correctness never depends on
  it working. `FESTINA_NO_PARSE_CACHE=1` disables it entirely.

### Fixed

- **`while (a || b) && c { }` and `if (a || b) && c { }` now parse.**
  Optional condition parens were implemented by eating a leading `(` and
  its match, which truncated any condition that merely *begins* with a
  parenthesised group — the condition ended at `)` and the parser then
  demanded the block at `&&`. Both spellings work now, and the grouped
  operand keeps its own precedence. Found by writing Festina's parser in
  Festina (claude.md #273/#274).

- **A JSON-parsed struct is now a valid member of its own enum.**
  `.toStruct(T)`/`.toArr(T)`, where `T` is one of an enum's members,
  built the struct without the type tag every other way of building one
  writes. A successful parse produced a value that crashed the moment it
  was used as its enum; a failing parse freed the half-built value at
  the wrong offset. Both are fixed; nothing changes for a struct that
  isn't an enum member.

- **A stray character in a source file reports a normal compile error.**
  Any character the lexer doesn't recognise — including an unterminated
  string, the most likely typo — used to print a Python stack trace
  instead of `file:line:col: error: ...`. An unterminated string and a
  `$` outside a template string now say which mistake they are.

- **A query row is reference counted, so every way of using one is now
  safe.** A row used to be a bare borrow into the array that owned it,
  so a row outliving its array was either a leak or a crash depending
  on the shape: returning one read out of a function's own local array
  was a use-after-free, and reading a column off a call-result array
  (`rows()[0].name`) leaked the whole array. Rows now carry the same
  refcount header every other managed type has, so binding, aliasing,
  passing, returning, storing in an `arr`/`map`, and `free` all behave
  exactly as they do for a struct. Rows still alias — `p.name = 'x'` is
  visible through every binding of that row, unchanged.

- **`.length` off a `blob`/`text`/`ascii` field no longer leaks the
  object it came from.** `make().someBlob.length`, and every shape like
  it — a struct field or a query-row column — kept the whole struct or
  row alive. Only the `arr[T]` case ever released it. Answers are
  unchanged everywhere; only what gets reclaimed afterwards changed.

- **A `throw` out of a `.sort()` comparator no longer leaks the sort's
  scratch buffer.** The comparator is ordinary Festina code, so it can
  throw, and the throw jumps straight past the runtime's own sorting
  frame — skipping that frame's `free()`. Festina-side locals were
  already released on the way out; this was the one piece of memory the
  runtime itself was still holding. `.forEach()` was audited too and
  never leaked (it allocates nothing), and a throw out of a timer or
  event handler cannot reach a `try` at all — it ends the program, as
  before. Error-path only; nothing changes on a sort that doesn't throw.

- **`T? x = <a fresh call>` now compiles for `blob` and `ascii`.** The
  fresh-construction escape hatch stripped the `?` off the declared
  type only for the manually-manageable dataclasses, missing
  `PrimitiveType` — which is `blob`'s category — so
  `blob? x = makeBlob()` was rejected while the structurally identical
  `Circle? c = makeCircle()` compiled. Broken since the escape hatch
  was introduced; `ascii?` inherited it. `T? x = <an existing plain
  binding>` stays rejected, unchanged.

- **A copied `headers` map no longer duplicates `Host`/
  `Content-Length`/`Connection`/`Transfer-Encoding` on the wire.**
  These four are always computed by this runtime itself; forwarding a
  real request's own `req.headers` into an outbound request, or a real
  response's own `headers` back out (`res.headers = upstream.headers`
  — a reverse proxy's most natural shape), used to append the
  caller's copy on top of the runtime's own, producing the same header
  name twice. A strict server (Go's `net/http`) hard-rejects a request
  with two `Host` lines outright.
- **`return <text-expr>` from a `blob`/`img`/`aud` func no longer leaks
  the intermediate text.** The implicit text-to-handle load conversion
  at a `return` site (`blob func f() { return `path${x}` }`) passed
  the fresh path text to `festina_blob_open`/`festina_load_image`/
  `festina_load_audio` — all three copy what they need internally —
  and never freed the original afterward. The ordinary `blob b =
  <text-expr>` declaration form was unaffected; only a `return` of a
  computed path was.

### Changed

- **`ascii.charCodeAt(i)` no longer costs a function call.** It compiles
  to a null check, a header load, a bounds check and a byte load emitted
  inline where the expression is used, so a character-by-character scan
  loop contains no call at all. The runtime function it replaced was
  deleted rather than kept unused. Behavior is unchanged in every case,
  including the `null` answers for a null receiver, a negative index and
  an index past the end. On the `char_scan` benchmark this took Festina
  from 24.8 ms to 13.6 ms — ahead of equivalent Rust (16.3 ms) and Go
  (14.8 ms) loops indexing raw bytes, where it had been ~1.7x behind
  both. `s[i]` still calls into the runtime: its result points into the
  immortal singleton table, and reaching that from emitted IR would mean
  hard-coding the C struct's layout.

- **Building a string one piece at a time is O(n), not O(n²).**
  `s = `${s}...`` and `s = s + ...` (any number of further pieces:
  literals, other variables, plain field reads) now grow `s`'s own
  buffer in place with a compiler-tracked length instead of copying
  the whole string into a fresh buffer on every step. The
  `string_concat` benchmark's 15,000 appends move a few kilobytes
  instead of ~112 MB. Nothing observable changes: the pattern is only
  taken when `s` is a plain text variable, the pieces cannot run user
  code, and `s` appears exactly once, at the front.
- **A `.wasm` that never touches a database is 31 KB, not 1.47 MB.**
  The wasm32-wasi link now uses link-time optimization across the
  program and the core runtime and strips the sysroot libc's debug
  sections. The vendored SQLite was kept alive only by `main()`'s
  closing `festina_db_close()` on a null handle — that call is no
  longer emitted for a program with no `table`/`sqlite()`, and the
  linker drops all of SQLite. A program that does use a database still
  gets all of it (about 1.1 MB). Smaller modules also load faster.
- **Solid shapes are drawn without a rasterizer.** An opaque
  flat-colour `drawRect`/`drawCircle`/`drawPixel` at an integer
  position (no `fillAlpha` below 1, gradient, border, scale or
  rotation) is written straight into the pixels, on the canvas and on
  an `img` alike; circles come from a per-radius coverage mask Cairo
  rasterizes once, blended with pixman's own arithmetic so the result
  is byte-identical to before. The canvas benchmark's 40,000-shape
  frame went from 35 ms to 7 ms; the layered-canvas benchmark's four
  `img?` layers from 84 ms to 8 ms on one thread. Everything outside
  that contract still goes through Cairo unchanged.
  `FESTINA_NO_DIRECT_FILL=1` switches the direct path off.
- **Fresh image surfaces are faulted in when created**, not one page
  at a time on first touch — the reason four threads painting four
  new `img?` layers ran no faster than one (first-touch page faults
  serialize across a process's threads). The layered benchmark's
  four-thread run went from 62 ms to 7 ms.
- `img.clip()`, `saveCanvas()`-to-`img` and `img.resize()` copy their
  source with Cairo's SOURCE operator instead of blending it OVER a
  transparent surface: the same pixels, a straight copy.
- The per-call colour form of `drawCircle` now produces exactly the
  same pixels as the `fillStyle()` form (it used to tessellate where
  the plain form stamped a cached mask, 1/255 apart on a few edge
  pixels at larger radii).

## [0.43] - 2026-09-02

### Added

- **A compiled `.wasm` runs in a browser tab.** `runtime/wasm/` ships
  a dependency-free WASI Preview 1 host of this project's own
  (`festina_wasi_browser.js`), a Web Worker that runs a program on it,
  and `browser.html?wasm=program.wasm`, the smallest page that does --
  stdout/stderr stream into the page and `window.festinaResult` holds
  the exit code, output and every file the program wrote to its
  in-memory sandbox. `node runtime/wasm/run_wasi_js.mjs` runs the same
  host outside a browser. Tested through the host under Node and in
  headless Chromium (files, directories, SQLite, timers, `argv`, exit
  codes). See wasm.md's "In a browser".
- **The Windows CI job drives a real window**: a new test finds the
  compiled program's Win32 window and posts mouse, keyboard, resize and
  close messages to it, asserting every input handler's output.
- **A browser-side wasm benchmark.** `benchmarks/run_wasm_browser_benchmarks.py`
  runs the five wasm.md programs, compiled from Festina, C and Go, inside
  headless Chromium on the project's own WASI host, timing compile,
  instantiate and run separately with `performance.now()` inside the
  worker. Results are in wasm.md's "In a browser: Festina vs C vs Go".

### Fixed

- **`try`/`catch`/`throw` works on macOS, and no longer crashes after
  a catch on Windows.** A `try` is a direct call to libc's own
  `setjmp` now and a `throw` is libc's `longjmp`, replacing LLVM's SjLj
  intrinsics -- which have no AArch64 lowering (macOS rejected `try`
  outright) and a broken x86_64 Windows one (the catch ran, then every
  local read as garbage). wasm32-wasi is the one target left without
  it (wasi-libc has no setjmp/longjmp). AddressSanitizer can instrument
  a `try`/`catch` program now, too.
- On Windows, a program whose only listening port belongs to a
  `thread` no longer exits the moment its top-level code finishes
  (`WSAPoll` rejects an empty fd set where POSIX `poll` sleeps).
- On Windows, `blob.append()` is an atomic append (`FILE_APPEND_DATA`),
  so two threads appending to one file no longer overwrite each other's
  bytes.
- The Windows CI job is green: the four remaining failures were tests
  assuming `/bin/sh`, `/bin/echo` or `apt` exist, fixed in the tests.
- **A `throw` no longer leaks the locals of the functions it unwinds
  through.** A function that merely called something which eventually
  threw -- no `try` or `throw` of its own -- used to skip its scope-exit
  cleanup entirely (the one documented leak of the try/throw
  mechanism). In a program containing a `try`, every managed local is
  now registered on the runtime's per-thread cleanup stack as it is
  bound, and every call site's owning argument temporaries for the
  duration of the call; a `throw` releases everything above the
  catching `try`, newest first. 0 bytes leaked under AddressSanitizer
  and Valgrind through three frames, every kind of local, a rethrow and
  a JSON failure. A program with no `try` is unchanged; one with a
  `try` pays about 8 ns per managed local binding.
- The JSON nesting cap is the parser's own (1000 levels) rather than a
  side effect of the cleanup stack's size: `JSON nested too deeply
  (more than 1000 levels)`.

See claude.md #235-#238.

## [0.42] - 2026-09-02

### Added

- **An `img` is now a self-contained drawing target** (uraikus/festina#93)
  -- three groups of methods mirroring the canvas calls name-for-name,
  each touching only the image it is called on (so they work from a
  worker thread too):
  - **A per-image transform and state stack:** `img.translate(dx, dy)`,
    `img.rotate(degrees)`, `img.scale(sx, sy)`, `img.resetTransform()`,
    `img.saveState()`, `img.restoreState()`. Identity from creation,
    independent of the canvas's transform, applied to everything drawn,
    cleared or composited onto that image. The stack holds the image's
    transform only; style state stays with the canvas's `saveState()`.
  - **Clearing to transparent:** `img.clear()`, `img.clearRect(x, y, w,
    h)`, `img.clearCircle(x, y, r)`, `img.clearPixel(x, y)` -- alpha 0,
    so a later draw underneath shows through. `clear()` ignores the
    transform like `clearCanvas()`; the region forms honour it.
  - **Compositing:** `img.drawImage(src, x, y)` and `img.drawImage(src,
    x, y, w, h)` -- through the destination's transform, honouring
    `fillAlpha`; drawing an image onto itself copies it first.

  A layer can now be painted in place -- a rotated brush stroke, an
  eraser, a tiled background, a swayed sprite stamp -- instead of being
  bounced through the canvas (`clearCanvas`, `drawImage` in, draw,
  `saveCanvas`, `clip`) at two window-sized copies per stamp.

### Fixed

- The per-call colour forms (`drawRect(..., color[, border])`,
  `drawCircle(..., color[, border])`, `drawPixel(..., color)`, canvas
  and `img` alike) no longer write the global fill/border state around
  each call -- a data race when a worker thread painted its own layer
  with them while main drew anything with a colour of its own. Same
  results, computed locally.

### Documentation

- api.md's Images table lists every `img` method (including the
  `getPixelColor` row it was missing and `drawCircle`'s colour forms),
  with a new "An image as a layer" section; `examples/layers.f` uses
  `.clear()` and a per-image transform for its HUD layer.

See claude.md #234.

## [0.41] - 2026-09-02

### Fixed

- **`.toStruct()`/`.toArr()` work again under `--target=wasm32-wasi`
  and on macOS, and their error path works again on Windows.** 0.36's
  partial-parse leak fix put a `setjmp` catch frame inside every
  generated JSON parsing function, which made any program that parses
  JSON a "uses `try`" program -- rejected outright on the two targets
  with no SjLj lowering, and silently broken on Windows (the catch
  never ran). The parsing functions now register what they are
  building on a per-thread *cleanup stack* in the runtime, which
  `throw` unwinds on its way to the catching `try`: plain portable C,
  no `setjmp`, and the per-call overhead 0.36 added is essentially
  gone (100k-object parse benchmark: median 233 -> 225 ms; 223 ms
  before 0.36).
- A duplicate `text` key whose second value fails to parse
  (`{"name":"a","name":5}` inside a `try`) no longer double-frees the
  first value.
- Trailing data after a complete JSON value (`'{"id":1} extra'`
  inside a `try`) no longer leaks the parsed value.
- A self-referencing struct nested pathologically deep (hundreds of
  thousands of `{"next":` levels -- reachable through `req.toStruct()`
  on a network body) now throws `JSON nested too deeply` instead of
  overflowing the C stack.
- **Windows: the HTTP runtime compiles again.** `<pthread.h>` was only
  included on POSIX while a mutex added in 0.25 used it on every
  platform; every `openPort()` program failed to build on Windows.
- Windows: `festina compile --target=wasm32-wasi tool.wasm.f` no
  longer names its output `tool.wasm.exe`.

### Changed

- CI on `main` had been failing on all three platforms; besides the
  fixes above, the Linux job now installs `x11-apps`/`x11-utils`
  (four real-pixel tests needed `xwd`/`xprop`), the Linux-only
  `/proc` check and the POSIX-signal tests skip cleanly on the
  platforms that lack them, and one test that asserted the value of a
  field read through a freed struct (undefined behavior) now asserts
  the documented contract, `c == null`.

### Documentation

- api.md: what `free` promises (the binding reads `null`) versus what
  it does not (a field read through the freed binding), the JSON
  cleanup-stack design and its Valgrind coverage, `drain()` in every
  Threads method list; wasm.md/macos.md: JSON parsing is unaffected by
  the `try` gate; stale runtime/todo/contract wording refreshed.

See claude.md #233.

## [0.40] - 2026-09-01

### Changed

- **`NAME.drain()`'s bookkeeping no longer costs the worker an extra
  mutex round trip per message.** The "finished dispatching" flag is
  now cleared inside the lock acquisition the worker already makes
  when it looks for its next message, and the wake-up broadcast is
  skipped entirely unless a `drain()` is actually waiting -- a
  program that never calls `drain()` pays one predictable branch per
  message and nothing else. Measured on a 400,000-message
  fire-and-forget program: min 208 -> 171 ms, median 300 -> 225 ms.

### Fixed

- `drain()` on a thread that stops while the call is blocked can no
  longer sleep forever -- `alive` is part of the wait predicate and a
  stopping thread wakes any waiter on its way out. (Not reachable
  from Festina code today, where only the main program can call
  either `drain()` or `kill()` and never concurrently; kept correct
  rather than relied on.)

### Documentation

- `drain()` waits for the thread's own side effects, not for a
  reply's `.callback(fn)` to run on main -- spelled out in api.md,
  with a test pinning the order.

See claude.md #232.

## [0.39] - 2026-09-01

### Added

- **`NAME.drain()`** blocks until a thread's own inbound queue is
  fully processed, then returns with the thread still running. The
  deliberate opposite of `kill()`'s own discard-don't-wait choice --
  exists specifically so `on close()`/`on exit(code:int)` can fire off
  a final `postMessage` (e.g. a database write on a thread with its
  own `DatabaseURL`) and be sure it landed before the teardown that
  follows a window closing or a graceful shutdown would otherwise
  discard it, unprocessed, exactly like `kill()` already does. Same
  main-only shape as `kill()`/`live()`/`isAlive()`, including
  `pool[i].drain()`. See [api.md](api.md#lifecycle-kill-live-isalive-drain).

See claude.md #231 (uraikus/festina#91).

## [0.38] - 2026-09-01

### Fixed

- **A thread's second-ever `.reply()` was silently dropped.** A worker's
  reply/`.callback()` mechanism delivered successfully only the first
  time, for that handle's entire process lifetime -- every reply after
  that from the same sender was silently discarded, no error. Root
  cause: removing the last entry from a sender's own pending-callback
  list left its tail pointer dangling, corrupting the very next
  registration (a real use-after-free write) so it became unreachable
  from the list's own head. Fixed by tracking the previous node
  explicitly during removal and correcting the tail pointer whenever
  the removed node was it. api.md's own "reply at most once per
  message" documentation needed no change -- it already described the
  intended behavior; the runtime just wasn't providing it.

See claude.md #230 (uraikus/festina#89, #90).

## [0.37] - 2026-09-01

### Documentation

- **api.md reorganized.** Three sections that had drifted far from
  their own topic (an artifact of being documented in whatever order
  they were built rather than the order a reader looks for them) moved
  to where they belong: "Single-value queries"/"JSON and full-text
  search" into `Built-in SQLite`, "Growing arrays"/"Sorting" into
  `Arrays`, and `Imports` up near the top, right after the compilation
  pipeline. No headings renamed, so every existing anchor (including
  external links from this changelog) still resolves. Checked the
  whole file for stale language left over from this session's earlier
  removals/fixes -- none found.

See claude.md #229.

## [0.36] - 2026-09-01

### Fixed

- **`toStruct()`/`toArr()`'s partial-parse-failure leak.** A JSON value
  that failed to parse partway through being built -- a struct's third
  field turning out to be the wrong type, having already parsed the
  first two; an array's fourth element failing, having already
  collected three -- used to leak whatever was already built for that
  one call. Every generated from-JSON parsing function now installs its
  own local `try`/`catch` around its own build loop, and the
  `.toStruct()`/`.toArr()` call site does the same for its own cursor
  and receiver text. Verified leak-free under Valgrind across a flat
  struct, a nested struct field, an array, a `map[T]` field, a
  self-referencing struct, and malformed JSON syntax itself.

### Added

- `scripts/valgrind_stress.sh` + `tests/valgrind_stress/`: a permanent
  home for stress programs that use `try`/`throw`, which cannot run
  under the existing AddressSanitizer-based `scripts/leak_stress.sh` in
  this environment.

See claude.md #223.

## [0.35] - 2026-09-01

### Fixed

- **`.callback(fn)` now fires on MAIN's own OS thread for a send
  addressed to main.** A worker's own bare `postMessage(x).callback(fn)`
  (always addressed to main) used to have `fn` fire back on the
  SENDING worker's own OS thread once main replied -- a real
  cross-thread-isolation hazard. It's now marshaled onto main, the
  same mechanism `blob`/`img`/`aud`'s own `.callback()` already uses.
  A worker messaging ANOTHER worker directly is unaffected -- `fn`
  still fires on the sending worker's own thread there.
- **A previously-latent data race in the async-io worker pool's own
  outstanding-job counter**, exposed by the fix above the first time
  that path was ever exercised from a thread other than main.

See claude.md #222.

## [0.34] - 2026-09-01

### Removed

- **`exec(args, callback)`** -- the non-blocking form. `exec(args)`
  (blocking) is unaffected. The callback always ran on main's own OS
  thread regardless of which thread dispatched it -- a real cross-
  thread-isolation hazard for a language whose whole thread story is
  "no shared mutable state to race on" -- so it's gone rather than
  documented around.

See claude.md #221.

## [0.33] - 2026-09-01

### Added

- **`thread NAME[] { ... }`** -- empty brackets, no literal size --
  sizes the pool itself: `os.cpu_count()` (read on the machine
  compiling the program) minus every other thread the program
  declares, floored at 1. `thread NAME { ... }` (no brackets at all)
  is unchanged, still a singleton. Two auto-sized pools in the same
  program each get the full remaining budget rather than splitting it.
  See [api.md](api.md#thread-pools-thread-namen).

See claude.md #220.

## [0.32] - 2026-09-01

### Removed

- **`sqliteInt()`, `sqliteFloat()`, and `sqliteText()`.** `sqlite()`
  itself, `table`, and `DatabaseURL` are unaffected. These three
  existed to read a single value (like a `count(*)`) without declaring
  a `table` (which creates a real table) just to hold it -- but a
  `struct` used as a query target creates no real table either, so
  `arr[SomeStruct] x = sqlite('SELECT count(*) AS n FROM ...')` already
  gives the identical schema-free round trip through `sqlite()`'s own
  one path. Calling any of the three now fails with the same
  "no such function" error any other unrecognized name gets.

See claude.md #219.

## [0.31] - 2026-09-01

### Fixed

- **An out-of-range thread-pool index no longer registers a dead
  callback.** `pool[99].postMessage(x).callback(fn)` registered `fn`
  before the bounds check, so it could never fire and its slot was
  never reclaimed. The whole expression is a clean no-op now.
- **`.reply()` with no message in flight** (a second reply to one
  message, or a `thread` value stashed in a struct/array/map and
  replied to later) silently dropped the reply and leaked its payload.
  It now releases the payload and delivers nothing. Every other path
  that can drop a reply -- including one still queued when its target
  is killed -- releases it correctly too.
- **Error messages naming a thread type** printed the compiler's own
  internal repr (`ThreadType(None)`) instead of `thread`. `log()` and
  template interpolation of a thread value now give the same specific
  "has no text form" error `img`/`aud` already did, and the "thread has
  no field X" error no longer suggests a method that doesn't exist on a
  thread value.
- A broken intra-document link in api.md (`#http--websocket-servers`).

### Changed

- **Thread messaging is faster.** The pending-callback list appends
  instead of prepending, turning the ordinary in-order reply case from
  O(N^2) into an O(1) lookup (measured on the identical program built
  both ways: 0.06s -> 0.04s at 5,000 in-flight sends, 0.92s -> 0.47s at
  20,000), and a worker now takes its inbound mutex once per message
  instead of twice.
- **api.md's Threads section reorganized** into ten subsections with
  working cross-references, plus newly documented behavior: reply at
  most once per message, `kill()` drops pending callbacks, a thread
  value has no text form, and a pool compiles its body once per
  instance (a compiled-size cost worth knowing before picking a large
  `N`).

See claude.md #218.

## [0.30] - 2026-09-01

### Added

- **`t.reply(response)` / `NAME.postMessage(x).callback(fn)`** -- a
  general request/response mechanism on top of thread messaging. A
  thread's (or main's) reply type is fixed by its first `.reply(...)`
  call; every `postMessage(x)` call site targeting a receiver with a
  reply type must chain `.callback(fn)` to receive it, or it's a
  compile error. `.reply()` never triggers `on message` on the
  receiving end -- it's a separate delivery path straight to `fn`,
  which runs back on whichever OS thread originally sent the message.

### Fixed

- **`festina_thread_kill`'s leftover-message cleanup was type-confused**
  for a queued `giveRequest` hand-off -- it called the wrong release
  function on the wrong struct shape. Now kind-aware; a killed
  thread's own pending `.callback(fn)` registrations are also freed
  (never invoked) so a later `live()` respawn can't inherit stale ones.

See claude.md #217.

## [0.29] - 2026-09-01

### Changed

- **`worker:thread` (the `on message(worker:thread, msg:T)` parameter)
  is never `null` any more.** When main is the sender, `worker` is now a
  real, singleton `thread` value -- check the new `.main:bool` field to
  tell it apart from an ordinary worker's own handle. Comparing a
  `thread` value against `null` is now a compile error naming `.main`
  as the replacement. This is Phase 1 of a larger messaging redesign;
  `.reply()`/`.callback()` and the removal of `sqlite()` are separate,
  later changes.

See claude.md #216.

## [0.28] - 2026-09-01

### Added

- **`examples/threaded_http_server.f`** and **`benchmarks/http_threaded/`**
  -- a real example and a `wrk`-based benchmark showing `thread pool[N]`
  + `NAME.giveRequest(r)` computing genuine per-request CPU-bound work
  across more than one OS thread at once, with a single-threaded
  baseline for comparison. See benchmark.md's new "HTTP:
  single-threaded vs. thread pool" section for measured numbers.

### Fixed

- **A `thread pool[N]` can no longer declare its own `DatabaseURL`.**
  Every instance in a pool shares one declared body, so this would have
  meant `N` independent, uncoordinated sqlite connections into the
  identical literal file at once -- now a compile error naming the fix.

See claude.md #215.

## [0.27] - 2026-09-01

### Changed

- **Docs/tests/release consolidation for the `thread` extensions
  plan** (thread pools, thread-private functions, wider builtin
  access, private per-thread HTTP contexts, live connection
  hand-off -- 0.22 through 0.26). Audited api.md's Threads/HTTP
  sections end to end; clarified the "Sendable types" paragraph's own
  wording now that a live `http` request can be handed off (not
  cloned, not sent through `postMessage`) via `giveRequest`. Full,
  unfiltered `scripts/leak_stress.sh` and `scripts/thread_tsan_
  stress.sh` runs (every stress program, not just this plan's own
  additions) both clean.

See claude.md #214.

## [0.26] - 2026-09-01

### Added

- **Live connection hand-off: `NAME.giveRequest(r)`.** The main
  program, having accepted a live request on its own port, may hand
  it directly to a thread -- that thread's own `on request` fires for
  it, on the connection's own live socket. Legal only from main, only
  onto a thread that has declared its own `on request`, and only for
  a manually-managed `http?` value (reusing `T?` rather than a new
  compile-time move-checker). First cut: plain (non-TLS) connections
  only.

See claude.md #213 for the full design, including a real
ThreadSanitizer-caught data race found and fixed during this phase's
own verification.

## [0.25] - 2026-09-01

### Added

- **Private per-thread HTTP context.** A `thread { }` may now declare
  `on request(req:http)`/`on upgrade(s:socket)`/
  `on socketMessage(s:socket, msg:blob)`/`on socketClose(s:socket)` --
  the identical four handlers the main program's own top-level HTTP/
  WebSocket support already has -- and, once it has declared at least
  one, call `openPort()`/`closePort()`/`openSecurePort()`. This gives
  the thread a fully private connection table and listener set, never
  shared with the main program's own HTTP context or with any other
  thread's, so a program can serve real, concurrent traffic on more
  than one port from more than one OS thread with no coordination
  needed between them. The blocking http client form (`req.send()`
  with zero arguments) also works from inside a thread body, targeting
  any other context's port; a thread must never target its own
  listener from inside that same thread (a documented, structural
  deadlock, not a bug) -- see api.md's new "Per-thread HTTP context"
  section.

See claude.md #212 for the full design, including the `__thread`
conversion of `festina_runtime_http.c`'s own connection/listener/
handler state and a real leak this phase's own sanitizer verification
caught and fixed.

## [0.24] - 2026-09-01

### Added

- **Wider builtin access inside a `thread { }` body.** `regex()`,
  `mkdir()`, and `ls()` are now callable from inside a thread's own
  handlers and private funcs, alongside the blocking, 1-argument
  `exec(args)`. Each is safe with zero runtime changes: `regex()`'s
  memoization slot is a per-call-site codegen global, never shared
  between threads; `mkdir()`/`ls()` are thin, purely local POSIX
  wrappers; `exec(args)`'s `fork()`/`execvp()`/`waitpid()` only ever
  touches the calling thread. The non-blocking, 2-argument
  `exec(args, callback)` form stays rejected inside a thread body —
  its callback always runs on the main program's own OS thread
  regardless of which thread dispatched it, a genuine cross-thread
  isolation violation if allowed.

See claude.md #211 for the full design, including how this was
verified against the actual C runtime rather than assumed from names
alone.

## [0.23] - 2026-09-01

### Added

- **Thread-private helper functions.** A `func` declared directly in a
  `thread { }` body's own top level (a sibling of its state vars/
  `on load`/`on message`/`on exit`) is now callable from that one
  thread's own handlers and other private funcs, with direct read/
  write access to that thread's own state. Two private funcs may call
  each other regardless of declaration order. An ordinary top-level
  `func` remains completely uncallable from inside a thread body,
  unchanged. Each thread pool instance gets its own independent copy
  of every private func, closing over that one instance's own state.

See claude.md #210 for the full design.

## [0.22] - 2026-09-01

### Added

- **`thread NAME[N] { ... }` -- thread pools.** Declares `N` fully
  independent instances of the same thread body, each its own OS
  thread, private state, and inbound queue. Addressed with `NAME[i]`
  (any `int` expression) everywhere a singleton thread's own bare
  `NAME` would be used — `pool[i].postMessage(x)`/`.kill()`/
  `.live(callback)`/`.isAlive()` all work identically to the singleton
  form, just per-instance. An out-of-range index is a silent no-op,
  matching `NAME.isAlive()`'s own established "test, don't fail"
  convention rather than crashing or raising.

See claude.md #209 for the full design.

## [0.21] - 2026-09-01

### Changed

- **`thread` messaging is now a single, unified model** — replaces
  per-thread `NAME.onMessage(callback)` registration with one global
  top-level `on message(worker:thread, msg:T)` handler that declares
  its own message type directly. A thread's own inbound handler now
  uses the identical `(worker:thread, msg:T)` shape (previously
  `on message(p:T)`); `worker` identifies the sender and is `null`
  when the message was sent by the main program. Threads may now
  message each other directly via `NAME.postMessage(x)` from inside
  another thread's own body (lifecycle control — `kill()`/`live()`/
  `isAlive()` — remains main-program-only). **Breaking:** any program
  using `NAME.onMessage(...)` or a thread's old single-parameter
  `on message(p:T)` needs updating to the new form.
- **The top-level WebSocket frame handler is renamed from
  `on message(s:socket, msg:blob)` to `on socketMessage(s:socket,
  msg:blob)`** — frees the `on message` name for the unified messaging
  model above; behavior is unchanged, only the name. **Breaking:**
  update any `on message(s:socket, ...)` declaration to
  `on socketMessage`.

See claude.md #208 for the full design and migration notes.

## [0.20] - 2026-09-01

### Fixed

- **A `thread` with its own `DatabaseURL` now closes its private
  sqlite handle on `kill()`** — previously, a `kill()`/`live()` cycle
  reopened a fresh handle every time without closing the old one (a
  real, small, per-cycle leak: one `sqlite3*` plus an open fd).
  Confirmed via a real LeakSanitizer report before the fix and a clean
  one after (`tests/stress/thread_db_kill_live_churn.f`). See
  claude.md #207.

## [0.19] - 2026-09-01

### Fixed

- **`.toStruct(T)`/`.toArr(T)` now decode `\u` unicode escapes** —
  previously threw `"... not yet supported"` on any JSON string
  containing a `\uXXXX` escape, including a UTF-16 surrogate pair
  (astral-plane codepoints); raw, un-escaped UTF-8 bytes were always
  fine, but a producer that specifically `\u`-escaped non-ASCII text
  could never be parsed. Now decodes both BMP codepoints and surrogate
  pairs into their real UTF-8 encoding, with a clear, catchable throw
  for malformed input (an unpaired surrogate, invalid hex, a truncated
  escape). See claude.md #206.

## [0.18] - 2026-09-01

### Fixed

- **A real heap-use-after-free**: assigning an ordinary, automatically-
  managed struct value into a manually-managed (`T?`) enum binding
  (`enum Shape = Circle; Shape? shape; ...; shape = c` for an existing
  `Circle c`) compiled without error and left `shape` dangling once
  `c` went out of scope and its own automatic release freed it —
  `check_assignable`'s enum member-coercion rule never accounted for
  `manually_managed`. Now correctly rejected as a type mismatch; a
  *fresh* member value (`Shape? shape = makeCircle()`) is unaffected.
- A manually-managed `blob?`/`regex?` thread-message parameter's own
  method calls (`p.write(...)`, `p.test(...)`) inside `on message`
  could fail to compile, or compile to invalid LLVM IR — a handful of
  exact-equality type checks in codegen.py (predating `T?`) never
  accounted for the flag, and `regex`/`http`/`socket` were never
  taught that a manually-managed instance can now reach code paths an
  ordinary one never could. Fixed; see claude.md #205.

### Added

- Real per-type test coverage for a manually-managed value crossing a
  `thread` boundary — `arr[T]`/`map[T]`/`enum`/`img`/`blob`/`aud`/
  `regex`/`url` each get a dedicated compile-and-run round-trip proof
  (`tests/test_manually_managed.py::TestThreadReferenceSharingPerType`),
  alongside struct's own existing one.

## [0.17] - 2026-09-01

### Fixed

- A manually-managed (`T?`) declaration's own initializer can now be a
  **fresh construction** of the matching plain type — a `regex`/
  `arr[T]`/`map[T]` literal, a `regex()` call, or any function call at
  all (including a struct-returning factory function). Previously
  `regex? r = /pattern/`, `arr[int]? xs = [1, 2, 3]`, and
  `Circle? c = makeCircle()` were all compile errors, since a fresh
  literal or a call's own return value always infers as the plain,
  unflagged type and `T?`/`T` are genuinely non-interchangeable. Safe
  because a freshly-constructed value has no other binding referencing
  it yet — reading an *existing* plain binding into a `T?` position is
  still, correctly, rejected. See
  [api.md](api.md#t-manually-managed-values).

## [0.16] - 2026-09-01

### Added

- `postMessage`/`on message` now share the raw reference for a
  manually-managed (`T?`) value crossing a `thread` boundary, instead
  of deep-cloning it like every other value type — a mutation made on
  one side is visible on the other, since it's the identical
  underlying value, not a copy. Sound because nothing on either side's
  automatic bookkeeping ever touches a manually-managed value's
  refcount, so there is nothing for two threads to race on.
- A manually-managed parameter can now be `free`d — it was never
  "borrowed" the way an ordinary parameter is, since nothing
  auto-manages it on either side of a call.

See [claude.md #203](claude.md) for the full design and implementation
record.

## [0.15] - 2026-09-01

### Added

- `T?` — a trailing `?` after a type at a variable/parameter
  declaration opts that one binding out of automatic memory management
  entirely (no retain on alias, no release at scope exit or
  reassignment). `free`/`delete` work unchanged and become the *only*
  release it ever gets. A genuinely distinct type from `T` (mirroring
  `amor arr[T]`'s own relationship to plain `arr[T]`) — no implicit
  decay either direction. Applies to struct/`arr[T]`/`map[T]`/`enum`/
  `blob`/`img`/`aud`/`http`/`socket`/`url`/`regex`; accepted but inert
  on `int`/`float`/`bool`/`text`/`color`/`font`/`table`. `arr[T?]`, a
  `T?` struct field, a `T?` return type, and `const T? x` are all
  compile errors this round. See [api.md](api.md#t-manually-managed-values).

### Fixed

- A user-defined function with a manually-managed parameter
  (`func f(p:Circle?)`) was permanently uncallable — the call-site
  argument check re-derived the parameter's type without its own `?`,
  rejecting the one argument type that could ever match it.
- `blob?`/`img?`/`aud?` failed to parse at all, misrouted into the
  unrelated anonymous `.callback()` form.
- `.test()`/`.play()`/`.playLoop()`/`.stop()`/`.isPlaying()`/`.send()`/
  `.clip()`/`.resize()`/`.getPixelColor()`/`.save()`/`.saveCopy()` and
  the `text -> blob` coercion stopped recognizing a manually-managed
  receiver of the matching type, via several pre-existing exact-equality
  type checks never meant to distinguish more than one shape of blob/
  regex/img/aud/http/socket.
- A grammar-ambiguity disambiguation helper (`Circle? c` vs. a bare
  ternary statement) used an absolute token index where a relative
  offset was passed in, silently misrouting a `T?` declaration found
  anywhere but the very first statement of a file.

See [claude.md #202](claude.md) for the full design and implementation
record. Crossing a `thread` boundary with a manually-managed value
(`on message`/`postMessage`) is not yet supported — planned follow-up
work, not a permanent restriction.

## [0.14] - 2026-08-31

### Documentation

- Final consolidation pass closing out `thread NAME { ... }` (claude.md
  #195-#201): api.md's own "Threads" section no longer calls itself
  "an early phase" and now documents the thread-private-helper-function
  restriction it was missing; `todo.md` gains three previously-untracked,
  already-true open items (singleton threads, no thread-private helper
  functions, a thread's own sqlite handle not explicitly closed on
  `kill()`); `README.md`'s own stale top-level test count corrected.
  No functional changes. See [claude.md #201](claude.md).

## [0.13] - 2026-08-31

### Fixed

- Documented that a `thread`'s own `on exit(code:int)` always receives
  `code` `0` — including when torn down by main-thread death — never
  the process's own real exit code. Not a behavior change (this is
  what the runtime already did); found and clarified while verifying
  `thread` process-exit interaction end to end. See
  [api.md](api.md#threads) and [claude.md #200](claude.md).

## [0.12] - 2026-08-31

### Added

- A `thread`'s own first statement may be `DatabaseURL = '<literal>'`,
  giving it a private SQLite handle — never shared with the main
  program or any other thread — so it may call `sqlite()`/
  `sqliteInt()`/`sqliteFloat()`/`sqliteText()` (a thread that didn't
  declare one still may not). A compile-time check rejects two
  contexts (a thread and the main program, or two threads) that would
  resolve to the same database file, main program's own default
  included. See [api.md](api.md#threads) and
  [claude.md #199](claude.md).

### Fixed

- A real (if narrow) data race, found while building the above: the
  media-decoder registration in `main()`'s own prologue used to run
  after every declared `thread` was already spawned, and the literal-
  SQL prepared-statement cache (claude.md #113) had no synchronization
  at all — both harmless as long as only the main thread ever queried
  SQLite, which per-thread `DatabaseURL` is what first breaks. Fixed
  before either was ever reachable; verified race-free under
  ThreadSanitizer.

## [0.11] - 2026-08-31

### Added

- `thread`'s own message types widen to include `blob`/`img`/`aud`/
  `url` — each deep-cloned (never shared) across the boundary, the
  same guarantee `struct`/`arr[T]`/`map[T]`/`enum` already got.
  Drawing/clip/resize/pixel methods on an `img` value (e.g.
  `pic.drawRect(...)`) work from inside a thread body, verified
  race-free under ThreadSanitizer. See [api.md](api.md#threads) and
  [claude.md #198](claude.md).

## [0.10] - 2026-08-31

### Added

- `thread`'s own message types widen to `struct`/`arr[T]`/`map[T]`/
  `enum` — each deep-cloned (never shared) across the boundary,
  built recursively from any mix of `int`/`float`/`bool`/`text`/
  `color`/`font`. A self-referencing (cyclic) `struct`/`arr[T]`/
  `map[T]` type is rejected with a clear error, not a hang; `blob`/
  `img`/`aud`/`url` message types remain not yet implemented. See
  [api.md](api.md#threads) and [claude.md #197](claude.md).

### Fixed

- A fresh, with-initializer `enum`-typed local variable — `Choice c
  = someExpr` declared directly inside a loop or function body, as
  opposed to reassigning an already-declared one — was never freed
  at scope exit, leaking its own box (and, for a boxed `text`
  member, that buffer too) on every declaration. The identical gap
  is fixed for `http`/`socket`/`url`-typed locals, found by the same
  audit.
- `NAME.postMessage(x)`, when `x` coerces into a compound message
  type (e.g. a `text` literal posted against an `enum` inbound
  type), no longer frees the coerced result with the wrong release
  function.

## [0.9] - 2026-08-31

### Added

- `thread NAME { ... }`: an isolated background worker with its own
  OS thread, its own private state, and message queues to and from
  the main program. `on load()`/`on message(p:T)`/`on exit(code:int)`
  handlers; `NAME.postMessage(x)`/`NAME.onMessage(callback)` for
  message passing (`int`/`float`/`bool`/`text` today); `NAME.kill()`/
  `NAME.live(callback)`/`NAME.isAlive()` for lifecycle control. Every
  message crossing the boundary is a deep, independent copy — no two
  threads ever share a mutable value. A thread's own body can see
  only its own state/locals, function names, and type names, never a
  global variable/constant or an ordinary top-level function call.
  See [api.md](api.md#threads) and
  [claude.md #195](claude.md)/[#196](claude.md).

## [0.8] - 2026-08-31

### Security

- The HTTP server no longer hangs or aborts on two malformed inputs
  reachable from a single unauthenticated request: a chunked
  `chunk-size` near 2^64 (which overflowed size arithmetic into an
  infinite buffer-growth loop) and a WebSocket frame declaring a
  ~16-exabyte payload (which reached a failing `malloc` that aborted
  the process). Both are now rejected against the existing 8MB cap.
  The chunked-decoder fix also protects the `req.send()` client
  parsing a hostile server's response.
- `.toStruct()` no longer overflows the stack on deeply nested JSON in
  an unknown field (reachable via `req.toStruct()` on a network body);
  nesting past 1000 levels now throws the same catchable error every
  other malformed input does.

### Fixed

- A value assigned to a global (or otherwise escaping) only inside a
  `try` or `catch` body is no longer freed while still referenced —
  escape analysis didn't look inside try/catch bodies, so such a value
  was stack-allocated and reclaimed at scope exit (a use-after-free).
- A refcounted local (array, map, struct, text) declared before a
  `try` that throws is no longer double-freed. A throw caught in the
  same function freed that local, and the normal scope exit after the
  catch freed it again (glibc aborted with "double free detected").
- The two branches of a `?:` are now required to have the same type;
  a mismatch (e.g. `c ? 'text' : someBlob`) used to compile and render
  garbage at runtime. `null` is still allowed in either branch.
- `&&` and `||` now require bool operands, matching `if`/`while`
  conditions; `1 && 2` used to compile and print `null`.
- A `?:` with a `null` branch (`c ? 1 : null`, `c ? null : 7`) now
  compiles — it produced invalid IR or crashed the compiler before.
- Passing a text literal or template to an `img`/`blob`/`aud`
  parameter (`show(`sprite${n}.png`)`) no longer corrupts the heap or
  leaks the loaded handle — the argument coercion mishandled the
  freshly minted handle's ownership.
- An `arr[blob]`, `arr[img]`, or `arr[aud]` no longer leaks its
  elements when the array is released — element handles were freed as
  a plain buffer with no per-element release.
- `.push()`/`.unshift()`/`.indexOf()` of a path string into an
  `arr[blob]`/`arr[img]`/`arr[aud]` no longer leaks the loaded handle.
- Interpolating a freshly built container into a template
  (`` `${make()}` ``, `` `${[1,2,3]}` ``) no longer leaks the
  container — only the rendered text was being freed.

- Integer fields in `.toStruct()` keep full 64-bit precision. They
  were parsed through a `double`, silently corrupting any value past
  2^53 — and `INT64_MAX` in particular read back as `null`.
- A finite float equal to nearly `DBL_MAX` renders as its own value in
  JSON output instead of `null` (the NaN/Infinity guard used a literal
  slightly below `DBL_MAX`).
- `img.getPixelColor()` on a JPEG-loaded image returns the real color
  instead of `null` for every pixel (JPEG surfaces store no alpha
  channel; the reader had treated the unused byte as alpha 0).
- A malformed WAV file (sample-rate 0) is now a normal load failure
  instead of cascading into a shutdown of all other playing audio with
  a misleading "no audio device" error.
- Several catchable-error paths no longer leak: a failed `fetch`
  response (up to 8MB per failed request, once per retry), an invalid
  `parseURL` port (~5 allocations per call), and a corrupt JPEG
  decode (its decoded surface, via a `setjmp`-clobbered local).

- Long-running loops that declare locals (a struct, an array, any
  variable) no longer overflow the stack. Codegen emitted each
  `alloca` at its declaration site — inside the loop body — so every
  iteration permanently grew the stack until the function returned;
  a loop declaring a six-field struct segfaulted at roughly
  150,000–300,000 iterations with flat heap usage. Every static
  alloca is now hoisted to its function's entry block: one slot per
  declaration, reused each iteration (verified to 3,000,000
  iterations). Locals are still re-zeroed at their declaration site
  every iteration, so behavior is otherwise unchanged.

See [claude.md #191](claude.md) for the full diagnosis.

## [0.7] - 2026-08-29

### Changed

- `.toText()` JSON-style rendering (`log()`/template interpolation of a
  struct, table row, `arr[T]`, or `map[T]`, and explicit `.toText()`
  calls on any of them) is faster, with no change in output. Every
  compile-time-known literal the renderer appends (JSON punctuation, a
  struct field's own baked `"name":` key) now skips a runtime
  `strlen()` rescan of a length the compiler already knew, and string
  escaping now bulk-copies runs of bytes that don't need escaping
  instead of handling one byte at a time. Measured ~2.5x faster
  (~215ms → ~85ms median over 5 runs) on a 100,000-iteration
  text-heavy struct-rendering benchmark.

See [claude.md #190](claude.md) for the full measurement methodology.

## [0.6] - 2026-08-29

### Added

- `getPixelColor(x, y)`: reads one pixel back off the canvas as a
  `color` — `null` for a coordinate outside the canvas, or a fully
  transparent pixel. Correctly undoes Cairo's premultiplied alpha, so
  a pixel painted under `fillAlpha` reads back as the color that was
  actually painted, not one darkened by the alpha in effect at the
  time.
- `img.getPixelColor(x, y)`: the same, reading an `img`'s own surface.

### Fixed

- `color == null` (and `!=`) generated invalid LLVM IR and failed to
  compile at all, for any program that tried it — entirely
  independent of the above. `color` is an `i64`-shaped value, and the
  bare `null` literal was routed through the "null" *pointer* keyword
  unconditionally. Now resolves to `color`'s own existing `-1`/'none'
  sentinel, the same value an uninitialized `color` already reads as.

See [api.md](api.md#drawing-is-offscreen-render-puts-it-on-screen).

## [0.5] - 2026-08-27

### Added

- `Math.floorDiv(a, b)`: integer division rounding toward negative
  infinity (unlike `/`'s own truncate-toward-zero), for tile/grid
  calculations that previously needed `Math.floor(a / b)` spelled out
  by hand.
- `blankImage(w, h)`: a fresh, fully-transparent `img` at a given
  size, with no existing image or canvas needed to derive it from.
- `row.rowid`: a table row's own SQLite identity, read-only — only
  populated when the query's own SQL explicitly selects `rowid`.
- `drawRect`/`drawCircle` (and their `img`-method equivalents) accept
  a further optional trailing `borderColor` argument, after the fill
  color — overrides it for that one call only. `drawCircle` also
  gains the fill-only override it previously lacked entirely.

### Documentation

- Clarified that the canvas's own real alpha channel is only real
  off-screen — a transparent region reads back as opaque white once
  `render()` puts it on screen, even though the same content saved via
  `saveCanvas()` still carries its real alpha.

See [api.md](api.md#types) and
[api.md](api.md#drawing-is-offscreen-render-puts-it-on-screen).

## [0.4] - 2026-08-27

### Added

- `arr[T].sort(cmpFn)`: an in-place, stable, comparator-based sort —
  `cmpFn:func[T,T]:int`, JavaScript's/C `qsort()`'s own convention.
  See [api.md](api.md#sorting-sortcmpfn).
- `drawImage(img, x, y, w, h)`: scales the whole image to fit a `w`×`h`
  box, without mutating the source image the way `img.resize()` does.
- `drawImage(img, sx, sy, sw, sh, dx, dy, dw, dh)`: the full
  canvas-style form — a source rect cut out of the image, scaled into
  a destination rect. See
  [api.md](api.md#drawing-is-offscreen-render-puts-it-on-screen).
- `map[T].keys()`/`map[T].values()`: a plain, independent snapshot
  array (`arr[text]`/`arr[T]`), no callback needed — sidesteps
  `.forEach()`'s bare/no-closures callback restriction for the common
  "collect entries matching a condition" case.

## [0.3.1] - 2026-08-27

### Fixed

- `fillAlpha` had no effect on `drawImage` — two images drawn back to
  back under different `fillAlpha` values came out pixel-for-pixel
  identical. `drawImage` now respects it, blending the image into
  whatever's underneath exactly like every other draw call. See
  [api.md](api.md#gradients-and-transparency).

## [0.3] - 2026-08-27

### Added

- `on mouseWheelUp(x:int, y:int)`/`on mouseWheelDown(x:int, y:int)`:
  scroll wheel events, split by direction. Fixes a real pre-existing
  bug on X11/Linux where scrolling the wheel silently also fired a
  spurious `mouseDown`+`mouseUp` pair.
- `devicePixelRatio:float`: a read-only global reporting the display's
  pixel density (`1.0` normally, ~`2.0` on Retina/HiDPI).
- `showCursor()`/`hideCursor()`: toggle the mouse cursor's visibility
  over the canvas.
- Real right-click and middle-click support on macOS and Windows —
  previously only the left button worked on either platform.

### Changed

- **Breaking:** `on mouseDown`/`on mouseUp` now require a third
  argument, `button:int` — `on mouseDown(x:int, y:int, button:int)`.
  `1` = left, `2` = middle, `3` = right, `8` = back, `9` = forward.
  `on mouse` (continuous movement) is unaffected.

See [api.md](api.md#mouse-events) and
[api.md](api.md#drawing-is-offscreen-render-puts-it-on-screen).

## [0.2.3] - 2026-08-27

### Added

- `enterFullscreen()`/`exitFullscreen()`: toggle true OS fullscreen on
  the graphics window. See
  [api.md](api.md#drawing-is-offscreen-render-puts-it-on-screen).

### Changed

- The graphics window is now fully decorated — a title bar and the
  OS's normal minimize/maximize/close controls, resizable by dragging
  an edge — instead of the previous borderless, canvas-only look.

## [0.2.2] - 2026-08-27

### Fixed

- A program calling `setClientWidth`/`setClientHeight` near the top of
  its own boot sequence briefly opened a real, on-screen window at the
  hardcoded 800×600 default before correcting itself, since the window
  used to open (and its size reset back to that default) before the
  program's own top-level code ever ran. The window now opens lazily,
  after any such call has already taken effect, directly at the
  requested size. See [api.md](api.md#graphics).

## [0.2.1] - 2026-08-26

### Fixed

- The macOS and Windows windowing backends could present stale or blank
  pixel content — most visibly, `img.clip()` (including the
  `saveCanvas().clip(...)` idiom) reliably showing the clipped region
  only on its first use in a process. Both backends now flush the
  surface before reading its pixels directly, as Cairo's own API
  requires.
- Assigning a field on a manually-declared table row (not obtained from
  a query) segfaulted. A table row is a borrowed handle onto one row of
  a query result, not an independently constructible value — declaring
  one with no initializer is now a clear compile-time error, pointing
  at `struct` as the way to build a value by hand
  (see [api.md](api.md#structs-as-query-targets)).

### Documentation

- Clarified that `on ...` event handlers are active as soon as they're
  declared, regardless of position in the file — the same hoisting
  `func` declarations already get. See
  [api.md](api.md#graphics).

## [0.2] - 2026-08-26

### Added

- `exec(args, callback)`: a non-blocking counterpart to `exec(args)`.
  Dispatches the same spawn to a background worker thread and returns
  immediately; `callback:func[int]:void` receives the real exit code once
  the child process exits. See [api.md](api.md#running-other-programs).

## [0.1] - 2026-08-26

### Added

- Version tracking: `festina.__version__`, and `festina --version` on the
  CLI.
- This changelog.

### Changed

- Project documentation (`README.md`, `api.md`, and every other `.md`
  file except `claude.md` and `tests/CONTRACT.md`) rewritten to describe
  the software as it stands today, rather than narrating how it got
  there.
