# Evidence

What ran, on which machine, and what it found, one directory per release. A record is history: it shows what that release's code did then, and a later release's claims rest on its own directory. A claim in the documentation names the record it rests on.

| Directory | What it holds |
|---|---|
| `v1_0/` | The milestone tagged `v0.8.0`, on an AArch64 GH200: the release gates in `summary.json`, the device benchmark in `gpu/`, the freestanding transcript in `embedded/` and the Lean build in `lean/`. |
| `v1_1/` | The same gates, and `ai_pilot/`, the preregistered pilot: nine fresh subjects of one model family against hidden tests, with no comparison arm. |
| `v1_2/` | The gates, the Lean build, and `host_regions/`, where a host region starts to beat its loop. `RUN_NOTES.md` says what ran and what did not. |
| `v1_3/` | The first record on x86-64 and a consumer GPU: the gates, the Lean build, the first run of the preregistered CPU suite in `bench/`, and the device benchmark in `gpu/`. `RUN_NOTES.md` has the machine and the verdicts. |
| `v1_4/` | Records of the work after `v0.8.3`, taken as it landed, and the gates of the never-tagged 1.4.0 at `ca709c8` in `summary.json` and `RUN_NOTES.md`; [v1_4/README.md](v1_4/README.md) lists them. |
| `v0_9/` | Records of the 0.9.0 release work, taken as it landed on a machine shared with other agents, one directory per subject with a README saying what ran and what did not: `demos/` for the three demos, `perf/` for the compiler, the test suite, CI and the lane pool, `trim/` for the pass that shared duplicated logic in `src/cairn`, `scale/` for check, build, rebuild and editor times of generated projects up to 77,000 lines and the size limits, `execution/` for what a repeated device pipeline makes, allocates and waits for on the thread's execution context, `ai_benchmark/` for the preregistered equal-budget benchmark of CAIRN, C++ and Rust, whose `RESULTS.md` says what ran and what did not, and `skill/` for a six-run smoke comparison of sessions with and without the Claude Code plugin. |

The 0.5 and 0.6 checkpoints (unit, native and sanitizer runs, codegen comparisons, wheel builds) are the earliest records; they stay in `evidence/v0_5/` and `evidence/v0_6/` of the `v0.8.3` tag.

The internal milestones were tagged `v1.0.0` to `v1.3.0` until the public numbering started at 0.9.0. They are now `v0.8.0` to `v0.8.3`, at the same commits. The folders `v1_0` to `v1_4` keep the names they were written under. `v0_9/` was written as `v1_5/`, and [bench/ai/PREREGISTRATION.md](../bench/ai/PREREGISTRATION.md), committed before the benchmark ran, names it so. A record written before the tag rename, such as `v1_4/diff`, names the old tags: read `v1.3.0` there as `v0.8.3`.

A record names the paths of the tree it ran in. Since 1.4.0 the harnesses under `bench/cpu/`, `bench/host_regions/`, `bench/host_tasks/` and `bench/scan/` live in `bench/host/` (`bench/cpu/run.py` is `paired.py`, `bench/scan/run.py` is `scan.py`) and `bench/codegen/`, the teaching corpus under `training/` is `tools/corpus/`, the C++ runtime tests under `tests/native/` are in `tests/runtime/`, and `tests/soundness/test_review_1_4.py` and `test_review_1_4b.py` are `test_review_forms.py` and `test_review_tools.py`.

`tools/release/collect_evidence.py` and `collect_lean_evidence.py` write a release's gates and Lean build, and [docs/internals.md](../docs/internals.md#releasing) says how a release is cut.
