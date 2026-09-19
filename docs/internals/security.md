# Safety and trust

CAIRN is a young compiler, not a security sandbox and not an entirely verified toolchain. Use OS isolation for hostile programs, adapters or compilers.

CPU, address-space and core-dump limits do not protect files, credentials, syscalls or network access. `cairn run` caps virtual address space at 1024 MiB by default (64..65536 MiB). The cap is lifted for device programs, because CUDA reserves far more address space than it uses, and it never constrains a shared library loaded by a foreign host. Host OOM, stack exhaustion, allocation failure and process termination remain possible.

## What safe code promises, and on what evidence

Outside `unsafe` and `extern`, an accepted program cannot use a moved owner, leak or double-consume a linear value, alias a mutable borrow, keep a borrow past its call, race in a parallel region, touch a place a live task was lent, or index memory of the wrong placement. Arithmetic, bounds, tags, extents and array parts are guarded, and a failed guard aborts the process, on the device as on the host.

These rules are implemented in `checking.py` and exercised by rejection tests and by native runs under Address, Leak, UndefinedBehavior and Thread sanitizers and device death tests. They are not mechanized: a checker bug is a soundness bug, and generic code is checked per instance. Aborts do not run cleanup.

## The foreign boundary

`extern` declarations, `mmio_read`, `mmio_write` and `asm` are usable only inside `unsafe { }`. Their effects (`ffi:symbol`, `io`, `mmio`, `asm`) propagate to every transitive caller, and `unsafe` blocks are counted per function in the receipt, so an audit starts from the receipt rather than from a text search:

```json
"process": { "effects": ["ffi:getpid", "io"], "syntactic_check_sites": { "unsafe_blocks": 1 } }
```

An extern's signature and effects are trusted as written. Callers of exported functions must supply live, initialized, correctly typed storage for every borrow, and a valid tag and active payload for every sum; numerical entry guards cannot prove provenance or exclude concurrent foreign access.

## Builds

Manifests are data and accept local listed paths only: no hooks, commands, downloads, arbitrary flags, traversal or symlinks. One checker reads every manifest of a build, so a dependency's `cairn.toml` is refused for the same unknown table, unknown option, bad name or unknown kind, architecture or target as the root's. Every path in any of them, whether a source, a test contract or a dependency directory, has one canonical spelling inside its own root: no `.`, `..` or backslash segment.

Each rule below is enforced in `src/cairn/projects/` and pinned in `tests/projects/test_projects.py`.

| Rule | Enforced by | Test that pins it |
|---|---|---|
| Manifests are data, and a dependency's is read as strictly as the root's | `project.read_manifest` | `test_bad_manifests_fail_closed` |
| No path segment is a symbolic link | `project.contained_file` | `test_symlink_source_rejected` |
| A dependency is vendored inside the root, modules only, pinned by hash, nothing fetched | `project.dependencies` | `test_vendored_dependencies_load_first_stay_private_and_are_pinned` |
| A symbolic link is not a dependency directory | `project.dependencies` | `test_a_symbolic_link_is_not_a_dependency` |
| One directory is one project under one name, and one name is one project | `project.dependencies` | `test_one_name_is_one_project_of_the_build` |
| No project declares a `std.*` module, and no project reopens a module another declared | `project.claim` | `test_a_project_of_one_file_declares_no_module_of_the_packaged_library` |
| An executable's `main` comes only from the sources the root project lists | `build.build` | `test_the_entry_point_is_the_root_project_s_own` |
| A cached object is reused only while its bytes still match the digest beside it | `build.intact`, `build.store` | `test_a_cached_object_is_reused_only_while_its_bytes_still_match_its_key` |
| The object cache is `build/objects`; neither it, an object nor a digest may be a link | `build.objects` | `test_the_object_cache_stays_inside_the_project_build_directory` |
| A unit whose compile times out or is killed leaves no object and still writes `cairn.build/1` | `build.objects` | `test_a_unit_that_times_out_or_is_killed_records_the_same_build_as_one_unit_does` |

Only project modules, the modules of dependencies vendored inside the project root, and the packaged `std.*` can be imported. A file with no `module` header would declare into the previous file's module, which is why reopening is refused where a dependency's module is still open. A dependency is source you chose to vendor: it is checked like your own code, its `unsafe` blocks and `extern` declarations show in the effect rows of whatever calls them, and nothing else vouches for it. Since `main` is searched only in the root project's own sources, a dependency neither supplies the entry point nor denies the project its own.

Native builds use fresh directories and never reuse a stale binary. `--incremental` reuses an object only under a key that hashes everything that went into it, and only while the object still matches the sha256 written beside it, so a truncated, half-written or substituted file is compiled again instead of linked. That digest is an integrity check, not an authorization one. It catches an interrupted build, a corrupted or shared cache, and a stale file left under a valid name. It stops nobody who can write into the project's own `build/`: whoever can do that can rewrite the digest as easily as the object, and usually the sources too.

Compiler paths and output locations are trusted host choices, and `toolchain.py` is the only place a native flag is chosen. A freestanding image has no MMU, caches or vector table set up: a hardware fault hangs rather than reports, and the language's own guards are what stop bad indices there.

## Agents

A model cannot change a pinned reference, domain, signature, effect ceiling, visible dependency set or source outside its authorized range through an edit response, and the whole linked module is rechecked after every edit. Proof and feedback channels fail closed for missing tools or unsupported fragments. The formatter fails closed: same tokens and comments, or no change. The language server analyses the open buffer in-process and executes nothing.

## Publication

Development is local. Publication is opt-in, private-only and non-force. The preflight scans every reachable committed blob for finite credential patterns and excluded binary and secret paths, and is not an exhaustive detector. The GitHub workflow is manual-only, private-repository guarded and read-only. No license or public host has been selected. [private-publication.md](../project/private-publication.md) has the sequence and its failure modes. Report sensitive issues only through the owner's private channel.
