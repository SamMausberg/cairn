# Evidence

What ran, on which machine, and what it found, one directory per release. A record is history: it shows what that release's code did then, and a later release's claims rest on its own directory. A claim in the documentation names the record it rests on.

| Directory | What it holds |
|---|---|
| `v0_5/`, `v0_6/` | The 0.5 and 0.6 checkpoints: unit, native and sanitizer runs, codegen comparisons, the collector and wire cases, wheel builds, and a `summary.json` in each. |
| `v1_0/` | The first release, on an AArch64 GH200: the release gates in `summary.json`, the device benchmark in `gpu/`, the freestanding transcript in `embedded/` and the Lean build in `lean/`. |
| `v1_1/` | The same gates, and `ai_pilot/`, the preregistered pilot: nine fresh subjects of one model family against hidden tests, with no comparison arm. |
| `v1_2/` | The gates, the Lean build, and `host_regions/`, where a host region starts to beat its loop. `RUN_NOTES.md` says what ran and what did not. |
| `v1_3/` | The first record on x86-64 and a consumer GPU: the gates, the Lean build, the first run of the preregistered CPU suite in `bench/`, and the device benchmark in `gpu/`. `RUN_NOTES.md` has the machine and the verdicts. |
| `v1_4/` | Records of the 1.4 work, taken as it landed; [v1_4/README.md](v1_4/README.md) lists them. The release's gates, `summary.json` and `RUN_NOTES.md` come when 1.4 is cut. |

`tools/release/collect_evidence.py` and `collect_lean_evidence.py` write a release's gates and Lean build, and [docs/internals.md](../docs/internals.md#releasing) says how a release is cut.
