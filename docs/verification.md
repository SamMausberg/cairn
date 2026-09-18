# Verification boundaries and exact arithmetic certificates

## What is proved, and what is not

There is still no whole-compiler proof. There is now one real Lean result. `proofs/` is a dependency-free Lean 4 project (toolchain pinned in `proofs/lean-toolchain`; no Mathlib) that `lake build` checks in about two seconds. It proves, with no `sorry`, no `native_decide` and no added axioms (the audit in `evidence/v1_0/lean/print-axioms.txt` shows only `propext` and `Quot.sound`):

- `Cairn.check_sound`: if the certificate checker accepts a rule, the rule's conclusion is nonnegative for every integer assignment that satisfies its assumptions.
- `Cairn.Collector.all_checked`: all seventeen collector certificates pass that checker inside Lean, and each named obligation is a Lean theorem obtained by applying its certificate.
- For an executable model of the collector loop: the invariant `0 <= k <= i <= n` is preserved by emit and skip steps (using the certified inequalities), every store index is below the capacity (`store_index_lt_capacity`), both increments stay representable (`increments_fit`), and the result is stable selection: the written prefix equals `(inputs.filter pred).map proj`, its length is returned, and the tail is unchanged (`collect_spec`).

The Lean file of certificates is generated from the Python rules by `tools/export_lean_certificates.py`; a test fails if they drift, and the compiler receipt reports `lean_verified: true` only while the live bundle hashes to the bundle Lean checked. What this does **not** establish: that the Python checker implements the Lean `check` (it is a transliteration, reviewed, not extracted), that the emitter's C++ corresponds to the loop model, or anything about the parser, type checker, ownership, lanes, tasks, the C++ compiler or the machine. SMT results still trust their translator and Z3. Proving one relation does not confer correctness on the rest.

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

## Pure scalar source equivalence

The inherited symbolic evaluator models exact-width integer/Boolean values, checked and wrapping operations, local assignments, branching, short-circuiting, early return and acyclic scalar calls. An observation is either return(value) or an undifferentiated abort. Memory, loops, recursion, floats, sums and concurrency are unsupported.

The fixed host-owned reference R, candidate C and domain D produce definedness/value pairs (d_R,v_R), (d_C,v_C). The checker requires a well-defined nonempty domain and, by default, a reference that returns on all admitted inputs. It asks Z3 whether this is satisfiable:

    D && ((d_R != d_C) || (d_R && d_C && v_R != v_C)).

Unsatisfiable means `smt-equivalent` in the named source model. A satisfiable counterexample is independently replayed before rejection. Unavailable tools, translation errors, unsupported syntax, mismatched replay or solver timeouts are unknown, never acceptance. Domain restrictions are caller obligations, not guards automatically inserted by the native builder. The shipped module demo uses all declared inputs.

Source identity, translator identity, query hashes, solver version and outcomes are recorded. There is no checked proof reconstruction into Lean, and no theorem relates this translator to native output. A wrong reference can still express the wrong human requirement.

## Whole-module coverage, not selected-function promotion

```sh
python3 bin/cairn verify examples/proof_scope/reference.cairn \
  examples/proof_scope/candidate.cairn --all
```

The coverage checker parses and checks both complete sources, compares public records/enums/sums, and enumerates every function on both sides. Missing or extra entries, unsupported functions, mismatched types, partial references or any undecided obligation prevent `smt-module-equivalent`. Each entry retains its own result; the receipt lists covered/uncovered functions. Native storage and tagged-result wrappers remain uncovered even when their observable return is scalar. There is a 64000-byte limit per input, at most 128 functions, and a soft 30-second solver budget. That budget is not a security sandbox or strict wall-clock bound on all compilation.

The example `mixed.cairn` is a negative coverage fixture: comparison with itself must remain incomplete because it contains an unsupported memory function. Empty coverage is not success. Public-type changes also block aggregate acceptance even if numeric function results agree.

## Tests, native code and failures

Unit tests, independent Python behavior oracles, both native compilers, instrumented allocation/release observations, ASan/UBSan/LSan and object comparisons provide finite executed evidence. Instrumented lifetime counters run at O0 and do not establish optimized allocation counts. Production allocation-limit and invalid-access fixtures must terminate by SIGABRT. `testing.evaluate` requires both a passing behavioral report and a successful child exit; a process cannot print a pass and then crash into a successful receipt.

Runtime address-space/CPU limits are protections against some runaway executions, not isolation. Native section equality is code-identity evidence under one compiler/flag profile, not a universal correctness or latency theorem. A new implementation may typecheck, pass examples and still be incorrect or slower on untested inputs.

## Tested, not proved: ownership, lanes, tasks and placement

The 1.0 rules for affine and linear values, second-class borrows, leases, race-free lanes, placement and the effect fixed point are implemented in `checking.py` and exercised by acceptance and rejection tests. Accepted programs run natively under both compilers and under AddressSanitizer, UndefinedBehaviorSanitizer, LeakSanitizer and ThreadSanitizer; device guards are exercised by death tests that must abort the host. These are finite executions, not theorems: a sanitizer-clean run shows the absence of those faults on those inputs only. Generic code is checked per instance, so an uninstantiated template is unchecked and is listed in the receipt rather than trusted.

## Formal completion gate

The next formal step is to connect the two halves that are now each real: prove that the emitted collector loop refines the Lean model (or generate it from the model), and extend the mechanized core from the collector to the ownership and lease rules, where a small calculus with progress and preservation is the natural statement. Only a pinned Lean build with audited axioms may be called Lean verification; the receipt field above is the single place the compiler says so, and it is scoped to the certificate bundle.
