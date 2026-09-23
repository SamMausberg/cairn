# basis_points

Repair a program that reports usage as a share of a quota in basis points.

## What the program should do

Standard input holds whitespace-separated decimal integers: first `m` (0 to 100000), then `m` pairs `used total`. In every pair `total` is from 0 to 1000000000000000 (10^15), `used` is from 0 to 10^18, and, when `total` is not 0, `used` is at most `1000 * total`.

For each pair the program prints one line: `undefined` when `total` is 0, and otherwise `B bp`, where `B` is `floor(used * 10000 / total)` computed exactly.

## Bug report

"For `used = 2000000000000000` and `total = 1000000000000000` the report should say `20000 bp`. It does not: depending on the build it prints a wrong number or aborts. Small quotas are fine."

Find the defect and fix it, changing as little of the program as the fix needs. Every other input must keep the behaviour described above.

## Example

Input:

```
4
1 3
2500 10000
7 0
2000000000000000 1000000000000000
```

Output:

```
3333 bp
2500 bp
undefined
20000 bp
```
