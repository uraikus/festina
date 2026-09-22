#!/usr/bin/env bash
# decisions.md #344: the whole test suite, as fast as it goes safely.
#
# Two runs, not one, because the suite has two populations with
# opposite needs:
#
#   tests/test_bootstrap_*.py  -- 92.8% of the wall time (6,116s of
#       6,593s), almost all of it 161 canaries, each building a whole
#       compiler from a deliberately broken copy of bootstrap/. They
#       share nothing: canary.build_patched copies the tree into its
#       own TemporaryDirectory and never writes to the repository,
#       which tests/test_bootstrap_canary.py asserts per canary. No
#       ports, no X display, no audio device. Run in parallel.
#
#   everything else -- 583s, and full of tests that each want a
#       singleton the machine only has one of: a TCP port named in a
#       Festina literal, an X server, an ALSA device. Several of them
#       only ever passed because nothing else was competing; running
#       them four-wide turned up four separate latent races (see #343
#       and #344). All four are fixed. Run serially anyway: the fixes
#       removed the races that were found, not the shared singletons
#       that make this population able to have them.
#
# Measured through this script, twice: 36m40s and 43m44s (the parallel
# half 1,685s and 2,040s, the serial one 515s and 583s), against
# 6,592.77s (1:49:52) for a plain serial run -- 2.5x to 3.0x. The
# spread is what a shared four-core machine does to a CPU-bound
# workload; neither number is the number. Running EVERYTHING in
# parallel instead measured 1,844s, bought by letting tests race for a
# port, an X server and an audio device. Not worth it; #344 has the
# reasoning.
#
# `--dist worksteal` rather than xdist's default `load` is not a
# detail: `load` hands each worker a consecutive chunk of the
# collection up front, which puts every canary on one worker and drops
# the return from 3.7x to 1.2x. #344 has the arithmetic.
set -uo pipefail

cd "$(dirname "$0")/.."

BOOTSTRAP=(tests/test_bootstrap_*.py)
IGNORES=()
for f in "${BOOTSTRAP[@]}"; do IGNORES+=("--ignore=$f"); done

echo "== bootstrap differential (parallel) =="
python -m pytest "${BOOTSTRAP[@]}" -n auto --dist worksteal "$@"
bootstrap_status=$?

echo
echo "== everything else (serial) =="
python -m pytest "${IGNORES[@]}" "$@"
rest_status=$?

# Both always run: a red bootstrap differential should not hide a red
# behavioural suite, and vice versa. pytest's exit code 5 means "no
# tests collected", which is a real problem here rather than a pass --
# it would mean the ignore list swallowed everything.
echo
if [ "$bootstrap_status" -ne 0 ] || [ "$rest_status" -ne 0 ]; then
    echo "FAILED (bootstrap: $bootstrap_status, rest: $rest_status)"
    exit 1
fi
echo "OK"
