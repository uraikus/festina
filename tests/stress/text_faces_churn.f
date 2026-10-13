// runtime.md phase 7, slice 8: text in other families, bold and italic.
//
// What it exercises: the font directories walked once, lazily, and the
// face index they build; a found face's bytes read and built into the
// arr[int] text.f keeps (immortal, loaded once per face and never freed --
// reachable from a global, so not a leak); the face id switching under
// text.f's one open font; synthesized bold (a stroke outline added to the
// glyph's path, every glyph) and italic; families that are not found; a
// list; the generic names; and every one of them measured and drawn onto
// an image and the canvas, at many sizes.

img layer = blankImage(260, 50)
color ink = 'black'
int total = 0
int i = 0
while i < 120 {
    fillStyle(ink)
    int px = 9 + (i % 23)
    if i % 6 == 0 { changeFont(px, '', 'sans-serif') }
    if i % 6 == 1 { changeFont(px, 'bold', 'DejaVu Serif') }
    if i % 6 == 2 { changeFont(px, 'italic', 'Liberation Sans, DejaVu Sans, sans-serif') }
    if i % 6 == 3 { changeFont(px, 'bold italic', 'serif') }
    if i % 6 == 4 { changeFont(px, '', 'monospace') }
    if i % 6 == 5 { changeFont(px, 'bold', 'No Such Family') }
    layer.drawText(`face ${i} Rag 42`, 2, 36)
    drawText('Canvas text', 4, 20)
    total = total + measureTextWidth(`w${i}`) + measureTextHeight('Hg')
    fillAlpha(0.5)
    layer.drawText('half é 😀 中', 2, 36)
    fillAlpha(1.0)
    i = i + 1
}
log(total > 0)
