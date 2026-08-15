# API Reference

The Festina language and standard library, as implemented today. For
narrative introductions and rationale see the individual sections below;
for exactly what's implemented vs. not (and known caveats), see
[`tests/CONTRACT.md`](tests/CONTRACT.md); for the full target-language
spec this compiler is built against, see [`claude.md`](claude.md).

## CLI

Four subcommands (`festina/cli.py`), not a single bare `festina file.f`
— that would leave `festina run` (which executes the compiled result)
ambiguous with `festina compile` (which never does) without inventing a
flag to distinguish them:

| Command | What it does |
|---|---|
| `festina compile entry.f -o out` | Compile to a native executable at `out` (default: `entry`'s own filename without `.f`). `--emit-llvm` prints LLVM IR to stdout instead of linking. `--cc` picks the C compiler/linker (default: whichever of `clang`/`gcc`/`cc` is found first). |
| `festina run entry.f` | Compile to a throwaway temp executable and run it immediately — stdin/stdout/stderr inherited directly (not captured), so an interactive program (graphics/audio/timers) behaves exactly like a normal compile-then-run. Exits with the *compiled program's own* exit code, so `festina run x.f && ...` composes the same way `go run`/`cargo run` do. The temp binary is always cleaned up afterward. |
| `festina doctor` | Checks every dependency the compiler itself needs (a C compiler, `pkg-config`, sqlite3/cairo-xlib/alsa dev headers, `libLLVM`) and reports what's missing and how to install it — the same install hints a real compile failure would give (`claude.md #59`), just checked proactively instead of only on failure. Also reports whether `festina` itself is resolvable on `PATH`, and if not, exactly how to add it (the checkout's `bin/` directory, or a packaged binary — see [setup.md](setup.md)). Exits 0 if every *required* dependency is present — graphics/audio are optional, since a compiler that can't build a graphics program is still a fully working compiler for everything else (see [security.md](security.md#binary-slimming)). |
| `festina help` | Prints this same command list. |

```bash
bin/festina compile program.f -o program   # compile
bin/festina compile program.f --emit-llvm  # print LLVM IR instead of linking
./program                                  # the result needs neither
                                            # Python nor festina/ to run
bin/festina run program.f                  # or just run it directly
bin/festina doctor                         # check dependencies
```

A program needs no `main()` — top-level statements in the entry file run
in order, after every import is resolved and every declared table's
schema is synced.

## Compilation pipeline

```
Festina source (.f)
   -> AST (festina.parser)
   -> Semantic analysis (festina.semantic)
   -> LLVM IR (festina.codegen)
   -> Object file (festina.llvm_backend, via libLLVM -- or clang's IR
      frontend as a fallback)
   -> Link against the runtime (runtime/festina_runtime.c and,
      conditionally, _graphics.c/_audio.c -- see "Binary size" below)
   -> Native executable
```

### Binary size

A compiled program only links what it actually uses: the graphics
(Cairo/X11) and audio (ALSA) runtime object files are only passed to the
linker when the program calls something from them (see
[security.md](security.md#binary-slimming)). `libsqlite3` is always
available (statically linked when possible — see
[setup.md](setup.md#static-linking-sqlite3)), since automatic database
support (`table`, `sqlite()`) is always on.

## Types

```text
int       -- 64-bit signed integer
float     -- 64-bit floating point (IEEE 754 double)
bool      -- true / false, no truthy/falsy coercion from anything else
text      -- UTF-8 string
blob      -- binary data (shares text's representation; text -> blob
             assignment is allowed, the reverse is not)
arr[T]    -- homogeneous array of any of the above, a struct, or a
             declared table's row type
map[T]    -- text-keyed map of any of the above except arr[T]/map[T]
             itself (see the Maps section below)
struct    -- user-declared record type
table     -- a struct that's also backed by a SQLite table
img       -- an image loaded via loadImage() (opaque handle)
aud       -- an audio clip loaded via loadAudio() (opaque handle)
regex     -- a compiled pattern from a /pattern/flags literal or regex() (opaque handle)
```

Every type may hold `null` (`bool x = null` included — see "Division and
modulo by zero" below for how `int`/`float`/`bool` each represent it
internally; comparing a null `int`/`bool` against the `null` literal
with `==`/`!=` works as expected, but a null `float` never compares
equal to `null` via `==`, even to itself — it's a real NaN under the
hood, and IEEE-754 NaN comparisons are always false).

`int`/`float` never mix implicitly, in arithmetic or comparisons:

```festina
int a = 5
float b = 2.5
float c = a.toFloat() + b        // int.toFloat() is the only int->float conversion
```

```text
Math.floor(x:float) -> int
Math.ceil(x:float) -> int
Math.round(x:float) -> int
Math.trunc(x:float) -> int
```

Division/modulo by zero return `null` (for both `int` and `float`)
rather than crashing:

```festina
int result = 10 / 0   // null
```

`int`/`float`/`bool` also each have `.toText()`, returning the same
text template interpolation already produces for that value implicitly
— useful when that text is needed outside of a template:

```festina
int count = 5
log(count.toText())   // "5" -- identical to log(`${count}`)
```

## Variables, constants, functions

```festina
int count = 10
const int max = 100      // reassigning a const is a compile error
text message = 'Hello'

int func add(a:int, b:int) {
    return a + b
}

void func log_it() {
    log('called')
}
```

No `var`/`let` — every declaration states its type. Functions are not
first-class values (no closures, no passing a function as a value) —
this is why `setTimeout`/`setInterval`'s callback argument must be the
bare name of an already-declared, zero-parameter, `void`-returning
function, not an arbitrary expression.

## Control flow

```festina
if condition {
    ...
} else {
    ...
}

bool result = condition ? 'yes' : 'no'   // ternary
bool both = a && b                        // short-circuit
bool either = a || b                      // short-circuit

for int i = 0, i < 10, i++ {
    if i == 5 { break }
    if i % 2 == 0 { continue }
    log(i)
}

while condition {
    ...
}
```

Conditions must be `bool` — no implicit truthiness from `int`/`text`/etc.
`break` exits the nearest enclosing `for`/`while` loop immediately;
`continue` skips to that loop's next iteration (a `for` loop's update
expression still runs first). Both are a compile error outside any loop,
and both only ever affect the *nearest* enclosing loop — no labeled
break/continue targeting an outer one. Postfix `++`/`--` work on any
mutable `int` variable.

## Strings

```festina
text greeting = `Hello, ${name}!`     // template literals
text a = 'room 42'.replace('room', 'suite')
text b = 'a1b2c3'.replaceAll(/[0-9]/, '-')
bool matched = /[0-9]+/.test('room 42')
text found = 'room 42'.match(/[0-9]+/)   // null if no match
```

## Structs

```festina
struct User {
    id:int
    name:text
    active:bool
}

User user
user.id = 1
user.name = 'Patrick'
```

Structs are native in-memory records — declaration, field read/write,
and passing to/returning from functions by value.

Memory for structs, arrays, and maps is managed automatically — no
manual allocation or freeing. A local struct/`arr[T]`/`map[T]`
declared in a function, event handler, `if` branch, `while` body, or
`for` body, and never returned or stored anywhere longer-lived, is
reclaimed automatically as soon as control leaves the block it was
declared in — for a value declared inside a loop body, that means
every iteration, not deferred until the function eventually returns;
`break`/`continue` reclaim it too, the same as reaching the end of that
iteration normally would. Passing a value to another function no
longer unconditionally prevents this: if that function's own body
never itself lets the value outlive the call (only reads/writes
through its own fields, or passes it on to some other function that
in turn doesn't retain it either), the original value is still
reclaimed exactly the same way. A struct reclaimed this way is a real
stack allocation, not a heap allocation freed afterward — faster, not
just eventually cleaned up, and with each recursive call still getting
its own independent copy the same way any other stack-local value
would. A `map[T]` reclaimed this way frees each of its own entries
completely, keys included, not just the entries themselves. A value
that does escape a function entirely isn't necessarily lost, either: a
struct-typed global variable's value is reference counted and freed
once nothing references it anymore, on every reassignment (including
its own initial declaration) — a global repeatedly reassigned in a
loop no longer leaks every value but the last. A struct-typed local
that escapes gets the same treatment at its own scope-exit — declared
with an initializer, or reassigned after declaration, no longer exclude
it either, since every new value a local ever comes to hold (through an
initializer or a plain reassignment) is now retained first whenever
that value might already be referenced elsewhere. Being returned no
longer excludes a local either — a function's own `return` retains the
value it hands back under the same rule, so a struct local that's ever
returned, a struct-typed parameter returned straight through, and a
`cond ? a : b` between two locals are all now correctly reclaimed
(whichever value wasn't actually returned is freed; the one that was
survives with exactly the right reference count). A call result
discarded outright, never bound to any variable at all (`someFunc();`
used as a bare statement), is reclaimed too — released immediately at
the point it's discarded, since a function's own return value is
always freshly produced and nothing else can be referencing it yet.
Every struct value is now correctly reclaimed once nothing references
it anymore, whichever of these shapes produced it. This includes a
struct's own struct-typed *fields*: `outer.field = value` retains
`value` the same way any other binding does, and freeing `outer`
recursively frees whatever its own struct-typed fields still hold too,
however many levels deep a program actually nests structs.

An escaping `arr[T]`/`map[T]` value is reclaimed the same way: two
variables made to alias each other (`map[T] b = a`) now share one
underlying value, not independent copies — so growing `b` (adding a
new key) is correctly visible through `a` too, not just the data each
started out with. Assigning `[1, 2, 3]`/`{...}` into a fresh binding,
returning an array/map, passing one to another function, storing one
in a struct field — every one of these is reclaimed once nothing
references it anymore, the identical rule struct values already
follow. This includes an `arr[T]`/`map[T]`'s own elements/values, when
their own type is itself reclaimed this way (a struct, `arr[T]`, or
`map[T]`): `boxes[0] = replacement`/`boxes['key'] = replacement`
retains the new value and releases whatever that slot previously held,
the same rule a struct's own field write already follows, and freeing
an array or map recursively releases each of its own elements/values
too, however many levels deep a program nests `arr[T]`/`map[T]`.

`text` is reclaimed too, by a different mechanism, and the difference
is visible in one place worth knowing about. Rather than being
reference counted, every text-typed binding — local, global, struct
field, array element, map value, parameter — always holds its *own*
private copy of the string, made automatically wherever one is needed.
So unlike `arr[T]`/`map[T]`, two text variables never come to share one
underlying buffer: `text b = a` gives `b` its own copy, and text
values are immutable in Festina anyway, so nothing can observe the
difference except that each binding can be freed independently. That's
what lets a text value be freed on every reassignment and at every
scope exit unconditionally, with no escape analysis involved — a loop
that rebuilds a string each iteration (`` s = `${s}x` ``) frees the
previous buffer every time instead of accumulating them. Two cases are
not yet reclaimed: text passed to the graphics, sqlite, and timer
builtins, and text globals at process exit.

## Arrays

```festina
arr[int] numbers = [1, 2, 3]
arr[arr[int]] matrix
log(numbers.length)
numbers[0] = 10
```

Not bounds-checked. Memory is reclaimed automatically — see "Structs"
above for the full picture (non-escaping locals reclaimed at scope-exit,
escaping values reference counted).

## Maps

```festina
text npc2Id = 'npc2'
map[int] npcHealths = {'npc1': 10, npc2Id: 15}   // keys are always text
map[text] npcNames = {'npc1': 'jim', npc2Id: 'john'}

npcHealths['npc1']          // -> 10
npcHealths[npc2Id]          // -> 15 -- a key can be any text expression
npcHealths['missing']       // -> null -- a missing key, not an error

npcHealths['npc1'] = 30     // updates an existing key
npcHealths['npc3'] = 5      // adds a new one

void func logHealth(h:int, key:text) {
    log(`${key} ${h.toText()}`)
}
npcHealths.forEach(logHealth)   // (value, key) -- visit order is unspecified
```

An unquoted identifier key (`npc2Id` above) is a reference to that
variable's own text value, not bareword-as-string-name shorthand the
way a plain JS object literal has. `map[T]`'s `T` may be any type
except `arr[...]`/`map[...]` itself (a map value is stored in one
fixed-size slot, which those two don't fit in). `.forEach()`'s callback
must be an already-declared function taking exactly `(value, key:text)`
and returning nothing, the same "bare name of a declared function"
restriction `setTimeout`'s callback has. Not a hash table internally —
lookup/insert are O(n) over the entry count, a deliberate simplicity
tradeoff for what's meant to be a small, config/game-state-shaped
collection, not a large-scale data structure.

## Built-in SQLite

```festina
table People {
    id:int
    name:text
}

arr[People] people = sqlite('SELECT * FROM People')
sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])
```

Every `table` declaration is synced against `festina.sqlite` at startup
— created if missing, columns added/dropped/retyped (existing data
preserved via a temp-table rebuild) to match the declaration exactly.
`sqlite()`'s optional second argument (bound parameters) must be a
literal array expression, not an arbitrary `arr[T]` value. Query result
columns map onto a declared table's fields by position, not by name.

### Database configuration

`festina.sqlite` is the default, but the entry file's very first line
(before any other code and before any `import`) may override it:

```festina
DatabaseURL = 'game_saves.sqlite'
```

`path` may be any text expression, including `environment.NAME` (see
[Environment variables](#environment-variables) below) — useful for
picking the database path per-deployment without recompiling:

```festina
DatabaseURL = environment.DATABASE_URL
```

`DatabaseURL` appearing anywhere other than the entry file's first
statement is a compile-time error; it has no effect at all in an
imported file (only the file actually passed to the compiler is
checked).

## Environment variables

```festina
text apiKey = environment.API_KEY
text home = environment['HOME']       // computed key -- must be text

if apiKey == null {
    fail('API_KEY is not set')
}
```

Returns the named environment variable as `text`, or `null` if it
isn't set. Read-only (assigning to `environment.NAME` is a compile-time
error) and can't be used by itself without a `.NAME`/`[keyExpr]` — both
are also compile-time errors, not runtime ones.

## Regex

```festina
regex digits = /[0-9]+/                    // JS-style literal, POSIX extended regex underneath
regex ci = /^hello$/i                      // 'i' = case-insensitive; 'g' is also accepted (see below)
digits.test('room 42')                     // -> bool
'room 42'.match(digits)                    // -> text or null
'a1b2'.replace(digits, 'x')                // first match only
'a1b2'.replaceAll(digits, 'x')             // every match
```

`flags` immediately follows the closing `/`, no space (`/pattern/flags`).
Only `i` (case-insensitive) and `g` are accepted — `g` is recognized
for familiarity with JavaScript but has no additional effect, since
`.replace()`/`.replaceAll()` already say "first match" vs. "every
match" explicitly, the same distinction `g` controls implicitly in JS.
Any other flag letter is a compile-time error. `\w`/`\d`/`\s`/`\b` work
as expected (glibc's `regcomp()` supports them as GNU extensions), but
there are no capture groups, backreferences, or non-greedy quantifiers
(POSIX ERE's own limits).

A pattern/flags that aren't known until runtime (built from a variable
or a template) can't use the literal syntax — the global `regex(pattern,
flags)` function is still available for that case, the same split
JavaScript itself has between a `/pattern/` literal and `new
RegExp(...)`:

```festina
text userPattern = someInput()
regex dynamic = regex(userPattern)
```

Compiled fresh every time it's evaluated (both forms) — no caching by
pattern text.

## Graphics

```festina
drawRect(0, 0, 100, 100)
drawCircle(50, 50, 25)
drawText('Hello', 20, 20)

img profile = loadImage('profile.png')    // PNG only
drawImage(profile, 0, 0)

log(`canvas is ${clientWidth}x${clientHeight}`)

on click(x:int, y:int)  { ... }
on mouse(x:int, y:int)  { ... }
on key(key:text)        { ... }
on resize()             { ... }
on close()               { ... }
```

A real on-screen X11 window (via Cairo's Xlib backend), opened
automatically the first time a program draws something, reads
`clientWidth`/`clientHeight`, or declares one of the five event
handlers above — `loadImage()` alone does *not* open a window (decoding
a PNG needs no display). Undecorated, starts at 800×600, everything
draws in solid black (no color argument in any of claude.md's own
examples). After the entry file's top-level code finishes, if a window
was opened, the process blocks handling redraws/input until the window
closes.

## Timers

```festina
void func tick() {
    log('tick')
}

int id = setInterval(tick, 500)
setTimeout(tick, 1000)
clearInterval(id)          // clearTimeout()/clearInterval() are interchangeable
```

JS-style scheduling. The program keeps running as long as a `setTimeout`
is pending or a `setInterval` is uncleared — exactly like an uncleared
JS interval keeping a process alive. Combines with graphics: if a
program uses both, one event loop multiplexes X11 events and timer
deadlines together so neither blocks the other.

## Audio

```festina
aud music = loadAudio('music.wav')   // WAV, 16-bit PCM only
music.play()
music.stop()
music.isPlaying()                     // true the instant play() returns,
                                       // false the instant stop() returns
```

Plays through a real ALSA output device on a background thread, so
playback doesn't block the rest of the program. Calling `play()` again
while already playing restarts from the beginning.

## Imports

```festina
import database.f
import graphics.f
```

Resolved recursively before compilation into one merged compilation
unit; a file is never imported more than once even if multiple files
depend on it; errors still point at the file a statement actually came
from.

## `log()` / `fail()`

```festina
log(value)     // prints any primitive to stdout, newline-terminated
fail('message')  // prints to stderr, exits(1)
```

## Error format

Compile errors are `file:line:column: error: message`, e.g.:

```
main.f:12:5: error: condition must be bool, found text
```

See `tests/test_semantic_errors.py` for the full set of categories.

## Examples

[`examples/`](examples/) has a full set of small, runnable programs
exercising everything above, including a real playable game
(`tic_tac_toe.f`) — see the README's "See it in action" section for the
index, or just `bin/festina run examples/<name>.f`.
