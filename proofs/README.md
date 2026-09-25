# Lean proofs

A Lean 4 project with no dependencies, pinned in `lean-toolchain`. `lake build` here checks it from scratch. `make lean` also checks that the generated certificates are current and runs the differential checks of the compiler's rules against these models.

[docs/verification.md](../docs/verification.md#the-lean-project) says what each file models, what is proved of it, and what the proofs do not cover: they are about models written by hand beside the compiler, not about its Python.
