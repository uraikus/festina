"""LLVM IR code generation -- claude.md #47 (executable generation) and
the runtime-facing halves of #5/#6 (multi-file compilation -- see
CodeGen.filename's note in generate()), #7/#8 (entry point + startup),
#26 (arrays), #29-31 (automatic SQLite schema sync), #32-34 (sqlite()
queries, parameterized queries, query result types), #37, #39, #40
(img, graphics functions, click/mouse events -- see the "Graphics"
note below), #38 (aud, loadAudio(), .play()/.stop()/.isPlaying() --
see the "Audio" note below), #41/#42 (log/fail), #45 (string
interpolation), #55-57 (int.toFloat(), Math.floor/ceil/round/trunc,
division/modulo by zero), #60/#61 (for/while loops), #63 (array
.length), #66 (postfix ++/--), #67-68 (regex(), .test(), .match(),
.replace()/.replaceAll() -- see _emit_regex_call and the Member-call
handling in _emit_call), #69 (setTimeout/setInterval/clearTimeout/
clearInterval -- see the "Timers" note below), #70 (DatabaseURL -- see
_emit_main_and_entry), #71 (environment.NAME/environment[keyExpr] --
see _emit_environment_get), #72 (map[T] -- literals, indexed get/set,
.forEach() -- see _emit_map_lit/_emit_map_get/_emit_map_set/
_emit_map_foreach_trampoline), #73 (break/continue -- see
self._loop_targets and _emit_stmt's BreakStmt/ContinueStmt handling),
#74 (automatic reclamation of provably non-escaping struct/arr[T]/
map[T] locals, stage 1 -- see escape_analysis.py, _emit_func_body, and
_emit_free_active_locals).
#37, #39, #40 also cover `on key`/`on resize`/`on close` and the
clientWidth/clientHeight globals (see the "Graphics" note below).

Scope: primitives (int/float/bool/text), global and local variables and
constants, functions, if/else, for/while loops, return, the full
expression grammar (arithmetic/comparison/logical/ternary/template
strings/postfix ++/--), structs (GEP field access; see the
heap-allocation note below), arrays (arr[T] literals, indexed get/set,
nesting, `.length` -- see the FESTINA_ARRAY_LLVM_TYPE note below),
automatic table schema sync against festina.sqlite via the
festina_runtime C helpers, sqlite() queries (SELECT into arr[Table],
parameterized INSERT/UPDATE/DELETE -- see the "Query rows" note
below), regex()/.test()/.match()/.replace()/.replaceAll() (POSIX
extended regular expressions via the festina_runtime C helpers -- no
bundled regex engine, see festina_runtime.h's doc comment on why),
img/drawRect/drawCircle/drawText/drawImage/`on click`/`on mouse` (a
real X11 window rendered via Cairo -- see the "Graphics" note below),
setTimeout/setInterval/clearTimeout/clearInterval (see the "Timers"
note below), and aud/loadAudio()/.play()/.stop()/.isPlaying() (a real
ALSA output device, WAV audio only -- see the "Audio" note below).

Nothing is left unimplemented -- every claude.md section this compiler
targets generates real code now. See api.md for the language/standard
library reference and tests/CONTRACT.md's "Status" section for the
up-to-date implemented-vs-not detail.

Graphics (claude.md #37, #39, #40): a real on-screen window (Xlib +
Cairo's Xlib surface backend), not a file written to disk -- "Graphics
are backed by Cairo" plus #40's click/mouse events firing against "the
canvas" only make sense together as an actual window. Opened lazily
(CodeGen.uses_graphics, set by any draw* call or an `on click`/`on
mouse` handler, but deliberately NOT by loadImage() alone -- decoding a
PNG needs no window; see _emit_graphics_call's own note) in main()
before __festina_main() runs, exactly
the same "only pay for what you use" pattern uses_sqlite already
follows for festina_db_open(); a program that never touches graphics
never opens a window. After __festina_main() returns, if graphics or
timers (see "Timers" below) were used, main() blocks in
festina_run_event_loop() (Expose/click/mouse/key/resize/window-close,
interleaved with any pending timers) until the window is closed. Canvas
size starts at
a fixed 800x600 and every shape/text draws in solid black -- claude.md
has no syntax for declaring a size or a color, so both are
implementation-defined defaults, not derived from the spec; the size
can change afterwards, though, if the window is resized (see `on
resize` below). `on click`/`on mouse`/`on key`/`on resize`/`on close`
each compile to a real function (_emit_event_handler) registered with
the runtime as a fixed-signature function pointer (see
_EVENT_SIGNATURES in semantic.py, and festina_runtime.h's declarations,
for the exact signature each one gets) -- any other declared event name
still compiles (so its body is still checked) but is simply never
called, since there's no event source this runtime generates for it.
`on resize` fires on a genuine size change (an X11 ConfigureNotify,
e.g. from window-manager-driven resizing) and clears the canvas back to
white at the new size -- see festina_runtime.h's doc comment for why
(matching how resizing a browser's `<canvas>` element also clears it,
which clientWidth/clientHeight below are themselves named after). `on
close` fires right before the window actually closes (on the same
WM_DELETE_WINDOW ClientMessage the window's own standard close-button
handling already used) -- it cannot cancel the close, there's no
"prevent default" here. clientWidth/clientHeight (a bare Identifier,
not a Call -- see the special case at the top of the Identifier branch
in _emit_expr) read the canvas's *current* size via
festina_client_width()/_height() rather than a compile-time constant,
since `on resize` can change it after startup; referencing either one
sets self.uses_graphics just like a draw* call does, since querying a
window's size implies a window exists. Full rationale (window
decorations, the backing-store redraw strategy, why Xlib instead of a
GUI toolkit) is in festina_runtime.h's doc comment, verified against an
actual rendered window (not just reasoned about) via a virtual X server
-- see tests/test_codegen.py's TestGraphics.

Timers (claude.md #69: setTimeout/setInterval/clearTimeout/
clearInterval): the callback can only be the bare name of an
already-declared `void func name() { }`, since Festina has no
first-class functions or closures -- semantic.py's
_infer_call enforces this structurally rather than through the normal
expression-typing path, and _emit_timer_call below just needs that
function's own LLVM symbol (`@<name>`, already exactly the `void
(*)(void)` function pointer festina_set_timeout/_interval expect -- the
same convention `on resize`/`on close` handlers already use). Opened
lazily via CodeGen.uses_timers, a separate flag from uses_graphics
(set only by setTimeout/setInterval, not clearTimeout/clearInterval
alone) since a timers-only program never opens a window and a
graphics-only program never touches the timer machinery -- but both
flags gate the same festina_run_event_loop() call in main(), which
multiplexes X11 events with timer deadlines when both are in play. See
festina_runtime.h's doc comment on festina_set_timeout/_interval for
the full runtime design, including when a program with pending timers
actually exits (matching Node's "exits once the event loop is empty"
behavior) and why an uncleared setInterval keeps a program running
forever, exactly like in a real JS runtime.

Audio (claude.md #38): a loaded clip (`ptr` to an opaque
FestinaAudio -- decoded PCM samples plus playback state, same
lower-to-`ptr` convention as img/regex/table values) plays through a
real ALSA output device -- ALSA (libasound) rather than a toolkit like
SDL_mixer or a PulseAudio client, since it's the lowest-level standard
Linux audio API and this project already leans toward the smallest
dependency that does the job (claude.md #59; same reasoning that picked
Xlib over a GUI toolkit for graphics). loadAudio() only supports WAV
(16-bit PCM) -- claude.md's own example names a `.mp3`, but unlike
Cairo (which decodes PNG on its own) nothing this project already
depends on can decode MP3 without a real new library, so WAV -- a
container simple enough to parse directly in festina_runtime.c with no
decoder dependency at all -- is the implementation-defined choice here,
the same kind of call PNG-only images already made. play()/stop()/
isPlaying() are ordinary Call-on-Member patterns (same family as
Math.floor/int.toFloat()/the regex methods above), each emitting a
single call to festina_audio_play/_stop/_is_playing; there's no IR-level
machinery of its own; the interesting part -- playback actually running
on a background thread so a playing clip doesn't block the rest of the
program, matching what having a separate isPlaying() to poll implies --
is entirely in the runtime. See festina_runtime.h's doc comment on
festina_load_audio for the full design (why ALSA's "default" device,
the play-while-playing restart semantics, thread-safety between
play()/stop()/isPlaying() and the background thread).

Query rows (claude.md #32-34): sqlite()'s result, when assigned to a
declared arr[Table], is built by festina_runtime's
festina_sqlite_collect_rows -- each row is `col_count` consecutive
8-byte slots (never narrower, regardless of the column's actual Festina
type), addressed here the same way: `_member_ptr`'s TableType branch
does a flat `getelementptr i8, ptr row, i64 (field_index * 8)` rather
than a named LLVM struct type, so there's no struct-layout/alignment
rule to keep in sync between this file and festina_runtime.c beyond
"every field is one 8-byte slot, in declared order" (see
festina_runtime.h's doc comment on festina_sqlite_collect_rows for the
full rationale). Columns map to a table's declared fields *by
position*, not by name, matching claude.md #34's own `SELECT *`
example. sqlite()'s optional second argument (bound parameters) must be
a literal array expression (`ast.ArrayLit`), not an arbitrary
expression -- claude.md #33's own example (`[1, 'Patrick']`) is itself
a heterogeneously-typed literal, which Festina's normal arr[T] typing
rules don't allow as a real array *value*; treating it as special call
syntax instead (each element bound individually, by its own type, at
compile time) sidesteps that without changing arr[T] semantics
elsewhere.

Uses LLVM's opaque-pointer IR (`ptr` everywhere) to match clang 15+'s
default, so no manual bitcasting between pointer "flavors" is needed.

Struct storage is always heap-allocated (calloc), never a stack alloca,
even for a struct declared local to a function. claude.md #43 prefers
stack allocation "when the value's lifetime permits it," which would be
true for a struct that provably never leaves its declaring function --
but a struct's address genuinely can outlive its function (returned,
stored in an array or another struct's field, ...), and stack-
allocating unconditionally, without proving which case applies first,
silently corrupted every one of those cases (verified: returning a
local struct by value produced garbage at the call site). calloc'ing
every struct is the simple, uniformly-correct choice per #54's
ambiguity rule ("prefer the simplest implementation" / "prefer
performance" only when it doesn't also mean "prefer incorrect").
calloc (not malloc) so uninitialized fields read as zero, matching a
global struct's `zeroinitializer` -- local and global structs now start
identically rather than one being zeroed and the other garbage.

This still always heap-allocates rather than ever choosing a stack
alloca -- claude.md #74 (stage 1 of #43's automatic memory management
promise) takes a different, narrower path to the same underlying goal:
rather than proving a value never escapes and skipping the heap
allocation entirely (which is what "prefer stack allocation" actually
asks for, and remains unimplemented -- the exact same escape-proving
problem, just attempted at allocation time instead of at scope-exit
time, with the exact same risk if done unsoundly), #74 always still
calloc's, then *frees* that same allocation automatically at scope exit
when escape_analysis.py proves the value's address never left its
declaring function/handler. Slower than true stack allocation would be
(a real calloc + a real free, not a zero-cost stack pointer bump) but
implementable as a genuinely separable, individually-verifiable first
step -- see escape_analysis.py's own module docstring and
CodeGen._emit_func_body/_emit_free_active_locals for the actual
mechanism, and claude.md #74 for exactly what is and isn't covered by
this first stage.

Array representation: claude.md #26 specifies arr[T]'s type-resolution
rules but not its runtime representation or push/pop-style operations
-- claude.md #54's ambiguity rule says to treat undefined behavior as
unresolved rather than invent it, so those still aren't implemented.
#63 and #60/#61 (added later than #26) do specify a length accessor and
loop constructs, and both are implemented: `.length` is read via
`extractvalue` on the array's own `{i64, ptr}` value (see
`_emit_expr`'s Member handling) rather than through `_member_ptr`,
since not every array-typed expression is addressable and `.length` is
read-only anyway; for/while loops (`_emit_for`/`_emit_while`) are
ordinary structured control flow, no different in kind from `_emit_if`.
No growth, no bounds checking (documented in todo.md as a known gap,
consistent with #14's performance-first / low-runtime-overhead priority
in the absence of a spec requirement either way). `break`/`continue`
(claude.md #73) target the *nearest* enclosing for/while loop -- see
self._loop_targets' own comment for why that's a plain stack rather than
something threaded through ctx the way `terminated` is.

Every arr[T], regardless of T, lowers to the same fixed-size aggregate
FESTINA_ARRAY_LLVM_TYPE = `%struct._FestinaArray = type { i64, ptr }`
(length, data pointer) -- Festina's own type system (not the generated
IR) is what keeps different arr[T] values from mixing, exactly like
festina.types keeps PrimitiveType/StructType/etc. distinct without a
runtime tag (claude.md #11). Named `_FestinaArray` (leading underscore)
rather than a plainer name specifically to make an accidental collision
with a user-declared `struct _FestinaArray { ... }` less likely --
Festina's identifier grammar still technically allows a user to write
that exact name, so this lowers the odds without eliminating the
possibility; a Festina identifier can never collide with an LLVM name
containing a `.` in the middle the way `struct_llvm_name` produces
(`%struct.Name`), so a scheme that didn't reuse that "%struct." prefix
at all would close the gap completely if it's ever worth the churn. The
data pointer is malloc'd, and (as of claude.md #74) freed automatically
at scope exit when escape_analysis.py proves a local arr[T]/map[T]
never escapes its declaring function/handler -- otherwise (or for a
global, or a value declared inside a nested if/while/for body, or
inside a loop at all -- see #74's own stated stage-1 limitations) it
still leaks exactly as before; see todo.md's "Memory management"
section for the full picture of what's covered and what's still ahead.

Null for int/float (claude.md #10, #25, #57): i64/double have no spare
bit pattern for "null" the way a pointer has NULL, and LLVM's `null`
literal is only valid for pointer types -- storing it into an i64/double
slot is a link error (verified). Represented with a reserved sentinel
instead (INT_NULL_CONST = i64 minimum; FLOAT_NULL_CONST = a quiet NaN),
per #57's "implementation-defined" allowance. This is what
division/modulo by zero produce (#57) and what a literal `null` lowers
to when assigned/passed/returned as int or float (see
_emit_value_for) -- before this change the bare "null" keyword was used
unconditionally, which broke exactly the same way `int x = null` did.
Using an already-null int or float as an operand in further arithmetic
is unresolved per #57 --
NaN naturally propagates through float arithmetic (for free), but
INT_NULL_CONST is just an ordinary (if extreme) i64 to int arithmetic,
so it does not propagate the same way.

Null for bool: `bool` had the identical "null literal for a non-pointer
type" problem (verified: same link error) -- fixed the same way, but
with one extra step int/float didn't need. `bool`'s natural LLVM
representation, i1, has only two possible bit patterns, both already
meaningful (0=false, 1=true) -- there is no spare pattern to reserve the
way i64/double have plenty. So BOOL now lowers to i8 (_llvm_type),
wide enough for a third reserved value (BOOL_NULL_CONST = 2) while still
only ever needing 1 byte of storage, same as i1 would have. This widens
every stored/passed bool value uniformly (variables, struct/table
fields, array/map elements, function params/returns) -- but a
comparison or `&&`/`||`/`!` result is still only ever a genuine LLVM i1
the instant it's produced (icmp/fcmp/xor all require it), so every one
of those gets a `zext i1 ... to i8` immediately before being treated as
a "bool value" the rest of codegen deals with, and _bool_cond does the
inverse (`icmp ne i8 ..., 0`) at the handful of places that need a real
i1 back to actually branch on (if/while/for conditions, ternary,
&&/||'s own short-circuit test, !). The five runtime functions that take
or return a Festina bool (festina_log_bool, festina_str_from_bool,
festina_str_eq, festina_regex_test, festina_audio_is_playing) already
declared their C-side parameter/return as `int8_t`, not `_Bool` or `int`
-- their LLVM `declare`s used to say `i1` anyway (a latent, mostly-
harmless ABI mismatch that happened to work because only 0/1 were ever
produced); moving those declares to `i8` alongside this fix corrects
that too, not just serves the null feature. Using an already-null bool
as a condition, or as an operand to `!`/`&&`/`||`, is unresolved per
#57's same "implementation-defined" allowance int/float's own null
sentinels get -- _bool_cond's own comment covers exactly what happens
(nonzero, so treated as truthy), which is a consistent, documented
choice rather than proper defined behavior.
"""
import struct

from . import ast
from . import types as types_mod
from . import semantic as semantic_mod
from . import escape_analysis
from .errors import CompileError

BOOL = types_mod.PrimitiveType("bool")
INT = types_mod.PrimitiveType("int")
FLOAT = types_mod.PrimitiveType("float")
TEXT = types_mod.PrimitiveType("text")
REGEX = types_mod.RegexType()
AUDIO = types_mod.AudioType()

FESTINA_ARRAY_LLVM_TYPE = "%struct._FestinaArray"

# claude.md #72: map[T] -- same two-field shape as _FestinaArray above
# (i64 count, ptr to the backing storage) but kept as its own distinct
# LLVM type name rather than reusing FESTINA_ARRAY_LLVM_TYPE outright:
# the two are never interchangeable (a map's `ptr` field points at an
# array of FestinaMapEntry {key, value} pairs, not raw element values,
# and only festina_map_set/_get/_for_each know how to read it), so
# giving them separate names catches an accidental mix-up in the IR
# itself rather than relying on convention alone.
FESTINA_MAP_LLVM_TYPE = "%struct._FestinaMap"

# claude.md #57: division/modulo by zero returns null; null has no spare
# bit pattern in a plain i64/double, so it's a reserved sentinel instead
# (see the module docstring's "Null for int/float" note).
INT_NULL_CONST = "-9223372036854775808"  # i64 minimum
FLOAT_NULL_CONST = "0x7FF8000000000000"  # a quiet NaN, as a raw double bit pattern
# See the module docstring's "Null for bool" note: bool widened from i1
# to i8 specifically to make room for this -- 2 is neither 0 (false) nor
# 1 (true), and (unlike int/float's sentinels) is already a valid literal
# for both an i8 and an i64 context as plain text, so no separate
# "_AS_I64" variant is needed the way FLOAT_NULL_CONST_AS_I64 exists below.
BOOL_NULL_CONST = "2"
# claude.md #72: the exact same quiet-NaN bit pattern as FLOAT_NULL_CONST
# above, spelled as a plain decimal integer instead of LLVM's hex-float
# literal syntax -- needed because festina_map_get's "value to return if
# the key is missing" parameter is always i64 (map values of every
# element type flow through this one generically-typed runtime function
# -- see _emit_map_get), and LLVM's `0x...` literal form is *only* ever
# parsed as a floating-point bit pattern, never as a hex integer
# constant (verified directly: `add i64 0x7FF8000000000000, 0` fails to
# parse) -- so the i64 context needs this same bit pattern spelled out
# in decimal instead. 9221120237041090560 == 0x7FF8000000000000, keeping
# both constants in sync is this comment's job since nothing enforces it
# structurally.
FLOAT_NULL_CONST_AS_I64 = "9221120237041090560"
MATH_INTRINSICS = {
    "floor": "llvm.floor.f64", "ceil": "llvm.ceil.f64",
    "round": "llvm.round.f64", "trunc": "llvm.trunc.f64",
}


class CodegenError(CompileError):
    def __init__(self, message, **kw):
        kw.setdefault("category", "not implemented")
        super().__init__(message, **kw)


def _llvm_type(t):
    if isinstance(t, types_mod.PrimitiveType):
        # "bool": "i8", not "i1" -- see the module docstring's "Null for
        # bool" note (BOOL_NULL_CONST needs a third bit pattern i1 can't
        # provide). A genuine i1 (from icmp/fcmp/xor) is still used
        # transiently wherever LLVM itself requires one -- see
        # _bool_cond and every zext-to-i8-immediately-after site.
        return {"int": "i64", "float": "double", "bool": "i8",
                "text": "ptr", "blob": "ptr"}[t.name]
    if isinstance(t, types_mod.StructType):
        return "ptr"
    if isinstance(t, types_mod.ArrayType):
        return FESTINA_ARRAY_LLVM_TYPE
    if isinstance(t, types_mod.TableType):
        # claude.md #32-34: a table-typed value is one row from a query
        # result -- like StructType, it's always a pointer to the row's
        # own storage (here, the flat byte-slot buffer
        # festina_sqlite_collect_rows allocates; see _member_ptr's
        # TableType branch), never an inline aggregate.
        return "ptr"
    if isinstance(t, types_mod.ImageType):
        # claude.md #37: a loaded image is an opaque Cairo surface
        # pointer -- see _emit_call's loadImage/drawImage handling.
        return "ptr"
    if isinstance(t, types_mod.AudioType):
        # claude.md #38: a loaded clip is an opaque FestinaAudio pointer
        # (decoded PCM samples + playback state) -- see _emit_call's
        # loadAudio/.play()/.stop()/.isPlaying() handling.
        return "ptr"
    if isinstance(t, types_mod.RegexType):
        # claude.md #67: a compiled regex_t*, opaque to codegen -- see
        # _emit_regex_call.
        return "ptr"
    if isinstance(t, types_mod.MapType):
        return FESTINA_MAP_LLVM_TYPE
    raise CodegenError(f"cannot generate code for type {t!r}")


class Env:
    """Mirrors festina.semantic.Scope, but maps names to (llvm_ref, Type)
    pairs instead of Symbols."""

    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent

    def define(self, name, ref, type_):
        self.vars[name] = (ref, type_)

    def lookup(self, name):
        env = self
        while env is not None:
            if name in env.vars:
                return env.vars[name]
            env = env.parent
        return None


class CodeGen:
    def __init__(self, analyzed, filename="main.f"):
        self.analyzed = analyzed
        self.entry_filename = filename         # the file actually passed to the compiler -- see generate()
        self.filename = filename               # mutated per top-level statement (see generate()); used by every error site
        self.structs = analyzed.structs       # name -> {field: Type}
        self.struct_order = list(analyzed.structs.keys())
        self.tables = analyzed.tables          # name -> {field: festina-type-name}
        self.string_constants = {}             # text -> global name
        self.tmp_counter = 0
        self.label_counter = 0
        self.global_env = Env()
        self.func_defs = []                    # emitted `define` blocks (text)
        self.extra_globals = []                # globals discovered while emitting main() (e.g. table column arrays)
        self.entry_stmts = []                  # top-level statements for __festina_main
        self.func_decls = {}                   # name -> ast.FuncDecl (for signatures)
        self.cur_block = None                  # label of the block currently being emitted into
        self.uses_sqlite = False               # any sqlite() call anywhere -- see _emit_sqlite_call
        self._table_arrays_cache = {}          # table name -> (names_global, types_global, ncols);
                                                # schema sync and query codegen can both ask for the
                                                # same table's column-name/type globals, and emitting
                                                # them twice would redefine the same LLVM global names
        self.uses_graphics = False             # any draw* call or an `on click`/`on mouse` handler
                                                # anywhere -- NOT loadImage() alone; see
                                                # _emit_graphics_call and _emit_main_and_entry
        self.event_handlers = {}               # "click"/"mouse" -> @__festina_on_<name> -- see
                                                # _emit_event_handler and _emit_main_and_entry
        self.uses_timers = False               # any setTimeout()/setInterval() call anywhere --
                                                # NOT clearTimeout()/clearInterval() alone; see
                                                # _emit_timer_call and _emit_main_and_entry
        self.uses_graphics_code = False        # any drawRect/drawCircle/drawText/drawImage/
                                                # loadImage call anywhere -- a strict superset of
                                                # uses_graphics (see _emit_graphics_call's doc
                                                # comment on why loadImage() alone does NOT set
                                                # uses_graphics), purely a linking signal like
                                                # uses_audio below: cli.py links the graphics
                                                # (Cairo/X11) runtime object file whenever this is
                                                # true, whether or not a window ever actually opens
        self.uses_audio = False                # any loadAudio()/.play()/.stop()/.isPlaying()
                                                # anywhere -- purely a linking signal (unlike
                                                # uses_graphics/uses_timers, nothing in codegen
                                                # branches on it): cli.py reads it after generate()
                                                # to decide whether the ALSA-linked runtime object
                                                # file needs to be linked in at all, so a program
                                                # that never touches audio doesn't pull in
                                                # libasound (see cli.py's _ensure_runtime_objects)
        self.database_url_expr = None          # claude.md #70: DatabaseURL's value expression, or
                                                # None -- set from program.database_url in generate()
                                                # (festina.imports.build_program already extracted and
                                                # validated its *position*; see _emit_main_and_entry
                                                # for where this actually gets evaluated)
        self._loop_targets = []                # stack of (continue_label, break_label) for the
                                                # innermost currently-being-emitted for/while loop --
                                                # claude.md #73: break/continue always target the
                                                # *nearest* enclosing loop, so this is a plain stack,
                                                # pushed/popped around each loop body's own emission
                                                # (_emit_while/_emit_for), not threaded through ctx --
                                                # it needs to keep working unchanged through arbitrarily
                                                # nested if/block statements inside that body, which is
                                                # exactly what a shared instance-level stack gives for
                                                # free. semantic.py has already rejected a break/continue
                                                # outside any loop by the time codegen runs, so this
                                                # being empty here would only ever fire on a compiler bug
                                                # -- see _emit_stmt's own defensive check.
        self._active_free_locals = []          # claude.md #74: stack of "frames," one per currently-
                                                # being-emitted function/event handler body (see
                                                # _emit_func_body) -- each frame is a list of (storage
                                                # ref, Type) for every non-escaping struct/arr[T]/map[T]
                                                # local declared so far, on every path that's reached
                                                # this point in program order. Appended to as
                                                # _emit_func_body's own top-level statement loop reaches
                                                # each qualifying VarDecl; consulted by
                                                # _emit_free_active_locals at every Return (however
                                                # deeply nested inside if/while/for) and at a function's
                                                # own implicit fall-off-the-end exit. Empty outside any
                                                # tracked function/handler body (e.g. __festina_main's
                                                # own top-level statements, which claude.md #74 doesn't
                                                # analyze at all) -- _emit_free_active_locals is a no-op
                                                # then. Same "instance-level stack, not threaded through
                                                # ctx" shape as _loop_targets above, for the same reason:
                                                # it needs to keep working through arbitrary if/block
                                                # nesting inside the tracked body.
        self._regex_lit_cache = {}             # id(ast.RegexLit node) -> its private cache global's
                                                # name -- see _emit_cached_regex_lit; keyed by node
                                                # identity (not pattern text) so two textually
                                                # identical /pattern/flags literals at different
                                                # source locations still each get their own slot,
                                                # simpler than deduplicating by text and just as
                                                # correct (each still only ever compiles once)

    # ---- naming ----
    def tmp(self):
        self.tmp_counter += 1
        return f"%t{self.tmp_counter}"

    def label(self, prefix):
        self.label_counter += 1
        return f"{prefix}{self.label_counter}"

    def _start_block(self, label, lines):
        """Open a new basic block and record it as the current one, so
        branch-merging code (ternary/&&/||) can find the *actual* last
        block of an arm -- which may differ from the label the arm
        started in in if that arm itself contains nested control flow."""
        lines.append(f"{label}:")
        self.cur_block = label

    def string_const(self, text):
        if text in self.string_constants:
            return self.string_constants[text]
        name = f"@.str.{len(self.string_constants)}"
        self.string_constants[text] = name
        return name

    # ---- struct layout ----
    def struct_llvm_name(self, name):
        return f"%struct.{name}"

    def struct_fields(self, name):
        """Ordered [(field_name, Type)] for a declared struct."""
        return list(self.structs[name].items())

    def struct_field_index(self, struct_name, field_name):
        for i, (fname, _) in enumerate(self.struct_fields(struct_name)):
            if fname == field_name:
                return i
        raise CodegenError(f"struct '{struct_name}' has no field '{field_name}'")

    # ---- table layout (claude.md #32-34) ----
    def table_fields(self, name):
        """Ordered [(field_name, Type)] for a declared table, mirroring
        struct_fields -- self.tables stores raw type-expr strings (see
        semantic.analyze_table), not resolved Type objects, so each
        lookup resolves on demand."""
        return [(fname, self._resolve(traw, None)) for fname, traw in self.tables[name].items()]

    def table_field_index(self, table_name, field_name):
        for i, (fname, _) in enumerate(self.table_fields(table_name)):
            if fname == field_name:
                return i
        raise CodegenError(f"table '{table_name}' has no field '{field_name}'")

    # ---- entry point ----
    def generate(self, program):
        self.database_url_expr = getattr(program, "database_url", None)
        for stmt in program.body:
            # claude.md #6: a multi-file program (festina.imports.
            # build_program) is one merged ast.Program, but errors
            # should still point at whichever source file a statement
            # actually came from. self.filename is read fresh at every
            # error site (never cached into a local), so mutating it
            # here -- right before processing each top-level statement --
            # is enough to correctly attribute everything that
            # statement's codegen touches, however deeply nested (a
            # function body, its own nested blocks, ...), until the next
            # top-level statement (possibly from a different file)
            # updates it again. A single-file program tags every
            # statement with that one file (see build_program), so this
            # is a no-op behavior change for today's single-file callers.
            self.filename = getattr(stmt, "file", self.filename)
            self._toplevel(stmt)
        # Emitted first so any table-column globals it discovers land in
        # self.extra_globals before that list is read below.
        entry_and_main = self._emit_main_and_entry()
        module = []
        module.append('; ModuleID = "festina"')
        module.append(f'; generated from {self.entry_filename} -- claude.md #47')
        module.append("")
        module.extend(self._runtime_declares())
        module.append("")
        # claude.md #29, #32-34: one process-wide database handle, opened
        # once in main() (see _emit_main_and_entry) and read by every
        # sqlite() call site, wherever in the program it appears --
        # unconditionally emitted (harmless if unreferenced) rather than
        # gated on self.tables/self.uses_sqlite, so nothing else has to
        # track whether this global exists.
        module.append("@__festina_db = global ptr null")
        module.append("")
        module.extend(self._struct_type_defs())
        module.append("")
        module.extend(self._global_var_defs())
        module.append("")
        module.extend(self.extra_globals)
        module.append("")
        module.extend(self.func_defs)
        module.append("")
        module.extend(entry_and_main)
        module.append("")
        module.extend(self._string_const_defs())
        return "\n".join(module) + "\n"

    def _runtime_declares(self):
        return [
            "declare void @festina_log_int(i64)",
            "declare void @festina_log_float(double)",
            # i8, not i1 -- see the module docstring's "Null for bool"
            # note. Every one of the i8-typed bool declares below matches
            # this runtime function's own C signature exactly (int8_t,
            # not _Bool/int) -- these used to say i1 despite that, a
            # latent ABI mismatch that happened to work only because 0/1
            # were the only values ever produced.
            "declare void @festina_log_bool(i8)",
            "declare void @festina_log_text(ptr)",
            "declare void @festina_fail(ptr)",
            "declare ptr @festina_str_from_int(i64)",
            "declare ptr @festina_str_from_float(double)",
            "declare ptr @festina_str_from_bool(i8)",
            "declare ptr @festina_str_concat(ptr, ptr)",
            "declare i8 @festina_str_eq(ptr, ptr)",
            # claude.md #70: DatabaseURL -- path is festina.sqlite's
            # location, NULL/empty meaning "use the default" (a plain
            # string constant already covers the no-directive case, so
            # this is only ever actually NULL if a DatabaseURL expression
            # itself somehow evaluates to a null text value at runtime).
            "declare ptr @festina_db_open(ptr)",
            "declare void @festina_sync_table(ptr, ptr, ptr, ptr, i32)",
            # claude.md #32-34: sqlite() queries.
            "declare ptr @festina_sqlite_prepare(ptr, ptr)",
            "declare void @festina_sqlite_bind_int(ptr, i32, i64)",
            "declare void @festina_sqlite_bind_float(ptr, i32, double)",
            "declare void @festina_sqlite_bind_text(ptr, i32, ptr)",
            "declare void @festina_sqlite_bind_null(ptr, i32)",
            "declare void @festina_sqlite_exec(ptr)",
            "declare void @festina_sqlite_collect_rows(ptr, i32, ptr, ptr, ptr)",
            # claude.md #67-68: regex(), .test(), .match(), .replace()/.replaceAll().
            "declare ptr @festina_regex_compile(ptr, ptr)",
            "declare i8 @festina_regex_test(ptr, ptr)",
            "declare ptr @festina_regex_match(ptr, ptr)",
            "declare ptr @festina_str_replace(ptr, ptr, ptr, i8)",
            "declare ptr @festina_regex_replace(ptr, ptr, ptr, i8)",
            # claude.md #37, #39, #40: img, graphics functions, click/mouse events.
            "declare void @festina_graphics_init()",
            "declare void @festina_run_event_loop()",
            "declare void @festina_draw_rect(i64, i64, i64, i64)",
            "declare void @festina_draw_circle(i64, i64, i64)",
            "declare void @festina_draw_text(ptr, i64, i64)",
            "declare ptr @festina_load_image(ptr)",
            "declare void @festina_draw_image(ptr, i64, i64)",
            "declare void @festina_register_click_handler(ptr)",
            "declare void @festina_register_mouse_handler(ptr)",
            "declare void @festina_register_key_handler(ptr)",
            "declare void @festina_register_resize_handler(ptr)",
            "declare void @festina_register_close_handler(ptr)",
            "declare i64 @festina_client_width()",
            "declare i64 @festina_client_height()",
            # claude.md #69: setTimeout/setInterval/clearTimeout/clearInterval
            # -- see the module docstring's "Timers" note.
            "declare i64 @festina_set_timeout(ptr, i64)",
            "declare i64 @festina_set_interval(ptr, i64)",
            "declare void @festina_clear_timeout(i64)",
            "declare void @festina_clear_interval(i64)",
            # Timers-without-graphics blocking loop -- pure POSIX (no
            # X11), lives in the core runtime; see _emit_main_and_entry.
            # A `declare` alone never forces linking anything (only an
            # actual `call` does), so this is safe to emit unconditionally
            # for every program, same as festina_run_event_loop above.
            "declare void @festina_run_timer_loop()",
            # claude.md #38: aud, loadAudio(), .play()/.stop()/.isPlaying().
            "declare ptr @festina_load_audio(ptr)",
            "declare void @festina_audio_play(ptr)",
            "declare void @festina_audio_stop(ptr)",
            "declare i8 @festina_audio_is_playing(ptr)",
            "declare ptr @malloc(i64)",
            "declare ptr @calloc(i64, i64)",
            # claude.md #74: automatic reclamation of provably non-
            # escaping struct/arr[T]/map[T] locals -- see
            # _emit_free_active_locals.
            "declare void @free(ptr)",
            # claude.md #71: environment.NAME / environment[keyExpr].
            "declare ptr @festina_getenv(ptr)",
            # claude.md #72: map[T] -- count_ptr/entries_ptr (the two
            # fields of a FESTINA_MAP_LLVM_TYPE value's storage slot,
            # always passed by address so festina_map_set can grow the
            # backing array in place) are always `ptr`/`ptr` regardless
            # of T; the value itself is always passed/returned as a raw
            # i64 (every map value type's bit pattern fits in 8 bytes --
            # see types.MapType's doc comment -- codegen reinterprets
            # to/from the real LLVM type at each call site, see
            # _map_value_to_i64/_i64_to_map_value).
            "declare void @festina_map_set(ptr, ptr, ptr, i64)",
            "declare i64 @festina_map_get(i64, ptr, ptr, i64)",
            "declare void @festina_map_for_each(i64, ptr, ptr)",
            # claude.md #56: Math.floor/ceil/round/trunc, via LLVM's
            # built-in intrinsics rather than a runtime C function.
            "declare double @llvm.floor.f64(double)",
            "declare double @llvm.ceil.f64(double)",
            "declare double @llvm.round.f64(double)",
            "declare double @llvm.trunc.f64(double)",
        ]

    def _struct_type_defs(self):
        # claude.md #26: every arr[T] -- regardless of T -- lowers to the
        # same fixed-size {length, data} header; see the module docstring.
        # claude.md #72: every map[T] -- regardless of T -- lowers to the
        # identical-shaped {count, entries} header, for the same reason
        # (see FESTINA_MAP_LLVM_TYPE's own comment on why it's still a
        # distinct name rather than reusing _FestinaArray outright).
        lines = [
            f"{FESTINA_ARRAY_LLVM_TYPE} = type {{ i64, ptr }}",
            f"{FESTINA_MAP_LLVM_TYPE} = type {{ i64, ptr }}",
        ]
        for name in self.struct_order:
            fields = self.struct_fields(name)
            field_types = ", ".join(_llvm_type(t) for _, t in fields)
            lines.append(f"{self.struct_llvm_name(name)} = type {{ {field_types} }}")
        return lines

    def _global_var_defs(self):
        lines = []
        for name, (ref, type_) in self.global_env.vars.items():
            if name in self.func_decls:
                continue
            if isinstance(type_, types_mod.StructType):
                # `ref` (@name) holds a *pointer* to the struct's actual
                # storage, exactly like a local struct var's alloca slot
                # (see _emit_stmt) -- kept uniform so Identifier lookup
                # never needs to special-case structs.
                backing = f"{ref}.storage"
                lines.append(f"{backing} = global {self.struct_llvm_name(type_.name)} zeroinitializer")
                lines.append(f"{ref} = global ptr {backing}")
                continue
            llvm_ty = _llvm_type(type_)
            zero = self._zero_value(type_)
            lines.append(f"{ref} = global {llvm_ty} {zero}")
        return lines

    def _zero_value(self, type_):
        llvm_ty = _llvm_type(type_)
        if llvm_ty in ("i64", "i1", "i8"):
            return "0"
        if llvm_ty == "double":
            return "0.0"
        if llvm_ty.startswith("%struct."):
            # Named aggregate types (currently just FESTINA_ARRAY_LLVM_TYPE
            # reaches this branch -- struct-typed globals are handled
            # separately in _global_var_defs) can't use "null"; a plain
            # "ptr null" only works for actual pointer types.
            return "zeroinitializer"
        return "null"

    def _string_const_defs(self):
        lines = []
        for text, name in self.string_constants.items():
            encoded, length = _encode_c_string(text)
            lines.append(f'{name} = private unnamed_addr constant [{length} x i8] c"{encoded}"')
        return lines

    # ---- top-level declarations ----
    def _toplevel(self, stmt):
        if isinstance(stmt, ast.ImportDecl):
            return  # claude.md #6: import resolution happens before this stage
        if isinstance(stmt, ast.StructDecl):
            return  # already reflected in self.structs (from semantic analysis)
        if isinstance(stmt, ast.TableDecl):
            return  # already reflected in self.tables; schema sync emitted in main()
        if isinstance(stmt, ast.FuncDecl):
            self._emit_func(stmt)
            return
        if isinstance(stmt, ast.EventHandler):
            self._emit_event_handler(stmt)
            return
        if isinstance(stmt, ast.VarDecl):
            type_ = self._resolve(stmt.type_expr, stmt)
            ref = f"@{stmt.name}"
            self.global_env.define(stmt.name, ref, type_)
            self.entry_stmts.append(stmt)
            return
        # claude.md #7: any other executable top-level statement goes into
        # the generated entry function.
        self.entry_stmts.append(stmt)

    def _resolve(self, type_expr, node):
        return semantic_mod.resolve_type_name(
            type_expr, self.structs, self.tables, self.filename, node)

    # ---- functions ----

    def _emit_func_body(self, block, parent_env, return_type, lines):
        """Like _emit_block, but ONLY for a function/event handler's own
        top-level body -- never for a nested if/while/for body, which
        still goes through the ordinary _emit_block unchanged (claude.md
        #74 doesn't analyze those yet; see that section's own stated
        limitations).

        Runs escape_analysis.find_escaping_names once for this body,
        then walks its own top-level statements in program order,
        maintaining self._active_free_locals so any Return anywhere in
        the rest of this body -- however deeply nested inside if/while/
        for -- frees exactly the non-escaping locals active at that
        point, and never one declared later on a different path: a
        candidate only gets added to the active set once this loop
        actually reaches and finishes emitting its own VarDecl, so a
        Return that textually precedes a later candidate's declaration
        (necessarily nested inside an earlier if/while/for, since a
        *top-level* Return before it would make the declaration dead
        code -- unreachable, and never emitted at all) correctly never
        tries to free something that was never allocated on that path.
        """
        scope = Env(parent_env)
        escaping = escape_analysis.find_escaping_names(block)
        ctx = {"lines": lines, "terminated": False}
        self._active_free_locals.append([])
        try:
            for stmt in block.body:
                if ctx["terminated"]:
                    break
                self._emit_stmt(stmt, scope, return_type, ctx)
                if isinstance(stmt, ast.VarDecl) and stmt.name not in escaping:
                    found = scope.lookup(stmt.name)
                    if found is not None:
                        ref, type_ = found
                        if isinstance(type_, (types_mod.StructType, types_mod.ArrayType, types_mod.MapType)):
                            self._active_free_locals[-1].append((ref, type_))
            if not ctx["terminated"]:
                self._emit_free_active_locals(lines)
        finally:
            self._active_free_locals.pop()
        return ctx

    def _emit_free_active_locals(self, lines):
        """claude.md #74: frees every currently-active non-escaping
        local (self._active_free_locals[-1]) -- called from _emit_stmt's
        Return handling (after the return value, if any, has already
        been computed -- see that call site's own comment on why the
        order matters) and once more from _emit_func_body itself for a
        function/handler that falls off its own end without an explicit
        return. A no-op outside any tracked function/handler body
        (self._active_free_locals empty -- e.g. __festina_main's own
        top-level statements, which claude.md #74 doesn't analyze at
        all).

        What "free" means differs by type, since only structs are
        represented as a pointer to their own backing storage -- arr[T]/
        map[T] locals are the {i64, ptr} header itself, stack-allocated
        inline (never heap-allocated on their own), with only their
        data/entries *field* separately heap-allocated (see
        FESTINA_ARRAY_LLVM_TYPE/FESTINA_MAP_LLVM_TYPE's own module
        docstring notes) -- so freeing one of those means loading the
        header value and freeing its second field, not the local itself.
        """
        if not self._active_free_locals:
            return
        for ref, type_ in self._active_free_locals[-1]:
            if isinstance(type_, types_mod.StructType):
                loaded = self.tmp()
                lines.append(f"  {loaded} = load ptr, ptr {ref}")
                lines.append(f"  call void @free(ptr {loaded})")
            elif isinstance(type_, types_mod.ArrayType):
                loaded = self.tmp()
                lines.append(f"  {loaded} = load {FESTINA_ARRAY_LLVM_TYPE}, ptr {ref}")
                data_ptr = self.tmp()
                lines.append(f"  {data_ptr} = extractvalue {FESTINA_ARRAY_LLVM_TYPE} {loaded}, 1")
                lines.append(f"  call void @free(ptr {data_ptr})")
            elif isinstance(type_, types_mod.MapType):
                loaded = self.tmp()
                lines.append(f"  {loaded} = load {FESTINA_MAP_LLVM_TYPE}, ptr {ref}")
                entries_ptr = self.tmp()
                lines.append(f"  {entries_ptr} = extractvalue {FESTINA_MAP_LLVM_TYPE} {loaded}, 1")
                lines.append(f"  call void @free(ptr {entries_ptr})")

    def _emit_func(self, decl):
        return_type = None if decl.return_type == "void" else self._resolve(decl.return_type, decl)
        self.func_decls[decl.name] = decl
        self.global_env.define(decl.name, f"@{decl.name}", return_type)

        param_types = [self._resolve(p.type_expr, decl) for p in decl.params]
        llvm_ret = "void" if return_type is None else _llvm_type(return_type)
        params_ir = ", ".join(f"{_llvm_type(t)} %arg.{p.name}" for t, p in zip(param_types, decl.params))

        body_env = Env(self.global_env)
        body_lines = []
        entry_label = self.label("entry")
        self._start_block(entry_label, body_lines)
        for t, p in zip(param_types, decl.params):
            slot = f"%{p.name}"
            body_lines.append(f"  {slot} = alloca {_llvm_type(t)}")
            body_lines.append(f"  store {_llvm_type(t)} %arg.{p.name}, ptr {slot}")
            body_env.define(p.name, slot, t)

        block = self._emit_func_body(decl.body, body_env, return_type, body_lines)
        if not block["terminated"]:
            # claude.md never says whether a non-void function must
            # return a value on every code path (unlike the
            # void-vs-non-void distinction #23 itself draws, which
            # analyze_statement's Return handling in semantic.py does
            # enforce) -- #54's ambiguity rule treats a genuinely
            # undetermined case like this as an implementation-defined
            # choice, not something to invent a new restriction for.
            # The choice made here: falling off the end of a non-void
            # function's body returns that type's zero value
            # (_zero_value below) rather than being a compile error --
            # deterministic and never crashes, the same "prefer the
            # simplest implementation" reasoning #54 itself asks for.
            if return_type is None:
                block["lines"].append("  ret void")
            else:
                block["lines"].append(f"  ret {_llvm_type(return_type)} {self._zero_value(return_type)}")

        func = [f"define {llvm_ret} @{decl.name}({params_ir}) {{"]
        func.extend(block["lines"])
        func.append("}")
        self.func_defs.extend(func)
        self.func_defs.append("")

    def _emit_event_handler(self, decl):
        """claude.md #40: `on eventName(...) { }`. Compiles to a real
        void function (@__festina_on_<name>, not registered as an
        ordinary callable -- an event handler is a listener, not
        something Festina code calls by name) exactly like _emit_func,
        minus a return type (event handlers never return a value).
        click/mouse/key/resize/close additionally get registered with
        the runtime as a function pointer (see festina_runtime.h's doc
        comment on festina_register_click_handler/_mouse_handler/
        _key_handler/_resize_handler/_close_handler) -- the only event
        sources this runtime actually generates (claude.md #40's own
        examples; semantic.py's _EVENT_SIGNATURES enforces the fixed
        signature each one needs, matching the runtime's fixed function
        pointer type for it). Any other declared name still compiles (so
        a typo/bug in its body is still caught) but is simply dead code:
        nothing ever calls it."""
        symbol = f"@__festina_on_{decl.name}"
        param_types = [self._resolve(p.type_expr, decl) for p in decl.params]
        params_ir = ", ".join(f"{_llvm_type(t)} %arg.{p.name}" for t, p in zip(param_types, decl.params))

        body_env = Env(self.global_env)
        body_lines = []
        entry_label = self.label("entry")
        self._start_block(entry_label, body_lines)
        for t, p in zip(param_types, decl.params):
            slot = f"%{p.name}"
            body_lines.append(f"  {slot} = alloca {_llvm_type(t)}")
            body_lines.append(f"  store {_llvm_type(t)} %arg.{p.name}, ptr {slot}")
            body_env.define(p.name, slot, t)

        block = self._emit_func_body(decl.body, body_env, None, body_lines)
        if not block["terminated"]:
            block["lines"].append("  ret void")

        func = [f"define void {symbol}({params_ir}) {{"]
        func.extend(block["lines"])
        func.append("}")
        self.func_defs.extend(func)
        self.func_defs.append("")

        if decl.name in ("click", "mouse", "key", "resize", "close"):
            self.uses_graphics = True
            self.event_handlers[decl.name] = symbol

    # ---- statements ----
    def _emit_block(self, block, parent_env, return_type, lines):
        env = Env(parent_env)
        ctx = {"lines": lines, "terminated": False}
        for stmt in block.body:
            if ctx["terminated"]:
                break
            self._emit_stmt(stmt, env, return_type, ctx)
        return ctx

    def _emit_stmt(self, stmt, env, return_type, ctx):
        lines = ctx["lines"]
        if isinstance(stmt, ast.VarDecl):
            type_ = self._resolve(stmt.type_expr, stmt)
            if isinstance(type_, types_mod.StructType):
                # `slot` holds a *pointer* to the struct's own storage,
                # kept uniform with every other type so Identifier lookup
                # never needs a struct special-case. That storage is
                # calloc'd, not a stack alloca -- see the module
                # docstring's "Struct storage is always heap-allocated"
                # note for why (a stack-allocated struct's address can
                # outlive its function: returned, put in an array, stored
                # in another struct's field -- verified to silently
                # corrupt memory when it does).
                uid = self._unique()
                struct_ty = self.struct_llvm_name(type_.name)
                size_val = self._sizeof(struct_ty, lines)
                backing = f"%{stmt.name}.storage.{uid}"
                slot = f"%{stmt.name}.{uid}"
                lines.append(f"  {backing} = call ptr @calloc(i64 1, i64 {size_val})")
                lines.append(f"  {slot} = alloca ptr")
                lines.append(f"  store ptr {backing}, ptr {slot}")
                env.define(stmt.name, slot, type_)
                # No struct-literal initializer syntax exists yet, so
                # stmt.init is always None here.
                return
            llvm_ty = _llvm_type(type_)
            slot = f"%{stmt.name}.{self._unique()}"
            lines.append(f"  {slot} = alloca {llvm_ty}")
            env.define(stmt.name, slot, type_)
            if stmt.init is not None:
                val, vtype = self._emit_value_for(stmt.init, env, lines, type_)
                val = self._coerce(val, vtype, type_, lines)
                lines.append(f"  store {llvm_ty} {val}, ptr {slot}")
            return
        if isinstance(stmt, ast.ExprStmt):
            self._emit_expr(stmt.expr, env, lines)
            return
        if isinstance(stmt, ast.Return):
            # claude.md #74: free every currently-active non-escaping
            # local -- AFTER the return value (if any) has already been
            # computed, so anything that value's evaluation reads
            # through one of those locals' own fields (safe under
            # claude.md #74's own rule, since that's not an escaping use)
            # sees it while it's still alive, and BEFORE the `ret`
            # itself, so nothing after this point in the function can
            # observe the now-freed memory.
            if stmt.value is None or return_type is None:
                if stmt.value is not None:
                    self._emit_expr(stmt.value, env, lines)  # side effects only
                self._emit_free_active_locals(lines)
                lines.append("  ret void")
            else:
                val, vtype = self._emit_value_for(stmt.value, env, lines, return_type)
                val = self._coerce(val, vtype, return_type, lines)
                self._emit_free_active_locals(lines)
                lines.append(f"  ret {_llvm_type(return_type)} {val}")
            ctx["terminated"] = True
            return
        if isinstance(stmt, ast.IfStmt):
            self._emit_if(stmt, env, return_type, ctx)
            return
        if isinstance(stmt, ast.WhileStmt):
            self._emit_while(stmt, env, return_type, ctx)
            return
        if isinstance(stmt, ast.ForStmt):
            self._emit_for(stmt, env, return_type, ctx)
            return
        if isinstance(stmt, ast.BreakStmt):
            # semantic.py already rejects this outside any loop, so an
            # empty stack here would mean a compiler bug, not bad source.
            if not self._loop_targets:
                raise CodegenError("'break' outside a loop", file=self.filename,
                                    line=stmt.line, column=stmt.column)
            _, break_label = self._loop_targets[-1]
            lines.append(f"  br label %{break_label}")
            ctx["terminated"] = True
            return
        if isinstance(stmt, ast.ContinueStmt):
            if not self._loop_targets:
                raise CodegenError("'continue' outside a loop", file=self.filename,
                                    line=stmt.line, column=stmt.column)
            # for a `for` loop this is the update block, not the
            # condition block directly -- claude.md #60's step order
            # still runs the update expression before the next check,
            # exactly like a normal iteration would; see _emit_for.
            continue_label, _ = self._loop_targets[-1]
            lines.append(f"  br label %{continue_label}")
            ctx["terminated"] = True
            return
        if isinstance(stmt, ast.Block):
            inner = self._emit_block(stmt, env, return_type, lines)
            ctx["terminated"] = inner["terminated"]
            return
        raise CodegenError(f"cannot generate code for statement {type(stmt).__name__}",
                            file=self.filename, line=getattr(stmt, "line", 0),
                            column=getattr(stmt, "column", 0))

    def _emit_if(self, stmt, env, return_type, ctx):
        lines = ctx["lines"]
        cond_val, _ = self._emit_expr(stmt.test, env, lines)
        cond = self._bool_cond(cond_val, lines)
        then_label = self.label("if.then")
        else_label = self.label("if.else")
        end_label = self.label("if.end")
        lines.append(f"  br i1 {cond}, label %{then_label}, label %{else_label}")

        self._start_block(then_label, lines)
        then_ctx = self._emit_block(stmt.then, env, return_type, lines)
        if not then_ctx["terminated"]:
            lines.append(f"  br label %{end_label}")

        self._start_block(else_label, lines)
        else_terminated = False
        if stmt.orelse is not None:
            if isinstance(stmt.orelse, ast.IfStmt):
                else_ctx = {"lines": lines, "terminated": False}
                self._emit_stmt(stmt.orelse, env, return_type, else_ctx)
                else_terminated = else_ctx["terminated"]
            else:
                else_ctx = self._emit_block(stmt.orelse, env, return_type, lines)
                else_terminated = else_ctx["terminated"]
        if not else_terminated:
            lines.append(f"  br label %{end_label}")

        if then_ctx["terminated"] and else_terminated:
            ctx["terminated"] = True
        else:
            self._start_block(end_label, lines)

    def _emit_while(self, stmt, env, return_type, ctx):
        # claude.md #61. Never sets ctx["terminated"] even if the body
        # always returns -- the condition can be false on the very first
        # check, so control can always fall through to while.end,
        # regardless of what happens inside the body.
        lines = ctx["lines"]
        cond_label = self.label("while.cond")
        body_label = self.label("while.body")
        end_label = self.label("while.end")

        lines.append(f"  br label %{cond_label}")
        self._start_block(cond_label, lines)
        cond_val, _ = self._emit_expr(stmt.test, env, lines)
        cond = self._bool_cond(cond_val, lines)
        lines.append(f"  br i1 {cond}, label %{body_label}, label %{end_label}")

        self._start_block(body_label, lines)
        # claude.md #73: continue re-checks the condition directly (no
        # update expression for a while loop); break exits past end_label.
        self._loop_targets.append((cond_label, end_label))
        try:
            body_ctx = self._emit_block(stmt.body, env, return_type, lines)
        finally:
            self._loop_targets.pop()
        if not body_ctx["terminated"]:
            lines.append(f"  br label %{cond_label}")

        self._start_block(end_label, lines)

    def _emit_for(self, stmt, env, return_type, ctx):
        # claude.md #60. `loop_env` holds just the init variable, scoped
        # to this statement only (see the semantic.py note this mirrors)
        # -- _emit_block below nests the body under loop_env, not env, so
        # the variable is visible in test/update/body but env itself
        # (and anything emitted after this statement) never sees it.
        lines = ctx["lines"]
        loop_env = Env(env)
        init_ctx = {"lines": lines, "terminated": False}
        self._emit_stmt(stmt.init, loop_env, return_type, init_ctx)

        cond_label = self.label("for.cond")
        body_label = self.label("for.body")
        update_label = self.label("for.update")
        end_label = self.label("for.end")

        lines.append(f"  br label %{cond_label}")
        self._start_block(cond_label, lines)
        cond_val, _ = self._emit_expr(stmt.test, loop_env, lines)
        cond = self._bool_cond(cond_val, lines)
        lines.append(f"  br i1 {cond}, label %{body_label}, label %{end_label}")

        self._start_block(body_label, lines)
        # claude.md #73: continue for a `for` loop still runs the update
        # expression before the next condition check -- routing it to
        # update_label (not cond_label directly) gives that for free,
        # same as a normal fall-through iteration.
        self._loop_targets.append((update_label, end_label))
        try:
            body_ctx = self._emit_block(stmt.body, loop_env, return_type, lines)
        finally:
            self._loop_targets.pop()
        if not body_ctx["terminated"]:
            lines.append(f"  br label %{update_label}")

        self._start_block(update_label, lines)
        self._emit_expr(stmt.update, loop_env, lines)
        lines.append(f"  br label %{cond_label}")

        self._start_block(end_label, lines)

    _uid = 0

    def _unique(self):
        CodeGen._uid += 1
        return CodeGen._uid

    # ---- expressions ----
    def _coerce(self, val, from_type, to_type, lines):
        # claude.md #55: int and float never convert implicitly, not even
        # on assignment -- semantic.py already rejects a mismatched
        # int/float assignment before codegen ever runs, so there is no
        # remaining case that needs a numeric promotion here. What's left
        # is genuinely permissive by design: a null literal (from_type is
        # None or NULL-ish) or an unconstrained builtin return (e.g.
        # sqlite()) flowing into a concretely-typed slot.
        return val

    def _bool_cond(self, val, lines):
        """Narrows an already-emitted BOOL value (i8 -- see the module
        docstring's "Null for bool" note) down to a genuine i1, the only
        type LLVM's `br` ever accepts. Used at every place a Festina
        condition actually becomes a branch: if/while/for, ternary,
        &&/||'s own short-circuit test, and `!`. Treats the reserved null
        sentinel (BOOL_NULL_CONST, nonzero) as truthy -- using an
        already-null bool as a condition is unresolved, same allowance
        claude.md #57 already gives int/float's own null sentinels in
        further arithmetic; this is a documented, consistent choice, not
        a claim that it's meaningful."""
        out = self.tmp()
        lines.append(f"  {out} = icmp ne i8 {val}, 0")
        return out

    def _emit_expr(self, expr, env, lines):
        if isinstance(expr, ast.NumberLit):
            if isinstance(expr.value, float):
                return _format_double(expr.value), FLOAT
            return str(expr.value), INT
        if isinstance(expr, ast.BoolLit):
            return ("1" if expr.value else "0"), BOOL
        if isinstance(expr, ast.StringLit):
            return self._const_string(expr.value, lines), TEXT
        if isinstance(expr, ast.NullLit):
            # No declared-type context here (see _emit_value_for for the
            # version that has one) -- "null" is only valid IR for a
            # pointer type, which covers every Festina type reachable
            # without context (text/blob/struct/array all lower to `ptr`
            # or a pointer-holding aggregate). int/float/bool can't reach
            # this path uniformly assigned/coerced (see _emit_value_for).
            return "null", None
        if isinstance(expr, ast.RegexLit):
            # claude.md #67: /pattern/flags. Both arguments are known
            # string constants (fixed at parse time, unlike regex()'s
            # general call-argument case -- see _emit_regex_call), so
            # unlike a dynamic regex() call this literal's compiled
            # result never needs recomputing after the first time this
            # exact node is reached; see _emit_cached_regex_lit.
            out = self._emit_cached_regex_lit(expr, lines)
            return out, REGEX
        if isinstance(expr, ast.TemplateLit):
            return self._emit_template(expr, env, lines), TEXT
        if isinstance(expr, ast.Identifier):
            # claude.md #39: clientWidth/clientHeight -- a bare
            # identifier, not a Call, so this can't go through the usual
            # builtin-function dispatch; read the canvas's *current*
            # size from the runtime (not a compile-time constant, since
            # `on resize` can change it after startup -- see
            # festina_client_width/_height's own doc comment) and set
            # uses_graphics exactly like a draw* call does, since asking
            # for the window's size implies a window exists.
            if expr.name in ("clientWidth", "clientHeight"):
                self.uses_graphics = True
                fn = "festina_client_width" if expr.name == "clientWidth" else "festina_client_height"
                out = self.tmp()
                lines.append(f"  {out} = call i64 @{fn}()")
                return out, INT
            if expr.name in self.func_decls:
                raise CodegenError("functions are not first-class values yet "
                                    f"(found bare reference to '{expr.name}')",
                                    file=self.filename, line=expr.line, column=expr.column)
            found = env.lookup(expr.name)
            if found is None:
                raise CodegenError(f"unknown variable '{expr.name}'",
                                    file=self.filename, line=expr.line, column=expr.column)
            ref, type_ = found
            # Every env slot -- scalar, struct, or function -- uniformly
            # holds a value of _llvm_type(type_) at `ref`; for structs
            # that value is itself a pointer to the struct's storage
            # (see the VarDecl/global handling below), so a plain load
            # here is correct for every case, not just scalars.
            out = self.tmp()
            lines.append(f"  {out} = load {_llvm_type(type_)}, ptr {ref}")
            return out, type_
        if isinstance(expr, ast.Member):
            # claude.md #71: environment.NAME / environment[keyExpr] --
            # checked structurally (an Identifier literally named
            # "environment"), before ever touching expr.obj as a real
            # expression -- there's no storage/value behind that
            # identifier to emit (see semantic.py's matching check).
            if isinstance(expr.obj, ast.Identifier) and expr.obj.name == "environment":
                return self._emit_environment_get(expr, env, lines)
            if not expr.computed and expr.prop == "length":
                # claude.md #63: unlike struct/table field access,
                # .length isn't addressable via a GEP -- an arr[T] value
                # is a plain {i64, ptr} aggregate *value* (not a pointer
                # to one; see the module docstring), and not every
                # array-typed expression is even an lvalue (e.g. a
                # function call's return value). extractvalue on the
                # object's value works uniformly regardless, and .length
                # is read-only anyway (see semantic.py), so there's never
                # a need to go through _member_ptr for it.
                obj_val, obj_type = self._emit_expr(expr.obj, env, lines)
                if isinstance(obj_type, types_mod.ArrayType):
                    out = self.tmp()
                    lines.append(f"  {out} = extractvalue {FESTINA_ARRAY_LLVM_TYPE} {obj_val}, 0")
                    return out, INT
            if expr.computed:
                # claude.md #26/#72: arr[i] / map[key] -- expr.obj is
                # emitted exactly once here, then branched on by type,
                # rather than delegating to _member_ptr (which would
                # emit it again from scratch) -- correct not just
                # efficient, since expr.obj could be an arbitrary
                # expression with side effects (e.g. a function call
                # returning an array or map).
                obj_val, obj_type = self._emit_expr(expr.obj, env, lines)
                if isinstance(obj_type, types_mod.MapType):
                    key_val, _ = self._emit_expr(expr.prop, env, lines)
                    return self._emit_map_get(obj_val, obj_type.value, key_val, lines)
                if not isinstance(obj_type, types_mod.ArrayType):
                    raise CodegenError(f"cannot index into {types_mod.type_name(obj_type)}",
                                        file=self.filename, line=getattr(expr, "line", 0))
                idx_val, _ = self._emit_expr(expr.prop, env, lines)
                ptr, elem_type = self._array_elem_ptr(obj_val, obj_type, idx_val, lines)
                out = self.tmp()
                lines.append(f"  {out} = load {_llvm_type(elem_type)}, ptr {ptr}")
                return out, elem_type
            return self._emit_member_load(expr, env, lines)
        if isinstance(expr, ast.ArrayLit):
            # No contextual element type here -- reached only when an
            # array literal appears somewhere _emit_value_for's callers
            # don't thread a declared type through (e.g. nested inside
            # another expression). Falls back to the elements' own type.
            return self._emit_array_lit(expr, env, lines, expected_type=None)
        if isinstance(expr, ast.MapLit):
            # Same reasoning as ArrayLit just above -- no contextual
            # value type here, falls back to the entries' own values.
            return self._emit_map_lit(expr, env, lines, expected_type=None)
        if isinstance(expr, ast.Assign):
            return self._emit_assign(expr, env, lines)
        if isinstance(expr, ast.Ternary):
            return self._emit_ternary(expr, env, lines)
        if isinstance(expr, ast.LogicalOp):
            return self._emit_logical(expr, env, lines)
        if isinstance(expr, ast.BinOp):
            return self._emit_binop(expr, env, lines)
        if isinstance(expr, ast.UnaryOp):
            return self._emit_unary(expr, env, lines)
        if isinstance(expr, ast.PostfixOp):
            return self._emit_postfix(expr, env, lines)
        if isinstance(expr, ast.Call):
            return self._emit_call(expr, env, lines, expected_type=None)
        raise CodegenError(f"cannot generate code for expression {type(expr).__name__}",
                            file=self.filename, line=getattr(expr, "line", 0),
                            column=getattr(expr, "column", 0))

    def _const_string(self, text, lines):
        name = self.string_const(text)
        return name

    def _emit_template(self, expr, env, lines):
        result = self._const_string(expr.parts[0], lines)
        for part_expr, next_part in zip(expr.exprs, expr.parts[1:]):
            val, vtype = self._emit_expr(part_expr, env, lines)
            piece = self._to_text(val, vtype, lines)
            out = self.tmp()
            lines.append(f"  {out} = call ptr @festina_str_concat(ptr {result}, ptr {piece})")
            result = out
            part_str = self._const_string(next_part, lines)
            out2 = self.tmp()
            lines.append(f"  {out2} = call ptr @festina_str_concat(ptr {result}, ptr {part_str})")
            result = out2
        return result

    def _to_text(self, val, type_, lines):
        if type_ == TEXT:
            return val
        out = self.tmp()
        if type_ == INT:
            lines.append(f"  {out} = call ptr @festina_str_from_int(i64 {val})")
        elif type_ == FLOAT:
            lines.append(f"  {out} = call ptr @festina_str_from_float(double {val})")
        elif type_ == BOOL:
            lines.append(f"  {out} = call ptr @festina_str_from_bool(i8 {val})")
        else:
            raise CodegenError(f"cannot interpolate a value of type {types_mod.type_name(type_)}")
        return out

    def _emit_value_for(self, node, env, lines, expected_type):
        """Like _emit_expr, but for positions where the *declared* type is
        already known (a var's declared type, a param's type, a function's
        return type) -- lets an array literal pick its element type from
        context instead of guessing from its own elements, and lets a
        bare `null` literal pick the right runtime encoding (claude.md
        #10/#25/#57): "null" the LLVM keyword for text/blob/struct/array
        (all pointer-backed), but the reserved sentinel constants for
        int/float/bool, none of which have a spare bit pattern for a
        real null (see the module docstring)."""
        if isinstance(node, ast.ArrayLit):
            return self._emit_array_lit(node, env, lines, expected_type)
        if isinstance(node, ast.MapLit):
            return self._emit_map_lit(node, env, lines, expected_type)
        if isinstance(node, ast.NullLit):
            if expected_type == INT:
                return INT_NULL_CONST, INT
            if expected_type == FLOAT:
                return FLOAT_NULL_CONST, FLOAT
            if expected_type == BOOL:
                return BOOL_NULL_CONST, BOOL
            return "null", expected_type
        if isinstance(node, ast.Call):
            # sqlite()'s codegen needs to know the *declared* target type
            # to tell a `SELECT` captured into arr[Table] apart from a
            # statement whose result is discarded (INSERT/UPDATE/DELETE,
            # or a SELECT nobody captures) -- see _emit_sqlite_call.
            return self._emit_call(node, env, lines, expected_type)
        return self._emit_expr(node, env, lines)

    def _sizeof(self, llvm_ty, lines):
        """sizeof(llvm_ty) as a runtime i64, via the standard
        getelementptr-on-null trick -- avoids reimplementing LLVM's
        struct layout/alignment rules in Python."""
        ptr_val = self.tmp()
        lines.append(f"  {ptr_val} = getelementptr {llvm_ty}, ptr null, i64 1")
        size_val = self.tmp()
        lines.append(f"  {size_val} = ptrtoint ptr {ptr_val} to i64")
        return size_val

    def _emit_array_lit(self, expr, env, lines, expected_type=None):
        # claude.md #26: "Arrays may contain supported primitive types,
        # structs, tables, and other array types" -- table elements are
        # rejected by _llvm_type(TableType) below, since there's no way
        # to construct a Table-typed value without sqlite() queries yet.
        expected_elem = expected_type.element if isinstance(expected_type, types_mod.ArrayType) else None

        values = []
        elem_type = expected_elem
        for e in expr.elements:
            if isinstance(e, ast.ArrayLit) and isinstance(expected_elem, types_mod.ArrayType):
                val, vtype = self._emit_array_lit(e, env, lines, expected_elem)
            else:
                val, vtype = self._emit_value_for(e, env, lines, expected_elem)
            if expected_elem is not None:
                val = self._coerce(val, vtype, expected_elem, lines)
                vtype = expected_elem
            values.append(val)
            elem_type = elem_type or vtype

        if elem_type is None:
            raise CodegenError(
                "cannot infer the element type of an empty array literal without a declared type",
                file=self.filename, line=getattr(expr, "line", 0),
            )
        elem_llvm_ty = _llvm_type(elem_type)
        n = len(values)

        header = f"%arr.hdr.{self._unique()}"
        lines.append(f"  {header} = alloca {FESTINA_ARRAY_LLVM_TYPE}")
        len_ptr = self.tmp()
        lines.append(f"  {len_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}, i32 0, i32 0")
        lines.append(f"  store i64 {n}, ptr {len_ptr}")

        data_ptr = self.tmp()
        if n == 0:
            lines.append(f"  {data_ptr} = call ptr @malloc(i64 0)")
        else:
            elem_size = self._sizeof(elem_llvm_ty, lines)
            total_size = self.tmp()
            lines.append(f"  {total_size} = mul i64 {elem_size}, {n}")
            lines.append(f"  {data_ptr} = call ptr @malloc(i64 {total_size})")
            for i, val in enumerate(values):
                elem_ptr = self.tmp()
                lines.append(f"  {elem_ptr} = getelementptr {elem_llvm_ty}, ptr {data_ptr}, i64 {i}")
                lines.append(f"  store {elem_llvm_ty} {val}, ptr {elem_ptr}")

        data_field_ptr = self.tmp()
        lines.append(f"  {data_field_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}, i32 0, i32 1")
        lines.append(f"  store ptr {data_ptr}, ptr {data_field_ptr}")

        out = self.tmp()
        lines.append(f"  {out} = load {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}")
        return out, types_mod.ArrayType(elem_type)

    def _emit_map_lit(self, expr, env, lines, expected_type=None):
        """claude.md #72: { key: value, ... } -- built the same way
        _emit_array_lit builds an array literal: a temporary header
        alloca (needed here, unlike a fixed-size array, because
        festina_map_set has to mutate count/entries in place as each
        entry is added -- see _emit_map_set), one festina_map_set call
        per entry in source order (so a repeated key naturally ends up
        "last one wins", with no separate dedup pass needed), then a
        single `load` of the finished {count, entries} value out of that
        header at the end -- same "build in a scratch slot, load the
        final value once" shape _emit_array_lit already uses."""
        expected_value = expected_type.value if isinstance(expected_type, types_mod.MapType) else None

        header = f"%map.hdr.{self._unique()}"
        lines.append(f"  {header} = alloca {FESTINA_MAP_LLVM_TYPE}")
        count_ptr = self.tmp()
        lines.append(f"  {count_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {header}, i32 0, i32 0")
        lines.append(f"  store i64 0, ptr {count_ptr}")
        entries_field_ptr = self.tmp()
        lines.append(f"  {entries_field_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {header}, i32 0, i32 1")
        lines.append(f"  store ptr null, ptr {entries_field_ptr}")

        value_type = expected_value
        for key_expr, val_expr in expr.entries:
            # Keys are always text (claude.md #72; semantic.py already
            # rejected anything else) -- _emit_value_for still routes a
            # bare `null` key through NullLit's own text-context handling
            # correctly (a plain LLVM `null` pointer, which
            # festina_map_set/_get and festina_str_eq all already treat
            # as an empty string, same as everywhere else text does).
            key_val, _ = self._emit_value_for(key_expr, env, lines, TEXT)
            val_val, vtype = self._emit_value_for(val_expr, env, lines, expected_value)
            if expected_value is not None:
                val_val = self._coerce(val_val, vtype, expected_value, lines)
                vtype = expected_value
            value_type = value_type or vtype
            self._emit_map_set(header, value_type, key_val, val_val, lines)

        if value_type is None:
            raise CodegenError(
                "cannot infer the value type of an empty map literal without a declared type",
                file=self.filename, line=getattr(expr, "line", 0),
            )

        out = self.tmp()
        lines.append(f"  {out} = load {FESTINA_MAP_LLVM_TYPE}, ptr {header}")
        return out, types_mod.MapType(value_type)

    def _map_value_to_i64(self, val, value_type, lines):
        """Reinterprets an already-emitted value of the given map value
        type into the raw i64 every map runtime function deals in
        generically, regardless of T (see _runtime_declares's own
        comment on festina_map_set/_get/_for_each) -- the inverse of
        _i64_to_map_value."""
        llvm_ty = _llvm_type(value_type)
        if llvm_ty == "i64":
            return val
        out = self.tmp()
        if llvm_ty == "double":
            lines.append(f"  {out} = bitcast double {val} to i64")
        elif llvm_ty == "i8":
            lines.append(f"  {out} = zext i8 {val} to i64")
        else:  # "ptr" -- text/blob/struct/table/img/aud/regex all lower to ptr
            lines.append(f"  {out} = ptrtoint ptr {val} to i64")
        return out

    def _i64_to_map_value(self, raw, value_type, lines):
        """The inverse of _map_value_to_i64 -- reinterprets a raw i64
        (festina_map_get's return value, or a map .forEach() callback
        trampoline's own raw parameter -- see _emit_map_foreach_trampoline)
        back into the given map value type's real LLVM representation."""
        llvm_ty = _llvm_type(value_type)
        if llvm_ty == "i64":
            return raw
        out = self.tmp()
        if llvm_ty == "double":
            lines.append(f"  {out} = bitcast i64 {raw} to double")
        elif llvm_ty == "i8":
            lines.append(f"  {out} = trunc i64 {raw} to i8")
        else:  # "ptr"
            lines.append(f"  {out} = inttoptr i64 {raw} to ptr")
        return out

    def _map_missing_default(self, value_type):
        """claude.md #72: "if the key is not present, the result is
        null" -- the i64 handed to festina_map_get as the value to
        return outright when the key isn't found, computed per Festina
        type here (compile time), the same as every other "which null
        representation" decision in this codebase (see the module
        docstring's "Null for int/float" note) -- festina_map_get itself
        has no idea what T a given map's values are (it only ever sees
        raw i64 payloads), so it can't make this choice on its own."""
        llvm_ty = _llvm_type(value_type)
        if llvm_ty == "i64":
            return INT_NULL_CONST
        if llvm_ty == "double":
            return FLOAT_NULL_CONST_AS_I64
        if llvm_ty == "i8":
            # BOOL_NULL_CONST ("2") is already a valid i64-context literal
            # as-is -- see its own comment for why no separate "_AS_I64"
            # variant is needed the way FLOAT_NULL_CONST_AS_I64 exists.
            return BOOL_NULL_CONST
        # "ptr" -- NULL is already 0, exactly Festina's own null-for-text/
        # blob/struct/etc. the same way it is everywhere else.
        return "0"

    def _emit_map_get(self, obj_val, value_type, key_val, lines):
        """claude.md #72: npcHealths['npc1'] -- count/entries are pulled
        directly out of the already-emitted map VALUE (extractvalue,
        exactly like array indexing's own data-pointer field -- no
        addressability needed for a read, unlike a write; see
        _emit_map_set)."""
        count = self.tmp()
        lines.append(f"  {count} = extractvalue {FESTINA_MAP_LLVM_TYPE} {obj_val}, 0")
        entries = self.tmp()
        lines.append(f"  {entries} = extractvalue {FESTINA_MAP_LLVM_TYPE} {obj_val}, 1")
        default = self._map_missing_default(value_type)
        raw = self.tmp()
        lines.append(f"  {raw} = call i64 @festina_map_get(i64 {count}, ptr {entries}, ptr {key_val}, i64 {default})")
        return self._i64_to_map_value(raw, value_type, lines), value_type

    def _emit_map_set(self, map_ptr, value_type, key_val, value_val, lines):
        """claude.md #72: npcHealths['npc1'] = 30 (and the equivalent
        per-entry calls a map literal builds itself out of -- see
        _emit_map_lit). Unlike a read, this needs the map's own
        ADDRESS (`map_ptr`, a `ptr` to its {count, entries} storage
        slot -- from a variable's own alloca/global, a struct field's
        GEP, or (during literal construction) the literal's own scratch
        header alloca), not just its value, since festina_map_set can
        grow the backing entries array and has to write the new
        count/entries back into that same slot for the change to
        actually stick."""
        count_ptr = self.tmp()
        lines.append(f"  {count_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {map_ptr}, i32 0, i32 0")
        entries_ptr = self.tmp()
        lines.append(f"  {entries_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {map_ptr}, i32 0, i32 1")
        raw_val = self._map_value_to_i64(value_val, value_type, lines)
        lines.append(f"  call void @festina_map_set(ptr {count_ptr}, ptr {entries_ptr}, ptr {key_val}, i64 {raw_val})")

    def _emit_environment_get(self, expr, env, lines):
        """claude.md #71: environment.NAME / environment[keyExpr]. NAME
        (dot access) is a compile-time-known string constant; keyExpr
        (bracket access) is an arbitrary already-type-checked text
        expression -- either way this ends up calling festina_getenv
        with a `ptr` to the name text, returning NULL (Festina's own
        null-for-text, unchanged) if that variable isn't set."""
        if expr.computed:
            key_val, _ = self._emit_expr(expr.prop, env, lines)
        else:
            key_val = self.string_const(expr.prop)
        out = self.tmp()
        lines.append(f"  {out} = call ptr @festina_getenv(ptr {key_val})")
        return out, TEXT

    def _emit_member_load(self, expr, env, lines):
        ptr, ftype = self._member_ptr(expr, env, lines)
        out = self.tmp()
        lines.append(f"  {out} = load {_llvm_type(ftype)}, ptr {ptr}")
        return out, ftype

    def _array_elem_ptr(self, obj_val, obj_type, idx_val, lines):
        """Given an already-emitted arr[T] value, its ArrayType, and an
        already-emitted int index value, returns (ptr, element_type) --
        a pointer to that element's storage slot. Shared by _emit_expr's
        computed-Member read dispatch and _emit_assign's write dispatch
        so obj_val/idx_val are each the caller's own single emission,
        never re-emitted here -- see _emit_expr's own comment on why an
        object expression might not be safe to emit twice."""
        data_ptr = self.tmp()
        lines.append(f"  {data_ptr} = extractvalue {FESTINA_ARRAY_LLVM_TYPE} {obj_val}, 1")
        elem_type = obj_type.element
        elem_llvm_ty = _llvm_type(elem_type)
        out = self.tmp()
        lines.append(f"  {out} = getelementptr {elem_llvm_ty}, ptr {data_ptr}, i64 {idx_val}")
        return out, elem_type

    def _try_addressable(self, expr, env, lines):
        """Resolves `expr` to (address, value, type) for _emit_assign's
        computed-Member target dispatch (map[key] = v / arr[i] = v),
        which needs an actual ADDRESS for a map target (festina_map_set
        mutates count/entries in place -- see _emit_map_set) but only
        ever needs a VALUE for an array target (indexing works directly
        off the {length, data} value via extractvalue, the data pointer
        it holds is already its own independent heap allocation -- see
        _array_elem_ptr). Exactly one of address/value is non-None:
        address, for the same two forms _member_ptr's struct/table field
        access already knows how to address (a plain variable, or a
        non-computed field of one); value, for anything else (a
        function call, another computed index, ...) -- which also means
        a map[key] = v target on anything other than a plain variable or
        field is a compile error (see _emit_assign), since there's no
        way to mutate a map value that doesn't live anywhere addressable.
        `expr` is emitted at most once regardless of which case applies."""
        if isinstance(expr, ast.Identifier):
            found = env.lookup(expr.name)
            if found is None:
                raise CodegenError(f"unknown variable '{expr.name}'",
                                    file=self.filename, line=getattr(expr, "line", 0))
            ref, ttype = found
            return ref, None, ttype
        if isinstance(expr, ast.Member) and not expr.computed:
            ptr, ftype = self._member_ptr(expr, env, lines)
            return ptr, None, ftype
        val, vtype = self._emit_expr(expr, env, lines)
        return None, val, vtype

    def _member_ptr(self, expr, env, lines):
        # Struct/table field access only -- computed (array/map
        # indexing) member access never reaches this function anymore;
        # it's handled directly in _emit_expr (reads) and _emit_assign
        # (writes, via _try_addressable/_array_elem_ptr above), each
        # needing expr.obj emitted exactly once before branching on its
        # type (map vs array), which delegating to this function
        # (re-emitting expr.obj from scratch every time it's called)
        # can't guarantee.
        obj_val, obj_type = self._emit_expr(expr.obj, env, lines)
        if isinstance(obj_type, types_mod.TableType):
            # claude.md #32-34: a table-typed value is one query result
            # row -- flat `field_index * 8` byte offset, not a named
            # struct GEP; see the module docstring's "Query rows" note.
            idx = self.table_field_index(obj_type.name, expr.prop)
            ftype = self.table_fields(obj_type.name)[idx][1]
            out = self.tmp()
            lines.append(f"  {out} = getelementptr i8, ptr {obj_val}, i64 {idx * 8}")
            return out, ftype
        if not isinstance(obj_type, types_mod.StructType):
            raise CodegenError(f"cannot access field '{expr.prop}' on {types_mod.type_name(obj_type)}",
                                file=self.filename, line=expr.line, column=expr.column)
        idx = self.struct_field_index(obj_type.name, expr.prop)
        ftype = self.struct_fields(obj_type.name)[idx][1]
        out = self.tmp()
        struct_ty = self.struct_llvm_name(obj_type.name)
        lines.append(f"  {out} = getelementptr {struct_ty}, ptr {obj_val}, i32 0, i32 {idx}")
        return out, ftype

    def _emit_assign(self, expr, env, lines):
        # The target's declared type is resolved *before* the value, so an
        # array-literal RHS (e.g. `nums = [4, 5, 6]`) can pick its element
        # type from the target instead of guessing from its own elements.
        if isinstance(expr.target, ast.Identifier):
            found = env.lookup(expr.target.name)
            if found is None:
                raise CodegenError(f"unknown variable '{expr.target.name}'",
                                    file=self.filename, line=expr.target.line)
            ref, ttype = found
            val, vtype = self._emit_value_for(expr.value, env, lines, ttype)
            val = self._coerce(val, vtype, ttype, lines)
            lines.append(f"  store {_llvm_type(ttype)} {val}, ptr {ref}")
            return val, ttype
        if isinstance(expr.target, ast.Member):
            if expr.target.computed:
                # claude.md #72: npcHealths['npc1'] = 30 / npcHealths[key] = 30
                addr, val_of_obj, obj_type = self._try_addressable(expr.target.obj, env, lines)
                if isinstance(obj_type, types_mod.MapType):
                    if addr is None:
                        raise CodegenError(
                            "a map assignment target must be a plain variable or field, "
                            "not an arbitrary expression",
                            file=self.filename, line=getattr(expr.target, "line", 0))
                    key_val, _ = self._emit_expr(expr.target.prop, env, lines)
                    val, vtype = self._emit_value_for(expr.value, env, lines, obj_type.value)
                    val = self._coerce(val, vtype, obj_type.value, lines)
                    self._emit_map_set(addr, obj_type.value, key_val, val, lines)
                    return val, obj_type.value
                # claude.md #26: arr[i] = v -- unlike a map target, an
                # array only ever needs its VALUE (see _try_addressable's
                # own comment), so this works whether expr.target.obj was
                # addressable or not.
                if not isinstance(obj_type, types_mod.ArrayType):
                    raise CodegenError(f"cannot index into {types_mod.type_name(obj_type)}",
                                        file=self.filename, line=getattr(expr.target, "line", 0))
                if addr is not None:
                    obj_val = self.tmp()
                    lines.append(f"  {obj_val} = load {FESTINA_ARRAY_LLVM_TYPE}, ptr {addr}")
                else:
                    obj_val = val_of_obj
                idx_val, _ = self._emit_expr(expr.target.prop, env, lines)
                ptr, ftype = self._array_elem_ptr(obj_val, obj_type, idx_val, lines)
            else:
                ptr, ftype = self._member_ptr(expr.target, env, lines)
            val, vtype = self._emit_value_for(expr.value, env, lines, ftype)
            val = self._coerce(val, vtype, ftype, lines)
            lines.append(f"  store {_llvm_type(ftype)} {val}, ptr {ptr}")
            return val, ftype
        raise CodegenError("unsupported assignment target", file=self.filename)

    def _emit_ternary(self, expr, env, lines):
        cond_val, _ = self._emit_expr(expr.test, env, lines)
        cond = self._bool_cond(cond_val, lines)
        then_label = self.label("tern.then")
        else_label = self.label("tern.else")
        end_label = self.label("tern.end")
        lines.append(f"  br i1 {cond}, label %{then_label}, label %{else_label}")

        self._start_block(then_label, lines)
        cons_val, cons_type = self._emit_expr(expr.cons, env, lines)
        then_pred = self.cur_block  # may differ from then_label if expr.cons had its own branches
        lines.append(f"  br label %{end_label}")

        self._start_block(else_label, lines)
        alt_val, _ = self._emit_expr(expr.alt, env, lines)
        else_pred = self.cur_block
        lines.append(f"  br label %{end_label}")

        self._start_block(end_label, lines)
        out = self.tmp()
        llvm_ty = _llvm_type(cons_type)
        lines.append(f"  {out} = phi {llvm_ty} [ {cons_val}, %{then_pred} ], [ {alt_val}, %{else_pred} ]")
        return out, cons_type

    def _emit_logical(self, expr, env, lines):
        left_val, _ = self._emit_expr(expr.left, env, lines)  # i8 BOOL value
        left_cond = self._bool_cond(left_val, lines)
        rhs_label = self.label("logic.rhs")
        end_label = self.label("logic.end")
        start_label = self.label("logic.start")
        # `start_label` exists purely so the short-circuit edge into
        # end_label always originates from a block we control, even if
        # evaluating expr.left itself opened (and left us inside) other
        # blocks -- that only affects where left_val was *computed*, not
        # where this edge originates.
        lines.append(f"  br label %{start_label}")
        self._start_block(start_label, lines)
        if expr.op == "&&":
            lines.append(f"  br i1 {left_cond}, label %{rhs_label}, label %{end_label}")
        else:
            lines.append(f"  br i1 {left_cond}, label %{end_label}, label %{rhs_label}")
        self._start_block(rhs_label, lines)
        right_val, _ = self._emit_expr(expr.right, env, lines)  # i8 BOOL value
        rhs_pred = self.cur_block  # may differ from rhs_label if expr.right had its own branches
        lines.append(f"  br label %{end_label}")
        self._start_block(end_label, lines)
        out = self.tmp()
        # phi over the actual i8 BOOL values (left_val/right_val), not
        # left_cond -- the short-circuited result is whichever *value*
        # won, same as JS/&&/|| semantics generally, not a freshly
        # recomputed true/false.
        lines.append(f"  {out} = phi i8 [ {left_val}, %{start_label} ], [ {right_val}, %{rhs_pred} ]")
        return out, BOOL

    def _emit_binop(self, expr, env, lines):
        # A bare `null` literal compared against a concretely-typed int/
        # float/bool operand (e.g. `x == null`) needs that operand's own
        # null sentinel (INT_NULL_CONST/FLOAT_NULL_CONST/BOOL_NULL_CONST),
        # not the generic LLVM `null` keyword _emit_expr's own NullLit
        # branch produces unconditionally -- `null` is only valid IR for
        # a pointer type, so `icmp eq i64 %x, null` is itself invalid IR
        # (verified) regardless of what the *other* operand's value is.
        # Routing the null side through _emit_value_for, with the other
        # (already-emitted) operand's type as context, is exactly the
        # same "does this position have a declared type to pick a null
        # encoding from" pattern _emit_value_for already exists for (var
        # decls, params, returns) -- a binary comparison is just one more
        # such position. Only applies when exactly one side is a literal
        # null; `null == null` has no type context on *either* side to
        # infer from and stays unresolved, same as before this fix (an
        # exceedingly rare, not obviously meaningful expression to begin
        # with -- claude.md #54's ambiguity rule, same as this file's
        # other deliberately-left corner cases).
        if isinstance(expr.right, ast.NullLit) and not isinstance(expr.left, ast.NullLit):
            left_val, left_type = self._emit_expr(expr.left, env, lines)
            right_val, right_type = self._emit_value_for(expr.right, env, lines, left_type)
        elif isinstance(expr.left, ast.NullLit) and not isinstance(expr.right, ast.NullLit):
            right_val, right_type = self._emit_expr(expr.right, env, lines)
            left_val, left_type = self._emit_value_for(expr.left, env, lines, right_type)
        else:
            left_val, left_type = self._emit_expr(expr.left, env, lines)
            right_val, right_type = self._emit_expr(expr.right, env, lines)

        if left_type == TEXT or right_type == TEXT:
            if expr.op in ("==", "!="):
                out = self.tmp()
                # i8, matching festina_str_eq's updated declare -- this
                # is already the final BOOL value, no zext needed, unlike
                # the icmp/fcmp path below (festina_str_eq only ever
                # produces 0 or 1, never BOOL_NULL_CONST).
                lines.append(f"  {out} = call i8 @festina_str_eq(ptr {left_val}, ptr {right_val})")
                if expr.op == "!=":
                    neg = self.tmp()
                    # A plain xor still flips 0<->1 correctly at i8 width
                    # -- out is guaranteed exactly 0 or 1 (see above), so
                    # this needs no icmp/zext round trip the way a
                    # genuine i1 source would.
                    lines.append(f"  {neg} = xor i8 {out}, 1")
                    return neg, BOOL
                return out, BOOL
            if expr.op == "+":
                out = self.tmp()
                lines.append(f"  {out} = call ptr @festina_str_concat(ptr {left_val}, ptr {right_val})")
                return out, TEXT
            raise CodegenError(f"operator '{expr.op}' is not supported on text",
                                file=self.filename, line=expr.line)

        # claude.md #55: int and float never mix directly -- semantic.py
        # already rejected a genuine mismatch before codegen ever runs, so
        # reaching here with different numeric types is a compiler bug,
        # not a user error; this is a consistency check, not a promotion
        # (there's no implicit numeric conversion left in this codegen).
        if left_type in (INT, FLOAT) and right_type in (INT, FLOAT) and left_type != right_type:
            raise CodegenError(
                f"internal error: mismatched numeric operands ({left_type!r}, {right_type!r}) "
                "reached codegen -- semantic analysis should have rejected this",
                file=self.filename, line=expr.line,
            )
        use_float = left_type == FLOAT

        if expr.op in ("/", "%"):
            out = self._emit_divmod(expr.op, left_val, right_val, use_float, lines)
            return out, (FLOAT if use_float else INT)

        arith = {"+": "add", "-": "sub", "*": "mul"}
        farith = {"+": "fadd", "-": "fsub", "*": "fmul"}
        icmp = {"<": "slt", ">": "sgt", "<=": "sle", ">=": "sge", "==": "eq", "!=": "ne"}
        fcmp = {"<": "olt", ">": "ogt", "<=": "ole", ">=": "oge", "==": "oeq", "!=": "one"}

        out = self.tmp()
        if expr.op in arith:
            op = farith[expr.op] if use_float else arith[expr.op]
            ty = "double" if use_float else "i64"
            lines.append(f"  {out} = {op} {ty} {left_val}, {right_val}")
            return out, (FLOAT if use_float else INT)
        if expr.op in icmp:
            # icmp/fcmp always produce a genuine i1 (LLVM has no other
            # option) -- that's an intermediate here, not the final BOOL
            # value, so it gets its own tmp and an immediate zext into
            # `out` (i8) rather than being returned directly.
            cmp_out = self.tmp()
            if use_float:
                lines.append(f"  {cmp_out} = fcmp {fcmp[expr.op]} double {left_val}, {right_val}")
            else:
                ty = "i64" if left_type != BOOL else "i8"
                lines.append(f"  {cmp_out} = icmp {icmp[expr.op]} {ty} {left_val}, {right_val}")
            lines.append(f"  {out} = zext i1 {cmp_out} to i8")
            return out, BOOL
        raise CodegenError(f"unsupported operator '{expr.op}'", file=self.filename, line=expr.line)

    def _emit_divmod(self, op, left_val, right_val, is_float, lines):
        """claude.md #57: division/modulo by zero returns null instead of
        crashing. For int specifically, `sdiv`/`srem` by zero is undefined
        behavior at the hardware level (SIGFPE) -- checking *after*
        computing would be too late, and a `select` would still evaluate
        the trapping instruction unconditionally, so this has to be real
        control flow that skips the division entirely on the zero path."""
        llvm_ty = "double" if is_float else "i64"
        zero_lit = "0.0" if is_float else "0"
        null_const = FLOAT_NULL_CONST if is_float else INT_NULL_CONST
        cmp_instr = "fcmp oeq" if is_float else "icmp eq"

        is_zero = self.tmp()
        lines.append(f"  {is_zero} = {cmp_instr} {llvm_ty} {right_val}, {zero_lit}")

        zero_label = self.label("divzero")
        nonzero_label = self.label("divnonzero")
        end_label = self.label("divend")
        lines.append(f"  br i1 {is_zero}, label %{zero_label}, label %{nonzero_label}")

        self._start_block(zero_label, lines)
        zero_pred = self.cur_block
        lines.append(f"  br label %{end_label}")

        self._start_block(nonzero_label, lines)
        instr = {"float": {"/": "fdiv", "%": "frem"}, "int": {"/": "sdiv", "%": "srem"}}["float" if is_float else "int"][op]
        result = self.tmp()
        lines.append(f"  {result} = {instr} {llvm_ty} {left_val}, {right_val}")
        nonzero_pred = self.cur_block
        lines.append(f"  br label %{end_label}")

        self._start_block(end_label, lines)
        out = self.tmp()
        lines.append(f"  {out} = phi {llvm_ty} [ {null_const}, %{zero_pred} ], [ {result}, %{nonzero_pred} ]")
        return out

    def _emit_unary(self, expr, env, lines):
        val, vtype = self._emit_expr(expr.operand, env, lines)
        out = self.tmp()
        if expr.op == "!":
            # val is i8 (see _llvm_type) -- narrow to a genuine i1 first
            # (same _bool_cond every other condition uses, so `!` treats
            # an already-null bool the same "nonzero is truthy" way a
            # bare `if x` would), negate, then widen the result back to
            # the i8 every BOOL value is represented as.
            cond = self._bool_cond(val, lines)
            negated = self.tmp()
            lines.append(f"  {negated} = xor i1 {cond}, 1")
            lines.append(f"  {out} = zext i1 {negated} to i8")
            return out, BOOL
        if expr.op == "-":
            if vtype == FLOAT:
                lines.append(f"  {out} = fneg double {val}")
            else:
                lines.append(f"  {out} = sub i64 0, {val}")
            return out, vtype
        return val, vtype  # unary '+' is a no-op

    def _emit_postfix(self, expr, env, lines):
        # claude.md #66: postfix ++/-- -- semantic.py has already
        # verified expr.operand is a mutable int Identifier by the time
        # codegen ever sees this node. Returns the *pre*-increment value,
        # standard postfix semantics, even though every current caller
        # (ExprStmt, a for-loop's update clause) discards the result.
        ref, _ = env.lookup(expr.operand.name)
        old = self.tmp()
        lines.append(f"  {old} = load i64, ptr {ref}")
        new = self.tmp()
        op = "add" if expr.op == "++" else "sub"
        lines.append(f"  {new} = {op} i64 {old}, 1")
        lines.append(f"  store i64 {new}, ptr {ref}")
        return old, INT

    def _emit_call(self, expr, env, lines, expected_type=None):
        callee = expr.callee
        if isinstance(callee, ast.Identifier):
            name = callee.name
            if name == "log":
                val, vtype = self._emit_expr(expr.args[0], env, lines)
                if not isinstance(vtype, types_mod.PrimitiveType):
                    raise CodegenError(
                        f"log() only supports primitive values right now, "
                        f"found {types_mod.type_name(vtype)}",
                        file=self.filename, line=callee.line)
                # claude.md #10 lists blob among the five primitive
                # types with no exception carved out for log() (#41);
                # blob shares text's exact `ptr`-to-bytes representation
                # (see _llvm_type), so festina_log_text handles it too --
                # this used to be a bare KeyError (blob passed the
                # PrimitiveType check above but had no dict entry) since
                # nothing could actually construct a blob value before
                # check_assignable allowed text -> blob assignment.
                fn = {"int": "festina_log_int", "float": "festina_log_float",
                      "bool": "festina_log_bool", "text": "festina_log_text",
                      "blob": "festina_log_text"}[vtype.name]
                ty = _llvm_type(vtype)
                lines.append(f"  call void @{fn}({ty} {val})")
                return "0", None
            if name == "fail":
                val, vtype = self._emit_expr(expr.args[0], env, lines)
                text_val = self._to_text(val, vtype, lines)
                lines.append(f"  call void @festina_fail(ptr {text_val})")
                return "0", None
            if name == "sqlite":
                return self._emit_sqlite_call(expr, env, lines, expected_type)
            if name == "regex":
                return self._emit_regex_call(expr, env, lines)
            if name in ("drawRect", "drawCircle", "drawText", "drawImage", "loadImage"):
                return self._emit_graphics_call(name, expr, env, lines)
            if name in ("setTimeout", "setInterval", "clearTimeout", "clearInterval"):
                return self._emit_timer_call(name, expr, env, lines)
            if name == "loadAudio":
                self.uses_audio = True
                path_val, _ = self._emit_expr(expr.args[0], env, lines)
                out = self.tmp()
                lines.append(f"  {out} = call ptr @festina_load_audio(ptr {path_val})")
                return out, AUDIO
            if name in self.func_decls:
                decl = self.func_decls[name]
                arg_vals = []
                for arg_expr, param in zip(expr.args, decl.params):
                    ptype = self._resolve(param.type_expr, decl)
                    val, vtype = self._emit_value_for(arg_expr, env, lines, ptype)
                    val = self._coerce(val, vtype, ptype, lines)
                    arg_vals.append(f"{_llvm_type(ptype)} {val}")
                ret_ref, ret_type = env.lookup(name)
                args_ir = ", ".join(arg_vals)
                if ret_type is None:
                    lines.append(f"  call void @{name}({args_ir})")
                    return "0", None
                out = self.tmp()
                lines.append(f"  {out} = call {_llvm_type(ret_type)} @{name}({args_ir})")
                return out, ret_type
            raise CodegenError(f"unknown function '{name}'", file=self.filename, line=callee.line)
        if isinstance(callee, ast.Member) and not callee.computed:
            # claude.md #56: Math.floor/ceil/round/trunc(x:float) -> int
            if (isinstance(callee.obj, ast.Identifier) and callee.obj.name == "Math"
                    and callee.prop in MATH_INTRINSICS):
                val, vtype = self._emit_expr(expr.args[0], env, lines)
                if vtype != FLOAT:
                    raise CodegenError(
                        f"Math.{callee.prop}() expects a float argument, found {types_mod.type_name(vtype)}",
                        file=self.filename, line=callee.line)
                rounded = self.tmp()
                lines.append(f"  {rounded} = call double @{MATH_INTRINSICS[callee.prop]}(double {val})")
                out = self.tmp()
                lines.append(f"  {out} = fptosi double {rounded} to i64")
                return out, INT
            # claude.md #55: int.toFloat() -> float
            if callee.prop == "toFloat" and not expr.args:
                val, vtype = self._emit_expr(callee.obj, env, lines)
                if vtype == INT:
                    out = self.tmp()
                    lines.append(f"  {out} = sitofp i64 {val} to double")
                    return out, FLOAT
            # int/float/bool.toText() -> text -- an explicit spelling of
            # exactly the stringification template interpolation already
            # does under the hood (_to_text, shared with _emit_template),
            # for a value that needs converting outside of a template
            # (e.g. building a plain text value to pass elsewhere, or a
            # map .forEach() callback formatting its own log line).
            if callee.prop == "toText" and not expr.args:
                val, vtype = self._emit_expr(callee.obj, env, lines)
                if vtype in (INT, FLOAT, BOOL):
                    return self._to_text(val, vtype, lines), TEXT
            # claude.md #67: pattern.test(value:text) -> bool
            if callee.prop == "test":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if obj_type == REGEX:
                    arg_val, _ = self._emit_expr(expr.args[0], env, lines)
                    out = self.tmp()
                    lines.append(f"  {out} = call i8 @festina_regex_test(ptr {obj_val}, ptr {arg_val})")
                    return out, BOOL
            # claude.md #68: value.match(pattern:regex) -> text (or null)
            if callee.prop == "match":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if obj_type == TEXT:
                    arg_val, _ = self._emit_expr(expr.args[0], env, lines)
                    out = self.tmp()
                    lines.append(f"  {out} = call ptr @festina_regex_match(ptr {arg_val}, ptr {obj_val})")
                    return out, TEXT
            # claude.md #68: value.replace(search, replacement:text) -> text
            #                value.replaceAll(search, replacement:text) -> text
            if callee.prop in ("replace", "replaceAll"):
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if obj_type == TEXT:
                    search_val, search_type = self._emit_expr(expr.args[0], env, lines)
                    replacement_val, _ = self._emit_expr(expr.args[1], env, lines)
                    replace_all = "1" if callee.prop == "replaceAll" else "0"
                    out = self.tmp()
                    if search_type == REGEX:
                        lines.append(
                            f"  {out} = call ptr @festina_regex_replace(ptr {search_val}, ptr {obj_val}, "
                            f"ptr {replacement_val}, i8 {replace_all})"
                        )
                    else:
                        # search_type is TEXT, or None from a bare `null`
                        # literal (festina_str_replace treats a NULL
                        # search pointer defensively -- see the runtime).
                        lines.append(
                            f"  {out} = call ptr @festina_str_replace(ptr {obj_val}, ptr {search_val}, "
                            f"ptr {replacement_val}, i8 {replace_all})"
                        )
                    return out, TEXT
            # claude.md #38: music.play() / music.stop() / music.isPlaying()
            if callee.prop == "play":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if obj_type == AUDIO:
                    lines.append(f"  call void @festina_audio_play(ptr {obj_val})")
                    return "0", None
            if callee.prop == "stop":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if obj_type == AUDIO:
                    lines.append(f"  call void @festina_audio_stop(ptr {obj_val})")
                    return "0", None
            if callee.prop == "isPlaying":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if obj_type == AUDIO:
                    out = self.tmp()
                    lines.append(f"  {out} = call i8 @festina_audio_is_playing(ptr {obj_val})")
                    return out, BOOL
            # claude.md #72: npcHealths.forEach(callback)
            if callee.prop == "forEach":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if isinstance(obj_type, types_mod.MapType):
                    # semantic.py already checked expr.args[0] is an
                    # ast.Identifier resolving to a correctly-shaped
                    # declared function -- its own LLVM symbol is just
                    # its Festina name, same convention every other
                    # user-function reference in this file already uses.
                    callback_name = f"@{expr.args[0].name}"
                    trampoline_name = self._emit_map_foreach_trampoline(obj_type.value, callback_name)
                    count = self.tmp()
                    lines.append(f"  {count} = extractvalue {FESTINA_MAP_LLVM_TYPE} {obj_val}, 0")
                    entries = self.tmp()
                    lines.append(f"  {entries} = extractvalue {FESTINA_MAP_LLVM_TYPE} {obj_val}, 1")
                    lines.append(
                        f"  call void @festina_map_for_each(i64 {count}, ptr {entries}, ptr {trampoline_name})")
                    return "0", None
        raise CodegenError("only calls to named functions are implemented",
                            file=self.filename, line=getattr(expr, "line", 0))

    def _emit_map_foreach_trampoline(self, value_type, callback_name):
        """claude.md #72: generates and registers (self.func_defs) a
        small internal function bridging festina_map_for_each's always-
        i64-valued C callback signature (void(int64_t, const char*) --
        see _runtime_declares's own comment) to the user's real forEach()
        callback, whose LLVM-level signature depends on the map's actual
        value type (e.g. `double` for a map[float], not i64). Calling a
        double-taking function through an i64-typed function pointer
        would be a genuine calling-convention mismatch on plenty of
        real ABIs, not just an LLVM type-checker technicality, so this
        always generates a real, correctly-typed trampoline rather than
        relying on the two shapes happening to be compatible -- the same
        "declared types must actually match at every call site" rule
        every other function-pointer registration in this file already
        follows (e.g. festina_register_click_handler's fixed
        `void(i64,i64)` signature, matched exactly by semantic.py's
        _EVENT_SIGNATURES check on `on click`'s own declared params)."""
        uid = self._unique()
        trampoline_name = f"@__festina_maptrampoline_{uid}"
        value_llvm_ty = _llvm_type(value_type)

        body = [f"define void {trampoline_name}(i64 %raw, ptr %key) {{", "entry:"]
        reinterpreted = self._i64_to_map_value("%raw", value_type, body)
        body.append(f"  call void {callback_name}({value_llvm_ty} {reinterpreted}, ptr %key)")
        body.append("  ret void")
        body.append("}")
        self.func_defs.extend(body)
        self.func_defs.append("")
        return trampoline_name

    # ---- regex literal caching (claude.md #67) ----
    def _emit_cached_regex_lit(self, expr, lines):
        """A /pattern/flags literal's pattern and flags are fixed at
        parse time -- the same ast.RegexLit node always compiles to the
        identical regcomp() result every single time it's reached, so
        recompiling it on every execution (the previous behavior) was
        pure wasted runtime work for something entirely knowable ahead
        of time. This does NOT extend to the dynamic regex(pattern,
        flags) builtin (_emit_regex_call below) -- there pattern is an
        arbitrary runtime expression, so the same call site can
        legitimately see a different pattern on each call (e.g.
        regex(userPattern) inside a loop), and caching by AST node there
        would silently keep reusing the FIRST pattern ever seen forever
        -- a real correctness bug, not just a missed optimization -- so
        that path intentionally stays uncached.

        Caches per-AST-node (via self._regex_lit_cache) behind a private
        global `ptr` initialized to null, using the standard lazy-init
        null-check + phi shape _emit_ternary/_emit_logical already use
        for conditional control flow: check the cache, branch to compile
        only if it's still null, store the result, merge. This still
        only compiles the pattern the first time *this* literal is
        actually reached at runtime (a literal inside a branch that's
        never taken, or a function that's never called, still costs
        nothing -- unlike unconditionally precomputing every literal in
        main()'s prologue, which would force eager evaluation regardless
        of whether that code path ever runs), and every later reach of
        the same literal (e.g. inside a loop) is just a load.
        """
        key = id(expr)
        cache_global = self._regex_lit_cache.get(key)
        if cache_global is None:
            cache_global = f"@.regex.cache.{len(self._regex_lit_cache)}"
            self._regex_lit_cache[key] = cache_global
            self.extra_globals.append(f"{cache_global} = private global ptr null")

        loaded = self.tmp()
        lines.append(f"  {loaded} = load ptr, ptr {cache_global}")
        is_null = self.tmp()
        lines.append(f"  {is_null} = icmp eq ptr {loaded}, null")
        compile_label = self.label("regex.compile")
        done_label = self.label("regex.done")
        lines.append(f"  br i1 {is_null}, label %{compile_label}, label %{done_label}")
        load_pred = self.cur_block

        self._start_block(compile_label, lines)
        pattern_val = self._const_string(expr.pattern, lines)
        flags_val = self._const_string(expr.flags, lines)
        compiled = self.tmp()
        lines.append(f"  {compiled} = call ptr @festina_regex_compile(ptr {pattern_val}, ptr {flags_val})")
        lines.append(f"  store ptr {compiled}, ptr {cache_global}")
        compile_pred = self.cur_block
        lines.append(f"  br label %{done_label}")

        self._start_block(done_label, lines)
        out = self.tmp()
        lines.append(f"  {out} = phi ptr [ {loaded}, %{load_pred} ], [ {compiled}, %{compile_pred} ]")
        return out

    # ---- regex() / .test() / .match() / .replace() / .replaceAll() (claude.md #67-68) ----
    def _emit_regex_call(self, expr, env, lines):
        """claude.md #67: regex(pattern:text) / regex(pattern:text,
        flags:text) -> regex. Compiles the pattern via POSIX regcomp()
        at the call site, every time it's evaluated -- no caching across
        calls, the same tradeoff already accepted for sqlite()'s
        prepared statements (see _emit_sqlite_call). Unlike a /pattern/
        flags literal (see _emit_cached_regex_lit above), pattern here
        is an arbitrary runtime expression, so the same call site can
        legitimately see a different pattern on different calls (e.g.
        regex(userPattern) inside a loop) -- caching by call site would
        be a correctness bug, not just a missed optimization, so a
        regex() call inside a hot loop still recompiles every iteration;
        documented as a known gap rather than solved here (claude.md
        #54's ambiguity rule -- correctness over micro-optimizing
        something #67 doesn't ask for). An invalid pattern fails at
        runtime (festina_fail(), via the C runtime's regcomp() error
        handling), not at compile time -- the Python compiler doesn't
        itself validate regex syntax (claude.md #67's own words)."""
        callee = expr.callee
        if not expr.args:
            raise CodegenError("regex() requires at least a pattern argument",
                                file=self.filename, line=callee.line)
        pattern_val, pattern_type = self._emit_expr(expr.args[0], env, lines)
        if pattern_type != TEXT:
            raise CodegenError(
                f"regex()'s pattern argument must be text, found {types_mod.type_name(pattern_type)}",
                file=self.filename, line=callee.line)
        if len(expr.args) > 1:
            flags_val, flags_type = self._emit_expr(expr.args[1], env, lines)
            if flags_type != TEXT:
                raise CodegenError(
                    f"regex()'s flags argument must be text, found {types_mod.type_name(flags_type)}",
                    file=self.filename, line=callee.line)
        else:
            flags_val = self.string_const("")
        out = self.tmp()
        lines.append(f"  {out} = call ptr @festina_regex_compile(ptr {pattern_val}, ptr {flags_val})")
        return out, REGEX

    # ---- graphics: drawRect/drawCircle/drawText/drawImage/loadImage (claude.md #37, #39) ----
    def _emit_graphics_call(self, name, expr, env, lines):
        """Draws onto (or loads an image for) the graphics canvas.
        Sets self.uses_graphics so main() knows to open the canvas
        window before __festina_main() runs, and enter the event loop
        after it returns (see _emit_main_and_entry) -- exactly the same
        "only pay for what you use" pattern self.uses_sqlite already
        follows for festina_db_open(). semantic.py has already checked
        each function's argument count/types against claude.md's own
        worked examples (claude.md #37, #39), so this just emits the
        call; there's no color argument to pass because none of those
        examples ever take one (see festina_runtime.h's doc comment).

        loadImage() deliberately does NOT set self.uses_graphics:
        decoding a PNG (Cairo's own in-memory decoder -- see
        festina_load_image) needs no X server at all, unlike every
        other function here, which draws onto a window. claude.md #37's
        own example always pairs loadImage() with a later drawImage()
        anyway, which sets the flag itself -- so this only matters for
        the edge case of loading an image without ever drawing it,
        where requiring a window/display would be an artificial
        restriction claude.md never asks for."""
        self.uses_graphics_code = True
        args = [self._emit_expr(a, env, lines)[0] for a in expr.args]
        if name == "loadImage":
            out = self.tmp()
            lines.append(f"  {out} = call ptr @festina_load_image(ptr {args[0]})")
            return out, types_mod.ImageType()
        self.uses_graphics = True
        if name == "drawRect":
            x, y, w, h = args
            lines.append(f"  call void @festina_draw_rect(i64 {x}, i64 {y}, i64 {w}, i64 {h})")
        elif name == "drawCircle":
            x, y, r = args
            lines.append(f"  call void @festina_draw_circle(i64 {x}, i64 {y}, i64 {r})")
        elif name == "drawText":
            text, x, y = args
            lines.append(f"  call void @festina_draw_text(ptr {text}, i64 {x}, i64 {y})")
        elif name == "drawImage":
            img, x, y = args
            lines.append(f"  call void @festina_draw_image(ptr {img}, i64 {x}, i64 {y})")
        return "0", None

    # ---- setTimeout/setInterval/clearTimeout/clearInterval (claude.md
    # #69 -- see the module docstring's "Timers" note) ----
    def _emit_timer_call(self, name, expr, env, lines):
        """setTimeout/setInterval's first argument is the bare name of an
        already-declared `void func name() { }` (semantic.py's
        _infer_call checked this structurally, not through the normal
        expression-typing path -- Festina has no first-class functions/closures).
        That means expr.args[0] is always an ast.Identifier here, never
        an arbitrary expression to run through _emit_expr -- its LLVM
        symbol is simply `@<name>` (see _emit_func), already exactly the
        `void (*)(void)` function pointer festina_set_timeout/_interval
        expect, the same convention `on resize`/`on close` handlers
        already use.

        Sets self.uses_timers (mirroring self.uses_graphics) so main()
        knows to block in festina_run_event_loop() after __festina_main()
        returns -- but only for setTimeout/setInterval, which actually
        schedule ongoing work; clearTimeout()/clearInterval() alone
        don't, the same "only pay for what you use" reasoning
        self.uses_graphics already applies to loadImage()."""
        if name in ("setTimeout", "setInterval"):
            self.uses_timers = True
            callback_symbol = f"@{expr.args[0].name}"
            delay_val, _ = self._emit_expr(expr.args[1], env, lines)
            fn = "festina_set_timeout" if name == "setTimeout" else "festina_set_interval"
            out = self.tmp()
            lines.append(f"  {out} = call i64 @{fn}(ptr {callback_symbol}, i64 {delay_val})")
            return out, INT
        id_val, _ = self._emit_expr(expr.args[0], env, lines)
        fn = "festina_clear_timeout" if name == "clearTimeout" else "festina_clear_interval"
        lines.append(f"  call void @{fn}(i64 {id_val})")
        return "0", None

    # ---- sqlite() queries (claude.md #32-34) ----
    def _emit_sqlite_call(self, expr, env, lines, expected_type):
        """`expected_type` is the declared type of wherever this call's
        result flows into (a var's declared type, a param type, a return
        type, ... -- whatever _emit_value_for was threading through).
        When it's arr[TableType(name)], this is a SELECT whose rows get
        collected into that array (_emit_sqlite_collect); otherwise the
        statement just runs to completion and any result is discarded
        (festina_sqlite_exec), covering INSERT/UPDATE/DELETE and a SELECT
        nobody captures."""
        self.uses_sqlite = True
        callee = expr.callee
        if not expr.args:
            raise CodegenError("sqlite() requires at least a SQL string argument",
                                file=self.filename, line=callee.line)
        sql_val, sql_type = self._emit_expr(expr.args[0], env, lines)
        if sql_type != TEXT:
            raise CodegenError(
                f"sqlite()'s first argument must be text, found {types_mod.type_name(sql_type)}",
                file=self.filename, line=callee.line)

        db_val = self.tmp()
        lines.append(f"  {db_val} = load ptr, ptr @__festina_db")
        stmt_val = self.tmp()
        lines.append(f"  {stmt_val} = call ptr @festina_sqlite_prepare(ptr {db_val}, ptr {sql_val})")

        if len(expr.args) > 1:
            self._emit_sqlite_bind_params(expr.args[1], stmt_val, env, lines)

        table_type = expected_type.element if isinstance(expected_type, types_mod.ArrayType) else None
        if isinstance(table_type, types_mod.TableType):
            arr_val = self._emit_sqlite_collect(stmt_val, table_type, lines)
            return arr_val, expected_type

        lines.append(f"  call void @festina_sqlite_exec(ptr {stmt_val})")
        return "0", None

    def _emit_sqlite_bind_params(self, params_node, stmt_val, env, lines):
        # claude.md #33's own example binds a heterogeneously-typed
        # literal ([1, 'Patrick']) -- Festina's normal arr[T] rules
        # require a single element type, so a real arr[T] *value* can't
        # represent that. Treating the parameter list as special call
        # syntax (a literal array expression, each element bound
        # individually by its own type at compile time) sidesteps the
        # conflict instead of loosening arr[T] itself; see the module
        # docstring's "Query rows" note.
        if not isinstance(params_node, ast.ArrayLit):
            raise CodegenError(
                "sqlite()'s second argument (bound parameters) must be a "
                "literal array, e.g. sqlite(sql, [1, 'Patrick'])",
                file=self.filename, line=getattr(params_node, "line", 0))
        for i, elem in enumerate(params_node.elements):
            idx = i + 1  # sqlite3_bind_* parameters are 1-indexed
            if isinstance(elem, ast.NullLit):
                lines.append(f"  call void @festina_sqlite_bind_null(ptr {stmt_val}, i32 {idx})")
                continue
            val, vtype = self._emit_expr(elem, env, lines)
            if vtype == INT:
                lines.append(f"  call void @festina_sqlite_bind_int(ptr {stmt_val}, i32 {idx}, i64 {val})")
            elif vtype == FLOAT:
                lines.append(f"  call void @festina_sqlite_bind_float(ptr {stmt_val}, i32 {idx}, double {val})")
            elif vtype == TEXT:
                lines.append(f"  call void @festina_sqlite_bind_text(ptr {stmt_val}, i32 {idx}, ptr {val})")
            elif vtype == BOOL:
                # claude.md #30: bool maps to SQLite INTEGER, same as int.
                # An already-null bool (BOOL_NULL_CONST) binds as plain
                # 2 here rather than a real SQL NULL -- same "unresolved"
                # territory as using one in further boolean logic (see
                # the module docstring), not a new special case.
                z = self.tmp()
                lines.append(f"  {z} = zext i8 {val} to i64")
                lines.append(f"  call void @festina_sqlite_bind_int(ptr {stmt_val}, i32 {idx}, i64 {z})")
            else:
                raise CodegenError(
                    "sqlite() parameters must be int/float/bool/text/null, "
                    f"found {types_mod.type_name(vtype)}",
                    file=self.filename, line=getattr(elem, "line", 0))

    def _emit_sqlite_collect(self, stmt_val, table_type, lines):
        table_name = table_type.name
        cols = self.tables[table_name]
        _, types_global, ncols = self._table_arrays(table_name, cols)

        n_slot = self.tmp()
        lines.append(f"  {n_slot} = alloca i64")
        data_slot = self.tmp()
        lines.append(f"  {data_slot} = alloca ptr")
        lines.append(
            f"  call void @festina_sqlite_collect_rows(ptr {stmt_val}, i32 {ncols}, "
            f"ptr {types_global}, ptr {n_slot}, ptr {data_slot})"
        )
        n_val = self.tmp()
        lines.append(f"  {n_val} = load i64, ptr {n_slot}")
        data_val = self.tmp()
        lines.append(f"  {data_val} = load ptr, ptr {data_slot}")

        # Same {i64 length, ptr data} header _emit_array_lit builds --
        # festina_sqlite_collect_rows's out_data is already an array of
        # row pointers (one 8-byte pointer per row), exactly the layout
        # an arr[T] data pointer expects when _llvm_type(T) is "ptr"
        # (true for TableType, same as StructType) -- no repacking needed.
        header = f"%arr.hdr.{self._unique()}"
        lines.append(f"  {header} = alloca {FESTINA_ARRAY_LLVM_TYPE}")
        len_ptr = self.tmp()
        lines.append(f"  {len_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}, i32 0, i32 0")
        lines.append(f"  store i64 {n_val}, ptr {len_ptr}")
        data_field_ptr = self.tmp()
        lines.append(f"  {data_field_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}, i32 0, i32 1")
        lines.append(f"  store ptr {data_val}, ptr {data_field_ptr}")
        out = self.tmp()
        lines.append(f"  {out} = load {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}")
        return out

    # ---- entry function / main ----
    def _emit_main_and_entry(self):
        lines = []
        entry_ctx = {"lines": lines, "terminated": False}
        env = self.global_env
        self.cur_block = "entry"
        for stmt in self.entry_stmts:
            self.filename = getattr(stmt, "file", self.filename)  # see generate()'s note
            self._emit_toplevel_stmt(stmt, env, entry_ctx)
        if not entry_ctx["terminated"]:
            lines.append("  ret void")

        entry_func = ["define void @__festina_main() {"]
        entry_func.append("entry:")
        entry_func.extend(lines)
        entry_func.append("}")

        main_lines = ["define i32 @main() {", "entry:"]
        # self.uses_sqlite/self.uses_graphics are only reliably set by
        # this point because every function body (self.func_defs) and
        # every entry statement (the loop above) has already been
        # emitted -- see the module docstring's ordering note, or
        # generate()'s call order.
        if self.tables or self.uses_sqlite:
            # claude.md #70: DatabaseURL, evaluated here -- in main()'s
            # own prologue, before festina_db_open() -- rather than as
            # an ordinary entry statement (which would run far too late,
            # inside __festina_main(), after the database is already
            # open with whatever path was used). This also means the
            # expression runs before every OTHER global's own init
            # expression (those are ordinary entry statements, run
            # inside __festina_main()) -- referencing another global
            # variable here would see its zero value, not its
            # initializer's result; environment.NAME and a plain string
            # literal/template (the only cases claude.md #70 itself
            # shows) are unaffected by this, since neither depends on
            # any other global.
            if self.database_url_expr is not None:
                url_val, url_type = self._emit_value_for(self.database_url_expr, self.global_env, main_lines, TEXT)
                url_val = self._coerce(url_val, url_type, TEXT, main_lines)
            else:
                url_val = self.string_const("festina.sqlite")
            main_lines.append(f"  %db = call ptr @festina_db_open(ptr {url_val})")
            main_lines.append("  store ptr %db, ptr @__festina_db")
            for tname, cols in self.tables.items():
                names_global, types_global, ncols = self._table_arrays(tname, cols)
                main_lines.append(
                    f"  call void @festina_sync_table(ptr %db, ptr {self.string_const(tname)}, "
                    f"ptr {names_global}, ptr {types_global}, i32 {ncols})"
                )
        if self.uses_graphics:
            main_lines.append("  call void @festina_graphics_init()")
            register_fn = {"click": "festina_register_click_handler",
                            "mouse": "festina_register_mouse_handler",
                            "key": "festina_register_key_handler",
                            "resize": "festina_register_resize_handler",
                            "close": "festina_register_close_handler"}
            for event_name, symbol in self.event_handlers.items():
                main_lines.append(f"  call void @{register_fn[event_name]}(ptr {symbol})")
        main_lines.append("  call void @__festina_main()")
        if self.uses_graphics:
            # claude.md #40's "canvas" only means something while a
            # window is actually open -- block here, after the entry
            # function's own top-level statements have run (so anything
            # drawn there is already visible), handling Expose/click/
            # mouse/key/resize/close and any pending setTimeout/
            # setInterval callbacks until there's nothing left to wait
            # for (see festina_run_event_loop's own doc comment in
            # festina_runtime.h/graphics.c). festina_run_event_loop lives
            # in the graphics translation unit (X11 select()-based), so
            # it's only ever declared-and-called -- never linked in --
            # for a program that actually opens a window; see cli.py's
            # per-feature object file selection.
            main_lines.append("  call void @festina_run_event_loop()")
        elif self.uses_timers:
            # No window, but setTimeout/setInterval callbacks still need
            # a blocking loop to fire in -- festina_run_timer_loop is the
            # pure-POSIX (nanosleep-based, no X11 at all) equivalent that
            # lives in the core translation unit, so a timers-only
            # program never needs to link the graphics object file just
            # to wait for its callbacks.
            main_lines.append("  call void @festina_run_timer_loop()")
        main_lines.append("  ret i32 0")
        main_lines.append("}")

        return entry_func + [""] + main_lines

    def _table_arrays(self, table_name, cols):
        # Cached because both schema sync (main()) and a sqlite() query
        # against the same table (anywhere in the program) need these same
        # two globals -- emitting them a second time would redefine the
        # same LLVM global names (a link/parse error).
        cached = self._table_arrays_cache.get(table_name)
        if cached is not None:
            return cached
        names = list(cols.keys())
        types = list(cols.values())
        names_arr = f"@{table_name}.cols"
        types_arr = f"@{table_name}.types"
        name_ptrs = ", ".join(f"ptr {self.string_const(n)}" for n in names)
        type_ptrs = ", ".join(f"ptr {self.string_const(t)}" for t in types)
        self.extra_globals.append(f"{names_arr} = private constant [{len(names)} x ptr] [{name_ptrs}]")
        self.extra_globals.append(f"{types_arr} = private constant [{len(types)} x ptr] [{type_ptrs}]")
        result = (names_arr, types_arr, len(names))
        self._table_arrays_cache[table_name] = result
        return result

    def _emit_toplevel_stmt(self, stmt, env, ctx):
        lines = ctx["lines"]
        if isinstance(stmt, ast.VarDecl):
            ref, type_ = env.lookup(stmt.name)
            if stmt.init is not None:
                val, vtype = self._emit_value_for(stmt.init, env, lines, type_)
                val = self._coerce(val, vtype, type_, lines)
                lines.append(f"  store {_llvm_type(type_)} {val}, ptr {ref}")
            return
        self._emit_stmt(stmt, env, None, ctx)


def generate_ir(program, analyzed, filename="main.f"):
    gen = CodeGen(analyzed, filename)
    return gen.generate(program)


def _format_double(v):
    # repr(float) used to be used here directly, on the assumption its
    # decimal form always round-trips into something LLVM's IR parser
    # accepts -- it doesn't: repr() switches to scientific notation for
    # small/large magnitudes (e.g. 1e-07), and LLVM's double-literal
    # grammar rejects that (verified: "integer constant must have integer
    # type", i.e. it doesn't parse as a float literal at all). LLVM's `0x`
    # hex-float form takes the raw IEEE-754 bit pattern directly, so it's
    # exact and unambiguous regardless of magnitude -- no formatting edge
    # cases to enumerate.
    bits = struct.unpack(">Q", struct.pack(">d", float(v)))[0]
    return f"0x{bits:016X}"


def _encode_c_string(text):
    data = text.encode("utf-8") + b"\x00"
    out = []
    for b in data:
        c = chr(b)
        if c.isprintable() and c not in ('"', "\\") and b < 128:
            out.append(c)
        else:
            out.append(f"\\{b:02X}")
    return "".join(out), len(data)
