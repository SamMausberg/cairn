# CAIRN 0.2: general systems design and native profile

Revision date: 17 September 2026.

## 1. What this revision changes

CAIRN now has a real, executable native frontend. It parses a conventional source language, checks a restricted type/effect system, expands three closed generative forms, emits readable C++20, and builds CPU machine code with Clang or GCC. Version 0.1 had only an independent finite JSON checker. The native compiler is new and is not a verified lowering of that earlier core.

The full language's intended scope is general systems software: ordinary applications and libraries, operating-system components, databases, simulators, network services, embedded code, CPU parallelism, and GPU kernels/runtimes. This is capability breadth, not compatibility with every C++ surface feature. The current compiler does not implement that entire scope. Section 4 separates the implemented profile from the full design; section 19 gives an explicit acceptance criterion for each missing subsystem. No design entry in that table is an implemented feature by implication.

The design keeps observable resource cuts from 0.1. A cut fixes behavior, ownership, failure behavior, and permitted costs at a component boundary. Implementations can change private representation and schedule without changing that contract. The new organizing mechanism is **contracted expansion**: concise, typed generative descriptions elaborate into ordinary inspectable code, with semantic and resource obligations attached to the expansion rather than repeated at every use.

Two principles govern this revision. First, compression should remove repeated decisions, not meaningful names or visible effects. Second, performance should come from a representation that exposes invariants to the backend, not from changing semantics or asserting that a short expression must be fast.

## 2. The 100x target, made testable

A fixed, fully charged vocabulary cannot give every arbitrary program a lossless 100x shorter description: there are more independent long descriptions than short descriptions available to identify them. Most useful compression must exploit repeated structure or a shared dictionary of algorithms. Libraries, templates, and compiler primitives are such dictionaries, including in C++.

The design therefore targets very large reductions for repeated families, protocol schemas, data-layout conversions, staged schedules, and local edits. It does not promise 100x against concise C++ for every algorithm. A family declaration may describe hundreds of native functions, but that is an expansion ratio, not evidence that C++ templates need hundreds of copies.

Four quantities must be reported separately:

1. Expansion ratio: tokens in generated code divided by tokens in authored source.
2. Comparative source ratio: well-factored C++ source divided by CAIRN source for a stated behavior and API.
3. Cold-context cost: source plus language instructions, imported contract definitions, examples and task requirements supplied to the agent.
4. Edit cost: the actual read, write, proof and diagnostic transcript for a change, charging fetched dependencies on both sides.

A generator may keep implementation details out of a normal call-site edit only when its contract is sufficient for that edit. Changing the generator itself invalidates that assumption. An unresolved correctness obligation cannot be hidden by presenting a shorter view. The measurements in this release are lexical counts, not model-comprehension or safe-edit-success experiments.

## 3. One language, three views

The authored view contains data definitions, contracts, compositional algorithms and explicit execution choices. The expanded view contains typed operations, instantiated layouts, calls, bounds obligations and effects. The target view contains generated C++ today, and eventually machine instructions, register/shared-memory use, transfer instructions and synchronization. These are views of the same artifact, not three independently editable programs.

Every component has a stable name. In the full design, a module owns `schema`, `contract`, `implementation`, `mapping`, and `tests` sections. Small modules may put these together; large modules may split them into files without changing meaning. Generated definitions retain source-origin IDs. Public contracts and wire formats are versioned independently of optimization schedules. No unrelated dependency should enter an agent's context merely because it shares a source file.

Native 0.2 currently accepts one source file at a time and uses a single declaration namespace. It emits source/generation/runtime hashes and per-function effects. It does not implement a module linker, a language server, a formatter, incremental checking, a minimum-context extractor, or full source maps for generated definitions. Its generated C++ and receipts are the first two implemented inspection surfaces.

## 4. Breadth and implementation status

| Capability | Full design decision | Native 0.2 status |
|---|---|---|
| Scalar and structured computation | Value types, explicit conversions, records, tagged sums, pattern matching | Numeric scalars, bool, records, tag-only enums, branches, loops, recursion |
| Generic libraries | Static type/value parameters and constrained implementations; monomorphization by default | One static natural parameter and bounded concrete families |
| Data structures | Owned regions, borrowed views, arenas, explicit grow/reserve; generational handles for graphs | Borrowed host arrays only; no owning containers or heap operations |
| Resource lifetime | Affine owners, lexical borrowing, explicit-cost cleanup; unwind-free Result paths by default | No owning values, destructors, allocation or free |
| Object-oriented interfaces | Records plus traits; composition; explicitly marked dynamic interfaces | Records and direct calls only |
| Closures and callbacks | Explicit capture ownership; stack environment unless allocation requested | Not implemented |
| Errors | Tagged Result values and visible propagation; traps separate from recoverable errors | Defined aborting guards; no Result type |
| CPU concurrency | Scoped tasks, ownership transfer, atomics with explicit order, audited locks | Not implemented; external mutation forbidden during native calls |
| GPU programming | Placement- and scope-indexed regions, collective participation, tickets, target intrinsics | Prior design retained; no native GPU code generation |
| Asynchronous I/O | Linear completion tickets, explicit cancel/drain state, event dependency graph | Not implemented |
| Interoperability | C ABI first; C++ wrappers with explicit effects, ownership and exception boundaries | Exported C-linkage functions; host calls them using the generated ABI |
| Low-level systems | Freestanding target, volatile/MMIO intrinsics, layout attributes, inline target blocks with contracts | Hosted 64-bit ABI only; no freestanding, MMIO or inline assembly |
| Metaprogramming | Typed AST construction with deterministic dependencies and expansion budgets | Closed family, wire, and bounded-collector generators |
| General applications | OS I/O, text/bytes, collections, runtime services as explicit capability libraries | Native algorithm libraries only; no standard application library |
| Verification | Cut replacement and resource certificates checked against fixed semantics | Python frontend/tests only; legacy Lean remains UNCHECKED |

The design does not require inheritance, unrestricted implicit conversion, textual macros or unspecified evaluation order to be broadly useful. It does require real libraries, ABI support, operating-system bindings and tooling. Those are engineering deliverables, not facts implied by Turing completeness or by compiling through C++.

## 5. Implemented grammar

This grammar describes the accepted native profile, not future extensions. ASCII identifiers start with a letter or underscore; comments start with `//`. Integer literals are decimal, even with leading zeros. Floating literals contain a decimal point or exponent. Keywords and built-in names cannot be redefined. Whitespace has no semantic role.

```ebnf
program = declaration* ;
declaration = record | enum | function | family | wire ;
record = "struct" id "{" (id ":" type ";")+ "}" ;
enum = "enum" id "{" (id ";")+ "}" ;
function = "fn" id ("[" id ":nat]")? "(" params? ")"
           ("->" type)? block ;
params = id ":" type ("," id ":" type)* ;
family = "family" id "=" id "[" integer ".." integer "];" ;
wire = "derive wire for" id ";" ;
type = scalar | record_id | enum_id
     | ("ro" | "rw") "<" value_type ">[" extent "]@host" ;
extent = earlier_usize_parameter | integer ;
block = "{" statement* "}" ;
statement = ("let" | "reg") id (":" type)? "=" expression ";"
          | "let" id (":usize")? "= compact" id "for" id "in"
            expression "where" expression "yield" expression ";"
          | place "=" expression ";"
          | "if" expression block ("else" block)?
          | "while" expression block
          | "for" id "in" expression ".." expression block
          | "each" id "in" expression block
          | "return" expression? ";"
          | call ";" ;
place = id | place "." id | id "[" expression "]" ;
expression = literal | place | enum_id "." variant | call
           | unary expression | expression binary expression
           | "(" expression ")" ;
call = id "(" (expression ("," expression)*)? ")" ;
```

Precedence is postfix, unary, multiplicative, additive, comparisons, bitwise and/xor/or, logical and/or as encoded by `PREC` in the shipped parser. Parenthesize mixed comparisons and bitwise expressions for clarity. There is no operator overloading. The parser is authoritative if this abbreviated grammar omits a lexical detail. Native constraints in the following sections further restrict syntactically valid forms.

There are no methods, modules/imports, string literals, sum payloads, match statements, aliases, anonymous functions, local arrays, device statements, exception handling, or pointer expressions in this profile. Those forms are not silently translated to C++.

## 6. Values, control flow and calling

Scalars are `bool`, `u8`, `u16`, `u32`, `u64`, `usize`, `i32`, `i64`, `f32`, and `f64`. `usize` is 64-bit in the supported target profile. Omitted return type is void. Literals use their expected scalar type where one is available; otherwise integer and floating literals default to `u64` and `f64`. Explicit conversion is written as a type call. Float-to-integer conversions are rejected in this version. Integer conversions trap outside the destination range.

Record fields are previously declared value types, excluding void and borrowed views. Records are nonempty; recursive value layouts and enum-valued fields are outside this ABI subset. Construction supplies fields in declaration order. Record assignment is a value copy and can cost multiple machine instructions: there is no claim that copying a large record is free. Enums have distinct zero-based 32-bit tags, and direct enum parameters receive a validity guard.

Parameters, `let` bindings and loop indices are immutable. `reg` declares mutable local storage, initialized at its declaration. Mutating fields of a reg record is allowed. Locals cannot shadow any visible name. Functions may call each other or recurse, but all nonvoid paths must return according to the conservative structural checker. Recursive calls and while loops may diverge and may exhaust the machine stack; neither termination nor a stack bound is proved.

`for i in lo..hi` evaluates lo, then hi, once, then visits increasing indices below hi. If lo >= hi, it is empty. `each i in n` abbreviates a zero lower bound. There is no implicit parallelism, no break/continue, and no loop fusion at the source level. The backend may transform loops while respecting the selected C++ compilation profile.

Logical operators short-circuit. Assignment evaluates its right-hand side before computing the destination place, following the generated C++20 sequencing rule. The native profile rejects writing function calls nested inside other expressions, including collector predicates/projections. Bind such calls separately. Read-only expression evaluation can be reordered by C++; the profile does not expose floating exception flags, trap-site identity, or signal-handler observation as semantic outputs. Traps terminate the process; a partially completed sequence of earlier statements is not rolled back. This restricted observation model matters when the host examines shared memory after a crash.

## 7. Memory and the foreign boundary

A native array parameter is a borrowed view whose type states element type, mutability, extent and host placement. An extent must be an integer literal or an earlier immutable `usize` parameter. Index expressions have type `usize`. The compiler emits a checked access for ordinary indexing. Read-only views can alias. Every mutable view must be disjoint from all other view parameters in the same call, including read-only views. A function can pass an rw view as ro, but cannot create an additional mutable alias by passing the same source view twice.

The entry wrapper checks nonempty pointers for non-nullness, alignment, representable byte length and nonwrapping address intervals. It checks overlapping intervals involving a mutable view. Empty views may be null. These are runtime checks; the current checker does not prove all interprocedural alias facts statically and does not automatically add C++ restrict promises.

The foreign caller must supply genuinely live, initialized, correctly typed storage for the entire stated extent and lifetime of the call. It must prevent concurrent external writes or conflicting access. Address arithmetic cannot establish allocation provenance, initialization, lifetime, or an external thread's behavior. A malicious or erroneous foreign caller can violate the contract even when numerical entry checks pass. This is not unconditional memory safety for arbitrary C callers.

There are no owning allocations, free operations, view locals, escaped views or closures in Native 0.2. As a result, it cannot currently construct a general graph, own a hash table, manage an OS resource, or implement a native CPU/GPU transfer lifecycle. Those are deliberate omissions, not hidden runtime features.

## 8. Arithmetic and failure semantics

Ordinary integer addition, subtraction and multiplication check representability and abort on overflow. Division and remainder reject zero and signed minimum divided by minus one. Unsigned `add_wrap`, `sub_wrap` and `mul_wrap` are modular in the operand width. `shl_wrap` and `shr` require a shift count below the width. Unsigned bitwise and/or/xor/not are supported. `min` and `max` currently accept integers only, avoiding an implicit choice about floating NaNs.

Floating expressions use the named IEEE storage formats as provided by the target C++ implementation. The prescribed flags are `-ffp-contract=off -fno-fast-math`, so contraction and reassociation are not silently authorized. This release has no formal IEEE semantics, NaN-payload theorem, portable dynamic-rounding support, or floating-exception observation contract. Out-of-range floating literals or unsupported target behavior may still be rejected by the C++ compiler after native frontend acceptance. A full build, not a frontend receipt alone, establishes native compilability.

A failed check calls the runtime trap, currently `std::abort`. There is no exception unwinding, implicit catch, allocation, transactional rollback, or automatic resource recovery. The full language uses explicit Result values for expected failures, reserving traps for contract violations. This is a future extension and will require a different metatheory from a pure fixed-arena model.

## 9. The bounded collector

This implemented construct writes a stable compacted prefix:

```cairn
fn compact_even(n:usize, out:rw<u64>[n]@host,
                x:ro<u64>[n]@host) -> usize {
  let used = compact out for i in n
    where (x[i] & 1) == 0 yield x[i];
  return used;
}
```

The target must be a direct rw parameter, and the iteration extent must equal its capacity by the native checker's literal/name test. The predicate is bool and the projection has the output element type. Neither can mention output or call a function whose transitive effects include a write. The binder and result name are fresh. Predicate evaluation occurs once per visited input; projection evaluation occurs only when selected. The unfilled output tail is unchanged. No new array is allocated.

The generator owns a private output cursor. At the start of iteration i, let k be the number emitted. The invariant is `0 <= k <= i <= n`. It holds initially. A rejected input leaves k unchanged. An accepted input occurs only when i<n, so k<=i<n establishes the next store is in bounds. Advancing k and i restores the invariant. On exit k<=n, and induction on the prefix establishes stable selection. The last increment cannot overflow usize because k+1<=n and n is representable.

This is a mathematical argument, not a Lean-checked theorem. The compiler implements the invariant structurally rather than asking a general solver to infer it from arbitrary imperative code. Its generated output store omits a dynamic bound guard; ordinary input reads retain guards for the backend to eliminate. The current implementation of this trusted rule could contain a bug. Finite exhaustive tests and code inspection are evidence, not a substitute for mechanization.

This case motivated the revision empirically: the earlier hand-written cursor form left a bounds check in the optimized loop, while this form produced the same function section and relocation entries as the selected C++ reference. The lesson is to make useful invariants part of an algorithm constructor, not to remove all safety checks globally.

## 10. Static families and wire schemas

A function may have one natural compile-time parameter. A family instantiates a concrete half-open range:

```cairn
fn scale[K:nat](n:usize, out:rw<f32>[n]@host,
                x:ro<f32>[n]@host) {
  each i in n { out[i] = x[i]*f32(K); }
}
family gain = scale[1..257];
```

This produces 256 named functions and typechecks each instantiated AST. It adds no runtime dispatcher. Static parameters are substituted as typed constants, not textual source fragments. The prototype rejects empty/reversed ranges, unresolved templates, name collisions, oversize families and exhausted expansion budgets. A family can still inflate code size and compile time. Redundant specializations should be deduplicated or replaced with runtime parameters when measurements support that choice.

`derive wire for Record;` generates encode, decode and wire-size functions for a record containing only fixed unsigned scalar fields. It first constructs ordinary typed AST operations, then runs the same native checker. The wire order is declaration order, each field is little endian, and padding is omitted. The native record ABI is separate. The included Packet has 36 wire bytes but 40 native bytes on the tested ABI.

For a w-bit unsigned value v, encoding byte j extracts `(v >> 8j) & 255`, and decoding combines `byte_j << 8j` with bitwise or. Distinct shifts occupy disjoint bit ranges; therefore decoding an encoding yields v, and encoding a decoded full-width byte sequence yields the original bytes. Concatenating fields preserves this property. This representation proof is on paper only. It does not prove framing, validation of protocol-specific fields, checksums, authentication or version compatibility, none of which is generated.

## 11. Full-language contracted expansion

The proposed generalization allows library authors to define typed recipes over value types, shapes, record schemas and a restricted AST. A recipe has a fixed meaning, admissible input domain, effect row, resource budget and expansion procedure. An instance may not change the contract it is supposed to implement. Definitions and imported proof/primitive models are pinned by content hash.

Generation is deterministic in explicit inputs. No generator silently reads the filesystem, network, clock or arbitrary compiler environment. Such dependencies must be named and hashed. Expansion has finite resource budgets. Recursion that exhausts the budget yields unknown/rejected generation, not acceptance by fiat. Generated names are hygienic, and every generated node has a path to its authored origin. Native 0.2 only implements closed recipes, not this general recipe API.

Useful library-scale applications include protocol serialization, typed state machines, record projections, structure-of-arrays layouts, container methods, SIMD variants, GPU tile families and fused pipelines. A short declaration can describe considerable repeated machinery. It cannot specify an arbitrary concurrent service's semantics just by naming `server`, nor can a generic recipe infer transaction isolation, error recovery or numerical tolerances from absent requirements.

An efficient full admission architecture has two routes. Known recipes replay a checked semantic theorem instantiated at the actual arguments. New candidate bodies provide a proof/refinement certificate against the unchanged cut. Both must report costs and domain restrictions. Today the closed native generators and C++ compiler are trusted code; no checked theorem authorizes them.

## 12. Full-language ownership and general data structures

The proposed type vocabulary adds `own<T>@region`, read and write borrows with lifetimes, fixed arrays, explicit-capacity vectors, tagged sums, and generational handles. An owner is affine: it may be moved or destroyed once, not duplicated implicitly. Trivially copyable scalar/record values retain value semantics. Deep copies require a named operation and an allocating capability. A vector distinguishes logical length from reserved capacity; a growth operation cannot masquerade as an element store.

Cyclic structures use arena handles rather than cyclic exclusive borrows. A handle carries arena identity, slot and generation. Dereference requires a live generation check and an appropriate lease to the target. Owning an arena allows bulk release only when no borrowed references escape; individual reclamation must invalidate the generation before reuse. This permits graphs and data engines without requiring a tracing collector. It does not by itself solve concurrent reclamation, ABA, or lock-free linearizability.

Resources such as files, sockets and device allocations use the same owner discipline. A proposed explicit cleanup construct may lower deterministic release actions at lexical exits, but its effect receipt must show those actions and the source must authorize them. Cleanup is not free because it is automatic. Asynchronous owners cannot be destroyed while an operation holds their pending lease; draining, cancellation or quarantine is explicit.

The design allows an auditable foreign/target module for behaviors outside the safe core. The module declares preconditions, footprints, invalidation, failure and synchronization. Callers know whether the obligation is mechanically proved, dynamically checked, vendor-assumed or unaudited. An unsafe escape hatch expands capability coverage but never extends a theorem to unverified code.

## 13. Full-language traits, dispatch and errors

Static traits specify operations and laws without forcing runtime indirection. A monomorphic implementation is the default. Dynamic trait objects are explicit fat values with a named dispatch effect and layout. Closures state capture mode and storage location. Passing a closure can allocate only when the API requires an allocating capability or the program expressly chooses a heap environment. Polymorphism must not silently box values.

Tagged sums support Result and Option, with exhaustive pattern matching. Error propagation moves ownership through the selected branch. Public APIs distinguish expected failure from traps and divergence. Destructors/cleanup and foreign exceptions require specified behavior on every path. A C++ wrapper catches exceptions at an explicit boundary or declares termination; an exception must not cross the native C ABI accidentally.

This design can express behavior commonly implemented with inheritance through records, composition and interfaces, but it does not automatically import an existing C++ class ABI, templates, multiple-inheritance layouts or exception runtime. Those require audited shims and platform-specific ABI tests. A future CAIRN package ecosystem is not supplied by the present artifact.

## 14. Full-language CPU concurrency and GPU control

The proposal carries regions and leases into execution hierarchies. CPU tasks can share immutable borrows or receive disjoint mutable pieces. Atomics specify width, address space, memory order and synchronization scope. Locks own protected capabilities; lock acquisition transfers them to a guard, and release returns them. Lock-free algorithms require an additional linearizability/reclamation argument rather than a blanket race-freedom slogan.

GPU placement names host, pinned, device-global, cluster-shared, block-shared, warp and register regions. Execution names device, grid, cluster, block, warp and lane. Collective instructions specify participants, uniformity, alignment and completion. A schedule may pick tiling, layout, vectorization, streams and transfers, but it must preserve the public contract and legal access scopes. A portable meaning need not have a portable optimal schedule.

Async work moves operand leases into a completion ticket. Dependent tickets order operations without a host wait. Only completion restores ordinary access. Failed enqueue, cancellation and device loss need explicit draining or quarantine states. The native prototype has none of these operations. Its no-external-concurrent-mutation precondition must not be read as a proof of this proposed system.

NVIDIA's documented PTX is a virtual ISA and CUDA can already issue its public instructions through inline PTX. AMD exposes documented backend/code-object interfaces. The language cannot create an instruction privilege absent from vendor interfaces. Native instruction scheduling, driver admission, caches, contention and power behavior are not fixed by merely making source costs explicit. A future backend must inspect final code and name the remaining trust/measurement boundary. No GPU throughput or latency measurement is included here.

## 15. Costs and optimization architecture

Native receipts include syntax-level guard-site counts, a conservative call/effect closure, zero heap-allocation constructors and zero synchronization constructors. They are not exact instruction counts, loop trip counts, dynamic costs or physical communication bounds. Record copies, index arithmetic, ordinary loads/stores, stack use and spills still cost work. `abort` and the host environment lie beyond an allocation-free pure computation claim.

The native pipeline is parse -> closed typed expansion -> type/effect checks -> explicit guarded C++ -> optimized object. In particular it uses existing C++ optimization rather than expecting a new verified optimizer to become competitive immediately. This is a pragmatic backend, not proof-carrying native compilation. Eight byte-identical function sections under one pinned compiler/flag profile do not prove every new source program will optimize equally well.

The full design adds resource-cut replacement above a typed IR and target validation below it. The public numerical/effect contract is immutable under search. Each candidate records its semantic evidence, dynamic guard domain, cost vector, compile budget and measured workloads. Search compares full costs, including scratch storage, code size, launch overhead and compilation. A candidate cannot win by hiding checks, changing valid input domains, silently using fast math, or changing denominators after measurement.

The remaining performance research problem is generalizing the collector pattern: find compact algorithm constructors whose invariants are expressive enough for useful code, cheap enough to instantiate, and strong enough to eliminate guards or redundant movement without forcing a bad machine schedule. Capacity-aware GPU reuse remains a separate harder problem. Neither automatic synthesis nor a universal optimality theorem was achieved here.

## 16. Diagnostics and edit context

The implemented protocol is `cairn.diagnostic/1`. An error contains status, stable rule code, message, source line/column when available, and trust=`prototype-not-verified`. Examples include type mismatch, forbidden alias, out-of-profile syntax, mutable capture/order problems, invalid collector capacity, generated-name collision and expansion budget. I/O or resource exhaustion is reported separately from a logical rejection where the CLI can distinguish it. Native compiler errors remain native compiler errors, not fabricated CAIRN proof failures.

Receipts contain source, generated-code and runtime hashes; instantiated families; wire schemas; function count; calls/effects; guard sites; FFI assumptions; target profile; and formal status. A hash establishes identity, not correctness. Generated source locations are incomplete in this prototype, and diagnostics do not promise a minimal conflicting context.

The proposed agent packet contains the changed component, relevant public contracts, affected ownership/resource boundary, permitted proof lemmas, target settings and last counterexample. Everything else can stay out of view only when the checker has enough evidence to validate composition. Changing a contract or recipe invalidates dependent proofs and dispatch admissions. No full-context audit may be claimed from an edit packet that omitted unresolved dependencies.

## 17. Density and evaluation protocol

`tools/density.py` counts the exact plain-text byte-token encoding used by ByT5: UTF-8 bytes mapped to byte IDs, excluding special tokens. It supports optional tiktoken runs when that package and vocabulary are available. They were unavailable here. Byte counts are genuine counts for that language-model encoding but are not evidence about GPT/Claude BPE context size. Whitespace and comments are retained.

The family is compared both to its large generated C++ expansion and to a compact C++20 template implementing the same 256 numerical operations. That template exposes an indexed entry rather than 256 named C entries; the API difference is explicit. The nine algorithm source slices come from complete function definitions, not from bodies with contracts removed. Both sides share the boundary runtime implementation; its size is recorded separately.

The card and generator-contract packet are counted, as is the full compiler implementation for a source-audit denominator. These constructed packets are not a logged fresh-agent edit trial. No comprehension test or frontier-model completion study ran. Native tests include independent Python value oracles, explicit abort cases, exhaustive short collectors, wire encoding comparison, and sanitizers. Performance uses separate objects and no LTO, equal entry guards, fixed floating flags and validated reference outputs before timing. All timings are from the current session on the described shared virtualized CPU.

## 18. Relationship to prior work and proof status

Mojo already provides ownership, traits, parameterization, compile-time evaluation, reflection and CPU/GPU programming. Zig already provides general systems programming with compile-time computation and explicit allocation choices. MLIR's Transform dialect already separates transformation descriptions from payload IR. These are inherited design directions, not CAIRN inventions. C++ templates and standard algorithms also encode compact reusable behavior. [R1-R3,R6]

The version-0.1 resource-cut design and its cited GPU/verification work remain in `legacy/CAIRN_0_1_SPEC.md`. The old Lean zip is retained unchanged, marked UNCHECKED. No `lake build` or axiom audit succeeded in this revision, and no new collector/wire theorem has been machine-checked. The native compiler, runtime, C++ optimizer, ABI and hardware are outside that old attempted formalization. Finite tests establish the reported cases only.

The defensible contribution of this revision is an executable experiment: contracted source forms can improve readability and code size, and a bounded collector can express an invariant the ordinary guarded loop failed to communicate to the selected optimizer. It is not an established novelty claim against the full state of the art.

## 19. Completion criteria for the broad language

Before describing CAIRN as comparable in implemented breadth to C++, require a native module/package system; separate compilation and debug information; owning containers, sums, traits and closures; recoverable errors and correct cleanup; tested platform ABI and OS libraries; a freestanding profile; CPU atomic/locking semantics; a working GPU backend with asynchronous lifetimes; and realistic applications in at least a service, storage engine, simulator, embedded target and GPU runtime.

Before claiming ordinary C++ performance with ease, require a preregistered task suite, equal semantics and safety boundaries, strong independently tuned baselines, controlled machines and agents given equal total budgets. Report failures and code-size/compile-time costs. Before claiming density, count real tokenizer transcripts, cold and warm contexts, imported semantic content and repair attempts. Before claiming proof, run the pinned kernel build, audit axioms and connect the actual frontend/backend to the proved semantics.

This release completes the smaller native artifact and broadens the design, not these entire acceptance gates.

## 20. Primary references

Accessed 17 September 2026. These references support prior-art and toolchain facts, not this release's performance numbers.

[R1] Mojo Manual. https://mojolang.org/docs/manual/

[R2] Zig Language Reference, master documentation. https://ziglang.org/documentation/master/

[R3] MLIR Transform Dialect. https://mlir.llvm.org/docs/Dialects/Transform/

[R4] Clang Users Manual, floating-point options. https://clang.llvm.org/docs/UsersManual.html

[R5] Xue et al. ByT5: Towards a Token-Free Future with Pre-trained Byte-to-Byte Models. arXiv:2105.13626. https://arxiv.org/abs/2105.13626

[R6] ISO C++ working draft, templates. https://eel.is/c++draft/temp

[R7] NVIDIA PTX ISA documentation. https://docs.nvidia.com/cuda/parallel-thread-execution/index.html

[R8] LLVM AMDGPU usage. https://llvm.org/docs/AMDGPUUsage.html
