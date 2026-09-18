# Safety and trust boundary

This is an alpha compiler, not a security sandbox or a formally verified toolchain. Use isolated operating-system environments for hostile programs, manifests, adapters or native compilers. Process limits alone do not isolate files, credentials, system calls or the network.

The new project loader accepts only explicit data fields and local source/test paths. It rejects path traversal, symbolic-link inputs, duplicates, unknown commands/hooks and oversized sources. The normal builder uses fresh output directories, validates executable entry signatures, preserves runtime guards and reports failed or timed-out builds. It does not download compilers, run manifest commands, silently enable fast math or reuse a previous successful artifact.

External compiler executables and caller-selected output paths are trusted choices. Filesystem concurrency and hostile global Git configuration are not comprehensively isolated. A hostile foreign caller can violate view lifetime/provenance preconditions despite numerical pointer guards. The compiler has no proof covering native memory, concurrency or GPU behavior.

Repository publication is opt-in, private-only, and fail-closed. The default publisher performs no network actions. It checks all reachable committed blobs for named credential patterns, rejects binary artifacts and sensitive paths, checks local state and personal account identity, creates a new private repository and verifies privacy before pushing. Pattern scanning is not a guarantee that every possible secret has been found. Ignored or untracked files are not uploaded; untracked files block the publish preflight.

The GitHub workflow is manual-only with read-only repository permissions and a commit-pinned checkout action. It does not publish packages, upload artifacts, deploy, accept pull_request_target events or request secrets. It has not been executed remotely in this delivery. Dependency installation happens only if a user explicitly runs that workflow.

Do not report a vulnerability through a public issue containing private source or credentials. Use the owner's chosen private reporting channel. No public hosting location or open-source license has been selected by this artifact.
