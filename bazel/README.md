# rules_cairn

This folder is a Bazel module that builds CAIRN libraries, binaries and tests. `defs.bzl` holds the rules `cairn_library`, `cairn_binary` and `cairn_test`. `extensions.bzl` names the compiler the rules run: a checkout named with `cairn.local(path = ...)`, or the `cairn` on `PATH`. `stage.py` runs one `cairn` command over the sources Bazel hands an action. Nothing is downloaded.

[docs/tools.md](../docs/tools.md#large-projects-and-bazel) shows how to use the rules, and `examples/bazel` is a workspace that does.
