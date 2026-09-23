# The printing card

Sent to an agent when the program uses `eprint`, `eprintln`, `format`, `print`, `println`. Codes: `E-FORMAT-TARGET`, `E-PARALLEL-CALL`, `E-PLACEMENT`, `E-PRINT-ARG`.

```text
println("n = ", n, ' ', ok); writes each argument in turn: an integer in decimal (-1 alone is an i64), a bool as true/false, a character literal as its byte, a float in its shortest round-trip form, or the bytes of a string, u8 view, part, Buf, Array or Vec[u8]; anything else is E-PRINT-ARG, so format it with std.fmt or print its fields. print, println, eprint, eprintln: stdout or stderr, no allocation; format(v, ...) appends to a Vec[u8] (E-FORMAT-TARGET) and may grow it. Every argument is computed, left to right, before a byte is written. Not in a lane (E-PLACEMENT, E-PARALLEL-CALL); a program's own print wins.
```
