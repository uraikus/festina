"""claude.md #176: enum + typeof -- a tagged union over any type, plus a
type-introspection operator.

Lexer/parser/semantic-level tests only -- see tests/test_codegen.py's
TestEnums for the real compile-and-run end-to-end coverage of both
representations (pure-struct self-tagging, mixed heap-boxed), retain/
release, and the runtime `festina_fail` safety net.
"""
import pytest


class TestEnumDecl:
    """claude.md #176: `enum Name = Member1, Member2, ...`."""

    def test_pure_struct_enum_parses_and_analyzes(self, parser, semantic):
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        enum Shape = Circle, Square
        """
        semantic.analyze(parser.parse(source))

    def test_mixed_enum_parses_and_analyzes(self, parser, semantic):
        source = """
        struct User { id:int }
        enum Json = int, text, User
        """
        semantic.analyze(parser.parse(source))

    def test_forward_reference_to_a_struct_declared_later_resolves(self, parser, semantic):
        # claude.md #106/#176: enum names (and the structs they name)
        # are pre-scanned before any declaration's real analysis runs,
        # so declaration order never matters -- same as struct/table.
        source = """
        enum Shape = Circle, Square
        struct Circle { radius:int }
        struct Square { area:int }
        """
        semantic.analyze(parser.parse(source))

    def test_struct_field_can_name_an_enum_declared_later(self, parser, semantic):
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        struct Holder { shape:Shape }
        enum Shape = Circle, Square
        """
        semantic.analyze(parser.parse(source))

    def test_unknown_member_name_is_rejected(self, parser, semantic, errors):
        program = parser.parse("enum Shape = Circle, Square")
        with pytest.raises(errors.CompileError, match="unknown type 'Circle'"):
            semantic.analyze(program)

    def test_duplicate_member_is_rejected(self, parser, semantic, errors):
        source = """
        struct Circle { radius:int }
        enum Shape = Circle, Circle
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="more than once"):
            semantic.analyze(program)

    def test_enum_of_enum_is_rejected(self, parser, semantic, errors):
        source = """
        struct Circle { radius:int }
        enum Shape = Circle
        enum Outer = Shape
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="cannot have another enum"):
            semantic.analyze(program)

    def test_field_name_collision_across_members_is_rejected(self, parser, semantic, errors):
        # claude.md #176: `shape.radius` must resolve to a single,
        # unambiguous owning struct -- two members declaring the same
        # field name breaks that, so it's rejected at declaration time.
        source = """
        struct Circle { size:int }
        struct Square { size:int }
        enum Shape = Circle, Square
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="would be ambiguous"):
            semantic.analyze(program)

    def test_duplicate_enum_name_is_rejected(self, parser, semantic, errors):
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        enum Shape = Circle
        enum Shape = Square
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="already declared"):
            semantic.analyze(program)

    def test_enum_name_colliding_with_a_struct_is_rejected(self, parser, semantic, errors):
        source = """
        struct Circle { radius:int }
        struct Shape { x:int }
        enum Shape = Circle
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="already declared"):
            semantic.analyze(program)


class TestTypeof:
    """claude.md #176: `typeof <expr>` -- a prefix operator, always text."""

    def test_typeof_parses_on_any_expression(self, parser):
        parser.parse("typeof 5")
        parser.parse("typeof 'hi'")
        parser.parse("text n = 'x'\nlog(typeof n)")

    def test_typeof_infers_as_text(self, parser, semantic):
        source = "bool b = (typeof 5 == 'int')"
        semantic.analyze(parser.parse(source))

    def test_typeof_works_on_an_enum_typed_value(self, parser, semantic):
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        enum Shape = Circle, Square
        Circle c
        Shape shape = c
        bool isCircle = (typeof shape == 'Circle')
        """
        semantic.analyze(parser.parse(source))


class TestEnumCoercion:
    """claude.md #176: a member type coerces into its enum "pseudo type"
    -- check_assignable's one addition covers every position that
    already goes through it (var decl, function param/return, struct
    field, array/map element)."""

    def test_member_struct_assigns_to_enum_typed_var_decl(self, parser, semantic):
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        enum Shape = Circle, Square
        Circle c
        Shape shape = c
        """
        semantic.analyze(parser.parse(source))

    def test_member_struct_assigns_to_enum_typed_function_param(self, parser, semantic):
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        enum Shape = Circle, Square
        void func describe(shape:Shape) {
            log(typeof shape)
        }
        Circle c
        describe(c)
        """
        semantic.analyze(parser.parse(source))

    def test_member_struct_assigns_to_enum_typed_struct_field(self, parser, semantic):
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        enum Shape = Circle, Square
        struct Holder { shape:Shape }
        Circle c
        Holder h
        h.shape = c
        """
        semantic.analyze(parser.parse(source))

    def test_unrelated_struct_assigned_to_enum_typed_var_is_rejected(self, parser, semantic, errors):
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        struct Triangle { base:int }
        enum Shape = Circle, Square
        Triangle t
        Shape shape = t
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="cannot assign"):
            semantic.analyze(program)

    def test_enum_typed_value_does_not_implicitly_assign_back_to_a_member_type(
            self, parser, semantic, errors):
        # The coercion is one-directional (member -> enum), the same
        # way text -> blob/aud/img/color/font all are.
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        enum Shape = Circle, Square
        Circle c
        Shape shape = c
        Circle other = shape
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="cannot assign"):
            semantic.analyze(program)


class TestEnumFieldAccess:
    """claude.md #176: `shape.radius` -- scoped to pure-struct enums
    only; a mixed enum has no fields to speak of."""

    def test_field_access_on_pure_struct_enum_type_checks(self, parser, semantic):
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
        """
        semantic.analyze(parser.parse(source))

    def test_field_access_on_a_mixed_enum_is_rejected(self, parser, semantic, errors):
        source = """
        struct User { id:int }
        enum Json = int, text, User
        void func f(j:Json) {
            log(j.id)
        }
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="field access only works on an enum"):
            semantic.analyze(program)

    def test_field_not_declared_by_any_member_is_rejected(self, parser, semantic, errors):
        source = """
        struct Circle { radius:int }
        struct Square { area:int }
        enum Shape = Circle, Square
        void func f(shape:Shape) {
            log(shape.color)
        }
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="has no member with field"):
            semantic.analyze(program)
