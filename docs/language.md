# CAIRN Native 0.5

This is the implemented CPU source profile, not the broader systems-language proposal. The full inherited rules are in history/CAIRN_0_4_SPEC.md. The changes below override its syntax and tooling descriptions, not its arithmetic or memory model.

## Compact source with a single meaning

A nonvoid function with an explicit return type can use an expression body:

```cairn
fn increment(x:u64) -> u64 = add_wrap(x, 1);
```

It elaborates to `fn increment(x:u64) -> u64 { return add_wrap(x, 1); }`. The same type/effect checks, guard insertion, scalar translation and C++ emission apply. There is no implicit closure, dynamic dispatch, extra call, or weaker overflow rule. A void expression body is rejected.

Host placement is the default for borrowed views in this CPU-only profile: `ro<u64>[n]` equals `ro<u64>[n]@host`, and likewise for `rw`. This does not infer a device, transfer, allocation, layout conversion or parallel schedule. Any future multi-target profile must preserve explicit address-space meaning rather than silently changing this default.

`else if` is accepted and elaborated to an ordinary nested conditional. Canonical AST projection may still print the longer nested form. It is an inspection view, not a comment-preserving source formatter.

Grammar additions:

```ebnf
function = "fn" id static_params? "(" params? ")" ("->" type)?
           (block | "=" expression ";") ;
view     = ("ro" | "rw") "<" value_type ">[" extent "]" ("@host")? ;
if       = "if" expression block ("else" (if | block))? ;
```

The expression-body alternative requires a declared nonvoid return type. Static families keep their prior restrictions. The compiler rejects unknown source features rather than passing arbitrary text through to C++.

## Values and effects

Scalars are bool, u8/u16/u32/u64, usize, i32/i64, f32/f64. The tested ABI uses 64-bit usize. Types are explicit at interfaces; locals can be inferred. Parameters and `let` locals are immutable. `let mut` allows local mutation. No shadowing, implicit numeric conversions, overloaded operators or implicit tail return exists. Records have positional constructors and fixed nonrecursive layouts; enums have tags without payloads.

Ordinary integer arithmetic checks overflow. The explicitly named wrapping operations have modular semantics. Division and remainder reject zero and signed minimum divided by -1. Integer narrowing checks range. Floating output uses strict no-fast-math/no-contraction flags; this is not a formal IEEE proof.

Borrowed views require live, initialized, typed caller storage. Read-only views may alias; each mutable view is disjoint from all other view parameters during the call. Numerical entry guards do not establish provenance, lifetime or external thread safety. There is no owning heap allocation, returned borrow, hidden synchronization or implicit parallel loop.

The bounded collector evaluates a predicate once per input, emits selected projections in order into caller storage, returns the selected count, and preserves the unused output tail. Its capacity invariant remains a trusted compiler rule, tested but not machine-proved.

## Executables and projects

An executable needs an ordinary, nonstatic `fn main() -> i32` with no parameters. A small C++ wrapper calls it. Library entries remain C-linkage `cf_name` functions. This supplies native process execution, not an OS library, command-line arguments, printing, exceptions, or a general language runtime.

```toml
[project]
name = "demo"
sources = ["src/math.cairn", "src/main.cairn"]
tests = ["tests/average.json"]

[build]
kind = "exe"
arch = "x86-64"
```

Sources form one ordered declaration namespace. Their order is part of the source identity; records still need their field types declared earlier. The loader adds source markers and maps diagnostics back to original paths and line numbers. No independent module compilation, imports, namespace resolution, dependency downloads or build hooks is implemented.

Manifest input is limited to 64 source files, 128 test files and 2 MB of combined native source. Paths must be canonical relative paths inside the project. Traversal, absolute paths, duplicate entries and symbolic-link inputs are rejected. Unknown keys are errors. Executable compiler paths, commands, flags and output directories cannot be supplied by the manifest.

The caller selects a trusted compiler and output location through the CLI. Builds use a fresh directory and never fall back to a stale product. Input hashes bind the bytes actually read, not a transactional snapshot of a concurrently changing filesystem. Process limits are not a security sandbox.

## Diagnostics and AI edits

The existing edit/session and named-choice protocols still preserve the task, signature, dependency and effect boundary. Expression slots now work in expression-bodied functions. Body edits can select either source form. Whole-module checking still runs after insertion, and unchanged comments remain unchanged.

CLI statuses distinguish typed source, native-built artifacts, finite test outcomes, scalar SMT comparison and unknown environment/tool failures. Nonzero process exits are not silently turned into successful tasks. No manifest tests is an error, not vacuous testing success.

## No expanded proof claim

These additions are parser desugarings, a project loader, packaging and tooling. They do not add Lean verification, memory equivalence, GPU execution, owned lifetimes, concurrency, or universal performance. Historical SMT receipts do not automatically verify a changed translator. Re-run the named check for the exact new source and toolchain.
