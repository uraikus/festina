"""claude.md #37 (image), #39 (graphics), #40 (events).

Lexer/parser/semantic-level tests only -- see tests/test_codegen.py's
TestGraphics for the real compile-and-run end-to-end coverage,
including tests that actually open a window against a virtual X server
and verify mouseDown/mouseUp/mouse dispatch.
"""
import pytest


class TestImageType:
    """claude.md #37: img, declared from a path (claude.md #109 removed
    loadImage(), leaving the path form as the only spelling)."""

    def test_img_declaration_parses(self, parser):
        parser.parse("img profile = 'profile.png'")

    def test_img_is_a_valid_type(self, parser, semantic):
        program = parser.parse("img profile = 'profile.png'")
        semantic.analyze(program)

    def test_the_path_may_be_any_text_expression(self, parser, semantic):
        # claude.md #101/#109: a real load at run time, not a
        # compile-time resolution, so this is not restricted to literals.
        source = "text dir = 'art/'\nimg profile = dir + 'profile.png'"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_load_image_is_gone_and_says_what_to_use_instead(
            self, parser, semantic, errors):
        # claude.md #109: removed rather than aliased, and the error
        # names the replacement -- there is nothing else in the language
        # that would tell a reader where it went.
        program = parser.parse("img profile = loadImage('profile.png')")
        with pytest.raises(errors.CompileError, match="loadImage"):
            semantic.analyze(program)

    def test_the_load_image_error_shows_the_path_form(self, parser, semantic, errors):
        program = parser.parse("img profile = loadImage('profile.png')")
        with pytest.raises(errors.CompileError) as excinfo:
            semantic.analyze(program)
        assert "img sprite = 'sprite.png'" in str(excinfo.value)

    def test_a_non_text_path_is_still_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("img profile = 5")
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
        source = "img profile = 'a.png'\ndrawImage(profile, 0, 0)"
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
    """claude.md #40: `on eventName(arguments) { }` -- mouseDown/mouseUp/
    mouse/key/resize/close specifically must each declare a fixed signature
    matching claude.md's own examples exactly (the runtime registers
    each one as a fixed-signature function pointer -- see
    festina_runtime.h's doc comment)."""

    @pytest.mark.parametrize("name", ["mouseDown", "mouseUp"])
    def test_mouse_button_handlers_parse_and_analyze(self, parser, semantic, name):
        # claude.md #106: `on click` split into two, the same way
        # claude.md #98 split `on key`.
        source = f"on {name}(x:int, y:int) {{\n    log(x)\n}}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_click_is_no_longer_a_constrained_event(self, parser, semantic):
        # claude.md #106 removed `on click` outright rather than keeping
        # it as an alias. The name is not reserved any more, so it falls
        # through to the unconstrained case below: it analyzes fine with
        # any signature and simply never fires, because nothing in the
        # runtime registers it.
        source = "on click(a:text) {\n    log(a)\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_mouse_handler_parses_and_analyzes(self, parser, semantic):
        source = "on mouse(x:int, y:int) {\n    log(y)\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    @pytest.mark.parametrize("name", ["keyDown", "keyUp"])
    def test_key_handlers_parse_and_analyze(self, parser, semantic, name):
        # claude.md #98: `on key` split into two.
        source = f"on {name}(key:text) {{\n    log(key)\n}}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_resize_handler_parses_and_analyzes(self, parser, semantic):
        source = "on resize() {\n    log('resized')\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_close_handler_parses_and_analyzes(self, parser, semantic):
        source = "on close() {\n    log('closing')\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_unrecognized_event_name_is_unconstrained(self, parser, semantic):
        # claude.md #40 never restricts event names to a fixed set --
        # only mouseDown/mouseUp/mouse/key/resize/close get a signature
        # requirement, because only those have a runtime event source.
        source = "on somethingElse(a:text, b:bool) {\n    log(a)\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    @pytest.mark.parametrize("name", ["mouseDown", "mouseUp", "mouse"])
    def test_wrong_parameter_count_is_a_compile_error(self, parser, semantic, errors, name):
        source = f"on {name}(x:int) {{\n    log(x)\n}}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match=name):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", ["mouseDown", "mouseUp", "mouse"])
    def test_wrong_parameter_type_is_a_compile_error(self, parser, semantic, errors, name):
        source = f"on {name}(x:text, y:int) {{\n    log(x)\n}}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match=name):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", ["keyDown", "keyUp"])
    def test_key_wrong_parameter_count_is_a_compile_error(self, parser, semantic, errors, name):
        source = f"on {name}() {{\n    log('x')\n}}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match=name):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", ["keyDown", "keyUp"])
    def test_key_wrong_parameter_type_is_a_compile_error(self, parser, semantic, errors, name):
        source = f"on {name}(key:int) {{\n    log(key)\n}}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match=name):
            semantic.analyze(program)

    def test_bare_on_key_is_no_longer_a_recognized_event(self, parser, semantic, codegen):
        # claude.md #98 replaced `on key` outright rather than keeping it
        # as an alias. It still COMPILES -- claude.md #40 never
        # restricted event names -- but it is now ordinary dead code with
        # no runtime event source, exactly like `on somethingElse`. The
        # give-away is that it no longer forces the graphics runtime to
        # be linked in.
        source = "on key(key:text) {\n    log(key)\n}"
        program = parser.parse(source)
        analyzed = semantic.analyze(program)
        gen = codegen.CodeGen(analyzed, "main.f")
        gen.generate(program)
        assert gen.uses_graphics is False

    @pytest.mark.parametrize("name", ["resize", "close"])
    def test_resize_and_close_reject_any_parameters(self, parser, semantic, errors, name):
        # resize/close take no arguments at all -- claude.md #40's own
        # examples for both are `on resize()`/`on close()`.
        source = f"on {name}(x:int) {{\n    log(x)\n}}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match=name):
            semantic.analyze(program)


class TestClientSize:
    """claude.md #39: clientWidth/clientHeight -- read-only global ints
    reporting the canvas window's current size (borrowed from the DOM's
    Element.clientWidth/clientHeight, the closest analogue)."""

    def test_client_width_and_height_are_valid_int_identifiers(self, parser, semantic):
        source = "log(clientWidth)\nlog(clientHeight)"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_usable_inside_a_template_literal_in_an_event_handler(self, parser, semantic):
        source = "on resize() {\n    log(`size ${clientWidth}x${clientHeight}`)\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    @pytest.mark.parametrize("name", ["clientWidth", "clientHeight"])
    def test_assigning_to_it_is_a_compile_error(self, parser, semantic, errors, name):
        program = parser.parse(f"{name} = 100")
        with pytest.raises(errors.CompileError, match="read-only"):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", ["clientWidth", "clientHeight"])
    def test_declaring_a_variable_with_the_same_name_is_a_compile_error(self, parser, semantic, errors, name):
        # Scope.define's own "already declared" check catches this for
        # free, since clientWidth/clientHeight are pre-registered into
        # global_scope -- see semantic.py's _CLIENT_SIZE_GLOBALS.
        program = parser.parse(f"int {name} = 5")
        with pytest.raises(errors.CompileError, match="already declared"):
            semantic.analyze(program)


class TestScreenSize:
    """claude.md #139: screenWidth/screenHeight -- read-only global ints
    reporting the PHYSICAL display's own resolution, independent of
    whatever the current window's content size is (that's clientWidth/
    clientHeight, tested just above). Same registration shape as those
    two, just answering a different question -- see semantic.py's
    _SCREEN_SIZE_GLOBALS."""

    def test_screen_width_and_height_are_valid_int_identifiers(self, parser, semantic):
        source = "log(screenWidth)\nlog(screenHeight)"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_usable_inside_a_template_literal(self, parser, semantic):
        source = "log(`${screenWidth}x${screenHeight}`)"
        program = parser.parse(source)
        semantic.analyze(program)

    @pytest.mark.parametrize("name", ["screenWidth", "screenHeight"])
    def test_assigning_to_it_is_a_compile_error(self, parser, semantic, errors, name):
        # There is no setter for these -- a program cannot resize the
        # physical display it's running on, unlike clientWidth/
        # clientHeight's setClientWidth/setClientHeight below.
        program = parser.parse(f"{name} = 100")
        with pytest.raises(errors.CompileError, match="read-only"):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", ["screenWidth", "screenHeight"])
    def test_declaring_a_variable_with_the_same_name_is_a_compile_error(self, parser, semantic, errors, name):
        program = parser.parse(f"int {name} = 5")
        with pytest.raises(errors.CompileError, match="already declared"):
            semantic.analyze(program)


class TestSetClientSize:
    """claude.md #139: setClientWidth(int)/setClientHeight(int) -- the
    setters for clientWidth/clientHeight."""

    @pytest.mark.parametrize("name", ["setClientWidth", "setClientHeight"])
    def test_call_with_an_int_argument_is_valid(self, parser, semantic, name):
        program = parser.parse(f"{name}(400)")
        semantic.analyze(program)

    @pytest.mark.parametrize("name", ["setClientWidth", "setClientHeight"])
    def test_call_with_no_arguments_is_a_compile_error(self, parser, semantic, errors, name):
        program = parser.parse(f"{name}()")
        with pytest.raises(errors.CompileError, match=name):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", ["setClientWidth", "setClientHeight"])
    def test_call_with_a_text_argument_is_a_compile_error(self, parser, semantic, errors, name):
        program = parser.parse(f"{name}('400')")
        with pytest.raises(errors.CompileError, match=name):
            semantic.analyze(program)
