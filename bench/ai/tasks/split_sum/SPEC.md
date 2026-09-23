# split_sum

Repair a program that adds a list of numbers on several threads.

## What the program should do

Standard input holds whitespace-separated decimal integers: first `k` (1 to 16) and `n` (0 to 200000), then `n` values from 0 to 18446744073709551615.

The program splits the values into `k` contiguous chunks that together cover every value exactly once, adds each chunk on a thread (or task) of its own, all started before any is waited for, and prints `sum S`: the total of all `n` values modulo 2^64. Every chunk must be summed on its own thread, and a data race fails the task.

## Bug report

"With `k = 3` and the ten values 1 to 10 the program prints `sum 45`, and the right answer is `sum 55`. With `k = 2` it gets that input right. It looks like some values are skipped when the count does not divide evenly."

Find the defect and fix it, changing as little of the program as the fix needs.

## Example

Input:

```
3 10
1 2 3 4 5 6 7 8 9 10
```

Output:

```
sum 55
```
