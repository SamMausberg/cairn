---
max_turns: 12
allowed_tools: [Read, Glob, Grep, Skill]
---

Write a CAIRN function `checksum(n:usize, bytes:ro<u8>[n]) -> u32` that returns the sum of the bytes modulo 2^32, and a `test` block that checks it on three bytes whose sum you state. Answer with the code in one ```cairn block and nothing else.
