let total = 0;
for (let i = 0; i < 100000000; i++) {
    total = (total * 1000003 + i) % 1000000007;
}
console.log(total);
