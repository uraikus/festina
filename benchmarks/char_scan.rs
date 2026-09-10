// See char_scan.f's own comment: a lexer-shaped workload -- walk a
// source-sized buffer character by character, counting identifier runs.
// Indexes the raw bytes (str is UTF-8, and char_indices would measure
// decoding rather than scanning), matching every other implementation.
fn main() {
    let unit = "int func compute(a:int, b:int) { return a + b * 2 } ";
    let mut src = String::from(unit);
    for _ in 0..15 {
        let copy = src.clone();
        src.push_str(&copy);
    }
    let bytes = src.as_bytes();
    let mut total: u64 = 0;
    for _ in 0..5 {
        let mut tokens: u64 = 0;
        let mut in_word = false;
        for &c in bytes {
            let is_alpha = (97..=122).contains(&c) || (65..=90).contains(&c);
            if is_alpha {
                if !in_word {
                    tokens += 1;
                }
                in_word = true;
            } else {
                in_word = false;
            }
        }
        total += tokens;
    }
    println!("{}", total);
}
