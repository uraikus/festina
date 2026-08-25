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
import math

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
    # claude.md #94: single-value queries, so a scalar result needs no
    # throwaway `table` declaration (which would create a real table).
    "sqliteInt", "sqliteFloat", "sqliteText",
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
    # claude.md #94
    "sqliteInt": types_mod.PrimitiveType("int"),
    "sqliteFloat": types_mod.PrimitiveType("float"),
    "sqliteText": types_mod.PrimitiveType("text"),
    # claude.md #132
    "mkdir": types_mod.PrimitiveType("bool"),
    "ls": types_mod.ArrayType(types_mod.PrimitiveType("text")),
    # claude.md #146
    "isAudioPlayerPlaying": types_mod.PrimitiveType("bool"),
    # claude.md #150
    "exec": types_mod.PrimitiveType("int"),
}

# claude.md #55: int and float never mix directly in a binary operator.
_INT = types_mod.PrimitiveType("int")
_FLOAT = types_mod.PrimitiveType("float")
_NUMERIC_TYPES = (_INT, _FLOAT)
_TEXT = types_mod.PrimitiveType("text")
_BLOB = types_mod.PrimitiveType("blob")
_BOOL = types_mod.PrimitiveType("bool")

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
    "drawCircle": (_INT, _INT, _INT),
    "drawText": (_TEXT, _INT, _INT),
    "drawImage": (types_mod.ImageType(), _INT, _INT),
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
    # claude.md #150: exec(args) -- args[0] the program to run
    # (PATH-searched), the rest its own argv.
    "exec": (types_mod.ArrayType(_TEXT),),
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

# claude.md #33/#94: every builtin taking (sql, [params]) -- the bound
# parameter list is a literal array that is explicitly allowed to mix
# types, so all of them need the same carve-out from the ordinary
# same-element-type array rule.
_SQLITE_BUILTINS = frozenset({"sqlite", "sqliteInt", "sqliteFloat", "sqliteText"})

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
    "drawRect": [(_INT, _INT, _INT, _INT), (_INT, _INT, _INT, _INT, _COLOR)],
    "drawPixel": [(_INT, _INT), (_INT, _INT, _COLOR)],
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
# Every name reachable as Math.<name>(...), for the "is this a Math
# call at all" test; the three sets above decide arity and result type.
MATH_FUNCTIONS = (MATH_ROUNDING_FUNCTIONS | MATH_FLOAT_FUNCTIONS
                  | MATH_FLOAT2_FUNCTIONS | {"random"})
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
    "mouseDown": ((_INT, _INT), "(x:int, y:int)"),
    "mouseUp": ((_INT, _INT), "(x:int, y:int)"),
    "mouse": ((_INT, _INT), "(x:int, y:int)"),
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
    # WebSocket handshake for that same connection; `on message` fires
    # once per complete WebSocket frame received; `on socketClose`
    # fires once, whenever an upgraded connection ends (the peer
    # closed it, sent a close frame, or the read failed) -- exactly
    # once per connection that ever reached `on upgrade`, never for a
    # plain HTTP connection that never upgraded.
    "request": ((_HTTP,), "(req:http)"),
    "upgrade": ((_SOCKET,), "(s:socket)"),
    "message": ((_SOCKET, _BLOB), "(s:socket, msg:blob)"),
    "socketClose": ((_SOCKET,), "(s:socket)"),
}

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


class AnalyzedProgram:
    def __init__(self, symbols, structs, tables, imports):
        self.symbols = symbols
        self.structs = structs
        self.tables = tables
        self.imports = imports


def resolve_type_name(type_expr, structs, tables, filename="<string>", node=None):
    if isinstance(type_expr, ast.ArrayTypeExpr):
        return types_mod.ArrayType(resolve_type_name(type_expr.element, structs, tables, filename, node),
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
            resolve_type_name(p, structs, tables, filename, node)
            for p in type_expr.param_types
        )
        return_type = (None if type_expr.return_type == "void"
                        else resolve_type_name(type_expr.return_type, structs, tables, filename, node))
        return types_mod.FuncType(param_types, return_type)
    if isinstance(type_expr, ast.MapTypeExpr):
        value_type = resolve_type_name(type_expr.value, structs, tables, filename, node)
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
        return types_mod.MapType(value_type, amortized=type_expr.amortized)
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
    if name == "color":
        return types_mod.ColorType()
    if name == "font":
        return types_mod.FontType()
    if name in structs:
        return types_mod.StructType(name)
    if name in tables:
        return types_mod.TableType(name)
    raise CompileError(
        f"unknown type '{name}'",
        file=filename, line=getattr(node, "line", 0), column=getattr(node, "column", 0),
        category="unknown type",
    )


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
    structs = {}
    tables = {}
    imports = []
    entry_filename = filename  # see the DatabaseURL check at the bottom
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
        return resolve_type_name(type_expr, structs, tables, filename, node)

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
        if declared == _BLOB and actual == _TEXT:
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
            param_types = tuple(resolve(p.type_expr, expr) for p in expr.params)
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
                param_types = tuple(resolve(p.type_expr, decl) for p in decl.params)
                ret_type = resolve(decl.return_type, decl) if decl.return_type != "void" else None
                return types_mod.FuncType(param_types, ret_type)
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
            # claude.md #39/#139: clientWidth/clientHeight/screenWidth/
            # screenHeight are read-only too -- same reasoning and same
            # "catch it before the generic target_type/value_type check
            # below" placement as .length above, since that check alone
            # has no way to tell a read from a write target.
            if isinstance(expr.target, ast.Identifier) and expr.target.name in _SIZE_GLOBALS:
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
            infer(expr.alt, scope)
            return cons_type
        if isinstance(expr, ast.LogicalOp):
            infer(expr.left, scope)
            infer(expr.right, scope)
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
        if isinstance(obj_type, types_mod.TableType):
            # claude.md #34: a query against a declared table produces
            # arr[TableType(name)] -- field access on a row (e.g.
            # `people[0].name`) resolves against that table's declared
            # columns, same as a struct field except `tables` stores raw
            # type-expr strings rather than already-resolved Type objects
            # (see analyze_table above), so each lookup resolves on demand.
            columns = tables.get(obj_type.name, {})
            if expr.prop not in columns:
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

    def _infer_call(expr, scope):
        callee = expr.callee
        if isinstance(callee, ast.Identifier):
            name = callee.name
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
                # rather than accepting any container shape. `.amortized`
                # is deliberately ignored here (an `amor map[text]`
                # fields argument works exactly the same way a plain one
                # does -- there's nothing about amortized growth that
                # matters once the map is just being rendered to JSON).
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
                    if arg_type is not None and arg_type is not NULL and arg_type != expected:
                        raise CompileError(
                            f"argument {i + 1} of '{name}' expects "
                            f"{types_mod.type_name(expected)}, found {types_mod.type_name(arg_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                return fn_type.return_type
            if sym is None or sym.kind != "function":
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
                param_type = resolve(param.type_expr, callee)
                if arg_type is not None and arg_type is not NULL and arg_type != param_type:
                    raise CompileError(
                        f"argument '{param.name}' of '{name}' expects "
                        f"{types_mod.type_name(param_type)}, found {types_mod.type_name(arg_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
            return sym.type
        if isinstance(callee, ast.Member) and not callee.computed:
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
                    expr.args[0].type_expr, structs, tables, filename, expr.args[0])
                # claude.md #159 v1 SCOPE CUT (api.md/todo.md document
                # it too): only int/float/bool/text are supported as a
                # target struct's own field types or toArr()'s own
                # element type -- nested struct/arr[T]/map[T] aren't
                # parseable yet. Rejected here, at compile time, with a
                # clear message naming exactly what's unsupported --
                # never silently ignored or left null.
                _JSON_SCALAR_TYPES = (_INT, _FLOAT, _BOOL, _TEXT)
                if callee.prop == "toStruct":
                    if not isinstance(target_type, types_mod.StructType):
                        raise CompileError(
                            f"toStruct()'s argument must be a struct name, found "
                            f"{types_mod.type_name(target_type)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    for fname, ftype in structs.get(target_type.name, {}).items():
                        if ftype not in _JSON_SCALAR_TYPES:
                            raise CompileError(
                                f"toStruct({target_type.name}) doesn't support field "
                                f"'{fname}' of type {types_mod.type_name(ftype)} yet -- "
                                f"only int/float/bool/text fields are supported",
                                file=filename, line=callee.line, column=callee.column,
                                category="invalid function argument type",
                            )
                    return target_type
                else:  # toArr
                    if target_type not in _JSON_SCALAR_TYPES:
                        raise CompileError(
                            f"toArr()'s element type must be int/float/bool/text, "
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
            if callee.prop == "test" and infer(callee.obj, scope) == _REGEX:
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
            if callee.prop in ("play", "playLoop") and infer(callee.obj, scope) == _AUDIO:
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
                if (obj_type == _BLOB
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
            # blob ONLY for now -- img/aud were asked for too, but both
            # already call festina_fail() (a hard process exit, not
            # blob's own graceful "empty on failure" contract) on a
            # corrupt/unreadable file, which a background WORKER thread
            # calling exit() concurrently with the main thread is a
            # real, unverified risk this pass doesn't ship; img
            # additionally needs its decoded cairo_surface_t built on
            # that same worker thread, a Cairo thread-safety question
            # not yet confirmed either. Both are a natural, likely
            # follow-up, not ruled out -- just not done here. See
            # claude.md #165's own account.
            if callee.prop == "callback" and infer(callee.obj, scope) == _TEXT:
                if len(expr.args) != 1:
                    raise CompileError(
                        f"callback() expects exactly 1 argument (the func to "
                        f"call once the background load finishes), got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                fn_type = infer(expr.args[0], scope)
                if (isinstance(fn_type, types_mod.FuncType) and len(fn_type.param_types) == 1
                        and fn_type.return_type is None
                        and fn_type.param_types[0] in (_IMAGE, _AUDIO)):
                    raise CompileError(
                        f"callback() only supports func[blob]:void for now -- "
                        f"img/aud background loading isn't implemented yet",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                if (not isinstance(fn_type, types_mod.FuncType)
                        or len(fn_type.param_types) != 1
                        or fn_type.return_type is not None
                        or fn_type.param_types[0] != _BLOB):
                    raise CompileError(
                        f"callback() expects func[blob]:void, found "
                        f"{types_mod.type_name(fn_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return _BLOB
            # claude.md #109: blob's five methods -- the file functions
            # claude.md #93 spelled as free functions taking a path,
            # moved onto the value that already knows the path. Checked
            # by name here, like every other method on a non-struct
            # receiver, so arity and argument types are enforced rather
            # than left to the generic member fallback.
            if callee.prop in _BLOB_METHODS and infer(callee.obj, scope) == _BLOB:
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
            if callee.prop in _HTTP_METHODS and infer(callee.obj, scope) == _HTTP:
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
            if callee.prop in _SOCKET_METHODS and infer(callee.obj, scope) == _SOCKET:
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
            if callee.prop == "send" and (callee_obj_is_http_lit or infer(callee.obj, scope) == _HTTP):
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
            if callee.prop == "send" and infer(callee.obj, scope) == _SOCKET:
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
            if callee.prop == "stop" and infer(callee.obj, scope) == _AUDIO:
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
                               "indexOf"):
                obj_type = infer(callee.obj, scope)
                if isinstance(obj_type, types_mod.ArrayType):
                    elem = obj_type.element
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
            if callee.prop in ("clip", "resize") and infer(callee.obj, scope) == _IMAGE:
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
            # claude.md #134: drawRect/drawPixel/drawCircle/drawText as
            # methods on img -- the same four canvas-level drawing
            # builtins claude.md #37/#39/#133 already give, now also
            # callable on an image's OWN surface instead of the canvas.
            # drawRect/drawPixel keep their own optional trailing
            # `color` (claude.md #133); coordinates are always in the
            # image's own pixel space, with no window/canvas needed at
            # all -- see codegen.py's _emit_image_draw_method.
            if (callee.prop in ("drawRect", "drawPixel", "drawCircle", "drawText")
                    and infer(callee.obj, scope) == _IMAGE):
                alternates = {
                    "drawRect": [(_INT, _INT, _INT, _INT), (_INT, _INT, _INT, _INT, _COLOR)],
                    "drawPixel": [(_INT, _INT), (_INT, _INT, _COLOR)],
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
                    sig = {"drawCircle": (_INT, _INT, _INT),
                           "drawText": (_TEXT, _INT, _INT)}[callee.prop]
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
            if callee.prop == "isPlaying" and infer(callee.obj, scope) == _AUDIO:
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
                if arg_type is not None and arg_type is not NULL and arg_type != expected:
                    raise CompileError(
                        f"argument {i + 1} expects {types_mod.type_name(expected)}, "
                        f"found {types_mod.type_name(arg_type)}",
                        file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                        category="invalid function argument type",
                    )
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

    def analyze_var_decl(decl, scope, is_global):
        declared_type = resolve(decl.type_expr, decl)
        # claude.md #156: `amor map[T]` (local or global) requires an
        # initializer -- unlike plain map[T]/arr[T], which start
        # "empty" via a real, immortal, zero-entry static header
        # (see codegen.py's _global_var_defs), an amortized map local's
        # own with-no-initializer path was deliberately never given
        # the equivalent (codegen's own scope boundary: it always
        # heap-allocates through the generic path instead, which needs
        # a real value to store, not an implicit empty default) --
        # requiring one here is what keeps that boundary from ever
        # being reached as an uninitialized-pointer bug instead of a
        # clear compile error. Struct fields have no initializer
        # syntax at all, so this can't (and doesn't need to) apply to
        # them -- they rely on auto-vivify instead (see codegen.py's
        # own comment on that path).
        if (isinstance(declared_type, types_mod.MapType) and declared_type.amortized
                and decl.init is None):
            raise CompileError(
                f"'{decl.name}' (amor map[{types_mod.type_name(declared_type.value)}]) "
                f"requires an initializer -- write e.g. `amor map[{types_mod.type_name(declared_type.value)}] "
                f"{decl.name} = {{}}` for an empty one",
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
            elif (isinstance(declared_type, types_mod.MapType) and declared_type.amortized
                    and isinstance(decl.init, ast.MapLit)):
                # claude.md #156: same bypass shape as the arr[img] case
                # just above, for the identical reason -- MapLit's own
                # generic inference (_infer_member's MapLit branch)
                # always returns a NON-amortized MapType regardless of
                # context (it has no way to know a declaration wants
                # `amor`), so the generic infer()+check_assignable()
                # path below would always reject a `{...}` literal
                # against an `amor map[T]` declared type. Validates
                # each entry directly against the declared value type
                # instead -- the same per-entry key/value checks the
                # generic inference already does (claude.md #72's key-
                # must-be-text rule, claude.md #154's mixed-value-type
                # rule), just checked against a known target type
                # rather than inferred from the literal's own entries.
                value_type_name = types_mod.type_name(declared_type.value)
                for key_expr, val_expr in decl.init.entries:
                    key_type = infer(key_expr, scope)
                    if key_type is not None and key_type is not NULL and key_type != _TEXT:
                        raise CompileError(
                            f"map key must be text, found {types_mod.type_name(key_type)}",
                            file=filename, line=getattr(key_expr, "line", 0),
                            column=getattr(key_expr, "column", 0),
                            category="invalid operand type",
                        )
                    val_type = infer(val_expr, scope)
                    if (val_type is not None and val_type is not NULL
                            and val_type != declared_type.value):
                        raise CompileError(
                            f"map literal value expects {value_type_name}, "
                            f"found {types_mod.type_name(val_type)}",
                            file=filename, line=getattr(val_expr, "line", 0),
                            column=getattr(val_expr, "column", 0),
                            category="invalid operand type",
                        )
            elif (isinstance(declared_type, types_mod.HttpType)
                    and (isinstance(decl.init, ast.MapLit)
                         or _http_send_lit_receiver(decl.init) is not None)):
                # claude.md #162: same bypass shape as the amor-map
                # case just above, for the identical reason -- MapLit's
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
            func_scope.define(p.name, Symbol(p.name, resolve(p.type_expr, decl), "parameter"), decl, filename)
        analyze_block(decl.body, func_scope, return_type=return_type)

    def analyze_event_handler(decl):
        # claude.md #40: see _EVENT_SIGNATURES above -- click/mouse/key/
        # resize/close get a fixed-signature check; any other event name
        # is unconstrained (and simply never fires -- there's no event
        # source claude.md defines for it).
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
            handler_scope.define(p.name, Symbol(p.name, resolve(p.type_expr, decl), "parameter"), decl, filename)
        analyze_block(decl.body, handler_scope, return_type=None)

    def analyze_statement(stmt, scope, return_type, loop_depth=0):
        if isinstance(stmt, ast.ImportDecl):
            imports.append(stmt.path)
        elif isinstance(stmt, ast.StructDecl):
            analyze_struct(stmt)
        elif isinstance(stmt, ast.TableDecl):
            analyze_table(stmt)
        elif isinstance(stmt, ast.FuncDecl):
            analyze_func(stmt)
        elif isinstance(stmt, ast.EventHandler):
            analyze_event_handler(stmt)
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
            if sym.kind == "parameter":
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
        # unrecognized statement kinds are ignored (no-op)

    def analyze_block(block, parent_scope, return_type, loop_depth=0):
        scope = Scope(parent_scope)
        for stmt in block.body:
            analyze_statement(stmt, scope, return_type, loop_depth)

    # claude.md #106: every struct and table NAME is registered before
    # any of their fields resolve, so declaration order stops mattering.
    # `struct Outer { inner:Inner }` written above `struct Inner { ... }`
    # used to fail with "unknown type 'Inner'" -- an ordering rule
    # wearing a typo's error message, and a genuinely surprising one in
    # a language with no forward declarations to write instead. The
    # per-declaration registration in analyze_struct/analyze_table stays
    # as it is: this pre-pass only guarantees the name exists, and those
    # still fill in the real field types and still reject a duplicate.
    for stmt in program.body:
        if isinstance(stmt, ast.StructDecl) and stmt.name not in structs:
            structs[stmt.name] = {}
        elif isinstance(stmt, ast.TableDecl) and stmt.name not in tables:
            tables[stmt.name] = {}

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

    return AnalyzedProgram(global_scope.vars, structs, tables, imports)
