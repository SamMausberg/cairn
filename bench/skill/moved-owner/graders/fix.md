---
name: fix
type: llm
---

The answer says `consume(readings)` takes the owner `readings` by value, so `readings` is moved and the later `largest(readings)` uses a moved value. An acceptable fix keeps the program's result: call `largest` before the move, or make `consume` borrow (`ro<u64>[n]` or `ro<Buf[u64]>`) instead of taking the owner. It fails if it proposes copying through unsafe code, a Rust-style `&` reference or `.clone()`, none of which CAIRN has.
