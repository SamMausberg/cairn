# Development

Use a branch and a focused change. Run the fast suite before and after modifying code. Run both native compilers and sanitizers when changing runtime or lowering. Maintain rejection tests and independent behavior oracles, not just accepted examples.

Keep source in src/cairn, tests in tests, real examples in examples, and generated results outside tracked source. Do not duplicate compiler implementations in scripts. Changing the parser/checker/codegen must update the semantic/editor implementation digests where applicable.

Every added language construct must have a precise elaboration, failure policy, cost boundary and implementation-status entry. Prefer removing repeated boilerplate over adding opaque punctuation. Do not reduce source-token measurements by excluding required imported semantics.

No license grant, public hosting, package release or dependency auto-update is authorized by this development scaffold. Publication remains an explicit owner action through the private-only path.
