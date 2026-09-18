# CAIRN 1.1 run notes

Same rented host as 1.0 (GH200, AArch64, Ubuntu 22.04, clang++ 15, g++ 11/12, CUDA 12.8, Z3, Lean 4.34, QEMU), same day. `summary.json` is written by `tools/collect_evidence.py --release v1_1` on a committed tree; `ai_pilot/` holds the preregistered fresh-model pilot. The 1.0 records under `evidence/v1_0/` are unchanged.

## What 1.1 added, and how each was checked here

- **Recipes.** `std.wire` replaced the closed Python wire generator; its C++ was compared byte for byte with the 1.0.0 compiler's output on a two-module sample before the old generator was deleted, and the suite builds and runs a structure-of-arrays recipe, a natural-range recipe and `wire` together under clang++ with Address and UndefinedBehavior sanitizers and under g++ with `-Wall -Wextra -Werror`.
- **Queued device work.** A transfer, a device region `after` it and a transfer back, overlapped with host work, run on the GPU in the suite; an independent reviewer ran the same shapes under `compute-sanitizer --tool racecheck` and `memcheck` (0 hazards, 0 errors) and could not get a race accepted.
- **Incremental builds.** Cold, warm, body-only and signature edits are asserted per unit under both compilers; three of the four applications were also built that way by hand (the fourth is a device program and stays one unit).
- **Checked `reduce +`.** Host and device agree, and an overflowing device sum aborts the host, in the suite on this GPU; the reviewer confirmed the carry logic for `u8`/`u16`, where C++ promotes to `int`.
- **SMT.** The equivalence checker now covers records, sums, floats, bounded loops and fixed local storage; its concrete evaluator is compared with native runs on solver counterexamples and random inputs, and three spot checks written by the maintainer (a loop against a multiply including its traps, a NaN path, a float boundary) came back as expected.
- **Lean.** `proofs/Cairn/Ownership.lean` was rebuilt from scratch by the maintainer (2.3 s, no `sorry`, no `native_decide`, axioms `propext` and `Quot.sound` only). It proves safety of a hand-written core calculus over whole places, including race freedom under interleaving, and exhibits reachable faults for rejected programs so that the theorems are not vacuous. Nothing links it mechanically to `checking.py`.

## Reviews

A fourth adversarial review attacked only the 1.1 features and found seven defects, all in recipe expansion and the incremental build (a `where` name that rewrote ordinary identifiers, three Python tracebacks and a negative natural in the static language, unbounded nested iteration, a private `wire` hiding the packaged one, derivation order, a module named `entry`); none in queued device work. All are fixed and pinned. Separately the maintainer found a data race that predates 1.1 and that three audits had missed: a `wait` on a path that returns ended the lease on the path that goes on. Leases are now path-sensitive, and the Lean calculus, written independently, has the same join rule.

## What was not done

Generics are still checked per instance. Asynchronous I/O is still a task over blocking I/O. Recipes do not take expression fragments. No multi-device work, no refinement proof, no link between the Lean calculus and the implementation, and no comparative AI experiment: the pilot measures whether the rule cards suffice for one model family on nine small tasks, nothing more.
