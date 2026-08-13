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

**Test suite:** 261/261 passing, 0 skipped, 0 failed (`pytest tests/`).
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
| Semantic analysis | ✅ type resolution, struct/table distinction, bool-only conditions, function arg/return checking, all `#48` error categories |
| Primitives (`int` / `float` / `bool` / `text` / `blob`) | ✅ |
| Variables / constants | ✅ global and local |
| Functions | ✅ typed params, return values, `void`, structs returned by value |
| Control flow | ✅ `if`/`else`, ternary, `&&` / `||` (short-circuit), all operators |
| **Loops (`claude.md #60/#61`)** | ✅ `for init, cond, update { }` and `while cond { }`, including the loop-variable scoping rule and `while true` infinite loops — no `break`/`continue` (claude.md doesn't define either; `return` from the enclosing function is the only documented way out early) |
| **Postfix `++`/`--` (`claude.md #66`)** | ✅ mutable `int` variables only, compile-time-checked |
| String interpolation | ✅ `` `Hello ${name}` `` |
| `log()` / `fail()` | ✅ |
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
| 2. Package the compiler frontend as a real binary (no separate Python install to *run the compiler*) | — | not started |
| 3. Drive libLLVM directly instead of shelling out to the `clang` binary | `festina/llvm_backend.py` compiles the generated LLVM IR to an object file in-process via libLLVM's C API (ctypes) — `clang` is no longer *specifically* required; `gcc` (or any working C compiler/linker) now works too, since the only thing left for it to do is compile `festina_runtime.c` and link plain object files. Falls back to the original clang-only pipeline automatically if libLLVM can't be loaded, so this is purely additive | ✅ done |
| 4. Embed LLD too, removing the last external dependency (a system linker) | Some C compiler/linker still has to be present to compile `festina_runtime.c` and link — that's a meaningfully smaller ask than clang/LLVM specifically, but not yet zero | not started |

Stages 2 and 4 don't remove anything end users of *compiled Festina
programs* depend on — they're about what it takes to install and run
the `festina` compiler itself. Verified concretely, not just reasoned
about: `gcc` genuinely can't handle a `.ll` file at all (it hands it to
`ld`, which treats it as a corrupt linker script and fails) — compiling
the IR ourselves is what actually broadens compiler compatibility, not
just a style preference.

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
- `drawRect`/`drawCircle`/`drawText`/`drawImage`/`loadImage`/`loadAudio`
  aren't reserved words (unlike `log`/`fail`/`sqlite`, which are lexer
  keywords) — declaring a function with one of those names silently
  shadows the builtin at every call site rather than erroring. Left as
  is since none of them are implemented yet anyway; worth reserving
  properly once they are.
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

### Not implemented yet

| Area | Status |
|---|---|
| Graphics (`drawRect`, `img`, Cairo) | ❌ |
| Audio (`aud`, `loadAudio`) | ❌ |
| `on eventName` event handlers | ❌ |
| Multi-file compilation in the CLI | ⚠️ `festina.imports` resolves import graphs and is tested standalone; `bin/festina` itself still only compiles a single file |

### Setup

Two different dependency lists, and they're not the same size — this is
the practical payoff of the staged plan above.

**To *use* the compiler** (`bin/festina program.f`) on a fresh system:

| Dependency | Why | Required? |
|---|---|---|
| Python 3 | Runs the compiler frontend itself (`bin/festina` execs `python3 -m festina.cli`) — packaging it as a standalone binary is stage 2, not done yet | Required |
| A C compiler (`clang` or `gcc`) | Compiles `festina_runtime.c` and links the final binary | Required (either works, per stage 3) |
| `libsqlite3-dev` (headers) | `festina_runtime.c` does `#include <sqlite3.h>` | Required |
| `pkg-config` | Locates sqlite3's compile/link flags | Required |
| `llvm` (provides `libLLVM`) | Lets `festina/llvm_backend.py` compile IR directly (stage 3's fast path, and the one that makes `gcc` usable at all) | Recommended — without it, the C compiler must specifically be `clang`, since only clang can parse `.ll` text (verified: `gcc` hands it to `ld`, which fails treating it as a corrupt linker script) |

Missing any of these fails with a specific, actionable error (claude.md
#59) rather than a raw traceback — naming the tool and how to get it.

Debian/Ubuntu:

```bash
sudo apt install clang libsqlite3-dev pkg-config
```

`clang` conveniently pulls in `libLLVM` as a dependency, covering both
the fast path and its fallback in one line. (`gcc` works too for the
fast path, but only if `libLLVM` is separately present — `clang` is the
simpler single recommendation.) macOS (Homebrew) should be similar in
spirit — `brew install llvm sqlite pkg-config` — though that combination
isn't verified in this repo's own test environment the way the
Debian/Ubuntu one is.

**To *run* a program someone already compiled with Festina**: usually
nothing beyond libc/libm, already present on essentially any machine —
confirmed via `ldd` on a compiled binary. The one conditional dependency
is `libsqlite3.so`, and only if the machine that *compiled* it didn't
have a static `libsqlite3.a` available (falls back to dynamic linking in
that case, per stage 1) — check any specific binary with `ldd` to be
sure.

```bash
pip install -r requirements-dev.txt   # pytest, for the test suite
pytest tests/                         # 261 passed

./bin/festina examples/hello.f -o hello
./hello
```

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

## Graphics

> **Status:** not implemented yet, including the event handlers below —
> see [Implementation Status](#implementation-status).

Festina includes global graphics functions backed by Cairo.

```festina
drawRect(0, 0, 100, 100)
```

Images use the `img` type:

```festina
img profile = loadImage('profile.png')

drawImage(profile, 0, 0)
```

Event handlers can be declared directly in the source:

```festina
on click(x:int, y:int) {
    log(`Clicked at ${x}, ${y}`)
}
```

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

> **Status:** import resolution (recursive, deduplicated, cycle-checked)
> is implemented and tested in `festina.imports`, but `bin/festina`
> doesn't call it yet — it compiles a single file only. See
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
