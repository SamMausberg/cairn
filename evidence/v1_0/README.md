# 1.0.0 records

Each directory is one record of the work between `v0.8.3` and 1.0.0, taken as it landed on the reference machine: an AMD Ryzen 7 7800X3D (16 threads) under WSL2 with clang++ 21.1.8, g++ 13.3 and CUDA 13.2, shared throughout with other agents' builds and suites. Each record says what ran, with what, and how the sharing bears on it. Nothing here ran on a GPU. The release's own gates, Lean build and run notes sit beside the records (`summary.json`, `lean/`, `RUN_NOTES.md`); `gates_2026_09_22/` holds the same for a candidate that was prepared on 2026-09-22 and never tagged.

| Record | What it holds |
|---|---|
| `bench/` | The preregistered CPU baseline suite run again with no agent running: the parallel `reduce` and the lane-owned-block histogram that 0.8.3 could not write are level with OpenMP and oneTBB, and `dot_f64` keeps its written order. |
| `guards/` | The optimized build against the one that keeps every guard: 1,821 generated functions, 174,816 cases per compiler, no difference. |
| `lowering/` | The guards the emitted C++ writes before and after the elision work, the checks internal calls no longer run at a lean body, and their cost in time. |
| `perf_model/` | The shipped host profile, how it was measured, and how `cairn predict` did against timings calibration never saw. |
| `fusion/`, `scan/`, `runtime/` | Fused regions, the pooled scan and radix sort, and reused task threads, each timed against what it replaces beside the model's prediction. |
| `context/`, `tokens/` | Packet, card and refusal sizes in `o200k_base` tokens, and the library and examples in lexer tokens, before and after the reduction passes; no model took part. |
| `protocol_trial/` | What was checked before the preregistered edit-protocol trial may run; the trial has not run. |
| `diff/` | `cairn diff v0.8.3` against a later commit for the library and every example project: each function's class, its changes and the semantic-version verdict. |
| `review/` | Two adversarial reviews of the tools and runtime added after `v0.8.3`: what was attacked, found, fixed and held. |
| `demos/` | What each program under `demos/` printed and drew when it ran. |
| `perf/`, `trim/`, `scale/` | The speed of the compiler, the test suite, CI and the lane pool; the pass that removed duplicated logic from `src/cairn`; check, build, rebuild and editor times of generated projects up to 77,000 lines, and the size limits. |
| `ai_benchmark/` | The preregistered equal-budget benchmark of CAIRN, C++ and Rust; its `RESULTS.md` says what ran and what did not. |
| `skill/` | A six-run smoke comparison of sessions with and without the Claude Code plugin, and what the MCP server adds to a session. |
| `execution/` | What a repeated device pipeline makes, allocates and waits for on the thread's execution context, counted on a host stand-in. |
| `implementations/`, `search/` | Alternative implementations, the implementation session and `cairn validate` on `examples/implementations`; what the bounded search of `cairn tune` compiles, keeps and runs again, including the instances of a parameterized implementation. |
| `cooperative/`, `predict_cooperative/` | Cooperative regions and pipeline stages under the thread sanitizer and compiled for sm_120; what `cairn predict`, `explain` and `tune` say of them. |
| `tensor/`, `foreign/` | Two tensor-core multiplies written with layouts and fragments; typed assembly and foreign C++ and CUDA implementations. |
| `review_implementation_layer/` | An adversarial review of the implementation layer: 21 defects and their fixes, 78 refused attacks, and what was not examined. |
