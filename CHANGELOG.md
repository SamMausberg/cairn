# Changelog

## 1.0.0

The language grows from a checked CPU kernel language into a general systems language; the compiler core was rebuilt around one typed tree, and every addition arrived with native behavior tests, rejection tests and an application.

- **Types**: generic records, sums and functions with inference and on-demand monomorphization; payloads and fields of any value type; inline `Array[T, N]`; `i8`/`i16`; hex, character and string literals; `const`; layout attributes.
- **Traits**: static dispatch on `Self`, bounds checked per instance, `value.method(...)` resolved in the receiver's module, and explicit `ro<dyn Trait>` fat references with a `dispatch` effect.
- **Ownership**: second-class borrows of values and arrays, checked array parts with visibly-disjoint splitting, first-class zeroed `Buf[T]`, affine moves, `take`/`swap`, `linear struct`, `defer`. No lifetime annotations exist.
- **Errors**: `try` propagates the failure of a two-variant sum; it is the only propagation form.
- **Functions**: copyable code pointers and non-escaping closures passed as `ro<fn(...)>`.
- **Effects**: checked `pure`/`effects(...)` ceilings; `extern` declarations with mandatory effects, `ffi:symbol` propagation and an `unsafe` gate; `mmio_read`, `mmio_write`, `asm`.
- **Concurrency**: `spawn`/`wait` with linear scope-bound tickets and borrow leases; `Atomic[T]` with explicit memory orders; `Mutex[T]` entered only through a closure. ThreadSanitizer-clean.
- **Data parallelism**: `parallel i in n` lanes that are race free by construction, `reduce`, placement types (`@host @pinned @unified @device`), scoped device owners, `transfer`, and the bounded collector as stable stream compaction on the device. One lane body lowers to host threads or CUDA lanes; guards work in device code and abort the host.
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
