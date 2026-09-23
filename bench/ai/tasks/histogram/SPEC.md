# histogram

Write a program that counts values into 256 bins, with the counting spread over several threads.

## Input

Standard input holds whitespace-separated decimal integers: first `n` (0 to 2000000) and `shift` (0 to 24), then `n` values, each from 0 to 4294967295.

## Output

A value `v` falls in bin `(v >> shift) & 255`. For every bin from 0 to 255 whose count is not zero, in increasing bin order, print one line: the bin, a space, and its count. Print nothing else. With `n = 0` the output is empty.

## Requirement

The counting must run on at least two threads (a parallel region or at least two tasks where the language has them). A program that counts on one thread fails the task, and so does one with a data race.

## Example

Input:

```
6 4
16 17 32 4096 255 31
```

Output:

```
0 1
1 3
2 1
15 1
```
