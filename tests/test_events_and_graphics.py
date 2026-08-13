"""claude.md #39 (graphics), #40 (events), #41 (logging)."""
import pytest


class TestGraphics:
    """claude.md #39: graphics are plain global function calls, no import."""

    @pytest.mark.parametrize("source", [
        "drawRect(0, 0, 100, 100)",
        "drawCircle(50, 50, 25)",
        "drawText('Hello', 20, 20)",
    ])
    def test_graphics_call_parses(self, parser, source):
        parser.parse(source)


class TestEvents:
    """claude.md #40: `on eventName(arguments) { }`."""

    def test_mouse_event_handler_parses(self, parser):
        source = """
        on mouse(x:int, y:int) {
            log(`Mouse moved over canvas on x: ${x}, y: ${y}`)
        }
        """
        parser.parse(source)

    def test_click_event_handler_parses(self, parser):
        source = """
        on click(x:int, y:int) {
            log(`Mouse clicked on canvas at ${x}, ${y}`)
        }
        """
        parser.parse(source)

    def test_event_handler_arguments_require_types(self, parser, errors):
        with pytest.raises(errors.CompileError):
            parser.parse("on click(x, y) {\n    log(x)\n}")


class TestLogging:
    """claude.md #41: log() is a built-in global, replacing console.log()."""

    def test_log_call_parses(self, parser):
        parser.parse("log('Hello')")

    def test_console_log_is_not_the_festina_convention(self, parser, semantic, errors):
        program = parser.parse("console.log('Hello')")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)
