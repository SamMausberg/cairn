# Numerics

Numbers stored in fewer bits than they are computed in, the one multiply whose sums follow the hardware's order, and derivatives the compiler writes for you. Every rounding a program performs is named in its source and listed in its build receipt under `numerics`.

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
