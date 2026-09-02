"""claude.md #202: `T?` -- a trailing '?' after a type at a variable or
parameter declaration opts that one binding out of EVERY automatic
memory-management call site this compiler has (no retain on alias, no
release at scope exit, no retain/release on reassignment) while keeping
the identical heap+refcount-header representation `free`/`delete`
(claude.md #111) already rely on -- both become the ONLY release a
manually-managed value ever gets.

This file covers grammar/AST and semantic checks (parser.parse() +
semantic.analyze() only, no compile-and-run) plus a handful of real
compile-and-run checks for the representation/behavior itself. The
opposite-direction ASan/LeakSanitizer proof (a manually-managed value
that's never freed is a REAL, reported leak, not silently reclaimed)
lives in tests/test_leak_stress.py, alongside the ordinary "freed
correctly, no leak" per-type coverage -- both need the real sanitizer
toolchain this file's own fixtures don't set up.
"""
import os
import shutil

import pytest

_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_WAV_FIXTURE = os.path.join(_FIXTURES_DIR, "beep.wav")


class TestGrammar:
    """`T?` is consumed only at parse_var_decl/parse_typed_params,
    never inside parse_type() itself -- see ast.VarDecl's own doc
    comment for why that's what gives "no nesting" for free."""

    def test_scalar_manually_managed_var_decl_parses(self, parser):
        prog = parser.parse("void func f() { int? count = 1 }")
        decl = prog.body[0].body.body[0]
        assert decl.manually_managed is True
        assert decl.name == "count"

    def test_plain_var_decl_is_not_manually_managed(self, parser):
        prog = parser.parse("void func f() { int count = 1 }")
        decl = prog.body[0].body.body[0]
        assert decl.manually_managed is False

    def test_struct_manually_managed_var_decl_with_no_initializer_parses(self, parser):
        prog = parser.parse("struct Circle { x:int y:int }\nvoid func f() { Circle? c }")
        decl = prog.body[1].body.body[0]
        assert decl.manually_managed is True

    def test_struct_manually_managed_var_decl_followed_by_another_statement_parses(self, parser):
        # claude.md #202: a no-initializer manually-managed declaration
        # immediately followed by an unrelated statement -- the
        # "running out of the enclosing scope" case
        # _confirms_manually_managed_var_decl's own scan resolves via
        # reaching EOF/a closing brace, not an `=`/`:`.
        prog = parser.parse(
            "struct Circle { x:int y:int }\n"
            "void func f() { Circle? c\nlog(1) }"
        )
        body = prog.body[1].body.body
        assert len(body) == 2
        assert body[0].manually_managed is True

    @pytest.mark.parametrize("kw", ["blob", "img", "aud"])
    def test_manually_managed_media_type_parses(self, parser, kw):
        # claude.md #202: a real, found-and-fixed bug -- these three
        # keywords have their own pre-existing anonymous-callback
        # parse branch (claude.md #165/#171) that used to misroute
        # `blob? b = ...` into trying to parse a bare `?` as an
        # expression.
        prog = parser.parse(f"void func f() {{ {kw}? x }}")
        decl = prog.body[0].body.body[0]
        assert decl.manually_managed is True

    def test_manually_managed_media_anonymous_callback_form_still_parses(self, parser):
        # The pre-existing form this bug's own fix must not break.
        import festina.ast as ast
        prog = parser.parse(
            "void func onLoaded(b:blob) { log('loaded') }\n"
            "blob 'x.bin'.callback(onLoaded)\n"
        )
        assert isinstance(prog.body[1], ast.ExprStmt)

    def test_ternary_statement_starting_with_capitalized_identifier_still_parses_as_ternary(
            self, parser):
        # claude.md #202: the grammar ambiguity this feature's own `?`
        # consumption created -- `Flag ? a : b` (a bare ternary
        # expression statement) shares the identical `IDENT OP(?)
        # IDENT` token-shape prefix with `Circle? c` (a manually-
        # managed declaration).
        import festina.ast as ast
        prog = parser.parse(
            "bool Flag = true\n"
            "int a = 1\n"
            "int b = 2\n"
            "Flag ? log(a) : log(b)\n"
        )
        assert isinstance(prog.body[3], ast.ExprStmt)
        assert isinstance(prog.body[3].expr, ast.Ternary)

    def test_ternary_with_bare_identifier_branches_still_parses_as_ternary(self, parser):
        import festina.ast as ast
        prog = parser.parse(
            "bool Flag = true\n"
            "int y = 1\n"
            "int z = 2\n"
            "Flag ? y : z\n"
        )
        assert isinstance(prog.body[3], ast.ExprStmt)
        assert isinstance(prog.body[3].expr, ast.Ternary)

    def test_manually_managed_param_parses(self, parser):
        prog = parser.parse(
            "struct Circle { x:int y:int }\n"
            "void func f(c:Circle?) { }\n"
        )
        param = prog.body[1].params[0]
        assert param.manually_managed is True

    @pytest.mark.parametrize("bad_source", [
        "struct Circle { x:int y:int }\nvoid func f() { arr[Circle?] xs }",
        "struct Circle { x:int? y:int }",
        "struct Circle { x:int y:int }\nCircle? func make() { Circle? c\nreturn c }",
    ])
    def test_no_nesting_is_a_parse_error(self, parser, bad_source):
        # claude.md #202: no arr[T?], no T? struct field, no T? return
        # type -- all a consequence of '?' only ever being consumed at
        # parse_var_decl/parse_typed_params, never inside parse_type().
        with pytest.raises(Exception):
            parser.parse(bad_source)

    def test_const_manually_managed_is_a_parse_error(self, parser):
        # claude.md #202: deliberately unsupported this round -- a
        # const you could never mutate but also could never manually
        # release would be a permanent, unavoidable leak.
        with pytest.raises(Exception):
            parser.parse("void func f() { const int? x = 1 }")


class TestSemantic:
    def test_manually_managed_and_plain_struct_are_not_interchangeable(self, parser, semantic):
        source = """
        struct Circle { x:int y:int }
        void func takesCircle(c:Circle) { log(c.x) }
        void func f() {
            Circle? c
            takesCircle(c)
        }
        """
        with pytest.raises(Exception):
            semantic.analyze(parser.parse(source))

    def test_plain_struct_cannot_flow_into_manually_managed_param(self, parser, semantic):
        source = """
        struct Circle { x:int y:int }
        void func takesManaged(c:Circle?) { log(c.x) }
        void func f() {
            Circle c
            takesManaged(c)
        }
        """
        with pytest.raises(Exception):
            semantic.analyze(parser.parse(source))

    def test_manually_managed_struct_param_is_callable(self, parser, semantic):
        # claude.md #202: a real, found-and-fixed bug -- the ordinary
        # user-function call-argument check used to re-resolve the
        # callee's own parameter type WITHOUT re-applying `?`, making
        # any function with a manually-managed parameter permanently
        # uncallable.
        source = """
        struct Circle { x:int y:int }
        void func touch(c:Circle?) { log(c.x) }
        void func f() {
            Circle? c
            c.x = 1
            touch(c)
        }
        """
        semantic.analyze(parser.parse(source))

    @pytest.mark.parametrize("decl", [
        "int? a = 1",
        "float? f = 1.5",
        "bool? b = true",
        "text? t = 'hi'",
    ])
    def test_manually_managed_is_inert_on_scalars_and_text(self, parser, semantic, decl):
        semantic.analyze(parser.parse(f"void func f() {{ {decl} }}"))

    def test_manually_managed_blob_method_dispatch_still_works(self, parser, semantic):
        # claude.md #202: a real, found-and-fixed bug -- blob's own
        # `declared == _BLOB`/`infer(...) == _BLOB` checks (an exact
        # dataclass-equality comparison, since blob had no dedicated
        # dataclass of its own to `isinstance` against) silently
        # stopped recognizing a manually-managed blob as a blob at
        # all, for both the text -> blob coercion and every one of
        # blob's own methods.
        semantic.analyze(parser.parse(
            "void func f() { blob? b = 'x.bin'\nlog(b.toText()) }"
        ))

    def test_manually_managed_regex_test_dispatch_still_works(self, parser, semantic):
        source = """
        void func testIt(r:regex?) {
            log(r.test('abcdef'))
        }
        """
        semantic.analyze(parser.parse(source))

    @pytest.mark.parametrize("decl", [
        "regex? r = /abc/",
        "regex? r = regex('abc', '')",
        "arr[int]? xs = [1, 2, 3]",
        "map[int]? m = {'a': 1}",
    ])
    def test_fresh_construction_initializes_a_manually_managed_declaration(
            self, parser, semantic, decl):
        # claude.md #204: a regex/array/map literal, or a regex()
        # call, always infers as the plain (unflagged) type -- there
        # being no `?`-producing expression syntax anywhere in the
        # language -- but nothing else could already reference a value
        # that was just constructed right here, so it may adopt
        # manually-managed-ness from its own declaration instead of
        # being rejected as a type mismatch.
        semantic.analyze(parser.parse(f"void func f() {{ {decl} }}"))

    def test_struct_factory_function_initializes_a_manually_managed_declaration(
            self, parser, semantic):
        # claude.md #204: the identical fresh-construction reasoning,
        # for a struct built by a FACTORY FUNCTION rather than
        # field-by-field -- struct's own "no literal syntax" escape
        # hatch only ever covered the no-initializer case.
        source = """
        struct Circle { x:int y:int }
        Circle func make() { Circle c\nc.x = 1\nreturn c }
        void func f() { Circle? c = make() }
        """
        semantic.analyze(parser.parse(source))

    def test_fresh_construction_still_enforces_the_matching_bare_type(self, parser, semantic):
        # claude.md #204: the escape hatch only ever matches the BARE
        # counterpart of the declared type -- a genuine mismatch (not
        # just manually-managed-ness) is still a real type error.
        source = """
        struct Circle { x:int y:int }
        struct Square { x:int y:int }
        Circle func make() { Circle c\nc.x = 1\nreturn c }
        void func f() { Square? s = make() }
        """
        with pytest.raises(Exception):
            semantic.analyze(parser.parse(source))

    def test_an_existing_plain_binding_still_cannot_initialize_a_manually_managed_one(
            self, parser, semantic):
        # claude.md #204: the escape hatch is scoped to FRESH
        # construction only (Call/ArrayLit/MapLit/RegexLit) -- reading
        # an existing, already-live plain binding is still rejected,
        # since something else (this binding's own ordinary,
        # automatically-managed lifecycle) already references that
        # value, exactly the aliasing hazard "no implicit decay" exists
        # to prevent.
        source = """
        struct Circle { x:int y:int }
        void func f() {
            Circle c
            Circle? d = c
        }
        """
        with pytest.raises(Exception):
            semantic.analyze(parser.parse(source))

    def test_manually_managed_thread_message_type_is_accepted(self, parser, semantic):
        # claude.md #202 Phase 2: a manually-managed message type is a
        # real, supported inbound type now -- shares the reference
        # rather than deep-cloning it (see TestRuntime's own real
        # round-trip proof below).
        source = """
        struct Circle { x:int y:int }
        thread Worker {
            on message(worker:thread, msg:Circle?) { log('got') }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_manually_managed_post_message_is_accepted(self, parser, semantic):
        source = """
        struct Circle { x:int y:int }
        thread Worker {
            on message(worker:thread, msg:Circle?) { log('got') }
        }
        void func f() {
            Circle? c
            Worker.postMessage(c)
        }
        """
        semantic.analyze(parser.parse(source))

    def test_mismatched_manually_managed_and_plain_message_type_is_still_rejected(
            self, parser, semantic):
        # claude.md #202 Phase 2: `Circle?`/`Circle` remain genuinely
        # distinct, non-interchangeable types even across a thread
        # boundary -- this is an ordinary type mismatch
        # (check_assignable at the postMessage() call site), unrelated
        # to whether manually-managed values can cross threads at all.
        source = """
        struct Circle { x:int y:int }
        thread Worker {
            on message(worker:thread, msg:Circle) { log('got') }
        }
        void func f() {
            Circle? c
            Worker.postMessage(c)
        }
        """
        with pytest.raises(Exception):
            semantic.analyze(parser.parse(source))

    def test_manually_managed_enum_cannot_alias_an_existing_plain_member_value(
            self, parser, semantic):
        # claude.md #205: a real, ASan-confirmed heap-use-after-free,
        # found while adding per-type thread coverage -- check_
        # assignable's own EnumType member-coercion bypass (claude.md
        # #176) used to fire regardless of whether the DECLARED enum
        # was manually-managed, letting an ORDINARY, automatically-
        # managed member value (`c` here) flow into a manually-managed
        # binding with no retain (codegen's own retain-skip for a
        # manually-managed declaration is unconditional -- claude.md
        # #202's own codegen section). Once `c` went out of scope and
        # its own automatic release freed it, `shape` was left pointing
        # at freed memory. Now correctly rejected as an ordinary type
        # mismatch, the same as a plain `Circle` flowing into
        # `Circle?` already was.
        source = """
        struct Circle { x:int y:int }
        enum Shape = Circle
        Shape? shape
        void func leakIntoShape() {
            Circle c
            c.x = 42
            shape = c
        }
        """
        with pytest.raises(Exception):
            semantic.analyze(parser.parse(source))

    def test_manually_managed_enum_fresh_construction_still_works(self, parser, semantic):
        # claude.md #205: the fix above must not break the ONE
        # legitimate way to populate a manually-managed enum a fresh
        # member value already had (claude.md #204's own escape hatch)
        # -- `check_assignable` here runs against the BARE enum type,
        # which its own EnumType branch (now gated on `not
        # declared.manually_managed`) still matches correctly.
        source = """
        struct Circle { x:int y:int }
        enum Shape = Circle
        Circle func makeCircle() { Circle c\nc.x = 1\nreturn c }
        void func f() { Shape? shape = makeCircle() }
        """
        semantic.analyze(parser.parse(source))

    def test_manually_managed_ordinary_enum_coercion_is_unaffected(self, parser, semantic):
        # claude.md #205: the fix is scoped to a manually-managed
        # DECLARED type only -- an ordinary (non-`?`) enum still
        # accepts an existing member value exactly as before, since
        # there is no automatic-management mismatch to guard against
        # there (both sides are ordinarily managed).
        source = """
        struct Circle { x:int y:int }
        enum Shape = Circle
        void func f() {
            Circle c
            Shape shape = c
        }
        """
        semantic.analyze(parser.parse(source))

    @pytest.mark.parametrize("decl", [
        "on message(worker:thread, msg:http?) { log('got') }",
        "on message(worker:thread, msg:socket?) { log('got') }",
    ])
    def test_manually_managed_http_and_socket_message_types_are_accepted(
            self, parser, semantic, decl):
        # claude.md #205: `_is_thread_sendable_type`'s own early
        # `manually_managed` return (claude.md #202 Phase 2) makes NO
        # exception for http/socket -- both are ordinarily rejected
        # outright (claude.md #195: "tied to the single main-thread
        # connection table"), but a manually-managed one shares its
        # raw pointer rather than being cloned, so neither of those
        # ordinary concerns applies. Semantic-only coverage: neither
        # type is practically constructible as a FRESH, standalone
        # value outside a live request/connection context (both only
        # ever come from an event-handler parameter to begin with), so
        # a full round-trip runtime proof isn't realistically
        # writable the way the other eight thread-sendable types' own
        # TestThreadReferenceSharingPerType tests are.
        source = f"""
        thread Worker {{
            {decl}
        }}
        """
        semantic.analyze(parser.parse(source))


class TestRuntime:
    """Real compile-and-run coverage for the representation itself --
    `free`/`delete` still fully reclaim a manually-managed value, and a
    freed manually-managed binding reads `null` exactly like an
    ordinary `free` already does (claude.md #111)."""

    def test_worked_example_int_count(self, compile_and_run):
        result = compile_and_run("void func f() { int? count = 1\nlog(count) }\nf()\n")
        assert result.returncode == 0
        assert result.stdout.strip() == "1"

    def test_manually_managed_struct_free_then_read_is_null(self, compile_and_run):
        # claude.md #233: this used to `log(c.x)` after `free c` and
        # expect "0". What `free` promises (api.md's own `free` section)
        # is that the BINDING reads null -- `c == null` -- not that a
        # field can still be read THROUGH it: `c.x` on a null struct
        # binding is a load through a null pointer, and LLVM treats that
        # as undefined. It happened to print 0 on Linux and a stray
        # heap pointer on macOS and Windows (both CI jobs, every push),
        # so the old assertion was pinning luck, not a contract.
        source = """
        struct Circle { x:int y:int }
        void func f() {
            Circle? c
            c.x = 1
            c.y = 2
            log(c.x)
            c.x = 99
            log(c.x)
            free c
            log(c == null)
        }
        f()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["1", "99", "true"]

    def test_manually_managed_array_and_map_free_correctly(self, compile_and_run):
        source = """
        void func f() {
            arr[int]? xs
            xs.push(1)
            xs.push(2)
            log(xs.length)
            free xs

            map[int]? m
            m['k'] = 5
            log(m['k'])
            free m
        }
        f()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["2", "5"]

    def test_manually_managed_value_shares_one_reference_across_two_bindings(
            self, compile_and_run):
        # claude.md #202: aliasing a manually-managed value never bumps
        # its refcount -- two bindings referencing the same value share
        # exactly one reference, and a mutation through one is visible
        # through the other.
        source = """
        struct Circle { x:int y:int }
        void func f() {
            Circle? a
            a.x = 1
            Circle? b = a
            b.x = 42
            log(a.x)
            free a
        }
        f()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "42"

    def test_manually_managed_thread_message_shares_the_reference_not_a_clone(
            self, compile_and_run):
        # claude.md #202 Phase 2: the direct, positive proof no clone
        # ever happened, in either direction -- Worker mutates the
        # struct it received THROUGH ITS OWN `on message` parameter,
        # then echoes it back unchanged (bare `postMessage(p)`, the
        # thread -> main direction, also never clones a manually-
        # managed value). If EITHER direction had deep-cloned instead
        # of sharing the reference, the mutation would be invisible on
        # one side or the other: `x.x` (the echo main's own callback
        # receives) would still show it even after a clone, but `c.x`
        # (main's own ORIGINAL binding, never itself touched by main
        # after the initial postMessage call) could only show 99 if
        # Worker's mutation landed on the exact same underlying memory
        # `c` still points to.
        source = """
        struct Circle { x:int y:int }

        Circle? c

        void func onReply(x:Circle?) {
            log(x.x)
            log(c.x)
            close(0)
        }

        on message(worker:thread, msg:Circle?) {
            onReply(msg)
        }

        thread Worker {
            on message(worker:thread, msg:Circle?) {
                msg.x = 99
                postMessage(msg)
            }
        }

        c.x = 1
        Worker.postMessage(c)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["99", "99"]

    def test_fresh_construction_escape_hatch_compiles_and_runs(self, compile_and_run):
        # claude.md #204: a real compile-and-run of every fresh-
        # construction shape the escape hatch covers -- a struct
        # factory function, an array literal, a map literal, and a
        # regex literal -- each declared, used, and freed with no
        # crash and the expected values.
        source = """
        struct Circle { x:int y:int }
        Circle func make() { Circle c\nc.x = 7\nreturn c }

        void func run() {
            Circle? c = make()
            log(c.x)
            free c

            arr[int]? xs = [1, 2, 3]
            log(xs.length)
            free xs

            map[int]? m = {'a': 5}
            log(m['a'])
            free m

            regex? r = /^ab/
            log(r.test('abc'))
            free r
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["7", "3", "5", "true"]


class TestThreadReferenceSharingPerType:
    """claude.md #205: `TestRuntime`'s own
    `test_manually_managed_thread_message_shares_the_reference_not_a_
    clone` proves reference-sharing (not cloning) for exactly one type
    -- struct. Every OTHER manually-manageable, thread-sendable type
    (`arr[T]`/`map[T]`/`enum`/`img`/`blob`/`aud`/`regex`/`url`) gets
    its own dedicated proof here, since `_thread_payload_is_
    passthrough`'s own per-type dispatch (codegen.py) is exactly the
    kind of thing that can be correct for one type and silently wrong
    for another -- confirmed directly while writing this class: `blob?`
    and `regex?` both raised a genuine, previously-unexercised
    CodegenError/invalid-LLVM-IR failure the very first time either
    was actually sent across a thread (see this class's own per-test
    comments, and claude.md #205's own write-up, for the two real bugs
    those failures led to).

    The shared shape: a thread's `on message` handler mutates (or, for
    `regex`/`url`, simply reads) the value it received THROUGH its own
    parameter, then echoes it back via a bare `postMessage`. The
    receiving callback checks BOTH the echoed value's own property AND
    the SENDER's ORIGINAL binding (never touched again after the
    initial `postMessage` call) -- both reflecting the same result is
    only possible if they are the exact same underlying value, not two
    independent clones. The echo is also what makes each test race-
    free: reading the sender's own binding is only safe once the
    thread's own reply proves it is done touching that value."""

    def test_arr_shares_the_reference_not_a_clone(self, compile_and_run):
        source = """
        arr[int]? xs = [1, 2]

        void func onReply(x:arr[int]?) {
            log(x.length)
            log(xs.length)
            close(0)
        }

        on message(worker:thread, msg:arr[int]?) {
            onReply(msg)
        }

        thread Worker {
            on message(worker:thread, msg:arr[int]?) {
                msg.push(99)
                postMessage(msg)
            }
        }

        Worker.postMessage(xs)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["3", "3"]

    def test_map_shares_the_reference_not_a_clone(self, compile_and_run):
        source = """
        map[int]? m = {'k': 1}

        void func onReply(x:map[int]?) {
            log(x['k'])
            log(m['k'])
            close(0)
        }

        on message(worker:thread, msg:map[int]?) {
            onReply(msg)
        }

        thread Worker {
            on message(worker:thread, msg:map[int]?) {
                msg['k'] = 99
                postMessage(msg)
            }
        }

        Worker.postMessage(m)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["99", "99"]

    def test_enum_shares_the_reference_not_a_clone(self, compile_and_run):
        # claude.md #205: `shape` is populated via the fresh-
        # construction escape hatch (claude.md #204) -- `Shape? shape =
        # makeCircle()` -- not `Shape? shape = c` for an EXISTING
        # Circle local `c`, which check_assignable's own EnumType
        # branch now correctly rejects (see
        # TestSemantic.test_manually_managed_enum_cannot_alias_an_
        # existing_plain_member_value for the real heap-use-after-free
        # that combination used to compile straight into).
        source = """
        struct Circle { x:int y:int }
        enum Shape = Circle

        Circle func makeCircle() {
            Circle c
            c.x = 1
            return c
        }

        Shape? shape = makeCircle()

        void func onReply(x:Shape?) {
            log(x.x)
            log(shape.x)
            close(0)
        }

        on message(worker:thread, msg:Shape?) {
            onReply(msg)
        }

        thread Worker {
            on message(worker:thread, msg:Shape?) {
                msg.x = 99
                postMessage(msg)
            }
        }

        Worker.postMessage(shape)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["99", "99"]

    def test_img_shares_the_reference_not_a_clone(self, compile_and_run, sprite_sheet_png):
        # claude.md #189: drawPixel(...)/getPixelColor(...) mutate/read
        # the image's own in-memory pixel buffer directly -- the same
        # kind of observable, in-place mutation a struct field gives.
        source = f"""
        img? sheet = '{sprite_sheet_png}'
        color red = 'red'

        void func onReply(x:img?) {{
            log(x.getPixelColor(0, 0) == red)
            log(sheet.getPixelColor(0, 0) == red)
            close(0)
        }}

        on message(worker:thread, msg:img?) {{
            onReply(msg)
        }}

        thread Worker {{
            on message(worker:thread, msg:img?) {{
                color red = 'red'
                msg.drawPixel(0, 0, red)
                postMessage(msg)
            }}
        }}

        Worker.postMessage(sheet)
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "true"]

    def test_blob_shares_the_reference_not_a_clone(self, compile_and_run):
        # claude.md #205: a real, found-and-fixed bug -- `p.write(...)`
        # inside `on message(p:blob?)` used to raise "cannot access
        # field 'write' on blob?", a CodegenError -- the thread
        # handler's own parameter binding stored the FLAGGED
        # `info.inbound_type` directly into its own `body_env` (unlike
        # every other declaration/parameter site in this file, which
        # keep `env` bare -- claude.md #202's own deliberate design),
        # so blob's own exact-equality (`== BLOB`) method dispatch
        # stopped recognizing it as blob at all. See codegen.py's
        # `_bare_type` for the fix.
        source = """
        blob? b = 'data.bin'

        void func onReply(x:blob?) {
            log(x.toText())
            log(b.toText())
            close(0)
        }

        on message(worker:thread, msg:blob?) {
            onReply(msg)
        }

        thread Worker {
            on message(worker:thread, msg:blob?) {
                msg.write('mutated')
                postMessage(msg)
            }
        }

        Worker.postMessage(b)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["mutated", "mutated"]

    def test_aud_shares_the_reference_not_a_clone(self, compile_and_run, audio_null_env, tmp_path):
        # claude.md #205: `isPlaying()` is a genuine per-VALUE identity
        # check at the runtime level (festina_audio_is_playing compares
        # the channel's own `clip` pointer against the queried value's
        # own pointer, see festina_runtime_audio.c) -- a CLONE would
        # have its own, different pointer, so `clip.isPlaying()` true
        # on the SENDER's own original binding, after only the
        # RECEIVED parameter ever called `.playLoop()`, is only
        # possible if they share the identical underlying value.
        # `.playLoop()`, not `.play()` -- a real, found timing gap:
        # `.play()` finishes almost immediately (beep.wav is short),
        # and by the time the round trip through the thread's own
        # message queue completes, a one-shot play() had already ended
        # -- observed directly as a false negative before switching to
        # playLoop(), not assumed.
        shutil.copy(_WAV_FIXTURE, tmp_path / "beep.wav")
        source = """
        aud? clip = 'beep.wav'

        void func onReply(x:aud?) {
            log(x.isPlaying())
            log(clip.isPlaying())
            x.stop()
            close(0)
        }

        on message(worker:thread, msg:aud?) {
            onReply(msg)
        }

        thread Worker {
            on message(worker:thread, msg:aud?) {
                msg.playLoop()
                postMessage(msg)
            }
        }

        Worker.postMessage(clip)
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "true"]

    def test_regex_shares_the_reference_not_a_clone(self, compile_and_run):
        # claude.md #205: a second real, found-and-fixed bug, the same
        # shape as blob's own -- `_thread_payload_is_passthrough`
        # (codegen.py) never had an answer for regex AT ALL (an
        # ordinary regex was always rejected upstream by semantic.py's
        # own `_is_thread_sendable_type`, so this code path was
        # genuinely unreachable before a manually-managed regex could
        # cross a thread) -- `on message(p:regex?)` compiled (semantic
        # analysis correctly allows it) but produced invalid LLVM IR
        # (`add ptr ..., 0`) at the unboxing step, misrouting a
        # pointer-shaped payload down the SCALAR unboxing path. `regex`
        # has no mutable state to prove sharing via mutation the way
        # struct/arr/map/img/blob do -- `.test()` alone is not a
        # reference-identity proof (a clone would answer identically)
        # -- so this is a real round-trip + no-crash proof of the fix,
        # not a same-address proof; the codegen fix itself is what
        # matters here, not this test's own assertions.
        source = """
        regex? r = /^ab/

        void func onReply(x:regex?) {
            log(x.test('abc'))
            log(r.test('abc'))
            close(0)
        }

        on message(worker:thread, msg:regex?) {
            onReply(msg)
        }

        thread Worker {
            on message(worker:thread, msg:regex?) {
                log(msg.test('abc'))
                postMessage(msg)
            }
        }

        Worker.postMessage(r)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "true", "true"]

    def test_url_shares_the_reference_not_a_clone(self, compile_and_run):
        # claude.md #205: url's own fields are all read-only (claude.md
        # #162), so there is no mutation to prove sharing with the way
        # struct/arr/map/img/blob do -- this is a round-trip proof
        # (compiles, runs, both sides read the identical field) rather
        # than a same-address proof, same caveat as regex's own test
        # above, and for the identical underlying reason (an immutable
        # value's identity is not behaviorally observable in Festina).
        source = """
        url? u = parseURL('https://example.com/path')

        void func onReply(x:url?) {
            log(x.hostname)
            log(u.hostname)
            close(0)
        }

        on message(worker:thread, msg:url?) {
            onReply(msg)
        }

        thread Worker {
            on message(worker:thread, msg:url?) {
                postMessage(msg)
            }
        }

        Worker.postMessage(u)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["example.com", "example.com"]
