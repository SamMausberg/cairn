# block_scan

Write a program that computes running totals the way a GPU does: in blocks of threads that share a scratch array and wait for each other at a barrier.

## Input

Standard input holds whitespace-separated decimal integers: first `n` (0 to 100000), then `n` values, each from 0 to 4294967295.

## Output

`n` lines: line `i` (counting from 0) is the sum of the first `i + 1` values. With `n = 0` the output is empty.

## Requirement

Compute the totals as a GPU kernel would. The values fall into blocks of 256 consecutive values, the last block possibly shorter. A team of threads scans each block in an array the team shares: in steps, where every thread of the team waits at a barrier before any thread starts the next step, until each element holds the total of its block up to itself. Then each block's elements are offset by the totals of all earlier blocks. The paragraph on your language below says which constructs must carry this. A program that scans on one thread, or without a barrier between the steps, fails the task, and so does one with a data race.

## Example

Input:

```
5
1 2 3 4294967295 5
```

Output:

```
1
3
6
4294967301
4294967306
```
