# Festina

**A fast, simple, JavaScript-inspired language that compiles straight to native code.**

Keep the syntax you already know. Drop the interpreter, the runtime
overhead, and the boilerplate. Festina compiles through LLVM to a real,
standalone executable — with SQLite, graphics, audio, and JS-style
timers built directly into the language, not bolted on as libraries.

[![Tests](https://img.shields.io/badge/tests-971%20passing-brightgreen)](tests/CONTRACT.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

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

No imports. No config. No `main()`. Run this today — `festina.sqlite`
and the `People` table are created and kept in sync automatically, the
whole `festina` runtime is one native binary, and it needs nothing
Python or JIT-shaped to execute.

```bash
bin/festina compile examples/greet.f -o greet && ./greet
```

## Why Festina

- **Native compilation, not a VM.** Every Festina program compiles
  through LLVM to a real executable. No interpreter, no JIT warmup, no
  runtime shipped alongside your code — see how it stacks up against
  Rust, Go, and Bun in [benchmark.md](benchmark.md).
- **A database that's just... there.** Declare a `table`, and
  `festina.sqlite` is created, migrated, and kept in sync automatically
  — no ORM, no migration scripts, no setup code.
- **Graphics and audio, no dependencies to wire up yourself.** A real
  on-screen canvas (`drawRect`, `on click`, typed `color`/`font` values,
  ...) and real audio playback
  (`loadAudio().play()`) are just functions, backed by Cairo/X11 and
  ALSA under the hood.
- **JavaScript syntax, none of the surprises.** Template strings,
  ternaries, familiar control flow — but `int`/`float` never silently
  mix, every condition is a real `bool`, and everything is statically
  typed and checked before it runs.
- **Slim by default.** A compiled program only links what it actually
  uses — skip graphics and audio in your source, and the binary skips
  Cairo, X11, and ALSA too. See [security.md](security.md#binary-slimming).
- **Minimal setup, by design.** `claude.md #59`'s whole premise: the
  fewest dependencies that get the job done, failing loudly and clearly
  the moment one is actually missing. See [setup.md](setup.md).

## Get started

```bash
sudo apt install clang libsqlite3-dev libcairo2-dev libx11-dev libasound2-dev pkg-config
bin/festina compile examples/hello.f -o hello
./hello
```

Or skip the intermediate binary and just run it:

```bash
bin/festina run examples/hello.f
```

Not sure your machine has everything Festina needs? `bin/festina doctor`
checks every dependency above and tells you exactly what's missing and
how to install it — including whether `festina` itself is on `PATH` yet.

That's the whole loop — see [setup.md](setup.md) for the full dependency
breakdown (what's required vs. only-if-you-use-it), packaged-binary
installs, and running the test suite.

## A familiar language, statically typed

```festina
int count = 10
text message = 'Hello'
bool active = true

if active {
    log(`${message}, ${count} times`)
}

int func add(a:int, b:int) {
    return a + b
}

for int i = 0, i < 10, i++ {
    log(i)
}
```

`int` and `float` never mix implicitly — convert explicitly with
`.toFloat()` or `Math.floor/ceil/round/trunc`. Division and modulo by
zero return `null` instead of crashing. See [api.md](api.md) for the
full language and standard library reference.

## Built in, not bolted on

```festina
// SQLite -- no setup code, no ORM
table People { id:int  name:text }
arr[People] people = sqlite('SELECT * FROM People')

// Graphics -- a real window, opened on first use
color brand = '#4a90d9'
font  title = 'bold 24px sans-serif'
fillStyle(brand)
changeFont(title)
drawRect(0, 0, 100, 100)
on click(x:int, y:int) { log(`clicked ${x}, ${y}`) }

// Audio
aud music = loadAudio('music.wav')
music.play()

// Timers, JS-style
setTimeout(showMessage, 1000)
setInterval(tick, 500)

// Regex -- JS-style literal syntax
'room 42'.replace(/[0-9]+/, 'N')

// Maps -- text-keyed, JS-object-literal-flavored
map[int] npcHealths = {'npc1': 10, 'npc2': 15}
npcHealths['npc1'] = 30

// Config straight from the environment, no extra library
text apiKey = environment.API_KEY
```

Full reference for every one of these — signatures, caveats, what's
implementation-defined vs. spec-mandated — is in [api.md](api.md).

## See it in action

[`examples/`](examples/) has small, runnable programs covering the
whole language — from a one-liner to a real, playable two-player
**tic-tac-toe game** ([`tic_tac_toe.f`](examples/tic_tac_toe.f), click a
cell, alternating X/O, real win detection) built entirely in Festina
with nothing but `drawRect`/`drawText`/`on click`:

```bash
bin/festina compile examples/tic_tac_toe.f -o tic_tac_toe && ./tic_tac_toe
```

| Example | What it shows |
|---|---|
| [`greet.f`](examples/greet.f) | The README's own hero example |
| [`fizzbuzz.f`](examples/fizzbuzz.f) | Loops, modulo, control flow — no dependencies |
| [`arrays.f`](examples/arrays.f) | Array literals, indexing, `.length` |
| [`basic.f`](examples/basic.f) / [`hello.f`](examples/hello.f) | Tables, SQLite queries, structs, functions |
| [`multifile.f`](examples/multifile.f) + [`geometry.f`](examples/geometry.f) | `import` across files |
| [`maps.f`](examples/maps.f) | `map[T]` literals, indexed get/set, `.forEach()` |
| [`config.f`](examples/config.f) | `DatabaseURL`, `environment.NAME` |
| [`regex.f`](examples/regex.f) | `/pattern/flags` literals, `.test()`, `.match()`, `.replace()`/`.replaceAll()` |
| [`timers.f`](examples/timers.f) | `setTimeout`/`setInterval`/`clearInterval` |
| [`graphics.f`](examples/graphics.f) | A drawn canvas, plus all five `on click`/`mouse`/`key`/`resize`/`close` handlers |
| [`audio.f`](examples/audio.f) | `loadAudio()`/`.play()`/`.stop()`/`.isPlaying()`, with a tiny bundled WAV |
| [`tic_tac_toe.f`](examples/tic_tac_toe.f) | The game above — graphics, global game state, and win-checking logic together |

Every one of these is compiled and checked by the test suite on every
change (`tests/test_examples.py` and, for the two needing a display,
`tests/test_codegen.py::TestExampleGraphicsAndGame`) — they're not just
snippets that happened to work once.

## How it compares

Festina, Rust, Go, and Bun, on the same small equivalent-logic
benchmarks (recursive function calls, tight-loop arithmetic, process
startup, array allocation, string concatenation) — see
[benchmark.md](benchmark.md) for the methodology and the full,
regularly-refreshed results table. Short version: Festina holds its own
against Rust and Go on compute-bound native code, and comfortably
outruns a JIT on cold single-shot execution — unsurprising for a young
optimizer against two of the most mature compiled-language backends
around, and exactly the kind of regression tracking a young compiler
needs.

## Project status

Festina is under active development, but not vaporware — the compiler
frontend, LLVM codegen backend, and native C runtime are real and
tested: **971 tests, 0 failures.** Every `claude.md` language construct
this project has committed to is implemented end to end, not just
parsed. See [`tests/CONTRACT.md`](tests/CONTRACT.md) for exactly what's
covered and how, and [todo.md](todo.md) for what's next (macOS, Windows,
HTTP).

| | |
|---|---|
| **Language & API reference** | [api.md](api.md) |
| **Setup & dependencies** | [setup.md](setup.md) |
| **Security & audit history** | [security.md](security.md) |
| **Benchmarks vs. Rust/Go/Bun** | [benchmark.md](benchmark.md) |
| **Roadmap** | [todo.md](todo.md) |
| **Full spec-compliance test suite** | [tests/CONTRACT.md](tests/CONTRACT.md) |
| **Language specification** | [claude.md](claude.md) |

## Design philosophy

Festina intentionally favors:

- Performance over flexibility
- Static typing over runtime inference
- Simple language rules over extensive syntax
- Compile-time work over runtime work
- Native representations over unnecessary abstraction
- Familiar syntax without JavaScript's dynamic semantics

Festina is not JavaScript with a different compiler — it's a compiled
language that borrows syntax and ideas from JavaScript where they make
development easier.

## License

MIT — see [LICENSE](LICENSE).

## Repository

[https://github.com/uraikus/festina](https://github.com/uraikus/festina)
