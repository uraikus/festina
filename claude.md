# Festina Programming Language Specification

## 1. Overview

Festina is a statically typed, LLVM-compiled programming language designed for speed, simplicity, and desktop application development.

Its syntax and conventions are heavily inspired by JavaScript. Festina should remain JavaScript-like unless a deviation is explicitly specified by this language specification.

Festina prioritizes execution speed over flexibility.

The primary goals are:

* Native binary compilation through LLVM.
* High runtime performance.
* Static typing.
* Predictable behavior.
* Minimal runtime overhead.
* Simple syntax familiar to JavaScript developers.
* Built-in SQLite integration.
* Built-in graphics and multimedia support.

Repository:

https://github.com/uraikus/festina

---

# 2. General Language Philosophy

Festina should follow JavaScript conventions wherever practical.

The language should only deviate from JavaScript when the deviation is part of the defined Festina language or is necessary to improve:

* Performance.
* Type safety.
* Compiler simplicity.
* Memory efficiency.
* Predictability.

When there is a choice between flexibility and performance, prefer performance.

Festina should compile to native machine code through LLVM rather than relying on an interpreter or general-purpose virtual machine.

---

# 3. Variables and Types

Festina uses explicit static type declarations.

Variables are declared using the type followed by the variable name.

Examples:

```festina
int count = 10
text name = 'Festina'
bool enabled = true
float percentage = 98.5
blob data = 'path/to/file'
```

The core primitive types are:

```text
int
float
bool
text
blob
```

All types may contain `null`.

Example:

```festina
int count = null
text name = null
```

Festina should avoid implicit type coercion.

---

# 4. Integers and Floating-Point Values

`int` represents an integer value.

`float` represents a floating-point value.

The compiler should use native LLVM integer and floating-point representations rather than storing primitive values in SQLite.

Primitive values should be kept in native memory whenever possible.

---

# 5. Booleans

Festina provides:

```text
true
false
```

Boolean values are semantically distinct from integers even though the runtime representation may use integer values.

The compiler may represent:

```text
true  = 1
false = 0
```

internally.

Boolean expressions should not use JavaScript-style truthiness.

For example, an integer or string cannot automatically be interpreted as a boolean.

Valid:

```festina
bool enabled = true

if enabled {
    log('Enabled')
}
```

The language should not allow:

```festina
text name = 'Patrick'

if name {
}
```

---

# 6. Equality

Festina uses:

```text
==
!=
```

for equality comparisons.

Festina does not support JavaScript's strict equality operators.

Do not use:

```text
===
!==
```

Example:

```festina
if age == 18 {
    log('Adult')
}
```

---

# 7. Conditional Statements

Parentheses around `if` conditions are optional.

Both forms are valid:

```festina
if test {
    log('success')
}
```

```festina
if (test) {
    log('success')
}
```

Conditions must evaluate to `bool`.

Festina does not use JavaScript truthy or falsy semantics.

---

# 8. Ternary Operator

Festina supports the JavaScript-style ternary operator:

```festina
text result = score > 50 ? 'pass' : 'fail'
```

---

# 9. Strings

Festina uses JavaScript-like string literals.

Supported string forms include:

```festina
text name = 'Festina'
text message = "Hello"
```

String interpolation uses JavaScript-style template literals:

```festina
text name = 'Patrick'

log(`Hello ${name}`)
```

---

# 10. Semicolons

Semicolons are optional.

The preferred style is semicolon-free:

```festina
text name = 'Festina'
log(name)
```

---

# 11. Functions

Functions use the following syntax:

```festina
[return type] func functionName(arguments) {
}
```

Example:

```festina
text func returnHello() {
    text value = 'hello'
    return value
}
```

Function arguments specify the variable name followed by a colon and its type:

```festina
text func logStr(str:text) {
    log(str)
    return str
}
```

Functions that do not return a value use `void`:

```festina
void func updateUser() {
    log('updated')
}
```

---

# 12. Constants

Immutable values are declared using `const`.

Example:

```festina
const text appName = 'Festina'
const int maxUsers = 100
```

The compiler should use the immutability of constants to perform optimizations where possible.

---

# 13. Arrays

Arrays are the primary collection type in Festina.

Arrays are declared using:

```text
arr[data type]
```

Examples:

```festina
arr[text] names
arr[int] scores
arr[User] users
```

Example initialization:

```festina
arr[text] names = ['Patrick', 'John', 'Mary']
```

Arrays are the primary non-SQLite collection type in the language.

---

# 14. Structs

Structs represent typed in-memory objects.

Structs may only be declared in the global scope.

Example:

```festina
struct User {
    id:int
    name:text
    active:bool
}
```

Instances behave similarly to JavaScript objects, with statically typed properties:

```festina
User user

user.id = 1
user.name = 'Patrick'
user.active = true
```

Structs should be represented using native memory rather than SQLite.

---

# 15. Database Tables

SQLite is a first-class part of Festina.

Database-backed models are declared using `table`.

Example:

```festina
table events {
    id:int
    key:text
    user_id:int
}
```

Tables represent persistent SQLite data and are distinct from normal in-memory structs.

This distinction allows structs to be used for high-performance in-memory data while tables provide automatic SQLite integration.

---

# 16. SQLite

SQLite is globally available.

No `import` or `require` statement is necessary to access SQLite.

The underlying implementation should use `better-sqlite3` where appropriate.

Example:

```festina
table events {
    id:int
    key:text
    user_id:int
}

arr[events] rows = sqlite('select * from events')
```

SQLite queries should automatically map returned rows into the appropriate Festina types.

Example:

```festina
sqlite(
    'insert into events(key, user_id) values(?, ?)',
    ['event', 5]
)
```

SQLite should be abstracted from normal language operations while remaining directly accessible through the global `sqlite()` interface.

---

# 17. Memory Management

Festina uses automatic memory management while prioritizing native performance.

Programmers do not directly manage pointers.

The compiler and runtime should prefer:

1. Stack allocation.
2. Compile-time lifetime analysis.
3. Native heap allocation where necessary.
4. Automatic cleanup when values leave their applicable scope.

The compiler should avoid unnecessary allocations.

The implementation should not use SQLite as the universal memory-management system for primitive values.

For example:

```festina
int example = 166
```

should be represented as a native integer rather than as a row in an SQLite pointer table.

The compiler should aggressively optimize temporary values, stack allocations, constants, and short-lived objects.

---

# 18. Error Handling

Festina uses `fail()` rather than `throw`.

Example:

```festina
if test != true {
    fail('Test failed')
}
```

`fail()` terminates the current execution unless a future explicit error-handling mechanism is introduced.

---

# 19. Logging

Festina uses:

```festina
log()
```

instead of JavaScript's:

```text
console.log()
```

Example:

```festina
log('Hello World')
```

---

# 20. Binary Files

`blob` represents arbitrary binary data.

Example:

```festina
blob explosion = 'path/to/file'
```

The runtime may load the referenced file into memory as necessary.

---

# 21. Images

`img` represents image resources.

Example:

```festina
img profile = loadImage('path/to/profile.png')
```

Images may be passed directly to graphics functions:

```festina
drawImage(profile, 0, 0)
```

---

# 22. Audio

`aud` represents audio resources.

Example:

```festina
aud music = loadAudio('path/to/music.mp3')
```

Audio resources provide:

```festina
music.play()
music.stop()

bool playing = music.isPlaying()
```

The runtime determines which audio formats are supported.

---

# 23. Graphics

Festina provides global graphics functions backed by Cairo.

No graphics import is required.

Example:

```festina
drawRect(0, 0, 100, 100)
```

Additional graphics functions may include:

```festina
drawCircle(50, 50, 25)
drawText('Hello', 20, 20)
drawImage(profile, 0, 0)
```

Graphics functionality should be exposed primarily through global functions rather than requiring an explicit GUI object.

---

# 24. Event Listeners

Event listeners are declared directly in Festina source files using `on`.

Mouse movement:

```festina
on mouse(x:int, y:int) {
    log(`Mouse moved over canvas on x: ${x}, y: ${y}`)
}
```

Mouse clicks:

```festina
on click(x:int, y:int) {
    log(`Mouse clicked on canvas at ${x}, ${y}`)
}
```

The runtime automatically registers these handlers with the application event system.

---

# 25. Enumerations

Festina supports enumerations.

Example:

```festina
enum Direction {
    Up,
    Down,
    Left,
    Right
}
```

Usage:

```festina
Direction direction = Direction.Up
```

Enums should have an efficient native representation.

---

# 26. Modules and Imports

Festina uses a compile-time file inclusion system.

The syntax is:

```festina
import file.f
```

No module namespace is created.

An import effectively includes the referenced file as part of the current compilation unit.

For example:

```festina
import utils.f
```

causes the compiler to read and incorporate `utils.f` before compiling the program.

---

## Recursive Imports

Imported files may themselves import other files.

Example:

```festina
// main.f
import ui.f
```

```festina
// ui.f
import graphics.f
```

The compiler recursively resolves imports before compilation.

Conceptually:

```text
main.f
 └── ui.f
      └── graphics.f
```

becomes a single compilation unit containing the contents of:

```text
graphics.f
ui.f
main.f
```

in dependency order.

---

## Duplicate Imports

The compiler must never import the same file more than once.

Example:

```text
main.f
 ├── import utils.f
 └── import player.f
                  └── import utils.f
```

`utils.f` must only appear once in the resulting compilation unit.

The compiler should maintain an import registry containing the canonical path of every file already processed.

This registry must also prevent circular imports from recursively expanding forever.

For example:

```text
a.f -> b.f -> a.f
```

must not cause `a.f` to be processed twice.

---

# 27. Compilation Order

Compilation should occur in the following conceptual stages.

## Stage 1: Resolve Entry File

The compiler identifies the file supplied as the program's entry file.

For example:

```text
main.f
```

---

## Stage 2: Resolve Imports

The compiler recursively processes every `import` statement.

Each file is processed only once.

Dependencies must be processed before the files that depend upon them.

---

## Stage 3: Construct Unified Source

All imported files are combined into one logical compilation unit.

Conceptually:

```text
[dependency files]

[entry file]
```

The entry file must be processed last.

---

## Stage 4: Parse

The unified source is parsed into the Festina AST.

---

## Stage 5: Generate Program Entry

Festina source files do not require programmers to explicitly declare `main()`.

The compiler automatically generates an internal entry function for the entry file.

For example:

```festina
text appName = 'Festina'

log(appName)
```

is conceptually transformed into:

```festina
void func __festina_main() {
    text appName = 'Festina'

    log(appName)
}
```

The generated function becomes the program's execution entry point.

---

# 28. Program Startup

Imported files must be completely processed before the entry file begins execution.

The conceptual startup sequence is:

```text
Entry file discovered
        ↓
Imports recursively resolved
        ↓
Duplicate imports removed
        ↓
Dependencies ordered
        ↓
All source files combined
        ↓
AST generated
        ↓
Entry function generated
        ↓
LLVM IR generated
        ↓
Native executable produced
        ↓
Program starts
        ↓
Generated entry function executes
```

Imported declarations, functions, structs, tables, enums, and other compile-time constructs must therefore be available before the generated entry function begins execution.

Top-level executable code in the entry file is placed into the generated entry function.

---

# 29. Top-Level Declarations

The following may exist at file scope:

```text
import
struct
table
enum
const
func
```

Top-level executable statements in the entry file are automatically placed into the compiler-generated entry function.

Example:

```festina
const text appName = 'Festina'

log(appName)
```

Conceptually becomes:

```festina
const text appName = 'Festina'

void func __festina_main() {
    log(appName)
}
```

Imported files should primarily contain declarations, definitions, and reusable functionality.

---

# 30. Compiler Architecture

Festina should compile through LLVM.

The compiler should generally follow this pipeline:

```text
Festina Source
      ↓
Import Resolver
      ↓
Unified Compilation Unit
      ↓
Lexer
      ↓
Parser
      ↓
AST
      ↓
Semantic Analysis
      ↓
Type Checking
      ↓
Optimization
      ↓
LLVM IR
      ↓
LLVM Optimization
      ↓
Native Machine Code
      ↓
Executable
```

---

# 31. Performance Requirements

Performance is a primary design goal.

The compiler should:

* Prefer native representations.
* Prefer stack allocation.
* Minimize heap allocation.
* Avoid unnecessary runtime abstractions.
* Perform aggressive inlining.
* Eliminate dead code.
* Propagate constants.
* Optimize temporary values.
* Avoid runtime reflection.
* Avoid dynamic typing.
* Avoid implicit type coercion.
* Use LLVM optimization capabilities extensively.

Language features should not be added merely for flexibility if they introduce significant runtime overhead.

Festina should provide JavaScript-like syntax with native compiled performance.

---

# 32. Standard Library Philosophy

The standard library should favor simple, direct APIs.

Functionality that is fundamental to Festina's intended application environment should be globally accessible when practical.

Examples include:

```text
log()
sqlite()
drawRect()
drawCircle()
drawText()
drawImage()
```

The goal is to avoid unnecessary boilerplate for common operations.

---

# 33. JavaScript Compatibility Philosophy

Festina is JavaScript-like, not JavaScript-compatible.

Developers should be able to recognize familiar constructs such as:

```text
Objects
Arrays
Date
String interpolation
Ternary expressions
Property access
```

while Festina maintains:

* Static typing.
* Native compilation.
* Predictable memory behavior.
* Explicit data types.
* No truthy/falsy coercion.
* No dynamic runtime type system.

When a JavaScript feature conflicts with Festina's performance or type-safety goals, Festina should favor its own statically typed semantics.
