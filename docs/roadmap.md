# Remaining acceptance gates

The CPU compiler now has scoped scalar storage and monomorphic tagged outcomes. These are not general owning containers or generic Result values. The next breadth gate is move/borrow-capable owners and containers, sum/record composition, real namespaces and separately compiled interfaces, then generic functions/traits and controlled foreign I/O. Each addition needs an actual application and rejection/behavior tests. OS libraries, atomics/locks, concurrency, asynchronous I/O, freestanding targets and GPU lowering remain absent.

The immediate formal gate is a Lean-checked semantics and translation theorem for this actual bit-vector and scoped-storage implementation. The affine-implication checker is small enough to be one first verified component, followed by a collector induction and certified code correspondence. A statement that all arithmetic coefficients match does not prove lifetime safety, type soundness or compiler refinement. Source and native layers require separate statements and real toolchain/axiom audits.

The AI gate is a fresh-model experiment with independent tasks, comparable C++/Rust editing and verification tools, equal total inference/checker/test budgets, real model tokenizer accounting and family-disjoint evaluation. Count failed attempts, caller regressions, task correctness, native speed and total context. No corpus or deterministic search substitutes for this experiment.

GPU support needs a real backend, target memory/collective semantics, explicit asynchronous lifetimes and measurements. No CPU C++ backend or scalar proof confers GPU behavior. No communication-optimality or universal C++ performance result has been obtained.
