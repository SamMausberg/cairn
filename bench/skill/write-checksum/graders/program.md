---
name: program
type: llm
---

The answer is one CAIRN program that `cairn check` would accept: a signature exactly `fn checksum(n:usize, bytes:ro<u8>[n]) -> u32`, a loop over the bytes, each byte converted explicitly with `u32(bytes[i])`, the running sum kept with `add_wrap` so that it wraps modulo 2^32 instead of trapping, an explicit `return` on every path, and a `test NAME { ... }` block that builds its input (for example with `Buf[u8](3)`) and uses `assert_eq` on a sum it computes correctly. It fails for any Rust, C++ or Python habit CAIRN refuses: `as` casts, `&` or `&mut`, `wrapping_add`, iterators or closures over the array, a tail expression without `return`, or a `String`.
