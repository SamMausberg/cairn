# The demos, as they ran

Records of the three programs under [demos/](../../../demos/README.md), taken on 2026-09-23 at the commit that added them, on one AMD Ryzen 7 7800X3D (8 cores, 16 threads) under WSL2 with clang 21.1.8.

| File | What it holds |
|---|---|
| `numeric_host.json` | `demos/numeric/run.py --rounds 7`: the plate's 200 sweeps of 1024 x 1024 cells on 16 lanes and on one, beside the C++ loop on 16 OpenMP threads with and without guards and on one thread with them, every run's time and the load average before and after. |

The numeric run found every program printing the same fingerprint, so all five computed the same bits, and the CAIRN plate within 0.0000148 of the f64 reference against a contract of 0.0048. Those two results are exact and do not depend on load.

The times do. Five other agents were building and testing on the machine, and the load average ran from 10.5 to 16.2 on 16 threads. The medians were 195 ms on 16 lanes, 396 ms on one lane and 396 ms for the guarded C++ loop on one thread. The OpenMP builds took 978 ms guarded and 1342 ms unguarded, with runs from 345 ms to 2376 ms: an OpenMP static schedule waits at every sweep for its slowest thread, and libomp's threads spin while they wait, which an oversubscribed machine punishes. This record shows that the lane pool kept working under that load. It is not a comparison of CAIRN with OpenMP, and no ratio from it should be quoted; `make demo-numeric` on a quiet machine gives one.

The device half of the numeric demo has not run: it runs only under `make gpu`, which writes `results/demos/numeric/device.json`. The repair demo's one live run, claude-sonnet-5 through `claude -p`, is `demos/repair/live-sonnet-5.json` beside the demo, with its date and cost. The repair and visual demos otherwise time nothing.
