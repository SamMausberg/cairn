# Releasing

A release is a tag on `main` whose evidence directory records what ran, on which machine, and what did not. Nothing is published by a test or a script; every outward step is an owner action.

## Versions

One version string is stated in six places, and they must agree: `pyproject.toml`, `src/cairn/version.py` (which the receipt reports as the profile `cairn-native/<version>`), `proofs/lakefile.toml`, `editors/vscode/package.json`, the `profile` of `docs/project/capabilities.json` and the opening of the `base` rule card in `src/cairn/agent/teaching.py`. CHANGELOG.md gets one section per release, written in the same categories as the ones before it: language, projects and tools, runtime, verification, reviews and users.

## The sequence

Every change lands with the gates AGENTS.md lists. Then, on a clean committed tree:

```sh
make all native systems                        # lint, the suite, proofs, sanitizers, the systems examples
make gpu embedded                              # where the hardware and the emulator are present
make bench && python3 bench/suite/report.py    # the preregistered CPU suite, hours
python3 tools/release/collect_evidence.py --release v1_3
python3 tools/release/collect_lean_evidence.py --release v1_3
make wheel audit
```

`collect_evidence.py` runs the release gates and writes `evidence/<release>/summary.json`: each gate's command, status, exit code, seconds and last lines, `cairn doctor`, the commit, whether the tree was dirty, and source-line counts. It writes nothing else. `collect_lean_evidence.py` rebuilds `proofs/` from scratch and writes the build log, the axiom audit, the toolchain versions and a summary under `lean/`. The GPU record is copied from `results/gpu/benchmark.json`, the freestanding transcript from `cairn run examples/embedded`, and the bench tables from `results/bench_suite/`, each into its own subdirectory of the release. `RUN_NOTES.md` beside them names what did not run and why: a gate whose tool is absent is `unavailable`, never passed, and a machine that cannot run a target says so.

Tag the commit the evidence names, `git tag -a v1.3.0`, and push `main` and the tag. A release that does not pass every gate on a committed tree is not tagged; the failure is recorded and fixed first.

## What a release may claim

Only what its evidence directory shows. Accepted, typed, native-built, finite-tested, sanitizer-clean, SMT-equivalent, Lean-checked and benchmarked are separate claims, and unknown is never any of them. An earlier release's evidence is history, and no old result confers verification on new source. A speed, a GPU advantage or an AI result is claimed only with an executed run recorded under `evidence/`, with its losses beside its wins.

`docs/project/capabilities.json` and [roadmap.md](roadmap.md) are rewritten at each release to match what landed, and a gate closes there only when its tests, docs and evidence exist.

## Hosting and the private publisher

The repository is hosted on GitHub and licensed MIT or Apache-2.0. Changing its visibility, force-pushing, deleting a remote resource or uploading to a package index is never done by a script here.

`tools/release/publish_private.py` is the one scripted path that touches a remote, and it creates a new private personal repository and pushes `main` to it, nothing more:

```sh
python tools/release/publish_private.py SamMausberg/cairn             # local-only dry run
python tools/release/publish_private.py SamMausberg/cairn --execute   # needs an authenticated gh
```

It runs the audit of `tools/release/audit_repository.py` over every reachable committed blob first, requires a clean tree on `main` with no configured remote, creates the repository as the collision check, verifies identity and privacy before and after an exact-commit non-force push, and then registers `origin`. It never asks for a token, installs a credential helper, reuses an existing repository, forces, deletes or changes visibility. A failure after creation can leave an empty private repository, which it does not delete. Its tests use fakes for every remote interaction, so a passing test shows the branches it takes, not that a real account will accept the push.
