---
max_turns: 12
allowed_tools: [Read, Glob, Grep, Skill]
---

Without running anything, say whether `cairn check` accepts this CAIRN program. If it refuses it, name the diagnostic code it reports, say in two or three sentences which rule the program breaks, and give the smallest change that makes it check while keeping what the program computes.

```cairn
// Pushes a value into a counter and returns how many were pushed, twice, and adds the answers.
fn push(n:usize, counts:rw<u64>[n], at:usize) -> u64 {
  counts[at] += 1;
  return counts[at];
}

fn main() -> i32 {
  let mut counts = Buf[u64](4);
  let total = push(counts, 1) + push(counts, 1);
  if total != 3 { return 1; }
  return 0;
}
```
