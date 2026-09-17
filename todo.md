# Roadmap

Open work only, shortest useful form. The reasoning behind every closed
item lives in [decisions.md](decisions.md) (the numbered decision log,
cited as `claude.md #N` throughout the repository) and
[tests/CONTRACT.md](tests/CONTRACT.md) (what is verified, and how).

## Reported bugs — all closed

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
the uncounted raw pointer the report proposed, so they cannot dangle;
the struct field that could never read as `null` is fixed in #333, by a
rule about terminal reads rather than the narrower one about null tests
that the report suggested, which would have left a list walk still
non-terminating; `'' == null` is fixed in #334, where the cause turned
out to be one line of the runtime's text comparison rather than the
representation the report suspected; and the host-CPU targeting is
fixed in #335, and in #336 the default flipped to portable with
`FESTINA_TARGET_CPU=native` as the opt-in. **Every bug from that report
is now closed, and nothing is open here.**


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
- **The `test` type's remaining methods.** `.throws()` and
  `.contains()` were named in the original sketch as the natural home
  for assertions that are not plain equality, and decisions.md #341
  shipped only `.near()` — the one that answers a real trap (exact float
  equality) rather than a convenience. `.throws()` needs a decision
  about what it takes: a `func[]:void` value is the obvious shape, and
  it interacts with `try`/`catch` in ways the equality assertions do
  not.

- **Deep equality, if an assertion ever needs it.** #341 restricted an
  assertion's arguments to types whose `==` is value equality, because a
  struct compares by identity (specification.md 8.9.1) and giving `test`
  a second meaning of equality would make it disagree with `==` on the
  same two values. Widening that restriction stays compatible; it needs
  its own answers for cycles, map ordering and NaN first, and nothing
  has asked for it yet.

- **A test build for `bootstrap/`.** The port of #341 covers what a
  corpus file can measure: the declaration parses and analyses the same
  on both sides, and an ordinary build removes every assertion. What has
  no harness is the EMITTING half, because `bootstrap/irdumpf.f`
  compiles the way `festina compile` does. Giving it a `festina test`
  mode of its own would put the group registration, the comparison per
  type, the rendered source line and the report under the same
  byte-exact differential as everything else — worth doing before the
  `test` type grows any further.

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

- **Text globals are not freed at process exit** — deliberate: they are
  reachable until exit, LeakSanitizer agrees, and freeing them would be
  exit-time busywork.

## The bootstrap compiler

**The port is complete.** Every pass agrees with its original on every
file of the corpus, the bootstrap's own 122,000-line-of-IR source
included:

| | |
|---|---|
| lexer | 130 match, 0 differ |
| parser | 130 match, 0 differ, 0 unported |
| semantic | 130 match, 0 differ, 0 unported |
| escape analysis | 117 match, 0 differ, 0 unported — 2,265 of 2,265 records |
| codegen | 117 match, 0 differ, 0 unported — 425,103 of 425,103 IR lines |
| canaries | 159 registered, 0 missed — 152 caught, 7 via the ratchet |

All ten of the bootstrap's own files reproduce their own compilation
byte for byte, and the second-generation binary built from that IR is
identical to the first. The thirteen files neither side compiles are
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
