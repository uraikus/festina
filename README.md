# Festina

**A fast, simple, JavaScript-inspired programming language that compiles to native code through LLVM.**

Festina is designed around a simple idea: keep the familiar parts of JavaScript, remove much of the dynamic overhead, and compile everything possible down to fast native code.

```festina
table People {
    id:int
    name:text
}

text func greet(name:text) {
    return `Hello, ${name}!`
}

log(greet('Festina'))
```

*(This exact example runs today — `festina.sqlite` and the `People`
table are created/synced automatically at startup, though this
particular snippet doesn't query it; `sqlite()` queries work too, see
[Built-in SQLite](#built-in-sqlite) and
[Implementation Status](#implementation-status).)*

## Implementation Status

Festina is under active development. Everything else in this README
describes the full target language from `claude.md`; this section is
the ground truth for what actually runs today.

**Test suite:** 349 tests, 0 failed (`pytest tests/`) — 342 passed/7
skipped by default (2 need `pyinstaller`, an opt-in build-time-only
dependency for the packaged-binary tests; 5 need `Xvfb`/`xdotool` to
open and interact with a real window for the graphics tests; see
[Setup](#setup)), or all 349 passed/0 skipped with those installed.
See [`tests/`](tests/) and [`tests/CONTRACT.md`](tests/CONTRACT.md) for
the spec-driven suite this is measured against — every test cites the
`claude.md` section it checks.

### Implemented and working end to end

Compiles, type-checks, generates real LLVM IR, links against a native C
runtime, and *runs* as a standalone executable (no Python or the
`festina` package needed at runtime):

| Area | Status |
|---|---|
| Lexer / parser | ✅ full grammar — imports, types, structs, tables, functions, events, control flow, template strings |
| **Multi-file compilation (`claude.md #5/#6`)** | ✅ `import file.f` pulls the whole dependency graph into one compilation unit — recursive resolution, canonical-path dedup, circular-import detection, cross-file struct/table/function/global references, correct per-file error attribution even though it's all one merged program internally |
| Semantic analysis | ✅ type resolution, struct/table distinction, bool-only conditions, function arg/return checking, all `#48` error categories |
| Primitives (`int` / `float` / `bool` / `text` / `blob`) | ✅ |
| Variables / constants | ✅ global and local |
| Functions | ✅ typed params, return values, `void`, structs returned by value |
| Control flow | ✅ `if`/`else`, ternary, `&&` / `||` (short-circuit), all operators |
| **Loops (`claude.md #60/#61`)** | ✅ `for init, cond, update { }` and `while cond { }`, including the loop-variable scoping rule and `while true` infinite loops — no `break`/`continue` (claude.md doesn't define either; `return` from the enclosing function is the only documented way out early) |
| **Postfix `++`/`--` (`claude.md #66`)** | ✅ mutable `int` variables only, compile-time-checked |
| String interpolation | ✅ `` `Hello ${name}` `` |
| `log()` / `fail()` | ✅ |
| **Regex, string match/replace (`claude.md #67/#68`)** | ✅ `regex()`, `.test()`, `.match()`, `.replace()`/`.replaceAll()` (search may be text or regex) — POSIX extended regular expressions (no bundled/external regex engine — see [Setup](#setup)); no capture groups, backreferences, or non-greedy quantifiers (POSIX ERE's own limits, not something worked around here) |
| **Graphics (`claude.md #37/#39/#40`)** | ✅ `img`/`loadImage()`, `drawRect`/`drawCircle`/`drawText`/`drawImage`, `on click`/`on mouse`/`on key`/`on resize`/`on close`, `clientWidth`/`clientHeight` — a real on-screen X11 window rendered via Cairo, verified against an actual virtual display, not just reasoned about (see [Graphics](#graphics) below for the caveats: canvas starts at 800×600, solid black only, PNG-only images) |
| Structs | ✅ declaration, field read/write, passed to and returned from functions |
| **Arrays (`arr[T]`)** | ✅ literals, indexed read/write by any index expression, nesting, `.length` (`claude.md #63`), as function params/return values, as struct-array elements — see the caveats below and in `festina/codegen.py`'s module docstring |
| **Numeric conversion (`claude.md #55/#56`)** | ✅ int and float never mix implicitly, in any operator (arithmetic *or* comparison) — `int.toFloat()` and `Math.floor/ceil/round/trunc(x)` are the only conversions, both compile-time-checked and runtime-tested |
| **Division/modulo by zero (`claude.md #57`)** | ✅ returns `null` instead of crashing the process, for both `int` and `float` |
| **Automatic SQLite schema sync** | ✅ `festina.sqlite` opens/creates itself; tables are created, and columns added/dropped/retyped with existing data preserved via a temp-table rebuild — the `claude.md #31` worked examples all pass as tests |
| **`sqlite()` queries (`claude.md #32-34`)** | ✅ `SELECT` into a declared `arr[Table]` (with field access on the resulting rows), parameterized `INSERT`/`UPDATE`/`DELETE`/`SELECT` via a literal params array, `NULL` columns round-trip as the same null sentinel used elsewhere — see the caveats below |
| Native executables | ✅ `bin/festina program.f -o program` produces a real, standalone binary |

### Deployment: real compilation, minimal setup

claude.md #59 requires minimizing the dependencies needed both to use
the compiler and to run a compiled program, preferring broader tool
compatibility over depending on one specific tool, and failing with a
clear, actionable error when a dependency really is missing. This
section tracks progress against that requirement — see [Setup](#setup)
below for the concrete, current dependency list.

Getting Festina closer to "one binary, nothing to install" the way Go
manages it (Go ships its own compiler *and* linker, statically links its
runtime, and mostly avoids libc — see the project discussion this table
tracks). Staged, since a full rewrite of the backend isn't realistic
right now:

| Stage | What | Status |
|---|---|---|
| 1. Static-link sqlite3 into compiled programs | A Festina program built here no longer needs `libsqlite3.so` on the machine that *runs* it (falls back to a normal dynamic link if no static archive is available in the build environment) | ✅ done |
| 2. Package the compiler frontend as a real binary (no separate Python install to *run the compiler*) | `scripts/package_compiler.sh` bundles `festina/` (plus a real Python interpreter) into a single standalone binary via PyInstaller — verified to compile and link a real program with `python`/`python3` shadowed by an always-failing command on `PATH`. Still needs a C compiler/linker at runtime (that part is stages 1/3/4, unaffected) — this only removes the Python dependency | ✅ done |
| 3. Drive libLLVM directly instead of shelling out to the `clang` binary | `festina/llvm_backend.py` compiles the generated LLVM IR to an object file in-process via libLLVM's C API (ctypes) — `clang` is no longer *specifically* required; `gcc` (or any working C compiler/linker) now works too, since the only thing left for it to do is compile `festina_runtime.c` and link plain object files. Falls back to the original clang-only pipeline automatically if libLLVM can't be loaded, so this is purely additive | ✅ done |
| 4. Embed LLD too, removing the last external dependency (a system linker) | Some C compiler/linker still has to be present to compile `festina_runtime.c` and link — that's a meaningfully smaller ask than clang/LLVM specifically, but not yet zero | not started |

Stages 2 and 4 don't remove anything end users of *compiled Festina
programs* depend on — they're about what it takes to install and run
the `festina` compiler itself. Verified concretely, not just reasoned
about: `gcc` genuinely can't handle a `.ll` file at all (it hands it to
`ld`, which treats it as a corrupt linker script and fails) — compiling
the IR ourselves is what actually broadens compiler compatibility, not
just a style preference. Stage 2 was verified the same way — actually
running the packaged binary with every `python`/`python3*` on `PATH`
replaced by a command that always fails (see
`tests/test_packaging.py`), not just checking that PyInstaller reports
success.

Known limitations, all deliberate per `claude.md #54`'s ambiguity rule
(unspecified stays unresolved rather than invented) or explicitly
scoped out rather than silently missing:

- Arrays aren't bounds-checked, don't grow, and their data is `malloc`'d
  and never freed — claude.md #43 promises automatic memory management
  this compiler doesn't implement yet (no GC, no refcounting). The same
  is true of struct storage, which is always heap-allocated (`calloc`)
  rather than stack-allocated, even for a struct local to one function —
  a stack-allocated struct's address can genuinely outlive its function
  (returned, stored in an array or another struct), which used to
  silently corrupt memory; see the "Struct storage is always
  heap-allocated" note in `festina/codegen.py`'s module docstring.
- No `break`/`continue` — claude.md #60/#61 define `for`/`while` but
  don't define either, so the only documented way out of a loop body
  early is `return` from the enclosing function (see
  `TestLoops.test_while_true_exits_via_return_inside_the_loop` in
  `tests/test_codegen.py` for a worked example). A `for` loop's update
  expression is evaluated as an arbitrary expression, not restricted to
  just `i++`/`i--` at the implementation level — claude.md #60 lists
  those as the valid forms but doesn't say the update clause can be
  *nothing else*, so this doesn't add a restriction beyond what's typed.
- `bool` has the same "no representable `null`" problem `int`/`float`
  used to (LLVM's `null` literal is only valid for pointer types, and
  `i1` has no spare bit pattern) — `bool x = null` still fails to
  compile. Not fixed: doing so means widening `bool`'s representation
  everywhere it's stored (fields, params, array elements), well beyond
  what fixing `int`/`float` needed.
- Every `arr[T]` lowers to one shared internal type, currently named
  `_FestinaArray` specifically to make an accidental collision with a
  same-named user struct unlikely — but Festina's identifier grammar
  still technically permits a user to write `struct _FestinaArray`, so
  this lowers the odds without eliminating the possibility.
- `drawRect`/`drawCircle`/`drawText`/`drawImage`/`loadImage`/`loadAudio`/
  `regex` aren't reserved words (unlike `log`/`fail`/`sqlite`, which are
  lexer keywords) — declaring a function with one of those names
  silently shadows the builtin at every call site rather than erroring.
  Left as is for the graphics/audio functions since none of them are
  implemented yet anyway; `regex` follows the same convention
  deliberately, matching how `loadImage`/`loadAudio` are already
  builtin *functions*, not dedicated keywords or literal syntax.
- `sqlite()`'s optional second argument (bound parameters) must be a
  literal array expression (e.g. `sqlite(sql, [1, 'Patrick'])`), not an
  arbitrary `arr[T]`-typed variable or expression — claude.md #33's own
  example is itself a heterogeneously-typed literal, which a real
  `arr[T]` *value* can't represent under Festina's normal (homogeneous)
  array typing, so the params list is special call syntax instead
  (each element bound individually, by its own type, at compile time).
  Passing anything else there is a clear compile-time error, not a
  runtime one. Query result columns map onto a declared table's fields
  *by position*, not by name, matching claude.md #34's own `SELECT *`
  example.
- `regex(pattern)` compiles the pattern (via POSIX `regcomp()`) fresh at
  every call site — there's no caching by pattern text, so a `regex()`
  call inside a loop recompiles every iteration. Same tradeoff already
  accepted for `sqlite()`'s prepared statements (also re-prepared per
  call, not cached); worth revisiting if it matters for a real program,
  but not solved here. An invalid pattern is a runtime error (`fail()`,
  with the underlying `regcomp()` error message), not a compile-time
  one — claude.md #67 says so explicitly, since the Python compiler
  doesn't parse regex syntax itself.

### Not implemented yet

| Area | Status |
|---|---|
| Audio (`aud`, `loadAudio`) | ❌ |

### Setup

Three different dependency lists, and they're not the same size — this
is the practical payoff of the staged plan above.

**To *use* the compiler from a checkout** (`bin/festina program.f`) on a
fresh system:

| Dependency | Why | Required? |
|---|---|---|
| Python 3 | Runs the compiler frontend itself (`bin/festina` execs `python3 -m festina.cli`) — only if running from source; see the packaged-binary option below to avoid this entirely | Required (unless using the packaged binary) |
| A C compiler (`clang` or `gcc`) | Compiles `festina_runtime.c` and links the final binary | Required (either works, per stage 3) |
| `libsqlite3-dev` (headers) | `festina_runtime.c` does `#include <sqlite3.h>` | Required |
| `libcairo2-dev` + `libx11-dev` (headers) | `festina_runtime.c` does `#include <cairo/cairo.h>`/`<X11/Xlib.h>` (claude.md #37/#39's img/graphics functions) — needed to *compile* the runtime even for a program that never draws anything, same as sqlite3's headers | Required |
| `pkg-config` | Locates sqlite3's and Cairo/X11's compile/link flags | Required |
| `llvm` (provides `libLLVM`) | Lets `festina/llvm_backend.py` compile IR directly (stage 3's fast path, and the one that makes `gcc` usable at all) | Recommended — without it, the C compiler must specifically be `clang`, since only clang can parse `.ll` text (verified: `gcc` hands it to `ld`, which fails treating it as a corrupt linker script) |

Missing any of these fails with a specific, actionable error (claude.md
#59) rather than a raw traceback — naming the tool and how to get it.
Notably absent: anything for `regex()`/`.test()`/`.match()`/`.replace()`
(claude.md #67/#68) — they're built on POSIX extended regular
expressions (`<regex.h>`), already part of libc everywhere this list's
C compiler already requires libc, so regex support adds zero new
dependencies. Graphics is the opposite case: Cairo is a genuinely new
dependency (claude.md #39 itself requires it — "Graphics are backed by
Cairo"), and windowing needs libX11 alongside it (see [Regex and
String Matching](#regex-and-string-matching) vs.
[Graphics](#graphics) below for why one added nothing and the other
did).

Debian/Ubuntu:

```bash
sudo apt install clang libsqlite3-dev libcairo2-dev libx11-dev pkg-config
```

`clang` conveniently pulls in `libLLVM` as a dependency, covering both
the fast path and its fallback in one line. (`gcc` works too for the
fast path, but only if `libLLVM` is separately present — `clang` is the
simpler single recommendation.) macOS (Homebrew) should be similar in
spirit — `brew install llvm sqlite pkg-config` — though that combination
isn't verified in this repo's own test environment the way the
Debian/Ubuntu one is.

**To *use* a packaged `festina` binary** (stage 2 — built via
`./scripts/package_compiler.sh`, or downloaded from wherever a
maintainer published one): the same list above, minus Python 3 — the
packaged binary embeds its own interpreter, verified by actually running
it with every `python`/`python3*` on `PATH` replaced by a command that
always fails (`tests/test_packaging.py`). Building the binary yourself
needs one more thing, PyInstaller — a build-time-only dependency, not
something the resulting binary or festina/ itself needs:

```bash
pip install -r requirements-build.txt  # pyinstaller
./scripts/package_compiler.sh          # -> ./dist/festina
./dist/festina examples/hello.f -o hello
```

**To *run* a program someone already compiled with Festina**: this list
grew with graphics support (claude.md #37/#39) — confirmed via `ldd` on
a compiled binary, not just reasoned about. Before graphics, this was
close to nothing beyond libc/libm (plus `libsqlite3.so` conditionally,
per stage 1's static-link-when-possible). Now every compiled program
also dynamically links `libcairo.so`/`libX11.so` and their own
transitive dependencies (fontconfig, freetype, libpng, the X11
client-side stack, ...) — *even a program that never calls a graphics
function or opens a window*, since `festina_runtime.c` is one object
file linked into every program regardless of which parts it actually
uses (same reasoning as the compile-time header dependency above).
Static-linking Cairo/X11 the way stage 1 does for sqlite3 would undo
this, but hasn't been done (Cairo's own dependency tree is large
enough that it's a bigger undertaking than sqlite3's single-library
case was) — check any specific binary with `ldd` to see exactly what
it needs.

```bash
pip install -r requirements-dev.txt   # pytest, for the test suite
pytest tests/                         # 342 passed, 7 skipped (see Test suite above)

./bin/festina examples/hello.f -o hello
./hello
```

The 7 skips above need tools that aren't Python packages, so they're
not in any requirements file — `pip install`s nothing for them.
`pyinstaller` covers 2 (see the packaged-binary section above); `Xvfb`
(a virtual X server) and `xdotool` (simulates clicks, mouse movement,
key presses, and resizing) cover the other 5, needed only to test
claude.md #37/#39/#40's graphics functions and event handlers against a
real window without an actual display attached (`sudo apt install xvfb
xdotool` on Debian/Ubuntu) — a real `$DISPLAY` works too, if one is
already available.

## Why Festina?

JavaScript is incredibly productive, but its dynamic nature can introduce complexity and runtime overhead.

Festina keeps familiar syntax while introducing:

* Static typing
* Native compilation through LLVM
* Fast native execution
* Simple syntax
* Automatic memory management
* Built-in SQLite
* Automatic database schema management
* Built-in graphics
* Built-in audio support
* JavaScript-style string interpolation
* JavaScript-style ternary expressions
* A straightforward type system

The goal is not to recreate JavaScript. The goal is to create a language that feels familiar to JavaScript developers while behaving more like a traditional compiled language.

## A Familiar Syntax

Festina uses JavaScript-inspired syntax where it makes sense.

```festina
text name = 'Patrick'
int age = 32
bool active = true

if active {
    log(`Hello, ${name}`)
}
```

Parentheses around conditions are optional:

```festina
if active {
    log('Active')
}
```

or:

```festina
if (active) {
    log('Active')
}
```

Functions use explicit return types:

```festina
int func add(a:int, b:int) {
    return a + b
}
```

## Strong Types Without the Ceremony

Variables declare their types directly:

```festina
int count = 10
text message = 'Hello'
bool enabled = true
```

Festina provides:

```text
int
float
bool
text
blob
arr[T]
struct
table
img
aud
```

There is no `var` or `let`.

Booleans are explicit. Festina does not use JavaScript's truthy/falsy rules.

```festina
bool ready = true

if ready {
    log('Ready')
}
```

## Numeric Conversion

> **Status:** implemented, compile-time-checked and runtime-tested — see [Implementation Status](#implementation-status).

`int` and `float` never mix directly — not in arithmetic, and not in comparisons:

```festina
int a = 5
float b = 2.5
float c = a + b     // compile-time error
```

Convert one side explicitly. Every `int` has a `.toFloat()` method:

```festina
int a = 5
float b = 2.5
float c = a.toFloat() + b
```

Going from `float` to `int` means picking a rounding rule, so it's a `Math` function rather than a single method:

```festina
float price = 19.99
int total = Math.ceil(price) + 3
```

```text
Math.floor(x:float) -> int
Math.ceil(x:float) -> int
Math.round(x:float) -> int
Math.trunc(x:float) -> int
```

Division and modulo by zero don't crash the program — they return `null`:

```festina
int a = 10
int b = 0
int result = a / b   // null, not a crash
```

This applies to both `int` and `float`. `null` already has no natural bit pattern in a plain `i64`/`double` the way it does for a pointer, so the runtime represents it with a reserved value internally — an implementation detail, not something Festina source code inspects directly.

## Structs

Structs provide familiar object-like syntax while remaining statically typed.

```festina
struct User {
    id:int
    name:text
    active:bool
}

User user

user.id = 1
user.name = 'Patrick'
user.active = true
```

Structs are native in-memory types.

## Built-in SQLite

> **Status:** implemented and tested end to end — automatic table
> creation and schema sync (the section right after this one), `sqlite()`
> queries into a declared `arr[Table]`, and parameterized statements
> below. See [Implementation Status](#implementation-status) for the
> caveats (params must be a literal array; columns map by position).

SQLite is a first-class part of Festina.

There is no database setup code and no need to import a SQLite library.

Simply declare a table:

```festina
table People {
    id:int
    name:text
    age:int
}
```

Festina automatically uses:

```text
festina.sqlite
```

and automatically creates the table when necessary.

Queries are just as simple:

```festina
arr[People] people = sqlite('SELECT * FROM People')
```

Parameterized queries are supported:

```festina
sqlite(
    'INSERT INTO People (id, name) VALUES (?, ?)',
    [1, 'Patrick']
)
```

## Automatic Database Schema Management

The Festina table declaration is the source of truth for the database schema.

For example:

```festina
table People {
    id:int
    name:text
}
```

If the database does not contain `People`, Festina creates it.

If the database contains additional columns, Festina removes them.

If the Festina declaration adds a column, Festina adds it to the database.

If a declared column changes, Festina updates the database schema as necessary.

This means database schema changes can be made directly in the Festina source rather than requiring handwritten migration scripts.

## Arrays

> **Status:** implemented — literals, indexed read/write, nesting,
> `.length`, function params/return values. Not bounds-checked
> (claude.md doesn't specify it); data currently leaks (no GC yet).
> See [Implementation Status](#implementation-status).

Arrays are strongly typed:

```festina
arr[int] numbers
arr[text] names
arr[User] users
arr[People] people
```

Nested arrays are also possible:

```festina
arr[arr[int]] matrix
```

Every array has a built-in read-only `.length`:

```festina
arr[int] values = [1, 2, 3]
log(values.length)
```

## Loops

> **Status:** implemented — `for` and `while`, including loop-variable
> scoping and `while true`. No `break`/`continue` (claude.md doesn't
> define either); the only documented way out of a loop body early is
> `return` from the enclosing function. See
> [Implementation Status](#implementation-status).

C-style counted loops:

```festina
for int x = 0, x < 10, x++ {
    log(x)
}
```

Iterating an array with `.length`:

```festina
for int x = 0, x < array.length, x++ {
    log(array[x])
}
```

`while` loops:

```festina
int e = 0

while e < 10 {
    log(e)
    e++
}
```

Postfix `++`/`--` work on any mutable `int` variable, not just inside a
loop header:

```festina
int i = 0
i++
i--
```

## Regex and String Matching

> **Status:** implemented — `regex()`, `.test()`, `.match()`,
> `.replace()`/`.replaceAll()`, backed by POSIX extended regular
> expressions (no bundled or external regex engine — see
> [Setup](#setup)). No capture groups, backreferences, or non-greedy
> quantifiers (POSIX ERE's own limits). See
> [Implementation Status](#implementation-status).

A regex value is created with the global `regex()` function — there's
no `/pattern/` literal syntax, `regex()` is a global function like
`sqlite()` and `loadImage()`:

```festina
regex digits = regex('[0-9]+')
```

An optional second argument supplies flags as text; the only supported
flag is `i` (case-insensitive):

```festina
regex greeting = regex('^hello$', 'i')
```

Test whether a pattern matches anywhere in a value:

```festina
log(digits.test('room 42'))
```

Get the first matching substring, or `null` if there's no match:

```festina
text found = 'room 42'.match(digits)
```

Replace the first match, or every match, with a new value — `search`
may be either text (a literal substring match) or a `regex`:

```festina
text a = 'room 42'.replace('room', 'suite')
text b = 'a1b2c3'.replaceAll(regex('[0-9]'), '-')
```

`replace()`/`replaceAll()` return a new value; the original is
unchanged. If there's no match, they return the original value
unchanged too.

## Graphics

> **Status:** implemented — a real on-screen window (Xlib + Cairo's
> Xlib surface backend, not a file written to disk), opened
> automatically the first time a program draws something, reads
> `clientWidth`/`clientHeight`, or declares `on
> click`/`mouse`/`key`/`resize`/`close`. Verified against an actual
> rendered window via a virtual X server (`Xvfb`), not just reasoned
> about — see `tests/test_codegen.py`'s `TestGraphics`. See
> [Implementation Status](#implementation-status) for the caveats
> below.

Festina includes global graphics functions backed by Cairo.

```festina
drawRect(0, 0, 100, 100)
```

```festina
drawCircle(50, 50, 25)
drawText('Hello', 20, 20)
```

Images use the `img` type:

```festina
img profile = loadImage('profile.png')

drawImage(profile, 0, 0)
```

The canvas's current size is available as `clientWidth`/`clientHeight`
(read-only, named after the DOM's `Element.clientWidth`/`clientHeight`):

```festina
log(`canvas is ${clientWidth}x${clientHeight}`)
```

Event handlers can be declared directly in the source:

```festina
on click(x:int, y:int) {
    log(`Clicked at ${x}, ${y}`)
}

on mouse(x:int, y:int) {
    log(`Mouse moved over canvas on x: ${x}, y: ${y}`)
}

on key(key:text) {
    log(`Key pressed: ${key}`)
}

on resize() {
    log(`Canvas resized to ${clientWidth}x${clientHeight}`)
}

on close() {
    log('Canvas window closing')
}
```

A program that never calls a graphics function, never reads
`clientWidth`/`clientHeight`, and never declares one of the five event
handlers above never opens a window — the canvas only appears when
something actually needs it, the same way `festina.sqlite` only gets
touched by a program that declares a `table`.

Implementation-defined details claude.md doesn't specify, so these are
this compiler's own choices rather than anything from the spec:

- The canvas starts at a fixed 800×600 and is undecorated (no title
  bar/border — via the Motif WM hints convention, which most window
  managers honor but isn't part of the core X11 protocol, so a
  specific one could still ignore it). There's no syntax for declaring
  a different starting size, though the window can still be resized
  afterwards (e.g. by a window manager), which `clientWidth`/
  `clientHeight` and `on resize` reflect.
- Every shape and piece of text draws in solid black — none of
  claude.md's own `drawRect`/`drawCircle`/`drawText` examples take a
  color argument, so there's nothing to make configurable yet.
- `loadImage()` only supports PNG, via Cairo's own built-in decoder —
  claude.md #37 leaves supported formats up to "the runtime," and PNG
  is the one format Cairo can decode without another dependency.
- `on click`/`mouse` must declare exactly `(x:int, y:int)`, `on key`
  must declare exactly `(key:text)`, and `on resize`/`close` must
  declare no parameters — matching claude.md's own examples for each;
  the runtime registers the compiled handler as a fixed-signature
  function pointer per event. Any other declared event name still
  compiles, but never fires — there's no event source this runtime
  generates for it (claude.md #40 only ever shows these five).
- `on key`'s `key` text is the typed character for an ordinary
  printable key (e.g. `"a"`, `"5"`, `" "`), or X11's own key name for
  one that doesn't type a character (e.g. `"Escape"`, `"Return"`,
  `"Left"`) — there's no claude.md-defined naming scheme for these.
- `on resize` fires on a genuine size change and clears the canvas back
  to white at the new size, the same way resizing a browser's
  `<canvas>` element clears it too (which `clientWidth`/`clientHeight`
  are themselves named after).
- `on close` fires right before the window closes but can't cancel it
  — there's no "prevent default" here, just a chance to react.
- After the program's top-level code finishes running, if a window was
  opened, the process blocks (handling redraws, clicks, mouse
  movement, key presses, and resizes) until the window is closed, then
  exits normally. There's no way to close the window from Festina code
  itself.

## Audio

> **Status:** not implemented yet — see [Implementation Status](#implementation-status).

Audio uses the `aud` type:

```festina
aud music = loadAudio('music.mp3')

music.play()
```

Playback can be controlled with:

```festina
music.play()
music.stop()
music.isPlaying()
```

## Imports

> **Status:** implemented and tested end to end — recursive resolution,
> canonical-path deduplication, circular-import detection
> (`festina.imports`), and `bin/festina` actually compiles the whole
> multi-file dependency graph as one program. See
> [Implementation Status](#implementation-status).

Festina uses a deliberately simple import system:

```festina
import database.f
import graphics.f
```

Imports are resolved before compilation and are combined into the compilation unit.

A file is never imported more than once, even if multiple files depend on it.

## No Manual `main()`

The entry file does not need to define `main()`.

Given:

```festina
log('Hello, world!')
```

Festina automatically generates the application's entry point.

The compiler resolves imports and declarations first, initializes the application's database schema, and then executes the entry file.

## Native Compilation

Festina is designed to compile to native executables through LLVM:

```text
Festina
   ↓
AST
   ↓
Semantic Analysis
   ↓
LLVM IR
   ↓
Native Machine Code
   ↓
Executable
```

The goal is high performance without sacrificing a familiar development experience.

The compiler executable itself is `festina`. In this repository it lives
at `bin/festina` — a plain file named `festina` can't sit next to a
`festina/` package directory at the repo root, so the wrapper script is
one level down instead:

```bash
bin/festina main.f
```

produces a native executable (this part is implemented and tested today
— see [Implementation Status](#implementation-status)).

## Design Philosophy

Festina intentionally favors:

* Performance over flexibility
* Static typing over runtime inference
* Simple language rules over extensive syntax
* Compile-time work over runtime work
* Native representations over unnecessary abstraction
* Familiar syntax without JavaScript's dynamic semantics

Festina is not intended to be JavaScript with a different compiler. It is a compiled language that borrows syntax and ideas from JavaScript where they make development easier.

## Project Status

See [Implementation Status](#implementation-status) near the top of this
README for exactly what runs today versus what's still pending, and the
current test pass rate.

The language specification (`claude.md`) is evolving alongside the
compiler and runtime. Features described here represent the intended
direction of the language and may change during development.

## License

Festina is released under the MIT License.

## Repository

[https://github.com/uraikus/festina](https://github.com/uraikus/festina)
