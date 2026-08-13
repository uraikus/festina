"""PyInstaller entry point for the packaged `festina` compiler binary --
"real compilation, minimal setup" stage 2 (claude.md #59; see
README.md's "Deployment" section for the full staged plan and
scripts/package_compiler.sh for how this gets built).

This isn't part of the normal dev workflow -- `bin/festina` still execs
`python3 -m festina.cli` directly, unchanged, and remains the fastest
way to run the compiler from a checkout. This script exists only
because PyInstaller needs a real top-level script (not a `-m` module
target with relative imports) to analyze as its entry point; it does
nothing `festina.cli.main` doesn't already do on its own.
"""
import sys

from festina.cli import main

if __name__ == "__main__":
    sys.exit(main())
