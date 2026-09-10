// See char_scan.f's own comment: a lexer-shaped workload -- walk a
// source-sized buffer character by character, counting identifier runs.
const unit = "int func compute(a:int, b:int) { return a + b * 2 } ";
let src = unit;
for (let d = 0; d < 15; d++) src = src + src;

let total = 0;
for (let pass = 0; pass < 5; pass++) {
  const n = src.length;
  let tokens = 0;
  let inWord = 0;
  for (let i = 0; i < n; i++) {
    const c = src.charCodeAt(i);
    const isAlpha = (c >= 97 && c <= 122) || (c >= 65 && c <= 90);
    if (isAlpha) {
      if (!inWord) tokens++;
      inWord = 1;
    } else {
      inWord = 0;
    }
  }
  total += tokens;
}
console.log(total);
