"""claude.md #37 (image), #39 (graphics), #40 (events).

Lexer/parser/semantic-level tests only -- see tests/test_codegen.py's
TestGraphics for the real compile-and-run end-to-end coverage,
including tests that actually open a window against a virtual X server
and verify click/mouse dispatch.
"""
import pytest


class TestImageType:
    """claude.md #37: img, loadImage()."""

    def test_img_declaration_parses(self, parser):
        parser.parse("img profile = loadImage('profile.png')")

    def test_img_is_a_valid_type(self, parser, semantic):
        program = parser.parse("img profile = loadImage('profile.png')")
        semantic.analyze(program)

    def test_load_image_wrong_argument_count_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("img profile = loadImage()")
        with pytest.raises(errors.CompileError, match="loadImage"):
            semantic.analyze(program)

    def test_load_image_non_text_argument_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("img profile = loadImage(5)")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestGraphicsFunctions:
    """claude.md #39: drawRect/drawCircle/drawText/drawImage -- each
    checked against the exact signature its own worked example uses."""

    def test_draw_rect_parses_and_analyzes(self, parser, semantic):
        program = parser.parse("drawRect(0, 0, 100, 100)")
        semantic.analyze(program)

    def test_draw_circle_parses_and_analyzes(self, parser, semantic):
        program = parser.parse("drawCircle(50, 50, 25)")
        semantic.analyze(program)

    def test_draw_text_parses_and_analyzes(self, parser, semantic):
        program = parser.parse("drawText('Hello', 20, 20)")
        semantic.analyze(program)

    def test_draw_image_parses_and_analyzes(self, parser, semantic):
        source = "img profile = loadImage('a.png')\ndrawImage(profile, 0, 0)"
        program = parser.parse(source)
        semantic.analyze(program)

    @pytest.mark.parametrize("call,expected_count", [
        ("drawRect(0, 0, 100)", 4),
        ("drawCircle(50, 50)", 3),
        ("drawText('Hello', 20)", 3),
    ])
    def test_wrong_argument_count_is_a_compile_error(self, parser, semantic, errors, call, expected_count):
        program = parser.parse(call)
        with pytest.raises(errors.CompileError, match=str(expected_count)):
            semantic.analyze(program)

    def test_draw_rect_non_int_argument_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("drawRect('x', 0, 100, 100)")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_draw_text_first_argument_must_be_text(self, parser, semantic, errors):
        program = parser.parse("drawText(5, 20, 20)")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_draw_image_first_argument_must_be_img(self, parser, semantic, errors):
        program = parser.parse("drawImage('not an image', 0, 0)")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestEventHandlers:
    """claude.md #40: `on eventName(arguments) { }` -- "click" and
    "mouse" specifically must declare (x:int, y:int), matching
    claude.md's own examples exactly (the runtime registers them as a
    fixed-signature function pointer -- see
    festina_runtime.h's doc comment)."""

    def test_click_handler_parses_and_analyzes(self, parser, semantic):
        source = "on click(x:int, y:int) {\n    log(x)\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_mouse_handler_parses_and_analyzes(self, parser, semantic):
        source = "on mouse(x:int, y:int) {\n    log(y)\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_unrecognized_event_name_is_unconstrained(self, parser, semantic):
        # claude.md #40 never restricts event names to a fixed set --
        # only "click"/"mouse" get a signature requirement, because
        # only those two have a runtime event source at all.
        source = "on somethingElse(a:text, b:bool) {\n    log(a)\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    @pytest.mark.parametrize("name", ["click", "mouse"])
    def test_wrong_parameter_count_is_a_compile_error(self, parser, semantic, errors, name):
        source = f"on {name}(x:int) {{\n    log(x)\n}}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match=name):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", ["click", "mouse"])
    def test_wrong_parameter_type_is_a_compile_error(self, parser, semantic, errors, name):
        source = f"on {name}(x:text, y:int) {{\n    log(x)\n}}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match=name):
            semantic.analyze(program)
