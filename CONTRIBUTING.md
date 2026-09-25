# Contributing

[AGENTS.md](AGENTS.md) is the working agreement for anyone who changes this repository, human or agent. Read it first, then [docs/internals.md](docs/internals.md), which says how the compiler is built and tested.

Set up a checkout with:

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cairn doctor
make lint test
```

`compile_flags.txt` gives clangd the runtime's C++20 flags and include path, and `.clangd` reads the CUDA header and the device tests as CUDA. With both, the headers under `src/cairn/runtime/` and the native tests read cleanly in an editor.

## Tests and checks

Run `make lint test` before and after you touch code, or `make lint test JOBS=4` for an agent on a shared machine, as [AGENTS.md](AGENTS.md#development) says. Run `make native` when you touch the runtime or the lowering, `make lean` when you touch a rule the calculus models, and `make gpu` or `make embedded` where the hardware is present. `make gpu`, `make tune-device` and `make calibrate-device` are the only targets that run code on a device. They run one at a time, and only the owner of the machine starts them.

A pull request needs one check green, `ci-passed`, which passes only when every job the workflow ran passed. On a pull request those jobs are the lint and example checks, the suite, the proofs, device code compiled under CUDA 13.2 with both host compilers, and the installed package. The compatibility jobs run on `main` and every Monday: device code under CUDA 12.9, the oldest supported and the newest compilers, Python 3.11, 3.13 and 3.14, and an AArch64 host ([internals.md](docs/internals.md#continuous-integration)).

A pull request also needs a rejection test for every rule it adds and a behaviour test for every observable change.

## Documentation

The suite compiles every fenced `cairn` example in the documentation, so change the documentation in the same commit as the behaviour it describes. `make docs` generates `docs/std_api.md`, the pages under `docs/std/` and the capability matrix in `docs/verification.md`, and `make editors` generates the editor grammars and `skills/cairn/`. The suite fails while any of them is stale. `make site` builds the documentation website from `docs/`, after `pip install -r tools/site/requirements.txt`.

## License

By contributing you agree that your contribution is licensed under the same terms as the project, MIT or Apache-2.0 at the recipient's option, with no additional terms.

## Pull requests

Every change reaches `main` through a pull request that merges itself when CI passes. [AGENTS.md](AGENTS.md#development) says how to branch, title and land one, and each section of [the template](.github/PULL_REQUEST_TEMPLATE.md) says what the description holds.

## Issues and labels

Open an issue with one of its forms: a bug, a language change, a performance problem, or a kernel or benchmark request. Each form asks for what reproduces the problem and applies its `kind:` label. A soundness bug goes privately to the owner, as [SECURITY.md](SECURITY.md) says.

The labels are listed in [.github/labels.yml](.github/labels.yml), and `tools/release/sync_labels.py` applies that file when the owner runs it. Each prefix answers one question, so an issue has one `kind:` label and any number of the others.

| Prefix | What it says | Who applies it |
|---|---|---|
| `kind:` | what it is: `bug`, `soundness`, `performance`, `language`, `tooling`, `kernel` or `evaluation` | the issue form at filing, the author of a pull request, a maintainer who corrects it |
| `area:` | the part of the tree it touches: `compiler`, `runtime`, `projects`, `agent`, `editor`, `perf`, `verify`, `docs`, `ci` or `examples` | a maintainer at triage; an author may add it |
| `backend:` | the target it concerns: `host`, `cuda`, `freestanding`, or `hip`, which does not exist yet | a maintainer at triage; an author may add it |
| `needs:` | what it waits on: `hardware` CI lacks, such as a GPU, or a `design` decision | a maintainer, who removes it when the need is met |
| `good first issue` | small work that stands alone, whose owning file the issue names | a maintainer |

A label outside the file is not used. To add one, change the file in a pull request first, and the owner then runs the sync. The sync creates and updates labels and never deletes one.
