# 0.9 records

Each directory is one record of the 0.9.0 release work, taken as it landed on a machine shared with other agents. Each has a README saying what ran and what did not.

| Record | What it holds |
|---|---|
| `demos/` | The three demos. |
| `perf/` | The compiler, the test suite, CI and the lane pool. |
| `trim/` | The pass that shared duplicated logic in `src/cairn`. |
| `scale/` | Check, build, rebuild and editor times of generated projects up to 77,000 lines, and the size limits. |
| `execution/` | What a repeated device pipeline makes, allocates and waits for on the thread's execution context. |
| `ai_benchmark/` | The preregistered equal-budget benchmark of CAIRN, C++ and Rust; its `RESULTS.md` says what ran and what did not. |
| `skill/` | A six-run smoke comparison of sessions with and without the Claude Code plugin. |
| `implementations/` | Alternative implementations, the implementation session and `cairn validate` on `examples/implementations`. |
| `search/` | What the bounded search of `cairn tune` compiles, keeps and runs again, and its search over the instances of a parameterized implementation. |
| `tensor/` | Two matrix multiplies written with layouts and tensor-core fragments, checked on the host and compiled for sm_120. |
| `cooperative/` | Cooperative regions and pipeline stages: host runs under the thread sanitizer, and the sm_120 device build, which has not run. |
| `review/` | An adversarial review of the implementation layer: the defects found, their fixes, the attacks that held and what was not examined. |
| `predict_cooperative/` | What `cairn predict`, `explain` and `tune` say of cooperative regions: shared memory against ptxas, a pipeline's depth, a search over block shape and depth; nothing ran. |
