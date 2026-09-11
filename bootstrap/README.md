# bootstrap/

Festina's own compiler, written in Festina — the lexer and the parser,
both complete.

Nothing in the shipped compiler depends on this directory. It exists to
be a demanding real program in the language, and to be checked against
the Python implementation it mirrors.

## What's here

| | |
|---|---|
| `lexer.f` | `festina/lexer.py`, ported — the importable library |
| `lexdump.f` | entry point: dumps a token stream in the canonical form |
| `parser.f` | `festina/parser.py`, ported (imports `lexer.f`) |
| `astdumpf.f` | entry point: dumps an AST in the canonical form |
| `difftest.py` | diffs both lexers over every `.f` file in the repo |
| `astdump.py` | the Python side's canonical AST dump |
| `astdiff.py` | diffs both parsers over the same corpus |
| `cases/*.f` | targeted sources covering what the corpus doesn't reach |

`tests/test_bootstrap_lexer.py` and `tests/test_bootstrap_parser.py` run
the same comparisons from pytest, so a divergence fails CI rather than
waiting to be noticed.

## Running it

```sh
python bootstrap/difftest.py                        # lexer, whole corpus
python bootstrap/astdiff.py                         # parser, whole corpus
python bootstrap/difftest.py examples/hello.f       # just these files
```

Over the 89-file repository corpus:

- **lexer: 89 match, 0 differ.**
- **parser: 89 match, 0 differ, 0 unported.**

The lexer lexes itself; the parser parses itself. Lexing and parsing
`parser.f`, the largest source in the corpus at ~1,200 lines, takes
about 60 ms.

## Why a port, not a rewrite

The only claim worth making about a reimplementation is that it agrees
with the original. `lexer.f` and `parser.f` are therefore deliberate
translations of `festina/lexer.py` and `festina/parser.py` — same token
kinds, same keyword sets, same precedence — checked by diffing a
canonical dump from each side over the whole corpus.

The lexer has one structural difference from its original: where the
Python lexer uses a single master regex with named groups and
`lastgroup`, this is a hand-written byte scanner, because Festina's
`regex` is POSIX ERE with no named groups. The alternation order of
`TOKEN_SPEC` is load-bearing — Python's `re` alternation is
leftmost-**first**, not longest-match, so the scanner tries the same
kinds in the same order. Getting that wrong is how `x++` becomes
`+` `+`.

The parser's AST is one generic node — a kind plus a list of named
fields — rather than the ~45 structs mirroring `festina/ast.py` would
need. That is what lets the dump be generic on both sides; a per-node
dumper would be a second parser to keep in sync.

Both sides print one token per line in the same canonical form:

```
line:col|KIND|value            # value escaped: \\ \n \t \r \p (|)
line:col|REGEX|pattern|flags
line:col|LEXERR|char           # a rejected source reports only this
```

## The UNPORTED marker

A construct with no implementation produces an `(UNPORTED ...)` node,
and `astdiff.py` counts a file containing one as *unported* — never as
a match, never as a difference. Coverage therefore only moves when
something is really implemented, and a construct that silently
mis-parses shows up as a difference rather than as progress.

Nothing in the corpus reaches it today. It is kept so the next
construct the grammar grows announces itself rather than mis-parsing;
the one construct still without an implementation is the `http {...}`
anonymous send, which no corpus file uses.

## What the corpus does and doesn't prove

The 89-file repository corpus is a strong oracle for ordinary code and
a weak one for edge cases — it contains no ambiguous `/` at all, and
block comments appear in exactly one file. `cases/` closes that, and
its own coverage is checked rather than assumed: deleting the
regex-vs-division denylist from `lexer.f` must make the test fail, and
`tests/test_bootstrap_lexer.py::TestTheDifferentialTestCanFail` pins
that discipline.

That check earns its place. A `division_vs_regex.f` with one `/` per
line passes whether or not the denylist works at all, because a failed
regex attempt falls back to division on its own. Two `/` on one line is
what actually tests it.

Both harnesses carry verified negative controls:

| break this | and this many files differ |
|---|---|
| longest-match on `++` | 17 |
| the regex-vs-division denylist | `OP\|/` where `REGEX\| 2 \|` belongs |
| character columns (use bytes) | exactly the 2 non-ASCII files |
| additive/multiplicative precedence | 25 |
| postfix `++` | 24 |

`difftest.KNOWN_DIVERGENCES` is the place to record a divergence that
genuinely cannot be fixed, so the decision lives next to the test
rather than in a commit message. It is empty.

## Next: semantic analysis, then codegen

Together about 20,000 lines of Python — more than everything ported so
far combined, and at the ratio the lexer and parser came out at,
25,000–35,000 lines of Festina.

Neither depends on any language change. The ports use no `T?`, no
`free` and no `delete`: automatic reclamation handles the whole front
end unassisted.

What makes semantic analysis harder than either port so far is the
oracle. A lexer has a token stream and a parser has an AST dump — both
total, canonical and line-comparable. Semantic analysis produces an AST
*annotated* with resolved types plus a set of accepted and rejected
programs, and a port that cannot be diffed cannot be verified. Defining
that dump is the first task, not the last.

Codegen has the strongest oracle in the project and needs no design
work: its output is LLVM IR text, comparable byte for byte.

The obstacles are structural rather than semantic:

- **Festina structs have no methods.** 227 class methods and 77 AST and
  type classes become free functions over explicit state, the shape
  `parser.f` already uses.
- **`isinstance` dispatch**, 480 sites, becomes the generic node's kind
  string.
- **`map[T]` keys are `text`.** Three side tables keyed by node identity
  become a field on the node itself.
- **One return value per function**, so 259 tuple returns become structs.
