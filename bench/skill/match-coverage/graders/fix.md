---
name: fix
type: llm
---

The answer says a `match` has exactly one arm per variant and no wildcard, so `_ => return 0;` is refused and `Empty` has no arm of its own. An acceptable fix replaces the wildcard arm with `Empty => return 0;`. It fails if it keeps a wildcard, turns the match into an if chain that compares tags, or changes the sum.
