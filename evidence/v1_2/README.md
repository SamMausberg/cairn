# 1.2 records

Each directory is one record of the work after `v1.1.0`, taken as it landed. Its README says what ran, on which machine, with which tools, and what did not run. They ran on the reference machine, an AMD Ryzen 7 7800X3D (16 threads) under WSL2, shared with other agents' builds and suites, unless the record names another.

| Record | What it holds |
|---|---|
| `device_facts/` | The layer norm, stencil and f32 sum pairs of `bench/device` before and after the guards #171 discharges and #173's block divider: their SASS for sm_90 and their GPU times on a GH200, interleaved; one GPU, not comparable to the RTX 5070 Ti records. |
| `discovery/` | What `cairn find` answers for each library fact the 1.1 subjects searched the documentation for, beside what they read for it, in tokens; no model ran. |
| `facts/` | The guards a usize product by a constant, a quotient's multiples and a sum's lower bound discharge: the Lean differential, the guard differential under both compilers and sanitizers, and which programs emit fewer guards; on the GH200 host, nothing on the GPU. |
| `gpu_gh200/` | The suite's device tests on an NVIDIA GH200, the first on a Hopper GPU since 0.8.2: the three defects a run at `dda8208` found, and a run at `449189b` where every test that ran passed, with Compute Sanitizer's four tools clean on the device validation tests; nothing timed. |
| `loop/` | What an agent reads between an edit and a run, in bytes, before and after each change that acts on that loop; no model ran. |
| `std/` | Reading all of standard input into a `Vec[u8]` with `std` before and after each change to it: wall time and peak resident memory on the host, clang++ builds only; no device code ran. |
| `terse/` | What each `cairn` command and MCP tool prints for an agent to read, in bytes and tokens, before and after the cuts that took 63% of it, and the budgets that hold it; no model ran. |
