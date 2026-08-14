FESTINA — AI AGENT IMPLEMENTATION SPECIFICATION

1. PROJECT

Festina is a statically typed programming language designed for high-performance native applications.

Repository:
https://github.com/uraikus/festina

The compiler executable must be named:

festina

Festina compiles source code through LLVM into native executables.

The language is JavaScript-inspired but is not JavaScript-compatible.

When a Festina rule differs from JavaScript, the Festina rule always takes precedence.

Primary design priority:

Performance > Flexibility

Secondary priorities:

- Simplicity
- Predictability
- Static typing
- Low runtime overhead
- Familiar syntax
- Native performance


2. CORE IMPLEMENTATION PRINCIPLES

An AI agent implementing Festina must follow these principles:

1. Do not invent language behavior that is not specified.
2. Prefer compile-time validation over runtime validation.
3. Prefer native LLVM representations over unnecessary runtime abstractions.
4. Do not introduce JavaScript-style implicit coercion.
5. Do not introduce JavaScript truthy/falsy behavior.
6. Resolve all types during semantic analysis.
7. Resolve all imports before compilation.
8. Never import the same file more than once.
9. Generate the program entry point automatically.
10. SQLite requires no explicit initialization by the programmer.
11. All default SQLite operations use festina.sqlite.
12. The Festina source declaration is authoritative for SQLite table schemas.
13. Preserve the distinction between compile-time declarations and runtime execution.
14. Prefer simple implementations with low runtime overhead.


3. COMPILER PIPELINE

The compiler should follow this conceptual pipeline:

Source Files
    ↓
Import Resolution
    ↓
Unified Compilation Unit
    ↓
Lexing
    ↓
Parsing
    ↓
AST
    ↓
Name Resolution
    ↓
Type Resolution
    ↓
Semantic Analysis
    ↓
Table Metadata Generation
    ↓
Entry Function Generation
    ↓
LLVM IR Generation
    ↓
LLVM Optimization
    ↓
Native Linking
    ↓
Executable

LLVM IR generation must occur only after name resolution, type resolution, and semantic analysis have completed.


4. SOURCE FILES

Festina source files use the .f extension.

Examples:

main.f
database.f
ui.f


5. IMPORTS

Imports use:

import file.f

No import { ... } from ... syntax is used.

No require() syntax is used.

Imports are compile-time operations.

An import includes the specified file and all of its dependencies in the current compilation unit.

Example:

import database.f
import ui.f

Imported files do not become runtime modules.


6. IMPORT RESOLUTION

Import resolution must be recursive.

Given:

main.f
 ├── ui.f
 │    └── graphics.f
 └── database.f

the compiler must resolve the dependency order before compilation.

Each source file must be processed only once.

The compiler should use canonical paths when determining whether a file has already been imported.

For example:

./utils.f
src/../utils.f

must be treated as the same file when they resolve to the same canonical path.

Circular imports must be detected and must not cause infinite recursion.

Example:

a.f → b.f → a.f

The compiler must report the circular dependency or otherwise handle it deterministically without repeatedly processing either file.


7. ENTRY FILE

The file passed directly to the compiler is the entry file.

Example:

festina main.f

main.f is the entry file.

Imported files are processed before the entry file.

The programmer does not need to define main().

Executable statements in the entry file are automatically placed into a generated program entry function.

For example:

log('Hello')

is conceptually transformed into:

void func __festina_main() {
    log('Hello')
}

The exact internal name is implementation-defined.

The generated entry function is the runtime entry point of the application.


8. PROGRAM STARTUP

Program initialization must follow this order:

1. Resolve the entry file.
2. Recursively resolve all imports.
3. Remove duplicate imports.
4. Detect circular imports.
5. Establish dependency order.
6. Parse all source files.
7. Build symbol tables.
8. Resolve all names.
9. Resolve all types.
10. Perform semantic validation.
11. Collect table declarations.
12. Generate SQLite schema synchronization.
13. Generate the entry function.
14. Generate LLVM IR.
15. Optimize LLVM IR.
16. Link the native executable.
17. Start the application.
18. Open or create festina.sqlite.
19. Synchronize all declared table schemas.
20. Execute the generated entry function.

SQLite schema synchronization must occur before application code attempts to access the declared tables.


9. LEXICAL CONVENTIONS

Festina generally follows JavaScript conventions for literals and operators.

Supported string literals:

'hello'
"hello"

Template strings support interpolation:

`Hello ${name}`

Semicolons are optional.

Preferred style:

text name = 'Festina'
log(name)


10. PRIMITIVE TYPES

Festina provides the following primitive types:

int
float
bool
text
blob

null is a valid value for every type.

Examples:

int value = null
text name = null
bool enabled = null

The compiler must preserve the underlying type when a value is null.


11. TYPE CATEGORIES

The compiler must distinguish between the following type categories.

Primitive types:

int
float
bool
text
blob

Native types:

struct
arr[T]
img
aud

Database types:

table

Each type must have an explicit internal representation.

For example:

PrimitiveType(INT)
StructType(User)
TableType(People)
ArrayType(PrimitiveType(INT))
ArrayType(StructType(User))
ArrayType(TableType(People))


12. TYPE RESOLUTION

When the compiler encounters:

arr[T]

it must resolve T through the compiler's symbol and type tables.

Example:

arr[int] values

resolves to:

ArrayType(
    PrimitiveType(INT)
)

Example:

struct User {
    name:text
}

arr[User] users

resolves to:

ArrayType(
    StructType(User)
)

Example:

table People {
    name:text
}

arr[People] people

resolves to:

ArrayType(
    TableType(People)
)

The compiler must resolve the identifier according to the declarations in scope.

The compiler must not infer the type category from naming conventions.


13. UNKNOWN TYPES

An unknown type is a compile-time error.

Example:

arr[Person] people

when Person has not been declared should produce an error similar to:

error: unknown type 'Person'


14. INTEGER

int is a native integer type.

Example:

int count = 100

The compiler should use an appropriate LLVM integer representation.


15. FLOATING-POINT VALUES

float is a native floating-point type.

Example:

float percentage = 98.5


16. BOOLEANS

bool represents boolean values.

Valid values:

true
false
null

The runtime representation of bool may use:

true  → 1
false → 0

The semantic type remains bool.

Only boolean expressions may be used where a boolean condition is required.


17. TRUTHINESS

Festina does not use JavaScript truthy/falsy semantics.

The following values must not automatically become boolean conditions:

0
1
-1
''
'hello'
null
arrays
structs
tables

A conditional expression must have type bool.


18. EQUALITY

Supported equality operators:

==
!=

Unsupported operators:

===
!==

Example:

if value == 10 {
    log('Ten')
}

The compiler must report a compile-time error when === or !== is used.


19. CONDITIONALS

Parentheses around conditions are optional.

Both forms are valid:

if test {
    log('yes')
}

if (test) {
    log('yes')
}

The condition must resolve to bool.


20. TERNARY OPERATOR

Festina supports the JavaScript-style ternary operator:

text result = test ? 'yes' : 'no'

The condition must have type bool.


21. VARIABLES

Variables are declared using:

type name = value

Examples:

int count = 10
text name = 'Festina'
bool enabled = true

Festina does not use var or let.


22. CONSTANTS

Constants use:

const type name = value

Example:

const text name = 'Festina'

Constants should be available for compiler optimization.


23. FUNCTIONS

Function syntax is:

return_type func name(arguments) {
}

Example:

text func returnHello() {
    text value = 'hello'
    return value
}

A function that does not return a value uses void:

void func sayHello() {
    log('Hello')
}


24. FUNCTION ARGUMENTS

Function arguments use:

name:type

Example:

text func logStr(str:text) {
    log(str)
    return str
}

Multiple arguments:

int func add(a:int, b:int) {
    return a + b
}


25. NULL

null represents the absence of a value.

Every type may contain null.

Examples:

text name = null
int id = null
User user = null

The compiler must preserve the underlying type.

null is not a boolean and must not participate in implicit boolean conversion.


26. ARRAYS

Arrays use:

arr[T]

Examples:

arr[int] numbers
arr[text] names
arr[User] users
arr[People] people

Arrays may contain supported primitive types, structs, tables, and other array types.

The array element type must be resolved at compile time.

Nested arrays are valid:

arr[arr[int]] matrix


27. STRUCTS

Structs are native in-memory objects.

Structs may only be declared in global scope.

Example:

struct User {
    id:int
    name:text
    active:bool
}

A struct instance behaves similarly to a JavaScript object while remaining statically typed.

Example:

User user

user.id = 1
user.name = 'Patrick'
user.active = true

Struct fields are statically typed.

Structs are not database tables.

Declaring a struct does not create a SQLite table.


28. TABLES

Tables are SQLite-backed persistent data models.

Example:

table People {
    id:int
    name:text
    age:int
}

A table declaration automatically ensures that the corresponding SQLite table exists and that its schema matches the Festina declaration.

The Festina table declaration is authoritative.

When the application loads, Festina must compare each declared table against the corresponding table in festina.sqlite.

If the table does not exist, it must be created.

If the table exists but differs from the Festina declaration, the SQLite schema must be synchronized with the Festina declaration.

Schema synchronization must support:

- Adding columns that exist in the Festina declaration but not in SQLite.
- Removing columns that exist in SQLite but not in the Festina declaration.
- Updating column definitions when the declared Festina type differs from the SQLite type.
- Creating tables that do not exist.
- Preserving existing data whenever possible.

When SQLite cannot perform a requested schema change directly, Festina may use a temporary table and data migration.

The programmer does not need to write schema migration code.


29. AUTOMATIC SQLITE DATABASE

Every Festina application automatically uses:

festina.sqlite

The database is opened or created automatically.

The programmer does not need to:

- Create the database.
- Open a database connection.
- Initialize SQLite.
- Configure a database path.

All normal Festina SQLite operations use festina.sqlite.


30. SQLITE TYPE MAPPING

The initial type mapping is:

Festina     SQLite

int      →  INTEGER
float    →  REAL
bool     →  INTEGER
text     →  TEXT
blob     →  BLOB

bool values are stored as 0 or 1.


31. AUTOMATIC TABLE CREATION AND SCHEMA SYNCHRONIZATION

Given:

table People {
    id:int
    name:text
}

the application must ensure that festina.sqlite contains a matching People table before application code accesses it.

If People does not exist, create it using the equivalent of:

CREATE TABLE IF NOT EXISTS People (
    id INTEGER,
    name TEXT
);

If People already exists, compare its schema against the Festina declaration.

The Festina declaration is authoritative.

The synchronization process must:

1. Create missing tables.
2. Add missing columns.
3. Remove undeclared columns.
4. Update incompatible column definitions.
5. Preserve existing data whenever possible.
6. Use temporary tables and data migration when SQLite cannot perform the required alteration directly.

Schema synchronization occurs during application initialization before the generated entry function executes.

The programmer does not need to write CREATE TABLE, ALTER TABLE, migration, or database initialization code.

Example:

table People {
    id:int
    name:text
}

If the existing SQLite schema is:

People (
    id INTEGER,
    name TEXT,
    obsolete TEXT
)

the application must modify the database so that it becomes equivalent to:

People (
    id INTEGER,
    name TEXT
)

If the Festina declaration later becomes:

table People {
    id:int
    full_name:text
}

the application must synchronize the database accordingly:

People (
    id INTEGER,
    full_name TEXT
)

Schema synchronization must happen automatically each time the application loads.


32. SQLITE QUERIES

SQLite is accessed through the global sqlite() function.

Example:

arr[People] people = sqlite('SELECT * FROM People')

No import is required.

No database initialization is required.

All SQLite queries use:

festina.sqlite


33. PARAMETERIZED SQLITE QUERIES

Parameterized queries must be supported.

Example:

sqlite(
    'INSERT INTO People (id, name) VALUES (?, ?)',
    [1, 'Patrick']
)

Query parameters are passed as an array.


34. QUERY RESULT TYPES

A query against a declared table may produce an array of that table type.

Example:

table People {
    id:int
    name:text
}

arr[People] people = sqlite('SELECT * FROM People')

The compiler resolves People as:

TableType(People)

and the array as:

ArrayType(TableType(People))

The table declaration defines the expected fields and their types.


35. STRUCT/TABLE DISTINCTION

struct and table are distinct language constructs.

A struct:

struct User {
    name:text
}

represents a native in-memory type.

A table:

table Users {
    name:text
}

represents persistent SQLite data.

They must remain distinct in the compiler's type system.


36. BLOB

blob represents binary data.

Example:

blob data = 'path/to/file'


37. IMAGE

The image type is:

img

Example:

img profile = loadImage('path/to/profile.png')

Images may be passed to graphics functions:

drawImage(profile, 0, 0)

Supported image formats are determined by the runtime.


38. AUDIO

The audio type is:

aud

Example:

aud music = loadAudio('path/to/music.mp3')

Supported methods:

music.play()
music.stop()
music.isPlaying()

isPlaying() returns bool.


39. GRAPHICS

Graphics operations are exposed as global functions.

Example:

drawRect(0, 0, 100, 100)

Other graphics functions may include:

drawCircle(50, 50, 25)
drawText('Hello', 20, 20)
drawImage(profile, 0, 0)

Graphics are backed by Cairo.

No GUI import is required.

The current size of the canvas window is available as:

clientWidth
clientHeight

Both are read-only global int values.


40. EVENTS

Event listeners use:

on eventName(arguments) {
}

Example:

on mouse(x:int, y:int) {
    log(`Mouse moved over canvas on x: ${x}, y: ${y}`)
}

Example:

on click(x:int, y:int) {
    log(`Mouse clicked on canvas at ${x}, ${y}`)
}

Example:

on key(key:text) {
    log(`Key pressed: ${key}`)
}

Example:

on resize() {
    log(`Canvas resized to ${clientWidth}x${clientHeight}`)
}

Example:

on close() {
    log('Canvas window closing')
}

The runtime automatically registers declared event handlers.


41. LOGGING

Use:

log('Hello')

log() is a built-in global function.

It replaces the need for console.log().


42. FAILURE

Use:

fail()

instead of throw.

Example:

if test != true {
    fail('Test failed')
}

The initial implementation should treat fail() as a runtime failure mechanism.


43. MEMORY MANAGEMENT

Festina uses automatic memory management.

The programmer does not manually allocate or free memory.

The compiler should prefer stack allocation when the value's lifetime permits it.

The compiler should use native representations for primitive values.

Heap allocation should only be used when required by the value's lifetime, size, or semantics.

The compiler should automatically release or reclaim memory when values are no longer reachable according to the runtime's memory-management strategy.


44. PERFORMANCE

Performance is a primary language requirement.

Prefer compile-time work over runtime work where practical.

The compiler should use LLVM optimization capabilities, including where applicable:

- Constant folding.
- Dead-code elimination.
- Function inlining.
- Constant propagation.
- Allocation optimization.
- Unused-code elimination.

Avoid unnecessary:

- Runtime reflection.
- Dynamic type checking.
- Boxing.
- Heap allocation.
- Dynamic dispatch.
- Runtime conversions.


45. JAVASCRIPT-LIKE FEATURES

Festina should retain familiar JavaScript conventions where they do not conflict with the static type system or performance goals.

Supported or intended features include:

- String interpolation.
- Ternary operator.
- Objects through structs.
- Arrays.
- Date.
- Property access.

Festina does not inherit JavaScript's dynamic runtime semantics.


46. BUILT-IN SQLITE INTEGRATION

SQLite is a built-in feature of Festina.

The programmer should be able to write:

table People {
    name:text
}

arr[People] people = sqlite('SELECT * FROM People')

without importing a SQLite library or initializing a database.

The runtime automatically manages:

festina.sqlite

and automatically creates and synchronizes declared tables when necessary.


47. EXECUTABLE GENERATION

The compiler must produce native executables.

Example:

festina main.f

The resulting executable must not require Festina source files to execute.

The intended architecture is:

Festina source
      ↓
LLVM IR
      ↓
Machine code
      ↓
Native executable


48. COMPILER ERRORS

Errors should be reported at the earliest reasonable stage.

Compile-time errors include:

- Unknown type.
- Unknown variable.
- Unknown function.
- Unknown struct.
- Unknown table.
- Invalid function argument type.
- Invalid return type.
- Invalid condition type.
- Duplicate declaration.
- Invalid import.
- Circular import.
- Unsupported operator.
- Invalid field access.

Error messages should include:

- File.
- Line.
- Column.
- Error category.
- Human-readable explanation.

Example:

main.f:12:5: error: condition must be bool, found text


49. SYMBOL TABLE

The compiler should maintain explicit symbol information.

Symbols should distinguish at minimum:

- Variable.
- Constant.
- Function.
- Struct.
- Table.
- Enum.

Types should distinguish at minimum:

- PrimitiveType.
- StructType.
- TableType.
- ArrayType.
- ImageType.
- AudioType.

The compiler should resolve symbols during semantic analysis rather than relying on naming conventions.


50. TYPE CHECKING

Type checking must happen before LLVM generation.

Valid:

int x = 10

Invalid:

int x = 'hello'

Valid:

bool enabled = true

if enabled {
}

Invalid:

int value = 1

if value {
}

Valid:

arr[int] values

Valid:

arr[User] users

Valid:

arr[People] people

provided People is declared as a table.


51. RESERVED LANGUAGE FEATURES

The following names have defined language meanings:

int
float
bool
text
blob
arr
struct
table
img
aud
null
true
false
void
func
const
import
if
else
on
fail
log
sqlite


52. EXAMPLE PROGRAM

A valid Festina application may look like:

import database.f
import ui.f

table People {
    id:int
    name:text
}

struct User {
    id:int
    name:text
}

const text appName = 'Festina'

text func greet(user:User) {
    text message = `Hello ${user.name}`
    log(message)
    return message
}

on click(x:int, y:int) {
    log(`Clicked at ${x}, ${y}`)
}

drawRect(0, 0, 100, 100)

User user

user.id = 1
user.name = 'Patrick'

greet(user)

arr[People] people = sqlite('SELECT * FROM People')

log(appName)

The application startup behavior is:

Resolve imports
    ↓
Build complete compilation unit
    ↓
Resolve symbols
    ↓
Resolve types
    ↓
Collect table declarations
    ↓
Generate SQLite schema synchronization
    ↓
Generate entry function
    ↓
Compile through LLVM
    ↓
Create/open festina.sqlite
    ↓
Synchronize declared tables
    ↓
Execute application


53. NON-GOALS

Unless explicitly added to the language specification, do not implement:

- JavaScript truthiness.
- var.
- let.
- ===.
- !==.
- throw.
- require().
- Runtime module loading.
- Dynamic typing.
- Implicit type coercion.
- Manual SQLite initialization.
- Manual SQLite connection management.


54. IMPLEMENTATION RULE FOR AMBIGUITY

When implementing behavior that is not explicitly specified:

1. Prefer the simplest implementation.
2. Prefer compile-time behavior.
3. Prefer native representations.
4. Prefer JavaScript-like syntax.
5. Prefer static typing.
6. Prefer performance.
7. Do not introduce new syntax without necessity.
8. Do not silently change existing semantics.
9. If multiple implementations satisfy the specification, choose the implementation with the lowest runtime overhead.
10. If behavior genuinely cannot be determined from this specification, treat it as an unresolved language-design decision rather than inventing behavior.

The compiler implementation must follow this specification rather than assuming behavior from JavaScript, TypeScript, SQLite, or another language when Festina has explicitly defined its own behavior.


55. NUMERIC CONVERSION

int and float do not mix directly.

Example (invalid):

int a = 5
float b = 2.5
float c = a + b

The compiler must reject this at compile time.

This applies to every binary operator, not only arithmetic: comparison and equality operators (<, >, <=, >=, ==, !=) also require both operands to be the same numeric type. Comparing an int directly to a float is a compile-time error.

To combine an int and a float, convert one of them explicitly first.

Every int value has a method that returns the equivalent float:

int a = 5
float b = 2.5
float c = a.toFloat() + b

Converting a float to an int requires a rounding decision, so it is done through the Math functions in section 56 rather than a single method:

float price = 19.99
int total = Math.ceil(price) + 3

This section overrides any implicit numeric promotion that might otherwise be assumed from JavaScript familiarity (section 45): Festina does not perform implicit conversion between int and float.

int, float, and bool values also each have a toText() method, returning the same text representation string interpolation (section 45) already produces for that value:

int count = 5
text s = count.toText()

log(`${count}`) and log(count.toText()) therefore always produce identical output for an int/float/bool value -- toText() exists for when that text value is needed outside of a template, e.g. to pass elsewhere or build up separately.


56. MATH

A global Math namespace provides explicit float-to-int conversion, mirroring JavaScript's Math object:

Math.floor(x:float) -> int
Math.ceil(x:float) -> int
Math.round(x:float) -> int
Math.trunc(x:float) -> int

Example:

float price = 19.99
int rounded = Math.ceil(price)

Other Math functions (for example Math.abs, Math.sqrt, Math.pow, Math.min, Math.max) are not yet specified.


57. DIVISION AND MODULO BY ZERO

Division (/) or modulo (%) by zero does not raise a runtime error and does not crash the program.

Instead, the result is null.

Example:

int a = 10
int b = 0
int result = a / b

result is null.

This applies to both int and float operands.

Since null must be representable for every type that can be divided (section 10), the compiler must choose a runtime representation capable of distinguishing null from every valid value of that type -- for example, a reserved sentinel value, or a NaN-based encoding for float. The exact representation is implementation-defined.

Using a null int or null float as an operand in further arithmetic is not specified by this section and is treated as unresolved per section 54.


58. STRUCT/TABLE NAMESPACE

struct and table names occupy a namespace separate from variables, constants, and functions.

Example:

struct User {
    name:text
}

int User = 5

is valid: the struct type User and the variable User do not conflict, because struct/table names and variable/function/constant names are resolved independently. This mirrors the same separation found in many statically typed languages (for example, C's separate tag namespace for struct/enum/union names).

No duplicate-declaration error is required across these two namespaces -- only within each one.


59. MINIMAL DEPENDENCIES AND SETUP

Both using the Festina compiler and running a compiled Festina program should require as few external dependencies as practical.

When more than one implementation would satisfy a requirement, prefer the one that:

1. Requires fewer separately-installed tools to compile a Festina program.
2. Requires fewer runtime dependencies for the resulting executable.
3. Works with more than one common toolchain (for example, more than one C compiler) rather than depending on a single specific one, when a broader-compatible alternative is available at similar implementation cost.
4. Fails with a clear, actionable error identifying the missing dependency and how to obtain it, rather than a raw or unclear error, when a required dependency is genuinely missing.

This does not require eliminating every external dependency at any cost. Section 54's ambiguity rules still apply: weigh this against simplicity and performance rather than treating it as an override. It does mean an implementation should not introduce an unnecessary external dependency, or depend on a specific tool where a more common or already-required one would do.


60. FOR LOOPS

Festina supports C-style counted loops.

Syntax:

for initialization, condition, update {
}

Example:

for int x = 0, x < 10, x++ {
    log(x)
}

Array iteration example:

for int x = 0, x < array.length, x++ {
    log(array[x])
}

Execution order:

1. Execute initialization once.
2. Evaluate condition.
3. If condition is false, exit loop.
4. Execute loop body.
5. Execute update expression.
6. Repeat from step 2.

The condition must resolve to bool.

The initialization variable is scoped to the loop body.

Valid update expressions include:

i++
i--

61. WHILE LOOPS

Festina supports while loops.

Syntax:

while condition {
}

Examples:

int e = 0

while e < 10 {
    log(e)
    e++
}

Infinite loop:

while true {
    log('running')
}

The condition must resolve to bool.

Festina does not perform truthy/falsy conversion when evaluating loop conditions.

62. ARRAY LITERALS

Arrays may be initialized using array literals.

Syntax:

arr[T] values = [ ... ]

Examples:

arr[int] numbers = [1, 2, 3]

arr[text] names = [
    'Patrick',
    'Sarah',
    'John'
]

The compiler must verify that every element is compatible with the declared array element type.

63. ARRAY LENGTH

Every array provides a built-in read-only property:

.length

Example:

arr[int] values = [1, 2, 3]

log(values.length)

The type of .length is int.

65. ARRAY INDEXING

Arrays support zero-based indexing.

Example:

arr[int] values = [1, 2, 3]

log(values[0])
log(values[1])
log(values[2])

The index expression must resolve to int.

66. POSTFIX INCREMENT AND DECREMENT

Festina supports postfix increment and decrement operators.

Supported operators:

++
--

These operators are valid only on mutable integer variables.

The operand must be int.

Using increment or decrement on any other type is a compile-time error.


67. REGULAR EXPRESSIONS

The regex type is:

regex

A regex value is created with a JavaScript-style literal:

/pattern/flags

Example:

regex pattern = /^[a-z]+$/

flags is optional and, if present, immediately follows the closing `/` with no space. The supported flags are:

i -- case-insensitive matching
g -- accepted for familiarity with JavaScript, but has no additional effect: replace()/replaceAll() below already say "first match" vs. "every match" explicitly, the same distinction JavaScript's g flag controls implicitly elsewhere.

Example:

regex pattern = /^[a-z]+$/i

Any flag letter other than i or g is a compile-time error.

A pattern/flags known only at runtime (built from a variable, a template, ...) cannot use the literal syntax -- for that case, the global regex() function is still available, taking the same two arguments as text:

regex pattern = regex(userSuppliedPattern)
regex pattern = regex(userSuppliedPattern, userSuppliedFlags)

An invalid pattern (either form) is a runtime error (fail()), not a compile-time error -- the compiler does not itself validate regex syntax, for a literal any more than for regex().

A regex value supports:

pattern.test(value:text)

test() returns bool: true if the pattern matches anywhere in value, false otherwise.

Example:

regex digits = /[0-9]+/
log(digits.test('room 42'))


68. STRING MATCH AND REPLACE

Every text value supports:

value.match(pattern:regex)

match() returns the first substring of value that pattern matches, or null if there is no match. The return type is text.

Example:

regex digits = /[0-9]+/
text found = 'room 42'.match(digits)
log(found)

Every text value also supports:

value.replace(search, replacement:text)
value.replaceAll(search, replacement:text)

search may be either text or regex.

replace() replaces the first match with replacement. replaceAll() replaces every match with replacement. Both return a new text value; the original value is unchanged.

Examples:

text a = 'room 42'.replace('room', 'suite')
text b = 'a1b2c3'.replaceAll(/[0-9]/, '-')

If search is text, matching is a literal substring match, not a pattern match.

If there is no match, replace() and replaceAll() return the original value unchanged.


69. TIMERS

Festina provides JavaScript-style timers as global functions:

setTimeout(callback, delayMs)
setInterval(callback, delayMs)

callback must be the name of an already-declared function that takes no parameters and returns nothing.

Example:

void func showMessage() {
    log('Delayed message')
}

setTimeout(showMessage, 1000)

setInterval repeats the callback every delayMs milliseconds until cancelled:

void func tick() {
    log('tick')
}

setInterval(tick, 500)

Both setTimeout() and setInterval() return an int timer id.

Timers are cancelled with:

clearTimeout(id)
clearInterval(id)

A program keeps running as long as it has a pending timeout or an uncleared interval, exactly as in JavaScript -- clearing every interval (or letting every timeout fire) lets it exit normally.


70. DATABASE CONFIGURATION

By default the automatic SQLite database (#8, #29) is always festina.sqlite in the current working directory. The entry file may override this by making its very first line, before any other code and before any import:

DatabaseURL = path

path must be a text expression. Example:

DatabaseURL = 'game_saves.sqlite'
DatabaseURL = environment.DATABASE_URL

If DatabaseURL does not appear as the entry file's first statement, the default (festina.sqlite) is used. DatabaseURL appearing anywhere other than the first statement of the entry file is a compile-time error. DatabaseURL has no effect in an imported file -- it is only recognized in the file actually passed to the compiler.


71. ENVIRONMENT VARIABLES

Environment variables are read through the global environment object:

environment.NAME

This returns the value of the environment variable NAME as text, or null if it is not set. NAME may also be given as a computed (bracket) key, which must be a text expression:

environment['NAME']

Example:

text apiKey = environment.API_KEY
if apiKey == null {
    fail('API_KEY is not set')
}

environment is read-only -- assigning to environment.NAME (or environment['NAME']) is a compile-time error. environment cannot be used by itself (without a .NAME or ['NAME']) -- doing so is a compile-time error.


72. MAPS

The map type associates text keys with values of one declared type:

map[T]

Where T may be any type a map value can hold except another map or an array (a map value is stored in a single fixed-size slot internally, which an array or map value does not fit in).

A map is created with a map literal:

map[T] name = { key: value, key: value, ... }

Every key expression must be text -- a plain string literal, or any other expression that evaluates to text (a variable, a template string, ...). It is not a bareword/identifier-as-string shorthand: an unquoted identifier used as a key is a reference to that variable's text value, not the identifier's own name.

Example:

text npc2Id = 'npc2'
map[int] npcHealths = { 'npc1': 10, npc2Id: 15 }
map[text] npcNames = { 'npc1': 'jim', npc2Id: 'john' }

An empty map literal is written {}.

If the same key appears more than once in a literal, the last value for that key wins.

If both of the colliding keys are plain string literals (e.g. { 'a': 1, 'a': 2 }), the collision is a compile error instead -- it is always knowable ahead of time in that case, and is essentially always a mistake. This only applies when both keys are literal text; a key that is a variable or other expression is not compared at compile time, since its value is not known until the program runs.

A map value is read by indexing with a text key:

npcHealths['npc1']
npcHealths[npc2Id]

If the key is not present in the map, the result is null.

A map value is written the same way:

npcHealths['npc1'] = 30
npcHealths[npc2Id] = 30

Assigning to a key that does not yet exist adds it; assigning to an existing key replaces its value.

Every map supports:

map.forEach(callback)

callback must be the name of an already-declared function taking exactly two parameters -- the value (typed the same as the map's declared value type) and the key (text) -- and returning nothing. It is called once for each entry currently in the map. The order entries are visited in is not specified.

Example:

void func logHealth(h:int, key:text) {
    log(`${key} ${h.toText()}`)
}

npcHealths.forEach(logHealth)


73. BREAK AND CONTINUE

break exits the nearest enclosing for or while loop immediately. No further statements in the loop body run, and the loop's condition is not checked again.

continue skips directly to the next iteration of the nearest enclosing for or while loop. For a for loop, the update expression still runs before the condition is checked again, the same as it would at the end of a normal iteration; for a while loop, continue goes straight to the condition check.

Using break or continue outside of a for or while loop is a compile error.

break and continue only affect the nearest enclosing loop. A loop nested inside another loop's body is unaffected by a break or continue in the outer loop, and vice versa.

Example:

for int i = 0, i < 10, i++ {
    if i == 5 {
        break
    }
    if i % 2 == 0 {
        continue
    }
    log(i)
}

This logs 1, 3.

There is no labeled break or continue targeting an outer loop specifically -- only return from the enclosing function can exit more than one loop at once.


74. AUTOMATIC MEMORY RECLAMATION (STAGE 1: NON-ESCAPING LOCALS)

This section describes the first stage of section 43's automatic memory management promise. It is deliberately incremental -- only some memory is reclaimed automatically by this stage. Memory not covered by this stage behaves exactly as before: heap-allocated and not yet reclaimed. A later stage will extend this coverage; nothing in this section should be read as the complete picture of section 43.

A local variable of struct, arr[T], or map[T] type, declared directly in the body of a function, event handler, if branch, while body, or for body, is automatically freed as soon as control leaves the block it was declared in, if the compiler can prove its value never outlives that block's own enclosing function or handler.

The compiler proves this the same way regardless of which block the variable is declared in: by checking every place that variable's name appears anywhere in the enclosing function or handler body, not just within its own declaring block. If the variable is used only to read or write its own fields or elements (v.field, v.field = x, v[i], v[i] = x, v.someMethod(...)), its value cannot have been stored anywhere else, passed anywhere else, or returned, and it is safe to free automatically. If the variable's name appears anywhere else -- as a return value, as an argument to any function call, as the value or target of a plain assignment, as an element of an array or map literal, or in any other position, anywhere in the function or handler -- the compiler does not attempt to prove anything further about it, and it is not freed automatically. This is a conservative check: it only ever concludes a variable is safe to free when it can prove this from the syntax of the function or handler alone. When it cannot prove this, the variable leaks exactly as it does today. This must never free a variable whose safety was not proven.

"As soon as control leaves the block it was declared in" is deliberately not "when the enclosing function or handler returns": a variable declared inside a for or while loop's body is freed at the end of every iteration that reaches the end of the loop body, not deferred until the loop itself finishes or the function eventually returns. break and continue leaving a loop early free every such variable declared since that loop's body began (including ones declared in an if branch nested inside the loop body) before actually transferring control, the same as reaching the natural end of the loop body would. A variable declared outside a loop and merely used inside it (read or written through its own fields/elements, which is always safe regardless of where the variable itself was declared) is unaffected by that loop's own break/continue -- only variables declared since the loop's own body began are freed by them.

This stage does not yet analyze:

- Whether a value passed as an argument to another function is retained by that function. Any use as a call argument is treated as escaping, unconditionally, even if the called function does not actually retain it.
- Fields within a freed struct that are themselves struct, arr[T], or map[T] values. Freeing the outer struct does not free those nested allocations; they continue to leak independently until a later stage addresses them.
- A freed map[T]'s own individual entries. Freeing a map frees its entries buffer as a whole, but each entry's key is its own separate allocation (independent of the map's declared value type, and independent of whether that value type is itself covered by this stage or not); those per-entry key allocations are not freed and continue to leak until a later stage addresses them.

None of these are safety gaps -- each one simply means less memory is reclaimed automatically than a more complete implementation would reclaim, not that anything is freed incorrectly. Extending coverage to these cases is expected in later stages, each documented as its own addition to this section or a new one, following the same rule: memory is only freed automatically where the compiler can prove it is safe.
