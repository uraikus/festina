var WIDTH = 2000;
var HEIGHT = 1500;
var MAX_ITER = 1000;
var X_MIN = -2.0;
var X_MAX = 1.0;
var Y_MIN = -1.5;
var Y_MAX = 1.5;

function calculateMandelbrot() {
  var result = [];
  var xScale = (X_MAX - X_MIN) / WIDTH;
  var yScale = (Y_MAX - Y_MIN) / HEIGHT;

  for (var y = 0; y < HEIGHT; y++) {
    var y0 = Y_MIN + y * yScale;
    var rowOffset = y * WIDTH;

    for (var x = 0; x < WIDTH; x++) {
      var x0 = X_MIN + x * xScale;
      var zx = 0.0;
      var zy = 0.0;
      var iter = 0;

      while (zx * zx + zy * zy <= 4.0 && iter < MAX_ITER) {
        var zx2 = zx * zx;
        var zy2 = zy * zy;
        zy = 2.0 * zx * zy + y0;
        zx = zx2 - zy2 + x0;
        iter++;
      }
      result[rowOffset + x] = iter;
    }
  }
  return result;
}
var data = calculateMandelbrot();
console.log('Complete');
