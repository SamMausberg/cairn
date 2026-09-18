# Changelog

## Unreleased

- **Recipes**: generators are library code. `recipe name[K:nat] for R { ... }` holds ordinary function and record declarations with static `each` (over a record's fields or a natural range, at declaration, statement, field-list and call-argument level), `fold`, `where` values, `$name` splices and `require` domains; `derive name[naturals] for Type;` expands it before checking into code of the deriving module. The closed Python generator behind `derive wire` is gone: `std.wire` is twelve lines of CAIRN and produces the same C++ byte for byte. Receipts pin each recipe by the hash of its tokens. `wire` is no longer a reserved word.

- **Queued device work**: `let t = spawn transfer(dst, src);` and `let k = spawn parallel i in n after t { ... };` put device work on its own stream and return; the ticket leases what the work touches until `wait`, `after` orders work by device events without a host wait, and work queued after a ticket may share what that ticket holds. This exposes the runtime's stream tickets, which until now only its own tests used.

- **Incremental builds**: `cairn build --incremental` compiles one object per module against a shared interface header and reuses an object only when everything that went into it hashes the same; a body-only edit recompiles one module. Opt-in, because it gives up inlining across modules; device programs and images stay one unit.

- **Checked reduction**: `reduce +` is offered on unsigned integers, on the host and on the device, and traps exactly when the total does not fit, in any order.
- **Evidence**: a preregistered fresh-model pilot (`evidence/v1_1/ai_pilot`): nine of nine tasks solved from the rule cards alone, eight on the first compile, every transcript audited. The cards were then revised with what the subjects had to guess.

## 1.0.0

The language grows from a checked CPU kernel language into a general systems language; the compiler core was rebuilt around one typed tree, and every addition arrived with native behavior tests, rejection tests and an application.

- **Types**: generic records, sums and functions with inference and on-demand monomorphization; payloads and fields of any value type; inline `Array[T, N]`; `i8`/`i16`; hex, character and string literals; `const`; layout attributes.
- **Traits**: static dispatch on `Self`, bounds checked per instance, `value.method(...)` resolved in the receiver's module, explicit `ro<dyn Trait>` fat references with a `dispatch` effect, and owned `Dyn[Trait]` values for heterogeneous collections.
- **Ownership**: second-class borrows of values and arrays, checked array parts with visibly-disjoint splitting, first-class zeroed `Buf[T]`, affine moves, `take`/`swap`, `linear struct`, `defer`. No lifetime annotations exist.
- **Errors**: `try` propagates the failure of a two-variant sum; it is the only propagation form.
- **Functions**: copyable code pointers and non-escaping closures passed as `ro<fn(...)>`.
- **Conversions**: float-to-integer is a checked truncation that traps on NaN or out of range (it was unsupported).
- **Effects**: checked `pure`/`effects(...)` ceilings; `extern` declarations (optionally `extern "symbol"`) with mandatory effects, `ffi:symbol` propagation and an `unsafe` gate; `mmio_read`, `mmio_write`, `asm`.
- **Concurrency**: `spawn`/`wait` with linear scope-bound tickets and borrow leases; `Atomic[T]` with explicit memory orders; `Mutex[T]` entered only through a closure. ThreadSanitizer-clean.
- **Audit**: an adversarial review of the new checker found fourteen accepted-but-unsound programs (use after free, overflow through extent identity, three lease races, forged linear values, under-reported effects, operand-order holes); each is fixed and pinned in `tests/test_soundness.py`, and the first library author's seven compiler bugs are pinned in `tests/test_language.py`. A second round with three reviewers (checker, trait and module system, emitter) found about forty more: closures that freed or raced what their own call lent, lanes whose own bodies escaped the rule their callees obey, a device collector that was never checked as device code, impls never held to their trait, overlapping and ambiguous impls, a self-owning record classified as a copy, `try` abandoning an owner mid-expression, `len` and `transfer` ignoring task leases, privacy holes in `family` and `derive wire`, locals differing only by underscores sharing a C++ name, executables that dropped reachable vtable members, and a `.gitignore` pattern that had hidden `std/core.cairn` from every fresh checkout. A third pass attacked only the new rules and found nine more (a host view reaching device code through a helper, an internal error from an unmapped lane footprint, a `Self`-returning member behind `dyn`, a record holding itself through an inline array, and lanes that could not yet reach helpers that dispatch). All are fixed and pinned there too; the fixes also made the rules more precise (a closure borrows exactly what it captures, a parallel map may take a closure that writes nothing it captured, K-way part splits are accepted).
- **Data parallelism**: `parallel i in n` lanes that are race free by construction, `kernel fn` device helpers, `reduce`, placement types (`@host @pinned @unified @device`), scoped device owners, `transfer`, and the bounded collector as stable stream compaction on the device. One lane body lowers to host threads or CUDA lanes; guards work in device code and abort the host.
- **Modules**: `module`, `import` (aliases and name lists), `pub`, and a standard library written in CAIRN and linked from the package.
- **Targets**: Linux x86-64 and AArch64 hosts from one toolchain table, CUDA through `nvcc` under the same strict floating-point contract, and a freestanding AArch64 profile verified under QEMU.
- **Proof**: a dependency-free Lean 4 development proves the certificate checker sound, checks the seventeen collector certificates, and proves in-bounds stores and stable selection for the collector loop model; receipts report it only for the exact checked bundle.
- **Agent layer**: the canonical projection, edit sessions, effect ceilings and rule cards cover the whole language; an edit cannot add a lane race, a shared write, an allocation or a task past its ceiling.
- **Tooling**: `cairn fmt` (comment preserving), `cairn lsp`, an editor grammar, optional `#line` source maps, `ruff` formatting and lint for the Python sources, `make lint`.
- **Compatibility**: every 0.6 program, diagnostic code and receipt field is preserved. Newly reserved words: `trait impl dyn const pub linear parallel reduce spawn try as type device pinned unified`. New builtin names (`take swap transfer wait mmio_read mmio_write asm`) yield to a program's own function of the same name. Two 0.6 rejections became legal by design: record payloads in sums and arrays of sums.

## 0.6.0

- Added zeroed lexical heap buffers and fixed stack arrays with nonescaping borrows, explicit storage/effects, and metadata-only len.
- Added monomorphic scalar payload sums, typed error data, exhaustive match, and correct loop break/continue through match arms.
- Added exact integer coefficient certificates for 17 collector arithmetic obligations; trusted Python checker, not Lean.
- Added all-function scalar equivalence coverage and public-type census; unsupported entries block aggregate verification.
- Replaced substring feature selection with lexical cards and shortened common instructions without altering edit authority.
- Fixed finite-task acceptance after abnormal child exit; added address-space limits to CLI execution.
- Added independent decimal/sorting/filtering examples, lifetime observer tests, production sanitizer and abort tests.
- Retained local-only publication policy and explicit gaps in C++ breadth, native proof, GPU support and measured AI proficiency.

## 0.5.0

- Split native syntax, typed expansion, checking, C++ emission and runtime into an installable src/cairn package; retained one implementation behind the compatibility facade.
- Added one CLI for creating, checking, building, running, testing, inspecting and scalar-comparing programs.
- Added explicit ordered multi-file projects, source-origin diagnostics, data-only manifests and native executable entry points.
- Added expression-bodied functions, optional host placement in the CPU profile, and else-if desugaring. Existing arithmetic and safety rules are unchanged.
- Preserved expression/body edit support and source comments for the compact forms.
- Changed the new CLI's default architecture to baseline x86-64; legacy comparison harnesses keep their explicit x86-64-v3 profile.
- Added offline wheel packaging, clean-install tests, fresh build directories, input-path rejection tests, and a private-only first-publication tool.
- Corrected source-audit density accounting to include the complete split package, not merely its small facade.
- Organized earlier specifications and teaching data separately from active compiler code. No model training, new Lean verification, GPU lowering or timing improvement is claimed.
