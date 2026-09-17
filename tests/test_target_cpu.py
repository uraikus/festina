"""claude.md #335/#336: `FESTINA_TARGET_CPU`, and what a binary targets
when nobody says.

festina/llvm_backend.py built its target machine from
`LLVMGetHostCPUName()` and `LLVMGetHostCPUFeatures()`, so an object
emitted here was tuned for -- and might only run on -- the processor
that compiled it. On the machine this was written on that is
`emeraldrapids` with twelve AVX-512 feature flags enabled. #335 added
the escape hatch; #336 made portable the DEFAULT and `native` the way
to ask for the old behaviour.

Two harms drove it, and the reported one is the smaller. A binary built
on a recent chip refuses to start on an older one, which is a strange
thing to get from a compiler whose output you would hand to someone.
And valgrind dies with SIGILL before `main` on an AVX-512 host, which
matters more than it sounds: valgrind is how two of the other bugs from
the same report were found, so the tool that finds memory bugs was
unusable on exactly the machines with the newest instructions.

Reported by uraikus/archtelos-browser, who had to monkeypatch the
compiler to get this.
"""
import os
import subprocess
import sys

import pytest

from festina import llvm_backend


_SOURCE = """
float acc = 0.0
int i = 0
while i < 20000 {
    acc = acc + (i * 1.5) / 2.25
    i++
}
log(`${acc}`)
"""


class TestTheSelection:
    """Unit-level: what CPU and features the backend asks LLVM for. No
    compiler run, so these are the checks that hold on any host."""

    def test_unset_means_the_portable_baseline(self):
        # claude.md #336: the flip. A binary you hand to someone has to
        # start on their machine, so that is what an unset variable
        # gets.
        cpu, features = llvm_backend.target_cpu_and_features(environ={})
        assert cpu == b"generic"
        assert features == b""

    def test_native_asks_for_the_build_machine(self):
        # The opt-in, and the only value that reads the processor. Not
        # asserting WHICH cpu -- that is whatever machine this runs on;
        # the claim is that it names a real one and carries its
        # features, which no other value does.
        cpu, features = llvm_backend.target_cpu_and_features(
            environ={"FESTINA_TARGET_CPU": "native"})
        assert cpu and cpu != b"generic"
        assert isinstance(features, bytes)
        assert features != b"", (
            "native must carry the host feature string; without it the "
            "CPU name alone is most of the point but not all of it")

    def test_generic_clears_the_host_features(self):
        # The load-bearing half. LLVM derives a named CPU's features from
        # the name, so leaving the host feature string in place would put
        # every AVX-512 flag straight back and the setting would look
        # like it did nothing.
        cpu, features = llvm_backend.target_cpu_and_features(
            environ={"FESTINA_TARGET_CPU": "generic"})
        assert cpu == b"generic"
        assert features == b""

    def test_any_cpu_name_is_passed_through(self):
        # A dial, not a switch: `generic` is the documented value but any
        # LLVM CPU name works, so someone targeting a known floor can say
        # so rather than dropping all the way to baseline.
        cpu, features = llvm_backend.target_cpu_and_features(
            environ={"FESTINA_TARGET_CPU": "x86-64-v2"})
        assert cpu == b"x86-64-v2"
        assert features == b""

    def test_whitespace_only_is_treated_as_unset(self):
        blank = llvm_backend.target_cpu_and_features(
            environ={"FESTINA_TARGET_CPU": "   "})
        unset = llvm_backend.target_cpu_and_features(environ={})
        assert blank == unset

    def test_the_real_environment_is_the_default_source(self, monkeypatch):
        monkeypatch.setenv("FESTINA_TARGET_CPU", "native")
        cpu, _ = llvm_backend.target_cpu_and_features()
        assert cpu != b"generic"


def _compile(tmp_path, name, env_extra):
    out = tmp_path / name
    src = tmp_path / "prog.f"
    src.write_text(_SOURCE, encoding="utf-8")
    env = dict(os.environ)
    env.pop("FESTINA_TARGET_CPU", None)
    env.update(env_extra)
    result = subprocess.run(
        [sys.executable, "-m", "festina.cli", "compile", str(src), "-o", str(out)],
        capture_output=True, text=True, env=env, timeout=600,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if result.returncode != 0:
        pytest.skip(f"cannot compile in this environment: {result.stderr[-400:]}")
    return out


class TestEndToEnd:
    """Through the real CLI, with the variable set the way a user would
    set it -- the unit checks above cannot catch a wiring mistake
    between `target_cpu_and_features` and the target machine."""

    def test_a_native_build_runs_and_agrees_with_the_default(self, tmp_path):
        native = _compile(tmp_path, "native", {"FESTINA_TARGET_CPU": "native"})
        generic = _compile(tmp_path, "generic", {})
        got_native = subprocess.run([str(native)], capture_output=True, text=True,
                                    timeout=120)
        got_generic = subprocess.run([str(generic)], capture_output=True, text=True,
                                     timeout=120)
        assert got_native.returncode == 0, got_native.stderr
        assert got_generic.returncode == 0, got_generic.stderr
        # Same answer, which is the point: this changes the instruction
        # selection and nothing about the program's meaning.
        assert got_native.stdout == got_generic.stdout
        assert got_generic.stdout.strip() != ""

    def test_the_setting_actually_reaches_the_object(self, tmp_path):
        # If the wiring broke, both builds would be byte-identical and
        # every other test here would still pass.
        native = _compile(tmp_path, "native2", {"FESTINA_TARGET_CPU": "native"})
        generic = _compile(tmp_path, "generic2", {})
        assert native.read_bytes() != generic.read_bytes()


class TestTheInstructionsThemselves:
    """The observable the report is really about. Host-specific only --
    on a machine with no AVX at all the two builds legitimately agree,
    and asserting otherwise would fail for the right reason on the wrong
    hardware."""

    def _disassemble(self, path):
        which = subprocess.run(["objdump", "-d", str(path)],
                               capture_output=True, text=True, timeout=300)
        if which.returncode != 0:
            pytest.skip("objdump is not usable here")
        return which.stdout

    def test_a_generic_build_drops_the_host_only_encodings(self, tmp_path):
        try:
            cpuinfo = open("/proc/cpuinfo", encoding="utf-8").read()
        except OSError:
            pytest.skip("no /proc/cpuinfo to establish what this host supports")
        if " avx " not in cpuinfo and "\navx " not in cpuinfo and " avx\n" not in cpuinfo:
            pytest.skip("this host has no AVX, so both builds agree by rights")

        native = self._disassemble(
            _compile(tmp_path, "native3", {"FESTINA_TARGET_CPU": "native"}))
        generic = self._disassemble(_compile(tmp_path, "generic3", {}))

        # VEX-encoded scalar float ops -- `vmulsd`/`vdivsd` against plain
        # `mulsd`/`divsd`. The host build uses them because the host has
        # AVX; the baseline x86-64 build cannot.
        assert "vmulsd" in native or "vaddsd" in native or "vdivsd" in native, (
            "expected FESTINA_TARGET_CPU=native to use VEX-encoded float "
            "ops on an AVX machine -- if this fails, `native` has stopped "
            "reaching the target machine and the opt-in does nothing")
        assert "vmulsd" not in generic
        assert "vdivsd" not in generic
        assert "vaddsd" not in generic
