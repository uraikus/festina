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
# A second, real MSYS2 gotcha (found by real Windows CI): PyInstaller
# there is a NATIVE (non-MSYS) Windows executable, so MSYS2's bash
# automatically rewrites any argument that looks like a POSIX path
# (e.g. `/d/a/festina/festina/...`, what `pwd` returns under MSYS2)
# into its real Windows form before the native process ever sees it --
# ordinarily transparent, but that auto-conversion gets confused by a
# COMPOUND `SRC;DEST` argument (`--add-data`'s own required shape) and
# mis-converts it, observed producing a doubled, broken path
# (`D:/d/a/festina/festina/...` -- the drive letter prepended to the
# ALREADY-POSIX path rather than replacing its `/d` prefix). The fix is
# converting each SRC to its native form ourselves via `cygpath -m`
# (an MSYS2 core utility, always present -- the `-m` "mixed" form is a
# real absolute Windows path, drive letter and all, just with forward
# slashes, so it concatenates cleanly with the `/festina_runtime.c`
# suffix below without a stray backslash) before it ever reaches a
# compound argument, sidestepping automatic conversion entirely rather
# than fighting its compound-argument blind spot.
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
if [[ "${OSTYPE:-}" == "msys" ]]; then
    ADD_DATA_SEP=";"
    FESTINA_BIN="$OUT_DIR/festina.exe"
    # -m (not -w): forward-slash Windows form -- still a real, absolute
    # Windows path (so MSYS2's auto-conversion leaves it alone), but
    # avoids a backslash immediately butting up against the
    # concatenated "/festina_runtime.c" suffix below, which `-w`'s
    # backslash form would.
    RUNTIME_DIR="$(cygpath -m "$RUNTIME_DIR")"
fi

cd "$REPO_ROOT"
pyinstaller \
    --onefile \
    --name festina \
    --distpath "$OUT_DIR" \
    --workpath "$WORK_DIR/build" \
    --specpath "$WORK_DIR" \
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
