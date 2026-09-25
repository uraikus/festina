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

### Phase 4, specified

Phases 1–3 are decoders: bytes in, pixels out, once per load. A
rasteriser is a different animal — it draws *onto* a surface the C
side owns, repeatedly, interleaved with C-side operations, possibly
every frame. Three things had to be settled before writing any of it,
and two of them turned out to be already answered in this repository.

**What Cairo is actually used for.** Measured, not guessed —
`festina_runtime_graphics.c` names 70 distinct Cairo entry points.
Phase 4's share:

| group | entry points |
|---|---|
| paths | `move_to`, `line_to`, `curve_to`, `close_path`, `rectangle`, `arc`, `new_path` |
| painting | `fill`, `fill_preserve`, `stroke`, `paint`, `paint_with_alpha`, `mask_surface` |
| source | `set_source_rgba`, `set_source_surface`, `set_source` |
| patterns | `pattern_create_linear`, `pattern_create_radial`, `add_color_stop_rgb`, `set_filter` |
| transform | `matrix_init_identity`, `translate`, `scale`, `rotate`, `set_matrix` |
| state | `save`, `restore`, `clip`, `set_line_width`, `set_operator`, `set_antialias` |

Text (`select_font_face`, `show_text`, `text_extents`) is phase 5.
PNG I/O is phases 1 and 6. The Xlib surface is the windowing seam and
is not in scope at all.

**The architecture is already precedented.** claude.md #240 added a
direct-pixel fast path for opaque flat rectangles, pixels and circles:
it writes ARGB32 words straight into the Cairo surface, gated on the
style state being solid and the transform being a whole-pixel offset,
and falls through to Cairo when the contract does not hold. That is
exactly the shape phase 4 wants, widened — *Festina computes pixels, C
owns the surface, Cairo stays as the fallback for whatever the port
has not reached yet.* It is also the same stance `png.f` and `jpeg.f`
already take: refuse rather than half-do, and let the caller fall
through.

**The oracle is already built, too.** `FESTINA_NO_DIRECT_FILL=1`
renders the same scene through the fast path and through Cairo and
demands byte-identical PNGs (`TestSolidFillFastPath`, with `_png_diff`
in `test_codegen.py`). A `FESTINA_*` switch selecting the Festina
rasteriser reuses that harness whole.

**But the oracle is two-tier, and saying so now is the point.**
Byte-identity is available for flat, axis-aligned, opaque fills, and
#240 already demonstrates it. It is NOT available for antialiased
edges: Cairo and pixman make particular sampling choices, and a
different rasteriser making different ones is not a bug. Those get
properties that hold of any correct rasteriser — interior fully
covered, exterior untouched, edge strictly between, coverage monotonic
along the normal — plus a measured bound against Cairo, the way
`jpeg.f` carries "max deviation 1 across 768 samples". A bound that is
measured and asserted is a test; a bound chosen to make today's output
pass is not, and the difference will be visible in the diff.

**What it costs, measured.** The worry was that a rasteriser in
Festina would be too slow to be the real backend, and the figure that
suggested it — 18M px/s — was wrong for the question: that is
`blankImage` plus a `drawPixel` builtin call per pixel, not a pixel
loop. An actual loop over an `arr[int]`, compiled `-O2`:

| inner loop | throughput | a full 800×600 canvas |
|---|---|---|
| flat fill | 1.92 G px/s | 0.25 ms |
| src-over blend, 8-bit coverage, per channel | 96 M px/s | 5.0 ms |

5 ms is the cost of blending *every pixel on the canvas* with
coverage. Real scenes blend along edges — thousands of pixels — and
fill flat spans inside, so a frame's rasterising is well under a
millisecond against a 16.6 ms budget at 60fps. (Benchmarks written so
the optimiser cannot collapse them: the fill value varies per
repetition, and the blend reads what the previous pass wrote. The
first version of the flat measurement reported 2.4 G px/s from twenty
identical passes, which is what collapsing looks like.)

**What is still missing: the handoff.** `imageFromPixels` (#346)
builds an image *from* a buffer. A rasteriser also needs to read the
surface it is drawing onto — a bulk counterpart, not `getPixelColor`
one pixel at a time. That is phase 4's equivalent of #346 and its
first piece of compiler work.

**Slices, in dependency order.** Each ends green and is useful alone:

1. ✅ the bulk pixel read, and a `raster.f` that can fill one
   axis-aligned opaque rectangle — the narrowest slice that exercises
   the whole handoff, and one where byte-identity against Cairo is
   available
2. ✅ edge list, scanline fill, nonzero and even-odd winding, with
   sub-scanline coverage — the core; everything below is expressed
   in it
3. ✅ `curve_to` by flattening, `arc` by the same, `rectangle` and the
   existing circle cache expressed as paths
4. ✅ stroking: joins, caps, line width, reduced to a fill of the
   stroke outline
5. ✅ clipping as a coverage mask intersected with the span coverage
6. ✅ linear and radial gradients as a per-span source
7. ✅ `save`/`restore`, the transform stack, and the operators actually
   reachable from the language

Phase 5 (glyphs) needs 1–5 and nothing after.

**Slice 1, as built.** `img.toPixels()` is the read half of the
handoff and `runtime/festina/raster.f` holds `rasNewSurface` and
`rasFillRect`. A scene of four opaque rectangles — including two that
run off opposite edges — is rendered by Cairo's `drawRect` and by
`raster.f` through `imageFromPixels`, and the two PNGs are compared
byte for byte. They match.

The comparison harness is itself checked against two images differing
in one pixel, because a byte-identity assertion that cannot fail
proves nothing.

`raster.f` also joined the differential corpus the moment it existed —
the corpus is auto-discovered, so a new `.f` file under `runtime/
festina/` is one — and all five harnesses (lexer, parser, semantic,
escape, codegen) agree on it between the two compiler
implementations.

One decision recorded where it will start to matter: the buffer format
is STRAIGHT alpha, because that is what the handoff already speaks.
Premultiplied is what compositing arithmetic actually wants, and
converting at the edges will cost two multiplies per pixel touched
once slice 2 introduces blending. The alternative — a rasteriser whose
buffers cannot reach `imageFromPixels` without a conversion pass — is
a second in-memory format for everyone to get wrong. Slice 1 does no
blending, so the cost today is zero.

**Slice 2, as built.** `rasFillPath` takes a path as two flat arrays
— `pts` of x,y pairs and `ends` holding one index per subpath — fills
it by the nonzero or even-odd rule, and composites with src-over.
Coverage is EXACT in x and sampled at `RAS_SUB` positions in y.

**The bound against Cairo is 12, and the obvious explanation for it
was wrong.** A triangle filled both ways over an opaque background
differs on 331 of 480,000 pixels, maximum 12, mean 0.0028. The natural
reading is that `RAS_SUB`'s 1/16 quantisation in y is the residual —
so that was checked, by varying `RAS_SUB` over 8, 16, 32 and 64. The
maximum moved by one unit: 13, 12, 12, 12. Sampling density is not
what separates the two rasterisers, so sixteen is kept because
thirty-two buys nothing, and the difference lives in how coverage is
computed rather than how finely it is sampled.

**The measurement itself had to be fixed first.** Comparing the two on
a TRANSPARENT surface reported a maximum deviation of 255. That was
the measurement's fault: a pixel with alpha 1/255 un-premultiplies to
a saturated colour, so comparing RGB while ignoring alpha compares
noise. Over an opaque background every pixel is opaque and the numbers
are what a viewer would see. A bound taken from the first version
would have been meaningless and would have looked alarming.

Pixel-aligned geometry is still asserted exactly, including against
slice 1's own rectangle fill — two independent code paths that must
agree on a box — and a left edge at x = 1.5 leaves exactly 128, which
is the assertion that would catch an off-by-half in the span
arithmetic.

**Slice 3, as built.** `rasCubicTo`, `rasArc` and `rasCircle` turn
curves into polygons for slice 2 to fill. Segment counts are DERIVED
from error bounds rather than tuned, so 0.1 px — Cairo's own default
tolerance — is a guarantee, and it is tested as one against the true
curve sampled densely: 0.057 px worst case for a test cubic, 0.0957
for an arc, whose bound is tight because the sagitta *is* the error.

**Filled circles had a bias, and fixing it took two tries.** An
inscribed polygon lies wholly inside its circle, so every filled
circle came out small. Against Cairo that looked like a max deviation
of 38 with a large one-sided sum; against the TRUE circle it measured
+12.12 green units light on average.

The first fix balanced the extreme deviations — vertices outside by as
much as chord midpoints are inside, r' = 2r / (1 + cos(π/n)). It
helped and was wrong: +3.07 still. Expanding both candidate radii to
second order in h = π/n shows why — extremes-balanced is r(1 + h²/4),
area-balanced r(1 + h²/3), and the h²/12 between them predicts a
3.7-unit deficit. Coverage is an area, so the AREA has to balance:
r' = r·√(2π / (n·sin(2π/n))). That measures +0.06.

Only a whole circle gets it. A partial arc's end points must lie on the
true radius because the path's next segment starts there, so `rasArc`
stays inscribed.

**Against the truth rather than against Cairo:**

| vs the true circle | max \|err\| | mean \|err\| | mean signed |
|---|---|---|---|
| Cairo | 22.3 | 3.35 | +1.63 |
| raster.f | 13.5 | 4.45 | +0.06 |

The earlier "max 24 against Cairo" was two approximations of the same
circle disagreeing — neither is the reference. raster.f has the
smaller worst case and less bias; its mean absolute error is higher
than Cairo's and that is not yet explained. It is recorded, not bounded
away.

**Two things this slice nearly got wrong in its own tests.** The arc
end-point check first compared 37.5 against 37.49988 and blamed the
arc — the harness prints floats to six significant figures, so the
property is now checked inside the program at full precision. And the
bias test's failure message claimed an inscribed polygon measured
"about +6", written before measuring; it is +12.12.

**And one it nearly got wrong in the corpus.** `Math.PI` is a property
READ, which `bootstrap/codegen.f` has not ported, and using it moved
`raster.f` from compared to "not ported yet" in three differential
tests — silently, as a skip. So pi is spelled out, to sixteen
significant figures: the bootstrap converts float literals only inside
the exact fast path (all digits below 2⁵³), a twenty-digit pi is
outside it, and these sixteen digits round to precisely the doubles
`Math.PI` and 2·`Math.PI` hold. All five harnesses compare the file
again.

**Slice 4, as built.** A stroke is the FILL of its outline:
`rasStrokeOutline` turns each segment into a quad and each join into a
polygon on the outside of the turn, and `rasStrokePath` fills the set
once with the nonzero rule. Once, as a union, is the point — an
overlap is inside once, so a half-transparent stroke that crosses
itself reads the same at the crossing as anywhere else (127 over
white, where painting the pieces one by one would give about 64).

Styles are Cairo's defaults, because the runtime has only ever set a
line width: miter joins, miter limit 10, butt caps. Against Cairo's
own `strokePath` the maximum deviation is 6 on a polyline with a
closed square, and 14 on two spikes either side of the miter limit —
6.5°, which must bevel, and 16.3°, which must keep a seventy-pixel
miter. Zero pixels differ by more than 60 on either, which is asserted
separately from the maximum because it is the join test: a wrong
miter-or-bevel decision leaves a whole wedge wrong by around 225.

**One test claimed more than it checked.** The overlap test's
docstring said it would catch pieces wound inconsistently. Putting
that bug back — removing `rasEmitPoly`'s orientation fix — left it
passing. The reason is geometric: segment quads are consistently
wound by construction, and a join fills the wedge *outside* its two
quads, so it never overlaps them. The fix matters only when some other
part of the path crosses a join, so there is now a test that runs a
segment straight through a miter corner: 127 with the fix, 255 — a
hole, the crossing cancelled under nonzero — without it. Both stroke
bugs were put back and both are caught.

**Slice 5, as built.** A clip is a mask — one float per pixel, the
fraction the clip lets through — built by the same `rasRowCoverage`
every fill uses, and a clipped draw multiplies its coverage by it.
Masks compose: two clips intersect by multiplying, a soft clip edge
needs nothing special, and strokes are clipped for free because they
are fills.

The row loop moved out of `rasFillPath` into `rasRowCoverage` so the
same coverage can be blended, stored or multiplied without three
copies of the scanline code. The refactor was checked byte-for-byte,
not just by the tests still passing — several of those use bounds,
and "within the bound" is not "unchanged". A scene with a translucent
triangle, an even-odd circle and a translucent stroke renders to an
identical PNG before and after.

**There is no Cairo to compare against here.** The language exposes
no path clip; the runtime's only `cairo_clip` is internal to
`drawImageRegion`. So the oracles are exact: a rectangular clip, two
clips intersecting, and a cross-check — a full-surface fill through a
soft circular clip must be byte-identical to a plain fill of that
circle, because a shape with coverage 1 everywhere makes the product
equal to the clip. That comparison includes the soft edges, and the
test asserts that it does.

**A product is not an intersection, and that is the definition.**
Where shape and clip both have soft edges in one pixel, multiplying
coverages does not give the area of their geometric overlap. The test
that pins this now puts them on OPPOSITE halves of a pixel: they do
not overlap at all, and the product still draws a quarter. Cairo's
clip does the same — it is the conflation every mask compositor has —
so it is asserted, to stop a later "fix" diverging from what it
replaces. The first version of that test put the edges on
perpendicular sides, where the halves genuinely overlap in a quarter,
and so could not tell the two apart whatever its name claimed.

`rasClipIntersect` visits every row, not just the new path's: a row
the new path cannot reach is outside it and must close. The obvious
optimisation leaves those rows as open as before; put back, it fails
the test that names it.

**Slice 6, as built.** A fill's colour is now a SOURCE — a small
float array describing a solid colour, a linear gradient or a radial
one — and each covered pixel's colour is computed at its centre. These
are exactly the gradients the language can ask for: two opaque stops
at 0 and 1, PAD-extended because the runtime never sets an extend
mode. The per-pixel blend moved into one `rasBlendPixel` that solid
and gradient rows both call, so the arithmetic cannot drift between
them; solid fills through the new path were checked byte-identical,
and the extra call costs nothing measurable.

Against Cairo, each gradient fills a pixel-aligned rectangle, so
coverage is 1 everywhere and only COLOUR is compared: a horizontal
linear gradient is byte-identical including both pad regions, a
diagonal one and a radial one are within 1.

**The degenerate cases were measured, and my guess was wrong for both
— differently.** Coincident end points, or a radius of zero, give a
gradient no direction. I assumed PAD would continue the end colour.
Cairo draws the AVERAGE of the stops for a degenerate linear gradient
— (128, 0, 128) from red and blue — and draws NOTHING AT ALL for a
zero-radius radial one. They are two rules now, stated as
measurements, and both are byte-identical to Cairo.

**And the comparison found a real bug in the C runtime.** Under
`fillAlpha`, `festina_set_fill_source` called `cairo_paint_with_alpha`
for a gradient — and `paint()` ignores the path. So a gradient drawn
with `fillAlpha(0.5)` washed a translucent gradient over the ENTIRE
canvas, and the shape itself was then filled at full opacity: opaque
red inside a 50% rectangle, a blue tint four hundred pixels away.
api.md documents `fillAlpha` as applying to every fill. The fix scales
the gradient's own stops by the alpha, which makes the source
translucent for every caller — fill, preserved fill and text — and a
regression test in `test_codegen.py` fails against the old code.

One bound was written before it was measured: the soft-edge gradient
test asserted 24, copied from slice 3's circle. It measured 27 — the
same coverage error, since slice 3 drew red over white (a 225-unit
channel) and this rim is blue over white (255): 24/225 × 255 = 27.2.

**Slice 7, as built.** A transform is Cairo's own six-float matrix,
and `rasTranslate`, `rasScale` and `rasRotate` compose the way
`cairo_matrix_translate` and friends do — the new operation applies
first, M' = M·T. Paths are given in user space and mapped to device
space just before filling, which is exact for an affine map. Two
things cannot be mapped afterwards and are decided with the matrix in
hand:

- a circle's SEGMENT COUNT comes from its radius times the matrix's
  largest stretch (its larger singular value), so a radius-2 circle
  scaled by 25 stays within 0.1 px of the true circle instead of
  being a visible polygon. The area-balanced radius from slice 3
  survives the map, because an affine map scales every area by the
  same factor;
- a stroke's outline is built in user space, with the user-space
  width, and then mapped — the pen lives in user space, so
  `scale(3, 1)` makes a width-4 line twelve pixels wide where it runs
  vertically and four where it runs horizontally.

A zero scale is ignored, as `festina_scale` ignores it. The matrix is
the only state these functions share — colour, source and width are
already arguments to every call — so it is the only thing with a
stack here; the runtime keeps the rest.

Against Cairo, with no pixel off by more than 60 in any of them:

| scene | max deviation |
|---|---|
| a rotated rectangle | 13 |
| translate, then rotate | 12 |
| rotate, then translate | 13 |
| a circle under scale(2, 0.5) | 21 |
| a stroke under scale(3, 1) | 14 |

**The first composition test was blind.** It drew
translate-then-rotate and rotate-then-translate on one canvas. With
every operation composed backwards (T·M), it still came in at 12 and
13, while a single sequence drawn alone had 15,036 pixels off by more
than 60 (maximum 225). The algebra says why: composed backwards, "A
then B" yields B·A, so each order's program draws the OTHER order's
picture, and a canvas holding both is unchanged. Each order is now its
own test, and each fails alone with its own bug put back: a backwards
`rasTranslate` fails rotate-then-translate, a backwards `rasRotate`
fails translate-then-rotate. Neither is caught by the other test,
which is why there are two. The segment-count bug (the count taken
from the user-space radius) fails both the faceting test and the
ellipse.

**Clearing is the one other operator the language reaches.**
`clearRect`, `clearCircle` and `clearPixel` are Cairo's SOURCE with a
transparent source; every other SOURCE in the runtime copies a whole
surface or starts a new one, and no program can aim it at a path.
With coverage c it leaves dst·(1 − c), which in straight alpha scales
the ALPHA and leaves the colour alone — a half-cleared (40, 120, 200)
pixel is (40, 120, 200, 128), not a darker colour. Fully cleared is
written (0, 0, 0, 0), the one transparent `blankImage` makes. Clearing
a pixel-aligned rectangle is exact; clearing a shape is the complement
of filling it to within 1 at every pixel, soft edges included.

Against the true circle, clearing does better than Cairo's clear does:

| clearing a circle, vs the truth | max \|err\| | mean \|err\| | mean signed |
|---|---|---|---|
| Cairo, r = 35 | 18.8 | 4.94 | +4.81 |
| raster.f, r = 35 | 13.1 | 5.04 | +0.14 |
| Cairo, r = 50 | 29.9 | 7.65 | +7.49 |
| raster.f, r = 50 | 15.1 | 5.29 | +0.22 |

Cairo's clear is biased, and more so at the larger radius; the cause
on Cairo's side was not investigated. So the test compares against
the truth and bounds the bias at 1, not against Cairo: a
Cairo-bounded test would have to allow its 7.5-unit bias and could
not catch this implementation acquiring one.

**And the port found a second bug in the C runtime.** The canvas
state saved by `saveState()` held the fill colour but not the
gradient, and `festina_set_fill_source` reads the gradient first. So
a gradient set between `saveState()` and `restoreState()` outlived the
restore: `fillStyle(green)`, save, a red-to-blue gradient, restore,
`drawRect` drew red-to-blue — (241, 0, 14) at the left, (11, 0, 244)
at the right — instead of green. The state now holds a reference to
the saved gradient and hands it back on restore; the regression test
checks both directions (a gradient must not survive a restore to a
colour, and a saved gradient must come back after `fillStyle`
destroyed the live one) and fails against the old code. decisions.md
#350.

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
