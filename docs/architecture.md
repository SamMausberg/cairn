# Compiler ownership and data flow

## Native path

`project.py -> syntax.py -> expansion.py -> checking.py -> codegen.py -> build.py`

The project loader reads only manifest-listed inputs, hashes bytes and maps diagnostic locations. Syntax owns tokens, spans, AST nodes and source forms. Expansion owns closed families and wire codecs. Checking owns lexical scopes, types, owner restrictions, match coverage and the finite interprocedural effect fixed point. Code generation emits ordinary readable C++; runtime/cairn_runtime.hpp implements guards, views and scoped allocation. The builder chooses a trusted native toolchain, writes a fresh directory and hashes its output. The small cairnc.py facade invokes the exact collector arithmetic-certificate gate before emission, then delegates to these components.

Owners are represented as lexical compiler bindings, not a general runtime object system. Private storage footprints are summarized as local_read/local_write at public boundaries; alloc/free/initialization/stack effects remain visible. Callee view effects are renamed to caller arguments before private footprints are hidden. A helper must not leak its local names into a caller's public effect row.

A tagged sum is a monomorphic scalar tag/payload value. Exhaustive source match lowers to a switch with valid-tag checks. Loop break/continue use compiler-owned labels only when necessary so switching does not retarget control flow. C++ scopes perform normal owner destruction at those exits. Neither C++ RAII nor finite lifetime tests are a mechanized ownership theorem.

## Agent and proof paths

`agent_tools.py` supplies typed source sites, canonical read-only projection and sealed edit sessions. `sketches.py` binds named choices to host-owned ranges/contracts. `teaching.py` uses lexical features to select short semantic cards. Source splicing preserves material outside the authorized range. The full module is rechecked, not just the displayed packet.

`linear_certificates.py` checks exact fixed affine implications. It has no solver, native execution or input-dependent advice. `scalar_semantics.py` and `smt_bridge.py` perform fixed-reference integer/Boolean source comparisons and concrete replay. `verification.py` owns aggregate function/type coverage and cannot mark a whole module checked merely because one function passed.

No agent, test generator or solver may rewrite the authority it is checked against. Effects do not specify functional behavior. A return type does not specify a reference's intent. Native libraries do not import the agent tooling or Z3 at runtime.

## Packaging and repository

The wheel contains the compiler package, C++ header, typed marker and CLI metadata only. Tests, benchmarks, training data, evidence and historical specifications stay out of the installed package. Ordinary native compilation has no third-party Python dependency. Z3 is an optional local system library; exact affine certificates use Python alone. Current evidence is versioned under evidence/v0_6, earlier evidence stays historical.

The layout is not a namespaced module implementation, a separate linker, an application ecosystem or a complete standard library. Those remain named missing capabilities in capabilities.json. Small files and short cards reduce repeated context in some examples but do not reduce the semantic obligations of an actual edit by fiat.
