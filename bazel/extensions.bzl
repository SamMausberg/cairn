"""The `cairn` module extension: which CAIRN compiler the rules run.

    cairn = use_extension("@rules_cairn//:extensions.bzl", "cairn")
    cairn.local(path = "/path/to/a/cairn/checkout")   # or leave it out to use the `cairn` on PATH

It makes the repository @cairn_compiler with two targets: `cairn`, the command-line script, and `compiler`, the
files that script reads. Nothing is downloaded. A path is a checkout's root, absolute or relative to the root module.
"""

def _compiler_impl(rctx):
    if rctx.attr.path:
        given = rctx.attr.path
        root = rctx.path(given) if given.startswith("/") else rctx.workspace_root.get_child(given)
        if not root.get_child("bin").get_child("cairn").exists:
            fail("cairn.local(path = %r): no bin/cairn there" % given)
        rctx.symlink(root.get_child("bin").get_child("cairn"), "bin/cairn")
        rctx.symlink(root.get_child("src"), "src")
        compiler = 'glob(["src/cairn/**"], exclude = ["**/__pycache__/**"])'
    else:
        found = rctx.which("cairn")
        if not found:
            fail("No `cairn` on PATH: install CAIRN, or name a checkout with cairn.local(path = ...)")
        rctx.symlink(found, "bin/cairn")
        compiler = "[]"
    rctx.file("BUILD.bazel", """exports_files(["bin/cairn"])

alias(name = "cairn", actual = "bin/cairn", visibility = ["//visibility:public"])

filegroup(name = "compiler", srcs = %s, visibility = ["//visibility:public"])
""" % compiler)

cairn_compiler = repository_rule(
    implementation = _compiler_impl,
    attrs = {"path": attr.string(doc = "A CAIRN checkout's root; empty means the `cairn` on PATH.")},
    local = True,
)

def _cairn_impl(mctx):
    path = ""
    for module in mctx.modules:
        for local in module.tags.local:
            if module.is_root or not path:  # the root module's choice wins
                path = local.path
    cairn_compiler(name = "cairn_compiler", path = path)

cairn = module_extension(
    implementation = _cairn_impl,
    tag_classes = {"local": tag_class(attrs = {"path": attr.string(mandatory = True)})},
)
