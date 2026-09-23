---
max_turns: 12
allowed_tools: [Read, Glob, Grep, Skill]
---

Without running anything, say whether `cairn check` accepts this CAIRN program. If it refuses it, name the diagnostic code it reports, say in two or three sentences which rule the program breaks, and give the smallest change that makes it check while keeping what the program computes.

```cairn
// Fills the first half on a task while main marks the start of the data.
fn fill(n:usize, out:rw<u64>[n], start:u64) {
  for i in 0..n { out[i] = start + u64(i); }
}

fn main() -> i32 {
  let mut data = Buf[u64](8);
  let half = spawn fill(data[0..4], 100);
  data[0] = 7;
  wait(half);
  if data[1] != 101 { return 1; }
  return 0;
}
```
