# Tools

Scripts that check the compiler, generate fixtures, write records and cut releases. None of them is part of the installed package, and each one's docstring says what it does and how to run it.

| Folder | What it holds |
|---|---|
| `checks/` | independent checks of the compiler: native builds against Python oracles (`verify.py`, `validate_*.py`), the differential runs against the Lean models (`differential_*.py`), `emission_identity.py`, the Lean certificate export, density, and the scripts that write records under `evidence/` (`execution_counts.py`, `perf_validation.py`, `foreign_records.py`) |
| `corpus/` | generated teaching fixtures beside the scripts that write and check them; each generator's `--check` fails when a committed file differs from a fresh run |
| `ai/` | the agent loop, its scripted demos, the context measurement and the model trials, which run a model only when given one |
| `release/` | the release gates' collectors, the repository audit and the opt-in private publisher |
| `support.py` | what the scripts share: compiler flags and profiles from `cairn.projects.toolchain`, the device lock, fixture drift, and where `lake` is |

[docs/internals.md](../docs/internals.md#testing) says what each check establishes, and [docs/internals.md](../docs/internals.md#releasing) how the release scripts are used.
