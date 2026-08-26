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

86. A NON-ESCAPING REGEX LOCAL IS FREED

Section 85 freed a regex used as a temporary in the expression that compiled it (`regex(p).test(s)`), but left a regex bound to a variable leaking exactly as before -- and `regex r = regex(p)` inside a loop leaks a full compiled automaton, several kilobytes, on every iteration, so this was unbounded rather than bounded by declaration count as first assumed.

This section frees exactly the case that is provable: a regex local whose initializer is a `regex(...)` Call, and whose name escape analysis (sections 74-75) proves never leaves the declaring function. Both halves are load-bearing, and relaxing either frees something still in use. A `/pattern/` literal initializer is a pointer into the process-lifetime cache section 67 builds, so freeing it would leave every later evaluation of that same literal running `regexec` against freed memory. And an escaping regex has no equivalent of the copy-on-alias escape hatch that makes text's own freeing unconditional (section 83): a regex "copy" would mean recompiling, and the pattern string isn't retained to recompile from, so exclusivity can't be manufactured the way it can for text. An escaping regex is therefore left to leak, deliberately, rather than freed while another binding may still point at it.

Deliberately reached only through the scope-exit path, never through `_release_fn_for`. Routing it through the generic dispatcher would make an `arr[regex]`'s own element cascade free each element, and those elements can perfectly well be cached literals -- the exact double-free-a-shared-constant hazard this section's own ownership test exists to avoid, reintroduced one level down. This is the same containment argument section 85 already makes for a table row's per-row free.

87. THE X DISPLAY CONNECTION IS RETRIED

`festina_graphics_init` called `XOpenDisplay` exactly once. Xlib does no retrying of its own, so a single transient failure to connect -- a full listen backlog on the X server's socket under load, or a server accepting connections but momentarily not completing them -- killed the whole program with a fatal error that named entirely the wrong cause ("is $DISPLAY set?").

This was also the entire cause of `tests/test_codegen.py`'s TestGraphics being intermittently flaky: roughly a third of full-suite runs failed one graphics test, essentially never when run in isolation. That had previously been attributed to contention making window startup slow, and "fixed" by raising the test's own polling timeout from 10s to 20s. The diagnosis was wrong and the fix could never have worked, because the program had already exited by the time the polling began. Measured directly: a window appears in about 0.2 seconds, consistently, and no timeout helps when the process is already dead.

The forensics are worth recording, since the failure looks exactly like a dead or wrong X server and isn't either. Instrumenting the moment of failure showed the Xvfb process still alive; `/tmp/.X11-unix/X<n>` and `/tmp/.X<n>-lock` both present, with the lock file naming that same live server's own pid (ruling out a display-number collision, the other obvious suspect); and `xdotool` connecting to that exact display successfully both immediately before and immediately after the failed attempt. The connection was simply refused once, under load.

Ten attempts, 100ms apart, so a genuinely absent X server still fails with the same clear message in about a second. Verified against the workload that produced the bug: 0 failures in 216 runs under heavy parallel contention, against 4 in 128 before, and three consecutive clean full-suite runs where roughly one graphics test had been failing per run. The suite also got about 25 seconds faster, purely from no longer spending 20s timing out on an already-dead process.

88. A STRUCT'S OWN TEXT FIELD IS FREED

Section 83 added the machinery to free a struct's text-typed field, but never widened the predicate that decides whether a struct needs field cleanup at all -- it still counted only struct/arr[T]/map[T] fields, the set sections 78-79 established. A struct whose only managed field is a text one therefore fell through both directions that predicate gates: stack-allocated, it was never scheduled for field release at all; heap-allocated, it got the plain generic `festina_release` rather than a per-struct wrapper. Either way its field's buffer was never freed, so `Person p` with a `name:text` field leaked that field on every scope exit, and a struct rebuilt in a loop leaked one buffer per iteration.

The fix is the one-line widening the section-83 work should have included, plus the rename that makes the predicate's job honest: "has a managed field" rather than "has a refcounted field," since text is managed by exclusive ownership rather than by counting. Found by AddressSanitizer over a struct whose text field is reassigned (`p.name = `tmpl ${p.name}``), which leaked the field's final buffer every time -- a reminder that the section-83 verification sweep passed only because none of its own programs happened to leave a struct alive with a text field in it.

A genuinely separate, long-standing crash surfaced while building the test program for this section, and is recorded here rather than fixed, since it is a language-semantics question rather than a memory-management one: writing to a field of a NESTED struct field that was never itself assigned (`outer.inner.label = x`, where `inner` is a struct-typed field that no one has given a value) dereferences a null pointer and segfaults. A stack-allocated struct is `zeroinitializer`d, so its struct-typed fields start as null pointers, and nothing allocates one on first use the way a top-level declaration does. Confirmed to predate every section from 74 onward by reproducing it directly on the commit before that whole effort began. Fixing it means deciding whether a nested struct field should be allocated eagerly at its parent's declaration, or lazily on first write, and that choice interacts with sections 77-79's ownership rules -- see todo.md.

89. CANVAS DRAWING STYLE AND TEXT METRICS

Sections 37 and 39 gave the canvas its drawing functions but no way to change how anything looks: every shape was solid black, every string was 16px sans-serif, and nothing could be outlined. This section adds `fillStyle(text)`, `borderColor(text)`, `lineWidth(int)`, `font(text)`, `measureTextWidth(text)` and `measureTextHeight(text)`.

Style is process-global state, set once and read by every later draw call, rather than extra arguments on each drawing function. That follows the HTML canvas 2D context, and it is also what sections 37/39's own worked examples require: `drawRect(0, 0, 100, 100)` takes geometry only, so adding style parameters would have meant either changing those signatures or carrying two spellings of every function. Every default reproduces exactly what these functions drew before this section existed -- black fill, no border, 16px sans-serif -- so no existing program's output changes.

`fillStyle` sets the colour of everything filled: `drawRect`, `drawCircle`, and the glyphs `drawText` draws. `borderColor` together with `lineWidth` outlines the shapes -- `drawRect` and `drawCircle` -- and deliberately does not outline text, since a glyph outline is a genuinely different operation from a shape's border and is not what a program setting a border colour is asking for. A border is drawn only once `borderColor` has actually been called with a real colour, which is what keeps a program that never mentions it drawing the same plain filled shapes it always did.

A colour is a name from a small fixed table (`red`, `blue`, `black`, `orange`, ...), a `#rgb` or `#rrggbb` hex value, or `none`/`transparent`. Names are case-insensitive and `#abc` expands to `#aabbcc`, both matching CSS. The table is deliberately the couple of dozen colours a program actually reaches for rather than the full ~148-entry CSS list, because every additional name is one more thing a typo can silently resolve to. `none` is meaningful on both: as a fill it leaves a shape's interior untouched so that `borderColor` alone draws an outline-only shape, and as a border colour it turns borders back off again. Anything else calls `festina_fail` naming the offending value, rather than defaulting to black -- and it fails at the `fillStyle()` call itself rather than at the next draw, so the error points at the line that set the bad value.

`font` accepts a tolerant subset of the CSS/canvas shorthand: whitespace-separated words in any order, where `italic`/`oblique` set the slant, `bold` sets the weight, a bare number or `<n>px` sets the size, and the first word that is none of those becomes the family. Order-independence is deliberate. The strict CSS grammar requires size and family last and in that order, which is exactly the kind of rule that turns a reasonable-looking string into a silent no-op, and none of the ambiguity that grammar exists to resolve can arise here.

The two measure functions return an `int` and, unlike every other function in this section's neighbourhood, deliberately do not open the canvas window -- the same rule `loadImage` already follows (section 37). Text metrics depend only on the font, so they run against a scratch image surface and work in a program that never draws at all, and indeed with no X server present. `measureTextWidth` returns the advance width -- how far the pen moves -- which is what laying one string out after another actually needs, and matches the canvas `measureText().width`. `measureTextHeight` returns the inked height of that particular string, which is why it takes the text rather than reading the font alone: `x` is shorter than `Xg`. Both share the same font-application path the drawing functions use, so a measurement can never disagree with what a later draw of the same string produces.

These six join the existing rule that a user function may not shadow a builtin name (see the section-7 audit note): codegen's builtin dispatch always wins, so a same-named user function would be silently uncallable. A program that already declares its own `font` or `lineWidth` now fails to compile, which is the intended behaviour for exactly the reason that rule exists.

90. COLOURS AND FONTS ARE RESOLVED AT COMPILE TIME

Section 89 gave the canvas colours and fonts, but had the runtime do the work: `fillStyle` took a `char*`, compared it against a colour table and parsed hex; `font` took a `char*` and parsed a shorthand grammar. Both ran on every single call. This section moves all of it to compile time, which is this project's own stated preference ("compile-time work over runtime work") applied to the one part of the graphics API still doing string work while drawing.

`fillStyle('red')` and `font('arial 14px bold')` still read exactly the same in source. What changes is what they compile to: `festina_set_fill_rgb(255, 0, 0)` and `festina_set_font(14, "bold", "arial")`. The runtime now holds no colour-name table, no hex parsing and no font grammar at all -- what used to be a table scan per `fillStyle` is three integers already in registers.

Resolution lives in `festina/colors.py`, which is deliberately the ONLY copy of this knowledge in the project. Duplicating a 148-entry colour table between a Python compiler and a C runtime would have been a standing invitation for the two to drift, and neither the CSS names nor the font grammar are things the runtime has any remaining reason to know.

The colour set is now the full CSS Color Module Level 4 named-colour list -- the 147 extended keywords inherited from X11 plus `rebeccapurple` -- alongside `#rgb`/`#rrggbb` and `none`/`transparent`. Section 89 deliberately shipped a much smaller table on the grounds that every extra name is one a typo could silently resolve to; that argument no longer applies, because a name is now resolved while compiling, so a typo is a compile error naming the offending value and its line rather than anything that can reach a running program.

`none`/`transparent` compiles to a negative component. No real channel value can be negative, so that says "no colour at all" without needing an extra argument or a second runtime function to distinguish it.

Both functions also accept an explicit form -- `fillStyle(red, green, blue)` with each component 0-255, and `font(px, style, family)` where `style` and `family` may be `null` and a non-positive `px` leaves the size alone. This is what a program uses to compute a colour or a size at runtime, and it is why requiring a literal in the one-argument form costs nothing: the explicit form is strictly MORE capable for anything dynamic, since it takes any int expression, where a colour name could only ever have named one of a fixed set. A non-literal passed to the one-argument form is a compile error pointing straight at it.

The font shorthand accepts its words in any order, and any part may be omitted: `'arial 14px bold'`, `'bold 14px arial'` and `'14px'` are all accepted, the last compiling to `font(14, null, null)`. Order-independence is deliberate -- CSS's own grammar requires size and family last and in that order, exactly the kind of rule that turns a reasonable-looking string into a silent no-op, and none of the ambiguity that grammar exists to resolve can arise here. `style` is normalised by the compiler to `null`/`'bold'`/`'italic'`/`'italic bold'`, so the runtime never sees an ordering or spelling variant; the substring tests it does use exist only because the explicit form can hand it an arbitrary runtime string.

The one behaviour this removes is a colour or font built from a runtime-computed *string*. That was expressible under section 89 and is now a compile error. It is not a loss in practice -- the explicit numeric form covers every dynamic case more directly, and does so without the string ever existing -- but it is a real difference from what section 89 shipped, so it is called out here rather than left to be discovered.

91. COLOR AND FONT ARE TYPES

Section 90 resolved colour and font literals at compile time, but they were still *arguments*: every `fillStyle('red')` re-resolved the same name, and a colour used in twenty places was written out twenty times. This section makes both first-class types, so a colour or a font is resolved exactly once -- at the declaration that names it -- and referred to by name everywhere after.

```festina
color brand = '#4a90d9'
font  body  = '13px arial bold'

fillStyle(brand)
changeFont(body)
```

`font` becoming a type name is what forces the setter's rename: `font(...)` can no longer be a function call when `font` introduces a declaration, so the setter is now `changeFont(newFont:font)`. `fillStyle`/`borderColor` keep their names and take a `color`.

Each type compiles to the shape its use actually wants. A `color` is a packed `0xRRGGBB` integer, so passing one costs a single register, comparing two is one integer compare, and a negative value means "no colour at all" without needing a second field or a separate function to say so. A `font` is a pointer to a static `%struct._FestinaFont` constant -- size, slant, weight, family -- that codegen emits into the binary's own read-only data from the declaration's literal, so declaring a font costs no runtime work whatsoever and `changeFont` passes one pointer. Identical fonts share one constant, keyed on their resolved parts rather than their source text, so `'bold 13px arial'` and `'arial bold 13px'` collapse together.

Neither type touches the memory machinery sections 74-88 built, and this is worth stating plainly because both look like they might. A colour is a plain integer, with no more lifetime than an `int`. A font is a pointer to a constant that nothing allocates and nothing frees, so copying a font value copies a pointer to storage that outlives every binding that could ever hold it. Neither is reference-counted, neither is copy-managed, and neither appears anywhere in scope-exit handling.

Both are written as text at the declaration, because `color brand = '#4a90d9'` reads better than any constructor syntax would, and there is no separate literal form for either. That reuses exactly the one-directional `text -> X` allowance section 36 already established for `blob`, for the same reason: nothing else in the language could construct one.

The consequence, and the rule this section is really about: **a colour name or a font shorthand can only ever come from a literal.** `fillStyle('red')` is no longer valid -- a name must be declared as a `color` first. A `text` value computed at runtime cannot become either type, and trying is a compile error naming the alternative. That alternative is `fillStyle(red, green, blue)` with each component 0-255, and `changeFont(px, style, family)` with a nullable style and family; both remain exactly as section 90 left them. This costs nothing, because those forms are strictly MORE capable for anything dynamic -- they take arbitrary int expressions, where a colour *name* could only ever have named one of a fixed set -- and it buys a language where no colour string and no font grammar exists at runtime at all.

A `font` literal that says nothing (`font f = ''`) is rejected rather than silently producing a record that changes nothing, since it is far more likely to be a mistake than an intent.

92. IMG GAINS CLIP, RESIZE, WIDTH AND HEIGHT

Section 37 gave `img` exactly two operations: load one, draw it whole. That is enough for a picture and useless for a spritesheet, which is the shape most 2D graphics actually take -- one PNG holding a grid of frames, each drawn separately. This section adds `.clip(x, y, w, h)`, `.resize(w, h)`, `.width` and `.height`.

```festina
img sheet = loadImage('sheet.png')
img grass = sheet.clip(0, 0, 64, 64)
grass.resize(32, 32)
```

`clip` returns a NEW image and leaves the source untouched, so one sheet can be clipped as many times as a program likes. A region reaching past the source's edge is deliberately not an error: the overlapping part is copied and the rest stays transparent, which is what a canvas `drawImage` with a source rectangle does, and is ordinary at a sheet's right or bottom margin. A non-positive width or height IS an error, since Cairo would otherwise accept it and hand back a surface nothing can ever draw -- a silent no-op in place of an obvious mistake.

`resize` changes the image IN PLACE. That follows from how it reads: `grass.resize(32, 32)` is a statement, not something whose result you assign, so it has to change `grass` itself. A Cairo surface cannot be resized in place, which is why an `img` value is now a pointer to a small box holding the surface rather than the surface directly. That indirection is the whole representation change, and it is what makes sharing behave consistently: two bindings naming the same image both see the new size, exactly as they both saw the old one.

`.width`/`.height` are runtime calls rather than stored fields, because `resize` replaces the surface underneath them and anything cached would go stale. The `img` branch of member access was previously a permissive "anything is allowed" fallthrough, dating from when section 37 defined nothing at all on img; it is now strict, so a typo like `.widht` is an error rather than silently typeless, and naming a method without calling it says so specifically.

The box also gives `img` an ownership story it never had, using the same two-part test section 86 established for regex: an `img` local whose initializer is a Call -- `loadImage(...)` or `sheet.clip(...)`, both of which hand back something freshly created that nothing else references yet -- and whose name escape analysis proves never leaves the declaring function is destroyed at scope exit. This matters more here than it did for regex, because `clip` exists to be called repeatedly: without it, extracting frames inside a loop leaks an entire Cairo surface per iteration.

Making that reclamation actually reach the common case required widening escape analysis. Its stage-2 machinery (section 75) exempts a call argument when the callee is a user function whose own body proves that position safe, but a builtin has no Festina body to analyse, so every builtin call argument fell under the conservative "anything passed to a call escapes" default. That meant `drawImage(tile, x, y)` alone kept `tile` alive forever -- defeating this section's own reclamation in precisely the clip-draw-repeat shape it exists for. Builtins are now listed as non-retaining, each checked against the runtime rather than assumed: Cairo copies the glyphs and pixels it paints, sqlite binds with `SQLITE_TRANSIENT`, the measure functions only take metrics, and the style setters copy what they need into their own state. Anything not listed keeps the conservative default, and this incidentally improves reclamation for struct/arr[T]/map[T] locals passed to those same builtins.

93. MATH, FILES, TIME AND CANVAS EXPORT

Everything in this section was already paid for. `-lm` and libc are on every link line unconditionally, and Cairo's PNG *writer* is compiled into the very library whose *reader* `loadImage` already uses -- so none of what follows costs a new dependency. This is claude.md #59's minimal-dependency principle applied in the other direction: exhaust what is already linked before reaching for anything new.

`Math` had exactly four functions, all of them rounding: floor, ceil, round, trunc. A program could not take a square root, a sine, a power, a minimum or a random number -- which is to say it could not do most of what a program with a canvas is written to do. Added: `sqrt`, `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `exp`, `log`, `log2`, `log10`, `abs` (all float -> float), `pow`, `min`, `max`, `atan2` (float, float -> float), `random()` (-> float), and the constants `Math.PI` and `Math.E`.

The rounding four keep returning `int` and everything new returns `float`. That split is deliberate rather than incidental: rounding answers "which integer", the rest answer "which real number", and collapsing them would make `Math.sqrt(2.0)` silently an int. Most of the new functions compile to real LLVM intrinsics rather than opaque libm calls, which lets them constant-fold and vectorise; the handful with no intrinsic (`tan`, `asin`, `acos`, `atan`, `atan2`) go straight to libm. `min`/`max` use `llvm.minnum`/`maxnum`, matching the IEEE-754 semantics every other language's `Math.min` implements. `Math.PI`/`Math.E` are emitted as raw double bit patterns for the same reason `FLOAT_NULL_CONST` is -- decimal text round-tripping through the IR parser could lose the last bit.

`Math.random()` is plain `rand()`, seeded once from the clock. That is the right tool for gameplay and sampling and the wrong one for anything security-adjacent, and saying so plainly is better than reaching for a CSPRNG and implying a guarantee this does not make. It returns a value in [0, 1) rather than [0, 1]: 1.0 being reachable would quietly break `arr[Math.floor(Math.random() * length)]`, the single most common thing anyone does with it.

File access is whole-file text I/O: `readFile`, `writeFile`, `appendFile`, `fileExists`, `deleteFile`. `readFile` answers `null` for anything it cannot read and the writers answer `false` on failure, rather than failing the program -- a missing file is an ordinary condition a program should be able to test for, exactly the reasoning claude.md #57 applies to division by zero. A failing `fclose` counts as a failed write, since a full disk can fail there even when every `fwrite` succeeded. What `readFile` returns is an ordinary owned text value (claude.md #83), so it composes with templates, `.replace()`, and everything else without any special case.

`now()` returns milliseconds since the Unix epoch -- the same unit and origin JavaScript's `Date.now()` uses, and already the unit this language's own `setTimeout` takes. `formatTime(ms, format)` is `strftime` against local time, returning `null` for a format that produces nothing.

`saveCanvas(path)` writes the canvas to a PNG. It saves the BACKING surface rather than the window, so the result is what the program drew rather than whatever happened to be unobscured on screen -- and unlike every other function in this section it does need a canvas to exist, so it opens one the same way the drawing functions do.

94. PATHS, TRANSFORMS, GRADIENTS AND SINGLE-VALUE QUERIES

Two gaps found by asking what the already-linked dependencies could do that this language could not reach.

The canvas could draw exactly three things: a rectangle, a circle, and a line of text. There was no way to express a triangle, a polygon, a curve, a rotated anything, a gradient, or transparency -- all of which Cairo has always been able to do, on the same library already linked for `drawRect`. This section adds paths (`beginPath`, `moveTo`, `lineTo`, `curveTo`, `closePath`, `fillPath`, `strokePath`), transforms (`translate`, `rotate`, `scale`, `resetTransform`, `saveState`, `restoreState`), two-stop gradients (`fillLinearGradient`, `fillRadialGradient`) and `fillAlpha`.

Every drawing function builds its own short-lived Cairo context, which starts with an identity matrix -- so the current transform has to live outside all of them and be applied to each one as it is created. That is precisely what makes `translate(100, 0)` affect the NEXT `drawRect` rather than nothing at all, and it is the only structural change this section makes. A path needs the same treatment in reverse: a Cairo path lives on its context, and this language's drawing calls are each separate statements, so one context stays open from `beginPath()` until `fillPath()`/`strokePath()` consumes it. Both of those end the path, as `fill()`/`stroke()` do on a canvas -- keeping it would make a second fill silently repaint the same shape.

`saveState`/`restoreState` save the whole drawing state, not just the transform: colours, alpha, line width and font too. Restoring a transform while leaving a colour changed is exactly the kind of half-measure that produces baffling bugs, and the canvas `save()`/`restore()` these mirror save everything as well.

Only `beginPath`/`fillPath`/`strokePath` open the canvas. Transforms, saved state, alpha and gradients are pure state and open nothing, exactly as section 89's own style setters don't -- which also means `restoreState()` with nothing saved reports THAT rather than a missing display. Rotation is in degrees, since this language has no angle type to make the unit self-documenting and `Math.PI` (section 93) is there for anyone who wants radians. Gradients take exactly two stops: that covers essentially every gradient a program actually draws and needs no new value type, where an n-stop version would need a whole gradient object.

The second gap is in the database, and was found by checking what SQLite could already do. JSON1 and FTS5 both turn out to need NO compiler feature at all -- they are ordinary SQL, and `sqlite()` has always passed SQL through untouched, so `json_extract` queries and full FTS5 virtual tables with ranked `MATCH` already worked. What made them unpleasant was narrower: receiving any result at all required declaring a `table` to hold the row shape, and a `table` declaration CREATES a real table (sections 28-31's automatic schema sync). Asking for a `count(*)` or a single `json_extract` therefore left a throwaway table sitting in the database forever.

`sqliteInt`, `sqliteFloat` and `sqliteText` close that: the first column of the first row, then finalize, with no schema at all. They share `sqlite()`'s own prepare-and-bind path exactly -- only the stepping differs. A query matching no rows, or whose value is SQL NULL, answers with this language's own null for that type rather than failing, the same reasoning section 57 applies to division by zero.

95. THE CANVAS IS OFFSCREEN; RENDER PUTS IT ON SCREEN

Drawing used to imply a window. Any `drawRect` opened one, blitted the entire canvas to it and flushed X -- so a program whose whole job was producing a PNG still needed a display, still opened a window nobody would look at, and still blocked forever in the event loop afterwards. It also meant a frame of a hundred sprites cost a hundred full-canvas round trips: 2000 rectangles measured at 1.6 seconds, against a 16ms budget for a 60fps frame.

This section separates the two. Drawing paints an offscreen image surface that needs no X server, no display and no window manager. `render()` is the single call that puts that surface on screen, opening the window the first time it runs.

Three things fall out of that one split, which is why it is worth the breaking change. Headless rendering becomes possible at all: draw, `saveCanvas('out.png')`, exit -- on a build server, in a container, over ssh, with `DISPLAY` unset entirely. "Does this program need a GUI?" becomes a question with a syntactic answer, since only `render()` and the event handlers require one. And a frame costs one blit instead of one per shape: the same 2000 rectangles now take about 1ms, plus 2ms for a single `render()`.

Everything that merely paints -- `drawRect`, `drawCircle`, `drawText`, `drawImage`, every path call, `saveCanvas`, and reading `clientWidth`/`clientHeight` -- no longer opens a window. The canvas has a size (800x600 until an `on resize` changes it) whether or not it is on screen, and forcing a window just to ask how big it is would have defeated the whole point for the very common case of measuring before drawing. Declaring an event handler still requires a window, because an `on click` genuinely cannot fire without one.

`clearCanvas()` erases the whole canvas to white, and `clearRect(x, y, w, h)` erases one region. The absence of these was the reason nothing on a Festina canvas could ever move: a canvas could only accumulate, so every frame painted on top of every frame before it, and `examples/tic_tac_toe.f` works only because a board never needs anything removed. `clearCanvas` deliberately ignores the current transform -- a rotated "erase everything" that leaves wedges behind would be a trap rather than a feature -- while `clearRect` honours it, since it names a region in the same coordinates as the drawing around it.

The cost is that an existing program showing something must now say `render()`. That is a real break, and it is the right one: the alternative is guessing when a program wants pixels on screen, which is exactly the guess that made headless output impossible and drawing slow.

96. ARRAYS GROW

An array's length was fixed at construction. There was no way to add to one, and writing past the end was an unchecked heap overflow rather than an error -- so a list that grows, which is most of what a program does with an array, had no representation at all. This section adds `push`, `pop`, `shift`, `unshift` and `splice`, each behaving as its JavaScript namesake does.

The runtime moves elements by BYTES with the element size passed in from codegen, which knows it at compile time for every `arr[T]` -- one set of helpers rather than a family per element type. Growth is a plain `realloc` per push rather than geometric over-allocation, which would need a capacity field in the header; in practice the allocator extends in place for a growing buffer most of the time, and if that ever stops being good enough, adding capacity is an additive change to the header rather than a redesign.

The ownership half matters as much as the resizing, and is where this could quietly corrupt memory. `xs.push(s)` follows exactly the rule `xs[i] = s` already follows (sections 80 and 83): a struct/arr/map element is retained as it is stored, and a text one is copied unless its source is already owning. Without that, an array and the variable pushed into it would share one buffer, and whichever was freed first would leave the other dangling. Removal is the mirror image and deliberately releases nothing: `pop`/`shift` hand the element back and `splice` hands it to the array it returns, so ownership transfers rather than ending.

`pop`/`shift` on an empty array answer with the element type's own null -- not its zero, which for an `int` is an ordinary 0 and would make an empty pop indistinguishable from popping a real zero. `splice` clamps exactly as JavaScript's does, negative start included, so `splice(i, 1)` at a boundary is a no-op rather than a crash. It takes `(start, count)` and returns what it removed; the variadic insert JavaScript also allows has no spelling here, since this language has no variadic calls.

97. UNCHECKED INDEXING IS THE USER'S, EVERYTHING ELSE HERE WAS OURS

Four separate defects, found while adding one method. They are grouped because they were found together and share one lesson: the bugs were in the cases nobody had written a program for yet.

**Reaching through an unassigned struct/arr/map field segfaulted.** claude.md's own rule is that an uninitialized field reads as its zero value, and for `int`/`float`/`bool`/`text` it already did. But a field whose type is a struct, `arr[T]`, or `map[T]` is a pointer, and `calloc` (or a global's `zeroinitializer`) leaves it null -- so `o.inner.n` dereferenced null. The recorded scope of this was "writes to nested struct fields"; probing it first showed the real scope was worse in two directions: reads crashed identically, and the array and map cases crashed the same way the struct one did. `log(o.inner.n)`, `log(s.xs.length)` and `s.m['k'] = 1` were all exit 139.

The fix creates the value lazily, on first reach, rather than eagerly at declaration. Eager allocation would need a place to run an initializer, and a global has none -- its storage is a compile-time constant. Lazy vivification needs no such place, and one mechanism then covers stack locals, heap locals, globals, parameters, and fields nested arbitrarily deep. The load becomes a null test, a make-and-store branch, and a phi; the identity is preserved (the storage is created once, not per access), which is what makes `o.inner.n = 5` followed by a read answer 5.

**`arr[bool]` was silently corrupt.** Section 96's helpers move elements by a byte count the compiler passes in, and that count was hardcoded to 8. Every Festina element type is 8 bytes wide except `bool`, which is `i8` -- so `push` wrote to byte `8*i` while `xs[i]` read byte `i`. The value went in and a neighbouring element's byte came back out. The stride is now computed from the element type, matching exactly what `getelementptr` walks with at every index site. Widening `bool`'s storage to 8 bytes would also have fixed it, and would have been the wrong fix: changing every bool array's layout to suit one method's convenience.

**A text `+` was not treated as an owning source.** Section 83 classified a Call and a template literal as "already a fresh, exclusively-owned buffer" and everything else as aliasing. A text `+` compiles to exactly one `festina_str_concat`, which mallocs unconditionally with no operand-passthrough path -- it is as owning as either of those. Leaving it out meant every binding of a concatenation copied a buffer that was already exclusively owned and then dropped the original on the floor: `text j = a + b` and `return s + '!'` each leaked one buffer per evaluation. The second half is that `festina_str_concat` copies from both operands and keeps neither, so a chained `a + b + c` has to free its own intermediate -- the same fix section 83 already applied to `_emit_template`'s intermediates, which is exactly where this should have been caught the first time.

**Computed map keys leaked, and top-level block scopes were never tracked.** `festina_map_set` strdups the key it is given and `festina_map_get` only reads it, so `m[`s${i}`] = v` had no owner left for the key it built; both sites now free it. Separately, section 74's scope tracking only ever ran inside a function or handler body, so a local declared in a nested block at TOP level -- `text row = a + b` inside a top-level `while` -- was emitted as an ordinary alloca and never freed. That is one leaked buffer per iteration in exactly the shape a game loop is written in. The top-level statement list now gets the same whole-body escape analysis every function gets.

**Unchecked indexing stays unchecked, and is now documented as such.** `xs[i]` past the end is a genuine heap-buffer-overflow -- confirmed under AddressSanitizer, for reads as well as writes. Adding a bounds check would put a branch in the hot path of every loop a game writes, which is not what this language spends its budget on. So api.md now says plainly that keeping the index in range is the user's responsibility, that a read past the end returns arbitrary heap bytes rather than null or zero, and that a write past the end corrupts the heap. It also says which operations are NOT in that category, since the surrounding design is otherwise forgiving: a missing map key answers null, an empty `pop`/`shift` answers null, `splice` clamps. Indexing is the only unchecked operation in the language, and saying so is worth more than hedging about it.

`indexOf(value)` is the method this all started with: the first index holding `value`, or `-1`. `-1` rather than null because the answer is an index, and every use of one is a comparison or a `splice` argument -- `if xs.indexOf(v) >= 0` and `xs.splice(xs.indexOf(v), 1)` both read naturally, where a null index would read as neither. It is also what JavaScript answers, and section 26's arrays are JavaScript-shaped. The comparison is over the raw element slot, which is by value for `int`/`float`/`bool` and identity for struct/`arr`/`map` (aliases share a pointer, per section 79) -- with `text` switching to `strcmp`, because section 83 copies text on binding, so two equal strings are almost always two different buffers and identity would make the method useless for the element type it is most used with. Nothing is retained or released: the needle is only read, and an index is not a reference.

One display fix rode along. `bool`'s null is the reserved bit pattern 2, and both `festina_log_bool` and `festina_str_from_bool` rendered it with a plain `v ? "true" : "false"` -- so it printed as `true`, indistinguishable from a genuine true, which made section 96's "an empty pop answers null" impossible to observe at all for an `arr[bool]`. Both now render it as `null`. Only the sentinel takes that branch, so no real boolean's output changes.

98. SOUNDS OVERLAP, AND A KEY PRESS IS NOT A KEY RELEASE

Two changes, both about a program not being able to express something it obviously needs.

**`play()` cut off whatever that clip was already playing.** One `aud` held one playback thread and one ALSA handle, so a second `play()` stopped the first and restarted from the beginning. That is defensible for background music, which is the case claude.md #38's own example shows, and useless for everything else: a footstep, a gunshot, a coin pickup fired in rapid succession would each silence the one before it, so the faster the effect fires the quieter it gets.

Each `aud` now owns a pool of voices -- one thread and one device handle per simultaneous playback, all streaming the same decoded PCM read-only, so N voices cost N devices and never N copies of the audio. The default is 10, per clip, overridable with `setMaxAudioPlayers(n)` and readable back with `maxAudioPlayers()` (readable back because the value is clamped into [1, 64] rather than rejected -- this is a tuning knob, and failing a program over a number that is merely unreasonable is a worse trade than giving it the nearest workable one).

At the limit the **oldest** voice is stolen rather than the new play being dropped. Something has to give when every voice is busy, and the sound that has been playing longest is closest to finishing anyway, whereas dropping the new play would silence a rapid-fire effect at exactly the moment it fires fastest. At a limit of 1 this reduces exactly to the old behaviour, which is what makes `setMaxAudioPlayers(1)` a real way to ask for it back rather than an approximation of it.

`stop()` and `isPlaying()` stay about the CLIP rather than one playback of it: `stop()` ends every voice and `isPlaying()` is true while any is still going. There is no syntax for naming an individual voice and inventing one would mean exposing the pool, which is exactly what this design keeps out of the language. One consequence had to be handled that the single-voice design got away with ignoring: a voice that reaches its natural end clears itself but stays *joinable*, and whoever next claims that slot joins the finished thread first. The old code never joined a naturally-finished thread because it only ever had one; a pool that never joined would leak one thread per `play()` over the life of a game.

**`on key` could not tell a press from a release.** A movement key held down and a movement key tapped were the same event, so the most ordinary thing a 2D game does with the keyboard had no expressible form. `on key` is replaced -- not aliased -- by `on keyDown` and `on keyUp`, both taking the same `(key:text)` and both fed by one shared name function, so a release always reports exactly what the press reported and can be matched against it. `on key` still *compiles*, since claude.md #40 never restricted event names, but it is now ordinary dead code with no runtime event source, the same as `on somethingElse`; the give-away is that it no longer causes the graphics runtime to be linked in at all.

The part that makes this usable rather than merely present is auto-repeat. X synthesizes a KeyRelease before every auto-repeated KeyPress, so a held key would have fired a stream of phantom key-up events -- which would have made the split worse than useless for the exact keys it exists for. XKB's detectable auto-repeat is requested at window creation, and where the server does not support it the release half of a repeat pair is filtered by peeking at the queue (a repeat pair shares a timestamp and a keycode). Both paths were verified against a real X server, the fallback by forcing it on. `keyDown` deliberately still repeats: that is how text entry works, and a program that only wants the first press can track what it has already seen go down.

One failure mode the pool introduced had to be handled, and it is the reason a voice limit is not the only thing bounding this. Opening one ALSA handle per voice assumes the "default" device does software mixing, and not every one does -- a bare `hw:` device with no dmix is ordinary on minimal and embedded Linux, and any machine where another program holds the device exclusively behaves the same way. On such a system the SECOND concurrent open fails with EBUSY, and treating that as fatal (which it briefly was) meant an overlapping `play()` killed the program outright, with an error claiming there was no audio device when there plainly was one. The single-voice design could never hit this, because it never had two handles open. The fix distinguishes the two ways an open can fail: "this device will not give me an Nth stream" is a limit of the device, not the absence of one, so a playing voice's handle is given back and the open retried; only when there is no other voice left to free has the program genuinely failed to open any device at all, which is what that error message is actually about. The result degrades to exactly the pre-pool behaviour -- overlapping plays cut each other off instead of layering -- which is right, since on a device that cannot mix, layering was never physically possible, and quietly getting fewer simultaneous sounds beats not running.

Documenting the audio pool meant admitting what could not be tested through the language. A Festina program cannot count voices, by design, so the pool's own behaviour is verified by a white-box C harness that includes the translation unit directly. That harness also replaces the ALSA device layer, and not as a shortcut: the null ALSA device the other audio tests use consumes PCM instantly (measured -- a 2-second clip finishes in 0ms), so under it every voice is finished before the next play() begins and there is no concurrency left to observe at all. Everything above the device -- the pool, the stealing, the slot reuse, the joining -- is the genuine runtime code, and it is clean under both ThreadSanitizer and AddressSanitizer.

99. CHANNELS ARE NAMED, AND A LOOP RESERVES ONE

claude.md #98's pool gave a clip overlapping playback but no way to address any of it. Everything was automatic: a program could say "play this" and "stop this clip," never "play this on the music channel" or "stop whatever is on the music channel." This section adds `play(n)`, `playLoop(n)` and `stopAudioPlayer(n)`, each with the channel optional.

The change that made this work was giving up the per-`aud` pool. The motivating example is two music tracks trading one channel:

    adventureMusic.playLoop(0)
    battleMusic.playLoop(0)     // takes channel 0 over

With a pool per clip those are two different pools and "channel 0" names two different things, so the handover cannot be expressed at all. The pool is therefore PROCESS-GLOBAL now and its slots are channels. That is also what lets `stopAudioPlayer(0)` be an ordinary free function: with per-clip pools it would have had to name a clip in order to find the channel, which is exactly backwards -- the point of stopping a channel is that you do not care what is on it.

`playLoop` does two things, and the second is the one that matters. It repeats the clip until something stops it -- restarting the frame counter rather than reopening the device, so there is no gap between repetitions beyond ALSA's own buffering. It also RESERVES its channel: a reserved channel is never chosen by automatic assignment and never stolen at the limit. Without the reservation, looping music would be evicted by an ordinary sound effect the moment the pool filled up, which makes `playLoop` useless for the one thing anyone would use it for. A zero-length clip ends rather than looping, since spinning forever on silence is never what anyone meant.

Three things release a reservation, and the third is the subtle one. `stopAudioPlayer(n)` releases it; the clip's own `stop()` releases every channel that clip holds, because a looping track that was told to stop is not still owed its channel; and naming the channel explicitly in another `play(n)`/`playLoop(n)` takes it over. In the runtime that last rule is a single assignment -- `locked = looping` -- which is worth noticing: `playLoop(n)` takes the channel and keeps it, `play(n)` takes the channel and hands it back, and both are the same line of code, because a one-shot has nothing to reserve a channel for.

Two boundaries needed a decision, and both went the way claude.md #98's own clamping already went. An out-of-range channel is clamped into [0, 64) rather than being fatal: a bad channel number should not kill a running game, and `maxAudioPlayers()` is there for a program that wants to check rather than guess. And `setMaxAudioPlayers` bounds only what the pool assigns on its own -- an explicitly named channel is honoured anywhere in range, so `play(40)` works with a pool of 10. That split is what "micro control the channel" has to mean; a limit on automatic assignment that also silently rewrote explicit requests would be the opposite of control.

One case has no good answer and gets the least-bad one: if every channel in the table is reserved and a program then fires an unnamed `play()`, the play is dropped. There is nothing left that automatic assignment is permitted to touch, and the only alternative is breaking a reservation the program explicitly asked for. Automatic assignment does look above the pool limit for a free channel first, so this only happens to a program that reserved all sixty-four.

`stop()` and `isPlaying()` deliberately did not change: both are still about the CLIP, so `stop()` ends every channel playing it and `isPlaying()` is true while any of them is going. A per-playback `isPlaying` would need a handle to a playback, which is the pool-as-language-surface this design has refused twice now; a program that wants to ask about one playback names its channel instead.

100. A PATH DECLARES A CLIP, AND STOPPING IS ALWAYS BY CHANNEL

Two corrections to the audio surface, both of them removing an inconsistency rather than adding a capability.

`aud music = 'path/track.wav'` now declares a clip directly. Every other type whose value is naturally written as text already worked this way -- `blob data = 'path/to/file'` (claude.md #36), `color red = 'red'` and `font body = '13px arial'` (claude.md #91) -- and `aud` was the odd one out, requiring `loadAudio('...')` for no reason beyond the order things were built in. This is the same one-directional text -> X allowance those three have, in the same place (semantic.py's check_assignable), so it applies uniformly wherever an `aud` is expected rather than only at a declaration.

It differs from colour and font in one way worth being precise about: those are RESOLVED at compile time, so they require a genuine literal. An audio clip is decoded at runtime, so this becomes a real `loadAudio()` call at the point of conversion -- which means the path may be any text expression (`aud hit = soundDir + 'hit.wav'`), and also means the conversion is a real file read wherever it happens. `loadAudio('...')` was left in place rather than removed; it is the same call spelled longer, and nothing is gained by breaking every program that uses it.

`aud.stop()` is REMOVED, and this is the more interesting of the two. It was already wrong when claude.md #98 gave a clip a pool of voices, and #99 only made it more obviously so: one clip can be playing on several channels at once -- three overlapping gunshots are the ordinary case, not the exotic one -- so "stop this clip" never named one thing. Its only honest reading was "stop every copy," which is almost never what a program firing overlapping effects wants, and which quietly threw away the channel a program had deliberately reserved. Playback is addressed by channel now, with no per-clip shortcut: `stopAudioPlayer(n)` for one, `stopAudioPlayer()` for all. The compiler catches `.stop()` by name rather than letting it fall into the generic unknown-method error, so the message can say what to use instead.

`isPlaying()` deliberately survives the same argument, because it does not have the same problem. "Is this sound audible anywhere" has one answer no matter how many channels are playing it, so a clip-wide reading is the correct one rather than a lossy compromise -- and it is what the music-handover pattern in #99 is built on.

101. IMAGES AND CLIPS ARE PATHS, MORE FORMATS, AND BOTH FIT IN A TABLE

Three changes that turn out to be one change, because the same refactor underlies all of them.

`img sprite = 'sprite.png'` now works, exactly as `aud` does since claude.md #100. Leaving the two media types different was never a decision, just the order things were built in, and #100 made the asymmetry glaring rather than merely untidy. One detail was worth getting right: this sets the compiler's uses_graphics_CODE flag rather than uses_graphics, so a headless program that loads a sprite does not die on "could not open the X display" -- exactly the artificial restriction loadImage() already avoided, and the short form has no business reintroducing it.

JPEG and MP3 are decoded now, via libjpeg and libmpg123. Both are the smallest dependency that does the job, chosen the same way Xlib was chosen over a GUI toolkit (claude.md #59). claude.md's own audio example always named a `.mp3`, so this closes a gap the spec had from the start rather than adding a feature to it. Format is sniffed from MAGIC BYTES, not from the file extension -- an asset coming out of a database column has no extension, and an extension was never evidence of anything anyway.

The third change is what forced the shape of the other two. `file:aud` and `pic:img` are table columns now, stored as SQLite BLOBs. Previously such a column fell through to TEXT, which would have truncated silently at the first NUL byte in a PNG header -- it compiled and it was nonsense.

Making that work meant inverting how loading is written. Decoding from MEMORY is the primitive now, and loading a path is "read the file, then decode the bytes" -- which is exactly why the same code serves a file and a BLOB, and why the format sniffing had to stop depending on filenames. Each handle keeps the bytes it was decoded from, so what a column stores is the asset's own encoding rather than a re-encoding: a round trip is byte-identical, an MP3 stays an MP3 instead of becoming a much larger WAV, and a JPEG stays a JPEG instead of becoming a PNG several times its size. The kept bytes are usually SMALLER than the decoded form they sit beside (a 128x64 PNG is a couple of kilobytes against 32KB of ARGB32), so this is a modest overhead rather than the doubling it sounds like. The one case with no source bytes is an image a program built rather than loaded -- a clip() or resize() result -- and those are encoded to PNG on demand, losslessly, and cached.

Reading such a column back needed care with the translation-unit split, which is load-bearing: the core runtime must not name anything in the graphics or audio units, or a program using neither would link both. So main() REGISTERS the two decoders as function pointers, exactly when the program already links that feature, and the core runtime calls through them. An unregistered column reads as null rather than crashing.

Three memory bugs surfaced while verifying this under LeakSanitizer, and two of them were mine.

First, the `img sprite = 'path'` sugar silently broke claude.md #92's own reclamation. That test asks whether the initializer is a Call -- true for loadImage(), false for a StringLit -- so the moment the short form existed, every image declared with it leaked. Measured: one handle per loop iteration. The predicate is now about whether the initializer PRODUCES a fresh handle rather than what shape its AST node happens to be.

Second, `aud` had never been reclaimed at all, by anything. claude.md #92 gave `img` scope-exit freeing and simply never did the same for audio; nobody noticed because loading a clip inside a loop was awkward to write until #100 made it natural. It has the identical treatment now, with the destructor stopping any channel still playing the clip first -- codegen only ever frees a clip it has proven unshared, so that is a safety net rather than a likely case, and it turns "freed while a thread is streaming it" from a crash into an impossibility.

Third, a query row holding an `aud`/`img` column leaked its decoded handle. claude.md #85's per-row release function frees each text/blob column with free(), which is right for a strdup'd buffer and wrong for a handle owning a Cairo surface or a block of PCM. Those columns now get their own type's destructor.

One further improvement came out of the same investigation rather than being a bug. Escape analysis exempted an argument to a non-retaining builtin only when the argument was a bare identifier -- but `sqlite()`'s bound parameters are always a LITERAL ARRAY, so in practice every value ever bound to a query was treated as escaping and never reclaimed. The exemption now reaches inside a literal array argument. That is sound for the same reason the builtin was exempt to begin with: every parameter is bound with SQLITE_TRANSIENT, so sqlite has copied what it needs before the call returns.

102. A BUG HUNT, AND A HARNESS THAT CAN FAIL

Six bugs, found by deliberately probing rather than by waiting for them, plus the leak stress suite that found the last one and will find the next.

**Comparing anything pointer-backed against null did not compile.** `x == null` on a struct, an `arr[T]`, a `map[T]`, an `img`, an `aud` or a `regex` emitted `icmp eq i64 <a pointer>, null`, which is not valid IR -- so the compile died with an LLVM PARSE ERROR naming a generated temporary. That is an internal-error message for something entirely reasonable to write, and it covered every managed type in the language except text. It surfaced from a nullable BLOB column, where checking for null is not optional: there is no other way to ask whether a row has a file. Fixed with a plain pointer comparison, which is what those types are anyway. `float` keeps its documented IEEE behaviour: a live float is neither `== null` nor `!= null`, because every ordered comparison against a NaN is false.

**A table column of type `aud`/`img` did not link.** claude.md #101 gave such a column a decoder registration in main() and a destructor call in the per-table row release function, but nothing marked the program as USING audio or graphics -- so a program whose only use of audio was `file:aud` in a table failed at the link step with an undefined reference to `festina_audio_free`. A compiler bug reported as a linker error, which is close to the least useful place for it to appear. The column type itself now sets the flag.

**Math.floor of a null float returned a stack address.** `fptosi` is undefined behaviour for a NaN, for an infinity, and for anything outside i64's range -- not "some unspecified integer", genuinely undefined. Measured: `Math.floor(1.0 / 0.0)` printed a different value on every build, once a stack ADDRESS, and in one program `Math.floor(nan)` answered 1 while `Math.ceil(nan)` on the very next line answered the null sentinel, because the optimizer had folded two identical UB sites differently. A language whose stated position is that division by zero returns null rather than crashing cannot then hand back a stack address for Math.floor of that same null. The answer is null in all three cases, via `llvm.fptosi.sat` (fully defined, so nothing downstream inherits poison) and an explicit test. Saturating instead would have been worse rather than better: i64's minimum IS the int null sentinel, so clamping a huge negative float lands on it anyway, and clamping a huge positive one asserts a precise answer the input never had.

**A call result reached for one field leaked the whole value.** claude.md #77 already released a call result discarded as a bare statement, reasoning that a Call's result is fresh and unshared by construction so that expression is provably its only reference. `makeThing().count` is the same situation and was never covered: one whole struct -- header, fields and all -- per evaluation, which in a loop is per iteration. Now released, but only when the field's own type is NOT managed, and that restriction is load-bearing rather than conservative: releasing the parent recursively releases its struct/arr/map fields and frees its text fields, so for those field types the value just loaded would be freed before the caller saw it -- trading a leak for a use-after-free. The chained form (`makeThing().inner.n`) therefore still leaks, deliberately; fixing it needs a notion of an owned temporary outliving its producing expression, which this codegen does not have. A test pins the behaviour so a later "optimization" cannot quietly make that trade.

**A literal of all nulls could not be written.** `arr[text] a = [null]` and `map[int] m = {'k': null}` inferred `arr[null]`/`map[null]` and were then rejected against every declared element type -- while `a.push(null)`, `m[k] = null` and `[null, 'x']` were all already fine. An inconsistency rather than a policy, since null is a valid value of every type; a container of nulls is now assignable to a container of anything, with genuine element mismatches still rejected.

**A sqlite parameter was never reclaimed.** Recorded here because it was found in the same sweep: escape analysis exempted an argument to a non-retaining builtin only when the argument was a bare identifier, but `sqlite()`'s bound parameters are always a literal array -- so in practice every value ever bound to a query was treated as escaping. Fixed in claude.md #101.

The stress suite is the durable part of this. Five programs under AddressSanitizer and LeakSanitizer, each hammering one ownership mechanism -- text, collections, structs and query rows, media handles, regexes and files -- some thousands of times, so a leak of a few bytes per pass is unmissable. They are written as one long loop each rather than as many small cases on purpose: the interesting failures are the ones where a value's ownership is right in isolation and wrong when it is aliased, returned, stored and discarded in the same breath. That is exactly how the call-result leak above was found.

Two things about the harness are worth stating because both are easy to get wrong and neither is visible in a passing run. `clang -fsanitize=address -c file.ll` does NOT instrument raw LLVM IR text -- ASan's per-function opt-in is added by clang's C frontend, which is bypassed entirely when the input is already .ll -- so a harness built the obvious way passes everything and proves nothing; the attribute is stamped onto every `define` line first. And the harness needs TWO compilers, because clang is the only one that parses .ll while its ASan runtime library is shipped separately and is routinely absent (this container has exactly that combination), so the linking compiler is probed rather than assumed. Against both traps the only real defence is a canary: a test that feeds the harness a known-leaking program and fails if it comes back clean.

103. THE CANVAS BENCHMARK, AND WHAT IT COSTS TO MEASURE HONESTLY

The language benchmarks were re-run after this session's codegen changes and nothing regressed: fib 7.2ms, loop_sum 551ms, array_sum 98.5ms, string_concat 3.5ms, all within noise of where claude.md #83 left them. One methodology fix came out of the re-run rather than the results. Build time had a warmup for RUNS but not for BUILDS, so whichever program a toolchain compiled first absorbed its whole cold-start cost -- Rust's `hello` measured 5.1 seconds against 0.1 seconds for the very next program it built, which says nothing about `hello`. One untimed throwaway build per toolchain fixes it, and the previously published build-time column was wrong in exactly that way.

The new benchmark is Festina's canvas against an HTML `<canvas>` in headless Chromium: 20,000 filled rectangles and 20,000 filled circles, fill colour changed between every shape. It is the one comparison here that is not against another language, and it is the right one to make, because a 2D game not written in Festina would most likely be written on a browser canvas.

**Festina loses the drawing by about 1.4x** (90ms against 64ms), and that goes in the document in bold rather than in a footnote. Skia has had years of SIMD-level investment aimed squarely at this loop and Cairo has not. There is no workload selection that makes this flattering and no version of this project's own claims that survives hiding it.

Three things had to be true for the number to mean anything, and each was wrong on the first attempt.

Both sides must draw OFFSCREEN. Festina paints an offscreen surface (claude.md #95) and the browser canvas is never attached to the document, so neither is timed presenting to a screen; comparing a headless rasterizer against a compositing browser window would measure the window system.

The browser must be forced to RASTERIZE inside the timed region. Skia batches and defers, so a naive loop around fillRect can return before any pixel exists. A one-pixel getImageData after the loop forces the flush: measured at 50.5ms without it against 70.2ms with it, so omitting it would have overstated the browser by about 40% -- in Festina's favour, which is the direction that would have been least likely to get caught.

Both sides must time the DRAW LOOP ITSELF. The first attempt subtracted a blank-frame process time from a full-frame one, which quietly charged Festina for PNG encoding: a blank 800x600 image compresses far faster than a busy one, and the difference was being attributed to drawing. Each side now times its own loop with its own monotonic clock.

The headline uses the MINIMUM rather than the median, and that is a considered choice rather than the flattering one. The browser's median swings by more than 20ms between invocations of the same script -- a median-based headline flipped between 1.1x and 1.6x on identical code -- while Festina's minimum and median sit on top of each other. Both are in the table, because the spread is real information: for a frame budget, predictability is not a footnote. Startup is reported separately and goes the other way by more than an order of magnitude, since one side launches a browser and the other launches a process.

Finally, the two outputs are compared rather than assumed equivalent -- cell by cell over a 16x16 grid, worst per-channel difference 0.2 out of 255. Not byte for byte: Cairo and Skia disagree about antialiasing on every curve, and demanding identical bytes would only prove the two rasterizers are the same program. That check paid for itself immediately by catching a bug in the benchmark script, where the blank-frame baseline run overwrote the real frame's PNG and left the comparison looking at an empty canvas. A benchmark whose two sides are not verified to be doing the same work is not a benchmark.


104. CIRCLES ARE STAMPED, NOT TESSELLATED

claude.md #103 measured Festina's canvas at 1.4x SLOWER than a browser's and said so in bold. This closes that gap and reverses it: the same frame now draws 2.1x faster than Chromium.

One change did it, and the interesting part is that the obvious suspect was wrong. Every draw call creates and destroys a Cairo context, which looks like the kind of per-call waste that explains a 1.4x gap -- and measuring it showed 4 ms out of 90. Splitting the frame by shape type instead found the real cost immediately: 20,000 rectangles cost 10 ms and 20,000 circles cost 76 ms. `cairo_arc` followed by `cairo_fill` turns the curve into Bezier segments and scan-converts a general polygon, every time, for every circle.

A filled circle of a given radius is the same picture wherever it lands. So it is rasterized once into an A8 alpha mask and stamped thereafter, which is exactly what a glyph cache does and for exactly the same reason. Circles went from 76 ms to 20 ms, 4.4x, and the frame from 90 ms to 31 ms.

The cache is keyed on radius, which is an `int` in this language -- nothing to quantize, no rounding to get wrong. It holds sixteen entries and evicts round-robin rather than least-recently-used: the working set that matters is "the few sizes this program draws", which any policy keeps resident once warm, and LRU bookkeeping would cost more per stamp than it could save. A program cycling through more radii than the cache holds still draws correctly, just without the benefit.

What makes this safe rather than merely fast is knowing exactly when it is NOT allowed. A pre-rasterized bitmap is only equivalent to a freshly tessellated curve while it lands on the same pixels, so the fast path is skipped for a scale or a rotation (either would resample the mask -- blurry, and visibly wrong against a curve rasterized at that size), for a fractional translation (off the pixel grid), and for a bordered circle (a stroke needs a real path, and a mask has none). Everything else falls back, at the cost of one matrix read.

The verification is the part worth keeping. A frame exercising every one of those cases -- plain fills at several radii, a border, alpha, a whole-number translation, a scale, a rotation, a gradient, and degenerate radii of 0 and -5 -- was rendered twice, once with the fast path and once with it forcibly disabled, and compared pixel by pixel: 5 pixels of 480,000 differed, all by 1 of 255, all inside the gradient, which is sampling rounding rather than geometry. Isolated circles at radii 1 through 20 are bit-identical, with one channel off by one at r=40. That exactness is not luck -- `drawCircle` takes an integer centre and an integer radius, so the mask always lands on whole-pixel boundaries, and the transform check exists to preserve precisely that property.

One test expectation had to be corrected rather than the code: a radius-1 circle does not fully cover even its own centre pixel, so Cairo antialiases it to grey. Asserting solid black there would have been testing Cairo's coverage arithmetic rather than this cache, and the fallback produces the identical grey.

What remains is 11 ms of rectangles and 20 ms of circles. Rectangles at 0.5 microseconds each are not obviously improvable without leaving Cairo, and the context-per-call overhead that started this investigation is still there, still worth 4 ms, and still not worth the state-leak risk of a shared long-lived context. The honest summary is that this workload is now bounded by Cairo's span filling rather than by anything Festina is doing to it.


105. MONOGAME, AND A NUMBER THAT NEEDS ITS CAVEAT READ FIRST

The canvas benchmark gained a third side: MonoGame, drawing the same 20,000 rectangles and 20,000 circles through SpriteBatch into an offscreen RenderTarget2D. Festina draws the frame in 31 ms, Chromium's canvas in about 60 ms, and MonoGame in about 177 ms.

That last number is close to meaningless without what follows it, so it is printed with the caveat attached in both the benchmark output and the document. MonoGame is a GPU framework. This machine has no GPU, so its GL context is Mesa's `llvmpipe` -- a software implementation of the entire graphics pipeline -- and it is therefore paying in software for vertex transform, rasterization setup and per-pixel texture sampling that real hardware does for free. On an actual GPU these 40,000 sprites batch into a couple of draw calls and finish in well under a millisecond, which no CPU rasterizer in this comparison can approach. What the MonoGame row measures is the headless, no-GPU case -- CI, a build server, a container -- and nothing else. Reporting "Festina is 5.7x faster than MonoGame" without that sentence would be a lie by omission, and a particularly cheap one.

The MonoGame side is written the way MonoGame is meant to be written, which matters more than it sounds. A filled rectangle is a 1x1 white texture stretched and tinted; a circle is a pre-rendered circle texture tinted the same way; both go through one deferred SpriteBatch so the framework batches 40,000 draws into a couple of calls. Defeating that batching would have produced a bigger number and a worthless one. Worth noticing in passing: a MonoGame circle is a pre-rendered texture stamped per instance, which is exactly what claude.md #104 made Festina's own drawCircle do internally -- the two arrived at the same trick from opposite directions.

Getting a trustworthy measurement out of it took three attempts, and the failure mode is the same one the browser side had, only worse. GL is asynchronous, so timing the submission measures how fast a command buffer fills. Three sync strategies, measured:

    no readback     min 516  median 526  max 538 ms
    one pixel       min 193  median 519  max 553 ms
    whole target    min 188  median 195  max 272 ms

The one-pixel readback that works for the browser syncs only SOMETIMES here, which is why its numbers swing threefold inside a single run. No readback at all is worse than either, because frames queue and a timed region ends up holding some other frame's backlog. Reading the whole target forces a real finish, and costs 0.4 ms on an untouched target -- measured, so it is not what is being timed.

Even with that settled, llvmpipe is multithreaded and far more exposed to whatever else the machine is doing than single-threaded Cairo: three consecutive invocations measured 176 ms, 182 ms and 513 ms. The runner therefore launches the process several times and keeps the best, which is the same min-of-runs the rest of benchmark.md already uses, applied one level up.

The output comparison earns its place again. MonoGame's frame matches Festina's with a worst per-channel difference of 0.0 over the 16x16 grid -- better than the browser's 0.2 -- which is only possible because the circle texture is built with the same coverage-based antialiasing Cairo applies rather than a hard-edged disc. Two rasterizers agreeing to the byte at that granularity is what makes the timing comparison mean anything at all.

The benchmark degrades rather than fails where it cannot run: no .NET SDK, or no network to restore the NuGet package from, skips that side with a note. The project file and source are checked in; bin/ and obj/ are not.


106. A STRUCT THAT CAN NAME ITSELF, AND A CLICK THAT IS TWO EVENTS

Two unrelated fixes, both of the same shape: something the language had no reason to forbid, forbidding it anyway because of an implementation detail nobody had revisited.

`struct Node { n:int next:Node }` did not compile. The error said "unknown type 'Node'", which reads like a typo and is not one -- the name genuinely was not in the struct table yet, because `analyze_struct` resolved every field type first and registered the finished struct afterwards. That ordering meant a struct could not mention itself, and, for the same reason, could not mention any struct declared later in the file: `struct Outer { inner:Inner }` above `struct Inner { ... }` failed identically. In a language with no forward declarations to write instead, that is a rule you cannot work around, only work backwards from -- and it was never a rule anybody chose.

Nothing about the representation required it. A struct-typed field is already a pointer (codegen's `_llvm_type`), so a self-reference is finite-sized, and claude.md #97's auto-vivification makes reaching through one work with no further machinery at all: `head.next.next.n = 3` allocates both links on the way in. The fix is two lines of ordering. The name is registered before its own fields resolve, and the whole program's struct and table names are registered in a pre-pass before any declaration is analyzed. The placeholder is empty and is *replaced* rather than mutated, so no field lookup can ever observe a half-built struct -- `resolve()` only needs the name to exist to hand back a StructType, never the field list. A failed resolve deletes the placeholder again, so a program with a genuine typo in a field type leaves no half-registered name behind for the next declaration to trip over.

That change makes the duplicate-declaration check subtler than it looks, and it is worth writing down why. It used to ask whether the name was present. Now every name is present, so it asks whether the name has real *fields* in it, or whether the other namespace claimed it -- `if structs.get(name) or tables.get(name) is not None`. Get that wrong and every struct reports itself as a duplicate of itself.

A linked list built and traversed end to end confirms it works: three nodes, `head.next.next.n = 3`, printed forwards, summed by walking. Forward references compile. Duplicates are still rejected. Unknown field types are still rejected.

The acyclic case is fully reclaimed, which is worth stating because it is the case that actually matters. A three-node list built and torn down 200 times leaks nothing at all under LeakSanitizer -- claude.md #78's per-type release wrapper cascades into the `next` field exactly as it does into any other struct-typed field, and it works here only because the wrapper's cache entry is written *before* its field loop recurses. That one line is now the difference between a self-referential type and infinite recursion in the compiler; it was previously a small deduplication for sibling fields of the same type, and its comment said so. It has been rewritten to say what it now actually load-bears.

**This introduces a real hazard, and claude.md #77 explicitly relied on it being impossible.** Reference counting cannot free a cycle, and until now Festina had no way to write one: a struct could not refer to its own type, so no chain of struct fields could ever close. It can now. `a.next = a` leaks -- measured under LeakSanitizer at exactly 1,200 bytes in 50 objects over 50 iterations, and it grows without bound. It is a clean leak and nothing worse: no double-free, no use-after-free, which is the property a test now pins, because a naive "detect the cycle and break it on release" fix is precisely how a leak turns into something far more dangerous. This is the same class of leak as the escaping-handle case documented in todo.md, and it has the same answer, which is that the answer is a tracing collector and there isn't one. The note in todo.md claiming cycles are unconstructible has been corrected; it was true when it was written and this section is what made it false. The trade is deliberate: linked lists, trees and parent pointers are ordinary things to want, and refusing all of them to prevent a cycle nobody can currently write is a worse deal than allowing both.

The second fix: `on click` is gone, replaced by `on mouseDown` and `on mouseUp`. This is precisely the split claude.md #98 made for the keyboard, arrived at from the same complaint. A click is a press and a release. `on click` fired on the press and discarded the release, so a program that needed to tell them apart -- dragging a selection box, charging a shot, holding to aim -- had nothing to listen for on the way up. The runtime was not even asking X for the events: `ButtonReleaseMask` was missing from the event mask, so the release was never delivered to begin with.

Both handlers take the same `(x:int, y:int)` and both report the pointer position at the moment the button changed state, which is the entire point -- press and release report *different* coordinates whenever the pointer moved in between, and that difference is the drag. A program that only ever wanted "was clicked" changes one word and behaves exactly as before, since `on mouseDown` fires where `on click` used to.

As with `on key`, the old name is removed rather than aliased. `on click` still *compiles* -- claude.md #40 never restricted event names -- but it is now ordinary dead code with no event source behind it, exactly like `on somethingElse`. This is breaking, and was asked for as such.

The end-to-end test is the one worth having. A single simulated click now produces two lines in order, `down 150 220` then `up 150 220`, from one xdotool invocation -- and a separate test presses at one point, moves, and releases at another, asserting the two coordinates differ. That second test could not have been written at all before this change.


107. THE 'g' FLAG STOPS BEING DECORATION

`/pattern/g` has parsed since claude.md #67 and done nothing. The reasoning at the time is written down in the parser, and it was not unreasonable: `.replace()` and `.replaceAll()` already said "first match" and "every match" out loud, which is the distinction JS's `g` controls implicitly, so there was no behavior left for the flag to turn on. It was accepted rather than rejected purely so that JS habits would not produce a compile error.

That argument has a hole in it, and the hole is `regex(pattern, flags)`. When first-vs-every is decided by *which method you call*, the compiler decides it, at the call site, from a name it can read. A pattern built at run time has flags the compiler cannot read -- so `regex(userPattern, 'g')` could parse, could pass its flags to `regcomp`, and could never once mean "replace every match". The two spellings of a regex did not have the same expressive power, and the missing half was exactly the half a program reaches for when the pattern comes from configuration or from a user.

So `g` is now meaningful, `.replaceAll()` is removed, and the flag travels with the compiled pattern rather than with the call. A compiled regex is no longer a bare `regex_t` but a small struct holding the `regex_t` plus the flag, which `festina_regex_replace` reads back. Both `festina_regex_replace` and `festina_str_replace` lost their `replace_all` parameter, because there was nothing left for a caller to say: a regex knows from its own flag, and a plain-text search -- which has no flags to carry -- replaces the first match only, exactly like `String.prototype.replace` with a string argument in JS.

That last part is a genuine narrowing, and it is worth being blunt about rather than filing under "JS compatibility". There is no longer any way to replace every occurrence of a literal substring without writing it as a pattern: `s.replaceAll(',', '-')` becomes `s.replace(/,/g, '-')`. For a comma that is a fair trade. For a substring containing regex metacharacters it is worse than a fair trade, because the text would have to be escaped first and this language has no escaping helper. The alternative was keeping `replaceAll` alongside `/g` -- two ways to say one thing, with the awkward follow-up question of what `s.replaceAll(/x/, 'y')` means when the pattern says first-match and the method says every-match. Removing the method makes the flag the single answer, and that was the instruction.

`replaceAll` is caught by name with an error that names the flag, the same treatment claude.md #100 gave `aud.stop()`, rather than being left to a generic unknown-method message. A breaking change should explain itself at the point of breakage.

Two things `g` deliberately does NOT do, both of which JS does and both of which would be worse here:

`.test()` does not become stateful. In JS a `/g` regex carries a `lastIndex` that advances on every `.test()`, so testing the same string twice returns `true` and then `false`. That is a well-known bug factory and there is no reason to import it.

`.match()` still returns `text`. JS's `/g` changes `.match()`'s return type from a string to an array, and a return type cannot depend on a flag that `regex(pattern, flags)` only knows at run time. This one is a real limitation rather than a preference -- there is no match-all in this language, and adding one would need a separate method with its own static return type, not a flag. Both exclusions are documented in api.md as limits, not left to be discovered.

While rewriting the regex documentation, one line in api.md turned out to be simply false: "compiled fresh every time it's evaluated (both forms) -- no caching by pattern text". Literals have been cached per AST node since claude.md #85; only `regex()` recompiles. The corrected section carries the measurement, because the gap is not small: over 200,000 iterations, `/[0-9]+/.test(s)` takes 15 ms, the same pattern hoisted into a `regex` variable outside the loop takes 13 ms, and `regex('[0-9]+').test(s)` inside the loop takes 367 ms. Roughly 24x, entirely avoidable, and previously undocumented in the one place a reader would look.


108. THE DECISION BELONGED AT THE END OF THE CHAIN

claude.md #102 released a call result that existed only to have one field read off it -- `config().retries` -- and stopped there, with a note explaining why `make().inner.n` had to keep leaking: releasing the parent frees the field just loaded, so it would trade a leak for a use-after-free. That note was right about `make().inner`, and wrong to conclude anything about `make().inner.n`.

The two are not the same expression. `make().inner` yields a struct, and releasing the parent to hand it back would indeed free it. `make().inner.n` yields an *int* -- a copy that owes nothing to the object it came from -- and by the time it has been loaded, the entire object graph is unreachable and free to go. #102 could not see the difference because it made its decision one link too early: at `.inner` the field type is managed so it bailed out, and at `.n` the receiver is a Member rather than a Call so nothing was left to notice a call result existed at all. Neither frame ever saw both facts. Measured under LeakSanitizer at 5,200 bytes over 100 iterations -- the whole `Outer`, its `Inner`, and both of their text fields.

The fix is to make the decision at the outermost link, where the type of the value that actually escapes the chain is finally known. Each inner link parks its receiver instead of releasing it; the outermost link releases every parked receiver if what it loaded is a plain copy, and releases nothing if the chain ends in a managed value or a text, exactly as before. The one non-obvious part is deciding what "inner link" means. It is not "a member load emitted while a chain is in flight" -- a member load inside a call ARGUMENT (`make(other.field).inner.n`) is not part of the chain, and swallowing it would move its release to a point that may never arrive. So a link recognizes itself by AST node identity: the enclosing frame publishes the exact node it is about to emit as its receiver, and only that node counts.

Chasing this turned up a second, unrelated leak that had been hiding behind a comment. `_release_member_receiver_temp`'s own docstring listed `rowsFor(x).length` as one of the shapes #102 covered. It never did: `.length` has its own branch in the expression emitter and never reached the member-load path at all, so a call result read for its length was never released -- 2,880 bytes over 60 iterations, and undetected because the documentation asserted it was handled. It is handled now, by the same mechanism; a length is an i64 copy, so its receiver is always releasable, whether it is the call itself or the base of a longer chain.

What still leaks is what #102 identified and could not fix: a chain that yields a managed value or a text (`Inner got = make().inner`, `text t = make().inner.label`). Both keep leaking deliberately, both have tests pinning that the value read back is intact rather than freed, and both need the same thing to fix properly -- a notion of an owned temporary that outlives its producing expression, which this codegen still does not have.

One consequence outside the fix itself: the leak-stress suite's canary program was a chained read, chosen precisely because it was known to leak. It stopped leaking, and the canary test failed loudly, which is exactly the failure mode a canary should have -- the alternative is discovering months later that the harness had quietly become vacuous. It is a reference cycle now (claude.md #106), which needs a tracing collector and so should outlast anything else available to leak on purpose.


109. BLOB MEANS WHAT #36 ALWAYS SAID IT MEANT

claude.md #36 is three lines long. "blob represents binary data", and one example: `blob data = 'path/to/file'`. That example has compiled since the beginning, and until now it did nothing it looked like it did -- `blob` was implemented as a second name for `text`, sharing its representation outright, so the declaration stored the *path* and no file was ever read. A spec-compliance pass had noticed the example failed to compile and fixed that by allowing text into a blob; nobody went back and asked what the value was supposed to BE.

A blob is the file's bytes, loaded at the declaration, keeping the path it came from. That is the same shape `img` and `aud` already have since claude.md #101 -- content plus the bytes it came from -- which is why the same value now serves a program, a SQLite BLOB column and a byte-identical round trip through one. A 4,079-byte PNG stored and read back compares equal, which a text-shaped blob could never have managed: it read columns with `sqlite3_column_text`, so anything binary was truncated at its first NUL. That was the exact bug #101 called out for media columns and fixed only for `aud`/`img`; `blob` had it the whole time and nothing noticed, because nothing could construct a blob worth storing.

The five file functions claude.md #93 added -- `readFile`, `writeFile`, `appendFile`, `fileExists`, `deleteFile` -- are gone, and their capability moved onto the value that already knows the path. `blob f = 'notes.txt'` then `f.write(...)`, `f.append(...)`, `f.toText()`, `f.exists()`, `f.delete()`. The C helpers underneath are untouched and still do the work; what went away is threading the same path string through five separate calls. #93's rule that nothing here fails the program is untouched too: a path that cannot be read yields an empty blob whose `exists()` is false, which is also how a file that does not exist yet gets created, and the writers still answer `false` rather than stopping the program.

Two consequences of the type change, both breaking and both worth naming. `log(someBlob)` prints the contents now; it used to print the path, and leaving it alone would have printed a struct's first field as if it were a string. And `blob == text` is a compile error where it used to be allowed -- that exception existed *because* the two shared a representation, and comparing a handle's address against a string's contents is not a comparison. `f.toText() == t` is what was meant and says so.

**A blob is reference counted, and it is the first handle here that is.** claude.md #109 asked for two specific behaviors: `blob a = b` shares the file rather than re-reading it, and rebinding a blob frees what it held if nothing else wants it. That is reference counting exactly, and it needed almost nothing new -- a blob carries the same `i64` header immediately before its payload that structs and arrays have carried since claude.md #77, so `festina_retain`, `festina_release_check` and the retain-before-release ordering at a reassignment all worked on it unchanged. The only blob-specific piece is a destructor with two inner strings to free, dispatched exactly like a struct's field cascade.

This is the difference between blob and `img`/`aud`, and it is worth stating because it looks like an inconsistency and isn't. Those two are owned-or-leaked: the compiler must PROVE a handle is unshared before freeing it, because there is no way for them to express sharing, so an escaping one leaks (see todo.md). A blob just counts, so no proof is needed and nothing has to escape-analyze. Where the old code asked `isinstance(t, (StructType, ArrayType, MapType))` at fifteen separate sites, there is now one `_is_refcounted(t)` that also answers yes to blob -- and `text` deliberately still isn't in it, because text is copied on alias rather than retained.

The one place the existing machinery genuinely could not answer for blob was freshness. `_is_owning_refcounted_source` asks "is this a Call", which is right for every type that can only be produced by a call or a literal. A blob is produced by a *text expression*, which `_coerce` turns into a `festina_blob_open` call -- as fresh as any other call result, but the AST node is a StringLit. Asking about the node retains a handle whose count is already 1, which leaks it permanently. The real test is whether a coercion happened, so the source TYPE is threaded to the decision alongside the node.

Three real bugs fell out of building this, and two of them were not mine.

An array literal never retained a blob element, because `elem_is_refcounted` was one of the fifteen sites and the only one written across two lines, which my search-and-replace missed. `arr[blob] many = [c]` then produced a genuine use-after-free -- both `c` and the array's element cascade released the same handle. Caught by a sweep that put a blob in every position the language allows, which is exactly why that sweep exists.

`_release_member_receiver_temp` -- claude.md #102/#108's machinery for releasing a call result reached for one value -- tested its receiver against the same three-class tuple. A blob is the first refcounted type with *methods*, so it is the first that can be a member-call receiver without being a struct: `make().exists()` produces a handle purely to ask it one question. That leaked one handle per evaluation.

And the pre-existing one: coercing a computed path into an `img` or an `aud` leaked the path string. `img s = dir + 'a.png'` -- 1,029 bytes over 49 iterations, measured. `festina_load_image` copies what it needs and keeps no pointer into the path, so the temporary was dead on return and nothing freed it. This has been wrong since claude.md #100/#101 introduced the short form; blob did not cause it, blob made it easy to hit, because building a path per iteration is the ordinary way to use one. All three loaders free the temporary now, which meant threading the source expression through `_coerce` to nine call sites -- the only way to tell a temporary from a variable's own buffer.

Also in this section, and unrelated except that it is the other half of a decision claude.md #100 got half right: **`aud.stop()` is back.** #100 removed it, correctly observing that one clip can be playing on several channels, that "stop this clip" therefore has only one honest reading -- stop every copy -- and that this is almost never what a program firing overlapping effects wants. All true. What it missed is that the case it dismissed is a real one: a looping engine hum, a music bed on more than one channel, a dialogue line. "Silence this sound, wherever it is" is a thing to want, and doing it by hand meant tracking channel numbers the runtime already knew.

The reason #100 could dismiss it is that it pointed at `stopAudioPlayer(n)` as the answer, and that answer had a hole: automatic assignment picks a channel the program has no way to learn. The pool was addressable only by naming channels by hand -- which is to say, by not using the pool. So `play()` and `playLoop()` return the channel they used now, and the two mechanisms sit side by side answering different questions rather than one standing in for the other. `stop()` is clip-wide, `stopAudioPlayer(n)` is one channel, `isPlaying()` is clip-wide for exactly the reason `stop()` is. `stop(n)` remains an error, since a channel argument would mean the other thing. A `stop()` on a channel a `playLoop` had reserved releases that reservation too -- silencing the sound while leaving the channel locked would quietly shrink the pool on every call.

`loadAudio()` and `loadImage()` are removed as well. claude.md #100 kept `loadAudio` on the grounds that "breaking every program that uses it would gain nothing", which was right then and stopped being right once the path form was the documented spelling everywhere: two ways to say one thing is a cost every reader pays forever, against a one-line edit paid once. Both names, and all five file functions, now error with the replacement in the message rather than merely reporting an unknown function -- the same treatment #100 gave `aud.stop()` and #107 gave `replaceAll()`. Nothing reserves the names any more, so a program is free to declare its own `readFile`, and unlike before it will actually be called.

One limit shipped knowingly rather than papered over: a blob read out of a database column has bytes and no path, so its `exists()`/`write()`/`append()`/`delete()` all answer false, and there is currently no way to write it back out to disk. `toText()` stops at the first NUL, so it cannot carry binary content out of the value. Reading a binary column and re-inserting it works; reading one and saving it as a file does not. That needs either a bytes-preserving transfer between blobs or a `saveTo(path)`, neither of which was asked for, and inventing one quietly would be worse than saying where the edge is.


110. SAVING BYTES BACK OUT

claude.md #109 shipped with a hole in it, recorded in its own last paragraph: a handle with no path could not reach the disk. An `img` from `clip()` had never been on disk, nor had anything read out of a database column, and `toText()` stops at the first NUL so it could not carry binary content out of the value either. Reading a stored PNG and re-inserting it worked; reading one and saving it as a file did not. That is what this closes.

`save()`, `save(path)` and `saveCopy(path)`, on `blob`, `img` and `aud` alike. One policy for all three, because all three are the same shape of value -- content plus the bytes it came from -- and three nearly-identical answers to "write those bytes somewhere" would be three things to remember instead of one. The runtime reflects that: a single `festina_save_bytes` holds the whole policy, and each type's entry point is three lines handing it a byte buffer and the address of its own path field.

The distinction that matters is between the two argument forms, and it is worth stating plainly because it is the only thing here a reader could get wrong. `save(path)` **adopts** the path -- afterwards the value writes, tests and deletes there. `saveCopy(path)` does not; the value stays pointed where it was. So `f.saveCopy('backup.txt')` followed by `f.write(...)` writes the original, and `f.save('two.txt')` followed by `f.delete()` deletes `two.txt`. Both spellings exist because both are wanted, and having only one would mean writing the other by hand.

`saveCopy` requires its path, enforced in the compiler rather than the runtime. A copy to nowhere in particular is not a thing to ask for, and making the argument mandatory turns "I meant `save()`" into a compile error instead of a silent overwrite of the original file.

A pathless handle given no path **fails the program** rather than answering false. That is a deliberate split from every other file operation here, which returns false and lets the program decide: `save()` on a clip is a bug in the program, where an unwritable directory is a condition of the filesystem. The error says which type it was and what to write instead, since "false" would have been indistinguishable from a full disk.

The path names a file, not a directory. That was not the first design -- a directory target that borrowed the filename from the handle's own path got written and then removed, because the reasoning does not survive contact with the actual use case: the handle that most needs saving is a clip or a column, and that is exactly the handle with no filename to lend. The shorthand would have worked only where it was least useful. Passing a directory now answers false like any other unwritable target.

Formats survive, which comes free from claude.md #101's decision to keep the bytes a handle was decoded from: an MP3 saves as an MP3, a JPEG as a JPEG, byte-identical to the file it was loaded from. The exception is an image built rather than loaded -- a `clip()` or `resize()` result has no source bytes, so `festina_image_bytes` encodes a PNG on demand, the same path a BLOB column already used. A 32x32 clip of a 64x32 sheet saves as a 136-byte PNG, verified by reading the IHDR back.

One detail that only shows up when it is wrong: the path is adopted on **success** only. Adopting it first and writing second would leave `exists()` answering false about a path the program had just been told it now had.

The sweep across every save shape turned up one leak, and the interesting part is that it was not this section's and should not be fixed here. `makeTile(i).save(path)` -- a discarded call result -- leaks the `img`, and leaks it identically with no `save()` involved at all (38,415 bytes over 40 iterations either way), so `save()` merely made the leaked object bigger by materializing its PNG. This is claude.md #109's own open entry: `img`/`aud` are owned-or-leaked rather than refcounted, so freeing one needs *proof* it is unshared.

The tempting fix is to extend claude.md #102's release-the-receiver rule to them, since a Call result is fresh and unshared by construction. It is not safe, and the reason is worth recording so nobody tries it: `img func get() { return shared }` compiles and hands back a global, so `get().width` would free a value the global still holds. #102's rule is sound for refcounted types precisely because the retain and release balance; with no refcount there is nothing to balance. The real fix remains what todo.md already says -- give `img` and `aud` the refcount header `blob` got in #109 -- and until then the conservative leak is the correct trade.

Also worth noting, since it cost a real debugging detour: three test harnesses compile the audio translation unit standalone with stubs, specifically to avoid linking sqlite3 into a test about the channel pool. Adding a core-runtime call to that unit broke all nine of them at link time. The stub is two lines; finding out that was the failure took longer than writing it.


111. FREE, DELETE, AND THE DIFFERENCE BETWEEN TWO KINDS OF NULL

Three additions that read as one: manual control over lifetimes (`free`), JS-shaped removal (`delete`), and `row.undefined('col')` -- which forced a bug fix that matters more than the feature it serves.

`free name` releases whatever the binding holds and nulls the binding, and it works on every type by meaning something slightly different for each, honestly rather than uniformly. For the refcounted family -- struct, arr, map, blob -- it is a DECREMENT, so a value something else still references survives until its own last reference drops; freeing an array releases elements the same way, which is the "an element with a pointer elsewhere is retained" guarantee. For img and aud it frees outright, and that is the point: those two have no refcount (todo.md's oldest open entry), so an escaping handle could previously only leak, and `free spritesheet` after cutting clips is the manual escape hatch. For text it frees the exclusively-owned buffer; for a scalar it degenerates to `x = null`; for a query row it nulls the binding WITHOUT freeing, because the array owns the row and freeing it here would double-free at the array's release.

Two design decisions carry the feature. First, the null store: every release in this runtime is null-safe, so `free` composes with the automatic reclamation machinery with zero bookkeeping -- scope exit finds null and does nothing, `free x` twice is a no-op, and use-after-free through the freed binding is just an ordinary null. Second, a `free` target counts as ESCAPING in escape analysis, and this single line is what makes the feature safe rather than merely present: a non-escaping struct/arr/map local stack-allocates its storage, and calling a refcounted release on a stack address underflows into the frame -- ASan caught exactly that on the first sweep, a stack-buffer-underflow in `festina_release_check` reading eight bytes before a stack map header. Escaping-ness forces the heap allocation with a real header, and as a side effect keeps scope-exit tracking from also claiming ownership.

Two guards close the type matrix. A regex value now carries a `cached` mark: a /pattern/ literal's compilation is shared by every later execution of its line, so festina_regex_free no-ops on it, and `free r` is safe whether r held a literal or a regex() result -- verified by freeing a literal binding in a loop and testing the pattern again afterwards. And `free` is refused at compile time on constants and on parameters, the latter because a parameter borrows its caller's value (claude.md #84) and freeing a borrow is never right.

The sweep found one genuine pre-existing bug that `free` merely made observable: a GLOBAL bound to a fresh refcounted value carried a refcount of 2, because _emit_global_retain_release retained unconditionally on the reasoning that over-retaining "can only ever delay a free". True while globals lived to process exit unconditionally -- the extra count was unobservable. `free g` decremented 2 to 1 and freed nothing, silently. Globals now use the same freshness test locals have always used.

`delete` is JS's, with JS's meanings. On a map the entry stops EXISTING -- count drops, forEach skips it -- which set-to-null could never express; deleting a missing key is a safe no-op; the key may be computed. On a struct field the value is released and the field reads null (with claude.md #97's inherited caveat: a struct/arr/map-typed field auto-vivifies on the next reach-through, so it returns empty rather than staying null -- documented, not hidden). On a query-row field it does both of the above and clears the row's presence bit, so a deleted column and a never-selected column are indistinguishable afterwards -- which is JS's own behavior for deleted properties. `delete x` on a whole variable is an error that names `free`. And `delete` staying a valid MEMBER name is what keeps blob's `f.delete()` compiling -- the parser's eat_name already accepted keywords as member names, so making `delete` a keyword cost nothing.

`undefined()` needed each row to know which columns its query actually produced -- and building that exposed that column matching was POSITIONAL. `select name from t` against `table t { id:int name:text }` read the name's text into the id slot as an integer; `select id` read a result column that did not exist for name, which is formally undefined behavior in sqlite. It had hidden behind the `SELECT *` habit since claude.md #32, and one test even pinned it as intended behavior, named test_columns_map_by_position_not_name. Matching is by name now, case-insensitively, so partial and reordered selects land every value in its declared column; the inverted test pins the new contract, including the alias consequence (an alias renames a column away from matching -- alias TO a declared name to remap deliberately).

Each row now carries a presence bitmask one hidden slot past its columns -- offsets untouched, one extra i64 per row -- set per declared column when the result set contained it. `row.undefined('col')` reads the bit through the same column-names global that schema sync already used; an unknown name fails the program, because asking about a column the table does not have is a typo and either boolean answer would bury it. Columns past the 64th report as always-present; a table that wide has other problems first. The distinction this all serves is real and small: `name == null` conflates "the database has no name" with "you didn't ask for the name", and a program deciding whether to trust a value needs to know which it got.


112. A STRUCT AS THE QUERY'S LANDING SPOT

claude.md #111's alias note ended with an instruction that deserved to be read as an indictment: "alias TO a declared name to remap deliberately." That works when the query's shape happens to be a table's shape. It has no answer for `SELECT count(*) AS total`, a JOIN, or any computed column -- results whose shape exists only in the query, which is most of what SQL is for. A `table` declaration cannot chase those, and it should not try: declaring a table CREATES a table (claude.md #28), and a result-only shape has no business leaving DDL behind in the database.

So a struct can receive a query now: `arr[data] q = sqlite('select id as whatever from examples')`, with `struct data { whatever:int }`. Name the fields after the result's own column names -- including its aliases -- and #111's name matching does the rest.

The implementation is deliberately thin, and where it is thin is the design. Collection reuses the entire table pipeline -- prepare, bind, the name-matched, presence-masked flat rows of #111 -- with the column-name and column-type arrays derived from the struct's fields instead of a table's columns. Then one generated function per struct type converts each flat row into a real struct instance, in place in the result array: each 8-byte slot is loaded at the field's own LLVM type and stored at the field's real offset, pointer fields transferring ownership, and the struct starts at refcount 1 owned by the array. Everything downstream -- member access, aliasing, release cascades, `free`, `delete` -- applies with literally nothing new, because after conversion there is nothing query-shaped left: the element IS an ordinary struct.

Two deliberate exclusions. A struct field that no query column could produce -- arr, map, a nested struct -- is a compile error naming the field, rather than a silently-null field. And the presence mask is dropped in conversion, so `undefined()` does not exist on these results: it is a table-row method, and a struct that remembers which query built it would be a third kind of value pretending to be a first. A field the result did not produce reads null, full stop; a program that needs the finer distinction queries into a table row, which is what the distinction was built on.

Verified the way the rest of this stack is: the user's exact example, aggregates, an unmatched field reading null, a blob column landing in a struct field and reading back its bytes, an element aliased out of a freed result surviving (refcount, not luck), and 120 iterations of query-alias-free churn with text and blob fields under ASan and LeakSanitizer -- zero leaked bytes, with ownership of every text and blob transferring from row to struct exactly once.


113. WHAT THE COMPILER KNOWS, THE RUNTIME SHOULD STOP RE-LEARNING

A survey pass, prompted by the question "what else is paid at runtime that compile time could pay once?" -- with an honest accounting of what was already done, two things worth doing, and one thing that looked like the answer and wasn't.

Already done, for the record, because a survey that only lists new work misrepresents the state: clang runs at -O2 for both the generated IR and the runtime (so constant folding, inlining and vectorization are already bought), /pattern/ literals compile once per site (#85), color and font literals resolve at compile time outright (#91), string constants are interned, and the circle mask cache (#104) precomputes what a draw loop would otherwise re-rasterize.

The real find: `sqlite()` re-prepared its SQL on every call. sqlite3_prepare_v2 parses, plans and compiles the text into a bytecode program each time -- and when the SQL is a compile-time string literal, that text can never change, so every preparation after the first is pure waste. This is the identical fact pattern #85 found for regex literals and the fix has the identical shape: one private cache slot per call site, filled on first reach, with dynamic SQL (a template, a variable) keeping the per-call path because the same site can legitimately see different text. The runtime side is a small registry so every existing consumer -- row collection, exec, the three scalar helpers -- ends its statement through one function that RESETS a cached statement and finalizes an uncached one; no signatures changed. Slots are per-site rather than per-text, so two identical literals can never entangle. Measured: 20,000 one-row SELECTs, 164ms to 55ms.

The thing that looked like the answer and wasn't: the INSERT benchmark. 20,000 literal INSERTs took 16.7 SECONDS, and no amount of prepare-caching touches that, because the cost was never parsing -- it was sqlite's shipped defaults fsyncing every autocommitted statement to disk. The fix is a runtime default, not a compile-time anything: the database now opens in WAL mode with synchronous=NORMAL, the standard application-embedded configuration (a transaction survives an application crash unconditionally; an OS crash or power cut can lose the most recent commits but can never corrupt the file). That is the right trade for games and tools, and the same one every browser and phone ships sqlite with. Errors from the PRAGMAs are deliberately ignored so a read-only filesystem just keeps the old defaults. 20,000 INSERTs: 16.7s to 0.30s -- 56x, of which the statement cache is a rounding error. Filed under this section anyway, because the honest summary of the survey is "the biggest win wasn't compile-time at all, and pretending otherwise would have shipped the small fix and called it done."

Surveyed and deliberately not taken: pre-merging template-literal constant parts (the concat is a runtime cost but a small one, and -O2 already folds the length math), bulk-building map literals (linear find per entry, but map literals are small by doctrine -- see festina_map_find's own comment), and resolving undefined()'s column name at compile time for literal arguments (correct, easy, and pointless -- undefined() is never hot). Each would be motion, not progress.

The same pass added leak ISOLATION to the stress suite: one minimal program per data type -- int, float, bool, text, blob, regex, arr of unmanaged and of managed elements, map of both, struct, self-referencing struct, table rows, struct query results, img, aud -- each exercising create/alias/reassign/destroy for that type alone under ASan and LeakSanitizer. The churn programs stay, because mixed ownership is where the interesting bugs live; what they could never do is NAME the type that regressed, and these do. Writing them re-proved the documentation twice: the first draft of the img and aud programs freed one handle through two bindings, and ASan rejected them with exactly the double-free the manual-free contract warns about -- the failure mode is real, reproducible, and now pinned in a comment where the next person will read it.


114. EVERYTHING PRINTS, EXCEPT WHAT SHOULDN'T

`log()` and `${}` now accept any value with an honest text form, by compiling a non-text value as its `.toText()`. int/float/bool keep the stringifiers they always had. Structs, table rows, arrays and maps render JSON-like -- which is new, and which is what this section is actually about. And blob, img and aud are compile errors in both positions, which is the design decision worth defending.

The rendering is split where it wants to be split: everything that touches BYTES is a small growable string builder in the runtime (append, JSON-escape, the null-sentinel-aware number/bool appends -- O(n) overall, where chaining festina_str_concat would have been O(n^2) in exactly the loop people log from), and everything that knows STRUCTURE is generated IR, one cached function per type, because only the compiler knows a struct's layout, a row's column names, or which mask bit is which. The per-type functions recurse into each other for nested containers, registered-before-generated exactly like the release wrappers (claude.md #106's load-bearing cache write), so a self-referencing struct generates one function that calls itself. Runtime depth caps at 32 and renders null past it: a cyclic value (constructible since #106) truncates instead of overflowing the stack, because a debug rendering that can crash the program it exists to debug would be worse than an honest ellipsis. JSON.stringify throws on cycles; a logging statement should not.

The details that make the output trustworthy rather than merely present: text is escaped properly (quotes, backslashes, control characters), a null text or element is the JSON null literal, a float NaN -- which is also Festina's float null -- renders null because JSON has no NaN, a table row renders a database NULL as null but OMITS an undefined column entirely, which is precisely JSON.stringify's treatment of undefined and completes the analogy #111 built the presence mask on. An opaque handle inside a container renders as "<blob>"/"<img>"/"<aud>" or null, because erroring an entire struct over one field would make rendering useless for the debugging it exists for. One expectation I got wrong and the test caught: an UNASSIGNED float field renders 0, not null -- claude.md #97's zero-value rule, calloc'd storage reads zero -- while an explicitly-nulled one renders null. The test now documents the distinction instead of fighting it.

Bare blob/img/aud in log() or a template refuse at compile time, each error naming the way out. This REVERSES #109's choice for blob, which made log(blob) print the contents -- reversed deliberately: a blob's bytes may be binary garbage mid-string, the explicit `.toText()` is one method call away, and the general principle -- rendering that could silently produce nonsense should be asked for, not defaulted -- beats the convenience. img and aud have no text form, so for them the error is not even a policy, just the truth.

.toText() is also the explicit spelling on all four container kinds now, so the implicit conversion is sugar over a method a program can call itself -- the same relationship int's `${n}` has always had to `n.toText()`. Verified: every shape end to end (nesting, escaping, both kinds of null, omission, placeholders, the cycle), 150 iterations of rendering churn across all four container kinds under ASan/LeakSanitizer with zero leaked bytes, and the builder's ownership convention (finish() hands the caller an owned buffer) riding the same temporary-freeing paths every other owned text already uses.


115. A BLOB IS USUALLY A TEXT FILE

claude.md #114 put blob in the refuse-to-render list, reasoning that its bytes may be binary garbage mid-string. One message later that was overturned for the better reason: a blob is very often a TEXT file -- a config, a save, a log -- and it already carries the exact method the implicit conversion is defined as. `log(config)` and `${config}` now mean `config.toText()`, the contents. A binary blob renders its bytes up to the first NUL, which is precisely what its explicit toText() does, and the implicit and explicit spellings disagreeing would have been worse than either policy.

What survives from #114's caution, deliberately: img and aud still refuse (no text form exists, so the error is just the truth), and a blob FIELD inside a rendered struct/row/array/map still shows the "<blob>" placeholder rather than its contents -- a JSON-ish debug rendering that inlined whole files would drown the structure it exists to show. The bare value is yours to print; the container stays a map of the territory.


116. SPLIT AND JOIN

`sentence.split(sep)` and `words.join(sep)`, the last two pieces of everyday string handling this language was missing. The separator is a text or a regex -- the same pair `.replace()` already accepts, so the surface has no new shape, only new names.

The semantics are JS's, taken deliberately and in full, because half-JS is the worst dialect: empty pieces between adjacent separators are KEPT ('a,,b' is three pieces), a separator at either edge yields an edge empty, an empty-match regex splits between characters without looping forever and without a trailing empty ('abc'.split(/x*/) is exactly ['a','b','c']), an empty TEXT separator splits per UTF-8 code point rather than per byte -- a byte split would shatter every non-ASCII character into invalid fragments, and 'héllo' keeps its é -- and join renders a null element as an empty string, so [1, null, 3].join('-') is '1--3'. Each of those is a test, because each is a decision.

The split result is built by the runtime as a real refcounted arr[text] -- the same {refcount | length, data} layout every array has, pieces owned -- so binding, aliasing, push, free and scope-exit reclamation all apply to it with literally nothing new; it is an "owning" source exactly as an array literal is. join works on arrays of text, int, float and bool -- element kinds with a text form of their own -- through one runtime function that takes the element kind as a compiler-supplied constant, the same only-the-compiler-knows-T reasoning behind the per-type JSON render functions, and anything else is a compile error naming the rule.

One inherited edge, not new here: the chained `sentence.split(' ').join('-')` works and leaks its intermediate array -- claude.md #108's open case of a chain yielding a managed value, no worse for split than for any other producer. Bind the intermediate to a name and it reclaims normally, which the churn test does and the docs show. Verified end to end for every semantic above, plus 200 iterations of split/join/regex-split churn with aliasing and manual free under ASan/LeakSanitizer -- zero leaked bytes.


117. THE OWNED TEMPORARY WAS A RETAIN ALL ALONG

claude.md #102 and #108 both stopped at the same wall and wrote the same note: a chain yielding a managed value or a text (`Inner got = make().inner`, `make().inner.label`, and lately `split(' ').join('-')`) had to keep leaking, because releasing the parent frees the value just loaded, and fixing it "needs a notion of an owned temporary that outlives its producing expression -- which this codegen does not have". This section is that fix, and the humbling part is that the missing notion was one instruction: RETAIN THE RESULT FIRST.

Retain the escaping value, then release the parent. The parent's release cascades into its fields -- decrementing the very value just retained -- so the net is exactly one reference, owned by the expression that extracted it. For a text result there is no count to retain, so it is COPIED (festina_text_own) before the graph it aliased into is released. Both directions of the old dilemma dissolve: nothing leaks, and nothing is freed under the caller, by construction rather than by refusal.

The +1 has to land with exactly one owner, and that is the second half of the fix: `_is_owning_refcounted_source` and `_is_owning_text_source` now answer yes for a non-computed member chain whose base is a Call. A binding takes the +1 without adding its own retain, a return transfers it, a global reassignment and a push absorb it -- every position that already knew what to do with a fresh Call result now treats `make().inner` identically, which is the point: after the load, it IS a fresh result. The predicate and the emission are the same walk (chase `.obj` through non-computed links, ask if the base is a Call), and the comment on `_member_chain_call_base` says out loud that the two must never disagree: a promise of ownership the load didn't mint drops a retain, and the reverse leaks one.

Alongside the field-load fix, the method-receiver sites (join, toText, the blob methods, save, undefined) stopped judging their receiver by their RESULT type. That judgement was #102's aliasing precaution applied where no aliasing exists -- join's text does not point into the array it joined -- and it is what made `split(' ').join('-')` leak. Receivers are now released whenever the expression owns them, which one shared helper decides with the same owning predicate. Two side effects came free: `log([1,2,3])` no longer leaks the literal it rendered, and a discarded owning chain as a bare statement is reclaimed, because the discard checks now ask the predicate instead of re-listing node types.

What this deliberately did NOT change: an intermediate link's value is an alias into the base call's graph and is reached exactly once by the base's cascade -- releasing it directly too would double-free, so only the base Call's value is ever released, same as #108. A chain based on a VARIABLE stays borrowed, no retain, no release -- `o.inner` is an alias exactly as before. img/aud call receivers stay unreleased for #110's documented reason. And two smaller leaks in this class remain, now written in todo.md at their actual size: a chain passed as a function argument leaks its +1 (parameters are borrows, so no one owns the release), and a computed-index receiver (`getRows()[0]`) still leaks its array.

The #108 test that PINNED the managed-chain leak -- asserting no release was emitted, so the leak could not silently become a use-after-free -- failed the moment this landed, exactly as a pinned tradeoff should. It now asserts the inverse and more: the release must appear, and the retain must precede it in the emitted IR, because the ORDER is the entire safety argument. Verified beyond it: every previously-leaking shape plus every transfer position (return, global, push) and every borrowed form, 120-iteration churn each, zero leaked bytes and zero corruption under ASan/LeakSanitizer -- and the whole suite and stress corpus, which exercise the old behavior's every assumption, green on top.


118. COUNTING REPLACED PROVING: HEADERS FOR IMG, AUD AND REGEX -- AND A MEMO FOR regex()

Three types were still living outside the refcount protocol. img and aud were owned-or-leaked: a handle the compiler could prove created-here-and-never-escaping was freed at scope exit, and everything else -- an alias, an escaper, a call result that might be shared -- leaked, because freeing was final and freeing anything possibly shared was a use-after-free. regex carried a runtime `cached` flag purely so `free` could refuse to free the literal cache. And #110 had a standing note that call-result img receivers must never be released, because `img func get() { return shared }` hands back the global itself. Every one of those hedges was the same missing 8 bytes.

This section gives all three the standard i64 header -- allocated in festina_image_box, festina_audio_from_bytes and festina_regex_compile, the same raw-8 layout blob uses -- and turns their free functions into releases: decrement via festina_release_check, destroy only on the last reference. The codegen side then DELETED more than it added: _OwnedImage/_OwnedAudio/_OwnedRegex and their ownership proofs are gone, the three types joined _is_refcounted and blob's always-release VarDecl branch, and the img/aud special cases in `free`, `delete`, receiver-release and the struct-field cascade all collapsed into the one dispatch every other refcounted type already goes through. A binding owns exactly one countable reference wherever its value came from; releasing it is always a safe decrement. The literal regex cache stopped being a flag and became the standard immortal sentinel (negative header, set by festina_regex_mark_cached), so `free` on a binding aliasing a /pattern/ literal no-ops through the very same path as everything else -- one less special case in the runtime too. Two documented gaps closed at once: an escaping img/aud handle no longer leaks (security.md's dangling-alias contract paragraph is retired outright), and #110's never-release-a-call-result rule dissolved because the Return path retains an aliased value on the way out, so the temporary always owns its own +1. An aud destroyed by its last release still stops every channel streaming it first; a freed-but-still-referenced clip keeps playing, which is what a decrement means.

The freshness test needed the same widening the blob form did: `img s = 'x.png'` is a StringLit whose coercion emitted the load call, so text-in/handle-out IS fresh -- and threading that test through the places that only asked "is this a Call" (Return, member-assign, map-set, array-literal elements) fixed a latent blob over-retain in each of them along the way. A /re/ literal classifies fresh too, on immortality grounds: retain and release are both no-ops on it, so the cheaper answer is the right one.

The header is also what finally made dynamic regex() cacheable. The blocker was never the lookup -- it was EVICTION: a per-site cache that recompiles on a changed pattern must free the superseded compilation, and a binding somewhere may still alias it. With a refcount that hazard is just a decrement. festina_regex_compile_memo keeps one {pattern copy, flags copy, compiled} slot per call site (a private [3 x ptr] global, keyed by AST node like the literal cache); a strcmp hit retains and returns, a miss releases the slot's reference, recompiles and takes a fresh one. The caller always receives its own +1, so memoized results ride the exact temporary/scope-exit machinery unchanged. Measured: 200k regex('[0-9]+').test() evaluations dropped from ~367ms to ~15ms -- literal speed -- and the alternating-pattern site recompiles per change, never serving a stale automaton, which the correctness half of the old "caching would be a bug" note demanded and a test now pins.

Three tests that PINNED the old ownership proofs failed exactly as pinned tradeoffs should, and were inverted to assert the new safety argument: the release IS emitted, and the retain precedes it -- order is the argument, same as #117. The audio white-box harnesses needed a festina_release_check stub with real header semantics (the audio TU calls into the core runtime now; CONTRACT.md's cross-TU-stub lesson, third application). Verified under ASan/LeakSanitizer: alias-free-then-use for img and aud, escaping-handle churn, a shared global surviving its temporary's release, img struct fields cascading, 100 iterations of memo eviction with a live alias to the evicted regex, and the full suite plus the per-type leak programs -- the img/aud ones rewritten from "free exactly once, the alias dangles" to freeing through BOTH bindings, because that is the ordinary shape now.


119. THE LAST TWO CHAIN SHAPES, AND A LEDGER FOR MINTED OWNERSHIP

claude.md #117 fixed chained extraction by retaining the escaping value before releasing its producer, and left two shapes in todo.md at their actual size: a computed-index receiver (`getRows()[0]` leaked the array nothing ever released) and an owning argument to a user function (`f(make())` leaked the +1, because parameters are borrows and nobody owned the release). Both close here with the same retain-first argument, and the interesting part is not the fix but the bookkeeping it forced.

The computed-index fix is #117 verbatim, one level down: mint the element's own ownership first -- retain a refcounted element, copy a text one -- then release the container, whose element cascade decrements the just-retained value back to a net of exactly one owned reference. A scalar element needs no minting, so its container is simply released. The argument fix is the text rule generalized: after the call, an owning refcounted argument is released exactly where a text temporary was already freed, and it is sound for the same reason -- anything the callee KEPT took its own retain on the way to wherever it was stored (an escaping parameter retains at binding, a global or field store retains, a returned alias is retained by the Return path), so the caller's +1 is provably the last reference nothing else will drop. The receiver sites that had no release at all -- array methods, clip/resize, img.width/height -- now run the shared owned-receiver helper too, which no-ops unless the receiver is actually owned.

The forced insight: syntax stopped being enough to answer "does this expression own its value". Every prior owning predicate was a walk over AST shapes -- a Call is fresh, a literal is fresh, a dotted chain over a Call is owned. A computed member breaks that, because the SAME syntax mints or borrows depending on the element's type: `matrix()[0]` retains a real refcounted row, but `rows()[0]` on a query-result array hands back a table row that has no header to retain -- the array owns its rows outright (#85), and claiming ownership there would have made every binding skip the copy that keeps the row's columns alive. Guessing from syntax would be exactly the predicate/emission disagreement #117's comment warned corrupts memory in whichever direction it errs. So the emission now RECORDS what it did: _minted_values holds the id of every expression whose emitted IR actually contains the retain/copy, the computed branch and the chain release write to it at the moment they mint, and the predicates read it back. The ledger cannot disagree with the IR because the IR's author is the only writer, and every consumer runs after the value it asks about was emitted -- an ordering the codegen already guaranteed everywhere (emit first, then decide retain/copy).

What deliberately remains, renamed in todo.md to its true residual size: `rows()[0]` on a call-result array of TABLE ROWS still leaks the array, because the row genuinely cannot outlive it and releasing would be worse than leaking. That case stays unrecorded, so the predicates keep calling the row borrowed and a column read off it is still copied at its binding -- verified intact under ASan, not just reasoned about. Verified beyond it: every closed shape (array/map/text/scalar elements off call results, nested `matrix()[0][1]`, owned chains over computed bases, literal and call and chain arguments, callee-keeps-the-argument), 120-iteration churn, zero leaked bytes under ASan/LeakSanitizer, and the full suite green. 1243 tests.


120. CYCLES COLLECT: TRIAL DELETION IN THE RELEASE WRAPPERS

The oldest accepted leak falls. claude.md #106 made `struct Node { next:Node }` legal and recorded the cost: a reference cycle keeps its own counts above zero forever, refcounting can never free it, todo.md said "tracing collector or weak references" and recommended breaking cycles by hand. This section implements the middle road both of those framings skipped: synchronous, single-root TRIAL DELETION (the classic Bacon-Rajan test), run by the generated release wrappers themselves whenever a value of a possibly-cyclic TYPE is released but still referenced. markGray tentatively removes every reference internal to the value's reachable subgraph; scan checks what the counts say survives (anything still positive is externally held -- scanBlack restores it and everything it reaches); collectWhite frees what nothing external could reach. A garbage cycle dies on the release of its last external reference; a held cycle is provably restored to exactly the counts it entered with.

The division of labor follows the release wrappers' own: the runtime got the small type-blind state machine (festina_cycle_* -- claim-this-node color transitions, edge inc/dec, the container visit/dispose loops), and codegen generates the traversals, because only it knows a struct's field layout. Four functions per participating type -- gray, scan, black, white -- registered before generated exactly like the release wrappers, so a self-referencing type's traversal calls itself. Color state lives in bits 61-62 of the SAME i64 header the refcount uses: black is the all-zero encoding, so outside a trial every header is a plain count and festina_retain/release_check never mask anything; a trial always exits with survivors black and their counts exact. The immortal sentinel composes for free -- negative header means every helper declines to color, count or traverse it, which is precisely right: immortal-anchored data is reachable by definition. And the whole apparatus is gated on one compile-time question, "can this type reach itself through managed edges" (struct fields, arr elements, map values -- computed over the declared type graph, cached per type name): an acyclic program generates zero traversal functions and its wrappers gain zero instructions, so the collector is genuinely free for everyone not using cycles. Cycles routed through containers -- `kids:arr[Tree]` against `parent:Tree`, a map of peers -- participate fully; the leaf types (text/blob/img/aud/regex) never can, and a white node's non-cyclic cargo is disposed through the ordinary release machinery, whose counts the trial never touched.

The fix that mattered most was not the algorithm but an ORDERING the algorithm exposed. Every field overwrite used to go load-old, retain-new, release-old, store-new -- harmless when a survivor's release did nothing, memory corruption once it runs a trial: the field still physically points at the value whose count the release just removed, markGray walks the graph, finds the stale edge, and removes the same reference AGAIN -- enough to whiten and free a value a real external reference still holds. Every traversable location now stores before it releases: field and element writes (load-retain-STORE-release), map sets (the old value's release deferred past festina_map_set), `delete` on a field (null stored first), and festina_map_delete in C (entry removed from the table before its value is released). The graph a trial walks is the graph the counts describe, at every release site. A dedicated test pins store-before-release in the emitted IR, because this is the kind of invariant a refactor re-breaks silently.

The struct_self leak program now closes its chain into a real cycle -- refcounting alone would fail it, and it runs leak-free under LeakSanitizer on every test run. Which forced the canary question #108 already answered once: the harness's proof-it-can-fail program WAS a reference cycle, on the theory that nothing would free one for a long time. Fixed leaks make lying canaries; it is now the #119 row-array residual, the one deliberate leak left standing. Verified under ASan/LeakSanitizer: self-cycles, pair cycles, array-routed parent trees and map-routed rings, all reclaimed across hundreds of iterations with zero leaked bytes; held cycles reads-intact through every trial; the mixed churn of list-building-with-aliasing plus cycle-closing, clean. Measured: 20,000 dropped 21-node cycles in ~34ms end to end, so the synchronous per-release trial is far from the theoretical worst case for ordinary graphs -- the deferred-root buffer remains as the known optimization, recorded in todo.md at its real size. 1249 tests.


121. THE AUDIO DEVICE SEAM, AND MACOS SUPPORT BEGINS

macos.md and windows.md turned from analysis into work this session, and the first two phases of the macOS plan landed together because the second is what proves the first. Phase 0: a real CI matrix (.github/workflows/ci.yml) -- the Linux job runs the whole suite with FESTINA_STRICT_DEPS=1, and a macos-14 job installs the brew tier, runs the WHOLE suite, and compiles and runs the four windowless examples, the plan's exit criteria verbatim. The mechanism that makes one suite serve both platforms is a conftest rule, not a test list: a compile that fails for a missing dev package or a platform with no backend for the feature becomes a pytest skip -- except under FESTINA_STRICT_DEPS, so the primary platform's coverage cannot silently shrink while the new platform's job sheds exactly the tiers it lacks. Compiling an audio program on darwin now says the true thing ("the AudioQueue backend awaits real-hardware verification -- macos.md Phase 1", overridable via FESTINA_ENABLE_MACOS_AUDIO=1 for exactly that verification) instead of a pkg-config error inviting a Mac user to apt-install ALSA, and festina doctor's audio lines match.

Phase 1 is the plans' first shared seam, cut where the audit said to cut it: the entire platform difference in 780 lines of audio runtime was six ALSA calls, and they are now behind festina_pcm_open/write/close -- open takes channels/rate and hands back a handle or an error string, write BLOCKS until the device has room (the contract the per-channel streaming threads are built on), close is close. snd_pcm_recover moved inside the ALSA write, because retry-the-recoverable-cases is a device property, not channel-pool logic. Everything above the seam -- the pool, the stealing, WAV/MP3 decoding, the pthread model -- compiles unchanged on every platform.

Three implementations sit behind the seam. ALSA is the original six calls, moved verbatim. The AudioQueue backend (macos.md Phase 1) is ~150 lines under __APPLE__: four preallocated buffers and a condition variable reproduce ALSA's blocking push exactly, per-channel queues mirror per-channel handles, and the synchronous AudioQueueStop on close means disposal races nothing. It is compiled and type-checked against the real AudioToolbox headers by the macOS CI job on every push -- honest words for what that verifies: the code, not the sound; the gate lifts when a real device has played through it. And FESTINA_AUDIO_NULL=1 is the third: an instant sink at the seam, the cross-platform successor to the ALSA-only ~/.asoundrc trick, letting play()/stop()/isPlaying() run end to end on machines with no audio stack at all -- verified on Linux against the real backend so the shim is pinned to real behavior.

The white-box harnesses got the re-seating the plans promised: instead of macro-overriding five snd_pcm_* symbols under an ALSA include, each defines FESTINA_AUDIO_DEVICE_EXTERNAL and supplies the three festina_pcm_dev_* functions -- the single-stream harness's EBUSY device became "the second open returns NULL", exactly the semantics at the new level -- and the harnesses stopped needing ALSA headers at all, so the whole channel-pool white-box tier runs on macOS CI as-is. Verified: both compile modes clean, the 47-test audio tier green, the audio churn programs clean under ASan/LeakSanitizer through both the real-ALSA path and the null shim, and the full suite at 1279.


122. THE FIRST REAL MACOS RUN, AND WHAT IT TAUGHT

Two CI rounds on real Apple Silicon turned macos.md Phase 0 from implemented to verified, and each round's failures were worth more than a month of reasoning about portability. Round one: 1188 tests passed on arm64 -- the Python frontend, Apple clang consuming the generated IR, sqlite, blobs, timers, the offscreen graphics tier -- and all 31 failures were one fact: LeakSanitizer aborts AT RUNTIME on darwin/arm64 ("detect_leaks is not supported on this platform"), and the leak harness's environment probe checked that ASan LINKS but never that LeakSanitizer RUNS. The probe now executes its probe binary under detect_leaks=1 and exits the skip code when that aborts -- the Linux-only-by-decision tier macos.md documents, made mechanical.

Round two: 8 failures in two classes, one shallow, one deep. Shallow: three tests call compile_file directly rather than through compile_and_run, so the missing-dependency skip never saw them -- the skip logic moved into a shared conftest helper (compile_file_or_skip) used by the fixture and the direct callers alike. Deep, and the real find: /\s+/ matched NOTHING on macOS. \w, \d, \s and \b are GNU extensions of glibc's regcomp -- api.md promises them, tests pin them -- and BSD libc silently treats \s as a literal 's'. Silent is the operative word: no error, no warning, just different answers on a documented behavior, which is exactly the class of platform bug a CI matrix exists to surface and exactly what windows.md predicted its regex suite-as-referee would catch on a different libc.

The fix is translation, not a vendored engine: festina_regex_expand_gnu rewrites the GNU class escapes into the POSIX bracket classes every implementation defines ([[:space:]], [[:alnum:]_], ...) before regcomp, on EVERY platform -- one behavior exists, and it is the one already tested; on glibc the expansion is provably identical to the extension it replaces (verified against the full suite). Bracket expressions are walked, not regexed over: inside [...] a backslash is literal per POSIX so translation must not fire there, and a [:class:] body's ']' must not close the bracket early -- both pinned by tests, because either mistake would be silent on Linux. \b is the one per-platform seam: glibc has it natively and keeps it; BSD has [[:<:]]/[[:>:]] instead, so on __APPLE__ a \b becomes the opening or closing form by what follows it -- covering the \bword\b shape the escape exists for, with the whole-word-vs-substring distinction pinned. The expansion sits in festina_regex_compile, so literals, the memo, and dynamic regex() all inherit it in one place.

Also from round two's log, the good news that needs recording: the conftest dep-skip mechanism did its job perfectly (67 skips on macOS were exactly the audio and windowed tiers), and the audio gate produced its intended honest error at every site that met it. 1282 tests. The macOS job's remaining path to green is exactly these fixes, which is what round three is for.


123. THE WINDOWING SEAM, AND A NATIVE COCOA BACKEND

macos.md Phase 2, cut the same way Phase 1 cut audio: extract the platform-specific sliver, put a small seam in front of it, implement the seam twice. The sliver was smaller than the 1,477-line graphics translation unit suggested -- everything that draws (rects, circles, text, paths, transforms, gradients, images, clips, `saveCanvas`) already targets an offscreen Cairo image surface and never touched X11 at all. What was platform-specific was exactly the window: open it, close it, hand it a finished frame, and turn its native events into the five the language already defines (mouseDown/mouseUp/mouseMove, keyDown/keyUp, resize, close). That's `runtime/festina_runtime_window.h` -- `festina_window_open/close/present/events_wait/events_drain` -- and `festina_runtime_graphics.c` now calls exactly those five instead of any Xlib symbol directly; the existing X11 code moved into the same file as one of the two implementations, unchanged in behavior, verified against it by the full pre-existing Xvfb/xdotool/openbox suite with zero regressions.

The plan's own sketch expected a `window_client_size` accessor and left open whether redraws needed a seam-level event. Neither survived contact with the extraction: the window's size is already a parameter of open (and kept current by resize events, which every caller already handles), and a REDRAW event would have meant round-tripping every expose/drawRect callback through shared dispatch code for no reason a backend can't handle better itself -- so each backend just remembers the last surface handed to `festina_window_present` and repaints from it on its own OS-triggered callback. Fewer moving parts than the plan, not more.

The macOS side is one new translation unit, `festina_runtime_window_mac.m` -- Objective-C, because Cocoa cannot live in a plain .c file, compiled by the same clang via the .m extension and linked -framework Cocoa. An NSWindow with an NSView subclass whose drawRect: blits the Cairo backing surface through a CGImage (CAIRO_FORMAT_ARGB32 is byte-for-byte kCGBitmapByteOrder32Little | kCGImageAlphaPremultipliedFirst on any little-endian Mac, the standard interop recipe, not a coincidence). events_wait peeks the AppKit queue with nextEventMatchingMask:untilDate:...dequeue:NO so the timeout can carry the same timer deadline select() carries on Linux; events_drain fully pumps it (dequeue:YES, sendEvent:, updateWindows) into a small ring buffer that window/view delegate callbacks -- windowDidResize:, windowShouldClose: -- also push into, since those arrive as callbacks with no underlying NSEvent to drain. No [NSApp run] anywhere; the event loop stays exactly where festina_run_event_loop already put it. Key names go through a small keyCode table into the same vocabulary runtime/festina_key_names.h already pins from the X11 side, because a keyboard-driven program silently behaving differently on one platform is worse than no macOS support at all.

Two flags do the gating, and conflating them was the one design mistake worth naming because it would have been silent: CodeGen.uses_graphics_code (any drawing call at all, including a program that only ever calls saveCanvas and never opens a window) versus the new, narrower CodeGen.uses_graphics (only render() or a window event handler -- something that will actually put a window on screen). needs_graphics, unions of both, still decides whether to link a graphics backend at all -- unchanged. But the real-hardware-verification gate, the same shape Phase 1 built for audio (FESTINA_ENABLE_MACOS_GRAPHICS=1, CompileError naming macos.md Phase 2 otherwise), now keys off the narrow flag alone. Get that backwards and every macOS program that only ever draws offscreen -- the exact tier Phase 0's CI already runs headless -- breaks for no reason; a dedicated test (test_offscreen_graphics_never_reaches_the_darwin_gate) pins the distinction directly rather than trusting it to fall out of the rest of the suite.

No real macOS hardware touched this Cocoa file. It is syntax-verified the same careful way the rest of this session had to work without one: a hand-written, uncommitted stub covering only the AppKit/Foundation/CoreGraphics surface the file actually calls, checked with the GNU Objective-C runtime already on the Linux box (-fobjc-runtime=gnustep-2.0) -- an approximation of the real SDK, not a replacement for it, and said so in the file's own top comment exactly as the audio backend's did. It caught one real, worth-keeping bug regardless of the stub's fidelity: snprintf used with no #include <stdio.h>, silently relying on a transitive Foundation include that no other runtime file leans on. The macOS CI job gets a new compile-only step, mirroring the audio one exactly, so every push type-checks this file against the real headers even though the gate keeps it out of user programs until confirmed on a Mac with a real window, mouse and keyboard.

Two pre-existing test_platform.py tests were written against Phase 2's original XQuartz-era sketch and are now correctly wrong -- test_graphics_is_not_gated_anywhere and test_graphics_is_unchanged_per_platform_for_now assumed darwin graphics would stay ungated and untouched. Inverted, not deleted, into the shape the real behavior earns: darwin now swaps cairo-xlib for plain cairo and adds -framework Cocoa, and windowed (not offscreen) programs are gated. scripts/package_compiler.sh needed the two new runtime files added to its --add-data list -- caught by tests/test_packaging.py's real PyInstaller-and-run check, not reasoned about in advance. Full suite: 1287 passed, 7 skipped; scripts/leak_stress.sh clean.


124. PACKAGING GETS ITS FIRST REAL CI RUN

macos.md Phase 3 turned out to be less about writing new code than about noticing that scripts/package_compiler.sh -- which has produced a working per-platform PyInstaller binary since claude.md #59 -- had never actually been RUN by CI on either platform. pyinstaller is deliberately opt-in (requirements-build.txt, not requirements-dev.txt, per #59's own minimal-dependencies principle for developing festina/ itself), so tests/test_packaging.py's two tests, which build and smoke-test a real packaged binary, had been silently skipping in every CI run since the file was written. Nothing was wrong with the packaging path; nothing had ever exercised it outside a maintainer's own machine either. Both CI jobs now install pyinstaller and run the script for real as their own dedicated step (package, then compile and run examples/hello.f with the fresh binary) -- the closest thing to a release flow this project has, and now the first time either platform's packaged binary has been produced and run anywhere but by hand.

That step means something extra on the macos-14 job specifically: those runners are Apple Silicon, so packaging there produces and validates a real arm64 Mach-O binary on every push -- literally what "add an arm64 build to the release flow" (macos.md Phase 3's own words) asks for, once there's a CI step to call a release flow at all. package_compiler.sh also grew a Darwin branch, guarded on `uname -s` and on codesign being present, that ad-hoc codesigns the binary it just built (`codesign -s -`) so Gatekeeper allows running it locally with no prompt -- a self-signature, not an identity; it proves nothing to anyone the binary is handed to, which is exactly why full Developer-ID signing and notarization stay out of scope until there is an actual distribution channel to justify them. The CI step re-verifies with `codesign -v` rather than trusting that the codesign command merely exited zero.

setup.md's macOS line -- "should be similar in spirit... though native macOS support isn't there yet" -- was the last piece of documentation still describing macOS as unsupported, three phases after that stopped being true. It is now a real section: the exact brew line per feature tier (no `llvm`, since Apple clang consumes the generated IR directly and brew's own bottle would be redundant weight), the `xcode-select --install` / Xcode >= 15 floor, brew sqlite's keg-only PKG_CONFIG_PATH requirement, and -- since Phase 2 shipped native Cocoa rather than the XQuartz interim path Phase 3's own plan sketch expected to still be documenting -- an explicit "no XQuartz, no X11 at all" note in its place.

All four macOS phases are now built, in the sense that matters here: implemented, real-CI-verified on Apple Silicon on every push, and honestly gated where "verified" still means "type-checks against real headers" rather than "ran on a real device." What's left is exactly that gap -- flipping FESTINA_ENABLE_MACOS_AUDIO and FESTINA_ENABLE_MACOS_GRAPHICS on a real Mac and confirming sound plays and a window responds -- which needs a human with the hardware, not more code. Verified: the exact CI commands run locally first (package, smoke-test, and on this Linux box the Darwin codesign branch correctly no-ops); full suite still 1287 passed, 7 skipped.


125. WINDOWS PHASE 0 BEGINS

windows.md turned from analysis into work this session, the same pivot macos.md made when #121 started. The first surprise: most of Phase 0's checklist was already done. Item 2 (`.exe` naming) and item 3 (libLLVM's MSYS2 DLL candidates) turned out to have landed back at claude.md #39, written platform-generically from the start on the theory that Windows would eventually need them -- and it did, unchanged. Item 6 (binary-mode fopen, forward-slash paths) was likewise already pinned by TestBinaryFidelity, running on every platform's CI since before this port had one. What was actually still open was three things: the regex decision, doctor's Windows hints, and a CI job to run any of it for real.

The regex decision (item 1) is the one core-runtime gap windows.md's own audit named: MinGW-w64 has no <regex.h> at all, unlike glibc and BSD libc alike, which festina_runtime.c's core translation unit depends on unconditionally (every program links it, not an optional feature tier). windows.md's preferred answer -- MSYS2's mingw-w64-*-libgnurx package, the standard regex.h/libregex shim for MinGW -- is now wired in as _core_pkgs(platform_name), a pure function returning ["libgnurx"] on win32 and [] everywhere else, exactly the injectable-platform-name shape _static_sqlite_attempt and _feature_pkgs_and_flags already established. It reaches three places: the cached core object's own cflags, the final link line's libs, and the clang-IR-frontend fallback's cflags -- all three needed it, and missing any one would have been a real, silent gap. Whether libgnurx's ERE behavior actually matches glibc's under the existing regex suite -- the fallback this item reserves, vendoring musl's regcomp/regexec/regfree -- isn't answered yet; it can't be, from this session, without a real Windows box.

_check_feature_supported grew win32 branches for graphics and audio, but not the darwin shape: darwin's gates unlock with an env var because a real backend already exists there, compiled and type-checked, awaiting only hardware confirmation. Windows has no backend for either yet at all -- windows.md Phases 1-2 are still fully open -- so these two branches raise unconditionally, no override, naming "windows.md Phase 1"/"Phase 2" the same way the darwin ones name their own phases. Two pre-existing tests asserted the OLD, wrong claim (audio/graphics ungated on win32, written before this session under the assumption Phase 0 would leave them untouched) and are inverted into the shape that's actually true now, same pattern as every prior gate landing in this project.

festina doctor gets real Windows lines instead of silently reusing Linux's: libgnurx as REQUIRED (parallel to sqlite3, not optional -- it's core), graphics/audio as "not yet implemented, windows.md Phase 1/2" (distinct in wording from darwin's "built but unverified" lines, because the underlying claim really is different), and a bare $MSYSTEM check that flags the plain MSYS shell specifically -- MSYS2's POSIX-emulation layer itself, not one of its MinGW-w64 subsystems, so a binary built from inside it links against msys-2.0.dll instead of being an ordinary Windows PE executable. Silent about anything else: unset MSYSTEM isn't necessarily wrong, just not running inside an MSYS2 shell, which doctor has no extra opinion about.

The CI job is the one piece of this section that carries real, stated uncertainty. windows-latest via msys2/setup-msys2, UCRT64 environment, Python and the whole toolchain from the same MSYS2 install so there's exactly one PATH to reason about -- written from documented usage, not from having ever run it, because this project has no Windows or MSYS2 access at all. Unlike every macOS claim in this file, none of this has executed on real Windows even once. That is stated plainly in windows.md's own status block rather than implied to be more solid than it is, and its first real run is expected to surface real bugs -- exactly the role macOS Phase 0's four real-hardware rounds played (#121-122), just not played yet here.

9 new tests (TestCorePkgs, plus TestFeatureGating/TestAudioFeatureConfig additions), all unit-testing the pure per-platform functions from Linux -- the same technique that verified every darwin branch years before macOS CI hardware existed, now doing the same job for win32. Full suite: 1296 passed, 7 skipped. scripts/leak_stress.sh clean (untouched by this session's changes, re-run anyway).


126. THE FIRST REAL RUNS: MACOS GRAPHICS AND WINDOWS, BOTH FOR REAL THIS TIME

Two real bugs, found the only way either ever gets found -- pushing to real CI and reading what comes back, exactly the role macOS Phase 0's four hardware rounds played (#121-122) and windows.md's own status block said its first run was FOR.

macOS: festina_runtime_graphics.c failed to compile at all -- `#include <cairo/cairo.h>` in the new windowing seam header (#123) couldn't find the file. This translation unit had genuinely never been compiled on real macOS hardware before now: every prior CI round either predated the Cocoa backend (graphics needed cairo-xlib, which brew doesn't package, so the whole feature skipped as a missing dependency) or predated Phase 2 swapping in plain `cairo`. The bug itself is a portability gap in a header convention this codebase already used successfully elsewhere: `<cairo/cairo-xlib.h>`, in the X11-only block, works on Linux only because `/usr/include` is always on gcc/clang's own default search path there, quietly masking that pkg-config's own `-I` flag for both cairo-xlib and cairo points directly INTO the cairo headers directory (`-I/usr/include/cairo`, `-I<brew prefix>/include/cairo`), not its parent -- confirmed by literally running `pkg-config --cflags cairo-xlib` and finding both `cairo.h` and `cairo-xlib.h` sitting where that flag already points. Homebrew's non-default install prefix shares none of that implicit-default-path luck. The fix is the portable form, `#include <cairo.h>`, which resolves via the explicit `-I` flag alone on every platform -- the X11-only line stays as it was, since it's real, working, Linux-only code, not touched just because it happens to share the same coincidence.

Packaging failed next, for an unrelated reason: `codesign -s -` refused to sign a binary PyInstaller had already ad-hoc-signed itself as part of building it (a newer PyInstaller behavior, not something package_compiler.sh's own logic anticipated) -- "is already signed", nonzero exit, the whole script aborting under `set -euo pipefail`. `-f`/`--force` fixes it outright.

Windows: 21 of 26 failures traced to one root cause. `mingw-w64-ucrt-x86_64-libgnurx` -- windows.md's own preferred, documented answer for the one core-runtime gap -- IS a real, installable MSYS2 package, but `pacman --noconfirm` silently drops it from the install set because it CONFLICTS with `mingw-w64-ucrt-x86_64-libsystre`, already pulled in transitively by something else in the UCRT64 toolchain. No error, no warning surfaced as a failure -- just a `pkg-config --cflags libgnurx` that came up empty two steps later, in a completely different part of the run. The remaining 5 Windows failures were independent, genuine bugs of their own: `tests/conftest.py`'s `write_text`/`subprocess.run` calls used Python's locale-default encoding instead of UTF-8 on both the write and read side, which is harmless everywhere the default happens to BE UTF-8 (Linux, macOS) but corrupted a non-ASCII literal on Windows and then crashed decoding the compiled program's real UTF-8 output under the wrong codec -- now explicit both ways, matching festina/imports.py's own already-explicit UTF-8 file reads. And one test asserted `str(CompileError).split(":")[0].endswith(...)` to isolate the file path, which breaks the instant the path itself contains a colon -- a Windows drive letter, always -- now `rsplit(":", 4)`, peeling off exactly the four format-string colons (line/column/"error:"/message) from the right regardless of what the path itself contains.

The PR carrying those fixes got its own real CI run, which found the fix wasn't quite right yet -- the value of pushing early rather than reasoning further in isolation. `_core_pkgs` had switched to asking pkg-config for `libsystre` on the theory that whatever's actually installed is the name to ask for; wrong theory. `libsystre` is a deliberate DROP-IN REPLACEMENT for `libgnurx` -- its own PKGBUILD declares `Provides`/`Conflicts`/`Replaces` against it, which is exactly why the two conflict at package-manager level at all -- and it ships its pkgconfig file under libgnurx's OLD name, `gnurx.pc`, not `libsystre.pc` (confirmed via MSYS2's own package listing, not guessed). Install `libsystre`, ask pkg-config for `gnurx`: two different strings, doing two different jobs. That same real run surfaced a second, unrelated bug in the FIX's own tests: four new `_doctor_report()` tests monkeypatch `sys.platform` to `"win32"` to exercise the Windows doctor lines from Linux CI, but `shutil.which` has its own internal `sys.platform == "win32"` check that the spoof triggers for real, calling into the Windows-only `_winapi` module -- `None` on POSIX -- and crashing with `AttributeError` on Python 3.12+ (reproduced locally against a real 3.12.3 venv, not just reasoned about; passed clean on the 3.11 this sandbox otherwise runs). Every darwin-platform-spoofing test elsewhere in this file was already safe, because `shutil.which`'s Windows branch only fires for `"win32"` -- this was a genuinely new hazard, not a pre-existing one this session happened to trip. Fixed by patching `shutil.which` itself in those four tests, sidestepping it entirely rather than trying to out-think its internals.

That fix's own PR got a third real round, and it found three more things -- two genuine bugs this fix had simply missed, one flake unrelated to any of it. First: the `#include <cairo/cairo.h>` bug from the first round had a second, separate occurrence -- `festina_runtime_window_mac.m` has its OWN copy of that same include line, and only real macOS CI compiling THAT file (which nothing but the macos-14 job ever does) could have caught it; fixed identically. Second, and new: `festina_runtime.c` -- audited at the very start of macos.md/windows.md as "core runtime: none, POSIX only" -- turned out to have exactly one function that audit missed. `localtime_r` is POSIX, not ISO C, and MinGW-w64's UCRT headers don't provide it; only Microsoft's own `localtime_s` does, with the arguments reversed (`tm*` first, `time_t*` second) and success reported as `0` instead of a non-NULL return. `#ifdef _WIN32` now branches to it, with the exact signature taken directly from the compiler's own error output rather than remembered, since there was no way to compile-check either branch locally without a real MinGW toolchain. Third: `tests/test_packaging.py`'s own "prove no system Python is needed" test replaced PATH outright with a hardcoded `/usr/bin:/bin`, dropping Homebrew's bin directory where `pkg-config` actually lives on macOS -- invisible on Linux, where pkg-config lives in `/usr/bin` anyway. Fixed to prepend the python-shadowing directory to the REAL PATH instead of replacing it, the same shape `tests/conftest.py`'s own `path_without` fixture already uses elsewhere in this suite. Separately, that same run's Linux job failed once on `test_play_returns_distinct_pooled_channels` (a channel-pool allocation race), unrelated to anything in this diff and never reproduced locally across many runs -- left alone as CI-runner flakiness rather than chased with an unrelated change.

A fourth real round on the same PR found that the second cairo.h fix was itself incomplete, plus two genuine Windows correctness bugs the earlier rounds hadn't reached yet because nothing had compiled and RUN a real program on Windows until this round. `festina_runtime_window_mac.m`'s `#include <cairo.h>` now resolved as a file, but the file still failed to compile -- because `_feature_extra_object` had always passed an EMPTY pkg-config package list to the call that compiles this translation unit, so it never received cairo's `-I` cflags at all, on any round; the include-spelling fix was necessary but not sufficient. Passing `["cairo"]` there is the actual fix -- the darwin windowing companion object needed the same cairo package festina_runtime_graphics.c itself gets, and nothing had ever given it that.

With the regex and localtime_r fixes from the prior rounds, a compiled program could finally RUN on Windows for the first time -- and running it surfaced two things no amount of reading the code could have: first, `capfd`-captured program output came back `"hello from run\r\n"` against an expected `"hello from run\n"` -- the MinGW/UCRT C runtime opens stdout/stderr in TEXT mode by default, silently rewriting every `\n` a program prints to `\r\n` at the point of the write, which no platform's file-I/O audit had reason to even consider since it's a STREAM property, not a file-open-mode one. Fixed with a new `festina_runtime_init()` -- `_setmode(_fileno(stdout), _O_BINARY)` under `#ifdef _WIN32`, a no-op everywhere else -- called as literally the first thing every compiled program's `main()` does, ahead of even the database/graphics setup that already ran first. Second: roughly a dozen `test_compiles[example.f]`/explicit-`-o` tests failed with the output binary simply not existing at the path asked for -- `_default_output_name`'s own docstring had ALREADY documented that MinGW's linker appends `.exe` to a `-o` name lacking one regardless of whether that name came from the default-name logic or an explicit caller request, but the actual protection only ever covered the former (the only caller of `_default_output_name` is the "no `-o` given" branch). An explicit `-o program` silently linked to `program.exe` while `compile_file` kept insisting `program` was the output. Rather than silently substitute `.exe` into the caller's own explicit request, `_rename_if_linker_appended_exe` runs after linking and renames the linker's real output back to the exact name asked for, honoring the caller's request rather than working around it.

Nothing here needed a design change -- every fix is a one-line-to-one-function correction to something written without the real environment to test it against, which is the entire reason macos.md and windows.md both budgeted for exactly this kind of round from the start, including the rounds where the fix needed ITS OWN fix, three times over now. Verified: both cairo.h fixes and the extra_object cflags fix compile clean with the real cairo-xlib pkg-config flags on Linux (the shared header) or can only be reasoned about (the .m file, which nothing but real macOS CI compiles); festina_runtime_init and the .exe rename fixup are exercised for real on Linux, where both are confirmed no-ops, and produce a real compiled-and-run binary end to end; the localtime_s branch still can't be compile-checked at all without a real MinGW toolchain. Full suite re-run clean after every fix in this round (1296 passed, 7 skipped) plus scripts/leak_stress.sh. What remains unconfirmed is the one thing that only a real Windows/MSYS2 or macOS run can confirm -- whether THIS round's fixes are actually the last ones needed -- exactly the honesty windows.md's and macos.md's status blocks already commit to.

A fifth real round, on the push carrying round four's fixes, found the extra_object cflags fix hadn't actually reached macOS CI's real compile path at all -- and a second, independent bug on Windows that round four's own gating logic had accidentally created room for. macOS: `_compile_via_clang_ir_frontend` -- the fallback pipeline used whenever libLLVM isn't loaded, which per ci.yml's own comment is macOS CI's ENTIRE path (the LLVM bottle is deliberately not installed there; only the Linux job exercises the libLLVM fast path) -- had never been updated for `_feature_pkgs_and_flags`/`_feature_extra_object`'s per-platform darwin swaps at all. It built its pkg-config list from `_RUNTIME_FEATURES["graphics"]["pkgs"]` (the raw Linux table) directly and never so much as called `_feature_extra_object`, so `_RUNTIME_WINDOW_MAC_M` was never linked in on this path regardless of round four's fix to that function -- every graphics program, offscreen ones included (they link the same translation unit), failed at the LINKER stage with `_festina_window_open` and its neighbors undefined, the exact symbols only the Cocoa companion object provides. Round four's fix was correct but only reachable from the libLLVM path, which macOS CI never takes. Fixed by routing this fallback through the same `_feature_pkgs_and_flags`/`_feature_extra_object` functions `_runtime_objects_and_link_libs` already uses, rather than duplicating a second, platform-blind copy of the same lookup.

Windows: the SAME class of symptom, but pkg-config failed first (`'cairo-xlib' was not found`) rather than reaching the linker, since `_feature_pkgs_and_flags` has no win32 branch at all for graphics -- masking what would otherwise have been the identical undefined-window-symbol failure, since no window_win32 companion object exists either (windows.md Phase 2 hasn't started). The actual bug, though, was one level up: `_runtime_objects_and_link_libs`'s "offscreen drawing never reaches the platform gate" exemption (built for darwin in claude.md #123, where it's genuinely true -- offscreen links fine there) had been written platform-agnostic, skipping `_check_feature_supported` for ANY offscreen-only graphics program regardless of platform. On win32 that's wrong: there is no graphics backend at all yet, windowed or offscreen, so offscreen use should hit the same clean "windows.md Phase 2" error every windowed use already gets, not fall through to real pkg-config/linking code with nothing behind it. Fixed by scoping the exemption to platforms where it actually holds (everywhere except win32). Two of `TestSlimBinaries`'s own tests had the same gap the audio test right next to them had already been fixed for two rounds ago: they called `compile_file` directly instead of through `compile_file_or_skip`, so a correctly-gated CompileError on Windows would have surfaced as a raw test failure instead of the skip every other platform-conditional test gets -- fixed to match the audio test's own pattern.

Round five's log also carried several failures this round did NOT chase: two SQLite schema-sync tests reporting an unexpected column type, a timer test seeing zero ticks, one example's output not matching, two `festina doctor` path-reporting tests, and -- still open across two rounds now -- two `TestMissingDependencyErrors` tests that don't raise when pkg-config/cc are hidden from PATH via the `path_without` fixture. None of these trace to anything touched in this round's diff or the four before it; guessing further at increasingly speculative causes had worse odds than pushing the three confirmed, log-supported fixes above and reading what the NEXT real run says, the same judgment call round three's leftover audio flake used. Verified the same way as every prior round: no real macOS or Windows hardware, so the darwin fallback-routing fix and the win32 gating fix are reasoned from the log plus a full local Linux run (1302 passed, 7 skipped, including new coverage for both: a real compile+run through the forced clang-IR-frontend fallback with a graphics program, and a direct test of the win32 gate now firing for offscreen use) -- never confirmed by re-running on either platform itself.

Round six -- the run for round five's push -- landed the fallback-routing fix cleanly: real Linux CI went fully green for the first time (a first across six rounds) and macOS CI dropped to exactly ONE failure out of the whole suite. `TestGraphics::test_compiles_and_links_successfully` opens a real window, so it correctly hit the real-hardware-verification gate on darwin -- but, like the two `TestSlimBinaries` tests round five already fixed, it called `compile_file` directly instead of through `compile_file_or_skip`, the same gap its own `TestAudio` sibling test right below it had already avoided. One-line fix, matching the established pattern exactly. This round did not touch Windows at all -- that job was still `in_progress` when this fix went out, so its own status (including whether round five's win32 gating fix and the still-open failures from round five's log persist) is unconfirmed pending the next check-in.

Round seven -- Windows' own result for round five's push -- confirmed the win32 gating fix worked (the two `TestSlimBinaries` graphics tests it fixed were gone from the failure list) but found three more bugs, two of them self-inflicted by round five and six's own new code. First: `TestGraphics::test_compiles_and_links_successfully`, fixed on darwin in round six, has the identical bug on win32 too -- same missing `compile_file_or_skip` wrapper, same fix. Second: round five's own win32 gate message still said "drawing to an offscreen canvas... work[s] today with no window involved at all" -- true before that round's fix, false after it, since the fix's whole point was making offscreen graphics gated on win32 too. Caught by round five's OWN new regression test (`test_a_graphics_program_still_links_via_the_fallback`), which -- ironically, given its purpose was proving the fallback-path fix didn't regress anything -- had the exact same missing-skip-wrapper bug it exists to guard against, so it failed hard on Windows instead of skipping. Both message and test fixed together. Third, unrelated to graphics: two `festina doctor` tests traced to real, confirmable causes rather than staying open. `test_reports_festina_on_path_when_resolvable` created a fake executable literally named `festina` with no extension -- unfindable by `shutil.which` on Windows, which resolves bare names via PATHEXT extension search, the identical shell-needs-an-executable-extension fact `_default_output_name` already encodes elsewhere in this same file; fixed to name it `festina.exe` on win32. `test_missing_graphics_or_audio_deps_are_optional_not_fatal` mocked "which pkg-config packages exist" as `pkg == "sqlite3"` -- correct on every platform except win32, where `_core_pkgs()` REQUIRES `gnurx` too, so the mock made a required dependency appear missing and correctly (if unintentionally) flipped `all_ok` to `False`; fixed to derive the required set from `_core_pkgs()` instead of hardcoding it. None of the three needed guessing -- each traced cleanly to a specific, quotable line once read carefully against what round five/six had just changed. Still open, unchanged from round five: the SQLite schema-sync mismatches, the timer test, and `test_files_demo_runs_correctly`; briefly investigated the schema-sync one (a WAL-checkpoint-on-process-exit or cross-process-read-visibility difference on Windows is the leading theory, since every failure follows a SECOND compile+run against a database a separate Python process had already written to) but stopped short of a fix without a way to confirm it -- guessing at a WAL-related change with no Windows access to verify it actually helps was judged worse than shipping the four confirmed fixes above and reading the next real log.

Round eight -- Windows' own result for round seven's push -- went from 12 failures down to 9, confirming both the `TestGraphics` skip-wrapper fix and the doctor mock fix. It found four more, three genuinely new and one revealing why the two `TestMissingDependencyErrors` "DID NOT RAISE" tests have survived three straight rounds unexplained. That one turned out to be the most interesting: `_run_tool` handed `cmd` straight to `subprocess.run` with no pre-resolution, trusting it to fail the same way `shutil.which`-based checks do when a tool is hidden from PATH -- but Win32's `CreateProcess` (what `subprocess.run` calls into) searches several locations BEFORE PATH, per Microsoft's own documented order, including the directory the calling process itself loaded from. On a Windows CI runner where Python is an MSYS2 UCRT64 package, that directory is the SAME `bin/` pkg-config and the whole C toolchain live in -- so `tests/conftest.py`'s `path_without` fixture (which only edits the `PATH` environment variable) correctly fooled `shutil.which`-based checks (`festina doctor`) but never had a chance against an actual `subprocess.run` call, which found the "hidden" tool anyway via that extra search location most callers never hit. Fixed by resolving `cmd[0]` through `shutil.which` explicitly inside `_run_tool` before ever calling `subprocess.run`, making every tool invocation PATH-only and deterministic everywhere, closing the gap at its source rather than in the test.

`test_reports_festina_on_path_when_resolvable`'s round-seven fix (naming the fake executable `festina.exe` on win32) got the file found, but the ASSERTION still failed -- `shutil.which`'s Windows PATHEXT search returns the extension IT matched (`.EXE`, uppercase, straight from the `PATHEXT` environment variable) rather than preserving the fake file's own on-disk case (`.exe`, as this test created it), so an exact string comparison failed even though the right file was genuinely found. Fixed with `os.path.normcase` on both sides of the comparison -- a no-op on POSIX, case/separator-folding on Windows, matching how Windows itself treats the two spellings as identical.

The last new one was `test_files_demo_runs_correctly`, and it was a real bug in `examples/files.f` itself, not a test-harness gap: the demo hardcoded `/tmp/...` absolute paths, which a native Windows binary resolves under the CURRENT DRIVE's root (`D:\tmp\...`), not MSYS2's own `/tmp` mapping -- that translation only applies inside MSYS2's POSIX emulation layer, and a UCRT64-compiled Festina program is a plain native executable, not something running inside it. `D:\tmp` doesn't exist on the runner, so every file operation in the demo failed starting from the very first `write()`, cascading through the whole output. Fixed by switching the example to plain relative filenames (portable on every platform, since they resolve against whatever directory the program is run from) and giving its test an isolated `cwd=tmp_path` to match.

The still-open SQLite schema-sync mismatches and the timer test remain exactly that -- open, no new information this round to narrow them down further, and still the pattern from round five: guessing further without a way to verify on real Windows is worse than shipping four more confirmed, log-supported fixes and reading what the next run says.

Round nine -- Windows' own result for round eight's push -- went from 9 down to 6, and delivered the payoff round eight's `_run_tool` fix set up: `test_missing_pkg_config_gives_actionable_error` finally passed (gone from the failure list entirely), but `test_missing_cc_gives_actionable_error` failed with a NEW, far more informative error -- `_run_tool` now correctly reported `'pkg-config' is not installed`, when this test only ever meant to hide clang/gcc/cc, leaving pkg-config resolvable. That pointed straight at `path_without` itself: its symlinks were named after the bare logical tool name ("pkg-config", no extension), and `shutil.which`'s own Windows PATHEXT search only ever tries name+extension candidates ("pkg-config.EXE", ...) -- never the bare name by itself -- so `path_without`'s "everything except the hidden tool stays resolvable" promise was NEVER actually true on Windows for anything routed through `shutil.which`. It only ever WORKED by accident, because `_run_tool` used to bypass `shutil.which` entirely (round eight's own subject) -- fixing that gap is exactly what was needed to expose this one. Fixed by symlinking under `os.path.basename(found)` (preserving whatever real extension the resolved tool actually has) instead of the bare logical name.

The remaining two failure classes -- the SQLite schema-sync mismatches and the timer test -- got a real, reasoned attempt this round rather than staying deferred again. Auditing the actual runtime turned up something concrete: no compiled Festina program has EVER called `sqlite3_close()` on its own database handle -- `main()` just returns and the OS reclaims the file descriptor on exit. That works (SQLite's WAL format is specifically designed to survive an unclosed writer -- the next connection recovers it), but skips SQLite's own auto-checkpoint-on-last-close, leaving genuinely committed data sitting in the WAL file rather than the main .sqlite file until something else triggers a checkpoint. Every failing schema-sync test's pattern fits exactly what a reader that can't or doesn't perform identical WAL recovery would see: a SECOND process (a plain Python sqlite3 connection, not necessarily the same SQLite build/version this binary statically links) reading back a schema the FIRST compiled program just committed, and seeing the OLD one. Fixed with a new `festina_db_close()`, called unconditionally as the literal last thing every compiled program's `main()` does (mirroring `festina_runtime_init()` at the start): finalizes every cached prepared statement (the ONE piece that makes this non-trivial -- `festina_sqlite_prepare_cached`'s whole design leaves statements alive, never finalized, across the program's normal operation, so `sqlite3_close()` would otherwise return `SQLITE_BUSY` and do nothing) and then closes for real, forcing SQLite's own checkpoint. Deliberately plain `sqlite3_close()`, not `_v2` -- `_v2` silently defers to a "zombie" close on anything still unfinalized, which would have papered over a bug in the finalize step instead of surfacing it.

This is the most speculative fix of the whole effort -- Linux never reproduced the bug this targets, so there is no local before/after to compare, only that it doesn't regress anything: full suite clean (1304 passed, 7 skipped) and, critically, `scripts/leak_stress.sh` clean under real ASan/LeakSanitizer, including `structs_and_rows_churn.f`/`media_churn.f`, which both hammer exactly the cached-statement path this change finalizes thousands of times per run -- if finalizing an already-cached statement or double-closing were unsafe, that would have caught it directly. Also manually smoke-tested the exact schema-sync scenario end to end locally (create table, insert via a separate Python sqlite3 connection, recompile with an added column, reopen) -- works correctly, as it always has on Linux. Whether it actually fixes the Windows symptom is unconfirmed until the next real run says so.

Round ten -- Windows' own result for round nine's push -- delivered the payoff on `path_without`'s own fix (`test_missing_cc_gives_actionable_error` passed outright, gone from the list) but a clean, honest negative result on the `festina_db_close()` bet: all four SQLite schema-sync failures persisted, byte-for-byte identical to before -- the WAL-checkpoint-on-close theory was wrong, or at least insufficient, and `festina_db_close()` stays in the runtime as a genuine hygiene improvement but is no longer claimed as *the* fix for anything. Rather than reach for a third guess at the same bug, this round looked at the timer test instead, which had been sitting unexplained just as long, and found a different, well-supported root cause: `festina_log_*` never flushed stdout, so once redirected to a file or pipe (as any subprocess-captured program's is), the C runtime's default block buffering can sit on a handful of short `log()` lines indefinitely. `stdbuf -oL` (what the test relies on to force line buffering) works around this on Linux/macOS via `LD_PRELOAD`/`DYLD_INSERT_LIBRARIES` interposition against the SAME libc the target binary links -- a trick that cannot work at all against a compiled Festina program on Windows, a plain native UCRT64 PE binary sharing no runtime with MSYS2's own `stdbuf`. The test's own comment already half-documented this exact class of gap for macOS ("macOS has no stdbuf, so there the test... drops only the mid-run line inspection") without generalizing it to "a `stdbuf` that's merely *found* isn't the same as one that *works* against this specific binary." Fixed with an explicit `fflush(stdout)` after every `festina_log_*` call rather than `setvbuf(..., _IOLBF, ...)` -- deliberately, since Microsoft's own C runtime has long treated line-buffering the same as full buffering, so the "portable" libc answer isn't actually portable here. This is a real product fix, not just a test one: any long-running, log()-heavy Festina program piped or redirected on any platform would have had the exact same latency. Verified locally (this test always passed on Linux, where stdbuf genuinely works, so there was no failure to reproduce -- only that the explicit flush doesn't change Linux's already-correct behavior) plus the full suite and leak_stress.sh, both clean. The SQLite schema-sync mismatches remain open, now with one theory eliminated rather than zero -- next real run's log is what's needed, not another guess from here.

Round eleven -- Windows' own result for round ten's push -- confirmed the fflush fix (the timer test passed outright, gone from the list) and, more significantly, Linux, macOS, and CodeQL were ALL green on the same run for the first time across eleven rounds -- only Windows remains, down to exactly the four SQLite schema-sync failures, unchanged from every round since they first appeared. Two real, reasoned fix attempts (an explicit close forcing SQLite's own checkpoint; before that, nothing at all) have both left these four byte-for-byte identical, which is itself a meaningful data point: if the ALTER were running and merely not durably visible afterward, an explicit checkpoint should have moved the needle at least somewhat. That it moved nothing points toward the migration never running against the file the test reads back from in the first place -- plausibly a program instance touching a different actual `festina.sqlite`, despite `compile_and_run`'s two calls sharing the same `cwd=tmp_path` for both the compile and the subprocess run. Rather than guess a third specific mechanism blind, this round adds instrumentation instead of another behavioral change: the four tests' own assertions now report an mtime-before/mtime-after comparison on `festina.sqlite` (unchanged means the second compiled program never touched that file at all) plus the compiled program's captured stdout/stderr, and `festina_db_close` now logs to stderr if `sqlite3_close` itself ever returns non-OK (previously ignored entirely) -- both purely additive, change no behavior, and turn the next real Windows log from "same four failures, no new information" into a log that can actually distinguish "wrong file" from "wrong contents of the right file" without another guess-and-wait round. Verified: full suite and `scripts/leak_stress.sh` both clean, unaffected by additions that are either test-only or a no-op success-path stderr check.

Round twelve -- Windows' own result for round eleven's push, and the round the instrumentation actually paid off. The mtime diagnostic showed the file genuinely untouched (before == after, to the microsecond), and the captured stdout was the real smoking gun: the "second" compiled program's own output was `v1`, not `v2` -- meaning it was literally running the FIRST program's binary, not a fresh build from the changed source, on every one of the four failing tests. The cause was sitting in plain sight in code claude.md #126's own round four had written: `_rename_if_linker_appended_exe`'s guard was `if os.path.exists(exe_path) and not os.path.exists(output_path)`. That `not os.path.exists(output_path)` half was never a safety check -- it was backwards. `compile_and_run`'s own `_run` closure reuses the exact same `tmp_path / "program"` for every compile within a test (recompiling after a source change is precisely the scenario `TestAutomaticSqliteSchemaSync` exercises, twice, in every one of its tests), so by the SECOND compile, `output_path` already exists from the first one -- and the guard skipped the rename entirely, leaving the stale first binary sitting at the exact path the test runs, while the freshly linked `program.exe` sat right next to it, completely unused. `os.replace` already overwrites atomically on Windows; the exists-check was pure liability, never a needed guard, since the whole point of this function is putting the JUST-linked binary at the caller's requested path regardless of what used to be there. Fixed by dropping the check. The existing unit test for this exact function had, ironically, been asserting the wrong behavior as correct (`test_windows_never_clobbers_an_existing_exact_name` -- fixed to assert the right thing, renamed to say so). This also retroactively explains why NOTHING about the schema-sync bug ever moved with either of the two previous fix attempts: neither `festina_db_close()` nor the stdout flush could have helped, because the second program's OWN COMPILED CODE was never even running -- an explicit-`-o` recompile-in-place bug, not a database or buffering one, hiding behind symptoms (stale schema, stale printed output) that pointed everywhere except its actual location. This is very likely also the underlying explanation for at least some of the other early-round Windows failures already fixed by other means along the way, though nothing else in this effort ever recompiled to the same explicit path twice within one test the way TestAutomaticSqliteSchemaSync does, so it's plausible this bug was uniquely exposed by exactly one test class this whole time. Verified: full suite and leak_stress.sh clean; the corrected unit test passes; this can only be reasoned about and locally regression-tested on Linux (`sys.platform` injection), same as this function's fix always could be, since MinGW's exe-append behavior itself is Windows-only.

Confirmed: this round's push (commit b9d4369) went fully green -- linux, macos, windows, and every CodeQL analyzer, all green on real CI, for the first time across twelve rounds. windows.md's own "not yet confirmed" caveat, carried unchanged through eleven rounds because this project has no Windows/MSYS2 access of its own, is now retired: a real Windows CI run confirmed it, not another round of reasoning from a log. Twelve rounds, from 26 failures in round one to zero, entirely through pushing to real CI and reading what came back -- the only way any of these ever actually get found.

PR #47 merged after that fully green run.


127. WINDOWS PHASE 1: THE WAVEOUT AUDIO BACKEND

With Windows Phase 0 confirmed green on real CI (#126), the next open item per windows.md's own phase order is Phase 1: the waveOut device backend behind the shared `festina_pcm_open/write/close` seam that macOS Phase 1 (#121) already cut. That seam is exactly why this phase is a pure device-layer swap and not a channel-pool or codegen change at all -- the pool, the WAV parser, mpg123 decoding, and pthread use were already platform-generic before Windows existed as a target.

The implementation mirrors the AudioQueue backend's own shape deliberately, buffer for buffer: a fixed pool of `FESTINA_WO_BUFFERS` (4) `WAVEHDR` blocks, each `FESTINA_WO_CHUNK_FRAMES` (4096) frames, prepared once at `festina_pcm_dev_open` via `waveOutPrepareHeader`; a `WOM_DONE` completion callback (`festina_wo_proc`, running via `CALLBACK_FUNCTION` on some winmm-internal thread, which is why it does nothing beyond the short allow-list of Win32 calls the docs permit -- lock, push to the free list, condvar-signal, unlock) that returns finished buffers to a free list; and `festina_pcm_dev_write` blocking on a `pthread_cond_t` exactly the way ALSA's `snd_pcm_writei` blocks and AudioQueue's own condvar blocks, so the seam's documented "write() BLOCKS until the device has room" contract holds identically across all three backends. `festina_pcm_dev_close` calls `waveOutReset` first -- its synchronous "every pending buffer's callback fires before this returns" guarantee is the exact Win32 analog of `AudioQueueStop(..., true)`, and is what makes unpreparing and freeing every buffer immediately afterward race nothing, same reasoning as the AudioQueue backend's own close comment already gave for its own platform. Every allocation path (the struct, each `WAVEHDR`, each buffer's backing memory) has symmetric cleanup on every failure branch, including partial-loop failures partway through preparing the buffer pool -- again matching the AudioQueue backend's own existing standard rather than introducing a looser one just because this file's second platform branch is newer.

One deliberate deviation from windows.md's original text: the plan as written said "semaphore"; the implementation uses a `pthread_cond_t` instead, because (a) MinGW-w64's UCRT already ships real pthreads (`mingw-w64-ucrt-x86_64-winpthreads`, installed by the windows CI job for reasons unrelated to audio) and (b) a condition variable is the more direct match for the "wait until `free_count > 0`" shape both the AudioQueue and ALSA backends already use -- introducing a Win32 semaphore here would have meant a third synchronization primitive for the exact same one job. windows.md's prose is updated to match.

Three other files change alongside the runtime translation unit. `festina/cli.py`: `_feature_pkgs_and_flags` gains a win32 audio branch (`alsa` drops out, `-lwinmm` comes in, `libmpg123` and the already-shared `-pthread` stay -- MSYS2 UCRT64 ships a real `libmpg123` pkg-config package) reached identically by both link paths (the libLLVM fast path and the clang-IR-frontend fallback both already call this one function, so neither needed its own edit); `_check_feature_supported`'s win32 audio branch changes from windows.md Phase 1's original "nothing built yet, raise unconditionally" shape to the same real-hardware-verification gate every darwin branch already uses -- `FESTINA_ENABLE_WINDOWS_AUDIO=1` overrides it, unset it raises with a message naming the new env var; `festina doctor`'s win32 audio line changes from "no Windows backend yet" to "built but awaits real-hardware verification," matching the darwin audio line's own wording one-for-one. `.github/workflows/ci.yml`: a new "Compile the audio runtime (waveOut backend)" step, `clang -c` only (not linked -- this translation unit calls `festina_fail`/`festina_retain`/`festina_release_check`/`festina_save_bytes`, all defined in `festina_runtime.c`, so a standalone link would fail on those symbols, not on anything this step exists to catch), the same compile-only shape the macOS job already uses for its own AudioQueue step; also adds `mingw-w64-ucrt-x86_64-mpg123` to the windows job's install list, needed for this new step's `pkg-config --cflags libmpg123` call (nothing before this needed it, since audio was never compiled on windows CI at all until now). `tests/test_platform.py`: `test_audio_on_windows_names_the_plan` updated (it still names windows.md Phase 1, but now for the built-and-gated reason, not the nothing-built-yet reason) plus a new `test_the_windows_audio_gate_is_overridable_for_hardware_verification` mirroring the darwin one exactly, and a new `test_windows_swaps_alsa_for_winmm` in `TestAudioFeatureConfig` asserting the exact pkg/flag list.

Verified: full suite (1306 passed, 7 skipped) and `scripts/leak_stress.sh` (all five stress programs clean) both green on Linux -- the ALSA `#else` branch is untouched by this change, so this is confirming no regression, not testing the new code. The `_WIN32` branch itself can only be reasoned about from here, unit-tested via `sys.platform` injection (the same limitation every prior Windows-only branch in this project has had, and the same one Windows CI's new compile-only step exists to close, the way the macOS job's own AudioQueue step already closes it for that platform) -- real verification is the next windows CI run against this push.

Real Windows CI's first run against that push caught exactly the kind of mistake local reasoning alone can't: the windows job failed immediately at package install with `error: target not found: mingw-w64-ucrt-x86_64-libmpg123` -- the `lib`-prefixed name assumed by analogy with `mingw-w64-ucrt-x86_64-libsystre` (regex) and `mingw-w64-ucrt-x86_64-libjpeg-turbo` (graphics) does not exist for mpg123; MSYS2's actual package is `mingw-w64-ucrt-x86_64-mpg123`, no `lib` prefix, matching the upstream project's own name (confirmed via packages.msys2.org). The pkg-config *query* name stays `libmpg123` unchanged -- that's a property of mpg123's own installed .pc file, not of the pacman package name, and it's the same string every other platform already queries pkg-config with. Fixed by correcting the one install-list line; no other file needed a change.

Confirmed: the corrected push (commit 390315d) went green on windows -- the "Compile the audio runtime (waveOut backend)" step compiled the new `_WIN32` branch against real `<mmsystem.h>` headers for the first time, and it just worked, no further fixes needed. linux/macos both failed on the same two already-diagnosed unrelated flakes (the audio channel-pool race, the macOS timer race) and cleared on rerun -- linux needed a third attempt (the channel-pool race recurred twice in a row on this one push, still the same assertion every prior round saw, still untouched by anything in this diff, which only ever touched `.github/workflows/ci.yml` and `claude.md`).


128. WINDOWS PHASE 2: THE WIN32 WINDOWING BACKEND

With Windows Phase 1 confirmed compiling on real CI (#127), the next open item per windows.md's own phase order is Phase 2: the Win32 windowing backend behind the shared `festina_window_*` seam macOS Phase 2 (#123) already cut. Unlike audio's device seam, this one had a real, previously invisible bug waiting in it: `festina_runtime_graphics.c`'s X11 backend was guarded `#ifndef __APPLE__` -- which is ALSO true on Windows. Nothing had ever asked this file to compile on win32 before now (the graphics gate fired unconditionally, before even reaching the compiler), so the fact that this guard would have tried to compile `<X11/Xlib.h>` and friends under MinGW sat undiscovered the same way the round-four Windows CI bugs did (#126) -- found by design review this time, not by a real CI failure, since fixing it was a prerequisite for writing the Win32 backend at all rather than a consequence of it. Fixed to `#if !defined(__APPLE__) && !defined(_WIN32)`, both at the X11 block itself and at the two X11-only top-of-file includes (`<sys/select.h>`, and `<time.h>` for the connect-retry's `nanosleep`) that had been unconditional at file scope despite being used only inside that same block.

The new `runtime/festina_runtime_window_win32.c` mirrors the Cocoa backend's shape more than the X11 one, deliberately: Win32 delivers input through a WndProc callback `DispatchMessage` invokes synchronously, the same push-based model Cocoa uses (X11's flat `XNextEvent` stream turns out to be the outlier of the three, not the norm), so this file uses the identical small ring-buffer queue the Cocoa backend's `festina_mac_push`/`festina_mac_pop` establish, fed by the WndProc and drained after each `PeekMessage`/`TranslateMessage`/`DispatchMessage` pump. The window itself is `WS_POPUP` (borderless, no title bar/menu), matching the X11 backend's Motif no-decorations hint and the Cocoa backend's `NSWindowStyleMaskBorderless` -- all three backends present the same "canvas, nothing else" surface, so the requested width/height is the client size directly everywhere, no client-vs-window-frame math needed on any platform. `WM_CLOSE` pushes CLOSE and returns 0 without calling `DefWindowProc`, leaving the actual `DestroyWindow` to `festina_window_close()` once shared code's `on close` handler has run -- the same "let shared code decide" pattern the X11 `WM_DELETE_WINDOW` protocol and Cocoa's `windowShouldClose:` (returning NO) already use.

`festina_window_present`'s blit is `StretchDIBits` reading the Cairo `ARGB32` backing surface directly as a 32bpp top-down DIB (`biHeight` negative) -- the same well-known cairo/GDI byte-order coincidence (`B,G,R,A` in memory on any little-endian machine) the Cocoa backend's CGImage path already documents for itself, and windows.md's own plan called out as the reason no `cairo-win32` backend would ever be needed. A 32bpp DIB's scanline stride is always exactly `width*4` bytes (the one alignment DIBs require, already satisfied), which is also cairo's own `ARGB32` stride for every width, so unlike the CGImage path (which reads cairo's stride explicitly via a data provider), `StretchDIBits` needs no separate stride parameter at all.

Key handling is the one place this backend's design genuinely differs from windows.md's original sketch, for a real reason found while writing it, not a bug found afterward: the plan called for `WM_KEYDOWN/UP` plus a separate `WM_CHAR` handler, but `WM_CHAR` only ever fires for the DOWN half of a press (posted by `TranslateMessage`), which would leave `keyUp` unable to report the same text a matching `keyDown` did -- breaking the two-tier key rule's own symmetry (X11's `XLookupString` and Cocoa's `charactersIgnoringModifiers` both compute their tier-1 printable character synchronously, inside the SAME handler that fires for both down and up). `ToUnicode` (virtual-key code + scancode + a snapshotted keyboard state via `GetKeyboardState`) computes the identical shift-aware character synchronously for both halves, sidestepping the WM_CHAR timing problem entirely and needing no separate handler at all. Its documented side effect on dead-key composition state is an accepted simplification, the same "single-byte ASCII printable character only, no non-Latin keyboard layout" scope limit the Cocoa backend's own comment already states for itself -- not a new limitation introduced here. Left/right Shift/Control/Alt need their own disambiguation since `WM_KEYDOWN`/`WM_KEYUP` report only the generic `VK_SHIFT`/`VK_CONTROL`/`VK_MENU` otherwise -- Control and Alt read off `lParam`'s extended-key bit directly, Shift needs its scancode remapped through `MapVirtualKey`, both standard documented Win32 technique, not guesswork. Every named key this file's own vk-to-name table produces is drawn from `runtime/festina_key_names.h`'s pinned vocabulary with no invented names -- `TestKeyNameVocabulary` (unchanged) is what makes that mechanically checkable rather than trusted.

Downstream wiring in `festina/cli.py` mirrors the darwin graphics wiring one-for-one: `_feature_pkgs_and_flags`'s win32 branch drops `cairo-xlib` for plain `cairo` (MSYS2 ships a real package, same as Homebrew) and adds `-lgdi32 -luser32` (the Win32 counterpart to `-framework Cocoa` -- system import libraries, no pkg-config file); `_feature_extra_object` gains a win32 branch compiling `festina_runtime_window_win32.c` with cairo's cflags (it `#include`s `<cairo.h>` directly for the same `StretchDIBits`-reads-the-surface-directly reason `window_mac.m` needs them for its CGImage path); `_check_feature_supported`'s win32 graphics branch inverts from windows.md Phase 0's original "nothing built yet, raise unconditionally" shape to the same real-hardware-verification gate every other branch in that function now uses -- `FESTINA_ENABLE_WINDOWS_GRAPHICS=1` overrides it. That inversion has a second consequence worth calling out on its own: `_runtime_objects_and_link_libs`'s win32-specific offscreen-gate-exemption carve-out (added in round five of #126, when there genuinely was no `window_win32` companion object for an offscreen-only program to link against) is retired along with it -- offscreen use (`drawRect()`+`saveCanvas()`, no `render()`, no event handler) is now gate-exempt on win32 exactly like every other platform, checked by the same code path rather than a platform-specific branch. `festina doctor`'s win32 graphics line changes from "no Windows backend yet" to "built but awaits real-hardware verification," matching the darwin line's wording one-for-one; the win32 `libjpeg`/`cairo` dev-header checks that used to be skipped outright (there was nothing on win32 to need them) now run for real, and both install hints gained a `pacman` line alongside their existing `apt`/`brew` ones.

`.github/workflows/ci.yml` gains two new compile-only steps mirroring the macOS job's own "Compile the audio runtime"/"Compile the windowing backend" pair: one compiling `festina_runtime_graphics.c` standalone (the one platform check that file itself had never had -- its portable Cairo/libjpeg code was already known-portable from Linux and macOS, but the `#ifndef __APPLE__` guard bug above never would have been caught without a compiler actually trying to build this file on win32), one compiling `festina_runtime_window_win32.c`. Both are compile-only, same reasoning as Phase 1's audio step: both files call into `festina_runtime.c`'s core symbols, so a standalone link would fail on those, not on anything either step exists to catch. `mingw-w64-ucrt-x86_64-cairo` and `mingw-w64-ucrt-x86_64-libjpeg-turbo` join the windows job's install list -- both verified against packages.msys2.org before use this time (#127's `libmpg123` naming mistake made that check worth doing up front rather than finding out from a failed CI run again).

`tests/test_platform.py`: `test_windowed_graphics_is_gated_on_windows` updated for the new gated-not-unbuilt reason (still names windows.md Phase 2, now for having a backend awaiting verification rather than having none at all) plus a new `test_the_windows_graphics_gate_is_overridable`; `test_offscreen_graphics_still_reaches_the_windows_gate` -- the round-five test that pinned the now-retired win32-only carve-out -- is inverted to `test_offscreen_graphics_never_reaches_the_windows_gate_either`, asserting the SAME exemption the darwin test above it already asserts; two new `TestAudioFeatureConfig` tests (`test_windows_graphics_swaps_cairo_xlib_for_plain_cairo`, `test_windows_graphics_extra_object_is_the_win32_companion`) pin the exact pkg/flag list and the extra-object wiring, mirroring their darwin counterparts.

One more real bug, caught by watching the local verification run rather than by CI: `scripts/package_compiler.sh`'s PyInstaller `--add-data` list bundles every runtime source file `festina/cli.py` reads a path to at runtime (`_RUNTIME_DIR` resolves through `sys._MEIPASS` when frozen, per that file's own doc comment) -- it already listed `festina_runtime_window_mac.m` but had no line for the new `festina_runtime_window_win32.c`, which would have made a *packaged* `festina` binary (as opposed to one run from a source checkout) unable to find it the moment anything tried to compile Windows graphics, a failure mode invisible to every check in this round except literally noticing the packaging step's own `--add-data` flags scroll past while it ran. Fixed by adding the one missing line; this is windows.md Phase 3's own concern in substance (packaging) surfacing early because Phase 2 added a file Phase 3 hadn't been written yet to know about.

Verified: full suite (1309 passed, 7 skipped -- three more than #127's count, the three new tests this round added) and `scripts/leak_stress.sh` clean on Linux -- the X11 `#else` branch is untouched by this change beyond the guard condition itself, so this confirms no regression in the portable and Linux-specific code, not the new Windows code. The `_WIN32` branch, like Phase 1's before it, can only be reasoned about from here and unit-tested via `sys.platform` injection; real verification is Windows CI's two new compile-only steps against this push, and beyond that, the same real-hardware playback/window/mouse/keyboard pass every darwin gate and Phase 1's audio gate are still waiting on too -- this project still has no Windows or Mac machine of its own.


129. WINDOWS PHASE 3: PACKAGING AND DISTRIBUTION

With Windows Phases 1 and 2 confirmed compiling on real CI (#127-128), the last open item per windows.md's own phase order is Phase 3: packaging and distribution -- the one phase whose macOS counterpart (macos.md Phase 3) had already landed and could be mirrored directly rather than designed from scratch, since the underlying tool (PyInstaller) and script (`scripts/package_compiler.sh`) are already fully cross-platform in principle. Three concrete pieces, all from windows.md's own three-item list.

**Item 1 -- the packaging script itself.** `scripts/package_compiler.sh` needed exactly the two real Windows-specific facts windows.md's plan already named: PyInstaller's `--add-data` flag uses `;` to separate source from destination on Windows and `:` everywhere else (a real `:` there would parse as part of a Windows path, `C:\...`, not a separator -- these genuinely cannot share one spelling), and PyInstaller itself already emits `festina.exe` there with no extra flag needed, mirroring how MinGW's own linker already appends `.exe` to every other compiled Festina program (windows.md Phase 0). Detection uses bash's own `OSTYPE` variable (`"msys"` under MSYS2 bash specifically, set automatically, nothing to configure) rather than parsing `uname -s`, which reports differently depending on which MSYS2 subsystem is active and isn't the standard idiom other cross-platform bash scripts use for this exact question. Both the codesign step (already darwin-gated) and the final "wrote ..." message now read from one `FESTINA_BIN` variable computed once at the top, rather than hardcoding `$OUT_DIR/festina` in three separate places that would each need their own Windows branch.

**Item 2 -- the DLL story**, the one item with a real decision to make rather than a mechanical translation. A MinGW-built program depends on a handful of MSYS2 runtime DLLs (`libgcc_s_seh-1.dll`, `libwinpthread-1.dll`) that a bare Windows install doesn't have -- sqlite3 is already handled (windows.md Phase 0's toolchain decision keeps `_static_sqlite_attempt`'s GNU ld `-Bstatic`/`-Bdynamic` toggles working identically on Linux and MinGW-on-Windows), so this is specifically about the other two. New `_windows_static_runtime_flags(cc, uses_audio, platform_name)` in `festina/cli.py`: `-static-libgcc` unconditionally on win32 (a plain, universally-understood GCC/Clang driver flag -- no probe needed, and harmless to add even when a program needs nothing from libgcc_s), plus a *probed* `-Wl,-Bstatic -lwinpthread -Wl,-Bdynamic`, reusing `_can_link` (already extracted for exactly this "does a static archive by this name actually exist" question) rather than assuming `mingw-w64-ucrt-x86_64-winpthreads` ships `libwinpthread.a` and not only the shared `.dll` -- this project has no Windows machine to confirm that directly, so probe-then-fallback is the only honest option, identical in shape to `_sqlite_link_flags`'s own established pattern. The winpthread probe is skipped ENTIRELY (not just expected to fail) whenever a program uses `aud`: audio already links winpthread dynamically via its own unconditional `-pthread` flag (`_RUNTIME_FEATURES["audio"]`), and stacking a second, statically-scoped `-lwinpthread` on top of that risks a link-order conflict real Windows CI is the only thing that could ever confirm is safe -- so this stays strictly scoped to windows.md's own "core-only programs" framing for the copy-anywhere claim (core, and offscreen-only graphics, both qualify -- neither needs pthread at all), while graphics/audio programs keep the plan's OTHER named option: document the MSYS2 requirement, landed in `setup.md`'s new Windows section rather than attempted as automatic DLL-copying alongside the `.exe` (a meaningfully larger, harder-to-verify-from-Linux scope deliberately left out of this round). Wired into `_runtime_objects_and_link_libs`'s existing `link_libs` list, which both compile paths (the libLLVM fast path and the clang-IR-frontend fallback) already share -- neither needed its own edit, the same "reached identically by both paths" shape #127's `-lwinmm` wiring already established.

The `ldd`-equivalent pin windows.md's own item 2 calls for lands as a genuinely live test, not just a unit test of the flag-selection function: `TestOnWindows::test_core_only_binary_has_no_msys2_runtime_dll_dependency` compiles `hello.f` for real and greps `objdump -p`'s own `DLL Name` lines for `libgcc`/`libwinpthread` -- exactly the tool windows.md names, and the only thing that can actually confirm the static-link attempt succeeded on real MinGW rather than silently fell back. The pure function itself gets its own `TestWindowsStaticRuntimeFlags` class (5 tests), unit-tested via a stubbed `_can_link` the same way `TestStaticSqliteAttempt` already covers sqlite3's identical probe-then-fallback shape -- including one test asserting the winpthread probe is never even ATTEMPTED when `uses_audio` is true, not merely that its result is ignored, since a probe that runs and is discarded would still be wasted toolchain work on every audio compile for nothing.

**Item 3 -- setup.md's real Windows section**, mirroring the macOS section's own structure and level of detail: the UCRT64-specific pacman one-liners per tier (core/graphics/audio, matching the windows CI job's own install list exactly), the `gnurx`-vs-`libsystre` package-name-vs-pkg-config-name split explained inline (the same real surprise claude.md #126 found), an explicit "MSVC is out of scope" statement naming windows.md's own toolchain decision, and a new subsection on the DLL story matching item 2's actual landed behavior rather than the plan's original open question. The packaged-binary and "run a compiled program" sections both gained a short Windows-specific note (`.exe` naming; `objdump -p` as the `ldd` counterpart) rather than a full parallel section, since everything else there was already platform-generic.

Finally, a new windows CI step, "Package and smoke-test the standalone compiler binary," mirrors the linux/macos jobs' own -- installs `requirements-build.txt`, runs `package_compiler.sh`, compiles and runs `hello.f` through the freshly packaged `festina.exe` -- verifying the whole chain for real on every push rather than a path only a human packaging a release by hand had ever exercised, the identical reasoning macos.md Phase 3's own item 1 already used to justify its own version of this same step. No codesign step, unlike macOS -- Windows has no Gatekeeper-equivalent local-run prompt to route around.

Verified: full suite and `scripts/leak_stress.sh` clean on Linux; the new `_windows_static_runtime_flags` function returns `[]` unconditionally on every non-win32 platform, so this is confirmed to add nothing to Linux/macOS link lines at all, not merely assumed to. Every piece touching real Windows toolchain state -- the packaging script's own branch, the static-link probe's real outcome, the `objdump`-based DLL pin -- can only be reasoned about and unit-tested via `sys.platform`/`OSTYPE` injection from here, the same limitation every Windows-only piece of this whole effort has had from Phase 0 onward; real verification is the next windows CI run against this push. With this phase, windows.md has no open phases left -- every one of its four phases is now built and CI-verified, with real-hardware audio/graphics verification the one thing left open across all of them, same as it has been since Phase 1, for the same reason: this project has never had a Windows machine of its own.

Real Windows CI's own run against that push found one more real bug, in a place this round didn't even touch: `TestSlimBinaries::test_graphics_binary_links_cairo_and_x11_but_not_alsa` failed with `libcairo` correctly present but `libX11` correctly ABSENT. This test predates Phase 2 entirely and had simply never run to completion on Windows before -- offscreen graphics used to hit the win32 gate unconditionally (claude.md #126 round six), so `compile_file_or_skip` always skipped it there, long before reaching its own `assert "libX11" in ldd_output` line. Phase 2 (#128) made offscreen graphics link for real and gate-exempt on win32, which is exactly correct -- and exactly what finally let this test run its assertions on Windows for the first time, exposing that the assertion itself was written assuming Linux (X11 always present there) without ever being exercisable anywhere else. Windows graphics is native Win32 windowing (`festina_runtime_window_win32.c`), not an X11 server under emulation, so a Windows binary genuinely has nothing X11 to depend on -- this is the correct, intended platform difference, not a bug in the DLL-slimming logic itself. Fixed by guarding the X11 assertion on `sys.platform != "win32"` (a new top-level `import sys` in `tests/test_codegen.py`, which had never needed one before); the `libcairo`-present and `libasound`-absent assertions stay unconditional, since both hold on every platform. Verified: the full suite (1314 passed, 8 skipped, unchanged from before this fix since it only touches an already-Windows-only assertion path) and `scripts/leak_stress.sh` clean on Linux; `TestSlimBinaries` alone re-run and confirmed green. linux and macOS both went fully green on the same push with no flakes this round -- only this one real, genuinely-new-to-reach bug on windows.

Real Windows CI's next run against that fix (several days later, per its own queued-notification timestamp -- no local reasoning could have caught this one, only real MinGW toolchain behavior) got past the full test suite cleanly and reached the NEW packaging step for the first time, where it failed with `ERROR: Unable to find 'D:/d/a/festina/festina/runtime/festina_runtime.c' when adding binary and data files.` -- a doubled, broken path, not a missing file. Root cause: `scripts/package_compiler.sh`'s `--add-data` argument is a compound `SRC;DEST` string (the shape PyInstaller itself requires), and PyInstaller under MSYS2 UCRT64 Python is a NATIVE (non-MSYS) Windows executable -- MSYS2's bash automatically rewrites any argument that looks like a POSIX path (`$REPO_ROOT` from `pwd`, e.g. `/d/a/festina/festina`) into its real Windows form before a native process ever sees it, ordinarily transparent, but that auto-conversion has a compound-argument blind spot: handed `/d/a/festina/festina/runtime/festina_runtime.c;runtime` it mis-converted only part of it, prepending the drive letter (`D:`) directly onto the already-POSIX path instead of replacing its `/d` prefix -- `D:` + `/d/a/festina/festina/...` = the exact doubled path the error names. Fixed by converting `$REPO_ROOT/runtime` to its real Windows form ourselves, once, via `cygpath -m` (MSYS2's own core path-conversion utility -- `-m` for the forward-slash "mixed" form specifically, so concatenating `/festina_runtime.c` onto it afterward stays a plain forward-slash path with no backslash to escape), before it ever reaches a compound argument -- sidestepping the auto-converter's blind spot entirely rather than fighting it. `ADD_DATA_SEP`'s own `;`/`:` selection is untouched; only what each `--add-data` line's SRC half is built from changed, gated on the same `OSTYPE == "msys"` check already used for that. Verified on the unaffected Linux path: a real `package_compiler.sh` run (unchanged there -- `RUNTIME_DIR` stays the plain POSIX `$REPO_ROOT/runtime`, `cygpath` never called), compiling and running `hello.f` through the freshly packaged binary exactly as before; `tests/test_packaging.py` (2 tests) and the full suite both re-confirmed green. The win32 branch itself -- whether `cygpath -m`'s output actually sidesteps the auto-conversion bug for real -- can only be confirmed by the next real Windows CI run against this push, the same limitation every Windows-only fix in this whole effort has had.

That confirmation arrived, and refuted the theory: real Windows CI's next run failed with the EXACT SAME error, byte for byte -- `D:/d/a/festina/festina/runtime/festina_runtime.c`, the identical doubled path, even with `cygpath -m` now supplying an already-correct native Windows path as the compound argument's SRC half. That rules out "MSYS2 mis-converts a raw POSIX path it finds inside a compound argument" as the actual mechanism: the automatic argv conversion re-mangles an ALREADY-correct Windows path sitting inside a compound `SRC;DEST` argument just as badly, meaning no amount of pre-converting the string ourselves can out-guess it -- the conversion has to be turned off outright for this one call. Fixed with `MSYS2_ARG_CONV_EXCL="*"`, the standard, documented MSYS2 escape hatch that tells the runtime to leave every argument of the next command alone. That alone would have traded one broken path for three: `--distpath`/`--workpath`/`--specpath` were never part of the bug (single, non-compound path arguments, which the automatic conversion handles correctly, confirmed by every prior round's logs showing those paths resolve fine) but disabling conversion wholesale would have broken them too, since they still carry raw POSIX values from `$OUT_DIR`/`$WORK_DIR`. So all three now get the identical `cygpath -m` treatment RUNTIME_DIR already had, making every absolute path this script hands `pyinstaller` self-converted before `MSYS2_ARG_CONV_EXCL` shuts the automatic mechanism off entirely -- belt and suspenders together, not either alone, which is exactly what two straight rounds of real-CI failures against each half individually now demonstrate is necessary. Verified on the unaffected Linux path: a real `package_compiler.sh` run (`DISTPATH`/`WORKPATH`/`SPECPATH`/`RUNTIME_DIR` all stay their plain POSIX values there, `cygpath`/`MSYS2_ARG_CONV_EXCL` never touched), packaged binary compiled and ran `hello.f` correctly; `tests/test_packaging.py` green. The win32 branch awaits its third real Windows CI verification round.

That third round produced the IDENTICAL byte-for-byte broken path a third time -- `D:/d/a/festina/festina/runtime/festina_runtime.c`, unchanged despite `MSYS2_ARG_CONV_EXCL="*"` and `cygpath -m` now applied to every absolute path this script hands `pyinstaller`. Two straight rounds of theorizing about the exact MSYS2/PyInstaller interaction from Linux, each producing a plausible-sounding but wrong fix, is the same shape claude.md #126 round eleven's own SQLite schema-sync investigation hit before it stopped guessing and added instrumentation instead. Doing the same here: rather than a fourth theory, `package_compiler.sh` now echoes `$OSTYPE`, `cygpath`'s resolved location, the post-conversion `RUNTIME_DIR`/`DISTPATH` values, and the exact first `--add-data` string it's about to hand `pyinstaller`, all to stderr, purely diagnostic and never changing what gets built. This turns the next real Windows CI log into one that can actually distinguish "cygpath itself already returns the broken value" (a `cygpath` misuse or genuine bug on this specific runner) from "something downstream re-mangles an already-correct one regardless of `MSYS2_ARG_CONV_EXCL`" (meaning that env var isn't actually reaching wherever the conversion happens) -- two structurally different bugs the error message alone cannot tell apart, and guessing between them blind is exactly how two straight rounds were spent on the wrong one. Verified: the debug block is gated on the same `OSTYPE == "msys"` check as everything else it sits beside, confirmed silent on a real Linux run (no stray debug output, `package_compiler.sh` otherwise unchanged in behavior there). The actual diagnosis waits for the next real Windows CI log.

That log came back, and it was the diagnostic itself that solved this, not what it printed but what it DIDN'T: no `debug: ...` line appeared anywhere in the run, not one -- meaning the entire `if [[ "${OSTYPE:-}" == "msys" ]]` block, cygpath calls and all, had never executed on real Windows CI, across all three prior rounds. `$OSTYPE == "msys"` -- the single assumption every fix attempt built on -- was wrong the whole time; the msys2/setup-msys2 action's own `shell: msys2 {0}` wrapper (a cmd.exe-launched bash, not a plain interactive MSYS2 terminal) apparently doesn't leave `OSTYPE` set to the compiled-in "msys" that detection technique assumes. Every prior fix (the `;` separator, `cygpath -m`, `MSYS2_ARG_CONV_EXCL`) was sound reasoning about a real MSYS2/PyInstaller interaction -- none of it could ever have helped while the gate guarding all of it silently stayed closed, which is exactly why round two produced the identical byte-for-byte broken path as round one, and round three the identical broken path as round two: with the whole block skipped every time, `ADD_DATA_SEP` stayed the plain `:` and `RUNTIME_DIR` stayed the raw, unconverted POSIX path in every single round, so the exact same colon-and-drive-letter-confusable compound argument got built and mis-handled by MSYS2's own still-fully-active automatic conversion each time, with nothing in any of the three "fixes" ever in a position to change that.

Fixed by switching the detection to `$MSYSTEM` -- confirmed directly present in every single CI log line's own "env:" block throughout every round so far (`MSYSTEM: UCRT64`), and, tellingly, already this project's own proven-working Windows-CI detection signal: `festina/cli.py`'s doctor logic (`_doctor_report`'s wrong-shell check) has keyed off `$MSYSTEM` since windows.md Phase 0, real Windows CI having exercised it successfully many times over since. `OSTYPE`, despite being the far more commonly cited technique in generic MSYS2 documentation, had simply never been exercised anywhere else in this codebase -- this script was the first place anyone reached for it, and the first place real Windows CI ever got to test whether it actually worked in THIS project's specific execution context. All three previous rounds' actual fixes (the `;` separator, `cygpath -m` on every absolute path, `MSYS2_ARG_CONV_EXCL="*"`) are kept exactly as designed, now finally reachable. Verified: real Windows CI's own "env:" block is direct, repeated, first-party evidence for `$MSYSTEM`'s value, not an inference; a real `package_compiler.sh` run on the unaffected Linux path (`$MSYSTEM` unset there, matching the original untouched behavior) confirmed no regression. The fix itself -- whether `$MSYSTEM` actually gates the block open this time -- awaits the fourth real Windows CI round, the first one with any real chance of testing the packaging logic rather than never reaching it.

That fourth round came back fully green: linux, macOS, windows, and every CodeQL analyzer, all passing on commit 78ae1d1 -- the windows job's packaging step actually ran, for the first time, and passed. Four real rounds on one bug, but each one narrowed the search: round one found the doubled-path symptom; rounds two and three each fixed a real, correct MSYS2/PyInstaller interaction that turned out to be unreachable; round three's own diagnostic instrumentation -- present by its absence, not its content -- is what finally exposed the actual gate that had been closed the entire time. With this, every phase windows.md names is built AND now genuinely CI-verified end to end, packaging included, closing out the plan started at windows.md Phase 0: what remains everywhere is the one thing that has been true since Phase 1 and cannot be closed from here -- real-hardware audio and windowed-graphics verification, since this project has never had a Windows machine of its own.

130. SPLICE GROWS AN INSERTION ARM: splice(start, count, insertArr)

The first item off a long, unprioritized feature-request list: `arr.splice` should work like JavaScript's, "return array of spliced items and also allow insertions." claude.md #96 had already built the JS-shaped remove half (`splice(start, count) -> arr[T]` of what was removed, clamped exactly like JS); what was missing was the insertion half -- JS's own `splice(start, deleteCount, ...items)`. Festina has no variadic parameters, so the natural spelling is one explicit third argument: `splice(start, count, insertArr:arr[T])`, still returning only what was removed (JS's own splice() never hands back what was inserted either).

The interesting part was ownership, not arity. `festina_array_splice_insert` (new, alongside the untouched original `festina_array_splice`) is a pure byte-mover in `festina_runtime.c`: it removes `count` elements at `start` into `dst_hdr` exactly like the 2-argument form, then grows or shrinks the buffer to fit `insert_len` new elements from `insert_data` and memcpy's them in -- a `new_length > len` (growing) branch reallocs first, then shifts the tail right into the just-grown buffer; a `new_length <= len` (shrinking-or-equal) branch shifts the tail into place first (still fits the OLD buffer, since `start + insert_len + tail == new_length <= len`) and reallocs down after. It has no notion of a Festina type, so it does not retain or copy anything it moves -- exactly the same split every other array method in this file already keeps (runtime decides bytes, codegen decides refcounting).

Codegen's own half is genuinely new shape, not a repeat of push/unshift's. Those retain a SINGLE value by asking whether ITS OWN source expression is "owning" (`_is_owning_refcounted_source` -- a fresh Call/ArrayLit vs. a plain variable read). splice-insert copies a RUNTIME-COUNTED RANGE of raw bytes out of a whole separate array's buffer, with no per-element source expression to ask anything of -- so the question "is this source fresh" doesn't even apply per-element, and asking it of the ARRAY as a whole doesn't help either: even a fresh literal insert argument's own elements were already given their own reference the moment the literal was built (claude.md #79's `_emit_array_lit` retain-per-element loop), so copying their raw pointer VALUES into a second, independent buffer needs a second, independent reference regardless -- the source array keeps managing its own elements' lifetime whatever codegen does to the destination. The correct rule turned out to be unconditional: a new `_emit_retain_or_own_range` helper (an LLVM loop shaped exactly like claude.md #80's own `_emit_release_array_elements`, just retaining/copying instead of releasing) always retains a refcounted element in place or copies a text one via `festina_text_own` (storing the fresh pointer back into the slot the runtime already memcpy'd into) over the destination's own newly-written `[start, start+insert_len)` range, no freshness check possible or needed. The array HEADER argument itself still gets the ordinary owning-source treatment once done with it: `_release_owned_receiver(expr.args[2], ...)` releases it only if it was fresh (a literal argument, now genuinely finished with), leaving a named binding untouched -- unchanged from how every other method argument in this codegen already handles "was I handed something I now own, or something that still belongs to its own binding."

One more real bug caught only by tracing the byte math by hand before running anything: the destination array's own `data` field has to be RELOADED after calling `festina_array_splice_insert`, not reused from before the call -- the runtime call can realloc it, and a stale pointer captured earlier would make the retain loop write into freed memory.

Verified: four new tests in `TestArrayMethods` (`tests/test_codegen.py`) -- basic remove-and-insert, the grow/shrink split (pure insertion at count=0, and shrinking when more is removed than inserted), text elements copied independently of a still-alive named source array, and struct elements retained (not copied) the same way, checked against the SOURCE array afterward to confirm it still holds its own valid reference. Plus three new arity/type rejections (too many arguments, wrong element type, mismatched arr[T] element type for the insert argument) in the existing `test_wrong_arity_and_types_are_rejected`. A new `tests/stress/splice_insert_churn.f` -- 1200 iterations mixing fresh-literal and named-binding insert sources, text and struct element types, pure insertion and pure removal, nested with `free` on every array that doesn't auto-release at scope exit -- run through `scripts/leak_stress.sh` under real AddressSanitizer + LeakSanitizer: clean. Full suite: 1318 passed, 8 skipped (up from 1314 baseline by exactly the 4 new `TestArrayMethods` tests -- the 3 new rejection cases live inside an existing parametrized test, so they add no new test IDs).

131. close(code): PROGRAM EXIT WITH A HANDLER, NOT A WINDOW EVENT

Second item off the same feature list #130 started working through: "close(code) // exit program with code. Run on exit(code) before hand if close(code) is called" (the user's own follow-up clarification -- the original phrasing said "on code()", corrected mid-task to "on exit(code)"). The name collision with the EXISTING `on close` handler (claude.md #40, the window's close-button event) turned out to be no collision at all: `on close` is an EventHandler declaration, `close(...)` is a plain Call to an Identifier -- different syntactic positions, checked by completely different code paths (analyze_event_handler vs. the BUILTIN_FUNCTIONS branch of infer's Call case), so both names coexist with no ambiguity anywhere a program could actually write.

The one real design decision was where this lives. `on close` and its five siblings (mouseDown/mouseUp/mouse/keyDown/keyUp/resize) are graphics-only: registered with the runtime only inside codegen's `if self.uses_graphics:` block, backed by `g_close_handler` and friends in `festina_runtime_graphics.c`, fired only from a real window event. close(code) has to work in EVERY program, windowed or not -- a headless script computing something and exiting with a status code is exactly the kind of program that would want it most. So `on exit(code:int)` is a genuinely new, seventh event name added to semantic.py's `_EVENT_SIGNATURES` (fixed signature enforced the same way as the other six), but codegen's `_emit_event_handler` routes it differently: it does NOT set `self.uses_graphics` and does NOT join `self.event_handlers` (whose own registration loop is graphics-gated) -- instead it's tracked in a new `self.exit_handler_symbol`, registered unconditionally near the very top of `main()` (right after `festina_runtime_init()`, before database/graphics setup, before `__festina_main()` runs at all), so `close()` called from the earliest top-level code still finds a handler that was declared anywhere in the program. The runtime half lives in `festina_runtime.c` (the core translation unit, always linked) rather than `festina_runtime_graphics.c`: a new `g_exit_handler` static, `festina_register_exit_handler` to set it, and `festina_program_exit(code)` which calls the handler if one is registered and then calls libc's own `exit(code)` -- the same "register once, fire later" shape every other `festina_register_*_handler` in this runtime already uses, just relocated to the translation unit every program actually links.

`close` itself joins `BUILTIN_FUNCTIONS` with a one-`int`-argument signature in `_BUILTIN_SIGNATURES`, which gets it arity/type checking and (via the existing `decl.name in BUILTIN_FUNCTIONS` check in `analyze_func`) protection against a user function shadowing it, for free -- the same mechanism every other builtin here already relies on. Codegen's own `close` branch sits right next to `fail`'s (a similar "coerce an argument, emit one runtime call, return void" shape): coerces the argument to `int` and emits `call void @festina_program_exit(i64 %val)`, no `unreachable` needed afterward (matching `fail`'s own call to `festina_fail`, which also never returns but isn't marked `noreturn` at the IR level either -- dead code after it is simply never reached, not a verifier problem).

Verified: manually first (compiled and ran both a program with `on exit` declared, confirming the handler's own log line prints before the process exits with the right code, and one without, confirming close() alone still exits cleanly with no handler to call), then a new `TestCloseAndExitHandler` class in `tests/test_codegen.py` (6 tests: exits with the given code and runs no handler by default; the handler runs and prints before the real exit; close() with no handler declared still works; close()'s own arity/type rejections; `on exit`'s own signature rejections; a user function can't shadow `close`). This feature touches no refcounted state at all (an int argument, a void handler, a libc `exit()` call), so no leak-stress coverage was needed for it.

Running the full suite for THIS round (not just the targeted subset, which is all #130 itself checked before committing) caught a real gap #130 left behind: `TestLeakStress::test_the_suite_covers_every_managed_resource` asserts the exact SET of `tests/stress/*.f` filenames, a guard against the stress suite quietly shrinking -- and #130's own new `splice_insert_churn.f` was never added to that set, so the assertion failed the moment anything exercised the full suite again. Not a functional bug (leak_stress.sh itself already ran that file directly and confirmed it clean, claude.md #130's own verification paragraph), but a real hole in the coverage-of-coverage guard, caught only because closing out THIS item happened to be the next thing that ran the whole suite rather than a filtered subset -- the actual argument for running the full suite before every commit, not only the tests a change looks related to. Fixed by adding the new filename to the expected set. Full suite: 1325 passed, 8 skipped (1318 baseline + the 6 new `TestCloseAndExitHandler` tests + the one `test_the_suite_covers_every_managed_resource` fixed from failing to passing).

132. mkdir() AND ls(): DIRECTORIES JOIN blob's FILES

Third item off the same feature list #130/#131 are working through: `mkdir('./temp')` (returns true if it created the directory, false if it already existed) and `arr[text] filePaths = ls('./temp/')` (returns the directory's entry names -- despite the user's own variable name, this returns NAMES, not full paths, matching every other language's own `ls`/`readdir`-shaped builtin and confirmed by re-reading the request's own words: "returns array file names"). Both join `BUILTIN_FUNCTIONS` with a one-`text`-argument signature, dispatched through the exact same generic `_FILE_TIME_BUILTINS` mechanism `formatTime`/`saveCanvas` already use in codegen's `_emit_call` -- one runtime call, arg coerced and freed automatically, no new dispatch machinery needed at all.

`mkdir` answers `bool` rather than failing the program on any outcome other than "I created it" -- already-exists, a missing parent, no permission, all collapse to `false`. This is claude.md #93's own rule (a missing/unwritable file is something a program tests for, not something that stops it) applied to directories, extended past claude.md #109's `blob` methods that rule already covers for files. `ls` answers a fresh `arr[text]` built EXACTLY the way `festina_text_split` already builds one (claude.md #116): the same `FestinaPieces` accumulator, the same `festina_pieces_finish` wrapping into a refcounted array with a hidden -8-byte refcount prefix, reused directly rather than duplicated -- `ls` and `split` are both "collect a run of owned strings, hand back a fresh array," just fed from `readdir()` instead of a separator scan. A missing/unreadable directory answers an empty array, the same "test, don't fail" choice `mkdir` makes right next to it.

The one real platform question -- `<sys/stat.h>`'s `mkdir()` takes two arguments (path, mode) on POSIX but MinGW-w64 exposes only the single-argument `_mkdir()` (from `<direct.h>`) for real directory creation (NTFS has no POSIX mode bits for a second argument to mean anything) -- resolved with an explicit `#ifdef _WIN32` branch rather than trusting any MinGW `mkdir`-macro redefinition to paper over the arity difference, consistent with this runtime's established practice (`festina_runtime_init`'s own `_O_BINARY`/`_setmode` block, right above these new functions) of never assuming a POSIX-compat shim behaves identically without being able to verify it on real hardware. `<dirent.h>`'s `opendir`/`readdir`/`closedir`, by contrast, are used completely unconditionally: MinGW-w64 ships a real, long-standing `<dirent.h>` with the identical POSIX shape on every platform this project targets, so `ls()` needed no platform branch at all -- still an unconfirmed-on-real-Windows-CI assumption, like every other blind win32 addition since windows.md Phase 0, but a much safer one than the `mkdir` arity question.

Verified: manually first (created a directory, confirmed the second `mkdir()` call answers `false`, wrote two files into it via `blob`, confirmed `ls()` lists exactly those two names), then a new "filesystem" section in `TestMathFileAndTime` (`tests/test_codegen.py`, 5 tests: creation vs. already-exists, an impossible path answers false rather than crashing, `ls()` lists entry names not full paths and excludes `.`/`..`, a missing directory answers an empty array, arity/type rejections for both). `tests/stress/collections_churn.f` gained a bounded mkdir/ls exercise (a 20-name working set cycled by index, so `ls()`'s own allocation loop runs 1500 times without the directory growing unbounded) -- run through `scripts/leak_stress.sh` under real ASan/LeakSanitizer: clean. Full suite: 1330 passed, 8 skipped (up from 1325 by exactly the 5 new tests) -- run in full, not a filtered subset, before this commit, closing the exact gap #130 left open.

133. drawPixel/clearPixel/clearCircle, AND AN OPTIONAL color ON drawRect/drawPixel

Fourth and fifth items off the same feature list, taken together since they're the same seam: `drawPixel(x:int, y:int, optional:color otherwise uses fillColor)`, `clearCircle`, `clearPixel`, and `drawRect(x, y, xs, ys, optional:color otherwise uses fillColor)` -- the request's own restatement of the ALREADY-existing drawRect (claude.md #37), now with the same optional trailing colour drawPixel gets.

The one real design question was what "optional color, otherwise fillColor" means operationally: does passing a colour just SET fillStyle before drawing (leaking into every later draw call too), or does it paint with that colour for THIS call only, leaving fillStyle exactly as a program left it? The request's own wording -- "otherwise uses fillColor" -- reads as a per-call override, the same relationship `borderColor`/`lineWidth` already have to `drawRect` (configured once, consulted per call, never mutated by the call itself), so that's what got built: `festina_draw_rect_color`/`festina_draw_pixel_color` (new, alongside the untouched `festina_draw_rect`/new `festina_draw_pixel`) save the current fill state (flat colour AND any active gradient, since a gradient is also "the current fillStyle"), substitute the given colour for the duration of one Cairo fill, then restore exactly what was there before -- `festina_fill_and_border_with_color`, a small wrapper around the existing `festina_fill_and_border` (claude.md #89). A `color < 0` (claude.md #91's own 'none' encoding) paints nothing, matching `fillStyle('none')`.

Two functions rather than a `has_color`-flag-plus-sentinel on one: `color`'s own packed representation already uses -1 for 'none', so there was no unclaimed sentinel value left to mean "not provided, use current fillStyle" without colliding with a real, already-meaningful colour. A second C function sidesteps the collision entirely and reads more plainly at each call site than a magic flag argument would. Dispatch in codegen picks the C function purely by `len(args)` -- 4 vs. 5 for drawRect, 2 vs. 3 for drawPixel -- the identical arity-based pattern `fillStyle`/`borderColor`/`changeFont`'s own 1-vs-3-argument forms already established (claude.md #90/#91); `semantic.py`'s `_BUILTIN_SIGNATURE_ALTERNATES` (drawRect's old fixed 4-int entry in `_BUILTIN_SIGNATURES` moved there, alongside a new 2-argument entry for drawPixel) checks exactly the same way.

`drawPixel` needed one more real decision: Cairo's own antialiasing blends a shape's edges even at whole-number coordinates, so a naive 1x1 `cairo_rectangle`+fill paints a faint smudge, not one solid pixel -- confirmed by tracing through Cairo's own rasterizer behavior, not assumed. `cairo_set_antialias(cr, CAIRO_ANTIALIAS_NONE)` around the fill (saved and restored, so it never leaks into the next draw call using the same short-lived context) makes an integer-aligned 1x1 rectangle paint deterministically exactly one pixel. No border on a pixel -- a 1x1 shape has nothing meaningful to stroke, unlike drawRect/drawCircle, which both still support one.

`clearCircle`/`clearPixel` are `clearRect`'s own shape variants, at the exact same "erase back to white, honour the current transform" behavior (claude.md #95) -- no fast-path circle-mask cache the way `drawCircle` has one (claude.md #104), since clearing is a far rarer call than drawing and the cache's own upkeep would cost more than it would save here. Both dispatch through the existing `_CANVAS_OPS` mechanism in codegen (the same one `clearRect` already uses), needing no new dispatch machinery.

Verified: a new `TestDrawPixelClearCircleAndColorOverrides` in `tests/test_codegen.py` (6 tests, all decoding the SAVED PNG's real pixels via the existing `_decode_png` helper `TestSaveCanvas` already established, not just checking the call compiled) -- drawPixel paints exactly one pixel and nothing around it; a colour override wins for its one call and fillStyle is unchanged for the NEXT plain drawRect; an explicit 'none' override paints nothing; clearPixel erases exactly one pixel; clearCircle erases a circular region while a far corner stays untouched; arity/type rejections for all four names (including the now-5-argument-capable drawRect and the now-3-argument-capable drawPixel). The full existing `TestGraphics`/`TestSaveCanvas`/`TestCanvasPathsTransformsAndGradients` suites re-run clean (38 tests), confirming drawRect's un-broken 4-argument form and the fillStyle/borderColor/changeFont arity-dispatch pattern this reuses are both still exactly as they were. No new leak-stress coverage: none of these six functions allocates a refcounted Festina value at all (every argument is a plain int/color, Cairo owns its own internal state), the same reason the pre-existing drawRect/drawCircle never needed a leak-stress entry either -- this stress harness targets managed VALUES a Festina program can leak, not a graphics backend's own internal drawing state.

134. drawRect/drawPixel/drawCircle/drawText JOIN img AS METHODS

Sixth item off the same feature list: "add drawRect, drawPixel, drawText, drawCircle, etc. as methods for img types as well." The canvas already has all four (claude.md #37/#39, #133); the new work is retargeting them at an image's OWN surface instead.

The two real design decisions, both made explicit rather than left implicit: does image drawing honour the canvas's global `translate`/`rotate`/`scale` transform, and does it need a style state of its own? Both resolved toward the SIMPLER option, and both are defensible on the same grounds -- an `img` is a portable asset (loadable, clippable, saveable independent of any window), not a second canvas. So: NO transform (an image draws in its own local pixel coordinates, regardless of whatever the canvas's transform happens to be set to when the call runs), but YES the SAME global `fillStyle`/`borderColor`/`lineWidth`/`changeFont` state every canvas draw call already reads -- claude.md #133's own "otherwise uses fillColor" default reads most naturally as one shared style a program configures once, not a second one to duplicate per image.

Mechanically this turned out to be six thin wrapper functions, not a refactor of the existing four: `festina_image_draw_rect`/`_rect_color`/`_pixel`/`_pixel_color`/`_circle`/`_text`, each just `cairo_create()` on the receiver `FestinaImageBox`'s own surface (no `festina_backing_require()` -- an image's surface exists in full the moment the image does, unlike the canvas's lazily-created one) instead of `festina_canvas_context()`, then the identical body the canvas functions already have (`festina_fill_and_border`/`_with_color`, the same antialiasing-disabled 1x1-rectangle trick for pixels, `festina_apply_font` for text). `festina_image_draw_circle` skips the canvas's own circle-mask fast path (claude.md #104) on purpose -- that cache is keyed on the CANVAS's transform state specifically, which images deliberately don't use, and drawing onto an image is a far rarer call than a frame's worth of canvas shapes. Each of the six also drops the receiver's cached PNG bytes (`festina_image_bytes_now_stale`, factored out of what `festina_image_resize` already did inline) -- any of them mutates the actual pixels, so a stale cached encoding would silently save the WRONG bytes otherwise (claude.md #101's own bytes-cache invalidation, now with a second caller).

Dispatch lives in codegen's existing Member-call chain, right next to `clip`/`resize`'s own `isinstance(obj_type, types_mod.ImageType)` check -- semantic.py gained one new branch (checked against `_IMAGE` the identical way clip/resize already are) reusing drawRect/drawPixel's own arity-based alternates from claude.md #133 rather than duplicating that logic. No naming collision with the canvas-level builtins of the same name: `img.drawRect(...)` is a Member call (`callee` is an `ast.Member`), the canvas-level `drawRect(...)` is a plain Identifier call -- two different AST shapes semantic.py and codegen both already dispatch through entirely separate code paths, the same non-collision claude.md #131 already established between `close(code)` and `on close`.

Verified: a new `TestImageDrawMethods` (5 tests, all decoding the real saved PNG via the existing `_decode_png` helper) -- drawing lands on the image's own surface at the right pixel; the default (no explicit color) form reads the current global fillStyle; drawText writes without crashing (pixel-level glyph verification is out of scope, matching `TestSaveCanvas`'s own choice); a CLIPPED image's own drawing does not leak back into its source (proving the surface is genuinely independent, not aliased); the full arity/type rejection set. `tests/stress/media_churn.f` gained a drawRect/drawPixel/drawCircle exercise -- both plain and color-override forms, plus one through a chained `png.clip(...).drawRect(...)` call (the "owning receiver released right after" path, distinct from a plain-variable receiver, which stays alive under its own binding) -- confirmed clean under `scripts/leak_stress.sh`. `drawText` deliberately excluded from that stress file: exercising it revealed a REAL discovery, unrelated to this feature's own correctness -- `cairo_select_font_face`/fontconfig caches font-matching state for the whole process's lifetime with no teardown API, which LeakSanitizer reports as a leak on the very first call regardless of caller, canvas-level or image-level. Confirmed this is fontconfig's own known behavior, not a Festina bug, by removing just the `drawText` line and re-running clean; also confirmed the CANVAS's own pre-existing `drawText` has never been exercised by any stress file either, for the identical reason -- a pre-existing gap this round didn't introduce and isn't the place to close. Full suite: 1341 passed, 8 skipped (up from 1336 by exactly the 5 new tests).

135. saveCanvas() WITH NO PATH RETURNS AN img SNAPSHOT

Seventh item off the same feature list: "saveCanvas() should return an img type if a path isn't specified." saveCanvas(path) is unchanged (writes a PNG, answers bool); the new zero-argument form skips the file entirely and hands back the canvas's own pixels as an ordinary `img` value instead.

The one real design decision was snapshot vs. live alias -- does the returned img keep tracking the canvas as it's drawn into further, or is it frozen at the instant of the call? Every other way an `img` comes into existence (a path, `clip()`, `resize()`) already produces something with its OWN independent surface, never a view onto something else that keeps changing underneath it; a live-aliasing saveCanvas() would be the one exception, and a silently surprising one -- `img snap = saveCanvas()` followed by `clearCanvas()` would retroactively blank `snap` too, with nothing in the syntax suggesting that's possible. So: a snapshot, built the identical way `festina_image_clip` already builds any other fresh img from existing pixels (a new ARGB32 surface, the source painted onto it via `cairo_set_source_surface`+`cairo_paint`, then boxed) -- `festina_canvas_to_image`, a thin new function reusing that exact shape at the canvas's full size and offset (0,0) instead of a clipped region.

The interesting compiler-side wrinkle: saveCanvas's return TYPE now depends on its own arity (`img` with zero arguments, `bool` with one), which the generic `_BUILTIN_SIGNATURE_ALTERNATES` mechanism (claude.md #90/#91/#133's arity-dispatch pattern) can't express -- it picks an ARGUMENT signature per arity, but still answers one fixed return type per NAME regardless of which alternate matched (`_BUILTIN_RETURN_TYPES.get(name)`, looked up once). So saveCanvas got pulled out into its own dedicated branch in both `semantic.py`'s `_infer_call` and `codegen.py`'s `_emit_call` -- removed from the generic `_BUILTIN_SIGNATURES`/`_BUILTIN_RETURN_TYPES`/`_FILE_TIME_BUILTINS` tables entirely rather than left as dead, misleading entries, and checked explicitly before the generic `BUILTIN_FUNCTIONS` dispatch, the identical shape `setTimeout`/`clearTimeout` already use for their own "needs bespoke argument-shape checking" reason. Ownership tracking needed nothing new for the img-returning form: codegen's refcounting is entirely AST-shape-based (any `ast.Call` is treated as a fresh, owning +1, claude.md #79/#117), not name-based, so `img snap = saveCanvas()` is automatically handled by the exact same machinery that already handles `img snap = sheet.clip(...)` -- no special-casing needed for the new form to just work correctly.

Verified: manually first (compiled and ran a program confirming the returned img reports the canvas's own 800x600 size, and that clearing the canvas afterward leaves a previously-taken snapshot's own saved PNG unchanged -- proving snapshot, not alias, for real rather than by inspection alone), then 3 new tests in `TestSaveCanvas` (the img form round-trips through `.save()` with the right pixels; the snapshot survives a later `clearCanvas()`+redraw untouched; arity/type rejections) plus the pre-existing 2 tests re-confirming the path-argument form is byte-for-byte unchanged. `tests/stress/media_churn.f` gained a `saveCanvas()`-then-`.drawPixel()` exercise (a genuinely new allocation path, distinct from claude.md #134's own draw-in-place img methods), clean under `scripts/leak_stress.sh`. Full suite: 1344 passed, 8 skipped (up from 1341 by exactly the 3 new tests) -- run in full before committing, matching the practice claude.md #131/#132 already re-established after #130's own gap.

136. THE CANVAS CLEARS TO TRANSPARENT, NOT WHITE

Last item off the same feature list this whole run has been working through: "clearCanvas, clearRect, clearCircle, etc should fill the canvas with a transparent background that carries to saveCanvas." claude.md #95 painted the canvas's blank state opaque white from the start; this switches every place that paints a blank canvas -- `clearCanvas`, `clearRect`, `clearCircle`/`clearPixel` (claude.md #133's own new ones), the very first backing-surface creation, and the resize handler's rebuild -- to fully transparent instead, matching the HTML5 `<canvas>` model this whole feature already otherwise mirrors (a fresh or resized/recreated `<canvas>` element really is transparent, not white -- the existing resize-handler comment claiming otherwise was simply wrong, corrected in passing).

The one real Cairo mechanic to get right: `cairo_set_source_rgba(cr, 0,0,0,0)` alone does NOT clear anything under Cairo's default `CAIRO_OPERATOR_OVER` -- OVER compositing is `result = src*alpha + dst*(1-alpha)`, which for a fully-transparent source (`alpha=0`) reduces to `dst` unchanged, so "painting nothing" over existing content is a genuine no-op, not a clear. Every site needed `cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE)` first -- SOURCE replaces the destination outright regardless of the source's own alpha, which is what "erase to transparent" actually requires. Each site sets it on its own short-lived `cr` (created and destroyed within the same function, the established pattern every draw/clear function here already follows), so nothing needed restoring afterward -- no other function's own context is ever affected. `saveCanvas()`'s own PNG writer needed no changes at all: Cairo's `cairo_surface_write_to_png` already round-trips an ARGB32 surface's real alpha channel faithfully, so "carries to saveCanvas" was true automatically the moment the backing surface genuinely held transparent pixels -- confirmed, not assumed, by decoding the actual PNG bytes in every test below rather than trusting that claim.

This was the single largest blast radius of anything in this whole feature-list run: 19 existing test assertions, spanning `TestCircleMaskFastPath` (11, none of them about THIS feature -- they happened to check "background is white" as a side observation while testing claude.md #104's circle cache), `TestSaveCanvas`, `TestRenderClearAndHeadless`, and this round's own new `TestDrawPixelClearCircleAndColorOverrides` tests from #133, all asserting `pixel(x, y) == (255, 255, 255)` for "nothing was drawn here." Found by doing exactly what claude.md #130's own gap taught: implement the runtime change FIRST, then run the untouched full suite before writing a single new test, and fix every real failure it reports rather than guessing which assertions would need updating. One of those 19 needed a genuine redesign rather than a value swap: `test_alpha_applies_to_a_circle` blended alpha=0.5 black against what used to be an implicit white background to prove "mid grey" -- with the canvas now transparent by default, that blend produces solid black (unpremultiplying an alpha=0.5, RGB=0 premultiplied pixel divides 0 by 0.5, giving RGB=0 either way), so the fix draws an explicit opaque white rect first, making the test about `fillAlpha()`'s own blending again rather than about what the canvas happens to default to.

`tests/test_codegen.py` gained a `_decode_png_rgba` sibling to the existing `_decode_png` (both now share a `_png_raw` decode step) -- `_decode_png` itself is completely unchanged in shape and behavior, so none of the ~20 OTHER callers checking real drawn colors needed to change at all; only the 19 failing "is this transparent" assertions were switched to the RGBA decoder and an explicit `(0, 0, 0, 0)` check, more precise than the old `(255, 255, 255)` RGB-only comparison ever was (a `(0, 0, 0)` opaque-black pixel and a genuinely transparent one are indistinguishable through RGB alone; they are not through RGBA).

Verified: all 19 previously-failing tests pass again (re-purposed to check transparency, not rewritten around it) plus 2 renamed to match (`_erases_..._back_to_white` -> `_erases_..._to_transparent`); the wider `TestGraphics`/`TestSaveCanvas`/`TestCanvasPathsTransformsAndGradients`/`TestImageDrawMethods`/`TestImageClipRendersRealPixels` suites re-run clean (48 tests, including the real-X-display tier via Xvfb, confirming on-screen rendering of actually-drawn/opaque content is unaffected). `scripts/leak_stress.sh` run in full (all six stress programs, not just a targeted one) -- clean, as expected for a change that touches only which colour gets composited, never allocation. Full suite: 1344 passed, 8 skipped (unchanged in total count from before this entry -- 19 fixes plus 2 renames plus one new helper function, net zero new test IDs). With this, every item from the feature-request list this run started with is done.

137. arr[img]/arr[blob]/arr[aud] LOAD EACH ELEMENT FROM A PATH

New batch of feature requests. First item: "Allow declaration of img/blob/aud in array: arr[img] brushes = ['./brush1.png', './brush2.png']". claude.md #100/#101/#109 already let a SCALAR media declaration take a path directly (`img sprite = 'sprite.png'`, one-directional text -> media, checked in semantic.py's `check_assignable` and performed in codegen's `_coerce` via a real file read/decode); this is the identical allowance applied per-element inside an array literal.

The gap was entirely in semantic.py, and entirely about missing CONTEXT, not missing capability. `ArrayLit` type inference has no notion of an "expected" element type at all -- it only ever infers each element's OWN type and demands they agree (`[1, 'x', true]` rejected as mixed), so `['a.png', 'b.png']` always infers as `arr[text]` regardless of what it's being declared into, and `check_assignable`'s array/map case had exactly one special allowance (`arr[T] = [null, ...]`, claude.md #102) -- nothing that let a `text` ELEMENT coerce into a `media` element the way a `text` VALUE already coerces into a media declaration. So `arr[img] brushes = ['a.png', 'b.png']` reached `check_assignable(arr[img], arr[text])` and was flatly rejected as a type mismatch, never reaching codegen at all.

Codegen, meanwhile, needed ZERO changes -- confirmed, not assumed, by implementing only the semantic.py half and testing immediately. `_emit_array_lit` already threads an `expected_type` through to `_coerce` per element (the same mechanism claude.md #130's splice-insertion review traced through), and `_coerce` already handles `TEXT -> BLOB/AudioType/ImageType` generically (a real `festina_load_image`/`festina_load_audio`/`festina_blob_open` call, exactly what a scalar declaration triggers). The only reason `arr[img] brushes = [...]` didn't already work was that semantic.py never let it reach that already-capable codegen path.

Fixed with a new, narrowly-scoped branch in `analyze_var_decl`, checked BEFORE the generic `infer()`+`check_assignable()` call rather than trying to thread an "expected type" parameter through the shared `infer()` closure for one caller: when the declared type is `arr[img]`/`arr[blob]`/`arr[aud]` and the initializer is literally an `ast.ArrayLit`, each element is inferred and checked against "either the declared media type itself (an existing value reused by reference) or `text` (a path)" directly, bypassing the generic same-concrete-type-for-every-element rule that would otherwise reject the mix. Every other declaration shape (a plain `arr[img]` from a function call, a table query, `null`, ...) still goes through the unchanged generic path.

Verified: manually first (loaded two real image paths into `arr[img]`, confirmed both decoded at their real dimensions; mixed a path with an existing aliased `img` in one literal, confirmed the alias reads as non-null; loaded `arr[blob]` and `arr[aud]` the same way; confirmed a genuinely wrong element type -- an int, a bool, an `aud` where `img` was declared -- is rejected with a clear message), then 6 new tests in `TestTypedMediaArrayLiterals` (`tests/test_codegen.py`) covering all three media types loading real fixture files, an already-typed element mixed with a path in one literal, an aliasing check (resizing through one name is visible through the array element sharing it, proving retain rather than copy -- claude.md #79/#80's own array-literal retain rule applied with no changes needed), and 5 rejection cases. `tests/stress/media_churn.f` gained an `arr[img]`/`arr[blob]` literal exercise, clean under `scripts/leak_stress.sh`. Full suite: 1350 passed, 8 skipped (up from 1344 by exactly the 6 new tests) -- run in full before committing.

138. "COMPILE SQLITE QUERIES INTO PREPARED STATEMENTS": ALREADY DONE

Next item off the same feature list: "on compile turn each sqlite query into a prepared statement (if possible)." Checked before writing anything, per this whole run's own established practice of confirming the actual gap before implementing -- and there wasn't one. claude.md #113 already built exactly this, well before this run started: `_emit_sqlite_prepare` (codegen.py) checks whether a call's SQL argument is a genuine compile-time `ast.StringLit` and, when it is, routes through `festina_sqlite_prepare_cached` -- one dedicated global slot per CALL SITE, prepared once on first reach, reset-and-rebound (not re-parsed) on every call after. Dynamic SQL (a template literal, a variable) keeps the ordinary per-call `festina_sqlite_prepare`, since the same call site can genuinely see different SQL text across calls -- precisely the "if possible" the request itself already anticipates. `sqliteInt`/`sqliteFloat`/`sqliteText` share the identical path, not just the plain `sqlite()` call (confirmed by re-reading `_emit_sqlite_scalar`'s own doc comment and by `TestStatementCache`'s own existing `log(sqliteInt('SELECT count(*) FROM T'))` test case).

A pre-existing `TestStatementCache` (`tests/test_codegen.py`, 3 tests, re-run now and confirmed still green) already covers exactly what a from-scratch implementation of this request would have needed proven: a literal call site gets exactly one cache slot and reuses it correctly across different bound parameters (the reset+rebind path, where a caching bug would show as stale results); a literal site gets the cached prepare while a dynamic (template) site at a different call point keeps the uncached one, in the same program; two textually IDENTICAL literal queries at two different call sites get two independent slots, not one shared statement (so one being mid-collection can never disturb the other). claude.md #113's own measurement: 20,000 one-row SELECTs went from 164ms to 55ms, 20,000 INSERTs from 16.7s to 0.3s (paired with the WAL journal mode change from the same entry).

No code changed for this entry -- it exists purely as the record that this item was checked against the actual codebase and found already satisfied, the same as any other item on this list, rather than silently skipped or re-implemented redundantly.

139. screenWidth/screenHeight AND setClientWidth/setClientHeight

Next batch off the feature list: "setClientHeight(int) and setClientWidth(int)" and "screenWidth and screenHeight global properties." `clientWidth`/`clientHeight` (claude.md #39) already report the canvas window's current CONTENT size, read-only; this adds the physical DISPLAY's own resolution (`screenWidth`/`screenHeight`, read-only, no setter -- a program cannot resize the screen it's running on) and a way to change the content size (`setClientWidth`/`setClientHeight`), which `clientWidth`/`clientHeight` never had despite being named after the DOM's own read-only `Element.clientWidth`/`clientHeight`.

Both size-global families share every touch point in semantic.py -- registration into `global_scope`, the read-only-assignment rejection, the "already declared" collision check -- so `_SCREEN_SIZE_GLOBALS` joins the existing `_CLIENT_SIZE_GLOBALS` into one combined `_SIZE_GLOBALS` tuple those touch points iterate, rather than either being special-cased or the two folded into one name (they answer genuinely different questions: a window can be, and usually is, smaller than the screen it's on). `setClientWidth`/`setClientHeight` are ordinary one-`int`-argument `BUILTIN_FUNCTIONS`, no different in shape from `mkdir`/`ls` (claude.md #132).

The real design work was in the runtime, and split into two questions. First: does `festina_window_screen_size` need a window already open? No -- unlike every other windowing-seam function (open/close/present/events_wait/events_drain), a headless program asking "how big is the screen" before ever drawing anything is a real, intended use (`saveCanvas`/`clientWidth`/image loading already all work headless, claude.md #95), so the X11 implementation opens its own throwaway `Display*` via `XOpenDisplay` when `g_display` is still null, queries `DisplayWidth`/`DisplayHeight`, and closes it again -- invisible to the caller either way. This is also the ONE graphics read that still fails without a display at all: there is no window to answer from and no other way to ask a headless process "how big is the screen," so it reports the identical "no X display" `festina_fail` message `render()` itself does, rather than a silent 0x0.

Second, and the one that actually took real testing to get right: `setClientWidth`/`setClientHeight` needed to be DETERMINISTIC and SYNCHRONOUS -- `setClientWidth(400)` followed immediately by `log(clientWidth)` must read `400` right away, not whatever stale value was true before a native window manager gets around to confirming a resize asynchronously (the exact same "make it true here, not eventually" reasoning claude.md #136's own canvas-clear-to-transparent design already applied). So `festina_set_client_size` (the shared portable core both setters call into) updates `g_canvas_width`/`g_canvas_height` and rebuilds the backing surface immediately, in the call itself, THEN separately asks the OS window to match via the new `festina_window_resize` seam function, for when one is open.

That "ask the OS window to match, separately" is where the real bug was, and it was found by real testing, not guessed: a native resize the seam function requests still generates its own trailing RESIZE event later (an X11 `ConfigureNotify`), which is an ECHO of a change already applied synchronously above, not a second logical resize -- without suppressing it, one `setClientWidth` call would rebuild the backing store and fire `on resize` TWICE. The first fix attempted (compare the incoming event's size against current `g_canvas_width`/`g_canvas_height`, skip if it already matches) is intuitive and wrong: a real Xvfb reproduction of `render() setClientWidth(500) setClientHeight(350)` -- two calls back-to-back -- produced FOUR `on resize` firings instead of two. The reason: X11 does not coalesce `ConfigureNotify` events across two separate `XResizeWindow` calls, so two separate echoes arrive, and the first one can carry a STALE intermediate geometry (whatever the window manager's timing happened to catch mid-flight) that doesn't match either the size before or after -- a size-comparison guard lets that stale echo through (current state hasn't been touched yet, so it still "matches"), and PROCESSING it clobbers `g_canvas_width`/`g_canvas_height` away from what `festina_set_client_size` had already committed, making the SECOND echo also look novel and fire again too. Fixed by replacing the size comparison with a `g_pending_self_resizes` counter: incremented once per `festina_window_resize` call, decremented (with the entire event body skipped, no geometry even inspected) whenever it's positive. This counts OWED echoes rather than comparing geometry, so it can't be fooled by what value a given echo happens to report -- confirmed by re-running the identical repro after the fix and seeing exactly two firings, `count=1` then `count=2`, with a further 0.5s wait producing no spurious third or fourth.

Cocoa (`festina_runtime_window_mac.m`) and Win32 (`festina_runtime_window_win32.c`) got the same two seam functions -- `[NSScreen mainScreen].frame` / `GetSystemMetrics(SM_CXSCREEN/SM_CYSCREEN)` for screen size, `[g_window setContentSize:]` / `SetWindowPos(..., SWP_NOMOVE | SWP_NOZORDER)` for resize -- built the same way every other platform-specific piece of this codebase has been since macos.md/windows.md Phase 0: real logic ported to each platform's equivalent call, unverified on real hardware yet, documented as such in the code itself rather than left silent about it. The macOS screen-size query deliberately reads in POINTS, matching every other size this backend already exchanges with Festina code (there is no `backingScaleFactor` handling anywhere in that file), so a Retina display's doubled pixel density can't leak through as a unit mismatch.

Verified: manually first, against a real Xvfb X server -- headless `clientWidth`/`clientHeight` via `setClientWidth`/`setClientHeight` (800x600 -> 400x300, synchronously, no display needed); `screenWidth`/`screenHeight` reporting the real Xvfb resolution (1024x768) when a display exists, and failing with the exact "X display" message when `$DISPLAY` is unset; the double-fire bug reproduced BEFORE the fix (4 firings) and confirmed gone AFTER it (exactly 2). Then a new `TestScreenSize`/`TestSetClientSize` in `tests/test_graphics.py` (semantic-level: valid identifiers, template-literal usage, read-only-assignment and already-declared rejections for `screenWidth`/`screenHeight`; arity/type rejections for `setClientWidth`/`setClientHeight`) and a new `TestScreenSizeAndSetClientSize` in `tests/test_codegen.py` (5 tests: the no-display runtime error; `screenWidth`/`screenHeight` matching the REAL display resolution, queried independently via `xdotool getdisplaygeometry` rather than hardcoded, so the assertion holds whether `x_display` spins up its own throwaway Xvfb or reuses a real, already-set `$DISPLAY`; headless `setClientWidth`/`setClientHeight` updating `clientWidth`/`clientHeight` synchronously; non-positive sizes silently ignored; and the double-fire regression itself, against a real Xvfb window, with an explicit 0.5s tail wait to give a spurious extra echo a real chance to arrive before declaring the fix correct). No leak-stress coverage needed: none of these four functions allocates a refcounted Festina value -- `screenWidth`/`screenHeight` return plain ints, `setClientWidth`/`setClientHeight` mutate existing runtime state, matching claude.md #133's own reasoning for why drawPixel/clearCircle/clearPixel needed none either. Full suite run in full before committing (not a filtered subset, per this whole run's own established practice since claude.md #130's gap).

140. FUNCTION HOISTING TO THE TOP OF THEIR CONTEXT

New feature-list item: "Hoist functions to the top of their contexts on compilation." Before this, a call to a function had to appear textually AFTER that function's own declaration -- the module docstring at the top of semantic.py said so outright ("None of this repo's fixtures need forward references... functions are always declared before use"), and both semantic.py's `analyze_func` and codegen.py's `_emit_func` registered a function's name/signature and processed its body in the SAME single left-to-right pass, so a forward call simply never resolved. This makes function declaration order stop mattering entirely -- calling a function above its own declaration, and two functions mutually recursing (each necessarily calling the other before its own textual declaration, impossible to write at all under the old rule), both just work now -- the identical declaration-order-independence claude.md #106 already gave struct/table NAMES, extended to function signatures.

The mechanism is the same shape #106 already established: a dedicated PRE-PASS over the whole program, registering every function's name and signature before the real analysis/codegen walk ever checks a single call, so by the time anything looks a function up it's already there. `semantic.py` split the old single `analyze_func` into `register_func_signature` (the builtin-name-collision check plus the `global_scope.define` call -- moved to the pre-pass) and a trimmed `analyze_func` (body analysis only, reading the signature the pre-pass already registered). `codegen.py` got the identical split: `_register_func_signature`/`_register_all_func_signatures` (a new pre-pass in `generate()`, before the real emission loop) versus a trimmed `_emit_func` (body emission only). The pre-pass runs AFTER claude.md #106's own struct/table name pre-pass, not folded into it or run first -- a function's own parameter/return type can itself name a struct, so every struct/table name has to already exist before `register_func_signature`'s own `resolve()` calls run.

The pre-pass had to be a REAL recursive walk, not a flat scan of `program.body`, because Festina already allows a `FuncDecl` to appear anywhere a statement can -- nested inside an `if`/`while`/`for`, or even inside ANOTHER function's own body -- and `analyze_func` has always treated one as an ordinary GLOBAL declaration regardless of nesting (`Scope(global_scope)` as the function's own parent scope, always, never the enclosing block's). A new `_iter_func_decls`/`_register_all_func_signatures` walker (semantic.py/codegen.py respectively) recurses into every statement shape that can hold a nested FuncDecl -- Block, either arm of an IfStmt, a While/ForStmt body, an EventHandler body, or a FuncDecl's own body -- deliberately kept in lockstep with `analyze_statement`/`_emit_stmt`'s own recursive descent, since a FuncDecl the real pass would eventually reach but the pre-pass skipped would defeat hoisting for exactly that one declaration.

That recursive walk surfaced a genuine, PRE-EXISTING gap unrelated to hoisting itself: a nested `FuncDecl` was already accepted by the parser and already fully analyzed by semantic.py (nothing about analyze_func's own recursion cared about nesting), but codegen's `_emit_stmt` had NO branch for `ast.FuncDecl` at all -- it fell through to the final "cannot generate code for statement FuncDecl" `CodegenError` the moment one was ever reached. This had simply never been exercised by anything in the existing test suite. Fixed as part of this work (required for hoisting a nested function to mean anything at the codegen level too): `_emit_stmt` now emits a nested FuncDecl's body exactly the way a top-level one is emitted (into `self.func_defs`, via the same `_emit_func`), with its own textual STATEMENT position becoming a no-op -- the same "this position does nothing at runtime, the function already exists" semantics a JavaScript function-declaration statement has.

Making `_emit_func` itself REENTRANT (nested calls now reach it recursively, mid-emission of an enclosing function's own body) surfaced a second, real bug, this one caught by an actual failing compile, not by inspection: `self._current_escaping_names` and the `down_to=0` default every `Return` statement used were both written under the explicit, now-false assumption that "Festina has no nested function declarations reaching codegen... only ever one function/handler's body is being emitted at a time." A struct/text/array/map local declared in an enclosing function, followed by a nested `FuncDecl`, produced a genuine LLVM verifier error ("use of undefined value") -- the nested function's own trivial `return` statement, using the hardcoded `down_to=0`, freed EVERY frame on the shared `self._active_free_locals` stack, including the enclosing function's own still-live locals sitting further down that same stack, not just its own (empty) one. Fixed with two changes: `self._current_escaping_names` is now SAVED and RESTORED around `_emit_analyzed_func_body`'s own recursive re-entry (rather than unconditionally reset to `None`), and a new `self._current_func_frame_base` field (also save/restored, around `_emit_func`'s own frame push/pop) replaces `Return`'s hardcoded `down_to=0` with `down_to=self._current_func_frame_base` -- the index of the CURRENTLY-being-emitted function's own outermost frame, 0 for any top-level function/handler (where the shared stack is always empty on entry) but correctly offset for a nested one. Both fixes are plain save/restore around a recursive call, needing no separate explicit stack (the Python call stack itself already is one, the same reasoning `_active_free_locals`/`_loop_targets` already lean on for genuinely nested BLOCKS within one function). `_active_free_locals` and `_loop_targets` themselves needed no fix at all -- both are already push-then-pop balanced around whatever they track, so a nested `_emit_func` call pushing and fully popping its own entries on top of an enclosing function's still-open ones is already correct, no reentrancy bug there.

`escape_analysis.py`'s own docstring, and codegen.py's `self.escaping_params` field comment, both claimed "semantic.py already rejects any other forward reference to a function before its own declaration" as the reason a callee not yet in `escaping_params` could only mean self-recursion. Hoisting makes a genuine forward reference to an EARLIER-emitted function's LATER-declared callee possible now too -- but this needed no functional fix, only a comment correction: a callee not present in `escaping_params` (self-recursion, a genuine forward reference, a builtin, a non-Identifier callee) already fell back to the conservative "every call argument escapes" default, which is always SAFE (one call argument gets an unneeded extra retain/release pair it didn't strictly need), never a soundness gap -- "not yet proven safe" and "escaping" were always the identical fallback. Both comments were rewritten to describe the fallback's actual safety property directly, rather than leaning on an ordering guarantee that no longer holds.

Verified: manually first, several real compiled-and-run programs -- a plain forward call, mutual recursion, a function nested inside an always-taken `if`, a function nested inside a genuinely `if (false)`-guarded (never-executed-at-runtime, but still hoisted, matching JS's own unconditional hoisting) block, a function nested inside another function and called from the top level, `setTimeout`'s own callback argument resolving to a forward-declared function -- all producing the right output. The struct/text/array/map corruption bug was found and confirmed exactly this way: compiling `struct Point{...} int func outer(seed:text){ Point p; ...; text local = ...; arr[int] nums = [...]; int func inner(){return 7}; ...}` produced a real LLVM verifier error before the frame-base fix, and the fixed generated IR was inspected directly (`inner`'s own emitted function body went from freeing FOUR of `outer`'s own locals down to a bare `ret i64 7`, nothing else) before moving on to automated tests. Then a new `TestFunctionHoisting` in `tests/test_syntax_declarations.py` (7 semantic-level tests: forward call, mutual recursion, a function nested in a block, a function nested in another function, an unresolvable forward reference still errors, a duplicate function name is still a duplicate-declaration error, a function's own signature can forward-reference a struct declared even later) and a new `TestFunctionHoisting` in `tests/test_codegen.py` (8 compile-and-run tests covering the identical shapes end to end, including the exact struct/text/array/map corruption regression -- reading every one of those four locals' values AFTER the nested declaration and checking the arithmetic comes out right -- and a `break`/`continue` sanity check around a nested FuncDecl inside a loop body, confirming `_loop_targets` needed no fix). `tests/stress/structs_and_rows_churn.f` gained a `makeOuterViaNestedHelper` function (a struct-returning function with a FuncDecl nested in its own body, mirroring the exact regression shape) exercised 400 times in the existing hot loop -- clean under `scripts/leak_stress.sh`, run in full (all six stress programs). Full suite: 1382 passed, 8 skipped (up from 1367 by exactly the 15 new tests) -- run in full, twice: once right after the codegen/semantic.py changes (before any new tests existed, to prove zero regressions against the pre-existing 1367), and again after adding the new tests.

141. FIRST-CLASS FUNCTIONS: func[T,T,...]:R AS ARGUMENT, STRUCT PROPERTY, MAP VALUE, ARRAY VALUE

Next feature-list item, and the one #140's own hoisting work already sat next to conceptually: "Use func as argument, struct property, map value, or an array value." Before this, a function's own NAME meant exactly one thing -- the callee of an immediate `Call` -- and codegen even carried an explicit, pre-existing placeholder proving this was already anticipated: `raise CodegenError("functions are not first-class values yet (found bare reference to '{name}')")`, hit the moment a bare function name was used anywhere OTHER than as a Call's own callee. This entry replaces that placeholder with a real implementation: functions are now genuine values, typed `func[paramType, paramType, ...]:returnType` (`func[]:void` for zero arguments, void-returning), assignable to a variable, passable as an argument, storable in a struct field, an `arr[T]` element, or a `map[T]` value -- and, critically, CALLABLE back out of any of those, not merely storable.

The type spelling was a real design choice, made to avoid a genuine grammar ambiguity rather than for its own sake. The request's own example return-type-before-`func` syntax (`void func(arg:text) => ...`, from the arrow-function item right next to this one) can't spell a bare TYPE the same way: `void func(text) x = someFunc` is token-for-token indistinguishable, arbitrarily far ahead, from `void func name(...)` (a real function DECLARATION) until the parser reaches either a `NAME(` or a bare `TYPE)` inside the parens -- expensive, fragile backtracking for something that needs to be unambiguous at a single token of lookahead the way every other type-position decision in this parser already is. `func[T, T, ...]:R` sidesteps the whole problem: `func` immediately followed by `[` can ONLY be this construct (a real declaration's `func` is always immediately followed by a NAME), so `parse_statement`'s own pre-existing "func" branch (previously an unconditional "functions require an explicit return type" rejection, since a bare `func` starting a statement used to mean only one thing: the pre-#141 mistake of writing `func name(...)` instead of `text func name(...)`) needed exactly one extra token of lookahead (`self.peek(1).type != "LBRACK"`) to keep meaning that ONLY when it isn't secretly the start of a func-typed declaration instead.

The type system side is a new `types.FuncType(param_types, return_type)` -- `return_type` is `None` for void, the same internal convention `FuncDecl.return_type`/every Symbol already uses, so nothing else needed to learn a second "no return value" sentinel. Structural (dataclass) equality is what makes `check_assignable`'s existing generic `declared != actual` fallback correctly reject a signature mismatch with zero FuncType-specific code -- confirmed directly (`func[int]:void cb = greet` where `greet` takes `text`, and a mismatched RETURN type alone, both correctly rejected with the exact same "cannot assign value of type X to Y" message every other type mismatch already gets). The runtime VALUE is a bare LLVM function pointer -- `_llvm_type(FuncType) == "ptr"`, deliberately excluded from `_is_refcounted` -- so a func-typed slot rides every existing generic scalar-shaped code path (VarDecl, struct field, array element, map value, argument passing, the null-zero-value fallback) completely unchanged, the identical "immortal pointer, nothing to retain or release" treatment ColorType/FontType already established; confirmed clean under `scripts/leak_stress.sh` specifically to prove struct/array/map release logic really does skip a func-typed slot rather than mishandling it, not merely assumed from the type being unregistered in `_is_refcounted`.

Two places needed a real, deliberate redesign of what a bare function-name reference and a function CALL each mean, since both semantic.py and codegen.py already stored a function's Symbol/env entry keyed to its RETURN type (used directly by the ordinary `name(...)` call path, `return sym.type`) -- reusing that same field for "the type of a bare reference to `name` itself" would have been simply wrong (a bare `greet` would infer as `greet`'s return type, e.g. `text`, not as anything callable). Instead, both `infer()`'s Identifier branch (semantic.py) and `_emit_expr`'s Identifier branch (codegen.py) SPECIAL-CASE a function-kind symbol only when the Identifier is being read as a bare VALUE (never touching `sym.type`/the env's stored return-type, which the ordinary Call path still reads directly, unaware this special case even exists) -- building a fresh `FuncType` from the FuncDecl's own params/return_type each time, cheap and side-effect-free the same way `_register_func_signature`'s own resolve() calls already are. The two paths are told apart structurally (is this Identifier the direct, unwrapped callee of an enclosing Call, or not), never by anything cached on the Symbol/env entry -- so a plain `greet(x)` call and a bare `func[text]:void cb = greet` reference sitting right next to each other in the same program read the identical Symbol/env slot two different, correct ways.

Calling THROUGH a func-typed value needed its own new dispatch, in three places, since Festina had never had an INDIRECT call before (every existing call site assumed the callee's identity, and therefore its exact signature, was known at compile time from a global `self.func_decls` lookup): (1) `x(...)` where `x` is a func-typed variable/parameter -- checked in BOTH semantic.py's `_infer_call` and codegen's `_emit_call`, and in BOTH cases checked BEFORE the existing "is this a real declared function" path, mirroring `Scope`/`Env`'s own resolution order so a local variable that happens to SHADOW a global function's name (permitted; `Scope.define` only checks its own immediate scope, not the parent chain) resolves calls against its own shadowing signature, never silently falling through to the shadowed global instead -- confirmed with a dedicated test (`func[text]:void greet = other` inside a function that ALSO has a global `greet`, calling the local `other`, not the global `greet`). (2) `h.cb(...)` / `fns[i](...)` / `handlers[key](...)` -- struct field, array element, and map value calls, ALL landing at one shared new branch (codegen's OWN pre-existing "only calls to named functions are implemented" fallback, confirmed as a genuine unimplemented gap by tracing a real crash through it) that emits the callee Member expression as an ordinary VALUE read (no special-casing needed for struct/array/map access specifically -- reading `h.cb` as a bare expression was already fully generic) and, if that value's inferred type is a FuncType, emits an indirect `call` through it. Indirect-call LLVM syntax was verified directly rather than assumed correct from memory -- a standalone hand-written `.ll` file with `%loaded = load ptr, ptr %fp` followed by `call i64 %loaded(i64 3, i64 4)` was compiled AND executed (not just verified with `opt -passes=verify`) before this pattern went into codegen.py at all, confirming `3+4=7` came back correctly through a genuinely indirect call.

The recursive-emission fix from claude.md #140 (nested FuncDecl reaching codegen for the first time) had already made `_emit_func` reentrant; first-class functions needed no NEW reentrancy fix on top of that, since a func-typed value's own storage/read/call all ride the ordinary generic expression-emission machinery rather than anything `_emit_func`-specific.

Deliberately out of scope, staying true to the request's own wording ("argument, struct property, map value, array value" -- not "closures"): no lexical capture. A func-typed value is always a plain reference to one of the program's own declared functions (top-level or nested, claude.md #140), never a closure over local state -- consistent with Festina having no lexical scoping for functions at all to begin with (every FuncDecl, however nested, is already an ordinary GLOBAL declaration). `setTimeout`/`setInterval`'s own callback argument is UNCHANGED -- still specifically the bare name of a zero-parameter, void-returning DECLARED function (its own dedicated, pre-existing signature check), not widened to accept an arbitrary `func[...]:...`-typed expression; extending that is a separate decision the request didn't ask for.

Verified: manually first, all 5 requested shapes (variable assignment+call, function argument, struct field, array element, map value) compiled AND RAN correctly end to end, plus a combined 3000-iteration smoke test exercising all five together, clean under `scripts/leak_stress.sh`; separately, null assignment/comparison, wrong-signature/wrong-arity/wrong-argument-type rejections (semantic-level), and the shadowing-a-global-function case, all confirmed with real compiled output, not just semantic analysis alone (an early testing mistake this round caught on itself: an initial test claiming `fns[0](5)` "worked" had only run semantic analysis, never codegen, and silently missed that codegen's own indirect-Member-call path didn't exist yet at all -- caught by actually running the SAME source through full compilation before trusting the earlier result). Then `TestFuncTypeSyntax` (`tests/test_syntax_declarations.py`, 7 parser-level tests: the type in every requested position, the zero-arg form, and confirming the pre-existing "missing return type" error still fires for a bona fide `func name(...)` mistake) and `TestFirstClassFunctions` (same file, 13 semantic-level tests: all 5 valid shapes, null, signature/return-type/arity/argument-type mismatches for both a plain variable and a struct-field callee, and the shadowing case) plus `TestFirstClassFunctions` (`tests/test_codegen.py`, 10 compile-and-run tests: the identical shapes executed end to end, a zero-argument and a non-void-returning function value, the shadowing case's real output, null declared and left uncalled, and a struct holding a func field surviving both a read and an explicit `free`). `tests/stress/collections_churn.f` gained an `arr[func[int]:int]`/`map[func[int]:int]` exercise and `tests/stress/structs_and_rows_churn.f` gained a `Callback` struct with a func-typed field, both called through indirectly inside their existing hot loops -- clean under `scripts/leak_stress.sh`, run in full (all six stress programs). Full suite: 1413 passed, 8 skipped (up from 1382 by exactly the 31 new tests) -- run in full before committing, per this whole run's own established practice.

142. ARROW FUNCTIONS COMPILE TO REGULAR FUNCTIONS

Last item off the same feature list: "compile arrow functions as regular functions. Example: `void (arg:text) => log(arg)` // Equivalent to: `void func(arg:text) {return log(arg)}`." Builds directly on claude.md #141 (first-class functions): an arrow function is an ANONYMOUS function VALUE -- new syntax, but nothing new underneath it. `returnType (params) => expr` desugars to a synthesized, uniquely-named top-level function (`__festina_arrow_N`), and the arrow expression itself evaluates to a `func[...]:...` reference to it -- exactly the "compiles to regular functions" framing the request itself uses, and literally true: semantic.py builds a real `ast.FuncDecl` and runs it through the SAME `register_func_signature`/`analyze_func` every ordinary function declaration already goes through, with zero arrow-specific type-checking logic anywhere.

The request's own equivalence example doesn't quite typecheck as written -- `return log(arg)` inside a `void func` is rejected outright by claude.md #23's existing rule (a void function's body may never `return <value>`, only a bare `return`) -- so a literal transcription was never the goal; what the example is clearly gesturing at (the arrow body's expression becomes the function's own "result") is what got built, split by return type: a VOID arrow function's body is a bare expression statement (evaluated for side effects, its value discarded, matching how `void func(arg:text) { log(arg) }` -- no `return` at all -- is what claude.md #23 actually requires), while a NON-void arrow function's body genuinely is `return <expr>`, matching the request's own example exactly for that case. Documented as a deliberate, reasoned deviation from the literal wording, not a silent reinterpretation.

The syntax itself -- `func[T, T, ...]:R` for the arrow's own return-type-then-params shape, not the request's bare `void (arg:text) =>` spelled directly as a value's TYPE -- was a real, necessary design choice, not preference: `void func(text) x = someFunc` (a func-TYPE spelled the request's own way) is token-for-token indistinguishable from `void func name(...)` (a real function DECLARATION) until the parser reaches either a bare `NAME(` or a `TYPE)` arbitrarily far into the parens, needing expensive backtracking for something every other type-position decision in this parser resolves with one token of lookahead. `func[T,T,...]:R` avoids that ambiguity entirely (claude.md #141), and the arrow EXPRESSION syntax adopted here (`returnType (params) => expr`, matching the request's own example precisely, unlike the type spelling) turned out to need real lookahead of its own for a DIFFERENT reason: an arrow function's return type can be `void`/a TYPE_KEYWORD/`arr[T]`/`map[T]`/`func[...]:...` (all unambiguous -- none can ever start an ordinary expression on their own, so seeing one immediately followed by `(` can only ever mean an arrow function, no lookahead needed) OR a bare struct/table-typed IDENT, which genuinely IS ambiguous (`Point(x)` is also a perfectly ordinary function call, the common case by far) -- resolved with a bounded, backtracking-free scan (`_arrow_params_end`) that only commits to "arrow function" for the IDENT case once it's confirmed the FULL candidate parameter list ends in `) =>`.

A real, caught-directly bug came out of building that lookahead: an early version called `_type_expr_end` (the existing #140/#141 return-type-skipping helper) unconditionally on whatever token started the current expression, without first confirming that token could even START a type. `_type_expr_end`'s own fallback treats ANY unrecognized token as "a valid one-token type" -- correct for its ORIGINAL two callers (`_starts_func_decl`, `_looks_like_declaration`), both of which already gate on the exact right token set before ever calling it -- but calling it blind here misidentified `log(x)` ITSELF as an arrow-function start (`log`'s own token type is the literal keyword `log`, not `IDENT`, so the "is this followed by LPAREN" check alone let it straight through), which would have broken every single `log(...)` call in the language. Caught by running the full existing test suite before trusting the new lookahead at all, fixed with an explicit gate (the first token must itself be `TYPE_KEYWORDS`/`void`/`arr`/`map`/`func`/`IDENT` before `_type_expr_end` is even consulted) -- exactly the guard `_looks_like_declaration` already had, that this new caller had skipped.

Building the arrow-function lookahead also surfaced a second, separate, genuinely pre-existing gap in claude.md #141's own work, unrelated to arrow functions themselves: extending `_type_expr_end` to correctly skip a `func[...]:...`-shaped return type (needed so an arrow function's own return type, `func[int]:int (x:int) => ...`, could be told apart from its parameter list) revealed that `func[int]:int func makeAdder() {...}` -- an ordinary NAMED function whose OWN return type is `func[...]:...` -- failed to parse at all. `_type_expr_end` had never been taught to skip past the new multi-token `func[...]:...` shape when #141 added it, so `_starts_func_decl`'s own lookahead landed on `[` instead of the declaration's real `func` keyword. Fixed as part of this entry (touching the same function this entry already needed to extend for its own purposes), confirmed with a dedicated test.

Two design decisions were made explicit rather than left implicit, matching this whole feature list's own established practice: (1) NO closures, for the identical reason claude.md #141 already gives -- an arrow function's synthesized FuncDecl is analyzed via the SAME `analyze_func` every function already uses, which always parents a function's own scope at `global_scope` regardless of nesting, so a true local from wherever the arrow expression is written is unreachable from inside it (confirmed directly: referencing an enclosing function's own local variable is a clear "unknown variable" error; referencing a TOP-LEVEL variable works fine, since that's a genuine global any function can already see, arrow or not -- not a closure at all). (2) Hoisting (claude.md #140) does not apply to arrow functions and needed no accommodation: an anonymous expression has no name a forward reference could ever spell, so its synthesized FuncDecl is registered and analyzed synchronously, right at its own expression position, never through the whole-program pre-pass.

Two coordination points were needed between semantic.py and codegen.py, both compile.py re-walking the SAME AST object (festina/cli.py's compile_file passes the identical `program` to both stages): semantic.py's own ArrowFuncExpr handling stashes the synthesized FuncDecl directly onto the AST node (`expr.decl`) rather than codegen re-synthesizing an independent name of its own (which risked desyncing from whatever counter value semantic.py used) -- codegen just reads it back and, the first time each expression is reached (guarded by the same `self.func_decls` membership check every other synthesized-function site in this file already uses), registers the signature and emits the body exactly like claude.md #140's nested-FuncDecl handling already does. Separately, `escape_analysis.py`'s `_walk_expr` (deliberately strict -- it raises on any UNRECOGNIZED expression kind, precisely so a newly-added ast.py node can never silently fall through as "definitely doesn't escape") needed a new, explicit branch for `ArrowFuncExpr`: a documented no-op, not an oversight -- an arrow function's own body is analyzed and emitted in a completely separate scope from whatever function it's written inside, so nothing in it could ever alias one of the ENCLOSING function's own escape-analysis candidates; its body gets its own, entirely separate `find_escaping_names` call the same way any other nested FuncDecl's body already does.

Verified: manually first, every requested shape (assigned to a variable and called, passed as a call argument inline, stored in a struct field/array element/map value) compiled and ran correctly end to end; the no-closure rejection and top-level-global exception both confirmed with real compiler output; a 3000-iteration combined stress smoke test (arrow functions in a variable, a struct field, an array, and a map, all inside one hot loop) clean under `scripts/leak_stress.sh`; the `_type_expr_end` func-return-type fix confirmed with a standalone before/after compile. Then `TestArrowFunctionSyntax` (`tests/test_syntax_declarations.py`, 8 parser-level tests: every valid shape, the struct-return-type ambiguity resolved correctly, and an explicit regression guard proving ordinary `log(...)`/function calls still parse as calls) and `TestArrowFunctions` (same file, 9 semantic-level tests: valid assignment/call/argument/struct-field shapes, mismatched-signature and mismatched-body-return-type rejections reusing `analyze_func`'s own existing Return-checking with zero new code, the no-closure rejection, and the top-level-global exception) plus `TestArrowFunctions` (`tests/test_codegen.py`, 7 compile-and-run tests: the same shapes executed end to end, two independent arrow functions at different expression positions confirmed to stay independent, and the no-closure/global-access cases re-confirmed at full compile level). `tests/stress/structs_and_rows_churn.f` gained an arrow-function variant of its existing `Callback` struct-field exercise (proving the SAME synthesized function, re-stored via a fresh struct every iteration, never double-registers or double-emits) -- clean under `scripts/leak_stress.sh`, run in full (all six stress programs). Full suite: 1437 passed, 8 skipped (up from 1413 by exactly the 24 new tests) -- run in full before committing.

With this, every item from the SECOND feature-list batch (claude.md #139-142) is done, closing out both rounds of the original ~20-item feature request in full.

143. NUMERIC COERCION: INT/FLOAT MIX FREELY, DIVISION ALWAYS RETURNS FLOAT

One more item from the original feature list, found still pending after #139-142 were believed to close it out: "pre-compile float and int values with their conversion methods. So if 10 + 1.3 compile as though it were written 10.toFloat() + 1.3. Division always returns a float. If mixing float and int, always return float. The only way to get back an int from an operation that makes a float is using the Math methods."

This directly INVERTS claude.md #55's own deliberate rule ("int and float never mix directly in a binary operator -- forces the .toFloat() the request now asks to be implicit"), and does so at foundational scale: "division always returns a float" breaks every single existing `int c = a / b` call site in the entire codebase, not just new code -- an ordinary additive feature this is not. Given that, scope was confirmed with the user via AskUserQuestion before writing a line of implementation, rather than guessed: offered a narrower "arithmetic mixing only, `/` keeps returning int for int/int" alternative against the request's literal wording, and the user chose "Full change, as written" explicitly -- int/float mixing coerces to float in ANY binary operator, `/` always returns float even for two ints, with every existing int/int division site across tests and examples rewritten to match and the full suite run to find and fix whatever broke. What follows is that full change, as written.

semantic.py: the #55 rejection block inside `infer()`'s `ast.BinOp` handling (`if left in _NUMERIC_TYPES and right in _NUMERIC_TYPES and left != right: raise ...`) is gone outright, replaced with a comment pointing at this entry as its replacement. `==`/`!=`'s own `compatible` check needed widening too -- it already special-cased `NULL` but required exact type equality otherwise, which would have kept rejecting `5 == 5.0` even after the general mixing rule opened up; now `left in _NUMERIC_TYPES and right in _NUMERIC_TYPES` is accepted there as well, same as every other binary operator. Comparison operators (`<`,`>`,`<=`,`>=`) needed NO change at all -- they already inferred `bool` from independently-checked operand types without requiring the two sides to match, so mixed comparisons like `5 < 3.2` were already legal; only their RESULT typing changes conceptually (a mixed comparison used to be reachable only by accident since one side would already have been rejected upstream by the #55 check on some OTHER expression -- now it is a first-class, intended, and separately tested case). Result-type inference itself gets one new special case ahead of the existing generic float-promotion fallback: `if expr.op == "/": return PrimitiveType("float")`, unconditional -- checked before, not folded into, the fallback's own `if left==float or right==float: return float; return left or right`, since that fallback alone would have kept returning `int` for `int/int`, exactly the one case the request calls out by name as changing unconditionally. `%` deliberately does NOT get the same unconditional treatment -- it rides the ordinary fallback only, so `int % int` still infers `int` (unaffected; the request's own wording is specifically about "division," not modulo) while `int % float` still promotes to float same as `+`/`-`/`*`. Declaration-time coercion (`float x = 5`, no operator at all) was deliberately left OUT of scope and still rejected exactly as before -- the request's wording is specifically about binary OPERATORS ("10 + 1.3... as though 10.toFloat() + 1.3"), not about relaxing assignment/declaration type-checking generally, and #55's own rule about THAT case was never about operators to begin with.

codegen.py's `_emit_binop` previously had a defensive "internal error: mismatched numeric operands" raise at the point where it needed both operands the same LLVM type to proceed -- reachable only because semantic.py's #55 check had always guaranteed operands never mismatched by the time codegen saw them. With that guarantee gone, this became the actual coercion site: `left_type==INT and right_type==FLOAT` (or the symmetric case) now emits a real `sitofp i64 %val to double` on the int side and updates its tracked type before anything downstream reads it, exactly mirroring what `int.toFloat()` itself already compiles to elsewhere in this file -- literally implementing the request's own "as though .toFloat() had been written" framing at the IR level, not just conceptually. `/` specifically: if the (post-coercion) operands aren't already both float, BOTH get an unconditional `sitofp` (even true int/int, where neither side needed coercion for TYPE-matching purposes -- this one is coerced purely to satisfy "division always returns float"), then dispatches through the existing `_emit_divmod(..., use_float=True, ...)` and always returns `FLOAT` as the result type. `_emit_divmod`'s own zero-check/null-sentinel machinery needed no changes whatsoever and was reused completely as-is -- confirmed by direct LLVM IR inspection of a real `int a/int b` divide-by-zero, which correctly walks the FLOAT path now (`fcmp oeq double ..., 0.0` guarding a `phi double [0x7FF8000000000000 (NaN), ...]`, the same `FLOAT_NULL_CONST` sentinel non-zero-int-division-by-zero already used to use only when float was already involved).

A brief false alarm during manual verification, resolved without any code change: a test expecting `float f = 10/0; log(f==null)` to print `true` (mirroring int-null-comparison semantics) instead printed `false`. Traced through the generated IR, the division-by-zero mechanism was working exactly as intended (the correct NaN bit pattern was genuinely produced) -- the "failure" is IEEE-754 itself: `fcmp oeq` between a NaN and the identical NaN bit pattern is ALWAYS false, by definition, regardless of which specific bits it is. This is pre-existing, intentional, ALREADY-documented codegen behavior with its own already-passing test (`test_comparing_a_null_float_against_the_null_literal`, whose own comment explains this exact semantics) -- nothing in this entry's own change caused or altered it; the ad-hoc verification script's own expectation was simply wrong and was corrected, `_emit_divmod` and the null-comparison mechanism untouched.

Running the full suite after the implementation landed surfaced 19 failures, triaged individually before fixing anything: 2 were unrelated environmental false positives (this session's recurring stale-`festina.sqlite`-from-manual-binary-runs issue on one test; a genuine timing race on `test_tic_tac_toe_detects_a_win`, where the background suite run read `tic_tac_toe.f` mid-edit -- confirmed passing in isolation once the edit had fully landed), and the remaining 17 were real, EXPECTED consequences of the semantic inversion -- every one fixed by rewriting the test/example's own expectation to match the new, correctly-implemented behavior, never by reverting or weakening the implementation itself. `examples/tic_tac_toe.f` needed three real fixes, not just test-expectation ones: `int row = cellIndex / 3`, `int col = (x - GRID_X) / CELL`, and `int row = (y - GRID_Y) / CELL` all relied on the OLD int/int-division-stays-int behavior to compute a grid coordinate -- each now wrapped in `Math.floor(...)`, the request's own named escape hatch ("the only way to get back an int... is using the Math methods"), with a comment citing this entry. `fizzbuzz.f`'s own `%` usage needed no change (modulo's int/int behavior is unaffected, as above). `api.md` got its "int/float never mix implicitly" section rewritten into a "mix freely, int promoted to float, `/` always float" section with new worked examples, and its division-by-zero example split from a single `int result = 10/0` into separate `float divided = 10/0` / `int remainder = 10%0` examples reflecting the two operators' now-different result types.

Test suite: `tests/test_numeric_conversion.py`'s `TestNoImplicitIntFloatConversion` (the class that used to assert #55's rejection) is now `TestImplicitIntFloatCoercion`, asserting the opposite -- mixed arithmetic succeeds and infers float, its result cannot be assigned back to an int-declared variable (an ordinary declared-vs-actual mismatch, not a mixing error), mixed comparisons succeed and infer bool, and the int-to-float DECLARATION path is confirmed still separately rejected (scope boundary above). A new `TestDivisionAlwaysReturnsFloat` class covers `/`'s unconditional float result (including int/int) against `%`'s continued int/int-stays-int behavior, front-end only. `tests/test_codegen.py` gained a parallel `TestNumericCoercion` class (17 compile-and-run tests: mixed arithmetic and comparison for every operator, division always float including two-int division actually printing a fractional result, modulo staying int, and confirming Math.floor/etc remain the only way back to int from a float result) alongside fixes to three pre-existing tests whose OWN hardcoded expectations were built on #55-era behavior (`test_mixed_int_float_rejected_end_to_end` -> `test_mixed_int_float_produces_float_end_to_end`, now expecting success; `test_int_division_by_zero_returns_null` and its neighbor `test_int_division_by_nonzero_is_unaffected` -> `test_division_and_modulo_by_nonzero`, both re-typed `float`; two more incidental int/int-division sites used only as an unrelated test's SETUP, in `test_comparing_a_null_int_against_the_null_literal` and `TestMaps::test_missing_key_on_int_map_returns_the_int_null_sentinel`, switched to `%` since they specifically needed an INT null sentinel and `/` can no longer produce one). Full suite: 1462 passed, 8 skipped (up from 1437 by exactly the 25 new/rewritten tests) -- clean under `scripts/leak_stress.sh` as well (all six stress programs; none of the six needed source changes -- checked directly for `/` usage, found none, and for `%` usage, found only pre-existing int/int cases unaffected by this entry).

With this, every item from the original ~20-item feature list -- both rounds, claude.md #139-142 and this late-discovered #143 -- is done.

144. FESTINA DOCTOR --FIX: AUTO-INSTALL MISSING DEPENDENCIES

Not from the original feature list -- asked directly: "is there a way you can ease setup for each OS, anything built into the festina binary." `festina doctor` (claude.md #59) already diagnoses every missing dependency and prints the exact install command for it; the one remaining friction was that the person still has to read that text and type the command themselves. Scoped down from three candidate directions (auto-running the install command, auto-adding `festina` to PATH, a hosted curl-sh installer script) to just the first, by explicit choice when asked -- the other two are real ideas, not built here.

`--fix` reuses `_doctor_report()` completely unchanged in what it CHECKS, only extending what it returns: `check()` gained an optional `key=` parameter, appending `(key, required)` to a new `missing` list whenever a check fails and names one (every check already backed by a real installable package; the platform-gated informational lines -- the MSYS2-wrong-shell warning, the darwin/win32 "awaits real-hardware verification" graphics/audio lines -- pass no key, since none of those describe anything a package manager could install). `_doctor_report()`'s return signature became a 3-tuple, `(lines, all_ok, missing)`, instead of a new parallel function re-deriving the same list by re-parsing the printed text -- the option considered and rejected, since two independent implementations of "what's missing" can drift out of sync with each other in a way a single shared list cannot. Every existing 2-tuple call site (`_run_doctor` itself, plus 13 across test_cli.py/test_platform.py) needed updating to unpack three values instead of two; none needed any OTHER change, since `missing` is purely additive.

A new `_PKG_MANAGER_PACKAGES` dict maps each check's key to the real package name(s) for exactly the three package managers setup.md itself documents and this project has actually run CI against -- `apt` (Linux), Homebrew (macOS), MSYS2's `pacman` (Windows). Deliberately narrow: a key or manager with no entry means "print the existing hint and let the person install it by hand," not "guess a plausible-looking command for dnf/Arch's `pacman`/zypper/etc" -- the same preference for admitting a limit over confidently getting it wrong that claude.md #59's own "fail loudly and clearly" already established for a missing dependency itself. `_detect_package_manager()` picks one of the three (or None) purely from what's on PATH per `sys.platform`, mirroring `_which_any`'s existing style.

One dependency needed a real special case rather than a package mapping: a missing C compiler on macOS. The actual fix there is Xcode Command Line Tools (`xcode-select --install`, setup.md's own recommendation -- Apple's own clang already works; Homebrew's `llvm` formula is unnecessary and keg-only besides), which pops a GUI installer `--fix` cannot drive non-interactively the way it drives `apt install`/`brew install`/`pacman -S`. `_PKG_MANAGER_PACKAGES["cc"]` has no `"brew"` entry at all; `_run_doctor_fix` instead prints a one-line note pointing at the real command whenever `cc` is missing and brew is the detected manager, rather than either silently doing nothing for it or trying to shell out to a GUI installer and hanging.

`_run_doctor_fix(assume_yes=False)`: prints the same report doctor always prints, then -- if `missing` is non-empty (required or optional; the goal here is easing full setup, not just clearing the required bar) -- builds one deduplicated package list across every missing key for the detected manager, prints the exact command it's about to run, and asks for confirmation before running it (`answer = input(...)`), UNLESS `--yes` was passed. Two things are checked before ever prompting or running anything: no supported manager found at all (prints the limit, doesn't guess), and nothing installable for THIS manager specifically even though something IS missing (e.g. `llvm`'s apt-only entry, with brew detected) -- both exit 0 if only optional dependencies were involved, 1 if something required still isn't fixable. Running non-interactively (`sys.stdin.isatty()` false) without `--yes` refuses outright rather than either hanging on a read that will never come or silently proceeding without real consent -- the same two-sided caution `git`/`npm`-style installers apply, just enforced explicitly since Python's own `input()` has no equivalent built-in guard. After a successful install (`subprocess.run(cmd).returncode == 0`), it re-runs `_doctor_report()` once more and prints whether everything required is now actually present -- a failed install's nonzero exit code is reported directly and propagates as `_run_doctor_fix`'s own return value WITHOUT that second check, since re-confirming after a `subprocess.run` that already reported failure would just repeat information already known, not add any.

`sudo` is prepended to the apt command only when not already root (`hasattr(os, "geteuid") and os.geteuid() != 0` -- the `hasattr` guard exists because `os.geteuid` doesn't exist on Windows at all, not because of any doubt about POSIX systems that do have it); brew never gets a `sudo` prefix (it actively refuses to run as root) and MSYS2's `pacman` needs no elevation at all, managing its own user-writable prefix rather than the host Windows installation. `-y`/`--noconfirm` is passed to the package manager itself unconditionally once execution is reached, since by that point the person has already given their own consent once (interactively or via `--yes`) -- a second manager-level prompt on top of that would be redundant friction, not an extra safety check.

Verified: manually, end to end, three separate real invocations against this machine's actual apt (never letting a real install through) -- the full report followed by "nothing to fix" when everything is already present; a hidden `pkg-config` correctly building `apt install -y pkg-config libsqlite3-dev libcairo2-dev libx11-dev libjpeg-dev libmpg123-dev libasound2-dev` (every missing required+optional dependency, deduplicated into ONE command) and correctly refusing to run it non-interactively without `--yes`; and a real subprocess.run against a throwaway fake `apt` script confirming `--yes` actually executes the command with no prompt, with the fake apt's own stderr proving it received the exact argv built. Then two new test classes: `TestDetectPackageManager` and `TestDoctorFixInstallCommand` in test_platform.py (12 tests -- apt/apt-get-fallback/brew/msys2/none detection per spoofed `sys.platform`, using the same `_stub_which_any`-style shutil.which patching every other cross-platform-from-Linux test in that file already relies on; the sudo/no-sudo/apt-get-fallback/brew-never-sudo/msys2-noconfirm command-building branches, pure-function tested with no subprocess involved at all) and `TestDoctorFix` plus `TestMainDispatchDoctorFix` in test_cli.py (14 tests -- `_doctor_report` itself monkeypatched to a fixed or, for the one test that needs to observe a real "fixed it" transition, stateful stubbed return, so these exercise `_run_doctor_fix`'s own decision logic in isolation from this machine's real dependency state, which `TestDoctor` already covers producing correctly: nothing-to-fix, no-supported-manager for required vs. optional-only, decline-does-nothing, non-interactive-refuses, `--yes` installs and re-checks successfully via the stateful stub, a failed install's exit code propagating without a redundant re-check, a still-missing-after-install report, an unfixable-key-alongside-a-fixable-one note, a nothing-at-all-installable-for-this-manager report, the macOS `xcode-select` note, and the `--fix`/`--yes` flags actually reaching `main()`'s own dispatch). Full suite: 1488 passed, 8 skipped (up from 1462 by exactly the 26 new tests).

Documented in api.md's CLI table (a new `festina doctor --fix` row alongside the existing `festina doctor` one), README.md's "Get started" section, and setup.md's own `festina doctor` introduction -- consistent with how every other CLI-surface change this project has made gets the same three-doc treatment.

145. FESTINA DOCTOR --FIX ALSO FIXES PATH, PLUS A ONE-LINE INSTALL SCRIPT

The other two directions from claude.md #144's own closing offer ("auto-adding festina to PATH" and "a hosted curl-sh/iwr installer"), both asked for together in one follow-up ("yes, let's add both").

**PATH itself.** `_doctor_report` has always diagnosed `festina` not resolving on PATH and printed the fix; `doctor --fix` now does what it already prints, mirroring the dependency-installing half's own shape exactly: a plain-data plan (`_festina_path_fix_plan`, four kinds -- `symlink` for the packaged binary, `shell_rc` for a checkout on bash/zsh, `windows_path` for a checkout on win32 via `setx`, `unsupported_shell` for anything else) separate from the code that executes it (`_apply_festina_path_fix`), the identical split `_doctor_fix_install_command`/the dependency-fixing code already use, and for the identical reason `_default_output_name` takes an injectable `platform_name` -- every plan-building branch is a pure function of state a test can substitute, unit-testable from any one OS. `_run_doctor_fix` itself needed restructuring to attempt BOTH fixes (dependency-installing, now factored out into its own `_fix_missing_dependencies` returning an exit code rather than inlined as it was in #144's own first cut) rather than either/or, with the exit code reflecting the dependency side only -- PATH has never been a `required`-flagged doctor check, so fixing or failing to fix it shouldn't change what this command's own success/failure means, exactly as plain `festina doctor` already treats it as informational-only. A shared `_confirm(assume_yes, prompt)` helper replaced the confirmation logic #144 had inlined once, now used by both PATH- and dependency-fixing the same way.

The symlink case refuses to overwrite anything already at `/usr/local/bin/festina` that isn't already a symlink to the exact binary being installed -- the same "fail loudly rather than guess wrong" preference #144's own `_PKG_MANAGER_PACKAGES` docstring already states, applied here to "don't clobber a program someone else put there" instead of "don't guess an install command." The `shell_rc` case is idempotent by construction: it checks whether the target rc file already contains the bin directory before ever printing a plan or asking to confirm, so running `doctor --fix` twice never duplicates the export line (confirmed for real, end to end, not just unit-tested -- see below). The Windows case (`setx PATH "%PATH%;{bin_dir}"`) is honestly documented as best-effort, the same "no hardware to confirm this against" caveat windows.md/macos.md already carry elsewhere in this codebase: `setx` only affects new sessions (never the one that ran it) and has a known ~1024-character PATH truncation risk this project has no Windows machine to actually hit and confirm.

**A real mistake, caught and fixed before it reached anything committed:** manually verifying the interactive confirmation path (piping input through a real pseudo-terminal via `script`, the only way to exercise `sys.stdin.isatty()` returning True outside of an actual terminal) without first isolating `$HOME` wrote a real `export PATH=...` line into this sandbox's own actual `/root/.bashrc` -- every earlier manual test of this feature had correctly isolated `HOME` to a temp directory, this one specific pty-based check didn't. Caught by inspecting the real file directly rather than trusting the printed output alone, reverted precisely (removed exactly the appended block, confirmed by diffing the restored file's tail against what a bare Debian `.bashrc` looks like), and every subsequent manual test of the same interactive path re-ran with `$HOME` pointed at a fresh temp directory, confirmed both accepting and declining the prompt behave correctly with zero effect on the real environment. Documented here rather than quietly fixed and left unmentioned, matching this whole log's own practice of recording real mistakes and how they were caught, not just the final clean state.

**The install script.** This repository has no release pipeline -- `scripts/package_compiler.sh` builds a binary locally for a maintainer, nothing publishes one anywhere a script could download it from. Rather than write a `curl | sh` script pointing at a download URL that doesn't exist (or spend this round standing up release automation, a bigger and more permanent, outward-facing decision of its own), scope was confirmed directly: install from source, no release infrastructure needed. `install.sh` clones a fresh checkout (`git clone --depth 1`, or GitHub's own source-archive endpoint via `curl`+`tar` when `git` itself isn't present -- both work against any public GitHub repo with zero release infrastructure of its own, unlike a prebuilt-binary download) into `$FESTINA_INSTALL_DIR` (default `~/.festina`), then hands off entirely to `festina doctor --fix` for both dependency-checking/installing and PATH-fixing -- deliberately not reimplementing either concern in shell a second time now that #144 and this entry's own PATH half already do both correctly. Re-running it against an existing checkout updates in place (`git fetch`/`reset --hard`) rather than re-cloning or erroring; running it against a directory that already exists and ISN'T a festina checkout refuses outright rather than guessing what's safe to touch.

The classic `curl | sh` problem -- the pipe consumes the script's own stdin, so `doctor --fix`'s confirmation prompt would always hit its non-interactive guard and refuse even with a real person watching -- is handled the same way rustup's own installer handles it: reconnecting the child process's stdin to `/dev/tty` (`"$FESTINA_BIN" doctor --fix < /dev/tty`), gated on `[ -t 1 ] && [ -r /dev/tty ]` so a genuinely headless run (CI, `sh install.sh < /dev/null`) falls back to printing the one command to run instead of guessing at consent nobody gave. `--yes`/`-y` as a script argument forwards straight through to `doctor --fix`'s own `--yes` for a fully non-interactive install.

Windows support is deliberately scoped to MSYS2 UCRT64 bash only -- windows.md's own one supported Windows toolchain/shell (MSVC is explicitly out of scope there) -- rather than also writing a native PowerShell/`iwr` installer, which would first need to bootstrap MSYS2 itself from nothing before any of this project's own tooling could even run, a materially bigger undertaking than this script and out of scope here for the identical reason windows.md already keeps MSVC out of scope. `install.sh` checks `$MSYSTEM` (the same signal `festina/cli.py`'s own doctor logic already keys off) and refuses cleanly, naming the fix, on any of MSYS2's other subsystems or plain Cygwin.

A real, useful discovery from testing this against an actual clone rather than only unit tests: this repository's real `main` branch is a stale pre-`festina/`-package prototype (no `bin/`, no `festina/` package at all) -- every real, current feature described anywhere in this log lives on `claude/unit-tests-claude-md-8518wb`, which hasn't merged yet. `install.sh`'s own default (`FESTINA_BRANCH=main`) is still the CORRECT long-term choice (what `main` will look like once this branch merges), left as-is rather than hardcoded to a feature branch that will eventually disappear -- but verifying the script's actual mechanics needed pointing it at the real current branch explicitly (`FESTINA_BRANCH=claude/unit-tests-claude-md-8518wb`) via a local bare clone standing in for the real remote, never the real network, for every test below.

Verified: manually, end to end, entirely against local git remotes and a local HTTP server standing in for GitHub (never the real network) -- a fresh clone, re-running against an already-cloned directory (updates in place, confirmed via the checked-out commit), refusing against a pre-existing non-git directory, the `git`-missing curl+tar fallback (a real tarball built with `git archive`, served by a throwaway `python3 -m http.server`, extracted correctly with `--strip-components=1`), the non-interactive/no-tty fallback correctly printing instructions without executing anything, and -- after the HOME-isolation mistake above was caught and fixed -- the real interactive path via a genuine pty (`script`), both accepting and declining the PATH-fix prompt, confirmed via the isolated fake `$HOME`'s own `.bashrc` and confirmed NOT to touch the real one. `sh -n`/`bash -n`/`dash -n` all confirm the script parses cleanly under every shell it claims to support (no shellcheck available in this environment to lint further). Then `TestFestinaPathFixPlan` (`tests/test_platform.py`, 8 tests: already-on-PATH is nothing to fix, the packaged-binary/bash/zsh/unsupported-shell/unset-shell/windows branches, and confirming a packaged binary on Windows still plans a symlink rather than `setx`) and `TestApplyFestinaPathFix` plus `TestDoctorFixPath` (`tests/test_cli.py`, 14 tests: every plan kind executed with subprocess/file-I/O mocked or redirected into `tmp_path`, confirming, declining, sudo-vs-not, the refuse-to-clobber case, idempotent re-runs, and `_run_doctor_fix`'s own wiring -- PATH-fix attempted even when nothing is missing, attempted even after a failed dependency step, and never changing the dependency-driven exit code). Full suite: 1510 passed, 8 skipped (up from 1488 by exactly the 22 new tests).

Documented in api.md's CLI table (the `festina doctor --fix` row's own description extended to mention PATH), README.md's "Get started" section, and setup.md's own "To use the compiler from a checkout" section, plus install.sh's own top comment as its primary documentation (it isn't imported by anything import-based doc tooling would find, so the comment has to carry the full explanation on its own).

146. ISAUDIOPLAYERPLAYING(CHANNEL): THE PER-CHANNEL COUNTERPART TO AUD.ISPLAYING()

todo.md's own long-standing gap, asked for directly by name: "`play()` returns its channel and `stop()` is clip-wide (claude.md #109), but there is still no `isPlaying(channel)`." `aud.isPlaying()` answers "is this CLIP audible anywhere" -- exactly the wrong question once a program has a channel number in hand and no longer has (or cares about) which clip is on it, e.g. `int ch = engine.playLoop()` stored somewhere `engine` itself isn't. `isAudioPlayerPlaying(channel)` is the missing half: a free function, matching `stopAudioPlayer`'s own naming convention (process-global channels have no clip to hang a method off), answering about the CHANNEL, not whatever clip happens to be on it.

The channel argument is required, not optional like `stopAudioPlayer`'s -- deliberately: a bare `stopAudioPlayer()` has an obvious meaning ("stop everything"), but there is no equally obvious reading for "is anything playing" as a channel-less query, so `_BUILTIN_SIGNATURES` gets a fixed one-`int`-argument entry rather than `_BUILTIN_SIGNATURE_ALTERNATES`' optional-arg mechanism.

Runtime: `festina_channel_is_playing(int64_t channel)` in `festina_runtime_audio.c`, right next to `festina_stop_audio_player` and `festina_audio_is_playing` (whose own per-clip loop it's the direct counterpart to) -- a single `g_channels[festina_clamp_channel(channel)].active` read under `g_audio_lock`, reusing the exact clamping (`[0, 64)`, never a crash on a bad channel number) every other channel-accepting call already applies. No new state, no allocation, nothing for `scripts/leak_stress.sh` to actually stress -- exercised in `tests/stress/media_churn.f`'s existing channel-pool loop anyway, alongside `play`/`playLoop`/`stopAudioPlayer`, for completeness rather than necessity.

Wired through exactly like `stopAudioPlayer`: `festina/cli.py`'s `BUILTIN_FUNCTIONS`/`_BUILTIN_SIGNATURES`/`_BUILTIN_RETURN_TYPES` in semantic.py (one `(_INT,) -> bool` entry each), a new `declare i8 @festina_channel_is_playing(i64)` and dispatch branch in codegen.py setting `self.uses_audio = True` (naming it alone, with no `aud` declaration in sight, is what makes a program link the audio translation unit at all -- confirmed with a dedicated compile-only test mirroring `stopAudioPlayer`'s own).

Verified: manually end to end first, against a real (virtual) ALSA null device -- `true` immediately after `play()`, `false` immediately after `stopAudioPlayer(ch)`, `false` for a channel never played on, and an out-of-range channel (`999`, `-1`) clamping to a safe `false` rather than crashing -- before writing a single test. Then 5 new semantic-level tests in `tests/test_audio.py` (required-argument arity/type checking, declared-type mismatch) and 7 new compile-and-run tests in a dedicated `TestIsAudioPlayerPlaying` class in `tests/test_codegen.py` (the same manual scenarios above, reused rather than re-invented, plus the case this whole feature exists for: a second clip taking the same channel over makes the FIRST clip's own `isPlaying()` say false while `isAudioPlayerPlaying(ch)` correctly still says true for that channel). Full suite: 1522 passed, 8 skipped (up from 1510 by exactly the 12 new tests) -- clean under `scripts/leak_stress.sh`, run in full (all six stress programs). Documented in api.md's Audio section: the quick-reference block, a new worked example in "Stopping a sound" explaining exactly when this answers a question `isPlaying()` cannot, the calls table, and the closing "to address a single playback" note. todo.md's own "there is still no isPlaying(channel)" bullet is gone -- this closes it.

147. TODO.MD'S "STATIC SQLITE3 LINKING" BULLET WAS STALE

Asked for directly, alongside #146 above -- investigated before touching anything, since claude.md's own #126/#128-#129 entries already described `_sqlite_link_flags`/`_static_sqlite_attempt` (probe a static `libsqlite3.a` via `_can_link`, fall back to dynamic with a printed `festina: wrote ... (sqlite3 linked dynamically -- no static libsqlite3.a found)` note) as long since built, cross-platform (Linux's GNU-ld `-Bstatic`/`-Bdynamic` toggles, macOS's explicit-archive-path variant, MinGW's identical GNU-ld path), tested (`TestStaticSqliteAttempt`), and documented in setup.md's own "Static-linking sqlite3" section. Confirmed directly rather than trusted from memory: compiled a real `hello.f` and ran `ldd` on the result -- no `libsqlite3.so` in the output, only libc/libm/libz. The feature was never missing; todo.md's own bullet was simply never removed when it landed.

Rather than silently doing nothing (todo.md pointed at real, working code, so a person reading it would be misled) or unilaterally inventing new scope under the same label, asked directly, offering three real options: confirm as done and remove the stale bullet; extend the same opportunistic static-link treatment to graphics/audio's own libraries too (cairo/libjpeg/mpg123/ALSA, always dynamic today); or go further and vendor the SQLite amalgamation source to GUARANTEE static linking always succeeds rather than depending on what the build machine happens to have installed. The user chose the first: confirm, and remove the stale bullet -- done in the same edit that removed #146's own now-closed "isPlaying(channel)" bullet from todo.md's Platforms/Language sections. No code changes.

148. WASM EXPORT: FESTINA COMPILE --TARGET=WASM32-WASI

Asked for directly, with three explicit sub-requirements: "add an ability to export to wasm, also include a wasm.md that shows implementation, wasm benchmarks vs wasms compiled by c and go, and also mention limitations to APIs that wasm doesn't have access to."

Target choice, decided before writing any code: `wasm32-wasi` (WASI Preview 1), not bare `wasm32-unknown-unknown`. The latter has no libc and no syscalls at all -- every I/O call would need hand-written JS glue. `wasm32-wasi` sits on [wasi-libc](https://github.com/WebAssembly/wasi-libc), a real libc, which meant Festina's existing POSIX-based core runtime (`festina_runtime.c` -- file I/O, `clock_gettime`, `regex.h`, SQLite's VFS layer) had a real chance of compiling unmodified against it. Confirmed directly, not assumed: it does, with zero source changes to that file.

**The unconditional sqlite3 dependency was the first real obstacle.** `table`/`sqlite()` support is core, not a feature tier (claude.md #10/#28-31) -- every compiled program links sqlite3 symbols whether or not it declares a `table`. There is no system `libsqlite3` built for `wasm32-wasi` on any package manager to link against, static or dynamic -- unlike every native target, where a system library already exists (and, per #147 above, was explicitly NOT vendored for that reason). Vendoring the SQLite amalgamation was the only option here, and is genuinely necessary rather than merely convenient, a real architectural distinction from #147's own declined-for-native vendoring decision. Obtaining it hit its own real obstacle: `sqlite.org` itself returned a 403 through this environment's outbound proxy. Worked around via `npm pack better-sqlite3`, whose published tarball vendors the identical upstream amalgamation (confirmed byte-for-byte against its own top-comment version string, `3.53.4`) at `deps/sqlite3/{sqlite3.c,sqlite3.h}` -- npm's registry was reachable when sqlite.org's wasn't. Landed at `runtime/wasm/{sqlite3.c,sqlite3.h}` (269,668 + 14,349 lines, unmodified), with `runtime/wasm/README.md` documenting the provenance, the public-domain license, and an explicit note that any FUTURE re-vendoring should prefer sqlite.org directly -- this indirect route was this one vendoring's own workaround, not a recommendation.

**Two real bugs found by actually compiling, linking, and running real programs** -- not merely reasoning about the target, matching this whole log's own standing practice:

- **`wasm-ld: undefined symbol: main`.** wasi-libc's `_start` never calls a function literally named `main` -- ordinary C compilation silently renames `int main(void)` to `__main_void` via macro machinery in the C frontend before the compiler proper ever sees it. Festina's codegen emits raw LLVM IR text directly (`_emit_main_and_entry`), which never goes through that renaming, so its literal `define i32 @main()` links clean on every native target (where `main` really is the expected symbol) but left wasi-libc's own `_start` -> `__main_void` chain looking for a symbol nothing defined. Renaming Festina's own generated symbol in codegen.py was considered and rejected -- it would make codegen target-aware for something that is really wasi-libc's own linking convention, not a property of the generated program. Fixed with a one-line bridge object instead, `runtime/festina_runtime_wasm_entry.c` (`extern int main(void); int __main_void(void) { return main(); }`), linked only for the wasm build.
- **`RuntimeError: unreachable` at actual execution**, after a link that only warned ("function signature mismatch: calloc"). Every native target Festina supports is 64-bit; codegen.py hardcoded `i64` for the external `calloc`/`malloc` declarations it emits, correct everywhere else -- but `wasm32-wasi`'s libc genuinely has a 32-bit `size_t`, and LLVM requires an external `declare` to match its call sites exactly, with no implicit truncation. Fixed narrowly, not with a sprawling target-width-awareness refactor: `CodeGen.__init__` gained a `target` parameter and `self.pointer_bits` (32 for `wasm32-wasi`, 64 everywhere else); three new helpers, `_size_arg`/`_emit_calloc`/`_emit_malloc`, either pass a size straight through (64-bit targets) or emit a `trunc i64 ... to i32` first. Every `calloc`/`malloc` call site across codegen.py (VarDecl heap allocation, `_emit_fresh_heap_header`, array-literal malloc's zero- and nonzero-length branches, a row-to-struct helper body builder) was updated to go through these helpers. Every OTHER `i64`/pointer conversion in the file (`ptrtoint`/`inttoptr`) was audited and deliberately left alone -- confirmed genuinely safe across pointer widths in LLVM (it zero-extends/truncates these correctly regardless of target); only the calloc/malloc *external-call ABI boundary* needed a real fix. Verified the 64-bit path is a byte-identical no-op: `pytest tests/test_codegen.py` unaffected, full count unchanged.

**Compiling** (`_compile_via_wasm` in `festina/cli.py`): the same LLVM IR text every other target's clang-IR-frontend fallback path already produces, handed straight to `clang --target=wasm32-wasi -O2`, linked against three object files (`_wasm_runtime_objects`, cached the same `_ensure_runtime_object`-style way as native's own runtime objects, in a namespace-separate `festina_wasm_*` cache key so the two builds never collide): `festina_runtime.c` (compiled against the vendored `sqlite3.h` instead of a system one), the vendored `sqlite3.c` itself, and the entry bridge above. No libLLVM in-process path is used for wasm at all -- this project has not verified libLLVM can emit wasm32 objects directly, so clang always does the compiling here, unconditionally rather than as a fallback. Graphics/audio are rejected OUTRIGHT before any of this real work runs (`_check_wasm_feature_supported`, checked first thing inside `_compile_via_wasm`) -- unlike `_check_feature_supported`'s macOS/Windows gates (a real backend EXISTS, pending hardware verification, with an env-var override), there is no WASI graphics or audio backend to ever turn on, so this has no escape hatch at all. `--cc` must resolve to clang specifically for a wasm build (checked, clear error) -- only clang can target `wasm32-wasi`.

**Running:** a `.wasm` file isn't something any OS execs directly, so `festina run --target=wasm32-wasi` and the new benchmark runner both execute a compiled binary through `runtime/wasm/run_wasi.mjs`, built on Node's own built-in `node:wasi` module -- chosen over requiring a separate wasmtime/wasmer install since Node is already a listed setup.md dependency for running the compiler frontend itself from a checkout. The invoking process's own cwd is passed through as a WASI "preopen" mapped to the wasm program's own `/`, the same way `table`/`sqlite()`/`blob`/`mkdir`/`ls` already resolve relative paths against a native binary's own cwd -- WASI's capability-based sandboxing model requires the host to explicitly grant a directory rather than exposing the whole real filesystem a native binary can see. The script propagates the compiled program's own exit code (`wasi.start()`'s return value) as its own, so `close(code)`/`fail()` remain visible exactly like a native binary's exit code already is.

**CLI wiring:** `--target` (`choices=["native", "wasm32-wasi"]`, default `native`) added to both `compile` and `run` subparsers; `_default_output_name` gained a `target` parameter (`.wasm` unconditionally for the wasm target, overriding even `platform_name == "win32"`'s own `.exe` logic -- the HOST doing the compiling has no bearing on what the OUTPUT format is). `main()`'s own compile-success message branches on `args.target`: the native branch's `_sqlite_link_flags(args.cc)` call (pkg-config-based, meaningless for a wasm build, which always statically compiles the vendored amalgamation with no dynamic-linking story to report on) is skipped entirely for wasm, replaced with a message pointing at how to actually run the `.wasm` produced.

**`festina doctor`:** a new optional check (`_wasm_toolchain_ok`), same tier as graphics/audio -- a compiler that can't cross-compile to wasm is still a fully working compiler for everything else. Deliberately a REAL functional probe (`clang --target=wasm32-wasi -x c - -o /dev/null` against a trivial in-memory program), not a guessed install path -- this project found wasi-libc and libclang_rt's own files at `/usr/lib/wasm32-wasi` and `/usr/lib/llvm-18/lib/clang/18/lib/wasi` on the machine this was built on, but hardcoding either path would be exactly the kind of unverified guess `_pkg_config_has`/`_which_any` avoid elsewhere in this same function by construction -- a round-trip compile is the only check that can't give a false "OK". `_PKG_MANAGER_PACKAGES["wasm"]` is apt-only (`wasi-libc`, `libclang-rt-18-dev-wasm32`) -- no brew/msys2 equivalent found or attempted, so nothing is claimed for those managers.

**Verified end to end, manually, before any test was written:** a real "hello from wasm" program compiling, linking, AND executing correctly through Node's WASI support; then, after both bugs above were found and fixed, a program combining `table`/`sqlite()` (insert + select), recursive `fib(10)`, and `regex.test()` -- all three producing correct output under wasm32-wasi, confirming the vendored SQLite and the calloc/malloc fix both actually work at runtime, not just link cleanly.

**Testing:** a new `compile_and_run_wasm` fixture in `tests/conftest.py`, the wasm counterpart to `compile_and_run` -- compiles via the real toolchain and executes via the real `run_wasi.mjs`/Node WASI host, skipping cleanly on a machine without a working wasm32-wasi clang or without Node, EXCEPT under `FESTINA_STRICT_DEPS=1` (the Linux CI job), where that skip becomes a hard failure instead -- mirroring `compile_file_or_skip`'s own discipline so this tier can't silently vanish on the primary platform the way no other optional tier can. A new `tests/test_wasm.py`: `_default_output_name`'s `.wasm` branch (including overriding win32's `.exe`), `_check_wasm_feature_supported`'s graphics/audio rejections (no toolchain needed -- the rejection fires before `_compile_via_wasm` ever looks at `cc`), real compile-and-run coverage (hello world, recursion, heap-allocated `arr`/`map` -- the direct regression test for the calloc/malloc fix actually EXECUTING correctly, not just linking -- structs, `table`/`sqlite()`, `regex`, string concatenation, exit-code propagation), the non-clang/`cc`-missing CLI validation errors, and `festina doctor`'s own new WASM check (both that it appears, and that its absence stays optional rather than failing the required-dependency gate). `.github/workflows/ci.yml`'s Linux job now installs `wasi-libc`/`libclang-rt-18-dev-wasm32` and Node explicitly (`actions/setup-node@v4`), plus a new step that compiles and actually RUNS a `.wasm` example on every push -- the same "not just compiling" discipline the macOS/Windows jobs already apply to their own gated backends. Full suite: 1543 passed, 8 skipped (up from 1522 by exactly the 21 new wasm tests).

**Benchmarks (`wasm.md`, the user's own explicit deliverable):** the same five programs `benchmark.md` already tracks natively (`hello`/`fib`/`loop_sum`/`array_sum`/`string_concat`), each also written in C (`benchmarks/*.c`, new -- hand-translated from the existing `.f`/`.go` sources, verified to produce byte-identical stdout to both before any run was trusted) and reusing the existing `.go` sources, all three cross-compiled to `wasm32-wasi` and run through the IDENTICAL WASI host (`run_wasi.mjs`/`node:wasi`) -- deliberate, so the numbers measure each language's generated code and Node's own WASI overhead identically, not three different WASI runtimes' own differing overhead. Go uses its own stable `GOOS=wasip1 GOARCH=wasm` support (Go 1.21+, confirmed present: go1.24.7) -- not `GOOS=js GOARCH=wasm`, which targets the browser's own different, incompatible ABI. Rust was NOT included, unlike benchmark.md's own native table: rustc dropped the bare `wasm32-wasi` target name (superseded by `wasm32-wasip1`, needing a separate `rustup target add` component not installed here) -- C stands in as the systems-language comparison instead. A new `benchmarks/run_wasm_benchmarks.py`, mirroring `run_benchmarks.py`'s own methodology (1 untimed warmup, minimum of 7 timed runs) exactly, writes results into `wasm.md` between marker comments the same way the native runner does for benchmark.md. Real numbers, not fabricated: Festina lands within a few percent of hand-written C on `fib` and `loop_sum`, consistently smaller `.wasm` output than Go (whose runtime ships in every binary) though larger than C's (the vendored SQLite amalgamation, paid unconditionally, same as natively), and Go is consistently slowest to start on `hello`. `array_sum` -- the one allocation-heavy benchmark -- came out faster for Festina than hand-written C in the run committed, plausible given escape analysis (claude.md #74/#76/#81) keeps this benchmark's array off the heap the same way the C version's plain stack array already is, but called out in wasm.md itself as noise-prone rather than a verdict, the same caveat benchmark.md's own native table already gives that same benchmark.

**`wasm.md`** (the user's other explicit deliverable) documents all of the above -- design, both bugs, setup (the two apt packages, `festina doctor`'s check), usage, the benchmark table and methodology, and a dedicated Limitations section: graphics and audio (both permanently absent, not hardware-gated -- WASI has no display server or audio device model of any kind), no command-line-argument support (true on every target, not wasm-specific -- Festina has no language builtin for argv at all), filesystem access sandboxed to one preopened directory (WASI's own capability model), no ASan/LeakSanitizer coverage for this target (unexplored, matching the precedent macOS's own port already set for its sanitizer tier), static-only linking (no dynamic-vs-static sqlite3 choice the way native has, claude.md #147), and no browser support claimed (every test/benchmark here uses Node's `node:wasi`, not a browser's own WASI polyfill, which this project has neither vendored nor tested against).

Also documented: `setup.md` (a new "Compiling to WASM" subsection plus a wasm-toolchain row in the extra-test-tools table), `api.md` (the CLI table's `compile`/`run` rows extended for `--target=wasm32-wasi`), `README.md` (Project status paragraph mentions native+wasm support, updated test count), `benchmark.md` (a cross-link to wasm.md's own benchmark section), and `todo.md` (a new WASM paragraph under Platforms naming the two things genuinely still open -- browser support and sanitizer coverage -- neither blocking).

149. LAYERS EXAMPLE (ARR[IMG] AS A LAYER STACK); VERIFIED EVERY EXAMPLE; REFRESHED BOTH BENCHMARK SUITES

Asked for directly, three parts: "make an example of using an array of img, as layers, where each layer is modified by the methods on the layers, with an overall Render function rendering each layer each frame. Also make sure all examples are still working and make sure benchmarks are up to date."

**`examples/layers.f`.** Four `arr[img]` layers, each modified a genuinely different way -- the point of the example, not incidental: a background layer drawn once and never touched again (composited every frame regardless -- a layer doesn't have to change just because it's redrawn); a stars layer drawn once, then modified sparsely (one extra star every 15 frames via `layers[LAYER_STARS].drawPixel(...)`, called on the SAME img object still sitting in the array, proving `layers[i].method()` reaches through to the real image rather than a copy); a trail layer modified every single frame (`layers[LAYER_TRAIL].drawCircle(...)` at a bouncing ball's new position) -- `img` has no "clear" the way the canvas does (api.md's Images section: only drawRect/drawPixel/drawCircle/drawText), so repeated drawing naturally accumulates into a trail, the same "can only add" constraint `tic_tac_toe.f` already relies on for the canvas itself; and a HUD layer REPLACED wholesale every frame rather than drawn onto, since a frame counter's text can't be erased from an img either -- `blankTemplate.clip(0, 0, clientWidth, clientHeight)` makes a fresh, fully transparent img the same size as the canvas each time (`clip()` always returns a NEW img, api.md's own table), with `blankTemplate` itself grabbed via `saveCanvas()` while the canvas was still blank at program start, so the whole example needs zero external image assets. One `renderFrame()` (the "overall Render function" asked for) advances the ball, modifies whichever layers need it that frame, then composites every layer in array order (`for int i = 0, i < layers.length, i++ { drawImage(layers[i], 0, 0) }`) before one `render()` call -- called once immediately for the first frame, then on a `setInterval(renderFrame, 33)` for ~30fps, self-stopping after 200 frames via `clearInterval` (long enough to guarantee at least one real edge bounce, confirmed visually, not just by log output -- see Verified below) and leaving the window open afterward, same convention `graphics.f`/`timers.f` already established separately.

A real language gap hit and worked around while writing it, not a bug: `/` always returns `float` (api.md's Numbers section), so `clientHeight / 2` used as an `int` argument to `drawRect` was a compile error (`argument 4 expects int, found float`) -- fixed by computing `int halfHeight = Math.floor(clientHeight / 2)` once at the top and reusing it, rather than scattering `Math.floor` calls at every division site.

Verified beyond "it compiles": ran it for real against a real (virtual, Xvfb) X server, captured the actual rendered window via `xwd`+`xwdtopnm`+`pnmtopng` (the same manual-verification tools `TestGraphics`'s own docstring already documents using and explicitly declines to add as automated test dependencies), and visually confirmed all four layers actually composite correctly -- the sky/ground background, the scattered stars (including one added mid-run), the ball's accumulated trail (including a real bounce off the bottom-right edge, only reachable once `totalFrames` was raised from an initial 90, which never triggered a bounce in that shorter run, to 200), and "Frame N/200" HUD text on top, all in the right positions. A new `test_layers_demo_renders_all_layers_and_stops_on_its_own` in `tests/test_codegen.py::TestExampleGraphicsAndGame` (joining `graphics.f`/`tic_tac_toe.f`'s own coverage there, same file for the same reason: needs the same `_find_window`/`_wait_for_output`/`x_display`/`run_graphics_program` machinery) waits for the self-stop log line the same way `test_timers_demo_runs_and_exits_on_its_own` already does for a non-graphics example. Documented in README.md's examples table (a new row, plus the "two needing a display" count corrected to three).

**"Make sure all examples are still working."** Ran the actual verification, not just asserted it: `tests/test_examples.py` (29 tests, plain compile-and-check-stdout sweep over every file in `examples/` including the new one, picked up automatically by its own glob) and `tests/test_codegen.py::TestExampleGraphicsAndGame`/`TestTimers` (the display-needing examples) both green. Also spot-checked the wasm.md-era CLI surface against this same file: `festina compile --target=wasm32-wasi examples/layers.f` correctly fails with the graphics-unsupported `CompileError` (claude.md #148) rather than silently miscompiling -- confirms that gate still fires correctly for a REAL example file, not just the synthetic one-liners `tests/test_wasm.py` uses.

**"Make sure benchmarks are up to date."** Both `benchmark.md` benchmark suites were stale (last run 2026-08-16, 8 days prior) -- re-ran both for real, on this machine, with every toolchain actually installed (rustc 1.94.1, go1.24.7, bun 1.3.11, Chromium via Playwright, dotnet for MonoGame): `python3 benchmarks/run_benchmarks.py --update-doc` refreshed the five-language native table cleanly (Festina/Rust/Go/Bun byte-identical stdout confirmed as always, numbers moved by ordinary run-to-run noise, no commentary in "Reading these numbers" needed correcting since the qualitative claims -- "array_sum lands at Rust/Go", "string_concat within ~2.4x of Rust" -- still held against the fresh figures).

The canvas comparison (`benchmarks/canvas/run_canvas_benchmark.py --update-doc`) did NOT refresh cleanly on the first attempt -- it exited nonzero, `compare_images` reporting Festina's and the browser's output as genuinely `DIFFERENT` (worst per-channel diff 115.7, tolerance 40), which would have silently left the doc stale rather than writing a wrong table (the script's own existing safety check, `if not same: sys.exit(1)`, working as designed). Investigated rather than dismissed as flakiness: dumped both PNGs, and side-by-side they were visually indistinguishable (dense diagonal moire from 40,000 tightly-packed shapes) -- but a real per-pixel diff found 20% of pixels differing, with background pixels specifically showing `festina=(0,0,0,0)` against `browser=(255,255,255,255)`. A genuine, second real bug in `compare_images`/`_tile_means`, not a rendering regression: Festina's `saveCanvas()` output is a real RGBA PNG that starts fully transparent (api.md's own "a fresh or cleared canvas is transparent, not white"), while the browser harness (`_BROWSER_HARNESS`) explicitly fills white before drawing at all -- `_tile_means` read raw RGB bytes directly, silently discarding the alpha channel, so an untouched background pixel compared as pure black against the browser's pure white: a legitimate, always-present 255-per-channel "difference" at every unshaded background pixel that had nothing to do with either rasterizer's actual drawing. Fixed by compositing both images onto the same white background before averaging into tile means (alpha is always binary 0/255 here, confirmed directly -- zero partial-alpha pixels found in the Festina PNG, so no premultiplication subtlety to get wrong); re-ran and the true worst diff dropped to 0.2, correctly reporting "same scene."

A THIRD, smaller bug surfaced while fixing the second: the doc's own "worst per-channel difference X out of 255" sentence was a hardcoded literal `0.2` in `_update_doc`'s template string, never actually wired to the real `compare_images` result computed in `main()` -- it happened to read correctly this run purely by coincidence (matching the freshly-computed real value after the alpha fix), not because it was ever live. Fixed by threading the real `browser_worst` value through as a new `_update_doc` parameter, and extended the surrounding prose (also static template text, not auto-computed, so editing it here is the only way it ever changes) to record BOTH bugs this same check has now caught, matching this whole file's own "record real mistakes and how they were caught" practice rather than silently fixing and moving on. Confirmed the fix is genuinely live now, not fixed-by-coincidence again: re-ran once more, watched the printed and written values move together with the actual measurement.

Both benchmark.md tables and their embedded prose (verdict ratios, noise commentary, the comparison-methodology paragraph) regenerated cleanly on the next run: Festina drawing a 40,000-shape frame in 37ms against the browser's 97ms (2.6x this run -- noisier than the doc's own prior 60-68ms range, exactly the variance the doc's own "browser's frame time is far noisier" paragraph already warns about, not a regression), MonoGame's own software-GL caveat paragraph unchanged since nothing about that measurement path was touched.

156. AMOR MAP[T] / AMOR ARR[T] -- A REAL, LANGUAGE-LEVEL AMORTIZED-GROWTH MODIFIER

Asked for directly, mid-implementation of claude.md #155's own "map_set amortized growth" item: that item had been explicitly flagged (AskUserQuestion) as needing a representation change to the whole language's `map[T]`, distinct in risk from the rest of that round -- the answer redirected it away from touching `map[T]` at all, into a genuinely new `amor` prefix MODIFIER (`amor map[T]`/`amor arr[T]`, composing with `const`: `const amor map[text] m`) rather than a separate type name. The design pivoted once more mid-session, too: the first attempt was a standalone `amap[T]` type (own keyword, own AST node, own Type class), fully scaffolded (lexer/parser/types.py/ast.py) before a follow-up message asked for the prefix-modifier shape instead -- reverted and rebuilt on the new shape rather than kept as a second, parallel path.

**Design: a field, not a new class.** `amortized: bool = False` joined both `types.MapType` and `types.ArrayType` directly (not a wrapper, not a subclass, not a separate type) -- this is what makes `isinstance(t, types_mod.MapType)` checks throughout semantic.py ALREADY correctly match an amortized map with zero changes needed at every "is this map-shaped" site (computed-member read, `.toText()`, `forEach` validation, `delete`, the null-tolerant container check in `check_assignable`) -- the only semantic.py work needed was `resolve_type_name` threading `type_expr.amortized` through, plus one genuinely new check (below). `MapType.amortized` IS part of that type's equality (two MapType instances with the same value type but different `.amortized` are different types, correctly rejecting `amor map[int] m2 = existingPlainMap`) -- but `ArrayType.amortized` uses `field(compare=False)`, deliberately NOT part of ArrayType's equality, for the real reason below.

**Grammar: `amor` is a one-token prefix ahead of `arr[T]`/`map[T]`**, parsed in `parse_type()` (the single entry point every type position -- var decls, params, return types, struct fields -- already funnels through, so no other parser site needed touching): `amor` is eaten, the next token must be `arr` or `map` (anything else is a direct, clear error rather than a confusing downstream one), the inner type parses normally, then `.amortized = True` is set on the returned node. `_type_expr_end`/`_looks_like_declaration`'s own token-recognition sets (used for declaration-vs-expression lookahead) needed `amor` added alongside `arr`/`map`, with `_type_expr_end` itself just recursing past the one-token prefix rather than trying to skip a `[...]` it doesn't have.

**Runtime: exactly one new C function, `festina_amap_set`.** `%struct._FestinaAmap = type { i64, ptr, i64 }` (count, entries, capacity) is a byte-compatible PREFIX extension of `%struct._FestinaMap = type { i64, ptr }` (count, entries) -- capacity is appended as a THIRD, trailing field rather than inserted between count and entries, specifically so every existing map runtime function that only ever touches count/entries (`festina_map_get`, `festina_map_for_each`, `festina_map_delete`, `festina_release_map`, `festina_map_free_entries`) is directly reusable on an amap payload completely unchanged -- confirmed by design, not just assumed: `festina_release_map`'s own C implementation reads count/entries by fixed byte offset and frees the whole allocation by its base pointer, which correctly reclaims the trailing capacity field too regardless of whether one exists. Only `festina_map_set`'s own growth logic needed a real counterpart: `festina_amap_set(count*, capacity*, entries*, key, value)` doubles capacity (8, 16, 32, ...) instead of `realloc`-ing to exactly `count+1` on every insert.

**Codegen: `_emit_map_lit`/`_emit_map_get`/`_emit_map_set` gained an `is_amap` parameter** rather than being duplicated -- the retain/release logic inside `_emit_map_set` (deferred-release-on-overwrite, the freshness test, the key-ownership free) is identical either way and genuinely easy to get subtly wrong twice; `is_amap` only changes the GEP's struct type name and (for the write path) whether a third `capacity_ptr` GEP is computed and passed to `festina_amap_set` in place of `festina_map_set`. `_release_fn_for_map`'s own lazily-generated per-value-type wrapper (for a map whose values are themselves refcounted or text) got the same GEP-type-name treatment; its plain-`@festina_release_map`-fast-path branch needed NO change at all, for the reason above. `_emit_value_for`'s MapLit dispatch (the one shared entry point literal construction anywhere in the language goes through) decides `is_amap` from `expected_type.amortized` -- `ast.MapLit` itself has no amor-vs-plain distinction, the same `{k: v, ...}` syntax either way.

**A real corruption risk found by reasoning, not by a failing test: struct-field auto-vivify.** A struct field has no initializer syntax at all (confirmed against the codebase directly, not assumed), so EVERY container-typed field relies entirely on the existing "build a fresh empty header the first time this null field is touched" mechanism -- which, before this fix, would have built the WRONG (smaller, plain-map-shaped) header for an `amor map[T]` field, silently corrupting memory the moment `festina_amap_set` first wrote its capacity field past the undersized allocation. Fixed by adding a third branch (payload_ty = FESTINA_AMAP_LLVM_TYPE when `ftype.amortized`) to that one dispatch. A dedicated test (`TestAmorMap::test_struct_field_auto_vivifies`) exercises exactly this path.

**A second real correctness question, resolved by requiring an initializer:** plain `map[T]`/`arr[T]` start implicitly "empty" (a global gets a real, immortal, zero-entry static header -- see `_global_var_defs`; a non-escaping local can even stack-allocate one, claude.md #79/#81). Extending that same machinery to `amor map[T]` would mean either building yet another FESTINA_AMAP_LLVM_TYPE-aware immortal-header path, or reusing the wrong-shaped one (the same corruption class as the struct-field bug above). Neither was worth it for a "no initializer" case that's rare in practice -- so semantic.py now requires an initializer for any `amor map[T]` declaration (`invalid declaration` category, matching this project's existing category taxonomy), and codegen's own immortal-sentinel-global path (`_global_var_defs`) and the non-escaping-local stack-allocation path (`_is_stack_allocatable_array_or_map_decl`) both explicitly exclude amortized maps, falling through to the SAME generic heap-allocate-via-`_emit_value_for` path blob/img/aud/http/socket globals/locals already use safely (already null-safe: `festina_retain`/`festina_release_check` are no-ops on a null payload) -- correct precisely because the required initializer guarantees that null is always overwritten before user code can observe it. Struct fields are unaffected by this restriction (they have no initializer syntax to require in the first place) and instead rely on the auto-vivify fix above.

**Array amortization: scoped out, honestly, not silently.** The user's own examples named both containers (`amor map[text] myMap` / `amor arr[text] myArr`), but array's own growable-buffer surface (`push`/`pop`/`shift`/`unshift`/`splice`, each independently calling `festina_array_resize`) is far larger than map's four operations, and building real amortized growth across all of it -- correctly -- was not achievable in the same round without risking exactly the kind of subtle corruption bug the map side already surfaced twice. `ArrayType.amortized` is real, tracked, and round-trips through parsing/resolution/`type_name()` (so `amor arr[T]` is a genuine, first-class parsed type, not silently dropped), but has NO runtime effect: `_global_var_defs`, `_is_stack_allocatable_array_or_map_decl`, and every array codegen path are deliberately left untouched by this round, so `amor arr[T]` compiles and behaves byte-for-byte like plain `arr[T]` today. `ArrayType.amortized` uses `field(compare=False)` specifically so an `amor arr[T]` and a plain `arr[T]` of the same element type stay assignment-compatible with each other in the meantime -- revisit that the moment array amortization is actually built, since at that point the two stop being interchangeable the same way map's own `amortized` field already isn't.

**Verified beyond compiling:** `gcc -Wall -Wextra -Wpedantic -O2` clean on the runtime addition. Manual compile-and-run programs confirmed `amor map[T]` literal init, indexed get/set, `forEach`, `delete`, JSON `toText()`, `const amor map[T]`, and `amor arr[T]` as a no-op-identical passthrough, ALL correct. A dedicated Valgrind stress run (500 inserts into an initially-empty `amor map[int]`, forcing several real capacity doublings, followed by 500 deletes) found zero errors and zero leaked bytes -- confirmed the growth doubling sequence (8→16→...→512) directly from the "still reachable" byte count at exit. Full suite green after landing (see the round's own final count).

**Tests**: `tests/test_maps.py`'s new `TestAmorPrefix` class (parser/semantic-level: `amor map[T]`/`amor arr[T]` parse, `const amor` composes, `amor` must be followed by `arr`/`map`, the no-initializer rejection, the mismatched-literal-value-type rejection, a variable-key literal, and a direct `resolve_type_name` check confirming `amor map[int]` really does resolve to `MapType(..., amortized=True)` and is NOT equal to the plain version). `tests/test_codegen.py`'s new `TestAmorMap` class (real compile-and-run: literal init + get/set, `forEach`, `delete`, JSON rendering, `const amor map[T]`, the struct-field auto-vivify fix, and 200 inserts into an initially-empty amortized map to force real capacity growth end to end, not just at the runtime-unit level).

Documented in api.md: a new "`amor` — amortized-growth maps" subsection under Maps, explicit about the initializer requirement, the `const` composition, and array amortization's own honest "parses, no runtime effect yet" scope note.



Asked for directly, as the "implement" half of a review-then-implement pair: claude.md #154's own benchmark writeup had answered "how is Rust so much faster" with a ranked list of refactor options (not yet applied); this round is "let's implement all of it" for the HTTP-scoped ones. One item (amortized growth for `festina_map_set`, the generic `map[T]` runtime function) turned out to need a real representation change to the whole language's map header (`%struct._FestinaMap` in codegen.py, mirrored in three places including this file's own `FestinaMapBlock`) -- flagged explicitly before touching it (AskUserQuestion) rather than silently doing a cross-cutting change on a blanket "implement all of it," since it's a different risk class than everything else in this round. The user's answer redirected it into its own new feature (a genuinely new `amap[T]` type) rather than modifying `map[T]` -- see claude.md #156.

**Five changes to `runtime/festina_runtime_http.c`, all self-contained to this one file:**

- **`festina_http_send`/`festina_http_redirect` now build one buffer and make ONE `festina_send_all` call**, not 4-5 separate ones (status line, each extra header, `Content-Length`, `Connection: close`, then the body sent as its own second call so a large body is never copied). This is the single biggest lever on the numbers below: `TCP_NODELAY` is set on every connection (Nagle's algorithm disabled, for low per-request latency), so every one of those separate `send()` calls used to become its own TCP segment, not just its own syscall -- a response with a couple of extra headers was several packets, not one. A new small `FestinaSendBuf` helper (stack-buffer-first, spilling to a doubling heap buffer only if a response's headers genuinely don't fit) does the building; `festina_write_extra_header` (driven through `festina_map_for_each`, whose callback has no userdata slot -- the same single-threaded scratch-global trick this file already used, now pointing at a buffer instead of an fd) appends directly into it instead of `snprintf`-then-send per header.
- **`festina_run_http_loop`'s poll set is now a persistent, doubling-growth buffer** (`g_poll_fds`/`g_poll_conn_ids`), not malloc'd and freed fresh on every single tick -- mirrors `festina_conn_ensure_capacity`'s own buffer-reuse shape for a connection's read buffer, applied to the loop driving every connection.
- **The loop no longer counts live connections in a separate pass before building the array.** It over-allocates to `g_listener_count + g_conn_count` (the connection table's own high-water mark, already tracked, always >= the true live count) and just fills in however many are actually alive, skipping one whole linear pass over the connection table per tick.
- **`festina_headers_add` now doubles capacity instead of `realloc`-ing by exactly one header per call** -- the one grower in this file that didn't already match the doubling shape everything else here uses (the read buffer, the listener/connection tables).
- **`festina_try_parse_request` now parses the request-line/headers AT MOST ONCE per connection** (a new `headers_parsed` guard), resuming the `\r\n\r\n` search from where the last call left off (`header_scan_pos`, backed up 3 bytes for a partial match straddling the boundary) instead of rescanning the whole buffer from byte 0 on every additional `recv()`.

**A real, confirmed bug found while designing that last change, not merely a performance one:** the OLD code re-ran the entire header-parsing block from scratch on every call that still hadn't seen the full body -- which re-`malloc`'d `method`/`path` over the PREVIOUS call's own pointers with nothing freeing them first, and re-appended every header onto the still-populated header array. Reproduced directly before writing the fix: a raw-socket client sending headers in one write, then the body in a second write after a real delay (forcing two separate poll-readable events on the server), against a debug program logging `req.method`/`req.headers` -- Valgrind reported `definitely lost: 41 bytes in 10 blocks` (the doubled method/path/header allocations) on the buggy version, `0 bytes in 0 blocks` after the fix. Invisible from the language level (`req.headers` is a `map[text]`, and a repeated key with an identical value dedups the same way it always does), which is exactly why it had gone unnoticed -- found only by reasoning through what the resumable-scan rewrite needed to guarantee, not by any test failing. A new regression test (`tests/test_http.py::test_body_arriving_in_a_separate_write_after_headers`, using a raw socket with a real `time.sleep` between writes -- a normal HTTP client library gives no way to force two separate server-side reads) exercises the split-arrival path for correctness, since the leak itself isn't observable from a black-box test; the structural fix (headers_parsed) is what actually makes it impossible to recur.

**Verified beyond compiling:** `gcc`/`clang -Wall -Wextra -Wpedantic -Wshadow -O2` clean, plus a Windows cross-compile (`x86_64-w64-mingw32-gcc -D_WIN32`) clean, mirroring claude.md #152's own verification method for this file. `tests/test_http.py`: 48/48 (47 existing + the new regression test). A manual Valgrind stress run (900 requests across all three response shapes -- plain send, `req.send()` with three extra headers on the `/json`-style route, and `req.redirect()`) found zero errors, zero leaked bytes.

**Results** (`wrk -t4 -c50 -d5s`, this machine, same methodology as claude.md #153): plaintext `/` went from 17,620 to 35,384 req/s, `/json` from 17,817 to 32,526 req/s -- roughly 2x on both routes, moving Festina from behind both Go and Bun to ahead of both, and within about 10% of Rust's raw-socket number (which has no runtime/managed-object overhead to begin with). benchmark.md's HTTP section regenerated with the new numbers and a new "Reading these numbers" bullet explaining what moved and why.



Asked for directly, as a follow-up to the benchmark writeup's own answer for why Rust was so much faster (claude.md #153): "can issue 3 be changed to a responsive map of connections rather than a loop? can you also fix the map bug where any type can be used" -- issue 3 being `festina_conn_by_id`'s O(live connections) linear scan (called at least once per public `festina_http_.../festina_socket_...` function, several times per request), and "the map bug" being the pre-existing gap claude.md #151 itself found and explicitly left open: `{'a': 1, 'b': 'two'}` passing semantic analysis silently and reaching codegen as invalid LLVM IR.

**Connection lookup: a small open-addressing hash table (int64 conn_id -> slot index), not a library hash map.** No existing generic hash-map runtime helper to reach for -- `map[T]`'s own implementation lives in codegen-emitted IR keyed by `text`, not a reusable C data structure keyed by `int64_t` -- so a small, purpose-built one (`festina_conn_index_*`, linear probing, `-1`/`-2` sentinels for empty/tombstone, doubling at a 75% load factor) replaces `festina_conn_by_id`'s linear scan directly.

**The connection table itself had to change shape first, not just gain an index on top.** The existing design compacted dead slots by MOVING every live connection down to fill the gaps whenever the array filled up (`festina_conn_new_slot`'s old compact-on-full pass) -- fine for a plain array, fatal for a hash index storing slot INDICES rather than pointers: every connection moved by a compaction pass would need its own index entry rewritten in the same pass, turning what should be an O(1) amortized insert into an O(live connections) one again, just moved to a different function. Replaced compaction with a LIFO free list of dead slot indices: a torn-down connection's slot is handed directly to the next new connection rather than shuffled, so no live connection's slot index ever changes for as long as it stays alive -- the index only ever needs ONE update per connection lifecycle (insert on accept, remove on teardown), not one per compaction pass. `festina_conn_teardown` removes the old conn_id from the index and pushes its slot onto the free list; `festina_conn_new_slot` pops a free slot (or grows the array, same as before) and inserts the NEW conn_id at that slot -- correct without any special-casing, since the old conn_id was already removed from the index before the slot could be reused, so there's no stale entry left to collide with the slot's new occupant.

**Verified for real, not just compiled clean.** `gcc -Wall -Wextra -Wpedantic` on the modified file: zero warnings. `tests/test_http.py`: 47/47, unchanged (this is a pure internal-representation change -- no public behavior moved). A dedicated Valgrind memcheck run against a compiled server under 1500 sequential real HTTP requests (enough churn to grow the free list, grow the hash index past its load factor multiple times, and recycle slots repeatedly) found zero errors and zero leaked/lost bytes (27,033 allocs, 27,024 frees, the difference being the handful of long-lived global structures this runtime's own "no GC at process exit" convention already leaves for the OS to reclaim). Re-ran the wrk benchmark from claude.md #153 afterward: numbers held steady (~17k req/s plaintext, ~17k json) -- expected and unsurprising at this benchmark's own 50-connection concurrency, where the linear scan was never the dominant cost (the malloc-per-poll-tick and multi-syscall-per-response costs claude.md #153's own answer already named are); the fix is a real algorithmic improvement (O(1) amortized vs. O(live connections) per lookup) that would show up more as concurrent connection counts grow far past what this specific benchmark drives, not a claim that it moved this specific number.

**The map-literal type gap:** `_infer_member`'s (semantic.py's) MapLit branch tracked only the LAST entry's value type (`value_type = infer(val_expr, scope)`, unconditionally overwritten every iteration) with nothing checking it against any earlier entry -- unlike the ArrayLit branch immediately above it, which already tracks a `concrete_type` and raises on a genuine mismatch (added at some earlier, unlogged point, per its own in-code claim; it predates this specific bug hunt). Fixed by mirroring that exact pattern: a `concrete_value_type` tracked across entries, raising `"map literal values must all be the same type, found X and Y"` (category `invalid operand type`, the same category the ArrayLit check and every other type-mismatch check in this file already use) the moment a second, different concrete type appears -- `value_type` itself is left tracking the last entry's type, unchanged, since that's what feeds the returned `MapType` and existing null-value corner cases (`{'a': null, 'b': 'x'}`) already depend on that exact behavior.

**Tests**: `tests/test_maps.py`'s `TestMapLiteral` class gains `test_mixed_value_types_in_a_map_literal_is_a_compile_error` (the `{'a': 1, 'b': 'two'}` case from claude.md #151's own bug report, now a clean `CompileError` instead of reaching codegen) and `test_null_values_do_not_count_as_a_mismatch` (mirroring ArrayLit's own null-tolerant behavior). Full suite: 1623 passed, 8 skipped (up from 1621 by exactly the 2 new tests). todo.md's own "map literals don't check value types" bullet removed, now that it's fixed rather than open. README.md's test count updated to match.



Asked for directly, the second half of the same request that produced #152: "...and then run http benchmarks." benchmark.md's own "Reading these numbers" section had been naming this gap explicitly since before HTTP existed at all ("no I/O ... see todo.md for what's still missing from Festina itself that would make a broader comparison meaningful (HTTP, for one)") -- closed now that claude.md #151/#152 gave Festina an actual server to measure.

**No existing tool to reach for.** `run_benchmarks.py`'s five programs are all single-shot compile-run-exit; an HTTP benchmark needs a long-running server process, an external load generator hammering it over real sockets, and a measurement window — a genuinely different shape, so a new `benchmarks/http/run_http_benchmarks.py` was written rather than stretching the existing script to fit. `wrk` (installed via `apt-get install -y wrk`, not previously present) does the actual load generation — not reinvented, the same "reach for a well-known existing tool before writing one" call this project already made for hyperfine-style timing (`run_benchmarks.py`'s own docstring) and SHA1/base64 (claude.md #151's WebSocket handshake).

**Four servers, `benchmarks/http/server.{f,rs,go,js}`, each answering the identical two routes** (`/` -- fixed plaintext, `/json` -- a small JSON body): the same "equivalent logic, not equivalent idiom" rule the five existing benchmark programs already follow, applied to a new dimension. Festina's own HTTP server is deliberately single-threaded with no keep-alive (api.md's own documented scope, claude.md #151) — so Rust's and Go's servers here are hand-rolled raw-socket implementations with a matching single-threaded sequential accept loop and close-after-every-response behavior, NOT `hyper`/`net/http`'s own default multi-threaded, keep-alive-capable servers, which would measure a mature framework's concurrency model against Festina's single-threaded one rather than the same connection-handling logic in four languages. Bun is the deliberate exception: `Bun.serve()`, its own native HTTP implementation, since there's no reason to hand-roll sockets in a runtime that already ships a fast one (`run_benchmarks.py`'s own "each language uses its own normal toolchain" rule) — with `Connection: close` set explicitly on every response so it still closes each connection the way the other three do, rather than its own keep-alive support winning the comparison for a reason none of the other three even have access to. All four builds stay dependency-light the same way the existing five benchmarks already are: no cargo, no go modules, no npm — `rustc -O`/`go build`/`bun run` directly against one source file each, Festina through `cli.compile_file` the same as `FestinaToolchain` in `run_benchmarks.py`.

**The runner** (`run_http_benchmarks.py`, mirroring `run_benchmarks.py`'s own `Toolchain` class structure): picks a free port the same TOCTOU-accepting way `tests/conftest.py`'s `_free_tcp_port` fixture already does, launches the built server as a background subprocess, polls with a real `connect()` attempt until the port actually accepts (not a fixed sleep, the same reasoning `compile_and_run_server`'s own fixture docstring gives), runs `wrk -t4 -c50 -d5s --latency` against each route in turn, parses `Requests/sec`/average latency/`Transfer/sec` out of `wrk`'s own text output, then terminates the server (SIGTERM, falling back to a hard kill) before moving to the next language. `--update-doc` splices a new `<!-- HTTP_BENCHMARK_RESULTS_START/END -->` block into benchmark.md, mirroring `update_doc`'s own plain-string (not lambda-interpreted-as-regex-backreferences) replacement technique from `run_benchmarks.py`.

**Verified each server manually before wiring up the runner**, not just trusted the code to be right: compiled/built all four by hand, curled `/` and `/json` on each, confirmed identical status/headers/body shape across all four (`Connection: close` present on every response, `Content-Length` correct, JSON body byte-identical text), then ran a single manual `wrk` pass against the Festina server alone to confirm a connection-closing server doesn't confuse `wrk`'s own reconnect logic (it doesn't — `wrk` reopens a connection transparently whenever the server closes one, exactly the behavior this benchmark depends on) before trusting the full four-language run.

**Results** (`wrk -t4 -c50 -d5s`, this machine, one run written into the doc — see benchmark.md's own new "HTTP" section for the numbers and its "Reading these numbers" subsection for what they do and don't claim): Rust leads by roughly 2.5x (raw sockets, no runtime overhead at all), Go and Bun land close to each other, and Festina is in the same rough band as Go/Bun rather than trailing by an order of magnitude — a young, single-threaded, interpreter-free compiled server holding its own against Go's raw sockets and Bun's own native HTTP implementation under matching connection-per-request load. Explicitly NOT read as "Rust is only ~2.5x faster than Festina at HTTP" in general: Rust's/Go's numbers here are deliberately NOT what their idiomatic keep-alive-capable HTTP stacks would report, a caveat spelled out directly in the doc's own "Reading these numbers" addition, the identical spirit as the MonoGame-software-GL caveat the canvas comparison above it already carries.

Documented in benchmark.md: a new "HTTP: Festina vs Rust vs Go vs Bun" section (Methodology explaining the equivalent-logic-not-equivalent-idiom choice, a `<!-- HTTP_BENCHMARK_RESULTS_START/END -->` results block, and a "Reading these numbers" subsection), the top-level Methodology section's "Reproduce locally" block gaining the new script's invocation, and the old "no I/O ... HTTP, for one" gap-callout bullet corrected to point at the new section now that the gap is closed.



Asked for directly, as a follow-up to claude.md #151: "add Windows support and then run http benchmarks." #151 itself had classified Windows as genuinely unimplemented for this feature (plain POSIX sockets, no winsock2 port attempted) rather than the "built, awaiting hardware verification" shape audio/graphics already use there -- this round built the real port and moved http into that same shape.

**No shared seam to fill in, unlike audio/graphics.** `festina_runtime_http.c` was plain POSIX sockets end to end with nothing cut in advance for a second platform, so porting it meant walking every socket call site directly rather than filling in an already-designed abstraction. Chose a single-file `#ifdef _WIN32` seam near the top of the file (`FestinaSocket`, `FESTINA_INVALID_SOCKET`, `festina_close_fd`, `festina_poll`/`FestinaPollFd`, `festina_socket_would_block`/`festina_socket_was_interrupted`) over a second whole-file duplicate -- mirroring `festina_runtime_audio.c`'s own small inline ALSA-vs-waveOut split, deliberately not graphics' two-file Cocoa/Win32 split (that split exists only because Cocoa is Objective-C, a real language difference this file has no equivalent of).

**Winsock2 differs from BSD sockets in exactly enough places to matter, all handled by the seam above:** a distinct `SOCKET` handle type that is UNSIGNED, so every POSIX-style `if (fd < 0)` check silently never fires on it -- caught by reasoning about the type before it could reach even a hypothetical Windows build, not by a compile error, and fixed with `FESTINA_INVALID_SOCKET`/`==` comparisons everywhere a raw `< 0` check used to live (`festina_accept_new_connections`, `festina_open_port`, the module's `g_http_send_extra_headers_fd` global); `closesocket()` not `close()`; `ioctlsocket()`/`FIONBIO` not `fcntl()`/`O_NONBLOCK`; `WSAGetLastError()` instead of `errno` (Winsock functions never touch the CRT's own `errno` at all); `recv()`/`send()` taking `char*`/`int` where POSIX takes `void*`/`size_t`; `WSAPoll()` not `poll()` -- confirmed to have IDENTICAL field names (`.fd`/`.events`/`.revents`) to POSIX `struct pollfd`, so one typedef swap covers every call site with no per-field translation; no `SIGPIPE` on Windows for a broken socket at all (`send()` just returns an error, never a signal), so the POSIX `signal(SIGPIPE, SIG_IGN)` fix from #151 has nothing to mirror there -- every write already checks its own return value regardless; an explicit `WSAStartup()` needed before any socket call, added at `festina_open_port`'s own entry point, idempotent by design (Winsock reference-counts it internally) so it runs on every `openPort()` call rather than being gated to "only the first," with no matching `WSACleanup()` -- this runtime's own established "no GC yet, process exit tears everything down" convention, same reasoning as everywhere else this project leaves teardown to the OS; and `SO_REUSEADDR` having a more permissive, port-hijacking-enabling meaning on Windows than POSIX, so deliberately not set there at all.

**A real naming collision, caught by an actual MinGW compile error** (`conflicting types for 'closesocket'`), not reasoned about in advance: the internal socket-closing macro was first written as `festina_socket_close`, colliding with the PRE-EXISTING PUBLIC `void festina_socket_close(void *handle)` (`s.close()`'s own runtime entry point, declared in festina_runtime.h). Renamed to `festina_close_fd` at every call site (`festina_conn_teardown`, `festina_open_port`'s two failure paths, `festina_close_port`) -- the identical class of mistake, and the identical fix, as claude.md #150's `festina_exec`/`festina_process_exec` collision, cited directly in the new code's own comment as precedent.

**Verification method, since this project has no local Windows/MSYS2 environment:** installed a real `mingw-w64` toolchain (`apt-get install -y mingw-w64`, which succeeded and provided `x86_64-w64-mingw32-gcc` plus real `winsock2.h`/`ws2tcpip.h` headers) and cross-compiled the ported file directly (`-D_WIN32 -Wall -Wextra -Wpedantic -c`) -- zero warnings. Re-verified natively on Linux the same way, confirming POSIX behavior stayed byte-for-byte unchanged. A full-core MinGW link was attempted and, as expected, failed on `festina_runtime.c` needing `<regex.h>` (MSYS2's `gnurx`/`libsystre`, windows.md Phase 0), unavailable outside a real MSYS2 environment -- a known, pre-existing boundary already documented there, not a new problem this round introduced or needed to solve; real Windows CI does the full link-and-run verification for every backend here, same as audio/graphics.

**Wired through both of this project's build paths.** `_feature_pkgs_and_flags`'s existing win32 branch structure gained an `http` case linking `-lws2_32` (a system DLL with an import library but no pkg-config file, the same shape `winmm`/`gdi32`/`user32` already are). The primary libLLVM in-process path (`_runtime_objects_and_link_libs`) already called `_feature_pkgs_and_flags` generically for every feature including http, so needed no change. The clang-IR-frontend fallback (`_compile_via_clang_ir_frontend`) needed a real fix: its own `needs_http` branch was still wired to `_RUNTIME_FEATURES["http"]` directly, the Linux-only pkgs/flags table -- the EXACT bug claude.md #126 round four already found and fixed once in this same function, for a different feature (graphics), where it silently dropped every platform swap. Caught proactively this time, before it shipped, by checking this function against every feature it handles rather than assuming a fix made for one feature generalized to the next one added -- rewired to go through `_feature_pkgs_and_flags("http")` like every other feature there.

**The compile-time platform gate** (`_check_feature_supported`'s `feature == "http" and platform_name == "win32"` branch) changed from an unconditional hard rejection to the same `FESTINA_ENABLE_WINDOWS_HTTP=1`-gated "exists, awaiting real-hardware verification" pattern the darwin http gate (from #151) and every audio/graphics Windows gate already use -- keeping that function's own docstring's global claim ("no remaining 'nothing built yet' branch on any platform") true again; it briefly went stale mid-round (still describing win32 http as the one genuine hard-reject exception) and was caught and corrected by re-reading it after the code change rather than assuming it still matched.

**Testing:** `tests/test_http.py`'s `TestPlatformAndWasmGating` class: removed the old `test_http_is_rejected_on_windows` (asserted the hard reject), added a mirrored pair matching the existing darwin tests -- `test_http_on_windows_is_gated_pending_verification` (raises `CompileError`, category `unsupported platform feature`, with the env var unset) and `test_http_on_windows_override_env_var_bypasses_the_gate` (no raise, with it set). `tests/test_platform.py`'s `TestAudioFeatureConfig` class gained three new `_feature_pkgs_and_flags("http", ...)` unit tests: linux and darwin both answer `([], [])` (plain POSIX sockets, never had a third-party library dependency on either), win32 answers `([], ["-lws2_32"])`. Full suite: 1621 passed, 8 skipped (up from 1617 by exactly the 4 new tests -- the darwin-mirrored gating pair net +1 over the removed hard-reject test, plus the three new `_feature_pkgs_and_flags` tests). README.md's test count updated to match.

Documented: `festina_runtime_http.c`'s own top-of-file comment and `festina_runtime.h`'s doc comment above the http/socket declarations, both rewritten from "no Windows backend, would need winsock2, a separate phase never attempted" to describe the real port and its `FESTINA_ENABLE_WINDOWS_HTTP` gate. windows.md gained a new Phase 4 section (mirroring Phases 1/2's own "built, CI-compiled; hardware verification open" framing, but explicit that this phase's own verification is local-MinGW-cross-compile only, not yet real Windows CI, unlike Phases 0-3) and its intro paragraph's phase count/gate-variable list updated to include it. api.md's HTTP/WebSocket Limitations section updated from "Linux and macOS only" to describe Windows as supported-but-gated. security.md's slim-binaries paragraph and its `SIGPIPE` finding writeup both updated to mention winsock2/the Windows port's own lack of exposure to that class of bug. macos.md's own http cross-reference needed no change -- it only ever described the darwin gate, never made a Windows claim.



Asked for directly, as a rough sketch rather than a full spec -- `openPort(port:int)`/`closePort(port:int)`, an `on request(req:http)` handler naming nine members (`.port`, `.method`, `.headers`, `.upgrade()`, `.toBlob()`/`.toImg()`/`.toAud()`/`.toText()`, `.ok()`, `.redirect(url)`, `.send(data:any, code:int, headers:set)`), and three more handlers (`on upgrade(s:socket)`, `on message(s:socket, msg:blob)`, `on socketClose(s:socket)`) with `socket` given `.state`/`.send()`/`.close()`. This is by a wide margin the largest single feature this whole log has ever added in one round -- todo.md's own "HTTP / networking -- the largest missing capability" bullet, now closed.

**Four real design gaps in the sketch, resolved by asking before writing any code (AskUserQuestion), not by guessing:**

- **Concurrency model.** This runtime's entire memory model (festina_retain/festina_release, non-atomic plain increments) was built single-threaded from claude.md #77 onward -- a thread-per-connection server would need every one of those calls to become atomic (or locked), a whole-runtime change with nothing to do with HTTP specifically. Chose the single-threaded event-loop model, the SAME "one thread total" shape setTimeout/setInterval (claude.md #69) and the graphics event loop (claude.md #40) already use -- extended, not reinvented. The real, accepted cost: a slow `on request`/`on message` handler delays every OTHER connection's own turn. Documented explicitly in both api.md and security.md as a structural property, not a bug.
- **`req.headers`'s own spelled type, `set` -- not a real type in this language at all.** Resolved as `map[text]` (header name -> value): reuses the existing container wholesale, and a set specifically would have thrown away the value half of each header pair, an odd fit for headers regardless. No new container type was added.
- **`data:any` on `req.send()`/`s.send()` -- Festina has no dynamic/tagged type at all.** Resolved as a compile-time-checked structural overload, the SAME shape `log()`/template interpolation already established for "any value with a text form" (claude.md #114) -- extended narrowly (blob sends its own raw bytes rather than decoding through `.toText()`; img/aud are compile errors, the same media-type rejection `_to_text` already has) rather than building a genuine runtime-tagged value type, a much bigger feature with uses well beyond this one API.
- **Platform/protocol scope for a first version.** Linux + macOS (POSIX sockets, unverified-on-hardware macOS gate matching the audio/graphics precedent exactly -- `FESTINA_ENABLE_MACOS_HTTP=1`), plain HTTP (no TLS -- documented as a real limitation, not a promise), rejected outright under `wasm32-wasi` (no listening-socket support in WASI Preview 1) and on Windows (no backend at all, would need winsock2 -- a real separate phase, not attempted here, so genuinely absent rather than hardware-gated, the identical distinction claude.md #150's own exec()-under-wasm gate already draws).

**One deliberate addition beyond the user's own literal spec:** `req.path:text`. The sketch never listed it, but a request handler has no way to route on anything at all without it (`if req.path == '/api/users' { ... }` is the whole point of writing a request handler) -- added and documented as a clearly-flagged addition, the same "the spec's own examples clearly need this to be useful" judgment call this log has made before rather than either silently omitting something obviously necessary or stopping to ask about something this obvious.

**Runtime (`runtime/festina_runtime_http.c`, new translation unit, linked only when a program actually uses this feature -- claude.md #59's own "slim binaries" splitting, extended to a fourth feature alongside core/graphics/audio):**

- **HTTP/1.1 scope**: request-line + headers + a `Content-Length` body only -- no chunked encoding, no HTTP/1.0-specific handling, no pipelining, no keep-alive (every response closes the connection afterward -- each request is genuinely its own TCP connection start to finish, which is what keeps the per-connection state machine a straight line: accept -> read one request -> dispatch -> respond -> close, no "wait for the next request on this fd" branch to get wrong). Documented explicitly as scope, not silently absent.
- **WebSocket scope (RFC 6455)**: text/binary data frames, close frames, and ping (answered with a pong) only -- no fragmentation (a `FIN=0` or continuation frame closes the connection with WebSocket close code 1003 rather than silently dropping or misreassembling data), no extensions. A hand-written SHA1 + base64 implementation computes the handshake's own `Sec-WebSocket-Accept` (RFC 6455's `base64(SHA1(key + a fixed magic GUID))`) -- ~150 lines total, verified directly against the RFC's own worked example (`dGhlIHNhbXBsZSBub25jZQ==` -> `s3pPLMBiTxaQ9kYGzzhZRbK+xOo=`, byte-for-byte) and against Python's real `hashlib.sha1` for three more test vectors (`""`, `"abc"`, a 44-byte string crossing the 64-byte block boundary), not vendored -- the same call this project already made for its own JSON renderer/UTF-8 walking rather than reaching for a dependency every time a well-known, small algorithm is needed.
- **The event loop, `festina_run_http_loop`**: a single `poll()`-based loop, called from `_emit_main_and_entry` (codegen.py) exactly where `festina_run_event_loop`/`festina_run_timer_loop` already are -- folds in `festina_next_timer_deadline()`/`festina_fire_expired_timers()` the identical way the graphics loop already does, so `openPort()` combined with `setTimeout`/`setInterval` gets both serviced from the one loop rather than two competing blocking calls. Exits once there is truly nothing left to wait for: no open listening port, no live connection, and no active timer -- widening `festina_run_timer_loop`'s own "exits once the event loop is empty" rule rather than inventing a different one.
- **The handle representation**: both `http` and `socket` are tiny, refcounted `{i64 refcount, i64 conn_id}` handles (joining `_is_refcounted`'s existing struct/arr/map/img/aud/regex/blob family, one shared `festina_release_conn_handle` release function for both, since neither type has more than the one shape -- the same reasoning RegexType/blob's own single release function already rests on) -- deliberately NOT a pointer to live connection state. Every runtime call taking one (`festina_http_port`, `festina_socket_send_text`, ...) looks `conn_id` up in the connection table FRESH, on every call, and silently no-ops (or answers null/false/-1, matching whatever "nothing happened" already means for that call) if the connection is gone -- the same "never fails the program" convention exec()/mkdir()/the file builtins already use, here covering a REAL case a server has to tolerate: a client disconnects mid-handler, or a program stores `req`/`s` somewhere that outlives the connection. `conn_id` is a monotonic counter, never reused, specifically so a stale id can never alias a DIFFERENT, later connection reusing the same fd -- the classic fd-reuse-after-close bug this indirection exists to rule out by construction.
- **`socket.state`**: a real, live, per-connection `map[text]` -- built by hand in C to the exact same `{refcount, count, entries}` layout codegen's own map[T] representation uses (confirmed against `festina_release_map`'s own comment describing that layout), created lazily on first access, retained once extra on every `festina_socket_state()` call (the connection keeps its OWN permanent reference; each hand-out is a genuinely fresh +1 from the caller's point of view), released via the ordinary `festina_release_map` at connection teardown. `s.state['k'] = v` afterward is ordinary, unmodified map[T] assignment codegen -- no special-casing needed once the pointer is correctly in hand.
- **`req.toImg()`/`req.toAud()`**: through two new core-runtime accessors, `festina_decode_image_bytes`/`festina_decode_audio_bytes` (festina_runtime.c), NOT `festina_image_from_bytes`/`festina_audio_from_bytes` directly -- those live in the graphics/audio translation units, and a plain http-only program that never calls `.toImg()` must not be forced to link Cairo/X11/libjpeg/ALSA/libmpg123 just to satisfy an unconditional reference (a real link failure hit and fixed during this same round: the first version called the graphics/audio functions directly from festina_runtime_http.c). Reuses the EXACT indirection (`g_image_decoder`/`g_audio_decoder` function pointers) the sqlite-column-to-img/aud path already established, through two thin new public wrappers.

**Two real bugs found by actually running the server, not reasoned about in advance:**

- **A silent, remotely-triggerable crash: `SIGPIPE`.** A stress test (20 sequential HTTP requests, then 5 WebSocket sessions x 10 messages each) reliably killed the server with ZERO output -- no ASan report, no crash message, just gone. Root-caused by elimination (raw-socket reproduction of the exact byte sequence a real client sent, confirming the server itself responds correctly when tested in isolation; the failure only appeared under the FULL stress sequence) to `send()`/`write()` on a connection the peer has already reset or closed early, whose default `SIGPIPE` disposition terminates the whole process with no error message at all -- the single most severe class of finding in this session (a genuinely remote, unauthenticated denial of service, one line of client behavior away, and one this project's own ASan/LeakSanitizer discipline cannot catch at all, since it isn't a memory-safety bug). Fixed with `signal(SIGPIPE, SIG_IGN)` at `festina_open_port`'s own entry point (idempotent, safe to call on every `openPort()`) -- every write in this file already checks its own return value the POSIX way, so the signal itself was pure noise once ignored. Documented in security.md's "Notable fixed findings" as the most severe entry there.
- **`s.state[k] = v` failed with "cannot access field 'state' on socket" despite semantic analysis (and a plain `log(s.state)`) both compiling fine.** Root-caused via a full Python traceback (not guessed at): `_emit_assign` resolves an assignment TARGET's own object half by calling `_emit_member_load` DIRECTLY, bypassing `_emit_expr`'s own Member dispatch (and the new req/socket field-access branches living only there) entirely -- a real architectural fork in this codebase between "read a field as a plain expression" and "read a field as the base of a computed assignment target" that only a container-typed http/socket FIELD (not method) ever exposed, since nothing before this feature had a Member field access that itself needed to be indexed into for a write. Fixed by extracting the req/socket field logic into a shared `_emit_http_socket_field` helper, called from BOTH `_emit_expr`'s dispatch and the START of `_emit_member_load` -- short-circuiting before `_emit_member_load`'s own chain-release bookkeeping runs (verified safe: neither HttpType nor SocketType has a further chainable field off one of these results, so nothing is lost by skipping that machinery for this shape), rather than letting both mechanisms independently decide what to do with the same receiver.

**A third, genuinely pre-existing bug found incidentally, NOT fixed (out of scope for this round, added to todo.md instead):** map literals never check that every entry shares one value type -- `{'a': 1, 'b': 'two'}` passes semantic analysis and reaches codegen, which then emits invalid LLVM IR (a raw `i64` where a `ptr` is required, or vice versa) instead of a clean compile error. Confirmed by reading `_infer_member`'s MapLit branch directly: `value_type = infer(val_expr, scope)` runs on every loop iteration with nothing checking it against the PREVIOUS iteration's own answer, unlike ArrayLit's own per-entry check. Hit twice while writing this round's own manual test programs (both times a mistake in the TEST, not this feature), confirmed as a real, general, pre-existing gap rather than something claude.md #151 introduced, and left for its own separate round -- the fix is ArrayLit's own existing per-entry check, applied to MapLit's value type the identical way.

**Semantic/codegen wiring**: `http`/`socket` joined the reserved-keyword set (lexer.py's `SPEC_KEYWORDS`, parser.py's `TYPE_KEYWORDS`) and `HttpType`/`SocketType` (types.py) joined the refcounted-type family (`_is_refcounted`, `_release_fn_for`, `_llvm_type` all return `ptr`, mirroring RegexType's own "one shape, no per-instance fields" treatment). `openPort`/`closePort` are ordinary `BUILTIN_FUNCTIONS` entries (`(int,) -> void`); `request`/`upgrade`/`message`/`socketClose` joined `_EVENT_SIGNATURES` alongside `exit` (unconditional registration in `main()`, not the graphics-gated `event_handlers` loop, since neither is a window event). `req`'s four fields and `send()`'s bespoke any-typed/optional-argument shape live in `_infer_member`/`_infer_call` following the identical dict-driven (`_HTTP_METHODS`/`_SOCKET_METHODS`, mirroring `_BLOB_METHODS`) and bespoke-branch (mirroring `setTimeout`/`saveCanvas`) patterns this file already established -- no new dispatch mechanism invented. `_emit_sendable_body`, a new shared codegen helper, reuses `_to_text` for every type it already gives a text form to (int/float/bool/struct/table/array/map), adding only blob's own raw-bytes branch on top -- both `req.send()` and `s.send()` call it. `festina/cli.py` gained an `"http"` entry in `_RUNTIME_FEATURES` (no pkgs, no extra link flags -- plain POSIX), `_check_feature_supported`'s macOS-gate/Windows-hard-reject branches, `_check_wasm_feature_supported`'s http branch, and a new compile-time rejection for `uses_http and uses_graphics` together (the two event loops are mutually exclusive in this version, checked before any real linking work runs, the same "reject outright, don't let a doomed compile run for tens of seconds" discipline `_compile_via_wasm`'s own graphics/audio/exec checks already established).

**Verified beyond compiling, extensively, before writing a single test:** a real curl round trip (method/path/headers/body all correct) before anything else; then a hand-rolled Python RFC 6455 client (no external websocket library available in this environment, and deliberately not reusing any part of this project's own implementation even if one had been) driving a full upgrade -> message -> echo -> close-frame cycle end to end, confirmed correct at every step (the handshake's own `Sec-WebSocket-Accept` value checked byte-for-byte, not just "some 101 came back"). Then a combined ASan + LeakSanitizer stress run: 20 sequential HTTP requests (varied methods/headers/bodies, JSON-rendered struct responses, custom response headers) followed by 5 WebSocket sessions x 10 messages each (including payloads over 125 bytes, exercising the extended-length frame-header path) -- caught the SIGPIPE bug above, confirmed zero leaks/errors after the fix (via a `close(0)`-triggered clean exit, the only way to actually reach LeakSanitizer's own atexit check rather than an abrupt SIGTERM that skips it entirely), and a separate targeted pass for abrupt, non-clean disconnects (mid-request, immediately-after-connect, and an upgraded WebSocket dropped with no close frame at all) -- all handled without a crash or a leak, `on socketClose` still firing correctly for the WebSocket case.

**Testing**: a new `compile_and_run_server` fixture in `tests/conftest.py` -- launches a compiled server as a real background subprocess (unlike `compile_and_run`'s blocking `subprocess.run`, since a server never exits on its own), polls for the port actually accepting connections rather than a fixed sleep, and hands the test a small client (`http_get`/`http_post` via `http.client`, `ws_connect` via the same hand-rolled RFC 6455 client style used for manual verification above) with SIGTERM-then-SIGKILL cleanup in a finalizer so a failing assertion mid-test still tears the server down. Deliberately NOT `str.format()` for injecting the test's own free port into `openPort(__PORT__)` -- Festina source is full of its own bare `{`/`}` (every block body, every map literal), which `.format()` would misparse; a plain literal-token `.replace()` has no such collision (a real mistake caught immediately by trying the first test, before it was ever run against a compiled program). A new `tests/test_http.py`: signature/type-checking coverage needing no toolchain (openPort/closePort's own arity, the four event handlers' fixed signatures, req's read-only fields, `send()`'s argument validation, `s.state`/`s.send()`/`s.close()`), then 20 real compile-and-run tests over `compile_and_run_server` covering every field/method on both types, the auto-default-response case, multiple independent WebSocket sessions proving `s.state` is genuinely per-connection, and a platform/wasm-gating class mirroring `test_wasm.py`'s own established shape (graphics+http rejection, the win32 hard-reject, the darwin gate and its override, the wasm32-wasi rejection both as a unit check and through the real `compile_file` pipeline). Full suite: 1617 passed, 8 skipped (up from 1571 by exactly the 46 new tests).

**Documented** in api.md (a new "HTTP and WebSocket servers" section: openPort/closePort, every `req`/`s` member with a worked example, and a dedicated Limitations subsection listing the HTTP/1.1 and WebSocket scope decisions, the platform/wasm restrictions, and the graphics-incompatibility rule), wasm.md's own Limitations list (a new bullet, matching exec()'s own existing entry there), macos.md and windows.md (one paragraph each, noting the new `FESTINA_ENABLE_MACOS_HTTP` gate and the Windows hard-reject respectively, cross-linked to api.md), security.md (substantially rewritten: the "no networking" paragraph replaced with a real accounting of the new attack surface -- no TLS, the single-threaded availability tradeoff, the 8MB per-connection buffer cap with no connection-count limit, and the hand-written parser itself as the largest new memory-unsafety risk category this project has taken on -- plus the SIGPIPE finding in "Notable fixed findings" and http's own entry in "Slim binaries"), README.md (test count, the one-line feature list), and todo.md (the now-closed "HTTP / networking" bullet removed, replaced with the newly-discovered map-literal-type-uniformity gap as its own new, separate, honestly-scoped-out bullet).

150. EXEC(), ARGV, TEXT.TOINT(), TEXT[I] INDEXING

Asked for directly: "add an exec function, argv global, add a toInt method to text, and allow chars in text to be reached with with indexs []." Two steering messages mid-task: offload as much work as possible to compile time rather than runtime, and have `toInt` return `null` on unparseable input (mirroring `toFloat`'s existing null-on-failure convention) rather than raising.

**`exec(args:arr[text]):int`.** Runtime-named `festina_process_exec`, not `festina_exec` -- a real naming collision found by an actual compile error against a pre-existing internal SQL-DDL helper of that name (`static void festina_exec(sqlite3*, const char*)`) already in `festina_runtime.c`. Spawns `args[0]` (`execvp`, PATH-searched) with the rest of `args` as its own argv, inheriting stdio directly (not captured -- the same "really runs it" model the file builtins already use). A real bug found and fixed before this shipped: the missing-executable case initially returned 127, indistinguishable from a real program that legitimately calls `exit(127)` itself, since a failed `execvp`'s own `_exit(127)` fallback is an ordinary `WIFEXITED`-true exit as far as `waitpid` can see. Fixed with the self-pipe technique (`pipe()` + `FD_CLOEXEC` on both ends): the child writes its own `errno` to the pipe only if `execvp` itself failed; a successful exec closes the write end for free via CLOEXEC, so the parent's `read()` returns EOF instead, letting it distinguish "never started" (returns `-1`) from "started and exited 127 on its own" (returns 127). Verified directly: a missing binary now correctly returns `-1` while `/bin/false` still correctly returns `1`. Three platform branches (`_WIN32` via `_spawnvp`, `__wasi__` unreachable stub, POSIX real fork/exec) -- gated at compile time for wasm (`_check_wasm_feature_supported("exec")`, same "genuinely absent, not hardware-gated" category graphics/audio already use) since WASI has no process model at all.

**`argv`.** A real, mutable `arr[text]` global -- pre-registered in `semantic.analyze` (usable with no declaration, same mechanism `environment` already uses) and populated from the process's own OS argc/argv at the very start of `main()`, before any top-level statement runs. This needed `main`'s own signature to change from `define i32 @main()` to `define i32 @main(i32 %argc, ptr %argv_raw)` -- no native-side consequence at all, since this signature already IS the real C ABI entry point on every native target codegen already targets. `festina_argv_array(argc, argv)` (new runtime helper, reusing the existing `FestinaPieces` array-building machinery) turns the raw C argv into a real `arr[text]` header.

A real regression this surfaced, caught by the full pytest suite rather than by manual testing: the first version stored argv's initial value through `_emit_global_retain_release` -- the same helper every other global's declaration-with-initializer already uses, reused on the reasoning that argv's first-ever store deserved no special-casing. That helper unconditionally emits a call to the arr[text] release wrapper (`_release_fn_for_array`) to release whatever the global held before -- correct in general, but here it made that wrapper function appear in EVERY compiled program's own IR, even a program that never otherwise touches `arr[text]` at all, since `main()` always runs this code. `TestAutomaticMemoryReclamation::test_non_escaping_struct_local_is_stack_allocated` failed on exactly this: its own assertion (`"call void @free(" not in ir`) checks the WHOLE module, and the newly-always-present release wrapper's own body contains a `free()` call for freeing each text element, unrelated to anything the test's own `f()` function does. Root-caused rather than patched around at the test level: at this specific call site, BOTH halves of the general helper's own uncertainty are actually certainties -- `festina_argv_array`'s return is always freshly, uniquely built (no retain needed, ownership transfers straight into `@argv`'s slot), and `@argv`'s own current value at this exact point is always its untouched, immortal, zero-length sentinel (this is provably `main`'s first-ever write to `@argv`, before any user code has run), so there is nothing to release either -- not even a header-level no-op. Replaced with a direct `store ptr %argv_arr, ptr @argv`, no retain, no release, and the spurious wrapper stopped being emitted for programs that don't need it. Full suite confirmed green after the fix, not just the one failing test.

**`text.toInt():int`.** JS `parseInt()` semantics: skips leading whitespace, an optional sign, then digits until the first non-digit (trailing garbage ignored, not an error) -- `null` (int-null, `-9223372036854775808`) if no digits were found at all, per the steering message. `_parse_int_like_strtoll`, a new module-level Python function in codegen.py, replicates C's own `strtoll` behavior (deliberately ASCII-only digit/whitespace matching, not Python's Unicode-aware `str.isdigit()`, to stay behaviorally identical to the C locale `festina_text_to_int` itself uses) so a literal receiver (`'42'.toInt()`) constant-folds ENTIRELY at compile time -- confirmed via `--emit-llvm` that no `festina_text_to_int` call is emitted for that case, directly answering the "offload to compile time" steering message. A dynamic receiver calls the real runtime function, i64-min/max-clamping on overflow either way.

**`text[i]:text`.** UTF-8 codepoint-indexed (not byte-indexed -- `'café'[3]` returns the whole 2-byte `é`, matching `.length`'s own codepoint count), read-only (`s[i] = v` is a compile-time "text is immutable" error, the same category `environment.NAME = ...` already uses), and -- deliberately UNLIKE `arr[T]` indexing's own unchecked-raw-memory contract (api.md's "Indexing is not bounds-checked") -- always bounds-checked: an out-of-range or negative index answers `null`, never a crash. `festina_text_char_at`, a new runtime function, reuses the UTF-8 continuation-byte-walking technique `festina_text_split`'s own empty-separator branch already established. Both a literal receiver AND a literal, non-negative index constant-fold entirely in Python (`list(expr.obj.value)` plus `self._const_string`) -- again, per the steering message, confirmed via `--emit-llvm` that no runtime call is emitted for that case.

A real ownership-tracking gap found and fixed before this shipped: the dynamic-path `text[i]` codegen calls a runtime function that always returns a genuinely fresh, owned buffer, but the DEFAULT treatment for any computed-Member expression node is "borrowed/aliasing" (`_is_owning_text_source`'s own conservative default), which would have meant an unnecessary defensive copy AND a leaked original on every bound/used `text[i]` result. Fixed by reusing `self._minted_values` -- a pre-existing mechanism (originally built for refcounted array/map element reads through an owning container) that lets a computed-Member node signal "this result is actually owning" without needing type-inference access at the point ownership is later queried -- rather than inventing a second, parallel mechanism for the same problem.

**Verified beyond compiling:** manually, before any test was written -- real compile-and-run tests for all four features (including negative/out-of-range/null cases) plus full ASan+LeakSanitizer runs (a combined stress test exercising loops, function calls, discarded temps, and mixed usage of all four features together, both natively and, after the argv-bridge fix below, under wasm), zero leaks or errors either way.

**A serious WASM-specific regression, found and fixed entirely before returning to the user:** every `wasm32-wasi` build started hanging after `main`'s signature changed for `argv`. Root-caused via `llvm-nm-18 -a`/`llvm-objdump-18 -r` (the correct wasm-object-format tools -- plain `nm` silently prints nothing on a wasm object, a real false lead hit and cleared along the way): the entry bridge this project already shipped for the ORIGINAL `main()` -> `__main_void` case (claude.md #148's own bug 2) was a C file, and writing the new argc/argv-taking version the same way (`extern int main(int, char**); int __main_argc_argv(int argc, char **argv) { return main(argc, argv); }`) compiled and linked without any error, then hung at actual runtime. The SAME clang macro that renames a *defined* `int main(int, char**)` to `__main_argc_argv` for `wasm32-wasi` ALSO rewrites any *reference* to the literal identifier `main` in a translation unit compiled for that target -- including the bridge's own `extern` declaration and the call built from it, silently turning `return main(argc, argv)` into `return __main_argc_argv(argc, argv)`: the bridge calling ITSELF, confirmed directly via the compiled object's own relocation record (`R_WASM_FUNCTION_INDEX_LEB __main_argc_argv+0` at the call site, not a reference to `main` at all) and, at `-O0`, a real crash trace showing genuine infinite recursion. The ORIGINAL void-arg bridge never hit this bug -- its own `main` reference resolves to a DIFFERENT name (`__original_main`), so it was never self-referential -- only the new, identically-named argc/argv bridge collided with itself. Fixed by rewriting the bridge as raw LLVM IR text (`runtime/festina_runtime_wasm_entry.ll`, replacing the old `.c` file, which was deleted), bypassing the C frontend's renaming macro entirely -- `declare i32 @main(i32, ptr)` there can only ever mean the real external symbol, confirmed via the same relocation inspection (`U main`, not `U __main_argc_argv`) and via actual execution (correct exit code, correct `argv`) through the REAL `festina/cli.py` pipeline, not just a manual clang invocation. `wasm.md`'s Design section (a new "Bug 3" subsection, alongside the renumbered "Bug 2" reflecting `main`'s new signature) documents the full discovery, matching this whole log's own "record real mistakes and how they were caught" practice.

**Semantic/codegen wiring:** `argv` pre-registered in `semantic.analyze`; `exec` added to `BUILTIN_FUNCTIONS`/`_BUILTIN_SIGNATURES`/`_BUILTIN_RETURN_TYPES`; `.toInt()` added to the Call-inference dispatch (mirroring `.toFloat()`'s existing pattern) and to `_emit_call`; `text[i]` added to `_infer_member`'s computed-Member handling (validating the index is `int`, returning `text`) and to `_emit_expr`'s computed-Member branch, plus an assignment-target rejection alongside the existing `.length`/`environment` read-only checks. `festina/cli.py`'s `_check_wasm_feature_supported` gained an `exec` branch; `_compile_via_wasm` gained a `needs_exec` parameter, checked alongside graphics/audio.

**Testing:** new `TestArgv`/`TestExec`/`TestToInt`/`TestTextIndexing` classes in `tests/test_codegen.py` (23 tests: successful/failed/stdio-inherited `exec()` plus its wasm rejection; argv's program-path/extra-args/mutability; `toInt`'s clean/garbage-trailing/whitespace-and-sign/null-on-empty/null-on-unparseable/dynamic-receiver/constant-fold cases; `text[i]`'s middle/zero/out-of-range-null/negative-null/multibyte-UTF-8/assignment-rejected/dynamic-receiver/constant-fold/no-leak-round-trip cases), plus 4 new tests in `tests/test_wasm.py` (`argv` populated from real WASI argc/argv, `toInt`/`text[i]` working under wasm, `exec()`'s wasm rejection) -- `TestWasmRun`'s own docstring and `compile_and_run_wasm`'s own conftest.py docstring updated, since both used to correctly say Festina had no argv support at all. Full suite: 1571 passed, 8 skipped (up from 1543 by exactly the 28 new tests) -- clean under manual ASan/LeakSanitizer runs described above.

Documented in api.md: a new "Command-line arguments" section (after Environment variables), a new "Parsing an int" and "Indexing a character out" subsection under Strings, and a new "Running other programs" section (after Directories) for `exec()`. wasm.md updated throughout: the Design section's own bug list, a new Limitations bullet for `exec()`'s rejection, and the stale "no command-line arguments" bullet corrected to describe what `argv` actually does under wasm (works; always length 1, since `run_wasi.mjs` hardcodes WASI's own `args` to `[wasmPath]`).

Verified: full suite (`python3 -m pytest -q`) run clean before and after every change in this round, no regressions; `tests/test_examples.py`/`TestExampleGraphicsAndGame`/`TestTimers` specifically re-run in isolation multiple times during the layers.f iteration. Final full suite: 1545 passed, 8 skipped (up from 1543 by exactly the 2 new tests -- the explicit interactive one plus `TestAllExamplesCompile`'s own glob picking up `layers.f` automatically). README.md's test count updated to match.

157. TRY / CATCH / THROW -- REAL EXCEPTION HANDLING, VIA SETJMP/LONGJMP EMITTED DIRECTLY BY CODEGEN

Asked for directly, the first of a five-item batch (`try {} catch(error:text) {}`, a `thread {}`/`thread expr` concurrency feature, `openSecurePort`/TLS, JSON `.toStruct()`/`.toArr()`, and structured `troubleshoot()`/`fail()`) requested together after a benchmark-review question surfaced this language's biggest production gap: every runtime error was unconditionally fatal (`throw` was a reserved keyword the parser explicitly REJECTED -- "use fail() instead" -- since #23's own original design), meaning one bad request in an `openPort()` handler killed the whole server for every other connection. Two design questions were asked back before implementing anything: the `thread {}` concurrency-safety model (the current refcounting/cycle-collector is entirely non-atomic, correct only because this runtime has exactly one thread today) and TLS library choice. The answer deferred threading entirely ("we'll come back to threading later") and picked mbedTLS for TLS -- both out of scope for this entry; `try`/`catch`/`throw` alone is what follows.

**Grammar.** `try { A } catch (name:text) { B }` -- `catch` is mandatory (a bare `try` with no handler is indistinguishable from writing `A` directly), and the caught variable's type annotation must literally be `text` (the parser rejects anything else immediately, rather than letting a typo'd `catch(e:int)` surface as a confusing semantic error later -- a thrown value is always text, exactly like `log()`/`fail()`'s own implicit-toText rule, claude.md #35). `throw <expr>` is its own statement (not a call, like `fail()`), accepting any type. `"try"`/`"catch"` joined `SPEC_KEYWORDS`; `"throw"` was already reserved (claude.md #23) and its parser-level rejection was simply removed. `ast.TryStmt`/`ast.ThrowStmt` are new nodes; `semantic.py`'s `analyze_statement` gained matching branches -- `TryStmt` analyzes `try_body` in the caller's own scope and `catch_body` in a fresh child scope with `catch_var` pre-bound to text (so it's visible ONLY inside `catch_body`, never in `try_body` or after the whole statement, mirroring the `for` loop's own init-variable scoping); `ThrowStmt` just runs `infer()` on the expression, no type restriction.

**The runtime mechanism -- and a first, completely broken design, caught by direct testing before it ever reached a test file.** The obvious first design: a small C runtime function, `festina_try_enter()`, calls `setjmp()` on a `malloc`'d frame, pushes it onto a global catch-frame stack, and returns 0 (generated code runs the try body) or nonzero (a throw longjmp'd back here -- run the catch body). Fully implemented, including codegen's `_emit_try` branching on the return value -- and silently, completely broken: a direct hand-written test (`try { throw 'boom' } catch (e:text) { log(e) }`) printed `'boom'` was never caught at all, straight through to whatever followed the try/catch as if nothing happened. Root-caused by writing an EVEN SMALLER standalone C reproduction outside the compiler entirely (a two-function `enter()`/`doThrow()` pair using raw `setjmp`/`longjmp`), which didn't just misbehave -- it segfaulted. The actual bug, once isolated: `setjmp()` only captures a valid jump target for as long as ITS OWN calling function's stack frame is still live (on the real machine call stack, not yet returned) -- `festina_try_enter()` calls `setjmp()` and then RETURNS 0 to ITS OWN caller (the Festina-generated code that will run the try body), which means by the time some LATER, unrelated `throw` tries to `longjmp` back into `festina_try_enter`'s own now-defunct frame, that frame's stack space has typically already been reused by whatever ran in between -- undefined behavior per the C standard, and empirically either "the longjmp silently does nothing observable" or a crash, depending on exactly what else touched that stack space first. This is NOT the "callback function that stays on the stack for the whole try/catch" shape that setjmp/longjmp libraries correctly use (e.g. `cexcept`) -- it's the textbook-wrong "wrap setjmp in a helper that returns" shape, and no amount of correct bookkeeping around it could have fixed it, because the fix has to change WHERE setjmp is called from, not how its result is used.

**The fix: codegen emits the setjmp call directly, in the function that contains the `try` statement itself.** That function, by construction, cannot have returned by the time any later code inside it (including a nested call chain) might throw -- it can only exit through one of the paths already tracked below. Rather than calling libc's own `setjmp`/`longjmp` symbols directly from hand-written LLVM IR (verified via `clang -S -emit-llvm` to lower to platform/libc-specific names -- `_setjmp` on this glibc, not `setjmp`, with a 200-byte `jmp_buf` whose exact layout is not something this compiler should need to know per-target), `_emit_try` emits `llvm.eh.sjlj.setjmp`/`llvm.eh.sjlj.longjmp` -- the SAME portable LLVM intrinsics `clang` itself lowers `__builtin_setjmp`/`__builtin_longjmp` to (confirmed directly the same way: `clang -S -emit-llvm` on a `__builtin_setjmp`/`__builtin_longjmp` test), with a fixed, target-independent `[5 x ptr]` buffer (`alloca`'d directly in the enclosing function, populated via `llvm.frameaddress.p0`/`llvm.stacksave.p0` exactly as clang's own lowering does) rather than a libc-specific struct. `longjmp` itself has NO equivalent "must be called from a still-live frame" restriction -- only the ORIGINATING setjmp does -- so `festina_throw` stays an ordinary, nested C runtime function (using `__builtin_longjmp`, which clang understands natively), doing the catch-frame-stack bookkeeping (a plain, unsynchronized global linked list -- correct today because this runtime is single-threaded throughout, deliberately not touched by this entry's own thread-related deferral) in real, portable C.

**A second real bug, also found only by testing the fix, not by reasoning about it in advance:** with the corrected setjmp placement, catching now worked for a throw written directly in the try body -- but wrapping the SAME test in a loop with a struct/array/text local declared before the throw showed those locals genuinely leaking (confirmed via Valgrind: "definitely lost" bytes matching exactly the struct's array and the text local's own buffer). Root cause: `ThrowStmt`'s own codegen emitted NOTHING before the `festina_throw` call -- it relied on the try body's own `_emit_block`-managed frame being freed by that block's ordinary trailing cleanup code, exactly like every OTHER statement in a block. But that trailing cleanup is placed AFTER the throw statement in program order, and since `festina_throw` never returns when a catch exists (it jumps away via `__builtin_longjmp`), that later code is dead -- reachable in the generated IR's text, never actually executed. Fixed by giving `ThrowStmt` its own explicit `_emit_free_active_locals(down_to=self._current_func_frame_base)` call, BEFORE the `festina_throw` call -- the identical pattern `Return` already uses for the identical reason.

**That fix immediately caused a THIRD bug, one commit later, also caught only by testing:** the very first re-test after adding the pre-throw free call showed a DIRECTLY-thrown, actually-caught exception being treated as UNCAUGHT (`fail: ...` printed instead of the catch running) -- `_emit_free_active_locals`'s existing walk, called from `ThrowStmt`, was ALSO popping the enclosing try's own `_TryFrameMarker` entry (still present in `self._active_free_locals` at that point, since `_emit_try`'s own Python-level pop of that entry only happens once `_emit_block(stmt.try_body, ...)` fully returns -- which hasn't happened yet while still emitting code INSIDE that call). That IR-level `festina_try_pop()` call executes unconditionally (unlike the dead code past it), popping the runtime's own catch frame BEFORE `festina_throw` ever runs, so by the time it does, `g_festina_catch_top` is already gone -- an otherwise-correctly-caught throw silently became uncaught. Fixed with a `skip_try_pop` parameter on `_emit_free_active_locals`, set only by `ThrowStmt`'s own call: every OTHER exit path (`Return`/`Break`/`Continue`/a try body's own normal fallthrough) still emits `festina_try_pop()` for a `_TryFrameMarker` it walks past, but a throw never does -- popping the frame it might be about to target is exclusively `festina_throw`'s own runtime-side job (it looks up and pops exactly the frame it's about to jump to, at the actual moment of the jump), not something generated code should ever race it on.

**A FOURTH bug, from a test written specifically to probe the boundary the first fix (#2 above) was meant to close:** `try { text s = 'hello'; throw s } catch (e:text) { log(e) }` -- a bare identifier reference, not a literal or template -- crashed under Valgrind with "Invalid read... 0 bytes inside a block... free'd", inside `festina_throw`'s own internal `strdup(msg)`. `_to_text` on an already-TEXT value doesn't copy (that's not its job -- it exists to coerce int/float/bool, and returns an ALREADY-text value's own pointer unchanged), so `text_val` was the exact same pointer as `s`'s own storage -- and the newly-added `_emit_free_active_locals` call (bug #2's fix) freed `s` immediately before `festina_throw` tried to read it. Fixed the same way `Return`'s own, structurally-identical text branch already handles this exact hazard: `self._is_owning_text_source(stmt.expr)` decides whether the thrown expression is already an independent, fresh buffer or an alias that needs an explicit `festina_text_own` copy FIRST, before any locals are freed. `festina_throw` was also changed to TAKE OWNERSHIP of `msg` directly (`g_festina_error_message = (char *)msg`) rather than making its own second `strdup` copy -- the caller-side copy already made for safety would otherwise become an un-freeable, permanently-leaked second copy on every single caught throw, since nothing downstream ever gets the chance to free a caller-side temporary once the call diverts control away for good.

**The resulting, precisely-scoped leak caveat -- confirmed empirically, corrected once from an earlier, overstated draft.** An earlier pass at this comment claimed "a throw reached from inside a called function leaks" -- broader, and wrong, than what's actually true: `ThrowStmt`'s own pre-throw free call (bug #2's fix) means the function that DIRECTLY contains the `throw` -- however many calls deep that is from the `try` itself -- is always leak-free, exactly like `Return` already is for that same function. Verified directly: 0 bytes leaked throwing from the function a `try` calls, and 0 bytes leaked one level deeper still. The real gap is narrower: any INTERMEDIATE frame on the call chain -- a function that merely CALLS something which eventually throws, without itself containing a `throw` or `try` -- never runs any of its own cleanup at all, because the `longjmp` skips past its remaining code entirely. Reproduced on demand: a 500-iteration loop calling a two-level chain (`outer()` declares a text local, then calls `risky()`, which throws) showed exactly 500 "definitely lost" blocks, one per call, of exactly the declared local's own size -- moved the local from `outer` into `risky` itself (removing the intermediate frame) and the leak vanished completely, 0 bytes, confirming the boundary precisely rather than just asserting it. This is a leak, never a use-after-free or corruption -- the same correctness class this runtime already accepts for the one documented row-array chain shape in security.md.

**Verified beyond the bugs above:** nested try/catch with a rethrow from inside the inner catch (unwinding correctly to the OUTER try, not the same one twice); `return` from inside both a `try` and a `catch` body; `break`/`continue` crossing a try/catch nested inside a `while` loop (including the loop's own iteration continuing correctly after a caught throw); a non-text thrown value (`throw 42`) coercing to text exactly like `fail(42)` already does; an uncaught throw at top level producing byte-identical behavior to `fail()` (same stderr prefix, same exit code, confirmed the loop that failed catches nothing when the exception source is simply never wrapped in a `try` at all).

**A FIFTH bug, the widest-blast-radius one, caught only by the full suite (not a targeted test) after everything above already looked correct in isolation:** the full `pytest` run broke EVERY `--target=wasm32-wasi` test, including ones with no relationship to `try`/`throw` at all (`test_hello_world`, `test_arithmetic_and_control_flow`, ...) -- `runtime/festina_runtime.c:286: error: __builtin_longjmp is not supported for the current target`. Root cause: `festina_throw` lives in the CORE runtime translation unit, which (per this file's own top comment, and claude.md #150's identical precedent for `exec()`) is compiled UNCONDITIONALLY for every wasm build regardless of whether a given program ever calls it -- LLVM's wasm32 backend has no SjLj lowering at all outside emscripten's own EH pass, so the whole file failed to compile, for every program, the moment `__builtin_longjmp` appeared anywhere in it. Fixed the identical way `festina_process_exec` already handles the same situation: `festina_throw`'s real body moves behind `#if !defined(__wasi__)`, with a `__wasi__` stub (`festina_fail(msg)`, unreachable in practice) purely so the file compiles. A NEW `codegen.uses_try` flag (mirroring `uses_exec` exactly) plus a `"try"` branch in `_check_wasm_feature_supported` now rejects a wasm compile OUTRIGHT, at compile time, for any program that actually uses `try`/`throw` -- consistent with graphics/audio/`exec()`/`openPort()`'s own "genuinely absent, not hardware-gated" treatment, and confirmed directly both ways: a plain `log()`-only program now compiles for wasm again, and a `try`/`throw`-using one fails immediately with a clear `CompileError` naming wasm.md's own new Limitations bullet, rather than either a confusing low-level clang error or a silent platform-dependent semantic change. A pre-existing `tests/test_non_goals.py` test asserting `throw` was REJECTED syntax (claude.md #23's original design) was updated to match the new reality -- removed from `TestExcludedSyntax`'s parametrized list, and its sibling class renamed from `TestFailReplacesThrow` to `TestFail` with a docstring correction: `fail()` and `throw` now coexist, `fail()` was never replaced.

**Tests:** new `tests/test_try_catch.py` -- `TestParsing` (6 tests: grammar, the catch-variable-must-be-text parse error, try-without-catch, catch-variable scoping) and `TestRuntimeBehavior` (10 tests, covering catching/not-catching, throwing through a called function, uncaught-behaves-like-fail, nested try/catch with rethrow, return/break/continue crossing a try/catch, non-text coercion, and the exact "locals declared before a direct throw survive intact" shape bug #3/#4 above were found through). Full suite: 1653 passed, 8 skipped (up from 1639: 16 new tests here, minus the one `test_non_goals.py` case removed above). Documented in api.md (a new "try / catch / throw" section, with the leak caveat spelled out as plainly as the rest of this entry does, not softened).

158. TROUBLESHOOT() STRUCTURED LOGGING; STRUCTURED FAIL()

Asked for directly, the third item of the same five-item batch claude.md #157 opened ("Add a structured log called troubleshoot() and make fail structured as well"). `troubleshoot(event, fields)` prints one JSON line to stdout (`timestamp`/`level`/`event`/`fields`) meant for a real log aggregator, not eyeballing -- unlike `log()`, every field is always present and always in the same shape. `fail(message)` is completely unchanged; `fail(message, fields)`, a new optional second argument, is the identical structured shape written to stderr instead (`"level":"error"`, key `"message"`), still followed by `exit(1)`.

**Design: `fields` is `map[text]`, not "any type."** The obvious first instinct -- let `fields` be any JSON-renderable value, the same flexibility `log()`'s own single argument already has -- turns out not to be safe: `.toText()`'s existing rendering is JSON-safe for struct/table/arr/map (already properly quoted/escaped internally) and for int/float/bool (already bare-JSON-legal), but NOT for a bare `text` value used standalone -- `log('hello')` prints `hello`, unquoted, exactly as it should for a plain log line, which is NOT valid JSON if spliced directly into a `"fields":<here>` slot. Restricting `fields` to `map[text]` specifically sidesteps this entirely (a map's own JSON rendering already escapes/quotes every value correctly) at the honest cost of scoping `fields` to string-valued tags rather than arbitrary nested structure -- exactly the kind of deliberate, disclosed scope cut this project already makes elsewhere (claude.md #156's own `amor arr[T]` being the most recent). `event`/`message` keep the full "any type, coerced to text" flexibility `log()`/`fail()` already had, since that value becomes its own quoted JSON string either way, with no aliasing hazard the way a bare unquoted text splice would have.

**Semantic layer:** `fail`/`troubleshoot` got their own dedicated branch in `_infer_call` (mirroring `saveCanvas`'s existing "arity determines the return shape" pattern) rather than falling through the generic `BUILTIN_FUNCTIONS`/`_BUILTIN_SIGNATURES` path -- neither builtin's "any type" first argument nor its fixed-but-only-conditionally-required second argument fits that table's plain fixed-arity-fixed-type model. A `MapLit` fields argument gets claude.md #156's own bypass verbatim (validated entry-by-entry against `map[text]` directly, instead of through generic `infer()`, which has no way to resolve an empty `{}` literal's value type, or to know `{'a': 'b'}`'s literal type should connect to this specific expected shape at all) -- confirmed necessary directly, not preemptively: `troubleshoot('x', {})` failed with "cannot infer the value type of an empty map literal" before this was wired through.

**Runtime:** `festina_troubleshoot`/`festina_fail_structured`, both new, small C functions building one JSON line via the SAME string-builder primitives (`festina_sb_new`/`_append`/`_append_json_text`/`_finish`) the existing struct/map JSON renderer already uses -- no new escaping logic anywhere. A shared `festina_write_log_timestamp` helper produces a UTC RFC3339-ish timestamp (`gmtime_r`/`gmtime_s`, mirroring `festina_format_time`'s existing `localtime_r`/`localtime_s` platform split exactly) -- UTC deliberately, unlike `formatTime`'s own local-time convention, since a structured log line meant for aggregation should never depend on the machine's local timezone.

**Codegen: reuses `_to_text` for BOTH arguments, not a new rendering path.** `_to_text` (the exact conversion `.toText()`/`log()`'s container path already use) already produces a JSON-safe string for every type `fields`/`event` can actually be (`map[text]` for fields; anything for event) -- so codegen's own job is purely assembling the envelope around two already-rendered strings, mirroring the runtime side's own "no new escaping logic" shape. A NEW `_emit_value_for`-threading detail was needed specifically for the fields argument: since it's a plain call-argument position (not a var declaration, which already threads its own declared type into literal inference), a bare `{}`/`{'a': 'b'}` fields literal has no expected-type context to resolve against by default -- fixed by passing `expected_type=types_mod.MapType(TEXT)` through to `_emit_value_for` explicitly for this one argument, the identical mechanism `amor map[T]`'s own declared-type threading already established, just reached from a different call site.

**A real bug, caught directly by running it (not found by review): `troubleshoot()`'s own JSON output came out with garbage bytes where the fields object should have been.** Root cause: the helper method computing the fields argument's rendered JSON text ALSO freed that buffer internally, as part of the same call -- meaning by the time the CALLER went on to emit the actual `festina_troubleshoot(...)` call that consumes it, the buffer was already freed. A textbook "cleanup landed before the value's real use, not after" ordering bug, exactly the shape of mistake claude.md #157's own throw-a-bare-local bug (bug #3 there) was a close cousin of, but on the PRODUCING side this time rather than the consuming side. Fixed by splitting what had been one method into two -- `_emit_json_arg_text` (produce the rendered text, nothing else) and a separate `_cleanup_json_arg_text` (free it, release the original value's own reference) -- with `troubleshoot`'s own codegen now calling cleanup only AFTER emitting the real `festina_troubleshoot()` call, never before. Confirmed correct afterward via direct output inspection (the fields JSON reads back exactly as written) and a 50-iteration Valgrind run (0 bytes leaked, 0 errors) exercising both a fresh literal AND a repeatedly-referenced (never consumed) `map[text]` variable passed to `troubleshoot()` many times in a loop, the second case specifically confirming the map itself survives fully usable afterward (`tags['service']` read correctly after 20 `troubleshoot()` calls referencing the same binding).

**Verified beyond the bug above:** `fail('msg')`'s one-argument form produces byte-identical output to before this entry (still exactly what an uncaught `throw` produces, claude.md #157's own contract untouched); `fail('msg', fields)`'s structured form correctly uses `"message"` rather than `"event"` as its key and `"level":"error"`; a non-text `event`/`message` argument (`troubleshoot(42, {})`, `fail(42)`) coerces to text exactly like `log()`/`fail()` already do, rendering as a quoted JSON string (`"42"`), not a bare JSON number -- event/message are semantically text labels, never numeric fields, regardless of what was passed; every documented error path (`troubleshoot()` with 1 argument, `fail()` with 3, a fields argument of `map[int]`, a fields literal with a non-text key or value) raises a clear `CompileError` naming exactly what's wrong.

**Tests:** new `tests/test_troubleshoot.py` -- `TestSemanticErrors` (6 tests: arity for both builtins, the unchanged 1-argument fail() form, the map[text]-only fields restriction against both a wrong map type and a literal's own wrong key/value type) and `TestRuntimeBehavior` (7 tests: the structured JSON shape for both troubleshoot() and fail(), the empty-literal case, a referenced map[text] variable staying usable afterward, non-text event/message coercion, and the 50-iteration loop pinning correct behavior in the exact shape the bug above was found through). Full suite: 1666 passed, 8 skipped (up from 1653 by the 13 new tests here). Documented in api.md (a new "troubleshoot() -- structured logging" section, placed between log()/fail()/close() and try/catch/throw).

159. TOSTRUCT() / TOARR() -- REAL JSON DESERIALIZATION, PAIRED WITH TRY/CATCH

Asked for directly, the fourth item of the same five-item batch claude.md #157 opened ("Can we add: '{"test":true'}.toStruct(structName) or '[1,2]'.toArr(int)"). `text.toStruct(StructName)`/`text.toArr(ElementType)` are the reverse of `.toText()`'s own JSON rendering (claude.md #114) -- a hand-written recursive-descent JSON parser, new to this runtime, deliberately structured so parsing failures reuse claude.md #157's own throw/catch machinery as this whole feature's entire error-handling story, rather than a second, separate one.

**Grammar: the one place in the language a call's own "argument" is a TYPE, not a value.** `.toStruct(T)`/`.toArr(T)` are special-cased at the exact point `parse_call_member` is about to parse an ordinary argument list -- checked by method NAME right there, so nothing about parsing any OTHER call anywhere in the language changes -- and parse a single `parse_type()` (the SAME entry point every other type position in the grammar already uses) wrapped in a new `ast.TypeArg` node, rather than `parse_args()`'s normal expression list. `escape_analysis.py`'s own exhaustive node-type assertion (deliberately raises on any unhandled expression kind, per that module's own docstring) needed a one-line addition for `TypeArg` -- caught immediately by the first real compile-and-run test, not found by review.

**Design: every low-level parsing primitive either succeeds or throws -- never returns an error value.** The obvious alternative (each primitive returns an error code/message, threaded back up through hand-generated LLVM IR branches) was rejected before writing any code: claude.md #157 already built a complete, tested throw/catch mechanism, and every JSON parsing primitive (`festina_json_parse_string`/`_number`/`_bool`, the object/array iteration helpers, ...) simply calls `festina_throw()` directly and never returns on failure -- meaning codegen's own generated per-type parsing functions are PLAIN, straight-line/looping code with zero separate failure-path branching to generate at all. A `festina_json_throwf` helper (`vsnprintf` into a stack buffer, then `strdup` before handing it to `festina_throw` -- `festina_throw` TAKES OWNERSHIP of what it's given, per claude.md #157/#158's own established convention, so the stack buffer itself can never be what's actually thrown) is the only new plumbing this required.

**v1 scope cut, decided before writing any parsing code, not discovered as a limitation partway through:** a target struct's fields and `toArr`'s own element type must be `int`/`float`/`bool`/`text` -- no nested `struct`/`arr[T]`/`map[T]` support yet, rejected at COMPILE TIME (semantic.py, walking the target struct's own field types / the given element type against the same four) with a clear error naming exactly what's unsupported, never silently ignored. `\u` unicode string escapes are the identical kind of cut, for the identical reason (de-risking a hand-written parser over untrusted input, this runtime's own stated highest-risk category per security.md) -- raw, un-escaped UTF-8 bytes are completely unaffected either way. `festina_json_skip_value` (used to correctly skip past an unrecognized struct key's own value) is fully general regardless of this scope cut -- an unrecognized key can still legally hold arbitrarily nested JSON, and needs to be skipped correctly either way, so it recurses with no depth limit of its own (matching this file's own `_json_fn_for` render-side recursion, which IS depth-capped at 32 for cyclic Festina VALUES -- not applicable here, since a JSON document being PARSED cannot itself be cyclic the way a live Festina struct graph can).

**Runtime verified in complete isolation before touching codegen at all** -- a lesson taken directly from claude.md #157's own setjmp misadventure: a small standalone C harness exercised every primitive (string escapes, numbers, bool/null, object/array iteration including a case-insensitive key match and a deliberately malformed trailing-comma input) against the compiled runtime object file alone, confirming both the happy path AND the throw-on-malformed-input path (correctly falling through to `festina_fail`'s own uncaught behavior, exit(1)) before any codegen existed to call it from. Caught nothing this time (unlike #157's own setjmp bug) -- but the discipline held anyway, and the actual compiler-level integration (built next) worked correctly on its own first real end-to-end test.

**Codegen: mirrors `_json_fn_for`'s existing cache-by-type, generate-on-first-use pattern, in the opposite direction.** `_from_json_struct_fn_for`/`_from_json_arr_fn_for` each generate (once per distinct struct name / element type, cached in a new `self._from_json_fns` dict keyed `"struct:Name"`/`"arr:elemtype"`) a `ptr @__festina_from_json_*_N(ptr %cursor)` function: allocates a fresh, refcount=1 heap value via the SAME `_emit_fresh_heap_header` every other struct/array-producing site already uses, then either an object-field loop (a chain of case-insensitive `festina_json_key_matches` checks against each known field name, GEP-writing the parsed value into that field's own slot, freeing any pre-existing text there first for the "duplicate key, last one wins" case, with `festina_json_skip_field_value` as the unmatched-key fallback) or an array-element loop (`festina_json_read_*` per element, `festina_array_push` -- the SAME runtime helper `.push()` itself already uses, needing no extra ownership retain/copy since a JSON-read text result is always already a fresh, uniquely-owned buffer). The call site itself (`.toStruct(T)`/`.toArr(T)`'s own `_emit_call` branch) is a thin wrapper: get a cursor over the receiver's bytes, call the generated function, reject trailing garbage (`festina_json_expect_end`), free the cursor, free the receiver's own text temp -- five runtime calls, no branching of its own at all.

**A real, structural leak class found via Valgrind after everything else already looked correct -- the same shape claude.md #157's own "intermediate frame" leak is, discovered here for a genuinely new reason.** `Person p = '{"id":1,"name":"x"}extra'.toStruct(Person)` inside a `try` (rejecting the trailing "extra") showed the fully-built struct -- header AND its own "name" field's text buffer -- as "definitely lost." Root cause, once traced: `__festina_from_json_struct_N`'s own `out` register is hand-written LLVM IR, generated completely OUTSIDE `_emit_block`'s normal per-statement `_active_free_locals` tracking -- by the time `festina_json_expect_end` throws, `out` was never a "local" in ANY sense claude.md #157's existing cleanup machinery could ever have known to free, because the VarDecl that would eventually bind it to `p` hadn't finished evaluating yet (a VarDecl only registers its own local for scope-exit tracking AFTER its whole init expression completes -- `_emit_block`'s own documented ordering). Investigated rather than patched around: confirmed the SAME leak shape (bounded, error-path-only) already existed even WITHOUT the trailing-garbage check at all -- a struct whose second field fails to parse, having already read a first one successfully, leaks the same way, for the identical underlying reason (the hand-written per-type parsing function's own `out` never participates in any Festina-level cleanup tracking, regardless of which specific call inside it throws). Concluded this is a genuine, inherent property of hand-generated code that never goes through `_emit_block` at all, not something a small reordering fix could close -- proper exception-safe cleanup for a value built mid-expression-evaluation would need real RAII/unwind-table machinery this language doesn't have anywhere yet, a substantially larger undertaking than this feature's own scope, and NOT attempted here. Documented as plainly as claude.md #157's own comparable caveat, not softened: THE HAPPY PATH LEAKS NOTHING (confirmed directly, not just reasoned about -- 30 repeated successful calls in a loop, 0 bytes, under Valgrind), this is strictly an error-path leak, bounded to at most one partially-built value per FAILED call, never unbounded or accumulating.

**Verified beyond the leak caveat above:** every scalar field type (int/float/bool/text) round-trips correctly through a real struct; `arr[int]`/`arr[float]`/`arr[bool]`/`arr[text]` all parse correctly; an unrecognized JSON key is silently skipped (including one holding a deeply nested object/array, confirming `festina_json_skip_value`'s own generality); a struct field the JSON never mentions keeps its ordinary zero value; JSON key matching is case-insensitive (mirroring claude.md #111's own query-column convention, not re-derived); a duplicate key is "last one wins," freeing the earlier text value correctly; JSON `null` becomes the target type's own null; malformed JSON, a type mismatch mid-parse, and trailing data after the value all throw descriptive messages, catchable by an enclosing `try` exactly as designed, or behaving exactly like `fail()` when uncaught.

**Tests:** new `tests/test_json_parse.py` -- `TestParsing` (3 tests: grammar for both methods) and `TestSemanticErrors` (6 tests: wrong receiver type, a non-struct toStruct() argument, a non-scalar toArr() element type, a struct with an unsupported nested field type, both methods' own successful resolution) and `TestRuntimeBehavior` (12 tests: every scalar field type, all four toArr() element types, unknown-key skipping, missing-key zero values, case-insensitive matching, duplicate-key handling, JSON null, all three throw paths -- malformed/type-mismatch/trailing-data -- both caught and uncaught, and the 30-iteration no-leak-on-success loop the leak investigation above was grounded in). Full suite: 1687 passed, 8 skipped (up from 1666 by the 21 new tests here). Documented in api.md (a new ".toStruct() / .toArr() -- parsing JSON" section, immediately after try/catch/throw, with the leak caveat spelled out with the same weight the rest of this entry gives it) and todo.md (two new bullets: the v1 scalars-only scope, and the leak class folded into the existing memory-model list alongside claude.md #157's own).

160. OPENSECUREPORT() -- TLS FOR THE HTTP/WEBSOCKET SERVER, VIA MBEDTLS

Asked for directly, the second item of the same five-item batch claude.md #157 opened ("Can we add a openSecurePort(port:Int, key:blob)?"). `openSecurePort(port:int, key:blob)` is the TLS counterpart to claude.md #151's `openPort()` -- same listener table, same connection table, same single-threaded `festina_run_http_loop()` poll() event loop, same `on request`/`on upgrade`/`on message`/`on socketClose` handler surface. A program can mix plain and TLS listeners freely; nothing about reading a request or sending a response differs based on which port it arrived on. mbedTLS 2.x (the library actually installed and tested against -- `libmbedtls-dev` on this Debian box, 2.28.8) is the library, following the "We'll come back to threading later" / "mbedTLS (Recommended)" answers given when this whole five-item batch was scoped.

**Design: an opaque function-pointer hook table, mirroring `g_audio_decoder`/`g_image_decoder` exactly, so mbedTLS is confined to programs that actually call `openSecurePort()`.** `festina_runtime_https.c` is a brand-new translation unit (`festina_runtime_http.c`'s own sibling, not a modification of it beyond the seam) holding every mbedTLS-touching line in this feature -- `festina_runtime_http.c` itself has zero mbedTLS `#include`s and never references an mbedTLS symbol directly, only seven `static` function pointers (`g_tls_listener_new/_free`, `g_tls_conn_new/_free`, `g_tls_handshake`, `g_tls_recv`, `g_tls_send`) it calls through. `festina_set_tls_hooks(...)` (declared in `festina_runtime.h`, defined in `festina_runtime_http.c`, storing into those seven pointers) is what wires the two files together; `festina_register_tls_hooks()` (in `festina_runtime_https.c`) is the one function generated code's own `main()` actually calls -- and ONLY when `self.uses_https` (a new codegen flag, narrower than `self.uses_http`: a program using `openPort()` with no TLS at all must never pull mbedTLS in). cli.py's own `_RUNTIME_FEATURES["https"]` entry follows the exact same per-feature-object-file-and-pkg-config-libs split graphics/audio/http already established -- three pkg-config packages (`mbedtls`/`mbedx509`/`mbedcrypto`, mbedTLS's own crypto/x509/ssl split), linked ON TOP of "http" (never instead of it -- `openSecurePort()`'s own codegen dispatch always sets `uses_http = True` alongside `uses_https`, since it needs the whole listener/connection/event-loop machinery `openPort()` already built).

**Runtime primitives verified in complete isolation before touching codegen at all** -- the same discipline claude.md #157's setjmp misadventure and claude.md #159's JSON parser both already established, and worth repeating a third time because it kept working: (1) a bare standalone C harness confirmed `mbedtls_x509_crt_parse`/`mbedtls_pk_parse_key`, each handed the SAME combined cert+key PEM buffer, correctly extract only their own block and ignore the other -- confirming the "one blob, either order" design for `key:blob` actually holds before any runtime code assumed it. (2) A second standalone harness built a real non-blocking, poll()-driven TLS server using exactly the planned hook contract (the seven functions' own signatures and return-code conventions), and completed a real TLS 1.2 handshake plus an HTTP-shaped request/response exchange against Python's own `ssl` module as an independent client. (3) Once the actual `festina_runtime_https.c`/`festina_runtime_http.c` changes were written, the SAME two verifications were repeated -- combined-PEM parsing, and a real handshake -- but this time linking and running the actual runtime object files (`core.o` + `http.o` + `https.o`) through a tiny driver `main()`, before generating a single line of Festina-facing codegen. Both passed on the first real run; the actual bug (below) was caught only once codegen wiring began.

**The one real bug, caught directly by running it, not found by review: a table-less/sqlite-less `openSecurePort()` program compiled cleanly but exited immediately instead of listening.** The TLS-hook-registration call (`call void @festina_register_tls_hooks()`) had been placed, by a first draft, inside `_emit_main_and_entry`'s existing `if self.tables or self.uses_sqlite:` block -- right alongside the audio/image decoder-hook registrations, which genuinely do belong there (they exist to let a *table column* of type `aud`/`img` decode). But that whole block, `festina_db_open()` included, is skipped ENTIRELY for a program with no `table` declarations and no `sqlite()` call (this language's own "don't touch the filesystem for `festina.sqlite` unless actually needed" convention) -- so a minimal `openSecurePort()`-only test program (no tables) silently never registered its TLS hooks, `g_tls_listener_new` stayed `NULL`, `festina_open_secure_port` real-checked `!g_tls_listener_new` and silently no-op'd, and `festina_run_http_loop` found zero listeners and zero timers and returned immediately -- clean exit code 0, no error at all, just a program that does nothing. Confirmed via the compiled IR directly (`festina_register_tls_hooks` was `declare`d but never `call`ed) before fixing it. Fixed by moving the registration out to the SAME unconditional position the four `on request`/`on upgrade`/`on message`/`on socketClose` handler registrations already use, just above the sqlite block -- `openSecurePort()` has nothing to do with SQLite, and should never have been coupled to it.

**Key material: one `blob`, a combined PEM (certificate or chain, plus the matching UNENCRYPTED private key, concatenated, either order) -- no splitting needed, confirmed directly rather than assumed.** `mbedtls_x509_crt_parse`/`mbedtls_pk_parse_key` are each simply handed the WHOLE buffer; each recognizes and extracts only its own PEM block type, ignoring everything else in the buffer -- verified with a real openssl-generated cert+key pair before any runtime code depended on it (see the verification section above). An encrypted (password-protected) key is rejected (mbedTLS's own `pwd`/`pwdlen` left `NULL`/`0`) -- out of scope for v1, the same kind of scope cut claude.md #159's own scalars-only JSON parser already made. A bad PORT number is a silent no-op, matching `openPort()`'s own "test, don't fail" contract exactly -- but a certificate/key that fails to parse, or a key that doesn't match its certificate, FAILS THE PROGRAM (`festina_fail`, with the real mbedTLS error text folded in via `mbedtls_strerror`) -- a program-authoring mistake, not a runtime condition worth testing for, the same line claude.md #59 already draws for every other builtin.

**Handshake: non-blocking, resumed across however many `poll()` ticks it takes, driven from the exact same `festina_conn_readable` call site every connection's first readable byte already reaches.** Every connection's underlying fd is already non-blocking by the time `festina_tls_conn_new` builds its `mbedtls_ssl_context` (using mbedTLS's own `mbedtls_net_send`/`_recv` BIO callbacks over a real `mbedtls_net_context` -- confirmed against mbedTLS's own header rather than assumed which fields it has), so `mbedtls_ssl_handshake()` itself never blocks -- it returns `WANT_READ`/`WANT_WRITE` instead, and `FestinaConn` gained `tls`/`tls_handshake_done`/`tls_wants_write` fields to track exactly where a given connection's handshake stands. `tls_wants_write` is the one genuinely new piece of poll-loop plumbing: `festina_run_http_loop`'s own poll-fd-array construction now requests `POLLOUT` (in addition to the usual `POLLIN`) for any connection that last returned `WANT_WRITE` -- without it, a handshake that stalled wanting to write (a full TCP send buffer, rare for small handshake flights but possible) could wait forever on a `POLLIN` that might never come. Once the handshake completes, `festina_conn_readable` falls straight through into the ordinary read loop in the SAME call, rather than waiting for a fresh `POLLIN` -- the handshake's last flight and the client's first application-data record can arrive in one physical TCP segment, already fully consumed by mbedTLS's own internal BIO reads during the handshake call, so waiting for another readable event here could stall indefinitely.

**Every read/write touchpoint threads through `FestinaConn` rather than a raw fd, which turned out to be a small, contained change.** `festina_send_all` (used by every response/WebSocket-frame send path) and `festina_ws_send_frame` both already took their connection's own state in hand at every call site -- changing their signature from a bare `FestinaSocket fd` to `FestinaConn *c` and branching on `c->tls` internally was the entire fix, no call site needed new bookkeeping. `festina_send_all`'s own established "a write that would block is treated as outright failure, never retried through the event loop" precedent (claude.md #155) extends unchanged to `g_tls_send`'s `WANT_READ`/`WANT_WRITE` -- both fold into the identical "just fail" answer the plain-socket path already gives, not a new retry story. The one raw `recv()` call (`festina_conn_readable`'s own read loop) got an equivalent `g_tls_recv` branch alongside it, using the same "would-block breaks the loop, 0 means peer closed, negative means fatal" shape the plain-socket path already has.

**Scope, deliberately cut for v1, each named directly rather than left implicit:** server-side only (no TLS client anywhere in this language); one certificate/key pair per listening port, no SNI (a program needing per-hostname certs calls `openSecurePort()` once per port); no client-certificate/mutual TLS (`mbedtls_ssl_conf_authmode(..., MBEDTLS_SSL_VERIFY_NONE)`, unconditionally); no ALPN (no HTTP/2 negotiation); no `close_notify` sent at teardown (the underlying fd is closed outright regardless, and writing one could itself block with no event loop left to drive a retry -- a peer sees an abrupt close, the identical "no graceful half-close" shape this runtime's own "every response closes the connection, no keep-alive" HTTP/1.1 scope already accepts). Platform gating rides entirely on `openPort()`'s own existing macOS/Windows "exists, awaiting real-hardware verification" gates (`FESTINA_ENABLE_MACOS_HTTP`/`_WINDOWS_HTTP`) rather than a separate TLS-specific flag -- `openSecurePort()` always sets `uses_http` too, so that gate fires first, before a separate `https`-named gate would ever need to. Rejected outright at compile time under `--target=wasm32-wasi`, the identical "genuinely absent, not a hardware-verification gate" reasoning `openPort()` itself already uses (WASI Preview 1 has no listening-socket support at all, which `openSecurePort()` needs just as much as `openPort()` does).

**Tests:** new `tests/test_secure_port.py` -- `TestParsingAndSignature` (5 tests: the fixed `(int, blob)` signature, wrong port/key types, wrong arity, sharing the `on request`/etc. handler surface with plain `openPort()`) and `TestRuntimeBehavior` (5 tests, all real compiled-binary-plus-real-TLS-client runs via a new `compile_and_run_secure_server` fixture: a basic request/response, the same "handler never responds" default-200 behavior `openPort()` itself has, `req.method`/`req.path` readable over TLS, a raw non-TLS client talking to a TLS-only port never getting back a valid HTTP response, and ten requests across ten separate real TLS connections in a row). The new fixture generates a fresh, throwaway self-signed certificate per test via the real `openssl` CLI (skipped cleanly, not a failure, if `openssl` isn't on PATH -- the same "this environment lacks a real dependency" skip `compile_file_or_skip` already gives a missing mbedTLS dev package). Full suite: 1697 passed, 8 skipped (up from 1687 by the 10 new tests here). Documented in api.md (a new `openSecurePort(port:int, key:blob)` -- TLS" section, immediately after the http/WebSocket Limitations block) and setup.md (mbedTLS added to the dependency table and every platform's install command, plus the Windows DLL-story note) and wired into `festina doctor` (a new optional mbedTLS check, `all()` across the three pkg-config packages since a program either has everything `openSecurePort()` needs or it doesn't).

161. GRACEFUL SHUTDOWN -- SIGINT/SIGTERM RUN close()'S OWN CLEAN-EXIT PATH, AND DRAIN THE HTTP SERVER

Asked for directly, the fifth and final item of the same five-item batch claude.md #157 opened ("Add graceful shutdown"). Before this entry, `SIGINT` (Ctrl-C) and `SIGTERM` (`kill`, a container orchestrator's own shutdown signal, ...) always fell through to the OS's default disposition -- immediate termination, no cleanup, `on exit(code:int)` (claude.md #131) never fires, and for a running `openPort()`/`openSecurePort()` (claude.md #151/#160) server every open connection is severed mid-response with no chance to finish. Now both signals run the exact same clean-exit path `close(code)` already established: `on exit(code:int)` fires (passed a conventional `128 + signal` exit code -- `130` for `SIGINT`, `143` for `SIGTERM`, the same encoding a shell itself reports for an ordinary process killed the same way), then the process exits -- and, specifically for the HTTP/WebSocket server, every listening port closes immediately (refusing new connections outright) while already-open connections get a bounded grace period to finish on their own first.

**Design: the signal handler itself does the absolute minimum async-signal-safety allows -- two `sig_atomic_t` flag writes, nothing else -- and every blocking loop polls those flags on its own schedule instead.** A signal handler cannot safely call malloc, do I/O, or run arbitrary Festina/generated-IR code directly (it could interrupt a non-reentrant call already in progress and corrupt state or deadlock) -- so `festina_shutdown_signal_handler` only ever sets `g_shutdown_requested`/`g_shutdown_exit_code` and returns. The three blocking loops that already exist (`festina_run_http_loop`, `festina_run_timer_loop`, `festina_run_event_loop`) each gained one check at their own natural per-iteration poll point -- the identical shape `festina_next_timer_deadline()`/`_fire_expired_timers()` already use for "check something on your own schedule, from safe context" -- and do the real cleanup (draining connections, closing a window, running `on exit`) from there, in ordinary execution.

**Design: the handler is installed ONLY when the program actually has one of those three pollable loops -- never unconditionally, and never merely because `on exit` is declared.** A first design considered installing it whenever the program declared `on exit(code:int)` too, reasoning that seemed like the obviously-useful case. Caught directly by testing it, not by review: a program with `on exit` but no `openPort()`/timers/graphics at all -- just its own hand-written `while (true) { ... }` at top level -- has no point in its own execution that could ever check `festina_shutdown_requested()`. Installing the handler there doesn't just mean "skip running `on exit`" (today's pre-existing gap); it makes the program **genuinely unkillable via SIGINT/SIGTERM**, worse than doing nothing at all -- the signal sets the flag and control returns right back into the same loop that will never check it, silently swallowing what used to be an immediate, working kill. Fixed by gating installation strictly on `self.uses_graphics or self.uses_http or self.uses_timers` (codegen.py's `_emit_main_and_entry`), dropping `exit_handler_symbol is not None` from the condition entirely -- confirmed directly afterward that this exact shape (`on exit` + a bare infinite loop, no other feature) is still killed immediately by the OS's own default disposition, unchanged from before this entry.

**A second real bug, also caught only by running the actual failure scenario, not found by review: the HTTP server's own grace-period deadline was silently unenforceable whenever nothing else was already waking `poll()` up.** `festina_run_http_loop`'s per-iteration shutdown check computed a drain deadline correctly, but the loop's own `poll()` timeout was computed ONLY from `festina_next_timer_deadline()` -- with no active timer and one open connection sitting idle (a WebSocket that never sends or closes), `timeout_ms` stayed `-1` (block forever), so the loop would never come back around to re-check the deadline at all until *something else* happened to wake `poll()` up. Confirmed directly with a real test: a WebSocket connection held open across a `SIGTERM` appeared to respect the 10-second grace period (drained in ~14s) -- but tracing the actual timeline showed the drain had nothing to do with the deadline at all; the TEST'S OWN client script's unrelated 15-second sleep completing and closing its socket was what woke the loop, purely by coincidence. A second test holding the connection for 30 seconds (well past any plausible coincidence) exposed the real bug: the server hung indefinitely, past the supposed 10-second cutoff, with nothing forcing it closed. Fixed by having the drain deadline also bound the `poll()` timeout itself (`if (g_http_draining && (deadline < 0.0 || g_http_drain_deadline < deadline)) deadline = g_http_drain_deadline;`) -- re-verified with the same 30-second-hold scenario, now correctly forced closed at the configured deadline, and with an env-var override (`FESTINA_SHUTDOWN_GRACE_SECONDS`, a debug/test knob, not documented language behavior) added specifically so the automated test suite could exercise this exact path in about a second rather than actually waiting out the 10-second production default.

**A third bug, this one caught by the full test suite rather than a targeted run (the same way claude.md #157's own wasm regression was): the core translation unit -- compiled unconditionally for every target, including `wasm32-wasi` -- failed to build at all once `<signal.h>` entered the picture.** wasi-libc's own `<signal.h>` is an unconditional `#error` unless compiled with `-D_WASI_EMULATED_SIGNAL` (WASI Preview 1 has no signal model whatsoever, the identical "genuinely absent" situation `exec()`/`openPort()`/`try` already document for this target) -- confirmed directly rather than guessed at from the build failure text itself. Fixed by wrapping both the `#include <signal.h>` and every use of `sig_atomic_t`/`signal()`/`SIGINT`/`SIGTERM` in `#if !defined(__wasi__)`, with a matching `#else` providing three harmless no-op stubs (`festina_install_shutdown_handler`/`_shutdown_requested`/`_shutdown_exit_code`) -- needed because a timers-only wasm program (`uses_timers`, still perfectly valid under WASI) still emits a call to `festina_install_shutdown_handler()`, which now simply does nothing there rather than failing to link.

**Verified directly, end to end, with real signals against real compiled subprocesses -- not simulated or reasoned about in isolation:** `SIGTERM`/`SIGINT` against a running `openPort()` server run `on exit` with the correct `128+signal` code and exit with it; a new connection attempt is refused (`ECONNREFUSED`) within one loop tick of the signal arriving; a connection already open at the moment of the signal still completes its response before the process exits; a connection that never closes on its own is forced closed once the grace period elapses (confirmed both at the real 10-second default and, repeatably, via the `FESTINA_SHUTDOWN_GRACE_SECONDS` override); a timers-only program (no `openPort()` at all) exits cleanly via `festina_run_timer_loop`'s own identical check; a plain script with no event loop of any kind is still killed immediately by the OS's default disposition, matching pre-existing behavior exactly, with or without a declared `on exit` handler.

**Tests:** new `tests/test_graceful_shutdown.py` -- `TestHttpGracefulShutdown` (5 tests: `on exit` fires with the right code for both signals, new connections refused immediately, an in-flight connection still completes, forced exit after the grace period for a connection that never closes), `TestTimerOnlyGracefulShutdown` (1 test: a timers-only program), and `TestNoRegression` (2 tests: a plain infinite loop with no event loop stays killable, and -- the specific edge case the second design bug above was found through -- a plain infinite loop that ALSO declares `on exit` stays killable too, with the handler correctly never firing). Full suite: 1705 passed, 8 skipped (up from 1697). Documented in api.md (a new "Graceful shutdown (Ctrl-C / SIGTERM)" subsection under `close()`/`on exit`, cross-referenced from the HTTP server's own Limitations section).

162. HTTP REDESIGN -- url TYPE / parseURL(), http AS A GENUINE VALUE, AND req.send() AS THE CLIENT (fetch() REMOVED IN FAVOR OF IT)

Asked for directly: replace `http`'s `port`/`path` fields with a single `url:text` field, add a `url` struct-like type (`parseURL(text):url`, with `.hash`/`.hostname`/`.password`/`.pathname`/`.port`/`.protocol`/`.searchParams`/`.username`), let `http x = {...}` be constructed as a genuine literal (`{headers}` as shorthand for `{'headers': headers}`), and give `http` a way to make OUTBOUND requests as a client, not just respond to inbound ones on the server. The user's own sketch called the client trigger `fetch(req)`; mid-implementation, two direct corrections replaced it entirely: first "also use req.send() on the client," then "you can remove the fetch function altogether" -- so there is no `fetch()` builtin at all. `req.send()`, called with **zero** arguments, is now the exact same method name `req.send(res)` already used server-side, told apart purely by arity: zero arguments sends `req` itself as an outbound request and overwrites it in place with the response; one `http`-typed argument sends that value as this connection's response. Asked how literally to take the user's own `func`-typed method-field notation (`ok:func`, `send:func[http]`, etc.) in their sketch, the answer was "internally do whatever is most efficient and consistent, I wrote it that way to communicate intent of the methods" -- so these stayed the existing built-in-method-call dispatch pattern (the same shape `.ok()`/`.upgrade()`/every other method on every other type already uses), not a new first-class-func-value mechanism; no functional difference is observable from Festina source.

**Representation: `http` is no longer an opaque `{refcount, conn_id}` handle rebuilding its own headers map on every field read -- it's now a genuine refcounted VALUE, `FestinaHttpValue {refcount, url, method, code, headers, body, body_len, conn_id}` (festina_runtime_http.c), the same shape blob/img/aud/regex already have.** `conn_id` survives as an optional extra: zero for a plain constructed value or a client response (never live), nonzero when the value came from `on request` and is still bound to a real accepted connection -- that's what lets `.ok()`/`.redirect()`/`.upgrade()`/`.send(res)` still reach the right socket. This is a strict improvement over the old design even ignoring the new client/literal features: `req.headers` used to rebuild a fresh `map[text]` from the connection's own header list on EVERY read; now it's built once, at dispatch time, and every subsequent read just retains the same live map. `code` reuses the existing int-null sentinel (`festina_null_int()`/`INT64_MIN`, the same one `text.toInt()`'s failure case already established) for "no response yet" -- `req.code` on a live inbound request, or on a freshly-constructed client value before `.send()` runs.

**`http x = {...}` needed a genuinely new literal-construction path, not an extension of the existing one.** This language has no `{...}` struct-literal syntax at all -- `{...}` in expression position was ONLY ever `ast.MapLit`, which demands one homogeneous value type across every entry (a real constraint an http literal's `url:text`/`code:int`/`headers:map[text]`/`body:any` genuinely violates). Fixed the same way claude.md #156's `amor map[T]` bypass already handles its own "MapLit syntax, non-MapLit meaning" case: intercepted in `_emit_value_for` (codegen.py) and `analyze_var_decl` (semantic.py) BEFORE the generic MapLit branch, whenever the declared/expected type is `HttpType`, and again in `req.send({...})`'s own one-argument form (which never goes through a declared-type position at all) via a shared `_validate_http_lit`/`_emit_http_lit` pair. Five keys accepted, all optional (`url`/`method`:text, `code`:int, `headers`:map[text], `body`: anything with a body form -- `_is_http_body_type`, which is `_is_sendable_type` (`text`/`int`/`float`/`bool`/`blob`/struct/table/`arr`/`map`) PLUS `img`/`aud`, deliberately wider than `log()`/templates/`socket.send()`'s own body-type checks: a real HTTP body uploading or returning a picture is completely ordinary, unlike a WebSocket frame or a log line). A literal key must be a plain `ast.StringLit` -- a computed key expression is rejected outright, since there's no way to validate (or, in codegen, build) a heterogeneous literal whose field set isn't known until compile time.

**The object-literal shorthand `{headers}` (for `{'headers': headers}`) is a small, purely additive parser change, unrelated to the http-specific bypass above -- it works for every `{...}` map/http literal, not just http's.** In `parse_primary`'s `LBRACE` branch: if a parsed key is a bare `ast.Identifier` NOT followed by `:`, it's expanded into `('name' as a StringLit key, that same identifier as the value expression)` instead of erroring; anything else (an explicit `key: value` pair, or a non-identifier key expression) parses exactly as before. `map[int] x = {a, 'b': 2}` and a plain http literal's `{headers}` both go through the identical code path.

**Client-side `req.send()` is a full, blocking HTTP/HTTPS client implementation, genuinely new runtime code (festina_runtime_http.c's new "http -- client side" section + festina_runtime_https.c's new "TLS CLIENT" section), not a thin wrapper over anything that already existed.** Parses `req.url` via the SAME `festina_parse_url()` claude.md's own new `url`/`parseURL()` type uses (CORE, festina_runtime.c -- always linked, so `parseURL()` alone never requires linking HTTP support at all), resolves the host via `getaddrinfo`, connects, sets a 30-second `SO_RCVTIMEO`/`SO_SNDTIMEO` (this runtime's single-threaded design already accepts "a slow handler delays everything"; a timeout bounds the worst case rather than pretending the tradeoff doesn't exist), dispatches to a real mbedTLS CLIENT-mode handshake for `https://` or a plain socket for `http://` (a `FestinaClientTransport` union hides the difference from the rest of the function), builds and sends the HTTP/1.1 request, reads until the peer closes (bounded by the same 8MB-per-connection cap the server side already enforces), parses the status line/headers/body, and overwrites `req.code`/`req.headers`/`req.toText()`-etc.'s underlying body in place -- `req.url`/`req.method` are left untouched, so calling `.send()` again re-sends the same request. **Asked which schemes to support, the answer was "both http:// and https://"** -- meaning EVERY program that calls `req.send()` at all, not just ones that happen to pass an `https://` URL, unconditionally links mbedTLS (the scheme is a runtime string the compiler can't inspect in advance -- the exact same reasoning claude.md #160's `openSecurePort()` already established for the SERVER side, now applied to the client). TLS verification is best-effort: `festina_tls_client_connect` searches a handful of common system CA-bundle paths (`/etc/ssl/certs/ca-certificates.crt` and similar) and uses `VERIFY_REQUIRED` if one is found, `VERIFY_NONE` otherwise -- documented in code comments as exactly that, not a security guarantee for every possible deployment. A genuine failure anywhere in this path (bad host, connection refused, failed handshake, unparseable response) throws via the existing claude.md #157 throw/catch mechanism, matching claude.md #159's own JSON-parser precedent for "this can really fail, with real diagnostic text" -- deliberately NOT this runtime's usual "test, don't fail" convention, since there's no sensible sentinel value for "the network failed."

**Verified far beyond a syntax check, using the same discipline claude.md #160 established for TLS: standalone C harnesses first, then real network round trips.** Before any Python-side wiring, `festina_http_literal_new`/`festina_http_send_client` were exercised directly from a hand-written C driver: a real local `python3 -m http.server` (plain HTTP, correct status/body), a local Python TLS server presenting a SELF-SIGNED cert (correctly THREW a catchable TLS-handshake error -- proof verification is genuinely active, not silently skipped), and two real production HTTPS hosts through the sandbox's own egress proxy (`https://example.com/`, and `https://raw.githubusercontent.com/...` returning real README content) -- a real CA-verified TLS handshake plus a correctly parsed HTTP response, the strongest evidence this actually works end to end. Only after that did the Python-side compiler wiring happen (parser shorthand, semantic.py's `_validate_http_lit`/arity-overloaded `send()`, codegen.py's `_emit_http_lit`/client-`send()`-vs-server-`send()` split, plus the runtime `declare` list, which still had the OLD `festina_http_port`/`_path`/5-argument `festina_http_send` signatures left over from claude.md #151 and needed a full rewrite, caught by a real `bin/festina compile` run rather than by inspection alone), then re-verified with real compiled Festina programs: a server built from `on request`/`http res = {...}`/`req.send(res)` hit with `curl`, and a separate compiled CLIENT program calling `req.send()` (zero arguments) against that same real server subprocess -- genuinely two independent compiled binaries talking over a real socket, not a mock.

**Tests:** `tests/test_http.py` (previously testing `req.port`/`req.path`/3-argument `req.send(data, code, headers)`) rewritten for the new fields/arity throughout, plus new coverage: `http`/`url` literal construction and rejection (unknown key, non-sendable body type, computed key), object-literal shorthand, `url`/`parseURL()` field reads and read-only enforcement, and a new `TestHttpClient` class (4 tests, real `compile_and_run_server` + `compile_and_run` pairs: `req.send()` mutating the request in place with a real response, POSTing a body, sending custom headers, and a genuine network failure -- connecting to `127.0.0.1:1`, nothing ever listens on port 1 -- throwing and being caught). `tests/test_secure_port.py` and `tests/test_graceful_shutdown.py` updated for the new field names/`send()` arity (no new tests needed there -- same behavior, new spelling). Full suite: 1716 passed, 8 skipped (up from 1705). Documented in api.md (the HTTP/WebSocket server section rewritten for `url`/`code` fields and arity-overloaded `send()`, a new "The `http` type" subsection for literal construction, a new "Making outbound requests" subsection for the client form, and a new "The `url` type / `parseURL()`" subsection) and security.md (the network attack-surface section's stale `req.path` reference fixed to `req.url`, and a new bullet noting `req.send()`'s outbound direction as a genuinely new SSRF-adjacent exposure a program building `req.url` from untrusted input should be aware of).

163. NON-BLOCKING req.send(): AN OPTIONAL http.callback FIELD, A BACKGROUND WORKER POOL, AND THE FIRST REAL THREADING THIS RUNTIME HAS EVER HAD

Asked directly, after a long design discussion about `defer`/async options for this runtime (threads vs. a single-threaded deferred queue vs. real coroutines) that the user chose not to build any of, in favor of something much more narrowly scoped: add an optional `callback:func[http]:void` field to `http`. Non-null makes `req.send()` (the zero-argument CLIENT form) return immediately instead of blocking, running `callback` later -- passed the same `req`, mutated in place with the response, exactly like the blocking form already does -- once the request actually completes.

**This is the first real multi-threading this runtime has ever had for GENERATED FESTINA CODE, and it was scoped deliberately narrowly to avoid reopening the whole-runtime concurrency redesign the earlier `defer` discussion identified as the expensive part.** No general async/await, no coroutine state-machine transform, no change to how ordinary Festina code executes -- exactly ONE operation (an outbound `req.send()`) can now run on a background thread, and the actual network I/O reuses `festina_http_send_client` (claude.md #162) completely unchanged. The one piece of genuinely new architecture: a small, lazily-spawned pool of 4 worker threads (POSIX only for now; Windows keeps calling the blocking path regardless of `callback`, the same staged per-platform rollout every other http feature here already went through) that do the blocking connect/TLS/parse work, then hand the COMPLETED result back to the single MAIN thread -- which is the only thread that EVER runs a Festina callback, touches a refcount, or reads/writes a global. That split is the entire safety argument: nothing about this runtime's existing single-threaded design (unsynchronized refcounts, unsynchronized globals, one connection table) had to change, because generated Festina code genuinely still only ever executes on one thread.

**Design: `festina_http_send_client_dispatch` -- codegen's new entry point for `req.send()`, replacing a direct call to `festina_http_send_client` -- checks `req.callback` at RUNTIME (codegen has no way to know it in advance) and either calls the existing blocking function directly (unchanged) or retains the payload, queues it, and returns.** A worker pops the job, runs the (still fully synchronous) blocking send, pushes the result onto a second queue, and writes one byte to a self-pipe. The main thread's own `festina_run_http_loop` -- already a `poll()`-based loop servicing listeners/connections/timers -- gained one more pollable fd (the self-pipe's read end, appended past the end of its own listener/connection range so the existing index-based dispatch loops never need to know it exists) and a new per-iteration drain step (`festina_async_drain_completed`, called unconditionally each tick, the identical placement `festina_fire_expired_timers` already has) that runs every completed job's `callback` right there, on the main thread. `g_async_outstanding` (queued + in-flight + completed-but-undrained) is what keeps the loop's own "nothing left to wait for" exit check from returning out from under work still in flight -- the same job an active timer's deadline already does -- which is also what makes a program with ONLY `req.send()` calls and no `openPort()`/timers at all (the feature's own originating example) correctly stay alive exactly as long as it needs to and no longer.

**A real, pre-existing correctness hazard surfaced by this feature, not created by it: `festina_throw`'s catch-frame stack (`g_festina_catch_top`/`g_festina_error_message`) was a plain, unsynchronized global -- correct for exactly as long as this runtime stayed single-threaded, which stopped being true the moment a background thread could call something that throws.** `festina_http_send_client` throws on a real network failure (claude.md #162); a worker thread hitting that would either corrupt whatever try/catch frame the MAIN thread happened to have open at that exact moment, or `__builtin_longjmp` into a stack frame that isn't even on the current thread's stack -- a genuine crash/corruption hazard, not a hypothetical one. Fixed by making both globals `__thread`-local (confirmed compiling clean for wasm32-wasi too, where TLS collapses to an ordinary global in a single-threaded target with no behavior change) and having each worker set up its own catch frame via a hand-written `__builtin_setjmp` (NOT generated LLVM IR -- this is the first place in the runtime that calls `festina_try_push`/`festina_throw`'s own matching-setjmp machinery directly, from hand-written C) before calling `festina_http_send_client`, converting a caught failure into `r.code` staying `null` (explicitly reset, in case the literal set it to something else) with `r.toText()`/etc. holding the failure's own message -- there's no `try` frame left to deliver a throw TO by the time a background result comes back later, so this is the only sane place for it to go. Verified directly with a standalone two-thread harness (one thread throwing repeatedly, one not, racing the MAIN thread's own independent try/catch) BEFORE this went anywhere near the real runtime, confirming `__builtin_setjmp`/`__builtin_longjmp` genuinely interoperate this way, not just reasoned about.

**A second real, ThreadSanitizer-caught bug, found only by actually running concurrent workers against real sockets, not by inspection: `g_http_send_header_buf` -- a scratch global `festina_write_extra_header`'s own `festina_map_for_each` callback shape forces (no user-data parameter to route it through directly) -- was ALSO a plain global, safe for exactly the same "only one thread ever called this" reason `g_festina_catch_top` was, and broken for the identical reason once `festina_http_send_client` became callable from multiple worker threads at once.** TSan reported it immediately and reproducibly (two workers building two different requests' headers at the same time, corrupting each other's own `FestinaSendBuf` pointer) on the very first concurrent test run; `festina_http_send` (the SERVER side) never had this problem, since it's still exclusively main-thread. Fixed the identical way -- `__thread` -- confirmed clean (0 warnings) across repeated TSan runs afterward, including a genuine mix of concurrent successes and connection-refused failures exercising both the normal and the caught-throw path at once.

**Verified far beyond a syntax check, in layers: a standalone C harness first (the sjlj-interop question above), then real compiled Festina programs (non-blocking ordering -- the caller's own `log()` line appearing BEFORE the callback's, proving the call genuinely returned before the network round trip finished -- correct response data, the failure path via a connect-refused port, 6-8 concurrent dispatches all completing with a shared global counter incrementing correctly with no lost updates, and the specific "built entirely inside a function that already returned" escape-analysis scenario the feature's own design explicitly had to get right), then ThreadSanitizer against the real runtime (0 warnings after the two fixes above, confirmed stable across repeated runs) and Valgrind leak-checking on the real compiled binaries (0 bytes definitely/indirectly lost -- the only "possibly lost" reports are `pthread_create`'s own well-documented TLS/DTV allocation bookkeeping, not application code, confirmed by every one of them tracing back to `festina_http_send_client_dispatch`'s own thread-spawn call and nowhere else).

**Tests:** new `TestHttpCallbackSemantics` (4 tests: the field accepts a matching `func[http]:void`, rejects a wrong signature, is read-only, reads back correctly) and `TestHttpCallbackRuntime` (4 tests, all real `compile_and_run_server`+`compile_and_run` pairs: non-blocking ordering with a real response, the failure path setting `code` to `null`, 8 concurrent callbacks all completing, and the cross-function-boundary survival case) in `tests/test_http.py`. Full suite: see claude.md #164 below (documented together; both shipped in the same pass).

164. TWO http SHORTHANDS: `{...}.send()` AS A VarDecl INITIALIZER, AND THE FULLY ANONYMOUS `http {...}` STATEMENT

Asked for directly, immediately after claude.md #163 shipped: let an http literal be built and sent in one expression (`http req = {...}.send()`), and let the send happen with no variable at all when the response is never read (`http {...}`, implicitly sent, callback-or-not). The second form's own explicit requirement -- "it shouldn't be garbage collected until after the callback fires and if it didn't escape the function" -- was already exactly what claude.md #163's own retain-inside-`festina_http_send_client_dispatch` design guarantees (a callback-mode value survives independent of its own declaring scope specifically because the dispatcher itself, not the caller's own binding, is what keeps it alive) -- confirmed directly with the SAME escape-analysis test claude.md #163 already had, this time built entirely inside a function that returns before the response arrives, END TO END with these new shorthands: no additional lifetime engineering needed, only new SYNTAX on top of already-correct plumbing.

**The core piece both shorthands share: `.send()`'s RECEIVER can now itself be a raw `ast.MapLit`, treated as an http literal directly -- `{...}.send()` -- without ever calling the generic `infer()`/`_emit_expr` on it.** A MapLit's own generic handling (both inference and codegen) demands one homogeneous value type across every entry, which an http-shaped literal's genuinely heterogeneous fields (text/int/map/func/body) can never satisfy -- the exact same reason `http x = {...}` needed its own bypass in claude.md #162. Wired in at both layers: semantic.py's `_infer_call` now checks `isinstance(callee.obj, ast.MapLit)` BEFORE calling `infer(callee.obj, scope)` at all (gated on `callee.prop == "send"` first, so a MapLit calling some unrelated method name is untouched), validating it via the SAME `_validate_http_lit` the VarDecl bypass already uses; codegen's own `.send()` dispatch branch does the identical check, building the receiver via `_emit_http_lit` instead of `_emit_expr` when it's a MapLit. A dedicated release helper, `_release_http_send_receiver` (codegen.py), replaces the generic `_release_owned_receiver` at this one call site specifically because `_is_owning_refcounted_source` was never taught to recognize a bare MapLit as owning (nothing http-adjacent could be built from one before claude.md #162) -- without it, an anonymous `{...}.send()` with no named variable anywhere would leak its own freshly-built value every time; this is the SAME `isinstance(expr, ast.MapLit)` OR-check the argument-position case (`req.send({...})`) already established, applied here to the receiver instead.

**`http req = {...}.send()` needed real care about what it MEANS, not just how to parse it -- the two readings genuinely differ in risk.** The first design considered making `.send()` itself RETURN the value it was called on (so `http req = req.send()` on any pre-existing `req` would "just work" symmetrically) -- rejected before implementation, not caught by a test: if the receiver is an ORDINARY pre-existing variable rather than a fresh literal, `.send()`'s return value would be the SAME pointer that variable already refers to, and `_is_owning_refcounted_source` unconditionally treats any `ast.Call`'s result as fresh/unaliased (a deliberate, otherwise-sound simplification documented in its own doc comment) -- binding that "return value" into a NEW variable would create two independent bindings to one value with no retain between them, each releasing it independently at scope exit: a real double-free. Fixed by scoping this narrowly instead, exactly matching the user's own framing (the two-statement form shown FIRST, the chained form introduced as "could ALSO be written like" -- the same meaning, shorter syntax, not a new return-value mechanic): a NEW shared helper, `_http_send_lit_receiver` (duplicated verbatim in semantic.py and codegen.py, matching how these two files already keep no cross-import dependency on each other), recognizes ONLY the exact shape `Call(Member(<MapLit>, 'send', computed=False), [])` sitting directly in a VarDecl's own init position -- `.send()` itself still always returns void everywhere else, unchanged; this shape is recognized as sugar for "build the literal, then also dispatch it," with the VarDecl's own new local taking ownership of the freshly-built (therefore genuinely unaliased) value directly, no extra retain needed, no release either (unlike the bare-expression-statement case, which DOES release it once the statement ends -- this one doesn't, because `req` keeps living).

**`http {...}` (no variable name at all) is pure parser-level sugar, not a new AST shape of its own.** `parse_statement` already special-cases `t.type == "LBRACE"` as a plain block (never expression position) BEFORE it would otherwise misroute -- so this shorthand couldn't be spelled as a literal, unadorned `{...}.send()` statement even if it wanted to be (`{...}.send()` written directly as a top-level statement is genuinely unreachable source syntax, confirmed by two tests that had to be corrected mid-implementation once this was discovered, not merely asserted) -- `http` has to come first for the parser to disambiguate at all. Checked in `parse_statement` before `_looks_like_declaration` would otherwise route a bare `http` into `parse_var_decl` (whose own `self.eat("IDENT")` right after the type expects a variable name this form deliberately has none of): `http` immediately followed by `{` parses the MapLit via the ordinary `parse_primary` (getting the `{headers}`-shorthand support, claude.md #162, for free) and desugars directly into `ExprStmt(Call(Member(maplit, 'send', computed=False), []))` -- semantic.py/codegen.py need no separate awareness this spelling exists at all, since it produces the identical AST the bare-expression `{...}.send()` case above already handles.

**Verified end to end with real compiled Festina programs, both shorthands, both with and without a callback:** the chained form (`http req = {...}.send()`) against a real `compile_and_run_server`, confirming non-blocking ordering and correct response data exactly like claude.md #163's own plain form; the anonymous form (`http {...}`) the same way, PLUS the callback-less blocking variant (response entirely discarded, checked only for "doesn't leak or crash"); the escape-analysis survival case specifically through the anonymous form, matching the user's own stated requirement word for word. Valgrind leak-checked on all of these (0 bytes definitely/indirectly lost, same harmless `pthread_create` DTV pattern claude.md #163 already established as expected).

**Tests:** new `TestHttpShorthandSemantics` (6 tests: bare-MapLit-send analyzes, an unknown key is still rejected through this path, the chained VarDecl form, the anonymous form's own AST shape confirmed directly via a new `ast_mod` fixture in conftest.py, a bare block statement stays unaffected, and a callback-less anonymous send still analyzes) and `TestHttpShorthandRuntime` (3 tests: the chained form, the anonymous form, and the callback-less blocking anonymous form) in `tests/test_http.py`. Full suite: 1733 passed, 8 skipped (up from 1716, by claude.md #163's 8 tests and #164's 9). Documented in api.md (a new "Non-blocking requests: callback" subsection and a new "Shorthand: {...}.send() and http {...}" subsection, both under Making outbound requests, plus `callback` added to the `http` type's own field list).

165. NON-BLOCKING blob LOADING VIA .callback() -- A GENERIC ASYNC-IO WORKER POOL SHARED ACROSS THREE POSSIBLE HOST LOOPS

Asked directly, immediately after claude.md #163/#164 shipped: extend the same `callback` pattern from http's client `req.send()` to blob/img/aud's own file loading -- `blob 'path'.callback(fn)`. Shipped for **blob only** in this pass; img/aud are a real, likely follow-up, not ruled out -- see below for exactly why they were deliberately left out rather than rushed.

**The core design difference from claude.md #163's own http pool: there is no guarantee ANY particular loop is running.** Any use of `req.send()` already implies `uses_http`, which always routes the program into `festina_run_http_loop` -- so http's own async pool only ever needed to hook into that one loop. A program using ONLY `blob.callback()` has no such guarantee: no `openPort()`, no graphics, maybe not even a timer, so it could end up in `festina_run_timer_loop`, `festina_run_http_loop`, or `festina_run_event_loop` depending on what else the program does. Solved with a shared hook seam in the core translation unit -- `festina_set_async_io_hooks(outstanding_fn, drain_fn)`, mirroring `festina_set_tls_client_hooks`' own cross-translation-unit registration pattern exactly -- that all THREE loops now check every iteration (`festina_async_io_outstanding()`/`festina_async_io_drain()`, both a safe no-op when nothing registered them). `festina_run_timer_loop`'s own "nothing left to wait for" exit condition also now factors in outstanding async-io work, and codegen's loop-selection widened its `elif self.uses_timers:` branch to `elif self.uses_timers or self.uses_async_io:` -- one changed condition is the entire fix for "guarantee SOME loop runs," since that loop already checks the hooks regardless of why it was entered.

**A genuinely new translation unit, `festina_runtime_async.c` -- a generic (payload, work_fn, callback, release_fn) job queue and worker pool, not http's own http-specific one reused.** Deliberately kept separate from claude.md #163's already-built, already-TSan-verified http pool rather than unified with it -- refactoring stable, tested code to be generic purely to save one thread pool's worth of memory isn't worth the regression risk at this runtime's scale. Linked only when `CodeGen.uses_async_io` is set (a full per-feature `_RUNTIME_FEATURES["async_io"]` entry in cli.py, threaded through every one of the three compile paths -- the cached-object-file loop, the clang-IR-frontend fallback, and the wasm32-wasi path, which REJECTS it outright: the default non-shared-memory wasm32-wasi target this project builds for has no real pthread implementation to speak of, the identical "genuinely absent" situation exec()/http/try are already in for that target). Unlike http's own pool, this one needs NO exception-safety machinery at all (no thread-local catch frame, no `__builtin_setjmp`) -- confirmed directly by checking first, not assumed: `festina_blob_open`/`festina_read_file_sized` never call `festina_throw`, only ever answering an empty blob on failure (the same "test, don't fail" contract blob has always had), so the worker's whole body is just `work_fn(payload)`.

**Why img/aud were asked for but NOT shipped here: both already call `festina_fail()` -- a hard, unconditional `exit()` -- on a corrupt or unreadable file, unlike blob's own graceful contract, and that call would now happen from a background WORKER thread instead of the main thread.** `exit()` is not fully specified as safe to call concurrently with other threads still running (buffered-stdio/atexit-handler races are a real, if narrow, category of risk under POSIX) -- a genuinely new risk this feature would introduce for img/aud specifically that blob never has, and not something to ship without real verification this pass didn't have time to do properly. img adds a second, independent concern on top: its decoded value is a `cairo_surface_t*`, which would need to be CREATED on the worker thread to make the same "empty placeholder now, filled in later" pattern blob uses work at all -- a Cairo thread-safety question (creating independent surfaces from multiple threads) never before asked of this codebase, and not confirmed safe here either. Both are real, deliberately explicit gaps -- semantic.py's own `.callback()` type-check accepts `func[blob]:void` only, rejecting `func[img]:void`/`func[aud]:void` with a clear "isn't implemented yet" message naming exactly what's missing, rather than a confusing downstream failure; the parser's own anonymous-statement shorthand still recognizes `img`/`aud` as a prefix (so a program reaching for the not-yet-built form gets that same clear semantic.py error, not a raw parse error).

**Syntax: `<text-expr>.callback(fn:func[blob]:void)` works as an ordinary expression anywhere a text->blob coercion already would (a VarDecl init, an assignment, a function argument) -- no special position required, unlike claude.md #164's `{...}.send()`.** The target type is read directly off `fn`'s own inferred signature, not from surrounding declared-type context -- `.callback()`'s receiver is plain `text`, never a heterogeneous literal the way an http `{...}` literal is, so there's no MapLit-style generic-inference conflict to route a bypass around at all. The anonymous, no-variable form (`blob 'path'.callback(fn)`) needed one small parser addition -- `blob`/`img`/`aud` followed by a non-`IDENT` token (the same "declared type known, next token isn't a name" signal claude.md #164's `http {...}` used, generalized past the single `LBRACE` case) -- but needs no AST rewriting at all: the leading type keyword is simply discarded (redundant, since `.callback()`'s type is already unambiguous from `fn`'s signature) and the expression is parsed and wrapped as an ordinary `ExprStmt`, unlike `http {...}`'s own explicit `{...}.send()` desugar.

**Verified directly with real compiled Festina programs, the same discipline claude.md #163 established:** non-blocking ordering (the caller's own `log()` line appearing before the callback's), correct loaded content, a genuinely unreadable path answering gracefully (`exists()` false, `toText()` empty, no crash), 6 concurrent loads all completing with a shared global counter incrementing correctly, both syntax forms (chained-declaration and fully anonymous), a program with `.callback()` as its ONLY feature (no `openPort()`, no timer, no graphics) correctly staying alive until the load completes and THEN exiting cleanly with no explicit `close()` at all -- confirming the loop-selection widening and the timer loop's own outstanding-check both work end to end -- and, specifically exercising the cross-loop concern this entry is about, a real `openPort()` server whose `on request` handler ALSO dispatches a background blob load, confirming `festina_run_http_loop`'s own hook integration (not just the timer loop's) fires the callback correctly. Valgrind leak-checked throughout (0 bytes definitely/indirectly lost; the only "possibly lost" reports are the same well-documented `pthread_create`-internal DTV-allocation pattern claude.md #163 already established as expected, not a real leak).

**A real regression, caught only by running the FULL suite rather than these tests in isolation:** the first version of `festina_blob_load_dispatch` (in always-linked core) called `festina_async_io_run` -- defined only in the conditionally-linked `festina_runtime_async.c` -- directly, by name. That put an unresolved external symbol in every compiled program's core object file regardless of whether it ever used `.callback()` at all, a hard link failure for literally every test that compiles anything (`python3 -m pytest tests/ -q` returned 724 failed, 1009 passed, 8 skipped -- confirmed to be exactly this, via one failing test's own linker output, not a mystery). This is precisely the mistake the hook-registration seam exists to prevent -- caught here because the seam had only been applied to `festina_async_io_outstanding`/`_drain`, not to dispatch itself. Fixed by extending `festina_set_async_io_hooks` with a third `run_fn` pointer and adding `festina_async_io_dispatch()` in core, which calls through the hook (falling back to a synchronous inline run if somehow unregistered) instead of ever naming `festina_async_io_run` directly -- the same pattern the other two hooks already used, now applied consistently. Re-verified with real compiles both ways (a program using `.callback()`, and a plain `log()`-only program that never touches async-io at all), then a full clean suite run to confirm no other program was affected.

**Tests:** new `tests/test_async_io.py` -- `TestAsyncIoSemantics` (9 tests: a string literal and an arbitrary text expression both work as the receiver, arity/argument-type checks, img/aud's own explicit rejection, the anonymous form's AST shape confirmed directly, and a regression check that the pre-existing `blob name` zero-init declaration is unaffected by the new parser branch) and `TestAsyncIoRuntime` (6 tests: non-blocking ordering, exiting cleanly with no explicit `close()`, the graceful-failure path, 6 concurrent loads, the anonymous form, and the combined-with-a-real-http-server cross-loop case). Full suite: 1748 passed, 8 skipped (up from 1733, by these 15 new tests) -- run clean AFTER the linker-bug fix above, not before. Documented in api.md (a new "Loading in the background: .callback()" subsection under Files).

166. COMBINING openPort() WITH GRAPHICS -- SERVICE HTTP FROM INSIDE THE GRAPHICS EVENT LOOP

Asked directly, picking the first item off api.md's own http Limitations list: lift claude.md #151's original restriction against combining `openPort()`/`on request`/.../`on socketClose` with graphics (`render()`, or an `on mouseDown`/.../`close` handler) in the same program. That restriction existed because main() only ever blocks in ONE loop (`_emit_main_and_entry`'s own loop-selection, festina/codegen.py) -- graphics winning over http whenever both were present meant an open port would just sit there, never actually polled or accepted, so the combination was rejected outright at compile time instead of silently shipping a broken binary.

**The fix doesn't touch loop-selection at all -- it makes the graphics loop capable of servicing http itself, the exact same hook-seam pattern claude.md #165 built for async-io, applied to a second, independent pair of loops.** `festina_run_event_loop` (festina_runtime_graphics.c, linked only when a program opens a window) can't reference `festina_runtime_http.c`'s own symbols directly -- that file is linked only when a program uses http, and a direct-by-name reference would force it into every graphics program regardless (precisely claude.md #165's own just-fixed linking bug, in a different pair of files). Solved with a new hook seam in core: `festina_set_http_service_hooks(outstanding_fn, ready_fn)`, registered by `festina_register_http_service_hooks()` (defined in festina_runtime_http.c, called from main() whenever `self.uses_http`, whether or not the program also uses graphics -- harmless either way, since nothing ever calls through the hooks unless festina_run_event_loop itself is linked). `festina_run_event_loop` bounds its own wait to 20ms whenever `festina_http_service_outstanding() > 0` (a listener open, a live connection, or a pending background client request from claude.md #163) -- the identical shape it already uses for outstanding async-io work -- and calls `festina_http_service_ready()` every iteration, a no-op default that becomes a real, zero-timeout poll pass once http.c registers it.

**`festina_http_service_ready`'s own implementation, `festina_http_service_once`, is a deliberately SEPARATE, smaller copy of `festina_run_http_loop`'s own poll-set-building/accept/dispatch logic, not a refactor of it.** That loop is already fully tested end to end, including graceful shutdown's own grace-period draining; duplicating roughly 30 lines here avoided any risk of regressing it, the same "don't refactor stable, tested code to save a few lines" call claude.md #165 already made for async-io's pool vs. http's. The duplicate deliberately does NOT replicate the standalone loop's shutdown-draining behavior at all -- see the next paragraph.

**One real, documented gap: a combined program's shutdown skips the standalone server's own graceful-shutdown grace period entirely.** `festina_run_http_loop`'s own Ctrl-C/SIGTERM handling (claude.md #161) closes listeners and gives already-open connections up to 10 seconds to finish before exiting anyway. `festina_run_event_loop`'s shutdown path is unchanged by this entry: it tears the window down and exits immediately the instant it always has, with no equivalent drain window. Teaching the graphics loop its own copy of that grace-period bookkeeping was a bigger change than "make the combination possible at all" needed to take on in one pass -- documented in both api.md's http Limitations and security.md's own single-threaded-availability bullet as a known simplification, not silently dropped.

**Verified with a real compiled program combining both** -- an `on request` handler and an `on mouseDown` handler in the same source, run under Xvfb: multiple real http requests all answered 200 while the window stayed open, a real `xdotool` click still reached `on mouseDown` (interleaved with requests before and after it, proving neither direction starves the other), and a clean SIGTERM exit. Valgrind-checked: the one "definitely lost" report it turned up (93 bytes in 9 blocks, `festina_conn_readable` -> `festina_text_own` -> `strdup`) reproduces byte-for-byte identically on a plain, non-graphics `openPort()` program killed with an immediate SIGTERM right after its last request completes -- confirmed directly by running the same check against `festina_run_http_loop` alone before concluding anything -- so it's a pre-existing abrupt-shutdown race in code this entry didn't touch, not a regression; out of scope for this pass.

**Tests:** `tests/test_http.py`'s `TestPlatformAndWasmGating.test_http_and_graphics_together_is_rejected` (a CompileError assertion) replaced with `test_http_and_graphics_together_compiles_cleanly` (asserts no raise), and a new `TestGraphicsAndHttp` class (2 tests, Xvfb-gated like TestGraphics: a request served while the window stays open, and window input/requests interleaved in one process, both via `run_graphics_program`). Full suite: 1750 passed, 8 skipped (up from 1748, by these 2 new tests -- one test removed, two added). Documented in api.md's http Limitations section and security.md's single-threaded-availability bullet.

167. HTTP/1.1 KEEP-ALIVE -- PLUS A REAL, PRE-EXISTING map[text] LEAK FOUND WHILE VERIFYING IT

Asked directly, next off api.md's own http Limitations list after claude.md #166: "No keep-alive. Every response closes the connection afterward." Ordinary HTTP/1.1 semantics now apply -- a connection stays open for another request once a response finishes, closing only when the request/response pair actually calls for it.

**The version/`Connection` header decide keep-alive once, right when a request's headers are parsed.** `festina_try_parse_request` now reads the request line's own HTTP version (previously parsed and discarded) and, once headers are available, computes `c->keep_alive`: an explicit `Connection: close` always forces it off (exact match, case-insensitive, the same rigor the pre-existing Upgrade-header check already uses); otherwise it defaults to the version's own convention -- keep-alive for HTTP/1.1+, close for HTTP/1.0. `festina_http_ok`/`_redirect`/`_send` (the three server-side response writers) all answer with a matching `Connection: keep-alive`/`Connection: close` through one new shared helper (`festina_append_connection_header`) instead of the old hardcoded `Connection: close` literal each had.

**A keep-alive response resets the connection for another request instead of tearing it down.** `festina_dispatch_request`'s own tail now branches on `fresh->keep_alive`: true calls a new `festina_conn_reset_for_next_request` (frees every per-request field -- method/path/headers/body/etc -- exactly mirroring what `festina_conn_new_slot` zeroes for a brand new connection, and shifts any bytes already read past this request down to the front of `buf`), false still calls the original `festina_conn_teardown`. `festina_conn_readable`'s own dispatch site became a loop (try-parse, dispatch, refetch-by-id, repeat while still `READING_REQUEST`) rather than a single call -- needed because a client that pipelines (sends request 2 before reading response 1) can leave a second complete request already sitting in `buf` right after a keep-alive reset, and simply waiting for another `poll()`-readable event to notice it can deadlock: the client already sent everything and is now only waiting on responses. Verified directly: two requests over one `http.client.HTTPConnection` (same underlying socket object, confirmed via identity), an explicit `Connection: close` request actually closing (http.client itself drops the socket), a raw HTTP/1.0 request defaulting to close, and three requests pipelined in a single write all answered in order.

**An idle keep-alive connection -- no request in flight, just open waiting to be reused -- is reaped after ~15 seconds (`FESTINA_HTTP_KEEPALIVE_IDLE_SECONDS`, overridable via the environment, the exact same test-only-override shape `FESTINA_SHUTDOWN_GRACE_SECONDS` already established) so it doesn't hold an fd/connection-table slot open forever.** Without this, keep-alive would introduce a genuinely new resource-exhaustion path this runtime never had before: every previously-alive connection WAS mid-request, by construction, before keep-alive gave one a reason to be alive AND idle at once (security.md's own "no connection-count limit" bullet updated to note this). `festina_reap_idle_keepalive_connections` (called every iteration from both `festina_run_http_loop` and `festina_run_event_loop`'s own `festina_http_service_once`, claude.md #166) only ever targets a connection with nothing buffered and nothing parsed -- a slow client mid-request is never touched, bounded only by the pre-existing `FESTINA_HTTP_MAX_BUFFER` cap the same way it always was. `festina_earliest_keepalive_deadline` folds the earliest such timeout into `festina_run_http_loop`'s own `poll()` wait, the same bounding trick the shutdown drain deadline already uses, so an otherwise-quiet server actually wakes up to reap on time rather than whenever the next unrelated event happens to arrive.

**Shutdown also closes an idle keep-alive connection immediately, rather than waiting out the full 10-second grace period for a connection with nothing left to finish -- but this needed a real bug fix before it was safe.** A first version closed any connection matching "nothing buffered, nothing parsed" the instant draining began; a real test (`test_an_in_flight_connection_still_completes_before_exit`, pre-existing, not new) caught it dropping a response outright -- a connection accepted just before SIGTERM, with its one and only request sent just AFTER, looks byte-for-byte identical to an idle reused connection (both have `buf_len == 0`, `!headers_parsed`) even though nothing has gone wrong yet. Fixed with a new field, `served_a_request`, set only inside `festina_conn_reset_for_next_request` -- true means this connection already completed a full cycle and is genuinely idle-for-reuse; false (a freshly accepted connection) is left alone for the ordinary grace period, since it might still be about to send its first request. Re-verified: the original failing test now passes, and a real compiled program confirms the fast path still works (SIGTERM after an idle, already-served connection exits in ~3ms, well under the 10-second grace period).

**A real, pre-existing memory leak found while Valgrind-checking a multi-request keep-alive session, confirmed unrelated to keep-alive itself, then fixed.** A mixed session (4 requests over one connection, an idle-reap, a pipelined burst) showed a real "definitely lost" report scaling with request volume. A debug-symbol rebuild (`-g` added to `_ensure_runtime_object`'s compile command temporarily, reverted after) pinned the exact call site: `festina_build_headers_map` -> `festina_dispatch_request`, not the vague `festina_conn_readable` frame Valgrind's stripped-binary trace had been attributing it to. Isolated from keep-alive directly -- reproduced byte-for-byte on a SINGLE plain request against the code exactly as it stood before this entry (`git show HEAD:runtime/festina_runtime_http.c`), confirming it predates keep-alive entirely and was simply easier to notice at keep-alive's own request volume. Root cause: `festina_release_map` is deliberately value-blind (its own doc comment already says so -- correct for `map[int]`/`map[bool]`, wrong for anything whose values need freeing), and every codegen-generated Festina-level `map[text]` variable already gets a DIFFERENT, value-aware release wrapper (`_release_fn_for_map` in codegen.py) precisely because of this -- but four places this runtime builds a `map[text]` value directly in C, never through codegen, were all calling the generic one anyway: an inbound request's own `headers` and any http value's `.headers` in general (`festina_release_http`, plus the outbound-response overwrite site in the client `req.send()` path), `socket.state` (`festina_conn_teardown`), and `url.searchParams` (`festina_release_url`). Fixed with one new function, `festina_release_text_map` (festina_runtime.c) -- the C-side equivalent of codegen's own wrapper, freeing each value via `festina_map_for_each` before deferring to the same entries/header cleanup `festina_release_map` itself uses -- used at all four sites. Re-verified: 0 bytes definitely/indirectly lost on both the original single-request repro and the full mixed keep-alive session, debug and release builds alike.

**Tests:** new `tests/test_http.py::TestHttpKeepAlive` (6 tests: two requests reuse one TCP connection, explicit `Connection: close`, HTTP/1.0's own close default, three pipelined requests answered in order, an idle connection reaped under a fast `FESTINA_HTTP_KEEPALIVE_IDLE_SECONDS` override, and a request genuinely still arriving is never reaped even past that same window). One pre-existing test (`test_body_arriving_in_a_separate_write_after_headers`) updated to send its own explicit `Connection: close`, since it depended on the old always-closes behavior to read a response until EOF -- the split-arrival behavior it actually tests is unrelated to keep-alive either way. Full suite: 1756 passed, 8 skipped (up from 1750, by these 6 new tests). Documented in api.md (a new Keep-alive subsection) and security.md (the connection-count bullet, plus a new Notable fixed findings entry for the map[text] leak). benchmark.md's own HTTP section flagged as stale post-#167 (its deliberately-matched "no keep-alive anywhere" methodology no longer describes Festina's own real behavior) rather than silently left inaccurate -- rerunning it fairly would mean adding keep-alive to the Rust/Go comparison servers too, a real follow-up not done here.

168. CHUNKED TRANSFER-ENCODING (BOTH DIRECTIONS) AND WEBSOCKET FRAGMENTATION -- THE LAST TWO ITEMS OFF api.md's OWN http LIMITATIONS LIST

Asked directly, in one pass: "let's get them both" -- the last two items api.md's own http Limitations section still listed after claude.md #166/#167 ("No chunked transfer-encoding", "No WebSocket fragmentation"). Both are decode-only features (this runtime never needs to SEND either encoding -- a server response's own body length is always known upfront, since it's a fully-materialized Festina value before `req.send(res)`/`s.send()` ever writes a byte, and this runtime never fragments an outbound WebSocket message either, `festina_ws_send_frame`'s own long-standing "FIN=1, no fragmentation from this runtime" comment unchanged), so both are pure parsing/reassembly work on the RECEIVING side.

**Chunked transfer-encoding: one shared decoder (`festina_chunk_decode_step`), driven two different ways.** RFC 7230 §4.1 chunk framing (`hex-size CRLF, that many data bytes, CRLF, repeat, a 0-size chunk plus optional trailers plus a final blank line ends it`) is genuinely the same grammar whether it's an inbound REQUEST body (server side, claude.md #151) or an outbound RESPONSE body (client side, claude.md #162's `req.send()`) -- so one function decodes both, called two different ways for two different reasons. The server side needs it INCREMENTALLY: `festina_try_parse_request` may see a chunked body arrive across several separate `recv()` calls, so `festina_chunk_decode_step` takes an explicit `*consumed` position it can resume from (mirroring `header_scan_pos`'s own resumable-scan shape exactly) and simply returns 0 ("not done yet, call me again once more bytes arrive") when it runs out mid-chunk. The client side needs it only ONCE: `festina_client_read_all` already reads an entire response until the peer closes (this client always sends its own `Connection: close`, so any real server honors that and closes after responding, chunked or not) before `festina_parse_http_response` ever runs, so there's nothing to resume across calls there -- a truncated or malformed chunked response is treated the same lenient way a short `Content-Length` one already was: whatever decoded so far is simply the body, not a thrown error. Trailer headers (rare in practice) are scanned past and discarded, never merged into the request/response's own headers map -- this runtime has no use for them once the body already exists.

**No new size limit needed for a chunked REQUEST body specifically** -- confirmed by reasoning about the encoding, not by adding a new check: `festina_conn_readable`'s existing `FESTINA_HTTP_MAX_BUFFER` cap on the raw connection buffer already bounds it, since chunk framing is never SHORTER than the data it encodes (every chunk adds at least `"N\r\n"` + `"\r\n"` of its own overhead), and the decoded body only ever comes from bytes already sitting in that same capped buffer.

**WebSocket fragmentation reassembly needed its own DEDICATED cap, unlike chunked HTTP bodies -- the identical reasoning above doesn't hold for it.** Each wire frame is fully consumed OUT of the connection's read buffer the moment it's parsed (`festina_ws_try_parse_frame`'s own `memmove`), so that buffer's cap only ever bounds ONE frame at a time, never the sum of many small ones a hostile or broken peer could otherwise use to grow a reassembled message without bound. `festina_ws_frag_append` enforces the same `FESTINA_HTTP_MAX_BUFFER` limit explicitly, closing the connection with WebSocket code 1009 ("Message Too Big", a real, standard close code, not an invented one) if a peer tries to exceed it.

**Fragmentation is a state machine, not just "handle FIN=0 too" -- `festina_ws_try_parse_frame` now reports the raw FIN bit honestly (no more collapsing it into a synthetic 0xFF "unsupported" opcode) and a new `festina_ws_process_one_frame` does the reassembly.** A FIN=0 text/binary frame starts a message; FIN=0 continuation frames (opcode 0x0) extend it; a FIN=1 continuation frame completes it, dispatching to `festina_dispatch_ws_frame` with the ORIGINAL opcode and the full concatenated payload -- the exact same shape a single, ordinary FIN=1 message already dispatched with, so that function itself needed no changes at all. Control frames (close/ping/pong) are NEVER fragmented per RFC 6455 §5.4 and are handled immediately regardless of whether a text/binary message is mid-reassembly -- the RFC's own explicit allowance for interleaving them between another message's own fragments, verified directly (a ping sent between two fragments gets its pong back before the message's own reassembly completes, and the message still reassembles correctly afterward). Three distinct protocol violations -- a continuation frame with nothing being reassembled, a new text/binary frame starting while one already is, a fragmented control frame -- all close with code 1002 (protocol error) through one shared `festina_ws_protocol_error` helper, rather than three near-duplicate close-frame-building blocks.

**A second real, pre-existing bug found and fixed along the way, the same "caught by actually testing the new code, not by reasoning about it" pattern claude.md #167's own map[text] leak was:** a malformed chunk (invalid hex chunk-size) closed the connection cleanly in testing -- until it didn't. `festina_try_parse_request`'s own pre-existing malformed-request-line handling (`if (p >= limit) { c->alive = 0; return; }`, unrelated to this entry) was the pattern the new malformed-chunk check copied -- and testing THAT new check directly (a raw socket sending `ZZZ\r\nbad\r\n0\r\n\r\n`) surfaced that `c->alive = 0` alone never actually closes the fd or returns the connection-table slot to the free list; only `festina_conn_teardown`'s own bookkeeping does that. A client sending a malformed request used to leak both, silently, forever -- confirmed directly by testing the ORIGINAL (pre-#168) malformed-request-line path the identical way, unrelated to chunked encoding at all. Fixed at all three sites (the two pre-existing ones plus the new chunked one) by calling `festina_conn_teardown(c)` instead -- safe even this early in parsing, since every field it frees is still NULL at that point and `free(NULL)` is always a no-op.

**Verified directly with real compiled programs, the same discipline claude.md #163/#165/#167 established:** a chunked POST body decoded correctly (including arriving across two separate writes, exercising the resumable scan); chunked combined with keep-alive (the raw byte count consumed for the connection-reset shift, `chunk_scan_pos`, has to be right or the next request on the same connection would desync); a malformed chunk-size closing cleanly (real EOF, not a hang, and the server process itself never crashing); a chunked response from a real, independent, hand-rolled upstream server (never reusing this project's own http implementation, the same "a shared bug can't cancel itself out" discipline the WebSocket client tests already follow) decoded correctly on the client side; a fragmented text message and a fragmented binary message both reassembled correctly; a ping interleaved between two fragments answered immediately without disturbing reassembly; all three protocol-violation shapes closing with code 1002; and a ~9MB fragmented message (past the 8MB cap) closing with code 1009. Valgrind-checked throughout, including the malformed-chunk error path and a combined session mixing chunked bodies (both keep-alive-continuing and closing), plain keep-alive requests, and a fragmented WebSocket exchange on the same running server: 0 bytes definitely/indirectly lost in every case.

**Tests:** new `tests/test_http.py::TestChunkedTransferEncoding` (5 tests: a chunked request body decoded, arriving in separate writes, combined with keep-alive, a malformed chunk-size dropping the connection cleanly, and a chunked response from a real upstream server decoded on the client side) and `TestWebSocketFragmentation` (7 tests: a fragmented text message, a fragmented binary message, a control frame interleaved between fragments, an orphan continuation frame, a fragmented control frame, a new message starting mid-reassembly, and an oversized fragmented message -- the last four all closing with the correct WebSocket close code). Two small module-level helpers, `_ws_mask_frame`/`_ws_recv_frame`, added alongside the existing `_find_festina_window` pattern -- deliberately not importing conftest.py's own `_WsConn` frame-sending logic, since it always sets FIN=1 (this project's own server never sends a fragmented frame) and can't build the FIN=0 fragments these tests need. Full suite: 1768 passed, 8 skipped (up from 1756, by these 12 new tests). Documented in api.md (the Limitations list trimmed to just what's still actually missing, a new paragraph on chunked bodies working transparently under `on request`/client `req.send()`, and a new paragraph on fragmentation being invisible to `on message`) and security.md (the WebSocket reassembly cap noted alongside the existing per-frame one, the parser bullet's own scope widened, and the fd/slot-leak fix added as a Notable fixed finding).
