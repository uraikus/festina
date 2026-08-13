#!/usr/bin/env bash
# Build a standalone native binary from a .js file using a real clang/LLVM
# toolchain. Requires: clang, and dev headers/libs for sqlite3 + cairo, e.g.
# on Debian/Ubuntu:  sudo apt install clang libsqlite3-dev libcairo2-dev
#
# Usage: ./build.sh path/to/program.js [output_binary]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JS_FILE="$1"
OUT="${2:-a.out}"
IR_FILE="$(mktemp --suffix=.ll)"

echo "[1/3] compiling $JS_FILE -> LLVM IR"
python3 "$SCRIPT_DIR/compiler/jsc.py" "$JS_FILE" -o "$IR_FILE"

echo "[2/3] compiling runtime + IR with clang"
CAIRO_FLAGS="$(pkg-config --cflags --libs cairo 2>/dev/null || echo -lcairo)"
SQLITE_FLAGS="$(pkg-config --cflags --libs sqlite3 2>/dev/null || echo -lsqlite3)"

clang -O2 "$IR_FILE" "$SCRIPT_DIR/runtime/runtime.c" \
    $CAIRO_FLAGS $SQLITE_FLAGS -lm -o "$OUT"

echo "[3/3] done -> $OUT"
rm -f "$IR_FILE"
