let total = 0;
for (let i = 0; i < 2000000; i++) {
    const nums = [
        total % 97,
        (total + i) % 97,
        (total + i * 2) % 97,
        (total + i * 3) % 97,
        (total + i * 4) % 97,
        (total + i * 5) % 97,
        (total + i * 6) % 97,
        (total + i * 7) % 97,
    ];
    for (let j = 0; j < nums.length; j++) {
        total = (total * 1000003 + nums[j]) % 1000000007;
    }
}
console.log(total);
