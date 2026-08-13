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


59. MINIMAL DEPENDENCIES AND SETUP

(Beginning after the existing section 59 in the original document, add the following new sections.)

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
