# Bounded search

What `cairn tune`'s search costs, and what its candidate history saves. Device programs are compiled for sm_120 and read with ptxas and cuobjdump, which need `nvcc`; `search.py` records a device program as skipped without them. Nothing runs on a GPU. Host timings run on this machine, and the load average is recorded before and after.

| File | What it measures | Writes |
|---|---|---|
| `search.py` | per program: a cold search with a compile budget, the same search again answered from the history, twice the budget, and no budget; one host program also timed with `--measure`, cold and again | `--out FILE` (required) |
| `wall.py` | the wall time of the search itself on the suite's kernels, `examples/cooperative/tuned.toml` and three device programs, three times with no compile and once with a compile budget, for the CAIRN `--src` names; and one check of the tuned example, `blur` and `stencil_1d` against the part of it a plan cannot reach; with `--every`, the compiles that give every candidate of `blur` and `two` a reading | `--out FILE` (required) |
| `instances.py` | the search over `examples/implementations`' parameterized `prefix_by[K]`: each instance validated, then searched predicted-only and with `--measure 4`, every selection retimed in five interleaved rounds, and a device program searched with 4 compiles | `results/search/instances.json` |

```sh
python3 bench/search/search.py --out results/search/search.json
python3 bench/search/instances.py
python3 bench/search/wall.py --out results/search/wall.json
```
