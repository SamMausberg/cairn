# Task threads reused, measured on a shared machine

`bench/host_tasks/host_tasks.py` builds `bench/host_tasks/host_tasks.cpp` twice with the project's own flags, once against the runtime headers of 89392ed (one thread per task) and once against the crew of reusable task threads, and runs the two in alternation. Each case repeats one round of a pipeline and reports the median time a round took, with its allocations and synchronization included: a spawn and its wait, a fork-join step of two tasks over the halves of 4096 elements, a group of eight refilled and collected in full, and a chain of sixteen tasks each waiting for the one it started. `benchmark.json` files each session under a label, with every run, the load average around it and the threads each case started.

## What does not depend on the load

The crew counts the threads it starts, and the old runtime starts one per task by construction:

| case | rounds | threads started before | after |
|---|---|---|---|
| spawn and wait | 28,000 | 28,000 | 1 |
| fork-join of two tasks | 14,000 | 28,000 | 1 |
| group of eight, refilled | 3,500 | 28,000 | 6 |
| chain of sixteen | 1,400 | 22,400 | 8 |

A thread already parked served the rest, and the cases ran one after another in one process.

## What the times show

Session `loaded_1` ran with five other agents building and testing on the same sixteen-lane machine, at a load average of 17 to 23:

| case | before, median ns | after, median ns |
|---|---|---|
| spawn and wait | 313,976 | 312 |
| fork-join of two tasks | 442,181 | 100,008 |
| group of eight, refilled | 1,533,714 | 1,619,134 |
| chain of sixteen | 7,142,126 | 5,674,618 |

A spawn followed at once by its wait finds its thread still spinning, so the round costs a hand-off instead of a thread, three orders of magnitude less. The other three cases wake threads that have gone to sleep, and on a machine with more runnable threads than cores the time to be scheduled dominates: in this session the group round came out level although it started 6 threads where it had started 28,000, and the fork-join and the chain gained a factor of four and a fifth. Nothing here was measured on a quiet machine, and nothing is claimed for one.

## What this does not show

The crew keeps the lane pool's spin before a thread sleeps (about sixty microseconds, measured for regions on a quiet machine), and was not tuned here, because shortening it on this machine traded one case against another inside the noise. Parked threads hold their stacks' address space, which is why at most `hardware_concurrency()` stay parked. Device work is not part of this record.
