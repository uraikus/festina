"""claude.md #72: map[T] -- literals, indexed get/set, .forEach().

Lexer/parser/semantic-level tests only -- see tests/test_codegen.py's
TestMaps for the real compile-and-run end-to-end coverage of these same
sections.
"""
import pytest


class TestMapType:
    """claude.md #72: `map[T]` as a type -- mirrors arr[T] syntactically
    (parser.parse_type), but keys are always text, so only T (the value
    type) is spelled out."""

    def test_map_type_parses_as_a_var_decl(self, parser):
        parser.parse("map[int] scores = {}")

    def test_map_of_text_parses(self, parser):
        parser.parse("map[text] names = {}")

    def test_nested_map_of_struct_parses(self, parser, semantic):
        source = "struct P { x:int }\nmap[P] points = {}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_map_as_function_param_and_return_type_parses(self, parser, semantic):
        source = "map[int] func identity(m:map[int]) {\n    return m\n}\nidentity({})"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_map_of_array_is_a_compile_error(self, parser, semantic, errors):
        # claude.md #72: a map value is stored in one fixed-size slot,
        # which an arr[T] value (a 16-byte {length, data} pair) doesn't
        # fit in -- see types.MapType's own doc comment.
        program = parser.parse("map[arr[int]] bad = {}")
        with pytest.raises(errors.CompileError, match="map values cannot be"):
            semantic.analyze(program)

    def test_map_of_map_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("map[map[int]] bad = {}")
        with pytest.raises(errors.CompileError, match="map values cannot be"):
            semantic.analyze(program)


class TestMapLiteral:
    """claude.md #72: { key: value, ... } -- every key must be text."""

    def test_empty_map_literal_parses(self, parser, semantic):
        program = parser.parse("map[int] m = {}")
        semantic.analyze(program)

    def test_literal_with_string_keys_parses(self, parser, semantic):
        program = parser.parse("map[int] m = {'a': 1, 'b': 2}")
        semantic.analyze(program)

    def test_literal_with_a_variable_key_parses(self, parser, semantic):
        # claude.md #72: an unquoted identifier key is a reference to
        # that variable's own text value -- NOT bareword-as-string-name
        # shorthand the way a plain JS object literal has.
        source = "text npc2Id = 'npc2'\nmap[int] m = {npc2Id: 15}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_literal_infers_map_type(self, parser, semantic):
        program = parser.parse("map[int] m = {'a': 1}")
        semantic.analyze(program)

    def test_non_text_key_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("map[int] m = {5: 1}")
        with pytest.raises(errors.CompileError, match="map key must be text"):
            semantic.analyze(program)

    def test_bool_key_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("map[int] m = {true: 1}")
        with pytest.raises(errors.CompileError, match="map key must be text"):
            semantic.analyze(program)

    def test_duplicate_string_literal_key_is_a_compile_error(self, parser, semantic, errors):
        # Both keys are plain string literals here -- the duplicate is
        # knowable right now, at zero runtime cost, so it's a compile
        # error rather than silent "last value wins" (see
        # test_codegen.py's TestMaps for the still-legal runtime-only
        # case, where one of the two colliding keys is a variable).
        program = parser.parse("map[int] m = {'a': 1, 'b': 2, 'a': 3}")
        with pytest.raises(errors.CompileError, match="duplicate map key 'a'"):
            semantic.analyze(program)

    def test_a_literal_key_and_a_variable_key_are_not_flagged_as_duplicates(self, parser, semantic):
        # The variable's text value isn't known at compile time, so this
        # can't be (and isn't) rejected here -- claude.md #72's own
        # example relies on exactly this shape (npc2Id might or might
        # not collide with 'npc1' at runtime).
        source = "text npc2Id = 'npc1'\nmap[int] m = {'npc1': 10, npc2Id: 15}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_mixed_value_types_in_a_map_literal_is_a_compile_error(self, parser, semantic, errors):
        # claude.md #153: a real, pre-existing gap first noticed (and
        # left open) by claude.md #151's own testing -- a mismatched
        # value literal like this used to pass semantic analysis
        # silently and reach codegen, which then emitted invalid LLVM
        # IR (a raw i64 where a ptr was required, or vice versa)
        # instead of a clean compile error. Deliberately no declared
        # target type here (a bare `var`-less literal has none to check
        # against either) -- this is MapLit's own inference catching it,
        # the same as ArrayLit's mirrored check just above this class.
        program = parser.parse("map[int] m = {'a': 1, 'b': 'two'}")
        with pytest.raises(errors.CompileError, match="map literal values must all be the same type"):
            semantic.analyze(program)

    def test_null_values_do_not_count_as_a_mismatch(self, parser, semantic):
        # Mirrors ArrayLit's own null-tolerant behavior (e.g. `[5, null]`
        # against a declared arr[int]) -- a null entry carries no
        # concrete type to conflict with anything.
        program = parser.parse("map[text] m = {'a': null, 'b': 'x', 'c': null}")
        semantic.analyze(program)


class TestMapIndexing:
    """claude.md #72: npcHealths['npc1'] / npcHealths[key] -- read and
    write, both via computed Member access (the same grammar arr[T]
    indexing already uses, dispatched on the receiver's type)."""

    def test_read_with_a_string_literal_key_parses(self, parser, semantic):
        source = "map[int] m = {'a': 1}\nlog(m['a'])"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_read_with_a_variable_key_parses(self, parser, semantic):
        source = "map[int] m = {'a': 1}\ntext k = 'a'\nlog(m[k])"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_read_infers_the_maps_value_type(self, parser, semantic):
        source = "map[int] m = {'a': 1}\nint x = m['a']"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_int_key_is_a_compile_error(self, parser, semantic, errors):
        source = "map[int] m = {'a': 1}\nlog(m[5])"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="map key must be text"):
            semantic.analyze(program)

    def test_write_to_a_new_key_parses(self, parser, semantic):
        source = "map[int] m = {}\nm['a'] = 5"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_write_to_an_existing_key_parses(self, parser, semantic):
        source = "map[int] m = {'a': 1}\nm['a'] = 5"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_write_with_a_variable_key_parses(self, parser, semantic):
        source = "map[int] m = {}\ntext k = 'a'\nm[k] = 5"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_write_with_wrong_value_type_is_a_compile_error(self, parser, semantic, errors):
        source = "map[int] m = {}\nm['a'] = 'not an int'"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_indexing_a_non_map_non_array_is_a_compile_error(self, parser, semantic, errors):
        source = "int x = 5\nlog(x['a'])"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="cannot index into"):
            semantic.analyze(program)


class TestMapForEach:
    """claude.md #72: map.forEach(callback) -- callback must be an
    already-declared function taking exactly (value, key:text) and
    returning nothing, checked structurally the same way setTimeout's
    callback is."""

    def test_forEach_with_a_matching_callback_parses(self, parser, semantic):
        source = """
        void func logHealth(h:int, key:text) {
            log(key)
        }
        map[int] m = {'a': 1}
        m.forEach(logHealth)
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_forEach_argument_must_be_a_declared_function(self, parser, semantic, errors):
        source = "map[int] m = {'a': 1}\nm.forEach(5)"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_forEach_argument_must_be_an_identifier_not_an_expression(self, parser, semantic, errors):
        source = """
        void func f(h:int, key:text) { log(key) }
        map[int] m = {'a': 1}
        m.forEach(f())
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_forEach_callback_wrong_first_param_type_is_a_compile_error(self, parser, semantic, errors):
        source = """
        void func f(h:text, key:text) { log(h) }
        map[int] m = {'a': 1}
        m.forEach(f)
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="first parameter"):
            semantic.analyze(program)

    def test_forEach_callback_wrong_second_param_type_is_a_compile_error(self, parser, semantic, errors):
        source = """
        void func f(h:int, key:int) { log(h) }
        map[int] m = {'a': 1}
        m.forEach(f)
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="second parameter"):
            semantic.analyze(program)

    def test_forEach_callback_wrong_param_count_is_a_compile_error(self, parser, semantic, errors):
        source = """
        void func f(h:int) { log(h) }
        map[int] m = {'a': 1}
        m.forEach(f)
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_forEach_callback_must_return_nothing(self, parser, semantic, errors):
        source = """
        int func f(h:int, key:text) { return h }
        map[int] m = {'a': 1}
        m.forEach(f)
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_forEach_on_a_non_map_is_a_compile_error(self, parser, semantic, errors):
        source = """
        void func f(h:int, key:text) { log(h) }
        arr[int] a = [1, 2]
        a.forEach(f)
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestAmorPrefix:
    """claude.md #156: `amor map[T]` / `amor arr[T]` -- an "amortized"
    growth modifier, composing with `const` the same way (`const amor
    map[text] m`). Parser/semantic-level coverage only -- see
    tests/test_codegen.py::TestAmorMap for real compile-and-run
    coverage of amor map[T]'s own observable behavior."""

    def test_amor_map_parses_as_a_var_decl(self, parser):
        parser.parse("amor map[int] scores = {}")

    def test_amor_arr_parses_as_a_var_decl(self, parser):
        parser.parse("amor arr[int] xs = []")

    def test_const_amor_map_parses(self, parser):
        parser.parse("const amor map[text] m = {'x': 'y'}")

    def test_amor_map_resolves_to_an_amortized_maptype(self, parser, semantic, types_mod):
        program = parser.parse("amor map[int] m = {'a': 1}")
        decl = program.body[0]
        resolved = semantic.resolve_type_name(decl.type_expr, {}, {})
        assert resolved == types_mod.MapType(types_mod.PrimitiveType("int"), amortized=True)
        assert resolved != types_mod.MapType(types_mod.PrimitiveType("int"))

    def test_amor_must_be_followed_by_arr_or_map(self, parser):
        with pytest.raises(Exception):
            parser.parse("amor int x = 1")

    def test_amor_map_without_an_initializer_is_a_compile_error(self, parser, semantic, errors):
        # claude.md #156: unlike plain map[T] (which starts "empty" via
        # a real immortal static header -- see codegen.py's
        # _global_var_defs), amor map[T] never got that treatment (a
        # deliberate scope boundary: it always heap-allocates through
        # the same generic path blob/img/aud/etc. use, which needs a
        # real value to store) -- requiring an initializer here is
        # what keeps that boundary from being reachable as an
        # uninitialized-pointer bug instead of a clear compile error.
        program = parser.parse("amor map[int] m")
        with pytest.raises(errors.CompileError, match="requires an initializer"):
            semantic.analyze(program)

    def test_amor_map_literal_rejects_a_mismatched_value_type(self, parser, semantic, errors):
        program = parser.parse("amor map[int] m = {'a': 1, 'b': 'two'}")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_amor_map_literal_with_a_variable_key_parses(self, parser, semantic):
        source = "text npc2Id = 'npc2'\namor map[int] m = {'npc1': 10, npc2Id: 15}"
        program = parser.parse(source)
        semantic.analyze(program)
