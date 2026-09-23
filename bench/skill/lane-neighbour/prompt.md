---
max_turns: 12
allowed_tools: [Read, Glob, Grep, Skill]
---

Without running anything, say whether `cairn check` accepts this CAIRN program. If it refuses it, name the diagnostic code it reports, say in two or three sentences which rule the program breaks, and give the smallest change that makes it check while keeping what the program computes.

```cairn
// Each output element is the difference of two neighbouring inputs, computed in parallel lanes.
fn differences(n:usize, xs:ro<i64>[n], out:rw<i64>[n]) {
  parallel i in n - 1 {
    out[i + 1] = xs[i + 1] - xs[i];
  }
}

fn main() -> i32 {
  let mut xs = Buf[i64](5);
  let mut out = Buf[i64](5);
  for i in 0..5 { xs[i] = i64(i) * i64(i); }
  differences(5, xs[0..5], out[0..5]);
  if out[4] != 7 { return 1; }
  return 0;
}
```
