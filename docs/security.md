# Safety and trust

CAIRN is an alpha compiler, not a security sandbox or an entirely verified toolchain. Use OS isolation for hostile programs, adapters or compilers. CPU/address-space/core-dump limits do not protect files, credentials, syscalls or network access. CLI `run` has a default 1024 MiB virtual-address-space cap, configurable in the allowed range 64..65536 MiB. This does not constrain a shared library loaded directly by a foreign host. Host OOM, stack exhaustion, allocation failure and process termination remain possible.

Manifests are data and accept local listed paths only; no hooks, commands, downloads, arbitrary compiler flags, traversal or symlinks. Native builds use fresh directories and do not reuse a successful stale binary after failure. External native compiler paths and output locations are trusted host choices. Filesystem races and hostile global tool configuration are not fully isolated.

Scoped heap/stack buffers are initialized scalar storage with no source-level escaping references, but their compiler/runtime lifetime enforcement is not proved. Aborts do not promise RAII cleanup. Native views still require genuine provenance, live initialized storage and no conflicting foreign access. Tagged sums require a valid tag and initialized matching active payload. Numerical guards cannot prove those foreign assumptions.

Proof and feedback channels fail closed for missing tools or unsupported fragments. A model cannot change a pinned reference/domain/signature/effect ceiling through its edit response. Exact affine certificates cover specified identities only. Z3 source equivalence is not a Lean proof or a native refinement theorem. Child processes printing a pass and exiting abnormally are rejected.

All development in this delivery is local; no code or private proof was sent to a remote service. Publication remains opt-in, private-only and non-force. The local preflight scans every reachable committed blob for finite credential patterns and excluded binary/secret paths. It is not an exhaustive detector. Untracked files block publication. No unrelated user file, token or third-party toolchain binary belongs in the repository.

The GitHub workflow is manual-only, private-repository guarded, read-only, and commit-pins checkout without retained credentials. No remote workflow ran here. It has no deployment, artifact publication or public fallback. Tests use fake publication responses. A public host, open-source license and wider sharing were not selected. Report sensitive issues only through the owner's private channel.
