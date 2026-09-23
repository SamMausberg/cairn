# What changed from 0.8.3, as cairn diff reads it

`cairn diff v0.8.3 13692b3` was run on 2026-09-22 for the packaged library and for each of the eight example projects that 0.8.3 already held, by `cairn diff v0.8.3 REV --std --budget-s 600` and `cairn diff v0.8.3 REV --in examples/PROJECT --budget-s 120` (the tag then had its old name). Each `.json` is the `cairn.diff/1` record and each `.md` the pull request section written beside it. Both versions were read from git objects into scratch directories, and both were checked and lowered by the compiler of `13692b3`, so this compares two sources, not two compilers. The machine was shared with other agents' builds and suites, which bears only on how far the solver got inside its budgets.

| side | identical-code | identical-source | smt-equivalent | behavior-changed | unknown | other | semantic version |
|---|---|---|---|---|---|---|---|
| std | 60 | 21 | 1 | 0 | 40 | 129 added | major |
| hello | 2 | 0 | 0 | 0 | 0 | | none |
| systems | 3 | 0 | 0 | 0 | 3 | | unknown, at least patch |
| apps/analytics | 78 | 5 | 0 | 0 | 12 | | unknown, at least patch |
| apps/kvstore | 12 | 0 | 0 | 0 | 6 | | unknown, at least patch |
| apps/service | 6 | 0 | 0 | 0 | 2 | 2 signature-changed, 3 added, 1 removed | major |
| apps/simulator | 6 | 0 | 0 | 0 | 2 | | unknown, at least patch |
| apps/gpu_pipeline | 7 | 0 | 0 | 0 | 2 | | unknown, at least patch |
| embedded | 3 | 0 | 0 | 0 | 4 | | unknown, at least patch |

Most of what the later work rewrote in these programs compiles to the same C++ it did. Of the 277 functions both versions hold, 177 are identical code with everything they call, and 26 more are templates nothing instantiates whose source is unchanged. The syntax passes of this release (bare variants, one-statement arms, compound assignment on a local, call statements) account for much of that, and that is what the class is for: it says those rewrites changed nothing that runs.

The library is `major` for one reason beside its additions: `std.map.Map` gained the generation that makes a `Slot` detect a stale entry, which changes the record's definition. The service is `major` because `respond` and `say` changed signatures and `serve` was removed when it moved to the I/O ring. No function in any record is `behavior-changed`. That says the solver found no difference where it could decide, and nothing about the functions it could not.

Every `unknown` names its reason, and they fall into a few kinds.

| reason | functions |
|---|---|
| a generic function no code instantiates, whose tokens changed or which names a changed function | 29 |
| an `unsafe` block, which the value model does not enter | 16 |
| an owner inside a record, a sum or an array | 13 |
| a string literal as an expression | 4 |
| a loop the solver cannot bound in 16 iterations | 4 |
| a comparison stopped at its limit, or a budget spent before the function | 4 |
| `dyn` | 1 |

The four loops are `std.text.parse_u64`, `parse_i64` and `hash_bytes`, and `analytics.agg.run_static`. The diff retries such a loop with its unsigned parameters at 16 or below, and a record keeps that retry only when it decides: here it found neither a difference nor an equivalence, so the four stay unknown with the unbounded reason. Three comparisons were stopped at the diff's limit for one function and one was never reached before the budget ran out, all in `systems` and `embedded`. The query `systems.main` produces is 536,886 bytes, and Z3 was still reading it when the limit came. A larger `--budget-s` or `--timeout-ms` may decide them; it was not tried, and until it is they are unknown. Nothing here was replayed natively, because no function has a witness.

What this does not show: that 0.8.3 and the later commit behave alike wherever a function is `unknown`, that the C++ compilers compile identical text identically (they are the same compiler here, on both sides), or anything about the device code of `gpu_pipeline`, which the diff compares as emitted text and never runs.
