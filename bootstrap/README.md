# bootstrap/

Festina's own compiler, being rewritten in Festina — starting with the
lexer.

## What's here

| | |
|---|---|
| `lexer.f` | `festina/lexer.py`, ported to Festina |
| `difftest.py` | runs both lexers over every `.f` file in the repo and diffs the token streams |
| `cases/*.f` | targeted sources covering what the repo corpus doesn't reach |

`tests/test_bootstrap_lexer.py` runs the same comparison from pytest, so
a divergence fails CI rather than waiting to be noticed.

## Running it

```sh
python bootstrap/difftest.py                        # the whole corpus
python bootstrap/difftest.py examples/hello.f       # just these files
```

Both sides print one token per line in the same canonical form:

```
line:col|KIND|value            # value escaped: \\ \n \t \r \p (|) \z (NUL)
line:col|REGEX|pattern|flags
line:col|LEXERR|char           # a rejected source reports only this
```

Current state: **80 files match, 0 differ, 1 known divergence, 3 skipped.**

## Why a port, not a rewrite

The only claim worth making about a reimplementation is that it agrees
with the original. `lexer.f` is therefore a deliberate translation of
`festina/lexer.py` — same token kinds, same keyword sets, same
precedence — with one structural difference: where the Python lexer uses
a single master regex with named groups and `lastgroup`, this is a
hand-written character scanner, because Festina's own `regex` is POSIX
ERE with no named groups, and scanning is what `ascii` exists for.

The alternation order of `TOKEN_SPEC` is load-bearing. Python's `re`
alternation is leftmost-**first**, not longest-match, so the scanner
tries the same kinds in the same order. Getting that wrong is how `x++`
becomes `+` `+`.

## What the corpus does and doesn't prove

The 69-file repo corpus is a strong oracle for ordinary code and a weak
one for edge cases — it contains no ambiguous `/` at all, and block
comments appear in exactly one file. `cases/` exists to close that, and
its own coverage is checked rather than assumed: deleting the
regex-vs-division denylist from `lexer.f` must make the test fail, and
`tests/test_bootstrap_lexer.py::TestTheDifferentialTestCanFail` pins
that discipline.

That check has already earned its place. The first `division_vs_regex.f`
put one `/` per line, which passes whether or not the denylist works at
all — a failed regex attempt falls back to division on its own. Two `/`
on one line is what actually tests it.

## The one known divergence

`cases/strings_and_escapes.f` contains `'a\0b'`. The Python lexer
produces the full three-character value; `lexer.f` produces `a`.

This is not a bug in the port and cannot be fixed there: Festina's
`text` is NUL-terminated, so `'a\0b'.length` is `1` in *any* Festina
program. The port can only be as expressive as the language it is
written in, and this is the first place that bites. `difftest.py` records
it in `KNOWN_DIVERGENCES` and the pytest suite xfails it, so it stays
visible instead of being quietly dropped from the corpus.

## What this told us about the language

Findings are recorded in full in claude.md #271. In short:

- **`ascii` cannot read 3 of the 69 corpus files.** `text.toAscii()`
  validates and answers `null` for non-ASCII, but a lexer for a UTF-8
  language must carry non-ASCII bytes through string literals untouched.
  This is the single biggest obstacle to going further.
- **`text` cannot hold a NUL**, as above.
- **`text` has no `.trim()`** — `lexer.f` carries its own.
- **`int / int` promotes to float**, so there is no integer midpoint to
  binary-search with; `lexer.f` uses a forward-only line cursor instead.

## Next

The parser is the natural next step, and it needs none of the above
fixed. Semantic analysis and codegen should wait for the `?` cell model
(the plan's Part 2), which is a documented breaking change to `?`
semantics and would otherwise land under a half-ported compiler.
