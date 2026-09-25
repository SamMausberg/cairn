# Numerics

This page says where a CAIRN program rounds a float beyond plain `f32` and `f64` arithmetic, and what each rounding promises. It covers the storage floats and `quantize`, the tensor core multiply and its fragments, atomic float addition, `derive grad`, and where a device program run on the host can differ from a run on the device. After reading it you can store numbers in 16 or 8 bits, use the tensor cores, and say exactly how far a result may be from the exact one. Plain `f32` and `f64` arithmetic is in [language.md](language.md#values-and-arithmetic).

Every rounding a program performs is named in its source and listed under `numerics` in its build receipt, the record a build writes beside what it built.

## Storage floats

A storage float holds a number in 16 or 8 bits, so an array of them takes a half or a quarter of the memory an `f32` array takes. `f16`, `bf16`, `f8e4m3` and `f8e5m2` hold a value and convert, and they never compute. A program widens a storage float, computes in `f32` or `f64`, and rounds back where it says so.

```cairn
fn pack(n:usize, weights:ro<f32>[n], out:rw<f8e4m3>[n], scale:f32) {
  for i in 0..n { out[i] = quantize[f8e4m3](weights[i], scale); }     // one rounding, saturating at 448
}

fn dot(n:usize, w:ro<f8e4m3>[n], xs:ro<f32>[n], scale:f32) -> f32 {
  let mut sum:f32 = 0.0;
  for i in 0..n { sum = sum + f32(w[i]) * scale * xs[i]; }           // widen exactly, compute in f32
  return sum;
}

fn mean(a:bf16, b:bf16) -> bf16 = bf16((f32(a) + f32(b)) / 2.0);

fn main() -> i32 {
  let n:usize = 4;
  buffer weights:f32[n] = zeroed;
  buffer packed:f8e4m3[n] = zeroed;
  for i in 0..n { weights[i] = f32(i) * 0.5; }
  pack(n, weights, packed, 0.125);
  if dot(n, packed, weights, 0.125) != 3.5 { return 1; }
  if to_bits(f16(1.0 / 3.0)) != 0x3555 || f32(mean(bf16(1.0), bf16(0.5))) != 0.75 { return 2; }
  if quantize[i8](-2.5, 1.0) != -2 || quantize[i8](500.0, 2.0) != 127 { return 3; }   // ties to even; saturates
  return 0;
}
```

`f16` is IEEE 754 binary16, and `bf16` is the upper half of an `f32`. The 8-bit formats are OCP's E4M3, which reaches 448 and has no infinity, and E5M2, which reaches 57344. Arithmetic on a storage float, a comparison of two, and a literal of one are refused (`E-OPERATOR`, `E-TYPE-MISMATCH`).

```cairn rejects E-OPERATOR
fn mean(a:bf16, b:bf16) -> bf16 = (a + b) / bf16(2.0);
```

```text
bf16 is a storage float: widen it with f32(x) to compute, and round back with bf16(y) or quantize.
```

`f32(h)` is exact. `f16(x)` rounds once, to nearest with ties to even. Past the format's range it gives infinity, and in `f8e4m3`, which has no infinity, it traps. An integer or another storage float converts through `f32` first (`E-CAST`).

`quantize[T](x, scale)` is `x / scale`, rounded once to nearest with ties to even and clamped to `T`'s finite range, so it saturates where a conversion would overflow. `T` is a storage float or one of `i8`, `u8`, `i16` and `u16` (`E-QUANTIZE`), and `x` and `scale` are `f32` (`E-TYPE-MISMATCH`). A scale that is not positive and finite traps. `quantize_stochastic[T](x, scale, noise)` rounds away from zero when the dropped fraction exceeds the caller's `u32` noise, so it is unbiased over uniform noise and gives the same answer for the same noise. `f32(q) * scale` turns a quantized value back into a number, and `to_bits(h)` and `from_bits[T](u)` move between a float and its bits.

One integer routine rounds on the host and in a device lane alike. The suite holds it to an independent model over every bit pattern of all four formats and every tie, under both compilers and the sanitizers. The SMT model behind `cairn verify` answers `unknown` for a function that uses a storage float.

## The tensor-core multiply

`mma_unordered(m, n, k, c, a, b)` multiplies two matrices on the tensor cores. It adds the product of the row-major `m x k` matrix `a` and the row-major `k x n` matrix `b` into the row-major `m x n` matrix `c`. `a` and `b` are views of one storage float, and `c` is an `rw` view of `f32`. Its float additions do not follow the written order, and its name says so.

```cairn
fn layer(m:usize, n:usize, k:usize, cn:usize, c:rw<f32>[cn]@device, an:usize, a:ro<f16>[an]@device,
         bn:usize, b:ro<f16>[bn]@device) {
  mma_unordered(m, n, k, c, a, b);                   // on the tensor cores: its sums in the hardware's order
}
```

The multiply's contract, and nothing stronger, has three parts. Every product is exact in `f32`, which holds for every `f16` and 8-bit product, and for every `bf16` product that stays inside `f32`'s range. Every output is its old value plus its `k` products, each partial sum rounded to `f32`, in an order the hardware picks. Every finite output lies within `(k + 1) * 2^-22 * (|c[i][j]| + sum |a[i][p] * b[p][j]|)` of the exact sum.

Two runs on one device give the same bits. The host and the device need not.

On the host the multiply is the written loop, in increasing `p`. On the device, where all three views must live (`E-PLACEMENT`), it runs 64 x 64 tiles on the tensor cores through two stages in shared memory. The extents are checked once, at the call.

`E-MMA` refuses a `c` that is not an `rw` view of `f32`, and an `a` and `b` of different formats. A multiply inside a lane is `E-PARALLEL-NEST`.

```cairn rejects E-MMA
fn widened(n:usize, c:rw<f32>[n], a:ro<f32>[n], b:ro<f32>[n]) { mma_unordered(1, 1, n, c, a, b); }
```

`cairn verify` answers `unknown` for the multiply. `cairn predict` prices it at the published tensor peak, which is a roofline.

The host suite checks the reference loop bit for bit against Python, and the bound against the exact rational sum. It runs the device tiling thread by thread on the host under the sanitizers. Only `make gpu` checks that the tensor cores meet the contract, and it passed there on one RTX 5070 Ti in the 1.1.0 session ([evidence/v1_1/gpu](../evidence/v1_1/gpu/README.md)).

## Atomic float addition

`atomic_add_unordered(x[i], v)` adds an `f32` or `f64` into one element from any number of lanes or threads at once ([concurrency.md](concurrency.md#atomics-and-mutexes)). The adds happen one at a time, each rounded to nearest, in the order the threads arrive. The hardware and the host's scheduler pick that order, so two runs may differ in the last places. The name says so, as `mma_unordered`'s does.

The contract, and nothing stronger: after `k` adds the element is its old value plus every added value, each partial sum rounded once, in some order. On the device an `f32` add also flushes a subnormal operand or result to zero, as PTX's `atom.add.f32` does. An `f64` add does not. So an `f32` element ends within `k * 2^-23 * (|old| + sum |v|) + k * 2^-125` of the exact sum while `k` is below `2^22`, and an `f64` one within `k * 2^-52 * (|old| + sum |v|)` while `k` is below `2^51`.

The receipt lists the contract under `numerics`, and `cairn verify` answers `unknown` for a function that holds an atomic float add. A sum that must come out the same on every run folds in an order the program fixes instead: `reduce +` on the host, and `reduce + warp` within a warp.

## Gradients

`derive grad for f;` writes `f_grad`, the reverse-mode derivative of `f`, as an ordinary function that the checker checks and `cairn expand` prints. `derive grad[w, b] for f;` differentiates only the named parameters. Without a list, it differentiates every float parameter and every `ro` float view.

`f_grad` takes `f`'s parameters, then `seed` when `f` returns a float, then one adjoint per differentiated parameter (`d_x:rw<T>` or `d_x:rw<T>[n]`). An adjoint is where the gradient with respect to that parameter is added. For each float view `f` writes, `f_grad` also takes `d_out:ro<T>[n]`. It runs `f`, adds `seed` times each partial derivative into the adjoints, and returns `f`'s result. It adds into the adjoints, and never assigns them, so that gradients compose.

```cairn
fn square(x:f64) -> f64 = x * x;

fn loss(n:usize, w:ro<f64>[n], x:ro<f64>[n], bias:f64, target:f64) -> f64 {
  let guess = reduce + for i in n yield w[i] * x[i];
  return square(guess + bias - target);
}

derive grad for square;
derive grad[w, bias] for loss;                     // x and target are data: no adjoint for them

fn main() -> i32 {
  let n:usize = 2;
  buffer w:f64[n] = zeroed;
  buffer x:f64[n] = zeroed;
  buffer dw:f64[n] = zeroed;
  x[0] = 1.0;
  x[1] = 2.0;
  let mut bias:f64 = 0.0;
  for _ in 0..100 {
    for i in 0..n { dw[i] = 0.0; }
    let mut dbias:f64 = 0.0;
    loss_grad(n, w, x, bias, 5.0, 1.0, dw, dbias);           // the loss, dropped; seed times its gradient added
    for i in 0..n { w[i] -= 0.05 * dw[i]; }
    bias -= 0.05 * dbias;
  }
  if loss(n, w, x, bias, 5.0) > 0.000000001 { return 1; }
  return 0;
}
```

`derive grad` differentiates these forms: immutable `let`s, an `if` whose paths return, sums, loops and regions that write each output element once, `+ - * /`, `sqrt`, `abs` (derivative 1 at zero), `floor`, `ceil` and `trunc` (derivative 0), conversions between `f32` and `f64`, `std.math.exp` and `std.math.log`, and calls of functions with a derived gradient of their own. A sum is a `reduce +`, or a float `let mut` that one sequential loop adds into (`acc += e`). Anything else is `E-GRAD-FORM`. A call without a matching gradient is `E-GRAD-CALL`, and a bad target or list is `E-GRAD`.

Nothing is recorded on a tape. Each `return` carries its own backward sweep, which recomputes the `let`s on its path. A region's backward sweep is a region in which each lane adds into its own element of each adjoint. A lane that reads a differentiated input at another lane's element would add its adjoint into another lane's element, so that read is `E-GRAD-RACE`.

```cairn rejects E-GRAD-RACE
fn smooth(n:usize, x:ro<f64>[n], out:rw<f64>[n]) {
  parallel i in n { out[i] = x[i] + x[(i + 1) % n]; }
}
derive grad for smooth;
```

```text
The adjoint of x[(i + 1) % n] adds into d_x at another lane's element; read x at [i] in the region, or differentiate a sequential loop.
```

The derivative is the derivative of the formulas in exact arithmetic, and it ignores their rounding. The suite holds gradients to central differences and, where torch is present, to torch's autograd, under both compilers and the sanitizers. That is finite testing, and it proves nothing beyond the cases it ran.

## Tensor-core fragments

A fragment is one warp's share of a tensor core instruction: an operand A, an operand B or an accumulator. `mma_unordered(m, n, k, c, a, b)` runs one fixed tiling. With fragments a program writes its own: the tile, the warps, the stages and the layouts are the program's, and the runtime header does not change.

| Type | Family | `M, N, K` | Device capability |
|---|---|---|---|
| `WmmaA[T, M, N, K]`, `WmmaB`, `WmmaAcc` | `nvcuda::wmma` | 16, 16, 16; 32, 8, 16; 8, 32, 16 | `wmma`, and `bf16` for bf16 |
| `MmaA[T, M, N, K]`, `MmaB`, `MmaAcc` | PTX `mma.sync` | 16, 8, 16 | `mma_sync`, and `bf16` for bf16 |
| `TmemAcc[T, M, N, K]` | tcgen05 tensor memory | none lowered | `tcgen05` |

Operands hold `f16` or `bf16`, and accumulators hold `f32` (`E-FRAGMENT`). A is `M x K`, B is `K x N` and the accumulator is `M x N`. `WmmaAcc[f32, 16, 16, 16](0.0)` fills an accumulator. `mma_load[F](tile, L, i, j)` reads fragment `(i, j)` of a tile laid out by the layout `L`, counting in whole fragments, and `mma_store(tile, L, i, j, acc)` writes one back. `acc = mma_unordered(acc, a, b)` adds `a * b` under the contract of [the multiply](#the-tensor-core-multiply): each output's `K` products and its old value, summed in `f32` in an order the hardware picks.

```cairn
layout TILE = rows(16, 16);
const CELLS:usize = TILE.cosize();

// One warp: out = c + a * b for a 16 x 16 tile, on the tensor cores.
fn tile(out:rw<f32>[256]@device, c:ro<f32>[256]@device, a:ro<f16>[256]@device, b:ro<f16>[256]@device) {
  blocks g in 1 threads t in 32 {
    shared sc:f32[CELLS] = zeroed;
    let x = mma_load[WmmaA[f16, 16, 16, 16]](a, TILE, 0, 0);
    let y = mma_load[WmmaB[f16, 16, 16, 16]](b, TILE, 0, 0);
    let mut acc = mma_load[WmmaAcc[f32, 16, 16, 16]](c, TILE, 0, 0);
    acc = mma_unordered(acc, x, y);             // acc + x * y, its sums in the hardware's order
    mma_store(sc, TILE, 0, 0, acc);             // the warp's write: a barrier before anyone reads it
    barrier;
    for i in 0..8 { out[t + 32 * i] = sc[t + 32 * i]; }
  }
}
```

Every fragment operation is a warp operation. It is legal only inside a cooperative region ([devices.md](devices.md#cooperative-regions)), where each warp reaches it whole (`E-FRAGMENT` outside one, `E-COOP-WARP` under a condition that differs within a warp). The lanes of a warp name one fragment together, so a fragment's coordinates, a fill's value, A and B, and a WMMA accumulator are the same in every thread of the warp. `t / 32` is the same across a warp, and `t % 2` is `E-COOP-WARP`. An `mma.sync` accumulator may differ from lane to lane, since each lane holds its own elements.

A fragment is stored into a shared array of the block, and loaded from one or from an `ro` view of `@device` or `@unified` memory. A view a fragment reads places the region on the device or the host, as an index does. The array must hold every offset the layout places. A literal length is checked when the program is, and any other length where the operation runs, trapping on the host and in a device lane alike.

The phase rule ([devices.md](devices.md#the-phase-rule)) counts a load as reads of its fragment's elements. An `mma.sync` store is each lane's writes of the elements the PTX ISA gives that lane. A WMMA store names no lane, so it is a write by the whole warp: any access to what it wrote before a barrier, the storing thread's own included, is refused as it is around any other write.

Each family reads the layouts it can ([memory.md](memory.md#layouts)). WMMA takes a pointer and a leading dimension, so an operand's layout is row-major, with its rows a multiple of 16 bytes apart, and a swizzled tile is `E-LAYOUT-CONSUMER`. `mma.sync` loads a shared tile with `ldmatrix`, one row address per lane, so it can read a swizzle that keeps 16-byte runs together, and it refuses a pad that splits them.

```cairn rejects E-LAYOUT-CONSUMER
layout SWIZZLED = swizzle(rows(16, 16), 1, 3, 3);
fn first(a:ro<f16>[256]@device) { let x = mma_load[WmmaA[f16, 16, 16, 16]](a, SWIZZLED, 0, 0); }
```

An accumulator's elements are reached one at a time where the family says which lane holds which. `mma_get(acc, v)` is the value `v` that this thread's lane holds of an `mma.sync` accumulator, and `acc = mma_set(acc, v, x)` replaces it. Lane `l`'s value `v` is element `(l / 4 + 8 * (v / 2), 2 * (l % 4) + v % 2)`, the share the PTX ISA states. A program names that share as the layout `spread(rows(16, 8), 8, 4, 1, 2)`: in a region of `threads t in 32`, `SHARE.col(t, v)` is the column of `mma_get(acc, v)`. `proofs/Cairn/Layout.lean` checks that this spread is the ISA's formula and gives each element one lane. WMMA leaves the share unspecified, so there reaching an element is `E-FRAGMENT`.

The family is a capability the build's device target must provide ([tools.md](tools.md#the-device-target)). `TmemAcc` needs tcgen05 and tensor memory, which sm_120 does not have. Nothing here lowers `TmemAcc`, so it is refused, and it is never emulated.

```cairn rejects E-TARGET-FEATURE
fn tensor_memory() { let acc = TmemAcc[f32, 128, 256, 16](0.0); }
```

On the host every thread of a warp holds each fragment whole and adds in increasing `k`. It stores only the elements its lane holds on the device, so a warp's threads write each element once.

`examples/tensor` writes two matrix multiplies with `mma_unordered`'s signature this way. `tile64` is the tiling `mma_unordered` fixes: 64 x 64 tiles, four warps of 2 x 2 WMMA fragments, and `k` in steps of 32 through two padded stages. `tile32` is another: 64 x 32 tiles, eight warps of two `mma.sync` fragments, and one stage whose A tile is swizzled. On generated shapes with partial tiles in every direction, each output of both lies within the contract's bound of the exact sum, and equals the reference loop's bit for bit under both compilers. Their threads run clean under the thread sanitizer. Both compile for sm_120, to `HMMA.16816.F32` and `HMMA.16816.F32.BF16` fed by `LDSM`, and on an RTX 5070 Ti each kept the contract ([evidence](../evidence/v1_0/tensor/README.md), [device run](../evidence/v1_1/gpu/README.md)).

## Emulated device runs

A device program built with `--emulate` ([devices.md](devices.md#emulating-device-code-on-the-host)) runs its device work on host threads. It gives the host's bits wherever the language fixes them, and a device agrees wherever the language says it does. Where the language leaves an order to the hardware, the emulation takes one legal order and a device may take another.

| What a program computes | An emulated run against a device run |
|---|---|
| integer arithmetic, conversions and every guard | the same: each is the same C++ on both sides, and a failed guard aborts either way |
| `+ - * /` and `sqrt` on `f32` and `f64` | the same bits: the host builds with `-ffp-contract=off -fno-fast-math` and nvcc with `--fmad=false`, so nothing becomes a fused multiply-add, and CUDA's default division and square root are correctly rounded and keep subnormals, as the host's are and do |
| `floor`, `ceil`, `trunc`, `abs`, storage float conversions, `quantize` | the same: each is exact, or one integer routine on both sides |
| `exp`, `log`, `sin` and the other libm functions | never in device code: a lane cannot call `std.math` (`E-PARALLEL-CALL`), so neither libm nor CUDA's libdevice runs there |
| a device `reduce` or `scan` over integers | the same: every operator allowed is associative, and a checked `+` traps exactly when the total overflows |
| a device `reduce` over floats | may differ in the last places: the device combines in a tree whose order is unspecified, and the emulation in index order, as a host `reduce + for` does; a float `scan` over device views is refused (`E-SCAN-ORDER`) |
| `mma_unordered`, on whole matrices or on fragments | may differ within the contract's bound: the emulation adds in increasing `k`, which is the reference loop's order, and the tensor cores add in an order the hardware picks |
| `reduce OP warp` and the shuffles | the same: one fixed butterfly on both sides |
| `load_wide` and `store_wide` | the same: `K` plain accesses on the host move the bytes one access moves on the device, and a hint changes no value |
| integer atomic updates | the same final values of `atomic_add_wrap`, `min`, `max`, `and`, `or` and `xor`, whose operators commute; the old values threads get back, and what `atomic_cas` leaves, follow the order the threads arrive in, on either side |
| `atomic_add_unordered` | may differ in the last places, within its bound: the host's scheduler and the hardware pick the order, and a device `f32` add flushes subnormals |
| code that relies on a warp running in step, and memory ordering | nothing to observe: a block's threads share memory only across the barriers the phase rule demands, a warp operation needs its whole warp, and a lane touches only its own elements of what any lane writes |

An emulated run of `examples/tensor` therefore equals the reference loop bit for bit, and a device run is held only to the contract's bound. Only `make gpu` checks that the tensor cores meet that bound, and it did so on one RTX 5070 Ti in the 1.1.0 session ([evidence/v1_1/gpu](../evidence/v1_1/gpu/README.md)).
