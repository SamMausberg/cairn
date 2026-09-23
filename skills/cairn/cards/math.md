# The math card

Sent to an agent when the program uses `abs`, `ceil`, `floor`, `sqrt`, `to_bits`, `trunc`. Codes: `E-MATH-TYPE`.

```text
sqrt(x) is correctly rounded and floor, ceil, trunc exact, on f32 or f64, alike on every compiler and the device; abs(x) takes a float or a signed integer and traps on the minimum; to_bits(x) is the IEEE pattern as u32 or u64; other arguments are E-MATH-TYPE. exp, log, pow, sin and the like are std.math, libm calls whose last bit varies (row ffi:exp), each bound alone.
```
