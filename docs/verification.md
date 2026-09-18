# Verification boundaries and exact arithmetic certificates

## There is no whole-compiler proof

No successful `lake build` or `#print axioms` output exists for this release. Lean/Lake were absent. Direct toolchain retrieval failed; an official CI binary exceeded the connector's 512 MiB limit. A smaller third-party WASM link kit was downloaded and hash-checked but contained neither a runnable prover nor its required compiled libraries. It was not used to certify source and is not bundled here. No remote checker received this private repository; no remote workflow was triggered. The retrieval record is in evidence/v0_6/lean_attempt.json.

The exact algebra checker below is working code, not a relabeled Lean development. SMT results similarly trust their translator and Z3. Proving one relation does not confer correctness on the parser, typechecker, allocator, compiler, native instruction stream, foreign callers or operating system.

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

## Formal completion gate

The requested stronger result needs a small executable core matching this actual integer/view/lifetime model, progress/preservation or equivalent safety theorems, certified elaboration and model translation, then native refinement including the runtime and relevant target memory model. Only a real pinned Lean build with audited axioms can be called Lean verification. The official Lean axiom reference describes `#print axioms` and the distinction between accepted declarations and their assumptions: https://lean-lang.org/doc/reference/latest/Axioms/ . No theorem is claimed here merely because a proof script exists elsewhere.
