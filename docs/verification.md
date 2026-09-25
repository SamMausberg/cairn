# Verification

This page says what has been shown about CAIRN's compiler and the programs it accepts, and by what means. After reading it you can tell which claim a result supports, what backs that claim, and what it leaves open.

Nothing proves the whole compiler correct. Three means each establish something narrower: Lean 4 proofs about models written by hand beside the compiler, Z3 queries about a modeled fragment of the source language, and tests that run programs natively. The claims they support are kept apart, and `unknown` is none of them.

## The eight claims

Each claim below is a different statement, and a result supports only the claim it names.

| Claim | What it says | Where you meet it |
| --- | --- | --- |
| accepted | The compiler checked the program against its rules and refused nothing. | every command that compiles |
| typed | The status `cairn check` and the edit hosts give an accepted program or edit. It says nothing about what the program computes, whether it ends, or how fast it runs. | `cairn check`, the edit hosts |
| native-built | A C++ compiler built the emitted code into an artifact. | `cairn build` |
| finite-tested | The cases that ran passed natively. Every other input is unchecked. | `cairn test`, `cairn validate` |
| sanitizer-clean | A sanitizer watched the runs and reported nothing, on the inputs that ran. | `cairn run --sanitize`, the test suite |
| SMT-equivalent | Z3 found no allowed input on which two versions differ, within the fragment of the language it models. | `cairn verify`, `cairn diff` |
| Lean-checked | Lean's kernel checked a proof about a model written by hand. It says nothing about the Python that implements the rule. | `proofs/`, the receipt's `lean_verified` |
| benchmarked | A timing ran and is recorded under `evidence/`, with its machine. | `bench/`, `evidence/` |

`unknown` means nobody has checked, or a check could not decide. It never counts as success.

A guard is a check the emitted code makes before an operation, such as an index bound or an overflow test, and a failed guard aborts the program. A certificate is arithmetic evidence, in a form a small checker verifies, that one bound on the loop `compact` lowers to holds ([the collector certificates](#the-collector-certificates-and-the-loop-model)).

An edit host is the program that holds the source for an agent and checks each change the agent sends before it is kept, as [agents.md](agents.md) describes. A receipt is the JSON record a compile or a build writes beside its result: the hashes of the source, the generated C++ and the runtime, each function's effects and guards, the lowering rules it trusts, and the certificate bundle it checked.

## What is established, and by what

| Claim | Established by | Where | What it does not cover |
|---|---|---|---|
| The certificate checker is sound: when it accepts a certificate, the conclusion holds. | Lean, `check_sound` | `proofs/Cairn/Affine.lean` | That `linear_certificates.py` implements the Lean `check`; it is a translation checked by review. |
| The seventeen collector certificates pass that checker. | Lean kernel `decide`, `all_checked` | `proofs/Cairn/CollectorCertificates.lean`, generated | Anything about the emitted loop. |
| The model of the collector loop stores only in bounds, never lets either cursor pass the largest value it can hold, and keeps the selected elements in order. | Lean, `store_index_lt_capacity`, `increments_fit`, `collect_spec` | `proofs/Cairn/Collector.lean` | That the emitted C++ is this loop. The model uses `Int` and `Nat` where the C++ uses machine words. |
| An accepted program of the ownership and lease model has no use after a move, use after free, double free, leaked ticket or group, use of a group after `wait`, aliased argument or race, in every order its threads' steps can take and for every value of its bounds; it never gets stuck; and it releases every cell exactly once when it ends normally. | Lean, `accepted_no_fault` and the named faults, `accepted_threads_disjoint`, `accepted_frees_each_allocation_once`, `accepted_progress` | `proofs/Cairn/Places.lean`, `proofs/Cairn/Ownership/` | That `checking.py` implements this model: the differential harness shows agreement on generated programs, and nothing extracts the checker from the proof. Closures, `lane:f`, placement, `reduce` and `compact`, streams. The part guard is assumed of the emitter. |
| A host region runs every index exactly once before it returns, no pool worker is inside it when it returns, and from every state it reaches it returns without another worker joining. | Lean, `runs_once`, `quiet_when_back`, `finishes` | `proofs/Cairn/Region.lean` | That `cairn_parallel.hpp`, with its relaxed atomics, performs the modelled steps, which the model takes to be sequentially consistent. Device launches. |
| Where the checker's facts are true, a guard lowering leaves out cannot fail. | Lean, `index_sound`, `add_sound`, `sub_sound`, `atMostConst_sound`, `part_sound` | `proofs/Cairn/Facts.lean` | That the facts in scope are true where they are used, which an audit checks and nothing proves. That `facts.py` computes what `Facts.lean` computes, which a differential run samples. `usize(x)` of a narrower value, and the lane blocks `window` places. |
| A declared spread gives every element of its tile exactly one holder, and with a declared storage layout no two holders write one offset. | Lean, `ok_one_holder`, `ok_covers`, `ok_one_writer`, `storage_distinct`, `ok_no_collision` | `proofs/Cairn/Layout.lean` | That `layout_algebra.py` computes what `Layout.lean` computes, which a differential run samples. The code that uses a layout: that the lowering computes `D.at(t, v)` as the model's offset, and that a program writes through a spread at all. |
| In a phase the cooperative rule accepts, no two threads are about to take clashing steps, and every complete interleaving leaves one memory and one set of registers; so does a block of accepted phases. | Lean, `no_race`, `deterministic`, `block_deterministic` | `proofs/Cairn/Cooperative.lean` | That `phases.py` decides what the model's `program` decides, which a differential run samples. The global rule of `footprints.py`, warp operations, pipeline stages, and that the lowerings run the modelled steps. |
| Two versions of one function agree on the result and on everything they were lent, for every allowed input. | Z3 over a modeled source fragment, `smt-equivalent` | `src/cairn/verify/scalar/semantics.py` | Anything outside the [modeled fragment](#value-level-source-equivalence), which is `unknown`. It trusts the translator, Z3 and the assumptions stated there. |
| Every declared function and public type of two modules was compared that way. | `cairn verify --all`, `smt-module-equivalent` | `src/cairn/verify/verification.py` | One uncovered function keeps the module incomplete. A restricted entry holds only where its precondition does. Size, count and solver budgets apply. |
| Accepted programs build and run under both compilers, four sanitizers, CUDA and QEMU, and pass their finite task contracts. | Executed tests | `tests/` | Finite inputs only. |
| An implementation computes what its reference computes on the generated boundary cases and the kept regressions. | Executed, finite-tested, `cairn validate` | `src/cairn/verify/validation/validation.py` | Every other input. Both sides are compiled by this compiler, so a fault in the shared lowering agrees with itself. Device implementations run only under `make gpu`. |

These commands run the checks:

```sh
cd proofs && lake build                       # a few seconds from scratch, no dependencies
cd proofs && lake env lean Cairn/Audit.lean   # the axiom audit on its own
python3 -m pytest -q tests/verification
python3 bin/cairn certificates
python3 bin/cairn verify examples/proof_scope/reference.cairn examples/proof_scope/candidate.cairn --all
```

## Tests, native code and failures

The test suite builds accepted programs with both native compilers, clang++ and g++, and runs them under AddressSanitizer, UndefinedBehaviorSanitizer, LeakSanitizer and ThreadSanitizer. Guards in device code have death tests, programs that must abort, and those run only under `make gpu`. A sanitizer-clean run shows that those faults did not happen on the inputs it ran. It says nothing about other inputs.

A generic template that nothing instantiates is never checked, and the receipt lists it under `uninstantiated_templates`.

The rules of `checking.py` that the Lean model leaves out, such as linear values, leases over parts whose bounds are not visible, what a lane's callees may do, placement and the fixed point of effect rows, are exercised by tests that a program is accepted or refused. Nothing connects them mechanically to a model. A test process that prints a pass and then crashes has failed.

## Diffs between versions

[`cairn diff`](tools.md#cairn-diff) gives each function of two versions a class, and each class is a different claim:

| Class | What it says |
|---|---|
| `identical-code` | The same C++ is compiled, up to renaming and the two identities `verify/emission.py` names. It says nothing about whether that C++ is right. |
| `identical-source` | The tokens of a generic nothing instantiates are unchanged, and nothing more. |
| `smt-equivalent` | The result of [value-level source equivalence](#value-level-source-equivalence), with its fragment and its trust in the translator and Z3. |
| `behavior-changed` | A counterexample, replayed by the value model and natively: a fact about both programs on that input. |
| `unknown` | Nothing. |

The deltas printed beside each class are what the checker recorded for each version. The semantic version `cairn diff` proposes is only as strong as the classes, and the change in cost is a prediction.

## Validating an implementation

[`cairn validate`](tools.md#cairn-validate) and the implementation session hold an implementation to its reference by running both on inputs drawn from the contract. The reference is an independent algorithm, so it serves as an oracle, the program whose answer counts as right. It is compiled by the same compiler, though, so a fault in the shared parser, checker, lowering or runtime can make both wrong alike. A pass is finite testing of exactly the cases that ran. A run with no case, an implementation no case reached, and a call past its limit are `unknown`.

Float results agree under one numerical policy, `cairn.agreement/1` in `verify/validation/agreement.py`. Both values first widen exactly to f64, a storage float through f32, and every rule computes in f64. The first rule that applies decides:

1. A NaN agrees with any NaN and with nothing else.
2. Two equal values agree when their bits match, so `-0.0` and `0.0` agree only under a tolerance.
3. An infinity agrees only with itself.
4. Otherwise the values agree when `|r - c| <= absolute + relative * |r|`, where `r` is the reference's value and `c` the candidate's.

The relative part scales the reference, as `numpy.isclose` scales its second argument, so a wrong result cannot widen its own bound.

The rules are written once, as CAIRN expressions. Host validation, `cairn test`'s replay of kept cases and the implementation session evaluate them as Python, and the generated device tests carry them as a CAIRN function the compiler lowers. `tests/verification/test_agreement.py` requires the same verdict from both forms on one table of pairs, among them NaNs, both zeros, infinities, subnormals and the largest finite value, under clang++ and g++.

Z3's answer on the same pair of functions is reported beside the finite result, and it decides a loop only up to the unrolling bound the record names. Z3 compares exactly and does not know the allowed domain, so a counterexample it finds is replayed natively through the same path as every case. One that breaks the policy fails the validation and is kept as a regression. One outside the domain, or whose native results agree under the policy, leaves the finite result standing, and the record says which. A replay that decides nothing makes the validation `unknown`.

## Value-level source equivalence

`cairn verify` and `cairn diff` ask whether two versions of one function can differ. `src/cairn/verify/scalar/semantics.py` evaluates a fragment of the source symbolically, as formulas over unknown inputs, and Z3 searches for inputs that tell the two versions apart. The fragment has integers of exact width and bools, `f32` and `f64`, records, enums and sums with `match` and `try`, array views and parts, single borrows, fixed local storage, owners on the heap that a function keeps to itself, `compact`, host `reduce`, branches, bounded loops, early return, and calls without cycles.

An observation is what a caller can see of a call: the returned value, the length and elements of a returned owner, and the final contents of every `rw` parameter; or else one abort, with no difference made between aborts. These are `unknown`, with the reason: an owner inside a record, a sum or an array, recursion, tasks, lanes, atomics, device placement, closures, `dyn`, the foreign boundary, a storage float or a quantization, and a function that asserts.

For a reference R, a candidate C and a domain D, the inputs a precondition allows, Z3 is asked whether some allowed input makes one version abort and the other return, or makes both return different observations. When Z3 finds no such input, and no NaN can reach what is observed, the answer is `smt-equivalent` within this model. A counterexample is replayed through an independent evaluator before it is reported, and the checker asks Z3 again for one with small values, so a slip at a boundary reads as `x = 100`. A missing tool, a translation error, unsupported syntax, an exhausted budget, a replay that disagrees and a timeout are all `unknown`. No proof is rebuilt in Lean, and no theorem relates this translator to native code. A reference that states the wrong requirement is checked as faithfully as a right one.

What the model assumes, case by case:

| Case | What the model assumes |
|---|---|
| Views | A view is one SMT array per scalar component, with the extent its signature names. Inputs are what the emitted entry guards accept: `rw` views are distinct storage, and read-only views may alias. Two parts of one array share one array term, and the callee's `cr::disjoint` guard aborts as the emitter runs it, a case that `E-ALIAS` keeps an accepted program from reaching. |
| Owners | A local owner is zeroed storage with its own length. `take` leaves length 0 behind, and `swap` exchanges storage and length. A `Buf[T]` passed by value has any length and contents, and a returned one is observed element by element. |
| Tags | A sum is its emitted tag beside every payload, and two sums compare their tags and the active payload. Only a value parameter's top-level tag is guarded, as the emitter guards it, so any other tag may name no variant, and a `match` over such a tag aborts. This assumes the emitter closes every `switch` with `default: cr::trap();`. A type in the signature must be defined the same on both sides, or the answer is `invalid-contract`. |
| Collectors | `compact` and `reduce` are unrolled as the host emits them. A float reduction is `unknown`. |
| Traps | Every checked operation and guard aborts, and every abort is one observation, so two versions that both trap are equal. By default the reference must return on every allowed input (`allow_reference_traps` opts out). |
| Floats | Z3's FloatingPoint theory, rounding to nearest even once per operation. `-0.0` and `+0.0` are different results. Two assumptions are stated and not proved: the C++ compiler rounds decimal literals to nearest even, and the host evaluates `float` in `float`. An observed float that may be NaN makes the answer `unknown`; `assume="x==x"` excludes NaN. |
| Loops | A loop is unrolled up to 16 iterations, and Z3 must show that no allowed input reaches a seventeenth, or the answer is `unknown`. An extent is symbolic, so a pass over `0..n` needs a precondition that bounds `n`. |
| Order | A `try` is modelled only as the whole right-hand side of a binding, an assignment, a return or a statement; anywhere else it is `unknown`. |

## Comparing whole modules

`cairn verify --all` compares every function and public type of two complete sources. Anything missing, extra, unsupported or undecided prevents `smt-module-equivalent`, and one uncovered function keeps the module incomplete. `--assume` gives one function a precondition, and it can be repeated:

```sh
python3 bin/cairn verify reference.cairn candidate.cairn --all --assume 'total=n<=4'
```

A restricted entry is equivalent only where its precondition holds, and the receipt records every precondition. A precondition that allows no input is `invalid-domain`, and one that names no declared function is refused. Inputs are limited to 64000 bytes and 128 functions, with a soft solver budget of 30 seconds for the whole module. `examples/proof_scope/mixed.cairn` is the negative fixture that must stay incomplete even against itself.

## The Lean project

`proofs/` is a Lean 4 project with no dependencies, not even Mathlib, pinned in `proofs/lean-toolchain`. `lake build` checks it from scratch in a few seconds: 21 modules in 6.3 s in the 1.1.0 record ([evidence/v1_1/lean](../evidence/v1_1/lean/README.md)). In that record [the audit](#pinned-versions-and-the-audit) found no axiom but `propext` and `Quot.sound`.

| File | Contents |
| --- | --- |
| `Cairn/Affine.lean` | `Form`, `Form.eval`, `Rule`, `Certificate`, the computable `check`, and `check_sound`. |
| `Cairn/CollectorCertificates.lean` | Generated by `tools/checks/export_lean_certificates.py`. The 17 obligations and their certificates as Lean data, `all_checked`, and one corollary per obligation. |
| `Cairn/Collector.lean` | The executable model of the loop, the invariant derived from the certificates, stores in bounds, stable selection and the bounds on each increment. |
| `Cairn/Places.lean` | What a borrow names: roots, bounds, valuations, `reaches`, the checker's overlap `ovl` (mirroring `compiler/check/places.py:overlaps`), the real overlap `meets`, and the bridge `ovl_sound`. |
| `Cairn/Ownership/` | The ownership and lease model, one file per subject, imported by `Ownership.lean`: `Syntax`, `Lanes`, `Checker` (the executable `accepts`), `Weakening` (`Checks`, acceptance that may forget), `Machine` (the machine that takes one small step at a time, and `Reach`), `Heap`, `Invariant`, `Preservation` (one lemma per statement, assembled in `Ok_succ`), `Soundness` (the `accepted_*` theorems and progress) and `Regress` (the pinned programs and the fault witnesses). |
| `Cairn/Region.lean` | The lane pool's region protocol and the three region theorems. |
| `Cairn/Facts.lean` | The guard-elision rule of `compiler/check/facts.py`: atoms, facts, the Bellman-Ford search, the five decisions lowering acts on, and their soundness. |
| `Cairn/Layout.lean` | The layout rules of `compiler/device/layout_algebra.py` as their definitions, what a layout that passes promises, and one example of each refusal. |
| `Cairn/Cooperative.lean` | The phase rule of `compiler/cooperative/phases.py`: steps, phases, interleavings, the executable `accepts`, the three theorems, and the reduced source language (`E`, `C`, `S`) that `program` runs for every thread. |
| `Cairn/Audit.lean` | `#print axioms` for every headline theorem, and the ownership regression line. |

The certificate module is generated from the Python rules, and `tools/checks/export_lean_certificates.py --check` fails when the two drift apart. The receipt reports `lean_verified: true` only while the live bundle hashes to the one Lean checked. None of the proofs says anything about the parser, the type checker, placement, effects, the C++ compiler or the machine.

### Pinned versions and the audit

| Component | Version |
| --- | --- |
| Lean | 4.34.0 (`leanprover/lean4:v4.34.0`) |
| Dependencies | none (`lake-manifest.json` lists no packages; no Mathlib) |

`Cairn/Audit.lean` runs `#print axioms` on every headline declaration, and in the 1.1.0 record each one depends on `[propext, Quot.sound]` at most. There is no `sorry`, `sorryAx`, `native_decide`, `Lean.ofReduceBool`, `Lean.trustCompiler`, `axiom`, `unsafe` or `implemented_by`, and `tests/verification/test_lean_proofs.py` enforces that against the sources and the audit output. The test also admits `Classical.choice`, except in the ownership theorems, which must not use it. `tools/release/collect_lean_evidence.py` records a build from scratch and the audit under `evidence/<release>/lean/`; the 1.1.0 record audited 112 declarations.

The receipt's `lean_verified` field covers the certificate bundle alone, and [roadmap.md](roadmap.md#proof) lists the formal steps that remain.

## The collector certificates and the loop model

`compact` keeps the elements a predicate selects, in order. It lowers to a loop whose one store has no bounds check at run time:

```c
k = 0;
for (i = 0; i < n; ++i) { if (pred(i)) { out[k] = proj(i); ++k; } }
used = k;
```

`out` has capacity exactly `n`, and `pred` and `proj` never read `out`. The unchecked store rests on the cursor invariant `0 <= k <= i <= n <= M`, where `k` counts outputs, `i` counts inputs visited, `n` is the capacity and `M` is the largest value a cursor can hold. While `i < n`, the store at `k` is in range and both increments fit.

An affine form is a constant plus a sum of variables, each times an integer. `src/cairn/verify/linear_certificates.py` writes each affine form as five integer coefficients over `(1,k,i,n,M)`, meaning "this form is nonnegative". A certificate gives nonnegative multipliers c_j and c_0, and the checker accepts only when `g = c_0 + sum_j c_j * a_j` holds coefficient by coefficient, so `g` is nonnegative wherever the premises `a_j` are. Seventeen obligations cover initialization, stores, both increments, keeping the invariant across an emit and a skip, and the output count; some are trivial identities. The compiler checks the bundle before every emission, a corrupted bundle blocks compilation, and the receipt pins the rules and the checker by SHA-256.

Lean adds three results. The first is `Cairn.check_sound`: if the checker accepts a rule, its conclusion holds for every integer assignment that satisfies its assumptions.

```lean
theorem Cairn.check_sound {r : Rule} {c : Certificate} (h : check r c = true) :
    ∀ K I N M : Int, (∀ a ∈ r.assumptions, 0 ≤ a.eval K I N M) →
      0 ≤ r.conclusion.eval K I N M
```

The second is `Cairn.Collector.all_checked`: all seventeen certificates pass that checker inside Lean, by `decide`. Each transition theorem over `Inv K I N M` applies its certified obligation and never works the arithmetic out again:

```lean
theorem Inv.init {N M : Int} (hn : 0 ≤ N) (hm : N ≤ M) : Inv 0 0 N M   -- initial.*
theorem Inv.emit  (h : Inv K I N M) (hlt : I < N) : Inv (K + 1) (I + 1) N M -- emit.invariant.0-3
theorem Inv.skip  (h : Inv K I N M) (hlt : I < N) : Inv K (I + 1) N M      -- skip.invariant.0-3
theorem Inv.store_nonneg         (h : Inv K I N M) (hlt : I < N) : 0 ≤ K   -- store.nonnegative
theorem Inv.store_lt_capacity    (h : Inv K I N M) (hlt : I < N) : K < N   -- store.strictly_below_capacity
theorem Inv.emit_increment_fits  (h : Inv K I N M) (hlt : I < N) : K + 1 ≤ M -- emit.cursor_increment_fits
theorem Inv.step_increment_fits  (h : Inv K I N M) (hlt : I < N) : I + 1 ≤ M -- step.input_increment_fits
theorem Inv.exit_le_capacity     (h : Inv K I N M) : K ≤ N                 -- exit.output_count_bounded
```

The third concerns an executable model of the loop. Every store is in bounds with no guard, both increments stay within `M`, and the result is stable selection:

```lean
theorem store_index_lt_capacity (pred : α → Bool) (proj : α → β)
    (xs : List α) (out : List β) (hcap : out.length = xs.length)
    (pre : List α) (x : α) (post : List α) (hsplit : xs = pre ++ x :: post) :
    (run pred proj pre ⟨out, 0, 0⟩).k < out.length

theorem increments_fit (pred : α → Bool) (proj : α → β)
    (xs : List α) (out : List β) (hcap : out.length = xs.length)
    (M : Int) (hM : (out.length : Int) ≤ M)
    (pre : List α) (x : α) (post : List α) (hsplit : xs = pre ++ x :: post) :
    ((run pred proj pre ⟨out, 0, 0⟩).k : Int) + 1 ≤ M
    ∧ ((run pred proj pre ⟨out, 0, 0⟩).i : Int) + 1 ≤ M

theorem collect_spec (pred : α → Bool) (proj : α → β) (xs : List α) (out : List β)
    (hcap : out.length = xs.length) :
    (collect pred proj xs out).k = (xs.filter pred).length
    ∧ (collect pred proj xs out).k ≤ out.length
    ∧ (collect pred proj xs out).i = xs.length
    ∧ (collect pred proj xs out).out.length = out.length
    ∧ (collect pred proj xs out).out.take (collect pred proj xs out).k
        = (xs.filter pred).map proj
    ∧ (collect pred proj xs out).out.drop (collect pred proj xs out).k
        = out.drop (collect pred proj xs out).k
```

`store_index_lt_buffer_length` states the bound against the live buffer, so `List.set`, which would silently drop a write out of range, never drops one.

Three things are trusted: Python's integer arithmetic, the checker's implementation, and, by review only, that the compiler's cursor operations are these transitions. The certificates justify this one store. They say nothing about aliasing, lifetimes, initialization or the rest of the compiler. Every other guard the compiler leaves out rests on [the guard-elision rule](#the-guard-elision-rule).

## Ownership and leases in Lean

`Cairn/Places.lean` and `Cairn/Ownership/` model the ownership and lease rules as a small language of their own, a calculus. The model has three parts: a checker Lean can run, a machine that runs a program one small step at a time with its threads' steps in any order and with explicit error states, and theorems of safety and progress. It is written by hand beside `src/cairn/compiler/check/checking.py`. It shows that the rules are safe as a set, and [the differential harness](#the-differential-harness) tests whether `checking.py` follows them.

A lease is what a running task holds: the borrows it was handed, which nothing else may touch in a conflicting way until the task is waited for. An interleaving is one order in which the threads' steps run, and a valuation is one choice of values for the bounds. The theorems cover every interleaving and every valuation.

### Locals and places

A local is the unit of ownership. A place is what one borrow names, and the model has one constructor for each shape `checking.py` produces:

| Lean | Source | What it is |
| --- | --- | --- |
| `whole x` | `d` | the owner itself, header and elements: what an `rw<Buf[T]>` parameter lends, and the only borrow through which a task can replace the cell (`swap`) |
| `hdr x` | the `len(d)` read | the header alone: the length and the identity of the cell (`leased(..., elements=False)`) |
| `elems x` | `d[]` | every element: what an array view `rw<T>[n]` of a whole owner lends |
| `part x lo hi` | `d[lo..hi]` | those elements |

Each place has a root, a local plus a field path, so `r.xs[lo..hi]` is a part at the root `r.xs`. A bound is an integer literal or an immutable natural number.

A single element `a[i]`, a part of a part, and a part with a bound that can change are all modelled conservatively as `elems`: each overlaps every place of its base and adds no `lo <= hi` fact. The regression checks these classifications against `checking.py` on its named programs (`tests/soundness/test_soundness.py`).

### Disjointness, decided twice

The checker decides overlap from the text, as `compiler/check/places.py:overlaps` does. Different locals, distinct fields of one record, and a header and its elements never overlap, which is why `len(d)` stays readable while `d`'s elements are lent. Two parts of one root are disjoint exactly when one visibly ends at or before the other begins, through a chain of the guarded `lo <= hi` facts of the parts in play (`reaches`). The machine decides overlap from the values of the bounds, and `Ownership.ovl_sound` is the bridge: disjointness the checker sees in the text implies real disjointness.

`leased` chains through the parts live tasks hold, and `disjoint` through the parts of one argument list. So one call handed `d[0..a]` and `d[b..n]` is refused even while `d[a..b]` is lent, and spawning the two ends one at a time is accepted. Both checkers can refuse a safe program, and neither accepts an unsafe one.

### The guard, and the trap

A part is formed by `cr::part`, which traps unless `lo <= hi`, and that is the only guard the model has. The machine runs it on the spawner's thread before the call or task starts. A failed guard is a trap: a defined abort of the whole state of the machine, which is neither a fault nor a race. That makes it safe to accept `d[6..3]` as the part that orders `d[0..6]` before `d[3..9]`: the program aborts at its spawn before either task exists.

### Syntax, checker and machine

A program is one scope of declared locals and a body of statements: `alloc`, `mkScalar`, `copy`, `move`, `drop`, `call` with a list of `(place, ro|rw)` borrows, `spawn` and `wait`, `ite` with both branches, `parallel`, and the group statements `group`, `submit` and `collect`. A borrow exists only as an entry in one argument list, so borrows are second class by construction.

`Ownership.accepts` is a checker Lean can run, and it carries the state `checking.py` carries: which locals hold scalars or owners, which are dead, which groups are live, and which borrows each ticket or group holds. It refuses a use of a dead local, a copy of an owner, a move of anything leased, a conflicting access to what a live task holds, overlapping `rw` arguments in one call, a group used after `wait`, branches that disagree about live tickets or groups, and a live ticket or group at the end of the scope.

The machine runs a program over a heap, one small step at a time. It checks nothing on purpose, so that programs the checker refuses can reach a fault. Its error states are `UseAfterMove`, `UseAfterFree`, `DoubleFree`, `Leak`, `Race`, `AliasedArgs` and `DeadGroup`; `Trap` is not one of them. A task's body is reduced to its footprint, the places it may touch: while its ticket is live it may touch any place it was lent, in that mode, between any two steps of the spawner. The machine does not evaluate the condition of an `if`, so both branches are always reachable.

### Parallel regions

A region's body is a list of lane accesses, as `compiler/check/concurrency.py:region` records them: at the lane's own index, inside the lane's block of stride `S`, at any other index (every element), the place itself, or `len(x)`. The rule is the one in `compiler/check/concurrency.py`: whatever any lane writes is touched only inside each lane's own block, with one stride per local. A region beside a task that holds any part of the same array is refused, conservatively.

`lanesOf n body` builds one thread per index, and lanes step exactly as tasks do, so one `Pairwise` statement covers two tasks, a task and a lane, and two lanes. The spawner is blocked while a region runs, which `Region.lean` proves the pool does.

### Task groups

A group is a name many tasks share. `collect g` joins one finished task, and which one is unknown, so the machine has one next state for each task that might have finished, and the checker keeps every lease until `wait g`. After a branch, a group holds what either path lent it, and a part keeps its bounds only if both paths formed it, since its guard ran only where it was formed. `tests/soundness/test_groups.py` has the programs that raced before this rule. A join claims more than either path, so preservation, the lemma that every step keeps the invariant, is proved for acceptance that may forget between statements (`Checks`, `joinOf_le`, `Sync.weaken`).

### The lane pool

`Cairn/Region.lean` models the lane pool behind `cr::par::run`. A wide region is cut into consecutive homes, one for each lane it can use, and each home has a counter from which lanes claim chunks. Every lane goes through the homes in an order of its own, and a worker starts on the same home in every region, so it runs the same indices from its own cache before it helps with the others. The thread that starts the region takes every home, unlinks the region when it has been through them all, and returns once no worker is inside.

The theorems hold under every interleaving of any number of workers, with any claim size, any cut into consecutive homes and any order a worker takes them in. `runs_once` says every index below `n` has run exactly once when the starter returns, `quiet_when_back` says no worker is inside, and `finishes` says the region returns without waiting for a worker to arrive.

The model is sequentially consistent: all threads see every step in one order. The C++ bumps each counter with a relaxed `fetch_add` and orders a leaving worker against the waiting starter with sequentially consistent operations. That the header performs the modelled steps rests on review, with no proof, and `tests/runtime/parallel_runtime.cpp` checks that its cut is the one the model assumes. A region below `lanes::CUTOFF`, or in a process with one lane, is the plain loop on the thread that starts it.

### The theorems

Every statement below is proved in `proofs/Cairn/`, audited in `Cairn/Audit.lean` and required by `tests/verification/test_lean_proofs.py`. `Reach ρ p.scope (Cfg.start p) cfg` means that the machine can reach the state `cfg` in some interleaving when the bounds take the values `ρ` gives, and every valuation is covered.

```lean
theorem reaches_sound {ρ : Valuation} {facts : List Fact} (hf : FactsTrue ρ facts)
    {x goal : Bound} (h : reaches facts x goal = true) : x.eval ρ ≤ goal.eval ρ

theorem ovl_sound {ρ : Valuation} {inPlay : List Place}
    (hg : ∀ p ∈ inPlay, p.guard ρ = true) {r s : Place}
    (h : ovl inPlay r s = false) : meets ρ r s = false

theorem accepted_no_fault {p : Program} (hp : accepts p = true) {ρ : Valuation} {cfg : Cfg}
    (hr : Reach ρ p.scope (Cfg.start p) cfg) (e : Err) : cfg ≠ Cfg.err e

theorem accepted_threads_disjoint {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {code : List Stmt} {tasks lanes : List Task} {st : State}
    (hr : Reach ρ p.scope (Cfg.start p) (.run code tasks lanes st)) :
    (tasks ++ lanes).Pairwise (NoRacePair ρ)

theorem lanesOf_pairwise (ρ : Valuation) : ∀ (n : Nat) {body : List Touch},
    laneRule body = true → (lanesOf n body).Pairwise (NoRacePair ρ)

theorem accepted_frees_each_allocation_once {p : Program} (hp : accepts p = true)
    {ρ : Valuation} {st : State} (hr : Reach ρ p.scope (Cfg.start p) (Cfg.done st)) :
    (∀ a, st.live a = false) ∧ (∀ a, a < st.next → st.frees a = 1)

def Progress : Prop :=
  ∀ (p : Program) (ρ : Valuation), accepts p = true →
    ∀ cfg, Reach ρ p.scope (Cfg.start p) cfg →
      (∃ st, cfg = Cfg.done st) ∨ cfg = Cfg.trap ∨ succ ρ p.scope cfg ≠ []

theorem accepted_progress : Progress
```

`accepted_no_use_after_move`, `accepted_no_use_after_free`, `accepted_no_double_free`, `accepted_race_free`, `accepted_no_leaked_ticket`, `accepted_no_use_after_wait` and `accepted_no_aliased_args` are `accepted_no_fault` at each error. They rest on `Ok_succ`, the preservation lemma. The theorem about releases covers normal termination only, and nothing is claimed about the cells a run that trapped leaves behind. `accepted_progress` says the only states where the machine is stuck are the errors, which `accepted_no_fault` excludes.

### The regression, and that the faults are reachable

`ownership_regression` checks that the Lean encodings of the programs pinned in `tests/soundness/` are classified as the Python checker classifies them. Each refused or accepted program is written out with its CAIRN source in `Cairn/Ownership/Regress.lean`: moves, leases, overlapping parts, fields, groups and lane blocks. The build prints `ownership-regression: pass`, and the Python test asserts on that line.

The safety theorems would say nothing if the machine could never fault, so sixteen witnesses drive it to `Race`, `AliasedArgs`, `DoubleFree`, `UseAfterMove`, `Leak` and `DeadGroup` for programs the checker refuses, and `witnesses_are_rejected` and `group_witnesses_are_rejected` confirm the refusals. `UseAfterFree` is the one error with no witness. `backwardsPart_traps` shows an accepted program reaching `Trap` at the guard of `d[6..3]` before any race.

### The differential harness

A differential test gives two implementations of one rule the same generated inputs and requires the same answer. `tools/checks/differential_ownership.py` is the one mechanical link between the two ownership checkers. A generator with a fixed seed renders each program twice, as CAIRN source and as a Lean `Program`, and the Python checker's verdict and Lean's `accepts` must match on every program. `--fragment` prints what the generator covers: locals, moves, calls, tasks, branches, regions with lane blocks, and task groups. Record fields, loops, closures, placement, `reduce`, `compact`, `take` and `swap` are outside it. The generated programs are checked, never built or run.

```sh
python tools/checks/differential_ownership.py --count 2000 --seed 7
CAIRN_DIFFERENTIAL_N=5000 python -m pytest -q tests/verification/test_differential_ownership.py
```

`make test` runs 40 programs and `make lean` 200, and the test plants one wrong verdict that the harness must report. Twenty thousand programs agreed on the run recorded in `evidence/v1_0/gates_2026_09_22/lean/differential.json`. Without `lake` the harness exits with status 3, which is not a pass.

### What this does not cover

| Left out | Where it stands |
| --- | --- |
| That `checking.py` implements this model | The Lean checker is written by hand; the differential harness above compares the two. |
| The part guard running on the spawning thread before a task starts | Asserted by `codegen.py` and `cairn_owners.hpp`, and pinned for `spawn` and `spawn ... into g` by `tests/soundness/test_concurrency.py::test_a_part_is_guarded_by_the_spawner_before_its_task_exists`. |
| The `elems` mapping for every program | Checked only on the programs the regression names. |
| Where a field read is charged | `compiler/check/expressions.py:e_field` charges `leased(box.a, "ro", elements=False)` at the outermost field of the path; the model writes the read as an explicit `call [(hdr box.a, ro)]`. |
| A region beyond its accesses | `lane:f`, `E-PARALLEL-CALL`, placement, `reduce`, `compact`, queued device work and the values lanes compute. A lane index that is neither the binder nor inside a placed block counts as every element, so `parallel i in n { x[i + 1] = 0; }`, which cannot race, is a race here, and the checker refuses it too. |
| The pool's memory orders | Review only, as [the lane pool](#the-lane-pool) says. |
| Closures | Captures are not modelled. |
| Device streams | The `after` exemption is absent, so the model would refuse those programs. |
| A group submitted to inside a loop | The rule that nothing the body touches may conflict with what an earlier iteration lent is tested. A group lent to a callee is refused by its signature (`E-PINNED`). |
| Declared field extents | A typing rule of `checking.py`, held by `E-EXTENT-FIELD` and tested natively. |
| The rest of the language | Linear values other than tickets and groups, `defer`, `take` and `swap` as statements, loops, `return`, traits, generics, atomics, mutexes, I/O rings, effects and the foreign boundary. |
| Callee bodies | A call is its footprint, so `AliasedArgs` is detected at a call and never caused inside one. |
| Values | The race result is about conflicting access; it says nothing about whether results are deterministic. |
| The machine | No native memory model, allocator, C++ or thread semantics. |

## The guard-elision rule

A guard is a check the emitted code makes before an operation, such as an index bound or an overflow test, and a failed guard aborts the program. Lowering leaves a guard out where `compiler/check/facts.py` shows it cannot fail, and `proofs/Cairn/Facts.lean` proves the rule. A fact is `x - y <= k` between two atoms: zero, an immutable `usize`, or a multiple of a stride. A Bellman-Ford search finds the tightest `k` the facts give. Five decisions read the result: an index below its extent, a `+` that stays at most the largest `usize`, a `-` that stays at least zero, a value at most a constant (a shift count or a narrowing), and a part inside its view. `distance_sound`, `bounds_sound`, `index_sound`, `add_sound`, `sub_sound`, `atMostConst_sound` and `part_sound` prove them under every valuation that makes the facts true.

The Lean functions translate the Python ones line by line, and `tools/checks/differential_facts.py` asks both the same generated questions. Twenty thousand inputs agreed on the run in `evidence/v1_0/gates_2026_09_22/lean/facts_differential.json`, and an off-by-one error planted in the Python is caught within a few hundred inputs.

Whether the facts in scope are true where they are used is checked at every site, and nothing proves it. `src/cairn/verify/elision.py` reads each function on its own, in the order it runs, and never imports `facts.py`. It requires every fact that a guard lowering left out cites to come from an origin in force at that site (a loop, lane or collector binder, an immutable `let`, a collector's result, a condition, the left side of `&&` or `||`, a collector's predicate, an early exit) and to name only values that cannot have changed. It then decides the guard again from those facts alone. The emitter keeps every guard whose proof the audit refuses, and the receipt counts them under `refused_discharges`.

Across the library, the examples and the docs, the audit refuses nothing. `tests/soundness/test_elision.py` requires a proof tampered with in any one way to be refused, and an off-by-one error planted in `facts.py` to reach no emitted program. `tools/checks/differential_guards.py` builds generated programs as emitted and with every guard kept, under clang++ with the address and undefined sanitizers and under g++, and requires the same value or the same trap on every input: 1,821 functions and 174,816 cases per compiler agreed (`evidence/v1_0/guards/`). The audit's own rules are written by hand and are not in Lean, and the model has no `usize(x)` of a narrower integer.

## The layout rule

A layout says where each element of a tile lives. A spread says which participant, a thread or a lane, holds each element, and a storage layout gives each element an offset in memory. A declared layout is held to two rules, and `proofs/Cairn/Layout.lean` states them as their definitions: every element of a spread has exactly one (participant, value) holder, and every element of a storage layout has its own offset.

`ok_one_holder` and `ok_covers` say that a phase in which every participant writes each of its values writes every element once, `ok_one_writer` that no two pairs write one element, and `ok_no_collision` that with a tile that passes its own rule no two pairs write one offset. `gappy_gap`, `doubled_overlap` and `cleared_clash` show that each refusal happens. `accumulator_share_ok` and `accumulator_share_is_the_isa` say that the spread a program declares for the lanes of an `mma.sync` accumulator is the PTX ISA's formula and gives each element one lane, which is the share `mma_store` writes by.

`compiler/device/layout_algebra.py` counts holders in one pass and finds a shared offset with a table, where the Lean counts element by element. `tools/checks/differential_layouts.py` asks both about generated layouts, among them the ones `spread`, `transpose`, `swizzle` and `inverse` make and the reads `stage` places, and requires the same verdict: a coordinate outside the tile, the first element held twice, the first left to nobody, and the first two elements that share an offset. Three hundred agree in `make test`, and a planted slip that lets one participant hold an element twice is reported. The code that uses a layout is tested, and nothing proves it.

## The phase rule

`proofs/Cairn/Cooperative.lean` models one block of a [cooperative region](devices.md#cooperative-regions), the threads of one GPU block, which share memory and meet at barriers. Every thread runs the same phases, the stretches of code between two barriers. Within a phase the threads' steps, loads into registers and stores of values computed from them, interleave in any order. `no_race` says the next steps of two threads of an accepted phase never clash. `deterministic` says any two interleavings that run every thread to its end leave the same memory and registers, because every element holds what its one writer would have put there running alone, or what it held before. `block_deterministic` carries that through a block of phases with a barrier between each two.

The model's `program` runs a reduced source language for every thread (element reads and writes at indexes built from the thread's number, loop counters and literals, `if` on the thread's number, loops of known count, barriers) and applies `accepts` to each phase. `tools/checks/differential_cooperative.py` renders generated regions as CAIRN and as Lean terms and requires `compiler/cooperative/phases.py` to accept exactly the regions `program` accepts. A slip planted in `phases.py` must be reported. That is agreement on samples, and it does not prove that the Python is the model.

The other rules of a cooperative region have no Lean model and are finite-tested only: the global rule of `footprints.py` (`E-COOP-GLOBAL`), pipeline stages (`E-STAGE-UNREADY`, `E-STAGE-BUSY`, `E-STAGE-LOOP`), which threads reach a barrier or a warp operation together (`E-COOP-BARRIER`, `E-COOP-WARP`), atomic updates as a class apart from plain accesses (`E-ATOMIC-MIXED`), a region's finish, and the rule that lets a shared array go unzeroed (`E-COOP-UNWRITTEN`). The model's steps are plain loads and stores, so an atomic update is outside it, and its source declares every array zeroed. These rules rest on the refusal tests in `tests/soundness/test_cooperative.py`, `test_pipelines.py`, `test_reach.py`, `test_atomics.py`, `test_finish.py` and `test_written.py`, and on thread sanitizer runs of the host lowering. Those runs cannot see a fault only the device has, such as a read of a stage whose `cp.async` copy is still in flight.

Work toward models of these rules, and the review, found accepted programs that raced, hung or read an unfinished stage. Each is now refused, with a test:

| Code | What was accepted |
|---|---|
| `E-COOP-GLOBAL` | a condition bound over digits counted both ways; a loop range that moves with another digit |
| `E-COOP-BARRIER`, `E-COOP-WARP` | a value made different for each thread through an `rw` borrow, a closure, an element, `swap`, an atomic, typed `asm` or a method's receiver; a warp operation on the right of `&&` or `\|\|`; a `while` condition; a fixed point that needed more than eight rounds |
| `E-COOP-UNDECIDED` | a closure naming a shared array, a pipeline or an outside array |
| `E-STAGE-UNREADY`, `E-STAGE-LOOP`, `E-PLACEMENT` | a stage used whole or in part before its wait; a `break` out of a fill loop; a fill from memory the region cannot copy from |

## What each feature has shown

Each row below is one feature, and each column is one claim about it: whether it is implemented, which targets its code compiles for, whether it has run on a CPU and on a GPU, which sanitizers watched it run, and whether its speed was measured. A cell is `yes`, `partial` (it says which part holds), `no`, `unknown` or `n/a`, followed by what it rests on. `unknown` means nobody has checked, and it is never success. A GPU run or a measurement counts only with an executed record under `evidence/`. A device test that skips without a GPU is no such record.

A host row's claims are for x86-64 Linux under clang++ and g++, the reference machine the records were taken on, unless the row names another platform. Hosted AArch64 last ran the suite on a GH200 at v0.8.2, before most of these features existed, and a CI job on an AArch64 host counts here once its run is recorded. On a device, "compiles" means that nvcc and ptxas accepted the code for that target, and nothing more.

The rows are data, in [project/capability_matrix.json](project/capability_matrix.json). To add a feature, add a row there, run `python3 tools/release/capability_matrix.py` (which `make docs` runs), and commit both. `tests/tooling/test_capability_matrix.py` fails while this table differs from the data, while a row names a record that does not exist, while a host row makes a GPU claim, or while a GPU run or a measurement says `yes` or `partial` without a record under `evidence/`.

<!-- generated from project/capability_matrix.json by tools/release/capability_matrix.py; edit the data -->

### On the host

| Feature | Implemented | Compiles for | Ran on a CPU | Ran on a GPU | Sanitizers | Measured | Records |
|---|---|---|---|---|---|---|---|
| [Owners, moves, linear values and `defer`](memory.md#owners-and-moves) | yes | yes: x86-64, clang++ and g++ | yes: x86-64, the suite | n/a | yes: address and leak | no | [test_memory.py](../tests/language/test_memory.py), [test_soundness.py](../tests/soundness/test_soundness.py) |
| [Guards, and the guards lowering leaves out](verification.md#the-guard-elision-rule) | yes | yes: x86-64, clang++ and g++ | yes: 174,816 cases a compiler against a build that keeps every guard | n/a | yes: address and undefined under clang++ | yes: guarded and matched baselines in the preregistered CPU suite | [v1_0/guards/NOTES.md](../evidence/v1_0/guards/NOTES.md), [v1_0/lowering/NOTES.md](../evidence/v1_0/lowering/NOTES.md), [v1_0/bench](../evidence/v1_0/bench/README.md), [test_elision.py](../tests/soundness/test_elision.py) |
| [Tasks, leases and task groups](concurrency.md#tasks-and-leases) | yes | yes: x86-64, clang++ and g++ | yes: x86-64, the suite | n/a | yes: thread, and address and undefined with leak detection, under both compilers | yes: reused task threads against a thread a task | [test_groups.py](../tests/soundness/test_groups.py), [test_concurrency.py](../tests/soundness/test_concurrency.py), [v1_0/runtime](../evidence/v1_0/runtime/README.md) |
| [Atomics and mutexes](concurrency.md#atomics-and-mutexes) | yes | yes: x86-64, clang++ and g++ | yes: x86-64, the suite | n/a | yes: thread under clang++ | no | [test_concurrency.py](../tests/soundness/test_concurrency.py) |
| [I/O rings over io_uring](concurrency.md#io-rings) | yes | yes: x86-64 Linux, clang++ and g++ | yes: x86-64 Linux, the suite | n/a | yes: address and undefined under clang++, thread for a ring lent to a task | no | [test_rings.py](../tests/soundness/test_rings.py) |
| [Parallel regions on the host lane pool](concurrency.md#parallel-regions) | yes | yes: x86-64, clang++ and g++ | yes: x86-64, the suite | n/a | yes: thread | yes: the preregistered CPU suite, and where a region starts to pay off on a GH200 at v0.8.2 | [test_native_runtime.py](../tests/runtime/test_native_runtime.py), [v1_0/bench](../evidence/v1_0/bench/README.md), [v0_8_2/host_regions](../evidence/v0_8_2/host_regions/README.md) |
| [`reduce`, `scan` and `compact` on the host](concurrency.md#reduce-and-compact) | yes | yes: x86-64, clang++ and g++ | yes: x86-64, the suite | n/a | yes: address, undefined and thread | yes: the scan and the radix sort, and the parallel reduce in the preregistered suite | [test_scan.py](../tests/soundness/test_scan.py), [v1_0/scan](../evidence/v1_0/scan/README.md), [v1_0/bench](../evidence/v1_0/bench/README.md) |
| [Host plans: `grain`, `lanes` and `fuse`](concurrency.md#plans) | yes | yes: x86-64, clang++ and g++ | yes: x86-64, the suite | n/a | yes: thread | yes: fused regions against the regions they were written as | [test_fusion.py](../tests/soundness/test_fusion.py), [test_plans.py](../tests/soundness/test_plans.py), [v1_0/fusion](../evidence/v1_0/fusion/README.md) |
| [Storage floats and `quantize`](numerics.md#storage-floats) | yes | yes: x86-64, clang++ and g++ | yes: x86-64, the suite | n/a | yes: address and undefined under clang++ | no | [test_storage_floats.py](../tests/language/test_storage_floats.py) |
| [`derive grad`](numerics.md#gradients) | yes | yes: x86-64, clang++ and g++ | yes: x86-64, the suite | n/a | yes: address, undefined and thread under clang++ | no | [test_gradients.py](../tests/language/test_gradients.py) |
| [Typed assembly for x86-64 and AArch64](memory.md#layout-and-the-machine) | yes | partial: x86-64 under clang++ and g++; AArch64 is checked and lowered and has not been built | partial: x86-64, each statement against its instruction's definition; AArch64 has not run | n/a | yes: address and undefined under both compilers, on x86-64 | no | [v1_0/foreign](../evidence/v1_0/foreign/README.md), [v1_0/RUN_NOTES.md](../evidence/v1_0/RUN_NOTES.md), [test_assembly.py](../tests/language/test_assembly.py) |
| [Foreign C++ implementations](memory.md#foreign-implementations) | yes | yes: x86-64, clang++ and g++ | yes: validated on 256 boundary cases under both compilers | n/a | yes: address and undefined under both compilers | no | [v1_0/foreign](../evidence/v1_0/foreign/README.md), [test_foreign.py](../tests/projects/test_foreign.py) |
| [Alternative implementations and `cairn validate`](abstractions.md#implementations) | yes | yes: x86-64, clang++ and g++ | yes: finite-tested against the reference on generated boundary cases | n/a | yes: address and undefined under both compilers | no | [v1_0/implementations](../evidence/v1_0/implementations/README.md), [test_implementations.py](../tests/language/test_implementations.py) |
| [`cairn tune`: the bounded search over plans and implementations](tools.md#cairn-tune) | yes | yes: x86-64, clang++, the host candidates `--measure` times | yes: x86-64, the suite and the searches evidence/v1_1/search records | n/a | n/a | yes: the search's own cost, its checks, compiles and wall time before and after lazy generation | [test_search.py](../tests/tooling/test_search.py), [test_search_budget.py](../tests/tooling/test_search_budget.py), [test_tune_objective.py](../tests/tooling/test_tune_objective.py), [v1_0/search](../evidence/v1_0/search/README.md), [v1_1/search](../evidence/v1_1/search/README.md) |
| [Libraries and their C header](tools.md#cairn-build---header) | yes | yes: x86-64 Linux, clang++ and g++ | yes: x86-64 Linux through ctypes; no other platform | n/a | yes: address and undefined | no | [test_interop.py](../tests/projects/test_interop.py) |
| [Freestanding AArch64 images](tools.md#the-freestanding-target) | yes | unknown: its tests build the image only on an AArch64 host, and the last was a GH200 at v0.8.2 | no: it last ran under QEMU on that GH200, in the v0.8.2 suite | n/a | n/a | no | [v0_8_2/summary.json](../evidence/v0_8_2/summary.json), [v0_8_0/embedded](../evidence/v0_8_0/embedded/README.md), [test_freestanding.py](../tests/projects/test_freestanding.py) |
| [Device programs emulated on host threads (`--emulate`)](devices.md#emulating-device-code-on-the-host) | yes | yes: x86-64, clang++ and g++, judged against a device target | yes: every device example `--emulate` accepts, under both compilers; a vendored CUDA kernel is refused with `E-EMULATE` | n/a | yes: address, undefined and leak; thread on cooperative regions | n/a: an emulated time measures host threads, never a device | [v1_1/emulation](../evidence/v1_1/emulation/README.md), [test_emulation.py](../tests/projects/test_emulation.py) |
| [One compile per source for the hosts, `cairn mcp` and `cairn lsp`](tools.md#cairn-mcp) | yes | n/a: the compiler's own tooling, which builds no program of its own | yes: every example emits the same C++ and receipt from a kept copy as from scratch | n/a | n/a | yes: MCP checks, edit sessions, and cycles of an edit and a check, before and after, on one loaded machine | [v1_1/workspace](../evidence/v1_1/workspace/README.md), [test_compilations.py](../tests/agent/test_compilations.py), [test_mcp.py](../tests/agent/test_mcp.py) |

### On a device

| Feature | Implemented | Compiles for | Ran on a CPU | Ran on a GPU | Sanitizers | Measured | Records |
|---|---|---|---|---|---|---|---|
| [Device regions, `reduce`, `scan` and `compact`](concurrency.md#parallel-regions) | yes | yes: sm_120 in the suite; sm_80, sm_89, sm_90a, sm_100a and sm_120a for the suite's kernels | yes: emulated, and on a host stand-in for the CUDA runtime | partial: `parallel` regions, the `reduce` collector, `reduce op out[k]` over device views, a device `scan` and a device `compact` ran on an RTX 5070 Ti, each result checked against the host, CUDA or the exact sum; a checked total that overflows has not trapped on a GPU | partial: the host sanitizers on emulated builds; Compute Sanitizer has not run | partial: regions and `reduce` against hand-written CUDA on an RTX 5070 Ti; `scan` and `compact` untimed | [v0_8_3/gpu/benchmark.json](../evidence/v0_8_3/gpu/benchmark.json), [v1_1/catalog](../evidence/v1_1/catalog/README.md), [v1_1/emulation](../evidence/v1_1/emulation/README.md), [v1_0/execution](../evidence/v1_0/execution/README.md), [v1_1/device_perf](../evidence/v1_1/device_perf/README.md), [v1_1/gpu](../evidence/v1_1/gpu/README.md) |
| [Guards in device lanes](devices.md#emulating-device-code-on-the-host) | yes | yes: sm_120 | yes: emulated, where a failing guard aborts with SIGABRT | partial: the checked multiply, add and subtract of every integer type on 1.6 million operand pairs on an RTX 5070 Ti, none trapping; a guard that fails has not run since v0.8.3 | partial: address on emulated builds reports a read past a device array; Compute Sanitizer has not run | partial: what checked index arithmetic adds to kernel time against unchecked CUDA | [test_emulation.py](../tests/projects/test_emulation.py), [v1_0/RUN_NOTES.md](../evidence/v1_0/RUN_NOTES.md), [v1_1/device_perf](../evidence/v1_1/device_perf/README.md) |
| [Transfers, queued device work and the execution context](devices.md#queued-device-work) | yes | yes: sm_120 | yes: on a host stand-in that counts every stream, allocation and wait, and emulated | yes: the execution context on a caller's stream, a held function's one wait, queued work ordered by tickets, and `cq_` entries called directly and replayed in a CUDA graph, on an RTX 5070 Ti | partial: thread and undefined on the host stand-in; Compute Sanitizer has not run | partial: the host waits a call makes and what each costs on an RTX 5070 Ti under WSL2 | [v1_0/execution](../evidence/v1_0/execution/README.md), [test_execution.py](../tests/runtime/test_execution.py), [v1_1/emulation](../evidence/v1_1/emulation/README.md), [v1_1/device_perf](../evidence/v1_1/device_perf/README.md), [v1_1/gpu](../evidence/v1_1/gpu/README.md) |
| [Device plans: `block`, `per_lane`, `unroll`, `vector` and `stage`](concurrency.md#plans) | yes | yes: sm_120 | partial: a `vector 4` and a `stage 1` region on the host stand-in | yes: every device plan, `vector` plan and `stage` plan of the suite against the unplanned launch on an RTX 5070 Ti | partial: thread and undefined on the host stand-in; Compute Sanitizer has not run | no: predicted only | [test_plans.py](../tests/soundness/test_plans.py), [test_staging.py](../tests/soundness/test_staging.py), [v1_0/execution](../evidence/v1_0/execution/README.md), [v1_1/gpu](../evidence/v1_1/gpu/README.md) |
| [Wide loads and stores: `load_wide[K]`, `store_wide` and `Cache` hints](devices.md#wide-loads-and-stores) | yes | yes: sm_120, to LDG.E.EF.128, STG.E.EF.128, LDG.E.128.CONSTANT, LDS.128 and STS.128 | yes: in host code, host lanes and host threads, and emulated, under both compilers | yes: `load_wide[4]` and `store_wide` with cache hints in lanes and a cooperative region on an RTX 5070 Ti, checked against plain loops, and in the sums of bench/device | partial: address and undefined under clang++, thread on host threads; Compute Sanitizer has not run | partial: that load in a sum of two passes against CUDA's `__ldcg` on an RTX 5070 Ti | [test_wide.py](../tests/soundness/test_wide.py), [v1_1/device_perf](../evidence/v1_1/device_perf/README.md), [v1_1/gpu](../evidence/v1_1/gpu/README.md) |
| [Atomic updates of one element in lanes and threads](concurrency.md#atomics-and-mutexes) | yes | yes: sm_120, to RED, ATOMG and ATOMS | yes: on host lanes and host threads under both compilers, and emulated | yes: the suite's atomic updates in lanes and cooperative threads on an RTX 5070 Ti, checked against plain loops | partial: thread, which reports an update made plain, and address and undefined under clang++; Compute Sanitizer has not run | no | [test_atomics.py](../tests/soundness/test_atomics.py), [v1_1/gpu](../evidence/v1_1/gpu/README.md) |
| [Cooperative regions: shared arrays, barriers and warp operations](devices.md#cooperative-regions) | yes | yes: sm_80, sm_89, sm_90a, sm_100a and sm_120a | yes: each block's threads as host threads at a barrier, under both compilers | yes: layer norm, transpose, stencil and sums of two passes on an RTX 5070 Ti, each result checked against CUDA, and warp votes checked against plain loops | partial: thread under both compilers, which reports a removed barrier; Compute Sanitizer has not run | yes: against hand-written CUDA of the same algorithms on an RTX 5070 Ti | [v1_0/cooperative](../evidence/v1_0/cooperative/README.md), [v1_1/catalog](../evidence/v1_1/catalog/README.md), [v1_0/predict_cooperative](../evidence/v1_0/predict_cooperative/README.md), [test_cooperative.py](../tests/soundness/test_cooperative.py), [test_reduction_example.py](../tests/projects/test_reduction_example.py), [v1_1/kernels](../evidence/v1_1/kernels/README.md), [test_votes.py](../tests/soundness/test_votes.py), [v1_1/device_perf](../evidence/v1_1/device_perf/README.md), [v1_1/gpu](../evidence/v1_1/gpu/README.md) |
| [A cooperative region's finish: `then threads t in T { }`](devices.md#cooperative-regions) | yes | yes: sm_120, one launch whose last block runs the finish | yes: one more team of host threads after the blocks, grids of 0 to 70 blocks, under both compilers, and emulated | yes: the suite's finish, `examples/reduction`'s sums in one launch, and a finish's `cq_` entry replayed in a CUDA graph beside direct launches, on an RTX 5070 Ti | partial: thread, which reports a finish that does not wait for the blocks; Compute Sanitizer has not run | no | [test_finish.py](../tests/soundness/test_finish.py), [v1_1/gpu](../evidence/v1_1/gpu/README.md) |
| [Shared arrays nobody zeroes](devices.md#cooperative-regions) | yes | yes: sm_120, zeroing none of them where a block starts | yes: on host threads under both compilers, and emulated, each block starting them filled with a pattern | yes: the suite's unzeroed shared arrays on an RTX 5070 Ti, checked against plain loops | partial: thread, and address and undefined under clang++; Compute Sanitizer has not run | no | [test_written.py](../tests/soundness/test_written.py), [v1_1/gpu](../evidence/v1_1/gpu/README.md) |
| [Pipeline stages](devices.md#cooperative-regions) | yes | yes: sm_120 with `cp.async`; sm_80, sm_89, sm_90a, sm_100a and sm_120a | yes: row sums at depth 2 and 3 on host threads, under both compilers | partial: `examples/cooperative`'s row sums at depth 2 and 3 on an RTX 5070 Ti, equal to their plain loops | partial: thread under both compilers; Compute Sanitizer has not run | no | [v1_0/cooperative](../evidence/v1_0/cooperative/README.md), [v1_1/catalog](../evidence/v1_1/catalog/README.md), [test_pipelines.py](../tests/soundness/test_pipelines.py), [v1_1/gpu](../evidence/v1_1/gpu/README.md) |
| [The tensor-core multiply and its fragments](numerics.md#tensor-core-fragments) | yes | yes: sm_80, sm_89, sm_90a, sm_100a and sm_120a | yes: two kernels on 16 shapes under both compilers, within the contract's bound | yes: the suite's tensor-core kernels and `examples/tensor`'s multiplies on an RTX 5070 Ti, within their numerical contract | partial: thread on host threads; Compute Sanitizer has not run | no | [v1_0/tensor](../evidence/v1_0/tensor/README.md), [v1_1/catalog](../evidence/v1_1/catalog/README.md), [test_tensor_kernels.py](../tests/soundness/test_tensor_kernels.py), [v1_1/gpu](../evidence/v1_1/gpu/README.md) |
| [Layouts and spreads in code](memory.md#layouts) | yes | yes: sm_120 | yes: the offsets of 120 random layouts against the checker's own enumeration, under both compilers | partial: the tensor-core tile kernels, which index shared memory through `T.at`, on an RTX 5070 Ti within their contract; a coordinate outside its layout has not trapped on a GPU | partial: undefined under both compilers on the host; Compute Sanitizer has not run | no | [v1_0/review_implementation_layer](../evidence/v1_0/review_implementation_layer/README.md), [v1_0/tensor](../evidence/v1_0/tensor/README.md), [test_layouts.py](../tests/language/test_layouts.py), [v1_1/gpu](../evidence/v1_1/gpu/README.md) |
| [Storage floats, gradients and asserts in a lane](numerics.md#storage-floats) | yes | yes: sm_120 | unknown: no emulated run of them is recorded | no | no: Compute Sanitizer has not run | no | [test_storage_floats.py](../tests/language/test_storage_floats.py), [test_gradients.py](../tests/language/test_gradients.py) |
| [Typed PTX](memory.md#layout-and-the-machine) | yes | yes: sm_120, with the SASS it names read back | no: `--emulate` refuses it | yes: 16-byte loads and `brev` in a `parallel` and a cooperative region on an RTX 5070 Ti, once PTX in a region's body launched | no | yes: a sum of two passes with 16-byte PTX loads against CUDA on an RTX 5070 Ti | [v1_0/foreign](../evidence/v1_0/foreign/README.md), [test_assembly.py](../tests/language/test_assembly.py), [v1_1/device_perf](../evidence/v1_1/device_perf/README.md) |
| [Foreign CUDA implementations and `launch`](memory.md#foreign-implementations) | yes | yes: sm_120 with every warning an error | no: `--emulate` refuses vendored CUDA | yes: a foreign CUDA implementation against its CAIRN reference on an RTX 5070 Ti | no: Compute Sanitizer runs only under make gpu | no | [v1_0/foreign](../evidence/v1_0/foreign/README.md), [test_foreign.py](../tests/projects/test_foreign.py), [v1_1/gpu](../evidence/v1_1/gpu/README.md) |
| [Device validation of an implementation](tools.md#cairn-validate) | yes | yes: the generated device tests build for sm_120 | yes: with `--emulate`, as finite-tested-emulated | no: Compute Sanitizer cannot instrument the device under WSL2, so every tool is `unavailable` on the reference machine | no: each Compute Sanitizer tool runs only under make gpu | n/a | [v1_0/foreign](../evidence/v1_0/foreign/README.md), [v1_1/emulation](../evidence/v1_1/emulation/README.md), [test_device_validation.py](../tests/verification/test_device_validation.py), [v1_1/gpu](../evidence/v1_1/gpu/README.md) |
| [`cairn tune` on device candidates](tools.md#cairn-tune) | yes | yes: sm_120, read by ptxas and cuobjdump, nothing launched | n/a | no: make tune-device has not run | n/a | partial: its compiles and the search's wall time, evidence/v1_1/search; no device time | [test_search.py](../tests/tooling/test_search.py), [test_search_budget.py](../tests/tooling/test_search_budget.py), [test_search_instances.py](../tests/tooling/test_search_instances.py), [v1_0/search](../evidence/v1_0/search/README.md), [v1_1/search](../evidence/v1_1/search/README.md) |
| [SOL-ExecBench solutions, and projects from its problems](devices.md#benchmark-submissions) | yes | yes: sm_100a, compiled and linked against torch 2.9.0+cu130 as SOL-ExecBench's build_ext.py builds a solution, and accepted by its pydantic models | yes: a solution with its views on the host, built by torch.utils.cpp_extension.load, equals the reference with CPU torch, and an emulated RMSNorm is within SOL-ExecBench's tolerance on nine workloads | no | no | no | [v1_1/harness](../evidence/v1_1/harness/README.md), [test_harness.py](../tests/projects/test_harness.py), [test_harness_torch.py](../tests/projects/test_harness_torch.py) |
| [GPU MODE submissions](devices.md#benchmark-submissions) | yes | yes: sm_100a through load_inline, compiled and linked against torch 2.9.0+cu130 | yes: a submission with its views on the host, built by load_inline, returns the reference's output with CPU torch | no | no | no | [v1_1/harness](../evidence/v1_1/harness/README.md), [test_harness.py](../tests/projects/test_harness.py), [test_harness_torch.py](../tests/projects/test_harness_torch.py) |
| [KernelBench `ModelNew`](devices.md#benchmark-submissions) | yes | yes: sm_100a through load_inline, compiled and linked against torch 2.9.0+cu130, and valid under KernelBench's static checker | yes: a ModelNew with its views on the host, built by load_inline, equals the problem's Model with CPU torch | no | no | no | [v1_1/harness](../evidence/v1_1/harness/README.md), [test_harness.py](../tests/projects/test_harness.py), [test_harness_torch.py](../tests/projects/test_harness_torch.py) |

<!-- end of the generated capability matrix -->
