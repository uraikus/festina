package main

import "fmt"

// See char_scan.f's own comment: a lexer-shaped workload -- walk a
// source-sized buffer character by character, counting identifier runs.
// Ranges over []byte rather than the string directly, since ranging a
// Go string decodes runes and would measure UTF-8 decoding instead of
// scanning -- matching every other implementation.
func main() {
	unit := "int func compute(a:int, b:int) { return a + b * 2 } "
	src := unit
	for d := 0; d < 15; d++ {
		src = src + src
	}
	buf := []byte(src)
	total := 0
	for pass := 0; pass < 5; pass++ {
		tokens := 0
		inWord := false
		for _, c := range buf {
			isAlpha := (c >= 97 && c <= 122) || (c >= 65 && c <= 90)
			if isAlpha {
				if !inWord {
					tokens++
				}
				inWord = true
			} else {
				inWord = false
			}
		}
		total += tokens
	}
	fmt.Println(total)
}
