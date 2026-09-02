"""claude.md #237: running a compiled .wasm in a browser.

runtime/wasm/festina_wasi_browser.js is a dependency-free WASI Preview 1
host written for the browser; runtime/wasm/browser.html runs a program
on it inside a Web Worker. Two tiers prove it:

1. The host itself, under Node (`runtime/wasm/run_wasi_js.mjs`) --
   exactly the JavaScript a browser executes, with the real directory
   loaded into its in-memory filesystem and written back afterwards.
   Same skip rule as compile_and_run_wasm (no wasm toolchain / no Node
   -> skip; FESTINA_STRICT_DEPS=1 -> fail).

2. A real browser: browser.html served over HTTP and opened in headless
   Chromium through Playwright, the program's output read back from
   `window.festinaResult`. Skips when Playwright or its Chromium isn't
   installed (the linux CI job installs both, and FESTINA_STRICT_DEPS=1
   turns the skip into a failure there).
"""
import http.server
import os
import shutil
import socket
import subprocess
import threading

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WASM_DIR = os.path.join(_ROOT, "runtime", "wasm")
_HOST_FILES = ("festina_wasi_browser.js", "festina_wasi_worker.js", "browser.html")


@pytest.fixture
def compile_wasm(tmp_path, cli_mod):
    """Compiles a program to tmp_path/program.wasm (skipping, or failing
    under FESTINA_STRICT_DEPS, exactly as compile_and_run_wasm does)
    and returns its path."""
    clang = shutil.which("clang")
    if not clang or not cli_mod._wasm_toolchain_ok(clang):
        missing = ("no working wasm32-wasi clang on PATH -- needs wasi-libc and "
                   "clang's wasm32 compiler-rt installed (see wasm.md)")
        if os.environ.get("FESTINA_STRICT_DEPS"):
            pytest.fail(missing)
        pytest.skip(missing)

    def _compile(source):
        src_path = tmp_path / "main.f"
        src_path.write_text(source, encoding="utf-8")
        out_path = tmp_path / "program.wasm"
        cli_mod.compile_file(str(src_path), str(out_path), cc=clang, target="wasm32-wasi")
        return out_path

    return _compile


@pytest.fixture
def run_in_js_host(tmp_path, compile_wasm):
    """Tier 1: the browser host under Node. Returns (CompletedProcess,
    sandbox dir) -- the sandbox is what the program saw as "/"."""
    node = shutil.which("node")
    if not node:
        missing = "Node.js isn't on PATH -- needed to run the browser WASI host outside a browser"
        if os.environ.get("FESTINA_STRICT_DEPS"):
            pytest.fail(missing)
        pytest.skip(missing)

    def _run(source, files=None):
        wasm = compile_wasm(source)
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir(exist_ok=True)
        for name, content in (files or {}).items():
            target = sandbox / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        result = subprocess.run(
            [node, "--no-warnings", os.path.join(_WASM_DIR, "run_wasi_js.mjs"), str(wasm), str(sandbox)],
            cwd=tmp_path, capture_output=True, text=True, timeout=60, encoding="utf-8")
        return result, sandbox

    return _run


class TestBrowserHostUnderNode:
    def test_hello_world(self, run_in_js_host):
        result, _ = run_in_js_host("log('hello from the browser host')")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "hello from the browser host"

    def test_exit_code_and_stderr_come_through(self, run_in_js_host):
        result, _ = run_in_js_host("log('before')\nfail('nope')")
        assert result.returncode == 1
        assert result.stdout.strip() == "before"
        assert "nope" in result.stderr

    def test_close_code_is_the_exit_code(self, run_in_js_host):
        result, _ = run_in_js_host("close(7)")
        assert result.returncode == 7

    def test_files_directories_and_the_sandbox_round_trip(self, run_in_js_host):
        # blob write/append/read, mkdir, ls -- every filesystem call a
        # Festina program has -- against the in-memory filesystem, with
        # the results written back to the real sandbox directory.
        result, sandbox = run_in_js_host("""
        blob seeded = 'seed.txt'
        log(seeded.toText())
        blob notes = 'notes.txt'
        notes.write('first')
        notes.append('|second')
        blob again = 'notes.txt'
        log(again.toText())
        mkdir('sub')
        blob inner = 'sub/deep.txt'
        inner.write('deep')
        log(ls('sub').join(','))
        log(notes.exists())
        notes.delete()
        log(notes.exists())
        """, files={"seed.txt": "from the outside"})
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["from the outside", "first|second", "deep.txt", "true", "false"]
        assert (sandbox / "sub" / "deep.txt").read_text(encoding="utf-8") == "deep"
        assert not (sandbox / "notes.txt").exists()

    def test_a_table_persists_through_sqlite_on_the_in_memory_filesystem(self, run_in_js_host):
        # SQLite's own file I/O (open, seek, read, write, truncate,
        # fdstat) all go through the host -- the most demanding client
        # of it a compiled program has.
        result, sandbox = run_in_js_host("""
        table People { id:int  name:text }
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'ada'])
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [2, 'grace'])
        arr[People] rows = sqlite('SELECT * FROM People ORDER BY id')
        log(rows.length)
        log(rows[1].name)
        """)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["2", "grace"]
        assert (sandbox / "festina.sqlite").exists()

    def test_timers_sleep_through_poll_oneoff(self, run_in_js_host):
        result, _ = run_in_js_host("""
        int ticks = 0
        void func tick() {
            ticks = ticks + 1
            log(`tick ${ticks}`)
            if ticks == 3 { close(0) }
        }
        setInterval(tick, 20)
        """)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == ["tick 1", "tick 2", "tick 3"]

    def test_argv_is_the_module_path(self, run_in_js_host):
        result, _ = run_in_js_host("log(argv.length)\nlog(argv[0])")
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        assert lines[0] == "1"
        assert lines[1].endswith("program.wasm")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def served_dir(tmp_path):
    """Serves tmp_path over HTTP on a background thread (module workers
    can't load from file://), with the cross-origin-isolation headers
    that let the worker sleep with Atomics.wait instead of spinning."""
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(tmp_path), **kw)

        def end_headers(self):
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *a):
            pass

    Handler.extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                              ".js": "text/javascript", ".mjs": "text/javascript",
                              ".wasm": "application/wasm"}
    port = _free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def chromium_page():
    """A headless Chromium page via Playwright; skips (or fails under
    FESTINA_STRICT_DEPS) when either isn't installed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        missing = "playwright isn't installed (pip install playwright; playwright install chromium)"
        if os.environ.get("FESTINA_STRICT_DEPS"):
            pytest.fail(missing)
        pytest.skip(missing)
    with sync_playwright() as pw:
        # The Chromium `playwright install chromium` fetched for THIS
        # Playwright version is the default; FESTINA_CHROMIUM names a
        # different binary to drive (a machine with a Chromium from
        # another Playwright version already on disk).
        launch_options = [{}]
        override = os.environ.get("FESTINA_CHROMIUM")
        if override:
            launch_options.insert(0, {"executable_path": override})
        browser = None
        failure = None
        for options in launch_options:
            try:
                browser = pw.chromium.launch(**options)
                break
            except Exception as exc:  # no browser binary
                failure = exc
        if browser is None:
            missing = f"Playwright's Chromium isn't available: {failure}"
            if os.environ.get("FESTINA_STRICT_DEPS"):
                pytest.fail(missing)
            pytest.skip(missing)
        page = browser.new_page()
        try:
            yield page
        finally:
            browser.close()


class TestInARealBrowser:
    def _run_in_browser(self, page, served_dir, tmp_path, compile_wasm, source):
        compile_wasm(source)
        for name in _HOST_FILES:
            shutil.copy(os.path.join(_WASM_DIR, name), tmp_path / name)
        page.goto(f"{served_dir}/browser.html?wasm=program.wasm")
        page.wait_for_function("window.festinaResult !== null", timeout=30000)
        return page.evaluate("window.festinaResult")

    def test_hello_world_runs_in_chromium(self, chromium_page, served_dir, tmp_path, compile_wasm):
        result = self._run_in_browser(chromium_page, served_dir, tmp_path, compile_wasm,
                                      "log('hello from a browser tab')")
        assert result["code"] == 0, result
        assert result["stdout"].strip() == "hello from a browser tab"
        # the page rendered it too
        assert "hello from a browser tab" in chromium_page.text_content("#out")

    def test_files_timers_and_exit_code_in_chromium(self, chromium_page, served_dir, tmp_path, compile_wasm):
        result = self._run_in_browser(chromium_page, served_dir, tmp_path, compile_wasm, """
        blob notes = 'notes.txt'
        notes.write('kept in the tab')
        mkdir('d')
        blob inner = 'd/x.txt'
        inner.write('x')
        log(ls('d').join(','))
        int ticks = 0
        void func tick() {
            ticks = ticks + 1
            if ticks == 2 {
                log('ticked twice')
                close(3)
            }
        }
        setInterval(tick, 10)
        """)
        assert result["code"] == 3, result
        assert result["stdout"].splitlines() == ["x.txt", "ticked twice"]
        # the program's files come back to the page as bytes (a
        # Uint8Array in the page; Playwright hands it over as a list)
        assert bytes(result["files"]["/notes.txt"]).decode() == "kept in the tab"

    def test_an_uncaught_error_reports_exit_1_and_stderr(self, chromium_page, served_dir, tmp_path, compile_wasm):
        result = self._run_in_browser(chromium_page, served_dir, tmp_path, compile_wasm,
                                      "log('a')\nfail('boom in the browser')")
        assert result["code"] == 1
        assert result["stdout"].strip() == "a"
        assert "boom in the browser" in result["stderr"]
