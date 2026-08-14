// FizzBuzz -- a small, dependency-free tour of loops, modulo, and
// control flow. Build and run with:
//
//   ./bin/festina examples/fizzbuzz.f -o fizzbuzz
//   ./fizzbuzz

for int i = 1, i <= 30, i++ {
    if i % 15 == 0 {
        log('FizzBuzz')
    } else if i % 3 == 0 {
        log('Fizz')
    } else if i % 5 == 0 {
        log('Buzz')
    } else {
        log(i)
    }
}
