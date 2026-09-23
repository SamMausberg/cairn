# The implementations card

Sent to an agent when the program uses `implements`. Codes: `E-IMPL-CALL`, `E-IMPL-EFFECT`, `E-IMPL-NUMERICS`, `E-IMPL-SIGNATURE`, `E-IMPL-TARGET`, `E-IMPL-USE`, `E-IMPL-WHEN`, `E-IMPLEMENTS`.

```text
fn total_by4(n:usize, xs:ro<u64>[n]) -> u64 implements total when n % 4 == 0 { ... } is a separately written implementation of the reference total: exactly its parameters, types, extents, placements and result (E-IMPL-SIGNATURE), declared in total's module, not generic, not a kernel, and never an implementation of an implementation (E-IMPLEMENTS). Its row stays inside total's declared ceiling, or inside total's own row when total declares none (E-IMPL-EFFECT: a while loop adds diverge, a Buf alloc, a parallel region par:host), and it writes no rounding total does not write (E-IMPL-NUMERICS).

when is optional and cannot trap: comparisons, && || !, & | ^ ~, min, max, the wrapping forms, / or % by a nonzero literal and len of a view parameter, over value parameters, literals and constants (E-IMPL-WHEN). Where it is false the reference runs, so the input domain stays the reference's. needs(cp_async) names the device features its device code uses (E-IMPLEMENTS for any other name, or for an implementation that runs no device code).

plan total use total_by4; is the only way it runs: total's body then starts by testing the condition and calling total_by4, and a call such as total(8, xs), whose arguments decide the condition, calls total_by4 directly (E-IMPL-USE for a name that is not total's implementation, or a second selection; E-IMPL-TARGET when the build's device target lacks a feature the selected implementation needs). Without a plan the reference runs. Only a test block calls an implementation by name, and an implementation never calls its reference (E-IMPL-CALL). total's row joins every implementation's, so a selection changes no row. Do not change the reference, the signature or the ceiling to admit an implementation.
```
