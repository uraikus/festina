// decisions.md #327: `&`, `|`, `^`, `~`, `<<`, `>>` and hexadecimal
// literals.
//
// Written at the same time as the operators themselves, because
// otherwise nothing in the corpus would use them at all -- and a
// mechanism no corpus file exercises is one the differential harness
// cannot see, however carefully both implementations were written.
//
// What this file is shaped to make visible:
//
//   - each of the three binary operators and the unary one, which are
//     single instructions with different spellings and nothing else to
//     distinguish them if one were emitted for another.
//   - `>>` being ARITHMETIC. `int` is signed, so a negative value
//     shifted right keeps its sign; `lshr` would answer an enormous
//     positive number and look perfectly reasonable in isolation.
//   - the two shift forms, which are genuinely different code: a
//     LITERAL count in range is one instruction, and a count that is
//     not costs the compare, the branch and the phi that §8.3's
//     out-of-range rule needs. Both appear below, adjacent.
//   - PRECEDENCE, in all three places it departs from the obvious:
//     the binary operators bind tighter than the comparisons (unlike
//     C), the shifts bind looser than `+` (unlike Go), and the three
//     binary ones nest `&` inside `^` inside `|`.
//   - a hexadecimal literal being base sixteen and not base ten, and
//     lexing as ONE token rather than a zero followed by a name.
//
// Every value is logged, so a wrong answer is a different program
// rather than a different-looking one.

int a = 12
int b = 10

log(a & b)
log(a | b)
log(a ^ b)
log(~a)

// Hex, including the case-insensitivity of the digits.
log(0xff)
log(0XAbCdEf)
log(0x0)

// A literal shift count: one instruction, no bounds check.
log(a << 4)
log(a >> 2)

// A non-literal count: the checked form, in range and out of it in
// both directions.
int count = 3
int wide = 64
int neg = 0 - 1
log(a << count)
log((a << wide) == null)
log((a >> wide) == null)
log((a << neg) == null)

// An arithmetic right shift keeps the sign a logical one would lose.
int negative = 0 - 16
log(negative >> 2)
log((0 - 1) >> 40)

// Precedence, each line grouping the way the table says and not the
// way C or Go would group it.
log(a & b == 8)
log(1 | 6 ^ 3 & 1)
log(1 << 4 + 1)
log(8 >> 2 + 1)

// The use case the operators exist for: a packed triple, written and
// read back.
int r = 0xde
int g = 0xad
int bl = 0xbe
int packed = (r << 16) | (g << 8) | bl
log(packed)
log((packed >> 16) & 0xff)
log((packed >> 8) & 0xff)
log(packed & 0xff)

// `&&`/`||` still lex and bind as themselves alongside the new
// single-character forms.
bool t = true
log(t && false)
log(t || false)
log(1 & 1 == 1 && 2 | 0 == 2)
