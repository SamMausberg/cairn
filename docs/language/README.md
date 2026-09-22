# Language reference

This reference states implemented behavior. Every construct is executed natively by the test suite, every accepted example compiles, and every refused example is refused with the code shown. None of it is a whole-compiler proof; [verification](../verification.md) says what is proved. `docs/history/` holds the 0.2, 0.3 and 0.4 specifications, which record earlier proposals and must not be used to infer accepted features.

Three rules explain most of the language.

Costs are visible. Nothing allocates, synchronizes, copies an owner, runs in parallel or crosses a memory boundary unless the source says so, and every function carries an inferred effect row.

Borrows are second class. A borrow exists only as a parameter or a call argument, so there are no lifetime annotations and no dangling references.

Short forms are contracts. `compact`, `reduce`, `parallel`, `family`, `derive wire` and `try` expand to ordinary inspectable code with their obligations attached to the expansion.

| Chapter | Covers |
| --- | --- |
| [values.md](values.md) | scalars, checked and wrapping arithmetic, conversions, functions and control flow, records, sums, `match`, `try`, constants |
| [memory.md](memory.md) | arrays, views and parts, owners and moves, `take` and `swap`, linear values and `defer`, layout and the machine |
| [generics.md](generics.md) | type and natural parameters, bounds, certifying a template, traits, `dyn` and `Dyn`, function values and closures |
| [effects.md](effects.md) | the effect row, ceilings, operand order, `extern` and `unsafe` |
| [concurrency.md](concurrency.md) | tasks and leases, atomics and mutexes, parallel regions, `reduce` and `compact`, placement, queued device work |
| [modules.md](modules.md) | modules, projects, vendored dependencies, recipes and `derive` |

## What the language does not have

* No inheritance and no implicit boxing.
* No lifetime annotations: a borrow cannot outlive the call it is written in.
* No implicit conversion, no operator overloading, no shadowing, no block-tail return.
* No wildcard arm, and no propagation form other than `try`.
* No exception, no unwinding and no rollback: a failed guard aborts.
* No orphan rule, because coherence is judged over the whole program.
* No cancellation of a task or of queued device work.
* No loop that implies parallelism.
* No downloads: dependencies are vendored sources.

## Scope of proof

Typed, native-built, finite-tested, SMT-equivalent and Lean-checked are distinct claims; see [verification](../verification.md). Generic instances, owners, lanes and the foreign boundary are native-implemented and tested, not mechanized.
