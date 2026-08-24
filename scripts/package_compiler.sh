#!/usr/bin/env bash
# "Real compilation, minimal setup" stage 2 (claude.md #59; see
# setup.md for the full staged plan) -- packages the Python compiler
# frontend (festina/) into a single standalone binary via PyInstaller,
# so *using* the Festina compiler no longer requires a separate Python
# install on the machine that runs it.
#
# This is a build-time step for maintainers/packagers producing a
# distributable `festina` binary, not something an end user needs to
# run themselves. The resulting binary still needs a C compiler/linker
# at runtime to actually build a Festina program (see setup.md) --
# stage 2 only removes the Python dependency, nothing else.
#
# Requires PyInstaller at build time only (not a runtime dependency of
# either festina/ or the binary it produces):
#   pip install -r requirements-build.txt
#
# On macOS (macos.md Phase 3) the binary is also ad-hoc codesigned
# (`codesign -f -s -`) after packaging, so Gatekeeper allows running it
# locally without prompting -- a self-signature, not an identity;
# it proves nothing to anyone the binary is handed to. Full Developer-ID
# signing + notarization is a distribution decision, deliberately out
# of scope until there is an actual distribution channel to justify it.
# The -f/--force matters: recent PyInstaller versions already ad-hoc
# sign the EXE themselves as part of building it (confirmed on real
# macOS CI, claude.md #126), and codesign refuses to resign an
# already-signed binary without it ("is already signed", nonzero exit).
#
# windows.md Phase 3 (claude.md #129): runs under MSYS2 bash on Windows
# (the windows CI job's own shell, and the only supported toolchain --
# windows.md's own decision, MSVC out of scope), detected via `$MSYSTEM`
# (set to e.g. "UCRT64" there -- this project's own already-proven
# Windows-CI detection signal, the same one festina/cli.py's doctor
# logic keys off) and nothing else this script needs to special-case
# for -- PyInstaller itself already emits `festina.exe` there with no
# extra flag, exactly like MinGW's linker already appends `.exe` to
# every OTHER compiled Festina program (windows.md Phase 0). The one
# real platform difference is `--add-data`'s SRC:DEST separator:
# PyInstaller's own documented spelling is `;` on Windows and `:`
# everywhere else -- a real `:` would be read as part of a Windows path
# instead (`C:\...`), not a separator, so this can't share one spelling
# across platforms the way every other flag here does.
#
# A second, real MSYS2 gotcha (found by real Windows CI): PyInstaller
# there is a NATIVE (non-MSYS) Windows executable, and MSYS2's bash
# automatically rewrites path-shaped arguments before such a process
# ever sees them -- ordinarily transparent, but it mishandles a
# COMPOUND `SRC;DEST` argument (`--add-data`'s own required shape).
# Every absolute path this script hands `pyinstaller` is converted to
# its real Windows form first via `cygpath -m` (an MSYS2 core utility,
# `-m` for the forward-slash "mixed" form so it concatenates cleanly
# onto a compound argument's suffix with no backslash to escape), and
# `MSYS2_ARG_CONV_EXCL="*"` -- the standard, documented way to tell
# MSYS2's runtime to leave EVERY argument of the next command alone --
# suppresses the automatic conversion that would otherwise re-mangle
# an already-correct path sitting inside that compound argument.
#
# The detection condition itself (`$MSYSTEM`, not the more commonly
# suggested `$OSTYPE == "msys"`) is the one thing that took four real
# Windows CI rounds to actually pin down, all against the identical
# symptom (`D:/d/a/festina/festina/...`, a doubled path) -- rounds one
# through three each fixed a real, plausible MSYS2/PyInstaller
# interaction (the `;` separator, `cygpath -m`, `MSYS2_ARG_CONV_EXCL`)
# that could never have mattered, because the `if` guarding all of it
# was never true in the first place: under the msys2/setup-msys2
# action's own `shell: msys2 {0}` wrapper (a cmd.exe-launched bash, not
# a plain interactive MSYS2 terminal), `$OSTYPE` apparently isn't the
# compiled-in "msys" that detection technique assumes elsewhere. Found
# by round three's own diagnostic stderr echoes -- not by what they
# printed, but by their total ABSENCE from the next real CI log,
# proving the whole block had never executed at all.
#
# Usage: ./scripts/package_compiler.sh [output_dir]
#   -> writes <output_dir>/festina (default: ./dist/festina), or
#      <output_dir>/festina.exe on Windows
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT_DIR="${1:-$REPO_ROOT/dist}"

if ! command -v pyinstaller >/dev/null 2>&1; then
    echo "error: pyinstaller is not installed (build-time only dependency)" >&2
    echo "install it with: pip install -r requirements-build.txt" >&2
    exit 1
fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

ADD_DATA_SEP=":"
FESTINA_BIN="$OUT_DIR/festina"
RUNTIME_DIR="$REPO_ROOT/runtime"
DISTPATH="$OUT_DIR"
WORKPATH="$WORK_DIR/build"
SPECPATH="$WORK_DIR"
# claude.md #129 round four: the actual root cause of three straight
# identical real-CI failures, found by what the round-three diagnostic
# echoes did NOT show rather than by what they did -- they never
# printed anything AT ALL, meaning this whole block, cygpath calls and
# all, had never once executed on real Windows CI. `${OSTYPE:-}
# == "msys"` was the wrong detection the entire time; under the
# msys2/setup-msys2 action's own `shell: msys2 {0}` wrapper (a cmd.exe-
# launched bash, not a plain interactive MSYS2 terminal), OSTYPE
# apparently isn't the compiled-in "msys" this technique assumes
# elsewhere. `$MSYSTEM`, by contrast, is directly confirmed present in
# every single CI log line's own "env:" block (set to `UCRT64`) and is
# already this project's own proven-working Windows-CI detection
# signal -- festina/cli.py's doctor logic already keys off it. Every
# fix attempted in earlier rounds (the `;` separator, `cygpath -m`,
# `MSYS2_ARG_CONV_EXCL`) was itself sound; none of it could ever have
# helped while the gate guarding all of it silently never opened.
if [[ -n "${MSYSTEM:-}" ]]; then
    ADD_DATA_SEP=";"
    FESTINA_BIN="$OUT_DIR/festina.exe"
    # -m (not -w): forward-slash Windows form, so RUNTIME_DIR
    # concatenates cleanly with the "/festina_runtime.c" suffix below
    # with no backslash to escape.
    RUNTIME_DIR="$(cygpath -m "$RUNTIME_DIR")"
    DISTPATH="$(cygpath -m "$DISTPATH")"
    WORKPATH="$(cygpath -m "$WORKPATH")"
    SPECPATH="$(cygpath -m "$SPECPATH")"
    # MSYS2_ARG_CONV_EXCL="*": PyInstaller under MSYS2 UCRT64 Python is
    # a native (non-MSYS) Windows executable, so MSYS2's bash otherwise
    # automatically rewrites path-shaped arguments before it ever sees
    # them -- including, unpredictably, an already-correct Windows path
    # sitting inside a compound `SRC;DEST` argument. This is the
    # standard, documented escape hatch: leave every argument of the
    # next command alone, no automatic conversion at all. Scoped to
    # this one `pyinstaller` invocation, and paired with converting
    # EVERY absolute path handed to it above (not just RUNTIME_DIR) --
    # --distpath/--workpath/--specpath would otherwise be relying on
    # that same automatic conversion for their own single, non-compound
    # path arguments, so disabling it wholesale without pre-converting
    # those too would trade one broken path for three different ones.
    export MSYS2_ARG_CONV_EXCL="*"
fi

cd "$REPO_ROOT"
pyinstaller \
    --onefile \
    --name festina \
    --distpath "$DISTPATH" \
    --workpath "$WORKPATH" \
    --specpath "$SPECPATH" \
    --add-data "$RUNTIME_DIR/festina_runtime.c${ADD_DATA_SEP}runtime" \
    --add-data "$RUNTIME_DIR/festina_runtime_graphics.c${ADD_DATA_SEP}runtime" \
    --add-data "$RUNTIME_DIR/festina_runtime_audio.c${ADD_DATA_SEP}runtime" \
    --add-data "$RUNTIME_DIR/festina_runtime_window_mac.m${ADD_DATA_SEP}runtime" \
    --add-data "$RUNTIME_DIR/festina_runtime_window_win32.c${ADD_DATA_SEP}runtime" \
    --add-data "$RUNTIME_DIR/festina_runtime.h${ADD_DATA_SEP}runtime" \
    --add-data "$RUNTIME_DIR/festina_runtime_internal.h${ADD_DATA_SEP}runtime" \
    --add-data "$RUNTIME_DIR/festina_runtime_window.h${ADD_DATA_SEP}runtime" \
    --paths . \
    packaging/festina_entry.py

if [[ "$(uname -s)" == "Darwin" ]] && command -v codesign >/dev/null 2>&1; then
    codesign -f -s - "$FESTINA_BIN"
    echo "ad-hoc codesigned $FESTINA_BIN"
fi

echo "wrote $FESTINA_BIN"
