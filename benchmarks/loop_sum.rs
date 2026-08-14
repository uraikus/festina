fn main() {
    let mut total: i64 = 0;
    let mut i: i64 = 0;
    while i < 100_000_000 {
        total = (total * 1_000_003 + i) % 1_000_000_007;
        i += 1;
    }
    println!("{}", total);
}
