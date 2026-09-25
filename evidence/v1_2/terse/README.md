# What an agent reads back from cairn

An agent's tokens are almost all context reads, and each tool result is read again at every later request ([the 1.1 friction record](../../v1_1/friction/README.md)). So the size of every output `cairn` prints is paid many times over. This record measures those outputs on a fixed corpus before and after the TERSE track, and `tools/ai/output_budgets.json` holds each one to a budget that `tests/tooling/test_tools.py` checks.

## What was measured

`tools/ai/output_sizes.py` runs each command the way an agent's shell runs it: piped, in the project's directory, with stdout and stderr together. The programs are the 1.1 evaluation's `histogram` task, its reference laid out as a subject's project was (`cairn.toml` and `src/main.cairn`): with two test blocks, with one of them failing, and with one and with three of the mistakes the subjects made (a literal's `u64` used as a `usize`, a misspelled field, a misspelled name). A case whose name ends `at a terminal` sets `CAIRN_FORMAT=human`, as a person's terminal would. The replay case checks the 69 programs the 1.1 subjects checked, which `tools/ai/friction.py` rebuilds from their transcripts, and counts every record.

The MCP cases start `cairn mcp` and count each tool's name, description and input schema as `tools/list` gives them, which a client sends with every request, and the text of a typical result of each tool. The implementation session, on `examples/implementations`, is opened in the same server after the edit session.

Every output says the project's directory as `/home/agent/task`, so a count does not depend on where the corpus ran. Tokens are tiktoken's `o200k_base`, and bytes are UTF-8. Both runs used clang 21.1.8 on this machine.

## Before and after

`before.json` is main at 0af5987. `after.json` is the same corpus with the track's six pull requests merged: #136 the measurement, #141 compact records, #146 the check record, #147 build, run and test, #148 MCP, #149 `cairn explain`.

| surface | tokens before | tokens after | cut |
|---|---|---|---|
| check and refusals | 16,468 | 3,584 | 78% |
| build, run and test | 1,499 | 708 | 53% |
| MCP definitions and results | 7,366 | 6,535 | 11% |
| the rest | 25,070 | 7,909 | 68% |
| all | 50,403 | 18,736 | 63% |

| case | tokens before | tokens after | bytes before | bytes after |
|---|---|---|---|---|
| check, accepted | 244 | 21 | 657 | 88 |
| check, accepted, at a terminal | 13 | 13 | 44 | 44 |
| check, one refusal | 167 | 103 | 512 | 377 |
| check, one refusal, at a terminal | 125 | 101 | 424 | 349 |
| check, three refusals | 444 | 249 | 1,461 | 890 |
| check, three refusals, at a terminal | 293 | 269 | 953 | 878 |
| build | 685 | 217 | 1,841 | 660 |
| run | 92 | 68 | 246 | 197 |
| run --sanitize address | 99 | 69 | 272 | 219 |
| test, passing | 217 | 118 | 727 | 425 |
| test, failing | 356 | 186 | 1,153 | 629 |
| test, failing, at a terminal | 50 | 50 | 140 | 140 |
| new | 27 | 19 | 91 | 78 |
| rules E-EFFECT-ORDER | 227 | 188 | 893 | 779 |
| doc --std --module std.io | 1,718 | 1,691 | 5,836 | 5,742 |
| explain | 18,242 | 2,073 | 67,448 | 7,047 |
| explain --symbol count | 1,352 | 867 | 4,937 | 2,992 |
| state | 627 | 438 | 2,227 | 1,363 |
| inspect --symbol count | 2,877 | 2,633 | 10,760 | 10,106 |
| check, the 69 programs of the 1.1 replay | 15,182 | 2,828 | 41,752 | 10,734 |
| mcp tools/list, all eight tools | 938 | 938 | 4,201 | 4,201 |
| mcp check, accepted | 25 | 25 | 102 | 102 |
| mcp check, three refusals | 379 | 298 | 1,422 | 1,068 |
| mcp edit_open | 1,964 | 1,964 | 7,545 | 7,545 |
| mcp edit_request, refused | 65 | 57 | 231 | 197 |
| mcp edit_request, admitted | 40 | 40 | 145 | 145 |
| mcp plan_open | 288 | 288 | 896 | 896 |
| mcp plan_reply | 165 | 165 | 420 | 420 |
| mcp implementation_open | 2,589 | 1,847 | 9,157 | 6,200 |
| mcp implementation_submit | 384 | 384 | 1,264 | 1,264 |
| mcp state | 437 | 437 | 1,362 | 1,362 |
| mcp state, again | 92 | 92 | 198 | 198 |

Each cut, and what it kept:

| change | what went | what stayed |
|---|---|---|
| piped records are one compact line (#141) | the indentation, 15% to 36% of each record | every field; a terminal that asks for JSON still gets it indented |
| the check record (#146) | an accepted check's project receipt, three SHA-256 digests; the data a refusal's hint states in words | the verdict and counts, `formal_status`; every code, line, message, card and hint, `expected_type` and `actual_type`, the available names when no close one exists |
| build, run and test (#147) | the build's hashes, compiler version and time, which `receipt.json` keeps; empty streams and zero statuses; a failed assert's stderr when it is its reason | the status, command, artifact and directory the eval judge reads; every failure's reason and output |
| MCP (#148) | hint data the MCP check merged back; terms repeated on every further refusal; cards the edit host already sent | the refusal as the command line gives it, with its line of source; every card once |
| `cairn explain` (#149) | 48 library functions the program imports, unless named; runtime loop paths into a deleted directory | the program's own functions, each library call under `costly_calls`, every loop verdict |

The replay's check records fall most because 53 of its 69 programs are accepted, and an accepted record was almost all project receipt. `inspect`, `edit_open` and `implementation_open` stay the largest: their rule cards are the teaching they exist to send, sent once per conversation. The eight tool definitions are unchanged at 938 tokens; their words carry what a client needs to call them.

## What this does not show

This measures the size of what the tools print, not what an agent does with it. A smaller output is not evidence that a model does better, and `o200k_base` is not Claude's tokenizer. The corpus is one small program and one example; a program with more functions prints more. The sizes of `cairn explain` depend on the C++ compiler: clang 18 reports fewer loops than clang 21, so its explanation is smaller. The MCP implementation result depends on whether Z3 decides the equivalence in its time.

## Commands

```sh
python3 tools/ai/output_sizes.py --output evidence/v1_2/terse/after.json   # about 25 seconds
python3 tools/ai/output_sizes.py --check                                    # exit 1 when a case is over its budget
python3 tools/ai/output_sizes.py --budget                                   # rewrite the budgets from a measurement
python3 tools/ai/output_sizes.py --compiler DIR                             # the corpus through another tree's bin/cairn
```
