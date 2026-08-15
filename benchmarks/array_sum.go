package main

import "fmt"

func main() {
	var total int64 = 0
	for i := int64(0); i < 2000000; i++ {
		nums := []int64{
			total % 97,
			(total + i) % 97,
			(total + i*2) % 97,
			(total + i*3) % 97,
			(total + i*4) % 97,
			(total + i*5) % 97,
			(total + i*6) % 97,
			(total + i*7) % 97,
		}
		for j := 0; j < len(nums); j++ {
			total = (total*1000003 + nums[j]) % 1000000007
		}
	}
	fmt.Println(total)
}
