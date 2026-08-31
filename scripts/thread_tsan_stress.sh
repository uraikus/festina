#!/usr/bin/env bash
# claude.md #195 Phase 2: the ThreadSanitizer counterpart to
# scripts/leak_stress.sh, scoped to `tests/stress/thread_*.f` only --
# TSan instruments every memory access to detect a genuine DATA RACE
# (two threads touching the same address with no synchronization
# between them, at least one a write), a class of bug ASan cannot see
# at all. Every OTHER stress program in tests/stress/ runs entirely on
# one thread, so TSan would have nothing new to say about them; only a
# `thread`-using program actually runs Festina code on more than one
# OS thread at once, and this is exactly the "verified with ASan AND
# ThreadSanitizer" bar claude.md #163 already set once for the same
# class of bug (its own TSan run caught a real one).
#
# Mirrors leak_stress.sh's own two-compiler split and IR-attribute-
# stamping trick exactly (see that script's own top comment for why
# both are needed) -- `sanitize_thread`, not `sanitize_address`, is
# the attribute clang's C frontend would have added on its own had the
# generated IR gone through it instead of being emitted directly.
#
# Usage: scripts/thread_tsan_stress.sh [program.f ...]
#        (default: every tests/stress/thread_*.f)
set -uo pipefail

for tool in mktemp sed basename pkg-config python3; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "thread_tsan_stress: '$tool' is not on PATH" >&2
        exit 77
    }
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

IR_CC="${IR_CC:-clang}"
command -v "$IR_CC" >/dev/null || { echo "thread_tsan_stress: no $IR_CC on PATH" >&2; exit 77; }

probe_san_cc() {
    local candidate probe="$WORK/probe.c"
    printf 'int main(void){return 0;}\n' > "$probe"
    for candidate in "${SAN_CC:-}" clang gcc cc; do
        [ -n "$candidate" ] || continue
        command -v "$candidate" >/dev/null || continue
        if "$candidate" -fsanitize=thread "$probe" -o "$WORK/probe.bin" >/dev/null 2>&1; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}
SAN_CC="$(probe_san_cc)" || {
    echo "thread_tsan_stress: no compiler on PATH can link with -fsanitize=thread" >&2
    echo "                    (install compiler-rt for clang, or libtsan for gcc)" >&2
    exit 77
}

build_runtime() {
    local name="$1" src="$2"; shift 2
    [ -f "$WORK/rt_$name.o" ] && return 0
    "$SAN_CC" -fsanitize=thread -g -O1 -c "$ROOT/runtime/$src" "$@" -o "$WORK/rt_$name.o" || return 1
}
build_runtime core festina_runtime.c $(pkg-config --cflags sqlite3) || exit 1
build_runtime async festina_runtime_async.c || exit 1
build_runtime thread festina_runtime_thread.c || exit 1
# claude.md #198 Phase 4: `thread`'s own blob/img/aud/url clone --
# festina_runtime_graphics.c/_audio.c only, same conditional probe
# leak_stress.sh already uses (these two are real, routinely-absent
# system dependencies, unlike core/async/thread which are pure POSIX).
GFX_OK=0; AUD_OK=0
if pkg-config --exists cairo-xlib x11 libjpeg; then
    build_runtime graphics festina_runtime_graphics.c $(pkg-config --cflags cairo-xlib x11 libjpeg) && GFX_OK=1
fi
if pkg-config --exists alsa libmpg123; then
    build_runtime audio festina_runtime_audio.c $(pkg-config --cflags alsa libmpg123) && AUD_OK=1
fi

LIBS=(-lsqlite3 -lm -pthread)
OBJS=("$WORK/rt_core.o" "$WORK/rt_async.o" "$WORK/rt_thread.o")
[ $GFX_OK = 1 ] && { OBJS+=("$WORK/rt_graphics.o"); LIBS+=($(pkg-config --libs cairo-xlib x11 libjpeg)); }
[ $AUD_OK = 1 ] && { OBJS+=("$WORK/rt_audio.o"); LIBS+=($(pkg-config --libs alsa libmpg123)); }

PROGRAMS=("$@")
if [ ${#PROGRAMS[@]} -eq 0 ]; then
    PROGRAMS=("$ROOT"/tests/stress/thread_*.f)
fi

failures=0
for src in "${PROGRAMS[@]}"; do
    name="$(basename "$src" .f)"
    out="$WORK/$name"
    if ! (cd "$ROOT" && python3 -m festina.cli compile "$src" --emit-llvm) > "$out.ll" 2> "$out.compile.err"; then
        echo "FAIL $name -- compile"; sed 's/^/    /' "$out.compile.err"; failures=$((failures + 1)); continue
    fi
    sed -E 's/^(define [^{]+) \{/\1 sanitize_thread {/' "$out.ll" > "$out.tsan.ll"
    if ! "$IR_CC" -fsanitize=thread -g -O1 -c "$out.tsan.ll" -o "$out.o" 2> "$out.cc.err"; then
        echo "FAIL $name -- IR compile"; sed 's/^/    /' "$out.cc.err"; failures=$((failures + 1)); continue
    fi
    if ! "$SAN_CC" -fsanitize=thread -g -O1 "$out.o" "${OBJS[@]}" "${LIBS[@]}" -o "$out.bin" 2> "$out.link.err"; then
        echo "FAIL $name -- link"; sed 's/^/    /' "$out.link.err"; failures=$((failures + 1)); continue
    fi

    rundir="$WORK/run_$name"; mkdir -p "$rundir"
    cp "$ROOT"/tests/fixtures/* "$rundir/" 2>/dev/null || true
    if (cd "$rundir" && TSAN_OPTIONS="halt_on_error=1" "$out.bin" > "$out.stdout" 2> "$out.stderr"); then
        echo "ok   $name"
    else
        echo "FAIL $name -- exit $?"
        sed 's/^/    /' "$out.stderr" | head -60
        failures=$((failures + 1))
    fi
done

if [ "$failures" -ne 0 ]; then
    echo "thread_tsan_stress: $failures program(s) failed"
    exit 1
fi
echo "thread_tsan_stress: all clean"
