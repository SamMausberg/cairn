# Equal-budget AI evaluations

Small systems tasks given to fresh model subjects in CAIRN, C++ and Rust, with the same budget in every arm. Two studies share this harness, each fixed by a preregistration committed before its counted subjects ran:

- `v1_0`, [PREREGISTRATION.md](PREREGISTRATION.md): ten tasks, three arms, results in `evidence/v1_0/ai_benchmark/`.
- `v1_1`, [PREREGISTRATION_V1_1.md](PREREGISTRATION_V1_1.md): the ten tasks and three where CAIRN's checks are the point, four arms (CAIRN with its Claude Code plugin, CAIRN with its documentation, C++ and Rust), results in `evidence/v1_1/ai_eval/`.

```sh
python3 bench/ai/harness.py verify                                   # every reference passes, every starter fails; no model runs
python3 bench/ai/harness.py run --phase counted --replicate 1         # under ~/cairn-aieval by default
python3 bench/ai/harness.py report --phase counted                   # results, tables, programs and transcripts under evidence/
python3 bench/ai/harness.py --study v1_0 report --phase primary      # the 1.0 benchmark's report
```

`run` starts headless Claude Code sessions in sandboxes under `--root`, which must be outside the repository (and, for `v1_1`, outside `/tmp`), up to the study's number at once, and judges and audits each subject as it finishes. A `v1_1` session runs in a user and mount namespace of its own, so it needs `unshare`. It stops starting subjects after an infrastructure failure, when a `PAUSE` file appears in the records, or at the study's cost ceiling, and a second `run` continues from where it stopped. It needs the `claude` command, `clang++`, `cargo` and `setarch`, and it spends model usage. `verify` and the tests in `tests/tooling/test_ai_bench.py` and `test_ai_eval.py` need no model.

| file | holds |
|---|---|
| `tasks.py` | the task table, each 1.0 task's oracle and hidden cases, and the input rules every case follows |
| `checked.py` | the oracles, hidden cases and input rules of the three 1.1 tasks |
| `tasks/<name>/` | `SPEC.md`, and a starter and a reference in each language |
| `checking.py` | the hidden check: the judged builds, the sanitizers, the constructs a task requires and the comparison with the oracle |
| `subjects.py` | the arms, the sandbox, TASK.md, the plugin's copy, the installed CAIRN toolchain and the session's command line |
| `isolation.py` | what a 1.1 subject's session sees: its own `/tmp`, sandbox and plugin, and none of the checkouts, other subjects or sessions |
| `scoring.py` | the transcript audit, token counts, the 1.0 paired comparison and tables |
| `analysis.py` | the 1.1 analysis: cost per solved task and solve rates with bootstrap intervals, and safety failures |
| `harness.py` | the command line above |
