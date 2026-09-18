# Compiler ownership and data flow

## Native path

`project.py -> syntax.py -> modules.py -> expansion.py -> checking.py -> codegen.py -> build.py`

The project loader reads only manifest-listed inputs, hashes bytes and maps diagnostic locations. **Syntax** owns tokens, spans and the tree; it resolves nothing. **Modules** links the `std.*` modules a program imports from the package; nothing else can be imported and nothing is fetched. **Expansion** owns the closed generators (bounded families, wire codecs). **Checking** owns everything semantic, in one pass per function over one typed tree: name resolution across modules, on-demand instantiation of generic types and functions (each instance is checked as ordinary monomorphic code), places and second-class borrows, affine and linear ownership, leases held by tasks, race-freedom of lanes, placement, and the interprocedural effect fixed point with caller-renamed footprints. It annotates the tree (`Expr.ty`, `Expr.ref`) and never produces text. **Codegen** lowers that typed tree to readable C++20; a lane body is one lambda whose entry point (`cr::par::run` or `cr::gpu::launch`) is chosen by placement, so host and device share the emitter. **Build** asks `toolchain.py` for the one native command line (clang++/g++, or nvcc with the same strict floating-point and warning contract), writes a fresh directory and hashes its output. `cairnc.py` is the facade; it runs the collector's arithmetic-certificate gate before every emission.

Per-function state lives in one `Scope` object that the checker swaps when it checks a generic instance in the middle of a caller, so instantiation is re-entrant. Signatures are resolved for every concrete function before any body is checked, so call sites never see an unresolved type.

## Runtime

`runtime/cairn_runtime.hpp` owns guards (checked arithmetic, bounds, entry checks) and the scoped scalar buffer; every guard is host/device callable and the device trap was chosen by measurement. `cairn_owners.hpp` owns the movable zeroed `Buf`, `Defer`, borrowed callables and checked parts. `cairn_parallel.hpp` owns threads per region, linear tasks, `Mutex` and `Atomic` with explicit orders. `cairn_gpu.hpp` owns scoped device/pinned/unified memory, lanes, linear stream tickets, reduction and stable compaction. Generated code includes only the headers it needs; there is no scheduler, pool or global state.

## Agent and proof paths

`agent_tools.py` supplies typed source sites, the canonical read-only projection of the whole language, and sealed edit sessions whose effect ceiling spans the full effect vocabulary. `sketches.py` binds named choices to host-owned ranges and contracts. `teaching.py` selects short rule cards from lexical tokens. Source splicing preserves everything outside the authorized range and the complete linked module is rechecked, not just the displayed packet.

`linear_certificates.py` checks exact affine implications; `proofs/` proves that checker sound in Lean, checks the same seventeen certificates there, and proves the collector loop model in bounds and stable. `scalar_semantics.py` and `smt_bridge.py` perform fixed-reference integer/Boolean source comparisons with concrete replay; `verification.py` owns aggregate coverage and cannot mark a module checked because one function passed.

No agent, test generator or solver may rewrite the authority it is checked against. Effects do not specify functional behavior. Native libraries never import the agent tooling or Z3.

## Packaging and repository

The wheel contains the compiler package, runtime headers, target support files, the `std` sources, the typed marker and CLI metadata. Tests, benchmarks, proofs, evidence and historical specifications stay out of it. Ordinary compilation has no third-party Python dependency; Z3, Lean, CUDA and QEMU are optional local tools whose absence turns the corresponding gates into skips or `unknown`, never success.
