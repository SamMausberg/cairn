# Numerics

This reference covers the numbers CAIRN stores in fewer bits than it computes in, and the derivatives it writes for you: storage floats and their conversions, `quantize` and its stochastic form, and `derive grad`. [language.md](language.md#values-and-arithmetic) has the arithmetic of the scalars they convert to, and [abstractions.md](abstractions.md#recipes) has the `derive` form `grad` shares with the other recipes. Every rounding a program performs is named in its source and listed in its build receipt under `numerics`.

## Storage floats

`f16`, `bf16`, `f8e4m3` and `f8e5m2` hold a value in 16 or 8 bits and convert. They never compute: arithmetic, a comparison or a literal of one is refused (`E-OPERATOR`, `E-TYPE-MISMATCH`), so a program widens, computes in `f32` or `f64`, and rounds back where it says so. `f16` is IEEE 754 binary16, `bf16` is the upper half of an `f32`, and the two 8-bit formats are OCP's E4M3, which reaches 448 and has no infinity, and E5M2, which reaches 57344.

`f32(h)` and `f64(h)` are exact, since every value of the four formats is an `f32`. `f16(x)` rounds an `f32` or `f64` once, to nearest with ties to even, as IEEE 754 converts: a value past the range becomes infinity and a NaN stays a NaN. `f8e4m3` has no infinity to give, so there its conversion traps. An integer or another storage float converts through `f32` first (`E-CAST`), which is exact and keeps the rounding single.

`quantize[T](x, scale)` is `x / scale` rounded once to nearest with ties to even and clamped to T's finite range, so the quotient is never rounded twice and saturation takes the place of overflow. T is a storage float or one of `i8 u8 i16 u16`, the widths where one rounding of the quotient of two `f32` values is exact, and `x` and `scale` are `f32` (`E-QUANTIZE`). A scale that is not positive and finite traps, and so does a NaN quantized to an integer, which has none. `quantize_stochastic[T](x, scale, noise)` is the one other rounding: it moves away from zero exactly when the fraction the double quotient drops, to 32 bits, exceeds the `u32` noise the caller draws, so over uniform noise it is unbiased, and the same noise gives the same bits everywhere. The receipt says `stochastic-u32`. Dequantizing needs no builtin: `f32(q) * scale` widens exactly and rounds once. `to_bits(h)` and `from_bits[T](u)` move between a float and its pattern, for `f32` and `f64` as well.

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

The build receipt lists every rounding the source writes under the function's `numerics`: the operation, its formats, the rounding, what happens past the range and what happens to a NaN. One routine rounds on the host and in a device lane alike, working on the bit pattern with integers, and the suite holds it to an independent model over every pattern of all four formats, every tie between two neighbours and random doubles from the subnormals up, under both compilers and the sanitizers. The SMT model does not describe storage floats, so it answers `unknown` for a function that uses one.

## The tensor-core multiply

`mma_unordered(m, n, k, c, a, b)` adds the product of the row-major `m x k` matrix `a` and the row-major `k x n` matrix `b` into the row-major `m x n` matrix `c`. `a` and `b` are views of one storage float and `c` is an `rw` view of `f32`, the accumulator. It is the one operation in CAIRN whose float additions do not follow the written order, and its name says so, as `add_wrap` says it wraps. The receipt lists it under the function's `numerics` with `rounding: unordered-f32` and its bound.

```cairn
fn layer(m:usize, n:usize, k:usize, cn:usize, c:rw<f32>[cn]@device, an:usize, a:ro<f16>[an]@device,
         bn:usize, b:ro<f16>[bn]@device) {
  mma_unordered(m, n, k, c, a, b);                   // on the tensor cores: its sums in the hardware's order
}
```

The contract is this, and nothing stronger. Every product `a[i][p] * b[p][j]` is exact in f32, which holds for every product of two `f16`, `f8e4m3` or `f8e5m2` values, and for two `bf16` values unless the product leaves f32's range. Every output is its old value plus its `k` products, each partial sum rounded to f32, in an order and grouping the hardware picks. Every finite output lies within `(k + 1) * 2^-22 * (|c[i][j]| + sum |a[i][p] * b[p][j]|)` of the exact sum, and an output any of whose products or partial sums is not finite is not finite either, with no promise which. Two runs on one device give the same bits; the host and the device need not.

On the host the multiply is its reference loop: every output's products in increasing `p` after its old value, so `c` is exactly what the written loop would compute. On the device, where all three views must live (`E-PLACEMENT` otherwise), it runs 64 x 64 output tiles on four warps each, over `k` in steps of 32 staged through shared memory in two buffers, each warp multiplying 16 x 16 x 16 fragments on the tensor cores. An 8-bit float is widened to `f16` on its way into shared memory, which is exact. Where the format is 2 bytes, `k` and `n` are multiples of 8 and `a` and `b` sit on 16 bytes, the stages cross in asynchronous 16-byte copies while the warps multiply the previous one. The extents are checked once at the call: `len(c) == m * n`, `len(a) == m * k` and `len(b) == k * n`, or the call traps. The row says `read:a`, `read:b`, `write:c`, `trap`, and `par:device` on the device.

`E-MMA` refuses a `c` that is not an `rw` view of `f32` and an `a` and `b` that are not views of one storage float; a multiply inside a lane is `E-PARALLEL-NEST`. `cairn verify` answers `unknown` for a function that multiplies, and `cairn predict` prices a device multiply at the published tensor peak, which is its roofline and says so: the kernel's own efficiency is measured only by the owner's device calibration.

```cairn rejects E-MMA
fn widened(n:usize, c:rw<f32>[n], a:ro<f32>[n], b:ro<f32>[n]) { mma_unordered(1, 1, n, c, a, b); }
```

The host suite replays the reference in Python and requires it bit for bit, and the contract's bound against the exact rational sum, over every format and shapes with tails in every direction. It also runs the device tile's phases thread by thread on the host, with a model of the tensor-core operations that adds in increasing `k`, and requires every output to equal the reference, so the tiling, the tails, the zero fill and the two stages are checked here, under the sanitizers. That the tensor cores meet the contract is checked only by `make gpu`, which compares them with the reference within its bound.

## Gradients

`derive grad for f;` generates `f_grad`, the reverse-mode derivative of `f`, as an ordinary function: `cairn expand` prints it and the checker checks it like any other. `derive grad[w, b] for f;` differentiates with respect to the named parameters only. Without a list, every float parameter and every `ro` float view is differentiated.

`f_grad` takes `f`'s parameters, then `seed` when `f` returns a float, then one adjoint for each parameter it differentiates: `d_x:rw<T>` or `d_x:rw<T>[n]`, into which it adds, and `d_out:ro<T>[n]` for each float view `f` writes, which it reads. It runs `f`, adds `seed` times each partial derivative into the adjoints, and returns `f`'s result. Adding rather than assigning is what lets gradients compose: a gradient that calls `g` hands `g_grad` its own adjoints.

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

The differentiated fragment is one whose adjoint lies in the same fragment: immutable `let`s, `if` whose paths return, sums, loops and regions that write each output element once at their binder, `+ - * /`, `sqrt`, `abs` (whose derivative at zero is taken as 1), `floor ceil trunc` (whose derivative is 0), conversions between `f32` and `f64`, `std.math.exp` and `std.math.log`, and calls of functions that derive their own gradient. A sum is a `reduce +`, or a float `let mut` that one sequential loop adds into (`acc += e`, with `e` not reading `acc`) and that nothing reads before that loop ends: each step's `e` then takes the sum's own adjoint. A function that reaches libm cannot run inside a `reduce`, so its sums are loops. Any other `let mut`, `while`, a `reduce` other than `+`, and an output read or written twice are `E-GRAD-FORM`. A call of a function with no derived gradient, or one whose gradient leaves out a parameter the caller differentiates, is `E-GRAD-CALL`. A target that is not a plain function of this module, or a list naming something other than its float parameters, is `E-GRAD`.

Nothing is taped. Each `return`, and the end of a function that writes views, carries its own backward sweep, which recomputes the `let`s on its path. A region's backward sweep is a region of its own, in which each lane adds into its own element of each adjoint. Whatever the lanes would add into a shared scalar is gathered by a sequential loop, so no lane writes a value another lane writes. A lane that reads a differentiated input at another lane's element would scatter its adjoint across lanes, so that is `E-GRAD-RACE`: read it at `[i]`, differentiate a sequential loop, or leave the input out of the list.

```cairn rejects E-GRAD-RACE
fn smooth(n:usize, x:ro<f64>[n], out:rw<f64>[n]) {
  parallel i in n { out[i] = x[i] + x[(i + 1) % n]; }
}
derive grad for smooth;
```

```text
The adjoint of x[(i + 1) % n] adds into d_x at another lane's element; read x at [i] in the region, or differentiate a sequential loop.
```

The derivative is the derivative of the formulas, not of their rounding: the float operations round as written, and the adjoint's own operations round too. The suite holds gradients to central differences of the compiled function and, where the system Python has torch, to torch's autograd over the same formulas, under both compilers, the sanitizers and ThreadSanitizer for a region. That is finite testing, not a proof, and the SMT model does not describe generated gradients any more than it describes the regions they contain.
