fn main() {
    let mut total: i64 = 0;
    for i in 0i64..2_000_000 {
        let nums: Vec<i64> = vec![
            total % 97,
            (total + i) % 97,
            (total + i * 2) % 97,
            (total + i * 3) % 97,
            (total + i * 4) % 97,
            (total + i * 5) % 97,
            (total + i * 6) % 97,
            (total + i * 7) % 97,
        ];
        for j in 0..nums.len() {
            total = (total * 1_000_003 + nums[j]) % 1_000_000_007;
        }
    }
    println!("{}", total);
}
