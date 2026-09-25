# 1.1 records

Each directory is one record of the work after `v1.0.0`, taken as it landed. Its README says what ran, on which machine, with which tools, and what did not run. Most ran on the reference machine, an AMD Ryzen 7 7800X3D (16 threads) under WSL2, shared with other agents' builds and suites; `device_perf/` is the only record whose code ran on a GPU.

| Record | What it holds |
|---|---|
| `ai_eval/` | The evaluation [bench/ai/PREREGISTRATION_V1_1.md](../../bench/ai/PREREGISTRATION_V1_1.md) fixes: its pilot, and its counted run as it stood when it stopped early, 54 of 156 subjects. |
| `catalog/` | What `cairn predict` says of the suite's kernels and the device examples on every packaged device card, priced from NVIDIA's published figures; compiled for each card's target, never run. |
| `ci/` | What the compatibility jobs added to CI on 24 September 2026 ran and found in their first runs, and the first main run where every job passed. |
| `device_perf/` | CAIRN kernels timed beside hand-written CUDA on one GPU: how much of the gap was kernel time and how much host synchronization, and what the fixes changed. |
| `diagnostics/` | `cairn check` reporting every independent refusal: the first refusal unchanged, accepted programs emitting the same C++, and checking no slower. |
| `emulation/` | Device programs judged against `sm_120` and run under `--emulate`, their device work on host threads. |
| `friction/` | Where the 1.1 evaluation's CAIRN tokens went, taken apart from the subjects' transcripts: what each request carried, each refusal and its cost, and the causes; no model ran. |
| `harness/` | What `cairn export --harness` wrote for each benchmark and how far each submission got without a GPU. |
| `kernels/` | Wide loads and stores, atomic updates, a cooperative region's finish and shared arrays nobody zeroes, and `examples/reduction` rewritten without `unsafe`. |
| `review/` | An adversarial review of what landed after 1.0.0: the attacks, and which held. |
| `search/` | What `cairn tune`'s search costs before and after lazy generation and shared compiles. |
| `teaching/` | What the agent skill asks an agent to read, in tokens, before and after refusals carried their rule card and fix; no model ran. |
| `validation/` | One numerical policy for every float comparison, device tests that write every input exactly, and Z3 counterexamples replayed natively. |
| `workspace/` | What an agent waits for over `cairn mcp`, before and after one compile per distinct source. |
