# Festina

**A fast, simple, statically typed language that compiles straight to native code.**

Familiar, readable syntax — template strings, ternaries, arrow
functions, ordinary control flow — checked at compile time and backed
by real static types, not a runtime doing the work for you. Festina
compiles through LLVM to a real, standalone executable — with SQLite,
graphics, audio, timers, threads, and an HTTP/WebSocket server built
directly into the language, not bolted on as libraries.

Version 0.38 — see [CHANGELOG.md](CHANGELOG.md).

[![Tests](https://img.shields.io/badge/tests-2206%20passing-brightgreen)](tests/CONTRACT.md)
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
  on-screen canvas (`drawRect`, `on mouseDown`, typed `color`/`font` values,
  ...) and real audio playback
  (`aud music = 'music.mp3'` then `music.play()`) are just declarations
  and methods, backed by Cairo/X11 and
  ALSA under the hood.
- **Real threads, without the footguns.** `thread worker { ... }` runs
  on its own OS thread and can't touch a global or reach into another
  thread at all — every message across the boundary is a deep copy, so
  there is no shared mutable state to race on. Send with
  `worker.postMessage(x)`, answer with `t.reply(y)`, get the answer back
  with `.callback(fn)`, and scale out with `thread pool[N] { ... }`.
- **Automatic memory, manual override.** Values are reclaimed for you —
  and `free spritesheet` / `delete map.key` exist for the moments you
  know the lifetime better than the compiler does.
- **Readable syntax, real guarantees.** Template strings, ternaries,
  familiar control flow — but every condition is a real `bool`, and
  everything is statically typed and checked before it runs.
- **Slim by default.** A compiled program only links what it actually
  uses — skip graphics and audio in your source, and the binary skips
  Cairo, X11, and ALSA too. See [security.md](security.md#slim-binaries).
- **Minimal setup, by design.** The fewest dependencies that get the
  job done, failing loudly and clearly the moment one is actually
  missing. See [setup.md](setup.md).

## Get started

One line, on Linux/macOS/MSYS2 UCRT64 bash on Windows (no prebuilt binary
to trust — it clones the source and checks/installs build dependencies
for you; see [setup.md](setup.md) for what it actually does):

```bash
curl -fsSL https://raw.githubusercontent.com/uraikus/festina/main/install.sh | sh
```

Or from an existing checkout:

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
Add `--fix` and it installs whatever's missing for you (via `apt`/
Homebrew/MSYS2's `pacman`, whichever this machine has) and adds
`festina` to `PATH` too.

That's the whole loop — see [setup.md](setup.md) for the full dependency
breakdown (what's required vs. only-if-you-use-it), packaged-binary
installs, and running the test suite.

Editing `.f` files in Vim or Neovim? See
[editors/vim](editors/vim/README.md) for syntax highlighting (Vim's
own bundled Fortran filetype otherwise claims the `.f` extension).

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

`int` and `float` mix freely — the `int` side is promoted to `float`
automatically, and `/` always returns `float`. The only way back to
`int` is `Math.floor/ceil/round/trunc`. Division and modulo by zero
return `null` instead of crashing. See [api.md](api.md) for the full
language and standard library reference.

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
on mouseDown(x:int, y:int, button:int) { log(`pressed ${x}, ${y}`) }
on mouseUp(x:int, y:int, button:int)   { log(`released ${x}, ${y}`) }

// Audio
aud music = 'music.wav'
music.play()

// Files -- a blob is a file's bytes, and knows its own path
blob notes = 'notes.txt'
notes.write('hello')
log(notes.toText())
notes.saveCopy('notes.bak')           // img and aud save the same way

// Timers
setTimeout(showMessage, 1000)
setInterval(tick, 500)

// Regex -- a /pattern/flags literal
'room 42'.replace(/[0-9]+/, 'N')

// Maps -- text-keyed literals
map[int] npcHealths = {'npc1': 10, 'npc2': 15}
npcHealths['npc1'] = 30

// Threads -- isolated, message-passing, no shared mutable state
thread doubler {
    on message(sender:thread, msg:int) { sender.reply(msg * 2) }
}
doubler.postMessage(21).callback(void (answer:int) => log(answer))

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
with nothing but `drawRect`/`drawText`/`on mouseDown`:

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
| [`regex.f`](examples/regex.f) | `/pattern/flags` literals, `.test()`, `.match()`, `.replace()`, and `/g` for every-match |
| [`timers.f`](examples/timers.f) | `setTimeout`/`setInterval`/`clearInterval` |
| [`graphics.f`](examples/graphics.f) | A drawn canvas, plus every `on mouseDown`/`mouseUp`/`mouse`/`key`/`resize`/`close` handler |
| [`audio.f`](examples/audio.f) | `aud` from a path, `.play()`/`.stop()`/`.isPlaying()`, channels, with a tiny bundled WAV |
| [`files.f`](examples/files.f) | `blob` — a file's bytes, its methods, `save`/`saveCopy`, and what sharing one means |
| [`tic_tac_toe.f`](examples/tic_tac_toe.f) | The game above — graphics, global game state, and win-checking logic together |
| [`layers.f`](examples/layers.f) | `arr[img]` as a layer stack — each layer modified by its own drawing methods, one function compositing all of them every frame |
| [`threaded_http_server.f`](examples/threaded_http_server.f) | `thread pool[N]` + `NAME.giveRequest(r)` — an HTTP server that computes real, CPU-bound per-request work across more than one OS thread at once |

Every one of these is compiled and checked by the test suite on every
change (`tests/test_examples.py` and, for the three needing a display,
`tests/test_codegen.py::TestExampleGraphicsAndGame`) — they're not just
snippets that happened to work once.

## How it compares

Festina, Rust, Go, and Bun, on the same small equivalent-logic
benchmarks (recursive function calls, tight-loop arithmetic, process
startup, array allocation, string concatenation) — see
[benchmark.md](benchmark.md) for the methodology and the full,
regularly-refreshed results table. Short version: Festina holds its own
against Rust and Go on compute-bound native code, comfortably outruns a
JIT on cold single-shot execution, and its canvas draws a 40,000-shape
frame about twice as fast as Chromium's.

## Project status

The compiler frontend, LLVM codegen backend, and native C runtime are
real and tested: **2206 tests, 0 failures** (9 more skip cleanly when
their optional tooling isn't installed — see
[setup.md](setup.md#running-the-test-suite)). Every language construct
in the [specification](claude.md) is implemented end to end, not just
parsed. That includes a leak stress suite —
[`scripts/leak_stress.sh`](scripts/leak_stress.sh) runs mixed churn
programs plus one isolation program per data type under
AddressSanitizer and LeakSanitizer — because "the answers are right"
and "nothing accumulates while producing them" are different claims.
Native builds on Linux, macOS, and Windows, plus cross-compiling to
`wasm32-wasi` (see [wasm.md](wasm.md)), are all supported today. See
[`tests/CONTRACT.md`](tests/CONTRACT.md) for exactly what's covered
and how, and [todo.md](todo.md) for what's next.

```bash
python3 -m pytest            # the whole suite
scripts/leak_stress.sh       # just the sanitizer stress runs
```

| | |
|---|---|
| **Language & API reference** | [api.md](api.md) |
| **Setup & dependencies** | [setup.md](setup.md) |
| **Security** | [security.md](security.md) |
| **Benchmarks vs. Rust/Go/Bun** | [benchmark.md](benchmark.md) |
| **Roadmap** | [todo.md](todo.md) |
| **Changelog** | [CHANGELOG.md](CHANGELOG.md) |
| **Full spec-compliance test suite** | [tests/CONTRACT.md](tests/CONTRACT.md) |
| **Language specification** | [claude.md](claude.md) |

## Design philosophy

Festina intentionally favors:

- Performance over flexibility
- Static typing over runtime inference
- Simple language rules over extensive syntax
- Compile-time work over runtime work
- Native representations over unnecessary abstraction
- Readable, familiar syntax with none of the dynamic-typing surprises

Festina is a compiled language, checked and typed at every step — its
syntax is meant to read easily and get out of your way, not to trade
away safety or performance for flexibility.

## License

MIT — see [LICENSE](LICENSE).

## Repository

[https://github.com/uraikus/festina](https://github.com/uraikus/festina)
