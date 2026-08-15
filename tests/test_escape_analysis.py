"""festina/escape_analysis.py -- claude.md #74's core safety-critical
logic (find_escaping_names), tested directly and exhaustively at the
unit level: parse a small function body, ask which names escape, check
the exact answer. This needs no C compiler and no semantic analysis at
all -- find_escaping_names is purely syntactic (see its own module
docstring), so these tests can use undeclared type names freely (Point,
Outer, ...) and still be a complete, fast check of the analysis itself.

Deliberately separate from tests/test_codegen.py's
TestAutomaticMemoryReclamation, which verifies the analysis's result
actually gets wired into correct, running compiled programs (real
free() calls landing in the right place, real programs still producing
correct output) -- this file is about the analysis in isolation, cheap
enough to cover every syntactic escaping/non-escaping pattern
exhaustively.
"""
import pytest


@pytest.fixture
def escape_analysis_mod():
    from tests.conftest import import_spec_module
    return import_spec_module("escape_analysis")


def _escaping_names(parser, escape_analysis_mod, body_source):
    """body_source is a function BODY only (no `func` wrapper) --
    wrapped here into a minimal void function so it parses as a real
    FuncDecl, whose .body is exactly the ast.Block find_escaping_names
    expects."""
    program = parser.parse(f"void func f() {{\n{body_source}\n}}")
    func_decl = program.body[0]
    return escape_analysis_mod.find_escaping_names(func_decl.body)


class TestSafeUses:
    """None of these should mark the candidate name as escaping."""

    def test_never_referenced_again(self, parser, escape_analysis_mod):
        assert "p" not in _escaping_names(parser, escape_analysis_mod, "Point p")

    def test_field_read(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "Point p\nlog(p.x)")
        assert "p" not in names

    def test_field_write(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "Point p\np.x = 5")
        assert "p" not in names

    def test_field_write_reading_the_variables_own_other_field(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "Point p\np.x = p.y")
        assert "p" not in names

    def test_array_index_read(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "arr[int] a = [1]\nlog(a[0])")
        assert "a" not in names

    def test_array_index_write(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "arr[int] a = [1]\na[0] = 2")
        assert "a" not in names

    def test_map_index_read(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "map[int] m = {}\nlog(m['x'])")
        assert "m" not in names

    def test_map_index_write(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "map[int] m = {}\nm['x'] = 2")
        assert "m" not in names

    def test_method_call_on_the_variable_itself(self, parser, escape_analysis_mod):
        source = (
            "void func cb(h:int, key:text) { log(key) }\n"
            "map[int] m = {}\n"
            "m.forEach(cb)"
        )
        names = _escaping_names(parser, escape_analysis_mod, source)
        assert "m" not in names

    def test_deep_field_chain(self, parser, escape_analysis_mod):
        # o.inner.value -- o is the .obj of the *inner* Member node
        # (o.inner), which is itself the .obj of the outer one -- still
        # safe at every level.
        names = _escaping_names(parser, escape_analysis_mod, "Outer o\nlog(o.inner.value)")
        assert "o" not in names

    def test_used_only_inside_a_nested_if(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "Point p\nif true {\n    log(p.x)\n}")
        assert "p" not in names

    def test_used_only_inside_a_nested_else(self, parser, escape_analysis_mod):
        names = _escaping_names(
            parser, escape_analysis_mod,
            "Point p\nif false {\n    log('a')\n} else {\n    log(p.x)\n}",
        )
        assert "p" not in names

    def test_used_only_inside_a_nested_while(self, parser, escape_analysis_mod):
        names = _escaping_names(
            parser, escape_analysis_mod,
            "Point p\nwhile p.x < 5 {\n    p.x = p.x + 1\n}",
        )
        assert "p" not in names

    def test_used_only_inside_a_nested_for(self, parser, escape_analysis_mod):
        names = _escaping_names(
            parser, escape_analysis_mod,
            "Point p\nfor int i = 0, i < p.x, i++ {\n    log(i)\n}",
        )
        assert "p" not in names

    def test_used_inside_a_ternary_test(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "Point p\nint x = p.x > 0 ? 1 : 2")
        assert "p" not in names


class TestEscapingUses:
    """Every one of these should mark the relevant name as escaping."""

    def test_returned_directly(self, parser, escape_analysis_mod):
        program = parser.parse("Point func f() {\n    Point p\n    return p\n}")
        names = escape_analysis_mod.find_escaping_names(program.body[0].body)
        assert "p" in names

    def test_returned_field_is_still_safe_for_the_struct_itself(self, parser, escape_analysis_mod):
        # Returning a primitive FIELD is not returning p's own address.
        program = parser.parse("int func f() {\n    Point p\n    return p.x\n}")
        names = escape_analysis_mod.find_escaping_names(program.body[0].body)
        assert "p" not in names

    def test_passed_as_a_call_argument(self, parser, escape_analysis_mod):
        source = "void func g(x:Point) { log(x.x) }\nPoint p\ng(p)"
        names = _escaping_names(parser, escape_analysis_mod, source)
        assert "p" in names

    def test_passed_as_a_call_argument_nested_in_another_expression(self, parser, escape_analysis_mod):
        source = "int func g(x:Point) { return x.x }\nPoint p\nint y = g(p) + 1"
        names = _escaping_names(parser, escape_analysis_mod, source)
        assert "p" in names

    def test_assigned_into_another_local_variable(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "Point p\nPoint q\nq = p")
        assert "p" in names

    def test_assigned_into_a_global(self, parser, escape_analysis_mod):
        program = parser.parse("Point g\nvoid func f() {\n    Point p\n    g = p\n}")
        names = escape_analysis_mod.find_escaping_names(program.body[1].body)
        assert "p" in names

    def test_bare_reassignment_marks_both_target_and_value(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "Point p\nPoint q\np = q")
        assert "p" in names  # p is the reassignment's TARGET -- its old value may be aliased elsewhere
        assert "q" in names  # q is the reassignment's VALUE

    def test_assigned_into_a_struct_field(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "Outer o\nPoint p\no.inner = p")
        assert "p" in names

    def test_stored_into_an_array_element(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "arr[Point] a = []\nPoint p\na[0] = p")
        assert "p" in names

    def test_stored_into_a_map_value(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "map[Point] m = {}\nPoint p\nm['k'] = p")
        assert "p" in names

    def test_element_of_an_array_literal(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "Point p\narr[Point] a = [p]")
        assert "p" in names

    def test_value_of_a_map_literal(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "Point p\nmap[Point] m = {'k': p}")
        assert "p" in names

    def test_key_of_a_map_literal(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "text k\nmap[int] m = {k: 1}")
        assert "k" in names

    def test_ternary_branches(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "Point p\nPoint q\nPoint r = true ? p : q")
        assert "p" in names
        assert "q" in names

    def test_logical_op_operand(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "bool b\nbool c = b && true")
        assert "b" in names

    def test_binop_operand(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "int func g(x:Point) { return 1 }\nPoint p\nint y = g(p) + 1")
        assert "p" in names

    def test_unary_operand(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "bool b\nbool c = !b")
        assert "b" in names

    def test_computed_index_expression_is_walked_too(self, parser, escape_analysis_mod):
        # Not a realistic type-correct program (the walker is purely
        # syntactic, no type checking) -- confirms it recurses into a
        # computed Member's index expression rather than skipping it.
        names = _escaping_names(parser, escape_analysis_mod, "int i\nmap[int] m = {}\nlog(m[i])")
        assert "i" in names

    def test_used_as_a_return_in_a_nested_if(self, parser, escape_analysis_mod):
        program = parser.parse(
            "Point func f(cond:bool) {\n"
            "    Point p\n"
            "    if cond {\n"
            "        return p\n"
            "    }\n"
            "    return p\n"
            "}"
        )
        names = escape_analysis_mod.find_escaping_names(program.body[0].body)
        assert "p" in names

    def test_template_interpolation(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "int func g(x:Point) { return 1 }\nPoint p\ntext s = `${g(p)}`")
        assert "p" in names

    def test_postfix_op_operand(self, parser, escape_analysis_mod):
        names = _escaping_names(parser, escape_analysis_mod, "int i = 0\ni++")
        assert "i" in names


class TestInterproceduralEscapingParams:
    """claude.md #74 stage 2: find_escaping_names's optional
    escaping_params argument. Unit-level, on find_escaping_names
    directly with a hand-built {func_name: set[int]} table -- the
    question of how codegen.py actually BUILDS that table (one function
    at a time, in program order, using each earlier function's own
    already-computed result) is covered separately in
    tests/test_codegen.py::TestAutomaticMemoryReclamation, against real
    generated IR and real compiled output. This class only checks the
    consuming half: given a table, does a Call argument position get
    exempted (or not) exactly as documented in escape_analysis.py's own
    module docstring."""

    def test_position_proven_safe_is_exempted(self, parser, escape_analysis_mod):
        program = parser.parse("void func f() {\n    Point p\n    g(p)\n}")
        names = escape_analysis_mod.find_escaping_names(
            program.body[0].body, escaping_params={"g": set()})
        assert "p" not in names

    def test_position_proven_escaping_still_escapes(self, parser, escape_analysis_mod):
        program = parser.parse("void func f() {\n    Point p\n    g(p)\n}")
        names = escape_analysis_mod.find_escaping_names(
            program.body[0].body, escaping_params={"g": {0}})
        assert "p" in names

    def test_unknown_callee_defaults_to_escaping(self, parser, escape_analysis_mod):
        # g simply isn't a key in the table at all (a builtin, a method,
        # or -- from codegen.py's own use of this -- a function this
        # program never got around to registering, e.g. it's still
        # being analyzed itself, mid-self-recursion) -- falls back to
        # the original unconditional rule, exactly as if escaping_params
        # were never passed.
        program = parser.parse("void func f() {\n    Point p\n    g(p)\n}")
        names = escape_analysis_mod.find_escaping_names(
            program.body[0].body, escaping_params={})
        assert "p" in names

    def test_escaping_params_omitted_matches_original_unconditional_behavior(
            self, parser, escape_analysis_mod):
        program = parser.parse("void func f() {\n    Point p\n    g(p)\n}")
        names = escape_analysis_mod.find_escaping_names(program.body[0].body)
        assert "p" in names

    def test_only_the_matching_argument_position_is_exempted(self, parser, escape_analysis_mod):
        # g's own analysis proved position 1 escapes but position 0
        # doesn't -- p (position 0) must be exempted, q (position 1)
        # must not, in the exact same call.
        program = parser.parse(
            "void func f() {\n    Point p\n    Point q\n    g(p, q)\n}")
        names = escape_analysis_mod.find_escaping_names(
            program.body[0].body, escaping_params={"g": {1}})
        assert "p" not in names
        assert "q" in names

    def test_method_call_never_consults_escaping_params(self, parser, escape_analysis_mod):
        # m.forEach(cb) -- the callee is a Member (m.forEach), not a
        # plain Identifier, so it can never match a key in the table no
        # matter what that table contains; cb still escapes via the
        # original unconditional rule. A table entry deliberately keyed
        # "forEach" (matching the method NAME, not a real function) is
        # included to prove the lookup genuinely never even considers
        # Member callees, not just that this particular table happens
        # not to have a matching key.
        program = parser.parse(
            "void func f() {\n    map[int] m = {}\n    Point cb\n    m.forEach(cb)\n}")
        names = escape_analysis_mod.find_escaping_names(
            program.body[0].body, escaping_params={"forEach": set()})
        assert "cb" in names

    def test_non_identifier_argument_at_an_exempted_position_is_still_walked_safely(
            self, parser, escape_analysis_mod):
        # g(p.x) at a proven-safe position -- p.x isn't a bare
        # Identifier, so the exemption's own isinstance guard doesn't
        # apply to it, but it's still just p's own Member.obj-safe field
        # read either way (see TestSafeUses.test_field_read) -- exists
        # to confirm this doesn't crash or behave differently than the
        # no-escaping_params case for a non-Identifier argument.
        program = parser.parse("void func f() {\n    Point p\n    g(p.x)\n}")
        names = escape_analysis_mod.find_escaping_names(
            program.body[0].body, escaping_params={"g": set()})
        assert "p" not in names

    def test_still_escapes_via_an_unrelated_use_despite_the_exempted_call(
            self, parser, escape_analysis_mod):
        # p is exempted at the g(p) call site specifically, but also
        # returned two lines later -- the exemption only stops THAT one
        # call site from being the reason p escapes; it must not paper
        # over p's own, entirely separate, genuinely escaping use.
        program = parser.parse(
            "Point func f() {\n    Point p\n    g(p)\n    return p\n}")
        names = escape_analysis_mod.find_escaping_names(
            program.body[0].body, escaping_params={"g": set()})
        assert "p" in names
