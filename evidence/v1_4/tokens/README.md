# The library and the examples, shorter

`tokens.json` counts lines and lexer tokens (`cairn.compiler.lexing.lex`) in the 48 CAIRN files of `src/cairn/std/` and `examples/`, leaving out `examples/proof_scope/`, whose text the verification tests pin. Comments and layout are not tokens.

| | lines | tokens |
|---|---|---|
| before | 2840 | 25150 |
| after the identical pass | 2807 | 24537 |
| after the shared digit loop | 2805 | 24506 |

The identical pass is a handful of rewrites, each tried at every site and kept only where nothing any program emits changed. A call leaves out the extents its views carry (`io.write(f, "xy")` for `io.write(f, 2, "xy")`). A string bound to a local and read once goes to the call that reads it. A `let` loses a type its initializer already has, a parameter loses `@host`, a function whose body is one `return` becomes an expression body, and three pairs of parentheses the precedence already implies are gone.

`tools/checks/emission_identity.py` checked it. It records the C++ and the effect rows of 1044 programs: every example project and single-file example, one program importing all of `std`, every CAIRN program written into a test, and every `cairn` block of the docs, or the code that refuses one. Taken on the commit before the identical pass and on the pass itself (the commit that adds this tool) with `--normalize literals --normalize zero`, the two records are equal. Without the normalizations 21 programs differ, and only where those two identities say: a string once bound to a `const` local is now written at its one use, and an omitted extent of the part `v[0..n]` is written `(n - 0)`.

The digit loop is the one change whose C++ differs. `parse_u64` and `parse_i64` in `std.text` now share a private `digits` that accumulates a decimal against a limit, where each held its own copy. Its effect row is the loop's, the rows of the two parsers are unchanged, and `digits` adds one entry guard that repeats the caller's. `tests/language/test_std.py` builds and runs the parsers natively under both compilers: both signed limits and one past each, the `u64` maximum, a 21-digit overflow and the offset of the first bad byte.

Some rewrites were tried and dropped. An expression body `m <= n && equal(s[0..m], prefix)` for `starts_with` and `ends_with` emits a checked subtraction the block form does not, because the fact `m <= n` does not reach the right side of `&&`. Leaving out an extent that a part names as `hi - lo` emits that subtraction where the source had a name for it. Parentheses the precedence makes redundant were kept where they help a reader, as in `(i + 1) & mask`.
