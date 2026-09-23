## What changed from `v1.3.0` to `13692b3e0333925b8f9fb2f2af2800a337360b05`

Compared by `cairn diff` (cairn-native/1.3.0): 78 identical-code, 5 identical-source, 12 unknown. A function whose code is identical, with everything it calls, is counted and not listed.

| function | class | evidence | compiler-established changes |
|---|---|---|---|
| `analytics.agg.bins_total` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `analytics.agg.bins_total[u64]` | unknown | An owner inside a record, a sum or an array is not modeled. |  |
| `analytics.agg.run_dyn` | unknown | rw<dyn analytics.agg.Aggregator> is outside the modeled value fragment. |  |
| `analytics.agg.run_plan` | unknown | An owner inside a record, a sum or an array is not modeled. Its own code is identical; something it calls changed. |  |
| `analytics.agg.run_static` | unknown | A generic function no code instantiates, so there is no code to compare, and its tokens changed. |  |
| `analytics.agg.run_static[analytics.agg.SumAgg]` | unknown | A loop may run past the 16-iteration unrolling budget; deciding it needs a precondition that holds every trip count, and so every symbolic extent it reads, at 16 or below. |  |
| `analytics.main.ingest` | unknown | An owner inside a record, a sum or an array is not modeled. | guards written 4 -> 7; guards discharged 0 -> 3 |
| `analytics.main.main` | unknown | Statement 'unsafe' is not modeled. Its own code is identical; something it calls changed. | guards written 64 -> 71; guards discharged 22 -> 29 |
| `analytics.report.line` | unknown | Statement 'unsafe' is not modeled. | effects -diverge -local_read -local_write -stack_storage -zero_init |
| `analytics.report.micros` | unknown | Statement 'unsafe' is not modeled. | effects -diverge -local_read -local_write -stack_storage -zero_init |
| `analytics.report.say` | unknown | Statement 'unsafe' is not modeled. | effects -diverge |
| `analytics.table.write_dataset` | unknown | Statement 'unsafe' is not modeled. | guards written 14 -> 15; guards discharged 1 -> 2 |

Predicted, not measured (AMD Ryzen 7 7800X3D 8-Core Processor, 16 lanes): analytics.agg.run_dyn x1.0, analytics.agg.run_static[analytics.agg.SumAgg] x1.0, analytics.main.ingest x1.0, analytics.main.main x1.0, analytics.table.write_dataset x1.0.

Semantic version: **unknown (at least patch)**.

It is unknown, because these public functions are unproven: `analytics.agg.bins_total`, `analytics.agg.bins_total[u64]`, `analytics.agg.run_dyn`, `analytics.agg.run_plan`, `analytics.agg.run_static`, `analytics.agg.run_static[analytics.agg.SumAgg]`, `analytics.main.main`, `analytics.report.line`, `analytics.report.micros`, `analytics.report.say`, `analytics.table.write_dataset`.
