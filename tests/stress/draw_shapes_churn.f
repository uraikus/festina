// runtime.md phase 7, slice 4: every shape the graphics runtime now draws
// through draw.f, churned under the leak harness.
//
// What it exercises: the arrays C builds and Festina reads every call (the
// matrix, the fill source, a path's operations and coordinates) and C then
// releases; the coverage row and gradient words draw.f keeps in globals;
// shapes with borders, gradient fills, transforms, fillAlpha, point-sampled
// pixels, the clearing calls, and paths built and filled or stroked -- on
// an img and on the canvas.

img layer = blankImage(160, 120)
color red = '#c83232'
color blu = '#3232c8'
color yel = '#e0c020'
color ink = '#203040'
color none = 'none'
int n = 0
while n < 150 {
    fillStyle(red)
    borderColor(ink)
    lineWidth(1 + (n % 4))
    fillAlpha(0.4 + ((n % 5).toFloat() * 0.1))
    layer.drawRect(n % 40, 5, 50, 30)
    layer.drawCircle(60 + (n % 30), 60, 12 + (n % 9))
    layer.drawCircle(20, 90, 10, blu, ink)
    drawRect(n % 60, 10, 40, 20)
    drawCircle(80, 50 + (n % 20), 15)
    fillLinearGradient(0, 0, red, 100, 60, blu)
    layer.drawRect(10, 40, 80, 50)
    drawRect(10, 40, 80, 50)
    fillRadialGradient(80, 60, 30, yel, blu)
    layer.drawCircle(80, 60, 28)
    fillStyle(ink)
    layer.drawPixel(n % 100, 3)
    drawPixel(n % 90, 4, yel)
    layer.translate(3, 2)
    layer.rotate(7.0)
    layer.drawRect(30, 30, 20, 20)
    layer.clearRect(35, 35, 6, 6)
    layer.clearCircle(60, 60, 4)
    layer.clearPixel(40, 40)
    layer.resetTransform()
    translate(2, 1)
    rotate(5.0)
    clearRect(20, 20, 8, 8)
    clearCircle(50, 50, 5)
    resetTransform()
    beginPath()
    moveTo(10, 10)
    lineTo(90, 15)
    curveTo(110, 40, 90, 70, 40, 80)
    closePath()
    fillPath()
    beginPath()
    moveTo(5, 100)
    curveTo(30, 80, 70, 120, 100, 100)
    strokePath()
    layer.clear()
    n = n + 1
}
clearCanvas()
log(layer.width == 160)
