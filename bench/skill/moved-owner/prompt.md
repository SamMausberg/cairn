---
max_turns: 12
allowed_tools: [Read, Glob, Grep, Skill]
---

Without running anything, say whether `cairn check` accepts this CAIRN program. If it refuses it, name the diagnostic code it reports, say in two or three sentences which rule the program breaks, and give the smallest change that makes it check while keeping what the program computes.

```cairn
// Keeps the larger of two readings and reports how many it saw.
fn largest(n:usize, xs:ro<u64>[n]) -> u64 {
  let mut best:u64 = 0;
  for i in 0..n { if xs[i] > best { best = xs[i]; } }
  return best;
}

fn consume(v:Buf[u64]) -> usize = len(v);

fn main() -> i32 {
  let mut readings = Buf[u64](4);
  readings[2] = 9;
  let count = consume(readings);
  if largest(readings) != 9 { return 1; }
  if count != 4 { return 1; }
  return 0;
}
```
