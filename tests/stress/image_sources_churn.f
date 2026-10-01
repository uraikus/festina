// runtime.md phase 7, slice 5: every way an image is a source -- copied,
// put down at a whole pixel, interpolated, reduced by a box, rotated -- and
// every one of the engine's scratch tables (the axis weights, the padded
// rows, the reduced-row cache), churned under the leak harness.
//
// It also runs the surfaces' own bounds under AddressSanitizer: the
// sources reach past the destination on every side and the destinations
// past the sources, at scales from a pixel to eight times.

arr[int] px = []
int k = 0
while k < 23 * 17 {
    px.push((k * 37) % 256)
    px.push((k * 91) % 256)
    px.push((k * 53) % 256)
    px.push(k % 4 == 0 ? 128 : 255)
    k = k + 1
}
img src = imageFromPixels(px, 23, 17)
img layer = blankImage(120, 90)
int n = 0
while n < 120 {
    drawImage(src, (n % 50) - 8, (n % 30) - 6)
    fillAlpha(0.6)
    drawImage(src, 790, 590)
    fillAlpha(1.0)
    drawImage(src, 5, 5, 30 + (n % 40), 20 + (n % 25))
    drawImage(src, 100, 5, 3 + (n % 11), 2 + (n % 7))
    drawImage(src, 200, 5, 1, 1)
    drawImage(src, -300, -300, 700, 500)
    drawImage(src, 2, 1, 9, 7, 50 + (n % 20), 50, 40, 31)
    drawImage(src, -5, -4, 40, 30, 120, 50, 12, 9)
    layer.drawImage(src, n % 90, n % 70)
    layer.drawImage(src, 3, 3, 40 + (n % 30), 35)
    layer.drawImage(layer, 2, 3)
    translate(200, 200)
    rotate(10.0 + (n % 70).toFloat())
    drawImage(src, 1, 1, 15, 11, 3, 2, 45, 33)
    drawImage(src, 0, 0, 23, 17, -30, -20, 90, 60)
    drawImage(src, 0, 0)
    drawImage(src, 0, 0, 60, 40)
    scale(0.4, 0.4)
    drawImage(src, 0, 0)
    resetTransform()
    scale(2.0, 0.5)
    drawImage(src, 10, 40)
    resetTransform()
    scale(0.5, 0.5)
    drawImage(src, 2, 2, 9, 7, 31, 33, 40, 30)
    resetTransform()
    layer.rotate(25.0)
    layer.drawImage(src, 20, 10)
    layer.resetTransform()
    img piece = src.clip(n % 10, n % 7, 14, 11)
    drawImage(piece, 300, 300)
    img wide = src.clip(-4, -3, 40, 30)
    wide.resize(5 + (n % 60), 4 + (n % 50))
    drawImage(wide, 400, 100)
    img snap = saveCanvas()
    wide.resize(300, 2)
    wide.resize(2, 300)
    n = n + 1
}
log(layer.width)
