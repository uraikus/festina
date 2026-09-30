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

`cairo_select_font_face(cr, g_font_family, …)` — one call site now,
in `festina_apply_font`, with `"sans-serif"` the default family —
hides the entire question of *which file is sans-serif on this
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
| 5 | glyph rasterisation + the font-discovery decision | — | phase 4 |
| 6 | PNG encode | — | the `blob` write half |
| 7 | wiring 4 and 5 in, image sources, the window seam — nine slices, five decisions; see "Phase 7, specified" | **Cairo** | phases 4, 5, 6 |

Phases 1–3 are independent of 4–5 and can land in any order. Phase 6
is gated on a language feature that does not exist yet.

**Phase 2's ✅ is for the decoder, not yet for the removal.** libjpeg
is still in `setup.md` and still on the link line, because the
fallback needs it: `jpeg.f` refuses progressive, arithmetic and 12-bit
files, and those load through the C path. Dropping the dependency
means either covering progressive JPEG or deciding to refuse it
outright — a user-visible choice, not a port detail — and neither has
been made. The same will be true of Cairo, at phase 7 — see phase 5's
spec for why not 5.

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
translucent for both callers — fill, and the preserved fill before a
border (not text, which never reads the gradient; see phase 5) — and a
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

### Phase 5, specified

Phase 4 drew shapes. Phase 5 draws text, and what text is today had to
be measured before anything could be promised about it, because most
of it turned out not to be what the API reads as.

**What the language asks for.** `drawText(text, x, y)` and its
`img.drawText` form, `measureTextWidth` and `measureTextHeight`, and a
`font` of four parts: a family name, a size in px, `bold`, and
`italic`/`oblique`. Behind them, four Cairo calls: `select_font_face`
(once, in `festina_apply_font`), `set_font_size`, `show_text` (two call
sites, canvas and image) and `text_extents` (the two measures). That is
Cairo's "toy" text API, and the toy API decides a lot on the program's
behalf.

**What it actually does, measured** — on Linux (Ubuntu 24.04, Cairo
1.18.0, the default font packages; CI's Linux runner installs the same
`fonts-dejavu-core`):

| question | answer |
|---|---|
| which file is `sans-serif` | DejaVu Sans Book, via fontconfig — TrueType (`glyf`) outlines, 2048 units per em. `serif` and `monospace` are DejaVu too; `fc-match` gives an unknown family DejaVu Sans |
| advances | whole pixels. Hint metrics are ON, so `"iiiiiiiiii"` is exactly 10 × 4 at 13 px, and 13.5 px measures the same as 13 |
| kerning | none. DejaVu has 2,727 kerning pairs (AV is −131/2048 em, To −348/2048, −2.7 px at 16 px), and `"AV"` measures exactly A + V |
| ligatures | none. `"fi"` and `"ffl"` draw byte-identical to their pieces |
| anti-aliasing | SUBPIXEL. Ubuntu's `fontconfig-config` enables `10-sub-pixel-rgb.conf` by default, so 1,044 of 1,072 inked pixels of black text are coloured, channel spread up to 86 |
| hinting | `hintslight`, from the same default configuration |
| `bold` | a real bold face: DejaVuSans-Bold.ttf |
| `italic` | **nothing.** `fonts-dejavu-core` ships no oblique, fontconfig reports a 0.2 shear for synthesising one, and Cairo's toy face does not apply it: `font f = 'italic 40px'` draws byte-identical to upright text (0 of 72,000 channels differ; `bold` changes 3,114). api.md says `italic` sets the slant. No test looks at italic pixels — the three that mention it check IR |
| a gradient | ignored. Both text call sites set the flat colour, so text drawn under a red-to-blue gradient came out 4,137 pixels of the earlier `fillStyle` green, none red or blue. api.md lists `drawText` among the fills a gradient replaces |

Glyph origins are whole pixels unless a transform moves them, because
`drawText` takes `int` coordinates and every advance is rounded. Under
`scale` or `rotate` Cairo does position glyphs at fractions of a pixel
(moving the origin in 0.25 px steps moved the ink by 0.19–0.32 px).

**macOS and Windows are not measured**, and should not be guessed at.
Cairo chooses a font backend per build — Quartz, DirectWrite or GDI,
FreeType — so the same five words may reach CoreText on one platform
and fontconfig on another. Run 171's `fc-match sans-serif` answered
`verdana.ttf` on the Windows runner, but that is what fontconfig would
pick, not proof of what Cairo's toy face uses there.

**What that means.** Text is already the least portable thing the
runtime draws: which face, whether it is hinted, whether it has colour
fringes, and whether `italic` does anything all depend on the
machine's font configuration, and an image a program saves carries
those choices in its pixels. A Festina text path cannot be "identical
to Cairo" when Cairo is not identical to itself. What it can be is
identical to *a stated configuration*, and the spec has to choose one.

**The proposal:**

- **Greyscale anti-aliasing, not subpixel.** The destination is an
  image that may be saved, scaled, rotated or read back with
  `toPixels`; subpixel fringes are only correct on the physical LCD
  they were computed for, and wrong everywhere else.
- **Unhinted outlines, with rounded advances.** Keeping hint metrics
  keeps every `measureTextWidth` result and every line of existing
  layout the same. Dropping outline hinting changes how glyphs look:
  against today's output, an unhinted greyscale rendering differs by a
  mean of 24.4 / 17.3 / 20.0 grey levels over inked pixels at 12 / 16 /
  32 px, max 209 / 116 / 193 — mostly stems moving to fractional
  positions. Hinting alone accounts for most of it (mean 23.2 / 14.4 /
  17.3 between hinted and unhinted greyscale), subpixel for the rest
  (mean 12.2 / 10.2 / 8.1). Reproducing `hintslight` means reproducing
  FreeType's light hinting mode, and native TrueType hinting is a
  bytecode interpreter for the fonts' own instruction programs;
  neither is in scope. **This is a visible change and it is the user's call.**
  (Decided: unhinted — decisions.md #352.)
- **No kerning, no ligatures**, to match. A later phase can add
  kerning from `kern`/GPOS as a deliberate change; it would change
  `measureTextWidth`, so it is not a port detail either.
- **The open behaviours stay open until decided:** italic that does
  nothing, and text that ignores gradients. Both are api.md promises
  the runtime does not keep. Phase 5 can keep them, but that changes
  output, so each needs a yes first; until then the port reproduces
  what the runtime does.

**What gets parsed.** TrueType `glyf` outlines, which is what DejaVu,
Liberation, Arial and Verdana are: the table directory, `head`, `maxp`,
`hhea`, `hmtx`, `cmap` formats 4 and 12, `loca` and `glyf`, simple and
composite glyphs. Quadratic segments flatten with slice 3's derived
tolerance (0.1 px) and fill through raster.f with the nonzero rule,
which is the rule TrueType outlines are drawn to. Refused, falling
through to Cairo the way `jpeg.f` refuses progressive files: CFF and
CFF2 outlines (`OTTO`), collections (`ttcf`) until a slice needs one,
bitmap-only fonts, and a font with no Unicode `cmap`. A code point with
no glyph draws glyph 0, as the `cmap` says.

**The oracles, and which are exact.**

- *Outlines* are integers in font units, and FreeType's
  `FT_Outline` for the same glyph with hinting off reports the same
  integers. Parsing is compared exactly, every glyph of a file.
- *Metrics* are integers after rounding: `measureTextWidth` and
  `measureTextHeight` through font.f must equal the runtime's for the
  same face, exactly, over a corpus of strings.
- *Rasterised glyphs* are not exact. The reference is Cairo with its
  options set explicitly — `ANTIALIAS_GRAY`, `HINT_STYLE_NONE`,
  `HINT_METRICS_ON` — and the face loaded from the same FILE through
  `cairo_ft_font_face_create_for_ft_face`, so the comparison is outline
  rasterisation alone, independent of whatever fontconfig says on the
  machine. A measured bound, as slices 2–7 carry, and the true-coverage
  check slice 3 used for circles.

The oracle program links `cairo-ft` and FreeType, which CI's Linux job
already has through `libcairo2-dev`. That is a test-time use of
libraries the runtime already links, not a new dependency.

**Cairo is not removed by phase 5,** and the plan table saying so was
wrong. Counting what the runtime calls Cairo for once text and phase
4's shapes are both Festina's:

| still Cairo | why | covered by |
|---|---|---|
| every draw call | raster.f is built and compared, not wired in: no draw call reaches it yet | phase 4's switch, not yet built |
| `set_source_surface` + `paint`/`mask_surface`, `set_filter(GOOD)` | `drawImage`, `resize`, images under a transform — resampling | nothing yet |
| `create_from_png_stream` | the fallback for PNGs `png.f` refuses | phase 1's refusals |
| `write_to_png`, `write_to_png_stream` | saving | phase 6, gated on `blob` writes |
| `xlib_surface_create`, `xlib_surface_set_size` | presenting to an X11 window | a platform seam — `XPutImage` would do; Win32 already reads raw pixels |

So phase 5 delivers glyphs and the font decision, and removes nothing.
Retiring Cairo is its own step, after phase 4's wiring, image sources
and 6, and it is listed as one in the plan table.

**Font discovery, with the new evidence.** Three options now, not two:

- **Bundle a font.** One checked-in TrueType file — DejaVu Sans is
  759,720 bytes, under the permissive Bitstream Vera licence with
  DejaVu's own changes in the public domain — always
  available, identical output on every platform, and the only option
  under which the glyph oracle can run on macOS and Windows CI, where
  DejaVu is not installed. Costs repository size and the user's system
  fonts. It adds a file the project did not have, so under this
  project's rule on dependencies it needs an explicit yes.
- **A thin discovery shim in C** — "give me a path for this family" on
  each platform. Honours system fonts. Keeps fontconfig on Linux, and
  keeps output machine-dependent in exactly the ways measured above.
- **A directory scan in Festina.** Read the `name` table of every file
  under the platform's font directories (`/usr/share/fonts`,
  `C:\Windows\Fonts`, `/System/Library/Fonts`) and match the family
  there. No C and no fontconfig, and it honours installed fonts, but
  it does not honour a user's fontconfig aliases, and it costs a scan
  (59 fontconfig entries on this machine; hundreds on a desktop) once per process.

My recommendation is to bundle one face as the default and scan for
any family a program names explicitly: the default is what almost
every program uses, and it is the part that most needs to be the same
everywhere. That is a recommendation, not a decision; the choice is
the user's, and the bundle needs permission. (Decided: DejaVu Sans is
bundled — decisions.md #352. The directory scan for named families was
not asked for and is not built: other families stay with Cairo.)

**Slices, in dependency order.** Each ends green and is useful alone:

0. measure macOS and Windows: what Cairo's toy `sans-serif` resolves
   to, its anti-aliasing, hint metrics and kerning there — a CI probe
   step, the way the Windows crash was measured, removed once read
1. ✅ `runtime/festina/font.f`: the table directory and the tables above,
   simple glyphs to outlines in font units, compared exactly against
   FreeType for every glyph of DejaVu Sans; the refusals, each tested
2. ✅ composite glyphs — offsets, scaled components; point-matched
   components refused until a font needs them
3. ✅ glyph outlines through raster.f: quadratic flattening, scale and
   y-flip, fractional origins, compared against the explicit-option
   Cairo oracle with a measured bound
4. ✅ layout: UTF-8 decoding, rounded advances, glyph 0 for the missing;
   `measureTextWidth` reproduced exactly, and `measureTextHeight`
   exactly against the unhinted reference — not against today's runtime,
   whose heights depend on hinting (see slice 4, as built)
5. ✅ the font decision implemented, whichever it is — DejaVu Sans,
   bundled
6. ✅ wiring: `drawText`, `img.drawText` and both measures reach font.f
   behind a switch, the way phase 4's wiring will, with refused fonts
   falling through to Cairo

Slices 1–4 need no decision and can start now. Slice 5 needs the font
decision, and slice 6 needs the greyscale/unhinted change agreed,
since that is when users would see it.

**Slice 1, as built.** `font.f` reads a font in place, from the file's
own bytes: `fntOpen` finds and range-checks the tables, `fntGlyphIndex`
maps a code point through the cmap, `fntAdvance`/`fntLsb` read hmtx,
and `fntGlyph` decodes a simple glyph into points, on-curve flags and
contour ends in font units. Every glyph of the four fonts
fonts-dejavu-core installs matches FreeType's unscaled, unhinted
outline point for point — 11,363 simple glyphs, with every advance —
and the format-12 cmap matches over all of U+0000–U+10FFFF, 5,918
mappings in DejaVu Sans, supplementary planes included. The format-4
table matches too. The whole of DejaVu Sans — every glyph and 1.1
million cmap lookups — parses in 0.15 s.

**Three glyphs disagreed, by one unit, and FreeType was right.**
FreeType places a glyph so its left edge sits `lsb` (from hmtx) right
of the origin, which is what the glyph header's `xMin` normally says
as well. In DejaVu Sans the two disagree for six glyphs, by one unit
each; three are simple, and those were exactly the three that
differed. `fntGlyph` now moves the outline by `lsb − xMin`, as
FreeType does. Checked across the whole font before it was believed:
every other glyph has `lsb = xMin`.

**The fonts CI has do not reach every path, so the test builds one
that does.** All four are long-loca and all four have a format-12
cmap. (DejaVu Sans does exercise hmtx's shared-advance tail — 6,238
entries for 6,253 glyphs — and putting that bug back fails its test.)
So the test assembles a real TrueType file
from DejaVu's own glyphs — short loca, format 4 only, a monospaced tail
past `numberOfHMetrics` — and FreeType reads it too, so it is compared
exactly like the rest. Its digits share one format-4 segment mapped
through the glyph array, with holes and a nonzero `idDelta`, because
one of the eleven bugs put back to check these tests survived: a 0
read from the glyph array must mean "missing" without the delta being
added, and neither DejaVu's format-4 table nor the first version of
the built font ever took that path. It fails now.

**Refusals are codes, and damage is not absence.** CFF (`OTTO`),
collections, a file that is not a font, a missing table and a font
with no Unicode cmap each return their own `FNT_ERR`. A table that is
listed but runs past the end of the file says `E_SHORT`, not
`E_TABLE`: the first version reported a file cut off in the middle of
`glyf` as missing its `glyf`, which is the wrong thing to tell the
caller. Every multi-byte read is range-checked, so a truncated file —
tested at seven lengths from 0 bytes to the middle of `glyf` — is
refused rather than read past, and a single glyph whose `loca` entry
points outside `glyf` fails alone while the glyphs around it still
read. `font.f` is in the differential corpus and all five harnesses
compare it.

**Slice 2, as built.** Composites are decoded in place: each component
is another glyph, itself possibly composite, transformed by its matrix
and moved by its offset. The four CI fonts hold 8,120 composites,
nested up to four deep with up to eight components, and every one
matches FreeType's composed outline point for point — so all 19,483
glyphs of those fonts now do. They matched on the first run, which is
exactly when the checking matters, because those fonts use none of
the harder paths: no scale, no 2×2 matrix, no point matching.

So the test builds a font that does, the way slice 1 built one for
short loca. A single scale, separate x and y scales, a 2×2 matrix, a
scaled composite nested inside another, and a composite whose own lsb
is five units right of its xMin, all compared exactly with FreeType.
Exactly, because transforms here go through FreeType's own 16.16
arithmetic (`FT_MulFix`, rounding halves away from zero), so a scaled
point lands on the same integer in both. A naive multiply that shifts
without handling the sign disagrees only on negative exact halves;
put back, it fails.

**The lsb adjustment belongs to the glyph asked for, not to its
components.** The built font's `l` is three units off its xMin, and
placed inside a composite it is placed by the component offset alone —
FreeType's behaviour, established by the comparison, and pinned by a
test that also checks the rounding. Applying the adjustment at every
level instead fails five tests, including DejaVu's own.

Point-matched components, and offsets the flags say to scale (which
FreeType scales by a vector length computed its own way), are refused
with `FNT_E_COMPONENT` rather than written untested: no font here uses
either. A composite that contains itself stops at a depth of 16 with
`FNT_E_GLYPH`; without the guard it crashes the program. Ten bugs were
put back across the decoder and every one fails a test.

**Slice 3, as built.** `fntGlyphPath` turns a glyph into a raster.f
path at a size and an origin: TrueType's quadratic B-splines walked the
way FreeType's `FT_Outline_Decompose` walks them — implied on-curve
midpoints between off-curve runs, and a contour that starts on an
off-curve point started on its last point or halfway — then flattened
by a derived segment count and filled with the nonzero rule. font.f
produces paths and does not import raster.f; whoever draws decides how.

Against Cairo with the spec's options and the same font file, drawing
24 glyphs (composites included) one at a time:

| size | 12 | 16 | 32 | 64 | 128 |
|---|---|---|---|---|---|
| worst pixel | 12 | 16 | 20 | 18 | 20 |
| mean over ink | 2.58 | 2.39 | 1.03 | 0.86 | 0.30 |

with no pixel off by more than 60 at any size, which is what a
misplaced edge or a missing contour would produce.

**The tolerance is not raster.f's, and it was measured, not copied.**
At raster.f's 0.1 px a chord can sit a tenth of a pixel inside a curve
— up to 25 grey levels on an edge pixel, always light. Against the
true outline:

| tolerance | worst (16 / 32 px) | mean over ink | bias | per glyph |
|---|---|---|---|---|
| 0.1 | 22.9 / 27.9 | 4.58 / 3.18 | +0.81 | 44 µs |
| 0.03 | 8.0 / 8.5 | 1.87 / 1.29 | +0.19 | 54 µs |
| 0.01 | 8.0 / 8.5 | 0.95 / 0.61 | +0.09 | 70 µs |

The worst case stops improving at 0.03, and that was checked rather
than assumed: 8 is raster.f's own sampling floor — with 64 sub-rows
instead of 16 it falls to 2.3 at 0.01 — and 0.03 px is at most 7.7
levels, just under it. Tighter lowers only the mean, for 30% more
time, so glyphs flatten at 0.03.

**Against the truth, raster.f is closer than Cairo.** Cairo's own
greyscale glyphs measure 19.7 / 23.2 worst and 3.39 / 1.87 mean from
the true outline at 16 / 32 px; raster.f's 8.0 / 8.5 and 1.87 / 1.29.
That is why raster.f's distance from Cairo *grew* slightly as its
tolerance tightened: what is left between them is mostly Cairo's
error. The reference itself was checked first — its first reading
disagreed with both renderers more than they disagreed with each
other, which looked like a bug in the reference; 16, 64 and 256
sub-rows agree to 0.3, so it was not. At fractional origins the
numbers hold: worst 8.4, mean 1.84 / 1.25, bias +0.17.

The segment count is a guarantee, tested as one: six quadratics from
nearly flat to a 400 px sweep, each chord within 0.03 px of the curve
sampled densely, and some chord at least half that, so the count is
not just generously high. A contour rotated to start in each of the
three cases draws the same pixels.

Seven bugs were put back; six fail. The seventh — starting on the last
point without consuming it — only adds a zero-length closing edge,
the same curves and one duplicate point, so no picture can see it and
it is not a bug. The consuming form is kept because it is FreeType's.

**Slice 4, as built.** `fntLayout` maps a text's code points to glyphs
— one to one, no kerning, no ligatures — and places each at a whole-
pixel pen position; `fntTextWidth` and `fntTextHeight` are the two
measures. Code points come from the language (`split('')`), which
already decodes UTF-8, so a four-byte character is one glyph; a
character the font lacks is glyph 0 with glyph 0's advance.

The arithmetic is FreeType's and Cairo's integers, reproduced: the
size becomes a 16.16 scale through `FT_DivFix`, each advance is scaled
into 26.6 with `FT_MulFix` and rounded half up to a pixel, and the
inked height is the union of each glyph's control box widened
outwards to the pixel grid. Checked first in Python across 111 strings
× 67 sizes, then in font.f:

| | width | height |
|---|---|---|
| vs the runtime's `measureTextWidth`, today | exact | — |
| vs Cairo, unhinted, same file | exact | exact |
| vs the runtime's `measureTextHeight`, today (hinted) | exact | differs in 3,484 of 7,437, from −2 to +3 px — mostly +1, unhinted being taller |

Glyph ids and pen positions match Cairo's own `text_to_glyphs` too.

**Today's heights are not one thing** (decisions.md #351). The
runtime's text is hinted or not depending on the first size the
process uses — below 7.5 px Ubuntu's fontconfig turns hinting off for
DejaVu Sans, and Cairo's toy API keeps that decision for every later
size. So "matches measureTextHeight" has no fixed meaning to aim at,
and the test that tried to record the difference was itself fooled by
it: its first size was 6. Width is unaffected — hinting never moves an
advance in these fonts, exact both ways.

**Two things DejaVu cannot show, so the built font does.** DejaVu's em
is 2048 units, a power of two, so `FT_DivFix` never rounds and dropping
its rounding term changed nothing. And real glyphs put an on-curve
point at every extreme, so "control box" and "on-curve points only"
give the same heights. The built font gets a 1000-unit em, and a glyph
whose top is an off-curve point at twice the curve's height, with an
advance of 2291 — found by searching every advance for one whose pixel
width `FT_DivFix`'s rounding changes at a tested size. Cairo on that
file settles both: the height is the control box's (60 px, not 30, at
40 px), and the rounding is there. Six bugs were put back; both of
those survived until the built font existed, and all six fail now.

**Slices 5 and 6, as built** (decisions.md #352 has the decisions and
what the wiring found). `runtime/fonts/DejaVuSans.ttf` is the default
face, unmodified, with its licence beside it. `runtime/festina/text.f`
answers three questions for the C side — how wide, how tall, and which
pixels a line covers — and regular `sans-serif` text at a whole-pixel
size under no scale or rotation goes through it; bold, italic, other
families and scaled or rotated text go to Cairo as before, byte for
byte, which a test checks for each.

text.f's answer is a coverage mask: each glyph rasterised in a box its
own size and added into the line's mask, saturating, which is how
Cairo accumulates a glyph run. C composites the mask with the source it
always set — so colour, fillAlpha, and text's indifference to
gradients are the C side's and unchanged — and the result is exact:
black text on a transparent image leaves alpha equal to the mask, byte
for byte, at 9, 16 and 31 px. The canvas and an `img` draw identical
text.

**It is 12 times slower than Cairo per line, and that was not noticed
until after CI was dispatched.** 74 us per 25-character line against
5.9 us (693 us before a glyph cache and `amor` arrays — decisions.md
#352 has the numbers and the profile). 224 lines fit a 60 fps frame;
Cairo's 2,814. Every glyph of a size is rasterised once and blitted at
whole-pixel pens after that, checked against an uncached rasterisation.

The font reaches a program as generated C — the bytes and a table of
text.f's entry points, registered from a constructor — linked only
where text is drawn or measured (781,008 bytes). Components export
only their entry points now; text.f is serialised behind a spinlock,
without which two workers drawing text crashed every time;
FESTINA_CAIRO_TEXT=1 is the switch back to Cairo; the packaged compiler
carries the whole runtime directory; and text is under the leak
harness for the first time.

### Phase 7, specified

Phases 4 and 5 built the rasteriser and the text path; phase 7 is where
Cairo actually leaves. "Wiring 4 and 5 in" was how the plan table put
it, and measuring what that means turned up four things the table did
not know. The findings come first because they set the shape of the
plan.

**What Cairo still does, measured.** 96 of the 109 functions in
`festina_runtime_graphics.c` that mention Cairo, by what would have to
replace it (the other 13 only name a type):

| what | functions | replacement |
|---|---|---|
| draw fallbacks: a context, then fill / stroke / paint | 36 | raster.f, once it is fast enough |
| surface storage and access only | 17 | a plain buffer — mechanical |
| transform state (`cairo_matrix_t`) | 10 | raster.f's matrix, phase 4 slice 7 |
| images as sources (`set_source_surface`, `mask_surface`) | 10 | **nothing built** — blits and a resampler |
| gradients / patterns | 6 | raster.f, phase 4 slice 6 |
| text through the toy API | 5 | text.f for the default face; **nothing for the rest** |
| path builders (`move_to` … `close_path`) | 4 | path arrays into raster.f |
| X11 present and resize | 4 | `XPutImage` — six lines of Cairo today |
| PNG decode / encode | 3 | png.f decodes; **encode needs the `blob` write half** |
| image resample (`set_filter(GOOD)`) | 1 | **nothing built** |

Every canvas and every `img` is a `cairo_surface_t`; the Win32 and
macOS backends read only its data pointer, width, height and stride.

**Finding 1: raster.f is not yet fast enough to be the backend.** Phase
4 said a frame's rasterising would be "well under a millisecond". That
was true of the inner blend loop and untrue of the path it sits in.
Per shape, drawn 4,000 times onto an 800×600 surface, fills only (an
earlier run compared raster.f's fill against Cairo's fill *and border*
and flattered raster.f):

| per shape | the runtime today | raster.f | ratio |
|---|---|---|---|
| opaque rectangle 120×80 | 2.1 us (direct-pixel path, #240) | 21.6 us | 10× |
| opaque circle r = 50 | 11.7 us (cached coverage mask, #104) | 606.6 us | 52× |
| 50% rectangle | 6.0 us (Cairo) | 17.2 us | 2.9× |
| 50% circle | 63.3 us (Cairo) | 696.2 us | 11× |

The C fast paths from #104 and #240 are the bar, not Cairo: they are
what a game loop runs on, and text.f began 77 times slower than Cairo
for want of exactly this measurement. A circle costs 7.7 million
instructions — 41% in `rasRowCoverage`, then `rasAddSpan`,
`rasBlendRowSource` and array reallocation — about 980 per pixel.
There is real headroom (interior rows need no edge walk; sixteen
sub-scanlines are more than most rows want), and it is a work item of
its own, with a gate: no drawing call slower than the path it replaces.

**Finding 2: pixels cannot travel as `arr[int]`.** `toPixels` plus
`imageFromPixels` on 800×600 costs 6.9 ms, and raster.f's surface is an
`int` per channel — 32 bytes a pixel against the surface's 4. So
raster.f cannot draw onto a copy of the canvas per call, and it is
eight times the memory traffic on its own arrays. Whatever draws must
write the surface's own bytes.

**Finding 3: the direct paths already do not need Cairo.** #104 and
#240 write premultiplied ARGB32 words straight into the surface's
memory; Cairo is only what allocates it and what the fallback calls.
And a plain buffer can be handed to Cairo without a copy —
`cairo_image_surface_create_for_data`, measured here: the pixels
written through the wrapper are in the original buffer (50% red reads
`0x80800000` in it), the pointer is shared, and a wrapper plus context
costs 258 ns per call (133 ns on one kept wrapper). That is what makes
removal incremental.

**Finding 4: removing the fallback removes behaviour, not just
pixels.** Today "not handled" means "Cairo does it". With Cairo gone:

- **bold, italic and other families have no path.** text.f draws the
  bundled regular face. Bold needs DejaVuSans-Bold (709 KB); italic
  needs an oblique face `fonts-dejavu-core` does not ship (today's
  italic draws upright anyway — runtime.md phase 5); another family
  needs discovery (the directory scan option, which font.f can now
  read TrueType from) or an error;
- **png.f refuses 1, 2, 4 and 16-bit files and jpeg.f refuses
  progressive ones** — the plan noted this for libjpeg at phase 2:
  implement, or refuse outright, and either way it is a user-visible
  choice;
- **saving has no encoder.** `img.save`, `saveCanvas` and the bytes a
  `file:img` column stores go through `cairo_surface_write_to_png`.
  Encoding PNG in Festina needs to produce bytes, and there is still no
  way to build a `blob` from an `arr[int]` — phase 6's gate, unchanged.

**And the pixel-exactness cost.** Cairo and raster.f differ at
anti-aliased edges by a measured 12–27 grey levels, so a saved image's
bytes change at every curved or slanted edge. 111 tests read or compare
pixels, 71 of them in `test_codegen.py`; most probe interiors, and how
many pin an edge is a slice's measurement, not a guess made here.

**The architecture proposed.** Three ways to give raster.f the surface:

| | how | cost |
|---|---|---|
| A | **Festina computes coverage, C composites rows.** The surface is a plain C buffer; raster.f, given the `img`, hands each row's coverage to one C primitive that blends it into the surface's own bytes | one new internal `img` method; no copies; per-pixel work stays in C |
| B | port the coverage engine to C | fastest; gives up the point of writing the runtime in Festina |
| C | a pixel-buffer type in the language | the general answer, and a compiler change on the scale of `amor` |

A, because it is what text.f already does — Festina answers coverage, C
composites with the source it always set — and because compositing is
the part that wants 4-byte words and no bounds ceremony. B is the
fallback if A cannot reach the bar in Finding 1. C is worth having and
is not needed for this.

**Decisions this needs from the user** (none is a port detail):

1. **Architecture:** A (recommended), B, or C.
2. **Non-default text:** bundle a bold face and refuse or substitute
   italic and other families; scan the system fonts for named families;
   or keep Cairo as an optional dependency for exactly this. The last
   is the only one under which Cairo is not removed.
3. **Formats:** implement 1/2/4/16-bit PNG and progressive JPEG, or
   refuse them with an error that says so.
4. **Pixels:** accept that anti-aliased edges move by the measured
   bound, and that tests pinning an edge are re-baselined rather than
   loosened.
5. **The bar:** "no drawing call slower than the path it replaces",
   measured per shape as in Finding 1 — or a looser one.

**Decisions, answered** (the user, 2026-09-30):

1. **Architecture: A.** Festina computes coverage, C composites rows.
2. **Non-default text: scan the system fonts for named families, and fall
   back when one is unavailable.** Slice 8 is therefore font discovery on
   three platforms plus a rule for what "unavailable" draws (the bundled
   face, and bold or italic asked of a family that lacks them, are
   slice 8's to specify and to put in front of the user before it is
   built). Cairo is removed.
3. **Formats: implement.** 1-, 2-, 4- and 16-bit PNG and progressive
   JPEG, in slice 6.
4. **Pixels: accept** the measured edge movement; tests that pin an edge
   are re-baselined against the new value with the reason written next to
   it, never loosened.
5. **The bar: no fixed number; examine every way to make it faster.** The
   proposed bar stays the yardstick each slice measures against, but what
   is asked for is the work: find the speed-ups (the profile and the
   arithmetic, not the tolerance) and land the ones that hold, with the
   measurement beside each.

**Slices, in dependency order.** Each ends green, and with Cairo still
present every slice compares against it, byte for byte where possible:

1. ✅ `FestinaSurface`: a plain buffer for the canvas and every `img`, with
   a zero-copy Cairo wrapper for the fallbacks that remain. **No
   behaviour change, and no decision needed:** the oracle is the whole
   suite, unchanged, plus byte-identical saved images
2. ✅ the row-compositing primitive and raster.f driving an `img` directly
   (decision 1); fills and paths, compared with Cairo
3. ◐ coverage speed to the bar (decision 5) — measured per shape; first pass done, see "Slice 3, first pass"
4. fills, strokes, paths, gradients, clip and transforms off Cairo: the
   36 fallback functions lose their contexts, one group at a time
5. images as sources: integer-offset blits (exact), scaled and rotated
   draws, `resize`, `clip`, `clone` — a resampler, compared with a bound
6. ◐ the decoder gaps (decision 3): PNG done (see "Slice 6, PNG"); progressive JPEG next
7. presentation: `XPutImage`, and the window seam speaking a plain
   surface (the Win32 and macOS backends already do)
8. non-default text (decision 2)
9. PNG encode (phase 6, gated on building a `blob` from bytes), then
   delete Cairo from the link line, `setup.md`, CI's package lists and
   the `FESTINA_CAIRO_TEXT` switch

**Slice 1, as built.** Every ARGB32 and RGB24 surface the runtime makes --
the canvas, `img.new`, `clone`, `resize`, `fromPixels`, the decoders' and
the text mask's destinations, twelve sites -- now comes from
`festina_surface_create` in `festina_runtime_graphics.c`, which
`calloc`s the pixels itself and hands them to
`cairo_image_surface_create_for_data`. The buffer belongs to the surface
through a user-data key whose destroy callback frees it, so it lives
until the last Cairo reference goes, exactly as Cairo's own storage did.
The stride is Cairo's (`cairo_format_stride_for_width`), the memory is
zeroed as before, and a size Cairo refuses (negative, or over its
32,767 limit) is still refused by Cairo: the seam falls back to
`cairo_image_surface_create`, which is where that verdict comes from.
Nothing else changed -- every fallback still draws through Cairo, on
memory that is now plain C, which is what slices 2 onward need.

Measured: the eight-image scene renders byte for byte the same as the
tree before the change; wrapping a surface and making a context is
258 ns; the leak harness is clean, and with the free callback removed it
reports 233,656,320 bytes in 1,080 objects (media_churn) and 7,500,800 in
1,600 (image_layer_churn), so it does see the buffers.
`tests/test_surface_seam.py` compiles the seam alone against real Cairo
with counting allocators and puts each bug back: a stray direct create
(caught by the source check), a callback that frees nothing (allocations
and frees disagree), an allocation that does not zero (pixels not zero).

Not routed, deliberately: the A8 coverage mask text draws through (it is
a transient, freed in the same call) and the surfaces the PNG stream
reader creates -- both go when their callers do, in slices 4 and 9.

**Slice 2, as built.** One new internal method,
`img.__blendRow(row, x0, x1, coverage, r, g, b, alpha)`, is the whole of
what raster.f needs from C: it blends a row of coverage (an `arr[float]`,
0..1) into the surface's own bytes, clipped to the image and to the
array, so nothing is copied and no Cairo call is made
(`festina_image_blend_row`). It is named with underscores because it is
the runtime's seam, not language surface, and is deliberately absent from
api.md. raster.f gains `rasFillPathImg`, `rasStrokePathImg`, their
`...Clip` and `...TImg` (transform) forms, and `rasFillRectImg` /
`rasFillCircleImg`, all over the coverage functions the `arr[int]` target
uses, so a shape has the same coverage on either.

Three things the tests found rather than the design predicted:

- **Alpha is a float, not a byte.** Cairo premultiplies a solid colour
  from the double `fillAlpha` holds -- alpha and each channel times alpha,
  each to a 16-bit short, then its high byte. Premultiplying from the
  rounded 8-bit alpha (the first version) was up to two grey levels off,
  over 135 pixels of a three-layer scene, at alphas like 0.4 and 0.7. The
  primitive and the `...Img` functions take the double; the result is
  byte-identical to Cairo wherever coverage is whole.
- **Drawing has to drop the image's cached file bytes** (claude.md #101),
  as every other drawing call does. The first version did not, and a JPEG
  drawn on with raster.f was saved as the original file; the JPEG test
  caught it, and `test_drawing_on_a_loaded_image_stops_it_saving_the_files_own_bytes`
  holds it.
- **Two mutations were invisible to output tests**: a row or span outside
  the image, and a coverage array shorter than the span, draw nothing
  anyone sees when they write past the buffer. `tests/test_blend_row.py`
  cuts the real function out of the runtime, compiles it with
  AddressSanitizer and calls it with every argument past its edge.

Measured against Cairo (same scenes, saved PNGs compared): aligned
opaque and translucent rectangles, over opaque, transparent and
JPEG-backed (RGB24) grounds, are byte-identical; a half-transparent
circle differs only on its rim (every differing pixel within 1.5 px of
the edge, at most 27 levels); and the two raster.f targets agree to one
grey level across fills, strokes, clips and transforms. The
differential harnesses pass with the method ported to
`bootstrap/codegen.f` (its declare line and its `CG_IMAGE_OPS` entry --
claude.md #346's lesson, applied before the failure this time).

**Speed, measured before slice 3 sets a bar** (800x600 image, per shape,
single thread, idle machine; the current runtime is its direct
fill path or Cairo):

| shape | current runtime | raster.f -> img |
|---|---|---|
| opaque rect 40x30 | 0.2 us | 69 us |
| opaque circle r=20 | 2.7 us | 146 us |
| 50% rect 40x30 | 1.3 us | 82 us |
| 50% circle r=20 | 29.8 us | 149 us |

The circle is four times better than the `arr[int]` target's 606 us
(per-pixel blends into 32-byte pixels were most of that) and a rect is
worse than the 21.6 us of Finding 1, because every row's coverage is
cleared, accumulated and blended across the WHOLE 800-pixel width for a
40-pixel shape. Bounding each row to the shape's own span is slice 3's
first job, and the numbers above are its baseline.

**Slice 3, first pass.** The bar (decision 5) has not been answered, so
this pass takes only speed-ups that change nothing visible: the pixels of every
shape are the same. Each was found by profiling
(callgrind, per-row instruction counts) before it was written:

- **A row's work is limited to the columns the shape touches.** Every
  row cleared, accumulated and blended the whole surface width; a 40-pixel
  shape on 800 columns did 20 times the work. `RAS_LO`/`RAS_HI` track the
  columns a row's spans reached; the clear, the clip multiply, the blend
  and `__blendRow` use only those. (Invariant: `RAS_COV` is zero outside
  them; a row puts back what it touched before it starts.)
- **A sub-scanline walks only the edges that reach its row.** A circle's
  sixty-odd edges were each tested by all sixteen sub-scanlines of every
  row; now a row picks its active edges once, flattened, in the path's
  order, so the crossings and their insertion order are unchanged.
- **The whole pixels inside a span are summed once.** Each sub-scanline
  added its weight to every interior pixel, sixteen times per pixel; a
  difference array records where an interior starts and ends and the row
  sums it once. The weights are powers of two, so those sums are exact.
  This is the one change that can move a coverage by the last bit of a
  double, where a pixel gets both partial and interior contributions and
  the additions are associated differently; a visible pixel can move only
  if a coverage lands within 1e-13 of a rounding boundary, and none does
  on the 260-shape battery below.
- In C: full coverage skips the multiplies (MUL_UN8 by 255 is the
  identity, checked for all 256 values), and `festina_over_un8x4` is
  written as pixman writes it, two channels to a lane. The new form is
  held equal to the channel-at-a-time one on 200 million random pairs and
  every combination of the edge values, and a test keeps that.

`tests/test_raster_golden.py` pins the result: 260 pseudo-random shapes
(both fill rules, circles, fractional rectangles, strokes, clipped and
transformed fills, gradients, clears) on an odd-sized surface with shapes
running off every edge, hashed on both targets, against the hashes the
code produced at the end of slice 2. Nine bugs put back in the speed-ups
(a too-strict edge filter, an interior never subtracted or started a
pixel late, the touched range one short at either end, stale coverage
left in the row, the difference array not cleared, the blend range one
short) all fail it; a tenth, an edge test loosened by 0.01, changes
nothing because no sub-scanline can sit that close to a row's top, and is
noted as an equivalent mutant rather than a hole.

Measured again as before (800x600, per shape, idle machine):

| shape | current runtime | slice 2 | now |
|---|---|---|---|
| opaque rect 40x30 | 0.3 us | 69 us | 24 us |
| opaque circle r=20 | 2.5 us | 146 us | 38 us |
| 50% rect 40x30 | 1.3 us | 82 us | 31 us |
| 50% circle r=20 | 32.7 us | 149 us | 42 us |

So a general shape is now within 1.3x of Cairo (the 50% circle), and
raster.f is still 20-80x the runtime's C fast paths on rectangles and
circles. Those fast paths (#104, #240) are not Cairo and are not going
away; what they cannot do is a translucent or fractional rectangle,
which Cairo does in 1.3 us and this does in 31. What is left, in the
order the profile says: a row whose active edges are all vertical (every
rectangle) computes its crossings once and replays them, saving about
half a row; the C side gaining a translucent/fractional rectangle path of
its own, which is decision 5's question and not this slice's to settle;
and the per-row edge collection, which rebuilds for every row what
changes only at an edge's end.

**Slice 3, second pass (decision 5: "examine ways to make it faster").**
What was tried, with what it measured, in the order it was done:

- **Rows every rectangle has: landed.** An inside row whose active edges
  are all vertical and cross the whole row meets all sixteen
  sub-scanlines at the same x, so it is computed once and its additions
  repeated (the two partial pixels one repetition at a time, so the same
  rounding; the whole pixels between them as sixteen times a power of
  two, which is exact). Rectangle rows went from 12.9k instructions to
  2.7k; the 40x30 rectangle from 24 us to 9-12 us. Held bit for bit by
  the golden scene (five bugs put back in it, all caught).
- **Opaque full coverage in C: landed.** `__blendRow` writes the pixel
  where the coverage is 1 and the colour opaque (blend cost 42M -> 23M
  instructions on the same rectangles).
- **Translucent flat colours through the direct-pixel path: landed, and
  the biggest.** The #240 fast path took only an opaque colour; a
  translucent circle went to Cairo at 28 us and a translucent rectangle
  at 1.3 us. The direct path now takes them (`festina_solid_pixel_premul`
  reduces the colour the way Cairo does, the cached circle coverage
  scales it, OVER does the rest). Translucent circle 27.9 -> 8.0 us;
  translucent 40x30 rectangle 1.35 (Cairo) -> 1.57 us, level, not
  faster: clang vectorises the OVER at about 12 instructions a pixel and
  the arithmetic is the limit. `tests/test_translucent_fast_path.py`
  draws seven alphas of stacked rectangles, circles and pixels on a
  canvas and an img with the fast path on and off, byte-identical; four
  bugs put back fail it. A probe of 20,000 random shapes against Cairo
  found the identity holds for every alpha below 0.996 with no
  exception.
- **Tried, no gain, reverted:** a precomputed slope per edge (removes a
  divide per crossing: 3% fewer instructions, no change in time), and a
  compositing loop with the source's half hoisted out (no change).

**A finding, not fixed.** The same probe shows that from alpha 0.9961
upward -- which is where Cairo calls a colour opaque, and so includes the
opaque #240 path that has been in since claude.md #240 -- about 1.3% of
partly covered pixels differ from Cairo by one grey level in one channel
when the pixel underneath is translucent. It is not the coverage (that is
Cairo's own) and not the two-step rounding I tried (single rounding, both
floor and round, are worse, wrong on most pixels). Cairo's source for its
opaque span path was not read. The existing tests do not see it because
their scenes lay opaque shapes on a few uniform colours. With Cairo
leaving, the edge pixels of those shapes change anyway (decision 4), so
this is noted rather than chased.

**Ideas left, and why.** *Runs instead of a dense coverage row*: pass
each row's interior as (start, end, value) runs and let C add them,
which removes the per-pixel diff pass and the zeroing loop; estimated 2x
on rectangles, about 10% on circles, and a new C signature to keep in
step; worth doing if rectangles through raster.f matter once slice 4 is
in. *Analytic area coverage* (accumulate signed area per cell instead of
sampling sixteen sub-scanlines): removes the 16x on every edge and is the
one thing that would speed circles several times over, but it is exact
only for shapes whose windings do not cancel, and a stroke's outline is a
union of overlapping quads under the non-zero rule, so it needs a
fallback for overlapping geometry and changes edge pixels (accepted, by
decision 4) -- its own slice, with its own tests. *SIMD by hand*: the
platform matrix (x86, ARM, MinGW) makes it a maintenance cost against a
gain that clang's own vectoriser already takes some of.

Per shape now (800x600; the direct path is what a drawing call runs):

| shape | direct path | Cairo | raster.f -> img |
|---|---|---|---|
| opaque rect 40x30 | 0.2 us | 0.8 us | 9-12 us |
| opaque circle r=20 | 3.0 us | 16 us | 35 us |
| 50% rect 40x30 | 1.6 us | 1.4 us | 16 us |
| 50% circle r=20 | 8.0 us | 28 us | 43-48 us |

**Slice 6, PNG.** png.f now decodes every PNG: all five colour types at
every depth the format allows for each (1, 2, 4, 8, 16 -- grey, RGB,
palette, grey+alpha, RGBA), Adam7 interlaced or not, and tRNS in all
three of its meanings (a palette alpha per entry; a colour key for grey;
a colour key for RGB, compared at the file's own depth). It replaces the
"refuse what is not 8-bit and non-interlaced" stance of phase 1, because
after slice 9 there is no libpng to hand a refused file to.

The oracle is written in Python from the spec and from what Cairo's
reader does where the spec leaves a choice, and that second half is
checked against Cairo itself through ctypes rather than assumed:
`tests/test_png_full.py` encodes every colour type x depth x
(plain, Adam7) x eight sizes (including 1x1, 1x5 and 5x1, where whole
Adam7 passes are empty), with rows filtered through all five filter
types and the IDAT split across chunks; Cairo reads every case it can
(everything but 16-bit) to exactly the expectation, and png.f matches the
expectation on all of them. Eleven bugs put back (Adam7 start and step,
low byte instead of high, sub-byte scaled by shift, key compared after
the 16-bit cut or on one sample fewer, filter distance for sub-byte
pixels, bit order, row rounding, an empty pass consumed, palette alpha
ignored) each fail it; one -- an RGB key ignoring blue -- passed until the
generator was made to produce pixels that miss the key by one in a
single sample, and that is in it now.

Where a choice was made: 1/2/4-bit grey scales to the full range (0,
85, 170, 255 for two bits); a 16-bit sample keeps its high byte
(`png_set_strip_16`); no gamma is applied whatever gAMA, sRGB or iCCP
say. **A finding the work turned up:** on cairo 1.17.2 and later the C
loader returns a *float* surface for a 16-bit PNG, which the runtime
reads as ARGB32, so a 16-bit file was already loading as garbage (a red
pixel came back as 518, 0, 0, alpha 63). png.f is the first correct
16-bit path.

Decode speed is what it was for 8-bit files and is not good: a
1024x1024 RGBA file takes 0.5 s (0.8 s interlaced), against tens of
milliseconds for libpng. It is the inflate loop and per-byte pushes, and
it is worth its own measurement before slice 9 makes it the only path.

**What removing Cairo buys, and what it does not.** `libcairo2-dev`
leaves `setup.md`'s graphics tier, along with MSYS2's cairo (and the
crash class of decisions.md #348) and Homebrew's; libjpeg stays unless
decision 3 says otherwise, libX11 stays (it is what opens a window).
It does not make drawing faster — Finding 1 says the reverse until
slice 3 — and it changes edge pixels.

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
