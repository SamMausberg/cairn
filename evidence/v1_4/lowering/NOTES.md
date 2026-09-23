# Guards the lowering leaves out

The first two sections count guard calls in emitted C++ text, which says neither how many guards run, since a guard in a loop runs once per iteration and one at an entry once per call, nor what a guard costs. The third times calls with and without their callee's entry checks, and the fourth times the compiler. The machine was shared with other agents' builds and suites, which counting text does not feel and timing does.

## Every program as it stands

`guard_counts.json` was taken at commit 816afbd with `python3 bench/cpu/guard_counts.py --out evidence/v1_4/lowering/guard_counts.json`. It covers the 23 programs that compile on this host: every example project and single-file example, and the eight preregistered bench kernels. Each is emitted once as the compiler emits it, where a guard is left out only where `verify/elision.py` accepted the checker's proof, and once with `keep_guards`, which writes every guard as the 1.3 emitter did.

| guard | every guard kept | as emitted |
|---|---|---|
| `cr::at`, an element index | 1533 | 499 |
| `cr::add`, `sub`, `mul`, checked arithmetic | 1266 | 792 |
| `cr::convert`, `truncate`, a narrowing conversion | 577 | 300 |
| `cr::divide`, `remainder` | 96 | 96 |
| `cr::part`, an array part | 243 | 189 |
| `cr::view`, `disjoint`, the entry checks | 1431 | 1431 |

The entry checks are the same text in both, and they now run in fewer places. A function that takes views has a checked C entry `cf_` and a lean body `ci_`, and 621 call sites from CAIRN code reach a lean body. Each time those sites execute they skip 900 entry checks between them, which the conservative build runs. `examples/basics/family.cairn` holds 512 of the index sites, one per specialized function, and loses all of them.

## The same programs under two compilers

`guard_delta_ed5a179.json` and `guard_delta_816afbd.json` hold the programs fixed: the examples and bench kernels of commit ed5a179, where this round of work began, compiled by the compiler of ed5a179 and by the compiler of 816afbd with the standard library of ed5a179, so that library edits made in between do not enter. The command was `python3 bench/cpu/guard_delta.py SRC CORPUS --out FILE`, with CORPUS a `git archive` of ed5a179. All twenty programs compiled under both.

| guard | ed5a179 | 816afbd |
|---|---|---|
| `cr::at` | 273 | 273 |
| `cr::add`, `sub`, `mul` | 448 | 385 |
| `cr::convert`, `truncate` | 194 | 194 |
| `cr::divide`, `remainder` | 39 | 39 |
| `cr::part` | 141 | 108 |
| `cr::view`, `disjoint` | 1123 | 1123 |

The 63 fewer checked subtractions and additions are extents: an extent a call leaves out, or writes as a part's own `hi - lo`, is covered by that part's guard, and the right side of `&&` now knows what its left side established. The 33 fewer parts are parts whose `lo <= hi <= len` the facts settle and whose extent is `hi - lo`. The service, the key/value store, the analytics engine, the GPU pipeline's host code, the simulator and `tasks_split` changed; the other programs emit the guards they did. The compiler of 816afbd also holds other agents' changes of the same days, none of which changes what these programs lower to except through guards.

Before the checked entries landed, `tools/checks/emission_identity.py compare --normalize guards --normalize literals` on the 1044 programs of ed5a179 (every example, the whole library, every program written into a test and every `cairn` block of the docs) found every change made by 0512566 and 6b45dd0 to be a guard left out or a runtime header included later or not at all (a program whose every part is settled needs no `cairn_owners.hpp`), and no program writing more guards than before. A program keyed by a line of `tests/soundness/test_established.py` that moved is not counted as a change. After 9087d5c a function that takes views is written as two C++ functions, so that comparison no longer applies as it stands.

What stays: every guard on a `let mut` local, since the facts name only values that cannot change; every index into an array whose extent is not tied to the index by a loop, a condition or an early exit (`std.map`'s probe, `std.text`'s parsers); the checked arithmetic in `u64` and smaller types, since the facts are about `usize`; a part whose end holds a guard of its own, since a discharged part does not evaluate its end again; and a bound written as `n - w` and indexed as `i + w`, since the facts relate two values at a time.

## What an internal call stops paying

`entry_checks.json` is one run of `python3 bench/cpu/entry_checks.py --repeat 15 --rounds 5 --out evidence/v1_4/lowering/entry_checks.json` at commit 816afbd, and `entry_checks_repeat.json` the same command a minute later. Each case is a CAIRN program whose hot loop calls a function that takes views, over 2^24 elements. It is emitted twice from one checked tree, once as the compiler emits it, where the call reaches the callee's lean body `ci_`, and once with every call sent through the checked entry `cf_`, as before checked entries existed; every other guard is the same. Both builds use the build's flags (`-O3`, `-march` of this host), the two alternate, each keeps its best of 15 runs over 5 rounds, and both must return the same value. The machine was shared with other agents (load average about 7 on sixteen threads), so only ratios measured side by side are worth reading.

| case | what the loop calls | g++ checked / lean | clang++ checked / lean |
|---|---|---|---|
| `windows` | `total` on a four-element part, once per element | 1.68, 1.69 | 1.03, 1.02 |
| `find` | `std.text.find` once; its own loop indexes and calls nothing | 1.00, 1.00 | 0.99, 1.00 |
| `pairs` | `add_into(acc, x[r * 8..r * 8 + 8])`, a view check and a disjointness check per row | 1.17, 1.12 | 1.79, 1.92 |

Where the loop calls a small function with views, skipping its entry checks took g++ from 8.1 to 4.8 ms on `windows`, and clang++ from 6.7 to 3.8 ms on `pairs`, whose callee runs both a view check and a disjointness check. Under clang++ `windows` did not change, and `find`, whose hot loop calls nothing, did not change under either compiler, as it should not. These are three small cases on one machine, not a general speedup.

## What the audit costs the compiler

Compiling the 211 programs of ed5a179 that `compile_source` is given here (every example project, one program importing the whole library, and every `cairn` block of the docs, some of which are refused) took 0.859 s with `verify/elision.py` and 0.807 s with the audit replaced by nothing, best of five interleaved passes, so the independent check costs about 6% of compile time here. That was the audit of the commit that adds these notes, which does not walk a function where the checker proposed nothing; before that change the same measurement gave 7%. The same corpus compiled in 0.77 to 0.89 s by the compiler of ed5a179 and 0.85 to 1.05 s by the compiler of 816afbd over three interleaved rounds, which also holds every other change made in between.
