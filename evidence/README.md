# Evidence

What ran, on which machine, and what it found, one directory per release. A record is history: it shows what that release's code did then, and a later release's claims rest on its own directory. A claim in the documentation names the record it rests on.

| Directory | What it holds |
|---|---|
| `v1_0/` | The first release, on an AArch64 GH200: the release gates in `summary.json`, the device benchmark in `gpu/`, the freestanding transcript in `embedded/` and the Lean build in `lean/`. |
| `v1_1/` | The same gates, and `ai_pilot/`, the preregistered pilot: nine fresh subjects of one model family against hidden tests, with no comparison arm. |
| `v1_2/` | The gates, the Lean build, and `host_regions/`, where a host region starts to beat its loop. `RUN_NOTES.md` says what ran and what did not. |
| `v1_3/` | The first record on x86-64 and a consumer GPU: the gates, the Lean build, the first run of the preregistered CPU suite in `bench/`, and the device benchmark in `gpu/`. `RUN_NOTES.md` has the machine and the verdicts. |
| `v1_4/` | Records of the 1.4 work, taken as it landed; [v1_4/README.md](v1_4/README.md) lists them. The release's gates, `summary.json` and `RUN_NOTES.md` come when 1.4 is cut. |
| `v1_5/` | Records of the work after 1.4, taken as it landed, one directory per subject with a README saying what ran and what did not: `demos/` for the three demos, `perf/` for the compiler, the test suite, CI and the lane pool, `trim/` for the pass that shared duplicated logic in `src/cairn`. |

The 0.5 and 0.6 checkpoints (unit, native and sanitizer runs, codegen comparisons, wheel builds) are pre-1.0 records; they stay in `evidence/v0_5/` and `evidence/v0_6/` of the v1.3.0 tag.

A record names the paths of the tree it ran in. Since 1.4.0 the harnesses under `bench/cpu/`, `bench/host_regions/`, `bench/host_tasks/` and `bench/scan/` live in `bench/host/` (`bench/cpu/run.py` is `paired.py`, `bench/scan/run.py` is `scan.py`) and `bench/codegen/`, the teaching corpus under `training/` is `tools/corpus/`, the C++ runtime tests under `tests/native/` are in `tests/runtime/`, and `tests/soundness/test_review_1_4.py` and `test_review_1_4b.py` are `test_review_forms.py` and `test_review_tools.py`.

`tools/release/collect_evidence.py` and `collect_lean_evidence.py` write a release's gates and Lean build, and [docs/internals.md](../docs/internals.md#releasing) says how a release is cut.
