# The skill and plugin, with and without

A smoke comparison of the Claude Code plugin (the skill in `skills/cairn/`, `bin/cairn` on `PATH`, `cairn lsp`) against the same sessions without it. It shows the plugin works end to end and where the tokens go. It is not a benchmark: one run per task and arm, one model, not preregistered.

## What ran

Three prompts (`tasks.json`), each given once to `claude -p` with the plugin (`--plugin-dir` at commit 479d4bc's tree) and once without it, in a fresh empty directory, on 2026-09-23. Model `claude-sonnet-5`, Claude Code 2.1.280, 40 turns at most, tools Bash, Write, Read, Edit, Skill, Glob and Grep. Both arms had `cairn` on `PATH` and the same other installed plugins and skills; the only difference was the CAIRN plugin. The machine was shared with six agents running test suites.

Each program was graded apart from what the session said: `cairn check` must type it, `cairn test` must pass its test blocks, and `cairn run` must print the answer a Python oracle gives (collatz: 6171 with 261 steps; stats: `0 1000002 499999547508`; parse: 4096 and an overflow).

## Results

| Task | Arm | Solved | Turns | Cost (USD) | Input tokens | Tool output (chars) | Seconds |
|---|---|---|---|---|---|---|---|
| collatz | plugin | yes | 9 | 0.187 | 313,516 | 4,097 | 45 |
| collatz | without | yes | 25 | 0.419 | 906,209 | 53,553 | 79 |
| stats | plugin | yes | 11 | 0.246 | 379,910 | 9,106 | 56 |
| stats | without | yes | 25 | 0.656 | 1,455,132 | 69,422 | 137 |
| parse | plugin | yes | 20 | 0.432 | 681,185 | 30,410 | 116 |
| parse | without | yes | 20 | 0.634 | 1,315,401 | 83,505 | 132 |
| all | plugin | 3 of 3 | 40 | 0.865 | 1,374,611 | 43,613 | 217 |
| all | without | 3 of 3 | 70 | 1.709 | 3,676,742 | 206,480 | 348 |

Every run solved its task, so this cannot say whether the plugin changes what gets solved. With the plugin the three sessions cost 0.51 times as much, read 0.37 times the input tokens and took 40 turns instead of 70. `traces.json` shows why: without it, the collatz session spent 20 tool calls finding the checkout, the examples and the docs before writing the program; with it, the session invoked the skill, read two cards, wrote the file, and checked, tested and ran it. Input tokens count the cached prefix each turn rereads, so they grow with turns.

## Limits

One run per cell cannot separate the plugin's effect from run-to-run variance, and the tasks are small single-file programs. The sessions without the plugin could read an untracked `results/` directory on this machine that holds earlier benchmark programs, which a fresh checkout does not have; that can only have helped them. The preregistered benchmark in `bench/ai` is the comparison to rerun with the plugin before any claim beyond this one.

## The MCP server's cost

The plugin gained `cairn mcp` after these runs (f0bfadf). A one-turn `claude-haiku-4-5` session with the plugin as of 99c2b33 (no MCP server) read 25,344 input tokens, and one with it as of a52726b read 25,460: the eight tools add about 116 tokens, because Claude Code 2.1.280 lists MCP tools by name and loads a schema only when a tool is searched for. `claude plugin details` does not count inline MCP servers, so this is the measured figure.

## Files

`tasks.json` holds the prompts, `results.json` the metrics and grades, `traces.json` each session's tool calls and final message (paths shortened, no raw transcript), and `programs/` the six programs as written.
