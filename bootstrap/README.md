# bootstrap/

Festina's own compiler, written in Festina — the lexer, the parser and
semantic analysis, all three complete and all three agreeing with their
originals over the whole corpus.

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
| `semantic.f` | `festina/semantic.py`, ported (imports `parser.f`) |
| `semdumpf.f` | entry point: dumps semantic analysis in the canonical form |
| `semdump.py` | the Python side's canonical semantic-analysis dump |
| `semdiff.py` | diffs both analyzers over the same corpus |
| `cases/*.f` | targeted sources covering what the corpus doesn't reach |

`tests/test_bootstrap_lexer.py`, `test_bootstrap_parser.py` and
`test_bootstrap_semantic.py` run the same comparisons from pytest, so a
divergence fails CI rather than waiting to be noticed — on Linux, for
the reason below.

## Running it

```sh
python bootstrap/difftest.py                        # lexer, whole corpus
python bootstrap/astdiff.py                         # parser, whole corpus
python bootstrap/semdiff.py                         # analyzer, whole corpus
python bootstrap/difftest.py examples/hello.f       # just these files
```

Over the 94-file repository corpus:

- **lexer: 94 match, 0 differ.**
- **parser: 94 match, 0 differ, 0 unported.**
- **semantic: 94 match, 0 differ, 0 unported.**

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

The 94-file repository corpus is a strong oracle for ordinary code and
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

All three harnesses carry verified negative controls:

| break this | and this many files differ |
|---|---|
| longest-match on `++` | 17 |
| the regex-vs-division denylist | `OP\|/` where `REGEX\| 2 \|` belongs |
| character columns (use bytes) | exactly the 2 non-ASCII files |
| additive/multiplicative precedence | 25 |
| postfix `++` | 24 |
| assignability checking (accept everything) | 2 |
| the arrow-function counter (never advance it) | 1 |

That last row is why `cases/arrow_numbering.f` exists. Freezing the
counter changed nothing across the entire repository corpus, because no
file in it has more than one arrow function — every program produced
exactly `__festina_arrow_0`, and a counter that never advanced looked
correct. A case with three arrows, one nested inside another, is what
makes the numbering testable at all.

`difftest.KNOWN_DIVERGENCES` is the place to record a divergence that
genuinely cannot be fixed, so the decision lives next to the test
rather than in a commit message. It is empty.

## Semantic analysis: 94 match, 0 differ, 0 unported

All three stages of the front end now agree with their originals over
the whole corpus. `semantic.f` resolves declarations, merges imports,
walks scopes including thread bodies, descends into expressions, infers
types, and rejects the programs the original rejects.

**The type checker is conservative by construction.** `inferExpr`
answers "no type" for anything it does not understand, and every
caller treats that as "no opinion" and checks nothing. That is the only
safe shape for a partial checker inside a differential test: a missed
error leaves the port differing on a file the original rejects, which
is progress not yet made, while a false error turns a matching file
into a differing one, which is progress lost. It can be wrong in one
direction only.

Specification.md §10.2 is why this gets as far as it does with no
inference at all: "A declaration states its type; there is no `var`,
`let` or inference." Every `DECL` record's type therefore comes from a
declared type expression.

The one expression that *does* bind names is the arrow function: `void
(x:int) => log(x)` compiles to an ordinary top-level function
(claude.md #142), so analysing it defines a synthesized
`__festina_arrow_N` in the global scope plus a parameter for each of
its own. The descent that finds it is generic — every `node` and
`list` field of every node, in parser order — rather than a case per
expression kind, because a case list has to be complete to be correct
and goes quietly out of date the moment the grammar grows.

All three harnesses run from pytest, so a divergence fails CI rather
than waiting to be noticed — **on Linux only** (decisions.md #287).
They compile three Festina binaries and run them across the corpus, and
nothing in any of them is platform-specific: they compare two
implementations against each other, which is compiler-development
tooling rather than platform coverage. On Linux all three cost about 15
seconds together, which is why that is where they run.

The gate was justified partly on CI budget at the time, and that part of
the argument did not survive measurement — see decisions.md #287. The
short version: the Windows job ran the identical test set in **39:13**
on one commit and **22:15** on the next, where that next commit changed
two documentation files and nothing else. Hosted-runner wall-clock is a
sample, not a measurement, and the variance is larger than any harness
cost anyone has claimed to observe. The coverage argument above is the
one that holds.

`FESTINA_BOOTSTRAP_EVERYWHERE=1` runs them anyway, for confirming by
hand that the ports are not somehow platform-dependent.

## Then: codegen

About 14,500 lines of Python — more than everything ported so far
combined.

Neither depends on any language change. The ports use no `T?`, no
`free` and no `delete`: automatic reclamation handles the whole front
end unassisted.

`semdump.py` defines the oracle semantic analysis is ported against.
`analyze()` is a checker rather than an annotator — it raises, or
returns a symbol table, and writes nothing back onto the AST — so
diffing its return value alone would say nothing about the inside of a
function body. Instead the dump wraps `Scope.define`, the single
chokepoint every binding in the program passes through, and records
**the resolved type of every name the program binds, anywhere**:
globals, constants, functions, parameters, loop and catch variables,
and locals nested arbitrarily deep. The wrapper lives in the harness,
so the compiler carries no test-only hook.

Over the corpus that is 4,898 records across 82 analyzed files; the
11 rejected ones are all `cases/*.f`, which exist to be lexed rather
than to be valid programs. A rejection dumps `SEMERR|line|col` alone —
position, never message text.

`tests/test_bootstrap_semantic.py` pins the oracle's own discriminating
power, including that a type renderer collapsing every type into one
string fails three of its tests.

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
