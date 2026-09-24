# sieve

Write a program that finds the primes up to a bound, with the sieving spread over several threads.

## Input

Standard input holds one decimal integer `N`, from 0 to 20000000.

## Output

Four lines about the primes from 2 to `N`, in this order:

- `count C`: how many primes there are.
- `sum S`: their sum.
- `last L`: the largest of them, or `last none` when there is none.
- `gap G P`: `G` is the largest difference between two consecutive primes up to `N`, and `P` is the smaller prime of the first pair with that difference. Print `gap none` when there are fewer than two primes.

## Requirement

The sieving must run on at least two threads (a parallel region or at least two tasks where the language has them). A program that sieves on one thread fails the task, and so does one with a data race.

## Example

Input:

```
30
```

Output:

```
count 10
sum 129
last 29
gap 6 23
```
