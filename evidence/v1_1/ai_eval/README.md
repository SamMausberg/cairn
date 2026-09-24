# The 1.1 evaluation: CAIRN with its plugin, CAIRN with its documentation, C++ and Rust

This folder holds the records of the evaluation [bench/ai/PREREGISTRATION_V1_1.md](../../../bench/ai/PREREGISTRATION_V1_1.md) fixes: its pilot, and its counted run as it stood when it stopped early, 54 of 156 subjects. [RESULTS.md](RESULTS.md) says what the counted subjects did and what may not be read into it, and [RUN_NOTES.md](RUN_NOTES.md) lists every event of the run. `tables_counted.md`, `results_counted.json` and `subjects/counted/` hold every counted subject, and the eleven set aside for a harness failure are under `subjects/counted/r1/*/plugin/set_aside/`. `verify.json` is the check of every reference and starter at the pinned commit, dd3f75e.

## The pilot

The pilot gave `block_scan` to one fresh subject per arm on 24 September 2026: `claude-sonnet-5` at effort `high` in Claude Code 2.1.281, three sessions at a time, each judged by the hidden check as it finished. The toolchain was a wheel of branch commit ba9eb67, whose compiler, documentation and skill are main's at 2679cb3. The machine is an AMD Ryzen 7 7800X3D (16 threads, 70 GB) under WSL2 (Linux 6.18, Ubuntu 24.04), with clang++ 21.1.8, g++ 13.3.0, cargo and rustc 1.95.0 and Python 3.12.3, shared with other agents' test suites.

All four solved it. `tables_pilot.md` has each subject's numbers and `results_pilot.json` the same data. Each subject's final program, its record and its transcript (compressed with xz, and named in the record by the sha256 of the uncompressed file) are under `subjects/pilot/`. `audit_decisions.json` records why each audit flag was cleared: a `find` over the pilot's root that never ran, because the command before its `||` succeeded, and reads of the machine's CPU limits.

After the pilot, `harness.py verify` showed every reference passing its hidden check and every starter failing it at ba9eb67, 91 of 91 as required. It runs again at the pinned commit before the first counted subject.

## What did not run

The counted run's remaining 102 subjects, and the rerun of the replicate 1 `block_scan` plugin cell. No device code ran on a GPU: the CAIRN subjects' `block_scan` programs, and the judge's builds of them, ran the device work on host threads under `--emulate`. The pilot's subjects shared the machine's `/tmp`, which the counted run replaced with a `/tmp` of each subject's own. The counted run used the same machine and tools as the pilot, the toolchain built from dd3f75e.
