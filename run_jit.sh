#!/usr/bin/env bash
# Compile and run a .js file using the libLLVM-based JIT (no clang/llc needed).
# Useful in environments (like sandboxes/CI) that have libLLVM but not the
# full clang toolchain. Requires the runtime to already be built:
#   gcc -c -fPIC -O2 -o runtime/runtime.o runtime/runtime.c
#   gcc -shared -fPIC -O2 -o runtime/libjsruntime.so runtime/runtime.o \
#       /lib/x86_64-linux-gnu/libsqlite3.so.0 -lcairo -lm
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JS_FILE="$1"
IR_FILE="$(mktemp --suffix=.ll)"

python3 "$SCRIPT_DIR/compiler/jsc.py" "$JS_FILE" -o "$IR_FILE"
python3 "$SCRIPT_DIR/jit_run.py" "$IR_FILE" "$SCRIPT_DIR/runtime/libjsruntime.so"
rm -f "$IR_FILE"
