"""The `cairn` module extension: which CAIRN compiler the rules run.

    cairn = use_extension("@rules_cairn//:extensions.bzl", "cairn")
    cairn.local(path = "/path/to/a/cairn/checkout")   # or leave it out to use the `cairn` on PATH

It makes the repository @cairn_compiler with two targets: `cairn`, the command-line script, and `compiler`, the
files that script reads. Nothing is downloaded. A path is a checkout's root, absolute or relative to the root module.

A checkout's bin/cairn runs under the `python3` on the PATH Bazel was started with, found here once and required to
be 3.11 or later. Bazel runs each action with a PATH of its own, whose `python3` may be older (Ubuntu 22.04's is 3.10).
"""

def _python(rctx):
    found = rctx.which("python3")
    if not found:
        fail("No `python3` on PATH: CAIRN needs Python 3.11 or later")
    checked = rctx.execute([found, "-c", "import sys; raise SystemExit(sys.version_info < (3, 11))"])
    if checked.return_code:
        fail("CAIRN needs Python 3.11 or later, and the `python3` on PATH, %s, is older" % found)
    return found

def _compiler_impl(rctx):
    if rctx.attr.path:
        given = rctx.attr.path
        root = rctx.path(given) if given.startswith("/") else rctx.workspace_root.get_child(given)
        script = root.get_child("bin").get_child("cairn")
        if not script.exists:
            fail("cairn.local(path = %r): no bin/cairn there" % given)
        rctx.file("bin/cairn", "#!/bin/sh\nexec '%s' '%s' \"$@\"\n" % (_python(rctx), script), executable = True)
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
    environ = ["PATH"],
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
