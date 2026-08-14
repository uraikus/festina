package main

import "fmt"

func main() {
	var total int64 = 0
	for i := int64(0); i < 100000000; i++ {
		total = (total*1000003 + i) % 1000000007
	}
	fmt.Println(total)
}
