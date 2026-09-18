# CAIRN 0.4: host-bound sketches and scalar semantic feedback

17 September 2026. Native profile `cairn-native/0.4.0`; scalar model
`cairn-scalar-bv/1`; named-choice transport `cairn.choices/1`.

## 1. Status and the design decision

This release adds a Python sketch API, strict named-expression replies, exact-width
scalar symbolic execution, solver-backed equivalence checks, counterexample reuse,
and same-contract teaching material to the existing native compiler. It does not
change the meaning of native source constructs or add a general Python frontend.
The native grammar and semantics are reproduced in Appendix A.

The governing choice is to compress repeated decisions, not meaningful names or
unstated assumptions. A host fixes a component's public task and edit boundary;
an agent proposes choices within it. The existing observable resource-cut design
remains the broader architecture. A scalar reference is a concrete restricted
behavior boundary, not a new general resource-optimality theorem.

The language should leverage familiar source patterns and a familiar host API,
then make semantic differences executable. This is a design hypothesis about AI
proficiency. No weights were trained, model inference endpoint invoked, or fresh
model evaluated in this release. The prototype remains a restricted CPU library
compiler, not a C++-breadth language or native GPU implementation.

## 2. Coverage and trust

| Layer | Implemented evidence | Exclusions |
|---|---|---|
| Native parser/type/effect checker | Tested Python implementation | Not formally verified |
| C++20 backend | Clang/GCC native tests and object comparisons | No lowering/refinement proof |
| Sketch admission | Pinned source surgery and whole-module checks | No authentication or sandbox |
| Scalar semantics | Integer/Boolean branches and acyclic scalar calls | Memory, loops, recursion, floats, records, void returns |
| Equivalence | Z3 decision over declared-width inputs and fixed domain | Translator and solver trusted; no Lean certificate |
| Counterexamples | Independent concrete replay; curriculum also native-instrumented | Interactive checks do not automatically replay native code |
| Training artifacts | Checked preferences and executed repair fixtures | No model training or measured model proficiency |
| Legacy Lean | Old project retained unchanged, UNCHECKED | No successful `lake build` or axiom audit |

Z3 was available as an installed shared library, version 4.13.3.0. The bridge uses
its C API through Python ctypes; no Python z3 package is required. An unavailable
library or undecided obligation produces unknown, never success. The library is
not vendored. A different version must be recorded in newly generated receipts.

## 3. A sketch is a sealed host object, not a new source language

`Sketch(source, symbol, task=..., semantic=..., include=...)` constructs an ordinary
0.3-compatible `EditSession`. The original source must typecheck. The optional
`ScalarContract(reference, symbol, assume='true', allow_reference_traps=False)`
is supplied by the host. The agent cannot replace it in its choice response.

`hole(name, original, occurrence=None)` selects one exact expression range from
the compiler's site inspection. Repeated identical text is ambiguous unless an
occurrence is explicitly supplied. Names must be identifiers; at most 16 holes
are allowed. Overlapping and nested holes are rejected. Sealing hashes the edit
session, slot map and scalar contract. Any accidental change to those inputs or
the compiler's Python files requires a fresh sketch.

A slot is an editing aid, not a persistent AST identity. Its offsets index Unicode
code points in the exact original source. Emitted bytes outside the selected
ranges remain the original bytes after UTF-8 encoding. No formatter overwrites
comments. Conventional CAIRN code remains the durable, readable program artifact.

## 4. Choice admission and context transport

`fill_json` accepts a strict JSON object mapping every slot name to one expression
string. It rejects duplicate fields, nonfinite values, extra/missing slots and
non-string values. Transport text is bounded at 128,000 UTF-8 bytes; the combined
expression strings at 64,000. Each expression must parse to end of input.

`fill(**choices)` performs substitutions from the last range backwards and wraps
each choice in parentheses. The complete resulting function body goes through the
existing session checker. The complete module is then recompiled. Function
signature, declaration set, effect ceiling and visible dependencies remain fixed;
an unchanged caller may not acquire new effects. The result is `typed`, not a
behavioral or native proof. Multiple coordinated nonoverlapping replacements are
one admission transaction, not a sequence of temporarily invalid programs.

The model packet retains the bidirectional static call-graph source closure, all
record/enum definitions, selected language cards, task, local bindings and slot
expected types. When a scalar contract is attached, its full reference source,
symbol, fixed input domain and trap policy are also disclosed read-only. The
packet says explicitly that the builder does not insert a runtime precondition
guard. The model must see the meaning it is asked to preserve; disclosure does
not grant permission to modify that meaning. It does not promise minimum context.
It removes duplicated source signatures and protocol hashes from the model's reply. The host retains hashes
and contract identity. This is a local host-binding rule, not cryptographic
provenance. A network service must add authenticated session routing and isolation.

Python is the orchestration and metaprogramming API. There is no `eval` of a model
reply. Executing arbitrary generated Python would exceed this data-only boundary
and requires a separate security design. The current process limits are not a
sandbox. The adapter interface from 0.3 remains available for full-body edits;
the new Python API can be integrated with a caller-selected adapter without
shipping a model endpoint or credentials.

## 5. Scalar calculus and observations

Values are Booleans and the native fixed-width integer types, including 64-bit
`usize`. The selected function and its transitively reached scalar callees may
use immutable/mutable locals, assignment, conditionals, early returns, pure scalar
expressions and acyclic calls. All value-returning paths must return according to
the native checker. Static generation is rejected at this interface. Unrelated
functions do not gain verification from a selected function's result.

An evaluation produces either `return(v)` or an undifferentiated `abort`. Memory,
I/O, timing, stack usage, source trap identity and signal-handler observations are
not modeled. This observation set is suitable only for the stated pure fragment.
It is not a semantics of arbitrary failing C++ programs.

The symbolic evaluator represents an expression by `(defined, value)` and an
environment of typed bit-vector/Boolean terms. Paths conjoin definedness of every
executed statement. A return contributes its value only on its successful path.
The function's definedness is the disjunction of successful return paths. Branch
conditions partition paths; lexical locals leave scope while assignments to outer
mutable locals remain visible. Calls substitute arguments and inline only along
an acyclic call graph. Shared typed SSA definitions limit duplicated formulas.

For `a && b`, the definedness is `d_a && (v_a => d_b)`; for `a || b`, it is
`d_a && (!v_a => d_b)`. Eagerly requiring both operands defined would be wrong.
Ordinary integer addition/subtraction/multiplication compare their double-width
exact result with the sign- or zero-extension of the truncated result. Equality
means the operation fits; inequality means abort. Wrapping primitives use only
the fixed-width result. Signed division/remainder use truncation toward zero,
reject zero divisor and signed minimum divided by -1. This is not Python `%`.

Casts compare numerical values after suitable sign/zero extension to a width
larger than source and target. Shift counts must be below the operand width.
Boolean ordering follows the native false-before-true rule. Floating arithmetic
is explicitly unsupported, not approximated by reals or by integers.

Bounds: each scalar source is at most 64,000 UTF-8 bytes; path budget 256; symbolic
visit/definition budget 12,000; call depth 24; SMT text at most 8 MB; per-query
timeout 1..30,000 milliseconds. These are rejection/unknown limits, not supported
program resource theorems. Path explosion remains possible within these limits.

## 6. Immutable domains and equivalence obligations

Let `D` be the host precondition, `R` the fixed reference and `C` the candidate.
Their signatures, including parameter names/types and return type, must match.
The precondition is parsed as exactly one Boolean expression over the reference's
parameters and disclosed reference functions. It is never model-editable text.

Admission first establishes that D does not abort and admits at least one input.
The default also requires R to return on every input satisfying D. Then it asks
whether the following formula is satisfiable:

`D && ((d_R != d_C) || (d_R && d_C && v_R != v_C))`.

Unsatisfiable yields `smt-equivalent` in this source model. Satisfiable yields a
candidate distinguishing input. The independent Python arithmetic interpreter
replays reference and candidate before reporting semantic rejection. A replay
disagreement yields unknown. An explicit partial-reference policy drops only the
reference-totality obligation: candidate/reference return-versus-abort outcomes
must still agree on the complete domain. No implementation is admitted vacuously
by changing D to false or by making a total task abort.

The receipt binds reference and candidate source digests, symbol, domain, trap
policy, translator implementation digest, profile, solver version, query hashes
and individual decision outcomes. Optional logs retain standalone SMT-LIB input.
These are reproducible obligations, not proof objects accepted by Lean. The
translator, parser/typechecker and solver remain trusted. An erroneous reference
can still specify the wrong human task; equivalence cannot repair that error.

A nontrivial D is a caller obligation, not an automatically generated runtime
guard. The ordinary native builder neither consumes an SMT receipt to remove
checks nor inserts D at an entry point. No equivalence claim holds outside D.
The average demonstration and all forty corpus tasks use D=true, avoiding a
hidden restriction of their declared-width input domains.

## 7. Counterexample-guided finite search

`solve_finite(sketch, choices, limit, timeout_ms, use_counterexample_cache)` explores
an explicitly supplied ordered Cartesian product of expressions. The maximum
product is 4,096. No language model is invoked by this function and no complete
or optimal grammar is inferred. Every candidate first undergoes normal edit
admission. Existing distinguishing inputs may reject it through concrete replay.
Only a fresh full-width solver check can accept a candidate.

Conditional pruning argument: if a stored input satisfies D and R differs from C
there, C cannot be universally equivalent under D. Thus replay can remove only an
invalid candidate, assuming correct replay. Final acceptance does not depend on
cache completeness because the universal query is still required. These are
paper arguments and implementation invariants, not Lean theorems. The cache may
save no calls when counterexamples are not shared among later candidates.

The result records every candidate, stage and outcome. `solver_calls` is the
historical field name for complete semantic-checker invocations, each possibly
containing several Z3 calls; `smt_queries` counts the individual obligations.
Unknown, unavailable solver, unsupported syntax and exhausted budgets never
become positive examples or correctness claims.

## 8. Teaching data and the acceptance hierarchy

The new corpus contains same-signature, same-domain good/bad implementations.
Both must typecheck. Good labels require a decided equivalence obligation; bad
labels require a replayed counterexample. Every corpus counterexample is also
checked under instrumented Clang and GCC. The production abort runtime is not
changed: a test-only runtime converts traps to caught exceptions so many edge
cases can be observed in one process. This does not verify the process-abort path.

Protocol-aligned lessons use the exact named-choice parser and sketch admission.
The supervised export makes the bad proposal and diagnostic user context, and
only the good choice an assistant target. The preference export labels good and
bad whole implementations under the same contract. Width variants remain within
an algorithm family; splitting is by family, not by spelling. All answers ship
for auditing, so evaluation files are not confidential held-out benchmarks.

Acceptance levels are parsed, typed, native-built, passed-finite-tests,
smt-equivalent under the stated profile, Lean-verified under a named formal
model, and benchmarked on a named workload. This release implements all except
Lean verification; it reruns code-section comparisons but performs no new native
timing benchmark. These levels are not a single ascending score: SMT equivalence
can coexist with an unverified backend and poor performance.

## 9. Cost, density and learning claims

The new sketch/solver tools execute in the host authoring/checking process, not
inside generated native functions. Native arithmetic/checking rules are unchanged.
That fact is not a guarantee that an agent chooses a fast body. Search cost,
solver cost, compile cost and inference cost remain separate from native runtime.

Density reports retain task, all visible source, selected language cards, slot
metadata and complete replies. Identity hashes omitted from model transport stay
in the host manifest. The measured comparison is a constructed warm-protocol
comparison, not an LLM interaction. A model asked to author the Python host or
reference must be charged those inputs/outputs too. Small functions can be more
expensive with packets than with plain source. The installed tokenizer fallback
is exact ByT5 byte mapping, not frontier-model BPE. No 100x total-context claim is
supported. No model training, inference trial or language-learning ablation ran.

## 10. Research boundary and next acceptance gate

Solver-aided host metaprogramming, counterexample-guided synthesis, type-constrained
generation and semantic contrast training all have prior work. The contribution
here is an implemented combination with pinned editing boundaries, explicit abort
semantics, fail-closed labels and protocol-aligned artifacts. It is not an
exclusive novelty or state-of-the-art model-performance claim. Primary sources
and review depth appear in `design/RESEARCH_0_4.md` and the report.

A convincing AI advantage requires an equal-budget fresh-model experiment against
C++/Rust with equally capable editing and verification tools, independent tasks,
real model-token accounting, and correctness-plus-native-performance reporting.
The immediate formal task is to connect this exact parser/bit-vector translator
to a Lean proof and then to native lowering. The broader owning-runtime, CPU/GPU
concurrency and resource-optimality designs remain unimplemented proposals from
0.1/0.2, not consequences of the scalar result.

## Appendix A. Complete inherited native source profile

The following native rules are unchanged from 0.3, except the compiler profile
identifier is now 0.4. Historical phrases such as "this revision" within this
appendix refer to the introduction of the inherited mechanism, not a new 0.4
implementation claim. The original 0.3 specification is also retained.

### A.1. Canonical surface and compatibility

Use descriptive ASCII identifiers, braces, semicolons and `//` comments. Whitespace is insignificant. Non-ASCII text is allowed in comments, not identifiers. There is no operator overloading, implicit user conversion, implicit tail return, or name shadowing.

Canonical authored code uses `let` for immutable locals and `let mut` for mutable locals. Parameters remain immutable. `mut` is now reserved and cannot be an identifier. `reg` remains an accepted compatibility spelling for `let mut`, and `each i in n` remains an accepted spelling for `for i in 0..n`. Their meanings are identical within the native profile. No extra runtime operation is attached to a spelling.

The `project` tool prints a canonical AST view with explicit parentheses, `let mut`, and full `for` ranges. It is not a comment-preserving formatter: comments are omitted. It never writes over the authored file. Session edits splice the original source and preserve everything outside the authorized range, including comments. The canonical view must not silently replace source containing human review notes.

#### Native grammar

```ebnf
program      = declaration* ;
declaration  = record | enum | function | family | wire ;
record       = "struct" id "{" (id ":" type ";")+ "}" ;
enum         = "enum" id "{" (id ";")+ "}" ;
function     = "fn" id ("[" id ":nat]")? "(" params? ")"
               ("->" type)? block ;
params       = id ":" type ("," id ":" type)* ;
family       = "family" id "=" id "[" integer ".." integer "];" ;
wire         = "derive wire for" id ";" ;
type         = scalar | record_id | enum_id
             | ("ro" | "rw") "<" value_type ">[" extent "]@host" ;
extent       = earlier_usize_parameter | integer ;
block        = "{" statement* "}" ;
statement    = ("let" "mut"? | "reg") id (":" type)? "=" expression ";"
             | "let" id (":usize")? "= compact" id "for" id "in"
               expression "where" expression "yield" expression ";"
             | place "=" expression ";"
             | "if" expression block ("else" block)?
             | "while" expression block
             | "for" id "in" expression ".." expression block
             | "each" id "in" expression block
             | "return" expression? ";" | call ";" ;
place        = id | place "." id | id "[" expression "]" ;
expression   = literal | place | enum_id "." variant | call
             | unary expression | expression binary expression
             | "(" expression ")" ;
call         = id "(" (expression ("," expression)*)? ")" ;
```

Static and semantic restrictions below further constrain the grammar. `PREC` in the shipped parser is authoritative for precedence. Parenthesize mixed comparisons and bitwise operations. Decimal literals are decimal even with leading zeros. Floating literals contain a decimal point or exponent. Reserved keywords and primitive/type names cannot be redeclared. Source is limited to 2,000,000 UTF-8 bytes, an individual family to 1,024 instances, expanded functions to 2,048, and checked AST visits to 200,000.

### A.2. Values, memory and evaluation

Scalars are `bool`, `u8/u16/u32/u64`, `usize`, `i32/i64`, and `f32/f64`. The tested native ABI is 64-bit Linux x86-64; `usize` is 64-bit. Nonvoid functions must return on every structurally required path. `while` and recursion may diverge. There is no termination or stack-bound proof.

Records are nonempty value types with nonrecursive, previously declared fields. Fields cannot be borrowed views or void. The current record ABI subset does not accept enum-valued fields. Construct positionally, for example `Pair(a,b)`. A record copy can cost multiple instructions. Enums have distinct zero-based 32-bit tags; direct enum arguments have entry validity checks. There are no payload variants or pattern matching.

A borrowed array parameter has the form `x:ro<u64>[n]@host` or `out:rw<u64>[n]@host`. Its extent is a literal or an earlier immutable `usize` parameter. Indexes are `usize`. Read-only views may alias; each mutable view must be disjoint from every other view in a call. The foreign caller must provide live, initialized, correctly typed storage and prevent conflicting concurrent access. Entry checks enforce numerical null/alignment/length/interval/overlap conditions, not provenance or lifetime. An empty view may be null. The prototype does not create local view aliases or return views.

Ordinary integer `+`, `-`, and `*` abort on overflow in every build. Division and remainder reject zero and signed minimum divided by minus one. Unsigned `add_wrap`, `sub_wrap`, and `mul_wrap` have modular semantics. `shl_wrap` and `shr` require a shift count below the operand width. Bitwise operations require unsigned types; `min` and `max` are integer-only. Conversions are explicit type calls. Integer narrowing checks range; float-to-integer conversion is unsupported. Literals follow an expected type when one is available and otherwise default to `u64` or `f64`.

Prescribed native flags include `-ffp-contract=off -fno-fast-math`. No implicit reassociation or FMA is authorized. This is not a complete mechanized IEEE model. Floating flags, NaN payloads and signal-handler observation are not exposed by the restricted observation model. A failed check aborts; it does not roll back earlier writes or produce a recoverable Result.

A `for` evaluates its bounds once, in order, and visits the half-open range. Empty and reversed ranges execute zero iterations. Logical operators short-circuit. Assignment evaluates its right-hand side before its destination according to generated C++20 sequencing. Writing calls nested within another expression are rejected; bind them at statement level. A loop or view never implies parallel execution or heap allocation.

### A.3. Contracted operations

The bounded collector writes a stable selected prefix and returns its length:

```cairn
fn select_gt(n:usize, out:rw<u64>[n]@host,
             x:ro<u64>[n]@host, threshold:u64) -> usize {
  let used = compact out for i in n
    where x[i] > threshold yield x[i];
  return used;
}
```

Its output is a direct `rw` parameter. Iteration extent must match output capacity under the current name/literal check. Predicate and projection cannot read the output or call a writing function. Each selected input emits once; the unfilled tail is unchanged. The private cursor satisfies `used <= i < n` before each output store, allowing that generated store to omit a dynamic bound guard. This invariant remains a paper argument and a trusted compiler rule, not a Lean theorem.

A function family instantiates one static natural parameter over a finite range, typechecking each generated function. `derive wire` emits fixed-width unsigned, little-endian, declaration-order, no-padding codecs. Neither operation infers application-specific correctness, protocol validation or a good specialization budget. Full contracts are in `GENERATOR_CONTRACTS.md`. Generated entries and template bodies cannot be session edit targets in 0.3.

### A.4. Interprocedural effects

A function's effect row contains `read:parameter`, `write:parameter`, `trap`, `ffi_precondition` and `diverge` as applicable. Rows are conservative, syntactic summaries. They do not state a function's output, the number of accesses, input-dependent trapping conditions, or elapsed time.

The checker now substitutes a callee's view parameter names with the actual caller arguments. For a call `copy(n, destination, input)` whose callee parameters are `out` and `src`, its footprint is `write:destination` and `read:input`, not `write:out` and `read:src`.

Let `L_f` be the local effects and `rho_c` the formal-to-actual view mapping of a call. The checker computes the least fixed point of:

`E_f = L_f union recursive_divergence_f union UNION_(c: f calls g) rho_c(E_g)`.

It marks syntactic recursion cycles as potentially divergent and propagates those effects. Every update only adds elements from a finite universe of parameter footprints and global effect labels. A changed sweep adds at least one new element, so a bound based on that universe suffices. A bound based only on the number of functions does not: a self-call can permute several array arguments and need several propagation rounds. Regression tests cover these permutations. This is an implementation argument, not a machine-checked fixed-point proof.

This fixes a real 0.2 receipt bug. It matters for edit admission: an agent must not redirect a helper's access to another buffer while the caller's receipt still appears unchanged.

