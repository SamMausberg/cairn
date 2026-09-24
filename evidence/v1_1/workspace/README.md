# One compile per distinct source

What was run to support the claim that an agent over `cairn mcp` no longer waits for a whole check when nothing it asks about has changed. The change is the commit that adds `src/cairn/compiler/compilations.py` ("The hosts, cairn state, cairn mcp and the language server share one compile per distinct source, and an admission names the rows it moved"), timed as 5fa2886 before it was rebased onto main. The before arm is dd3f75e, the main it was rebased onto. Everything ran on 2026-09-24 on one machine: an AMD Ryzen 7 7800X3D (8 cores, 16 threads) under WSL2 (Linux 6.18), Python 3.12.3. Four other agents were running test suites on it at the same time, and the load average was between 538 and 986 while these timings ran, so every number here is slower and noisier than on an idle machine.

## What ran

`bench/workspace/measure.py` starts one `cairn mcp` per project from the checkout it is given, and times each tool call as the client sees it: ten `check` calls on the unchanged project, two `edit_open` calls on one function, and three rounds of `edit_open`, an admitted `edit_request` that writes a body no earlier round wrote, and `check`. It ran on a copy of `examples/apps/analytics` (775 lines in `src/`) and on `bench/scale/generate.py --modules 185` (64 files, 9,579 lines). The before arm ran from a `git archive` of dd3f75e, the after arm from this repository, one right after the other.

```sh
python3 bench/workspace/measure.py --root /tmp/base --commit dd3f75e --modules 185 --out before.json
python3 bench/workspace/measure.py --modules 185 --out after.json
```

## What it showed

Seconds per call, the median where a part has several calls. A cycle is one `edit_open`, one admitted edit and one `check`.

| | analytics before | analytics after | generated before | generated after |
|---|---|---|---|---|
| first `check` | 0.310 | 0.360 | 1.912 | 1.598 |
| each later `check` | 0.298 | 0.0055 | 2.233 | 0.016 |
| first `edit_open` | 0.653 | 0.070 | 4.712 | 0.647 |
| second `edit_open` | 0.631 | 0.040 | 5.100 | 0.381 |
| edit-then-check cycle | 1.347 | 0.370 | 7.561 | 2.359 |
| `check` after an admitted edit | 0.332 | 0.0035 | 1.548 | 0.017 |
| server peak resident memory, MiB | 59.6 | 65.3 | 255.0 | 312.8 |

A `check` of an unchanged project, and the `check` that follows an admitted edit, answer from a compile the server already made, and each such answer says `"cached": true` in `after.json`. An edit session opens from that compile too, since it no longer checks the program twice and records expression sites only when a site is asked for. What a cycle still costs is the admitted edit itself: the candidate is a new program, checked and emitted whole, about 0.33 seconds on analytics and 2.1 on the generated project in both arms. The first `check` is a whole compile in both arms and also pickles what it made; the difference between the arms is inside the spread of this loaded machine.

The server held 5.7 MiB more at its peak on analytics and 57.8 MiB more on the generated project. What the cache keeps is bounded at 16 programs and 256 MiB of pickles, and `tests/agent/test_compilations.py` and `tests/agent/test_mcp.py` hold that bound with smaller ones; this run did not reach it.

## What the tests establish

`tests/agent/test_compilations.py` compiles every example project and `examples/basics` program from scratch and from a kept copy, and requires the same C++, the same manifest and the same receipts, with and without `every` and expression sites. That is finite testing on those 25 inputs, not a proof that a copy is faithful for every program. The same file holds that a change a caller makes to what it got never reaches the next caller, and that a changed source, a changed `std` file or a changed compiler digest compiles again.

## What did not run

`make scale`, which times builds and a language-server refresh up to 1,480 modules, did not run, and neither did the language server's own timing (`bench/scale/driver.py --lsp`). `before_repeat.json` and `after_repeat.json` are an earlier pair of the same commits under similar load, a few minutes apart rather than back to back. Every row but the first `check` moved the same way in it, and its first `check` took 0.39 and 0.39 seconds on analytics and 1.94 and 2.07 on the generated project. Nothing checks less than a whole program after an edit: [docs/roadmap.md](../../../docs/roadmap.md) lists what rechecking only what an edit reaches would have to redo. Nothing here runs device code.
