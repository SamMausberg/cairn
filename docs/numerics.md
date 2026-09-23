# Numerics

Every rounding a program performs is named in its source and listed in its build receipt under `numerics`.

## Storage floats

`f16`, `bf16`, `f8e4m3` and `f8e5m2` hold a value in 16 or 8 bits and convert, but never compute: arithmetic, a comparison or a literal of one is refused (`E-OPERATOR`, `E-TYPE-MISMATCH`). A program widens, computes in `f32` or `f64`, and rounds back where it says so. `f16` is IEEE 754 binary16, `bf16` the upper half of an `f32`, and the 8-bit formats are OCP's E4M3 (up to 448, no infinity) and E5M2 (up to 57344).

`f32(h)` is exact. `f16(x)` rounds once, to nearest with ties to even: past the range it gives infinity, and in `f8e4m3`, which has none, it traps. An integer or another storage float converts through `f32` first (`E-CAST`).

`quantize[T](x, scale)` is `x / scale` rounded once to nearest with ties to even and clamped to T's finite range, so saturation takes the place of overflow. T is a storage float or one of `i8 u8 i16 u16`, and `x` and `scale` are `f32` (`E-QUANTIZE`). A scale that is not positive and finite traps. `quantize_stochastic[T](x, scale, noise)` rounds away from zero when the dropped fraction exceeds the caller's `u32` noise, so it is unbiased over uniform noise and reproducible for the same noise. `f32(q) * scale` dequantizes, and `to_bits(h)` and `from_bits[T](u)` move between a float and its bits.

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

```cairn rejects E-OPERATOR
fn mean(a:bf16, b:bf16) -> bf16 = (a + b) / bf16(2.0);
```

```text
bf16 is a storage float: widen it with f32(x) to compute, and round back with bf16(y) or quantize.
```

One integer routine rounds on the host and in a device lane alike. The suite holds it to an independent model over every pattern of all four formats and every tie, under both compilers and the sanitizers. The SMT model answers `unknown` for a function that uses a storage float.

## The tensor-core multiply

`mma_unordered(m, n, k, c, a, b)` adds the product of the row-major `m x k` matrix `a` and the `k x n` matrix `b` into the `m x n` matrix `c`. `a` and `b` are views of one storage float, and `c` is an `rw` view of `f32`. It is the one operation whose float additions do not follow the written order, and its name says so.

```cairn
fn layer(m:usize, n:usize, k:usize, cn:usize, c:rw<f32>[cn]@device, an:usize, a:ro<f16>[an]@device,
         bn:usize, b:ro<f16>[bn]@device) {
  mma_unordered(m, n, k, c, a, b);                   // on the tensor cores: its sums in the hardware's order
}
```

The contract, and nothing stronger: every product is exact in f32 (true for all `f16` and 8-bit products, and for `bf16` unless a product leaves f32's range), and every output is its old value plus its `k` products, each partial sum rounded to f32 in an order the hardware picks. Every finite output lies within `(k + 1) * 2^-22 * (|c[i][j]| + sum |a[i][p] * b[p][j]|)` of the exact sum. Two runs on one device give the same bits; the host and the device need not.

On the host the multiply is the written loop, in increasing `p`. On the device, where all three views must live (`E-PLACEMENT`), it runs 64 x 64 tiles on the tensor cores through two shared-memory stages. The extents are checked once at the call. `E-MMA` refuses a `c` that is not an `rw` view of `f32` or an `a` and `b` of different formats, and a multiply inside a lane is `E-PARALLEL-NEST`. `cairn verify` answers `unknown` for it, and `cairn predict` prices it at the published tensor peak, a roofline.

```cairn rejects E-MMA
fn widened(n:usize, c:rw<f32>[n], a:ro<f32>[n], b:ro<f32>[n]) { mma_unordered(1, 1, n, c, a, b); }
```

The host suite checks the reference bit for bit against Python and the bound against the exact rational sum, and runs the device tiling thread by thread on the host under the sanitizers. That the tensor cores meet the contract is checked only by `make gpu`, which has not run on this release.

## Tensor-core fragments

A fragment is one warp's share of a tensor-core instruction: an operand A, an operand B or an accumulator. Where `mma_unordered(m, n, k, c, a, b)` runs one fixed tiling, fragments let a program write its own: the tile, the warps, the stages and the layouts are the program's, and the runtime header does not change.

| Type | Family | `M, N, K` | Device capability |
|---|---|---|---|
| `WmmaA[T, M, N, K]`, `WmmaB`, `WmmaAcc` | `nvcuda::wmma` | 16, 16, 16; 32, 8, 16; 8, 32, 16 | `wmma`, and `bf16` for bf16 |
| `MmaA[T, M, N, K]`, `MmaB`, `MmaAcc` | PTX `mma.sync` | 16, 8, 16 | `mma_sync`, and `bf16` for bf16 |
| `TmemAcc[T, M, N, K]` | tcgen05 tensor memory | none lowered | `tcgen05` |

Operands hold `f16` or `bf16`, and accumulators `f32` (`E-FRAGMENT`). A is `M x K`, B is `K x N` and the accumulator `M x N`. `WmmaAcc[f32, 16, 16, 16](0.0)` fills an accumulator, `mma_load[F](tile, L, i, j)` reads fragment `(i, j)` of a tile laid out by `L`, counting whole fragments, and `mma_store(tile, L, i, j, acc)` writes one back. `acc = mma_unordered(acc, a, b)` adds `a * b` under the contract above: each output's `K` products and its old value, summed in f32 in an order the hardware picks.

Every fragment operation is a warp operation. It is legal only inside a cooperative region ([concurrency.md](concurrency.md)), where each warp reaches it whole (`E-FRAGMENT` outside one, `E-COOP-WARP` under a condition that differs within a warp). The lanes of a warp name one fragment together, so its coordinates, a fill's value, A and B, and a WMMA accumulator are the same in every thread of the warp: `t / 32` is, and `t % 2` is `E-COOP-WARP`. An `mma.sync` accumulator may differ from lane to lane, since each lane holds its own elements.

A fragment is stored into a shared array of the block and loaded from one or from a read-only device view, and a view it reads places the region as an index does. The array must hold every offset the layout places: a literal length is checked when the program is, and any other where the operation runs, trapping on the host and in a device lane alike. The phase rule counts a load as reads of its fragment's elements. An `mma.sync` store is each lane's writes of the elements the PTX ISA gives it; a WMMA store names no lane, so it is a write by the warp, and any access to what it wrote before a barrier, the storing thread's own included, is refused as it is around any other access.

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

Each family reads the layouts it can ([memory.md](memory.md#layouts)). WMMA takes a pointer and a leading dimension, so an operand's layout is row-major with its rows a multiple of 16 bytes apart, and a swizzled tile is `E-LAYOUT-CONSUMER`. `mma.sync` loads a shared tile with `ldmatrix`, one row address a lane, so a swizzle that keeps 16-byte runs together is readable and a pad that splits them is refused.

```cairn rejects E-LAYOUT-CONSUMER
layout SWIZZLED = swizzle(rows(16, 16), 1, 3, 3);
fn first(a:ro<f16>[256]@device) { let x = mma_load[WmmaA[f16, 16, 16, 16]](a, SWIZZLED, 0, 0); }
```

An accumulator's elements are reached one at a time where the family says which lane holds which. `mma_get(acc, v)` is the value `v` this thread's lane holds of an `mma.sync` accumulator, and `acc = mma_set(acc, v, x)` replaces it. Lane `l`'s value `v` is element `(l / 4 + 8 * (v / 2), 2 * (l % 4) + v % 2)`, the share the PTX ISA states, which a program names as the layout `spread(rows(16, 8), 8, 4, 1, 2)`: in a region of `threads t in 32`, `SHARE.col(t, v)` is the column of `mma_get(acc, v)`. `proofs/Cairn/Layout.lean` checks that this spread is the ISA's formula and gives each element one lane. WMMA leaves the share unspecified, so there an element is `E-FRAGMENT`.

The family is a capability the build's device target must provide ([tools.md](tools.md#the-device-target)). `TmemAcc` needs tcgen05 and tensor memory, which sm_120 does not have and which nothing here lowers, so it is refused rather than emulated.

```cairn rejects E-TARGET-FEATURE
fn tensor_memory() { let acc = TmemAcc[f32, 128, 256, 16](0.0); }
```

On the host every thread of a warp holds each fragment whole, adds in increasing k, and stores only the elements its lane holds on the device, so a warp's threads write each element once.

Two matrix multiplies with `mma_unordered`'s signature are written this way in `examples/tensor`: `tile64`, the tiling `mma_unordered` fixes (64 x 64 tiles, four warps of 2 x 2 WMMA fragments, k in steps of 32 through two padded stages), and `tile32`, another (64 x 32 tiles, eight warps of two `mma.sync` fragments, one stage whose A tile is swizzled). On generated shapes with partial tiles in every direction, each output of both lies within the contract's bound of the exact sum and equals the reference loop's bit for bit, under both compilers, and their threads run clean under the thread sanitizer. Both compile for sm_120, to `HMMA.16816.F32` and `HMMA.16816.F32.BF16` fed by `LDSM`, and have not run on a GPU ([evidence](../evidence/v0_9/tensor/README.md)).

## Gradients

`derive grad for f;` generates `f_grad`, the reverse-mode derivative of `f`, as an ordinary function that `cairn expand` prints and the checker checks. `derive grad[w, b] for f;` differentiates only the named parameters; without a list, every float parameter and `ro` float view is differentiated.

`f_grad` takes `f`'s parameters, then `seed` when `f` returns a float, then one adjoint per differentiated parameter (`d_x:rw<T>` or `d_x:rw<T>[n]`), and `d_out:ro<T>[n]` for each float view `f` writes. It runs `f`, adds `seed` times each partial derivative into the adjoints, and returns `f`'s result. Adding rather than assigning is what lets gradients compose.

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

The fragment that can be differentiated is: immutable `let`s, `if` whose paths return, sums, loops and regions that write each output element once, `+ - * /`, `sqrt`, `abs` (derivative 1 at zero), `floor ceil trunc` (derivative 0), `f32`/`f64` conversions, `std.math.exp` and `std.math.log`, and calls of functions with their own derived gradient. A sum is a `reduce +` or a float `let mut` one sequential loop adds into (`acc += e`). Anything else is `E-GRAD-FORM`, a call without a matching gradient is `E-GRAD-CALL`, and a bad target or list is `E-GRAD`.

Nothing is taped: each `return` carries its own backward sweep, which recomputes the `let`s on its path. A region's backward sweep is a region in which each lane adds into its own element of each adjoint. A lane that reads a differentiated input at another lane's element would scatter its adjoint across lanes, so it is `E-GRAD-RACE`.

```cairn rejects E-GRAD-RACE
fn smooth(n:usize, x:ro<f64>[n], out:rw<f64>[n]) {
  parallel i in n { out[i] = x[i] + x[(i + 1) % n]; }
}
derive grad for smooth;
```

```text
The adjoint of x[(i + 1) % n] adds into d_x at another lane's element; read x at [i] in the region, or differentiate a sequential loop.
```

The derivative is that of the formulas, not of their rounding. The suite holds gradients to central differences and, where torch is present, to its autograd, under both compilers and the sanitizers. That is finite testing, not a proof.
