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

## One check reports the independent refusals of one body

A refusal used to end the check of its function, so independent mistakes in one `main` came back one check at a time. A refused statement is now taken back and the rest of its block is checked, and a later refusal that may only follow from a refused statement is not reported. The first refusal is what a check that stops gives, in every program below.

| measure | main at 16e49c3 | after |
|---|---|---|
| the 69 programs the 1.1 subjects checked: refused | 16 | 16 |
| the same: refusals reported | 16 | 32 |
| the same: bytes of all 69 records | 10,742 | 18,307 |
| every refused program the repository holds, 1,241: further refusals | 16, in 15 programs | 104, in 54 programs |
| the four-mistake `main` of `tests/language/test_refusals.py`: checks until `typed`, fixing what each reports | 5 | 2 |

Five of the 69 checks gain further refusals: `block_scan`'s three other `transfer` calls that pass a `Buf` where `[n]` is expected, in three checks of two subjects, and `sieve`'s three other calls of the `println` an import put in place of the builtin, in two checks. Every further refusal the repository's programs gained was read, and each is a mistake of its own: a helper the program never declares, called again on another line, or a template's placeholder. A record is about 470 bytes a refusal larger, and a check the agent no longer runs is a whole request.

## Commands

```sh
python3 tools/ai/output_sizes.py --check      # every case within its budget
python3 tools/ai/friction.py replay --compiler .   # each check's first refusal, and the code and line of each further one
python3 tools/checks/refusal_differential.py       # every refused program both ways
git archive d004653 | tar -x -C DIR           # main, outside the temporary directory
python3 DIR/tools/ai/output_sizes.py          # main's corpus, by main's own tool
```
