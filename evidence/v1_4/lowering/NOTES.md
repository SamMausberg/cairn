# Guards left out by the checker's facts

`guard_counts.json` counts the guard calls in the C++ that commit 164b63e emits for twenty programs: every example project and single-file example that compiles on this host, and the eight preregistered bench kernels. Each program is emitted twice from one checked tree, once as it is and once with every established site forgotten, which is what the 1.3 emitter wrote. The command was `python3 bench/cpu/guard_counts.py --out evidence/v1_4/lowering/guard_counts.json`.

| guard | written by 1.3 | written now |
|---|---|---|
| `cr::at`, an element index | 1165 | 265 |
| `cr::add`, `sub`, `mul`, checked arithmetic | 500 | 456 |
| `cr::convert`, `truncate`, a narrowing conversion | 409 | 180 |
| `cr::divide`, `remainder` | 43 | 43 |
| `cr::part`, an array part | 141 | 141 |
| `cr::view`, `disjoint`, the entry guards | 1112 | 1112 |

`examples/basics/family.cairn` holds 512 of the 1165 index sites, one per specialized function, and loses all of them. Without it the index sites go from 653 to 265. Every preregistered kernel except `stencil_1d_wrap` and `tasks_split` now keeps only its entry guards: the wrapping stencil indexes at `sub_wrap(i, 1)`, which can wrap, and `tasks_split` keeps its parts and the checked `q1 + q1`, which nothing bounds.

These are counts of text. They say nothing about how many guards run, since a guard in a loop runs once per iteration and one at an entry runs once per call, and nothing about what a guard costs. Timing is a separate run under the addendum in `bench/suite/PREREGISTRATION.md`.

What stays: every guard on a `let mut` local, since the facts name only values that cannot change; every index into an array whose extent is not tied to the index by a loop, a condition or an early exit (`std.map`'s probe, `std.text`'s parsers); the checked arithmetic in `u64` and smaller types, since the facts are about `usize`; array parts and entry guards, which this change does not touch.
