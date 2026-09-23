# Refusals

Each code `cairn check` can name that a card states or the host has a fix for. Read the card for the rule; the fix is the smallest change that keeps the program's meaning.

| Code | Card | Fix |
|---|---|---|
| `E-ALIAS` | [views](cards/views.md) | Pass parts that visibly meet at one boundary, such as x[0..m] and x[m..n], or read through ro. |
| `E-ARITY` | [views](cards/views.md), [tests](cards/tests.md) |  |
| `E-ASM-CLOBBER` | [assembly](cards/assembly.md) |  |
| `E-ASM-CONSTRAINT` | [assembly](cards/assembly.md) |  |
| `E-ASM-EFFECT` | [assembly](cards/assembly.md) |  |
| `E-ASM-LANE` | [assembly](cards/assembly.md) |  |
| `E-ASM-OPERANDS` | [assembly](cards/assembly.md) |  |
| `E-ASM-TARGET` | [assembly](cards/assembly.md) |  |
| `E-ASSERT-EQ` | [tests](cards/tests.md) |  |
| `E-CALLEE` | [tests](cards/tests.md) |  |
| `E-CAST` | [storage](cards/storage.md) |  |
| `E-COLLECT-CAPACITY` |  | The collector's extent must be exactly the output's capacity. |
| `E-COOP-BARRIER` | [cooperative](cards/cooperative.md) | Move the barrier out from under a condition on a thread name; the whole block must reach it. |
| `E-COOP-CONFLICT` | [cooperative](cards/cooperative.md) | Give each thread its own element of the shared array, for example tile[t], in a phase. |
| `E-COOP-GLOBAL` | [cooperative](cards/cooperative.md) | Write each outside element from one thread, at an index built as block * width + thread. |
| `E-COOP-REUSE` | [cooperative](cards/cooperative.md) | Put a barrier after the last read of the old value, before the thread rewrites the element. |
| `E-COOP-SHAPE` | [cooperative](cards/cooperative.md) |  |
| `E-COOP-SHARED` | [cooperative](cards/cooperative.md) |  |
| `E-COOP-UNDECIDED` | [cooperative](cards/cooperative.md) |  |
| `E-COOP-UNORDERED` | [cooperative](cards/cooperative.md) | Put a barrier between the write and the read the refusal names; every thread must reach it. |
| `E-COOP-WARP` | [cooperative](cards/cooperative.md), [fragments](cards/fragments.md) |  |
| `E-DECLARATION` |  | Write only the one function's body; add or remove no declaration. |
| `E-DISCARD` | [sums](cards/sums.md) |  |
| `E-DOMAIN` |  | The permitted inputs are the host's: narrow where the implementation applies with when instead. |
| `E-EFFECT-CEILING` | [effects](cards/effects.md) |  |
| `E-EFFECT-EXPANSION` |  | Change the implementation, not the ceiling: the host owns it. |
| `E-EFFECT-ORDER` | [calls](SKILL.md#core-rules), [assembly](cards/assembly.md), [tests](cards/tests.md) |  |
| `E-EXTENT` | [owners](cards/owners.md) |  |
| `E-EXTENT-FIELD` | [owners](cards/owners.md) |  |
| `E-FORMAT-TARGET` | [printing](cards/printing.md) |  |
| `E-FRAGMENT` | [fragments](cards/fragments.md) |  |
| `E-GRAD` | [gradients](cards/gradients.md) |  |
| `E-GRAD-CALL` | [gradients](cards/gradients.md) |  |
| `E-GRAD-FORM` | [gradients](cards/gradients.md) |  |
| `E-GRAD-RACE` | [gradients](cards/gradients.md) |  |
| `E-IMMUTABLE` |  | Parameters and let bindings are immutable: copy it into a let mut local and change that. |
| `E-IMPL-CALL` | [implementations](cards/implementations.md) | Call a helper the reference and the implementation share, never the reference itself. |
| `E-IMPL-EFFECT` | [foreign](cards/foreign.md), [implementations](cards/implementations.md) |  |
| `E-IMPL-NUMERICS` | [implementations](cards/implementations.md) |  |
| `E-IMPL-SIGNATURE` | [implementations](cards/implementations.md) | Copy the reference's parameters, types, extents, placements and result exactly. |
| `E-IMPL-TARGET` | [implementations](cards/implementations.md) |  |
| `E-IMPL-USE` | [implementations](cards/implementations.md) |  |
| `E-IMPL-WHEN` | [implementations](cards/implementations.md) | Test only value parameters, with operations that cannot trap, such as n % 4 == 0 or n >= 64. |
| `E-IMPLEMENTS` | [implementations](cards/implementations.md) |  |
| `E-IMPORT` |  | Only the project's modules and std.* can be imported. |
| `E-LAUNCH` | [foreign](cards/foreign.md) |  |
| `E-LAYOUT` | [layouts](cards/layouts.md) |  |
| `E-LAYOUT-CONSUMER` | [layouts](cards/layouts.md), [fragments](cards/fragments.md) |  |
| `E-LAYOUT-GAP` | [layouts](cards/layouts.md) |  |
| `E-LAYOUT-OVERLAP` | [layouts](cards/layouts.md) |  |
| `E-LEASED` | [tasks](cards/tasks.md) | Touch it after the wait, or lend each task a part the other does not touch. |
| `E-LENDS` | [lends](cards/lends.md) |  |
| `E-LINEAR-BRANCH` | [owners](cards/owners.md) |  |
| `E-LINEAR-LEAK` | [sums](cards/sums.md), [owners](cards/owners.md) | Pass it to the function that consumes it, or defer that call, on every path. |
| `E-LINEAR-STORAGE` | [memory](cards/memory.md) |  |
| `E-LOOP-CONTROL` |  | break and continue need an enclosing for or while loop. |
| `E-MATCH-BINDING` |  | Bind one fresh immutable value only in an arm whose variant declares a payload. |
| `E-MATCH-COVERAGE` | [sums](cards/sums.md) |  |
| `E-MATH-TYPE` | [math](cards/math.md) |  |
| `E-MMA` | [storage](cards/storage.md) |  |
| `E-MOVE-IN-LOOP` | [owners](cards/owners.md) |  |
| `E-MOVED` | [owners](cards/owners.md) | Use it before it moves, move it once, or lend it (ro<T>, rw<T>) instead of passing it by value. |
| `E-OPERATOR` | [storage](cards/storage.md) |  |
| `E-OWNER-EXTENT` | [memory](cards/memory.md) |  |
| `E-PARALLEL-CALL` | [printing](cards/printing.md) |  |
| `E-PARALLEL-RACE` | [assembly](cards/assembly.md) |  |
| `E-PARSE` |  | Use braces, semicolons and CAIRN's grammar, not Rust's or Python's. |
| `E-PARTIAL-MOVE` | [owners](cards/owners.md) |  |
| `E-PINNED` | [rings](cards/rings.md) |  |
| `E-PLACEMENT` | [foreign](cards/foreign.md), [printing](cards/printing.md) |  |
| `E-PLAN` | [parallel](cards/parallel.md) |  |
| `E-PRESERVE` |  | Keep what the function does; a witness, when the refusal has one, is an input where it differs. |
| `E-PRINT-ARG` | [printing](cards/printing.md) |  |
| `E-PRIVATE` | [modules](cards/modules.md) |  |
| `E-QUANTIZE` | [storage](cards/storage.md) |  |
| `E-RECORD-TYPE` | [records](cards/records.md) |  |
| `E-REDUCE-ORDER` | [parallel](cards/parallel.md) |  |
| `E-REFERENCE` |  | The reference is pinned: write a new function that implements it. |
| `E-RETURN` |  | End every path with a return; there is no implicit tail return. |
| `E-SCAN-EXTENT` | [scan](cards/scan.md) |  |
| `E-SCAN-OP` | [scan](cards/scan.md) |  |
| `E-SCAN-ORDER` | [scan](cards/scan.md) |  |
| `E-SCAN-TARGET` | [scan](cards/scan.md) |  |
| `E-SESSION` |  | Refresh the packet from the host; never guess a digest or a handle. |
| `E-SHADOW` |  | Choose a fresh descriptive name; nothing may shadow another name. |
| `E-SIGNATURE` |  | Keep the parameters, return type and ceiling exactly as written; change only the body. |
| `E-STACK-LIMIT` | [memory](cards/memory.md) | Declare less stack storage, or a buffer if the ceiling allows alloc. Do not hide the cost. |
| `E-STAGE-BUSY` | [cooperative](cards/cooperative.md) | Put a barrier after release and before the next fill, or give the pipeline one more stage. |
| `E-STAGE-LOOP` | [cooperative](cards/cooperative.md) |  |
| `E-STAGE-UNREADY` | [cooperative](cards/cooperative.md) | Wait for the stage before reading it, and read it before release. |
| `E-SYMBOL` |  | Name a function or type exactly as a packet or a body shows it. |
| `E-TARGET-FEATURE` | [fragments](cards/fragments.md) |  |
| `E-TEST` | [tests](cards/tests.md) |  |
| `E-TEST-POLICY` |  | The cases, the seed and the tests are the host's: submit the implementation and its helpers. |
| `E-TOLERANCE` |  | The tolerance is the host's: bring the implementation's result closer to the reference's. |
| `E-TRY` | [sums](cards/sums.md) |  |
| `E-TYPE-MISMATCH` | [storage](cards/storage.md) | Use the expected type; an explicit conversion may trap. Do not change a signature to hide it. |
| `E-UNBOUND` |  | Use a name from available_names, or declare it before this use. |
| `E-VARIANT-AMBIGUOUS` | [sums](cards/sums.md) |  |
| `E-WRITE-LEASE` |  | This place is not writable here. Do not turn ro into rw: the host owns that contract. |

A code not listed here says what to change in its message. Codes are stable across releases.
