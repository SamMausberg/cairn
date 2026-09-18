# Verification boundaries and exact arithmetic certificates

## What is proved, and what is not

There is still no whole-compiler proof. There are now two real Lean results. `proofs/` is a dependency-free Lean 4 project (toolchain pinned in `proofs/lean-toolchain`; no Mathlib) that `lake build` checks in about two seconds. It proves, with no `sorry`, no `native_decide` and no added axioms (every audited declaration depends on `propext` and `Quot.sound` only; the ownership calculus does not even use `Classical.choice`):

- `Cairn.check_sound`: if the certificate checker accepts a rule, the rule's conclusion is nonnegative for every integer assignment that satisfies its assumptions.
- `Cairn.Collector.all_checked`: all seventeen collector certificates pass that checker inside Lean, and each named obligation is a Lean theorem obtained by applying its certificate.
- For an executable model of the collector loop: the invariant `0 <= k <= i <= n` is preserved by emit and skip steps (using the certified inequalities), every store index is below the capacity (`store_index_lt_capacity`), both increments stay representable (`increments_fit`), and the result is stable selection: the written prefix equals `(inputs.filter pred).map proj`, its length is returned, and the tail is unchanged (`collect_spec`).
- For a hand-written core calculus of ownership and leases over whole places: if the Lean checker accepts a program, then under every interleaving of the spawner with its tasks no execution reaches a use-after-move, a use-after-free, a double free, a leaked ticket, an aliased call argument or a data race, and on normal termination every cell the run allocated has been released exactly once. The section "Ownership and leases" below states exactly what that model contains and what it leaves out.

The Lean file of certificates is generated from the Python rules by `tools/export_lean_certificates.py`; a test fails if they drift, and the compiler receipt reports `lean_verified: true` only while the live bundle hashes to the bundle Lean checked. What this does **not** establish: that the Python checker implements the Lean `check` (it is a transliteration, reviewed, not extracted), that the emitter's C++ corresponds to the loop model, that `checking.py` implements the Lean ownership calculus (that model is written by hand and nothing connects the two mechanically), or anything about the parser, type checker, lanes, placement, effects, the C++ compiler or the machine. SMT results still trust their translator and Z3. Proving one relation does not confer correctness on the rest.

## Bounded collector: checked affine implications

Let k be emitted outputs, i visited inputs, n capacity, and M the largest representable cursor. The intended loop invariant is 0 <= k <= i <= n <= M. While i < n, an output store at k is in range and both increments fit. Emit substitutes k'=k+1 and i'=i+1; skip substitutes k'=k and i'=i+1. Initialization substitutes i=k=0. On exit, k<=n.

`linear_certificates.py` represents an affine form by five exact integer coefficients for (1,k,i,n,M), interpreted as nonnegative. A rule fixes premises a_j and conclusion g. Its certificate gives nonnegative integer multipliers c_j and a nonnegative integer c_0. The checker accepts only when coefficient-wise:

    g = c_0 + sum_j c_j * a_j

For any assignment satisfying a_j>=0, every summand is nonnegative. Distributivity and equality of coefficients give g>=0. That is the mathematical soundness argument. It is universal over integer assignments, not an enumeration of example states. The implementation rejects Boolean/floating/negative multipliers, malformed dimensions and excessive integer sizes.

For example, the store bound follows by the exact identity:

    n-k-1 = (i-k) + (n-i-1).

The emitted-cursor bound adds M-n:

    M-k-1 = (i-k) + (n-i-1) + (M-n).

Seventeen obligations cover initialization, nonnegative/in-range stores, both increments, emit/skip preservation and output-count boundedness. Some initialization obligations are trivial identities. Seventeen is an obligation count, not seventeen independent research theorems. The compiler checks the fixed bundle before every emission; mutation tests confirm that a corrupted bundle blocks compilation. There is no user-editable assumption channel in the source or edit protocol.

The receipt pins the rules and checker source by SHA-256. Hashes bind bytes, not correctness. Trusted assumptions include Python exact integer arithmetic, this small checker's implementation, and the connection between the compiler's cursor operations and the stated transitions. There is no mechanized proof of that connection. The certificate does NOT prove stable-selection behavior, alias/lifetime safety, memory initialization, absence of compiler bugs, or native machine behavior. It does not authorize arbitrary bounds-check removal.

Run:

```sh
python3 bin/cairn certificates
python3 -m pytest -q tests/test_linear_certificates.py
```

## Ownership and leases: a mechanized core calculus

`proofs/Cairn/Ownership.lean` is a second, independent Lean development: a small ownership-and-lease calculus with an executable checker, an interleaving small-step machine with explicit error states, and a checked soundness theorem. It is written by hand. Nothing extracts it from `src/cairn/checking.py` and nothing mechanically relates the two; it is a statement about the calculus below, offered as evidence that the *rules* hang together, not that the implementation obeys them.

### What is modelled

**Places are atomic names.** Two places overlap exactly when they are equal. Fields, array elements, array parts and the `overlaps` chain in `checking.py` that licenses a K-way split of one buffer (`d[0..a]`, `d[a..b]`, `d[b..n]`) are **not** modelled; in the Lean model the split appears as two different places.

**Syntax.** A program is one scope: a list of declared places plus a body. Statements are `alloc x` (a fresh heap cell lands in `x`), `mkScalar x` (a copyable scalar), `copy y x`, `move y x`, `drop x` (the implicit release at the exit of an inner scope), `call args` where `args` is a list of `(place, ro|rw)` borrows lent for the duration of the call, `spawn t args` (the borrows stay lent until `wait t`), `wait t`, and `ite thn els` with both branches. A read of a place is `call [(x, ro)]` and a write is `call [(x, rw)]`; borrows are second class by construction, since a borrow exists only as an entry in one argument list.

**The checker** (`Ownership.accepts`) is an executable Lean function, not a relation, and its decisions can be evaluated. It carries the state `checking.py` carries in its `Scope`: which places hold a scalar, which hold an owner, which are dead, and a lease map from a ticket to the borrows it holds. It enforces: only declared places; no use of a dead place (moved, dropped, or killed by a join); `copy` only of a scalar; no move, drop or rebinding of a leased place; no read of what a live task writes and no write of what a live task reads or writes; no two overlapping borrows with an `rw` among the arguments of one call; a fresh ticket per `spawn`; at a branch join, a place moved on one path is dead afterwards and both branches must agree on which tickets are live; and no live ticket where the scope ends.

**The machine** is a small-step semantics over a heap of allocation ids, a liveness map and a per-cell release counter. It is deliberately undefensive, so that the faults are actually reachable for programs the checker rejects: `copy` duplicates whatever bits a place holds (which is how a mis-accepted copy of an owner reaches a double free), binding a place releases what it held before, and a move leaves a ghost mark whose only purpose is to let a later use be *named* `UseAfterMove`. Nothing in the machine prevents a fault; only the checker does. The error configurations are `UseAfterMove`, `UseAfterFree`, `DoubleFree`, `Leak` (a live ticket where the scope ends), `Race` and `AliasedArgs`.

**Concurrency is modelled as interleaving, not as a sequential approximation.** A task's body is abstracted to its footprint: while its ticket is live it may touch any place it was lent, in the mode it was lent, between any two steps of the spawner, any number of times. A task that holds a place `rw` may also replace the cell in it, which is what `swap` through a lent owner does. `Race` is an error configuration reached when the spawner and a live task, or two live tasks, touch one place with a write among them. `if` has an opaque condition, so both branches are always reachable and the join is what makes a one-sided move dead.

### The theorems

Every statement below is proved in `proofs/Cairn/Ownership.lean`, audited in `proofs/Cairn/Audit.lean` and required by `tests/test_lean_proofs.py`. `Reach p.scope (Cfg.start p) cfg` means "`cfg` is reachable from the initial configuration of `p` under the interleaving semantics".

```lean
theorem accepted_no_fault {p : Program} (hp : accepts p = true) {cfg : Cfg}
    (hr : Reach p.scope (Cfg.start p) cfg) (e : Err) : cfg ≠ Cfg.err e

theorem accepted_no_use_after_move {p : Program} (hp : accepts p = true) {cfg : Cfg}
    (hr : Reach p.scope (Cfg.start p) cfg) (q : Place) : cfg ≠ Cfg.err (.useAfterMove q)

theorem accepted_no_use_after_free {p : Program} (hp : accepts p = true) {cfg : Cfg}
    (hr : Reach p.scope (Cfg.start p) cfg) (a : AllocId) : cfg ≠ Cfg.err (.useAfterFree a)

theorem accepted_no_double_free {p : Program} (hp : accepts p = true) {cfg : Cfg}
    (hr : Reach p.scope (Cfg.start p) cfg) (a : AllocId) : cfg ≠ Cfg.err (.doubleFree a)

theorem accepted_race_free {p : Program} (hp : accepts p = true) {cfg : Cfg}
    (hr : Reach p.scope (Cfg.start p) cfg) (q : Place) : cfg ≠ Cfg.err (.race q)

theorem accepted_no_leaked_ticket {p : Program} (hp : accepts p = true) {cfg : Cfg}
    (hr : Reach p.scope (Cfg.start p) cfg) (t : Ticket) : cfg ≠ Cfg.err (.leak t)

theorem accepted_no_aliased_args {p : Program} (hp : accepts p = true) {cfg : Cfg}
    (hr : Reach p.scope (Cfg.start p) cfg) : cfg ≠ Cfg.err .aliasedArgs

theorem accepted_frees_each_allocation_once {p : Program} (hp : accepts p = true) {st : State}
    (hr : Reach p.scope (Cfg.start p) (Cfg.done st)) :
    (∀ a, st.live a = false) ∧ (∀ a, a < st.next → st.frees a = 1)
```

The race theorem is proved for the whole calculus, not for a fragment. It rests on `Ok_succ`, the preservation lemma: every successor of a configuration satisfying the invariant satisfies it too, and the invariant is `False` on error configurations.

`ownership_regression` is the executable sanity check: the Lean encodings of the CAIRN programs pinned in `tests/test_soundness.py` and `tests/test_concurrency.py` are classified the way the Python checker classifies them — a move beside a view a task still holds, a leased read, a double move, an unawaited ticket, two arguments of one call overlapping with a write, a place moved on one path only, branches that disagree about live tickets, two tasks writing one place, a copy of an owner and a use after the implicit release are all **rejected**; the disjoint two-task split, shared read-only lending, a move on both paths and a scalar copy are **accepted**. The build prints `ownership-regression: pass`, and the Python gate asserts on that line.

### What this does NOT cover

* **Any connection to `checking.py`.** The Lean checker is a hand-written abstraction of the Python rules. It is not extracted from them, not compared against them by a test, and the Python checker does many things this model does not.
* **Array parts, fields and element views.** Places are atomic. The `overlaps`/`disjoint` reasoning about `b[0..mid]` and `b[mid..n]`, the "part of a part" rule, and the distinction between lending an owner and lending its elements (`len(d)` staying readable) are all outside the model.
* **Linear values other than tickets, `defer`, `take`/`swap`, loops, `return`, `break`/`continue`, closures, traits, generics, lanes, placement, atomics, mutexes, effects and the foreign boundary.** None of them appear.
* **Callee bodies.** A call is its footprint. `AliasedArgs` is detected, not caused: the model does not say what a callee would do with two overlapping views, only that the checker never hands it any.
* **Value-level behaviour.** A task's write does not change the abstract value at a place (only a replacement of the cell does). The race result is the absence of conflicting concurrent access, not determinism of results.
* **Machine reality.** No native memory model, no allocator, no C++ and no thread semantics. `Race` is a property of this abstract machine, not of hardware.
* **Progress.** The result is safety. `Ownership.OpenGoal.Progress` states, as a named `def ... : Prop` that is deliberately left unproved (it is not an axiom and not a `sorry`), that an accepted program never gets stuck. Nothing in the file proves it.

## Value-level source equivalence

The symbolic evaluator models exact-width integer and Boolean values, `f32`/`f64`, records, tag-only enums and payload sums with `match` and `try`, fixed local storage (`stack x:T[N] = zeroed;` and `Array[T, N]` locals) indexed under its bounds guard, checked and wrapping operations, explicit conversions, assignment to a name, a field or an element, branching, short-circuiting, bounded `for`/`while` with `break` and `continue`, early return and acyclic calls of value parameters (a `ro` single borrow reads as its value). An observation is either return(value) or an undifferentiated abort. Heap owners (`buffer`, `Buf`, `Dyn`), views passed as parameters, `rw` borrows, recursion, tasks, lanes, closures, `dyn` dispatch, atomics, the foreign boundary and void results are unsupported: each returns unknown with the reason that refused it.

**Values.** A value is flattened into its scalar components: a record is its fields in declaration order, a sum is the emitted `std::uint32_t` tag beside every variant payload, an inline array is its elements. Two values look alike when their components do, except that a sum compares its tag and the payload of the *active* variant only, so storage the emitter never reads is not an observation. Quantification is over well-formed values: every tag names a declared variant, which is exactly what the emitted entry guard `if (tag >= n) cr::trap();` admits. A record or sum named in the signature must have the same definition on both sides, or the answer is `invalid-contract` rather than a proof about two different types with one name.

**Traps.** Checked `+ - *`, division and remainder by zero or signed-minimum over minus one, narrowing and sign-changing conversions, shift counts at or above the width, the array bounds guard and the float-to-integer guard all abort. Every abort is one undifferentiated observation, so "both trap" counts as equal behaviour; by default the reference must additionally return on every admitted input, and `allow_reference_traps` is the explicit opt-out that compares return against abort.

**Floating point.** `f32` and `f64` use Z3's FloatingPoint theory. Every operation rounds to nearest even exactly once, which is the contract the compiler builds with (`-ffp-contract=off -fno-fast-math`, `--fmad=false` on the device): no contraction and no reassociation are modeled, because none is authorized. Comparisons are the IEEE predicates, so a `NaN` operand compares false and `+0.0 == -0.0`; equality of *results* is instead equality of the IEEE datum, so returning `-0.0` is not the same function as returning `+0.0`. `cr::truncate` is modeled as the header writes it, including the `low - F(1)` boundary it uses where that value is not representable, so NaN and out-of-range values trap. Integer-to-float conversion rounds once, which for `f32` is not always what rounding through `f64` would give. Two modeling assumptions are stated rather than proved: that the C++ compiler rounds a decimal literal to nearest even, and that the host evaluates `float` arithmetic in `float` (`FLT_EVAL_METHOD == 0`, which the 64-bit target profile gives).

**NaN.** The theory has exactly one NaN and so cannot speak about payload bits, while the emitted C++ returns whatever bit pattern the machine produced and a foreign caller may read it. Rather than assume an equality the model cannot justify, the checker asks one more question before accepting: if a returned float (at any depth of the returned value) may be NaN on an admitted input, the answer is `unknown` with that input, never `smt-equivalent`. A caller who does not care excludes them with a precondition (`assume="x==x"`), which is a caller obligation like every other one here. NaN *inputs* are ordinary admitted values.

**Loops.** `for` and `while` are unrolled up to 16 iterations, and what would still be running after that becomes an obligation of its own: Z3 is asked whether any admitted input reaches a seventeenth iteration, and anything but "no" is `unknown`. `break` leaves the loop, `continue` rejoins the increment, and mutually exclusive paths are merged at the end of each iteration so a branching body costs iterations rather than powers of two.

**Evaluation order.** Traps are order-insensitive here, because one abort is the same observation wherever it happens. A `try` is not: it returns. So `try` is modeled only as the whole right-hand side of a binding, an assignment, a return or an expression statement, and a `try` written as an operand of a larger expression is unknown — C++ leaves the order of those operands unspecified, which would make the answer depend on it.

The fixed host-owned reference R, candidate C and domain D produce definedness/value pairs (d_R,v_R), (d_C,v_C). The checker requires a well-defined nonempty domain, a complete unrolling, and by default a reference that returns on all admitted inputs. It asks Z3 whether this is satisfiable:

    D && ((d_R != d_C) || (d_R && d_C && v_R != v_C)).

Unsatisfiable, with no reachable NaN result, means `smt-equivalent` in the named source model. A satisfiable counterexample is independently replayed through an operational evaluator written against Python values before rejection. Unavailable tools, translation errors, unsupported syntax, budget exhaustion, mismatched replay or solver timeouts are unknown, never acceptance. Domain restrictions are caller obligations, not guards automatically inserted by the native builder. The shipped module demo uses all declared inputs.

Source identity, translator identity, query hashes, solver version and outcomes are recorded. There is no checked proof reconstruction into Lean, and no theorem relates this translator to native output. A wrong reference can still express the wrong human requirement.

## Whole-module coverage, not selected-function promotion

```sh
python3 bin/cairn verify examples/proof_scope/reference.cairn \
  examples/proof_scope/candidate.cairn --all
```

The coverage checker parses and checks both complete sources, compares public records/enums/sums, and enumerates every function on both sides. Missing or extra entries, unsupported functions, mismatched types, partial references or any undecided obligation prevent `smt-module-equivalent`. Each entry retains its own result; the receipt lists covered/uncovered functions. A tagged result is covered when its payloads are modeled values, but heap storage is not, and one uncovered function keeps the whole module incomplete. There is a 64000-byte limit per input, at most 128 functions, and a soft 30-second solver budget. That budget is not a security sandbox or strict wall-clock bound on all compilation.

The example `mixed.cairn` is a negative coverage fixture: comparison with itself must remain incomplete because it contains an unsupported memory function. Empty coverage is not success. Public-type changes also block aggregate acceptance even if numeric function results agree.

## Tests, native code and failures

Unit tests, independent Python behavior oracles, both native compilers, instrumented allocation/release observations, ASan/UBSan/LSan and object comparisons provide finite executed evidence. Instrumented lifetime counters run at O0 and do not establish optimized allocation counts. Production allocation-limit and invalid-access fixtures must terminate by SIGABRT. `testing.evaluate` requires both a passing behavioral report and a successful child exit; a process cannot print a pass and then crash into a successful receipt.

Runtime address-space/CPU limits are protections against some runaway executions, not isolation. Native section equality is code-identity evidence under one compiler/flag profile, not a universal correctness or latency theorem. A new implementation may typecheck, pass examples and still be incorrect or slower on untested inputs.

## Tested, not proved: the implementation of ownership, lanes, tasks and placement

The section above proves a core ownership and lease calculus over whole places. The 1.0 rules as `checking.py` actually implements them -- affine and linear values, second-class borrows, leases over array parts and fields, race-free lanes, placement and the effect fixed point -- are exercised by acceptance and rejection tests, and nothing connects them mechanically to that calculus. Accepted programs run natively under both compilers and under AddressSanitizer, UndefinedBehaviorSanitizer, LeakSanitizer and ThreadSanitizer; device guards are exercised by death tests that must abort the host. These are finite executions, not theorems: a sanitizer-clean run shows the absence of those faults on those inputs only. Generic code is checked per instance, so an uninstantiated template is unchecked and is listed in the receipt rather than trusted.

## Formal completion gate

Three formal steps remain. Prove that the emitted collector loop refines the Lean model, or generate it from the model. Extend the ownership calculus from atomic places to the places the language actually has -- fields, array elements and the visibly disjoint parts that license a K-way split -- and add progress to the safety result (`Ownership.OpenGoal.Progress` names it and leaves it unproved). And relate `checking.py` to the calculus by something stronger than review: today the Lean checker is an abstraction written by hand beside the Python one, not extracted from it. Only a pinned Lean build with audited axioms may be called Lean verification; the receipt field above is the single place the compiler says so, and it is scoped to the certificate bundle.
