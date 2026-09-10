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
line:col|KIND|value            # value escaped: \\ \n \t \r \p (|)
line:col|REGEX|pattern|flags
line:col|LEXERR|char           # a rejected source reports only this
```

Current state: **85 files match, 0 differ, 0 known divergences, 0 skipped.**

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

## What this told us about the language

Four limits, recorded in full in claude.md #271. Three are now fixed
(claude.md #272), which is what this exercise was for:

- **`ascii` could not read 3 of the 69 corpus files.** `text.toAscii()`
  validates and answers `null` for non-ASCII, but a lexer for a UTF-8
  language must carry those bytes through string literals untouched.
  **Fixed:** `blob.byteAt(i)`/`blob.slice(a, b)` give the read half of a
  byte buffer on the type that already holds a file's bytes, and
  `lexer.f` now scans the source blob by byte offset. All 69 files lex.
- **`text` could not hold a NUL**, so it could not represent a value its
  own lexer produced (`'a\0b'.length` answered 1). **Fixed:** the `\0`
  escape is a compile error now.
- **`text` had no `.trim()`.** **Fixed:** it does.
- **`int / int` promotes to float**, so there is no integer midpoint to
  binary-search with. *Not* fixed — that is claude.md #61's rule working
  as designed; `lexer.f` walks a forward-only line cursor instead.

And one the differential test found that neither lexer showed alone:

- **A column is a *character* offset, not a byte offset.** Python's
  lexer indexes `str`, so it counts code points for free. Scanning bytes
  gives a different answer on any line with non-ASCII before the token —
  and that column is what every compile error's caret points at. Only
  running both lexers against real non-ASCII source made it visible.

## Recording a divergence

`difftest.KNOWN_DIVERGENCES` is empty, and kept rather than deleted. It
held exactly one entry — the `\0` case above — which was fixed in the
language instead of tolerated here. If a future divergence genuinely
cannot be fixed, that table is where it goes, so the decision lives next
to the test rather than in a commit message.

## Next

The parser is the natural next step, and it needs none of the above
fixed. Semantic analysis and codegen should wait for the `?` cell model
(the plan's Part 2), which is a documented breaking change to `?`
semantics and would otherwise land under a half-ported compiler.
