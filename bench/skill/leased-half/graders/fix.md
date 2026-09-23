---
name: fix
type: llm
---

The answer says `spawn fill(data[0..4], 100)` lends `data` to the task `half` until `wait(half)`, so writing `data[0]` before the wait touches memory the task holds. An acceptable fix moves `data[0] = 7;` after `wait(half);`. It fails if it adds a mutex, an atomic or unsafe code, removes the task, or claims that writing another element of `data` before the wait would be accepted.
