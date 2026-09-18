# CAIRN 0.6 execution record

This directory holds fresh results from the local 0.6 source, not relabeled historical results. The implementation identity lists every tracked non-evidence file in the implementation commit. Final evidence-only commits do not alter compiler/runtime behavior.

The initial imported 0.5 baseline passed 299 tests. The final source passed all 422 unit tests. The eleven final validation commands completed with their expected exits. The deliberately incomplete module comparison returns 2; this is successful rejection, not proof of that module. Full child command results for the fourteen-command legacy native gate are retained in verification_run.json. There is no interrupted command counted as a pass in that final eleven-check gate.

Systems validation independently checks parsed decimal values/first-error offsets, sorting/filtering, untouched input/output regions, lexical-owner cleanup paths, production runtime sanitizers and intentional aborts. Allocation/release counts use an O0 instrumented observer and are not optimization-time allocation proofs. Arithmetic differential validation separately instruments native aborts as exceptions for observation; it does not replace production abort behavior.

Native object comparisons use ordinary C++ references, equal entry checks and explicit x86-64-v3 flags. Eight of nine sections match bytes and relocations. No new timing experiment was run. The everyday CLI still defaults to baseline x86-64.

A fresh virtualenv outside the checkout installed the wheel without network or Python runtime dependencies. Fourteen command checks passed their expected outcomes, including intentional rejection of unsupported module verification. Twenty-one packaged Python/runtime/typed-marker files matched both the final source and installation byte-for-byte.

The context result holds all non-card fields of the current packet fixed while replacing only rule-card texts with preserved 0.5 text on 23 legacy-profile functions. It is a constructed single-packet comparison, not actual model usage, a fixed-semantics ablation, or a comparison against C++ agents. Complete new-feature packets are reported without a fictitious executable 0.5 baseline. The all-feature card grew. No tokenizer other than plain ByT5 byte encoding ran.

Lean/Lake are absent. A local `lake build` attempt exits 127 before execution. Read-only toolchain retrieval was attempted; an over-limit official binary could not be retrieved. A smaller third-party link kit was retrieved and hash-checked but was not a runnable prover and was not used for checking. There are no Lean-verified claims. No private source was uploaded for checking and no workflow was triggered.

An initial release-evidence assembly script stopped on a schema-key mismatch while forming its summary. The key was corrected and assembly rerun. Raw validation records were unaffected. This packaging interruption was not counted as a successful assembly. No incomplete archive was delivered.

All Git changes remain local. The optional publisher was never executed remotely. Secrets-pattern audits have finite coverage and do not guarantee absence of every possible secret. Final archive/bundle integrity and extraction checks are reported by the external build report, after the release commit is known.
