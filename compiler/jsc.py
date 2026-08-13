#!/usr/bin/env python3
"""jsc: compile the JS subset to LLVM IR.

Usage:
    python3 jsc.py input.js -o output.ll
"""
import argparse
import sys
from codegen import compile_to_ir


def main():
    ap = argparse.ArgumentParser(description='Compile a JS subset to LLVM IR')
    ap.add_argument('input', help='input .js file')
    ap.add_argument('-o', '--output', help='output .ll file (default: stdout)')
    args = ap.parse_args()

    with open(args.input) as f:
        src = f.read()

    try:
        ir = compile_to_ir(src)
    except (SyntaxError, NotImplementedError, NameError) as e:
        print(f'compile error: {e}', file=sys.stderr)
        sys.exit(1)

    if args.output:
        with open(args.output, 'w') as f:
            f.write(ir)
    else:
        print(ir)


if __name__ == '__main__':
    main()
