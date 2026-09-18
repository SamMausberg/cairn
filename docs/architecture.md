# Compiler and repository architecture

## Production path

`project.py -> syntax.py -> expansion.py -> checking.py -> codegen.py -> build.py`

The project loader reads explicitly listed files, records their identity and maps source diagnostics. The parser owns tokens, source spans, types and AST construction. Closed expansion implements static families and wire schemas. Checking owns lexical environments, array rules and interprocedural effects. Code generation lowers the checked AST to readable C++ with explicit guards. The separately packaged runtime header defines those guards and view access. The builder chooses an explicit native compiler and records the native result.

`cairnc.py` is a small compatibility facade for `compile_source`, AST types and existing research tools. The implementation is not duplicated behind it. Tests and tools import the installable `cairn` package. Source-audit measurements must count the transitive implementation, not only this facade.

`cli.py` is the command boundary. `project.py` never launches processes. The manifest cannot select a shell command or compiler executable. `build.py` does not execute the program; `run` is explicit. `testing.py` builds and executes supplied finite task cases in a limited child process. No component installs tools or authenticates to a network service implicitly.

## AI path

`agent_tools.py` exposes source sites, canonical projection and sealed edit sessions. `sketches.py` binds named expression choices to fixed host-owned slots. `teaching.py` supplies relevant syntax and semantic-difference cards. `scalar_semantics.py` translates the restricted pure scalar fragment and independently replays distinguishing inputs; `smt_bridge.py` accesses a locally installed Z3 shared library.

The agent is not a trusted source of contracts or permissions. A successful typecheck does not imply the task is correct. An SMT-equivalence result does not prove the native backend. The solver and model do not become runtime dependencies of generated native code.

## Packaging

`pyproject.toml` produces a small Python wheel containing only the compiler package, runtime header, entry point and metadata. Tests, training data, benchmarks and research history do not enter the installed runtime. The package requires no third-party Python library for ordinary compilation. Optional Z3 is a system-library dependency of scalar verification only. Packaging build tools and development tests have explicit versions.

The repository contains the earlier research sources with provenance, but omits old generated binaries, nested archives, PDF reports and run caches. Historical specifications live under docs/history. Training artifacts retain their original claims and are explicitly labeled inherited; they are not fresh 0.5 results. Current results are generated into ignored results/ and selected evidence is retained under evidence/.

## Limits of this structure

Splitting files does not itself prove modularity or reduce the semantic work of a safe edit. There is still one frontend AST, one global source namespace and one C++ backend. General owning types, errors, standard libraries, concurrency and GPU semantics remain engineering and verification work. The layout gives those changes a named home without asserting they exist.
