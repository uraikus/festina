# bootstrap/

Festina's own compiler, written in Festina — the lexer, the parser and
semantic analysis all complete and all agreeing with their originals
over the whole corpus, and codegen begun.

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
| `codegen.f` | `festina/codegen.py`, in progress (imports `semantic.f`) |
| `irdumpf.f` | entry point: dumps LLVM IR |
| `irdump.py` | the Python side's IR, which needs no canonical form of its own |
| `irdiff.py` | diffs both code generators over the same corpus |
| `cases/*.f` | targeted sources covering what the corpus doesn't reach |

`tests/test_bootstrap_lexer.py`, `test_bootstrap_parser.py`,
`test_bootstrap_semantic.py` and `test_bootstrap_codegen.py` run the
same comparisons from pytest, so a divergence fails CI rather than
waiting to be noticed — on Linux, for the reason below.

## Running it

```sh
python bootstrap/difftest.py                        # lexer, whole corpus
python bootstrap/astdiff.py                         # parser, whole corpus
python bootstrap/semdiff.py                         # analyzer, whole corpus
python bootstrap/irdiff.py                          # codegen, whole corpus
python bootstrap/difftest.py examples/hello.f       # just these files
```

Over the 97-file repository corpus:

- **lexer: 97 match, 0 differ.**
- **parser: 97 match, 0 differ, 0 unported.**
- **semantic: 97 match, 0 differ, 0 unported.**
- **codegen: 3 match, 0 differ, 83 unported, 11 rejected by both** —
  103 of 155,742 file-specific IR lines. See below for why that is the
  number reported rather than a file count, and why it is expected to
  stay flat for several more increments.

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

The 97-file repository corpus is a strong oracle for ordinary code and
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

## Semantic analysis: 97 match, 0 differ, 0 unported

All three stages of the front end agree with their originals over the
whole corpus. `semantic.f` resolves declarations, merges imports,
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

## Codegen: 103 of 155,742 file-specific IR lines

About 14,500 lines of Python, more than everything ported so far
combined, and begun rather than finished. It depends on no language
change: the ports use no `T?`, no `free` and no `delete`, so automatic
reclamation handles the whole compiler unassisted.

**The oracle needed no design at all.** The other three stages required
a canonical form to be invented for them, because their outputs are
objects with no text form of their own. Codegen's output *is* text, so
`irdump.py` is `generate_ir` and the comparison is the IR, line for
line. It is the strongest oracle in the project and the only one with no
judgement in it.

It needed two corrections all the same, both found by measuring:

**`CodeGen._uid` is a class attribute.** `_unique()` does
`CodeGen._uid += 1`, so the counter keeps climbing across every CodeGen
instance in a process, and generating IR for one file twice produces two
different texts — identical in structure, every generated name shifted
by a constant:

```
-@__festina_stmtcache_7       +@__festina_stmtcache_17
-%a.1  %b.2  %dx.3            +%a.11 %b.12 %dx.13
```

Nothing is wrong with the compiler; the CLI compiles one file per
process. But an in-process oracle would hand the port a moving target,
and the failure would read as a port bug in every file after the first.
`irdump.dump_file` resets it, and the reset is checked against a
genuinely fresh subprocess rather than trusted.

**The IR embeds its own source path**, on line 2, so a dump taken with
an absolute path bakes the checkout's location into the expected output.
`irdiff` makes every path repo-relative before either side sees it —
with absolute paths every file's IR contains `/home/user/festina`, with
relative paths none does.

### Why the number here is lines and not files

**382 of every module's lines are the identical runtime declaration
block** — 94% of the smallest file in the corpus, 23% of all its IR. A
port able to do nothing but print that block is already within a couple
of dozen lines of matching `benchmarks/hello.f`.

Worse, 11 corpus files are `cases/*.f`, which exist to be lexed rather
than to be valid programs: both implementations answer a bare
`SEMERR|line|col` for them and "agree" without either generating
anything. The first real run reported **12 match** on a port that could
emit exactly one module — 22 file-specific lines out of 153,320, or
0.014%.

So `irdiff.py` reports those eleven as *rejected by both*, separately
from matches, and coverage is measured as file-specific IR lines
reproduced. The eleven prove only that both sides reject the same
programs, which is semantic analysis's claim and not codegen's. A number
that flatters by three orders of magnitude is worse than no number.

`tests/test_bootstrap_codegen.py` pins all of it, including a control
that disables the `_uid` reset and confirms two dumps then diverge —
which is what caught the reproducibility tests being pointed at
`benchmarks/hello.f`, a program that calls `_unique()` exactly zero
times and so could not have varied either way.

### What is left

`StructDecl` is done: one `%struct.Name = type { ... }` per declaration,
every field lowered to its LLVM scalar. The trap there is `color`, which
is the only type outside int/float/bool that does **not** lower to
`ptr` — a packed RGBA value in an `i64` — so treating "not a scalar
primitive" as "pointer" silently mislays the layout of every struct
with a color field.

**Closing it unlocked nothing**, and that is the useful part. It was
listed against 22 files; after it landed, coverage stayed at exactly 103
lines and 3 files. Every one of those 22 simply hit whatever was behind
it. The blocker table had been a histogram of *first* blockers, which
reads as a promise it cannot keep, so `cgUnported` now records every
distinct reason and the table carries two columns:

|blocks|only|construct|
|---:|---:|---|
|42|1|`FuncDecl`|
|40|2|`WhileStmt`|
|37|0|`log(Identifier)`|
|36|1|a declaration of a non-scalar type|
|19|0|`EventHandler`|
|19|0|a non-call expression statement|
|18|0|`ThreadDecl`|
|14|1|`ForStmt`|
|12|0|`log(Call)`|
|10|0|`BinOp` initializer|
|10|0|a call through a non-identifier callee|
|10|0|`TableDecl`|

Only the second column predicts anything. `FuncDecl` appears in 42
files and is the last thing in the way for **one**.

**How far each file is, measured rather than guessed:** across the 83
unported files the median is **4 distinct blockers**, the maximum 12,
and only **5 files are a single construct away** (11 within two). So
codegen coverage will stay near zero through several more increments and
then move in steps, rather than climbing steadily the way the lexer's
did. That is what a real program using most of the language looks like
from the inside, and it is worth knowing before a run of flat numbers
gets read as no progress.

The structural obstacles are unchanged:

- **Festina structs have no methods.** 227 class methods and 77 AST and
  type classes become free functions over explicit state, the shape
  `parser.f` already uses.
- **`isinstance` dispatch**, 480 sites, becomes the generic node's kind
  string.
- **`map[T]` keys are `text`.** Three side tables keyed by node identity
  become a field on the node itself.
- **One return value per function**, so 259 tuple returns become structs.

What codegen needs from the analyzer is small and already mostly there:
`structs`, `tables`, `enums`, `threads`, and the two message types.
`codegen.f` imports `semantic.f` and reads them the way `CodeGen` reads
them off `AnalyzedProgram`.
