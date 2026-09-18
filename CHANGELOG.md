# Changelog

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
