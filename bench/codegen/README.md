# Emitted code

What the emitter writes, measured without running a program. Everything here compiles or counts text; nothing is timed.

| File | What it measures | Needs | Writes |
|---|---|---|---|
| `codegen_only.py` | object sections of `examples/basics/native.cairn` against the independently written C++ in `bench/host/reference.cpp`: a section is identical only if its bytes and relocations match | `clang++`, and `results/native/` from `tools/checks/verify.py`; `make native` runs both | `results/codegen/codegen.json` |
| `guard_counts.py` | the guards the emitter writes for every example project, the single-file examples in `examples/basics` and the bench kernels, as emitted and with every guard kept, and the entry checks calls no longer run | nothing beyond the package | `results/lowering/guard_counts.json` |
| `guards.py` | what the two guard counts share: the programs of a checkout they compile, and the kind of each guard call they count | | |
| `guard_delta.py` | the same guard counts from two compilers (two `src` directories) over one corpus, so a lowering change is measured on fixed input | two checkouts | `--out FILE` |
| `family_template.cpp` | a compact C++ family of the 256 operations `examples/basics/family.cairn` writes, for the token counts of `tools/checks/density.py` and a native build in `tools/checks/verify.py` | | |

```sh
make native                                    # verify.py, then codegen_only.py
python3 bench/codegen/guard_counts.py
python3 bench/codegen/guard_delta.py OLD/src NEW --out results/lowering/delta.json
```
