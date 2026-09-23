# Equal-budget AI benchmark

Ten small systems tasks given to fresh model subjects in CAIRN, C++ and Rust, with the same budget in every language. [PREREGISTRATION.md](PREREGISTRATION.md) fixes the design, and `evidence/v1_0/ai_benchmark/` holds the results.

```sh
python3 bench/ai/harness.py verify                     # every reference passes, every starter fails; no model runs
python3 bench/ai/harness.py run --phase primary --replicate 1 --root /tmp/cairn-aibench
python3 bench/ai/harness.py report --phase primary     # results, tables and each subject's program under evidence/
```

`run` starts one headless Claude Code session at a time in a sandbox under `--root`, which must be outside the repository, and judges and audits each subject as it finishes. It needs the `claude` command, `clang++`, `cargo` and `setarch`, and it spends model usage. `verify` and `tests/tooling/test_ai_bench.py` need no model.

| file | holds |
|---|---|
| `tasks.py` | the task table, each task's oracle and hidden cases, and the input rules every case follows |
| `tasks/<name>/` | `SPEC.md`, and a starter and a reference in each language |
| `checking.py` | the hidden check: the judged builds, the sanitizers and the comparison with the oracle |
| `subjects.py` | the sandbox, TASK.md, the installed CAIRN toolchain and the session's command line |
| `scoring.py` | the transcript audit, token counts, the paired comparison and the tables |
| `harness.py` | the command line above |
