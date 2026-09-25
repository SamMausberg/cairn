# rules_cairn

A Bazel module with `cairn_library`, `cairn_binary` and `cairn_test` (`defs.bzl`). `extensions.bzl` names the compiler the rules run, a checkout or the `cairn` on `PATH`, and `stage.py` runs one `cairn` command over the sources Bazel hands an action. Nothing is downloaded.

[docs/tools.md](../docs/tools.md#large-projects-and-bazel) shows how to use the rules, and `examples/bazel` is a workspace that does.
