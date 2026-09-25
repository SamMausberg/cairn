# Scale

These scripts measure how the tools grow with a project's size. `make scale` runs them, which takes about an hour.

| File | What it does |
|---|---|
| `generate.py` | writes a synthetic, deterministic project of a chosen number of modules, over at most 64 source files by default (`--files`) |
| `driver.py` | runs one `cairn` command in a cold process and prints its wall time and peak resident memory; `--unlimited` lifts the compiler's size limits, to measure past them |
| `measure.py` | for each size: `cairn check`, a build of the whole program, a cold incremental build and one more with nothing changed, three incremental builds after an edit (a body in a leaf module, a body in the module every other one imports, and that module's interface), and one refresh of the language server |

`measure.py` writes `results/scale/measure.json` and prints a Markdown table. The native builds need `clang++`, and nothing runs on a device.

```sh
python3 bench/scale/measure.py --modules 185 370 --native 185
```
