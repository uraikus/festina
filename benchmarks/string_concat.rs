fn main() {
    let mut s = String::new();
    for _ in 0..15_000 {
        s = s + "x";
    }
    println!("{}", s);
}
