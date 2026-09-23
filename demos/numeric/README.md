# Numeric: one sweep on host lanes and on CUDA lanes

A square plate of 1024 x 1024 cells has its top edge held at 100 degrees and its other edges at 0. Jacobi relaxation finds its temperature: each sweep sets every interior cell to the mean of its four neighbours. The sweep is written once, as a `parallel` region. With host views it runs on the lane pool; with `@device` views the same body runs as CUDA lanes.

```sh
make demo-numeric                     # host: run, check against f64, time beside C++ (demos/numeric/run.py)
cairn run demos/numeric               # the CAIRN program alone
cairn run demos/numeric/gpu.toml      # the device configuration: only under `make gpu`
```

## The code

`src/plate.cairn` holds the plate. The cell's arithmetic is one pure function, which host lanes and device lanes may both call:

```cairn fragment
fn mean4(n:f32, s:f32, w:f32, e:f32) -> f32 = 0.25 * ((n + s) + (w + e));

fn sweep(out:rw<f32>[CELLS], t:ro<f32>[CELLS]) {
  parallel i in CELLS {
    if edge(i) {
      out[i] = t[i];
    } else {
      out[i] = mean4(t[i - SIDE], t[i + SIDE], t[i - 1], t[i + 1]);
    }
  }
}
```

A lane writes only `out[i]` and reads `t`, which no lane writes, so the checker accepts the region and the result cannot depend on how many lanes run it. `src/device_main.cairn` has `sweep_device`, the same body with both views placed `@device`; `tests/projects/test_demos.py` checks that every line of its body is a line of `plate.cairn`. The lowering fixes the order of the arithmetic on both sides (`-ffp-contract=off` on the host, `--fmad=false` on the device), so the device is meant to give the host's bits exactly.

## The numerical contract

After `STEPS` sweeps every cell is within `STEPS * 4 * 2^-24 * 100` of an f64 reference, which for 200 sweeps is 0.0048. A sweep takes a mean of values that are each within the previous error, which keeps that error, and its three f32 additions round a value of at most 100; the fourth unit covers the reference's own rounding. The reference in `plate.cairn` is a plain loop over rows and columns on one thread, written differently from the code it checks.

## What a run prints

```text
plate: 1024 x 1024 cells, 200 sweeps on the host lanes in 163959 us
plate: middle column 1, 4 and 16 rows below the hot edge: 92.045975, 68.95758, 10.992733
plate: fingerprint of every cell's bits 13714961143245356297
plate: largest distance from the f64 reference 0.000014842041622387114, contract 0.00476837158203125

Every program printed the same fingerprint: yes.
```

The largest error is 0.0000148, about 300 times inside the contract. `run.py` then builds `baseline/plate.cpp`, the same loop as a C++ programmer writes it, with the same compiler and the flags CAIRN builds with. With `-DGUARDS` it makes the same checks per cell that the CAIRN build keeps (`cairn explain demos/numeric --symbol sweep` lists them), and with `-fopenmp` it is an OpenMP parallel for over as many threads as the lane pool. It runs every program in interleaved rounds and prints the median sweep time of each. Every C++ build prints the fingerprint above: all of them computed the same bits.

The timings in [evidence/v0_9/demos](../../evidence/v0_9/demos/README.md) were taken while five other agents loaded the machine, and they are not a performance comparison: under that load OpenMP's static schedule waits on its slowest thread. Run `make demo-numeric` on a quiet machine before you quote a ratio.

## The device

The device configuration compiles for sm_120 with `nvcc -Werror`, which `tests/projects/test_demos.py` checks. It runs only under the owner's `make gpu`, which checks that the device plate has the host's fingerprint and keeps the contract, and writes its output and times to `results/demos/numeric/device.json`. That has not run, so no device number for this plate exists.

The closest measured evidence is a different kernel, `evidence/v1_3/gpu/benchmark.json` from one RTX 5070 Ti: an elementwise f32 `saxpy` whose host and device results agreed bit for bit. Its kernel was 25 times as fast as the sequential host at 10^8 elements, and the whole run, transfers included, 0.47 times as fast.

## What is verified and what is not

The host program is native-built and finite-tested at one size: the contract holds, and one lane and four lanes print the same fingerprint. The C++ loop computes the same bits. The device program is accepted and compiles; its bits and its speed are unmeasured until `make gpu` runs. The error bound is argued above, not proved.
