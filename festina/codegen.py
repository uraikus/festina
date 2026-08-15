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
#74/#75 (automatic reclamation of provably non-escaping struct/arr[T]/
map[T] locals -- stage 1 covering nested if/while/for bodies and
per-iteration loop-local freeing, stage 2 covering interprocedural
call-argument analysis, and a struct proven non-escaping this way now
gets a real stack alloca instead of a calloc+free pair -- see
escape_analysis.py, _emit_analyzed_func_body, _emit_block,
_emit_free_active_locals, and _emit_stmt's own VarDecl handling).
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

Struct storage is heap-allocated (calloc) by default, even for a struct
declared local to a function -- a struct's address genuinely can
outlive its function (returned, stored in an array or another struct's
field, ...), and stack-allocating unconditionally, without proving
which case applies first, silently corrupted every one of those cases
(verified: returning a local struct by value produced garbage at the
call site). calloc'ing every struct was the simple, uniformly-correct
starting choice per #54's ambiguity rule ("prefer the simplest
implementation" / "prefer performance" only when it doesn't also mean
"prefer incorrect"). calloc (not malloc) so uninitialized fields read
as zero, matching a global struct's `zeroinitializer` -- local and
global structs start identically rather than one being zeroed and the
other garbage.

claude.md #43 prefers stack allocation "when the value's lifetime
permits it" -- claude.md #74/#75 (automatic memory management's
staged rollout) is the proof mechanism that makes it possible to know
which structs that actually is: #74 first proves a struct's address
never left its declaring function/handler at all (stage 1), then #75
extends that same proof through calls to other functions in the same
program too (stage 2, interprocedural). A struct provably safe under
either stage now gets a genuine stack alloca (_emit_stmt's own VarDecl
handling), explicitly zeroed with `store %struct.T zeroinitializer`
immediately after (matching calloc's own zero-init behavior -- the two
allocation strategies must stay indistinguishable to any Festina
program, not just individually correct), instead of calloc+free --
zero allocator traffic, not just leak-free. This is sound for exactly
the same reason the earlier calloc+free-at-scope-exit approach was: an
address that never escapes needs nothing beyond its own function's
stack frame to remain valid, and a stack alloca's own lifetime already
matches exactly the points _emit_free_active_locals would otherwise
have freed it at (block exit, every loop iteration, break/continue) --
LLVM's alloca reserves one fixed address for the whole enclosing
function regardless of which basic block textually contains it, so a
loop-body-declared one is simply reused, address and all, on the next
iteration, and each recursive call still gets its own genuinely
distinct address (ordinary calling-convention behavior, unrelated to
this choice) -- verified directly, not just reasoned about (see
tests/test_codegen.py::TestAutomaticMemoryReclamation's
test_recursive_function_with_a_non_escaping_struct_local_keeps_each_calls_own_value).
A struct that isn't provably safe under either stage still calloc's
and still leaks, exactly as before -- this changes *how* a proven-safe
struct is allocated, not what counts as proven safe; see
escape_analysis.py's own module docstring and
CodeGen._emit_analyzed_func_body/_emit_block/_emit_free_active_locals
for the analysis and freeing machinery, and claude.md #74/#75 for
exactly what is and isn't covered.

arr[T]/map[T] locals are unaffected by any of this: their data/entries
buffer is a genuinely dynamically-growing allocation (`.push()`, a map
literal growing past its initial size, ...) with no compile-time-known
fixed size, so it isn't safe to give a fixed-size alloca regardless of
escaping-ness -- they still always calloc their data/entries buffer and
still free it (map[T] additionally freeing each entry's own strdup'd
key -- see festina_map_free_entries in runtime/festina_runtime.c) when
proven non-escaping, exactly as stage 1 originally shipped.

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

Every arr[T], regardless of T, has the same fixed-size aggregate
*payload* shape, FESTINA_ARRAY_LLVM_TYPE = `%struct._FestinaArray =
type { i64, ptr }` (length, data pointer) -- Festina's own type system
(not the generated IR) is what keeps different arr[T] values from
mixing, exactly like festina.types keeps PrimitiveType/StructType/etc.
distinct without a runtime tag (claude.md #11). Named `_FestinaArray`
(leading underscore) rather than a plainer name specifically to make an
accidental collision with a user-declared `struct _FestinaArray { ...
}` less likely -- Festina's identifier grammar still technically
allows a user to write that exact name, so this lowers the odds
without eliminating the possibility; a Festina identifier can never
collide with an LLVM name containing a `.` in the middle the way
`struct_llvm_name` produces (`%struct.Name`), so a scheme that didn't
reuse that "%struct." prefix at all would close the gap completely if
it's ever worth the churn. claude.md #79: an arr[T]/map[T] *value*
(what `_llvm_type` returns, what a variable/field/param/return actually
holds) is a `ptr` to that payload's own storage, not the payload
itself -- the same indirect representation a struct value already has
(see `_emit_fresh_heap_header`), needed for two aliased bindings to
share one genuine identity rather than independently-mutable copies.
The data pointer is malloc'd, and (as of claude.md #74) freed
automatically at scope exit when escape_analysis.py proves a local
arr[T]/map[T] never escapes its declaring function/handler; an
ESCAPING one (claude.md #79) is reference counted instead, the same
treatment claude.md #77 already gives an escaping struct -- see
todo.md's "Memory management" section for the full picture of what's
covered and what's still ahead.

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
        # claude.md #79: like StructType, always a `ptr` to the value's
        # own storage -- never FESTINA_ARRAY_LLVM_TYPE's `{i64, ptr}`
        # shape directly (that shape still describes the storage this
        # points AT, unchanged; see that constant's own comment and
        # _emit_array_lit). This is what gives two arr[T] bindings a
        # genuine shared identity on assignment (`b = a` -- both now
        # hold the exact same header pointer, not independent copies
        # that merely started out agreeing), the same "aliasing means
        # sharing one address" semantics a struct-typed value already
        # has -- previously, arr[T] was the `{i64,ptr}` value itself,
        # copied by value on every assignment (a genuine, pre-existing,
        # unrelated-to-refcounting bug for map[T] specifically: growing
        # one alias via a key add can realloc its own entries buffer,
        # which only that ONE copy's own header ever finds out about --
        # confirmed directly, a real segfault, fixed as a side effect of
        # this same representation change).
        return "ptr"
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
        # claude.md #79: see the ArrayType branch above -- identical
        # reasoning, FESTINA_MAP_LLVM_TYPE's own `{i64, ptr}` shape
        # still describes the storage this points at.
        return "ptr"
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


class _StackStructFieldsOnly:
    """claude.md #78: wraps a StructType to mark, in CodeGen.
    _active_free_locals, a STACK-allocated struct local (see claude.md
    #74/#76 -- provably non-escaping, so its own storage is never
    heap-allocated/refcounted at all) that still has at least one
    struct-typed field of its own. Such a local needs a scope-exit
    action _emit_free_active_locals's existing StructType branch can't
    give it (releasing the struct ITSELF, via its own refcount header,
    which this local was never given in the first place -- see
    _emit_stmt's own VarDecl handling) and the plain "nothing at all"
    every other stack-allocated struct correctly gets: only its own
    struct-typed field(s) need releasing, the same "the container stays
    stack-allocated, but what it points to still needs explicit
    freeing" pattern arr[T]/map[T] locals already follow for their own
    data/entries buffer. See _emit_release_nested_fields_only."""

    def __init__(self, struct_type):
        self.struct_type = struct_type


class _StackArrayOrMap:
    """claude.md #79: wraps an ArrayType/MapType to mark, in CodeGen.
    _active_free_locals, a STACK-allocated arr[T]/map[T] local (see
    claude.md #74 -- provably non-escaping, so its own header is never
    heap-allocated/refcounted at all, unchanged from before this
    section). Its data/entries buffer is still always heap-allocated
    regardless of the header's own escaping-ness -- a dynamically-sized
    buffer was never safe to give a fixed-size alloca, before or after
    this section -- so it still needs freeing at scope-exit, through
    the header's own now-indirect `ptr` slot, and never through
    festina_release_array/_map (which would try to release/free the
    header itself, memory this local's storage never had)."""

    def __init__(self, type_):
        self.type_ = type_


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
        self._struct_release_fns = {}          # claude.md #78: struct name -> LLVM function name of
                                                # its own lazily-generated, per-type release wrapper --
                                                # see _release_fn_for_struct's own comment. Only ever
                                                # populated for a struct that has at least one struct-
                                                # typed field of its own; every other struct's values
                                                # keep using the plain, generic @festina_release
                                                # directly, unchanged from claude.md #77.
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
        self._loop_targets = []                # stack of (continue_label, break_label, free_depth) for
                                                # the innermost currently-being-emitted for/while loop --
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
                                                # -- see _emit_stmt's own defensive check. free_depth
                                                # (claude.md #74) is the self._active_free_locals frame
                                                # index this loop's own body frame occupies, recorded
                                                # right before _emit_block pushes it -- see that field's
                                                # own comment and _emit_free_active_locals.
        self._current_escaping_names = None    # claude.md #74: set (by _emit_analyzed_func_body) to
                                                # escape_analysis.find_escaping_names's result for the
                                                # function/handler body currently being emitted -- a
                                                # name's escaping-ness is a property of the whole
                                                # enclosing function, computed once, regardless of which
                                                # nested block within it happens to declare that name.
                                                # None outside any tracked function/handler body (e.g.
                                                # __festina_main's own top-level statements, which
                                                # claude.md #74 doesn't analyze at all) -- _emit_block
                                                # skips all of #74's tracking entirely in that case. Never
                                                # a stack: Festina has no nested function declarations
                                                # reaching codegen (see _toplevel), so only ever one
                                                # function/handler's body is being emitted at a time.
        self.escaping_params = {}              # claude.md #74 stage 2 (interprocedural): {func_name:
                                                # set[int]} -- for each FuncDecl already fully emitted,
                                                # which of ITS OWN parameter positions escape_analysis
                                                # proved escape somewhere in its own body. Built up
                                                # incrementally, one entry per function, immediately
                                                # after that function's own _emit_analyzed_func_body call
                                                # returns (see that method) -- never all at once in a
                                                # separate pass, because it doesn't need to be: semantic.py
                                                # already rejects a call to a function before its own
                                                # declaration (no forward references), so by the time any
                                                # function F's body is being walked, every function F could
                                                # possibly call (other than F itself, mid-recursion) is
                                                # necessarily already a key in this dict. A self-recursive
                                                # call inside F's own body looks up F's own name here
                                                # *before* F's own entry has been added -- a plain,
                                                # ordinary dict miss, correctly falling back to
                                                # escape_analysis.py's original conservative "any call
                                                # argument escapes" default, exactly like a call to an
                                                # unanalyzed builtin does. Never cleared, never popped:
                                                # unlike _current_escaping_names (one function at a time)
                                                # this accumulates across the whole program, since a
                                                # function emitted early may be called by many functions
                                                # emitted later. Passed to every
                                                # escape_analysis.find_escaping_names call this session
                                                # makes (see _emit_analyzed_func_body) -- see that
                                                # module's own docstring for the full contract.
        self._active_free_locals = []          # claude.md #74: stack of "frames," one per currently-
                                                # open block within the function/handler body being
                                                # emitted -- not just its own top-level body anymore, but
                                                # every if-then/if-else/while-body/for-body/plain nested
                                                # block within it too (see _emit_block), each pushed on
                                                # entry and popped on exit, mirroring the real block
                                                # nesting structure. Each frame is a list of (storage ref,
                                                # Type) for every non-escaping struct/arr[T]/map[T] local
                                                # declared directly in that block, appended to as
                                                # _emit_block's own statement loop reaches each qualifying
                                                # VarDecl in program order. Consulted by
                                                # _emit_free_active_locals -- a Return frees every open
                                                # frame at once (down_to=0, the whole stack, since
                                                # returning exits every nested scope simultaneously); a
                                                # Break/Continue frees only the frames opened since the
                                                # nearest enclosing loop's own body began (down_to = the
                                                # frame index recorded alongside that loop's own entry in
                                                # self._loop_targets); a block's own natural, non-
                                                # terminated fall-through exit frees just its own single
                                                # (topmost) frame. Same "instance-level stack, not
                                                # threaded through ctx" shape as _loop_targets, for the
                                                # same reason: it needs to keep working correctly through
                                                # arbitrary nesting depth.
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
            # claude.md #77: reference counting for struct values escape
            # analysis proves DO escape (global structs, and escaping
            # local structs that are never themselves returned) -- see
            # both functions' own doc comment in
            # runtime/festina_runtime.c, _global_var_defs, and
            # _emit_free_active_locals's own StructType branch.
            "declare void @festina_retain(ptr)",
            "declare void @festina_release(ptr)",
            # claude.md #78: the decrement-and-check half of
            # festina_release, split out so a struct with its own
            # struct-typed field(s) can cascade into releasing those
            # fields BEFORE actually freeing its own storage -- see
            # _release_fn_for_struct and festina_release_check's own
            # comment in runtime/festina_runtime.c.
            "declare i8 @festina_release_check(ptr)",
            # claude.md #79: reference counting for arr[T]/map[T]
            # values that escape -- see _release_fn_for and each
            # function's own doc comment in runtime/festina_runtime.c.
            "declare void @festina_release_array(ptr)",
            "declare void @festina_release_map(ptr)",
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
            # claude.md #74/#75: frees each entry's own strdup'd key
            # (never a plain free()-the-buffer-alone away from leaking
            # them -- see festina_map_free_entries's own doc comment)
            # before freeing the entries buffer itself -- see
            # _emit_free_active_locals's MapType branch.
            "declare void @festina_map_free_entries(i64, ptr)",
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
                #
                # claude.md #77: this initial storage is never freed --
                # it's static/global memory, not a heap allocation, and
                # passing it to free() would be undefined behavior. It
                # still gets the same i64-refcount-header layout every
                # other struct value does (see festina_retain/
                # festina_release's own comment in
                # runtime/festina_runtime.c), but with the header
                # initialized to -1, a sentinel both functions treat as
                # "immortal, always a no-op" -- so a global's first-ever
                # reassignment (from this untouched storage to a real
                # heap value) needs no special-casing at the assignment
                # site at all; retaining/releasing whatever this global
                # currently points to is always safe, whatever that is.
                struct_ty = self.struct_llvm_name(type_.name)
                header = f"{ref}.header"
                lines.append(f"{header} = global {{i64, {struct_ty}}} {{i64 -1, {struct_ty} zeroinitializer}}")
                lines.append(f"{ref} = global ptr getelementptr({{i64, {struct_ty}}}, ptr {header}, i32 0, i32 1)")
                continue
            if isinstance(type_, (types_mod.ArrayType, types_mod.MapType)):
                # claude.md #79: identical treatment to the StructType
                # branch just above -- see its own comment for the full
                # reasoning (immortal sentinel refcount, no special-
                # casing needed at a global's first-ever reassignment).
                # Only the payload shape differs (FESTINA_ARRAY_LLVM_TYPE/
                # FESTINA_MAP_LLVM_TYPE's `{i64, ptr}` in place of a
                # user struct's own field list).
                payload_ty = (FESTINA_ARRAY_LLVM_TYPE if isinstance(type_, types_mod.ArrayType)
                              else FESTINA_MAP_LLVM_TYPE)
                header = f"{ref}.header"
                lines.append(f"{header} = global {{i64, {payload_ty}}} {{i64 -1, {payload_ty} zeroinitializer}}")
                lines.append(f"{ref} = global ptr getelementptr({{i64, {payload_ty}}}, ptr {header}, i32 0, i32 1)")
                continue
            llvm_ty = _llvm_type(type_)
            zero = self._zero_value(type_)
            lines.append(f"{ref} = global {llvm_ty} {zero}")
        return lines

    def _zero_value(self, type_):
        # claude.md #79: every "%struct."-shaped LLVM type (arr[T]/
        # map[T]'s own FESTINA_ARRAY_LLVM_TYPE/FESTINA_MAP_LLVM_TYPE
        # payload) is now only ever reached *indirectly*, through a
        # `ptr` -- struct/array/map-typed globals all get their own
        # dedicated, refcount-sentinel-carrying storage in
        # _global_var_defs instead of ever reaching this function, so
        # there is no remaining case that needs a "zeroinitializer"
        # aggregate value here; "null" (the final fallback below) is
        # correct for all three now, the same as it already was for
        # StructType.
        llvm_ty = _llvm_type(type_)
        if llvm_ty in ("i64", "i1", "i8"):
            return "0"
        if llvm_ty == "double":
            return "0.0"
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

    def _emit_free_active_locals(self, lines, down_to=0):
        """claude.md #74: frees every non-escaping local active in every
        frame of self._active_free_locals from the top of the stack down
        to (and including) index `down_to`.

        down_to=0 (the default) frees every currently open frame --
        correct for a Return, which exits the *entire* function/handler
        at once, so every nested block's own still-open locals need
        freeing together, not just the innermost one (see _emit_stmt's
        Return handling). A Break/Continue only frees frames opened
        since the nearest enclosing loop's own body began (down_to = the
        frame index _emit_while/_emit_for recorded when that body's
        frame was about to be pushed -- see self._loop_targets' own
        comment) -- an outer function-level local merely *used* inside
        that loop, not declared inside it, must NOT be freed by the
        loop's own break/continue, and this is what keeps that true.
        _emit_block's own natural (non-terminated) fall-through exit
        frees just its own single frame (down_to = that frame's own,
        topmost, index) before popping it.

        A no-op if self._active_free_locals is empty (outside any
        tracked function/handler body -- e.g. __festina_main's own
        top-level statements, which claude.md #74 doesn't analyze at
        all) or if `down_to` is already past the current top of stack
        (nothing to free -- e.g. a block that never actually opened its
        own frame because it isn't inside a tracked body).

        What "free"/"release" means differs by exactly what
        _active_free_locals' own entry marks a name as (see
        _emit_block's own tracking comment for how each is chosen):
        a plain StructType/ArrayType/MapType entry means this local's
        own header is heap-allocated and refcounted (claude.md #77/
        #79), so it's RELEASED, via whichever function _release_fn_for
        dispatches to for that type, only actually freed once nothing
        else references it; a _StackStructFieldsOnly/_StackArrayOrMap
        entry means the header itself is stack-allocated (never
        released), but something reachable through it still needs
        explicit freeing (a struct's own struct-typed field(s), an
        array's data buffer, a map's entries buffer). Frames are freed
        innermost-first purely for readability of the emitted IR --
        each release/free call is independent, so the actual order
        never affects correctness.
        """
        if down_to >= len(self._active_free_locals):
            return
        for frame in reversed(self._active_free_locals[down_to:]):
            for ref, type_ in frame:
                if isinstance(type_, _StackStructFieldsOnly):
                    # claude.md #78: a stack-allocated struct local
                    # (see _emit_block's own tracking comment) whose own
                    # storage is never released here -- only whatever
                    # its own struct-typed field(s) currently point to,
                    # since a field write (_emit_assign) may have
                    # retained a reference nothing else will ever
                    # release otherwise.
                    self._emit_release_nested_fields_only(ref, type_.struct_type, lines)
                elif isinstance(type_, _StackArrayOrMap):
                    # claude.md #79: a stack-allocated arr[T]/map[T]
                    # local (see _emit_block's own tracking comment) --
                    # `ref` is the local's own `alloca ptr` slot, so
                    # this needs one load to reach the header's own
                    # (stack) storage before GEPing into its own
                    # data/entries field, the same pattern claude.md #78
                    # already established for a stack-allocated struct's
                    # own field access. Only that buffer is freed here,
                    # never the header itself -- it has no refcount
                    # header and isn't heap memory at all, so it must
                    # never reach festina_release_array/_map.
                    header = self.tmp()
                    lines.append(f"  {header} = load ptr, ptr {ref}")
                    if isinstance(type_.type_, types_mod.ArrayType):
                        field_ptr = self.tmp()
                        lines.append(f"  {field_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}, i32 0, i32 1")
                        data_ptr = self.tmp()
                        lines.append(f"  {data_ptr} = load ptr, ptr {field_ptr}")
                        lines.append(f"  call void @free(ptr {data_ptr})")
                    else:
                        # claude.md #74/#75: unlike an array's plain
                        # data buffer, a map's entries buffer has its
                        # own nested allocation per entry (each key is
                        # its own strdup'd copy -- see festina_map_set's
                        # own comment) that a plain free() of the
                        # entries pointer alone would leak.
                        # festina_map_free_entries frees each entry's
                        # key first, then the entries buffer itself.
                        count_ptr = self.tmp()
                        lines.append(f"  {count_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {header}, i32 0, i32 0")
                        count_val = self.tmp()
                        lines.append(f"  {count_val} = load i64, ptr {count_ptr}")
                        field_ptr = self.tmp()
                        lines.append(f"  {field_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {header}, i32 0, i32 1")
                        entries_ptr = self.tmp()
                        lines.append(f"  {entries_ptr} = load ptr, ptr {field_ptr}")
                        lines.append(f"  call void @festina_map_free_entries(i64 {count_val}, ptr {entries_ptr})")
                elif isinstance(type_, (types_mod.StructType, types_mod.ArrayType, types_mod.MapType)):
                    # claude.md #77/#79: release (not free) -- this
                    # value is refcounted (see _emit_stmt's own VarDecl
                    # handling), so its own reference simply needs
                    # dropping; whichever function _release_fn_for
                    # dispatches to only actually frees it once nothing
                    # else references it.
                    loaded = self.tmp()
                    lines.append(f"  {loaded} = load ptr, ptr {ref}")
                    lines.append(f"  call void {self._release_fn_for(type_)}(ptr {loaded})")

    def _emit_analyzed_func_body(self, decl, body_env, return_type, body_lines):
        """claude.md #74: runs escape_analysis.find_escaping_names once
        for decl's whole body and makes it available (self.
        _current_escaping_names) to every _emit_block call this body's
        emission reaches -- the function/handler's own top-level body
        and every nested if/while/for body alike, all governed by that
        one whole-function-scoped name set (a name's escaping-ness is a
        property of the whole enclosing function, not of whichever block
        it happens to be declared in -- see escape_analysis.py's own
        module docstring). Reset back to None afterward: Festina has no
        nested function declarations reaching codegen (a FuncDecl only
        ever exists at a whole program's top level -- see _toplevel), so
        this never needs to be a stack the way _active_free_locals and
        _loop_targets are, just a single value cleared between one
        function/handler's emission and the next.

        claude.md #74 stage 2 (interprocedural): passes self.
        escaping_params into find_escaping_names so a Call argument
        position already proven safe by some EARLIER function's own
        analysis is exempted from the default "any call argument
        escapes" rule (see escape_analysis.py's own docstring for the
        full contract). After decl's own body is fully walked, a real
        ast.FuncDecl (not an EventHandler -- nothing ever calls a
        handler by name, so it never needs an entry here) registers its
        own result into self.escaping_params, keyed by name, so any
        LATER function's own analysis can use it in turn -- see that
        field's own comment on why this incremental,
        one-function-at-a-time approach is already sound with no
        separate whole-program pre-pass or fixpoint needed.

        claude.md #77 (widened): a struct value returned through
        Return no longer needs its own separate name-based tracking
        the way it once did (self._returned_names, since removed) --
        Return's own handling now retains the value being returned
        whenever it might be aliased, the same treatment every other
        struct-producing site in this stage already gets, so no extra
        per-function state is needed here for it at all."""
        escaping = escape_analysis.find_escaping_names(decl.body, escaping_params=self.escaping_params)
        self._current_escaping_names = escaping
        try:
            block = self._emit_block(decl.body, body_env, return_type, body_lines)
        finally:
            self._current_escaping_names = None
        if isinstance(decl, ast.FuncDecl):
            self.escaping_params[decl.name] = {
                i for i, p in enumerate(decl.params) if p.name in escaping
            }
        return block

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

        block = self._emit_analyzed_func_body(decl, body_env, return_type, body_lines)
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

        block = self._emit_analyzed_func_body(decl, body_env, None, body_lines)
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
        """claude.md #74: this is the ONE block-body emitter used
        everywhere -- a function/event handler's own top-level body
        (via _emit_analyzed_func_body), an if-then/if-else, a while/for
        body, and a plain nested `{ }` block all go through this same
        method. When self._current_escaping_names is set (i.e. this
        block is somewhere inside a function/handler body #74 is
        analyzing at all -- see _emit_analyzed_func_body), this pushes
        its own frame onto self._active_free_locals, tracks every
        directly-declared non-escaping struct/arr[T]/map[T] local into
        it as that VarDecl is actually reached (in program order, so an
        earlier Return/Break/Continue on a path that never reaches a
        later declaration correctly never tries to free it), and frees
        just that one frame -- self._emit_free_active_locals(lines,
        down_to=<this frame's own index>) -- if this block reaches its
        own natural (non-terminated) end, before popping it. A Return/
        Break/Continue inside this block (however deeply nested in a
        further-nested block within it) frees this frame -- and every
        other frame it needs to -- itself, via _emit_stmt's own
        handling; this method's own trailing free is correctly skipped
        whenever that already happened, since it only runs when this
        block's own ctx["terminated"] is still False.

        Outside any tracked function/handler body (self.
        _current_escaping_names is None -- __festina_main's own top-
        level statements, which #74 doesn't analyze at all), this is
        unchanged from before #74 existed: no frame, no tracking,
        nothing freed."""
        env = Env(parent_env)
        ctx = {"lines": lines, "terminated": False}
        tracking = self._current_escaping_names is not None
        if tracking:
            self._active_free_locals.append([])
        try:
            for stmt in block.body:
                if ctx["terminated"]:
                    break
                self._emit_stmt(stmt, env, return_type, ctx)
                if tracking and isinstance(stmt, ast.VarDecl):
                    found = env.lookup(stmt.name)
                    if found is not None:
                        ref, type_ = found
                        if isinstance(type_, (types_mod.ArrayType, types_mod.MapType)):
                            # claude.md #79 (mirroring claude.md #77's
                            # own struct widening below): a with-init
                            # local never stack-allocates at all (see
                            # _emit_stmt's own VarDecl handling -- it
                            # always aliases its initializer's value),
                            # so it's always refcounted and always safe
                            # to schedule for release, regardless of its
                            # own escaping-ness. A no-init local is only
                            # refcounted (not stack-allocated) when
                            # escape_analysis actually proves it
                            # escapes -- exactly mirroring _emit_stmt's
                            # own stack-vs-heap decision. Either way, a
                            # name that's ever itself returned is not
                            # excluded here either -- Return's own
                            # handling retains first, same as for
                            # structs.
                            is_stack_allocated = (stmt.init is None
                                                   and stmt.name not in self._current_escaping_names)
                            if not is_stack_allocated:
                                self._active_free_locals[-1].append((ref, type_))
                            else:
                                # arr[T]/map[T] still always calloc/
                                # malloc their data/entries buffer
                                # regardless of the HEADER's own
                                # escaping-ness (a genuinely dynamic-size
                                # buffer isn't safe to give a fixed-size
                                # alloca -- see this method's own
                                # docstring) -- so a stack-allocated
                                # header still needs THAT buffer freed at
                                # scope-exit; see _StackArrayOrMap's own
                                # comment.
                                self._active_free_locals[-1].append(
                                    (ref, _StackArrayOrMap(type_)))
                        elif isinstance(type_, types_mod.StructType):
                            # claude.md #77 (widened): a struct local
                            # declared WITH an initializer never goes
                            # through stack allocation at all (see
                            # _emit_stmt's own VarDecl handling -- it
                            # always aliases its initializer's value,
                            # retained there whenever that source isn't
                            # a fresh call result), so it's always
                            # refcounted and always safe to schedule for
                            # release, regardless of its own escaping-
                            # ness. A struct local declared WITHOUT one
                            # is only refcounted (not stack-allocated)
                            # when escape_analysis actually proves it
                            # escapes -- exactly mirroring _emit_stmt's
                            # own stack-vs-heap decision, since
                            # scheduling anything else for release would
                            # try to release a stack address that was
                            # never calloc'd with a header in the first
                            # place. A name that's ever itself returned
                            # is no longer excluded here either (unlike
                            # this stage's earlier passes): Return's own
                            # handling now retains the value first
                            # whenever it might be aliased (see
                            # _emit_stmt's Return branch), so releasing
                            # this binding too, right alongside every
                            # other active local, nets out to exactly
                            # one reference surviving -- the one handed
                            # to the caller -- rather than leaking it.
                            is_stack_allocated = (stmt.init is None
                                                   and stmt.name not in self._current_escaping_names)
                            if not is_stack_allocated:
                                self._active_free_locals[-1].append((ref, type_))
                            elif self._struct_has_own_refcounted_field(type_.name):
                                # claude.md #78: this local's own
                                # storage is stack-allocated and never
                                # itself released -- but writing into
                                # one of its struct-typed fields
                                # (_emit_assign) still retains whatever
                                # value that field points to, and
                                # nothing else will ever release that
                                # extra reference unless this scope-exit
                                # does. See _StackStructFieldsOnly's own
                                # comment.
                                self._active_free_locals[-1].append(
                                    (ref, _StackStructFieldsOnly(type_)))
            if tracking and not ctx["terminated"]:
                self._emit_free_active_locals(lines, down_to=len(self._active_free_locals) - 1)
        finally:
            if tracking:
                self._active_free_locals.pop()
        return ctx

    def _emit_stmt(self, stmt, env, return_type, ctx):
        lines = ctx["lines"]
        if isinstance(stmt, ast.VarDecl):
            type_ = self._resolve(stmt.type_expr, stmt)
            if isinstance(type_, types_mod.StructType):
                # `slot` holds a *pointer* to the struct's own storage,
                # kept uniform with every other type so Identifier lookup
                # never needs a struct special-case, whichever way that
                # storage itself was obtained below.
                #
                # claude.md #43: "prefer stack allocation when the
                # value's lifetime permits it." A struct whose address
                # can outlive its function (returned, put in an array,
                # stored in another struct's field, ...) genuinely can't
                # -- unconditional stack allocation was tried once,
                # before claude.md #74 existed, and reverted after being
                # verified to silently corrupt memory (see this module's
                # own docstring's "Struct storage is always heap-
                # allocated" note, and escape_analysis.py's docstring)
                # -- but claude.md #74/#75's escape analysis now proves
                # exactly the cases where it DOES permit it: whenever
                # this exact name is also what _emit_block would
                # otherwise have freed at scope exit (see that method's
                # own tracking logic, which now skips StructType
                # precisely because there's nothing left for it to
                # free). Reusing that same proof for allocation instead
                # of only for freeing is sound for the identical reason
                # it was sound for freeing: an address that never
                # escapes its declaring function needs nothing beyond
                # that function's own stack frame to remain valid, and a
                # stack alloca's own lifetime already matches exactly
                # the same points (block exit, every loop iteration,
                # break/continue) _emit_free_active_locals would
                # otherwise have freed it at -- LLVM's alloca reserves a
                # single fixed address for the whole enclosing function
                # regardless of which basic block textually contains it,
                # so a loop-body-declared one is simply reused, address
                # and all, on the next iteration, with zero allocator
                # traffic either way (faster than the calloc+free stage
                # 1/2 alone would have produced, not just leak-free
                # sooner). Recursion needs no special handling either:
                # each call gets its own stack frame regardless of this
                # choice (ordinary calling-convention behavior), so each
                # recursive invocation's own alloca is a genuinely
                # distinct address.
                #
                # Scoped to *this* local's own storage only, not
                # anything reachable through it -- a struct-typed FIELD
                # of this struct, if ever assigned an existing local's
                # value (the only way one can be populated; there is no
                # struct-literal initializer syntax), still always
                # aliases that other value's own storage exactly as
                # before, unconditionally heap-allocated, unconditionally
                # never retained/released via this local's own cleanup
                # (claude.md #77's own release, below, only ever touches
                # a struct's own top-level allocation, never walks into
                # its fields) -- proving that's *also* safe to change
                # needs real aliasing analysis (does anything else still
                # reference that field's value when this struct's own
                # lifetime ends), a genuinely different and harder
                # question than "does this local's own name ever appear
                # outside a safe position," and not attempted here; see
                # todo.md.
                uid = self._unique()
                struct_ty = self.struct_llvm_name(type_.name)
                backing = f"%{stmt.name}.storage.{uid}"
                slot = f"%{stmt.name}.{uid}"
                if stmt.init is not None:
                    # `Point r = someExpr` -- most commonly a function
                    # call returning a struct by value (claude.md #55's
                    # own "returning a struct by value" fix is exactly
                    # what makes this safe: the callee's own return
                    # value is never a stack allocation the callee's
                    # frame owns). This local simply aliases whatever
                    # storage the initializer's own value already
                    # points to, the same as a plain `r = expr`
                    # assignment would -- no fresh allocation of its
                    # own at all, since structs are always reference
                    # types at the Festina level (see this method's own
                    # "kept uniform" note above).
                    #
                    # claude.md #77: retained here (mirroring
                    # _emit_local_retain_release's own logic,
                    # which this can't just call -- there is no OLD
                    # value to release for a name's own first-ever
                    # declaration) whenever the initializer isn't a
                    # plain function call -- see
                    # _is_owning_refcounted_source's own comment for why a
                    # call result needs no retain (it already owns a
                    # fresh +1 nothing else references yet) while
                    # anything else (an existing local/parameter/global
                    # read, a field read, ...) does (something else
                    # already references it too). This is what makes it
                    # safe for _emit_block to schedule r for release at
                    # its own scope-exit regardless of whether it has an
                    # initializer -- r's own reference is now always
                    # correctly counted, whichever way it came to exist.
                    val, vtype = self._emit_value_for(stmt.init, env, lines, type_)
                    val = self._coerce(val, vtype, type_, lines)
                    if not self._is_owning_refcounted_source(stmt.init):
                        lines.append(f"  call void @festina_retain(ptr {val})")
                    lines.append(f"  {slot} = alloca ptr")
                    lines.append(f"  store ptr {val}, ptr {slot}")
                    env.define(stmt.name, slot, type_)
                    return
                non_escaping = (self._current_escaping_names is not None
                                and stmt.name not in self._current_escaping_names)
                if non_escaping:
                    lines.append(f"  {backing} = alloca {struct_ty}")
                    # calloc zero-initializes; alloca doesn't -- claude.md
                    # #74's own module docstring note ("uninitialized
                    # fields read as zero, matching a global struct's
                    # zeroinitializer") still has to hold here, so this
                    # explicitly zeros the same way a global's own
                    # `zeroinitializer` storage already does (see
                    # _struct_type_defs / _global_var_defs) -- the two
                    # allocation strategies below must remain
                    # indistinguishable to any Festina program, or this
                    # wouldn't be "prefer stack allocation when it's
                    # safe," it would be a visible behavior change.
                    lines.append(f"  store {struct_ty} zeroinitializer, ptr {backing}")
                else:
                    # claude.md #77: an escaping struct local is
                    # refcounted like any other escaping struct value
                    # (see festina_retain/festina_release's own comment
                    # in runtime/festina_runtime.c for the header
                    # layout) -- allocate 8 extra bytes for the i64
                    # refcount header, initialize it to 1 (this
                    # binding's own reference), and offset the visible
                    # `backing` pointer past it so every existing GEP-
                    # based field access downstream is unaffected.
                    size_val = self._sizeof(struct_ty, lines)
                    total_size = self.tmp()
                    lines.append(f"  {total_size} = add i64 {size_val}, 8")
                    raw = f"%{stmt.name}.raw.{uid}"
                    lines.append(f"  {raw} = call ptr @calloc(i64 1, i64 {total_size})")
                    lines.append(f"  store i64 1, ptr {raw}")
                    lines.append(f"  {backing} = getelementptr i8, ptr {raw}, i64 8")
                lines.append(f"  {slot} = alloca ptr")
                lines.append(f"  store ptr {backing}, ptr {slot}")
                env.define(stmt.name, slot, type_)
                # stmt.init is None here -- handled by the early-return
                # branch above.
                return
            if isinstance(type_, (types_mod.ArrayType, types_mod.MapType)):
                # claude.md #79: identical treatment to the StructType
                # branch just above -- see its own comment for the full
                # reasoning (this is what gives an arr[T]/map[T] local a
                # real stack-allocation option when non-escaping, the
                # same "prefer stack allocation when the value's
                # lifetime permits it" claude.md #43 already promises
                # structs, plus the identical retain-on-alias treatment
                # for a with-initializer declaration). Only the payload
                # shape differs.
                payload_ty = (FESTINA_ARRAY_LLVM_TYPE if isinstance(type_, types_mod.ArrayType)
                              else FESTINA_MAP_LLVM_TYPE)
                uid = self._unique()
                backing = f"%{stmt.name}.storage.{uid}"
                slot = f"%{stmt.name}.{uid}"
                if stmt.init is not None:
                    val, vtype = self._emit_value_for(stmt.init, env, lines, type_)
                    val = self._coerce(val, vtype, type_, lines)
                    if not self._is_owning_refcounted_source(stmt.init):
                        lines.append(f"  call void @festina_retain(ptr {val})")
                    lines.append(f"  {slot} = alloca ptr")
                    lines.append(f"  store ptr {val}, ptr {slot}")
                    env.define(stmt.name, slot, type_)
                    return
                non_escaping = (self._current_escaping_names is not None
                                and stmt.name not in self._current_escaping_names)
                if non_escaping:
                    lines.append(f"  {backing} = alloca {payload_ty}")
                    lines.append(f"  store {payload_ty} zeroinitializer, ptr {backing}")
                else:
                    backing = self._emit_fresh_heap_header(payload_ty, lines)
                lines.append(f"  {slot} = alloca ptr")
                lines.append(f"  store ptr {backing}, ptr {slot}")
                env.define(stmt.name, slot, type_)
                # stmt.init is None here -- handled by the early-return
                # branch above.
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
            val, vtype = self._emit_expr(stmt.expr, env, lines)
            # claude.md #77: the one struct-return leak the retain-on-
            # Return fix above doesn't touch -- a call result that's
            # discarded outright, never bound to anything at all (`f();`
            # as a bare statement). Nothing else in this stage's
            # tracking would ever reach this value: it's not a local
            # (no VarDecl), not a global (no assignment), and Return's
            # own retain only protects a value being handed BACK to a
            # caller, not one a caller is about to throw away. Since a
            # Call's own return value is always "owning" (see
            # _is_owning_refcounted_source) -- fresh, nothing else
            # referencing it yet -- this ExprStmt is provably the value's
            # ONLY reference, so releasing it immediately (freeing it,
            # since nothing else can possibly still hold it) is always
            # correct, not just conservative. Only fires for a bare Call
            # used as a statement, matching exactly the "owning" source
            # shape -- an ExprStmt wrapping anything else (a bare
            # Identifier, a Member read, ...) never allocates anything of
            # its own to begin with, so there is nothing to release.
            if (isinstance(vtype, (types_mod.StructType, types_mod.ArrayType, types_mod.MapType))
                    and isinstance(stmt.expr, (ast.Call, ast.ArrayLit, ast.MapLit))):
                # claude.md #78/#79: through _release_fn_for (which
                # dispatches to _release_fn_for_struct for a struct, so
                # a discarded struct-typed call result with its own
                # nested struct-typed field(s) still cascades correctly)
                # rather than any one type's release function directly.
                # claude.md #79 widens this beyond a discarded Call: a
                # bare array/map literal statement (`[1,2,3];`,
                # degenerate but syntactically valid) is just as
                # "owning" a source as a Call is, and just as
                # unambiguously this statement's own sole reference.
                lines.append(f"  call void {self._release_fn_for(vtype)}(ptr {val})")
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
                # claude.md #77 (widened further): a struct being handed
                # back to the caller gets the exact same owning/aliasing
                # treatment _emit_local_retain_release already
                # gives a plain local assignment -- retain it here,
                # BEFORE _emit_free_active_locals below releases every
                # active local (which, since a returned name is no
                # longer excluded from that list -- see _emit_block's
                # own comment -- may include the very binding this value
                # came from). Skipped only when the source is a fresh,
                # uniquely-owned call result (_is_owning_refcounted_source),
                # the same "no retain needed, the +1 just transfers"
                # case every other call site in this stage already
                # relies on. This is what makes it safe to stop
                # excluding a returned local from scope-exit release at
                # all: retain-then-release-everything nets out to
                # exactly one reference surviving per binding, on every
                # path, including a Ternary/parameter/field read this
                # source could be -- not just the bare-Identifier case a
                # name-based exclusion could ever recognize.
                # claude.md #79: widened to arr[T]/map[T] return values
                # too, the identical rule -- retain always being the
                # same generic @festina_retain regardless of type is
                # exactly what makes this one check cover all three.
                if (isinstance(return_type, (types_mod.StructType, types_mod.ArrayType, types_mod.MapType))
                        and not self._is_owning_refcounted_source(stmt.value)):
                    lines.append(f"  call void @festina_retain(ptr {val})")
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
            # claude.md #74: free every non-escaping local declared since
            # this loop's own body began -- its own frame, and any
            # further-nested block's frame between it and this break --
            # BEFORE actually leaving, same as reaching the loop body's
            # natural end would. free_depth is the frame index
            # _emit_while/_emit_for recorded right before that body's own
            # frame was pushed; it deliberately does NOT touch anything
            # below that (an outer function-level local merely *used*
            # inside this loop, not declared inside it, isn't this loop's
            # to free).
            _, break_label, free_depth = self._loop_targets[-1]
            self._emit_free_active_locals(lines, down_to=free_depth)
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
            # claude.md #74: same free-before-leaving treatment as break
            # above -- continuing still exits this iteration's own
            # nested scopes, even though the loop itself continues.
            continue_label, _, free_depth = self._loop_targets[-1]
            self._emit_free_active_locals(lines, down_to=free_depth)
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
        # claude.md #74: free_depth is recorded BEFORE _emit_block pushes
        # the body's own frame, so it's exactly that frame's own index --
        # break/continue free everything from there up, and nothing below.
        free_depth = len(self._active_free_locals)
        self._loop_targets.append((cond_label, end_label, free_depth))
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
        # claude.md #74: see _emit_while's identical free_depth note.
        free_depth = len(self._active_free_locals)
        self._loop_targets.append((update_label, end_label, free_depth))
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
                # claude.md #79: an arr[T] value is a `ptr` to its own
                # {i64, ptr} storage now, so .length is a GEP+load of
                # field 0 off that pointer -- not extractvalue on a
                # value anymore (that only ever worked because arr[T]
                # used to BE the {i64,ptr} value itself; see the module
                # docstring). Still not going through _member_ptr for it
                # (see claude.md #63): not every array-typed expression
                # is addressable at the SOURCE-LANGUAGE level (e.g. a
                # function call's own return value), even though every
                # one of them is now a `ptr` at the LLVM level -- and
                # .length is read-only anyway (see semantic.py).
                obj_val, obj_type = self._emit_expr(expr.obj, env, lines)
                if isinstance(obj_type, types_mod.ArrayType):
                    len_ptr = self.tmp()
                    lines.append(f"  {len_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 0")
                    out = self.tmp()
                    lines.append(f"  {out} = load i64, ptr {len_ptr}")
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

    def _emit_fresh_heap_header(self, payload_llvm_ty, lines):
        """claude.md #79: allocates a fresh, uniquely-owned (refcount=1)
        heap block for a refcounted value's own header -- an escaping
        struct local's own calloc (_emit_stmt's VarDecl handling) and
        every array/map literal (_emit_array_lit/_emit_map_lit/
        _emit_sqlite_collect) all now share this one implementation --
        and returns a `ptr` to the PAYLOAD portion, past the 8-byte i64
        refcount prefix every refcounted value shares (see
        festina_retain/festina_release's own comment in
        runtime/festina_runtime.c). calloc zero-initializes the whole
        block, so the payload's own fields all start at their zero
        value (0/null) exactly like a global's own `zeroinitializer`
        storage does -- the caller only needs to fill in whichever
        fields it actually has a value for."""
        size_val = self._sizeof(payload_llvm_ty, lines)
        total_size = self.tmp()
        lines.append(f"  {total_size} = add i64 {size_val}, 8")
        raw = self.tmp()
        lines.append(f"  {raw} = call ptr @calloc(i64 1, i64 {total_size})")
        lines.append(f"  store i64 1, ptr {raw}")
        payload = self.tmp()
        lines.append(f"  {payload} = getelementptr i8, ptr {raw}, i64 8")
        return payload

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

        # claude.md #79: a fresh, uniquely-owned (refcount=1) heap
        # header -- the same "owning" source _is_owning_refcounted_source
        # already treats an array/map literal as, so binding it into a
        # new slot needs no separate retain. Unconditionally heap-
        # allocated, the same as an escaping struct local's own
        # storage; a *non-escaping* local's own stack-allocated
        # optimization (claude.md #74) is a property of the LOCAL
        # BINDING this literal happens to initialize, decided in
        # _emit_stmt, not of the literal's own construction here.
        header = self._emit_fresh_heap_header(FESTINA_ARRAY_LLVM_TYPE, lines)
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

        return header, types_mod.ArrayType(elem_type)

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

        # claude.md #79: a fresh, uniquely-owned heap header -- see
        # _emit_array_lit's own comment just above for why this is
        # always heap-allocated regardless of where this literal ends
        # up bound. calloc already zero-initializes count/entries (0,
        # null), so no separate stores are needed for them the way the
        # pre-#79 stack-alloca'd scratch header needed.
        header = self._emit_fresh_heap_header(FESTINA_MAP_LLVM_TYPE, lines)

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

        return header, types_mod.MapType(value_type)

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
        """claude.md #72: npcHealths['npc1'] -- count/entries are read
        straight out of the already-emitted map's own storage
        (claude.md #79: `obj_val` is now a `ptr` to that storage, not
        the {count,entries} value itself, so this needs a GEP+load per
        field, the same two-step pattern struct field reads already
        use -- not addressable via extractvalue anymore). No
        addressability needed for a READ regardless, unlike a write;
        see _emit_map_set."""
        count_ptr = self.tmp()
        lines.append(f"  {count_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 0")
        count = self.tmp()
        lines.append(f"  {count} = load i64, ptr {count_ptr}")
        entries_ptr = self.tmp()
        lines.append(f"  {entries_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 1")
        entries = self.tmp()
        lines.append(f"  {entries} = load ptr, ptr {entries_ptr}")
        default = self._map_missing_default(value_type)
        raw = self.tmp()
        lines.append(f"  {raw} = call i64 @festina_map_get(i64 {count}, ptr {entries}, ptr {key_val}, i64 {default})")
        return self._i64_to_map_value(raw, value_type, lines), value_type

    def _emit_map_set(self, map_ptr, value_type, key_val, value_val, lines):
        """claude.md #72: npcHealths['npc1'] = 30 (and the equivalent
        per-entry calls a map literal builds itself out of -- see
        _emit_map_lit). Unlike a read, this needs the map's own actual
        header ADDRESS (`map_ptr`, a `ptr` to its {count, entries}
        storage), not just its value, since festina_map_set can grow
        the backing entries array and has to write the new
        count/entries back into that same storage for the change to
        actually stick. claude.md #79: since every arr[T]/map[T] value
        is now itself a `ptr` to that storage (not the storage inline),
        `map_ptr` here is always already that pointer -- the LOADED
        value of a variable's own slot/global, a struct field's own
        loaded value, or (during literal construction) the literal's
        own fresh heap header -- never a slot or field's own ADDRESS a
        further load would still be needed for; see _try_addressable's
        own comment for where that load actually happens."""
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
        """Given an already-emitted arr[T] value (claude.md #79: a
        `ptr` to its own {length, data} storage, not the value itself
        -- a GEP+load per field is needed to reach the data pointer,
        not extractvalue), its ArrayType, and an already-emitted int
        index value, returns (ptr, element_type) -- a pointer to that
        element's storage slot. Shared by _emit_expr's computed-Member
        read dispatch and _emit_assign's write dispatch so obj_val/
        idx_val are each the caller's own single emission, never
        re-emitted here -- see _emit_expr's own comment on why an
        object expression might not be safe to emit twice."""
        data_field_ptr = self.tmp()
        lines.append(f"  {data_field_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 1")
        data_ptr = self.tmp()
        lines.append(f"  {data_ptr} = load ptr, ptr {data_field_ptr}")
        elem_type = obj_type.element
        elem_llvm_ty = _llvm_type(elem_type)
        out = self.tmp()
        lines.append(f"  {out} = getelementptr {elem_llvm_ty}, ptr {data_ptr}, i64 {idx_val}")
        return out, elem_type

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

    def _emit_global_retain_release(self, ref, val, ttype, lines):
        """claude.md #77 (widened by claude.md #79 to arr[T]/map[T]
        globals too): called immediately before storing `val` (a
        struct/array/map-typed value) into a GLOBAL's own slot `ref`,
        whether that's an ordinary reassignment (_emit_assign) or a
        global's own declaration-with-initializer (_emit_toplevel_stmt)
        -- both sites need the identical treatment, factored out here
        so they can't drift apart. Retains the new value (this global's
        own slot is now one more binding referencing it) and releases
        whatever it previously held (one fewer binding referencing
        that), freeing it if that was the last one. Retain happens
        BEFORE release so a self-assignment (`g = g`, or aliasing
        through another name that already equals g's own current
        value) can never see refcount hit zero and free something still
        about to be stored. Always safe to call unconditionally,
        including the very first time a global's value is ever set
        (from its own untouched static initial storage): both functions
        treat a negative refcount as an immortal no-op (see
        _global_var_defs and festina_retain/festina_release's own
        comment in runtime/festina_runtime.c), so there's nothing to
        special-case here regardless of what `ref` currently holds.
        Retaining the new value even when it's a fresh, uniquely-owned
        value (a function call's own return value, never aliased
        anywhere else) is deliberately over-conservative rather than
        precise -- it can only ever delay a free that a sharper
        analysis could have done sooner, never cause one to happen too
        early. Releases through _release_fn_for rather than any one
        type's release function directly (claude.md #78) -- a global of
        a struct type with its own struct-typed field(s) needs its
        release to cascade into those fields too, the same as any other
        release site for that struct type."""
        if (not isinstance(ttype, (types_mod.StructType, types_mod.ArrayType, types_mod.MapType))
                or not ref.startswith("@")):
            return
        old = self.tmp()
        lines.append(f"  {old} = load ptr, ptr {ref}")
        lines.append(f"  call void @festina_retain(ptr {val})")
        lines.append(f"  call void {self._release_fn_for(ttype)}(ptr {old})")

    def _is_owning_refcounted_source(self, expr):
        """claude.md #77 (widened): a source expression is "owning" --
        a fresh value with no other binding referencing it yet, so
        aliasing it into a new slot needs no retain, the same "moves,
        doesn't copy" reasoning a Return statement already relies on --
        only when it's a plain function Call. Every other expression
        shape (a bare Identifier reading an existing local/parameter/
        global, a Member/field read, a Ternary between two such reads,
        ...) is conservatively treated as "aliasing": something ELSE
        already references this exact value (or could), so a NEW
        binding referencing it too needs its own retain. This can only
        ever retain when it turns out not to have been strictly
        necessary (over-conservative, never under), never skip a retain
        a real alias actually needed -- the same directional bias every
        other stage in this whole effort has taken when a choice wasn't
        fully provable either way.

        Deliberately simple and conservative rather than a full points-
        to analysis: a function call's own return value is "owning"
        regardless of what that function's OWN body actually returns
        (a fresh local it created, or -- in principle -- one of its own
        parameters passed straight through) -- verified sound for
        Festina specifically, not merely assumed, because nothing this
        stage does lets a value's lifetime outlive its own true owner's
        scope: a caller's local bound from a call result is itself
        subject to this exact same tracking (retained wherever it's
        THEN aliased further), so an under-counted "owning" call result
        can only ever matter if something reads it after its own
        binding's scope has already ended, which Festina's own lexical
        scoping (no closures, no way to keep a name alive past its own
        block) never allows in the first place.

        claude.md #79: an ast.ArrayLit/ast.MapLit is "owning" for the
        identical reason a Call is -- structs have no literal syntax at
        all, so this case never arose for them, but `[1,2,3]`/`{...}`
        allocate a fresh, uniquely-owned header exactly like a Call's
        own return value does (see _emit_array_lit/_emit_map_lit's own
        "fresh, uniquely-owned" comment), nothing else referencing it
        the instant it's produced."""
        return isinstance(expr, (ast.Call, ast.ArrayLit, ast.MapLit))

    def _emit_local_retain_release(self, ref, val, source_expr, ttype, lines):
        """claude.md #77 (widened; claude.md #79 widens it again to
        arr[T]/map[T] locals): the local-variable counterpart to
        _emit_global_retain_release, called from _emit_assign's
        Identifier branch when the target is a LOCAL (not a global) --
        never called for the target's own FIRST-EVER declaration
        (VarDecl-with-initializer handles that separately, in
        _emit_stmt, since it never has an "old value" to release), only
        for an ordinary `q = expr` reassignment of an already-declared
        local. Unlike the global version, there is no sentinel/immortal
        case to skip here: a local of any of these three types that's
        ever the target of ANY assignment is unconditionally marked
        escaping by escape_analysis's own existing rule (an assignment
        target always escapes, regardless of what type it is), so it
        can never have been stack-allocated (see _emit_stmt's own
        VarDecl handling) -- its current value, at the point of any
        reassignment, is always either its own original heap+header
        allocation or some other value it was previously made to
        alias, either way a real, releasable pointer. Retains the new
        value only when _is_owning_refcounted_source says it needs it
        (a fresh call/literal result's own +1 already correctly
        transfers by just aliasing it, no retain needed -- retaining it
        too would permanently over-count, the one case where matching
        the global-side function's always-retain choice would have
        cost real, permanent leaks this stage specifically exists to
        close); always releases the old value unconditionally, freeing
        it if this reassignment was its last reference. Retain (when it
        happens) still happens before release, for the identical
        self-assignment-safety reason the global version's own comment
        explains."""
        old = self.tmp()
        lines.append(f"  {old} = load ptr, ptr {ref}")
        if not self._is_owning_refcounted_source(source_expr):
            lines.append(f"  call void @festina_retain(ptr {val})")
        lines.append(f"  call void {self._release_fn_for(ttype)}(ptr {old})")

    def _release_fn_for(self, type_):
        """claude.md #79: the single dispatch point every release call
        site in this file goes through (mirroring claude.md #78's own
        _release_fn_for_struct, now one case among three rather than
        the only one) -- returns the LLVM function name to call to
        release a refcounted value of `type_`. A struct gets
        _release_fn_for_struct's own per-type dispatch (the plain
        generic release, or a lazily-generated per-struct-type cascade
        wrapper, depending on whether it has its own struct-typed
        field). An arr[T]/map[T] needs no such per-type dispatch at
        all: unlike a struct, whose own field layout varies by Festina
        type, EVERY arr[T]'s header has the identical `{i64,ptr}` shape
        regardless of T (same for map[T]), so a single fixed runtime
        function (festina_release_array / festina_release_map -- see
        their own doc comments in runtime/festina_runtime.c) already
        handles every one, with no codegen-generated wrapper needed."""
        if isinstance(type_, types_mod.StructType):
            return self._release_fn_for_struct(type_)
        if isinstance(type_, types_mod.ArrayType):
            return "@festina_release_array"
        if isinstance(type_, types_mod.MapType):
            return "@festina_release_map"
        raise CodegenError(f"cannot release a value of type {types_mod.type_name(type_)}")

    def _struct_has_own_refcounted_field(self, name):
        """claude.md #78 (widened by claude.md #79 to arr[T]/map[T]
        fields too): True when the struct declared `name` has at least
        one field of its own that is itself a struct/array/map-typed
        value -- never transitively (see _release_fn_for_struct's own
        comment on why only the direct case needs checking here)."""
        return any(isinstance(t, (types_mod.StructType, types_mod.ArrayType, types_mod.MapType))
                   for _, t in self.struct_fields(name))

    def _release_fn_for_struct(self, type_):
        """claude.md #78 (widened by claude.md #79): returns the LLVM
        function name to call to release a value of struct type
        `type_` -- the plain, generic `@festina_release` (unchanged
        from claude.md #77) for a struct with no struct/array/map-typed
        field of its own, since there is nothing for a release to
        cascade into; a dedicated, lazily-generated, per-struct-type
        wrapper (`@__festina_release_struct_<Name>`, cached in
        self._struct_release_fns so repeat callers -- and there are
        many, every release site in this file included -- never
        regenerate it) for one that has at least one.

        That wrapper does exactly what the plain runtime function can't,
        since the runtime is entirely type-blind (every value it ever
        touches is just a `void *payload`, see festina_release_check's
        own comment): decrement the refcount via festina_release_check,
        and -- only if that was the value's last reference -- release
        each of the struct's own struct/array/map-typed fields (via
        THAT field's own release function, found via _release_fn_for,
        recursing back into this same method for another struct-typed
        field) before actually freeing this struct's own storage. Every
        other field (int/float/bool/text/blob/...) is left untouched --
        text/blob are never refcounted at all (see claude.md #43's own
        note on string ownership). A struct-typed ELEMENT of an arr[T]/
        map[T]-typed field is still not individually released here --
        only the field's own container (its header) is; see todo.md for
        why that's a separate, still-open gap.

        The recursion here always terminates and can never produce a
        duplicate/infinite chain of wrapper functions, for the same
        reason claude.md #77 already gives for why reference cycles are
        structurally impossible in Festina: a struct field's type must
        always be declared *before* the struct containing it (claude.md
        #48's "declared before used" rule, the same one that already
        governs function forward references), so the graph of "which
        struct types reference which other struct types through their
        own fields" is a DAG by construction, never a cycle -- a
        struct's own release wrapper can transitively call another
        struct's, but never, even indirectly, its own."""
        if not self._struct_has_own_refcounted_field(type_.name):
            return "@festina_release"
        if type_.name in self._struct_release_fns:
            return self._struct_release_fns[type_.name]
        fn_name = f"@__festina_release_struct_{type_.name}"
        # Registered before the field loop below (which may recurse
        # back into this same method for a DIFFERENT struct type) so a
        # second, unrelated caller reaching this struct type again
        # while this one's own body is still being built -- e.g. two
        # sibling fields of some other struct both being this same
        # type -- gets the cached name immediately rather than
        # triggering a second, redundant generation.
        self._struct_release_fns[type_.name] = fn_name
        struct_ty = self.struct_llvm_name(type_.name)
        body = [f"define void {fn_name}(ptr %payload) {{", "entry:"]
        should_free = self.tmp()
        body.append(f"  {should_free} = call i8 @festina_release_check(ptr %payload)")
        cond = self.tmp()
        body.append(f"  {cond} = icmp ne i8 {should_free}, 0")
        free_label = self.label("relstruct.free")
        done_label = self.label("relstruct.done")
        body.append(f"  br i1 {cond}, label %{free_label}, label %{done_label}")
        body.append(f"{free_label}:")
        self._emit_release_struct_field_refs("%payload", type_, body)
        header = self.tmp()
        body.append(f"  {header} = getelementptr i8, ptr %payload, i64 -8")
        body.append(f"  call void @free(ptr {header})")
        body.append(f"  br label %{done_label}")
        body.append(f"{done_label}:")
        body.append("  ret void")
        body.append("}")
        body.append("")
        self.func_defs.extend(body)
        return fn_name

    def _emit_release_struct_field_refs(self, obj_ptr, type_, lines):
        """claude.md #78 (widened by claude.md #79 to arr[T]/map[T]
        fields too): releases every struct/array/map-typed field of
        `type_` directly reachable from `obj_ptr` (already-emitted IR
        for a `ptr` to that struct's own storage) -- the shared field-
        walking core both _release_fn_for_struct (a heap-allocated
        struct that's about to be freed) and
        _emit_release_nested_fields_only (a stack-allocated struct
        whose own storage is never freed, but whose field references
        still need dropping) build on. Never touches `type_`'s own
        storage or refcount header -- entirely the caller's own
        responsibility, since the two callers need different things
        done with it (free it outright, or nothing at all)."""
        struct_ty = self.struct_llvm_name(type_.name)
        for i, (_, ftype) in enumerate(self.struct_fields(type_.name)):
            if isinstance(ftype, (types_mod.StructType, types_mod.ArrayType, types_mod.MapType)):
                field_release_fn = self._release_fn_for(ftype)
                fptr = self.tmp()
                lines.append(f"  {fptr} = getelementptr {struct_ty}, ptr {obj_ptr}, i32 0, i32 {i}")
                fval = self.tmp()
                lines.append(f"  {fval} = load ptr, ptr {fptr}")
                lines.append(f"  call void {field_release_fn}(ptr {fval})")

    def _emit_release_nested_fields_only(self, ref, struct_type, lines):
        """claude.md #78: the stack-allocated counterpart to
        _release_fn_for_struct's own generated wrapper -- called from
        _emit_free_active_locals for a struct local _emit_block marked
        with _StackStructFieldsOnly. `ref` is the local's own `alloca
        ptr` slot (see _emit_stmt's VarDecl handling -- even a stack-
        allocated struct is addressed through one, so Identifier lookup
        never needs to special-case where a struct's storage actually
        lives); loading it gives a perfectly valid pointer into that
        stack storage, usable for a GEP into its own fields exactly
        like a heap pointer would be -- it must simply never reach
        festina_release/@free, since it has no refcount header and
        isn't heap memory at all."""
        obj = self.tmp()
        lines.append(f"  {obj} = load ptr, ptr {ref}")
        self._emit_release_struct_field_refs(obj, struct_type, lines)

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
            if isinstance(ttype, (types_mod.StructType, types_mod.ArrayType, types_mod.MapType)):
                if ref.startswith("@"):
                    self._emit_global_retain_release(ref, val, ttype, lines)
                else:
                    self._emit_local_retain_release(ref, val, expr.value, ttype, lines)
            lines.append(f"  store {_llvm_type(ttype)} {val}, ptr {ref}")
            return val, ttype
        if isinstance(expr.target, ast.Member):
            if expr.target.computed:
                # claude.md #72: npcHealths['npc1'] = 30 / npcHealths[key] = 30
                # claude.md #79: expr.target.obj is emitted exactly
                # once, giving obj_val -- now always the array/map's own
                # header pointer (see the module docstring's arr[T]/
                # map[T] note), the same value _emit_expr's own
                # computed-Member READ dispatch would produce for it.
                # Previously this went through _try_addressable
                # (removed), which needed to tell an "addressable" arr/
                # map (a plain variable or field, whose own STORAGE
                # SLOT could be written back through if a map grew) apart
                # from a merely "valuable" one (anything else, e.g. a
                # function call) -- ptr-to-shared-header values from
                # #79 no longer need that distinction at all: a map's
                # own festina_map_set mutates the header EVERY alias
                # already shares, not a slot private to this one
                # expression, so there's nothing left to "write back"
                # into that obj_val doesn't already give directly.
                obj_val, obj_type = self._emit_expr(expr.target.obj, env, lines)
                if isinstance(obj_type, types_mod.MapType):
                    # Still restricted to a plain variable or a non-
                    # computed field target -- unchanged from before
                    # this section (not a restriction the representation
                    # change above actually still requires, but not this
                    # section's place to relax it either; claude.md #54).
                    obj = expr.target.obj
                    addressable = isinstance(obj, ast.Identifier) or (
                        isinstance(obj, ast.Member) and not obj.computed)
                    if not addressable:
                        raise CodegenError(
                            "a map assignment target must be a plain variable or field, "
                            "not an arbitrary expression",
                            file=self.filename, line=getattr(expr.target, "line", 0))
                    key_val, _ = self._emit_expr(expr.target.prop, env, lines)
                    val, vtype = self._emit_value_for(expr.value, env, lines, obj_type.value)
                    val = self._coerce(val, vtype, obj_type.value, lines)
                    self._emit_map_set(obj_val, obj_type.value, key_val, val, lines)
                    return val, obj_type.value
                if not isinstance(obj_type, types_mod.ArrayType):
                    raise CodegenError(f"cannot index into {types_mod.type_name(obj_type)}",
                                        file=self.filename, line=getattr(expr.target, "line", 0))
                idx_val, _ = self._emit_expr(expr.target.prop, env, lines)
                ptr, ftype = self._array_elem_ptr(obj_val, obj_type, idx_val, lines)
            else:
                ptr, ftype = self._member_ptr(expr.target, env, lines)
            val, vtype = self._emit_value_for(expr.value, env, lines, ftype)
            val = self._coerce(val, vtype, ftype, lines)
            if not expr.target.computed and isinstance(
                    ftype, (types_mod.StructType, types_mod.ArrayType, types_mod.MapType)):
                # claude.md #78 (widened by claude.md #79 to arr[T]/
                # map[T]-typed fields too): `outer.field = value` -- the
                # ONLY way a struct/array/map-typed field is ever
                # populated (there's no struct/array/map-literal-as-
                # field-initializer syntax) -- gets the exact same
                # owning/aliasing retain rule as a plain local
                # reassignment, and releases whatever the field
                # previously held (always safe: a field of any of these
                # three types starts out null, per its zeroinitializer/
                # calloc'd storage, and every release function already
                # null-checks). Deliberately NOT applied to a computed
                # target (arr[i] = v / map[key] = v) even when the
                # array/map's own ELEMENT type happens to be one of
                # these three -- that's a different, still-open gap
                # (see todo.md): arr[T]/map[T] values are refcounted
                # CONTAINERS as of this section, but their own individual
                # elements/values are not yet individually tracked, so
                # there is no scope-exit release an element-level retain
                # here could ever be paired with; only a genuine FIELD,
                # on a genuine struct, is covered here.
                old = self.tmp()
                lines.append(f"  {old} = load ptr, ptr {ptr}")
                if not self._is_owning_refcounted_source(expr.value):
                    lines.append(f"  call void @festina_retain(ptr {val})")
                lines.append(f"  call void {self._release_fn_for(ftype)}(ptr {old})")
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
                    # claude.md #79: obj_val is now a `ptr` to the map's
                    # own storage, not the {count,entries} value itself
                    # -- GEP+load per field, same as _emit_map_get.
                    count_ptr = self.tmp()
                    lines.append(f"  {count_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 0")
                    count = self.tmp()
                    lines.append(f"  {count} = load i64, ptr {count_ptr}")
                    entries_ptr = self.tmp()
                    lines.append(f"  {entries_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 1")
                    entries = self.tmp()
                    lines.append(f"  {entries} = load ptr, ptr {entries_ptr}")
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
        # (true for TableType, same as StructType) -- no repacking
        # needed. claude.md #79: a fresh, uniquely-owned heap header,
        # same as _emit_array_lit's own -- a sqlite() query result is
        # exactly as "owning" a source as an array literal is (nothing
        # else references it yet).
        header = self._emit_fresh_heap_header(FESTINA_ARRAY_LLVM_TYPE, lines)
        len_ptr = self.tmp()
        lines.append(f"  {len_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}, i32 0, i32 0")
        lines.append(f"  store i64 {n_val}, ptr {len_ptr}")
        data_field_ptr = self.tmp()
        lines.append(f"  {data_field_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}, i32 0, i32 1")
        lines.append(f"  store ptr {data_val}, ptr {data_field_ptr}")
        return header

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
                # claude.md #77: a global's own declaration-with-
                # initializer is just another point its value changes
                # -- see _emit_global_retain_release's own
                # comment for why this needs the exact same retain/
                # release treatment an ordinary `g = expr` reassignment
                # gets (_emit_assign), not a plain store.
                self._emit_global_retain_release(ref, val, type_, lines)
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
