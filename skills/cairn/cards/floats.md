# The floats card

Sent to an agent when the program uses `f32`, `f64`.

```text
f32/f64 use -ffp-contract=off -fno-fast-math: no reassociation or FMA. Strict flags are not a mechanized IEEE proof. Floating exception flags and NaN payloads are outside the observation model; compiler, runtime and hardware stay trusted.
```
