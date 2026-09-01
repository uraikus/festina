"""Semantic analysis -- claude.md #48 (error categories), #49 (symbol
table), #50 (type checking), plus the type-resolution/truthiness/equality
rules from #12-20, the struct/table distinction from #27, #28, #35, and
#56 (Math.floor/ceil/round/trunc and int.toFloat() are the ONLY ways to
turn a float back into an int -- claude.md #143 superseded #55's own
"int and float never mix directly" half of this entry: int/float mix
freely now, in any binary operator, the int side implicitly coerced to
float). #58 (struct/table namespace): struct/table names live in
`structs`/`tables`, never cross-checked against `Scope` (variables/
functions) -- separate namespaces by design.
#67/#68/#107 (regex(), .test(), .match(), .replace()) follow
the same "recognized Call-on-Member pattern" approach Math.floor/
int.toFloat() already established -- Festina has no general concept of
methods on primitive types, so each one is checked by name against the
receiver's inferred type in _infer_call, not looked up in some method
table.

`analyze(program)` walks the AST top to bottom. Struct/table names
(claude.md #106) and function signatures (claude.md #140) are each
pre-registered in their own dedicated pass over the whole program
before the real left-to-right walk begins, so declaring one of those
below where it's first used/called is never an ordering error --
"hoisting". Variables still are declared-before-use, genuinely (there
is no pre-pass for those, and no reasonable hoisting semantics for a
value that has to come from somewhere at runtime). This also makes
multi-file compilation (claude.md #5-6) work with no extra machinery
here: festina.imports.build_program already merged every file's
statements into one `program.body`, in dependency order, before
analyze() ever sees it -- the only thing this module does specially for
that is re-read each top-level statement's originating file (`.file`,
set by build_program) into the `filename` closure variable right before
analyzing it, so errors still name the right file (see the loops at the
bottom of analyze()).
"""
from . import ast
import dataclasses
import math
import os

from . import types as types_mod
from .errors import CompileError

# claude.md #39, #41, #42, #32, #67, #69: builtin globals that don't
# need a programmer declaration. setTimeout/setInterval/clearTimeout/
# clearInterval are listed here for documentation/completeness, but
# _infer_call actually dispatches them through their own branch before
# ever reaching the BUILTIN_FUNCTIONS check below, since setTimeout/
# setInterval's first argument (a callback) needs structural handling
# no other builtin needs -- see the comment there.
# claude.md #109: names that used to be builtins, each mapped to the
# sentence that says what replaced it. Checked before "unknown
# function" so a program written against the old surface gets told what
# to write instead of being told the name does not exist -- the same
# treatment claude.md #100 gave aud.stop() and #107 gave replaceAll().
# Every one of these was removed because the language had grown a
# better way to say the same thing and keeping both was the only thing
# making either confusing.
# claude.md #109: blob's methods -- name -> (argument types, return
# type). A blob is a file's bytes plus the path they came from, so
# these are exactly the five things claude.md #93's free functions did
# with a path, asked of the value that already holds one.
#
# toText() returns the bytes as text; the other four act on the file.
# write/append/delete return bool rather than failing the program,
# preserving claude.md #93's own rule that a missing or unwritable file
# is something a program tests for rather than something that stops it.
_REMOVED_BUILTINS = {
    "loadImage": "loadImage() is gone -- declare the image from its path "
                 "instead: img sprite = 'sprite.png' (the path may be any "
                 "text expression)",
    "loadAudio": "loadAudio() is gone -- declare the clip from its path "
                 "instead: aud music = 'music.mp3' (the path may be any "
                 "text expression)",
    "readFile": "readFile() is gone -- declare a blob from the path and read "
                "it: blob f = 'notes.txt'  text body = f.toText()",
    "writeFile": "writeFile() is gone -- declare a blob from the path and "
                 "write to it: blob f = 'notes.txt'  f.write('hello')",
    "appendFile": "appendFile() is gone -- declare a blob from the path and "
                  "append to it: blob f = 'notes.txt'  f.append(' world')",
    "fileExists": "fileExists() is gone -- declare a blob from the path and "
                  "ask it: blob f = 'notes.txt'  bool there = f.exists()",
    "deleteFile": "deleteFile() is gone -- declare a blob from the path and "
                  "delete it: blob f = 'notes.txt'  f.delete()",
}

BUILTIN_FUNCTIONS = {
    "log", "fail", "troubleshoot", "sqlite",
    "drawRect", "drawCircle", "drawText", "drawImage",
    # claude.md #133: drawPixel (with drawRect's own optional trailing
    # `color` argument, see _BUILTIN_SIGNATURE_ALTERNATES below) and
    # clearRect's circle/pixel-shaped counterparts.
    "drawPixel", "clearCircle", "clearPixel",
    # claude.md #98: how many channels the pool may assign automatically.
    "setMaxAudioPlayers", "maxAudioPlayers",
    # claude.md #99: stop one channel (or, with no argument, all of them).
    "stopAudioPlayer",
    # claude.md #146: true while that channel is playing anything,
    # regardless of clip -- the per-CHANNEL counterpart to aud's own
    # clip-wide isPlaying() method.
    "isAudioPlayerPlaying",
    # claude.md #89/#91: canvas drawing style + text metrics. `font` is
    # NOT here -- claude.md #91 turned it into a type name, so the
    # setter is changeFont().
    "fillStyle", "borderColor", "lineWidth", "changeFont",
    "measureTextWidth", "measureTextHeight",
    "regex",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    # claude.md #93: time and canvas export -- backed by libc and by
    # Cairo's own PNG writer, both already linked. claude.md #109 moved
    # this section's file functions onto `blob` itself.
    "now", "formatTime", "saveCanvas",
    # claude.md #94: paths, transforms, gradients, alpha
    "render", "clearCanvas", "clearRect",
    "beginPath", "moveTo", "lineTo", "curveTo", "closePath",
    "fillPath", "strokePath",
    "translate", "rotate", "scale", "resetTransform",
    "saveState", "restoreState",
    "fillAlpha", "fillLinearGradient", "fillRadialGradient",
    # claude.md #180: the window now opens fully decorated (title bar,
    # minimize/maximize/close, like any other window -- claude.md #95's
    # own "undecorated" language is retired) -- enterFullscreen()/
    # exitFullscreen() toggle true OS fullscreen on top of that, same
    # as render() the only two things here that need a real GUI (see
    # codegen.py's own uses_graphics condition).
    "enterFullscreen", "exitFullscreen",
    # claude.md #182: showCursor()/hideCursor() -- toggles the mouse
    # cursor's visibility over the canvas.
    "showCursor", "hideCursor",
    # claude.md #131: exits the program with `code`, running a declared
    # `on exit(code:int)` handler first (see _EVENT_SIGNATURES below) --
    # works with or without a window, unlike mouseDown/.../close's on-
    # screen-only handlers.
    "close",
    # claude.md #132: filesystem builtins -- mkdir() answers a bool
    # rather than failing (claude.md #93's own "a missing/unwritable
    # file is something a program tests for" rule, extended to
    # directories), ls() answers arr[text] of entry names.
    "mkdir", "ls",
    # claude.md #139: the setters for clientWidth/clientHeight --
    # screenWidth/screenHeight have no setter, since a program cannot
    # resize the physical display it's running on.
    "setClientWidth", "setClientHeight",
    # claude.md #150: exec(args) -- spawns args[0] with args[1:] as its
    # own argv, answering its real exit code (or -1 if it never even
    # started) rather than failing the program -- the same test-don't-
    # fail choice claude.md #93/#132 already made for file/directory
    # operations.
    "exec",
    # claude.md #151: openPort/closePort -- start/stop listening for
    # HTTP connections on a port. Neither fails the program either
    # (openPort on an already-open port, or a privileged/in-use one,
    # is a silent no-op; closePort on a port never opened likewise) --
    # the same "test, don't fail" convention as mkdir/exec above.
    "openPort", "closePort",
    # claude.md #160: the TLS counterpart to openPort -- same "bad
    # port number is a silent no-op" contract, but unlike openPort a
    # malformed/mismatched certificate or key DOES fail the program
    # (a program-authoring mistake, not a runtime condition to test
    # for -- the same line claude.md #59 already draws elsewhere).
    "openSecurePort",
    # claude.md #195: the bare, context-implicit form used inside a
    # thread's own body to send outward to the main program -- reserved
    # everywhere (not just inside a thread) for the identical reason
    # every other builtin name here is: a user function declared with
    # this name would be permanently unreachable inside a thread body,
    # where the real meaning always wins.
    "postMessage",
    # claude.md #188 (uraikus/festina#76 item 4): blankImage(w, h) ->
    # img -- a fresh, fully-transparent image, with no existing image
    # or canvas to derive it from (unlike .clip()/.resize()/
    # saveCanvas(), every one of which copies from something).
    "blankImage",
    # claude.md #189: getPixelColor(x, y) -> color -- reads one pixel
    # back off the canvas. img.getPixelColor(x, y) is the img-method
    # counterpart, checked separately below (img methods aren't part
    # of this free-function set).
    "getPixelColor",
    # claude.md #162: parseURL(text) -- like mkdir/exec above, a fixed
    # (text,) -> url signature the standard _BUILTIN_SIGNATURES/
    # _BUILTIN_RETURN_TYPES tables already handle directly, no bespoke
    # _infer_call branch needed the way regex()'s own variable-arity
    # handling requires. There is no separate fetch() builtin --
    # outbound requests go through http's own zero-argument
    # req.send() instead (see _HTTP_METHODS' own comment).
    "parseURL",
}

_BUILTIN_RETURN_TYPES = {
    # claude.md #98: reads back the limit AFTER clamping, so a program
    # can see what it actually got rather than what it asked for.
    "maxAudioPlayers": types_mod.PrimitiveType("int"),
    "regex": types_mod.RegexType(),
    # claude.md #188 (uraikus/festina#76 item 4)
    "blankImage": types_mod.ImageType(),
    # claude.md #189
    "getPixelColor": types_mod.ColorType(),
    # claude.md #162
    "parseURL": types_mod.UrlType(),
    # claude.md #89: the only two graphics builtins that return anything
    "measureTextWidth": types_mod.PrimitiveType("int"),
    "measureTextHeight": types_mod.PrimitiveType("int"),
    # claude.md #93
    "now": types_mod.PrimitiveType("int"),
    "formatTime": types_mod.PrimitiveType("text"),
    # claude.md #135: saveCanvas's return type now depends on its own
    # arity (bool with a path, img without) -- handled by its own
    # dedicated branch in _infer_call, not this fixed-per-name table.
    # claude.md #132
    "mkdir": types_mod.PrimitiveType("bool"),
    "ls": types_mod.ArrayType(types_mod.PrimitiveType("text")),
    # claude.md #146
    "isAudioPlayerPlaying": types_mod.PrimitiveType("bool"),
    # claude.md #150/#177: exec() -- NOT here. Its return type depends
    # on which of its two arities is used (1-arg -> int, 2-arg -> void),
    # which this fixed dict can't express -- see _infer_call's own
    # dedicated branch, the same reason setTimeout/saveCanvas aren't
    # here either.
}

# claude.md #55: int and float never mix directly in a binary operator.
_INT = types_mod.PrimitiveType("int")
_FLOAT = types_mod.PrimitiveType("float")
_NUMERIC_TYPES = (_INT, _FLOAT)
_TEXT = types_mod.PrimitiveType("text")
_BLOB = types_mod.PrimitiveType("blob")
_BOOL = types_mod.PrimitiveType("bool")


def _is_blob_type(t):
    """claude.md #202: `t == _BLOB` breaks for a manually-managed
    `blob?` -- a genuinely different, unequal dataclass instance from
    the plain `_BLOB` singleton (manually_managed is a real, `__eq__`-
    participating field) -- everywhere blob is checked by identity
    rather than via an isinstance check the way img/aud/http/... all
    already are (blob has no dedicated dataclass of its own for that).
    Use this instead of `== _BLOB` at any site that must keep treating
    a manually-managed blob exactly like an ordinary one -- every
    site that dispatches by VALUE SHAPE (a method call, a coercion
    rule, ...) needs to, since `T?`'s representation is identical to
    `T`'s; only the handful of sites that gate AUTOMATIC bookkeeping
    care about the flag at all."""
    return isinstance(t, types_mod.PrimitiveType) and t.name == "blob"

# claude.md #202: `T?` -- the exact set of resolved types
# `manually_managed` means something real for, mirroring
# codegen._is_refcounted's own family exactly (every type this runtime
# already knows how to release via a refcount header). Not TableType
# (a query row has no refcount header at all -- see FreeStmt's own
# "nulled WITHOUT freeing" special case) and not FuncType.
_MANUALLY_MANAGEABLE_TYPES = (
    types_mod.StructType, types_mod.ArrayType, types_mod.MapType, types_mod.EnumType,
    types_mod.ImageType, types_mod.AudioType, types_mod.HttpType, types_mod.SocketType,
    types_mod.UrlType, types_mod.RegexType,
)


def apply_manually_managed(resolved_type, manually_managed):
    """claude.md #202: called right after EVERY `resolve(...)` of a
    declaration/parameter whose own AST node carries a `manually_managed`
    flag (set by a trailing `?` after the type -- parser.py's
    parse_var_decl/parse_typed_params) -- rebuilds the resolved type
    with `manually_managed=True` when it's one of
    `_MANUALLY_MANAGEABLE_TYPES` (via `dataclasses.replace`, the same
    mechanism `resolve_type_name`'s own `amortized=type_expr.amortized`
    already uses for ArrayType), or the one non-isinstance special case
    (`blob`, a PrimitiveType value rather than its own dataclass).
    Every OTHER type (int/float/bool/text/color/font/table/func) is
    returned completely unchanged regardless of `manually_managed` --
    `?` on one of those is accepted grammar with zero type-level
    effect, matching claude.md #202's own `int? count = 1` example
    exactly: it resolves to the identical, fully interchangeable
    `PrimitiveType("int")` whether or not the source wrote `?`."""
    if not manually_managed:
        return resolved_type
    if isinstance(resolved_type, _MANUALLY_MANAGEABLE_TYPES):
        return dataclasses.replace(resolved_type, manually_managed=True)
    if resolved_type == _BLOB:
        return types_mod.PrimitiveType("blob", manually_managed=True)
    return resolved_type


def _is_fresh_construction(expr):
    """claude.md #204: mirrors codegen._is_owning_refcounted_source's
    own "nothing else could already reference this value" reasoning
    (a plain Call/ArrayLit/MapLit is "owning" there for exactly this
    reason), at the semantic-analysis level, for one purpose: deciding
    whether a manually-managed declaration's own initializer may adopt
    manually-managed-ness from the BARE type its expression naturally
    infers as.

    Without this, `T?` had only two escape hatches for actually
    getting a value into a fresh declaration -- struct's own "no
    literal syntax, fields set individually" shape, and blob/img/aud's
    text-coercion -- and every OTHER eligible type (arr[T]/map[T]/
    regex, and even a STRUCT built by a factory function rather than
    field-by-field) had none at all: `regex? r = /pattern/`,
    `arr[int]? xs = [1, 2, 3]`, and `Circle? c = makeCircle()` were all
    compile errors, since a fresh array/map/regex literal or any
    function call's own return value always infers as the plain,
    unflagged type -- there being no `?`-producing expression syntax
    anywhere in the language.

    Deliberately narrower than codegen's own version (no ternary, no
    member-chain-off-a-call tracking) -- this only ever needs to
    recognize the plain shapes that can appear directly as a
    declaration's own initializer expression; codegen's fuller version
    already handles anything more deeply nested correctly regardless,
    since its own retain-skip for a manually-managed declaration
    (`stmt.manually_managed`) is unconditional on freshness in the
    first place (see _emit_stmt's own VarDecl branches) -- this
    predicate only ever gates whether semantic.py's own type check
    ACCEPTS the combination at all, never anything about codegen's own
    bookkeeping."""
    return isinstance(expr, (ast.Call, ast.ArrayLit, ast.MapLit, ast.RegexLit))

# See the placeholder above for what this is and why. Defined here
# rather than there because it needs _TEXT/_BOOL.
_BLOB_METHODS = {
    "toText": ((), _TEXT),
    "write": ((_TEXT,), _BOOL),
    "append": ((_TEXT,), _BOOL),
    "exists": ((), _BOOL),
    "delete": ((), _BOOL),
}

# claude.md #37, #39: signatures for the builtins with real implementations
# (drawCircle/drawText/drawImage/loadImage) -- matches each function's own
# worked example in the spec exactly (e.g. drawRect(0, 0, 100, 100)).
# Builtins with no entry here (log, fail, sqlite) stay permissive:
# their args are inferred but not checked against a fixed signature,
# since claude.md leaves their shape open.
_BUILTIN_SIGNATURES = {
    # claude.md #133: drawRect's own fixed entry moved to
    # _BUILTIN_SIGNATURE_ALTERNATES below, alongside drawPixel -- both
    # now have a second, 1-argument-longer form with a trailing `color`.
    # claude.md #188 (uraikus/festina#76 item 8): drawCircle's own
    # fixed entry moved there too, alongside its new fill/fill+border
    # trailing-color forms.
    "drawText": (_TEXT, _INT, _INT),
    # claude.md #188 (uraikus/festina#76 item 4)
    "blankImage": (_INT, _INT),
    # claude.md #189
    "getPixelColor": (_INT, _INT),
    # claude.md #185: drawImage's own fixed 3-argument entry moved to
    # _BUILTIN_SIGNATURE_ALTERNATES below, alongside its new 5- and
    # 9-argument forms.
    "setMaxAudioPlayers": (_INT,),  # claude.md #98
    "maxAudioPlayers": (),
    # claude.md #89: a colour is text (a name, #rgb/#rrggbb, or 'none'),
    # validated at runtime rather than compile time -- the value is an
    # arbitrary expression, so there is nothing to check here beyond its
    # type (the same split regex() already uses for its own pattern).
    "lineWidth": (_INT,),
    "measureTextWidth": (_TEXT,),
    "measureTextHeight": (_TEXT,),
    # claude.md #93
    "now": (),
    "formatTime": (_INT, _TEXT),
    # claude.md #94
    "render": (),
    "clearCanvas": (),
    "clearRect": (_INT, _INT, _INT, _INT),
    # claude.md #133
    "clearCircle": (_INT, _INT, _INT),
    "clearPixel": (_INT, _INT),
    "beginPath": (),
    "moveTo": (_INT, _INT),
    "lineTo": (_INT, _INT),
    "curveTo": (_INT, _INT, _INT, _INT, _INT, _INT),
    "closePath": (),
    "fillPath": (),
    "strokePath": (),
    "translate": (_INT, _INT),
    "rotate": (_FLOAT,),
    "scale": (_FLOAT, _FLOAT),
    "resetTransform": (),
    "saveState": (),
    "restoreState": (),
    "fillAlpha": (_FLOAT,),
    "fillLinearGradient": (_INT, _INT, types_mod.ColorType(), _INT, _INT, types_mod.ColorType()),
    "fillRadialGradient": (_INT, _INT, _INT, types_mod.ColorType(), types_mod.ColorType()),
    # claude.md #180
    "enterFullscreen": (),
    "exitFullscreen": (),
    # claude.md #182
    "showCursor": (),
    "hideCursor": (),
    # claude.md #131
    "close": (_INT,),
    # claude.md #132
    "mkdir": (_TEXT,),
    "ls": (_TEXT,),
    # claude.md #139
    "setClientWidth": (_INT,),
    "setClientHeight": (_INT,),
    # claude.md #146: the channel argument is required, unlike
    # stopAudioPlayer's optional one (see _BUILTIN_SIGNATURE_ALTERNATES
    # below) -- there's no sensible "any channel" reading for a query.
    "isAudioPlayerPlaying": (_INT,),
    # claude.md #150/#177: exec() -- NOT here either, for the identical
    # reason it's absent from _BUILTIN_RETURN_TYPES above: its 2-arg
    # form's callback argument needs the same structural (not fixed-
    # type) checking setTimeout's own callback already needs, which
    # this dict has no way to express. See _infer_call's own dedicated
    # branch, which owns both of exec()'s arities.
    # claude.md #151
    "openPort": (_INT,),
    "closePort": (_INT,),
    # claude.md #160: (port, key) -- key is a combined PEM blob (cert
    # + unencrypted private key). See _BUILTIN_FUNCTIONS above.
    "openSecurePort": (_INT, _BLOB),
    # claude.md #162: parseURL(text) -> url.
    "parseURL": (_TEXT,),
}

# claude.md #90: three builtins accept two different shapes. The
# one-argument form takes a literal the compiler resolves outright
# (fillStyle('red'), font('bold 14px arial')); the explicit form takes
# the already-resolved parts, and is what a program uses to compute a
# colour or font size at runtime. Checked here as "any of these
# signatures", with the arity picking which one applies.
_COLOR = types_mod.ColorType()
_FONT = types_mod.FontType()

# claude.md #33/#94/#219: every builtin taking (sql, [params]) -- the
# bound parameter list is a literal array that is explicitly allowed to
# mix types, so it needs a carve-out from the ordinary same-element-type
# array rule. `sqliteInt`/`sqliteFloat`/`sqliteText` (claude.md #94's
# own single-value-query convenience wrappers) were removed in claude.md
# #219 -- `sqlite()` itself is the only member left, but this stays a
# named set (not an inline `name == "sqlite"` check) since every call
# site below reads as "one of the sql-taking builtins", which is still
# the real question being asked even with one member.
_SQLITE_BUILTINS = frozenset({"sqlite"})

_BUILTIN_SIGNATURE_ALTERNATES = {
    # claude.md #91: the one-argument form takes a `color` value, not a
    # text literal -- a colour name has to be declared as one
    # (`color red = 'red'`) so that resolution happens exactly once, at
    # that declaration. The three-int form is what a program uses to
    # compute a colour at runtime.
    "fillStyle": [(_COLOR,), (_INT, _INT, _INT)],
    "borderColor": [(_COLOR,), (_INT, _INT, _INT)],
    # claude.md #91: likewise a `font` value; the explicit form (px,
    # style, family -- style/family nullable, px <= 0 keeps the current
    # size) remains for a font whose size is computed at runtime.
    "changeFont": [(_FONT,), (_INT, _TEXT, _TEXT)],
    # claude.md #99: stopAudioPlayer(n) stops one channel;
    # stopAudioPlayer() stops every channel.
    "stopAudioPlayer": [(), (_INT,)],
    # claude.md #133: an optional trailing `color` -- present, paints
    # with it for this one call only; absent, uses the current
    # fillStyle, exactly like every other draw call already does.
    # claude.md #188 (uraikus/festina#76 item 8): drawRect grows a
    # SECOND optional trailing colour, a border override -- present,
    # strokes with it for this one call only (no border at all if it's
    # `none`); absent (the 5-argument form), uses the current
    # borderColor, exactly the split fillStyle()/borderColor()
    # themselves keep. drawCircle gains the identical two-color shape,
    # NEWLY -- it previously had no per-call colour override at all.
    "drawRect": [(_INT, _INT, _INT, _INT), (_INT, _INT, _INT, _INT, _COLOR),
                 (_INT, _INT, _INT, _INT, _COLOR, _COLOR)],
    "drawPixel": [(_INT, _INT), (_INT, _INT, _COLOR)],
    "drawCircle": [(_INT, _INT, _INT), (_INT, _INT, _INT, _COLOR),
                    (_INT, _INT, _INT, _COLOR, _COLOR)],
    # claude.md #185 (uraikus/festina#76 item 3): drawImage(img, x, y)
    # is unchanged; drawImage(img, x, y, w, h) scales the WHOLE image
    # to fit a w x h box; drawImage(img, sx, sy, sw, sh, dx, dy, dw, dh)
    # is the full canvas-style form -- a SOURCE rect cut out of the
    # image, scaled to fit a DESTINATION rect. All three share the
    # first argument's image type, so len() alone (3 vs. 5 vs. 9) picks
    # the right one with no ambiguity.
    "drawImage": [
        (types_mod.ImageType(), _INT, _INT),
        (types_mod.ImageType(), _INT, _INT, _INT, _INT),
        (types_mod.ImageType(), _INT, _INT, _INT, _INT, _INT, _INT, _INT, _INT),
    ],
}
_REGEX = types_mod.RegexType()
_AUDIO = types_mod.AudioType()
_IMAGE = types_mod.ImageType()
_HTTP = types_mod.HttpType()
_SOCKET = types_mod.SocketType()

# claude.md #151: http's fixed-arity, fixed-argument-type methods --
# same (arg types, return type) shape as _BLOB_METHODS above, and for
# the identical reason (arity/argument types enforced by name here
# rather than left to the generic member-access fallback). `send`
# isn't here: its `data` argument accepts any concrete type with a
# body form (checked structurally in _infer_call, the same way
# setTimeout's callback argument is) and its `code`/`headers`
# arguments are each optional, which this fixed-shape table has no way
# to express -- see _infer_call's own dedicated branch for it.
_HTTP_METHODS = {
    "ok": ((), None),
    "redirect": ((_TEXT,), None),
    # claude.md #151: switches this connection from HTTP to WebSocket
    # -- sends the 101 handshake immediately (a no-op if the request
    # doesn't carry a valid Upgrade: websocket/Sec-WebSocket-Key pair,
    # never a compile-time OR runtime failure), then, once `on
    # request` returns, the runtime fires `on upgrade(s:socket)` once
    # for this same connection if one is declared.
    "upgrade": ((), None),
    "toBlob": ((), _BLOB),
    "toImg": ((), _IMAGE),
    "toAud": ((), _AUDIO),
    "toText": ((), _TEXT),
}

# claude.md #151: socket's own fixed-arity method -- `send` is
# handled the same bespoke way http's own `send` is (see above);
# `close` is the only socket method with a fixed, checkable shape.
_SOCKET_METHODS = {
    "close": ((), None),
}

# claude.md #151: the concrete types http.send()/socket.send()'s
# `data:any` argument actually accepts -- deliberately the SAME set
# _to_text (this module's own codegen counterpart) already gives a
# text form to, since every one of these (other than blob, sent as
# its own raw bytes rather than decoded through toText()) becomes the
# response/frame body by calling toText() on it first. img/aud/http/
# socket/regex/color/font/func are rejected with a compile error --
# no body form, the same "silently printing a placeholder would hide
# a mistake" reasoning claude.md #114 already applied to log()/
# templates for img/aud specifically, widened here to every type that
# was never sendable in the first place.
def _is_sendable_type(t):
    if t in (_TEXT, _INT, _FLOAT, _BOOL, _BLOB):
        return True
    return isinstance(t, (types_mod.StructType, types_mod.TableType,
                          types_mod.ArrayType, types_mod.MapType))

# claude.md #162: an http literal's own `'body':` key accepts
# everything _is_sendable_type already does, PLUS img/aud -- unlike
# socket.send()'s data:any (a raw WebSocket frame has no meaningful
# "media" reading), a real HTTP request/response body uploading or
# returning a picture/clip is completely ordinary, so the same
# `img`/`aud` rejection log()/templates/socket.send() all share
# doesn't apply here.
def _is_http_body_type(t):
    return _is_sendable_type(t) or t in (_IMAGE, _AUDIO)

# claude.md #163: `callback`'s exact required signature -- `void
# func(http)`, matching `void func processLater(http r) { ... }` in
# the feature's own originating example. A module-level constant
# (rather than building it fresh at every call site) purely so
# `_validate_http_lit`'s generic `val_type != expected` comparison
# below and `_infer_member`'s own HttpType branch can share the exact
# same FuncType instance -- structural equality (FuncType is a frozen
# dataclass) means this would work either way, but sharing one literal
# instance is simpler to read than reconstructing it twice.
_HTTP_CALLBACK_TYPE = types_mod.FuncType((types_mod.HttpType(),), None)

# claude.md #162 (extended by #163's `callback`): the field set
# `http x = {...}` accepts -- shared between VarDecl's own bypass
# (mirroring claude.md #156's identical amor-map-literal bypass, see
# analyze_var_decl) and req.send(res)'s one-argument form, when the
# caller writes the response inline (`req.send({'code':200, ...})`)
# rather than through an already-declared http variable. Each entry's
# KEY must already be a literal text key (ast.StringLit) naming one of
# these six fields -- an arbitrary computed key expression is rejected
# outright, since there is no way to validate (or, in codegen, build)
# a heterogeneous literal whose very field set isn't known until
# runtime, unlike a genuine map[T] literal where every value shares
# one type regardless of which key produced it.
_HTTP_LIT_FIELD_TYPES = {
    "url": _TEXT,
    "method": _TEXT,
    "code": _INT,
    "headers": None,  # checked separately below (map[text])
    "body": None,     # checked separately below (_is_http_body_type)
    "callback": _HTTP_CALLBACK_TYPE,
}


def _validate_http_lit(maplit, scope, filename, infer):
    for key_expr, val_expr in maplit.entries:
        if not isinstance(key_expr, ast.StringLit):
            raise CompileError(
                "an http literal's keys must be plain text -- "
                "'url'/'method'/'code'/'headers'/'body'/'callback', not a computed expression",
                file=filename, line=getattr(key_expr, "line", 0),
                column=getattr(key_expr, "column", 0),
                category="invalid operand type",
            )
        key = key_expr.value
        if key not in _HTTP_LIT_FIELD_TYPES:
            raise CompileError(
                f"http has no field '{key}' to construct -- an http literal "
                f"accepts 'url', 'method', 'code', 'headers', 'body', and 'callback'",
                file=filename, line=getattr(key_expr, "line", 0),
                column=getattr(key_expr, "column", 0),
                category="invalid field access",
            )
        val_type = infer(val_expr, scope)
        if val_type is None or val_type is NULL:
            continue
        if key == "headers":
            if val_type != types_mod.MapType(_TEXT):
                raise CompileError(
                    f"http literal's 'headers' expects map[text], found "
                    f"{types_mod.type_name(val_type)}",
                    file=filename, line=getattr(val_expr, "line", 0),
                    column=getattr(val_expr, "column", 0),
                    category="invalid operand type",
                )
        elif key == "body":
            if not _is_http_body_type(val_type):
                raise CompileError(
                    f"http literal's 'body' has no body form -- found "
                    f"{types_mod.type_name(val_type)}",
                    file=filename, line=getattr(val_expr, "line", 0),
                    column=getattr(val_expr, "column", 0),
                    category="invalid operand type",
                )
        else:
            expected = _HTTP_LIT_FIELD_TYPES[key]
            if val_type != expected:
                raise CompileError(
                    f"http literal's '{key}' expects {types_mod.type_name(expected)}, "
                    f"found {types_mod.type_name(val_type)}",
                    file=filename, line=getattr(val_expr, "line", 0),
                    column=getattr(val_expr, "column", 0),
                    category="invalid operand type",
                )


def _http_send_lit_receiver(node):
    """claude.md #164: `http req = {...}.send()` -- if `node` is
    exactly `Call(Member(<MapLit>, 'send', computed=False), [])`,
    answers the inner MapLit; otherwise None. Used ONLY at a VarDecl's
    own init position (analyze_var_decl/`_emit_value_for`'s matching
    bypass in codegen.py) -- NOT a general property of `.send()`
    expressions elsewhere, deliberately: `.send()` itself still always
    returns void (see _infer_call's own `send` branch), so this is
    pure syntax-level sugar recognized at exactly one position, not a
    new return type. That restriction is what keeps this safe --
    `req.send()` on an EXISTING variable still can't be captured into
    a new binding at all (a real double-ownership hazard: the same
    pointer would then be reachable through two independent bindings,
    each releasing it on its own), and this helper only ever matches
    when the receiver is a MapLit literal, which is unconditionally
    fresh (nothing else could possibly reference it yet) regardless of
    where it appears."""
    if (isinstance(node, ast.Call) and len(node.args) == 0
            and isinstance(node.callee, ast.Member)
            and not node.callee.computed and node.callee.prop == "send"
            and isinstance(node.callee.obj, ast.MapLit)):
        return node.callee.obj
    return None

# claude.md #56: float -> int, with an explicit rounding decision.
MATH_ROUNDING_FUNCTIONS = {"floor", "ceil", "round", "trunc"}
# claude.md #93: float -> float. Kept separate from the rounding four
# above because the RETURN type differs -- rounding answers "which
# integer", these answer "which real number" -- and conflating them
# would make Math.sqrt(2.0) silently an int.
MATH_FLOAT_FUNCTIONS = {"sqrt", "sin", "cos", "tan", "asin", "acos", "atan",
                        "exp", "log", "log2", "log10", "abs"}
# claude.md #93: (float, float) -> float.
MATH_FLOAT2_FUNCTIONS = {"pow", "min", "max", "atan2"}
# claude.md #188 (uraikus/festina#76 item 1): (int, int) -> int -- the
# one Math function that takes INT arguments rather than float, kept in
# its own set for exactly that reason (every check above this one
# assumes float args and an int-or-float result; floorDiv is neither).
MATH_INT2_FUNCTIONS = {"floorDiv"}
# Every name reachable as Math.<name>(...), for the "is this a Math
# call at all" test; the sets above decide arity and result type.
MATH_FUNCTIONS = (MATH_ROUNDING_FUNCTIONS | MATH_FLOAT_FUNCTIONS
                  | MATH_FLOAT2_FUNCTIONS | MATH_INT2_FUNCTIONS | {"random"})
# claude.md #93: Math.PI / Math.E -- constants, not calls. Trigonometry
# is unusable without at least PI, and making a program spell out
# 3.14159... is exactly the kind of thing a standard library exists to
# prevent.
MATH_CONSTANTS = {"PI": math.pi, "E": math.e}

# claude.md #40: these five event names are the only ones with a real
# runtime source (an X11 event of some kind -- see festina_runtime.h's
# doc comment on festina_run_event_loop), so only they get a fixed-
# signature check; codegen registers each compiled handler with the
# runtime through a fixed C function-pointer type per event, so a
# mismatched signature would be a silent ABI mismatch, not just an
# unusual choice. Any other event name (analyze_event_handler leaves it
# unconstrained) still compiles but is simply dead code -- nothing ever
# fires it. Each entry is (required-arg-types, human-readable signature
# for the error message) -- resize/close take no arguments, so their
# tuple is empty and `param_types != sig` alone (no separate length
# check needed) catches both "too many" and "wrong type" mistakes.
_EVENT_SIGNATURES = {
    # claude.md #106: `on click` split into press and release, the same
    # way claude.md #98 split `on key`. A click is a press and a
    # release, and dragging -- or charging a shot, or hold-to-aim --
    # needs to tell them apart; `on click` fired on press and collapsed
    # the two, so there was nothing to listen for on the way up.
    # claude.md #182: `button` reports which physical button, X11's own
    # numbering (see FestinaWindowEvent's own doc comment in
    # festina_runtime_window.h) -- `mouse` (continuous movement,
    # unaffected) stays (x, y) only, since a move has no button of its
    # own to report.
    "mouseDown": ((_INT, _INT, _INT), "(x:int, y:int, button:int)"),
    "mouseUp": ((_INT, _INT, _INT), "(x:int, y:int, button:int)"),
    "mouse": ((_INT, _INT), "(x:int, y:int)"),
    # claude.md #181: the scroll wheel -- one event per notch/step,
    # split by direction rather than a single `on mouseWheel(delta)`
    # the same way `on mouseDown`/`on mouseUp` are split by direction
    # (up/down) rather than one `on click` -- see that entry's own
    # reasoning, which applies identically here. `x`/`y` report the
    # pointer's position at the moment of the scroll, same convention
    # as every other pointer event above.
    "mouseWheelUp": ((_INT, _INT), "(x:int, y:int)"),
    "mouseWheelDown": ((_INT, _INT), "(x:int, y:int)"),
    # claude.md #98: one `on key` became two, so a program can tell a
    # press from a release -- holding a movement key and letting it go
    # had no expressible difference before.
    "keyDown": ((_TEXT,), "(key:text)"),
    "keyUp": ((_TEXT,), "(key:text)"),
    "resize": ((), "no parameters"),
    "close": ((), "no parameters"),
    # claude.md #131: NOT a graphics event -- fires from the close(code)
    # builtin, which works with or without a window. Kept in this same
    # table since the shape (a fixed, enforced signature) is identical;
    # analyze_event_handler and _emit_event_handler both special-case it
    # away from the other six's window-only registration.
    "exit": ((_INT,), "(code:int)"),
    # claude.md #151: the http/websocket event sources -- fired from
    # festina_runtime_http.c's own single-threaded poll loop, not a
    # graphics event, so (like `exit` above) these register
    # unconditionally in main() rather than joining the graphics-gated
    # event_handlers loop. `on request` fires once per accepted
    # connection's parsed HTTP request; `on upgrade` fires once, right
    # after a `req.upgrade()` call inside `on request` completes the
    # WebSocket handshake for that same connection; `on socketMessage`
    # fires once per complete WebSocket frame received; `on
    # socketClose` fires once, whenever an upgraded connection ends
    # (the peer closed it, sent a close frame, or the read failed) --
    # exactly once per connection that ever reached `on upgrade`,
    # never for a plain HTTP connection that never upgraded.
    #
    # claude.md #208: renamed from `on message` -- that name is now the
    # unified thread-messaging handler (see _THREAD_EVENT_SIGNATURES
    # and analyze_event_handler's own special-cased handling of it,
    # below), which needed the name free at the top level too, not
    # just inside a thread body.
    "request": ((_HTTP,), "(req:http)"),
    "upgrade": ((_SOCKET,), "(s:socket)"),
    "socketMessage": ((_SOCKET, _BLOB), "(s:socket, msg:blob)"),
    "socketClose": ((_SOCKET,), "(s:socket)"),
}

# claude.md #195/#208: `on load()`/`on message(worker:thread, msg:T)`/
# `on exit(code:int)` nested inside a `thread { ... }` body -- a
# SEPARATE, closed table from _EVENT_SIGNATURES above, not folded into
# it, since (unlike _EVENT_SIGNATURES, where an unrecognized name is
# deliberately tolerated -- claude.md #40 -- since it might be a
# not-yet-implemented future event) a thread body is a brand new,
# closed construct with no such legacy: an unrecognized `on` name
# inside one is a real mistake worth catching immediately, not
# silently-dead code. `None` for "message" marks it as the one entry
# whose param types aren't fixed here -- both DECLARED, at the point
# this specific handler is written (see _check_message_handler_params,
# shared with the top-level `on message` handler below, which follows
# the identical (worker:thread, msg:T) shape for the SAME reason: both
# are just "this receiver's own inbound message handler," one written
# inside a thread body, one written at the top level for main).
_THREAD_EVENT_SIGNATURES = {
    "load": ((), "no parameters"),
    "message": (None, "(worker:thread, msg:T) -- worker.main is true when sent by main"),
    "exit": ((_INT,), "(code:int)"),
    # claude.md #212 (Phase 4 -- private per-thread HTTP context): the
    # SAME four names/signatures _EVENT_SIGNATURES already declares
    # for main's own top-level http/websocket handlers -- a thread's
    # own copy fires from that ONE thread's own private connection
    # table (see festina_runtime_http.c's __thread conversion), never
    # main's or another thread's. Declaring one of these here is what
    # sets info.has_http_handler (below), which in turn gates
    # openPort()/closePort()/openSecurePort() for this thread (see
    # _infer_call) -- a thread that calls openPort() but never
    # declares a handler here would silently sit accepting connections
    # nothing responds to (see that gate's own comment for why this
    # matters at RUNTIME, not just style).
    "request": ((_HTTP,), "(req:http)"),
    "upgrade": ((_SOCKET,), "(s:socket)"),
    "socketMessage": ((_SOCKET, _BLOB), "(s:socket, msg:blob)"),
    "socketClose": ((_SOCKET,), "(s:socket)"),
}

# claude.md #212: `on request`/`on upgrade`/`on socketMessage`/`on
# socketClose` -- any one of these declared inside a thread body sets
# has_http_handler, which _infer_call's own openPort/closePort/
# openSecurePort gate (below) requires before allowing this thread to
# call any of the three.
_THREAD_HTTP_HANDLER_NAMES = frozenset({"request", "upgrade", "socketMessage", "socketClose"})


def _check_message_handler_params(params, node, filename, structs, tables, enums, help_text):
    """claude.md #208: validates an `on message(worker:thread, msg:T)`
    handler's own parameter list -- shared verbatim between a thread's
    own inbound handler (inside `analyze_thread`) and the top-level
    handler receiving everything sent to main (`analyze_event_handler`)
    -- both are the SAME mechanism, just declared in different places:
    exactly 2 parameters, the first a bare `thread` (the generic,
    name=None variant -- see resolve_type_name's own "thread" case),
    the second any thread-sendable type (the receiver's own choice,
    T -- see _is_thread_sendable_type). Returns the resolved `msg`
    type. Every `postMessage()` send targeting this receiver is then
    `check_assignable`'d against it, exactly the way a struct/array/map
    field or an ordinary function parameter already is -- no separate
    inference machinery needed (an earlier design here DID try to
    INFER this from scattered postMessage() call sites instead of
    requiring a declaration, and hit the identical dead end
    _merge_thread_outbound_type's own history already found for the
    thread-outbound direction: there is no Festina syntax that could
    ever spell an anonymous, compiler-invented type for a callback/
    handler parameter to receive -- so this handler DECLARES msg's own
    type directly, the same way a thread's inbound type always has)."""
    if len(params) != 2:
        raise CompileError(
            f"'on message' must declare {help_text}, got {len(params)}",
            file=filename, line=node.line, column=node.column,
            category="invalid function argument type",
        )
    worker_type = apply_manually_managed(
        resolve_type_name(params[0].type_expr, structs, tables, enums, filename, node),
        params[0].manually_managed)
    if worker_type != types_mod.ThreadType(None):
        raise CompileError(
            f"'on message' must declare {help_text} -- its first parameter "
            f"must be a bare 'thread' (found "
            f"{types_mod.type_name(worker_type)})",
            file=filename, line=node.line, column=node.column,
            category="invalid function argument type",
        )
    msg_type = apply_manually_managed(
        resolve_type_name(params[1].type_expr, structs, tables, enums, filename, node),
        params[1].manually_managed)
    if not _is_thread_sendable_type(msg_type, structs, enums):
        raise CompileError(
            f"'on message(worker:thread, msg:{types_mod.type_name(msg_type)})': "
            # claude.md #218: `thread` belongs in this list too -- it
            # became a real, holdable value type in claude.md #208/#216
            # and is genuinely one of the types someone can try to send
            # (directly, or nested in a struct), so leaving it out made
            # the error name every unsendable type EXCEPT the one the
            # reader was actually holding.
            f"{types_mod.type_name(msg_type)} cannot cross a thread boundary -- "
            f"func/http/socket/regex/table/thread values are not sendable (see "
            f"claude.md #195's own list)",
            file=filename, line=node.line, column=node.column,
            category="invalid function argument type",
        )
    return msg_type

# claude.md #195: builtins a thread body may never call -- every one
# tied to exactly one piece of MAIN-thread-only shared state (the X11
# window/canvas, the one audio channel table, the one timer/async-io
# queue, the one process-wide sqlite handle, the one connection table,
# the process itself). Checked by bare Identifier name in _infer_call,
# which is why this only ever catches the FREE-FUNCTION forms -- an
# img-METHOD call (`someImg.drawRect(...)`) is a Member callee, a
# different AST shape entirely, and is always fine: it touches only
# that one private Cairo surface, confirmed safe for concurrent use of
# DIFFERENT surfaces on different threads. `blankImage` is deliberately
# NOT here for the identical reason -- it only ever creates a fresh,
# private surface, nothing shared. `sqlite` is deliberately NOT here
# (claude.md #199 Phase 5) -- it's gated by its own dedicated check in
# _infer_call instead, since the answer depends on THIS thread's own
# `database_url` (a thread that declared its own `DatabaseURL` may call
# it; one that didn't may not), a per-thread question this flat, unconditional set
# has no way to represent. `exec` is ALSO deliberately not here
# (claude.md #211) -- its only remaining form (claude.md #221 removed
# the non-blocking `exec(args, callback)` form, whose callback ran on
# MAIN's OS thread regardless of which thread dispatched it -- a real
# cross-thread-isolation violation) is the blocking, 1-argument one,
# whose own fork/execvp/waitpid touches no shared state at all
# (confirmed directly by reading festina_run_argv). `regex`/`mkdir`/`ls` are NOT here either
# (claude.md #211) -- confirmed safe by reading each: `regex()`'s own
# memoization slot is a per-CALL-SITE codegen-generated global
# (`_regex_memo_slots`, keyed by `id(Call node)`), lexically private
# to whichever one thread's generated code contains that call site,
# never shared; `mkdir`/`ls` are thin, purely local POSIX wrappers
# (`mkdir()`/`opendir()`/`readdir()`/`closedir()`) touching no shared
# global at all. `openPort`/`closePort`/`openSecurePort` are ALSO NOT
# here any more (claude.md #212 Phase 4) -- confirmed safe by
# converting festina_runtime_http.c's own connection/listener/handler
# state to `__thread` (the SAME storage class this file's own
# g_http_send_header_buf/festina_runtime.c's catch-frame stack already
# used), so a thread's own openPort() genuinely never shares so much
# as one fd with main or another thread's own context. Each is instead
# gated by its own dedicated check in _infer_call, mirroring the
# sqlite one's own shape exactly: legal only for a thread that has
# ALREADY declared at least one HTTP-shaped handler (on request/on
# upgrade/on socketMessage/on socketClose -- see
# _THREAD_HTTP_HANDLER_NAMES/info.has_http_handler), since that
# declaration is what makes codegen give this thread's own worker loop
# the bounded-poll shape that actually SERVICES a listener at all
# (festina_thread_set_http_context/festina_thread_http_service_pass) --
# without it, an accepted connection would just sit forever, nothing
# ever polling for it. `close` (the close(code) process-exit builtin --
# entirely unrelated to closePort/a socket's own .close()) stays
# unconditionally disallowed, unchanged.
_THREAD_DISALLOWED_BUILTINS = frozenset({
    "drawRect", "drawCircle", "drawText", "drawImage", "drawPixel",
    "clearRect", "clearCircle", "clearPixel", "clearCanvas",
    "fillStyle", "borderColor", "lineWidth", "changeFont",
    "measureTextWidth", "measureTextHeight",
    "render", "saveCanvas",
    "beginPath", "moveTo", "lineTo", "curveTo", "closePath",
    "fillPath", "strokePath",
    "translate", "rotate", "scale", "resetTransform",
    "saveState", "restoreState",
    "fillAlpha", "fillLinearGradient", "fillRadialGradient",
    "enterFullscreen", "exitFullscreen", "showCursor", "hideCursor",
    "setClientWidth", "setClientHeight",
    "setMaxAudioPlayers", "maxAudioPlayers", "stopAudioPlayer",
    "isAudioPlayerPlaying",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    "close",
})


def _is_thread_database_url_stmt(stmt):
    """claude.md #199 Phase 5: the thread-body counterpart of
    festina/imports.py's own `_is_database_url_assignment` -- the
    identical AST-shape match (an ExprStmt wrapping an
    `Identifier("DatabaseURL") = expr` Assign), duplicated rather than
    imported since imports.py's own version is scoped to the ENTRY
    FILE's raw pre-merge statement list, a wholly different stage of
    the pipeline than a thread body's own (already-parsed,
    already-merged) statement list this runs over instead."""
    return (isinstance(stmt, ast.ExprStmt)
            and isinstance(stmt.expr, ast.Assign)
            and isinstance(stmt.expr.target, ast.Identifier)
            and stmt.expr.target.name == "DatabaseURL")

# claude.md #39: clientWidth/clientHeight report the canvas window's
# current size as read-only global ints (borrowing the name from the
# DOM's Element.clientWidth/clientHeight, which they're the closest
# analogue to) -- pre-registered directly into global_scope below so a
# plain identifier reference just works through the same Scope.lookup
# every real global variable uses, and so Scope.define's own
# "already declared" check rejects a user var/function/struct/table
# with either name for free, with no extra machinery here.
_CLIENT_SIZE_GLOBALS = ("clientWidth", "clientHeight")

# claude.md #139: screenWidth/screenHeight -- the PHYSICAL display's own
# resolution, not the window's content size (that's clientWidth/
# clientHeight just above). Same read-only global-int registration
# shape, kept as its own tuple rather than folded into
# _CLIENT_SIZE_GLOBALS since the two answer genuinely different
# questions (a window can be smaller than the screen it's on).
_SCREEN_SIZE_GLOBALS = ("screenWidth", "screenHeight")
# Both size-global families are read-only the identical way and share
# every touch point below; combined once here so those touch points
# don't need to know there happen to be two separate families.
_SIZE_GLOBALS = _CLIENT_SIZE_GLOBALS + _SCREEN_SIZE_GLOBALS

# claude.md #181: devicePixelRatio -- how many actual device pixels
# back one canvas/CSS pixel (1.0 on a standard display, typically 2.0
# on a Retina/HiDPi one), read-only the identical way as _SIZE_GLOBALS
# just above -- a genuinely different QUESTION (a display's pixel
# density, not a width/height), and the wrong TYPE to fold into that
# same tuple (float, not int, so it needs its own Symbol registration
# below rather than sharing _SIZE_GLOBALS' single int-typed loop) even
# though it shares every other read-only-global touch point with it.
_DEVICE_PIXEL_RATIO_GLOBALS = ("devicePixelRatio",)

# The read-only-assignment check below needs both families in one set;
# the type-registration loop above still needs them kept apart (float
# vs int), so this combination exists only for that one shared check.
_READONLY_SCALAR_GLOBALS = _SIZE_GLOBALS + _DEVICE_PIXEL_RATIO_GLOBALS

# claude.md #71: `environment` -- unlike clientWidth/clientHeight above,
# this is never a valid *value* on its own (only environment.NAME or
# environment[keyExpr] mean anything, and NAME is arbitrary -- there's
# no fixed set of members to register the way clientWidth/clientHeight
# are their own two complete globals). So this name is pre-registered
# into global_scope purely for Scope.define's "already declared"
# collision protection (same free "a user can't redeclare this name"
# guarantee clientWidth/clientHeight get) -- its Symbol's `type` is
# never actually read anywhere; _infer_member and the bare-Identifier
# check below both special-case the AST shape (an Identifier literally
# named "environment") directly, before any real type-based dispatch,
# precisely so environment.NAME still resolves correctly even though a
# bare `environment` reference is deliberately rejected (see the
# Identifier branch in infer()).
_ENVIRONMENT_NAME = "environment"


class _NullType:
    def __repr__(self):
        return "null"


NULL = _NullType()  # claude.md #10, #25: null is valid for every type.


class Symbol:
    def __init__(self, name, type, kind, node=None):
        self.name = name
        self.type = type
        self.kind = kind  # variable | constant | function | parameter
        self.node = node

    def __repr__(self):
        return f"Symbol({self.name!r}, {self.type!r}, kind={self.kind!r})"


class Scope:
    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent

    def define(self, name, symbol, err_node, filename):
        if name in self.vars:
            if name == _ENVIRONMENT_NAME:
                # claude.md #71: environment is pre-registered into
                # global_scope purely so this collision is caught at all
                # (see analyze()) -- give it the same specific, named
                # treatment as a builtin-function-name collision
                # (analyze_func below) instead of the generic message,
                # since "already declared" alone doesn't explain *why*
                # (there's no earlier `environment` declaration in this
                # program to point a user back to).
                raise CompileError(
                    f"'{_ENVIRONMENT_NAME}' is reserved for reading environment "
                    f"variables ({_ENVIRONMENT_NAME}.NAME) and cannot be "
                    f"declared as a variable, constant, function, struct, or table",
                    file=filename, line=getattr(err_node, "line", 0),
                    column=getattr(err_node, "column", 0),
                    category="duplicate declaration",
                )
            raise CompileError(
                f"'{name}' is already declared",
                file=filename, line=getattr(err_node, "line", 0),
                column=getattr(err_node, "column", 0),
                category="duplicate declaration",
            )
        self.vars[name] = symbol

    def lookup(self, name):
        scope = self
        while scope is not None:
            if name in scope.vars:
                return scope.vars[name]
            scope = scope.parent
        return None


class _EnumInfo:
    """claude.md #176: the real data an `enum Name = Member1, Member2,
    ...` declaration carries -- `enums[name]` holds one of these, the
    same "the Type is just a name-handle, the real data lives in a
    side dict" split `structs`/`tables` already use for StructType/
    TableType. `members` is the resolved [Type, ...] list, in
    declaration order. `is_pure_struct` (every member a StructType)
    decides the runtime representation codegen picks: a zero-overhead
    self-tagged struct pointer when true, a heap-boxed {tag, value}
    pair when false -- and, independently, whether field access
    (`shape.radius`) is allowed at all (only ever true for a pure-
    struct enum)."""
    def __init__(self, members, is_pure_struct):
        self.members = members
        self.is_pure_struct = is_pure_struct


class _ThreadInfo:
    """claude.md #195/#208: the real data a `thread NAME { ... }`
    declaration carries -- threads[name] holds one of these, the same
    "the Type is just a name-handle, the real data lives in a side
    dict" split _EnumInfo/structs/tables already use. `inbound_type`
    is DECLARED, not inferred -- whatever this thread's own `on
    message(worker:thread, msg:T)` declares its `msg` parameter as
    (None if it has none -- such a thread accepts no messages at all,
    and `NAME.postMessage(x)` anywhere in the program is a compile
    error). There is no separate outbound-type concept any more:
    claude.md #208 replaced the old `NAME.onMessage(callback)`-plus-
    inferred-outbound-type mechanism with a single, unified, top-level
    `on message(worker:thread, msg:T)` handler that every thread's own
    bare `postMessage(x)` (still meaning "send to main") gets checked
    against directly -- see `main_message_type`/`_check_bare_send` on
    AnalyzedProgram, below."""
    def __init__(self, node):
        self.node = node
        self.inbound_type = None
        # claude.md #199 Phase 5: this thread's own resolved
        # `DatabaseURL = '<literal>'` first statement (None if it
        # never declared one) -- a thread with one gets its own
        # private sqlite handle and may call sqlite(); one without
        # one may not.
        # `database_url_node` is kept only for the whole-program
        # conflict check's own error reporting.
        self.database_url = None
        self.database_url_node = None
        # claude.md #209: `thread NAME[N] { ... }` -- None for an
        # ordinary singleton thread, or N (a positive int) for a pool.
        # ONE _ThreadInfo is shared by every index of a pool -- every
        # instance runs the identical body, so there is exactly one
        # `inbound_type`/`database_url` to reason about regardless of
        # which index a particular send targets.
        self.pool_size = node.pool_size
        # claude.md #212 (Phase 4 -- private per-thread HTTP context):
        # true once this thread's own body declares at least one of
        # on request/on upgrade/on socketMessage/on socketClose (see
        # _THREAD_HTTP_HANDLER_NAMES) -- gates openPort()/closePort()/
        # openSecurePort() for this thread (see _infer_call), the same
        # "gate the builtin on a per-thread capability" shape
        # `database_url` already gives sqlite().
        self.has_http_handler = False
        # claude.md #213 (Phase 5 -- giveRequest): WHICH of the four
        # this thread declared, by name -- has_http_handler alone
        # can't tell `NAME.giveRequest(r)` (which specifically
        # requires an `on request`, since that's the one handler it
        # dispatches) apart from a thread that only declared, say,
        # `on socketClose`. Populated by the identical hoisting
        # pre-scan that sets has_http_handler, same reasoning (order-
        # independent -- a thread declaring `on request` AFTER some
        # other statement that doesn't care shouldn't matter here
        # either).
        self.declared_http_handlers = set()
        # claude.md #217: this thread's own reply type -- unlike
        # inbound_type (DECLARED, on `on message`'s own `msg`
        # parameter), reply_type is INFERRED, from the first
        # `t.reply(...)` call site textually inside this thread's own
        # body (any handler, or a thread-private func reached from
        # one) -- None until then. There is no separate declaration
        # syntax for it (the user's own request didn't specify one),
        # so it works the same way claude.md #208's own now-removed
        # outbound-type inference used to, just scoped to ONE thread
        # and fixed by the FIRST call rather than merged across many --
        # every later `t.reply(...)` call in the same thread is
        # check_assignable'd against whatever the first one fixed
        # (enum-coercion included, exactly like an ordinary parameter).
        # Every `NAME.postMessage(x)` call site targeting a thread with
        # a non-None reply_type must chain `.callback(fn)` (fn's own
        # parameter type checked against this), or it's a compile
        # error -- see the `.callback` combined-pattern recognition in
        # _infer_call.
        self.reply_type = None


class AnalyzedProgram:
    def __init__(self, symbols, structs, tables, enums, imports, threads=None,
                 main_message_type=None, main_reply_type=None):
        self.symbols = symbols
        self.structs = structs
        self.tables = tables
        self.enums = enums
        self.imports = imports
        self.threads = threads if threads is not None else {}
        # claude.md #208: the top-level `on message(worker:thread,
        # msg:T)` handler's own declared `msg` type -- None if the
        # program never declares one at all (every bare `postMessage(x)`
        # call site anywhere is then a compile error, "nothing receives
        # this"). Every OTHER thread's own inbound type stays on its
        # own `_ThreadInfo.inbound_type` (main is not itself a `thread`
        # declaration, so it has no `_ThreadInfo` of its own to live
        # on -- this is its counterpart).
        self.main_message_type = main_message_type
        # claude.md #217: main's own reply type -- the exact
        # counterpart of `_ThreadInfo.reply_type`, for when main's own
        # top-level `on message` handler calls `worker.reply(x)`. None
        # until the first such call is analyzed (main never replies).
        self.main_reply_type = main_reply_type


def resolve_type_name(type_expr, structs, tables, enums=None, filename="<string>", node=None):
    if enums is None:
        enums = {}
    if isinstance(type_expr, ast.ArrayTypeExpr):
        return types_mod.ArrayType(resolve_type_name(type_expr.element, structs, tables, enums, filename, node),
                                    amortized=type_expr.amortized)
    if isinstance(type_expr, ast.FuncTypeExpr):
        # claude.md #141: func[T, T, ...]:R -- a first-class function
        # type. Resolved recursively the same way ArrayTypeExpr/
        # MapTypeExpr are, just over a LIST of param type expressions
        # instead of one. `"void"` is the same string sentinel
        # FuncDecl.return_type already uses for a void-returning
        # function, so this reads it the identical way analyze_func
        # does (`!= "void"` gates the resolve() call).
        param_types = tuple(
            resolve_type_name(p, structs, tables, enums, filename, node)
            for p in type_expr.param_types
        )
        return_type = (None if type_expr.return_type == "void"
                        else resolve_type_name(type_expr.return_type, structs, tables, enums, filename, node))
        return types_mod.FuncType(param_types, return_type)
    if isinstance(type_expr, ast.MapTypeExpr):
        value_type = resolve_type_name(type_expr.value, structs, tables, enums, filename, node)
        # claude.md #72: a map value is stored in one fixed 8-byte slot
        # (see types.MapType's own doc comment) -- an ArrayType (16
        # bytes: length + data pointer) or another MapType simply
        # doesn't fit, so this is rejected here at the point a map[T]
        # type is resolved, the same "unknown type"-shaped error a
        # genuinely undefined type name gets below.
        if isinstance(value_type, (types_mod.ArrayType, types_mod.MapType)):
            raise CompileError(
                f"map values cannot be {types_mod.type_name(value_type)} -- a map value is "
                f"stored in a single fixed-size slot, which an array or another map doesn't fit in",
                file=filename, line=getattr(node, "line", 0), column=getattr(node, "column", 0),
                category="unknown type",
            )
        return types_mod.MapType(value_type)
    name = type_expr
    if name in types_mod.PRIMITIVE_NAMES:
        return types_mod.PrimitiveType(name)
    if name == "img":
        return types_mod.ImageType()
    if name == "aud":
        return types_mod.AudioType()
    if name == "regex":
        return types_mod.RegexType()
    if name == "http":
        return types_mod.HttpType()
    if name == "url":
        return types_mod.UrlType()
    if name == "socket":
        return types_mod.SocketType()
    if name == "thread":
        # claude.md #208: the GENERIC `thread` type -- the only way
        # this string ever reaches here is a `worker:thread` parameter
        # (a specific declared thread's own type, e.g. `myWorker`'s
        # own ThreadType("myWorker"), is never spelled as a type
        # expression at all; it only ever exists as the compile-time
        # type of the bare identifier at a `myWorker.postMessage(...)`
        # call site). `name=None` deliberately never matches any
        # specific declared thread's own type (dataclass equality) --
        # see ThreadType's own doc comment.
        return types_mod.ThreadType(None)
    if name == "color":
        return types_mod.ColorType()
    if name == "font":
        return types_mod.FontType()
    if name in structs:
        return types_mod.StructType(name)
    if name in tables:
        return types_mod.TableType(name)
    if name in enums:
        return types_mod.EnumType(name)
    raise CompileError(
        f"unknown type '{name}'",
        file=filename, line=getattr(node, "line", 0), column=getattr(node, "column", 0),
        category="unknown type",
    )


_JSON_SCALAR_TYPES = (types_mod.PrimitiveType("int"), types_mod.PrimitiveType("float"),
                      types_mod.PrimitiveType("bool"), types_mod.PrimitiveType("text"))


def _is_json_parseable_type(t, structs, _seen_structs=frozenset()):
    """claude.md #173 (extends #159): is `t` a valid .toStruct()/
    .toArr() field/element/target type? v1 (claude.md #159) only
    allowed int/float/bool/text. This widens that to also allow a
    nested struct, arr[T] or map[T] -- exactly mirroring codegen's own
    _from_json_struct_fn_for/_from_json_arr_fn_for/_from_json_map_fn_for,
    which now recurse into a nested field/element's own from-JSON
    function the same way JSON *rendering* (_json_fn_for) already
    recurses for nested containers -- as long as EVERY scalar this type
    eventually bottoms out at is itself int/float/bool/text. A struct
    used to check itself (directly or through a cycle of other structs
    -- claude.md #17 made self-referencing structs legal generally) is
    treated as valid without re-descending into it again: the check
    already in progress for that struct, higher up this same recursion,
    is the one that actually decides it, and re-entering it here would
    only ever recurse forever without ever reaching a different answer.
    `map[T]`'s own value can never itself be an ArrayType/MapType --
    resolve_type_name above already rejects that at the point a map[T]
    type is RESOLVED, so there is nothing left for this function to
    reject on that axis; it only ever needs to keep recursing through
    struct fields, array elements and map values until every leaf is
    scalar."""
    if t in _JSON_SCALAR_TYPES:
        return True
    if isinstance(t, types_mod.StructType):
        if t.name in _seen_structs:
            return True
        seen = _seen_structs | {t.name}
        return all(_is_json_parseable_type(ftype, structs, seen)
                   for ftype in structs.get(t.name, {}).values())
    if isinstance(t, types_mod.ArrayType):
        return _is_json_parseable_type(t.element, structs, _seen_structs)
    if isinstance(t, types_mod.MapType):
        return _is_json_parseable_type(t.value, structs, _seen_structs)
    return False


_THREAD_SENDABLE_SCALAR_TYPES = (
    types_mod.PrimitiveType("int"), types_mod.PrimitiveType("float"),
    types_mod.PrimitiveType("bool"), types_mod.PrimitiveType("text"),
    types_mod.ColorType(), types_mod.FontType(),
)


def _is_thread_sendable_type(t, structs, enums, _seen_structs=frozenset()):
    """claude.md #195: is `t` safe to deep-clone across a thread
    boundary -- a thread's own inbound type (its `on message(p:T)`),
    and every type inferred for its outbound direction (its own
    `postMessage(x)` call sites)? Mirrors _is_json_parseable_type's
    recursive-walk shape exactly (same idea: keep descending through
    struct fields/array elements/map values/enum members until every
    leaf is checked, with the identical "a struct already being
    checked higher up this same recursion is trusted, not re-entered"
    cycle guard), with a different leaf set: scalars, PLUS blob/img/
    aud/url (mechanically clonable -- malloc-and-copy for blob/aud/url,
    a lossless bytes-encode/decode round trip for img -- no live OS
    resource in any of them, confirmed during design). Rejected:
    `func` (no closures to send -- claude.md #141), `http`/`socket`
    (tied to the single main-thread connection table -- claude.md
    #151), `regex` (an escaping regex has no retained pattern text to
    recompile from -- claude.md #86), and `table` (query-result rows
    are not a general value type).

    claude.md #202 Phase 2: `T?` -- checked first, ahead of even the
    scalar cases, since a manually-managed value crosses a thread
    boundary by sharing its own raw reference (codegen's
    _emit_thread_clone_value), never a deep clone -- no structural walk
    needed at all, so this is sendable regardless of what's inside it,
    INCLUDING a genuinely self-referencing (cyclic) struct/arr[T]/
    map[T], which an ordinary (non-manually-managed) one of the same
    shape is correctly still rejected for elsewhere (codegen's
    _is_cyclic_type/_is_thread_clonable_type) -- there is no clone
    recursion left to loop forever on a cyclic VALUE when nothing is
    ever cloned. Sound for the identical reason sharing a manually-
    managed value's pointer across threads is sound at all: neither
    side's automatic bookkeeping ever touches its refcount, so there is
    no non-atomic increment/decrement for two threads to race on."""
    if getattr(t, "manually_managed", False):
        return True
    if t in _THREAD_SENDABLE_SCALAR_TYPES:
        return True
    if t == types_mod.PrimitiveType("blob") or isinstance(
            t, (types_mod.ImageType, types_mod.AudioType, types_mod.UrlType)):
        return True
    if isinstance(t, types_mod.StructType):
        if t.name in _seen_structs:
            return True
        seen = _seen_structs | {t.name}
        return all(_is_thread_sendable_type(ftype, structs, enums, seen)
                   for ftype in structs.get(t.name, {}).values())
    if isinstance(t, types_mod.ArrayType):
        return _is_thread_sendable_type(t.element, structs, enums, _seen_structs)
    if isinstance(t, types_mod.MapType):
        return _is_thread_sendable_type(t.value, structs, enums, _seen_structs)
    if isinstance(t, types_mod.EnumType):
        info = enums.get(t.name)
        if info is None:
            return True  # name already validated to exist by resolve_type_name
        return all(_is_thread_sendable_type(m, structs, enums, _seen_structs) for m in info.members)
    return False


def _iter_func_decls(stmts):
    """claude.md #140: yields every ast.FuncDecl reachable from `stmts`,
    however deeply nested -- inside a Block, either arm of an IfStmt, a
    While/ForStmt body, an EventHandler body, or even another FuncDecl's
    own body (a function nested inside a function, which the parser
    already allows and analyze_func already treats as an ordinary
    global declaration regardless of nesting -- see its own comment).
    This traversal shape must stay in lockstep with analyze_statement/
    analyze_block's own recursive descent -- every statement kind that
    can hold a nested statement list is walked here the identical way,
    since a FuncDecl analyze_statement would eventually reach but this
    pre-pass skipped would defeat hoisting for exactly that one
    declaration (it would still analyze fine when the real pass reaches
    it, just too late for anything calling it earlier to see it)."""
    for stmt in stmts:
        if isinstance(stmt, ast.FuncDecl):
            yield stmt
            yield from _iter_func_decls(stmt.body.body)
        elif isinstance(stmt, ast.EventHandler):
            yield from _iter_func_decls(stmt.body.body)
        elif isinstance(stmt, ast.Block):
            yield from _iter_func_decls(stmt.body)
        elif isinstance(stmt, ast.IfStmt):
            yield from _iter_func_decls(stmt.then.body)
            if isinstance(stmt.orelse, ast.IfStmt):
                yield from _iter_func_decls([stmt.orelse])
            elif stmt.orelse is not None:
                yield from _iter_func_decls(stmt.orelse.body)
        elif isinstance(stmt, (ast.WhileStmt, ast.ForStmt)):
            yield from _iter_func_decls(stmt.body.body)


def analyze(program, filename="<string>"):
    global_scope = Scope()
    # claude.md #195: a SEPARATE scope, holding only function symbols
    # (populated in lockstep with global_scope by register_func_signature
    # below, never anything else) -- what a thread's own isolated
    # handler bodies parent on INSTEAD of global_scope, so global
    # variables/constants become invisible while function names (and
    # struct/table/enum type names, already their own namespace outside
    # Scope entirely) stay visible. No parent of its own: nothing above
    # a thread's own isolation boundary should ever be reachable from
    # inside it.
    functions_scope = Scope(None)
    structs = {}
    tables = {}
    enums = {}  # claude.md #176: name -> _EnumInfo
    threads = {}  # claude.md #195: name -> _ThreadInfo
    imports = []
    entry_filename = filename  # see the DatabaseURL check at the bottom
    # claude.md #195: a one-slot mutable box naming which thread's OWN
    # handler body is currently being analyzed (a _ThreadInfo, or None
    # outside any thread) -- NOT a new parameter threaded through
    # infer()/_infer_call()'s entire recursive call graph (which would
    # touch dozens of call sites for a fact only a handful of them ever
    # need); the same "a single nonlocal slot, not a parameter
    # everywhere" shape next_arrow_name's own _arrow_counter above
    # already uses. Read by _infer_call's bare `postMessage(x)` handling
    # (to know it's inside a thread body at all -- see claude.md #208,
    # bare postMessage checks against `_main_message_type[0]` now, not
    # a per-thread accumulator) and its disallowed-builtins/disallowed-
    # function-call checks (to know whether the CURRENT call site is
    # inside an isolated thread body at all).
    _current_thread = [None]
    # claude.md #208: the top-level `on message(worker:thread, msg:T)`
    # handler's own declared `msg` type, set once by
    # analyze_event_handler when it reaches that declaration (None
    # until then, and forever if the program never declares one at
    # all) -- every bare `postMessage(x)` call site anywhere checks
    # against this directly, mirroring exactly how `NAME.postMessage(x)`
    # already checks against a specific thread's own declared
    # `inbound_type`. Ordinary "declared before referenced" program-
    # order rules apply (the same rule a thread's own name already has
    # -- see analyze_thread's own doc comment): a bare postMessage(x)
    # textually BEFORE the top-level `on message` declaration is a
    # compile error, same as referencing any other not-yet-declared
    # name would be.
    _main_message_type = [None]
    # claude.md #217: main's own reply type -- the exact counterpart of
    # `_ThreadInfo.reply_type`, for when main's own top-level `on
    # message` handler calls `worker.reply(x)` (a worker replying to
    # ITS sender, when that sender is main). Set by the first such call
    # found while analyzing the top-level handler's body; every
    # `NAME.postMessage(x)` call site made FROM INSIDE a thread body,
    # targeting main via the bare form, checks against this the same
    # way a named send checks against a thread's own reply_type.
    _main_reply_type = [None]
    # claude.md #142: one monotonic counter for every arrow-function
    # expression's own synthesized name (__festina_arrow_N) -- a plain
    # int, not a list-wrapped closure cell, since every read/increment
    # happens through the single next_arrow_name() closure below rather
    # than needing `nonlocal` at each call site.
    _arrow_counter = 0

    def next_arrow_name():
        nonlocal _arrow_counter
        name = f"__festina_arrow_{_arrow_counter}"
        _arrow_counter += 1
        return name

    # claude.md #39/#139: clientWidth/clientHeight/screenWidth/screenHeight,
    # see _SIZE_GLOBALS above.
    for _name in _SIZE_GLOBALS:
        global_scope.define(_name, Symbol(_name, _INT, "constant", None), None, filename)
    # claude.md #181: devicePixelRatio -- see _DEVICE_PIXEL_RATIO_GLOBALS
    # above for why this isn't folded into the loop just above (float,
    # not int).
    for _name in _DEVICE_PIXEL_RATIO_GLOBALS:
        global_scope.define(_name, Symbol(_name, _FLOAT, "constant", None), None, filename)
    # claude.md #71: environment, see _ENVIRONMENT_NAME above -- type is
    # irrelevant (never consulted), only here so redeclaring it is a
    # duplicate-declaration error like any other reserved global.
    global_scope.define(_ENVIRONMENT_NAME, Symbol(_ENVIRONMENT_NAME, None, "constant", None), None, filename)
    # claude.md #150: argv -- an ordinary, MUTABLE global arr[text]
    # (kind "variable", unlike clientWidth/environment just above),
    # since it's a plain snapshot captured once at process startup, not
    # a live system readout -- nothing about reassigning it, or calling
    # push()/splice()/etc on it, is any different from any other
    # declared arr[text] variable, so there's no reason to forbid it
    # the way clientWidth's own read-only checks do. Pre-registered
    # only so redeclaring `argv` is a duplicate-declaration error like
    # any other reserved global; codegen.py's own generate() is the
    # other half (its own pre-registration, plus populating the real
    # value from argc/argv in main()'s prologue).
    global_scope.define("argv", Symbol("argv", types_mod.ArrayType(_TEXT), "variable", None), None, filename)

    def resolve(type_expr, node=None):
        return resolve_type_name(type_expr, structs, tables, enums, filename, node)

    def check_assignable(declared, actual, node, what="value"):
        if actual is None or actual is NULL or declared is None:
            return
        # claude.md #36: blob's only worked example ("blob data =
        # 'path/to/file'") assigns a plain string literal -- which
        # infers as `text` (there's no separate blob-literal syntax
        # anywhere in claude.md) -- directly to a blob-declared
        # variable. Without this, that example wouldn't compile at all,
        # and blob would be completely unconstructible (no builtin
        # returns one either), so text -> blob is allowed here
        # one-directionally, matching the only direction claude.md ever
        # shows; codegen needs no special coercion for it either, since
        # blob and text already share the identical `ptr` runtime
        # representation (see _llvm_type).
        # claude.md #202: _is_blob_type rather than `declared == _BLOB`
        # -- see its own doc comment; otherwise this coercion silently
        # stops working for `blob?` specifically.
        if _is_blob_type(declared) and actual == _TEXT:
            return
        # claude.md #91: `color red = 'red'` / `font body = '13px arial'`
        # -- a colour and a font are written as text because that is what
        # reads well, and resolved to their compiled form at the
        # declaration. Same one-directional text -> X allowance blob
        # already has above, and for the same reason: there is no
        # separate literal syntax for either, and nothing else in the
        # language could construct one. Whether the value is a genuine
        # LITERAL (and so resolvable at all) is checked in codegen, which
        # is where the resolution happens and where the error can name
        # the offending text.
        if (isinstance(declared, (types_mod.ColorType, types_mod.FontType))
                and actual == _TEXT):
            return
        # claude.md #100/#101: `aud music = 'path/track.wav'` and
        # `img sprite = 'sprite.png'` -- the same
        # one-directional text -> X allowance, for the same reason the
        # three above have it: a path is what reads well, and there is no
        # other literal syntax for an audio clip. Unlike colour and font
        # this is NOT resolved at compile time -- it becomes a real
        # loadAudio() call wherever the conversion happens (see codegen's
        # _coerce), so the path may be any text expression, not just a
        # literal. claude.md #101 gave `img` the same treatment, so the
        # two media types no longer differ for no reason. That also means it is a genuine file read at that
        # point, which is worth knowing when the conversion is at a call
        # site rather than a declaration.
        if isinstance(declared, (types_mod.AudioType, types_mod.ImageType)) and actual == _TEXT:
            return
        # claude.md #102: `arr[text] a = [null]` / `map[int] m = {'k': null}`.
        # A literal whose values are ALL null infers its element type as
        # null itself, and that was then rejected against every declared
        # element type -- so the one literal shape that says "empty of
        # meaning but not empty of entries" could not be written at all,
        # even though `a.push(null)` and `m[k] = null` were both already
        # fine and `[null, 'x']` inferred text without complaint. null is
        # a valid value of every type (claude.md #10/#25), so a container
        # of nulls is assignable to a container of anything.
        for container in (types_mod.ArrayType, types_mod.MapType):
            if isinstance(declared, container) and isinstance(actual, container):
                inner = actual.element if container is types_mod.ArrayType else actual.value
                if inner is NULL or inner is None:
                    return
        # claude.md #176: a member type coerces into its enum "pseudo
        # type" -- e.g. Circle -> Shape for `enum Shape = Circle,
        # Square`. One check here covers every position this function
        # already gates (var decl, function param/return, struct
        # field, array/map element, ...), the same way the container-
        # null tolerance just above does.
        #
        # claude.md #205: a real, ASan-confirmed heap-use-after-free
        # was possible here before the `not manually_managed` guard --
        # this bypass used to fire regardless of whether `declared`
        # (the enum) was manually-managed, letting an ORDINARY,
        # automatically-managed member value (`Circle c; ...; Shape?
        # shape = c`) flow straight into a manually-managed binding
        # with no retain (codegen's own retain-skip for a manually-
        # managed declaration is unconditional on `stmt.manually_
        # managed`, exactly the same as every other eligible type --
        # see claude.md #202's own codegen section). Once `c` went out
        # of scope and its own automatic release dropped the last
        # reference, `shape` was left pointing at freed memory --
        # confirmed directly, not just reasoned about, with a real
        # AddressSanitizer heap-use-after-free report. Skipping this
        # bypass when `declared` is manually-managed falls through to
        # the strict `declared != actual` check below instead, which
        # correctly rejects a plain member value the same way it
        # already rejects a plain `Circle` flowing into `Circle?` --
        # `analyze_var_decl`'s own fresh-construction escape hatch
        # (claude.md #204) still lets a FRESH member value in (e.g.
        # `Shape? shape = makeCircle()`), since it checks against the
        # BARE enum type, which reaches this exact branch un-flagged.
        if isinstance(declared, types_mod.EnumType) and not declared.manually_managed:
            info = enums.get(declared.name)
            if info is not None and actual in info.members:
                return
        if declared != actual:
            raise CompileError(
                f"cannot assign {what} of type {types_mod.type_name(actual)} "
                f"to {types_mod.type_name(declared)}",
                file=filename, line=getattr(node, "line", 0), column=getattr(node, "column", 0),
                category="invalid assignment",
            )

    def check_condition_bool(cond_type, node):
        if cond_type is None:
            return
        if cond_type != types_mod.PrimitiveType("bool"):
            raise CompileError(
                f"condition must be bool, found {types_mod.type_name(cond_type)}",
                file=filename, line=getattr(node, "line", 0), column=getattr(node, "column", 0),
                category="invalid condition type",
            )

    def infer(expr, scope):
        if isinstance(expr, ast.NumberLit):
            return types_mod.PrimitiveType("float" if isinstance(expr.value, float) else "int")
        if isinstance(expr, ast.StringLit):
            return types_mod.PrimitiveType("text")
        if isinstance(expr, ast.BoolLit):
            return types_mod.PrimitiveType("bool")
        if isinstance(expr, ast.NullLit):
            return NULL
        if isinstance(expr, ast.RegexLit):
            # claude.md #67: /pattern/flags -- same RegexType() a
            # regex(...) call infers (see BUILTIN_FUNCTIONS/
            # _BUILTIN_RETURN_TYPES above); flags were already validated
            # for real (unsupported/duplicate letters) by the parser,
            # since a literal's flags are compile-time-known text, unlike
            # regex()'s flags argument.
            return types_mod.RegexType()
        if isinstance(expr, ast.TemplateLit):
            for e in expr.exprs:
                infer(e, scope)
            return types_mod.PrimitiveType("text")
        if isinstance(expr, ast.ArrayLit):
            elem_type = None
            # concrete_type tracks the first non-null element's type, purely
            # to catch a genuine mismatch between two concrete element types
            # (e.g. [1, 'x', true]) -- before this check existed, a mixed
            # literal like that reached codegen and failed as a raw,
            # confusing "LLVM object emission failed" error instead of a
            # normal CompileError, the same class of gap the security.md
            # audit fixed for comparisons/array indices/etc. but missed
            # here. elem_type itself is left tracking the *last* element's
            # type, same as before this check -- that's what feeds the
            # returned ArrayType below, and several existing null-element
            # corner cases (e.g. `[5, null]` failing against a declared
            # arr[int]) already depend on that exact "last wins" value;
            # this fix only adds a mismatch check, it doesn't change what
            # gets returned.
            concrete_type = None
            for e in expr.elements:
                elem_type = infer(e, scope)
                if elem_type is not None and elem_type is not NULL:
                    if concrete_type is None:
                        concrete_type = elem_type
                    elif elem_type != concrete_type:
                        raise CompileError(
                            f"array literal elements must all be the same type, "
                            f"found {types_mod.type_name(concrete_type)} and "
                            f"{types_mod.type_name(elem_type)}",
                            file=filename, line=getattr(e, "line", 0), column=getattr(e, "column", 0),
                            category="invalid operand type",
                        )
            return types_mod.ArrayType(elem_type) if elem_type is not None else None
        if isinstance(expr, ast.MapLit):
            # claude.md #72: { key: value, ... } -- every key must be
            # text (checked per-entry, unlike ArrayLit's value type
            # above which only ever keeps the *last* element's type --
            # a key's type genuinely needs checking every time, since
            # nothing else ever constrains it the way a declared
            # map[T]'s value slot constrains values). value_type follows
            # ArrayLit's own convention exactly: None (needs a declared
            # type for context, e.g. an empty `{}`) if there's nothing
            # to infer it from.
            #
            # claude.md #153: value_type used to just get overwritten on
            # every entry with nothing checking it against the PREVIOUS
            # entry's own answer -- so a mixed-value literal like
            # {'a': 1, 'b': 'two'} passed this check silently and reached
            # codegen, which then emitted invalid LLVM IR (a raw i64
            # where a ptr was required, or vice versa) instead of a clean
            # compile error. A real, pre-existing gap, first found and
            # left open by claude.md #151's own testing (its own
            # writeup: "map literals never check that every entry shares
            # one value type... left for its own separate round").
            # concrete_value_type mirrors ArrayLit's concrete_type above
            # exactly -- tracks the first non-null value's type purely to
            # catch a genuine mismatch; value_type itself is left
            # tracking the *last* entry's type, unchanged from before
            # this fix, since that's what feeds the returned MapType and
            # existing null-value corner cases already depend on it.
            value_type = None
            concrete_value_type = None
            # Duplicate-literal-key detection: "last value wins" for a
            # repeated key (claude.md #72) is fine as a runtime rule when
            # the key is a general text expression -- there's no way to
            # know at compile time whether two different expressions
            # produce the same string. But when BOTH keys in a pair are
            # plain string literals (ast.StringLit, not a variable/
            # template/other expression), the duplicate is knowable right
            # now, at zero runtime cost, and it's essentially always a
            # typo rather than something intentional -- the same
            # "catch it before it runs" instinct as the const-reassignment
            # and void-return checks (security.md's audit). Tracked by
            # literal string value, first-occurrence node only (so the
            # error always points at the *second*, redundant one).
            seen_literal_keys = {}
            for key_expr, val_expr in expr.entries:
                key_type = infer(key_expr, scope)
                if key_type is not None and key_type is not NULL and key_type != _TEXT:
                    raise CompileError(
                        f"map key must be text, found {types_mod.type_name(key_type)}",
                        file=filename, line=getattr(key_expr, "line", 0), column=getattr(key_expr, "column", 0),
                        category="invalid operand type",
                    )
                if isinstance(key_expr, ast.StringLit):
                    if key_expr.value in seen_literal_keys:
                        raise CompileError(
                            f"duplicate map key '{key_expr.value}' in this literal "
                            f"(the earlier entry for this key would never be seen)",
                            file=filename, line=getattr(key_expr, "line", 0), column=getattr(key_expr, "column", 0),
                            category="invalid operand type",
                        )
                    seen_literal_keys[key_expr.value] = key_expr
                value_type = infer(val_expr, scope)
                if value_type is not None and value_type is not NULL:
                    if concrete_value_type is None:
                        concrete_value_type = value_type
                    elif value_type != concrete_value_type:
                        raise CompileError(
                            f"map literal values must all be the same type, "
                            f"found {types_mod.type_name(concrete_value_type)} and "
                            f"{types_mod.type_name(value_type)}",
                            file=filename, line=getattr(val_expr, "line", 0), column=getattr(val_expr, "column", 0),
                            category="invalid operand type",
                        )
            return types_mod.MapType(value_type) if value_type is not None else None
        if isinstance(expr, ast.ArrowFuncExpr):
            # claude.md #142: `void (arg:text) => log(arg)` compiles to
            # an ordinary, synthesized top-level function
            # (__festina_arrow_N(arg:text) { log(arg) }), with the
            # arrow expression itself evaluating to a func[...]:...
            # VALUE referring to it -- "arrow functions compile to
            # regular functions," the request's own framing. Built and
            # analyzed HERE, once, synchronously -- NOT through claude.md
            # #140's whole-program hoisting pre-pass, which an arrow
            # function has no use for at all: it has no name a forward
            # reference could ever spell (its synthesized name is
            # invisible to Festina source), so the synthesized FuncDecl
            # only ever needs to exist by the time this expression
            # itself is reached, never any earlier.
            param_types = tuple(apply_manually_managed(resolve(p.type_expr, expr), p.manually_managed)
                               for p in expr.params)
            return_type = None if expr.return_type == "void" else resolve(expr.return_type, expr)
            # claude.md #23's own void-vs-non-void rules already reject
            # `return <value>` inside a void function -- so a void arrow
            # function's body expression becomes a bare ExprStmt
            # (evaluated for its side effects, result discarded), not
            # literally `return <expr>` the way the request's own
            # comment shows it (a simplification in the request's own
            # wording that would not actually typecheck as written --
            # see claude.md #142's own log entry). A non-void arrow
            # function's body IS `return <expr>`, matching the
            # request's example exactly for that case.
            if return_type is None:
                body_block = ast.Block([ast.ExprStmt(expr.body)])
            else:
                body_block = ast.Block([ast.Return(expr.body, expr.line, expr.column)])
            decl = ast.FuncDecl(next_arrow_name(), expr.return_type, expr.params,
                                 body_block, expr.line, expr.column)
            register_func_signature(decl)
            analyze_func(decl)
            # claude.md #142: codegen.py re-walks this SAME AST object
            # (see festina/cli.py's compile_file) -- stashing the
            # synthesized FuncDecl here is what lets it emit the exact
            # same function codegen needs, rather than re-synthesizing
            # an independent (and, absent careful coordination,
            # possibly desynced) name of its own.
            expr.decl = decl
            return types_mod.FuncType(param_types, return_type)
        if isinstance(expr, ast.Identifier):
            # claude.md #71: `environment` alone means nothing -- only
            # environment.NAME/environment[keyExpr] do (see
            # _ENVIRONMENT_NAME above). Checked by name, before the
            # generic scope.lookup below, since environment IS present
            # in global_scope (for collision protection only) and would
            # otherwise silently return its placeholder `type=None`
            # here instead of a clear, specific error.
            if expr.name == _ENVIRONMENT_NAME:
                raise CompileError(
                    f"'{_ENVIRONMENT_NAME}' must be accessed as {_ENVIRONMENT_NAME}.NAME "
                    f"(e.g. {_ENVIRONMENT_NAME}.DATABASE_URL), not used by itself",
                    file=filename, line=expr.line, column=expr.column,
                    category="invalid field access",
                )
            sym = scope.lookup(expr.name)
            if sym is None:
                raise CompileError(
                    f"unknown variable '{expr.name}'",
                    file=filename, line=expr.line, column=expr.column,
                    category="unknown variable",
                )
            if sym.kind == "function":
                # claude.md #141: a bare reference to a function's own
                # NAME -- not immediately called -- is a first-class
                # function VALUE now, typed func[paramTypes]:returnType.
                # Built fresh from the FuncDecl every time (never cached
                # on the Symbol itself), so `sym.type` keeps meaning
                # exactly what it always has everywhere else this Symbol
                # is read: the RETURN type of a CALL to this function
                # (_infer_call's own Identifier-callee branch, just
                # below, still reads sym.type directly for that, unaware
                # this branch even exists -- the two paths are told
                # apart structurally, by whether the Identifier is the
                # callee of an immediately-enclosing Call or not, not by
                # anything stored on the Symbol).
                decl = sym.node
                param_types = tuple(apply_manually_managed(resolve(p.type_expr, decl), p.manually_managed)
                                   for p in decl.params)
                ret_type = resolve(decl.return_type, decl) if decl.return_type != "void" else None
                return types_mod.FuncType(param_types, ret_type)
            if sym.kind == "thread_function":
                # claude.md #210: unlike an ordinary top-level function,
                # a thread-private helper has no first-class VALUE form
                # -- it may only ever be CALLED by name (the branch
                # above, `sym.kind == "function"`, deliberately doesn't
                # also match "thread_function"). Never explicitly
                # requested, and closing over one thread's own private
                # state makes "hand this function pointer to code
                # outside that thread" a can of worms (claude.md #195's
                # own isolation model) this phase doesn't need to open.
                raise CompileError(
                    f"'{expr.name}' is a thread-private function -- it can only be "
                    f"called (e.g. '{expr.name}(...)'), not referenced as a value",
                    file=filename, line=expr.line, column=expr.column,
                    category="invalid function argument type",
                )
            return sym.type
        if isinstance(expr, ast.Member):
            return _infer_member(expr, scope)
        if isinstance(expr, ast.Call):
            return _infer_call(expr, scope)
        if isinstance(expr, ast.Assign):
            # claude.md #63: ".length" is read-only -- caught here, before
            # the generic infer(expr.target) below, which would otherwise
            # happily type-check `arr.length = n` as a plain int
            # assignment (the ArrayType branch in _infer_member has no
            # way to tell a read apart from a write target).
            if (isinstance(expr.target, ast.Member) and not expr.target.computed
                    and expr.target.prop == "length"
                    and isinstance(infer(expr.target.obj, scope), types_mod.ArrayType)):
                raise CompileError(
                    "'.length' is read-only and cannot be assigned to",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid assignment",
                )
            # claude.md #39/#139/#181: clientWidth/clientHeight/
            # screenWidth/screenHeight/devicePixelRatio are read-only
            # too -- same reasoning and same "catch it before the
            # generic target_type/value_type check below" placement as
            # .length above, since that check alone has no way to tell
            # a read from a write target.
            if isinstance(expr.target, ast.Identifier) and expr.target.name in _READONLY_SCALAR_GLOBALS:
                raise CompileError(
                    f"'{expr.target.name}' is read-only and cannot be assigned to",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid assignment",
                )
            # claude.md #71: environment.NAME/environment[keyExpr] are
            # read-only too -- same placement/reasoning as .length and
            # clientWidth/clientHeight just above.
            if (isinstance(expr.target, ast.Member)
                    and isinstance(expr.target.obj, ast.Identifier)
                    and expr.target.obj.name == _ENVIRONMENT_NAME):
                raise CompileError(
                    f"'{_ENVIRONMENT_NAME}' is read-only and cannot be assigned to",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid assignment",
                )
            # claude.md #150: text[i] is read-only too -- same placement/
            # reasoning as the checks just above. text has no in-place
            # mutation anywhere else in this language either (.replace()
            # etc. all return a NEW text rather than editing one in
            # place), so `s[i] = 'x'` would be the one exception to that
            # rule rather than a natural extension of it.
            if (isinstance(expr.target, ast.Member) and expr.target.computed
                    and infer(expr.target.obj, scope) == _TEXT):
                raise CompileError(
                    "text is immutable -- s[i] can only be read, not assigned to",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid assignment",
                )
            # claude.md #162/#163: http's own five fields (url/method/
            # code/headers/callback) are read-only too -- same
            # placement/reasoning as .length above (the ArrayType-style
            # generic check below can't tell a read from a write target
            # either). The only way to SET them is the literal-
            # construction syntax (`http x = {...}`) at creation time.
            if (isinstance(expr.target, ast.Member) and not expr.target.computed
                    and expr.target.prop in ("url", "method", "code", "headers", "callback")
                    and isinstance(infer(expr.target.obj, scope), types_mod.HttpType)):
                raise CompileError(
                    f"'.{expr.target.prop}' is read-only and cannot be assigned to",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid assignment",
                )
            # claude.md #162: every url field is read-only, same
            # placement/reasoning as http's own fields just above -- a
            # url is built once, by parseURL(), never mutated afterward.
            if (isinstance(expr.target, ast.Member) and not expr.target.computed
                    and expr.target.prop in ("hash", "hostname", "password", "pathname",
                                              "port", "protocol", "searchParams", "username")
                    and isinstance(infer(expr.target.obj, scope), types_mod.UrlType)):
                raise CompileError(
                    f"'.{expr.target.prop}' is read-only and cannot be assigned to",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid assignment",
                )
            # claude.md #188 (uraikus/festina#76 item 5): row.rowid is
            # read-only too, same placement/reasoning as .length/http's
            # own fields/url's own fields above -- it's the row's own
            # database identity, not ordinary column data, so mutating
            # it in memory would never mean anything (unlike an ordinary
            # column, there is no corresponding `UPDATE` this language
            # performs on assignment -- a table row is a plain in-memory
            # snapshot either way, see the module docstring's "Query
            # rows" note).
            if (isinstance(expr.target, ast.Member) and not expr.target.computed
                    and expr.target.prop == "rowid"
                    and isinstance(infer(expr.target.obj, scope), types_mod.TableType)):
                raise CompileError(
                    "'.rowid' is read-only and cannot be assigned to",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid assignment",
                )
            # claude.md #22: a `const`-declared variable cannot be
            # reassigned -- the whole point of "constant," and needed
            # for "Constants should be available for compiler
            # optimization" to actually hold (an optimization assuming
            # a const never changes would be unsafe if reassignment
            # were silently allowed). Checked here for the same reason
            # .length/clientWidth's read-only checks are: the generic
            # target_type/value_type check below only verifies type
            # compatibility, it can't tell a legal write target from an
            # illegal one. Postfix ++/-- already separately rejects a
            # constant operand (see PostfixOp below) -- that's a
            # different AST node, so it needs its own check rather than
            # sharing this one.
            if isinstance(expr.target, ast.Identifier):
                target_sym = scope.lookup(expr.target.name)
                if target_sym is not None and target_sym.kind == "constant":
                    raise CompileError(
                        f"cannot assign to constant '{expr.target.name}'",
                        file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                        category="invalid assignment",
                    )
            target_type = infer(expr.target, scope)
            value_type = infer(expr.value, scope)
            check_assignable(target_type, value_type, expr)
            return target_type
        if isinstance(expr, ast.PostfixOp):
            # claude.md #66: postfix ++/-- -- valid only on a mutable int
            # variable.
            if not isinstance(expr.operand, ast.Identifier):
                raise CompileError(
                    f"'{expr.op}' can only be used on a variable, not an arbitrary expression",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid operand type",
                )
            sym = scope.lookup(expr.operand.name)
            if sym is None:
                raise CompileError(
                    f"unknown variable '{expr.operand.name}'",
                    file=filename, line=expr.operand.line, column=expr.operand.column,
                    category="unknown variable",
                )
            if sym.type != _INT:
                raise CompileError(
                    f"'{expr.op}' requires an int operand, found {types_mod.type_name(sym.type)}",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid operand type",
                )
            if sym.kind == "constant":
                raise CompileError(
                    f"cannot use '{expr.op}' on constant '{expr.operand.name}'",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid operand type",
                )
            return _INT
        if isinstance(expr, ast.Ternary):
            cond_type = infer(expr.test, scope)
            check_condition_bool(cond_type, expr)
            cons_type = infer(expr.cons, scope)
            alt_type = infer(expr.alt, scope)
            # claude.md #192: the two branches must have compatible
            # types, checked the same way == / != checks its operands
            # (and for the same reason -- #2's "no implicit coercion").
            # The alt branch's type used to be inferred and DISCARDED,
            # with the whole expression typed from the cons branch
            # alone, so `c ? 'yes' : someBlob` silently compiled and
            # rendered a blob handle's bytes as text, and `c ? xs :
            # someMap` treated a map header as an array. null is valid
            # against everything (#25), and the result is the OTHER,
            # concrete branch's type (so `c ? null : 7` is int, not
            # null); int/float mix, coercing to float (#143).
            if cons_type is None:
                return alt_type
            if alt_type is None:
                return cons_type
            if cons_type is NULL:
                return alt_type
            if alt_type is NULL:
                return cons_type
            if cons_type == alt_type:
                return cons_type
            # A ?: produces ONE value through a phi of one LLVM type, so
            # unlike a binary operator (#143, where an int operand is
            # coerced to float on the spot) the two branches must ALREADY
            # match -- including int vs float. Rejecting a numeric
            # mismatch here with a clear message beats codegen emitting a
            # phi of two different numeric types (invalid IR) or silently
            # truncating one branch; the fix the message names
            # (`.toFloat()` on the int branch) is exactly #143's own
            # explicit conversion.
            hint = ""
            if cons_type in _NUMERIC_TYPES and alt_type in _NUMERIC_TYPES:
                hint = " -- write .toFloat() on the int branch to make both float"
            raise CompileError(
                f"the two branches of a ?: must have the same type, found "
                f"{types_mod.type_name(cons_type)} and {types_mod.type_name(alt_type)}{hint}",
                file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                category="invalid operand type",
            )
        if isinstance(expr, ast.LogicalOp):
            # claude.md #192: && / || require bool operands, checked the
            # same way an if/while condition is (claude.md #17: values
            # like 0/1/-1 must not silently become booleans). The
            # operand types used to be inferred and discarded, so
            # `1 && 2` compiled and printed the raw i8 constant (2
            # happens to be the bool-null sentinel -> "null"), and
            # `i && true` on an int operand reached codegen's `icmp ne
            # i8` on an i64 value -> invalid IR.
            check_condition_bool(infer(expr.left, scope), expr.left)
            check_condition_bool(infer(expr.right, scope), expr.right)
            return types_mod.PrimitiveType("bool")
        if isinstance(expr, ast.BinOp):
            left = infer(expr.left, scope)
            right = infer(expr.right, scope)
            # claude.md #143: superseded claude.md #55's old "int and
            # float never mix directly" rule -- int/float now mix
            # freely in any binary operator (arithmetic, comparison, or
            # equality), the int side implicitly coerced to float, as
            # though int.toFloat() had been written explicitly. Nothing
            # is checked or rejected here for it any more; see
            # codegen.py's own comment on _emit_binop for where the
            # actual sitofp conversion is emitted, and the arithmetic-
            # result-type branch further down for what type this whole
            # expression itself infers as.
            if expr.op in ("==", "!="):
                # claude.md #18 shows == / != between two values of the
                # same type; it never shows (and #2's "no implicit
                # coercion" principle rules out) comparing genuinely
                # different types like int and text. Without this check
                # something like `5 == 'x'` passed straight through to
                # codegen, which has no general fallback for a type
                # mismatch here (only the text-specific branch of
                # _emit_binop even looks at the operand types) and
                # produced invalid LLVM IR instead of a clear compile
                # error. NULL is valid against everything (#25).
                #
                # claude.md #109 removed the blob/text exception that
                # used to live here. It was justified by the two sharing
                # a runtime representation -- both `ptr` to bytes,
                # compared by festina_str_eq either way -- which stopped
                # being true when a blob became a handle. Comparing one
                # to a text now would compare a struct's address against
                # a string's contents. `f.toText() == t` is the
                # comparison that was actually meant, and it says so.
                compatible = (
                    left is None or right is None
                    or left is NULL or right is NULL
                    or left == right
                    # claude.md #143: int/float mix freely now, == / !=
                    # included -- the int side is coerced to float, the
                    # same as every other binary operator.
                    or (left in _NUMERIC_TYPES and right in _NUMERIC_TYPES)
                )
                # claude.md #216: `worker:thread` is never `null` any
                # more (claude.md #208's own "null when sent by main"
                # design is gone -- see ThreadType's own doc comment and
                # the `.main` field access above), so comparing one
                # against `null` is dead code now, not a live "is this
                # from main" check -- rejected with a pointer at `.main`
                # rather than silently compiling to an always-false
                # comparison. `thread == thread` (two genuine values)
                # stays unsupported too, unchanged from claude.md #208:
                # codegen's non-null equality path would hit the
                # identical pre-existing "icmp eq i64 <a ptr>" invalid-
                # IR bug struct == struct already has (confirmed
                # directly, unrelated to this feature, out of scope to
                # fix here).
                if isinstance(left, types_mod.ThreadType) or isinstance(right, types_mod.ThreadType):
                    if left is NULL or right is NULL:
                        raise CompileError(
                            f"'{expr.op}' against null is not supported for a thread "
                            f"value any more -- a 'worker:thread' parameter is never "
                            f"null, use '.main' to check whether it was sent by the "
                            f"main program",
                            file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                            category="invalid operand type",
                        )
                    if isinstance(left, types_mod.ThreadType) and isinstance(right, types_mod.ThreadType):
                        raise CompileError(
                            f"'{expr.op}' between two thread values is not supported",
                            file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                            category="invalid operand type",
                        )
                if not compatible:
                    raise CompileError(
                        f"cannot compare {types_mod.type_name(left)} and "
                        f"{types_mod.type_name(right)} with {expr.op}",
                        file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                        category="invalid operand type",
                    )
                return types_mod.PrimitiveType("bool")
            if expr.op in ("<", ">", "<=", ">="):
                # claude.md never defines ordering for anything but
                # numbers, and codegen only ever implements int/float
                # comparisons for these operators (text raises its own
                # clear CodegenError already, but nothing stopped e.g.
                # two `bool` operands from silently reaching codegen's
                # numeric icmp path and being "ordered" in a way
                # claude.md never sanctions).
                numeric_or_null = (_INT, _FLOAT, NULL)
                left_ok = left is None or left in numeric_or_null
                right_ok = right is None or right in numeric_or_null
                if not (left_ok and right_ok):
                    raise CompileError(
                        f"'{expr.op}' requires int or float operands, found "
                        f"{types_mod.type_name(left)} and {types_mod.type_name(right)}",
                        file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                        category="invalid operand type",
                    )
                return types_mod.PrimitiveType("bool")
            if expr.op == "/":
                # claude.md #143: division always returns float,
                # unconditionally -- the one arithmetic operator that's
                # float-returning even when BOTH operands are int
                # (every other arithmetic operator here -- +, -, *, %
                # -- only promotes to float when the two operands
                # actually differ, via the generic check just below).
                return types_mod.PrimitiveType("float")
            if left == types_mod.PrimitiveType("float") or right == types_mod.PrimitiveType("float"):
                return types_mod.PrimitiveType("float")
            return left if left is not None else right
        if isinstance(expr, ast.UnaryOp):
            operand = infer(expr.operand, scope)
            if expr.op == "!":
                return types_mod.PrimitiveType("bool")
            return operand
        if isinstance(expr, ast.TypeofExpr):
            # claude.md #176: always text, regardless of the operand's
            # own type -- infer() runs purely to type-check the operand
            # (an unknown identifier, say, should still be caught),
            # nothing about the RESULT depends on what it resolves to.
            infer(expr.operand, scope)
            return _TEXT
        return None

    def _infer_member(expr, scope):
        # claude.md #93: Math.PI / Math.E -- checked here for the same
        # reason `environment` is below: Math is a namespace, not a
        # value, so there is nothing to infer the type of.
        if (isinstance(expr.obj, ast.Identifier) and expr.obj.name == "Math"
                and not expr.computed):
            if expr.prop in MATH_CONSTANTS:
                return _FLOAT
            if expr.prop in MATH_FUNCTIONS:
                raise CompileError(
                    f"Math.{expr.prop} is a function -- call it, "
                    f"e.g. `Math.{expr.prop}(...)`",
                    file=filename, line=getattr(expr, "line", 0),
                    column=getattr(expr, "column", 0),
                    category="invalid field access",
                )
            raise CompileError(
                f"Math has no member '{expr.prop}'",
                file=filename, line=getattr(expr, "line", 0),
                column=getattr(expr, "column", 0),
                category="invalid field access",
            )
        # claude.md #71: environment.NAME / environment[keyExpr] --
        # checked structurally (an Identifier literally named
        # "environment"), before the generic infer(expr.obj, scope)
        # below, since environment isn't a real value to infer the type
        # of (see _ENVIRONMENT_NAME's own comment) -- it's a namespace,
        # not a variable, so its "object" is never actually evaluated.
        if isinstance(expr.obj, ast.Identifier) and expr.obj.name == _ENVIRONMENT_NAME:
            if expr.computed:
                key_type = infer(expr.prop, scope) if isinstance(expr.prop, ast.Node) else None
                if key_type is not None and key_type is not NULL and key_type != _TEXT:
                    raise CompileError(
                        f"{_ENVIRONMENT_NAME}[...] key must be text, found {types_mod.type_name(key_type)}",
                        file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                        category="invalid operand type",
                    )
            return _TEXT
        obj_type = infer(expr.obj, scope)
        if expr.computed:
            idx_type = infer(expr.prop, scope) if isinstance(expr.prop, ast.Node) else None
            if isinstance(obj_type, types_mod.MapType):
                # claude.md #72: npcHealths['npc1'] / npcHealths[key] --
                # keys are always text (never int, unlike array
                # indexing just below), and a missing key is not a
                # compile-time concern at all (it's claude.md #72's own
                # "returns null" runtime behavior -- see codegen.py's
                # _emit_map_get), so there's nothing else to check here
                # beyond the key's own type.
                if idx_type is not None and idx_type is not NULL and idx_type != _TEXT:
                    raise CompileError(
                        f"map key must be text, found {types_mod.type_name(idx_type)}",
                        file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                        category="invalid operand type",
                    )
                return obj_type.value
            if obj_type == _TEXT:
                # claude.md #150: s[i] -> a single UTF-8 code point, the
                # same unit split('') already uses -- or null (not a
                # bounds-check failure the way arr[T] indexing is,
                # api.md's own "Indexing is not bounds-checked" section
                # already scopes that unchecked-ness to arr[T] alone)
                # for i<0 or i past the last code point, the same
                # "answer null, don't crash" choice a missing map[T] key
                # already gets just above.
                if idx_type is not None and idx_type is not NULL and idx_type != _INT:
                    raise CompileError(
                        f"text index must be int, found {types_mod.type_name(idx_type)}",
                        file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                        category="invalid operand type",
                    )
                return _TEXT
            if not isinstance(obj_type, types_mod.ArrayType):
                raise CompileError(
                    f"cannot index into {types_mod.type_name(obj_type)}",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid field access",
                )
            # claude.md #65: "The index expression must resolve to
            # int" -- idx_type used to be inferred and then simply
            # discarded, never actually checked against anything. A
            # float or text index (e.g. `a[1.5]`, `a['x']`) passed
            # semantic analysis and reached codegen, which emits a
            # `getelementptr` using the index value's own LLVM
            # representation regardless of its Festina type --
            # producing invalid LLVM IR (a raw double or a `ptr` used
            # where an `i64` GEP index is required) and a confusing
            # internal "LLVM object emission failed" error instead of a
            # clear compile-time one. Covers both a read (`a[i]`) and a
            # write target (`a[i] = v`), since Assign's target_type
            # also goes through this same function.
            if idx_type is not None and idx_type is not NULL and idx_type != _INT:
                raise CompileError(
                    f"array index must be int, found {types_mod.type_name(idx_type)}",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid operand type",
                )
            return obj_type.element
        if isinstance(obj_type, types_mod.StructType):
            fields = structs.get(obj_type.name, {})
            if expr.prop not in fields:
                raise CompileError(
                    f"struct '{obj_type.name}' has no field '{expr.prop}'",
                    file=filename, line=expr.line, column=expr.column,
                    category="invalid field access",
                )
            return fields[expr.prop]
        if isinstance(obj_type, types_mod.EnumType):
            # claude.md #176: field access only exists for a pure-
            # struct enum (every member a struct) -- a mixed enum has
            # no fields to speak of (what would `.radius` even mean on
            # a Json value that might currently hold an int?). Resolves
            # to a SINGLE owning member, guaranteed unique by
            # analyze_enum's own field-collision check at declaration
            # time, so there's no ambiguity to resolve here, only a
            # lookup.
            info = enums.get(obj_type.name)
            if info is None or not info.is_pure_struct:
                raise CompileError(
                    f"cannot access field '{expr.prop}' on '{obj_type.name}' -- field access "
                    f"only works on an enum whose members are all structs",
                    file=filename, line=expr.line, column=expr.column,
                    category="invalid field access",
                )
            owner = next((m for m in info.members if expr.prop in structs.get(m.name, {})), None)
            if owner is None:
                raise CompileError(
                    f"enum '{obj_type.name}' has no member with field '{expr.prop}'",
                    file=filename, line=expr.line, column=expr.column,
                    category="invalid field access",
                )
            return structs[owner.name][expr.prop]
        if isinstance(obj_type, types_mod.TableType):
            # claude.md #34: a query against a declared table produces
            # arr[TableType(name)] -- field access on a row (e.g.
            # `people[0].name`) resolves against that table's declared
            # columns, same as a struct field except `tables` stores raw
            # type-expr strings rather than already-resolved Type objects
            # (see analyze_table above), so each lookup resolves on demand.
            columns = tables.get(obj_type.name, {})
            if expr.prop not in columns:
                # claude.md #188 (uraikus/festina#76 item 5): row.rowid
                # -- not a declared column at all (no CREATE TABLE
                # side effect, no schema-sync ALTER TABLE consideration
                # -- see codegen.py's own _table_arrays_for_select vs.
                # _table_arrays split for why keeping it OUT of the
                # `columns` dict entirely matters), but always
                # readable: SQLite gives every ordinary rowid table one
                # for free, and this just exposes it. Populated only
                # when the query's own SQL actually selected a result
                # column literally named `rowid` -- e.g. `SELECT
                # rowid, * FROM t` -- otherwise reads as int's own null,
                # the same "the query never mentioned this" signal
                # `.undefined()` already gives an ordinary column.
                if expr.prop == "rowid":
                    return _INT
                raise CompileError(
                    f"table '{obj_type.name}' has no field '{expr.prop}'",
                    file=filename, line=expr.line, column=expr.column,
                    category="invalid field access",
                )
            return resolve(columns[expr.prop], expr)
        if isinstance(obj_type, types_mod.ArrayType):
            # claude.md #63: the only field an array has is the built-in
            # read-only `.length` (int) -- anything else is an error, not
            # a permissive fallthrough, matching how struct/table field
            # access is handled just above.
            if expr.prop != "length":
                raise CompileError(
                    f"array has no field '{expr.prop}' (did you mean '.length'?)",
                    file=filename, line=expr.line, column=expr.column,
                    category="invalid field access",
                )
            return types_mod.PrimitiveType("int")
        if isinstance(obj_type, types_mod.ImageType):
            # claude.md #92: img has exactly two readable properties.
            # Strict, like ArrayType's own `.length` handling just above
            # -- this branch used to be a permissive `return None`, back
            # when claude.md #37 defined nothing at all on img, which
            # silently accepted every typo.
            if expr.prop in ("width", "height"):
                return types_mod.PrimitiveType("int")
            if expr.prop in ("clip", "resize", "save", "saveCopy"):
                raise CompileError(
                    f"'{expr.prop}' is a method on img -- call it, "
                    f"e.g. `sheet.{expr.prop}(...)`",
                    file=filename, line=expr.line, column=expr.column,
                    category="invalid field access",
                )
            raise CompileError(
                f"img has no field '{expr.prop}' "
                f"(img has .width, .height, .clip() and .resize())",
                file=filename, line=expr.line, column=expr.column,
                category="invalid field access",
            )
        if isinstance(obj_type, types_mod.UrlType):
            # claude.md #162: every field is read-only (see the
            # assignment-rejection check right alongside http's own,
            # earlier in this module) -- a url is built once, by
            # parseURL(), never mutated afterward.
            if expr.prop in ("hostname", "password", "pathname", "protocol", "username", "hash"):
                return _TEXT
            if expr.prop == "port":
                return _INT
            if expr.prop == "searchParams":
                return types_mod.MapType(_TEXT)
            raise CompileError(
                f"url has no field '{expr.prop}' (url has .hash, .hostname, "
                f".password, .pathname, .port, .protocol, .searchParams, and .username)",
                file=filename, line=expr.line, column=expr.column,
                category="invalid field access",
            )
        if isinstance(obj_type, types_mod.HttpType):
            # claude.md #162/#163: five read-only fields (url/method/
            # code/headers/callback), the rest are methods (see
            # _HTTP_METHODS/_infer_call's own `send` branch) -- same
            # strict shape as ImageType's own field/method split above,
            # for the same reason. `url`/`code` replace the old
            # `port`/`path` pair (claude.md #151) -- a live inbound
            # request reconstructs `url` from its own scheme/Host
            # header/path (see festina_runtime_http.c's own comment),
            # and `code` is `null` on an inbound request or a freshly-
            # constructed outbound one, set once a response actually
            # exists (or a background send() fails -- claude.md #163).
            if expr.prop in ("url", "method"):
                return _TEXT
            if expr.prop == "code":
                return _INT
            if expr.prop == "headers":
                return types_mod.MapType(_TEXT)
            if expr.prop == "callback":
                return _HTTP_CALLBACK_TYPE
            if expr.prop in _HTTP_METHODS or expr.prop == "send":
                raise CompileError(
                    f"'{expr.prop}' is a method on http -- call it, "
                    f"e.g. `req.{expr.prop}(...)`",
                    file=filename, line=expr.line, column=expr.column,
                    category="invalid field access",
                )
            raise CompileError(
                f"http has no field '{expr.prop}' (http has .url, "
                f".method, .code, .headers, .callback, and the methods listed in api.md)",
                file=filename, line=expr.line, column=expr.column,
                category="invalid field access",
            )
        if isinstance(obj_type, types_mod.SocketType):
            # claude.md #151: `state` is the one real field -- a plain
            # mutable map[text] scratchpad for per-connection data
            # (`s.state['user'] = 'ada'`), which needs no special-
            # casing here at all: MapType's own computed-member
            # read/assignment machinery already handles a map reached
            # through an arbitrary expression, socket included.
            if expr.prop == "state":
                return types_mod.MapType(_TEXT)
            if expr.prop in _SOCKET_METHODS or expr.prop == "send":
                raise CompileError(
                    f"'{expr.prop}' is a method on socket -- call it, "
                    f"e.g. `s.{expr.prop}(...)`",
                    file=filename, line=expr.line, column=expr.column,
                    category="invalid field access",
                )
            raise CompileError(
                f"socket has no field '{expr.prop}' (socket has .state, "
                f".send() and .close())",
                file=filename, line=expr.line, column=expr.column,
                category="invalid field access",
            )
        if isinstance(obj_type, types_mod.ThreadType):
            # claude.md #216: `worker`/`t` (a `thread`-typed value,
            # always the generic `ThreadType(None)` variant -- the only
            # one ordinary code ever HOLDS a value of, per ThreadType's
            # own doc comment) is never `null` any more -- when main is
            # the sender, `worker` is a real, singleton handle
            # (festina_thread_get_main_handle at the runtime level) with
            # `.main` reading true. This is the ONE field on `thread`;
            # `.postMessage`/`.reply`/`.callback` stay methods (Call-on-
            # Member, handled in _infer_call, matching every other
            # method-only type here -- ImageType/HttpType/SocketType
            # above all split fields vs. methods the identical way).
            if expr.prop == "main":
                return _BOOL
            # claude.md #218: the old wording here suggested
            # `.postMessage()` as the fix, which is wrong for exactly
            # the case that reaches this branch -- `.postMessage()` is
            # not a method on a `thread` VALUE at all, it's a method on
            # a declared thread's own NAME (a different receiver
            # entirely, dispatched by name). Someone writing
            # `w.postMessage(x)` inside a handler was being pointed
            # straight back at the thing that just failed.
            raise CompileError(
                f"thread has no field '{expr.prop}' -- a thread value has "
                f"'.main' and '.reply(x)' (reply to the message being "
                f"handled); to send a NEW message, name the target thread, "
                f"e.g. 'someThread.postMessage(x)'",
                file=filename, line=expr.line, column=expr.column,
                category="invalid field access",
            )
        # claude.md #38: aud's play()/stop()/isPlaying() are only
        # recognized as Call-on-Member patterns (see _infer_call) --
        # this branch is for a bare `music.play` reference with no
        # call, which was never a valid thing to write, so it's a hard
        # error like everything else below, not a silent fallthrough
        # (AudioType used to share ImageType's permissive branch above,
        # back when neither had any real methods modeled).
        raise CompileError(
            f"cannot access field '{expr.prop}' on {types_mod.type_name(obj_type)}",
            file=filename, line=expr.line, column=expr.column,
            category="invalid field access",
        )

    def _is_postmessage_callee(c):
        """claude.md #217: True for a bare `postMessage` Identifier
        callee, or a `NAME.postMessage`/`pool[i].postMessage` Member
        callee -- the two shapes `.callback()`'s own combined-pattern
        recognition (below) needs to tell apart from an unrelated
        `.callback()` receiver (blob/img/aud's own file-loading one,
        claude.md #165/#171, whose receiver can ALSO be an arbitrary
        Call -- `getPath().callback(fn)` -- so checking `isinstance(...,
        ast.Call)` alone isn't specific enough)."""
        if isinstance(c, ast.Identifier) and c.name == "postMessage":
            return True
        return isinstance(c, ast.Member) and not c.computed and c.prop == "postMessage"

    def _postmessage_target_reply_type(call_node):
        """claude.md #217: given a Call node already known to be a
        postMessage send (bare or named/pool -- see
        _is_postmessage_callee), returns (reply_type, target_desc) --
        `reply_type` is None if that target never replies (or hasn't
        been analyzed yet, which never happens in practice: a named
        target must be declared, hence fully analyzed, before any call
        site referencing it, per this whole file's "declared before
        referenced" rule -- see analyze_thread's own doc comment).
        `target_desc` is a short name for error messages ('the main
        program', or 'thread_name'). Returns (None, None) if this
        somehow isn't a postMessage call at all (defensive; every
        caller already checked _is_postmessage_callee first)."""
        callee = call_node.callee
        if isinstance(callee, ast.Identifier) and callee.name == "postMessage":
            return (_main_reply_type[0], "the main program")
        if isinstance(callee, ast.Member) and not callee.computed and callee.prop == "postMessage":
            receiver = callee.obj
            if (isinstance(receiver, ast.Member) and receiver.computed
                    and isinstance(receiver.obj, ast.Identifier) and receiver.obj.name in threads):
                receiver = receiver.obj  # claude.md #209: pool[i] -> the pool's own name
            if isinstance(receiver, ast.Identifier) and receiver.name in threads:
                return (threads[receiver.name].reply_type, receiver.name)
        return (None, None)

    def _infer_call(expr, scope):
        callee = expr.callee
        if isinstance(callee, ast.Identifier):
            name = callee.name
            # claude.md #195: everything below is only relevant while
            # analyzing a thread's OWN handler bodies (_current_thread[0]
            # is a _ThreadInfo then, None everywhere else in the
            # program) -- checked first, once, ahead of every other
            # builtin-name branch below, so none of them need their own
            # awareness of threads at all.
            if _current_thread[0] is not None:
                if name == "postMessage":
                    # claude.md #195/#208: the bare, context-implicit
                    # form -- "send FROM me, TO main" -- checked against
                    # the top-level `on message(worker:thread, msg:T)`
                    # handler's own declared type, the SAME way the
                    # named `NAME.postMessage(x)` form (ThreadType
                    # Member-call dispatch, above) checks against a
                    # specific thread's own declared inbound_type.
                    # Ordinary "declared before referenced" program-
                    # order rules apply -- a bare postMessage(x) written
                    # textually before the top-level `on message`
                    # handler sees `_main_message_type[0]` still None.
                    if len(expr.args) != 1:
                        raise CompileError(
                            f"postMessage() expects exactly 1 argument, got "
                            f"{len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    if _main_message_type[0] is None:
                        raise CompileError(
                            "postMessage() sends to the main program, but it declares "
                            "no top-level 'on message(worker:thread, msg:T)' handler "
                            "(yet) -- nothing would ever receive this",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid declaration",
                        )
                    arg_type = infer(expr.args[0], scope)
                    check_assignable(_main_message_type[0], arg_type, expr.args[0],
                                      what="postMessage() argument")
                    return None
                if name in _SQLITE_BUILTINS and _current_thread[0].database_url is None:
                    # claude.md #199 Phase 5: unlike every OTHER
                    # disallowed builtin (a flat, unconditional "never
                    # from a thread body"), sqlite() is allowed for a
                    # thread that declared its own private database --
                    # its own separate sqlite3* handle, never crossing
                    # threads, is exactly what makes this safe (see
                    # claude.md #195's own design). A thread that
                    # didn't gets this error instead of falling through
                    # to ordinary sqlite() handling below, which would
                    # otherwise silently reach for the shared
                    # @__festina_db main uses.
                    raise CompileError(
                        f"'{name}()' cannot be called from inside thread "
                        f"'{_current_thread[0].node.name}' -- it hasn't declared "
                        f"its own database (a thread's first statement may be "
                        f"DatabaseURL = '<path>', giving it a private sqlite "
                        f"handle no other thread or the main program shares)",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                if (name in ("openPort", "closePort", "openSecurePort")
                        and not _current_thread[0].has_http_handler):
                    # claude.md #212: mirrors the sqlite gate just
                    # above exactly -- legal only for a thread that has
                    # ALREADY declared at least one HTTP-shaped handler
                    # (on request/on upgrade/on socketMessage/on
                    # socketClose), since that declaration is what
                    # gives this thread's own worker loop the bounded-
                    # poll shape that actually services a listener at
                    # all (see festina_thread_set_http_context) --
                    # without one, an accepted connection would simply
                    # sit forever, nothing ever polling for it. A
                    # thread that HAS declared one falls through to
                    # ordinary openPort()/closePort()/openSecurePort()
                    # handling below, identical to main's own.
                    raise CompileError(
                        f"'{name}()' cannot be called from inside thread "
                        f"'{_current_thread[0].node.name}' -- it hasn't declared "
                        f"an HTTP-shaped handler yet (on request(req:http)/"
                        f"on upgrade(s:socket)/on socketMessage(s:socket, msg:blob)/"
                        f"on socketClose(s:socket)), so nothing would ever service "
                        f"a connection accepted here",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                if name in _THREAD_DISALLOWED_BUILTINS:
                    raise CompileError(
                        f"'{name}()' cannot be called from inside a thread body -- "
                        f"it touches state shared with the main program (see "
                        f"claude.md #195's own list of what a thread body may call)",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                _called_sym = scope.lookup(name)
                if _called_sym is not None and _called_sym.kind == "function":
                    raise CompileError(
                        f"'{name}()' cannot be called from inside a thread body -- "
                        f"functions declared outside a thread aren't isolated the "
                        f"same way it is (see claude.md #195); declare '{name}' as a "
                        f"func directly inside this thread's own body instead "
                        f"(claude.md #210), if it only needs to be called from here",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
            if name in ("setTimeout", "setInterval"):
                # claude.md #69 -- see BUILTIN_FUNCTIONS's comment
                # above and festina_runtime.h's doc comment on
                # festina_set_timeout/_interval. The callback has to be
                # the bare name of an already-declared function (Festina
                # has no first-class functions/closures -- see
                # codegen.py's "functions are not first-class values yet"
                # CodegenError), checked structurally here rather than
                # through infer(), which would otherwise just return that
                # function's own return type like any other identifier
                # reference -- it's not being *used* as a value here, its
                # *declaration* is what's being validated.
                if len(expr.args) != 2:
                    raise CompileError(
                        f"{name}() expects 2 arguments (a callback function and a "
                        f"delay in milliseconds), got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                callback_expr = expr.args[0]
                if not isinstance(callback_expr, ast.Identifier):
                    raise CompileError(
                        f"{name}()'s first argument must be the name of a declared "
                        f"function, not an arbitrary expression",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                callback_sym = scope.lookup(callback_expr.name)
                if callback_sym is None or callback_sym.kind != "function":
                    raise CompileError(
                        f"'{callback_expr.name}' is not a declared function",
                        file=filename, line=callback_expr.line, column=callback_expr.column,
                        category="unknown function",
                    )
                if callback_sym.type is not None or callback_sym.node.params:
                    raise CompileError(
                        f"the callback passed to {name}() must take no parameters and "
                        f"return nothing -- declare it as "
                        f"'void func {callback_expr.name}() {{ ... }}'",
                        file=filename, line=callback_expr.line, column=callback_expr.column,
                        category="invalid function argument type",
                    )
                delay_type = infer(expr.args[1], scope)
                if delay_type is not None and delay_type is not NULL and delay_type != _INT:
                    raise CompileError(
                        f"{name}()'s delay argument must be an int (milliseconds), "
                        f"found {types_mod.type_name(delay_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return _INT  # a timer id, usable with clearTimeout()/clearInterval()
            if name in ("clearTimeout", "clearInterval"):
                if len(expr.args) != 1:
                    raise CompileError(
                        f"{name}() expects exactly 1 argument (a timer id), got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                id_type = infer(expr.args[0], scope)
                if id_type is not None and id_type is not NULL and id_type != _INT:
                    raise CompileError(
                        f"{name}()'s argument must be an int (a timer id), "
                        f"found {types_mod.type_name(id_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return None
            # claude.md #135: saveCanvas(path) -> bool (unchanged) or
            # saveCanvas() -> img (new) -- the return TYPE itself
            # depends on which form is used, which the generic
            # _BUILTIN_SIGNATURE_ALTERNATES mechanism just below can't
            # express (it picks an argument signature by arity, but
            # answers one fixed return type for the name regardless of
            # which alternate matched), so this gets its own branch
            # instead, the same way setTimeout/clearTimeout just above
            # do for their own reason.
            if name == "saveCanvas":
                if len(expr.args) == 0:
                    return _IMAGE
                if len(expr.args) != 1:
                    raise CompileError(
                        f"saveCanvas() expects 0 or 1 argument(s), got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                arg_type = infer(expr.args[0], scope)
                if arg_type is not None and arg_type is not NULL and arg_type != _TEXT:
                    raise CompileError(
                        f"saveCanvas()'s argument expects text, found "
                        f"{types_mod.type_name(arg_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return _BOOL
            # claude.md #150: exec(args) -> int -- spawns args[0] with
            # args[1:] as its own argv, blocking until it exits, and
            # answers its real exit code. claude.md #177's own
            # non-blocking exec(args, callback) form was removed in
            # claude.md #221 (its callback always ran on main's own OS
            # thread regardless of which thread dispatched it -- a real
            # cross-thread-isolation hazard for a language whose whole
            # thread story is "no shared mutable state to race on" --
            # so only the always-safe blocking form remains).
            if name == "exec":
                if len(expr.args) != 1:
                    raise CompileError(
                        f"exec() expects 1 argument (args), got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                args_type = infer(expr.args[0], scope)
                if (args_type is not None and args_type is not NULL
                        and args_type != types_mod.ArrayType(_TEXT)):
                    raise CompileError(
                        f"exec()'s first argument expects arr[text], found "
                        f"{types_mod.type_name(args_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return _INT
            if name in ("fail", "troubleshoot"):
                # claude.md #158: fail(message) is unchanged (1 argument,
                # any type -- coerced to text at codegen, same as
                # log()'s own single argument always has been). Both
                # fail(message, fields) and troubleshoot(event, fields)
                # add/require a second argument that must be map[text]
                # specifically -- reusing the exact JSON rendering
                # map[T]'s own .toText() already has (codegen's
                # _to_text) rather than inventing a second one, at the
                # cost of restricting `fields` to string-valued tags
                # rather than accepting any container shape.
                min_args = 1 if name == "fail" else 2
                if len(expr.args) < min_args or len(expr.args) > 2:
                    shape = "1 or 2" if name == "fail" else "2"
                    raise CompileError(
                        f"{name}() expects {shape} argument(s), got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                infer(expr.args[0], scope)  # message/event: any type, coerced to text
                if len(expr.args) == 2:
                    fields_expr = expr.args[1]
                    if isinstance(fields_expr, ast.MapLit):
                        # claude.md #156's own identical bypass, same
                        # reason: MapLit's generic infer() always
                        # returns a plain, non-context-aware map[T] (or,
                        # for an empty `{}`, no usable type at all --
                        # infer() has nothing to generalize a value type
                        # FROM), so `troubleshoot('x', {})` or
                        # `troubleshoot('x', {'a': 'b'})` would always
                        # fail the check below despite being exactly
                        # right. Validated directly against map[text]
                        # instead of through generic inference.
                        for key_expr, val_expr in fields_expr.entries:
                            key_type = infer(key_expr, scope)
                            if key_type is not None and key_type is not NULL and key_type != _TEXT:
                                raise CompileError(
                                    f"{name}()'s fields map key must be text, found "
                                    f"{types_mod.type_name(key_type)}",
                                    file=filename, line=getattr(key_expr, "line", 0),
                                    column=getattr(key_expr, "column", 0),
                                    category="invalid operand type",
                                )
                            val_type = infer(val_expr, scope)
                            if (val_type is not None and val_type is not NULL
                                    and val_type != _TEXT):
                                raise CompileError(
                                    f"{name}()'s fields map value expects text, found "
                                    f"{types_mod.type_name(val_type)}",
                                    file=filename, line=getattr(val_expr, "line", 0),
                                    column=getattr(val_expr, "column", 0),
                                    category="invalid operand type",
                                )
                    else:
                        fields_type = infer(fields_expr, scope)
                        if not (isinstance(fields_type, types_mod.MapType)
                                and fields_type.value == _TEXT):
                            raise CompileError(
                                f"{name}()'s fields argument expects map[text], found "
                                f"{types_mod.type_name(fields_type) if fields_type is not None else 'null'}",
                                file=filename, line=callee.line, column=callee.column,
                                category="invalid function argument type",
                            )
                return None
            if name in BUILTIN_FUNCTIONS:
                sig = _BUILTIN_SIGNATURES.get(name)
                alternates = _BUILTIN_SIGNATURE_ALTERNATES.get(name)
                if alternates is not None:
                    sig = next((a for a in alternates if len(a) == len(expr.args)), None)
                    if sig is None:
                        shapes = " or ".join(str(len(a)) for a in alternates)
                        raise CompileError(
                            f"{name}() expects {shapes} argument(s), "
                            f"got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                if sig is None:
                    for a in expr.args:
                        if (name in _SQLITE_BUILTINS and isinstance(a, ast.ArrayLit)):
                            # claude.md #33: sqlite()'s parameter list is
                            # passed as an array literal, but -- unlike
                            # every real arr[T] value in the language --
                            # it's explicitly allowed (and, per #33's own
                            # worked example, [1, 'Patrick'], expected) to
                            # mix types: one bound value per '?'
                            # placeholder, each independently int/float/
                            # text/bool/null. Infer each element on its
                            # own (still catching a genuinely broken
                            # sub-expression) instead of delegating to the
                            # ArrayLit branch above, whose same-element-
                            # type check exists for real arr[T] values and
                            # would otherwise wrongly reject this one,
                            # spec-mandated exception.
                            for e in a.elements:
                                infer(e, scope)
                        else:
                            infer(a, scope)
                else:
                    if len(expr.args) != len(sig):
                        raise CompileError(
                            f"{name}() expects {len(sig)} argument(s), got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    for i, (arg_expr, expected) in enumerate(zip(expr.args, sig)):
                        arg_type = infer(arg_expr, scope)
                        if arg_type is not None and arg_type is not NULL and arg_type != expected:
                            raise CompileError(
                                f"{name}()'s argument {i + 1} expects "
                                f"{types_mod.type_name(expected)}, found {types_mod.type_name(arg_type)}",
                                file=filename, line=callee.line, column=callee.column,
                                category="invalid function argument type",
                            )
                return _BUILTIN_RETURN_TYPES.get(name)
            sym = scope.lookup(name)
            if (sym is not None and sym.kind != "function"
                    and isinstance(sym.type, types_mod.FuncType)):
                # claude.md #141: an INDIRECT call, through a func[...]:...
                # -typed variable/parameter/constant/field/element rather
                # than a plain declared function's own name -- checked
                # before the "not a function at all" rejection just
                # below, since a func-typed local is exactly as callable
                # as a real function, just via a different Symbol.kind.
                # A local shadowing a real global function of the same
                # name (Scope.define permits it -- see its own comment)
                # is handled automatically here too: scope.lookup always
                # resolves to the innermost binding, so the shadowing
                # local's own FuncType signature is what a call is
                # checked against, never the shadowed global function's.
                fn_type = sym.type
                if len(expr.args) != len(fn_type.param_types):
                    raise CompileError(
                        f"'{name}' expects {len(fn_type.param_types)} argument(s), "
                        f"got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                for i, (arg_expr, expected) in enumerate(zip(expr.args, fn_type.param_types)):
                    arg_type = infer(arg_expr, scope)
                    check_assignable(expected, arg_type, callee,
                                      what=f"argument {i + 1} of '{name}'")
                return fn_type.return_type
            # claude.md #210: "thread_function" (a thread-private
            # helper) is accepted here on equal footing with an
            # ordinary "function" -- everything below reads `sym.node`
            # generically (a plain FuncDecl either way), so no further
            # branching is needed. This widening is safe EVERYWHERE
            # this function runs, not just inside a thread body:
            # `scope.lookup(name)` is what actually enforces privacy
            # (a thread-private func's own name was only ever defined
            # in that ONE thread's own thread_functions_scope, never in
            # global_scope/functions_scope), so a call site outside
            # that thread's own body can never even find the symbol to
            # reach this check in the first place.
            if sym is None or sym.kind not in ("function", "thread_function"):
                # claude.md #109: a name this language used to have gets
                # told what replaced it, not merely that it is unknown.
                # A user-declared function of the same name still wins,
                # since scope.lookup ran first -- nothing here reserves
                # the old names, it only explains them.
                raise CompileError(
                    _REMOVED_BUILTINS.get(name, f"unknown function '{name}'"),
                    file=filename, line=callee.line, column=callee.column,
                    category="unknown function",
                )
            func_decl = sym.node
            if len(expr.args) != len(func_decl.params):
                raise CompileError(
                    f"function '{name}' expects {len(func_decl.params)} argument(s), "
                    f"got {len(expr.args)}",
                    file=filename, line=callee.line, column=callee.column,
                    category="invalid function argument type",
                )
            for arg_expr, param in zip(expr.args, func_decl.params):
                arg_type = infer(arg_expr, scope)
                # claude.md #202: `T?` -- missing this wrap here made
                # every user-defined function with a manually-managed
                # parameter permanently uncallable (its own declared
                # `Circle?` parameter was checked here as plain
                # `Circle`, rejecting the one argument type that could
                # ever actually match it -- caught by a real end-to-end
                # compile, not a unit test alone).
                param_type = apply_manually_managed(resolve(param.type_expr, callee), param.manually_managed)
                check_assignable(param_type, arg_type, callee,
                                  what=f"argument '{param.name}' of '{name}'")
            return sym.type
        if (isinstance(callee, ast.Member) and not callee.computed
                and callee.prop == "reply" and infer(callee.obj, scope) == types_mod.ThreadType(None)):
            # claude.md #217: `t.reply(response)` -- legal on ANY
            # expression of the GENERIC thread type (there is no other
            # way to obtain one at all, per ThreadType's own doc
            # comment -- it only ever arrives via an `on message`
            # parameter, so gating on TYPE alone is exactly as precise
            # as gating on "is this literally the `worker` parameter"
            # would be, with none of the extra plumbing that would
            # need). Which "receiver context" this reply BELONGS to
            # (main's own top-level `on message`, or a specific
            # thread's own) is read off `_current_thread[0]` -- the
            # SAME switch bare `postMessage(x)` already uses to choose
            # between `_main_message_type[0]` and a thread's own
            # `inbound_type`. First call fixes reply_type; every later
            # one in the same context is check_assignable'd against it
            # (enum-coercion included). Does NOT trigger `on message`
            # on the receiving end -- delivered through an entirely
            # separate runtime path (festina_thread_reply), dispatched
            # straight to whichever `.callback(fn)` is waiting.
            if len(expr.args) != 1:
                raise CompileError(
                    f"reply() expects exactly 1 argument, got {len(expr.args)}",
                    file=filename, line=callee.line, column=callee.column,
                    category="invalid function argument type",
                )
            arg_type = infer(expr.args[0], scope)
            reply_slot = _current_thread[0].reply_type if _current_thread[0] is not None else _main_reply_type[0]
            if reply_slot is None:
                reply_slot = arg_type
            else:
                check_assignable(reply_slot, arg_type, expr.args[0], what="reply() argument")
            if _current_thread[0] is not None:
                _current_thread[0].reply_type = reply_slot
            else:
                _main_reply_type[0] = reply_slot
            return None
        if (isinstance(callee, ast.Member) and not callee.computed and callee.prop == "callback"
                and isinstance(callee.obj, ast.Call) and _is_postmessage_callee(callee.obj.callee)):
            # claude.md #217: `NAME.postMessage(x).callback(fn)` -- the
            # combined pattern, recognized as ONE unit (mirrors the
            # existing `<text>.callback(fn)` file-loading precedent's
            # own "match the AST shape directly" style, claude.md
            # #165/#171) rather than trying to make postMessage's own
            # return type carry a synthetic "pending" marker forward.
            # `infer(callee.obj, scope)` runs postMessage's own EXISTING
            # validation completely unchanged (arity, "declares no
            # handler" check, inbound_type check_assignable) -- reused
            # as-is, not duplicated. postMessage's own branches never
            # look at reply_type at all; enforcing "must chain
            # .callback()" is entirely this branch's and the ExprStmt-
            # level bare-send check's job (see analyze_statement) --
            # together they cover the only two syntactically valid
            # positions a postMessage call can appear in (a bare
            # statement, or wrapped in exactly this pattern).
            infer(callee.obj, scope)
            reply_type, target_desc = _postmessage_target_reply_type(callee.obj)
            if reply_type is None:
                raise CompileError(
                    f"'.callback(...)' requires a target that replies -- "
                    f"{target_desc or 'this target'} has no 't.reply(...)' anywhere "
                    f"in its body, so this callback would never run",
                    file=filename, line=callee.line, column=callee.column,
                    category="invalid declaration",
                )
            if len(expr.args) != 1:
                raise CompileError(
                    f"callback() expects exactly 1 argument (the func to call with "
                    f"the reply), got {len(expr.args)}",
                    file=filename, line=callee.line, column=callee.column,
                    category="invalid function argument type",
                )
            fn_type = infer(expr.args[0], scope)
            if (not isinstance(fn_type, types_mod.FuncType)
                    or len(fn_type.param_types) != 1
                    or fn_type.return_type is not None
                    or fn_type.param_types[0] != reply_type):
                raise CompileError(
                    f"callback() expects func[{types_mod.type_name(reply_type)}]:void "
                    f"to match {target_desc}'s own reply type, found "
                    f"{types_mod.type_name(fn_type)}",
                    file=filename, line=callee.line, column=callee.column,
                    category="invalid function argument type",
                )
            return None
        if isinstance(callee, ast.Member) and not callee.computed:
            # claude.md #195: a thread's own five methods, checked by
            # name against the `threads` dict directly (the same
            # "Math"-namespace shape just below uses -- a bare Identifier
            # receiver checked by NAME, not by inferring its type first)
            # rather than through infer(callee.obj, scope), which would
            # do the identical lookup less directly. Checked first, ahead
            # of everything else in this branch, since ThreadType is its
            # own closed namespace no other check here could ever match
            # anyway.
            # claude.md #209: `pool[i].postMessage(...)` -- `pool[i]`
            # itself is an ordinary computed `Member` (`callee.obj`),
            # not a bare Identifier, needing no grammar of its own (see
            # parse_thread_decl's own comment); recognized here by
            # peeling that one extra layer off before the identical
            # by-name lookup below, so every check underneath (method
            # name, main-only lifecycle methods, postMessage's own
            # inbound-type check) runs completely unchanged for a pool
            # instance -- `thread_name`/`info` name the POOL as a
            # whole (one `_ThreadInfo` shared by every index, since
            # every index runs the identical body), the index itself
            # only ever matters at codegen's own runtime select.
            pool_index_expr = None
            pool_receiver = callee.obj
            if (isinstance(callee.obj, ast.Member) and callee.obj.computed
                    and isinstance(callee.obj.obj, ast.Identifier)
                    and callee.obj.obj.name in threads):
                if threads[callee.obj.obj.name].pool_size is None:
                    raise CompileError(
                        f"'{callee.obj.obj.name}' is an ordinary thread, not a pool -- "
                        f"'{callee.obj.obj.name}[i]' is only valid for a "
                        f"'thread {callee.obj.obj.name}[N] {{ ... }}' pool declaration",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid method receiver",
                    )
                pool_index_expr = callee.obj.prop
                pool_receiver = callee.obj.obj
            if isinstance(pool_receiver, ast.Identifier) and pool_receiver.name in threads:
                thread_name = pool_receiver.name
                info = threads[thread_name]
                if info.pool_size is not None and pool_index_expr is None:
                    raise CompileError(
                        f"thread pool '{thread_name}' must be indexed to call a "
                        f"method -- e.g. '{thread_name}[0].{callee.prop}(...)'",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid method receiver",
                    )
                if pool_index_expr is not None:
                    idx_type = infer(pool_index_expr, scope)
                    if idx_type != _INT:
                        raise CompileError(
                            f"thread pool index must be int, found "
                            f"{types_mod.type_name(idx_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                if callee.prop not in ("postMessage", "kill", "live", "isAlive", "giveRequest",
                                        "drain"):
                    raise CompileError(
                        f"thread '{thread_name}' has no method '{callee.prop}' -- only "
                        f"postMessage/kill/live/isAlive/giveRequest/drain are supported",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid method receiver",
                    )
                # claude.md #208: `kill`/`live`/`isAlive` stay MAIN-
                # program-only -- a thread's own body still has no
                # control over another thread's (or its own) lifecycle.
                # `postMessage` is the one exception now: threads may
                # message each other directly (see the bare, context-
                # implicit form's own comment, above, for "send to
                # main" specifically -- this named form is "send to a
                # SPECIFIC thread," legal from main OR from inside any
                # other thread's own body).
                if callee.prop != "postMessage" and _current_thread[0] is not None:
                    raise CompileError(
                        f"'{thread_name}.{callee.prop}(...)' cannot be called from "
                        f"inside a thread body -- only the main program controls a "
                        f"thread's own lifecycle and hands off live connections "
                        f"(threads may message each other via postMessage, but not "
                        f"kill/live/isAlive/giveRequest/drain each other)",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                if callee.prop == "postMessage":
                    if info.inbound_type is None:
                        raise CompileError(
                            f"thread '{thread_name}' declares no 'on message' handler, "
                            f"so '{thread_name}.postMessage(...)' would never be "
                            f"received by anything",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid declaration",
                        )
                    if len(expr.args) != 1:
                        raise CompileError(
                            f"postMessage() expects exactly 1 argument, got "
                            f"{len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    arg_type = infer(expr.args[0], scope)
                    # claude.md #195: check_assignable, not a raw `!=` --
                    # reuses the SAME "a member type coerces into its
                    # enum" rule (claude.md #176) every other parameter/
                    # assignment position already gets, so
                    # `myWorker.postMessage(circleValue)` against an
                    # `on message(worker:thread, msg:Shape)` (Shape =
                    # Circle, Square) works exactly like passing a
                    # Circle to any other Shape-typed parameter already
                    # does, with no special-casing needed here.
                    check_assignable(info.inbound_type, arg_type, expr.args[0],
                                      what="postMessage() argument")
                    return None
                if callee.prop == "giveRequest":
                    # claude.md #213 (Phase 5): main-only (already
                    # enforced above, the same gate kill/live/isAlive
                    # get), and legal only when the target thread has
                    # declared its own `on request` -- that's the ONE
                    # handler giveRequest ever dispatches, so "declared
                    # SOME http handler" (has_http_handler, Phase 4's
                    # own gate) isn't specific enough here.
                    if "request" not in info.declared_http_handlers:
                        raise CompileError(
                            f"'{thread_name}.giveRequest(...)' requires thread "
                            f"'{thread_name}' to have declared its own "
                            f"'on request(req:http)' handler -- nothing else would "
                            f"ever receive a handed-off request",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid declaration",
                        )
                    if len(expr.args) != 1:
                        raise CompileError(
                            f"giveRequest() expects exactly 1 argument, got "
                            f"{len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    # claude.md #213: reuses T? (claude.md #202/#203)
                    # rather than a new compile-time move-checker (per
                    # explicit follow-up during this feature's design)
                    # -- legal only when the argument's OWN static type
                    # is manually-managed `http?`, i.e. it flowed in
                    # from an `on request(req:http?)` parameter (or
                    # another http? binding derived from one). A
                    # manually-managed value is never auto-retained or
                    # auto-released by design, so handing it away costs
                    # nothing to make safe from codegen's own side --
                    # there is no automatic release racing against the
                    # receiving thread's own use, because there was
                    # never any automatic release to begin with. Giving
                    # away an ordinary (auto-managed) `http` -- one NOT
                    # declared `?` -- is rejected outright: main's own
                    # end-of-scope cleanup would still release it right
                    # out from under the thread this just handed it to.
                    arg_type = infer(expr.args[0], scope)
                    if (not isinstance(arg_type, types_mod.HttpType)
                            or not getattr(arg_type, "manually_managed", False)):
                        raise CompileError(
                            f"giveRequest() requires a manually-managed 'http?' "
                            f"value, found {types_mod.type_name(arg_type)} -- declare "
                            f"the receiving 'on request(req:http?)' handler's own "
                            f"parameter with '?' (see claude.md #202's own T? "
                            f"feature): an ordinary, auto-managed 'http' would still "
                            f"be released out from under the thread this hands it to",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    return None
                if callee.prop == "kill":
                    if expr.args:
                        raise CompileError(
                            f"kill() expects no arguments, got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    return None
                if callee.prop == "drain":
                    # claude.md #231 (uraikus/festina#91): blocks until
                    # `thread_name`'s own inbound queue is fully
                    # processed -- everything already queued at the
                    # moment this call runs, no new messages accepted
                    # meanwhile changes that -- distinct from kill(),
                    # which explicitly does NOT wait and discards
                    # anything still queued. Same main-only,
                    # no-arguments shape as kill(); a dead (never
                    # live()'d, or already kill()'d) thread's own drain
                    # is a safe no-op at the runtime level, not rejected
                    # here -- draining nothing is a valid thing to ask
                    # for.
                    if expr.args:
                        raise CompileError(
                            f"drain() expects no arguments, got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    return None
                if callee.prop == "live":
                    if len(expr.args) != 1:
                        raise CompileError(
                            f"live() expects exactly 1 argument (a func[bool]:void "
                            f"callback), got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    fn_type = infer(expr.args[0], scope)
                    expected = types_mod.FuncType((_BOOL,), None)
                    if fn_type != expected:
                        raise CompileError(
                            f"live() expects func[bool]:void, found "
                            f"{types_mod.type_name(fn_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    return None
                # callee.prop == "isAlive"
                if expr.args:
                    raise CompileError(
                        f"isAlive() expects no arguments, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return _BOOL
            # claude.md #188 (uraikus/festina#76 item 1):
            # Math.floorDiv(a:int, b:int) -> int -- floor-toward-
            # negative-infinity integer division, JS's Math.floorDiv
            # doesn't exist but Python's `//`/Java's Math.floorDiv do,
            # and this follows their exact rounding direction (not C's
            # own truncate-toward-zero `/`). Checked in its own branch,
            # ahead of the generic Math float-argument check just below,
            # since this is the one Math function taking INT arguments.
            if (isinstance(callee.obj, ast.Identifier) and callee.obj.name == "Math"
                    and callee.prop in MATH_INT2_FUNCTIONS):
                if len(expr.args) != 2:
                    raise CompileError(
                        f"Math.{callee.prop}() expects exactly 2 argument(s), "
                        f"got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                for arg in expr.args:
                    arg_type = infer(arg, scope)
                    if arg_type is not None and arg_type is not NULL and arg_type != _INT:
                        raise CompileError(
                            f"Math.{callee.prop}() expects int argument(s), "
                            f"found {types_mod.type_name(arg_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                return _INT
            # claude.md #56: Math.floor/ceil/round/trunc(x:float) -> int
            if (isinstance(callee.obj, ast.Identifier) and callee.obj.name == "Math"
                    and callee.prop in MATH_FUNCTIONS):
                expected = 0 if callee.prop == "random" else (
                    2 if callee.prop in MATH_FLOAT2_FUNCTIONS else 1)
                if len(expr.args) != expected:
                    raise CompileError(
                        f"Math.{callee.prop}() expects exactly {expected} "
                        f"argument(s), got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                for arg in expr.args:
                    arg_type = infer(arg, scope)
                    if arg_type is not None and arg_type is not NULL and arg_type != _FLOAT:
                        raise CompileError(
                            f"Math.{callee.prop}() expects float argument(s), found {types_mod.type_name(arg_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                # claude.md #56 vs #93: only the rounding four answer
                # with an int; everything else stays in float.
                return _INT if callee.prop in MATH_ROUNDING_FUNCTIONS else _FLOAT
            # claude.md #55: int.toFloat() -> float
            if callee.prop == "toFloat" and not expr.args and infer(callee.obj, scope) == _INT:
                return _FLOAT
            # claude.md #150: text.toInt() -> int, JS parseInt()-style
            # (skips leading whitespace/an optional sign, stops at the
            # first non-digit rather than requiring the whole text to
            # be numeric) -- null (int's own -9223372036854775808
            # sentinel, the same one every other "no valid int here"
            # site in this language already answers with) when nothing
            # parseable is found at all, never a compile-time-only or
            # runtime failure.
            if callee.prop == "toInt" and not expr.args and infer(callee.obj, scope) == _TEXT:
                return _INT
            # int/float/bool.toText() -> text -- an explicit spelling of
            # the same stringification template interpolation already
            # does implicitly for these three types (see codegen.py's
            # _to_text); the receiver check is against the SAME three
            # types _to_text itself handles, kept in sync deliberately.
            if callee.prop == "toText" and not expr.args:
                recv = infer(callee.obj, scope)
                if recv in (_INT, _FLOAT, types_mod.PrimitiveType("bool")):
                    return _TEXT
                # claude.md #114: containers render JSON-like, so their
                # explicit .toText() types as text too. (blob's own
                # toText is handled with the rest of its methods.)
                if isinstance(recv, (types_mod.StructType, types_mod.TableType,
                                     types_mod.ArrayType, types_mod.MapType)):
                    return _TEXT
            # claude.md #159: 'json'.toStruct(T) -> T; 'json'.toArr(T)
            # -> arr[T]. The parser only ever produces a single-element
            # args list containing an ast.TypeArg for these two method
            # names (see parse_call_member's own comment) -- the
            # isinstance check here is defensive, not load-bearing.
            if callee.prop in ("toStruct", "toArr") and len(expr.args) == 1 \
                    and isinstance(expr.args[0], ast.TypeArg):
                recv = infer(callee.obj, scope)
                if recv is not None and recv is not NULL and recv != _TEXT:
                    raise CompileError(
                        f"{callee.prop}() can only be called on text, found "
                        f"{types_mod.type_name(recv)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid method receiver",
                    )
                target_type = resolve_type_name(
                    expr.args[0].type_expr, structs, tables, enums, filename, expr.args[0])
                # claude.md #173 (widens claude.md #159's v1 scope cut):
                # a target struct's own field types and toArr()'s own
                # element type may now themselves be a nested struct,
                # arr[T] or map[T], as long as every scalar they
                # eventually bottom out at is int/float/bool/text --
                # see _is_json_parseable_type's own doc comment.
                # Rejected here, at compile time, with a clear message
                # naming exactly what's unsupported -- never silently
                # ignored or left null.
                if callee.prop == "toStruct":
                    if not isinstance(target_type, types_mod.StructType):
                        raise CompileError(
                            f"toStruct()'s argument must be a struct name, found "
                            f"{types_mod.type_name(target_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    for fname, ftype in structs.get(target_type.name, {}).items():
                        if not _is_json_parseable_type(ftype, structs):
                            raise CompileError(
                                f"toStruct({target_type.name}) doesn't support field "
                                f"'{fname}' of type {types_mod.type_name(ftype)} yet -- "
                                f"only int/float/bool/text, a struct, arr[T] or map[T] "
                                f"built from those (recursively) are supported",
                                file=filename, line=callee.line, column=callee.column,
                                category="invalid function argument type",
                            )
                    return target_type
                else:  # toArr
                    if not _is_json_parseable_type(target_type, structs):
                        raise CompileError(
                            f"toArr()'s element type must be int/float/bool/text, a "
                            f"struct, arr[T] or map[T] built from those (recursively), "
                            f"found {types_mod.type_name(target_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    return types_mod.ArrayType(target_type)
            # claude.md #116: sentence.split(sep) -> arr[text]; sep is a
            # text (literal substring) or a regex, the same pair
            # .replace() already accepts.
            if callee.prop == "split" and infer(callee.obj, scope) == _TEXT:
                if len(expr.args) != 1:
                    raise CompileError(
                        f"split() expects exactly 1 argument (a text or regex "
                        f"separator), got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                sep_type = infer(expr.args[0], scope)
                if (sep_type is not None and sep_type is not NULL
                        and sep_type not in (_TEXT, _REGEX)):
                    raise CompileError(
                        f"split()'s separator must be text or regex, found "
                        f"{types_mod.type_name(sep_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return types_mod.ArrayType(_TEXT)
            # claude.md #116: words.join(sep) -> text, on an array of
            # text/int/float/bool -- element kinds with a text form of
            # their own. A null element joins as '', JS's choice.
            if callee.prop == "join":
                obj_type = infer(callee.obj, scope)
                if isinstance(obj_type, types_mod.ArrayType):
                    if obj_type.element not in (_TEXT, _INT, _FLOAT,
                                                 types_mod.PrimitiveType("bool")):
                        raise CompileError(
                            f"join() works on an array of text/int/float/bool, "
                            f"found {types_mod.type_name(obj_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    if len(expr.args) != 1:
                        raise CompileError(
                            f"join() expects exactly 1 argument (a text "
                            f"separator), got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    sep_type = infer(expr.args[0], scope)
                    if (sep_type is not None and sep_type is not NULL
                            and sep_type != _TEXT):
                        raise CompileError(
                            f"join()'s separator must be text, found "
                            f"{types_mod.type_name(sep_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    return _TEXT
            # claude.md #67: pattern.test(value:text) -> bool
            if callee.prop == "test" and isinstance(infer(callee.obj, scope), types_mod.RegexType):
                if len(expr.args) != 1:
                    raise CompileError(
                        f"test() expects exactly 1 argument, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                arg_type = infer(expr.args[0], scope)
                if arg_type is not None and arg_type is not NULL and arg_type != _TEXT:
                    raise CompileError(
                        f"test() expects a text argument, found {types_mod.type_name(arg_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return types_mod.PrimitiveType("bool")
            # claude.md #68: value.match(pattern:regex) -> text (or null,
            # if there's no match -- claude.md #25: null is valid for
            # every type).
            if callee.prop == "match" and infer(callee.obj, scope) == _TEXT:
                if len(expr.args) != 1:
                    raise CompileError(
                        f"match() expects exactly 1 argument, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                arg_type = infer(expr.args[0], scope)
                if arg_type is not None and arg_type is not NULL and arg_type != _REGEX:
                    raise CompileError(
                        f"match() expects a regex argument, found {types_mod.type_name(arg_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return _TEXT
            # claude.md #107: .replaceAll() is GONE. How many matches a
            # replace touches is a property of the PATTERN now, spelled
            # with JS's own 'g' flag -- `/x/g` replaces every match,
            # `/x/` replaces the first -- rather than a property of
            # which method was called. Two ways to say the same thing
            # is one too many, and the flag is the way JS says it.
            # Caught by name here, like aud.stop() below, so the error
            # can name the replacement instead of just failing.
            if callee.prop == "replaceAll" and infer(callee.obj, scope) == _TEXT:
                raise CompileError(
                    "text has no replaceAll() -- use the 'g' flag on the "
                    "pattern instead: value.replace(/search/g, replacement)",
                    file=filename, line=callee.line, column=callee.column,
                    category="unknown method",
                )
            # claude.md #68: value.replace(search, replacement:text) -> text
            # search may be text (a literal substring match) or regex.
            # claude.md #107: a text search replaces the FIRST match
            # only, since plain text carries no flags to say otherwise
            # -- exactly like JS's String.prototype.replace with a
            # string argument. Every match is spelled /search/g.
            if callee.prop == "replace" and infer(callee.obj, scope) == _TEXT:
                if len(expr.args) != 2:
                    raise CompileError(
                        f"{callee.prop}() expects exactly 2 arguments, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                search_type = infer(expr.args[0], scope)
                if search_type is not None and search_type is not NULL and search_type not in (_TEXT, _REGEX):
                    raise CompileError(
                        f"{callee.prop}()'s first argument must be text or regex, "
                        f"found {types_mod.type_name(search_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                replacement_type = infer(expr.args[1], scope)
                if replacement_type is not None and replacement_type is not NULL and replacement_type != _TEXT:
                    raise CompileError(
                        f"{callee.prop}()'s replacement argument must be text, "
                        f"found {types_mod.type_name(replacement_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return _TEXT
            # claude.md #38: music.play() / music.stop() / music.isPlaying()
            # -- the only three methods claude.md defines for aud, so
            # (unlike log/fail/sqlite's deliberately open shape) any
            # other method call on an aud value falls through to the
            # generic "Member call" fallback below and fails there via
            # _infer_member's now-strict AudioType handling, the same
            # way an unknown struct field does.
            # claude.md #99: play/playLoop take an OPTIONAL channel.
            # claude.md #109: and both RETURN the channel they played
            # on, as an int. Automatic assignment picks a channel the
            # caller could not otherwise learn, so the pool was
            # addressable only by naming a channel by hand -- which is
            # to say, by not using the pool. -1 if nothing was played.
            if callee.prop in ("play", "playLoop") and isinstance(infer(callee.obj, scope), types_mod.AudioType):
                if len(expr.args) > 1:
                    raise CompileError(
                        f"{callee.prop}() expects 0 or 1 argument (an optional "
                        f"channel), got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                if expr.args:
                    arg_type = infer(expr.args[0], scope)
                    if arg_type is not None and arg_type is not NULL and arg_type != _INT:
                        raise CompileError(
                            f"{callee.prop}()'s channel must be int, found "
                            f"{types_mod.type_name(arg_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                return _INT
            # claude.md #111: row.undefined('col') -- true when the named
            # column was not in the query's result set, or was deleted;
            # false when the database genuinely returned NULL (or a
            # value). The distinction null alone cannot carry.
            if callee.prop == "undefined":
                obj_type = infer(callee.obj, scope)
                if isinstance(obj_type, types_mod.TableType):
                    if len(expr.args) != 1:
                        raise CompileError(
                            f"undefined() expects exactly 1 argument (a column "
                            f"name), got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    arg_type = infer(expr.args[0], scope)
                    if (arg_type is not None and arg_type is not NULL
                            and arg_type != _TEXT):
                        raise CompileError(
                            f"undefined()'s column name must be text, found "
                            f"{types_mod.type_name(arg_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    return _BOOL
            # claude.md #110: save()/saveCopy() -- shared by blob, img and
            # aud, because all three are the same shape of value
            # (claude.md #101/#109: content plus the bytes it came from)
            # and one policy for "write those bytes somewhere" is worth
            # more than three that almost agree.
            #
            # save() takes an OPTIONAL path: with one it adopts that path
            # and writes there, without one it writes to the path it
            # already has. saveCopy() REQUIRES one -- a copy to nowhere
            # in particular is not a thing to ask for, and making the
            # argument mandatory turns "I meant save()" into a compile
            # error rather than a silent overwrite of the original.
            if callee.prop in ("save", "saveCopy"):
                obj_type = infer(callee.obj, scope)
                if (_is_blob_type(obj_type)
                        or isinstance(obj_type, (types_mod.ImageType,
                                                 types_mod.AudioType))):
                    lo = 0 if callee.prop == "save" else 1
                    if not (lo <= len(expr.args) <= 1):
                        if callee.prop == "saveCopy":
                            detail = ("saveCopy() expects exactly 1 argument (the "
                                      "path to copy to) -- use save() to write to "
                                      "this value's own path")
                        else:
                            detail = ("save() expects 0 or 1 argument (an optional "
                                      "path to save to, and adopt)")
                        raise CompileError(
                            f"{detail}, got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    if expr.args:
                        arg_type = infer(expr.args[0], scope)
                        if (arg_type is not None and arg_type is not NULL
                                and arg_type != _TEXT):
                            raise CompileError(
                                f"{callee.prop}()'s path must be text, found "
                                f"{types_mod.type_name(arg_type)}",
                                file=filename, line=callee.line, column=callee.column,
                                category="invalid function argument type",
                            )
                    return _BOOL
            # claude.md #165: <text>.callback(fn:func[T]:void) -- a
            # non-blocking blob load, the file-loading counterpart to
            # claude.md #163's http client callback. T is read directly
            # off `fn`'s OWN signature, not from surrounding context
            # the way claude.md #164's `{...}.send()` needed a
            # VarDecl-specific bypass for -- `.callback()`'s receiver
            # is plain text, never a heterogeneous literal, so there's
            # no MapLit-style inference conflict to route around, and
            # this works as an ordinary expression anywhere a
            # text->blob coercion already would (a VarDecl init, an
            # assignment, a function argument, ...), no special
            # position required.
            #
            # claude.md #171: img/aud now share the same path -- both
            # runtime loaders (festina_runtime_graphics.c,
            # festina_runtime_audio.c) grew a non-throwing decode step a
            # background worker thread can call (a missing file, an
            # unrecognized format, or corrupt data all just leave the
            # value as an empty placeholder, matching blob's own
            # "test, don't fail" contract, rather than calling
            # festina_fail() concurrently with the main thread), and img's
            # own Cairo/libjpeg decode into a private surface was
            # confirmed thread-safe by a real concurrent ThreadSanitizer
            # run, not just by inspection. See claude.md #171's own
            # account, and #165's for the original blob-only design.
            if callee.prop == "callback" and infer(callee.obj, scope) == _TEXT:
                if len(expr.args) != 1:
                    raise CompileError(
                        f"callback() expects exactly 1 argument (the func to "
                        f"call once the background load finishes), got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                fn_type = infer(expr.args[0], scope)
                if (not isinstance(fn_type, types_mod.FuncType)
                        or len(fn_type.param_types) != 1
                        or fn_type.return_type is not None
                        or fn_type.param_types[0] not in (_BLOB, _IMAGE, _AUDIO)):
                    raise CompileError(
                        f"callback() expects func[blob]:void, func[img]:void, or "
                        f"func[aud]:void, found {types_mod.type_name(fn_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return fn_type.param_types[0]
            # claude.md #109: blob's five methods -- the file functions
            # claude.md #93 spelled as free functions taking a path,
            # moved onto the value that already knows the path. Checked
            # by name here, like every other method on a non-struct
            # receiver, so arity and argument types are enforced rather
            # than left to the generic member fallback.
            if callee.prop in _BLOB_METHODS and _is_blob_type(infer(callee.obj, scope)):
                arg_types, return_type = _BLOB_METHODS[callee.prop]
                if len(expr.args) != len(arg_types):
                    raise CompileError(
                        f"{callee.prop}() expects {len(arg_types)} argument"
                        f"{'' if len(arg_types) == 1 else 's'}, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                for i, expected in enumerate(arg_types):
                    arg_type = infer(expr.args[i], scope)
                    if (arg_type is not None and arg_type is not NULL
                            and arg_type != expected):
                        raise CompileError(
                            f"{callee.prop}()'s argument {i + 1} expects "
                            f"{types_mod.type_name(expected)}, found "
                            f"{types_mod.type_name(arg_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                return return_type
            # claude.md #151: http's fixed-shape methods, checked the
            # identical dict-driven way blob's own just above are.
            if callee.prop in _HTTP_METHODS and isinstance(infer(callee.obj, scope), types_mod.HttpType):
                arg_types, return_type = _HTTP_METHODS[callee.prop]
                if len(expr.args) != len(arg_types):
                    raise CompileError(
                        f"{callee.prop}() expects {len(arg_types)} argument"
                        f"{'' if len(arg_types) == 1 else 's'}, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                for i, expected in enumerate(arg_types):
                    arg_type = infer(expr.args[i], scope)
                    if (arg_type is not None and arg_type is not NULL
                            and arg_type != expected):
                        raise CompileError(
                            f"{callee.prop}()'s argument {i + 1} expects "
                            f"{types_mod.type_name(expected)}, found "
                            f"{types_mod.type_name(arg_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                return return_type
            # claude.md #151: socket's own fixed-shape method (`close`).
            if callee.prop in _SOCKET_METHODS and isinstance(infer(callee.obj, scope), types_mod.SocketType):
                arg_types, return_type = _SOCKET_METHODS[callee.prop]
                if len(expr.args) != len(arg_types):
                    raise CompileError(
                        f"{callee.prop}() expects {len(arg_types)} argument"
                        f"{'' if len(arg_types) == 1 else 's'}, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return return_type
            # claude.md #151: req.send(data:any, code:int, headers:map)
            # -- `data` accepts any concrete type with a body form
            # (_is_sendable_type, the same set _to_text already gives
            # a text form to, plus blob sent as its own raw bytes),
            # `code` defaults to 200, `headers` defaults to no extra
            # headers -- both trailing arguments genuinely optional,
            # which _HTTP_METHODS' fixed-arity table has no way to
            # express, so this is its own bespoke branch, the same
            # reason saveCanvas()/setTimeout() above have theirs.
            # claude.md #164: `{...}.send()` -- an http literal built
            # and sent in one expression, no named variable at all --
            # needs the receiver treated as http WITHOUT ever calling
            # the generic `infer(callee.obj, scope)` on it: a raw
            # MapLit's own generic inference demands one homogeneous
            # value type across every entry, which an http-shaped
            # literal's genuinely heterogeneous fields (text/int/map/
            # func/body) can never satisfy, the same reason
            # `http x = {...}` needed its own bypass in the first
            # place (see _validate_http_lit's own doc comment). Gated
            # on `callee.prop == "send"` FIRST -- a MapLit calling some
            # OTHER method name (`{...}.pop()`, say) is nothing to do
            # with http and must fall through untouched.
            # `parser.py`'s own `http {...}` statement shorthand
            # desugars to exactly this same AST shape (a MapLit as a
            # `.send()` call's receiver), so this one check covers
            # both spellings.
            callee_obj_is_http_lit = callee.prop == "send" and isinstance(callee.obj, ast.MapLit)
            if callee_obj_is_http_lit:
                _validate_http_lit(callee.obj, scope, filename, infer)
            if callee.prop == "send" and (callee_obj_is_http_lit or isinstance(infer(callee.obj, scope), types_mod.HttpType)):
                # claude.md #162: send() is now overloaded by ARITY,
                # not just optional trailing arguments the way it used
                # to be -- `req.send(res:http)` (one argument) is the
                # SERVER side (unchanged in spirit from before this
                # entry, just taking one constructed http value now
                # instead of three separate data/code/headers ones),
                # `req.send()` (zero arguments) is the CLIENT side: an
                # outbound request, sent using THIS value's own url/
                # method/headers/body, which then gets overwritten in
                # place with the response (mirroring what a bare
                # `res.toText()` etc. would read afterward) -- the
                # exact same runtime call either way could in
                # principle reach (a live, server-accepted connection
                # calling the zero-arg form, or a plain constructed
                # value calling the one-arg form) is never rejected at
                # compile time, only ever a silent no-op at runtime,
                # the same "never crashes on a value that doesn't
                # apply" convention every other http method already
                # has (see festina_runtime_http.c's own comment).
                if len(expr.args) == 0:
                    return None
                if len(expr.args) == 1:
                    if isinstance(expr.args[0], ast.MapLit):
                        # claude.md #162: an inline response literal --
                        # `req.send({'code':200, ...})` -- same bypass
                        # analyze_var_decl's own http-literal branch
                        # uses, needed here too since this argument
                        # position never goes through that function.
                        _validate_http_lit(expr.args[0], scope, filename, infer)
                        return None
                    arg_type = infer(expr.args[0], scope)
                    if arg_type is not None and arg_type is not NULL and arg_type != _HTTP:
                        raise CompileError(
                            f"send()'s argument expects http, found "
                            f"{types_mod.type_name(arg_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    return None
                raise CompileError(
                    f"send() expects 0 arguments (an outbound client request) "
                    f"or 1 (a constructed http response), got {len(expr.args)}",
                    file=filename, line=callee.line, column=callee.column,
                    category="invalid function argument type",
                )
            # claude.md #151: socket.send(data:any) -- the same
            # sendable-type check as http.send() above, but always
            # exactly one argument (a WebSocket frame has no status
            # code or headers to attach) -- blob sends as a binary
            # frame, everything else as a text frame (see codegen's
            # _emit_socket_send).
            if callee.prop == "send" and isinstance(infer(callee.obj, scope), types_mod.SocketType):
                if len(expr.args) != 1:
                    raise CompileError(
                        f"send() expects exactly 1 argument (the data to send), "
                        f"got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                data_type = infer(expr.args[0], scope)
                if (data_type is not None and data_type is not NULL
                        and not _is_sendable_type(data_type)):
                    raise CompileError(
                        f"send()'s data argument has no body form -- found "
                        f"{types_mod.type_name(data_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return None
            # claude.md #109: aud.stop() is back, and means the thing
            # claude.md #100 identified as its only honest reading --
            # stop every channel playing this clip. #100 removed it
            # because that is almost never what a program firing
            # overlapping effects wants, which is true and was never a
            # reason to withhold it: "silence this sound, wherever it
            # is" is a real thing to want, and doing it by hand meant
            # tracking channel numbers the runtime already knows. The
            # overlapping-effects case is covered by play() returning
            # its channel, so the two coexist instead of one standing
            # in for the other.
            if callee.prop == "stop" and isinstance(infer(callee.obj, scope), types_mod.AudioType):
                if expr.args:
                    raise CompileError(
                        f"stop() expects no arguments, got {len(expr.args)} -- "
                        f"it stops every channel playing this clip; to stop one "
                        f"channel use stopAudioPlayer(channel)",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return None
            # claude.md #92: sheet.clip(x, y, w, h) -> img, and
            # image.resize(w, h) -> void (in place). Checked here rather
            # than left to the generic Member-call fallback so the arity
            # and the int-ness of every argument are enforced.
            # claude.md #96: array methods, JS-shaped.
            if callee.prop in ("push", "pop", "shift", "unshift", "splice",
                               "indexOf", "sort"):
                obj_type = infer(callee.obj, scope)
                if isinstance(obj_type, types_mod.ArrayType):
                    elem = obj_type.element
                    # claude.md #184 (uraikus/festina#76 item 2):
                    # sort(cmpFn) -> void, in place, JS/C-qsort style --
                    # cmpFn:func[T,T]:int, negative/zero/positive meaning
                    # exactly what C's own qsort() comparator already
                    # means (first-before-second / equal / first-after-
                    # second). Checked structurally off cmpFn's own
                    # signature, the same permissive "any func-typed
                    # expression" rule blob/img/aud's `.callback()`
                    # established (claude.md #165/#171) -- not restricted
                    # to a bare declared-function name the way
                    # setTimeout's own older convention is.
                    if callee.prop == "sort":
                        if len(expr.args) != 1:
                            raise CompileError(
                                "sort() expects exactly 1 argument (the "
                                f"comparator), got {len(expr.args)}",
                                file=filename, line=callee.line, column=callee.column,
                                category="invalid function argument type",
                            )
                        fn_type = infer(expr.args[0], scope)
                        if (not isinstance(fn_type, types_mod.FuncType)
                                or fn_type.param_types != (elem, elem)
                                or fn_type.return_type != _INT):
                            raise CompileError(
                                f"sort() expects func[{types_mod.type_name(elem)}, "
                                f"{types_mod.type_name(elem)}]:int, found "
                                f"{types_mod.type_name(fn_type)}",
                                file=filename, line=callee.line, column=callee.column,
                                category="invalid function argument type",
                            )
                        return None
                    # claude.md #97: indexOf(value) -> int, -1 when absent.
                    # The argument has to be assignable to the element type
                    # for the same reason push()'s does: a search for a
                    # value the array cannot hold is a mistake, not a
                    # never-matching search.
                    if callee.prop == "indexOf":
                        if len(expr.args) != 1:
                            raise CompileError(
                                "indexOf() expects exactly 1 argument, "
                                f"got {len(expr.args)}",
                                file=filename, line=callee.line, column=callee.column,
                                category="invalid function argument type",
                            )
                        check_assignable(elem, infer(expr.args[0], scope),
                                          callee, what="indexOf() argument")
                        return _INT
                    if callee.prop in ("push", "unshift"):
                        if len(expr.args) != 1:
                            raise CompileError(
                                f"{callee.prop}() expects exactly 1 argument, "
                                f"got {len(expr.args)}",
                                file=filename, line=callee.line, column=callee.column,
                                category="invalid function argument type",
                            )
                        check_assignable(elem, infer(expr.args[0], scope),
                                          callee, what=f"{callee.prop}() argument")
                        # The new length, as JS returns.
                        return _INT
                    if callee.prop in ("pop", "shift"):
                        if expr.args:
                            raise CompileError(
                                f"{callee.prop}() takes no arguments, "
                                f"got {len(expr.args)}",
                                file=filename, line=callee.line, column=callee.column,
                                category="invalid function argument type",
                            )
                        return elem
                    # claude.md #130: splice(start, count) -> arr[T] of
                    # what was removed, or splice(start, count,
                    # insertArr) -- JavaScript's splice(start,
                    # deleteCount, ...items) with the variadic items
                    # spelled as one arr[T] argument instead, since
                    # Festina has no variadic parameters. Either way the
                    # return value is only what was removed, exactly as
                    # JS's own splice() answers -- the inserted elements
                    # are never handed back, only placed.
                    if len(expr.args) not in (2, 3):
                        raise CompileError(
                            "splice() expects 2 arguments (start, count) or "
                            "3 (start, count, insertArr), "
                            f"got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    for arg in expr.args[:2]:
                        arg_type = infer(arg, scope)
                        if arg_type is not None and arg_type is not NULL and arg_type != _INT:
                            raise CompileError(
                                "splice() expects int arguments, found "
                                f"{types_mod.type_name(arg_type)}",
                                file=filename, line=callee.line, column=callee.column,
                                category="invalid function argument type",
                            )
                    if len(expr.args) == 3:
                        check_assignable(types_mod.ArrayType(elem), infer(expr.args[2], scope),
                                          callee, what="splice() insert argument")
                    return types_mod.ArrayType(elem)
            if callee.prop in ("clip", "resize") and isinstance(infer(callee.obj, scope), types_mod.ImageType):
                expected = 4 if callee.prop == "clip" else 2
                if len(expr.args) != expected:
                    raise CompileError(
                        f"{callee.prop}() expects {expected} argument(s), "
                        f"got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                for i, arg in enumerate(expr.args):
                    arg_type = infer(arg, scope)
                    if arg_type is not None and arg_type is not NULL and arg_type != _INT:
                        raise CompileError(
                            f"{callee.prop}()'s argument {i + 1} expects int, "
                            f"found {types_mod.type_name(arg_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                return types_mod.ImageType() if callee.prop == "clip" else None
            # claude.md #189: img.getPixelColor(x, y) -> color -- the
            # img-method counterpart of the canvas-level
            # getPixelColor(x, y), checked in its own branch (rather
            # than folded into the drawRect/.../drawText block just
            # below) since every one of those returns nothing, and this
            # returns a color.
            if callee.prop == "getPixelColor" and isinstance(infer(callee.obj, scope), types_mod.ImageType):
                if len(expr.args) != 2:
                    raise CompileError(
                        f"getPixelColor() expects 2 arguments, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                for i, arg in enumerate(expr.args):
                    arg_type = infer(arg, scope)
                    if arg_type is not None and arg_type is not NULL and arg_type != _INT:
                        raise CompileError(
                            f"getPixelColor()'s argument {i + 1} expects int, "
                            f"found {types_mod.type_name(arg_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                return _COLOR
            # claude.md #134: drawRect/drawPixel/drawCircle/drawText as
            # methods on img -- the same four canvas-level drawing
            # builtins claude.md #37/#39/#133 already give, now also
            # callable on an image's OWN surface instead of the canvas.
            # drawRect/drawPixel keep their own optional trailing
            # `color` (claude.md #133); coordinates are always in the
            # image's own pixel space, with no window/canvas needed at
            # all -- see codegen.py's _emit_image_draw_method.
            if (callee.prop in ("drawRect", "drawPixel", "drawCircle", "drawText")
                    and isinstance(infer(callee.obj, scope), types_mod.ImageType)):
                # claude.md #188 (uraikus/festina#76 item 8): the same
                # optional-trailing-fill-and-border-colour forms the
                # free-function versions gained, in _BUILTIN_SIGNATURE_
                # ALTERNATES above -- drawCircle joins drawRect/drawPixel
                # here too, newly, having had no per-call colour override
                # at all before this.
                alternates = {
                    "drawRect": [(_INT, _INT, _INT, _INT), (_INT, _INT, _INT, _INT, _COLOR),
                                 (_INT, _INT, _INT, _INT, _COLOR, _COLOR)],
                    "drawPixel": [(_INT, _INT), (_INT, _INT, _COLOR)],
                    "drawCircle": [(_INT, _INT, _INT), (_INT, _INT, _INT, _COLOR),
                                    (_INT, _INT, _INT, _COLOR, _COLOR)],
                }.get(callee.prop)
                if alternates is not None:
                    sig = next((a for a in alternates if len(a) == len(expr.args)), None)
                    if sig is None:
                        shapes = " or ".join(str(len(a)) for a in alternates)
                        raise CompileError(
                            f"{callee.prop}() expects {shapes} argument(s), "
                            f"got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                else:
                    sig = {"drawText": (_TEXT, _INT, _INT)}[callee.prop]
                    if len(expr.args) != len(sig):
                        raise CompileError(
                            f"{callee.prop}() expects {len(sig)} argument(s), "
                            f"got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                for i, (arg, expected) in enumerate(zip(expr.args, sig)):
                    arg_type = infer(arg, scope)
                    if arg_type is not None and arg_type is not NULL and arg_type != expected:
                        raise CompileError(
                            f"{callee.prop}()'s argument {i + 1} expects "
                            f"{types_mod.type_name(expected)}, found {types_mod.type_name(arg_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                return None
            if callee.prop == "isPlaying" and isinstance(infer(callee.obj, scope), types_mod.AudioType):
                if expr.args:
                    raise CompileError(
                        f"isPlaying() takes no arguments, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return types_mod.PrimitiveType("bool")
            # claude.md #72: npcHealths.forEach(callback) -- the callback
            # is checked structurally, the same way setTimeout/
            # setInterval's callback is above: it has to be the bare
            # name of an already-declared function (Festina has no
            # first-class functions/closures), not an arbitrary
            # expression, since its *declaration* (parameter types) is
            # what's being validated here, not its value.
            if callee.prop == "forEach":
                obj_type = infer(callee.obj, scope)
                if isinstance(obj_type, types_mod.MapType):
                    if len(expr.args) != 1:
                        raise CompileError(
                            f"forEach() expects exactly 1 argument (a callback function), "
                            f"got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    callback_expr = expr.args[0]
                    if not isinstance(callback_expr, ast.Identifier):
                        raise CompileError(
                            "forEach()'s argument must be the name of a declared function, "
                            "not an arbitrary expression",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    callback_sym = scope.lookup(callback_expr.name)
                    if callback_sym is None or callback_sym.kind != "function":
                        raise CompileError(
                            f"'{callback_expr.name}' is not a declared function",
                            file=filename, line=callback_expr.line, column=callback_expr.column,
                            category="unknown function",
                        )
                    params = callback_sym.node.params
                    value_type_name = types_mod.type_name(obj_type.value)
                    expected_sig = f"({value_type_name} value, text key)"
                    if callback_sym.type is not None or len(params) != 2:
                        raise CompileError(
                            f"the callback passed to forEach() must take exactly 2 parameters "
                            f"(the map's value, then its key) and return nothing -- declare it as "
                            f"'void func {callback_expr.name}(v:{value_type_name}, key:text) {{ ... }}'",
                            file=filename, line=callback_expr.line, column=callback_expr.column,
                            category="invalid function argument type",
                        )
                    value_param_type = resolve(params[0].type_expr, callee)
                    key_param_type = resolve(params[1].type_expr, callee)
                    if value_param_type != obj_type.value:
                        raise CompileError(
                            f"forEach()'s callback first parameter must be {value_type_name} "
                            f"(this map's value type), found {types_mod.type_name(value_param_type)}",
                            file=filename, line=callback_expr.line, column=callback_expr.column,
                            category="invalid function argument type",
                        )
                    if key_param_type != _TEXT:
                        raise CompileError(
                            f"forEach()'s callback second parameter must be text (the map's key), "
                            f"found {types_mod.type_name(key_param_type)}",
                            file=filename, line=callback_expr.line, column=callback_expr.column,
                            category="invalid function argument type",
                        )
                    return None
            # claude.md #186 (uraikus/festina#76 item 7): map[T].keys()
            # -> arr[text], map[T].values() -> arr[T] -- a plain
            # snapshot array, walkable with an ordinary `for` loop, no
            # callback needed at all. Exists specifically to sidestep
            # forEach()'s own bare/no-closures callback restriction
            # (claude.md #72) for the common "collect entries matching
            # a condition" case, where that restriction otherwise pushes
            # every call site's own accumulator state into extra
            # globals purely so the callback can reach it.
            if callee.prop in ("keys", "values"):
                obj_type = infer(callee.obj, scope)
                if isinstance(obj_type, types_mod.MapType):
                    if expr.args:
                        raise CompileError(
                            f"{callee.prop}() takes no arguments, got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    if callee.prop == "keys":
                        return types_mod.ArrayType(_TEXT)
                    return types_mod.ArrayType(obj_type.value)
        # Member call, e.g. someUnknownMethod() on a struct-shaped
        # receiver -- validates the member access itself (so an unknown
        # method on a real type still fails with a specific message
        # rather than silently passing through as untyped).
        callee_type = infer(callee, scope)
        if isinstance(callee_type, types_mod.FuncType):
            # claude.md #141: an INDIRECT call through a func[...]:...
            # -typed STRUCT FIELD (h.cb(...)), ARRAY ELEMENT (fns[i](...)),
            # or MAP VALUE (handlers[key](...)) -- every shape this
            # fallback's own generic `infer(callee, scope)` just above
            # already resolves correctly (Member/computed-Member
            # inference doesn't care whether it's reached from a Call or
            # anywhere else), so the only thing left is the same arity/
            # argument-type validation the bare-Identifier indirect-call
            # branch above already does, checked against callee_type's
            # own signature rather than a Symbol's.
            if len(expr.args) != len(callee_type.param_types):
                raise CompileError(
                    f"expects {len(callee_type.param_types)} argument(s), "
                    f"got {len(expr.args)}",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid function argument type",
                )
            for i, (arg_expr, expected) in enumerate(zip(expr.args, callee_type.param_types)):
                arg_type = infer(arg_expr, scope)
                check_assignable(expected, arg_type, expr, what=f"argument {i + 1}")
            return callee_type.return_type
        for a in expr.args:
            infer(a, scope)
        return None

    def analyze_struct(decl):
        # claude.md #106: `structs[name]` may already hold the empty
        # placeholder this declaration's own pre-pass entry installed,
        # so a duplicate is a name with real FIELDS in it, or one the
        # other namespace claimed -- not merely a name that is present.
        if structs.get(decl.name) or tables.get(decl.name) is not None:
            raise CompileError(
                f"'{decl.name}' is already declared",
                file=filename, line=decl.line, column=decl.column, category="duplicate declaration",
            )
        # claude.md #106: the name is registered BEFORE its own fields
        # resolve, which is the whole fix for `struct Node { next:Node }`.
        # Resolving fields first meant a struct could not mention itself
        # -- the name simply was not in `structs` yet -- and the error
        # said "unknown type 'Node'", which reads like a typo rather
        # than an ordering rule. Nothing about the representation ever
        # required this: a struct-typed field is a pointer (see
        # codegen's _llvm_type), so a self-reference is finite-sized,
        # and claude.md #97's auto-vivification makes reaching through
        # one work without any further machinery.
        #
        # The placeholder is empty and is replaced below rather than
        # mutated in place, so a field lookup can never observe a
        # half-built struct: resolve() only needs the NAME to exist to
        # hand back a StructType, never the field list.
        structs[decl.name] = {}
        try:
            field_types = {}
            for f in decl.fields:
                field_types[f.name] = resolve(f.type_expr, decl)
        except Exception:
            del structs[decl.name]   # leave no half-registered name behind
            raise
        structs[decl.name] = field_types

    def analyze_table(decl):
        # claude.md #106: see analyze_struct's own note on why this
        # tests for real fields rather than for the name's presence.
        if tables.get(decl.name) or structs.get(decl.name) is not None:
            raise CompileError(
                f"'{decl.name}' is already declared",
                file=filename, line=decl.line, column=decl.column, category="duplicate declaration",
            )
        # claude.md #106: registered before its own fields resolve, the
        # same way a struct is -- though a table referring to itself is
        # far less useful, since a column's SQL type has to be one of
        # the scalars festina_sql_type knows.
        tables[decl.name] = {}
        try:
            columns = {}
            for f in decl.fields:
                resolve(f.type_expr, decl)  # validates the type is known
                columns[f.name] = f.type_expr
        except Exception:
            del tables[decl.name]
            raise
        tables[decl.name] = columns

    def analyze_enum(decl):
        # claude.md #176: same duplicate-declaration/placeholder-before-
        # resolving shape analyze_struct/analyze_table already use --
        # see analyze_struct's own comment for the full reasoning
        # (a name existing with real content is a duplicate; a bare
        # placeholder is not).
        if (structs.get(decl.name) or tables.get(decl.name) is not None
                or enums.get(decl.name) is not None):
            raise CompileError(
                f"'{decl.name}' is already declared",
                file=filename, line=decl.line, column=decl.column, category="duplicate declaration",
            )
        # claude.md #176: unlike struct/table, an enum can never
        # legitimately self-reference (no enum-of-enum -- checked
        # below), so there's no "placeholder before resolving" step
        # needed here the way analyze_struct's own comment explains --
        # `enums[decl.name]` is already `None` from the pre-scan (or
        # this is a name the pre-scan somehow missed; harmless either
        # way), and stays that way until every member has resolved
        # successfully.
        try:
            members = []
            for m in decl.members:
                mtype = resolve(m, decl)
                # claude.md #176: no enum-of-enum -- a member type is
                # itself a compile-time-fixed, single concrete type
                # (int, a struct, ...); an EnumType is the one kind of
                # type that ISN'T single/concrete (that's the whole
                # point of it), so nesting one inside another would mean
                # deciding what "the tag" of an enum-typed member even
                # is, a real design question this round doesn't answer.
                if isinstance(mtype, types_mod.EnumType):
                    raise CompileError(
                        f"enum '{decl.name}' cannot have another enum ('{types_mod.type_name(mtype)}') "
                        f"as a member",
                        file=filename, line=decl.line, column=decl.column,
                        category="invalid declaration",
                    )
                if mtype in members:
                    raise CompileError(
                        f"enum '{decl.name}' lists '{types_mod.type_name(mtype)}' more than once",
                        file=filename, line=decl.line, column=decl.column,
                        category="duplicate declaration",
                    )
                members.append(mtype)
            is_pure_struct = all(isinstance(m, types_mod.StructType) for m in members)
            # claude.md #176: field access (`shape.radius`) only exists
            # for a pure-struct enum, and only works at all because it
            # resolves to a single, unambiguous owning member at
            # COMPILE time -- so two members declaring the same field
            # name would make `shape.thatField` genuinely ambiguous.
            # Rejected here, once, at declaration time, rather than
            # needing per-access-site disambiguation (or a runtime
            # ordering rule) later.
            if is_pure_struct:
                owner_by_field = {}
                for m in members:
                    for fname in structs[m.name]:
                        if fname in owner_by_field and owner_by_field[fname] != m.name:
                            raise CompileError(
                                f"enum '{decl.name}': field '{fname}' is declared by both "
                                f"'{owner_by_field[fname]}' and '{m.name}' -- field access on "
                                f"'{decl.name}' would be ambiguous",
                                file=filename, line=decl.line, column=decl.column,
                                category="invalid declaration",
                            )
                        owner_by_field[fname] = m.name
        except Exception:
            del enums[decl.name]
            raise
        enums[decl.name] = _EnumInfo(members=members, is_pure_struct=is_pure_struct)

    def analyze_var_decl(decl, scope, is_global):
        declared_type = resolve(decl.type_expr, decl)
        # claude.md #202: `T?` -- applied immediately after resolve(),
        # before ANY of the checks below (the `amor` no-initializer
        # check, the TableType check, check_assignable, ...) run, so
        # every one of them already sees the real, possibly-manually-
        # managed type -- in particular, check_assignable's own generic
        # `declared != actual` fallback is what makes a `Circle?`
        # source assigned into an ordinary `Circle`-typed position (or
        # vice versa) a compile error, with zero new code of its own
        # (the two are already unequal dataclass instances once this
        # line runs).
        declared_type = apply_manually_managed(declared_type, decl.manually_managed)
        # claude.md #174: `amor arr[T]` (local or global) requires an
        # initializer -- unlike plain arr[T]/map[T], which start
        # "empty" via a real, immortal, zero-entry static header (see
        # codegen.py's _global_var_defs), an amortized array local's
        # own with-no-initializer path was deliberately never given
        # the equivalent (codegen's own scope boundary: it always
        # heap-allocates through the generic path instead, which needs
        # a real value to store, not an implicit empty default) --
        # requiring one here is what keeps that boundary from ever
        # being reached as an uninitialized-pointer bug instead of a
        # clear compile error. Struct fields have no initializer
        # syntax at all, so this can't (and doesn't need to) apply to
        # them -- they rely on auto-vivify instead (see codegen.py's
        # own comment on that path). map[T] has no such requirement any
        # more -- claude.md #175 removed `amor map[T]`, so every
        # map[T] declaration (with or without an initializer) takes the
        # ordinary plain-map path now.
        if (isinstance(declared_type, types_mod.ArrayType) and declared_type.amortized
                and decl.init is None):
            raise CompileError(
                f"'{decl.name}' (amor arr[{types_mod.type_name(declared_type.element)}]) "
                f"requires an initializer -- write e.g. `amor arr[{types_mod.type_name(declared_type.element)}] "
                f"{decl.name} = []` for an empty one",
                file=filename, line=decl.line, column=decl.column,
                category="invalid declaration",
            )
        # claude.md #178 (new entry): a table-typed value is a BORROWED
        # handle onto one row of a query result (codegen.py's own
        # `_emit_free`, "TableType (a borrowed query row)") -- unlike
        # struct, it has never had its own standalone allocation/auto-
        # vivify story, since every real row it could ever alias
        # already exists somewhere (an arr[T] the query result built).
        # `Table t` with no initializer defaults to null exactly like
        # any other pointer-shaped type -- but codegen's own field-
        # write path (_member_ptr_from's TableType branch) computes a
        # plain `getelementptr i8, ptr %obj, i64 idx*8` off that
        # pointer with no null check at all (unlike struct fields,
        # which auto-vivify, and unlike enum values, which fail
        # loudly) -- so the very first `t.field = ...` on one
        # segfaulted, confirmed directly (SIGSEGV, not a hang or a
        # clean crash) by actually compiling and running the
        # reproduction, not just by reading the code. Rejected here
        # instead of taught to auto-vivify: doing that would mean
        # inventing real ownership/allocation semantics for TableType
        # that do not exist anywhere else in this compiler (it is never
        # retained, released, or freed -- see FreeStmt's own comment),
        # a materially bigger change than this bug report calls for.
        # `struct` already has everything a hand-built row needs
        # (api.md's own "Structs as query targets" section is exactly
        # this pattern) and needs no such check, since its local
        # declaration already always allocates real, zeroed storage
        # immediately (codegen.py's own VarDecl StructType branch).
        if isinstance(declared_type, types_mod.TableType) and decl.init is None:
            raise CompileError(
                f"'{decl.name}' ({declared_type.name}) requires an initializer -- "
                f"a table row is a borrowed handle onto one row of a query result, "
                f"never independently constructed (assign an existing row, e.g. "
                f"`{declared_type.name} {decl.name} = rows[0]`); to build a value "
                f"by hand, declare a struct with the same fields instead (see "
                f"api.md's 'Structs as query targets' section)",
                file=filename, line=decl.line, column=decl.column,
                category="invalid declaration",
            )
        if decl.init is not None:
            # claude.md #137: arr[img]/arr[aud]/arr[blob] declared
            # directly from a literal of paths -- `arr[img] brushes =
            # ['./brush1.png', './brush2.png']` -- the array-typed
            # counterpart of `img sprite = 'sprite.png'` (claude.md
            # #100/#101/#109's own one-directional text -> media
            # allowance, handled just below in check_assignable for a
            # single value). ArrayLit's own generic inference (just
            # above) has no notion of an "expected" element type -- it
            # only ever infers each element's OWN type and demands they
            # all agree, so `['a.png', 'b.png']` infers as arr[text]
            # regardless of what it's being declared into, and
            # check_assignable's array/map case never had a "text
            # element coerces to media element" rule (only the
            # all-null case) -- so this needs its own check here,
            # bypassing the generic infer()+check_assignable() path
            # for exactly this one shape. Every element must be EITHER
            # a path (text) or already the declared media type itself
            # (e.g. an existing img being reused in the literal) --
            # codegen's own _emit_array_lit/_coerce already do the
            # per-element text -> media loading generically once the
            # expected element type reaches them; the gap was only
            # ever here, in what semantic.py would let through.
            if (isinstance(declared_type, types_mod.ArrayType)
                    and declared_type.element in (_IMAGE, _AUDIO, _BLOB)
                    and isinstance(decl.init, ast.ArrayLit)):
                elem = declared_type.element
                for e in decl.init.elements:
                    etype = infer(e, scope)
                    if etype is not None and etype is not NULL and etype != elem and etype != _TEXT:
                        raise CompileError(
                            f"array literal element expects "
                            f"{types_mod.type_name(elem)} (or text, naming a "
                            f"path), found {types_mod.type_name(etype)}",
                            file=filename, line=getattr(e, "line", 0),
                            column=getattr(e, "column", 0),
                            category="invalid operand type",
                        )
            elif (isinstance(declared_type, types_mod.ArrayType) and declared_type.amortized
                    and isinstance(decl.init, ast.ArrayLit)):
                # claude.md #174: same bypass shape as the arr[img]/
                # amor-map cases here -- ArrayLit's own generic
                # inference (just above) always returns a NON-amortized
                # ArrayType regardless of context, so the generic
                # infer()+check_assignable() path below would always
                # reject a `[...]` literal against an `amor arr[T]`
                # declared type. The media (img/aud/blob) element case
                # is already covered by the branch just above --
                # unconditional on `.amortized`, so it already handles
                # `amor arr[img] pics = [...]` too -- this only ever
                # runs for a non-media amor arr[T].
                elem_type_name = types_mod.type_name(declared_type.element)
                for e in decl.init.elements:
                    etype = infer(e, scope)
                    if etype is not None and etype is not NULL and etype != declared_type.element:
                        raise CompileError(
                            f"array literal element expects {elem_type_name}, "
                            f"found {types_mod.type_name(etype)}",
                            file=filename, line=getattr(e, "line", 0),
                            column=getattr(e, "column", 0),
                            category="invalid operand type",
                        )
            elif (isinstance(declared_type, types_mod.HttpType)
                    and (isinstance(decl.init, ast.MapLit)
                         or _http_send_lit_receiver(decl.init) is not None)):
                # claude.md #162: same bypass shape as the arr[img] case
                # above, for the identical reason -- MapLit's
                # own generic inference demands one homogeneous value
                # type across every entry, which an http literal's
                # genuinely heterogeneous field set (text/int/map/body)
                # can never satisfy. claude.md #164 widens this to
                # `http req = {...}.send()` too (see
                # _http_send_lit_receiver's own doc comment) -- the
                # trailing `.send()` is validated as a plain 0-argument
                # http `send()` call by _infer_call already having run
                # on `decl.init` via the generic path below... except
                # it HASN'T, because this whole branch is instead of
                # that generic infer()+check_assignable() call, so the
                # MapLit itself is what actually gets validated here --
                # correct either way, since the trailing `.send()`
                # syntax carries no extra semantic content beyond "the
                # literal just built also gets sent."
                lit = decl.init if isinstance(decl.init, ast.MapLit) else _http_send_lit_receiver(decl.init)
                _validate_http_lit(lit, scope, filename, infer)
            elif decl.manually_managed and _is_fresh_construction(decl.init):
                # claude.md #204: a manually-managed declaration's own
                # initializer may adopt manually-managed-ness from a
                # FRESH construction (a Call/ArrayLit/MapLit/RegexLit)
                # of the matching BARE type -- see
                # _is_fresh_construction's own doc comment for why this
                # is needed at all and why it's safe: nothing else can
                # already reference a value that was JUST constructed
                # right here, so there is no aliasing hazard the
                # ordinary "no implicit decay" rule exists to prevent.
                # Checks against the BARE (unflagged) declared type,
                # since that's what a fresh construction always infers
                # as; `declared_type` itself (already manually-managed)
                # is what actually gets bound below, same as every
                # other branch here.
                actual_type = infer(decl.init, scope)
                bare_declared = (dataclasses.replace(declared_type, manually_managed=False)
                                  if isinstance(declared_type, _MANUALLY_MANAGEABLE_TYPES)
                                  else declared_type)
                check_assignable(bare_declared, actual_type, decl)
            else:
                actual_type = infer(decl.init, scope)
                check_assignable(declared_type, actual_type, decl)
        kind = "constant" if decl.is_const else "variable"
        scope.define(decl.name, Symbol(decl.name, declared_type, kind, decl), decl, filename)

    def register_func_signature(decl):
        # `log`/`fail`/`sqlite` are already lexer keywords, so a
        # function can never be declared with those names in the first
        # place -- but drawRect/drawCircle/drawText/drawImage/
        # loadImage/loadAudio/regex/setTimeout/setInterval/
        # clearTimeout/clearInterval are ordinary identifiers, only
        # recognized as builtins by name inside _infer_call's Call
        # dispatch (which always checks the builtin name *before* ever
        # looking the name up in scope). A user function declared with
        # one of those names would therefore compile fine but be
        # permanently unreachable -- every call to it resolves to the
        # builtin instead, silently. This used to be accepted (see
        # security.md's audit writeup) on the reasoning that none of
        # graphics/audio/timers were implemented yet, so the collision
        # couldn't actually bite; now that they are, it can.
        #
        # claude.md #140: this is deliberately just the NAME/SIGNATURE
        # half of what used to be one function (analyze_func, below) --
        # called once per FuncDecl from the whole-program pre-pass near
        # the bottom of analyze(), for every FuncDecl reachable anywhere
        # in the program (however deeply nested -- see
        # _iter_func_decls), before a single CALL anywhere gets checked.
        # That's what makes declaration order stop mattering
        # ("hoisting"): a call reached earlier in the real analysis pass
        # than its own callee's textual declaration still finds the
        # name already defined in global_scope, with its real signature,
        # by the time _infer_call looks it up.
        if decl.name in BUILTIN_FUNCTIONS:
            raise CompileError(
                f"'{decl.name}' is a builtin function name and cannot be used to "
                f"declare a function -- a function declared with this name would be "
                f"permanently unreachable, since every call to '{decl.name}(...)' "
                f"resolves to the builtin instead",
                file=filename, line=decl.line, column=decl.column,
                category="duplicate declaration",
            )
        return_type = resolve(decl.return_type, decl) if decl.return_type != "void" else None
        global_scope.define(decl.name, Symbol(decl.name, return_type, "function", decl), decl, filename)
        # claude.md #195: populated in lockstep, same signature, no
        # parent -- see functions_scope's own comment above. Never
        # collides (global_scope.define just above already guarantees
        # this exact name is being defined here for the first time).
        functions_scope.vars[decl.name] = Symbol(decl.name, return_type, "function", decl)

    def analyze_func(decl):
        # claude.md #140: decl's own signature was already registered
        # by the pre-pass above (register_func_signature) -- this only
        # analyzes the BODY now, wherever the declaration is textually
        # reached during the real walk. Re-resolving return_type here
        # (rather than reading it back out of the Symbol register_func_
        # signature already stored) is what analyze_func has always
        # done and stays cheap and side-effect free either way --
        # resolve() is a pure function of structs/tables, neither of
        # which changes mid-compile.
        return_type = resolve(decl.return_type, decl) if decl.return_type != "void" else None
        func_scope = Scope(global_scope)
        for p in decl.params:
            # claude.md #202: `T?` -- resolve() then, when the
            # parameter's own `?` was written, rebuild with
            # manually_managed=True (see apply_manually_managed's own
            # doc comment). A manually-managed parameter is still just
            # borrowed like any other (codegen's own escaping-parameter
            # retain logic separately learns to skip retaining one).
            ptype = apply_manually_managed(resolve(p.type_expr, decl), p.manually_managed)
            func_scope.define(p.name, Symbol(p.name, ptype, "parameter"), decl, filename)
        analyze_block(decl.body, func_scope, return_type=return_type)

    def analyze_event_handler(decl):
        # claude.md #40: see _EVENT_SIGNATURES above -- click/mouse/key/
        # resize/close get a fixed-signature check; any other event name
        # is unconstrained (and simply never fires -- there's no event
        # source claude.md defines for it).
        #
        # claude.md #208: `on message(worker:thread, msg:T)` is the one
        # exception -- the unified handler for everything sent to main
        # (`NAME.postMessage(x)` from any thread, or bare postMessage(x)
        # from inside another thread's own body). Not in
        # _EVENT_SIGNATURES at all (that table only ever holds FIXED
        # signatures; `msg`'s own type is the receiver's choice, the
        # same "declared, not inferred" shape a thread's own `on
        # message` already has) -- checked here instead, via the same
        # shared helper analyze_thread's own `on message` handling
        # uses, and its result recorded into `_main_message_type[0]`
        # for every bare postMessage(x) call site to check against.
        if decl.name == "message":
            if _main_message_type[0] is not None:
                raise CompileError(
                    "the program already declares a top-level 'on message' handler",
                    file=filename, line=decl.line, column=decl.column,
                    category="duplicate declaration",
                )
            _main_message_type[0] = _check_message_handler_params(
                decl.params, decl, filename, structs, tables, enums,
                "(worker:thread, msg:T) -- worker.main is true when sent by main")
            handler_scope = Scope(global_scope)
            for p in decl.params:
                ptype = apply_manually_managed(resolve(p.type_expr, decl), p.manually_managed)
                handler_scope.define(p.name, Symbol(p.name, ptype, "parameter"), decl, filename)
            analyze_block(decl.body, handler_scope, return_type=None)
            return
        entry = _EVENT_SIGNATURES.get(decl.name)
        if entry is not None:
            sig, help_text = entry
            param_types = tuple(resolve(p.type_expr, decl) for p in decl.params)
            if param_types != sig:
                raise CompileError(
                    f"on {decl.name}(...) must declare exactly {help_text}, "
                    f"matching claude.md #40's own example",
                    file=filename, line=decl.line, column=decl.column,
                    category="invalid function argument type",
                )
        handler_scope = Scope(global_scope)
        for p in decl.params:
            ptype = apply_manually_managed(resolve(p.type_expr, decl), p.manually_managed)
            handler_scope.define(p.name, Symbol(p.name, ptype, "parameter"), decl, filename)
        analyze_block(decl.body, handler_scope, return_type=None)


    def analyze_thread(decl):
        # claude.md #195: analyzed at its own ORDINARY third-pass
        # position, in textual program order -- exactly like
        # struct/table/enum/func's own BODY analysis (only their bare
        # NAMES are pre-registered early; a struct's real FIELD types,
        # a function's real BODY, are only resolved once that
        # declaration's own textual position is reached). Threads get
        # no special early treatment either: an earlier draft of this
        # gave threads a fully-early pass (right after function-
        # signature hoisting), reasoning that isolation means nothing
        # about a thread body's correctness could depend on program
        # position -- true for VARIABLE visibility, but wrong for
        # struct FIELD data specifically: `structs[name]` only holds
        # the empty name-pre-registration placeholder until that
        # struct's own analyze_struct call fills in real field types,
        # which (like everything else) happens during the normal third
        # pass, in order. A thread analyzed BEFORE that point would see
        # every referenced struct as fieldless -- confirmed directly
        # (an `on message(p:SomeStruct) { log(p.field) }` failed with
        # "struct 'SomeStruct' has no field 'field'" even though the
        # struct itself was already fully, correctly declared, just
        # too late for the old early pass to have seen it). Ordinary
        # third-pass timing is what every other declaration kind here
        # already gets, so this is not a new limitation for threads --
        # it exactly matches the SAME "declared before referenced"
        # requirement an ordinary global variable already has from an
        # earlier-declared function's own body (confirmed directly:
        # unrelated to threads at all, `void func f(){log(x)} int x=1`
        # already fails today with "unknown variable 'x'", since only a
        # function's SIGNATURE is hoisted, never its body's own
        # variable references). `NAME.postMessage(x)`/`.onMessage(...)`/
        # `.kill()`/`.live(...)`/`.isAlive()` calls elsewhere in the
        # program follow the identical rule: written after this
        # thread's own declaration, exactly as every example in
        # claude.md #195's own request already does. The one thing
        # that still genuinely needs the WHOLE program first -- has
        # SOME `NAME.onMessage(...)` registration happened by the very
        # end, anywhere -- stays a separate, end-of-analyze() check
        # (the "no dead sends" loop there), since that's a real "did
        # this ever happen ANYWHERE" question a single textual position
        # can't answer.
        global_scope.define(decl.name, Symbol(decl.name, types_mod.ThreadType(decl.name), "thread", decl),
                             decl, filename)
        info = _ThreadInfo(decl)
        threads[decl.name] = info
        thread_state_scope = Scope(functions_scope)
        seen_handlers = set()
        # claude.md #199 Phase 5: `DatabaseURL = '<literal>'` may be
        # this thread's own first statement -- checked ONCE, over the
        # WHOLE body, rather than only at index 0, so a misplaced one
        # gets this clear positional error instead of falling through
        # to the generic "may only contain state declarations and
        # on ... handlers" rejection below (which would be true, but
        # not point at the actual fix). A thread whose body doesn't
        # start with one skips this block entirely -- info.database_url
        # stays None, its default.
        thread_body = decl.body.body
        for i, stmt in enumerate(thread_body):
            if not _is_thread_database_url_stmt(stmt):
                continue
            if decl.pool_size is not None:
                # claude.md #215: a `thread NAME[N] { ... }` pool
                # shares ONE `_ThreadInfo` (and therefore one
                # `database_url`) across every instance -- the body is
                # textually identical, so a `DatabaseURL = '<literal>'`
                # here would be the SAME literal path for all N of
                # them. Each instance still gets its OWN sqlite3*
                # handle (festina_db_open runs once per instance, in
                # that instance's own on_load), so N instances would
                # mean N independent, uncoordinated connections into
                # the identical file, at the same time, from N real OS
                # threads -- not the "never shared" isolation
                # `DatabaseURL` gives an ordinary singleton thread at
                # all. Rejected outright, the same "don't allow a
                # construction that's a genuine hazard, not just an
                # unusual one" call the whole-program DatabaseURL
                # conflict check below already makes for two ordinary
                # threads naming the same file.
                raise CompileError(
                    f"thread pool '{decl.name}[{decl.pool_size}]' cannot declare "
                    f"its own DatabaseURL -- every instance in the pool would open "
                    f"its own independent connection to the SAME literal file "
                    f"concurrently, with no coordination between them (unlike an "
                    f"ordinary singleton thread's own DatabaseURL, which is always "
                    f"private to that one thread). Give each instance a genuinely "
                    f"distinct database of its own with an ordinary (non-pool) "
                    f"thread declared per instance instead, or have pool workers "
                    f"message a single dedicated database thread rather than "
                    f"querying sqlite directly.",
                    file=filename, line=stmt.expr.line, column=stmt.expr.column,
                    category="invalid declaration",
                )
            if i != 0:
                raise CompileError(
                    f"thread '{decl.name}': DatabaseURL = ... must be the "
                    f"first statement in the thread's own body",
                    file=filename, line=stmt.expr.line, column=stmt.expr.column,
                    category="invalid syntax",
                )
            value_expr = stmt.expr.value
            if not isinstance(value_expr, ast.StringLit):
                raise CompileError(
                    f"thread '{decl.name}': DatabaseURL must be a plain string "
                    f"literal (e.g. DatabaseURL = './{decl.name}.sqlite') -- "
                    f"unlike the main program's own DatabaseURL, a thread's "
                    f"own copy can't be a computed expression, since the "
                    f"whole-program file-conflict check (claude.md #199) has "
                    f"to be able to prove it's distinct from every other "
                    f"context's own database file at compile time",
                    file=filename, line=getattr(value_expr, "line", stmt.expr.line),
                    column=getattr(value_expr, "column", stmt.expr.column),
                    category="invalid assignment",
                )
            info.database_url = value_expr.value
            # claude.md #199 Phase 5: the ASSIGN node (`stmt.expr`), not
            # the bare StringLit `value_expr` -- StringLit (ast.py)
            # carries no line/column of its own at all (only its
            # `.value`), so a later error pointing at
            # info.database_url_node (the whole-program conflict check,
            # below) would silently fall back past a nonexistent
            # attribute to a much less precise location. Assign always
            # carries a real line/column from parsing.
            info.database_url_node = stmt.expr
            thread_body = thread_body[1:]
            break
        # claude.md #212: has_http_handler is hoisted -- a single scan
        # for any of the four HTTP-shaped handler names, BEFORE the
        # main per-statement loop below runs -- rather than set inline
        # as each is encountered in textual order. Unlike the bare-
        # postMessage-needs-a-textually-earlier-top-level-`on message`
        # rule (a real whole-program ordering constraint), there is no
        # reason to force `on request`/etc to appear before `on
        # load()` just because openPort() happens to be called from
        # inside it -- the whole thread body is known in full before
        # any of it runs, so gating on textual order here would be a
        # needless, surprising restriction on ordinary code (writing
        # `on load()` first, `on request(...)` below it, is the most
        # natural order to write this in).
        info.declared_http_handlers = {
            stmt.name for stmt in thread_body
            if isinstance(stmt, ast.EventHandler) and stmt.name in _THREAD_HTTP_HANDLER_NAMES
        }
        info.has_http_handler = bool(info.declared_http_handlers)
        # claude.md #210: thread-private helper functions -- a `func`
        # declared directly in a thread's own body (a sibling of its
        # state vars/on load/on message/on exit, not nested inside one
        # of THOSE), callable only from this one thread's own handlers
        # and other private funcs, with read/write access to this
        # thread's own state. `thread_functions_scope` is this
        # thread's OWN, separate function-name namespace -- parented
        # on `thread_state_scope` (so a private func's own body sees
        # thread state directly, the same "closes over this thread's
        # own state" a handler already gets), never on the top-level
        # `functions_scope` (which would leak every private func into
        # every OTHER thread, and into main). Two-pass, the identical
        # "hoist every signature before any call checks" shape
        # `register_func_signature`'s own whole-program pre-pass
        # already uses -- but deliberately NOT `_iter_func_decls`
        # itself, since that recurses into the GLOBAL `functions_scope`
        # and would defeat the whole point of a SEPARATE, private one.
        thread_functions_scope = Scope(thread_state_scope)
        for stmt in thread_body:
            if not isinstance(stmt, ast.FuncDecl):
                continue
            if stmt.name in BUILTIN_FUNCTIONS:
                raise CompileError(
                    f"'{stmt.name}' is a builtin function name and cannot be used to "
                    f"declare a function -- a function declared with this name would be "
                    f"permanently unreachable, since every call to '{stmt.name}(...)' "
                    f"resolves to the builtin instead",
                    file=filename, line=stmt.line, column=stmt.column,
                    category="duplicate declaration",
                )
            if stmt.name in thread_functions_scope.vars:
                raise CompileError(
                    f"thread '{decl.name}' already declares a function named "
                    f"'{stmt.name}'",
                    file=filename, line=stmt.line, column=stmt.column,
                    category="duplicate declaration",
                )
            func_return_type = resolve(stmt.return_type, stmt) if stmt.return_type != "void" else None
            thread_functions_scope.vars[stmt.name] = Symbol(
                stmt.name, func_return_type, "thread_function", stmt)
        _current_thread[0] = info
        try:
            for stmt in thread_body:
                if isinstance(stmt, ast.VarDecl):
                    analyze_var_decl(stmt, thread_state_scope, False)
                    continue
                if isinstance(stmt, ast.EventHandler):
                    if stmt.name not in _THREAD_EVENT_SIGNATURES:
                        raise CompileError(
                            f"'on {stmt.name}' is not a thread event -- a thread body "
                            f"may only declare on load()/on message(worker:thread, msg:T)/"
                            f"on exit(code:int), plus (claude.md #212) on request(req:http)/"
                            f"on upgrade(s:socket)/on socketMessage(s:socket, msg:blob)/"
                            f"on socketClose(s:socket)",
                            file=filename, line=stmt.line, column=stmt.column,
                            category="invalid declaration",
                        )
                    if stmt.name in seen_handlers:
                        raise CompileError(
                            f"thread '{decl.name}' already declares 'on {stmt.name}'",
                            file=filename, line=stmt.line, column=stmt.column,
                            category="duplicate declaration",
                        )
                    seen_handlers.add(stmt.name)
                    # claude.md #212: info.has_http_handler was already
                    # set by the hoisting pre-scan just above, before
                    # this loop started -- nothing to do here.
                    sig, help_text = _THREAD_EVENT_SIGNATURES[stmt.name]
                    if stmt.name == "message":
                        info.inbound_type = _check_message_handler_params(
                            stmt.params, stmt, filename, structs, tables, enums, help_text)
                    else:
                        param_types = tuple(apply_manually_managed(resolve(p.type_expr, stmt), p.manually_managed)
                                             for p in stmt.params)
                        if param_types != sig:
                            raise CompileError(
                                f"on {stmt.name}(...) must declare exactly {help_text}",
                                file=filename, line=stmt.line, column=stmt.column,
                                category="invalid function argument type",
                            )
                    # claude.md #210: parented on thread_functions_scope
                    # (not thread_state_scope directly), so a handler
                    # can call this thread's own private funcs -- which
                    # transitively still sees thread_state_scope/
                    # functions_scope too, exactly as before.
                    handler_scope = Scope(thread_functions_scope)
                    for p in stmt.params:
                        ptype = apply_manually_managed(resolve(p.type_expr, stmt), p.manually_managed)
                        handler_scope.define(p.name, Symbol(p.name, ptype, "parameter"),
                                              stmt, filename)
                    analyze_block(stmt.body, handler_scope, return_type=None)
                    continue
                if isinstance(stmt, ast.FuncDecl):
                    # claude.md #210: the BODY half -- the signature was
                    # already hoisted into thread_functions_scope above,
                    # the same "hoist first, analyze bodies at their own
                    # textual position" split register_func_signature/
                    # analyze_func already use at the top level. Parented
                    # on thread_functions_scope, not thread_state_scope
                    # directly, so one private func can call ANOTHER
                    # (declared anywhere in this thread's body, thanks
                    # to hoisting -- order among private funcs doesn't
                    # matter, same as top-level).
                    func_sym = thread_functions_scope.vars[stmt.name]
                    func_scope = Scope(thread_functions_scope)
                    for p in stmt.params:
                        ptype = apply_manually_managed(resolve(p.type_expr, stmt), p.manually_managed)
                        func_scope.define(p.name, Symbol(p.name, ptype, "parameter"),
                                           stmt, filename)
                    analyze_block(stmt.body, func_scope, return_type=func_sym.type)
                    continue
                raise CompileError(
                    f"a thread's own body may only contain state declarations "
                    f"(e.g. 'map[text] state'), func declarations, and "
                    f"on load()/on message(p:T)/on exit(code:int) handlers",
                    file=filename, line=getattr(stmt, "line", decl.line),
                    column=getattr(stmt, "column", decl.column),
                    category="invalid declaration",
                )
        finally:
            _current_thread[0] = None

    def analyze_statement(stmt, scope, return_type, loop_depth=0):
        if isinstance(stmt, ast.ImportDecl):
            imports.append(stmt.path)
        elif isinstance(stmt, ast.StructDecl):
            analyze_struct(stmt)
        elif isinstance(stmt, ast.TableDecl):
            analyze_table(stmt)
        elif isinstance(stmt, ast.EnumDecl):
            analyze_enum(stmt)
        elif isinstance(stmt, ast.FuncDecl):
            analyze_func(stmt)
        elif isinstance(stmt, ast.EventHandler):
            analyze_event_handler(stmt)
        elif isinstance(stmt, ast.ThreadDecl):
            analyze_thread(stmt)
        elif isinstance(stmt, ast.VarDecl):
            analyze_var_decl(stmt, scope, scope is global_scope)
        elif isinstance(stmt, ast.IfStmt):
            cond_type = infer(stmt.test, scope)
            check_condition_bool(cond_type, stmt)
            # loop_depth passes through an if/else unchanged (not reset
            # to 0) -- claude.md #73's break/continue target the nearest
            # enclosing *loop*, and an if inside a loop body is still
            # inside that loop, not a boundary of its own the way a
            # function body is (see analyze_func, which never threads
            # loop_depth through at all -- a fresh call always starts at
            # the default 0).
            analyze_block(stmt.then, scope, return_type, loop_depth)
            if stmt.orelse is not None:
                if isinstance(stmt.orelse, ast.IfStmt):
                    analyze_statement(stmt.orelse, scope, return_type, loop_depth)
                else:
                    analyze_block(stmt.orelse, scope, return_type, loop_depth)
        elif isinstance(stmt, ast.WhileStmt):
            # claude.md #61: condition must be bool, no truthy/falsy
            # conversion -- same rule check_condition_bool already
            # enforces for if/ternary.
            cond_type = infer(stmt.test, scope)
            check_condition_bool(cond_type, stmt)
            analyze_block(stmt.body, scope, return_type, loop_depth + 1)
        elif isinstance(stmt, ast.ForStmt):
            # claude.md #60: "the initialization variable is scoped to
            # the loop body" -- a fresh scope holds just the loop
            # variable, and analyze_block below nests the body under
            # *that* (not the outer scope), so the variable is visible
            # in the condition/update/body but nowhere after the loop.
            loop_scope = Scope(scope)
            analyze_var_decl(stmt.init, loop_scope, is_global=False)
            cond_type = infer(stmt.test, loop_scope)
            check_condition_bool(cond_type, stmt)
            infer(stmt.update, loop_scope)
            analyze_block(stmt.body, loop_scope, return_type, loop_depth + 1)
        elif isinstance(stmt, ast.TryStmt):
            # claude.md #157: try_body and catch_body are each analyzed
            # in their own fresh child scope (analyze_block already
            # does this) -- catch_var is visible only inside catch_body,
            # never inside try_body or after the whole statement, the
            # same "scoped to exactly where it's declared" rule the for
            # loop's own init variable already follows. loop_depth
            # passes through unchanged, same reasoning as IfStmt just
            # above: try/catch is a branch, not a loop boundary of its
            # own, so break/continue inside it still target whatever
            # loop (if any) already encloses it.
            analyze_block(stmt.try_body, scope, return_type, loop_depth)
            catch_scope = Scope(scope)
            catch_scope.define(stmt.catch_var, Symbol(stmt.catch_var, _TEXT, "variable"),
                                stmt, filename)
            analyze_block(stmt.catch_body, catch_scope, return_type, loop_depth)
        elif isinstance(stmt, ast.ThrowStmt):
            # claude.md #157: any type is accepted and coerced to text
            # at codegen, exactly like log()/fail() (claude.md #35) --
            # no restriction here beyond "it's a valid expression".
            infer(stmt.expr, scope)
        elif isinstance(stmt, ast.FreeStmt):
            # claude.md #111: `free name`. Any declared variable of any
            # type -- releasing is type-dispatched in codegen, and for a
            # type with nothing to release (int, a borrowed query row)
            # freeing degenerates to nulling the binding, which is still
            # a coherent thing to ask for.
            sym = scope.lookup(stmt.name)
            if sym is None or sym.kind not in ("variable", "constant", "parameter"):
                raise CompileError(
                    f"free: unknown variable '{stmt.name}'",
                    file=filename, line=stmt.line, column=stmt.column,
                    category="unknown variable",
                )
            if sym.kind == "constant":
                raise CompileError(
                    f"cannot free the constant '{stmt.name}'",
                    file=filename, line=stmt.line, column=stmt.column,
                    category="invalid statement",
                )
            # claude.md #202: a manually-managed parameter is exempt --
            # it was never auto-retained on entry and never auto-
            # released at the callee's own scope exit (codegen's own
            # _emit_param_bindings gates both on `p.manually_managed`),
            # so it is NOT "borrowed" in the sense the check just below
            # means: nothing else is waiting to release it, on either
            # side, ever, unless something explicitly does. A thread's
            # own `on message(p:T?)` handler -- the whole point of
            # Phase 2's own reference-sharing design -- is the single
            # most common place that "something" naturally is.
            if sym.kind == "parameter" and not getattr(sym.type, "manually_managed", False):
                # A parameter is a BORROWED reference (claude.md #84) --
                # the caller's value, which the caller will release.
                raise CompileError(
                    f"cannot free the parameter '{stmt.name}' -- a parameter "
                    f"borrows its caller's value; free it in the caller",
                    file=filename, line=stmt.line, column=stmt.column,
                    category="invalid statement",
                )
        elif isinstance(stmt, ast.DeleteStmt):
            # claude.md #111: `delete m.key` / `delete m['key']` /
            # `delete s.field`. The target's OBJECT decides the meaning:
            # a map loses the entry, a struct/table field becomes null
            # (a row also clears its presence bit -- see undefined()).
            tgt = stmt.target
            obj_type = infer(tgt.obj, scope)
            if isinstance(obj_type, types_mod.MapType):
                if tgt.computed:
                    key_type = infer(tgt.prop, scope)
                    if (key_type is not None and key_type is not NULL
                            and key_type != _TEXT):
                        raise CompileError(
                            f"delete on a map takes a text key, found "
                            f"{types_mod.type_name(key_type)}",
                            file=filename, line=stmt.line, column=stmt.column,
                            category="invalid statement",
                        )
            elif isinstance(obj_type, types_mod.StructType):
                if tgt.computed:
                    raise CompileError(
                        "a struct field is deleted by name (delete s.field), "
                        "not by a computed key",
                        file=filename, line=stmt.line, column=stmt.column,
                        category="invalid statement",
                    )
                if tgt.prop not in structs.get(obj_type.name, {}):
                    raise CompileError(
                        f"struct '{obj_type.name}' has no field '{tgt.prop}'",
                        file=filename, line=stmt.line, column=stmt.column,
                        category="invalid field access",
                    )
            elif isinstance(obj_type, types_mod.TableType):
                if tgt.computed:
                    raise CompileError(
                        "a row field is deleted by name (delete row.field), "
                        "not by a computed key",
                        file=filename, line=stmt.line, column=stmt.column,
                        category="invalid statement",
                    )
                cols = tables.get(obj_type.name) or {}
                if tgt.prop not in cols:
                    raise CompileError(
                        f"table '{obj_type.name}' has no column '{tgt.prop}'",
                        file=filename, line=stmt.line, column=stmt.column,
                        category="invalid field access",
                    )
            elif obj_type is not None:
                raise CompileError(
                    f"delete works on a map key or a struct/row field, not on "
                    f"{types_mod.type_name(obj_type)}",
                    file=filename, line=stmt.line, column=stmt.column,
                    category="invalid statement",
                )
        elif isinstance(stmt, ast.BreakStmt):
            if loop_depth == 0:
                raise CompileError(
                    "'break' can only be used inside a for/while loop",
                    file=filename, line=stmt.line, column=stmt.column,
                    category="invalid statement",
                )
        elif isinstance(stmt, ast.ContinueStmt):
            if loop_depth == 0:
                raise CompileError(
                    "'continue' can only be used inside a for/while loop",
                    file=filename, line=stmt.line, column=stmt.column,
                    category="invalid statement",
                )
        elif isinstance(stmt, ast.Return):
            # claude.md #23: "A function that does not return a value
            # uses void" implies the converse too -- a void function
            # doesn't return one, and a non-void function does. Neither
            # direction used to be checked: `void func f() { return 5 }`
            # silently discarded the 5 (LLVM `ret void` regardless of
            # what expression was given -- the expression itself was
            # still evaluated, so a side-effecting return value's
            # effects still happened, just its result vanished), and
            # `int func f() { return }` (no value) fell through with
            # nothing checked at all in either branch below.
            if stmt.value is not None:
                actual = infer(stmt.value, scope)
                if return_type is None:
                    raise CompileError(
                        "cannot return a value from a void function",
                        file=filename, line=getattr(stmt, "line", 0), column=getattr(stmt, "column", 0),
                        category="invalid return type",
                    )
                check_assignable(return_type, actual, stmt, what="return value")
            elif return_type is not None:
                raise CompileError(
                    f"function must return a value of type {types_mod.type_name(return_type)}, "
                    f"not a bare 'return'",
                    file=filename, line=getattr(stmt, "line", 0), column=getattr(stmt, "column", 0),
                    category="invalid return type",
                )
        elif isinstance(stmt, ast.Block):
            analyze_block(stmt, scope, return_type, loop_depth)
        elif isinstance(stmt, ast.ExprStmt):
            infer(stmt.expr, scope)
            # claude.md #217: a BARE postMessage(x)/NAME.postMessage(x)
            # statement -- not wrapped in `.callback(...)` (that shape
            # is `Call(Member(_, "callback"), ...)` at the statement's
            # own top level, never reaching here with a postMessage
            # callee) -- is a compile error once its target has a
            # reply_type, since nothing would ever be able to receive
            # the reply it might send. This and the `.callback()`
            # combined-pattern branch in _infer_call together cover the
            # only two syntactically valid positions for a postMessage
            # call.
            if isinstance(stmt.expr, ast.Call) and _is_postmessage_callee(stmt.expr.callee):
                reply_type, target_desc = _postmessage_target_reply_type(stmt.expr)
                if reply_type is not None:
                    raise CompileError(
                        f"{target_desc} replies to messages -- "
                        f"'.postMessage(...)' here must chain '.callback(fn)' "
                        f"(fn:func[{types_mod.type_name(reply_type)}]:void) to "
                        f"receive it, or use a target that never replies",
                        file=filename, line=getattr(stmt.expr, "line", 0),
                        column=getattr(stmt.expr, "column", 0),
                        category="invalid declaration",
                    )
        # unrecognized statement kinds are ignored (no-op)

    def analyze_block(block, parent_scope, return_type, loop_depth=0):
        scope = Scope(parent_scope)
        for stmt in block.body:
            analyze_statement(stmt, scope, return_type, loop_depth)

    # claude.md #220: `thread NAME[] { ... }` -- empty brackets, no
    # literal N -- resolves its own pool size HERE, before anything
    # else in this function ever reads a ThreadDecl's `pool_size`. The
    # parser leaves the sentinel string `"auto"` in place (see
    # parse_thread_decl's own comment); this pass mutates it in place
    # into a real positive int, so every later reader -- the rest of
    # this file, all of codegen.py -- never has to know "auto" existed.
    #
    # The rule: os.cpu_count() (the machine COMPILING the program, not
    # necessarily the one that later runs it -- see claude.md #220's
    # own entry for why compile time was chosen over run time) minus
    # every OTHER thread the program declares, floored at 1. "Every
    # other thread" counts an ordinary singleton as 1 and an explicit
    # `NAME[N]` pool as N; another `NAME[]` auto pool is deliberately
    # NOT counted here (each auto pool sizes itself independently
    # against the same fixed total, rather than trying to solve a
    # system of equations between them -- simple and order-independent,
    # at the cost of two auto pools on the same machine each getting
    # the full remaining budget rather than splitting it).
    _fixed_thread_total = sum(
        1 if stmt.pool_size is None else stmt.pool_size
        for stmt in program.body
        if isinstance(stmt, ast.ThreadDecl) and stmt.pool_size != "auto"
    )
    _cpu_count = os.cpu_count() or 1
    for stmt in program.body:
        if isinstance(stmt, ast.ThreadDecl) and stmt.pool_size == "auto":
            stmt.pool_size = max(1, _cpu_count - _fixed_thread_total)

    # claude.md #106: every struct and table NAME is registered before
    # any of their fields resolve, so declaration order stops mattering.
    # `struct Outer { inner:Inner }` written above `struct Inner { ... }`
    # used to fail with "unknown type 'Inner'" -- an ordering rule
    # wearing a typo's error message, and a genuinely surprising one in
    # a language with no forward declarations to write instead. The
    # per-declaration registration in analyze_struct/analyze_table stays
    # as it is: this pre-pass only guarantees the name exists, and those
    # still fill in the real field types and still reject a duplicate.
    # claude.md #176: enum names get the identical treatment -- an
    # `enum Shape = Circle, Square` declared above `struct Circle`
    # resolves Circle/Square fine (they're pre-scanned too, in the same
    # loop), and a struct field/function signature naming `Shape`
    # before the `enum` line itself resolves fine too, symmetrically.
    for stmt in program.body:
        if isinstance(stmt, ast.StructDecl) and stmt.name not in structs:
            structs[stmt.name] = {}
        elif isinstance(stmt, ast.TableDecl) and stmt.name not in tables:
            tables[stmt.name] = {}
        elif isinstance(stmt, ast.EnumDecl) and stmt.name not in enums:
            enums[stmt.name] = None

    # claude.md #140: every function's NAME and SIGNATURE is registered
    # before any CALL resolves -- "hoisting", the same declaration-
    # order-independence claude.md #106 just gave struct/table names
    # above, extended to functions (and, since a nested FuncDecl is
    # already treated as an ordinary global declaration regardless of
    # nesting -- see analyze_func's own comment -- however deeply one is
    # nested inside blocks/loops/other functions; see _iter_func_decls).
    # filename is threaded the identical way the real analysis loop
    # right below threads it: reset right before each TOP-LEVEL
    # statement, since only top-level statements carry their own
    # `.file` (build_program) -- everything nested under one shares
    # that statement's file. This has to run as a fully separate pass
    # over the WHOLE program (not folded into the struct/table loop
    # just above) because a function's return/parameter types can
    # themselves name a struct or table, so every struct/table name
    # must already exist before register_func_signature's own resolve()
    # calls run.
    for stmt in program.body:
        filename = getattr(stmt, "file", filename)
        for func_decl in _iter_func_decls([stmt]):
            register_func_signature(func_decl)

    for stmt in program.body:
        # claude.md #6: a multi-file program (festina.imports.build_program)
        # is one merged ast.Program, but errors should still point at
        # whichever source file a statement actually came from. Every
        # nested function above closes over `filename` as a free
        # variable, resolved fresh on each call (Python's late-binding
        # closures) rather than captured once -- so reassigning it here,
        # right before analyzing each top-level statement, is enough to
        # correctly thread the right filename through everything that
        # statement's analysis touches, however deeply nested, with no
        # changes needed to any individual `raise CompileError(...)` site.
        # A single-file program tags every statement with that one file
        # (see build_program), so this is a no-op change of behavior for
        # today's single-file callers.
        filename = getattr(stmt, "file", filename)
        analyze_statement(stmt, global_scope, None)

    # claude.md #70: DatabaseURL = <expr> -- festina.imports.build_program
    # already validated *position* (first statement of the entry file,
    # before any other code or import) and pulled the value expression
    # out to program.database_url; this is the one thing left to check,
    # the same as any other value: it must actually be text. Uses
    # entry_filename (captured before the loop above reassigned
    # `filename` statement by statement) since this expression always
    # comes from the entry file specifically -- build_program never
    # recognizes it in an imported file.
    if getattr(program, "database_url", None) is not None:
        database_url_type = infer(program.database_url, global_scope)
        if database_url_type is not None and database_url_type is not NULL and database_url_type != _TEXT:
            raise CompileError(
                f"DatabaseURL must be text, found {types_mod.type_name(database_url_type)}",
                file=entry_filename, line=getattr(program.database_url, "line", 0),
                column=getattr(program.database_url, "column", 0),
                category="invalid assignment",
            )

    # claude.md #208: the old "no dead sends" whole-program check
    # (a thread that posts but nothing ever registers .onMessage(...)
    # for it) is gone along with .onMessage(...) itself -- bare
    # postMessage(x) now checks `_main_message_type[0]` directly, at
    # its own call site, the moment it's compiled (see _infer_call),
    # the same "declared before referenced" program-order rule every
    # other name in this language already has, needing no separate
    # end-of-program pass.

    # claude.md #199 Phase 5: a thread's own private database and the
    # main program's are each just a path on disk -- nothing stops one
    # from silently choosing the SAME file another context already
    # uses, accidentally sharing the one thing per-thread isolation is
    # supposed to keep private. Checked once, over the whole program,
    # the same "can't check until everything's been walked" timing as
    # the "no dead sends" check just above. Only a LITERAL path can be
    # compared this way -- main's own DatabaseURL may be an arbitrary
    # text expression (environment.NAME, a template, ...), and there's
    # no way to prove a computed value is (or isn't) equal to anything
    # else at compile time, so a non-literal main DatabaseURL skips
    # this check entirely rather than guessing; a thread's own
    # DatabaseURL is always a literal already (enforced above, in
    # analyze_thread), so every thread that declared one always
    # participates. Comparison is a plain string match on the resolved
    # path text, not a filesystem-level "same file" check (no
    # `os.path.realpath`/symlink resolution, no normalizing `./x` vs
    # `x`) -- simple and exactly matches the literal text a reader of
    # both DatabaseURL lines would compare by eye.
    db_contexts = []  # [(label, resolved_path, file, line, column)]
    main_database_url = getattr(program, "database_url", None)
    if main_database_url is None:
        db_contexts.append(("the main program", "festina.sqlite", entry_filename, 0, 0))
    elif isinstance(main_database_url, ast.StringLit):
        db_contexts.append((
            "the main program", main_database_url.value, entry_filename,
            getattr(main_database_url, "line", 0),
            getattr(main_database_url, "column", 0),
        ))
    for name, info in threads.items():
        if info.database_url is not None:
            db_contexts.append((
                f"thread '{name}'", info.database_url,
                getattr(info.node, "file", filename),
                getattr(info.database_url_node, "line", info.node.line),
                getattr(info.database_url_node, "column", info.node.column),
            ))
    for i in range(len(db_contexts)):
        for j in range(i + 1, len(db_contexts)):
            label_a, path_a, _file_a, _line_a, _col_a = db_contexts[i]
            label_b, path_b, file_b, line_b, col_b = db_contexts[j]
            if path_a == path_b:
                raise CompileError(
                    f"{label_a} and {label_b} would both open the same "
                    f"database file ('{path_a}') -- every thread with its "
                    f"own DatabaseURL (and the main program) must use a "
                    f"genuinely distinct file, per claude.md #195's own "
                    f"per-thread isolation guarantee",
                    file=file_b, line=line_b, column=col_b,
                    category="invalid declaration",
                )

    return AnalyzedProgram(global_scope.vars, structs, tables, enums, imports, threads,
                            main_message_type=_main_message_type[0],
                            main_reply_type=_main_reply_type[0])
