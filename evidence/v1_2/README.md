# 1.2 records

Each directory is one record of the work after `v1.1.0`, taken as it landed. Its README says what ran, on which machine, with which tools, and what did not run. They ran on the reference machine, an AMD Ryzen 7 7800X3D (16 threads) under WSL2, shared with other agents' builds and suites.

| Record | What it holds |
|---|---|
| `occupancy/` | The blocks an SM holds as every packaged card counts them, against CUDA's own occupancy calculator (`cuda_occupancy.h`) over a grid of block sizes, registers and shared bytes, and the predictions the resident block limit changes; nothing ran on a GPU. |
| `terse/` | What each `cairn` command and MCP tool prints for an agent to read, in bytes and tokens, and the budgets that hold it; no model ran. |
