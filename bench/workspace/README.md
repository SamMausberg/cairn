# Workspace

This script measures what an agent waits for over `cairn mcp`, before and after a change to the compiler's tools. `measure.py` copies `examples/apps/analytics` and a project that `bench/scale/generate.py` writes into `results/workspace/`, starts one `cairn mcp` per project, and times each tool call as the client sees it:

| Part | Calls |
|---|---|
| checks | ten `check` calls on the unchanged project |
| opens | two `edit_open` calls on one function |
| cycles | three rounds of `edit_open`, an `edit_request` the host accepts, which writes a body no earlier round wrote, and `check` |

It records every call, a summary, the server's peak resident memory from `/proc`, and the machine's load average before and after each project. It needs Linux and the Python that runs the test suite. Nothing runs on a device, and no native compiler is started.

```sh
python3 bench/workspace/measure.py --modules 185 --out results/workspace/after.json
git archive BASE bin src | tar -x -C /tmp/base
python3 bench/workspace/measure.py --root /tmp/base --commit BASE --modules 185 --out results/workspace/before.json
```

`--root` names the checkout whose `bin/cairn mcp` is timed, so one script times two versions alike. [evidence/v1_1/workspace](../../evidence/v1_1/workspace/README.md) keeps a run.
