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
