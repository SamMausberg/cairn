# Native CAIRN 0.6 language profile

This document specifies implemented native behavior. `docs/history/` records earlier proposals and must not be used to infer extra accepted features. The broader CPU/GPU language is a design goal, not completed by this version. Grammar and checks are implemented in `syntax.py`, `checking.py` and `expansion.py`; actual native behavior also depends on `codegen.py`, the runtime, the C++ compiler and platform.

## Values and conventional source

Source uses ASCII identifiers, UTF-8 comments, braces, semicolons and `//` comments. There is no shadowing, implicit user conversion, operator overloading, or implicit block-tail return. Integers are decimal even with leading zeros. Floating literals contain a decimal point or exponent. Parameters and `let` locals are immutable; `let mut` declares a mutable initialized local. `reg` remains an alias. `each i in n` remains an alias for `for i in 0..n`.

Scalars are `bool`, `u8/u16/u32/u64`, `usize` (64-bit here), `i32/i64`, `f32/f64`. An expression-bodied nonvoid function `fn inc(x:u64)->u64 = add_wrap(x,1);` elaborates to a block with one return. Ordinary blocks still require explicit returns. Nonvoid control-flow paths must return under conservative structural checking. Recursion and `while` may diverge; stack exhaustion is not ruled out.

Ordinary integer +, -, * abort on overflow in every configuration. Division/remainder reject zero and signed minimum/-1. Signed remainder truncates toward zero, not Python floor division. Unsigned `add_wrap/sub_wrap/mul_wrap` are modular; `shl_wrap/shr` require a `usize` count less than width. Bitwise operations are unsigned. Integer-only `min/max` avoid an implicit floating NaN choice. Conversions are explicit; integer narrowing checks range; float-to-integer conversion is unsupported. Expected types guide literals, otherwise defaults are `u64` and `f64`.

Floating compilation requires `-ffp-contract=off -fno-fast-math`; no reassociation or implicit FMA is authorized. This is not a formal IEEE model. NaN payloads, dynamic rounding, floating flags, source trap identity and signal-handler observation are outside the declared model. Guards abort rather than roll back earlier writes or return typed error data.

## Records, enums and tagged outcomes

`struct Pair {x:u64; y:u64;}` defines an ordinary copyable value; construct `Pair(a,b)` and access `.x`. Fields are nonempty, previously declared nonrecursive scalar/record values, not owners, borrows, enums or sums. There are no methods or structural inheritance. Copying a record can cost several instructions.

`enum Op {Read; Write;}` retains tag-only semantics and zero-based tags. A declaration with at least one scalar payload defines a tagged sum:

```cairn
enum Parsed {Value(u64); Invalid(usize); Overflow(usize); Empty;}
fn classify(value:Parsed)->u32 {
  match value {
    Parsed.Value(number) => { return 0; }
    Parsed.Invalid(offset) => { return 1; }
    Parsed.Overflow(offset) => { return 2; }
    Parsed.Empty => { return 3; }
  }
}
```

A variant has zero or one scalar payload. No generic type parameters, multiple/record/owner payloads, recursive sums or borrowed payloads exist. Constructors are `Parsed.Value(x)` and `Parsed.Empty`; a nullary tagged-sum constructor also accepts `Parsed.Empty()`. A sum is copyable, assignable through mutable locals, a parameter or return value. Arrays and record fields of sums are rejected. Equality and direct raw payload/tag projection on sums are rejected.

`match` evaluates its scrutinee once and accepts tag-only enums or tagged sums. Every variant must occur once, with no wildcard. Duplicate, missing or foreign variants are rejected. Payload arms bind one fresh immutable scalar; nullary arms bind none. Bindings do not escape the arm. Return/loop control inside arms is preserved. The backend uses an explicit tag and union record; an invalid foreign tag aborts. A foreign caller must still initialize the matching active payload with the correct type. Numerical tag checking cannot establish lifetime, initialization or foreign ABI correctness.

Typed errors are ordinary sum values. They do not insert allocation, exceptions, propagation, recovery policy or hidden unwrapping. `Division.Value`/`ZeroDivisor` is a concrete type, not a generic `Result<T,E>` library.

## Borrowed memory

An array parameter is `n:usize, input:ro<u8>[n], out:rw<u8>[n]`; optional `@host` has the same CPU meaning. An interface extent is a literal or earlier immutable `usize` parameter. Indexes are `usize`. Read-only views may alias, but each mutable view must be disjoint from every other view in a call. Entry guards check non-null nonempty views, alignment, byte length, nonwrapping address intervals and overlap. Empty views may be null.

Callers must supply live, initialized, typed storage throughout each call and prevent conflicting external access. Guards do not prove provenance, allocation size, initialization or thread behavior. There are no arbitrary pointer casts, local aliases of borrowed views, view returns, callbacks retaining a view, or resizing.

`len(view)` accepts one direct view/buffer name and reads its extent metadata. It does not load all elements. Dynamic extents in helper calls must match by immutable name/literal identity; `len(view)` can supply that known identity. Algebraically equal but differently named extents can be conservatively rejected.

## Scoped storage

```cairn
buffer scratch:u64[n] = zeroed;
stack histogram:usize[256] = zeroed;
```

These statements allocate lexical owners of scalar arrays and expose only their mutable borrows inside the scope. Scalars, including floats and bool, are permitted; records, sums, borrows and nested owners are not. `buffer` capacity must be a literal or immutable `usize` binding; first bind a computed expression to such a local. `stack` requires a literal. Source declarations total at most 65536 bytes per function, even for disjoint branches. This bound excludes compiler locals, stack frames, spills, callees, recursion and ABI overhead.

Storage is zero-initialized before any access. Heap allocation checks native object-size representability before allocating with the nonthrowing allocator; failure aborts. Zero-sized owners are legal but have no valid element access. Huge requests may still encounter OS overcommit/physical OOM; typechecking does not prevent those outcomes. The CLI `run` limits address space by default, not all shared-library callers.

Owners are not first-class values: copying, moving, returning them, storing them in records or escaping a derived borrow is unsupported. A direct borrow can be passed to a helper and is valid for that synchronous call. Normal scope exit, function return, break and continue destroy the lexical heap owner once. Abort/device failure/asynchronous cancellation are not cleanup paths covered here. There is no manual free, general affine owner transfer, capacity growth, arena handle or OS resource owner. Backend RAII is tested, not mechanized.

Receipts report storage names, element types, capacities, placement, zero-initialization and normal-scope release. `heap_allocations` counts syntactic declaration sites, not dynamic allocations in loops/recursion. Interprocedural effects include alloc/free/zero_init/stack_storage and private local reads/writes. No omitted explicit storage declaration is inferred.

## Control and evaluation

`for i in lo..hi` evaluates lo then hi once and visits increasing indices below hi; reversed/empty ranges do nothing. `if/else if/else` and `while` are ordinary blocks. No loop implies parallelism. `break`/`continue` target the nearest loop, including from inside a match. They are rejected outside loops. The native emitter uses internal labels when needed so a switch cannot intercept a loop break; there is no user `goto`.

Logical operators short-circuit. Assignment follows RHS-before-place evaluation in generated C++20. Calls with external writes or heap allocation/free are forbidden as nested operands to avoid unspecified operand order; bind them at an allowed whole-expression root. A private stack-using helper can be permitted because its internal writes do not escape; its stack/initialization effects remain visible. No claim is made about observing trap-site order or floating environment state.

## Compact forms and costs

```cairn
let used = compact out for i in n where predicate yield value;
```

The output is a mutable borrowed parameter or scoped buffer. Its capacity must match the iteration extent by the same name/literal identity; `len` is supported. Predicate is bool, projection has output element type. Neither may mention output or call an externally-writing or allocating/freeing helper. Predicate runs once per visited input; projection only when selected. The stable output prefix is written into existing storage, tail preserved, no temporary array or synchronization introduced. The private cursor never emits more than once per input. Arithmetic obligations are now checked by exact certificates as described in verification.md. Stable-selection semantics and correspondence to the emitted code remain trusted implementation obligations, not consequences of those identities alone.

Families instantiate one static natural parameter over bounded concrete ranges, checking generated ASTs. `derive wire for Packet;` supports fixed-width unsigned fields, little endian, declaration order, no padding. It does not infer framing, authentication or field validity. There are no general macros, generic traits, closures or virtual dispatch. Generator contracts remain in docs/cards/GENERATOR_CONTRACTS.md.

## Projects and edits

A `cairn.toml` lists ordered relative source files and independent task files. Files form one namespace, not modules/imports or separate compilation. Traversal, symlinks, duplicates, unknown keys and executable hooks are rejected. Builds always use fresh directories. The host selects trusted native compilers; manifests do not select commands or arbitrary flags.

Agent body/expression edits preserve signatures, tasks, effect ceilings, visible dependencies and unrelated source. Match/storage/loop sites are included. Whole-module checking runs after insertion. Canonical projection is read-only and may omit comments; edit splicing preserves comments outside authorized ranges. Rule cards are selected from lexical tokens, not substring guesses, so whitespace or code words inside comments cannot hide/add features.

Diagnostics report the specific failure and usable local facts. `E-MATCH-COVERAGE` identifies missing/foreign variants. Storage errors explain capacity, element or stack constraints. These hints never authorize changing the task or permitted effects. No model-intelligence or training result is implied by the existence of diagnostics.

## Scope of proof

See verification.md. Local storage, sums, loops and their wrappers are native-implemented but unsupported by scalar equivalence. Module coverage reports each unchecked function, rather than inheriting one scalar success. There is no Lean-kernel acceptance, entire-compiler proof, weak-memory theorem, allocation/lifetime theorem, native refinement or C++-breadth completeness claim.
