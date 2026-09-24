# Refusals

Each code the compiler, the hosts and `cairn` can name, with the card that states its rule. The fix is the smallest change that keeps the program's meaning.

| Code | Card | Fix |
|---|---|---|
| `E-ALIAS` | [views](cards/views.md) | Pass parts that visibly meet at one boundary, such as x[0..m] and x[m..n], or read through ro. |
| `E-ALIGN` | [records](cards/records.md) |  |
| `E-ARITY` | [calls](SKILL.md#core-rules) |  |
| `E-ASM-CLOBBER` | [assembly](cards/assembly.md) |  |
| `E-ASM-CONSTRAINT` | [assembly](cards/assembly.md) |  |
| `E-ASM-EFFECT` | [assembly](cards/assembly.md) |  |
| `E-ASM-LANE` | [assembly](cards/assembly.md) |  |
| `E-ASM-OPERANDS` | [assembly](cards/assembly.md) |  |
| `E-ASM-TARGET` | [assembly](cards/assembly.md) |  |
| `E-ASSERT-EQ` | [tests](cards/tests.md) |  |
| `E-AST-LIMIT` | [limits](cards/limits.md) |  |
| `E-BOUND` | [generics](cards/generics.md) |  |
| `E-BUILTIN-NAME` | [base](SKILL.md#core-rules) |  |
| `E-CALL` | [calls](SKILL.md#core-rules) |  |
| `E-CALL-SHAPE` | [views](cards/views.md) |  |
| `E-CALL-VIEW` | [views](cards/views.md) |  |
| `E-CALLEE` | [calls](SKILL.md#core-rules) |  |
| `E-CALLER-EFFECT` | [hosts](cards/hosts.md) |  |
| `E-CAST` | [integers](SKILL.md#core-rules) |  |
| `E-CLOSURE` | [closures](cards/closures.md) |  |
| `E-COLLECT-BINDING` | [compact](cards/compact.md) |  |
| `E-COLLECT-CAPACITY` | [compact](cards/compact.md) | The collector's extent must be exactly the output's capacity. |
| `E-COLLECT-SELF-READ` | [compact](cards/compact.md) |  |
| `E-CONST` | [integers](SKILL.md#core-rules) |  |
| `E-CONTEXT-CLOSURE` | [hosts](cards/hosts.md) |  |
| `E-CONTRACT` | [hosts](cards/hosts.md) |  |
| `E-COOP-BARRIER` | [cooperative](cards/cooperative.md) | Move the barrier out from under a condition on a thread name; the whole block must reach it. |
| `E-COOP-CONFLICT` | [cooperative](cards/cooperative.md) | Give each thread its own element of the shared array, for example tile[t], in a phase. |
| `E-COOP-GLOBAL` | [cooperative](cards/cooperative.md) | Write each outside element from one thread, at an index built as block * width + thread. |
| `E-COOP-REUSE` | [cooperative](cards/cooperative.md) | Put a barrier after the last read of the old value, before the thread rewrites the element. |
| `E-COOP-SHAPE` | [cooperative](cards/cooperative.md) |  |
| `E-COOP-SHARED` | [cooperative](cards/cooperative.md) |  |
| `E-COOP-UNDECIDED` | [cooperative](cards/cooperative.md) |  |
| `E-COOP-UNORDERED` | [cooperative](cards/cooperative.md) | Put a barrier between the write and the read the refusal names; every thread must reach it. |
| `E-COOP-WARP` | [cooperative](cards/cooperative.md) |  |
| `E-DECLARATION` | [hosts](cards/hosts.md) | Write only the one function's body; add or remove no declaration. |
| `E-DEFER` | [owners](cards/owners.md) |  |
| `E-DEPTH` | [limits](cards/limits.md) |  |
| `E-DERIVE-COLLISION` | [generators](cards/generators.md) |  |
| `E-DERIVE-DOMAIN` | [generators](cards/generators.md) |  |
| `E-DERIVE-FIELD` | [generators](cards/generators.md) |  |
| `E-DERIVE-RECIPE` | [generators](cards/generators.md) |  |
| `E-DERIVE-TYPE` | [generators](cards/generators.md) |  |
| `E-DISCARD` | [calls](SKILL.md#core-rules) |  |
| `E-DOMAIN` | [validation](cards/validation.md) | The permitted inputs are the host's: narrow where the implementation applies with when instead. |
| `E-DUPLICATE` | [base](SKILL.md#core-rules) |  |
| `E-DYN` | [closures](cards/closures.md) |  |
| `E-EDIT-PROFILE` | [hosts](cards/hosts.md) |  |
| `E-EFFECT-CEILING` | [effects](cards/effects.md) |  |
| `E-EFFECT-EXPANSION` | [hosts](cards/hosts.md) | Change the implementation, not the ceiling: the host owns it. |
| `E-EFFECT-LIMIT` | [limits](cards/limits.md) |  |
| `E-EFFECT-ORDER` | [calls](SKILL.md#core-rules) |  |
| `E-ELEMENT-LOOP` | [base](SKILL.md#core-rules) |  |
| `E-EMULATE` | [parallel](cards/parallel.md) |  |
| `E-ENUM` | [records](cards/records.md) |  |
| `E-ENUM-VARIANT` | [sums](cards/sums.md) |  |
| `E-ESCAPE` | [views](cards/views.md) |  |
| `E-EXPANSION-LIMIT` | [limits](cards/limits.md) |  |
| `E-EXPORT` | [commands](cards/commands.md) |  |
| `E-EXPORT-TAMPERED` | [commands](cards/commands.md) |  |
| `E-EXPORT-TOOLCHAIN` | [commands](cards/commands.md) |  |
| `E-EXPRESSION-BODY` | [base](SKILL.md#core-rules) |  |
| `E-EXTENT` | [views](cards/views.md) |  |
| `E-EXTENT-FIELD` | [owners](cards/owners.md) |  |
| `E-EXTERN` | [effects](cards/effects.md) |  |
| `E-EXTERN-EFFECTS` | [effects](cards/effects.md) |  |
| `E-FAMILY-LIMIT` | [generators](cards/generators.md) |  |
| `E-FAMILY-TARGET` | [generators](cards/generators.md) |  |
| `E-FIELD` | [records](cards/records.md) |  |
| `E-FN-TYPE` | [closures](cards/closures.md) |  |
| `E-FOREIGN` | [foreign](cards/foreign.md) |  |
| `E-FORMAT-TARGET` | [printing](cards/printing.md) |  |
| `E-FRAGMENT` | [fragments](cards/fragments.md) |  |
| `E-GENERIC-ARITY` | [generics](cards/generics.md) |  |
| `E-GENERIC-KIND` | [generics](cards/generics.md) |  |
| `E-GRAD` | [gradients](cards/gradients.md) |  |
| `E-GRAD-CALL` | [gradients](cards/gradients.md) |  |
| `E-GRAD-FORM` | [gradients](cards/gradients.md) |  |
| `E-GRAD-RACE` | [gradients](cards/gradients.md) |  |
| `E-IMMUTABLE` | [base](SKILL.md#core-rules) | Parameters and let bindings are immutable: copy it into a let mut local and change that. |
| `E-IMPL-CALL` | [implementations](cards/implementations.md) | Call a helper the reference and the implementation share, never the reference itself. |
| `E-IMPL-EFFECT` | [implementations](cards/implementations.md) |  |
| `E-IMPL-NUMERICS` | [implementations](cards/implementations.md) |  |
| `E-IMPL-PARAM` | [implementations](cards/implementations.md) | List each natural's values with tune K in [4, 8], and select one of them: plan f use g[8];. |
| `E-IMPL-SIGNATURE` | [implementations](cards/implementations.md) | Copy the reference's parameters, types, extents, placements and result exactly. |
| `E-IMPL-TARGET` | [implementations](cards/implementations.md) |  |
| `E-IMPL-USE` | [implementations](cards/implementations.md) |  |
| `E-IMPL-WHEN` | [implementations](cards/implementations.md) | Test only value parameters, with operations that cannot trap, such as n % 4 == 0 or n >= 64. |
| `E-IMPLEMENTS` | [implementations](cards/implementations.md) |  |
| `E-IMPORT` | [modules](cards/modules.md) | Only the project's modules and std.* can be imported. |
| `E-INDEX` | [views](cards/views.md) |  |
| `E-INFER` | [generics](cards/generics.md) |  |
| `E-INTERNAL` | [limits](cards/limits.md) |  |
| `E-LAUNCH` | [foreign](cards/foreign.md) |  |
| `E-LAYOUT` | [layouts](cards/layouts.md) |  |
| `E-LAYOUT-CONSUMER` | [layouts](cards/layouts.md) |  |
| `E-LAYOUT-GAP` | [layouts](cards/layouts.md) |  |
| `E-LAYOUT-OVERLAP` | [layouts](cards/layouts.md) |  |
| `E-LEASED` | [tasks](cards/tasks.md) | Touch it after the wait, or lend each task a part the other does not touch. |
| `E-LEN` | [views](cards/views.md) |  |
| `E-LENDS` | [lends](cards/lends.md) |  |
| `E-LEX` | [base](SKILL.md#core-rules) |  |
| `E-LINEAR-BRANCH` | [owners](cards/owners.md) |  |
| `E-LINEAR-LEAK` | [owners](cards/owners.md) | Pass it to the function that consumes it, or defer that call, on every path. |
| `E-LINEAR-STORAGE` | [owners](cards/owners.md) |  |
| `E-LITERAL-RANGE` | [integers](SKILL.md#core-rules) |  |
| `E-LOOP-CONTROL` | [base](SKILL.md#core-rules) | break and continue need an enclosing for or while loop. |
| `E-LVALUE` | [base](SKILL.md#core-rules) |  |
| `E-MANGLE` | [modules](cards/modules.md) |  |
| `E-MATCH-BINDING` | [sums](cards/sums.md) | Bind one fresh immutable value only in an arm whose variant declares a payload. |
| `E-MATCH-COVERAGE` | [sums](cards/sums.md) |  |
| `E-MATCH-DUPLICATE` | [sums](cards/sums.md) |  |
| `E-MATCH-TYPE` | [sums](cards/sums.md) |  |
| `E-MATH-TYPE` | [math](cards/math.md) |  |
| `E-MIGRATION` | [migrations](cards/migrations.md) |  |
| `E-MIGRATION-SCOPE` | [migrations](cards/migrations.md) |  |
| `E-MINMAX` | [integers](SKILL.md#core-rules) |  |
| `E-MMA` | [storage](cards/storage.md) |  |
| `E-MOVE-BORROW` | [owners](cards/owners.md) |  |
| `E-MOVE-IN-LOOP` | [owners](cards/owners.md) |  |
| `E-MOVED` | [owners](cards/owners.md) | Use it before it moves, move it once, or lend it (ro<T>, rw<T>) instead of passing it by value. |
| `E-NAME` | [base](SKILL.md#core-rules) |  |
| `E-OPERATOR` | [integers](SKILL.md#core-rules) |  |
| `E-OWNER-ELEMENT` | [memory](cards/memory.md) |  |
| `E-OWNER-EXTENT` | [memory](cards/memory.md) |  |
| `E-PARALLEL-CALL` | [parallel](cards/parallel.md) |  |
| `E-PARALLEL-CONTROL` | [parallel](cards/parallel.md) |  |
| `E-PARALLEL-NEST` | [parallel](cards/parallel.md) |  |
| `E-PARALLEL-RACE` | [parallel](cards/parallel.md) |  |
| `E-PARALLEL-WRITE` | [parallel](cards/parallel.md) |  |
| `E-PARAM` | [base](SKILL.md#core-rules) |  |
| `E-PARSE` | [base](SKILL.md#core-rules) | Use braces, semicolons and CAIRN's grammar, not Rust's or Python's. |
| `E-PARTIAL-MOVE` | [owners](cards/owners.md) |  |
| `E-PINNED` | [rings](cards/rings.md) |  |
| `E-PLACE` | [parallel](cards/parallel.md) |  |
| `E-PLACEMENT` | [parallel](cards/parallel.md) |  |
| `E-PLAN` | [parallel](cards/parallel.md) |  |
| `E-PRESERVE` | [hosts](cards/hosts.md) | Keep what the function does; a witness, when the refusal has one, is an input where it differs. |
| `E-PRINT-ARG` | [printing](cards/printing.md) |  |
| `E-PRIVATE` | [modules](cards/modules.md) |  |
| `E-PROJECT-OR-ENVIRONMENT` | [commands](cards/commands.md) |  |
| `E-PROJECTION` | [limits](cards/limits.md) |  |
| `E-QUANTIZE` | [storage](cards/storage.md) |  |
| `E-RECIPE` | [generators](cards/generators.md) |  |
| `E-RECIPE-STATIC` | [generators](cards/generators.md) |  |
| `E-RECORD` | [records](cards/records.md) |  |
| `E-RECORD-TYPE` | [records](cards/records.md) |  |
| `E-REDUCE-OP` | [parallel](cards/parallel.md) |  |
| `E-REDUCE-ORDER` | [parallel](cards/parallel.md) |  |
| `E-REFERENCE` | [validation](cards/validation.md) | The reference is pinned: write a new function that implements it. |
| `E-REQUEST` | [hosts](cards/hosts.md) |  |
| `E-RESOURCE-OR-IO` | [limits](cards/limits.md) |  |
| `E-RETURN` | [base](SKILL.md#core-rules) | End every path with a return; there is no implicit tail return. |
| `E-SCAN-EXTENT` | [scan](cards/scan.md) |  |
| `E-SCAN-OP` | [scan](cards/scan.md) |  |
| `E-SCAN-ORDER` | [scan](cards/scan.md) |  |
| `E-SCAN-TARGET` | [scan](cards/scan.md) |  |
| `E-SESSION` | [hosts](cards/hosts.md) | Refresh the packet from the host; never guess a digest or a handle. |
| `E-SHADOW` | [base](SKILL.md#core-rules) | Choose a fresh descriptive name; nothing may shadow another name. |
| `E-SIGNATURE` | [hosts](cards/hosts.md) | Keep the parameters, return type and ceiling exactly as written; change only the body. |
| `E-SITE` | [hosts](cards/hosts.md) |  |
| `E-SKETCH-BUDGET` | [sketches](cards/sketches.md) |  |
| `E-SKETCH-CHOICES` | [sketches](cards/sketches.md) |  |
| `E-SKETCH-CONTRACT` | [sketches](cards/sketches.md) |  |
| `E-SKETCH-EMPTY` | [sketches](cards/sketches.md) |  |
| `E-SKETCH-NAME` | [sketches](cards/sketches.md) |  |
| `E-SKETCH-OVERLAP` | [sketches](cards/sketches.md) |  |
| `E-SKETCH-SEALED` | [sketches](cards/sketches.md) |  |
| `E-SKETCH-SITE` | [sketches](cards/sketches.md) |  |
| `E-SOURCE-LIMIT` | [limits](cards/limits.md) |  |
| `E-SPAWN` | [tasks](cards/tasks.md) |  |
| `E-STACK-EXTENT` | [memory](cards/memory.md) |  |
| `E-STACK-LIMIT` | [memory](cards/memory.md) | Declare less stack storage, or a buffer if the ceiling allows alloc. Do not hide the cost. |
| `E-STAGE-BUSY` | [cooperative](cards/cooperative.md) | Put a barrier after release and before the next fill, or give the pipeline one more stage. |
| `E-STAGE-LOOP` | [cooperative](cards/cooperative.md) |  |
| `E-STAGE-UNREADY` | [cooperative](cards/cooperative.md) | Wait for the stage before reading it, and read it before release. |
| `E-STATIC` | [generics](cards/generics.md) |  |
| `E-SUM-ARITY` | [sums](cards/sums.md) |  |
| `E-SUM-PAYLOAD` | [sums](cards/sums.md) |  |
| `E-SYMBOL` | [hosts](cards/hosts.md) | Name a function or type exactly as a packet or a body shows it. |
| `E-TARGET` | [commands](cards/commands.md) |  |
| `E-TARGET-FEATURE` | [commands](cards/commands.md) |  |
| `E-TARGET-MISMATCH` | [commands](cards/commands.md) |  |
| `E-TARGET-TOOLKIT` | [commands](cards/commands.md) |  |
| `E-TEST` | [tests](cards/tests.md) |  |
| `E-TEST-POLICY` | [validation](cards/validation.md) | The cases, the seed and the tests are the host's: submit the implementation and its helpers. |
| `E-TOLERANCE` | [validation](cards/validation.md) | The tolerance is the host's: bring the implementation's result closer to the reference's. |
| `E-TRAIT-AMBIGUOUS` | [generics](cards/generics.md) |  |
| `E-TRAIT-IMPL` | [generics](cards/generics.md) |  |
| `E-TRAIT-OVERLAP` | [generics](cards/generics.md) |  |
| `E-TRY` | [sums](cards/sums.md) |  |
| `E-TYPE` | [base](SKILL.md#core-rules) |  |
| `E-TYPE-MISMATCH` | [base](SKILL.md#core-rules) | Use the expected type; an explicit conversion may trap. Do not change a signature to hide it. |
| `E-UNBOUND` | [base](SKILL.md#core-rules) | Use a name from available_names, or declare it before this use. |
| `E-UNINSTANTIATED` | [generators](cards/generators.md) |  |
| `E-UNPACK` | [owners](cards/owners.md) |  |
| `E-UNREACHABLE` | [base](SKILL.md#core-rules) |  |
| `E-UNSAFE` | [effects](cards/effects.md) |  |
| `E-VALIDATION` | [validation](cards/validation.md) |  |
| `E-VARIANT-AMBIGUOUS` | [sums](cards/sums.md) |  |
| `E-VIEW-ALIAS` | [views](cards/views.md) |  |
| `E-WRAP-TYPE` | [integers](SKILL.md#core-rules) |  |
| `E-WRITE-LEASE` | [views](cards/views.md) | This place is not writable here. Do not turn ro into rw: the host owns that contract. |

Codes are stable across releases.
