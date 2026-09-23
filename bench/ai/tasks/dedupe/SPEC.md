# dedupe

Repair a program that summarizes runs of equal readings.

## What the program should do

Standard input holds whitespace-separated decimal integers: first `n` (0 to 200000), then `n` signed 64-bit readings in non-decreasing order.

For each distinct reading, in increasing order, the program prints a line `V xC`: the reading and how many times it occurs. Then it prints `distinct D`, the number of distinct readings.

## Bug report

"When the data file holds no readings, so the input is just `0`, the report should be the single line `distinct 0`. Instead the program crashes, hangs or prints garbage, depending on the build."

Find the defect and fix it, changing as little of the program as the fix needs. Every other input must keep the behaviour described above.

## Example

Input:

```
6
-4 -4 0 7 7 7
```

Output:

```
-4 x2
0 x1
7 x3
distinct 3
```
