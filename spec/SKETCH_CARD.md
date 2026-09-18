# CAIRN 0.4 named-choice card

Read the host packet: task, source, named slots, expected types, local bindings,
allowed effects and selected language cards. Return only one JSON object mapping
EVERY slot name to a CAIRN expression string. No Markdown, extra keys or duplicate
keys. Example reply: {"value":"(x & y) + shr(x ^ y, 1)"}.

The host inserts the choices into its pinned original source with parentheses,
then rechecks the complete module. Do not rewrite function signatures, task,
reference, permissions, preconditions, compiler settings or tests. Missing context
is not permission to invent a dependency. Slot names are descriptive identifiers,
not code or persistent IDs. The host binds your reply; do not invent hashes.

Use familiar expressions, but preserve CAIRN semantics: fixed-width integers;
ordinary +,-,* trap on overflow; explicit unsigned add_wrap/sub_wrap/mul_wrap;
checked casts and shift counts; sequential loops; no implicit allocation. A type
error requests a local type repair, not changing the public API. A semantic
counterexample gives an input where your proposal returns a wrong value or traps.
Fix the algorithm on the original domain. Do not patch only that example.

Typed means only the static checks passed. The optional scalar checker compares
with a fixed host reference for all declared-width inputs under a total, nonempty
host precondition. smt-equivalent trusts the translator and Z3; it is not a Lean
proof or native-code proof. It models scalar integers/bools, branches and acyclic
calls, not arrays, loops, floats, concurrency or GPU execution. Unknown/timeout is
never accepted. Tests and benchmarks are separate stages. No model has been
trained or evaluated by this release merely because this card is supplied.
