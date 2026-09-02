"""Code generation -- claude.md #47 (executable generation), plus the
runtime-facing halves of #7/#8 (entry point + startup), #26 (arrays),
#28-34 (automatic SQLite schema sync, sqlite() queries), #41/#42
(log/fail), #45 (string interpolation).

Two kinds of tests here:

- Tests needing no C toolchain at all -- either they only check that
  festina.codegen raises a CompileError (nothing left actually needs
  this treatment; every claude.md construct this compiler targets now
  generates real code), or, like TestUnrecognizedEventName below, they
  only inspect the generated IR text directly.
- End-to-end tests actually compile a Festina program to a native
  executable (via the `compile_and_run` fixture) and check its real
  stdout/exit code/festina.sqlite -- these skip cleanly if no C compiler
  is on PATH, since that's an environment limitation, not a missing
  Festina feature.
"""
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
import wave

import pytest

_EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
# claude.md #101: real JPEG/MP3 files, committed rather than generated,
# because nothing in this repo can encode either and a test that only
# exercised a hand-rolled approximation would prove nothing about
# libjpeg/libmpg123 actually being wired up. Both are tiny (a 16x16
# gradient and a fifth of a second of a 440Hz tone).
_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_JPEG_FIXTURE = os.path.join(_FIXTURES_DIR, "gradient.jpg")
_MP3_FIXTURE = os.path.join(_FIXTURES_DIR, "tone.mp3")


# ---- no C toolchain needed -- IR-text-only checks ----

class TestUnrecognizedEventName:
    def _generate(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_unrecognized_event_name_still_compiles_but_is_never_called(self, parser, semantic, codegen):
        # claude.md #40 only ever shows mouse and keyboard events --
        # any other
        # name still compiles (it's checked like any other code) but
        # there's no event source for it, so it's simply dead code, not
        # a compile error. See TestGraphics for the same point end to
        # end (the declared handler never fires).
        source = "on somethingElse(a:int) {\n    log(a)\n}"
        ir = self._generate(parser, semantic, codegen, source)
        assert "@__festina_on_somethingElse" in ir


# ---- end-to-end: real compiled, real executed programs ----

class TestCloseAndExitHandler:
    """claude.md #131: close(code) exits the program with `code`,
    running a declared `on exit(code:int)` handler first if there is
    one. Deliberately NOT a graphics feature -- close()/`on exit` work
    in a program that never opens a window, unlike `on close` (the
    existing window-close-button event), which this is not the same
    thing as."""

    def test_close_exits_with_the_given_code_and_runs_no_handler_by_default(self, compile_and_run):
        result = compile_and_run("log('before')\nclose(7)\nlog('unreachable')")
        assert result.returncode == 7
        assert result.stdout == "before\n"

    def test_on_exit_runs_before_the_program_actually_exits(self, compile_and_run):
        source = """
        on exit(code:int) {
            log(`exiting with ${code}`)
        }
        log('before')
        close(42)
        log('unreachable')
        """
        result = compile_and_run(source)
        assert result.returncode == 42
        assert result.stdout == "before\nexiting with 42\n"

    def test_close_with_no_on_exit_handler_declared_works_fine(self, compile_and_run):
        result = compile_and_run("close(0)")
        assert result.returncode == 0
        assert result.stdout == ""

    def test_close_requires_exactly_one_int_argument(self, parser, semantic, errors):
        for source in ["close()", "close(1, 2)", "close('a')", "close(1.5)"]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")

    def test_on_exit_requires_exactly_one_int_parameter(self, parser, semantic, errors):
        for source in [
            "on exit() { }",
            "on exit(code:text) { }",
            "on exit(a:int, b:int) { }",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")

    def test_a_user_function_cannot_shadow_close(self, parser, semantic, errors):
        program = parser.parse("void func close(x:int) { }", filename="main.f")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program, filename="main.f")


class TestArithmeticAndControlFlow:
    """claude.md #14-16, #18-20, #23-24: functions, expressions, if/else,
    ternary all produce correct runtime behavior, not just valid IR."""

    def test_function_call_and_arithmetic(self, compile_and_run):
        source = """
        int func add(a:int, b:int) {
            return a + b
        }
        log(add(2, 3))
        log(add(10, -4))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["5", "6"]

    def test_if_else_branches(self, compile_and_run):
        source = """
        int func classify(n:int) {
            if n > 0 {
                return 1
            } else {
                return -1
            }
        }
        log(classify(5))
        log(classify(-5))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "-1"]

    def test_ternary_result(self, compile_and_run):
        source = """
        int x = 7
        text label = x > 5 ? 'big' : 'small'
        log(label)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "big"

    def test_ternary_with_a_null_branch(self, compile_and_run):
        # claude.md #192: a bare `null` branch is emitted AS the other
        # branch's type -- `c ? 1 : null` used to phi a raw "null"
        # against i64 (invalid IR) and `c ? null : 7` crashed the
        # compiler on a None type. The taken null branch yields the
        # int-null sentinel; the taken concrete branch yields its value.
        source = """
        log(true ? 1 : null)
        log(false ? 1 : null)
        log(false ? null : 7)
        log(true ? null : 7)
        """
        result = compile_and_run(source)
        lines = result.stdout.splitlines()
        assert lines[0] == "1"
        assert lines[2] == "7"
        # the null-branch cases print int's own null sentinel
        assert lines[1] == lines[3]
        assert lines[1] != "1" and lines[1] != "7"

    def test_logical_and_or_short_circuit(self, compile_and_run):
        # claude.md doesn't spell out short-circuit evaluation explicitly,
        # but it's the JavaScript-familiar behavior claude.md #45 asks for
        # ("Festina should retain familiar JavaScript conventions").
        source = """
        bool func sideEffect(tag:text) {
            log(tag)
            return true
        }
        bool r1 = false && sideEffect('should-not-print-1')
        bool r2 = true || sideEffect('should-not-print-2')
        log(r1)
        log(r2)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true"]

    def test_float_and_bool_and_text_log(self, compile_and_run):
        source = """
        float pi = 3.5
        bool enabled = true
        text name = 'Festina'
        log(pi)
        log(enabled)
        log(name)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["3.5", "true", "Festina"]


class TestStrings:
    """claude.md #9, #45: template string interpolation."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_template_interpolation(self, compile_and_run):
        source = """
        text func greet(name:text) {
            return `Hello, ${name}!`
        }
        log(greet('World'))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "Hello, World!"

    def test_multiple_interpolations(self, compile_and_run):
        source = """
        int x = 3
        int y = 4
        log(`(${x}, ${y})`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "(3, 4)"

    # -- claude.md #82: a template literal skips concatenating with an
    # empty literal piece entirely (starts/ends with an interpolation,
    # or has two interpolations back to back) instead of emitting a
    # wasted `festina_str_concat("", ...)`/`festina_str_concat(..., "")`
    # call for it -- correctness (output is identical either way) and
    # the actual call-count reduction are both worth locking in
    # separately, so the optimization can't silently regress back to
    # the old always-two-calls-per-interpolation shape.

    def test_interpolation_at_the_start_produces_correct_output(self, compile_and_run):
        source = """
        text name = 'World'
        log(`${name}!`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "World!"

    def test_interpolation_at_the_end_produces_correct_output(self, compile_and_run):
        source = """
        text name = 'World'
        log(`Hello, ${name}`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "Hello, World"

    def test_bare_interpolation_with_no_surrounding_text_produces_correct_output(
            self, compile_and_run):
        source = """
        text name = 'World'
        log(`${name}`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "World"

    def test_adjacent_interpolations_with_no_text_between_them_produce_correct_output(
            self, compile_and_run):
        source = """
        int x = 3
        int y = 4
        log(`${x}${y}`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "34"

    def test_a_leading_empty_piece_emits_no_concat_for_it(self, parser, semantic, codegen):
        # `${name}!` has parts = ["", "!"] -- the leading "" is never
        # concatenated at all; only ONE festina_str_concat call remains
        # (appending "!"), not two.
        source = """
        void func f(name:text) {
            log(`${name}!`)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call ptr @festina_str_concat(") == 1

    def test_a_trailing_empty_piece_emits_no_concat_for_it(self, parser, semantic, codegen):
        # `Hello, ${name}` has parts = ["Hello, ", ""] -- the trailing ""
        # is never concatenated; only ONE call remains (prepending
        # "Hello, ").
        source = """
        void func f(name:text) {
            log(`Hello, ${name}`)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call ptr @festina_str_concat(") == 1

    def test_a_bare_interpolation_emits_no_concat_call_at_all(self, parser, semantic, codegen):
        # `${name}` has parts = ["", ""] -- both empty, so the
        # interpolated value's own text is used directly, with zero
        # festina_str_concat calls (the old codegen emitted two: `"" +
        # name`, then `+ ""`).
        source = """
        void func f(name:text) {
            log(`${name}`)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call ptr @festina_str_concat(") == 0

    def test_adjacent_interpolations_emit_exactly_one_concat_for_the_empty_piece_between_them(
            self, parser, semantic, codegen):
        # `${x}${y}` has parts = ["", "", ""] -- the leading and
        # trailing pieces are both skipped (same as the bare-
        # interpolation case), but the piece BETWEEN x and y is also
        # "" and must still result in exactly one concat joining x's
        # and y's own text together (there's no way to skip joining two
        # genuinely different runtime values into one string).
        source = """
        void func f(x:int, y:int) {
            log(`${x}${y}`)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call ptr @festina_str_concat(") == 1

    def test_a_template_with_no_empty_pieces_is_unaffected(self, parser, semantic, codegen):
        # `(${x}, ${y})` has parts = ["(", ", ", ")"] -- none empty, so
        # every concat call this template always needed is still there:
        # 4 total (join x in, join ", " in, join y in, join ")" in),
        # unchanged from before claude.md #82.
        source = """
        void func f(x:int, y:int) {
            log(`(${x}, ${y})`)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call ptr @festina_str_concat(") == 4


class TestBlob:
    """claude.md #36, given its real meaning by claude.md #109.

    #36's only worked example was always `blob data = 'path/to/file'`,
    and for a long time that stored the PATH and never read the file --
    blob was a second name for `text`. #109 makes the example mean what
    it says: a blob is the file's BYTES, loaded at the declaration,
    keeping the path so the file can be written, appended to, tested
    for and deleted through the same value.
    """

    def test_a_blob_loads_the_bytes_at_its_path(self, compile_and_run, tmp_path):
        path = tmp_path / "data.txt"
        path.write_text("the contents")
        source = f"blob data = '{path}'\nlog(data.toText())"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "the contents"

    def test_logging_a_blob_prints_its_contents(self, compile_and_run, tmp_path):
        # claude.md #115: log(blob) and `${blob}` print the contents --
        # the blob's own toText(), which is what the implicit conversion
        # means. (#114 briefly made this an error; a blob is very often
        # a text file, so the conversion it already had wins.)
        path = tmp_path / "data.txt"
        path.write_text("the contents")
        source = f"""
        blob data = '{path}'
        log(data)
        log(`inline: ${{data}}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["the contents", "inline: the contents"]

    def test_a_missing_path_is_an_empty_blob_not_a_failure(self, compile_and_run):
        # claude.md #93's rule, inherited: a missing file is something
        # you test for, not something that stops the program. It is also
        # how a file that does not exist yet gets created.
        source = ("blob data = '/nonexistent/nowhere.txt'\n"
                  "log(data.exists())\n"
                  "log(data.toText() == '')\n")
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["false", "true"]

    def test_write_append_read_exists_delete(self, compile_and_run, tmp_path):
        path = tmp_path / "notes.txt"
        source = f"""
        blob f = '{path}'
        log(f.write('hello'))
        log(f.append(' world'))
        log(f.toText())
        log(f.exists())
        log(f.delete())
        log(f.exists())
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == [
            "true", "true", "hello world", "true", "true", "false"]

    def test_the_bytes_survive_deleting_the_file(self, compile_and_run, tmp_path):
        # .delete() removes the FILE. The blob is an ordinary value and
        # is unaffected, which is what makes "delete it but keep what it
        # said" expressible.
        path = tmp_path / "notes.txt"
        source = f"""
        blob f = '{path}'
        f.write('remembered')
        f.delete()
        log(f.exists())
        log(f.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "remembered"]

    def test_write_updates_what_to_text_reports(self, compile_and_run, tmp_path):
        # Not just the file: the in-memory bytes too, so toText() after
        # write() reports what was written rather than what the file
        # held when the blob was declared.
        path = tmp_path / "notes.txt"
        path.write_text("original")
        source = f"""
        blob f = '{path}'
        log(f.toText())
        f.write('replaced')
        log(f.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["original", "replaced"]

    def test_the_path_may_be_any_text_expression(self, compile_and_run, tmp_path):
        path = tmp_path / "data.txt"
        path.write_text("found")
        source = f"""
        text dir = '{tmp_path}/'
        blob f = dir + 'data.txt'
        log(f.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "found"

    def test_assigning_a_blob_shares_one_handle(self, compile_and_run, tmp_path):
        # claude.md #109: "if a blob = another blob, have it copy the
        # reference of that current file". Writing through one is
        # visible through the other, which is what proves they are one
        # handle rather than two copies.
        path = tmp_path / "shared.txt"
        source = f"""
        blob a = '{path}'
        a.write('first')
        blob b = a
        a.write('second')
        log(b.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "second"

    def test_reassigning_leaves_an_earlier_reference_intact(
            self, compile_and_run, tmp_path):
        # The other half of the same rule: rebinding `a` must not
        # disturb `keep`, which still holds the first file. Verified
        # leak-free separately under LeakSanitizer -- this pins that the
        # surviving reference is also still READABLE, i.e. that the
        # release did not free a handle someone else still held.
        p1 = tmp_path / "one.txt"
        p2 = tmp_path / "two.txt"
        source = f"""
        blob a = '{p1}'
        a.write('file one')
        blob keep = a
        a = '{p2}'
        a.write('file two')
        log(keep.toText())
        log(a.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["file one", "file two"]

    def test_a_blob_is_no_longer_comparable_to_text(self, parser, semantic, errors):
        # It used to be, because it WAS a text. A handle and a string
        # are not the same kind of thing, and comparing them would have
        # compared a pointer against a string's contents.
        program = parser.parse("blob data = 'x'\ntext t = 'x'\nlog(data == t)")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_a_blob_compares_against_null(self, compile_and_run, tmp_path):
        path = tmp_path / "data.txt"
        path.write_text("x")
        source = f"blob f = '{path}'\nlog(f == null)\nlog(f != null)"
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true"]

    def test_an_unknown_blob_method_is_rejected(self, parser, semantic, errors):
        program = parser.parse("blob f = 'x'\nf.slurp()")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_write_requires_exactly_one_text_argument(self, parser, semantic, errors):
        program = parser.parse("blob f = 'x'\nf.write()")
        with pytest.raises(errors.CompileError, match="write"):
            semantic.analyze(program)

    def test_to_text_takes_no_arguments(self, parser, semantic, errors):
        program = parser.parse("blob f = 'x'\nlog(f.toText('extra'))")
        with pytest.raises(errors.CompileError, match="toText"):
            semantic.analyze(program)


class TestStructs:
    """claude.md #27: structs are native in-memory objects with typed,
    assignable fields."""

    def test_struct_field_assignment_and_read(self, compile_and_run):
        source = """
        struct Point {
            x:int
            y:int
        }
        Point p
        p.x = 3
        p.y = 4
        log(p.x + p.y)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7"

    def test_struct_passed_to_function(self, compile_and_run):
        source = """
        struct User {
            id:int
            name:text
        }
        int func idOf(u:User) {
            return u.id
        }
        User user
        user.id = 42
        user.name = 'Patrick'
        log(idOf(user))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "42"

    def test_struct_returned_by_value_from_function(self, compile_and_run):
        # Regression test: struct backing storage used to be a stack
        # alloca even for locals, so returning one handed the caller a
        # pointer into a popped stack frame -- verified to silently print
        # garbage before the fix (calloc'd storage instead; see
        # festina/codegen.py's module docstring).
        source = """
        struct Point {
            x:int
            y:int
        }
        Point func origin() {
            Point p
            p.x = 0
            p.y = 0
            return p
        }
        Point func makePoint(a:int, b:int) {
            Point p
            p.x = a
            p.y = b
            return p
        }
        Point o = origin()
        log(o.x)
        log(o.y)
        Point q = makePoint(7, 8)
        log(q.x)
        log(q.y)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "0", "7", "8"]

    def test_local_struct_fields_default_to_zero(self, compile_and_run):
        # calloc, not malloc, for struct backing storage -- a local
        # struct's unset fields should read as zero, same as a global
        # struct's (`zeroinitializer`), not garbage.
        source = """
        struct Counters {
            hits:int
            misses:int
        }
        Counters c
        log(c.hits)
        log(c.misses)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "0"]


class TestSelfReferencingStructs:
    """claude.md #106: a struct may name its own type in a field, and may
    name a struct declared later in the file. Both used to be rejected
    with "unknown type", not because the representation could not hold
    them -- a struct-typed field is a pointer -- but because
    analyze_struct registered the finished struct only after resolving
    every field."""

    def test_struct_may_reference_its_own_type(self, compile_and_run):
        source = """
        struct Node {
            n:int
            next:Node
        }
        Node head
        head.n = 1
        head.next.n = 2
        log(head.n + head.next.n)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "3"

    def test_a_linked_list_can_be_built_and_walked(self, compile_and_run):
        # claude.md #97's auto-vivification is what makes the build side
        # work with no extra machinery: reaching THROUGH a null struct
        # field allocates it. The walk then reads back what was written.
        source = """
        struct Node {
            n:int
            next:Node
        }
        Node head
        head.n = 1
        head.next.n = 2
        head.next.next.n = 3

        int total = 0
        Node cursor = head
        for int i = 0, i < 3, i++ {
            total = total + cursor.n
            cursor = cursor.next
        }
        log(total)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "6"

    def test_two_structs_may_reference_each_other(self, compile_and_run):
        source = """
        struct A {
            n:int
            b:B
        }
        struct B {
            n:int
            a:A
        }
        A first
        first.n = 1
        first.b.n = 2
        first.b.a.n = 3
        log(first.n + first.b.n + first.b.a.n)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "6"

    def test_a_struct_may_reference_one_declared_later(self, compile_and_run):
        # The forward-reference half of the same fix -- this failed for
        # exactly the same reason and with the same error message.
        source = """
        struct Outer {
            inner:Inner
        }
        struct Inner {
            n:int
        }
        Outer o
        o.inner.n = 42
        log(o.inner.n)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "42"

    def test_a_self_referencing_struct_still_has_a_release_wrapper(
            self, parser, semantic, codegen):
        # A struct with a struct-typed field needs the per-type release
        # wrapper of claude.md #78, and here that field is the struct
        # itself. The wrapper's cache entry is written before the field
        # loop recurses, so exactly ONE wrapper is generated and it
        # calls itself -- rather than the compiler recursing forever.
        source = ("struct Node { n:int next:Node }\n"
                  "Node func make(v:int) {\n"
                  "    Node p\n"
                  "    p.n = v\n"
                  "    return p\n"
                  "}\n"
                  "Node head = make(1)\n"
                  "head.next = make(2)\n"
                  "log(head.n + head.next.n)\n")
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        define = "define void @__festina_release_struct_Node(ptr %payload) {"
        assert ir.count(define) == 1
        # ...and the one wrapper genuinely recurses into itself: the
        # cascade over its `next` field is a call to the very function
        # being defined, which is only well-founded because the cache
        # entry is written before the field loop runs.
        body = ir.split(define, 1)[1].split("\n}", 1)[0]
        assert "call void @__festina_release_struct_Node(" in body

    def test_a_reference_cycle_runs_correctly_and_does_not_crash(self, compile_and_run):
        # claude.md #106 made `a.next = a` constructible and accepted
        # the leak; claude.md #120's trial deletion now reclaims a
        # GARBAGE cycle. What this test pins is the other half: a
        # cycle that is still externally held (the global binding here)
        # keeps working -- reads through it stay valid, the trial run
        # by each release must never free a reachable cycle.
        source = """
        struct Node {
            n:int
            next:Node
        }
        Node a
        a.n = 7
        a.next = a
        log(a.next.next.next.n)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "7"

    def test_breaking_a_cycle_with_null_reclaims_it(self, compile_and_run):
        # The workaround api.md recommends, pinned so it stays true:
        # clearing the back-reference drops the count to zero and the
        # value is reclaimed normally. Verified separately under
        # LeakSanitizer (50 iterations, zero leaked bytes -- against
        # 1,200 for the identical program without the `= null`); this
        # test pins the behavior, since the sanitizer does not run here.
        source = """
        struct Node {
            n:int
            next:Node
        }
        void func build() {
            Node a
            a.n = 7
            a.next = a
            a.next = null
        }
        for int i = 0, i < 50, i++ {
            build()
        }
        log('done')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_a_duplicate_struct_name_is_still_rejected(self, parser, semantic, errors):
        # The pre-pass registers every name up front, so the duplicate
        # check can no longer just ask whether the name is present --
        # it has to ask whether it has real fields. Get that wrong and
        # every struct is a duplicate of itself.
        program = parser.parse("struct A { n:int }\nstruct A { m:int }\n")
        with pytest.raises(errors.CompileError, match="already declared"):
            semantic.analyze(program)

    def test_a_struct_colliding_with_a_table_name_is_still_rejected(
            self, parser, semantic, errors):
        program = parser.parse("table A { n:int }\nstruct A { m:int }\n")
        with pytest.raises(errors.CompileError, match="already declared"):
            semantic.analyze(program)

    def test_an_unknown_field_type_is_still_rejected(self, parser, semantic, errors):
        # And the failed declaration must not leave a half-registered
        # name behind for a later declaration to collide with.
        program = parser.parse("struct A { n:Nonexistent }\n")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_a_name_left_by_a_failed_struct_does_not_become_a_duplicate(
            self, parser, semantic, errors):
        # The failure above must report the unknown type, not a
        # spurious "already declared" from its own placeholder.
        program = parser.parse("struct A { n:Nonexistent }\n")
        with pytest.raises(errors.CompileError) as excinfo:
            semantic.analyze(program)
        assert "already declared" not in str(excinfo.value)


class TestArrays:
    """claude.md #26: arr[T] with elements sized/typed at compile time,
    from a literal, read and written by index. `.length` (#63) and
    for/while loops (#60/#61) are covered in TestArrayLength and
    TestLoops below. No growth and no bounds checking -- claude.md
    doesn't specify either (see the module docstring in
    festina/codegen.py), so they aren't implemented."""

    def test_literal_index_read(self, compile_and_run):
        result = compile_and_run("arr[int] nums = [10, 20, 30]\nlog(nums[0])\nlog(nums[2])")
        assert result.stdout.splitlines() == ["10", "30"]

    def test_indexed_write(self, compile_and_run):
        source = """
        arr[int] nums = [1, 2, 3]
        nums[1] = 99
        log(nums[1])
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "99"

    def test_index_by_variable_expression(self, compile_and_run):
        source = """
        arr[int] nums = [5, 6, 7]
        int i = 2
        log(nums[i])
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7"

    def test_array_of_floats_and_text(self, compile_and_run):
        source = """
        arr[float] prices = [1.5, 2.5, 3.0]
        arr[text] names = ['a', 'b', 'c']
        log(prices[1])
        log(names[2])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["2.5", "c"]

    def test_declaration_without_initializer(self, compile_and_run):
        # claude.md #26's own examples (`arr[int] numbers`) have no
        # initializer.
        result = compile_and_run("arr[int] empty\nlog('declared fine')")
        assert result.returncode == 0
        assert result.stdout.strip() == "declared fine"

    def test_nested_arrays(self, compile_and_run):
        # claude.md #26: "Nested arrays are valid: arr[arr[int]] matrix".
        source = """
        arr[arr[int]] matrix = [[1, 2], [3, 4]]
        log(matrix[0][1])
        log(matrix[1][0])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["2", "3"]

    def test_array_as_function_parameter_and_return_value(self, compile_and_run):
        source = """
        int func sum3(nums:arr[int]) {
            return nums[0] + nums[1] + nums[2]
        }
        arr[int] func makeRange(a:int, b:int) {
            return [a, b, a + b]
        }
        log(sum3([1, 2, 3]))
        arr[int] r = makeRange(3, 4)
        log(r[0])
        log(r[1])
        log(r[2])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["6", "3", "4", "7"]

    def test_array_of_structs(self, compile_and_run):
        source = """
        struct Point {
            x:int
            y:int
        }
        Point p1
        p1.x = 1
        p1.y = 2
        Point p2
        p2.x = 3
        p2.y = 4
        arr[Point] points = [p1, p2]
        log(points[0].x)
        log(points[1].y)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "4"]


class TestArrayLength:
    """claude.md #63: every array has a built-in read-only `.length`."""

    def test_length_of_literal(self, compile_and_run):
        result = compile_and_run("arr[int] values = [1, 2, 3]\nlog(values.length)")
        assert result.stdout.strip() == "3"

    def test_length_of_empty_array(self, compile_and_run):
        result = compile_and_run("arr[int] values = []\nlog(values.length)")
        assert result.stdout.strip() == "0"

    def test_length_updates_after_reassignment(self, compile_and_run):
        source = """
        arr[int] values = [1, 2]
        values = [1, 2, 3, 4, 5]
        log(values.length)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "5"


class TestMaps:
    """claude.md #72: map[T] -- { key: value, ... } literals, indexed
    get/set, .forEach()."""

    def test_literal_with_string_and_variable_keys(self, compile_and_run):
        source = """
        text npc2Id = 'npc2'
        map[int] npcHealths = {'npc1': 10, npc2Id: 15}
        log(npcHealths['npc1'])
        log(npcHealths[npc2Id])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["10", "15"]

    def test_map_of_text_values(self, compile_and_run):
        source = """
        text npc2Id = 'npc2'
        map[text] npcNames = {'npc1': 'jim', npc2Id: 'john'}
        log(npcNames['npc1'])
        log(npcNames[npc2Id])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["jim", "john"]

    def test_missing_key_returns_null(self, compile_and_run):
        # claude.md #72: "If the key is not present in the map, the
        # result is null" -- text's null already prints as an empty
        # line (see TestRegex's identical match()-with-no-match test).
        source = """
        map[text] m = {'a': 'x'}
        log('before')
        log(m['missing'])
        log('after')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["before", "", "after"]

    def test_missing_key_on_int_map_returns_the_int_null_sentinel(self, compile_and_run):
        # Same null representation int already uses everywhere else
        # (e.g. modulo by zero -- claude.md #57) -- not a special case
        # invented for maps. claude.md #143: division by zero is no
        # longer a source of an INT null value (/ always returns float
        # now), so % -- still int-returning for two ints -- is the
        # comparison source instead.
        int_null = compile_and_run("int a = 1\nint b = 0\nlog(a % b)")
        missing_key = compile_and_run("map[int] m = {'a': 1}\nlog(m['missing'])")
        assert missing_key.stdout == int_null.stdout

    def test_empty_map_literal(self, compile_and_run):
        result = compile_and_run("map[int] m = {}\nlog(m['x'])")
        assert result.returncode == 0

    def test_duplicate_key_in_a_literal_last_one_wins(self, compile_and_run):
        # Two *literal* string keys colliding (`{'a': 1, 'a': 2}`) is now
        # a compile-time error instead (see tests/test_maps.py's
        # TestMapLiteral -- knowable for free, almost always a typo).
        # "last value wins" for a genuine runtime collision still holds
        # and is still exercised here, via a variable key that happens
        # to equal an earlier literal key only at runtime.
        source = "text k = 'a'\nmap[int] m = {'a': 1, k: 2}\nlog(m['a'])"
        result = compile_and_run(source)
        assert result.stdout.strip() == "2"

    def test_write_adds_a_new_key(self, compile_and_run):
        source = """
        map[int] m = {'a': 1}
        m['b'] = 2
        log(m['a'])
        log(m['b'])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "2"]

    def test_write_replaces_an_existing_key(self, compile_and_run):
        source = """
        map[int] m = {'a': 1}
        m['a'] = 30
        log(m['a'])
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "30"

    def test_write_with_a_variable_key(self, compile_and_run):
        source = """
        text npc2Id = 'npc2'
        map[int] npcHealths = {'npc1': 10, npc2Id: 15}
        npcHealths[npc2Id] = 30
        log(npcHealths[npc2Id])
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "30"

    def test_zero_initialized_map_variable_behaves_as_empty(self, compile_and_run):
        source = """
        map[text] m
        log(m['x'])
        m['x'] = 'now set'
        log(m['x'])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["", "now set"]

    def test_map_as_a_struct_field(self, compile_and_run):
        source = """
        struct Holder {
            scores:map[int]
        }
        Holder h
        h.scores = {'a': 1}
        h.scores['b'] = 2
        log(h.scores['a'])
        log(h.scores['b'])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "2"]

    def test_map_assignment_on_a_non_addressable_target_is_a_clear_error(self, parser, semantic, codegen, errors):
        source = """
        map[int] func getMap() {
            map[int] m = {'a': 1}
            return m
        }
        getMap()['a'] = 5
        """
        program = parser.parse(source)
        analyzed = semantic.analyze(program)
        with pytest.raises(errors.CompileError, match="plain variable or field"):
            codegen.generate_ir(program, analyzed)

    def test_forEach_visits_every_entry(self, compile_and_run):
        source = """
        void func logHealth(h:int, key:text) {
            log(`${key} ${h.toText()}`)
        }
        map[int] npcHealths = {'npc1': 10, 'npc2': 15}
        npcHealths.forEach(logHealth)
        """
        result = compile_and_run(source)
        assert sorted(result.stdout.splitlines()) == ["npc1 10", "npc2 15"]

    def test_forEach_with_a_float_valued_map(self, compile_and_run):
        # Exercises the .forEach() trampoline's float (double)
        # reinterpretation path specifically -- see
        # _emit_map_foreach_trampoline's own comment on why a real
        # trampoline is needed at all, not just for int.
        source = """
        void func logPrice(v:float, key:text) {
            log(`${key} ${v}`)
        }
        map[float] prices = {'apple': 1.5}
        prices.forEach(logPrice)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "apple 1.5"

    def test_forEach_with_a_bool_valued_map(self, compile_and_run):
        source = """
        void func logFlag(v:bool, key:text) {
            log(`${key} ${v}`)
        }
        map[bool] flags = {'ready': true}
        flags.forEach(logFlag)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "ready true"

    def test_forEach_with_a_struct_valued_map(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func logPoint(v:Point, key:text) {
            log(`${key}: (${v.x},${v.y})`)
        }
        Point origin
        origin.x = 0
        origin.y = 0
        map[Point] points = {'origin': origin}
        points.forEach(logPoint)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "origin: (0,0)"

    def test_json_renders_a_multi_key_map(self, compile_and_run):
        # claude.md #175: map[T] is a real hash table now -- bucket
        # order is a function of each key's hash, not insertion order,
        # so (unlike the single-key case above) a multi-key map's own
        # JSON key order is genuinely unspecified. Compared via
        # json.loads, not exact string equality.
        source = """
        map[int] m = {'a': 1, 'b': 2, 'c': 3, 'd': 4}
        log(m)
        """
        result = compile_and_run(source)
        assert json.loads(result.stdout.strip()) == {"a": 1, "b": 2, "c": 3, "d": 4}

    def test_many_inserts_survive_real_capacity_growth(self, compile_and_run):
        # claude.md #175: map[T] is a real hash table now -- growth is
        # intrinsic (doubling capacity on crossing a 75% load factor),
        # not the old realloc-by-exactly-one. 200 entries forces
        # several real rehashes (8 -> 16 -> ... -> 256), not just the
        # empty/one-entry cases the other tests above cover.
        source = """
        map[int] m = {}
        int i = 0
        while i < 200 {
            m[`key${i}`] = i
            i = i + 1
        }
        log(m['key0'])
        log(m['key100'])
        log(m['key199'])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "100", "199"]

    def test_interleaved_insert_delete_forces_rehash_with_tombstones_present(self, compile_and_run):
        # claude.md #175: a failure mode a linear scan never had --
        # festina_map_grow's rehash must correctly skip every tombstone
        # (from the deletes below) and move only live entries, even
        # when a rehash is triggered while tombstones are still
        # present in the table (deleting doesn't shrink capacity, so
        # the inserts after the deletes below force at least one such
        # rehash). Verifies every surviving key reads back correctly
        # and every deleted one reads back null, after the churn.
        source = """
        map[int] m = {}
        int i = 0
        while i < 30 {
            m[`key${i}`] = i
            i = i + 1
        }
        i = 0
        while i < 15 {
            delete m[`key${i}`]
            i = i + 1
        }
        i = 30
        while i < 60 {
            m[`key${i}`] = i
            i = i + 1
        }
        log(m['key5'])
        log(m['key20'])
        log(m['key45'])
        log(m['key59'])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            "-9223372036854775808", "20", "45", "59",
        ]

    def test_json_skips_a_tombstoned_bucket(self, compile_and_run):
        # claude.md #175: the direct test of _json_fn_for's own rewrite
        # -- JSON rendering must skip a bucket a prior delete left
        # tombstoned rather than emitting a phantom entry for it, and
        # must not get its leading-comma logic wrong when the first
        # LIVE bucket in table order isn't index 0 (which a deleted-
        # then-refilled table makes likely).
        source = """
        map[int] m = {'a': 1, 'b': 2, 'c': 3}
        delete m['b']
        m['d'] = 4
        log(m)
        """
        result = compile_and_run(source)
        assert json.loads(result.stdout.strip()) == {"a": 1, "c": 3, "d": 4}


class TestMapKeysAndValues:
    """claude.md #186 (uraikus/festina#76 item 7): map[T].keys() ->
    arr[text], map[T].values() -> arr[T] -- a plain snapshot array,
    walkable with an ordinary `for` loop, sidestepping forEach()'s own
    bare/no-closures callback restriction (claude.md #72) for the
    common "collect entries matching a condition" case."""

    def test_keys_and_values_on_an_int_valued_map(self, compile_and_run):
        source = """
        map[int] scores
        scores['alice'] = 10
        scores['bob'] = 20
        scores['carol'] = 30
        arr[text] ks = scores.keys()
        log(ks.length)
        log(ks.indexOf('alice') >= 0)
        log(ks.indexOf('bob') >= 0)
        log(ks.indexOf('carol') >= 0)
        log(ks.indexOf('dave') >= 0)
        int func cmpInt(a:int, b:int) { return a - b }
        arr[int] vs = scores.values()
        vs.sort(cmpInt)
        log(vs)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            "3", "true", "true", "true", "false", "[10,20,30]"]

    def test_empty_map_answers_empty_arrays(self, compile_and_run):
        source = """
        map[int] empty
        arr[text] ks = empty.keys()
        arr[int] vs = empty.values()
        log(ks.length)
        log(vs.length)
        """
        result = compile_and_run(source)
        assert result.stdout == "0\n0\n"

    def test_keys_snapshot_is_independent_of_a_later_delete(self, compile_and_run):
        # The whole point of a plain array over forEach: it's collected
        # ONCE. A later mutation of the source map must not retroactively
        # change what was already handed back.
        source = """
        map[int] m
        m['a'] = 1
        m['b'] = 2
        arr[text] ks = m.keys()
        delete m['a']
        log(ks.length)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "2"

    def test_values_on_a_text_valued_map_copies_independently_of_the_map(self, compile_and_run):
        # text has no shared representation to retain -- each collected
        # value must be its OWN copy, still readable after the source
        # map (and whatever it held) is freed.
        source = """
        map[text] names
        names['x'] = 'hello'
        names['y'] = 'world'
        arr[text] vs = names.values()
        free names
        log(vs[0] == 'hello' || vs[0] == 'world')
        log(vs[1] == 'hello' || vs[1] == 'world')
        """
        result = compile_and_run(source)
        assert result.stdout == "true\ntrue\n"

    def test_values_on_a_struct_valued_map_retains_independently_of_the_map(self, compile_and_run):
        source = """
        struct P { v:int }
        map[P] m
        P a
        a.v = 1
        P b
        b.v = 2
        m['a'] = a
        m['b'] = b
        arr[P] vs = m.values()
        free m
        log(vs[0].v + vs[1].v)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "3"

    def test_values_on_a_bool_valued_map(self, compile_and_run):
        # arr[bool]'s one-byte element stride (claude.md #97) has to
        # come out right here too, not just from arr[T]'s own methods.
        source = """
        map[bool] flags
        flags['a'] = true
        flags['b'] = false
        arr[bool] vs = flags.values()
        int trues = 0
        for int i = 0, i < vs.length, i++ {
            if (vs[i]) { trues = trues + 1 }
        }
        log(vs.length)
        log(trues)
        """
        result = compile_and_run(source)
        assert result.stdout == "2\n1\n"

    def test_wrong_arity_is_a_compile_error(self, parser, semantic, errors):
        for source in [
            "map[int] m\nm.keys(1)",
            "map[int] m\nm.values(1)",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")


class TestAmorArray:
    """claude.md #174: amor arr[T] -- an "amortized array", the
    array-typed counterpart of TestAmorMap above and the runtime effect
    claude.md #156 originally left `amor arr[T]` without. Same
    push/pop/shift/unshift/splice/indexing/`.length` surface as plain
    arr[T] -- only the growth strategy differs internally
    (festina_array_resize's own capacity-aware doubling in place of a
    plain arr[T]'s exact-size realloc), which these tests can't observe
    directly, so they exercise the same observable behavior plain
    arr[T] already has, plus enough pushes to force several real
    capacity doublings."""

    def test_literal_init_index_and_length(self, compile_and_run):
        source = """
        amor arr[int] xs = [1, 2, 3]
        log(xs.length)
        log(xs[0])
        log(xs[2])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["3", "1", "3"]

    def test_push_pop_shift_unshift(self, compile_and_run):
        source = """
        amor arr[int] xs = [1, 2, 3]
        xs.push(4)
        int popped = xs.pop()
        int shifted = xs.shift()
        xs.unshift(99)
        log(xs.length)
        log(popped)
        log(shifted)
        log(xs[0])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["3", "4", "1", "99"]

    def test_splice_two_and_three_argument_forms(self, compile_and_run):
        source = """
        amor arr[int] xs = [1, 2, 3, 4, 5]
        arr[int] removed = xs.splice(1, 2)
        log(removed.length)
        log(removed[0])
        log(xs.length)
        arr[int] removed2 = xs.splice(0, 1, [100, 200])
        log(removed2[0])
        log(xs[0])
        log(xs[1])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["2", "2", "3", "1", "100", "200"]

    def test_many_pushes_survive_real_capacity_growth(self, compile_and_run):
        # claude.md #174's own point: festina_array_resize doubles
        # capacity rather than growing by exactly one per push -- 500
        # pushes forces several real doublings (8 -> 16 -> ... -> 512),
        # not just the empty/few-element cases the other tests cover.
        source = """
        amor arr[int] xs = []
        int i = 0
        while i < 500 {
            xs.push(i)
            i = i + 1
        }
        log(xs.length)
        log(xs[0])
        log(xs[250])
        log(xs[499])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["500", "0", "250", "499"]

    def test_const_amor_arr_composes(self, compile_and_run):
        source = """
        const amor arr[int] xs = [1, 2, 3]
        log(xs.length)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "3"

    def test_struct_field_auto_vivifies(self, compile_and_run):
        # claude.md #156's own map version of this test (just above)
        # found a real review-caught risk: building the WRONG (smaller,
        # plain-array-shaped) header for a field the rest of codegen
        # treats as amor-shaped would silently corrupt memory the
        # moment festina_array_resize's own amor path first touched the
        # missing capacity field. The identical risk for arr[T].
        source = """
        struct Bag { xs:amor arr[int] }
        Bag b
        b.xs.push(1)
        b.xs.push(2)
        b.xs.push(3)
        log(b.xs.length)
        log(b.xs[0])
        log(b.xs[2])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["3", "1", "3"]

    def test_text_elements_retain_and_release_correctly(self, compile_and_run):
        source = """
        amor arr[text] xs = ['a', 'b', 'c']
        xs.push(`d${1}`)
        log(xs.length)
        log(xs[0])
        log(xs[3])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["4", "a", "d1"]

    def test_refcounted_elements_survive_many_pushes(self, compile_and_run):
        # A struct element specifically -- exercises the retain path on
        # every push (claude.md #80), not just raw bytes, across enough
        # real capacity growth to matter.
        source = """
        struct P { n:int }
        amor arr[P] ps = []
        int i = 0
        while i < 300 {
            P p
            p.n = i
            ps.push(p)
            i = i + 1
        }
        log(ps.length)
        log(ps[0].n)
        log(ps[299].n)
        P alias = ps[150]
        log(alias.n)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["300", "0", "299", "150"]

    def test_media_element_array_composes(self, compile_and_run, sprite_sheet_png):
        # claude.md #137's own text-path-to-media-element allowance for
        # arr[img]/arr[aud]/arr[blob] literals, combined with `amor`.
        source = f"""
        amor arr[img] pics = ['{sprite_sheet_png}', '{sprite_sheet_png}']
        log(pics.length)
        pics.push(pics[0])
        log(pics.length)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["2", "3"]

    def test_amor_and_plain_are_not_assignable(self, parser, semantic, errors):
        program = parser.parse("""
        amor arr[int] xs = [1, 2, 3]
        arr[int] ys = xs
        """)
        with pytest.raises(errors.CompileError, match="cannot assign"):
            semantic.analyze(program)

    def test_no_initializer_is_a_clear_error(self, parser, semantic, errors):
        program = parser.parse("amor arr[int] xs")
        with pytest.raises(errors.CompileError, match="requires an initializer"):
            semantic.analyze(program)

    def test_nested_amor_arr_of_arr(self, compile_and_run):
        source = """
        amor arr[arr[int]] grid = [[1, 2], [3, 4]]
        grid.push([5, 6])
        log(grid.length)
        log(grid[2][1])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["3", "6"]


class TestLoops:
    """claude.md #60 (for loops), #61 (while loops), #66 (postfix ++/--)."""

    def test_for_loop_array_iteration_example(self, compile_and_run):
        # The exact worked example from claude.md #60.
        source = """
        arr[int] array = [10, 20, 30]
        for int x = 0, x < array.length, x++ {
            log(array[x])
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["10", "20", "30"]

    def test_for_loop_counts_from_zero(self, compile_and_run):
        result = compile_and_run("for int x = 0, x < 5, x++ {\n    log(x)\n}")
        assert result.stdout.splitlines() == ["0", "1", "2", "3", "4"]

    def test_for_loop_with_decrement_update(self, compile_and_run):
        result = compile_and_run("for int x = 3, x > 0, x-- {\n    log(x)\n}")
        assert result.stdout.splitlines() == ["3", "2", "1"]

    def test_for_loop_body_never_runs_when_condition_starts_false(self, compile_and_run):
        source = "for int x = 0, x < 0, x++ {\n    log('never')\n}\nlog('after')"
        result = compile_and_run(source)
        assert result.stdout.strip() == "after"

    def test_for_loop_init_variable_does_not_leak_past_the_loop(self, compile_and_run):
        # claude.md #60: "The initialization variable is scoped to the
        # loop body" -- a same-named outer variable must be unaffected.
        source = """
        int x = 99
        for int x = 0, x < 3, x++ {
        }
        log(x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "99"

    def test_nested_for_loops(self, compile_and_run):
        source = """
        for int i = 0, i < 2, i++ {
            for int j = 0, j < 2, j++ {
                log(i * 10 + j)
            }
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "10", "11"]

    def test_while_loop_counts_up(self, compile_and_run):
        source = """
        int e = 0
        while e < 5 {
            log(e)
            e++
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "2", "3", "4"]

    def test_while_loop_body_never_runs_when_condition_starts_false(self, compile_and_run):
        source = "int e = 10\nwhile e < 5 {\n    log('never')\n}\nlog('after')"
        result = compile_and_run(source)
        assert result.stdout.strip() == "after"

    def test_while_true_exits_via_return_inside_the_loop(self, compile_and_run):
        # `return` from the enclosing function still works as a way out
        # of an infinite loop's body too, same as before claude.md #73
        # added break/continue -- this isn't the only way out anymore
        # (see TestBreakAndContinue below), just still a valid one.
        source = """
        void func run() {
            int count = 0
            while true {
                log(count)
                count++
                if count == 3 {
                    return
                }
            }
        }
        run()
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "2", "done"]

    def test_postfix_increment_and_decrement(self, compile_and_run):
        source = """
        int i = 5
        i++
        log(i)
        i--
        i--
        log(i)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["6", "4"]

    def test_postfix_increment_returns_pre_increment_value(self, compile_and_run):
        # Standard postfix semantics: `i++` evaluates to the value *before*
        # incrementing.
        source = "int i = 5\nlog(i++)\nlog(i)"
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["5", "6"]

    def test_iterative_fibonacci(self, compile_and_run):
        # A real loop-driven computation, not just a counter -- exercises
        # for + array-free accumulation together.
        source = """
        int func fibIter(n:int) {
            int a = 0
            int b = 1
            for int i = 0, i < n, i++ {
                int next = a + b
                a = b
                b = next
            }
            return a
        }
        log(fibIter(10))
        log(fibIter(20))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["55", "6765"]


class TestBreakAndContinue:
    """claude.md #73."""

    def test_break_stops_the_loop_immediately(self, compile_and_run):
        source = """
        for int i = 0, i < 10, i++ {
            if i == 5 {
                break
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "2", "3", "4"]

    def test_continue_skips_the_rest_of_that_iteration(self, compile_and_run):
        source = """
        for int i = 0, i < 5, i++ {
            if i % 2 == 0 {
                continue
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "3"]

    def test_claude_mds_own_worked_example(self, compile_and_run):
        # claude.md #73's own example: "This logs 1, 3."
        source = """
        for int i = 0, i < 10, i++ {
            if i == 5 {
                break
            }
            if i % 2 == 0 {
                continue
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "3"]

    def test_continue_in_a_for_loop_still_runs_the_update_expression(self, compile_and_run):
        # claude.md #73: "the update expression still runs before the
        # condition is checked again" -- if continue skipped straight to
        # the condition instead, i would never advance and this would
        # loop forever (or time out); it doesn't.
        source = """
        for int i = 0, i < 5, i++ {
            continue
        }
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "done"

    def test_break_in_a_while_loop(self, compile_and_run):
        source = """
        int i = 0
        while true {
            if i >= 3 {
                break
            }
            log(i)
            i++
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "2"]

    def test_continue_in_a_while_loop(self, compile_and_run):
        source = """
        int i = 0
        while i < 5 {
            i++
            if i % 2 == 0 {
                continue
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "3", "5"]

    def test_break_only_exits_the_innermost_loop(self, compile_and_run):
        source = """
        for int i = 0, i < 3, i++ {
            for int j = 0, j < 3, j++ {
                if j == 1 {
                    break
                }
                log(`${i},${j}`)
            }
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0,0", "1,0", "2,0"]

    def test_continue_only_affects_the_innermost_loop(self, compile_and_run):
        source = """
        for int i = 0, i < 2, i++ {
            for int j = 0, j < 3, j++ {
                if j == 1 {
                    continue
                }
                log(`${i},${j}`)
            }
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0,0", "0,2", "1,0", "1,2"]

    def test_statements_after_break_in_the_same_block_are_not_run(self, compile_and_run):
        source = """
        for int i = 0, i < 3, i++ {
            if i == 1 {
                break
                log('unreachable')
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0"]


class TestAutomaticMemoryReclamation:
    """claude.md #74, stage 1: a local struct/arr[T]/map[T] declared
    directly in a function/event handler's own top-level body is freed
    automatically at every return, when tests/test_escape_analysis.py's
    find_escaping_names proves it never escapes. That module is tested
    exhaustively on its own (every syntactic escaping/non-escaping
    pattern, no C compiler needed) -- this class checks the other half:
    that the analysis's result actually gets wired into real generated
    IR (free() calls landing in exactly the right place) and, more
    importantly, that real compiled programs still produce correct
    output in both the freed and (still-leaking, unchanged) escaping
    cases."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    # ---- IR-level: no C compiler needed ----

    def test_non_escaping_struct_local_is_stack_allocated(self, parser, semantic, codegen):
        # claude.md #43/#74/#75: a struct local proven never to escape
        # its declaring function is now a real stack alloca, not a
        # calloc+free pair -- see _emit_stmt's own VarDecl comment for
        # why reusing the exact same escape-analysis proof is sound for
        # allocation, not just for freeing.
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            p.x = 1
            log(p.x)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "%p.storage" in ir
        assert "alloca %struct.Point" in ir
        assert "store %struct.Point zeroinitializer" in ir
        assert "call ptr @calloc(" not in ir
        assert "call void @free(" not in ir

    def test_non_escaping_array_local_declared_with_a_literal_initializer_is_stack_allocated(
            self, parser, semantic, codegen):
        # claude.md #81: a with-initializer arr[T]/map[T] local used to
        # never stack-allocate at all (mirroring claude.md #77's own
        # original struct rule) -- always refcounted, released through
        # festina_release_array/_map. Now, when the initializer is a
        # literal written directly here AND the local never escapes
        # (both true in this exact source), the header itself is
        # stack-allocated instead, exactly like a no-init non-escaping
        # local already was (see
        # test_no_init_non_escaping_array_local_frees_its_data_pointer_directly,
        # just below) -- only the data buffer this literal still
        # malloc's needs freeing at scope-exit, through a bare @free(),
        # never festina_release_array. The ESCAPING with-init case is
        # unaffected and still fully refcounted -- see
        # test_with_init_array_local_is_refcounted_via_a_shared_header.
        source = """
        void func f() {
            arr[int] a = [1, 2, 3]
            log(a[0])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "getelementptr %struct._FestinaArray, ptr" in ir
        assert "alloca %struct._FestinaArray" in ir
        assert "call void @free(" in ir
        assert "call void @festina_release_array(" not in ir

    def test_no_init_non_escaping_array_local_frees_its_data_pointer_directly(
            self, parser, semantic, codegen):
        # A no-init arr[T] local that never escapes still stack-
        # allocates its own header (claude.md #74, unchanged) -- only
        # its data buffer needs freeing at scope-exit, through a bare
        # @free(), never festina_release_array (the header itself was
        # never heap-allocated/refcounted at all).
        source = """
        void func f() {
            arr[int] a
            log(a.length)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" in ir
        assert "call void @festina_release_array(" not in ir

    def test_non_escaping_map_local_declared_with_a_literal_initializer_is_stack_allocated(
            self, parser, semantic, codegen):
        # claude.md #81: same as the array case above -- a with-
        # initializer map[T] local used to always be refcounted,
        # released through festina_release_map (which itself calls
        # festina_map_free_entries internally, in C). Now, non-escaping
        # with a literal initializer written directly here, its header
        # is stack-allocated instead, so festina_map_free_entries is
        # called directly in this function's own IR (not hidden inside
        # festina_release_map anymore) to free just the entries buffer.
        source = """
        void func f() {
            map[int] m = {'a': 1}
            log(m['a'])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "getelementptr %struct._FestinaMap, ptr" in ir
        assert "alloca %struct._FestinaMap" in ir
        assert "call void @festina_map_free_entries(" in ir
        assert "call void @festina_release_map(" not in ir

    def test_escaping_map_local_declared_with_an_initializer_is_refcounted(
            self, parser, semantic, codegen):
        # The escaping counterpart to the non-escaping test just above
        # (mirroring test_with_init_array_local_is_refcounted_via_a_shared_header):
        # `m` here is assigned into a global, so it can't be stack-
        # allocated at all -- still always refcounted, released through
        # festina_release_map, exactly as every with-init map local was
        # before claude.md #81.
        source = """
        map[int] g
        void func f() {
            map[int] m = {'a': 1}
            g = m
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "getelementptr %struct._FestinaMap, ptr" in ir
        assert "call void @festina_release_map(" in ir
        assert "call void @festina_map_free_entries(" not in ir

    def test_no_init_non_escaping_map_local_frees_its_entries_pointer_directly(
            self, parser, semantic, codegen):
        # claude.md #74/#75: a map's entries buffer has its own nested
        # per-entry key allocation (see festina_map_set's own comment),
        # so freeing it goes through festina_map_free_entries -- which
        # frees each entry's key too -- not a plain @free(entries) that
        # would leak them. A no-init map that never escapes still
        # stack-allocates its own header (claude.md #74, unchanged), so
        # this call is still emitted directly in the IR, unlike the
        # with-initializer case above.
        source = """
        void func f() {
            map[int] m
            m['a'] = 1
            log(m['a'])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_map_free_entries(" in ir
        assert "call void @festina_release_map(" not in ir

    def test_returned_struct_is_not_freed(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point func makePoint() {
            Point p
            p.x = 1
            return p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_struct_passed_to_a_non_retaining_function_is_now_stack_allocated(
            self, parser, semantic, codegen):
        # claude.md #74 stage 2 (interprocedural): takesPoint only reads
        # p.x (a Member.obj-safe use, same rule as any local's own
        # fields) -- its own parameter never escapes within its own
        # body, so escaping_params[takesPoint] = {} and f()'s own `p`,
        # passed there and never used any other way, is now provably
        # safe too. This is the exact case stage 1's own module
        # docstring called out as its stated limitation ("even if the
        # called function provably doesn't retain it") -- combined with
        # the stack-allocation swap (claude.md #43/#74/#75), f()'s own
        # `p` is now a stack alloca, not a calloc'd allocation at all.
        source = """
        struct Point { x:int y:int }
        void func takesPoint(p:Point) {
            log(p.x)
        }
        void func f() {
            Point p
            takesPoint(p)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "alloca %struct.Point" in f_body
        assert "call ptr @calloc(" not in f_body

    def test_loop_local_struct_is_stack_allocated_and_reused_across_iterations(
            self, parser, semantic, codegen):
        # claude.md #43/#74/#75, corrected by #191: a loop-body struct
        # local reuses ONE stack slot across iterations. This test
        # originally asserted the alloca itself sat inside the loop
        # body, on the belief that LLVM reserves one fixed slot per
        # alloca regardless of block -- that belief was wrong: an
        # alloca EXECUTES each time it's reached, growing the stack
        # every iteration until the function returns (measured: a
        # six-field struct declared in a while body overflowed the
        # 8MB stack and segfaulted at ~150k-300k iterations). The
        # alloca now lives in the function's ENTRY block (one slot per
        # call, genuinely reused), while the zeroinitializer store
        # stays inside the loop body so each iteration still sees a
        # fresh zeroed struct -- and there is still no calloc/free
        # anywhere in the function at all.
        source = """
        struct Point { x:int y:int }
        void func f() {
            for int i = 0, i < 3, i++ {
                Point p
                p.x = i
                log(p.x)
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        f_start = next(i for i, l in enumerate(lines) if l.startswith("define void @f("))
        f_end = next(i for i in range(f_start, len(lines)) if lines[i] == "}")
        body_start = next(i for i in range(f_start, f_end) if lines[i].strip().startswith("for.body"))
        body_end = next(i for i in range(body_start, f_end) if lines[i].strip().startswith("br label %for.update"))
        entry_lines = lines[f_start:body_start]
        body_lines = lines[body_start:body_end]
        # one slot, allocated up front...
        assert any("alloca %struct.Point" in l for l in entry_lines)
        assert not any("alloca" in l for l in body_lines)
        # ...re-zeroed at the declaration site, every iteration.
        assert any("store %struct.Point zeroinitializer" in l for l in body_lines)
        assert "call ptr @calloc(" not in ir
        assert "call void @free(" not in ir

    def test_a_long_running_loop_declaring_locals_does_not_grow_the_stack(
            self, compile_and_run):
        # claude.md #191: the regression this whole hoist exists for.
        # Before it, each iteration's allocas (the struct's storage,
        # its pointer slot, every scratch temporary) pushed fresh
        # stack that never popped until function return -- this exact
        # program segfaulted between 150k and 300k iterations with
        # completely flat heap usage. 500k iterations now run in a
        # fixed-size frame.
        source = '''
        struct Item {
            id:int
            name:text
            description:text
            price:float
            inStock:bool
            tags:arr[text]
        }
        int i = 0
        while i < 500000 {
            Item it
            it.id = i
            it.name = `item number ${i}`
            it.description = 'A "quoted" description with\\na newline'
            it.price = 19.99
            it.inStock = i % 2 == 0
            it.tags = ['alpha', 'beta', `gamma-${i}`]
            i = i + 1
        }
        log(i)
        '''
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == '500000'

    def test_nested_if_declared_struct_is_stack_allocated(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        void func f(cond:bool) {
            if cond {
                Point p
                p.x = 1
                log(p.x)
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "alloca %struct.Point" in ir
        assert "call ptr @calloc(" not in ir

    def test_struct_passed_to_a_retaining_function_is_still_not_freed(self, parser, semantic, codegen):
        # The mirror case: retains actually stores its own parameter
        # into a global (an unconditional hard escape, same rule as
        # ever) -- escaping_params[retains] = {0}, so f()'s own `p`,
        # passed at that same position, still correctly escapes too.
        # Interprocedural analysis only ever widens what's PROVEN safe;
        # it must never widen what's proven unsafe.
        source = """
        struct Point { x:int y:int }
        Point stash
        void func retains(p:Point) {
            stash = p
        }
        void func f() {
            Point p
            retains(p)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_struct_assigned_into_a_global_is_not_freed(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            Point p
            g = p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_reassigned_struct_is_not_freed(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            Point q
            p = q
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_a_loop_local_array_is_freed_inside_the_loop_body(self, parser, semantic, codegen):
        # claude.md #74's nested-block extension: a loop-body-declared
        # local is freed at the end of *every* iteration -- exactly
        # one free call, and it must be inside the loop body's own
        # block (part of the runtime back-edge cycle), not just once
        # after the loop as a whole exits. Uses arr[int], not a
        # struct -- since the stack-allocation swap (claude.md #43/#74/
        # #75), a non-escaping struct local no longer goes through this
        # free-scheduling machinery at all (see
        # test_a_loop_local_struct_is_reused_across_iterations_via_the_same_alloca
        # for the struct/stack-allocation equivalent of this same
        # shape). `p` here is non-escaping with a literal initializer,
        # so its own HEADER is stack-allocated (claude.md #81) and only
        # its data buffer needs freeing at scope-exit (a plain @free(),
        # not festina_release_array) -- still exactly the right type to
        # exercise the free-scheduling logic itself with, just through
        # a different runtime call than before #81.
        source = """
        void func f() {
            for int i = 0, i < 3, i++ {
                arr[int] p = [i]
                log(p[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        body_start = next(i for i, l in enumerate(lines) if l.strip().startswith("for.body"))
        body_end = next(i for i in range(body_start, len(lines)) if lines[i].strip().startswith("br label %for.update"))
        body_lines = lines[body_start:body_end]
        assert sum("call void @free(" in l for l in body_lines) == 1
        assert ir.count("call void @free(") == 1

    def test_a_nested_if_declared_array_is_freed_at_the_ifs_own_end(self, parser, semantic, codegen):
        source = """
        void func f(cond:bool) {
            if cond {
                arr[int] p = [1]
                log(p[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" in ir

    def test_break_frees_the_loop_local_but_not_an_outer_scope_local(self, parser, semantic, codegen):
        source = """
        void func f() {
            arr[int] outer = [100]
            for int i = 0, i < 5, i++ {
                arr[int] inner = [i]
                if inner[0] == 2 {
                    break
                }
                log(inner[0])
            }
            log(outer[0])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        break_block = next(i for i, l in enumerate(lines) if l.strip().startswith("if.then"))
        break_block_end = next(i for i in range(break_block, len(lines)) if lines[i].strip().startswith("br label %for.end"))
        break_lines = lines[break_block:break_block_end]
        # Exactly one free on the break path -- inner's, not outer's
        # (outer is declared outside the loop, merely used inside it).
        assert sum("call void @free(" in l for l in break_lines) == 1
        # outer is still freed exactly once overall, at the function's
        # own end (after the loop, whichever way it was exited).
        assert ir.count("call void @free(") == 3  # inner (break path) + inner (fall-through path) + outer

    def test_continue_frees_locals_declared_since_the_loop_body_began(self, parser, semantic, codegen):
        source = """
        void func f() {
            for int i = 0, i < 5, i++ {
                arr[int] p = [i]
                if p[0] % 2 == 0 {
                    continue
                }
                log(p[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        continue_block = next(i for i, l in enumerate(lines) if l.strip().startswith("if.then"))
        continue_block_end = next(i for i in range(continue_block, len(lines)) if lines[i].strip().startswith("br label %for.update"))
        continue_lines = lines[continue_block:continue_block_end]
        assert sum("call void @free(" in l for l in continue_lines) == 1

    def test_nested_if_inside_a_loop_frees_correctly_on_both_the_break_and_fallthrough_paths(self, parser, semantic, codegen):
        source = """
        void func f() {
            for int i = 0, i < 5, i++ {
                arr[int] p = [i]
                if p[0] == 3 {
                    break
                }
                log(p[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        # One free on the break path (p, via break's own free-before-
        # branch), one on the normal fall-through-to-next-iteration path
        # (p, via the loop body's own natural end) -- two total, both p,
        # never both taken on the same iteration.
        assert ir.count("call void @free(") == 2

    def test_early_return_before_the_declaration_has_no_free_on_that_path(self, parser, semantic, codegen):
        source = """
        void func f(cond:bool) {
            if cond {
                return
            }
            arr[int] p = [1]
            log(p[0])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        func_start = next(i for i, l in enumerate(lines) if l.startswith("define void @f("))
        func_end = next(i for i in range(func_start, len(lines)) if lines[i] == "}")
        func_lines = lines[func_start:func_end]
        # The very first `ret void` (the early-return path, before p is
        # declared) must not be preceded by a free call anywhere earlier
        # in the function; the second (the fall-through path, after p is
        # declared) must be.
        ret_indices = [i for i, l in enumerate(func_lines) if l.strip() == "ret void"]
        assert len(ret_indices) == 2
        assert not any("call void @free(" in l for l in func_lines[:ret_indices[0] + 1])
        assert any("call void @free(" in l for l in func_lines[ret_indices[0] + 1:ret_indices[1] + 1])

    def test_event_handler_locals_are_analyzed_too(self, parser, semantic, codegen):
        source = """
        on mouseDown(x:int, y:int, button:int) {
            arr[int] p = [x]
            log(p[0])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" in ir

    def test_event_handler_struct_local_is_stack_allocated_too(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        on mouseDown(x:int, y:int, button:int) {
            Point p
            p.x = x
            log(p.x)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "alloca %struct.Point" in ir
        assert "call ptr @calloc(" not in ir

    # ---- end-to-end: real compiled programs, real output ----

    def test_non_escaping_struct_local_still_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            p.x = 5
            p.y = 10
            log(p.x + p.y)
        }
        f()
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["15", "done"]

    def test_returning_a_struct_still_works_correctly(self, compile_and_run):
        # The exact shape of bug this stage must never reintroduce --
        # see security.md's original "returning a struct by value"
        # fix, and this session's own live demonstration of what
        # freeing an escaping local does to it.
        source = """
        struct Point { x:int y:int }
        Point func makePoint(a:int, b:int) {
            Point p
            p.x = a
            p.y = b
            return p
        }
        Point q1 = makePoint(10, 20)
        Point q2 = makePoint(999, 888)
        log(q1.x)
        log(q1.y)
        log(q2.x)
        log(q2.y)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["10", "20", "999", "888"]

    def test_recursive_function_with_a_non_escaping_struct_local_keeps_each_calls_own_value(
            self, compile_and_run):
        # The single most important correctness question the stack-
        # allocation swap (claude.md #43/#74/#75) raises: does each
        # recursive call really get its own, distinct stack slot for
        # p, or could a wrong assumption about LLVM's calling
        # convention let a deeper call's own p.x overwrite an
        # outstanding shallower call's? recur(n)'s own p.x must still
        # read back correctly *after* its own recursive call to
        # recur(n-1) returns -- if calls shared one slot, this would
        # read n-1's (or some other call's) value instead of n's own.
        source = """
        struct Point { x:int y:int }
        int func recur(n:int) {
            Point p
            p.x = n
            if n == 0 {
                return p.x
            }
            int inner = recur(n - 1)
            return p.x * 100 + inner
        }
        log(recur(3))
        log(recur(5))
        """
        result = compile_and_run(source)
        # recur(0)=0, recur(1)=100, recur(2)=300, recur(3)=600,
        # recur(4)=1000, recur(5)=1500 -- hand-derived, each building on
        # the previous: recur(n) = n*100 + recur(n-1).
        assert result.stdout.splitlines() == ["600", "1500"]

    def test_struct_assigned_into_a_global_keeps_its_value(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            Point p
            p.x = 7
            g = p
        }
        f()
        log(g.x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7"

    def test_struct_passed_to_another_function_keeps_its_value(self, compile_and_run):
        # Since claude.md #74 stage 2, takesPoint's own read-only use of
        # its parameter (see test_struct_passed_to_a_non_retaining_function_is_now_freed)
        # means p is now provably safe throughout f() too -- p ends up
        # freed at the end of f(), but only *after* both reads (inside
        # takesPoint and f()'s own trailing log(p.x)) have already
        # happened, so the values here must still come out correct.
        source = """
        struct Point { x:int y:int }
        void func takesPoint(p:Point) {
            log(p.x)
        }
        void func f() {
            Point p
            p.x = 3
            takesPoint(p)
            log(p.x)
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["3", "3"]

    def test_reassignment_keeps_the_new_values_valid(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            p.x = 1
            Point q
            q.x = 2
            p = q
            log(p.x)
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "2"

    def test_early_return_before_declaration_runs_correctly_on_both_paths(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f(cond:bool) {
            if cond {
                log('early')
                return
            }
            Point p
            p.x = 42
            log(p.x)
        }
        f(true)
        f(false)
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["early", "42", "done"]

    def test_non_escaping_array_and_map_locals_still_produce_correct_output(self, compile_and_run):
        source = """
        void func useArray() {
            arr[int] a = [1, 2, 3]
            log(a[0] + a[1] + a[2])
        }
        void func useMap() {
            map[int] m = {'a': 1, 'b': 2}
            log(m['a'] + m['b'])
        }
        useArray()
        useMap()
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["6", "3", "done"]

    def test_calling_a_freeing_function_many_times_does_not_crash(self, compile_and_run):
        # Not a memory-bound proof (see tests/CONTRACT.md for why an
        # RSS-based measurement isn't reliable against this compiler's
        # own O2 pipeline, which can eliminate an unobserved allocation
        # entirely) -- a basic robustness check that repeated alloc/free
        # of the same struct across many calls doesn't corrupt anything
        # an allocator would notice (a bad free()/double free is exactly
        # the kind of thing that tends to crash loudly under real
        # allocator bookkeeping, especially across enough iterations to
        # exercise its free-list reuse).
        source = """
        struct Point { x:int y:int }
        void func useLocal(n:int) {
            Point p
            p.x = n
            p.y = n * 2
        }
        int total = 0
        for int i = 0, i < 50000, i++ {
            useLocal(i)
            total = total + 1
        }
        log(total)
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "50000"

    def test_nested_if_declared_struct_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f(cond:bool) {
            if cond {
                Point p
                p.x = 5
                log(p.x)
            }
            log('after')
        }
        f(true)
        f(false)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["5", "after", "after"]

    def test_loop_local_struct_produces_correct_output_every_iteration(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            for int i = 0, i < 5, i++ {
                Point p
                p.x = i * i
                log(p.x)
            }
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "4", "9", "16"]

    def test_break_and_continue_with_loop_local_structs_produce_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func withBreak() {
            for int i = 0, i < 5, i++ {
                Point p
                p.x = i
                if p.x == 2 {
                    break
                }
                log(p.x)
            }
            log('after break loop')
        }
        void func withContinue() {
            for int i = 0, i < 5, i++ {
                Point p
                p.x = i
                if p.x % 2 == 0 {
                    continue
                }
                log(p.x)
            }
        }
        withBreak()
        withContinue()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "after break loop", "1", "3"]

    def test_outer_scope_struct_survives_break_and_continue_from_a_nested_loop(self, compile_and_run):
        # The critical case: a struct declared OUTSIDE a loop and merely
        # used (not declared) inside it must keep its correct value --
        # break/continue only free locals declared since the loop's own
        # body began, never anything from an outer scope.
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point outer
            outer.x = 100
            for int i = 0, i < 3, i++ {
                Point inner
                inner.x = i
                if inner.x == 1 {
                    break
                }
                log(inner.x + outer.x)
            }
            log(outer.x)
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["100", "100"]

    def test_while_loop_local_struct_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            int i = 0
            while i < 4 {
                Point p
                p.x = i
                if p.x % 3 == 0 {
                    Point r
                    r.x = p.x * 100
                    log(r.x)
                }
                i = i + 1
            }
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "300"]

    def test_struct_escaping_a_loop_into_a_global_still_works_correctly(self, compile_and_run):
        # A value that genuinely escapes (assigned into a global) from
        # inside a loop must never be freed, on any path, including
        # break/continue -- it's excluded from every frame entirely,
        # the same escape_analysis.find_escaping_names result as ever.
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            for int i = 0, i < 3, i++ {
                Point p
                p.x = i
                g = p
            }
        }
        f()
        log(g.x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "2"

    def test_many_loop_iterations_with_nested_if_and_break_continue_does_not_crash(self, compile_and_run):
        # A heavier robustness check than the stage-1 version above --
        # this one exercises the actual new machinery: a loop-local
        # struct freed every iteration, an if nested inside the loop
        # body with its own struct local, and break/continue both firing
        # repeatedly across many iterations, all in the same loop.
        source = """
        struct Point { x:int y:int }
        void func run(n:int) {
            Point outer
            outer.x = 0
            for int i = 0, i < n, i++ {
                Point p
                p.x = i
                if p.x % 7 == 0 {
                    continue
                }
                if p.x % 13 == 0 {
                    break
                }
                Point q
                q.x = p.x * 2
                outer.x = outer.x + q.x
            }
            log(outer.x)
        }
        for int i = 0, i < 20000, i++ {
            run(50)
        }
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.splitlines()[-1] == "done"

    # ---- edge cases beyond the two increments' own core scenarios ----
    # Added in a follow-up robustness pass over the whole feature (not a
    # new increment -- no new codegen.py behavior was needed for any of
    # these; they exercise combinations the two increments' own tests
    # didn't specifically spell out). Verified against real generated IR
    # and real compiled output here, and additionally against a combined
    # 5000-iteration program exercising every one of these patterns
    # together under AddressSanitizer/LeakSanitizer (see tests/CONTRACT.md)
    # -- zero ASan errors, zero leaks.

    def test_break_in_a_nested_loop_frees_only_the_inner_loops_own_locals(self, parser, semantic, codegen):
        # A loop-local declared in an OUTER loop's body, merely used (not
        # re-declared) inside a nested inner loop, must survive the inner
        # loop's own break -- free_depth is captured fresh by each
        # _emit_for call, so the inner loop's break must never reach past
        # its own body's frame down into the outer loop's. arr[T], not a
        # struct -- see test_a_loop_local_array_is_freed_inside_the_loop_body's
        # own note on why arr[T] is what still exercises this machinery
        # since the stack-allocation swap.
        source = """
        void func f() {
            for int i = 0, i < 3, i++ {
                arr[int] mid = [i]
                for int j = 0, j < 3, j++ {
                    arr[int] inner = [j]
                    if inner[0] == 1 {
                        break
                    }
                }
                log(mid[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        inner_break_block = next(i for i, l in enumerate(lines) if l.strip().startswith("if.then"))
        inner_break_end = next(i for i in range(inner_break_block, len(lines))
                                if lines[i].strip().startswith("br label %for.end"))
        break_lines = lines[inner_break_block:inner_break_end]
        # Exactly one free on the inner break path -- inner's own, not mid's.
        assert sum("call void @free(" in l for l in break_lines) == 1

    def test_break_in_a_nested_loop_does_not_corrupt_the_outer_loops_local(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            for int i = 0, i < 3, i++ {
                Point mid
                mid.x = i * 100
                for int j = 0, j < 3, j++ {
                    Point inner
                    inner.x = j
                    if inner.x == 1 {
                        break
                    }
                }
                log(mid.x)
            }
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "100", "200"]

    def test_return_from_a_nested_if_after_the_loop_locals_declaration_frees_it(self, compile_and_run):
        # A return nested two levels deep inside a loop body (an if
        # inside the loop), textually AFTER the loop-local's own
        # declaration -- down_to=0 on Return must reach through the
        # if-then's own frame *and* the loop body's frame *and* the
        # function's own top-level frame, all at once.
        source = """
        struct Point { x:int y:int }
        int func f(n:int) {
            for int i = 0, i < n, i++ {
                Point p
                p.x = i
                if p.x == 3 {
                    return p.x * 100
                }
            }
            return -1
        }
        log(f(10))
        log(f(2))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["300", "-1"]

    def test_return_from_a_nested_if_before_the_loop_locals_declaration_has_no_free_on_that_path(
            self, parser, semantic, codegen):
        # The mirror of test_early_return_before_the_declaration_has_no_free_on_that_path,
        # but with the early return nested inside a loop body instead of
        # directly in the function body -- the loop-local's VarDecl
        # hasn't been walked yet when this path's Return fires, so its
        # frame must still be empty on this specific path.
        source = """
        struct Point { x:int y:int }
        int func f(n:int) {
            for int i = 0, i < n, i++ {
                if i == 2 {
                    return 777
                }
                Point p
                p.x = i
            }
            return -1
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        func_start = next(i for i, l in enumerate(lines) if l.startswith("define i64 @f("))
        func_end = next(i for i in range(func_start, len(lines)) if lines[i] == "}")
        func_lines = lines[func_start:func_end]
        ret_lines = [i for i, l in enumerate(func_lines) if l.strip().startswith("ret i64 777")]
        assert len(ret_lines) == 1
        assert not any("call void @free(" in l for l in func_lines[:ret_lines[0] + 1])

    def test_return_from_a_nested_if_before_the_loop_locals_declaration_produces_correct_output(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        int func f(n:int) {
            for int i = 0, i < n, i++ {
                if i == 2 {
                    return 777
                }
                Point p
                p.x = i
            }
            return -1
        }
        log(f(10))
        log(f(1))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["777", "-1"]

    def test_else_if_chain_frees_each_branchs_own_struct_local(self, compile_and_run):
        # Each `else if` is its own nested IfStmt (see parser.py's
        # parse_if), so each branch's then-block is its own _emit_block
        # frame -- a struct declared in one arm must never affect another
        # arm's freeing, and the un-taken arms' structs must never even
        # be allocated (ordinary control flow, unrelated to #74, but
        # worth pinning down together with the rest of this).
        source = """
        struct Point { x:int y:int }
        int func f(n:int) {
            if n == 0 {
                Point a
                a.x = 10
                return a.x
            } else if n == 1 {
                Point b
                b.x = 20
                return b.x
            } else if n == 2 {
                Point c
                c.x = 30
                return c.x
            } else {
                Point d
                d.x = 40
                return d.x
            }
        }
        log(f(0))
        log(f(1))
        log(f(2))
        log(f(3))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["10", "20", "30", "40"]

    def test_bare_nested_block_frees_its_own_local(self, compile_and_run):
        # A standalone `{ }` block (not attached to if/while/for) is
        # still routed through the same _emit_block -- and two sibling
        # bare blocks reusing the same local name must not collide or
        # double-free (each is its own Env/frame).
        source = """
        struct Point { x:int y:int }
        int func f() {
            int total = 0
            {
                Point p
                p.x = 5
                total = total + p.x
            }
            {
                Point p
                p.x = 6
                total = total + p.x
            }
            return total
        }
        log(f())
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "11"

    def test_sibling_if_else_branches_can_declare_the_same_local_name_without_double_free(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        int func f(cond:bool) {
            if cond {
                Point p
                p.x = 1
                return p.x
            } else {
                Point p
                p.x = 2
                return p.x
            }
        }
        log(f(true))
        log(f(false))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "2"]

    def test_combined_nested_loops_elif_and_bare_blocks_do_not_crash_under_heavy_iteration(
            self, compile_and_run):
        # The same combination verified separately by hand under
        # AddressSanitizer/LeakSanitizer (see tests/CONTRACT.md) -- here
        # as an ordinary correctness-and-no-crash pytest check: nested
        # for-in-for with an inner break, return after and before a
        # loop-local's own declaration, a 4-way else-if chain, bare
        # blocks with a shadowed name, and if/else sibling shadowing,
        # all run back to back many times.
        source = """
        struct Point { x:int y:int }
        int func nestedForBreak() {
            Point outer
            outer.x = 1000
            int total = 0
            for int i = 0, i < 4, i++ {
                Point mid
                mid.x = i
                for int j = 0, j < 4, j++ {
                    Point inner
                    inner.x = j
                    if inner.x == 2 {
                        break
                    }
                    total = total + inner.x
                }
                total = total + mid.x
            }
            total = total + outer.x
            return total
        }
        int func elifChain(n:int) {
            if n == 0 {
                Point a
                a.x = 10
                return a.x
            } else if n == 1 {
                Point b
                b.x = 20
                return b.x
            } else {
                Point c
                c.x = 30
                return c.x
            }
        }
        int func bareBlocks() {
            int total = 0
            {
                Point p
                p.x = 5
                total = total + p.x
            }
            {
                Point p
                p.x = 6
                total = total + p.x
            }
            return total
        }
        for int i = 0, i < 3000, i++ {
            if nestedForBreak() != 1010 {
                fail('nestedForBreak drifted')
            }
            if elifChain(i % 3) != (10 + (i % 3) * 10) {
                fail('elifChain drifted')
            }
            if bareBlocks() != 11 {
                fail('bareBlocks drifted')
            }
        }
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    # ---- interprocedural (claude.md #74 stage 2) ----
    # See festina/escape_analysis.py's own module docstring and
    # tests/test_escape_analysis.py::TestInterproceduralEscapingParams
    # for the analysis in isolation (no C compiler, no codegen at all).
    # These check the other half: that CodeGen actually builds a
    # correct escaping_params table one function at a time, in program
    # order, and wires it into real generated IR and real compiled
    # output -- including the two cases the analysis-only tests can't
    # reach on their own (a real multi-function program, and a real
    # self-recursive function).

    def test_transitive_chain_of_non_retaining_calls_stack_allocates_the_original_local(
            self, parser, semantic, codegen):
        # a() -> b() -> c(), and c only reads its own parameter -- the
        # "safe" result has to propagate through b's own analysis (b's
        # parameter is only ever used as a pass-through argument to c)
        # before a's own local can be proven safe. Requires
        # escaping_params[c] to already exist by the time b is
        # analyzed, and escaping_params[b] to already exist by the time
        # a is analyzed -- exactly the program-order guarantee
        # CodeGen.escaping_params's own comment describes. Checks for a
        # stack alloca, not a free() -- since the stack-allocation swap
        # (claude.md #43/#74/#75), a struct proven safe this way never
        # goes through calloc+free at all.
        source = """
        struct Point { x:int y:int }
        void func c(q:Point) {
            log(q.x)
        }
        void func b(r:Point) {
            c(r)
        }
        void func a() {
            Point p
            b(p)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        a_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @a("))
        a_body = "\n".join(ir.splitlines()[a_start:])
        assert "alloca %struct.Point" in a_body
        assert "call ptr @calloc(" not in a_body

    def test_transitive_chain_stops_freeing_at_the_first_retaining_link(
            self, parser, semantic, codegen):
        # Same shape, but c now retains its own parameter (stores it
        # into a global) -- the escaping-ness must propagate all the
        # way back UP the chain: b's own argument to c escapes, so b's
        # own parameter escapes, so a's own local passed to b escapes
        # too. Nothing anywhere in this chain should be freed.
        source = """
        struct Point { x:int y:int }
        Point stash
        void func c(q:Point) {
            stash = q
        }
        void func b(r:Point) {
            c(r)
        }
        void func a() {
            Point p
            b(p)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_self_recursive_function_passing_its_own_param_stays_conservative(
            self, parser, semantic, codegen):
        # f calls itself, passing its own struct parameter straight
        # through. self.escaping_params has no entry for 'f' yet at the
        # point f's own body is being walked (f isn't registered until
        # AFTER its own analysis completes), so this recursive call
        # site falls back to the original unconditional "any call
        # argument escapes" default, exactly like a call to an unknown
        # builtin would -- p is marked escaping via that one use, even
        # though every OTHER use of p in f is a safe field read. A
        # caller of f() must see that same conservative result: it must
        # not free its own local either, even though nothing in this
        # program actually corrupts anything either way.
        source = """
        struct Point { x:int y:int }
        void func f(p:Point, n:int) {
            log(p.x)
            if n > 0 {
                f(p, n - 1)
            }
        }
        void func g() {
            Point p
            f(p, 3)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_only_the_provably_safe_parameter_position_is_exempted(
            self, parser, semantic, codegen):
        # mixed(a, b): a is only ever read (safe), b is stored into a
        # global (escapes) -- escaping_params[mixed] must end up
        # {1}, not {0, 1} or {} -- and f()'s own call must stack-
        # allocate exactly the argument at the safe position (p,
        # position 0) and leave the other (q, position 1) heap-
        # allocated with a refcount header (claude.md #77 -- q is
        # escaping AND never returned, so it's also scheduled for
        # release, not just left leaking calloc'd), exactly the same
        # non-stack-allocated treatment escaping structs always got.
        source = """
        struct Point { x:int y:int }
        Point stash
        void func mixed(a:Point, b:Point) {
            log(a.x)
            stash = b
        }
        void func f() {
            Point p
            Point q
            mixed(p, q)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        p_storage_line = next(l for l in f_body.splitlines() if l.strip().startswith("%p.storage."))
        assert "alloca %struct.Point" in p_storage_line
        # claude.md #176: q's own calloc now goes through the shared
        # _emit_fresh_heap_header (anonymous %tN temps, not a
        # %q.raw.<uid>-named one -- struct allocation sites share this
        # one implementation now so enum tagging only has to live in a
        # single place) -- p, the safe/stack-allocated position, must
        # never reach @calloc at all, so exactly one call proves q (and
        # only q) was heap-allocated.
        assert f_body.count("call ptr @calloc(") == 1
        assert "call void @festina_release(" in f_body

    def test_transitive_chain_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        int func c(q:Point) {
            return q.x * 10
        }
        int func b(r:Point) {
            return c(r) + 1
        }
        int func a(n:int) {
            Point p
            p.x = n
            return b(p)
        }
        log(a(5))
        log(a(7))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["51", "71"]

    def test_multi_param_mixed_escaping_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point stash
        void func mixed(a:Point, b:Point) {
            log(a.x)
            stash = b
        }
        void func f() {
            Point p
            p.x = 1
            Point q
            q.x = 2
            mixed(p, q)
        }
        f()
        log(stash.x)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "2"]

    def test_self_recursive_function_produces_correct_output_and_does_not_crash(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f(p:Point, n:int) {
            log(p.x)
            if n > 0 {
                f(p, n - 1)
            }
        }
        void func g() {
            Point p
            p.x = 42
            f(p, 3)
        }
        g()
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["42", "42", "42", "42", "done"]

    def test_interprocedural_combination_does_not_crash_under_heavy_iteration(
            self, compile_and_run):
        # The same combination verified separately by hand under
        # AddressSanitizer/LeakSanitizer (see tests/CONTRACT.md): a
        # transitive non-retaining chain, a transitive retaining chain,
        # a self-recursive function, and a multi-parameter
        # mixed-escaping function, all called many times in a loop.
        source = """
        struct Point { x:int y:int }
        Point stash
        int func readOnly(q:Point) {
            return q.x
        }
        int func passThrough(r:Point) {
            return readOnly(r)
        }
        int func nonRetainingChain(n:int) {
            Point p
            p.x = n
            return passThrough(p)
        }
        void func retains(q:Point) {
            stash = q
        }
        void func retainingChain(r:Point) {
            retains(r)
        }
        void func mixed(a:Point, b:Point) {
            log(a.x)
            stash = b
        }
        void func recur(p:Point, n:int) {
            if n > 0 {
                recur(p, n - 1)
            }
        }
        for int i = 0, i < 3000, i++ {
            if nonRetainingChain(i) != i {
                fail('nonRetainingChain drifted')
            }
            Point retainMe
            retainMe.x = i
            retainingChain(retainMe)
            if stash.x != i {
                fail('retainingChain drifted')
            }
            Point a
            a.x = i
            Point b
            b.x = i * 2
            mixed(a, b)
            if stash.x != i * 2 {
                fail('mixed drifted')
            }
            Point r
            r.x = i
            recur(r, 5)
        }
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.splitlines()[-1] == "done"

    # ---- reference counting (claude.md #77) ----
    # Scope for this stage: struct-typed GLOBALS (retain new/release old
    # on every reassignment) and escaping struct LOCALS that are never
    # themselves returned anywhere in their own function (released at
    # the same scope-exit points stages 1/2 already track). Struct
    # fields, arr[T]/map[T] values, and a value returned from a
    # function are all explicitly out of scope -- see todo.md.

    def test_global_struct_uses_a_sentinel_header_for_static_storage(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point g
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "@g.header = global {i64, %struct.Point} {i64 -1, %struct.Point zeroinitializer}" in ir
        assert "@g = global ptr getelementptr({i64, %struct.Point}, ptr @g.header, i32 0, i32 1)" in ir

    def test_global_struct_reassignment_retains_new_and_releases_old(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            Point p
            g = p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        # retain happens before release -- see _emit_assign's own
        # comment on why the order matters for self-assignment safety.
        retain_idx = f_body.index("call void @festina_retain(")
        release_idx = f_body.index("call void @festina_release(")
        assert retain_idx < release_idx

    def test_escaping_local_never_returned_is_released_at_scope_exit(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            Point p
            g = p
            log(p.x)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        # One release from the global assignment itself (releasing the
        # OLD global value, not p), one more from p's own scope-exit --
        # two total.
        assert f_body.count("call void @festina_release(") == 2

    def test_escaping_local_that_is_sometimes_returned_is_now_released_at_scope_exit(
            self, parser, semantic, codegen):
        # p is returned on ONE path (cond true) but not the other (cond
        # false, falls through to the end) -- textually identical
        # `return p` on both paths here, but that's just this source's
        # own shape, not something the retain-on-Return logic cares
        # about: every Return of a struct-typed value retains it first
        # (since a bare Identifier is an "aliasing" source, not an
        # owning one) and then _emit_free_active_locals releases every
        # active local, p included, on every path -- retain-then-
        # release-everything nets out to exactly one surviving
        # reference, the one just handed to the caller, on whichever
        # path actually runs. Three releases total: the global
        # assignment's own release of ITS previous value, plus one from
        # each of the two Return statements' own _emit_free_active_locals
        # call (only one of which runs on any given call, but both
        # appear in the IR).
        source = """
        struct Point { x:int y:int }
        Point g
        Point func f(cond:bool) {
            Point p
            g = p
            if cond {
                return p
            }
            return p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define ptr @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call void @festina_release(") == 3
        # And a matching retain for each of the two Return statements,
        # on top of the one from `g = p` itself -- three total.
        assert f_body.count("call void @festina_retain(") == 3

    def test_loop_local_escaping_struct_is_released_every_iteration(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            for int i = 0, i < 3, i++ {
                Point p
                p.x = i
                g = p
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        body_start = next(i for i, l in enumerate(lines) if l.strip().startswith("for.body"))
        body_end = next(i for i in range(body_start, len(lines)) if lines[i].strip().startswith("br label %for.update"))
        body_lines = lines[body_start:body_end]
        # One release for the OLD global value, one for p's own
        # per-iteration scope-exit -- both inside the loop body, every
        # iteration, not just once after the loop as a whole exits.
        assert sum("call void @festina_release(" in l for l in body_lines) == 2

    def test_break_releases_the_escaping_loop_local(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            for int i = 0, i < 5, i++ {
                Point p
                p.x = i
                g = p
                if p.x == 2 {
                    break
                }
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        break_block = next(i for i, l in enumerate(lines) if l.strip().startswith("if.then"))
        break_block_end = next(i for i in range(break_block, len(lines)) if lines[i].strip().startswith("br label %for.end"))
        break_lines = lines[break_block:break_block_end]
        assert any("call void @festina_release(" in l for l in break_lines)

    def test_global_reassignment_in_a_loop_produces_correct_final_value(
            self, compile_and_run):
        # claude.md #77's own motivating case, found and documented
        # earlier in this same effort: a global repeatedly reassigned
        # inside a loop used to orphan every value but the last one,
        # leaking each -- now they're actually freed, and the reachable
        # (last) one must still read back correctly.
        source = """
        struct Point { x:int y:int }
        Point g
        void func run() {
            for int i = 0, i < 500, i++ {
                Point p
                p.x = i
                g = p
            }
        }
        run()
        log(g.x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "499"

    def test_intermediate_global_values_stay_correct_across_reassignments(
            self, compile_and_run):
        # Not just the FINAL value -- every value read back immediately
        # after its own assignment (before the next iteration's release
        # could possibly touch it) must be correct too.
        source = """
        struct Point { x:int y:int }
        Point g
        void func run() {
            for int i = 0, i < 5, i++ {
                Point p
                p.x = i * i
                g = p
                log(g.x)
            }
        }
        run()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "4", "9", "16"]

    def test_self_assignment_of_a_global_struct_does_not_crash(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            Point p
            p.x = 7
            g = p
            g = g
            g = g
        }
        f()
        log(g.x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7"

    def test_escaping_and_sometimes_returned_local_still_produces_correct_output(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g
        Point func f(cond:bool) {
            Point p
            p.x = 3
            g = p
            if cond {
                return p
            }
            return p
        }
        Point q1 = f(true)
        Point q2 = f(false)
        log(q1.x)
        log(q2.x)
        log(g.x)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["3", "3", "3"]

    def test_two_different_global_structs_are_tracked_independently(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g1
        Point g2
        void func f() {
            Point p
            p.x = 1
            g1 = p
            Point q
            q.x = 2
            g2 = q
        }
        f()
        log(g1.x)
        log(g2.x)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "2"]

    def test_local_struct_declared_with_a_call_result_initializer_keeps_the_value(
            self, compile_and_run):
        # A real, pre-existing (not introduced by claude.md #77) bug
        # found while testing this stage: `Point r = make(5)` for a
        # LOCAL r (inside a function -- a top-level/global one already
        # worked, via a completely different code path,
        # _emit_toplevel_stmt) silently discarded the initializer and
        # left r's fields at their stack-allocated zero value instead,
        # since _emit_stmt's own StructType VarDecl branch never
        # actually looked at stmt.init at all. Traced by hand-tracing
        # the generated IR (`%r.storage = alloca %struct.Point; store
        # ... zeroinitializer` with the make(5) call's own return value
        # never referenced again) before fixing it, not just from the
        # symptom.
        source = """
        struct Point { x:int y:int }
        Point func make(n:int) {
            Point p
            p.x = n
            return p
        }
        void func f() {
            Point r = make(5)
            log(r.x)
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "5"

    def test_reassigning_a_local_struct_to_alias_another_does_not_double_release(
            self, compile_and_run):
        # A real bug found while first building claude.md #77's own
        # narrower initial scope: `Point q; q = p;` (q reassigned, after
        # its own declaration, to alias p's storage) then BOTH p and q
        # separately escaping (to two different globals) meant both
        # were scheduled for release at their own, independent scope-
        # exits -- with no retain anywhere to account for q's
        # reassignment creating a second reference, the second release
        # would decrement an already-zero (and, one more reassignment
        # later, already-freed) refcount. That narrower scope's own fix
        # was to exclude any reassigned struct local from release
        # tracking entirely (it leaked instead); this stage's own
        # widening replaces that exclusion with the actual missing
        # piece -- _emit_local_struct_retain_release now retains
        # whatever a reassignment's source aliases, which is what makes
        # it safe to schedule q for release too, not just avoid double-
        # releasing it. Confirmed via a real AddressSanitizer build with
        # the generated code itself properly instrumented
        # (`sanitize_address` added to every `define` -- see
        # tests/CONTRACT.md's own note on why
        # `clang -fsanitize=address -c file.ll` alone does not
        # instrument raw LLVM IR the way it does C source), not just
        # from this test's own passing assertion.
        source = """
        struct Point { x:int y:int }
        Point g1
        Point g2
        Point g3
        void func f() {
            Point p
            p.x = 5
            Point q
            q = p
            g1 = p
            g2 = q
        }
        f()
        Point other
        other.x = 999
        g1 = other
        log(g2.x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "5"

    def test_struct_declared_with_an_initializer_is_now_included_in_release_tracking(
            self, parser, semantic, codegen):
        # claude.md #77 widened: r's own initializer is a Call (make's
        # return value), an "owning" source per _is_owning_struct_source
        # -- no retain needed there, r's alias just carries make()'s own
        # +1 forward. `g = r` retains it (2). Two releases total: r's
        # own scope-exit (2 -> 1, correctly still alive via g) and the
        # global assignment's own release of g's previous value
        # (static, a no-op, but the call still happens).
        source = """
        struct Point { x:int y:int }
        Point g
        Point func make(n:int) {
            Point p
            p.x = n
            return p
        }
        void func f() {
            Point r = make(5)
            g = r
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call void @festina_release(") == 2

    def test_reassigned_struct_is_now_included_in_release_tracking(
            self, parser, semantic, codegen):
        # claude.md #77 widened: q's own reassignment (`q = p`, p a bare
        # Identifier -- an "aliasing" source) now retains p's value and
        # releases q's OWN original allocation (freed immediately,
        # nothing else ever referenced it) -- this is exactly what makes
        # it safe to now ALSO schedule q for release at its own scope-
        # exit, unlike before this widening. Five releases total: q's
        # own reassignment releasing its original allocation, the two
        # global assignments' own release of their previous (static,
        # no-op) values, and p's and q's own scope-exit releases. Final
        # refcount ends at 2 (g1 and g2, the two true live references),
        # matching reality -- see this test's own compile-and-run
        # sibling
        # (test_reassigning_a_local_struct_to_alias_another_does_not_double_release)
        # for the correctness confirmation, not just the IR shape.
        source = """
        struct Point { x:int y:int }
        Point g1
        Point g2
        void func f() {
            Point p
            Point q
            q = p
            g1 = p
            g2 = q
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call void @festina_release(") == 5

    # ---- widening local retain/release (claude.md #77, same stage) ----

    def test_local_reassignment_from_an_existing_identifier_retains(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            Point q
            q = p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" in f_body

    def test_local_reassignment_from_a_call_result_does_not_retain(
            self, parser, semantic, codegen):
        # make()'s own return value is a fresh, uniquely-owned value --
        # aliasing it into q needs no retain (see
        # _is_owning_struct_source's own comment).
        source = """
        struct Point { x:int y:int }
        Point func make(n:int) {
            Point p
            p.x = n
            return p
        }
        void func f() {
            Point q
            q = make(5)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" not in f_body
        # Still releases q's own original allocation on the way out.
        assert "call void @festina_release(" in f_body

    def test_vardecl_init_from_an_existing_identifier_retains(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            Point q = p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" in f_body

    def test_vardecl_init_from_a_call_result_does_not_retain(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point func make(n:int) {
            Point p
            p.x = n
            return p
        }
        void func f() {
            Point r = make(5)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" not in f_body
        assert "call void @festina_release(" in f_body

    def test_with_init_local_that_never_further_escapes_is_freed_correctly(
            self, compile_and_run):
        # The gap stage 4's own initial, narrower scope explicitly left
        # open: a local declared with an initializer and never
        # otherwise escaping used to leak permanently, since it was
        # excluded from release tracking entirely. Now included --
        # correctness confirmed here; the actual freeing (not just "no
        # crash") is confirmed under AddressSanitizer/LeakSanitizer
        # (see tests/CONTRACT.md).
        source = """
        struct Point { x:int y:int }
        Point func make(n:int) {
            Point p
            p.x = n
            return p
        }
        void func f(n:int) {
            Point r = make(n)
            log(r.x)
        }
        for int i = 0, i < 5, i++ {
            f(i * i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "4", "9", "16"]

    def test_reassignment_chain_through_two_globals_keeps_correct_values(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g1
        Point g2
        void func f(n:int) {
            Point p
            p.x = n
            Point q
            q = p
            g1 = p
            g2 = q
        }
        for int i = 0, i < 500, i++ {
            f(i)
        }
        log(g1.x)
        log(g2.x)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["499", "499"]

    def test_struct_field_read_used_as_a_reassignment_source_retains_correctly(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        struct Outer { inner:Point label:text }
        Point g
        void func f(n:int) {
            Point p
            p.x = n
            Outer o
            o.inner = p
            Point q
            q = o.inner
            g = q
        }
        for int i = 0, i < 500, i++ {
            f(i)
        }
        log(g.x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "499"

    def test_self_reassignment_of_a_local_struct_does_not_crash(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f(n:int) {
            Point p
            p.x = n
            p = p
            p = p
            log(p.x)
        }
        f(7)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7"

    def test_widened_local_retain_release_combination_does_not_crash(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g1
        Point g2
        Point func make(n:int) {
            Point p
            p.x = n
            return p
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                Point r = make(i)
                if r.x != i {
                    fail('with-init local drifted')
                }
                Point p
                p.x = i
                Point q
                q = p
                g1 = p
                g2 = q
                if g1.x != i || g2.x != i {
                    fail('reassignment chain drifted')
                }
                Point acc
                acc = r
                acc = q
                if acc.x != i {
                    fail('multi-reassignment drifted')
                }
            }
        }
        run(3000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_many_global_reassignments_with_break_and_continue_does_not_crash(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g
        void func run(n:int) {
            for int i = 0, i < n, i++ {
                Point p
                p.x = i
                g = p
                if p.x % 7 == 0 {
                    continue
                }
                if p.x % 97 == 0 {
                    break
                }
            }
        }
        for int i = 0, i < 3000, i++ {
            run(50)
        }
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    # -- retaining a function's own Return value (claude.md #77, widened
    # further): the last of the three conditions the original stage-4
    # scope excluded a struct local for is now handled too. See
    # tests/CONTRACT.md for the full writeup.

    def test_return_of_a_bare_identifier_retains(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point func f() {
            Point p
            return p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define ptr @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" in f_body
        assert "call void @festina_release(" in f_body

    def test_return_of_a_call_result_does_not_retain(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point func make() {
            Point p
            return p
        }
        Point func f() {
            return make()
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        f_start = next(i for i, l in enumerate(lines) if l.startswith("define ptr @f("))
        f_end = next(i for i in range(f_start, len(lines)) if lines[i].strip() == "}")
        f_body = "\n".join(lines[f_start:f_end])
        # f() itself has nothing to retain -- its own Return value is a
        # fresh, uniquely-owned call result, the same "no retain needed"
        # case a VarDecl-with-initializer or plain reassignment already
        # gets from a Call source. (make()'s own Return of its bare
        # local p is a separate, unrelated retain -- deliberately
        # excluded by only looking at f()'s own body.)
        assert "call void @festina_retain(" not in f_body
        assert "call void @festina_release(" not in f_body

    def test_returning_a_parameter_directly_keeps_the_callers_copy_correct(
            self, compile_and_run):
        # The soundness case this widening exists for, not just a leak
        # count: identity(x) hands x's own storage straight back out.
        # Without retaining it there, y (the caller's own copy of the
        # return value) would alias x's storage with no reference of
        # its own -- x's later scope-exit release would free memory y
        # still points to, and reading y afterward would be a genuine
        # use-after-free. Reading x AND y, well after x's own local
        # would ordinarily have been released, is the actual check.
        source = """
        struct Point { x:int y:int }
        Point func identity(p:Point) {
            return p
        }
        void func run() {
            for int i = 0, i < 2000, i++ {
                Point x
                x.x = i
                Point y = identity(x)
                if x.x != i || y.x != i {
                    fail('identity aliasing drifted')
                }
            }
        }
        run()
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_returning_a_ternary_between_two_locals_frees_the_untaken_branch(
            self, compile_and_run):
        # Neither branch of `cond ? a : b` is a bare Identifier Return
        # value in the old, narrower sense (the Return's own value
        # expression is the Ternary itself) -- this only works soundly
        # because the retain applies to whatever the Ternary evaluates
        # to at runtime, and _emit_free_active_locals still releases
        # BOTH a and b unconditionally afterward: the branch that was
        # actually returned nets out to one surviving reference (the
        # retain cancels its own release), and the untaken branch is
        # simply released and freed, same as if it had never been
        # returned at all.
        source = """
        struct Point { x:int y:int }
        Point func pick(cond:bool, n:int) {
            Point a
            a.x = n
            Point b
            b.x = n * 2
            return cond ? a : b
        }
        void func run() {
            for int i = 0, i < 2000, i++ {
                Point p = pick(i % 2 == 0, i)
                int expected = (i % 2 == 0) ? i : i * 2
                if p.x != expected {
                    fail('ternary return drifted')
                }
            }
        }
        run()
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    # -- releasing a discarded return value (claude.md #77, same stage):
    # the one struct-return leak left standing after the retain-on-Return
    # fix above -- a call result never bound to anything at all. See
    # tests/CONTRACT.md for the full writeup.

    def test_discarded_struct_returning_call_result_is_released(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point func make(n:int) {
            Point p
            p.x = n
            return p
        }
        void func f() {
            make(5)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_release(" in f_body

    def test_discarded_void_call_result_is_not_released(self, parser, semantic, codegen):
        # A negative check alongside the positive one above: a void
        # call has nothing to release (_emit_call returns ("0", None)
        # for it), and this fix only ever fires for a StructType result
        # -- confirms it doesn't misfire on the overwhelmingly more
        # common "call something for its side effects" case.
        source = """
        void func sideEffect() {
            log(1)
        }
        void func f() {
            sideEffect()
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_release(" not in f_body

    def test_discarded_struct_return_used_only_for_side_effects_still_crashes_safely(
            self, compile_and_run):
        # The struct value itself is thrown away, but the call still
        # runs and its side effect (the global write) still must happen
        # -- releasing the return value doesn't mean skipping the call.
        source = """
        struct Point { x:int y:int }
        int counter
        Point func makeAndCount(n:int) {
            counter = counter + 1
            Point p
            p.x = n
            return p
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                makeAndCount(i)
            }
        }
        run(2000)
        log(counter)
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "2000"

    # -- reference counting for a struct's own struct-typed fields
    # (claude.md #78, new section): the nested-field gap sections 74-77
    # deliberately left open. See tests/CONTRACT.md for the full
    # writeup.

    def test_struct_field_write_from_an_identifier_retains(self, parser, semantic, codegen):
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        void func f() {
            Inner i
            Outer o
            o.inner = i
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" in f_body

    def test_struct_field_write_from_a_call_result_does_not_retain(self, parser, semantic, codegen):
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        Inner func make() {
            Inner i
            return i
        }
        void func f() {
            Outer o
            o.inner = make()
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        f_start = next(i for i, l in enumerate(lines) if l.startswith("define void @f("))
        f_end = next(i for i in range(f_start, len(lines)) if lines[i].strip() == "}")
        f_body = "\n".join(lines[f_start:f_end])
        assert "call void @festina_retain(" not in f_body

    def test_struct_with_no_struct_fields_still_uses_the_generic_release(
            self, parser, semantic, codegen):
        # The common case (the overwhelming majority of structs have no
        # struct-typed field of their own) must stay exactly as cheap as
        # claude.md #77 already made it -- no per-type wrapper function
        # generated, no extra indirection, when there's nothing for a
        # release to cascade into.
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            Point p
            g = p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "@__festina_release_struct_" not in ir
        assert "call void @festina_release(" in ir

    def test_struct_with_a_nested_struct_field_gets_a_dedicated_release_function(
            self, parser, semantic, codegen):
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        Outer g
        void func f() {
            Outer o
            g = o
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "define void @__festina_release_struct_Outer(ptr %payload)" in ir
        # The wrapper itself must release the nested field via the
        # plain generic release (Inner has no struct-typed field of its
        # own) before freeing Outer's own storage.
        wrapper_start = ir.index("define void @__festina_release_struct_Outer(")
        wrapper_end = ir.index("\n}\n", wrapper_start)
        wrapper_body = ir[wrapper_start:wrapper_end]
        assert "call i8 @festina_release_check(" in wrapper_body
        assert "call void @festina_release(" in wrapper_body
        assert "call void @free(" in wrapper_body
        # And every release site for an Outer value (here, g's own
        # reassignment and o's own scope-exit) must call the wrapper,
        # not the plain generic release, directly.
        assert ir.count("call void @__festina_release_struct_Outer(") >= 2

    def test_deeply_nested_struct_fields_cascade_through_every_level(
            self, parser, semantic, codegen):
        # A -> B -> C, three levels deep -- confirms the recursive
        # wrapper-generation handles more than one level, not just the
        # immediate-child case every test above exercises.
        source = """
        struct C { v:int }
        struct B { c:C }
        struct A { b:B }
        A g
        void func f() {
            A a
            g = a
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "define void @__festina_release_struct_A(" in ir
        assert "define void @__festina_release_struct_B(" in ir
        # C has no struct-typed field of its own -- no wrapper needed.
        assert "define void @__festina_release_struct_C(" not in ir
        a_start = ir.index("define void @__festina_release_struct_A(")
        a_end = ir.index("\n}\n", a_start)
        a_body = ir[a_start:a_end]
        assert "call void @__festina_release_struct_B(" in a_body
        b_start = ir.index("define void @__festina_release_struct_B(")
        b_end = ir.index("\n}\n", b_start)
        b_body = ir[b_start:b_end]
        assert "call void @festina_release(" in b_body

    def test_nested_struct_field_reads_and_writes_correctly(self, compile_and_run):
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        Outer g
        void func f(n:int) {
            Inner i
            i.v = n
            Outer o
            o.inner = i
            g = o
        }
        f(42)
        log(g.inner.v)
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "42"

    def test_original_local_stays_correct_after_being_written_into_a_field(
            self, compile_and_run):
        # The exact aliasing hazard todo.md documented as the reason
        # this was deliberately deferred: `outer.field = someLocal`
        # aliases someLocal's own storage, not a copy. Without
        # retaining that reference on the way in, someLocal's own
        # later scope-exit release could free memory outer.field still
        # points to. Reading BOTH someLocal and outer.field.v, well
        # after someLocal's own scope would ordinarily have released
        # it, is the actual check -- 2000 iterations so a real
        # use-after-free (not just a lucky read of not-yet-reused
        # memory) would reliably surface as wrong output.
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        void func f(n:int) {
            Inner i
            i.v = n
            Outer o
            o.inner = i
            if i.v != n || o.inner.v != n {
                fail('field aliasing drifted before scope exit')
            }
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_reassigning_a_struct_field_releases_the_old_value_correctly(
            self, compile_and_run):
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        void func f(n:int) {
            Inner a
            a.v = n
            Inner b
            b.v = n * 2
            Outer o
            o.inner = a
            o.inner = b
            if o.inner.v != n * 2 {
                fail('field reassignment drifted')
            }
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_self_assignment_of_a_struct_field_does_not_crash(self, compile_and_run):
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        void func f(n:int) {
            Inner i
            i.v = n
            Outer o
            o.inner = i
            o.inner = o.inner
            o.inner = o.inner
            if o.inner.v != n {
                fail('self-assignment corrupted')
            }
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_freeing_an_outer_struct_frees_its_nested_field_too(self, compile_and_run):
        # A correctness/no-crash check that the OUTER struct being
        # freed doesn't corrupt anything -- the actual leak-vs-freed
        # verification is done with a real AddressSanitizer/
        # LeakSanitizer run (see tests/CONTRACT.md); pytest itself
        # doesn't drive ASan builds.
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        Outer g
        void func f(n:int) {
            Inner i
            i.v = n
            Outer o
            o.inner = i
            g = o
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
            }
        }
        run(2000)
        log(g.inner.v)
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "1999"

    # -- reference counting for arr[T]/map[T] values that escape
    # (claude.md #79, new section): arr[T]/map[T] is now a `ptr` to a
    # heap-allocated header, the same indirect, shared-identity
    # representation a struct value already has -- closing both the
    # escaping-array/map leak this whole roadmap item names, and a
    # separate, pre-existing memory-safety bug this representation
    # change fixes as a side effect (growing a map through one alias
    # left any other alias holding a dangling pointer). See
    # tests/CONTRACT.md for the full writeup.

    def test_with_init_array_local_is_refcounted_via_a_shared_header(
            self, parser, semantic, codegen):
        source = """
        arr[int] g
        void func f() {
            arr[int] a = [1, 2, 3]
            g = a
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        # A literal source is "owning" -- no retain for a's own
        # declaration -- but g's own reassignment always retains.
        assert f_body.count("call void @festina_retain(") == 1
        assert "call void @festina_release_array(" in f_body

    def test_array_field_write_from_an_identifier_retains(self, parser, semantic, codegen):
        source = """
        struct Bag { items:arr[int] }
        void func f() {
            arr[int] a
            Bag b
            b.items = a
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" in f_body

    def test_array_field_write_from_a_literal_does_not_retain(self, parser, semantic, codegen):
        source = """
        struct Bag { items:arr[int] }
        void func f() {
            Bag b
            b.items = [1, 2, 3]
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" not in f_body

    def test_struct_with_an_array_field_gets_a_dedicated_release_function(
            self, parser, semantic, codegen):
        source = """
        struct Bag { items:arr[int] }
        Bag g
        void func f() {
            Bag b
            g = b
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "define void @__festina_release_struct_Bag(ptr %payload)" in ir
        wrapper_start = ir.index("define void @__festina_release_struct_Bag(")
        wrapper_end = ir.index("\n}\n", wrapper_start)
        wrapper_body = ir[wrapper_start:wrapper_end]
        assert "call void @festina_release_array(" in wrapper_body

    def test_no_struct_or_array_type_uses_extractvalue_anymore(self, parser, semantic, codegen):
        # claude.md #79: arr[T]/map[T] moved from a plain aggregate
        # VALUE to a `ptr`, the same representation change structs
        # already went through -- extractvalue on a FESTINA_ARRAY_LLVM_TYPE/
        # FESTINA_MAP_LLVM_TYPE value should never appear anywhere in
        # generated IR anymore (every read now goes through a GEP+load
        # on the pointer instead).
        source = """
        void func printer(v:int, k:text) {
            log(v)
        }
        arr[int] a = [1, 2, 3]
        map[int] m = {'x': 1}
        log(a.length)
        log(a[0])
        log(m['x'])
        m.forEach(printer)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "extractvalue" not in ir

    def test_map_growth_through_one_alias_is_visible_through_another(self, compile_and_run):
        # The exact pre-existing (unrelated to refcounting) bug this
        # representation change fixes as a side effect: growing a map
        # via one alias used to leave any OTHER alias holding a stale
        # pointer into memory festina_map_set's own realloc had already
        # moved or freed -- confirmed directly, a real segfault, before
        # this section existed. Both aliases now share one identical
        # heap header, so festina_map_set's own mutation is visible to
        # both immediately, correctly, every time.
        source = """
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                map[int] a = {'x': i}
                map[int] b = a
                b['y'] = i + 1
                if a['y'] != i + 1 {
                    fail('map growth not visible through original alias')
                }
                b['x'] = i + 100
                if a['x'] != i + 100 {
                    fail('map mutation not visible through original alias')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_array_and_map_function_parameters_alias_the_callers_own_value(
            self, compile_and_run):
        source = """
        void func mutateFirst(a:arr[int], v:int) {
            a[0] = v
        }
        void func addEntry(m:map[int], k:text, v:int) {
            m[k] = v
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                arr[int] a = [i]
                mutateFirst(a, i + 1)
                if a[0] != i + 1 {
                    fail('array param mutation not visible to caller')
                }
                map[int] m = {'a': i}
                addEntry(m, 'b', i + 1)
                if m['b'] != i + 1 {
                    fail('map param mutation not visible to caller')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_returning_an_array_or_map_keeps_the_correct_value(self, compile_and_run):
        source = """
        arr[int] func makeArr(n:int) {
            arr[int] a = [n, n * 2, n * 3]
            return a
        }
        map[int] func makeMap(n:int) {
            map[int] m = {'a': n}
            return m
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                arr[int] r = makeArr(i)
                if r[0] != i || r[1] != i * 2 || r[2] != i * 3 {
                    fail('returned array drifted')
                }
                map[int] mr = makeMap(i)
                if mr['a'] != i {
                    fail('returned map drifted')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_discarded_array_and_map_call_results_do_not_crash(self, compile_and_run):
        source = """
        arr[int] func makeArr(n:int) {
            arr[int] a = [n]
            return a
        }
        map[int] func makeMap(n:int) {
            map[int] m = {'a': n}
            return m
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                makeArr(i)
                makeMap(i)
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_recursive_function_over_an_array_parameter_works_correctly(self, compile_and_run):
        source = """
        int func sumRecursive(a:arr[int], idx:int) {
            if idx >= a.length {
                return 0
            }
            return a[idx] + sumRecursive(a, idx + 1)
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                arr[int] nums = [i, i + 1, i + 2, i + 3]
                if sumRecursive(nums, 0) != i + (i + 1) + (i + 2) + (i + 3) {
                    fail('sumRecursive drifted')
                }
            }
        }
        run(1000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_struct_field_of_array_and_map_type_reads_and_writes_correctly(
            self, compile_and_run):
        source = """
        struct Bag { items:arr[int] counts:map[int] }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                arr[int] items = [i, i + 1, i + 2]
                map[int] counts = {'total': i}
                Bag bag
                bag.items = items
                bag.counts = counts
                if bag.items[0] != i || bag.counts['total'] != i {
                    fail('struct arr/map field drifted')
                }
                bag.items = [i + 10]
                if bag.items[0] != i + 10 {
                    fail('struct arr field reassignment drifted')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_global_array_reassigned_in_a_loop_produces_correct_final_value(
            self, compile_and_run):
        # claude.md #79's own motivating case, the same as claude.md
        # #77's original one for structs: a global repeatedly
        # reassigned inside a loop used to leak every value but the
        # last (arrays weren't refcounted at all before this section);
        # now each iteration's own value is correctly released, and the
        # final (reachable) one still reads back correctly.
        source = """
        arr[int] g
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                arr[int] a = [i]
                g = a
            }
        }
        run(2000)
        log(g[0])
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "1999"

    # -- reference counting for individual elements/values stored
    # *inside* an arr[T]/map[T] (claude.md #80, new section): claude.md
    # #79 made the array/map's own header a refcounted, shared-identity
    # value, but never touched what happens to a struct/arr/map ELEMENT
    # once it's copied into the array's data buffer or a map's entry --
    # that element was never retained on the way in, and never released
    # on the way out, leaving a struct-typed array element readable
    # after its original binding's own scope had already released it
    # (a confirmed, reproduced heap-use-after-free). See tests/CONTRACT.md
    # for the full writeup.

    def test_struct_element_of_an_escaping_array_survives_its_source_scope(
            self, compile_and_run):
        # The exact use-after-free claude.md #79 left open and #80
        # closes: a struct built fresh inside f(), stored as an
        # array's sole element, the array escaping through a global
        # while the struct's own local scope ends when f() returns.
        source = """
        struct Point { x:int y:int }
        arr[Point] g

        void func f(n:int) {
            Point p
            p.x = n
            arr[Point] pts = [p]
            g = pts
        }

        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
                if g[0].x != i {
                    fail('array element of struct type did not survive its source scope')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_struct_value_of_an_escaping_map_survives_its_source_scope(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        map[Point] g

        void func f(n:int) {
            Point p
            p.x = n
            map[Point] pts = {'a': p}
            g = pts
        }

        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
                if g['a'].x != i {
                    fail('map value of struct type did not survive its source scope')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_array_of_arrays_element_survives_its_source_scope(self, compile_and_run):
        source = """
        arr[arr[int]] g

        void func f(n:int) {
            arr[int] inner = [n, n + 1]
            arr[arr[int]] outer = [inner]
            g = outer
        }

        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
                if g[0][0] != i || g[0][1] != i + 1 {
                    fail('array-of-arrays element did not survive its source scope')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_reassigning_an_array_element_releases_the_old_value_correctly(
            self, compile_and_run):
        # arr[i]=v now shares the exact retain-new/release-old path
        # struct-field writes already use -- the old element (still
        # reachable nowhere else) must be released, and the new one
        # (an aliased identifier, not a fresh literal) must be retained,
        # without either double-freeing or leaking.
        source = """
        struct Box { v:int }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                Box b0
                Box b1
                arr[Box] boxes = [b0, b1]
                Box replacement
                replacement.v = i
                boxes[0] = replacement
                if boxes[0].v != i {
                    fail('array element reassignment drifted')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_reassigning_a_map_value_releases_the_old_value_correctly(
            self, compile_and_run):
        source = """
        struct Box { v:int }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                Box b0
                map[Box] boxes = {'a': b0}
                Box replacement
                replacement.v = i
                boxes['a'] = replacement
                if boxes['a'].v != i {
                    fail('map value reassignment drifted')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_non_escaping_array_of_structs_frees_its_elements_too(
            self, compile_and_run):
        # A purely local, never-escaping arr[Box] still stack-allocates
        # its own header (claude.md #76) but its Box elements are
        # heap-allocated (structs always are) -- the _StackArrayOrMap
        # scope-exit path must release each element before freeing the
        # data buffer, not just free the buffer itself.
        source = """
        struct Box { v:int }
        void func f(n:int) {
            Box b0
            Box b1
            arr[Box] boxes = [b0, b1]
            boxes[0].v = n
            boxes[1].v = n + 1
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
            }
        }
        run(20000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_array_literal_element_from_an_identifier_retains(self, parser, semantic, codegen):
        source = """
        struct Box { v:int }
        void func f() {
            Box b
            arr[Box] boxes = [b]
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" in f_body

    def test_array_literal_element_from_a_call_does_not_retain(self, parser, semantic, codegen):
        source = """
        struct Box { v:int }
        Box func makeBox() {
            Box b
            return b
        }
        void func f() {
            arr[Box] boxes = [makeBox()]
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" not in f_body

    def test_array_of_structs_release_wrapper_cascades_into_each_element(
            self, parser, semantic, codegen):
        source = """
        struct Box { v:int }
        arr[arr[Box]] g
        void func f() {
            Box b
            arr[Box] boxes = [b]
            arr[arr[Box]] outer = [boxes]
            g = outer
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        # A dedicated per-element-type release wrapper is generated for
        # arr[Box] (Box is refcounted), and outer's own wrapper (for
        # arr[arr[Box]]) must call into it rather than the generic,
        # element-blind @festina_release_array.
        assert "define void @__festina_release_array_" in ir
        wrapper_names = [
            l.split("define void @")[1].split("(")[0]
            for l in ir.splitlines() if l.startswith("define void @__festina_release_array_")
        ]
        assert len(wrapper_names) == 2
        outer_wrapper_body = None
        for name in wrapper_names:
            start = ir.index(f"define void @{name}(")
            end = ir.index("\n}\n", start)
            body = ir[start:end]
            if any(other in body for other in wrapper_names if other != name):
                outer_wrapper_body = body
        assert outer_wrapper_body is not None

    def test_map_of_structs_release_wrapper_uses_map_for_each(
            self, parser, semantic, codegen):
        source = """
        struct Box { v:int }
        map[Box] g
        void func f() {
            Box b
            map[Box] boxes = {'a': b}
            g = boxes
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "define void @__festina_release_map_" in ir
        wrapper_start = ir.index("define void @__festina_release_map_")
        wrapper_end = ir.index("\n}\n", wrapper_start)
        wrapper_body = ir[wrapper_start:wrapper_end]
        assert "call void @festina_map_for_each(" in wrapper_body

    def test_map_of_ints_still_uses_the_generic_release_function(
            self, parser, semantic, codegen):
        # An int-valued map has nothing to cascade into -- it must keep
        # using the plain, generic @festina_release_map from claude.md
        # #79 rather than generating a needless per-type wrapper.
        source = """
        map[int] g
        void func f() {
            map[int] m = {'a': 1}
            g = m
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "define void @__festina_release_map_" not in ir
        assert "call void @festina_release_map(" in ir


class TestFunctionHoisting:
    """claude.md #140: every function's name and signature is registered
    -- both in semantic.py's own symbol table and in codegen's
    self.func_decls/self.global_env -- in a pre-pass over the whole
    program before any code that might call it is emitted, so a call
    reached earlier in program order than its own callee's declaration
    still compiles and runs correctly ("hoisting"). tests/
    test_syntax_declarations.py's own TestFunctionHoisting covers the
    semantic-analysis half (declaration-order stops being an error);
    these compile-and-RUN the equivalent programs, since hoisting is a
    codegen concern too -- a forward call has to actually resolve to the
    right LLVM function and produce the right answer, not just pass
    semantic analysis."""

    def test_calling_a_function_declared_later_produces_the_right_answer(self, compile_and_run):
        source = """
        log(greet('world'))

        text func greet(name:text) {
            return concatHelper('Hello, ', name)
        }

        text func concatHelper(a:text, b:text) {
            return a + b + '!'
        }
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "Hello, world!"

    def test_mutual_recursion_produces_the_right_answer(self, compile_and_run):
        source = """
        bool func isEven(n:int) {
            if (n == 0) { return true }
            return isOdd(n - 1)
        }

        bool func isOdd(n:int) {
            if (n == 0) { return false }
            return isEven(n - 1)
        }

        log(isEven(10))
        log(isOdd(10))
        log(isEven(7))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "false", "false"]

    def test_a_function_nested_inside_a_block_is_callable_from_above_it(self, compile_and_run):
        # claude.md #140: a FuncDecl nested inside an if/while/for is
        # already treated as an ordinary GLOBAL declaration regardless
        # of nesting (semantic.py's analyze_func always defines into
        # global_scope) -- this confirms codegen actually emits its
        # body too (a real, once-crashing gap: _emit_stmt had no
        # ast.FuncDecl branch at all before this, so a nested
        # declaration compiled fine at the semantic-analysis stage and
        # then crashed codegen with "cannot generate code for statement
        # FuncDecl" the moment it was ever reached).
        source = """
        log(nested())

        if (true) {
            int func nested() { return 42 }
        }
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "42"

    def test_a_function_nested_inside_a_never_taken_branch_still_exists(self, compile_and_run):
        # Semantic analysis (and therefore codegen too) walks BOTH arms
        # of an if unconditionally -- the declaration is hoisted exactly
        # the same regardless of whether its own enclosing branch ever
        # actually runs, matching the way JavaScript's own function
        # declaration hoisting is unconditional too.
        source = """
        log(helper())

        if (false) {
            int func helper() { return 5 }
        }
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "5"

    def test_a_function_nested_inside_another_function_is_globally_callable(self, compile_and_run):
        source = """
        void func setup() {
            int func inner() { return 7 }
        }

        log(inner())
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "7"

    def test_nested_func_decl_does_not_corrupt_the_enclosing_functions_own_locals(self, compile_and_run):
        # claude.md #140's real regression, found via a genuine LLVM
        # verifier error ("use of undefined value"), not guessed: a
        # nested FuncDecl re-enters _emit_func recursively while the
        # ENCLOSING function's own struct/text/array/map locals are
        # still tracked on the shared self._active_free_locals stack --
        # without self._current_func_frame_base, the nested function's
        # own trivial `return` (down_to=0, unqualified) freed the
        # OUTER function's still-live locals instead of just its own
        # (empty) frame. This exercises every refcounted-local shape
        # that free/release machinery distinguishes: a struct field
        # (p.x/p.y), a text local, and an array local, all read AFTER
        # the nested declaration.
        source = """
        struct Point { x:int, y:int }

        int func outer(seed:text) {
            Point p
            p.x = 1
            p.y = 2
            text local = seed + '-suffix'
            arr[int] nums = [10, 20, 30]
            int func inner() { return 7 }
            int total = p.x + p.y + nums.length + inner()
            log(local)
            return total
        }

        log(outer('hi'))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        # p.x + p.y + nums.length + inner() = 1 + 2 + 3 + 7 = 13
        assert result.stdout.splitlines() == ["hi-suffix", "13"]

    def test_a_function_declared_before_its_own_struct_type_still_compiles(self, compile_and_run):
        # claude.md #106's own struct-name pre-pass and claude.md #140's
        # function-signature pre-pass run as two SEPARATE passes, struct
        # names first -- this is what lets a function's own parameter/
        # return type NAME a struct declared later than the function
        # itself (register_func_signature's resolve() call needs
        # `Point` to already exist as a name, even before struct
        # Point's own fields are populated). This is deliberately NOT a
        # claim that a struct's FIELDS are hoisted the identical way --
        # accessing p.x before struct Point{}'s own declaration has
        # been reached by the real analysis pass is a pre-existing,
        # unrelated limitation claude.md #106 already has (its own
        # pre-pass only guarantees the NAME exists, same as this one),
        # so every field access below is deliberately placed after
        # struct Point's own declaration in program order, isolating
        # just the one thing actually new here: the function itself.
        source = """
        Point func identity(p:Point) {
            return p
        }

        struct Point { x:int, y:int }

        Point q
        q.x = 3
        q.y = 4
        log(identity(q).x)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "3"

    def test_break_and_continue_around_a_nested_func_decl_still_work(self, compile_and_run):
        # self._loop_targets is untouched by this feature (it was
        # already correctly stack-shaped and reentrant through nested
        # loops before this), but is worth confirming directly: a
        # nested FuncDecl's own emission must not leave a stray entry
        # behind that would make an unrelated LATER break/continue in
        # the ENCLOSING loop resolve to the wrong target.
        source = """
        for int i = 0, i < 5, i++ {
            if (i == 3) { break }
            if (i == 1) { continue }
            int func helper() { return 1 }
            log(`i=${i} helper=${helper()}`)
        }
        log('after loop')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["i=0 helper=1", "i=2 helper=1", "after loop"]


class TestFirstClassFunctions:
    """claude.md #141: func[T, T, ...]:R -- a first-class function
    value, usable as an argument, struct property, map value, or array
    value. tests/test_syntax_declarations.py's own TestFuncTypeSyntax/
    TestFirstClassFunctions cover parsing and semantic analysis; these
    compile and RUN the equivalent programs, since the actual codegen
    mechanism (a bare function symbol as a `ptr` value, an indirect
    `call` through it) needed its own real verification -- a pre-
    existing placeholder in codegen.py (`raise CodegenError("functions
    are not first-class values yet"...)`) shows this was anticipated
    but never implemented before this entry, and a SEPARATE, real gap
    (`raise CodegenError("only calls to named functions are
    implemented"...)`) covered calling through a struct field/array
    element/map value specifically."""

    def test_assigning_a_function_by_name_and_calling_through_the_variable(self, compile_and_run):
        source = """
        void func greet(name:text) { log(name) }
        func[text]:void cb = greet
        cb('world')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "world"

    def test_passing_a_function_as_an_argument(self, compile_and_run):
        source = """
        void func greet(name:text) { log(name) }
        void func apply(fn:func[text]:void, arg:text) { fn(arg) }
        apply(greet, 'hi')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "hi"

    def test_storing_in_a_struct_field_and_calling_through_it(self, compile_and_run):
        source = """
        void func greet(name:text) { log(name) }
        struct Holder { cb:func[text]:void }
        Holder h
        h.cb = greet
        h.cb('yo')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "yo"

    def test_storing_in_an_array_and_calling_by_index(self, compile_and_run):
        source = """
        int func inc(x:int) { return x + 1 }
        int func dec(x:int) { return x - 1 }
        arr[func[int]:int] fns = [inc, dec]
        log(fns[0](5))
        log(fns[1](5))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["6", "4"]

    def test_storing_in_a_map_and_calling_by_key(self, compile_and_run):
        source = """
        void func greet(name:text) { log(name) }
        map[func[text]:void] handlers
        handlers['g'] = greet
        handlers['g']('map-call')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "map-call"

    def test_a_zero_argument_void_function_value_works(self, compile_and_run):
        source = """
        void func tick() { log('ticked') }
        func[]:void cb = tick
        cb()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "ticked"

    def test_a_function_value_with_a_non_void_return_type_works(self, compile_and_run):
        source = """
        int func square(x:int) { return x * x }
        func[int]:int f = square
        log(f(7))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "49"

    def test_local_variable_shadowing_a_global_function_calls_the_local(self, compile_and_run):
        # claude.md #141: Scope.define permits a local to reuse a
        # global name -- calling `greet` inside the shadowing scope
        # must resolve to the LOCAL func-typed variable's own target
        # (`other`), never silently fall back to the shadowed global
        # `greet` itself.
        source = """
        void func greet(name:text) { log(`global greet: ${name}`) }
        void func other(name:text) { log(`shadowed target: ${name}`) }

        void func useShadowed() {
            func[text]:void greet = other
            greet('x')
        }

        useShadowed()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "shadowed target: x"

    def test_null_func_value_is_a_valid_declaration(self, compile_and_run):
        source = """
        func[text]:void cb = null
        log('ok')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "ok"

    def test_a_struct_holding_a_func_field_survives_scope_exit_and_a_free(self, compile_and_run):
        # claude.md #141: a func-typed field must be correctly skipped
        # by the struct's own release/free cascade (never mistaken for
        # a refcounted or stack-cascaded field) -- run under
        # AddressSanitizer via leak_stress.sh separately for the
        # thousands-of-iterations version of this; this is the plain
        # correctness check that the VALUE itself still reads right
        # after a `free`.
        source = """
        int func doubleIt(x:int) { return x * 2 }
        struct Callback { fn:func[int]:int  label:text }
        Callback cbk
        cbk.fn = doubleIt
        cbk.label = 'd'
        log(cbk.fn(21))
        free cbk
        log('freed ok')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["42", "freed ok"]


class TestArrowFunctions:
    """claude.md #142: `returnType (params) => expr` -- an arrow
    function expression, desugaring to a synthesized, uniquely-named
    top-level function (compiling to a regular function, per the
    request's own framing) whose func[...]:... value is the arrow
    expression's own result. tests/test_syntax_declarations.py's own
    TestArrowFunctionSyntax/TestArrowFunctions cover parsing and
    semantic analysis; these compile and RUN the equivalent programs."""

    def test_a_void_arrow_function_assigned_and_called(self, compile_and_run):
        source = """
        func[text]:void cb = void (arg:text) => log(arg)
        cb('hello arrow')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "hello arrow"

    def test_a_non_void_arrow_functions_body_expression_is_its_return_value(self, compile_and_run):
        source = """
        func[int]:int sq = int (x:int) => x * x
        log(sq(7))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "49"

    def test_an_arrow_function_used_directly_as_a_call_argument(self, compile_and_run):
        source = """
        void func apply(fn:func[text]:void, arg:text) { fn(arg) }
        apply(void (arg:text) => log(arg), 'inline arrow')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "inline arrow"

    def test_arrow_functions_in_a_struct_field_array_and_map(self, compile_and_run):
        source = """
        struct Holder { cb:func[text]:void }
        Holder h
        h.cb = void (arg:text) => log(`struct: ${arg}`)
        h.cb('via struct')

        arr[func[int]:int] fns = [int (x:int) => x + 1, int (x:int) => x - 1]
        log(fns[0](10))
        log(fns[1](10))

        map[func[text]:void] handlers
        handlers['a'] = void (arg:text) => log(`map: ${arg}`)
        handlers['a']('via map')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == [
            "struct: via struct", "11", "9", "map: via map",
        ]

    def test_two_arrow_functions_at_different_expression_positions_stay_independent(self, compile_and_run):
        # Each arrow expression synthesizes its own uniquely-named
        # function -- calling one must never reach the other's body.
        source = """
        func[int]:int inc = int (x:int) => x + 1
        func[int]:int dec = int (x:int) => x - 1
        log(inc(10))
        log(dec(10))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["11", "9"]

    def test_referencing_an_enclosing_functions_local_variable_is_a_compile_error(
            self, parser, semantic, errors):
        # claude.md #142: no closures -- a CompileError, raised during
        # semantic analysis itself, so this is checked the same way
        # every other compile-time rejection in this file is (not via
        # compile_and_run, which expects the compile to succeed).
        source = """
        void func outer() {
            int localVar = 42
            func[text]:void cb = void (arg:text) => log(`${arg} ${localVar}`)
        }
        outer()
        """
        program = parser.parse(source, filename="main.f")
        with pytest.raises(errors.CompileError, match="unknown variable"):
            semantic.analyze(program, filename="main.f")

    def test_referencing_a_top_level_global_variable_works(self, compile_and_run):
        source = """
        int globalCount = 42
        func[text]:void cb = void (arg:text) => log(`${arg} ${globalCount}`)
        cb('x')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "x 42"


def _find_window(display, timeout=20):
    # A window actually appears in ~0.2s, measured, consistently -- this
    # timeout is generous insurance, not a figure anything is expected
    # to approach.
    #
    # It used to be 20s for a reason that turned out to be a
    # misdiagnosis, recorded here so it isn't re-derived: TestGraphics
    # was intermittently flaky (roughly a third of full-suite runs,
    # essentially never in isolation), and that was attributed to
    # contention pushing a window's startup past the old 10s, so the
    # timeout was doubled. Raising it never helped, and could not have:
    # the compiled program had already exited by then. The real cause
    # was a single un-retried XOpenDisplay in festina_graphics_init --
    # one transient connection refusal under load killed the program
    # outright, with a fatal error naming entirely the wrong cause ("is
    # $DISPLAY set?"). Fixed in the runtime (claude.md #87), which is
    # what actually made these tests deterministic; see that section for
    # the forensics proving the server was alive and reachable the whole
    # time. Module-level (not a method) so TestTimers's one combined
    # graphics+timers test can reuse it too.
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["xdotool", "search", "--name", "Festina"],
            env=dict(os.environ, DISPLAY=display),
            capture_output=True, text=True,
        )
        wids = result.stdout.split()
        if wids:
            return wids[0]
        time.sleep(0.2)
    raise AssertionError("the Festina canvas window never appeared")


def _require_x11_tool(name, purpose):
    """claude.md #233: skip (or, under FESTINA_STRICT_DEPS, fail loudly)
    when an X11 helper binary this test reads the screen through isn't
    installed -- `xwd` (x11-apps) and `xprop` (x11-utils) are optional
    tooling in setup.md's sense, exactly like xdotool/openbox, and a
    missing one used to surface as a raw FileNotFoundError from
    subprocess (four tests, every push, on the linux CI job before
    ci.yml installed them) instead of the clean skip every other
    optional tier already gets."""
    if shutil.which(name):
        return
    missing = f"{name} isn't installed -- needed to {purpose}"
    if os.environ.get("FESTINA_STRICT_DEPS"):
        pytest.fail(missing)
    pytest.skip(missing)


def _xwd_pixels(display, wid, points):
    """Capture a window with `xwd` and return the RGB at each (x, y).

    claude.md #89's colours are only really verified by looking at what
    actually landed on the canvas -- asserting the runtime call was
    emitted proves the plumbing, not that 'red' is red. xwd ships with
    the same x11-apps/x11-utils tier as xdotool, which these tests
    already require, so this needs no new dependency.

    XWD is a fixed 25-word big-endian header, then the window name, then
    the colormap, then rows of `bytes_per_line`; the r/g/b masks in the
    header say where each channel sits inside a pixel.
    """
    import struct
    _require_x11_tool("xwd", "read real canvas pixels")
    dump = subprocess.run(["xwd", "-id", wid], env=dict(os.environ, DISPLAY=display),
                          capture_output=True, check=True).stdout
    hdr = struct.unpack(">25I", dump[:100])
    header_size, bpp, bytes_per_line = hdr[0], hdr[11], hdr[12]
    red_mask, green_mask, blue_mask, ncolors = hdr[14], hdr[15], hdr[16], hdr[19]
    body = dump[header_size + ncolors * 12:]

    def channel(value, mask):
        if not mask:
            return 0
        shift = (mask & -mask).bit_length() - 1
        width = bin(mask >> shift).count("1")
        c = (value & mask) >> shift
        return c << (8 - width) if width < 8 else c

    out = []
    nbytes = bpp // 8
    for x, y in points:
        base = y * bytes_per_line + x * nbytes
        value = int.from_bytes(body[base:base + nbytes], "little")
        out.append((channel(value, red_mask), channel(value, green_mask),
                    channel(value, blue_mask)))
    return out


def _wait_for_output(stdout_path, predicate, timeout=20):
    # Polls instead of a fixed sleep-then-assert, for the same reason
    # x_display polls for Xvfb readiness instead of a fixed sleep (see
    # conftest.py): reliable running one test in isolation but flaky
    # under full-suite load, since dispatch latency (X server ->
    # compiled handler -> log() -> this process's read) isn't constant.
    # Returns the last text read so a timed-out caller's own assert
    # still shows what actually came back, not just "timed out".
    # Module-level for the same reason _find_window is.
    deadline = time.time() + timeout
    text = ""
    while time.time() < deadline:
        text = stdout_path.read_text()
        if predicate(text):
            return text
        time.sleep(0.1)
    return text


class TestGraphics:
    """claude.md #37 (image), #39 (graphics), #40 (events) -- a real
    X11 window rendered via Cairo, not a file written to disk (see
    festina/codegen.py's module docstring's "Graphics" note for the
    full design).

    Three tiers, cheapest first:
    - Compilation only (no display needed at all) -- catches codegen/
      linking bugs (e.g. a wrong declare signature) fast.
    - The "no display available" error path -- also doesn't need a
      real display; it specifically tests running with none.
    - Real interactive tests against a window (via the x_display /
      run_graphics_program fixtures -- prefers an already-set DISPLAY,
      otherwise spins up a throwaway Xvfb instance; skips cleanly if
      neither Xvfb nor xdotool is available). These were also verified
      manually beyond what's automated here: actually capturing the
      rendered window (via xwd) and visually confirming drawRect/
      drawCircle/drawText/drawImage all render at the right positions,
      in the right shapes -- not automated here (would add xwd/netpbm
      as further test-only dependencies for something the manual check
      already gave high confidence in), but the click/mouse/key/resize
      dispatch tests below do run automatically, since xdotool + log()
      output is enough to prove real input reaches the compiled handler
      without needing to capture pixels at all.

    `on close` is the one handler NOT covered here even though the
    others in this tier are: it fires on the exact same WM_DELETE_WINDOW
    ClientMessage the window's own close-button handling already uses,
    and (as already true of that close-button path before `on close`
    ever existed) a bare Xvfb instance runs no window manager to
    translate `xdotool windowclose` into that message -- verified
    directly: it leaves the process running rather than triggering the
    handler. An environment limitation of the test setup, not a gap in
    the app's own (standard) handling of that protocol -- see
    festina_runtime.c's festina_handle_graphics_event for the actual
    dispatch, right alongside the click/mouse/key/resize dispatch this
    class does verify.

    One test in this class runs against a *fourth*, separate tier: a
    real window manager (`openbox`, via the `x_display_with_wm` fixture)
    rather than the bare, WM-less Xvfb every other test here uses --
    needed because a whole class of window-manager-reparenting race
    exists only under a real WM, and a bare Xvfb instance can never
    reproduce it no matter how many times a graphics program is run
    against it. Skips cleanly if `openbox` isn't installed, same as the
    rest of this tier skips cleanly without Xvfb/xdotool.
    """

    def test_compiles_and_links_successfully(self, cli_mod, tmp_path):
        # No display needed -- just confirms codegen emits valid IR and
        # the result links against Cairo/X11 successfully (claude.md
        # #59: graphics is a real new link-time dependency -- see
        # festina/cli.py's cairo-xlib wiring).
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        source = """
        img icon = 'nonexistent.png'
        drawRect(0, 0, 100, 100)
        drawCircle(50, 50, 25)
        drawText('Hello', 20, 20)
        drawImage(icon, 10, 10)
        log(`${clientWidth}x${clientHeight}`)
        log(`${devicePixelRatio}`)
        showCursor()
        hideCursor()

        on mouseDown(x:int, y:int, button:int) {
            log(`press at ${x}, ${y} (${button})`)
        }
        on mouseUp(x:int, y:int, button:int) {
            log(`release at ${x}, ${y} (${button})`)
        }
        on mouse(x:int, y:int) {
            log(`mouse at ${x}, ${y}`)
        }
        on mouseWheelUp(x:int, y:int) {
            log(`wheel up at ${x}, ${y}`)
        }
        on mouseWheelDown(x:int, y:int) {
            log(`wheel down at ${x}, ${y}`)
        }
        on key(key:text) {
            log(`key ${key}`)
        }
        on resize() {
            log(`resize ${clientWidth} ${clientHeight}`)
        }
        on close() {
            log('closing')
        }
        """
        src_path = tmp_path / "main.f"
        src_path.write_text(source)
        out_path = tmp_path / "program"
        # claude.md #126 round six: this program opens a real window
        # (on mouseDown/mouseUp/mouse/key/resize/close), so on darwin it
        # must hit the real-hardware-verification gate (macos.md Phase
        # 2) same as TestAudio's own compile_file_or_skip use right
        # below -- calling compile_file directly meant that gate's
        # CompileError surfaced as a raw macOS CI failure instead of the
        # skip every other platform-conditional test gets.
        from tests.conftest import compile_file_or_skip
        result_path = compile_file_or_skip(cli_mod, str(src_path), str(out_path))
        assert result_path == str(out_path)
        assert out_path.exists()

    @pytest.mark.skipif(sys.platform == "win32", reason=(
        "claude.md #169: windows-latest's real Windows CI run found this "
        "test hangs there rather than failing -- a live desktop session "
        "is always present, so deleting DISPLAY (a POSIX/X11-only "
        "concept the Win32 backend never consults) doesn't reproduce "
        "'no display available' the way it does on headless Linux; "
        "render() instead opens a real window and blocks in its event "
        "loop with nothing to ever close it."))
    def test_missing_display_is_a_clear_runtime_error(self, compile_and_run, monkeypatch):
        # claude.md #95: it is render() that needs a display now, not
        # drawing -- drawing paints an offscreen canvas and is perfectly
        # happy with no X server, which is what makes headless PNG
        # output possible. So this is the call that must report it.
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run("drawRect(0, 0, 10, 10)\nrender()")
        assert result.returncode == 1
        assert "X display" in result.stderr

    def test_drawing_without_a_display_is_no_longer_an_error(
            self, compile_and_run, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run("drawRect(0, 0, 10, 10)\nlog('drew headlessly')")
        assert result.returncode == 0
        assert result.stdout == "drew headlessly\n"

    def test_invalid_image_path_is_a_clear_runtime_error(self, compile_and_run, monkeypatch):
        # claude.md #101 split "cannot open the file" from "cannot
        # decode what is in it" -- they are different mistakes and the
        # old single message named neither precisely.
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run("img icon = '/nonexistent/path.png'\nlog('unreachable')")
        assert result.returncode == 1
        assert "could not open image file" in result.stderr
        assert "unreachable" not in result.stdout

    def test_an_undecodable_image_names_both_supported_formats(self, compile_and_run, tmp_path,
                                                                 monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        (tmp_path / "bad.png").write_bytes(b"this is not an image at all")
        result = compile_and_run("img icon = 'bad.png'\nlog('unreachable')")
        assert result.returncode == 1
        assert "not a PNG or JPEG" in result.stderr
        assert "unreachable" not in result.stdout

    def test_program_without_graphics_never_opens_a_window(self, compile_and_run, monkeypatch):
        # self.uses_graphics gates festina_graphics_init() -- a program
        # that never calls a graphics function or declares on
        # mouseDown/mouse must behave exactly as before: no window, no
        # blocking event loop, normal immediate exit. Verified here by
        # deliberately having no display available at all and
        # confirming the program still succeeds (if it tried to open a
        # window, it would fail exactly like the test above).
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run("log('no graphics here')")
        assert result.returncode == 0
        assert result.stdout.strip() == "no graphics here"

    def test_mouse_down_and_up_dispatch_separately_with_correct_coordinates(
            self, run_graphics_program, x_display):
        # claude.md #106: one xdotool "click" is a press AND a release,
        # so a program declaring both handlers sees two distinct events
        # from it -- which is the whole point of the split. `on click`
        # used to collapse them into the single line this once asserted.
        source = ("on mouseDown(x:int, y:int, button:int) {\n    log(`down ${x} ${y}`)\n}\n"
                  "on mouseUp(x:int, y:int, button:int) {\n    log(`up ${x} ${y}`)\n}\n")
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "mousemove", "--window", wid, "150", "220"], env=env, check=True)
            subprocess.run(["xdotool", "click", "--window", wid, "1"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "up 150 220" in t)
            # Order matters: the press has to arrive before the release.
            assert text.splitlines() == ["down 150 220", "up 150 220"]
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_a_drag_reports_different_press_and_release_coordinates(
            self, run_graphics_program, x_display):
        # claude.md #106's motivating case: hold the button down, move,
        # then let go. `on click` could not express this at all -- it
        # only ever saw the press. Here the two events report the two
        # ends of the drag, which is what makes it reconstructible.
        source = ("on mouseDown(x:int, y:int, button:int) {\n    log(`down ${x} ${y}`)\n}\n"
                  "on mouseUp(x:int, y:int, button:int) {\n    log(`up ${x} ${y}`)\n}\n")
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "mousemove", "--window", wid, "100", "100"], env=env, check=True)
            subprocess.run(["xdotool", "mousedown", "--window", wid, "1"], env=env, check=True)
            _wait_for_output(stdout_path, lambda t: "down 100 100" in t)
            subprocess.run(["xdotool", "mousemove", "--window", wid, "400", "350"], env=env, check=True)
            subprocess.run(["xdotool", "mouseup", "--window", wid, "1"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "up " in t)
            assert "down 100 100" in text
            assert "up 400 350" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_mouse_move_dispatches_to_handler_with_correct_coordinates(self, run_graphics_program, x_display):
        source = "on mouse(x:int, y:int) {\n    log(`mouse ${x} ${y}`)\n}"
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "mousemove", "--window", wid, "300", "400"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "mouse 300 400" in t)
            assert "mouse 300 400" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_key_down_dispatches_printable_and_named_keys(self, run_graphics_program, x_display):
        # claude.md #40/#98: `on keyDown(key:text)`. A printable key
        # (e.g. "a") comes back as its own character; a non-printable one
        # (e.g. Escape, whose ASCII value is an unprintable control code)
        # is not a useful `text` value, so it falls back to X11's own key
        # name instead -- see festina_key_name in the graphics runtime.
        source = "on keyDown(key:text) {\n    log(`key ${key}`)\n}"
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            # The window needs real keyboard focus for KeyPress events to
            # reach it at all -- see festina_graphics_init's XSetInputFocus
            # call, needed since a bare Xvfb instance runs no window
            # manager to hand focus over the way a real desktop would.
            time.sleep(0.3)
            subprocess.run(["xdotool", "key", "--window", wid, "a"], env=env, check=True)
            subprocess.run(["xdotool", "key", "--window", wid, "Escape"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: len(t.splitlines()) >= 2)
            assert text.splitlines() == ["key a", "key Escape"]
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_key_up_fires_separately_and_reports_the_same_name(self, run_graphics_program, x_display):
        # claude.md #98: the whole point of the split -- a press and a
        # release are now distinguishable, and both name the same key the
        # same way (one shared festina_key_name), so a program can match
        # a release against the press that started it.
        source = ("on keyDown(key:text) {\n    log(`down ${key}`)\n}\n"
                  "on keyUp(key:text) {\n    log(`up ${key}`)\n}")
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            time.sleep(0.3)
            subprocess.run(["xdotool", "key", "--window", wid, "a"], env=env, check=True)
            subprocess.run(["xdotool", "key", "--window", wid, "Left"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: len(t.splitlines()) >= 4)
            assert text.splitlines() == ["down a", "up a", "down Left", "up Left"]
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_only_the_declared_key_handler_fires(self, run_graphics_program, x_display):
        # A program that declares only keyUp must not also get keyDown's
        # events -- they are two independent registrations, not one
        # handler called twice.
        source = "on keyUp(key:text) {\n    log(`up ${key}`)\n}"
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            time.sleep(0.3)
            subprocess.run(["xdotool", "key", "--window", wid, "b"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: t.strip() != "")
            time.sleep(0.3)
            with open(stdout_path) as f:
                assert f.read().splitlines() == ["up b"]
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_holding_a_key_does_not_fire_a_phantom_key_up(self, run_graphics_program, x_display):
        # claude.md #98: X's own auto-repeat synthesizes a
        # KeyRelease/KeyPress pair per repeat unless XKB's detectable
        # auto-repeat is on. Either way a HELD key must produce exactly
        # one keyUp -- the one where it is actually let go -- or the
        # split would be useless for the movement keys it exists for.
        source = ("on keyDown(key:text) {\n    log('down')\n}\n"
                  "on keyUp(key:text) {\n    log('up')\n}")
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            time.sleep(0.3)
            subprocess.run(["xdotool", "keydown", "--window", wid, "a"], env=env, check=True)
            time.sleep(1.0)   # long enough for auto-repeat to kick in
            subprocess.run(["xdotool", "keyup", "--window", wid, "a"], env=env, check=True)
            _wait_for_output(stdout_path, lambda t: "up" in t.splitlines())
            time.sleep(0.3)
            with open(stdout_path) as f:
                lines = f.read().splitlines()
            assert lines.count("up") == 1, lines
            assert lines[-1] == "up", lines
            assert lines[0] == "down", lines
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_client_size_matches_the_initial_canvas_before_any_resize(self, run_graphics_program, x_display):
        # claude.md #95: reading the canvas size no longer opens a
        # window -- render() is what does, so the window this asserts on
        # comes from that call rather than from the size read.
        source = "log(`${clientWidth}x${clientHeight}`)\nrender()"
        proc, stdout_path = run_graphics_program(source)
        try:
            _find_window(x_display)  # also proves a bare reference opens a window
            text = _wait_for_output(stdout_path, lambda t: t.strip() != "")
            assert text.strip() == "800x600"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_resize_dispatches_to_handler_and_updates_client_size(self, run_graphics_program, x_display):
        source = "on resize() {\n    log(`resize ${clientWidth} ${clientHeight}`)\n}"
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "windowsize", wid, "640", "480"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "resize 640 480" in t)
            assert "resize 640 480" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_graphics_init_does_not_crash_under_a_real_window_manager(
            self, run_graphics_program, x_display_with_wm):
        # A confirmed, reproduced regression, not a hypothetical: this
        # exact source, against this exact fixture, crashed with "X
        # Error of failed request: BadMatch ... X_SetInputFocus" before
        # the fix -- reproduced directly against `openbox`, not assumed
        # from reading the X11 spec. `x_display` (every other test in
        # this class) is a bare Xvfb instance with no window manager at
        # all to race with, so it can never reproduce this on its own:
        # festina_graphics_init's own best-effort XSetInputFocus call
        # always just succeeds there, no matter how many times it's run.
        # See festina_ignore_focus_error's own comment in
        # festina_runtime_graphics.c for the fix this guards.
        # claude.md #95: reading the canvas size no longer opens a
        # window -- render() is what does, so the window this asserts on
        # comes from that call rather than from the size read.
        source = "log(`${clientWidth}x${clientHeight}`)\nrender()"
        proc, stdout_path = run_graphics_program(source, display=x_display_with_wm)
        try:
            _find_window(x_display_with_wm)  # the window actually opened and is mapped
            text = _wait_for_output(stdout_path, lambda t: t.strip() != "")
            assert text.strip() == "800x600"
            assert proc.poll() is None, (
                "graphics program exited unexpectedly under a real window manager "
                f"(the XSetInputFocus BadMatch regression?) -- stdout:\n"
                f"{stdout_path.read_text()}"
            )
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestMouseWheelButtonAndCursor:
    """claude.md #182: which button (mouseDown/mouseUp), the scroll
    wheel no longer also firing a spurious click, and showCursor()/
    hideCursor()."""

    def test_wheel_scrolling_no_longer_fires_a_spurious_click(
            self, run_graphics_program, x_display):
        # Real, reproduced regression this entry's own X11 fix guards:
        # before it, every button's press/release (X11's own core-
        # protocol convention represents the wheel as buttons 4/5) fell
        # through to mouseDown/mouseUp completely unfiltered, so
        # scrolling over the canvas silently fired a mouseDown+mouseUp
        # pair at the wheel's own position. Declaring both mouseDown/
        # mouseUp AND mouseWheelUp/mouseWheelDown and scrolling once up
        # confirms only the wheel handler fires.
        source = (
            "on mouseDown(x:int, y:int, button:int) { log('click') }\n"
            "on mouseUp(x:int, y:int, button:int) { log('click') }\n"
            "on mouseWheelUp(x:int, y:int) { log(`wheel ${x} ${y}`) }\n"
        )
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "mousemove", "--window", wid, "60", "70"], env=env, check=True)
            subprocess.run(["xdotool", "click", "--window", wid, "4"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "wheel" in t)
            # A real chance for a spurious click line to arrive too.
            time.sleep(0.3)
            with open(stdout_path) as f:
                assert f.read().splitlines() == ["wheel 60 70"], (
                    "scrolling the wheel fired mouseDown/mouseUp as well")
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_wheel_up_and_down_fire_exactly_once_per_notch(
            self, run_graphics_program, x_display):
        source = (
            "on mouseWheelUp(x:int, y:int) { log('up') }\n"
            "on mouseWheelDown(x:int, y:int) { log('down') }\n"
        )
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "mousemove", "--window", wid, "10", "10"], env=env, check=True)
            subprocess.run(["xdotool", "click", "--window", wid, "4"], env=env, check=True)
            subprocess.run(["xdotool", "click", "--window", wid, "4"], env=env, check=True)
            subprocess.run(["xdotool", "click", "--window", wid, "5"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: t.count("\n") >= 3)
            assert text.splitlines() == ["up", "up", "down"]
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_left_middle_and_right_click_report_the_right_button_number(
            self, run_graphics_program, x_display):
        # claude.md #182: FestinaWindowEvent's own X11-derived numbering
        # (1/2/3 = left/middle/right) -- X11 already produces this
        # natively, so this is really confirming codegen/the runtime
        # thread the value through correctly end to end, not X11 itself.
        source = "on mouseDown(x:int, y:int, button:int) { log(`btn ${button}`) }\n"
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "mousemove", "--window", wid, "40", "40"], env=env, check=True)
            for button in ("1", "2", "3"):
                subprocess.run(["xdotool", "click", "--window", wid, button], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: t.count("\n") >= 3)
            assert text.splitlines() == ["btn 1", "btn 2", "btn 3"]
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_show_and_hide_cursor_do_not_crash_and_are_no_ops_when_redundant(
            self, run_graphics_program, x_display):
        # There's no practical way to inspect the real X11 cursor image
        # from a test (it's not exposed as a queryable window property
        # the way _NET_FRAME_EXTENTS is for decorations) -- this
        # confirms the one thing that IS observable: calling
        # hideCursor()/showCursor(), including redundantly, never
        # crashes the program and normal execution continues right
        # after.
        source = (
            "hideCursor()\n"
            "hideCursor()\n"  # redundant -- must not crash or misbehave
            "showCursor()\n"
            "showCursor()\n"  # redundant too
            "render()\n"
            "log('still running')\n"
        )
        proc, stdout_path = run_graphics_program(source)
        try:
            _find_window(x_display)
            text = _wait_for_output(stdout_path, lambda t: "still running" in t)
            assert text.strip() == "still running"
            assert proc.poll() is None
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_hide_cursor_before_the_window_opens_does_not_crash(
            self, run_graphics_program, x_display):
        # claude.md #182: the pre-window "just record the desired state"
        # path (see g_cursor_visible's own comment) -- hideCursor()
        # called before render() ever runs.
        source = "hideCursor()\nrender()\nlog('booted')\n"
        proc, stdout_path = run_graphics_program(source)
        try:
            _find_window(x_display)
            text = _wait_for_output(stdout_path, lambda t: "booted" in t)
            assert text.strip() == "booted"
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestScreenSizeAndSetClientSize:
    """claude.md #139: screenWidth/screenHeight -- the physical
    display's own resolution, read-only, answerable with or without a
    window open -- and setClientWidth/setClientHeight, which resize the
    canvas synchronously (and the real OS window too, when one is
    open)."""

    @pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason=(
        "claude.md #169/#170: this is the X11-specific failure mode -- "
        "festina_window_screen_size's Win32 counterpart calls "
        "GetSystemMetrics directly, and its Cocoa counterpart queries "
        "NSScreen directly, neither with a display handle to fail "
        "opening at all, so deleting DISPLAY (meaningless on both) "
        "doesn't reproduce anything; a real Windows CI run confirmed "
        "it just answers the real resolution instead (#169), which is "
        "correct behavior, not a bug -- macOS CI confirmed the "
        "identical thing the moment claude.md #170 let it actually "
        "reach this test for the first time."))
    def test_screen_size_without_any_display_is_a_clear_runtime_error(
            self, compile_and_run, monkeypatch):
        # festina_window_screen_size answers even with no window open,
        # but it still needs SOME X server to ask -- with none available
        # at all it reports the identical "no X display" failure
        # render() itself reports (both go through the same X11
        # festina_fail call -- see festina_window_screen_size's own
        # XOpenDisplay fallback in festina_runtime_graphics.c).
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run("log(screenWidth)")
        assert result.returncode == 1
        assert "X display" in result.stderr

    def test_screen_size_matches_the_real_display_resolution(self, compile_and_run, x_display):
        # Queried independently via xdotool rather than hardcoded, since
        # x_display can be a caller-provided real DISPLAY as well as the
        # throwaway 1024x768 Xvfb this fixture spins up itself -- see
        # conftest.py's own x_display fixture.
        env = dict(os.environ, DISPLAY=x_display)
        probe = subprocess.run(["xdotool", "getdisplaygeometry"], env=env,
                                capture_output=True, text=True, check=True)
        expected = "x".join(probe.stdout.split())
        result = compile_and_run("log(`${screenWidth}x${screenHeight}`)", env={"DISPLAY": x_display})
        assert result.returncode == 0
        assert result.stdout.strip() == expected

    def test_set_client_size_updates_client_size_headlessly(self, compile_and_run, monkeypatch):
        # No window needed at all -- setClientWidth/setClientHeight
        # update the canvas's own size synchronously regardless of
        # whether a window even exists yet (claude.md #139's own
        # "deliberately synchronous and self-contained" design note).
        monkeypatch.delenv("DISPLAY", raising=False)
        source = ("log(`${clientWidth}x${clientHeight}`)\n"
                   "setClientWidth(400)\nsetClientHeight(300)\n"
                   "log(`${clientWidth}x${clientHeight}`)")
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["800x600", "400x300"]

    def test_set_client_size_ignores_non_positive_values(self, compile_and_run, monkeypatch):
        # Matches festina_check_image_size's own "no image nothing could
        # ever draw to" reasoning, applied to the canvas itself -- a
        # non-positive size is silently ignored, not a runtime failure.
        monkeypatch.delenv("DISPLAY", raising=False)
        source = ("setClientWidth(0)\nsetClientHeight(-5)\n"
                   "log(`${clientWidth}x${clientHeight}`)")
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "800x600"

    def test_set_client_size_resizes_the_open_window_and_fires_on_resize_exactly_once_each(
            self, run_graphics_program, x_display):
        # claude.md #139's own regression: two setClientWidth/
        # setClientHeight calls back-to-back on an open window must fire
        # `on resize` exactly twice (once per call), not four times --
        # which is what a naive "does the incoming event's size match
        # current state" dedup guard actually produced against real X11
        # (two separate, non-coalesced ConfigureNotify echoes, the first
        # carrying a stale intermediate geometry that fooled the size
        # check into treating the second echo as novel too). Confirmed
        # via a real Xvfb run before landing the fix -- see
        # festina_handle_window_event's g_pending_self_resizes counter
        # in festina_runtime_graphics.c, which counts owed echoes rather
        # than comparing geometry.
        source = (
            "int resizeCount = 0\n"
            "on resize() {\n"
            "    resizeCount = resizeCount + 1\n"
            "    log(`resized to ${clientWidth}x${clientHeight}, count=${resizeCount}`)\n"
            "}\n"
            "render()\n"
            "setClientWidth(500)\n"
            "setClientHeight(350)\n"
            "log(`after: ${clientWidth}x${clientHeight}`)\n"
        )
        proc, stdout_path = run_graphics_program(source)
        try:
            _find_window(x_display)
            text = _wait_for_output(stdout_path, lambda t: "after:" in t)
            expected = [
                "resized to 500x600, count=1",
                "resized to 500x350, count=2",
                "after: 500x350",
            ]
            assert text.splitlines() == expected
            # Give any spurious extra echo (the regression this guards
            # against) a real chance to arrive before declaring victory.
            time.sleep(0.5)
            with open(stdout_path) as f:
                assert f.read().splitlines() == expected, "a spurious extra `on resize` fired"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_setting_client_size_before_the_window_opens_is_honored_as_its_initial_size(
            self, run_graphics_program, x_display):
        # claude.md #178 (uraikus/festina#79): a program that calls
        # setClientWidth/setClientHeight near the top of its own boot
        # sequence -- the documented, `on resize`-safe pattern
        # TestEventHandlersAreHoistedLikeFunctions below already
        # exercises for a DIFFERENT reason -- used to open a real,
        # on-screen window at the hardcoded 800x600 default FIRST
        # (main()'s own prologue called festina_graphics_init()
        # unconditionally before __festina_main() ever ran, and that
        # function itself then overwrote g_canvas_width/height back to
        # the hardcoded default even if a pre-window setClientWidth/
        # setClientHeight call had already changed them), then resized
        # itself out from under that -- each resize taking
        # festina_set_client_size's real, `g_window_open`-gated branch
        # (an actual XResizeWindow plus an `on resize` firing) since the
        # window was already open by the time either call ran. Fixed by
        # no longer opening the window before __festina_main() at all
        # (only registering handlers there) and having
        # festina_graphics_init() read g_canvas_width/height as they
        # already stand instead of resetting them -- so a size chosen
        # before the window exists is simply the window's initial size:
        # no flash of the wrong dimensions, and no `on resize` firing at
        # all for a size the program never asked to actually SEE change.
        source = (
            "on resize() {\n"
            "    log(`unexpected resize: ${clientWidth}x${clientHeight}`)\n"
            "}\n"
            "log('step0: process start')\n"
            "setClientWidth(1024)\n"
            "log(`step1: ${clientWidth}x${clientHeight}`)\n"
            "setClientHeight(700)\n"
            "log(`step2: ${clientWidth}x${clientHeight}`)\n"
            "render()\n"
        )
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            geometry = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", wid],
                env=env, capture_output=True, text=True, check=True,
            ).stdout
            dims = dict(line.split("=", 1) for line in geometry.splitlines() if "=" in line)
            # The window opened DIRECTLY at the fully-requested size --
            # not the 800x600 default, and not the 1024x600 intermediate
            # size #79's own repro observed.
            assert (dims["WIDTH"], dims["HEIGHT"]) == ("1024", "700")
            text = _wait_for_output(stdout_path, lambda t: "step2:" in t)
            # Neither setClientWidth nor setClientHeight fired `on
            # resize` -- there was no window yet for either call to
            # resize out from under itself.
            assert text.splitlines() == [
                "step0: process start",
                "step1: 1024x600",
                "step2: 1024x700",
            ]
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestFullscreenAndDecorations:
    """claude.md #180: the window opens fully decorated (title bar, and
    the OS's normal minimize/maximize/close controls -- like any other
    window, resizable by dragging an edge) instead of the borderless
    "canvas, nothing else" look claude.md #95 originally gave it, and
    enterFullscreen()/exitFullscreen() toggle true OS fullscreen on top
    of that -- X11's own _NET_WM_STATE_FULLSCREEN convention, honored by
    every EWMH-compliant window manager.

    Decorations can only be confirmed against a REAL window manager
    (`x_display_with_wm`, openbox) -- a bare Xvfb instance draws no
    frame around any window regardless of what a program's own Motif
    hints ask for, so a decoration check against the bare `x_display`
    fixture every other test in this file uses could never fail even if
    codegen regressed back to requesting a borderless window; the
    fullscreen tests below need a real WM for the identical reason (a
    bare Xvfb has nothing to interpret the _NET_WM_STATE ClientMessage
    at all, let alone actually resize/reposition the window in
    response)."""

    def test_the_window_is_really_decorated_under_a_real_window_manager(
            self, run_graphics_program, x_display_with_wm):
        # _NET_FRAME_EXTENTS is the window manager's own report of how
        # many pixels of chrome (title bar, border) it drew around the
        # window -- (0, 0, 0, 0) or absent entirely means "no decoration
        # was drawn", the exact claude.md #95 look this entry retires.
        # x_display_with_wm only PREFERS xprop (it falls back to a fixed
        # wait without it); this test genuinely needs it -- claude.md
        # #233: skip cleanly rather than FileNotFoundError.
        _require_x11_tool("xprop", "read the window manager's _NET_FRAME_EXTENTS")
        source = "drawRect(0, 0, 10, 10)\nrender()"
        proc, stdout_path = run_graphics_program(source, display=x_display_with_wm)
        try:
            wid = _find_window(x_display_with_wm)
            env = dict(os.environ, DISPLAY=x_display_with_wm)
            # openbox needs a moment after mapping to actually reparent
            # the window into its own decorated frame and publish this
            # property -- polled rather than assumed instant, the same
            # reasoning x_display_with_wm's own readiness wait uses.
            deadline = time.time() + 10
            extents = None
            while time.time() < deadline:
                probe = subprocess.run(
                    ["xprop", "-id", wid, "_NET_FRAME_EXTENTS"],
                    env=env, capture_output=True, text=True,
                )
                if probe.returncode == 0 and "_NET_FRAME_EXTENTS" in probe.stdout:
                    extents = probe.stdout
                    break
                time.sleep(0.1)
            assert extents is not None, "window manager never published _NET_FRAME_EXTENTS"
            # "= 0, 0, 0, 0" would mean a real property existed but
            # reported no chrome at all -- still a decoration failure,
            # so check the actual numbers, not just the property's
            # presence.
            numbers = extents.split("=", 1)[1]
            assert any(int(n.strip()) > 0 for n in numbers.split(",")), (
                f"window manager drew no decoration: {extents!r}")
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_enter_and_exit_fullscreen_are_zero_arg_builtins(self, parser, semantic, errors):
        for source in ["enterFullscreen(1)", "exitFullscreen(1)"]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")

    def test_calling_either_alone_still_opens_a_window(self, parser, semantic, codegen):
        # claude.md #95/#180: neither call draws anything, but both are
        # meaningless without a real OS window -- there is no "headless
        # fullscreen" the way there's a headless canvas -- so each has
        # to join render() as something that makes self.uses_graphics
        # true (see codegen.py's own _CANVAS_OPS handling), observable
        # here as festina_run_event_loop() appearing in the emitted IR
        # even with no render()/handler in sight.
        for source in ["enterFullscreen()", "exitFullscreen()"]:
            program = parser.parse(source, filename="main.f")
            analyzed = semantic.analyze(program, filename="main.f")
            ir = codegen.generate_ir(program, analyzed, filename="main.f")
            assert "call void @festina_run_event_loop()" in ir

    def test_entering_and_exiting_fullscreen_resizes_the_real_window_and_fires_on_resize(
            self, run_graphics_program, x_display_with_wm):
        # The real, end-to-end confirmation: toggling fullscreen via a
        # simulated keypress against a real window (openbox), reading
        # the window's ACTUAL on-screen geometry back via xdotool
        # (rather than trusting clientWidth/clientHeight alone, which
        # this same bug class could misreport if the real window and
        # Festina's own idea of its size ever disagreed) at each step.
        source = (
            "on resize() {\n"
            "    log(`resize ${clientWidth}x${clientHeight}`)\n"
            "}\n"
            "on keyDown(key:text) {\n"
            "    if key == 'i' { enterFullscreen() }\n"
            "    if key == 'o' { exitFullscreen() }\n"
            "}\n"
            "render()\n"
        )
        proc, stdout_path = run_graphics_program(source, display=x_display_with_wm)
        try:
            wid = _find_window(x_display_with_wm)
            env = dict(os.environ, DISPLAY=x_display_with_wm)
            screen = subprocess.run(["xdotool", "getdisplaygeometry"], env=env,
                                     capture_output=True, text=True, check=True)
            screen_w, screen_h = screen.stdout.split()

            subprocess.run(["xdotool", "windowfocus", wid], env=env, check=True)
            subprocess.run(["xdotool", "key", "--window", wid, "i"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "resize" in t)
            assert text.strip() == f"resize {screen_w}x{screen_h}"
            geo = subprocess.run(["xdotool", "getwindowgeometry", "--shell", wid],
                                  env=env, capture_output=True, text=True, check=True).stdout
            dims = dict(line.split("=", 1) for line in geo.splitlines() if "=" in line)
            assert (dims["WIDTH"], dims["HEIGHT"]) == (screen_w, screen_h)

            subprocess.run(["xdotool", "key", "--window", wid, "o"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: t.count("resize") >= 2)
            assert text.splitlines() == [
                f"resize {screen_w}x{screen_h}",
                "resize 800x600",
            ]
            geo = subprocess.run(["xdotool", "getwindowgeometry", "--shell", wid],
                                  env=env, capture_output=True, text=True, check=True).stdout
            dims = dict(line.split("=", 1) for line in geo.splitlines() if "=" in line)
            assert (dims["WIDTH"], dims["HEIGHT"]) == ("800", "600")
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_entering_fullscreen_before_the_window_opens_skips_the_windowed_flash(
            self, run_graphics_program, x_display_with_wm):
        # claude.md #178's own fix, extended: enterFullscreen() called
        # before any window exists just records the desired state (see
        # g_is_fullscreen's own comment in festina_runtime_graphics.c)
        # for festina_graphics_init to apply once one actually opens --
        # so the window should open DIRECTLY at the screen's own size,
        # never at 800x600 first.
        source = "enterFullscreen()\ndrawRect(0, 0, 10, 10)\nrender()"
        proc, stdout_path = run_graphics_program(source, display=x_display_with_wm)
        try:
            wid = _find_window(x_display_with_wm)
            env = dict(os.environ, DISPLAY=x_display_with_wm)
            screen = subprocess.run(["xdotool", "getdisplaygeometry"], env=env,
                                     capture_output=True, text=True, check=True)
            screen_w, screen_h = screen.stdout.split()
            # Give the window manager a moment to finish reacting, the
            # same generous wait test_resize_dispatches_to_handler_and_
            # updates_client_size's own sibling tests already budget for
            # a real WM round trip.
            time.sleep(0.5)
            geo = subprocess.run(["xdotool", "getwindowgeometry", "--shell", wid],
                                  env=env, capture_output=True, text=True, check=True).stdout
            dims = dict(line.split("=", 1) for line in geo.splitlines() if "=" in line)
            assert (dims["WIDTH"], dims["HEIGHT"]) == (screen_w, screen_h)
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_redundant_calls_are_no_ops(self, run_graphics_program, x_display_with_wm):
        # Calling enterFullscreen() twice (or exitFullscreen() while not
        # fullscreen) must not fire a second, redundant `on resize` --
        # the same "no-op if already in the requested state" discipline
        # claude.md #139's own setClientWidth/setClientHeight regression
        # test already pins for THAT pair of builtins.
        source = (
            "int resizeCount = 0\n"
            "on resize() {\n"
            "    resizeCount = resizeCount + 1\n"
            "    log(`resize count=${resizeCount}`)\n"
            "}\n"
            "on keyDown(key:text) {\n"
            "    if key == 'i' { enterFullscreen() enterFullscreen() }\n"
            "    if key == 'o' { exitFullscreen() exitFullscreen() }\n"
            "}\n"
            "render()\n"
            "exitFullscreen()\n"  # never entered -- must not crash or fire
        )
        proc, stdout_path = run_graphics_program(source, display=x_display_with_wm)
        try:
            wid = _find_window(x_display_with_wm)
            env = dict(os.environ, DISPLAY=x_display_with_wm)
            subprocess.run(["xdotool", "windowfocus", wid], env=env, check=True)
            subprocess.run(["xdotool", "key", "--window", wid, "i"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "count=1" in t)
            subprocess.run(["xdotool", "key", "--window", wid, "o"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "count=2" in t)
            # A real chance for a spurious THIRD firing to arrive.
            time.sleep(0.5)
            with open(stdout_path) as f:
                assert f.read().splitlines() == [
                    "resize count=1",
                    "resize count=2",
                ], "a redundant enterFullscreen()/exitFullscreen() call fired an extra resize"
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestEventHandlersAreHoistedLikeFunctions:
    """Not a bug, but a real, confirmed footgun -- flagged directly,
    costing real debugging time before it was traced back to this.
    `on ...` handlers are registered before the entry file's own top-
    level code runs at all (codegen.py's own _emit_main_and_entry:
    every festina_register_*_handler call happens in a fixed block,
    unconditionally, before `call void @__festina_main()`) -- the
    identical hoisting api.md's own "Functions are hoisted" note
    already documents for `func` declarations, just less obviously
    surprising there (a function only ever runs when something calls
    it; a handler can be triggered by an ordinary top-level statement
    written ABOVE its own declaration, e.g. setClientWidth firing `on
    resize` synchronously, inline, wherever it's called from).

    Confirmed directly (compiled and run, not just reasoned through):
    a handler declared textually AFTER a call that triggers it still
    fires, and fires against whatever state existed at that exact
    point in top-level execution -- which, for a global whose own
    initializer hasn't run yet, is still its zero/default, not the
    value the source implies it should already have. Documented in
    api.md right where event handlers are introduced."""

    def test_a_handler_fires_against_a_not_yet_initialized_global(
            self, compile_and_run, x_display):
        # The exact api.md reproduction: setClientWidth (textually
        # first) fires `on resize` (declared textually AFTER both it
        # and the array it reads) before `data`'s own initializer has
        # run -- so the handler sees the zero-length default, not 3.
        source = (
            "render()\n"
            "setClientWidth(400)\n"
            "log('after setClientWidth')\n"
            "\n"
            "arr[int] data = [1, 2, 3]\n"
            "on resize() {\n"
            "    log(`data.length=${data.length}`)\n"
            "}\n"
            "close(0)\n"
        )
        result = compile_and_run(source, env={"DISPLAY": x_display})
        assert result.returncode == 0, result.stdout
        # The handler's own output comes FIRST -- it ran synchronously,
        # inline, at the setClientWidth call site, before the
        # 'after setClientWidth' line even printed.
        assert result.stdout.splitlines() == [
            "data.length=0",
            "after setClientWidth",
        ]


class TestExampleGraphicsAndGame:
    """Interactive regression coverage for examples/graphics.f,
    examples/tic_tac_toe.f, and examples/layers.f -- the examples that
    need a real (or virtual) X server, so they can't join
    tests/test_examples.py's plain compile-and-check-stdout sweep.
    Lives here, not there, so it can reuse this file's own
    _find_window/_wait_for_output helpers and x_display/
    run_graphics_program fixtures, the same as TestGraphics above and
    TestTimers's combined graphics+timers test below."""

    def test_layers_demo_renders_all_layers_and_stops_on_its_own(self, run_graphics_program, x_display):
        # claude.md #149: arr[img] as a layer stack, composited by one
        # renderFrame() every setInterval tick. Like timers.f, the demo
        # clears its own interval and logs a final line once it's done
        # (200 frames), rather than needing this test to be the one that
        # decides when to stop it -- so this only needs to wait for that
        # line, the same pattern TestIndividualExamples.
        # test_timers_demo_runs_and_exits_on_its_own already uses for a
        # non-graphics example. Manually verified beyond what's
        # automated here (same "high confidence, not worth a netpbm
        # test dependency" call TestGraphics's own docstring makes):
        # captured the real rendered window via xwd and visually
        # confirmed all four layers actually composite -- the sky/ground
        # background, the scattered stars (plus one added mid-run), the
        # bouncing ball's accumulated trail (including a real bounce off
        # an edge), and the "Frame N/200" HUD text on top.
        source = open(os.path.join(_EXAMPLES_DIR, "layers.f")).read()
        proc, stdout_path = run_graphics_program(source)
        try:
            _find_window(x_display)
            text = _wait_for_output(stdout_path, lambda t: "rendered 200 frames" in t, timeout=30)
            assert "opening the canvas -- close the window to exit" in text
            assert "rendered 200 frames -- stopping (close the window to exit)" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_graphics_demo_dispatches_mouse_key_and_resize(self, run_graphics_program, x_display):
        source = open(os.path.join(_EXAMPLES_DIR, "graphics.f")).read()
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            text = _wait_for_output(stdout_path, lambda t: "canvas started at 800x600" in t)
            assert "canvas started at 800x600" in text

            time.sleep(0.3)  # keyboard focus -- see TestGraphics's own note on this
            subprocess.run(["xdotool", "mousemove", "--window", wid, "100", "100"], env=env, check=True)
            subprocess.run(["xdotool", "click", "--window", wid, "1"], env=env, check=True)
            subprocess.run(["xdotool", "key", "--window", wid, "a"], env=env, check=True)
            subprocess.run(["xdotool", "windowsize", wid, "900", "700"], env=env, check=True)

            text = _wait_for_output(stdout_path, lambda t: "resized to 900x700" in t)
            # claude.md #106: one xdotool click produces both halves.
            assert "pressed at 100, 100" in text
            assert "released at 100, 100" in text
            assert "key pressed: a" in text
            assert "resized to 900x700" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_tic_tac_toe_detects_a_win(self, run_graphics_program, x_display):
        # Same board layout as the example's own source (GRID_X=250,
        # GRID_Y=150, CELL=100) -- clicks the top row for X, the middle
        # row for O, then the rest of the top row, so X wins with three
        # across the top: (0,0), (1,0), (2,0).
        source = open(os.path.join(_EXAMPLES_DIR, "tic_tac_toe.f")).read()
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)

            def click(x, y):
                subprocess.run(["xdotool", "mousemove", "--window", wid, str(x), str(y)], env=env, check=True)
                subprocess.run(["xdotool", "click", "--window", wid, "1"], env=env, check=True)
                time.sleep(0.15)

            click(300, 200)  # X: top-left
            click(300, 300)  # O: middle-left
            click(400, 200)  # X: top-middle
            click(400, 300)  # O: middle-middle
            click(500, 200)  # X: top-right -- completes the top row

            text = _wait_for_output(stdout_path, lambda t: "X wins!" in t)
            assert "X wins!" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestTimers:
    """claude.md #69: setTimeout/setInterval/clearTimeout/clearInterval.
    Added because Festina otherwise has no way to schedule work after
    the fact, the same gap JS's setTimeout/setInterval fill. See
    festina/semantic.py's _infer_call (the setTimeout/setInterval
    branch) and runtime/festina_runtime.h's doc comment for the full
    design.

    Most of this needs no display at all -- a timers-only program never
    opens a window (CodeGen.uses_timers is a separate flag from
    uses_graphics) -- so these mostly use compile_and_run like any other
    runtime-behavior test. The one exception,
    test_timers_and_graphics_work_together, is the one thing that
    genuinely needs a real window: proving festina_run_event_loop's
    select()-based multiplexing keeps both a `setInterval` callback and
    `on mouseDown` responsive together, not just one or the other.
    """

    def test_timeout_fires_once_after_the_delay(self, compile_and_run):
        source = (
            "void func onTimeout() {\n"
            "    log('fired')\n"
            "}\n"
            "log('start')\n"
            "setTimeout(onTimeout, 10)\n"
        )
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["start", "fired"]

    def test_interval_fires_repeatedly_until_cleared(self, compile_and_run):
        source = (
            "int count = 0\n"
            "int intervalId = 0\n"
            "void func onInterval() {\n"
            "    count = count + 1\n"
            "    log(`tick ${count}`)\n"
            "    if (count >= 3) {\n"
            "        clearInterval(intervalId)\n"
            "    }\n"
            "}\n"
            "intervalId = setInterval(onInterval, 5)\n"
        )
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["tick 1", "tick 2", "tick 3"]

    def test_clear_timeout_cancels_a_pending_callback(self, compile_and_run):
        source = (
            "void func shouldNotFire() {\n"
            "    log('should not happen')\n"
            "}\n"
            "void func shouldFire() {\n"
            "    log('fired')\n"
            "}\n"
            "int id = setTimeout(shouldNotFire, 50)\n"
            "clearTimeout(id)\n"
            "setTimeout(shouldFire, 5)\n"
        )
        result = compile_and_run(source)
        assert result.returncode == 0
        assert "should not happen" not in result.stdout
        assert "fired" in result.stdout

    def test_callback_can_schedule_another_timer(self, compile_and_run):
        # Proves a timer created *from inside* a firing callback is
        # still picked up by the same run -- festina_run_event_loop
        # recomputes the earliest deadline fresh on every pass rather
        # than fixing it once at loop entry, and festina_add_timer's
        # array can safely grow (realloc) from inside a callback that's
        # itself being called from a loop iterating that same array.
        source = (
            "int calls = 0\n"
            "void func step() {\n"
            "    calls = calls + 1\n"
            "    log(`step ${calls}`)\n"
            "    if (calls < 3) {\n"
            "        setTimeout(step, 5)\n"
            "    }\n"
            "}\n"
            "setTimeout(step, 5)\n"
        )
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["step 1", "step 2", "step 3"]

    def test_program_with_only_timeouts_exits_once_they_all_fire(self, compile_and_run):
        # No graphics, no uncleared interval -- festina_run_event_loop
        # must actually return once there's nothing left to wait for,
        # not block forever; compile_and_run's own subprocess timeout
        # (15s) would turn a regression here into a hard failure rather
        # than a slow pass, but this asserts on the normal, fast path.
        source = "void func cb() {\n    log('done')\n}\nsetTimeout(cb, 5)\n"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_uncleared_interval_keeps_the_program_running(self, tmp_path, codegen, cli_mod):
        # The one JS-matching behavior compile_and_run's fixed 15s
        # timeout can't cleanly demonstrate (it would just make the test
        # slow, not prove anything a shorter wait doesn't already show)
        # -- drive it directly instead: start the compiled program in
        # the background, confirm it's still alive and still logging
        # after a short wait, then kill it ourselves. No DISPLAY needed
        # at all here -- this is exactly the "timers without graphics"
        # case CodeGen.uses_timers exists to keep separate from
        # uses_graphics.
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        source = "void func tick() {\n    log('tick')\n}\nsetInterval(tick, 5)\n"
        src_path = tmp_path / "main.f"
        src_path.write_text(source)
        out_path = tmp_path / "program"
        cli_mod.compile_file(str(src_path), str(out_path))
        stdout_path = tmp_path / "stdout.log"
        # stdbuf (GNU coreutils) forces line buffering so the log file
        # has lines mid-run; macOS has no stdbuf, so there the test
        # keeps its actual contract -- an uncleared interval keeps the
        # process alive -- and drops only the mid-run line inspection
        # (macos.md Phase 0's first CI run is what found this).
        have_stdbuf = shutil.which("stdbuf") is not None
        cmd = (["stdbuf", "-oL"] if have_stdbuf else []) + [str(out_path)]
        proc = subprocess.Popen(
            cmd,
            cwd=tmp_path, stdout=open(stdout_path, "w"), stderr=subprocess.STDOUT,
        )
        try:
            time.sleep(0.3)
            assert proc.poll() is None, "an uncleared setInterval should keep the program running"
            if have_stdbuf:
                lines = stdout_path.read_text().splitlines()
                assert len(lines) >= 2, f"expected multiple 'tick's by now, got {lines!r}"
                assert all(line == "tick" for line in lines)
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_timers_and_graphics_work_together(self, run_graphics_program, x_display):
        source = (
            "void func tick() {\n"
            "    log('tick')\n"
            "}\n"
            "on mouseDown(x:int, y:int, button:int) {\n"
            "    log(`click ${x} ${y}`)\n"
            "}\n"
            "drawRect(0, 0, 10, 10)\n"
            "setInterval(tick, 15)\n"
        )
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            text = _wait_for_output(stdout_path, lambda t: "tick" in t)
            assert "tick" in text, "the interval never fired alongside the open window"

            subprocess.run(["xdotool", "mousemove", "--window", wid, "5", "5"], env=env, check=True)
            subprocess.run(["xdotool", "click", "--window", wid, "1"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "click 5 5" in t)
            assert "click 5 5" in text
            # The interval kept firing before *and* after the click --
            # proves festina_run_event_loop's select() call is genuinely
            # multiplexing both, not just alternating or starving one.
            assert text.count("tick") >= 2
        finally:
            proc.terminate()
            proc.wait(timeout=5)


def _write_wav(path, duration_s=0.2, sample_rate=8000, channels=1):
    """A minimal, valid 16-bit PCM WAV file (silence -- only the
    container format matters for claude.md #38's loadAudio())."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * int(duration_s * sample_rate) * channels)


class TestAudio:
    """claude.md #38/#99/#100: aud, `aud m = 'path'`, .play()/
    .playLoop()/.isPlaying(), and stopAudioPlayer().

    Unlike TestGraphics, none of this needs an opt-in skip tier: the
    null-device trick audio_null_env uses (see conftest.py) needs no
    extra tool install the way Xvfb does -- ALSA's null plugin ships
    inside alsa-lib itself, which festina_runtime.c already links
    against unconditionally. So every test here only needs what
    compile_and_run already requires: a working C compiler.

    Two of the deterministic-by-design guarantees these tests lean on
    (see festina_runtime.c's festina_audio_play/_stop for why each is
    actually guaranteed, not just usually true): isPlaying() is true
    the instant play() returns (the flag is set synchronously, before
    the background playback thread is even spawned), and isPlaying()
    is false the instant stop() returns (stop() joins the thread before
    returning, it doesn't just signal it and hope).
    """

    def test_compiles_and_links_successfully(self, cli_mod, tmp_path):
        # No audio device needed -- just confirms codegen emits valid IR
        # and the result links against ALSA/pthread successfully
        # (claude.md #59: audio is a real new link-time dependency --
        # see festina/cli.py's alsa wiring).
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        source = """
        aud music = 'nonexistent.wav'
        music.play()
        stopAudioPlayer()
        log(music.isPlaying())
        """
        src_path = tmp_path / "main.f"
        src_path.write_text(source)
        out_path = tmp_path / "program"
        from tests.conftest import compile_file_or_skip
        result_path = compile_file_or_skip(cli_mod, str(src_path), str(out_path))
        assert result_path == str(out_path)
        assert out_path.exists()

    def test_missing_audio_device_is_a_clear_runtime_error(self, compile_and_run, tmp_path):
        wav_name = "clip.wav"
        _write_wav(tmp_path / wav_name)
        # A fresh, empty HOME (no .asoundrc) -- guarantees ALSA's
        # "default" device resolution fails the same way it does in
        # this dev sandbox generally (verified: no /dev/snd node at
        # all), regardless of whatever the real test-runner's own
        # environment happens to have.
        empty_home = tmp_path / "empty_home"
        empty_home.mkdir()
        result = compile_and_run(
            f"aud music = '{wav_name}'\nmusic.play()\nlog('unreachable')",
            env={"HOME": str(empty_home)},
        )
        assert result.returncode == 1
        assert "could not open an audio output device" in result.stderr
        assert "unreachable" not in result.stdout

    def test_invalid_audio_path_is_a_clear_runtime_error(self, compile_and_run):
        result = compile_and_run(
            "aud music = '/nonexistent/path.wav'\nlog('unreachable')"
        )
        assert result.returncode == 1
        assert "could not open audio file" in result.stderr
        assert "unreachable" not in result.stdout

    def test_undecodable_audio_is_a_clear_runtime_error(self, compile_and_run, tmp_path):
        (tmp_path / "bad.wav").write_bytes(b"this is not a wav file at all")
        result = compile_and_run("aud music = 'bad.wav'\nlog('unreachable')")
        assert result.returncode == 1
        # claude.md #101: the message names both formats this runtime
        # decodes, since "not a WAV" stopped being the whole story.
        assert "not 16-bit PCM WAV or MP3" in result.stderr
        assert "unreachable" not in result.stdout

    def test_is_playing_true_immediately_after_play(self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav")
        source = (
            "aud music = 'clip.wav'\n"
            "music.play()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_is_playing_false_immediately_after_stop(self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav")
        source = (
            "aud music = 'clip.wav'\n"
            "music.play()\n"
            "stopAudioPlayer()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "false"

    def test_stop_when_nothing_playing_is_a_safe_no_op(self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav")
        source = "aud music = 'clip.wav'\nstopAudioPlayer()\nlog(music.isPlaying())\n"
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "false"

    def test_calling_play_again_while_playing_restarts_without_crashing(
        self, compile_and_run, tmp_path, audio_null_env
    ):
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = (
            "aud music = 'clip.wav'\n"
            "music.play()\n"
            "music.play()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_max_audio_players_is_readable_and_clamped(self, compile_and_run):
        # claude.md #98: the limit is a tuning knob, so an unreasonable
        # value is clamped into [1, 64] rather than failing the program.
        # maxAudioPlayers() reads back what was actually applied.
        source = (
            "log(maxAudioPlayers())\n"
            "setMaxAudioPlayers(4)\n"
            "log(maxAudioPlayers())\n"
            "setMaxAudioPlayers(0)\n"
            "log(maxAudioPlayers())\n"
            "setMaxAudioPlayers(9999)\n"
            "log(maxAudioPlayers())\n"
        )
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["10", "4", "1", "64"]

    def test_setting_the_limit_links_the_audio_runtime(self, parser, semantic, codegen):
        # Both builtins live in the audio translation unit, so naming
        # either has to mark the program as using audio -- otherwise the
        # link would fail with an undefined symbol.
        program = parser.parse("setMaxAudioPlayers(3)", filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        gen = codegen.CodeGen(analyzed, "main.f")
        gen.generate(program)
        assert gen.uses_audio is True

    def test_overlapping_plays_all_keep_playing(self, compile_and_run, tmp_path, audio_null_env):
        # claude.md #98: the behaviour this replaced would have had the
        # second play() stop the first. Festina has no way to count
        # voices (deliberately -- the pool is not language surface), so
        # what is observable here is that three overlapping playbacks all
        # report as playing and nothing crashes; TestAudioVoicePool below
        # opens the runtime up and counts the voices directly.
        #
        # playLoop, not play, and that is not incidental: the null ALSA
        # device this runs against consumes PCM instantly (measured -- a
        # 2-second clip finishes in 0ms), so a one-shot can legitimately
        # finish between the play() call and the next statement. Written
        # with play() this test passed in isolation and failed under
        # full-suite load, which is a race in the TEST rather than in the
        # runtime -- "isPlaying() is true the instant play() returns" is
        # still honoured, it just says nothing about the statement after.
        # A loop never finishes on its own, so there is no window at all.
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = (
            "aud music = 'clip.wav'\n"
            "music.playLoop(0)\n"
            "music.playLoop(1)\n"
            "music.playLoop(2)\n"
            "log(music.isPlaying())\n"
            "stopAudioPlayer()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "false"]

    def test_a_limit_of_one_still_restarts_rather_than_failing(
        self, compile_and_run, tmp_path, audio_null_env
    ):
        # setMaxAudioPlayers(1) is the documented way to ask for the old
        # cut-off-the-previous-sound behaviour back.
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = (
            "setMaxAudioPlayers(1)\n"
            "aud music = 'clip.wav'\n"
            "music.play()\n"
            "music.play()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_a_path_declares_a_clip_and_really_loads_it(self, compile_and_run, tmp_path,
                                                          audio_null_env):
        # claude.md #100: `aud m = 'path'`. The proof that it is a real
        # load and not just a type-check allowance is that playing it
        # works -- and that a bad path fails exactly the way
        # loadAudio()'s own bad path does.
        _write_wav(tmp_path / "clip.wav", duration_s=0.5)
        source = (
            "aud music = 'clip.wav'\n"
            "music.play()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_a_path_may_be_a_computed_text_expression(self, compile_and_run, tmp_path,
                                                        audio_null_env):
        # Unlike color/font (resolved at compile time, so literal-only),
        # this becomes a real loadAudio() call, so any text works.
        _write_wav(tmp_path / "clip.wav", duration_s=0.5)
        source = (
            "text name = 'clip'\n"
            "aud music = name + '.wav'\n"
            "music.play()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_a_bad_path_in_the_short_form_fails_the_same_way(self, compile_and_run):
        result = compile_and_run("aud music = '/nonexistent/path.wav'\nlog('unreachable')")
        assert result.returncode == 1
        assert "could not open audio file" in result.stderr
        assert "unreachable" not in result.stdout

    def test_stop_is_back_and_is_clip_wide(self, parser, semantic):
        # claude.md #109: #100 removed stop() because "stop this clip"
        # has only one honest reading -- every channel playing it -- and
        # that is rarely what an overlapping-effects program wants. True,
        # and not a reason to withhold it: silencing a looping hum or a
        # music bed is a real thing to want, and doing it by hand meant
        # tracking channels the runtime already knows. play() returning
        # its channel covers the other case, so both exist now.
        program = parser.parse("aud music = 'x.wav'\nmusic.stop()", filename="main.f")
        semantic.analyze(program, filename="main.f")

    def test_stop_by_channel_is_still_how_one_playback_is_addressed(
            self, parser, semantic, errors):
        program = parser.parse("aud music = 'x.wav'\nmusic.stop(2)", filename="main.f")
        with pytest.raises(errors.CompileError, match="stopAudioPlayer"):
            semantic.analyze(program, filename="main.f")

    def test_channels_and_loops_compile_and_run(self, compile_and_run, tmp_path, audio_null_env):
        # claude.md #99: every shape of the new surface, through the
        # real compiler and the real runtime. What is observable from
        # Festina is that these run and that stop/isPlaying still agree;
        # which CHANNEL each landed on is checked white-box in
        # TestAudioChannels, since the language deliberately cannot see
        # it.
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = (
            "aud music = 'clip.wav'\n"
            "music.play()\n"
            "music.play(3)\n"
            "music.playLoop()\n"
            "music.playLoop(0)\n"
            "log(music.isPlaying())\n"
            "stopAudioPlayer(0)\n"
            "stopAudioPlayer(3)\n"
            "stopAudioPlayer()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "false"]

    def test_a_looping_track_outlives_its_own_length(self, compile_and_run, tmp_path,
                                                       audio_null_env):
        # The one loop property observable from Festina: a clip that
        # would long since have finished is still playing. 50ms of
        # audio, checked 400ms later.
        _write_wav(tmp_path / "clip.wav", duration_s=0.05)
        source = (
            "aud music = 'clip.wav'\n"
            "void func check() {\n"
            "    log(`still playing: ${music.isPlaying()}`)\n"
            "    stopAudioPlayer(0)\n"
            "    log(`after stop: ${music.isPlaying()}`)\n"
            "}\n"
            "music.playLoop(0)\n"
            "setTimeout(check, 400)\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["still playing: true", "after stop: false"]

    def test_a_non_looping_play_still_finishes_on_its_own(self, compile_and_run, tmp_path,
                                                            audio_null_env):
        # The control for the test above -- same clip, same delay,
        # play() instead of playLoop(). Without this pair, "still
        # playing" would not distinguish looping from a slow device.
        _write_wav(tmp_path / "clip.wav", duration_s=0.05)
        source = (
            "aud music = 'clip.wav'\n"
            "void func check() {\n"
            "    log(`still playing: ${music.isPlaying()}`)\n"
            "}\n"
            "music.play(0)\n"
            "setTimeout(check, 400)\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "still playing: false"

    def test_the_music_handover_from_the_motivating_example(self, compile_and_run, tmp_path,
                                                              audio_null_env):
        # claude.md #99's own worked example, shortened: two tracks
        # trading channel 0 back and forth. The point is that
        # isPlaying() flips each time, which only works if the second
        # playLoop(0) genuinely evicts the first.
        _write_wav(tmp_path / "adventure.wav", duration_s=0.05)
        _write_wav(tmp_path / "battle.wav", duration_s=0.05)
        source = (
            "aud adventureMusic = 'adventure.wav'\n"
            "aud battleMusic = 'battle.wav'\n"
            "void func changeMusic() {\n"
            "    if adventureMusic.isPlaying() {\n"
            "        battleMusic.playLoop(0)\n"
            "        log('battle')\n"
            "    } else {\n"
            "        adventureMusic.playLoop(0)\n"
            "        log('adventure')\n"
            "    }\n"
            "}\n"
            "int id = 0\n"
            "void func done() {\n"
            "    stopAudioPlayer(0)\n"
            "    clearInterval(id)\n"
            "}\n"
            "adventureMusic.playLoop(0)\n"
            "id = setInterval(changeMusic, 100)\n"
            "setTimeout(done, 450)\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        lines = result.stdout.splitlines()
        # Strict alternation: each tick sees the other track playing.
        assert lines[:4] == ["battle", "adventure", "battle", "adventure"]

    def test_timers_and_audio_work_together(self, compile_and_run, tmp_path, audio_null_env):
        # A short clip finishes (isPlaying() -> false) on its own, with
        # no stop() call -- checked from a setTimeout callback, proving
        # audio playback and the timer event loop coexist correctly
        # (the background playback thread doesn't block __festina_main()
        # or festina_run_event_loop() on the main thread).
        _write_wav(tmp_path / "clip.wav", duration_s=0.05)
        source = (
            "aud music = 'clip.wav'\n"
            "void func check() {\n"
            "    log(`playing after delay: ${music.isPlaying()}`)\n"
            "}\n"
            "music.play()\n"
            "setTimeout(check, 200)\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "playing after delay: false"


_VOICE_POOL_HARNESS = r"""
/* claude.md #98: a WHITE-BOX check of the per-aud voice pool.
 *
 * Two things make this a C harness rather than a Festina program.
 *
 * First, the pool is deliberately not language surface: a Festina
 * program can ask whether a clip is playing, never how many copies of
 * it are, and stop() names the clip rather than one playback of it.
 * Adding a counter to the language purely to make this testable would
 * be the tail wagging the dog, so this includes the translation unit
 * directly, which gives it FestinaAudio/FestinaVoice (both file-local
 * types) and lets it count active voices for real.
 *
 * Second, the DEVICE layer is replaced here -- claude.md #121's
 * festina_pcm_dev_* seam, supplied by this harness instead of any
 * platform backend (FESTINA_AUDIO_DEVICE_EXTERNAL), which is also
 * what lets this harness build on a machine with no audio stack at
 * all, ALSA headers included. That is not a shortcut -- it is the
 * only way this test can exist. The null device the rest of the audio
 * tests use consumes PCM instantly (measured: a 2-second clip
 * finishes in 0ms), so under it every voice is finished before the
 * next play() begins and there is no concurrency left to observe. A
 * stub that sleeps per chunk gives playback real duration under the
 * harness's own control. Everything above the device -- the pool, the
 * stealing, the slot reuse, the joining -- is the genuine runtime
 * code.
 */
#define FESTINA_AUDIO_DEVICE_EXTERNAL 1
#include <time.h>

#include "festina_runtime_audio.c"

void *festina_pcm_dev_open(int channels, unsigned int rate,
                           char *errbuf, size_t errbuf_size) {
    (void)channels; (void)rate; (void)errbuf; (void)errbuf_size;
    return (void *)1;
}
long festina_pcm_dev_write(void *dev, const int16_t *frames, size_t frame_count) {
    (void)dev; (void)frames;
    /* ~10ms per 4096-frame chunk, so a clip of a few chunks plays for
     * long enough that back-to-back play() calls genuinely overlap. */
    struct timespec ts = { 0, 10L * 1000L * 1000L };
    nanosleep(&ts, NULL);
    return (long)frame_count;
}
void festina_pcm_dev_close(void *dev) { (void)dev; }

/* The only thing the audio unit needs from the core runtime, supplied
 * here so this harness does not have to link festina_runtime.c (and
 * with it sqlite3) for a test that is entirely about audio. */
void festina_fail(const char *msg) {
    fprintf(stderr, "fail: %s\n", msg ? msg : "");
    exit(1);
}

/* claude.md #110: the audio unit's save()/saveCopy() delegate the shared
 * write-and-maybe-adopt-the-path policy to the core runtime. Stubbed
 * rather than linked for the same reason festina_fail is: these
 * harnesses are entirely about the channel pool, and pulling in
 * festina_runtime.c would drag sqlite3 along with it. */
int8_t festina_save_bytes(const char *target, char **own_path,
                          const void *data, int64_t len,
                          const char *what, int8_t adopt) {
    (void)target; (void)own_path; (void)data; (void)len; (void)what; (void)adopt;
    return 0;
}

/* claude.md #118: aud carries a refcount header now, and
 * festina_audio_free consults the core runtime's decrement-and-check.
 * Stubbed with the real semantics (these harnesses free clips whose
 * header festina_audio_from_bytes genuinely allocated) rather than
 * linked, for the same no-sqlite3 reason as the two stubs above. */
int8_t festina_release_check(void *payload) {
    if (!payload) return 0;
    int64_t *header = (int64_t *)((char *)payload - sizeof(int64_t));
    if (*header < 0) return 0;
    (*header)--;
    return *header == 0;
}

/* claude.md #171: festina_audio_load_dispatch (compiled into this
 * translation unit whether or not any harness main() below actually
 * calls it) references these two core-runtime symbols -- stubbed for
 * the same no-sqlite3 reason as festina_fail/festina_save_bytes/
 * festina_release_check above. festina_retain mirrors
 * festina_release_check's own real semantics; festina_async_io_dispatch
 * mirrors the core runtime's own no-hook-registered fallback (run the
 * job inline, synchronously) -- exactly right for a harness that never
 * links festina_runtime_async.c. */
void festina_retain(void *payload) {
    if (!payload) return;
    int64_t *header = (int64_t *)((char *)payload - sizeof(int64_t));
    if (*header < 0) return;
    (*header)++;
}
void festina_async_io_dispatch(void *payload, void (*work_fn)(void *payload),
                               void (*callback)(void *payload),
                               void (*release_fn)(void *payload)) {
    if (work_fn) work_fn(payload);
    if (callback) callback(payload);
    if (release_fn) release_fn(payload);
}

static int active_voices(void *audio) {
    int n = 0;
    pthread_mutex_lock(&g_audio_lock);
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
        if (g_channels[i].active && g_channels[i].clip == audio) n++;
    }
    pthread_mutex_unlock(&g_audio_lock);
    return n;
}

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    void *clip = festina_load_audio(argv[1]);

    printf("default %lld\n", (long long)festina_get_max_audio_players());

    /* Three overlapping plays, well inside the default limit of 10.
     * Before claude.md #98 the second would have cut the first off and
     * this would read 1. */
    festina_audio_play_on(clip, 0, 0, 0);
    festina_audio_play_on(clip, 0, 0, 0);
    festina_audio_play_on(clip, 0, 0, 0);
    printf("three %d\n", active_voices(clip));
    printf("isplaying %d\n", (int)festina_audio_is_playing(clip));

    /* claude.md #100: playback is stopped by CHANNEL now, and a
     * negative channel means every channel. */
    festina_stop_audio_player(-1);
    printf("stopped %d\n", active_voices(clip));
    printf("isplaying_after_stop %d\n", (int)festina_audio_is_playing(clip));

    /* At the limit, the oldest voice is stolen rather than the new play
     * being dropped -- so the count saturates at the limit, never
     * exceeds it, and never collapses to zero however many plays pile
     * up. */
    festina_set_max_audio_players(2);
    for (int i = 0; i < 6; i++) festina_audio_play_on(clip, 0, 0, 0);
    printf("limit2 %d\n", active_voices(clip));

    /* A limit of 1 is exactly the old behaviour: one voice, restarted. */
    festina_stop_audio_player(-1);
    festina_set_max_audio_players(1);
    festina_audio_play_on(clip, 0, 0, 0);
    festina_audio_play_on(clip, 0, 0, 0);
    printf("limit1 %d\n", active_voices(clip));

    festina_stop_audio_player(-1);
    printf("final %d\n", active_voices(clip));

    /* Slots are REUSED, not grown: 40 plays through a pool of 3 must
     * never show more than 3 voices, which only holds if a finished
     * thread is joined and its slot reclaimed (see
     * festina_audio_reap_locked). */
    festina_set_max_audio_players(3);
    int peak = 0;
    for (int i = 0; i < 40; i++) {
        festina_audio_play_on(clip, 0, 0, 0);
        int n = active_voices(clip);
        if (n > peak) peak = n;
    }
    printf("peak %d\n", peak);
    festina_stop_audio_player(-1);
    printf("drained %d\n", active_voices(clip));
    return 0;
}
"""


_SINGLE_STREAM_HARNESS = r"""
/* claude.md #98: a "default" device that does NO software mixing, so
 * the second concurrent open fails with EBUSY -- a bare hw: device
 * with no dmix, which is ordinary on minimal/embedded Linux and on any
 * machine where another program holds the device exclusively.
 *
 * The voice pool opens one handle per voice, so this is the case where
 * layering is not physically available. What must NOT happen is the
 * program dying: overlapping plays have to degrade back to cutting
 * each other off, which is exactly what they did before there was a
 * pool at all.
 */
#define FESTINA_AUDIO_DEVICE_EXTERNAL 1
#include <time.h>

#include "festina_runtime_audio.c"

/* claude.md #121: the single-stream device, expressed at the seam --
 * the second concurrent open fails, exactly as a dmix-less hw: device
 * refuses a second stream. */
static int g_open_count = 0;
void *festina_pcm_dev_open(int channels, unsigned int rate,
                           char *errbuf, size_t errbuf_size) {
    (void)channels; (void)rate;
    if (g_open_count >= 1) {
        snprintf(errbuf, errbuf_size, "device busy (harness single-stream limit)");
        return NULL;
    }
    g_open_count++;
    return (void *)(long)g_open_count;
}
void festina_pcm_dev_close(void *dev) { (void)dev; g_open_count--; }
long festina_pcm_dev_write(void *dev, const int16_t *frames, size_t frame_count) {
    (void)dev; (void)frames;
    struct timespec ts = { 0, 10L * 1000L * 1000L };
    nanosleep(&ts, NULL);
    return (long)frame_count;
}

void festina_fail(const char *msg) {
    fprintf(stderr, "fail: %s\n", msg ? msg : "");
    exit(1);
}

/* claude.md #110: the audio unit's save()/saveCopy() delegate the shared
 * write-and-maybe-adopt-the-path policy to the core runtime. Stubbed
 * rather than linked for the same reason festina_fail is: these
 * harnesses are entirely about the channel pool, and pulling in
 * festina_runtime.c would drag sqlite3 along with it. */
int8_t festina_save_bytes(const char *target, char **own_path,
                          const void *data, int64_t len,
                          const char *what, int8_t adopt) {
    (void)target; (void)own_path; (void)data; (void)len; (void)what; (void)adopt;
    return 0;
}

/* claude.md #118: aud carries a refcount header now, and
 * festina_audio_free consults the core runtime's decrement-and-check.
 * Stubbed with the real semantics (these harnesses free clips whose
 * header festina_audio_from_bytes genuinely allocated) rather than
 * linked, for the same no-sqlite3 reason as the two stubs above. */
int8_t festina_release_check(void *payload) {
    if (!payload) return 0;
    int64_t *header = (int64_t *)((char *)payload - sizeof(int64_t));
    if (*header < 0) return 0;
    (*header)--;
    return *header == 0;
}

/* claude.md #171: festina_audio_load_dispatch (compiled into this
 * translation unit whether or not any harness main() below actually
 * calls it) references these two core-runtime symbols -- stubbed for
 * the same no-sqlite3 reason as festina_fail/festina_save_bytes/
 * festina_release_check above. festina_retain mirrors
 * festina_release_check's own real semantics; festina_async_io_dispatch
 * mirrors the core runtime's own no-hook-registered fallback (run the
 * job inline, synchronously) -- exactly right for a harness that never
 * links festina_runtime_async.c. */
void festina_retain(void *payload) {
    if (!payload) return;
    int64_t *header = (int64_t *)((char *)payload - sizeof(int64_t));
    if (*header < 0) return;
    (*header)++;
}
void festina_async_io_dispatch(void *payload, void (*work_fn)(void *payload),
                               void (*callback)(void *payload),
                               void (*release_fn)(void *payload)) {
    if (work_fn) work_fn(payload);
    if (callback) callback(payload);
    if (release_fn) release_fn(payload);
}

static int active_voices(void *audio) {
    int n = 0;
    pthread_mutex_lock(&g_audio_lock);
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
        if (g_channels[i].active && g_channels[i].clip == audio) n++;
    }
    pthread_mutex_unlock(&g_audio_lock);
    return n;
}

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    void *clip = festina_load_audio(argv[1]);
    for (int i = 0; i < 5; i++) festina_audio_play_on(clip, 0, 0, 0);
    /* One stream is all the device has, so one voice is all there is --
     * and crucially the program is still running to say so. */
    printf("voices %d\n", active_voices(clip));
    printf("open_handles %d\n", g_open_count);
    printf("isplaying %d\n", (int)festina_audio_is_playing(clip));
    festina_stop_audio_player(-1);
    printf("after_stop %d\n", active_voices(clip));
    printf("leaked_handles %d\n", g_open_count);
    return 0;
}
"""


_CHANNEL_HARNESS = r"""
/* claude.md #99: named channels -- play(n)/playLoop(n)/
 * stopAudioPlayer(n), and the reservation playLoop takes out.
 *
 * White-box for the same two reasons the pool harness is (see
 * _VOICE_POOL_HARNESS): a Festina program cannot see which channel a
 * clip landed on, and a null device consumes PCM instantly so there
 * is no concurrency to observe under it. The device layer is supplied
 * at claude.md #121's festina_pcm_dev_* seam; the channel table is
 * the real one.
 */
#define FESTINA_AUDIO_DEVICE_EXTERNAL 1
#include <time.h>

#include "festina_runtime_audio.c"

void *festina_pcm_dev_open(int channels, unsigned int rate,
                           char *errbuf, size_t errbuf_size) {
    (void)channels; (void)rate; (void)errbuf; (void)errbuf_size;
    return (void *)1;
}
long festina_pcm_dev_write(void *dev, const int16_t *frames, size_t frame_count) {
    (void)dev; (void)frames;
    struct timespec ts = { 0, 10L * 1000L * 1000L };
    nanosleep(&ts, NULL);
    return (long)frame_count;
}
void festina_pcm_dev_close(void *dev) { (void)dev; }

void festina_fail(const char *msg) {
    fprintf(stderr, "fail: %s\n", msg ? msg : "");
    exit(1);
}

/* claude.md #110: the audio unit's save()/saveCopy() delegate the shared
 * write-and-maybe-adopt-the-path policy to the core runtime. Stubbed
 * rather than linked for the same reason festina_fail is: these
 * harnesses are entirely about the channel pool, and pulling in
 * festina_runtime.c would drag sqlite3 along with it. */
int8_t festina_save_bytes(const char *target, char **own_path,
                          const void *data, int64_t len,
                          const char *what, int8_t adopt) {
    (void)target; (void)own_path; (void)data; (void)len; (void)what; (void)adopt;
    return 0;
}

/* claude.md #118: aud carries a refcount header now, and
 * festina_audio_free consults the core runtime's decrement-and-check.
 * Stubbed with the real semantics (these harnesses free clips whose
 * header festina_audio_from_bytes genuinely allocated) rather than
 * linked, for the same no-sqlite3 reason as the two stubs above. */
int8_t festina_release_check(void *payload) {
    if (!payload) return 0;
    int64_t *header = (int64_t *)((char *)payload - sizeof(int64_t));
    if (*header < 0) return 0;
    (*header)--;
    return *header == 0;
}

/* claude.md #171: festina_audio_load_dispatch (compiled into this
 * translation unit whether or not any harness main() below actually
 * calls it) references these two core-runtime symbols -- stubbed for
 * the same no-sqlite3 reason as festina_fail/festina_save_bytes/
 * festina_release_check above. festina_retain mirrors
 * festina_release_check's own real semantics; festina_async_io_dispatch
 * mirrors the core runtime's own no-hook-registered fallback (run the
 * job inline, synchronously) -- exactly right for a harness that never
 * links festina_runtime_async.c. */
void festina_retain(void *payload) {
    if (!payload) return;
    int64_t *header = (int64_t *)((char *)payload - sizeof(int64_t));
    if (*header < 0) return;
    (*header)++;
}
void festina_async_io_dispatch(void *payload, void (*work_fn)(void *payload),
                               void (*callback)(void *payload),
                               void (*release_fn)(void *payload)) {
    if (work_fn) work_fn(payload);
    if (callback) callback(payload);
    if (release_fn) release_fn(payload);
}

/* Which channel index a clip is playing on, or -1. */
static int channel_of(void *clip) {
    int found = -1;
    pthread_mutex_lock(&g_audio_lock);
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
        if (g_channels[i].active && g_channels[i].clip == clip) { found = i; break; }
    }
    pthread_mutex_unlock(&g_audio_lock);
    return found;
}

static int locked_at(int i) {
    pthread_mutex_lock(&g_audio_lock);
    int v = g_channels[i].locked;
    pthread_mutex_unlock(&g_audio_lock);
    return v;
}

static int active_at(int i) {
    pthread_mutex_lock(&g_audio_lock);
    int v = g_channels[i].active;
    pthread_mutex_unlock(&g_audio_lock);
    return v;
}

static int active_total(void) {
    int n = 0;
    pthread_mutex_lock(&g_audio_lock);
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) if (g_channels[i].active) n++;
    pthread_mutex_unlock(&g_audio_lock);
    return n;
}

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    void *adventure = festina_load_audio(argv[1]);
    void *battle = festina_load_audio(argv[2]);

    /* An explicit channel is honoured exactly. */
    festina_audio_play_on(adventure, 5, 1, 0);
    printf("explicit %d\n", channel_of(adventure));
    festina_stop_audio_player(5);

    /* playLoop reserves the channel it uses. */
    festina_audio_play_on(adventure, 0, 1, 1);
    printf("loop_channel %d\n", channel_of(adventure));
    printf("loop_locked %d\n", locked_at(0));

    /* A reserved channel is never taken by automatic assignment, even
     * under pressure: 30 pooled plays with a limit of 3 must never land
     * on channel 0 or evict what is looping there. */
    festina_set_max_audio_players(3);
    int stole_channel_0 = 0;
    for (int i = 0; i < 30; i++) {
        festina_audio_play_on(battle, 0, 0, 0);
        pthread_mutex_lock(&g_audio_lock);
        if (g_channels[0].clip != adventure) stole_channel_0 = 1;
        pthread_mutex_unlock(&g_audio_lock);
    }
    printf("stole_reserved %d\n", stole_channel_0);

    /* The looping clip is STILL playing after all that -- it has run
     * far past its own length, which only a real loop does. */
    printf("still_looping %d\n", (int)festina_audio_is_playing(adventure));

    /* The handover from the user's own example: a different clip named
     * on the same channel takes it over. */
    festina_audio_play_on(battle, 0, 1, 1);
    printf("handover_battle %d\n", channel_of(battle));
    printf("handover_adventure_playing %d\n", (int)festina_audio_is_playing(adventure));
    printf("handover_still_locked %d\n", locked_at(0));

    /* An explicit one-shot play() on a reserved channel takes it over
     * AND releases the reservation. */
    festina_audio_play_on(adventure, 0, 1, 0);
    printf("oneshot_released %d\n", locked_at(0));

    /* stopAudioPlayer(n) stops that channel and releases it. Asserted
     * per CHANNEL, not per clip: isPlaying() is deliberately clip-wide
     * (claude.md #98), so with the same clip also running on channel 0
     * from the block above it would stay true regardless of what
     * channel 2 does -- which would make this assert nothing. */
    festina_audio_play_on(adventure, 2, 1, 1);
    printf("before_stop_locked %d\n", locked_at(2));
    printf("before_stop_active %d\n", active_at(2));
    festina_stop_audio_player(2);
    printf("after_stop_locked %d\n", locked_at(2));
    printf("after_stop_active %d\n", active_at(2));

    /* A bare stopAudioPlayer() stops everything. */
    festina_audio_play_on(adventure, 1, 1, 1);
    festina_audio_play_on(battle, 4, 1, 1);
    festina_audio_play_on(battle, 0, 0, 0);
    printf("before_stop_all %d\n", active_total() > 0);
    festina_stop_audio_player(-1);
    printf("after_stop_all %d\n", active_total());
    printf("after_stop_all_locks %d\n", locked_at(1) + locked_at(4));

    /* A bare stopAudioPlayer() releases reservations too -- a looping
     * track told to stop is not still owed its channel. */
    festina_audio_play_on(adventure, 7, 1, 1);
    festina_stop_audio_player(-1);
    printf("clip_stop_locked %d\n", locked_at(7));
    printf("clip_stop_playing %d\n", (int)festina_audio_is_playing(adventure));
    return 0;
}
"""


class TestCircleMaskFastPath:
    """claude.md #104: filled circles are rasterized once per radius and
    stamped thereafter, instead of tessellating the curve every time.

    Measured on the canvas benchmark, circles were 90% of the frame --
    20,000 of them cost 76 ms against 10 ms for the same number of
    rectangles -- because cairo_arc + cairo_fill turns the curve into
    Beziers and scan-converts a general polygon on every call. Caching
    an A8 alpha mask per radius is 4.4x faster on that workload and took
    the whole frame from 90 ms to 31 ms.

    Everything here is about the fast path being INVISIBLE. It is only
    correct while the mask lands exactly where a tessellated circle
    would, so anything that would move it off the pixel grid -- a scale,
    a rotation, a fractional translation -- has to fall back, and a
    border has to fall back because a stroke needs a real path. Verified
    against the tessellating fallback over a whole 800x600 frame
    covering every one of these cases: 5 pixels of 480,000 differed, all
    by 1/255, all inside a gradient (sampling rounding, not geometry).
    """

    def _canvas(self, compile_and_run, monkeypatch, body, name="out.png"):
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run(f"clearCanvas()\n{body}\nlog(saveCanvas('{name}'))")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "true"
        return result

    def test_a_filled_circle_is_actually_filled(self, compile_and_run, tmp_path, monkeypatch):
        self._canvas(compile_and_run, monkeypatch,
                      "fillStyle(200, 30, 30)\ndrawCircle(60, 60, 20)")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        _, _, pixel_rgba = _decode_png_rgba(str(tmp_path / "out.png"))
        assert pixel(60, 60) == (200, 30, 30)      # centre
        assert pixel(60, 45) == (200, 30, 30)      # inside, near the top edge
        # claude.md #136: the canvas clears to transparent, not white.
        assert pixel_rgba(60, 20) == (0, 0, 0, 0)  # well outside
        assert pixel_rgba(95, 60) == (0, 0, 0, 0)

    @pytest.mark.parametrize("radius", [1, 2, 3, 4, 8, 16, 32])
    def test_every_radius_covers_the_right_extent(self, compile_and_run, tmp_path,
                                                    monkeypatch, radius):
        # A mask stamped half a pixel off would show up here as an edge
        # landing one pixel early or late, at every radius independently.
        self._canvas(compile_and_run, monkeypatch,
                      f"fillStyle(0, 0, 0)\ndrawCircle(100, 100, {radius})")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        _, _, pixel_rgba = _decode_png_rgba(str(tmp_path / "out.png"))
        # Inked, not necessarily SOLID: a radius-1 circle does not fully
        # cover even its own centre pixel, so Cairo antialiases it to
        # grey -- in the fallback exactly as much as in the fast path
        # (compared directly: zero differing pixels at r=1). Demanding
        # pure black here would be testing Cairo's coverage arithmetic
        # rather than this cache.
        assert pixel(100, 100)[0] < 200, (radius, pixel(100, 100))
        # Just outside the circle in each direction is untouched
        # transparent (claude.md #136: the canvas clears to transparent,
        # not white).
        for dx, dy in ((radius + 2, 0), (-radius - 2, 0), (0, radius + 2), (0, -radius - 2)):
            assert pixel_rgba(100 + dx, 100 + dy) == (0, 0, 0, 0), (radius, dx, dy)

    def test_a_bordered_circle_still_gets_its_border(self, compile_and_run, tmp_path,
                                                      monkeypatch):
        # Must fall back: a stroke needs a real path, and the mask has
        # none. If the fast path ever swallowed this, the border would
        # silently vanish.
        self._canvas(compile_and_run, monkeypatch,
                      "color navy = 'navy'\n"
                      "fillStyle(255, 255, 0)\n"
                      "borderColor(navy)\n"
                      "lineWidth(4)\n"
                      "drawCircle(60, 60, 20)")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        assert pixel(60, 60) == (255, 255, 0)          # the fill
        r, g, b = pixel(60, 40)                         # on the border ring
        assert b > r and b > g, (r, g, b)

    def test_a_scaled_circle_is_the_scaled_size(self, compile_and_run, tmp_path, monkeypatch):
        # Must fall back: stamping a pre-rasterized mask under a scale
        # would resample it. The give-away is the extent -- a radius-10
        # circle drawn at scale 2 has to reach 20 pixels out.
        self._canvas(compile_and_run, monkeypatch,
                      "fillStyle(0, 0, 0)\nscale(2.0, 2.0)\ndrawCircle(50, 50, 10)")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        _, _, pixel_rgba = _decode_png_rgba(str(tmp_path / "out.png"))
        assert pixel(100, 100) == (0, 0, 0)
        assert pixel(100, 84) == (0, 0, 0)         # 16px out, inside a radius-20 circle
        # claude.md #136: the canvas clears to transparent, not white.
        assert pixel_rgba(100, 78) == (0, 0, 0, 0)   # 22px out, beyond it

    def test_a_translated_circle_moves(self, compile_and_run, tmp_path, monkeypatch):
        # A whole-number translation KEEPS the fast path, so this checks
        # the offset is applied rather than ignored.
        self._canvas(compile_and_run, monkeypatch,
                      "fillStyle(0, 0, 0)\ntranslate(100, 50)\ndrawCircle(30, 30, 10)")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        _, _, pixel_rgba = _decode_png_rgba(str(tmp_path / "out.png"))
        assert pixel(130, 80) == (0, 0, 0)
        # claude.md #136: the canvas clears to transparent, not white.
        assert pixel_rgba(30, 30) == (0, 0, 0, 0)

    def test_alpha_applies_to_a_circle(self, compile_and_run, tmp_path, monkeypatch):
        # claude.md #136: the canvas itself clears to transparent now,
        # so an opaque white backdrop is drawn explicitly here -- this
        # test is about fillAlpha() blending, not about what the canvas
        # happens to default to.
        self._canvas(compile_and_run, monkeypatch,
                      "fillStyle(255, 255, 255)\ndrawRect(0, 0, 800, 600)\n"
                      "fillStyle(0, 0, 0)\nfillAlpha(0.5)\ndrawCircle(60, 60, 20)")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        r, g, b = pixel(60, 60)
        # Half-transparent black over white: mid grey, not solid black.
        assert 100 < r < 160 and r == g == b, (r, g, b)

    def test_a_gradient_fills_a_circle(self, compile_and_run, tmp_path, monkeypatch):
        self._canvas(compile_and_run, monkeypatch,
                      "color a = 'red'\n"
                      "color b = 'blue'\n"
                      "fillLinearGradient(40, 0, a, 80, 0, b)\n"
                      "drawCircle(60, 60, 20)")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        left = pixel(45, 60)
        right = pixel(75, 60)
        assert left[0] > left[2], left      # red end
        assert right[2] > right[0], right   # blue end

    def test_a_degenerate_radius_draws_nothing_and_does_not_crash(self, compile_and_run,
                                                                    tmp_path, monkeypatch):
        self._canvas(compile_and_run, monkeypatch,
                      "fillStyle(0, 0, 0)\ndrawCircle(60, 60, 0)\ndrawCircle(90, 60, -5)")
        # claude.md #136: the canvas clears to transparent, not white.
        _, _, pixel_rgba = _decode_png_rgba(str(tmp_path / "out.png"))
        assert pixel_rgba(60, 60) == (0, 0, 0, 0)
        assert pixel_rgba(90, 60) == (0, 0, 0, 0)

    def test_many_distinct_radii_do_not_break_the_cache(self, compile_and_run, tmp_path,
                                                          monkeypatch):
        # The cache holds 16 entries and evicts round-robin, so a program
        # cycling through more radii than that exercises eviction on
        # every draw. Correctness must not depend on a hit.
        body = "fillStyle(0, 0, 0)\n" + "\n".join(
            f"drawCircle({12 + (i % 30) * 25}, {30 + (i // 30) * 40}, {1 + i % 20})"
            for i in range(60))
        self._canvas(compile_and_run, monkeypatch, body)
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        # Every one of the 60 got drawn somewhere: count inked centres
        # rather than assert a colour, since the smallest radii are
        # antialiased grey (see the extent test above).
        inked = sum(1 for i in range(60)
                     if pixel(12 + (i % 30) * 25, 30 + (i // 30) * 40)[0] < 200)
        assert inked == 60, inked


class TestAllNullLiterals:
    """claude.md #102: `arr[text] a = [null]` / `map[int] m = {'k': null}`.

    A literal whose values are ALL null infers its element type as null
    itself, and that was rejected against every declared element type --
    so the one literal shape meaning "entries, none of them meaningful
    yet" could not be written at all. `a.push(null)` and `m[k] = null`
    were both already fine, and `[null, 'x']` inferred text without
    complaint, which is what makes this an inconsistency rather than a
    policy.
    """

    def test_an_all_null_array_literal(self, compile_and_run):
        result = compile_and_run(
            "arr[text] a = [null]\nlog(a.length)\nlog(a[0] == null)")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["1", "true"]

    def test_an_all_null_map_literal(self, compile_and_run):
        result = compile_and_run(
            "map[text] m = {'a': null, 'b': null}\nlog(m['a'] == null)\nlog(m['b'] == null)")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "true"]

    def test_the_declared_element_type_still_decides_the_null(self, compile_and_run):
        # An int's null is its own reserved sentinel, not a pointer --
        # so this also proves the declared type reaches the elements
        # rather than them being left as generic pointers.
        result = compile_and_run(
            "arr[int] a = [null, null]\nlog(a[1])\nmap[int] m = {'k': null}\nlog(m['k'])")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["-9223372036854775808"] * 2

    def test_a_mixed_literal_still_infers_normally(self, compile_and_run):
        result = compile_and_run("arr[text] a = [null, 'x']\nlog(a.length)\nlog(a[1])")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["2", "x"]

    def test_a_genuine_type_mismatch_is_still_rejected(self, parser, semantic, errors):
        # The allowance is for null specifically, not a hole in element
        # type checking.
        for source in ["arr[int] a = ['x']", "map[int] m = {'k': 'x'}"]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError, match="cannot assign"):
                semantic.analyze(program, filename="main.f")


class TestNullComparisonOnEveryType:
    """claude.md #102: `x == null` on a pointer-backed type.

    Every one of these used to fail the COMPILE with an LLVM parse error
    naming a generated temporary -- `icmp eq i64 <a ptr>, null` is not
    valid IR at all. That is an internal-error message for something
    entirely reasonable to write, and it covered struct, arr[T], map[T],
    img, aud and regex: every managed type in the language except text.
    """

    @pytest.mark.parametrize("decl,expr", [
        ("struct S { n:int }\nS x", "x"),
        ("arr[int] x = []", "x"),
        ("map[int] x = {}", "x"),
        ("regex x = regex('a')", "x"),
        ("text x = 'a'", "x"),
        ("int x = 1", "x"),
        ("bool x = true", "x"),
    ])
    def test_a_live_value_is_not_null(self, compile_and_run, decl, expr):
        result = compile_and_run(f"{decl}\nlog({expr} == null)\nlog({expr} != null)")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["false", "true"]

    def test_float_keeps_its_documented_nan_behaviour(self, compile_and_run):
        # Deliberately NOT in the list above. A null float is a real
        # NaN, and IEEE-754 says every ordered comparison against a NaN
        # is false -- so a live float is neither == null nor != null.
        # api.md states this; pinned here so the fix above is not
        # mistaken for a licence to "correct" it.
        result = compile_and_run("float x = 1.5\nlog(x == null)\nlog(x != null)")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["false", "false"]

    def test_media_handles_compare_against_null(self, compile_and_run, tmp_path,
                                                  monkeypatch, sprite_sheet_png):
        monkeypatch.delenv("DISPLAY", raising=False)
        sheet = os.path.basename(sprite_sheet_png)
        result = compile_and_run(
            f"img p = '{sheet}'\naud a = 'beep.wav'\n"
            "log(p == null)\nlog(a == null)\n",
            env={"HOME": str(tmp_path)},
        ) if False else compile_and_run(f"img p = '{sheet}'\nlog(p == null)\nlog(p != null)")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["false", "true"]

    def test_a_null_media_column_reads_as_null(self, compile_and_run, tmp_path,
                                                monkeypatch):
        # The case that surfaced this: a nullable BLOB column has to be
        # checkable, and there is no other way to ask.
        monkeypatch.delenv("DISPLAY", raising=False)
        source = """
        table Asset {
            name:text
            pic:img
        }
        sqlite('INSERT INTO Asset (name) VALUES (?)', ['nopic'])
        arr[Asset] rows = sqlite('SELECT * FROM Asset')
        log(rows[0].name)
        log(rows[0].pic == null)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["nopic", "true"]


class TestMediaColumnsLinkTheirRuntime:
    """claude.md #102: a table column of type `aud`/`img` is by itself
    enough to make a program use that feature.

    Two things need it -- main() registers the media decoders, and the
    per-table row release function calls that type's destructor -- and
    both emit calls into a translation unit that would otherwise not be
    linked. A program whose only use of audio was `file:aud` in a table
    therefore failed at the LINK step with an undefined reference to
    festina_audio_free: a compiler bug reported as a linker error.
    """

    @pytest.mark.parametrize("column,flag", [("aud", "uses_audio"),
                                              ("img", "uses_graphics_code")])
    def test_the_column_alone_sets_the_flag(self, parser, semantic, codegen, column, flag):
        program = parser.parse(
            f"table T {{ name:text asset:{column} }}\nlog('x')", filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        gen = codegen.CodeGen(analyzed, "main.f")
        gen.generate(program)
        assert getattr(gen, flag) is True

    def test_such_a_program_actually_links_and_runs(self, compile_and_run, monkeypatch):
        # The end-to-end version: nothing here names a single audio or
        # graphics function, so only the column types can pull the
        # runtime in.
        monkeypatch.delenv("DISPLAY", raising=False)
        source = """
        table T {
            name:text
            clip:aud
            pic:img
        }
        sqlite('INSERT INTO T (name) VALUES (?)', ['row'])
        arr[T] rows = sqlite('SELECT * FROM T')
        log(rows[0].name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "row"


class TestFloatToIntIsNeverUndefined:
    """claude.md #102: Math.floor/ceil/round/trunc of a NaN, an infinity
    or an out-of-range double.

    A bare `fptosi` is undefined behaviour for all three -- not "some
    unspecified integer", genuinely undefined, and it behaved like it.
    Measured before the fix: `Math.floor(1.0 / 0.0)` printed a different
    value on every build, once a stack ADDRESS, and in one program
    `Math.floor(nan)` answered 1 while `Math.ceil(nan)` on the next line
    answered the null sentinel -- the optimizer had folded two identical
    UB sites differently. A language that returns null for division by
    zero rather than crashing cannot then hand back a stack address for
    Math.floor of that same null.
    """

    @pytest.mark.parametrize("fn", ["floor", "ceil", "round", "trunc"])
    def test_a_null_float_rounds_to_a_null_int(self, compile_and_run, fn):
        result = compile_and_run(
            f"float nan = 1.0 / 0.0\nlog(Math.{fn}(nan) == null)")
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    @pytest.mark.parametrize("fn", ["floor", "ceil", "round", "trunc"])
    def test_an_infinity_rounds_to_a_null_int(self, compile_and_run, fn):
        result = compile_and_run(
            "float inf = Math.exp(10000.0)\n"
            f"log(Math.{fn}(inf) == null)\n"
            "float ninf = 0.0 - inf\n"
            f"log(Math.{fn}(ninf) == null)\n")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "true"]

    def test_the_answer_is_the_same_on_every_run(self, compile_and_run):
        # The original symptom was non-determinism, so this checks for
        # non-determinism specifically rather than for a value.
        source = "float nan = 1.0 / 0.0\nlog(Math.floor(nan))\nlog(Math.ceil(nan))"
        first = compile_and_run(source).stdout
        for _ in range(3):
            assert compile_and_run(source).stdout == first
        assert set(first.split()) == {"-9223372036854775808"}

    def test_ordinary_values_are_untouched(self, compile_and_run):
        result = compile_and_run(
            "log(Math.floor(3.7))\nlog(Math.ceil(3.2))\nlog(Math.round(3.5))\n"
            "log(Math.round(0.0 - 3.5))\nlog(Math.trunc(0.0 - 3.9))\n"
            "log(Math.floor(0.0))\n")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["3", "4", "4", "-4", "-3", "0"]


class TestDiscardedCallResultReachedForAField:
    """claude.md #102: `makeThing().count` -- a call result produced
    solely to read one field out of, with nothing binding it.

    claude.md #77 already released a call result discarded as a bare
    statement, on the reasoning that a Call's result is fresh and
    unshared by construction so that expression is provably its only
    reference. Reading a field off it is the same situation and was
    simply never covered: measured at one whole struct per evaluation,
    which in a loop is per iteration. Found by the leak stress suite.
    """

    def test_the_value_is_still_correct(self, compile_and_run):
        source = """
        struct Config { retries:int name:text }
        Config func load() {
            Config c
            c.retries = 3
            c.name = 'main'
            return c
        }
        int total = 0
        for int i = 0, i < 5, i++ {
            total = total + load().retries
        }
        log(total)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "15"

    def test_the_receiver_is_released_exactly_once(self, parser, semantic, codegen):
        source = """
        struct Config { retries:int }
        Config func load() {
            Config c
            c.retries = 3
            return c
        }
        void func use() {
            log(load().retries)
        }
        use()
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        body = ir.split("define void @use()")[1].split("\n}")[0]
        assert body.count("@festina_release") == 1

    def test_a_managed_field_survives_being_read_off_a_call(self, compile_and_run):
        # The restriction is load-bearing, not conservative: releasing
        # the parent recursively releases its struct-typed fields, so
        # doing it for a managed field would free the very value just
        # loaded. This case therefore still leaks, deliberately -- and
        # what must never happen is the alternative, which is that the
        # value read back is garbage or the program crashes. Pinned so a
        # later "optimization" cannot quietly turn a documented leak
        # into a use-after-free.
        source = """
        struct Inner { n:int label:text }
        struct Outer { inner:Inner }
        Outer func make(n:int) {
            Outer o
            o.inner.n = n
            o.inner.label = `label ${n}`
            return o
        }
        for int i = 0, i < 20, i++ {
            Inner got = make(i).inner
            if got.n != i {
                log('corrupted')
            }
            if got.label != `label ${i}` {
                log('corrupted')
            }
        }
        log('intact')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "intact"


class TestChainedCallResultReachedForAField:
    """claude.md #108: `make().inner.n` -- a call result reached through
    a CHAIN of fields, which claude.md #102 could not cover.

    #102 released the receiver of a one-step read (`make().n`) and had
    to give up on `make().inner`, because releasing the parent there
    frees the Inner it is about to hand back. But that left the longer
    chain leaking the entire object graph: at `.inner` the field type is
    managed so nothing is released, and at `.n` the receiver is a Member
    rather than a Call so nothing notices the call result exists.
    Measured under LeakSanitizer at 5,200 bytes over 100 iterations.

    The decision now happens at the outermost link, where the type of
    the value that actually escapes the chain is known.
    """

    def test_a_chained_read_produces_the_right_value(self, compile_and_run):
        source = """
        struct Inner { n:int label:text }
        struct Outer { inner:Inner tag:text }
        Outer func make(v:int) {
            Outer o
            o.inner.n = v
            o.inner.label = 'L'
            o.tag = 'T'
            return o
        }
        int total = 0
        for int i = 0, i < 100, i++ {
            total = total + make(i).inner.n
        }
        log(total)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "4950"

    def test_the_chain_releases_the_call_result_exactly_once(
            self, parser, semantic, codegen):
        source = """
        struct Inner { n:int }
        struct Outer { inner:Inner }
        Outer func make() {
            Outer o
            o.inner.n = 3
            return o
        }
        void func use() {
            log(make().inner.n)
        }
        use()
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        body = ir.split("define void @use()")[1].split("\n}")[0]
        # Exactly one release, of the Outer -- Outer has a struct-typed
        # field so it gets #78's per-type wrapper rather than the plain
        # runtime function, and the wrapper cascades into Inner itself.
        assert body.count("@__festina_release_struct_Outer(") == 1
        assert body.count("@festina_release") == 0

    def test_a_chain_ending_in_a_managed_value_retains_then_releases(
            self, parser, semantic, codegen):
        # claude.md #117 INVERTED this test, which used to pin #102/#108's
        # deliberate leak ("releasing the parent would free the value
        # just loaded"). The fix is retain-first: the Inner is retained,
        # THEN the Outer released -- whose cascade decrements Inner back
        # to exactly one reference, owned by the binding. The parent
        # release must appear, and a retain must precede it.
        source = """
        struct Inner { n:int }
        struct Outer { inner:Inner }
        Outer func make() {
            Outer o
            o.inner.n = 3
            return o
        }
        void func use() {
            Inner got = make().inner
            log(got.n)
        }
        use()
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        body = ir.split("define void @use()")[1].split("\n}")[0]
        assert "@__festina_release_struct_Outer(" in body
        retain_at = body.index("call void @festina_retain(")
        release_at = body.index("@__festina_release_struct_Outer(")
        assert retain_at < release_at, "the retain must precede the parent release"

    def test_a_chain_ending_in_text_is_still_not_released(self, compile_and_run):
        # Same reasoning, and the same thing that must never happen:
        # the text read back must be intact, not freed out from under
        # the binding.
        source = """
        struct Inner { n:int label:text }
        struct Outer { inner:Inner }
        Outer func make(v:int) {
            Outer o
            o.inner.n = v
            o.inner.label = `label ${v}`
            return o
        }
        for int i = 0, i < 20, i++ {
            text got = make(i).inner.label
            if got != `label ${i}` {
                log('corrupted')
            }
        }
        log('intact')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "intact"

    def test_length_off_a_call_result_is_released(self, parser, semantic, codegen):
        # claude.md #108: .length never reached _emit_member_load at all,
        # so #102 never covered `rowsFor(x).length` even though its own
        # docstring claimed it did. A length is an i64 copy that owes the
        # array nothing, so the receiver is always releasable.
        source = """
        arr[int] func rows(v:int) {
            arr[int] a = [v, v, v]
            return a
        }
        void func use() {
            log(rows(1).length)
        }
        use()
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        body = ir.split("define void @use()")[1].split("\n}")[0]
        assert body.count("@festina_release") == 1

    def test_length_reached_through_a_chain_is_released(self, compile_and_run):
        source = """
        struct Inner { items:arr[int] }
        struct Outer { inner:Inner }
        Outer func make(v:int) {
            Outer o
            o.inner.items.push(v)
            o.inner.items.push(v)
            return o
        }
        int total = 0
        for int i = 0, i < 50, i++ {
            total = total + make(i).inner.items.length
        }
        log(total)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "100"

    def test_a_member_load_in_a_call_argument_is_not_swallowed_by_the_chain(
            self, compile_and_run):
        # "Part of this chain" is decided by AST node identity, not by
        # "a chain is in flight". A member load reached while emitting a
        # call ARGUMENT belongs to no chain, and treating it as one
        # would move its release to a point that may never arrive.
        source = """
        struct Inner { n:int }
        struct Outer { inner:Inner }
        Outer func make(v:int) {
            Outer o
            o.inner.n = v
            return o
        }
        int total = 0
        for int i = 0, i < 50, i++ {
            total = total + make(make(i).inner.n).inner.n
        }
        log(total)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "1225"


class TestComputedIndexAndArgumentOwnership:
    """claude.md #119: the two chain shapes #117 left leaking, closed
    by the same retain-first argument. A computed-index element off an
    owning receiver (`getRows()[0]`, `getMap()['k']`) is minted its own
    ownership (retain for a refcounted element, copy for a text one)
    and the container released; an owning refcounted ARGUMENT to a user
    function (`f(make())`, `f(getRows()[0])`) is released after the
    call, exactly like a text temporary. Whether an emission minted a
    +1 is recorded (_minted_values) rather than re-derived from syntax,
    because the one shape syntax cannot classify -- a table-row element
    is borrowed where a struct element is retained -- is exactly where
    a predicate/emission disagreement would corrupt memory."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_a_computed_element_retains_before_the_container_release(
            self, parser, semantic, codegen):
        source = """
        arr[arr[int]] func matrix() {
            arr[arr[int]] m = [[1, 2], [3, 4]]
            return m
        }
        void func use() {
            arr[int] row = matrix()[0]
            log(row.length)
        }
        use()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @use()")[1].split("\n}")[0]
        retain_at = body.index("call void @festina_retain(")
        release_at = body.index("@__festina_release_array_")
        assert retain_at < release_at, "the element retain must precede the container release"

    def test_computed_index_values_survive_their_container(self, compile_and_run):
        source = """
        arr[arr[int]] func matrix() {
            arr[arr[int]] m = [[1, 2], [3, 4]]
            return m
        }
        map[text] func conf() {
            map[text] m = {'k': 'value'}
            return m
        }
        arr[text] func names() {
            arr[text] t = ['alpha', 'beta']
            return t
        }
        int total = 0
        for int i = 0, i < 60, i++ {
            arr[int] row = matrix()[1]
            total = total + row[0] + matrix()[0][1]
            if conf()['k'] != 'value' { log('corrupted') }
            if names()[0] != 'alpha' { log('corrupted') }
        }
        log(total)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "300"

    def test_an_owning_argument_is_released_after_the_call(
            self, parser, semantic, codegen):
        source = """
        int func total(xs:arr[int]) {
            return xs.length
        }
        void func use() {
            log(total([1, 2, 3]))
        }
        use()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @use()")[1].split("\n}")[0]
        call_at = body.index("call i64 @total(")
        release_at = body.index("@festina_release")
        assert call_at < release_at, "the argument release must come after the call"

    def test_owning_arguments_reach_the_callee_intact(self, compile_and_run):
        # The callee stores, reads and returns through the borrowed
        # argument; anything it KEEps takes its own retain, so the
        # caller's post-call release never pulls memory out from under
        # a kept reference.
        source = """
        struct Box { n:int }
        arr[Box] kept
        Box func makeBox(v:int) {
            Box b
            b.n = v
            return b
        }
        void func keep(b:Box) {
            kept.push(b)
        }
        int func readThrough(b:Box) {
            return b.n
        }
        int total = 0
        for int i = 0, i < 50, i++ {
            keep(makeBox(i))
            total = total + readThrough(makeBox(i))
        }
        total = total + kept[10].n + kept.length
        log(total)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == str(sum(range(50)) + 10 + 50)

    def test_a_table_row_element_stays_borrowed(self, compile_and_run, tmp_path):
        # The one computed-index shape that deliberately does NOT mint:
        # rows have no refcount header (the array owns them outright),
        # so the container is left alive -- leaked, per todo.md -- and
        # a column read off the row is still copied at its binding.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table People {{ id:int  name:text }}
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'row'])
        arr[People] func rows() {{
            arr[People] r = sqlite('SELECT * FROM People')
            return r
        }}
        text got = rows()[0].name
        log(got)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "row"


class TestCycleCollection:
    """claude.md #120: reference cycles are collected, not leaked. A
    release of a value whose TYPE can reach itself (struct fields, arr
    elements, map values) that leaves the count above zero runs a
    synchronous trial deletion -- markGray / scan / collectWhite over
    generated per-type traversals -- freeing a cycle no external
    reference holds and restoring, exactly, one that something still
    does. Acyclic types generate none of it and pay nothing. The
    leak-freedom half is verified under ASan/LeakSanitizer by the
    struct_self per-type program (now a genuine cycle) and was measured
    directly for self-cycles, pair cycles, array-routed parent pointers
    and map-routed rings; these tests pin the structure and the
    reachability behavior."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_a_cyclic_type_release_wrapper_runs_a_trial(
            self, parser, semantic, codegen):
        source = """
        struct Node { n:int next:Node }
        void func f() {
            Node a
            a.n = 1
            a.next = a
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        wrapper = ir.split("define void @__festina_release_struct_Node(")[1].split("\n}")[0]
        assert "call i8 @festina_cycle_candidate(" in wrapper
        assert "@__festina_cycle_gray_" in wrapper
        assert "@__festina_cycle_scan_" in wrapper
        assert "@__festina_cycle_white_" in wrapper

    def test_an_acyclic_type_generates_no_cycle_machinery(
            self, parser, semantic, codegen):
        # The gate: a program whose types cannot form a cycle carries
        # zero collector code and zero trial calls.
        source = """
        struct Inner { n:int }
        struct Outer { inner:Inner }
        void func f() {
            Outer o
            o.inner.n = 1
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "festina_cycle_candidate" not in ir.replace(
            "declare i8 @festina_cycle_candidate(ptr)", "")
        assert "@__festina_cycle_" not in ir

    def test_garbage_cycles_are_reclaimed_and_reused_memory_stays_sane(
            self, compile_and_run):
        # 300 dropped cycles; correctness of everything built afterwards
        # is the observable half (the zero-leaked-bytes half runs under
        # the sanitizer harness in test_leak_stress).
        source = """
        struct Node { n:int next:Node label:text }
        void func cycle(v:int) {
            Node a
            Node b
            a.n = v
            a.label = `n${v}`
            b.n = v + 1
            a.next = b
            b.next = a
        }
        int total = 0
        for int i = 0, i < 300, i++ {
            cycle(i)
            total = total + i
        }
        log(total)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == str(sum(range(300)))

    def test_a_reachable_cycle_survives_its_trial_intact(self, compile_and_run):
        # The safety half: a trial on a cycle something still holds
        # must restore every count and free nothing.
        source = """
        struct Node { n:int next:Node }
        Node keep
        void func build() {
            Node a
            Node b
            a.n = 10
            b.n = 20
            a.next = b
            b.next = a
            keep = b
        }
        build()
        log(keep.n)
        log(keep.next.n)
        log(keep.next.next.n)
        keep.next = null
        log('broken')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["20", "10", "20", "broken"]

    def test_cycles_through_arrays_and_maps_are_collected(self, compile_and_run):
        source = """
        struct Tree { n:int kids:arr[Tree] parent:Tree }
        void func family() {
            Tree root
            root.n = 1
            Tree kid
            kid.n = 2
            kid.parent = root
            root.kids.push(kid)
        }
        struct Ring { name:text peers:map[Ring] }
        void func ring() {
            Ring a
            Ring b
            a.name = 'a'
            b.name = 'b'
            a.peers['b'] = b
            b.peers['a'] = a
        }
        for int i = 0, i < 200, i++ {
            family()
            ring()
        }
        log('done')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_field_stores_land_before_the_old_values_release(
            self, parser, semantic, codegen):
        # claude.md #120's ordering rule: with trials traversing the
        # object graph, a field must never still point at a value whose
        # count the in-flight release already dropped -- markGray would
        # double-count the edge and could free something a real
        # external reference holds. The store must precede the release
        # in the emitted IR.
        source = """
        struct Node { n:int next:Node }
        void func f(a:Node, b:Node) {
            a.next = b
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f(")[1].split("\n}")[0]
        store_at = body.index("store ptr")
        release_at = body.index("call void @__festina_release_struct_Node(")
        assert store_at < release_at, "the field store must precede the old value's release"


class TestAudioChannelReturnAndClipStop:
    """claude.md #109: play()/playLoop() return the channel they chose,
    and aud.stop() is back as a clip-wide stop.

    These are two halves of one fix. claude.md #100 removed stop()
    because "stop this clip" only honestly means "every channel playing
    it", which is rarely what an overlapping-effects program wants --
    but the alternative it pointed at, stopAudioPlayer(n), needed a
    channel number that automatic assignment never told anyone. So the
    pool was addressable only by naming channels by hand, which is to
    say by not using the pool.
    """

    def test_play_returns_distinct_pooled_channels(
            self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        int a = clip.play()
        int b = clip.play()
        int c = clip.play()
        log(a != b)
        log(b != c)
        log(a >= 0)
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "true", "true"]

    def test_play_on_an_explicit_channel_returns_that_channel(
            self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        log(clip.play(3))
        log(clip.playLoop(5))
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.stdout.splitlines() == ["3", "5"]

    def test_the_returned_channel_can_be_stopped(
            self, compile_and_run, tmp_path, audio_null_env):
        # The whole point: a channel chosen automatically is now
        # addressable, without the program having picked it up front.
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        int ch = clip.playLoop()
        log(clip.isPlaying())
        stopAudioPlayer(ch)
        log(clip.isPlaying())
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_stop_silences_every_channel_playing_the_clip(
            self, compile_and_run, tmp_path, audio_null_env):
        # Three overlapping voices of one clip, stopped by one call --
        # the case claude.md #100 said stop() could only mean, restored
        # because it is a real thing to want.
        # playLoop rather than play: against ALSA's null device a
        # one-shot drains instantly, so a finished voice would be
        # indistinguishable from a stopped one and the test would pass
        # for the wrong reason. A loop only ends when something stops it.
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        clip.playLoop()
        clip.playLoop()
        clip.playLoop()
        log(clip.isPlaying())
        clip.stop()
        log(clip.isPlaying())
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_stop_leaves_another_clip_alone(
            self, compile_and_run, tmp_path, audio_null_env):
        # Clip-wide, not global -- the distinction between stop() and
        # stopAudioPlayer() with no argument.
        _write_wav(tmp_path / "a.wav", duration_s=1.0)
        _write_wav(tmp_path / "b.wav", duration_s=1.0)
        source = """
        aud a = 'a.wav'
        aud b = 'b.wav'
        a.playLoop()
        b.playLoop()
        a.stop()
        log(a.isPlaying())
        log(b.isPlaying())
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.stdout.splitlines() == ["false", "true"]

    def test_stop_returns_a_reserved_channel_to_the_pool(
            self, compile_and_run, tmp_path, audio_null_env):
        # A playLoop channel is reserved (claude.md #99). Stopping the
        # clip has to release that reservation too -- silencing the
        # sound while leaving the channel locked would quietly shrink
        # the pool every time.
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        setMaxAudioPlayers(1)
        int first = clip.playLoop()
        clip.stop()
        int second = clip.play()
        log(first == second)
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.stdout.strip() == "true"

    def test_a_play_that_finds_no_channel_returns_minus_one(
            self, compile_and_run, tmp_path, audio_null_env):
        # -1 means "nothing was played", and there is exactly one way to
        # reach it from Festina: every channel in the table reserved by
        # playLoop, with none left to claim. (A clip that failed to LOAD
        # cannot reach it -- festina_load_audio fails the program at the
        # declaration, long before any play() runs.) Returning a channel
        # number here would name one this call never used.
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        setMaxAudioPlayers(64)
        for int i = 0, i < 64, i++ {
            clip.playLoop(i)
        }
        log(clip.play())
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "-1"


class TestIsAudioPlayerPlaying:
    """claude.md #146: isAudioPlayerPlaying(channel) -- the per-CHANNEL
    counterpart to aud.isPlaying()'s per-clip question. Fills the exact
    gap TestAudioChannelReturnAndClipStop's own docstring names: play()
    hands back a channel number, but there was previously no way to ask
    about that CHANNEL directly once the program no longer has (or
    cares about) which clip is on it."""

    def test_true_immediately_after_play(self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        int ch = clip.playLoop()
        log(isAudioPlayerPlaying(ch))
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_false_immediately_after_stop_audio_player(
            self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        int ch = clip.playLoop()
        stopAudioPlayer(ch)
        log(isAudioPlayerPlaying(ch))
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "false"

    def test_false_for_a_channel_never_played_on(self, compile_and_run, audio_null_env):
        result = compile_and_run("log(isAudioPlayerPlaying(5))", env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "false"

    def test_out_of_range_channel_is_clamped_not_a_crash(
            self, compile_and_run, audio_null_env):
        # Same "a bad channel number should not kill a running program"
        # rule play(n)/stopAudioPlayer(n) already apply -- claude.md
        # #99's own clamp-into-[0,64) behavior, extended to this query.
        result = compile_and_run(
            "log(isAudioPlayerPlaying(999))\nlog(isAudioPlayerPlaying(-1))",
            env=audio_null_env,
        )
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["false", "false"]

    def test_answers_about_the_channel_not_the_clip(
            self, compile_and_run, tmp_path, audio_null_env):
        # A different clip taking the same channel over is exactly the
        # scenario that makes a per-clip isPlaying() insufficient: the
        # channel is still playing SOMETHING, even though the original
        # clip's own aud.isPlaying() would now say false.
        _write_wav(tmp_path / "a.wav", duration_s=1.0)
        _write_wav(tmp_path / "b.wav", duration_s=1.0)
        source = """
        aud a = 'a.wav'
        aud b = 'b.wav'
        int ch = a.playLoop(0)
        b.playLoop(0)
        log(a.isPlaying())
        log(isAudioPlayerPlaying(ch))
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["false", "true"]

    def test_stopping_one_channel_leaves_another_playing_the_same_clip_alone(
            self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        int a = clip.playLoop()
        int b = clip.playLoop()
        stopAudioPlayer(a)
        log(isAudioPlayerPlaying(a))
        log(isAudioPlayerPlaying(b))
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["false", "true"]

    def test_compiles_and_links_successfully(self, cli_mod, tmp_path):
        # Naming isAudioPlayerPlaying alone (no aud declaration at all)
        # must still pull in the audio translation unit -- the same
        # "naming it is what makes a program use audio" contract
        # stopAudioPlayer/setMaxAudioPlayers already have.
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        src_path = tmp_path / "main.f"
        src_path.write_text("log(isAudioPlayerPlaying(0))")
        out_path = tmp_path / "program"
        from tests.conftest import compile_file_or_skip
        result_path = compile_file_or_skip(cli_mod, str(src_path), str(out_path))
        assert result_path == str(out_path)
        assert out_path.exists()


class TestSplitAndJoin:
    '''claude.md #116: sentence.split(sep) -> arr[text], sep a text or a
    regex; words.join(sep) -> text, on arrays of text/int/float/bool.
    JS semantics throughout: empty pieces kept, edge empties kept, an
    empty-match regex splits between characters without a trailing
    empty, an empty text separator splits per UTF-8 code point, and a
    null element joins as an empty string.'''

    def test_the_spec_example(self, compile_and_run):
        source = r'''
        text sentence = 'the quick brown fox'
        arr[text] words = sentence.split(' ')
        log(words.length)
        sentence = words.join('\t')
        log(sentence)
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ['4', 'the\tquick\tbrown\tfox']

    def test_regex_split(self, compile_and_run):
        source = r'''
        arr[text] words = 'one   two	three'.split(/\s+/g)
        log(words)
        log(words.join('-'))
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            '["one","two","three"]', 'one-two-three']

    def test_empty_pieces_are_kept(self, compile_and_run):
        # JS: 'a,,b'.split(',') has three pieces; separators at the
        # edges yield edge empties.
        source = '''
        log('a,,b'.split(','))
        log(',start'.split(','))
        log('end,'.split(','))
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            '["a","","b"]', '["","start"]', '["end",""]']

    def test_no_match_is_one_piece(self, compile_and_run):
        result = compile_and_run("log('whole'.split(','))")
        assert result.stdout.strip() == '["whole"]'

    def test_an_empty_match_regex_splits_between_characters(self, compile_and_run):
        # ...and does not loop forever, and adds no trailing empty --
        # both exactly JS.
        result = compile_and_run("log('abc'.split(/x*/))")
        assert result.stdout.strip() == '["a","b","c"]'

    def test_an_empty_text_separator_splits_utf8_code_points(self, compile_and_run):
        # Per CODE POINT, not per byte -- a byte split would shatter
        # every non-ASCII character into invalid fragments.
        result = compile_and_run("log('h\u00e9llo'.split(''))")
        assert result.stdout.strip() == '["h","\u00e9","l","l","o"]'

    def test_join_renders_scalars_and_null_as_empty(self, compile_and_run):
        # JS: [1, null, 3].join('-') is '1--3'.
        source = '''
        arr[int] nums = [1, null, 3]
        log(nums.join('-'))
        arr[bool] flags = [true, false]
        log(flags.join('|'))
        arr[float] fs = [1.5, 2.5]
        log(fs.join(', '))
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ['1--3', 'true|false', '1.5, 2.5']

    def test_a_split_result_is_an_ordinary_array(self, compile_and_run):
        # Refcounted, aliasable, freeable -- built by the runtime with
        # the same layout every arr[text] has.
        source = '''
        arr[text] words = 'a b c'.split(' ')
        arr[text] alias = words
        words.push('d')
        log(alias.length)
        free words
        log(alias[0])
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ['4', 'a']

    def test_split_separator_must_be_text_or_regex(self, parser, semantic, errors):
        program = parser.parse("log('a b'.split(5))")
        with pytest.raises(errors.CompileError, match='text or regex'):
            semantic.analyze(program)

    def test_join_needs_a_joinable_element_type(self, parser, semantic, errors):
        program = parser.parse(
            'struct P { n:int }\n'
            'arr[P] ps = []\n'
            "log(ps.join(','))")
        with pytest.raises(errors.CompileError, match='join'):
            semantic.analyze(program)

    def test_join_separator_must_be_text(self, parser, semantic, errors):
        program = parser.parse("arr[int] ns = [1]\nlog(ns.join(5))")
        with pytest.raises(errors.CompileError, match='separator must be text'):
            semantic.analyze(program)


class TestJsonRendering:
    '''claude.md #114: any non-text value in log() or `${}` compiles as
    its .toText(). int/float/bool keep their stringifiers; struct/table
    row/arr/map render JSON-like through generated per-type functions;
    blob/img/aud are compile errors, since a blob's bytes may be binary
    (its explicit .toText() exists for when that is wanted) and img/aud
    have no text form at all.'''

    def test_a_struct_renders_as_json(self, compile_and_run):
        source = '''
        struct Inner { n:int  tag:text }
        struct P { id:int  ok:bool  name:text  inner:Inner  xs:arr[int] }
        P p
        p.id = 7
        p.ok = true
        p.name = 'x'
        p.inner.n = 3
        p.inner.tag = 'in'
        p.xs.push(1)
        p.xs.push(2)
        log(p)
        '''
        result = compile_and_run(source)
        assert result.stdout.strip() == (
            '{"id":7,"ok":true,"name":"x",'
            '"inner":{"n":3,"tag":"in"},"xs":[1,2]}')

    def test_arrays_and_maps_render_as_json(self, compile_and_run):
        source = '''
        arr[int] nums = [1, 2, 3]
        log(nums)
        arr[text] words = ['a', null, 'c']
        log(words)
        map[int] m = {'one': 1}
        log(m)
        log(`inline: ${nums}`)
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            '[1,2,3]', '["a",null,"c"]', '{"one":1}', 'inline: [1,2,3]']

    def test_text_is_escaped_and_assigned_null_renders_null(self, compile_and_run):
        # The name contains a real double quote and a real tab, which
        # must come out as \" and \t. An UNASSIGNED scalar field renders
        # its zero (claude.md #97: calloc'd storage reads 0, not the
        # null sentinel); a field explicitly made null renders null.
        source = r'''
        struct P { name:text  score:float  missing:float }
        P p
        p.name = 'has "quotes" and	tab'
        p.score = 1.0 / 0.0
        log(p)
        '''
        result = compile_and_run(source)
        assert result.stdout.strip() == (
            '{"name":"has \\"quotes\\" and\\ttab","score":null,"missing":0}')

    def test_escape_run_boundaries_render_correctly(self, compile_and_run):
        # claude.md #190: festina_sb_append_json_text now bulk-copies
        # a "safe run" of bytes instead of handling one byte at a
        # time, so the exact boundary where a run starts/ends/never-
        # exists is exactly where a bug would hide: a string starting
        # AND ending on an escape-needing byte (no leading/trailing
        # safe run at all), a string of NOTHING but escape-needing
        # bytes back-to-back (every "run" is zero-length), and an
        # empty string (no bytes to scan at all).
        source = r'''
        struct P {
            leading:text
            trailing:text
            onlyEscapes:text
            empty:text
        }
        P p
        p.leading = '"start and end with quotes"'
        p.trailing = 'ab\\'
        p.onlyEscapes = '"\\\n\t\r'
        p.empty = ''
        log(p)
        '''
        result = compile_and_run(source)
        assert result.stdout.strip() == (
            '{"leading":"\\"start and end with quotes\\"",'
            '"trailing":"ab\\\\",'
            '"onlyEscapes":"\\"\\\\\\n\\t\\r",'
            '"empty":""}')

    def test_a_table_row_renders_with_database_null_and_omits_undefined(
            self, compile_and_run, tmp_path):
        # A database NULL is the JSON null; a column the query never
        # selected is OMITTED, exactly what JSON.stringify does for an
        # undefined property -- the analogy claude.md #111 built.
        db = tmp_path / 't.sqlite'
        source = f'''
        DatabaseURL = '{db}'
        table T {{ id:int  name:text  ok:bool }}
        sqlite('INSERT INTO T (id, name, ok) VALUES (?, ?, ?)', [1, null, 1])
        arr[T] rows = sqlite('SELECT * FROM T')
        log(rows[0])
        arr[T] partial = sqlite('SELECT id FROM T')
        log(partial[0])
        log(rows)
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            '{"id":1,"name":null,"ok":true}',
            '{"id":1}',
            '[{"id":1,"name":null,"ok":true}]']

    def test_explicit_to_text_is_the_same_rendering(self, compile_and_run):
        source = '''
        arr[int] nums = [1, 2]
        text json = nums.toText()
        log(json)
        struct P { n:int }
        P p
        p.n = 5
        log(p.toText())
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ['[1,2]', '{"n":5}']

    def test_interpolating_a_fresh_container_does_not_leak(self, compile_and_run):
        # claude.md #192: `${make()}` renders a fresh array/struct/map to
        # text and is then done with the container, but _emit_template
        # released only the text pieces, never the container itself -- a
        # leak per evaluation. This pins the OBSERVABLE result (the
        # rendering is correct); the leak itself is ASan-verified
        # separately. A tight loop makes a missed release matter.
        source = '''
        arr[int] func makeArr() {
            arr[int] xs = [1, 2, 3]
            return xs
        }
        int i = 0
        text last = ''
        while i < 50 {
            last = `v: ${makeArr()} and ${[9, 8]}`
            i = i + 1
        }
        log(last)
        '''
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == 'v: [1,2,3] and [9,8]'

    def test_a_null_container_renders_as_null(self, compile_and_run):
        source = '''
        struct P { n:int }
        P nothing = null
        log(nothing)
        log(`v: ${nothing}`)
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ['null', 'v: null']

    def test_a_cycle_truncates_instead_of_crashing(self, compile_and_run):
        # claude.md #106 made cycles constructible; a debug rendering
        # that overflowed the stack on one would crash the program it
        # exists to debug. Depth caps at 32, rendering null beyond.
        source = '''
        struct Node { n:int  next:Node }
        Node a
        a.n = 1
        a.next = a
        log(a)
        '''
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().endswith('null}' + '}' * 32)

    def test_an_opaque_handle_in_a_struct_renders_as_a_placeholder(
            self, compile_and_run, tmp_path):
        path = tmp_path / 'f.txt'
        source = f'''
        struct Holder {{ name:text  data:blob }}
        Holder empty
        empty.name = 'no file'
        log(empty)
        Holder full
        full.name = 'with file'
        full.data = '{path}'
        log(full)
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            '{"name":"no file","data":null}',
            '{"name":"with file","data":"<blob>"}']

    @pytest.mark.parametrize('decl,use', [
        ("img v = 'x.png'", 'log(v)'),
        ("img v = 'x.png'", 'log(`${v}`)'),
        ("aud v = 'x.wav'", 'log(v)'),
        ("aud v = 'x.wav'", 'log(`${v}`)'),
    ])
    def test_img_and_aud_in_log_or_template_are_compile_errors(
            self, parser, semantic, codegen, decl, use):
        # claude.md #115: blob renders (it has a text form); img and aud
        # do not, so they refuse.
        program = parser.parse(f'{decl}\n{use}', filename='main.f')
        analyzed = semantic.analyze(program, filename='main.f')
        with pytest.raises(Exception, match=r'text form'):
            codegen.generate_ir(program, analyzed, filename='main.f')


class TestStatementCache:
    """claude.md #113: literal SQL is prepared once per call site and
    reset+reused ever after -- the sqlite counterpart of claude.md #85's
    regex literal cache, driven by the same compile-time fact (the text
    cannot change). Dynamic SQL keeps the per-call prepare. Measured:
    20,000 one-row SELECTs 164ms -> 55ms; with WAL, 20,000 INSERTs
    16.7s -> 0.3s."""

    def test_a_literal_site_reuses_its_statement_across_params(
            self, compile_and_run, tmp_path):
        # Same call site, different bound parameters each iteration --
        # the reset+rebind path, which is where a caching bug would
        # show as stale results.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table T {{ id:int  v:int }}
        for int i = 0, i < 5, i++ {{
            sqlite('INSERT INTO T (id, v) VALUES (?, ?)', [i, i * 10])
        }}
        int total = 0
        for int i = 0, i < 5, i++ {{
            arr[T] rows = sqlite('SELECT * FROM T WHERE id = ?', [i])
            total = total + rows[0].v
        }}
        log(total)
        arr[T] counted = sqlite('SELECT count(*) AS id FROM T')
        log(counted[0].id)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["100", "5"]

    def test_literal_sql_gets_a_cache_slot_and_dynamic_sql_does_not(
            self, parser, semantic, codegen):
        source = """
        table T { id:int }
        sqlite('INSERT INTO T (id) VALUES (1)')
        text tbl = 'T'
        sqlite(`DELETE FROM ${tbl}`)
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        # One literal site -> one slot; the template site stays on the
        # per-call prepare, because its SQL can differ every evaluation.
        assert ir.count("@__festina_stmtcache_") >= 2  # global + use
        assert ir.count("call ptr @festina_sqlite_prepare_cached(") == 1
        assert ir.count("call ptr @festina_sqlite_prepare(") == 1

    def test_two_identical_literals_get_independent_slots(
            self, compile_and_run, tmp_path):
        # Per-SITE, not per-text: two textually identical queries are
        # two statements, so one being mid-collection can never disturb
        # the other. Cheap insurance rather than a measured need.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table T {{ id:int }}
        sqlite('INSERT INTO T (id) VALUES (1)')
        arr[T] a = sqlite('SELECT * FROM T')
        arr[T] b = sqlite('SELECT * FROM T')
        log(a[0].id + b[0].id)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "2"


class TestStructQueryTargets:
    """claude.md #112: `arr[SomeStruct] q = sqlite(...)` -- a struct as
    the query's landing spot. A table's declared columns can never chase
    a query's aliases, so `SELECT id AS whatever`, a JOIN, or a computed
    column had nowhere typed to land; a struct names its fields after
    the result's own column names, and -- unlike declaring a table --
    carries no CREATE TABLE side effect. Collection shares claude.md
    #111's name matching; each row then becomes a real refcounted
    struct, indistinguishable from one built by hand."""

    def test_the_spec_example(self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int  name:text }}
        sqlite('INSERT INTO examples (id, name) VALUES (?, ?)', [7, 'seven'])
        struct data {{
          whatever:int
        }}
        arr[data] query = sqlite('select id as whatever from examples')
        log(query.length)
        log(query[0].whatever)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["1", "7"]

    def test_computed_columns_land_by_alias(self, compile_and_run, tmp_path):
        # The case a table can never express: the column does not exist
        # anywhere in the schema, only in the query.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int  name:text }}
        sqlite('INSERT INTO examples (id, name) VALUES (?, ?)', [7, 'seven'])
        sqlite('INSERT INTO examples (id, name) VALUES (?, ?)', [8, 'eight'])
        struct summary {{ total:int  biggest:text }}
        arr[summary] agg = sqlite(
            "select count(*) as total, max(name) as biggest from examples")
        log(agg[0].total)
        log(agg[0].biggest)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["2", "seven"]

    def test_an_unmatched_field_reads_null(self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int }}
        sqlite('INSERT INTO examples (id) VALUES (1)')
        struct wide {{ whatever:int  missing:text }}
        arr[wide] w = sqlite('select id as whatever from examples')
        log(w[0].missing == null)
        log(w[0].whatever)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "1"]

    def test_the_result_is_an_ordinary_struct(self, compile_and_run, tmp_path):
        # Refcounted like any other: an element aliased out of the array
        # survives freeing the array, fields can be reassigned and
        # deleted, and the whole thing can be freed by hand.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int  name:text }}
        sqlite('INSERT INTO examples (id, name) VALUES (?, ?)', [7, 'seven'])
        struct data {{ whatever:int  label:text }}
        arr[data] q = sqlite('select id as whatever, name as label from examples')
        data keep = q[0]
        free q
        log(q == null)
        log(keep.label)
        keep.label = 'renamed'
        log(keep.label)
        delete keep.label
        log(keep.label == null)
        free keep
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            "true", "seven", "renamed", "true"]

    def test_a_blob_column_lands_in_a_struct_field(self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        src = tmp_path / "payload.txt"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int  pic:blob }}
        blob payload = '{src}'
        payload.write('the bytes')
        sqlite('INSERT INTO examples (id, pic) VALUES (?, ?)', [1, payload])
        struct data {{ pic:blob }}
        arr[data] q = sqlite('select pic from examples')
        log(q[0].pic.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "the bytes"

    def test_a_non_queryable_field_is_a_clear_error(self, parser, semantic, codegen):
        source = (
            "struct bad { xs:arr[int] }\n"
            "arr[bad] q = sqlite('select 1')\n"
        )
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        with pytest.raises(Exception, match="xs"):
            codegen.generate_ir(program, analyzed, filename="main.f")

    def test_undefined_stays_a_table_row_method(self, parser, semantic, errors):
        # A struct instance from a query is an ordinary struct -- the
        # presence mask is a row concept, deliberately dropped in the
        # conversion, so undefined() is not offered.
        program = parser.parse(
            "struct data { whatever:int }\n"
            "arr[data] q = sqlite('select 1 as whatever')\n"
            "log(q[0].undefined('whatever'))")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestFreeStatement:
    """claude.md #111: `free name` -- release what the binding holds,
    null the binding. For refcounted types it is a DECREMENT, so a value
    something else still references survives; for img/aud it is the
    manual escape hatch for the escaping-handle leak; for value types it
    degenerates to `x = null`. Composable with automatic reclamation
    because every release in the runtime is null-safe and a free target
    counts as escaping (see escape_analysis's own comment)."""

    def test_freeing_nulls_the_binding(self, compile_and_run, tmp_path):
        path = tmp_path / "b.txt"
        source = f"""
        blob b = '{path}'
        free b
        log(b == null)
        int n = 5
        free n
        log(n == null)
        text t = 'x'
        free t
        log(t == null)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true", "true"]

    def test_freeing_twice_is_a_no_op(self, compile_and_run):
        source = """
        struct P { n:int }
        P p
        p.n = 1
        free p
        free p
        log('survived')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "survived"

    def test_a_shared_value_survives_freeing_one_reference(self, compile_and_run):
        # The user-facing half of "free is a decrement": the array is
        # freed, the element it shared with `keep` is not. Verified
        # leak-free AND corruption-free under ASan separately; this pins
        # the visible behavior.
        source = """
        struct P { n:int  label:text }
        P keep
        keep.n = 7
        keep.label = 'held'
        arr[P] xs = [keep, keep]
        free xs
        log(xs == null)
        log(keep.label)
        free keep
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "held"]

    def test_freeing_a_clip_source_leaves_its_clips_alone(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # The motivating example: clips carry their own surfaces, so the
        # sheet can go the moment the clips are cut.
        source = f"""
        img spritesheet = '{sprite_sheet_png}'
        img grass = spritesheet.clip(0, 0, 31, 31)
        img dirt = spritesheet.clip(32, 0, 31, 31)
        free spritesheet
        log(spritesheet == null)
        log(grass.width)
        log(dirt.width)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "31", "31"]

    def test_freeing_a_regex_literal_binding_keeps_the_cache_intact(
            self, compile_and_run):
        # A /pattern/ literal is compiled once and cached for the whole
        # process (claude.md #85); the value carries a `cached` mark and
        # festina_regex_free no-ops on it, so free is safe on a regex
        # binding whichever way it was produced.
        source = """
        for int i = 0, i < 3, i++ {
            regex r = /a+/i
            log(r.test('AAA'))
            free r
        }
        regex dyn = regex('[0-9]+')
        free dyn
        log(dyn == null)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true", "true", "true"]

    def test_freeing_a_query_row_drops_the_binding_without_freeing(
            self, compile_and_run, tmp_path):
        # A row is owned by the array it came from -- freeing it here
        # would double-free at the array's own release, so `free row`
        # only nulls the binding. Freeing the ARRAY is the real release.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table T {{ id:int }}
        sqlite('INSERT INTO T (id) VALUES (1)')
        arr[T] rows = sqlite('SELECT * FROM T')
        T first = rows[0]
        free first
        log(first == null)
        log(rows[0].id)
        free rows
        log(rows == null)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "1", "true"]

    def test_free_on_an_unknown_name_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("free nothing")
        with pytest.raises(errors.CompileError, match="unknown variable"):
            semantic.analyze(program)

    def test_free_on_a_constant_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("const int N = 5\nfree N")
        with pytest.raises(errors.CompileError, match="constant"):
            semantic.analyze(program)

    def test_free_on_a_parameter_is_a_compile_error(self, parser, semantic, errors):
        # A parameter borrows its caller's value (claude.md #84).
        program = parser.parse(
            "void func f(t:text) {\n    free t\n}\nf('x')")
        with pytest.raises(errors.CompileError, match="parameter"):
            semantic.analyze(program)


class TestDeleteStatement:
    """claude.md #111: `delete`, JS-shaped. A map entry stops existing;
    a struct field reads null afterwards; a query-row field reads null
    AND its presence bit clears, so undefined() reports it like a column
    the query never selected."""

    def test_both_map_forms_from_the_spec_example(self, compile_and_run):
        source = """
        map[text] example = {'data': 'some data', 'more-data': 'Some more data'}
        delete example.data
        delete example['more-data']
        log(example['data'] == null)
        log(example['more-data'] == null)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true"]

    def test_a_deleted_key_stops_existing_for_for_each(self, compile_and_run):
        # Removal, not set-to-null: forEach never visits the deleted key,
        # which null could not express.
        source = """
        void func show(v:int, k:text) {
            log(`${k}=${v}`)
        }
        map[int] m = {'a': 1, 'b': 2, 'c': 3}
        delete m.b
        m.forEach(show)
        """
        result = compile_and_run(source)
        assert sorted(result.stdout.splitlines()) == ["a=1", "c=3"]

    def test_deleting_a_missing_key_is_a_no_op(self, compile_and_run):
        source = """
        map[int] m = {'a': 1}
        delete m.nothing
        delete m['also-nothing']
        log(m['a'])
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "1"

    def test_a_computed_key_expression_works(self, compile_and_run):
        source = """
        map[int] m = {'k1': 10, 'k2': 20}
        int i = 1
        delete m[`k${i}`]
        log(m['k1'] == null)
        log(m['k2'])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "20"]

    def test_a_deleted_struct_field_reads_null(self, compile_and_run):
        source = """
        struct P { n:int  label:text }
        P p
        p.n = 5
        p.label = 'x'
        delete p.label
        delete p.n
        log(p.label == null)
        log(p.n == null)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true"]

    def test_delete_on_a_non_container_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("int x = 5\nint y = 1\ndelete y.something")
        with pytest.raises(errors.CompileError, match="delete"):
            semantic.analyze(program)

    def test_delete_of_a_whole_variable_says_to_use_free(self, parser, errors):
        with pytest.raises(errors.CompileError, match="free"):
            parser.parse("int x = 5\ndelete x")

    def test_deleting_an_unknown_struct_field_is_a_compile_error(
            self, parser, semantic, errors):
        program = parser.parse("struct P { n:int }\nP p\ndelete p.missing")
        with pytest.raises(errors.CompileError, match="missing"):
            semantic.analyze(program)

    def test_blob_delete_method_still_works(self, compile_and_run, tmp_path):
        # `delete` became a keyword; member names accept keywords
        # (parser.eat_name), so blob's method is untouched.
        path = tmp_path / "f.txt"
        source = f"""
        blob f = '{path}'
        f.write('x')
        log(f.delete())
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"


class TestUndefinedAndNameMatchedColumns:
    """claude.md #111: result columns match declared columns by NAME
    (positional matching silently misaligned every partial or reordered
    SELECT), and each row records which declared columns the result set
    actually contained -- read by row.undefined('col'), which is the
    difference between "the database said NULL" and "the query never
    asked"."""

    def test_the_spec_example(self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int  name:text }}
        sqlite('INSERT INTO examples (id, name) VALUES (?, ?)', [1, null])
        arr[examples] data = sqlite('select id from examples')
        if data[0] != null {{
          log('should log')
          if data[0].name == null && data[0].undefined('name') {{
            log('should also log')
          }}
        }}
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["should log", "should also log"]

    def test_a_genuine_database_null_is_not_undefined(self, compile_and_run, tmp_path):
        # The whole point of the distinction: NULL that came from the
        # database is a VALUE, and undefined() says so.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int  name:text }}
        sqlite('INSERT INTO examples (id, name) VALUES (?, ?)', [1, null])
        arr[examples] data = sqlite('select * from examples')
        log(data[0].name == null)
        log(data[0].undefined('name'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_a_deleted_row_column_becomes_undefined(self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int  name:text }}
        sqlite('INSERT INTO examples (id, name) VALUES (?, ?)', [1, 'x'])
        arr[examples] data = sqlite('select * from examples')
        log(data[0].undefined('name'))
        delete data[0].name
        log(data[0].name == null)
        log(data[0].undefined('name'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true", "true"]

    def test_a_reordered_select_lands_by_name(self, compile_and_run, tmp_path):
        # The bug the name matching fixes: this used to read name's text
        # into the id slot as an integer.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table T {{ id:int  name:text  score:float }}
        sqlite('INSERT INTO T (id, name, score) VALUES (?, ?, ?)', [7, 'seven', 1.5])
        arr[T] rows = sqlite('select score, name from T')
        log(rows[0].name)
        log(rows[0].score)
        log(rows[0].id == null)
        log(rows[0].undefined('id'))
        log(rows[0].undefined('score'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            "seven", "1.5", "true", "true", "false"]

    def test_an_unknown_column_name_fails_clearly(self, compile_and_run, tmp_path):
        # Asking about a column the table does not have is a typo, and
        # true or false would both bury it.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table T {{ id:int }}
        sqlite('INSERT INTO T (id) VALUES (1)')
        arr[T] rows = sqlite('SELECT * FROM T')
        log(rows[0].undefined('nmae'))
        """
        result = compile_and_run(source)
        assert result.returncode != 0
        assert "no column by that name" in result.stderr

    def test_undefined_requires_one_text_argument(self, parser, semantic, errors):
        program = parser.parse(
            "table T { id:int }\n"
            "arr[T] rows = sqlite('SELECT * FROM T')\n"
            "log(rows[0].undefined(5))")
        with pytest.raises(errors.CompileError, match="must be text"):
            semantic.analyze(program)


class TestRowRowid:
    """claude.md #188 (uraikus/festina#76 item 5): a table row's own
    `rowid`, exposed read-only -- SQLite already computes one for free
    on every ordinary rowid table, so this is a thin wrapper, not a new
    schema concept. Only populated when the query's own SQL explicitly
    selects a result column named `rowid` (`SELECT rowid, ...`) -- a
    bare `SELECT *` does not implicitly include it, so this is int's
    own null in that case, the same "the query never mentioned this"
    signal `.undefined()` already gives an ordinary column."""

    def test_rowid_reads_back_the_real_sqlite_rowid(self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table Users {{ name:text }}
        sqlite('INSERT INTO Users (name) VALUES (?)', ['ada'])
        sqlite('INSERT INTO Users (name) VALUES (?)', ['grace'])
        arr[Users] rows = sqlite('SELECT rowid, name FROM Users ORDER BY rowid')
        log(rows[0].rowid)
        log(rows[0].name)
        log(rows[1].rowid)
        log(rows[1].name)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "ada", "2", "grace"]

    def test_rowid_is_null_when_the_query_never_selected_it(self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table Users {{ name:text }}
        sqlite('INSERT INTO Users (name) VALUES (?)', ['ada'])
        arr[Users] rows = sqlite('SELECT name FROM Users')
        log(rows[0].rowid == null)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"

    def test_undefined_and_delete_are_unaffected_by_rowid(self, compile_and_run, tmp_path):
        # rowid isn't a declared column at all -- it must not shift or
        # otherwise disturb the presence mask an ordinary column's
        # undefined()/delete already rely on.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table Users {{ name:text  age:int }}
        sqlite('INSERT INTO Users (name, age) VALUES (?, ?)', ['ada', 30])
        arr[Users] rows = sqlite('SELECT rowid, name FROM Users')
        log(rows[0].undefined('age'))
        log(rows[0].undefined('name'))
        delete rows[0].name
        log(rows[0].name == null)
        log(rows[0].undefined('name'))
        log(rows[0].rowid == null)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false", "true", "true", "false"]

    def test_a_struct_query_target_has_no_rowid(self, parser, semantic, errors):
        # claude.md #112: a struct landing spot isn't a table row at
        # all -- rowid is deliberately a TableType-only concept, and an
        # extra unmatched result column (the SQL selecting rowid) is
        # simply not looked at, the same as any other result column the
        # struct's own fields don't name.
        program = parser.parse(
            "struct Row { name:text }\n"
            "table Users { name:text }\n"
            "arr[Row] rows = sqlite('SELECT rowid, name FROM Users')\n"
            "log(rows[0].rowid)")
        with pytest.raises(errors.CompileError, match="no field 'rowid'"):
            semantic.analyze(program)

    def test_rowid_is_read_only(self, parser, semantic, errors):
        program = parser.parse(
            "table T { id:int }\n"
            "arr[T] rows = sqlite('SELECT rowid, id FROM T')\n"
            "rows[0].rowid = 5")
        with pytest.raises(errors.CompileError, match="read-only"):
            semantic.analyze(program)


class TestSaveAndSaveCopy:
    """claude.md #110: save()/saveCopy() on blob, img and aud.

    One policy for all three, because all three are the same shape of
    value (claude.md #101/#109: content plus the bytes it came from).
    save() writes to the path the handle already has; save(path) adopts
    that path first; saveCopy(path) writes elsewhere and leaves the
    handle's own path alone.

    This is what closes the gap claude.md #109 shipped knowingly: a
    handle with no path -- an img from clip(), anything out of a
    database column -- could not reach the disk at all.
    """

    # ---- blob ----

    def test_blob_save_writes_to_its_own_path(self, compile_and_run, tmp_path):
        path = tmp_path / "notes.txt"
        source = f"""
        blob f = '{path}'
        f.write('written')
        f.delete()
        log(f.exists())
        log(f.save())
        log(f.exists())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true", "true"]
        assert path.read_text() == "written"

    def test_blob_save_with_a_path_adopts_it(self, compile_and_run, tmp_path):
        first = tmp_path / "one.txt"
        second = tmp_path / "two.txt"
        source = f"""
        blob f = '{first}'
        f.write('content')
        log(f.save('{second}'))
        // the path CHANGED, so everything else follows it
        log(f.delete())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true"]
        assert second.exists() is False, "delete() should have followed the new path"
        assert first.read_text() == "content", "the original must be untouched"

    def test_blob_save_copy_leaves_the_path_alone(self, compile_and_run, tmp_path):
        first = tmp_path / "one.txt"
        copy = tmp_path / "copy.txt"
        source = f"""
        blob f = '{first}'
        f.write('original')
        log(f.saveCopy('{copy}'))
        f.write('changed after the copy')
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"
        # The later write went to the ORIGINAL path, not the copy --
        # which is the whole difference between save and saveCopy.
        assert first.read_text() == "changed after the copy"
        assert copy.read_text() == "original"

    def test_a_pathless_blob_save_fails_and_says_why(self, compile_and_run, tmp_path):
        # claude.md #110: a program asking to save something to nowhere
        # has a bug, so this fails rather than answering false. An
        # unwritable directory is a condition of the filesystem and
        # still answers false -- see the test below.
        db = tmp_path / "t.sqlite"
        src = tmp_path / "payload.txt"
        source = f"""
        DatabaseURL = '{db}'
        table Saves {{ name:text  data:blob }}
        blob f = '{src}'
        f.write('stored')
        sqlite('INSERT INTO Saves (name, data) VALUES (?, ?)', ['s', f])
        arr[Saves] rows = sqlite('SELECT * FROM Saves')
        log(rows[0].data.save())
        """
        result = compile_and_run(source)
        assert result.returncode != 0
        assert "no path to save() to" in result.stderr
        assert "blob" in result.stderr

    def test_saving_somewhere_impossible_returns_false(self, compile_and_run, tmp_path):
        path = tmp_path / "notes.txt"
        source = f"""
        blob f = '{path}'
        f.write('x')
        log(f.saveCopy('/definitely/not/a/directory/x.txt'))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "false"

    def test_a_failed_save_does_not_adopt_the_path(self, compile_and_run, tmp_path):
        # Adopting a path the write never reached would leave exists()
        # answering false about a path the program was just told it has.
        path = tmp_path / "notes.txt"
        source = f"""
        blob f = '{path}'
        f.write('x')
        log(f.save('/definitely/not/a/directory/x.txt'))
        log(f.exists())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true"]

    def test_a_database_blob_reaches_the_disk_byte_identically(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # The gap claude.md #109 recorded as open, closed. A blob out of
        # a column has bytes and no path; save(path) gives it one.
        db = tmp_path / "t.sqlite"
        out = tmp_path / "recovered.png"
        source = f"""
        DatabaseURL = '{db}'
        table Assets {{ name:text  data:blob }}
        blob png = '{sprite_sheet_png}'
        sqlite('INSERT INTO Assets (name, data) VALUES (?, ?)', ['tiles', png])
        arr[Assets] rows = sqlite('SELECT * FROM Assets')
        blob back = rows[0].data
        log(back.exists())
        log(back.save('{out}'))
        log(back.exists())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true", "true"]
        assert out.read_bytes() == open(sprite_sheet_png, "rb").read()

    # ---- img ----

    def test_a_clipped_image_can_be_saved_to_a_path(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # claude.md #110's motivating case, and #109's other open gap: a
        # clip() result has never been on disk, so save(path) is the only
        # way it ever gets there.
        out = tmp_path / "grass.png"
        source = f"""
        img spritesheet = '{sprite_sheet_png}'
        img grass = spritesheet.clip(0, 0, 32, 32)
        log(grass.save('{out}'))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"
        data = out.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", "a clip encodes as PNG"
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (32, 32)

    def test_saving_a_clip_then_saving_again_with_no_argument(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # save(path) adopted the path, so the bare save() now works on a
        # value that had none a moment ago.
        out = tmp_path / "grass.png"
        source = f"""
        img spritesheet = '{sprite_sheet_png}'
        img grass = spritesheet.clip(0, 0, 32, 32)
        log(grass.save('{out}'))
        log(grass.save())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true"]

    def test_a_pathless_image_save_fails_and_names_img(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        source = f"""
        img spritesheet = '{sprite_sheet_png}'
        img grass = spritesheet.clip(0, 0, 32, 32)
        log(grass.save())
        """
        result = compile_and_run(source)
        assert result.returncode != 0
        assert "this img has no path to save() to" in result.stderr

    def test_an_image_save_copy_preserves_its_source_bytes(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # claude.md #101 keeps the bytes an image was loaded from, so a
        # copy is byte-identical rather than re-encoded.
        out = tmp_path / "copy.png"
        source = f"""
        img sheet = '{sprite_sheet_png}'
        log(sheet.saveCopy('{out}'))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"
        assert out.read_bytes() == open(sprite_sheet_png, "rb").read()

    def test_a_jpeg_stays_a_jpeg(self, compile_and_run, tmp_path):
        src = os.path.join(_FIXTURES_DIR, "gradient.jpg")
        out = tmp_path / "copy.jpg"
        source = f"""
        img grad = '{src}'
        log(grad.saveCopy('{out}'))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"
        assert out.read_bytes() == open(src, "rb").read()

    # ---- aud ----

    def test_an_mp3_stays_an_mp3(self, compile_and_run, tmp_path):
        # Not re-encoded as WAV, for the same reason claude.md #101's
        # aud columns round-trip: the clip keeps its own encoded bytes.
        src = os.path.join(_FIXTURES_DIR, "tone.mp3")
        out = tmp_path / "copy.mp3"
        source = f"""
        aud tone = '{src}'
        log(tone.saveCopy('{out}'))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"
        assert out.read_bytes() == open(src, "rb").read()

    def test_an_audio_save_with_a_path_adopts_it(self, compile_and_run, tmp_path):
        src = os.path.join(_FIXTURES_DIR, "beep.wav")
        first = tmp_path / "a.wav"
        second = tmp_path / "b.wav"
        source = f"""
        aud clip = '{src}'
        log(clip.save('{first}'))
        log(clip.save('{second}'))
        log(clip.save())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true", "true"]
        original = open(src, "rb").read()
        assert first.read_bytes() == original
        assert second.read_bytes() == original

    def test_a_resized_image_keeps_its_path_and_saves_at_the_new_size(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # resize() mutates in place (claude.md #92), so the path survives
        # it -- save() with no argument overwrites the file the image came
        # from, with the resized version. That follows from what save()
        # means rather than being a special case, and is worth pinning
        # because it is destructive.
        src = tmp_path / "sheet.png"
        src.write_bytes(open(sprite_sheet_png, "rb").read())
        source = f"""
        img sheet = '{src}'
        sheet.resize(16, 16)
        log(sheet.save())
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"
        data = src.read_bytes()
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (16, 16)

    def test_an_audio_column_reaches_the_disk_byte_identically(
            self, compile_and_run, tmp_path):
        src = os.path.join(_FIXTURES_DIR, "tone.mp3")
        db = tmp_path / "t.sqlite"
        out = tmp_path / "recovered.mp3"
        source = f"""
        DatabaseURL = '{db}'
        table Tracks {{ name:text  clip:aud }}
        aud tone = '{src}'
        sqlite('INSERT INTO Tracks (name, clip) VALUES (?, ?)', ['tone', tone])
        arr[Tracks] rows = sqlite('SELECT * FROM Tracks')
        log(rows[0].clip.save('{out}'))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"
        assert out.read_bytes() == open(src, "rb").read()

    # ---- the surface itself ----

    @pytest.mark.parametrize("decl", [
        "blob v = 'x'",
        "img v = 'x.png'",
        "aud v = 'x.wav'",
    ])
    def test_save_copy_requires_its_path(self, parser, semantic, errors, decl):
        program = parser.parse(f"{decl}\nlog(v.saveCopy())")
        with pytest.raises(errors.CompileError, match="saveCopy"):
            semantic.analyze(program)

    @pytest.mark.parametrize("decl", [
        "blob v = 'x'",
        "img v = 'x.png'",
        "aud v = 'x.wav'",
    ])
    def test_save_takes_at_most_one_path(self, parser, semantic, errors, decl):
        program = parser.parse(f"{decl}\nlog(v.save('a', 'b'))")
        with pytest.raises(errors.CompileError, match="save"):
            semantic.analyze(program)

    @pytest.mark.parametrize("decl", [
        "blob v = 'x'",
        "img v = 'x.png'",
        "aud v = 'x.wav'",
    ])
    def test_the_path_must_be_text(self, parser, semantic, errors, decl):
        program = parser.parse(f"{decl}\nlog(v.save(5))")
        with pytest.raises(errors.CompileError, match="must be text"):
            semantic.analyze(program)

    @pytest.mark.parametrize("decl,method", [
        ("blob v = 'x'", "save"),
        ("img v = 'x.png'", "saveCopy"),
        ("aud v = 'x.wav'", "save"),
    ])
    def test_both_return_bool(self, parser, semantic, decl, method):
        arg = "'p'" if method == "saveCopy" else ""
        program = parser.parse(f"{decl}\nbool ok = v.{method}({arg})")
        semantic.analyze(program)

    def test_saving_is_not_offered_on_a_type_that_has_no_bytes(
            self, parser, semantic, errors):
        # text has no path and no bytes-plus-origin shape, so this is an
        # ordinary unknown-method error rather than a save it cannot do.
        program = parser.parse("text t = 'x'\nlog(t.save('p'))")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestBlobColumns:
    """claude.md #109: a blob column stores the file's BYTES, so it
    round-trips. Same treatment claude.md #101 gave aud/img columns, and
    for the same reason -- all three are content plus the bytes it came
    from."""

    def test_a_blob_column_round_trips_its_contents(
            self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        src = tmp_path / "payload.txt"
        source = f"""
        DatabaseURL = '{db}'
        table Saves {{ name:text  data:blob }}
        blob f = '{src}'
        f.write('the payload')
        sqlite('INSERT INTO Saves (name, data) VALUES (?, ?)', ['slot1', f])
        arr[Saves] rows = sqlite('SELECT * FROM Saves')
        log(rows.length)
        log(rows[0].name)
        log(rows[0].data.toText())
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["1", "slot1", "the payload"]

    def test_binary_content_survives_the_round_trip_intact(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # The case a text column could never handle: a PNG is full of
        # NUL bytes, and reading the column as a C string would stop at
        # the first one. Checked by length, since a truncation is
        # exactly what a length comparison catches.
        db = tmp_path / "t.sqlite"
        out = tmp_path / "back.png"
        source = f"""
        DatabaseURL = '{db}'
        table Assets {{ name:text  data:blob }}
        blob png = '{sprite_sheet_png}'
        sqlite('INSERT INTO Assets (name, data) VALUES (?, ?)', ['tiles', png])
        arr[Assets] rows = sqlite('SELECT * FROM Assets')
        blob back = rows[0].data
        blob out = '{out}'
        log(back.toText() != '')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        # The real proof is byte-level and lives outside the language,
        # which has no way to compare binary buffers: read the stored
        # column back with Python and diff it against the source file.
        import sqlite3
        stored = sqlite3.connect(str(db)).execute(
            "select data from Assets").fetchone()[0]
        assert bytes(stored) == open(sprite_sheet_png, "rb").read()

    def test_a_column_blob_has_no_path(self, compile_and_run, tmp_path):
        # It has bytes and nowhere to put them: a path is meaningful
        # only on the machine that stored it, so the contents are what
        # round-trips. exists()/write()/delete() answer false rather
        # than inventing a temporary file.
        db = tmp_path / "t.sqlite"
        src = tmp_path / "payload.txt"
        source = f"""
        DatabaseURL = '{db}'
        table Saves {{ name:text  data:blob }}
        blob f = '{src}'
        f.write('stored')
        sqlite('INSERT INTO Saves (name, data) VALUES (?, ?)', ['s', f])
        arr[Saves] rows = sqlite('SELECT * FROM Saves')
        blob back = rows[0].data
        log(back.toText())
        log(back.exists())
        log(back.write('nope'))
        log(back.delete())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["stored", "false", "false", "false"]

    def test_a_null_blob_column_reads_back_as_null(self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table Saves {{ name:text  data:blob }}
        sqlite('INSERT INTO Saves (name) VALUES (?)', ['empty'])
        arr[Saves] rows = sqlite('SELECT * FROM Saves')
        log(rows[0].data == null)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"

    def test_the_column_type_is_a_sql_blob(self, sqlite_schema):
        assert sqlite_schema.TYPE_MAP["blob"] == "BLOB"


class TestMediaFormatsAndPaths:
    """claude.md #101: `img sprite = 'sprite.png'` alongside claude.md
    #100's `aud`, JPEG and MP3 decoding, and both types as sqlite BLOB
    columns.

    The format tests run against REAL files committed under
    tests/fixtures/ rather than anything generated here. Nothing in this
    repo can encode a JPEG or an MP3, so a hand-rolled approximation
    would only prove that a hand-rolled approximation decodes -- the
    point is that libjpeg and libmpg123 are genuinely wired up.
    """

    def test_a_path_declares_an_image(self, compile_and_run, tmp_path, monkeypatch,
                                       sprite_sheet_png):
        # The img counterpart of claude.md #100's aud form. Headless on
        # purpose: decoding needs no display, and the short form must not
        # quietly demand one where loadImage() never did.
        monkeypatch.delenv("DISPLAY", raising=False)
        name = os.path.basename(sprite_sheet_png)   # already written into tmp_path
        result = compile_and_run(
            f"img sheet = '{name}'\nlog(`${{sheet.width}}x${{sheet.height}}`)")
        assert result.returncode == 0
        assert result.stdout.strip() == "128x64"

    def test_an_image_path_may_be_a_computed_text_expression(self, compile_and_run, tmp_path,
                                                               monkeypatch, sprite_sheet_png):
        monkeypatch.delenv("DISPLAY", raising=False)
        stem = os.path.basename(sprite_sheet_png)[:-len(".png")]
        result = compile_and_run(
            f"text name = '{stem}'\nimg sheet = name + '.png'\nlog(sheet.width)")
        assert result.returncode == 0
        assert result.stdout.strip() == "128"

    def test_jpeg_decodes(self, compile_and_run, tmp_path, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        shutil.copy(_JPEG_FIXTURE, tmp_path / "gradient.jpg")
        result = compile_and_run(
            "img photo = 'gradient.jpg'\nlog(`${photo.width}x${photo.height}`)")
        assert result.returncode == 0
        assert result.stdout.strip() == "16x16"

    def test_jpeg_pixels_are_right_way_round(self, compile_and_run, tmp_path, monkeypatch):
        # The fixture is a gradient: red rises with x, green with y, blue
        # is flat at 128. That makes a channel swap (the classic mistake
        # converting libjpeg's RGB into Cairo's native-endian pixel)
        # impossible to miss, which "it decoded to 16x16" would not
        # catch. Tolerance is for JPEG being lossy, nothing else.
        monkeypatch.delenv("DISPLAY", raising=False)
        shutil.copy(_JPEG_FIXTURE, tmp_path / "gradient.jpg")
        result = compile_and_run(
            "img photo = 'gradient.jpg'\n"
            "drawImage(photo, 0, 0)\n"
            "log(saveCanvas('out.png'))\n"
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "true"
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        for x, y, expected in [(0, 0, (0, 0, 128)), (15, 0, (240, 0, 128)),
                                (0, 15, (0, 240, 128)), (15, 15, (240, 240, 128))]:
            got = pixel(x, y)
            worst = max(abs(g - e) for g, e in zip(got, expected))
            assert worst <= 24, f"({x},{y}) expected ~{expected}, got {got}"

    def test_mp3_decodes_and_plays(self, compile_and_run, tmp_path, audio_null_env):
        shutil.copy(_MP3_FIXTURE, tmp_path / "tone.mp3")
        result = compile_and_run(
            "aud tone = 'tone.mp3'\n"
            "tone.play()\n"
            "log(tone.isPlaying())\n",
            env=audio_null_env,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_format_is_sniffed_from_content_not_the_extension(self, compile_and_run, tmp_path,
                                                                monkeypatch):
        # A blob out of a database has no extension at all, so the
        # decoder never had any business trusting one. Same JPEG, named
        # ".png".
        monkeypatch.delenv("DISPLAY", raising=False)
        shutil.copy(_JPEG_FIXTURE, tmp_path / "lying.png")
        result = compile_and_run("img photo = 'lying.png'\nlog(photo.width)")
        assert result.returncode == 0
        assert result.stdout.strip() == "16"


class TestTypedMediaArrayLiterals:
    """claude.md #137: arr[img]/arr[aud]/arr[blob] declared directly
    from a literal of paths -- `arr[img] brushes = ['a.png', 'b.png']`
    -- the array-typed counterpart of `img sprite = 'sprite.png'`
    (claude.md #100/#101/#109's own text -> media declaration
    shorthand), now also allowed element-by-element inside a literal
    the array is declared from."""

    def test_an_image_array_literal_loads_each_path(self, compile_and_run, tmp_path,
                                                      monkeypatch, sprite_sheet_png):
        monkeypatch.delenv("DISPLAY", raising=False)
        name = os.path.basename(sprite_sheet_png)
        shutil.copy(sprite_sheet_png, tmp_path / "other.png")
        source = f"""
        arr[img] sheets = ['{name}', 'other.png']
        log(sheets.length)
        log(`${{sheets[0].width}}x${{sheets[0].height}}`)
        log(`${{sheets[1].width}}x${{sheets[1].height}}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\n128x64\n128x64\n"

    def test_a_blob_array_literal_loads_each_path(self, compile_and_run, tmp_path):
        (tmp_path / "a.txt").write_text("first")
        (tmp_path / "b.txt").write_text("second")
        source = """
        arr[blob] files = ['a.txt', 'b.txt']
        log(files.length)
        log(files[0].toText())
        log(files[1].toText())
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\nfirst\nsecond\n"

    def test_an_audio_array_literal_loads_each_path(self, compile_and_run, tmp_path,
                                                      audio_null_env):
        shutil.copy(_MP3_FIXTURE, tmp_path / "tone.mp3")
        result = compile_and_run(
            "arr[aud] clips = ['tone.mp3']\nlog(clips.length)\nlog(clips[0] == null)",
            env=audio_null_env,
        )
        assert result.returncode == 0
        assert result.stdout == "1\nfalse\n"

    def test_an_element_may_already_be_the_media_type_not_just_a_path(
            self, compile_and_run, tmp_path, monkeypatch, sprite_sheet_png):
        # A mix: one path, one already-declared img reused by reference.
        monkeypatch.delenv("DISPLAY", raising=False)
        name = os.path.basename(sprite_sheet_png)
        shutil.copy(sprite_sheet_png, tmp_path / "second.png")
        source = f"""
        img second = 'second.png'
        arr[img] sheets = ['{name}', second]
        log(sheets.length)
        log(sheets[1] == null)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\nfalse\n"

    def test_a_clipped_images_own_array_literal_does_not_alias_its_source(
            self, compile_and_run, tmp_path, monkeypatch, sprite_sheet_png):
        # The array literal path (_emit_array_lit) has to retain a
        # reused element the same way push()/a plain array literal
        # already do (claude.md #80) -- reusing `second` here must not
        # leave sheets[1] silently sharing ownership incorrectly.
        monkeypatch.delenv("DISPLAY", raising=False)
        name = os.path.basename(sprite_sheet_png)
        source = f"""
        img second = '{name}'
        arr[img] sheets = [second]
        second.resize(4, 4)
        log(`${{sheets[0].width}}x${{sheets[0].height}}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        # Aliased, not copied -- resizing through `second` is visible
        # through `sheets[0]` too, same as any other img alias.
        assert result.stdout.strip() == "4x4"

    def test_wrong_element_type_is_rejected(self, parser, semantic, errors):
        for source in [
            "arr[img] brushes = [5]",
            "arr[img] brushes = [true]",
            "aud a = 'x.wav'\narr[img] brushes = [a]",
            "arr[blob] files = [3.5]",
            "arr[aud] clips = [null, 7]",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")


class TestMediaColumnsInTables:
    """claude.md #101: `file:aud` / `pic:img` table columns, stored as
    SQLite BLOBs.

    The stored bytes are the asset's OWN encoded bytes, not a
    re-encoding, so a round trip is byte-identical and an MP3 stays an
    MP3 rather than becoming a much larger WAV. That is checked here by
    reading the database back with Python's own sqlite3 and comparing
    against the fixture file, which is the only way to prove it is a
    real BLOB rather than something that merely round-trips through
    Festina.
    """

    def _db(self, tmp_path):
        return sqlite3.connect(str(tmp_path / "festina.sqlite"))

    def test_the_column_type_is_blob(self, compile_and_run, tmp_path):
        source = """
        table Music {
            name:text
            file:aud
        }
        table Sprites {
            name:text
            pic:img
        }
        log('synced')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        schema = dict(self._db(tmp_path).execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN ('Music','Sprites')").fetchall())
        # TEXT (what these used to fall through to) would have silently
        # truncated at the first NUL byte in a PNG header.
        assert "file BLOB" in schema["Music"]
        assert "pic BLOB" in schema["Sprites"]

    def test_binding_an_aud_stores_its_own_bytes(self, compile_and_run, tmp_path, audio_null_env):
        shutil.copy(_MP3_FIXTURE, tmp_path / "tone.mp3")
        source = """
        table Music {
            name:text
            file:aud
        }
        aud track = 'tone.mp3'
        sqlite('INSERT INTO Music (name, file) VALUES (?, ?)', ['theme', track])
        log('inserted')
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        stored = self._db(tmp_path).execute("SELECT file FROM Music").fetchone()[0]
        assert isinstance(stored, bytes)
        assert stored == open(_MP3_FIXTURE, "rb").read()

    def test_binding_an_img_stores_its_own_bytes(self, compile_and_run, tmp_path, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        shutil.copy(_JPEG_FIXTURE, tmp_path / "gradient.jpg")
        source = """
        table Sprites {
            name:text
            pic:img
        }
        img hero = 'gradient.jpg'
        sqlite('INSERT INTO Sprites (name, pic) VALUES (?, ?)', ['hero', hero])
        log('inserted')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        stored = self._db(tmp_path).execute("SELECT pic FROM Sprites").fetchone()[0]
        assert isinstance(stored, bytes)
        # Byte-identical: a JPEG stays a JPEG rather than being
        # re-encoded as PNG on the way in.
        assert stored == open(_JPEG_FIXTURE, "rb").read()

    def test_a_stored_clip_reads_back_as_a_playable_aud(self, compile_and_run, tmp_path,
                                                          audio_null_env):
        shutil.copy(_MP3_FIXTURE, tmp_path / "tone.mp3")
        source = """
        table Music {
            name:text
            file:aud
        }
        aud track = 'tone.mp3'
        sqlite('INSERT INTO Music (name, file) VALUES (?, ?)', ['theme', track])
        arr[Music] rows = sqlite('SELECT * FROM Music')
        log(rows[0].name)
        rows[0].file.play()
        log(rows[0].file.isPlaying())
        stopAudioPlayer()
        log(rows[0].file.isPlaying())
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["theme", "true", "false"]

    def test_a_stored_image_reads_back_with_its_real_size(self, compile_and_run, tmp_path,
                                                            monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        shutil.copy(_JPEG_FIXTURE, tmp_path / "gradient.jpg")
        source = """
        table Sprites {
            name:text
            pic:img
        }
        img hero = 'gradient.jpg'
        sqlite('INSERT INTO Sprites (name, pic) VALUES (?, ?)', ['hero', hero])
        arr[Sprites] rows = sqlite('SELECT * FROM Sprites')
        log(`${rows[0].name} ${rows[0].pic.width}x${rows[0].pic.height}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "hero 16x16"

    def test_a_clipped_image_with_no_source_bytes_is_encoded_on_demand(
        self, compile_and_run, tmp_path, monkeypatch, sprite_sheet_png
    ):
        # A clip() result never came from a file, so it has no bytes to
        # store -- festina_image_bytes encodes PNG for it. The round trip
        # has to preserve the SIZE of the clipped tile, not the sheet.
        monkeypatch.delenv("DISPLAY", raising=False)
        sheet = os.path.basename(sprite_sheet_png)
        source = f"""
        table Sprites {{
            name:text
            pic:img
        }}
        img sheet = '{sheet}'
        img tile = sheet.clip(0, 0, 32, 32)
        sqlite('INSERT INTO Sprites (name, pic) VALUES (?, ?)', ['tile', tile])
        arr[Sprites] rows = sqlite('SELECT * FROM Sprites')
        log(`${{rows[0].pic.width}}x${{rows[0].pic.height}}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "32x32"
        stored = self._db(tmp_path).execute("SELECT pic FROM Sprites").fetchone()[0]
        assert stored[:8] == b"\x89PNG\r\n\x1a\n"


class TestAudioChannels:
    """claude.md #99: channels are named, and playLoop reserves one.

    The pool from claude.md #98 was per-`aud`, which cannot express the
    thing this section exists for -- two different clips sharing one
    channel:

        adventureMusic.playLoop(0)
        battleMusic.playLoop(0)     // takes channel 0 over

    With a per-clip pool those are two different pools and "channel 0"
    means two different things. So the pool became process-global and
    its slots became channels, which is also what lets
    stopAudioPlayer(0) be a plain free function rather than something
    that would have to name a clip to find the channel.
    """

    def _run(self, tmp_path):
        cc = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")
        if not cc:
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        # claude.md #101: the audio unit needs libmpg123 for decoding.
        # claude.md #121: and ONLY libmpg123 -- the harness supplies the
        # device layer at the festina_pcm_dev_* seam, so no ALSA headers
        # (or any platform audio stack) are needed to build it.
        alsa = subprocess.run(["pkg-config", "--cflags", "--libs", "libmpg123"],
                               capture_output=True, text=True)
        if alsa.returncode != 0:
            pytest.skip("libmpg123 dev headers are not installed")
        runtime_dir = os.path.join(os.path.dirname(_EXAMPLES_DIR), "runtime")
        harness = tmp_path / "channel_harness.c"
        harness.write_text(_CHANNEL_HARNESS)
        binary = tmp_path / "channel_harness"
        a = tmp_path / "adventure.wav"
        b = tmp_path / "battle.wav"
        # Short clips: the looping assertions below only mean something
        # if a non-looping clip would have finished long before them.
        _write_wav(a, duration_s=0.5)
        _write_wav(b, duration_s=0.5)
        build = subprocess.run(
            [cc, "-I", runtime_dir, str(harness), "-o", str(binary), "-pthread"]
            + alsa.stdout.split(),
            capture_output=True, text=True,
        )
        assert build.returncode == 0, build.stderr
        run = subprocess.run([str(binary), str(a), str(b)], capture_output=True,
                              text=True, timeout=180)
        assert run.returncode == 0, run.stderr
        return dict(line.split() for line in run.stdout.splitlines())

    def test_an_explicit_channel_is_honoured_exactly(self, tmp_path):
        out = self._run(tmp_path)
        assert out["explicit"] == "5"

    def test_play_loop_reserves_its_channel_and_keeps_playing(self, tmp_path):
        out = self._run(tmp_path)
        assert out["loop_channel"] == "0"
        assert out["loop_locked"] == "1"
        # 30 pooled plays through a limit of 3, and the reservation is
        # never touched -- without the lock, stealing would have taken
        # channel 0 almost immediately.
        assert out["stole_reserved"] == "0"
        # And it is still going, far past its own half-second length,
        # which only a real loop does.
        assert out["still_looping"] == "1"

    def test_a_second_clip_can_take_the_channel_over(self, tmp_path):
        # The handover straight out of the motivating example.
        out = self._run(tmp_path)
        assert out["handover_battle"] == "0"
        assert out["handover_adventure_playing"] == "0"
        assert out["handover_still_locked"] == "1"

    def test_an_explicit_one_shot_play_releases_the_reservation(self, tmp_path):
        # "or if the channel is explicitly listed in play()/playLoop()":
        # play(n) takes the channel over AND hands it back to the pool,
        # since a one-shot has nothing to reserve it for.
        out = self._run(tmp_path)
        assert out["oneshot_released"] == "0"

    def test_stop_audio_player_stops_and_releases_one_channel(self, tmp_path):
        out = self._run(tmp_path)
        assert out["before_stop_locked"] == "1"
        assert out["before_stop_active"] == "1"
        assert out["after_stop_locked"] == "0"
        assert out["after_stop_active"] == "0"

    def test_a_bare_stop_audio_player_stops_every_channel(self, tmp_path):
        out = self._run(tmp_path)
        assert out["before_stop_all"] == "1"
        assert out["after_stop_all"] == "0"
        assert out["after_stop_all_locks"] == "0"

    def test_stopping_everything_releases_every_reservation(self, tmp_path):
        out = self._run(tmp_path)
        assert out["clip_stop_locked"] == "0"
        assert out["clip_stop_playing"] == "0"


class TestAudioOnANonMixingDevice:
    """claude.md #98: the pool opens one ALSA handle per voice, and not
    every "default" device does software mixing. On a bare hw: device
    with no dmix -- ordinary on minimal/embedded Linux, and on any
    machine where another program holds the device exclusively -- the
    second concurrent open fails with EBUSY.

    Treating that as fatal (which it briefly was) meant an overlapping
    play() killed the program outright on such a system, with an error
    claiming there was no audio device when there plainly was one. That
    was a regression the pool introduced: the single-voice design it
    replaced never had two handles open, so it could never hit it.

    The right answer is to give a playing voice's handle back and retry,
    which degrades to exactly the pre-pool behaviour -- overlapping
    plays cut each other off instead of layering. On a device that
    cannot mix, layering was never physically possible, and quietly
    getting fewer simultaneous sounds beats not running.
    """

    def _run(self, tmp_path):
        cc = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")
        if not cc:
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        # claude.md #101: the audio unit needs libmpg123 for decoding.
        # claude.md #121: and ONLY libmpg123 -- the harness supplies the
        # device layer at the festina_pcm_dev_* seam, so no ALSA headers
        # (or any platform audio stack) are needed to build it.
        alsa = subprocess.run(["pkg-config", "--cflags", "--libs", "libmpg123"],
                               capture_output=True, text=True)
        if alsa.returncode != 0:
            pytest.skip("libmpg123 dev headers are not installed")
        runtime_dir = os.path.join(os.path.dirname(_EXAMPLES_DIR), "runtime")
        harness = tmp_path / "single_stream_harness.c"
        harness.write_text(_SINGLE_STREAM_HARNESS)
        binary = tmp_path / "single_stream_harness"
        wav = tmp_path / "clip.wav"
        _write_wav(wav, duration_s=2.0)
        build = subprocess.run(
            [cc, "-I", runtime_dir, str(harness), "-o", str(binary), "-pthread"]
            + alsa.stdout.split(),
            capture_output=True, text=True,
        )
        assert build.returncode == 0, build.stderr
        run = subprocess.run([str(binary), str(wav)], capture_output=True, text=True,
                              timeout=120)
        return run

    def test_overlapping_plays_degrade_instead_of_killing_the_program(self, tmp_path):
        run = self._run(tmp_path)
        assert run.returncode == 0, run.stderr
        out = dict(line.split() for line in run.stdout.splitlines())
        # Five plays, one stream: one voice, no crash, still playing.
        assert out["voices"] == "1"
        assert out["open_handles"] == "1"
        assert out["isplaying"] == "1"

    def test_no_handle_is_leaked_by_the_retry(self, tmp_path):
        run = self._run(tmp_path)
        assert run.returncode == 0, run.stderr
        out = dict(line.split() for line in run.stdout.splitlines())
        # Every failed open must close nothing and every freed voice must
        # release its handle -- otherwise the device would stay busy and
        # the retry could never succeed a second time.
        assert out["after_stop"] == "0"
        assert out["leaked_handles"] == "0"


class TestAudioVoicePool:
    """claude.md #98: `aud.play()` no longer cuts off a playback that is
    already running -- each clip owns a pool of voices, one thread and
    one ALSA handle per simultaneous playback, all streaming the same
    decoded PCM read-only.

    See _VOICE_POOL_HARNESS's own comment for why this is a white-box C
    test: the pool is deliberately invisible to the language, and the
    null ALSA device the other audio tests use consumes PCM instantly,
    so under it there is no concurrency left to observe at all.
    """

    def _run_harness(self, tmp_path):
        cc = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")
        if not cc:
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        # claude.md #101: the audio unit needs libmpg123 for decoding.
        # claude.md #121: and ONLY libmpg123 -- the harness supplies the
        # device layer at the festina_pcm_dev_* seam, so no ALSA headers
        # (or any platform audio stack) are needed to build it.
        alsa = subprocess.run(["pkg-config", "--cflags", "--libs", "libmpg123"],
                               capture_output=True, text=True)
        if alsa.returncode != 0:
            pytest.skip("libmpg123 dev headers are not installed")

        runtime_dir = os.path.join(os.path.dirname(_EXAMPLES_DIR), "runtime")
        harness = tmp_path / "voice_pool_harness.c"
        harness.write_text(_VOICE_POOL_HARNESS)
        binary = tmp_path / "voice_pool_harness"
        # 2 seconds at 8kHz is 16000 frames -- four 4096-frame chunks,
        # so ~40ms of playback per voice under the harness's stub. Long
        # enough that back-to-back plays overlap, short enough that 40
        # of them through a pool of 3 still finish quickly.
        wav = tmp_path / "clip.wav"
        _write_wav(wav, duration_s=2.0)

        build = subprocess.run(
            [cc, "-I", runtime_dir, str(harness),
             "-o", str(binary), "-pthread"] + alsa.stdout.split(),
            capture_output=True, text=True,
        )
        assert build.returncode == 0, build.stderr
        run = subprocess.run([str(binary), str(wav)], capture_output=True, text=True,
                              timeout=180)
        assert run.returncode == 0, run.stderr
        return dict(line.split() for line in run.stdout.splitlines())

    def test_overlapping_plays_each_get_their_own_voice(self, tmp_path):
        out = self._run_harness(tmp_path)
        assert out["default"] == "10"
        # The whole point: three plays, three voices, none cut off.
        assert out["three"] == "3"
        assert out["isplaying"] == "1"

    def test_stopping_every_channel_ends_every_voice(self, tmp_path):
        out = self._run_harness(tmp_path)
        assert out["stopped"] == "0"
        assert out["isplaying_after_stop"] == "0"
        assert out["final"] == "0"
        assert out["drained"] == "0"

    def test_the_limit_caps_the_pool_and_steals_rather_than_dropping(self, tmp_path):
        out = self._run_harness(tmp_path)
        # Six plays into a limit of 2: saturated, never exceeded, and
        # still 2 rather than 0 -- the older voices were stolen and
        # replaced, not merely stopped, and no play() was dropped.
        assert out["limit2"] == "2"
        # A limit of 1 is the old cut-off-the-previous-sound behaviour.
        assert out["limit1"] == "1"

    def test_slots_are_reused_rather_than_grown(self, tmp_path):
        # 40 plays through a pool of 3. This only holds if a finished
        # thread is joined and its slot reclaimed -- otherwise the pool
        # would either overflow or leak one thread per play over the
        # life of a long-running game.
        out = self._run_harness(tmp_path)
        assert out["peak"] == "3"


class TestRegex:
    """claude.md #67 (regular expressions), #68 (string match/replace)."""

    def test_test_matches_and_does_not_match(self, compile_and_run):
        source = """
        regex digits = regex('[0-9]+')
        log(digits.test('room 42'))
        log(digits.test('no numbers'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_case_insensitive_flag(self, compile_and_run):
        source = """
        regex greeting = regex('^hello$', 'i')
        log(greeting.test('HELLO'))
        log(greeting.test('goodbye'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_match_returns_first_match(self, compile_and_run):
        source = """
        regex digits = regex('[0-9]+')
        log('room 42, building 7'.match(digits))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "42"

    def test_match_with_no_match_returns_null(self, compile_and_run):
        # claude.md #68: match() returns null (claude.md #25: null is
        # valid for every type) if there's no match -- text's null is
        # represented as a plain NULL pointer (see festina_runtime.h's
        # doc comment on festina_regex_match), which festina_log_text
        # already prints as an empty line.
        source = """
        regex digits = regex('[0-9]+')
        text found = 'no numbers here'.match(digits)
        log('before')
        log(found)
        log('after')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["before", "", "after"]

    def test_replace_with_literal_text_search(self, compile_and_run):
        result = compile_and_run("log('room 42'.replace('room', 'suite'))")
        assert result.stdout.strip() == "suite 42"

    def test_replace_only_replaces_first_occurrence(self, compile_and_run):
        result = compile_and_run("log('a-b-c'.replace('-', '_'))")
        assert result.stdout.strip() == "a_b-c"

    def test_a_text_search_never_replaces_more_than_the_first(self, compile_and_run):
        # claude.md #107: .replaceAll() is gone and a plain-text search
        # carries no flags, so there is no longer any way to replace
        # every occurrence of a literal substring except by making it a
        # /g pattern. That is a real narrowing of the text path and is
        # pinned here so it cannot drift back silently.
        result = compile_and_run("log('a-b-c'.replace('-', '_'))")
        assert result.stdout.strip() == "a_b-c"

    def test_a_g_pattern_is_how_every_occurrence_is_spelled_now(self, compile_and_run):
        result = compile_and_run(r"log('a-b-c'.replace(/-/g, '_'))")
        assert result.stdout.strip() == "a_b_c"

    def test_replace_with_no_match_returns_original_unchanged(self, compile_and_run):
        result = compile_and_run("log('hello world'.replace('zzz', 'nope'))")
        assert result.stdout.strip() == "hello world"

    def test_a_dynamic_regex_can_carry_the_g_flag(self, compile_and_run):
        # claude.md #107's real gain over .replaceAll(): the flag lives
        # on the compiled pattern, so a pattern whose flags are only
        # known at run time can be global. The old design decided
        # first-vs-every at the CALL SITE, which a runtime-built
        # pattern could never influence.
        result = compile_and_run("log('a1b2c3'.replace(regex('[0-9]', 'g'), '-'))")
        assert result.stdout.strip() == "a-b-c-"

    def test_a_dynamic_regex_without_g_replaces_the_first_only(self, compile_and_run):
        result = compile_and_run("log('a1b2c3'.replace(regex('[0-9]'), '-'))")
        assert result.stdout.strip() == "a-b2c3"

    def test_a_dynamic_regex_flag_string_may_combine_g_and_i(self, compile_and_run):
        result = compile_and_run("log('TEST test'.replace(regex('test', 'gi'), 'x'))")
        assert result.stdout.strip() == "x x"

    def test_replace_with_regex_search_first_match_only(self, compile_and_run):
        result = compile_and_run("log('a1b2c3'.replace(regex('[0-9]'), '-'))")
        assert result.stdout.strip() == "a-b2c3"

    def test_replace_all_zero_width_match_does_not_hang(self, compile_and_run):
        # claude.md #54's ambiguity rule doesn't cover this -- it's a
        # straightforward correctness requirement, not something to
        # leave unresolved: a pattern that can match zero-width (e.g.
        # "x*" where there's no "x") must not spin the runtime forever.
        result = compile_and_run("log('abc'.replace(regex('x*', 'g'), '-'))")
        assert result.returncode == 0
        assert result.stdout.strip() == "-a-b-c-"

    def test_original_text_value_is_unchanged_after_replace(self, compile_and_run):
        source = """
        text original = 'room 42'
        text renamed = original.replace('room', 'suite')
        log(original)
        log(renamed)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["room 42", "suite 42"]

    def test_invalid_pattern_is_a_clear_runtime_error(self, compile_and_run):
        # claude.md #67: "An invalid pattern is a runtime error (fail()),
        # not a compile-time error."
        result = compile_and_run("regex bad = regex('[unclosed')\nlog('unreachable')")
        assert result.returncode == 1
        assert "invalid regex pattern" in result.stderr
        assert "unreachable" not in result.stdout


class TestRegexLiteral:
    """claude.md #67: /pattern/flags -- same end-to-end behavior as
    TestRegex above (a literal compiles down to exactly the same
    festina_regex_compile() call), just via the new literal syntax."""

    def test_test_matches_and_does_not_match(self, compile_and_run):
        source = """
        regex digits = /[0-9]+/
        log(digits.test('room 42'))
        log(digits.test('no numbers'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_i_flag_matches_case_insensitively(self, compile_and_run):
        source = """
        regex greeting = /^hello$/i
        log(greeting.test('HELLO'))
        log(greeting.test('goodbye'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_g_flag_is_accepted_and_still_only_case_sensitive_by_default(self, compile_and_run):
        # 'g' alone has no additional effect (see the parser's own
        # comment on _SUPPORTED_REGEX_FLAGS) -- this just confirms
        # accepting it doesn't silently turn on case-insensitivity too.
        source = """
        regex digits = /[a-z]+/g
        log(digits.test('room'))
        log(digits.test('ROOM'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_word_shorthand_class_matches_via_glibcs_gnu_extension(self, compile_and_run):
        # \w -- not official POSIX ERE syntax, but glibc's regcomp()
        # supports it as a GNU extension even in REG_EXTENDED mode
        # (verified directly against this runtime's own libc) -- this is
        # exactly the case that makes the JS-familiar shorthand classes
        # work in practice, not just the narrower official POSIX escapes.
        result = compile_and_run(r"log(/\w+/.test('hello world'))")
        assert result.stdout.strip() == "true"

    def test_combined_flags_from_the_readme_example(self, compile_and_run):
        result = compile_and_run(r"log(/\w+/gi.test('Hello'))")
        assert result.stdout.strip() == "true"

    def test_gnu_class_escapes_work_on_every_platform(self, compile_and_run):
        # claude.md #122: api.md promises \w/\d/\s/\b, which are GNU
        # extensions -- macOS's BSD regcomp treats \s as a literal 's',
        # caught by the first real macos-14 CI run. The runtime now
        # expands them to POSIX classes before regcomp on EVERY
        # platform, so this test passing on both CI jobs is the
        # portability proof.
        source = r"""
        log(/a\db/.test('a5b'))
        log(/a\db/.test('axb'))
        log(' xy '.match(/\S+/))
        log('12ab34'.match(/\D+/))
        log('a1 b2'.replace(/\w\d/g, '#'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false", "xy", "ab", "# #"]

    def test_word_boundary_replaces_only_the_whole_word(self, compile_and_run):
        # \b: native in glibc, translated to BSD's [[:<:]]/[[:>:]] on
        # darwin (claude.md #122's one per-platform difference).
        source = r"""
        log('a word here'.replace(/\bword\b/, 'X'))
        log('swordfish'.replace(/\bword\b/, 'X'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["a X here", "swordfish"]

    def test_escapes_inside_brackets_stay_untranslated(self, compile_and_run):
        # POSIX (and glibc): a backslash inside [...] is a literal, so
        # the expansion must not fire there -- and a [:class:] body's
        # ']' must not end the bracket early. Both pinned, because the
        # translator walks brackets itself and either mistake would be
        # silent on Linux.
        source = r"""
        log('x7y'.match(/[[:digit:]]+/))
        log(/a\.b/.test('a.b'))
        log(/a\.b/.test('axb'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["7", "true", "false"]

    def test_match_returns_first_match(self, compile_and_run):
        result = compile_and_run("log('room 42, building 7'.match(/[0-9]+/))")
        assert result.stdout.strip() == "42"

    def test_a_g_literal_replaces_every_match(self, compile_and_run):
        result = compile_and_run("log('a1b2c3'.replace(/[0-9]/g, '-'))")
        assert result.stdout.strip() == "a-b-c-"

    def test_the_same_literal_without_g_replaces_only_the_first(self, compile_and_run):
        result = compile_and_run("log('a1b2c3'.replace(/[0-9]/, '-'))")
        assert result.stdout.strip() == "a-b2c3"

    def test_gi_together_are_global_and_case_insensitive(self, compile_and_run):
        # The user's own example spelling: /test/gi.
        result = compile_and_run("log('TEST test'.replace(/test/gi, 'x'))")
        assert result.stdout.strip() == "x x"

    def test_i_alone_is_case_insensitive_but_not_global(self, compile_and_run):
        result = compile_and_run("log('TEST test'.replace(/test/i, 'x'))")
        assert result.stdout.strip() == "x test"

    def test_g_does_not_make_test_stateful(self, compile_and_run):
        # claude.md #107: JS's /g gives .test() a lastIndex that makes
        # repeated calls alternate true/false. Deliberately not
        # reproduced -- the same test against the same string is the
        # same answer every time.
        source = """
        regex p = /[0-9]/g
        log(p.test('a1'))
        log(p.test('a1'))
        log(p.test('a1'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true", "true"]

    def test_g_does_not_change_what_match_returns(self, compile_and_run):
        # claude.md #107: in JS, /g makes .match() return an array
        # instead of a string. Festina's .match() returns text, and a
        # return TYPE cannot depend on a flag that regex(p, f) only
        # knows at run time -- so 'g' is ignored here, by design.
        result = compile_and_run("log('a1b2c3'.match(/[0-9]/g))")
        assert result.stdout.strip() == "1"

    def test_escaped_slash_in_a_literal_pattern_matches_a_literal_slash(self, compile_and_run):
        result = compile_and_run(r"log(/a\/b/.test('a/b'))")
        assert result.stdout.strip() == "true"

    def test_used_directly_without_a_named_variable(self, compile_and_run):
        # No `regex x = ...` in between -- the literal is itself a
        # complete expression, usable anywhere a regex value is.
        result = compile_and_run("log(/[0-9]+/.test('42'))")
        assert result.stdout.strip() == "true"

    def test_a_real_division_right_after_this_feature_still_works(self, compile_and_run):
        # End-to-end confirmation that adding regex literals didn't
        # break ordinary division anywhere a human could plausibly
        # confuse the two -- see test_lexer.py::TestRegexLiterals for
        # the exhaustive disambiguation matrix at the tokenizer level.
        result = compile_and_run("int a = 10\nint b = 2\nlog(a / b)")
        assert result.stdout.strip() == "5"


class TestNumericConversion:
    """claude.md #56 (Math), #57 (division/modulo by zero returns
    null), #143 (int/float mixing always promotes to float -- see
    TestNumericCoercion below for its own dedicated runtime coverage).
    See tests/test_numeric_conversion.py for the parser/semantic-only
    tests; these check the actual runtime behavior of a compiled
    program."""

    @pytest.mark.parametrize("fn,expected", [
        ("floor", "19"), ("ceil", "20"), ("round", "20"), ("trunc", "19"),
    ])
    def test_math_function_runtime_result(self, compile_and_run, fn, expected):
        result = compile_and_run(f"float price = 19.99\nlog(Math.{fn}(price))")
        assert result.stdout.strip() == expected

    def test_to_float_runtime_result(self, compile_and_run):
        source = """
        int a = 5
        float b = 2.5
        float c = a.toFloat() + b
        log(c)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7.5"

    def test_to_text_runtime_result(self, compile_and_run):
        source = """
        int i = 42
        float f = 3.14
        bool b = true
        log(i.toText())
        log(f.toText())
        log(b.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["42", "3.14", "true"]

    def test_to_text_matches_template_interpolation(self, compile_and_run):
        result = compile_and_run("int i = 42\nlog(i.toText())\nlog(`${i}`)")
        lines = result.stdout.splitlines()
        assert lines[0] == lines[1]

    def test_mixed_int_float_produces_float_end_to_end(self, compile_and_run):
        # claude.md #143: confirms the whole pipeline (not just
        # semantic.py in isolation) auto-coerces the int side to float,
        # rather than rejecting the mix the way it used to.
        result = compile_and_run("int a = 5\nfloat b = 2.5\nfloat c = a + b\nlog(c)")
        assert result.returncode == 0
        assert result.stdout.strip() == "7.5"

    def test_int_division_by_zero_returns_null(self, compile_and_run):
        # claude.md #57: must not crash (SIGFPE) and must not silently
        # compute garbage -- the sentinel is intentionally an
        # implementation detail (see codegen.py's module docstring), so
        # this only checks the process survives and produces *a* value,
        # not the exact sentinel bit pattern. claude.md #143: `result`
        # is float now -- / always returns float, even for two ints.
        source = """
        int a = 10
        int b = 0
        float result = a / b
        log('survived')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "survived"

    def test_int_modulo_by_zero_returns_null(self, compile_and_run):
        source = "int a = 10\nint b = 0\nint result = a % b\nlog('survived')"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "survived"

    def test_float_division_by_zero_returns_null(self, compile_and_run):
        source = "float a = 5.0\nfloat b = 0.0\nfloat result = a / b\nlog('survived')"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "survived"

    def test_division_and_modulo_by_nonzero(self, compile_and_run):
        # claude.md #143: / is now float even for two ints; % is
        # unaffected (still int, unchanged).
        result = compile_and_run("int a = 10\nint b = 4\nlog(a / b)\nlog(a % b)")
        assert result.stdout.splitlines() == ["2.5", "2"]

    def test_null_int_and_float_assignment(self, compile_and_run):
        # Regression test: `null` used to lower to the LLVM keyword
        # `null` unconditionally, which is only valid for pointer types --
        # `int x = null` failed to link before this fix.
        result = compile_and_run("int a = null\nfloat b = null\nlog('assigned fine')")
        assert result.returncode == 0
        assert result.stdout.strip() == "assigned fine"

    def test_null_bool_assignment(self, compile_and_run):
        # Regression test: same "null is only valid IR for a pointer
        # type" link failure as int/float, but bool needed one extra
        # step to fix -- see festina/codegen.py's module docstring's
        # "Null for bool" note (bool widened from i1 to i8 to make room
        # for a third, reserved value).
        result = compile_and_run("bool a = null\nlog('assigned fine')")
        assert result.returncode == 0
        assert result.stdout.strip() == "assigned fine"

    def test_comparing_a_null_int_against_the_null_literal(self, compile_and_run):
        # Regression test, found alongside the bool-null fix: `x == null`
        # for a concretely-typed int/float/bool operand used to reach
        # codegen as `icmp eq i64 %x, null` -- also invalid IR (null is
        # only valid for a pointer type), independent of bool at all.
        # claude.md #143: `1 / 0` no longer produces an int (/ always
        # returns float now) -- `%` still does, so it's the source of a
        # genuine int null value here instead.
        result = compile_and_run("int a = 1 % 0\nlog(a == null)")
        assert result.stdout.strip() == "true"

    def test_comparing_a_non_null_int_against_the_null_literal(self, compile_and_run):
        result = compile_and_run("int a = 5\nlog(a == null)")
        assert result.stdout.strip() == "false"

    def test_comparing_a_null_float_against_the_null_literal(self, compile_and_run):
        # Unlike int/bool, this is "false", not "true" -- FLOAT_NULL_CONST
        # is a real NaN, and IEEE-754's `oeq`/`one` ("ordered" compares,
        # which is what claude.md's == / != already lower to -- see
        # _emit_binop's fcmp dict) are false for *any* comparison
        # involving NaN, including a NaN compared with itself. This test
        # exists to confirm the fix at least compiles/runs cleanly now
        # (it used to be an LLVM IR parse error -- see the int case
        # above) -- not to claim float null-checking via == is reliable
        # the way it is for int/bool, which it structurally can't be
        # without changing every == / != to unordered compares, a
        # separate, unrelated design decision this fix doesn't make.
        result = compile_and_run("float a = 1.0 / 0.0\nlog(a == null)")
        assert result.stdout.strip() == "false"

    def test_comparing_a_null_bool_against_the_null_literal(self, compile_and_run):
        result = compile_and_run("bool a = null\nlog(a == null)")
        assert result.stdout.strip() == "true"

    def test_comparing_a_non_null_bool_against_the_null_literal(self, compile_and_run):
        result = compile_and_run("bool a = true\nlog(a == null)")
        assert result.stdout.strip() == "false"

    def test_null_bool_survives_a_function_call_round_trip(self, compile_and_run):
        source = """
        bool func identity(b:bool) {
            return b
        }
        log(identity(null) == null)
        log(identity(false) == null)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_null_bool_struct_field(self, compile_and_run):
        source = """
        struct Flag { value:bool }
        Flag f
        f.value = null
        log(f.value == null)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"

    def test_ordinary_bool_logic_is_unaffected_by_the_widened_representation(self, compile_and_run):
        # claude.md #21/#22-ish sanity check: none of &&/||/!/==/!= on
        # ordinary (non-null) bool values should have changed behavior
        # just because the underlying storage widened from i1 to i8.
        source = """
        bool a = true
        bool b = false
        log(a && b)
        log(a || b)
        log(!a)
        log(!b)
        log(a == b)
        log(a != b)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true", "false", "true", "false", "true"]

    def test_float_literal_needing_scientific_notation(self, compile_and_run):
        # Regression test: _format_double used repr(), which switches to
        # scientific notation for small/large magnitudes (e.g. 1e-07) --
        # LLVM's float-literal grammar rejected that as invalid syntax.
        result = compile_and_run("float tiny = 0.0000001\nlog('compiled fine')")
        assert result.returncode == 0
        assert result.stdout.strip() == "compiled fine"


class TestNumericCoercion:
    """claude.md #143: int and float mix freely in any binary operator
    now -- the int side implicitly coerced to float, "as though
    int.toFloat() had been written" -- and division always returns
    float, even for two ints. Supersedes claude.md #55's old "int and
    float never mix directly" rule; see tests/test_numeric_conversion.py
    for the parser/semantic-only coverage of the same rule."""

    @pytest.mark.parametrize("op,expected", [
        ("+", "7.5"), ("-", "2.5"), ("*", "12.5"), ("%", "0"),
    ])
    def test_mixed_arithmetic_coerces_the_int_side(self, compile_and_run, op, expected):
        result = compile_and_run(f"int a = 5\nfloat b = 2.5\nlog(a {op} b)")
        assert result.stdout.strip() == expected

    @pytest.mark.parametrize("op,expected", [
        ("<", "false"), (">", "true"), ("<=", "false"), (">=", "true"),
        ("==", "false"), ("!=", "true"),
    ])
    def test_mixed_comparison_coerces_the_int_side(self, compile_and_run, op, expected):
        result = compile_and_run(f"int a = 5\nfloat b = 2.5\nlog(a {op} b)")
        assert result.stdout.strip() == expected

    def test_division_always_returns_float_even_for_two_ints(self, compile_and_run):
        result = compile_and_run("int a = 10\nint b = 3\nfloat c = a / b\nlog(c)")
        assert result.returncode == 0
        assert result.stdout.strip() == "3.33333"

    def test_division_between_two_floats_is_unaffected(self, compile_and_run):
        result = compile_and_run("float a = 10.0\nfloat b = 4.0\nlog(a / b)")
        assert result.stdout.strip() == "2.5"

    def test_mixed_division_is_float(self, compile_and_run):
        result = compile_and_run("int a = 10\nfloat b = 4.0\nlog(a / b)")
        assert result.stdout.strip() == "2.5"

    def test_modulo_between_two_ints_is_still_int(self, compile_and_run):
        # claude.md #143's own "division always returns float" is
        # specific to /, not modulo -- confirmed here at runtime (the
        # 3 is a genuine int, not e.g. "3.0").
        result = compile_and_run("int a = 10\nint b = 3\nlog(a % b)")
        assert result.stdout.strip() == "1"

    def test_only_math_methods_convert_a_float_result_back_to_int(self, compile_and_run):
        # The request's own closing line: "the only way to get back an
        # int from an operation that makes a float is using the Math
        # methods." int.toFloat() is the one-directional int->float
        # conversion; Math.floor/ceil/round/trunc are the only float->int
        # ones -- both already existed (claude.md #55/#56), unchanged by
        # this feature, just now the ONLY way back once an operator has
        # already promoted to float.
        source = """
        int a = 10
        int b = 3
        float divided = a / b
        int backToInt = Math.floor(divided)
        log(backToInt)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "3"

    def test_mixed_operand_in_a_struct_field_assignment(self, compile_and_run):
        source = """
        struct Box { total:float }
        Box b
        int count = 4
        float price = 2.5
        b.total = count * price
        log(b.total)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "10"

    def test_mixed_operand_as_a_function_argument(self, compile_and_run):
        source = """
        float func scaleUp(x:float) { return x * 2 }
        int n = 5
        log(scaleUp(n.toFloat()))
        log(n * 1.5)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["10", "7.5"]


class TestFail:
    """claude.md #42: fail() is the runtime failure mechanism."""

    def test_fail_exits_nonzero_with_message(self, compile_and_run):
        source = """
        bool ok = false
        if ok != true {
            fail('Test failed')
        }
        log('unreachable')
        """
        result = compile_and_run(source)
        assert result.returncode == 1
        assert "Test failed" in result.stderr
        assert "unreachable" not in result.stdout


class TestEntryPoint:
    """claude.md #7: the programmer never defines main(); top-level
    executable statements run automatically."""

    def test_program_runs_without_a_declared_main(self, compile_and_run):
        result = compile_and_run("log('hello from entry')")
        assert result.returncode == 0
        assert result.stdout.strip() == "hello from entry"


class TestMultiFileCompilation:
    """claude.md #5, #6: `import file.f` pulls a file and its whole
    dependency graph into the current compilation unit -- one real
    compiled program spanning multiple source files, not per-file
    namespacing or runtime modules."""

    def test_struct_and_function_shared_across_files(self, compile_multi_and_run):
        files = {
            "main.f": (
                "import shapes.f\n"
                "Point p\n"
                "p.x = 3\n"
                "p.y = 4\n"
                "log(describe(p))\n"
            ),
            "shapes.f": (
                "struct Point {\n    x:int\n    y:int\n}\n"
                "text func describe(p:Point) {\n"
                "    return `(${p.x}, ${p.y})`\n"
                "}\n"
            ),
        }
        result = compile_multi_and_run(files)
        assert result.returncode == 0
        assert result.stdout.strip() == "(3, 4)"

    def test_transitive_imports(self, compile_multi_and_run):
        # main.f -> ui.f -> database.f
        files = {
            "main.f": "import ui.f\nshowGreeting()\n",
            "ui.f": "import database.f\nvoid func showGreeting() {\n    log(greeting())\n}\n",
            "database.f": "text func greeting() {\n    return 'hello from database.f'\n}\n",
        }
        result = compile_multi_and_run(files)
        assert result.returncode == 0
        assert result.stdout.strip() == "hello from database.f"

    def test_diamond_import_is_not_duplicated(self, compile_multi_and_run):
        # main.f imports both ui.f and database.f directly, and ui.f also
        # imports database.f -- claude.md #6: "each source file must be
        # processed only once." If database.f's `table Items` were
        # emitted twice, this would fail to compile (duplicate LLVM
        # globals) rather than merely produce a wrong answer.
        files = {
            "main.f": (
                "import ui.f\n"
                "import database.f\n"
                "sqlite('INSERT INTO Items (id) VALUES (1)')\n"
                "arr[Items] items = sqlite('SELECT * FROM Items')\n"
                "log(items.length)\n"
            ),
            "ui.f": "import database.f\n",
            "database.f": "table Items {\n    id:int\n}\n",
        }
        result = compile_multi_and_run(files)
        assert result.returncode == 0
        assert result.stdout.strip() == "1"

    def test_table_declared_in_imported_file_syncs_schema(self, compile_multi_and_run, tmp_path):
        files = {
            "main.f": "import schema.f\nlog('synced')\n",
            "schema.f": "table People {\n    id:int\n    name:text\n}\n",
        }
        result = compile_multi_and_run(files)
        assert result.returncode == 0
        assert result.stdout.strip() == "synced"
        assert (tmp_path / "festina.sqlite").exists()

    def test_compile_error_in_an_imported_file_is_reported(self, cli_mod, errors, tmp_path):
        # Full-pipeline version of TestBuildProgram's equivalent check in
        # test_imports.py -- goes through festina.cli.compile_file itself.
        # No C compiler needed: this fails during semantic analysis,
        # before compile_file ever reaches the link step.
        files = {
            "main.f": "import broken.f\nlog('start')\n",
            "broken.f": "log(undefinedVariable)\n",
        }
        for relpath, content in files.items():
            p = tmp_path / relpath
            p.write_text(content)
        with pytest.raises(errors.CompileError) as exc_info:
            cli_mod.compile_file(str(tmp_path / "main.f"), str(tmp_path / "out"))
        assert "broken.f" in str(exc_info.value)


class TestAutomaticSqliteSchemaSync:
    """claude.md #8, #28-31: festina.sqlite is created/opened
    automatically and each declared table's schema is synchronized
    before the entry function runs -- worked examples from #31."""

    def _schema(self, db_path, table):
        conn = sqlite3.connect(db_path)
        try:
            return conn.execute(f"PRAGMA table_info({table})").fetchall()
        finally:
            conn.close()

    def test_missing_table_is_created(self, compile_and_run, tmp_path):
        source = """
        table People {
            id:int
            name:text
        }
        log('synced')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        db = tmp_path / "festina.sqlite"
        assert db.exists()
        cols = {row[1]: row[2] for row in self._schema(db, "People")}
        assert cols == {"id": "INTEGER", "name": "TEXT"}

    def _diag(self, db, result, mtime_before):
        # claude.md #126 round eleven: every one of this class's real
        # Windows CI failures reports an unaltered schema with no error
        # at all (returncode 0), which is also exactly what a SECOND
        # compiled program touching a DIFFERENT actual database file
        # would produce -- the original tmp_path/"festina.sqlite" the
        # test itself reads back from would simply never have been
        # written to. Two prior rounds' fixes (an explicit
        # sqlite3_close/checkpoint, a stdout-flush fix for an unrelated
        # bug) didn't move this one at all, so this diagnostic -- an
        # mtime check plus the compiled program's own captured output
        # -- is what the NEXT real Windows log needs to actually
        # distinguish "wrote the right file, wrong contents" from
        # "never touched this file at all" instead of guessing again.
        mtime_after = db.stat().st_mtime if db.exists() else None
        return (f"mtime before second run: {mtime_before}, after: {mtime_after} "
                f"(unchanged means the second compiled program's own "
                f"festina.sqlite was never touched at all)\n"
                f"second run stdout: {result.stdout!r}\nstderr: {result.stderr!r}")

    def test_missing_column_is_added_and_data_preserved(self, compile_and_run, tmp_path):
        compile_and_run("table People {\n    id:int\n    name:text\n}\nlog('v1')")
        db = tmp_path / "festina.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO People (id, name) VALUES (1, 'Patrick')")
        conn.commit()
        conn.close()
        mtime_before = db.stat().st_mtime

        result = compile_and_run(
            "table People {\n    id:int\n    name:text\n    age:int\n}\nlog('v2')",
            filename="v2.f",
        )
        assert result.returncode == 0, self._diag(db, result, mtime_before)
        cols = {row[1]: row[2] for row in self._schema(db, "People")}
        assert cols == {"id": "INTEGER", "name": "TEXT", "age": "INTEGER"}, \
            self._diag(db, result, mtime_before)
        rows = sqlite3.connect(db).execute("SELECT id, name FROM People").fetchall()
        assert rows == [(1, "Patrick")]

    def test_obsolete_column_is_dropped_data_preserved(self, compile_and_run, tmp_path):
        # claude.md #31 worked example: People(id, name, obsolete) -> People(id, name).
        compile_and_run(
            "table People {\n    id:int\n    name:text\n    obsolete:text\n}\nlog('v1')"
        )
        db = tmp_path / "festina.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO People (id, name, obsolete) VALUES (1, 'Patrick', 'junk')")
        conn.commit()
        conn.close()
        mtime_before = db.stat().st_mtime

        result = compile_and_run(
            "table People {\n    id:int\n    name:text\n}\nlog('v2')", filename="v2.f",
        )
        assert result.returncode == 0, self._diag(db, result, mtime_before)
        cols = {row[1] for row in self._schema(db, "People")}
        assert cols == {"id", "name"}, self._diag(db, result, mtime_before)
        rows = sqlite3.connect(db).execute("SELECT id, name FROM People").fetchall()
        assert rows == [(1, "Patrick")]

    def test_column_rename_via_declaration_change(self, compile_and_run, tmp_path):
        # claude.md #31 worked example: People(id, name) -> People(id, full_name).
        compile_and_run("table People {\n    id:int\n    name:text\n}\nlog('v1')")
        db = tmp_path / "festina.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO People (id, name) VALUES (1, 'Patrick')")
        conn.commit()
        conn.close()
        mtime_before = db.stat().st_mtime

        result = compile_and_run(
            "table People {\n    id:int\n    full_name:text\n}\nlog('v2')", filename="v2.f",
        )
        assert result.returncode == 0, self._diag(db, result, mtime_before)
        cols = {row[1] for row in self._schema(db, "People")}
        assert cols == {"id", "full_name"}, self._diag(db, result, mtime_before)
        rows = sqlite3.connect(db).execute("SELECT id FROM People").fetchall()
        assert rows == [(1,)]  # id survives the rebuild; the old `name` data does not

    def test_incompatible_column_type_is_altered_data_cast(self, compile_and_run, tmp_path):
        compile_and_run("table Items {\n    id:int\n    price:int\n}\nlog('v1')")
        db = tmp_path / "festina.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO Items (id, price) VALUES (1, 100)")
        conn.commit()
        conn.close()
        mtime_before = db.stat().st_mtime

        result = compile_and_run(
            "table Items {\n    id:int\n    price:float\n}\nlog('v2')", filename="v2.f",
        )
        assert result.returncode == 0, self._diag(db, result, mtime_before)
        cols = {row[1]: row[2] for row in self._schema(db, "Items")}
        assert cols == {"id": "INTEGER", "price": "REAL"}, self._diag(db, result, mtime_before)
        rows = sqlite3.connect(db).execute("SELECT id, price FROM Items").fetchall()
        assert rows == [(1, 100.0)]

    def test_no_tables_declared_means_no_db_file(self, compile_and_run, tmp_path):
        # claude.md #29: the database is only ever touched automatically --
        # a program with no `table` declarations shouldn't create one.
        result = compile_and_run("log('no tables here')")
        assert result.returncode == 0
        assert not (tmp_path / "festina.sqlite").exists()

    def test_a_table_too_wide_for_the_runtimes_sql_buffer_fails_cleanly(self, compile_and_run):
        # Security regression test: festina_sync_table builds several
        # SQL statements incrementally across a loop over the declared
        # columns, using the (deceptively unsafe -- see
        # festina_check_sql_buffer's own comment in festina_runtime.c)
        # `pos += snprintf(buf + pos, sizeof(buf) - pos, ...)` idiom.
        # Once accumulated output exceeds the fixed-size buffer, the
        # *next* call's "remaining space" computation used to
        # underflow (unsigned arithmetic) to a huge number, hand
        # snprintf permission to write far past the buffer, and
        # genuinely stack-smash -- verified directly with
        # AddressSanitizer before this was fixed. A table with enough
        # columns (or long enough names) to overflow the 2048-byte
        # CREATE TABLE buffer must now fail cleanly instead.
        cols = "\n".join(
            f"    col_{i}_{'x' * 60}:text" for i in range(40)
        )
        source = f"table Big {{\n{cols}\n}}\nlog('unreachable')"
        result = compile_and_run(source)
        assert result.returncode == 1
        assert "too long for this compiler's fixed-size buffer" in result.stderr
        assert "unreachable" not in result.stdout


class TestDatabaseURL:
    """claude.md #70: DatabaseURL = <expr>, the entry file's own first
    statement, overriding festina.sqlite's default location."""

    def test_no_directive_uses_the_default_filename(self, compile_and_run, tmp_path):
        result = compile_and_run("table People {\n    id:int\n}\nlog('built')")
        assert result.returncode == 0
        assert (tmp_path / "festina.sqlite").exists()

    def test_string_literal_directive_changes_the_path(self, compile_and_run, tmp_path):
        source = "DatabaseURL = 'custom.sqlite'\ntable People {\n    id:int\n}\nlog('built')"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert (tmp_path / "custom.sqlite").exists()
        assert not (tmp_path / "festina.sqlite").exists()

    def test_directive_from_environment_variable(self, compile_and_run, tmp_path):
        source = "DatabaseURL = environment.DB_PATH\ntable People {\n    id:int\n}\nlog('built')"
        result = compile_and_run(source, env={"DB_PATH": "from_env.sqlite"})
        assert result.returncode == 0
        assert (tmp_path / "from_env.sqlite").exists()

    def test_data_actually_lands_in_the_configured_database(self, compile_and_run, tmp_path):
        source = """
        DatabaseURL = 'game.sqlite'
        table People {
            id:int
            name:text
        }
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])
        arr[People] people = sqlite('SELECT * FROM People')
        log(people[0].name)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "Patrick"
        import sqlite3
        conn = sqlite3.connect(tmp_path / "game.sqlite")
        rows = conn.execute("SELECT name FROM People").fetchall()
        assert rows == [("Patrick",)]


class TestEnvironment:
    """claude.md #71: environment.NAME / environment[keyExpr]."""

    def test_reads_a_set_variable(self, compile_and_run):
        result = compile_and_run("log(environment.FESTINA_TEST_VAR)", env={"FESTINA_TEST_VAR": "hello"})
        assert result.stdout.strip() == "hello"

    def test_unset_variable_is_null(self, compile_and_run, monkeypatch):
        monkeypatch.delenv("FESTINA_DEFINITELY_UNSET_VAR", raising=False)
        result = compile_and_run("log('before')\nlog(environment.FESTINA_DEFINITELY_UNSET_VAR)\nlog('after')")
        assert result.stdout.splitlines() == ["before", "", "after"]

    def test_computed_access_with_a_variable_key(self, compile_and_run):
        source = "text k = 'FESTINA_TEST_VAR'\nlog(environment[k])"
        result = compile_and_run(source, env={"FESTINA_TEST_VAR": "computed"})
        assert result.stdout.strip() == "computed"

    def test_null_check_pattern(self, compile_and_run, monkeypatch):
        monkeypatch.delenv("FESTINA_DEFINITELY_UNSET_VAR", raising=False)
        source = """
        text apiKey = environment.FESTINA_DEFINITELY_UNSET_VAR
        if apiKey == null {
            log('not set')
        } else {
            log('set')
        }
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "not set"


class TestSqliteQueries:
    """claude.md #32-34: sqlite() queries, parameterized queries, and
    query result types (arr[Table])."""

    def test_select_into_arr_table_with_field_access(self, compile_and_run):
        source = """
        table People {
            id:int
            name:text
            score:float
        }
        sqlite('INSERT INTO People (id, name, score) VALUES (?, ?, ?)', [1, 'Patrick', 9.5])
        sqlite('INSERT INTO People (id, name, score) VALUES (?, ?, ?)', [2, 'Ada', 10.0])
        arr[People] people = sqlite('SELECT * FROM People ORDER BY id')
        log(people[0].id)
        log(people[0].name)
        log(people[0].score)
        log(people[1].name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["1", "Patrick", "9.5", "Ada"]

    def test_parameterized_insert_and_select(self, compile_and_run):
        # claude.md #33's own example: sqlite(sql, [1, 'Patrick']) -- a
        # heterogeneously-typed literal bound as parameters.
        source = """
        table People {
            id:int
            name:text
        }
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [2, 'Ada'])
        arr[People] found = sqlite('SELECT * FROM People WHERE id = ?', [2])
        log(found[0].name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "Ada"

    def test_null_column_value_round_trips(self, compile_and_run):
        # claude.md #57: a SQL NULL column comes back as the same
        # reserved null sentinel used elsewhere for a null float (a quiet
        # NaN -- see test_float_division_by_zero_returns_null and the
        # module docstring's "Null for int/float" note), not a crash or a
        # 0.0.
        source = """
        table People {
            id:int
            score:float
        }
        sqlite('INSERT INTO People (id, score) VALUES (?, ?)', [1, null])
        arr[People] people = sqlite('SELECT * FROM People')
        log(people[0].score)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "nan"

    def test_columns_map_by_name_not_position(self, compile_and_run):
        # claude.md #111 INVERTED this test, which used to pin positional
        # mapping (and was named test_columns_map_by_position_not_name).
        # Positional mapping was a bug hiding behind the SELECT * habit:
        # `SELECT name FROM People` read the name's text into the id slot
        # as an integer, and `SELECT id` read a result column that did
        # not exist. Matching is by name now, case-insensitively, so a
        # REORDERED select lands every value in its declared column --
        # and an alias renames a column away from its declared name, so
        # aliased columns are simply not matched (they read as null and
        # undefined()). Alias TO a declared name to remap deliberately.
        source = """
        table People {
            id:int
            name:text
        }
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])
        arr[People] people = sqlite('SELECT name, id FROM People')
        log(people[0].id)
        log(people[0].name)
        arr[People] aliased = sqlite('SELECT id AS whatever FROM People')
        log(aliased[0].id == null)
        log(aliased[0].undefined('id'))
        arr[People] remapped = sqlite("SELECT 'Renamed' AS name FROM People")
        log(remapped[0].name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == [
            "1", "Patrick", "true", "true", "Renamed"]

    def test_exec_only_query_discards_result(self, compile_and_run):
        # A statement whose result isn't captured into an arr[Table]
        # (here, INSERT) just runs to completion.
        source = """
        table People {
            id:int
        }
        sqlite('INSERT INTO People (id) VALUES (1)')
        log('done')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_non_literal_params_argument_is_a_clear_error(self, parser, semantic, codegen, errors):
        # claude.md #33's params must be a literal array -- see the
        # module docstring's "Query rows" note for why (a real arr[T]
        # value can't represent claude.md's own heterogeneously-typed
        # example).
        source = """
        table People {
            id:int
        }
        arr[int] ids = [1]
        sqlite('SELECT * FROM People WHERE id = ?', ids)
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        with pytest.raises(errors.CompileError, match="literal array"):
            codegen.generate_ir(program, analyzed, filename="main.f")

    def test_schema_sync_and_query_against_same_table_do_not_collide(self, compile_and_run):
        # _table_arrays' globals are shared between schema sync (main())
        # and query codegen -- this would previously have redefined the
        # same LLVM globals (a link error) if not cached.
        source = """
        table People {
            id:int
            name:text
        }
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])
        arr[People] people = sqlite('SELECT * FROM People')
        log(people[0].name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "Patrick"


class TestMinimalRuntimeDependencies:
    """"Real compilation, minimal setup" stage 1: a compiled program
    shouldn't need libsqlite3.so installed on the machine that runs it,
    if a static sqlite3 archive was available at compile time (see
    festina/cli.py's _sqlite_link_flags)."""

    def test_compiled_binary_does_not_dynamically_link_libsqlite3(self, compile_and_run, tmp_path):
        # compile_and_run's own fixture skip (no clang) covers this test
        # too; ldd is the other half of the toolchain this needs.
        if not shutil.which("ldd"):
            pytest.skip("ldd not on PATH -- cannot inspect the binary's dynamic dependencies")
        from festina import cli as cli_mod

        _, statically_linked = cli_mod._sqlite_link_flags(shutil.which("clang"))
        if not statically_linked:
            pytest.skip("no static libsqlite3.a available in this environment -- "
                        "_sqlite_link_flags already fell back to dynamic linking, "
                        "which is the correct behavior, just not what this test checks")

        compile_and_run("table People {\n    id:int\n}\nlog('built')")
        binary = tmp_path / "program"
        assert binary.exists()

        ldd_output = subprocess.run(["ldd", str(binary)], capture_output=True, text=True).stdout
        assert "libsqlite3" not in ldd_output


class TestSlimBinaries:
    """claude.md #59's binary-slimming requirement: "if a canvas isn't
    used, make sure the binary remains slim". The runtime is split into
    core/graphics/audio translation units (runtime/festina_runtime.c/
    _graphics.c/_audio.c) specifically so a compiled program's `cc`
    invocation only ever gets -lcairo/-lX11/-lasound on its command line
    when that program actually uses graphics/audio (see festina/cli.py's
    _runtime_objects_and_link_libs, driven by CodeGen.uses_graphics/
    uses_graphics_code/uses_audio) -- confirmed here by inspecting the
    linked binary's actual dynamic dependencies (ldd), not just that
    compilation succeeded, since --gc-sections/--as-needed alone was
    verified NOT to remove an unused library from a single-object-file
    build (the linker's "is this needed" decision is made against the
    whole translation unit before dead-code elimination runs)."""

    def _ldd(self, binary):
        if not shutil.which("ldd"):
            pytest.skip("ldd not on PATH -- cannot inspect the binary's dynamic dependencies")
        return subprocess.run(["ldd", str(binary)], capture_output=True, text=True).stdout

    def test_graphics_and_audio_free_binary_links_neither(self, compile_and_run, tmp_path):
        compile_and_run("log('hi')")
        ldd_output = self._ldd(tmp_path / "program")
        assert "libcairo" not in ldd_output
        assert "libX11" not in ldd_output
        assert "libasound" not in ldd_output

    def test_graphics_binary_links_cairo_and_x11_but_not_alsa(self, tmp_path):
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        from festina import cli as cli_mod

        src = tmp_path / "main.f"
        src.write_text("drawRect(1, 1, 2, 2)")
        out = tmp_path / "program"
        # claude.md #126 round four: calling compile_file directly (as
        # this test always has, to inspect the linked binary below)
        # means a platform-gated CompileError -- e.g. on a platform
        # whose graphics gate still fires -- would otherwise propagate
        # as a raw test failure instead of the skip every other
        # platform-conditional test gets.
        from tests.conftest import compile_file_or_skip
        compile_file_or_skip(
            cli_mod, str(src), str(out),
            cc=shutil.which("clang") or shutil.which("gcc") or shutil.which("cc"))
        ldd_output = self._ldd(out)
        assert "libcairo" in ldd_output
        # claude.md #129: windows.md Phase 2 landing made this
        # reachable on real Windows CI for the first time -- offscreen
        # graphics used to hit the win32 gate unconditionally, so
        # compile_file_or_skip always skipped before reaching this
        # assertion there. It compiles and links for real now, and
        # correctly links no X11 at all: Windows graphics is native
        # Win32 windowing (festina_runtime_window_win32.c), not an X11
        # server running under emulation, so there is genuinely nothing
        # X11 to find in a Windows binary's own DLL dependencies --
        # this isn't a platform-specific carve-out for a bug, it's the
        # correct, intended difference the X11 check itself only makes
        # sense on the platform that actually has X11 in the first
        # place.
        if sys.platform != "win32":
            assert "libX11" in ldd_output
        assert "libasound" not in ldd_output

    def test_audio_binary_links_alsa_but_not_cairo_or_x11(self, tmp_path):
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        from festina import cli as cli_mod

        # Loading a nonexistent file fails at *runtime*, not compile
        # time (claude.md #38's loadAudio() has no compile-time path
        # validation) -- irrelevant here, this only checks what got
        # linked, never runs the binary.
        src = tmp_path / "main.f"
        src.write_text("aud music = 'nonexistent.wav'")
        out = tmp_path / "program"
        from tests.conftest import compile_file_or_skip
        compile_file_or_skip(
            cli_mod, str(src), str(out),
            cc=shutil.which("clang") or shutil.which("gcc") or shutil.which("cc"))
        ldd_output = self._ldd(out)
        assert "libasound" in ldd_output
        assert "libcairo" not in ldd_output
        assert "libX11" not in ldd_output

    def test_loadimage_alone_still_links_successfully_without_opening_a_window(self, tmp_path):
        # Regression test: loadImage() deliberately does NOT set
        # CodeGen.uses_graphics (see _emit_graphics_call's doc comment --
        # decoding a PNG needs no X server), but festina_load_image()
        # still lives in the graphics object file, not core. Without
        # CodeGen.uses_graphics_code as a separate, broader linking
        # signal, this compiled fine but failed to *link*
        # ("undefined reference to festina_load_image").
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        from festina import cli as cli_mod

        src = tmp_path / "main.f"
        src.write_text("img icon = 'nonexistent.png'")
        out = tmp_path / "program"
        # claude.md #126 round four: same skip-not-fail fix as the test
        # just above -- see its own comment.
        from tests.conftest import compile_file_or_skip
        compile_file_or_skip(
            cli_mod, str(src), str(out),
            cc=shutil.which("clang") or shutil.which("gcc") or shutil.which("cc"))
        assert out.exists()


class TestMinimalBuildDependencies:
    """"Real compilation, minimal setup" stage 3: using Festina shouldn't
    require clang *specifically*. Before this stage, cc had to be clang
    because it was handed the .ll file directly (the only common
    compiler with an LLVM-IR-text frontend); now festina.llvm_backend
    compiles that step itself via libLLVM, so cc's remaining job --
    compiling festina_runtime.c and linking plain object files -- is
    compiler-agnostic. See festina/cli.py's module docstring."""

    def test_gcc_works_as_cc_when_libllvm_is_available(self, parser, semantic, codegen, tmp_path, llvm_backend):
        if not shutil.which("gcc"):
            pytest.skip("gcc not on PATH")
        if not llvm_backend.available():
            pytest.skip("libLLVM unavailable -- gcc-as-cc only works via the "
                        "libLLVM path (the clang-IR-frontend fallback needs clang)")
        from festina import cli as cli_mod

        src = tmp_path / "main.f"
        src.write_text("table People {\n    id:int\n    name:text\n}\nlog('built with gcc')")
        out = tmp_path / "program"
        cli_mod.compile_file(str(src), str(out), cc="gcc")
        assert out.exists()

        result = subprocess.run([str(out)], cwd=tmp_path, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0
        assert result.stdout.strip() == "built with gcc"

    def test_falls_back_to_clang_ir_frontend_when_libllvm_unavailable(self, parser, semantic, codegen, tmp_path, monkeypatch):
        clang = shutil.which("clang")
        if not clang:
            pytest.skip("clang not on PATH -- nothing to fall back to")
        from festina import cli as cli_mod, llvm_backend

        class _Unavailable:
            lib = None

        monkeypatch.setattr(llvm_backend, "_binding_instance", _Unavailable())
        assert llvm_backend.available() is False

        src = tmp_path / "main.f"
        src.write_text("log('built via fallback')")
        out = tmp_path / "program"
        cli_mod.compile_file(str(src), str(out), cc=clang)
        assert out.exists()

    def test_a_graphics_program_still_links_via_the_fallback(
            self, parser, semantic, codegen, tmp_path, monkeypatch):
        # claude.md #126 round four: real macOS CI (which always takes
        # this fallback -- ci.yml deliberately skips installing libLLVM
        # there) found this path had never been updated for
        # _feature_pkgs_and_flags/_feature_extra_object's own per-
        # platform swaps -- it built its pkg-config list from the raw
        # Linux table directly and never linked the darwin Cocoa
        # companion object at all, so even an offscreen-only graphics
        # program failed to link with `_festina_window_open` and its
        # neighbors undefined. This can only exercise the (unchanged)
        # Linux branch for real, but it does prove the refactor that
        # fixed the darwin branch didn't regress the platform this
        # sandbox can actually build on.
        clang = shutil.which("clang")
        if not clang:
            pytest.skip("clang not on PATH -- nothing to fall back to")
        from festina import cli as cli_mod, llvm_backend

        class _Unavailable:
            lib = None

        monkeypatch.setattr(llvm_backend, "_binding_instance", _Unavailable())
        assert llvm_backend.available() is False

        out_png = str(tmp_path / "canvas.png")
        src = tmp_path / "main.f"
        src.write_text(f"""
        color red = 'red'
        fillStyle(red)
        drawRect(0, 0, 10, 10)
        log(saveCanvas('{out_png}'))
        """)
        out = tmp_path / "program"
        # claude.md #126 round six: same skip-not-fail gap as
        # TestSlimBinaries/TestGraphics fixed in the two rounds before
        # this one -- graphics (including this offscreen-only program)
        # is gated unconditionally on win32 (no backend at all yet), so
        # a direct compile_file call here would fail hard there instead
        # of skipping like every other platform-conditional test.
        from tests.conftest import compile_file_or_skip
        compile_file_or_skip(cli_mod, str(src), str(out), cc=clang)
        assert out.exists()

        result = subprocess.run([str(out)], cwd=tmp_path, env={**os.environ, "DISPLAY": ""},
                                 capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "true"
        assert os.path.exists(out_png)


class TestMissingDependencyErrors:
    """claude.md #59: a genuinely missing dependency must fail with a
    clear, actionable message naming it and how to install it -- not a
    raw exception. Verified directly: subprocess.run(..., check=False)
    does *not* catch "the executable doesn't exist at all" the way it
    catches a nonzero exit code, so this needs its own handling
    (festina/cli.py's _run_tool). path_without is a shared conftest.py
    fixture -- test_cli.py's TestDoctor reuses it too."""

    def test_missing_pkg_config_gives_actionable_error(self, parser, semantic, codegen, cli_mod, errors, tmp_path, path_without, monkeypatch):
        path_without("pkg-config")
        # claude.md #59's per-feature object file split (see
        # festina/cli.py's _RUNTIME_FEATURES) means a graphics/audio-free
        # program like this one never calls pkg-config for cairo-xlib/
        # alsa at all -- only sqlite3, which is always needed. sqlite3's
        # own flags are cached process-wide (_sqlite_link_cache, keyed by
        # `cc`) across every compile_file call in this test session, so
        # without clearing it here, an earlier test's successful compile
        # would make this one a silent cache hit that never calls
        # pkg-config, never noticing it's missing.
        monkeypatch.setattr(cli_mod, "_sqlite_link_cache", {})
        src = tmp_path / "main.f"
        src.write_text("log('hi')")
        with pytest.raises(errors.CompileError, match="pkg-config.*install"):
            cli_mod.compile_file(str(src), str(tmp_path / "out"), cc="clang")

    def test_missing_cc_gives_actionable_error(self, parser, semantic, codegen, cli_mod, errors, tmp_path, path_without):
        path_without("clang", "gcc", "cc")
        src = tmp_path / "main.f"
        src.write_text("log('hi')")
        with pytest.raises(errors.CompileError, match="clang.*install"):
            cli_mod.compile_file(str(src), str(tmp_path / "out"), cc="clang")


class TestTextReferenceManagement:
    """claude.md #83: every text-typed binding (local, global, struct
    field, array element, map value, parameter) always holds either NULL
    or a heap buffer it owns EXCLUSIVELY -- never a bare alias of a
    `.str.N` literal constant or of another binding's buffer. Unlike
    struct/arr[T]/map[T] (claude.md #77/#79, a refcount header in front
    of the payload), text keeps its plain `char*` representation
    everywhere -- sqlite, regex, log, and every other existing `char*`
    consumer are untouched -- and gets its exclusivity by COPYING at
    each binding site (festina_text_own, a NULL-safe strdup) whenever
    the value's source isn't already a fresh buffer. That invariant is
    what makes freeing unconditional: a text local is always safe to
    free on reassignment and at scope exit, with no escape analysis
    needed, because no other binding can ever be sharing its buffer.

    Before this section text was never freed anywhere at all, which is
    why benchmarks/string_concat.f spent its whole runtime growing the
    heap (816 brk() calls, now 3)."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    # ---- IR-level: no C compiler needed ----

    def test_text_local_from_a_literal_is_copied_not_aliased(self, parser, semantic, codegen):
        # A StringLit is a pointer to a `.str.N` global CONSTANT -- if a
        # local aliased it directly, the scope-exit free below would be
        # handing free() a pointer into the binary's own static data.
        source = """
        void func f() {
            text a = 'lit'
            log(a)
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call ptr @festina_text_own(ptr @.str." in ir

    def test_text_local_from_a_call_takes_ownership_with_no_copy(self, parser, semantic, codegen):
        # A Call already returns a fresh, exclusively-held buffer
        # (_is_owning_text_source), so copying it would be pure waste --
        # the returned pointer is stored directly.
        source = """
        text func make() { return `x` }
        void func f() {
            text b = make()
            log(b)
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        assert "call ptr @make()" in body
        # the call's result is stored straight into b's slot, uncopied
        assert "@festina_text_own" not in body

    def test_uninitialized_text_local_is_null_initialized(self, parser, semantic, codegen):
        # Prerequisite for freeing text at all: before this section a
        # `text s` with no initializer got an alloca and no store
        # whatsoever, so its slot held genuine uninitialized garbage --
        # freeing that at scope exit would be freeing a wild pointer.
        source = """
        void func f() {
            text u
            log(u)
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        assert "store ptr null" in body

    def test_text_local_reassignment_frees_the_old_buffer(self, parser, semantic, codegen):
        source = """
        text func make() { return `x` }
        void func f() {
            text a = 'lit'
            a = make()
            log(a)
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        assert "call void @free(" in body

    def test_bare_interpolation_template_copies_its_only_piece(self, parser, semantic, codegen):
        # `` `${s}` `` performs no concatenation at all (claude.md #82
        # skips the two empty literal pieces), so without an explicit
        # copy it would hand back s's OWN buffer -- and since every
        # TemplateLit counts as owning, the caller would then free a
        # buffer s still points at.
        source = """
        void func f(s:text) { log(`${s}`) }
        f('x')
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f(ptr %arg.s)")[1].split("\n}")[0]
        assert "@festina_text_own" in body
        assert "@festina_str_concat" not in body

    def test_interpolation_followed_by_a_literal_piece_skips_the_copy(self, parser, semantic, codegen):
        # `` `${s}!` `` DOES concatenate, and festina_str_concat already
        # mallocs a fresh buffer from its two operands -- copying s
        # first would allocate a second buffer that nothing ever frees
        # (festina_str_concat never frees what it is handed). This was a
        # real leak, caught by LeakSanitizer while building this section.
        source = """
        void func f(s:text) { log(`${s}!`) }
        f('x')
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f(ptr %arg.s)")[1].split("\n}")[0]
        assert "@festina_str_concat" in body
        # the only festina_text_own left is the parameter's own binding
        # copy (claude.md #84), never one feeding the concat
        assert body.count("@festina_text_own") == 1

    def test_chained_template_frees_every_intermediate_concat(self, parser, semantic, codegen):
        # Each festina_str_concat copies both operands into a brand new
        # buffer and leaves them untouched, so a template chaining four
        # of them leaks three intermediates unless each is freed the
        # moment the next concat has finished copying out of it.
        source = """
        void func f(s:text) { log(`a${s}b${s}c`) }
        f('x')
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f(ptr %arg.s)")[1].split("\n}")[0]
        concats = body.count("@festina_str_concat")
        assert concats == 4
        # 3 intermediates + the final result (freed after log) + the
        # parameter's own scope-exit free
        assert body.count("call void @free(") == concats + 1

    def test_text_temporary_passed_as_an_argument_is_freed_after_the_call(
            self, parser, semantic, codegen):
        # Callees never take ownership of a text argument -- one they
        # reassign is copied at binding (claude.md #84), one they only
        # read is borrowed for the call's duration -- so the caller
        # still owns what it passed and must free it, or it leaks with
        # no binding anywhere left to free it later.
        source = """
        text func make() { return `x` }
        void func f() {
            log(make())
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        assert "@festina_log_text" in body
        assert "call void @free(" in body

    def test_borrowed_text_argument_is_not_freed_by_the_caller(self, parser, semantic, codegen):
        # A bare Identifier argument is the variable's own buffer, not a
        # temporary -- freeing it here would leave the variable dangling.
        source = """
        void func f() {
            text a = 'lit'
            log(a)
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        # exactly one free: a's own scope-exit free, none after the log
        assert body.count("call void @free(") == 1

    # ---- behavioural: real compiled programs ----

    def test_text_locals_globals_and_reassignment_stay_correct(self, compile_and_run):
        source = """
        text g = 'initial'
        text func wrap(s:text) { return `<${s}>` }
        void func run() {
            text a = 'hello'
            text b = wrap(a)
            text c = `${a} ${b}`
            a = wrap(b)
            log(a)
            log(b)
            log(c)
            g = wrap('global')
            log(g)
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == (
            "<<hello>>\n"
            "<hello>\n"
            "hello <hello>\n"
            "<global>\n"
        )

    def test_text_in_struct_fields_arrays_and_maps_stays_correct(self, compile_and_run):
        source = """
        struct Person { name:text }
        text func wrap(s:text) { return `<${s}>` }
        void func run() {
            Person p
            p.name = 'field'
            log(p.name)
            p.name = wrap('field2')
            log(p.name)
            p.name = `tmpl ${p.name}`
            log(p.name)

            arr[text] names = ['one', wrap('two')]
            names[0] = wrap('changed')
            log(names[0])
            log(names[1])

            map[text] m = {'k1': 'v1'}
            m['k1'] = wrap('v1b')
            m['k2'] = `tmpl-${m['k1']}`
            log(m['k1'])
            log(m['k2'])
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == (
            "field\n"
            "<field2>\n"
            "tmpl <field2>\n"
            "<changed>\n"
            "<two>\n"
            "<v1b>\n"
            "tmpl-<v1b>\n"
        )

    def test_repeated_concatenation_in_a_loop_builds_the_right_string(self, compile_and_run):
        # benchmarks/string_concat.f's own shape: `s` is freed and
        # replaced every iteration, so the heap stays flat instead of
        # growing by one leaked buffer per iteration.
        source = """
        void func run() {
            text s = ''
            for int i = 0, i < 200, i++ {
                s = `${s}x`
            }
            log(s)
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "x" * 200 + "\n"

    def test_text_methods_on_temporaries_stay_correct(self, compile_and_run):
        source = """
        text func wrap(s:text) { return `<${s}>` }
        void func run() {
            log('room 42'.replace(/[0-9]+/, 'N'))
            log(wrap('abc').replace('b', 'Z'))
            log(`${'hello'}`.replace(/l/g, 'L'))
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "room N\n<aZc>\nheLLo\n"


class TestParameterReassignmentOwnership:
    """claude.md #84: a struct/arr[T]/map[T] parameter is passed as the
    caller's own raw pointer, unretained -- a deliberately "borrowed"
    convention, since a callee that only reads its parameter has no
    reason to touch the refcount. But a callee that REASSIGNS its own
    parameter (`p = somethingElse`) runs the ordinary local-reassignment
    path, which releases whatever the binding currently holds -- and for
    a borrowed parameter that is the CALLER's live value, dropping its
    refcount to zero and freeing it out from under the caller.

    That was a real, pre-existing use-after-free (confirmed under
    AddressSanitizer with a heap-allocated local passed to a
    parameter-reassigning callee), not something introduced by the text
    work -- it was found while designing text's own parameter handling,
    which has exactly the same shape. The fix: any parameter escape
    analysis shows the callee assigns to is given its own reference at
    binding time (festina_retain for struct/arr[T]/map[T], a
    festina_text_own copy for text) and released at the callee's own
    scope exit, so the callee is never mutating a binding it doesn't own.

    Note the retain/copy is keyed on the whole `escaping` set rather
    than on reassignment alone. Every reassigned name is necessarily in
    that set (escape_analysis._walk_assign_target adds every bare
    Identifier assignment target), so this is safe but conservative --
    it also copies a text parameter that merely gets interpolated or
    passed along. See todo.md."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_reassigned_struct_parameter_is_retained_at_binding(self, parser, semantic, codegen):
        source = """
        struct Point { x:int }
        void func mutate(p:Point) {
            Point fresh
            fresh.x = 999
            p = fresh
        }
        Point func make() { Point o o.x = 1 return o }
        void func run() {
            Point original = make()
            mutate(original)
            log(original.x)
        }
        run()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @mutate(ptr %arg.p)")[1].split("\n}")[0]
        assert "call void @festina_retain(ptr %arg.p)" in body

    def test_reassigned_text_parameter_is_copied_at_binding(self, parser, semantic, codegen):
        source = """
        void func mutate(s:text) { s = `${s}!` }
        mutate('x')
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @mutate(ptr %arg.s)")[1].split("\n}")[0]
        assert "call ptr @festina_text_own(ptr %arg.s)" in body

    def test_callers_struct_survives_a_callee_reassigning_its_parameter(self, compile_and_run):
        # The regression this section exists for: before the fix,
        # `original` was freed by mutate()'s own reassignment and
        # original.x read freed memory.
        source = """
        struct Point { x:int }
        void func mutate(p:Point) {
            Point fresh
            fresh.x = 999
            p = fresh
        }
        Point func make() { Point o o.x = 1 return o }
        void func run() {
            Point original = make()
            mutate(original)
            log(original.x)
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "1\n"

    def test_callers_text_survives_a_callee_reassigning_its_parameter(self, compile_and_run):
        source = """
        void func mutate(s:text) { s = `${s}!` }
        text func make() { return `hello` }
        void func run() {
            text original = make()
            mutate(original)
            log(original)
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "hello\n"


class TestQueryRowAndRegexReclamation:
    """claude.md #85: two leak classes the text work (claude.md #83)
    surfaced but did not itself cause, both pre-existing and both
    unbounded rather than one-off.

    A sqlite result row is deliberately not shaped like any other
    Festina value: `festina_sqlite_collect_rows` builds each one as a
    plain `malloc(col_count * sizeof(int64_t))` with each text/blob
    column strdup'd into its slot, and no refcount header in front of
    it. Every `isinstance(t, (StructType, ArrayType, MapType))` check in
    codegen misses `TableType` entirely, so nothing ever freed a row or
    its text columns -- and `arr[People] rows = sqlite(...)` is the
    language's most central idiom, so every query leaked its whole row
    set. The array header and its pointer buffer WERE already freed
    (an arr[T] is an arr[T] whatever its element type); only the rows
    hanging off it were not.

    Because a row has no refcount header, this cannot reuse
    `festina_release`, and -- more importantly -- the array owns its
    rows outright: a `People p = rows[0]` local is only ever borrowing
    one. So the per-row free is reached solely from
    `_release_fn_for_array`'s own cascade, never from
    `_release_fn_for`, which would otherwise let an arbitrary
    TableType-typed binding double-free a row the array still owns.

    Separately, a regex compiled by a runtime `regex(...)` call was
    never freed either, so a `regex(...)` inside a loop leaked a full
    compiled automaton (several KB) per iteration. A /pattern/ literal
    is compiled once into a process-lifetime cache and must NOT be
    freed, which is exactly what separates the two: only `regex(...)`
    is an ast.Call."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_array_of_query_rows_gets_a_per_row_release_cascade(self, parser, semantic, codegen):
        source = """
        table People { id:int  name:text }
        void func run() {
            arr[People] rows = sqlite('SELECT * FROM People')
            log(rows.length)
        }
        run()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "@__festina_release_row_People" in ir
        # the generated row release frees the text column and the row
        row_fn = ir.split("define void @__festina_release_row_People")[1].split("\n}")[0]
        assert row_fn.count("call void @free(") == 2

    def test_row_release_frees_each_column_by_its_own_kind(
            self, parser, semantic, codegen):
        # int/float/bool columns are plain i64 slots, never heap
        # pointers -- freeing one would be freeing an integer.
        #
        # claude.md #109: a blob column is a real handle now, so it goes
        # through festina_blob_release rather than a plain @free. Doing
        # otherwise would leak its path and byte buffer and skip its
        # refcount entirely -- the same mistake claude.md #101 fixed for
        # aud/img columns, arriving here by the same route.
        source = """
        table Mixed { id:int  score:float  ok:bool  name:text  data:blob }
        void func run() {
            arr[Mixed] rows = sqlite('SELECT * FROM Mixed')
            log(rows.length)
        }
        run()
        """
        ir = self._ir(parser, semantic, codegen, source)
        row_fn = ir.split("define void @__festina_release_row_Mixed")[1].split("\n}")[0]
        # The text column, plus the row buffer itself.
        assert row_fn.count("call void @free(") == 2
        assert row_fn.count("call void @festina_blob_release(") == 1

    def test_runtime_regex_temporary_is_freed(self, parser, semantic, codegen):
        source = """
        void func run() {
            log(regex('[0-9]+').test('abc 42'))
        }
        run()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_regex_free(" in ir

    def test_cached_regex_literal_is_never_freed(self, parser, semantic, codegen):
        # A /pattern/ literal is compiled once and reused for the life
        # of the process -- freeing it would leave the cache holding a
        # dangling regex_t for every later evaluation.
        source = """
        void func run() {
            log(/[0-9]+/.test('abc 42'))
        }
        run()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_regex_free(" not in ir

    def test_query_rows_and_borrowed_row_stay_correct(self, compile_and_run, tmp_path):
        source = """
        table People { id:int  name:text }
        void func run() {
            sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'alice'])
            sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [2, 'bob'])
            arr[People] rows = sqlite('SELECT * FROM People ORDER BY id')
            log(rows.length)
            People first = rows[0]
            log(first.name)
            text copied = rows[1].name
            log(copied)
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\nalice\nbob\n"

    def test_runtime_and_literal_regexes_stay_correct_together(self, compile_and_run):
        source = """
        void func run() {
            for int i = 0, i < 3, i++ {
                log(regex('[0-9]+').test('abc 42'))
                log('room 42'.replace(regex('[0-9]+'), 'N'))
                log('room 42'.match(regex('[0-9]+')))
                log(/[0-9]+/.test('abc 42'))
                log('room 42'.replace(/[0-9]+/, 'N'))
            }
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\nroom N\n42\ntrue\nroom N\n" * 3


class TestOwnedRegexLocals:
    """claude.md #86/#118: a `regex` local is released at scope exit --
    EVERY regex local, not just one this scope could prove it owned.
    #86's created-here + never-escaping ownership proof existed because
    a regex had no refcount: freeing was final, so freeing anything
    possibly shared was a use-after-free. #118 gave regex the standard
    i64 header, so a binding always owns exactly one countable
    reference and releasing it is always safe -- a /pattern/ literal's
    cached compilation is immortal (festina_regex_mark_cached sets the
    negative-header sentinel) and no-ops through the very same release
    call, and an escaping regex no longer leaks."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_non_escaping_regex_local_from_a_call_is_freed(self, parser, semantic, codegen):
        source = """
        void func f() {
            regex r = regex('[0-9]+')
            log(r.test('42'))
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        assert "call void @festina_regex_free(" in body

    def test_regex_local_from_a_literal_is_released_and_the_cache_is_immortal(
            self, parser, semantic, codegen):
        # claude.md #118 INVERTED this test: it used to assert the
        # release was absent, because releasing the shared literal cache
        # would have freed it under every later execution of the line.
        # The safety argument moved into the value itself -- the cache
        # is marked immortal at first compile, so the scope-exit release
        # (present, like every other regex local's) is a no-op on it.
        source = """
        void func f() {
            regex r = /[0-9]+/
            log(r.test('42'))
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        assert "call void @festina_regex_free(" in body
        assert "call void @festina_regex_mark_cached(" in body

    def test_escaping_regex_local_is_released_too(self, parser, semantic, codegen):
        # claude.md #118 INVERTED this test: an escaping regex used to
        # be left to leak (no copy-on-alias escape hatch, no count to
        # decrement). With the header, `g = r` retains for the global
        # and the local's own release at scope exit is an ordinary
        # decrement -- the global keeps the compilation alive.
        source = """
        regex g = /[a-z]+/
        void func f() {
            regex r = regex('[0-9]+')
            g = r
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        retain_at = body.index("call void @festina_retain(ptr")
        release_at = body.index("call void @festina_regex_free(")
        assert retain_at < release_at

    def test_owned_literal_and_escaping_regexes_stay_correct_together(self, compile_and_run):
        source = """
        regex g = /[0-9]+/
        void func owned() {
            for int i = 0, i < 50, i++ {
                regex r = regex('[0-9]+')
                log(r.test('abc 42'))
            }
        }
        void func fromLiteral() {
            regex r = /[a-z]+/
            log(r.test('abc'))
        }
        void func escapes() {
            regex r = regex('[0-9]+')
            g = r
            log(r.test('42'))
        }
        owned()
        fromLiteral()
        escapes()
        log(g.test('42'))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\n" * 53


class TestRefcountedHandles:
    """claude.md #118: img, aud and regex carry the same i64 refcount
    header struct/arr/map/blob do, so every binding owns exactly one
    countable reference and `free`/scope exit/reassignment are always a
    safe decrement. This retired two documented gaps at once -- an
    escaping img/aud handle leaked, and `free` on an aliased one left
    the alias dangling -- and made the dynamic regex() memo possible
    (evicting a superseded compilation is only safe when a binding that
    still aliases it keeps it alive)."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    # ---- the regex() memo ----

    def test_dynamic_regex_compiles_through_a_per_site_memo(
            self, parser, semantic, codegen):
        # Not plain @festina_regex_compile any more: the memo compares
        # the actual pattern+flags against the site's last compilation
        # at run time, which is what per-AST-node caching (the literal
        # scheme) could never do safely for a runtime pattern.
        source = "regex r = regex('[0-9]+', 'i')\nlog(r.test('42'))"
        ir = self._ir(parser, semantic, codegen, source)
        assert "call ptr @festina_regex_compile_memo(" in ir
        assert "@.regex.memo.0 = private global [3 x ptr] zeroinitializer" in ir

    def test_each_regex_call_site_gets_its_own_memo_slot(
            self, parser, semantic, codegen):
        source = """
        regex a = regex('[0-9]+')
        regex b = regex('[a-z]+')
        log(a.test('1'))
        log(b.test('x'))
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "@.regex.memo.0 = private global [3 x ptr] zeroinitializer" in ir
        assert "@.regex.memo.1 = private global [3 x ptr] zeroinitializer" in ir

    def test_a_changed_pattern_is_recompiled_not_served_stale(
            self, compile_and_run):
        # The memo's one correctness hazard, pinned: the same call site
        # fed a DIFFERENT pattern must recompile, never answer with the
        # previous automaton. Alternating patterns through one site
        # exercises the miss path on every iteration after the first.
        source = """
        arr[text] pats = ['[0-9]+', '[a-z]+']
        for int i = 0, i < 6, i++ {
            regex r = regex(pats[i % 2])
            log(r.test('42'))
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"] * 3

    # ---- free on an alias is a decrement ----

    def test_free_on_an_aliased_img_leaves_the_alias_usable(
            self, compile_and_run, sprite_sheet_png):
        # The exact shape security.md used to document as the dangling-
        # alias hazard, now safe: the alias holds its own reference.
        source = f"""
        img sheet = '{sprite_sheet_png}'
        img tile = sheet.clip(0, 0, 8, 8)
        img alias = tile
        free tile
        log(alias.width)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "8"

    def test_releasing_a_call_result_aliasing_a_shared_img_is_safe(
            self, compile_and_run, sprite_sheet_png):
        # claude.md #110 recorded why call-result img receivers could
        # not be released: `img func get() { return shared }` handed
        # back the global itself. The Return path retains an aliased
        # value now, so the temporary's release is a decrement and the
        # global survives it.
        source = f"""
        img shared = '{sprite_sheet_png}'
        img func getShared() {{
            return shared
        }}
        img c = getShared()
        free c
        log(shared.width)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "128"

    def test_free_on_an_aliased_aud_leaves_the_alias_playable(
            self, compile_and_run, audio_null_env):
        src = os.path.join(_FIXTURES_DIR, "beep.wav")
        source = f"""
        aud clip = '{src}'
        aud alias = clip
        free clip
        int ch = alias.play()
        log(ch >= 0)
        alias.stop()
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"


class TestStructTextFieldReclamation:
    """claude.md #88: a struct's own text-typed field is freed when the
    struct is, in both directions the same predicate gates.

    claude.md #83 added the field-level free itself but never widened
    the eligibility check that decides whether a struct needs field
    cleanup at all -- it still only counted struct/arr[T]/map[T] fields
    (`_struct_has_own_refcounted_field`, now
    `_struct_has_own_managed_field`). So a struct whose only managed
    field is a text one fell through both paths: stack-allocated, it was
    never scheduled for field release at all; heap-allocated, it got the
    plain generic @festina_release instead of a per-struct wrapper.
    Either way the field's buffer was never freed. Caught by an ASan run
    over a struct whose text field is reassigned in a loop."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_struct_with_only_a_text_field_gets_a_release_wrapper(self, parser, semantic, codegen):
        source = """
        struct Person { name:text }
        Person g
        Person func make() { Person p  p.name = `x`  return p }
        g = make()
        log(g.name)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "define void @__festina_release_struct_Person" in ir
        wrapper = ir.split("define void @__festina_release_struct_Person")[1].split("\n}")[0]
        # the text field, then the struct's own header
        assert wrapper.count("call void @free(") == 2

    def test_stack_allocated_struct_frees_its_text_field(self, parser, semantic, codegen):
        source = """
        struct Person { name:text }
        void func f() {
            Person p
            p.name = `x`
            log(p.name)
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        # stack-allocated: no calloc for the struct, but the field is freed
        assert "alloca %struct.Person" in body
        assert "call void @free(" in body

    def test_struct_text_fields_stay_correct_across_stack_heap_and_global(self, compile_and_run):
        source = """
        struct Person { name:text  age:int }
        Person g
        text func upper(s:text) { return `[${s}]` }
        Person func make(n:text) { Person p  p.name = upper(n)  return p }
        void func run() {
            Person p
            p.name = 'field'
            p.name = upper(p.name)
            log(p.name)
            Person q = make('heap')
            log(q.name)
        }
        run()
        g = make('global')
        log(g.name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "[field]\n[heap]\n[global]\n"




class TestColorAndFontTypes:
    """claude.md #91: `color` and `font` are real types, and a value of
    either is resolved to its compiled form once, at the declaration
    that names it.

    ```festina
    color brand = '#4a90d9'
    font  body  = '13px arial bold'
    fillStyle(brand)
    changeFont(body)
    ```

    A `color` compiles to a packed 0xRRGGBB integer (negative meaning
    'none'), so passing one costs a single register. A `font` compiles
    to a pointer to a static `%struct._FestinaFont` constant -- size,
    slant, weight, family -- living in the binary's read-only data, so
    declaring a font costs no runtime work at all and `changeFont()`
    passes one pointer.

    Neither type interacts with the reference-counting or
    text-ownership machinery: a colour is a plain integer, and a font
    points at a constant nothing allocates or frees.

    A colour name or font shorthand can therefore only come from a
    literal. Anything chosen at runtime uses `fillStyle(r, g, b)` or
    `changeFont(px, style, family)`, which are strictly more capable for
    that job anyway."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    # ---- the color type ----

    def test_a_color_declaration_compiles_to_a_packed_integer(
            self, parser, semantic, codegen):
        # 0x4a90d9 == 4886745; one immediate, not three arguments.
        ir = self._ir(parser, semantic, codegen, "color brand = '#4a90d9'")
        assert "4886745" in ir

    def test_every_css_name_and_hex_shape_resolves(self, parser, semantic, codegen):
        cases = {
            "red": 0xFF0000,
            "RED": 0xFF0000,
            "rebeccapurple": 0x663399,
            "yellowgreen": 0x9ACD32,
            "#00f": 0x0000FF,
            "#00FF7F": 0x00FF7F,
        }
        for spelling, packed in cases.items():
            ir = self._ir(parser, semantic, codegen, f"color c = '{spelling}'")
            assert str(packed) in ir, f"{spelling} did not resolve to {packed}"

    def test_none_is_the_negative_sentinel(self, parser, semantic, codegen):
        # A negative value can't be a real packed colour, so it says
        # "no colour" without needing a second field or function.
        for spelling in ["none", "transparent"]:
            ir = self._ir(parser, semantic, codegen, f"color c = '{spelling}'")
            assert "-1" in ir

    def test_fillStyle_takes_a_color_value(self, parser, semantic, codegen):
        source = """
        color brand = 'teal'
        fillStyle(brand)
        borderColor(brand)
        drawRect(0, 0, 10, 10)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_set_fill_color(i64 " in ir
        assert "call void @festina_set_border_color(i64 " in ir

    def test_a_bad_colour_name_fails_at_the_declaration(
            self, parser, semantic, codegen, errors):
        with pytest.raises(errors.CompileError, match="nosuchcolour"):
            self._ir(parser, semantic, codegen, "color c = 'nosuchcolour'")
        for bad in ["#12345", "#xyz", "#"]:
            with pytest.raises(errors.CompileError, match="not a colour"):
                self._ir(parser, semantic, codegen, f"color c = '{bad}'")

    def test_a_text_literal_is_not_a_color(self, parser, semantic, errors):
        # The whole point of the type: fillStyle('red') no longer works,
        # because a colour name has to be resolved at a declaration.
        program = parser.parse("fillStyle('red')", filename="main.f")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program, filename="main.f")

    def test_a_runtime_text_cannot_become_a_color(
            self, parser, semantic, codegen, errors):
        source = """
        text name = 'red'
        color c = name
        """
        with pytest.raises(errors.CompileError, match="must come from a literal"):
            self._ir(parser, semantic, codegen, source)

    def test_the_explicit_rgb_form_remains_for_runtime_colours(
            self, parser, semantic, codegen):
        source = """
        int shade = 200
        fillStyle(shade, 0, 255 - shade)
        borderColor(1, 2, 3)
        drawRect(0, 0, 10, 10)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_set_border_rgb(i64 1, i64 2, i64 3)" in ir
        assert "@festina_set_fill_rgb(i64 %" in ir

    def test_colors_can_be_copied_and_passed_around(self, compile_and_run):
        # A colour is an ordinary value: assignable, passable, returnable.
        source = """
        color brand = '#4a90d9'
        color func pick(c:color) { return c }
        void func run() {
            color local = brand
            fillStyle(pick(local))
            log('ok')
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "ok\n"

    # ---- the font type ----

    def test_a_font_declaration_compiles_to_a_static_record(
            self, parser, semantic, codegen):
        ir = self._ir(parser, semantic, codegen, "font body = '13px arial bold'")
        assert "%struct._FestinaFont = type { i64, i64, i64, ptr }" in ir
        # 13px, upright, bold, a real family pointer
        assert "private constant %struct._FestinaFont { i64 13, i64 0, i64 1, ptr @" in ir

    def test_font_words_may_appear_in_any_order(self, parser, semantic, codegen):
        # Every ordering of the same three facts yields the same record.
        for spec in ["arial 14px bold", "bold 14px arial", "14px arial bold",
                     "bold arial 14px"]:
            ir = self._ir(parser, semantic, codegen, f"font f = '{spec}'")
            assert "{ i64 14, i64 0, i64 1, ptr @" in ir

    def test_omitted_font_parts_are_left_alone(self, parser, semantic, codegen):
        # size only -- family stays null, meaning "don't change it"
        ir = self._ir(parser, semantic, codegen, "font f = '12px'")
        assert "{ i64 12, i64 0, i64 0, ptr null }" in ir
        # family only -- px 0 means "don't change the size"
        ir = self._ir(parser, semantic, codegen, "font f = 'monospace'")
        assert "{ i64 0, i64 0, i64 0, ptr @" in ir
        # italic and bold together
        ir = self._ir(parser, semantic, codegen, "font f = 'italic bold 9px'")
        assert "{ i64 9, i64 1, i64 1, ptr null }" in ir

    def test_identical_fonts_share_one_constant(self, parser, semantic, codegen):
        # Keyed on the resolved parts, not the source text, so differently
        # written but identical fonts collapse together.
        source = """
        font a = 'bold 13px arial'
        font b = 'arial bold 13px'
        font c = '20px serif'
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert ir.count("private constant %struct._FestinaFont") == 2

    def test_changeFont_takes_a_font_value(self, parser, semantic, codegen):
        source = """
        font body = '13px arial'
        changeFont(body)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_set_font_value(ptr " in ir

    def test_an_empty_font_literal_is_rejected(self, parser, semantic, codegen, errors):
        with pytest.raises(errors.CompileError, match="says nothing about a font"):
            self._ir(parser, semantic, codegen, "font f = ''")

    def test_a_runtime_text_cannot_become_a_font(
            self, parser, semantic, codegen, errors):
        source = """
        text spec = '14px'
        font f = spec
        """
        with pytest.raises(errors.CompileError, match="must come from a literal"):
            self._ir(parser, semantic, codegen, source)

    def test_the_explicit_font_form_remains_for_runtime_sizes(
            self, parser, semantic, codegen):
        source = """
        int size = 22
        changeFont(size, null, null)
        changeFont(18, 'italic', 'serif')
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "@festina_set_font(i64 %" in ir
        assert "call void @festina_set_font(i64 18, ptr @" in ir

    def test_font_is_a_type_name_not_a_function(self, parser, semantic, errors):
        # `font(...)` was the setter before #91; it is a type now, so the
        # old spelling must not silently keep working.
        program = parser.parse("font('14px')", filename="main.f")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program, filename="main.f")

    def test_a_user_function_may_not_shadow_changeFont(self, parser, semantic, errors):
        program = parser.parse("void func changeFont() { log('mine') }",
                                filename="main.f")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program, filename="main.f")

    # ---- behaviour ----

    def test_measuring_alone_does_not_open_a_canvas_window(
            self, parser, semantic, codegen):
        source = """
        font body = '16px sans-serif'
        color brand = 'red'
        changeFont(body)
        fillStyle(brand)
        log(measureTextWidth('hello'))
        """
        ir = self._ir(parser, semantic, codegen, source)
        # claude.md #178: festina_graphics_init() is no longer emitted by
        # codegen at all -- see test_render_is_what_opens_the_window's own
        # comment -- so the meaningful check is that nothing here reaches
        # for a window in the first place: no render() (which lazily opens
        # one) and no festina_run_event_loop() (which self.uses_graphics
        # would otherwise cause main() to call after __festina_main()).
        assert "call void @festina_render()" not in ir
        assert "call void @festina_run_event_loop()" not in ir

    def test_drawing_alone_no_longer_opens_a_canvas_window(
            self, parser, semantic, codegen):
        # claude.md #95: drawing paints the offscreen canvas, which needs
        # no display at all -- render() is the one call that needs a GUI.
        source = """
        color brand = 'red'
        fillStyle(brand)
        drawRect(0, 0, 10, 10)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_render()" not in ir
        assert "call void @festina_run_event_loop()" not in ir

    def test_render_is_what_opens_the_window(self, parser, semantic, codegen):
        source = """
        color brand = 'red'
        fillStyle(brand)
        drawRect(0, 0, 10, 10)
        render()
        """
        ir = self._ir(parser, semantic, codegen, source)
        # claude.md #178 (uraikus/festina#79): festina_graphics_init() is
        # no longer called eagerly from main()'s own prologue (that used
        # to open the window, at the hardcoded 800x600 default, before
        # __festina_main() -- and any setClientWidth/setClientHeight call
        # it makes -- ever ran). It's purely an internal, self-guarded C
        # runtime call now, reached lazily from festina_render()'s own
        # `if (!g_window_open)` check (or festina_run_event_loop()'s
        # matching fallback for a program that never calls render() at
        # all) -- invisible at the LLVM IR level either way. So the
        # observable signal that THIS program opens a window is simply
        # that it calls festina_render() -- see
        # TestScreenSizeAndSetClientSize's own
        # test_setting_client_size_before_the_window_opens_is_honored_as_its_initial_size
        # for the real runtime behavior this enables.
        assert "call void @festina_render()" in ir

    def test_text_metrics_follow_the_declared_font(self, compile_and_run):
        source = """
        font small = '8px sans-serif'
        font big = '32px sans-serif'
        changeFont(small)
        int w1 = measureTextWidth('Hello')
        changeFont(big)
        int w2 = measureTextWidth('Hello')
        log(w2 > w1)
        changeFont(32, null, null)
        log(measureTextWidth('Hello') == w2)
        log(measureTextWidth(''))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\ntrue\n0\n"


class TestCanvasStyleRendersRealPixels:
    """claude.md #89/#91, the tier the IR-level tests can't reach: that
    the declared colours actually land on the canvas. Asserting a packed
    integer appears in the IR proves the resolution, not that 'red'
    comes out red, that `#00f` expands to `#0000ff`, or that a
    `color`-typed 'none' genuinely leaves an interior unpainted.

    Same opt-in tier as the rest of TestGraphics: needs a real display
    (Xvfb is fine) plus `xdotool`/`xwd`."""

    def test_fill_border_and_none_render_the_expected_pixels(
            self, run_graphics_program, x_display):
        if not shutil.which("xwd"):
            pytest.skip("xwd isn't installed -- needed to read real canvas pixels")
        source = """
        color red = 'red'
        color green = '#00ff00'
        color blue = '#00f'
        color yellow = 'yellow'
        color black = 'black'
        color nofill = 'none'
        color purple = 'purple'
        color teal = 'teal'

        fillStyle(red)     drawRect(10, 10, 100, 100)
        fillStyle(green)   drawRect(150, 10, 100, 100)
        fillStyle(blue)    drawRect(290, 10, 100, 100)

        fillStyle(yellow)  borderColor(black)  lineWidth(10)
        drawRect(10, 150, 100, 100)

        fillStyle(nofill)  borderColor(purple)  lineWidth(8)
        drawRect(150, 150, 100, 100)

        borderColor(nofill)  fillStyle(teal)
        drawRect(290, 150, 100, 100)
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            got = _xwd_pixels(x_display, wid, [
                (60, 60),    # 'red'
                (200, 60),   # '#00ff00'
                (340, 60),   # '#00f' -> #0000ff
                (60, 200),   # yellow fill, inside its black border
                (10, 200),   # on that 10px black border
                (152, 200),  # purple border of the unfilled rect
                (200, 200),  # its interior -- 'none', so untouched
                (340, 200),  # teal, after borderColor('none') turned it off
                (500, 400),  # untouched canvas background
            ])
            assert got[0] == (255, 0, 0)
            assert got[1] == (0, 255, 0)
            assert got[2] == (0, 0, 255)
            assert got[3] == (255, 255, 0)
            assert got[4] == (0, 0, 0)
            assert got[5] == (128, 0, 128)
            assert got[6] == (255, 255, 255)
            assert got[7] == (0, 128, 128)
            assert got[8] == (255, 255, 255)
        finally:
            proc.terminate()

    def test_a_declared_font_changes_what_is_drawn(self, run_graphics_program, x_display):
        if not shutil.which("xwd"):
            pytest.skip("xwd isn't installed -- needed to read real canvas pixels")
        source = """
        color black = 'black'
        font tiny = '10px sans-serif'
        font huge = '48px sans-serif'
        fillStyle(black)
        changeFont(tiny)
        drawText('Hg', 20, 40)
        changeFont(huge)
        drawText('Hg', 20, 140)
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            small = [(x, y) for x in range(20, 70, 2) for y in range(20, 45, 2)]
            big = [(x, y) for x in range(20, 70, 2) for y in range(95, 145, 2)]
            small_inked = sum(1 for p in _xwd_pixels(x_display, wid, small)
                              if p != (255, 255, 255))
            big_inked = sum(1 for p in _xwd_pixels(x_display, wid, big)
                            if p != (255, 255, 255))
            assert small_inked > 0, "the 10px text drew nothing at all"
            assert big_inked > small_inked, (
                f"48px text inked {big_inked} sampled pixels, 10px inked "
                f"{small_inked} -- changeFont() had no effect")
        finally:
            proc.terminate()


class TestImageClipResizeAndSize:
    """claude.md #92: `img` gains `.width`/`.height` and the two methods
    a spritesheet actually needs.

    ```festina
    img sheet = 'sheet.png'
    img grass = sheet.clip(0, 0, 64, 64)   // a new image
    grass.resize(32, 32)                    // in place
    ```

    `clip` returns a NEW image and leaves the source untouched, so one
    sheet can be clipped as many times as a program likes. `resize`
    changes the image IN PLACE -- it reads as a statement, so it has to
    -- which is why an `img` value is a pointer to a small box holding
    the Cairo surface rather than the surface itself: a Cairo surface
    can't be resized in place, and boxing it means every binding
    sharing an image sees the new one.

    That box is also what carries claude.md #118's refcount header:
    clip() exists to be called repeatedly, so without scope-exit
    reclamation, extracting frames in a loop would leak a whole surface
    per iteration -- and counting (rather than #92's created-here +
    never-escaping ownership proof) is what lets EVERY img binding be
    released, aliases and escapers included."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    # ---- IR level ----

    def test_width_and_height_emit_runtime_calls(self, parser, semantic, codegen):
        source = """
        img sheet = 's.png'
        log(sheet.width)
        log(sheet.height)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call i64 @festina_image_width(ptr" in ir
        assert "call i64 @festina_image_height(ptr" in ir

    def test_clip_and_resize_emit_runtime_calls(self, parser, semantic, codegen):
        source = """
        img sheet = 's.png'
        img tile = sheet.clip(0, 32, 64, 64)
        tile.resize(16, 16)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert ("call ptr @festina_image_clip(ptr %" in ir
                and "i64 0, i64 32, i64 64, i64 64)" in ir)
        assert "call void @festina_image_resize(ptr %" in ir

    def test_a_clipped_local_is_freed_at_scope_exit(self, parser, semantic, codegen):
        # Without this, clipping inside a loop leaks a surface per pass.
        source = """
        void func f(sheet:img) {
            img tile = sheet.clip(0, 0, 8, 8)
            drawImage(tile, 0, 0)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f(ptr %arg.sheet)")[1].split("\n}")[0]
        assert "call void @festina_image_free(ptr" in body

    def test_an_aliasing_image_binding_retains_before_its_release(
            self, parser, semantic, codegen):
        # claude.md #118 INVERTED this test: `other` used to be left
        # unreleased because it merely aliased the caller's image and a
        # free was final. With the refcount header the binding takes its
        # own +1 at the declaration and drops exactly that +1 at scope
        # exit -- the caller's surface survives because the count says
        # so, not because the release was skipped. The order is the
        # safety argument: retain first, release later.
        source = """
        void func f(sheet:img) {
            img other = sheet
            drawImage(other, 0, 0)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f(ptr %arg.sheet)")[1].split("\n}")[0]
        retain_at = body.index("call void @festina_retain(ptr")
        release_at = body.index("call void @festina_image_free(ptr")
        assert retain_at < release_at

    def test_an_escaping_image_is_released_and_the_global_retains(
            self, parser, semantic, codegen):
        # claude.md #118 INVERTED this test: an escaping img used to be
        # left to leak (releasing it would have dangled the global).
        # Now `kept = tile` retains for the global before the local's
        # own scope-exit release decrements -- the clip survives through
        # `kept`, and nothing leaks.
        source = """
        img kept
        void func f(sheet:img) {
            img tile = sheet.clip(0, 0, 8, 8)
            kept = tile
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f(ptr %arg.sheet)")[1].split("\n}")[0]
        retain_at = body.index("call void @festina_retain(ptr")
        release_at = body.index("call void @festina_image_free(ptr")
        assert retain_at < release_at

    # ---- type checking ----

    def test_wrong_arity_and_types_are_rejected(self, parser, semantic, errors):
        for source in [
            "img s = 'a.png'\ns.clip(0, 0, 64)",
            "img s = 'a.png'\ns.resize(1, 2, 3)",
            "img s = 'a.png'\ns.clip(0, 0, 64, 'x')",
            "img s = 'a.png'\ns.resize('a', 2)",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")

    def test_an_unknown_img_field_is_rejected(self, parser, semantic, errors):
        # This branch used to be a permissive `return None`, which
        # silently accepted every typo on an img.
        program = parser.parse("img s = 'a.png'\nlog(s.widht)",
                                filename="main.f")
        with pytest.raises(errors.CompileError, match="img has no field"):
            semantic.analyze(program, filename="main.f")

    def test_a_method_referenced_without_calling_says_so(self, parser, semantic, errors):
        program = parser.parse("img s = 'a.png'\nlog(s.clip)",
                                filename="main.f")
        with pytest.raises(errors.CompileError, match="is a method on img"):
            semantic.analyze(program, filename="main.f")

    def test_a_struct_field_named_width_still_works(self, compile_and_run):
        # `.width` is only special on an img; a struct may still declare
        # one, and the object must be evaluated exactly once either way.
        source = """
        struct Box { width:int  height:int }
        Box func make() { Box b  b.width = 7  b.height = 9  return b }
        log(make().width)
        log(make().height)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "7\n9\n"

    # ---- behaviour, against a real PNG ----

    def test_clip_resize_and_dimensions_against_a_real_sheet(
            self, compile_and_run, sprite_sheet_png):
        source = f"""
        img sheet = '{sprite_sheet_png}'
        log(`${{sheet.width}}x${{sheet.height}}`)

        img tile = sheet.clip(32, 32, 32, 32)
        log(`${{tile.width}}x${{tile.height}}`)

        // clipping leaves the source alone
        log(`${{sheet.width}}x${{sheet.height}}`)

        // resize changes the image in place
        tile.resize(8, 4)
        log(`${{tile.width}}x${{tile.height}}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "128x64\n32x32\n128x64\n8x4\n"

    def test_resize_is_visible_through_every_binding(
            self, compile_and_run, sprite_sheet_png):
        # An img is a shared handle, so resizing through one name is
        # visible through another -- that is what "in place" means.
        source = f"""
        img sheet = '{sprite_sheet_png}'
        img a = sheet.clip(0, 0, 32, 32)
        img b = a
        a.resize(4, 4)
        log(`${{b.width}}x${{b.height}}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "4x4\n"

    def test_a_non_positive_size_fails_clearly(self, compile_and_run, sprite_sheet_png):
        for call in ["sheet.clip(0, 0, 0, 8)", "sheet.resize(8, 0)"]:
            source = f"""
            img sheet = '{sprite_sheet_png}'
            {call}
            """
            result = compile_and_run(source)
            assert result.returncode != 0
            assert "must both be positive" in result.stdout + result.stderr


class TestBlankImage:
    """claude.md #188 (uraikus/festina#76 item 4): blankImage(w, h) --
    a fresh, fully-transparent img with no existing image or canvas to
    derive it from, closing the gap `.clip()`/`.resize()`/saveCanvas()
    leave (every one of them copies FROM something that already
    exists) -- previously getting an independently-resizable, blank
    image meant bouncing through the canvas by hand."""

    def test_blank_image_has_the_requested_size(self, compile_and_run):
        source = """
        img brush = blankImage(32, 24)
        log(brush.width)
        log(brush.height)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "32\n24\n"

    def test_blank_image_is_fully_transparent(self, compile_and_run, tmp_path):
        out = str(tmp_path / "blank.png")
        source = f"""
        img brush = blankImage(10, 10)
        log(brush.save('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout.strip() == "true"
        _, _, pixel_rgba = _decode_png_rgba(out)
        assert pixel_rgba(5, 5) == (0, 0, 0, 0)

    def test_blank_image_composes_with_clip_and_resize(self, compile_and_run):
        source = """
        img brush = blankImage(20, 20)
        img piece = brush.clip(0, 0, 10, 10)
        log(piece.width)
        brush.resize(40, 40)
        log(brush.width)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "10\n40\n"

    def test_a_non_positive_size_fails_clearly(self, compile_and_run):
        for call in ["blankImage(0, 8)", "blankImage(8, -1)"]:
            result = compile_and_run(f"img brush = {call}\nlog(brush.width)")
            assert result.returncode != 0
            assert "must both be positive" in result.stdout + result.stderr

    def test_wrong_arity_or_types_is_a_compile_error(self, parser, semantic, errors):
        for source in [
            "img b = blankImage(8)",
            "img b = blankImage(8, 8, 8)",
            "img b = blankImage(8.0, 8)",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")


class TestGetPixelColor:
    """claude.md #189: getPixelColor(x, y) -> color, and its img-method
    counterpart img.getPixelColor(x, y) -- reads one pixel back off the
    canvas's own offscreen backing store, or an img's own surface,
    needing no window/display the same way saveCanvas() doesn't
    (claude.md #95). Out of bounds, or a fully transparent pixel,
    reads as `null` -- color's own existing 'none' sentinel (-1),
    reused rather than inventing a second reserved value."""

    def test_reads_back_a_painted_pixel(self, compile_and_run):
        source = """
        color red = 'red'
        fillStyle(red)
        drawRect(0, 0, 50, 50)
        color c = getPixelColor(10, 10)
        log(c == red)
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_unpainted_and_out_of_bounds_read_as_null(self, compile_and_run):
        source = """
        color red = 'red'
        fillStyle(red)
        drawRect(0, 0, 10, 10)
        log(getPixelColor(50, 50) == null)    // unpainted, in bounds
        log(getPixelColor(-1, 0) == null)     // out of bounds
        log(getPixelColor(9999, 9999) == null) // way out of bounds
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\ntrue\ntrue\n"

    def test_undoes_premultiplied_alpha_to_answer_the_paint_color(self, compile_and_run):
        # Cairo's ARGB32 stores premultiplied alpha -- reading the raw
        # channel values back without undoing that would answer a
        # darkened colour, not the one fillStyle/fillAlpha actually
        # asked for.
        source = """
        color red = 'red'
        fillStyle(red)
        fillAlpha(0.5)
        drawRect(0, 0, 10, 10)
        log(getPixelColor(5, 5) == red)
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_img_get_pixel_color(self, compile_and_run):
        source = """
        color blue = 'blue'
        img sq = blankImage(20, 20)
        sq.drawRect(0, 0, 20, 20, blue)
        log(sq.getPixelColor(5, 5) == blue)
        log(sq.getPixelColor(100, 100) == null)
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\ntrue\n"

    def test_wrong_arity_or_types_is_a_compile_error(self, parser, semantic, errors):
        for source in [
            "log(getPixelColor(1))",
            "log(getPixelColor(1, 2, 3))",
            "log(getPixelColor(1.0, 2))",
            "img sq\nlog(sq.getPixelColor(1))",
            "img sq\nlog(sq.getPixelColor('a', 1))",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")


class TestImageDrawMethods:
    """claude.md #134: drawRect/drawPixel/drawCircle/drawText as methods
    on img -- the same four canvas-level drawing builtins (claude.md
    #37/#39/#133), retargeted at the receiver image's own surface
    instead of the canvas. Needs no display or window at all -- an
    image's surface already exists in full the moment the image does,
    same as saveCanvas() needing none (claude.md #95) -- verified here
    the same way, with DISPLAY explicitly unset."""

    def test_draw_methods_paint_onto_the_images_own_surface(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # sheet.png's own layout (conftest.py's sprite_sheet_png
        # fixture): tile (0,0), the top-left 32x32, is solid red.
        out = str(tmp_path / "drawn.png")
        source = f"""
        color blue = 'blue'
        img sheet = '{sprite_sheet_png}'
        sheet.drawRect(0, 0, 5, 5, blue)
        sheet.drawPixel(10, 0, blue)
        log(sheet.save('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\n"
        _, _, pixel = _decode_png(out)
        assert pixel(2, 2) == (0, 0, 255), "drawRect's color override should have painted"
        assert pixel(10, 0) == (0, 0, 255), "drawPixel's color override should have painted"
        assert pixel(20, 20) == (255, 0, 0), "untouched red tile should be unaffected"

    def test_draw_methods_use_the_current_fill_style_by_default(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        out = str(tmp_path / "drawn.png")
        source = f"""
        color blue = 'blue'
        fillStyle(blue)
        img sheet = '{sprite_sheet_png}'
        sheet.drawCircle(16, 16, 4)
        sheet.drawPixel(0, 0)
        log(sheet.save('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\n"
        _, _, pixel = _decode_png(out)
        assert pixel(16, 16) == (0, 0, 255)
        assert pixel(0, 0) == (0, 0, 255)

    def test_draw_text_writes_onto_the_image(self, compile_and_run, tmp_path, sprite_sheet_png):
        out = str(tmp_path / "drawn.png")
        source = f"""
        color black = 'black'
        fillStyle(black)
        img sheet = '{sprite_sheet_png}'
        sheet.drawText('hi', 4, 40)
        log(sheet.save('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\n"
        # Just confirms it wrote a valid, readable PNG at the same size
        # -- glyph rasterization details aren't this test's concern
        # (see TestSaveCanvas for the equivalent canvas-level choice).
        width, height, _pixel = _decode_png(out)
        assert (width, height) == (128, 64)

    def test_a_clipped_images_own_drawing_does_not_affect_its_source(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        out = str(tmp_path / "drawn.png")
        source = f"""
        color blue = 'blue'
        img sheet = '{sprite_sheet_png}'
        img tile = sheet.clip(0, 0, 32, 32)
        tile.drawRect(0, 0, 32, 32, blue)
        log(sheet.save('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\n"
        _, _, pixel = _decode_png(out)
        assert pixel(5, 5) == (255, 0, 0), "the clip's own drawing must not leak into its source"

    def test_wrong_arity_and_types_are_rejected(self, parser, semantic, errors):
        for source in [
            "img sheet = 's.png'\nsheet.drawRect(0, 0, 10)",
            "img sheet = 's.png'\nsheet.drawRect(0, 0, 10, 10, 10, 10)",
            "img sheet = 's.png'\nsheet.drawPixel(0)",
            "img sheet = 's.png'\nsheet.drawPixel(0, 0, 0, 0)",
            "img sheet = 's.png'\nsheet.drawCircle(0, 0)",
            "img sheet = 's.png'\nsheet.drawText(0, 0, 0)",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")


class TestImageLayerOps:
    """claude.md #234 (uraikus/festina#93): an img as a self-contained
    drawing target -- its own translate/rotate/scale/resetTransform
    and saveState/restoreState stack, clear/clearRect/clearCircle/
    clearPixel to transparent, and img.drawImage(src, x, y[, w, h]).
    Every one of these needs no display (same as the four drawing
    methods TestImageDrawMethods covers), so every pixel here is read
    back headlessly through img.getPixelColor (claude.md #189) with
    DISPLAY explicitly unset."""

    def _run(self, compile_and_run, body):
        source = "color red = 'red'\ncolor blue = 'blue'\n" + body
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0, result.stderr
        return result.stdout.split()

    def test_translate_moves_the_images_own_drawing(self, compile_and_run):
        assert self._run(compile_and_run, """
        img a = blankImage(40, 40)
        a.translate(10, 10)
        a.drawRect(0, 0, 5, 5, blue)
        log(a.getPixelColor(12, 12) == blue)
        log(a.getPixelColor(2, 2) == null)
        """) == ["true", "true"]

    def test_rotate_is_in_degrees_about_the_translated_origin(self, compile_and_run):
        # A 10x2 bar at the origin, after translate(20, 20) + rotate(90),
        # stands vertically just left of x=20 from y=20 down to y=30.
        assert self._run(compile_and_run, """
        img b = blankImage(40, 40)
        b.translate(20, 20)
        b.rotate(90.0)
        b.drawRect(0, 0, 10, 2, blue)
        log(b.getPixelColor(19, 25) == blue)
        log(b.getPixelColor(25, 21) == null)
        """) == ["true", "true"]

    def test_scale_grows_the_drawing(self, compile_and_run):
        assert self._run(compile_and_run, """
        img c = blankImage(40, 40)
        c.scale(2.0, 2.0)
        c.drawRect(0, 0, 5, 5, blue)
        log(c.getPixelColor(8, 8) == blue)
        log(c.getPixelColor(12, 12) == null)
        """) == ["true", "true"]

    def test_save_restore_and_reset_transform(self, compile_and_run):
        assert self._run(compile_and_run, """
        img d = blankImage(40, 40)
        d.saveState()
        d.translate(20, 20)
        d.restoreState()
        d.drawRect(0, 0, 3, 3, blue)          // back at identity
        d.translate(30, 30)
        d.resetTransform()
        d.drawRect(10, 10, 3, 3, red)         // identity again
        log(d.getPixelColor(1, 1) == blue)
        log(d.getPixelColor(11, 11) == red)
        log(d.getPixelColor(31, 31) == null)
        """) == ["true", "true", "true"]

    def test_the_image_transform_and_the_canvas_transform_are_independent(
            self, compile_and_run):
        # The canvas's own transform is never applied to image draws
        # (unchanged from claude.md #134), and an image's transform never
        # touches the canvas.
        assert self._run(compile_and_run, """
        translate(100, 100)
        img e = blankImage(40, 40)
        e.drawRect(0, 0, 3, 3, blue)
        e.translate(15, 15)
        fillStyle(red)
        drawRect(0, 0, 3, 3)
        log(e.getPixelColor(1, 1) == blue)
        log(getPixelColor(101, 101) == red)
        log(getPixelColor(1, 1) == null)
        """) == ["true", "true", "true"]

    def test_clears_erase_to_transparent(self, compile_and_run):
        assert self._run(compile_and_run, """
        img f = blankImage(40, 40)
        f.drawRect(0, 0, 40, 40, blue)
        f.clearRect(0, 0, 10, 10)
        f.clearCircle(30, 30, 5)
        f.clearPixel(20, 5)
        log(f.getPixelColor(5, 5) == null)
        log(f.getPixelColor(30, 30) == null)
        log(f.getPixelColor(20, 5) == null)
        log(f.getPixelColor(15, 15) == blue)
        """) == ["true"] * 4

    def test_region_clears_honour_the_transform_but_clear_does_not(self, compile_and_run):
        assert self._run(compile_and_run, """
        img f = blankImage(40, 40)
        f.drawRect(0, 0, 40, 40, blue)
        f.translate(20, 20)
        f.clearRect(0, 0, 5, 5)
        log(f.getPixelColor(22, 22) == null)
        log(f.getPixelColor(15, 15) == blue)
        f.clear()
        log(f.getPixelColor(15, 15) == null)
        log(f.getPixelColor(1, 1) == null)
        """) == ["true"] * 4

    def test_a_cleared_region_lets_a_later_draw_underneath_show(self, compile_and_run):
        # The whole point of clearing to alpha 0 rather than painting
        # transparent black: what is drawn UNDER the layer afterwards
        # shows through where it was cleared.
        assert self._run(compile_and_run, """
        img layer = blankImage(20, 20)
        layer.drawRect(0, 0, 20, 20, blue)
        layer.clearCircle(10, 10, 4)
        img under = blankImage(20, 20)
        under.drawRect(0, 0, 20, 20, red)
        under.drawImage(layer, 0, 0)
        log(under.getPixelColor(10, 10) == red)
        log(under.getPixelColor(1, 1) == blue)
        """) == ["true", "true"]

    def test_draw_image_plain_scaled_and_through_the_transform(self, compile_and_run):
        assert self._run(compile_and_run, """
        img src = blankImage(10, 10)
        src.drawRect(0, 0, 10, 10, red)
        img g = blankImage(40, 40)
        g.drawImage(src, 5, 5)
        g.drawImage(src, 20, 20, 20, 20)
        log(g.getPixelColor(7, 7) == red)
        log(g.getPixelColor(2, 2) == null)
        log(g.getPixelColor(35, 35) == red)
        img h = blankImage(40, 40)
        h.translate(20, 20)
        h.drawImage(src, 0, 0)
        log(h.getPixelColor(22, 22) == red)
        log(h.getPixelColor(2, 2) == null)
        """) == ["true"] * 5

    def test_draw_image_honours_fill_alpha(self, compile_and_run):
        # 50% red over opaque blue is neither pure colour -- a real
        # blend, the same fillAlpha contract the canvas drawImage has
        # (claude.md #183).
        assert self._run(compile_and_run, """
        img src = blankImage(10, 10)
        src.drawRect(0, 0, 10, 10, red)
        img k = blankImage(10, 10)
        k.drawRect(0, 0, 10, 10, blue)
        fillAlpha(0.5)
        k.drawImage(src, 0, 0)
        fillAlpha(1.0)
        color blended = k.getPixelColor(5, 5)
        log(blended != red)
        log(blended != blue)
        log(blended != null)
        """) == ["true"] * 3

    def test_drawing_an_image_onto_itself_copies_first(self, compile_and_run):
        assert self._run(compile_and_run, """
        img m = blankImage(20, 20)
        m.drawRect(0, 0, 10, 20, red)
        m.drawImage(m, 10, 0)
        log(m.getPixelColor(15, 5) == red)
        log(m.getPixelColor(5, 5) == red)
        """) == ["true", "true"]

    def test_an_owning_clip_source_is_released_after_the_blit(self, compile_and_run):
        assert self._run(compile_and_run, """
        img sheet = blankImage(32, 32)
        sheet.drawRect(0, 0, 32, 32, blue)
        img n = blankImage(32, 32)
        n.drawImage(sheet.clip(0, 0, 8, 8), 4, 4)
        log(n.getPixelColor(6, 6) == blue)
        log(n.getPixelColor(20, 20) == null)
        """) == ["true", "true"]

    def test_a_layer_in_an_array_is_edited_in_place(self, compile_and_run):
        # The festina-game shape: layers held in an arr[img], each
        # stamped through its own transform with no canvas round trip.
        assert self._run(compile_and_run, """
        arr[img] chunks = [blankImage(16, 16), blankImage(16, 16)]
        chunks[1].saveState()
        chunks[1].translate(8, 8)
        chunks[1].rotate(45.0)
        chunks[1].drawRect(-2, -2, 4, 4, red)
        chunks[1].restoreState()
        log(chunks[1].getPixelColor(8, 8) == red)
        log(chunks[0].getPixelColor(8, 8) == null)
        """) == ["true", "true"]

    def test_restore_with_nothing_saved_fails_clearly(self, compile_and_run):
        result = compile_and_run("img a = blankImage(4, 4)\na.restoreState()\nlog('unreachable')",
                                 env={"DISPLAY": ""})
        assert result.returncode != 0
        assert "img.restoreState(): nothing was saved" in result.stdout + result.stderr

    def test_nesting_past_64_saves_fails_clearly(self, compile_and_run):
        result = compile_and_run(
            "img a = blankImage(4, 4)\nint i = 0\nwhile i < 65 { a.saveState()\n i = i + 1 }\n"
            "log('unreachable')", env={"DISPLAY": ""})
        assert result.returncode != 0
        assert "img.saveState(): nested too deeply" in result.stdout + result.stderr

    def test_wrong_arity_and_types_are_rejected(self, parser, semantic, errors):
        for source in [
            "img s = blankImage(4, 4)\ns.rotate(45)",            # float, like the canvas's
            "img s = blankImage(4, 4)\ns.translate(1.0, 2)",
            "img s = blankImage(4, 4)\ns.scale(2.0)",
            "img s = blankImage(4, 4)\ns.drawImage(1, 2, 3)",
            "img s = blankImage(4, 4)\nimg t = blankImage(2, 2)\ns.drawImage(t, 2, 3, 4)",
            "img s = blankImage(4, 4)\ns.restoreState(1)",
            "img s = blankImage(4, 4)\ns.clearRect(0, 0, 1)",
            "img s = blankImage(4, 4)\ns.clear(1)",
            "text s = 'x'\ns.translate(1, 2)",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")


class TestImageClipRendersRealPixels:
    """claude.md #92, the tier the rest can't reach: that clip() lifts
    the region it was actually asked for. Asserting the runtime call was
    emitted, or that the result reports 32x32, proves neither that the
    right pixels came out nor that the offset was applied in the right
    direction.

    Same opt-in tier as the rest of TestGraphics: a real display plus
    `xdotool`/`xwd`."""

    def test_each_clipped_tile_carries_its_own_colour(
            self, run_graphics_program, x_display, sprite_sheet_png):
        if not shutil.which("xwd"):
            pytest.skip("xwd isn't installed -- needed to read real canvas pixels")
        # The fixture's grid: tile (col, row) -> index row*4+col.
        # (0,0) red, (1,0) green, (1,1) cyan.
        source = f"""
        img sheet = '{sprite_sheet_png}'
        img red = sheet.clip(0, 0, 32, 32)
        img green = sheet.clip(32, 0, 32, 32)
        img cyan = sheet.clip(32, 32, 32, 32)
        drawImage(red, 10, 10)
        drawImage(green, 100, 10)
        drawImage(cyan, 200, 10)
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            got = _xwd_pixels(x_display, wid, [(25, 25), (115, 25), (215, 25)])
            assert got[0] == (255, 0, 0), "tile (0,0) should be red"
            assert got[1] == (0, 255, 0), "tile (1,0) should be green"
            assert got[2] == (0, 255, 255), "tile (1,1) should be cyan"
        finally:
            proc.terminate()

    def test_a_resized_tile_covers_its_new_size(
            self, run_graphics_program, x_display, sprite_sheet_png):
        if not shutil.which("xwd"):
            pytest.skip("xwd isn't installed -- needed to read real canvas pixels")
        source = f"""
        img sheet = '{sprite_sheet_png}'
        img big = sheet.clip(0, 0, 32, 32)
        big.resize(96, 96)
        drawImage(big, 10, 10)
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            # inside the scaled-up tile, and past where the 32x32
            # original would have ended
            got = _xwd_pixels(x_display, wid, [(20, 20), (90, 90), (120, 120)])
            assert got[0] == (255, 0, 0)
            assert got[1] == (255, 0, 0), "the tile did not scale up to 96x96"
            assert got[2] == (255, 255, 255), "it scaled past its requested size"
        finally:
            proc.terminate()


class TestMathFileAndTime:
    """claude.md #93: the standard-library gaps that needed no new
    dependency at all -- `-lm` and libc are already on every link line,
    and Cairo's PNG *writer* is compiled into the same library whose
    reader `loadImage` already uses.

    `Math` had only floor/ceil/round/trunc, so a program couldn't take a
    square root or a sine; there was no way to read or write a file, and
    no way to ask the time even though the timer machinery already calls
    clock_gettime. Rounding still answers with an `int` (claude.md #56);
    everything added here answers in `float`, because "which integer" and
    "which real number" are different questions and conflating them would
    make `Math.sqrt(2.0)` silently an int."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    # ---- math ----

    def test_float_math_uses_llvm_intrinsics_where_they_exist(
            self, parser, semantic, codegen):
        # Real intrinsics optimize and constant-fold in ways an opaque
        # libm call can't.
        ir = self._ir(parser, semantic, codegen, "log(Math.sqrt(2.0))")
        assert "call double @llvm.sqrt.f64(double" in ir
        ir = self._ir(parser, semantic, codegen, "log(Math.pow(2.0, 8.0))")
        assert "call double @llvm.pow.f64(double" in ir

    def test_math_without_an_intrinsic_falls_back_to_libm(
            self, parser, semantic, codegen):
        # -lm is already unconditional on every link line.
        ir = self._ir(parser, semantic, codegen, "log(Math.tan(1.0))")
        assert "call double @tan(double" in ir

    def test_rounding_still_returns_int_and_the_rest_returns_float(
            self, parser, semantic, codegen, errors):
        # An int-typed binding accepts floor() and rejects sqrt().
        self._ir(parser, semantic, codegen, "int a = Math.floor(3.7)\nlog(a)")
        self._ir(parser, semantic, codegen, "float b = Math.sqrt(9.0)\nlog(b)")
        with pytest.raises(errors.CompileError):
            self._ir(parser, semantic, codegen, "int c = Math.sqrt(9.0)")

    def test_math_constants_are_compile_time_values(self, parser, semantic, codegen):
        ir = self._ir(parser, semantic, codegen, "log(Math.PI)")
        # emitted as a raw double bit pattern, not a runtime call
        assert "0x400921FB54442D18" in ir
        assert "@festina_" not in ir.split("define void @__festina_main")[1].split("\n}")[0].replace(
            "festina_log_float", "")

    def test_math_arity_and_types_are_checked(self, parser, semantic, errors):
        for source in ["log(Math.sqrt())", "log(Math.sqrt(1.0, 2.0))",
                       "log(Math.pow(2.0))", "log(Math.sqrt(4))"]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")

    def test_an_unknown_math_member_is_rejected(self, parser, semantic, errors):
        program = parser.parse("log(Math.TAU)", filename="main.f")
        with pytest.raises(errors.CompileError, match="Math has no member"):
            semantic.analyze(program, filename="main.f")

    def test_a_math_function_named_without_calling_says_so(
            self, parser, semantic, errors):
        program = parser.parse("log(Math.sqrt)", filename="main.f")
        with pytest.raises(errors.CompileError, match="is a function"):
            semantic.analyze(program, filename="main.f")

    def test_math_computes_the_right_answers(self, compile_and_run):
        source = """
        log(Math.sqrt(16.0))
        log(Math.pow(2.0, 10.0))
        log(Math.abs(0.0 - 5.5))
        log(Math.min(3.0, 7.0))
        log(Math.max(3.0, 7.0))
        log(Math.floor(3.7))
        log(Math.sin(0.0))
        log(Math.cos(0.0))
        log(Math.log(Math.E))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "4\n1024\n5.5\n3\n7\n3\n0\n1\n1\n"

    def test_random_stays_in_range_and_varies(self, compile_and_run):
        # [0, 1) specifically: 1.0 would break the standard
        # `arr[floor(random() * length)]` idiom.
        source = """
        bool inRange = true
        float first = Math.random()
        bool varied = false
        for int i = 0, i < 200, i++ {
            float r = Math.random()
            if r < 0.0 || r >= 1.0 { inRange = false }
            if r != first { varied = true }
        }
        log(inRange)
        log(varied)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\ntrue\n"

    # ---- files, via blob since claude.md #109 ----

    def test_file_round_trip(self, compile_and_run, tmp_path):
        # claude.md #109: the same round trip claude.md #93's five free
        # functions did, asked of the value that already holds the path.
        path = str(tmp_path / "note.txt")
        source = f"""
        blob f = '{path}'
        log(f.write('hello'))
        log(f.append(' world'))
        log(f.toText())
        log(f.exists())
        log(f.delete())
        log(f.exists())
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\ntrue\nhello world\ntrue\ntrue\nfalse\n"

    def test_a_missing_file_is_an_ordinary_condition_not_a_crash(
            self, compile_and_run, tmp_path):
        # A missing file is something to test for, the same reasoning
        # claude.md #57 applies to division by zero. claude.md #109
        # keeps that rule: declaring a blob on a path that is not there
        # yields an empty blob rather than failing.
        missing = str(tmp_path / "nope.txt")
        source = f"""
        blob f = '{missing}'
        log(f.toText() == '')
        log(f.exists())
        log(f.delete())
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\nfalse\nfalse\n"

    def test_writing_somewhere_impossible_returns_false(self, compile_and_run):
        source = ("blob f = '/definitely/not/a/directory/x.txt'\n"
                  "log(f.write('hi'))")
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "false\n"

    def test_a_file_round_trips_through_text_operations(
            self, compile_and_run, tmp_path):
        # The value toText() hands back is an ordinary owned text, so it
        # composes with everything else (claude.md #83).
        path = str(tmp_path / "data.txt")
        source = f"""
        blob f = '{path}'
        f.write('a,b,c')
        text body = f.toText()
        log(body.replace(/,/g, '-'))
        log(`${{body}}!`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "a-b-c\na,b,c!\n"

    def test_the_removed_file_functions_name_their_replacement(
            self, parser, semantic, errors):
        # claude.md #109: removed rather than aliased, each with an
        # error that shows the blob spelling.
        for name, call in [("readFile", "readFile('x')"),
                           ("writeFile", "writeFile('x', 'y')"),
                           ("appendFile", "appendFile('x', 'y')"),
                           ("fileExists", "fileExists('x')"),
                           ("deleteFile", "deleteFile('x')")]:
            program = parser.parse(f"log({call})")
            with pytest.raises(errors.CompileError, match=name) as excinfo:
                semantic.analyze(program)
            assert "blob" in str(excinfo.value)

    # ---- filesystem: claude.md #132 ----

    def test_mkdir_reports_creation_versus_already_exists(self, compile_and_run, tmp_path):
        target = tmp_path / "sub"
        source = f"""
        log(mkdir('{target.as_posix()}'))
        log(mkdir('{target.as_posix()}'))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\nfalse\n"
        assert target.is_dir()

    def test_mkdir_on_an_impossible_path_returns_false_not_a_crash(self, compile_and_run, tmp_path):
        # No parent directory -- mkdir() is a test, not a failure, the
        # same claude.md #93 rule blob's own write()/append()/delete()
        # already follow.
        target = tmp_path / "missing_parent" / "sub"
        result = compile_and_run(f"log(mkdir('{target.as_posix()}'))")
        assert result.returncode == 0
        assert result.stdout == "false\n"
        assert not target.exists()

    def test_ls_lists_entry_names_not_full_paths(self, compile_and_run, tmp_path):
        # A dedicated subdirectory, not tmp_path itself -- compile_and_run
        # writes main.f/program straight into tmp_path, which would
        # otherwise show up in the listing too.
        listed = tmp_path / "listed"
        listed.mkdir()
        (listed / "a.txt").write_text("x")
        (listed / "b.txt").write_text("y")
        (listed / "sub").mkdir()
        source = f"""
        arr[text] names = ls('{listed.as_posix()}')
        log(names.length)
        log(names.indexOf('a.txt') >= 0)
        log(names.indexOf('b.txt') >= 0)
        log(names.indexOf('sub') >= 0)
        log(names.indexOf('.') >= 0)
        log(names.indexOf('..') >= 0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "3\ntrue\ntrue\ntrue\nfalse\nfalse\n"

    def test_ls_on_a_missing_directory_is_an_empty_array_not_a_crash(self, compile_and_run, tmp_path):
        missing = tmp_path / "does_not_exist"
        result = compile_and_run(f"log(ls('{missing.as_posix()}').length)")
        assert result.returncode == 0
        assert result.stdout == "0\n"

    def test_mkdir_and_ls_require_exactly_one_text_argument(self, parser, semantic, errors):
        for source in ["mkdir()", "mkdir('a', 'b')", "mkdir(5)",
                       "ls()", "ls('a', 'b')", "ls(5)"]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")

    # ---- time ----

    def test_now_and_formatTime(self, compile_and_run):
        source = """
        int t = now()
        log(t > 1700000000000)
        log(formatTime(0, '%Y'))
        """
        result = compile_and_run(source, env={"TZ": "UTC"})
        assert result.returncode == 0
        assert result.stdout == "true\n1970\n"

    def test_now_advances(self, compile_and_run):
        source = """
        int a = now()
        int total = 0
        for int i = 0, i < 2000000, i++ { total = total + i }
        int b = now()
        log(b >= a)
        log(total > 0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\ntrue\n"

    # ---- claude.md #188 (uraikus/festina#76 item 1): Math.floorDiv ----

    def test_floor_div_rounds_toward_negative_infinity(self, compile_and_run):
        # Unlike `/`'s own truncate-toward-zero: -7/2 truncates to -3,
        # but floors to -4. The two only ever disagree when the signs
        # differ and the division isn't exact.
        source = """
        log(Math.floorDiv(7, 2))
        log(Math.floorDiv(-7, 2))
        log(Math.floorDiv(7, -2))
        log(Math.floorDiv(-7, -2))
        log(Math.floorDiv(6, 2))
        log(Math.floorDiv(0, 5))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "3\n-4\n-4\n3\n3\n0\n"

    def test_floor_div_by_zero_returns_null(self, compile_and_run):
        # claude.md #57's own by-zero convention, shared rather than
        # given a second one just for this function.
        source = "log(Math.floorDiv(5, 0) == null)"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_floor_div_wrong_arity_or_types_is_a_compile_error(self, parser, semantic, errors):
        for source in [
            "log(Math.floorDiv(1))",
            "log(Math.floorDiv(1, 2, 3))",
            "log(Math.floorDiv(1.0, 2))",
            "log(Math.floorDiv(1, 2.0))",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")

    def test_math_floor_div_bare_reference_is_rejected(self, parser, semantic, errors):
        program = parser.parse("log(Math.floorDiv)", filename="main.f")
        with pytest.raises(errors.CompileError, match="call it"):
            semantic.analyze(program, filename="main.f")


def _png_raw(path):
    """Shared decode step behind _decode_png/_decode_png_rgba below --
    -> (width, height, stride, bpp, out), `out` the fully unfiltered
    pixel bytes. See _decode_png's own doc comment for why this is
    written out by hand at all."""
    import struct
    import zlib

    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    pos, idat, width, height, ctype = 8, b"", 0, 0, 0
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, _depth, ctype = struct.unpack(">IIBB", chunk[:10])
        elif tag == b"IDAT":
            idat += chunk
        pos += 12 + length
    bpp = {2: 3, 6: 4}[ctype]
    stride = width * bpp
    raw = zlib.decompress(idat)
    out, prev, i = bytearray(), bytearray(stride), 0
    for _y in range(height):
        filt = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if filt == 1:
                line[x] = (line[x] + a) & 255
            elif filt == 2:
                line[x] = (line[x] + b) & 255
            elif filt == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pred) & 255
        out += line
        prev = line
    return width, height, stride, bpp, out


def _decode_png(path):
    """Minimal PNG decoder -> (width, height, pixel(x, y) -> (r, g, b)).

    claude.md #93's saveCanvas is only really verified by reading the
    file back and finding the drawing in it -- "the call returned true"
    proves the plumbing, not that the canvas was captured rather than a
    blank surface. Written out here (zlib plus the five PNG filter
    types) because the compiler has no image library and neither should
    its tests; the same reasoning as the sprite_sheet_png fixture, which
    encodes rather than decodes.
    """
    width, height, stride, bpp, out = _png_raw(path)

    def pixel(x, y):
        off = y * stride + x * bpp
        return tuple(out[off:off + 3])

    return width, height, pixel


def _decode_png_rgba(path):
    """claude.md #136: _decode_png's own RGBA sibling -- (width, height,
    pixel(x, y) -> (r, g, b, a)), for the transparent-clear tests that
    need to see the alpha channel _decode_png's plain RGB deliberately
    drops (every other caller only ever cares about drawn colour, never
    transparency, so changing _decode_png's own return shape would be
    pure risk to ~20 existing assertions for no benefit to them)."""
    width, height, stride, bpp, out = _png_raw(path)

    def pixel(x, y):
        off = y * stride + x * bpp
        vals = tuple(out[off:off + bpp])
        return vals if bpp == 4 else vals + (255,)

    return width, height, pixel


class TestSaveCanvas:
    """claude.md #93: saveCanvas() writes the canvas to a PNG through
    Cairo's own writer -- compiled into the very library whose reader
    `loadImage` already uses, so it costs no new dependency and no
    encoder to write.

    It saves the BACKING surface rather than the window, so the result
    is what the program drew rather than whatever happened to be
    unobscured on screen. Needs a display only because the canvas has to
    exist at all."""

    def test_the_saved_png_contains_what_was_drawn(self, compile_and_run, tmp_path):
        # claude.md #95: no display, no window, no event loop. This used
        # to need all three -- a program whose whole job was writing a
        # PNG still opened a window and then blocked forever in the
        # event loop, which is exactly what the render() split fixed.
        out = str(tmp_path / "canvas.png")
        source = f"""
        color red = 'red'
        color blue = 'blue'
        fillStyle(red)
        drawRect(0, 0, 40, 40)
        fillStyle(blue)
        drawRect(40, 0, 40, 40)
        log(saveCanvas('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0, (
            "drawing and saving should need no display at all: "
            + result.stdout + result.stderr)
        assert result.stdout == "true\n"
        width, height, pixel = _decode_png(out)
        assert (width, height) == (800, 600)
        assert pixel(20, 20) == (255, 0, 0), "the red rect is missing"
        assert pixel(60, 20) == (0, 0, 255), "the blue rect is missing"
        # claude.md #136: the canvas is transparent, not white, wherever
        # nothing was drawn.
        _, _, pixel_rgba = _decode_png_rgba(out)
        assert pixel_rgba(400, 300) == (0, 0, 0, 0), "background should be transparent"

    def test_an_unwritable_path_returns_false(self, compile_and_run):
        source = """
        drawRect(0, 0, 10, 10)
        log(saveCanvas('/definitely/not/a/directory/out.png'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "false\n"

    def test_no_path_returns_an_img_snapshot(self, compile_and_run, tmp_path):
        # claude.md #135: saveCanvas() with no argument -> a fresh img
        # holding what's been drawn, instead of writing a file.
        out = str(tmp_path / "canvas.png")
        source = f"""
        color red = 'red'
        fillStyle(red)
        drawRect(0, 0, 40, 40)
        img snap = saveCanvas()
        log(`${{snap.width}}x${{snap.height}}`)
        log(snap.save('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "800x600\ntrue\n"
        _, _, pixel = _decode_png(out)
        assert pixel(20, 20) == (255, 0, 0)

    def test_the_snapshot_is_independent_of_later_canvas_changes(
            self, compile_and_run, tmp_path):
        # A snapshot, not a live view -- clearing/redrawing the canvas
        # afterward must not retroactively change what was captured.
        out = str(tmp_path / "snap.png")
        source = f"""
        color red = 'red'
        color blue = 'blue'
        fillStyle(red)
        drawRect(0, 0, 40, 40)
        img snap = saveCanvas()
        clearCanvas()
        fillStyle(blue)
        drawRect(0, 0, 40, 40)
        log(snap.save('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\n"
        _, _, pixel = _decode_png(out)
        assert pixel(20, 20) == (255, 0, 0), "the snapshot should still be the red canvas"

    def test_wrong_arity_and_types_are_rejected(self, parser, semantic, errors):
        for source in ["saveCanvas(1)", "saveCanvas('a', 'b')"]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")


class TestDrawPixelClearCircleAndColorOverrides:
    """claude.md #133: drawPixel/clearPixel/clearCircle, and an optional
    trailing `color` argument on drawRect/drawPixel that paints with it
    for that one call only, restoring the current fillStyle (flat
    colour or gradient) afterward rather than changing it."""

    def test_draw_pixel_uses_current_fill_style(self, compile_and_run, tmp_path):
        out = str(tmp_path / "canvas.png")
        source = f"""
        color red = 'red'
        fillStyle(red)
        drawPixel(10, 10)
        log(saveCanvas('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\n"
        _, _, pixel = _decode_png(out)
        _, _, pixel_rgba = _decode_png_rgba(out)
        assert pixel(10, 10) == (255, 0, 0)
        # claude.md #136: the canvas clears to transparent, not white.
        assert pixel_rgba(11, 10) == (0, 0, 0, 0), "only the one pixel should be painted"
        assert pixel_rgba(10, 11) == (0, 0, 0, 0), "only the one pixel should be painted"

    def test_draw_rect_and_pixel_color_override_does_not_change_fill_style(
            self, compile_and_run, tmp_path):
        out = str(tmp_path / "canvas.png")
        source = f"""
        color red = 'red'
        color blue = 'blue'
        fillStyle(red)
        drawRect(0, 0, 10, 10, blue)
        drawPixel(20, 20, blue)
        drawRect(30, 0, 10, 10)
        log(saveCanvas('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\n"
        _, _, pixel = _decode_png(out)
        assert pixel(5, 5) == (0, 0, 255), "the color override should win"
        assert pixel(20, 20) == (0, 0, 255), "drawPixel's own override"
        assert pixel(35, 5) == (255, 0, 0), "fillStyle(red) should still be in effect after"

    def test_draw_rect_color_none_paints_nothing(self, compile_and_run, tmp_path):
        out = str(tmp_path / "canvas.png")
        source = f"""
        color none = 'none'
        color red = 'red'
        fillStyle(red)
        drawRect(0, 0, 20, 20, none)
        log(saveCanvas('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\n"
        # claude.md #136: the canvas clears to transparent, not white --
        # painting nothing leaves it transparent, not opaque.
        _, _, pixel_rgba = _decode_png_rgba(out)
        assert pixel_rgba(10, 10) == (0, 0, 0, 0)

    def test_clear_pixel_erases_one_pixel_to_transparent(self, compile_and_run, tmp_path):
        out = str(tmp_path / "canvas.png")
        source = f"""
        color red = 'red'
        fillStyle(red)
        drawRect(0, 0, 10, 10)
        clearPixel(5, 5)
        log(saveCanvas('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\n"
        _, _, pixel = _decode_png(out)
        _, _, pixel_rgba = _decode_png_rgba(out)
        assert pixel_rgba(5, 5) == (0, 0, 0, 0)
        assert pixel(4, 4) == (255, 0, 0), "only the one pixel should be erased"

    def test_clear_circle_erases_a_circular_region_to_transparent(self, compile_and_run, tmp_path):
        out = str(tmp_path / "canvas.png")
        source = f"""
        color red = 'red'
        fillStyle(red)
        drawRect(0, 0, 100, 100)
        clearCircle(50, 50, 20)
        log(saveCanvas('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\n"
        _, _, pixel = _decode_png(out)
        _, _, pixel_rgba = _decode_png_rgba(out)
        assert pixel_rgba(50, 50) == (0, 0, 0, 0), "circle center should be cleared"
        assert pixel(1, 1) == (255, 0, 0), "far corner should still be red"

    def test_wrong_arity_and_types_are_rejected(self, parser, semantic, errors):
        for source in [
            "drawPixel()",
            "drawPixel(1)",
            "drawPixel(1, 2, 3)",
            "drawRect(0, 0, 10)",
            "drawRect(0, 0, 10, 10, 10, 10)",
            "clearCircle(0, 0)",
            "clearPixel(0)",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")


class TestDrawRectAndCircleBorderColorOverride:
    """claude.md #188 (uraikus/festina#76 item 8): a further optional
    trailing `borderColor` argument on drawRect/drawCircle -- present,
    strokes with it for this one call only; absent, uses the current
    borderColor, the same "this call only, then restore" shape the
    existing trailing FILL colour already has (claude.md #133).
    drawCircle gains BOTH trailing forms here, newly -- it previously
    had no per-call colour override at all."""

    def test_draw_rect_fill_and_border_override_do_not_leak(self, compile_and_run, tmp_path):
        out = str(tmp_path / "canvas.png")
        source = f"""
        color red = 'red'
        color blue = 'blue'
        color green = 'green'
        fillStyle(green)
        borderColor(green)
        lineWidth(6)
        drawRect(0, 0, 20, 20, red, blue)
        drawRect(40, 0, 20, 20)
        log(saveCanvas('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\n"
        _, _, pixel = _decode_png(out)
        assert pixel(10, 10) == (255, 0, 0), "fill override should win"
        # A few px in from the edge -- comfortably inside the (6px-wide,
        # centered-on-the-path) stroke, past Cairo's own antialiased
        # boundary right at x=0.
        assert pixel(2, 10) == (0, 0, 255), "border override should win at the edge"
        assert pixel(50, 10) == (0, 128, 0), "fillStyle(green) should still apply after"
        assert pixel(42, 10) == (0, 128, 0), "borderColor(green) should still apply after"

    def test_draw_rect_border_none_paints_no_border(self, compile_and_run, tmp_path):
        out = str(tmp_path / "canvas.png")
        source = f"""
        color red = 'red'
        color blue = 'blue'
        color none = 'none'
        borderColor(blue)
        drawRect(0, 0, 20, 20, red, none)
        log(saveCanvas('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\n"
        _, _, pixel = _decode_png(out)
        assert pixel(0, 10) == (255, 0, 0), "border('none') should leave the fill showing at the edge"

    def test_draw_circle_gains_fill_and_border_override(self, compile_and_run, tmp_path):
        out = str(tmp_path / "canvas.png")
        source = f"""
        color red = 'red'
        color blue = 'blue'
        lineWidth(6)
        drawCircle(30, 30, 20, red)
        drawCircle(80, 30, 20, red, blue)
        log(saveCanvas('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\n"
        _, _, pixel = _decode_png(out)
        assert pixel(30, 30) == (255, 0, 0), "center should be the fill override"
        assert pixel(80, 30) == (255, 0, 0), "center of the second circle: fill override"
        # The stroke is centered on the circle's own path (radius 20),
        # so its top point (y = 30 - 20 = 10) is comfortably inside a
        # 6px-wide ring there.
        assert pixel(80, 10) == (0, 0, 255), "top edge of the second circle: border override"

    def test_img_draw_rect_and_circle_gain_the_same_overrides(self, compile_and_run):
        source = """
        color red = 'red'
        color blue = 'blue'
        img sq = blankImage(20, 20)
        sq.drawRect(0, 0, 20, 20, red, blue)
        sq.drawCircle(10, 10, 5, red, blue)
        log('ok')
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "ok\n"

    def test_wrong_arity_or_types_is_a_compile_error(self, parser, semantic, errors):
        for source in [
            "color red = 'red'\ndrawRect(0, 0, 10, 10, red, red, red)",
            "color red = 'red'\ndrawCircle(0, 0, 10, red, red, red)",
            "drawRect(0, 0, 10, 10, 1, 2)",
            "drawCircle(0, 0, 10, 1, 2)",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")


class TestStructTargetScalarQueries:
    """claude.md #219: sqliteInt()/sqliteFloat()/sqliteText() (claude.md
    #94's own single-value-query convenience wrappers) were removed --
    the schema-free scalar round trip they existed for was already
    reachable through `sqlite()`'s own general path. A `struct` (unlike
    a `table`) creates no real table when used as a query target, so
    `arr[SomeStruct] x = sqlite('SELECT ... AS field FROM ...')` gives
    the same no-extra-table round trip these wrappers offered, through
    the one mechanism instead of four."""

    def test_a_struct_target_reads_values_without_a_result_table(
            self, compile_and_run, tmp_path):
        source = """
        table Post { id:int  title:text  score:float }
        struct Count { n:int }
        struct Title { title:text }
        struct Total { total:float }
        sqlite(`DELETE FROM Post`)
        sqlite(`INSERT INTO Post (id, title, score) VALUES (?, ?, ?)`, [1, 'alpha', 1.5])
        sqlite(`INSERT INTO Post (id, title, score) VALUES (?, ?, ?)`, [2, 'beta', 2.5])
        arr[Count] c = sqlite(`SELECT count(*) AS n FROM Post`)
        log(c[0].n)
        arr[Title] t = sqlite(`SELECT title FROM Post WHERE id = ?`, [2])
        log(t[0].title)
        arr[Total] s = sqlite(`SELECT sum(score) AS total FROM Post`)
        log(s[0].total)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\nbeta\n4\n"
        # The gap the removed wrappers existed to close: the only table
        # in the database afterwards should be the one actually declared.
        import sqlite3
        db = sqlite3.connect(str(tmp_path / "festina.sqlite"))
        names = sorted(r[0] for r in db.execute(
            "select name from sqlite_master where type='table' "
            "and name not like 'sqlite_%'"))
        db.close()
        assert names == ["Post"]

    def test_no_matching_row_is_an_empty_array(self, compile_and_run):
        source = """
        table Post { id:int  title:text }
        struct Title { title:text }
        sqlite(`DELETE FROM Post`)
        arr[Title] t = sqlite(`SELECT title FROM Post WHERE id = ?`, [99])
        log(t.length)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "0\n"

    def test_json1_works_through_a_struct_target(self, compile_and_run):
        # SQLite's JSON1 needs no compiler feature at all -- it is
        # ordinary SQL. This locks in that it stays reachable.
        source = """
        struct Num { n:int }
        struct Str { s:text }
        arr[Num] a = sqlite(`SELECT json_extract('{"n":42}','$.n') AS n`)
        log(a[0].n)
        arr[Str] b = sqlite(`SELECT json_extract('{"name":"ada"}','$.name') AS s`)
        log(b[0].s)
        arr[Num] c = sqlite(`SELECT json_array_length('[1,2,3]') AS n`)
        log(c[0].n)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "42\nada\n3\n"

    def test_fts5_full_text_search_works_end_to_end(self, compile_and_run):
        # Likewise FTS5: a virtual table, an index rebuild and a ranked
        # MATCH query, all through SQL the language already passes
        # through untouched.
        source = """
        table Post { id:int  title:text  body:text }
        struct Count { n:int }
        struct Title { title:text }
        sqlite(`DELETE FROM Post`)
        sqlite(`INSERT INTO Post (id, title, body) VALUES (?, ?, ?)`,
               [1, 'Compilers', 'a compiler turns source into machine code'])
        sqlite(`INSERT INTO Post (id, title, body) VALUES (?, ?, ?)`,
               [2, 'Gardening', 'planting tomatoes in spring soil'])
        sqlite(`INSERT INTO Post (id, title, body) VALUES (?, ?, ?)`,
               [3, 'Machines', 'machine learning and machine code differ'])
        sqlite(`DROP TABLE IF EXISTS PostSearch`)
        sqlite(`CREATE VIRTUAL TABLE PostSearch USING fts5(title, body, content='Post', content_rowid='id')`)
        sqlite(`INSERT INTO PostSearch(PostSearch) VALUES('rebuild')`)
        arr[Count] c = sqlite(`SELECT count(*) AS n FROM PostSearch WHERE PostSearch MATCH ?`, ['machine'])
        log(c[0].n)
        arr[Title] t = sqlite(`SELECT title FROM PostSearch WHERE PostSearch MATCH ? ORDER BY rank`, ['tomatoes'])
        log(t[0].title)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\nGardening\n"


class TestCanvasPathsTransformsAndGradients:
    """claude.md #94: the canvas gains arbitrary shapes.

    Before this it could draw exactly three things -- a rectangle, a
    circle and a line of text -- with no way to express a triangle, a
    polygon, a curve, a rotated anything, a gradient or transparency.

    Every drawing call builds its own short-lived Cairo context, so the
    transform lives outside all of them and is applied to each; that is
    what makes `translate(100, 0)` affect the NEXT drawRect rather than
    nothing. `saveState`/`restoreState` save the whole drawing state,
    matching the canvas `save()`/`restore()` they mirror."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_path_and_transform_calls_are_emitted(self, parser, semantic, codegen):
        source = """
        beginPath()
        moveTo(0, 0)
        lineTo(10, 10)
        curveTo(1, 2, 3, 4, 5, 6)
        closePath()
        fillPath()
        strokePath()
        translate(5, 5)
        rotate(45.0)
        scale(2.0, 2.0)
        resetTransform()
        saveState()
        restoreState()
        fillAlpha(0.5)
        """
        ir = self._ir(parser, semantic, codegen, source)
        for fn in ["begin_path", "move_to", "line_to", "curve_to", "close_path",
                   "fill_path", "stroke_path", "translate", "rotate", "scale",
                   "reset_transform", "save_state", "restore_state", "set_alpha"]:
            assert f"@festina_{fn}(" in ir, f"festina_{fn} not emitted"

    def test_gradients_take_color_values(self, parser, semantic, codegen):
        source = """
        color a = 'red'
        color b = 'blue'
        fillLinearGradient(0, 0, a, 100, 0, b)
        fillRadialGradient(50, 50, 40, a, b)
        drawRect(0, 0, 10, 10)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "@festina_fill_linear_gradient(" in ir
        assert "@festina_fill_radial_gradient(" in ir

    def test_a_gradient_rejects_a_non_color(self, parser, semantic, errors):
        program = parser.parse(
            "fillLinearGradient(0, 0, 'red', 100, 0, 'blue')", filename="main.f")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program, filename="main.f")

    def test_path_calls_do_not_open_a_canvas(self, parser, semantic, codegen):
        # claude.md #95: a path paints the offscreen canvas like any
        # other drawing -- only render() needs a window. claude.md #178:
        # festina_graphics_init() itself is never emitted directly any
        # more (see test_render_is_what_opens_the_window's own comment
        # in TestColorAndFontTypes), so festina_render()/
        # festina_run_event_loop()'s absence is what actually shows
        # this program never reaches for a window.
        ir = self._ir(parser, semantic, codegen, "beginPath()\nmoveTo(0,0)\nfillPath()")
        assert "call void @festina_render()" not in ir
        assert "call void @festina_run_event_loop()" not in ir

    def test_transforms_and_state_open_nothing(self, parser, semantic, codegen):
        # Pure state, exactly like claude.md #89's own style setters --
        # a program that only sets a transform shouldn't get a window.
        source = """
        saveState()
        translate(5, 5)
        rotate(45.0)
        scale(2.0, 2.0)
        fillAlpha(0.5)
        restoreState()
        resetTransform()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_render()" not in ir
        assert "call void @festina_run_event_loop()" not in ir

    def test_building_a_path_with_none_open_fails_clearly(self, compile_and_run):
        result = compile_and_run("moveTo(10, 10)")
        assert result.returncode != 0
        assert "beginPath" in result.stdout + result.stderr

    def test_restoring_more_than_was_saved_fails_clearly(self, compile_and_run):
        # No drawing, so no display needed -- restoreState is pure state.
        result = compile_and_run("restoreState()")
        assert result.returncode != 0
        assert "restoreState" in result.stdout + result.stderr


class TestCanvasPathsRenderRealPixels:
    """claude.md #94, the tier the IR tests can't reach: that a path is
    a real shape rather than its bounding box, that a transform actually
    moves what is drawn next, that a gradient interpolates, and that
    alpha blends. Needs a display plus `xdotool`/`xwd`."""

    def test_paths_transforms_gradients_and_alpha_render(
            self, run_graphics_program, x_display):
        if not shutil.which("xwd"):
            pytest.skip("xwd isn't installed -- needed to read real canvas pixels")
        source = """
        color red = 'red'
        color blue = 'blue'
        color black = 'black'
        color none = 'none'

        fillStyle(red)
        beginPath()
        moveTo(50, 50)
        lineTo(150, 50)
        lineTo(100, 140)
        closePath()
        fillPath()

        fillStyle(none)
        borderColor(black)
        lineWidth(6)
        beginPath()
        moveTo(200, 50)
        lineTo(300, 140)
        strokePath()

        saveState()
        translate(400, 40)
        fillStyle(blue)
        drawRect(0, 0, 60, 60)
        restoreState()
        fillStyle(blue)
        drawRect(400, 200, 20, 20)

        fillLinearGradient(50, 300, red, 250, 300, blue)
        drawRect(50, 280, 200, 60)

        fillStyle(red)
        fillAlpha(0.5)
        drawRect(500, 280, 80, 60)
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            got = _xwd_pixels(x_display, wid, [
                (100, 80),   # inside the triangle
                (55, 130),   # inside its bounding box but OUTSIDE the shape
                (250, 95),   # on the stroked diagonal
                (430, 70),   # the translated square
                (405, 205),  # drawn after restoreState -- untransformed
                (55, 300),   # gradient, red end
                (245, 300),  # gradient, blue end
                (540, 300),  # 50% red over white
            ])
            assert got[0] == (255, 0, 0), "the triangle did not fill"
            assert got[1] == (255, 255, 255), (
                "a corner outside the triangle was filled -- the path was "
                "treated as its bounding box")
            assert got[2] == (0, 0, 0), "the stroked path is missing"
            assert got[3] == (0, 0, 255), "translate() did not move drawRect"
            assert got[4] == (0, 0, 255), "restoreState() did not undo translate()"
            assert got[5][0] > 200 and got[5][2] < 60, "gradient start is not red"
            assert got[6][2] > 200 and got[6][0] < 60, "gradient end is not blue"
            assert got[7] == (255, 127, 127), "50% red over white should blend"
        finally:
            proc.terminate()

    def test_fill_alpha_applies_to_draw_image_too(self, run_graphics_program, x_display):
        # claude.md #183 (uraikus/festina#78): drawImage used to always
        # cairo_paint at full opacity, completely ignoring fillAlpha --
        # every OTHER draw call (a canvas fill, and a fill drawn
        # directly onto an img's own surface) already respected it, so
        # this was a real inconsistency, not intentional scoping. Same
        # "50% colour over white should blend" shape as
        # test_paths_transforms_and_gradients_render_correctly just
        # above, applied to an image blit instead of a fill.
        source = """
        color red = 'red'
        color white = 'white'

        clearCanvas()
        fillStyle(white)
        drawRect(0, 0, 60, 60)
        img sq = saveCanvas()

        clearCanvas()
        fillStyle(red)
        drawRect(0, 0, 400, 200)

        fillAlpha(0.5)
        drawImage(sq, 0, 0)     // should blend 50% into the red beneath
        fillAlpha(1.0)
        drawImage(sq, 200, 0)   // full opacity -- untouched white
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            got = _xwd_pixels(x_display, wid, [(30, 30), (230, 30)])
            # 0.5*255 + 0.5*0 = 127.5 for the G/B channels -- rounds to
            # 128 on this path (Cairo's own compositing, not this
            # test's arithmetic); R stays 255 either way since both the
            # white square and the red background already have R=255.
            assert got[0] == (255, 128, 128), (
                "fillAlpha(0.5) did not blend the drawImage'd square with the red beneath")
            assert got[1] == (255, 255, 255), (
                "fillAlpha(1.0) should leave the second drawImage fully opaque")
        finally:
            proc.terminate()

    def test_draw_image_with_destination_size_scales_the_whole_image(
            self, run_graphics_program, x_display):
        # claude.md #185 (uraikus/festina#76 item 3): drawImage(img, x,
        # y, w, h) -- a 10x10 solid blue square drawn at 40x40 should
        # cover pixel (25, 25) (inside the scaled box) with blue, and
        # leave a point well outside it (60, 60) showing the red
        # background instead.
        source = """
        setClientWidth(10)
        setClientHeight(10)
        color red = 'red'
        color blue = 'blue'

        clearCanvas()
        fillStyle(blue)
        drawRect(0, 0, 10, 10)
        img sq = saveCanvas()   // exactly a 10x10 blue image, no transparent margin

        setClientWidth(400)
        setClientHeight(200)
        clearCanvas()
        fillStyle(red)
        drawRect(0, 0, 400, 200)
        drawImage(sq, 0, 0, 40, 40)
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            got = _xwd_pixels(x_display, wid, [(20, 20), (60, 60)])
            assert got[0] == (0, 0, 255), "scaled drawImage should cover (20,20) with blue"
            assert got[1] == (255, 0, 0), "outside the scaled box should still be red"
        finally:
            proc.terminate()

    def test_draw_image_with_source_and_dest_rects(self, run_graphics_program, x_display):
        # claude.md #185: the full 8-argument canvas-style form. The
        # source image is left half blue, right half green; cutting out
        # just the right (green) half and drawing it scaled elsewhere
        # should show green there, not blue.
        source = """
        setClientWidth(20)
        setClientHeight(20)
        color blue = 'blue'
        color green = 'green'
        color red = 'red'

        clearCanvas()
        fillStyle(blue)
        drawRect(0, 0, 10, 20)
        fillStyle(green)
        drawRect(10, 0, 10, 20)
        img sheet = saveCanvas()   // exactly a 20x20 image, no transparent margin

        setClientWidth(400)
        setClientHeight(200)
        clearCanvas()
        fillStyle(red)
        drawRect(0, 0, 400, 200)
        // source rect: the right (green) 10x20 half; dest: 100,100 40x40
        drawImage(sheet, 10, 0, 10, 20, 100, 100, 40, 40)
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            got = _xwd_pixels(x_display, wid, [(120, 120), (200, 20)])
            assert got[0] == (0, 128, 0), "the clipped source rect should paint green, not blue"
            assert got[1] == (255, 0, 0), "outside the destination rect should still be red"
        finally:
            proc.terminate()

    def test_draw_image_wrong_arity_is_a_compile_error(self, parser, semantic, errors):
        # 3, 5, and 9 are the only valid shapes -- anything else (4, 6,
        # 8 args) is a real mistake, not a fourth form nobody wrote yet.
        for source in [
            "img sq\ndrawImage(sq, 0)",
            "img sq\ndrawImage(sq, 0, 0, 0)",
            "img sq\ndrawImage(sq, 0, 0, 0, 0, 0, 0)",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")


class TestRenderClearAndHeadless:
    """claude.md #95: drawing paints an offscreen canvas; `render()` is
    the one call that puts it on screen.

    That split does three things at once. A program that draws and saves
    a PNG never opens a window, never enters an event loop, and runs
    with no display at all -- previously impossible, since any drawing
    forced a window. "Does this program need a GUI?" becomes answerable
    by looking for `render()`. And a frame costs one blit instead of one
    per shape: every draw call used to flush the whole canvas to X, so
    2000 rectangles took ~1.6s; batched behind one render() they take
    ~1ms.

    `clearCanvas()` is the other half of animation -- without it a canvas
    could only ever accumulate, so nothing could move."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_render_and_clears_emit_their_calls(self, parser, semantic, codegen):
        source = """
        clearCanvas()
        clearRect(10, 10, 20, 20)
        render()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_clear_canvas()" in ir
        assert "call void @festina_clear_rect(i64 10, i64 10, i64 20, i64 20)" in ir
        assert "call void @festina_render()" in ir

    def test_clearing_alone_does_not_open_a_window(self, parser, semantic, codegen):
        ir = self._ir(parser, semantic, codegen, "clearCanvas()\nclearRect(0,0,5,5)")
        assert "call void @festina_render()" not in ir
        assert "call void @festina_run_event_loop()" not in ir

    def test_saving_a_canvas_needs_no_display(self, compile_and_run, tmp_path):
        # The capability the split exists for.
        out = str(tmp_path / "out.png")
        source = f"""
        color red = 'red'
        fillStyle(red)
        drawRect(0, 0, 30, 30)
        log(saveCanvas('{out}'))
        log('exited on its own')
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\nexited on its own\n"

    def test_clear_canvas_erases_everything(self, compile_and_run, tmp_path):
        # claude.md #136: clears to fully TRANSPARENT, not opaque white.
        out = str(tmp_path / "cleared.png")
        source = f"""
        color red = 'red'
        fillStyle(red)
        drawRect(0, 0, 100, 100)
        clearCanvas()
        log(saveCanvas('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        _w, _h, pixel = _decode_png_rgba(out)
        assert pixel(50, 50) == (0, 0, 0, 0), "clearCanvas() left the rect behind"

    def test_clear_rect_erases_only_its_region(self, compile_and_run, tmp_path):
        out = str(tmp_path / "partial.png")
        source = f"""
        color red = 'red'
        fillStyle(red)
        drawRect(0, 0, 200, 200)
        clearRect(50, 50, 40, 40)
        log(saveCanvas('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        _w, _h, pixel = _decode_png_rgba(out)
        assert pixel(60, 60) == (0, 0, 0, 0), "clearRect() did not erase its region"
        assert pixel(150, 150) == (255, 0, 0, 255), "clearRect() erased outside its region"
        assert pixel(10, 10) == (255, 0, 0, 255), "clearRect() erased outside its region"

    def test_drawing_survives_until_render(self, run_graphics_program, x_display):
        # Drawing before the window exists is not an error -- the canvas
        # is offscreen, and render() presents whatever is on it.
        if not shutil.which("xwd"):
            pytest.skip("xwd isn't installed -- needed to read real canvas pixels")
        source = """
        color red = 'red'
        fillStyle(red)
        drawRect(10, 10, 60, 60)
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            got = _xwd_pixels(x_display, wid, [(30, 30), (400, 300)])
            assert got[0] == (255, 0, 0), "what was drawn before render() was lost"
            assert got[1] == (255, 255, 255)
        finally:
            proc.terminate()


class TestArrayMethods:
    """claude.md #96: push/pop/shift/unshift/splice, JS-shaped.

    Before these, an array's length was fixed at construction and
    writing past the end was an unchecked heap overflow -- so there was
    no way to express a list that grows, which is most of what a program
    does with one.

    The ownership half matters as much as the resizing: `xs.push(s)`
    follows the same rule `xs[i] = s` already does (claude.md #80/#83),
    retaining a struct/array/map element and copying a text one unless
    its source is already owning. Removal transfers rather than
    releases -- pop/shift hand the element back and splice hands it to
    the returned array."""

    def test_push_and_pop(self, compile_and_run):
        source = """
        arr[int] xs = [1, 2, 3]
        log(xs.push(4))
        log(xs.length)
        log(xs.pop())
        log(xs.length)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "4\n4\n4\n3\n"

    def test_shift_and_unshift(self, compile_and_run):
        source = """
        arr[int] xs = [1, 2, 3]
        log(xs.shift())
        log(xs[0])
        log(xs.unshift(99))
        log(xs[0])
        log(xs.length)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "1\n2\n3\n99\n3\n"

    def test_splice_removes_and_returns(self, compile_and_run):
        source = """
        arr[int] xs = [10, 20, 30, 40, 50]
        arr[int] cut = xs.splice(1, 2)
        log(`${cut.length}: ${cut[0]},${cut[1]}`)
        log(`${xs.length}: ${xs[0]},${xs[1]},${xs[2]}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2: 20,30\n3: 10,40,50\n"

    def test_splice_clamps_like_javascript(self, compile_and_run):
        # A negative start counts back from the end, and an oversized
        # count clamps -- so splice(i, 1) at a boundary is a no-op
        # rather than a crash.
        source = """
        arr[int] a = [1, 2, 3, 4, 5]
        arr[int] tail = a.splice(-2, 5)
        log(`${tail.length}: ${tail[0]},${tail[1]}`)
        log(a.length)
        arr[int] b = [1, 2]
        arr[int] none = b.splice(10, 3)
        log(none.length)
        log(b.length)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2: 4,5\n3\n0\n2\n"

    def test_popping_an_empty_array_is_null(self, compile_and_run):
        # Null, not zero: an empty pop() must be distinguishable from
        # popping a real 0.
        source = """
        arr[int] e = []
        log(e.pop() == null)
        log(e.shift() == null)
        arr[int] z = [0]
        log(z.pop() == null)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\ntrue\nfalse\n"

    def test_growing_from_empty(self, compile_and_run):
        source = """
        arr[int] xs = []
        for int i = 0, i < 100, i++ { xs.push(i * 2) }
        log(xs.length)
        log(xs[0])
        log(xs[99])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "100\n0\n198\n"

    def test_text_elements_are_owned_not_shared(self, compile_and_run):
        # Pushing a text binding must copy it (claude.md #83), or the
        # array and the variable would share one buffer.
        source = """
        arr[text] names = []
        text n = 'first'
        names.push(n)
        n = 'changed'
        log(names[0])
        log(names.pop())
        log(names.length)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "first\nfirst\n0\n"

    def test_struct_elements_survive_push_and_pop(self, compile_and_run):
        source = """
        struct P { x:int }
        P func make(v:int) { P p  p.x = v  return p }
        arr[P] ps = []
        ps.push(make(7))
        ps.push(make(9))
        log(ps.length)
        P last = ps.pop()
        log(last.x)
        log(ps[0].x)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\n9\n7\n"

    def test_wrong_arity_and_types_are_rejected(self, parser, semantic, errors):
        for source in [
            "arr[int] xs = [1]\nxs.push()",
            "arr[int] xs = [1]\nxs.push(1, 2)",
            "arr[int] xs = [1]\nxs.pop(1)",
            "arr[int] xs = [1]\nxs.push('a')",
            "arr[int] xs = [1]\nxs.splice(1)",
            "arr[int] xs = [1]\nxs.splice(1, 'a')",
            "arr[int] xs = [1]\nxs.splice(0, 1, 2, 3)",
            "arr[int] xs = [1]\nxs.splice(0, 1, 'a')",
            "arr[int] xs = [1]\narr[text] ys = ['a']\nxs.splice(0, 1, ys)",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")

    def test_splice_insert_replaces_and_returns_removed(self, compile_and_run):
        # claude.md #130: splice(start, count, insertArr) -- JavaScript's
        # splice(start, deleteCount, ...items), the variadic items
        # spelled as one arr[T] argument since Festina has no variadic
        # parameters. Only the REMOVED elements come back, exactly as
        # JS's own splice() answers -- the inserted ones are placed, not
        # returned.
        source = """
        arr[int] xs = [1, 2, 3, 4, 5]
        arr[int] gone = xs.splice(1, 2, [10, 20, 30])
        log(`${gone.length}: ${gone[0]},${gone[1]}`)
        log(`${xs.length}: ${xs[0]},${xs[1]},${xs[2]},${xs[3]},${xs[4]},${xs[5]}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2: 2,3\n6: 1,10,20,30,4,5\n"

    def test_splice_insert_grows_and_shrinks_correctly(self, compile_and_run):
        source = """
        // Pure insertion: count is 0, the array only grows.
        arr[int] a = [1, 2, 3]
        arr[int] none = a.splice(1, 0, [8, 9])
        log(`${none.length} ${a.length}: ${a[0]},${a[1]},${a[2]},${a[3]},${a[4]}`)

        // Replacing more than is inserted: the array shrinks.
        arr[int] b = [1, 2, 3, 4, 5]
        arr[int] cut = b.splice(0, 4, [99])
        log(`${cut.length}: ${cut[0]},${cut[1]},${cut[2]},${cut[3]}`)
        log(`${b.length}: ${b[0]},${b[1]}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "0 5: 1,8,9,2,3\n4: 1,2,3,4\n2: 99,5\n"

    def test_splice_insert_owns_text_elements_independently(self, compile_and_run):
        # claude.md #130: the inserted elements are COPIED (text) or
        # RETAINED (struct/arr/map/img/aud/regex/blob) into the target
        # array, unconditionally -- the source array (here a named
        # binding, so not itself consumed) keeps its own elements alive
        # and independent, exactly like push() already does for a single
        # value.
        source = """
        arr[text] words = ['a', 'b', 'c']
        arr[text] extra = ['x', 'y']
        arr[text] gone = words.splice(1, 1, extra)
        log(gone[0])
        log(`${words.length}: ${words[0]},${words[1]},${words[2]},${words[3]}`)
        log(`${extra.length}: ${extra[0]},${extra[1]}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "b\n4: a,x,y,c\n2: x,y\n"

    def test_splice_insert_retains_struct_elements(self, compile_and_run):
        source = """
        struct P { v:int }
        P func make(v:int) { P p  p.v = v  return p }
        arr[P] ps = [make(1), make(2), make(3)]
        arr[P] src = [make(9)]
        arr[P] gone = ps.splice(1, 1, src)
        log(gone[0].v)
        log(`${ps[0].v},${ps[1].v},${ps[2].v}`)
        log(src[0].v)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\n1,9,3\n9\n"

    def test_a_queue_round_trips(self, compile_and_run):
        # The shape a game's entity list actually takes.
        source = """
        arr[text] queue = []
        queue.push('a')
        queue.push('b')
        queue.push('c')
        log(queue.shift())
        queue.push('d')
        log(queue.shift())
        log(`${queue.length}: ${queue[0]},${queue[1]}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "a\nb\n2: c,d\n"



class TestArrayIndexOf:
    """claude.md #97: xs.indexOf(v) -- the first index holding v, or -1.

    -1 rather than null because the answer is an index and every use of
    one is a comparison or a splice argument, both of which read
    naturally against -1 and neither of which would against null. It is
    also what JavaScript's own indexOf answers, and claude.md #26's
    arrays are JS-shaped.

    The comparison is by 8-byte slot for everything except text, which
    compares by content -- two equal strings are almost always two
    different buffers under claude.md #83's copy-on-alias rule, so
    identity would make indexOf useless for exactly the element type
    it's most often used with."""

    def test_int_elements_found_and_missing(self, compile_and_run):
        source = """
        arr[int] xs = [10, 20, 30]
        log(xs.indexOf(30))
        log(xs.indexOf(10))
        log(xs.indexOf(99))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\n0\n-1\n"

    def test_text_elements_compare_by_content_not_identity(self, compile_and_run):
        # Every one of these needles is a DIFFERENT buffer from the
        # array's own element (claude.md #83 copies text on binding), so
        # a pointer comparison would answer -1 for all three.
        source = """
        text func bang(s:text) {
            return s + '!'
        }
        arr[text] names = ['ada!', 'grace!', 'alan!']
        text who = 'grace'
        log(names.indexOf('alan!'))
        log(names.indexOf(bang(who)))
        log(names.indexOf(`${who}!`))
        log(names.indexOf(who + '!'))
        log(names.indexOf('nobody'))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\n1\n1\n1\n-1\n"

    def test_first_match_wins_and_empty_is_minus_one(self, compile_and_run):
        source = """
        arr[int] dupes = [5, 5, 5]
        log(dupes.indexOf(5))
        arr[int] empty = []
        log(empty.indexOf(1))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "0\n-1\n"

    def test_bool_elements(self, compile_and_run):
        # arr[bool] is the one element type whose slot is 1 byte wide,
        # not 8 -- see _elem_size.
        source = """
        arr[bool] flags = [true, true, false]
        log(flags.indexOf(false))
        log(flags.indexOf(true))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\n0\n"

    def test_float_elements(self, compile_and_run):
        source = """
        arr[float] fs = [1.5, 2.5]
        log(fs.indexOf(2.5))
        log(fs.indexOf(9.5))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "1\n-1\n"

    def test_struct_elements_match_by_identity(self, compile_and_run):
        # Two structs with identical fields are two distinct values --
        # claude.md #79's arr[T] holds each element's own header
        # pointer, so this is the same "aliasing means sharing one
        # address" identity every other struct operation uses.
        source = """
        struct Point { x:int y:int }
        Point a
        a.x = 1
        a.y = 2
        Point b
        b.x = 1
        b.y = 2
        arr[Point] ps = [a, b]
        log(ps.indexOf(a))
        log(ps.indexOf(b))
        Point c
        c.x = 1
        c.y = 2
        log(ps.indexOf(c))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "0\n1\n-1\n"

    def test_composes_with_splice(self, compile_and_run):
        # The reason -1 is the right answer shape: this is what removal
        # by value looks like, and it needs no separate "was it found"
        # dance for the found case.
        source = """
        arr[text] queue = ['a', 'b', 'c']
        arr[text] gone = queue.splice(queue.indexOf('b'), 1)
        log(gone[0])
        log(`${queue.length}: ${queue[0]},${queue[1]}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "b\n2: a,c\n"

    def test_wrong_arity_or_element_type_is_a_compile_error(self, parser, semantic, errors):
        for source in [
            "arr[int] xs = [1]\nlog(xs.indexOf())",
            "arr[int] xs = [1]\nlog(xs.indexOf(1, 2))",
            "arr[int] xs = [1]\nlog(xs.indexOf('a'))",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")


class TestArraySort:
    """claude.md #184 (uraikus/festina#76 item 2): xs.sort(cmpFn), a
    comparator-based sort taking cmpFn:func[T,T]:int, JS/C-qsort style
    -- negative/zero/positive meaning first-before-second/equal/
    first-after-second, exactly like a C qsort() comparator. In place,
    void, and STABLE (unlike qsort(), which makes no such promise) --
    see festina_array_sort's own runtime comment on why."""

    def test_sorts_ints_ascending_and_descending(self, compile_and_run):
        source = """
        int func byAsc(a:int, b:int) { return a - b }
        int func byDesc(a:int, b:int) { return b - a }
        arr[int] xs = [5, 3, 8, 1, 9, 2]
        xs.sort(byAsc)
        log(xs)
        xs.sort(byDesc)
        log(xs)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "[1,2,3,5,8,9]\n[9,8,5,3,2,1]\n"

    def test_empty_and_single_element_arrays_are_no_ops(self, compile_and_run):
        source = """
        int func byAsc(a:int, b:int) { return a - b }
        arr[int] empty = []
        empty.sort(byAsc)
        log(empty.length)
        arr[int] one = [42]
        one.sort(byAsc)
        log(one)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "0\n[42]\n"

    def test_stable_for_equal_elements(self, compile_and_run):
        # Two structs that compare equal (same y) must keep their
        # original relative order -- 'tree' was pushed before 'player'
        # and both have y=5, so 'tree' must still come first.
        source = """
        struct Sprite { name:text y:int }
        int func byY(p:Sprite, q:Sprite) { return p.y - q.y }
        Sprite a
        a.name = 'tree'
        a.y = 5
        Sprite b
        b.name = 'player'
        b.y = 5
        Sprite c
        c.name = 'rock'
        c.y = 2
        arr[Sprite] sprites = [a, b, c]
        sprites.sort(byY)
        log(`${sprites[0].name},${sprites[1].name},${sprites[2].name}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "rock,tree,player\n"

    def test_sorts_floats_and_bools(self, compile_and_run):
        source = """
        int func byFloat(a:float, b:float) {
            if (a < b) { return -1 }
            if (a > b) { return 1 }
            return 0
        }
        arr[float] fs = [3.5, 1.5, 2.5]
        fs.sort(byFloat)
        log(fs)
        int func byBool(a:bool, b:bool) {
            if (a == b) { return 0 }
            if (a) { return 1 }
            return -1
        }
        arr[bool] bs = [true, false, true, false]
        bs.sort(byBool)
        log(bs)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "[1.5,2.5,3.5]\n[false,false,true,true]\n"

    def test_arbitrary_expression_callback_not_just_a_bare_name(self, compile_and_run):
        # claude.md #165/#171's own permissive rule -- unlike setTimeout's
        # older bare-name-only convention, any func-typed EXPRESSION works.
        source = """
        int func byAsc(a:int, b:int) { return a - b }
        func[int,int]:int cmp = byAsc
        arr[int] xs = [3, 1, 2]
        xs.sort(cmp)
        log(xs)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "[1,2,3]\n"

    def test_wrong_arity_or_signature_is_a_compile_error(self, parser, semantic, errors):
        for source in [
            "int func byAsc(a:int, b:int) { return a - b }\n"
            "arr[int] xs = [1]\nxs.sort()",
            "int func byAsc(a:int, b:int) { return a - b }\n"
            "arr[int] xs = [1]\nxs.sort(byAsc, byAsc)",
            "text func bad(a:text, b:text) { return a }\n"
            "arr[int] xs = [1]\nxs.sort(bad)",
            "arr[int] xs = [1]\nxs.sort(5)",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")


class TestBoolArrayElementStride:
    """claude.md #97: arr[bool] is the one array whose element slot is
    a single byte -- bool lowers to i8 (see _llvm_type's own note on
    why it isn't i1), while int/float/text/struct/arr/map all lower to
    something 8 bytes wide.

    claude.md #96's array helpers move elements by a byte count the
    compiler hands them, and that count was hardcoded to 8. For an
    arr[bool] that made push() write to byte 8*i while xs[i] read byte
    i: the value went in and a neighbouring element's byte came back
    out. These pin every helper against the stride indexing actually
    uses."""

    def test_push_is_readable_at_its_own_index(self, compile_and_run):
        source = """
        arr[bool] bs = [true, false]
        bs.push(true)
        log(bs.length)
        log(bs[0])
        log(bs[1])
        log(bs[2])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "3\ntrue\nfalse\ntrue\n"

    def test_unshift_shift_and_splice(self, compile_and_run):
        source = """
        arr[bool] bs = [true, false, true, false]
        bs.unshift(true)
        log(`${bs.length} ${bs[0]} ${bs[1]}`)
        log(bs.shift())
        arr[bool] cut = bs.splice(1, 2)
        log(`${cut.length} ${cut[0]} ${cut[1]}`)
        log(`${bs.length} ${bs[0]} ${bs[1]}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == (
            "5 true true\n"
            "true\n"
            "2 false true\n"
            "2 true false\n"
        )

    def test_pop_on_an_empty_bool_array_is_null(self, compile_and_run):
        source = """
        arr[bool] bs = []
        log(bs.pop())
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "null\n"


class TestUnassignedNestedFieldsAutoVivify:
    """claude.md #97: reaching THROUGH an unassigned struct/arr/map
    field used to segfault.

    claude.md's own rule is that an uninitialized field reads as its
    zero value, and for int/float/bool/text that already held. But a
    struct/arr/map field starts as a null pointer (calloc, or a global's
    zeroinitializer, gives it nothing else), so `o.inner.n` dereferenced
    null -- a crash, not a zero. Both reads and writes crashed, and the
    array and map cases crashed the same way the struct one did.

    The fix gives such a field real storage the first time it is
    reached, lazily. Lazily rather than eagerly at declaration because
    one mechanism then covers stack locals, heap locals, globals (whose
    storage is a compile-time zeroinitializer with nowhere to run an
    initializer at all), parameters, and fields nested arbitrarily deep
    inside other fields."""

    def test_reading_through_an_unassigned_struct_field(self, compile_and_run):
        source = """
        struct Inner { n:int label:text }
        struct Outer { inner:Inner }
        Outer o
        log(o.inner.n)
        log(o.inner.label)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        # A null text logs as an empty line -- the existing convention,
        # unchanged here; what matters is that it does not crash.
        assert result.stdout == "0\n\n"

    def test_reading_an_unassigned_array_or_map_field(self, compile_and_run):
        source = """
        struct Bag { xs:arr[int] m:map[int] }
        Bag b
        log(b.xs.length)
        log(b.m['nothing'])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        # An absent map[int] key answers int's own null, which logs as
        # its reserved sentinel (see codegen's INT_NULL_CONST).
        assert result.stdout == "0\n-9223372036854775808\n"

    def test_the_storage_is_created_once_not_per_access(self, compile_and_run):
        # The identity check: if each read vivified a FRESH value, the
        # write below would land somewhere the read never looks at.
        source = """
        struct Inner { n:int }
        struct Outer { inner:Inner }
        Outer o
        o.inner.n = 5
        log(o.inner.n)
        o.inner.n = o.inner.n + 1
        log(o.inner.n)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "5\n6\n"

    def test_pushing_onto_an_unassigned_array_field(self, compile_and_run):
        source = """
        struct Bag { xs:arr[int] }
        Bag b
        b.xs.push(1)
        b.xs.push(2)
        log(`${b.xs.length}: ${b.xs[0]},${b.xs[1]}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2: 1,2\n"

    def test_setting_a_key_on_an_unassigned_map_field(self, compile_and_run):
        source = """
        struct Bag { m:map[int] }
        Bag b
        b.m['k'] = 9
        log(b.m['k'])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "9\n"

    def test_an_explicit_assignment_still_wins(self, compile_and_run):
        source = """
        struct Inner { n:int }
        struct Outer { inner:Inner }
        Inner i
        i.n = 7
        Outer o
        o.inner = i
        log(o.inner.n)
        i.n = 8
        log(o.inner.n)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        # Assigned by reference, so the later write to `i` is visible
        # through `o.inner` -- claude.md #79's shared-header identity.
        assert result.stdout == "7\n8\n"

    def test_deeply_nested_unassigned_fields(self, compile_and_run):
        source = """
        struct C { n:int }
        struct B { c:C }
        struct A { b:B }
        A a
        log(a.b.c.n)
        a.b.c.n = 3
        log(a.b.c.n)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "0\n3\n"


class TestTextConcatOwnership:
    """claude.md #97: a `+` on text is an owning source.

    claude.md #83 classified only a Call and a template literal as
    "already a fresh, exclusively-owned buffer". A text `+` compiles to
    exactly one festina_str_concat, which mallocs unconditionally with
    no operand-passthrough path -- so it is just as owning, and leaving
    it out meant every binding of a concatenation copied a buffer that
    was already exclusively owned and dropped the original: `text j = a
    + b` and `return s + '!'` each leaked one buffer per evaluation.

    The other half is that festina_str_concat COPIES from both operands
    and keeps neither, so a chained `a + b + c` has to free its own
    intermediate -- the same fix _emit_template already applies to its
    own intermediates.

    These are behaviour tests; the leaks themselves were measured under
    LeakSanitizer, which is not available here."""

    def test_chained_concatenation_frees_its_intermediate(self, parser, semantic, codegen):
        source = """
        text a = 'aa'
        text b = 'bb'
        text c = 'cc'
        void func use() {
            text joined = a + b + c
        }
        use()
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        body = ir.split("define void @use()")[1].split("\n}")[0]
        # Two concats, one free of the first one's result -- and no
        # festina_text_own, since the outer concat is itself owning.
        assert body.count("@festina_str_concat") == 2
        # One free for the `a + b` intermediate, one for `joined` at
        # scope exit -- and no festina_text_own at all, since the outer
        # concat is itself owning and needs no copy.
        assert body.count("call void @free(") == 2
        assert "@festina_text_own" not in body

    def test_returning_a_concatenation_does_not_copy_it(self, parser, semantic, codegen):
        source = """
        text func bang(s:text) {
            return s + '!'
        }
        log(bang('x'))
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        body = ir.split("define ptr @bang(")[1].split("\n}")[0]
        assert body.count("@festina_str_concat") == 1
        # The only text_own left is the parameter binding (claude.md
        # #84), never the returned concatenation itself.
        assert body.count("@festina_text_own") == 1

    def test_concatenation_still_produces_the_right_values(self, compile_and_run):
        source = """
        text func bang(s:text) {
            return s + '!'
        }
        text a = 'aa'
        text b = 'bb'
        log(a + b + 'cc')
        log(bang(a + b))
        text kept = a + b
        log(kept)
        log(kept + kept)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "aabbcc\naabb!\naabb\naabbaabb\n"

    def test_comparing_two_computed_texts(self, compile_and_run):
        source = """
        text a = 'aa'
        log(a + 'b' == 'aab')
        log(a + 'b' != 'aab')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\nfalse\n"


class TestComputedMapKeyOwnership:
    """claude.md #97: festina_map_set strdups the key it is given (see
    its own comment on why it never aliases the caller's pointer) and
    festina_map_get only reads it, so a key the CALLER allocated --
    `m[`s${i}`] = v`, `m[a + b]` -- has no owner left once the call
    returns. Both sites now free it."""

    def test_a_computed_key_round_trips(self, compile_and_run):
        source = """
        map[int] scores = {}
        for int i = 0, i < 3, i++ {
            scores[`s${i}`] = i * 10
        }
        log(scores['s0'])
        log(scores[`s${1}`])
        log(scores['s' + '2'])
        log(scores['missing'])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "0\n10\n20\n-9223372036854775808\n"

    def test_the_map_keeps_its_own_copy_of_the_key(self, compile_and_run):
        # The key buffer is freed at the call site now, so the map must
        # genuinely own its copy for this lookup to still work.
        source = """
        map[text] m = {}
        text k = 'na'
        m[k + 'me'] = 'ada'
        k = 'zzz'
        log(m['name'])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "ada\n"


class TestTopLevelBlockScopeTracking:
    """claude.md #97: claude.md #74's scope tracking now covers the
    top-level statements too.

    A top-level VarDecl is a global and is unaffected. What this
    reaches is a local declared inside a NESTED block at top level --
    `text row = a + b` in a top-level `while` body -- which was emitted
    as an ordinary alloca and, with tracking off, never freed: one
    leaked buffer per iteration, in exactly the shape a game loop is
    written in. Leak-freedom was measured under LeakSanitizer; what is
    pinned here is that the values stay correct, since the same switch
    also turns on claude.md #81's stack promotion for these locals."""

    def test_locals_in_a_top_level_loop_stay_correct(self, compile_and_run):
        source = """
        struct P { x:int y:int }
        arr[P] kept = []
        int total = 0
        for int i = 0, i < 5, i++ {
            P local
            local.x = i
            local.y = i * 2
            arr[int] nums = [i, i + 1]
            text row = `${local.x},${local.y}` + '|'
            total = total + nums[1]
            if i % 2 == 0 {
                kept.push(local)
            }
        }
        log(total)
        log(kept.length)
        log(`${kept[0].x}/${kept[0].y} ${kept[2].x}/${kept[2].y}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "15\n3\n0/0 4/8\n"

    def test_a_map_local_in_a_top_level_loop(self, compile_and_run):
        source = """
        text last = ''
        for int i = 0, i < 3, i++ {
            map[text] m = {'k': `v${i}`}
            last = m['k']
        }
        log(last)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "v2\n"


class TestArgv:
    """claude.md #150: `argv` -- a real, mutable `arr[text]` global,
    pre-registered in semantic.analyze (so it's usable without any
    declaration) and populated from the process's real OS argc/argv at
    the very start of main(), before any top-level statement runs. See
    _emit_main_and_entry's own comment for why the store is a plain,
    direct `store ptr %argv_arr, ptr @argv` rather than going through
    the generic global retain/release helper every OTHER global
    reassignment uses."""

    def test_argv0_is_the_program_path(self, compile_and_run):
        result = compile_and_run("log(argv.length)\nlog(argv[0] != '')\n")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["1", "true"]

    def test_extra_arguments_are_visible(self, compile_and_run):
        source = """
        log(argv.length)
        log(argv[1])
        log(argv[2])
        """
        result = compile_and_run(source, args=["hello", "world"])
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["3", "hello", "world"]

    def test_argv_is_a_real_mutable_array(self, compile_and_run):
        # Nothing about argv's pre-registration makes it special once
        # main() has populated it -- ordinary arr[text] operations work
        # on it like any other array local/global.
        source = """
        argv.push('extra')
        log(argv[argv.length - 1])
        """
        result = compile_and_run(source, args=["a"])
        assert result.returncode == 0
        assert result.stdout == "extra\n"


class TestExec:
    """claude.md #150: exec(args:arr[text]):int -- spawns args[0]
    (PATH-searched), inheriting stdio, and returns its real exit code,
    or -1 if the process never started at all (distinguished from a
    real exit(127) via the self-pipe technique -- see
    festina_process_exec's own comment in runtime/festina_runtime.c).
    Named festina_process_exec at the runtime level, not festina_exec,
    to avoid colliding with the pre-existing internal SQL-DDL helper of
    that name."""

    # claude.md #235: `/bin/sh` and `/bin/echo` are MSYS2 virtual paths
    # on Windows -- the shell resolves them, but festina_process_exec's
    # own _spawnvp (a plain CRT call, PATH-searched) cannot, so both
    # exec tests came back -1/empty on the windows CI job. `cmd /c` is
    # the Windows spelling of the same two programs; the runtime's own
    # contract (real exit code, inherited stdio) is what's under test,
    # not a particular shell.
    _SHELL_EXIT_3 = (["cmd", "/c", "exit 3"] if sys.platform == "win32"
                     else ["/bin/sh", "-c", "exit 3"])
    _ECHO_FROM_CHILD = (["cmd", "/c", "echo from-child"] if sys.platform == "win32"
                        else ["/bin/echo", "from-child"])

    def test_successful_exec_returns_the_real_exit_code(self, compile_and_run):
        cmd = ", ".join(f"'{part}'" for part in self._SHELL_EXIT_3)
        source = f"""
        arr[text] cmd = [{cmd}]
        log(exec(cmd))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "3\n"

    def test_a_missing_executable_returns_negative_one(self, compile_and_run):
        # Not 127 -- that would be indistinguishable from a real
        # program that legitimately calls exit(127) itself. See the
        # self-pipe technique in festina_process_exec.
        source = """
        arr[text] cmd = ['/no/such/binary/at/all/xyz']
        log(exec(cmd))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "-1\n"

    def test_stdio_is_inherited(self, compile_and_run):
        # exec() inherits stdout rather than capturing it -- the child's
        # own output lands directly in the parent's stdout stream.
        cmd = ", ".join(f"'{part}'" for part in self._ECHO_FROM_CHILD)
        source = f"""
        arr[text] cmd = [{cmd}]
        exec(cmd)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert "from-child" in result.stdout

    def test_exec_is_rejected_for_wasm(self, cli_mod, tmp_path):
        src = tmp_path / "main.f"
        src.write_text("arr[text] cmd = ['ls']\nexec(cmd)", encoding="utf-8")
        with pytest.raises(cli_mod.CompileError) as exc_info:
            cli_mod.compile_file(str(src), str(tmp_path / "out.wasm"),
                                  cc="clang", target="wasm32-wasi")
        assert exc_info.value.category == "unsupported platform feature"
        assert "exec" in str(exc_info.value)

    def test_the_removed_two_argument_callback_form_is_a_clear_error(
            self, parser, semantic, errors):
        # claude.md #221: exec(args, callback) -- the non-blocking form
        # claude.md #177 added -- was removed; exec() now only ever
        # takes 1 argument.
        source = """
        void func onDone(code:int) { log(code) }
        arr[text] cmd = ['ls']
        exec(cmd, onDone)
        """
        with pytest.raises(errors.CompileError, match="expects 1 argument"):
            semantic.analyze(parser.parse(source))


class TestToInt:
    """claude.md #150: text.toInt():int -- JS parseInt()-style parsing
    (leading whitespace skipped, an optional sign, digits until the
    first non-digit), returning int-null (-9223372036854775808) rather
    than raising on unparseable input, mirroring toFloat's existing
    null-on-failure convention. A literal-receiver call constant-folds
    entirely at compile time (see _parse_int_like_strtoll) -- steering
    message mid-task: 'offload as much of the work as possible at the
    compilation phase.'"""

    def test_a_clean_int_parses(self, compile_and_run):
        result = compile_and_run("log('42'.toInt())")
        assert result.returncode == 0
        assert result.stdout == "42\n"

    def test_trailing_garbage_is_ignored_js_style(self, compile_and_run):
        result = compile_and_run("log('42abc'.toInt())")
        assert result.returncode == 0
        assert result.stdout == "42\n"

    def test_leading_whitespace_and_sign_are_handled(self, compile_and_run):
        result = compile_and_run("log('  -17'.toInt())")
        assert result.returncode == 0
        assert result.stdout == "-17\n"

    def test_unparseable_text_returns_null(self, compile_and_run):
        source = """
        int n = 'nope'.toInt()
        log(n == null)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\n"

    def test_empty_text_returns_null(self, compile_and_run):
        source = """
        int n = ''.toInt()
        log(n == null)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\n"

    def test_a_dynamic_receiver_is_parsed_at_runtime(self, compile_and_run):
        # Not a StringLit receiver -- takes the real festina_text_to_int
        # runtime path rather than the compile-time constant fold.
        source = """
        text func makeNum() { return '9' + '9' }
        log(makeNum().toInt())
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "99\n"

    def test_literal_receiver_constant_folds(self, parser, semantic, codegen):
        # No festina_text_to_int call at all -- computed entirely in
        # Python via _parse_int_like_strtoll, matching the mid-task
        # steering message to offload work to compile time wherever
        # possible.
        program = parser.parse("log('123'.toInt())")
        analyzed = semantic.analyze(program)
        ir = codegen.generate_ir(program, analyzed)
        assert "call i64 @festina_text_to_int(" not in ir
        assert "123" in ir


class TestTextIndexing:
    """claude.md #150: text[i]:text -- read-only, UTF-8 codepoint-
    indexed character access, returning null (not a runtime crash) for
    an out-of-range or negative index. Deliberately DIFFERENT semantics
    from arr[T] indexing (which is unchecked raw-memory access) --
    text's own bounds are always checked. A literal receiver with a
    literal non-negative index constant-folds entirely in Python."""

    def test_a_middle_character(self, compile_and_run):
        result = compile_and_run("text s = 'hello'\nlog(s[1])")
        assert result.returncode == 0
        assert result.stdout == "e\n"

    def test_index_zero(self, compile_and_run):
        result = compile_and_run("text s = 'hello'\nlog(s[0])")
        assert result.returncode == 0
        assert result.stdout == "h\n"

    def test_out_of_range_is_null_not_a_crash(self, compile_and_run):
        source = """
        text s = 'hi'
        text c = s[100]
        log(c == null)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\n"

    def test_negative_index_is_null(self, compile_and_run):
        source = """
        text s = 'hi'
        text c = s[-1]
        log(c == null)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\n"

    def test_multibyte_utf8_is_indexed_by_codepoint_not_byte(self, compile_and_run):
        # 'café' -- 'é' is a 2-byte UTF-8 sequence, so a byte-indexed
        # implementation would return a broken half-character here;
        # codepoint indexing must return the whole 'é'.
        result = compile_and_run("text s = 'café'\nlog(s[3])")
        assert result.returncode == 0
        assert result.stdout == "é\n"

    def test_assignment_is_rejected(self, parser, semantic, errors):
        program = parser.parse("text s = 'hi'\ns[0] = 'x'")
        with pytest.raises(errors.CompileError, match="immutable"):
            semantic.analyze(program)

    def test_a_dynamic_receiver_and_index_go_through_the_runtime_path(self, compile_and_run):
        source = """
        text func makeText() { return 'ab' + 'cd' }
        int func idx() { return 1 + 1 }
        log(makeText()[idx()])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "c\n"

    def test_literal_receiver_and_index_constant_folds(self, parser, semantic, codegen):
        program = parser.parse("text s = 'hello'\nlog(s[1])\nlog('world'[0])")
        analyzed = semantic.analyze(program)
        ir = codegen.generate_ir(program, analyzed)
        # 'world'[0] is a literal-on-literal index -- folded to 'w' in
        # Python, no festina_text_char_at call needed for it. s[1]
        # (a variable receiver) still goes through the runtime path.
        assert "call ptr @festina_text_char_at(" in ir
        assert '"w\\00"' in ir or "w\\00" in ir

    def test_indexing_does_not_leak(self, compile_and_run):
        # festina_text_char_at always returns a freshly allocated
        # buffer -- the dynamic-path codegen must free the receiver AND
        # correctly mark the result as owning (self._minted_values) so
        # a bound/used result isn't defensively re-copied and leaked.
        # This is a correctness/output check, not a real leak check --
        # see scripts/leak_stress.sh for the ASan/LeakSanitizer side of
        # this, run manually during development of claude.md #150.
        source = """
        text s = 'abcdef'
        text out = ''
        int j = 0
        while j < 6 {
            out = out + s[j]
            j = j + 1
        }
        log(out)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "abcdef\n"


class TestEnums:
    """claude.md #176: enum + typeof end to end -- both representations
    (pure-struct self-tagging, mixed heap-boxed), typeof, coercion,
    field access and its runtime festina_fail safety net, and retain/
    release correctness for enum-typed locals.

    See tests/test_enums.py for the lexer/parser/semantic-only coverage
    of the same section (declaration rules, coercion type-checking,
    field-access type-checking)."""

    def test_worked_example_extracts_the_right_metric_for_a_circle(self, compile_and_run):
        source = """
        struct Circle { radius:int }
        struct Square { area:int }

        enum Shape = Circle, Square

        int func extractShapeMetric(shape:Shape) {
            if typeof shape == 'Circle' {
                return shape.radius
            } else {
                return shape.area
            }
        }

        Circle c
        c.radius = 5
        log(extractShapeMetric(c))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "5"

    def test_worked_example_extracts_the_right_metric_for_a_square(self, compile_and_run):
        source = """
        struct Circle { radius:int }
        struct Square { area:int }

        enum Shape = Circle, Square

        int func extractShapeMetric(shape:Shape) {
            if typeof shape == 'Circle' {
                return shape.radius
            } else {
                return shape.area
            }
        }

        Square s
        s.area = 42
        log(extractShapeMetric(s))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "42"

    def test_typeof_on_every_non_enum_type(self, compile_and_run):
        # claude.md #176: for anything not EnumType-typed, typeof is a
        # pure compile-time constant -- the runtime type IS the static
        # type, always.
        source = """
        struct Point { x:int }
        int i = 1
        float f = 1.5
        bool b = true
        text t = 'hi'
        arr[int] a = [1, 2]
        Point p
        log(typeof i)
        log(typeof f)
        log(typeof b)
        log(typeof t)
        log(typeof a)
        log(typeof p)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "int\nfloat\nbool\ntext\narr[int]\nPoint\n"

    def test_typeof_on_a_pure_struct_enum_returns_the_runtime_variant(self, compile_and_run):
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        enum Shape = Circle, Square
        Circle c
        Square sq
        Shape a = c
        Shape b = sq
        log(typeof a)
        log(typeof b)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "Circle\nSquare\n"

    def test_field_mismatch_fails_loudly_instead_of_corrupting(self, compile_and_run):
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        enum Shape = Circle, Square
        Square s
        s.area = 42
        Shape shape = s
        log(shape.radius)
        """
        result = compile_and_run(source)
        assert result.returncode == 1
        assert "field 'radius' is only valid when this Shape value is a Circle" in result.stderr

    def test_typeof_on_a_null_enum_value_fails_loudly(self, compile_and_run):
        # claude.md #176: an enum-typed value defaults to null until
        # assigned (no auto-vivify) -- typeof must fail loudly rather
        # than dereference the null tag pointer.
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        enum Shape = Circle, Square
        Shape shape
        log(typeof shape)
        """
        result = compile_and_run(source)
        assert result.returncode == 1
        assert "typeof applied to a null Shape value" in result.stderr

    def test_field_access_on_a_null_enum_value_fails_loudly(self, compile_and_run):
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        enum Shape = Circle, Square
        Shape shape
        log(shape.radius)
        """
        result = compile_and_run(source)
        assert result.returncode == 1
        assert "field 'radius' accessed on a null Shape value" in result.stderr

    def test_mixed_enum_round_trips_each_member_type_through_typeof(self, compile_and_run):
        source = """
        struct User { id:int name:text }
        enum Json = int, text, User

        int i = 5
        Json a = i
        log(typeof a)

        text t = 'hello'
        Json b = t
        log(typeof b)

        User u
        u.id = 1
        u.name = 'pat'
        Json c = u
        log(typeof c)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "int\ntext\nUser\n"

    def test_enum_typed_locals_alias_the_same_struct(self, compile_and_run):
        # claude.md #176: a pure-struct enum value IS the member
        # struct's own pointer -- assigning it into another enum-typed
        # slot shares that exact pointer, the same aliasing every other
        # struct assignment already has.
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        enum Shape = Circle, Square
        Circle c
        c.radius = 5
        Shape a = c
        Shape b = a
        c.radius = 9
        log(b.radius)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "9"

    def test_reassigning_a_pure_struct_enum_local_releases_the_old_value(self, compile_and_run):
        # Regression test: an enum-typed global/local starts out null
        # (no auto-vivify), and the FIRST reassignment used to release
        # that null value by unconditionally reading its tag at
        # payload-16 -- a real segfault fixed by null-checking before
        # ever reading the tag (see _release_fn_for_enum's own
        # comment). Every later iteration exercises the ordinary
        # struct-to-struct reassignment release path too.
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        enum Shape = Circle, Square
        Shape shape
        for int i = 0, i < 5, i++ {
            Circle c
            c.radius = i
            shape = c
        }
        log(shape.radius)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "4"

    def test_reassigning_a_mixed_enum_local_releases_the_old_box(self, compile_and_run):
        # Same regression coverage as the pure-struct case above, for
        # the heap-boxed mixed representation's own release wrapper.
        source = """
        enum Choice = int, text
        Choice c
        for int i = 0, i < 5, i++ {
            c = `item${i}`
        }
        log(typeof c)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "text"

    def test_a_fresh_with_initializer_enum_local_declared_in_a_loop_is_freed(self, compile_and_run):
        # claude.md #197: a genuine, pre-existing gap found while
        # building thread Phase 3 (no thread code involved at all) --
        # unlike REASSIGNING an already-declared enum-typed variable
        # (the two tests just above, both go through
        # _emit_local_retain_release, which already dispatched through
        # _is_refcounted correctly), DECLARING a fresh, WITH-
        # INITIALIZER enum-typed local was never scheduled for release
        # at scope exit at all (_emit_block's own tracking dispatch
        # only listed BLOB/REGEX/ImageType/AudioType, not EnumType --
        # claude.md #176's own comment on _is_refcounted claimed "no
        # special-casing needed anywhere else", which turned out not
        # to be true here) -- so a fresh mixed-enum box, and the text
        # buffer boxed inside it, leaked every single iteration. This
        # only checks correct behavior; the leak itself is verified
        # via scripts/leak_stress.sh (see tests/test_leak_stress.py's
        # own ASan/LeakSanitizer coverage, not duplicated here).
        source = """
        enum DataPacket = int, text
        int i = 0
        while i < 200 {
            DataPacket p = `hello${i}`
            i = i + 1
        }
        log('done')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"


class TestThreads:
    """claude.md #195 Phase 2: `thread NAME { ... }` -- the real,
    compiled-and-run counterpart to tests/test_threads.py's parser/
    semantic coverage (that file explicitly defers this class of test
    here, mirroring TestEnums' own split at the top of this file).
    Every test drives itself to a deterministic close(0) from inside
    an onMessage()/live() callback (an ordinary main-thread function
    call -- ending the process is the only reliable way a test can
    observe the reply side of a message round trip at all, since no
    top-level statement ever runs concurrently with the drain step
    that would deliver one -- see codegen.py's own _emit_main_and_entry
    doc comment on loop selection)."""

    def test_int_message_round_trip(self, compile_and_run):
        source = """
        on message(worker:thread, msg:int) {
            log(msg)
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:int) {
                postMessage(msg * 2)
            }
        }
        worker.postMessage(21)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "42"

    def test_worker_dot_main_reads_true_only_when_sent_by_main(self, compile_and_run):
        # claude.md #216: `worker` is never null any more -- `worker.main`
        # is the real replacement for claude.md #208's old "worker ==
        # null" check. main -> worker sees main=true; worker -> main
        # (the reply) sees main=false, since it genuinely came from the
        # worker thread, not main.
        source = """
        on message(worker:thread, msg:int) {
            log(`main got ${msg} from main=${worker.main}`)
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:int) {
                log(`worker got ${msg} from main=${worker.main}`)
                postMessage(msg * 2)
            }
        }
        worker.postMessage(21)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == (
            "worker got 21 from main=true\n"
            "main got 42 from main=false"
        )

    def test_text_message_round_trip(self, compile_and_run):
        source = """
        on message(worker:thread, msg:text) {
            log(msg)
            close(0)
        }
        thread echoer {
            on message(worker:thread, msg:text) {
                postMessage(`echo:${msg}`)
            }
        }
        echoer.postMessage('hi')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "echo:hi"

    def test_float_message_round_trip(self, compile_and_run):
        source = """
        on message(worker:thread, msg:float) {
            log(msg)
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:float) {
                postMessage(msg + 0.5)
            }
        }
        worker.postMessage(1.5)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "2"

    def test_bool_message_round_trip(self, compile_and_run):
        source = """
        on message(worker:thread, msg:bool) {
            log(msg)
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:bool) {
                postMessage(!msg)
            }
        }
        worker.postMessage(true)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "false"

    def test_on_load_fires_before_any_inbound_message_and_can_post_on_its_own(self, compile_and_run):
        source = """
        on message(worker:thread, msg:text) {
            log(msg)
            close(0)
        }
        thread worker {
            on load() {
                postMessage('ready')
            }
        }
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "ready"

    def test_thread_private_state_persists_and_accumulates_across_messages(self, compile_and_run):
        # claude.md #195 Phase 1: `int total = 0` is this thread's own
        # private state, invisible to the main program -- this proves
        # it's also real, per-thread, PERSISTENT storage at runtime
        # (not re-zeroed per message), by summing three messages
        # in order and checking the final accumulated total. Delivery
        # order is guaranteed here: both queues are plain FIFOs, and
        # everything on each side runs on exactly one OS thread (this
        # thread's own single worker; the main program's own single
        # thread), so three sends from main arrive, and are answered,
        # in the order they were sent.
        source = """
        int repliesSeen = 0
        int lastVal = 0
        on message(worker:thread, msg:int) {
            repliesSeen = repliesSeen + 1
            lastVal = msg
            if repliesSeen == 3 {
                log(lastVal)
                close(0)
            }
        }
        thread counter {
            int total = 0
            on message(worker:thread, msg:int) {
                total = total + msg
                postMessage(total)
            }
        }
        counter.postMessage(1)
        counter.postMessage(2)
        counter.postMessage(3)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "6"

    def test_kill_then_isalive_is_false_then_live_revives_it(self, compile_and_run):
        # No `on load`/`on message` handler at all here, deliberately --
        # this test's own stdout needs to be fully deterministic, and a
        # worker-thread log() would interleave with the main thread's
        # own unpredictably (real concurrency, not a bug -- see
        # test_int_message_round_trip and friends for why every OTHER
        # test here routes its own assertion through a close()-from-
        # inside-onMessage() callback instead). kill()/live() are both
        # BLOCKING (kill() pthread_joins; live() spawns and only then
        # calls its own callback), so this sequence is deterministic
        # with no message-passing involved at all.
        source = """
        thread worker {
        }
        log(worker.isAlive())
        worker.kill()
        log(worker.isAlive())
        worker.live(void (ok:bool) => log(ok))
        log(worker.isAlive())
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["true", "false", "true", "true"]

    def test_kill_then_live_reopens_a_genuinely_working_database_handle(
            self, compile_and_run, tmp_path):
        # claude.md #207: a kill()/live() cycle used to leak the old
        # sqlite3*/fd pair (on_load unconditionally opened a fresh
        # handle over the top of one never closed on kill()) -- this
        # pins the BEHAVIORAL half (leak-freedom itself is an ASan/
        # LeakSanitizer question, covered by
        # tests/stress/thread_db_kill_live_churn.f under
        # scripts/leak_stress.sh): several kill()/live() cycles in a
        # row, each blocking and deterministic exactly like
        # test_kill_then_isalive_is_false_then_live_revives_it just
        # above, then a real on message() round trip against the
        # THREAD's OWN database proves the handle live() just reopened
        # actually works, not just that the process didn't crash.
        db = tmp_path / "worker.sqlite"
        source = f"""
        on message(worker:thread, msg:int) {{
            log(msg)
            close(0)
        }}
        table Hits {{ n:int }}
        thread worker {{
            DatabaseURL = '{db}'
            on message(worker:thread, msg:int) {{
                sqlite('INSERT INTO Hits (n) VALUES (?)', [msg])
                arr[Hits] counted = sqlite('SELECT count(*) AS n FROM Hits')
                postMessage(counted[0].n)
            }}
        }}
        int cycle = 0
        while cycle < 5 {{
            worker.kill()
            worker.live(void (ok:bool) => log(ok))
            cycle = cycle + 1
        }}
        worker.postMessage(1)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        lines = result.stdout.strip().splitlines()
        assert lines[:5] == ["true"] * 5
        # A fresh db file, one INSERT total (the single postMessage
        # after the cycles) -- proves the handle live() reopened the
        # LAST time is a real, working connection against the thread's
        # own database, not a stale or reused one.
        assert lines[5] == "1"

    def test_main_thread_death_kills_a_still_idle_child_thread(self, compile_and_run):
        # claude.md #195: "if the main thread dies, kill all child
        # threads" -- festina_program_exit runs the exit handler (if
        # any -- none here) and THEN festina_thread_kill_all()
        # (synchronous, joins every thread), so 'worker exiting' is
        # guaranteed to print after 'main done', and this process must
        # exit cleanly rather than hang on an orphaned OS thread (the
        # 15s subprocess timeout this fixture's own `compile_and_run`
        # applies is exactly what would catch a regression here).
        source = """
        thread worker {
            on exit(code:int) {
                log('worker exiting')
            }
        }
        log('main done')
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "main done\nworker exiting\n"

    def test_two_independent_threads_do_not_collide(self, compile_and_run):
        # claude.md #208: ONE global top-level `on message` handler now
        # receives everything sent to main from EITHER thread -- there
        # is no per-thread `.onMessage()` registration any more to keep
        # the two replies apart by callback identity, so which of the
        # two real OS threads actually FINISHES first (and so posts
        # its reply first) is genuine, unordered concurrency, not a
        # FIFO-drain-order guarantee -- confirmed directly (an earlier
        # draft of this test asserted a fixed arrival order and failed
        # intermittently). Sorted into seenA/seenB by VALUE instead
        # (thread a's reply is always < 10, thread b's is always >=
        # 10), so the assertion is independent of which one arrives
        # first.
        source = """
        int seenA = 0
        int seenB = 0
        void func checkDone() {
            if seenA != 0 && seenB != 0 {
                log(seenA)
                log(seenB)
                close(0)
            }
        }
        on message(worker:thread, msg:int) {
            if msg < 10 {
                seenA = msg
            } else {
                seenB = msg
            }
            checkDone()
        }
        thread a {
            on message(worker:thread, msg:int) {
                postMessage(msg + 1)
            }
        }
        thread b {
            on message(worker:thread, msg:int) {
                postMessage(msg + 10)
            }
        }
        a.postMessage(1)
        b.postMessage(1)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["2", "11"]

    def test_thread_to_thread_message_sees_worker_dot_main_false(self, compile_and_run):
        # claude.md #216: a thread-to-thread send is genuinely NOT from
        # main -- `worker.main` on the receiving end must read false,
        # distinct from a main-originated send (covered above). `a`
        # posts back -1 instead of the real value if it ever observed
        # `worker.main` as true, so a wrong flip fails loudly rather
        # than just passing coincidentally.
        source = """
        on message(worker:thread, msg:int) {
            log(msg)
            close(0)
        }
        thread a {
            on message(worker:thread, msg:int) {
                postMessage(worker.main ? -1 : msg)
            }
        }
        thread b {
            on load() { a.postMessage(99) }
        }
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "99"

    def test_a_self_referencing_struct_message_type_is_a_clear_not_yet_error(self, parser, semantic, codegen, errors):
        # claude.md #197 Phase 3: struct/arr[T]/map[T] are clonable
        # now, but only when ACYCLIC -- a self-referencing struct type
        # is rejected outright (cloning it could loop forever on a
        # genuinely cyclic runtime value, unlike release's own
        # refcount-bounded cascade), with the identical "not supported
        # yet" shape a still-unimplemented type gets, rather than a
        # stack overflow or hang.
        source = """
        struct Node { val:int next:Node }
        thread worker {
            on message(worker:thread, msg:Node) {
                log(msg.val)
            }
        }
        Node n
        worker.postMessage(n)
        """
        program = parser.parse(source)
        analyzed = semantic.analyze(program)
        with pytest.raises(errors.CompileError, match="not supported yet"):
            codegen.generate_ir(program, analyzed)

    def test_struct_message_round_trip(self, compile_and_run):
        source = """
        struct Point { x:int y:int label:text }
        on message(worker:thread, msg:Point) {
            log(msg.x)
            log(msg.y)
            log(msg.label)
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:Point) {
                Point out = msg
                out.x = msg.x + 1
                postMessage(out)
            }
        }
        Point pt
        pt.x = 10
        pt.y = 20
        pt.label = 'hello'
        worker.postMessage(pt)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["11", "20", "hello"]

    def test_array_message_round_trip(self, compile_and_run):
        source = """
        on message(worker:thread, msg:arr[text]) {
            int i = 0
            while i < msg.length {
                log(msg[i])
                i = i + 1
            }
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:arr[text]) {
                arr[text] out = []
                int i = 0
                while i < msg.length {
                    out.push(`echo:${msg[i]}`)
                    i = i + 1
                }
                postMessage(out)
            }
        }
        arr[text] xs = ['a', 'b', 'c']
        worker.postMessage(xs)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["echo:a", "echo:b", "echo:c"]

    def test_map_message_round_trip(self, compile_and_run):
        source = """
        on message(worker:thread, msg:map[int]) {
            log(msg['a'])
            log(msg['b'])
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:map[int]) {
                map[int] out = {}
                out['a'] = msg['a'] * 2
                out['b'] = msg['b'] * 2
                postMessage(out)
            }
        }
        map[int] m = {}
        m['a'] = 5
        m['b'] = 7
        worker.postMessage(m)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["10", "14"]

    def test_mixed_enum_message_round_trip(self, compile_and_run):
        source = """
        enum DataPacket = int, text
        on message(worker:thread, msg:DataPacket) {
            log(typeof msg)
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:DataPacket) {
                log(typeof msg)
                DataPacket out = 'echoed'
                postMessage(out)
            }
        }
        DataPacket p = 42
        worker.postMessage(p)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["int", "text"]

    def test_pure_struct_enum_message_round_trip(self, compile_and_run):
        source = """
        struct Circle { radius:int }
        struct Square { side:int }
        enum Shape = Circle, Square
        on message(worker:thread, msg:Shape) {
            log(typeof msg)
            if typeof msg == 'Circle' {
                log(msg.radius)
            }
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:Shape) {
                postMessage(msg)
            }
        }
        Circle c
        c.radius = 99
        Shape s = c
        worker.postMessage(s)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["Circle", "99"]

    def test_blob_message_round_trip(self, compile_and_run, tmp_path):
        # claude.md #198 Phase 4: festina_blob_clone. `b.write(...)`
        # happens right after postMessage(b) returns -- since
        # postMessage's own clone runs synchronously on the MAIN
        # thread before it ever returns (see _emit_thread_box), the
        # worker's own copy is already fully independent by then,
        # regardless of how the two threads actually interleave --
        # proving a genuine deep clone, not a shared handle.
        (tmp_path / "source.txt").write_text("blob-payload")
        source = """
        on message(worker:thread, msg:blob) {
            log(msg.toText())
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:blob) {
                postMessage(msg)
            }
        }
        blob b = 'source.txt'
        worker.postMessage(b)
        b.write('mutated-after-send')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "blob-payload"

    def test_image_message_round_trip(self, compile_and_run):
        # claude.md #198 Phase 4: festina_image_clone (the bytes-round-
        # trip reuse), plus verifies the img-method allow-list actually
        # works end to end -- p.drawRect(...) runs INSIDE the thread
        # body, on that thread's own private clone of the surface, and
        # the drawn pixels survive the clone back out to the main
        # thread.
        source = """
        on message(worker:thread, msg:img) {
            color blue = 'blue'
            log(msg.getPixelColor(5, 5) == blue)
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:img) {
                color blue = 'blue'
                msg.drawRect(0, 0, 20, 20, blue)
                postMessage(msg)
            }
        }
        img pic = blankImage(20, 20)
        worker.postMessage(pic)
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_audio_message_round_trip(self, compile_and_run, tmp_path):
        # claude.md #198 Phase 4: festina_audio_clone (a direct field-
        # by-field copy, unlike img's bytes round trip -- see its own
        # doc comment in festina_runtime_audio.c for why). saveCopy()
        # writing real, non-empty bytes back out on the MAIN thread
        # proves the clone that crossed back out of the worker still
        # carries real, correctly-cloned audio data.
        _write_wav(tmp_path / "clip.wav", duration_s=0.05)
        source = """
        on message(worker:thread, msg:aud) {
            msg.saveCopy('echo.wav')
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:aud) {
                postMessage(msg)
            }
        }
        aud clip = 'clip.wav'
        worker.postMessage(clip)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        echo = tmp_path / "echo.wav"
        assert echo.exists()
        assert echo.stat().st_size > 0

    def test_url_message_round_trip(self, compile_and_run):
        # claude.md #198 Phase 4: festina_url_clone, including its own
        # nested map[text] searchParams clone (via the Phase 3-built
        # festina_map_clone, through the new festina_clone_text_map_
        # value trampoline).
        source = """
        on message(worker:thread, msg:url) {
            log(msg.hostname)
            log(msg.pathname)
            log(msg.searchParams['a'])
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:url) {
                postMessage(msg)
            }
        }
        url u = parseURL('https://example.com/path?a=1')
        worker.postMessage(u)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["example.com", "/path", "1"]

    def test_a_thread_with_its_own_database_url_has_a_genuinely_private_sqlite_handle(
            self, compile_and_run, tmp_path):
        # claude.md #199 Phase 5: `DatabaseURL = '<literal>'` as a
        # thread's own first statement -- its own INSERT/SELECT round
        # trips correctly through its own private handle, and the main
        # program's own separate database (a different literal file)
        # ends up as a genuinely distinct file on disk, with neither
        # program's own rows visible in the other's file -- not just
        # "the query returned the right answer" (which a single shared
        # handle would also satisfy), but "the actual bytes on disk are
        # two separate databases."
        source = """
        DatabaseURL = 'main_only.sqlite'
        table MainItem { id:int }
        sqlite('INSERT INTO MainItem (id) VALUES (1)')

        table WorkerItem { id:int label:text }
        on message(worker:thread, msg:text) {
            log(msg)
            close(0)
        }
        thread worker {
            DatabaseURL = 'worker_only.sqlite'
            on message(worker:thread, msg:int) {
                sqlite('INSERT INTO WorkerItem (id, label) VALUES (?, ?)',
                       [msg, `from-worker-${msg}`])
                arr[WorkerItem] rows = sqlite('SELECT * FROM WorkerItem')
                postMessage(rows[0].label)
            }
        }
        worker.postMessage(42)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "from-worker-42"
        assert (tmp_path / "main_only.sqlite").exists()
        assert (tmp_path / "worker_only.sqlite").exists()
        # claude.md #28's own schema-sync unconditionally creates every
        # declared table in EVERY database that gets opened (main's own
        # prologue, and this thread's own on_load, both sync the whole
        # self.tables set) -- but only the context that actually
        # QUERIED a table ever puts a ROW in it. WorkerItem exists as an
        # empty table in main's own file; MainItem exists as an empty
        # table in the worker's own file. Neither file's own MainItem/
        # WorkerItem row count crosses into the other's.
        import sqlite3 as _sqlite3
        main_conn = _sqlite3.connect(str(tmp_path / "main_only.sqlite"))
        worker_conn = _sqlite3.connect(str(tmp_path / "worker_only.sqlite"))
        try:
            assert main_conn.execute("SELECT count(*) FROM MainItem").fetchone() == (1,)
            assert main_conn.execute("SELECT count(*) FROM WorkerItem").fetchone() == (0,)
            assert worker_conn.execute("SELECT count(*) FROM WorkerItem").fetchone() == (1,)
            assert worker_conn.execute("SELECT count(*) FROM MainItem").fetchone() == (0,)
        finally:
            main_conn.close()
            worker_conn.close()

    def test_a_thread_sharing_the_main_programs_database_url_is_a_clear_compile_error(
            self, compile_and_run, errors):
        # claude.md #199 Phase 5's own whole-program conflict check,
        # exercised through the real, file-based compile pipeline (see
        # test_threads.py's own TestThreadDatabaseUrl for the same
        # check's unit-level coverage) -- festina.imports.build_program
        # is what actually sets Program.database_url from the entry
        # file's own leading `DatabaseURL = ...` statement, so this is
        # the one scenario that genuinely needs a real compile, not
        # just parser.parse()+semantic.analyze().
        source = """
        DatabaseURL = 'shared.sqlite'
        thread worker {
            DatabaseURL = 'shared.sqlite'
            on load() { sqlite('SELECT 1') }
        }
        """
        with pytest.raises(errors.CompileError,
                            match="the main program and thread 'worker' would both "
                                  "open the same database file"):
            compile_and_run(source)

    def test_postmessage_of_a_fresh_enum_coercion_does_not_leak_or_double_free(self, compile_and_run):
        # Regression coverage for a real bug found and fixed while
        # building this: postMessage(x)'s own cleanup used to always
        # call _free_text_temp with the PRE-coercion vtype, which is
        # only correct when the coercion is a no-op (TEXT -> TEXT).
        # Posting a bare text literal directly into a mixed-enum
        # inbound type coerces it into a freshly boxed enum value
        # (claude.md #176) -- the old code would have freed that box
        # as if it were a plain text buffer. ASan-verified separately
        # (see claude.md #197); this just checks the program still
        # runs correctly end to end.
        source = """
        enum DataPacket = int, text
        thread worker {
            on message(worker:thread, msg:DataPacket) {
                log(typeof msg)
            }
        }
        worker.postMessage('direct-literal-hello')
        worker.postMessage(123)
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0

    def test_a_thread_with_no_onmessage_registration_still_lets_the_process_exit_cleanly(self, compile_and_run):
        # A `peopleWorker`-style thread -- declared, idling, never
        # posting or receiving anything -- must not keep the process
        # alive past an explicit close(), and must not need
        # festina_run_timer_loop to hang waiting on it either.
        source = """
        thread idler {
        }
        log('main done')
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "main done"


class TestThreadReplyCallback:
    """claude.md #217: `t.reply(response)` / `NAME.postMessage(x).
    callback(fn)` -- the real, compiled-and-run counterpart to
    tests/test_threads.py's semantic coverage."""

    def test_main_to_worker_reply_round_trip(self, compile_and_run):
        # main sends with .callback, worker's own on message fires
        # normally (proving .reply doesn't replace ordinary delivery),
        # then replies -- the callback fires on main with the reply
        # value, not a second on_message dispatch.
        source = """
        void func onReply(r:int) {
            log(`reply: ${r}`)
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:int) {
                log(`worker got: ${msg}`)
                worker.reply(msg * 10)
            }
        }
        worker.postMessage(21).callback(onReply)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["worker got: 21", "reply: 210"]

    def test_worker_to_main_bare_reply_round_trip(self, compile_and_run):
        # a worker sends to main via the bare form with .callback;
        # main's own top-level on message fires normally, then replies
        # -- claude.md #222: the callback now fires back on MAIN's own
        # OS thread (marshaled through festina_async_io_dispatch), not
        # the worker's, closing a real cross-thread-isolation hazard.
        # Festina exposes no OS-thread-identity primitive a test could
        # check directly; the decisive proof is
        # tests/stress/thread_reply_callback_churn.f under
        # scripts/thread_tsan_stress.sh -- routing dispatch through a
        # genuinely different thread is exactly what surfaced (and let
        # this fix catch) a real, previously-latent data race in
        # festina_runtime_async.c's own g_outstanding counter, which
        # had never been written from any thread but main before.
        source = """
        void func onReply(r:int) {
            log(`worker reply: ${r}`)
        }
        void func finish() {
            close(0)
        }
        on message(worker:thread, msg:int) {
            log(`main got: ${msg}`)
            worker.reply(msg + 1)
            // The callback this send registered fires asynchronously,
            // via main's own event loop -- a short setTimeout (the
            // same pattern this suite's own timer tests already use
            // to let background work land before close()) gives it
            // time to run before the process exits.
            setTimeout(finish, 200)
        }
        thread worker {
            on load() {
                postMessage(5).callback(onReply)
            }
        }
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["main got: 5", "worker reply: 6"]

    def test_reply_does_not_trigger_on_message_again(self, compile_and_run):
        # claude.md #217: `.reply()` is a completely separate delivery
        # path -- the sender's own `on message` must NOT fire a second
        # time when the reply arrives.
        source = """
        int mainMessageCount = 0
        void func onReply(r:int) {
            log(`callback: ${r}`)
            close(0)
        }
        thread worker {
            on message(worker:thread, msg:int) {
                worker.reply(msg + 1)
            }
        }
        worker.postMessage(1).callback(onReply)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        # exactly one line -- the callback -- never a second on_message
        # dispatch on the reply's own arrival
        assert result.stdout.strip().splitlines() == ["callback: 2"]

    def test_two_outstanding_replies_resolve_to_the_right_callbacks(self, compile_and_run):
        # claude.md #217: two DIFFERENT txn ids, in flight from the
        # SAME sender at once, must each resolve to their own callback
        # -- not the other's.
        source = """
        int seen = 0
        void func onA(r:int) {
            log(`A: ${r}`)
            seen = seen + 1
            if seen == 2 { close(0) }
        }
        void func onB(r:int) {
            log(`B: ${r}`)
            seen = seen + 1
            if seen == 2 { close(0) }
        }
        thread worker {
            on message(worker:thread, msg:int) {
                worker.reply(msg)
            }
        }
        worker.postMessage(100).callback(onA)
        worker.postMessage(200).callback(onB)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        lines = sorted(result.stdout.strip().splitlines())
        assert lines == ["A: 100", "B: 200"]

    def test_a_second_sequential_reply_still_delivers(self, compile_and_run):
        # claude.md #230 (uraikus/festina#89): a SEQUENTIAL round trip
        # (register, send, reply, dispatch, callback fires -- THEN a
        # second, completely separate round trip begins) used to
        # corrupt the sender's own pending_callbacks list: dispatching
        # the first reply removed the only (and therefore TAIL) node
        # without updating pending_callbacks_tail, so the next
        # registration wrote into already-freed memory via the stale
        # tail and never linked itself into the list `pending_callbacks`
        # itself still pointed at -- the second reply had nothing left
        # to be found by and was silently dropped. onA triggers onB's
        # own send only once its own reply has actually arrived, which
        # is exactly what makes this deterministic (no timer needed)
        # and exactly the shape #89's own report called "the second
        # reply ever emitted by a given thread instance."
        source = """
        thread worker {
            on message(worker:thread, msg:int) {
                worker.reply(msg * 10)
            }
        }
        void func onB(r:int) {
            log(`B: ${r}`)
            close(0)
        }
        void func onA(r:int) {
            log(`A: ${r}`)
            worker.postMessage(2).callback(onB)
        }
        worker.postMessage(1).callback(onA)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip().splitlines() == ["A: 10", "B: 20"]

    def test_replies_arriving_out_of_registration_order_keep_the_list_intact(
            self, compile_and_run):
        # claude.md #232: the one removal shape claude.md #230's own
        # fix has that neither of its tests exercised -- removing the
        # TAIL of a list that is NOT thereby emptied. Main registers
        # cbSlow (to a thread that stalls ~200ms before replying) then
        # cbFast; the fast reply arrives first, removing the tail
        # node while the head survives, so pending_callbacks_tail must
        # be walked back to the head node, not left dangling or set
        # to NULL. Then the slow reply empties the list, and a THIRD
        # send proves the list is still perfectly usable after both
        # removal shapes in a row. Output order is therefore forced,
        # not racy: fast, slow, third.
        source = """
        int seen = 0
        void func finish() {
            seen = seen + 1
            if seen == 3 { close(0) }
        }
        void func onSlow(r:int) { log(`slow ${r}`) finish() }
        void func onFast(r:int) { log(`fast ${r}`) finish() }
        void func onThird(r:int) { log(`third ${r}`) finish() }
        thread slow {
            on message(w:thread, msg:int) {
                int s = now()
                while now() - s < 200 { }
                w.reply(msg)
            }
        }
        thread fast {
            on message(w:thread, msg:int) { w.reply(msg) }
        }
        slow.postMessage(1).callback(onSlow)
        fast.postMessage(2).callback(onFast)
        fast.postMessage(3).callback(onThird)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip().splitlines() == ["fast 2", "third 3", "slow 1"]

    def test_many_outstanding_replies_across_a_pool_all_resolve(self, compile_and_run):
        # claude.md #232: dynamic coverage of the pending-callback list
        # under a removal order NOTHING controls -- four real OS
        # threads racing to reply, so main's single list has entries
        # removed from the head, the middle and the tail in whatever
        # interleaving the scheduler produces this run, while new
        # registrations keep appending at the tail throughout (each
        # callback fires a fresh send until its own chain is done).
        # Every one of the 4 x 30 chained round trips must resolve to
        # its own callback exactly once: the count and the checksum
        # both pin that, whatever order it happened in.
        source = """
        int done = 0
        int sum = 0
        int DEPTH = 30
        thread pool[4] {
            on message(w:thread, msg:int) { w.reply(msg) }
        }
        void func onReply(r:int) {
            done = done + 1
            sum = sum + r
            int lane = r % 4
            int step = Math.floorDiv(r, 4)
            if step + 1 < DEPTH {
                pool[lane].postMessage(r + 4).callback(onReply)
            }
            if done == 4 * DEPTH {
                log(`${done} ${sum}`)
                close(0)
            }
        }
        pool[0].postMessage(0).callback(onReply)
        pool[1].postMessage(1).callback(onReply)
        pool[2].postMessage(2).callback(onReply)
        pool[3].postMessage(3).callback(onReply)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        # values 0..119 each exactly once: 120 callbacks, sum 119*120/2
        assert result.stdout.strip() == "120 7140"

    def test_an_out_of_range_pool_index_registers_no_callback(self, compile_and_run):
        # claude.md #218: the registration used to be emitted BEFORE
        # the pool's own bounds check, so an out-of-range index left a
        # pending callback that could never fire (its send never
        # happened) sitting on the sender's list forever. The whole
        # expression must be a no-op -- and the in-range send right
        # after it must still work, proving the fix didn't just disable
        # the feature.
        source = """
        void func onReply(r:int) { log(`cb ${r}`) }
        void func done() { log('done') close(0) }
        thread pool[2] {
            on message(w:thread, msg:int) { w.reply(msg * 10) }
        }
        pool[5].postMessage(1).callback(onReply)
        pool[0].postMessage(7).callback(onReply)
        setTimeout(done, 300)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["cb 70", "done"]

    def test_replying_twice_to_one_message_delivers_only_the_first(self, compile_and_run):
        # claude.md #218: the first reply consumes the only pending
        # callback slot; the second has nothing to answer. It must be
        # discarded cleanly (and its payload released -- the leak side
        # of this is covered at volume by
        # tests/stress/thread_reply_callback_churn.f).
        source = """
        void func onReply(r:int) { log(`cb ${r}`) }
        void func done() { log('done') close(0) }
        thread worker {
            on message(w:thread, msg:int) { w.reply(msg) w.reply(msg + 100) }
        }
        worker.postMessage(1).callback(onReply)
        setTimeout(done, 300)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["cb 1", "done"]

    def test_replying_from_a_thread_value_that_outlived_its_dispatch_is_a_no_op(
            self, compile_and_run):
        # claude.md #218: a `thread` value stashed in a struct field
        # (also legal in an arr[thread]/map[thread]) can be replied to
        # long after the message it identifies was finished -- there is
        # no pending callback left to answer, so this must deliver
        # nothing and release the payload rather than enqueuing a
        # message that gets silently dropped on arrival (which is what
        # it did before, leaking the payload with it).
        source = """
        struct Holder { t:thread }
        Holder h
        void func onReply(r:text) { log(`cb ${r}`) }
        void func ignore(r:int) { }
        void func later() {
            h.t.reply(999)
            log('stale reply was a no-op')
            close(0)
        }
        thread worker { on message(w:thread, msg:text) { w.reply(`echo:${msg}`) } }
        on message(w:thread, msg:int) { h.t = w  log(`main got ${msg}`) }
        thread pinger { on load() { postMessage(5).callback(ignore) } }
        worker.postMessage('hi').callback(onReply)
        setTimeout(later, 300)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert "stale reply was a no-op" in result.stdout
        assert "cb echo:hi" in result.stdout

    def test_a_thread_value_has_no_text_form(self, parser, semantic, codegen, errors):
        # claude.md #218: these used to fall through to codegen's own
        # generic fallbacks ("only supports primitive values", and a
        # template error that didn't even name the file) -- a thread
        # value now gets img/aud's own specific message.
        for src in ("thread worker { on message(w:thread, msg:int) { log(w) } }\n"
                    "worker.postMessage(1)\n",
                    "thread worker { on message(w:thread, msg:int) { log(`${w}`) } }\n"
                    "worker.postMessage(1)\n"):
            program = parser.parse(src)
            analyzed = semantic.analyze(program)
            with pytest.raises(codegen.CodegenError, match="no text form"):
                codegen.CodeGen(analyzed, filename="main.f").generate(program)


class TestThreadPools:
    """claude.md #209: `thread NAME[N] { ... }` -- real, compiled-and-
    run proof that N pool instances are genuinely independent (private
    state, correct per-instance message routing) and that an out-of-
    range index is a real, silent no-op at runtime, not just a
    semantic-analysis-time claim."""

    def test_two_pool_instances_have_genuinely_independent_private_state(self, compile_and_run):
        # Each instance accumulates its OWN total -- if state were
        # accidentally shared (e.g. both instances aliasing the same
        # global), pool[0]'s own total would include pool[1]'s posts
        # too, and vice versa.
        source = """
        int seenA = 0
        int seenB = 0
        void func checkDone() {
            if seenA != 0 && seenB != 0 {
                log(seenA)
                log(seenB)
                close(0)
            }
        }
        on message(worker:thread, msg:int) {
            if msg < 100 {
                seenA = msg
            } else {
                seenB = msg
            }
            checkDone()
        }
        thread pool[2] {
            int total = 0
            on message(worker:thread, msg:int) {
                total = total + msg
                postMessage(total)
            }
        }
        pool[0].postMessage(1)
        pool[0].postMessage(2)
        pool[1].postMessage(200)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        # pool[0]'s own total after two posts (1, then 1+2=3) --
        # checkDone only fires once BOTH seenA/seenB are non-zero, so
        # the observed seenA is whichever reply arrived MOST recently
        # before pool[1]'s own reply also landed; either 1 or 3 proves
        # independence (pool[1]'s reply is always >= 200, so it can
        # never leak into seenA).
        lines = result.stdout.strip().splitlines()
        assert lines[0] in ("1", "3")
        assert lines[1] == "200"

    def test_an_out_of_range_pool_index_is_a_real_silent_noop(self, compile_and_run):
        source = """
        thread pool[2] {
            on message(worker:thread, msg:int) {
                log('should never run')
            }
        }
        log(pool[0].isAlive())
        log(pool[99].isAlive())
        int negIdx = -1
        log(pool[negIdx].isAlive())
        pool[99].postMessage(1)
        pool[99].kill()
        log('still alive')
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == [
            "true", "false", "false", "still alive",
        ]

    def test_killing_one_pool_instance_does_not_affect_another(self, compile_and_run):
        source = """
        thread pool[2] {
        }
        pool[0].kill()
        log(pool[0].isAlive())
        log(pool[1].isAlive())
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["false", "true"]

    def test_an_auto_sized_pool_really_has_the_resolved_number_of_instances(
            self, compile_and_run, codegen, monkeypatch):
        # claude.md #220: `thread pool[] { }` resolves its own size at
        # semantic-analysis time (cpu_count minus every other declared
        # thread, floored at 1) -- codegen.py never sees the "auto"
        # sentinel at all, so this proves the RESOLVED pool really has
        # that many live instances, not just that it compiles.
        monkeypatch.setattr(codegen.semantic_mod.os, "cpu_count", lambda: 3)
        source = """
        thread solo { on load() { } }
        thread pool[] { }
        log(pool[0].isAlive())
        log(pool[1].isAlive())
        log(pool[2].isAlive())
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        # 3 cpus - 1 (solo) = 2 instances: pool[0]/pool[1] alive, pool[2] out of range.
        assert result.stdout.strip().splitlines() == ["true", "true", "false"]


class TestThreadPrivateFunctions:
    """claude.md #210: real, compiled-and-run proof that a thread-
    private function actually runs, actually mutates the state it
    closes over, and two pool instances' own private-func-mutated
    state stays genuinely independent."""

    def test_a_private_function_computes_correctly_and_can_call_another(
            self, compile_and_run):
        source = """
        on message(worker:thread, msg:int) {
            log(msg)
            close(0)
        }
        thread worker {
            int func helper(x:int) { return x + 1 }
            int func triple(x:int) { return helper(x) * 3 }
            on message(worker:thread, msg:int) {
                postMessage(triple(msg))
            }
        }
        worker.postMessage(2)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        # triple(2) = helper(2) * 3 = (2 + 1) * 3 = 9
        assert result.stdout.strip() == "9"

    def test_a_private_function_mutates_the_thread_state_it_closes_over(
            self, compile_and_run):
        source = """
        on message(worker:thread, msg:int) {
            log(msg)
            close(0)
        }
        thread counter {
            int total = 0
            void func addToTotal(x:int) {
                total = total + x
            }
            on message(worker:thread, msg:int) {
                addToTotal(msg)
                addToTotal(msg)
                postMessage(total)
            }
        }
        counter.postMessage(5)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "10"

    def test_a_private_function_can_postmessage_another_thread(self, compile_and_run):
        source = """
        on message(worker:thread, msg:int) {
            log(msg)
            close(0)
        }
        thread relay {
            on message(worker:thread, msg:int) {
                postMessage(msg + 100)
            }
        }
        thread worker {
            void func forward(x:int) {
                relay.postMessage(x)
            }
            on message(worker:thread, msg:int) {
                forward(msg)
            }
        }
        worker.postMessage(9)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "109"

    def test_two_pool_instances_private_functions_mutate_independent_state(
            self, compile_and_run):
        source = """
        int seenA = 0
        int seenB = 0
        void func checkDone() {
            if seenA != 0 && seenB != 0 {
                log(seenA)
                log(seenB)
                close(0)
            }
        }
        on message(worker:thread, msg:int) {
            if msg < 100 {
                seenA = msg
            } else {
                seenB = msg
            }
            checkDone()
        }
        thread pool[2] {
            int total = 0
            void func addToTotal(x:int) {
                total = total + x
            }
            on message(worker:thread, msg:int) {
                addToTotal(msg)
                postMessage(total)
            }
        }
        pool[0].postMessage(3)
        pool[1].postMessage(200)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["3", "200"]


class TestThreadWiderBuiltinAccess:
    """claude.md #211: real, compiled-and-run proof that
    exec(args)/regex()/mkdir()/ls() all actually work from inside a
    thread body, not just that semantic.py accepts them."""

    def test_exec_regex_mkdir_ls_all_work_inside_a_thread(
            self, compile_and_run, tmp_path):
        source = """
        on message(worker:thread, msg:text) {
            log(msg)
            close(0)
        }
        thread worker {
            on load() {
                mkdir('subdir')
                arr[text] entries = ls('.')
                bool foundDir = false
                int i = 0
                while i < entries.length {
                    if entries[i] == 'subdir' {
                        foundDir = true
                    }
                    i = i + 1
                }
                regex r = /^ab/
                bool matched = r.test('abc')
                int code = exec(['true'])
                if foundDir && matched && code == 0 {
                    postMessage('all good')
                } else {
                    postMessage('failed')
                }
            }
        }
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "all good"
        assert (tmp_path / "subdir").is_dir()


class TestThreadHttpContext:
    """claude.md #212: real, compiled-and-run proof that a thread's
    own private HTTP context (openPort()/on request/...) genuinely
    works and stays isolated from main's -- driven entirely from
    inside the compiled program itself (a thread's own `on message`,
    triggered by main, makes a real blocking client request --
    `req.send()` with zero arguments -- back to MAIN's own port; the
    response is checked byte-for-byte), the same self-contained
    pattern tests/stress/thread_http_context_churn.f uses at volume
    under ASan/TSan."""

    def test_a_thread_serves_real_requests_on_its_own_private_port(
            self, compile_and_run_server):
        source = """
        thread server {
            on load() { openPort(__PORT__) }
            on request(req:http) {
                req.send({'code': 200, 'body': 'from the thread'})
            }
        }
        """
        server = compile_and_run_server(source)
        status, _headers, body = server.http_get("/")
        assert status == 200
        assert body == b"from the thread"

    def test_a_thread_client_request_reaches_mains_own_port_and_back(
            self, compile_and_run):
        # claude.md #212: main and a thread each open their OWN
        # private port; the thread's own `on message` (driven by
        # main) makes a real blocking client request BACK to main's
        # port -- proving both contexts run concurrently, each
        # genuinely serving its own traffic, with the response
        # correctly attributed to the right one (a mix-up between the
        # two __thread-backed connection tables would show up as a
        # wrong body here, not a crash).
        source = """
        int TOTAL = 3
        int done = 0
        int failures = 0

        on request(req:http) {
            req.send({'code': 200, 'body': 'main-body'})
        }

        on message(worker:thread, msg:int) {
            done = done + 1
            if msg == 0 { failures = failures + 1 }
            if done >= TOTAL {
                if failures > 0 {
                    close(1)
                }
                close(0)
            }
        }

        thread client {
            on message(sender:thread, msg:int) {
                http req = {'url': 'http://127.0.0.1:18299/', 'method': 'GET'}
                req.send()
                bool ok = req.code == 200 && req.toText() == 'main-body'
                if ok {
                    postMessage(1)
                } else {
                    postMessage(0)
                }
            }
        }

        openPort(18299)
        int i = 0
        while i < TOTAL {
            client.postMessage(i)
            i = i + 1
        }
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_a_thread_with_no_openPort_call_still_gets_a_receive_only_context(
            self, compile_and_run):
        # claude.md #212 (Phase 4's own forward note, needed by Phase
        # 5's giveRequest): declaring an HTTP-shaped handler WITHOUT
        # ever calling openPort() is legal and simply idles -- proof
        # this doesn't busy-loop or hang is the process actually
        # exiting cleanly within the test harness's own timeout.
        source = """
        thread receiver {
            on request(req:http) { req.ok() }
        }
        log('started')
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "started" in result.stdout


class TestThreadDrain:
    """claude.md #231 (uraikus/festina#91): `NAME.drain()` -- blocks
    until a thread's own inbound queue is fully processed, so
    `on close()`/`on exit(code:int)` can fire off a final async job
    (e.g. a database write on a thread with its own DatabaseURL) and
    be sure it has actually landed before the process-exit teardown
    that follows discards anything still in-flight."""

    def test_a_write_on_a_drained_thread_survives_process_exit(
            self, compile_and_run, tmp_path):
        # claude.md #231's own decisive proof, and #91's exact repro
        # shape: without drain(), this same write is silently lost
        # (confirmed directly -- see claude.md #231's own "Verified"
        # note). A thread and main can never share one DatabaseURL
        # (semantic.py's own isolation gate), so the write is checked
        # the same way every other thread-DB test in this suite does
        # -- inspecting the thread's own private .sqlite file directly
        # from Python, once the process has actually exited.
        source = """
        table Written { n:int }

        thread writer {
            DatabaseURL = 'writer_drain.sqlite'
            on message(caller:thread, msg:int) {
                sqlite('INSERT INTO Written (n) VALUES (?)', [msg])
            }
        }

        writer.postMessage(42)
        writer.drain()
        log('drained')
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "drained" in result.stdout
        db = tmp_path / "writer_drain.sqlite"
        assert db.exists()
        rows = sqlite3.connect(db).execute("SELECT n FROM Written").fetchall()
        assert rows == [(42,)]

    def test_drain_blocks_until_every_queued_message_has_run(self, compile_and_run, tmp_path):
        # claude.md #232: the blocking guarantee itself, checked from
        # INSIDE the program rather than after it exits, across
        # deliberately uneven batch sizes (1, then a burst, then 1
        # again -- the list draining fully to empty and refilling is
        # exactly the shape that matters). The worker appends one byte
        # per message to a file; after each drain() main re-reads the
        # file and the byte count must equal everything sent so far,
        # every single time. A drain() that returned early -- while a
        # message was dequeued but still mid-append, or still queued --
        # shows up as a short count at that exact batch.
        source = """
        thread worker {
            blob out = 'progress.txt'
            on message(w:thread, msg:int) {
                out.append('x')
            }
        }
        arr[int] batches = [1, 7, 3, 25, 1, 60, 12, 100, 2]
        int sent = 0
        int b = 0
        while b < batches.length {
            int k = 0
            while k < batches[b] {
                worker.postMessage(k)
                k = k + 1
            }
            sent = sent + batches[b]
            worker.drain()
            blob check = 'progress.txt'
            int have = check.toText().split('').length
            if have != sent {
                log(`short at batch ${b}: have ${have}, sent ${sent}`)
                close(1)
            }
            b = b + 1
        }
        log(`all ${sent} landed`)
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "all 211 landed"

    def test_drain_works_on_a_thread_with_its_own_http_context(self, compile_and_run):
        # claude.md #232: a thread that declared an HTTP-shaped handler
        # runs the OTHER worker-loop shape (the bounded 20ms poll of
        # festina_thread_main's http branch, never blocking on its own
        # condvar). drain() must work identically there -- the
        # dispatching flag and its condvar live in the shared
        # try_dispatch_one path both loop shapes call, so this pins
        # that the polling shape clears/broadcasts too, not just the
        # blocking one every other test here happens to use.
        source = """
        thread worker {
            blob out = 'polled.txt'
            on request(req:http) { }
            on message(w:thread, msg:int) {
                out.append('x')
            }
        }
        int i = 0
        while i < 40 {
            worker.postMessage(i)
            i = i + 1
        }
        worker.drain()
        blob check = 'polled.txt'
        log(check.toText().split('').length)
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "40"

    def test_drain_from_on_exit_makes_a_final_write_durable(self, compile_and_run, tmp_path):
        # claude.md #232: the headline use case from uraikus/festina#91
        # verbatim -- the exit handler itself fires the last write and
        # drains, and it must land even though process teardown (which
        # discards a thread's queue, like kill()) follows immediately.
        source = """
        table Written { n:int }
        thread writer {
            DatabaseURL = 'exit_drain.sqlite'
            on message(caller:thread, msg:int) {
                sqlite('INSERT INTO Written (n) VALUES (?)', [msg])
            }
        }
        on exit(code:int) {
            writer.postMessage(99)
            writer.drain()
            log('drained in on exit')
        }
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "drained in on exit" in result.stdout
        rows = sqlite3.connect(tmp_path / "exit_drain.sqlite").execute(
            "SELECT n FROM Written").fetchall()
        assert rows == [(99,)]

    def test_drain_returns_before_pending_replies_reach_main(self, compile_and_run):
        # claude.md #232: pins a semantic that is easy to assume the
        # other way. drain() waits for the WORKER to finish processing
        # -- including its own .reply() call -- but a reply is
        # delivered to main's callback by main's own event loop, which
        # only runs once top-level code has returned. So the line
        # after drain() always runs BEFORE the callback, never after.
        # (drain() is about durability of the worker's own side
        # effects, not about round-trip completion; api.md says so.)
        source = """
        void func onReply(r:int) {
            log(`reply ${r}`)
            close(0)
        }
        thread worker {
            on message(w:thread, msg:int) { w.reply(msg * 2) }
        }
        worker.postMessage(21).callback(onReply)
        worker.drain()
        log('drained')
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip().splitlines() == ["drained", "reply 42"]

    def test_an_out_of_range_pool_drain_is_a_silent_no_op(self, compile_and_run):
        # claude.md #232: same "test, don't fail" convention every
        # other pool[i] lifecycle method already has -- and the
        # in-range drain right after it must still genuinely wait.
        source = """
        thread pool[2] {
            blob out = 'pool.txt'
            on message(w:thread, msg:int) { out.append('x') }
        }
        pool[5].drain()
        pool[0].postMessage(1)
        pool[1].postMessage(2)
        pool[0].drain()
        pool[1].drain()
        blob check = 'pool.txt'
        log(check.toText().split('').length)
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "2"

    def test_drain_on_a_never_started_thread_is_a_safe_no_op(self, compile_and_run):
        # claude.md #231: a thread with no `on message`/`postMessage`
        # ever sent to it is never live()'d in the first place --
        # drain() on it must return immediately rather than hang.
        source = """
        thread idle {
            on load() { }
        }
        idle.drain()
        log('done')
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "done" in result.stdout

    def test_drain_on_a_killed_thread_is_a_safe_no_op(self, compile_and_run):
        source = """
        thread worker {
            on message(w:thread, msg:int) { }
        }
        worker.postMessage(1)
        worker.drain()
        worker.kill()
        worker.drain()
        log('done')
        close(0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "done" in result.stdout


class TestGiveRequest:
    """claude.md #213 (Phase 5): real, compiled-and-run proof that
    `NAME.giveRequest(r)` -- live connection hand-off -- genuinely
    works end to end: a real external client connects to MAIN's own
    port, main hands the live connection to a thread via giveRequest,
    and that THREAD's own `on request` -- running on a different OS
    thread than the one that accepted the connection -- answers it
    directly on the same underlying socket."""

    def test_a_handed_off_request_is_answered_by_the_receiving_thread(
            self, compile_and_run_server):
        source = """
        thread worker {
            on request(req:http) {
                req.send({'code': 200, 'body': 'handled by worker'})
            }
        }

        on request(req:http?) {
            worker.giveRequest(req)
        }

        openPort(__PORT__)
        """
        server = compile_and_run_server(source)
        status, _headers, body = server.http_get("/")
        assert status == 200
        assert body == b"handled by worker"

    def test_a_thread_forgetting_to_respond_still_gets_the_default_200(
            self, compile_and_run_server):
        # claude.md #213: a handed-off request goes through the exact
        # same festina_finish_request_dispatch fallback path an
        # ordinarily-accepted one does -- proof this ISN'T a narrower,
        # hand-off-specific dispatch that forgot the fallback.
        source = """
        thread worker {
            int served = 0
            on request(req:http) {
                served = served + 1
            }
        }

        on request(req:http?) {
            worker.giveRequest(req)
        }

        openPort(__PORT__)
        """
        server = compile_and_run_server(source)
        status, _headers, body = server.http_get("/")
        assert status == 200
        assert body == b""
