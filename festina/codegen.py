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
.replace() -- see _emit_regex_call and the Member-call
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
below), regex()/.test()/.match()/.replace() (POSIX
extended regular expressions via the festina_runtime C helpers -- no
bundled regex engine, see festina_runtime.h's doc comment on why),
img/drawRect/drawCircle/drawText/drawImage/`on mouseDown`/`on
mouseUp`/`on mouse`/`on mouseWheelUp`/`on mouseWheelDown` (a
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
are backed by Cairo" plus #40's mouse events firing against "the
canvas" only make sense together as an actual window. Opened lazily
(CodeGen.uses_graphics, set by any draw* call or an `on
mouseDown`/`on mouseUp`/`on mouse`/`on mouseWheelUp`/`on mouseWheelDown`
handler, but deliberately NOT by loadImage() alone -- decoding a
PNG needs no window; see _emit_graphics_call's own note) in main()
before __festina_main() runs, exactly
the same "only pay for what you use" pattern uses_sqlite already
follows for festina_db_open(); a program that never touches graphics
never opens a window. After __festina_main() returns, if graphics or
timers (see "Timers" below) were used, main() blocks in
festina_run_event_loop() (Expose/mouse/key/resize/window-close,
interleaved with any pending timers) until the window is closed. Canvas
size starts at
a fixed 800x600 and every shape/text draws in solid black -- claude.md
has no syntax for declaring a size or a color, so both are
implementation-defined defaults, not derived from the spec; the size
can change afterwards, though, if the window is resized (see `on
resize` below). `on mouseDown`/`on mouseUp`/`on mouse`/`on
mouseWheelUp`/`on mouseWheelDown`/`on key`/`on resize`/`on close`
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
single call to festina_audio_play_on/_is_playing; there's no IR-level
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
from . import colors as colors_mod
from .errors import CompileError

BOOL = types_mod.PrimitiveType("bool")
INT = types_mod.PrimitiveType("int")
FLOAT = types_mod.PrimitiveType("float")
TEXT = types_mod.PrimitiveType("text")
BLOB = types_mod.PrimitiveType("blob")
REGEX = types_mod.RegexType()
AUDIO = types_mod.AudioType()


def _http_send_lit_receiver(node):
    """claude.md #164: mirrors semantic.py's own identically-named
    helper exactly (see its doc comment for the full reasoning) --
    answers the inner MapLit if `node` is exactly
    `Call(Member(<MapLit>, 'send', computed=False), [])`, else None.
    Duplicated rather than imported, matching how this file and
    semantic.py already keep no cross-import dependency on each
    other."""
    if (isinstance(node, ast.Call) and len(node.args) == 0
            and isinstance(node.callee, ast.Member)
            and not node.callee.computed and node.callee.prop == "send"
            and isinstance(node.callee.obj, ast.MapLit)):
        return node.callee.obj
    return None


def _is_refcounted(t):
    """claude.md #109: the single "does this value carry a refcount
    header" test, replacing the struct/arr[T]/map[T] tuple that used to
    be written out at each of the ~15 sites that ask.

    `blob` joined that family when #109 gave it its real meaning -- a
    file's bytes rather than a second spelling of `text` -- because
    #109 also asked for `blob a = b` to SHARE one handle and for
    reassigning a blob to free whatever the old one held if nothing
    else wanted it. That is reference counting exactly, and a blob
    carries the same i64 header immediately before its payload that a
    struct does, so every generic part of the protocol
    (festina_retain, festina_release_check, the retain-then-release
    ordering at a reassignment) already worked on it unchanged. The
    only blob-specific piece is its destructor, which has two inner
    strings to free first -- dispatched through _release_fn_for the
    same way a struct's field cascade is.

    claude.md #118: img, aud and regex joined the family too, for the
    same reason blob did -- each now carries the identical i64 header
    (allocated by festina_image_box / festina_audio_from_bytes /
    festina_regex_compile) and each has a destructor with contents of
    its own to free, dispatched through _release_fn_for exactly like
    blob's. That closed the "escaping img/aud handle leaks" and
    "`free` on an aliased img/aud dangles" gaps in one move: every
    binding owns exactly one countable reference, wherever the value
    came from, so releasing at scope exit / reassignment / `free` is
    always a safe decrement. A /pattern/ regex literal's process-
    lifetime cache is the standard immortal sentinel (negative
    header), so it flows through every one of these paths as a no-op
    with no special case left in this file.

    `text` is deliberately NOT here. A text is managed but not
    refcounted: it is copied on alias (festina_text_own) and freed
    outright, so it needs its own branch wherever ownership is decided
    -- see claude.md #83.

    claude.md #151: http and socket joined the family too, for the
    same reason img/aud did -- each request/socket handle
    festina_runtime_http.c hands out carries the identical i64 header
    (festina_handle_new), so retain/reassignment/`free`/scope-exit
    release all just work unchanged. Unlike img/aud, releasing one of
    these never frees anything about the underlying CONNECTION (owned
    separately by the connection table, see festina_runtime.h) -- only
    the tiny handle itself, dispatched through
    festina_release_conn_handle (_release_fn_for), shared by both
    types since neither has more than the one shape."""
    # claude.md #174: ArrayType's own `amortized` field (set by the
    # `amor` prefix) changes internal representation and growth
    # behavior, not refcounted-ness -- an isinstance check against
    # ArrayType already matches both the plain and amortized case, so
    # no separate entry is needed here. MapType has no such field any
    # more -- claude.md #175 removed `amor map[T]` outright.
    # claude.md #176: EnumType joined this family too -- both of its
    # runtime representations own a reference to their own current
    # payload (either the pointer IS the payload, self-tagged struct
    # case, or a small heap-boxed {tag, value} wrapper owns it, mixed
    # case), so an enum-typed value needs the identical retain-on-
    # alias/release-on-reassignment/release-at-scope-exit treatment
    # every other refcounted type already gets, with no special-casing
    # needed anywhere else in this generic protocol.
    return (isinstance(t, (types_mod.StructType, types_mod.ArrayType,
                           types_mod.MapType, types_mod.ImageType,
                           types_mod.AudioType, types_mod.RegexType,
                           types_mod.HttpType, types_mod.SocketType,
                           types_mod.UrlType, types_mod.EnumType))
            or t == BLOB)

FESTINA_ARRAY_LLVM_TYPE = "%struct._FestinaArray"
# claude.md #174: amor arr[T] -- an "amortized array". Byte-compatible
# with FESTINA_ARRAY_LLVM_TYPE's own {i64 length, ptr data} prefix
# (same two fields, same order, same offsets), with one trailing i64
# capacity field FESTINA_ARRAY_LLVM_TYPE doesn't have -- a common-
# PREFIX-not-an-inserted-field trick, kept for arrays even though
# claude.md #175 later gave map[T] itself a single universal capacity-
# tracking header instead of a separate amortized variant (there is no
# plain arr[T]-vs-amor-arr[T] merge here -- arrays are unaffected by
# that change). For the same reason as always: every
# array runtime function/codegen touchpoint that only ever reads
# length/data (indexing, `.length`, iteration, JSON rendering, the
# retain/release element cascade, ...) is directly reusable on an
# amor array's own payload unchanged -- only the five growable-buffer
# operations (push/pop/shift/unshift/splice) that can actually GROW
# the backing buffer need to know capacity exists at all, and thread it
# through festina_array_resize's own capacity-aware counterpart. See
# that function's own comment in runtime/festina_runtime.c for the
# full layout reasoning.
FESTINA_AMOR_ARRAY_LLVM_TYPE = "%struct._FestinaAmorArray"

# claude.md #72, rebuilt into a real hash table by #175: map[T] --
# kept as its own distinct LLVM type name rather than reusing
# FESTINA_ARRAY_LLVM_TYPE: the two are never interchangeable (a map's
# `ptr` field points at an open-addressing hash table of FestinaMapEntry
# {key, value} buckets, not raw element values, and only
# festina_map_set/_get/_for_each/_delete know how to read it), so
# giving them separate names catches an accidental mix-up in the IR
# itself rather than relying on convention alone. `{ i64 count, ptr
# entries, i64 capacity, i64 tombstones }` -- count is the live entry
# count, capacity the bucket array's length (a power of two), tombstones
# the count of deleted-but-not-yet-reclaimed buckets. A single universal
# shape: unlike claude.md #156's now-removed `amor map[T]`, plain
# map[T] itself grows geometrically (doubling capacity, same as
# festina_conn_index's own prior-art hash table in
# festina_runtime_http.c) as an intrinsic part of being a hash table,
# so there is no separate "amortized" variant left to give a byte-
# compatible-prefix shape to. See festina_map_set's own comment in
# runtime/festina_runtime.c for the full layout reasoning.
FESTINA_MAP_LLVM_TYPE = "%struct._FestinaMap"
# claude.md #91: the compiled shape of a `font` value -- see _llvm_type's
# own FontType branch and _emit_font_constant.
FESTINA_FONT_LLVM_TYPE = "%struct._FestinaFont"
# claude.md #176: the "mixed" enum representation -- an enum with at
# least one non-struct member (int/text/arr[T]/map[T]/any built-in
# handle type) is a small, independently heap-allocated, ordinary-
# refcounted-header box around this payload, `{ ptr tag, i64 value }` --
# `tag` an interned type-name string constant (see _enum_tag_const),
# `value` the member's own value reinterpreted through the identical
# i64 marshaling map[T]'s own values already use
# (_map_value_to_i64/_i64_to_map_value). ONE universal shape for every
# mixed enum, regardless of which specific enum or how many members it
# has -- the same "one fixed-size slot, T-agnostic" convention map[T]
# entries already use. A PURE-STRUCT enum (every member a struct) needs
# no shape of its own at all -- it's just a `ptr` to whichever member
# struct it currently holds, self-tagged in that struct's own widened
# header (see _emit_fresh_heap_header's own comment) instead.
FESTINA_ENUM_BOX_LLVM_TYPE = "%struct._FestinaEnumBox"

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

# claude.md #93: float -> float. LLVM has real intrinsics for most of
# these, which optimize and constant-fold in ways an opaque libm call
# can't; the rest are declared straight from libm, which is already on
# every link line (cli.py's link_libs has -lm unconditionally), so none
# of this costs a new dependency. Both kinds are emitted identically --
# a `call double @<name>(double)` -- so the split is purely about which
# name to use.
MATH_FLOAT_FNS = {
    "sqrt": "llvm.sqrt.f64", "sin": "llvm.sin.f64", "cos": "llvm.cos.f64",
    "exp": "llvm.exp.f64", "log": "llvm.log.f64", "log2": "llvm.log2.f64",
    "log10": "llvm.log10.f64", "abs": "llvm.fabs.f64",
    # no LLVM intrinsic for these -- libm directly
    "tan": "tan", "asin": "asin", "acos": "acos", "atan": "atan",
}
MATH_FLOAT2_FNS = {
    "pow": "llvm.pow.f64",
    # llvm.minnum/maxnum match the IEEE-754 minNum/maxNum that every
    # other language's Math.min/max implements (NaN-tolerant: if one
    # operand is NaN the other is returned).
    "min": "llvm.minnum.f64", "max": "llvm.maxnum.f64",
    "atan2": "atan2",
}


class CodegenError(CompileError):
    def __init__(self, message, **kw):
        kw.setdefault("category", "not implemented")
        super().__init__(message, **kw)


class _StmtList:
    """The one thing escape_analysis.find_escaping_names actually reads
    off an ast.Block -- its `.body` list. Lets the top-level statement
    list be analyzed by the same function without inventing a synthetic
    ast.Block (which carries a source position this list has no single
    one of). See _emit_main_and_entry."""

    __slots__ = ("body",)

    def __init__(self, body):
        self.body = body


def _elem_size(t):
    """The byte stride of one arr[T] element slot.

    This has to agree EXACTLY with the stride _array_elem_ptr's
    `getelementptr <elem type>` walks with, because the runtime's array
    helpers move elements with memmove over a size the compiler hands
    them while indexing still goes through that GEP. Everything Festina
    stores in an array is 8 bytes wide -- i64, double, and every `ptr`
    -- except bool, which is i8 (see _llvm_type's own note on why bool
    is not i1). Assuming 8 across the board, as this originally did,
    made push() on an arr[bool] write to byte 8*i while the read at
    xs[i] looked at byte i: the value went in and a neighbouring
    element's byte came back out. Confirmed, then fixed here rather
    than by widening bool's storage, which would change every bool
    array's layout for one method's convenience."""
    return 1 if t == BOOL else 8


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
    if isinstance(t, types_mod.EnumType):
        # claude.md #176: always `ptr`, regardless of which of the two
        # representations this specific enum happens to use -- a pure-
        # struct enum value IS whichever member struct's own pointer it
        # currently holds (self-tagged, no wrapping); a mixed enum
        # value is a `ptr` to its own small heap-boxed {tag, value}
        # pair. Either way, from this generic "what LLVM shape does a
        # scalar-passed value of this type have" question's own point
        # of view, it's just `ptr` -- the same answer ColorType/
        # FontType/FuncType already give for their own, different
        # reasons.
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
    if isinstance(t, (types_mod.HttpType, types_mod.SocketType)):
        # claude.md #151: a tiny {refcount, conn_id} handle, opaque to
        # codegen -- see festina_runtime.h's own doc comment for the
        # full representation.
        return "ptr"
    if isinstance(t, types_mod.UrlType):
        # claude.md #162: an opaque FestinaUrlValue*, same "codegen
        # never sees the real struct layout, only a ptr threaded
        # through dedicated accessor calls" shape http/socket already
        # have.
        return "ptr"
    if isinstance(t, types_mod.ColorType):
        # claude.md #91: a packed 0xRRGGBB integer (negative for 'none')
        # -- see _pack_color. One register, and an integer compare is
        # all that comparing two colours costs.
        return "i64"
    if isinstance(t, types_mod.FontType):
        # claude.md #91: a pointer to the static %struct.FestinaFont
        # constant codegen emitted for this font's own literal -- see
        # _emit_font_constant. Read-only data, never allocated or freed.
        return "ptr"
    if isinstance(t, types_mod.MapType):
        # claude.md #79: see the ArrayType branch above -- identical
        # reasoning, FESTINA_MAP_LLVM_TYPE's own `{i64, ptr, i64, i64}`
        # shape (claude.md #175) still describes the storage this
        # points at, an implementation detail invisible at this level.
        return "ptr"
    if isinstance(t, types_mod.FuncType):
        # claude.md #141: a bare LLVM function pointer -- like
        # FontType's static constant pointer just above, this is never
        # allocated or freed (a declared function is immortal for the
        # process's whole lifetime), so it rides every generic scalar-
        # shaped code path (VarDecl, struct field, array element, map
        # value, argument passing) unchanged, the identical way
        # ColorType/FontType already do.
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


class _TryFrameMarker:
    """claude.md #157: a placeholder entry in CodeGen._active_free_locals
    marking "a try block's own runtime catch-frame is still open here" --
    not a local variable at all, but it needs the exact same "pop this
    on every exit path from this scope, however that exit happens"
    treatment a real local's cleanup gets, so it rides the same
    machinery rather than needing its own separate tracking stack. See
    _emit_try's own comment for why this frame is pushed in its own,
    dedicated entry (wrapping _emit_block's call for the try body,
    rather than living inside that call's own frame)."""


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
    def __init__(self, analyzed, filename="main.f", target="native"):
        self.analyzed = analyzed
        self.entry_filename = filename         # the file actually passed to the compiler -- see generate()
        self.filename = filename               # mutated per top-level statement (see generate()); used by every error site
        # claude.md #148: WASM export. Every native target festina/cli.py
        # builds for (x86_64/aarch64 Linux/macOS/Windows) is 64-bit, so
        # nothing before this needed to track pointer width at all --
        # wasm32-wasi is the first 32-bit target, and its libc declares
        # calloc/malloc/etc. with a genuinely 32-bit size_t. LLVM
        # requires a call site to match its callee's declared signature
        # EXACTLY (no implicit truncation the way a real C compiler
        # would insert one), so passing Festina's own internal i64 size
        # arithmetic straight through produced a real link-time
        # "function signature mismatch: calloc" trap on real wasm32-wasi
        # -- confirmed directly, not just reasoned about. self.pointer_bits
        # is consulted only at the small handful of external-libc-call
        # sites that actually need it (_emit_calloc/_emit_malloc below);
        # every internal ptrtoint/inttoptr Festina already does to
        # compute its own struct/array sizes needs no change at all,
        # since LLVM defines those conversions to correctly zero-extend/
        # truncate against whatever the target's real pointer width is.
        self.target = target
        self.pointer_bits = 32 if target == "wasm32-wasi" else 64
        self.structs = analyzed.structs       # name -> {field: Type}
        self.struct_order = list(analyzed.structs.keys())
        self.tables = analyzed.tables          # name -> {field: festina-type-name}
        self.enums = analyzed.enums            # claude.md #176: name -> semantic._EnumInfo
        # claude.md #176: any struct that's a member of at least one
        # PURE-STRUCT enum needs its own per-instance heap header
        # widened by one word (a hidden type tag) -- computed once, up
        # front, so every struct allocation site can just check
        # membership. A struct that only ever appears in a MIXED
        # enum's member list is untouched -- that case's tag lives in
        # the enum's own heap-boxed wrapper instead (see
        # _release_fn_for_enum/_coerce's own enum-construction
        # comments), not in the struct's own header.
        self._tagged_structs = set()
        for _info in self.enums.values():
            if _info.is_pure_struct:
                self._tagged_structs.update(m.name for m in _info.members)
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
        self._array_release_fns = {}           # claude.md #80: types_mod.type_name(ArrayType) -> LLVM
                                                # function name of its own lazily-generated, per-
                                                # element-type release wrapper -- see
                                                # _release_fn_for_array's own comment. Only ever
                                                # populated for an arr[T] whose own T is itself
                                                # refcounted (struct/arr/map); every other arr[T]
                                                # keeps using the plain, generic @festina_release_array
                                                # directly, unchanged from claude.md #79.
        self._map_release_fns = {}             # claude.md #80: the map[T] counterpart to
                                                # self._array_release_fns -- see
                                                # _release_fn_for_map's own comment.
        self._enum_release_fns = {}            # claude.md #176: enum name -> LLVM function name of
                                                # its own lazily-generated release dispatcher -- see
                                                # _release_fn_for_enum's own comment.
        self._table_row_release_fns = {}       # claude.md #85: table name -> LLVM function name of its
                                                # own lazily-generated per-row release function, freeing
                                                # one sqlite result row's text/blob columns and then the
                                                # row buffer itself. Deliberately NOT reachable through
                                                # _release_fn_for: a sqlite row has no refcount header
                                                # (see festina_sqlite_collect_rows, a plain malloc), so
                                                # it is owned solely by the arr[T] holding it and must
                                                # only ever be freed by that array's own release --
                                                # never by an arbitrary TableType-typed binding, which
                                                # would double-free a row the array still owns.
        self.extra_globals = []                # globals discovered while emitting main() (e.g. table column arrays)
        self.entry_stmts = []                  # top-level statements for __festina_main
        self.func_decls = {}                   # name -> ast.FuncDecl (for signatures)
        self.cur_block = None                  # label of the block currently being emitted into
        self.uses_sqlite = False               # any sqlite() call anywhere -- see _emit_sqlite_call
        self._table_arrays_cache = {}          # table name -> (names_global, types_global, ncols);
                                                # schema sync and query codegen can both ask for the
                                                # same table's column-name/type globals, and emitting
                                                # them twice would redefine the same LLVM global names
        self.uses_graphics = False             # any draw* call or an `on mouseDown`/`on mouse` handler
                                                # anywhere -- NOT loadImage() alone; see
                                                # _emit_graphics_call and _emit_main_and_entry
        self.event_handlers = {}               # "mouseDown"/"mouse" -> @__festina_on_<name> -- see
                                                # _emit_event_handler and _emit_main_and_entry
        self.exit_handler_symbol = None        # claude.md #131: @__festina_on_exit if `on exit(code:int)`
                                                # was declared, else None -- registered unconditionally
                                                # in main() (close() works with or without a window), so
                                                # tracked separately from event_handlers' graphics-only six.
        self.uses_timers = False               # any setTimeout()/setInterval() call anywhere --
                                                # NOT clearTimeout()/clearInterval() alone; see
                                                # _emit_timer_call and _emit_main_and_entry
        self.uses_async_io = False             # claude.md #165: any blob/img/aud `.callback()` call
                                                # anywhere -- links festina_runtime_async.c (the
                                                # generic background-load worker pool) and, like
                                                # uses_timers, guarantees SOME loop runs even if
                                                # nothing else in the program would otherwise need
                                                # one (see _emit_main_and_entry's own loop-selection)
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
        self.uses_try = False                  # claude.md #157: any try/throw anywhere -- unlike
                                                # uses_graphics/uses_audio, festina_throw's own
                                                # __builtin_longjmp is unconditional core, so this
                                                # exists purely so compile_file can reject a
                                                # wasm32-wasi build outright (there is no SjLj
                                                # support for that target at all), the same way
                                                # uses_exec below already does.
        self._exec_callback_trampoline = None  # claude.md #177: lazily-generated, cached LLVM
                                                # symbol name for the ONE generic trampoline every
                                                # exec(args, callback) call site shares -- see
                                                # _emit_exec_callback_trampoline's own comment on
                                                # why a single generic trampoline (data-driven via
                                                # the payload) is right here, unlike
                                                # _emit_map_value_release_trampoline's own fresh-
                                                # per-call-site shape.
        self._sort_trampolines = {}            # claude.md #184: types_mod.type_name(element) ->
                                                # LLVM symbol name for that element type's qsort()
                                                # comparator trampoline -- one per DISTINCT element
                                                # type a program calls .sort() on (the trampoline has
                                                # to decode that type's own raw slot, so unlike
                                                # _exec_callback_trampoline above it can't be a single
                                                # shared symbol), cached the same way
                                                # _array_release_fns below already keys per-type
                                                # helpers.
        self.uses_exec = False                 # claude.md #150: any exec() call anywhere -- unlike
                                                # uses_graphics/uses_audio, festina_process_exec is
                                                # unconditional core (no extra library to
                                                # conditionally link -- fork/exec/waitpid are plain
                                                # libc), so this exists purely so compile_file can
                                                # reject a wasm32-wasi build outright, before doing
                                                # any real work, the same way needs_graphics/
                                                # needs_audio already do for that target (WASI has
                                                # no process model to spawn into at all -- see
                                                # wasm.md's Limitations section)
        self.uses_http = False                 # claude.md #151: openPort()/closePort()/any
                                                # http-or-socket-typed value anywhere (including
                                                # an `on request`/`on upgrade`/`on message`/
                                                # `on socketClose` handler even with no direct
                                                # openPort() call in sight) -- both a linking
                                                # signal (festina_runtime_http.c) and a real
                                                # main()/loop-selection branch, same dual role
                                                # uses_graphics/uses_timers already have. Also
                                                # used by cli.py to reject a wasm32-wasi build
                                                # outright (WASI has no listening-socket support)
                                                # and to gate macOS/Windows the same
                                                # "exists, unverified/unbuilt" way audio/graphics
                                                # already do -- see _check_platform_feature_supported.
        self.http_request_handler_symbol = None
        self.http_upgrade_handler_symbol = None
        self.http_message_handler_symbol = None
        self.http_socketclose_handler_symbol = None
        self.uses_https = False                # claude.md #160: openSecurePort() specifically --
                                                # narrower than uses_http (set alongside it, see
                                                # openSecurePort's own dispatch branch below): a
                                                # program can use openPort()/on request/... with no
                                                # TLS involved at all, and that must never pull in
                                                # mbedTLS. Drives cli.py's own festina_runtime_https.c
                                                # linking + -lmbedtls/-lmbedx509/-lmbedcrypto (only
                                                # when true) and the festina_register_tls_hooks()
                                                # call in main() below (see _emit_main_and_entry).

        # claude.md #102: a table column of type aud/img makes the
        # program use that feature, whether or not it ever names a
        # graphics or audio function. Two things need it: main()
        # registers the media decoders so a stored BLOB can become a
        # handle again (claude.md #101), and the per-table row release
        # function calls that type's destructor. Both emit calls into a
        # translation unit that would otherwise not be linked at all --
        # so without this a program whose ONLY use of audio is
        # `file:aud` in a table failed at the LINK step with an
        # undefined reference to festina_audio_free, which is a
        # compiler bug reported as a linker error.
        for _cols in self.tables.values():
            for _col_type in _cols.values():
                if _col_type == "aud":
                    self.uses_audio = True
                elif _col_type == "img":
                    self.uses_graphics_code = True
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
                                                # skips all of #74's tracking entirely in that case.
                                                # claude.md #140: no LONGER guaranteed only one function/
                                                # handler body is being emitted at a time -- a nested
                                                # FuncDecl (see _emit_stmt's own FuncDecl branch) re-
                                                # enters _emit_analyzed_func_body recursively while an
                                                # outer one's own body is still being walked, so this is
                                                # save/restored around that recursive call rather than
                                                # reset to a hardcoded None -- see that method's own
                                                # comment.
        self.escaping_params = {}              # claude.md #74 stage 2 (interprocedural): {func_name:
                                                # set[int]} -- for each FuncDecl already fully emitted,
                                                # which of ITS OWN parameter positions escape_analysis
                                                # proved escape somewhere in its own body. Built up
                                                # incrementally, one entry per function, immediately
                                                # after that function's own _emit_analyzed_func_body call
                                                # returns (see that method) -- never all at once in a
                                                # separate pass. claude.md #140: hoisting means a callee
                                                # is no longer guaranteed to already be a key here by the
                                                # time some EARLIER-emitted function's body calls it (a
                                                # genuine forward reference, now allowed) -- exactly like
                                                # the self-recursive case already below, a miss here is
                                                # always SAFE, just a missed optimization: escape_
                                                # analysis.py's own docstring spells out why a map miss
                                                # (self-recursion, a forward reference, a builtin, ...)
                                                # falls back to the original conservative "any call
                                                # argument escapes" default rather than ever being treated
                                                # as "proven not to escape." Never cleared, never popped:
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
                                                # returning exits every nested scope simultaneously, down
                                                # to self._current_func_frame_base -- see that field's own
                                                # comment -- not literally always 0); a Break/Continue
                                                # frees only the frames opened since the nearest enclosing
                                                # loop's own body began (down_to = the frame index recorded
                                                # alongside that loop's own entry in self._loop_targets); a
                                                # block's own natural, non-terminated fall-through exit
                                                # frees just its own single (topmost) frame. Same
                                                # "instance-level stack, not threaded through ctx" shape as
                                                # _loop_targets, for the same reason: it needs to keep
                                                # working correctly through arbitrary nesting depth.
        self._current_func_frame_base = 0      # claude.md #140: the self._active_free_locals index of
                                                # the OUTERMOST frame belonging to the function/handler
                                                # currently being emitted -- normally 0, since
                                                # self._active_free_locals is always empty entering any
                                                # top-level _emit_func/_emit_event_handler call. But a
                                                # nested FuncDecl (see _emit_stmt's own FuncDecl branch)
                                                # re-enters _emit_func recursively while an OUTER
                                                # function's own frames are still open beneath it on the
                                                # SAME shared stack -- without this, a bare `return` inside
                                                # the nested function's own body (down_to=0, unqualified)
                                                # would free every frame all the way down, including the
                                                # outer function's still-live locals it has no business
                                                # touching. Save/restored around _emit_func's own
                                                # push/pop, exactly like self._current_escaping_names --
                                                # see that field's own comment for the identical reasoning.
        self._font_constants = {}              # claude.md #91: (px, style, family) -> the name of the
                                                # static %struct._FestinaFont constant holding it.
                                                # Keyed on the RESOLVED parts rather than the source
                                                # text, so 'bold 13px arial' and 'arial bold 13px'
                                                # share one constant.
        self._json_fns = {}                    # claude.md #114: type_name -> its generated JSON
                                                # render function; registered before the body is
                                                # generated, so a self-referencing struct recurses
                                                # into its own function instead of generating forever
        self._from_json_fns = {}               # claude.md #159: "struct:Name" / "arr:elemtype" ->
                                                # its generated .toStruct()/.toArr() parsing function,
                                                # cached the same way self._json_fns is above (the
                                                # opposite direction -- parsing rather than rendering).
        self._row_to_struct_fns = set()        # claude.md #112: row->struct converters already
                                                # generated, one per struct type used as a
                                                # sqlite() target
        self._regex_lit_cache = {}             # id(ast.RegexLit node) -> its private cache global's
                                                # name -- see _emit_cached_regex_lit; keyed by node
                                                # identity (not pattern text) so two textually
                                                # identical /pattern/flags literals at different
                                                # source locations still each get their own slot,
                                                # simpler than deduplicating by text and just as
                                                # correct (each still only ever compiles once)
        self._regex_memo_slots = {}            # claude.md #118: id(regex() Call node) -> its private
                                                # [3 x ptr] memo slot global ({pattern copy, flags
                                                # copy, compiled}) -- see _emit_regex_call /
                                                # festina_regex_compile_memo
        self._cyclic_type_cache = {}           # claude.md #120: type_name -> bool ("can this type
                                                # reach itself through managed edges") -- the gate on
                                                # ALL cycle-collection machinery; acyclic types never
                                                # generate or run any of it
        self._cycle_fns = {}                   # claude.md #120: (op, type_name) -> generated cycle
                                                # traversal function name (ops: gray/scan/black/white
                                                # + the grayedge/blackedge container edge helpers)
        self._minted_values = set()            # claude.md #119: id(expr) of every emitted expression
                                                # whose EMISSION minted its own +1 (a retained
                                                # computed-index element, or a chain whose escaping
                                                # value _release_member_chain retained/copied) --
                                                # consulted by the ownership predicates so they and
                                                # the emission can never disagree about shapes syntax
                                                # alone cannot classify (a computed member over an
                                                # owning receiver mints for a refcounted/text element
                                                # but NOT for a borrowed table row). Recording happens
                                                # during emission, and every predicate consumer runs
                                                # after the value it asks about was emitted.
        # claude.md #108: state for a MEMBER CHAIN's deferred receiver
        # release -- see _emit_member_load. _chain_receiver holds the
        # exact AST node the enclosing member load is about to emit as
        # its receiver, so a nested member load can recognize, by node
        # IDENTITY, that it is part of a chain rather than an unrelated
        # subexpression (a call argument, an index) that merely happens
        # to be emitted while a chain is in flight. _chain_pending
        # collects the receivers those nested frames produced, for the
        # outermost frame to decide about once it knows the type of the
        # value that actually escapes the whole chain.
        self._chain_receiver = None
        self._chain_pending = []

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
        # claude.md #150: argv -- registered here rather than discovered
        # by walking program.body (the way every user-declared global
        # is, in _toplevel below) since there is no VarDecl AST node for
        # it at all; semantic.py's own analyze() already pre-registers
        # the SAME name into its global scope (so redeclaring `argv` is
        # a duplicate-declaration error like any other reserved global,
        # exactly the way clientWidth/environment are already handled),
        # so this and that are the two places aware of it, matching
        # every other pre-registered global's own split between the
        # two stages. Once this line runs, `argv` behaves EXACTLY like
        # an ordinary global arr[text] variable to every other piece of
        # codegen -- _global_var_defs emits its {refcount|len,data}
        # header with no special-casing at all, an ordinary Identifier
        # read/copy/free/reassignment all just work -- only its INITIAL
        # value is special, populated from the real argc/argv `main`
        # receives rather than a user-written init expression (see
        # _emit_main_and_entry).
        self.global_env.define("argv", "@argv", types_mod.ArrayType(TEXT))
        self.database_url_expr = getattr(program, "database_url", None)
        # claude.md #140: every function's signature is registered before
        # ANY code is emitted -- "hoisting" -- so a call reached earlier
        # in this same walk than its own callee's declaration still
        # resolves. filename is threaded the identical way the real
        # emission pass below threads it (reset right before each
        # TOP-LEVEL statement, since only top-level statements carry
        # their own `.file` -- see festina.imports.build_program --
        # and everything nested under one shares that statement's file),
        # even though this pre-pass itself never actually raises (a
        # signature collision was already caught by semantic analysis,
        # which ran first).
        for stmt in program.body:
            self.filename = getattr(stmt, "file", self.filename)
            self._register_all_func_signatures([stmt])
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
            "declare void @festina_runtime_init()",
            "declare void @festina_log_int(i64)",
            "declare void @festina_log_float(double)",
            # i8, not i1 -- see the module docstring's "Null for bool"
            # note. Every one of the i8-typed bool declares below matches
            # this runtime function's own C signature exactly (int8_t,
            # not _Bool/int) -- these used to say i1 despite that, a
            # latent ABI mismatch that happened to work only because 0/1
            # were the only values ever produced.
            "declare void @festina_log_bool(i8)",
            # claude.md #157: try/catch. _emit_try emits the actual
            # setjmp call directly (see its own docstring for why a
            # runtime-side wrapper doesn't work) via these four
            # intrinsics -- the same, portable, fixed-size-buffer
            # mechanism clang lowers __builtin_setjmp/__builtin_longjmp
            # to. festina_try_push registers the buffer that direct
            # setjmp call just populated as the new top catch frame
            # (only on its normal, 0-returning arrival); festina_try_pop
            # pops the current top frame on a NORMAL exit from a try
            # body (_emit_free_active_locals's own _TryFrameMarker
            # handling is the only generated-code caller); festina_try_error
            # hands over (and releases the runtime's own ownership of)
            # the thrown message as an ordinary owned text value;
            # festina_throw never returns.
            "declare ptr @llvm.frameaddress.p0(i32 immarg)",
            "declare ptr @llvm.stacksave.p0()",
            "declare i32 @llvm.eh.sjlj.setjmp(ptr)",
            "declare void @llvm.eh.sjlj.longjmp(ptr)",
            "declare void @festina_try_push(ptr)",
            "declare void @festina_try_pop()",
            "declare ptr @festina_try_error()",
            "declare void @festina_throw(ptr)",
            "declare void @festina_log_text(ptr)",
            "declare void @festina_fail(ptr)",
            # claude.md #158: troubleshoot()/fail()'s structured form --
            # both assemble a JSON envelope around already-rendered
            # text (codegen's own _to_text on each argument).
            "declare void @festina_troubleshoot(ptr, ptr)",
            "declare void @festina_fail_structured(ptr, ptr)",
            # claude.md #159: .toStruct()/.toArr() JSON parsing -- see
            # runtime/festina_runtime.c's own comment on this whole
            # group (every one either succeeds or throws internally).
            "declare ptr @festina_json_cursor_new(ptr)",
            "declare void @festina_json_cursor_free(ptr)",
            "declare void @festina_json_expect_end(ptr)",
            "declare void @festina_json_object_start(ptr)",
            "declare void @festina_json_array_start(ptr)",
            "declare i8 @festina_json_object_next(ptr, ptr)",
            "declare i8 @festina_json_array_next(ptr, ptr)",
            "declare ptr @festina_json_read_key(ptr)",
            "declare i8 @festina_json_key_matches(ptr, ptr)",
            "declare void @festina_json_skip_field_value(ptr)",
            "declare i64 @festina_json_read_int(ptr)",
            "declare double @festina_json_read_float(ptr)",
            "declare i8 @festina_json_read_bool(ptr)",
            "declare ptr @festina_json_read_text(ptr)",
            "declare ptr @festina_str_from_int(i64)",
            "declare ptr @festina_str_from_float(double)",
            "declare ptr @festina_str_from_bool(i8)",
            "declare ptr @festina_str_concat(ptr, ptr)",
            # claude.md #83: text values are copy-managed, not
            # refcounted -- this is the "make my own exclusive copy"
            # primitive every aliasing text binding site calls (a
            # NULL-safe strdup); freeing uses plain @free directly
            # (also NULL-safe), no dedicated release function needed.
            "declare ptr @festina_text_own(ptr)",
            # claude.md #93: math (libm/LLVM intrinsics), files, time
            *[f"declare double @{fn}(double)" for fn in sorted(set(MATH_FLOAT_FNS.values()))],
            *[f"declare double @{fn}(double, double)" for fn in sorted(set(MATH_FLOAT2_FNS.values()))],
            "declare double @festina_random()",
            # claude.md #109: blob is a real handle now -- bytes loaded
            # from a path, refcounted, with the file operations that
            # used to be free functions hanging off it.
            "declare ptr @festina_blob_open(ptr)",
            # claude.md #165: <text>.callback(fn) -- non-blocking blob
            # loading, the file-loading counterpart to claude.md #163's
            # http client callback.
            "declare ptr @festina_blob_load_dispatch(ptr, ptr)",
            "declare void @festina_register_async_io_hooks()",
            "declare ptr @festina_blob_from_bytes(ptr, i64)",
            "declare void @festina_blob_release(ptr)",
            "declare ptr @festina_blob_to_text(ptr)",
            "declare ptr @festina_blob_bytes(ptr, ptr)",
            "declare i8 @festina_blob_write(ptr, ptr)",
            "declare i8 @festina_blob_append(ptr, ptr)",
            "declare i8 @festina_blob_exists(ptr)",
            "declare i8 @festina_blob_delete(ptr)",
            # claude.md #110: save()/saveCopy(), one pair per handle type.
            "declare i8 @festina_blob_save(ptr, ptr)",
            "declare i8 @festina_blob_save_copy(ptr, ptr)",
            # claude.md #111/#175: `delete m[key]` -- count_ptr/
            # tombstones_ptr are out-params (delete may remove a live
            # entry or convert one to a tombstone), capacity is read-only
            # (delete never grows the table) -- and the guard that lets
            # `free` work on a regex without corrupting a literal cache.
            "declare i8 @festina_map_delete(ptr, ptr, i64, ptr, ptr, ptr)",
            "declare void @festina_regex_mark_cached(ptr)",
            "declare ptr @festina_read_file(ptr)",
            "declare i8 @festina_write_file(ptr, ptr)",
            "declare i8 @festina_append_file(ptr, ptr)",
            "declare i8 @festina_file_exists(ptr)",
            "declare i8 @festina_delete_file(ptr)",
            "declare i64 @festina_now_ms()",
            "declare ptr @festina_format_time(i64, ptr)",
            # claude.md #132
            "declare i8 @festina_mkdir(ptr)",
            "declare ptr @festina_ls(ptr)",
            "declare i8 @festina_str_eq(ptr, ptr)",
            # claude.md #150: text.toInt()/text[i], argv, exec().
            "declare i64 @festina_text_to_int(ptr)",
            "declare ptr @festina_text_char_at(ptr, i64)",
            "declare ptr @festina_argv_array(i32, ptr)",
            "declare i64 @festina_process_exec(ptr)",
            # claude.md #177: exec(args, callback) -- the non-blocking
            # counterpart just above.
            "declare void @festina_process_exec_dispatch(ptr, ptr, ptr)",
            "declare i64 @strlen(ptr)",
            # claude.md #151: openPort/on request/on upgrade/on message/
            # on socketClose -- see festina_runtime.h's own extensive
            # doc comment right above these declarations for the whole
            # design (single-threaded event loop, the handle
            # representation, http/1.1 and WebSocket scope).
            "declare void @festina_open_port(i64)",
            "declare void @festina_close_port(i64)",
            # claude.md #160: openSecurePort() -- see
            # festina_runtime.h's own doc comment right above these
            # two declarations. festina_register_tls_hooks() is called
            # from main() only when self.uses_https (see
            # _emit_main_and_entry) -- see festina_runtime_https.c's
            # own top comment for why this is the ONLY TLS-related
            # symbol this module ever references, cross-translation-
            # -unit hook wiring aside.
            "declare void @festina_open_secure_port(i64, ptr, i64)",
            "declare void @festina_register_tls_hooks()",
            # claude.md #162: url / parseURL() -- lives in CORE
            # (festina_runtime.c), see festina_runtime.h's own doc
            # comment right above these declarations.
            "declare ptr @festina_parse_url(ptr)",
            "declare ptr @festina_url_protocol(ptr)",
            "declare ptr @festina_url_username(ptr)",
            "declare ptr @festina_url_password(ptr)",
            "declare ptr @festina_url_hostname(ptr)",
            "declare i64 @festina_url_port(ptr)",
            "declare ptr @festina_url_pathname(ptr)",
            "declare ptr @festina_url_hash(ptr)",
            "declare ptr @festina_url_search_params(ptr)",
            "declare void @festina_release_url(ptr)",
            "declare void @festina_register_request_handler(ptr)",
            "declare void @festina_register_upgrade_handler(ptr)",
            "declare void @festina_register_message_handler(ptr)",
            "declare void @festina_register_socketclose_handler(ptr)",
            "declare void @festina_run_http_loop()",
            # claude.md #166: registered from main() whenever
            # self.uses_http (see _emit_main_and_entry) -- lets
            # festina_run_event_loop (festina_runtime_graphics.c)
            # service an open port too, which is what makes combining
            # openPort() with graphics possible at all.
            "declare void @festina_register_http_service_hooks()",
            # claude.md #162: http -- redesigned into a genuine
            # refcounted value (url/method/code/headers/body), see
            # festina_runtime.h's own doc comment for the full
            # rationale. claude.md #163 adds `callback` as a 7th
            # constructor argument (a bare func pointer, `null` for
            # none) and its own read-back accessor.
            "declare ptr @festina_http_literal_new(ptr, ptr, i64, ptr, ptr, i64, ptr)",
            "declare ptr @festina_http_url(ptr)",
            "declare ptr @festina_http_method(ptr)",
            "declare i64 @festina_http_code(ptr)",
            "declare ptr @festina_http_headers(ptr)",
            "declare ptr @festina_http_callback(ptr)",
            "declare void @festina_http_ok(ptr)",
            "declare void @festina_http_redirect(ptr, ptr)",
            "declare void @festina_http_upgrade(ptr)",
            "declare ptr @festina_http_to_blob(ptr)",
            "declare ptr @festina_http_to_img(ptr)",
            "declare ptr @festina_http_to_aud(ptr)",
            "declare ptr @festina_http_to_text(ptr)",
            "declare void @festina_http_send(ptr, ptr)",
            # claude.md #163: req.send() (zero arguments) now calls
            # festina_http_send_client_dispatch, not
            # festina_http_send_client directly -- the dispatcher
            # checks `.callback` at runtime and decides blocking vs.
            # background from there (see festina_runtime.h's own doc
            # comment). festina_http_send_client itself is still very
            # much alive in the runtime -- called from generated code
            # only indirectly now, through the dispatcher, so it needs
            # no `declare` of its own here anymore.
            "declare void @festina_http_send_client_dispatch(ptr)",
            "declare void @festina_release_http(ptr)",
            # festina_blob_bytes is already declared above (blob's own
            # sqlite-column binding uses it too) -- reused as-is by
            # _emit_sendable_body, not redeclared here.
            "declare ptr @festina_socket_state(ptr)",
            "declare void @festina_socket_send_text(ptr, ptr)",
            "declare void @festina_socket_send_binary(ptr, ptr, i64)",
            "declare void @festina_socket_close(ptr)",
            "declare void @festina_release_conn_handle(ptr)",
            # claude.md #70: DatabaseURL -- path is festina.sqlite's
            # location, NULL/empty meaning "use the default" (a plain
            # string constant already covers the no-directive case, so
            # this is only ever actually NULL if a DatabaseURL expression
            # itself somehow evaluates to a null text value at runtime).
            "declare ptr @festina_db_open(ptr)",
            "declare void @festina_sync_table(ptr, ptr, ptr, ptr, i32)",
            # claude.md #126 round nine: called unconditionally at the
            # very end of main() -- see festina_db_close's own comment.
            "declare void @festina_db_close(ptr)",
            # claude.md #32-34: sqlite() queries.
            "declare ptr @festina_sqlite_prepare(ptr, ptr)",
            # claude.md #113: literal SQL is prepared once per call site.
            "declare ptr @festina_sqlite_prepare_cached(ptr, ptr, ptr)",
            # claude.md #114: the string builder behind JSON rendering.
            "declare ptr @festina_sb_new()",
            "declare void @festina_sb_append(ptr, ptr)",
            "declare void @festina_sb_append_json_text(ptr, ptr)",
            "declare void @festina_sb_append_json_int(ptr, i64)",
            "declare void @festina_sb_append_json_float(ptr, double)",
            "declare void @festina_sb_append_json_bool(ptr, i8)",
            "declare void @festina_sb_append_json_bool64(ptr, i64)",
            "declare void @festina_sb_append_handle(ptr, ptr, ptr)",
            "declare ptr @festina_sb_finish(ptr)",
            # claude.md #116: split and join.
            "declare ptr @festina_text_split(ptr, ptr)",
            "declare ptr @festina_regex_split(ptr, ptr)",
            "declare ptr @festina_arr_join(ptr, ptr, ptr)",
            "declare void @festina_sqlite_bind_int(ptr, i32, i64)",
            "declare void @festina_sqlite_bind_float(ptr, i32, double)",
            "declare void @festina_sqlite_bind_text(ptr, i32, ptr)",
            "declare void @festina_sqlite_bind_null(ptr, i32)",
            # claude.md #101: aud/img columns are stored as their own
            # encoded bytes.
            "declare void @festina_sqlite_bind_blob(ptr, i32, ptr, i64)",
            "declare void @festina_set_audio_decoder(ptr)",
            "declare void @festina_set_image_decoder(ptr)",
            "declare ptr @festina_audio_bytes(ptr, ptr)",
            "declare ptr @festina_image_bytes(ptr, ptr)",
            "declare ptr @festina_audio_from_bytes(ptr, i64, ptr)",
            "declare void @festina_audio_free(ptr)",
            "declare ptr @festina_image_from_bytes(ptr, i64, ptr)",
            "declare void @festina_sqlite_exec(ptr)",
            # claude.md #94: single-value queries
            "declare i64 @festina_sqlite_scalar_int(ptr)",
            "declare double @festina_sqlite_scalar_float(ptr)",
            "declare ptr @festina_sqlite_scalar_text(ptr)",
            # claude.md #111: collect_rows takes the declared column
            # NAMES too (result columns are matched by name now), and
            # row.undefined() reads the presence mask it records.
            "declare void @festina_sqlite_collect_rows(ptr, i32, ptr, ptr, ptr, ptr, i8)",
            "declare i8 @festina_row_undefined(ptr, ptr, i32, ptr)",
            # claude.md #67-68, #107: regex(), .test(), .match(), .replace().
            "declare ptr @festina_regex_compile(ptr, ptr)",
            # claude.md #118: the per-call-site memo for dynamic regex().
            "declare ptr @festina_regex_compile_memo(ptr, ptr, ptr)",
            "declare void @festina_regex_free(ptr)",
            "declare i8 @festina_regex_test(ptr, ptr)",
            "declare ptr @festina_regex_match(ptr, ptr)",
            # claude.md #107: neither carries a replace-all argument any
            # more -- a regex knows from its own 'g' flag, and a text
            # search replaces the first match only.
            "declare ptr @festina_str_replace(ptr, ptr, ptr)",
            "declare ptr @festina_regex_replace(ptr, ptr, ptr)",
            # claude.md #37, #39, #40: img, graphics functions, click/mouse events.
            "declare void @festina_graphics_init()",
            "declare void @festina_run_event_loop()",
            "declare void @festina_draw_rect(i64, i64, i64, i64)",
            # claude.md #133
            "declare void @festina_draw_rect_color(i64, i64, i64, i64, i64)",
            # claude.md #188 (uraikus/festina#76 item 8)
            "declare void @festina_draw_rect_colors(i64, i64, i64, i64, i64, i64)",
            "declare void @festina_draw_pixel(i64, i64)",
            "declare void @festina_draw_pixel_color(i64, i64, i64)",
            "declare void @festina_draw_circle(i64, i64, i64)",
            "declare void @festina_draw_circle_color(i64, i64, i64, i64)",
            "declare void @festina_draw_circle_colors(i64, i64, i64, i64, i64)",
            "declare void @festina_draw_text(ptr, i64, i64)",
            # claude.md #89/#90: canvas drawing style + text metrics.
            # Colours and fonts are resolved at compile time (see
            # festina/colors.py), so these take numbers, not strings to
            # be parsed on every call.
            "declare void @festina_set_fill_rgb(i64, i64, i64)",
            "declare void @festina_set_border_rgb(i64, i64, i64)",
            # claude.md #91: the packed-colour and font-record forms
            "declare void @festina_set_fill_color(i64)",
            "declare void @festina_set_border_color(i64)",
            "declare void @festina_set_font_value(ptr)",
            "declare void @festina_set_line_width(i64)",
            "declare void @festina_set_font(i64, ptr, ptr)",
            "declare i64 @festina_measure_text_width(ptr)",
            "declare i64 @festina_measure_text_height(ptr)",
            "declare ptr @festina_load_image(ptr)",
            # claude.md #171: <text>.callback(fn:func[img]:void) -- the
            # img counterpart of claude.md #165's festina_blob_load_dispatch.
            "declare ptr @festina_image_load_dispatch(ptr, ptr)",
            "declare i8 @festina_save_canvas(ptr)",  # claude.md #93
            "declare ptr @festina_canvas_to_image()",  # claude.md #135
            # claude.md #94: paths, transforms, gradients, alpha
            # claude.md #95: render + clears
            "declare void @festina_render()",
            "declare void @festina_clear_canvas()",
            "declare void @festina_clear_rect(i64, i64, i64, i64)",
            # claude.md #133
            "declare void @festina_clear_circle(i64, i64, i64)",
            "declare void @festina_clear_pixel(i64, i64)",
            "declare void @festina_set_alpha(double)",
            "declare void @festina_fill_linear_gradient(i64, i64, i64, i64, i64, i64)",
            "declare void @festina_fill_radial_gradient(i64, i64, i64, i64, i64)",
            "declare void @festina_translate(i64, i64)",
            "declare void @festina_rotate(double)",
            "declare void @festina_scale(double, double)",
            "declare void @festina_reset_transform()",
            "declare void @festina_save_state()",
            "declare void @festina_restore_state()",
            "declare void @festina_begin_path()",
            "declare void @festina_move_to(i64, i64)",
            "declare void @festina_line_to(i64, i64)",
            "declare void @festina_curve_to(i64, i64, i64, i64, i64, i64)",
            "declare void @festina_close_path()",
            "declare void @festina_fill_path()",
            "declare void @festina_stroke_path()",
            # claude.md #92: img methods/properties
            "declare i64 @festina_image_width(ptr)",
            "declare i64 @festina_image_height(ptr)",
            "declare ptr @festina_image_clip(ptr, i64, i64, i64, i64)",
            # claude.md #188 (uraikus/festina#76 item 4)
            "declare ptr @festina_blank_image(i64, i64)",
            # claude.md #189
            "declare i64 @festina_get_pixel_color(i64, i64)",
            "declare i64 @festina_image_get_pixel_color(ptr, i64, i64)",
            "declare void @festina_image_resize(ptr, i64, i64)",
            # claude.md #134: drawRect/drawPixel/drawCircle/drawText as img methods.
            "declare void @festina_image_draw_rect(ptr, i64, i64, i64, i64)",
            "declare void @festina_image_draw_rect_color(ptr, i64, i64, i64, i64, i64)",
            # claude.md #188 (uraikus/festina#76 item 8)
            "declare void @festina_image_draw_rect_colors(ptr, i64, i64, i64, i64, i64, i64)",
            "declare void @festina_image_draw_pixel(ptr, i64, i64)",
            "declare void @festina_image_draw_pixel_color(ptr, i64, i64, i64)",
            "declare void @festina_image_draw_circle(ptr, i64, i64, i64)",
            "declare void @festina_image_draw_circle_color(ptr, i64, i64, i64, i64)",
            "declare void @festina_image_draw_circle_colors(ptr, i64, i64, i64, i64, i64)",
            "declare void @festina_image_draw_text(ptr, ptr, i64, i64)",
            "declare void @festina_image_free(ptr)",
            "declare i8 @festina_image_save(ptr, ptr)",
            "declare i8 @festina_image_save_copy(ptr, ptr)",
            "declare void @festina_draw_image(ptr, i64, i64)",
            # claude.md #185 (uraikus/festina#76 item 3)
            "declare void @festina_draw_image_scaled(ptr, i64, i64, i64, i64)",
            "declare void @festina_draw_image_region(ptr, i64, i64, i64, i64, i64, i64, i64, i64)",
            # claude.md #106: `on click` became mouseDown + mouseUp.
            "declare void @festina_register_mouse_down_handler(ptr)",
            "declare void @festina_register_mouse_up_handler(ptr)",
            "declare void @festina_register_mouse_handler(ptr)",
            # claude.md #181: the scroll wheel, split by direction --
            # see _EVENT_SIGNATURES' own comment in semantic.py.
            "declare void @festina_register_mouse_wheel_up_handler(ptr)",
            "declare void @festina_register_mouse_wheel_down_handler(ptr)",
            # claude.md #98: `on key` became `on keyDown` + `on keyUp`.
            "declare void @festina_register_key_down_handler(ptr)",
            "declare void @festina_register_key_up_handler(ptr)",
            "declare void @festina_register_resize_handler(ptr)",
            "declare void @festina_register_close_handler(ptr)",
            # claude.md #131: unlike the six handlers just above, these
            # two live in the CORE runtime translation unit (not
            # graphics) -- close(code)/`on exit` work in every program,
            # windowed or not.
            "declare void @festina_register_exit_handler(ptr)",
            "declare void @festina_program_exit(i64)",
            # claude.md #161: graceful shutdown -- see festina_runtime.h's
            # own doc comment right above these declarations.
            "declare void @festina_install_shutdown_handler()",
            "declare i64 @festina_client_width()",
            "declare i64 @festina_client_height()",
            # claude.md #139
            "declare i64 @festina_screen_width()",
            "declare i64 @festina_screen_height()",
            # claude.md #181
            "declare double @festina_device_pixel_ratio()",
            "declare void @festina_set_client_width(i64)",
            "declare void @festina_set_client_height(i64)",
            # claude.md #180
            "declare void @festina_enter_fullscreen()",
            "declare void @festina_exit_fullscreen()",
            # claude.md #182
            "declare void @festina_show_cursor()",
            "declare void @festina_hide_cursor()",
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
            # claude.md #171: <text>.callback(fn:func[aud]:void) -- the
            # aud counterpart of claude.md #165's festina_blob_load_dispatch.
            "declare ptr @festina_audio_load_dispatch(ptr, ptr)",
            # claude.md #99: play/playLoop, with or without a channel.
            # claude.md #109: play/playLoop hand back the channel they
            # chose, and aud.stop() is back as a clip-wide stop.
            "declare i64 @festina_audio_play_on(ptr, i64, i8, i8)",
            "declare void @festina_audio_stop_clip(ptr)",
            "declare i8 @festina_audio_save(ptr, ptr)",
            "declare i8 @festina_audio_save_copy(ptr, ptr)",
            "declare void @festina_stop_audio_player(i64)",
            "declare i8 @festina_audio_is_playing(ptr)",
            # claude.md #146: isAudioPlayerPlaying(channel) -- the
            # per-CHANNEL counterpart to festina_audio_is_playing's
            # per-clip question.
            "declare i8 @festina_channel_is_playing(i64)",
            # claude.md #98: the per-aud voice limit.
            "declare void @festina_set_max_audio_players(i64)",
            "declare i64 @festina_get_max_audio_players()",
            # claude.md #148: size_t's real width on the target being
            # linked against -- see __init__'s own note on
            # self.pointer_bits for why this can't just always be i64.
            f"declare ptr @malloc(i{self.pointer_bits})",
            f"declare ptr @calloc(i{self.pointer_bits}, i{self.pointer_bits})",
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
            # claude.md #120: the type-blind state half of cycle
            # collection -- see the festina_cycle_* block comment in
            # runtime/festina_runtime.c and _cycle_fn here.
            "declare i8 @festina_cycle_candidate(ptr)",
            "declare i8 @festina_cycle_begin_gray(ptr)",
            "declare void @festina_cycle_dec(ptr)",
            "declare void @festina_cycle_inc(ptr)",
            "declare i64 @festina_cycle_begin_scan(ptr)",
            "declare void @festina_cycle_set_black(ptr)",
            "declare i8 @festina_cycle_needs_black(ptr)",
            "declare i8 @festina_cycle_begin_white(ptr)",
            "declare void @festina_cycle_visit_array(ptr, ptr)",
            "declare void @festina_cycle_visit_map(ptr, ptr)",
            "declare void @festina_cycle_dispose_array(ptr)",
            "declare void @festina_cycle_dispose_map(ptr)",
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
            # claude.md #96: array methods. claude.md #174: each gained
            # a `ptr capacity` 2nd parameter -- NULL for a plain arr[T]
            # (festina_array_resize's own unchanged exact-size-realloc
            # behavior), or the address of an `amor arr[T]`'s own
            # tracked capacity field (FESTINA_AMOR_ARRAY_LLVM_TYPE's 3rd
            # field) for geometric doubling growth instead -- see
            # _array_capacity_arg's own comment and festina_array_resize's
            # in runtime/festina_runtime.c.
            "declare void @festina_array_push(ptr, ptr, i64, ptr)",
            "declare void @festina_array_unshift(ptr, ptr, i64, ptr)",
            "declare i8 @festina_array_pop(ptr, ptr, i64, ptr)",
            "declare i8 @festina_array_shift(ptr, ptr, i64, ptr)",
            "declare void @festina_array_splice(ptr, ptr, i64, i64, i64, ptr)",
            # claude.md #130: the 3-argument splice(start, count, insertArr) form.
            "declare void @festina_array_splice_insert(ptr, ptr, i64, i64, i64, ptr, i64, ptr)",
            # claude.md #97
            "declare i64 @festina_array_index_of(ptr, i64, ptr, i8)",
            # claude.md #184 (uraikus/festina#76 item 2)
            "declare void @festina_array_sort(ptr, i64, ptr, ptr)",
            "declare void @festina_release_map(ptr)",
            # claude.md #71: environment.NAME / environment[keyExpr].
            "declare ptr @festina_getenv(ptr)",
            # claude.md #72, rebuilt into a real hash table by #175:
            # map[T] -- count_ptr/entries_ptr/capacity_ptr/tombstones_ptr
            # (all four fields of a FESTINA_MAP_LLVM_TYPE value's storage
            # slot, always passed by address so festina_map_set can
            # rehash the whole table in place) are always `ptr` regardless
            # of T; the value itself is always passed/returned as a raw
            # i64 (every map value type's bit pattern fits in 8 bytes --
            # see types.MapType's doc comment -- codegen reinterprets
            # to/from the real LLVM type at each call site, see
            # _map_value_to_i64/_i64_to_map_value). festina_map_get/
            # _for_each only ever read, and only ever need entries/
            # capacity (a bucket scan is driven by capacity, not count).
            "declare void @festina_map_set(ptr, ptr, ptr, ptr, ptr, i64)",
            "declare i64 @festina_map_get(ptr, i64, ptr, i64)",
            "declare void @festina_map_for_each(ptr, i64, ptr)",
            # claude.md #186 (uraikus/festina#76 item 7)
            "declare void @festina_map_keys(ptr, i64, ptr)",
            "declare void @festina_map_values(ptr, i64, i64, i8, i8, ptr)",
            # claude.md #74/#75/#175: frees each live bucket's own
            # strdup'd key (never a plain free()-the-buffer-alone away
            # from leaking them -- see festina_map_free_entries's own doc
            # comment) before freeing the entries buffer itself -- see
            # _emit_free_active_locals's MapType branch.
            "declare void @festina_map_free_entries(ptr, i64)",
            # claude.md #56: Math.floor/ceil/round/trunc, via LLVM's
            # built-in intrinsics rather than a runtime C function.
            "declare i64 @llvm.fptosi.sat.i64.f64(double)",
            "declare double @llvm.floor.f64(double)",
            "declare double @llvm.ceil.f64(double)",
            "declare double @llvm.round.f64(double)",
            "declare double @llvm.trunc.f64(double)",
        ]

    def _struct_type_defs(self):
        # claude.md #26: every arr[T] -- regardless of T -- lowers to the
        # same fixed-size {length, data} header; see the module docstring.
        # claude.md #72/#175: every map[T] -- regardless of T -- lowers
        # to the identical-shaped {count, entries, capacity, tombstones}
        # header, for the same reason (see FESTINA_MAP_LLVM_TYPE's own
        # comment on why it's still a distinct name rather than reusing
        # _FestinaArray outright).
        lines = [
            f"{FESTINA_ARRAY_LLVM_TYPE} = type {{ i64, ptr }}",
            f"{FESTINA_AMOR_ARRAY_LLVM_TYPE} = type {{ i64, ptr, i64 }}",
            f"{FESTINA_MAP_LLVM_TYPE} = type {{ i64, ptr, i64, i64 }}",
            # claude.md #91: a `font` value points at one of these,
            # emitted as read-only data from the declaration's own
            # literal -- size in px, slant, weight, family. Layout must
            # match FestinaFont in runtime/festina_runtime.h.
            f"{FESTINA_FONT_LLVM_TYPE} = type {{ i64, i64, i64, ptr }}",
            # claude.md #176: see FESTINA_ENUM_BOX_LLVM_TYPE's own
            # comment -- the one universal payload shape every "mixed"
            # enum's heap box uses.
            f"{FESTINA_ENUM_BOX_LLVM_TYPE} = type {{ ptr, i64 }}",
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
                if type_.name in self._tagged_structs:
                    # claude.md #176: the widened {tag, refcount,
                    # payload} header, in static-global form -- see
                    # _emit_fresh_heap_header's own comment for why tag
                    # comes before refcount (so the refcount word stays
                    # at the identical `payload - 8` offset either way).
                    # The refcount field is still the immortal `-1`
                    # sentinel; the tag is a REAL constant (this global
                    # already knows its own concrete struct type, same
                    # as any other tagged-struct construction site).
                    tag_const = self._enum_tag_const(type_)
                    lines.append(f"{header} = global {{ptr, i64, {struct_ty}}} "
                                 f"{{ptr {tag_const}, i64 -1, {struct_ty} zeroinitializer}}")
                    lines.append(f"{ref} = global ptr getelementptr({{ptr, i64, {struct_ty}}}, "
                                 f"ptr {header}, i32 0, i32 2)")
                else:
                    lines.append(f"{header} = global {{i64, {struct_ty}}} {{i64 -1, {struct_ty} zeroinitializer}}")
                    lines.append(f"{ref} = global ptr getelementptr({{i64, {struct_ty}}}, ptr {header}, i32 0, i32 1)")
                continue
            array_is_amortized = isinstance(type_, types_mod.ArrayType) and type_.amortized
            if isinstance(type_, (types_mod.ArrayType, types_mod.MapType)) and not array_is_amortized:
                # claude.md #79: identical treatment to the StructType
                # branch just above -- see its own comment for the full
                # reasoning (immortal sentinel refcount, no special-
                # casing needed at a global's first-ever reassignment).
                # Only the payload shape differs (FESTINA_ARRAY_LLVM_TYPE's
                # `{i64, ptr}` or FESTINA_MAP_LLVM_TYPE's `{i64, ptr, i64,
                # i64}` in place of a user struct's own field list).
                #
                # claude.md #174: `amor arr[T]` is deliberately excluded
                # -- an amortized array global falls through to the
                # generic `global ptr null` case below instead, exactly
                # like a blob/img/aud/http/socket global already does.
                # Safe ONLY because semantic.py requires an amortized
                # declaration to have an initializer (unlike plain
                # arr[T]/map[T], which can be declared bare and start
                # "empty" via this immortal-sentinel trick) -- top-level
                # init code always runs and overwrites this null with a
                # real value before any user code could observe it, the
                # same reasoning that already makes a bare blob/img/aud/
                # http/socket global safe. Skipping this path also
                # sidesteps needing FESTINA_AMOR_ARRAY_LLVM_TYPE awareness
                # here at all. map[T] has no such exclusion any more --
                # claude.md #175 removed `amor map[T]` outright, so every
                # map[T] global takes this immortal-sentinel path, the
                # same as a struct.
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
        if isinstance(type_, types_mod.ColorType):
            # claude.md #91: an unset colour is 'none', not 0 -- 0 is a
            # real colour (opaque black), so it would silently paint.
            return "-1"
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
        if isinstance(stmt, ast.EnumDecl):
            return  # claude.md #176: already reflected in self.enums (from semantic analysis)
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
            type_expr, self.structs, self.tables, self.enums, self.filename, node)

    # ---- functions ----

    def _emit_free_active_locals(self, lines, down_to=0, skip_try_pop=False):
        """claude.md #74: frees every non-escaping local active in every
        frame of self._active_free_locals from the top of the stack down
        to (and including) index `down_to`.

        claude.md #157: skip_try_pop=True (ThrowStmt's own call is the
        only caller) leaves every _TryFrameMarker entry in the walked
        range alone -- emits nothing for it, unlike every other exit
        path (Return/Break/Continue/a try body's own normal
        fallthrough), which DO emit festina_try_pop() for one here (see
        that branch's own comment). A throw must never pop the very
        catch frame it might be about to unwind INTO: festina_throw
        itself looks up and pops exactly the frame it jumps to, at
        runtime, once it actually runs -- if this walk popped it FIRST,
        in generated code that always executes (unlike everything past
        the throw, which never runs), festina_throw would find the
        frame already gone and treat a perfectly-caught throw as
        uncaught. Real locals in the same walked range still need
        freeing here regardless (see _emit_try's own docstring for why
        that part isn't optional) -- only the marker itself is special-
        cased.

        down_to=0 is the parameter's own default, but a Return never
        actually relies on that default -- it passes down_to=self.
        _current_func_frame_base explicitly (claude.md #140), which
        frees every frame belonging to the function/handler currently
        being emitted, all the way to (and including) ITS OWN outermost
        one, so every nested block's own still-open locals need freeing
        together, not just the innermost one (see _emit_stmt's Return
        handling). That base is 0 for a top-level function/handler --
        self._active_free_locals is always empty entering one -- but
        NOT for a nested FuncDecl reached inside another function's own
        body (see self._current_func_frame_base's own comment): a plain
        hardcoded 0 there would free the ENCLOSING function's still-live
        locals too, which are further down the SAME shared stack. A
        Break/Continue only frees frames opened since the nearest
        enclosing loop's own body began (down_to = the frame index
        _emit_while/_emit_for recorded when that body's frame was about
        to be pushed -- see self._loop_targets' own comment) -- an outer
        function-level local merely *used* inside that loop, not
        declared inside it, must NOT be freed by the loop's own
        break/continue, and this is what keeps that true. _emit_block's
        own natural (non-terminated) fall-through exit frees just its
        own single frame (down_to = that frame's own, topmost, index)
        before popping it.

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
                if isinstance(type_, _TryFrameMarker):
                    # claude.md #157: not a local at all -- pops the
                    # runtime's own catch-frame stack so a LATER,
                    # unrelated throw (reached after this try/catch
                    # statement has already exited, on whatever path)
                    # can never land back in this try's own now-stale
                    # catch block. Every exit from a try body -- normal
                    # fallthrough, return, break, continue -- reaches
                    # here via this exact shared walk, so this is the
                    # ONLY place festina_try_pop needs calling from
                    # generated code (a throw itself pops its own frame
                    # from inside festina_throw, before the longjmp --
                    # see that function's own comment) -- skip_try_pop
                    # is exactly ThrowStmt's own call opting out of that
                    # for this reason (see this method's own docstring).
                    if not skip_try_pop:
                        lines.append("  call void @festina_try_pop()")
                elif isinstance(type_, _StackStructFieldsOnly):
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
                        elem_type = type_.type_.element
                        len_ptr = self.tmp()
                        lines.append(f"  {len_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}, i32 0, i32 0")
                        len_val = self.tmp()
                        lines.append(f"  {len_val} = load i64, ptr {len_ptr}")
                        field_ptr = self.tmp()
                        lines.append(f"  {field_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}, i32 0, i32 1")
                        data_ptr = self.tmp()
                        lines.append(f"  {data_ptr} = load ptr, ptr {field_ptr}")
                        if (_is_refcounted(elem_type)
                                or elem_type == TEXT):
                            # claude.md #80 (widened by #83 to text):
                            # this array's own elements are themselves
                            # refcounted/copy-managed -- release each
                            # one before freeing the data buffer they
                            # live in, the same element-release loop
                            # _release_fn_for_array's own generated
                            # wrapper uses for the heap-allocated case.
                            self._emit_release_array_elements(
                                data_ptr, len_val, self._release_fn_for(elem_type),
                                _llvm_type(elem_type), lines)
                        lines.append(f"  call void @free(ptr {data_ptr})")
                    else:
                        # claude.md #74/#75/#175: unlike an array's plain
                        # data buffer, a map's entries buffer has its
                        # own nested allocation per live bucket (each key
                        # is its own strdup'd copy -- see
                        # festina_map_set's own comment) that a plain
                        # free() of the entries pointer alone would leak.
                        # festina_map_free_entries frees each live
                        # bucket's key first, then the entries buffer
                        # itself -- scanned by capacity, not count (a
                        # bucket scan is driven by capacity, not a dense
                        # [0,count) range).
                        value_type = type_.type_.value
                        field_ptr = self.tmp()
                        lines.append(f"  {field_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {header}, i32 0, i32 1")
                        entries_ptr = self.tmp()
                        lines.append(f"  {entries_ptr} = load ptr, ptr {field_ptr}")
                        cap_ptr = self.tmp()
                        lines.append(f"  {cap_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {header}, i32 0, i32 2")
                        cap_val = self.tmp()
                        lines.append(f"  {cap_val} = load i64, ptr {cap_ptr}")
                        if (_is_refcounted(value_type)
                                or value_type == TEXT):
                            # claude.md #80 (widened by #83 to text):
                            # same as the array case just above, but
                            # through festina_map_for_each and a
                            # release trampoline, since a map's own
                            # entries layout stays opaque to codegen --
                            # see _release_fn_for_map's own comment.
                            trampoline_name = self._emit_map_value_release_trampoline(value_type)
                            lines.append(
                                f"  call void @festina_map_for_each(ptr {entries_ptr}, i64 {cap_val}, ptr {trampoline_name})")
                        lines.append(f"  call void @festina_map_free_entries(ptr {entries_ptr}, i64 {cap_val})")
                elif _is_refcounted(type_) or type_ == TEXT:
                    # claude.md #77/#79: release (not free) -- this
                    # value is refcounted (see _emit_stmt's own VarDecl
                    # handling), so its own reference simply needs
                    # dropping; whichever function _release_fn_for
                    # dispatches to only actually frees it once nothing
                    # else references it. claude.md #83: for text,
                    # _release_fn_for dispatches straight to a plain,
                    # NULL-safe @free -- there's no refcount to check,
                    # so freeing IS the whole job here (correct because
                    # every text local is scheduled for this regardless
                    # of escaping-ness -- see _emit_block's own tracking
                    # comment -- so this always sees its own exclusively
                    # owned copy, never a value some other binding still
                    # needs).
                    loaded = self.tmp()
                    lines.append(f"  {loaded} = load ptr, ptr {ref}")
                    lines.append(f"  call void {self._release_fn_for(type_)}(ptr {loaded})")

    def _emit_analyzed_func_body(self, decl, body_env, return_type, body_lines, escaping):
        """claude.md #74: runs escape_analysis.find_escaping_names once
        for decl's whole body and makes it available (self.
        _current_escaping_names) to every _emit_block call this body's
        emission reaches -- the function/handler's own top-level body
        and every nested if/while/for body alike, all governed by that
        one whole-function-scoped name set (a name's escaping-ness is a
        property of the whole enclosing function, not of whichever block
        it happens to be declared in -- see escape_analysis.py's own
        module docstring). claude.md #140: restored to whatever it held
        before (not unconditionally reset to None) afterward -- a nested
        FuncDecl inside decl's own body (see _emit_stmt's own FuncDecl
        branch) re-enters this method recursively one level deeper,
        while decl's own body is still being walked, so a bare reset
        would clobber decl's own tracking for everything left to emit
        after that nested declaration's position. Save/restore around
        the recursive call, using each call's own local `escaping`
        parameter, makes this correctly reentrant to however deep
        nesting goes with no separate explicit stack needed the way
        _active_free_locals and _loop_targets each keep one.

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
        per-function state is needed here for it at all.

        claude.md #84: `escaping` is computed by the CALLER now (once,
        before its own parameter-binding loop runs -- see
        _emit_param_bindings' own comment for why binding needs it
        before this method would otherwise compute it), not
        recomputed here -- this method just applies it.

        claude.md #140: SAVES and RESTORES self._current_escaping_names
        (rather than unconditionally resetting it to None afterward) --
        a nested FuncDecl reachable from decl's own body (see
        _emit_stmt's own FuncDecl branch) re-enters this exact method
        recursively, one level deeper, before decl's own body is fully
        walked. A bare reset-to-None would clobber decl's own tracking
        for every statement still left to emit after that nested
        declaration's position, silently turning tracked-and-safe-to-
        free locals back into leaks for the rest of decl's own body.
        Restoring the PREVIOUS value instead (None at the outermost
        level, decl's own `escaping` one level inside a function nested
        inside decl, and so on for however deep nesting goes) makes
        this correctly reentrant with no separate explicit stack
        needed -- each call's own local `escaping` parameter and the
        Python call stack itself already are one."""
        saved_escaping_names = self._current_escaping_names
        self._current_escaping_names = escaping
        try:
            block = self._emit_block(decl.body, body_env, return_type, body_lines)
        finally:
            self._current_escaping_names = saved_escaping_names
        if isinstance(decl, ast.FuncDecl):
            self.escaping_params[decl.name] = {
                i for i, p in enumerate(decl.params) if p.name in escaping
            }
        return block

    def _emit_param_bindings(self, decl, param_types, body_env, body_lines):
        """claude.md #84: binds every one of decl's own parameters into
        body_env (a fresh `alloca` + `store` per parameter, exactly as
        before this section), and returns the escaping-names set
        (escape_analysis.find_escaping_names, computed here -- BEFORE
        binding, not after -- so a struct/arr[T]/map[T]/text parameter
        that turns out to be escaping can be retained/copied right at
        its own binding point, not left to alias the caller's own
        argument pointer unretained) for the caller (_emit_func/
        _emit_event_handler) to pass through to
        _emit_analyzed_func_body afterward.

        Fixes a real, confirmed pre-existing bug (found while designing
        claude.md #83's own text parameter handling, but never
        specific to text at all): a struct/arr[T]/map[T] parameter is
        passed as the caller's own raw pointer, on purpose, never
        retained at the call site (the same "borrowed, not owned"
        convention every call argument already uses) -- but if the
        callee's own body ever REASSIGNS that parameter
        (`void func f(p:Point) { p = other }`), _emit_local_retain_
        release's own reassignment logic unconditionally releases
        whatever `p` currently holds, which, for a parameter that was
        never independently retained, IS the caller's own value --
        confirmed directly with AddressSanitizer as a genuine heap-
        use-after-free, the caller's own binding silently corrupted
        the moment the callee returns. A parameter that's merely
        *read* (never itself reassigned, returned, or otherwise made
        to escape) needs no fix: nothing releases an untracked
        binding's borrowed reference, so the caller's own reference is
        never touched.

        The fix mirrors an ordinary WITH-INITIALIZER local declared
        from an aliasing source exactly: retain (struct/arr[T]/map[T])
        or copy via festina_text_own (text) any parameter whose own
        name is in `escaping` -- deliberately not narrowed to "only
        when actually reassigned," the same over-conservative-rather-
        than-precise bias every other retain-or-not decision in this
        whole effort already takes, since a parameter that merely gets
        returned or passed onward (not reassigned) already works
        correctly without this and retaining it anyway costs nothing
        but a balanced extra retain/release pair -- then schedules it
        in _active_free_locals (a new, OUTERMOST frame the caller
        pushes before this call and pops/frees after
        _emit_analyzed_func_body returns) for release at the
        function's own scope-exit, the identical bookkeeping an
        ordinary escaping local already gets from _emit_block's own
        tracking."""
        escaping = escape_analysis.find_escaping_names(decl.body, escaping_params=self.escaping_params)
        for t, p in zip(param_types, decl.params):
            slot = f"%{p.name}"
            body_lines.append(f"  {slot} = alloca {_llvm_type(t)}")
            arg_ref = f"%arg.{p.name}"
            if p.name in escaping and _is_refcounted(t):
                # claude.md #118: _is_refcounted rather than the
                # struct/arr/map tuple, so a blob/img/aud/regex
                # parameter that escapes (is reassigned, say) carries
                # its own +1 here too -- otherwise the reassignment's
                # release would drop a reference the CALLER still owns.
                body_lines.append(f"  call void @festina_retain(ptr {arg_ref})")
                self._active_free_locals[-1].append((slot, t))
            elif p.name in escaping and t == TEXT:
                owned = self.tmp()
                body_lines.append(f"  {owned} = call ptr @festina_text_own(ptr {arg_ref})")
                arg_ref = owned
                self._active_free_locals[-1].append((slot, t))
            body_lines.append(f"  store {_llvm_type(t)} {arg_ref}, ptr {slot}")
            body_env.define(p.name, slot, t)
        return escaping

    def _register_func_signature(self, decl):
        """claude.md #140: registers decl's NAME and SIGNATURE only --
        self.func_decls (what _emit_call looks a callee's own arg/return
        types up from) and self.global_env (what an Identifier reference
        to the function resolves to) -- with no body emitted yet. Called
        once per FuncDecl, for every one reachable anywhere in the whole
        program, from _register_all_func_signatures' own pre-pass in
        generate(), BEFORE any code that might call it is emitted --
        this is what makes a function's declaration ORDER stop
        mattering (hoisting), the code-generation half of what
        semantic.py's own register_func_signature/analyze_func split
        already does for type-checking."""
        return_type = None if decl.return_type == "void" else self._resolve(decl.return_type, decl)
        self.func_decls[decl.name] = decl
        self.global_env.define(decl.name, f"@{decl.name}", return_type)

    def _register_all_func_signatures(self, stmts):
        """claude.md #140: recursively finds every ast.FuncDecl reachable
        from `stmts` -- inside a Block, either arm of an IfStmt, a
        While/ForStmt body, an EventHandler body, or even another
        FuncDecl's own body (a function nested inside a function, which
        semantic.py's analyze_func already treats as an ordinary global
        declaration regardless of nesting -- see its own comment) -- and
        registers each one's signature before generate()'s real emission
        pass ever starts. This traversal shape must stay in lockstep
        with _emit_stmt's own recursive descent (the shapes visitable
        with a FuncDecl inside them are exactly what analyze_statement/
        analyze_block descend into in semantic.py, and what _emit_stmt/
        _emit_if/_emit_while/_emit_for descend into here) -- if a new
        statement kind ever grows a nested body, both need to learn about
        it together, or a FuncDecl inside it would be reachable at
        analysis time (semantic.py's own mirror-image walker already
        found it) but silently skipped here, leaving self.func_decls
        without an entry _emit_call would then crash looking up."""
        for stmt in stmts:
            if isinstance(stmt, ast.FuncDecl):
                self._register_func_signature(stmt)
                self._register_all_func_signatures(stmt.body.body)
            elif isinstance(stmt, ast.EventHandler):
                self._register_all_func_signatures(stmt.body.body)
            elif isinstance(stmt, ast.Block):
                self._register_all_func_signatures(stmt.body)
            elif isinstance(stmt, ast.IfStmt):
                self._register_all_func_signatures(stmt.then.body)
                if isinstance(stmt.orelse, ast.IfStmt):
                    self._register_all_func_signatures([stmt.orelse])
                elif stmt.orelse is not None:
                    self._register_all_func_signatures(stmt.orelse.body)
            elif isinstance(stmt, (ast.WhileStmt, ast.ForStmt)):
                self._register_all_func_signatures(stmt.body.body)

    def _emit_func(self, decl):
        # claude.md #140: signature already registered by generate()'s
        # own pre-pass (_register_all_func_signatures) before this ever
        # runs -- re-resolving return_type here is cheap and side-effect
        # free (resolve_type_name is a pure function of self.structs/
        # self.tables, neither of which changes mid-compile), simpler
        # than threading the already-resolved type back out of
        # self.func_decls (which stores the raw ast.FuncDecl, not its
        # resolved return type) into this still fully self-contained
        # function.
        return_type = None if decl.return_type == "void" else self._resolve(decl.return_type, decl)
        param_types = [self._resolve(p.type_expr, decl) for p in decl.params]
        llvm_ret = "void" if return_type is None else _llvm_type(return_type)
        params_ir = ", ".join(f"{_llvm_type(t)} %arg.{p.name}" for t, p in zip(param_types, decl.params))

        body_env = Env(self.global_env)
        body_lines = []
        entry_label = self.label("entry")
        self._start_block(entry_label, body_lines)
        # claude.md #84: an OUTERMOST frame for this function's own
        # parameters -- pushed before binding them (so
        # _emit_param_bindings can schedule any escaping struct/
        # arr[T]/map[T]/text one it retains/copies), popped and freed
        # after decl's own body is fully emitted, one level below the
        # body's own frame(s) (_emit_block pushes/pops its own,
        # separately) so a Return anywhere inside frees both via the
        # same _emit_free_active_locals(down_to=self._current_func_frame_base)
        # call it already makes -- see _emit_param_bindings' own comment
        # for why this exists at all.
        #
        # claude.md #140: self._current_func_frame_base is saved and
        # restored around this push/pop (not just pushed/popped itself)
        # -- a nested FuncDecl inside decl's own body (see _emit_stmt's
        # own FuncDecl branch) re-enters THIS SAME _emit_func recursively
        # one level deeper, while decl's own frame(s) are still open
        # beneath it on the identical shared self._active_free_locals
        # stack. Capturing the base index here, right before this
        # function's own outermost frame is pushed, is what lets Return
        # (inside decl's own body, OR inside a nested function's) free
        # exactly its own frames and stop there -- see
        # self._current_func_frame_base's own comment for the bug this
        # fixes, confirmed with a real Xvfb-free repro (a struct/text/
        # array/map local declared before a nested FuncDecl, in the
        # SAME enclosing function, produced an LLVM verifier error
        # ("use of undefined value") before this fix -- the nested
        # function's own trivial `return` was freeing the ENCLOSING
        # function's still-live locals, not just its own empty frame).
        saved_func_frame_base = self._current_func_frame_base
        self._current_func_frame_base = len(self._active_free_locals)
        self._active_free_locals.append([])
        escaping = self._emit_param_bindings(decl, param_types, body_env, body_lines)

        block = self._emit_analyzed_func_body(decl, body_env, return_type, body_lines, escaping)
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
            # claude.md #84: also frees this function's own parameter
            # frame first -- _emit_block's own fall-through-exit only
            # ever frees its OWN (body-level) frame, never a caller-
            # pushed outer one.
            self._emit_free_active_locals(block["lines"], down_to=len(self._active_free_locals) - 1)
            if return_type is None:
                block["lines"].append("  ret void")
            else:
                block["lines"].append(f"  ret {_llvm_type(return_type)} {self._zero_value(return_type)}")
        self._active_free_locals.pop()
        self._current_func_frame_base = saved_func_frame_base

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
        mouseDown/mouseUp/mouse/mouseWheelUp/mouseWheelDown/key/resize/
        close additionally get
        registered with the runtime as a function pointer (see
        festina_runtime.h's doc comment on
        festina_register_mouse_down_handler/_mouse_up_handler/_mouse_handler/
        _mouse_wheel_up_handler/_mouse_wheel_down_handler/_key_handler/
        _resize_handler/_close_handler) -- the only event
        sources this runtime actually generates (claude.md #40's own
        examples; semantic.py's _EVENT_SIGNATURES enforces the fixed
        signature each one needs, matching the runtime's fixed function
        pointer type for it). claude.md #131: `exit` is a ninth
        recognized name, but not a graphics event -- it fires from the
        close(code) builtin (see _emit_call's own "close" branch),
        which works with or without a window, so its registration is
        unconditional in _emit_main_and_entry rather than joining the
        other six's graphics-gated loop. Any OTHER declared name still
        compiles (so a typo/bug in its body is still caught) but is
        simply dead code: nothing ever calls it."""
        symbol = f"@__festina_on_{decl.name}"
        param_types = [self._resolve(p.type_expr, decl) for p in decl.params]
        params_ir = ", ".join(f"{_llvm_type(t)} %arg.{p.name}" for t, p in zip(param_types, decl.params))

        body_env = Env(self.global_env)
        body_lines = []
        entry_label = self.label("entry")
        self._start_block(entry_label, body_lines)
        # claude.md #84: see _emit_func's own comment on this same
        # pattern -- an event handler's own parameters (e.g. `on
        # key(key:text)`) need the identical outer parameter frame.
        self._active_free_locals.append([])
        escaping = self._emit_param_bindings(decl, param_types, body_env, body_lines)

        block = self._emit_analyzed_func_body(decl, body_env, None, body_lines, escaping)
        if not block["terminated"]:
            self._emit_free_active_locals(block["lines"], down_to=len(self._active_free_locals) - 1)
            block["lines"].append("  ret void")
        self._active_free_locals.pop()

        func = [f"define void {symbol}({params_ir}) {{"]
        func.extend(block["lines"])
        func.append("}")
        self.func_defs.extend(func)
        self.func_defs.append("")

        if decl.name in ("mouseDown", "mouseUp", "mouse", "mouseWheelUp", "mouseWheelDown",
                          "keyDown", "keyUp", "resize", "close"):
            self.uses_graphics = True
            self.event_handlers[decl.name] = symbol
        elif decl.name == "exit":
            # claude.md #131: NOT a graphics event -- registered
            # unconditionally in main() below, so this deliberately does
            # not set self.uses_graphics or join event_handlers (whose
            # own registration loop is graphics-gated).
            self.exit_handler_symbol = symbol
        elif decl.name in ("request", "upgrade", "message", "socketClose"):
            # claude.md #151: NOT graphics events either -- same
            # unconditional-registration shape as `exit` just above,
            # for the same reason (an http/websocket connection has
            # nothing to do with a window). Declaring one of these
            # without ever calling openPort() anywhere is legal (the
            # handler just never fires, nothing ever accepts a
            # connection), but still sets uses_http so the runtime
            # translation unit these symbols reference is always
            # linked in wherever any of the four is declared.
            self.uses_http = True
            if decl.name == "request":
                self.http_request_handler_symbol = symbol
            elif decl.name == "upgrade":
                self.http_upgrade_handler_symbol = symbol
            elif decl.name == "message":
                self.http_message_handler_symbol = symbol
            else:
                self.http_socketclose_handler_symbol = symbol

    # ---- statements ----
    def _emit_free(self, stmt, env, lines):
        """claude.md #111: `free name` -- release whatever the binding
        holds, then null the binding. The null store is half the design:
        it is what makes `free` composable with everything the compiler
        already does. Every release function in this runtime is
        null-safe, so the automatic scope-exit release that may later
        visit this same binding finds null and does nothing -- manual
        free and automatic reclamation coexist with no bookkeeping,
        and `free x` twice is a no-op, not a double free. It is also
        what makes use-after-free THROUGH THIS BINDING impossible:
        reading x afterwards reads null, the ordinary absent value.

        Per type:
        - struct/arr[T]/map[T]/blob/img/aud/regex: a refcount
          DECREMENT, not a forced free -- an aliased value survives
          until its other references drop, which is the "retains
          pointers if they have a pointer elsewhere" guarantee.
          Freeing an array releases each element the same way, so a
          shared element outlives its array. (claude.md #118: img/aud
          used to be the exception here -- freed outright, alias left
          dangling -- and regex needed a runtime flag to protect the
          literal cache; the refcount header retired both special
          cases. A /pattern/ literal's cached compilation is immortal,
          so `free` on a binding aliasing one is a safe no-op.)
        - text: the buffer is exclusively owned (copy-on-alias,
          claude.md #83), so freed outright.
        - a query row: nulled WITHOUT freeing -- the row is owned by the
          array it came from, and freeing it here would double-free at
          the array's own release. Free the array.
        - int/float/bool (and every other value type): nothing to
          release; freeing degenerates to `x = null`.
        """
        found = env.lookup(stmt.name)
        if found is None:
            raise CodegenError(f"free: unknown variable '{stmt.name}'",
                                file=self.filename, line=stmt.line)
        ref, ttype = found
        llvm_ty = _llvm_type(ttype)
        if llvm_ty == "ptr":
            old = self.tmp()
            lines.append(f"  {old} = load ptr, ptr {ref}")
            if _is_refcounted(ttype):
                lines.append(f"  call void {self._release_fn_for(ttype)}(ptr {old})")
            elif ttype == TEXT:
                lines.append(f"  call void @free(ptr {old})")
            # TableType (a borrowed query row) and any other ptr-backed
            # value: nothing released, only the binding dropped.
            lines.append(f"  store ptr null, ptr {ref}")
        elif llvm_ty == "i64":
            lines.append(f"  store i64 {INT_NULL_CONST}, ptr {ref}")
        elif llvm_ty == "double":
            lines.append(f"  store double {FLOAT_NULL_CONST}, ptr {ref}")
        elif llvm_ty == "i8":
            lines.append(f"  store i8 {BOOL_NULL_CONST}, ptr {ref}")
        else:
            raise CodegenError(
                f"free: cannot free a value of type {types_mod.type_name(ttype)}",
                file=self.filename, line=stmt.line)

    def _emit_delete(self, stmt, env, lines):
        """claude.md #111: `delete`, JS-shaped. On a map the entry stops
        existing (count drops, forEach skips it); on a struct field the
        value is released and the field reads null afterwards; on a
        query-row field the same, plus the row's presence bit clears so
        undefined() reports it -- deletion and never-selected look
        identical from then on, which is the JS behavior too.

        One honest caveat, inherited from claude.md #97 rather than
        chosen here: a struct field whose own type is struct/arr/map
        auto-vivifies on the next reach-through, so deleting one
        releases its contents but a later `s.field.x` builds a fresh
        empty value rather than crashing on null. A scalar or text
        field genuinely reads null."""
        tgt = stmt.target
        obj_val, obj_type = self._emit_expr(tgt.obj, env, lines)

        if isinstance(obj_type, types_mod.MapType):
            # claude.md #175: count_ptr/tombstones_ptr are out-params
            # (delete removes a live entry or converts it to a
            # tombstone); capacity is read-only (delete never grows the
            # table).
            if tgt.computed:
                key_val, key_type = self._emit_expr(tgt.prop, env, lines)
            else:
                key_val, key_type = self.string_const(tgt.prop), None
            count_ptr = self.tmp()
            lines.append(f"  {count_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, "
                         f"ptr {obj_val}, i32 0, i32 0")
            entries_ptr = self.tmp()
            lines.append(f"  {entries_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, "
                         f"ptr {obj_val}, i32 0, i32 1")
            cap_ptr = self.tmp()
            lines.append(f"  {cap_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, "
                         f"ptr {obj_val}, i32 0, i32 2")
            cap_val = self.tmp()
            lines.append(f"  {cap_val} = load i64, ptr {cap_ptr}")
            tomb_ptr = self.tmp()
            lines.append(f"  {tomb_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, "
                         f"ptr {obj_val}, i32 0, i32 3")
            value_type = obj_type.value
            if _is_refcounted(value_type) or value_type == TEXT:
                tramp = self._emit_map_value_release_trampoline(value_type)
            else:
                tramp = "null"
            lines.append(f"  call i8 @festina_map_delete(ptr {count_ptr}, "
                         f"ptr {entries_ptr}, i64 {cap_val}, ptr {tomb_ptr}, "
                         f"ptr {key_val}, ptr {tramp})")
            if tgt.computed:
                self._free_text_temp(tgt.prop, key_val, key_type, lines)
            return

        ptr, ftype = self._member_ptr_from(obj_val, obj_type, tgt, lines)
        # Release what the field holds before nulling it -- the same
        # per-type dispatch a field REASSIGNMENT already performs, since
        # delete is a reassignment to null with one extra effect.
        #
        # claude.md #120: the null is stored BEFORE the old value is
        # released -- a cycle trial run by the release must never see
        # this field still pointing at the value whose count it just
        # dropped (see _emit_assign's store-before-release comment).
        old = None
        if _llvm_type(ftype) == "ptr":
            old = self.tmp()
            lines.append(f"  {old} = load ptr, ptr {ptr}")
        f_llvm = _llvm_type(ftype)
        null_const = {"ptr": "null", "i64": INT_NULL_CONST,
                      "double": FLOAT_NULL_CONST, "i8": BOOL_NULL_CONST}[f_llvm]
        lines.append(f"  store {f_llvm} {null_const}, ptr {ptr}")
        if _is_refcounted(ftype):
            # claude.md #118: covers img/aud fields too now -- one
            # release dispatch, no media special case left.
            lines.append(f"  call void {self._release_fn_for(ftype)}(ptr {old})")
        elif ftype == TEXT:
            lines.append(f"  call void @free(ptr {old})")

        if isinstance(obj_type, types_mod.TableType):
            # claude.md #111: clear the presence bit, so undefined()
            # reports a deleted column exactly like one the query never
            # selected. The mask lives one slot past the columns.
            cols = self.tables[obj_type.name]
            ncols = len(cols)
            idx = self.table_field_index(obj_type.name, tgt.prop)
            if idx < 64:
                mask_ptr = self.tmp()
                lines.append(f"  {mask_ptr} = getelementptr i8, ptr {obj_val}, "
                             f"i64 {ncols * 8}")
                mask = self.tmp()
                lines.append(f"  {mask} = load i64, ptr {mask_ptr}")
                cleared = self.tmp()
                # Signed decimal form -- LLVM's i64 literal grammar is
                # signed, so ~bit is emitted as a negative number.
                lines.append(f"  {cleared} = and i64 {mask}, {~(1 << idx)}")
                lines.append(f"  store i64 {cleared}, ptr {mask_ptr}")


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
                            # own struct widening below), extended by
                            # claude.md #81: a with-init local stack-
                            # allocates only when its initializer is a
                            # literal written directly here (see
                            # _is_stack_allocatable_array_or_map_decl's
                            # own comment, shared with _emit_stmt so this
                            # can never disagree with what it actually
                            # built) -- any other with-init shape (an
                            # existing identifier, a field read, a call
                            # result, ...) still always aliases its
                            # initializer's value and is always
                            # refcounted, regardless of its own escaping-
                            # ness. A no-init local is only refcounted
                            # (not stack-allocated) when escape_analysis
                            # actually proves it escapes. Either way, a
                            # name that's ever itself returned is not
                            # excluded here either -- Return's own
                            # handling retains first, same as for
                            # structs.
                            is_stack_allocated = self._is_stack_allocatable_array_or_map_decl(stmt, type_)
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
                            elif self._struct_has_own_managed_field(type_.name):
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
                        elif type_ == BLOB or type_ == REGEX or isinstance(
                                type_, (types_mod.ImageType, types_mod.AudioType)):
                            # claude.md #109: a blob local is ALWAYS
                            # scheduled for release, with no
                            # escaping-ness test and no
                            # is-this-a-fresh-source test. Both tests
                            # exist elsewhere to avoid releasing
                            # something this scope does not own, and
                            # neither applies here: a blob is
                            # refcounted, so its binding owns exactly
                            # one reference no matter where the value
                            # came from -- a fresh festina_blob_open
                            # starts the count at 1, and an alias of
                            # another blob was retained on the way in
                            # (see _emit_stmt's own VarDecl handling).
                            # Dropping that one reference is always
                            # correct and never frees a handle someone
                            # else still holds, which is the whole
                            # point of #109's "if a blob = another
                            # blob, copy the reference".
                            #
                            # claude.md #118: img/aud/regex carry the
                            # same header now, so the ownership proofs
                            # this branch used to demand for them
                            # (_OwnedImage/_OwnedAudio/_OwnedRegex's
                            # created-here + never-escaping tests) are
                            # gone along with those marker classes:
                            # counting replaced proving, for exactly the
                            # reason blob's own comment above gives. An
                            # escaping handle no longer leaks, and a
                            # /re/ literal's immortal header makes its
                            # release here a no-op rather than a
                            # use-after-free hazard.
                            self._active_free_locals[-1].append((ref, type_))
                        elif type_ == TEXT:
                            # claude.md #83: unlike the other three
                            # types, a text local is ALWAYS scheduled
                            # for scope-exit freeing here, regardless of
                            # whether it has an initializer or whether
                            # escape_analysis proves it escapes -- there
                            # is no stack-allocation option for a text
                            # local's own buffer to begin with (a
                            # dynamically-sized string was never a
                            # fixed-size alloca candidate), and, unlike
                            # struct/arr[T]/map[T]'s own shared,
                            # refcounted representation, a text local
                            # ALWAYS holds its own exclusively-owned
                            # copy no matter how many other bindings
                            # have READ it elsewhere (see
                            # _is_owning_text_source's own comment:
                            # copying happens at every consuming site,
                            # not by draining the source) -- so freeing
                            # it here can never affect any other
                            # binding, "escaping" or not.
                            self._active_free_locals[-1].append((ref, type_))
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
            if type_ == BLOB or type_ == REGEX or isinstance(
                    type_, (types_mod.ImageType, types_mod.AudioType)):
                # claude.md #109: `blob save = 'save.dat'` reads the file
                # and hands back a fresh handle with a refcount of 1;
                # `blob other = save` aliases the same handle and needs
                # its own +1. _is_owning_refcounted_source cannot tell
                # these apart on its own -- it answers "is this a Call",
                # and the path form is a StringLit or any other text
                # expression. What actually distinguishes them is
                # whether a coercion happened: _coerce turns text into a
                # festina_blob_open call, which is as fresh as any other
                # call result, so `vtype == TEXT` before coercion IS the
                # freshness test.
                #
                # claude.md #118: img/aud/regex take this exact branch
                # now that they carry the same header -- `img s =
                # 'x.png'` is the same coercion shape as the blob path
                # form, `img b = a` is the same alias-needs-a-retain
                # shape, and a /re/ literal initializer is immortal, so
                # the retain the freshness test emits for it, or skips,
                # is a no-op either way (it is classified fresh, so it
                # is skipped -- see _refcounted_source_is_fresh).
                uid = self._unique()
                slot = f"%{stmt.name}.{uid}"
                lines.append(f"  {slot} = alloca ptr")
                if stmt.init is None:
                    lines.append(f"  store ptr null, ptr {slot}")
                else:
                    val, vtype = self._emit_value_for(stmt.init, env, lines, type_)
                    fresh = self._refcounted_source_is_fresh(stmt.init, vtype, type_)
                    val = self._coerce(val, vtype, type_, lines, source_expr=stmt.init)
                    if not fresh:
                        lines.append(f"  call void @festina_retain(ptr {val})")
                    lines.append(f"  store ptr {val}, ptr {slot}")
                # No tracking call here: _emit_block scans each VarDecl
                # it emits and looks the name back up, so defining it is
                # all this branch owes the release machinery.
                env.define(stmt.name, slot, type_)
                return
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
                    val = self._coerce(val, vtype, type_, lines, source_expr=stmt.init)
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
                    # claude.md #176: routed through _emit_fresh_heap_header
                    # (widening to 16 extra bytes, tag first/refcount
                    # second, when this struct is a pure-struct enum
                    # member -- see that function's own comment) rather
                    # than the manual calloc this used to inline, so the
                    # tagging logic lives in exactly one place.
                    type_tag = (self._enum_tag_const(type_)
                                if type_.name in self._tagged_structs else None)
                    backing = self._emit_fresh_heap_header(struct_ty, lines, type_tag=type_tag)
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
                stack_allocatable = self._is_stack_allocatable_array_or_map_decl(stmt, type_)
                if stmt.init is not None and stack_allocatable:
                    # claude.md #81: a non-escaping local declared
                    # directly from an array/map literal -- see
                    # _is_stack_allocatable_array_or_map_decl's own
                    # comment for why this is safe. Builds the header
                    # straight into a stack slot (zero-initialized
                    # first, the same as the no-init case below, since
                    # _emit_array_lit/_emit_map_lit only ever WRITE
                    # fields they have a real value for -- an empty
                    # array literal's own data field, for one, still
                    # needs to start null) and hands it to
                    # _emit_array_lit/_emit_map_lit as `header` to build
                    # directly into, instead of routing through
                    # _emit_value_for's general (always-heap) path. No
                    # retain needed: a literal is always an "owning"
                    # source (_is_owning_refcounted_source), the same
                    # reason the general with-initializer path below
                    # already skips retaining one.
                    lines.append(f"  {backing} = alloca {payload_ty}")
                    lines.append(f"  store {payload_ty} zeroinitializer, ptr {backing}")
                    if isinstance(type_, types_mod.ArrayType):
                        self._emit_array_lit(stmt.init, env, lines, type_, header=backing)
                    else:
                        self._emit_map_lit(stmt.init, env, lines, type_, header=backing)
                    lines.append(f"  {slot} = alloca ptr")
                    lines.append(f"  store ptr {backing}, ptr {slot}")
                    env.define(stmt.name, slot, type_)
                    return
                if stmt.init is not None:
                    val, vtype = self._emit_value_for(stmt.init, env, lines, type_)
                    val = self._coerce(val, vtype, type_, lines, source_expr=stmt.init)
                    if not self._is_owning_refcounted_source(stmt.init):
                        lines.append(f"  call void @festina_retain(ptr {val})")
                    lines.append(f"  {slot} = alloca ptr")
                    lines.append(f"  store ptr {val}, ptr {slot}")
                    env.define(stmt.name, slot, type_)
                    return
                if stack_allocatable:
                    lines.append(f"  {backing} = alloca {payload_ty}")
                    lines.append(f"  store {payload_ty} zeroinitializer, ptr {backing}")
                else:
                    backing = self._emit_fresh_heap_header(payload_ty, lines)
                lines.append(f"  {slot} = alloca ptr")
                lines.append(f"  store ptr {backing}, ptr {slot}")
                env.define(stmt.name, slot, type_)
                # stmt.init is None here -- handled by the early-return
                # branches above.
                return
            if type_ == TEXT:
                # claude.md #83: text is copy-managed, not refcounted --
                # every declaration (with or without an initializer)
                # gets its own slot explicitly initialized (a bare
                # `store ptr null` for the no-init case, matching
                # struct/arr[T]/map[T]'s own zeroinitializer convention
                # just above -- this local's own value is scheduled for
                # unconditional freeing at scope-exit below, via
                # _active_free_locals, and freeing genuinely
                # uninitialized garbage would be a real, silent crash
                # risk the OTHER three types never had, since their own
                # no-init case was ALREADY always explicitly zeroed).
                # With an initializer, copies via festina_text_own only
                # when the source isn't itself owning -- the identical
                # rule every other with-initializer declaration in this
                # whole effort already follows.
                slot = f"%{stmt.name}.{self._unique()}"
                lines.append(f"  {slot} = alloca ptr")
                if stmt.init is not None:
                    val, vtype = self._emit_value_for(stmt.init, env, lines, type_)
                    val = self._coerce(val, vtype, type_, lines, source_expr=stmt.init)
                    if not self._is_owning_text_source(stmt.init):
                        owned = self.tmp()
                        lines.append(f"  {owned} = call ptr @festina_text_own(ptr {val})")
                        val = owned
                    lines.append(f"  store ptr {val}, ptr {slot}")
                else:
                    lines.append(f"  store ptr null, ptr {slot}")
                env.define(stmt.name, slot, type_)
                return
            llvm_ty = _llvm_type(type_)
            slot = f"%{stmt.name}.{self._unique()}"
            lines.append(f"  {slot} = alloca {llvm_ty}")
            env.define(stmt.name, slot, type_)
            if stmt.init is not None:
                val, vtype = self._emit_value_for(stmt.init, env, lines, type_)
                val = self._coerce(val, vtype, type_, lines, source_expr=stmt.init)
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
            if (_is_refcounted(vtype)
                    and self._is_owning_refcounted_source(stmt.expr)):
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
            elif vtype == TEXT and self._is_owning_text_source(stmt.expr):
                # claude.md #83: the text counterpart just above -- a
                # discarded text-returning call/template result is,
                # by the identical "owning" reasoning, provably this
                # statement's own sole reference, freed immediately via
                # a plain @free (never festina_release -- text has no
                # refcount header to check).
                lines.append(f"  call void @free(ptr {val})")
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
                # claude.md #140: down_to=self._current_func_frame_base,
                # not the implicit default of 0 -- see that field's own
                # comment. A bare 0 here frees every frame on the shared
                # stack, including an ENCLOSING function's still-live
                # locals whenever this Return is inside a nested
                # FuncDecl's own body.
                self._emit_free_active_locals(lines, down_to=self._current_func_frame_base)
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
                # claude.md #118: through _refcounted_source_is_fresh
                # rather than _is_owning_refcounted_source directly, so
                # `return 'x.png'` in an img/aud/blob function -- where
                # _coerce just emitted the load call, a fresh +1 --
                # is not retained a second time.
                if (_is_refcounted(return_type)
                        and not self._refcounted_source_is_fresh(
                            stmt.value, vtype, return_type)):
                    lines.append(f"  call void @festina_retain(ptr {val})")
                elif return_type == TEXT and not self._is_owning_text_source(stmt.value):
                    # claude.md #83: the text counterpart just above --
                    # copies (festina_text_own) rather than retains, for
                    # the identical reason: without it, a bare `return
                    # s` (aliasing a local) would hand the caller `s`'s
                    # own buffer, which _emit_free_active_locals is
                    # about to free right along with every other active
                    # local below, leaving the caller with a dangling
                    # pointer the instant this function returns.
                    owned = self.tmp()
                    lines.append(f"  {owned} = call ptr @festina_text_own(ptr {val})")
                    val = owned
                # claude.md #140: see the identical note on the other
                # Return branch just above -- down_to=self.
                # _current_func_frame_base, not an implicit 0.
                self._emit_free_active_locals(lines, down_to=self._current_func_frame_base)
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
        if isinstance(stmt, ast.TryStmt):
            self._emit_try(stmt, env, return_type, ctx)
            return
        if isinstance(stmt, ast.ThrowStmt):
            self.uses_try = True  # claude.md #157: see self.uses_try's own comment
            # claude.md #157: coerced to text exactly like fail() (see
            # the "fail" branch elsewhere in this method). UNLIKE
            # fail()/exit() though, throw frees every active local in
            # THIS function first -- self._emit_free_active_locals(down_to
            # =self._current_func_frame_base), the exact same call
            # Return makes just above -- because a throw CAUGHT by a
            # try in this same function keeps running afterward (the
            # process doesn't exit the way it does after fail()), so
            # anything declared between the try and here needs freeing
            # NOW: relying on _emit_block's own trailing "free my own
            # frame" cleanup (further down in program order) would never
            # run, since festina_throw's longjmp diverts control away
            # before that unreachable code executes -- a real leak a
            # direct Valgrind run against exactly this shape caught (see
            # claude.md #157). Locals in frames BELOW this function's
            # own base are correctly left alone: either an enclosing
            # try elsewhere in THIS function catches this (nothing below
            # its own frame base was ever this throw's to free), or it's
            # uncaught here and propagates out of the function entirely,
            # at which point this function's own frame -- all the way
            # down to _current_func_frame_base -- is gone regardless,
            # exactly like an ordinary Return.
            val, vtype = self._emit_expr(stmt.expr, env, lines)
            text_val = self._to_text(val, vtype, lines)
            if vtype == TEXT and not self._is_owning_text_source(stmt.expr):
                # claude.md #157: the identical hazard Return's own text
                # branch already guards against, and the identical fix
                # -- without this, `throw s` (aliasing a local) would
                # hand festina_throw a pointer that _emit_free_active_locals
                # is about to free right below, right along with every
                # other active local (a real use-after-free a direct
                # Valgrind run against exactly this shape caught). Any
                # non-text vtype needs no such guard -- _to_text always
                # produces a fresh buffer for those, never an alias of
                # an existing local.
                owned = self.tmp()
                lines.append(f"  {owned} = call ptr @festina_text_own(ptr {text_val})")
                text_val = owned
            self._emit_free_active_locals(lines, down_to=self._current_func_frame_base,
                                           skip_try_pop=True)
            lines.append(f"  call void @festina_throw(ptr {text_val})")
            return
        if isinstance(stmt, ast.FreeStmt):
            self._emit_free(stmt, env, lines)
            return
        if isinstance(stmt, ast.DeleteStmt):
            self._emit_delete(stmt, env, lines)
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
        if isinstance(stmt, ast.FuncDecl):
            # claude.md #140: a FuncDecl nested inside an if/while/for/
            # another function's body -- semantic.py's analyze_func
            # already treats one exactly like a top-level declaration
            # regardless of nesting (always global, see its own
            # comment), so this emits the identical top-level LLVM
            # function definition _emit_func always has (into
            # self.func_defs, unconditionally, once, via generate()'s
            # own pre-pass having already registered its signature) --
            # its own textual position in `lines` gets nothing at all,
            # the same "this position does nothing at runtime, hoisting
            # already made the function exist" semantics a JS function
            # declaration statement has.
            self._emit_func(stmt)
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

    def _emit_try(self, stmt, env, return_type, ctx):
        """claude.md #157: try { A } catch (name:text) { B }.

        The setjmp call is emitted RIGHT HERE, directly into the
        enclosing function's own IR -- not delegated to a runtime
        helper, which a first attempt at this tried and a direct test
        caught as broken: setjmp only captures a valid jump target
        while its OWN calling function's stack frame is still live, and
        a helper that calls it and then returns has already made that
        frame invalid by the time some later throw tries to jump back
        into it. Emitting it here means the "calling function" IS the
        one containing this try statement, which by construction can't
        have returned yet -- it can't exit before hitting one of the
        paths _TryFrameMarker instruments below.

        llvm.eh.sjlj.setjmp/llvm.eh.sjlj.longjmp (not libc's own
        setjmp/longjmp symbols) specifically because they're portable
        LLVM intrinsics with a fixed-size buffer, not a platform/libc-
        specific symbol name and struct layout -- the same mechanism
        clang itself lowers __builtin_setjmp/__builtin_longjmp to. 0
        means this is the first, normal arrival (run A); nonzero means
        a throw's __builtin_longjmp (festina_throw, in the C runtime --
        longjmp has no equivalent placement restriction, so it's free
        to live in an ordinary nested function) landed straight back
        here (run B).

        This is a plain two-way branch structurally, exactly like
        _emit_if just above -- the only difference is A's own frame
        (self._active_free_locals) gets one extra entry, a
        _TryFrameMarker, pushed in ITS OWN dedicated wrapper frame (not
        inside the one _emit_block(stmt.try_body, ...) manages itself)
        so that ANY exit from A -- normal fallthrough, return, break,
        continue, however deeply nested -- pops the runtime's catch
        frame via the exact same _emit_free_active_locals walk that
        already frees every other local on those exact same paths. A
        throw reached from directly inside A needs no special handling
        here at all: festina_throw pops the runtime's own frame itself
        (see its own comment) before the longjmp, so by the time B
        runs, both the runtime's bookkeeping and Python's
        self._active_free_locals are already consistent with "the try
        is over" -- nothing here needs to detect that it happened.
        """
        self.uses_try = True
        lines = ctx["lines"]
        buf = self.tmp()
        lines.append(f"  {buf} = alloca [5 x ptr], align 16")
        bufp = self.tmp()
        lines.append(f"  {bufp} = getelementptr inbounds [5 x ptr], ptr {buf}, i64 0, i64 0")
        frame_addr = self.tmp()
        lines.append(f"  {frame_addr} = call ptr @llvm.frameaddress.p0(i32 0)")
        lines.append(f"  store ptr {frame_addr}, ptr {bufp}, align 16")
        stack_save = self.tmp()
        lines.append(f"  {stack_save} = call ptr @llvm.stacksave.p0()")
        slot2 = self.tmp()
        lines.append(f"  {slot2} = getelementptr inbounds ptr, ptr {bufp}, i64 2")
        lines.append(f"  store ptr {stack_save}, ptr {slot2}, align 16")
        rc = self.tmp()
        lines.append(f"  {rc} = call i32 @llvm.eh.sjlj.setjmp(ptr {bufp})")
        is_catch = self.tmp()
        lines.append(f"  {is_catch} = icmp ne i32 {rc}, 0")
        try_label = self.label("try.body")
        catch_label = self.label("try.catch")
        end_label = self.label("try.end")
        lines.append(f"  br i1 {is_catch}, label %{catch_label}, label %{try_label}")

        self._start_block(try_label, lines)
        lines.append(f"  call void @festina_try_push(ptr {bufp})")
        tracking = self._current_escaping_names is not None
        if tracking:
            self._active_free_locals.append([(None, _TryFrameMarker())])
        try:
            try_ctx = self._emit_block(stmt.try_body, env, return_type, lines)
        finally:
            if tracking:
                if not try_ctx["terminated"]:
                    self._emit_free_active_locals(lines, down_to=len(self._active_free_locals) - 1)
                self._active_free_locals.pop()
        if not try_ctx["terminated"]:
            lines.append(f"  br label %{end_label}")

        self._start_block(catch_label, lines)
        # claude.md #157: festina_try_error() hands over an owned text
        # value (the runtime's own copy, made when the throw happened --
        # see its own comment) -- bound as an ordinary local exactly the
        # way _emit_block would bind any other with-init text VarDecl,
        # so its own scope-exit cleanup (below, via catch_env's frame)
        # is the SAME generic TEXT-local handling every other text local
        # already gets, not anything special-cased for this one.
        err_val = self.tmp()
        lines.append(f"  {err_val} = call ptr @festina_try_error()")
        err_slot = self.tmp()
        lines.append(f"  {err_slot} = alloca ptr")
        lines.append(f"  store ptr {err_val}, ptr {err_slot}")
        catch_env = Env(env)
        catch_env.define(stmt.catch_var, err_slot, TEXT)
        if tracking:
            self._active_free_locals.append([(err_slot, TEXT)])
        try:
            catch_ctx = self._emit_block(stmt.catch_body, catch_env, return_type, lines)
        finally:
            if tracking:
                if not catch_ctx["terminated"]:
                    self._emit_free_active_locals(lines, down_to=len(self._active_free_locals) - 1)
                self._active_free_locals.pop()
        if not catch_ctx["terminated"]:
            lines.append(f"  br label %{end_label}")

        if try_ctx["terminated"] and catch_ctx["terminated"]:
            ctx["terminated"] = True
        else:
            self._start_block(end_label, lines)

    _uid = 0

    def _unique(self):
        CodeGen._uid += 1
        return CodeGen._uid

    # ---- expressions ----
    def _coerce(self, val, from_type, to_type, lines, source_expr=None):
        # claude.md #55: int and float never convert implicitly, not even
        # on assignment -- semantic.py already rejects a mismatched
        # int/float assignment before codegen ever runs, so there is no
        # remaining case that needs a numeric promotion here. What's left
        # is genuinely permissive by design: a null literal (from_type is
        # None or NULL-ish) or an unconstrained builtin return (e.g.
        # sqlite()) flowing into a concretely-typed slot.
        #
        # claude.md #100/#101/#109 add the three conversions that are
        # not free: `aud music = 'track.wav'`, `img sprite =
        # 'sprite.png'` and `blob save = 'save.dat'`. colour and font
        # reach the same text -> X allowance in semantic.py's
        # check_assignable but need nothing here, being resolved by
        # their own literal handling. These three are real file reads,
        # emitted here so that every position that accepts one
        # (declaration, assignment, argument, field) gets it from a
        # single place rather than four.
        #
        # claude.md #109 is what put blob in this list. It used to
        # share text's representation outright and need no conversion
        # at all, which is precisely what made `blob data =
        # 'path/to/file'` store the path and never read the file --
        # claude.md #36's own example doing nothing it looked like it
        # did.
        # Every one of the three loaders below COPIES what it needs
        # from the path (strdup, fopen) and keeps no pointer into it, so
        # a path that was a temporary -- `img s = dir + 'a.png'`, or any
        # template literal -- is dead the moment the call returns and
        # must be freed. `source_expr` is what tells a temporary from a
        # variable's own buffer; without it _free_text_temp is a no-op,
        # which is why every call site that can coerce a computed path
        # threads it through.
        #
        # claude.md #109: this was a real, pre-existing leak in the
        # aud/img cases (claude.md #100/#101), not something blob
        # introduced -- blob merely made it easy to hit, since a path
        # built per iteration is the ordinary way to use one. Measured
        # at 1,029 bytes over 49 iterations for `img s = `${dir}x.png``
        # before this existed.
        if to_type == BLOB and from_type == TEXT:
            out = self.tmp()
            lines.append(f"  {out} = call ptr @festina_blob_open(ptr {val})")
            self._free_text_temp(source_expr, val, TEXT, lines)
            return out
        if isinstance(to_type, types_mod.AudioType) and from_type == TEXT:
            self.uses_audio = True
            out = self.tmp()
            lines.append(f"  {out} = call ptr @festina_load_audio(ptr {val})")
            self._free_text_temp(source_expr, val, TEXT, lines)
            return out
        # claude.md #101: `img sprite = 'sprite.png'`, the exact
        # counterpart of the aud case just above. uses_graphics_CODE,
        # not uses_graphics: decoding an image needs no X server, and
        # setting the stronger flag would make a headless program that
        # merely loads a sprite die on "could not open the X display"
        # -- exactly the artificial restriction loadImage() already
        # avoids for the same reason (see _emit_graphics_call).
        if isinstance(to_type, types_mod.ImageType) and from_type == TEXT:
            self.uses_graphics_code = True
            out = self.tmp()
            lines.append(f"  {out} = call ptr @festina_load_image(ptr {val})")
            self._free_text_temp(source_expr, val, TEXT, lines)
            return out
        # claude.md #176: a member type coercing into its enum "pseudo
        # type" -- e.g. Circle -> Shape for `enum Shape = Circle,
        # Square`. semantic.py's check_assignable already confirmed
        # from_type is a real member of to_type before codegen ever
        # runs (see AnalyzedProgram.enums/self.enums).
        if isinstance(to_type, types_mod.EnumType):
            info = self.enums.get(to_type.name)
            if info is not None and from_type in info.members:
                if info.is_pure_struct:
                    # A pure-struct enum value already IS whichever
                    # member struct's own pointer it holds -- self-
                    # tagged in that struct's own widened header at
                    # CONSTRUCTION time (see _emit_fresh_heap_header),
                    # not here. Nothing to build; identity.
                    return val
                # Mixed enum: build a fresh, independently-refcounted
                # heap box {tag, value} (FESTINA_ENUM_BOX_LLVM_TYPE).
                # The inner value gets the identical "retain/copy
                # unless the source is already a fresh, uniquely-owned
                # value" treatment every other fresh-container
                # construction site in this file already follows
                # (_emit_map_set, array/map literal construction) --
                # the box is a genuinely NEW owner of it.
                stored_val = val
                if _is_refcounted(from_type):
                    if not self._refcounted_source_is_fresh(source_expr, from_type, from_type):
                        lines.append(f"  call void @festina_retain(ptr {val})")
                elif from_type == TEXT:
                    if not self._is_owning_text_source(source_expr):
                        owned = self.tmp()
                        lines.append(f"  {owned} = call ptr @festina_text_own(ptr {val})")
                        stored_val = owned
                tag_const = self._enum_tag_const(from_type)
                box = self._emit_fresh_heap_header(FESTINA_ENUM_BOX_LLVM_TYPE, lines)
                tag_ptr = self.tmp()
                lines.append(f"  {tag_ptr} = getelementptr {FESTINA_ENUM_BOX_LLVM_TYPE}, ptr {box}, i32 0, i32 0")
                lines.append(f"  store ptr {tag_const}, ptr {tag_ptr}")
                val_ptr = self.tmp()
                lines.append(f"  {val_ptr} = getelementptr {FESTINA_ENUM_BOX_LLVM_TYPE}, ptr {box}, i32 0, i32 1")
                raw_val = self._map_value_to_i64(stored_val, from_type, lines)
                lines.append(f"  store i64 {raw_val}, ptr {val_ptr}")
                return box
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
        if isinstance(expr, ast.ArrowFuncExpr):
            # claude.md #142: semantic.py's own ArrowFuncExpr handling
            # already built and fully analyzed `expr.decl` (a
            # synthesized, uniquely-named ast.FuncDecl) and stashed it
            # on this SAME AST node -- codegen re-walks the identical
            # object (see festina/cli.py's compile_file), so this reads
            # it back directly rather than re-synthesizing an
            # independent name. Registered and emitted HERE, once
            # (guarded by self.func_decls, the same "already emitted?"
            # check every other synthesized-function site in this file
            # already uses), rather than through generate()'s own
            # whole-program pre-pass -- an arrow function has no name
            # anything could forward-reference, so it only ever needs
            # to exist by the time this expression itself is emitted.
            decl = expr.decl
            if decl.name not in self.func_decls:
                self._register_func_signature(decl)
                self._emit_func(decl)
            param_types = tuple(self._resolve(p.type_expr, decl) for p in decl.params)
            ret_type = None if decl.return_type == "void" else self._resolve(decl.return_type, decl)
            return f"@{decl.name}", types_mod.FuncType(param_types, ret_type)
        if isinstance(expr, ast.Identifier):
            # claude.md #39: clientWidth/clientHeight -- a bare
            # identifier, not a Call, so this can't go through the usual
            # builtin-function dispatch; read the canvas's *current*
            # size from the runtime (not a compile-time constant, since
            # `on resize` can change it after startup -- see
            # festina_client_width/_height's own doc comment).
            #
            # claude.md #95: this no longer opens a window either. It
            # used to, on the reasoning that asking for the size implies
            # a window -- but the canvas has a size (800x600 until a
            # resize) whether or not it is on screen, and forcing a
            # window here would defeat headless rendering for the very
            # common case of a program that asks how big its canvas is
            # before drawing into it.
            if expr.name in ("clientWidth", "clientHeight"):
                self.uses_graphics_code = True
                fn = "festina_client_width" if expr.name == "clientWidth" else "festina_client_height"
                out = self.tmp()
                lines.append(f"  {out} = call i64 @{fn}()")
                return out, INT
            # claude.md #139: screenWidth/screenHeight -- the PHYSICAL
            # display's resolution, unlike clientWidth/clientHeight's
            # in-memory canvas size. This genuinely cannot be answered
            # without a live connection to the display server (there is
            # no "headless" answer to "how big is the screen"), so
            # unlike clientWidth/clientHeight this sets uses_graphics_
            # CODE only (link the graphics backend) rather than the
            # stronger uses_graphics (which would force a window open
            # and block in the event loop afterward) -- the connect/
            # query/disconnect-if-newly-opened dance happens entirely
            # inside festina_screen_width/_height themselves, invisible
            # here, the same "only pay for what you use" split
            # loadImage()/img-from-path already established for
            # graphics code that needs no window.
            if expr.name in ("screenWidth", "screenHeight"):
                self.uses_graphics_code = True
                fn = "festina_screen_width" if expr.name == "screenWidth" else "festina_screen_height"
                out = self.tmp()
                lines.append(f"  {out} = call i64 @{fn}()")
                return out, INT
            # claude.md #181: devicePixelRatio -- the display's own
            # pixel density, exactly the same "needs a live connection,
            # uses_graphics_code only" shape as screenWidth/screenHeight
            # just above (a display property, not the in-memory canvas
            # size clientWidth/clientHeight answer), just float-typed
            # rather than int since a ratio like 1.5 is meaningful here
            # in a way a pixel count never is.
            if expr.name == "devicePixelRatio":
                self.uses_graphics_code = True
                out = self.tmp()
                lines.append(f"  {out} = call double @festina_device_pixel_ratio()")
                return out, FLOAT
            if expr.name in self.func_decls:
                # claude.md #141: a bare reference to a function's own
                # NAME, not immediately called -- the function's own
                # global symbol (@name) IS its first-class VALUE, no
                # separate "address-of" step needed the way a local
                # variable's own storage would need a load: LLVM already
                # treats a function symbol as a plain `ptr` constant.
                # Mirrors semantic.py's own infer() special-case for
                # ast.Identifier -- same "build the FuncType fresh from
                # the FuncDecl every time" choice, for the identical
                # reason (self.func_decls stores raw AST, not a cached
                # resolved signature, and _register_func_signature
                # already proved this resolve() is cheap and repeatable).
                decl = self.func_decls[expr.name]
                param_types = tuple(self._resolve(p.type_expr, decl) for p in decl.params)
                ret_type = (None if decl.return_type == "void"
                            else self._resolve(decl.return_type, decl))
                return f"@{expr.name}", types_mod.FuncType(param_types, ret_type)
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
            # claude.md #93: Math.PI / Math.E -- compile-time constants,
            # emitted as raw double bit patterns for the same reason
            # FLOAT_NULL_CONST is (see the module docstring): decimal
            # text would round-trip through the IR parser and could lose
            # the last bit.
            if (not expr.computed and isinstance(expr.obj, ast.Identifier)
                    and expr.obj.name == "Math"
                    and expr.prop in semantic_mod.MATH_CONSTANTS):
                raw = struct.unpack("<Q", struct.pack(
                    "<d", semantic_mod.MATH_CONSTANTS[expr.prop]))[0]
                return f"0x{raw:016X}", FLOAT
            # claude.md #92: img.width / img.height -- a runtime call
            # rather than a field read, since an `img` is a pointer to a
            # box whose Cairo surface owns the real dimensions (and
            # resize() replaces that surface underneath it, so caching
            # them anywhere would go stale).
            if not expr.computed and expr.prop in ("width", "height"):
                obj_val, obj_type = self._emit_expr(expr.obj, env, lines)
                if isinstance(obj_type, types_mod.ImageType):
                    fn = ("festina_image_width" if expr.prop == "width"
                          else "festina_image_height")
                    out = self.tmp()
                    lines.append(f"  {out} = call i64 @{fn}(ptr {obj_val})")
                    # claude.md #119: the int is independent of the
                    # image, so an owning receiver (`sheet.clip(...)
                    # .width`) is released here rather than leaked.
                    self._release_owned_receiver(expr.obj, obj_val, obj_type, lines)
                    return out, INT
                # A struct/table field genuinely named "width" or
                # "height" is perfectly legal, so it still resolves the
                # ordinary way -- reusing the object value emitted just
                # above rather than emitting expr.obj a second time,
                # which would run any side effects in it twice.
                ptr, ftype = self._member_ptr_from(obj_val, obj_type, expr, lines)
                return self._load_field_value(ptr, ftype, lines)
            if not expr.computed and expr.prop in ("port", "method", "path", "headers", "state"):
                obj_val, obj_type = self._emit_expr(expr.obj, env, lines)
                result = self._emit_http_socket_field(expr, obj_val, obj_type, lines)
                if result is not None:
                    return result
                # A struct/table field genuinely named one of these is
                # perfectly legal (mirroring img.width/.height's own
                # fallthrough just above) -- resolves the ordinary way.
                ptr, ftype = self._member_ptr_from(obj_val, obj_type, expr, lines)
                return self._load_field_value(ptr, ftype, lines)
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
                # claude.md #108: .length participates in the member
                # chain too. It never did, which made
                # the receiver-release docstring wrong
                # about `rowsFor(x).length` -- that shape does not reach
                # _emit_member_load at all, so #102 never covered it and
                # it leaked the whole array (measured: 2,880 bytes over
                # 60 iterations). A length is an i64 copy that owes the
                # array nothing, so the receiver is always releasable
                # here, whether it is the call itself or the base of a
                # longer chain (`make().inner.items.length`).
                state = self._begin_member_chain(expr)
                try:
                    obj_val, obj_type = self._emit_expr(expr.obj, env, lines)
                finally:
                    pending = self._end_member_chain(state)
                if isinstance(obj_type, types_mod.ArrayType):
                    len_ptr = self.tmp()
                    lines.append(f"  {len_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 0")
                    out = self.tmp()
                    lines.append(f"  {out} = load i64, ptr {len_ptr}")
                    if pending is not None:
                        self._release_member_chain(pending, expr.obj, obj_val,
                                                   obj_type, INT, lines)
                    return out, INT
            if expr.computed:
                # claude.md #26/#72: arr[i] / map[key] -- expr.obj is
                # emitted exactly once here, then branched on by type,
                # rather than delegating to _member_ptr (which would
                # emit it again from scratch) -- correct not just
                # efficient, since expr.obj could be an arbitrary
                # expression with side effects (e.g. a function call
                # returning an array or map).
                # claude.md #150: s[i] -- a compile-time-constant
                # receiver AND index (`'hello'[1]`) is resolved in
                # Python directly, the same "offload it out of the
                # compiled program entirely" treatment .toInt() just
                # above gives a literal receiver -- checked BEFORE
                # emitting expr.obj at all, since a StringLit needs no
                # emission in the first place (a bare _const_string
                # reference costs nothing either way, but skipping the
                # call entirely is the more honest "did no runtime work"
                # answer). Only folds a literal, non-negative int index
                # (`ast.NumberLit`) -- a negative index (`-1]`, parsed as
                # UnaryOp(-, NumberLit(1))) or any other expression falls
                # through to the runtime path below, which already
                # handles both correctly.
                if (isinstance(expr.obj, ast.StringLit) and isinstance(expr.prop, ast.NumberLit)
                        and isinstance(expr.prop.value, int) and not isinstance(expr.prop.value, bool)):
                    chars = list(expr.obj.value)
                    idx = expr.prop.value
                    if 0 <= idx < len(chars):
                        return self._const_string(chars[idx], lines), TEXT
                    return "null", TEXT
                obj_val, obj_type = self._emit_expr(expr.obj, env, lines)
                if isinstance(obj_type, types_mod.MapType):
                    key_val, key_type = self._emit_expr(expr.prop, env, lines)
                    out = self._emit_map_get(obj_val, obj_type.value, key_val, lines)
                    # claude.md #97: festina_map_get only READS the key
                    # (it strcmp's against each entry's own copy), so a
                    # key this expression allocated -- `m[`k${i}`]` --
                    # is finished the moment the lookup returns.
                    self._free_text_temp(expr.prop, key_val, key_type, lines)
                    out = self._mint_and_release_computed(
                        expr, out[0], obj_val, obj_type, obj_type.value, lines)
                    return out, obj_type.value
                if obj_type == TEXT:
                    # claude.md #150: unlike arr[text][i] (a BORROWED
                    # pointer into the array's own storage, see
                    # _mint_and_release_computed's own "a scalar element
                    # needs no minting" branch just above), this always
                    # calls a runtime function that mallocs a genuinely
                    # fresh, exclusively-owned one-code-point buffer --
                    # so this expression node is unconditionally marked
                    # minted (`_minted_values`), the same set
                    # _mint_and_release_computed itself populates for a
                    # refcounted element read through an owning
                    # container, so _is_owning_text_source's own
                    # _member_chain_call_base check (which reads that
                    # exact set) correctly treats the result as already
                    # owned everywhere it's later bound, passed, or
                    # freed as an unused temp -- with NO extra copy and
                    # NO leak either way.
                    idx_val, _ = self._emit_expr(expr.prop, env, lines)
                    out = self.tmp()
                    lines.append(f"  {out} = call ptr @festina_text_char_at(ptr {obj_val}, i64 {idx_val})")
                    self._free_text_temp(expr.obj, obj_val, obj_type, lines)
                    self._minted_values.add(id(expr))
                    return out, TEXT
                if not isinstance(obj_type, types_mod.ArrayType):
                    raise CodegenError(f"cannot index into {types_mod.type_name(obj_type)}",
                                        file=self.filename, line=getattr(expr, "line", 0))
                idx_val, _ = self._emit_expr(expr.prop, env, lines)
                ptr, elem_type = self._array_elem_ptr(obj_val, obj_type, idx_val, lines)
                out = self.tmp()
                lines.append(f"  {out} = load {_llvm_type(elem_type)}, ptr {ptr}")
                out = self._mint_and_release_computed(
                    expr, out, obj_val, obj_type, elem_type, lines)
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
        if isinstance(expr, ast.TypeofExpr):
            return self._emit_typeof(expr, env, lines)
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
        # Skips a @festina_str_concat call entirely for every EMPTY
        # literal piece (`` `${x}` ``'s own leading/trailing parts are
        # both "", and adjacent interpolations like `` `${a}${b}` `` have
        # an empty piece between them too) -- concatenating with "" is
        # always a no-op, so emitting the call at all was pure wasted
        # allocation+copy work, doubling the concat count for the
        # extremely common "starts or ends with an interpolation"
        # shape. `expr.exprs` is never empty (parse_template only builds
        # a TemplateLit once at least one `${...}` has been parsed -- a
        # template with none is a plain ast.StringLit instead), so the
        # loop below always runs at least once and `result` is
        # guaranteed non-None by the time it's returned.
        #
        # claude.md #83: every @festina_str_concat call mallocs a fresh
        # buffer and copies both operands into it, leaving the operands
        # themselves untouched -- so a template that chains several of
        # them leaks every intermediate unless each one is freed the
        # moment the next concat has finished copying out of it.
        # `result_owned`/`piece_owned` track exactly that: whether the
        # pointer in hand is a buffer THIS template allocated (free it
        # once consumed) or someone else's storage -- a `.str.N` literal
        # constant, or another binding's own buffer -- which must never
        # be freed here. A non-TEXT piece is always owned, because
        # _to_text's int/float/bool conversions all allocate.
        #
        # The template's own RESULT must in turn always be a fresh,
        # exclusively-owned buffer, since _is_owning_text_source treats
        # every TemplateLit as owning. Every concat output already
        # satisfies that; the one shape that doesn't is a template that
        # never concatenates at all -- a bare `` `${name}` ``, which
        # would otherwise hand back `name`'s own current buffer,
        # indistinguishable from it, so that freeing either one would
        # leave the other dangling. That case, and only that case, takes
        # a festina_text_own copy on the way out.
        result = self._const_string(expr.parts[0], lines) if expr.parts[0] else None
        result_owned = False
        for part_expr, next_part in zip(expr.exprs, expr.parts[1:]):
            val, vtype = self._emit_expr(part_expr, env, lines)
            piece = self._to_text(val, vtype, lines)
            piece_owned = vtype != TEXT or self._is_owning_text_source(part_expr)
            if result is None:
                result, result_owned = piece, piece_owned
            else:
                out = self.tmp()
                lines.append(f"  {out} = call ptr @festina_str_concat(ptr {result}, ptr {piece})")
                if result_owned:
                    lines.append(f"  call void @free(ptr {result})")
                if piece_owned:
                    lines.append(f"  call void @free(ptr {piece})")
                result, result_owned = out, True
            if next_part:
                part_str = self._const_string(next_part, lines)
                out2 = self.tmp()
                lines.append(f"  {out2} = call ptr @festina_str_concat(ptr {result}, ptr {part_str})")
                if result_owned:
                    lines.append(f"  call void @free(ptr {result})")
                result, result_owned = out2, True
        if not result_owned:
            owned = self.tmp()
            lines.append(f"  {owned} = call ptr @festina_text_own(ptr {result})")
            result = owned
        return result

    def _to_text(self, val, type_, lines):
        """claude.md #114: every non-text value in log() or `${}`
        compiles as its .toText() -- int/float/bool through the
        stringifiers they always had, struct/table/arr/map through a
        generated JSON-like render, and (claude.md #115) a blob through
        its own toText(), because a blob is very often a text file and
        the implicit conversion is DEFINED as the method it already
        has. img and aud are COMPILE ERRORS: they have no text form at
        all, and silently printing a placeholder would hide a mistake
        the type system can catch."""
        if type_ == TEXT:
            return val
        out = self.tmp()
        if type_ == INT:
            lines.append(f"  {out} = call ptr @festina_str_from_int(i64 {val})")
        elif type_ == FLOAT:
            lines.append(f"  {out} = call ptr @festina_str_from_float(double {val})")
        elif type_ == BOOL:
            lines.append(f"  {out} = call ptr @festina_str_from_bool(i8 {val})")
        elif isinstance(type_, (types_mod.StructType, types_mod.TableType,
                                types_mod.ArrayType, types_mod.MapType)):
            fn = self._json_fn_for(type_)
            sb = self.tmp()
            lines.append(f"  {sb} = call ptr @festina_sb_new()")
            lines.append(f"  call void {fn}(ptr {val}, ptr {sb}, i64 0)")
            lines.append(f"  {out} = call ptr @festina_sb_finish(ptr {sb})")
        elif type_ == BLOB:
            # claude.md #115: the contents. A binary blob renders its
            # bytes up to the first NUL -- which is exactly what its
            # explicit toText() does, and the two must not disagree.
            lines.append(f"  {out} = call ptr @festina_blob_to_text(ptr {val})")
        elif isinstance(type_, (types_mod.ImageType, types_mod.AudioType)):
            raise CodegenError(
                f"a value of type {types_mod.type_name(type_)} has no text "
                f"form and cannot appear in log() or a template",
                file=self.filename)
        else:
            raise CodegenError(f"cannot interpolate a value of type {types_mod.type_name(type_)}")
        return out

    def _emit_sendable_body(self, val, vtype, lines):
        """claude.md #151: http.send()/socket.send()'s `data:any`
        argument -- reuses _to_text for every type it already gives a
        text form to (see semantic.py's _is_sendable_type, the
        identical set), EXCEPT blob, sent as its own raw bytes rather
        than decoded through toText() (a response/frame body is much
        more likely to be genuinely binary than a log() argument
        ever is).

        Returns (data_ptr, len_val, temp_to_free): `data_ptr`/`len_val`
        are the raw bytes to send. `temp_to_free` is a freshly
        allocated scratch buffer THIS conversion made (free it via a
        plain @free only AFTER actually using data_ptr/len_val, e.g.
        after the send call itself) -- None when data_ptr aliases
        something the caller already owns some other way (`val`
        itself, when vtype is already TEXT and _to_text is a no-op
        passthrough; the blob's own internal storage, borrowed rather
        than copied). `val`/`vtype`'s own ownership is always the
        caller's separate responsibility, exactly like any other
        consumed argument (see exec()'s own argument-cleanup
        pattern) -- this never touches it."""
        if vtype == BLOB or isinstance(vtype, (types_mod.ImageType, types_mod.AudioType)):
            # claude.md #162: an http literal's own 'body' key
            # additionally accepts img/aud (see semantic.py's
            # _is_http_body_type -- a real request/response body
            # uploading or returning a picture/clip is completely
            # ordinary, unlike socket.send()'s data:any, which never
            # reaches this img/aud branch at all since
            # _is_sendable_type -- the ORIGINAL, narrower predicate --
            # still gates it). festina_image_bytes/_audio_bytes share
            # festina_blob_bytes's own (handle, len_out) -> borrowed-
            # bytes-ptr shape exactly (claude.md #101's own sqlite
            # blob-column binding already established this), so one
            # branch covers all three.
            if vtype == BLOB:
                fn = "festina_blob_bytes"
            elif isinstance(vtype, types_mod.AudioType):
                self.uses_audio = True
                fn = "festina_audio_bytes"
            else:
                self.uses_graphics_code = True
                fn = "festina_image_bytes"
            len_ptr = self.tmp()
            lines.append(f"  {len_ptr} = alloca i64")
            data = self.tmp()
            lines.append(f"  {data} = call ptr @{fn}(ptr {val}, ptr {len_ptr})")
            len_val = self.tmp()
            lines.append(f"  {len_val} = load i64, ptr {len_ptr}")
            return data, len_val, None
        text_val = self._to_text(val, vtype, lines)
        len_val = self.tmp()
        lines.append(f"  {len_val} = call i64 @strlen(ptr {text_val})")
        return text_val, len_val, (text_val if vtype != TEXT else None)

    def _emit_http_lit(self, maplit, env, lines):
        """claude.md #162 (extended by #163's `callback`): `http x =
        {...}` -- and `req.send({...})`'s own inline-response form --
        build a fresh http value via festina_http_literal_new from a
        MapLit's entries. semantic.py's own _validate_http_lit already
        confirmed every key is one of url/method/code/headers/body/
        callback with the right value type, so this only has to
        emit+coerce each one and make the single call; entries are
        evaluated in the SOURCE ORDER they appear (matching every other
        expression-evaluation-order convention in this compiler), a key
        simply never mentioned in the literal keeps
        festina_http_literal_new's own zero-value default for it (empty
        text for url/method, festina_null_int() for code, an empty map
        for headers, no body, `null` -- no callback -- for callback)."""
        self.uses_http = True
        url_val = self.string_const("")
        method_val = self.string_const("")
        code_val = INT_NULL_CONST
        headers_val = "null"
        body_ptr = "null"
        body_len_val = "0"
        callback_val = "null"
        cleanups = []  # [(callable taking no args)] run AFTER the literal_new call
        for key_expr, val_expr in maplit.entries:
            key = key_expr.value
            if key == "url":
                v, t = self._emit_expr(val_expr, env, lines)
                url_val = self._to_text(v, t, lines)
                cleanups.append(lambda v=v, t=t, e=val_expr: self._free_text_temp(e, v, t, lines))
            elif key == "method":
                v, t = self._emit_expr(val_expr, env, lines)
                method_val = self._to_text(v, t, lines)
                cleanups.append(lambda v=v, t=t, e=val_expr: self._free_text_temp(e, v, t, lines))
            elif key == "code":
                code_val, _ = self._emit_expr(val_expr, env, lines)
            elif key == "headers":
                v, t = self._emit_expr(val_expr, env, lines)
                # claude.md #162: festina_http_literal_new takes
                # OWNERSHIP of `headers` -- an owning source (a fresh
                # {...} literal, a call result) hands it in directly;
                # an aliasing one (an existing map[text] variable, the
                # `{headers}` shorthand's own common case) needs one
                # extra retain first, so the original binding keeps its
                # own valid reference exactly the same way any other
                # "pass a live container into something that will hold
                # onto it" call site in this compiler already does.
                if not self._is_owning_refcounted_source(val_expr):
                    lines.append(f"  call void @festina_retain(ptr {v})")
                headers_val = v
            elif key == "body":
                v, t = self._emit_expr(val_expr, env, lines)
                body_ptr, body_len_val, body_temp = self._emit_sendable_body(v, t, lines)

                def _cleanup_body(v=v, t=t, e=val_expr, temp=body_temp):
                    if temp is not None:
                        lines.append(f"  call void @free(ptr {temp})")
                    if _is_refcounted(t) and self._is_owning_refcounted_source(e):
                        lines.append(f"  call void {self._release_fn_for(t)}(ptr {v})")
                    else:
                        self._free_text_temp(e, v, t, lines)
                cleanups.append(_cleanup_body)
            elif key == "callback":
                # claude.md #163: a bare function pointer -- the SAME
                # runtime representation every other FuncType-typed
                # value already has (types.py's own doc comment:
                # "immortal for the life of the process"), so there is
                # nothing to retain, release, or free here at all --
                # unlike every other key above, this one is just "emit
                # the expression, use its value directly."
                callback_val, _ = self._emit_expr(val_expr, env, lines)
        out = self.tmp()
        lines.append(f"  {out} = call ptr @festina_http_literal_new(ptr {url_val}, ptr {method_val}, "
                     f"i64 {code_val}, ptr {headers_val}, ptr {body_ptr}, i64 {body_len_val}, "
                     f"ptr {callback_val})")
        for cleanup in cleanups:
            cleanup()
        return out, types_mod.HttpType()

    def _json_append_slot(self, body, sb, ftype, slot_ptr, depth_val):
        """claude.md #114: appends ONE value (stored at `slot_ptr`, of
        festina type `ftype`) to the builder -- the shared field/element/
        column emitter every generated render function is built from."""
        if ftype == INT:
            v = self.tmp()
            body.append(f"  {v} = load i64, ptr {slot_ptr}")
            body.append(f"  call void @festina_sb_append_json_int(ptr {sb}, i64 {v})")
        elif ftype == FLOAT:
            v = self.tmp()
            body.append(f"  {v} = load double, ptr {slot_ptr}")
            body.append(f"  call void @festina_sb_append_json_float(ptr {sb}, double {v})")
        elif ftype == BOOL:
            v = self.tmp()
            body.append(f"  {v} = load i8, ptr {slot_ptr}")
            body.append(f"  call void @festina_sb_append_json_bool(ptr {sb}, i8 {v})")
        elif ftype == TEXT:
            v = self.tmp()
            body.append(f"  {v} = load ptr, ptr {slot_ptr}")
            body.append(f"  call void @festina_sb_append_json_text(ptr {sb}, ptr {v})")
        elif isinstance(ftype, (types_mod.StructType, types_mod.TableType,
                                types_mod.ArrayType, types_mod.MapType)):
            v = self.tmp()
            body.append(f"  {v} = load ptr, ptr {slot_ptr}")
            inner = self._json_fn_for(ftype)
            d = self.tmp()
            body.append(f"  {d} = add i64 {depth_val}, 1")
            body.append(f"  call void {inner}(ptr {v}, ptr {sb}, i64 {d})")
        else:
            # blob/img/aud/regex/... inside a container: a labeled
            # placeholder, or null when the handle is null. Erroring the
            # whole container over one opaque field would make rendering
            # useless for exactly the debugging it exists for.
            label = self.string_const(f'"<{types_mod.type_name(ftype)}>"')
            v = self.tmp()
            body.append(f"  {v} = load ptr, ptr {slot_ptr}")
            body.append(f"  call void @festina_sb_append_handle(ptr {sb}, ptr {v}, ptr {label})")

    def _json_fn_for(self, type_):
        """claude.md #114: returns (generating on first use, cached by
        type name) `void @__festina_json_N(ptr value, ptr sb, i64 depth)`
        -- appends the value's JSON-like form to the builder. Registered
        BEFORE the body is generated, the same trick the release
        wrappers use (claude.md #106's own load-bearing cache write), so
        a self-referencing struct type generates ONE function that calls
        itself. The runtime recursion is depth-capped at 32: a cyclic
        value (constructible since #106) renders as null at the cap
        instead of overflowing the stack -- JSON.stringify throws on
        cycles, but a DEBUG rendering that can crash the program it is
        debugging would be worse than an honest truncation."""
        key = types_mod.type_name(type_)
        cached = self._json_fns.get(key)
        if cached is not None:
            return cached
        fn_name = f"@__festina_json_{self._unique()}"
        self._json_fns[key] = fn_name

        body = [f"define void {fn_name}(ptr %v, ptr %sb, i64 %depth) {{", "entry:"]
        null_lbl = self.label("json.null")
        deep_lbl = self.label("json.deep")
        go_lbl = self.label("json.go")
        isnull = self.tmp()
        body.append(f"  {isnull} = icmp eq ptr %v, null")
        body.append(f"  br i1 {isnull}, label %{null_lbl}, label %{deep_lbl}")
        body.append(f"{null_lbl}:")
        body.append(f"  call void @festina_sb_append(ptr %sb, ptr {self.string_const('null')})")
        body.append("  ret void")
        body.append(f"{deep_lbl}:")
        toodeep = self.tmp()
        body.append(f"  {toodeep} = icmp sgt i64 %depth, 32")
        body.append(f"  br i1 {toodeep}, label %{null_lbl}, label %{go_lbl}")
        body.append(f"{go_lbl}:")

        if isinstance(type_, types_mod.StructType):
            for idx, (fname, ftype) in enumerate(self.struct_fields(type_.name)):
                prefix = '{"' if idx == 0 else ',"'
                body.append(f"  call void @festina_sb_append(ptr %sb, "
                            f"ptr {self.string_const(prefix + fname + chr(34) + ':')})")
                fp = self.tmp()
                struct_ty = self.struct_llvm_name(type_.name)
                body.append(f"  {fp} = getelementptr {struct_ty}, ptr %v, i32 0, i32 {idx}")
                self._json_append_slot(body, "%sb", ftype, fp, "%depth")
            if not self.struct_fields(type_.name):
                body.append(f"  call void @festina_sb_append(ptr %sb, ptr {self.string_const('{')})")
            body.append(f"  call void @festina_sb_append(ptr %sb, ptr {self.string_const('}')})")

        elif isinstance(type_, types_mod.TableType):
            # A row: flat 8-byte slots, plus #111's presence mask one
            # slot past the columns. An UNDEFINED column is omitted
            # outright -- exactly what JSON.stringify does for an
            # undefined property, which is the analogy #111 built.
            fields = self.table_fields(type_.name)
            ncols = len(fields)
            first_slot = self.tmp()
            body.append(f"  {first_slot} = alloca i8")
            body.append(f"  store i8 1, ptr {first_slot}")
            body.append(f"  call void @festina_sb_append(ptr %sb, ptr {self.string_const('{')})")
            mask_ptr = self.tmp()
            body.append(f"  {mask_ptr} = getelementptr i8, ptr %v, i64 {ncols * 8}")
            mask = self.tmp()
            body.append(f"  {mask} = load i64, ptr {mask_ptr}")
            for idx, (fname, ftype) in enumerate(fields):
                skip_lbl = self.label(f"json.skip{idx}")
                emit_lbl = self.label(f"json.emit{idx}")
                sep_lbl = self.label(f"json.sep{idx}")
                key_lbl = self.label(f"json.key{idx}")
                if idx < 64:
                    bit = self.tmp()
                    body.append(f"  {bit} = and i64 {mask}, {1 << idx}")
                    present = self.tmp()
                    body.append(f"  {present} = icmp ne i64 {bit}, 0")
                    body.append(f"  br i1 {present}, label %{emit_lbl}, label %{skip_lbl}")
                else:
                    body.append(f"  br label %{emit_lbl}")
                body.append(f"{emit_lbl}:")
                fst = self.tmp()
                body.append(f"  {fst} = load i8, ptr {first_slot}")
                is_first = self.tmp()
                body.append(f"  {is_first} = icmp ne i8 {fst}, 0")
                body.append(f"  br i1 {is_first}, label %{key_lbl}, label %{sep_lbl}")
                body.append(f"{sep_lbl}:")
                body.append(f"  call void @festina_sb_append(ptr %sb, ptr {self.string_const(',')})")
                body.append(f"  br label %{key_lbl}")
                body.append(f"{key_lbl}:")
                body.append(f"  store i8 0, ptr {first_slot}")
                body.append(f"  call void @festina_sb_append(ptr %sb, "
                            f"ptr {self.string_const(chr(34) + fname + chr(34) + ':')})")
                slot = self.tmp()
                body.append(f"  {slot} = getelementptr i8, ptr %v, i64 {idx * 8}")
                if ftype == BOOL:
                    # A row stores bool in a full i64 slot with the INT
                    # null sentinel -- not the i8-with-2 a struct uses.
                    v = self.tmp()
                    body.append(f"  {v} = load i64, ptr {slot}")
                    body.append(f"  call void @festina_sb_append_json_bool64(ptr %sb, i64 {v})")
                else:
                    self._json_append_slot(body, "%sb", ftype, slot, "%depth")
                body.append(f"  br label %{skip_lbl}")
                body.append(f"{skip_lbl}:")
            body.append(f"  call void @festina_sb_append(ptr %sb, ptr {self.string_const('}')})")

        elif isinstance(type_, types_mod.ArrayType):
            elem_type = type_.element
            elem_llvm = _llvm_type(elem_type)
            elem_size = _elem_size(elem_type)
            body.append(f"  call void @festina_sb_append(ptr %sb, ptr {self.string_const('[')})")
            len_p = self.tmp()
            body.append(f"  {len_p} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr %v, i32 0, i32 0")
            n = self.tmp()
            body.append(f"  {n} = load i64, ptr {len_p}")
            data_p = self.tmp()
            body.append(f"  {data_p} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr %v, i32 0, i32 1")
            data = self.tmp()
            body.append(f"  {data} = load ptr, ptr {data_p}")
            i_slot = self.tmp()
            body.append(f"  {i_slot} = alloca i64")
            body.append(f"  store i64 0, ptr {i_slot}")
            cond = self.label("json.acond")
            loop = self.label("json.abody")
            sep = self.label("json.asep")
            elem_lbl = self.label("json.aelem")
            done = self.label("json.adone")
            body.append(f"  br label %{cond}")
            body.append(f"{cond}:")
            iv = self.tmp()
            body.append(f"  {iv} = load i64, ptr {i_slot}")
            more = self.tmp()
            body.append(f"  {more} = icmp slt i64 {iv}, {n}")
            body.append(f"  br i1 {more}, label %{loop}, label %{done}")
            body.append(f"{loop}:")
            nonfirst = self.tmp()
            body.append(f"  {nonfirst} = icmp sgt i64 {iv}, 0")
            body.append(f"  br i1 {nonfirst}, label %{sep}, label %{elem_lbl}")
            body.append(f"{sep}:")
            body.append(f"  call void @festina_sb_append(ptr %sb, ptr {self.string_const(',')})")
            body.append(f"  br label %{elem_lbl}")
            body.append(f"{elem_lbl}:")
            off = self.tmp()
            body.append(f"  {off} = mul i64 {iv}, {elem_size}")
            ep = self.tmp()
            body.append(f"  {ep} = getelementptr i8, ptr {data}, i64 {off}")
            self._json_append_slot(body, "%sb", elem_type, ep, "%depth")
            nx = self.tmp()
            body.append(f"  {nx} = add i64 {iv}, 1")
            body.append(f"  store i64 {nx}, ptr {i_slot}")
            body.append(f"  br label %{cond}")
            body.append(f"{done}:")
            body.append(f"  call void @festina_sb_append(ptr %sb, ptr {self.string_const(']')})")

        elif isinstance(type_, types_mod.MapType):
            value_type = type_.value
            # claude.md #175: a hash table's buckets aren't a dense
            # [0,count) run any more -- most slots in a capacity-sized
            # array are empty or tombstoned. Loop bound is `capacity`,
            # not `count`; each slot's key is checked (NULL = empty,
            # the reserved 1-valued pointer = tombstone -- see
            # FESTINA_MAP_TOMBSTONE in festina_runtime.c) and skipped
            # before it's treated as live. The `iv > 0` "not the first
            # entry" comma test no longer works either, since the first
            # LIVE bucket isn't reliably index 0 -- replaced with an
            # "emitted anything yet" flag set right after each real
            # append. The FestinaMapEntry stride itself (16 bytes: an
            # 8-byte key pointer, an 8-byte value) is unchanged by
            # #175, so the same offset math still applies once a slot
            # is known live.
            cnt_p = self.tmp()
            body.append(f"  {cnt_p} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr %v, i32 0, i32 2")
            cap = self.tmp()
            body.append(f"  {cap} = load i64, ptr {cnt_p}")
            ent_p = self.tmp()
            body.append(f"  {ent_p} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr %v, i32 0, i32 1")
            ents = self.tmp()
            body.append(f"  {ents} = load ptr, ptr {ent_p}")
            body.append(f"  call void @festina_sb_append(ptr %sb, ptr {self.string_const('{')})")
            i_slot = self.tmp()
            body.append(f"  {i_slot} = alloca i64")
            body.append(f"  store i64 0, ptr {i_slot}")
            emitted_slot = self.tmp()
            body.append(f"  {emitted_slot} = alloca i8")
            body.append(f"  store i8 0, ptr {emitted_slot}")
            cond = self.label("json.mcond")
            loop = self.label("json.mbody")
            live_lbl = self.label("json.mlive")
            sep = self.label("json.msep")
            kv_lbl = self.label("json.mkv")
            next_lbl = self.label("json.mnext")
            done = self.label("json.mdone")
            body.append(f"  br label %{cond}")
            body.append(f"{cond}:")
            iv = self.tmp()
            body.append(f"  {iv} = load i64, ptr {i_slot}")
            more = self.tmp()
            body.append(f"  {more} = icmp slt i64 {iv}, {cap}")
            body.append(f"  br i1 {more}, label %{loop}, label %{done}")
            body.append(f"{loop}:")
            # A FestinaMapEntry is {char *key, int64_t value}: 16 bytes.
            off = self.tmp()
            body.append(f"  {off} = mul i64 {iv}, 16")
            entp = self.tmp()
            body.append(f"  {entp} = getelementptr i8, ptr {ents}, i64 {off}")
            keyv = self.tmp()
            body.append(f"  {keyv} = load ptr, ptr {entp}")
            is_null = self.tmp()
            body.append(f"  {is_null} = icmp eq ptr {keyv}, null")
            is_tomb = self.tmp()
            body.append(f"  {is_tomb} = icmp eq ptr {keyv}, inttoptr (i64 1 to ptr)")
            skip = self.tmp()
            body.append(f"  {skip} = or i1 {is_null}, {is_tomb}")
            body.append(f"  br i1 {skip}, label %{next_lbl}, label %{live_lbl}")
            body.append(f"{live_lbl}:")
            emitted = self.tmp()
            body.append(f"  {emitted} = load i8, ptr {emitted_slot}")
            nonfirst = self.tmp()
            body.append(f"  {nonfirst} = icmp ne i8 {emitted}, 0")
            body.append(f"  br i1 {nonfirst}, label %{sep}, label %{kv_lbl}")
            body.append(f"{sep}:")
            body.append(f"  call void @festina_sb_append(ptr %sb, ptr {self.string_const(',')})")
            body.append(f"  br label %{kv_lbl}")
            body.append(f"{kv_lbl}:")
            body.append(f"  call void @festina_sb_append_json_text(ptr %sb, ptr {keyv})")
            body.append(f"  call void @festina_sb_append(ptr %sb, ptr {self.string_const(':')})")
            vslot = self.tmp()
            body.append(f"  {vslot} = getelementptr i8, ptr {entp}, i64 8")
            self._json_append_slot(body, "%sb", value_type, vslot, "%depth")
            body.append(f"  store i8 1, ptr {emitted_slot}")
            body.append(f"  br label %{next_lbl}")
            body.append(f"{next_lbl}:")
            nx = self.tmp()
            body.append(f"  {nx} = add i64 {iv}, 1")
            body.append(f"  store i64 {nx}, ptr {i_slot}")
            body.append(f"  br label %{cond}")
            body.append(f"{done}:")
            body.append(f"  call void @festina_sb_append(ptr %sb, ptr {self.string_const('}')})")

        body.append("  ret void")
        body.append("}")
        body.append("")
        self.func_defs.extend(body)
        return fn_name

    def _emit_json_read_scalar(self, ftype, cursor_ref, lines):
        """claude.md #159: emits the one runtime call that reads ONE
        JSON value at the cursor into ftype's own Festina representation
        -- int/float/bool/text are the only types _from_json_struct_fn_for/
        _from_json_arr_fn_for ever call this for (v1's own scope cut,
        already enforced in semantic.py -- see that check's own
        comment). Each of these either returns a valid value or calls
        festina_throw() internally and never returns (festina_runtime.c's
        own comment on this whole group), so nothing here ever needs to
        branch on success/failure itself."""
        v = self.tmp()
        if ftype == INT:
            lines.append(f"  {v} = call i64 @festina_json_read_int(ptr {cursor_ref})")
        elif ftype == FLOAT:
            lines.append(f"  {v} = call double @festina_json_read_float(ptr {cursor_ref})")
        elif ftype == BOOL:
            lines.append(f"  {v} = call i8 @festina_json_read_bool(ptr {cursor_ref})")
        elif ftype == TEXT:
            lines.append(f"  {v} = call ptr @festina_json_read_text(ptr {cursor_ref})")
        else:
            raise CodegenError(
                f"internal error: .toStruct()/.toArr() only support int/float/"
                f"bool/text (semantic.py should have already rejected "
                f"{types_mod.type_name(ftype)})", file=self.filename)
        return v

    def _emit_json_read_value(self, ftype, cursor_ref, lines):
        """claude.md #173 (extends claude.md #159): reads ONE JSON
        value at the cursor into ftype's own Festina representation --
        a scalar goes straight through _emit_json_read_scalar unchanged;
        a nested struct/arr[T]/map[T] field/element/target recurses
        into its own from-json function instead (_from_json_struct_fn_for/
        _from_json_arr_fn_for/_from_json_map_fn_for), the exact same way
        JSON *rendering* (_json_fn_for) already recurses for a nested
        container. semantic.py's _is_json_parseable_type has already
        confirmed every leaf ftype eventually bottoms out at
        int/float/bool/text, so the recursion here always terminates."""
        if isinstance(ftype, types_mod.StructType):
            fn_name = self._from_json_struct_fn_for(ftype)
        elif isinstance(ftype, types_mod.ArrayType):
            fn_name = self._from_json_arr_fn_for(ftype.element)
        elif isinstance(ftype, types_mod.MapType):
            fn_name = self._from_json_map_fn_for(ftype)
        else:
            return self._emit_json_read_scalar(ftype, cursor_ref, lines)
        v = self.tmp()
        lines.append(f"  {v} = call ptr {fn_name}(ptr {cursor_ref})")
        return v

    def _from_json_struct_fn_for(self, struct_type):
        """claude.md #159: returns (generating on first use, cached by
        struct name) `ptr @__festina_from_json_struct_N(ptr %cursor)` --
        parses one JSON object at the cursor into a FRESH struct value
        (a new, refcount=1 heap header, exactly the same
        _emit_fresh_heap_header every other struct-producing site
        already uses) and returns it.

        v1 scope cut (semantic.py's own check, not re-validated here):
        every field is int/float/bool/text. A JSON key matching a known
        field (case-insensitively, mirroring claude.md #111's own query-
        column convention) is parsed as that field's type and stored; an
        unrecognized key's own value is skipped (festina_json_skip_value,
        fully general regardless of this v1's own scope -- see its own
        comment) -- lenient, forward-compatible parsing, the same
        "an extra JSON key/a missing struct field is fine" contract
        api.md documents. A duplicate key overwrites (last one wins,
        freeing whatever text the earlier one already stored -- the
        identical "last one wins" convention a map literal's own
        repeated key already follows)."""
        cache_key = f"struct:{struct_type.name}"
        cached = self._from_json_fns.get(cache_key)
        if cached is not None:
            return cached
        fn_name = f"@__festina_from_json_struct_{self._unique()}"
        self._from_json_fns[cache_key] = fn_name

        body = [f"define ptr {fn_name}(ptr %cursor) {{", "entry:"]
        struct_ty = self.struct_llvm_name(struct_type.name)
        out = self._emit_fresh_heap_header(struct_ty, body)
        body.append("  call void @festina_json_object_start(ptr %cursor)")
        first = self.tmp()
        body.append(f"  {first} = alloca i8")
        body.append(f"  store i8 1, ptr {first}")
        loop_lbl = self.label("fromjson.loop")
        end_lbl = self.label("fromjson.end")
        readkey_lbl = self.label("fromjson.readkey")
        keydone_lbl = self.label("fromjson.keydone")
        body.append(f"  br label %{loop_lbl}")
        body.append(f"{loop_lbl}:")
        done = self.tmp()
        body.append(f"  {done} = call i8 @festina_json_object_next(ptr %cursor, ptr {first})")
        done_b = self.tmp()
        body.append(f"  {done_b} = icmp ne i8 {done}, 0")
        body.append(f"  br i1 {done_b}, label %{end_lbl}, label %{readkey_lbl}")
        body.append(f"{readkey_lbl}:")
        key_reg = self.tmp()
        body.append(f"  {key_reg} = call ptr @festina_json_read_key(ptr %cursor)")

        fields = self.struct_fields(struct_type.name)
        for idx, (fname, ftype) in enumerate(fields):
            match_lbl = self.label(f"fromjson.match{idx}")
            next_check_lbl = self.label(f"fromjson.check{idx}")
            matches = self.tmp()
            fname_const = self.string_const(fname)
            body.append(f"  {matches} = call i8 @festina_json_key_matches(ptr {key_reg}, ptr {fname_const})")
            matches_b = self.tmp()
            body.append(f"  {matches_b} = icmp ne i8 {matches}, 0")
            body.append(f"  br i1 {matches_b}, label %{match_lbl}, label %{next_check_lbl}")
            body.append(f"{match_lbl}:")
            slot = self.tmp()
            body.append(f"  {slot} = getelementptr {struct_ty}, ptr {out}, i32 0, i32 {idx}")
            # claude.md #173: a duplicate JSON key overwriting an
            # already-set nested struct/arr[T]/map[T] field must not
            # leak whatever it already parsed into that field -- the
            # same "last one wins, and doesn't leak the value it
            # replaces" contract a map literal's repeated key already
            # follows. Store-then-release (not release-then-store), the
            # same ordering claude.md #120's own cycle-trial-safety
            # comment on _emit_assign requires: a trial deletion
            # triggered by the release must never see this slot still
            # pointing at the value whose count it just dropped.
            old_refcounted = None
            if ftype == TEXT:
                old = self.tmp()
                body.append(f"  {old} = load ptr, ptr {slot}")
                body.append(f"  call void @free(ptr {old})")
            elif _is_refcounted(ftype):
                old_refcounted = self.tmp()
                body.append(f"  {old_refcounted} = load ptr, ptr {slot}")
            v = self._emit_json_read_value(ftype, "%cursor", body)
            ir_ty = _llvm_type(ftype)
            body.append(f"  store {ir_ty} {v}, ptr {slot}")
            if old_refcounted is not None:
                body.append(f"  call void {self._release_fn_for(ftype)}(ptr {old_refcounted})")
            body.append(f"  br label %{keydone_lbl}")
            body.append(f"{next_check_lbl}:")
        # No field matched -- an unrecognized key, skipped whole.
        body.append("  call void @festina_json_skip_field_value(ptr %cursor)")
        body.append(f"  br label %{keydone_lbl}")
        body.append(f"{keydone_lbl}:")
        body.append(f"  call void @free(ptr {key_reg})")
        body.append(f"  br label %{loop_lbl}")
        body.append(f"{end_lbl}:")
        body.append(f"  ret ptr {out}")
        body.append("}")
        body.append("")
        self.func_defs.extend(body)
        return fn_name

    def _from_json_arr_fn_for(self, elem_type):
        """claude.md #159 (widened by #172): the arr[T] counterpart to
        _from_json_struct_fn_for -- returns (generating on first use,
        cached by element type name) `ptr @__festina_from_json_arr_N(ptr
        %cursor)`, parsing one JSON array at the cursor into a fresh
        arr[T] header and returning it. T is int/float/bool/text, or a
        nested struct/arr[T]/map[T] built from those (recursively) --
        semantic.py's _is_json_parseable_type has already confirmed
        this, not re-validated here. Each parsed element is pushed via
        festina_array_push -- the SAME runtime helper `.push()` itself
        uses -- never needing an ownership retain/copy first the way
        `.push(expr)` sometimes does, since festina_json_read_text and
        every _from_json_*_fn_for below it always return an already-
        fresh, uniquely-owned value (or NULL), never an alias of
        something else that would need copying."""
        cache_key = f"arr:{types_mod.type_name(elem_type)}"
        cached = self._from_json_fns.get(cache_key)
        if cached is not None:
            return cached
        fn_name = f"@__festina_from_json_arr_{self._unique()}"
        self._from_json_fns[cache_key] = fn_name

        body = [f"define ptr {fn_name}(ptr %cursor) {{", "entry:"]
        out = self._emit_fresh_heap_header(FESTINA_ARRAY_LLVM_TYPE, body)
        body.append("  call void @festina_json_array_start(ptr %cursor)")
        first = self.tmp()
        body.append(f"  {first} = alloca i8")
        body.append(f"  store i8 1, ptr {first}")
        loop_lbl = self.label("fromjson.aloop")
        end_lbl = self.label("fromjson.aend")
        elem_lbl = self.label("fromjson.aelem")
        body.append(f"  br label %{loop_lbl}")
        body.append(f"{loop_lbl}:")
        done = self.tmp()
        body.append(f"  {done} = call i8 @festina_json_array_next(ptr %cursor, ptr {first})")
        done_b = self.tmp()
        body.append(f"  {done_b} = icmp ne i8 {done}, 0")
        body.append(f"  br i1 {done_b}, label %{end_lbl}, label %{elem_lbl}")
        body.append(f"{elem_lbl}:")
        v = self._emit_json_read_value(elem_type, "%cursor", body)
        elem_ir = _llvm_type(elem_type)
        elem_size = _elem_size(elem_type)
        slot = self.tmp()
        body.append(f"  {slot} = alloca {elem_ir}")
        body.append(f"  store {elem_ir} {v}, ptr {slot}")
        # claude.md #174: `null` capacity -- a JSON-parsed array is
        # always plain (there is no `.toArr(amor T)` syntax to request
        # an amortized one), so this always takes festina_array_resize's
        # unchanged, pre-#174 exact-size-realloc path.
        body.append(f"  call void @festina_array_push(ptr {out}, ptr null, i64 {elem_size}, ptr {slot})")
        body.append(f"  br label %{loop_lbl}")
        body.append(f"{end_lbl}:")
        body.append(f"  ret ptr {out}")
        body.append("}")
        body.append("")
        self.func_defs.extend(body)
        return fn_name

    def _from_json_map_value(self, map_ptr, value_type, key_val, value_val, lines):
        """claude.md #173/#175: sets one key/value pair on a freshly-built
        map[value_type] header during .toStruct()/.toArr() JSON parsing
        (_from_json_map_fn_for below) -- deliberately NOT a call to the
        general _emit_map_set: that method's own retain-unless-fresh
        heuristic is built to read an ast.Expr (`value_source_expr`) and
        decide from ITS shape whether the value already showed up with
        its own +1, which nothing generated here has -- `value_val` is
        always fresh and uniquely owned already (festina_json_read_text's
        own buffer, or another freshly-built struct/arr/map from one of
        this class's own _from_json_*_fn_for functions), the identical
        reasoning _from_json_arr_fn_for's own festina_array_push call
        already relies on, so this never retains. A duplicate JSON key --
        the only way this map builder is ever asked to overwrite an
        entry it already set -- still looks up and releases/frees
        whatever the key already mapped to, AFTER festina_map_set has
        stored the new value (claude.md #120's own cycle-trial-safety
        ordering, see _emit_map_set's identical comment), the same
        "last one wins, and doesn't leak the value it replaces"
        contract a map literal's own repeated key follows."""
        count_ptr = self.tmp()
        lines.append(f"  {count_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {map_ptr}, i32 0, i32 0")
        entries_ptr = self.tmp()
        lines.append(f"  {entries_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {map_ptr}, i32 0, i32 1")
        capacity_ptr = self.tmp()
        lines.append(f"  {capacity_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {map_ptr}, i32 0, i32 2")
        tombstones_ptr = self.tmp()
        lines.append(f"  {tombstones_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {map_ptr}, i32 0, i32 3")
        entries_val = self.tmp()
        lines.append(f"  {entries_val} = load ptr, ptr {entries_ptr}")
        capacity_val = self.tmp()
        lines.append(f"  {capacity_val} = load i64, ptr {capacity_ptr}")
        old_raw = self.tmp()
        lines.append(f"  {old_raw} = call i64 @festina_map_get(ptr {entries_val}, i64 {capacity_val}, "
                     f"ptr {key_val}, i64 0)")
        old_ptr = self.tmp()
        lines.append(f"  {old_ptr} = inttoptr i64 {old_raw} to ptr")
        raw_val = self._map_value_to_i64(value_val, value_type, lines)
        lines.append(f"  call void @festina_map_set(ptr {count_ptr}, ptr {entries_ptr}, "
                     f"ptr {capacity_ptr}, ptr {tombstones_ptr}, ptr {key_val}, i64 {raw_val})")
        if _is_refcounted(value_type):
            lines.append(f"  call void {self._release_fn_for(value_type)}(ptr {old_ptr})")
        elif value_type == TEXT:
            lines.append(f"  call void @free(ptr {old_ptr})")

    def _from_json_map_fn_for(self, map_type):
        """claude.md #173: the map[T] counterpart to
        _from_json_struct_fn_for/_from_json_arr_fn_for -- todo.md's own
        #159 entry named this the missing piece: "a map[text] parsing
        counterpart (a JSON object with arbitrary keys, rather than
        known field names) for a map[T] target." Returns (generating on
        first use, cached by value type name) `ptr
        @__festina_from_json_map_N(ptr %cursor)`, parsing one JSON
        object at the cursor into a fresh map[T].

        Unlike _from_json_struct_fn_for's own object-parsing loop (which
        matches each key against a FIXED set of known field names, and
        skips anything else), every key here becomes a map entry --
        there is no fixed field set to match against, exactly the
        "arbitrary keys" a map[T] target calls for."""
        cache_key = f"map:{types_mod.type_name(map_type.value)}"
        cached = self._from_json_fns.get(cache_key)
        if cached is not None:
            return cached
        fn_name = f"@__festina_from_json_map_{self._unique()}"
        self._from_json_fns[cache_key] = fn_name

        value_type = map_type.value
        body = [f"define ptr {fn_name}(ptr %cursor) {{", "entry:"]
        out = self._emit_fresh_heap_header(FESTINA_MAP_LLVM_TYPE, body)
        body.append("  call void @festina_json_object_start(ptr %cursor)")
        first = self.tmp()
        body.append(f"  {first} = alloca i8")
        body.append(f"  store i8 1, ptr {first}")
        loop_lbl = self.label("fromjson.mloop")
        end_lbl = self.label("fromjson.mend")
        entry_lbl = self.label("fromjson.mentry")
        body.append(f"  br label %{loop_lbl}")
        body.append(f"{loop_lbl}:")
        done = self.tmp()
        body.append(f"  {done} = call i8 @festina_json_object_next(ptr %cursor, ptr {first})")
        done_b = self.tmp()
        body.append(f"  {done_b} = icmp ne i8 {done}, 0")
        body.append(f"  br i1 {done_b}, label %{end_lbl}, label %{entry_lbl}")
        body.append(f"{entry_lbl}:")
        key_reg = self.tmp()
        body.append(f"  {key_reg} = call ptr @festina_json_read_key(ptr %cursor)")
        v = self._emit_json_read_value(value_type, "%cursor", body)
        self._from_json_map_value(out, value_type, key_reg, v, body)
        # claude.md #97: festina_map_set strdup's its own copy of the
        # key -- the same reason _emit_map_set frees its own
        # key_source_expr afterward -- so key_reg (heap-allocated by
        # festina_json_read_key) has no owner left once this returns.
        body.append(f"  call void @free(ptr {key_reg})")
        body.append(f"  br label %{loop_lbl}")
        body.append(f"{end_lbl}:")
        body.append(f"  ret ptr {out}")
        body.append("}")
        body.append("")
        self.func_defs.extend(body)
        return fn_name

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
            # claude.md #174: ast.ArrayLit itself has no amor-vs-plain
            # distinction (the same `[...]` syntax either way) -- only
            # the expected type (already known here) says which header
            # shape/growth function to build, the identical reasoning
            # the ast.MapLit branch just below already has for `amor
            # map[T]`.
            is_amor = isinstance(expected_type, types_mod.ArrayType) and expected_type.amortized
            return self._emit_array_lit(node, env, lines, expected_type, is_amor=is_amor)
        if isinstance(node, ast.MapLit) and isinstance(expected_type, types_mod.HttpType):
            # claude.md #162: `http x = {...}` -- checked before the
            # generic ast.MapLit branch just below, for the identical
            # reason claude.md #156's own amor-map bypass in
            # analyze_var_decl (semantic.py) exists: a MapLit's generic
            # handling demands one homogeneous value type across every
            # entry, which an http literal's genuinely heterogeneous
            # field set (text/int/map/body) can never satisfy.
            return self._emit_http_lit(node, env, lines)
        if isinstance(expected_type, types_mod.HttpType):
            lit = _http_send_lit_receiver(node)
            if lit is not None:
                # claude.md #164: `http req = {...}.send()` -- builds
                # the literal exactly like the plain `http x = {...}`
                # case just above, THEN also dispatches the send, and
                # returns the SAME pointer for `req`'s own binding to
                # take ownership of -- no release here (unlike a bare
                # `{...}.send()` expression-statement, which DOES
                # release it -- see _release_http_send_receiver): this
                # value doesn't die at the end of the statement, `req`
                # keeps living. Safe specifically because the receiver
                # is ALWAYS a fresh MapLit here (semantic.py's own
                # _http_send_lit_receiver only ever matches that
                # shape) -- nothing else could possibly hold a
                # reference to it yet, so handing it straight to `req`
                # with no extra retain is exactly the same "moves,
                # doesn't copy" reasoning every other fresh-value
                # VarDecl binding already relies on.
                out, out_type = self._emit_http_lit(lit, env, lines)
                self.uses_http = True
                self.uses_https = True
                lines.append(f"  call void @festina_http_send_client_dispatch(ptr {out})")
                return out, out_type
        if isinstance(node, ast.MapLit):
            return self._emit_map_lit(node, env, lines, expected_type)
        # claude.md #91: `color red = 'red'` / `font body = '13px arial'`.
        # Resolving here rather than in the VarDecl branch means every
        # position that knows its expected type gets it for free --
        # declarations, reassignments, call arguments, struct fields,
        # array elements and returns alike.
        if isinstance(expected_type, (types_mod.ColorType, types_mod.FontType)):
            what = types_mod.type_name(expected_type)
            line = getattr(node, "line", 0)
            if isinstance(node, ast.NullLit) and isinstance(expected_type, types_mod.ColorType):
                # claude.md #189: checked ahead of the "must come from a
                # literal" rule just below -- `null` isn't a text
                # literal to resolve at all, it's color's own existing
                # null-vs-zero-less sentinel (`-1`/'none', the same
                # value _zero_value already gives an uninitialized
                # color). A bare `null` against a FontType-expected
                # position falls through unchanged -- FontType's own
                # LLVM shape is a plain pointer, so the generic "null"
                # keyword the code below already produces is already
                # correct for it.
                return "-1", expected_type
            if isinstance(node, ast.StringLit):
                if isinstance(expected_type, types_mod.ColorType):
                    return (self._emit_color_value(node, node.value, line, what),
                            expected_type)
                return (self._emit_font_constant(node.value, line, what),
                        expected_type)
            val, vtype = self._emit_expr(node, env, lines)
            if vtype == TEXT:
                # Text that isn't a literal can't be resolved at compile
                # time, and there is deliberately no runtime resolver to
                # fall back on (claude.md #90) -- so this is an error
                # pointing at the two things that DO work.
                alt = ("fillStyle(red, green, blue) with each component 0-255"
                       if isinstance(expected_type, types_mod.ColorType)
                       else "changeFont(px, style, family)")
                raise CodegenError(
                    f"a {what} must come from a literal, so the compiler can "
                    f"resolve it once -- write `{what} name = '...'` and use "
                    f"`name`, or, to choose one at runtime, use {alt}",
                    file=self.filename, line=line)
            return val, vtype
        if isinstance(node, ast.NullLit):
            if expected_type == INT:
                return INT_NULL_CONST, INT
            if expected_type == FLOAT:
                return FLOAT_NULL_CONST, FLOAT
            if expected_type == BOOL:
                return BOOL_NULL_CONST, BOOL
            # ColorType is handled above, ahead of the "must come from a
            # literal" check the ColorType/FontType branch otherwise
            # applies -- a bare `null` is never a text literal to
            # resolve, so it can't reach here as ColorType at all.
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

    def _size_arg(self, i64_val, lines):
        """An i64 size/count value, narrowed to whatever width
        self.pointer_bits actually needs to call calloc/malloc with --
        see __init__'s own note. A plain `trunc` on a value that's
        already the right width would be invalid IR, so native (64)
        just passes the value through unchanged; only wasm32 (32)
        inserts the trunc. Every caller already has its size computed
        in i64 (Festina's own internal size arithmetic never needs to
        change -- see _sizeof above), so this is the one narrowing
        point, not a change scattered across every call site's own
        computation."""
        if self.pointer_bits == 64:
            return i64_val
        out = self.tmp()
        lines.append(f"  {out} = trunc i64 {i64_val} to i{self.pointer_bits}")
        return out

    def _emit_calloc(self, count_i64, size_i64, lines):
        """calloc(count, size), both operands narrowed to the real
        size_t width first -- see _size_arg. Every call site already
        passes count=1 (the total byte count folded into `size`
        instead), so calloc's own overflow-checking multiply stays
        meaningful rather than being defeated by a genuine element
        count."""
        ir_ty = f"i{self.pointer_bits}"
        count = self._size_arg(count_i64, lines)
        size = self._size_arg(size_i64, lines)
        out = self.tmp()
        lines.append(f"  {out} = call ptr @calloc({ir_ty} {count}, {ir_ty} {size})")
        return out

    def _emit_malloc(self, size_i64, lines):
        """malloc(size), narrowed the same way _emit_calloc's own
        operands are."""
        ir_ty = f"i{self.pointer_bits}"
        size = self._size_arg(size_i64, lines)
        out = self.tmp()
        lines.append(f"  {out} = call ptr @malloc({ir_ty} {size})")
        return out

    def _emit_fresh_heap_header(self, payload_llvm_ty, lines, type_tag=None):
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
        fields it actually has a value for.

        claude.md #176: `type_tag`, when given, is a `ptr` operand (an
        interned type-name string constant -- see string_const) for a
        struct that's a member of at least one pure-struct enum (see
        self._tagged_structs). The header widens from `{refcount}` to
        `{type_tag, refcount}` -- IN THAT ORDER, tag first (base+0),
        refcount second (base+8) -- so the refcount word stays at
        EXACTLY `payload - 8` regardless of whether a given struct is
        tagged, and festina_retain/festina_release_check (which only
        ever look at `payload - 8`) need zero changes either way. The
        tag word sits one further word back, at `payload - 16` -- read
        by _emit_typeof and the field-access tag check, never by the
        generic runtime functions."""
        size_val = self._sizeof(payload_llvm_ty, lines)
        if type_tag is not None:
            total_size = self.tmp()
            lines.append(f"  {total_size} = add i64 {size_val}, 16")
            raw = self._emit_calloc("1", total_size, lines)
            lines.append(f"  store ptr {type_tag}, ptr {raw}")
            refcount_ptr = self.tmp()
            lines.append(f"  {refcount_ptr} = getelementptr i8, ptr {raw}, i64 8")
            lines.append(f"  store i64 1, ptr {refcount_ptr}")
            payload = self.tmp()
            lines.append(f"  {payload} = getelementptr i8, ptr {raw}, i64 16")
            return payload
        total_size = self.tmp()
        lines.append(f"  {total_size} = add i64 {size_val}, 8")
        raw = self._emit_calloc("1", total_size, lines)
        lines.append(f"  store i64 1, ptr {raw}")
        payload = self.tmp()
        lines.append(f"  {payload} = getelementptr i8, ptr {raw}, i64 8")
        return payload

    def _enum_tag_const(self, type_):
        """claude.md #176: the interned `ptr` constant that IS a given
        type's own runtime tag -- string_const's own global-constant
        interning (the exact mechanism every text literal already uses),
        called once per concrete type name. Reading this tag back at
        runtime (see _emit_typeof) is already reading a valid `text`
        value -- there is no separate type-id -> name lookup table
        anywhere; the tag itself always simply IS the answer."""
        return self.string_const(types_mod.type_name(type_))

    def _array_capacity_arg(self, obj_val, obj_type, lines):
        """claude.md #174: an `amor arr[T]` passes the ADDRESS of its
        own capacity field (the third FESTINA_AMOR_ARRAY_LLVM_TYPE
        field) so
        festina_array_resize can grow it geometrically in place; a
        plain arr[T] passes a literal `null`, which
        festina_array_resize treats exactly as it always has --
        "resize to exactly the requested length," this feature's own
        unchanged pre-#174 behavior. Shared by every one of
        push/pop/shift/unshift/splice/splice_insert's own call sites
        in _emit_array_method below, so the same GEP shape is used
        everywhere rather than risking one touchpoint drifting from
        the others."""
        if not obj_type.amortized:
            return "null"
        cap_ptr = self.tmp()
        lines.append(f"  {cap_ptr} = getelementptr {FESTINA_AMOR_ARRAY_LLVM_TYPE}, "
                     f"ptr {obj_val}, i32 0, i32 2")
        return cap_ptr

    def _null_value(self, type_):
        """claude.md #96: the NULL of a type, as opposed to its zero.

        The two differ for exactly the types with no spare bit pattern
        to spend on null -- int/float/bool carry a reserved sentinel
        (see the module docstring's "Null for int/float" note), so an
        int's zero is 0 and its null is i64's minimum. Everything
        pointer-backed has a real null to use."""
        if type_ == INT:
            return INT_NULL_CONST
        if type_ == FLOAT:
            return FLOAT_NULL_CONST
        if type_ == BOOL:
            return BOOL_NULL_CONST
        return self._zero_value(type_)

    def _emit_array_method(self, name, obj_val, obj_type, expr, env, lines):
        """claude.md #96: push/pop/shift/unshift/splice.

        The runtime moves elements by BYTES with the element size passed
        in, so one set of helpers covers every arr[T]. What has to
        happen HERE is the ownership half, and it is the same rule
        `arr[i] = value` already follows (claude.md #80/#83): a struct/
        arr/map element being stored is retained, a text one is copied
        unless its source is already owning. Without that, `xs.push(s)`
        would leave the array and `s` sharing one buffer, and whichever
        was freed first would leave the other dangling.

        Nothing is released on removal: pop/shift hand the element back
        and splice hands it to the returned array, so ownership
        transfers rather than ending."""
        elem_type = obj_type.element
        elem_ir = _llvm_type(elem_type)
        elem_size = _elem_size(elem_type)

        if name in ("push", "unshift"):
            val, vtype = self._emit_value_for(expr.args[0], env, lines, elem_type)
            val = self._coerce(val, vtype, elem_type, lines, source_expr=expr.args[0])
            if _is_refcounted(elem_type):
                if not self._is_owning_refcounted_source(expr.args[0]):
                    lines.append(f"  call void @festina_retain(ptr {val})")
            elif elem_type == TEXT and not self._is_owning_text_source(expr.args[0]):
                owned = self.tmp()
                lines.append(f"  {owned} = call ptr @festina_text_own(ptr {val})")
                val = owned
            slot = self.tmp()
            lines.append(f"  {slot} = alloca {elem_ir}")
            lines.append(f"  store {elem_ir} {val}, ptr {slot}")
            fn = "festina_array_push" if name == "push" else "festina_array_unshift"
            cap_arg = self._array_capacity_arg(obj_val, obj_type, lines)
            lines.append(f"  call void @{fn}(ptr {obj_val}, ptr {cap_arg}, i64 {elem_size}, ptr {slot})")
            # JS hands back the new length; reading it costs one load.
            len_ptr = self.tmp()
            lines.append(f"  {len_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 0")
            out = self.tmp()
            lines.append(f"  {out} = load i64, ptr {len_ptr}")
            return out, INT

        if name in ("pop", "shift"):
            slot = self.tmp()
            lines.append(f"  {slot} = alloca {elem_ir}")
            # Pre-seeded with this element type's own NULL -- not its
            # zero value, which for an int is a perfectly ordinary 0 and
            # would make an empty pop() indistinguishable from popping a
            # real zero. The runtime leaves the slot alone when there is
            # nothing to remove, so this is what an empty array answers.
            lines.append(f"  store {elem_ir} {self._null_value(elem_type)}, ptr {slot}")
            fn = "festina_array_pop" if name == "pop" else "festina_array_shift"
            cap_arg = self._array_capacity_arg(obj_val, obj_type, lines)
            lines.append(
                f"  call i8 @{fn}(ptr {obj_val}, ptr {cap_arg}, i64 {elem_size}, ptr {slot})")
            out = self.tmp()
            lines.append(f"  {out} = load {elem_ir}, ptr {slot}")
            return out, elem_type

        if name == "indexOf":
            # claude.md #97. The needle is handed over BY ADDRESS so one
            # runtime helper serves every arr[T]: it compares the raw
            # 8-byte slot, which is right for int/float/bool and is
            # identity for struct/arr/map (aliases share a pointer). Text
            # is the exception the `is_text` flag exists for -- two equal
            # strings are usually two different buffers, so the runtime
            # switches to strcmp.
            #
            # No ownership work happens here in either direction: the
            # needle is only read, and an index is not a reference. That
            # also means the temporary is freed the ordinary way, exactly
            # as any other text argument at a call site would be.
            val, vtype = self._emit_value_for(expr.args[0], env, lines, elem_type)
            val = self._coerce(val, vtype, elem_type, lines, source_expr=expr.args[0])
            slot = self.tmp()
            lines.append(f"  {slot} = alloca {elem_ir}")
            lines.append(f"  store {elem_ir} {val}, ptr {slot}")
            is_text = 1 if elem_type == TEXT else 0
            out = self.tmp()
            lines.append(
                f"  {out} = call i64 @festina_array_index_of(ptr {obj_val}, "
                f"i64 {elem_size}, ptr {slot}, i8 {is_text})")
            self._free_text_temp(expr.args[0], val, elem_type, lines)
            return out, INT

        if name == "sort":
            # claude.md #184 (uraikus/festina#76 item 2): in-place,
            # stable sort. The comparator is a plain first-class
            # function value -- already a bare `ptr` (claude.md #141),
            # not a heap-allocated Festina value, so unlike an
            # arr[T]/map[T]/struct/text argument nothing here needs
            # retaining, copying, or releasing; it's read once and
            # handed straight to the runtime as opaque userdata for
            # festina_array_sort's own trampoline
            # (_emit_sort_comparator_trampoline) to call back through.
            cmp_val, _ = self._emit_expr(expr.args[0], env, lines)
            trampoline_name = self._emit_sort_comparator_trampoline(elem_type)
            lines.append(
                f"  call void @festina_array_sort(ptr {obj_val}, i64 {elem_size}, "
                f"ptr {trampoline_name}, ptr {cmp_val})")
            return "0", None

        # splice(start, count) -> arr[T] of the removed elements
        start_val, _ = self._emit_expr(expr.args[0], env, lines)
        count_val, _ = self._emit_expr(expr.args[1], env, lines)
        dst = self._emit_fresh_heap_header(FESTINA_ARRAY_LLVM_TYPE, lines)
        if len(expr.args) == 3:
            # claude.md #130: splice(start, count, insertArr) --
            # JavaScript's splice(start, deleteCount, ...items), the
            # variadic items spelled as one arr[T] argument since this
            # language has no variadic parameters.
            insert_type = types_mod.ArrayType(elem_type)
            insert_val, insert_vtype = self._emit_value_for(expr.args[2], env, lines, insert_type)
            insert_val = self._coerce(insert_val, insert_vtype, insert_type, lines, source_expr=expr.args[2])
            insert_len_ptr = self.tmp()
            lines.append(f"  {insert_len_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {insert_val}, i32 0, i32 0")
            insert_len = self.tmp()
            lines.append(f"  {insert_len} = load i64, ptr {insert_len_ptr}")
            insert_data_ptr = self.tmp()
            lines.append(f"  {insert_data_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {insert_val}, i32 0, i32 1")
            insert_data = self.tmp()
            lines.append(f"  {insert_data} = load ptr, ptr {insert_data_ptr}")
            cap_arg = self._array_capacity_arg(obj_val, obj_type, lines)
            lines.append(
                f"  call void @festina_array_splice_insert(ptr {obj_val}, ptr {cap_arg}, i64 {elem_size}, "
                f"i64 {start_val}, i64 {count_val}, ptr {insert_data}, i64 {insert_len}, ptr {dst})")
            # The call above may have realloc'd this array's own data
            # buffer, so its pointer has to be reloaded AFTER the call,
            # not reused from before it.
            data_field_ptr = self.tmp()
            lines.append(f"  {data_field_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 1")
            data_ptr_now = self.tmp()
            lines.append(f"  {data_ptr_now} = load ptr, ptr {data_field_ptr}")
            self._emit_retain_or_own_range(data_ptr_now, elem_ir, elem_type, start_val, insert_len, lines)
            self._release_owned_receiver(expr.args[2], insert_val, insert_type, lines)
        else:
            cap_arg = self._array_capacity_arg(obj_val, obj_type, lines)
            lines.append(
                f"  call void @festina_array_splice(ptr {obj_val}, ptr {cap_arg}, i64 {elem_size}, "
                f"i64 {start_val}, i64 {count_val}, ptr {dst})")
        return dst, types_mod.ArrayType(elem_type)

    def _emit_retain_or_own_range(self, data_ptr, elem_llvm_ty, elem_type, start_val, count_val, lines):
        """claude.md #130: splice(start, count, insertArr) copies raw
        element BYTES from a SEPARATE array's buffer into this array's
        buffer (festina_array_splice_insert, a plain memcpy with no
        notion of a Festina type) -- unlike push/unshift, whose single
        value's own source expression is examined directly
        (_is_owning_refcounted_source), there is no single source
        expression here to ask: the source is a whole array, read only
        for its raw bytes, and it keeps managing its own elements'
        lifetime independently of whatever this array now does with the
        copies. So the newly-written range always needs its own
        reference, unconditionally, with no freshness check possible or
        needed: a struct/arr/map/img/aud/regex/blob element is retained
        in place (same pointer, refcount +1), a text element is copied
        via festina_text_own (its own pointer replaced with the fresh
        copy, since text has no shared representation to retain), and
        anything else (int/float/bool/color, ...) needs nothing -- the
        raw bytes the runtime already copied are a complete, independent
        value on their own."""
        if not (_is_refcounted(elem_type) or elem_type == TEXT):
            return
        idx_slot = self.tmp()
        lines.append(f"  {idx_slot} = alloca i64")
        lines.append(f"  store i64 0, ptr {idx_slot}")
        loop_cond = self.label("spliceretain.loopcond")
        loop_body = self.label("spliceretain.loopbody")
        loop_end = self.label("spliceretain.loopend")
        lines.append(f"  br label %{loop_cond}")
        lines.append(f"{loop_cond}:")
        idx_val = self.tmp()
        lines.append(f"  {idx_val} = load i64, ptr {idx_slot}")
        keep_going = self.tmp()
        lines.append(f"  {keep_going} = icmp slt i64 {idx_val}, {count_val}")
        lines.append(f"  br i1 {keep_going}, label %{loop_body}, label %{loop_end}")
        lines.append(f"{loop_body}:")
        abs_idx = self.tmp()
        lines.append(f"  {abs_idx} = add i64 {start_val}, {idx_val}")
        elem_ptr = self.tmp()
        lines.append(f"  {elem_ptr} = getelementptr {elem_llvm_ty}, ptr {data_ptr}, i64 {abs_idx}")
        elem_val = self.tmp()
        lines.append(f"  {elem_val} = load {elem_llvm_ty}, ptr {elem_ptr}")
        if elem_type == TEXT:
            owned = self.tmp()
            lines.append(f"  {owned} = call ptr @festina_text_own(ptr {elem_val})")
            lines.append(f"  store ptr {owned}, ptr {elem_ptr}")
        else:
            lines.append(f"  call void @festina_retain(ptr {elem_val})")
        next_idx = self.tmp()
        lines.append(f"  {next_idx} = add i64 {idx_val}, 1")
        lines.append(f"  store i64 {next_idx}, ptr {idx_slot}")
        lines.append(f"  br label %{loop_cond}")
        lines.append(f"{loop_end}:")

    def _emit_array_lit(self, expr, env, lines, expected_type=None, header=None, is_amor=False):
        # `header`, when given, is an already-allocated (and, for a
        # `ptr` field, zero-initialized) FESTINA_ARRAY_LLVM_TYPE slot to
        # build directly into instead of allocating a fresh heap header
        # -- see this method's own header-allocation comment below, and
        # _emit_stmt's VarDecl handling, the only caller that ever
        # passes one (a non-escaping local declared directly from an
        # array literal -- claude.md #81). Never a caller-supplied
        # `header` when `is_amor` is true -- claude.md #174: see
        # _is_stack_allocatable_array_or_map_decl's own comment.
        #
        # claude.md #26: "Arrays may contain supported primitive types,
        # structs, tables, and other array types" -- table elements are
        # rejected by _llvm_type(TableType) below, since there's no way
        # to construct a Table-typed value without sqlite() queries yet.
        #
        # claude.md #174: `is_amor` builds an `amor arr[T]` header
        # (FESTINA_AMOR_ARRAY_LLVM_TYPE, tracking a capacity field
        # FESTINA_ARRAY_LLVM_TYPE doesn't) instead of a plain arr[T]
        # one -- the caller (_emit_value_for, which already knows the
        # declaration's own expected type, ArrayType.amortized
        # included) decides which; ast.ArrayLit itself has no
        # amor-vs-plain distinction, the same `[...]` syntax either
        # way -- map[T] no longer has an analogous parameter on
        # _emit_map_lit since claude.md #175 removed `amor map[T]`.
        llvm_type_name = FESTINA_AMOR_ARRAY_LLVM_TYPE if is_amor else FESTINA_ARRAY_LLVM_TYPE
        expected_elem = expected_type.element if isinstance(expected_type, types_mod.ArrayType) else None

        values = []
        sources = []
        pre_coerce_types = []   # claude.md #118: pre-coercion types, for
                                # the freshness test below (a text path
                                # coerced into a blob/img/aud element is
                                # already a fresh +1)
        elem_type = expected_elem
        for e in expr.elements:
            if isinstance(e, ast.ArrayLit) and isinstance(expected_elem, types_mod.ArrayType):
                elem_is_amor = expected_elem.amortized
                val, vtype = self._emit_array_lit(e, env, lines, expected_elem, is_amor=elem_is_amor)
            else:
                val, vtype = self._emit_value_for(e, env, lines, expected_elem)
            pre_coerce_types.append(vtype)
            if expected_elem is not None:
                val = self._coerce(val, vtype, expected_elem, lines, source_expr=e)
                vtype = expected_elem
            values.append(val)
            sources.append(e)
            elem_type = elem_type or vtype

        if elem_type is None:
            raise CodegenError(
                "cannot infer the element type of an empty array literal without a declared type",
                file=self.filename, line=getattr(expr, "line", 0),
            )
        elem_llvm_ty = _llvm_type(elem_type)
        n = len(values)

        # claude.md #79: a fresh, uniquely-owned (refcount=1) heap
        # header when `header` wasn't already supplied by the caller --
        # the same "owning" source _is_owning_refcounted_source already
        # treats an array/map literal as, so binding it into a new slot
        # needs no separate retain either way. Whether THIS literal's
        # own header is heap or stack is a property of the LOCAL BINDING
        # it happens to initialize (decided in _emit_stmt, not here) --
        # heap by default (matching an escaping struct local's own
        # storage), stack only for the one case claude.md #81 adds: a
        # non-escaping local declared directly from a literal, which
        # passes its own pre-allocated stack slot in as `header`.
        if header is None:
            header = self._emit_fresh_heap_header(llvm_type_name, lines)
        len_ptr = self.tmp()
        lines.append(f"  {len_ptr} = getelementptr {llvm_type_name}, ptr {header}, i32 0, i32 0")
        lines.append(f"  store i64 {n}, ptr {len_ptr}")

        if n == 0:
            data_ptr = self._emit_malloc("0", lines)
        else:
            elem_size = self._sizeof(elem_llvm_ty, lines)
            total_size = self.tmp()
            lines.append(f"  {total_size} = mul i64 {elem_size}, {n}")
            data_ptr = self._emit_malloc(total_size, lines)
            # claude.md #80: retain a struct/arr/map-typed element
            # whenever its own source isn't "owning" -- the identical
            # rule every other binding site in this stage uses. No
            # release-old logic needed here (unlike arr[i] = v on an
            # already-built array): this data buffer is fresh malloc'd
            # memory, never zero-initialized, so every index is written
            # exactly once, right here, before anything could ever read
            # a stale/garbage "old" value through it. claude.md #83:
            # a text element gets the identical treatment, copying
            # (festina_text_own) instead of retaining, for the same
            # "no release-old needed, fresh malloc'd memory" reason.
            elem_is_refcounted = _is_refcounted(elem_type)
            for i, val in enumerate(values):
                elem_ptr = self.tmp()
                lines.append(f"  {elem_ptr} = getelementptr {elem_llvm_ty}, ptr {data_ptr}, i64 {i}")
                if elem_is_refcounted and not self._refcounted_source_is_fresh(
                        sources[i], pre_coerce_types[i], elem_type):
                    lines.append(f"  call void @festina_retain(ptr {val})")
                elif elem_type == TEXT and not self._is_owning_text_source(sources[i]):
                    owned = self.tmp()
                    lines.append(f"  {owned} = call ptr @festina_text_own(ptr {val})")
                    val = owned
                lines.append(f"  store {elem_llvm_ty} {val}, ptr {elem_ptr}")

        data_field_ptr = self.tmp()
        lines.append(f"  {data_field_ptr} = getelementptr {llvm_type_name}, ptr {header}, i32 0, i32 1")
        lines.append(f"  store ptr {data_ptr}, ptr {data_field_ptr}")

        if is_amor:
            # claude.md #174: this literal's own backing buffer was
            # malloc'd for EXACTLY `n` elements (no slack) -- capacity
            # starts at exactly `n` too, so the very next push()
            # correctly triggers real growth (doubling from `n`, not
            # from a false 0 that would claim room this buffer doesn't
            # actually have).
            cap_ptr = self.tmp()
            lines.append(f"  {cap_ptr} = getelementptr {llvm_type_name}, ptr {header}, i32 0, i32 2")
            lines.append(f"  store i64 {n}, ptr {cap_ptr}")

        return header, types_mod.ArrayType(elem_type, amortized=is_amor)

    def _emit_map_lit(self, expr, env, lines, expected_type=None, header=None):
        """claude.md #72/#175: { key: value, ... } -- built the same way
        _emit_array_lit builds an array literal: a header (fresh heap by
        default, or the caller's own pre-allocated `header` -- see
        _emit_array_lit's own comment on that parameter, claude.md #81)
        that festina_map_set mutates in place as each entry is added
        (see _emit_map_set), one festina_map_set call per entry in
        source order (so a repeated key naturally ends up "last one
        wins", with no separate dedup pass needed).

        `header`, when given, must already be zero-initialized (its own
        count/entries/capacity/tombstones fields all starting at
        0/null/0/0) -- true of both a fresh calloc'd heap header and a
        `store {ty} zeroinitializer` stack one, so this method itself
        never needs to care which."""
        expected_value = (expected_type.value
                          if isinstance(expected_type, types_mod.MapType) else None)

        # claude.md #79: a fresh, uniquely-owned heap header when
        # `header` wasn't already supplied -- see _emit_array_lit's own
        # comment just above for the full reasoning, identical here.
        if header is None:
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
            pre_coerce_type = vtype   # claude.md #118: for the freshness test
            if expected_value is not None:
                val_val = self._coerce(val_val, vtype, expected_value, lines,
                                       source_expr=val_expr)
                vtype = expected_value
            value_type = value_type or vtype
            self._emit_map_set(header, value_type, key_val, val_val, val_expr, lines,
                                key_source_expr=key_expr,
                                value_pre_coerce_type=pre_coerce_type)

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
        """claude.md #72/#175: npcHealths['npc1'] -- entries/capacity
        are read straight out of the already-emitted map's own storage
        (claude.md #79: `obj_val` is now a `ptr` to that storage, not
        the header value itself, so this needs a GEP+load per field,
        the same two-step pattern struct field reads already use --
        not addressable via extractvalue anymore). No addressability
        needed for a READ regardless, unlike a write; see
        _emit_map_set. No `count` GEP -- festina_map_get scans buckets
        by capacity, not a dense [0,count) range, so count was never
        needed by a read."""
        entries_ptr = self.tmp()
        lines.append(f"  {entries_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 1")
        entries = self.tmp()
        lines.append(f"  {entries} = load ptr, ptr {entries_ptr}")
        capacity_ptr = self.tmp()
        lines.append(f"  {capacity_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 2")
        capacity = self.tmp()
        lines.append(f"  {capacity} = load i64, ptr {capacity_ptr}")
        default = self._map_missing_default(value_type)
        raw = self.tmp()
        lines.append(f"  {raw} = call i64 @festina_map_get(ptr {entries}, i64 {capacity}, ptr {key_val}, i64 {default})")
        return self._i64_to_map_value(raw, value_type, lines), value_type

    def _emit_map_set(self, map_ptr, value_type, key_val, value_val, value_source_expr, lines,
                       key_source_expr=None, value_pre_coerce_type=None):
        """claude.md #72/#175: npcHealths['npc1'] = 30 (and the
        equivalent per-entry calls a map literal builds itself out of
        -- see _emit_map_lit). Unlike a read, this needs the map's own
        actual header ADDRESS (`map_ptr`, a `ptr` to its {count,
        entries, capacity, tombstones} storage), not just its value,
        since festina_map_set can rehash the whole table and has to
        write the new count/entries/capacity/tombstones back into that
        same storage for the change to actually stick. claude.md #79:
        since every arr[T]/map[T] value is now itself a `ptr` to that
        storage (not the storage inline), `map_ptr` here is always
        already that pointer -- the LOADED value of a variable's own
        slot/global, a struct field's own loaded value, or (during
        literal construction) the literal's own fresh heap header --
        never a slot or field's own ADDRESS a further load would still
        be needed for; see _try_addressable's own comment for where
        that load actually happens.

        claude.md #80: when `value_type` is itself refcounted, retains
        `value_val` (unless `value_source_expr` is "owning") and
        releases whatever value the key previously mapped to, if any --
        the identical rule an array-element or struct-field write
        already follows. Unlike those, there's no fixed address to
        `load` an "old" value from first (a map key may or may not
        already be present, and festina_map_set itself doesn't say
        which) -- `festina_map_get` with a `0` (null) default finds it
        instead, the same lookup a genuine read already uses, safe to
        call unconditionally: `0` can never be a real heap pointer, so
        it only ever means "nothing to release," whether the key was
        truly absent or (i.e. `m[k] = null`) present with an
        already-null value -- releasing null is always a no-op either
        way, so the two cases need no distinguishing here. This is what
        makes overwriting a key -- including a repeated key within the
        very literal building this map, which is exactly a `map[key] =
        v` re-set applied to a key this same construction already
        set -- correctly release the value it replaces, not just skip
        it."""
        count_ptr = self.tmp()
        lines.append(f"  {count_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {map_ptr}, i32 0, i32 0")
        entries_ptr = self.tmp()
        lines.append(f"  {entries_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {map_ptr}, i32 0, i32 1")
        capacity_ptr = self.tmp()
        lines.append(f"  {capacity_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {map_ptr}, i32 0, i32 2")
        tombstones_ptr = self.tmp()
        lines.append(f"  {tombstones_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {map_ptr}, i32 0, i32 3")
        deferred_release = None
        if _is_refcounted(value_type):
            entries_val = self.tmp()
            lines.append(f"  {entries_val} = load ptr, ptr {entries_ptr}")
            capacity_val = self.tmp()
            lines.append(f"  {capacity_val} = load i64, ptr {capacity_ptr}")
            old_raw = self.tmp()
            lines.append(f"  {old_raw} = call i64 @festina_map_get(ptr {entries_val}, i64 {capacity_val}, ptr {key_val}, i64 0)")
            old_ptr = self.tmp()
            lines.append(f"  {old_ptr} = inttoptr i64 {old_raw} to ptr")
            # claude.md #118: the freshness test (with the caller's
            # pre-coercion type, when it passes one) so a text value
            # coerced into a blob/img/aud entry is not over-retained.
            if not self._refcounted_source_is_fresh(
                    value_source_expr, value_pre_coerce_type, value_type):
                lines.append(f"  call void @festina_retain(ptr {value_val})")
            # claude.md #120: the release of the overwritten value is
            # DEFERRED until after festina_map_set has stored the new
            # one -- a cycle trial run by the release must never see the
            # entry still pointing at the value whose count it just
            # dropped (see _emit_assign's store-before-release comment).
            deferred_release = (self._release_fn_for(value_type), old_ptr)
        elif value_type == TEXT:
            # claude.md #83: the text counterpart just above -- same
            # "look up whatever value the key currently maps to (if
            # any) via festina_map_get with a null default, since there
            # is no fixed address to load an old value from directly"
            # shape, copying (festina_text_own) instead of retaining
            # and freeing (a plain @free) instead of releasing.
            entries_val = self.tmp()
            lines.append(f"  {entries_val} = load ptr, ptr {entries_ptr}")
            capacity_val = self.tmp()
            lines.append(f"  {capacity_val} = load i64, ptr {capacity_ptr}")
            old_raw = self.tmp()
            lines.append(f"  {old_raw} = call i64 @festina_map_get(ptr {entries_val}, i64 {capacity_val}, ptr {key_val}, i64 0)")
            old_ptr = self.tmp()
            lines.append(f"  {old_ptr} = inttoptr i64 {old_raw} to ptr")
            if not self._is_owning_text_source(value_source_expr):
                owned = self.tmp()
                lines.append(f"  {owned} = call ptr @festina_text_own(ptr {value_val})")
                value_val = owned
            lines.append(f"  call void @free(ptr {old_ptr})")
        raw_val = self._map_value_to_i64(value_val, value_type, lines)
        lines.append(f"  call void @festina_map_set(ptr {count_ptr}, ptr {entries_ptr}, "
                     f"ptr {capacity_ptr}, ptr {tombstones_ptr}, ptr {key_val}, i64 {raw_val})")
        if deferred_release is not None:
            release_fn, old_ptr = deferred_release
            lines.append(f"  call void {release_fn}(ptr {old_ptr})")
        # claude.md #97: the key is strdup'd by festina_map_set (see its
        # own comment on why it never aliases the caller's pointer), so
        # a key the caller ALLOCATED -- `m[`s${i}`] = v`, `m[a + b] = v`
        # -- has no owner left once this returns. Freed here rather than
        # at each call site so both the literal path and the assignment
        # path get it from one place; `key_source_expr` is None only
        # where the key is a compile-time constant with nothing to free.
        if key_source_expr is not None:
            self._free_text_temp(key_source_expr, key_val, TEXT, lines)

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

    def _emit_http_socket_field(self, expr, obj_val, obj_type, lines):
        """claude.md #151: req.port/.method/.path/.headers, s.state --
        factored out of _emit_expr's own Member dispatch (which calls
        this after emitting expr.obj itself) specifically so
        _emit_member_load below can ALSO reach it: a real bug caught
        by testing `s.state[k] = v` -- _emit_assign resolves the
        ASSIGNMENT TARGET's own object half (s.state) by calling
        _emit_member_load directly, bypassing _emit_expr's dispatch
        (and therefore these branches) entirely, so without this
        shared helper `s.state[...] = ...` failed with "cannot access
        field 'state' on socket" even though semantic analysis (and a
        plain, non-computed `log(s.state)`) both already worked fine.

        `obj_val`/`obj_type` are the ALREADY-EMITTED receiver -- never
        re-emits expr.obj, so this is safe to call from either site
        without re-running its side effects. Returns (out, type_) if
        `expr` matched one of these fields, or None if the caller
        should fall through to its own ordinary field-access handling
        (a struct/table field genuinely named one of these names --
        see semantic.py's own identical fallthrough)."""
        if expr.computed:
            return None
        if isinstance(obj_type, types_mod.UrlType) and expr.prop in (
                "hash", "hostname", "password", "pathname", "port", "protocol",
                "searchParams", "username"):
            # claude.md #162: same "a runtime call, the real value
            # lives behind the handle" reasoning as http's own fields
            # right below -- every one of these is a small dedicated
            # accessor over the FestinaUrlValue* parseURL() built.
            out = self.tmp()
            fn_and_type = {
                "hash": ("festina_url_hash", TEXT),
                "hostname": ("festina_url_hostname", TEXT),
                "password": ("festina_url_password", TEXT),
                "pathname": ("festina_url_pathname", TEXT),
                "protocol": ("festina_url_protocol", TEXT),
                "username": ("festina_url_username", TEXT),
            }
            if expr.prop == "port":
                lines.append(f"  {out} = call i64 @festina_url_port(ptr {obj_val})")
                result_type = INT
            elif expr.prop == "searchParams":
                lines.append(f"  {out} = call ptr @festina_url_search_params(ptr {obj_val})")
                result_type = types_mod.MapType(TEXT)
            else:
                fn, result_type = fn_and_type[expr.prop]
                lines.append(f"  {out} = call ptr @{fn}(ptr {obj_val})")
            self._release_owned_receiver(expr.obj, obj_val, obj_type, lines)
            # claude.md #162: every one of these hands back a genuinely
            # FRESH value (an owned text copy, or -- searchParams -- an
            # already-retained-on-the-way-out map reference), the exact
            # same _minted_values reasoning http's own fields use just
            # below (skipping this would double-copy text results and
            # leak one reference every time .searchParams is read).
            self._minted_values.add(id(expr))
            return out, result_type
        if isinstance(obj_type, types_mod.HttpType) and expr.prop in (
                "url", "method", "code", "headers", "callback"):
            # claude.md #162 (extended by #163's `callback`): url/
            # method/code are owned dedicated accessor calls (see
            # festina_runtime_http.c's own FestinaHttpValue doc comment
            # -- codegen never lays these fields out itself, same
            # reasoning img.width/.height already have); headers is now
            # the SAME live map every read (retained on the way out),
            # not a rebuild; callback is a bare function pointer,
            # returned as-is -- no ownership question at all, the same
            # reason _emit_http_lit's own callback handling needs no
            # cleanup lambda either.
            self.uses_http = True
            out = self.tmp()
            if expr.prop == "url":
                lines.append(f"  {out} = call ptr @festina_http_url(ptr {obj_val})")
                result_type = TEXT
            elif expr.prop == "method":
                lines.append(f"  {out} = call ptr @festina_http_method(ptr {obj_val})")
                result_type = TEXT
            elif expr.prop == "code":
                lines.append(f"  {out} = call i64 @festina_http_code(ptr {obj_val})")
                result_type = INT
            elif expr.prop == "callback":
                lines.append(f"  {out} = call ptr @festina_http_callback(ptr {obj_val})")
                result_type = types_mod.FuncType((types_mod.HttpType(),), None)
            else:
                lines.append(f"  {out} = call ptr @festina_http_headers(ptr {obj_val})")
                result_type = types_mod.MapType(TEXT)
            self._release_owned_receiver(expr.obj, obj_val, obj_type, lines)
            # claude.md #151: a plain non-computed Member's DEFAULT
            # treatment (_is_owning_refcounted_source /
            # _is_owning_text_source) is "aliasing, not owning" --
            # correct for an ordinary struct field, wrong here:
            # festina_http_url/_method/_headers each return a
            # genuinely FRESH value (an owned text copy, or an
            # already-retained live map) with no other reference
            # anywhere the caller doesn't already own. Marking this
            # node in _minted_values is what tells both of those
            # ownership checks the truth -- the exact same mechanism
            # text[i] (claude.md #150) already established for this
            # identical problem. Skipping this for a text result would
            # silently double-copy it; skipping it for the map result
            # would leak one reference every time .headers is read and
            # its binding later goes out of scope (an extra, never-
            # undone retain codegen would otherwise add on top of this
            # already-fresh/retained value). `code` (a plain i64) needs
            # none of this, but doesn't hurt from it either.
            self._minted_values.add(id(expr))
            return out, result_type
        if isinstance(obj_type, types_mod.SocketType) and expr.prop == "state":
            # claude.md #151: s.state -- the SAME live map every call
            # for this connection (see festina_socket_state's own doc
            # comment in festina_runtime.h), already retained ONE
            # extra time on the way out specifically so this call
            # site's own result reads as fresh/owning -- same
            # _minted_values reasoning as .headers just above,
            # required for the identical reason (skipping it would
            # leak one reference per read whose binding later goes
            # out of scope).
            self.uses_http = True
            out = self.tmp()
            lines.append(f"  {out} = call ptr @festina_socket_state(ptr {obj_val})")
            self._release_owned_receiver(expr.obj, obj_val, obj_type, lines)
            self._minted_values.add(id(expr))
            return out, types_mod.MapType(TEXT)
        return None

    def _emit_member_load(self, expr, env, lines):
        """claude.md #102 released the receiver of a ONE-step field read
        off a call result (`make().n`). claude.md #108 extends that to a
        CHAIN (`make().inner.n`), which #102 could not reach and which
        leaked the entire object graph: `make().inner` saw a struct-typed
        field and bailed out (correctly -- releasing there would free the
        Inner it was about to hand back), and `.n`'s own receiver is a
        Member rather than a Call, so nothing was left to notice the
        call result at all.

        The fix is to decide at the OUTERMOST link, where the type of
        the value that actually escapes the chain is finally known. Each
        nested link parks its receiver instead of releasing it; the
        outermost link, if what it loaded is an ordinary unmanaged value
        (an int/float/bool -- a copy that owes nothing to the object it
        came from), releases every parked receiver. If the chain ends in
        a managed value or a text, nothing is released and the leak
        stands, exactly as before -- freeing the parent would free the
        thing just loaded.

        "Nested" is decided by AST node IDENTITY (_chain_receiver), not
        by "a chain is in flight". A member load reached while emitting
        a call ARGUMENT (`make(other.field).inner.n`) is not part of
        this chain, and treating it as one would silently move its own
        release to a point that may never come.

        claude.md #151: req.port/.method/.path/.headers/s.state are
        checked here too (via _emit_http_socket_field), not just in
        _emit_expr's own Member dispatch -- a real bug caught by
        testing `s.state[k] = v`: _emit_assign resolves an assignment
        TARGET's own object half by calling this function directly,
        bypassing _emit_expr's dispatch (and its own copy of this
        same check) entirely. Neither type has a further chainable
        field off one of these results, so a match here short-
        circuits before this function's own chain-release bookkeeping
        ever runs -- _emit_http_socket_field already released its
        receiver itself; letting the logic below ALSO decide what to
        do with `expr.obj`/`obj_val` would double-handle it."""
        state = self._begin_member_chain(expr)
        handled = None
        try:
            obj_val, obj_type = self._emit_expr(expr.obj, env, lines)
            if not expr.computed:
                handled = self._emit_http_socket_field(expr, obj_val, obj_type, lines)
            if handled is not None:
                out, ftype = handled
            else:
                ptr, ftype = self._member_ptr_from(obj_val, obj_type, expr, lines)
                out, ftype = self._load_field_value(ptr, ftype, lines)
        finally:
            pending = self._end_member_chain(state)
        if handled is not None:
            return out, ftype
        if pending is None:
            # An inner link. Park the receiver -- whether it is
            # releasable depends on a type this frame cannot see yet.
            self._chain_pending.append((expr.obj, obj_val, obj_type))
            return out, ftype
        out = self._release_member_chain(pending, expr.obj, obj_val, obj_type,
                                         ftype, lines, out, chain_expr=expr)
        return out, ftype

    def _begin_member_chain(self, expr):
        """claude.md #108: marks `expr.obj` as the receiver about to be
        emitted, so that if it turns out to be another member load it
        can recognize itself as an inner link of this same chain (see
        _emit_member_load). Returns opaque state for _end_member_chain,
        which must be called in a `finally`."""
        nested = self._chain_receiver is expr
        state = (nested, self._chain_receiver, self._chain_pending)
        if not nested:
            self._chain_pending = []
        self._chain_receiver = expr.obj
        return state

    def _end_member_chain(self, state):
        """Undoes _begin_member_chain. Returns the receivers parked by
        inner links, or None if this frame was ITSELF an inner link and
        so has no decision to make."""
        nested, saved_receiver, saved_pending = state
        self._chain_receiver = saved_receiver
        if nested:
            return None
        pending = self._chain_pending
        self._chain_pending = saved_pending
        return pending

    def _release_member_chain(self, pending, obj_expr, obj_val, obj_type, ftype,
                              lines, out=None, chain_expr=None):
        """claude.md #108 released a chain's call results only when the
        escaping value was a plain copy; claude.md #117 closes the other
        half. A managed escaping value is RETAINED first, then the call
        graph released -- the parent's own cascade decrements the field
        back, netting exactly one reference, owned by this expression
        (the widened _is_owning_refcounted_source is what hands that +1
        to exactly one owner). A text escaping value is COPIED first
        (festina_text_own), since text has no count to retain, then the
        graph -- original included -- is released. In both cases the
        thing that made #102/#108 refuse (the loaded value dying with
        its parent) is prevented by construction rather than avoided by
        leaking.

        Only receivers whose emission MINTED ownership, of refcounted
        type, are released: a Call's fresh result, or (claude.md #119)
        a computed-index member the computed branch retained -- read
        off _minted_values, filled before this runs. An intermediate
        link's value (`.inner` in make().inner.n) is an alias INTO the
        base call's graph, reached exactly once by the base's own
        cascade -- releasing it directly too would double-free.

        claude.md #119: when the escaping value is retained/copied
        here, the CHAIN expression itself (`chain_expr`) is recorded as
        minted, so the ownership predicates report the +1 this emission
        just created -- previously that role fell to the syntax-only
        Call-base walk, which computed bases made insufficient.
        Returns the (possibly replaced) result value."""
        receivers = list(pending) + [(obj_expr, obj_val, obj_type)]
        call_receivers = [
            (e, v, t) for e, v, t in receivers
            if (isinstance(e, ast.Call) or id(e) in self._minted_values)
            and _is_refcounted(t)
        ]
        if not call_receivers:
            return out
        if out is not None and _is_refcounted(ftype):
            lines.append(f"  call void @festina_retain(ptr {out})")
            if chain_expr is not None:
                self._minted_values.add(id(chain_expr))
        elif out is not None and ftype == TEXT:
            owned = self.tmp()
            lines.append(f"  {owned} = call ptr @festina_text_own(ptr {out})")
            out = owned
            if chain_expr is not None:
                self._minted_values.add(id(chain_expr))
        for _, v, t in call_receivers:
            lines.append(f"  call void {self._release_fn_for(t)}(ptr {v})")
        return out

    def _mint_and_release_computed(self, expr, out, obj_val, obj_type,
                                   elem_type, lines):
        """claude.md #119: closes the computed-index half of #117's two
        leftover chain leaks. `getRows()[0]`, `getMap()['k']` -- a
        computed Member whose RECEIVER this expression owns (a Call's
        fresh result, a literal, an owning chain, or another minted
        computed index) used to leak the whole container: nothing ever
        released it, because releasing it before the element escaped
        would free the element too. Same dilemma as #117's field loads,
        same one-instruction answer: mint the element's own ownership
        FIRST (retain a refcounted one, copy a text one), then release
        the container, whose element-release cascade decrements the
        just-retained value back to a net of exactly one reference --
        owned by this expression, recorded in _minted_values so every
        ownership predicate downstream agrees the +1 exists.

        A scalar element needs no minting (its loaded value survives
        the container by copy), so the container is simply released. A
        TABLE-ROW element is the one shape that still cannot be fixed
        this way: a row has no refcount header of its own -- the array
        owns its rows outright (#85) -- so there is nothing to retain,
        and releasing the array would free the row out from under the
        expression. That case deliberately keeps #117's documented
        leak (todo.md), and stays UNRECORDED here so the predicates
        keep treating the row as borrowed -- a text column read off it
        is still copied at its binding, exactly as before.

        Returns the (possibly replaced) element value."""
        if not (_is_refcounted(obj_type)
                and self._is_owning_refcounted_source(expr.obj)):
            return out
        if isinstance(elem_type, types_mod.TableType):
            return out
        if _is_refcounted(elem_type):
            lines.append(f"  call void @festina_retain(ptr {out})")
            self._minted_values.add(id(expr))
        elif elem_type == TEXT:
            owned = self.tmp()
            lines.append(f"  {owned} = call ptr @festina_text_own(ptr {out})")
            out = owned
            self._minted_values.add(id(expr))
        lines.append(f"  call void {self._release_fn_for(obj_type)}(ptr {obj_val})")
        return out

    def _release_owned_receiver(self, obj_expr, obj_val, obj_type, lines):
        """claude.md #102/#108/#117: releases a receiver (or argument)
        value that the current expression OWNS and is now done with -- a
        Call's fresh result, a literal, or a call-based member chain
        (whose +1 _release_member_chain minted). Used by every
        method-call site whose RESULT is a fresh value rather than an
        alias into the receiver -- join's text, toText's rendering, a
        blob method's answer -- which is why, unlike the field-load
        path, no result-type judgement is needed: the result never
        points into what is being released.

        claude.md #118: img/aud receivers are no longer skipped. #110
        skipped them because freeing a call-result img could free
        something shared (`img func get() { return shared }` hands back
        an alias); with the refcount header, the Return path retains an
        aliased value on the way out, so the call result always owns
        its own +1 and releasing it here is a decrement, never a
        premature destroy."""
        if not _is_refcounted(obj_type):
            return
        if not self._is_owning_refcounted_source(obj_expr):
            return
        lines.append(f"  call void {self._release_fn_for(obj_type)}(ptr {obj_val})")

    def _release_http_send_receiver(self, obj_expr, obj_val, lines):
        """claude.md #164: `.send()`'s own receiver-release, used
        instead of the generic _release_owned_receiver above for
        exactly one reason -- `obj_expr` may be a raw ast.MapLit
        (`{...}.send()`, or `http {...}`'s desugared form), which
        _is_owning_refcounted_source was never taught to recognize as
        owning (see _emit_http_lit's own doc comment for why: no other
        http-adjacent value could be built from a bare MapLit before
        claude.md #162). Without this, an anonymous `{...}.send()` --
        no named variable anywhere to release it later -- would leak
        its own freshly-built http value every time. HttpType is
        always refcounted (_is_refcounted's own tuple), so unlike the
        generic version this skips that check entirely."""
        if self._is_owning_refcounted_source(obj_expr) or isinstance(obj_expr, ast.MapLit):
            lines.append(f"  call void @festina_release_http(ptr {obj_val})")

    def _load_field_value(self, ptr, ftype, lines):
        """Loads one field, giving a struct/arr[T]/map[T]-typed one real
        storage the first time it is reached.

        claude.md #97: a field of one of those three types starts as a
        null pointer -- calloc/zeroinitializer gives it no value of its
        own, unlike an int field whose zero IS 0. So reaching through an
        unassigned one (`outer.inner.label`, `s.items.length`)
        dereferenced null and segfaulted, on both reads and writes.
        That contradicted the rule this language already states for
        every other field: an uninitialized field reads as its zero
        value (see _emit_stmt's own VarDecl comment, and #74's module
        note). For a struct field, "zero" means a struct with every
        field at ITS zero -- not the absence of a struct -- and for an
        arr[T]/map[T] field it means an empty one.

        So the storage is created on first use rather than eagerly at
        the parent's declaration. Lazily, because it covers every way a
        struct can come into being with one mechanism -- a stack local,
        a heap local, a global's static storage, a parameter, a field of
        a field -- where eager creation would need a separate pass for
        globals, whose storage is a compile-time `zeroinitializer` with
        nowhere to run an initializer. The value is stored back, so it
        is created once and every later read sees the same one; freeing
        the parent releases it through the field walk #78 already does.
        """
        if not isinstance(ftype, (types_mod.StructType, types_mod.ArrayType,
                                   types_mod.MapType)):
            out = self.tmp()
            lines.append(f"  {out} = load {_llvm_type(ftype)}, ptr {ptr}")
            return out, ftype

        loaded = self.tmp()
        lines.append(f"  {loaded} = load ptr, ptr {ptr}")
        is_null = self.tmp()
        lines.append(f"  {is_null} = icmp eq ptr {loaded}, null")
        make_label = self.label("field.make")
        done_label = self.label("field.done")
        lines.append(f"  br i1 {is_null}, label %{make_label}, label %{done_label}")
        load_pred = self.cur_block

        self._start_block(make_label, lines)
        if isinstance(ftype, types_mod.StructType):
            payload_ty = self.struct_llvm_name(ftype.name)
        elif isinstance(ftype, types_mod.ArrayType):
            # claude.md #174: a struct field has no initializer syntax
            # at all -- every field starts null regardless of type, so
            # `amor arr[T]` fields rely ENTIRELY on this auto-vivify
            # path. Building the wrong (smaller, plain-array-shaped)
            # header here for a field the rest of codegen treats as
            # FESTINA_AMOR_ARRAY_LLVM_TYPE-shaped would be a real
            # buffer overflow the moment festina_array_resize's own
            # amor path first touched its capacity field -- caught by
            # reasoning through this path directly, not by a failing
            # test (the same way claude.md #156's own now-obsolete map
            # version of this bug was originally caught, back when
            # `amor map[T]` still existed as a separate representation).
            payload_ty = FESTINA_AMOR_ARRAY_LLVM_TYPE if ftype.amortized else FESTINA_ARRAY_LLVM_TYPE
        else:
            # claude.md #175: every map[T] field -- there is no
            # amortized variant to distinguish any more, so this is
            # always FESTINA_MAP_LLVM_TYPE's single universal shape.
            payload_ty = FESTINA_MAP_LLVM_TYPE
        # calloc'd, so every field lands on its own zero -- an empty
        # arr[T]/map[T] is exactly {length 0, data null}.
        made = self._emit_fresh_heap_header(payload_ty, lines)
        lines.append(f"  store ptr {made}, ptr {ptr}")
        make_pred = self.cur_block
        lines.append(f"  br label %{done_label}")

        self._start_block(done_label, lines)
        out = self.tmp()
        lines.append(
            f"  {out} = phi ptr [ {loaded}, %{load_pred} ], [ {made}, %{make_pred} ]")
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
        return self._member_ptr_from(obj_val, obj_type, expr, lines)

    def _member_ptr_from(self, obj_val, obj_type, expr, lines):
        """_member_ptr's body, split out so a caller that has ALREADY
        emitted expr.obj can reuse that one emission instead of forcing
        a second one -- see _emit_expr's img .width/.height handling,
        where the object has to be emitted before its type is known and
        expr.obj may be an arbitrary side-effecting expression."""
        if isinstance(obj_type, types_mod.TableType):
            # claude.md #32-34: a table-typed value is one query result
            # row -- flat `field_index * 8` byte offset, not a named
            # struct GEP; see the module docstring's "Query rows" note.
            # claude.md #188 (uraikus/festina#76 item 5): `.rowid` is
            # not a declared column at all (see festina_runtime.c's own
            # festina_sqlite_collect_rows doc comment on why it's kept
            # fully separate from col_count/table_field_index) -- it
            # lives at the FIXED offset right after the presence mask,
            # `(declared column count + 1) * 8`, only ever populated
            # when want_rowid=1 (true for every TableType row, false
            # for a struct query target, which never reaches this
            # branch at all).
            if expr.prop == "rowid":
                idx = len(self.table_fields(obj_type.name)) + 1
                out = self.tmp()
                lines.append(f"  {out} = getelementptr i8, ptr {obj_val}, i64 {idx * 8}")
                return out, INT
            idx = self.table_field_index(obj_type.name, expr.prop)
            ftype = self.table_fields(obj_type.name)[idx][1]
            out = self.tmp()
            lines.append(f"  {out} = getelementptr i8, ptr {obj_val}, i64 {idx * 8}")
            return out, ftype
        if isinstance(obj_type, types_mod.EnumType):
            # claude.md #176: field access only reaches here for a
            # PURE-STRUCT enum (semantic.py's own _infer_member already
            # rejected a mixed enum's field access at compile time), so
            # `obj_val` already IS the self-tagged member struct's own
            # pointer -- no unwrapping needed, only a runtime check that
            # it's really the ONE member semantic.py resolved `prop`
            # against (analyze_enum's own field-collision check already
            # guarantees there's only ever one candidate).
            info = self.enums[obj_type.name]
            owner = next((m for m in info.members if expr.prop in self.structs.get(m.name, {})), None)
            if owner is None:
                raise CodegenError(
                    f"internal error: enum '{obj_type.name}' has no member with field "
                    f"'{expr.prop}' (semantic.py should have already rejected this)",
                    file=self.filename, line=expr.line, column=expr.column)
            # claude.md #176: an enum-typed value defaults to null until
            # assigned (no auto-vivify), so `obj_val` can genuinely be
            # null here -- guarded before the tag GEP/load below (which
            # would otherwise dereference near address -16) the same
            # "fail loudly, never silently misread memory" way a tag
            # MISMATCH already is, just below.
            is_null = self.tmp()
            lines.append(f"  {is_null} = icmp eq ptr {obj_val}, null")
            null_label = self.label("enumfield.null")
            nonnull_label = self.label("enumfield.nonnull")
            lines.append(f"  br i1 {is_null}, label %{null_label}, label %{nonnull_label}")
            self._start_block(null_label, lines)
            null_msg = self.string_const(
                f"field '{expr.prop}' accessed on a null {obj_type.name} value")
            lines.append(f"  call void @festina_fail(ptr {null_msg})")
            lines.append("  unreachable")
            self._start_block(nonnull_label, lines)
            tag_ptr = self.tmp()
            lines.append(f"  {tag_ptr} = getelementptr i8, ptr {obj_val}, i64 -16")
            tag = self.tmp()
            lines.append(f"  {tag} = load ptr, ptr {tag_ptr}")
            owner_const = self._enum_tag_const(owner)
            matches = self.tmp()
            lines.append(f"  {matches} = icmp eq ptr {tag}, {owner_const}")
            ok_label = self.label("enumfield.ok")
            mismatch_label = self.label("enumfield.mismatch")
            lines.append(f"  br i1 {matches}, label %{ok_label}, label %{mismatch_label}")
            self._start_block(mismatch_label, lines)
            # claude.md #176: fails loudly rather than silently reading
            # whatever bytes happen to sit at this offset in a
            # DIFFERENT member struct's own layout -- the runtime
            # safety net for a missing/wrong `typeof` guard. A fixed,
            # compile-time message (no runtime string formatting) --
            # it doesn't need to name the actual mismatched variant to
            # be a clear, immediate, unambiguous failure.
            msg = self.string_const(
                f"field '{expr.prop}' is only valid when this {obj_type.name} value is a "
                f"{owner.name}")
            lines.append(f"  call void @festina_fail(ptr {msg})")
            lines.append("  unreachable")
            self._start_block(ok_label, lines)
            idx = self.struct_field_index(owner.name, expr.prop)
            ftype = self.struct_fields(owner.name)[idx][1]
            out = self.tmp()
            struct_ty = self.struct_llvm_name(owner.name)
            lines.append(f"  {out} = getelementptr {struct_ty}, ptr {obj_val}, i32 0, i32 {idx}")
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

    def _emit_global_retain_release(self, ref, val, ttype, lines, source_expr=None,
                                    source_type=None):
        """claude.md #77 (widened by claude.md #79 to arr[T]/map[T]
        globals, claude.md #83 to text globals): called immediately
        before storing `val` into a GLOBAL's own slot `ref`, whether
        that's an ordinary reassignment (_emit_assign) or a global's
        own declaration-with-initializer (_emit_toplevel_stmt) -- both
        sites need the identical treatment, factored out here so they
        can't drift apart. Returns the value the caller should actually
        store -- `val` unchanged for struct/arr[T]/map[T] (retaining
        never changes which pointer is stored, only its own refcount),
        but possibly a *different*, freshly copied pointer for text
        (see below) -- callers must always store whatever this returns,
        not `val` itself.

        For struct/arr[T]/map[T]: retains the new value (this global's
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
        release site for that struct type.

        For text: unlike struct/arr[T]/map[T], over-copying is NOT
        cheap the way over-retaining is (a real strdup, not a refcount
        increment/decrement pair), so this DOES check
        _is_owning_text_source rather than unconditionally copying --
        `source_expr` (the new value's own AST source, needed only for
        this check) is required whenever `ttype` is TEXT. The old value
        is freed unconditionally via a plain `@free` (never
        festina_release -- text has no refcount header at all, see
        festina_text_own's own comment), safe even the very first time
        a global's value is ever set: its zero value is a plain LLVM
        `null`, and `free(NULL)` is always a defined no-op."""
        if not ref.startswith("@"):
            return val
        if ttype == TEXT:
            old = self.tmp()
            lines.append(f"  {old} = load ptr, ptr {ref}")
            if not self._is_owning_text_source(source_expr):
                owned = self.tmp()
                lines.append(f"  {owned} = call ptr @festina_text_own(ptr {val})")
                val = owned
            lines.append(f"  call void @free(ptr {old})")
            return val
        if not _is_refcounted(ttype):
            return val
        old = self.tmp()
        lines.append(f"  {old} = load ptr, ptr {ref}")
        # claude.md #111: this used to retain UNCONDITIONALLY, on the
        # comment's own reasoning that over-retaining "can only ever
        # delay a free". That was true while a global lived to process
        # exit no matter what -- the extra count was unobservable. `free`
        # made it observable: a global bound to a fresh value carried a
        # count of 2, so `free g` decremented to 1 and freed nothing,
        # silently. The freshness test is the same one locals have
        # always used (a fresh value's own +1 transfers by aliasing).
        if not self._refcounted_source_is_fresh(source_expr, source_type, ttype):
            lines.append(f"  call void @festina_retain(ptr {val})")
        lines.append(f"  call void {self._release_fn_for(ttype)}(ptr {old})")
        return val

    def _is_owning_refcounted_source(self, expr):
        """claude.md #77 (widened): a source expression is "owning" --
        a fresh value with no other binding referencing it yet, so
        aliasing it into a new slot needs no retain, the same "moves,
        doesn't copy" reasoning a Return statement already relies on --
        only when it's a plain function Call. Every other expression
        shape (a bare Identifier reading an existing local/parameter/
        global, a Member/field read, ...) is conservatively treated as
        "aliasing": something ELSE already references this exact value
        (or could), so a NEW binding referencing it too needs its own
        retain. This can only ever retain when it turns out not to have
        been strictly necessary (over-conservative, never under), never
        skip a retain a real alias actually needed -- the same
        directional bias every other stage in this whole effort has
        taken when a choice wasn't fully provable either way.

        claude.md #173: a Ternary is owning too -- not because both of
        its own branches are somehow guaranteed fresh (most aren't),
        but because _emit_ternary itself now normalizes whichever
        branch actually ran into a genuine +1 before this function is
        ever asked about it (retaining a branch whose own source
        wasn't already owning) -- see _own_ternary_branch's own
        comment for the real, ASan-confirmed leak treating a fresh
        ternary branch as merely "aliasing" used to cause: the caller
        retained the ternary's result exactly once regardless of which
        branch ran, so a branch that was ALREADY fresh got an extra,
        unbalanced retain on top of its own +1 every time it was
        chosen.

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
        if isinstance(expr, (ast.Call, ast.ArrayLit, ast.MapLit, ast.Ternary)):
            return True
        # claude.md #119: an expression whose own emission minted a +1
        # -- a retained computed-index element (`getRows()[0]`), or a
        # chain whose escaping value _release_member_chain retained --
        # is owned by exactly the same argument a fresh Call result is.
        # The set is filled during emission, so this can never promise
        # a +1 the emitted IR does not contain.
        if id(expr) in self._minted_values:
            return True
        # claude.md #117: a dotted chain whose base is a Call --
        # `make().inner`, `make().inner.items` -- yields an OWNED value
        # now: _emit_member_load retains the result before releasing
        # the call's own graph, so the +1 transfers to whoever binds it,
        # exactly like a Call's own return value. A chain based on a
        # variable stays aliasing/borrowed, unchanged.
        return self._member_chain_call_base(expr)

    def _member_chain_call_base(self, expr):
        """claude.md #117: True for a non-computed Member chain whose
        ultimate receiver is a Call -- the exact shape _emit_member_load
        emits a retain (or a text copy) for. This predicate and that
        emission MUST agree: the predicate promising ownership the load
        never produced would drop a needed retain, and the reverse
        would leak the one the load added.

        claude.md #119: a chain may also bottom out on a COMPUTED
        member (`getRows()[0].name`). Whether that base owns its value
        is not decidable from syntax -- a retained struct element does,
        a borrowed table row does not -- so the answer is read off
        _minted_values, which the computed-index emission filled before
        this walk could ever run (the chain's own emission emits its
        receiver first)."""
        while isinstance(expr, ast.Member) and not expr.computed:
            expr = expr.obj
        if isinstance(expr, ast.Member) and expr.computed:
            return id(expr) in self._minted_values
        return isinstance(expr, ast.Call)

    def _is_owning_text_source(self, expr):
        """claude.md #83: the text counterpart to
        _is_owning_refcounted_source -- "owning" here means "already a
        fresh, exclusively-held heap buffer, safe to take directly
        with no extra copy," not "safe to alias without retaining"
        (text has no shared representation to retain in the first
        place; the non-owning case calls festina_text_own -- a copy --
        instead of festina_retain -- an increment).

        A Call is owning for the identical reason it already is for
        struct/arr[T]/map[T]: every text-returning runtime function
        this language ever calls through one -- festina_str_concat
        (via a user function that returns text), festina_str_from_int/
        _float/_bool, festina_getenv, festina_regex_match,
        festina_str_replace/festina_regex_replace, sqlite text column
        reads -- always mallocs a fresh buffer nothing else references
        yet, whether reached directly or through a user-defined
        function's own `return` (which, by this same rule applied
        recursively, only ever hands back something IT already knows
        is fresh). A method call (`text.replace(...)`, `regex.test()`,
        ...) is syntactically a Call too (a Member callee, not a bare
        name) -- no separate case needed.

        A TemplateLit is owning because _emit_template itself
        guarantees every value it returns is a fresh buffer, never a
        bare alias of one of its own interpolated pieces -- see its
        own comment for exactly how (claude.md #82's own "skip the
        empty-piece concat" optimization had to be revised alongside
        this section to preserve that guarantee once text started
        being freed for real).

        A `+` BinOp is owning too (claude.md #97). In a text context
        `+` is concatenation and _emit_binop compiles it to exactly one
        @festina_str_concat, which mallocs unconditionally -- there is
        no operand-passthrough path, not even for an empty operand, so
        its result is always a fresh buffer. Leaving it out of this
        list (as claude.md #83 originally did) meant EVERY binding of a
        concatenation copied a buffer that was already exclusively
        owned and then dropped the original on the floor: `text j = a +
        b` and `return s + '!'` both leaked the concat result, once per
        evaluation. Reading the op alone is safe because every caller
        of this function has already established that the value's type
        is text, so a `+` reaching here is never integer addition.

        Everything else -- a bare Identifier (a local, a global, a
        parameter -- all just aliasing whatever they already point
        to), a Member/field read, and critically a StringLit itself (a
        pointer to a `.str.N` global CONSTANT, never allocated at all
        -- freeing one would corrupt the binary's own static data) --
        is "aliasing": conservatively copied via festina_text_own
        before being stored into a new binding, the same directional
        bias every prior stage in this whole effort defaults to
        whenever a choice isn't fully provable either way.

        claude.md #173: a Ternary is owning too now -- NOT because a
        ternary's own two branches are somehow both guaranteed fresh
        (most aren't), but because _emit_ternary itself now normalizes
        whichever branch actually ran into a genuinely fresh, owned
        buffer before this function is ever asked about it (copying it
        there if the branch's own source wasn't already owning) -- see
        _own_ternary_branch's own comment for why a Ternary used to be
        listed under "everything else... is aliasing" just above, and
        what that got silently wrong."""
        if isinstance(expr, ast.BinOp):
            return expr.op == "+"
        if isinstance(expr, (ast.Call, ast.TemplateLit, ast.Ternary)):
            return True
        # claude.md #151: a direct _minted_values check, mirroring
        # _is_owning_refcounted_source's own top-level check just
        # above it in this file -- needed for a NON-computed Member
        # whose own emission (not a chain walk) already guarantees a
        # fresh buffer, e.g. req.method/req.path (festina_http_method/
        # _path always return an owned copy). _member_chain_call_base
        # just below only ever consults _minted_values for a COMPUTED
        # member (or one reached by walking past non-computed dots to
        # find one) -- text[i]'s own marking (claude.md #150) happens
        # to be computed already, so it worked without this; a plain
        # `.prop` access never reaches that check at all without this
        # direct one first. Safe to add unconditionally: every node
        # this set has ever held was deliberately marked BECAUSE its
        # own emission is already known-fresh, so this can only ever
        # confirm what's already true, never manufacture a wrong
        # answer for a node nothing marked.
        if id(expr) in self._minted_values:
            return True
        # claude.md #117: a call-based chain ending in a text field
        # (`make().inner.label`) hands back a COPY -- _emit_member_load
        # runs it through festina_text_own before releasing the graph it
        # aliased into -- so it is exactly as owned as a concat result.
        return self._member_chain_call_base(expr)

    def _free_text_temp(self, source_expr, val, vtype, lines):
        """claude.md #83: frees a text value that the expression being
        emitted allocated itself and that nothing took ownership of --
        a call result or template literal used as an argument or a
        method receiver and then discarded, e.g. the `f()` in
        `log(f())` or the `` `${x}` `` in `g(`${x}`)`.

        Callees never take ownership of a text argument: a parameter the
        callee reassigns is copied at binding time (claude.md #84), and
        one it only reads is borrowed for the duration of the call, so
        the caller always still owns what it passed once the call
        returns. Freeing is therefore the caller's job at every one of
        these sites, and skipping it leaks the buffer outright -- there
        is no binding anywhere that would ever free it later.

        Every call site below evaluates its operands and consumes them
        within the SAME basic block, so emitting the free immediately
        after the consuming call always dominates correctly with no
        cleanup slot needed, and -- unlike a statement-level cleanup
        list -- frees once per loop iteration rather than once per loop.
        A borrowed value (an identifier's own buffer, a `.str.N`
        literal constant) is never freed here, exactly per
        _is_owning_text_source's own split."""
        if vtype == TEXT and self._is_owning_text_source(source_expr):
            lines.append(f"  call void @free(ptr {val})")

    def _emit_json_arg_text(self, arg_expr, env, lines, expected_type=None):
        """claude.md #158: evaluates arg_expr and renders it to text via
        _to_text -- used by troubleshoot()/fail()'s structured forms to
        get a JSON-safe piece for whatever type was passed (the exact
        same conversion log()'s own struct/map/blob path already uses),
        since (unlike log()'s primitive fast path, which prints an
        int/float/bool directly with no text conversion at all) every
        one of these needs a real text representation to splice into
        the surrounding JSON envelope.

        Returns (text_val, arg_expr, val, vtype) -- text_val is ready to
        pass to whatever runtime call actually consumes it; the other
        three are exactly what _cleanup_json_arg_text needs, deferred to
        its own separate call so the caller controls WHEN cleanup runs
        relative to that consuming call (seemingly obvious, but a real
        bug here -- freeing text_val inside this same method, before
        the caller had even emitted the call actually using it, was
        caught directly: real garbage bytes in troubleshoot()'s own
        stdout output rather than the rendered fields JSON -- see
        _cleanup_json_arg_text's own comment)."""
        if expected_type is not None:
            # claude.md #158: threads a fixed expected type (always
            # map[text] for troubleshoot()/fail()'s own fields argument)
            # into an ArrayLit/MapLit exactly the way a var declaration's
            # own init already does -- without this, `troubleshoot('x',
            # {})` couldn't resolve an empty literal's value type at
            # all, and `troubleshoot('x', {'a': 'b'})` would infer a
            # plain map[T] with no connection to this expected shape.
            val, vtype = self._emit_value_for(arg_expr, env, lines, expected_type)
        else:
            val, vtype = self._emit_expr(arg_expr, env, lines)
        text_val = self._to_text(val, vtype, lines)
        return text_val, arg_expr, val, vtype

    def _cleanup_json_arg_text(self, text_val, arg_expr, val, vtype, lines):
        """claude.md #158: the OTHER half of _emit_json_arg_text --
        called by troubleshoot()'s own codegen AFTER emitting the
        festina_troubleshoot() call that actually consumes text_val
        (never by fail(), which always exits right after either form,
        so, matching every other fail()/exit() call site's own
        established "dead code past an exiting call" precedent, no
        cleanup is emitted there at all). Frees text_val unless it's a
        bare alias of an already-owned text value (_to_text is a no-op
        passthrough there, so text_val IS val -- freeing it would free
        something the original binding still owns and will free at its
        own scope-exit), then releases the ORIGINAL value's own
        reference via _release_owned_receiver (always safe to call
        regardless of type -- it no-ops for anything that isn't
        refcounted, e.g. plain text/int/float/bool) -- exactly the
        cleanup log()'s own struct/map/blob argument path already does."""
        if vtype != TEXT or self._is_owning_text_source(arg_expr):
            lines.append(f"  call void @free(ptr {text_val})")
        self._release_owned_receiver(arg_expr, val, vtype, lines)

    def _free_regex_temp(self, source_expr, val, vtype, lines):
        """claude.md #85: the regex counterpart to _free_text_temp --
        frees a regex_t compiled by a runtime `regex(...)` call and used
        directly as a method's own receiver or argument
        (`regex(p).test(s)`), which nothing previously ever freed, so a
        `regex(...)` inside a loop leaked a full compiled automaton
        (several kilobytes) per iteration.

        The owning test is `isinstance(source_expr, ast.Call)`, which
        cleanly separates the two ways a regex value is produced: only
        `regex(...)` is a Call (whose memoized result carries this
        expression's own +1 -- see festina_regex_compile_memo), while a
        /pattern/ literal is an ast.RegexLit whose cached compilation
        is immortal, so releasing it here would be a harmless no-op
        anyway. A regex bound to a variable is an Identifier at its use
        sites and is not released here -- its own binding's scope-exit
        release owns that reference (claude.md #118)."""
        if vtype == REGEX and isinstance(source_expr, ast.Call):
            lines.append(f"  call void @festina_regex_free(ptr {val})")

    def _is_stack_allocatable_array_or_map_decl(self, stmt, type_):
        """claude.md #79 (extended by #81): whether an arr[T]/map[T]-
        typed VarDecl can keep its own HEADER on the stack instead of
        allocating a fresh, refcounted heap one -- true for a non-
        escaping local with either no initializer at all (claude.md
        #79's own original case), or one initialized directly from an
        array/map literal (claude.md #81: a literal's own element/entry
        count is always known right at the declaration site, so its
        data/entries buffer's own size is too -- see
        _emit_array_lit/_emit_map_lit's own `header` parameter, which is
        what actually builds into a stack slot this method says is
        eligible for one).

        Deliberately conservative the same direction
        _is_owning_refcounted_source already is: an initializer that's
        merely an IDENTIFIER bound to some OTHER array/map literal
        (`arr[int] a = [1,2,3]` then, elsewhere, `arr[int] b = a`) is
        NOT covered here -- only a literal written directly at this
        declaration's own initializer position is, since only then is
        the buffer size provably known without chasing another
        binding's own history.

        Shared by _emit_stmt (which actually emits the stack allocation
        this says is safe) and _emit_block (which decides how to
        schedule it for scope-exit cleanup -- a plain heap-refcounted
        RELEASE for anything this returns False for, a _StackArrayOrMap
        entry, freeing only the still-heap data/entries buffer, for
        anything it returns True for) so the two decisions can never
        drift apart into disagreeing about the same local.

        Sound for the identical reason claude.md #79's own no-init case
        already is, not a new argument: "non-escaping" here means
        escape_analysis's own existing whole-function analysis already
        proved this name is never returned, never stored anywhere
        longer-lived, and -- critically -- never itself the target of a
        LATER reassignment either (an assignment target always escapes,
        by escape_analysis's own existing rule -- see
        _emit_local_retain_release's own comment), so a stack-header
        local built here can never later be pointed at a *different*,
        possibly-heap value the way `_emit_assign`'s general retain/
        release machinery would otherwise need to account for.

        claude.md #174: `amor arr[T]` is unconditionally excluded --
        this optimization was never extended to it (a deliberate scope
        boundary; it always heap-allocates instead, through the same
        generic with-initializer path blob/img/aud/etc. already use).
        map[T] has no such exclusion any more -- claude.md #175 removed
        `amor map[T]` outright, so every non-escaping map[T] local is
        eligible on the same terms as a plain arr[T] one; a hash
        table's own growth (rehashing into a fresh bucket array,
        writing the new entries/capacity/tombstones back through the
        header's own out-pointers) works exactly the same whether the
        header itself lives on the stack or the heap, so nothing about
        #175's representation change disqualifies this optimization."""
        if self._current_escaping_names is None or stmt.name in self._current_escaping_names:
            return False
        if isinstance(type_, types_mod.ArrayType) and type_.amortized:
            return False
        if stmt.init is None:
            return True
        literal_cls = ast.ArrayLit if isinstance(type_, types_mod.ArrayType) else ast.MapLit
        return isinstance(stmt.init, literal_cls)

    def _refcounted_source_is_fresh(self, source_expr, source_type, target_type):
        """claude.md #109: _is_owning_refcounted_source asks "is this a
        Call", which is the right question for every refcounted type
        that existed before blob did -- those are only ever produced by
        a call or a literal. A blob is produced by a text expression
        too: `blob f = 'notes.txt'`, or any other text, which _coerce
        turns into a festina_blob_open call. That result is exactly as
        fresh as any other call result, but the AST node is a StringLit
        (or a BinOp, or an Identifier holding a path), so asking about
        the node alone gets it wrong -- and getting it wrong here means
        retaining a handle whose count is already 1, which leaks it
        permanently. Measured before this existed: one handle, its path
        and its bytes leaked per reassignment.

        So the freshness test for a blob is whether a COERCION
        happened, which `source_type` records: text in, blob out means
        _coerce emitted the open call itself.

        claude.md #118: img and aud have the identical short form
        (`img s = 'x.png'` -> festina_load_image via _coerce), so the
        same text-in/handle-out test covers them. A /re/ literal is
        "fresh" too, on different grounds: its cached compilation is
        immortal, so retain and release are both no-ops on it and the
        cheaper answer (skip the retain) is the right one."""
        if source_type == TEXT and (
                target_type == BLOB
                or isinstance(target_type, (types_mod.ImageType,
                                            types_mod.AudioType))):
            return True
        if isinstance(source_expr, ast.RegexLit):
            return True
        # claude.md #176: a mixed enum's own coercion (_coerce's enum
        # branch) always builds a brand-new heap box, exactly as fresh
        # as festina_blob_open's own handle above, for the identical
        # reason -- the RESULT this function is being asked about is
        # never the same allocation `source_expr` itself produced. A
        # PURE-STRUCT enum's own coercion is a plain identity pass-
        # through (see _coerce) -- no new allocation happens, so
        # freshness of the result is exactly freshness of the original
        # source, and this falls through to the generic check below
        # unchanged.
        if isinstance(target_type, types_mod.EnumType):
            info = self.enums.get(target_type.name)
            if info is not None and not info.is_pure_struct and source_type in info.members:
                return True
        return self._is_owning_refcounted_source(source_expr)

    def _emit_local_retain_release(self, ref, val, source_expr, ttype, lines,
                                   source_type=None):
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
        if not self._refcounted_source_is_fresh(source_expr, source_type, ttype):
            lines.append(f"  call void @festina_retain(ptr {val})")
        lines.append(f"  call void {self._release_fn_for(ttype)}(ptr {old})")

    def _emit_local_text_own_release(self, ref, val, source_expr, lines):
        """claude.md #83: the text counterpart to
        _emit_local_retain_release -- called from _emit_assign's
        Identifier branch for an ordinary `s = expr` reassignment of an
        already-declared LOCAL text variable (never the target's own
        first-ever declaration; _emit_stmt's VarDecl handling covers
        that separately, since it never has an "old value" to free).
        No escaping check needed here at all, unlike the struct/
        arr[T]/map[T] version: a text local's own buffer is always
        heap-allocated regardless of whether the local itself escapes
        (a dynamically-sized string was never a stack-allocation
        candidate to begin with -- see _emit_stmt's own VarDecl
        comment), so there's no "was this ever stack-allocated"
        question to rule out the way there is for the other three
        types. Copies the new value via festina_text_own only when
        _is_owning_text_source says it needs it (a fresh call/template
        result already owns its own +1 outright, no copy needed --
        copying it anyway would just leak that original allocation once
        nothing else ever references it either); always frees the old
        value unconditionally via a plain @free (text has no refcount
        header, never festina_release). Returns the value the caller
        should actually store, mirroring _emit_global_retain_release's
        own return-a-value contract -- `val` itself when owning, the
        freshly copied pointer otherwise."""
        old = self.tmp()
        lines.append(f"  {old} = load ptr, ptr {ref}")
        if not self._is_owning_text_source(source_expr):
            owned = self.tmp()
            lines.append(f"  {owned} = call ptr @festina_text_own(ptr {val})")
            val = owned
        lines.append(f"  call void @free(ptr {old})")
        return val

    def _release_fn_for(self, type_):
        """claude.md #79 (widened by claude.md #80): the single dispatch
        point every release call site in this file goes through
        (mirroring claude.md #78's own _release_fn_for_struct, now one
        case among three rather than the only one) -- returns the LLVM
        function name to call to release a refcounted value of `type_`.
        A struct gets _release_fn_for_struct's own per-type dispatch
        (the plain generic release, or a lazily-generated per-struct-
        type cascade wrapper, depending on whether it has its own
        struct-typed field). An arr[T] gets _release_fn_for_array's own
        analogous dispatch (the plain generic release, or a lazily-
        generated per-element-type cascade wrapper, depending on
        whether T is itself refcounted); map[T] gets
        _release_fn_for_map's own."""
        if isinstance(type_, types_mod.StructType):
            return self._release_fn_for_struct(type_)
        if isinstance(type_, types_mod.ArrayType):
            return self._release_fn_for_array(type_)
        if isinstance(type_, types_mod.MapType):
            return self._release_fn_for_map(type_)
        if type_ == BLOB:
            # claude.md #109: a blob carries the ordinary refcount
            # header, so the only thing generic @festina_release cannot
            # do for it is free the path and byte buffer hanging off
            # the payload -- which is exactly the shape of a per-struct
            # cascade wrapper, except the runtime can write this one
            # once instead of codegen generating it per type.
            return "@festina_blob_release"
        if isinstance(type_, types_mod.ImageType):
            # claude.md #118: same shape as blob -- release semantics
            # with a runtime-written destructor (surface, bytes, path).
            # uses_graphics_CODE, not uses_graphics: releasing an image
            # needs no X server, same distinction the loading coercion
            # already draws.
            self.uses_graphics_code = True
            return "@festina_image_free"
        if isinstance(type_, types_mod.AudioType):
            # claude.md #118: destruction stops every channel still
            # playing the clip first -- see festina_audio_free.
            self.uses_audio = True
            return "@festina_audio_free"
        if type_ == REGEX:
            # claude.md #118: regfree on the last reference; a cached
            # /pattern/ literal is immortal and no-ops through here.
            return "@festina_regex_free"
        if isinstance(type_, types_mod.HttpType):
            # claude.md #162: http moved to a real destructor -- url/
            # method/headers/body all live directly in the value now
            # (see festina_runtime_http.c's own FestinaHttpValue doc
            # comment), so releasing one has real contents to free,
            # unlike the tiny {refcount, conn_id} handle socket still
            # uses just below.
            self.uses_http = True
            return "@festina_release_http"
        if isinstance(type_, types_mod.SocketType):
            # claude.md #151: the tiny handle itself is all there is to
            # free (the underlying connection is owned separately by
            # festina_runtime_http.c's own connection table, torn down
            # independently of any handle's lifetime -- see
            # festina_runtime.h's doc comment).
            self.uses_http = True
            return "@festina_release_conn_handle"
        if isinstance(type_, types_mod.UrlType):
            # claude.md #162: lives in CORE (festina_runtime.c), not
            # festina_runtime_http.c -- parseURL() has nothing to do
            # with openPort()/on request/fetch() and must not force
            # uses_http on a program that only ever parses a URL.
            return "@festina_release_url"
        if type_ == TEXT:
            # claude.md #83: text has no refcount header to dispatch
            # through -- "releasing" one is always just a plain,
            # NULL-safe @free (used here so an array/map's own
            # element-type cascade -- _release_fn_for_array/_map,
            # _emit_release_array_elements, _emit_map_value_release_
            # trampoline -- can call _release_fn_for(elem_type) exactly
            # the same way regardless of whether elem_type is text or
            # one of the other three refcounted types).
            return "@free"
        if isinstance(type_, types_mod.EnumType):
            return self._release_fn_for_enum(type_)
        raise CodegenError(f"cannot release a value of type {types_mod.type_name(type_)}")

    def _struct_has_own_managed_field(self, name):
        """claude.md #78 (widened by #79 to arr[T]/map[T] fields, and by
        #83 to text ones): True when the struct declared `name` has at
        least one field of its own whose value this compiler manages --
        a struct/array/map (refcounted) or a text (an exclusively-owned
        heap buffer) -- never transitively (see _release_fn_for_struct's
        own comment on why only the direct case needs checking here).

        The text case was missed when #83 first landed, and it was a
        real leak in both directions this predicate gates: a
        stack-allocated struct local with a text field was never
        scheduled for field release at all, and a heap-allocated one got
        the plain generic @festina_release instead of a per-struct
        wrapper, so in neither case was the field's own buffer ever
        freed. Caught by an ASan run over a struct whose text field is
        reassigned (`p.name = `tmpl ${p.name}``), which leaked the
        field's final buffer every time."""
        return any(_is_refcounted(t)
                   or t == TEXT
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
        duplicate/infinite chain of wrapper functions -- but NOT for
        the reason this comment used to give. It said the type graph
        was a DAG by construction, because a struct field's type had to
        be declared before the struct containing it; claude.md #106
        removed that ordering rule, so `struct Node { next:Node }`
        compiles and the graph can now genuinely contain a cycle. What
        actually guarantees termination is the cache write below, which
        happens BEFORE the field loop recurses: a struct type that
        reaches itself finds its own name already registered and gets
        the name back instead of generating a second wrapper. So a
        struct's release wrapper CAN now call itself, directly or
        indirectly, and that is correct -- it is one function, and the
        recursion it performs is at runtime over the actual object
        graph, bounded by refcounts reaching zero.

        Which is also where the real cost of #106 lands: a runtime
        reference CYCLE never reaches zero, so it is never freed. See
        todo.md's "What's still ahead" -- that needs a tracing
        collector, and there isn't one."""
        # claude.md #176: a TAGGED struct (a pure-struct enum member)
        # always needs its own wrapper, even with no managed field of
        # its own -- its true allocation base sits 16 bytes back from
        # the payload, not 8, so the plain generic @festina_release
        # (which always frees at payload-8) would free the wrong
        # address entirely.
        tagged = type_.name in self._tagged_structs
        if not self._struct_has_own_managed_field(type_.name) and not tagged:
            return "@festina_release"
        if type_.name in self._struct_release_fns:
            return self._struct_release_fns[type_.name]
        fn_name = f"@__festina_release_struct_{type_.name}"
        # Registered before the field loop below (which recurses back
        # into this same method for every struct-typed field) so any
        # caller reaching this struct type again while this one's own
        # body is still being built gets the cached name immediately
        # rather than triggering a second generation. Two cases hit
        # this: two sibling fields of some other struct sharing this
        # type -- a redundancy -- and, since claude.md #106, a struct
        # that reaches ITSELF, where this write is the only thing
        # standing between the compiler and infinite recursion.
        self._struct_release_fns[type_.name] = fn_name
        struct_ty = self.struct_llvm_name(type_.name)
        cyclic = self._is_cyclic_type(type_)
        body = [f"define void {fn_name}(ptr %payload) {{", "entry:"]
        should_free = self.tmp()
        body.append(f"  {should_free} = call i8 @festina_release_check(ptr %payload)")
        cond = self.tmp()
        body.append(f"  {cond} = icmp ne i8 {should_free}, 0")
        free_label = self.label("relstruct.free")
        done_label = self.label("relstruct.done")
        # claude.md #120: a possibly-cyclic type's not-last-reference
        # branch runs a trial deletion instead of doing nothing -- the
        # released value may be the last EXTERNAL reference to a cycle
        # whose internal edges hold every count above zero. Acyclic
        # types keep the plain two-way branch and pay nothing.
        alive_label = self.label("relstruct.alive") if cyclic else done_label
        body.append(f"  br i1 {cond}, label %{free_label}, label %{alive_label}")
        body.append(f"{free_label}:")
        self._emit_release_struct_field_refs("%payload", type_, body)
        # claude.md #176: a tagged struct's true allocation base is 16
        # bytes back (tag word, then refcount word, then payload -- see
        # _emit_fresh_heap_header's own comment); every other struct
        # keeps the original 8-byte (refcount-only) offset.
        header_offset = -16 if tagged else -8
        header = self.tmp()
        body.append(f"  {header} = getelementptr i8, ptr %payload, i64 {header_offset}")
        body.append(f"  call void @free(ptr {header})")
        body.append(f"  br label %{done_label}")
        if cyclic:
            self._emit_cycle_trial(body, type_, alive_label, done_label)
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
            if _is_refcounted(ftype):
                field_release_fn = self._release_fn_for(ftype)
                fptr = self.tmp()
                lines.append(f"  {fptr} = getelementptr {struct_ty}, ptr {obj_ptr}, i32 0, i32 {i}")
                fval = self.tmp()
                lines.append(f"  {fval} = load ptr, ptr {fptr}")
                lines.append(f"  call void {field_release_fn}(ptr {fval})")
            elif ftype == TEXT:
                # claude.md #83: a text-typed field is copy-managed, not
                # refcounted -- freed with a plain @free (NULL-safe),
                # never through _release_fn_for's own struct/arr[T]/
                # map[T] dispatch, since there's no refcount header to
                # decrement here at all.
                fptr = self.tmp()
                lines.append(f"  {fptr} = getelementptr {struct_ty}, ptr {obj_ptr}, i32 0, i32 {i}")
                fval = self.tmp()
                lines.append(f"  {fval} = load ptr, ptr {fptr}")
                lines.append(f"  call void @free(ptr {fval})")

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

    def _release_fn_for_array(self, type_):
        """claude.md #80: the arr[T] counterpart to
        _release_fn_for_struct -- returns the plain, generic
        `@festina_release_array` (unchanged from claude.md #79) when
        T is not itself refcounted, since there's nothing for a
        release to cascade into; a dedicated, lazily-generated, cached
        (by `types_mod.type_name(type_)`, since arr[T] has no name of
        its own the way a struct does) per-element-type wrapper when
        T is a struct/arr/map.

        That wrapper decrements the refcount via festina_release_check,
        and -- only if this was the array's last reference -- releases
        each of its own elements (via T's own release function, found
        by calling _release_fn_for again -- recursing back into this
        same method for a nested arr[T] of arr[U]) in a plain runtime
        loop over the array's own length, before freeing the data
        buffer and the header itself (duplicating
        festina_release_array's own free logic -- the two can't simply
        call each other, since the element loop has to run strictly
        between the refcount check and the actual free).

        This recursion always terminates, for an even stronger reason
        than claude.md #77's own struct field DAG argument: arr[T]'s
        own element type is always a fresh syntactic type expression
        written out in the source (`arr[arr[int]]`, ...), never a name
        that could refer back to itself the way a struct declaration
        technically could before claude.md #48's "declared before
        used" rule forbids it -- there is no way to even *write* a
        self-referential arr[T] type in Festina's own grammar."""
        elem_type = type_.element
        if not (isinstance(elem_type, (types_mod.StructType, types_mod.ArrayType,
                                       types_mod.MapType, types_mod.TableType))
                or elem_type == TEXT):
            return "@festina_release_array"
        key = types_mod.type_name(type_)
        if key in self._array_release_fns:
            return self._array_release_fns[key]
        fn_name = f"@__festina_release_array_{self._unique()}"
        self._array_release_fns[key] = fn_name
        # claude.md #85: an arr[Table] owns its rows outright (they have
        # no refcount header of their own to share), so its cascade
        # frees each one directly instead of releasing a reference.
        if isinstance(elem_type, types_mod.TableType):
            elem_release_fn = self._emit_table_row_release_fn(elem_type)
        else:
            elem_release_fn = self._release_fn_for(elem_type)
        elem_llvm_ty = _llvm_type(elem_type)
        cyclic = self._is_cyclic_type(type_)
        body = [f"define void {fn_name}(ptr %payload) {{", "entry:"]
        should_free = self.tmp()
        body.append(f"  {should_free} = call i8 @festina_release_check(ptr %payload)")
        cond = self.tmp()
        body.append(f"  {cond} = icmp ne i8 {should_free}, 0")
        free_label = self.label("relarr.free")
        done_label = self.label("relarr.done")
        # claude.md #120: same trial-on-survival branch the struct
        # wrapper grows, for an arr[T] whose T sits on a cycle.
        alive_label = self.label("relarr.alive") if cyclic else done_label
        body.append(f"  br i1 {cond}, label %{free_label}, label %{alive_label}")
        body.append(f"{free_label}:")
        len_ptr = self.tmp()
        body.append(f"  {len_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr %payload, i32 0, i32 0")
        len_val = self.tmp()
        body.append(f"  {len_val} = load i64, ptr {len_ptr}")
        data_field_ptr = self.tmp()
        body.append(f"  {data_field_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr %payload, i32 0, i32 1")
        data_ptr = self.tmp()
        body.append(f"  {data_ptr} = load ptr, ptr {data_field_ptr}")
        self._emit_release_array_elements(data_ptr, len_val, elem_release_fn, elem_llvm_ty, body)
        body.append(f"  call void @free(ptr {data_ptr})")
        header = self.tmp()
        body.append(f"  {header} = getelementptr i8, ptr %payload, i64 -8")
        body.append(f"  call void @free(ptr {header})")
        body.append(f"  br label %{done_label}")
        if cyclic:
            self._emit_cycle_trial(body, type_, alive_label, done_label)
        body.append(f"{done_label}:")
        body.append("  ret void")
        body.append("}")
        body.append("")
        self.func_defs.extend(body)
        return fn_name

    def _emit_table_row_release_fn(self, table_type):
        """claude.md #85: generates and registers a per-table function
        freeing exactly one sqlite result row -- each of its own
        text/blob columns, then the row buffer itself.

        A row is deliberately not shaped like any other Festina value:
        festina_sqlite_collect_rows builds it as a plain
        `malloc(col_count * sizeof(int64_t))` with each text/blob column
        strdup'd into its slot, with no refcount header in front of it,
        so `festina_release` (which reads the 8 bytes before the
        payload) could never be pointed at one. That is why this is a
        bespoke function reached only from _release_fn_for_array, rather
        than a TableType case inside _release_fn_for: the array owns its
        rows outright, and any other TableType-typed binding -- a
        `People p = rows[0]` local, a row passed to a function -- is
        only ever borrowing one the array still owns.

        Which columns hold a heap pointer is decided by the identical
        rule the runtime used when building the row (`text` or `blob`
        -> strdup, everything else -> a plain i64), read off the same
        declared column types. free(NULL) is a no-op, which covers a
        column that was SQL NULL and so was never strdup'd at all.

        claude.md #101: an `aud`/`img` column is a heap pointer too, but
        NOT a plain buffer -- the runtime decoded the stored BLOB into a
        real handle, so freeing it needs that type's own destructor
        rather than free(). Missing this would have leaked one decoded
        clip or surface per row for as long as the result array lived,
        which is the shape a `SELECT * FROM Music` in a loop takes."""
        key = table_type.name
        if key in self._table_row_release_fns:
            return self._table_row_release_fns[key]
        fn_name = f"@__festina_release_row_{table_type.name}_{self._unique()}"
        self._table_row_release_fns[key] = fn_name
        cols = self.tables[table_type.name]
        body = [f"define void {fn_name}(ptr %row) {{", "entry:"]
        # claude.md #109: blob joins these -- a blob column is a real
        # handle now, so freeing it with plain @free would leak its path
        # and byte buffer and skip its refcount entirely.
        media_free = {"aud": "@festina_audio_free", "img": "@festina_image_free",
                      "blob": "@festina_blob_release"}
        for i, col_type in enumerate(cols.values()):
            if col_type != "text" and col_type not in media_free:
                continue
            slot = self.tmp()
            body.append(f"  {slot} = getelementptr i64, ptr %row, i64 {i}")
            val = self.tmp()
            body.append(f"  {val} = load ptr, ptr {slot}")
            # claude.md #101: a decoded handle needs its own destructor,
            # not free() -- an img owns a Cairo surface and an aud owns
            # its decoded PCM, neither of which a plain free() releases.
            body.append(f"  call void {media_free.get(col_type, '@free')}(ptr {val})")
        body.append("  call void @free(ptr %row)")
        body.append("  ret void")
        body.append("}")
        body.append("")
        self.func_defs.extend(body)
        return fn_name

    def _emit_release_array_elements(self, data_ptr, len_val, elem_release_fn, elem_llvm_ty, lines):
        """claude.md #80: the shared element-release loop both
        _release_fn_for_array's own generated wrapper (a heap-allocated
        array about to be freed) and _emit_free_active_locals (a
        stack-allocated array whose own header is never freed, but
        whose elements' own references still need dropping) build on
        -- a plain counted loop from 0 to `len_val`, releasing the
        element at each index via `elem_release_fn`. Never touches
        `data_ptr` itself or frees anything -- entirely the caller's
        own responsibility, since the two callers need different
        things done with it afterward."""
        idx_slot = self.tmp()
        lines.append(f"  {idx_slot} = alloca i64")
        lines.append(f"  store i64 0, ptr {idx_slot}")
        loop_cond = self.label("relarr.loopcond")
        loop_body = self.label("relarr.loopbody")
        loop_end = self.label("relarr.loopend")
        lines.append(f"  br label %{loop_cond}")
        lines.append(f"{loop_cond}:")
        idx_val = self.tmp()
        lines.append(f"  {idx_val} = load i64, ptr {idx_slot}")
        keep_going = self.tmp()
        lines.append(f"  {keep_going} = icmp slt i64 {idx_val}, {len_val}")
        lines.append(f"  br i1 {keep_going}, label %{loop_body}, label %{loop_end}")
        lines.append(f"{loop_body}:")
        elem_ptr = self.tmp()
        lines.append(f"  {elem_ptr} = getelementptr {elem_llvm_ty}, ptr {data_ptr}, i64 {idx_val}")
        elem_val = self.tmp()
        lines.append(f"  {elem_val} = load {elem_llvm_ty}, ptr {elem_ptr}")
        lines.append(f"  call void {elem_release_fn}(ptr {elem_val})")
        next_idx = self.tmp()
        lines.append(f"  {next_idx} = add i64 {idx_val}, 1")
        lines.append(f"  store i64 {next_idx}, ptr {idx_slot}")
        lines.append(f"  br label %{loop_cond}")
        lines.append(f"{loop_end}:")

    def _release_fn_for_map(self, type_):
        """claude.md #80: the map[T] counterpart to
        _release_fn_for_array -- returns the plain, generic
        `@festina_release_map` when T is not itself refcounted, or a
        dedicated, lazily-generated, cached per-value-type wrapper when
        it is.

        Unlike an array's own elements (a flat buffer codegen already
        knows the exact layout of), a map's own entries are opaque to
        codegen -- FestinaMapEntry's own C struct layout is only ever
        touched from festina_runtime.c, deliberately (see
        festina_map_find's own comment) -- so this can't just emit a
        raw GEP loop the way _release_fn_for_array does. Instead it
        reuses festina_map_for_each's own existing iteration (the exact
        mechanism .forEach() itself already uses), passing a small
        release-flavored trampoline (_emit_map_value_release_trampoline)
        that converts each entry's raw i64 value back to a `ptr` and
        releases it, in place of a user callback -- then, once every
        value is released, defers to festina_map_free_entries for the
        keys/entries buffer itself (identical to what
        festina_release_map's own C implementation already does) and
        frees the header.

        claude.md #175: the plain-`@festina_release_map` fast path
        just below reads entries/capacity (offsets 8/16) and frees the
        whole allocation by its base pointer, which correctly reclaims
        the trailing tombstones field along with everything else."""
        value_type = type_.value
        if not (_is_refcounted(value_type)
                or value_type == TEXT):
            return "@festina_release_map"
        key = types_mod.type_name(type_)
        if key in self._map_release_fns:
            return self._map_release_fns[key]
        fn_name = f"@__festina_release_map_{self._unique()}"
        self._map_release_fns[key] = fn_name
        trampoline_name = self._emit_map_value_release_trampoline(value_type)
        cyclic = self._is_cyclic_type(type_)
        body = [f"define void {fn_name}(ptr %payload) {{", "entry:"]
        should_free = self.tmp()
        body.append(f"  {should_free} = call i8 @festina_release_check(ptr %payload)")
        cond = self.tmp()
        body.append(f"  {cond} = icmp ne i8 {should_free}, 0")
        free_label = self.label("relmap.free")
        done_label = self.label("relmap.done")
        # claude.md #120: same trial-on-survival branch as the struct
        # and array wrappers, for a map[T] whose T sits on a cycle.
        alive_label = self.label("relmap.alive") if cyclic else done_label
        body.append(f"  br i1 {cond}, label %{free_label}, label %{alive_label}")
        body.append(f"{free_label}:")
        entries_field_ptr = self.tmp()
        body.append(f"  {entries_field_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr %payload, i32 0, i32 1")
        entries_ptr = self.tmp()
        body.append(f"  {entries_ptr} = load ptr, ptr {entries_field_ptr}")
        cap_ptr = self.tmp()
        body.append(f"  {cap_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr %payload, i32 0, i32 2")
        cap_val = self.tmp()
        body.append(f"  {cap_val} = load i64, ptr {cap_ptr}")
        body.append(f"  call void @festina_map_for_each(ptr {entries_ptr}, i64 {cap_val}, ptr {trampoline_name})")
        body.append(f"  call void @festina_map_free_entries(ptr {entries_ptr}, i64 {cap_val})")
        header = self.tmp()
        body.append(f"  {header} = getelementptr i8, ptr %payload, i64 -8")
        body.append(f"  call void @free(ptr {header})")
        body.append(f"  br label %{done_label}")
        if cyclic:
            self._emit_cycle_trial(body, type_, alive_label, done_label)
        body.append(f"{done_label}:")
        body.append("  ret void")
        body.append("}")
        body.append("")
        self.func_defs.extend(body)
        return fn_name

    def _release_fn_for_enum(self, type_):
        """claude.md #176: the enum counterpart to _release_fn_for_struct/
        _release_fn_for_map -- returns the LLVM function name to call to
        release a value of enum type `type_`, generating (once per enum
        name, cached in self._enum_release_fns) whichever of the two
        shapes below this specific enum needs.

        Pure-struct case: the enum value already IS its current
        member's own struct pointer -- self-tagged, no separate
        allocation of its own (see _emit_fresh_heap_header's own
        comment) -- so there is nothing here for THIS function to
        release directly. Reads the tag at payload-16 and dispatches
        straight to the matched member's own already-correct
        _release_fn_for(member_type), which already does its own
        festina_release_check/field-cascade/free at the right offset.
        No duplicate release-check needed, or possible -- this
        function's own job is purely "which member is this," not
        "should this be freed."

        Mixed case: the enum value is its OWN independent heap
        allocation (FESTINA_ENUM_BOX_LLVM_TYPE's {tag, value} payload,
        the ordinary refcount-header-immediately-before-payload
        convention every other generated wrapper in this file already
        uses) -- a real release-check-then-free this function IS
        responsible for, same shape _release_fn_for_map's own wrapper
        uses. Releases the inner value first (dispatched by tag, same
        matching as the pure-struct case) when that member's own type
        needs it, then frees the box.

        claude.md #120 scope note: an enum-typed edge is NOT walked by
        the cycle collector (_managed_type_children has no EnumType
        case) -- a genuine reference cycle routed THROUGH an enum-typed
        field would leak rather than being collected. Ordinary
        (acyclic) release is completely unaffected by this; only the
        trial-deletion cycle-breaking machinery doesn't extend through
        an enum edge yet. A narrow, stated scope cut (see todo.md),
        not a silently dropped one -- the identical category of gap
        this project already accepts elsewhere (e.g. the table-row-off-
        an-array leak), not a new kind of risk."""
        info = self.enums[type_.name]
        if type_.name in self._enum_release_fns:
            return self._enum_release_fns[type_.name]
        fn_name = f"@__festina_release_enum_{type_.name}"
        self._enum_release_fns[type_.name] = fn_name
        body = [f"define void {fn_name}(ptr %payload) {{", "entry:"]
        if info.is_pure_struct:
            # claude.md #176: an enum-typed value defaults to null until
            # assigned (no auto-vivify -- see analyze_enum's own scope-
            # cut note), and this wrapper is called unconditionally on
            # every reassignment/scope-exit release site the same way
            # every other release function is (see
            # _emit_global_retain_release's own "always safe to call
            # unconditionally" comment) -- so, exactly like
            # festina_release_check itself, this must check for null
            # BEFORE ever reading payload-16, not just before freeing.
            # The mixed-representation branch below needs no matching
            # guard: it already routes its only payload dereferences
            # through festina_release_check(payload) first, which is
            # null-safe on its own (see its own doc comment).
            null_check = self.tmp()
            body.append(f"  {null_check} = icmp eq ptr %payload, null")
            null_label = self.label("relenum.null")
            nonnull_label = self.label("relenum.nonnull")
            body.append(f"  br i1 {null_check}, label %{null_label}, label %{nonnull_label}")
            body.append(f"{null_label}:")
            body.append("  ret void")
            body.append(f"{nonnull_label}:")
            tag_ptr = self.tmp()
            body.append(f"  {tag_ptr} = getelementptr i8, ptr %payload, i64 -16")
            tag = self.tmp()
            body.append(f"  {tag} = load ptr, ptr {tag_ptr}")
            for i, member in enumerate(info.members):
                const = self._enum_tag_const(member)
                match_label = self.label(f"relenum.match{i}")
                cont_label = self.label(f"relenum.cont{i}")
                cmp = self.tmp()
                body.append(f"  {cmp} = icmp eq ptr {tag}, {const}")
                body.append(f"  br i1 {cmp}, label %{match_label}, label %{cont_label}")
                body.append(f"{match_label}:")
                body.append(f"  call void {self._release_fn_for(member)}(ptr %payload)")
                body.append("  ret void")
                body.append(f"{cont_label}:")
            # Unreachable in a correct program -- construction (see
            # _coerce's own enum branch) always writes a valid member
            # tag, and no member is ever added or removed after
            # analyze_enum resolves this enum's own member list.
            body.append("  unreachable")
        else:
            should_free = self.tmp()
            body.append(f"  {should_free} = call i8 @festina_release_check(ptr %payload)")
            cond = self.tmp()
            body.append(f"  {cond} = icmp ne i8 {should_free}, 0")
            free_label = self.label("relenum.free")
            done_label = self.label("relenum.done")
            body.append(f"  br i1 {cond}, label %{free_label}, label %{done_label}")
            body.append(f"{free_label}:")
            tag_ptr = self.tmp()
            body.append(f"  {tag_ptr} = getelementptr {FESTINA_ENUM_BOX_LLVM_TYPE}, ptr %payload, i32 0, i32 0")
            tag = self.tmp()
            body.append(f"  {tag} = load ptr, ptr {tag_ptr}")
            val_ptr = self.tmp()
            body.append(f"  {val_ptr} = getelementptr {FESTINA_ENUM_BOX_LLVM_TYPE}, ptr %payload, i32 0, i32 1")
            raw_val = self.tmp()
            body.append(f"  {raw_val} = load i64, ptr {val_ptr}")
            for i, member in enumerate(info.members):
                if not (_is_refcounted(member) or member == TEXT):
                    continue
                const = self._enum_tag_const(member)
                match_label = self.label(f"relenum.vmatch{i}")
                cont_label = self.label(f"relenum.vcont{i}")
                cmp = self.tmp()
                body.append(f"  {cmp} = icmp eq ptr {tag}, {const}")
                body.append(f"  br i1 {cmp}, label %{match_label}, label %{cont_label}")
                body.append(f"{match_label}:")
                inner = self._i64_to_map_value(raw_val, member, body)
                if member == TEXT:
                    body.append(f"  call void @free(ptr {inner})")
                else:
                    body.append(f"  call void {self._release_fn_for(member)}(ptr {inner})")
                body.append(f"  br label %{cont_label}")
                body.append(f"{cont_label}:")
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

    def _emit_map_value_release_trampoline(self, value_type):
        """claude.md #80: generates a small function matching
        festina_map_for_each's own fixed callback signature
        (`void(i64, ptr)` -- see _emit_map_foreach_trampoline's own
        comment on why a real, correctly-typed function is needed
        rather than relying on ABI coincidence, though here both sides
        of the mismatch this guards against for .forEach() don't apply
        -- this trampoline's OWN signature already matches exactly,
        it's just discarding the key and reinterpreting the raw i64
        value), releasing it via `value_type`'s own release function.
        Never cached/reused across value types the way
        _release_fn_for_array/_map's own wrappers are -- always freshly
        generated per call, since it's only ever needed once, at the
        one call site inside _release_fn_for_map that generates it."""
        uid = self._unique()
        trampoline_name = f"@__festina_maprelease_{uid}"
        release_fn = self._release_fn_for(value_type)
        body = [f"define void {trampoline_name}(i64 %raw, ptr %key) {{", "entry:"]
        ptr_val = self.tmp()
        body.append(f"  {ptr_val} = inttoptr i64 %raw to ptr")
        body.append(f"  call void {release_fn}(ptr {ptr_val})")
        body.append("  ret void")
        body.append("}")
        self.func_defs.extend(body)
        self.func_defs.append("")
        return trampoline_name

    def _emit_exec_callback_trampoline(self):
        """claude.md #177: bridges festina_async_io_dispatch's fixed
        `void(*)(void*)` callback ABI to exec(args, callback)'s real
        `func[int]:void` value -- the one wrinkle blob/img/aud's own
        `.callback()` never had, since THEIR callback value is already
        `func[T]:void` for a pointer-shaped T (an img/aud/blob handle),
        which already IS `void(ptr)`-shaped and needs no adapter at
        all. exec()'s result is a plain `int` (i64), so calling the
        real callback needs an actual bridge, not just a cast.

        Unlike _emit_map_value_release_trampoline just above (fresh
        per call site, hardcoding a fixed release-function SYMBOL known
        at codegen time), this trampoline cannot hardcode which
        function to call: exec(args, callback)'s own callback is
        checked the same permissive, structural way blob/img/aud's
        `.callback()` already is (semantic.py) -- any func[int]:void-
        typed EXPRESSION, which may be an ordinary runtime SSA value
        (a variable, a struct field, ...) that a separately-emitted
        top-level LLVM function could never reference directly. So
        instead this trampoline is entirely generic and DATA-driven:
        it reads both the exit code and the real callback pointer back
        out of the payload (FestinaExecPayload in runtime/
        festina_runtime.c -- `{ i64 exit_code; void(*user_callback)
        (i64); ... }`, a fixed two-field prefix both sides agree on)
        and makes an indirect call through the pointer it just loaded.
        That means ONE trampoline correctly serves every exec(args,
        callback) call site in the whole program, compile-time-
        constant callback or not -- generated once, lazily, and
        cached in self._exec_callback_trampoline."""
        if self._exec_callback_trampoline is not None:
            return self._exec_callback_trampoline
        uid = self._unique()
        trampoline_name = f"@__festina_exec_callback_{uid}"
        body = [f"define void {trampoline_name}(ptr %payload) {{", "entry:"]
        exit_code = self.tmp()
        body.append(f"  {exit_code} = load i64, ptr %payload")
        cb_slot = self.tmp()
        body.append(
            f"  {cb_slot} = getelementptr {{i64, ptr}}, ptr %payload, i32 0, i32 1")
        cb_val = self.tmp()
        body.append(f"  {cb_val} = load ptr, ptr {cb_slot}")
        body.append(f"  call void {cb_val}(i64 {exit_code})")
        body.append("  ret void")
        body.append("}")
        self.func_defs.extend(body)
        self.func_defs.append("")
        self._exec_callback_trampoline = trampoline_name
        return trampoline_name

    def _emit_sort_comparator_trampoline(self, elem_type):
        """claude.md #184 (uraikus/festina#76 item 2): bridges
        festina_array_sort's own `int(*)(const void*, const void*,
        void*)` comparator ABI -- given a real userdata slot from the
        start, unlike plain C qsort(), specifically so this trampoline
        would never need the process-global-variable workaround a
        userdata-less comparator would otherwise force -- to a real
        `func[T,T]:int` Festina value.

        Unlike _emit_exec_callback_trampoline just above, `userdata`
        IS the callback pointer itself, not a payload struct to read
        one back out of: .sort()'s comparator argument is evaluated
        once, at the call site, into an ordinary `ptr` value (first-
        class function values already ARE bare pointers, claude.md
        #141), and festina_array_sort hands that same pointer back on
        every single comparison unchanged, so there's nothing else to
        carry alongside it.

        Cached per element type (types_mod.type_name(elem_type), the
        same keying _array_release_fns already uses) rather than
        shared like _exec_callback_trampoline's one symbol, because
        decoding the two raw comparison slots needs THIS type's own
        LLVM type -- an i64 slot decodes differently than an i8 or
        double or ptr one, and the indirect call's own argument types
        have to match the real comparator's signature exactly."""
        key = types_mod.type_name(elem_type)
        if key in self._sort_trampolines:
            return self._sort_trampolines[key]
        elem_ir = _llvm_type(elem_type)
        uid = self._unique()
        trampoline_name = f"@__festina_sortcmp_{uid}"
        body = [f"define i32 {trampoline_name}(ptr %a, ptr %b, ptr %userdata) {{", "entry:"]
        av = self.tmp()
        body.append(f"  {av} = load {elem_ir}, ptr %a")
        bv = self.tmp()
        body.append(f"  {bv} = load {elem_ir}, ptr %b")
        r = self.tmp()
        body.append(f"  {r} = call i64 %userdata({elem_ir} {av}, {elem_ir} {bv})")
        r32 = self.tmp()
        body.append(f"  {r32} = trunc i64 {r} to i32")
        body.append(f"  ret i32 {r32}")
        body.append("}")
        self.func_defs.extend(body)
        self.func_defs.append("")
        self._sort_trampolines[key] = trampoline_name
        return trampoline_name

    # ---- cycle collection -- claude.md #120 ----

    def _is_cyclic_type(self, t):
        """claude.md #120: whether values of `t` can participate in a
        reference cycle -- i.e. whether `t` can reach itself through
        managed edges (struct fields, arr elements, map values). This
        is the single gate on all cycle-collection machinery: an
        acyclic type's releases never run a trial and never generate a
        traversal function, so a program with no self-referencing
        types pays literally nothing for the collector's existence.
        Purely a property of the declared TYPE GRAPH, so it is
        computed once per type name and cached."""
        if not isinstance(t, (types_mod.StructType, types_mod.ArrayType,
                              types_mod.MapType)):
            return False
        key = types_mod.type_name(t)
        cached = self._cyclic_type_cache.get(key)
        if cached is not None:
            return cached
        result = False
        seen = set()
        frontier = list(self._managed_type_children(t))
        while frontier:
            child = frontier.pop()
            ckey = types_mod.type_name(child)
            if ckey == key:
                result = True
                break
            if ckey in seen:
                continue
            seen.add(ckey)
            frontier.extend(self._managed_type_children(child))
        self._cyclic_type_cache[key] = result
        return result

    def _managed_type_children(self, t):
        """The type-graph edges _is_cyclic_type walks: a struct's
        struct/arr/map-typed fields, a container's element/value type
        when it is one of those three. Leaf types (text, blob, img,
        aud, regex, scalars) can never sit ON a cycle -- none of them
        holds a reference to another managed value -- so they have no
        outgoing edges here."""
        kinds = (types_mod.StructType, types_mod.ArrayType, types_mod.MapType)
        if isinstance(t, types_mod.StructType):
            return [ft for _, ft in self.struct_fields(t.name)
                    if isinstance(ft, kinds)]
        if isinstance(t, types_mod.ArrayType):
            return [t.element] if isinstance(t.element, kinds) else []
        if isinstance(t, types_mod.MapType):
            return [t.value] if isinstance(t.value, kinds) else []
        return []

    def _cycle_fn(self, op, type_):
        """claude.md #120: the generated per-type traversal functions
        that drive a trial deletion -- `gray` (tentatively remove every
        edge internal to the subgraph), `scan` (decide survival by the
        counts that remain), `black` (restore a surviving region's
        counts), `white` (free the garbage region) -- plus the
        `grayedge`/`blackedge` per-element helpers container traversals
        hand to festina_cycle_visit_array/_map. Registered before
        generated, exactly like the release wrappers (claude.md #106's
        load-bearing cache write), so a self-referencing type's
        traversal calls itself instead of recursing the compiler."""
        key = (op, types_mod.type_name(type_))
        if key in self._cycle_fns:
            return self._cycle_fns[key]
        fn_name = f"@__festina_cycle_{op}_{self._unique()}"
        self._cycle_fns[key] = fn_name
        if op == "grayedge":
            body = [f"define void {fn_name}(ptr %c) {{", "entry:"]
            body.append("  call void @festina_cycle_dec(ptr %c)")
            body.append(f"  call void {self._cycle_fn('gray', type_)}(ptr %c)")
            body.append("  ret void")
            body.append("}")
            body.append("")
        elif op == "blackedge":
            rec_label = self.label("cyedge.rec")
            done_label = self.label("cyedge.done")
            nb = self.tmp()
            cc = self.tmp()
            body = [f"define void {fn_name}(ptr %c) {{", "entry:"]
            body.append("  call void @festina_cycle_inc(ptr %c)")
            body.append(f"  {nb} = call i8 @festina_cycle_needs_black(ptr %c)")
            body.append(f"  {cc} = icmp ne i8 {nb}, 0")
            body.append(f"  br i1 {cc}, label %{rec_label}, label %{done_label}")
            body.append(f"{rec_label}:")
            body.append(f"  call void {self._cycle_fn('black', type_)}(ptr %c)")
            body.append(f"  br label %{done_label}")
            body.append(f"{done_label}:")
            body.append("  ret void")
            body.append("}")
            body.append("")
        elif isinstance(type_, types_mod.StructType):
            body = self._cycle_struct_body(op, type_, fn_name)
        else:
            body = self._cycle_container_body(op, type_, fn_name)
        self.func_defs.extend(body)
        return fn_name

    def _cycle_struct_children(self, type_):
        """(index, field_type) for every field a trial traverses --
        exactly the cyclic-typed ones. Everything else the struct owns
        (text buffers, blobs, acyclic containers, ...) is handled by
        `white`'s disposal instead, released through the ordinary
        machinery, because it provably is not part of any cycle and
        its counts were never touched by the trial."""
        return [(i, ftype)
                for i, (_, ftype) in enumerate(self.struct_fields(type_.name))
                if self._is_cyclic_type(ftype)]

    def _cycle_struct_body(self, op, type_, fn_name):
        struct_ty = self.struct_llvm_name(type_.name)
        children = self._cycle_struct_children(type_)

        def load_field(body, i):
            fptr = self.tmp()
            body.append(f"  {fptr} = getelementptr {struct_ty}, ptr %p, i32 0, i32 {i}")
            fval = self.tmp()
            body.append(f"  {fval} = load ptr, ptr {fptr}")
            return fval

        body = [f"define void {fn_name}(ptr %p) {{", "entry:"]
        if op == "gray":
            go = self.tmp()
            cond = self.tmp()
            walk = self.label("cygray.walk")
            done = self.label("cygray.done")
            body.append(f"  {go} = call i8 @festina_cycle_begin_gray(ptr %p)")
            body.append(f"  {cond} = icmp ne i8 {go}, 0")
            body.append(f"  br i1 {cond}, label %{walk}, label %{done}")
            body.append(f"{walk}:")
            for i, ftype in children:
                fval = load_field(body, i)
                body.append(f"  call void @festina_cycle_dec(ptr {fval})")
                body.append(f"  call void {self._cycle_fn('gray', ftype)}(ptr {fval})")
            body.append(f"  br label %{done}")
            body.append(f"{done}:")
        elif op == "scan":
            r = self.tmp()
            is1 = self.tmp()
            blackl = self.label("cyscan.black")
            chk2 = self.label("cyscan.chk2")
            walk = self.label("cyscan.walk")
            done = self.label("cyscan.done")
            body.append(f"  {r} = call i64 @festina_cycle_begin_scan(ptr %p)")
            body.append(f"  {is1} = icmp eq i64 {r}, 1")
            body.append(f"  br i1 {is1}, label %{blackl}, label %{chk2}")
            body.append(f"{blackl}:")
            body.append(f"  call void {self._cycle_fn('black', type_)}(ptr %p)")
            body.append(f"  br label %{done}")
            body.append(f"{chk2}:")
            is2 = self.tmp()
            body.append(f"  {is2} = icmp eq i64 {r}, 2")
            body.append(f"  br i1 {is2}, label %{walk}, label %{done}")
            body.append(f"{walk}:")
            for i, ftype in children:
                fval = load_field(body, i)
                body.append(f"  call void {self._cycle_fn('scan', ftype)}(ptr {fval})")
            body.append(f"  br label %{done}")
            body.append(f"{done}:")
        elif op == "black":
            body.append("  call void @festina_cycle_set_black(ptr %p)")
            for i, ftype in children:
                fval = load_field(body, i)
                body.append(f"  call void @festina_cycle_inc(ptr {fval})")
                nb = self.tmp()
                cc = self.tmp()
                rec = self.label("cyblack.rec")
                nxt = self.label("cyblack.next")
                body.append(f"  {nb} = call i8 @festina_cycle_needs_black(ptr {fval})")
                body.append(f"  {cc} = icmp ne i8 {nb}, 0")
                body.append(f"  br i1 {cc}, label %{rec}, label %{nxt}")
                body.append(f"{rec}:")
                body.append(f"  call void {self._cycle_fn('black', ftype)}(ptr {fval})")
                body.append(f"  br label %{nxt}")
                body.append(f"{nxt}:")
        else:  # white
            go = self.tmp()
            cond = self.tmp()
            walk = self.label("cywhite.walk")
            done = self.label("cywhite.done")
            body.append(f"  {go} = call i8 @festina_cycle_begin_white(ptr %p)")
            body.append(f"  {cond} = icmp ne i8 {go}, 0")
            body.append(f"  br i1 {cond}, label %{walk}, label %{done}")
            body.append(f"{walk}:")
            for i, ftype in children:
                fval = load_field(body, i)
                body.append(f"  call void {self._cycle_fn('white', ftype)}(ptr {fval})")
            # Dispose: everything the node owns that the trial did NOT
            # traverse -- ordinary releases, whose counts the trial
            # never altered. Cyclic children are NOT released here:
            # markGray already removed those counts and the white
            # recursion above frees whichever of them are garbage.
            for i, (_, ftype) in enumerate(self.struct_fields(type_.name)):
                if self._is_cyclic_type(ftype):
                    continue
                if _is_refcounted(ftype):
                    fval = load_field(body, i)
                    body.append(f"  call void {self._release_fn_for(ftype)}(ptr {fval})")
                elif ftype == TEXT:
                    fval = load_field(body, i)
                    body.append(f"  call void @free(ptr {fval})")
            # claude.md #176: same tagged-struct offset correction as
            # _release_fn_for_struct's own free path -- a tagged
            # struct's true allocation base sits 16 bytes back, not 8.
            hdr_offset = -16 if type_.name in self._tagged_structs else -8
            hdr = self.tmp()
            body.append(f"  {hdr} = getelementptr i8, ptr %p, i64 {hdr_offset}")
            body.append(f"  call void @free(ptr {hdr})")
            body.append(f"  br label %{done}")
            body.append(f"{done}:")
        body.append("  ret void")
        body.append("}")
        body.append("")
        return body

    def _cycle_container_body(self, op, type_, fn_name):
        """arr[T]/map[T] with a cyclic T: same four operations, with
        the per-element loop delegated to festina_cycle_visit_array/
        _map handing each element pointer to the element type's own
        function (or a grayedge/blackedge helper where the edge has
        count work of its own). Disposal is the runtime's
        festina_cycle_dispose_* -- the container's buffers and keys,
        never its elements."""
        is_map = isinstance(type_, types_mod.MapType)
        elem = type_.value if is_map else type_.element
        visit = "@festina_cycle_visit_map" if is_map else "@festina_cycle_visit_array"
        dispose = ("@festina_cycle_dispose_map" if is_map
                   else "@festina_cycle_dispose_array")
        body = [f"define void {fn_name}(ptr %p) {{", "entry:"]
        if op == "gray":
            go = self.tmp()
            cond = self.tmp()
            walk = self.label("cygray.walk")
            done = self.label("cygray.done")
            body.append(f"  {go} = call i8 @festina_cycle_begin_gray(ptr %p)")
            body.append(f"  {cond} = icmp ne i8 {go}, 0")
            body.append(f"  br i1 {cond}, label %{walk}, label %{done}")
            body.append(f"{walk}:")
            body.append(f"  call void {visit}(ptr %p, "
                        f"ptr {self._cycle_fn('grayedge', elem)})")
            body.append(f"  br label %{done}")
            body.append(f"{done}:")
        elif op == "scan":
            r = self.tmp()
            is1 = self.tmp()
            blackl = self.label("cyscan.black")
            chk2 = self.label("cyscan.chk2")
            walk = self.label("cyscan.walk")
            done = self.label("cyscan.done")
            body.append(f"  {r} = call i64 @festina_cycle_begin_scan(ptr %p)")
            body.append(f"  {is1} = icmp eq i64 {r}, 1")
            body.append(f"  br i1 {is1}, label %{blackl}, label %{chk2}")
            body.append(f"{blackl}:")
            body.append(f"  call void {self._cycle_fn('black', type_)}(ptr %p)")
            body.append(f"  br label %{done}")
            body.append(f"{chk2}:")
            is2 = self.tmp()
            body.append(f"  {is2} = icmp eq i64 {r}, 2")
            body.append(f"  br i1 {is2}, label %{walk}, label %{done}")
            body.append(f"{walk}:")
            body.append(f"  call void {visit}(ptr %p, "
                        f"ptr {self._cycle_fn('scan', elem)})")
            body.append(f"  br label %{done}")
            body.append(f"{done}:")
        elif op == "black":
            body.append("  call void @festina_cycle_set_black(ptr %p)")
            body.append(f"  call void {visit}(ptr %p, "
                        f"ptr {self._cycle_fn('blackedge', elem)})")
        else:  # white
            go = self.tmp()
            cond = self.tmp()
            walk = self.label("cywhite.walk")
            done = self.label("cywhite.done")
            body.append(f"  {go} = call i8 @festina_cycle_begin_white(ptr %p)")
            body.append(f"  {cond} = icmp ne i8 {go}, 0")
            body.append(f"  br i1 {cond}, label %{walk}, label %{done}")
            body.append(f"{walk}:")
            body.append(f"  call void {visit}(ptr %p, "
                        f"ptr {self._cycle_fn('white', elem)})")
            body.append(f"  call void {dispose}(ptr %p)")
            body.append(f"  br label %{done}")
            body.append(f"{done}:")
        body.append("  ret void")
        body.append("}")
        body.append("")
        return body

    def _emit_cycle_trial(self, body, type_, alive_label, done_label):
        """The still-referenced branch of a cyclic type's release
        wrapper: when the released value remains at a positive count,
        try it as a cycle root -- markGray / scan / collectWhite, the
        classic synchronous trial. A candidate check keeps the trial
        off null and immortal values; everything else is at worst
        wasted work (an externally-reachable subgraph scans black and
        comes out exactly as it went in), never corruption."""
        body.append(f"{alive_label}:")
        cand = self.tmp()
        cc = self.tmp()
        trial = self.label("reltrial.run")
        body.append(f"  {cand} = call i8 @festina_cycle_candidate(ptr %payload)")
        body.append(f"  {cc} = icmp ne i8 {cand}, 0")
        body.append(f"  br i1 {cc}, label %{trial}, label %{done_label}")
        body.append(f"{trial}:")
        body.append(f"  call void {self._cycle_fn('gray', type_)}(ptr %payload)")
        body.append(f"  call void {self._cycle_fn('scan', type_)}(ptr %payload)")
        body.append(f"  call void {self._cycle_fn('white', type_)}(ptr %payload)")
        body.append(f"  br label %{done_label}")

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
            val = self._coerce(val, vtype, ttype, lines, source_expr=expr.value)
            if _is_refcounted(ttype):
                if ref.startswith("@"):
                    val = self._emit_global_retain_release(ref, val, ttype, lines,
                                                          expr.value, source_type=vtype)
                else:
                    self._emit_local_retain_release(ref, val, expr.value, ttype, lines,
                                                    source_type=vtype)
            elif ttype == TEXT:
                if ref.startswith("@"):
                    val = self._emit_global_retain_release(ref, val, ttype, lines, expr.value)
                else:
                    val = self._emit_local_text_own_release(ref, val, expr.value, lines)
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
                    val = self._coerce(val, vtype, obj_type.value, lines,
                                       source_expr=expr.value)
                    self._emit_map_set(obj_val, obj_type.value, key_val, val, expr.value, lines,
                                        key_source_expr=expr.target.prop,
                                        value_pre_coerce_type=vtype)
                    return val, obj_type.value
                if not isinstance(obj_type, types_mod.ArrayType):
                    raise CodegenError(f"cannot index into {types_mod.type_name(obj_type)}",
                                        file=self.filename, line=getattr(expr.target, "line", 0))
                idx_val, _ = self._emit_expr(expr.target.prop, env, lines)
                ptr, ftype = self._array_elem_ptr(obj_val, obj_type, idx_val, lines)
            else:
                ptr, ftype = self._member_ptr(expr.target, env, lines)
            val, vtype = self._emit_value_for(expr.value, env, lines, ftype)
            val = self._coerce(val, vtype, ftype, lines, source_expr=expr.value)
            if _is_refcounted(ftype):
                # claude.md #78 (widened by claude.md #79 to arr[T]/
                # map[T]-typed fields, and claude.md #80 to arr[T]
                # ELEMENTS too): `outer.field = value` / `arr[i] = value`
                # -- the ONLY way a struct/array/map-typed field or
                # array element is ever populated (there's no struct/
                # array/map-literal-as-field/element-initializer syntax)
                # -- gets the exact same owning/aliasing retain rule as
                # a plain local reassignment, and releases whatever the
                # field/element previously held (always safe: a field
                # starts out null, per its zeroinitializer/calloc'd
                # storage; an array element always holds a real,
                # previously-retained value by the time this runs --
                # every index was already written once, in
                # _emit_array_lit's own construction, before any later
                # `arr[i] = v` could ever reach it -- and every release
                # function already null-checks regardless). `map[key] =
                # v` is handled separately, inside _emit_map_set, since
                # it needs a key-based lookup (not a fixed address) to
                # find whatever value it's overwriting, if any -- by the
                # time computed-Member reaches this shared code with
                # `ftype` set, it's already provably the array-element
                # case (the map one returns earlier, above).
                # claude.md #118: the freshness test (not the bare
                # owning-source one) so a text initializer coerced into
                # a blob/img/aud field -- a fresh handle from _coerce's
                # own load call -- is not retained a second time.
                #
                # claude.md #120: the NEW value is stored BEFORE the old
                # one is released -- load-retain-store-release, not
                # load-retain-release-store. With cycle trials, a
                # release may traverse the object graph, and a field
                # still physically pointing at a value whose reference
                # count this very release just removed would be
                # double-counted by markGray -- enough to whiten (and
                # free) a value a real external reference still holds.
                # Storing first keeps the graph the trial walks
                # consistent with the counts at every release.
                old = self.tmp()
                lines.append(f"  {old} = load ptr, ptr {ptr}")
                if not self._refcounted_source_is_fresh(expr.value, vtype, ftype):
                    lines.append(f"  call void @festina_retain(ptr {val})")
                lines.append(f"  store {_llvm_type(ftype)} {val}, ptr {ptr}")
                lines.append(f"  call void {self._release_fn_for(ftype)}(ptr {old})")
                return val, ftype
            elif ftype == TEXT:
                # claude.md #83: the text counterpart to the block just
                # above -- copies (via festina_text_own) rather than
                # retains, frees (via a plain @free) rather than
                # releases, but otherwise the identical "overwrite one
                # binding's worth of a fixed-address slot" shape a
                # struct field write and array element write already
                # share. A field starts out null (zeroinitializer/
                # calloc'd storage); an array element always holds a
                # real value by the time this runs (_emit_array_lit's
                # own construction already wrote every index once) --
                # either way @free is always safe (NULL-safe, and every
                # non-null value here is, by claude.md #83's own
                # invariant, always an exclusively-owned heap buffer).
                old = self.tmp()
                lines.append(f"  {old} = load ptr, ptr {ptr}")
                if not self._is_owning_text_source(expr.value):
                    owned = self.tmp()
                    lines.append(f"  {owned} = call ptr @festina_text_own(ptr {val})")
                    val = owned
                lines.append(f"  call void @free(ptr {old})")
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
        cons_val = self._own_ternary_branch(cons_val, cons_type, expr.cons, lines)
        then_pred = self.cur_block  # may differ from then_label if expr.cons had its own branches
        lines.append(f"  br label %{end_label}")

        self._start_block(else_label, lines)
        alt_val, _ = self._emit_expr(expr.alt, env, lines)
        alt_val = self._own_ternary_branch(alt_val, cons_type, expr.alt, lines)
        else_pred = self.cur_block
        lines.append(f"  br label %{end_label}")

        self._start_block(end_label, lines)
        out = self.tmp()
        llvm_ty = _llvm_type(cons_type)
        lines.append(f"  {out} = phi {llvm_ty} [ {cons_val}, %{then_pred} ], [ {alt_val}, %{else_pred} ]")
        return out, cons_type

    def _own_ternary_branch(self, val, vtype, source_expr, lines):
        """claude.md #173: a Ternary used to be treated as "aliasing"
        no matter what its own branches actually were (see
        _is_owning_text_source/_is_owning_refcounted_source's own prior
        comments on Ternary) -- correct whenever BOTH branches are
        themselves aliasing, but silently wrong the moment either one
        is a genuinely fresh, owning source (a template literal, a `+`
        concatenation, a function call, an array/map literal, ...):
        the caller retained/copied the ternary's overall result exactly
        once no matter which branch actually ran, so a fresh branch's
        own already-correct ownership got an EXTRA retain/copy with
        nothing left to ever balance it -- an unconditional, silent
        leak on every evaluation that took the fresh branch (found via
        real ASan/LeakSanitizer runs, not by inspection -- see
        tests/stress/json_parse_churn.f's own account of finding it).

        Fixed at the root, in EACH branch, rather than at every one of
        this ternary's own possible consumers: normalize a branch that
        wasn't already owning into one that is (retain a refcounted
        branch, copy a text branch) right here, so the phi'd result is
        ALWAYS owning regardless of which branch ran -- which is
        exactly what lets Ternary be added to both predicates' owning
        cases below, the same "the whole point is never having to fix
        this at each consumer separately" reasoning every earlier
        ownership-tracking stage in this file already leans on. A
        non-text, non-refcounted type (int/float/bool/color/font/...)
        is returned completely unchanged -- neither of those two
        protocols applies to it at all."""
        if vtype == TEXT:
            if not self._is_owning_text_source(source_expr):
                owned = self.tmp()
                lines.append(f"  {owned} = call ptr @festina_text_own(ptr {val})")
                return owned
        elif _is_refcounted(vtype):
            if not self._is_owning_refcounted_source(source_expr):
                lines.append(f"  call void @festina_retain(ptr {val})")
        return val

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

    def _emit_float_to_int(self, val, lines):
        """claude.md #102: the rounding four (Math.floor/ceil/round/
        trunc) converting a double to an i64, without the undefined
        behaviour a bare `fptosi` has.

        `fptosi` is UB in LLVM for a NaN, for an infinity, and for
        anything outside i64's range -- not "some unspecified integer",
        genuinely undefined, and it behaves like it. Measured before
        this: `Math.floor(1.0 / 0.0)` printed a different value on every
        build, once a stack ADDRESS; and in one program
        `Math.floor(nan)` answered 1 while `Math.ceil(nan)` on the very
        next line answered the int-null sentinel, because the optimizer
        had folded two identical UB sites differently. A language whose
        stated position is that division by zero returns null rather
        than crashing cannot then hand back a stack address for
        Math.floor of that same null.

        The answer is null in all three cases, which is the same claim
        claude.md #57 already makes for division by zero: there is no
        meaningful integer here. Saturating instead would be worse, not
        better -- i64's minimum IS the int null sentinel, so clamping a
        huge negative float would land on it anyway and clamping a huge
        positive one would assert a precise answer the input never had.

        llvm.fptosi.sat does the conversion itself, which is fully
        defined (NaN -> 0, out of range -> clamped) and so leaves no
        poison anywhere for the select below to inherit -- ordering it
        the other way, fptosi first and select afterward, would still be
        UB, since a select cannot launder a poisoned operand."""
        nan = self.tmp()
        lines.append(f"  {nan} = fcmp uno double {val}, {val}")
        # 2^63 and -2^63 exactly, as hex doubles: the first double at or
        # beyond i64's positive limit, and i64's exact negative limit.
        too_big = self.tmp()
        lines.append(f"  {too_big} = fcmp oge double {val}, 0x43E0000000000000")
        too_small = self.tmp()
        lines.append(f"  {too_small} = fcmp olt double {val}, 0xC3E0000000000000")
        out_of_range = self.tmp()
        lines.append(f"  {out_of_range} = or i1 {too_big}, {too_small}")
        unusable = self.tmp()
        lines.append(f"  {unusable} = or i1 {nan}, {out_of_range}")
        converted = self.tmp()
        lines.append(f"  {converted} = call i64 @llvm.fptosi.sat.i64.f64(double {val})")
        out = self.tmp()
        lines.append(f"  {out} = select i1 {unusable}, i64 {INT_NULL_CONST}, i64 {converted}")
        return out

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
                    result = neg
                else:
                    result = out
                # claude.md #97: an operand that allocated its own buffer
                # is consumed here and nothing downstream can reach it --
                # a bool is not a text reference, so this is the last
                # chance to free it. `f() == g()` leaked both results
                # before this.
                self._free_text_temp(expr.left, left_val, left_type, lines)
                self._free_text_temp(expr.right, right_val, right_type, lines)
                return result, BOOL
            if expr.op == "+":
                out = self.tmp()
                lines.append(f"  {out} = call ptr @festina_str_concat(ptr {left_val}, ptr {right_val})")
                # claude.md #97: festina_str_concat COPIES from both
                # operands and keeps neither, so an operand that was
                # itself freshly allocated -- the `a + b` inside
                # `a + b + c`, a call result, a template -- is dead the
                # moment this returns. Freeing it here rather than at
                # the eventual binding site is what keeps a chained
                # concatenation from leaking one buffer per `+`, the
                # same fix _emit_template already applies to its own
                # intermediates (claude.md #83).
                self._free_text_temp(expr.left, left_val, left_type, lines)
                self._free_text_temp(expr.right, right_val, right_type, lines)
                return out, TEXT
            raise CodegenError(f"operator '{expr.op}' is not supported on text",
                                file=self.filename, line=expr.line)

        # claude.md #102: every pointer-backed type -- struct, arr[T],
        # map[T], a table row, img, aud, regex, blob -- compares against
        # `null` with a plain pointer icmp. Without this the numeric path
        # below emitted `icmp eq i64 <a ptr>, null`, which is not valid
        # IR at all: the compile died with an LLVM PARSE ERROR naming a
        # generated temporary, which is an internal-error message for
        # what is an entirely reasonable thing to write. `x == null` on
        # a struct-typed value, and `row.file == null` on a nullable
        # media column, are both ordinary.
        #
        # Only ==/!= and only against a genuine null: two struct values
        # compared with `==` stays unsupported (it would have to mean
        # either identity or deep equality, and claude.md picks neither
        # -- #54's ambiguity rule), and ordering operators are meaningless
        # on a handle.
        null_side = (isinstance(expr.right, ast.NullLit), isinstance(expr.left, ast.NullLit))
        if expr.op in ("==", "!=") and any(null_side):
            other = left_type if null_side[0] else right_type
            if _llvm_type(other) == "ptr" if other is not None else False:
                value = left_val if null_side[0] else right_val
                cmp_out = self.tmp()
                op = "eq" if expr.op == "==" else "ne"
                lines.append(f"  {cmp_out} = icmp {op} ptr {value}, null")
                out = self.tmp()
                lines.append(f"  {out} = zext i1 {cmp_out} to i8")
                return out, BOOL

        # claude.md #143: int and float now mix freely in any binary
        # operator -- superseded claude.md #55's old "never mix
        # directly" rule, and with it this branch's old job (rejecting
        # a mismatch semantic.py was supposed to have already caught).
        # A mismatched INT/FLOAT pair reaching here is now the ordinary,
        # expected case: whichever side is INT gets an implicit sitofp
        # (the exact same conversion instruction int.toFloat() itself
        # emits, see just below), "as though .toFloat() had been
        # written" -- the request's own framing for this whole feature.
        if left_type == INT and right_type == FLOAT:
            coerced = self.tmp()
            lines.append(f"  {coerced} = sitofp i64 {left_val} to double")
            left_val, left_type = coerced, FLOAT
        elif left_type == FLOAT and right_type == INT:
            coerced = self.tmp()
            lines.append(f"  {coerced} = sitofp i64 {right_val} to double")
            right_val, right_type = coerced, FLOAT
        use_float = left_type == FLOAT

        if expr.op == "/":
            # claude.md #143: division always returns float, even for
            # two ints -- coerce BOTH operands to float unconditionally
            # (the mixing coercion just above only fires when the two
            # operand types actually differ, which two ints never do)
            # before handing off to the identical _emit_divmod every
            # other float division already goes through.
            if not use_float:
                left_coerced = self.tmp()
                lines.append(f"  {left_coerced} = sitofp i64 {left_val} to double")
                right_coerced = self.tmp()
                lines.append(f"  {right_coerced} = sitofp i64 {right_val} to double")
                left_val, right_val = left_coerced, right_coerced
            out = self._emit_divmod(expr.op, left_val, right_val, True, lines)
            return out, FLOAT
        if expr.op == "%":
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

    def _emit_floor_div(self, a_val, b_val, lines):
        """claude.md #188 (uraikus/festina#76 item 1): Math.floorDiv(a,
        b) -- rounds toward NEGATIVE INFINITY, unlike `/`'s own `sdiv`
        (LLVM's -- and the hardware's -- integer division truncates
        toward ZERO). The two only disagree when the operands have
        different signs and the division isn't exact, e.g. -7 sdiv 2 is
        -3 (truncated), but floor(-7/2) is -4 -- the standard `sdiv`
        result minus one whenever the remainder is nonzero AND the
        remainder's sign differs from the divisor's (Python's `//`/
        Java's Math.floorDiv use the identical rule).

        Shares claude.md #57's own by-zero convention (returns null,
        via real control flow -- see _emit_divmod's own comment on why
        a `select` alone wouldn't do, `sdiv`/`srem` by zero being
        undefined behavior at the hardware level) rather than
        introducing a second one just for this function."""
        is_zero = self.tmp()
        lines.append(f"  {is_zero} = icmp eq i64 {b_val}, 0")

        zero_label = self.label("floordivzero")
        nonzero_label = self.label("floordivnonzero")
        end_label = self.label("floordivend")
        lines.append(f"  br i1 {is_zero}, label %{zero_label}, label %{nonzero_label}")

        self._start_block(zero_label, lines)
        zero_pred = self.cur_block
        lines.append(f"  br label %{end_label}")

        self._start_block(nonzero_label, lines)
        q = self.tmp()
        lines.append(f"  {q} = sdiv i64 {a_val}, {b_val}")
        r = self.tmp()
        lines.append(f"  {r} = srem i64 {a_val}, {b_val}")
        r_nonzero = self.tmp()
        lines.append(f"  {r_nonzero} = icmp ne i64 {r}, 0")
        r_neg = self.tmp()
        lines.append(f"  {r_neg} = icmp slt i64 {r}, 0")
        b_neg = self.tmp()
        lines.append(f"  {b_neg} = icmp slt i64 {b_val}, 0")
        signs_differ = self.tmp()
        lines.append(f"  {signs_differ} = xor i1 {r_neg}, {b_neg}")
        need_adjust = self.tmp()
        lines.append(f"  {need_adjust} = and i1 {r_nonzero}, {signs_differ}")
        q_minus_1 = self.tmp()
        lines.append(f"  {q_minus_1} = sub i64 {q}, 1")
        result = self.tmp()
        lines.append(f"  {result} = select i1 {need_adjust}, i64 {q_minus_1}, i64 {q}")
        nonzero_pred = self.cur_block
        lines.append(f"  br label %{end_label}")

        self._start_block(end_label, lines)
        out = self.tmp()
        lines.append(f"  {out} = phi i64 [ {INT_NULL_CONST}, %{zero_pred} ], [ {result}, %{nonzero_pred} ]")
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

    def _emit_typeof(self, expr, env, lines):
        """claude.md #176: `typeof <expr>` -- always text.

        Non-enum operand: the runtime type IS the static type, always
        (Festina has no other source of runtime polymorphism) -- a
        pure compile-time constant, no runtime work at all. This is
        what makes `typeof myName == 'text'` free.

        Enum operand: reads the tag -- pure-struct representation:
        payload-16 (past the widened header, see
        _emit_fresh_heap_header's own comment); mixed representation:
        field 0 of the {tag, value} box (FESTINA_ENUM_BOX_LLVM_TYPE).
        That tag pointer IS the text result already (see
        _enum_tag_const's own comment: the tag is never a small
        integer needing a lookup table, it's always a `ptr` directly
        to the interned type-name constant) -- and it's always the
        CONCRETE runtime member's own name, never the enum's own name
        ("Shape" is never a typeof result; "Circle"/"Square" are)."""
        val, vtype = self._emit_expr(expr.operand, env, lines)
        if not isinstance(vtype, types_mod.EnumType):
            return self.string_const(types_mod.type_name(vtype)), TEXT
        info = self.enums[vtype.name]
        # claude.md #176: an enum-typed value defaults to null until
        # assigned (no auto-vivify) -- guarded here the same way field
        # access on an enum value is, since both read the tag at a
        # fixed negative offset (or box field 0) from the value's own
        # pointer, which is exactly what's unsafe to do when that
        # pointer is null. `typeof shape == 'Circle'` is the documented
        # safe-guard idiom BEFORE field access, so typeof itself failing
        # loudly on null (rather than crashing) is what keeps that
        # idiom actually safe to write.
        is_null = self.tmp()
        lines.append(f"  {is_null} = icmp eq ptr {val}, null")
        null_label = self.label("typeof.null")
        nonnull_label = self.label("typeof.nonnull")
        lines.append(f"  br i1 {is_null}, label %{null_label}, label %{nonnull_label}")
        self._start_block(null_label, lines)
        null_msg = self.string_const(f"typeof applied to a null {vtype.name} value")
        lines.append(f"  call void @festina_fail(ptr {null_msg})")
        lines.append("  unreachable")
        self._start_block(nonnull_label, lines)
        tag_ptr = self.tmp()
        if info.is_pure_struct:
            lines.append(f"  {tag_ptr} = getelementptr i8, ptr {val}, i64 -16")
        else:
            lines.append(f"  {tag_ptr} = getelementptr {FESTINA_ENUM_BOX_LLVM_TYPE}, ptr {val}, i32 0, i32 0")
        tag = self.tmp()
        lines.append(f"  {tag} = load ptr, ptr {tag_ptr}")
        return tag, TEXT

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
                # claude.md #114: log(x) for a non-text x compiles as
                # log of x.toText() -- containers render JSON-like, and
                # blob/img/aud are compile errors (see _to_text's own
                # comment for why they refuse rather than auto-render).
                if isinstance(vtype, (types_mod.StructType, types_mod.TableType,
                                      types_mod.ArrayType, types_mod.MapType)):
                    rendered = self._to_text(val, vtype, lines)
                    lines.append(f"  call void @festina_log_text(ptr {rendered})")
                    lines.append(f"  call void @free(ptr {rendered})")
                    self._release_owned_receiver(expr.args[0], val, vtype, lines)
                    return "0", None
                if vtype == BLOB:
                    # claude.md #115: log(blob) prints the contents,
                    # exactly as `${blob}` renders them -- one implicit
                    # conversion, both positions.
                    rendered = self._to_text(val, vtype, lines)
                    lines.append(f"  call void @festina_log_text(ptr {rendered})")
                    lines.append(f"  call void @free(ptr {rendered})")
                    self._release_owned_receiver(expr.args[0], val, vtype, lines)
                    return "0", None
                if isinstance(vtype, (types_mod.ImageType, types_mod.AudioType)):
                    raise CodegenError(
                        f"log() cannot print a value of type "
                        f"{types_mod.type_name(vtype)} -- it has no text form",
                        file=self.filename, line=callee.line)
                if not isinstance(vtype, types_mod.PrimitiveType):
                    raise CodegenError(
                        f"log() only supports primitive values right now, "
                        f"found {types_mod.type_name(vtype)}",
                        file=self.filename, line=callee.line)
                fn = {"int": "festina_log_int", "float": "festina_log_float",
                      "bool": "festina_log_bool", "text": "festina_log_text"}[vtype.name]
                ty = _llvm_type(vtype)
                lines.append(f"  call void @{fn}({ty} {val})")
                self._free_text_temp(expr.args[0], val, vtype, lines)
                return "0", None
            if name == "fail":
                # claude.md #158: fail(message) is unchanged (still the
                # exact "fail: <message>" line an uncaught throw also
                # produces -- claude.md #157). fail(message, fields), the
                # new structured form, is a genuinely different runtime
                # call/output shape, not a superset -- see
                # festina_fail_structured's own comment. Neither form
                # calls _cleanup_json_arg_text for either argument: both
                # always exit(1) right after, so, matching every other
                # fail()/exit() call site's own established precedent,
                # anything past the call would be dead code.
                text_val, *_ = self._emit_json_arg_text(expr.args[0], env, lines)
                if len(expr.args) == 2:
                    fields_json, *_ = self._emit_json_arg_text(
                        expr.args[1], env, lines, expected_type=types_mod.MapType(TEXT))
                    lines.append(f"  call void @festina_fail_structured(ptr {text_val}, ptr {fields_json})")
                else:
                    lines.append(f"  call void @festina_fail(ptr {text_val})")
                return "0", None
            if name == "troubleshoot":
                # claude.md #158: unlike fail(), this always returns
                # normally, so both arguments' own rendered-text buffers
                # (and, for the fields argument, the original map[text]
                # value's own reference) need real cleanup -- but only
                # AFTER the festina_troubleshoot() call that actually
                # consumes text_val/fields_json, never before (see
                # _emit_json_arg_text's own comment on the real bug that
                # ordering mistake caused).
                event_text, event_expr, event_val, event_vtype = \
                    self._emit_json_arg_text(expr.args[0], env, lines)
                fields_json, fields_expr, fields_val, fields_vtype = \
                    self._emit_json_arg_text(expr.args[1], env, lines,
                                              expected_type=types_mod.MapType(TEXT))
                lines.append(f"  call void @festina_troubleshoot(ptr {event_text}, ptr {fields_json})")
                self._cleanup_json_arg_text(event_text, event_expr, event_val, event_vtype, lines)
                self._cleanup_json_arg_text(fields_json, fields_expr, fields_val, fields_vtype, lines)
                return "0", None
            if name == "close":
                # claude.md #131: exits the program with `code`, running
                # a declared `on exit(code:int)` handler first --
                # festina_program_exit does both (see festina_runtime.c),
                # since the registered handler (if any) is a runtime
                # concern, not something codegen calls directly here.
                val, vtype = self._emit_expr(expr.args[0], env, lines)
                val = self._coerce(val, vtype, INT, lines, source_expr=expr.args[0])
                lines.append(f"  call void @festina_program_exit(i64 {val})")
                return "0", None
            # claude.md #93: files, time and canvas export. Each frees
            # any text temporary it was handed once the call returns --
            # none of these runtime functions keeps a pointer past it
            # (the file helpers read or write and close; strftime copies
            # into its own buffer; Cairo's PNG writer takes the path by
            # value).
            # claude.md #109 moved the five file functions onto `blob`
            # itself (see _emit_blob_method). The C helpers they called
            # are unchanged and still do the work -- what went away is
            # the free-function spelling, not the capability.
            # claude.md #132: mkdir()/ls() are the identical "one text
            # arg, one runtime call" shape formatTime/saveCanvas already
            # are -- mkdir answers bool (claude.md #93's own "a program
            # tests for this, it doesn't stop the program" rule, same as
            # a blob's write/append/delete), ls answers a fresh arr[text]
            # (festina_ls builds it exactly like festina_text_split
            # does -- see that function's own comment in
            # festina_runtime.c).
            _FILE_TIME_BUILTINS = {
                "formatTime": ("festina_format_time", "ptr", TEXT),
                "mkdir": ("festina_mkdir", "i8", BOOL),
                "ls": ("festina_ls", "ptr", types_mod.ArrayType(TEXT)),
            }
            # claude.md #94: paths, transforms, gradients and alpha.
            # All draw onto (or configure) the canvas, so all of them
            # open one -- unlike the style setters of claude.md #89,
            # which only record state.
            _CANVAS_OPS = {
                "render": ("festina_render", []),
                "clearCanvas": ("festina_clear_canvas", []),
                "clearRect": ("festina_clear_rect", ["i64"] * 4),
                # claude.md #133
                "clearCircle": ("festina_clear_circle", ["i64"] * 3),
                "clearPixel": ("festina_clear_pixel", ["i64"] * 2),
                # claude.md #139
                "setClientWidth": ("festina_set_client_width", ["i64"]),
                "setClientHeight": ("festina_set_client_height", ["i64"]),
                "beginPath": ("festina_begin_path", []),
                "moveTo": ("festina_move_to", ["i64", "i64"]),
                "lineTo": ("festina_line_to", ["i64", "i64"]),
                "curveTo": ("festina_curve_to", ["i64"] * 6),
                "closePath": ("festina_close_path", []),
                "fillPath": ("festina_fill_path", []),
                "strokePath": ("festina_stroke_path", []),
                "translate": ("festina_translate", ["i64", "i64"]),
                "rotate": ("festina_rotate", ["double"]),
                "scale": ("festina_scale", ["double", "double"]),
                "resetTransform": ("festina_reset_transform", []),
                "saveState": ("festina_save_state", []),
                "restoreState": ("festina_restore_state", []),
                "fillAlpha": ("festina_set_alpha", ["double"]),
                "fillLinearGradient": ("festina_fill_linear_gradient", ["i64"] * 6),
                "fillRadialGradient": ("festina_fill_radial_gradient", ["i64"] * 5),
                # claude.md #180: enterFullscreen()/exitFullscreen() --
                # meaningless without a real OS window (there is no
                # "headless fullscreen"), so both join render() as the
                # things here that need a GUI, below.
                "enterFullscreen": ("festina_enter_fullscreen", []),
                "exitFullscreen": ("festina_exit_fullscreen", []),
                # claude.md #182: showCursor()/hideCursor() -- unlike
                # render()/enterFullscreen()/exitFullscreen() just
                # above, these don't force a window open: a cursor is
                # meaningless with none, so calling either before one
                # exists just records the desired state (see
                # g_cursor_hidden's own comment in
                # festina_runtime_graphics.c) for whenever one does.
                "showCursor": ("festina_show_cursor", []),
                "hideCursor": ("festina_hide_cursor", []),
            }
            if name in _CANVAS_OPS:
                fn, arg_irs = _CANVAS_OPS[name]
                self.uses_graphics_code = True
                # claude.md #95/#180: render() and the two fullscreen
                # calls are the only things here that need a GUI.
                # Everything else paints the offscreen canvas, which
                # needs no X server at all.
                if name in ("render", "enterFullscreen", "exitFullscreen"):
                    self.uses_graphics = True
                # A gradient's colour arguments are `color`-typed
                # (semantic enforces it), so they arrive as the packed
                # integers claude.md #91 already made them -- nothing
                # here needs the expected-type path.
                vals = [self._emit_expr(a, env, lines)[0] for a in expr.args]
                sig = ", ".join(f"{ty} {v}" for ty, v in zip(arg_irs, vals))
                lines.append(f"  call void @{fn}({sig})")
                return "0", None
            if name in ("sqliteInt", "sqliteFloat", "sqliteText"):
                return self._emit_sqlite_scalar(name, expr, env, lines)
            if name == "now":
                out = self.tmp()
                lines.append(f"  {out} = call i64 @festina_now_ms()")
                return out, INT
            if name in _FILE_TIME_BUILTINS:
                emitted = [self._emit_expr(a, env, lines) for a in expr.args]
                sig = ", ".join(
                    f"{'i64' if t == INT else 'ptr'} {v}" for v, t in emitted)
                fn, ret_ir, ret_type = _FILE_TIME_BUILTINS[name]
                out = self.tmp()
                lines.append(f"  {out} = call {ret_ir} @{fn}({sig})")
                for arg_expr, (val, vtype) in zip(expr.args, emitted):
                    self._free_text_temp(arg_expr, val, vtype, lines)
                return out, ret_type
            if name == "exec":
                # claude.md #150: exec(args) -- args is arr[text], passed
                # exactly as the header pointer festina_process_exec
                # itself reads directly (same shape festina_arr_join's
                # own `arr` parameter takes). The argument-cleanup here
                # is the IDENTICAL "release if owning, else it's
                # borrowed" logic an ordinary user-function call's own
                # argument passing already uses just below (claude.md
                # #119) -- reused rather than duplicated, since exec()'s
                # own single arr[text] argument is exactly that same
                # case (a refcounted type, not text), not a new one.
                #
                # claude.md #177: exec(args, callback) -- the non-
                # blocking form. Same args handling, dispatched instead
                # through festina_process_exec_dispatch (releasing args
                # right after, exactly like the 1-arg form releases it
                # right after ITS own synchronous call returns -- same
                # timing, matches festina_process_exec_dispatch's own
                # doc comment on why it must deep-copy rather than
                # borrow). The callback value itself needs no cleanup
                # of its own -- func[T]:void values are never allocated/
                # freed (see _llvm_type's own FuncType comment), so
                # there's nothing here to release, same as the ordinary
                # indirect-call-through-a-func-value site below.
                self.uses_exec = True
                arg_expr = expr.args[0]
                val, vtype = self._emit_expr(arg_expr, env, lines)
                if len(expr.args) == 1:
                    out = self.tmp()
                    lines.append(f"  {out} = call i64 @festina_process_exec(ptr {val})")
                    if _is_refcounted(vtype) and self._is_owning_refcounted_source(arg_expr):
                        lines.append(f"  call void {self._release_fn_for(vtype)}(ptr {val})")
                    else:
                        self._free_text_temp(arg_expr, val, vtype, lines)
                    return out, INT
                self.uses_async_io = True
                cb_val, _ = self._emit_expr(expr.args[1], env, lines)
                trampoline_name = self._emit_exec_callback_trampoline()
                lines.append(
                    f"  call void @festina_process_exec_dispatch(ptr {val}, "
                    f"ptr {cb_val}, ptr {trampoline_name})")
                if _is_refcounted(vtype) and self._is_owning_refcounted_source(arg_expr):
                    lines.append(f"  call void {self._release_fn_for(vtype)}(ptr {val})")
                else:
                    self._free_text_temp(arg_expr, val, vtype, lines)
                return "0", None
            if name in ("openPort", "closePort"):
                # claude.md #151: both take a single plain int -- no
                # refcounted/text argument-cleanup story at all, unlike
                # exec() just above.
                self.uses_http = True
                val, _ = self._emit_expr(expr.args[0], env, lines)
                fn = "festina_open_port" if name == "openPort" else "festina_close_port"
                lines.append(f"  call void @{fn}(i64 {val})")
                return "0", None
            if name == "openSecurePort":
                # claude.md #160: the TLS counterpart -- shares uses_http
                # (same listener/connection table, same event loop) and
                # adds uses_https on top (the narrower "mbedTLS is
                # actually needed" signal, see this flag's own comment).
                # The key argument is always blob (semantic.py's own
                # _BUILTIN_SIGNATURES entry) -- _emit_sendable_body
                # already knows how to pull a blob's raw bytes out as
                # (data_ptr, len_val), the identical shape
                # http.send()/socket.send() already use for their own
                # blob-typed `data` argument.
                self.uses_http = True
                self.uses_https = True
                port_val, _ = self._emit_expr(expr.args[0], env, lines)
                key_expr = expr.args[1]
                key_val, key_vtype = self._emit_expr(key_expr, env, lines)
                data_ptr, len_val, temp_to_free = self._emit_sendable_body(key_val, key_vtype, lines)
                lines.append(f"  call void @festina_open_secure_port(i64 {port_val}, "
                             f"ptr {data_ptr}, i64 {len_val})")
                if temp_to_free is not None:
                    lines.append(f"  call void @free(ptr {temp_to_free})")
                if _is_refcounted(key_vtype) and self._is_owning_refcounted_source(key_expr):
                    lines.append(f"  call void {self._release_fn_for(key_vtype)}(ptr {key_val})")
                return "0", None
            if name == "parseURL":
                # claude.md #162: lives in CORE -- no uses_http/_https
                # flag to set here at all, unlike openPort/openSecurePort
                # just above (parseURL has nothing to do with the HTTP
                # server or fetch() -- see festina_release_url's own
                # comment in _release_fn_for).
                text_expr = expr.args[0]
                text_val, text_vtype = self._emit_expr(text_expr, env, lines)
                text_val = self._to_text(text_val, text_vtype, lines)
                out = self.tmp()
                lines.append(f"  {out} = call ptr @festina_parse_url(ptr {text_val})")
                self._free_text_temp(text_expr, text_val, text_vtype, lines)
                return out, types_mod.UrlType()
            # claude.md #95/#135: writes the OFFSCREEN canvas, so it
            # needs no window either way -- this is the headless case
            # the render() split exists for. saveCanvas() with no path
            # is new: a fresh img SNAPSHOT of the canvas (see
            # festina_canvas_to_image's own comment for why a snapshot,
            # not a live alias) instead of writing a file, so it gets
            # its own branch rather than joining _FILE_TIME_BUILTINS
            # above -- semantic.py already resolved the return TYPE
            # itself per arity (img vs. bool), which that generic
            # dispatch has no way to express (one fixed ret_type per
            # name, not per call).
            if name == "saveCanvas":
                self.uses_graphics_code = True
                if not expr.args:
                    out = self.tmp()
                    lines.append(f"  {out} = call ptr @festina_canvas_to_image()")
                    return out, types_mod.ImageType()
                val, vtype = self._emit_expr(expr.args[0], env, lines)
                out = self.tmp()
                lines.append(f"  {out} = call i8 @festina_save_canvas(ptr {val})")
                self._free_text_temp(expr.args[0], val, vtype, lines)
                return out, BOOL
            if name == "sqlite":
                return self._emit_sqlite_call(expr, env, lines, expected_type)
            if name == "regex":
                return self._emit_regex_call(expr, env, lines)
            if name in ("drawRect", "drawCircle", "drawText", "drawImage", "loadImage",
                        "drawPixel", "blankImage", "getPixelColor",
                        "fillStyle", "borderColor", "lineWidth", "changeFont",
                        "measureTextWidth", "measureTextHeight"):
                return self._emit_graphics_call(name, expr, env, lines)
            if name in ("setTimeout", "setInterval", "clearTimeout", "clearInterval"):
                return self._emit_timer_call(name, expr, env, lines)
            if name == "stopAudioPlayer":
                # claude.md #99. Lives in the audio translation unit, so
                # naming it is what makes a program "use audio" -- see
                # setMaxAudioPlayers just below for the same reasoning.
                # A bare stopAudioPlayer() passes -1, the runtime's own
                # "every channel" encoding: naming no channel obviously
                # means all of them, and there is no other way to say it.
                self.uses_audio = True
                if expr.args:
                    chan_val, chan_type = self._emit_expr(expr.args[0], env, lines)
                    chan_val = self._coerce(chan_val, chan_type, INT, lines)
                else:
                    chan_val = "-1"
                lines.append(f"  call void @festina_stop_audio_player(i64 {chan_val})")
                return "0", None
            if name == "isAudioPlayerPlaying":
                # claude.md #146: the per-CHANNEL counterpart to
                # aud.isPlaying()'s per-clip question -- unlike
                # stopAudioPlayer, the channel argument is required
                # (semantic.py's own fixed _BUILTIN_SIGNATURES entry,
                # not the alternates mechanism): there is no sensible
                # "any channel" reading the way a bare stopAudioPlayer()
                # naturally means "every channel".
                self.uses_audio = True
                chan_val, chan_type = self._emit_expr(expr.args[0], env, lines)
                chan_val = self._coerce(chan_val, chan_type, INT, lines)
                out = self.tmp()
                lines.append(f"  {out} = call i8 @festina_channel_is_playing(i64 {chan_val})")
                return out, BOOL
            if name in ("setMaxAudioPlayers", "maxAudioPlayers"):
                # claude.md #98. Both live in the audio translation unit,
                # so naming either is what makes a program "use audio" --
                # which is right: a program that tunes the voice limit is
                # a program that plays sounds, and one that never touches
                # audio never links these in.
                self.uses_audio = True
                if name == "maxAudioPlayers":
                    out = self.tmp()
                    lines.append(f"  {out} = call i64 @festina_get_max_audio_players()")
                    return out, INT
                max_val, max_type = self._emit_expr(expr.args[0], env, lines)
                max_val = self._coerce(max_val, max_type, INT, lines)
                lines.append(f"  call void @festina_set_max_audio_players(i64 {max_val})")
                return "0", None
            if name == "loadAudio":
                self.uses_audio = True
                path_val, path_type = self._emit_expr(expr.args[0], env, lines)
                out = self.tmp()
                lines.append(f"  {out} = call ptr @festina_load_audio(ptr {path_val})")
                # claude.md #83: the path is only fopen()'d, never kept.
                self._free_text_temp(expr.args[0], path_val, path_type, lines)
                return out, AUDIO
            found = env.lookup(name)
            if found is not None and isinstance(found[1], types_mod.FuncType):
                # claude.md #141: an INDIRECT call, through a
                # func[...]:...-typed variable/parameter/field/element
                # rather than a plain declared function's own name --
                # checked (and env.lookup'd) BEFORE self.func_decls
                # below, mirroring semantic.py's own dispatch order, so
                # a local variable that shadows a real global function
                # of the same name resolves to ITS OWN signature here
                # too, never silently falling through to a direct call
                # against the shadowed global instead. A real global
                # function's own env entry (registered by
                # _register_func_signature) is never mistaken for this:
                # it stores its RETURN type there, not a FuncType, so
                # only a genuine func-typed value ever takes this
                # branch.
                fn_ref, fn_type = found
                fn_ptr = self.tmp()
                lines.append(f"  {fn_ptr} = load ptr, ptr {fn_ref}")
                arg_vals = []
                arg_temps = []
                for arg_expr, ptype in zip(expr.args, fn_type.param_types):
                    val, vtype = self._emit_value_for(arg_expr, env, lines, ptype)
                    val = self._coerce(val, vtype, ptype, lines)
                    arg_vals.append(f"{_llvm_type(ptype)} {val}")
                    arg_temps.append((arg_expr, val, vtype, ptype))
                args_ir = ", ".join(arg_vals)
                ret_type = fn_type.return_type
                if ret_type is None:
                    lines.append(f"  call void {fn_ptr}({args_ir})")
                    out = "0"
                else:
                    out = self.tmp()
                    lines.append(f"  {out} = call {_llvm_type(ret_type)} {fn_ptr}({args_ir})")
                # claude.md #83/#119: identical post-call argument
                # cleanup to the direct-call path just below -- see its
                # own comment for the full reasoning.
                for arg_expr, val, vtype, ptype in arg_temps:
                    if (_is_refcounted(ptype)
                            and self._is_owning_refcounted_source(arg_expr)):
                        lines.append(
                            f"  call void {self._release_fn_for(ptype)}(ptr {val})")
                    else:
                        self._free_text_temp(arg_expr, val, vtype, lines)
                return out, ret_type
            if name in self.func_decls:
                decl = self.func_decls[name]
                arg_vals = []
                arg_temps = []
                for arg_expr, param in zip(expr.args, decl.params):
                    ptype = self._resolve(param.type_expr, decl)
                    val, vtype = self._emit_value_for(arg_expr, env, lines, ptype)
                    val = self._coerce(val, vtype, ptype, lines)
                    arg_vals.append(f"{_llvm_type(ptype)} {val}")
                    arg_temps.append((arg_expr, val, vtype, ptype))
                ret_ref, ret_type = env.lookup(name)
                args_ir = ", ".join(arg_vals)
                if ret_type is None:
                    lines.append(f"  call void @{name}({args_ir})")
                    out = "0"
                else:
                    out = self.tmp()
                    lines.append(f"  {out} = call {_llvm_type(ret_type)} @{name}({args_ir})")
                # claude.md #83: after the call, not before -- the callee
                # borrows every text argument for the duration of the call.
                #
                # claude.md #119: an OWNING refcounted argument -- a
                # Call's fresh result, a literal, an owned chain
                # (`f(make().inner)`, `f(getRows()[0])`) -- is released
                # after the call the same way, closing the
                # argument-position half of #117's leftovers. Sound for
                # the same reason the text free is: parameters are
                # borrows, and anything the callee KEPT took its own
                # retain on the way to wherever it was stored (an
                # escaping param retains at binding; a global/field
                # store retains; a returned alias is retained by the
                # Return path) -- so the caller's +1 is provably the
                # last reference nothing else will ever drop.
                for arg_expr, val, vtype, ptype in arg_temps:
                    if (_is_refcounted(ptype)
                            and self._is_owning_refcounted_source(arg_expr)):
                        lines.append(
                            f"  call void {self._release_fn_for(ptype)}(ptr {val})")
                    else:
                        self._free_text_temp(arg_expr, val, vtype, lines)
                return out, ret_type
            raise CodegenError(f"unknown function '{name}'", file=self.filename, line=callee.line)
        if isinstance(callee, ast.Member) and not callee.computed:
            # claude.md #188 (uraikus/festina#76 item 1):
            # Math.floorDiv(a:int, b:int) -> int
            if (isinstance(callee.obj, ast.Identifier) and callee.obj.name == "Math"
                    and callee.prop == "floorDiv"):
                a, _ = self._emit_expr(expr.args[0], env, lines)
                b, _ = self._emit_expr(expr.args[1], env, lines)
                out = self._emit_floor_div(a, b, lines)
                return out, INT
            # claude.md #56: Math.floor/ceil/round/trunc(x:float) -> int
            # claude.md #93: Math.sqrt/sin/pow/min/... and Math.random()
            if (isinstance(callee.obj, ast.Identifier) and callee.obj.name == "Math"
                    and callee.prop in MATH_FLOAT_FNS):
                val, _ = self._emit_expr(expr.args[0], env, lines)
                out = self.tmp()
                lines.append(
                    f"  {out} = call double @{MATH_FLOAT_FNS[callee.prop]}(double {val})")
                return out, FLOAT
            if (isinstance(callee.obj, ast.Identifier) and callee.obj.name == "Math"
                    and callee.prop in MATH_FLOAT2_FNS):
                a, _ = self._emit_expr(expr.args[0], env, lines)
                b, _ = self._emit_expr(expr.args[1], env, lines)
                out = self.tmp()
                lines.append(
                    f"  {out} = call double @{MATH_FLOAT2_FNS[callee.prop]}"
                    f"(double {a}, double {b})")
                return out, FLOAT
            if (isinstance(callee.obj, ast.Identifier) and callee.obj.name == "Math"
                    and callee.prop == "random"):
                out = self.tmp()
                lines.append(f"  {out} = call double @festina_random()")
                return out, FLOAT
            if (isinstance(callee.obj, ast.Identifier) and callee.obj.name == "Math"
                    and callee.prop in MATH_INTRINSICS):
                val, vtype = self._emit_expr(expr.args[0], env, lines)
                if vtype != FLOAT:
                    raise CodegenError(
                        f"Math.{callee.prop}() expects a float argument, found {types_mod.type_name(vtype)}",
                        file=self.filename, line=callee.line)
                rounded = self.tmp()
                lines.append(f"  {rounded} = call double @{MATH_INTRINSICS[callee.prop]}(double {val})")
                out = self._emit_float_to_int(rounded, lines)
                return out, INT
            # claude.md #55: int.toFloat() -> float
            if callee.prop == "toFloat" and not expr.args:
                val, vtype = self._emit_expr(callee.obj, env, lines)
                if vtype == INT:
                    out = self.tmp()
                    lines.append(f"  {out} = sitofp i64 {val} to double")
                    return out, FLOAT
            # claude.md #150: text.toInt() -> int. A literal receiver
            # (`'42'.toInt()`) is parsed once, in Python, at compile
            # time -- offloading work out of the compiled program
            # entirely, the same "resolved once, then costs nothing"
            # treatment claude.md #91 already gives color/font literals
            # -- rather than emitting a runtime call whose argument
            # happens to always be the same known string. A dynamic
            # receiver (the overwhelmingly common real case -- a
            # scanned token, user input, ...) still goes through
            # festina_text_to_int, which implements the identical
            # parseInt()-style rule this constant fold mirrors in
            # Python (see that function's own doc comment in
            # runtime/festina_runtime.c for exactly what "identical"
            # means here).
            if callee.prop == "toInt" and not expr.args:
                if isinstance(callee.obj, ast.StringLit):
                    return str(_parse_int_like_strtoll(callee.obj.value)), INT
                val, vtype = self._emit_expr(callee.obj, env, lines)
                if vtype == TEXT:
                    out = self.tmp()
                    lines.append(f"  {out} = call i64 @festina_text_to_int(ptr {val})")
                    self._free_text_temp(callee.obj, val, vtype, lines)
                    return out, INT
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
                # claude.md #114: the explicit spelling of the rendering
                # log()/`${}` now do implicitly for containers.
                if isinstance(vtype, (types_mod.StructType, types_mod.TableType,
                                      types_mod.ArrayType, types_mod.MapType)):
                    out = self._to_text(val, vtype, lines)
                    self._release_owned_receiver(callee.obj, val, vtype, lines)
                    return out, TEXT
            # claude.md #159: 'json'.toStruct(T) -> T; 'json'.toArr(T)
            # -> arr[T]. semantic.py already validated the receiver is
            # text and the target shape is v1-supported (scalar fields/
            # element only), so this is purely: get a cursor over the
            # receiver's own bytes, hand it to the (cached, generated
            # on first use) per-type parsing function, check for
            # trailing garbage, free the cursor. The RESULT is exactly
            # as fresh/owning a value as any other struct/array-
            # producing site (a brand-new, refcount=1 heap header the
            # generated function itself calloc'd) -- no retain needed
            # on the way out, same as an array/map literal or a call
            # result already isn't.
            if callee.prop in ("toStruct", "toArr") and len(expr.args) == 1 \
                    and isinstance(expr.args[0], ast.TypeArg):
                recv_val, recv_type = self._emit_expr(callee.obj, env, lines)
                cursor = self.tmp()
                lines.append(f"  {cursor} = call ptr @festina_json_cursor_new(ptr {recv_val})")
                target_type = self._resolve(expr.args[0].type_expr, expr.args[0])
                if callee.prop == "toStruct":
                    fn_name = self._from_json_struct_fn_for(target_type)
                    result_type = target_type
                else:
                    fn_name = self._from_json_arr_fn_for(target_type)
                    result_type = types_mod.ArrayType(target_type)
                out = self.tmp()
                lines.append(f"  {out} = call ptr {fn_name}(ptr {cursor})")
                lines.append(f"  call void @festina_json_expect_end(ptr {cursor})")
                lines.append(f"  call void @festina_json_cursor_free(ptr {cursor})")
                self._free_text_temp(callee.obj, recv_val, recv_type, lines)
                return out, result_type
            # claude.md #116: sentence.split(sep) -> arr[text]. The
            # result is a fresh refcounted array the runtime built, so
            # it is exactly as "owning" a source as an array literal --
            # binding it needs no retain, and scope exit reclaims it
            # (elements included) through the ordinary arr[text] release.
            if callee.prop == "split":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if obj_type == TEXT:
                    sep_val, sep_type = self._emit_expr(expr.args[0], env, lines)
                    out = self.tmp()
                    if sep_type == REGEX:
                        lines.append(f"  {out} = call ptr @festina_regex_split("
                                     f"ptr {sep_val}, ptr {obj_val})")
                    else:
                        lines.append(f"  {out} = call ptr @festina_text_split("
                                     f"ptr {obj_val}, ptr {sep_val})")
                    self._free_text_temp(callee.obj, obj_val, obj_type, lines)
                    self._free_text_temp(expr.args[0], sep_val, sep_type, lines)
                    self._free_regex_temp(expr.args[0], sep_val, sep_type, lines)
                    return out, types_mod.ArrayType(TEXT)
            # claude.md #116: words.join(sep) -> text. One runtime
            # function; the element KIND rides along as a constant,
            # since only the compiler knows an arr[T]'s T (the same
            # reason the JSON render functions are generated per type).
            if callee.prop == "join":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if isinstance(obj_type, types_mod.ArrayType):
                    kind = {INT: "int", FLOAT: "float", BOOL: "bool",
                            TEXT: "text"}.get(obj_type.element)
                    if kind is not None:
                        sep_val, sep_type = self._emit_expr(expr.args[0], env, lines)
                        out = self.tmp()
                        lines.append(f"  {out} = call ptr @festina_arr_join("
                                     f"ptr {obj_val}, ptr {sep_val}, "
                                     f"ptr {self.string_const(kind)})")
                        self._free_text_temp(expr.args[0], sep_val, sep_type, lines)
                        self._release_owned_receiver(callee.obj, obj_val, obj_type, lines)
                        return out, TEXT
            # claude.md #67: pattern.test(value:text) -> bool
            if callee.prop == "test":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if obj_type == REGEX:
                    arg_val, arg_type = self._emit_expr(expr.args[0], env, lines)
                    out = self.tmp()
                    lines.append(f"  {out} = call i8 @festina_regex_test(ptr {obj_val}, ptr {arg_val})")
                    self._free_text_temp(expr.args[0], arg_val, arg_type, lines)
                    self._free_regex_temp(callee.obj, obj_val, obj_type, lines)
                    return out, BOOL
            # claude.md #68: value.match(pattern:regex) -> text (or null)
            if callee.prop == "match":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if obj_type == TEXT:
                    arg_val, arg_type = self._emit_expr(expr.args[0], env, lines)
                    out = self.tmp()
                    lines.append(f"  {out} = call ptr @festina_regex_match(ptr {arg_val}, ptr {obj_val})")
                    self._free_text_temp(callee.obj, obj_val, obj_type, lines)
                    self._free_regex_temp(expr.args[0], arg_val, arg_type, lines)
                    return out, TEXT
            # claude.md #68: value.replace(search, replacement:text) -> text
            # claude.md #107: how many matches this touches is no longer
            # decided here. A regex search carries its own 'g' flag and
            # the runtime reads it; a text search has no flags and
            # replaces the first match only. There is nothing left for
            # codegen to pass, which is the point -- the old i8 argument
            # could only ever be a compile-time constant, so a regex
            # built by `regex(p, f)` could never have said "global".
            if callee.prop == "replace":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if obj_type == TEXT:
                    search_val, search_type = self._emit_expr(expr.args[0], env, lines)
                    replacement_val, replacement_type = self._emit_expr(expr.args[1], env, lines)
                    out = self.tmp()
                    if search_type == REGEX:
                        lines.append(
                            f"  {out} = call ptr @festina_regex_replace(ptr {search_val}, ptr {obj_val}, "
                            f"ptr {replacement_val})"
                        )
                    else:
                        # search_type is TEXT, or None from a bare `null`
                        # literal (festina_str_replace treats a NULL
                        # search pointer defensively -- see the runtime).
                        lines.append(
                            f"  {out} = call ptr @festina_str_replace(ptr {obj_val}, ptr {search_val}, "
                            f"ptr {replacement_val})"
                        )
                    self._free_text_temp(callee.obj, obj_val, obj_type, lines)
                    self._free_text_temp(expr.args[0], search_val, search_type, lines)
                    self._free_regex_temp(expr.args[0], search_val, search_type, lines)
                    self._free_text_temp(expr.args[1], replacement_val, replacement_type, lines)
                    return out, TEXT
            # claude.md #96: array methods.
            if callee.prop in ("push", "pop", "shift", "unshift", "splice",
                               "indexOf", "sort"):
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if isinstance(obj_type, types_mod.ArrayType):
                    result = self._emit_array_method(
                        callee.prop, obj_val, obj_type, expr, env, lines)
                    # claude.md #119: an owning receiver (a call/chain
                    # temporary) is released once the method is done
                    # with it -- safe even for pop/shift/splice, whose
                    # removed elements were transferred OUT of the
                    # array before this release could cascade to them.
                    self._release_owned_receiver(callee.obj, obj_val, obj_type, lines)
                    return result
            # claude.md #92: sheet.clip(x, y, w, h) -> img (a new image,
            # leaving the sheet untouched) and image.resize(w, h) -> void
            # (in place, so every binding holding it sees the new size).
            if callee.prop in ("clip", "resize"):
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if isinstance(obj_type, types_mod.ImageType):
                    arg_vals = [self._emit_expr(a, env, lines)[0] for a in expr.args]
                    if callee.prop == "clip":
                        out = self.tmp()
                        lines.append(
                            f"  {out} = call ptr @festina_image_clip(ptr {obj_val}, "
                            f"i64 {arg_vals[0]}, i64 {arg_vals[1]}, "
                            f"i64 {arg_vals[2]}, i64 {arg_vals[3]})")
                        # claude.md #118/#119: a clip is an independent
                        # new surface, so an owning receiver (another
                        # clip, a call result) is done with here.
                        self._release_owned_receiver(callee.obj, obj_val, obj_type, lines)
                        return out, types_mod.ImageType()
                    lines.append(
                        f"  call void @festina_image_resize(ptr {obj_val}, "
                        f"i64 {arg_vals[0]}, i64 {arg_vals[1]})")
                    self._release_owned_receiver(callee.obj, obj_val, obj_type, lines)
                    return "0", None
            # claude.md #189: img.getPixelColor(x, y) -> color -- the
            # img-method counterpart of the canvas-level
            # getPixelColor(x, y).
            if callee.prop == "getPixelColor":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if isinstance(obj_type, types_mod.ImageType):
                    x_val, _ = self._emit_expr(expr.args[0], env, lines)
                    y_val, _ = self._emit_expr(expr.args[1], env, lines)
                    out = self.tmp()
                    lines.append(
                        f"  {out} = call i64 @festina_image_get_pixel_color(ptr {obj_val}, "
                        f"i64 {x_val}, i64 {y_val})")
                    self._release_owned_receiver(callee.obj, obj_val, obj_type, lines)
                    return out, types_mod.ColorType()
            # claude.md #134: drawRect/drawPixel/drawCircle/drawText as
            # methods on img -- the same four canvas-level drawing
            # builtins (claude.md #37/#39/#133), retargeted at the
            # receiver image's own surface. semantic.py has already
            # checked argument count/types, including drawRect/
            # drawPixel's own optional trailing `color`, dispatched here
            # purely by arity exactly like the canvas-level forms are.
            if callee.prop in ("drawRect", "drawPixel", "drawCircle", "drawText"):
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if isinstance(obj_type, types_mod.ImageType):
                    emitted = [self._emit_expr(a, env, lines) for a in expr.args]
                    arg_vals = [v for v, _ in emitted]
                    if callee.prop == "drawRect":
                        # claude.md #188 (uraikus/festina#76 item 8): a
                        # 6th argument is a further optional trailing
                        # BORDER colour, mirroring the free-function
                        # form.
                        if len(arg_vals) == 6:
                            x, y, w, h, fill, border = arg_vals
                            lines.append(
                                f"  call void @festina_image_draw_rect_colors(ptr {obj_val}, "
                                f"i64 {x}, i64 {y}, i64 {w}, i64 {h}, i64 {fill}, i64 {border})")
                        elif len(arg_vals) == 5:
                            x, y, w, h, color = arg_vals
                            lines.append(
                                f"  call void @festina_image_draw_rect_color(ptr {obj_val}, "
                                f"i64 {x}, i64 {y}, i64 {w}, i64 {h}, i64 {color})")
                        else:
                            x, y, w, h = arg_vals
                            lines.append(
                                f"  call void @festina_image_draw_rect(ptr {obj_val}, "
                                f"i64 {x}, i64 {y}, i64 {w}, i64 {h})")
                    elif callee.prop == "drawPixel":
                        if len(arg_vals) == 3:
                            x, y, color = arg_vals
                            lines.append(
                                f"  call void @festina_image_draw_pixel_color(ptr {obj_val}, "
                                f"i64 {x}, i64 {y}, i64 {color})")
                        else:
                            x, y = arg_vals
                            lines.append(
                                f"  call void @festina_image_draw_pixel(ptr {obj_val}, i64 {x}, i64 {y})")
                    elif callee.prop == "drawCircle":
                        # claude.md #188: the same fill/fill+border
                        # trailing-colour forms drawRect has, newly.
                        if len(arg_vals) == 5:
                            x, y, r, fill, border = arg_vals
                            lines.append(
                                f"  call void @festina_image_draw_circle_colors(ptr {obj_val}, "
                                f"i64 {x}, i64 {y}, i64 {r}, i64 {fill}, i64 {border})")
                        elif len(arg_vals) == 4:
                            x, y, r, color = arg_vals
                            lines.append(
                                f"  call void @festina_image_draw_circle_color(ptr {obj_val}, "
                                f"i64 {x}, i64 {y}, i64 {r}, i64 {color})")
                        else:
                            x, y, r = arg_vals
                            lines.append(
                                f"  call void @festina_image_draw_circle(ptr {obj_val}, "
                                f"i64 {x}, i64 {y}, i64 {r})")
                    else:  # drawText
                        text, x, y = arg_vals
                        lines.append(
                            f"  call void @festina_image_draw_text(ptr {obj_val}, "
                            f"ptr {text}, i64 {x}, i64 {y})")
                        self._free_text_temp(expr.args[0], text, emitted[0][1], lines)
                    self._release_owned_receiver(callee.obj, obj_val, obj_type, lines)
                    return "0", None
            # claude.md #38: music.play() / music.stop() / music.isPlaying()
            # claude.md #99: play/playLoop take an optional channel, and
            # both compile to the same runtime entry point -- the four
            # shapes differ only in two flags it already takes.
            # claude.md #109: both now RETURN that channel as an int.
            if callee.prop in ("play", "playLoop"):
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if obj_type == AUDIO:
                    if expr.args:
                        chan_val, chan_type = self._emit_expr(expr.args[0], env, lines)
                        chan_val = self._coerce(chan_val, chan_type, INT, lines)
                        explicit = 1
                    else:
                        # Never read when explicit is 0, but a real
                        # constant rather than undef keeps the IR
                        # readable and the call trivially verifiable.
                        chan_val, explicit = "0", 0
                    looping = 1 if callee.prop == "playLoop" else 0
                    out = self.tmp()
                    lines.append(
                        f"  {out} = call i64 @festina_audio_play_on(ptr {obj_val}, "
                        f"i64 {chan_val}, i8 {explicit}, i8 {looping})")
                    return out, INT
            # claude.md #111: row.undefined('col') -- reads the presence
            # mask festina_sqlite_collect_rows records one slot past the
            # row's columns. The names array is the same global schema
            # sync already uses, so an unknown name fails with a clear
            # message at run time rather than silently answering.
            if callee.prop == "undefined":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if isinstance(obj_type, types_mod.TableType):
                    names_global, _, ncols = self._table_arrays(
                        obj_type.name, self.tables[obj_type.name])
                    arg_val, arg_type = self._emit_expr(expr.args[0], env, lines)
                    out = self.tmp()
                    lines.append(
                        f"  {out} = call i8 @festina_row_undefined(ptr {obj_val}, "
                        f"ptr {names_global}, i32 {ncols}, ptr {arg_val})")
                    self._free_text_temp(expr.args[0], arg_val, arg_type, lines)
                    return out, BOOL
            # claude.md #165 (extended to img/aud by #171):
            # <text>.callback(fn:func[T]:void) -- a non-blocking blob/
            # img/aud load. `fn`'s own inferred FuncType is what tells
            # this apart from an ordinary load entirely (semantic.py
            # already confirmed it's exactly func[T]:void for one of
            # the three) -- dispatches through the matching
            # festina_*_load_dispatch, the exact same
            # null-callback-means-blocking shape
            # festina_http_send_client_dispatch already established.
            if callee.prop == "callback":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if obj_type == TEXT:
                    fn_val, fn_type = self._emit_expr(expr.args[0], env, lines)
                    self.uses_async_io = True
                    result_type = fn_type.param_types[0]
                    if isinstance(result_type, types_mod.ImageType):
                        self.uses_graphics_code = True
                        dispatch_fn = "festina_image_load_dispatch"
                    elif isinstance(result_type, types_mod.AudioType):
                        self.uses_audio = True
                        dispatch_fn = "festina_audio_load_dispatch"
                    else:
                        dispatch_fn = "festina_blob_load_dispatch"
                    out = self.tmp()
                    lines.append(
                        f"  {out} = call ptr @{dispatch_fn}(ptr {obj_val}, "
                        f"ptr {fn_val})")
                    self._free_text_temp(callee.obj, obj_val, obj_type, lines)
                    return out, result_type
            # claude.md #110: save()/saveCopy() on blob, img or aud. One
            # branch for all three, dispatched on the receiver's type --
            # the runtime functions differ only in which struct's path
            # field they update, and all three share festina_save_bytes.
            if callee.prop in ("save", "saveCopy"):
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                suffix = "_save" if callee.prop == "save" else "_save_copy"
                fn = None
                if obj_type == BLOB:
                    fn = f"festina_blob{suffix}"
                elif isinstance(obj_type, types_mod.ImageType):
                    self.uses_graphics_code = True
                    fn = f"festina_image{suffix}"
                elif isinstance(obj_type, types_mod.AudioType):
                    self.uses_audio = True
                    fn = f"festina_audio{suffix}"
                if fn is not None:
                    if expr.args:
                        arg_val, arg_type = self._emit_expr(expr.args[0], env, lines)
                    else:
                        # The no-argument save(): a null path is how the
                        # runtime is told to use the handle's own.
                        arg_val, arg_type = "null", None
                    out = self.tmp()
                    lines.append(
                        f"  {out} = call i8 @{fn}(ptr {obj_val}, ptr {arg_val})")
                    if expr.args:
                        self._free_text_temp(expr.args[0], arg_val, arg_type, lines)
                    self._release_owned_receiver(callee.obj, obj_val, obj_type, lines)
                    return out, BOOL
            # claude.md #109: blob's five methods. The receiver is a
            # blob handle, which already holds the path -- so these are
            # single runtime calls with no path argument to thread
            # through, unlike the free functions they replace.
            if callee.prop in ("toText", "write", "append", "exists", "delete"):
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if obj_type == BLOB:
                    fn, ret_ir, ret_type = {
                        "toText": ("festina_blob_to_text", "ptr", TEXT),
                        "write": ("festina_blob_write", "i8", BOOL),
                        "append": ("festina_blob_append", "i8", BOOL),
                        "exists": ("festina_blob_exists", "i8", BOOL),
                        "delete": ("festina_blob_delete", "i8", BOOL),
                    }[callee.prop]
                    out = self.tmp()
                    if expr.args:
                        arg_val, arg_type = self._emit_expr(expr.args[0], env, lines)
                        lines.append(
                            f"  {out} = call {ret_ir} @{fn}(ptr {obj_val}, ptr {arg_val})")
                        self._free_text_temp(expr.args[0], arg_val, arg_type, lines)
                    else:
                        lines.append(f"  {out} = call {ret_ir} @{fn}(ptr {obj_val})")
                    # A blob produced purely to act on -- `blob f =
                    # 'x'` inline, or a call result -- is released the
                    # same way any other member receiver is; toText()
                    # hands back an owned copy and the rest return
                    # scalars, so nothing here points into the handle.
                    self._release_owned_receiver(callee.obj, obj_val, obj_type, lines)
                    return out, ret_type
            # claude.md #151: http's fixed-shape methods (everything
            # except send(), which needs the bespoke any-typed/
            # optional-argument handling just below).
            if callee.prop in ("ok", "redirect", "upgrade", "toBlob", "toImg", "toAud", "toText"):
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if isinstance(obj_type, types_mod.HttpType):
                    self.uses_http = True
                    if callee.prop == "ok":
                        lines.append(f"  call void @festina_http_ok(ptr {obj_val})")
                        self._release_owned_receiver(callee.obj, obj_val, obj_type, lines)
                        return "0", None
                    if callee.prop == "redirect":
                        url_val, url_type = self._emit_expr(expr.args[0], env, lines)
                        lines.append(f"  call void @festina_http_redirect(ptr {obj_val}, ptr {url_val})")
                        self._free_text_temp(expr.args[0], url_val, url_type, lines)
                        self._release_owned_receiver(callee.obj, obj_val, obj_type, lines)
                        return "0", None
                    if callee.prop == "upgrade":
                        lines.append(f"  call void @festina_http_upgrade(ptr {obj_val})")
                        self._release_owned_receiver(callee.obj, obj_val, obj_type, lines)
                        return "0", None
                    fn, ret_ir, ret_type = {
                        "toBlob": ("festina_http_to_blob", "ptr", BLOB),
                        "toImg": ("festina_http_to_img", "ptr", types_mod.ImageType()),
                        "toAud": ("festina_http_to_aud", "ptr", types_mod.AudioType()),
                        "toText": ("festina_http_to_text", "ptr", TEXT),
                    }[callee.prop]
                    if callee.prop == "toImg":
                        self.uses_graphics_code = True
                    if callee.prop == "toAud":
                        self.uses_audio = True
                    out = self.tmp()
                    lines.append(f"  {out} = call {ret_ir} @{fn}(ptr {obj_val})")
                    self._release_owned_receiver(callee.obj, obj_val, obj_type, lines)
                    return out, ret_type
            # claude.md #151: req.send(data:any, code:int, headers:map)
            # -- see semantic.py's own bespoke `send` branch for why
            # this can't be a fixed-shape dict entry like the ones just
            # above (an optional code/headers pair, and an any-typed
            # first argument).
            if callee.prop == "send":
                # claude.md #164: `{...}.send()` -- the receiver ITSELF
                # is a raw MapLit (semantic.py's own matching check
                # already confirmed this can only mean an http
                # literal -- sockets have no literal syntax at all) --
                # built via the exact same _emit_http_lit an
                # `http x = {...}` VarDecl already uses, bypassing the
                # generic _emit_expr(callee.obj, ...) entirely, since
                # that path has no notion of "build an http value" for
                # a bare MapLit (see _emit_http_lit's own doc comment).
                # `http {...}` (parser.py's own statement-level
                # shorthand) desugars to this identical AST shape, so
                # this one branch covers both spellings.
                if isinstance(callee.obj, ast.MapLit):
                    obj_val, obj_type = self._emit_http_lit(callee.obj, env, lines)
                else:
                    obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if isinstance(obj_type, types_mod.HttpType):
                    # claude.md #162: send() is now overloaded by ARITY
                    # -- see semantic.py's own comment on this same
                    # branch for the full reasoning.
                    self.uses_http = True
                    if len(expr.args) == 0:
                        # The CLIENT side -- an outbound request, using
                        # obj_val's own url/method/headers/body,
                        # mutating it in place with the response.
                        # https:// support means every program reaching
                        # this branch needs mbedTLS linked, the same
                        # unconditional "the compiler can't know a
                        # runtime string's scheme in advance" reasoning
                        # openSecurePort()'s own uses_https already
                        # established. claude.md #163: dispatches
                        # through festina_http_send_client_dispatch, not
                        # festina_http_send_client directly -- that
                        # function checks obj_val's own `.callback` at
                        # RUNTIME (codegen has no way to know it in
                        # advance) and decides blocking vs. a background
                        # worker from there; either way this call
                        # returns immediately from codegen's own point
                        # of view, since the blocking case is just as
                        # synchronous as it always was, one call deeper.
                        self.uses_https = True
                        lines.append(f"  call void @festina_http_send_client_dispatch(ptr {obj_val})")
                        self._release_http_send_receiver(callee.obj, obj_val, lines)
                        return "0", None
                    # The SERVER side -- send obj_val's own live
                    # connection a response built from expr.args[0], an
                    # http value either already in hand or constructed
                    # inline right here (`req.send({...})`) via the
                    # exact same _emit_http_lit an `http x = {...}`
                    # VarDecl already uses (routed through
                    # _emit_value_for with an explicit expected type,
                    # since a bare _emit_expr on a MapLit has no notion
                    # of "build an http value" at all).
                    res_val, res_type = self._emit_value_for(
                        expr.args[0], env, lines, types_mod.HttpType())
                    lines.append(f"  call void @festina_http_send(ptr {obj_val}, ptr {res_val})")
                    if self._is_owning_refcounted_source(expr.args[0]) or isinstance(expr.args[0], ast.MapLit):
                        # claude.md #162: an inline literal is ALWAYS a
                        # fresh, owned value (see _emit_http_lit) even
                        # though it isn't an ast.Call/etc.
                        # _is_owning_refcounted_source itself would
                        # recognize -- that predicate was never taught
                        # about MapLit since no other http-adjacent
                        # value could be built from one before this
                        # entry.
                        lines.append(f"  call void {self._release_fn_for(res_type)}(ptr {res_val})")
                    self._release_http_send_receiver(callee.obj, obj_val, lines)
                    return "0", None
                if isinstance(obj_type, types_mod.SocketType):
                    # claude.md #151: blob sends as a binary frame,
                    # everything else as a text frame -- _to_text
                    # (inside _emit_sendable_body) already gives every
                    # non-blob sendable type a text form.
                    self.uses_http = True
                    data_val, data_type = self._emit_expr(expr.args[0], env, lines)
                    if data_type == BLOB:
                        len_ptr = self.tmp()
                        lines.append(f"  {len_ptr} = alloca i64")
                        data_ptr = self.tmp()
                        lines.append(f"  {data_ptr} = call ptr @festina_blob_bytes(ptr {data_val}, ptr {len_ptr})")
                        len_val = self.tmp()
                        lines.append(f"  {len_val} = load i64, ptr {len_ptr}")
                        lines.append(
                            f"  call void @festina_socket_send_binary(ptr {obj_val}, "
                            f"ptr {data_ptr}, i64 {len_val})")
                    else:
                        text_val = self._to_text(data_val, data_type, lines)
                        lines.append(f"  call void @festina_socket_send_text(ptr {obj_val}, ptr {text_val})")
                        if data_type != TEXT:
                            lines.append(f"  call void @free(ptr {text_val})")
                    if _is_refcounted(data_type) and self._is_owning_refcounted_source(expr.args[0]):
                        lines.append(f"  call void {self._release_fn_for(data_type)}(ptr {data_val})")
                    else:
                        self._free_text_temp(expr.args[0], data_val, data_type, lines)
                    self._release_owned_receiver(callee.obj, obj_val, obj_type, lines)
                    return "0", None
            if callee.prop == "close":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if isinstance(obj_type, types_mod.SocketType):
                    self.uses_http = True
                    lines.append(f"  call void @festina_socket_close(ptr {obj_val})")
                    self._release_owned_receiver(callee.obj, obj_val, obj_type, lines)
                    return "0", None
            # claude.md #109: aud.stop() is back, clip-wide -- see the
            # runtime's own note on why #100 removed it and why that
            # reasoning did not survive play() returning a channel.
            if callee.prop == "stop":
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if obj_type == AUDIO:
                    lines.append(f"  call void @festina_audio_stop_clip(ptr {obj_val})")
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
                    # own storage, not the header value itself -- GEP+
                    # load per field, same as _emit_map_get. claude.md
                    # #175: festina_map_for_each scans by capacity, not
                    # count, so no count GEP is needed here at all.
                    entries_ptr = self.tmp()
                    lines.append(f"  {entries_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 1")
                    entries = self.tmp()
                    lines.append(f"  {entries} = load ptr, ptr {entries_ptr}")
                    capacity_ptr = self.tmp()
                    lines.append(f"  {capacity_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 2")
                    capacity = self.tmp()
                    lines.append(f"  {capacity} = load i64, ptr {capacity_ptr}")
                    lines.append(
                        f"  call void @festina_map_for_each(ptr {entries}, i64 {capacity}, ptr {trampoline_name})")
                    return "0", None
            # claude.md #186 (uraikus/festina#76 item 7): map[T].keys()
            # -> arr[text], map[T].values() -> arr[T]. No receiver
            # release here, matching forEach's own precedent just above
            # (every existing call site is a plain named map variable,
            # never a chained call-result temporary).
            if callee.prop in ("keys", "values"):
                obj_val, obj_type = self._emit_expr(callee.obj, env, lines)
                if isinstance(obj_type, types_mod.MapType):
                    entries_ptr = self.tmp()
                    lines.append(f"  {entries_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 1")
                    entries = self.tmp()
                    lines.append(f"  {entries} = load ptr, ptr {entries_ptr}")
                    capacity_ptr = self.tmp()
                    lines.append(f"  {capacity_ptr} = getelementptr {FESTINA_MAP_LLVM_TYPE}, ptr {obj_val}, i32 0, i32 2")
                    capacity = self.tmp()
                    lines.append(f"  {capacity} = load i64, ptr {capacity_ptr}")
                    dst = self._emit_fresh_heap_header(FESTINA_ARRAY_LLVM_TYPE, lines)
                    if callee.prop == "keys":
                        lines.append(
                            f"  call void @festina_map_keys(ptr {entries}, i64 {capacity}, ptr {dst})")
                        return dst, types_mod.ArrayType(TEXT)
                    elem_type = obj_type.value
                    elem_size = _elem_size(elem_type)
                    is_refcounted = 1 if _is_refcounted(elem_type) else 0
                    is_text = 1 if elem_type == TEXT else 0
                    lines.append(
                        f"  call void @festina_map_values(ptr {entries}, i64 {capacity}, "
                        f"i64 {elem_size}, i8 {is_refcounted}, i8 {is_text}, ptr {dst})")
                    return dst, types_mod.ArrayType(elem_type)
        if isinstance(callee, ast.Member):
            # claude.md #141: an indirect call through a func[...]:...
            # -typed struct field (h.cb(...)), array element
            # (fns[i](...)), or map value (handlers[key](...)) --
            # covers BOTH computed (bracket) and non-computed (dot)
            # member access, neither of which was specially dispatched
            # above for a callee that turns out to be a stored function
            # VALUE rather than one of this file's own recognized
            # method names. Checked LAST, right before the final "only
            # calls to named functions" fallback below (never reached
            # by a computed Member at all, since every branch above
            # this one is gated `and not callee.computed`) -- an
            # established special method name (img.drawRect,
            # Math.floor, ...) is never shadowed by this generic path.
            # _emit_expr on the whole Member node reads the callee's
            # VALUE exactly the same way any other expression position
            # would (`func[text]:void tmp = h.cb` uses the identical
            # code path) -- no special-casing needed for struct/array/
            # map access specifically, since they're already ordinary,
            # type-generic reads.
            callee_val, callee_type = self._emit_expr(callee, env, lines)
            if isinstance(callee_type, types_mod.FuncType):
                fn_type = callee_type
                arg_vals = []
                arg_temps = []
                for arg_expr, ptype in zip(expr.args, fn_type.param_types):
                    val, vtype = self._emit_value_for(arg_expr, env, lines, ptype)
                    val = self._coerce(val, vtype, ptype, lines)
                    arg_vals.append(f"{_llvm_type(ptype)} {val}")
                    arg_temps.append((arg_expr, val, vtype, ptype))
                args_ir = ", ".join(arg_vals)
                ret_type = fn_type.return_type
                if ret_type is None:
                    lines.append(f"  call void {callee_val}({args_ir})")
                    out = "0"
                else:
                    out = self.tmp()
                    lines.append(f"  {out} = call {_llvm_type(ret_type)} {callee_val}({args_ir})")
                # claude.md #83/#119: identical post-call argument
                # cleanup to the other two call forms above.
                for arg_expr, val, vtype, ptype in arg_temps:
                    if (_is_refcounted(ptype)
                            and self._is_owning_refcounted_source(arg_expr)):
                        lines.append(
                            f"  call void {self._release_fn_for(ptype)}(ptr {val})")
                    else:
                        self._free_text_temp(arg_expr, val, vtype, lines)
                return out, ret_type
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
        follows (e.g. festina_register_mouse_down_handler's fixed
        `void(i64,i64)` signature, matched exactly by semantic.py's
        _EVENT_SIGNATURES check on `on mouseDown`'s own declared
        params)."""
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
        # claude.md #111: `free` on a regex binding must not free THIS --
        # it is shared with every later execution of this line. The value
        # itself carries the answer, so festina_regex_free can no-op.
        lines.append(f"  call void @festina_regex_mark_cached(ptr {compiled})")
        lines.append(f"  store ptr {compiled}, ptr {cache_global}")
        compile_pred = self.cur_block
        lines.append(f"  br label %{done_label}")

        self._start_block(done_label, lines)
        out = self.tmp()
        lines.append(f"  {out} = phi ptr [ {loaded}, %{load_pred} ], [ {compiled}, %{compile_pred} ]")
        return out

    # ---- regex() / .test() / .match() / .replace() (claude.md #67-68, #107) ----
    def _emit_regex_call(self, expr, env, lines):
        """claude.md #67: regex(pattern:text) / regex(pattern:text,
        flags:text) -> regex.

        claude.md #118: compiled through a per-call-site MEMO
        (festina_regex_compile_memo) rather than a bare recompile per
        evaluation. The pattern is an arbitrary runtime expression, so
        the same call site can legitimately see a different pattern on
        different calls -- which is why plain per-AST-node caching (what
        /pattern/ literals get, see _emit_cached_regex_lit above) would
        have been a correctness bug, and why this stayed uncached for so
        long. The memo closes the gap from the other side: the runtime
        compares the ACTUAL pattern+flags against what this site
        compiled last time, reuses the compilation on a match (the
        common case -- a fixed pattern built from config, evaluated in a
        loop; measured ~24x cheaper than recompiling) and recompiles on
        a mismatch, so a genuinely varying pattern is never served a
        stale automaton. Evicting the superseded compilation is only
        safe because regex is refcounted now (#118): a binding still
        aliasing the old one keeps it alive.

        An invalid pattern fails at runtime (festina_fail(), via the C
        runtime's regcomp() error handling), not at compile time -- the
        Python compiler doesn't itself validate regex syntax (claude.md
        #67's own words)."""
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
        # claude.md #118: one private {pattern copy, flags copy,
        # compiled} slot per regex() call site -- the runtime memo's
        # working storage. Keyed by AST node identity, the same scheme
        # _regex_lit_cache uses for literals.
        key = id(expr)
        memo_global = self._regex_memo_slots.get(key)
        if memo_global is None:
            memo_global = f"@.regex.memo.{len(self._regex_memo_slots)}"
            self._regex_memo_slots[key] = memo_global
            self.extra_globals.append(
                f"{memo_global} = private global [3 x ptr] zeroinitializer")
        out = self.tmp()
        lines.append(f"  {out} = call ptr @festina_regex_compile_memo("
                     f"ptr {pattern_val}, ptr {flags_val}, ptr {memo_global})")
        # claude.md #83: the memo strdups the pattern/flags it keeps and
        # regcomp() reads them inline -- neither caller pointer is
        # retained past this call, so a temporary passed for either is
        # the caller's to free.
        self._free_text_temp(expr.args[0], pattern_val, pattern_type, lines)
        if len(expr.args) > 1:
            self._free_text_temp(expr.args[1], flags_val, flags_type, lines)
        return out, REGEX

    def _emit_color_value(self, decl_or_expr, text_value, line, what):
        """claude.md #91: a colour LITERAL -> the packed 0xRRGGBB integer
        a `color` value is. Negative means 'none'.

        Packing is what makes a colour cost one register instead of
        three, and makes `a == b` on two colours a single integer
        compare. Unpacking is three shift/mask pairs in the runtime,
        done once per fillStyle() call rather than per pixel."""
        resolved = colors_mod.resolve_color(text_value)
        if resolved is None:
            raise CodegenError(
                f"{what}: '{text_value}' is not a colour Festina understands "
                f"-- use a CSS colour name (red, rebeccapurple, ...), a #rgb "
                f"or #rrggbb hex value, or 'none' for no colour at all",
                file=self.filename, line=line)
        r, g, b = resolved
        if (r, g, b) == colors_mod.NO_COLOR:
            return "-1"
        return str((r << 16) | (g << 8) | b)

    def _emit_font_constant(self, text_value, line, what):
        """claude.md #91: a font LITERAL -> a pointer to a static
        %struct._FestinaFont constant holding its already-resolved
        parts.

        The whole record lands in the binary's read-only data, so
        declaring a font costs no code at all at runtime and
        changeFont() passes a single pointer. Identical literals share
        one constant (cached by their resolved parts, not by source
        text, so 'bold 13px arial' and 'arial bold 13px' collapse
        together). Nothing allocates or frees this -- see FontType's
        own comment."""
        px, style, family = colors_mod.parse_font(text_value)
        if px is None and style is None and family is None:
            raise CodegenError(
                f"{what}: '{text_value}' says nothing about a font -- give at "
                f"least a size (like '14px'), a family (like 'arial'), or a "
                f"style (like 'bold')",
                file=self.filename, line=line)
        key = (px, style, family)
        cached = self._font_constants.get(key)
        if cached is not None:
            return cached
        name = f"@.font.{len(self._font_constants)}"
        self._font_constants[key] = name
        slant = 1 if style and "italic" in style else 0
        weight = 1 if style and "bold" in style else 0
        family_ref = self.string_const(family) if family else "null"
        self.extra_globals.append(
            f"{name} = private constant {FESTINA_FONT_LLVM_TYPE} "
            f"{{ i64 {px or 0}, i64 {slant}, i64 {weight}, ptr {family_ref} }}")
        return name

    # ---- graphics: drawRect/drawPixel/drawCircle/drawText/drawImage/loadImage (claude.md #37, #39, #133) ----
    def _emit_graphics_call(self, name, expr, env, lines):
        """Draws onto (or loads an image for) the graphics canvas.
        Sets self.uses_graphics so main() knows to open the canvas
        window before __festina_main() runs, and enter the event loop
        after it returns (see _emit_main_and_entry) -- exactly the same
        "only pay for what you use" pattern self.uses_sqlite already
        follows for festina_db_open(). semantic.py has already checked
        each function's argument count/types against claude.md's own
        worked examples (claude.md #37, #39); claude.md #133 added one
        exception -- drawRect/drawPixel each have a second, longer form
        with a trailing `color` argument, dispatched here purely by
        arity (`len(args)`), the same way fillStyle/borderColor/
        changeFont's own 1-vs-3-argument forms already are, just below.

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
        emitted = [self._emit_expr(a, env, lines) for a in expr.args]
        args = [val for val, _ in emitted]

        def free_text_temps():
            # claude.md #83: Cairo copies the glyphs it draws and reads
            # the PNG path inline -- neither festina_draw_text nor
            # festina_load_image retains the pointer it was handed, so a
            # temporary passed to either is the caller's to free.
            for arg_expr, (val, vtype) in zip(expr.args, emitted):
                self._free_text_temp(arg_expr, val, vtype, lines)

        if name == "loadImage":
            out = self.tmp()
            lines.append(f"  {out} = call ptr @festina_load_image(ptr {args[0]})")
            free_text_temps()
            return out, types_mod.ImageType()

        # claude.md #188 (uraikus/festina#76 item 4): blankImage(w, h)
        # -> img. Shares loadImage's own reasoning for NOT setting
        # self.uses_graphics -- creating a Cairo surface needs no X
        # server, unlike drawing onto a window.
        if name == "blankImage":
            out = self.tmp()
            lines.append(f"  {out} = call ptr @festina_blank_image(i64 {args[0]}, i64 {args[1]})")
            return out, types_mod.ImageType()

        # claude.md #189: getPixelColor(x, y) -> color. Reads the
        # canvas's own backing store directly, so (like every other
        # function in this method) it needs no window, only Cairo.
        if name == "getPixelColor":
            out = self.tmp()
            lines.append(f"  {out} = call i64 @festina_get_pixel_color(i64 {args[0]}, i64 {args[1]})")
            return out, types_mod.ColorType()

        # claude.md #89: style setters and text metrics deliberately do
        # NOT set self.uses_graphics, for the same reason loadImage()
        # doesn't (see this method's own docstring): none of them draws
        # anything, so none of them needs a canvas window to exist. A
        # program that only measures text, or only sets a fill colour it
        # never draws with, should not have a window opened on it -- and
        # measuring genuinely works with no X server at all, since text
        # metrics depend only on the font (festina_measure_text_* run
        # against a scratch image surface).
        if name in ("fillStyle", "borderColor"):
            # claude.md #91: one argument is a `color` value -- already
            # packed, whether it came from a declaration's own literal or
            # from another colour-typed binding. Three are raw channels,
            # for a colour chosen at runtime.
            if len(expr.args) == 1:
                fn = ("festina_set_fill_color" if name == "fillStyle"
                      else "festina_set_border_color")
                lines.append(f"  call void @{fn}(i64 {args[0]})")
            else:
                fn = ("festina_set_fill_rgb" if name == "fillStyle"
                      else "festina_set_border_rgb")
                lines.append(
                    f"  call void @{fn}(i64 {args[0]}, i64 {args[1]}, i64 {args[2]})")
            free_text_temps()
            return "0", None
        if name == "changeFont":
            # claude.md #91: one argument is a `font` value (a pointer to
            # its static record); three are the explicit parts, for a
            # font whose size is computed at runtime.
            if len(expr.args) == 1:
                lines.append(f"  call void @festina_set_font_value(ptr {args[0]})")
            else:
                lines.append(
                    f"  call void @festina_set_font(i64 {args[0]}, ptr {args[1]}, "
                    f"ptr {args[2]})")
            free_text_temps()
            return "0", None
        if name == "lineWidth":
            lines.append(f"  call void @festina_set_line_width(i64 {args[0]})")
            free_text_temps()
            return "0", None
        if name in ("measureTextWidth", "measureTextHeight"):
            fn = ("festina_measure_text_width" if name == "measureTextWidth"
                  else "festina_measure_text_height")
            out = self.tmp()
            lines.append(f"  {out} = call i64 @{fn}(ptr {args[0]})")
            free_text_temps()
            return out, INT

        # claude.md #95: drawing paints the OFFSCREEN canvas -- it needs
        # no window, so it deliberately does not open one. render() is
        # the single call that does. That is what lets a program draw
        # and saveCanvas() with no display present at all.
        if name == "drawRect":
            # claude.md #133: a 5th argument is an optional trailing
            # `color` -- paint with it for this call only, instead of
            # the current fillStyle. claude.md #188 (uraikus/
            # festina#76 item 8): a 6th is a further optional trailing
            # BORDER colour, same "this call only" override.
            if len(args) == 6:
                x, y, w, h, fill, border = args
                lines.append(
                    f"  call void @festina_draw_rect_colors(i64 {x}, i64 {y}, "
                    f"i64 {w}, i64 {h}, i64 {fill}, i64 {border})")
            elif len(args) == 5:
                x, y, w, h, color = args
                lines.append(
                    f"  call void @festina_draw_rect_color(i64 {x}, i64 {y}, "
                    f"i64 {w}, i64 {h}, i64 {color})")
            else:
                x, y, w, h = args
                lines.append(f"  call void @festina_draw_rect(i64 {x}, i64 {y}, i64 {w}, i64 {h})")
        elif name == "drawPixel":
            # claude.md #133: same optional-trailing-`color` shape as
            # drawRect just above.
            if len(args) == 3:
                x, y, color = args
                lines.append(
                    f"  call void @festina_draw_pixel_color(i64 {x}, i64 {y}, i64 {color})")
            else:
                x, y = args
                lines.append(f"  call void @festina_draw_pixel(i64 {x}, i64 {y})")
        elif name == "drawCircle":
            # claude.md #188 (uraikus/festina#76 item 8): the same
            # fill/fill+border trailing-colour forms drawRect has,
            # newly -- drawCircle previously took no colour override.
            if len(args) == 5:
                x, y, r, fill, border = args
                lines.append(
                    f"  call void @festina_draw_circle_colors(i64 {x}, i64 {y}, "
                    f"i64 {r}, i64 {fill}, i64 {border})")
            elif len(args) == 4:
                x, y, r, color = args
                lines.append(
                    f"  call void @festina_draw_circle_color(i64 {x}, i64 {y}, "
                    f"i64 {r}, i64 {color})")
            else:
                x, y, r = args
                lines.append(f"  call void @festina_draw_circle(i64 {x}, i64 {y}, i64 {r})")
        elif name == "drawText":
            text, x, y = args
            lines.append(f"  call void @festina_draw_text(ptr {text}, i64 {x}, i64 {y})")
        elif name == "drawImage":
            # claude.md #185 (uraikus/festina#76 item 3): three shapes,
            # picked by argument count -- semantic.py's own
            # _BUILTIN_SIGNATURE_ALTERNATES entry already confirmed
            # exactly one of these matches.
            if len(args) == 3:
                img, x, y = args
                lines.append(f"  call void @festina_draw_image(ptr {img}, i64 {x}, i64 {y})")
            elif len(args) == 5:
                img, x, y, w, h = args
                lines.append(
                    f"  call void @festina_draw_image_scaled(ptr {img}, "
                    f"i64 {x}, i64 {y}, i64 {w}, i64 {h})")
            else:
                img, sx, sy, sw, sh, dx, dy, dw, dh = args
                lines.append(
                    f"  call void @festina_draw_image_region(ptr {img}, "
                    f"i64 {sx}, i64 {sy}, i64 {sw}, i64 {sh}, "
                    f"i64 {dx}, i64 {dy}, i64 {dw}, i64 {dh})")
        free_text_temps()
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
    def _emit_sqlite_prepare(self, expr, sql_val, db_val, lines):
        """claude.md #113: chooses per call site between the one-shot
        prepare and the cached one. A compile-time string literal can
        never change, so re-parsing it into sqlite bytecode on every
        call is pure waste -- the identical reasoning behind claude.md
        #85's regex literal cache, implemented the same way: one private
        global slot per call site, filled on first reach. Anything
        dynamic (a template, a variable) keeps the per-call prepare,
        because the same site can see different SQL each time. Measured:
        20,000 one-row SELECTs went from 164ms to 106ms."""
        stmt_val = self.tmp()
        if isinstance(expr.args[0], ast.StringLit):
            uid = self._unique()
            slot = f"@__festina_stmtcache_{uid}"
            self.extra_globals.append(f"{slot} = private global ptr null")
            lines.append(
                f"  {stmt_val} = call ptr @festina_sqlite_prepare_cached("
                f"ptr {db_val}, ptr {sql_val}, ptr {slot})")
        else:
            lines.append(
                f"  {stmt_val} = call ptr @festina_sqlite_prepare(ptr {db_val}, ptr {sql_val})")
        return stmt_val

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
        stmt_val = self._emit_sqlite_prepare(expr, sql_val, db_val, lines)
        # claude.md #83: sqlite3_prepare_v2 compiles the SQL into the
        # statement rather than holding the string, so a temporary
        # (`sqlite(`SELECT ... ${n}`)`) is the caller's to free here.
        self._free_text_temp(expr.args[0], sql_val, sql_type, lines)

        if len(expr.args) > 1:
            self._emit_sqlite_bind_params(expr.args[1], stmt_val, env, lines)

        table_type = expected_type.element if isinstance(expected_type, types_mod.ArrayType) else None
        if isinstance(table_type, types_mod.TableType):
            arr_val = self._emit_sqlite_collect(stmt_val, table_type, lines)
            return arr_val, expected_type
        if isinstance(table_type, types_mod.StructType):
            # claude.md #112: a STRUCT as the landing spot -- the shape
            # for `SELECT id AS whatever`, a JOIN, or any computed
            # column: name the fields after the result's own column
            # names, no table (and no CREATE TABLE side effect, which a
            # `table` declaration always carries) required.
            arr_val = self._emit_sqlite_collect_struct(stmt_val, table_type, lines,
                                                       line=callee.line)
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
                # claude.md #83: bound with SQLITE_TRANSIENT, so sqlite
                # has taken its own copy by the time this returns and a
                # temporary is safe (and necessary) to free right here.
                self._free_text_temp(elem, val, vtype, lines)
            elif vtype == BOOL:
                # claude.md #30: bool maps to SQLite INTEGER, same as int.
                # An already-null bool (BOOL_NULL_CONST) binds as plain
                # 2 here rather than a real SQL NULL -- same "unresolved"
                # territory as using one in further boolean logic (see
                # the module docstring), not a new special case.
                z = self.tmp()
                lines.append(f"  {z} = zext i8 {val} to i64")
                lines.append(f"  call void @festina_sqlite_bind_int(ptr {stmt_val}, i32 {idx}, i64 {z})")
            elif isinstance(vtype, (types_mod.AudioType, types_mod.ImageType)) or vtype == BLOB:
                # claude.md #101: an aud/img binds as its own encoded
                # bytes, so `sqlite('... VALUES (?)', [track])` stores
                # the file rather than a pointer. The accessor lives in
                # the graphics/audio translation unit, which is linked
                # because the program already holds one of these values.
                #
                # claude.md #109: a blob binds the same way, and is the
                # reason this branch reads naturally rather than as a
                # special case -- all three are "content plus the bytes
                # it came from", so all three store their bytes. A blob
                # needs no uses_* flag: its accessor is in the core
                # runtime, always linked.
                is_audio = isinstance(vtype, types_mod.AudioType)
                if vtype == BLOB:
                    fn = "festina_blob_bytes"
                elif is_audio:
                    self.uses_audio = True
                    fn = "festina_audio_bytes"
                else:
                    self.uses_graphics_code = True
                    fn = "festina_image_bytes"
                len_slot = self.tmp()
                lines.append(f"  {len_slot} = alloca i64")
                lines.append(f"  store i64 0, ptr {len_slot}")
                data = self.tmp()
                lines.append(f"  {data} = call ptr @{fn}(ptr {val}, ptr {len_slot})")
                blob_len = self.tmp()
                lines.append(f"  {blob_len} = load i64, ptr {len_slot}")
                lines.append(
                    f"  call void @festina_sqlite_bind_blob(ptr {stmt_val}, i32 {idx}, "
                    f"ptr {data}, i64 {blob_len})")
            else:
                raise CodegenError(
                    "sqlite() parameters must be int/float/bool/text/blob/aud/img/null, "
                    f"found {types_mod.type_name(vtype)}",
                    file=self.filename, line=getattr(elem, "line", 0))

    def _emit_sqlite_scalar(self, name, expr, env, lines):
        """claude.md #94: sqliteInt/sqliteFloat/sqliteText -- one value
        out of a query, with no `table` declaration to hold it.

        Shares _emit_sqlite_call's own prepare-and-bind path exactly;
        only the stepping differs, taking the first column of the first
        row instead of collecting rows into an array. That matters
        because a `table` declaration CREATES a table (claude.md
        #28-31), so before this, asking for a `count(*)` meant leaving a
        throwaway table behind in the database."""
        self.uses_sqlite = True
        callee = expr.callee
        if not expr.args:
            raise CodegenError(f"{name}() requires a SQL string argument",
                                file=self.filename, line=callee.line)
        sql_val, sql_type = self._emit_expr(expr.args[0], env, lines)
        if sql_type != TEXT:
            raise CodegenError(
                f"{name}()'s first argument must be text, found "
                f"{types_mod.type_name(sql_type)}",
                file=self.filename, line=callee.line)
        db_val = self.tmp()
        lines.append(f"  {db_val} = load ptr, ptr @__festina_db")
        # claude.md #113: same literal-SQL statement cache the array
        # query path uses -- sqliteInt('SELECT count(*) ...') in a loop
        # is exactly the shape that pays for re-preparing.
        stmt_val = self._emit_sqlite_prepare(expr, sql_val, db_val, lines)
        self._free_text_temp(expr.args[0], sql_val, sql_type, lines)
        if len(expr.args) > 1:
            self._emit_sqlite_bind_params(expr.args[1], stmt_val, env, lines)
        fn, ret_ir, ret_type = {
            "sqliteInt": ("festina_sqlite_scalar_int", "i64", INT),
            "sqliteFloat": ("festina_sqlite_scalar_float", "double", FLOAT),
            "sqliteText": ("festina_sqlite_scalar_text", "ptr", TEXT),
        }[name]
        out = self.tmp()
        lines.append(f"  {out} = call {ret_ir} @{fn}(ptr {stmt_val})")
        return out, ret_type

    def _emit_sqlite_collect(self, stmt_val, table_type, lines):
        table_name = table_type.name
        cols = self.tables[table_name]
        names_global, types_global, ncols = self._table_arrays(table_name, cols)

        n_slot = self.tmp()
        lines.append(f"  {n_slot} = alloca i64")
        data_slot = self.tmp()
        lines.append(f"  {data_slot} = alloca ptr")
        # claude.md #188 (uraikus/festina#76 item 5): want_rowid=1 --
        # only a TableType-shaped row (never a struct-query row, see
        # _emit_sqlite_collect_struct's own call just below) carries a
        # `.rowid` slot at all.
        lines.append(
            f"  call void @festina_sqlite_collect_rows(ptr {stmt_val}, i32 {ncols}, "
            f"ptr {types_global}, ptr {names_global}, ptr {n_slot}, ptr {data_slot}, i8 1)"
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
        # claude.md #97: claude.md #74's scope tracking now covers the
        # top-level statements too, not just function/handler bodies.
        # A top-level VarDecl is a GLOBAL (an `@name`, emitted by
        # _emit_toplevel_stmt, which never consults this) and is
        # unaffected -- what this reaches is a local declared inside a
        # NESTED block at top level, `text row = a + b` in a top-level
        # `while` body, which _emit_block emits as an ordinary alloca
        # and, with tracking off, never freed: one leaked buffer per
        # iteration, in exactly the shape a Festina game loop is
        # written in. The analysis input is the same whole-body name
        # set every function gets, computed over the top-level
        # statement list; `escaping_params` carries over so a call
        # argument already proven safe stays safe here too.
        self._current_escaping_names = escape_analysis.find_escaping_names(
            _StmtList(self.entry_stmts), escaping_params=self.escaping_params)
        try:
            for stmt in self.entry_stmts:
                self.filename = getattr(stmt, "file", self.filename)  # see generate()'s note
                self._emit_toplevel_stmt(stmt, env, entry_ctx)
        finally:
            self._current_escaping_names = None
        if not entry_ctx["terminated"]:
            lines.append("  ret void")

        entry_func = ["define void @__festina_main() {"]
        entry_func.append("entry:")
        entry_func.extend(lines)
        entry_func.append("}")

        # claude.md #150: real argc/argv, unconditionally -- every
        # target (native and wasm32-wasi both) always gets a program's
        # own real invocation arguments now, not just the wasm bridge's
        # own __main_argc_argv path this signature also happens to
        # satisfy (see runtime/festina_runtime_wasm_entry.c). Native
        # linking needs no bridge at all: `define i32 @main(...)` with
        # this exact signature already IS the real C ABI entry point on
        # every native target, argc/argv included, so this changes
        # nothing about how native main() gets found or called.
        main_lines = ["define i32 @main(i32 %argc, ptr %argv_raw) {", "entry:"]
        # windows.md Phase 0 (claude.md #126): unconditional, first
        # thing main() does, before even the database/graphics setup
        # below -- see festina_runtime_init's own comment for why.
        main_lines.append("  call void @festina_runtime_init()")
        # claude.md #150: argv -- built from the real argc/argv above
        # and stored directly into @argv (generate()'s own
        # pre-registration), deliberately WITHOUT going through
        # _emit_global_retain_release the way an ordinary global's
        # declaration-with-initializer does. That helper is the right
        # tool when either side of the store is uncertain -- here
        # neither is: festina_argv_array's own return is always a
        # freshly built, uniquely-owned array (nothing else has ever
        # seen this pointer, so no retain is needed -- ownership just
        # transfers straight into @argv's slot), and @argv's own
        # current value at this point is always its untouched static
        # initializer -- the immortal, refcount=-1, zeroinitializer-
        # payload sentinel every refcounted global gets (this is
        # provably main()'s first-ever write to @argv, before any user
        # code has run) -- so there's nothing to release either, not
        # even a header-level no-op. Going through the generic helper
        # anyway would still be correct, but it unconditionally emits a
        # call to the arr[text] release wrapper (_release_fn_for_array),
        # which then has to exist in EVERY compiled program's own IR
        # even when nothing else in that program ever touches
        # arr[text] -- real, avoidable code-size/output-shape noise for
        # a release that can never do anything (the sentinel's own
        # length is always 0). Before anything else in main() runs, so
        # top-level code can read argv as its very first statement.
        main_lines.append("  %argv_arr = call ptr @festina_argv_array(i32 %argc, ptr %argv_raw)")
        main_lines.append("  store ptr %argv_arr, ptr @argv")
        # claude.md #131: `on exit(code:int)` registers unconditionally
        # (with or without graphics) and before anything else runs, so
        # close(code) can fire the handler even from code that executes
        # before a window would otherwise be set up.
        if self.exit_handler_symbol is not None:
            main_lines.append(f"  call void @festina_register_exit_handler(ptr {self.exit_handler_symbol})")
        # claude.md #161: graceful shutdown -- installed ONLY when this
        # program has one of the three blocking loops just below, each
        # of which already polls festina_shutdown_requested() once per
        # ordinary iteration (see festina_runtime.h's own doc comment).
        # Deliberately NOT gated on self.exit_handler_symbol alone: a
        # program that declares `on exit(code:int)` but has no
        # http/timers/graphics loop -- say, its own hand-written
        # `while (true) { ... }` at top level -- has no point in its
        # own execution that could ever check the flag either. Installing
        # the handler there wouldn't just skip running the exit handler
        # (today's existing behavior); it would make Ctrl-C/SIGTERM stop
        # working AT ALL for such a program (the signal sets the flag and
        # returns control right back into the same un-checking loop,
        # silently swallowing what used to be an immediate kill) --
        # confirmed directly, and a strictly worse regression than the
        # gap this feature is closing. Only install where a poll point
        # is actually guaranteed to run soon.
        if self.uses_graphics or self.uses_http or self.uses_timers:
            main_lines.append("  call void @festina_install_shutdown_handler()")
        # claude.md #151: `on request`/`on upgrade`/`on message`/
        # `on socketClose` -- NOT graphics events either, same
        # unconditional-registration shape as `exit` just above (an
        # http/websocket connection has nothing to do with a window).
        if self.http_request_handler_symbol is not None:
            main_lines.append(
                f"  call void @festina_register_request_handler(ptr {self.http_request_handler_symbol})")
        if self.http_upgrade_handler_symbol is not None:
            main_lines.append(
                f"  call void @festina_register_upgrade_handler(ptr {self.http_upgrade_handler_symbol})")
        if self.http_message_handler_symbol is not None:
            main_lines.append(
                f"  call void @festina_register_message_handler(ptr {self.http_message_handler_symbol})")
        if self.http_socketclose_handler_symbol is not None:
            main_lines.append(
                f"  call void @festina_register_socketclose_handler(ptr {self.http_socketclose_handler_symbol})")
        # claude.md #160: unconditional-when-used, same shape as the
        # four http handler registrations just above -- deliberately
        # NOT nested inside the "if self.tables or self.uses_sqlite"
        # block below (a first draft of this put it there, alongside
        # the audio/image decoder hooks, and a table-less/sqlite-less
        # openSecurePort() program silently never registered its TLS
        # hooks at all -- confirmed directly: the compiled program
        # exited immediately instead of listening, since that whole
        # block, database open included, is skipped for a program with
        # no tables and no sqlite() call). openSecurePort() has nothing
        # to do with SQLite; only self.uses_https (set by its own
        # dispatch branch) should gate this.
        if self.uses_https:
            main_lines.append("  call void @festina_register_tls_hooks()")
        if self.uses_async_io:
            # claude.md #165: same reasoning/placement as
            # festina_register_tls_hooks() just above -- this has
            # nothing to do with SQLite either, gated purely on
            # self.uses_async_io.
            main_lines.append("  call void @festina_register_async_io_hooks()")
        if self.uses_http:
            # claude.md #166: gated purely on self.uses_http, same
            # placement/reasoning as the two hook registrations just
            # above -- registered whether or not this program ALSO
            # uses graphics (harmless either way, see
            # festina_register_http_service_hooks' own doc comment);
            # this is what lets festina_run_event_loop service an open
            # port when main() picks it as the one blocking loop below.
            main_lines.append("  call void @festina_register_http_service_hooks()")
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
            # claude.md #101: register the media decoders BEFORE any
            # query can run, so a table with an aud/img column can turn
            # a stored BLOB back into a handle. Emitted here rather than
            # called by name from the core runtime, which must not
            # reference the graphics/audio translation units at all --
            # that separation is what lets a program using neither link
            # neither. Only emitted when the program already links the
            # feature in question, so the symbol always exists.
            if self.uses_audio:
                main_lines.append(
                    "  call void @festina_set_audio_decoder(ptr @festina_audio_from_bytes)")
            if self.uses_graphics_code or self.uses_graphics:
                main_lines.append(
                    "  call void @festina_set_image_decoder(ptr @festina_image_from_bytes)")
            main_lines.append(f"  %db = call ptr @festina_db_open(ptr {url_val})")
            main_lines.append("  store ptr %db, ptr @__festina_db")
            for tname, cols in self.tables.items():
                names_global, types_global, ncols = self._table_arrays(tname, cols)
                main_lines.append(
                    f"  call void @festina_sync_table(ptr %db, ptr {self.string_const(tname)}, "
                    f"ptr {names_global}, ptr {types_global}, i32 {ncols})"
                )
        if self.uses_graphics:
            # claude.md #178 (uraikus/festina#79): deliberately NOT a
            # call to festina_graphics_init() here -- that used to run
            # unconditionally before __festina_main(), which opened the
            # real window at the hardcoded 800x600 default before the
            # program's own top-level setClientWidth/setClientHeight
            # calls (the documented, `on resize`-safe boot pattern #75
            # recommends) ever got a chance to run, forcing every such
            # program through a visible open-then-resize instead of
            # opening at the right size the first time. Handler
            # registration has no such ordering dependency (it only
            # ever stores a function pointer -- see
            # festina_register_resize_handler and friends in
            # festina_runtime_graphics.c, none of which touch the
            # window), so it still happens here, before
            # __festina_main() runs, same as before. The window itself
            # now opens lazily -- from festina_render()'s own existing
            # guard if the program calls it, or from
            # festina_run_event_loop()'s matching fallback below
            # otherwise -- always reading whatever g_canvas_width/
            # g_canvas_height already are by then, honoring any
            # pre-window setClientWidth/setClientHeight call instead of
            # overwriting it.
            register_fn = {"mouseDown": "festina_register_mouse_down_handler",
                            "mouseUp": "festina_register_mouse_up_handler",
                            "mouse": "festina_register_mouse_handler",
                            "mouseWheelUp": "festina_register_mouse_wheel_up_handler",
                            "mouseWheelDown": "festina_register_mouse_wheel_down_handler",
                            "keyDown": "festina_register_key_down_handler",
                            "keyUp": "festina_register_key_up_handler",
                            "resize": "festina_register_resize_handler",
                            "close": "festina_register_close_handler"}
            for event_name, symbol in self.event_handlers.items():
                main_lines.append(f"  call void @{register_fn[event_name]}(ptr {symbol})")
        main_lines.append("  call void @__festina_main()")
        if self.uses_graphics:
            # claude.md #40's "canvas" only means something while a
            # window is actually open -- block here, after the entry
            # function's own top-level statements have run (so anything
            # drawn there is already visible), handling Expose/mouse/
            # key/resize/close and any pending setTimeout/
            # setInterval callbacks until there's nothing left to wait
            # for (see festina_run_event_loop's own doc comment in
            # festina_runtime.h/graphics.c). festina_run_event_loop lives
            # in the graphics translation unit (X11 select()-based), so
            # it's only ever declared-and-called -- never linked in --
            # for a program that actually opens a window; see cli.py's
            # per-feature object file selection. claude.md #166: also
            # services an open http port, if self.uses_http is ALSO set
            # -- see festina_register_http_service_hooks() just above
            # and festina_run_event_loop's own doc comment in
            # festina_runtime_graphics.c for how.
            main_lines.append("  call void @festina_run_event_loop()")
        elif self.uses_http:
            # claude.md #151: openPort() was called somewhere (or an
            # http/websocket handler was declared) -- festina_run_http_loop
            # is the single-threaded poll()-based loop that services
            # connections AND fires any pending setTimeout/setInterval
            # callbacks (see its own doc comment in festina_runtime.h),
            # so this branch fully subsumes what festina_run_timer_loop
            # below does whenever both are in play -- checked first,
            # not `elif self.uses_http and not self.uses_timers`, since
            # a timers-only program still needs SOME loop when http is
            # also present and http's own loop already covers it. Only
            # reached when self.uses_graphics is false -- claude.md
            # #166: a program using BOTH takes the branch just above
            # instead (festina_run_event_loop services http itself
            # there), so this simpler, non-graphics-aware loop is
            # exactly what a program with no window at all still gets.
            main_lines.append("  call void @festina_run_http_loop()")
        elif self.uses_timers or self.uses_async_io:
            # No window, but setTimeout/setInterval callbacks still need
            # a blocking loop to fire in -- festina_run_timer_loop is the
            # pure-POSIX (nanosleep-based, no X11 at all) equivalent that
            # lives in the core translation unit, so a timers-only
            # program never needs to link the graphics object file just
            # to wait for its callbacks. claude.md #165: `or
            # self.uses_async_io` -- a program using ONLY blob/img/aud's
            # own `.callback()` form (no openPort(), no graphics, not
            # even a timer) still needs SOME loop to wait in for a
            # background load to finish, and festina_run_timer_loop
            # already checks the shared async-io hooks each iteration
            # regardless of why it was entered (see its own doc
            # comment) -- so widening this ONE condition is the whole
            # fix; no new branch needed.
            main_lines.append("  call void @festina_run_timer_loop()")
        # claude.md #126 round nine: unconditional, last thing main()
        # does -- @__festina_db defaults to (and stays) null for a
        # program with no `table` declarations, which festina_db_close
        # treats as a no-op, so this is safe to call every time rather
        # than gated on self.uses_sqlite. See that function's own
        # comment for why an explicit close (not just letting the OS
        # reclaim the fd on exit) matters.
        main_lines.append("  %final_db = load ptr, ptr @__festina_db")
        main_lines.append("  call void @festina_db_close(ptr %final_db)")
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

    def _query_struct_arrays(self, struct_type, line=0):
        """claude.md #112: the struct counterpart of _table_arrays --
        column-name and column-type globals derived from a STRUCT's
        fields, so festina_sqlite_collect_rows can match result columns
        to them by name exactly as it does for a table. This is what
        gives an aliased or computed column a declared landing spot:
        `SELECT id AS whatever` matches a struct field named `whatever`,
        where a table's declared columns can never be renamed to chase
        a query's aliases.

        Field types are restricted to what a query can actually produce
        (the same seven festina_sql_type knows); a struct with an
        arr/map/struct field can hold one in ordinary code but not
        receive one from sqlite, and the error says which field."""
        name = struct_type.name
        cached = self._table_arrays_cache.get("q$" + name)
        if cached is not None:
            return cached
        type_strings = []
        for fname, ftype in self.struct_fields(name):
            if ftype == INT:
                type_strings.append("int")
            elif ftype == FLOAT:
                type_strings.append("float")
            elif ftype == BOOL:
                type_strings.append("bool")
            elif ftype == TEXT:
                type_strings.append("text")
            elif ftype == BLOB:
                type_strings.append("blob")
            elif isinstance(ftype, types_mod.ImageType):
                # The decoder registration in main() keys on this flag,
                # the same as it does for a media table column.
                self.uses_graphics_code = True
                type_strings.append("img")
            elif isinstance(ftype, types_mod.AudioType):
                self.uses_audio = True
                type_strings.append("aud")
            else:
                raise CodegenError(
                    f"struct '{name}' cannot receive a sqlite() result: field "
                    f"'{fname}' is {types_mod.type_name(ftype)}, and a query "
                    f"column can only be int/float/bool/text/blob/img/aud",
                    file=self.filename, line=line)
        fields = self.struct_fields(name)
        names_arr = f"@{name}.qcols"
        types_arr = f"@{name}.qtypes"
        name_ptrs = ", ".join(f"ptr {self.string_const(n)}" for n, _ in fields)
        type_ptrs = ", ".join(f"ptr {self.string_const(t)}" for t in type_strings)
        self.extra_globals.append(
            f"{names_arr} = private constant [{len(fields)} x ptr] [{name_ptrs}]")
        self.extra_globals.append(
            f"{types_arr} = private constant [{len(type_strings)} x ptr] [{type_ptrs}]")
        result = (names_arr, types_arr, len(fields))
        self._table_arrays_cache["q$" + name] = result
        return result

    def _emit_row_to_struct_fn(self, struct_type):
        """claude.md #112: generates (once per struct type, cached) the
        function that turns one of festina_sqlite_collect_rows's flat
        rows into a real, refcounted struct instance.

        The flat row is `ncols` 8-byte slots plus the presence mask; a
        struct is an LLVM named struct with natural field offsets and a
        refcount header. Rather than teach every downstream consumer a
        second row layout, the row is converted ONCE, here, immediately
        after collection: each slot is loaded at the field's own LLVM
        type (the raw 8 bytes hold an i64, a double's bits, or a
        pointer -- little-endian, so an i8 bool reads its low byte, the
        same read a table-row member access does) and stored at the
        field's real offset. Pointer fields (text/blob/img/aud) TRANSFER
        ownership -- the struct owns them now, and only the row buffer
        itself is freed. The struct starts at refcount 1, owned by the
        result array, so element release, `free`, aliasing and the rest
        of claude.md #77's machinery apply with nothing new.

        The presence mask is deliberately dropped: undefined() is a
        TABLE-ROW method, and a struct instance from a query is an
        ordinary struct, indistinguishable from one built by hand -- an
        unmatched column simply reads null."""
        name = struct_type.name
        fn_name = f"@__festina_rowtostruct_{name}"
        if fn_name in self._row_to_struct_fns:
            return fn_name
        self._row_to_struct_fns.add(fn_name)
        struct_ty = self.struct_llvm_name(name)
        body = [f"define ptr {fn_name}(ptr %row) {{", "entry:"]
        size_ptr = self.tmp()
        body.append(f"  {size_ptr} = getelementptr {struct_ty}, ptr null, i64 1")
        size_val = self.tmp()
        body.append(f"  {size_val} = ptrtoint ptr {size_ptr} to i64")
        total = self.tmp()
        body.append(f"  {total} = add i64 {size_val}, 8")
        raw = self._emit_calloc("1", total, body)
        body.append(f"  store i64 1, ptr {raw}")
        payload = self.tmp()
        body.append(f"  {payload} = getelementptr i8, ptr {raw}, i64 8")
        for idx, (_, ftype) in enumerate(self.struct_fields(name)):
            f_llvm = _llvm_type(ftype)
            slot = self.tmp()
            body.append(f"  {slot} = getelementptr i8, ptr %row, i64 {idx * 8}")
            v = self.tmp()
            body.append(f"  {v} = load {f_llvm}, ptr {slot}")
            fp = self.tmp()
            body.append(f"  {fp} = getelementptr {struct_ty}, ptr {payload}, i32 0, i32 {idx}")
            body.append(f"  store {f_llvm} {v}, ptr {fp}")
        body.append("  call void @free(ptr %row)")
        body.append(f"  ret ptr {payload}")
        body.append("}")
        body.append("")
        self.func_defs.extend(body)
        return fn_name

    def _emit_sqlite_collect_struct(self, stmt_val, struct_type, lines, line=0):
        """claude.md #112: `arr[SomeStruct] q = sqlite(...)`. Shares the
        whole table pipeline -- prepare, bind, name-matched collection
        (claude.md #111) -- and differs only in the last step, where each
        flat row becomes a real struct instance in place."""
        names_global, types_global, ncols = self._query_struct_arrays(struct_type, line)

        n_slot = self.tmp()
        lines.append(f"  {n_slot} = alloca i64")
        data_slot = self.tmp()
        lines.append(f"  {data_slot} = alloca ptr")
        # claude.md #188: want_rowid=0 -- a struct query result isn't a
        # table row (no `.rowid` concept), and _emit_row_to_struct_fn's
        # own field-by-index conversion below has no rowid slot to read.
        lines.append(
            f"  call void @festina_sqlite_collect_rows(ptr {stmt_val}, i32 {ncols}, "
            f"ptr {types_global}, ptr {names_global}, ptr {n_slot}, ptr {data_slot}, i8 0)"
        )
        n_val = self.tmp()
        lines.append(f"  {n_val} = load i64, ptr {n_slot}")
        data_val = self.tmp()
        lines.append(f"  {data_val} = load ptr, ptr {data_slot}")

        convert_fn = self._emit_row_to_struct_fn(struct_type)
        uid = self._unique()
        i_slot = self.tmp()
        lines.append(f"  {i_slot} = alloca i64")
        lines.append(f"  store i64 0, ptr {i_slot}")
        cond = self.label(f"rowconv.cond{uid}")
        bodyl = self.label(f"rowconv.body{uid}")
        endl = self.label(f"rowconv.end{uid}")
        lines.append(f"  br label %{cond}")
        self._start_block(cond, lines)
        i_val = self.tmp()
        lines.append(f"  {i_val} = load i64, ptr {i_slot}")
        more = self.tmp()
        lines.append(f"  {more} = icmp slt i64 {i_val}, {n_val}")
        lines.append(f"  br i1 {more}, label %{bodyl}, label %{endl}")
        self._start_block(bodyl, lines)
        slot_ptr = self.tmp()
        lines.append(f"  {slot_ptr} = getelementptr ptr, ptr {data_val}, i64 {i_val}")
        row_val = self.tmp()
        lines.append(f"  {row_val} = load ptr, ptr {slot_ptr}")
        converted = self.tmp()
        lines.append(f"  {converted} = call ptr {convert_fn}(ptr {row_val})")
        lines.append(f"  store ptr {converted}, ptr {slot_ptr}")
        nxt = self.tmp()
        lines.append(f"  {nxt} = add i64 {i_val}, 1")
        lines.append(f"  store i64 {nxt}, ptr {i_slot}")
        lines.append(f"  br label %{cond}")
        self._start_block(endl, lines)

        header = self._emit_fresh_heap_header(FESTINA_ARRAY_LLVM_TYPE, lines)
        len_ptr = self.tmp()
        lines.append(f"  {len_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}, i32 0, i32 0")
        lines.append(f"  store i64 {n_val}, ptr {len_ptr}")
        data_field_ptr = self.tmp()
        lines.append(f"  {data_field_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}, i32 0, i32 1")
        lines.append(f"  store ptr {data_val}, ptr {data_field_ptr}")
        return header

    def _emit_toplevel_stmt(self, stmt, env, ctx):
        lines = ctx["lines"]
        if isinstance(stmt, ast.VarDecl):
            ref, type_ = env.lookup(stmt.name)
            if stmt.init is not None:
                val, vtype = self._emit_value_for(stmt.init, env, lines, type_)
                val = self._coerce(val, vtype, type_, lines, source_expr=stmt.init)
                # claude.md #77 (widened by #83 to text): a global's own
                # declaration-with-initializer is just another point its
                # value changes -- see _emit_global_retain_release's own
                # comment for why this needs the exact same retain/
                # release (or, for text, copy/free) treatment an
                # ordinary `g = expr` reassignment gets (_emit_assign),
                # not a plain store. Its own return value is what must
                # actually be stored -- see that method's own comment.
                val = self._emit_global_retain_release(ref, val, type_, lines, stmt.init,
                                                      source_type=vtype)
                lines.append(f"  store {_llvm_type(type_)} {val}, ptr {ref}")
            return
        self._emit_stmt(stmt, env, None, ctx)


def _parse_int_like_strtoll(text):
    """claude.md #150: Python-side replica of festina_text_to_int's own
    C strtoll()-based parse -- used ONLY to constant-fold a literal
    text receiver (`'42'.toInt()`) at compile time (see that call
    site's own comment); a dynamic receiver always goes through the
    real runtime function instead, so the two never need to agree via
    shared code, only via matching behavior, verified directly against
    each other (see tests/test_codegen.py's TestToInt). Deliberately
    ASCII-only digit/whitespace recognition (`'0' <= c <= '9'`, not
    Python's own Unicode-aware str.isdigit()/str.isspace()) -- C's
    strtoll in the "C" locale is ASCII-only for base 10, and Python's
    versions of those checks accept characters (Devanagari digits,
    superscripts, ...) the C runtime's own parse never would, which
    would make this compile-time shortcut disagree with the runtime
    path it's supposed to be indistinguishable from for the exact same
    literal text.
    """
    n = len(text)
    i = 0
    while i < n and text[i] in " \t\n\r\v\f":
        i += 1
    start = i
    if i < n and text[i] in "+-":
        i += 1
    digits_start = i
    while i < n and "0" <= text[i] <= "9":
        i += 1
    if i == digits_start:
        return int(INT_NULL_CONST)
    value = int(text[start:i])
    # claude.md #150: strtoll() itself clamps to LLONG_MIN/LLONG_MAX on
    # overflow (setting errno, which festina_text_to_int deliberately
    # doesn't check -- see that function's own comment) rather than
    # wrapping or erroring -- matched here so a literal long enough to
    # overflow folds to the exact same clamped value the runtime path
    # would have produced for it.
    i64_min, i64_max = -(2 ** 63), 2 ** 63 - 1
    return max(i64_min, min(i64_max, value))


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
