// Arrays -- see api.md's Arrays section for the full reference and
// caveats (not bounds-checked, no growth -- claude.md doesn't specify
// either).

int func sum3(nums:arr[int]) {
    return nums[0] + nums[1] + nums[2]
}

arr[int] scores = [88, 92, 79]
log(sum3(scores))

scores[1] = 100
log(scores[1])

arr[arr[int]] grid = [[1, 2], [3, 4]]
log(grid[1][1])

// claude.md #60, #63: for loops + .length -- the compiler's own worked
// example (claude.md #60's "array iteration example").
for int x = 0, x < scores.length, x++ {
    log(scores[x])
}
