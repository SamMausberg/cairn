# Evidence

What ran, on which machine, and what it found, one directory per release. A record is history: it shows what that release's code did then, and a later release's claims rest on its own directory. A claim in the documentation names the record it rests on.

| Directory | What it holds |
|---|---|
| `v0_8_0/` | The milestone tagged `v0.8.0`, on an AArch64 GH200: the release gates in `summary.json`, the device benchmark in `gpu/`, the freestanding transcript in `embedded/` and the Lean build in `lean/`. |
| `v0_8_1/` | The same gates, and `ai_pilot/`, the preregistered pilot: nine fresh subjects of one model family against hidden tests, with no comparison arm. |
| `v0_8_2/` | The gates, the Lean build, and `host_regions/`, where a host region starts to beat its loop. `RUN_NOTES.md` says what ran and what did not. |
| `v0_8_3/` | The first record on x86-64 and a consumer GPU: the gates, the Lean build, the first run of the preregistered CPU suite in `bench/`, and the device benchmark in `gpu/`. `RUN_NOTES.md` has the machine and the verdicts. |
| `v1_0/` | The 1.0.0 release: its gates and Lean build, and the records of the work after `v0.8.3`, taken as it landed; [v1_0/README.md](v1_0/README.md) lists them. |

The 0.5 and 0.6 checkpoints (unit, native and sanitizer runs, codegen comparisons, wheel builds) are the earliest records; they stay in `evidence/v0_5/` and `evidence/v0_6/` of the `v0.8.3` tag.

Records written before the release numbering had their version names and fields renamed to it in one commit, and git history holds them as written. A record names the tree it ran in, and some paths have moved since:

| Path in a record | Path now |
|---|---|
| `bench/cpu/`, `bench/host_regions/`, `bench/host_tasks/`, `bench/scan/` | `bench/host/` and `bench/codegen/` |
| `bench/cpu/run.py`, `bench/scan/run.py` | `bench/host/paired.py`, `bench/host/scan.py` |
| `training/` | `tools/corpus/` |
| `tests/native/` | `tests/runtime/` |

`tools/release/collect_evidence.py` and `collect_lean_evidence.py` write a release's gates and Lean build, and [docs/internals.md](../docs/internals.md#releasing) says how a release is cut.

The scripts that write the other records live under `tools/` and `bench/`, not here. Two records keep the code that made them as part of the record: `v0_8_0/embedded/capture.py`, which no longer runs against this tree, and the two C++ programs of `v1_0/perf/`.
