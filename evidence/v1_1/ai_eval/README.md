# The 1.1 evaluation: CAIRN with its plugin, CAIRN with its documentation, C++ and Rust

This folder holds the records of the evaluation [bench/ai/PREREGISTRATION_V1_1.md](../../../bench/ai/PREREGISTRATION_V1_1.md) fixes. So far only its pilot has run. The counted run starts from the commit that brings the preregistration to main, and its results will be in `RESULTS.md` here.

## What ran

The pilot gave `block_scan` to one fresh subject per arm on 24 September 2026: `claude-sonnet-5` at effort `high` in Claude Code 2.1.281, three sessions at a time, each judged by the hidden check as it finished. The toolchain was a wheel of branch commit ba9eb67, whose compiler, documentation and skill are main's at 2679cb3. The machine is an AMD Ryzen 7 7800X3D (16 threads, 70 GB) under WSL2 (Linux 6.18, Ubuntu 24.04), with clang++ 21.1.8, g++ 13.3.0, cargo and rustc 1.95.0 and Python 3.12.3, shared with other agents' test suites.

All four solved it. `tables_pilot.md` has each subject's numbers and `results_pilot.json` the same data. Each subject's final program, its record and its transcript (compressed with xz, and named in the record by the sha256 of the uncompressed file) are under `subjects/pilot/`. `audit_decisions.json` records why each audit flag was cleared: a `find` over the pilot's root that never ran, because the command before its `||` succeeded, and reads of the machine's CPU limits.

After the pilot, `harness.py verify` showed every reference passing its hidden check and every starter failing it at ba9eb67, 91 of 91 as required. It runs again at the pinned commit before the first counted subject.

## What did not run

The counted run has not started. No device code ran on a GPU: the CAIRN subjects' `block_scan` programs, and the judge's builds of them, ran the device work on host threads under `--emulate`. The pilot's subjects shared the machine's `/tmp`, which the counted run replaces with a `/tmp` of each subject's own.
