# Roadmap

Open work only, shortest useful form. The reasoning behind every closed
item lives in [decisions.md](decisions.md) (the numbered decision log,
cited as `claude.md #N` throughout the repository) and
[tests/CONTRACT.md](tests/CONTRACT.md) (what is verified, and how).

## Open bugs

Reported by [uraikus/archtelos-browser](https://github.com/uraikus/archtelos-browser)
— a browser engine written in Festina, whose `FINDINGS.md` and
`festina.md` record where the language ran out — and **reproduced here**
before being listed. Four of its reports were already fixed and are not
listed (mutual recursion across a file, a parameter or local shadowing a
function, and `\n` in a regex literal); three more are fixed in
decisions.md #326 (the `ascii` aliasing use-after-free, `==` on two
struct references, `ascii.toInt()`). The dropped query string is fixed
in decisions.md #330, which also fixed a `Host` header that omitted a
non-default port and a leak on a repeated query key, both found while
verifying it; the thirty-second read is fixed in #331, where the cause
turned out not to be the one reported; the quadratic cycle-collector
walk is fixed in #332, by `weak` fields — checked on read rather than
the uncounted raw pointer the report proposed, so they cannot dangle.
These are what is left, most damaging first.

- **`weak` is ported to `bootstrap/` only as far as the parser.**
  decisions.md #332 landed the feature in the shipped compiler and in
  `bootstrap/parser.f` (without which the AST dumps disagree), but not
  in `bootstrap/semantic.f` or `bootstrap/codegen.f` — so the
  self-hosted compiler parses a weak field and cannot compile one. No
  corpus file declares one, which is what keeps every harness green and
  is also why nothing measures the feature: a `cases/weak_fields.f` is
  the other half, and it cannot be added until the port is done or it
  would land as the corpus's first unported file.
- **A struct-typed field can never read as `null`.** A struct, array or
  map field is created empty the first time it is *reached*, including
  by `== null`, so `if node.next != null` is always true and
  `x.field = null` followed by `x.field == null` is `false`. Vivify on
  write and on member access, not on a null test. Every workaround for
  it is a parallel boolean or an id that is 0 when absent.
- **`'' == null` is `true`,** in a local, a struct field, an array
  element and a map value — a NUL-terminated `char *` with no header
  cannot tell them apart. It matters immediately for HTML, where
  `<input checked>` has an attribute whose value is the empty string.
  Either a static empty-string sentinel the runtime recognizes, or say
  so in the specification next to "`null` reads 0"; it is currently
  undocumented and surprising.
- **Compiled binaries target the host CPU's exact feature set.** On an
  AVX-512 machine every valgrind run dies with SIGILL before `main`,
  and valgrind is the tool that found two of the bugs above. A
  `FESTINA_TARGET_CPU=generic` escape hatch is what the reporter had to
  monkeypatch the compiler to get.

## Platforms

Linux is the primary, fully verified target. macOS and Windows builds
exist, compile, and type-check against real platform headers in CI —
see [macos.md](macos.md) and [windows.md](windows.md) for exactly
what's supported on each. What remains open:

- **Audio playback on a real Mac and a real Windows machine, and
  windowed mouse/keyboard/window behavior on a real Mac.** Each stays
  behind an explicit opt-in env var (`FESTINA_ENABLE_MACOS_AUDIO`/
  `_GRAPHICS`, `FESTINA_ENABLE_WINDOWS_AUDIO`) until confirmed on real
  hardware. Windows windowing needs no hardware: the CI job opens a
  real Win32 window and drives its mouse, keyboard, resize and close
  handlers itself (see [windows.md](windows.md)).

Compiling to `wasm32-wasi` is supported and CI-verified — see
[wasm.md](wasm.md) — and a compiled `.wasm` runs in a browser tab on
this project's own WASI host (`runtime/wasm/browser.html`, verified in
headless Chromium on every push). Graphics/audio are out of scope there
permanently (WASI has no backend for either), and so, it turns out, is
sanitizer coverage: `clang --target=wasm32-wasi -fsanitize=address` is
rejected by the compiler outright, and the wasm32 compiler-rt package
ships only `builtins` — no sanitizer runtime exists for the target.
Nothing this project can work around, and nothing it needs to: every
allocation the native sanitizer runs exercise is the same C source a
wasm build compiles, whose entire `__wasi__` delta is non-allocating
stubs. Nothing open here.

## Language & standard library

- **Media formats** stay PNG/JPEG + WAV/MP3, deliberately: each new
  format is a new system dependency for every machine that compiles a
  media-using program. Revisit only with a concrete need.
- **A raw byte-buffer type** — the *read* half of this shipped as
  `blob.byteAt(i)`/`blob.slice(a, b)` (claude.md #272), on the type that
  already is a file's bytes, rather than as a new primitive. What is
  still open is the **write** half: `[i] =` assignment into a mutable,
  indexable buffer, for binary protocol and data construction. Still
  unmotivated — nothing in the repository needs it, and the case that
  motivated the read half (a lexer carrying non-ASCII bytes through) is
  answered. `blob.slice` answering `text` rather than a blob is the
  deliberate part: a blob is a *file*, carrying its own path, so a slice
  of one has no path to carry.

### After the bootstrap port — now unblocked

Queued behind the self-hosting effort because each adds a *capability*
— new syntax, new runtime surface, or a new type — and every one would
have needed porting to `bootstrap/` a second time if it landed
mid-flight. **That target is reached** (decisions.md #313), so the
reason for the queue is gone. Each of these now costs its own
implementation plus a port of that implementation, which is the
ordinary price of a language change from here on.

A caution that applies to all six: `bootstrap/` is a second
implementation of the compiler, and a capability that changes codegen
has to land in both or the differential test goes red. That is the
point of the test, not a problem with it — but it does mean these are
larger than they look.

- **A native file picker.** The platform's own open/save dialog, so a
  program can ask for a path without inventing its own browser. Three
  backends (GTK/portal, Cocoa, Win32) behind one call, on the same
  opt-in-per-platform footing the graphics backends already use.
- **A `ContextMenu` API.** Right-click menus on a windowed program:
  items, separators, submenus, and a handler per item. Pairs with the
  existing mouse handlers rather than replacing them.
- **Clipboard.** Read and write the system clipboard, text first.
  Deliberately smaller than the other two: no format negotiation until
  something needs it.
- **A `vid` type, alongside `img` and `aud`.** Same shape as the media
  types that exist — a value that owns decoded frames, with the codec
  dependency compiled in *only when the type appears in the program*,
  exactly as `img`/`aud` already gate theirs. That conditional-linking
  property is the point: a program with no `vid` in it must not grow a
  video decoder.
- **A built-in test suite: a `test` type and `festina test`.** Named
  groups of assertions, run by a CLI verb of their own, so a Festina
  program can be tested without a second language in the loop.

  ```festina
  test basicMath = 'basic math test'
  basicMath(2 + 2, 4)
  basicMath(3 - 1, 2)
  basicMath(2 - 2, 4)

  test stringInterpolation = 'string interpolation'
  text name = 'Patrick'
  text greeting = `Hello, ${name}!`
  stringInterpolation(greeting, 'Hello, Patrick!')
  ```

  ```
  $ festina test ./test-example.f
  basic math test: 2 pass, 1 fail. 66%
   | - fail: basicMath(2-2, 4) // 0
  string interpolation: 1 pass. 100%
  Overall: 3 pass, 1 fail. 75%
  ```

  **`test` is a TYPE**, which is what makes this fit the language
  rather than bolt onto it. It joins `blob`/`img`/`aud`/`regex`/
  `ascii`/`color`/`font`/`http`/`url`/`socket`/`thread` in the type
  namespace — a list this language already extends by adding to — and
  the group's NAME is an ordinary binding, so nothing about `test` has
  to be contextual on what follows it. A `test` value being CALLABLE
  also has precedent: `func[T]:R` values already are (decisions.md
  #141), so the call syntax needs no new machinery, only a new callee
  type. And "methods on the type" is then the natural home for every
  assertion that isn't plain equality — `.throws()`, `.near()`,
  `.contains()` — instead of a growing set of global names.

  An earlier sketch made `test` both a block keyword and a callable.
  That is the version this replaces, and the reason is recorded rather
  than dropped: a name that is a declaration in one position and a call
  in another has no precedent here, and decisions.md #298 and #317 are
  two separate rounds of exactly that kind of name confusion already.

  What is still open:

  **Grouping is by the binding CALLED, not by position.** `basicMath(…)`
  belongs to `basicMath` wherever it appears, which is better than a
  block's brace-scoping — but it means a call can precede its own
  declaration, and it leaves open what happens to an assertion whose
  group was never declared (a compile error, presumably, since the
  callee would be an unknown name anyway).

  **What a `test` call ANSWERS.** Nothing (a statement), or a `bool` so
  a failing assertion can be branched on? The sketch only ever uses it
  as a statement, and `bool` is the choice that costs nothing and
  allows more.

  **What `test(actual, expected)` means for a non-scalar.** Scalars and
  `text` are obvious. `struct == struct` is currently *unsettled* — it
  emits invalid LLVM and nothing rejects it (see the entry below), and
  decisions.md #54's ambiguity rule is why neither identity nor deep
  equality was ever picked. A test assertion wants deep equality and
  would be the first thing in the language to need it, so this either
  forces that decision or restricts the call to types that already have
  `==`.

  **The failure line quotes the assertion's own SOURCE.** `basicMath(2-2,
  4)` appears spelled as it was written, not reconstructed from the AST
  — note the `2-2` against the `2 - 2` in the source. That needs the
  source span carried to wherever the report is produced, which today
  only error messages do. (The sample output writes `test(2-2, 4)` on
  that line, which is the older spelling; quoting the real callee is
  what the rest of the format implies.)

  **The percentages are truncated, not rounded** (2 of 3 is 66%, not
  67%), and a group with no failures omits the fail count entirely
  (`1 pass. 100%`, not `1 pass, 0 fail`). Both are worth pinning in
  tests, since both are the kind of detail a reimplementation gets
  subtly wrong.

  **The exit code is what makes it usable in CI**, and the sketch does
  not say what it is. Non-zero on any failure is the only answer that
  makes `festina test` usable in a pipeline.

  Also open: whether `festina test` runs the file's ordinary top-level
  code as well as its assertions (here it must — `name` and `greeting`
  are ordinary declarations between the test calls), and whether `test`
  bindings and their calls are stripped from a normal `festina compile`
  (they should be — a test in a shipped binary is dead weight, and that
  is a codegen change, which by the caution above means porting it
  twice).

- **Research a `gguf` type, for talking to a model directly.** The
  open questions are what the value actually owns (a memory-mapped
  file? a loaded context?), what the call surface is, and whether the
  dependency can be gated the way `vid`'s would be. Research first —
  the test suite above has open decisions of its own, but its shape is
  given; this one's is not.

## Memory model

Automatic reclamation is escape analysis plus reference counting.
Most managed types (`struct`/`arr[T]`/`map[T]`/`ascii`/`img`/`aud`/
`regex`/`blob`/`http`/`url`/`socket`/table rows) carry a refcount
header;
`text` is the exception — it has no header at all and is instead
copied on alias and freed outright — a live `text` pointer can be a
heap buffer, a bare `.rodata` literal, a borrowed environ pointer or an
X11 stack buffer, and a header would have to be valid for all four. Reference cycles are collected by trial
deletion, with `free`/`delete` as the manual override. What remains
open:

- **Cycle trials are synchronous and per-release** — every
  still-referenced release of a cycle-capable type walks the value's
  reachable subgraph. Fine for ordinary object graphs (20k dropped
  21-node *disjoint* cycles in ~34 ms) — but the case that number
  never tested, *shared* structure under repeated release-while-live
  churn, measures a real, cleanly linear cost specifically tied to
  sharing rather than to total node count:
  a shared ring costs ~9-10x a disjoint one at the same total node/
  iteration count, scaling linearly in both ring size and iteration
  count. The classic deferred-root buffer is the known optimization,
  now motivated by measurement rather than assumption. Still
  deliberately not started: the real algorithm needs the *free* path of
  every cyclic release wrapper to become buffering-aware too (a
  still-buffered node hitting refcount zero can't be freed immediately
  without leaving a dangling pointer in the pending-roots buffer) — new
  correctness-critical surface in code every struct/arr/map-using
  Festina program runs through, and batching still trades lower
  amortized CPU for higher peak memory (collection is delayed). Earns
  its own dedicated round: a fresh plan, and ASan/LeakSanitizer-under-
  stress verification of the deferred-free "zombie" path specifically.
- **Text globals are not freed at process exit** — deliberate: they are
  reachable until exit, LeakSanitizer agrees, and freeing them would be
  exit-time busywork.

## The bootstrap compiler

**The port is complete.** Every pass agrees with its original on every
file of the corpus, the bootstrap's own 122,000-line-of-IR source
included:

| | |
|---|---|
| lexer | 125 match, 0 differ |
| parser | 125 match, 0 differ, 0 unported |
| semantic | 125 match, 0 differ, 0 unported |
| escape analysis | 114 match, 0 differ, 0 unported — 2,232 of 2,232 records |
| codegen | 114 match, 0 differ, 0 unported — 420,357 of 420,357 IR lines |
| canaries | 147 registered, 0 missed — 140 caught, 7 via the ratchet |

All ten of the bootstrap's own files reproduce their own compilation
byte for byte, and the second-generation binary built from that IR is
identical to the first. The eleven files neither side compiles are
deliberately ill-formed sources the corpus keeps so that both
implementations are checked on the rejection too.

What is left below is not the port. An empty blocker table is a
statement about this corpus, not about the language: a construct no
file exercises is unmeasured however carefully both sides were
written, which is what `bootstrap/canary.py` exists to say out loud.

- **`bootstrap/lexer.f` does not follow Python's `repr()` into
  scientific notation.** The canonical token dump renders a float with
  Python's `str()`, which switches to exponent form below 1e-4 and at
  1e17 and above (`0.0000000001` prints as `1e-10`); the Festina lexer
  reproduces the trailing-zero half of `repr()` but keeps the source
  spelling otherwise, so it emits `0.0000000001`. No corpus file
  contains a literal outside the plain-decimal range, so the
  differential test has never seen it — found by adding
  `cases/float_bits.f` for the codegen port (decisions.md #290), whose
  small value is `0.0001` for exactly this reason.

  Fixing it properly means shortest-round-trip float formatting in
  Festina (Grisu/Ryū), which is its own piece of work. The alternative
  worth weighing first is changing what the dump renders: the IEEE-754
  bit pattern is exact, machine-independent, and something both sides
  can already produce — `bootstrap/codegen.f` has the encoder — but it
  would require the *reverse* conversion to be exact for every literal
  too, and the fast path there refuses values like
  `0.30000000000000004`.

- **A local that shadows a function name still reports badly inside a
  template literal.** The silent-miscompilation half of this is fixed
  (decisions.md #298): a shadowing local now reads as itself
  everywhere, because `_emit_expr`'s Identifier branch consults the
  scope chain before the program-wide function table. What remains is
  diagnostics. Interpolating a `func` value fails the whole compile
  with

      bootstrap/codegen.f:0:0: error: cannot interpolate a value of
      type func[text]:bool

  — no line, no column, and a type that appears nowhere in the
  statement at fault, so the only way to find it is to bisect the file.
  That message now only appears for a genuine attempt to interpolate a
  function, which is a real mistake worth reporting; it just has to say
  *where*. Two of this session's three shadowing hits presented as
  exactly this.

  Short helper names are what the bootstrap's own modules export
  (`at`, `esc`, `known`) and exactly what a code generator's locals
  want to be called, so this will keep coming up.

- **`struct == struct` emits invalid LLVM and fails to build.** The
  shipped compiler lowers it to `icmp eq i64 %ptr, %ptr`, which LLVM
  rejects: `'%t1' defined with type 'ptr' but expected 'i64'`. Two
  struct values reach the ordinary integer comparison, which never
  learns they were pointers. Reproduced on a four-line program:

      struct P { x:int }
      P a
      P b
      if a == b { log(1) }

  `festina/codegen.py` calls the construct "unsupported" in a comment
  -- it would have to mean identity or deep equality, and claude.md
  picks neither (#54's ambiguity rule) -- but nothing rejects it, so
  the error surfaces from LLVM rather than from the front end. Either
  answer is a decision rather than a fix: semantic.py could refuse it
  with a real message, or codegen could commit to identity and emit
  `icmp eq ptr`. Comparison against `null` already works and takes a
  branch of its own. Found while porting -- both implementations agree
  on the invalid IR, so this is the language's, not the port's
  (decisions.md #311).

- **`Math` is a namespace per method name, not per receiver.** With a
  variable called `Math` in scope, `Math.sqrt(9.0)` is still
  `llvm.sqrt.f64` and answers 3, while `Math.toText()` is that
  variable's own method. Nothing warns; the binding is simply ignored
  for any name in a Math table. Both implementations agree on it
  (decisions.md #306 reproduces it deliberately rather than tidying
  it), so this is a diagnostics question rather than a correctness one
  -- a shadowing declaration should say something.

## Deliberate behavior (documented, not planned work)

- **Array indexing is not bounds-checked** — a performance choice, see
  [api.md](api.md#indexing-is-not-bounds-checked).
- **`keyDown` auto-repeats while held** (that is how text entry works);
  a held key still fires exactly one `keyUp`. Track held keys yourself
  for edge-triggered input.
- **`regex(pattern, flags)` is memoized per call site** — the runtime
  compares the actual pattern+flags against the site's last
  compilation, so a repeated pattern costs what a literal does (~24x
  cheaper than recompiling) and a changed one recompiles. One site
  *alternating* patterns still recompiles per change — see
  [api.md](api.md#literals-are-compiled-once-regex-is-memoized-per-call-site).
