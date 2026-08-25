"""claude.md #159: text.toStruct(StructName) / text.toArr(ElementType).

JSON deserialization -- the reverse of .toText()'s own JSON rendering
(claude.md #114). Parser/semantic coverage (the type-as-argument
grammar, the v1 scalars-only scope restriction) and real
compile-and-run coverage (happy path, case-insensitive/lenient
parsing, and the throw-on-malformed-input pairing with claude.md #157)
live together in this one file.
"""
import pytest


class TestParsing:
    def test_to_struct_parses(self, parser):
        parser.parse("""
        struct Foo { id:int }
        Foo f = '{}'.toStruct(Foo)
        """)

    def test_to_arr_parses(self, parser):
        parser.parse("arr[int] xs = '[]'.toArr(int)")

    def test_to_arr_of_text_parses(self, parser):
        parser.parse("arr[text] xs = '[]'.toArr(text)")


class TestSemanticErrors:
    def test_to_struct_receiver_must_be_text(self, parser, semantic, errors):
        program = parser.parse("""
        struct Foo { id:int }
        int n = 5
        Foo f = n.toStruct(Foo)
        """)
        with pytest.raises(errors.CompileError, match="can only be called on text"):
            semantic.analyze(program)

    def test_to_struct_argument_must_be_a_struct_name(self, parser, semantic, errors):
        program = parser.parse("int n = '5'.toStruct(int)")
        with pytest.raises(errors.CompileError, match="must be a struct name"):
            semantic.analyze(program)

    def test_to_arr_element_type_must_be_scalar(self, parser, semantic, errors):
        program = parser.parse("""
        struct Foo { id:int }
        arr[Foo] xs = '[]'.toArr(Foo)
        """)
        with pytest.raises(errors.CompileError, match="int/float/bool/text"):
            semantic.analyze(program)

    def test_to_struct_rejects_a_nested_arr_field(self, parser, semantic, errors):
        # claude.md #159's own v1 scope cut.
        program = parser.parse("""
        struct Bag { xs:arr[int] }
        Bag b = '{}'.toStruct(Bag)
        """)
        with pytest.raises(errors.CompileError, match="doesn't support field"):
            semantic.analyze(program)

    def test_to_struct_resolves_to_the_struct_type(self, parser, semantic, types_mod):
        program = parser.parse("""
        struct Foo { id:int }
        Foo f = '{}'.toStruct(Foo)
        """)
        semantic.analyze(program)

    def test_to_arr_resolves_to_arr_of_element_type(self, parser, semantic):
        program = parser.parse("arr[int] xs = '[]'.toArr(int)")
        semantic.analyze(program)


class TestRuntimeBehavior:
    def test_to_struct_parses_every_scalar_field_type(self, compile_and_run):
        source = """
        struct Person { id:int name:text active:bool score:float }
        Person p = '{"id": 7, "name": "Ada", "active": true, "score": 9.5}'.toStruct(Person)
        log(p)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == '{"id":7,"name":"Ada","active":true,"score":9.5}'

    def test_to_arr_parses_int_float_bool_text(self, compile_and_run):
        result = compile_and_run("""
        log('[1,2,3,4,5]'.toArr(int))
        log('[1.5,2.5]'.toArr(float))
        log('[true,false]'.toArr(bool))
        log('["a","b","c"]'.toArr(text))
        """)
        assert result.stdout.splitlines() == [
            "[1,2,3,4,5]", "[1.5,2.5]", "[true,false]", '["a","b","c"]',
        ]

    def test_unknown_json_keys_are_silently_skipped(self, compile_and_run):
        source = """
        struct Person { id:int }
        Person p = '{"id": 1, "extra": {"nested": [1,2,3]}, "more": "x"}'.toStruct(Person)
        log(p)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == '{"id":1}'

    def test_missing_json_keys_keep_the_zero_value(self, compile_and_run):
        source = """
        struct Person { id:int name:text }
        Person p = '{}'.toStruct(Person)
        log(p)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == '{"id":0,"name":null}'

    def test_keys_match_case_insensitively(self, compile_and_run):
        source = """
        struct Person { id:int name:text }
        Person p = '{"ID": 1, "NaMe": "x"}'.toStruct(Person)
        log(p)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == '{"id":1,"name":"x"}'

    def test_duplicate_key_last_one_wins(self, compile_and_run):
        source = """
        struct Person { id:int }
        Person p = '{"id": 1, "id": 2}'.toStruct(Person)
        log(p)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == '{"id":2}'

    def test_json_null_becomes_the_field_type_own_null(self, compile_and_run):
        source = """
        struct Person { id:int name:text }
        Person p = '{"id": null, "name": null}'.toStruct(Person)
        log(p.id)
        log(p.name)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines()[1] == ""  # null text prints as empty

    def test_malformed_json_throws_caught_by_try_catch(self, compile_and_run):
        source = """
        struct Person { id:int name:text }
        try {
            Person p = 'not json'.toStruct(Person)
            log('unreachable')
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            "caught: expected '{' at position 0, found 'n'", "done",
        ]

    def test_type_mismatch_throws(self, compile_and_run):
        source = """
        try {
            arr[int] xs = '[1,2,"three"]'.toArr(int)
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.strip().startswith("caught:")

    def test_trailing_data_throws(self, compile_and_run):
        source = """
        struct Person { id:int }
        try {
            Person p = '{"id":1}extra'.toStruct(Person)
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        """
        result = compile_and_run(source)
        assert "trailing" in result.stdout

    def test_uncaught_parse_error_behaves_like_fail(self, compile_and_run):
        result = compile_and_run("""
        struct Person { id:int }
        Person p = 'nope'.toStruct(Person)
        """)
        assert result.returncode == 1
        assert result.stderr.strip().startswith("fail:")

    def test_successful_parses_leak_nothing_in_a_loop(self, compile_and_run):
        # claude.md #159's own leak caveat is strictly error-path-only
        # (see runtime/festina_runtime.c's own comment) -- not a
        # Valgrind run itself (this suite doesn't do that here), but
        # pins the OBSERVABLE happy-path behavior across many repeated
        # calls that the caveat's own claim depends on.
        source = """
        struct Person { id:int name:text }
        int i = 0
        while (i < 30) {
            i = i + 1
            Person p = '{"id": 1, "name": "x"}'.toStruct(Person)
            arr[int] xs = '[1,2,3]'.toArr(int)
        }
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "done"
