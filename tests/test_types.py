"""claude.md #10-16, #25-28, #35-38: primitive types, type categories,
type resolution, null, arrays, structs, tables, blob/img/aud."""
import pytest


class TestPrimitiveTypes:
    """claude.md #10: int, float, bool, text, blob; null valid for all."""

    @pytest.mark.parametrize("source", [
        "int value = null",
        "text name = null",
        "bool enabled = null",
        "float percentage = 98.5",
        "int count = 100",
    ])
    def test_primitive_declaration_parses(self, parser, source):
        parser.parse(source)

    def test_null_preserves_underlying_type(self, parser, semantic, types_mod):
        program = parser.parse("int value = null")
        analyzed = semantic.analyze(program)
        decl = analyzed.symbols["value"]
        assert decl.type == types_mod.PrimitiveType("int")


class TestTypeCategories:
    """claude.md #11: distinct internal representation per category."""

    def test_categories_are_distinct_types(self, types_mod):
        prim = types_mod.PrimitiveType("int")
        struct = types_mod.StructType("User")
        table = types_mod.TableType("People")
        arr = types_mod.ArrayType(prim)
        assert type(prim) is not type(struct)
        assert type(prim) is not type(table)
        assert isinstance(arr, types_mod.ArrayType)
        assert arr.element == prim


class TestTypeResolution:
    """claude.md #12: arr[T] resolves T through symbol/type tables, not by
    naming convention."""

    def test_array_of_primitive_resolves(self, parser, semantic, types_mod):
        program = parser.parse("arr[int] values")
        analyzed = semantic.analyze(program)
        resolved = analyzed.symbols["values"].type
        assert resolved == types_mod.ArrayType(types_mod.PrimitiveType("int"))

    def test_array_of_struct_resolves(self, parser, semantic, types_mod):
        source = """
        struct User {
            name:text
        }
        arr[User] users
        """
        program = parser.parse(source)
        analyzed = semantic.analyze(program)
        resolved = analyzed.symbols["users"].type
        assert resolved == types_mod.ArrayType(types_mod.StructType("User"))

    def test_array_of_table_resolves(self, parser, semantic, types_mod):
        source = """
        table People {
            name:text
        }
        arr[People] people
        """
        program = parser.parse(source)
        analyzed = semantic.analyze(program)
        resolved = analyzed.symbols["people"].type
        assert resolved == types_mod.ArrayType(types_mod.TableType("People"))

    def test_nested_arrays_resolve(self, parser, semantic, types_mod):
        program = parser.parse("arr[arr[int]] matrix")
        analyzed = semantic.analyze(program)
        resolved = analyzed.symbols["matrix"].type
        expected = types_mod.ArrayType(types_mod.ArrayType(types_mod.PrimitiveType("int")))
        assert resolved == expected

    def test_type_category_is_not_inferred_from_naming(self, parser, semantic, types_mod):
        # `Table` in the name must not make the compiler treat this as a
        # TableType -- it is an ordinary, undeclared identifier.
        program = parser.parse("arr[PeopleTable] values")
        with pytest.raises(Exception):
            semantic.analyze(program)


class TestUnknownType:
    """claude.md #13: referencing an undeclared type is a compile-time
    error, e.g. "unknown type 'Person'"."""

    def test_unknown_type_raises(self, parser, semantic, errors):
        program = parser.parse("arr[Person] people")
        with pytest.raises(errors.CompileError) as exc_info:
            semantic.analyze(program)
        assert "Person" in str(exc_info.value)


class TestStructsVsTables:
    """claude.md #27, #28, #35: structs are in-memory; tables are
    SQLite-backed. They must remain distinct."""

    def test_struct_field_access_parses(self, parser):
        source = """
        struct User {
            id:int
            name:text
            active:bool
        }
        User user
        user.id = 1
        user.name = 'Patrick'
        user.active = true
        """
        parser.parse(source)

    def test_struct_does_not_produce_a_table_type(self, parser, semantic, types_mod):
        source = "struct User {\n    name:text\n}\nUser user"
        program = parser.parse(source)
        analyzed = semantic.analyze(program)
        assert isinstance(analyzed.symbols["user"].type, types_mod.StructType)
        assert not isinstance(analyzed.symbols["user"].type, types_mod.TableType)

    def test_table_declaration_is_collected_as_a_table(self, parser, semantic, types_mod):
        source = "table People {\n    id:int\n    name:text\n}"
        program = parser.parse(source)
        analyzed = semantic.analyze(program)
        assert "People" in analyzed.tables


class TestBlobImgAud:
    """claude.md #36 (blob), #37 (img), #38 (aud). img/aud get their own
    dedicated, much deeper test files (test_graphics.py/TestGraphics,
    test_audio.py/TestAudio) -- these two classes exist so every type
    claude.md #10 lists gets at least this file's baseline coverage
    too. blob doesn't have a dedicated file of its own (it has no
    builtin functions/methods, unlike img/aud), so its own coverage
    here goes a bit further: claude.md #36's only worked example is
    exactly this parser.parse() call below, and it used to be the
    *only* thing checked -- semantic analysis on that same source
    actually failed (a plain string literal infers as `text`, and
    `text`/`blob` were fully incompatible types with no exception),
    meaning claude.md's own example didn't compile and nothing here
    would have caught it."""

    def test_blob_declaration_parses_and_analyzes(self, parser, semantic):
        program = parser.parse("blob data = 'path/to/file'")
        semantic.analyze(program)

    def test_blob_and_text_are_mutually_comparable(self, parser, semantic):
        # blob and text share the identical runtime representation
        # (both `ptr` -- see codegen.py's _llvm_type), so == between
        # them is allowed, unlike a genuine type mismatch (e.g. int
        # vs. text).
        source = "blob data = 'x'\ntext t = 'x'\nlog(data == t)\nlog(data == 'x')"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_text_cannot_be_assigned_from_blob(self, parser, semantic, errors):
        # The reverse direction claude.md never shows -- text -> blob
        # is allowed (see above) because blob has no other way to be
        # constructed at all; blob -> text has no such justification.
        source = "blob data = 'x'\ntext t = data"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_int_cannot_be_assigned_to_blob(self, parser, semantic, errors):
        program = parser.parse("blob data = 5")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_img_declaration_parses(self, parser):
        parser.parse("img profile = loadImage('path/to/profile.png')")

    def test_img_passed_to_graphics_function_parses(self, parser):
        source = (
            "img profile = loadImage('path/to/profile.png')\n"
            "drawImage(profile, 0, 0)"
        )
        parser.parse(source)

    def test_aud_declaration_and_methods_parse(self, parser):
        # claude.md #100: a path declares the clip directly, and playback
        # is stopped by channel rather than by clip.
        source = """
        aud music = 'path/to/music.wav'
        music.play()
        music.playLoop(0)
        stopAudioPlayer(0)
        bool playing = music.isPlaying()
        """
        parser.parse(source)
