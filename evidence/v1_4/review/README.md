# An adversarial review of what was added after 1.3.0

On 2026-09-22 one reviewer tried to break the soundness of every form and tool added since `ed5a179`, before a public release. [SECURITY.md](../../../SECURITY.md) defines a soundness bug. The attacks were written as CAIRN programs and edit-host requests, checked by the compiler of main at `be68a40` and after, and every accepted program was built with the runtime headers and run under AddressSanitizer with UndefinedBehaviorSanitizer or under ThreadSanitizer, with clang++ 21.1.8 and g++ 13.3. Nothing ran on a device.

## What was found and fixed

Three holes were found, all in the edit host rather than the language. Each is fixed in the file that owns the rule, and a regression test names its code.

| hole | what an agent could do | fix | test |
|---|---|---|---|
| A migration reply could declare more than its function | add a `const`, or a `plan` for a function the host never authorized, beside a replacement; the recheck compared only the set of functions | a replacement declares exactly one function and nothing else (`E-MIGRATION`), and the program keeps every other declaration it had, schedules included (`E-DECLARATION`) | `tests/agent/test_migration.py` |
| A reply ending in a line comment reached past its span | a body edit ending in `//` hid what the file wrote after the function on that line; a plan there was dropped and the edit was admitted | every token outside an edit's span must lex as it did (`E-DECLARATION`), for body and expression edits | `tests/agent/test_agent.py` |
| A sketch choice could delete code after its slot | a choice ending in `//` hid its own closing parenthesis and the rest of the line; where the next line closed the parenthesis, a call after the slot vanished while the receipt said only the declared expression changed | a slot value carries no comment (`E-SKETCH-CHOICES`) | `tests/agent/test_sketches.py` |

Two statements in the documentation were not exact, and now are. A host lane may read an I/O ring's `status`, `room` and `pending`, which only the owning thread changes and no lane can; the concurrency chapter had said a lane may not reach a ring at all. Submitting, collecting, cancelling and waiting stay refused in a lane (`E-PARALLEL-CALL`), and a device lane may not use a ring (`E-PLACEMENT`); the reads run clean under ThreadSanitizer with both compilers. A map `Slot` resolves to `None` once its key is removed or its map rehashes, but it answers for the map that gave it: every map counts its stamps from zero, so a Slot of one map resolved against another is only a number there, as a raw index is. The library chapter and `std/map.cairn` now say so. That is a question of logical identity, not of memory: `resolve` checks the index against the map it is given.

## What held

`tests/soundness/test_review_1_4.py` keeps 54 of the attacks that were correctly refused, each with the code it must keep, and 12 that compile and must stop at a guard or run race free, each under both compilers. What was tried, by target:

| target | tried | outcome |
|---|---|---|
| call statements, `let _` | dropping a task, a group, a linear value, one inside an `Option` or a record, a linear success of `try`; letting go of a leased owner; reading `_` | refused: `E-DISCARD`, `E-SPAWN`, `E-LINEAR-LEAK`, `E-LEASED`, `E-UNBOUND` |
| compound assignment | `+=` on a leased element or scalar, a moved owner, a declared extent field, a static string, an `ro` view, a storage float; by every lane on one element or a captured scalar; with an index that writes; across two borrows of one scalar | refused with the long form's codes; overflow, division and an index past the end trap |
| element loops | the owner, a field, the whole record or a declared-extent record replaced, swapped, taken or lent inside the loop, by the body or a closure; loops over leased, device, lane-written or array-element storage; moving the collection; index-name collisions | refused where the rule applies; every replacement keeps its guard and traps |
| bare variants, one-statement arms | clashes with a function, a local, a constant or a type parameter; arms that break, continue or return out of a lane; a payload moved twice; a linear payload dropped | refused: `E-VARIANT-AMBIGUOUS`, `E-LOOP-CONTROL`, `E-PARALLEL-CONTROL`, `E-MOVED`, `E-LINEAR-LEAK` |
| guard elision | facts through `&&`, `||` and their negations, `else` branches, early exits, `while` conditions, `break`-guarded loops, closures, narrowing and wrapping narrowing, `i + 1` and `i - 1`, parts with reversed or settled bounds, mutable owners and `rw` parameters changed after a check, declared extents replaced through a callee, a swap, a take, a closure or a nested field | no guard was dropped where its facts fail; every such program traps |
| the checked entry and its lean body | aliased or overlapping views through function values, tasks, `dyn`, symbolic array elements and parts, parts of parts, a view beside its record, a Vec's elements beside the Vec, closures that capture what a call was lent | refused: a function type carries no array view (`E-FN-TYPE`), and every aliasing call is `E-ALIAS` or `E-LEASED`, so the numeric check the lean body skips never had work left |
| the I/O ring | a count past the `Buf` for read, write and send; a field's `Buf` without `take`; a ring lent to a task and queried or waited; passed by value; never or twice waited; a full ring; `next` with nothing in flight; lanes that submit or ask | the counts, the full ring and the empty `next` trap; the rest are refused |
| test blocks, `assert` | a test that leaks, a test called as a function, a non-literal message, messages with quotes, `%n`, a NUL and a comment opener | refused, or printed as written: a message is never a format string |
| storage floats, `quantize` | arithmetic and comparison on storage floats, `from_bits` into `bool` or an enum, a zero scale, overflow of `f8e4m3` | refused, or trap |
| `derive grad` | a region whose lanes read a shared element, a shared scalar, a neighbour; a forward function with a `pure` ceiling or I/O | refused where an adjoint would race or cannot be formed; a shared scalar is summed by a sequential loop after the region, race free |
| edit-host requests | a migration reply that adds a constant, a plan, a test block, an extern, an impl, a record, an import, a derive or a second function; body and expression edits and sketch choices ending in comments; plan-only replies with booleans or foreign items; shot requests | the three holes above; the rest were refused |

## What this does not show

A review finds what its author thought to try, and one reviewer wrote every attack here. It covers the checker, the emitter's guards and the edit host on one x86-64 machine, and it ran nothing on a device, so the device lowering of the new forms, of storage floats and of derived gradients is compiled only. The value model, the Lean calculus and the performance model were not attacked. g++ with AddressSanitizer at `-O3` warns, and so fails under `-Werror`, on a constant index it can prove out of bounds even where the guard aborts first; a plain g++ build compiles and traps, and the table avoids constant out-of-bounds indices for that reason. The runtime agent's task-thread and scratch reuse, and `cairn diff`, were still landing while this ran and were not reviewed.
