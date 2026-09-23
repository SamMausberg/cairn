# chunk_sums

Write a program that splits a list of signed integers into `k` contiguous chunks, sums each chunk on its own thread, and reports every sum that overflows instead of wrapping or crashing.

## Input

Standard input holds whitespace-separated decimal integers: first `k` (1 to 16) and `n` (0 to 200000), then `n` values, each a signed 64-bit integer (from -9223372036854775808 to 9223372036854775807).

## Chunks

Chunk `j`, for `j` from 0 to `k - 1`, holds the values at positions `floor(j * n / k)` up to but not including `floor((j + 1) * n / k)`. A chunk may be empty; its sum is 0.

A chunk's sum is computed by adding its values to 0 from left to right. The chunk overflows when any of those partial sums falls outside the signed 64-bit range, even if a later value would bring it back.

## Output

Print `k` lines, one per chunk in order: `chunk J SUM`, or `chunk J overflow` when that chunk overflowed.

Then print one line: `total T`, where `T` is the sum of the chunk sums added from chunk 0 upwards, or `total overflow` when any chunk overflowed or when any partial sum of that addition falls outside the signed 64-bit range.

## Requirement

Each chunk must be summed by a thread (or task) of its own, all started before any is waited for. A program with a data race fails the task.

## Example

Input:

```
3 7
1 2 3 9223372036854775807 1 -5 -6
```

Output:

```
chunk 0 3
chunk 1 overflow
chunk 2 -10
total overflow
```
