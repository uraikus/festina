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
import pytest


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
        # claude.md #202: regex? cannot be initialized from a fresh
        # /pattern/ literal (a documented, accepted gap -- see
        # todo.md), so this exercises method dispatch through a
        # parameter instead, the one real way a manually-managed
        # regex value can exist.
        source = """
        void func testIt(r:regex?) {
            log(r.test('abcdef'))
        }
        """
        semantic.analyze(parser.parse(source))

    def test_regex_literal_cannot_initialize_a_manually_managed_regex(self, parser, semantic):
        # claude.md #202: the documented, accepted gap itself -- a
        # /pattern/ literal always infers as plain `regex`, and
        # `regex?`/`regex` are genuinely different, non-interchangeable
        # types.
        with pytest.raises(Exception):
            semantic.analyze(parser.parse("void func f() { regex? r = /abc/ }"))

    def test_manually_managed_thread_message_type_is_accepted(self, parser, semantic):
        # claude.md #202 Phase 2: a manually-managed message type is a
        # real, supported inbound type now -- shares the reference
        # rather than deep-cloning it (see TestRuntime's own real
        # round-trip proof below).
        source = """
        struct Circle { x:int y:int }
        thread Worker {
            on message(p:Circle?) { log('got') }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_manually_managed_post_message_is_accepted(self, parser, semantic):
        source = """
        struct Circle { x:int y:int }
        thread Worker {
            on message(p:Circle?) { log('got') }
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
            on message(p:Circle) { log('got') }
        }
        void func f() {
            Circle? c
            Worker.postMessage(c)
        }
        """
        with pytest.raises(Exception):
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
            log(c.x)
        }
        f()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["1", "99", "0"]

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

        thread Worker {
            on message(p:Circle?) {
                p.x = 99
                postMessage(p)
            }
        }

        Circle? c

        void func onReply(x:Circle?) {
            log(x.x)
            log(c.x)
            close(0)
        }

        Worker.onMessage(void (x:Circle?) => onReply(x))
        c.x = 1
        Worker.postMessage(c)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["99", "99"]
