# Scale

How the tools grow with a project's size. `make scale` runs it, about an hour.

| File | What it does |
|---|---|
| `generate.py` | writes a synthetic, deterministic project of a chosen number of modules, over at most 64 source files |
| `driver.py` | runs one `cairn` command in a cold process and prints its wall time and peak resident memory; `--unlimited` lifts the compiler's size limits for measuring past them |
| `measure.py` | for each size: `cairn check`, a whole-program build, a cold and three warm incremental builds after one-function edits, and one language-server refresh |

`measure.py` writes `results/scale/measure.json` and prints a Markdown table. The native builds need `clang++`; nothing runs on a device.

```sh
python3 bench/scale/measure.py --modules 185 370 --native 185
```
