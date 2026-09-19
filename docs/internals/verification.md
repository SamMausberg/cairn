# What is proved, what is tested, and what is not

There is no whole-compiler proof. Three mechanisms establish three different things about three different objects, and none of them transfers to the others: Lean 4 proofs about hand-written models, Z3 queries about a modeled source fragment, and executed tests. Accepted, typed, native-built, finite-tested, sanitizer-clean, SMT-equivalent, Lean-checked and benchmarked are separate claims, and `unknown` is never any of them.

## Summary

| Claim | Established by | Where | What it does not cover |
|---|---|---|---|
| The certificate checker is sound. | Lean, `check_sound` | `proofs/Cairn/Affine.lean` | That `linear_certificates.py` implements the Lean `check`; it is a reviewed transliteration. |
| The seventeen collector certificates pass that checker. | Lean kernel `decide`, `all_checked` | `proofs/Cairn/CollectorCertificates.lean`, generated | Anything about the emitted loop. |
| The collector loop model stores in bounds, keeps both cursors representable and selects stably. | Lean, `store_index_lt_capacity`, `increments_fit`, `collect_spec` | `proofs/Cairn/Collector.lean` | That the emitted C++ is this loop. The model uses `Int`/`Nat`, not machine words. |
| An accepted program of the ownership and lease calculus has no use-after-move, use-after-free, double free, leaked ticket, aliased argument or race, under every interleaving and every valuation; never gets stuck; and releases every cell exactly once on normal termination. | Lean, `accepted_no_fault` and the named faults, `accepted_threads_disjoint`, `accepted_frees_each_allocation_once`, `accepted_progress` | `proofs/Cairn/Places.lean`, `proofs/Cairn/Ownership.lean` | Any link to `checking.py`. Single elements, parts of parts, invisible bounds, closures, `lane:f`, placement, `reduce`/`compact`, streams. The part guard and the region join are assumed of the emitter. |
| Two versions of one function agree on the result and on everything they were lent, for every admitted input. | Z3 over a modeled source fragment, `smt-equivalent` | `src/cairn/verify/scalar_semantics.py` | A moving owner, recursion, tasks, lanes, device placement, closures, `dyn`, the foreign boundary, an unbounded trip count, an observed NaN, two views of one array in one call: each is `unknown`. A tag in storage that names no variant aborts, which assumes the emitter's `default: cr::trap()`. Trusts the translator and Z3. |
| Every declared function and public type of two modules was compared that way. | `cairn verify --all`, `smt-module-equivalent` | `src/cairn/verify/verification.py` | One uncovered function keeps the module incomplete. A restricted entry holds only where its precondition does. Size, count and solver budgets apply. |
| Accepted programs build and run under both compilers, four sanitizers, CUDA and QEMU, and pass their finite task contracts. | Executed tests | `tests/` | Finite inputs only. |

```sh
cd proofs && lake build                       # about four seconds, no dependencies
cd proofs && lake env lean Cairn/Audit.lean   # the axiom audit on its own
python3 -m pytest -q tests/verification       # 201 tests, including that build
python3 bin/cairn certificates
python3 bin/cairn verify examples/proof_scope/reference.cairn \
  examples/proof_scope/candidate.cairn --all
```

[proofs/README.md](../../proofs/README.md) has the Lean file layout, the pinned toolchain, the axiom audit and the drift check. [testing.md](testing.md) has the test layers.

## The Lean project

`proofs/` is a dependency-free Lean 4 project. The toolchain is pinned in `proofs/lean-toolchain`, Mathlib is not used, and `lake build` checks it from scratch in about four seconds. There is no `sorry`, no `native_decide` and no added axiom: every audited declaration depends on `propext` and `Quot.sound` at most, and `Classical.choice` appears nowhere, the ownership calculus included.

The certificate module is generated from the Python rules by `tools/checks/export_lean_certificates.py`. A test fails if the two drift, and the compiler receipt reports `lean_verified: true` only while the live bundle hashes to the bundle Lean checked.

None of this establishes that the Python checker implements the Lean `check` (it is a transliteration, reviewed, not extracted), that the emitter's C++ corresponds to the loop model, or anything about the parser, the type checker, placement, effects, the C++ compiler or the machine. What the ownership calculus leaves open has its own list below. SMT results still trust their translator and Z3. Proving one relation does not confer correctness on the rest.

## The collector certificates and the loop model

`compact` lowers to a loop whose one store carries no dynamic bounds check:

```c
k = 0;
for (i = 0; i < n; ++i) { if (pred(i)) { out[k] = proj(i); ++k; } }
used = k;
```

`out` has capacity exactly `n`, and `pred` and `proj` never read `out`. What licenses the unchecked store is the cursor invariant `0 <= k <= i <= n <= M`, where k is emitted outputs, i visited inputs, n capacity and M the largest representable cursor. While `i < n`, the store at k is in range and both increments fit. Emit substitutes `k'=k+1` and `i'=i+1`, skip substitutes `k'=k` and `i'=i+1`, initialization substitutes `i=k=0`, and on exit `k<=n`.

`src/cairn/verify/linear_certificates.py` writes an affine form as five exact integer coefficients over `(1,k,i,n,M)`, read as "this form is nonnegative". A rule fixes premises a_j and a conclusion g. Its certificate gives nonnegative integer multipliers c_j and a nonnegative integer c_0, and the checker accepts only when, coefficient by coefficient:

    g = c_0 + sum_j c_j * a_j

Under any assignment satisfying a_j>=0 every summand is nonnegative, so g>=0 by distributivity and equality of coefficients. The argument is universal over integer assignments, not an enumeration of example states. The implementation rejects Boolean, floating and negative multipliers, malformed dimensions and excessive integer sizes.

The store bound is the exact identity `n-k-1 = (i-k) + (n-i-1)`. The emitted-cursor bound adds `M-n`, giving `M-k-1 = (i-k) + (n-i-1) + (M-n)`.

Seventeen obligations cover initialization, nonnegative and in-range stores, both increments, emit and skip preservation, and output-count boundedness. Some initialization obligations are trivial identities. Seventeen is an obligation count, not seventeen independent research theorems.

The compiler checks the fixed bundle before every emission, and mutation tests confirm that a corrupted bundle blocks compilation. There is no user-editable assumption channel in the source or in the edit protocol. The receipt pins the rules and the checker source by SHA-256; hashes bind bytes, not correctness.

Trusted here: Python's exact integer arithmetic, this small checker's implementation, and the connection between the compiler's cursor operations and the transitions above. There is no mechanized proof of that connection. The certificates do not prove stable-selection behaviour, alias or lifetime safety, memory initialization, the absence of compiler bugs, or native machine behaviour, and they do not authorize arbitrary bounds-check removal.

Lean adds three results. `Cairn.check_sound`: if the checker accepts a rule, the rule's conclusion is nonnegative for every integer assignment that satisfies its assumptions. `Cairn.Collector.all_checked`: all seventeen certificates pass that checker inside Lean, and each named obligation becomes a Lean theorem by applying its certificate. And for an executable model of the loop: the invariant is preserved by emit and skip steps using the certified inequalities, every store index is below the capacity (`store_index_lt_capacity`), both increments stay representable (`increments_fit`), and the result is stable selection, so the written prefix equals `(inputs.filter pred).map proj`, its length is returned and the tail is unchanged (`collect_spec`).

```sh
python3 bin/cairn certificates
python3 -m pytest -q tests/verification/test_linear_certificates.py
```

## Ownership and leases: a mechanized core calculus

`proofs/Cairn/Places.lean` and `proofs/Cairn/Ownership.lean` are a second, independent development: a small ownership-and-lease calculus with an executable checker, an interleaving small-step machine with explicit error states, and checked safety and progress theorems. It is written by hand beside `src/cairn/compiler/checking.py`, not extracted from it, and it is evidence that the rules hang together, not that the implementation obeys them.

### Locals and places

A local is the unit of ownership: it is allocated, moved, dropped and released. A place is what one borrow names, with one constructor per shape `checking.py:path` produces and one per case `checking.py:lend` chooses between.

| Lean | source | what it is |
| --- | --- | --- |
| `whole x` | `d` | the owner itself, header and elements: what an `rw<Buf[T]>` parameter lends, and the only borrow through which a task can replace the cell (`swap`) |
| `hdr x` | the `len(d)` read | the header alone: the length and the identity of the cell (`leased(..., elements=False)`) |
| `elems x` | `d[]` | every element: what an array view `rw<T>[n]` of a whole owner lends |
| `part x lo hi` | `d[lo..hi]` | those elements |

Each place carries a root: a local plus a field path, which is what `path` writes as `r.a.b`. `r.xs[lo..hi]`, `r.xs[]` and `len(r.xs)` are the same four shapes at the root `r.xs`.

A bound is an integer literal or the name of an immutable natural, which is what `path` keeps. Anything else becomes `?` there and is not modelled. The names are read by an arbitrary valuation, and every theorem below quantifies over every valuation, so no result depends on the numbers. Single elements (`a[i]`) and parts of parts are not modelled.

### Disjointness, decided twice

The checker decides overlap syntactically, as `checking.py:overlaps` does. Places of different locals never overlap. The header and the elements do not overlap, which is why `len(d)` stays readable while a view of `d`'s elements is lent, and does not while `d` itself is lent. Two roots meet exactly when they are the same local and one field path is a prefix of the other, so distinct fields of one record are apart and a field and its record are not (`checking.py`'s dotted-prefix test). Two parts of one root are disjoint exactly when one visibly ends at or before the other begins. Everything else overlaps, and `whole x` overlaps every place of `x`.

"Visibly ends at or before" is `reaches`: the same bound, a literal comparison, or a chain through the `lo <= hi` facts of the parts in play. That is the reflexive-transitive closure `checking.py` searches depth-first, saturated one round at a time here.

The machine decides overlap under the valuation: two accesses collide when both touch the header or their index ranges meet. `Ownership.ovl_sound` is the bridge between the two, and the whole part story rests on it.

### The guard, and the trap

A slice is formed by `cr::part(p, lo, hi, n, want)`, which traps unless `lo <= hi`. The real guard also checks `hi <= n` and the length the callee declared; only `lo <= hi` matters to the alias rules, so only it is modelled. The machine performs that guard for every part a statement names, on the spawner's thread, before the call is made or the task is started.

A failed guard is a trap: a fourth configuration beside running, finished and faulted, and a defined abort of the whole configuration, live tasks included, which is what `cr::trap()` does to the process. It is not a fault and not a race. The invariant `TasksGuarded` states that every part a live task holds was guarded, and `inPlay_guarded` adds the place being touched, whose slice is formed now. That is the explicit form of "every chaining fact corresponds to a guard that has already run".

It is also exactly what makes `d[6..3]` safe to accept. That slice orders `d[0..6]` before `d[3..9]`, which overlap, and the program aborts at its spawn before either of those tasks exists (`tests/soundness/test_soundness.py::test_a_backwards_part_used_to_order_two_others_aborts_at_its_spawn`).

### Which facts each rule may chain

The two Python rules use different fact sets, and the model keeps them apart. `leased` chains through every part the live tasks hold, plus the place being touched. `disjoint` chains through the parts of the one argument list it is looking at.

So handing a single call `d[0..a]` and `d[b..n]` is rejected even while `d[a..b]` is lent to a live task, because that argument list contains nothing to order `a` before `b`, while spawning the same two ends one at a time is accepted. Both the Lean checker and `checking.py` behave that way. It is conservative, not unsound.

### Syntax

A program is one scope: a list of declared locals plus a body. Statements are `alloc x` (a fresh heap cell lands in `x`), `mkScalar x` (a copyable scalar), `copy y x`, `move y x`, `drop x` (the implicit release at the exit of an inner scope), `call args` where `args` is a list of `(place, ro|rw)` borrows lent for the duration of the call, `spawn t args` (the borrows stay lent until `wait t`), `wait t`, and `ite thn els` with both branches.

A read of a scalar is `call [(whole x, ro)]`, a write of a part is `call [(part x lo hi, rw)]`, and `let k = len(x);` is `call [(hdr x, ro)]`. Borrows are second class by construction, since a borrow exists only as an entry in one argument list.

### The checker

`Ownership.accepts` is an executable Lean function, not a relation, and its decisions can be evaluated. It carries the state `checking.py` carries in its `Scope`: which locals hold a scalar, which hold an owner, which are dead, and a lease map from a ticket to the borrows it holds.

It enforces: only declared locals; no use of a dead local (moved, dropped, or killed by a join); `copy` only of a scalar; no move, drop or rebinding of a local any of whose places is leased; no read of what a live task writes and no write of what a live task reads or writes, overlap decided by the rule above; no two overlapping borrows with an `rw` among the arguments of one call; a fresh ticket per `spawn`; at a branch join, a local moved on one path is dead afterwards and both branches must agree on which tickets are live; and no live ticket where the scope ends.

### The machine

A small-step semantics over a heap of allocation ids, a liveness map and a per-cell release counter. It is deliberately undefensive, so that the faults are actually reachable for programs the checker rejects. `copy` duplicates whatever bits a local holds, which is how a mis-accepted copy of an owner reaches a double free. Binding a local releases what it held before. A move leaves a ghost mark whose only purpose is to let a later use be named `UseAfterMove`. Nothing in the machine prevents a fault; only the checker does.

The error configurations are `UseAfterMove`, `UseAfterFree`, `DoubleFree`, `Leak` (a live ticket where the scope ends), `Race` and `AliasedArgs`. `Trap` is not one of them.

Concurrency is interleaving, not a sequential approximation. A task's body is abstracted to its footprint: while its ticket is live it may touch any place it was lent, in the mode it was lent, between any two steps of the spawner, any number of times. A task holding the whole owner `rw` may also replace the cell, which is what `swap` through a lent owner does; one holding only elements or a part cannot, since a view cannot replace the storage it views.

`Race` is reached when the spawner and a live task, or two live tasks, touch a common location of one local with a write among them; for two parts that means their index ranges meet under the valuation. `if` has an opaque condition, so both branches are always reachable and the join is what makes a one-sided move dead.

### Parallel regions

`parallel i in n { body }` is a statement whose body is a list of lane accesses, which is exactly what `checking.py:region` keeps in `Lanes.accesses`: for each place of the enclosing scope the body touches, its root, whether the index written there is the binder itself, and whether it writes.

Four shapes are modelled: `x[i]` at the lane's own index; `x[e]` for any other index expression, and any part or view lent on to a call, whose footprint is modelled as every element, the worst case, since nothing bounds where the index lands; the place itself (a shared scalar read or assigned, or a single borrow lent on); and `len(x)`.

The rule is `checking.py`'s: whatever any lane writes may be touched only at `x[i]`, keyed on the root local, not the field path, and a `len` read is not recorded at all (`check_len` never touches `Lanes.accesses`, and the header is not an element). Assigning a shared scalar is `E-PARALLEL-WRITE` there; here it fails the same rule, because such an access writes its local and is not at the binder. Every lane access is checked against the leases of the enclosing scope under the place `where` writes, which for any index is `x[]`, so a region beside a task holding any part of the same array is rejected, conservatively, exactly as `checking.py` rejects it.

What a lane cannot do is an absence rather than a rule: a body is a list of accesses, so a region cannot nest, `return`, move an outer owner or run a collector, and a local the body declares is not a place of the enclosing scope and does not appear.

`lanesOf n body` builds one thread per index below `n`, holding `part x [k, k+1)` where the body says `x[i]` and the worst case elsewhere. Lanes have the same shape as tasks and step by the same rule, so `stepThread` serves both, and the invariant's three clauses about live threads range over `tasks ++ lanes` together. That single `Pairwise` is what says no two threads conflict, whether they are two tasks, a task and a lane, or two lanes.

The spawner is blocked while a region runs, since `stepHost` offers it nothing but ending the region, which is what "completes before the next statement" means. Tasks spawned earlier keep running throughout and their leases are respected, because a lane's accesses were checked against them at the region. Race freedom between two lanes is `meets_own_element`: what any lane writes, every lane touches only at its own index, and two indices differ.

### The theorems

Every statement below is proved in `proofs/Cairn/`, audited in `proofs/Cairn/Audit.lean` and required by `tests/verification/test_lean_proofs.py`. `Reach ρ p.scope (Cfg.start p) cfg` means "`cfg` is reachable from the initial configuration of `p` under the interleaving semantics, when the immutable bounds are worth what `ρ` says". The valuation is implicit in the theorems and universally quantified: an accepted program is safe for every one.

```lean
theorem reaches_sound {ρ : Valuation} {facts : List Fact} (hf : FactsTrue ρ facts)
    {x goal : Bound} (h : reaches facts x goal = true) : x.eval ρ ≤ goal.eval ρ

theorem ovl_sound {ρ : Valuation} {inPlay : List Place}
    (hg : ∀ p ∈ inPlay, p.guard ρ = true) {r s : Place}
    (h : ovl inPlay r s = false) : meets ρ r s = false

theorem accepted_no_fault {p : Program} (hp : accepts p = true) {ρ : Valuation} {cfg : Cfg}
    (hr : Reach ρ p.scope (Cfg.start p) cfg) (e : Err) : cfg ≠ Cfg.err e

theorem accepted_no_use_after_move {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (q : Var) :
    cfg ≠ Cfg.err (.useAfterMove q)

theorem accepted_no_use_after_free {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (a : AllocId) :
    cfg ≠ Cfg.err (.useAfterFree a)

theorem accepted_no_double_free {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (a : AllocId) :
    cfg ≠ Cfg.err (.doubleFree a)

theorem accepted_race_free {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (q : Var) :
    cfg ≠ Cfg.err (.race q)

theorem accepted_threads_disjoint {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {code : List Stmt} {tasks lanes : List Task} {st : State}
    (hr : Reach ρ p.scope (Cfg.start p) (.run code tasks lanes st)) :
    (tasks ++ lanes).Pairwise (NoRacePair ρ)

theorem lanesOf_pairwise (ρ : Valuation) : ∀ (n : Nat) {body : List Touch},
    laneRule body = true → (lanesOf n body).Pairwise (NoRacePair ρ)

theorem accepted_no_leaked_ticket {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) (t : Ticket) :
    cfg ≠ Cfg.err (.leak t)

theorem accepted_no_aliased_args {p : Program} (hp : accepts p = true) {ρ : Valuation}
    {cfg : Cfg} (hr : Reach ρ p.scope (Cfg.start p) cfg) : cfg ≠ Cfg.err .aliasedArgs

theorem accepted_frees_each_allocation_once {p : Program} (hp : accepts p = true)
    {ρ : Valuation} {st : State} (hr : Reach ρ p.scope (Cfg.start p) (Cfg.done st)) :
    (∀ a, st.live a = false) ∧ (∀ a, a < st.next → st.frees a = 1)

def Progress : Prop :=
  ∀ (p : Program) (ρ : Valuation), accepts p = true →
    ∀ cfg, Reach ρ p.scope (Cfg.start p) cfg →
      (∃ st, cfg = Cfg.done st) ∨ cfg = Cfg.trap ∨ succ ρ p.scope cfg ≠ []

theorem accepted_progress : Progress
```

The race theorem is proved for the whole calculus, not for a fragment. It rests on `Ok_succ`, the preservation lemma: every successor of a configuration satisfying the invariant satisfies it too, the invariant is `False` on error configurations, and it is trivially true of a trap.

The release theorem is about normal termination only. A run that traps has aborted, and nothing is claimed about the cells it leaves behind.

`accepted_progress` is cheap on its own, because the machine is total by construction and every statement has at least one successor. Its content is that the stuck configurations are exactly the errors, which `accepted_no_fault` excludes. It replaces the `OpenGoal.Progress` that earlier versions of this file left unproved.

### The regression, and that the faults are reachable

`ownership_regression` is the executable sanity check: the Lean encodings of the CAIRN programs pinned in `tests/soundness/test_soundness.py` and `tests/soundness/test_concurrency.py` are classified the way the Python checker classifies them.

Rejected: a move beside a view a task still holds, a leased read, a double move, an unawaited ticket, two arguments of one call overlapping with a write, a place moved on one path only, branches that disagree about live tickets, two tasks writing one place, two tasks writing parts that really overlap (`d[0..6]` and `d[3..9]`), two parts with nothing lent between them to order their bounds, one call handed two overlapping parts, a `len` read of an owner a task may replace, a copy of an owner, and a use after the implicit release.

Accepted: the two-part and K-way splits (`d[0..a]`, `d[a..b]`, `d[b..n]`), the backwards part that orders two others, a `len` read under a lease of the elements, shared read-only lending, a move on both paths, and a scalar copy.

Each of those classifications was re-checked against the Python checker on the corresponding CAIRN source while this model was written. The build prints `ownership-regression: pass`, and the Python gate asserts on that line.

The safety theorems would be vacuous if the machine could never fault, so a fault is also shown reachable for programs the checker rejects, each by exhibiting the step sequence rather than by a tactic. `leasedRead_races`, `overlappingTasks_races`, `overlappingParts_races`, `fieldsToTwoTasks_races`, `laneWritesFixedIndex_races`, `laneWritesShared_races` and `laneReadsOther_races` drive the machine to `Race`; `fieldPartsOverlapInOneCall_aliases` to `AliasedArgs`; `copyAnOwner_doubleFrees` to `DoubleFree`; `useAfterDrop_usesDeadPlace` to `UseAfterMove`; and `unawaitedTicket_leaks` to `Leak`. `witnesses_are_rejected` confirms that the checker rejects all eleven. `UseAfterFree` is the one error configuration with no witness: no program here is shown to reach it.

`backwardsPart_traps` is the other side of the same coin. That program is accepted, and under the valuation it is written for (`a = 6`, `b = 3`, `n = 9`) the machine reaches `Trap` at the guard of `d[6..3]`, not a race. Together with `ownership_regression`, which rules out a checker that says no to everything, that pins the result from both sides.

### What this does NOT cover

* Any connection to `checking.py`. The Lean checker is a hand-written abstraction of the Python rules. It is not extracted from them, not compared against them by a test, and the Python checker does many things this model does not.
* The guard is an assumption about the emitter, not a theorem. The chaining rule is sound in this calculus because the machine performs the `lo <= hi` guard where the slice is formed, before the borrow is taken. That the emitted C++ does the same, `cr::part` at the call site, on the spawning thread, before the task starts, is asserted by `src/cairn/compiler/codegen.py`, `src/cairn/runtime/cairn_owners.hpp` and a test that pins exactly this (`tests/soundness/test_concurrency.py::test_a_part_is_guarded_by_the_spawner_before_its_task_exists`: the guard sits in the capture list of the task's lambda, and a backwards part aborts after `spawning` is printed and before the task or the next statement runs). It is not asserted by any proof. If a part guard ever moved into the task, the rule would be unsound and this model would no longer describe the language.
* Single elements, parts of parts and invisible bounds. `a[i]`, `a[lo..hi][j..k]` and any bound `path` writes as `?` are outside the model. `checking.py` treats them conservatively, so everything of that base overlaps; nothing here proves that it does.
* A field is reached through its record, and that read is a separate rule. `checking.py:e_name` runs `leased(box, "ro")` on the base local before `overlaps` ever sees `box.a`, so a lease on one field pins the whole record: `let l = spawn fill(len(box.a), box.a, 0); let r = spawn fill(len(box.b), box.b, 1);` is `E-LEASED` ("box is lent to l"), although `box.a` and `box.b` are disjoint places. The disjointness of two fields therefore only bites inside one argument list, where no base read intervenes (`both(6, box.xs[0..6], box.a[3..9])` is accepted). The model writes that base read out as an explicit `call [(whole box, ro)]` in the regression programs rather than building it into the field rule, and nothing proves that `checking.py` performs it exactly where the model does.
* Everything a region is besides its accesses. `lane:f` callbacks and the `E-PARALLEL-CALL` effect rule, device placement and `E-PLACEMENT`, `reduce`, `compact`, queued device work and `after`, and the value a lane computes are all outside the model. A lane body is its footprint; the model does not say what a lane-private local holds, only that it is not a place of the enclosing scope. A lane's index expression other than the binder is modelled as the whole element footprint, so the model calls a race what `parallel i in n { x[i + 1] = 0; }` would not actually have. The checker rejects that program either way, and nothing here claims the converse.
* That a region really completes before the next statement. The machine blocks the spawner while lanes are live. That the emitted `cr::par::run`, or a CUDA launch and its synchronize, joins every lane before returning is asserted by `src/cairn/compiler/codegen.py` and the runtime, and tested, not proved. It is the same kind of assumption as the part guard.
* Closure captures. `checking.py:disjoint` also compares a closure's captured places against the call's arguments, and a captured part contributes its own bounds to the chain before its slice has been formed. Closures are not modelled at all, so that case is neither proved nor refuted here.
* Device streams. `e_spawn` hides the leases of tickets named in `after`, because queued device work runs after them on one stream. There are no streams in this model, so that exemption is absent: the calculus would reject those programs.
* Linear values other than tickets, `defer`, `take` and `swap` as statements, loops, `return`, `break` and `continue`, closures, traits, generics, placement, atomics, mutexes, effects and the foreign boundary. None of them appear.
* Callee bodies. A call is its footprint. `AliasedArgs` is detected, not caused: the model does not say what a callee would do with two overlapping views, only that the checker never hands it any.
* Value-level behaviour. A task's write does not change the abstract value at a place; only a replacement of the cell does. The race result is the absence of conflicting concurrent access, not determinism of results.
* Machine reality. No native memory model, no allocator, no C++ and no thread semantics. `Race` is a property of this abstract machine, not of hardware, and `Trap` is a configuration of it, not a signal.
* What a trap leaves behind. A trapped run makes no claim about released cells; it is an abort.

## Value-level source equivalence

`src/cairn/verify/scalar_semantics.py` is a symbolic evaluator over a source fragment, and Z3 answers whether two versions of one function can differ. It models exact-width integer and Boolean values, `f32`/`f64`, records, tag-only enums and payload sums with `match` and `try`, array views (`ro<T>[n]`, `rw<T>[n]`) and the parts `x[lo..hi]` taken from them, single borrows of values, fixed local storage (`stack x:T[N] = zeroed;` and `Array[T, N]` locals), function-local heap owners (`buffer x:T[n] = zeroed;`, `Buf[T](n)`), `compact`, host `reduce`, checked and wrapping operations, explicit conversions, assignment to a name, a field or an element, branching, short-circuiting, bounded `for` and `while` with `break` and `continue`, early return, `void` results and acyclic calls.

An observation is the returned value together with the final contents of every `rw` parameter, or one undifferentiated abort. An owner that moves (`take`, `swap`, a returned, stored or by-value owner), recursion, tasks, lanes, device placement, closures, `dyn` dispatch, atomics and the foreign boundary are unsupported: each returns unknown with the reason that refused it.

### Views

A view parameter is one SMT array per scalar component of its element, read and written at `offset + i` while `i` is below the extent its signature names. The extent is a literal or an earlier `usize` parameter, so `xs` and `out` of `fn f(n:usize, xs:ro<T>[n], out:rw<T>[n])` share one extent term. `len(xs)` is that extent, an index at or above it is the trap `cr::at` takes, and a part `x[lo..hi]` shifts the window under the one guard `cr::part` applies (`lo <= hi <= len`, and `hi - lo` equal to the extent the callee declares).

The admitted inputs are the ones the emitted entry guards admit. `cr::view` says each view is a valid, aligned region of its extent, and `cr::disjoint` says the storage behind an `rw` view is distinct from the storage behind every other view of the same call, which is why two `rw` views are modeled as independent arrays. Read-only views may alias, and independent arrays of equal contents cover that case, so the quantification is over more inputs than a caller can build, never fewer. A single `rw` borrow of a value has no runtime guard: its independence is the `E-ALIAS` rule every call site obeys.

An `rw` view is observed element by element over its whole extent, so the solver is asked for an index inside the extent where the two runs differ.

A view whose elements carry a tag is admitted with whatever tag each element holds, because no entry guard reads a tag inside storage. A `match` over one that names no variant leaves no surviving path, so the call aborts. That is an assumption about the emitter, tested rather than proved, of the same kind as the part guard: `codegen.py` closes every emitted `switch` with `default: cr::trap();`, and `tests/verification/test_semantics.py` compares the two sides on such a tag rather than deriving the trap from the C++. Two views of one array in one call, the visibly disjoint `b[0..mid]`, `b[mid..n]` split the checker allows, are unknown, since the model updates each argument's storage independently. A local `buffer` or `Buf` is zeroed storage of its own; allocation failure is outside the model, and an owner that leaves its place is not modeled at all.

### Collectors and reductions

`compact` is unrolled as the host emits it: the predicate once per input, the projection only where it selects, the store at the running count (unguarded, as the certificates license), the tail untouched, and the count returned.

`reduce` is the in-order host fold from the identity the emitter uses, offered for `add_wrap`, `mul_wrap`, `&`, `|`, `^`, `min`, `max` and the checked unsigned `+`, all of which give the same result and the same trap in any order. A float reduction, whose order is unspecified, is unknown.

### Values

A value is flattened into its scalar components: a record is its fields in declaration order, a sum is the emitted `std::uint32_t` tag beside every variant payload, an inline array is its elements. Two values look alike when their components do, except that a sum compares its tag and the payload of the active variant only, so storage the emitter never reads is not an observation.

Quantification is over well-formed values: every tag of a value parameter names a declared variant, which is exactly what the emitted entry guard `if (tag >= n) cr::trap();` admits. Storage behind a view passes no such guard, so its elements carry any tag, and a `match` over one that names no variant aborts instead. A record or sum named in the signature must have the same definition on both sides, or the answer is `invalid-contract` rather than a proof about two different types with one name.

### Traps

Checked `+ - *`, division and remainder by zero or signed-minimum over minus one, narrowing and sign-changing conversions, shift counts at or above the width, the array bounds guard and the float-to-integer guard all abort. Every abort is one undifferentiated observation, so "both trap" counts as equal behaviour. By default the reference must additionally return on every admitted input, and `allow_reference_traps` is the explicit opt-out that compares return against abort.

### Floating point

`f32` and `f64` use Z3's FloatingPoint theory. Every operation rounds to nearest even exactly once, which is the contract the compiler builds with (`-ffp-contract=off -fno-fast-math`, `--fmad=false` on the device): no contraction and no reassociation are modeled, because none is authorized.

Comparisons are the IEEE predicates, so a `NaN` operand compares false and `+0.0 == -0.0`. Equality of results is instead equality of the IEEE datum, so returning `-0.0` is not the same function as returning `+0.0`. `cr::truncate` is modeled as the header writes it, including the `low - F(1)` boundary it uses where that value is not representable, so NaN and out-of-range values trap. Integer-to-float conversion rounds once, which for `f32` is not always what rounding through `f64` would give.

Two modeling assumptions are stated rather than proved: that the C++ compiler rounds a decimal literal to nearest even, and that the host evaluates `float` arithmetic in `float` (`FLT_EVAL_METHOD == 0`, which the 64-bit target profile gives).

### NaN

The theory has exactly one NaN and so cannot speak about payload bits, while the emitted C++ returns whatever bit pattern the machine produced and a foreign caller may read it. Rather than assume an equality the model cannot justify, the checker asks one more question before accepting: if an observed float, at any depth of the returned value or at any index of an `rw` view, may be NaN on an admitted input, the answer is `unknown`, never `smt-equivalent`. A caller who does not care excludes them with a precondition (`assume="x==x"`), which is a caller obligation like every other one here. NaN inputs are ordinary admitted values.

### Loops

`for`, `while`, `compact` and `reduce` are unrolled up to 16 iterations, and what would still be running after that becomes an obligation of its own: Z3 is asked whether any admitted input reaches a seventeenth iteration, and anything but "no" is `unknown`. `break` leaves the loop, `continue` rejoins the increment, and mutually exclusive paths are merged at the end of each iteration so a branching body costs iterations rather than powers of two. A caller's path enters the functions it calls, so a callee's trip count is bounded by what reaches it.

An extent is symbolic, so a pass over `0..n` is unknown until a precondition bounds `n` within that budget; a literal extent needs none. Sixteen unrolled iterations over storage is also where the solver budget bites: the query grows with the bound and with the width of the elements, and a timeout is `unknown`, never a pass.

### Evaluation order

Traps are order-insensitive here, because one abort is the same observation wherever it happens. A `try` is not: it returns. So `try` is modeled only as the whole right-hand side of a binding, an assignment, a return or an expression statement, and a `try` written as an operand of a larger expression is unknown, because C++ leaves the order of those operands unspecified and the answer would depend on it.

### The query

The fixed host-owned reference R, candidate C and domain D produce definedness/value pairs (d_R,v_R), (d_C,v_C), where v is the observation above. The checker requires a well-defined nonempty domain, a complete unrolling, and by default a reference that returns on all admitted inputs. It asks Z3 whether this is satisfiable:

    D && ((d_R != d_C) || (d_R && d_C && v_R != v_C))

Unsatisfiable, with no reachable NaN in what is observed, means `smt-equivalent` in the named source model.

A satisfiable counterexample is independently replayed through an operational evaluator written against Python values before rejection. Storage a counterexample lends is read back by naming the first 16 elements of every view in the query, so the witness carries the contents that separate the two; a mismatch that needs a longer view than that is reported unknown rather than shown unreplayed.

Unavailable tools, translation errors, unsupported syntax, budget exhaustion, mismatched replay and solver timeouts are unknown, never acceptance. Domain restrictions are caller obligations, not guards automatically inserted by the native builder. The shipped module demo uses all declared inputs.

Source identity, translator identity, query hashes, solver version and outcomes are recorded. There is no checked proof reconstruction into Lean, and no theorem relates this translator to native output. A wrong reference can still express the wrong human requirement.

## Whole-module coverage, not selected-function promotion

```sh
python3 bin/cairn verify examples/proof_scope/reference.cairn \
  examples/proof_scope/candidate.cairn --all
```

The coverage checker parses and checks both complete sources, compares public records, enums and sums, and enumerates every function on both sides. Missing or extra entries, unsupported functions, mismatched types, partial references or any undecided obligation prevent `smt-module-equivalent`. Each entry retains its own result, and the receipt lists covered and uncovered functions. A tagged result is covered when its payloads are modeled values, and one uncovered function keeps the whole module incomplete.

`--all` passes the precondition a caller gives for a function and nothing otherwise, so a function whose trip count depends on a symbolic extent stays uncovered until one bounds it. `--assume` supplies them, one per function, and repeats:

```sh
python3 bin/cairn verify reference.cairn candidate.cairn --all --assume 'total=n<=4'
```

A restricted entry is equivalent only where its precondition holds, so the receipt keeps every text under `preconditions`, each entry keeps its own `assume`, and the module's `domain` names them: "restricted by a host precondition where one is given (total only where n<=4)". Reading that receipt as plain equivalence is reading past the domain it quantifies over. A precondition that names no declared function is refused rather than ignored, one that admits no input is `invalid-domain`, and a function with none is still compared over the whole domain. There is a 64000-byte limit per input, at most 128 functions, and a soft 30-second solver budget. That budget is not a security sandbox or a strict wall-clock bound on all compilation.

`examples/proof_scope/mixed.cairn` is a negative coverage fixture: comparison with itself must remain incomplete, because it contains a function that moves an owner out of its place, which the model does not follow. Empty coverage is not success. Public-type changes also block aggregate acceptance even if numeric function results agree.

## Tests, native code and failures

Unit tests, independent Python behaviour oracles, both native compilers, instrumented allocation and release observations, ASan, UBSan and LSan, and object comparisons provide finite executed evidence. [testing.md](testing.md) has the commands.

Instrumented lifetime counters run at O0 and do not establish optimized allocation counts. Production allocation-limit and invalid-access fixtures must terminate by SIGABRT. `testing.evaluate` requires both a passing behavioural report and a successful child exit, so a process cannot print a pass and then crash into a successful receipt.

Runtime address-space and CPU limits are protections against some runaway executions, not isolation. Native section equality is code-identity evidence under one compiler and flag profile, not a universal correctness or latency theorem. A new implementation may typecheck, pass examples and still be incorrect or slower on untested inputs.

## Tested, not proved: ownership, lanes, tasks and placement as implemented

The 1.0 rules as `checking.py` actually implements them are outside the calculus above: affine and linear values, second-class borrows, leases over parts whose bounds are not visible, the effect rules a lane's callees obey, placement and the effect fixed point. Those are exercised by acceptance and rejection tests, and nothing connects them mechanically to that calculus.

Accepted programs run natively under both compilers and under AddressSanitizer, UndefinedBehaviorSanitizer, LeakSanitizer and ThreadSanitizer. Device guards are exercised by death tests that must abort the host. These are finite executions, not theorems: a sanitizer-clean run shows the absence of those faults on those inputs only.

Generic code is checked per instance, so an uninstantiated template is unchecked and is listed in the receipt rather than trusted.

## Formal completion gate

Three formal steps remain.

Prove that the emitted collector loop refines the Lean model, or generate it from the model.

Extend the ownership calculus past the places it now has, to single elements, parts of parts, closures, `lane:f` callbacks and placement. Then prove of the emitter what that calculus assumes of it: that a part's `lo <= hi` guard runs on the spawning thread before the task that borrows it starts.

Relate `checking.py` to the calculus by something stronger than review. Today the Lean checker is an abstraction written by hand beside the Python one, not extracted from it.

Only a pinned Lean build with audited axioms may be called Lean verification. The receipt field above is the single place the compiler says so, and it is scoped to the certificate bundle.
