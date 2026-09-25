# Verification

Nothing proves the whole compiler correct. Three mechanisms each establish something narrower: Lean 4 proofs about hand-written models, Z3 queries about a modeled fragment of the source, and executed tests. Accepted, typed, native-built, finite-tested, sanitizer-clean, SMT-equivalent, Lean-checked and benchmarked are separate claims, and `unknown` is none of them.

| Claim | Established by | Where | What it does not cover |
|---|---|---|---|
| The certificate checker is sound. | Lean, `check_sound` | `proofs/Cairn/Affine.lean` | That `linear_certificates.py` implements the Lean `check`; it is a reviewed transliteration. |
| The seventeen collector certificates pass that checker. | Lean kernel `decide`, `all_checked` | `proofs/Cairn/CollectorCertificates.lean`, generated | Anything about the emitted loop. |
| The collector loop model stores in bounds, keeps both cursors representable and selects stably. | Lean, `store_index_lt_capacity`, `increments_fit`, `collect_spec` | `proofs/Cairn/Collector.lean` | That the emitted C++ is this loop. The model uses `Int`/`Nat`, not machine words. |
| An accepted program of the ownership and lease calculus has no use-after-move, use-after-free, double free, leaked ticket or group, use of a group after `wait`, aliased argument or race, under every interleaving and every valuation; never gets stuck; and releases every cell exactly once on normal termination. | Lean, `accepted_no_fault` and the named faults, `accepted_threads_disjoint`, `accepted_frees_each_allocation_once`, `accepted_progress` | `proofs/Cairn/Places.lean`, `proofs/Cairn/Ownership/` | That `checking.py` implements this calculus: the differential harness shows agreement on generated programs, not extraction. Closures, `lane:f`, placement, `reduce`/`compact`, streams. The part guard is assumed of the emitter. |
| A host region runs every index exactly once before it returns, no pool worker is inside it when it returns, and from every state it reaches it returns without another worker joining. | Lean, `runs_once`, `quiet_when_back`, `finishes` | `proofs/Cairn/Region.lean` | That `cairn_parallel.hpp`, with its relaxed atomics, performs the modelled sequentially consistent steps. Device launches. |
| Where the checker's facts are true, a guard lowering leaves out cannot fail. | Lean, `index_sound`, `add_sound`, `sub_sound`, `atMostConst_sound`, `part_sound` | `proofs/Cairn/Facts.lean` | That the facts in scope are true where used, which an audit checks and nothing proves, and that `facts.py` computes what `Facts.lean` computes, which a differential run samples. `usize(x)` of a narrower value, and the lane blocks `window` places. |
| A declared spread gives every element of its tile exactly one holder, and with a declared storage layout no two holders write one offset. | Lean, `ok_one_holder`, `ok_covers`, `ok_one_writer`, `storage_distinct`, `ok_no_collision` | `proofs/Cairn/Layout.lean` | That `layouts.py` computes what `Layout.lean` computes, which a differential run samples. The code that uses a layout: that the lowering computes `D.at(t, v)` as the model's offset, and that a program writes through a spread at all. |
| In a phase the cooperative rule accepts, no two threads are about to take clashing steps, and every complete interleaving leaves one memory and one set of registers; so does a block of accepted phases. | Lean, `no_race`, `deterministic`, `block_deterministic` | `proofs/Cairn/Cooperative.lean` | That `phases.py` decides what the model's `program` decides, which a differential run samples. The global rule of `footprints.py`, warp operations, pipeline stages, and that the lowerings run the modelled steps. |
| Two versions of one function agree on the result and on everything they were lent, for every admitted input. | Z3 over a modeled source fragment, `smt-equivalent` | `src/cairn/verify/scalar_semantics.py` | Anything outside the [modeled fragment](#value-level-source-equivalence), which is `unknown`. Trusts the translator, Z3 and the assumptions stated there. |
| Every declared function and public type of two modules was compared that way. | `cairn verify --all`, `smt-module-equivalent` | `src/cairn/verify/verification.py` | One uncovered function keeps the module incomplete. A restricted entry holds only where its precondition does. Size, count and solver budgets apply. |
| Accepted programs build and run under both compilers, four sanitizers, CUDA and QEMU, and pass their finite task contracts. | Executed tests | `tests/` | Finite inputs only. |
| An implementation computes what its reference computes on the generated boundary cases and the kept regressions. | Executed, finite-tested, `cairn validate` | `src/cairn/verify/validation.py` | Every other input. Both sides are compiled by this compiler, so a shared lowering fault agrees with itself. Device implementations run only under `make gpu`. |

```sh
cd proofs && lake build                       # about four seconds, no dependencies
cd proofs && lake env lean Cairn/Audit.lean   # the axiom audit on its own
python3 -m pytest -q tests/verification
python3 bin/cairn certificates
python3 bin/cairn verify examples/proof_scope/reference.cairn examples/proof_scope/candidate.cairn --all
```

## The Lean project

`proofs/` is a Lean 4 project with no dependencies, not even Mathlib, pinned in `proofs/lean-toolchain`, and `lake build` checks it from scratch in a few seconds. [The audit](#pinned-versions-and-the-audit) holds it to `propext` and `Quot.sound`.

| File | Contents |
| --- | --- |
| `Cairn/Affine.lean` | `Form`, `Form.eval`, `Rule`, `Certificate`, the computable `check`, and `check_sound`. |
| `Cairn/CollectorCertificates.lean` | Generated by `tools/checks/export_lean_certificates.py`. The 17 obligations and their certificates as Lean data, `all_checked`, and one corollary per obligation. |
| `Cairn/Collector.lean` | The executable model of the loop, the invariant derived from the certificates, store-in-bounds, stable selection and increment bounds. |
| `Cairn/Places.lean` | What a borrow names: roots, bounds, valuations, `reaches`, the checker's overlap `ovl` (mirroring `checking.py:overlaps`), the real footprints `meets`, and the bridge `ovl_sound`. |
| `Cairn/Ownership/` | The ownership and lease calculus, one file per subject, imported by `Ownership.lean`: `Syntax`, `Lanes`, `Checker` (the executable `accepts`), `Weakening` (`Checks`, acceptance that may forget), `Machine` (the small-step machine, `Reach`), `Heap`, `Invariant`, `Preservation` (one lemma per statement, assembled in `Ok_succ`), `Soundness` (the `accepted_*` theorems and progress) and `Regress` (the pinned programs and the fault witnesses). |
| `Cairn/Region.lean` | The lane pool's region protocol and the three region theorems. |
| `Cairn/Facts.lean` | The guard-elision rule of `compiler/check/facts.py`: atoms, facts, the Bellman-Ford search, the five decisions lowering acts on, and their soundness. |
| `Cairn/Layout.lean` | The layout rules of `compiler/device/layout_algebra.py` as their definitions, what a layout that passes promises, and one example of each refusal. |
| `Cairn/Cooperative.lean` | The phase rule of `compiler/cooperative/phases.py`: steps, phases, interleavings, the executable `accepts`, the three theorems, and the cut-down source (`E`, `C`, `S`) that `program` runs for every thread. |
| `Cairn/Audit.lean` | `#print axioms` for every headline theorem, and the ownership regression line. |

The certificate module is generated from the Python rules, `tools/checks/export_lean_certificates.py --check` fails if the two drift, and the receipt reports `lean_verified: true` only while the live bundle hashes to the one Lean checked. None of it says anything about the parser, the type checker, placement, effects, the C++ compiler or the machine.

## The collector certificates and the loop model

`compact` lowers to a loop whose one store carries no dynamic bounds check:

```c
k = 0;
for (i = 0; i < n; ++i) { if (pred(i)) { out[k] = proj(i); ++k; } }
used = k;
```

`out` has capacity exactly `n`, and `pred` and `proj` never read `out`. The unchecked store rests on the cursor invariant `0 <= k <= i <= n <= M`, where k counts outputs, i inputs visited, n is the capacity and M the largest representable cursor. While `i < n`, the store at k is in range and both increments fit.

`src/cairn/verify/linear_certificates.py` writes each affine form as five integer coefficients over `(1,k,i,n,M)`, meaning "this form is nonnegative". A certificate gives nonnegative multipliers c_j and c_0, and the checker accepts only when `g = c_0 + sum_j c_j * a_j` coefficient by coefficient, so g is nonnegative wherever the premises a_j are. Seventeen obligations cover initialization, stores, both increments, emit and skip preservation and the output count; some are trivial identities. The compiler checks the bundle before every emission, a corrupted bundle blocks compilation, and the receipt pins the rules and the checker by SHA-256.

Lean adds three results. `Cairn.check_sound`: if the checker accepts a rule, its conclusion holds for every integer assignment that satisfies its assumptions.

```lean
theorem Cairn.check_sound {r : Rule} {c : Certificate} (h : check r c = true) :
    ∀ K I N M : Int, (∀ a ∈ r.assumptions, 0 ≤ a.eval K I N M) →
      0 ≤ r.conclusion.eval K I N M
```

`Cairn.Collector.all_checked`: all seventeen certificates pass that checker inside Lean, by `decide`, and each transition theorem over `Inv K I N M` applies its certified obligation rather than re-deriving the arithmetic:

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

For an executable model of the loop, every store is in bounds with no guard, both increments stay representable, and the result is stable selection:

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

`store_index_lt_buffer_length` states the bound against the live buffer, so `List.set`, which would silently drop an out-of-range write, never drops one.

Trusted: Python's integer arithmetic, the checker's implementation, and, by review only, that the compiler's cursor operations are these transitions. The certificates license this one store and say nothing about aliasing, lifetimes, initialization or the compiler. Every other guard the compiler leaves out rests on the guard-elision rule below.

## Ownership and leases: a mechanized core calculus

`Cairn/Places.lean` and `Cairn/Ownership/` are a small ownership-and-lease calculus: an executable checker, an interleaving small-step machine with explicit error states, and safety and progress theorems. Written by hand beside `src/cairn/compiler/check/checking.py`, it shows the rules are safe as a set; the differential harness tests whether `checking.py` follows them.

### Locals and places

A local is the unit of ownership. A place is what one borrow names, one constructor per shape `checking.py` produces:

| Lean | source | what it is |
| --- | --- | --- |
| `whole x` | `d` | the owner itself, header and elements: what an `rw<Buf[T]>` parameter lends, and the only borrow through which a task can replace the cell (`swap`) |
| `hdr x` | the `len(d)` read | the header alone: the length and the identity of the cell (`leased(..., elements=False)`) |
| `elems x` | `d[]` | every element: what an array view `rw<T>[n]` of a whole owner lends |
| `part x lo hi` | `d[lo..hi]` | those elements |

Each place has a root, a local plus a field path, so `r.xs[lo..hi]` is a part at the root `r.xs`. A bound is an integer literal or an immutable natural, and every theorem holds under every valuation of the bounds.

A single element `a[i]`, a part of a part, and a part with a bound that can change are all modelled conservatively as `elems`: each overlaps every place of its base and adds no `lo <= hi` fact. The regression checks these classifications against `checking.py` on its named programs (`tests/soundness/test_soundness.py`).

### Disjointness, decided twice

The checker decides overlap syntactically, as `checking.py:overlaps` does: different locals, distinct fields of one record, and a header and its elements never overlap, which is why `len(d)` stays readable while `d`'s elements are lent. Two parts of one root are disjoint exactly when one visibly ends at or before the other begins, through a chain of the guarded `lo <= hi` facts of the parts in play (`reaches`). The machine decides overlap under the valuation, and `Ownership.ovl_sound` is the bridge: syntactic disjointness implies real disjointness.

`leased` chains through the parts live tasks hold, and `disjoint` through the parts of one argument list. So one call handed `d[0..a]` and `d[b..n]` is rejected even while `d[a..b]` is lent, while spawning the two ends one at a time is accepted. Both checkers can refuse a safe program, and neither accepts an unsafe one.

### The guard, and the trap

A part is formed by `cr::part`, which traps unless `lo <= hi`, and only that guard is modelled. The machine runs it on the spawner's thread before the call or task starts. A failed guard is a trap: a defined abort of the whole configuration, which is neither a fault nor a race. That makes it safe to accept `d[6..3]` as the part that orders `d[0..6]` before `d[3..9]`: the program aborts at its spawn before either task exists.

### Syntax, checker and machine

A program is one scope of declared locals and a body of statements: `alloc`, `mkScalar`, `copy`, `move`, `drop`, `call` with a list of `(place, ro|rw)` borrows, `spawn` and `wait`, `ite` with both branches, `parallel`, and the group statements `group`, `submit` and `collect`. A borrow exists only as an entry in one argument list, so borrows are second class by construction.

`Ownership.accepts` is an executable Lean checker that carries the state `checking.py` carries: which locals hold scalars or owners, which are dead, which groups are live, and which borrows each ticket or group holds. It refuses a use of a dead local, a copy of an owner, a move of anything leased, a conflicting access to what a live task holds, overlapping `rw` arguments in one call, a group used after `wait`, branches that disagree about live tickets or groups, and a live ticket or group at the end of the scope.

The machine is a small-step semantics over a heap. It is deliberately undefensive, so that faults are reachable for programs the checker rejects. Its error configurations are `UseAfterMove`, `UseAfterFree`, `DoubleFree`, `Leak`, `Race`, `AliasedArgs` and `DeadGroup`; `Trap` is not one of them. A task's body is abstracted to its footprint: while its ticket is live it may touch any place it was lent, in that mode, between any two steps of the spawner. `if` has an opaque condition, so both branches are always reachable.

### Parallel regions

A region's body is a list of lane accesses, as `checking.py:region` records them: at the lane's own index, inside the lane's block of stride `S`, at any other index (every element), the place itself, or `len(x)`. The rule is `checking.py`'s: whatever any lane writes is touched only inside each lane's own block, with one stride per local. A region beside a task holding any part of the same array is rejected, conservatively.

`lanesOf n body` builds one thread per index, and lanes step exactly as tasks do, so one `Pairwise` statement covers two tasks, a task and a lane, and two lanes. The spawner is blocked while a region runs, which `Region.lean` proves the pool does.

### Task groups

A group is a name many tasks share. `collect g` joins one finished task, which one unknown, so the machine has a successor per task that might have finished, and the checker keeps every lease until `wait g`. After a branch, a group holds what either path lent it, and a part keeps its bounds only if both paths formed it, since its guard ran only where it was formed. `tests/soundness/test_groups.py` has the programs that raced before this rule. Because a join claims more than either path, preservation is proved for acceptance that may forget between statements (`Checks`, `joinOf_le`, `Sync.weaken`).

### The lane pool

`Cairn/Region.lean` models the lane pool behind `cr::par::run`. A wide region is cut into consecutive homes, one per lane it can use, each with a counter lanes claim chunks from. Every lane goes through the homes in an order of its own, and a worker starts on the same home in every region, so it runs the same indices from its own cache before it helps with the others. The starter takes every home, unlinks the region when it has been through them all, and returns once no worker is inside.

The theorems hold under every interleaving of any number of workers, with any claim size, any cut into consecutive homes and any order a worker takes them in. `runs_once` says every index below `n` has run exactly once when the starter returns, `quiet_when_back` that no worker is inside, and `finishes` that the region returns without waiting for a worker to arrive.

The model is sequentially consistent. The C++ bumps each counter with a relaxed `fetch_add` and orders a leaving worker against the waiting starter with sequentially consistent operations. That the header performs the modelled steps is review, not proof; `tests/runtime/parallel_runtime.cpp` checks that its cut is the one the model assumes. A region below `lanes::CUTOFF`, or in a process with one lane, is the plain loop on the thread that starts it.

### The theorems

Every statement below is proved in `proofs/Cairn/`, audited in `Cairn/Audit.lean` and required by `tests/verification/test_lean_proofs.py`. `Reach ρ p.scope (Cfg.start p) cfg` means `cfg` is reachable under the interleaving semantics when the bounds take the values `ρ` gives, and every valuation is covered.

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

`accepted_no_use_after_move`, `accepted_no_use_after_free`, `accepted_no_double_free`, `accepted_race_free`, `accepted_no_leaked_ticket`, `accepted_no_use_after_wait` and `accepted_no_aliased_args` are `accepted_no_fault` at each error. They rest on `Ok_succ`, the preservation lemma. The release theorem covers normal termination only; nothing is claimed about the cells a trapped run leaves. `accepted_progress` says the only stuck configurations are the errors, which `accepted_no_fault` excludes.

### The regression, and that the faults are reachable

`ownership_regression` checks that the Lean encodings of the programs pinned in `tests/soundness/` are classified as the Python checker classifies them. Each rejected or accepted program is written out with its CAIRN source in `Cairn/Ownership/Regress.lean`: moves, leases, overlapping parts, fields, groups and lane blocks. The build prints `ownership-regression: pass`, and the Python gate asserts on that line.

The safety theorems would be vacuous if the machine could never fault, so sixteen witnesses drive it to `Race`, `AliasedArgs`, `DoubleFree`, `UseAfterMove`, `Leak` and `DeadGroup` for programs the checker rejects, and `witnesses_are_rejected` and `group_witnesses_are_rejected` confirm the rejections. `UseAfterFree` is the one error with no witness. `backwardsPart_traps` shows an accepted program reaching `Trap` at the guard of `d[6..3]` before any race.

### The differential harness

`tools/checks/differential_ownership.py` is the one mechanical link between the two checkers. A seeded generator renders each program twice, as CAIRN source and as a Lean `Program`, and the Python checker's verdict and Lean's `accepts` must match for every program. `--fragment` prints what the generator covers: locals, moves, calls, tasks, branches, regions with lane blocks, and task groups. Record fields, loops, closures, placement, `reduce`, `compact`, `take` and `swap` are outside it. The generated programs are checked, never built or run.

```sh
python tools/checks/differential_ownership.py --count 2000 --seed 7
CAIRN_DIFFERENTIAL_N=5000 python -m pytest -q tests/verification/test_differential_ownership.py
```

`make test` runs 40 programs and `make lean` 200, and the test plants one wrong verdict that the harness must report. Twenty thousand programs agreed on the run recorded in `evidence/v1_0/gates_2026_09_22/lean/differential.json`. Without `lake` the harness exits 3 rather than passing.

### What this does not cover

| Left out | Where it stands |
| --- | --- |
| That `checking.py` implements this calculus | The Lean checker is written by hand; the differential harness above compares the two. |
| The part guard running on the spawning thread before a task starts | Asserted by `codegen.py` and `cairn_owners.hpp`, and pinned for `spawn` and `spawn ... into g` by `tests/soundness/test_concurrency.py::test_a_part_is_guarded_by_the_spawner_before_its_task_exists`. |
| The `elems` mapping for every program | Checked only on the programs the regression names. |
| Where a field read is charged | `checking.py:e_field` charges `leased(box.a, "ro", elements=False)` at the outermost field of the path; the model writes the read as an explicit `call [(hdr box.a, ro)]`. |
| A region beyond its accesses | `lane:f`, `E-PARALLEL-CALL`, placement, `reduce`, `compact`, queued device work and the values lanes compute. A lane index that is neither the binder nor inside a placed block counts as every element, so `parallel i in n { x[i + 1] = 0; }`, which cannot race, is a race here, and the checker refuses it too. |
| The pool's memory orders | Review only, as [the lane pool](#the-lane-pool) says. |
| Closures | Captures are not modelled. |
| Device streams | The `after` exemption is absent, so the calculus would refuse those programs. |
| A group submitted to inside a loop | The rule that nothing the body touches may conflict with what an earlier iteration lent is tested. A group lent to a callee is refused by its signature (`E-PINNED`). |
| Declared field extents | A typing rule of `checking.py`, held by `E-EXTENT-FIELD` and tested natively. |
| The rest of the language | Linear values other than tickets and groups, `defer`, `take` and `swap` as statements, loops, `return`, traits, generics, atomics, mutexes, I/O rings, effects and the foreign boundary. |
| Callee bodies | A call is its footprint, so `AliasedArgs` is detected at a call and never caused inside one. |
| Values | The race result is about conflicting access; it says nothing about whether results are deterministic. |
| The machine | No native memory model, allocator, C++ or thread semantics. |

## The guard-elision rule

Lowering leaves a guard out where `compiler/check/facts.py` shows it cannot fail, and `proofs/Cairn/Facts.lean` proves the rule. A fact is `x - y <= k` between two atoms (zero, an immutable `usize`, or a multiple of a stride), and a Bellman-Ford search finds the tightest `k` the facts give. Five decisions read the result: an index below its extent, a `+` that stays at most the largest `usize`, a `-` that stays at least zero, a value at most a constant (a shift count or a narrowing), and a part inside its view. `distance_sound`, `bounds_sound`, `index_sound`, `add_sound`, `sub_sound`, `atMostConst_sound` and `part_sound` prove them under every valuation that makes the facts true.

The Lean functions are transliterations of the Python ones, and `tools/checks/differential_facts.py` asks both the same generated questions. Twenty thousand inputs agreed on the run in `evidence/v1_0/gates_2026_09_22/lean/facts_differential.json`, and a planted off-by-one is caught within a few hundred.

That the facts in scope are true where they are used is checked at every site but not proved. `src/cairn/verify/elision.py` walks each function independently, without importing `facts.py`. It requires every fact a discharged guard cites to come from an origin in force at that site (a loop binder, an immutable `let`, a condition, an early exit, the left side of `&&`) and to name only values that cannot have changed. It then decides the guard again from those facts alone. The emitter keeps any guard whose proof the audit refuses, counted under `refused_discharges`.

Across the library, the examples and the docs the audit refuses nothing. `tests/soundness/test_elision.py` requires every single-point tampering of a proof to be refused, and an off-by-one planted in `facts.py` to reach no emitted program. `tools/checks/differential_guards.py` builds generated programs with and without every guard, under both compilers and the sanitizers, and requires the same value or the same trap: 1,821 functions and 174,816 cases per compiler agreed (`evidence/v1_0/guards/`). The audit's own rules are hand-written and not in Lean, and the model has no `usize(x)` of a narrower integer.

## The layout rule

A declared layout is held to two rules, and `proofs/Cairn/Layout.lean` states them as their definitions: a spread's every element has exactly one (participant, value) holder, and a storage layout's every element its own offset. `ok_one_holder` and `ok_covers` say a phase in which every participant writes each of its values writes every element once, `ok_one_writer` that no two pairs write one element, and `ok_no_collision` that with a tile that passes its own rule no two pairs write one offset. `gappy_gap`, `doubled_overlap` and `cleared_clash` show each refusal happens. `accumulator_share_ok` and `accumulator_share_is_the_isa` say the spread a program declares for an `mma.sync` accumulator's lanes is the PTX ISA's formula and gives each element one lane, which is the share `mma_store` writes by.

`compiler/device/layout_algebra.py` counts holders in one pass and finds a shared offset with a table, where the Lean counts element by element. `tools/checks/differential_layouts.py` asks both about generated layouts, among them what `spread`, `transpose` and `swizzle` make, and requires the same verdict: a coordinate outside the tile, the first element held twice, the first left to nobody, the first two elements that share an offset. Three hundred agree in `make test`, and a planted slip that lets one participant hold an element twice is reported. The code that uses a layout is tested, not proved.

## The phase rule

`proofs/Cairn/Cooperative.lean` models one block of a [cooperative region](devices.md#cooperative-regions): every thread runs the same phases, and within a phase the threads' steps, loads into registers and stores of values computed from them, interleave in any order. `no_race` says the next steps of two threads of an accepted phase never clash. `deterministic` says any two interleavings that run every thread to its end leave the same memory and registers, because every element holds what its one writer would have put there running alone or what it held before. `block_deterministic` carries that through a block of phases, a barrier between each two.

The model's `program` runs a cut-down source for every thread (element reads and writes at indexes built from the thread's number, loop counters and literals, `if` on the thread's number, loops of known count, barriers) and applies `accepts` to each phase. `tools/checks/differential_cooperative.py` renders generated regions as CAIRN and as Lean terms and requires `compiler/cooperative/phases.py` to accept exactly those `program` accepts. A slip planted in `phases.py` must be reported. That is agreement on samples, not a proof that the Python is the model.

The other rules of a cooperative region have no Lean model and are finite-tested only: the global rule of `footprints.py` (`E-COOP-GLOBAL`), pipeline stages (`E-STAGE-UNREADY`, `E-STAGE-BUSY`, `E-STAGE-LOOP`), who reaches a barrier or a warp operation together (`E-COOP-BARRIER`, `E-COOP-WARP`), atomic updates as a class apart from plain accesses (`E-ATOMIC-MIXED`), a region's finish, and the rule that lets a shared array go unzeroed (`E-COOP-UNWRITTEN`). The model's steps are plain loads and stores, so an atomic update is outside it, and its source declares every array zeroed. They rest on the refusal tests in `tests/soundness/test_cooperative.py`, `test_pipelines.py`, `test_reach.py`, `test_atomics.py`, `test_finish.py` and `test_written.py` and on the host lowering's thread sanitizer runs, which cannot see a device-only fault such as a read of a stage whose `cp.async` copy is still in flight.

Work toward models of them, and the review, found accepted programs that race, hang or read an unfinished stage. Each is now refused with a test:

| Code | What was accepted |
|---|---|
| `E-COOP-GLOBAL` | a condition bound over digits counted both ways; a loop range that moves with another digit |
| `E-COOP-BARRIER`, `E-COOP-WARP` | a value made per-thread through an `rw` borrow, a closure, an element, `swap`, an atomic, typed `asm` or a method's receiver; a warp operation on the right of `&&` or `\|\|`; a `while` condition; a fixed point that needed more than eight rounds |
| `E-COOP-UNDECIDED` | a closure naming a shared array, a pipeline or an outside array |
| `E-STAGE-UNREADY`, `E-STAGE-LOOP`, `E-PLACEMENT` | a stage used whole or in part before its wait; a `break` out of a fill loop; a fill from memory the region cannot copy from |

## Value-level source equivalence

`src/cairn/verify/scalar_semantics.py` evaluates a fragment of the source symbolically, and Z3 answers whether two versions of one function can differ. The fragment has exact-width integers and bools, `f32` and `f64`, records, enums and sums with `match` and `try`, array views and parts, single borrows, fixed local storage, function-local heap owners, `compact`, host `reduce`, branches, bounded loops, early return and acyclic calls.

An observation is the returned value, the length and elements of a returned owner, and the final contents of every `rw` parameter, or one undifferentiated abort. An owner inside a record, a sum or an array, recursion, tasks, lanes, atomics, device placement, closures, `dyn`, the foreign boundary, a storage float or a quantization, and a function that asserts are `unknown`, with the reason.

For reference R, candidate C and domain D, Z3 is asked whether some admitted input makes one abort and not the other, or both return different observations. Unsatisfiable, with no reachable NaN in what is observed, is `smt-equivalent` within this model. A counterexample is replayed through an independent evaluator before it is reported, and the checker asks again for one with small values, so a slip at a boundary reads as `x = 100`. A missing tool, a translation error, unsupported syntax, an exhausted budget, a disagreeing replay and a timeout are all `unknown`. No proof is reconstructed in Lean, and no theorem relates this translator to native code. A reference that states the wrong requirement is checked as faithfully as a right one.

What the model assumes, case by case:

- Views. A view is one SMT array per scalar component, with the extent its signature names. Inputs are what the emitted entry guards admit: `rw` views are distinct storage, and read-only views may alias. Two parts of one array share one array term, and the callee's `cr::disjoint` guard aborts as the emitter runs it, a case `E-ALIAS` keeps an accepted program from reaching.
- Owners. A local owner is zeroed storage with its own length. `take` leaves length 0 behind and `swap` exchanges storage and length. A `Buf[T]` passed by value has any length and contents, and a returned one is observed element by element.
- Tags. A sum is its emitted tag beside every payload, and compares its tag and the active payload. Only a value parameter's top-level tag is guarded, as the emitter guards it, so any other tag may name no variant, and a `match` over such a tag aborts; this assumes the emitter closes every `switch` with `default: cr::trap();`. A type in the signature must be defined the same on both sides, or the answer is `invalid-contract`.
- Collectors. `compact` and `reduce` are unrolled as the host emits them. A float reduction is `unknown`.
- Traps. Every checked operation and guard aborts, and every abort is one observation, so "both trap" is equal. By default the reference must return on every admitted input (`allow_reference_traps` opts out).
- Floats. Z3's FloatingPoint theory, rounding to nearest even once per operation. `-0.0` and `+0.0` are different results. Two assumptions are stated, not proved: the C++ compiler rounds decimal literals to nearest even, and the host evaluates `float` in `float`. An observed float that may be NaN makes the answer `unknown`; `assume="x==x"` excludes NaN.
- Loops. Unrolled up to 16 iterations, and Z3 must show no admitted input reaches a seventeenth, or the answer is `unknown`. An extent is symbolic, so a pass over `0..n` needs a precondition bounding `n`.
- Order. A `try` is modelled only as the whole right-hand side of a binding, assignment, return or statement; elsewhere it is `unknown`.

## Whole-module coverage

`cairn verify --all` compares every function and public type of two complete sources. Anything missing, extra, unsupported or undecided prevents `smt-module-equivalent`, and one uncovered function keeps the module incomplete. `--assume` gives one function a precondition, and repeats:

```sh
python3 bin/cairn verify reference.cairn candidate.cairn --all --assume 'total=n<=4'
```

A restricted entry is equivalent only where its precondition holds, and the receipt records every precondition. One that admits no input is `invalid-domain`. Inputs are limited to 64000 bytes and 128 functions, with a soft 30-second solver budget. `examples/proof_scope/mixed.cairn` is the negative fixture that must stay incomplete even against itself.

## Diffs between versions

Each class [`cairn diff`](tools.md#cairn-diff) gives is a different claim. `identical-code` says the same C++ is compiled, up to renaming and the two identities `verify/emission.py` names, and nothing about whether that C++ is right. `identical-source` says only that an uninstantiated generic's tokens are unchanged. `smt-equivalent` is the result above, with its fragment and its trust in the translator and Z3. `behavior-changed` is a counterexample replayed by the value model and natively, a fact about both programs on that input. `unknown` claims nothing. The deltas beside the class are what the checker recorded for each version, the semantic version is only as strong as the classes, and the cost change is a prediction.

## Tests, native code and failures

Accepted programs run natively under both compilers and under AddressSanitizer, UndefinedBehaviorSanitizer, LeakSanitizer and ThreadSanitizer, and device guards have death tests that run only under `make gpu`. A sanitizer-clean run shows the absence of those faults on the inputs it ran. A template nothing instantiates is unchecked, and the receipt lists it.

The rules of `checking.py` outside the calculus, such as linear values, leases over parts whose bounds are not visible, what a lane's callees may do, placement and the effect fixed point, are exercised by acceptance and rejection tests, and nothing connects them mechanically to a model. A process that prints a pass and then crashes has failed.

## What each feature has shown

Each row below is one feature, and each column one claim about it: whether it is implemented, which targets its code compiles for, whether it has run on a CPU and on a GPU, which sanitizers watched it run, and whether its speed was measured. A cell is `yes`, `partial` (it says which part holds), `no`, `unknown` or `n/a`, followed by what it rests on. `unknown` means nobody has checked, and is never success. A GPU run or a measurement counts only with an executed record under `evidence/`; a device test that skips without a GPU is not one.

A host row's claims are for x86-64 Linux under clang++ and g++, the reference machine the records were taken on, unless the row names another platform. Hosted AArch64 last ran the suite on a GH200 at v0.8.2, before most of these features existed, and a CI job on an AArch64 host counts here once its run is recorded. On a device, "compiles" means nvcc and ptxas accepted the code for that target, and nothing more.

The rows are data, [project/capability_matrix.json](project/capability_matrix.json). To add a feature, add a row there, run `make docs`, and commit both; `tests/tooling/test_capability_matrix.py` fails while this table differs from the data, while a row names a record that does not exist, or while a GPU run or a measurement says `yes` without one under `evidence/`.

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
| [Device programs emulated on host threads (`--emulate`)](devices.md#emulating-device-code-on-the-host) | yes | yes: x86-64, clang++ and g++, judged against a device target | yes: every device example under both compilers | n/a | yes: address, undefined and leak; thread on cooperative regions | n/a: an emulated time measures host threads, never a device | [v1_1/emulation](../evidence/v1_1/emulation/README.md), [test_emulation.py](../tests/projects/test_emulation.py) |
| [One compile per source for the hosts, `cairn mcp` and `cairn lsp`](tools.md#cairn-mcp) | yes | n/a: the compiler's own tooling, which builds no program of its own | yes: every example emits the same C++ and receipt from a kept copy as from scratch | n/a | n/a | yes: MCP checks, edit sessions and edit-then-check cycles before and after, on one loaded machine | [v1_1/workspace](../evidence/v1_1/workspace/README.md), [test_compilations.py](../tests/agent/test_compilations.py), [test_mcp.py](../tests/agent/test_mcp.py) |

### On a device

| Feature | Implemented | Compiles for | Ran on a CPU | Ran on a GPU | Sanitizers | Measured | Records |
|---|---|---|---|---|---|---|---|
| [Device regions, `reduce`, `scan` and `compact`](concurrency.md#parallel-regions) | yes | yes: sm_120 in the suite; sm_80, sm_89, sm_90a, sm_100a and sm_120a for the suite's kernels | yes: emulated, and on a host stand-in for the CUDA runtime | partial: `parallel` regions and the `reduce` collector ran on an RTX 5070 Ti in bench/device, each result checked against CUDA or the exact sum; `scan` and `compact` have not run since v0.8.3 | partial: the host sanitizers on emulated builds; Compute Sanitizer has not run | partial: regions and `reduce` against hand-written CUDA on an RTX 5070 Ti; `scan` and `compact` untimed | [v0_8_3/gpu/benchmark.json](../evidence/v0_8_3/gpu/benchmark.json), [v1_1/catalog](../evidence/v1_1/catalog/README.md), [v1_1/emulation](../evidence/v1_1/emulation/README.md), [v1_0/execution](../evidence/v1_0/execution/README.md), [v1_1/device_perf](../evidence/v1_1/device_perf/README.md) |
| [Guards in device lanes](devices.md#emulating-device-code-on-the-host) | yes | yes: sm_120 | yes: emulated, where a failing guard aborts with SIGABRT | partial: the checked multiply, add and subtract of every integer type on 1.6 million operand pairs on an RTX 5070 Ti, none trapping; a guard that fails has not run since v0.8.3 | partial: address on emulated builds reports a read past a device array; Compute Sanitizer has not run | partial: what checked index arithmetic adds to kernel time against unchecked CUDA | [test_emulation.py](../tests/projects/test_emulation.py), [v1_0/RUN_NOTES.md](../evidence/v1_0/RUN_NOTES.md), [v1_1/device_perf](../evidence/v1_1/device_perf/README.md) |
| [Transfers, queued device work and the execution context](devices.md#queued-device-work) | yes | yes: sm_120 | yes: on a host stand-in that counts every stream, allocation and wait, and emulated | partial: the execution context on a caller's stream, a held function's one wait and `cq_` entries with none, and a CUDA graph capture of them, on an RTX 5070 Ti; queued work has not run | partial: thread and undefined on the host stand-in; Compute Sanitizer has not run | partial: the host waits a call makes and what each costs on an RTX 5070 Ti under WSL2 | [v1_0/execution](../evidence/v1_0/execution/README.md), [test_execution.py](../tests/runtime/test_execution.py), [v1_1/emulation](../evidence/v1_1/emulation/README.md), [v1_1/device_perf](../evidence/v1_1/device_perf/README.md) |
| [Device plans: `block`, `per_lane`, `unroll`, `vector` and `stage`](concurrency.md#plans) | yes | yes: sm_120 | partial: a `vector 4` and a `stage 1` region on the host stand-in | no | partial: thread and undefined on the host stand-in; Compute Sanitizer has not run | no: predicted only | [test_plans.py](../tests/soundness/test_plans.py), [test_staging.py](../tests/soundness/test_staging.py), [v1_0/execution](../evidence/v1_0/execution/README.md) |
| [Wide loads and stores: `load_wide[K]`, `store_wide` and `Cache` hints](devices.md#wide-loads-and-stores) | yes | yes: sm_120, to LDG.E.EF.128, STG.E.EF.128, LDG.E.128.CONSTANT, LDS.128 and STS.128 | yes: in host code, host lanes and host threads, and emulated, under both compilers | partial: `load_wide[4]` with `Cache.l2` in a cooperative region on an RTX 5070 Ti, its sum checked; `store_wide` has not run | partial: address and undefined under clang++, thread on host threads; Compute Sanitizer has not run | partial: that load in a two-pass sum against CUDA's `__ldcg` on an RTX 5070 Ti | [test_wide.py](../tests/soundness/test_wide.py), [v1_1/device_perf](../evidence/v1_1/device_perf/README.md) |
| [Atomic updates of one element in lanes and threads](concurrency.md#atomics-and-mutexes) | yes | yes: sm_120, to RED, ATOMG and ATOMS | yes: on host lanes and host threads under both compilers, and emulated | no | partial: thread, which reports an update made plain, and address and undefined under clang++; Compute Sanitizer has not run | no | [test_atomics.py](../tests/soundness/test_atomics.py) |
| [Cooperative regions: shared arrays, barriers and warp operations](devices.md#cooperative-regions) | yes | yes: sm_80, sm_89, sm_90a, sm_100a and sm_120a | yes: each block's threads as host threads at a barrier, under both compilers | yes: layer norm, transpose, stencil and two-pass sums on an RTX 5070 Ti, each result checked against CUDA | partial: thread under both compilers, which reports a removed barrier; Compute Sanitizer has not run | yes: against hand-written CUDA of the same algorithms on an RTX 5070 Ti | [v1_0/cooperative](../evidence/v1_0/cooperative/README.md), [v1_1/catalog](../evidence/v1_1/catalog/README.md), [v1_0/predict_cooperative](../evidence/v1_0/predict_cooperative/README.md), [test_cooperative.py](../tests/soundness/test_cooperative.py), [test_reduction_example.py](../tests/projects/test_reduction_example.py), [v1_1/kernels](../evidence/v1_1/kernels/README.md), [test_votes.py](../tests/soundness/test_votes.py), [v1_1/device_perf](../evidence/v1_1/device_perf/README.md) |
| [A cooperative region's finish: `then threads t in T { }`](devices.md#cooperative-regions) | yes | yes: sm_120, one launch whose last block runs the finish | yes: one more team of host threads after the blocks, grids of 0 to 70 blocks, under both compilers, and emulated | no | partial: thread, which reports a finish that does not wait for the blocks; Compute Sanitizer has not run | no | [test_finish.py](../tests/soundness/test_finish.py) |
| [Shared arrays nobody zeroes](devices.md#cooperative-regions) | yes | yes: sm_120, zeroing none of them where a block starts | yes: on host threads under both compilers, and emulated, each block starting them filled with a pattern | no | partial: thread, and address and undefined under clang++; Compute Sanitizer has not run | no | [test_written.py](../tests/soundness/test_written.py) |
| [Pipeline stages](devices.md#cooperative-regions) | yes | yes: sm_120 with `cp.async`; sm_80, sm_89, sm_90a, sm_100a and sm_120a | yes: row sums at depth 2 and 3 on host threads, under both compilers | no | partial: thread under both compilers; Compute Sanitizer has not run | no | [v1_0/cooperative](../evidence/v1_0/cooperative/README.md), [v1_1/catalog](../evidence/v1_1/catalog/README.md), [test_pipelines.py](../tests/soundness/test_pipelines.py) |
| [The tensor-core multiply and its fragments](numerics.md#tensor-core-fragments) | yes | yes: sm_80, sm_89, sm_90a, sm_100a and sm_120a | yes: two kernels on 16 shapes under both compilers, within the contract's bound | no | partial: thread on host threads; Compute Sanitizer has not run | no | [v1_0/tensor](../evidence/v1_0/tensor/README.md), [v1_1/catalog](../evidence/v1_1/catalog/README.md), [test_tensor_kernels.py](../tests/soundness/test_tensor_kernels.py) |
| [Layouts and spreads in code](memory.md#layouts) | yes | yes: sm_120 | yes: the offsets of 120 random layouts against the checker's own enumeration, under both compilers | no | partial: undefined under both compilers on the host; Compute Sanitizer has not run | no | [v1_0/review_implementation_layer](../evidence/v1_0/review_implementation_layer/README.md), [v1_0/tensor](../evidence/v1_0/tensor/README.md), [test_layouts.py](../tests/language/test_layouts.py) |
| [Storage floats, gradients and asserts in a lane](numerics.md#storage-floats) | yes | yes: sm_120 | unknown: no emulated run of them is recorded | no | no: Compute Sanitizer has not run | no | [test_storage_floats.py](../tests/language/test_storage_floats.py), [test_gradients.py](../tests/language/test_gradients.py) |
| [Typed PTX](memory.md#layout-and-the-machine) | yes | yes: sm_120, with the SASS it names read back | no: `--emulate` refuses it | yes: 16-byte loads and `brev` in a `parallel` and a cooperative region on an RTX 5070 Ti, once PTX in a region's body launched | no | yes: a two-pass sum with 16-byte PTX loads against CUDA on an RTX 5070 Ti | [v1_0/foreign](../evidence/v1_0/foreign/README.md), [test_assembly.py](../tests/language/test_assembly.py), [v1_1/device_perf](../evidence/v1_1/device_perf/README.md) |
| [Foreign CUDA implementations and `launch`](memory.md#foreign-implementations) | yes | yes: sm_120 with every warning an error | no: `--emulate` refuses vendored CUDA | no: its device validation tests are generated and built, and run only under make gpu | no: Compute Sanitizer runs only under make gpu | no | [v1_0/foreign](../evidence/v1_0/foreign/README.md), [test_foreign.py](../tests/projects/test_foreign.py) |
| [Device validation of an implementation](tools.md#cairn-validate) | yes | yes: the generated device tests build for sm_120 | yes: with `--emulate`, as finite-tested-emulated | no | no: each Compute Sanitizer tool runs only under make gpu | n/a | [v1_0/foreign](../evidence/v1_0/foreign/README.md), [v1_1/emulation](../evidence/v1_1/emulation/README.md), [test_device_validation.py](../tests/verification/test_device_validation.py) |
| [`cairn tune` on device candidates](tools.md#cairn-tune) | yes | yes: sm_120, read by ptxas and cuobjdump, nothing launched | n/a | no: make tune-device has not run | n/a | partial: its compiles and the search's wall time, evidence/v1_1/search; no device time | [test_search.py](../tests/tooling/test_search.py), [test_search_budget.py](../tests/tooling/test_search_budget.py), [test_search_instances.py](../tests/tooling/test_search_instances.py), [v1_0/search](../evidence/v1_0/search/README.md), [v1_1/search](../evidence/v1_1/search/README.md) |
| [SOL-ExecBench solutions, and projects from its problems](devices.md#benchmark-submissions) | yes | yes: sm_100a, compiled and linked against torch 2.9.0+cu130 as SOL-ExecBench's build_ext.py builds a solution, and accepted by its pydantic models | yes: a host-view solution built by torch.utils.cpp_extension.load equals the reference with CPU torch, and an emulated RMSNorm is within SOL-ExecBench's tolerance on nine workloads | no | no | no | [v1_1/harness](../evidence/v1_1/harness/README.md), [test_harness.py](../tests/projects/test_harness.py), [test_harness_torch.py](../tests/projects/test_harness_torch.py) |
| [GPU MODE submissions](devices.md#benchmark-submissions) | yes | yes: sm_100a through load_inline, compiled and linked against torch 2.9.0+cu130 | yes: a host-view submission built by load_inline returns the reference's output with CPU torch | no | no | no | [v1_1/harness](../evidence/v1_1/harness/README.md), [test_harness.py](../tests/projects/test_harness.py), [test_harness_torch.py](../tests/projects/test_harness_torch.py) |
| [KernelBench `ModelNew`](devices.md#benchmark-submissions) | yes | yes: sm_100a through load_inline, compiled and linked against torch 2.9.0+cu130, and valid under KernelBench's static checker | yes: a host-view ModelNew built by load_inline equals the problem's Model with CPU torch | no | no | no | [v1_1/harness](../evidence/v1_1/harness/README.md), [test_harness.py](../tests/projects/test_harness.py), [test_harness_torch.py](../tests/projects/test_harness_torch.py) |

<!-- end of the generated capability matrix -->

## Validating an implementation

[`cairn validate`](tools.md#cairn-validate) and the implementation session hold an implementation to its reference by running both on inputs from the contract. The reference is an independent algorithm, which makes it an oracle, but not an independent compiler: a fault in the shared parser, checker, lowering or runtime can make both wrong alike. A pass is finite testing of exactly the cases that ran. A run with no case, an implementation no case reached, and a call past its limit are `unknown`.

Float results agree under one numerical policy, `cairn.agreement/1` in `verify/agreement.py`. Both values widen exactly to f64, a storage float through f32, and every rule computes in f64. The first rule that applies decides: a NaN agrees with any NaN and with nothing else; two equal values agree when their bits match, so `-0.0` and `0.0` agree only under a tolerance; an infinity agrees only with itself; otherwise `|r - c| <= absolute + relative * |r|`, where `r` is the reference's value. The relative part scales the reference, as `numpy.isclose` scales its second argument, so a wrong result cannot widen its own bound.

The rules are written once, as CAIRN expressions. Host validation, `cairn test`'s replay of kept cases and the implementation session evaluate them as Python, and the generated device tests carry them as a CAIRN function the compiler lowers. `tests/verification/test_agreement.py` requires the same verdict from both renderings on one table of pairs, NaNs, both zeros, infinities, subnormals and the largest finite value among them, under clang++ and g++.

Z3's answer on the same pair is reported beside the finite result, and decides a loop only up to the unrolling bound the record names. Z3 compares exactly and does not know the admitted domain, so a counterexample is replayed natively through the same path as every case. One that breaks the policy fails the validation and is kept as a regression. One outside the domain, or whose native results agree under the policy, leaves the finite result standing, and the record says which. A replay that decides nothing makes the validation `unknown`.

## Pinned versions and the audit

| Component | Version |
| --- | --- |
| Lean | 4.34.0 (`leanprover/lean4:v4.34.0`) |
| Dependencies | none (`lake-manifest.json` lists no packages; no Mathlib) |

`Cairn/Audit.lean` runs `#print axioms` on every headline declaration, and every one depends on `[propext, Quot.sound]` at most. There is no `sorryAx`, `Lean.ofReduceBool`, `axiom`, `unsafe` or `implemented_by`; `tests/verification/test_lean_proofs.py` enforces that against the sources and the audit output. `tools/release/collect_lean_evidence.py` records a from-scratch build and the audit under `evidence/<release>/lean/`.

The receipt's `lean_verified` field covers the certificate bundle alone, and [roadmap.md](roadmap.md#proof) lists the formal steps that remain.
