# Bundled font

`DejaVuSans.ttf` is the default face for text in compiled programs:
`sans-serif`, regular, drawn and measured by `runtime/festina/font.f`
and `text.f` instead of by Cairo and whatever font the machine's
fontconfig picks (runtime.md, phase 5; decisions.md #352).

It is linked only into programs that draw or measure text, the way
every other runtime piece is linked only where it is used.

## Provenance

| | |
|---|---|
| font | DejaVu Sans Book, version 2.37 |
| taken from | Ubuntu 24.04's `fonts-dejavu-core` 2.37-8, `/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf` — the same file CI's Linux job installs |
| size | 759,720 bytes |
| sha256 | `ae7b7855e115a5966d8b1b3f80f254ccc117ec86f9965e202ee2940453837280` |
| unmodified | yes — byte for byte the packaged file |

`LICENSE-DejaVu` is upstream's licence file verbatim
(`dejavu-fonts/dejavu-fonts`, `LICENSE`): the Bitstream Vera licence,
with DejaVu's own changes in the public domain and glyphs imported
from the Arev fonts under Tavmjong Bah's licence. Both are permissive
and require their notices to travel with the fonts, which is what this
file is for. Neither allows the font to be sold on its own; bundled
inside a program is expressly allowed.

The file must stay unmodified. Both licences require a modified font
to be renamed, and font.f's tests compare against FreeType reading
this exact file.
