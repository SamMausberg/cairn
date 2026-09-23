# The effects card

Sent to an agent when the program uses `effects`, `extern`, `pure`, `unsafe`. Codes: `E-EFFECT-CEILING`.

```text
Every function has an inferred effect row; pure and effects(read:x, trap) after the return type are checked ceilings, never wishes (E-EFFECT-CEILING). extern fn write(fd:i32, data:ro<u8>[n], n:usize) -> i64 effects(io); declares a C symbol whose effects are mandatory because its body is invisible, and ffi:write then appears in every transitive caller. alloc is charged where storage is taken and free where it goes back, so fn sink(b:Buf[u64]) {} has the row free, and a ceiling that leaves free out is refused.

Foreign calls, mmio_read[u32](addr), mmio_write[u32](addr, v) and asm("wfi") are legal only inside unsafe { }. Do not widen a ceiling or add unsafe to make an edit pass.
```
