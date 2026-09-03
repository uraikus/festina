"""claude.md #223: the Valgrind counterpart to test_leak_stress.py.

tests/stress/*.f runs under AddressSanitizer/LeakSanitizer via
scripts/leak_stress.sh. This second tier exists because
AddressSanitizer was incompatible with the try/throw mechanism of the
time (confirmed directly: llvm.eh.sjlj.setjmp/longjmp-based try/throw
crashed with SIGILL under -fsanitize=address, even for a plain
try/catch with nothing else involved), so every stress program using
try/throw lived in tests/valgrind_stress/ instead, checked here via
scripts/valgrind_stress.sh -- an ordinary compiled binary run under
`valgrind --leak-check=full`, the same tool api.md's own try/throw
leak-freedom claim was already using for manual verification.

claude.md #235 rebuilt try/throw on libc's own setjmp/longjmp, which
ASan intercepts and handles: tests/stress/ programs may use try/throw
freely now (confirmed by running this directory's own program through
scripts/leak_stress.sh: clean). This tier stays as an independent
second tool -- Valgrind sees invalid frees and uninitialised reads the
same run, with no instrumented rebuild -- not as the only place a
throwing program can be leak-checked.
"""
import os
import shutil
import subprocess

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "valgrind_stress.sh")
_STRESS_DIR = os.path.join(_ROOT, "tests", "valgrind_stress")

_SKIP_EXIT = 77


def _run_harness(*args):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on PATH -- the valgrind harness is a shell script")
    return subprocess.run([bash, _SCRIPT, *args],
                           capture_output=True, text=True, timeout=900)


def _stress_programs():
    if not os.path.isdir(_STRESS_DIR):
        return []
    return sorted(f for f in os.listdir(_STRESS_DIR) if f.endswith(".f"))


class TestValgrindStress:
    @pytest.mark.parametrize("program", _stress_programs())
    def test_program_is_leak_free(self, program):
        result = _run_harness(os.path.join(_STRESS_DIR, program))
        if result.returncode == _SKIP_EXIT:
            pytest.skip(result.stderr.strip() or "valgrind unavailable")
        assert result.returncode == 0, (
            f"{program} is not leak-free under valgrind:\n{result.stdout}\n{result.stderr}")

    def test_the_suite_covers_try_throw_using_programs(self):
        # A guard against this suite quietly shrinking, mirroring
        # test_leak_stress.py's own test_the_suite_covers_every_managed_
        # resource -- every entry here names the ownership shape it's
        # the only coverage for.
        assert set(_stress_programs()) == {
            # claude.md #223: .toStruct()/.toArr()'s own partial-parse
            # failure path -- a struct/array/map/nested-struct-field/
            # self-referencing-struct build that throws PARTWAY THROUGH,
            # at every shape the fix's own local catch frame applies to,
            # plus a malformed-syntax throw. This is the ONLY stress
            # coverage of that path; it cannot live in tests/stress/
            # because it necessarily exercises try/throw, which ASan
            # cannot run at all in this environment.
            "json_parse_fail_churn.f",
        }
