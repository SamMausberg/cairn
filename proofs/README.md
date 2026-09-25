# Lean proofs

This folder is a Lean 4 project that proves properties of models of CAIRN's rules, written by hand beside the compiler. It has no dependencies, and `lean-toolchain` pins the Lean version.

```sh
lake build        # in proofs/: checks every proof from scratch
make lean         # at the root: the certificates are current, lake build, and the differential runs
```

`make lean` also checks that the generated certificates are current, and it runs the differential checks, which give the compiler's rules and these models the same generated inputs and require the same answers. The proofs are about the models. Nothing proves that the compiler's Python implements them. [docs/verification.md](../docs/verification.md#the-lean-project) says what each file models, what is proved of it, and what the proofs do not cover.
