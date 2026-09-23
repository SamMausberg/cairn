# CAIRN 0.8.2 run notes

The same rented host as the 0.8.0 and 0.8.1 records: GH200, AArch64, Ubuntu 22.04, clang++ 15, g++ 11 and 12, CUDA 12.8, Z3, Lean 4.34, QEMU. 2026-09-19. `summary.json` is written by `tools/release/collect_evidence.py --release v0_8_2` on a committed tree, and `host_regions/` holds the lane pool benchmark.

## What 0.8.2 added, and how each was checked here

- Bounds and certification. Every template of `std` certifies, and a test keeps it so. After the fifth review, certification also applies the whole-program rules (ceilings, lanes, operand order) at its witnesses. The analytics application certifies all seven of its templates.
- Trait ceilings. A ceiling on a trait member binds every implementation. `std.core` declares `less`, `same` and `hash` pure, and the whole suite, the applications and the derived impls pass under that.
- Recipes. Generated impls, `kernel fn`s, function-name parameters, static folds, multi-expression `each` and hygiene each have an accepted program that runs under sanitizers and a rejection table. `std.wire` output is still byte for byte what 0.8.0 produced.
- Placement lending. A program passing pinned and unified buffers to host helpers, and a unified buffer to a device region, runs on this GPU in the suite.
- Host lane pool. Rebuilt and re-measured by the maintainer on this machine: the size at which a region beats the loop is 100,000 elements for `saxpy` and 30,000 for the dearer kernel, and a region of 64 elements costs what the loop costs. ThreadSanitizer, AddressSanitizer and UndefinedBehaviorSanitizer runs are in the suite for several lane counts.
- Lean. `proofs/` was rebuilt from scratch by the maintainer after each extension (about 4 s, no `sorry`, axioms `propext` and `Quot.sound` only). It now covers record fields, array parts with symbolic bounds, tasks, parallel regions and progress. Every accepted and rejected program in its regression was classified the same way by the Python checker.
- SMT. The equivalence checker models array views, parts, `rw` borrows, local heap storage, `compact` and host `reduce`. Its concrete evaluator is compared with native runs, final `rw` contents and traps included, on solver counterexamples and random inputs.
- Dependencies and the build cache. Every reproducer of the fifth review's nine loader and cache findings now refuses or behaves like a clean build, and each is pinned in `tests/projects/test_projects.py`.
- The full native tool (`tools/checks/verify.py --gcc --sanitize`) passed on the reorganized tree: 14 commands, exit 0.

## Reviews and users

A fifth adversarial review attacked only the 0.8.2 features. It found three soundness defects (an extent expression emitted twice, which gave an out-of-bounds read and write under both compilers; a `linear` value dropped through `Dyn`; a false `ok` from certification), one checker crash, and about twenty wrong or missing rules in constant folding, recipe expansion, trait coherence, operand order, the dependency loader and the object cache. All are fixed and pinned.

A second application, `examples/apps/analytics`, was written against 0.8.1 by a fresh agent and rewritten against the fixes by another. The first pass found one checker crash and a dozen rough edges. The second pass found three more rough edges. All are fixed. Its device configuration runs on this GPU and equals the host bit for bit.

## What was not done

Asynchronous I/O is still a task over blocking I/O. No multi-device work. No refinement proof, and no link between the Lean calculus and `checking.py`. Recipes do not take arbitrary expression fragments or named fields. Leasing one field of a record leases the whole record. The `free` effect is charged with `alloc`, so the row of a function that only drops an owner does not show it; that one was closed after this record was collected, and the row now carries `free` where the release runs. No comparative AI experiment was run. The performance numbers are from one machine.
