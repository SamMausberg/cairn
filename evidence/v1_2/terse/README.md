# What an agent reads back from cairn

An agent's tokens are almost all context reads, and each tool result is read again at every later request ([the 1.1 friction record](../../v1_1/friction/README.md)). So the size of every output `cairn` prints is paid many times over. This record measures those outputs on a fixed corpus, and `tools/ai/output_budgets.json` holds each one to a budget that `tests/tooling/test_tools.py` checks.

## What was measured

`tools/ai/output_sizes.py` runs each command the way an agent's shell runs it: piped, in the project's directory, with stdout and stderr together. The programs are the 1.1 evaluation's `histogram` task, its reference laid out as a subject's project was (`cairn.toml` and `src/main.cairn`): with two test blocks, with one of them failing, and with one and with three of the mistakes the subjects made (a literal's `u64` used as a `usize`, a misspelled field, a misspelled name). A case whose name ends `at a terminal` sets `CAIRN_FORMAT=human`, as a person's terminal would. The replay case checks the 69 programs the 1.1 subjects checked, which `tools/ai/friction.py` rebuilds from their transcripts, and counts every record.

The MCP cases start `cairn mcp` and count each tool's name, description and input schema as `tools/list` gives them, which a client sends with every request, and the text of a typical result of each tool. The implementation session is on `examples/implementations`.

Every output says the project's directory as `/home/agent/task`, so a count does not depend on where the corpus ran. Tokens are tiktoken's `o200k_base`, and bytes are UTF-8.

## Before

Measured at 0af5987 with clang 21.1.8 (`before.json`).

| surface | bytes | tokens |
|---|---|---|
| check and refusals | 45,803 | 16,468 |
| build, run and test | 4,379 | 1,499 |
| MCP definitions and results | 26,943 | 7,366 |
| the rest | 92,192 | 25,070 |
| all | 169,317 | 50,403 |

| case | bytes | tokens |
|---|---|---|
| check, accepted | 657 | 244 |
| check, accepted, at a terminal | 44 | 13 |
| check, one refusal | 512 | 167 |
| check, one refusal, at a terminal | 424 | 125 |
| check, three refusals | 1,461 | 444 |
| check, three refusals, at a terminal | 953 | 293 |
| check, the 69 programs of the 1.1 replay | 41,752 | 15,182 |
| build | 1,841 | 685 |
| run | 246 | 92 |
| run --sanitize address | 272 | 99 |
| test, passing | 727 | 217 |
| test, failing | 1,153 | 356 |
| test, failing, at a terminal | 140 | 50 |
| new | 91 | 27 |
| rules E-EFFECT-ORDER | 893 | 227 |
| doc --std --module std.io | 5,836 | 1,718 |
| explain | 67,448 | 18,242 |
| explain --symbol count | 4,937 | 1,352 |
| state | 2,227 | 627 |
| inspect --symbol count | 10,760 | 2,877 |
| mcp tools/list, all eight tools | 4,201 | 938 |
| mcp check, accepted | 102 | 25 |
| mcp check, three refusals | 1,422 | 379 |
| mcp edit_open | 7,545 | 1,964 |
| mcp edit_request, refused | 231 | 65 |
| mcp edit_request, admitted | 145 | 40 |
| mcp plan_open | 896 | 288 |
| mcp plan_reply | 420 | 165 |
| mcp implementation_open | 9,157 | 2,589 |
| mcp implementation_submit | 1,264 | 384 |
| mcp state | 1,362 | 437 |
| mcp state, again | 198 | 92 |

Three things stand out. An accepted check prints 244 tokens, most of them the project's hashes, and 53 of the 69 replayed programs are accepted, so the replay's records are mostly that receipt. `cairn explain` of the whole program is 18,242 tokens, and 48 of the 53 functions it explains are library functions the program imports. The piped records are indented JSON, and the indentation is 15% to 36% of each record's tokens: compacted, the three-refusal record is 292 tokens and the whole-program explanation 11,615.

## What this does not show

This measures the size of what the tools print, not what an agent does with it. A smaller output is not evidence that a model does better, and `o200k_base` is not Claude's tokenizer. The corpus is one small program and one example; a program with more functions prints more. The sizes of `cairn explain` depend on the C++ compiler: clang 18 reports fewer loops than clang 21, so its explanation is smaller.

## Commands

```sh
python3 tools/ai/output_sizes.py --output evidence/v1_2/terse/before.json   # about 25 seconds
python3 tools/ai/output_sizes.py --check                                     # exit 1 when a case is over its budget
python3 tools/ai/output_sizes.py --budget                                    # rewrite the budgets from a measurement
```
