# The AI edit protocol

This page covers how an AI agent reads and changes a CAIRN program through the compiler: what it is shown, what it may send, what is checked before a change is kept, and what is recorded about each attempt. After reading it you can install the agent skill and plugin, drive an edit, plan or implementation session from Python or over [`cairn mcp`](tools.md#cairn-mcp), and read what each answer establishes. [tools.md](tools.md) covers the command line.

## How an agent edits

In this protocol an agent does not write the program's files itself. It works through a host, a program that holds the source on the agent's behalf, shows the agent part of it, and decides whether each change the agent sends is kept. The edit, plan, implementation and migration hosts are Python classes in `cairn.agent.hosts`, and `cairn mcp` serves the first three as tools to an agent without a shell. A host exists so that a change is judged against the whole program and the rules the owner set, whatever the agent was shown.

A session is one piece of work on one function: an edit of its body, a change of its plan, a new implementation of it, or a change of its signature. The host opens the session and pins what it will judge a change against, such as the source, the host's own rules and the compiler, under a digest, a hash of all of it. An edit or implementation session has a handle such as `e1`, so the agent names it without copying the digest; a plan session is named by its packet's digest. The first thing a session sends is a packet, the JSON the agent reads before it writes: the function's source, what it calls and what calls it, the rules that apply, and what may change.

The agent answers with a request: a change, or a question such as a request for more context. The host splices a change into the source it pinned when the session opened, and checks the whole program again. It then accepts the change, which the records call an admission, or refuses it with a diagnostic code, the rule card that states the broken rule, and the smallest fix it can state. A rule card is a short statement of what one part of the language accepts and refuses, with the code of each rule.

A candidate is one version of a function that a session or a search tries: an edited body, a plan, or an implementation. What was tried on a candidate, what failed, what was validated and what was measured can be kept as records in the [candidate history](#candidate-history), so a later session starts from what still holds.

| Session | Protocol | What the agent may change | Described in |
|---|---|---|---|
| edit | `cairn.edit/2` | one function's body, or one expression in it | [packets](#packets), [what the host accepts](#what-the-host-accepts) |
| plan | `cairn.plan/1` | how one function's parallel regions run | [plan edits](#plan-edits) |
| implementation | `cairn.implementation/1` | a new implementation of one function | [implementation sessions](#implementation-sessions) |
| migration | `cairn.migration/1` | one function's signature, any signature the host names with it, and every caller | [interface migrations](#interface-migrations) |
| named choices | `cairn.choices/1` | named expressions inside one function | [named choices](#named-choices) |

A `cairn.edit/2` request names its session by its handle, and is a `body` edit of a whole function, an `expr` edit of one site named like `x3`, or one of the requests [below](#requests-beyond-an-edit). A replacement is at most 64000 UTF-8 bytes. [project/edit_schema.json](project/edit_schema.json) is the JSON Schema of the requests, including the older `cairn.edit/1`, which names a session by its digest. It does not describe the `predict` and `shot` requests. [internals.md](internals.md#safety-and-trust) lists what a reply can never change, and [demos/repair](../demos/repair/README.md) shows a whole session.

## The skill and the Claude Code plugin

`skills/cairn/` is an [Agent Skill](https://agentskills.io), the instructions an agent loads when a task involves CAIRN. `SKILL.md` holds the loop of check, test and run, and of validate and tune for implementations; the three cards every packet carries, with their codes; an example that compiles; an index of the cards; and the costliest mistakes. `cards/` holds the other thirty-seven cards, each with its codes.

The skill leaves out what a refusal already says: a refusal names its card and carries its fix, `cairn rules` prints any card, and `cairn --help` lists every command. An agent lists only the skill's description, about 180 tokens, and reads `SKILL.md` when a task involves CAIRN. `python -m cairn.agent.skill` writes the directory from `teaching.py`, `make editors` runs it, and `tests/tooling/test_skill.py` fails while a committed file differs from a fresh render.

The repository is also a Claude Code plugin and its own marketplace:

```sh
claude plugin marketplace add SamMausberg/cairn
claude plugin install cairn@cairn
```

The plugin adds the skill, puts `bin/cairn` on the session's `PATH`, and runs `cairn lsp` on `.cairn` files, so each edit returns the compiler's diagnostics to the agent. It needs Python 3.11 or later and a C++20 compiler, and downloads nothing. Another agent that reads Agent Skills can load `skills/cairn/` directly, with `bin/cairn` of a checkout on its `PATH`.

The plugin also starts [`cairn mcp`](tools.md#cairn-mcp), a Model Context Protocol server, so an agent without a shell reaches the same hosts: `check`, `state`, and the edit, plan and implementation sessions of this page, as eight tools that write an accepted change back to its files. Other MCP clients start the same server as `bin/cairn mcp`.

`claude plugin details` counts the skill's description, about 180 tokens, as the plugin's whole cost in every session, and does not count MCP tool schemas. The eight tools' list is 4,390 bytes of JSON. That is about a thousand tokens more for a client that loads tool schemas up front, and about 116 for Claude Code, which loads a schema only when a tool is searched for ([evidence/v1_0/skill](../evidence/v1_0/skill/README.md)).

A smoke comparison of six runs gave three small tasks to one `claude-sonnet-5` session each, with and without the plugin, before the plugin had `cairn mcp`. Every session solved its task. The sessions with the plugin cost 0.51 times as much and took 40 turns instead of 70, because they read two cards instead of searching the checkout ([evidence/v1_0/skill](../evidence/v1_0/skill/README.md)). One run per cell is not a benchmark.

`bench/skill/` is a `claude plugin eval` suite, which `plugin.json` names under `experimental.evals`. Its six cases need only the Read, Glob, Grep and Skill tools. Five are refused programs, each graded by a regular expression for its diagnostic code, a rubric for the fix, and whether the skill fired; one is a checksum to write. `tests/tooling/test_skill.py` holds each program to the code its case grades. The suite has not been run.

## The rule cards

`src/cairn/agent/teaching.py` holds the rule cards. `CARDS` has one per part of the language, and `TOOL_CARDS` one for each kind of refusal the hosts, the command line and the compiler's own limits give. Each card states what its part accepts and refuses and names the diagnostic code of each rule. `CODES` gives every code exactly one card, so a refusal leads back to the rule behind it, and `tests/agent/test_cards.py` fails on a code the package can emit that no card owns.

`base`, `integers` and `calls` go with every packet. The other language cards are picked by the lexical tokens of the source at hand, so a packet carries only what its program uses, and a comment cannot pick one. No source picks a tool card. [`cairn rules`](tools.md#cairn-rules) prints any card from the command line.

## Packets

A packet starts focused: it shows one function and what is next to it in the call graph. It holds:

- the function's source, its effect row and its ceiling;
- the signature, effect row, comment and evidence class of every function it calls and every function that calls it, and the types those name;
- the cards the function's source selects, and for an expression edit the expected type and the bindings at each site;
- `not_shown`, the names of every other function;
- the task's reference, input domain and trap policy, when the host gives one;
- `terms`, which say once what every scope, limit, evidence class, refusal and admission means.

An effect row is the list of what a function may do that a caller can observe, such as `alloc`, `io`, `trap` or `write:out`. The ceiling is the most the host lets the edited function's row hold. `cairn inspect --symbol f` prints the packet. The agent reads these facts and cannot replace them.

The evidence class says how much of a callee's behaviour the agent may rely on without reading its body. The session establishes it against the exact source when it opens, and never carries a claim in from elsewhere:

| Class | Established by | What the packet shows | May the agent rely on it |
|---|---|---|---|
| `interface` | the compiler: signature, effect row, types, [extents](memory.md#arrays-views-and-parts) | nothing more | only for the interface; expand the body first |
| `declared` | nobody: the host's text under `contracts` | the text | no; expand the body first |
| `finite-tested` | the host's `cairn.task/1` cases under `tests`, run natively now | the case count and up to eight cases | for those inputs only |
| `smt-equivalent` | Z3 over the host's `references`, checked now | the reference source and its precondition | yes, in place of the body |

A check that does not pass leaves the class `declared` and names what it reached (`counterexample`, `unknown`, `failed-tests`), because unknown is never success. The comment above a declaration is always the author's claim.

```python
from cairn.agent.hosts.edits import EditSession

source = "fn step(x:u64)->u64 { return add_wrap(x, 1); }\nfn caller(x:u64)->u64 { return step(x); }\n"
reference = "fn step(x:u64)->u64 { if x == 18446744073709551615 { return 0; } return x + 1; }"
packet = EditSession(source, "caller", {"references": {"step": {"reference": reference}}}).packet()
assert packet["dependencies"]["step"]["evidence"] == "smt-equivalent"
```

## Requests beyond an edit

`expand` asks for up to 32 more functions or types. The host answers with each body as written, the types it uses and any card it adds, and the agent may then call what it expanded. `cairn inspect --symbol f --expand g` shows the same, and `--scope component` shows the whole component of the call graph.

```json
{"protocol": "cairn.edit/2", "handle": "e1", "kind": "expand", "symbols": ["append", "Header"]}
```

`explain` returns [`cairn explain`](tools.md#cairn-explain) for the functions the packet shows, on the last accepted candidate. The agent sees whether its edit left a guard in a loop or stopped it vectorizing, without running anything. A guard is a check, such as a bounds or an overflow check, that the compiler writes wherever it cannot prove an operation safe.

`predict`, as in `{"protocol": "cairn.edit/2", "handle": "e1", "kind": "predict", "sizes": [{"n": 1e7}]}`, returns [`cairn predict`](tools.md#cairn-predict) for the functions the packet shows, and after an admission the predicted ratio against the original at each size. A prediction is not evidence of speed, and the host still decides what is measured.

`shot`, as in `{"protocol": "cairn.edit/2", "handle": "e1", "kind": "shot", "functions": ["panel.ui.update"]}`, runs the latest accepted candidate once, headless, and returns every frame [`std.draw.capture`](library.md#stddraw) wrote. It adds the effect rows of the named functions, and what each gained or lost against the original. `cairn shot app --symbol f` does the same from the command line. Nothing opens a window or touches a device.

```json
{"schema": "cairn.shot/1", "status": "shot", "exit_code": 0,
 "frames": [{"frame": 1, "png": "…/shot-x/frame-1.png", "since_previous_ns": 3261000,
             "layout": {"width": 320, "height": 200, "at_ns": 17035994170318,
                        "elements": [{"name": "list", "x": 8, "y": 8, "w": 150, "h": 170}]}}],
 "effects": {"panel.ui.update": ["read:ui", "trap", "write:ui"]},
 "changed": {"panel.render.frame": {"added": ["ffi:write", "io"], "removed": []}}}
```

The layout record is what the program says it drew where. A claim such as "the detail panel does not overlap the list" is then checked on numbers, and a test can hold it. The rows say what the edit costs: a frame function that gained `alloc` allocates on every frame. A failed program still returns the frames it wrote. `state` and `delta` requests are [below](#the-programs-state).

## What the host accepts

The host splices a reply into the pinned original and checks the whole linked module again, whatever the packet showed. A reply that changes one body is checked against the record of the module's last full check: that one body is checked, and every rule after the bodies runs again, which gives exactly the answer checking every body again would give ([internals.md](internals.md#compiler-architecture)). Every token outside the range the reply may change must lex as it did, so a reply that ends in a comment that would hide the rest of the line is refused. The host refuses:

| Code | Why |
|---|---|
| `E-SIGNATURE` | the signature changed |
| `E-DECLARATION` | a declaration was added, removed or hidden behind a trailing comment |
| `E-EFFECT-EXPANSION` | the new body has an effect beyond the ceiling |
| `E-CALLER-EFFECT` | another function's row gained an effect |
| `E-CONTEXT-CLOSURE` | it calls a function the packet did not show |
| `E-SESSION` | the session is stale or the handle unknown |
| `E-SYMBOL` | an expansion names nothing, or two things |
| `E-PRESERVE` | a refactoring changed behaviour, or could not be shown to keep it (below) |

An accepted reply is `typed`, which says nothing yet about its behaviour. Its answer says what the edit did to the rest of the program, without a second request. `effects` and `check_sites` are the edited function's row and guard sites, beside `check_sites_before` when they moved. `changed` names each other function whose row moved, as `cairn state` names it; a caller whose row lost `trap` because the edited function no longer traps is there. No other function's guards can move, since a function's guards are its own body's and the edit keeps every declaration.

A refusal points into the reply the agent wrote (`line`, `column`, `source_line`, and `in` is `reply`), unless the check of the whole module found the fault elsewhere. A reply is refused by the first refusal that check meets, alone; [`cairn check`](tools.md#every-refusal-in-one-check) lists every one. The refusal's `card` names the rule card that states its rule. Its `repair_hint` is the smallest fix the host can state without guessing: a close name for an unknown one, the construct that brings each refused effect, the expand request that shows a callee, or the conversion between two types.

The host keeps the digests behind each handle, so the agent never copies a hash, and sends each card and the terms once per host. On thirty scripted edits of five example programs, a focused packet with one expansion took about a quarter of the context of the component packet, which shows the whole component of the call graph (`evidence/v1_0/context/`). No model took part in that measurement.

For a refactoring the host puts `{"preserve": "equivalent"}` or `{"preserve": "identical"}` in the contract, and the candidate is compared with the original as [`cairn diff`](tools.md#cairn-diff) compares two versions. `identical` accepts only the original's code up to renaming, and `equivalent` also what Z3 shows behaves the same. Anything else is `E-PRESERVE`, with any witness input as the `repair_hint`. An admission names the class it established in `equivalence`, or `not-proved` without the contract.

## The program's state

`cairn state`, or a `state` request, prints the program as it stands under one `digest`: every function of the program's own modules as `[signature, effect row]`, the declared types, the open diagnostics and the accepted edits. A refused program keeps its parsed signatures with a row of `null`, which means unknown, never empty. An agent that resumes work reads the state instead of the conversation that led to it.

```sh
cairn state examples/apps/kvstore > before.json
cairn state examples/apps/kvstore --since before.json    # only what changed
```

A `delta` request, or `--since`, gives only what changed, and applying it to the state it names reproduces the new digest exactly. A delta from a digest the host never sent is `E-SESSION`. Over [`cairn mcp`](tools.md#cairn-mcp), a `state` of a path the server has answered before is already a delta since the last state it sent of that path, and `whole: true` asks for the whole state again. A closed diagnostic shows as a `diagnostics` list without it. `cairn state --symbol f` is the state of one function's performance work instead ([below](#resuming-an-investigation)).

```json
{"protocol": "cairn.edit/2", "handle": "e1", "kind": "delta", "since": "<the digest of an earlier state>"}
```

## Interface migrations

An edit never changes a signature. A signature change, such as a new parameter or error type, is a separate authorization the host grants by name, which an edit session cannot reach (`E-REQUEST`).

```sh
cairn migrate app --symbol lib.checksum --to "fn checksum(n:usize, bytes:ro<u8>[n], seed:u32) -> u32" > packet.json
cairn migrate app --symbol lib.checksum --to "fn checksum(n:usize, bytes:ro<u8>[n], seed:u32) -> u32" --reply reply.json
```

The host names the function, its new signature, any other function whose signature changes with it (`--also NAME=SIGNATURE`), and the effects rows may gain (`--allow EFFECT`). The packet shows every function the migration may rewrite: the migrated ones and all their callers, in every file. A reply maps function names to whole new declarations. The host refuses:

| Code | Why |
|---|---|
| `E-SESSION` | the reply is for another authorization, or a file changed since |
| `E-MIGRATION-SCOPE` | it rewrites a function it was not authorized to |
| `E-MIGRATION` | it changes visibility, declares anything beside the function, or renames it; or the authorization carries more than 64 functions or reaches a vendored file |
| `E-SIGNATURE` | a signature is not the authorized one |
| `E-DECLARATION` | a declaration was added, removed or hidden behind a comment |
| `E-CALLER-EFFECT` | a row gained an effect the host did not allow |

The host checks the whole program again with every replacement in place. It then writes each file beside itself and renames it into place, putting every file back if one rename fails, so the project holds all the changes or none. Nothing is built or tested, and the result says so.

## Plan edits

A plan edit (`cairn.plan/1`, `agent/hosts/plans.py`) lets the agent change only how one function's regions are scheduled, which never changes a result. The packet lists the [plan items](concurrency.md#plans) the function's regions take, their ranges, the current plan and its predicted cost.

```json
{"protocol": "cairn.plan/1", "session": "<the packet's digest>", "items": {"grain": 1, "lanes": 8}}
```

A reply names items and whole numbers, never source text. The host writes the plan, checks the program again, and requires the checker's record of every function to be unchanged apart from this function's plan, which must be the one the reply set. It refuses an item the regions do not take or a value out of range (`E-PLAN`); anything else in the reply, a plan sent to an edit host or a body sent to a plan host (`E-REQUEST`); and a session already spent or reopened (`E-SESSION`). A plan changes no result, so an accepted plan needs no test to be correct, only a measurement to be worth keeping.

A session opens on a function of any module, named with its module (`lib.spread`). A name that two modules declare is `E-SYMBOL`, with the qualified names. `PlanHost().open(load_project(path), "lib.spread")` says under `written_in` the module, file and line after which the plan is written, under the name its module gives the function. A plan that named the function from another module, such as `plan lib.spread { ... }` in the root, is removed so the plan is not written twice.

## Implementation sessions

An implementation is an alternative version of a function, declared as `fn g(...) implements f when ...`, that a plan can select in place of `f`, its reference ([abstractions.md](abstractions.md#implementations)). An implementation session (`cairn.implementation/1`, `agent/hosts/implementations.py`) opens on one reference and accepts new implementations of it. Each is validated against the reference before the host keeps it, so a faster algorithm cannot change what the function means. [demos/implement](../demos/implement/README.md) runs one session end to end through `cairn mcp`.

```python
from pathlib import Path

from cairn.agent.hosts.implementations import ImplementationHost
from cairn.projects.project import load_project

project = load_project(Path("examples/implementations"))
host = ImplementationHost(regressions=project.root / "regressions/prefix.json")
policy = {"tolerance": {"absolute": 0.0, "relative": 0.0}, "domain": {"largest_extent": 4096}}
packet = host.open(project.source, "prefix", policy)
source = (project.root / "candidates/prefix_blocks.cairn").read_text()
answer = host.respond({"protocol": "cairn.implementation/1", "handle": "i1", "kind": "submit", "source": source})
assert answer["status"] == "validated" and answer["select_with"] == "plan prefix use prefix_blocks;"
```

The packet shows the reference's declaration, row, ceiling and roundings, and the implementations it already has. It also shows what the host pinned, each with its digest: the tolerance on float results and the [numerical policy](verification.md#validating-an-implementation) that applies it, the test policy (cases, seed, shrinking budget, time per call), and the permitted inputs. A submission is one implementation of the reference, new or replacing one of the same name, with any helpers it calls.

The host splices it in, checks the whole program again under every `E-IMPL-*` rule, and runs [`cairn validate`](tools.md#cairn-validate) under the pinned policy. Only a validated implementation advances the source. The answer says how to select it, and selecting is the host's decision.

| Code | Why |
|---|---|
| `E-REFERENCE` | the submission redefines the reference, implements another function, or names a `reference` field |
| `E-TOLERANCE` | it names a tolerance or the numerical policy |
| `E-TEST-POLICY` | it names cases, a seed, a budget or a policy, or holds a test block |
| `E-DOMAIN` | it names a domain, inputs or a precondition |
| `E-DECLARATION` | it holds anything but functions, two implementations, a plan, or a helper that would replace a function of the program |
| `E-CALLER-EFFECT` | another function's row grew |
| `E-VALIDATION` | validation failed, with the shrunk input in `failed`, found by a finite case or by Z3's counterexample replayed natively, and the `repair_hint`; or it could not decide, which is never success |

The compiler's own refusals come back as they are (`E-IMPL-SIGNATURE`, `E-IMPL-WHEN`, `E-IMPL-EFFECT`, `E-IMPL-PARAM`, ...), located in the submission. A failing case is kept in the regressions file for the project's next test run. A submission with [natural parameters](abstractions.md#implementations) is accepted only when every instance its `tune` clause lists validates. The answer gives each instance's result under `instances`, and a failing one is `E-VALIDATION` naming the instance.

A validated answer holds the identity, the condition, the row and what the implementation requires of the machine, and under `changed` each function the submission added or whose row it moved, as `cairn state` names them. It holds the finite result with its counts and its label, which says finite testing and never proof, and Z3's answer apart from it.

When the host names a `records` directory, every submission goes to the [candidate history](#candidate-history): a `validation` record, or a `failure` record with its stage and why. The record's identity is made of the reference as written, the implementation's own identity, the pinned contract with the numerical policy, and where validation ran; the record keeps the native compiler that built it. A `history` callback gets the same entry. [examples/implementations](examples.md#examplesimplementations) replays a scripted agent through one session.

A device implementation is `unknown` to validation unless the session emulates the device. `host.open(source, "scale", policy, emulate=parse("sm_120"))`, or `emulate: "sm_120"` in `implementation_open` over [`cairn mcp`](tools.md#cairn-mcp), validates device code on host threads, judged against that target ([devices.md](devices.md#emulating-device-code-on-the-host)). The answer's `evidence` is then `finite-tested-emulated`, and its `emulation` names the target. The history keeps it under the target `host emulation of sm_120`, and `cairn tune` chooses on it only with `--accept-emulated`.

## Candidate history

`agent/history.py` keeps what was tried on a function, what failed and why, what was measured and how, and what is only a hypothesis, so that an agent or a search does not repeat work that still holds. `cairn tune` records into it, and so can any host. Records are appended to `records.jsonl` in the history directory:

```python
from cairn.agent.history import as_written, identity, record

source = "fn spread(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = u64(i); } }\n"
variant = {"plan": {"lanes": 4}}
made = identity(as_written(source, "spread"), variant, {"kind": "plan"}, {"kind": "host", "arch": "x86-64-v3"})
claim = {"claim": "four lanes may be enough at n=1e5"}
kept = record(".cairn/history", "hypothesis", "spread", "plan spread { lanes 4; }", made, claim, variant)
assert kept["kind"] == "hypothesis" and len(kept["id"]) == 16
```

Every record is about one candidate, and carries the candidate's identity in five parts:

| Part | What it holds |
|---|---|
| `source` | the function and everything it calls, as lowered and canonicalized, with the variant that makes the candidate; an edit elsewhere, a comment or a renamed local leaves it alone |
| `contract` | what the candidate must preserve |
| `target` | the host architecture or device target |
| `compiler` | `implementation_hash()` with the runtime headers |
| `artifact` | the build output, when there is one |

Each record has one kind, and a kind requires what makes it evidence:

| Kind | Requires | What it is |
|---|---|---|
| `attempt` | | a candidate or a search that was tried |
| `failure` | `stage`, `why` | a refusal by the checker, nvcc or a validation, or a run that failed |
| `validation` | `evidence` | a check of behaviour, with the evidence class it established |
| `observation` | `by` | a compiler's static reading: resources, instruction counts, a prediction |
| `measurement` | `procedure` | a timed run, with the procedure that produced its numbers |
| `profile` | `tool`, `run` | a profiler's reading from an explicit profiling run, apart from timing |
| `hypothesis` | `claim` | an explanation nothing has confirmed |
| `experiment` | `run`, `tests` | a run that would confirm or refute a hypothesis |

`History(where).judged(function, base, contracts, targets)` returns a record as `current` while its source, contract and compiler match the program now and its target is one the caller works on. Otherwise the record is under `stale`, with the parts that moved. An equal record is kept once. An analysis, such as what a compile read, is kept under the digest of everything it read, with its files.

A function's implementations are not part of its source. A candidate that selects one names it by its identity, by everything it calls as lowered, and by the sha256 of each vendored source it reaches. [`cairn tune`](tools.md#cairn-tune) and `cairn state --symbol` therefore cite a validation only while the implementation, its helpers, its vendored sources and its reference are as they were, and while no input that failed its validation is kept under the same contract. `judged` rebuilds a record's source from the variant it stored, to compare it with the implementation as it is now.

An implementation has one name in the history, the selection that runs it: `plan prefix use prefix_by4;`, or `plan prefix use prefix_by[16];` for an instance. The name is the same whoever kept the record (`cairn tune`, `cairn validate --history` or an implementation session), so the investigation packet shows what was validated, compiled and measured of it under that one name.

## Resuming an investigation

`cairn state --symbol f` prints the investigation of `f`, the performance work done on it, from its history. The history is `--history DIR`, by default `.cairn/history` beside the manifest, and the answer is current for this host (`--arch`, `--cxx`) and for the [device target](tools.md#the-device-target) (`--device-target`):

```sh
cairn state app --symbol lib.spread > before.json
cairn state app --symbol lib.spread --since before.json    # only what changed
```

```json
{"protocol": "cairn.investigation/1", "function": "spread", "plan": "(no plan for spread)",
 "regions": [{"id": "spread@b3bfc3de", "kind": "host", "line": 6, "binder": "i"}],
 "searches": [{"by": "cairn tune", "configurations": 36, "legal": 36, "chosen": "plan spread { grain 1; }",
               "measured_best": "plan spread { grain 1; lanes 16; }", "ranked": [["plan spread { grain 1; }", 49190.0], ...]}],
 "candidates": {"(no plan for spread)": {"measured": [{"median_ns": 633053.5, "min_ns": 628449.25, "max_ns": 649618.5,
                                                       "procedure": "p0", "sizes": {"n": 20000.0}, ...}]}, ...},
 "procedures": {"p0": "cairn.perf.measure: the candidate built with the project's flags beside a driver that fills each view, timed in blocks of at least 2 ms, the median of 3 blocks, on this host", ...},
 "hypotheses": [], "experiments": [], "stale": {"records": 0, "by_part": {}}, ...}
```

Beside the function's signature, row, plan, regions and current identity, the packet holds only the history that still holds. For each candidate that is what was measured and by which procedure, what a compile read, what failed and why, and what was validated or profiled. It adds the last searches and what they ranked best, and the hypotheses and suggested experiments; an experiment is `done` once the runs it asks for are kept. Records that no longer hold are counted under `stale` by the part that moved.

A validation holds while the implementation, its reference and the compiler are as they were, under the tolerance and on the host it names. It holds only under this numerical policy and the native compiler that the packet's host builds with, as `cairn tune` cites it. The packet above was 2,959 bytes for a function with three measured candidates, and running the same `cairn tune --measure` again starts no run, since every measurement it needs is kept.

## Named choices

A host can instead name expressions inside a function, called slots, ask the model for a new expression in each, and check the result against a reference. The model then writes expressions, never a whole function:

```python
from cairn.agent.sketches import Sketch, ScalarContract

source = "fn average(x:u64,y:u64)->u64 { return (x+y)/2; }"
reference = """fn average(x:u64,y:u64)->u64 {
  return (x/2)+(y/2)+((x%2+y%2)/2);
}"""
sketch = Sketch(
    source,
    "average",
    task={"task": "Return floor((x+y)/2) for all u64 inputs, without traps."},
    semantic=ScalarContract(reference, "average"),
).hole("value", "(x+y)/2")
packet = sketch.packet()
# Send packet to a model using the caller's chosen adapter.
# Here this explicit string is an authored example, not a model response.
reply = '{"value":"(x & y) + shr(x ^ y, 1)"}'
candidate = sketch.fill_json(reply)
result = sketch.check_semantics(candidate)
assert result["status"] == "smt-equivalent"
```

The reply maps each slot to an expression string. `fill_json` refuses duplicate keys, extra fields, values that are not strings, text that would escape its slot, stale bindings, a choice that carries a comment (`E-SKETCH-CHOICES`), and any choice the compiler refuses. The binding between a reply and its session lives in one host process, so a saved JSON map is not an approved patch on its own.

Types cannot tell `x+y` from `add_wrap(x,y)`, which differ only at overflow. For the scalar fragment the checker therefore asks Z3 for an admitted input on which the candidate and the reference differ, or on which only the candidate aborts. A distinguishing input is replayed in an independent Python interpreter before it is shown to the model. Code outside the [SMT model](verification.md#value-level-source-equivalence) goes to finite tests, and anything the model cannot decide is `unknown`. The reference can misstate what a person wanted, and a nontrivial `--assume` is a precondition on callers that the native build does not check.

```sh
python3 tools/ai/sketch_demo.py
python3 tools/checks/semantic_check.py examples/sketch/reference.cairn examples/sketch/after.cairn --symbol average --obligations build/obligations
python3 tools/checks/build_library.py examples/sketch/after.cairn
```

The receipt records the reference and candidate hashes, the domain, the query hashes and the Z3 version, and `--obligations` saves the SMT-LIB queries so anyone can run them again.

## The card for named choices

This is what the model is told when the host asks for named expressions instead of a whole function:

Read the host packet: the task, the source, the named slots, the expected types, the local bindings, the allowed effects and the selected language cards. Return only one JSON object that maps EVERY slot name to a CAIRN expression string, with no Markdown, extra keys or duplicate keys. An example reply is {"value":"(x & y) + shr(x ^ y, 1)"}.

The host inserts the choices into its pinned original source with parentheses, then checks the complete module again. Do not rewrite function signatures, the task, the reference, permissions, preconditions, compiler settings or tests. Missing context is no permission to invent a dependency. Slot names are descriptive identifiers; they are neither code nor persistent IDs. The host binds your reply, so do not invent hashes.

Use familiar expressions, and keep CAIRN's meaning: integers of fixed width; ordinary +, - and * trap on overflow; unsigned add_wrap, sub_wrap and mul_wrap wrap and say so; casts and shift counts are checked; loops are sequential; nothing allocates implicitly. A type error asks for a local repair of a type, so keep the public API. A semantic counterexample gives an input where your proposal returns a wrong value or traps. Fix the algorithm on the original domain, and do not patch only that example.

Typed means only that the static checks passed. The optional scalar checker compares with a fixed host reference for all inputs of the declared widths, under a total, nonempty host precondition. smt-equivalent trusts the translator and Z3; it is neither a Lean proof nor a proof about native code. It models integers, bools, floats, records, sums, branches, acyclic calls, array views and their parts, fixed storage and storage local to a function, `compact`, host `reduce` and loops it can unroll. It does not model an owner held inside a record or an array, recursion, concurrency or GPU execution. Unknown and timeout are never accepted. Tests and benchmarks are separate stages. Supplying this card trains no model and evaluates none.

## Closed generator contracts

These contracts are the packet an agent gets for editing the existing family and wire examples. They are neither the full language card nor a proof certificate. `compiler/derive/expansion.py` expands families and recipes, `std/wire.cairn` is the wire recipe, and `verify/linear_certificates.py` checks the collector; changing any of them needs a larger audit.

family/1. A static function with one natural parameter K and `family prefix = base[a..b];` produces b - a independent monomorphic functions, `prefix_a` through `prefix_(b-1)`. Each uses its literal K. An expansion that is empty, negative, reversed, oversized, colliding or unbound is refused, and every emitted function is checked. There is no dynamic dispatch, allocation or evaluation of source strings. A larger range costs object size and compilation work. Expansion is capped at 1024 functions per family, 2048 in all, and 200000 expression visits while checking.

wire/1. A record of fixed unsigned fields derives functions that encode it, decode it and give its length in bytes. Fields go in declaration order, each little endian, with no padding on the wire. The native layout of the record is not the wire layout. The encoded size is the sum of the field widths divided by eight, and every bit of every field is kept. The caller must supply a buffer of exactly the live extent, since guards cannot recover a forged pointer's provenance. No tags, checksums, value ranges, alignment padding, implicit versions or allocation are added.

bounded-collector/1. On an immutable extent n with an output capacity of n, the collector visits indices 0..n in order. It evaluates the predicate, which only reads, once per input. It evaluates the projection, which also only reads, only for selected inputs, and appends its value once. The predicate and the projection never read the output. The private cursor k starts at zero and satisfies k <= i at the start of iteration i, so every output store has k < n and the final k <= n. Elements of the tail that were not selected are unchanged. Inputs must not alias the output; entry checks enforce disjoint intervals under the FFI precondition. No worker threads, buffering, reordering, guarantee of vectorization, or hidden heap allocation are part of this contract.

## Training material and trials

`tools/corpus/` holds teaching material written by hand in the protocol's format: 40 tasks in 14 algorithm families, each with an equivalent and an inequivalent implementation, 28 preference pairs, and 29 executed repair transcripts. Its answers ship with it, so none of it is held out as a test. Solver timeouts, failed translations and tool errors never become positive labels, and edits that weaken a signature or empty a domain are not rewarded.

Three experiments are designed. [tools/ai/protocol_trial.md](../tools/ai/protocol_trial.md) compares the focused and component packets on twelve planted repairs, and has not run. [bench/ai/PREREGISTRATION.md](../bench/ai/PREREGISTRATION.md) gives ten tasks to fresh model subjects in CAIRN, C++ and Rust at equal budgets; it ran before the plugin existed, and its results are under `evidence/v1_0/ai_benchmark/`. [bench/ai/PREREGISTRATION_V1_1.md](../bench/ai/PREREGISTRATION_V1_1.md) adds a plugin arm and three tasks where CAIRN's checks are the point. Its counted run stopped at 54 of 156 subjects, and `evidence/v1_1/ai_eval/` reports those as they stand. Only the second and third compare languages, and none tests other model families or large programs.
