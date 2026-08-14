# API Reference

The Festina language and standard library, as implemented today. For
narrative introductions and rationale see the individual sections below;
for exactly what's implemented vs. not (and known caveats), see
[`tests/CONTRACT.md`](tests/CONTRACT.md); for the full target-language
spec this compiler is built against, see [`claude.md`](claude.md).

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

```bash
bin/festina program.f -o program   # compile
bin/festina program.f --emit-llvm  # print LLVM IR instead of linking
./program                          # the result needs neither Python
                                    # nor festina/ to run
```

A program needs no `main()` — top-level statements in the entry file run
in order, after every import is resolved and every declared table's
schema is synced.

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
struct    -- user-declared record type
table     -- a struct that's also backed by a SQLite table
img       -- an image loaded via loadImage() (opaque handle)
aud       -- an audio clip loaded via loadAudio() (opaque handle)
regex     -- a compiled pattern from regex() (opaque handle)
```

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
    log(i)
}

while condition {
    ...
}
```

Conditions must be `bool` — no implicit truthiness from `int`/`text`/etc.
No `break`/`continue` (undefined by the spec); `return` from the
enclosing function is the only documented way out of a loop early.
Postfix `++`/`--` work on any mutable `int` variable.

## Strings

```festina
text greeting = `Hello, ${name}!`     // template literals
text a = 'room 42'.replace('room', 'suite')
text b = 'a1b2c3'.replaceAll(regex('[0-9]'), '-')
bool matched = regex('[0-9]+').test('room 42')
text found = 'room 42'.match(regex('[0-9]+'))   // null if no match
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

## Arrays

```festina
arr[int] numbers = [1, 2, 3]
arr[arr[int]] matrix
log(numbers.length)
numbers[0] = 10
```

Not bounds-checked; data is never freed (no GC yet — see
[todo.md](todo.md)).

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

## Regex

```festina
regex digits = regex('[0-9]+')            // POSIX extended regex
regex ci = regex('^hello$', 'i')          // 'i' = case-insensitive, the only flag
digits.test('room 42')                     // -> bool
'room 42'.match(digits)                    // -> text or null
'a1b2'.replace(digits, 'x')                // first match only
'a1b2'.replaceAll(digits, 'x')             // every match
```

No capture groups, backreferences, or non-greedy quantifiers (POSIX
ERE's own limits). Compiled fresh at every `regex()` call site — no
caching by pattern text.

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
index, or just `bin/festina examples/<name>.f -o out && ./out`.
