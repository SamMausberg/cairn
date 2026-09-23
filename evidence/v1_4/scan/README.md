# The host scan and the radix sort, timed on a shared machine

`bench/scan/run.py` builds `bench/scan/scan.cairn` the way `cairn build` does, under clang++ 21.1.8 and g++ 13.3, and runs it once per compiler with fifteen repetitions. Every repetition times each kernel once, in an order that rotates, so a change in load reaches every kernel alike. `results.json` holds the median and the minimum of every kernel at every size, and `predicted.json` what `cairn predict` said of the three scan kernels before they ran.

The machine is an AMD Ryzen 7 7800X3D, sixteen lanes, under WSL2. It was shared: five other agents were building and testing on it, and the load average was between 8 and 11 on sixteen threads. Every time here is wall clock under that load, so the ratios mean more than the times, and a quiet machine may be faster.

## What was timed

`written` is the loop a scan replaces, `t = add_wrap(t, x[i]); out[i] = t;` over `u64`. `scan` is `scan add_wrap out for i in n yield x[i]`, and `scan_parallel` the same with `parallel`, on the lane pool at its default size. `radix_sort` is `std.sort.radix_sort` and `heapsort` is `std.sort.sort`, both over the same xorshift `u64` keys, rewritten before every timing.

## Medians, in microseconds

| kernel | n | clang++ | g++ |
|---|---|---|---|
| written | 1e5 | 43.3 | 54.0 |
| scan | 1e5 | 39.1 | 54.4 |
| scan_parallel | 1e5 | 33.1 | 35.6 |
| written | 1e6 | 355 | 403 |
| scan | 1e6 | 336 | 388 |
| scan_parallel | 1e6 | 250 | 234 |
| written | 1e7 | 8277 | 6880 |
| scan | 1e7 | 8476 | 6148 |
| scan_parallel | 1e7 | 5499 | 4525 |
| radix_sort | 1e5 | 1071 | 1182 |
| heapsort | 1e5 | 6035 | 10551 |
| radix_sort | 1e6 | 11371 | 14543 |
| heapsort | 1e6 | 91356 | 152062 |

The sequential scan runs level with the loop it replaces, as it should, since it emits that loop. The pooled scan was the fastest of the three at every size under both compilers: 1.3 to 1.5 times the written loop at 1e5, 1.4 to 1.7 at 1e6 and 1.5 at 1e7. The gain is modest because a prefix sum streams its array in both passes; the model names DRAM bandwidth as the bound at 1e7, and this run did not vary the lane count to test that. The radix sort sorted `u64` keys 5.6 to 10.5 times faster than the heapsort, which is the difference between eight linear passes and n log n comparisons; it is also stable, and the heapsort is not.

## Against the prediction

`cairn predict` ranked the three scan kernels the way they ran at every size under both compilers: the pooled scan below the two sequential ones, and those two equal. It predicted the times low. Under clang++ the sequential ones came out 1.7 to 2.1 times the prediction, and the pooled one 2.3 times at 1e5, 4.4 times at 1e6 and 1.2 times at 1e7. The pooled predictions carry `medium` confidence and say why: a wide host region between 1e5 and 1e7 elements is the range `evidence/v1_4/perf_model/` found the model weakest in, and this run was on a loaded machine as well.

## What this does not show

One machine, one run per compiler, under load. Nothing here is a device measurement: the device scan compiles for sm_120 and runs only under `make gpu`, which this run did not do. Nothing compares CAIRN with another language's scan or sort.
