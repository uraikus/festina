# Changelog

All notable changes to Festina are tracked here, starting from version
0.1. Versions follow `major.minor` (a `major.minor.patch` form is used if
a patch-only release is ever needed); dates are in `YYYY-MM-DD`.

This changelog starts from the point version tracking was introduced —
it is not a reconstruction of the project's earlier history. The full
round-by-round design and implementation record predating 0.1 lives in
[claude.md](claude.md).

## [0.2] - 2026-08-26

### Added

- `exec(args, callback)`: a non-blocking counterpart to `exec(args)`.
  Dispatches the same spawn to a background worker thread and returns
  immediately; `callback:func[int]:void` receives the real exit code once
  the child process exits. See [api.md](api.md#running-other-programs).

## [0.1] - 2026-08-26

### Added

- Version tracking: `festina.__version__`, and `festina --version` on the
  CLI.
- This changelog.

### Changed

- Project documentation (`README.md`, `api.md`, and every other `.md`
  file except `claude.md` and `tests/CONTRACT.md`) rewritten to describe
  the software as it stands today, rather than narrating how it got
  there.
