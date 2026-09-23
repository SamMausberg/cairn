# 1.4 records

Each directory is one record, taken on the reference machine while the 1.4 work landed: an AMD Ryzen 7 7800X3D (sixteen lanes) under WSL2 with clang++ 21.1.8 and g++ 13.3, shared throughout with other agents' builds and suites. A record's own notes say what ran, with what, and how the sharing bears on it. Nothing here ran on a device. The release's gates, `summary.json` and `RUN_NOTES.md` are written when 1.4 is cut.

| Record | What it holds |
|---|---|
| `guards/` | The optimized build against the one that keeps every guard: 1,821 generated functions, 174,816 cases per compiler, no difference. |
| `lowering/` | The guards the emitted C++ writes before and after the elision work, the checks internal calls no longer run at a lean body, and what those checks cost in time. |
| `lean/` | Two differential runs against the Lean models: 20,000 generated programs the checker and the ownership calculus classify alike (`differential.json`), and 20,000 guard sites `facts.py` and `Facts.lean` decide alike (`facts_differential.json`). |
| `perf_model/` | The shipped host profile, how it was measured, how `cairn predict` did against timings calibration never saw, and the second pass that corrected its folds, lanes and caches (`structure_2.json`). |
| `fusion/` | Fused regions timed against the regions they were written as, beside what the model predicted. |
| `scan/` | The pooled scan against its loop and the radix sort against the heapsort, with the predictions made before they ran. |
| `runtime/` | Reused task threads against one thread per task, on a repeated pipeline. |
| `context/` | Packet, card and refusal sizes in `o200k_base` tokens before and after the packet rework, on scripted transcripts; no model took part. |
| `tokens/` | The library and the examples counted in lexer tokens before and after the reduction passes. |
| `protocol_trial/` | What was checked before the preregistered edit-protocol trial may run; the trial itself has not run. |
| `review/` | The adversarial reviews of what 1.4 added: what was attacked, what was found and fixed, and what held. |
