# proof_scope

What `cairn verify` covers, and what it cannot reach. `reference.cairn` is the specification, `candidate.cairn` a different implementation of the same five functions, and `mixed.cairn` the case the value model has no account of.

```sh
cairn verify examples/proof_scope/reference.cairn examples/proof_scope/candidate.cairn --all
# "status": "smt-module-equivalent", covered: average, extent, shifted, smaller, total

cairn verify examples/proof_scope/reference.cairn examples/proof_scope/mixed.cairn --all
# "status": "incomplete", missing: extent, shifted; extra: moved; uncovered: extent, moved, shifted
```

`moved` allocates a `Buf` and `take`s it, which the value model cannot express, so it is reported as uncovered rather than as equivalent. `native_proof` and `lean_proof` are `false` in both answers: this is SMT equivalence of two sources under a restricted value model, not a proof about the emitted machine code.
