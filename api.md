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
| `festina compile entry.f -o out` | Compile to a native executable at `out` (default: `entry`'s own filename without `.f`). `--emit-llvm` prints LLVM IR to stdout instead of linking. `--cc` picks the C compiler/linker (default: whichever of `clang`/`gcc`/`cc` is found first). `--target=wasm32-wasi` cross-compiles to a standalone `.wasm` binary instead — see [wasm.md](wasm.md) for setup, usage, and limitations (graphics/audio aren't available under WASI). |
| `festina run entry.f` | Compile to a throwaway temp executable and run it immediately — stdin/stdout/stderr inherited directly (not captured), so an interactive program (graphics/audio/timers) behaves exactly like a normal compile-then-run. Exits with the *compiled program's own* exit code, so `festina run x.f && ...` composes the same way `go run`/`cargo run` do. The temp binary is always cleaned up afterward. `--target=wasm32-wasi` runs the compiled `.wasm` through Node's built-in WASI support instead of executing it directly. |
| `festina doctor` | Checks every dependency the compiler itself needs (a C compiler, `pkg-config`, sqlite3/cairo-xlib/alsa dev headers, `libLLVM`) and reports what's missing and how to install it — the same install hints a real compile failure would give (`claude.md #59`), just checked proactively instead of only on failure. Also reports whether `festina` itself is resolvable on `PATH`, and if not, exactly how to add it (the checkout's `bin/` directory, or a packaged binary — see [setup.md](setup.md)). Exits 0 if every *required* dependency is present — graphics/audio are optional, since a compiler that can't build a graphics program is still a fully working compiler for everything else (see [security.md](security.md#slim-binaries)). |
| `festina doctor --fix` | Same report, then actually fixes what it found instead of leaving the printed hint for a human to act on by hand: installs whatever dependencies are missing (required and optional both) via the detected package manager — `apt` on Linux, Homebrew on macOS, MSYS2's `pacman` on Windows — and, if `festina` itself isn't resolving on `PATH`, adds it (a symlink for a packaged binary, an `export PATH=...` line appended to `~/.bashrc`/`~/.zshrc` for a checkout, `setx` on Windows). Prints the exact command/change first and asks for confirmation (`--yes`/`-y` skips that, for every prompt this can raise); refuses to guess for any other package manager, overwrite something unrelated already on disk, or run non-interactively without `--yes`, rather than doing nothing or making a change nobody agreed to. The exit code reflects the dependency side only — not being on `PATH` has never been a required check. |
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
[security.md](security.md#slim-binaries)). `libsqlite3` is always
available (statically linked when possible — see
[setup.md](setup.md#static-linking-sqlite3)), since automatic database
support (`table`, `sqlite()`) is always on.

## Types

```text
int       -- 64-bit signed integer
float     -- 64-bit floating point (IEEE 754 double)
bool      -- true / false, no truthy/falsy coercion from anything else
text      -- UTF-8 string
blob      -- a file's bytes, loaded from a path (`blob save =
             'slot1.dat'`); see the Files section below
arr[T]    -- homogeneous array of any of the above, a struct, or a
             declared table's row type
map[T]    -- text-keyed map of any of the above except arr[T]/map[T]
             itself (see the Maps section below)
struct    -- user-declared record type
table     -- a struct that's also backed by a SQLite table
img       -- an image, declared from a path (`img hero = 'hero.png'`)
aud       -- an audio clip, declared from a path (`aud hit = 'hit.wav'`)
regex     -- a compiled pattern from a /pattern/flags literal or regex() (opaque handle)
color     -- a canvas color, declared from a literal (`color red = 'red'`)
font      -- a canvas font, declared from a literal (`font body = '13px arial'`)
```

`color` and `font` are resolved by the compiler at the declaration that
names them — see [Drawing style](#drawing-style). A `color` is a packed
integer and a `font` is a pointer to a constant in the binary, so
neither is reference-counted and neither costs anything at runtime.

Every type may hold `null` (`bool x = null` included — see "Division and
modulo by zero" below for how `int`/`float`/`bool` each represent it
internally; comparing a null `int`/`bool` against the `null` literal
with `==`/`!=` works as expected, but a null `float` never compares
equal to `null` via `==`, even to itself — it's a real NaN under the
hood, and IEEE-754 NaN comparisons are always false).

`int` and `float` mix freely in any binary operator — arithmetic,
comparison, or equality — with the `int` side implicitly promoted to
`float`, as though `.toFloat()` had been written on it:

```festina
int a = 5
float b = 2.5
float c = a + b          // 7.5 -- a is promoted to float automatically
bool less = a < b         // false
```

`/` (division) always returns `float`, even when both operands are
`int` — the one operator that promotes unconditionally, not just when
mixed:

```festina
int x = 10
int y = 3
float z = x / y           // 3.33333 -- not 3
```

Every other arithmetic operator (`+`, `-`, `*`, `%`) only promotes when
the two operands actually differ — `int + int` still returns `int`.
Declaring a variable as `int` from a genuinely `float`-typed expression
(mixed or from `/`) is still an ordinary type mismatch — the promotion
changes what an expression evaluates to, not what a declared type
accepts:

```festina
int bad = a + b            // compile error -- a + b is float
```

The only way back from a `float` to an `int` is still the rounding
four (`Math.floor`/`ceil`/`round`/`trunc`) — `int.toFloat()` is still
the one-directional `int` → `float` conversion, now mostly redundant
with the implicit promotion above but still useful for forcing a
result to `float` explicitly, e.g. inside a template literal.

```text
// float -> int (the rounding four)
Math.floor(x)   Math.ceil(x)   Math.round(x)   Math.trunc(x)

// float -> float
Math.sqrt(x)    Math.abs(x)    Math.exp(x)
Math.sin(x)     Math.cos(x)    Math.tan(x)
Math.asin(x)    Math.acos(x)   Math.atan(x)
Math.log(x)     Math.log2(x)   Math.log10(x)

// (float, float) -> float
Math.pow(a, b)  Math.min(a, b)  Math.max(a, b)  Math.atan2(y, x)

// no arguments -> float
Math.random()   // in [0, 1)

// constants
Math.PI   Math.E
```

Only the rounding four return `int`; everything else returns `float`,
because "which integer" and "which real number" are different questions
— `Math.sqrt(2.0)` is a float.

`Math.random()` is seeded once from the clock and is suitable for
gameplay and sampling — **not** for anything security-related. It
returns a value in `[0, 1)`, so `Math.floor(Math.random() * n)` is
always a valid index.

Division/modulo by zero return `null` (for both `int` and `float`)
rather than crashing:

```festina
float divided = 10 / 0   // null -- / always returns float
int remainder = 10 % 0    // null -- % still returns int for two ints
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

No `var`/`let` — every declaration states its type.

**Functions are first-class values** — a bare function name (not
called) is a value of type `func[paramTypes]:returnType`, usable
anywhere a value can go: a variable, a function argument, a struct
field, an array element, a map value. Calling THROUGH one of those —
not just the function's own original name — works exactly like calling
the function directly:

```festina
void func greet(name:text) { log(name) }

func[text]:void cb = greet
cb('world')                              // "world"

void func apply(fn:func[text]:void, arg:text) { fn(arg) }
apply(greet, 'hi')                       // "hi"

struct Handler { onEvent:func[text]:void }
Handler h
h.onEvent = greet
h.onEvent('yo')                          // "yo"

int func inc(x:int) { return x + 1 }
arr[func[int]:int] transforms = [inc]
log(transforms[0](5))                    // 6
```

`func[]:void` is a zero-argument, void-returning function type; `null`
is a valid value of any func type too. There are no closures — a
function's own type never captures anything from where it's declared,
so a `func[...]:...` value is always just a plain reference to one of
this program's own top-level (or nested, see below) function
declarations, nothing more. `setTimeout`/`setInterval`'s own callback
argument is unaffected by any of this: it's still the bare name of a
zero-parameter, `void`-returning function specifically, not an
arbitrary `func[...]:...`-typed expression.

**Arrow functions** — `returnType (params) => expr` — are an anonymous
function VALUE, compiling to an ordinary function with a compiler-
generated name; the arrow expression itself evaluates to a
`func[...]:...` reference to it, usable anywhere the plain-function
examples above are:

```festina
func[text]:void cb = void (arg:text) => log(arg)
cb('world')                                        // "world"

func[int]:int sq = int (x:int) => x * x
log(sq(7))                                         // 49

void func apply(fn:func[text]:void, arg:text) { fn(arg) }
apply(void (arg:text) => log(arg), 'hi')           // "hi"
```

A `void`-returning arrow function's body is a plain expression, run
for its side effects with its own value discarded (there's no `return`
inside a void function's body to write, arrow or not); a non-void
one's body IS its return value, no `return` keyword needed. Arrow
functions have no closures either, for the identical reason plain
functions don't: `void (arg:text) => log(`${arg} ${x}`)` compiles
correctly only if `x` is a top-level global (itself visible to every
function already) — a genuinely local variable from wherever the arrow
expression is written is not reachable from inside it.

**Functions are hoisted** — every function's name and signature exists
everywhere in the program, so calling one from above its own
declaration (including mutual recursion between two functions, each
necessarily calling the other before its own declaration) is not an
error:

```festina
log(greet('world'))    // fine -- greet is declared below

text func greet(name:text) {
    return 'Hello, ' + name
}
```

A function can also be declared nested inside an `if`/`while`/`for`
block, or inside another function — wherever it's written, it's still
one single, ordinary, globally-callable function (there's no
lexical scoping/closures for functions to begin with), so a call to it
works regardless of where the call site sits relative to that nested
declaration.

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
text b = 'a1b2c3'.replace(/[0-9]/g, '-')   // 'g' = every match
bool matched = /[0-9]+/.test('room 42')
text found = 'room 42'.match(/[0-9]+/)   // null if no match

arr[text] words = sentence.split(' ')    // or a regex: .split(/\s+/g)
sentence = words.join('\t')              // join works on text/int/float/bool arrays
```

`split` follows JS: empty pieces between adjacent separators are kept
(`'a,,b'.split(',')` has three pieces), a separator at the edge yields
an edge empty, an empty-match regex splits between characters, and an
empty text separator splits per UTF-8 code point. `join` renders a
`null` element as an empty string (`[1, null, 3].join('-')` is
`'1--3'`), also JS's choice.

### Parsing an int

```festina
int n = '42'.toInt()          // 42
int m = '  -17abc'.toInt()    // -17 -- leading whitespace, sign, trailing garbage all ok
int bad = 'nope'.toInt()      // null -- not a compile error, not a crash
if bad == null {
    fail('not a number')
}
```

`toInt()` follows JS's `parseInt()`: skips leading whitespace, reads an
optional `+`/`-`, then digits until the first non-digit (or the end of
the text) — whatever comes after the digits is ignored, not an error.
Returns `null` if no digits were found at all. A literal receiver
(`'42'.toInt()`) is computed entirely at compile time; nothing is
emitted to parse it at runtime.

### Indexing a character out

```festina
text s = 'hello'
log(s[0])              // h
log(s[10])              // null -- out of range, not a crash
log(s[-1])              // null -- negative, not a crash
```

`s[i]` reads the `i`-th UTF-8 **code point** (not byte — a multi-byte
character like `é` is one index, matching `.length`'s own code-point
count), as a fresh `text`. Unlike [array indexing](#indexing-is-not-bounds-checked),
this is always bounds-checked: an out-of-range or negative index
answers `null` rather than reading past the buffer. Read-only —
`s[0] = 'x'` is a compile-time error, the same way `environment.NAME =
...` is.

## Logging and rendering

`log()` and `${}` interpolation accept any value that has a text form —
a non-text value compiles as its `.toText()`:

- **`int` / `float` / `bool`** — the number, `true`/`false`, or `null`.
- **structs, table rows, arrays, maps** — JSON-like:

```festina
struct P { id:int  name:text  xs:arr[int] }
P p
p.id = 7
p.name = 'x'
p.xs.push(1)
log(p)                     // {"id":7,"name":"x","xs":[1]}
log(`state: ${p.xs}`)      // state: [1]
text json = p.toText()     // the explicit spelling of the same rendering
```

Text is escaped JSON-style; a `null` text/element renders as `null`; a
table-row column the query never selected is **omitted** (what
`JSON.stringify` does for `undefined`); a database NULL renders as
`null`. An opaque handle inside a container (a `blob`/`img`/`aud`/`regex`
field) renders as a placeholder like `"<blob>"`, or `null` when unset. A
cyclic value truncates at depth 32 instead of crashing. An **unassigned**
scalar field renders its zero value (per the zero-value rule); a field
explicitly assigned `null` renders `null`.

- **`blob`** — the contents, via its own `.toText()`: a blob is very
  often a text file. (A binary blob renders its bytes up to the first
  NUL — the same thing its explicit `.toText()` does.) A blob *field
  inside a rendered container* still shows as the `"<blob>"`
  placeholder, since embedding a whole file mid-JSON would drown the
  structure the rendering exists to show.

```festina
blob f = 'notes.txt'
log(f)                     // the contents
log(`config: ${f}`)        // interpolates the contents
```

- **`img`, `aud`** — a **compile error**: neither has a text form, and
  silently printing a placeholder would hide a mistake the type system
  can catch.

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

An unassigned field reads as its zero value, and that includes a field
whose own type is a `struct`, `arr[T]`, or `map[T]` — reaching through
one before anything was assigned to it gives you a real, empty value
rather than an error:

```festina
struct Inner { n:int }
struct Bag   { inner:Inner  xs:arr[int]  m:map[int] }

Bag b
log(b.inner.n)      // 0
log(b.xs.length)    // 0
b.xs.push(1)        // works -- the array is created on first reach
b.m['k'] = 9
```

The value is created once, on first reach, and stays — the read above
and the `push` below it are talking about the same array.

### A struct can name itself

A field may have the type of the struct it is declared in, or the type
of a struct declared further down the file. Declaration order does not
matter:

```festina
struct Node {
    n:int
    next:Node
}

Node head
head.n = 1
head.next.n = 2       // auto-vivified, same as any other struct field
head.next.next.n = 3

Node cursor = head
for int i = 0, i < 3, i++ {
    log(cursor.n)
    cursor = cursor.next
}
```

Linked lists, trees and parent pointers all work, and are reclaimed
automatically like any other struct — **including cycles**. Reference
counting alone can never free a value that points back at itself (the
loop keeps its own count above zero forever), so for types that *can*
form a cycle the compiler adds a cycle detector: when such a value is
released but still referenced, the runtime checks whether what remains
is only the cycle holding itself, and frees it if so.

```festina
Node a
a.n = 7
a.next = a      // reclaimed when `a` goes away, cycle and all
```

A cycle something still points at is never touched — parent pointers,
rings and doubly-linked structures stay valid for exactly as long as
anything outside them can reach them. Cycles through containers
(`kids:arr[Tree]` with a `parent:Tree` back-pointer, a `map` of peers)
collect the same way. Programs whose types cannot form a cycle carry
none of this machinery.

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
previous buffer every time instead of accumulating them.

Query results are reclaimed too: the rows an `arr[Table]` holds, and
each row's own text columns, are freed when that array is — so a
program that queries repeatedly no longer grows without bound. A single
row read out of one (`People p = rows[0]`) borrows from the array
rather than owning a copy, so it stays valid exactly as long as the
array does.

`img`, `aud` and `regex` handles are reference counted exactly like
structs: every binding — aliased, escaping, or a `/pattern/` literal's
— is released when it goes away, and the surface, decoded clip, or
compiled automaton is destroyed when the last reference drops. `img b
= a` shares one handle; `free a` afterwards is a decrement and `b`
stays usable. A `/pattern/` literal's process-lifetime compilation is
immortal, so every release that reaches it is a safe no-op.
The one thing not reclaimed is text globals at process exit, where the
operating system reclaims everything anyway.

## Arrays

```festina
arr[int] numbers = [1, 2, 3]
arr[arr[int]] matrix
log(numbers.length)
numbers[0] = 10
```

Memory is reclaimed automatically — see "Structs" above for the full
picture (non-escaping locals reclaimed at scope-exit, escaping values
reference counted).

**`arr[img]`/`arr[blob]`/`arr[aud]` load each element from a path**,
the array-typed counterpart of `img sprite = 'sprite.png'`:

```festina
arr[img] brushes = ['./brush1.png', './brush2.png']
arr[blob] saves = ['slot1.dat', 'slot2.dat']
```

An element may also already be a value of the array's own media type
(reusing an existing `img`/`blob`/`aud`, aliased rather than reloaded) —
mixing the two in one literal is fine.

### Indexing is not bounds-checked

**`numbers[i]` is a raw memory access, and keeping `i` in range is
yours to guarantee.** This is the one place Festina hands you a loaded
gun, and it is deliberate: an index is checked in the hot path of every
loop a game writes, and the check would cost more than the language is
willing to spend. Nothing about it is soft.

- **Reading past the end** returns whatever bytes follow the array. Not
  `null`, not a zero, not an error — arbitrary heap contents, different
  on each run.
- **Writing past the end** corrupts the heap. Confirmed under
  AddressSanitizer as a genuine heap-buffer-overflow. It may crash
  immediately, or corrupt an unrelated value and crash somewhere else
  much later, or appear to work.
- **A negative index** is the same, backwards.
- **`.length` is always right**; nothing else is checked against it.

So guard the index yourself:

```festina
if i >= 0 && i < xs.length {
    log(xs[i])
}
```

This applies only to `arr[T]` indexing. A missing `map[T]` key answers
`null` (see [Maps](#maps)); `pop()`/`shift()` on an empty array answer
`null` (see below); and `splice()` clamps its own range. Indexing is the
only unchecked operation in the language.

Arrays grow, and are searched, through the methods in
[Growing arrays](#growing-arrays) below.

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
collection, not a large-scale data structure. Also grows by exactly one
entry per insert internally, not amortized — see `amor map[T]` below
if that matters for a specific map.

### `amor` — amortized-growth maps

```festina
amor map[int] hitCounts = {}
const amor map[text] labels = {'ok': 'success'}   // composes with const

int i = 0
while i < 10000 {
    hitCounts[`key${i}`] = i
    i = i + 1
}
```

`amor map[T]` — an "amortized map" — is `map[T]` with a different
internal growth strategy: doubling capacity as needed instead of
growing by exactly one entry per insert, so a long run of inserts costs
O(log n) reallocations instead of O(n). Same literal syntax, same
indexed get/set, `.forEach()`, `delete`, and `.toText()`/JSON rendering
as plain `map[T]` — `amor` only changes how it grows internally, not
what it does. **Requires an initializer** (`amor map[int] m` with no
`= ...` is a compile error) — unlike plain `map[T]`, which can start
implicitly empty, an amortized map's own declaration always needs a
real value to store. Composes with `const` (`const amor map[T] m =
...`); as a struct field (`m:amor map[int]`), no initializer is needed
or possible, the same as any other struct field.

`amor arr[T]` parses and type-checks (also composing with `const`) but
does not yet change how an array grows — `push()`/`unshift()`/etc.
still realloc to exactly the new size either way, identically to plain
`arr[T]`. Real array amortization is a natural follow-up, not yet
built.

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

### Storing images, audio and files

A column may be an `img`, an `aud` or a `blob`. SQLite stores each as a
**BLOB**:

```festina
table Music {
    name:text
    file:aud
}

aud track = 'adventure.mp3'
sqlite('INSERT INTO Music (name, file) VALUES (?, ?)', ['theme', track])

arr[Music] rows = sqlite('SELECT * FROM Music')
rows[0].file.playLoop(0)          // straight out of the database
```

What's stored is the asset's **own encoded bytes**, so a round trip is
byte-identical — an MP3 stays an MP3 rather than becoming a much larger
WAV, and a JPEG stays a JPEG rather than being re-encoded as PNG.
Reading a row decodes it back into a real handle, so the value that
comes out behaves exactly like one loaded from a file.

The one case with no source bytes is an image you built rather than
loaded — a `clip()` or `resize()` result. Those are encoded as PNG on
demand, which is lossless.

A `blob` column works the same way and is the general case: any file,
not just the two the language decodes. See
[Blobs in the database](#blobs-in-the-database) for what a blob read back
out of a column can and cannot do.

Binding is by value: the parameter is copied into the database as the
statement runs, so nothing is retained afterwards and the asset stays
yours.

### Query performance

Two things happen automatically. A `sqlite()` call whose SQL is a
**string literal** is prepared once and reused — parsing and planning
happen on the first call only, so a query in a loop pays for binding and
stepping, nothing else. (Dynamic SQL — a template or a variable — is
prepared per call, since it can differ each time.) And the database
opens in **WAL mode** with `synchronous=NORMAL`, the standard
application configuration: measured, 20,000 inserts dropped from 16.7s
to 0.3s. A transaction survives an application crash; only an OS crash
or power loss can lose the most recent commits — never corrupt the file.

### Partial queries and `undefined()`

Result columns are matched to the table's declared columns **by name**
(case-insensitively), not by position — so a query may select any
subset of columns, in any order, and every value lands where it
belongs. A column the query didn't mention reads as `null`.

But "the query never asked" and "the database said NULL" are different
facts, and `row.undefined('col')` tells them apart:

```festina
table examples { id:int  name:text }
arr[examples] data = sqlite('select id from examples')

if data[0].name == null && data[0].undefined('name') {
    // name is null because it wasn't selected -- not because the
    // database has no name for this row
}
```

`undefined('col')` is `true` when the column wasn't in the result set
(or was `delete`d off the row), `false` when the database genuinely
returned a value or a NULL. Asking about a column the table doesn't
declare fails the program — that's a typo, and `true` or `false` would
both bury it.

A `SELECT ... AS alias` renames a column *away* from its declared name,
so an aliased column simply doesn't match; alias *to* a declared name to
remap a computed value into a column deliberately.

### Structs as query targets

A query doesn't have to land in a table's row type. Any **struct** whose
fields are queryable types (`int`/`float`/`bool`/`text`/`blob`/`img`/
`aud`) can receive a result — name its fields after the result's own
column names:

```festina
struct data {
    whatever:int
}
arr[data] query = sqlite('select id as whatever from examples')
log(query[0].whatever)
```

This is the shape for aliased columns, JOINs, and computed results — a
table's declared columns can never chase a query's aliases, and a
`table` declaration always *creates* a table, which a result-only shape
has no business doing:

```festina
struct summary { total:int  biggest:text }
arr[summary] agg = sqlite(
    'select count(*) as total, max(name) as biggest from examples')
```

The elements are **ordinary structs** — refcounted, aliasable,
`free`-able, their fields assignable and `delete`-able, exactly as if
built by hand. A field the result didn't produce reads `null`. One
consequence: `undefined()` is a table-row method and doesn't exist here,
since an ordinary struct carries no record of which query it came from.

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

## Command-line arguments

```festina
log(argv.length)
log(argv[0])          // the program's own path, same as C's argv[0]
if argv.length > 1 {
    log(`first arg: ${argv[1]}`)
}
```

`argv` is a real `arr[text]` global, populated from the process's own
OS argc/argv before any top-level statement runs — no declaration
needed. Unlike `environment`, it's an ordinary mutable array once
populated: `argv.push(...)`, `argv[i] = ...`, and every other
[array](#arrays)/[growing array](#growing-arrays) operation work on it
normally. Works under `--target=wasm32-wasi` too (WASI has its own
argc/argv), but this checkout's own runner (`run_wasi.mjs`) only ever
passes the compiled module's own path through, so `argv` there is
always a single-element array — see [wasm.md](wasm.md).

## Regex

```festina
regex digits = /[0-9]+/                    // JS-style literal, POSIX extended regex underneath
regex ci     = /^hello$/i                  // 'i' = case-insensitive
regex all    = /[0-9]/g                    // 'g' = replace every match
regex both   = /test/gi                    // flags combine

digits.test('room 42')                     // -> bool
'room 42'.match(digits)                    // -> text or null
'a1b2'.replace(/[0-9]/, 'x')               // 'a1b2' -> 'axb2'  (first match)
'a1b2'.replace(/[0-9]/g, 'x')              // 'a1b2' -> 'axbx'  (every match)
```

`flags` immediately follows the closing `/`, no space (`/pattern/flags`).
Only `i` and `g` are accepted; any other flag letter is a compile-time
error. `\w`/`\d`/`\s` (and their negations) and `\b` work as expected
on every platform — the runtime expands them to portable POSIX classes
before compiling, so they no longer depend on glibc's GNU extensions —
but there are no capture groups, backreferences, or non-greedy
quantifiers (POSIX ERE's own limits). Inside `[...]` a backslash is a
literal, per POSIX.

### What `g` does, and what it doesn't

`g` affects `.replace()` and nothing else.

```festina
'a-b-c'.replace(/-/g, '_')     // 'a_b_c'
'a-b-c'.replace(/-/, '_')      // 'a_b-c'
'a-b-c'.replace('-', '_')      // 'a_b-c' -- a text search has no flags
```

A plain-text search replaces the first match only, exactly like JS's
`String.prototype.replace` with a string argument. There is no
`.replaceAll()` — replacing every occurrence is spelled `/search/g`.

It deliberately does **not** do two things JS's `g` does:

- **`.test()` does not become stateful.** In JS a `/g` regex carries a
  `lastIndex` that advances on each `.test()`, so the same test against
  the same string returns `true`, then `false`. Here it returns the same
  answer every time.
- **`.match()` still returns `text`, not an array.** JS's `/g` changes
  `.match()`'s return type. A return type can't depend on a flag that
  `regex(pattern, flags)` only knows at run time, so `g` is ignored by
  `.match()`.

A pattern/flags that aren't known until runtime (built from a variable
or a template) can't use the literal syntax — the global `regex(pattern,
flags)` function is still available for that case, the same split
JavaScript itself has between a `/pattern/` literal and `new
RegExp(...)`:

```festina
text userPattern = someInput()
regex dynamic = regex(userPattern)
regex globalDynamic = regex(userPattern, 'g')   // 'g' works here too
```

The flag belongs to the compiled pattern, not to the call site, so both
spellings behave identically.

### Literals are compiled once; `regex()` is memoized per call site

A `/pattern/` literal is compiled the first time its line is reached and
cached for the life of the process. A `regex(pattern, flags)` call is
**memoized per call site**: each call compares its actual pattern and
flags against what that site compiled last time, reuses the compilation
when they match, and recompiles when they differ. A pattern that varies
per call is never served a stale automaton — the check is against the
runtime strings, not the source location.

So the steady-state cost matches the literal's. Measured over 200,000
iterations, `regex('[0-9]+').test(s)` inside a loop runs in ~15 ms, the
same as `/[0-9]+/.test(s)` — before the memo it was ~367 ms (one full
`regcomp()` per iteration, roughly 24x). A loop that genuinely
*alternates* patterns through one call site still pays a recompile per
change, since the memo keeps only the most recent compilation per site;
bind each pattern to its own variable outside the loop if that matters.

## Graphics

```festina
drawRect(0, 0, 100, 100)
drawRect(0, 0, 100, 100, blue)           // optional trailing color -- this call only
drawPixel(10, 10)                        // one pixel, current fillStyle
drawPixel(10, 10, blue)                  // one pixel, this call only
drawCircle(50, 50, 25)
drawText('Hello', 20, 20)

img profile = 'profile.png'              // PNG or JPEG
drawImage(profile, 0, 0)
log(`${profile.width}x${profile.height}`)

saveCanvas('screenshot.png')             // -> bool; writes what you drew
img snap = saveCanvas()                  // -> img; a snapshot, no file written

render()                                  // put the canvas on screen
clearCanvas()                             // erase everything to transparent
clearRect(10, 10, 40, 40)                 // erase one region to transparent
clearCircle(50, 50, 25)                   // erase a circular region to transparent
clearPixel(10, 10)                        // erase one pixel to transparent

log(`canvas is ${clientWidth}x${clientHeight}`)

log(`screen is ${screenWidth}x${screenHeight}`)  // the physical display, read-only
setClientWidth(1024)                             // resizes the canvas (and window, if open)
setClientHeight(768)

on mouseDown(x:int, y:int) { ... }
on mouseUp(x:int, y:int)   { ... }
on mouse(x:int, y:int)     { ... }
on keyDown(key:text)       { ... }
on keyUp(key:text)         { ... }
on resize()                { ... }
on close()                 { ... }
```

**Drawing is offscreen. `render()` puts it on screen.**

Every drawing call paints an offscreen canvas that needs no display at
all. `render()` is the one call that shows it, opening a real X11 window
(via Cairo's Xlib backend) the first time it runs — undecorated, 800×600.
Declaring one of the seven event handlers opens a window too, since they
can't fire without one. After the entry file's top-level code finishes,
if a window was opened, the process blocks handling redraws/input until
the window closes.

That split means two useful things:

```festina
// No display needed. No window. Exits on its own.
fillStyle(brand)
drawRect(0, 0, 100, 100)
saveCanvas('chart.png')
```

```festina
// A frame: draw everything, then present once.
clearCanvas()
drawSprites()
render()
```

Batching matters — drawing used to blit the whole canvas per call, so a
frame of 2000 rectangles took ~1.6s. Behind one `render()` the same
frame takes ~1ms.

**A fresh or cleared canvas is transparent, not white** — matching the
HTML5 `<canvas>` model this otherwise mirrors. `clearCanvas`/`clearRect`/
`clearCircle`/`clearPixel` all clear to fully transparent, and a canvas
that's never been drawn on starts that way too:

```festina
drawRect(0, 0, 100, 100)
clearRect(20, 20, 20, 20)   // that region is now transparent
saveCanvas('sprite.png')    // a real alpha channel, usable as an asset
```

That transparency is real alpha in whatever `saveCanvas()` produces
(a file or the `img` snapshot both), not something flattened to a
solid colour — useful for drawing a sprite or icon with a transparent
background to compose elsewhere.

**`saveCanvas()` with no argument returns an `img` instead of writing a
file** — a snapshot of the canvas at that instant, not a live view of
it: drawing or clearing the canvas afterward never changes what the
snapshot holds.

```festina
drawRect(0, 0, 100, 100)
img snap = saveCanvas()
clearCanvas()
snap.save('before-clear.png')   // still has the rectangle
```

Nothing but `render()` and the event handlers needs a display —
`saveCanvas`, `clientWidth`/`clientHeight` and loading an image all
work headless.

**`screenWidth`/`screenHeight`** report the physical display's own
resolution — not the window's content size (that's `clientWidth`/
`clientHeight`), a window can be, and usually is, smaller than the
screen it's on. Both are read-only. Unlike `clientWidth`/`clientHeight`,
reading them still needs an X server (there's no window yet to answer
from, and no other way to ask "how big is the screen"), so this is one
of the few graphics reads that fails without a display.

**`setClientWidth(int)`/`setClientHeight(int)`** resize the canvas —
and the real OS window too, if one is already open. Both apply
immediately: `setClientWidth(400)` is followed by `clientWidth` already
reading `400`, not whatever it was a moment before. A non-positive size
is silently ignored. If a window is open, the resized content is
cleared to transparent (matching `clearCanvas`'s own behavior) and `on
resize` fires once per call:

```festina
render()
setClientWidth(1024)   // window resizes; on resize fires once
setClientHeight(768)   // fires again
```

### Mouse events

`on mouseDown` fires when a button goes down, `on mouseUp` when it comes
back up, and `on mouse` continuously while the pointer moves. All three
report the pointer position at the moment the event happened.

A click is a press *and* a release, and they are separate events for the
same reason `keyDown` and `keyUp` are: holding the button down and
moving before letting go is a drag, and the only way to see one is to
see both ends of it.

```festina
int startX = 0
int startY = 0

on mouseDown(x:int, y:int) { startX = x  startY = y }
on mouseUp(x:int, y:int)   { log(`dragged ${x - startX}, ${y - startY}`) }
```

Press and release report *different* coordinates whenever the pointer
moved in between — that difference is the drag. A program that only
wants "was clicked" can just use `on mouseDown` and ignore the release.

Which button was pressed is not reported; every button dispatches the
same handler.

### Keyboard events

`on keyDown` fires when a key goes down, `on keyUp` when it comes back
up. Both report the same name for the same physical key: a key that
types a character gives you that character (`'a'`, `'5'`, `' '`), and
anything else gives you X11's own name for it (`'Left'`, `'Escape'`,
`'Return'`, `'space'` is `' '`). So a release can always be matched
against the press that started it:

```festina
map[bool] held = {}

on keyDown(key:text) { held[key] = true }
on keyUp(key:text)   { held[key] = false }
```

**Holding a key fires one `keyUp`, when you actually let go.** X's own
auto-repeat would otherwise synthesize a release before every repeat,
which would make the pair useless for exactly the movement keys it
exists for; the runtime turns that off where the server supports it and
filters it out where it doesn't.

`keyDown` *does* repeat while a key is held — that is how text entry
works, and a program that only wants the first press can check whether
it has already seen that key go down without a matching up.

### Images

```festina
img sheet = 'spritesheet.png'             // PNG or JPEG
log(`${sheet.width}x${sheet.height}`)

img grass = sheet.clip(0, 0, 64, 64)     // a new 64x64 image
grass.resize(32, 32)                      // scaled in place
drawImage(grass, 100, 100)
grass.save('grass.png')                   // -> bool; see Saving bytes
```

A path declares the image, the same way it declares an `aud` — and, like
that one, it's a real load rather than a compile-time resolution, so the
path may be any text expression (`img hero = spriteDir + 'hero.png'`).
`save()`/`saveCopy()` write one back out; see
[Saving bytes to a path](#saving-bytes-to-a-path).

**PNG and JPEG.** The format is sniffed from the file's contents, not
its extension — an image out of a database column has no extension, and
an extension was never evidence of anything anyway. Loading needs no
display: decoding is pure computation, so a headless program can load,
clip, resize and `saveCanvas` without an X server.

| | |
|---|---|
| `img.width` / `img.height` | Current size in pixels, as `int`. |
| `img.clip(x, y, w, h)` | A **new** `img` holding that rectangle. The source is untouched, so one sheet can be clipped as many times as you like. |
| `img.resize(w, h)` | Scales the image **in place** — it changes the image itself, so every name for it sees the new size. |
| `img.drawRect(x, y, w, h[, color])` / `img.drawPixel(x, y[, color])` / `img.drawCircle(x, y, r)` / `img.drawText(text, x, y)` | The same four canvas-level drawing calls, painting onto **this image's own surface** instead. |

`clip` is the spritesheet operation: one PNG holding a grid of frames,
sliced into the individual images you draw.

```festina
img sheet = 'tiles.png'
arr[img] tiles = []
for int i = 0, i < 8, i++ {
    tiles[i] = sheet.clip(i * 32, 0, 32, 32)
}
```

A clip region reaching past the source's edge isn't an error — the
overlapping part is copied and the rest stays transparent, which is
normal at a sheet's right or bottom margin. A zero or negative width or
height *is* an error, since it could only ever produce an image nothing
can draw.

**Drawing onto an image** uses the same style state as the canvas
(`fillStyle`, `borderColor`, `lineWidth`, `changeFont`) and the same
optional trailing `color` on `drawRect`/`drawPixel` — but nothing else
about the canvas. No window is needed (an image's surface already
exists in full the moment the image does), and the canvas's own
`translate`/`rotate`/`scale` transform is never applied — an image is a
portable asset with its own local pixel coordinates, independent of
whatever the canvas's transform happens to be set to:

```festina
color red = 'red'
color blue = 'blue'
img icon = 'blank.png'
fillStyle(red)
icon.drawRect(0, 0, 16, 16)
icon.drawPixel(24, 8, blue)      // this pixel only -- fillStyle stays red after
icon.save('icon-with-border.png')
```

Because `resize` changes the image itself, two names for one image stay
in step:

```festina
img a = sheet.clip(0, 0, 32, 32)
img b = a
a.resize(8, 8)
log(b.width)      // 8 -- a and b are the same image
```

An image created in a function (from a path, or by `clip`) and never
stored outside it is released when that function returns, so slicing
frames inside a loop doesn't accumulate.

### Drawing style

```festina
color brand = '#4a90d9'
color line = 'gray'
font  body  = 'bold 20px serif'

fillStyle(brand)            // fills: drawRect, drawPixel, drawCircle, drawText
borderColor(line)           // outlines drawRect/drawCircle
lineWidth(4)                // border thickness, in pixels
changeFont(body)            // used by drawText and both measure calls
```

Style is set once and applies to every later draw — the same model the
HTML canvas uses. Defaults are black fill, no border, and 16px
sans-serif, so a program that never calls these draws exactly what it
did before they existed.

**`drawRect`/`drawPixel` take an optional trailing `color`** that
overrides `fillStyle` for that one call only — the current fill (a flat
color or an active gradient) is unaffected afterward:

```festina
fillStyle(brand)
drawRect(0, 0, 20, 20)        // brand
drawRect(30, 0, 20, 20, line) // line, just this once
drawRect(60, 0, 20, 20)       // brand again
```

`borderColor`/`lineWidth` still apply as configured either way — only
the fill is a per-call override, not the border.

> **Colors and fonts must be declared.** Anything other than raw RGB
> numbers has to be a `color` or `font` declaration first:
>
> ```festina
> color red = 'red'      // then: fillStyle(red)
> font  body = '14px'    // then: changeFont(body)
> ```
>
> `fillStyle('red')` and `changeFont('14px')` do **not** work. The
> declaration is where the compiler resolves the name, once — after
> that a `color` is just a packed integer and a `font` is a pointer to a
> constant, so using either costs nothing.
>
> **If a color is chosen dynamically, use `fillStyle(r, g, b)`** — see
> [Computing a color or font at runtime](#computing-a-color-or-font-at-runtime)
> below. There is no way to turn a runtime `text` value into a `color`
> or a `font`, and attempting it is a compile error that says so.

#### The `color` type

```festina
color red   = 'red'
color brand = '#4a90d9'
color ghost = 'none'
```

A color literal is any of the **148 CSS color names** (`red`, `teal`,
`rebeccapurple`, `lightgoldenrodyellow`, …), a `#rgb` or `#rrggbb` hex
value, or `none`/`transparent`. Names are case-insensitive and `#abc`
expands to `#aabbcc`, both as in CSS.

The declaration is where the name is resolved: `color red = 'red'`
becomes the packed integer `0xFF0000` at compile time, so nothing parses
a color string while your program runs. A name the compiler doesn't
recognize is a compile error naming the value and its line — it can't
reach a running program, and it never silently falls back to black.

A `color` is an ordinary value after that: assign it, pass it to a
function, return one. It is a plain integer, so it is never
reference-counted and costs nothing to copy.

`none` works on both setters: as a fill it leaves a shape's interior
untouched, so `borderColor` alone gives an outline-only shape; as a
border color it switches borders back off.

```festina
color none = 'none'
color ring = 'purple'

fillStyle(none)
borderColor(ring)
lineWidth(8)
drawCircle(200, 200, 60)    // a purple ring, nothing inside it
```

`borderColor` outlines shapes only, not the glyphs `drawText` draws.

#### The `font` type

```festina
font body  = 'arial 14px bold'   // all three parts
font same  = 'bold 14px arial'   // any order — identical result
font small = '14px'              // just the size; family/style unchanged
font mono  = 'monospace'         // just the family; size unchanged
```

A font literal takes the CSS/canvas shorthand with words in **any
order**, and any part may be omitted — `italic`/`oblique` set the slant,
`bold` the weight, a bare number or `<n>px` the size, and the first word
that is none of those is the family. An omitted part means "leave that
alone", which is what lets `font small = '14px'` change only the size.

Each distinct font compiles to a constant in the binary's read-only
data, so declaring one costs nothing at runtime and `changeFont()`
passes a single pointer. Identical fonts share one constant, so `body`
and `same` above are literally the same record. An empty literal
(`font f = ''`) is rejected — it says nothing, and is far likelier to be
a mistake than an intent.

### Computing a color or font at runtime

There is deliberately **no way to turn a runtime `text` value into a
`color` or a `font`** — resolution happens at the declaration, so the
declaration needs a literal. To choose either from values you compute,
use the explicit numeric forms, which are strictly more capable for that
job anyway (they take any `int` expression, where a color *name* could
only ever have named one of a fixed set):

```festina
fillStyle(r, g, b)                // each 0-255; a negative value means 'none'
borderColor(r, g, b)
changeFont(px, style, family)     // style/family may be null;
                                   // px <= 0 keeps the current size
```

```festina
// a gradient of swatches — the color is different every iteration
for int i = 0, i < 10, i++ {
    fillStyle(i * 25, 0, 255 - i * 25)
    drawRect(i * 40, 0, 36, 36)
}

// a font size that depends on runtime state
int size = 12 + level * 4
changeFont(size, 'bold', null)    // family left as-is
```

Passing a non-literal where a `color` or `font` is expected is a compile
error that points at these forms:

```text
error: a color must come from a literal, so the compiler can resolve it
once -- write `color name = '...'` and use `name`, or, to choose one at
runtime, use fillStyle(red, green, blue) with each component 0-255
```

### Paths

```festina
fillStyle(red)
beginPath()
moveTo(50, 50)
lineTo(150, 50)
lineTo(100, 140)
closePath()
fillPath()        // a filled triangle
```

| | |
|---|---|
| `beginPath()` | Starts a new path. |
| `moveTo(x, y)` / `lineTo(x, y)` | Move the pen / draw a straight segment. |
| `curveTo(cx1, cy1, cx2, cy2, x, y)` | A cubic bezier to `(x, y)`. |
| `closePath()` | Closes back to the start. |
| `fillPath()` / `strokePath()` | Paints the path with the current fill / border colour, and **ends** it. |

`fillPath` uses `fillStyle`; `strokePath` uses `borderColor` and
`lineWidth`. Both consume the path, as `fill()`/`stroke()` do on a
canvas — call `beginPath()` again for the next shape. Using `moveTo` and
friends with no path open is a clean error naming the missing
`beginPath()`.

### Transforms

```festina
saveState()
translate(400, 40)
rotate(30.0)          // degrees
scale(2.0, 2.0)
drawRect(0, 0, 60, 60)
restoreState()        // transform (and style) back as it was
```

A transform applies to everything drawn *after* it, until changed.
`resetTransform()` returns to the identity.

`saveState`/`restoreState` save the whole drawing state — transform,
colors, alpha, line width and font — matching the canvas `save()`/
`restore()` they mirror. A `restoreState()` with nothing saved is an
error rather than a silent no-op.

Rotation is in **degrees**. `Math.PI` is there if you'd rather work in
radians.

### Gradients and transparency

```festina
color a = 'red'
color b = 'blue'

fillLinearGradient(50, 300, a, 250, 300, b)   // start point, colour -> end point, colour
drawRect(50, 280, 200, 60)

fillRadialGradient(400, 300, 60, a, b)        // centre, radius, inner, outer
drawCircle(400, 300, 60)

fillAlpha(0.5)                                 // 0.0 transparent .. 1.0 opaque
```

A gradient replaces the flat fill until the next `fillStyle()`. Two
stops rather than an arbitrary list — that covers essentially every
gradient a program draws, and needs no separate gradient type.

### Text metrics

```festina
int w = measureTextWidth('Hello')
int h = measureTextHeight('Hello')
```

Both measure against the current `font` and return `int`. Neither opens
a window — text metrics depend only on the font, so they work in a
program that never draws, and with no X server at all.

`measureTextWidth` is the advance width (how far the pen moves), which
is what you want for laying strings out one after another — the same
thing the canvas `measureText().width` reports. `measureTextHeight` is
the inked height of *that string*, which is why it takes the text:
`'x'` is shorter than `'Xg'`. For a stable line height independent of
which letters appear, measure a string with both an ascender and a
descender.

## Files

A file is a `blob`. Declaring one loads the bytes at that path, and
keeps the path, so everything you can do to a file is a method on the
value that already knows which file it is:

```festina
blob notes = 'notes.txt'              // loads the bytes at that path

notes.write('hello')                  // -> bool (did it land?)
notes.append(' world')                // -> bool
text body = notes.toText()            // -> the bytes, as text
bool there = notes.exists()           // -> bool
notes.delete()                        // -> bool; deletes the FILE

notes.save()                          // -> bool; write the bytes to its path
notes.save('other.txt')               // -> bool; adopt that path, then write
notes.saveCopy('backup.txt')          // -> bool; write there, keep its own path
```

The path may be any text expression, like `img` and `aud`:
`blob save = saveDir + 'slot1.dat'`.

**Nothing here fails the program.** A path that can't be read gives you
an empty blob, and the writers return `false` on failure — a missing
file is something you test for rather than something that stops you, the
same treatment division by zero gets. That is also how you create a file
that doesn't exist yet: declare the blob and write to it.

```festina
blob fresh = 'new.txt'
log(fresh.exists())                   // false
fresh.write('now it does')
log(fresh.exists())                   // true
```

`toText()` hands back an ordinary owned `text`, so it composes with
everything else:

```festina
blob data = 'data.csv'
log(data.toText().replace(/,/g, ' | '))
```

**A blob is its contents, not its path.** `write()` and `append()`
update the bytes as well as the file, so `toText()` after a write
reports what you wrote. And `delete()` removes the file while leaving
the blob alone — "delete it but keep what it said" is expressible:

```festina
blob temp = 'scratch.txt'
temp.write('remember this')
temp.delete()
log(temp.exists())                    // false
log(temp.toText())                    // remember this
```

**Assigning a blob shares one handle, it does not copy.** Two names for
one file's contents; writing through either is visible through both.
Rebinding one of them releases its own reference, and the contents are
freed once nothing refers to them:

```festina
blob a = 'one.txt'
blob b = a                            // same handle, not a second load
a.write('changed')
log(b.toText())                       // changed

a = 'two.txt'                         // `a` moves on; `b` still holds one.txt
```

### Blobs in the database

A `blob` column stores the **bytes**, so binary content round-trips
byte-identically — the same treatment `img` and `aud` columns get:

```festina
table Saves { name:text  data:blob }

blob save = 'slot1.dat'
sqlite('INSERT INTO Saves (name, data) VALUES (?, ?)', ['slot1', save])

arr[Saves] rows = sqlite('SELECT * FROM Saves')
log(rows[0].data.toText())
```

A blob that came out of a column has bytes but **no path** — a path is
meaningful only on the machine that stored it. Its `exists()`,
`write()`, `append()` and `delete()` all answer `false` rather than
inventing a temporary file. `toText()` works as usual.

`save(path)` is how one gets to disk — see
[Saving bytes to a path](#saving-bytes-to-a-path) below, which is the
same method on `img` and `aud`.

```festina
arr[Saves] rows = sqlite('SELECT * FROM Saves')
blob back = rows[0].data
log(back.exists())                    // false -- no path
back.save('recovered.dat')            // now it has one
log(back.exists())                    // true
```

## Directories

```festina
bool created = mkdir('./temp')        // -> bool: true if IT created it
arr[text] names = ls('./temp')        // -> arr[text] of entry names
```

`mkdir(path)` answers `true` only if it actually created the directory
— `false` for every other outcome, including "it already existed", a
missing parent, or no permission. Like the file builtins, nothing here
fails the program:

```festina
mkdir('./temp')                       // true
mkdir('./temp')                       // false -- already there
```

`ls(path)` answers the directory's entry names (not full paths, and
never `.`/`..`) as `arr[text]`, in whatever order the OS hands them
back. A missing or unreadable directory answers an empty array rather
than failing:

```festina
arr[text] names = ls('./temp')
log(names.length)
log(ls('./nowhere').length)           // 0
```

## Running other programs

```festina
arr[text] cmd = ['/bin/echo', 'hello']
int status = exec(cmd)
log(status)                            // 0
```

`exec(args:arr[text]):int` spawns `args[0]` (searched on `PATH` the
same way a shell finds it) with the rest of `args` as its own argv,
**inheriting stdin/stdout/stderr directly** — it doesn't capture the
child's output, the same "not a sandbox, this really runs it" model
`sqlite()`/the file builtins already use for the filesystem. Blocks
until the child exits and returns its real exit code, or `-1` if the
process never started at all (executable not found, no permission) —
`-1` is never a code the child itself could produce, so it's
unambiguous. Not available under `--target=wasm32-wasi` — WASI has no
process model to spawn into — rejected at compile time rather than
failing at runtime; see [wasm.md](wasm.md).

## HTTP and WebSocket servers

```festina
openPort(8080)

on request(req:http) {
    if req.path == '/hello' {
        req.send('hello world')
        return
    }
    req.ok()
}
```

`openPort(port:int)` starts listening for HTTP connections on `port`;
`closePort(port:int)` stops. Neither fails the program — an already-open
port, a privileged or in-use one, or closing a port never opened are all
silent no-ops, the same "test, don't fail" convention `mkdir()`/`exec()`
already use. A program is free to open more than one port.

Every connection is serviced from a **single thread**, the same "one
thread total" model `setTimeout`/`setInterval` and graphics event
handlers already use — connections are multiplexed, not run in
parallel, so ordinary globals need no locking to read/write safely
across requests. The tradeoff: a slow `on request`/`on message` handler
delays every *other* connection's own turn. This is built for the kind
of small, script-shaped server this language already targets, not as a
general-purpose production server replacement.

### `on request(req:http)`

Fires once per incoming HTTP request, fully parsed (request line,
headers, and body already buffered — see [Limitations](#http-limitations)
below for what "fully parsed" doesn't include).

```festina
req.port                              // int -- the port this connection arrived on
req.method                            // text -- 'GET', 'POST', ...
req.path                              // text -- '/hello', never includes the query string separately
req.headers                           // map[text] -- header names lowercased; a repeated
                                       // header's last occurrence wins
```

All four are read-only — assigning to any of them is a compile-time
error.

**Responding** — exactly one of the following ends the request; calling
a second one on the same `req` is a silent no-op (never a crash, never a
double response):

```festina
req.ok()                              // 200, empty body
req.redirect('https://example.com')   // 302, Location header set
req.send(data, code, headers)         // see below
```

`req.send(data:any, code:int, headers:map[text])` — `code` defaults to
`200`, `headers` to none. `data` accepts anything with a body form:
`text` (sent as-is), `int`/`float`/`bool` (stringified, the same
implicit conversion `log()` already does), a `struct`/table row/
`arr`/`map` (rendered as JSON, the same `.toText()` every container
already has), or `blob` (sent as its own raw bytes, not decoded through
`.toText()` — the more likely thing to want for a binary response). An
`img`/`aud` argument is a **compile error**: neither has a body form,
the same reasoning `log()`/templates already reject them with.

If `on request`'s own body returns without calling `ok()`/`redirect()`/
`send()`/`upgrade()` at all, the connection still gets a response — a
plain `200` with an empty body — rather than hanging the client
forever.

**Reading the body:**

```festina
text t = req.toText()                 // the raw bytes, as text
blob b = req.toBlob()                 // the raw bytes, as a blob
img i = req.toImg()                   // decoded as an image (null if it isn't one)
aud a = req.toAud()                   // decoded as audio (null if it isn't one)
```

A request with no body answers an empty `text`/`blob` (never `null`),
matching every other "nothing there" case in this language.

### WebSocket: `req.upgrade()`

```festina
on request(req:http) {
    if req.path == '/ws' {
        req.upgrade()
    }
}

on upgrade(s:socket) {
    log('client connected')
}

on message(s:socket, msg:blob) {
    s.send(`you said: ${msg.toText()}`)
}

on socketClose(s:socket) {
    log('client disconnected')
}
```

`req.upgrade()` performs the WebSocket handshake (RFC 6455) immediately
and switches the connection over — nothing else about the request
matters afterward (a call to `ok()`/`send()`/etc. on the same `req` is
now a no-op, same as any second response attempt). If the request isn't
actually a valid WebSocket handshake (missing/mismatched headers),
`upgrade()` is a silent no-op and the connection falls through to the
normal "no response sent" default (`200`, empty body) — never a crash.

Once upgraded, `on upgrade(s:socket)` fires once for that connection,
then `on message(s:socket, msg:blob)` fires once per message received
— **always as a `blob`**, whether the peer sent a text or binary frame
(call `.toText()` if you know it's always text). `on socketClose(s)`
fires exactly once when the connection ends, however it ends (the peer
closed it, sent a close frame, or the read failed) — never for a plain
HTTP connection that never upgraded.

```festina
s.state                               // map[text] -- a per-connection scratchpad,
                                       // starts empty, persists for the connection's
                                       // whole lifetime
s.state['user'] = 'ada'               // read/write like any other map[text]

s.send(data)                          // data:any -- same sendable types as req.send(),
                                       // minus the code/headers (a frame has neither);
                                       // blob sends a binary frame, everything else text
s.close()                             // sends a close frame and ends the connection
```

### <a name="http-limitations"></a>Limitations

- **HTTP/1.1 request-line + headers + a `Content-Length` body only.**
  No chunked transfer-encoding, no HTTP/1.0-specific behavior.
- **No keep-alive.** Every response closes the connection afterward —
  each request is its own TCP connection, start to finish.
- **No WebSocket fragmentation, ping/pong sent by this runtime, or
  extensions.** A fragmented message (a frame with `FIN=0`, or a
  continuation frame) closes the connection rather than being silently
  dropped or misread. A received ping is answered with a pong
  automatically; a received pong is ignored. `permessage-deflate` and
  every other WebSocket extension are unsupported.
- **Linux and macOS, plus Windows behind an opt-in flag.** Linux/macOS
  use plain POSIX sockets; Windows uses a real winsock2 port (built,
  CI-compiled — see [windows.md](windows.md)) gated behind
  `FESTINA_ENABLE_WINDOWS_HTTP=1` pending real-hardware verification,
  the same shape audio/graphics already use there. Not available under
  `--target=wasm32-wasi` at all — WASI Preview 1 has no listening-socket
  support — rejected at compile time; see [wasm.md](wasm.md).
- **Cannot be combined with graphics** (`render()`, or an `on
  mouseDown`/.../`close` handler) in the same program — the server's
  own event loop and the graphics event loop are mutually exclusive in
  this version. `setTimeout`/`setInterval` combine fine; both are
  serviced from the same loop.

## Freeing and deleting

Memory is automatic — but `free` and `delete` exist for the moments you
know better than the compiler does.

### `free`

```festina
img spritesheet = 'spritesheet.png'
img grass = spritesheet.clip(0, 0, 31, 31)
img dirt = spritesheet.clip(32, 0, 31, 31)
free spritesheet                       // the sheet goes now, not at exit
```

`free name` releases whatever the binding holds and sets the binding to
`null`. It works on **every type**:

- **struct / `arr[T]` / `map[T]` / `blob` / `img` / `aud` / `regex`** —
  a reference-count *decrement*, not a forced free. A value something
  else still points at survives until its last reference drops; freeing
  an array releases each element the same way, so a shared element
  outlives its array. An alias of a freed `img`/`aud` stays fully
  usable — the freed *binding* reads `null`, the alias does not, and the
  surface or clip goes away when the last reference does. Freeing an
  `aud` that was the last reference stops every channel still playing
  it. A `/pattern/` literal's process-lifetime cache is immortal, so
  `free` on a binding aliasing one is a safe no-op.
- **`text`** — the buffer is freed (a text is exclusively owned).
- **a query row** — the binding is nulled *without* freeing: the row is
  owned by the array it came from. Free the array.
- **`int` / `float` / `bool`** — nothing to release; `free x` is `x = null`.

`free` composes with automatic reclamation: freeing twice is a no-op,
and a freed binding that scope-exit cleanup later visits is already
`null`, which every release treats as nothing-to-do. Constants and
parameters can't be freed (a parameter borrows its caller's value).

### `delete`

```festina
map[text] example = {'data': 'some data', 'more-data': 'Some more data'}
delete example.data
delete example['more-data']
```

On a **map**, `delete` removes the entry, JS-style — the key stops
existing (`forEach` no longer visits it), which setting `null` could
never express. Deleting a missing key is a safe no-op. The key can be a
computed expression: `delete m[`k${i}`]`.

On a **struct or query-row field**, `delete` releases the value and the
field reads `null` afterwards. On a query row it *also* marks the column
undefined — see below. (One inherited caveat: a struct field whose own
type is struct/arr/map auto-vivifies on the next reach-through, per the
zero-value rule, so it re-appears empty rather than staying null.)

To remove a whole *variable*, that's `free` — `delete x` says so.

## Saving bytes to a path

`blob`, `img` and `aud` are the same shape of value — content, plus the
bytes it came from — so all three save the same way.

```festina
value.save()                          // write to the path it already has
value.save(path)                      // adopt `path`, then write there
value.saveCopy(path)                  // write there, keep its own path
```

All three return `bool`: `true` if the write landed.

**`save(path)` changes the value's path; `saveCopy(path)` doesn't.** That
is the whole difference. After `save`, everything else that acts on the
value follows the new path — a blob's `exists()` and `delete()` included.
After `saveCopy`, the value is still pointed at where it was:

```festina
blob f = 'one.txt'
f.write('original')

f.saveCopy('copy.txt')                // copy.txt written; f is still one.txt
f.write('changed')                    // ...so this goes to one.txt

f.save('two.txt')                     // two.txt written; f is now two.txt
f.delete()                            // deletes two.txt, not one.txt
```

`saveCopy` requires its path — a copy to nowhere in particular isn't a
thing to ask for, and making the argument mandatory turns "I meant
`save()`" into a compile error rather than a silent overwrite.

**A value with no path can only use `save(path)`.** An `img` from
`clip()`, or anything read out of a database column, has never been on
disk. Calling `save()` on one **fails the program** rather than returning
`false` — a program asking to save something to nowhere has a bug, where
an unwritable directory is a condition of the filesystem and still just
answers `false`.

```festina
img spritesheet = 'spritesheet.png'
img grass = spritesheet.clip(0, 0, 32, 32)

grass.save()                          // fails: this img has no path
grass.save('grass.png')               // fine -- and now it has one
grass.save()                          // fine from here on
grass.saveCopy('backup/grass.png')    // fine; grass.png stays its path
```

The path must name a **file**, not a directory. A directory would have to
borrow a filename from somewhere, and the value that most needs saving is
exactly the one with no filename to lend, so it would work only where it
was least useful. Passing one answers `false`.

**Formats survive.** What gets written is the value's own encoded bytes,
so an MP3 saves as an MP3 and a JPEG as a JPEG rather than being
re-encoded — the same property that makes a BLOB column round-trip
byte-identically. The one exception is an image you built rather than
loaded: a `clip()` or `resize()` result has no source bytes, so it is
encoded as PNG, which is lossless.

A failed save does **not** adopt the path. Pointing a value at a file
that was never written would leave `exists()` answering `false` about a
path you were just told it had.

## Time

```festina
int ms = now()                        // milliseconds since the Unix epoch
log(formatTime(ms, '%Y-%m-%d %H:%M')) // strftime, local time -> text
```

`now()` uses the same unit and origin as JavaScript's `Date.now()`, and
the same unit `setTimeout` already takes, so timing a block is just
subtraction:

```festina
int started = now()
doTheWork()
log(`took ${now() - started}ms`)
```

### Single-value queries

```festina
int total  = sqliteInt(`SELECT count(*) FROM Post`)
text name  = sqliteText(`SELECT title FROM Post WHERE id = ?`, [2])
float mean = sqliteFloat(`SELECT avg(score) FROM Post`)
```

The first column of the first row. Use these instead of declaring a
`table` just to receive a scalar — a `table` declaration *creates* a
real table, so a throwaway one for a `count(*)` would sit in your
database permanently.

A query matching no rows (or whose value is SQL NULL) returns `null`,
so it's something to test for rather than something that stops you.

### JSON and full-text search

Both are ordinary SQL, and `sqlite()` passes SQL through untouched — so
SQLite's JSON1 and FTS5 work today with no extra language feature:

```festina
log(sqliteText(`SELECT json_extract(data, '$.name') FROM Doc WHERE id = ?`, [1]))

sqlite(`CREATE VIRTUAL TABLE PostSearch USING fts5(title, body, content='Post', content_rowid='id')`)
sqlite(`INSERT INTO PostSearch(PostSearch) VALUES('rebuild')`)
log(sqliteInt(`SELECT count(*) FROM PostSearch WHERE PostSearch MATCH ?`, ['machine']))
```

## Growing arrays

```festina
arr[int] xs = [1, 2, 3]

xs.push(4)          // -> new length
xs.pop()            // -> last element, removed
xs.shift()          // -> first element, removed
xs.unshift(0)       // -> new length
arr[int] cut = xs.splice(1, 2)   // remove 2 from index 1, return them
xs.splice(1, 0, [8, 9])           // insert [8, 9] at index 1, remove nothing
xs.indexOf(3)       // -> first index holding 3, or -1
```

All six behave as their JavaScript namesakes do, including `splice`'s
clamping — a negative start counts back from the end, and an oversized
range clamps rather than failing, so `splice(i, 1)` at a boundary is a
no-op. `splice` takes an optional third argument — `splice(start, count,
insertArr)` — in place of JavaScript's variadic `...items` (Festina has
no variadic calls, so the items to insert are one explicit `arr[T]`
instead of a spread list); either way only the REMOVED elements come
back, never the inserted ones, exactly as JavaScript's own `splice()`
answers.

`pop()`/`shift()` on an empty array return `null` — not zero, so an
empty pop is distinguishable from popping a real `0`:

```festina
arr[int] empty = []
log(empty.pop() == null)     // true
```

`indexOf()` answers `-1` when the value isn't present, rather than
`null` — an index is the kind of thing you compare or feed straight to
`splice`, and both read naturally against `-1`:

```festina
if queue.indexOf(target) >= 0 { ... }
queue.splice(queue.indexOf(target), 1)   // remove by value
```

What "the same value" means depends on the element type:

- `int`, `float`, `bool` — **by value**.
- `text` — **by content**, so a needle built at runtime finds a match:
  `names.indexOf('gr' + 'ace')` is `1` for `['ada', 'grace']`. (Identity
  would be useless here: text is copied on binding, so two equal strings
  are almost always two different buffers.)
- `struct`, `arr`, `map` — **by identity**. Two separately-declared
  structs with identical fields are two different values; only the one
  actually in the array is found.

Elements are owned the same way any other binding owns them: pushing a
`text` copies it, so the array and the variable don't share a buffer.
Removing transfers ownership to whoever receives it. `indexOf()` takes
no ownership at all — an index isn't a reference.

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
aud music = 'music.wav'               // WAV (16-bit PCM) or MP3
int ch = music.play()                 // once  -> the channel it played on
music.playLoop()                      // until stopped -> also returns one
music.isPlaying()                     // true the instant play() returns
music.stop()                          // silence this clip, everywhere

stopAudioPlayer(ch)                   // stop one channel
stopAudioPlayer()                     // stop every channel
isAudioPlayerPlaying(ch)              // true while THAT CHANNEL plays anything
```

A path declares the clip, the same way `blob`, `color`, `font` and `img`
are each written as the text that reads best. It's a real load, not a
compile-time resolution, so the path may be any text expression
(`aud hit = soundDir + 'hit.wav'`).

`save()`/`saveCopy()` write a clip back out; see
[Saving bytes to a path](#saving-bytes-to-a-path).

**WAV (16-bit PCM) and MP3.** The format is sniffed from the file's
contents, not its extension — a clip out of a database column has no
extension, and an extension was never evidence of anything anyway.
Anything else (a compressed WAV, 8/24/32-bit PCM, Ogg, FLAC) fails at
load with a message naming both supported formats.

Plays through a real ALSA output device on a background thread, so
playback doesn't block the rest of the program.

### Stopping a sound

There are two questions, and they have two answers.

**`stop()` silences this clip everywhere.** One clip can be playing on
several channels at once, so this stops all of them. That is what you
want for a looping engine hum, a music bed or a dialogue line — anything
where "this sound should not be audible any more" is the whole thought.

**`stopAudioPlayer(n)` stops one channel.** That is what you want when
three gunshots are overlapping and only one of them should end.

Which channel? The one `play()` handed back:

```festina
aud engine = 'engine.wav'
int hum = engine.playLoop()    // the pool picked a channel; now you know it
// ...later...
stopAudioPlayer(hum)           // stop exactly that one
```

`play()` and `playLoop()` both return the channel they used, or `-1` if
nothing played (which happens only when every channel is reserved). Before
that, a channel the pool assigned on its own was one you could not name,
so the pool was addressable only by picking channels by hand — that is,
by not using the pool.

`isPlaying()` is clip-wide, like `stop()`: "is this sound audible
anywhere" and "silence it everywhere" are one question asked two ways.
For the other question — "is *this specific channel* still playing
anything" — there's `isAudioPlayerPlaying(n)`:

```festina
int hum = engine.playLoop()
// ...later, with no reference to `engine` in scope anymore...
if isAudioPlayerPlaying(hum) { stopAudioPlayer(hum) }
```

It answers about the channel, not the clip — if a different clip has
since taken that channel over (`play(n)`/`playLoop(n)`, or automatic
stealing), it reports on whatever's playing there *now*, which
`engine.isPlaying()` couldn't do once `engine` itself said false.

### Overlapping sounds

**`play()` while a sound is already playing does not cut it off.** Sound
goes out through a pool of **channels** — so a footstep, a gunshot or a
coin pickup firing in rapid succession layer instead of interrupting
each other, which is what a game actually needs:

```festina
aud coin = 'coin.wav'
coin.play()   // three overlapping copies, not one restarted three times
coin.play()
coin.play()
```

The clip's audio is decoded once, at the declaration; a channel costs
a thread and a device handle, never another copy of the samples.

```festina
setMaxAudioPlayers(4)          // channels the pool may assign on its own
log(maxAudioPlayers())         // -> 4, i.e. what was actually applied
```

`setMaxAudioPlayers` is clamped into `[1, 64]` rather than rejected.
When every unreserved channel in the pool is busy, the **oldest** is
stolen. Something has to give at the limit, and the sound that has been
playing longest is closest to finishing anyway — dropping the *new* play
instead would silence a rapid-fire effect at exactly the moment it fires
fastest.

`setMaxAudioPlayers(1)` is the way to ask for the old behaviour back:
one channel, restarted from the beginning on every `play()`.

### Channels and looping

Channels are **process-global and numbered from 0**, not per-clip — so
two different clips can share one, which is what makes handing a music
channel from one track to another expressible at all:

```festina
aud adventureMusic = 'adventure.wav'
aud battleMusic = 'battle.wav'

adventureMusic.playLoop(0)          // loops on channel 0, and reserves it
setInterval(changeMusic, 100000)

void func changeMusic() {
    if adventureMusic.isPlaying() {
        battleMusic.playLoop(0)     // takes channel 0 over
    } else {
        adventureMusic.playLoop(0)
    }
}

stopAudioPlayer(0)                  // stop that channel, release it
```

| Call | What it does |
|---|---|
| `clip.play()` | Play once on a channel the pool picks. |
| `clip.play(n)` | Play once on channel `n`, taking it over. |
| `clip.playLoop()` | Loop on a channel the pool picks, and **reserve** it. |
| `clip.playLoop(n)` | Loop on channel `n`, taking it over and **reserving** it. |
| `stopAudioPlayer(n)` | Stop channel `n` and release it. |
| `stopAudioPlayer()` | Stop every channel. |
| `clip.isPlaying()` | True while any channel is playing that clip. |
| `isAudioPlayerPlaying(n)` | True while channel `n` is playing anything, regardless of clip. |

**`playLoop` reserves its channel.** A reserved channel is never chosen
by automatic assignment and never stolen — so a looping music track
cannot be evicted by an ordinary sound effect, however many are firing.
Two things release it: `stopAudioPlayer(n)` (or a bare
`stopAudioPlayer()`), and naming the channel explicitly in another
`play(n)`/`playLoop(n)`. An explicit `play(n)` both takes the channel
over *and* hands it back to the pool, since a one-shot has nothing to
reserve it for.

An out-of-range channel is clamped into `[0, 64)`, the same call
`setMaxAudioPlayers` makes — a bad channel number should not kill a
running game. `setMaxAudioPlayers` bounds only what the pool assigns on
its own; an explicitly named channel is honoured anywhere in range, so
`play(40)` works with a pool of 10.

If you reserve *every* channel and then fire an unnamed `play()`, it is
dropped — there is nothing left the pool is allowed to touch, and the
alternative would be breaking a reservation you asked for.

`isPlaying()` is about the **clip**, not one playback of it: it is true
while any channel is playing that clip. To ask about a single
playback, name its channel with `isAudioPlayerPlaying(n)` instead.

## Imports

```festina
import database.f
import graphics.f
```

Resolved recursively before compilation into one merged compilation
unit; a file is never imported more than once even if multiple files
depend on it; errors still point at the file a statement actually came
from.

## `log()` / `fail()` / `close()`

```festina
log(value)     // prints any primitive to stdout, newline-terminated
fail('message')  // prints to stderr, exits(1)
close(code)    // exits(code), running `on exit` first if declared
```

`close(code)` exits the program with the given exit code — it works in
every program, with or without a window, unlike the graphics-only `on
close` handler under [Graphics](#graphics) above (which fires on the
window's own close button and is a different thing with a similar
name). If a program declares `on exit(code:int) { ... }`, `close(code)`
runs it — passed the same code — before the process actually exits:

```festina
on exit(code:int) {
    log(`exiting with ${code}`)
}
log('working...')
close(1)          // prints "exiting with 1", then exits with status 1
```

With no `on exit` handler declared, `close(code)` just exits.

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
