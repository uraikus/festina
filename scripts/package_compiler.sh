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
# (`codesign -s -`) after packaging, so Gatekeeper allows running it
# locally without prompting -- a self-signature, not an identity;
# it proves nothing to anyone the binary is handed to. Full Developer-ID
# signing + notarization is a distribution decision, deliberately out
# of scope until there is an actual distribution channel to justify it.
#
# Usage: ./scripts/package_compiler.sh [output_dir]
#   -> writes <output_dir>/festina (default: ./dist/festina)
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

cd "$REPO_ROOT"
pyinstaller \
    --onefile \
    --name festina \
    --distpath "$OUT_DIR" \
    --workpath "$WORK_DIR/build" \
    --specpath "$WORK_DIR" \
    --add-data "$REPO_ROOT/runtime/festina_runtime.c:runtime" \
    --add-data "$REPO_ROOT/runtime/festina_runtime_graphics.c:runtime" \
    --add-data "$REPO_ROOT/runtime/festina_runtime_audio.c:runtime" \
    --add-data "$REPO_ROOT/runtime/festina_runtime_window_mac.m:runtime" \
    --add-data "$REPO_ROOT/runtime/festina_runtime.h:runtime" \
    --add-data "$REPO_ROOT/runtime/festina_runtime_internal.h:runtime" \
    --add-data "$REPO_ROOT/runtime/festina_runtime_window.h:runtime" \
    --paths . \
    packaging/festina_entry.py

if [[ "$(uname -s)" == "Darwin" ]] && command -v codesign >/dev/null 2>&1; then
    codesign -s - "$OUT_DIR/festina"
    echo "ad-hoc codesigned $OUT_DIR/festina"
fi

echo "wrote $OUT_DIR/festina"
