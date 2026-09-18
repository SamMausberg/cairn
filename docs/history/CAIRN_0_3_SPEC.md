# CAIRN 0.3: native language and agent interaction specification

17 September 2026. Implemented profile: `cairn-native/0.3.0`.

## 1. Status and design target

CAIRN 0.3 is a runnable, restricted CPU language with a C++20 backend and a compiler-backed editing interface. It is not a complete C++ replacement, a GPU compiler, or a verified compiler. The general-systems architecture in `CAIRN_0_2_SPEC.md`, sections 11 through 15 and 19, remains a proposal. Nothing in the agent interface implements those missing language features. The original Lean project remains unchanged and UNCHECKED.

This revision optimizes the interaction, not just the source string. An agent should reuse familiar programming patterns, see the facts needed for its current decision, receive precise feedback, and change a small authorized region. The host retains the task, original source, effect permissions and tests. A fluent answer has no authority over those inputs.

The primary target for a future model experiment is independently tested task success at a fixed total inference, compiler and execution budget. A secondary metric is total visible tokens per successful task, charging failed attempts as well as successful ones. Source length, compilation rate and generated-code expansion are separate measurements. No pretrained model's weights were changed, and no fresh-model success rate is reported here.

## 2. Implemented and proposed capabilities

Implemented native computation includes scalar values, value records, tag-only enums, explicit control flow, recursion, borrowed host arrays, checked arithmetic, unsigned modular primitives, static function families, wire codecs and a bounded collector. Native output is a library with C-linkage entries, not a general application runtime.

New in this revision: `let mut`; token and expression source ranges; expected-type and lexical-environment inspection; corrected interprocedural memory effects; canonical AST projection; dependency-scoped packets; body/expression edit sessions; whole-candidate filtering; a finite task runner; a provider-neutral adapter loop; and reproducible teaching data.

Not implemented: native GPU execution, CPU concurrency, owning allocations and lifetimes, arbitrary pointers, modules, traits, closures, tagged payload sums, Result values, OS libraries, general typed macros, a verified optimizer, or an LLVM/PTX proof bridge. No token-level constrained decoder or automatic proof search was implemented. The candidate-filter API checks completed expressions.

## 3. Canonical surface and compatibility

Use descriptive ASCII identifiers, braces, semicolons and `//` comments. Whitespace is insignificant. Non-ASCII text is allowed in comments, not identifiers. There is no operator overloading, implicit user conversion, implicit tail return, or name shadowing.

Canonical authored code uses `let` for immutable locals and `let mut` for mutable locals. Parameters remain immutable. `mut` is now reserved and cannot be an identifier. `reg` remains an accepted compatibility spelling for `let mut`, and `each i in n` remains an accepted spelling for `for i in 0..n`. Their meanings are identical within the native profile. No extra runtime operation is attached to a spelling.

The `project` tool prints a canonical AST view with explicit parentheses, `let mut`, and full `for` ranges. It is not a comment-preserving formatter: comments are omitted. It never writes over the authored file. Session edits splice the original source and preserve everything outside the authorized range, including comments. The canonical view must not silently replace source containing human review notes.

### Native grammar

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

## 4. Values, memory and evaluation

Scalars are `bool`, `u8/u16/u32/u64`, `usize`, `i32/i64`, and `f32/f64`. The tested native ABI is 64-bit Linux x86-64; `usize` is 64-bit. Nonvoid functions must return on every structurally required path. `while` and recursion may diverge. There is no termination or stack-bound proof.

Records are nonempty value types with nonrecursive, previously declared fields. Fields cannot be borrowed views or void. The current record ABI subset does not accept enum-valued fields. Construct positionally, for example `Pair(a,b)`. A record copy can cost multiple instructions. Enums have distinct zero-based 32-bit tags; direct enum arguments have entry validity checks. There are no payload variants or pattern matching.

A borrowed array parameter has the form `x:ro<u64>[n]@host` or `out:rw<u64>[n]@host`. Its extent is a literal or an earlier immutable `usize` parameter. Indexes are `usize`. Read-only views may alias; each mutable view must be disjoint from every other view in a call. The foreign caller must provide live, initialized, correctly typed storage and prevent conflicting concurrent access. Entry checks enforce numerical null/alignment/length/interval/overlap conditions, not provenance or lifetime. An empty view may be null. The prototype does not create local view aliases or return views.

Ordinary integer `+`, `-`, and `*` abort on overflow in every build. Division and remainder reject zero and signed minimum divided by minus one. Unsigned `add_wrap`, `sub_wrap`, and `mul_wrap` have modular semantics. `shl_wrap` and `shr` require a shift count below the operand width. Bitwise operations require unsigned types; `min` and `max` are integer-only. Conversions are explicit type calls. Integer narrowing checks range; float-to-integer conversion is unsupported. Literals follow an expected type when one is available and otherwise default to `u64` or `f64`.

Prescribed native flags include `-ffp-contract=off -fno-fast-math`. No implicit reassociation or FMA is authorized. This is not a complete mechanized IEEE model. Floating flags, NaN payloads and signal-handler observation are not exposed by the restricted observation model. A failed check aborts; it does not roll back earlier writes or produce a recoverable Result.

A `for` evaluates its bounds once, in order, and visits the half-open range. Empty and reversed ranges execute zero iterations. Logical operators short-circuit. Assignment evaluates its right-hand side before its destination according to generated C++20 sequencing. Writing calls nested within another expression are rejected; bind them at statement level. A loop or view never implies parallel execution or heap allocation.

## 5. Contracted operations

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

## 6. Interprocedural effects

A function's effect row contains `read:parameter`, `write:parameter`, `trap`, `ffi_precondition` and `diverge` as applicable. Rows are conservative, syntactic summaries. They do not state a function's output, the number of accesses, input-dependent trapping conditions, or elapsed time.

The checker now substitutes a callee's view parameter names with the actual caller arguments. For a call `copy(n, destination, input)` whose callee parameters are `out` and `src`, its footprint is `write:destination` and `read:input`, not `write:out` and `read:src`.

Let `L_f` be the local effects and `rho_c` the formal-to-actual view mapping of a call. The checker computes the least fixed point of:

`E_f = L_f union recursive_divergence_f union UNION_(c: f calls g) rho_c(E_g)`.

It marks syntactic recursion cycles as potentially divergent and propagates those effects. Every update only adds elements from a finite universe of parameter footprints and global effect labels. A changed sweep adds at least one new element, so a bound based on that universe suffices. A bound based only on the number of functions does not: a self-call can permute several array arguments and need several propagation rounds. Regression tests cover these permutations. This is an implementation argument, not a machine-checked fixed-point proof.

This fixes a real 0.2 receipt bug. It matters for edit admission: an agent must not redirect a helper's access to another buffer while the caller's receipt still appears unchanged.

## 7. Context packets and source sites

A host creates `EditSession(source, symbol, contract, include)`. The source must typecheck. `symbol` must name an ordinary authored function. The optional host contract contains a task description and allowed effects; no supplied contract means no behavior may be inferred from the packet. Its default effect ceiling is the baseline's inferred row. An explicit ceiling may permit additional effects only if they are legal for the function's parameter permissions, and must cover the baseline.

The packet includes the target source, signatures, conservative effects, selected language cards, all record/enum declarations, and the complete source for the target's static call-graph connected component in both directions. The host may include extra declared dependencies. Generated dependencies are traced back to their family/template or wire declaration. This is not a smallest-context algorithm. It may include much more than one function and deliberately retains all type declarations.

The bidirectional closure prevents a common omission: presenting callees while hiding callers whose assumptions an edit could affect. An unrelated component can remain out of the normal packet because new calls to it are prohibited until the host refreshes the packet. The full original module is still checked after every edit. This graph discipline does not make the included task meaning complete and does not prove behavior preservation.

Source-site inspection returns the expected type when known, the inferred type, and in-scope bindings with type, mutability and static constant information. Source offsets are zero-based Unicode code-point offsets into the exact original string. Line/column coordinates are one-based. Source and contract digests hash UTF-8 bytes. A site ID is valid only for its pinned source; it is not a persistent symbol ID across revisions.

The session digest binds the original source, selected symbol, complete host contract, visible dependency set and compiler Python files. It is a stale-edit consistency check, not a signature, authorization system or proof. Backend flags are fixed by the host runner rather than carried as model-editable fields. Session identity does not pin the external compiler binary; native results separately record compiler settings and source identity.

## 8. Transactional edits and candidate filtering

The model supplies exactly one `cairn.edit/1` object. A body edit contains `protocol`, `session`, `kind:body`, and `replacement`. An expression edit additionally contains `site` and sets `kind:expr`. Unknown or duplicate JSON fields are rejected. Nonfinite JSON constants are rejected. Replacements are limited to 64,000 UTF-8 bytes.

A body must parse as one block followed by end of input; an expression as one expression followed by end of input. Expression substitution adds parentheses so a change cannot accidentally alter binding in its parent expression. The checker then:

1. Splices only the authorized original-source range.
2. Recompiles the complete module.
3. Verifies the target signature and declaration set are unchanged.
4. Enforces the target's host-owned effect ceiling.
5. Rejects calls to undisclosed dependencies.
6. Rejects expansion of any unchanged function's effect row.

The returned admission state is `typed`, not `correct` or `proved`. The receipt records candidate identity, before/after syntactic check sites and explicit unverified boundaries. It does not claim an equivalence proof, a native build or behavioral test completion. The CLI writes only a new candidate file and refuses to overwrite the original or an existing output.

A candidate-filter call accepts at most 128 completed expressions for a known site and applies this same admission process to each. It does not return a complete grammar prefix automaton, modify model logits, generate candidates, or guarantee an accepted candidate exists. Invalid syntax is not silently translated from Rust, Python or C++.

## 9. Diagnostics and acceptance levels

`cairn.diagnostic/2` contains a stable code, rejected status, source location when known and the trust label `prototype-not-verified`. Type mismatches include expected and actual types. Unknown variables include the lexical names available at the failure. The agent layer adds a local explanation and a nonautomatic repair hint. Resource limits and unavailable external tools are distinguished from proved failures where possible.

Hints never authorize changing the public signature, permissions or task to make an error disappear. A model that sees `E-WRITE-LEASE` must not turn `ro` into `rw`. A stale-session rejection requires refreshing source context, not guessing a hash. The system returns a first witness, not a guaranteed minimum error core.

Acceptance levels remain separate: parsed; typed; native-built; passed-finite-tests; proved under a named formal model; benchmarked under a named workload and environment. Only the first four are implemented in this native toolchain. Test failure may follow type acceptance. An effect row that permits `trap` does not permit violating the task's valid-input contract, although only tests currently check the supplied behavior. No status upgrades itself through a language-model explanation.

## 10. Task execution and adapter loop

`cairn.task/1` is a host-owned JSON object with a symbol and finite cases. Every nonvoid case supplies an expected return. Every mutable array supplies its full expected post-state, including untouched tails. The runner supports numeric and bool scalar/view signatures, not arbitrary record/enum or asynchronous APIs. Integer inputs must fit declared widths. Floating test inputs and expectations must be finite and representable; a nonfinite actual output is a test failure with an explicit diagnostic value.

The runner compiles with a configured local C++ compiler and executes in a child process with time, memory and core-dump limits. A timeout is unknown, not success. These limits are not a security sandbox. The compiler, runtime and foreign memory model remain trusted. Use OS-level isolation for hostile adapters, toolchains or candidate execution.

`agent_loop.py` accepts an explicit user-selected adapter argv. It contains no model endpoint, credentials or implicit network call. The adapter receives a packet and prior feedback, then returns one edit request. The loop allows a bounded number of attempts. Public examples may produce repair feedback; reserved cases run once after public examples pass and are not returned to the adapter for more repairs. Full test secrecy requires external workspace isolation. Every request, reply and verdict is retained, including repeated history for accounting.

The shipped adapter is a hard-coded three-step demonstration. It first returns a type error, then a type-correct behavioral error, then a tested body. Its result is marked `scripted-fixture`, never a model score. Even an external adapter label does not independently authenticate which model was used.

## 11. Language-learning material

The eight cards cover base syntax, integer differences, views, the collector, calls, floating point, records and generators. They explicitly distinguish familiar syntax from differences that matter at boundaries: fixed-width checked arithmetic, explicit returns, immutable parameters, disjoint mutable views, and no hidden allocation or parallelism. Packets select cards from their actual included source; the full card remains available for unfamiliar features.

The curriculum contains 64 authored programs in eight algorithm families. Forty examples from five families are teaching data, and 24 examples from three other families form a proposed family-disjoint evaluation split. Many variants only rename variables or change constants. Eight is the diversity count, not 64 independent algorithms. All answers and oracles are shipped for reproducibility and are therefore not intrinsically secret test data.

Independent Python arithmetic/list definitions produce 1,016 finite native test cases. Twelve rejected/accepted language pairs illustrate static rules. These pairs are not automatically authorized semantic repairs; some accepted siblings have a different API or meaning and are explicitly labeled. They must not be used to reward changing a task. Eight separate hand-authored, type-correct behavioral mutations are rejected by native task tests. This is a targeted test of the oracles, not an unbiased mutation score.

No fine-tuning, preference optimization, reinforcement learning, fresh-model evaluation or compiler-feedback ablation was run. A JSONL teaching corpus is a training artifact, not a trained model.

## 12. Measurements, limitations and future acceptance

Fresh measurements live in `results/`. Historical 0.2 results live under `legacy/v0_2_results/`. Current tests include frontend/session/task-runner regressions, 267 identity expression substitutions, both-compiler native suites, exhaustive short collectors, codec oracles, sanitizers, the curriculum and the scripted repair demonstration. Finite tests do not prove universal correctness.

Token counts use the exact plain ByT5 UTF-8 byte mapping, excluding special tokens. The requested frontier BPE package/vocabulary could not be obtained in this environment. All packet metadata, cards and source are counted. Packets can be larger than small source files; the canonical view can also be longer because it makes syntax explicit. No universal 100x compression, frontier-context reduction, or actual safe-edit-success result is claimed.

A new native object comparison still finds eight of nine selected function sections equal to the C++ references, including relocation entries. The exception is binary search. No new timing or GPU benchmark was run. Effect/inspection tooling does not enter the generated runtime, but arbitrary admitted bodies may still be slower. Resource cuts, guard counts and tests are not a latency guarantee.

A credible next model study needs independently authored tasks, real model-specific tokenizer accounting, equal-budget C++/Rust baselines with comparable editor tools, family/module-separated held-out data and preregistered ablations. Measure first-pass correctness, post-repair correctness, regression rate, dependency mistakes, total tokens and checker time, and native performance. Only such results can support the claim that an AI is particularly good at this language.

Primary research and its limits are recorded in `design/RESEARCH.md`. The original broad language proposal and uncompiled Lean work remain distinct from this implemented native/agent profile.
