# Task threads reused, measured on a shared machine

`bench/host_tasks/host_tasks.py` builds `bench/host_tasks/host_tasks.cpp` twice with the project's own flags, once against the runtime headers of 89392ed (one thread per task) and once against the crew of reusable task threads, and runs the two in alternation. Each case repeats one round of a pipeline and reports the median time a round took, with its allocations and synchronization included: a spawn and its wait, a fork-join step of two tasks over the halves of 4096 elements, a group of eight refilled and collected in full, and a chain of sixteen tasks each waiting for the one it started. `benchmark.json` files each session under a label, with every run, the load average around it and the threads each case started.

## What does not depend on the load

The crew counts the threads it starts, and the old runtime starts one per task by construction. Both sessions counted the same:

| case | rounds | threads started before | after |
|---|---|---|---|
| spawn and wait | 28,000 | 28,000 | 1 |
| fork-join of two tasks | 14,000 | 28,000 | 1 |
| group of eight, refilled | 3,500 | 28,000 | 6 |
| chain of sixteen | 1,400 | 22,400 | 8 |

A thread already parked served the rest, and the cases ran one after another in one process.

## What the times show

Both sessions ran while other agents built and tested on the same sixteen-lane machine, so the load average is recorded beside each. `loaded_1` was built for `x86-64` at a load average of 17 to 23, and `loaded_2` for `x86-64-v4` at 8 to 11. Times are median nanoseconds per round.

| case | `loaded_1` before | after | `loaded_2` before | after |
|---|---|---|---|---|
| spawn and wait | 313,976 | 312 | 100,117 | 265 |
| fork-join of two tasks | 442,181 | 100,008 | 146,349 | 929 |
| group of eight, refilled | 1,533,714 | 1,619,134 | 472,857 | 171,473 |
| chain of sixteen | 7,142,126 | 5,674,618 | 2,068,744 | 991,188 |

A spawn followed at once by its wait finds its thread still spinning, so the round costs a hand-off instead of a thread, two to three orders of magnitude less in both sessions. The other cases wake threads that may have gone to sleep, and how long a woken thread waits for a core depends on the load: at 8 to 11 the fork-join gained a factor of 150, the group round almost three and the chain two, while at 17 to 23 the group round came out level although it started 6 threads where it had started 28,000. Nothing here was measured on a quiet machine, and nothing is claimed for one.

## What this does not show

The crew keeps the lane pool's spin before a thread sleeps (about sixty microseconds, measured for regions on a quiet machine), and was not tuned here, because shortening it on this machine traded one case against another inside the noise. Parked threads hold their stacks' address space, which is why at most `hardware_concurrency()` stay parked. Device work is not part of this record: `cr::gpu::Context` is tested against a mock device and has not run on one.
