---
max_turns: 12
allowed_tools: [Read, Glob, Grep, Skill]
---

Without running anything, say whether `cairn check` accepts this CAIRN program. If it refuses it, name the diagnostic code it reports, say in two or three sentences which rule the program breaks, and give the smallest change that makes it check while keeping what the program computes.

```cairn
// The area of a shape, with pi taken as 3; an empty shape has none.
enum Shape { Square(u64); Circle(u64); Empty; }

fn area(s:Shape) -> u64 {
  match s {
    Square(side) => return side * side;
    Circle(radius) => return 3 * radius * radius;
    _ => return 0;
  }
}

fn main() -> i32 {
  if area(Square(3)) != 9 { return 1; }
  if area(Empty) != 0 { return 1; }
  return 0;
}
```
