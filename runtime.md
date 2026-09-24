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

## Phase 3 needs an oracle it does not have yet

Phases 1 and 2 could be checked against the thing they replace: zlib
is importable from the test harness, and libjpeg is reachable through
`img` plus a canvas save. Neither trick works for MP3.

`aud` exposes `.play()`, `.stop()`, `.isPlaying()` and `.save()`, and
`.save()` round-trips the original bytes — so a Festina program cannot
read decoded samples back, and there is no way to ask mpg123 what it
produced. This container also has no `mpg123`, `ffmpeg`, `sox` or
`lame` binary, and Python's standard library decodes WAV but not MP3.

The fix is already this repository's own practice rather than a new
dependency: `tests/test_cycle_buffer.py` compiles and runs a small C
probe against the runtime. A probe that calls **libmpg123** — which is
already required to build any audio-using program — and prints the
samples it decodes is the same shape, and gives phase 3 exactly the
oracle phases 1 and 2 had.

Worth writing down because the alternative is a decoder verified
against a plausible-looking waveform, which for a codec means very
little: an MP3 decoder with a wrong scalefactor table still produces
sound.

## Handing pixels back: what is actually true

This section said twice that phases 1 and 2 "decode correctly and have
nowhere to put the result", and that `img` is constructible from a
path and a database column "and from nothing else". **Both were
wrong**, and the correction matters because it changes what the next
piece of work is for.

`blankImage(w, h)` builds an image with no path
([specification.md](specification.md) §17.3), `fillStyle(r, g, b)`
takes runtime integers, and `img.drawPixel(x, y)` writes one pixel. So
a Festina decoder can produce an `img` today, pixel by pixel, with no
new builtin at all — checked, not reasoned about: a four-by-two image
built that way reads back the colours it was given.

And it is not slow in the way the second guess assumed. Measured:
**90,000 pixels in 5ms**, about 18 million pixels a second. A
1920x1080 photo hands off in roughly 115ms.

So a bulk entry point is a **performance primitive, not an enabler**.
115ms is real next to the ~2ms a buffer copy would cost, and it is
paid on every image a program loads — worth removing, and worth
removing for that reason rather than for a blocker that does not
exist. The phases that follow are not gated on it.

What is still true: `img.save()` round-trips the original bytes, so
PNG *encode* remains gated on the `blob` write half, exactly as the
top of this document says.

## Plan

Each phase ends green, with the dependency it removes actually gone
from `setup.md`'s table rather than merely unused.

| phase | delivers | removes | needs |
|---|---|---|---|
| 0 ✅ | conditional linking of Festina runtime sources | — | compiler change |
| 1 ✅ | `inflate.f`, PNG decode | — | phase 0 |
| 2 ✅ | `jpeg.f` | **libjpeg** | phase 0 |
| 3 | `mp3.f` | **mpg123** | phase 0 |
| 4 | `raster.f` — paths, AA fill, stroke, clip, gradients, compositing | — | phase 0 |
| 5 | glyph rasterisation + the font-discovery decision | **Cairo** | phase 4 |
| 6 | PNG encode | — | the `blob` write half |

Phases 1–3 are independent of 4–5 and can land in any order. Phase 6
is gated on a language feature that does not exist yet.

**Phase 2's ✅ is for the decoder, not yet for the removal.** libjpeg
is still in `setup.md` and still on the link line, because the
fallback needs it: `jpeg.f` refuses progressive, arithmetic and 12-bit
files, and those load through the C path. Dropping the dependency
means either covering progressive JPEG or deciding to refuse it
outright — a user-visible choice, not a port detail — and neither has
been made. The same will be true of Cairo at phase 5.

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

Neither was wired to `img` when this was written; "Wiring, as built"
below is that step, and it is the one that made the bootstrap
differential care.

### Phase 2, as built

`runtime/festina/jpeg.f` is baseline JPEG (ITU T.81): marker parse,
canonical Huffman, dequantisation, a separable inverse DCT over a
cosine table, triangular chroma upsampling, YCbCr to RGB. Progressive
(SOF2), arithmetic coding and 12-bit are refused with `JPG_ERR` set,
the same stance `png.f` takes.

**Measured against libjpeg itself**, which a Festina program can reach
without any new machinery: `img photo = 'x.jpg'` decodes through
libjpeg, drawing it and saving gives a real PNG, and the test harness
reads that back. Over the 768 samples of `tests/fixtures/gradient.jpg`
(4:2:0, the common case): **max deviation 1, 499 exact, mean 0.35**.

The residual is one decision and not an accumulation. libjpeg's
triangular upsampling rounds in integers, `(3a+b+1)>>2`; this rounds a
float bilinear result. Everything upstream — Huffman, dequantisation,
IDCT, the luma path — agrees exactly, which is visible in the image's
first pixel matching bit for bit.

That bound is load-bearing rather than decorative. The first version
used nearest-neighbour upsampling and scored a max deviation of 5, with
the first pixel still exact — which is what identified the resampling
as the only thing wrong. A test asserting `<= 1` fails if the
resampling regresses and fails far harder if anything before it does.

### Wiring, as built

`img photo = 'x.jpg'` now compiles to two calls:

```llvm
%d = call ptr @festinaDecodeImage(ptr %path)
%i = call ptr @festina_load_image_via(ptr %d, ptr %path)
```

`festinaDecodeImage` is `runtime/festina/imageload.f` — it sniffs the
signature, hands PNG to `png.f` and JPEG to `jpeg.f`, and returns null
for anything either decoder refuses. `festina_load_image_via` is three
lines of C: return what it was given, or fall through to the old C
loader. Every format the port does not cover loads exactly as it did
before, which is what makes a partial port safe to ship.

**A linked object, not injected source.** The first version merged
`imageload.f` into the user's program through `RUNTIME_TRIGGERS`. It
worked and it was wrong: eighty statements the programmer did not
write, in their program, changing their IR — so `bootstrap/` would have
had to replicate the injection or go red on every corpus file
mentioning `img`. It did, on 18 of 137. Compiled to an object instead,
the user's IR gains a declare and a call, and the bootstrap needs only
the matching declare.

**The object is a program minus its entry point, and the minus is
subtle.** `_strip_component_entry` drops `main`; dropping
`__festina_main` with it is the obvious next move and it segfaults.
That function holds the component's top-level statements, and for a
decoder those are the tables — `JPG_ZIGZAG = [0, 1, 8, ...]` is stores
inside it. Without them the object links, runs, reads element 0 of an
empty array and dies in `jpgBlock`. It is renamed
`__festina_component_init_<name>` and registered in
`@llvm.global_ctors`, so it runs before `main` without the user's
program emitting anything to call it.

**Only the path-shaped loads, so far.** `festinaDecodeImage` takes a
path and reads the file itself, which covers `img x = 'a.png'` and
`loadImage(...)`. An image arriving as BYTES — a `blob` column out of
a table, `req.toImg()` — still goes through the C decoder, because
those reach it via `festina_decode_image_bytes`'s function-pointer
hook and never touch a path at all. Routing them means a second entry
point taking an `arr[int]`, and the C side handing bytes to a Festina
function rather than the other way round. Not done; not pretended.

**Linked only when used**, on `gen.uses_image_load` — set where the
call is emitted, so it means an image load and not merely graphics. A
program that fills a rectangle and saves a canvas sets
`uses_graphics_code` and has no reason to carry a JPEG decoder.

The first version of that trigger searched the finished IR for `call
ptr @festinaDecodeImage(`, which is wrong in a way worth recording:
`bootstrap/codegen.f` is a compiler that *emits* that call, so its
source spells it, so its own IR carries it as a string constant. The
compiler linked a decoder into itself and then failed to link at all,
on the graphics symbols the decoder needs. Generated text cannot tell
an instruction from a literal.

**A decoded image still remembers its file.** `festina_load_image`
keeps the bytes it read so `save()`/`saveCopy()` reproduce the file
rather than re-encoding it (claude.md #110). The Festina decoder hands
back pixels and knows nothing about the file, so
`festina_load_image_via` attaches the path and bytes on the decoded
path too — the file is read there, though not decoded. Without that a
saved JPEG came back a PNG, which four tests in `test_codegen.py` said
out loud.

**Three link paths need the object**, and nothing makes them agree:
the libLLVM one, the `.ll`-to-clang fallback that macOS CI runs, and
`scripts/leak_stress.sh`, which builds and ASan-instruments every
runtime unit itself. The script asks `cli.component_ir(name)` for the
IR rather than reimplementing the entry-point rewrite in `sed`.

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
