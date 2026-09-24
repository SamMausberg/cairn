# The limits card

A refusal names this card. Codes: `E-AST-LIMIT`, `E-DEPTH`, `E-EFFECT-LIMIT`, `E-EXPANSION-LIMIT`, `E-INTERNAL`, `E-PROJECTION`, `E-RESOURCE-OR-IO`, `E-SOURCE-LIMIT`.

```text
The compiler bounds its own work: 16000000 bytes of source (E-SOURCE-LIMIT), expressions nested 100 deep (E-DEPTH), 200000 expression visits while checking (E-AST-LIMIT), 32768 functions and 3200000 syntax nodes after expansion, 2048 copies from one recipe or from all families (E-EXPANSION-LIMIT), and a finite effect fixed point (E-EFFECT-LIMIT); split the program or the expression. E-INTERNAL and E-PROJECTION are faults of the compiler, never of the program: report them with the program. E-RESOURCE-OR-IO says the compiler could not read or hold its input, and establishes nothing about the program.
```
