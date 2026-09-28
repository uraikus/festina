// runtime.md phase 5, slice 6: text drawn and measured through text.f.
//
// No stress file drew text before this one, deliberately: Cairo's text
// path reaches fontconfig, whose process-lifetime caches LeakSanitizer
// reports (see media_churn.f). The default face no longer goes there --
// text.f draws it from the bundled font -- so for the first time text
// can be churned under the leak harness. What it exercises: the mask
// array text.f hands to C and C releases, every call; the font array C
// builds once and marks immortal; measures; empty text; characters the
// font lacks and one four bytes long; and fillAlpha on the way through.

img layer = blankImage(200, 40)
color ink = 'black'
int total = 0
int i = 0
while i < 200 {
    changeFont(8 + (i % 24), null, null)
    fillStyle(ink)
    layer.drawText(`frame ${i}`, 2, 30)
    drawText('Canvas text', 4, 20)
    total = total + measureTextWidth(`w${i}`) + measureTextHeight('Hg')
    layer.drawText('', 0, 0)
    fillAlpha(0.5)
    layer.drawText('half é 😀 中', 2, 30)
    fillAlpha(1.0)
    i = i + 1
}
log(total > 0)
