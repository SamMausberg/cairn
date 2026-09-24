# The tests card

Sent to an agent when the program uses `assert`, `assert_eq`, `test`. Codes: `E-ASSERT-EQ`, `E-TEST`.

```text
test sums { let s = total(3); assert(s == 6, "three"); } is a test: no parameters, no result, one name per module (E-TEST), checked like a void function with any effects. No other build holds a test and nothing can call one (E-CALLEE). cairn test runs every test block, each in a process of its own, beside the project's JSON contracts; a test passes only when its process exits 0, so a failed assert or guard, a signal or a timeout fails that test alone. assert(cond) or assert(cond, "why") traps when cond is false, naming its file and line; the condition is a bool and the text one literal (E-ARITY). assert_eq(a, b) also prints both scalars when they differ (E-ASSERT-EQ otherwise). A call that allocates or writes is bound before the assert that reads it (E-EFFECT-ORDER).
```
