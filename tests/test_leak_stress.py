"""claude.md #102: the leak stress suite.

The rest of this directory proves the compiler produces the right
ANSWERS. This proves nothing accumulates while producing them: each
program in tests/stress/ hammers one managed resource -- text, arrays
and maps, structs and query rows, images and audio clips, regexes and
files -- some thousands of times, under AddressSanitizer and
LeakSanitizer. At that iteration count a leak of even a few bytes per
pass is unmissable, which is the entire point; a leak that only shows up
after a million frames of a game is otherwise invisible until it isn't.

The programs are deliberately written as one long loop rather than as
many small cases: the interesting failures are the ones where a value's
ownership is right in isolation and wrong when it is aliased, returned,
stored and discarded in the same breath.

Why a shell script rather than doing this inline: `clang
-fsanitize=address -c file.ll` does NOT instrument raw LLVM IR text --
ASan's per-function opt-in is added by clang's C frontend, which is
bypassed entirely when the input is already .ll. The script stamps the
attribute onto every `define` line first. See its own header comment for
the verification of that claim, and for why it needs two compilers.
"""
import os
import shutil
import subprocess

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "leak_stress.sh")
_STRESS_DIR = os.path.join(_ROOT, "tests", "stress")

# Exit code the script uses for "this environment cannot run me" (no
# clang for the IR, or no working ASan runtime to link against), as
# distinct from "a program leaked".
_SKIP_EXIT = 77


def _run_harness(*args):
    """Invokes the harness through an explicitly resolved `bash` rather
    than relying on its shebang. `/usr/bin/env bash` fails with a bare
    127 in a stripped PATH, which is indistinguishable from a real
    failure at the call site -- and the script's own 77-means-skip guard
    never gets to run, because the shell it guards was never found."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on PATH -- the leak harness is a shell script")
    return subprocess.run([bash, _SCRIPT, *args],
                           capture_output=True, text=True, timeout=900)


def _stress_programs():
    if not os.path.isdir(_STRESS_DIR):
        return []
    return sorted(f for f in os.listdir(_STRESS_DIR) if f.endswith(".f"))


class TestLeakStress:
    @pytest.mark.parametrize("program", _stress_programs())
    def test_program_is_leak_free(self, program):
        # One test per program rather than one for all of them, so a
        # failure names the resource that leaked instead of just
        # "something did".
        result = _run_harness(os.path.join(_STRESS_DIR, program))
        if result.returncode == _SKIP_EXIT:
            pytest.skip(result.stderr.strip() or "sanitizers unavailable")
        assert result.returncode == 0, (
            f"{program} is not leak-free:\n{result.stdout}\n{result.stderr}")

    def test_the_harness_can_actually_fail(self, tmp_path):
        """A leak checker that cannot report a leak is worse than none.

        This is not hypothetical caution: `clang -fsanitize=address -c
        file.ll` silently produces an UNinstrumented object, so a harness
        built the obvious way passes everything and proves nothing.

        The canary leaks on purpose and the harness must say so. It is a
        REFERENCE CYCLE (claude.md #106): reference counting cannot free
        one, so this leaks until this language grows a tracing
        collector, which makes it about as durable a canary as exists
        here. The previous canary -- a call result reached through a
        chain -- was retired because claude.md #108 fixed it, which is
        exactly the failure mode a canary is supposed to have: it stops
        leaking, the test fails loudly, and nobody discovers months
        later that the harness had been vacuous.
        """
        canary = tmp_path / "canary.f"
        canary.write_text(
            "struct Node { n:int next:Node }\n"
            "void func build() { Node a a.n = 1 a.next = a }\n"
            "int i = 0\n"
            "while i < 200 { build() i = i + 1 }\n"
            "log('done')\n"
        )
        result = _run_harness(str(canary))
        if result.returncode == _SKIP_EXIT:
            pytest.skip(result.stderr.strip() or "sanitizers unavailable")
        assert result.returncode != 0, (
            "the leak harness reported a known-leaking program as clean, which "
            "means it is not instrumenting anything:\n" + result.stdout)
        assert "LeakSanitizer" in result.stdout

    def test_the_suite_covers_every_managed_resource(self):
        # A guard against the suite quietly shrinking: each of these
        # names a distinct ownership mechanism, and dropping one would
        # leave a whole class of leak unwatched.
        assert set(_stress_programs()) == {
            "collections_churn.f",      # arr[T]/map[T], nested and aliased
            "media_churn.f",            # img/aud handles, incl. BLOB round trips
            "regex_and_files_churn.f",  # regex compilation, file and time text
            "structs_and_rows_churn.f", # structs, query rows, scope exits
            "text_churn.f",             # text, the copy-managed one
        }
