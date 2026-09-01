"""claude.md #161: graceful shutdown -- SIGINT/SIGTERM now run the SAME
clean-exit path close(code) already uses (claude.md #131: `on
exit(code:int)` fires, then the process exits) instead of the OS's own
default, abrupt, no-cleanup-at-all termination -- and, for a program
using openPort()/openSecurePort(), already-accepted connections get a
real chance to finish instead of being severed mid-response.

Every test here drives a real compiled subprocess with a real POSIX
signal -- there is no meaningful parser/semantic layer to this feature
(it adds no new syntax at all, only new behavior for `on exit` and the
existing openPort()/setTimeout() surface), so unlike most other
feature test files in this suite, everything below is runtime
behavior."""
import os
import signal
import socket
import subprocess
import time

import pytest


class TestHttpGracefulShutdown:
    def test_sigterm_stops_the_server_and_runs_on_exit(self, compile_and_run_server):
        server = compile_and_run_server("""
        on exit(code:int) {
            log(`exit code: ${code}`)
        }
        on request(req:http) {
            req.send({'code': 200, 'body': 'hi'})
        }
        openPort(__PORT__)
        """)
        status, _headers, body = server.http_get("/")
        assert status == 200
        assert body == b"hi"

        server.process.send_signal(signal.SIGTERM)
        server.process.wait(timeout=5)
        # 128 + SIGTERM(15) -- the conventional POSIX/shell "terminated
        # by signal N" encoding, same as an ordinary process killed the
        # same way would report.
        assert server.process.returncode == 143
        out = server.process.stdout.read()
        assert "exit code: 143" in out

    def test_sigint_uses_the_conventional_130_exit_code(self, compile_and_run_server):
        server = compile_and_run_server("""
        on exit(code:int) { log(`exit code: ${code}`) }
        on request(req:http) { req.ok() }
        openPort(__PORT__)
        """)
        server.process.send_signal(signal.SIGINT)
        server.process.wait(timeout=5)
        assert server.process.returncode == 130  # 128 + SIGINT(2)
        assert "exit code: 130" in server.process.stdout.read()

    def test_new_connections_are_refused_immediately_after_the_signal(self, compile_and_run_server):
        server = compile_and_run_server("""
        on request(req:http) { req.ok() }
        openPort(__PORT__)
        """)
        server.process.send_signal(signal.SIGTERM)
        deadline = time.time() + 3
        refused = False
        while time.time() < deadline:
            try:
                probe = socket.create_connection(("127.0.0.1", server.port), timeout=0.2)
                probe.close()
            except OSError:
                refused = True
                break
        assert refused, "a new connection was still accepted after shutdown was signaled"
        server.process.wait(timeout=5)

    def test_an_in_flight_connection_still_completes_before_exit(self, compile_and_run_server):
        server = compile_and_run_server("""
        on request(req:http) { req.send({'code': 200, 'body': 'still here'}) }
        openPort(__PORT__)
        """)
        # Connect BEFORE the signal, but don't send the request until
        # after it -- simulates a connection that was already open
        # (accepted) at the moment shutdown was triggered.
        sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        server.process.send_signal(signal.SIGTERM)
        time.sleep(0.2)  # let the signal actually get noticed
        sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        sock.settimeout(5)
        data = sock.recv(4096)
        sock.close()
        assert b"still here" in data
        server.process.wait(timeout=5)
        assert server.process.returncode == 143

    def test_forced_exit_after_the_grace_period_for_a_connection_that_never_closes(
            self, compile_and_run_server, monkeypatch):
        # claude.md #161's own bug, found by running this exact
        # scenario: a WebSocket connection that never closes on its
        # own used to hang festina_run_http_loop's poll() forever with
        # no periodic wakeup to ever re-check the grace-period deadline
        # against -- fixed by having the deadline also bound the
        # poll() timeout itself. FESTINA_SHUTDOWN_GRACE_SECONDS is a
        # debug/test-only override (see festina_runtime_http.c's own
        # comment) so this test exercises the real forced-cutoff code
        # path in a fraction of a second rather than the real 10s
        # default.
        monkeypatch.setenv("FESTINA_SHUTDOWN_GRACE_SECONDS", "1")
        server = compile_and_run_server("""
        on request(req:http) {
            url u = parseURL(req.url)
            if u.pathname == '/ws' { req.upgrade() }
        }
        on upgrade(s:socket) { }
        openPort(__PORT__)
        """)
        import base64
        key = base64.b64encode(os.urandom(16)).decode()
        sock = socket.create_connection(("127.0.0.1", server.port), timeout=5)
        req = (f"GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\n\r\n")
        sock.sendall(req.encode())
        sock.recv(4096)  # the 101 Switching Protocols response

        start = time.time()
        server.process.send_signal(signal.SIGTERM)
        server.process.wait(timeout=8)
        elapsed = time.time() - start
        sock.close()

        assert server.process.returncode == 143
        # Bounded well below the 10s production default, and well
        # above 0 -- confirms the grace period was actually enforced
        # (not skipped, not hung).
        assert 0.5 < elapsed < 5.0


class TestTimerOnlyGracefulShutdown:
    """No openPort() at all -- festina_run_timer_loop is the loop that
    needs to notice a shutdown signal here, not festina_run_http_loop."""

    def _run_background(self, tmp_path, cli_mod, source):
        from tests.conftest import compile_file_or_skip
        src_path = tmp_path / "main.f"
        src_path.write_text(source, encoding="utf-8")
        out_path = tmp_path / "program"
        compile_file_or_skip(cli_mod, str(src_path), str(out_path))
        return subprocess.Popen(
            [str(out_path)], cwd=tmp_path,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def test_sigterm_runs_on_exit_and_stops_a_timer_only_program(self, tmp_path, cli_mod):
        process = self._run_background(tmp_path, cli_mod, """
        on exit(code:int) { log(`exit code: ${code}`) }
        void func tick() { log('tick') }
        setInterval(tick, 200)
        """)
        time.sleep(0.5)
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=5)
        assert process.returncode == 143
        out = process.stdout.read()
        assert "tick" in out
        assert "exit code: 143" in out


class TestNoRegression:
    """Confirms this feature never makes a program HARDER to stop than
    it was before -- installing a signal handler is only safe where
    something is guaranteed to poll it soon (see codegen.py's own
    comment on why exit_handler_symbol alone is deliberately NOT part
    of the install condition)."""

    def _run_background(self, tmp_path, cli_mod, source):
        from tests.conftest import compile_file_or_skip
        src_path = tmp_path / "main.f"
        src_path.write_text(source, encoding="utf-8")
        out_path = tmp_path / "program"
        compile_file_or_skip(cli_mod, str(src_path), str(out_path))
        return subprocess.Popen(
            [str(out_path)], cwd=tmp_path,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def test_a_plain_script_with_no_event_loop_is_still_killed_immediately(self, tmp_path, cli_mod):
        process = self._run_background(tmp_path, cli_mod, """
        int i = 0
        while (true) {
            i = i + 1
        }
        """)
        time.sleep(0.3)
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=3)  # would time out (and fail the test) if unkillable

    def test_on_exit_declared_but_no_pollable_loop_still_uses_default_disposition(self, tmp_path, cli_mod):
        # The one genuinely risky edge case: a program that declares
        # `on exit` but has no http/timers/graphics loop at all. This
        # MUST still be killable -- see codegen.py's own comment on why
        # the shutdown handler is deliberately not installed for this
        # shape (nothing in such a program's own execution could ever
        # poll for it, so installing a handler would silently swallow
        # Ctrl-C instead of skipping the exit handler).
        process = self._run_background(tmp_path, cli_mod, """
        on exit(code:int) { log('should not print') }
        int i = 0
        while (true) {
            i = i + 1
        }
        """)
        time.sleep(0.3)
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=3)  # would time out (and fail the test) if unkillable
        assert "should not print" not in (process.stdout.read() or "")


class TestThreadGracefulShutdown:
    """claude.md #195 Phase 2/#200 Phase 6: SIGINT/SIGTERM and
    close(code) both eventually reach festina_program_exit, which is
    what actually tears down every declared `thread`
    (festina_thread_kill_all -- see claude.md #195's own "if the main
    thread dies, kill all child threads" design note) -- gated into the
    shutdown-handler-install condition above the same way
    uses_timers/uses_http/uses_graphics already are (`self.uses_threads`,
    codegen.py's own `_emit_main_and_entry`), so a thread-only program
    (no http/timers/graphics at all) is exactly as gracefully killable
    as any other kind. `TestThreads.test_main_thread_death_kills_a_
    still_idle_child_thread` (test_codegen.py) already covers the
    close()-with-an-IDLE-thread shape; this class covers what's
    genuinely different here: a thread ACTIVELY busy (not blocked on
    its own inbound condvar) when the signal arrives, a DatabaseURL-
    declared thread's own private handle, exit-code correctness, and a
    real /proc-based check that nothing survives the parent -- not just
    that subprocess.wait()'s own timeout didn't fire."""

    def _run_background(self, tmp_path, cli_mod, source):
        from tests.conftest import compile_file_or_skip
        src_path = tmp_path / "main.f"
        src_path.write_text(source, encoding="utf-8")
        out_path = tmp_path / "program"
        compile_file_or_skip(cli_mod, str(src_path), str(out_path))
        return subprocess.Popen(
            [str(out_path)], cwd=tmp_path,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def test_sigterm_tears_down_a_thread_actively_processing_a_message(
            self, tmp_path, cli_mod):
        # Unlike the still-IDLE thread claude.md #195's own test
        # covers, this worker is deep inside its own on_message's own
        # ~1.5s busy-wait -- NOT blocked on the inbound condvar -- when
        # the signal arrives. The worker loop only ever checks its own
        # kill flag BETWEEN messages (see festina_runtime_thread.c's
        # own worker loop), never preempting an in-flight on_message,
        # so kill()'s own pthread_join genuinely has to wait for this
        # one to run to completion before the process can exit at all
        # -- confirmed by 'worker finished a slow message' actually
        # appearing (not skipped/interrupted) even though the signal
        # arrived well before the busy-wait's own deadline. Bounded by
        # the 15s subprocess.wait() timeout, which is what would catch
        # a real hang. `on exit(code:int)`'s own `code` is always 0
        # here (see api.md's own note on `NAME.kill()`) -- the THREAD's
        # exit code, unrelated to the PROCESS's real one (143 below).
        process = self._run_background(tmp_path, cli_mod, """
        thread worker {
            on message(p:int) {
                int start = now()
                while now() - start < 1500 {
                }
                log('worker finished a slow message')
            }
            on exit(code:int) {
                log(`worker exit code: ${code}`)
            }
        }
        worker.postMessage(1)
        """)
        time.sleep(0.3)  # well inside the worker's own 1.5s busy-wait
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=15)
        assert process.returncode == 143
        out = process.stdout.read()
        assert "worker finished a slow message" in out
        assert "worker exit code: 0" in out

    def test_close_with_a_live_thread_preserves_the_real_exit_code(self, compile_and_run):
        # A live thread's own teardown must never override or corrupt
        # whatever exit code the main program actually asked for.
        source = """
        thread worker {
        }
        close(42)
        """
        result = compile_and_run(source)
        assert result.returncode == 42

    def test_sigterm_tears_down_a_database_url_thread_without_hanging_or_crashing(
            self, tmp_path, cli_mod):
        # claude.md #199 Phase 5's own private sqlite handle is
        # deliberately never explicitly closed on kill() (a documented,
        # accepted gap -- see that entry's own comment) -- which must
        # not translate into a HANG or crash on exit either:
        # festina_thread_kill_all() joins the thread the same way
        # regardless of what OS resources it still holds open, and the
        # process exits normally, the handle simply reclaimed by the OS.
        process = self._run_background(tmp_path, cli_mod, """
        thread worker {
            DatabaseURL = 'shutdown_worker.sqlite'
            on message(p:int) {
                sqlite('SELECT 1')
            }
            on exit(code:int) {
                log('worker exiting')
            }
        }
        worker.postMessage(1)
        """)
        time.sleep(0.2)
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=5)
        assert process.returncode == 143
        assert "worker exiting" in process.stdout.read()

    def test_no_orphaned_thread_survives_after_the_parent_exits(self, tmp_path, cli_mod):
        # A declared `thread` is an OS thread INSIDE the same process,
        # not a separate one -- the only way it could "survive" the
        # parent is if festina_thread_kill_all()'s own pthread_join
        # never actually returned, which would show up as this exact
        # process still lingering in the OS's own process table well
        # past when it reported exiting. Checked directly against
        # /proc rather than just trusting subprocess.wait() to have
        # returned in time -- first confirming the declared thread
        # really did show up as a second, distinct OS thread (not just
        # trusting that it would), then confirming /proc/<pid> is fully
        # gone once wait() reaps it.
        process = self._run_background(tmp_path, cli_mod, """
        thread worker {
        }
        log('ready')
        """)
        task_dir = f"/proc/{process.pid}/task"
        deadline = time.time() + 5
        thread_count = 1
        while time.time() < deadline:
            try:
                thread_count = len(os.listdir(task_dir))
            except OSError:
                break
            if thread_count > 1:
                break
            time.sleep(0.05)
        assert thread_count > 1, "the declared thread never showed up as a real OS thread"

        process.send_signal(signal.SIGTERM)
        process.wait(timeout=5)
        assert process.returncode == 143
        assert not os.path.exists(f"/proc/{process.pid}"), (
            "the process (and every one of its threads) should be fully gone "
            "from the OS's own process table once wait() has reaped it")
