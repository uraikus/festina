Festina — AI Agent Implementation Specification

PROJECT

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

Simplicity
Predictability
Static typing
Low runtime overhead
Familiar syntax
Native performance
CORE IMPLEMENTATION PRINCIPLES

An AI agent implementing Festina must follow these principles:

Do not invent language behavior that is not specified.
Prefer compile-time validation over runtime validation.
Prefer native LLVM representations over runtime abstractions.
Do not introduce JavaScript-style implicit coercion.
Do not introduce JavaScript truthy/falsy behavior.
Keep primitive values in native memory.
Keep struct values separate from SQLite.
Treat table as a database-backed type.
Resolve all types during semantic analysis.
Resolve all imports before compilation.
Never import the same file twice.
Generate the program entry point automatically.
Do not require the programmer to explicitly initialize SQLite.
All default SQLite operations use festina.sqlite.
Preserve the distinction between compile-time declarations and runtime execution.
COMPILER PIPELINE

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

Do not generate LLVM IR before name and type resolution has completed.

SOURCE FILES

Festina source files use the .f extension.

Examples:

main.f
database.f
ui.f

IMPORTS

Imports use exactly:

import file.f

No import { ... } from ... syntax is used.

No require() syntax is used.

Imports are compile-time operations.

An import means:

"Include this file and all of its dependencies in the current compilation unit."

Example:

import database.f
import ui.f

The imported files do not become runtime modules.

IMPORT RESOLUTION

Import resolution must be recursive.

Given:

main.f
├── ui.f
│ └── graphics.f
└── database.f

the compiler must resolve:

graphics.f
ui.f
database.f
main.f

before parsing the final compilation unit.

A file must only be imported once.

Use canonical/normalized absolute paths when determining whether two imports refer to the same file.

For example:

./utils.f
src/../utils.f

must resolve to the same file if they refer to the same canonical path.

Circular imports must not cause infinite recursion.

For example:

a.f → b.f → a.f

must result in each file being processed once.

ENTRY FILE

The file passed directly to the compiler is the entry file.

Example:

festina main.f

main.f is the entry file.

Imported files are processed before the entry file.

The entry file's executable statements are automatically placed into a generated entry function.

The programmer does not need to write main().

Conceptually:

log('Hello')

becomes:

void func __festina_main() {
log('Hello')
}

The exact internal name may differ, but the behavior must be equivalent.

The generated entry function is the program's runtime entry point.

PROGRAM STARTUP

Startup must occur in this order:

Resolve entry file.
Resolve all imports.
Remove duplicate imports.
Detect circular imports.
Order dependencies.
Parse all source files.
Build symbol tables.
Resolve all names.
Resolve all types.
Validate semantic rules.
Collect table declarations.
Generate SQLite table initialization.
Generate the entry function.
Generate LLVM IR.
Optimize LLVM IR.
Link the executable.
Start the application.
Open/create festina.sqlite.
Ensure declared tables exist.
Execute the generated entry function.

Table initialization must occur before application code attempts to use those tables.

LEXICAL CONVENTIONS

Festina generally follows JavaScript conventions for literals and operators.

Supported string literals:

'hello'
"hello"

Template strings support interpolation:

Hello ${name}

Semicolons are optional.

Preferred style:

text name = 'Festina'
log(name)

PRIMITIVE TYPES

Festina has the following primitive types:

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

The compiler must preserve type information even when a value is null.

TYPE CATEGORIES

The compiler must distinguish types by category.

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

The compiler must never determine a type category through textual guessing.

Each type must have an explicit internal representation.

For example:

PrimitiveType(INT)
StructType(User)
TableType(People)
ArrayType(PrimitiveType(INT))
ArrayType(StructType(User))
ArrayType(TableType(People))

TYPE RESOLUTION

When the compiler encounters:

arr[T]

it must resolve T through the compiler's symbol/type table.

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

The compiler must not use special string matching to determine whether T is a primitive, struct, or table.

UNKNOWN TYPES

An unknown type is a compile-time error.

Example:

arr[Person] people

when Person has not been declared must produce an error similar to:

error: unknown type 'Person'

INTEGER

int is a native integer type.

Example:

int count = 100

The compiler should use an appropriate LLVM integer representation.

Do not represent ordinary integers as SQLite rows.

FLOATING POINT

float is a native floating-point type.

Example:

float percentage = 98.5

BOOLEANS

bool represents boolean values.

Valid values:

true
false
null

Internally, booleans may use:

true → 1
false → 0

However, the semantic type remains bool.

Do not allow arbitrary values to automatically become booleans.

Invalid:

text name = 'Patrick'

if name {
}

TRUTHINESS

Festina does not implement JavaScript truthiness.

Do not treat the following as implicitly boolean:

0
1
-1
''
'hello'
null
arrays
objects

Only expressions whose type is bool may be used as conditions.

EQUALITY

Supported equality operators:

==
!=

Unsupported:

===
!==

Example:

if value == 10 {
log('Ten')
}

The compiler must reject === and !==.

CONDITIONALS

Parentheses are optional.

Both are valid:

if test {
log('yes')
}

if (test) {
log('yes')
}

The condition must resolve to bool.

TERNARY

The JavaScript-style ternary operator is supported:

text result = test ? 'yes' : 'no'

The condition must be boolean.

VARIABLES

Variables use:

type name = value

Examples:

int count = 10
text name = 'Festina'
bool enabled = true

Do not use JavaScript declarations:

var
let

CONSTANTS

Constants use:

const type name = value

Example:

const text name = 'Festina'

Constants should be available for compiler optimization.

FUNCTIONS

Function syntax is:

return_type func name(arguments) {
}

Example:

text func returnHello() {
text value = 'hello'
return value
}

A function without a return value uses void:

void func sayHello() {
log('Hello')
}

FUNCTION ARGUMENTS

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

NULL

null represents the absence of a value.

Every type may contain null.

Examples:

text name = null
int id = null
User user = null

The compiler must preserve the underlying type.

null is not a boolean and must not participate in truthiness conversion.

ARRAYS

Arrays use:

arr[T]

Examples:

arr[int] numbers
arr[text] names
arr[User] users
arr[People] people

Arrays may contain primitive types, structs, tables, or other supported types.

The array element type must be resolved at compile time.

STRUCTS

Structs are native in-memory objects.

Structs may only be declared in global scope.

Example:

struct User {
id:int
name:text
active:bool
}

A struct instance behaves similarly to a JavaScript object:

User user

user.id = 1
user.name = 'Patrick'
user.active = true

Struct fields are statically typed.

Structs are not SQLite tables.

Declaring a struct must not automatically create a SQLite table.

TABLES

Tables are SQLite-backed data models.

Example:

table People {
id:int
name:text
age:int
}

A table declaration must automatically ensure that the corresponding SQLite table exists.

Conceptually:

CREATE TABLE IF NOT EXISTS People (
id INTEGER,
name TEXT,
age INTEGER
);

The programmer must not have to manually execute this statement.

AUTOMATIC SQLITE DATABASE

Every Festina application automatically uses:

festina.sqlite

The database requires no explicit initialization.

The programmer does not need to:

Open the database.
Create the database.
Configure a connection.
Provide a database path.

The runtime/compiler handles this automatically.

SQLITE TYPE MAPPING

The initial mapping is:

Festina SQLite

int → INTEGER
float → REAL
bool → INTEGER
text → TEXT
blob → BLOB

bool uses 0 and 1 in SQLite.

AUTOMATIC TABLE CREATION

Given:

table People {
id:int
name:text
}

the generated application must ensure:

CREATE TABLE IF NOT EXISTS People (
id INTEGER,
name TEXT
);

This must occur before the entry function executes.

If People already exists, it must not be deleted or recreated.

The initial implementation does not need to automatically migrate an existing table when its declaration changes unless migration functionality is explicitly added later.

SQLITE QUERIES

SQLite is accessed through the global:

sqlite()

function.

Example:

arr[People] people = sqlite('SELECT * FROM People')

No import is required.

No database initialization is required.

All queries operate against:

festina.sqlite

PARAMETERIZED SQLITE QUERIES

Parameterized queries must be supported.

Example:

sqlite(
'INSERT INTO People (id, name) VALUES (?, ?)',
[1, 'Patrick']
)

Parameters are passed using an array.

QUERY RESULT TYPES

A query against a table may produce:

arr[People] people = sqlite('SELECT * FROM People')

The compiler knows that People is a TableType.

The resulting array is therefore:

ArrayType(TableType(People))

The compiler must use the table definition to determine the expected row structure.

STRUCT/TABLE SEPARATION

This distinction is mandatory:

struct User

means:

native in-memory type

while:

table Users

means:

SQLite-backed persistent type

Do not merge these concepts internally.

BLOB

blob represents binary data.

Example:

blob explosion = 'path/to/file'

IMAGE

The image type is:

img

Example:

img profile = loadImage('path/to/profile.png')

Images may be passed to:

drawImage(profile, 0, 0)

Supported formats are determined by the runtime.

AUDIO

The audio type is:

aud

Example:

aud music = loadAudio('path/to/music.mp3')

Supported methods:

music.play()
music.stop()
music.isPlaying()

isPlaying() returns bool.

GRAPHICS

Graphics operations are exposed as global functions.

Example:

drawRect(0, 0, 100, 100)

Other graphics functions may include:

drawCircle(50, 50, 25)
drawText('Hello', 20, 20)
drawImage(profile, 0, 0)

Graphics are backed by Cairo.

No GUI import is required.

EVENTS

Event listeners use:

on eventName(arguments) {
}

Example:

on mouse(x:int, y:int) {
log(Mouse moved over canvas on x: ${x}, y: ${y})
}

Example:

on click(x:int, y:int) {
log(Mouse clicked on canvas at ${x}, ${y})
}

The runtime automatically registers declared listeners.

LOGGING

Use:

log('Hello')

Do not require:

console.log()

log() is a built-in global function.

FAILURE

Use:

fail()

instead of throw.

Example:

if test != true {
fail('Test failed')
}

The initial implementation should treat fail() as a runtime failure mechanism.

MEMORY MANAGEMENT

Festina uses automatic memory management.

The programmer does not manually allocate or free memory.

The compiler should prefer stack allocation over heap allocation when the lifetime permits it.

Primitive values should use native LLVM representations.

Do not implement ordinary primitive variables using SQLite pointer tables.

For example:

int example = 166

must not require:

INSERT INTO int_pointers ...

The compiler should generate an appropriate native integer representation.

PERFORMANCE

Performance is a primary language requirement.

Prefer compile-time work over runtime work where practical.

The compiler should use LLVM optimizations such as:

Constant folding.
Dead-code elimination.
Function inlining.
Constant propagation.
Allocation optimization.
Unused-code elimination.

Avoid:

Dynamic reflection.
Runtime type inference.
Unnecessary boxing.
Unnecessary heap allocation.
Dynamic dispatch where static dispatch is possible.
Implicit conversions that require runtime work.
JAVASCRIPT-LIKE FEATURES

Festina should retain familiar JavaScript conventions where they do not conflict with the type system or performance goals.

Supported or intended features include:

String interpolation.
Ternary operator.
Objects through structs.
Arrays.
Date.
Property access.

Festina should not inherit JavaScript's dynamic runtime semantics.

BUILT-IN SQLITE INTEGRATION

SQLite should behave as a built-in application feature rather than a library that requires configuration.

This must work without:

import sqlite
require('sqlite')
openDatabase()
initializeDatabase()

The programmer should simply write:

table People {
name:text
}

arr[People] people = sqlite('SELECT * FROM People')

The runtime handles:

festina.sqlite

automatically.

EXECUTABLE GENERATION

The compiler must produce native executables.

Example:

festina main.f

The resulting executable must not require the Festina source files to execute.

The intended architecture is:

Festina source
↓
LLVM IR
↓
Machine code
↓
Native executable

COMPILER ERRORS

Errors should be reported at the earliest reasonable stage.

Examples of compile-time errors:

Unknown type.
Unknown variable.
Unknown function.
Unknown struct.
Unknown table.
Invalid function argument type.
Invalid return type.
Invalid condition type.
Duplicate declaration.
Invalid import.
Circular import.
Unsupported operator.
Invalid field access.

Error messages should include:

File.
Line.
Column.
Error category.
Human-readable explanation.

Example:

main.f:12:5: error: condition must be bool, found text

SYMBOL TABLE

The compiler should maintain explicit symbol information.

At minimum, symbols should distinguish:

Variable.
Constant.
Function.
Struct.
Table.
Enum.

Types should distinguish:

PrimitiveType.
StructType.
TableType.
ArrayType.
ImageType.
AudioType.

Do not determine symbol meaning by string conventions after parsing.

TYPE CHECKING

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

RESERVED LANGUAGE FEATURES

The following concepts have defined meanings and should not be repurposed without changing the language specification:

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

EXAMPLE PROGRAM

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
text message = Hello ${user.name}
log(message)
return message
}

on click(x:int, y:int) {
log(Clicked at ${x}, ${y})
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
Detect table People
↓
Generate SQLite initialization
↓
Generate entry function
↓
Compile through LLVM
↓
Create/open festina.sqlite
↓
CREATE TABLE IF NOT EXISTS People
↓
Execute application

NON-GOALS

Unless explicitly added to the specification, do not implement:

JavaScript truthiness.
var.
let.
===.
!==.
throw.
require().
Runtime module loading.
Dynamic typing.
Implicit type coercion.
Mandatory database initialization.
Manual database connection management.
IMPLEMENTATION RULE FOR AMBIGUITY

When implementing a feature not fully specified by this document:

Prefer the simplest implementation.
Prefer compile-time behavior.
Prefer native representation.
Prefer JavaScript-like syntax.
Prefer static typing.
Prefer performance.
Do not introduce new syntax without necessity.
Do not silently change existing semantics.
If multiple implementations satisfy the specification, choose the implementation with the lowest runtime overhead.
If behavior genuinely cannot be determined from this specification, treat it as an unresolved language-design decision rather than inventing behavior.

The compiler implementation must remain faithful to this specification rather than assuming behavior from JavaScript, TypeScript, SQLite, or another language where Festina has explicitly defined different semantics.
