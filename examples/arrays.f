// Arrays -- see README.md's "Arrays" section for current status/caveats.

int func sum3(nums:arr[int]) {
    return nums[0] + nums[1] + nums[2]
}

arr[int] scores = [88, 92, 79]
log(sum3(scores))

scores[1] = 100
log(scores[1])

arr[arr[int]] grid = [[1, 2], [3, 4]]
log(grid[1][1])
