# The runtime's own dependencies, and rewriting them in Festina

A compiled Festina program links C libraries this project did not
write: SQLite, Cairo, libX11, libjpeg, ALSA, mpg123 and mbedTLS. Each
is a package somebody has to install before a program using that
feature will compile, which is the cost [setup.md](setup.md)'s
dependency table spends most of its length on.

This document is the specification and plan for removing as many of
them as can honestly be removed, by writing the work in Festina
instead. It is deliberately not a promise to remove all seven: two of
them should not be rewritten, and the reasons are below rather than
discovered later.

## What is actually being depended on

Measured, not estimated — distinct symbols referenced anywhere under
`runtime/`:

| dependency | symbols | tier | what it does for us |
|---|---:|---|---|
| Cairo | 68 | graphics | path filling, stroking, transforms, gradients, PNG, text |
| mbedTLS | 42 | TLS | `openSecurePort()`'s handshake and record layer |
| libX11 | 38 | graphics | the window, its events, the cursor |
| SQLite | 27 | **core** | every `table`, always linked |
| libjpeg | 10 | graphics | JPEG decode |
| ALSA | 9 | audio | the PCM device |
| mpg123 | 9 | audio | MP3 decode |

Symbol count is not difficulty. SQLite is 27 symbols and roughly
150,000 lines of C; mpg123 is 9 symbols and a few thousand. The table
says what the seam looks like, not what is behind it.

## What Festina can express today, and what it cannot

This is the part that decides the plan, and it was checked rather than
assumed.

**`arr[int]` is mutable and indexable.** `a[i] = v` works. A decoder
can therefore build a pixel buffer or a sample buffer in ordinary
Festina, and a rasterizer can write into one. This is what makes any
of this possible.

**`blob` is read-only in the way that matters.** `.length`,
`.byteAt(i)` and `.slice(a, b)` read bytes; the only writer is
`.write(text)`, and `text` stops at its first NUL
([specification.md](specification.md) §8's `blob`-to-`text` rule). So a
Festina program can *consume* arbitrary bytes and cannot *produce*
them. Every decoder is expressible; no encoder is.

That gap is already [todo.md](todo.md)'s open item — "the write half:
`[i] =` assignment into a mutable, indexable buffer, for binary
protocol and data construction" — and it is a prerequisite here rather
than a nice-to-have.

**There is no syscall surface.** Festina has no `ioctl`, no
unix-domain sockets, no `mmap`, no FFI. Anything whose job is to talk
to a device or speak a wire protocol to a local server cannot be
written in Festina at all, however much arithmetic it also does.

## The three answers

### Rewrite: Cairo's drawing, libjpeg, mpg123

All three are pure computation over bytes, producing buffers. Nothing
in them needs the OS. They are the whole of the achievable program and
they remove **three of seven** dependencies outright.

### Rewrite, but only after the write half exists: PNG encode

PNG *decode* is inflate plus unfiltering — pure computation, and
expressible today. PNG *encode* has to emit bytes, so it waits on the
language feature above. Until then `img.save()` keeps its C path.

### Do not rewrite: SQLite, mbedTLS, libX11, ALSA

**SQLite** is the one always-on dependency, and replacing it means a
SQL parser, a B-tree, a pager, and WAL-mode crash recovery whose
failure mode is a corrupted database rather than a wrong pixel. It is
also the thing every `table` test in this repository already trusts.
Not a rewrite — a different project.

**mbedTLS** should stay on its own merits. Writing a TLS stack is a
recognised way to introduce security bugs that are invisible until
someone exploits them, and unlike a wrong glyph, a wrong padding check
does not show up in a pixel assertion.
[security.md](security.md) would have to say this runtime rolls its
own TLS, which is not a sentence worth writing.

**libX11 and ALSA** are the two that are simply out of reach: one
speaks a wire protocol over a unix socket, the other drives `/dev/snd`
with `ioctl`. Festina has neither. They stay in C regardless of how
much else moves.

So the end state is four dependencies rather than seven, and the
graphics tier stops needing Cairo and libjpeg — leaving libX11 for the
window itself, which is what a window fundamentally is.

## Font discovery is the hard part of text, not glyph rasterisation

`cairo_select_font_face(cr, "sans-serif", …)` appears at 8 call sites
and hides the entire question of *which file is sans-serif on this
machine* — fontconfig on Linux, DirectWrite on Windows, CoreText on
macOS. Rasterising a glyph once you have its outline is ordinary
computation and belongs in Festina; deciding which font to open is a
platform service.

Two options, and this plan does not pick between them yet because the
choice deserves its own decision:

- **Bundle a font.** One checked-in TrueType file, always available,
  identical output everywhere. Costs repository size and the ability
  to honour a user's system fonts.
- **Keep a thin discovery shim in C.** Three small platform functions
  answering "give me a path for this family", with everything after
  that in Festina.

Nothing else in the plan depends on which is chosen, so it is
sequenced last.

## Conditional compilation

The requirement is that a program only carries what it uses, which is
what [security.md](security.md#slim-binaries) already promises for the
C runtime: `festina/cli.py` selects translation units per feature from
`_RUNTIME_FEATURES`, driven by `gen.uses_graphics`, `gen.uses_audio`
and friends.

Festina-implemented pieces need the same property by a different
mechanism, because they are not translation units. The design:

- Each rewritten component is a Festina source file under
  `runtime/festina/` — `jpeg.f`, `inflate.f`, `raster.f`, `mp3.f`.
- The compiler links one in **only** when the analysed program reaches
  the builtin that needs it, by the same `uses_*` flags that already
  select C objects. `img x = 'photo.jpg'` pulls in `jpeg.f`; a program
  that never mentions `img` gets none of it.
- The mechanism is an **implicit import**, and it needs no new
  machinery: `festina/imports.py` already merges an imported file into
  one `ast.Program`, so a compiler-injected import is the same thing a
  written one is. Checked before this plan was committed rather than
  assumed — a Festina `adler32` over `arr[int]`, imported and called,
  agrees with zlib on the first string tried.
- The IR for an unused component is never generated, so this is a
  compile-time decision rather than dead code the linker strips. That
  matters: it is the difference between a smaller binary and a binary
  that never mentioned the code at all.

This is a real change to the compiler, not just to the runtime, and it
is Phase 0 for that reason — every later phase needs it to exist.

**Phase 0 has shipped.** `festina/imports.py` carries
`RUNTIME_TRIGGERS`, a table of component name to predicate, and
`build_program` prepends a triggered component's statements to the
program. Prepended rather than appended because
[specification.md](specification.md) §7.2 wants a global to precede its
first use, and a component that declares one would otherwise declare it
after the program reading it.

The table is **empty**, deliberately. A component nothing triggers is
code in every binary for no reason, and one whose predicate always
fires is the same thing louder; phase 1's decoder adds the first entry.
The mechanism is still tested in both directions today, because
`build_program` takes a trigger table as a parameter —
`tests/test_runtime_components.py` supplies its own. Injection was also
watched failing: with the prepend removed, four of its thirteen tests
go red and the nine asserting ABSENCE correctly stay green.

`runtime/festina/checksums.f` is the first component, and it is real
rather than a fixture: Adler-32 and CRC-32 are what a PNG carries, so
phase 1 needs both. They agree with zlib byte for byte over five
payloads including the empty one and all 256 byte values — the
differential shape this plan wants for every decoder, at the smallest
scale it applies to.

## Plan

Each phase ends green, with the dependency it removes actually gone
from `setup.md`'s table rather than merely unused.

| phase | delivers | removes | needs |
|---|---|---|---|
| 0 ✅ | conditional linking of Festina runtime sources | — | compiler change |
| 1 ✅ | `inflate.f`, PNG decode | — | phase 0 |
| 2 | `jpeg.f` | **libjpeg** | phase 0 |
| 3 | `mp3.f` | **mpg123** | phase 0 |
| 4 | `raster.f` — paths, AA fill, stroke, clip, gradients, compositing | — | phase 0 |
| 5 | glyph rasterisation + the font-discovery decision | **Cairo** | phase 4 |
| 6 | PNG encode | — | the `blob` write half |

Phases 1–3 are independent of 4–5 and can land in any order. Phase 6
is gated on a language feature that does not exist yet.

### Phase 1, as built

`runtime/festina/inflate.f` is DEFLATE (RFC 1951) — stored, fixed and
dynamic blocks, with back-references copied a byte at a time because a
run of 300 zeros is a distance of 1 and a length of 300, and slicing a
range would read bytes it has not produced yet. Structured after
zlib's `puff` reference decoder rather than its production one: a
symbol is decoded by walking code lengths shortest-first, which needs
two small arrays per table instead of a multi-level lookup. Slower per
symbol, and readable straight against the RFC.

`runtime/festina/png.f` is the decoder on top: chunk walk, IDAT
concatenation (one zlib stream may be split across chunks), the five
scanline filters, and widening colour types 0/2/3/4/6 to RGBA.

**Bit depth 8 and non-interlaced only, and the refusal is the
feature.** A 16-bit or Adam7 file sets `PNG_ERR` and answers an empty
array rather than decoding as if it were something else. A caller can
fall back from a refusal; it cannot un-see plausible wrong pixels.

Both are differential against zlib and against a Python reference
encoder, which is the harness shape this plan asks for: 12 inflate
cases across three compression levels — empty input, a 300-zero run,
all 256 byte values, repetitive text — and for PNG, each of the five
colour types and each of the five filters separately. Separately
matters: adaptive encoders pick a filter per row, so a Paeth bug shows
on roughly one real file in ten and would sit untested behind
whichever filter the encoder happened to choose.

Neither is wired to `img` yet. `RUNTIME_TRIGGERS` stays empty until
that wiring, which is its own step and the one that makes the
bootstrap differential care.

## Tests

**The existing tests largely stand, and that is the point.** They
assert what a Festina program does, not which library did it —
`test_codegen.py`'s image and audio suites decode a saved PNG and
check pixels, and those assertions are exactly as valid against a
Festina rasteriser.

Two things do change, and both are real costs rather than bookkeeping:

**Exact-pixel assertions will move.** There are 163 pixel assertions in
`test_codegen.py`, many of the form `assert pixel(60, 60) == (200, 30,
30)`. Flat fills stay exact. Anything anti-aliased — circle edges,
glyph coverage, gradient stops — will differ from Cairo in the last
bit or two, because a different rasteriser makes different choices.
Those assertions move to properties that are true of any correct
rasteriser (this pixel is inside and red, this one is outside and
untouched, this edge is neither fully on nor fully off) rather than to
looser ones. An assertion weakened rather than re-aimed is a
regression in coverage, and the diff will say which it was for every
one that changes.

**Decoders get a differential harness, like the compiler has.** A
decoder is exactly the shape `bootstrap/` already tests: two
implementations, one corpus, byte-identical output demanded. `jpeg.f`
against libjpeg over a corpus of JPEGs, `mp3.f` against mpg123, each
comparing decoded samples rather than trusting that both look right.
That harness is how the port stays honest while it is partial, and it
is the same machinery `difftest.py` already provides.

## What this does not promise

Four of seven dependencies remain, and the two largest — SQLite and
mbedTLS — are not being attempted. A program with no `img`, `aud` or
`table` in it already links nothing but libc; what this plan improves
is the program that draws something, which today needs three packages
installed and afterwards needs one.
