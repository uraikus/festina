#!/usr/bin/env bash
# claude.md #223: the Valgrind counterpart to scripts/leak_stress.sh,
# scoped to tests/valgrind_stress/*.f -- programs that use try/throw and
# so CANNOT run through the AddressSanitizer-based harness at all.
#
# Confirmed directly: AddressSanitizer is incompatible with this
# project's try/throw mechanism in this environment. try/throw is built
# on llvm.eh.sjlj.setjmp/llvm.eh.sjlj.longjmp (portable LLVM intrinsics,
# not libc setjmp/longjmp), and ANY program that uses try/throw --
# including a plain, pre-existing one with no JSON or anything else new
# involved -- crashes with SIGILL the moment it's compiled with
# -fsanitize=address. This is not something this script works around;
# it is why this script exists as a SEPARATE thing from leak_stress.sh
# rather than a flag on it. api.md's own try/throw leak-freedom claim
# was already stated as Valgrind-measured, not ASan-measured, for
# exactly this reason -- this script generalizes that to a real,
# automated, repeatable check instead of one-off manual verification.
#
# Unlike leak_stress.sh, this needs no instrumented runtime build and no
# IR-attribute stamping: valgrind runs an ORDINARY compiled binary
# (festina.cli's own normal `compile` pipeline, -O2 and all), so this
# script is just "compile it the regular way, run it under valgrind,
# fail if it reports a definite leak or a memory error".
#
# Usage: scripts/valgrind_stress.sh [program.f ...]
#        (default: every tests/valgrind_stress/*.f)
set -uo pipefail

for tool in mktemp basename python3 valgrind; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "valgrind_stress: '$tool' is not on PATH" >&2
        exit 77
    }
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

STRESS_DIR="$ROOT/tests/valgrind_stress"
PROGRAMS=("$@")
if [ ${#PROGRAMS[@]} -eq 0 ]; then
    PROGRAMS=("$STRESS_DIR"/*.f)
fi

failures=0
for src in "${PROGRAMS[@]}"; do
    name="$(basename "$src" .f)"
    out="$WORK/$name"
    if ! (cd "$ROOT" && python3 -m festina.cli compile "$src" -o "$out.bin") > "$out.compile.out" 2> "$out.compile.err"; then
        echo "FAIL $name -- compile"; sed 's/^/    /' "$out.compile.err"; failures=$((failures + 1)); continue
    fi

    rundir="$WORK/run_$name"; mkdir -p "$rundir"
    cp "$ROOT"/tests/fixtures/* "$rundir/" 2>/dev/null || true
    if (cd "$rundir" && valgrind --leak-check=full --error-exitcode=1 --errors-for-leak-kinds=definite \
            "$out.bin" > "$out.stdout" 2> "$out.stderr"); then
        echo "ok   $name"
    else
        echo "FAIL $name -- exit $?"
        sed 's/^/    /' "$out.stderr" | head -60
        failures=$((failures + 1))
    fi
done

if [ "$failures" -ne 0 ]; then
    echo "valgrind_stress: $failures program(s) failed"
    exit 1
fi
echo "valgrind_stress: all clean"
