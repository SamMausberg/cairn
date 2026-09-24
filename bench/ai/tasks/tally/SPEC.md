# tally

Repair a program that tallies large values on several threads.

## What the program should do

Standard input holds whitespace-separated decimal integers: first `k` (1 to 16), `n` (0 to 200000) and a threshold `t` (0 to 18446744073709551615), then `n` values from 0 to 18446744073709551615.

The program splits the values into `k` contiguous chunks that together cover every value exactly once, tallies each chunk on a thread (or task) of its own, all started before any is waited for, and prints three lines:

- `above C`: how many values are greater than `t`.
- `sum S`: the sum of those values modulo 2^64.
- `max M`: the largest of all `n` values, or `max none` when `n = 0`.

Every chunk must be tallied on its own thread, and a data race fails the task.

## Bug report

"With `k = 1` the answers are right. With more threads, on large inputs, `above` and `sum` sometimes come out lower than they should be, and they change from run to run on the same input."

Find the defect and fix it, changing as little of the program as the fix needs.

## Example

Input:

```
3 10 4
1 2 3 4 5 6 7 8 9 10
```

Output:

```
above 6
sum 45
max 10
```
