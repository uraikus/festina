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
table are created/synced automatically at startup even though nothing
queries it yet; see [Implementation Status](#implementation-status).)*

## Implementation Status

Festina is under active development. Everything else in this README
describes the full target language from `claude.md`; this section is
the ground truth for what actually runs today.

**Test suite:** 155/155 passing, 0 skipped, 0 failed (`pytest tests/`).
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
| Functions | ✅ typed params, return values, `void` |
| Control flow | ✅ `if`/`else`, ternary, `&&` / `||` (short-circuit), all operators |
| String interpolation | ✅ `` `Hello ${name}` `` |
| `log()` / `fail()` | ✅ |
| Structs | ✅ declaration, field read/write, passed to functions |
| **Automatic SQLite schema sync** | ✅ `festina.sqlite` opens/creates itself; tables are created, and columns added/dropped/retyped with existing data preserved via a temp-table rebuild — the `claude.md #31` worked examples all pass as tests |
| Native executables | ✅ `bin/festina program.f -o program` produces a real, standalone binary |

### Not implemented yet

| Area | Status |
|---|---|
| `arr[T]` as a real data structure | ❌ parses and type-checks; codegen raises a clear "not implemented yet" error |
| `sqlite()` queries into `arr[Table]` | ❌ schema sync works, fetching/inserting rows doesn't yet |
| Parameterized queries | ❌ |
| Graphics (`drawRect`, `img`, Cairo) | ❌ |
| Audio (`aud`, `loadAudio`) | ❌ |
| `on eventName` event handlers | ❌ |
| Multi-file compilation in the CLI | ⚠️ `festina.imports` resolves import graphs and is tested standalone; `bin/festina` itself still only compiles a single file |

`compiler/` in this repository is a separate, older prototype that
compiles a small JavaScript subset — unrelated to the `festina/` package
this status section describes.

### Building and running

Requires `clang` and the `sqlite3` development headers (`libsqlite3-dev`
on Debian/Ubuntu).

```bash
pip install -r requirements-dev.txt   # pytest, for the test suite
pytest tests/                         # 155 passed

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

> **Status:** automatic table creation and schema sync (the section right
> after this one) are implemented and tested. `sqlite()` queries and
> parameterized statements below are not implemented yet — see
> [Implementation Status](#implementation-status).

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

> **Status:** not implemented yet. `arr[T]` type-checks correctly, but
> codegen raises a clear error for it — see [Implementation Status](#implementation-status).

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
