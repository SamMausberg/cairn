# Evidence versions

One directory per release: `v0_5/`, `v0_6/`, `v1_0/`, `v1_1/`. In each, `summary.json` is the entry point and `RUN_NOTES.md` says what ran, on which machine, and what was not done. `v1_0/` also holds `lean/`, `gpu/` and `embedded/` records; `v1_1/` holds `lean/` and the preregistered `ai_pilot/`. `v1_2/host_regions/` is a later measurement of host parallel regions and supersedes the host-parallel column of `v1_0/gpu/benchmark.json`. `verification-run.json` is a retained child-command log from an earlier layout, run on an x86-64 host.

Every directory keeps the names and source identity it was recorded under. Earlier releases are history, not fresh measurements, and no old result confers verification on new source. `tools/release/collect_evidence.py` writes a new one; [testing.md](../docs/internals/testing.md) says how.
