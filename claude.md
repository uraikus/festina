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

- Fields within a freed struct that are themselves struct, arr[T], or map[T] values. Freeing the outer struct does not free those nested allocations; they continue to leak independently until a later stage addresses them.

This is not a safety gap -- it simply means less memory is reclaimed automatically than a more complete implementation would reclaim, not that anything is freed incorrectly. A freed map[T]'s own per-entry keys were also once an unaddressed part of this same list; that gap is closed (see section 76). Extending coverage to the remaining case is expected in a later stage, documented as its own addition to this section or a new one, following the same rule: memory is only freed automatically where the compiler can prove it is safe.

75. AUTOMATIC MEMORY RECLAMATION (STAGE 2: INTERPROCEDURAL CALL-ARGUMENT ANALYSIS)

This section extends section 74's stage 1. Stage 1 treated a value passed as an argument to any function call as escaping, unconditionally, even when the called function did not actually retain it. This stage removes that limitation for calls to functions declared in the same program.

For each function, in the order it is declared, the compiler determines which of that function's own parameters ever escape within that function's own body, using exactly the same rule section 74 already applies to locals: a parameter is safe if every use of its name is either the immediate object of a field or element access, or an argument to another function call at a position that function's own analysis has already proven safe; any other use marks it escaping. This result is recorded against the function's name once its body has been fully analyzed.

When a later function calls an earlier one, passing a local, arr[T], or map[T] value directly as an argument, that argument is now escaping only if the called function's own analysis marked the corresponding parameter position as escaping. If the called function's analysis proved that parameter safe, the argument is exempted from the default call-argument rule at that call site -- it may still escape some other way, through some other use elsewhere in the calling function, which is judged entirely independently. This proof composes across any number of calls: if function A calls B, and B passes its own parameter straight through to C, then A's own argument is only as safe as C's own analysis of the position it ultimately reaches.

A call to a function not declared in the program -- a builtin, or a call through a field or element access rather than a plain name -- is unaffected by this stage and continues to be treated as escaping, unconditionally, exactly as in stage 1.

A function that calls itself, directly or indirectly, is handled conservatively: since a function's own declaration must precede its use (see section 48's "unknown function" error), the only way a function can call itself is directly, by its own name, before its own analysis has completed. Any argument passed to such a call is treated as escaping, unconditionally, the same as a call to an unanalyzed function -- this may mark a value escaping that a more thorough analysis could have proven safe, but it never marks anything safe that is not.

This stage does not yet analyze whether a value stored into a field of another value that is itself passed as a call argument continues to be retained beyond that call, or extend any further than section 74's own stated remaining limitation on nested fields within a freed struct. Extending coverage further is expected in later stages, following the same rule both stages before it already follow: memory is only freed automatically where the compiler can prove it is safe.

76. AUTOMATIC MEMORY RECLAMATION (STAGE 3: STACK ALLOCATION AND MAP ENTRY KEYS)

This section does not widen what sections 74 and 75 together prove safe -- it changes what happens once something already is.

A struct local proven safe by section 74 or 75 is now allocated on the stack instead of on the heap, and is not freed at all -- its storage is simply reused (for a value declared inside a loop body, on the next iteration; for a value declared in a recursive function, each call still gets its own, since a stack frame is per call regardless) or reclaimed automatically when its declaring function's own stack frame is. This satisfies section 43's preference for stack allocation "when the value's lifetime permits it": sections 74 and 75 are exactly the proof that a given struct's lifetime does permit it. A struct not proven safe by either stage is still heap-allocated and still leaks, exactly as before -- this section only changes how a proven-safe struct's storage is obtained, not which structs count as proven safe.

arr[T] and map[T] locals are not affected by this: their data (or entries) buffer can grow after declaration (an element pushed, a key added to a map), so its size is not known at declaration time, and a stack allocation requires a size known in advance. These continue to be heap-allocated and freed exactly as sections 74 and 75 already describe.

Separately: a freed map[T]'s own entries are now fully reclaimed, including each entry's own key, not just the entries buffer as a whole. This was section 74's own stated remaining limitation on maps specifically, not a new capability -- freeing entries without freeing what each entry's key itself points to was always an incomplete implementation of freeing a map, not a deliberately narrower one.

77. REFERENCE COUNTING FOR ESCAPING STRUCT VALUES

Sections 74 through 76 prove, from syntax alone, that some struct values never outlive their declaring function -- those are freed (or, since section 76, stack-allocated) automatically. A value proven to genuinely escape (returned, stored in a global, ...) has nothing for that kind of proof to do: something else might still be using it, so it is never safe to simply free it once its own declaring scope ends. This section is the answer for that remainder, for struct values specifically: track how many bindings currently reference a value, and free it only once that count reaches zero.

This works completely, not just for the common case, because Festina's type system makes reference cycles structurally impossible: a struct field's type must always be a type declared before the struct containing it, the same rule that requires a function be declared before it is called. No struct can reference itself, directly or through any chain of fields, so the set of values any given struct could ever transitively reference is always a strict subset of what came before it in the same program -- never a cycle. Plain reference counting is therefore a complete answer here, not the usual "handles everything except cycles" partial one.

A struct-typed global variable's value is reference counted. Every time its value changes -- an ordinary reassignment, or its own declaration if that declaration includes an initializer -- the new value is retained and the value it previously held is released, freeing that previous value if nothing else now references it. This includes the very first time a global's value is ever set: its untouched initial value is never itself freed (it was never heap-allocated to begin with), so retaining or releasing it is always a safe no-op, regardless of what a global's value was before.

A local struct variable that is proven to escape its declaring function or handler is also reference counted, and its own reference is released when its declaring scope ends -- the same scope-exit points sections 74 through 76 already track (a function or handler's own end, a block's end, every loop iteration, break, continue). A local's declaration including an initializer, and a local ever being the target of a plain reassignment, do not exclude it from this: both retain the new value before releasing the old one, exactly as a global's own reassignment already did, so two different bindings that turn out to reference the same value never risk a double release. The retain is skipped only when the source of the new value is a plain function call (`Point r = make(n)`, `p = make(n)`) -- a freshly returned value nothing else yet references, whose own +1 transfers cleanly into the new binding -- and is applied for every other shape a source expression can take (reading an existing identifier, a field, a ternary, ...), since any of those might already alias a value some other tracked binding references.

A function's own `return` statement gets the identical treatment: the value being returned is retained first whenever its source expression isn't a plain function call (the same rule as above, applied to whatever `return` hands back to the caller), and every locally-declared struct still active at that point -- including the one just returned, if it was a local at all -- is released right after, the same scope-exit release every other function/handler exit already does. This is what makes it safe for a struct local to no longer need any special exclusion from scope-exit release just because it's returned somewhere in the function: retaining first and then releasing everything nets out to exactly one surviving reference on whichever path actually runs -- the one just handed to the caller -- whether the returned value is a bare local, a struct-typed parameter passed straight through, or one branch of a ternary between two locals (the untaken branch is simply released and freed, same as if it had never been returned at all).

A struct-returning call used as a bare statement, its result never bound to any variable at all (`make(n)` on its own line, not `Point r = make(n)`), is released immediately, right where it's discarded. A function call's own return value is always the "owning," freshly-produced kind this section already treats specially (never aliased anywhere else the moment it's produced), so a call site that never binds it to anything is provably that value's only reference -- releasing it there is always correct, not merely conservative, and needs no aliasing analysis to justify.

arr[T]/map[T] values generally are not covered by this section -- see section 76's own boundary. A struct's own struct-typed fields are covered by section 78.

78. REFERENCE COUNTING FOR A STRUCT'S OWN STRUCT-TYPED FIELDS

Section 77 tracks how many bindings -- a global, or an escaping local -- reference a struct value. The only way to populate a struct-typed field is `outer.field = value` (there is no struct-literal initializer syntax), which stores `value`'s own pointer into the field: an alias, not a copy. Until this section, that write was never itself counted as a reference, which left two different but related gaps: a value stored into a field could be freed by its own original binding's ordinary scope-exit release while the field still pointed to it (a genuine use-after-free, since the field's own reference was never retained), and a struct freed by section 77 never released whatever its own struct-typed fields still pointed to (a leak, since nothing else was ever going to). This section closes both, by treating a struct-typed field exactly like any other binding sections 77 already retains and releases for.

Every `outer.field = value` assignment, where `field` is struct-typed, retains the new value first (skipped only when `value`'s own source is a plain function call, the same "fresh, uniquely-owned, no retain needed" rule section 77 already applies everywhere else) and then releases whatever the field previously held -- always safe, since a struct's own fields start out null (its zero-initialized storage never populated any differently) and releasing null is always a no-op.

When a struct value section 77 frees reaches a refcount of zero, each of its own struct-typed fields is released too, before its own storage is freed -- recursively, so a field's own struct-typed field is released the same way, and so on through however many levels a program actually nests. This is sound for the identical reason section 77's own reference-cycle argument already established: a struct field's type must always be declared before the struct containing it, so the graph of which struct types can reference which others through their own fields is a DAG, never a cycle, and this recursion always terminates.

A struct local proven safe by sections 74-76 to live on the stack, rather than being reference counted at all, is unaffected in how its own storage is handled -- it is still never freed, exactly as before. But if such a local has a struct-typed field of its own, and that field is ever written, the field's own retained reference still needs releasing when the local's declaring scope ends, the same way an arr[T]/map[T] local's own data/entries buffer needs freeing even though the local's own header storage is stack-allocated: only the field's own reference is released at that point, never the local's own storage, which was never heap-allocated to begin with.

An arr[T]/map[T]-typed field of a struct is retained/released exactly like a struct-typed field, once section 79 makes arr[T]/map[T] itself a refcounted value with a release function of its own to call. A struct-typed element of an arr[T]/map[T] value is not covered by either section -- see section 79's own boundary for why.

79. REFERENCE COUNTING FOR ESCAPING ARR[T]/MAP[T] VALUES

Sections 74-76 free a non-escaping arr[T]/map[T] local's own data/entries buffer at scope-exit; an escaping one -- returned, stored in a global, assigned into another binding -- had nothing tracking it at all before this section, and simply leaked. This section is the same answer section 77 already gave for structs, applied to arr[T]/map[T]: an escaping arr[T]/map[T] value is now reference counted, freed only once nothing references it anymore.

This required a representation change first, not just a new tracking rule. An arr[T]/map[T] value used to be the `{length, data}` (or `{count, entries}`) pair itself, copied by value on every assignment -- two bindings that had been made to agree could each hold their own independent copy of that pair, sharing the same data/entries pointer only until one of them changed. arr[T] never grows after its own construction, so this was merely imprecise for arrays; map[T] does grow (a new key added via `npcHealths[key] = v` can realloc the entries buffer), and growing it through one binding left every other binding that had ever been made to alias it holding a stale pointer into memory that reallocation may have already moved or freed -- a genuine, pre-existing memory-safety bug, confirmed directly (a real segfault) before this section existed, not a hypothetical one. An arr[T]/map[T] value is now a single `ptr` to its own heap-allocated storage instead, the same indirect representation a struct value already has: two bindings made to alias each other now share the identical header, so a growth through either one is visible through both, correctly, every time -- closing this bug as a direct consequence of the representation change this section needed anyway, not a separately-motivated fix bolted on alongside it.

Every arr[T]/map[T] binding -- a local's own declaration, a plain reassignment, a global, a struct field, a value handed back through `return`, a call result discarded outright at its own call site -- follows the identical retain/release rule sections 77 and 78 already established for structs, down to the same "owning" exception: a plain function call is still "owning" (a fresh, uniquely-referenced value, no retain needed to alias it into a new binding), and so, new to this section, is an array or map literal (`[1, 2, 3]`, `{...}`) -- structs have no literal syntax at all, so this case never arose for them, but a literal allocates a fresh header exactly like a call's own return value does, nothing else referencing it the instant it's produced. Every other source shape (an existing identifier, a field read, a ternary, ...) is retained, the same conservative default every prior stage in this whole effort has used whenever a choice wasn't fully provable either way.

A non-escaping arr[T]/map[T] local proven safe by sections 74-76 to keep its own header on the stack is unaffected in how that header is allocated -- still never heap-allocated, still never itself refcounted, exactly as before this section. Its data/entries buffer is still always heap-allocated regardless (a dynamically-sized buffer was never safe to give a fixed-size alloca, escaping or not) and still needs freeing at that local's own scope-exit, exactly as sections 74-76 already required -- this section only adds a *second*, different scope-exit action (releasing the whole refcounted header) for the local whose header itself escapes.

A struct-typed element of an arr[T]/map[T] value, and an arr[T]/map[T]-typed element of another arr[T]/map[T] value, are not covered by this section: an arr[T]/map[T]'s own elements/values are a dynamically-sized, runtime-indexed collection, not a fixed field list a struct's own type declaration already enumerates, so retaining/releasing them individually needs walking the collection at every point one is stored or the whole container is freed -- a different, harder problem than the fixed-shape field walk sections 78 and this section both already do, not attempted here. A struct/array/map value stored as an element or value is still correctly retained by its own OTHER tracked binding, if it has one, exactly as before -- only the specific case of a value reachable *solely* through an arr[T]/map[T] element/value, after every other binding to it has gone out of scope, is unsound (a genuine use-after-free, confirmed directly the same way section 78's own original gap was, before this section closed the field case but left the element case open) -- see section 80.

80. REFERENCE COUNTING FOR AN ARR[T]/MAP[T]'S OWN ELEMENTS/VALUES

Section 79 made an arr[T]/map[T] value itself refcounted, but left its own elements/values untouched, since walking a dynamically-sized, runtime-indexed collection is a different problem from the fixed-shape field walk section 78 already does for a struct's own fields. This section is that walk: an array element or map value whose own type is itself refcounted (struct, arr[T], or map[T]) is now retained when stored and released when overwritten or when the container holding it is freed, closing the exact use-after-free section 79 left open -- confirmed directly beforehand, the same way as every other gap this whole effort has closed: a struct built fresh inside a function, stored as an array's sole element, the array assigned to a global before that function returns; reading the global's element afterward, well past the struct's own original scope-exit release, was a genuine heap-use-after-free, caught directly by AddressSanitizer before this section existed.

This is sound for exactly the same structural reason sections 77-79 already lean on, applied one level down: Festina's grammar gives every arr[T]/map[T] type a syntactically fresh, finite type expression at each nesting level -- there is no way to write a self-referential array or map type the way section 77's own argument rules out for structs -- so the recursion this section introduces (releasing an arr[arr[T]]'s own elements means releasing each one as an arr[T], which may in turn mean releasing ITS own elements, ...) always terminates, on a nesting depth fixed at compile time by the program's own source text.

Array elements and struct fields now share the identical retain-new/release-old code path for `arr[i] = value`, exactly as `outer.field = value` already used for struct writes -- unsurprising, since both are "overwrite one binding's worth of a fixed-address, refcounted slot," the same operation section 78 already handles for a field. The one-time element store during array-literal construction (`[a, b, c]`) retains each refcounted element the same way section 79 already retains an aliased whole-array/whole-map value, skipped only for the same "owning" source shapes section 79 already exempts (a function call, or -- new here, since it applies one level down too -- an array/map literal used as an element's own source expression); no release-old is needed there, since a freshly malloc'd buffer was never previously holding a valid pointer at any of its slots.

map[T] needed a different mechanism for both directions, since a `FestinaMapEntry`'s own layout is deliberately opaque outside the C runtime (the same boundary `festina_map_find`'s own comment already documents). `map[key] = value`, in both a map literal's own construction and a later assignment, retains the new value and releases whatever the key previously held by looking up any existing value first (via the existing `festina_map_get`, with a null default -- always safe, since releasing null is always a no-op, whether the key was genuinely absent or present but itself null) before the set proceeds; releasing every value in a map being freed reuses the existing `festina_map_for_each` iteration the language's own `.forEach()` already relies on, passing a release-flavored callback instead of a user one, rather than adding any new C-side structure access.

A non-escaping arr[T]/map[T] local proven safe by sections 74-76 to keep its own header on the stack still isn't itself refcounted, unaffected by this section exactly as section 79 already established -- but if its own element/value type is refcounted, each element/value still needs releasing at that local's own scope-exit, the same "the header's storage was never heap-allocated, but what it points to still needs freeing" distinction section 79 already draws for the data/entries buffer itself.

81. STACK ALLOCATION FOR A LITERAL-INITIALIZED NON-ESCAPING ARR[T]/MAP[T] LOCAL

Sections 74-76 already give a non-escaping struct local, and a non-escaping arr[T]/map[T] local declared with no initializer, a real stack-allocation option instead of a heap/refcounted one. A with-initializer arr[T]/map[T] local never got this option at all, even when non-escaping: section 79's own with-initializer path always routed through the general array/map-literal construction, which always heap-allocates its own header, unconditionally, regardless of where the resulting value ends up bound -- correct, since a literal used as a nested subexpression (a function argument, a return value, a struct field write, ...) might genuinely need that header to outlive its own construction, but needlessly conservative for the extremely common case of a local declared directly from a literal and never used anywhere else.

This section closes that gap for exactly the one case it's provably safe to: a local whose initializer is an array/map literal written directly at the declaration itself (not merely an expression that happens to evaluate to one), and which escape analysis (sections 74-75) already proves non-escaping. Both conditions matter: the literal's own element/entry count is only known at compile time when it's written directly here (an identifier bound to some other literal elsewhere doesn't give this method anything provable about it), and non-escaping already rules out this local ever later being the target of a plain reassignment too -- an assignment target always escapes, by escape analysis's own existing rule -- so there is no risk of the local later being pointed at a genuinely different, possibly-heap value the way the general retain/release machinery would otherwise need to account for.

The fix mirrors the no-initializer case exactly: the header is a plain, zero-initialized stack alloca instead of a fresh heap allocation, built directly into by the same array/map-literal construction logic (`_emit_array_lit`/`_emit_map_lit`), now parameterized to accept a caller-supplied header slot instead of always allocating its own. The literal's own data/entries buffer is unchanged -- still always heap-allocated (a dynamically-sized buffer was never safe to give a fixed-size alloca, matching section 79's own boundary) -- so this section only removes one of the two heap allocations a with-initializer local used to need, not both; the buffer is still freed at that local's own scope-exit, through the identical stack-header-with-heap-buffer scope-exit handling section 79's own no-initializer case already established.

Verified with a compile-and-run/ASan regression suite the same way every prior stage in this whole effort has been, and directly against a real benchmark: a 2,000,000-iteration loop building a fresh 8-element arr[int] local every iteration dropped from 209ms to 86ms -- landing at parity with Rust and Go's own equivalent, rather than a real, honest 2.4x behind them -- purely from this one allocation eliminated per iteration, with the exact same output before and after. See benchmark.md's own `array_sum` writeup for the full before/after numbers.

82. TEMPLATE LITERALS SKIP CONCATENATING WITH AN EMPTY LITERAL PIECE

A template literal `` `a${x}b` `` used to compile into a fixed shape regardless of content: starting from its own first literal piece, then alternating "concatenate the next interpolated value in, concatenate the next literal piece in" for every expression -- two `festina_str_concat` calls per interpolation, unconditionally, even when one (or both) of the pieces being concatenated is the empty string. Concatenating with an empty string is always a no-op (`"" + x == x`, `x + "" == x`), but the old codegen still allocated a fresh buffer and copied into it for that no-op every single time -- pure wasted work, and a common one: any template that starts or ends with an interpolation (`` `${x}` ``, `` `${x} things` ``, `` `things ${x}` ``) has an empty leading or trailing piece by construction, and adjacent interpolations (`` `${a}${b}` ``) have an empty piece between them too.

This section makes that no-op literal, not just semantically: an empty literal piece is skipped entirely rather than emitted as a `festina_str_concat` call. When the very first piece is empty, the first interpolated value's own text becomes the running result directly (an alias of its existing pointer, not a fresh copy) rather than being concatenated onto a fresh empty-string constant.

That aliasing was safe when this section was written, for a reason section 83 has since removed: `text` was at the time never freed or reference-counted anywhere in generated code, so there was no ownership concern in handing back a pointer two different expressions now both read. Section 83 makes text values genuinely owned and genuinely freed, which turns that alias into a real double-free/dangling-pointer hazard, and revises this optimization accordingly -- the empty-piece skip itself is unchanged and still applies, but a template whose result would otherwise be a bare alias of an interpolated value now takes one `festina_text_own` copy on the way out. See section 83 for the full rule.

Verified with the existing template-literal test suite (unaffected -- output is identical, only the generated IR's own call count changes) and directly against a real benchmark: 15,000 iterations of naive one-character-at-a-time concatenation (`` s = `${s}x` ``, which this section turns from `("" + s) + "x"`'s two calls into a single `s + "x"` call) dropped from 140ms to roughly 77ms -- close to halving the whole benchmark's own runtime, purely from removing the one redundant call this section closes, without touching the underlying O(n²) naive-concatenation algorithm at all. See benchmark.md's own `string_concat` writeup for the full before/after numbers.

83. TEXT VALUES ARE OWNED AND FREED (COPY ON ALIAS)

Sections 74-81 gave struct, arr[T], and map[T] a complete ownership story -- stack allocation where provable, reference counting everywhere else, cascading through fields, elements, and map values. `text` was left out of all of it: a text value was never freed anywhere in generated code, at any binding site, under any circumstance. Every reassignment abandoned the previous buffer, every scope exit abandoned every text local, and the process simply grew its heap until it exited. That is not a leak in the "small bounded overhead" sense -- benchmarks/string_concat.f, 15,000 iterations of `` s = `${s}x` ``, leaked every intermediate buffer it built, so its heap grew quadratically and the program spent essentially all of its runtime asking the kernel for more of it: 816 `brk()` calls, against 3 for the equivalent leak-free C. Freeing correctly took that benchmark from roughly 655ms to under 5ms.

Text does NOT get the refcount-header representation sections 77-79 use. That representation works by placing a counter immediately before the payload, which every consumer must then know about -- and text's payload is a plain `char*` that sqlite, the regex engine, `festina_log_text`, every comparison, and every runtime helper already take directly. Changing its representation would touch all of them. Instead text gets its exclusivity by copying, which needs no representation change at all: **every text-typed binding always holds either NULL or a heap buffer it owns exclusively**, never a bare alias of a `.str.N` literal constant and never a bare alias of another binding's buffer. The single runtime addition is `festina_text_own` (a NULL-safe `strdup`); freeing is plain `@free`, which is already NULL-safe by C's own rules, so unlike struct/arr[T]/map[T] no per-type release wrapper is needed -- `_release_fn_for` simply returns `@free` for text, which is what lets section 80's existing element/value cascade machinery pick up text-typed array elements and map values with no new cascade logic at all.

The decision at each binding site is whether the value's SOURCE is already a fresh buffer. A Call is (every text-returning runtime function mallocs, and a user function's own `return` only ever hands back something this same rule already proved fresh), and so is a TemplateLit (`_emit_template` guarantees it). Everything else aliases -- a bare Identifier, a Member read, a Ternary, and critically a StringLit, whose pointer is into the binary's own static data where `free()` would be catastrophic -- and so is copied through `festina_text_own` before being stored. This is the same owning-vs-aliasing split sections 77-80 already apply to struct/arr[T]/map[T], with a copy substituted for a retain.

Because a text binding always holds its own exclusive copy, **freeing it needs no escape analysis at all**. This is the sharp difference from struct/arr[T]/map[T], where whether a local may be freed at scope exit depends on proving it never escaped: copying happens at each CONSUMING site rather than by draining the source, so no number of other bindings having read a text local can make freeing it unsafe. A text local is therefore tracked for scope-exit freeing unconditionally, and freed unconditionally on reassignment. The one prerequisite this exposed had to be fixed first: a `text s` declared with no initializer previously got an alloca and no store whatsoever, leaving genuine uninitialized garbage in the slot -- harmless while text was never freed, an immediate wild-pointer `free()` afterward -- so it is now explicitly null-initialized, matching what struct/arr[T]/map[T] locals already did.

Section 82's empty-piece optimization had to be revised to preserve the "a TemplateLit is always fresh" half of that rule. A template that performs no concatenation at all (a bare `` `${name}` ``, whose leading and trailing pieces are both empty and both skipped) would otherwise hand back `name`'s own buffer, and every consumer, believing it owns what a template returns, would free a buffer `name` still points at. That one shape -- and only that shape -- now takes a `festina_text_own` copy on the way out. Getting this boundary wrong in the other direction is equally real and was caught by LeakSanitizer during development: copying whenever the running result was still empty, including when a literal piece was about to be concatenated onto it anyway, allocated a second buffer that `festina_str_concat` then read and never freed.

Templates also have to free their own intermediates, which nothing previously did. `festina_str_concat` allocates a fresh buffer and copies both operands into it, leaving both untouched, so a template chaining four concatenations leaks three buffers unless each is freed the moment the next concatenation has finished copying out of it. `_emit_template` now tracks, for the running result and for each interpolated piece, whether the pointer in hand is a buffer the template itself allocated (free it once consumed) or someone else's storage (never free it), and emits the frees inline.

The last class of leak is a text temporary that is produced and consumed without ever being bound to anything -- the `f()` in `log(f())`, or a template passed straight as an argument. Callees never take ownership of a text argument (one the callee reassigns is copied at binding time per section 84; one it only reads is borrowed for the call's duration), so the caller still owns what it passed and must free it once the call returns, or nothing ever will. These frees are emitted immediately after the consuming call rather than collected into a statement-level cleanup list, which is both simpler and better: producer and consumer are always in the same basic block at every one of these sites, so the free always dominates correctly with no holding slot needed, and a temporary inside a loop is freed once per iteration rather than once per loop.

Coverage is the set of sites where a text temporary is both produced and discarded: `log()`, user function calls, the text/regex methods (`.replace()`, `.replaceAll()`, `.match()`, `.test()`), `regex()`, `loadAudio()`, the graphics builtins (`drawText`, `loadImage`), and sqlite -- both a query's own SQL string and each bound text parameter. Each is safe to free at exactly that point for a reason checked against the runtime rather than assumed: `regcomp` compiles the pattern into its own `regex_t`, Cairo copies the glyphs it draws, `fopen`/`cairo_image_surface_create_from_png` only read the path, `sqlite3_prepare_v2` compiles the SQL into the statement, and `festina_sqlite_bind_text` binds with `SQLITE_TRANSIENT`, which makes sqlite take its own copy before returning. The timer builtins need nothing here: `setTimeout`/`setInterval` take a function name and an int delay, and `clearTimeout`/`clearInterval` take an int id, so no text ever reaches them.

Verified with a compile-and-run/AddressSanitizer regression suite the same way every prior stage in this effort has been -- locals, globals, uninitialized locals, reassignment, nested call temporaries, struct fields, array elements, map values, regex/text methods on temporaries, loop accumulation, and parameter reassignment, all clean under LeakSanitizer with byte-for-byte identical output -- and directly against the benchmark that motivated it. See benchmark.md's own `string_concat` writeup for the full before/after numbers.

84. A REASSIGNED PARAMETER OWNS ITS OWN REFERENCE

A struct/arr[T]/map[T] parameter is passed as the caller's own raw pointer, unretained. That "borrowed" convention is deliberate and worth keeping -- a callee that only reads its parameter has no reason to touch a refcount, and section 75's interprocedural analysis already depends on being able to prove exactly that. But a callee that REASSIGNS its own parameter (`p = somethingElse`) runs the ordinary local-reassignment path from section 77, which releases whatever the binding currently holds before storing the new value. For a borrowed parameter that is the CALLER's live value, and releasing it drops a refcount the callee never incremented -- freeing the caller's value out from under it while the caller is still using it.

This was a real, pre-existing use-after-free, not something the text work introduced; it was found while designing text's own parameter handling, which has exactly the same shape. Confirming it took some care, because the two most obvious repros both hide it. A global that has never been assigned still carries the immortal negative-refcount sentinel static storage is initialized with, on which retain and release are both no-ops, so nothing goes wrong. A global that HAS been assigned gets an unconditional retain on that assignment, leaving its count at 2, so the callee's erroneous release only brings it back to 1 and the symptom is a leak rather than a crash. Only a plain heap-allocated LOCAL passed to a parameter-reassigning callee exposes it, and under AddressSanitizer that is an unambiguous heap-use-after-free with both the freeing and the allocating stack recorded.

The fix: a parameter the callee assigns to is given its own reference at binding time -- `festina_retain` for struct/arr[T]/map[T], a `festina_text_own` copy for text -- and released at the callee's own scope exit, so the callee is only ever mutating a binding it owns. This required computing escape analysis BEFORE parameters are bound rather than after (it previously ran inside the function-body emitter, by which point the bindings already existed), and giving the parameters their own `_active_free_locals` frame outside the body's own, popped once the body is fully emitted.

The retain/copy is keyed on the whole `escaping` set rather than on reassignment alone. Every reassigned name is necessarily in that set -- escape analysis adds every bare-Identifier assignment target, which is exactly what makes this safe -- but the set is broader, so a text parameter that is merely interpolated or passed along also gets a copy it does not strictly need. That is the same over-conservative-rather-than-imprecise bias every prior stage in this effort defaults to, and narrowing it to genuine reassignment is tracked in todo.md. It is safe to narrow because every other escaping use of a parameter either borrows it (safe by the existing convention) or does its own retain/copy at the storing site.

85. QUERY ROWS AND RUNTIME-COMPILED REGEXES ARE RECLAIMED

Two leak classes section 83 surfaced but did not itself cause. Both were pre-existing, and both were unbounded rather than one-off -- they grew with the number of queries run or regexes compiled, not with the size of the program.

A sqlite result row is deliberately not shaped like any other value in this language. `festina_sqlite_collect_rows` builds each one as a plain `malloc(col_count * sizeof(int64_t))`, with each text/blob column strdup'd into its slot, and -- unlike every struct/arr[T]/map[T] value since section 77 -- no refcount header in front of it. `TableType` is also a separate type class from `StructType`, so every `isinstance(t, (StructType, ArrayType, MapType))` test in codegen missed it entirely and nothing ever freed a row or any of its text columns. `arr[People] rows = sqlite(...)` is this language's single most central idiom, so in practice every query leaked its entire row set. What was already correct is the container itself: an `arr[T]` is an `arr[T]` whatever its element type, so the array header and the pointer buffer hanging off it were always freed properly -- only the rows those pointers point AT were not.

The fix has to respect two things at once. Because a row has no refcount header, `festina_release` (which reads the eight bytes before the payload) could never be pointed at one, so this cannot reuse the existing release machinery. And because the array owns its rows outright, a `People p = rows[0]` local -- or a row passed to a function -- is only ever borrowing one the array still owns. So the per-row free is a bespoke, per-table generated function reached SOLELY from `_release_fn_for_array`'s own element cascade, and deliberately not exposed through `_release_fn_for`, which would otherwise let an arbitrary TableType-typed binding free a row out from under the array holding it. Which columns need freeing is decided by the identical rule the runtime used when building the row -- `text` or `blob` gets a strdup, everything else is a plain i64 -- read off the same declared column types, with `free(NULL)` covering a column that was SQL NULL and so never strdup'd at all.

The second leak is `regex()`. Every runtime `regex(pattern)` call compiles a fresh `regex_t` (several kilobytes once regcomp's automaton is built) that nothing ever freed, so a `regex(...)` evaluated inside a loop leaked one per iteration. A regex used as a temporary in the expression that compiled it -- `regex(p).test(s)`, the ordinary shape -- is now released through a new `festina_regex_free` (regfree to release what regcomp allocated inside the struct, then free for the struct itself), reusing section 83's own "free it immediately after the consuming call, in the same basic block" approach.

The owning test here separates the two ways a regex value is produced, and getting it wrong in either direction is a real bug rather than a missed optimization: only `regex(...)` is an `ast.Call`, while a `/pattern/` literal is an `ast.RegexLit` compiled once into a process-lifetime cache (section 67), which must never be freed or every later evaluation would run against a dangling `regex_t`. A regex bound to a variable is an Identifier at each of its use sites and so is likewise never freed -- it still leaks, bounded by the number of such declarations rather than by how often they run, since regex has no binding-level ownership story the way text now does. Tracked in todo.md rather than claimed closed.
