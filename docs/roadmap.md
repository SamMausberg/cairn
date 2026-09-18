# Next implementation gates

The next breadth work is real namespaced modules, separate compilation and stable public interfaces, followed by payload sums, Result values, owned storage and explicit cleanup. Each requires executable examples and rejection/behavior tests; a design entry alone is not completion.

The next proof work is a checked translation theorem for the actual scalar bit-vector translator, then guarded-array and lifetime semantics. The historical fixed-arena Lean sources do not establish these results. Do not claim a proof until the pinned toolchain runs and axioms are audited.

The next AI experiment compares fresh, equally budgeted agents using this workflow and equally capable C++/Rust tooling. Measure task correctness, total tokens including failed attempts, checking cost, regressions and native performance. Neither a corpus nor a scripted repair is that experiment.

The next GPU gate is a real target backend with explicit memory/collective semantics and asynchronous lifetimes. Do not infer GPU control or speed from a CPU C++ backend.
