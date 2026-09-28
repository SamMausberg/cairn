# What an agent reads between an edit and a run

An agent that writes CAIRN checks a program, runs it on an input and compares what it printed with what it expected, and every byte a command prints is read again at each later request ([the 1.1 friction record](../../v1_1/friction/README.md)). This record measures that loop's outputs before and after each change that acts on it, on the corpus of [terse](../terse/README.md), whose `tools/ai/output_budgets.json` holds each output to a budget. No model ran for it, and a smaller output is not evidence that a model does better.

Sizes are UTF-8 bytes of stdout and stderr together, as an agent's shell shows them, with the project's directory written as `/home/agent/task`. tiktoken is not installed on the machine that measured them, so there are no token counts. The measurements ran on an AArch64 GH200 host with clang 14, shared with other agents' builds.

## A run prints the program's own output

`cairn run` used to print, when piped, a JSON record with the program's output inside a string, which an agent cannot compare with an expected output without a parser. It now hands the program its standard streams and exits with its status, and says on standard error only what went wrong. `--format json` keeps the record.

| case | main at d004653 | after |
|---|---|---|
| run | 197 | 17 |
| run --sanitize address | 219 | 17 |
| run, input ends early | 235 | 154 |

The first two print the histogram, 17 bytes, and nothing else. The third is a new case: its input ends after one value, so the program's own assert fails, and `cairn` adds one line saying how it ended and that an allocation past the memory cap ends the same way:

```text
assertion failed at src/main.cairn:47: input ended early
error: solution was stopped by SIGABRT: a guard failed, or an allocation passed the 1024 MiB cap
```

The column for main is its own `output_sizes.py` for the first two cases, and the same command through its compiler for the third, which main's corpus does not have.

## Commands

```sh
python3 tools/ai/output_sizes.py --check      # every case within its budget
git archive d004653 | tar -x -C DIR           # main, outside the temporary directory
python3 DIR/tools/ai/output_sizes.py          # main's corpus, by main's own tool
```
