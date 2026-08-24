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
# windows.md's own decision, MSVC out of scope) via bash's own OSTYPE
# variable, set to "msys" there and nothing else this script needs to
# special-case for -- PyInstaller itself already emits `festina.exe`
# there with no extra flag, exactly like MinGW's linker already appends
# `.exe` to every OTHER compiled Festina program (windows.md Phase 0).
# The one real platform difference is `--add-data`'s SRC:DEST separator:
# PyInstaller's own documented spelling is `;` on Windows and `:`
# everywhere else -- a real `:` would be read as part of a Windows path
# instead (`C:\...`), not a separator, so this can't share one spelling
# across platforms the way every other flag here does.
#
# A second, real MSYS2 gotcha (found by real Windows CI, across two
# separate rounds): PyInstaller there is a NATIVE (non-MSYS) Windows
# executable, and MSYS2's bash automatically rewrites path-shaped
# arguments before such a process ever sees them -- ordinarily
# transparent, but it mishandles a COMPOUND `SRC;DEST` argument
# (`--add-data`'s own required shape), observed producing a doubled,
# broken path (`D:/d/a/festina/festina/...`). Round one tried
# converting SRC to its real Windows form first via `cygpath -m` (an
# MSYS2 core utility, `-m` for the forward-slash "mixed" form so it
# concatenates cleanly onto the compound argument's suffix with no
# backslash to escape) -- that alone was NOT enough: the identical
# doubled path came back regardless, meaning the automatic conversion
# re-mangles even an already-correct Windows path sitting inside a
# compound argument, not only a raw POSIX one. `cygpath -m` stays
# (still the correct value to hand it), paired now with
# `MSYS2_ARG_CONV_EXCL="*"` -- the standard, documented way to tell
# MSYS2's runtime to leave EVERY argument of the next command alone,
# no automatic conversion attempted at all, since this script is
# already supplying a correct native path itself and the automatic
# "help" is exactly what breaks it.
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
if [[ "${OSTYPE:-}" == "msys" ]]; then
    ADD_DATA_SEP=";"
    FESTINA_BIN="$OUT_DIR/festina.exe"
    # -m (not -w): forward-slash Windows form, so RUNTIME_DIR
    # concatenates cleanly with the "/festina_runtime.c" suffix below
    # with no backslash to escape.
    RUNTIME_DIR="$(cygpath -m "$RUNTIME_DIR")"
    DISTPATH="$(cygpath -m "$DISTPATH")"
    WORKPATH="$(cygpath -m "$WORKPATH")"
    SPECPATH="$(cygpath -m "$SPECPATH")"
    # Real Windows CI proved cygpath's own correct output alone is not
    # enough for --add-data specifically: MSYS2's automatic argv path
    # "conversion" re-mangles it anyway at exec time, producing the
    # identical broken doubled path (D:/d/a/...) whether or not this
    # file did its own conversion first -- the heuristic apparently
    # reprocesses an ALREADY-correct Windows path sitting inside a
    # compound SRC;DEST argument, not only a raw POSIX one.
    # MSYS2_ARG_CONV_EXCL="*" is the standard, documented escape hatch:
    # it tells MSYS2's runtime to leave every argument of the next
    # command alone, no automatic conversion at all. Scoped to this one
    # `pyinstaller` invocation, and paired with converting EVERY
    # absolute path handed to it above (not just RUNTIME_DIR) --
    # --distpath/--workpath/--specpath had been relying on that same
    # automatic conversion working correctly for their own single,
    # non-compound path arguments (and it does, there), so disabling it
    # wholesale without also pre-converting those would trade one
    # broken path for three different ones.
    export MSYS2_ARG_CONV_EXCL="*"
    # claude.md #129 round three: two straight real-CI rounds each
    # produced the IDENTICAL broken path (D:/d/a/festina/festina/...)
    # despite two DIFFERENT fix attempts (cygpath -m alone, then paired
    # with MSYS2_ARG_CONV_EXCL) -- reasoning further from here without
    # seeing what this script actually computed would be a third guess
    # in the dark, the same mistake claude.md #126 round eleven's own
    # instrumentation-over-guessing precedent exists to avoid. This
    # block is purely diagnostic, stderr-only, and never changes what
    # gets built -- it exists to turn the next real Windows CI log into
    # one that can actually distinguish "cygpath itself returned the
    # broken value" from "something downstream re-mangled a correct
    # one," which the error message alone cannot.
    echo "debug: OSTYPE=$OSTYPE" >&2
    echo "debug: cygpath resolves to: $(command -v cygpath || echo 'NOT FOUND')" >&2
    echo "debug: RUNTIME_DIR (post-cygpath) = $RUNTIME_DIR" >&2
    echo "debug: DISTPATH (post-cygpath) = $DISTPATH" >&2
    echo "debug: first --add-data value = $RUNTIME_DIR/festina_runtime.c${ADD_DATA_SEP}runtime" >&2
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
