# Festina Language Specification

Edition 0.44 · 2026-09-11

This document is the normative specification of the Festina programming
language: its syntax, its static semantics (what a conforming compiler
must accept and must reject), its runtime semantics, and the built-in
facilities every program may rely on. It consolidates the language as it
exists in this repository today, organized by topic in the manner of the
ECMAScript Language Specification rather than in the order features were
added.

Companion documents:

| Document | Role |
|---|---|
| [api.md](api.md) | The standard library reference as implemented, with worked examples, caveats and performance notes. Where this specification summarizes a built-in, api.md carries the full description. |
| [decisions.md](decisions.md) | The numbered, chronological decision log (formerly `claude.md`). A reference of the form `claude.md #N` anywhere in the repository means entry N of that file. This specification cites entries as `[#N]` for rationale; those citations are informative. |
| [tests/CONTRACT.md](tests/CONTRACT.md) | What is verified, and how. |
| [claude.md](claude.md) | Working instructions for AI agents implementing this specification. |
| [setup.md](setup.md), [macos.md](macos.md), [windows.md](windows.md), [wasm.md](wasm.md) | Toolchain and per-platform notes. |

## Contents

1. [Scope](#1-scope)
2. [Conformance](#2-conformance)
3. [Normative References](#3-normative-references)
4. [Overview](#4-overview)
5. [Notational Conventions](#5-notational-conventions)
6. [Source Text and Program Structure](#6-source-text-and-program-structure)
7. [Lexical Grammar](#7-lexical-grammar)
8. [Types](#8-types)
9. [Expressions](#9-expressions)
10. [Statements](#10-statements)
11. [Declarations](#11-declarations)
12. [Execution Model](#12-execution-model)
13. [Memory Management](#13-memory-management)
14. [Errors and Diagnostics](#14-errors-and-diagnostics)
15. [The Built-in Database](#15-the-built-in-database)
16. [The Standard Library](#16-the-standard-library)
17. [Graphics](#17-graphics)
18. [Audio](#18-audio)
19. [HTTP and WebSocket](#19-http-and-websocket)
20. [Threads](#20-threads)
21. [Compilation, Targets and Tooling](#21-compilation-targets-and-tooling)

Annexes

- [Annex A — Grammar Summary](#annex-a--grammar-summary)
- [Annex B — Reserved Words and Global Names](#annex-b--reserved-words-and-global-names)
- [Annex C — Removed and Superseded Features](#annex-c--removed-and-superseded-features)
- [Annex D — Non-goals](#annex-d--non-goals)
- [Annex E — Correspondence with the Original Numbered Specification](#annex-e--correspondence-with-the-original-numbered-specification)

---

## 1 Scope

This specification defines the Festina programming language. It covers:

- the lexical and syntactic grammar of Festina source text;
- the static type system, name resolution and every condition that is a
  compile error;
- the runtime semantics of expressions, statements and declarations;
- the memory-management model a program may rely on;
- the built-in facilities that are part of the language rather than
  libraries: the automatic SQLite database, files and directories,
  graphics, audio, timers, HTTP/WebSocket serving and requesting,
  threads, JSON conversion, regular expressions, and process control;
- the compilation model: import resolution, the generated program entry
  point, native executables, and the supported targets.

This specification does not cover the internal structure of the
reference compiler (`festina/`) or runtime (`runtime/`), the benchmark
methodology, or the documentation site. Signatures and behaviors of the
standard library are specified here to the level of what a program may
rely on; [api.md](api.md) is the complete reference and is normative
where this document defers to it explicitly.

## 2 Conformance

### 2.1 Conforming implementations

A conforming implementation of Festina:

1. must accept every program this specification defines as valid, and
   give it the semantics this specification defines;
2. must reject, with a compile error (§14.1), every program this
   specification designates as a compile error, and must not accept
   syntax or behavior this specification does not define;
3. must produce a native executable that runs without the Festina
   compiler, its sources, or a Python installation present (§21.3);
4. must provide the built-in database (§15) on every target, and the
   other built-in facilities on every target where §21.4 says they are
   available;
5. must report the errors of §14 with file, line, column and a
   human-readable message.

### 2.2 Normative and informative text

The words **must**, **must not**, **may** and **may not** are normative.
Text introduced by *Note:* is informative, as are citations of the form
`[#N]` into [decisions.md](decisions.md) and every example. Where an
example and normative text disagree, the normative text governs.

A **compile error** is a diagnostic emitted by the compiler that prevents
an executable from being produced (§14.1). A **runtime failure** is the
`fail()` mechanism (§14.2): a message on standard error followed by
process exit with status 1. A behavior is **undefined** when this
specification places no requirement on it; a conforming program must not
rely on it (§14.4). A behavior is **implementation-defined** when an
implementation must choose one and document it; it is **unspecified**
when an implementation may choose freely and need not document it.

### 2.3 The "test, don't fail" convention

Unless a clause says otherwise, a built-in operation whose outcome
depends on the environment rather than on the program (a file that
cannot be read, a directory that already exists, a port that is in use,
an audio channel out of range, a division by zero) must not fail the
program. It must instead answer a value the program can test: `null`,
`false`, `-1`, an empty value, or a silent no-op, as each clause
specifies. A runtime failure is reserved for a program-authoring mistake
(a certificate that does not parse, saving a value that has no path,
reading an enum field of the wrong variant). [#93, #97, #132, #160]

### 2.4 Resolving ambiguity

Where this specification does not determine behavior, an implementation
must apply these rules in order and must not invent behavior: [#2, #54]

1. Prefer the simplest implementation.
2. Prefer compile-time behavior over runtime behavior.
3. Prefer native representations over runtime abstractions.
4. Prefer JavaScript-like syntax.
5. Prefer static typing.
6. Prefer performance; among implementations that satisfy this
   specification, choose the one with the lowest runtime overhead.
7. Do not introduce new syntax without necessity.
8. Do not silently change existing semantics.
9. If behavior genuinely cannot be determined from this specification,
   treat it as an unresolved language-design decision, record it, and
   do not guess.

An implementation must follow this specification rather than assume
behavior from JavaScript, TypeScript, SQLite or any other language
wherever Festina defines its own.

### 2.5 Relationship to JavaScript

Festina is JavaScript-inspired and is not JavaScript-compatible. Familiar
syntax is retained where it does not conflict with static typing or
performance: template strings, the ternary operator, arrow functions,
`for`/`while`/`if`, property access, array and map literals, regex
literals, `try`/`catch`/`throw`. Festina does not inherit JavaScript's
dynamic semantics: there is no truthiness, no implicit coercion other
than the numeric promotion of §8.3, no `var`/`let`, no `===`/`!==`, no
prototypes, no closures, no runtime module loading. Annex D lists the
non-goals. When a Festina rule differs from JavaScript, the Festina rule
always takes precedence. [#1, #45, #53]

### 2.6 Evolution of the language

A change to the language is made by changing this specification and
recording the decision as a new numbered entry in
[decisions.md](decisions.md); [api.md](api.md), [CHANGELOG.md](CHANGELOG.md)
and [tests/CONTRACT.md](tests/CONTRACT.md) follow. Entry numbers are
permanent. Annex C records features that were specified and later
removed; an implementation must not provide them.

## 3 Normative References

The following documents are referenced by this specification. Only the
parts cited are normative.

- **LLVM** — the LLVM compiler infrastructure. Festina programs are
  compiled through LLVM IR to native code (§21.1).
- **SQLite** — the SQLite database engine, including its JSON1 and FTS5
  extensions where present (§15).
- **IEEE 754-2008** — binary floating-point arithmetic; `float` is a
  64-bit binary64 value (§8.3).
- **RFC 3629** — UTF-8; `text` is UTF-8 (§8.4).
- **IEEE Std 1003.1 (POSIX), Regular Expressions** — the Extended
  Regular Expression syntax `regex` uses (§8.15).
- **RFC 8259** — JSON, the interchange format of `.toText()`,
  `.toStruct()` and `.toArr()` (§8.21, §16.4).
- **RFC 9110 and RFC 9112** — HTTP semantics and HTTP/1.1 (§19).
- **RFC 6455** — the WebSocket protocol (§19.4).
- **WASI Preview 1** — the system interface of the `wasm32-wasi`
  target (§21.5).
- **CSS Color Module Level 4** — the 148 named colors and hexadecimal
  color notation `color` literals accept (§8.14).
- **ISO C, `strftime`** — the format language of `formatTime()` (§16.2).

*Note:* Cairo, X11, ALSA, Cocoa, Win32, winsock2 and mbedTLS are the
reference implementation's platform backends. They are named in this
document only to describe implementation-defined behavior; a conforming
implementation may use others.

## 4 Overview

### 4.1 Design priorities

Festina is a statically typed language for high-performance native
applications. Its primary design priority is **performance over
flexibility**. Its secondary priorities are simplicity, predictability,
static typing, low runtime overhead, familiar syntax and native
performance. [#1]

These priorities are visible throughout the language: every type is
resolved at compile time (§8.19); conditions must be `bool` (§9.8);
colors and fonts are resolved at their declaration (§8.14); array
indexing is unchecked (§9.3); a `match` statement compiles to exactly the
`if` chain it abbreviates (§10.9); memory is reclaimed without a garbage
collector (§13).

### 4.2 The shape of a program

A Festina program is a set of source files with the `.f` extension, one
of which is the *entry file* passed to the compiler. There is no `main()`:
the entry file's executable statements are the program. Declarations
(functions, structs, tables, enums, event handlers, threads) may appear
anywhere in a file and are hoisted (§6.6).

```festina
table People {
    id:int
    name:text
}

text func greet(name:text) {
    return `Hello, ${name}!`
}

log(greet('Festina'))
arr[People] people = sqlite('SELECT * FROM People')
```

Compiling and running this program creates `festina.sqlite`, creates or
synchronizes the `People` table, prints the greeting and runs the query,
with no imports, configuration or initialization written by the
programmer.

### 4.3 Built-in facilities

The following are part of the language and need no import:

| Facility | Clause |
|---|---|
| SQLite database with automatic schema synchronization | §15 |
| Files (`blob`), directories, environment, command line, process spawning | §16 |
| A drawing canvas, a window, mouse and keyboard events, images | §17 |
| Audio playback with channels | §18 |
| Timers | §12.4 |
| HTTP/1.1 and WebSocket servers, an HTTP client, TLS serving | §19 |
| Isolated message-passing threads and thread pools | §20 |
| JSON rendering and parsing, regular expressions, structured logging | §8.21, §8.15, §16 |

### 4.4 Execution model in brief

A compiled program runs on one main OS thread. After the entry file's
top-level statements finish, the program exits unless something keeps it
alive: a pending timer, an open window, a listening port, or a
background load in flight; in that case a single event loop services
timers, window events, connections and callbacks until none remain
(§12). Threads declared with `thread` run on their own OS threads and
communicate with the main program only by message (§20).

### 4.5 Organization of this specification

Clauses 6 through 11 define the language proper: source structure,
lexical grammar, types, expressions, statements and declarations.
Clauses 12 through 14 define the execution, memory and error models.
Clauses 15 through 21 define the built-in facilities and the compilation
model. The annexes collect the grammar, the reserved words, removed
features, non-goals, and the mapping from the original numbered
specification to this document.

## 5 Notational Conventions

### 5.1 Grammar notation

Syntax is given in an EBNF-style notation:

- *Nonterminal* names are written in *italics*; terminals are written in
  `monospace`.
- `::=` introduces a production; `|` separates alternatives.
- `[ x ]` means `x` is optional; `{ x }` means zero or more repetitions.
- *Identifier*, *NumericLiteral*, *StringLiteral*, *TemplateLiteral* and
  *RegexLiteral* are the lexical tokens of §7.
- Newlines are statement separators (§7.1); the grammar does not show
  them.

Annex A collects every production in one place.

### 5.2 Code and examples

Examples are Festina source in fenced blocks. A comment `// error:` on a
line indicates that the line is a compile error; `// fail:` indicates a
runtime failure. Examples are informative.

### 5.3 Terms

- **binding** — a variable, constant, parameter, struct field, array
  element, map value or catch variable that holds a value.
- **managed value** — a value whose memory is reclaimed automatically
  (§13.1); every value except `int`, `float`, `bool`, `color`, `font`
  and table declarations themselves.
- **handle** — a managed value that refers to a runtime resource: `img`,
  `aud`, `blob`, `regex`, `http`, `socket`, `url`.
- **zero value** — the value a binding of a type has before anything is
  assigned to it (§8.2).
- **entry file** — the file passed to the compiler (§6.3).
- **top level** — statements and declarations that are not inside a
  function, handler, thread or block.

## 6 Source Text and Program Structure

### 6.1 Source files

A Festina source file is a UTF-8 text file with the extension `.f`. A
program consists of the *entry file* and, transitively, every file it
imports. [#4]

### 6.2 Imports

*ImportDeclaration* ::= `import` *Path*

*Path* is a bare (unquoted) relative file path ending in `.f`, such as
`import database.f` or `import ui/graphics.f`. There is no
`import { ... } from`, no `require()`, and no runtime module loading;
an import is a compile-time operation. [#5, #53]

Import resolution must be recursive and must produce one compilation
unit containing every reachable file exactly once:

1. Paths are resolved relative to the importing file and compared by
   canonical path, so `./utils.f` and `src/../utils.f` are one file.
2. A file already in the unit is not processed again.
3. A circular import (`a.f` → `b.f` → `a.f`) must be detected and
   reported, or handled deterministically without reprocessing either
   file.
4. Imported files are processed before the file that imports them, and
   all imports are resolved before any semantic analysis. [#6, #8]

An imported file's declarations join the single global scope (§6.7);
imported files do not become modules or namespaces. Diagnostics must
name the file a statement actually came from (§14.1).

### 6.3 The entry file and the generated entry function

The file passed to the compiler is the entry file. The programmer does
not write `main()`. Every executable top-level statement of the
compilation unit is placed, in order, into a compiler-generated entry
function whose internal name is implementation-defined; that function
is the runtime entry point. [#7]

```festina
log('Hello')
```

is conceptually

```festina
void func __festina_main() {
    log('Hello')
}
```

Top-level statements of imported files run before the entry file's, in
import order. Declarations are not executable statements and are
hoisted (§6.6).

### 6.4 The `DatabaseURL` statement

*DatabaseURLStatement* ::= `DatabaseURL` `=` *Expression*

The entry file's first statement, before any other code and before any
import, may set the path of the automatic database (§15.1). The
expression must be `text`; it may be any text expression, including
`environment.NAME`. `DatabaseURL` anywhere else in the entry file is a
compile error, and it has no effect in an imported file. `DatabaseURL`
is not a reserved word; it is recognized by name in this position only,
and inside a thread body (§20.7). [#70]

### 6.5 Program startup

A compiled program must perform these steps before the entry function
runs, and the entry function before the event loop: [#8, #196]

1. Register every event handler (§11.5) and start every declared thread
   (§20.2).
2. Open or create the automatic database (§15.1) and synchronize the
   schema of every declared `table` (§15.2).
3. Populate `argv` (§16.2).
4. Run the generated entry function.
5. If anything keeps the program alive (§12.2), run the event loop
   until nothing does; then exit with status 0.

The relative order of steps 1 through 3 is implementation-defined.
Schema synchronization must complete before any application code can
touch a declared table.

### 6.6 Hoisting

The following declarations are visible throughout the whole
compilation unit regardless of where they appear, including above
their own text and inside branches that never execute: [#140, #178]

- function declarations (§11.1), including functions declared inside
  blocks, other functions, handlers or threads (a nested function is an
  ordinary global function);
- `struct`, `table` and `enum` declarations (§11.2–11.4), which may
  reference each other and themselves in any order;
- event handlers (§11.5): every handler is registered before the first
  top-level statement runs;
- `thread` declarations (§11.6).

Variables and constants are not hoisted: a top-level variable's
initializer runs at its position in the entry function, so a handler
triggered synchronously by a statement above the variable's declaration
sees the variable's zero value (§8.2).

### 6.7 Scope and namespaces

Festina has one global scope per program plus lexical block scopes for
variables. A variable declared in a block (a function body, a handler
body, an `if`/`else`/`while`/`for`/`try`/`catch`/`match` body, a
thread body) is visible from its declaration to the end of that block.
A `for` loop's initialization variable is scoped to the loop. A `catch`
variable is scoped to the `catch` body.

Functions have no lexical environment beyond the global scope: a
function body sees the global variables, the global constants, every
declared type and function, and its own parameters and locals. There
are no closures (§11.1.4).

Names live in two namespaces: [#58]

- **type names**: `struct`, `table` and `enum` names, which share one
  namespace and must be unique within it;
- **value names**: variables, constants, functions and thread names,
  which share another namespace.

A struct named `User` and a variable named `User` may coexist.
Redeclaring a name within its namespace in the same scope is a compile
error. A user function or variable must not take the name of a built-in
function (§16.1) or a built-in global (§16.2); doing so is a compile
error. [#89, #131, #195]

A thread body is its own scope with its own visibility rules (§20.3).

## 7 Lexical Grammar

### 7.1 Whitespace, line terminators and statement termination

Space, tab, carriage return and line feed are whitespace. A line feed
terminates a statement; a semicolon `;` may also terminate a statement
and is otherwise ignored. Semicolons are optional and the preferred
style omits them. [#9]

```festina
text name = 'Festina'
log(name)
```

### 7.2 Comments

*Comment* ::= `//` any characters to end of line | `/*` any characters `*/`

Comments are whitespace. Block comments do not nest.

### 7.3 Identifiers

*Identifier* ::= ( letter | `_` ) { letter | digit | `_` }

Letters are the ASCII letters `A`–`Z` and `a`–`z`. Identifiers are
case-sensitive.

### 7.4 Reserved words

The following words are reserved and may not name a variable, constant,
function, type or thread: [#51]

```
amor    arr     ascii   aud     blob    bool    break   catch
clear   const   continue delete else    enum    fail    false
float   for     free    func    http    if      img     import
int     let     log     map     match   null    on      return
socket  sqlite  struct  table   text    thread  throw   true
try     typeof  var     void    while
```

`var` and `let` are reserved so that using them is a clear compile
error naming the Festina spelling; they have no meaning (Annex D).

A reserved word may still be used as a member name after `.`, so
`file.delete()`, `'x'.match(re)` and `s.length` parse as method or
property accesses. [#111, #252]

The following words are **contextual**: they have meaning only in one
position and are ordinary identifiers elsewhere.

| Word | Meaning | Clause |
|---|---|---|
| `default` | the fall-through arm of a `match` | §10.9 |
| `use` | `on request use NAME` | §20.9 |
| `DatabaseURL` | database path assignment | §6.4, §20.7 |

The built-in global function and global object names of §16 (`log`,
`sqlite`, `Math`, `environment`, `argv`, `clientWidth`, `postMessage`,
…) are not reserved words but may not be redeclared (§6.7). Annex B
lists them.

### 7.5 Literals

#### 7.5.1 Numeric literals

*NumericLiteral* ::= digit { digit } [ `.` digit { digit } ]

A literal without a fractional part has type `int`; one with a
fractional part has type `float`. There is no exponent, hexadecimal,
octal or binary form, and no numeric separator. A negative number is
the unary `-` operator applied to a literal (§9.6). An integer literal
must be representable as an `int`.

#### 7.5.2 String literals

*StringLiteral* ::= `'` { character | escape } `'` | `"` { character | escape } `"`

Single- and double-quoted strings are equivalent and have type `text`.
A string may not contain an unescaped line terminator. Escapes are
`\n`, `\t`, `\r`, `\\`, `\'`, `\"` and `` \` ``; a backslash before
any other character yields that character. The `\0` escape is a compile
error: `text` cannot hold a NUL. [#272] A string literal
assigned to an `ascii` binding is converted at compile time (§8.5).
[#206, #266]

#### 7.5.3 Template literals

*TemplateLiteral* ::= `` ` `` { character | escape | `${` *Expression* `}` } `` ` ``

A template literal has type `text`. Each `${...}` substitution is
evaluated left to right and rendered as if by `.toText()` (§8.21); a
value with no text form is a compile error. A template literal always
produces a fresh text value. The character sequence `${` outside a
template literal is a compile error. [#9, #83, #114]

#### 7.5.4 Boolean and null literals

`true` and `false` have type `bool`. `null` is a value of every type
(§8.2); its type is determined by context.

#### 7.5.5 Regular expression literals

*RegexLiteral* ::= `/` pattern `/` [ flags ]

A regular expression literal has type `regex`. It may appear only where
an expression may begin; a `/` that follows an operand is the division
operator. The literal ends at the first unescaped `/` not inside a
bracket expression and must be terminated on the same line. *flags*
immediately follows the closing `/` with no space and is any
combination of `i` and `g`; any other flag letter is a compile error.
The pattern itself is not validated at compile time (§8.15). [#67]

#### 7.5.6 Array, map and `http` literals

Array literals `[ ... ]`, map literals `{ ... }` and `http` literals are
expressions, defined in §9.1.

### 7.6 Punctuators and operators

```
{  }  (  )  [  ]  .  ,  :  ;  ?  =  =>
+  -  *  /  %  ++  --
==  !=  <  >  <=  >=  !  &&  ||
```

`===` and `!==` are recognized so that using them is a compile error
naming `==`/`!=`. [#18]

## 8 Types

### 8.1 Overview

Every binding, parameter, field, element and expression has a static
type fixed at compile time. Every type is resolved during semantic
analysis, before code generation, from the declarations in scope; the
compiler must not infer a type category from a naming convention, and
an unknown type name is a compile error. [#11–#13, #50]

*Type* ::=
  `int` | `float` | `bool` | `text` | `ascii` | `blob`
| `img` | `aud` | `regex` | `color` | `font`
| `http` | `socket` | `url` | `thread`
| [ `amor` ] `arr` `[` *Type* `]`
| `map` `[` *Type* `]`
| `func` `[` [ *Type* { `,` *Type* } ] `]` `:` ( *Type* | `void` )
| *Identifier*

*DeclaredType* ::= *Type* [ `?` ]

An *Identifier* used as a type names a `struct`, `table` or `enum`
declaration. `void` is not a type; it is the return annotation of a
function that returns nothing. The `?` suffix is the manually-managed
modifier (§8.18).

| Category | Types | Representation | Memory (§13) |
|---|---|---|---|
| Scalars | `int`, `float`, `bool` | native machine values | not managed |
| Strings | `text` | UTF-8 buffer | owned, copied on alias |
| | `ascii` | one byte per character, with header | reference counted |
| Records | `struct` types, `table` row types | heap record | reference counted |
| Unions | `enum` types | member pointer or box | reference counted |
| Collections | `arr[T]`, `amor arr[T]`, `map[T]` | heap header + buffer | reference counted |
| Handles | `blob`, `img`, `aud`, `regex`, `http`, `socket`, `url` | opaque runtime handle | reference counted |
| Style | `color`, `font` | packed integer / pointer to constant | not managed |
| Functions | `func[...]:R` | code pointer | not managed |
| Threads | `thread` | opaque identity | not managed |

The compiler must keep every category distinct internally; in
particular a `struct` and a `table` are never interchangeable. [#35]

### 8.2 `null` and zero values

`null` is a valid value of every type; the compiler must preserve the
static type of a binding that holds `null`. `null` is not a `bool` and
never participates in a condition. [#10, #25]

Each type has a **zero value**, which is what a binding holds before
anything is assigned to it:

| Type | Zero value |
|---|---|
| `int` | `0` |
| `float` | `0.0` |
| `bool` | `false` |
| `text`, `ascii`, handles, `enum`, `func`, `table` rows | `null` |
| `color` | `null` (the same value as `none`) |
| `struct`, `arr[T]`, `map[T]` | an empty value, created on first reach (§8.9.2) |

A struct field, a global, or a local of struct, array or map type reads
as a fresh empty value the first time it is reached, and that value
persists. An `int`, `float` or `bool` local declared without an
initializer must be assigned before it is read; reading it first is
undefined. A `table`-row local must be declared with an initializer
(§8.10). [#97, #178, #191]

The representation of `null` for a scalar is implementation-defined but
must be distinguishable from every valid value: a null `int` or `bool`
compares equal to `null` with `==`; a null `float` is an IEEE 754 NaN,
so `x == null` and `x != null` are both `false` for it (§9.8). [#57, #143]

### 8.3 Numeric types

`int` is a 64-bit signed two's-complement integer. `float` is an IEEE
754 binary64 value. [#14, #15]

**Promotion.** `int` and `float` mix freely in every binary operator.
When one operand is `int` and the other `float`, the `int` operand is
converted to `float` as if `.toFloat()` had been written, and the
result is `float`. `/` always yields `float`, even for two `int`
operands. `+`, `-`, `*` and `%` yield `int` for two `int` operands.
Promotion affects what an expression evaluates to, not what a declared
type accepts: `int x = a + b` with `b:float` is a compile error, and so
is `float x = 5`. The only conversions from `float` to `int` are
`Math.floor`, `Math.ceil`, `Math.round` and `Math.trunc` (§16.2). [#143]

**Division and modulo by zero** do not fail the program: the result is
the operand type's `null` (§8.2). `Math.floorDiv` follows the same
rule. Using a null number in further arithmetic is unspecified. [#57, #188]

**Methods.** `int` has `.toFloat()`, `.toText()` and `.toChar()`;
`float` and `bool` have `.toText()` (§16.3). Integer overflow wraps.

### 8.4 `text`

`text` is an immutable UTF-8 string. Characters are Unicode code
points: `s[i]`, `.length`, `.charCodeAt(i)` and `.split('')` all count
code points, never bytes or UTF-16 units. [#150, #249, #251]

- `s[i]` yields the `i`-th code point as a fresh one-character `text`,
  or `null` when `i` is negative or past the end; it is read-only.
- `.length` is the code-point count, computed by a scan; `null` reads 0.
- `a == b` and `a != b` compare content. `a + b` concatenates two
  `text` values. A template literal builds text from any values with a
  text form.
- Every `text` binding owns a private copy of its buffer (§13.2); no
  two bindings share one, so assignment is always a copy.
- Methods: §16.3.

### 8.5 `ascii`

`ascii` is a string of one-byte characters with its length stored in a
header, so `.length`, `s[i]` and `.charCodeAt(i)` are O(1). It is
reference counted: `ascii b = a` shares one buffer. [#256]

- A string literal assigned to an `ascii` is converted at compile time;
  a literal containing a non-ASCII character is a compile error.
- `text.toAscii()` converts at runtime and yields `null` when the text
  is not representable; `ascii.toText()` always succeeds and copies.
- `s[i]` yields a one-character `ascii` from a table of 128 immortal
  values, or `null` out of range. `.slice(start, end)`, `+`, `==`, `!=`,
  and interpolation are supported (§16.3).
- `text` and `ascii` are distinct types with no implicit conversion.

### 8.6 `blob`

A `blob` is the bytes of a file together with the path they came from.
Declaring `blob f = path` loads the file synchronously; an unreadable
path yields an empty blob whose `.exists()` is `false`. Its bytes are
readable through `.length`, `.byteAt(i)` and `.slice(start, end)`
(§16.3). A blob is reference counted; assignment shares the handle. A blob read from a
database column has no path (§15.3). Methods: §16.3. [#36, #109]

### 8.7 Arrays: `arr[T]` and `amor arr[T]`

`arr[T]` is a growable, homogeneous, zero-indexed sequence whose
element type `T` may be any type, including another array or map. An
array is a single reference-counted value; `arr[int] b = a` aliases,
so growth through `b` is visible through `a`. [#26, #79]

- **Literals**: `[e1, e2, ...]`; every element must be assignable to
  `T`. A literal of only `null`s is assignable to any array type. [#62]
- **Indexing**: `xs[i]` with `i:int` reads or writes an element and is
  **not bounds-checked** (§9.3, §14.4).
- **`.length`**: a read-only `int`. [#63]
- **Growth**: `push`, `pop`, `shift`, `unshift`, `splice`, and search
  `indexOf`, sorting `sort`, joining `join` (§16.3). `pop`/`shift` on
  an empty array yield `null`; `splice` clamps; `indexOf` yields `-1`.
  A plain `arr[T]` resizes its buffer to exactly the new length on every
  growth. [#96, #130, #184]
- **`amor arr[T]`** is a distinct type with the same operations whose
  buffer grows geometrically. It is not assignment-compatible with
  `arr[T]` in either direction, and a variable of that type must have
  an initializer. `amor` may only precede `arr`; `amor map[T]` is a
  compile error. [#156, #174, #175]
- **Media arrays**: `arr[img]`, `arr[blob]` and `arr[aud]` literals
  accept a text path per element, loading each, or an existing value of
  the element type, and may mix the two. [#137]

### 8.8 Maps: `map[T]`

`map[T]` associates `text` keys with values of one type `T`, where `T`
may be any type except `arr[...]` or `map[...]`. A map is a
reference-counted hash table with average O(1) get, set and delete and
**unspecified iteration order**. [#72, #175]

- **Literals**: `{ key: value, ... }` and `{}`. Every key is a `text`
  expression: a string literal, or any other expression of type `text`
  (an unquoted identifier is a variable reference, never a bareword
  key). `{ name }` is shorthand for `{ 'name': name }`. All values must
  have one type (`null` excepted). Two identical string-literal keys in
  one literal are a compile error; otherwise the last value for a key
  wins. [#154, #162] A `{...}` written where a struct is expected is a
  struct literal instead (§8.9.4); the expected type is what
  distinguishes them.
- **Access**: `m[key]` reads a value, yielding `null` for a missing
  key; `m[key] = v` adds or replaces.
- **Removal**: `delete m[key]` / `delete m.key` (§10.11).
- **Methods**: `.forEach(fn)`, `.keys()`, `.values()` (§16.3). [#186]

### 8.9 Structs

#### 8.9.1 Declaration and instances

*StructDeclaration* ::= `struct` *Identifier* `{` { *Identifier* `:` *Type* } `}`

A struct is a record type declared at the top level; its fields are
statically typed. A field may be of the struct's own
type or of any struct or table declared anywhere in the program. A
declaration `User u` creates a fresh instance whose fields hold their
zero values (§8.9.2); fields are populated by assignment, or by a
struct literal (§8.9.4). [#27, #78, #106, #288]

```festina
struct User {
    id:int
    name:text
    active:bool
}

User user
user.id = 1
user.name = 'Patrick'

User other = {'id': 2, 'name': 'Brad'}   // active keeps its zero value
```

A struct value is a reference: `User b = a` makes `b` and `a` name the
same instance, and a struct passed to a function is the caller's
instance. Equality with `==`/`!=` between two structs is not defined;
`x == null` is. `indexOf` compares structs by identity. [#97, #102]

#### 8.9.2 Zero values and auto-vivification

A never-assigned field reads as its type's zero value (§8.2). A field,
local or global whose type is a struct, array or map is created empty
on first reach — read or write — once, and keeps its identity
afterwards, so `b.inner.n` and `b.xs.push(1)` work with nothing assigned
first. [#97]

#### 8.9.3 Structs as query targets

Any struct whose fields are all of queryable types may receive the
rows of a `sqlite()` query (§15.5). [#112]

#### 8.9.4 Struct literals

*StructLiteral* ::= `{` [ *FieldEntry* { `,` *FieldEntry* } ] `}`
*FieldEntry* ::= *StringLiteral* `:` *Expression* | *Identifier*

A struct literal builds a fresh instance and assigns its fields. It is
spelled exactly like a map literal (§8.8); **the expected type at the
position decides which one it is.** Where a `{...}` is expected to be a
struct, it is a struct literal; everywhere else it remains a map
literal. [#288]

```festina
struct User {
    id:int
    name:text
    active:bool
}

User a = {'id': 1, 'name': 'Patrick', 'active': true}
User b = {'name': 'Brad'}          // id 0, active false
User c = {}                        // same as `User c`
```

**Keys are string literals.** A field name is resolved at compile time,
so a key must be written as a string literal — never as a variable or
any other expression. This is what keeps one `{...}` spelling from
meaning two different things: an unquoted identifier is a variable
reference in a struct literal exactly as it is in a map literal, and
`{'name': x}` names the same key whatever the target type. `{ name }`
remains shorthand for `{ 'name': name }` (§8.8), so a field may be
filled from a same-named variable:

```festina
text name = 'Brad'
User d = { name }                  // d.name = 'Brad'
```

Rules:

- **Omitted fields keep their zero values** (§8.9.2). A literal always
  produces a *complete, fresh* instance, so assigning one to an existing
  binding replaces the whole value rather than updating the named fields
  — `u = {'id': 2}` leaves `u.name` empty, it does not preserve it.
- **An unknown field name is a compile error**, as is naming the same
  field twice, and as is a key that is not a string literal.
- **Each value must be assignable to that field's declared type** under
  §10.2, including its coercions — so a `text` path may initialize an
  `img`/`aud`/`blob` field exactly as it may a variable of that type.
- **A struct value is a reference** (§8.9.1), so the literal's instance
  is what the binding names; nothing is copied.

**Where the expected type is known.** A struct literal may appear in a
variable declaration's initializer and on the right of an assignment,
including assignment to a field or an array/map element. The expected
type propagates inward from there, so literals nest: into a struct-typed
field's value, into the elements of an `arr[Struct]` literal, and into
the values of a `map[Struct]` literal.

```festina
struct Point { x:int  y:int }
struct Shape { origin:Point  tags:map[text] }

Shape s = {'origin': {'x': 1, 'y': 2}, 'tags': {'kind': 'box'}}
arr[Point] ps = [{'x': 1, 'y': 2}, {'x': 3, 'y': 4}]
ps[0] = {'x': 9, 'y': 9}
```

In `Shape` above, `origin`'s `{...}` is a struct literal and `tags`'s is
a map literal; the field's own declared type is what distinguishes them.

A struct literal is a fresh construction, so it may initialize a
manually-managed binding (§10.11):

```festina
User? u = {'name': 'Brad'}
clear u
```

Function arguments and `return` expressions are **not** struct-literal
positions; build the value in a local and pass that. Tables (§8.10) have
no literal form.

### 8.10 Tables

*TableDeclaration* ::= `table` *Identifier* `{` { *Identifier* `:` *Type* } `}`

A table declares both a SQLite table, kept in sync with the declaration
(§15.2), and a *row type* usable as `arr[Table]`, as a binding, a
parameter, a return type, an element or a field. Column types must be
queryable: `int`, `float`, `bool`, `text`, `blob`, `img` or `aud`.
[#28, #101]

A row is an ordinary reference-counted value that may outlive the array
it came from; row bindings alias. In addition to its columns a row has
the read-only `rowid` and the method `undefined(col)` (§15.5). A row
binding must be declared with an initializer (there is no fresh row
constructor); a row may not cross a thread boundary. [#178, #265]

### 8.11 Enums

*EnumDeclaration* ::= `enum` *Identifier* `=` *Type* { `,` *Type* }

An enum is a tagged union over its member types, which may be any
types (structs, primitives, arrays, maps, handles) but not another
enum, and may be declared in any order. Duplicate members are a compile
error. [#176]

- **Coercion** is one-directional: a member value is assignable to the
  enum type at every assignable position (declaration, argument,
  return, field, element, message). The reverse is a compile error.
- **`typeof e`** (§9.6) yields the name of the member currently held,
  never the enum's own name.
- **Field access** `e.field` is permitted only when every member is a
  struct and exactly one member declares that field name; an enum whose
  members share a field name is a compile error at the declaration.
  Reading a field the current variant lacks is a runtime failure.
- A never-assigned enum binding is `null`; `typeof` or field access on
  it is a runtime failure.
- **`match`** (§10.9) dispatches on the tag with exhaustiveness
  checking.
- **Representation** is implementation-defined: a pure-struct enum
  must cost no allocation beyond the member itself; a mixed enum may
  box the value. A struct produced by JSON parsing is a valid member
  of every enum that lists its type. [#267]

### 8.12 Function types

`func[P1, P2, ...]:R` is the type of a function taking parameters of
types `P1, P2, ...` and returning `R` (`void` for none); `func[]:void`
is the zero-argument void type. Function types are compared
structurally. A function value is a plain reference to one declared or
arrow function (§11.1); it is not reference counted and captures
nothing. `null` is a valid value. A function value may be stored in a
variable, parameter, field, element or map value and called through any
of them with the same checking as a direct call. [#141, #142]

### 8.13 Images and audio: `img`, `aud`

`img` is a decoded raster image; `aud` a decoded audio clip. Each is
declared from a text expression naming a file, loaded synchronously
(`img hero = 'hero.png'`, `aud hit = dir + 'hit.wav'`), from a database
column (§15.3), from a builtin (`blankImage`, `saveCanvas()`, `clip`),
or in the background with `.callback()` (§12.5). The format is sniffed
from the bytes, never the extension: PNG and JPEG for `img`; 16-bit
PCM WAV and MP3 for `aud`. A synchronous load of an unreadable,
unrecognized or corrupt file is a runtime failure. Both are reference
counted handles; assignment shares. Neither has a text form: passing
one to `log()`, a template or `throw` is a compile error. [#37, #38,
#101, #114, #118, #172]

### 8.14 Style types: `color`, `font`

`color` and `font` are declared from a **string literal** and resolved
by the compiler at that declaration; a name the compiler does not
recognize is a compile error. A runtime `text` can never become a
`color` or `font`; the numeric forms `fillStyle(r, g, b)`,
`borderColor(r, g, b)` and `changeFont(px, style, family)` exist for
values computed at runtime. Neither type is managed; both are copied
freely. [#90, #91]

- A **color literal** is one of the 148 CSS named colors
  (case-insensitive), `#rgb`, `#rrggbb`, `none` or `transparent`. A
  `color` is a packed `0xRRGGBB` integer; `none` is a negative value
  and is the same value as `null`, so `c == null` tests for "no color".
  [#189]
- A **font literal** is the CSS shorthand with its parts in any order
  and any part omitted: `italic`/`oblique`, `bold`, a size as a bare
  number or `<n>px`, and a family. An omitted part means "unchanged".
  The empty literal is a compile error.

### 8.15 `regex`

A `regex` is a compiled POSIX Extended Regular Expression created from
a literal `/pattern/flags` (§7.5.5) or the builtin `regex(pattern[,
flags])`. Flags: `i` (case-insensitive) and `g` (every match, honored
by `.replace()` only). `\w`, `\d`, `\s`, their negations and `\b` are
guaranteed on every platform; there are no capture groups,
backreferences or non-greedy quantifiers; inside `[...]` a backslash is
literal. An invalid pattern is a runtime failure. A literal is compiled
once per site for the life of the process; `regex()` is memoized per
call site. `regex` is reference counted. Methods: `.test(text)` and,
on `text`, `.match`, `.replace`, `.split` (§16.3). [#67, #107, #118, #122]

### 8.16 Network types: `url`, `http`, `socket`

- `url` is the read-only result of `parseURL(text)` (§19.6).
- `http` is a request or response value with fields `url`, `method`,
  `code`, `headers`, `callback` and a body; it is constructed with the
  `http` literal (§9.1.4) or received by `on request`. Fields are
  read-only after construction. [#162]
- `socket` is a live WebSocket connection with the field `state` and
  the methods `send` and `close` (§19.4).

All three are reference counted. None may be sent through
`postMessage` (§20.4).

### 8.17 `thread` values

A `thread` value identifies the sender of a message inside an
`on message` handler. It has exactly one field, `.main:bool`, and one
method, `.reply(x)`. It cannot be compared (to `null` or another
thread), has no text form, cannot be sent as a message, and is not
reference counted. It may be stored in fields and containers. Declared
threads themselves are named by their declaration name, which is not an
expression of type `thread` (§20). [#216, #218]

### 8.18 Manually-managed types: `T?`

A trailing `?` on the type of a variable declaration or of a function,
handler or thread-handler parameter opts that binding out of automatic
memory management (§13.4). `T?` is a distinct type from `T`: neither is
assignable to the other. The one exception is that a `T?` declaration
may be initialized from a *fresh construction* of `T` — a literal, a
`regex()` call, or any function or method call — and `drawImage`
accepts an `img?` source. `?` may not appear inside another type, on a
struct field, on a return type, or with `const`. On `int`, `float`,
`bool`, `color`, `font`, `func` and `table` it is accepted and has no
effect; on `text` it does not change the type but does suppress
automatic freeing. [#202–#205, #241, #257]

### 8.19 Type resolution

`arr[T]`, `map[T]`, `func[...]`, `T?` and identifier types are resolved
recursively through the declarations in scope, producing an explicit
internal representation such as `ArrayType(StructType(User))`. Unknown
names are compile errors of the form `error: unknown type 'Person'`.
[#12, #13, #49]

### 8.20 Assignability

A value of type *S* is assignable to a binding of type *T* when:

1. *S* and *T* are the same type (structurally for `func`, `arr`,
   `map`);
2. *S* is `null` (every type accepts `null`);
3. *T* is a numeric type and *S* is the other numeric type only through
   the operator promotion of §8.3 — never at a declaration;
4. *S* is a member of enum *T* (§8.11), except when *T* is manually
   managed;
5. *S* is `text` and *T* is `blob`, `img`, `aud`, `color`, `font` or
   `ascii`, in which case the text is a path (loaded at runtime) or a
   literal (resolved at compile time) as each type specifies;
6. *T* is `T'?` and the expression is a fresh construction of `T'`
   (§8.18);
7. *T* is an array or map type and *S* is a literal of only `null`s.

Anything else is a compile error `cannot assign value of type S to T`.
`amor arr[T]` and `arr[T]`, `T?` and `T`, `text` and `ascii`, `struct`
and `table` of the same shape, and `blob` and `text` are all distinct.
[#20, #50, #109, #143]

### 8.21 Text rendering of values

`log()`, template substitution, `throw`, `troubleshoot()`, `fail()`, a
`body` in an `http` literal and `socket.send()` render a value as
text; `.toText()` is the explicit form. [#114, #115, #190, #192]

| Type | Rendering |
|---|---|
| `int`, `float`, `bool` | the number, `true`/`false`, or `null`; a non-finite `float` renders `null` |
| `text`, `ascii` | the string itself |
| `blob` | its bytes up to the first NUL, as text |
| struct, table row, `arr`, `map`, enum | JSON: objects with field names, arrays, `null`; text escaped with `\"`, `\\`, `\n`, `\r`, `\t`, `\uXXXX` for other control characters and raw UTF-8 otherwise; a handle field renders a placeholder such as `"<blob>"` or `null`; a row column the query did not select is omitted; depth is capped at 32 |
| `img`, `aud`, `thread`, `func`, `regex`, `http`, `socket`, `url`, `color`, `font` | no text form — a compile error |

The inverse operations are `text.toStruct(T)` and `text.toArr(T)`
(§16.4).

## 9 Expressions

Every expression has a static type. Operands are evaluated left to
right. The productions below are in increasing order of precedence;
§9.14 summarizes them.

### 9.1 Primary expressions

*PrimaryExpression* ::=
  *Identifier* | *NumericLiteral* | *StringLiteral* | *TemplateLiteral*
| *RegexLiteral* | `true` | `false` | `null` | `(` *Expression* `)`
| *ArrayLiteral* | *MapLiteral* | *ArrowFunction*

#### 9.1.1 Identifiers

An identifier names a variable, constant, parameter, function (as a
first-class value, §8.12), thread declaration (§20), or global object
(§16.2). An unknown name is a compile error.

#### 9.1.2 Array literals

*ArrayLiteral* ::= `[` [ *Expression* { `,` *Expression* } ] `]`

The literal's element type is taken from context (the declared type of
the binding, parameter, field or element it initializes); every element
must be assignable to it (§8.7). A bound-parameter array passed to
`sqlite()` is the one place a literal may mix element types (§15.4).

#### 9.1.3 Map literals

*MapLiteral* ::= `{` [ *MapEntry* { `,` *MapEntry* } ] `}`
*MapEntry* ::= *Expression* `:` *Expression* | *Identifier*

See §8.8. A `{` at the start of a statement begins a block, not a map
literal.

#### 9.1.4 `http` literals

A map-literal-shaped expression whose context requires an `http` value
(an `http` binding's initializer, `req.send(...)`'s argument, the
receiver of `{...}.send()`, or the `http {...}` statement) is an `http`
literal. Its keys must be string literals drawn from `url`, `method`,
`code`, `headers`, `body` and `callback`, all optional; any other key,
or a computed key, is a compile error. `body` accepts any value with a
text form, or a `blob`, `img` or `aud` (§19.2). [#162]

#### 9.1.5 Arrow functions

*ArrowFunction* ::= ( *Type* | `void` ) `(` [ *Parameters* ] `)` `=>` *Expression*

An arrow function evaluates to a `func[...]:R` value referring to a
compiler-generated top-level function. A `void` arrow's body is an
expression evaluated for effect; otherwise the body is the return
value. Arrow functions obey every rule of §11.1, including the absence
of closures: the body may reference globals and its own parameters
only. [#142]

### 9.2 Member access

*MemberExpression* ::= *Expression* `.` *Name*

*Name* is an identifier or a reserved word. Member access reads or
writes a struct field or table column, reads an enum member's field
(§8.11), reads a built-in property, or names a method for a call (§9.4).
Naming a method without calling it, or an unknown member, is a compile
error. Built-in properties:

| Receiver | Property | Type | Writable |
|---|---|---|---|
| `arr[T]`, `text`, `ascii`, `blob` | `length` | `int` | no |
| `img` | `width`, `height` | `int` | no |
| table row | `rowid` | `int` | no |
| `http` | `url`, `method`, `code`, `headers`, `callback` | see §19.2 | no |
| `url` | `protocol`, `username`, `password`, `hostname`, `port`, `pathname`, `searchParams`, `hash` | see §19.6 | no |
| `socket` | `state` | `map[text]` | entries writable |
| `thread` | `main` | `bool` | no |
| `environment` | any name | `text` | no |
| `Math` | `PI`, `E` | `float` | no |

A struct field named `length` shadows nothing; it is an ordinary field.

### 9.3 Indexing

*IndexExpression* ::= *Expression* `[` *Expression* `]`

| Receiver | Index | Result | Out of range |
|---|---|---|---|
| `arr[T]`, `amor arr[T]` | `int` | `T`, readable and writable | **undefined** (§14.4) |
| `text` | `int` | one-character `text`, read-only | `null` |
| `ascii` | `int` | one-character `ascii`, read-only | `null` |
| `map[T]` | `text` | `T`, readable and writable | read `null`; write adds |
| `environment` | `text` | `text`, read-only | `null` |
| a pool name | `int` | a thread instance (§20.6) | silent no-op |

Array indexing is a raw memory access: a read past either end yields
arbitrary memory and a write corrupts it. `.length` is always correct;
nothing else is checked. This is the only unchecked operation in the
language. [#65, #97, #150]

### 9.4 Calls

*CallExpression* ::= *Expression* `(` [ *Arguments* ] `)`
*Arguments* ::= *Expression* { `,` *Expression* }

A call invokes a declared function by name, a `func`-typed value, a
built-in function (§16.1), or a method on a receiver. Arguments are
matched to parameters by position; arity and each argument's
assignability are checked at compile time. There are no variadic
functions, default arguments, named arguments or overloading beyond the
fixed alternative arities that specific builtins define (for example
`drawRect` with 4, 5 or 6 arguments). A `void` call may not be used as
a value.

Two builtin methods take a **type** argument rather than a value:
`text.toStruct(T)` and `text.toArr(T)` (§16.4).

### 9.5 Postfix increment and decrement

*PostfixExpression* ::= *Expression* ( `++` | `--` )

The operand must be a mutable `int` variable; any other operand is a
compile error. `x++` adds one to `x` and `x--` subtracts one. The
expression's own value is unspecified; use it as a statement or as a
`for` update. [#66]

### 9.6 Unary operators

*UnaryExpression* ::= ( `-` | `+` | `!` ) *UnaryExpression* | `typeof` *UnaryExpression* | *PostfixExpression*

- `-x` negates and `+x` yields a numeric operand unchanged; the type is
  the operand's.
- `!b` requires `bool` and yields `bool`.
- `typeof e` yields a `text` naming the concrete runtime type of `e`:
  the member name for an enum value (§8.11), otherwise the static type
  name (`'int'`, `'User'`, `'arr[int]'`). [#176]

### 9.7 Multiplicative and additive operators

`*`, `/`, `%`, `+` and `-` on numeric operands follow §8.3. `+` on two
`text` operands, or two `ascii` operands, concatenates and yields a
fresh value. No other operand types are permitted; there is no implicit
conversion of a number to text (use a template literal). [#55, #143, #256]

### 9.8 Relational and equality operators

`<`, `>`, `<=` and `>=` require numeric operands (mixed `int`/`float`
allowed) and yield `bool`.

`==` and `!=` yield `bool` and are defined for:

- two numeric operands (mixed allowed), two `bool`, two `color`;
- two `text` or two `ascii` operands, compared by content;
- any operand and `null`, for every type except `thread`; for a
  reference type this tests whether the binding holds a value; a null
  `float` is NaN and is equal to nothing, itself included. [#102, #143]

Comparing two structs, arrays, maps or handles for equality, comparing
a `blob` with a `text`, comparing a `thread` value with anything, and
`===`/`!==` are compile errors. [#18, #109, #216]

### 9.9 Logical operators

`&&` and `||` require two `bool` operands, short-circuit, and yield
`bool`. [#193]

### 9.10 Conditional operator

*ConditionalExpression* ::= *Expression* `?` *Expression* `:` *Expression*

The condition must be `bool`. The two branches must have the same type;
either branch may be `null`, in which case the result has the other
branch's type. An `int`/`float` mix is a compile error. [#20, #193]

### 9.11 Assignment

*Assignment* ::= *Target* `=` *Expression*
*Target* ::= *Identifier* | *MemberExpression* | *IndexExpression*

The right side must be assignable (§8.20) to the target's type. A
constant, `environment`,
`s[i]` on `text`/`ascii`, `.length`, `rowid`, `screenWidth`,
`screenHeight`, `devicePixelRatio`, `clientWidth`, `clientHeight`, and
the fields of `http` and `url` are read-only targets; assigning to any
of them is a compile error. Assignment is used as a statement; its value
is unspecified.

Assigning to a struct field, array element or map entry stores the new
value before releasing the old one (§13). Assigning a new value to a
variable releases the old value according to §13.2.

*Note:* An assignment of the exact shape `` s = `${s}...` `` or
`s = s + ...`, where `s` is a plain `text` variable, parameter or
global that appears once at the front and every further piece is a
literal, a variable or a plain field read, is compiled as an in-place
append with amortized O(1) cost. Nothing about it is observable but
the time. [#243]

### 9.12 Template literals

See §7.5.3. Substitutions are rendered per §8.21.

### 9.13 Operator precedence

From highest to lowest: [#66]

| Level | Operators | Associativity |
|---|---|---|
| 1 | member `.`, index `[ ]`, call `( )`, postfix `++` `--` | left |
| 2 | unary `-` `+` `!` `typeof` | right |
| 3 | `*` `/` `%` | left |
| 4 | `+` `-` | left |
| 5 | `<` `>` `<=` `>=` | left |
| 6 | `==` `!=` | left |
| 7 | `&&` | left |
| 8 | `\|\|` | left |
| 9 | `? :` | right |
| 10 | `=` | right |

### 9.14 Evaluation order

Operands and arguments are evaluated left to right. `&&`, `||` and
`? :` evaluate only the operands they need. A `match` subject is
evaluated once per arm test but is restricted to side-effect-free
forms (§10.9).

## 10 Statements

*Statement* ::=
  *Block* | *VariableDeclaration* | *ConstantDeclaration*
| *ExpressionStatement* | *IfStatement* | *WhileStatement*
| *ForStatement* | *MatchStatement* | *TryStatement* | *ThrowStatement*
| *ReturnStatement* | `break` | `continue` | *FreeStatement* | *ClearStatement*
| *DeleteStatement* | *ImportDeclaration* | *DatabaseURLStatement*
| *Declaration*

*Declaration* (functions, structs, tables, enums, event handlers,
threads) is defined in §11. A statement ends at a line terminator or `;`
(§7.1).

### 10.1 Blocks

*Block* ::= `{` { *Statement* } `}`

A block introduces a scope (§6.7). Every compound statement's body is a
block; braces are required.

### 10.2 Variable and constant declarations

*VariableDeclaration* ::= *DeclaredType* *Identifier* [ `=` *Expression* ]
*ConstantDeclaration* ::= `const` *Type* *Identifier* `=` *Expression*

A declaration states its type; there is no `var`, `let` or inference.
Without an initializer the binding holds its zero value (§8.2). The
initializer must be assignable to the declared type (§8.20). A
constant must be initialized and may not be reassigned or freed;
`const` composes with `amor` (`const amor arr[int] xs = []`) but not
with `?`. An `amor arr[T]` variable requires an initializer. A
`table`-row variable requires an initializer. [#21, #22, #174, #178]

A declaration whose type is `blob`, `img`, `aud`, `color`, `font` or
`ascii` and whose initializer is `text` performs the conversion of
§8.20 rule 5.

### 10.3 Expression statements

*ExpressionStatement* ::= *Assignment* | *CallExpression* | *PostfixExpression*
| `http` *MapLiteral* | ( `blob` | `img` | `aud` ) *Expression*

A call whose result is discarded releases that result immediately
(§13.1). `http {...}` builds and sends an anonymous request (§19.5).
`blob 'path'.callback(fn)` (likewise `img`/`aud`) starts an anonymous
background load; the leading type keyword is documentary and the
expression is evaluated for effect (§12.5). [#164, #165]

### 10.4 `if`

*IfStatement* ::= `if` *Expression* *Block* [ `else` ( *IfStatement* | *Block* ) ]

The condition must be `bool`; there is no truthiness (§2.5).
Parentheses around the condition are ordinary grouping and are
optional; `if (a || b) && c { }` groups as written. `else if` chains
are permitted. [#17, #19, #274]

### 10.5 `while`

*WhileStatement* ::= `while` *Expression* *Block*

The condition must be `bool` and is evaluated before each iteration.
[#61]

### 10.6 `for`

*ForStatement* ::= `for` *VariableDeclaration* `,` *Expression* `,` *Expression* *Block*

The three clauses are separated by commas and not parenthesized. The
initialization is a variable declaration scoped to the loop. Execution:
initialize once; evaluate the condition (which must be `bool`); exit if
false; run the body; run the update; repeat from the condition. [#60]

```festina
for int i = 0, i < xs.length, i++ {
    log(xs[i])
}
```

### 10.7 `break` and `continue`

`break` leaves the nearest enclosing `for` or `while` immediately.
`continue` proceeds to the next iteration; in a `for` loop the update
runs first. Either outside a loop is a compile error. There is no
labeled form; only `return` leaves more than one loop at once. Both
reclaim the locals of the iteration (§13.1). [#73]

### 10.8 `return`

*ReturnStatement* ::= `return` [ *Expression* ]

Inside a non-`void` function the expression is required and must be
assignable to the return type; inside a `void` function, a handler or
top-level code an expression is a compile error. `return` may leave a
`try` or `catch` body and any number of loops. [#23]

### 10.9 `match`

*MatchStatement* ::= `match` *Subject* `{` { *StringLiteral* *Block* } [ `default` *Block* ] `}`
*Subject* ::= *Identifier* { `.` *Name* }

`match` dispatches on `typeof` of its subject. Each arm's tag is a
string literal equal to a name `typeof` can answer for the subject's
type; the first arm whose tag equals `typeof subject` runs, otherwise
`default` if present. For an enum subject the arms must cover every
member unless `default` is present; an uncovered member, an unknown
tag, or a repeated tag is a compile error. For a non-enum subject the
one valid tag is its static type name. The subject must be a plain
variable or a chain of field accesses; a call, index or operator
subject is a compile error. `match` is equivalent to, and must compile
to, the corresponding `if typeof s == '...' { } else if ...` chain.
`default` is contextual (§7.4). [#252]

```festina
match shape {
    'Circle' { log(shape.radius) }
    'Square' { log(shape.area) }
    default  { log('other') }
}
```

### 10.10 `try`, `catch` and `throw`

*TryStatement* ::= `try` *Block* `catch` `(` *Identifier* `:` `text` `)` *Block*
*ThrowStatement* ::= `throw` *Expression*

`throw` renders its operand as text (§8.21) and transfers control to
the nearest dynamically enclosing `catch`, through any number of
function calls, binding the text to the catch variable, which is
scoped to the catch body and must be annotated `text`. `catch` is
mandatory; there is no `finally`. `return`, `break` and `continue` may
leave either body, and a catch body may `throw` again. With no enclosing
`try` on the current thread's call stack, `throw` behaves exactly like
`fail(expr)` (§14.2). A throw from a timer, event or message handler
can never reach a `try`, because none is live when the event loop
dispatches it, and so ends the program. Unwinding releases every
managed local and temporary between the throw and the catch (§13.7).
Not available on `wasm32-wasi` (§21.5). [#157, #236, #259]

### 10.11 `free`, `clear` and `delete`

*FreeStatement* ::= `free` *Identifier*
*ClearStatement* ::= `clear` *Identifier*
*DeleteStatement* ::= `delete` *MemberExpression* | `delete` *IndexExpression*

`free x` releases whatever the variable holds and sets it to `null`.
For reference-counted types it is a decrement: a value still
referenced elsewhere survives, and an alias stays usable. For `text` the
buffer is freed. For scalars it is `x = null`. Freeing twice is a
no-op. Constants and ordinary parameters may not be freed; a `T?`
parameter may. After `free`, reading a field through the variable is
undefined (§14.4). [#111]

`clear x` is `free x` that overwrites the bytes with zero before they
are released, for a value whose contents should not outlive it — a key,
a password, a token. It is accepted wherever `free` is, rejected
wherever `free` is, and leaves the binding `null` exactly as `free`
does. Clearing twice is a no-op.

Zeroing happens **only where the storage is actually released**. For a
reference-counted type that means only when this was the last
reference: a value another binding still holds is neither freed nor
zeroed, because overwriting a buffer another binding can still read
would be a use-after-free by construction. `clear` on such a value
decrements and wipes nothing. A program that must guarantee the wipe
has to hold the only reference, which a `T?` binding does by definition
(§13.4) and a `text` binding does always (§13.2).

The zeroing follows the release cascade. Clearing a struct wipes the
struct's own storage and the storage of every field released with it;
clearing an `arr[T]` or `map[T]` covers the elements released with it;
and a nested value that survives on another reference is left intact,
along with the reference that saved it.

Zeroing is not elidable: an implementation must not optimize the write
away on the grounds that the storage is dead, which is the whole reason
the statement exists rather than being spelled `x = '' ; free x`.

`clear` makes no claim about copies the program made earlier, about
values the allocator has already recycled, or about memory the
operating system has paged out or written to a swap file or core dump.
It zeroes one buffer at one moment. [#283]

`delete m[key]` (or `delete m.key`) removes a map entry entirely; a
missing key is a no-op. `delete s.field` releases a struct field and
leaves it `null` (a struct/array/map field re-vivifies on the next
reach). `delete row.col` additionally marks the column undefined
(§15.5). `delete x` on a whole variable is a compile error naming
`free`. [#111]

Both statements work on `T?` bindings and are the only release such a
binding receives (§13.4).

### 10.12 `import` and `DatabaseURL`

See §6.2 and §6.4.

## 11 Declarations

### 11.1 Functions

#### 11.1.1 Syntax

*FunctionDeclaration* ::= ( *Type* | `void` ) `func` *Identifier* `(` [ *Parameters* ] `)` *Block*
*Parameters* ::= *Parameter* { `,` *Parameter* }
*Parameter* ::= *Identifier* `:` *DeclaredType*

```festina
int func add(a:int, b:int) {
    return a + b
}

void func sayHello() {
    log('Hello')
}
```

The return type precedes `func`; a function returning nothing is
declared `void`. Parameters are `name:type`. A parameter's type may
carry the `?` modifier (§8.18). [#23, #24]

#### 11.1.2 Placement and hoisting

A function may be declared at the top level, inside a block, inside
another function or handler, or inside a thread body (§20.5). Wherever
it appears it is a single global function, hoisted per §6.6, so calls
may precede the declaration and functions may be mutually recursive. A
nested declaration is not executed as a statement. Duplicate function
names, and a function named like a builtin, are compile errors. [#140]

#### 11.1.3 Parameters and calls

Parameters are passed by value for scalars, `color`, `font` and
`func`; every other type is passed as a reference to the caller's
value (a `text` is copied only if the callee reassigns it). A callee
borrows its parameters: it may not `free` an ordinary parameter
(§10.11). The return value is retained per §13.1. [#84]

#### 11.1.4 First-class functions and closures

A bare function name used as a value has type `func[...]:R` (§8.12).
There are no closures: a function body, arrow or not, can reference
only globals, its own parameters and its own locals. Certain builtins
(`setTimeout`, `setInterval`, `map.forEach`) require the bare name of
a declared function with a fixed signature rather than an arbitrary
function-typed expression; `.callback(fn)`, `.sort(fn)` and `.live(fn)`
accept any function-typed expression. [#141, #142, #187]

### 11.2 Structs

See §8.9. A struct is declared at the top level, never inside a
function, handler or thread body. Its name lives in the type namespace
(§6.7).

### 11.3 Tables

See §8.10 and §15.2. A table is declared at the top level, never inside
a function, handler or thread body; every declared table is
synchronized against every database the program opens, including each
thread's own (§20.7).

### 11.4 Enums

See §8.11.

### 11.5 Event handlers

*EventHandler* ::= `on` *Identifier* `(` [ *Parameters* ] `)` *Block* | `on` `request` `use` *Identifier*

An event handler declares the program's response to a named event. Its
parameter list must match the event's fixed signature exactly, or (for
`message`) its own declared message type. Handlers are hoisted and
registered before any top-level statement runs (§6.6). At most one
handler per event name per context (top level, or one thread body) is
permitted. A handler runs to completion on the thread that dispatches
it, and a `throw` inside it ends the program (§10.10). [#40, #178]

| Event | Signature | Top level | Thread body | Fires |
|---|---|---|---|---|
| `mouseDown` | `(x:int, y:int, button:int)` | yes | no | a mouse button pressed over the canvas (§17.4) |
| `mouseUp` | `(x:int, y:int, button:int)` | yes | no | a mouse button released |
| `mouse` | `(x:int, y:int)` | yes | no | the pointer moved |
| `mouseWheelUp` | `(x:int, y:int)` | yes | no | one wheel notch up |
| `mouseWheelDown` | `(x:int, y:int)` | yes | no | one wheel notch down |
| `keyDown` | `(key:text)` | yes | no | a key pressed (auto-repeats) |
| `keyUp` | `(key:text)` | yes | no | a key released (once) |
| `resize` | `()` | yes | no | the canvas size changed |
| `close` | `()` | yes | no | the window's close control was used |
| `exit` | `(code:int)` | yes | yes | the program (or thread) is exiting (§12.3, §20.2) |
| `request` | `(req:http)` or `(req:http?)` | yes | yes | an HTTP request arrived (§19.3) |
| `upgrade` | `(s:socket)` | yes | yes | a WebSocket handshake completed (§19.4) |
| `socketMessage` | `(s:socket, msg:blob)` | yes | yes | a WebSocket message arrived |
| `socketClose` | `(s:socket)` | yes | yes | a WebSocket connection ended |
| `message` | `(worker:thread, msg:T)` | yes | yes | a message was posted to this receiver (§20.4) |
| `load` | `()` | no | yes | the thread started (§20.2) |

Declaring any of the nine window events causes a window to exist
(§17.2). `on request use NAME` is sugar defined in §20.9. A handler
whose name is not in this table is accepted and never fires; an
implementation may warn. [#189]

### 11.6 Threads

*ThreadDeclaration* ::= `thread` *Identifier* [ `[` [ *NumericLiteral* ] `]` ] `{` { *ThreadMember* } `}`

See §20.

## 12 Execution Model

### 12.1 The main thread and the event loop

All top-level code, every function, every event handler, every timer
callback and every `.callback(fn)` runs on the program's single main OS
thread, one at a time. The runtime never runs two pieces of main-program
code concurrently, so globals need no synchronization. The only code
that runs elsewhere is a `thread` body (§20) and, for a worker-to-worker
reply, its callback (§20.4). [#151, #163, #222]

After the entry function returns, if anything of §12.2 keeps the
program alive, one event loop multiplexes every pending source: timer
deadlines, window events, listening ports and open connections,
finished background loads and requests, and thread replies addressed to
main. A slow handler delays every other source. [#69, #151, #166]

### 12.2 Program lifetime and exit status

The program exits with status 0 as soon as the entry function has
returned and none of the following remain:

- a pending `setTimeout` or an uncleared `setInterval` (§12.4);
- an open window (§17.2);
- a listening port or an open connection (§19);
- a background load or non-blocking request whose callback has not
  fired (§12.5, §19.5);
- a live declared thread (§20.2).

`close(code)` (§12.3) exits at once with `code`. `fail()` and an
uncaught `throw` exit with status 1 (§14.2). Every live thread is killed
before the process ends, and the database is closed on a normal exit.

### 12.3 `close()`, `on exit` and graceful shutdown

`close(code:int)` runs the top-level `on exit(code:int)` handler, if
declared, with the same code, then exits the process with that code.
It works with or without a window and is unrelated to the window event
`on close`. [#131]

In a program that uses timers, graphics, a listening port or threads,
`SIGINT` and `SIGTERM` take the same path as `close(128 + signal)`:
`on exit` fires with `130` or `143`, listening ports close immediately,
connections already open are given up to 10 seconds to finish, threads
are killed, and the process exits with that code. A program with none of
those keeps the operating system's default signal behavior, and `on
exit` does not fire. A program that combines a window with a listening
port closes the window and exits immediately, with no connection grace
period. `SIGTERM` handling is POSIX-only. [#161, #166, #169]

### 12.4 Timers

| Builtin | Effect |
|---|---|
| `setTimeout(fn, delayMs:int):int` | runs `fn` once after `delayMs` milliseconds |
| `setInterval(fn, delayMs:int):int` | runs `fn` every `delayMs` milliseconds until cleared |
| `clearTimeout(id:int)`, `clearInterval(id:int)` | cancel a timer; the two are interchangeable |

`fn` must be the bare name of a declared zero-parameter `void`
function. Callbacks run on the main thread from the event loop. A
pending timeout or an uncleared interval keeps the program alive.
Timers may not be used inside a thread body. [#69]

### 12.5 Background loading

`path.callback(fn)`, where `path` is any `text` expression and `fn` is
`func[blob]:void`, `func[img]:void` or `func[aud]:void`, yields an
empty value of the matching type immediately and loads the file on a
background worker; `fn` later runs on the main thread with the *same*
value, now filled in. The type is determined by `fn`'s parameter. A
load that fails leaves the value empty (an empty blob, a 1×1
transparent image, a silent clip) and still fires `fn`; unlike a
synchronous load it never fails the program. The program stays alive
until every pending callback has fired. Not available on `wasm32-wasi`
or inside a thread body. [#165, #172]

```festina
void func onLoaded(b:blob) { log(b.toText()) }
blob b = 'large.dat'.callback(onLoaded)
img 'sprite.png'.callback(onImage)      // anonymous statement form
```

### 12.6 Synchronous event delivery

Some builtins fire a handler synchronously at the call site:
`setClientWidth`/`setClientHeight` fire `on resize` when a window is
open (§17.2). Because handlers are hoisted and globals are not (§6.6),
such a call above a global's declaration runs the handler against that
global's zero value. Every other event is delivered from the event loop
after top-level code has finished. [#139, #178]

## 13 Memory Management

### 13.1 Automatic reclamation

Festina has no garbage collector and no manual allocation. Memory is
reclaimed automatically by a combination of compile-time escape
analysis, reference counting and cycle detection, and the compiler
must prefer stack allocation and native representations wherever a
value's lifetime permits. A program may rely on the following: [#43,
#74–#81, #117, #119]

1. A managed local (struct, array, map, handle, `text`, `ascii`, enum,
   row) that is never returned or stored anywhere longer-lived is
   reclaimed when control leaves its declaring block: at the end of a
   function, handler or branch, and at the end of **every iteration**
   of a loop body, including via `break` and `continue`.
2. A value that escapes — assigned to a global, stored in a field,
   element or map value, returned, or aliased — is reference counted
   and freed when its last reference is released. Every reassignment
   of a binding releases the previous value.
3. A call result used as a bare statement, or read through a member
   chain for a scalar, is released immediately.
4. Freeing a struct, array or map releases its managed fields,
   elements or values recursively; freeing a map frees its keys.
5. Query result arrays, rows and their columns are reclaimed like any
   other value (§15.6).
6. Reference cycles among structs, arrays and maps are collected when
   the last outside reference is released (§13.3).
7. `text` globals are not freed at process exit.

When a `struct` local is proven non-escaping it is a stack allocation,
and each recursive call gets its own. An implementation must never
reclaim a value it has not proven unreachable.

### 13.2 Ownership by type

| Type | Assignment `b = a` | Reclamation |
|---|---|---|
| `int`, `float`, `bool`, `color`, `font`, `func`, `thread` | copies the value | nothing to reclaim |
| `text` | `b` receives its own private copy | freed on every reassignment and scope exit, unconditionally |
| `ascii`, `blob`, `img`, `aud`, `regex`, `http`, `socket`, `url`, `enum`, struct, table row, `arr[T]`, `map[T]` | `b` and `a` share one value (one reference each) | reference counted; freed when the last reference drops |
| `/pattern/` literal | shares an immortal compilation | never freed; release is a no-op |
| `T?` | shares without counting | never automatic (§13.4) |

Because `text` is copied on every binding, two text bindings never
observe each other; because everything else aliases, a write through
one binding is visible through every other. [#83, #109, #118, #265]

### 13.3 Cycles

A type that can form a reference cycle (a struct naming itself, or
reaching itself through fields, elements or map values) carries a cycle
detector: when a value of such a type is released but still referenced,
the runtime determines whether only the cycle itself holds it and frees
the cycle if so. A cycle anything outside still reaches is never
touched. Types that cannot form cycles pay nothing. Edges through an
enum-typed field are not walked; a cycle closed only through an enum
field is not collected. [#120, #176]

### 13.4 Manually-managed values

A `T?` binding (§8.18) is never retained on alias, never released at
scope exit or reassignment, and never released by anyone but the
program: `free` and `delete` are its only releases, and omitting them
leaks by design. A `T?` local of struct, array or map type is always
heap-allocated. A `T?` value posted to a thread is shared by reference,
not cloned (§20.4); both sides then hold one uncounted reference, and
exactly one of them must free it. `const T?` is a compile error, as is
`?` on a field, element or return type. [#202–#205]

### 13.5 `free` and `delete`

Defined in §10.11.

### 13.6 Threads

A thread's private state is reclaimed on the same rules within that
thread. Every message except a `T?` value is deep-copied across the
boundary (§20.4), so no reference count is ever touched by two threads.

### 13.7 Unwinding

A `throw` releases every managed local and every call-site temporary
of every frame between the throw and the catching `try`, newest first,
including a rethrowing catch variable and the scratch memory of a
runtime frame (such as a `.sort()` comparator) it passes through. A
program containing no `try` pays nothing for this; one containing a
`try` pays a small per-binding cost. A thrown-through `.sort()` leaves
its array holding a permutation of its elements. [#236, #259]

## 14 Errors and Diagnostics

### 14.1 Compile errors

A compile error must be reported at the earliest stage that can detect
it, in the form

```
file:line:column: error: message
```

naming the file the offending statement came from (§6.2). The column
counts characters (code points), not bytes. [#272] Lexical
errors (an unexpected character, an unterminated string, `${` outside a
template) are reported in the same form. Compile errors include at
least: [#48, #266]

- unknown type, variable, function, struct, table, enum, member or
  event signature;
- invalid argument type or count, invalid return type, non-`bool`
  condition or logical operand, mismatched ternary branches;
- duplicate declaration in a namespace, or redeclaring a builtin;
- invalid or circular import, `DatabaseURL` out of position;
- unsupported operator (`===`, `!==`, `+` on mixed text/number,
  equality between structs), assignment to a read-only target;
- a `color`/`font` from a non-literal, an unknown color name, an
  invalid regex flag, a non-ASCII `ascii` literal, a mixed-type map
  literal, duplicate literal map keys;
- `amor` without an initializer or before `map`; misuse of `T?`;
- `break`/`continue` outside a loop, `return` with the wrong shape;
- `match` non-exhaustive, unknown or duplicate tag, complex subject;
- thread isolation violations (§20.3), messaging without a matching
  `on message`, database-file conflicts, pool misuse;
- a feature unavailable on the selected target or platform (§21.4,
  §21.5).

### 14.2 Runtime failure: `fail()`

`fail(message)` renders `message` as text, prints `fail: <message>` to
standard error and exits with status 1. `fail(message, fields:map[text])`
prints instead one JSON line to standard error with `timestamp`,
`"level":"error"`, `message` and `fields`, then exits with status 1. An
uncaught `throw` is indistinguishable from `fail(expr)`. [#42, #158]

The runtime itself fails the program only for program-authoring
mistakes, including: an unreadable or unrecognized file in a
synchronous `img`/`aud` declaration; an invalid regex pattern; reading
an enum field of the wrong variant or of a `null` enum; `save()` on a
value with no path; `restoreState()` with nothing saved; a path
operation with no open path; an unparseable or mismatched TLS
certificate; `undefined()` with an undeclared column; an audio file in
an unsupported format; opening a window with no display. Everything
environmental follows §2.3.

### 14.3 Exceptions

`try`/`catch`/`throw` are defined in §10.10. The builtins that throw are
`parseURL()` on malformed input, `req.send()` (client form) on a network
or protocol failure, and `.toStruct()`/`.toArr()` on malformed or
mismatched JSON.

### 14.4 Undefined behavior

The following are undefined; a conforming program must not do them:

- reading or writing an array element outside `[0, length)`;
- reading a field through a struct binding that holds `null`, including
  after `free`;
- reading an `int`, `float` or `bool` local before assigning it;
- touching a `T?` value after freeing it, or after handing it off with
  `giveRequest` (§20.9);
- accessing a shared `T?` value from two threads at the same time
  without the program's own synchronization;
- writing shared state from a worker-to-worker reply callback (§20.4)
  concurrently with the main program;
- making a client request from a thread to that same thread's own
  listener from inside one of its handlers.

### 14.5 Implementation-defined behavior

The representation of `null` for `int`, `float` and `bool`; the
internal name of the entry function; the exact moment a cycle is
collected; map iteration order; the width of a mouse-wheel step; the
platform names of non-character keys and of extra mouse buttons; the
window title; the text of diagnostic messages; audio channel count
defaults; the compiled-size cost of a thread pool.

## 15 The Built-in Database

### 15.1 The automatic database

Every program has a SQLite database, by default `festina.sqlite` in the
current working directory, overridable with `DatabaseURL` (§6.4). It is
opened or created at startup before any top-level statement runs, in
WAL mode with `synchronous=NORMAL`, and closed when the program exits.
The programmer never creates, opens, initializes or configures it.
Each thread that declares its own `DatabaseURL` has its own private
database (§20.7), and no two contexts may name the same literal path.
[#29, #46, #113, #199]

### 15.2 Table declarations and schema synchronization

A `table` declaration (§8.10) is authoritative for the SQLite table of
the same name. At every startup, for every declared table and every
database the program opens, the runtime must: [#28, #31]

1. create the table if it does not exist, as if by
   `CREATE TABLE IF NOT EXISTS Name (col TYPE, ...)`;
2. add columns present in the declaration but missing from the table;
3. remove columns present in the table but absent from the declaration;
4. change the type of columns whose declared type differs;
5. preserve existing data wherever possible, using a temporary table
   and data migration when SQLite cannot alter the table directly.

The programmer writes no `CREATE TABLE`, `ALTER TABLE` or migration
code. Synchronization completes before application code runs.

### 15.3 Type mapping

| Festina | SQLite | Notes |
|---|---|---|
| `int` | `INTEGER` | |
| `float` | `REAL` | |
| `bool` | `INTEGER` | stored as 0 or 1 |
| `text` | `TEXT` | |
| `blob` | `BLOB` | the file's bytes |
| `img`, `aud` | `BLOB` | the asset's own encoded bytes, byte-identical on round trip; an image built by `clip()`/`resize()`/`blankImage()` is encoded as PNG |

`null` maps to SQL `NULL` in both directions. Binding is by value; the
program's asset is never retained by the database. A `blob`, `img` or
`aud` read from a column has no path (§16.3). [#30, #101, #109]

### 15.4 Queries

`sqlite(sql:text)` and `sqlite(sql:text, params)` execute one SQL
statement against the current context's database. SQL is passed to
SQLite untouched, so SQLite's own JSON1 and FTS5 extensions are
available as plain SQL. `params` must be an **array literal** whose
elements are bound to `?` placeholders by position; it is the one
array literal that may mix element types. A `sqlite()` call whose SQL
is a string literal is prepared once per call site and reused. [#32,
#33, #94, #113]

A `sqlite()` call is either a statement, discarding any rows, or the
initializer of an `arr[Table]` or `arr[Struct]` binding that receives
the rows (§15.5). The single-value helpers `sqliteInt`, `sqliteFloat`
and `sqliteText` no longer exist (Annex C); a scalar is read through a
struct target. [#219]

### 15.5 Result binding

Result columns are matched to the target type's fields **by name**,
case-insensitively, not by position; a query may select any subset in
any order. A column the query did not produce reads `null`. A query that
matches no rows yields an empty array. [#111]

- **Table targets** (`arr[People]`): each element is a row of the
  declared table. `row.undefined('col')` is `true` when the column was
  not in the result set or has been `delete`d, `false` when the
  database supplied a value or a NULL; naming an undeclared column is a
  runtime failure. `row.rowid` is the SQLite rowid, populated only when
  the SQL selects `rowid` explicitly (`SELECT *` does not). [#188]
- **Struct targets** (`arr[Summary]`): any struct whose fields are all
  queryable types (`int`, `float`, `bool`, `text`, `blob`, `img`, `aud`)
  may receive rows; alias columns to field names. The elements are
  ordinary structs; `undefined()` and `rowid` do not exist on them.
  [#112]

### 15.6 Row lifetime

Rows are reference-counted values (§8.10): an array of rows holds one
reference to each; a row bound, returned, stored or passed elsewhere
survives its array; row bindings alias. [#265]

## 16 The Standard Library

This clause indexes every built-in function, global and method a
program may use, with the clause that defines it. [api.md](api.md) is
the complete reference for each and is normative for details this
clause does not state.

### 16.1 Global functions

| Area | Functions | Clause |
|---|---|---|
| Output | `log(v)`, `fail(msg[, fields])`, `troubleshoot(event, fields:map[text])` | §8.21, §14.2, §16.5 |
| Program | `close(code:int)`, `exec(args:arr[text]):int` | §12.3, §16.5 |
| Database | `sqlite(sql[, params])` | §15.4 |
| Files | `mkdir(path):bool`, `ls(path):arr[text]` | §16.5 |
| Time | `now():int`, `formatTime(ms:int, fmt:text):text` | §16.5 |
| Timers | `setTimeout`, `setInterval`, `clearTimeout`, `clearInterval` | §12.4 |
| Regex | `regex(pattern[, flags]):regex` | §8.15 |
| URL | `parseURL(text):url` | §19.6 |
| Canvas drawing | `drawRect`, `drawCircle`, `drawPixel`, `drawText`, `drawImage`, `clearCanvas`, `clearRect`, `clearCircle`, `clearPixel`, `render`, `saveCanvas`, `blankImage`, `getPixelColor` | §17.3, §17.6 |
| Canvas style | `fillStyle`, `borderColor`, `lineWidth`, `changeFont`, `fillAlpha`, `fillLinearGradient`, `fillRadialGradient`, `measureTextWidth`, `measureTextHeight` | §17.5 |
| Paths and transforms | `beginPath`, `moveTo`, `lineTo`, `curveTo`, `closePath`, `fillPath`, `strokePath`, `translate`, `rotate`, `scale`, `resetTransform`, `saveState`, `restoreState` | §17.5 |
| Window | `setClientWidth`, `setClientHeight`, `enterFullscreen`, `exitFullscreen`, `showCursor`, `hideCursor` | §17.2 |
| Audio | `setMaxAudioPlayers`, `maxAudioPlayers`, `stopAudioPlayer([n])`, `isAudioPlayerPlaying(n)` | §18 |
| Network | `openPort(port)`, `closePort(port)`, `openSecurePort(port, key:blob)` | §19.1, §19.8 |
| Threads | `postMessage(x)` (inside a thread body) | §20.4 |

A user declaration may not reuse any of these names (§6.7).

### 16.2 Global objects and values

| Name | Type | Description |
|---|---|---|
| `Math` | namespace | `floor`, `ceil`, `round`, `trunc` (`float → int`); `floorDiv(int, int):int`; `sqrt`, `abs`, `exp`, `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `log`, `log2`, `log10` (`float → float`); `pow`, `min`, `max`, `atan2` (`(float, float) → float`); `random():float` in `[0, 1)`; constants `PI`, `E`. Rounding a null, infinite or out-of-range float yields null. `random()` is not cryptographic. [#56, #93, #102, #188] |
| `environment` | read-only | `environment.NAME` or `environment['NAME']` is the environment variable as `text`, or `null`. Bare `environment` is a compile error. [#71] |
| `argv` | `arr[text]` | the process arguments, `argv[0]` being the program path; an ordinary mutable array. [#150] |
| `clientWidth`, `clientHeight` | `int`, read-only | the canvas size (§17.2) |
| `screenWidth`, `screenHeight` | `int`, read-only | the physical display size; needs a display [#139] |
| `devicePixelRatio` | `float`, read-only | device pixels per canvas pixel; informational [#181] |
| `DatabaseURL` | assignment target | §6.4, §20.7 |

### 16.3 Methods by type

**`int`**: `.toFloat():float`, `.toText():text`, `.toChar():text`
(UTF-8 encodes a code point; `null` for a negative value, a value above
`0x10FFFF` or a surrogate). **`float`**, **`bool`**: `.toText()`.
[#55, #249]

**`text`** [#68, #116, #150, #159, #249, #251]

| Method | Result | Notes |
|---|---|---|
| `.length` | `int` | code points |
| `s[i]` | `text` | one code point or `null` |
| `.toInt()` | `int` | leading whitespace, optional sign, digits, trailing garbage ignored; `null` if no digits |
| `.toAscii()` | `ascii` | `null` if not representable |
| `.trim()` | `text` | leading and trailing ASCII whitespace removed; byte-oriented, so UTF-8 is safe |
| `.charCodeAt(i)` | `int` | code point at `i`, or `null` |
| `.split(sep)` | `arr[text]` | `sep` is `text` or `regex`; empty pieces kept; an empty separator splits per code point |
| `.match(re)` | `text` | first match or `null`; `g` ignored |
| `.replace(search, repl)` | `text` | `search` is `text` (first occurrence) or `regex` (first match, or every match with `g`) |
| `.toStruct(T)`, `.toArr(T)` | `T`, `arr[T]` | JSON parsing (§16.4) |
| `.callback(fn)` | `blob`/`img`/`aud` | background load (§12.5) |

**`ascii`**: `.length`, `s[i]`, `.charCodeAt(i)`, `.slice(start, end)`,
`.toText()`, `+`, `==`. [#256]

**`arr[T]`, `amor arr[T]`** [#96, #116, #130, #184]

| Method | Result | Notes |
|---|---|---|
| `.length` | `int` | |
| `.push(v)`, `.unshift(v)` | `int` | new length |
| `.pop()`, `.shift()` | `T` | removed element, or `null` when empty |
| `.splice(start, count)` | `arr[T]` | removed elements; clamps, negative start counts from the end |
| `.splice(start, count, insert:arr[T])` | `arr[T]` | also inserts; returns only the removed elements |
| `.indexOf(v)` | `int` | first index or `-1`; by value for scalars, content for `text`, identity for structs/arrays/maps |
| `.sort(cmp:func[T,T]:int)` | — | in place, stable; negative/zero/positive convention |
| `.join(sep:text)` | `text` | elements of `text`/`int`/`float`/`bool`; `null` renders empty |
| `.toText()` | `text` | JSON (§8.21) |

**`map[T]`** [#72, #186]: `m[key]`, `m[key] = v`, `delete m[key]`,
`.forEach(fn)` with `fn` the bare name of a `void` function taking
`(value:T, key:text)`, `.keys():arr[text]`, `.values():arr[T]`
(independent snapshots, unspecified order), `.toText()`.

**struct**, **table row**: field access; `.toText()`; on a row only,
`.rowid`, `.undefined(col:text):bool`. [#111, #188]

**`blob`** [#109, #110, #251]

| Method | Result | Notes |
|---|---|---|
| `.length` | `int` | exact byte count, O(1) |
| `.byteAt(i)` | `int` | the byte at `i` as `0`–`255`, or `null` out of range |
| `.slice(start, end)` | `text` | the bytes in `[start, end)`, clamped; a `text` rather than a blob, since a slice has no path |
| `.toText()` | `text` | the bytes up to the first NUL |
| `.exists()` | `bool` | `false` for a pathless blob |
| `.write(t:text)`, `.append(t:text)` | `bool` | update the bytes and the file; `false` on failure or without a path |
| `.delete()` | `bool` | deletes the file, keeps the bytes |
| `.save()`, `.save(path)`, `.saveCopy(path)` | `bool` | §16.6 |

**`img`** [#92, #134, #135, #188, #189, #234]: `.width`, `.height`;
`.clip(x, y, w, h):img` (a new image; a non-positive size is a runtime
failure); `.resize(w, h)` (in place); `.getPixelColor(x, y):color`;
`.drawRect`, `.drawCircle`, `.drawPixel`, `.drawText`, `.drawImage`
(3- and 5-argument forms) with the canvas argument shapes of §17.3;
`.clear()`, `.clearRect`, `.clearCircle`, `.clearPixel`; `.translate`,
`.rotate`, `.scale`, `.resetTransform`, `.saveState`, `.restoreState`
(the image's own transform, §17.6); `.save()`, `.save(path)`,
`.saveCopy(path)`; `.callback(fn)`.

**`aud`** [#38, #98, #99, #109]: `.play([n]):int`, `.playLoop([n]):int`
(the channel used, or `-1`), `.stop()`, `.isPlaying():bool`, `.save()`,
`.save(path)`, `.saveCopy(path)`, `.callback(fn)` (§18).

**`regex`**: `.test(t:text):bool`. [#67]

**`http`** (§19.2–§19.5): `.ok()`, `.redirect(url:text)`, `.upgrade()`,
`.send(res:http)`, `.send()`, `.toText()`, `.toBlob()`, `.toImg()`,
`.toAud()`.

**`socket`** (§19.4): `.state:map[text]`, `.send(data)`, `.close()`.

**`url`** (§19.6): eight read-only fields.

**`thread`** value (§8.17): `.main`, `.reply(x)`.

**Declared thread name** `NAME` or `NAME[i]` (§20): `.postMessage(x)`,
`.kill()`, `.live(fn)`, `.isAlive()`, `.drain()`, `.giveRequest(r)`;
`.postMessage(x).callback(fn)`.

### 16.4 JSON

`v.toText()` renders any struct, row, array, map or enum as JSON
(§8.21). `text.toStruct(T)` and `text.toArr(T)` parse JSON into a
struct `T` or an `arr[T]`: [#159, #173, #192, #206, #267]

- JSON object keys match struct fields by name, case-insensitively;
  unknown keys are skipped; missing fields keep their zero value; a
  duplicate key's last value wins; JSON `null` is `null`.
- Fields and element types may be nested structs, arrays and maps to
  any depth, including self-referencing structs; a `map[T]` field takes
  every key of the object. A field of any other type (`img`, `func`,
  …) is a compile error, however deeply nested.
- Integers parse with full 64-bit precision; `\uXXXX` escapes,
  including surrogate pairs, are decoded; nesting deeper than 1000
  levels throws.
- Malformed JSON, a value of the wrong shape, or trailing data
  **throws** a descriptive `text` (§10.10). A parse that fails partway
  leaks nothing. JSON parsing does not depend on `try` support and is
  available on every target.

### 16.5 Process, files, time and logging

- `exec(args:arr[text]):int` runs `args[0]` (searched on `PATH`, never
  through a shell) with the remaining arguments, inheriting the
  standard streams, blocks, and yields the exit code, or `-1` if the
  process could not start. Not available on `wasm32-wasi`. [#150, #221]
- `mkdir(path):bool` is `true` only if it created the directory.
  `ls(path):arr[text]` lists entry names (never `.` or `..`) in OS
  order, or an empty array. Neither fails the program. [#132]
- `now():int` is milliseconds since the Unix epoch.
  `formatTime(ms, fmt):text` is `strftime` in local time, or `null` if
  the format produces nothing. [#93]
- `log(v)` prints the text form of `v` (§8.21) and a newline to
  standard output and flushes. [#41, #126]
- `troubleshoot(event, fields:map[text])` prints one JSON line to
  standard output with `timestamp` (UTC), `"level":"info"`, `event`
  (rendered as text) and `fields`; both arguments are required. [#158]

### 16.6 Saving bytes

`blob`, `img` and `aud` share `.save()` (write to the value's own
path), `.save(path)` (adopt `path`, then write) and `.saveCopy(path)`
(write there, keep the own path), each yielding `true` if the write
landed. What is written is the value's own encoded bytes; an image with
no source bytes is written as PNG. `save()` on a value with no path is a
runtime failure; a path naming a directory yields `false`; a failed
save does not adopt the path. [#110]

## 17 Graphics

### 17.1 The canvas model

Every program has an offscreen canvas of `clientWidth` × `clientHeight`
pixels, 800 × 600 by default, whose fresh or cleared pixels are fully
transparent. Drawing calls paint the canvas and never need a display.
`render()` presents the canvas in a window, opening it on first use.
Only `render()`, `enterFullscreen()`, `exitFullscreen()`, the window
events, `screenWidth`/`screenHeight` and `devicePixelRatio` need a
display; `saveCanvas`, image loading, image operations and text
metrics work headless. Graphics builtins may not be used inside a
thread body; `img` methods may. [#39, #95, #136]

### 17.2 The window

A window exists once `render()` runs or the entry function finishes
with any of the nine window events declared; it opens lazily, at the
size `clientWidth`/`clientHeight` have by then, as a normal decorated,
resizable OS window. While a window is open the program blocks in the
event loop until the window closes; closing it runs `on close`, then
the loop ends and the program exits. On screen, transparent canvas
regions appear opaque white. [#95, #179, #180]

- `setClientWidth(w)` / `setClientHeight(h)` resize the canvas at once;
  a non-positive size is ignored. With a window open they also resize
  the window, clear the resized content and fire `on resize` once,
  synchronously. Before the window exists they only set its initial
  size and fire nothing. [#139]
- `enterFullscreen()` / `exitFullscreen()` toggle OS fullscreen; before
  the window exists they set its initial state; the resulting size
  change and `on resize` arrive asynchronously; a redundant call is a
  no-op. [#180]
- `showCursor()` / `hideCursor()` toggle cursor visibility without
  forcing a window open. [#182]

### 17.3 Drawing

All coordinates are `int` pixels. Fills use the current fill style,
outlines the current border color and line width (§17.5); an optional
trailing `color` argument overrides the fill for that one call only,
and a second trailing `color` on `drawRect`/`drawCircle` overrides the
border for that call. [#37, #133, #188]

| Builtin | Effect |
|---|---|
| `drawRect(x, y, w, h[, fill[, border]])` | filled, optionally outlined rectangle |
| `drawCircle(x, y, r[, fill[, border]])` | filled, optionally outlined circle |
| `drawPixel(x, y[, fill])` | one pixel, no border |
| `drawText(t:text, x, y)` | text in the current font and fill; never outlined |
| `drawImage(img, x, y)` | an image at its stored size |
| `drawImage(img, x, y, w, h)` | scaled into a `w`×`h` box |
| `drawImage(img, sx, sy, sw, sh, dx, dy, dw, dh)` | a source rectangle scaled into a destination rectangle |
| `clearCanvas()` | everything to transparent, ignoring the transform |
| `clearRect(x, y, w, h)`, `clearCircle(x, y, r)`, `clearPixel(x, y)` | a region to transparent, honoring the transform |
| `saveCanvas(path):bool` | write the canvas as PNG, with alpha |
| `saveCanvas():img` | a snapshot of the canvas |
| `render()` | present the canvas on screen |
| `getPixelColor(x, y):color` | the painted color of one canvas pixel, or `null` when transparent or out of bounds |
| `blankImage(w, h):img` | a fresh, fully transparent image |

`drawImage` accepts an `img?` source as well as an `img` (§8.18). A
source region past the image's edge draws only the overlap. [#185, #241]

### 17.4 Events

The nine window events are listed in §11.5. `mouseDown`/`mouseUp`
report the pointer position and the button: `1` left, `2` middle, `3`
right, `8` back, `9` forward, otherwise a platform number. `mouse`
reports movement. `mouseWheelUp`/`mouseWheelDown` fire once per notch
with no magnitude. `keyDown`/`keyUp` report the same name for the same
key: the character for a character key, otherwise a platform key name
(`'Left'`, `'Escape'`, `'Return'`); `keyDown` auto-repeats while held,
`keyUp` fires exactly once on release. Wheel scrolling never fires
button events. [#98, #106, #181, #182]

### 17.5 Style, paths, transforms, gradients and text metrics

Style is global state applied to every later draw, on the canvas and
on images alike. Defaults: black fill, no border, 16px sans-serif, full
opacity, identity transform. [#89–#91, #94, #183]

- `fillStyle(c:color)` / `fillStyle(r, g, b)`; `borderColor(c)` /
  `borderColor(r, g, b)` (a negative component means `none`);
  `lineWidth(px:int)`; `changeFont(f:font)` / `changeFont(px, style,
  family)` (`style`/`family` may be `null`; `px <= 0` keeps the size).
  `none` as a fill leaves interiors untouched; as a border it disables
  borders.
- `fillAlpha(a:float)` in `[0, 1]` applies to every fill and to
  `drawImage`, canvas and image forms alike.
- `fillLinearGradient(x1, y1, c1, x2, y2, c2)` and
  `fillRadialGradient(cx, cy, r, inner, outer)` replace the flat fill
  until the next `fillStyle()`; exactly two stops.
- Paths: `beginPath()`, `moveTo`, `lineTo`, `curveTo(cx1, cy1, cx2,
  cy2, x, y)`, `closePath()`, then `fillPath()` or `strokePath()`, each
  of which consumes the path. A path call with no path open is a
  runtime failure.
- Transforms: `translate(dx, dy)`, `rotate(degrees:float)`,
  `scale(sx:float, sy:float)`, `resetTransform()`; the transform applies
  to everything drawn or cleared afterwards. `saveState()` /
  `restoreState()` push and pop the whole drawing state; `restoreState()`
  with nothing saved is a runtime failure.
- `measureTextWidth(t):int` is the advance width and
  `measureTextHeight(t):int` the inked height of `t` in the current
  font; neither needs a display.

*Note:* An opaque flat-color rectangle, circle or pixel at an integer
position with no alpha, gradient, border, scale or rotation may be
painted by a direct pixel path; its output must be identical to the
general path. `FESTINA_NO_DIRECT_FILL=1` disables it. [#240]

### 17.6 Images as drawing targets

An `img` is a self-contained drawing target with its own pixel
coordinates and its own transform and state stack, independent of the
canvas's transform but sharing the global style. Its `drawRect`,
`drawCircle`, `drawPixel`, `drawText`, `drawImage`, `clearRect`,
`clearCircle` and `clearPixel` honor its transform; `clear()` ignores
it. `saveState()`/`restoreState()` on an image push and pop only its
transform, to a depth of 64. Drawing an image onto itself copies the
source first. `resize` changes the image in place for every alias;
`clip` never touches the source. [#134, #234]

## 18 Audio

An `aud` is decoded once at its declaration (WAV 16-bit PCM or MP3,
sniffed from the bytes) and plays through a process-global pool of
numbered **channels**, on a background thread, without blocking the
program. Audio builtins may not be used inside a thread body. [#38, #98–#101, #146]

| Call | Effect |
|---|---|
| `clip.play()` | play once on a channel the pool picks; yields the channel or `-1` |
| `clip.play(n)` | play once on channel `n`, taking it over and releasing any reservation |
| `clip.playLoop()` | loop on a channel the pool picks and **reserve** it |
| `clip.playLoop(n)` | loop on channel `n`, taking it over and reserving it |
| `clip.stop()` | silence this clip on every channel, releasing reservations |
| `clip.isPlaying()` | `true` while any channel plays this clip |
| `stopAudioPlayer(n)` | stop channel `n` and release it |
| `stopAudioPlayer()` | stop every channel |
| `isAudioPlayerPlaying(n)` | `true` while channel `n` plays anything |
| `setMaxAudioPlayers(n)` | channels the pool may assign on its own, clamped to `[1, 64]` |
| `maxAudioPlayers()` | the value actually in effect |

Overlapping `play()` calls layer rather than restart. When every
unreserved channel is busy, the oldest is stolen; when every channel is
reserved, an unnamed `play()` is dropped. A reserved channel is never
auto-assigned or stolen. Channel numbers are clamped into `[0, 64)`.
Freeing the last reference to a clip stops it. [#118]

## 19 HTTP and WebSocket

### 19.1 Servers

`openPort(port:int)` starts accepting HTTP/1.1 connections on `port`;
`closePort(port:int)` stops. Both are silent no-ops on failure (§2.3).
A program may open several ports. Connections are serviced from the
event loop (§12.1), one handler at a time; a listening port or an open
connection keeps the program alive. HTTP/1.1 requests default to
keep-alive, HTTP/1.0 to close, `Connection: close` always closes, and
an idle connection is closed after about 15 seconds. Chunked request
bodies are decoded transparently. Fragmented WebSocket messages are
reassembled. Messages and bodies are capped at 8 MB. [#151, #167, #168]

### 19.2 The `http` value

An `http` value carries `url:text`, `method:text`, `code:int` (`null`
until a response exists), `headers:map[text]` (names lowercased; a
repeated header's last value wins), `callback:func[http]:void` (`null`
means blocking), and a body read through `.toText()`, `.toBlob()`,
`.toImg()` and `.toAud()` (the last two `null` when the body does not
decode; an absent body is an empty `text`/`blob`). It is built by the
`http` literal (§9.1.4), whose `body` may be a `text`, number, `bool`,
struct/row/array/map (rendered as JSON), `blob` (raw bytes), `img` or
`aud` (encoded bytes). Fields are read-only after construction. The
`Host`, `Content-Length`, `Connection` and `Transfer-Encoding` headers
are always computed by the runtime and never taken from `headers`.
[#162, #247]

### 19.3 `on request`

`on request(req:http)` fires once per request with the request fully
parsed. Exactly one of `req.ok()` (200, empty), `req.redirect(url)`
(302), `req.send(res:http)` (`res.code` defaults to 200) or
`req.upgrade()` answers it; later calls on the same request are silent
no-ops, and a handler that answers nothing yields a 200 with an empty
body. [#151]

### 19.4 WebSocket

`req.upgrade()` completes the RFC 6455 handshake (a silent no-op if the
request is not a valid handshake). Thereafter `on upgrade(s:socket)`
fires once, `on socketMessage(s:socket, msg:blob)` once per complete
message (always a `blob`), and `on socketClose(s:socket)` exactly once
when the connection ends, however it ends. `s.state` is a per-connection
`map[text]`; `s.send(data)` sends a text frame (a `blob` sends a binary
frame) and `s.close()` sends a close frame. Pings are answered
automatically; extensions are unsupported. A protocol violation closes
with code 1002, an oversize message with 1009. [#151, #168, #208]

### 19.5 The client

`req.send()` with **zero** arguments sends `req` as an outbound request
to `req.url` (`http://` or `https://`) and, in blocking mode, waits for
the whole response and overwrites `req.code`, `req.headers` and the
body in place; `req.url` and `req.method` are untouched, so a value may
be re-sent. A network or protocol failure **throws**. Same-host plain
HTTP requests reuse a keep-alive connection per OS thread on POSIX.
There is no `fetch()`. [#162, #248]

With a non-null `callback`, `req.send()` returns immediately, the
request runs in the background, and `callback(req)` later runs on the
main thread with the response filled in; on failure `req.code` stays
`null` and the body holds the failure message. The value stays alive
until the callback fires. Not available on Windows (the call blocks) or
inside a thread body. Shorthands: `http req = {...}.send()` as an
initializer, and the statement `http {...}` for an anonymous send.
[#163, #164]

### 19.6 The `url` type

`parseURL(t:text):url` splits an absolute URL into the read-only fields
`protocol` (with its trailing colon), `username`, `password`,
`hostname`, `port:int` (`null` if absent), `pathname`,
`searchParams:map[text]` (percent-decoded) and `hash`; it throws when
`t` has no `://` or a non-numeric port. [#162]

### 19.7 Combining with graphics and timers

Timers, a listening port and a window may coexist; once a window is
open, the graphics loop services the port with up to about 20 ms of
added latency and no shutdown grace period (§12.3). A genuinely separate
server loop is obtained by declaring the handlers inside a thread
(§20.8). [#166, #228]

### 19.8 TLS

`openSecurePort(port:int, key:blob)` is `openPort` over TLS, sharing
the same handlers and loop; `key` is one PEM blob holding the
certificate (or chain, leaf first) and the unencrypted private key. An
unparseable or mismatched key is a runtime failure. Server-side only,
one certificate per port, no SNI, no client certificates, no ALPN.
[#160]

## 20 Threads

### 20.1 Declaration

*ThreadDeclaration* ::= `thread` *Identifier* [ `[` [ *NumericLiteral* ] `]` ] `{` { *ThreadMember* } `}`
*ThreadMember* ::= *DatabaseURLStatement* | *VariableDeclaration* | *ConstantDeclaration* | *FunctionDeclaration* | *EventHandler*

`thread NAME { ... }` declares an isolated worker with its own OS
thread, private state and inbound message queue. `thread NAME[N]`
declares a pool of `N` independent instances (§20.6) and `thread
NAME[]` an auto-sized pool. A body may contain only state
declarations, private function declarations and handlers; a `struct`,
`table` or `enum` is declared at the top level. The handlers a body may
declare are
`load`, `message`, `exit`, `request`, `upgrade`, `socketMessage` and
`socketClose` (§11.5); an unknown handler name in a thread body is a
compile error. Not available on `wasm32-wasi`. [#195, #196, #209, #220]

### 20.2 Lifecycle

Every declared thread starts before the first top-level statement runs,
initializes its private state, runs `on load()` if declared, then waits
for messages. From the main program only: `NAME.kill()` runs the
thread's `on exit(0)`, discards queued messages and callbacks, and
blocks until it has stopped; `NAME.live(fn:func[bool]:void)` restarts a
killed thread and calls `fn(true)`; `NAME.isAlive():bool`;
`NAME.drain()` blocks until everything already queued has been
processed, leaving the thread running. A live thread keeps the program
alive; when the program exits every live thread is killed first, its
`on exit` receiving `0`. A thread never controls another's lifecycle.
[#196, #200, #231, #232]

### 20.3 Isolation

A thread body sees its own state, its own private functions, every
type name and every declared thread name. It cannot read or write a
global variable or constant, call a top-level function, or use canvas,
window, audio, timer, `close`, or background-load builtins. It may use
`log`, `fail`, `throw`/`try`, string, array, map, struct and enum
operations, `Math`, `now`/`formatTime`, `regex`, `mkdir`, `ls`, the
blocking `exec`, `blankImage` and every `img` method, the blocking
`req.send()`, `sqlite` if it declares a `DatabaseURL` (§20.7), and
`openPort`/`closePort`/`openSecurePort` if it declares an HTTP handler
(§20.8). Violations are compile errors. [#195, #211, #212]

### 20.4 Messaging

Each receiver — the main program, or one thread — declares at most one
`on message(worker:thread, msg:T)` naming the type it accepts.
`NAME.postMessage(x)` sends `x` to thread `NAME`, from main or from
another thread; a bare `postMessage(x)` inside a thread body sends to
main. Sending to a receiver with no `on message`, or a value not
assignable to its `T`, is a compile error; more than one shape needs an
`enum`. `worker` is never `null`; `worker.main` tells whether main sent
the message. [#208, #216]

Every message is a **deep copy**. Sendable types are `int`, `float`,
`bool`, `text`, `color`, `font`, `blob`, `img`, `aud`, `url`, and
structs, arrays, maps and enums built from them; a self-referencing
struct, array or map type is rejected. `func`, `http`, `socket`,
`regex`, `table` rows and `thread` values are not sendable. A `T?`
value is the exception: it crosses by reference, uncounted (§13.4).
[#197, #198, #203]

**Request/response.** `worker.reply(x)` inside a handler answers the
message being handled; the first `.reply` in a receiver fixes its
reply type. Every `postMessage` to a receiver that replies must chain
`.callback(fn:func[ReplyType]:void)`, and `.callback` on a receiver that
never replies is a compile error. `fn` runs on main's thread when the
send was addressed to main or made by main, and on the sending
worker's thread when one worker messages another (where writing shared
state from `fn` is a data race, §14.4). A message is answered at most
once; a stashed `thread` value replies to nothing; `kill()` drops
pending callbacks. [#217, #222, #230]

### 20.5 Private state and functions

A variable declared directly in a thread body persists across messages
and is visible only inside that thread. A function declared in the body
is private: callable only from that thread's handlers and other private
functions, able to read and write the thread's state, able to
`postMessage`, hoisted within the body, and without a first-class
value form. [#210]

### 20.6 Pools

`thread NAME[N]` compiles its body once per instance, each with its own
thread, state, queue, database and HTTP context. An instance is
addressed `NAME[i]` with any `int` expression; an out-of-range index is
a silent no-op whose arguments are not evaluated and whose `isAlive()`
is `false`. `NAME.postMessage(x)` and `NAME.giveRequest(r)` without an
index route to an idle instance, falling back to round-robin, never
blocking; every other method requires an index. `thread NAME[]` takes
`N` from the CPU count of the compiling machine minus every other
declared thread's instances, at least 1. A pool may not declare
`DatabaseURL`. [#209, #215, #220, #245, #246]

### 20.7 A thread's own database

A thread body's first statement may be `DatabaseURL = '<literal>'`
(a string literal only). Such a thread has a private database, closed
when it stops, against which every declared table is synchronized, and
may call `sqlite()`; a thread without it may not. The main program's
path (its literal `DatabaseURL`, or `festina.sqlite`) and every thread's
literal must be pairwise distinct, checked at compile time. [#199, #207]

### 20.8 A thread's own HTTP context

A thread that declares `on request`, `on upgrade`, `on socketMessage` or
`on socketClose` has a private connection table and may call
`openPort()`, `closePort()` and `openSecurePort()`, serving traffic
independently of main and of other threads; without such a handler
those calls are compile errors. Two contexts binding the same port fail
as the OS dictates. A thread must not request its own listener from its
own handlers. [#212]

### 20.9 Live connection hand-off

`NAME.giveRequest(r)` (or `NAME[i].giveRequest(r)`), callable only from
the main program, hands a live request to a thread that declares `on
request`, whose handler then answers on the connection's own socket. `r`
must be a manually-managed `http?`, so main's top-level handler must be
declared `on request(req:http?)`; after the hand-off `r` belongs to the
thread and main must not touch it. TLS, already-answered and
already-upgraded connections make it a silent no-op. `on request use
NAME` desugars at parse time to `on request(req:http?) {
NAME.giveRequest(req) }`. [#213, #225, #246]

## 21 Compilation, Targets and Tooling

### 21.1 The compiler pipeline

A conforming compiler must perform, in order: import resolution into
one compilation unit (§6.2); lexing; parsing to an AST; name
resolution; type resolution; semantic analysis (including every check
of §14.1, escape analysis and the `match` desugaring); table metadata
collection; entry function generation; LLVM IR generation; LLVM
optimization; native linking. IR generation must not begin before
name resolution, type resolution and semantic analysis have completed.
The compiler must produce a native executable that does not depend on
the Festina sources, the compiler or Python. [#3, #47]

Performance is a requirement: the compiler must prefer compile-time
work, use LLVM's optimizations (constant folding, dead-code
elimination, inlining, constant propagation, allocation optimization),
and avoid runtime reflection, dynamic type checks, boxing, dynamic
dispatch and unnecessary heap allocation. [#44]

### 21.2 The command line

The compiler executable is named `festina`. [#1, #144, #145, #148, #250]

| Command | Effect |
|---|---|
| `festina compile entry.f [-o out] [--emit-llvm] [--cc CC] [--target=native\|wasm32-wasi]` | compile to an executable (default name: the entry file without `.f`; `.exe` on Windows, `.wasm` for the WASI target) or print LLVM IR |
| `festina run entry.f [--target=...]` | compile to a temporary executable, run it with inherited streams, exit with its code |
| `festina doctor [--fix [--yes]]` | check every dependency and `PATH`; with `--fix`, install what is missing via the detected package manager and fix `PATH`, confirming first |
| `festina update` | fast-forward the installation's own git checkout; refuses on a dirty tree, detached HEAD, diverged history or a packaged binary |
| `festina help` | print the command list |

The compiler caches lex and parse results per source file on disk,
keyed by the file's content and the grammar version; `FESTINA_NO_PARSE_CACHE=1`
disables it. No language behavior depends on the cache. [#253]

### 21.3 Executables and linking

A compiled program links only what it uses: the graphics, audio, HTTP,
TLS and threading runtime parts are linked only when the program calls
into them, so a program without graphics has no display dependency.
SQLite is always linked, statically when possible. Every `log()` write
is flushed; the database is closed on exit. Missing build dependencies
must produce a clear, actionable error naming what is missing and how
to obtain it, and the compiler must not depend on a single specific C
compiler where a common alternative works. [#59, #126, #147]

### 21.4 Platforms

| Target | Core language, database, files, regex, JSON, timers | Windowed graphics | Audio | HTTP/WebSocket, TLS | Threads | `try`/`catch` | Background loads |
|---|---|---|---|---|---|---|---|
| Linux | yes | yes | yes | yes | yes | yes | yes |
| macOS | yes | opt-in (`FESTINA_ENABLE_MACOS_GRAPHICS=1`) | opt-in (`FESTINA_ENABLE_MACOS_AUDIO=1`) | opt-in (`FESTINA_ENABLE_MACOS_HTTP=1`) | yes | yes | yes |
| Windows (MSYS2 UCRT64) | yes | yes | opt-in (`FESTINA_ENABLE_WINDOWS_AUDIO=1`) | yes; no `SIGTERM` grace, client always blocks, no keep-alive reuse | yes | yes | yes |
| `wasm32-wasi` | yes | no | no | no | no | no | no |

Offscreen drawing (no `render()` and no window events) needs no display
and is never gated. A program using a feature its target does not
provide is rejected at compile time. Key names, mouse button numbers
beyond the standard five, and the audio device are platform-defined.
[#121–#129, #169, #235, #238]

### 21.5 The `wasm32-wasi` target

`--target=wasm32-wasi` produces a standalone `.wasm` for a WASI Preview 1
host, with the filesystem sandboxed to the host's preopened directory.
Available: the core language, `table`/`sqlite()` (with a vendored
SQLite, linked only when used), `blob`, `mkdir`/`ls`, regex, JSON
parsing, timers, `argv`, `close`/`fail`. Rejected at compile time:
graphics, audio, HTTP, threads, `try`/`catch`/`throw`, `exec`,
`.callback()`. An uncaught parse failure still ends the program like
any uncaught throw. A compiled `.wasm` also runs in a browser on the
project's own WASI host. [#148, #237, #242, #263]

### 21.6 Runtime environment variables

| Variable | Effect |
|---|---|
| `FESTINA_NO_DIRECT_FILL=1` | disable the direct pixel path (§17.5) |
| `FESTINA_AUDIO_NULL=1` | use a silent audio device |
| `FESTINA_NO_PARSE_CACHE=1` | disable the compiler's parse cache |
| `FESTINA_ENABLE_MACOS_GRAPHICS`, `FESTINA_ENABLE_MACOS_AUDIO`, `FESTINA_ENABLE_MACOS_HTTP`, `FESTINA_ENABLE_WINDOWS_AUDIO` | compile-time platform opt-ins (§21.4) |

---

## Annex A — Grammar Summary

```
Program            ::= { Statement }

Statement          ::= Block | VariableDeclaration | ConstantDeclaration
                     | ExpressionStatement | IfStatement | WhileStatement
                     | ForStatement | MatchStatement | TryStatement
                     | ThrowStatement | ReturnStatement | 'break' | 'continue'
                     | FreeStatement | ClearStatement | DeleteStatement
                     | ImportDeclaration
                     | DatabaseURLStatement | Declaration
Declaration        ::= FunctionDeclaration | StructDeclaration | TableDeclaration
                     | EnumDeclaration | EventHandler | ThreadDeclaration

Block              ::= '{' { Statement } '}'
VariableDeclaration::= DeclaredType Identifier [ '=' Expression ]
ConstantDeclaration::= 'const' Type Identifier '=' Expression
ExpressionStatement::= Assignment | CallExpression | PostfixExpression
                     | 'http' MapLiteral | ('blob' | 'img' | 'aud') Expression
IfStatement        ::= 'if' Expression Block [ 'else' ( IfStatement | Block ) ]
WhileStatement     ::= 'while' Expression Block
ForStatement       ::= 'for' VariableDeclaration ',' Expression ',' Expression Block
MatchStatement     ::= 'match' Subject '{' { StringLiteral Block } [ 'default' Block ] '}'
Subject            ::= Identifier { '.' Name }
TryStatement       ::= 'try' Block 'catch' '(' Identifier ':' 'text' ')' Block
ThrowStatement     ::= 'throw' Expression
ReturnStatement    ::= 'return' [ Expression ]
FreeStatement      ::= 'free' Identifier
ClearStatement     ::= 'clear' Identifier
DeleteStatement    ::= 'delete' ( MemberExpression | IndexExpression )
ImportDeclaration  ::= 'import' Path
DatabaseURLStatement ::= 'DatabaseURL' '=' Expression

FunctionDeclaration::= ( Type | 'void' ) 'func' Identifier '(' [ Parameters ] ')' Block
Parameters         ::= Parameter { ',' Parameter }
Parameter          ::= Identifier ':' DeclaredType
StructDeclaration  ::= 'struct' Identifier '{' { Identifier ':' Type } '}'
TableDeclaration   ::= 'table' Identifier '{' { Identifier ':' Type } '}'
EnumDeclaration    ::= 'enum' Identifier '=' Type { ',' Type }
EventHandler       ::= 'on' Identifier '(' [ Parameters ] ')' Block
                     | 'on' 'request' 'use' Identifier
ThreadDeclaration  ::= 'thread' Identifier [ '[' [ NumericLiteral ] ']' ] '{' { ThreadMember } '}'
ThreadMember       ::= DatabaseURLStatement | VariableDeclaration | ConstantDeclaration
                     | FunctionDeclaration | EventHandler

Type               ::= 'int' | 'float' | 'bool' | 'text' | 'ascii' | 'blob'
                     | 'img' | 'aud' | 'regex' | 'color' | 'font'
                     | 'http' | 'socket' | 'url' | 'thread'
                     | [ 'amor' ] 'arr' '[' Type ']' | 'map' '[' Type ']'
                     | 'func' '[' [ Type { ',' Type } ] ']' ':' ( Type | 'void' )
                     | Identifier
DeclaredType       ::= Type [ '?' ]

Expression         ::= Assignment | ConditionalExpression
Assignment         ::= Target '=' Expression
Target             ::= Identifier | MemberExpression | IndexExpression
ConditionalExpression ::= LogicalOr [ '?' Expression ':' Expression ]
LogicalOr          ::= LogicalAnd { '||' LogicalAnd }
LogicalAnd         ::= Equality { '&&' Equality }
Equality           ::= Relational { ( '==' | '!=' ) Relational }
Relational         ::= Additive { ( '<' | '>' | '<=' | '>=' ) Additive }
Additive           ::= Multiplicative { ( '+' | '-' ) Multiplicative }
Multiplicative     ::= Unary { ( '*' | '/' | '%' ) Unary }
Unary              ::= ( '!' | '-' | '+' ) Unary | 'typeof' Unary | PostfixExpression
PostfixExpression  ::= CallMember [ '++' | '--' ]
CallMember         ::= PrimaryExpression { '.' Name | '[' Expression ']' | '(' [ Arguments ] ')' }
Arguments          ::= Expression { ',' Expression }
PrimaryExpression  ::= Identifier | NumericLiteral | StringLiteral | TemplateLiteral
                     | RegexLiteral | 'true' | 'false' | 'null' | '(' Expression ')'
                     | ArrayLiteral | MapLiteral | ArrowFunction
ArrayLiteral       ::= '[' [ Expression { ',' Expression } ] ']'
MapLiteral         ::= '{' [ MapEntry { ',' MapEntry } ] '}'
MapEntry           ::= Expression ':' Expression | Identifier
ArrowFunction      ::= ( Type | 'void' ) '(' [ Parameters ] ')' '=>' Expression
Name               ::= Identifier | any reserved word
```

## Annex B — Reserved Words and Global Names

**Reserved words** (§7.4): `amor arr ascii aud blob bool break catch
clear const continue delete else enum fail false float for free func
http if img import int let log map match null on return socket sqlite
struct table text thread throw true try typeof var void while`.

**Contextual words**: `default`, `use`, `DatabaseURL`.

**Global names that may not be redeclared** (§16): every function in
§16.1, plus `Math`, `environment`, `argv`, `clientWidth`,
`clientHeight`, `screenWidth`, `screenHeight`, `devicePixelRatio`.

## Annex C — Removed and Superseded Features

An implementation must not provide any of the following. Each was once
specified or shipped and was replaced; the entry that removed it is
cited.

| Removed | Replaced by | Entry |
|---|---|---|
| `int`/`float` never mix; `int / int` yields `int` | implicit promotion; `/` always `float` (§8.3) | #143 |
| `text.replaceAll()` | the `g` flag on `.replace()` (§8.15) | #107 |
| `loadImage()`, `loadAudio()` | `img x = 'path'`, `aud x = 'path'` (§8.13) | #100, #101, #109 |
| `readFile`, `writeFile`, `appendFile`, `fileExists`, `deleteFile` | `blob` methods (§16.3) | #109 |
| `fillStyle('red')`, `font(...)` with inline strings; runtime color/font strings | `color`/`font` types, `changeFont()`, numeric forms (§8.14) | #90, #91 |
| drawing that opens a window implicitly; a white blank canvas; a borderless window; 800×600 opened before top-level code | `render()`, transparent canvas, decorated lazy window (§17) | #95, #136, #179, #180 |
| `on click(x, y)`, `on key(key)`; two-argument `on mouseDown`/`on mouseUp` | `mouseDown`/`mouseUp` with `button`, `keyDown`/`keyUp` (§11.5) | #98, #106, #182 |
| per-clip audio; `aud.stop(n)` | process-global channels; `stopAudioPlayer(n)` (§18) | #99, #109 |
| positional query column matching | matching by name (§15.5) | #111 |
| `sqliteInt()`, `sqliteFloat()`, `sqliteText()` | struct query targets (§15.4) | #219 |
| `amor map[T]` | `map[T]` is always a hash table (§8.8) | #175 |
| `amor arr[T]` as an alias of `arr[T]` | a distinct amortized type (§8.7) | #174 |
| "functions must be declared before use"; functions not first-class | hoisting; `func[...]:R`; arrow functions (§11.1) | #140–#142 |
| `throw` forbidden ("use `fail()`") | `try`/`catch`/`throw` (§10.10) | #157 |
| `http.port`, `http.path`; `req.send(data, code, headers)`; `fetch()` | `http.url` + `parseURL()`; `req.send(res)`; `req.send()` (§19) | #162 |
| `exec(args, callback)` | blocking `exec(args)` only (§16.5) | #221 |
| top-level `on message(s:socket, msg:blob)` for WebSocket | `on socketMessage` (§19.4) | #208 |
| thread `on message(msg:T)`, `NAME.onMessage(cb)`, outbound-type inference | `on message(worker:thread, msg:T)` (§20.4) | #208 |
| `worker == null` to detect main | `worker.main` (§8.17) | #216 |
| every HTTP response closes the connection; no chunked bodies; no fragment reassembly | keep-alive, chunked decoding, reassembly (§19.1) | #167, #168 |
| `http` + graphics in one program rejected | combined loop (§19.7) | #166 |
| `.callback()` for `img`/`aud` "not yet" | all three media types (§12.5) | #172 |
| scalar-only JSON parsing; no `\u` escapes | nested parsing and `\u` decoding (§16.4) | #173, #206 |
| Windows graphics/HTTP opt-in variables; macOS `try` rejection | first-class (§21.4) | #169, #235 |
| rows owned by their array and un-returnable | reference-counted rows (§8.10) | #265 |
| the `\0` string escape | a compile error; `text` cannot hold a NUL (§7.5.2) | #272 |

## Annex D — Non-goals

The following are deliberately absent and must not be implemented
unless this specification is changed: JavaScript truthiness; `var` and
`let`; `===` and `!==`; `require()` and runtime module loading;
dynamic typing; implicit type coercion beyond §8.3 and §8.20; closures
and bound-argument callbacks; labeled `break`/`continue`; `finally`;
variadic, default or named arguments; a struct literal in an argument or
`return` position (§8.9.4 covers declarations and assignments only);
bounds-checked
array indexing; a TLS client certificate or SNI; WebSocket extensions;
manual SQLite initialization or connection management; media formats
beyond PNG, JPEG, WAV and MP3; a raw byte-buffer type; graphics or audio
on `wasm32-wasi`. [#53, #187, #255]

## Annex E — Correspondence with the Original Numbered Specification

The original specification was written as numbered sections, kept
verbatim as entries 1–73 of [decisions.md](decisions.md). This table
maps each to the clause that now carries its content; where a later
entry changed the rule, the current clause governs.

| Original | Subject | Now |
|---|---|---|
| 1 | Project, priorities | §4.1, §21.2 |
| 2 | Core implementation principles | §2.1, §2.4 |
| 3 | Compiler pipeline | §21.1 |
| 4 | Source files | §6.1 |
| 5, 6 | Imports and resolution | §6.2 |
| 7 | Entry file | §6.3 |
| 8 | Program startup | §6.5 |
| 9 | Lexical conventions | §7 |
| 10–13 | Primitive types, categories, resolution, unknown types | §8.1, §8.2, §8.19 |
| 14–16 | `int`, `float`, `bool` | §8.3 |
| 17 | Truthiness | §2.5, §10.4 |
| 18 | Equality | §9.8 |
| 19, 20 | Conditionals, ternary | §10.4, §9.10 |
| 21, 22 | Variables, constants | §10.2 |
| 23, 24 | Functions, arguments | §11.1 |
| 25 | `null` | §8.2 |
| 26 | Arrays | §8.7 |
| 27 | Structs | §8.9 |
| 28–31 | Tables, automatic database, type mapping, synchronization | §8.10, §15.1–§15.3 |
| 32–34 | Queries, parameters, result types | §15.4, §15.5 |
| 35 | Struct/table distinction | §8.1 |
| 36 | `blob` | §8.6 |
| 37, 38 | `img`, `aud` | §8.13, §18 |
| 39 | Graphics | §17 |
| 40 | Events | §11.5 |
| 41, 42 | `log`, `fail` | §16.5, §14.2 |
| 43 | Memory management | §13 |
| 44 | Performance | §21.1 |
| 45 | JavaScript-like features | §2.5 |
| 46 | Built-in SQLite | §15 |
| 47 | Executable generation | §21.1, §21.3 |
| 48 | Compiler errors | §14.1 |
| 49, 50 | Symbol table, type checking | §8.19, §8.20 |
| 51 | Reserved words | §7.4, Annex B |
| 52 | Example program | §4.2 |
| 53 | Non-goals | Annex D |
| 54 | Ambiguity rule | §2.4 |
| 55 | Numeric conversion (superseded) | §8.3, Annex C |
| 56, 57 | `Math`, division by zero | §16.2, §8.3 |
| 58 | Namespaces | §6.7 |
| 59 | Minimal dependencies | §21.3 |
| 60, 61 | `for`, `while` | §10.6, §10.5 |
| 62, 63, 65 | Array literals, length, indexing | §8.7, §9.3 |
| 66 | Postfix `++`/`--` | §9.5 |
| 67, 68 | Regular expressions, match/replace | §8.15, §16.3 |
| 69 | Timers | §12.4 |
| 70 | `DatabaseURL` | §6.4 |
| 71 | Environment variables | §16.2 |
| 72 | Maps | §8.8 |
| 73 | `break`, `continue` | §10.7 |
