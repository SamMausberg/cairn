# Implement: an agent writes a faster sum of squares, and cairn tune chooses one

`src/sumsq.cairn` holds `sumsq`, the sum of squares of an f64 vector, the reduction a vector norm or an RMS normalization starts with. It adds one term at a time, so each addition depends on the one before it. An agent writes faster implementations of it through the implementation tools of `cairn mcp`, the host validates each one against the reference before it keeps it, and `cairn tune` times on this host only the ones that validated.

```sh
make demo-implement                                 # or: python3 demos/implement/run.py
```

## What happens

`run.py` copies the project to `results/demos/implement/sumsq` and starts `cairn mcp` beside it. `implementation_open` pins the reference and the policy in `policy.json`, each by digest: a relative tolerance of 2^-40 on the result, 128 generated cases from seed 0 and extents up to 4096. It answers with a packet of 7,353 bytes: the reference's declaration and row, six rule cards and the form of a reply.

The agent's first submission, `sumsq_by4`, keeps four running sums `when n >= 4`. Four sums round differently from one, so it also asks for a relative tolerance of 1e-6. The host refuses it before compiling anything:

```text
[sumsq] host: refused E-TOLERANCE: tolerance is the host's: a submission carries protocol, handle, kind and source, and nothing it carries changes the reference, the tolerance, the test policy or the permitted inputs.
```

Sent again without the tolerance, it compiles and the validator runs it against the reference, each call in a process of its own. The 11th case fails, and the validator shrinks it:

```text
[sumsq] host: refused E-VALIDATION: sumsq_by4 is not validated: failed against sumsq.
    case 11 failed (n = 5: a tile plus one, 4 from the condition's >= 4; ones elements), and 18 runs shrank it to
    n = 5, xs = [0.0, 0.0, 0.0, 0.0, 1.0]: sumsq returns 1.0, sumsq_by4 returns 0.0
    kept in regressions/sumsq.json
```

The loop covers the first four elements and never reads `xs[4]`. The corrected submission says `when n % 4 == 0`, so the reference runs on every other length. It validates on 129 cases, the kept one first, 49 of which met the condition and ran `sumsq_by4`, and the host writes it beside the reference in `src/sumsq.cairn`. The last submission is `sumsq_blocks[K]`: blocks of `K` terms, each summed from zero and added to the total, with `tune K in [4, 8, 16, 32]`. The host validates each of the four instances on its own and writes the implementation with its helper.

Given no history, `cairn tune` has no validation to cite, so it marks every implementation `not validated`, times only the reference and keeps it. With the history the session wrote, every row says `finite-tested`, and the search times all six on this host within its budgets and writes the fastest:

```text
$ cairn tune results/demos/implement/sumsq --symbol sumsq --at n=65536 --measure 6 --budget-seconds 120 --budget-runs 16 --write
  sumsq: 6 plans, 6 accepted, 0 refused or unchecked; now (no plan for sumsq)
     1  (no plan for sumsq)                               37 us predicted
     2  plan sumsq use sumsq_blocks[32];                38.2 us predicted  finite-tested
     3  plan sumsq use sumsq_blocks[16];                39.4 us predicted  finite-tested
     4  plan sumsq use sumsq_blocks[8];                 41.7 us predicted  finite-tested
     5  plan sumsq use sumsq_blocks[4];                 46.3 us predicted  finite-tested
     6  plan sumsq use sumsq_by4;                        106 us predicted  finite-tested
  chosen: plan sumsq use sumsq_blocks[4];
  measured, median of 3 blocks: (no plan for sumsq) 44.1 us, plan sumsq use sumsq_blocks[32]; 25.9 us, plan sumsq use sumsq_blocks[16]; 17.2 us, plan sumsq use sumsq_blocks[8]; 11.5 us, plan sumsq use sumsq_blocks[4]; 13.3 us, plan sumsq use sumsq_by4; 12.2 us
  measured, median of 6 blocks: plan sumsq use sumsq_blocks[8]; 12.2 us, plan sumsq use sumsq_by4; 23.4 us, plan sumsq use sumsq_blocks[4]; 13 us
  measured, median of 12 blocks: plan sumsq use sumsq_blocks[8]; 13.2 us, plan sumsq use sumsq_blocks[4]; 12.8 us
  budget: 0 of 4 compiles, 0 kept; 13.8 s of 120.0; 11 runs, 0 kept
  11 runs started of the 16 allowed; timed on this host (AMD Ryzen 7 7800X3D 8-Core Processor, 16 threads),
  which other work shared: load average 7.0 before, 6.9 after
  --write put plan sumsq use sumsq_blocks[4]; into src/sumsq.cairn
```

The model ranks the reference first and `sumsq_by4` last. Measured, the reference is the slowest, at nearly four times the fastest's time, and the search's answer counts 3 of 15 measured pairs in the predicted order. `cairn tune --compare` reports the difference between the reference and the chosen instance, each line labelled by the kind of evidence it is:

```text
$ cairn tune results/demos/implement/sumsq --symbol sumsq --at n=65536 --compare none --compare "use sumsq_blocks[4]"
  sumsq: (no plan for sumsq)  ->  plan sumsq use sumsq_blocks[4];
    [compiler observation] cairn predict (the model, not a run): at n=65536: predicted 3.705e+04 ns -> 4.631e+04 ns; the model's bound compute -> compute; confidence high
    [runtime measurement] cairn.perf.measure: the candidate built with the project's flags beside a driver that fills each view, timed in blocks of at least 2 ms, the median of 3 blocks, on this host: a at n=65536: median 4.413e+04 ns (min 4.374e+04, max 4.53e+04)
    [runtime measurement] cairn.perf.measure: the candidate built with the project's flags beside a driver that fills each view, timed in blocks of at least 2 ms, the median of 3 blocks, on this host: b at n=65536: median 1.333e+04 ns (min 1.218e+04, max 1.385e+04)
    [runtime measurement] cairn.perf.measure: the candidate built with the project's flags beside a driver that fills each view, timed in blocks of at least 2 ms, the median of 6 blocks, on this host: b at n=65536: median 1.3e+04 ns (min 1.17e+04, max 1.426e+04)
    [runtime measurement] cairn.perf.measure: the candidate built with the project's flags beside a driver that fills each view, timed in blocks of at least 2 ms, the median of 12 blocks, on this host: b at n=65536: median 1.277e+04 ns (min 1.081e+04, max 1.658e+04)
    [hypothesis] the model against the measurement: the model predicted x1.25 and the runs measured x0.289: the model leaves out what separates them
    [suggested experiment] suggested, not run: time a and b at the same sizes, interleaved: cairn tune --symbol sumsq --measure 2 --at ... with both plans on this host
```

The measurements come from the history the search wrote, so the report starts no run. Last, `cairn run` builds the copy with the selection in place, and its `main` checks the sums of squares of 1..n through the dispatch at every length from 0 to 63.

## The tolerance

Every implementation computes the same products `xs[i] * xs[i]` and adds them in another order. Added in any order, m nonnegative terms come within gamma(m - 1) = (m - 1)u / (1 - (m - 1)u) of their exact sum, relative, where u = 2^-53. Two orders are then within 2 gamma(m - 1) / (1 - gamma(m - 1)) of each other, relative to either result, which for m up to 4096 is below 8192u = 2^-40. The host pins that bound: a correct reordering validates under it, and no submission can widen it. The bound holds while no partial sum overflows; a term that overflows is infinite in every order.

A zero tolerance refuses the correct `sumsq_by4`: `cairn validate` with an exact policy finds that at n = 4096, with random elements, its result and the reference's differ by 3 units in the last place.

## What is verified and what is not

The agent is scripted: its four submissions are the files in `candidates/` and the tolerance in `run.py`, written by hand to show one refusal of each kind, and no model wrote them. Every refusal, validation, timing and search is computed on every run, and `tests/projects/test_demos.py` checks each outcome above except the times and which instance the search chooses, which depend on the machine.

A validation is finite testing on the cases that ran, never proof: the reference and each implementation are compiled by the same compiler, so a fault they share would agree with itself. Z3 is asked apart from the tests, and its query covers only n <= 16, its unrolling bound. In the recorded run it answered `unknown` for all five, within its time limit; an earlier run answered `smt-equivalent` for `sumsq_blocks[32]`, where that bound leaves only n = 0.

The times are host timing only, one run on a shared machine; [evidence/v1_0/demos](../../evidence/v1_0/demos/README.md) records the machine, its load and the run. They show every implementation faster than the reference at n = 65536 on that host. Which `K` wins is within the noise, and a run on a quiet machine may choose another. Nothing ran on a GPU.
