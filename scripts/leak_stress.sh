#!/usr/bin/env bash
# claude.md #102: the leak stress harness.
#
# Compiles each program in tests/stress/*.f with AddressSanitizer +
# LeakSanitizer and runs it, failing if any of them reports a leak or a
# memory error. Each program hammers one managed resource in a loop
# thousands of times, so a leak of even a few bytes per iteration is
# unmissable -- which is the point: the ordinary test suite proves the
# ANSWERS are right, and this proves nothing accumulates while
# producing them.
#
# CRITICAL, and the reason this is a script rather than three commands:
# `clang -fsanitize=address -c file.ll` does NOT instrument raw LLVM IR
# text. ASan's per-function opt-in (the `sanitize_address` attribute) is
# added by clang's C frontend, which is bypassed entirely when the input
# is already .ll. Verified directly: a hand-written calloc+free+
# read-after-free .ll compiled that way produces ZERO instrumentation
# symbols and does NOT catch a real use-after-free, while the identical
# .c source does. Stamping the attribute onto every `define` line before
# compiling fixes it completely, and is what the sed below does.
#
# Usage: scripts/leak_stress.sh [program.f ...]     (default: all of them)
set -uo pipefail

# Exit 77 means "this environment cannot run me" -- a missing tool or a
# missing sanitizer runtime -- as distinct from "a program leaked". The
# check comes first, before anything that would itself need those tools:
# without it a bare-PATH environment dies on mktemp with a shell error,
# and the caller cannot tell that apart from a real failure.
for tool in mktemp sed basename pkg-config python3; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "leak_stress: '$tool' is not on PATH" >&2
        exit 77
    }
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Two compilers, because they are not interchangeable here.
#
# IR_CC compiles the generated .ll and MUST be clang: it is the only one
# that parses LLVM IR text at all (gcc hands a .ll to ld, which fails
# treating it as a corrupt linker script -- the same reason festina's own
# cc fallback requires clang).
#
# SAN_CC compiles the C runtime and links, and needs a working ASan
# runtime library. Those are shipped separately from the compiler and are
# routinely absent -- this container has clang with no
# libclang_rt.asan-x86_64.a at all -- so it is probed rather than
# assumed, and gcc is tried second. Mixing the two is fine: both emit
# calls into whichever ASan runtime the link actually pulls in.
IR_CC="${IR_CC:-clang}"
command -v "$IR_CC" >/dev/null || { echo "leak_stress: no $IR_CC on PATH (needed to compile LLVM IR)" >&2; exit 77; }

probe_san_cc() {
    local candidate probe="$WORK/probe.c"
    printf 'int main(void){return 0;}\n' > "$probe"
    for candidate in "${SAN_CC:-}" clang gcc cc; do
        [ -n "$candidate" ] || continue
        command -v "$candidate" >/dev/null || continue
        if "$candidate" -fsanitize=address "$probe" -o "$WORK/probe.bin" >/dev/null 2>&1; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}
SAN_CC="$(probe_san_cc)" || {
    echo "leak_stress: no compiler on PATH can link with -fsanitize=address" >&2
    echo "             (install compiler-rt for clang, or libasan for gcc)" >&2
    exit 77
}

# LeakSanitizer is a separate question from ASan: on darwin/arm64 ASan
# links and runs fine but any binary started under
# ASAN_OPTIONS=detect_leaks=1 aborts at startup with "detect_leaks is
# not supported on this platform". macos.md's standing decision keeps
# this tier Linux-only; this probe is what turns that into the skip
# exit code instead of a wall of failures (measured on the first real
# macos-14 CI run: 31 of them, all this).
if ! ASAN_OPTIONS=detect_leaks=1 "$WORK/probe.bin" >/dev/null 2>&1; then
    echo "leak_stress: LeakSanitizer (ASAN_OPTIONS=detect_leaks=1) is not supported on this platform" >&2
    exit 77
fi

# ALSA's null plugin, so an audio program can open a "device" and stream
# real PCM without sound hardware -- the same mechanism tests/conftest.py
# uses. HOME is overridden because that is where ALSA looks.
export HOME="$WORK/home"
mkdir -p "$HOME"
printf 'pcm.!default { type null }\nctl.!default { type null }\n' > "$HOME/.asoundrc"

PROGRAMS=("$@")
if [ ${#PROGRAMS[@]} -eq 0 ]; then
    PROGRAMS=("$ROOT"/tests/stress/*.f)
fi

# Each runtime translation unit, instrumented once and reused. All three
# are linked unconditionally here -- unlike the real compiler, which
# selects them per feature; a sanitizer harness has no binary-size
# concern and this keeps the script from reimplementing that selection.
build_runtime() {
    local name="$1" src="$2"; shift 2
    [ -f "$WORK/rt_$name.o" ] && return 0
    "$SAN_CC" -fsanitize=address -g -O1 -c "$ROOT/runtime/$src" "$@" -o "$WORK/rt_$name.o" || return 1
}
build_runtime core festina_runtime.c $(pkg-config --cflags sqlite3) || exit 1
GFX_OK=0; AUD_OK=0
if pkg-config --exists cairo-xlib x11 libjpeg; then
    build_runtime graphics festina_runtime_graphics.c $(pkg-config --cflags cairo-xlib x11 libjpeg) && GFX_OK=1
fi
if pkg-config --exists alsa libmpg123; then
    build_runtime audio festina_runtime_audio.c $(pkg-config --cflags alsa libmpg123) && AUD_OK=1
fi

LIBS=(-lsqlite3 -lm)
OBJS=("$WORK/rt_core.o")
[ $GFX_OK = 1 ] && { OBJS+=("$WORK/rt_graphics.o"); LIBS+=($(pkg-config --libs cairo-xlib x11 libjpeg)); }
[ $AUD_OK = 1 ] && { OBJS+=("$WORK/rt_audio.o"); LIBS+=($(pkg-config --libs alsa libmpg123) -pthread); }

failures=0
for src in "${PROGRAMS[@]}"; do
    name="$(basename "$src" .f)"
    out="$WORK/$name"
    if ! (cd "$ROOT" && python3 -m festina.cli compile "$src" --emit-llvm) > "$out.ll" 2> "$out.compile.err"; then
        echo "FAIL $name -- compile"; sed 's/^/    /' "$out.compile.err"; failures=$((failures + 1)); continue
    fi
    sed -E 's/^(define [^{]+) \{/\1 sanitize_address {/' "$out.ll" > "$out.asan.ll"
    if ! "$IR_CC" -fsanitize=address -g -O1 -c "$out.asan.ll" -o "$out.o" 2> "$out.cc.err"; then
        echo "FAIL $name -- IR compile"; sed 's/^/    /' "$out.cc.err"; failures=$((failures + 1)); continue
    fi
    if ! "$SAN_CC" -fsanitize=address -g -O1 "$out.o" "${OBJS[@]}" "${LIBS[@]}" -o "$out.bin" 2> "$out.link.err"; then
        echo "FAIL $name -- link"; sed 's/^/    /' "$out.link.err"; failures=$((failures + 1)); continue
    fi

    # Each program runs in its own directory: several of them declare
    # tables, and a shared festina.sqlite would let one run's rows leak
    # into the next one's counts.
    rundir="$WORK/run_$name"; mkdir -p "$rundir"
    cp "$ROOT"/tests/fixtures/* "$rundir/" 2>/dev/null || true
    cp "$ROOT"/examples/beep.wav "$rundir/" 2>/dev/null || true
    if (cd "$rundir" && ASAN_OPTIONS=detect_leaks=1 "$out.bin" > "$out.stdout" 2> "$out.stderr"); then
        echo "ok   $name"
    else
        echo "FAIL $name -- exit $?"
        sed 's/^/    /' "$out.stderr" | head -40
        failures=$((failures + 1))
    fi
done

if [ "$failures" -ne 0 ]; then
    echo "leak_stress: $failures program(s) failed"
    exit 1
fi
echo "leak_stress: all clean"
