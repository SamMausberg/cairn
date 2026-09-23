"""Bazel rules for CAIRN: cairn_library, cairn_binary and cairn_test.

A CAIRN program is checked as one whole program, so a library's sources travel to every binary and test that
depends on it, dependencies first, and each of those runs the compiler once over all of them. A library is checked
on its own too, as a validation action, so `bazel build //some:lib` refuses a library the checker refuses. The
compiler and the host C++ compiler run from the host: the rules add no hermetic toolchain of their own.
"""

CairnInfo = provider(
    doc = "The CAIRN sources a target brings, its dependencies' first.",
    fields = {"srcs": "depset of .cairn files, in the order the compiler reads them"},
)

_TOOLS = {
    "_cairn": attr.label(default = Label("@cairn_compiler//:cairn"), executable = True, cfg = "exec", allow_files = True),
    "_compiler": attr.label(default = Label("@cairn_compiler//:compiler"), cfg = "exec"),
    "_stage": attr.label(default = Label("//:stage.py"), allow_single_file = True, cfg = "exec"),
}

def _sources(ctx):
    return depset(ctx.files.srcs, transitive = [d[CairnInfo].srcs for d in ctx.attr.deps], order = "postorder")

def _stage(ctx, mode, srcs, out, kind, extra_inputs = []):
    arguments = [ctx.file._stage.path, mode, "--cairn", ctx.executable._cairn.path, "--name", ctx.label.name,
                 "--kind", kind, "--cxx", ctx.attr.cxx, "--out", out.path, "--"]
    ctx.actions.run(
        executable = "python3",
        arguments = arguments + [f.path for f in srcs.to_list()],
        inputs = depset(
            [ctx.file._stage, ctx.executable._cairn] + extra_inputs,
            transitive = [srcs, ctx.attr._compiler[DefaultInfo].files],
        ),
        outputs = [out],
        mnemonic = "Cairn" + mode.capitalize(),
        progress_message = "cairn %s %s" % (mode, ctx.label),
        use_default_shell_env = True,  # python3 and the C++ compiler come from the host's PATH
    )

def _library_impl(ctx):
    srcs = _sources(ctx)
    stamp = ctx.actions.declare_file(ctx.label.name + ".check.json")
    _stage(ctx, "check", srcs, stamp, "library")
    return [
        CairnInfo(srcs = srcs),
        DefaultInfo(files = depset(ctx.files.srcs)),
        OutputGroupInfo(_validation = depset([stamp])),
    ]

def _binary_impl(ctx):
    srcs = _sources(ctx)
    exe = ctx.actions.declare_file(ctx.label.name)
    _stage(ctx, "build", srcs, exe, "exe")
    return [DefaultInfo(executable = exe, files = depset([exe])), CairnInfo(srcs = srcs)]

def _test_impl(ctx):
    srcs = _sources(ctx)
    runner = ctx.actions.declare_file(ctx.label.name + ".sh")
    words = ["exec", "python3", ctx.file._stage.short_path, "test", "--cairn", ctx.executable._cairn.short_path,
             "--name", ctx.label.name, "--cxx", ctx.attr.cxx, "--"] + [f.short_path for f in srcs.to_list()]
    ctx.actions.write(runner, "#!/bin/sh\n" + " ".join([_quote(w) for w in words]) + "\n", is_executable = True)
    files = depset([ctx.file._stage, ctx.executable._cairn], transitive = [srcs, ctx.attr._compiler[DefaultInfo].files])
    return [DefaultInfo(executable = runner, runfiles = ctx.runfiles(transitive_files = files))]

def _quote(word):
    return "'" + word.replace("'", "'\\''") + "'"

_COMMON = {
    "srcs": attr.label_list(allow_files = [".cairn"], doc = "This target's own sources, in the order they are read."),
    "deps": attr.label_list(providers = [CairnInfo], doc = "cairn_library targets whose sources come first."),
    "cxx": attr.string(default = "clang++", doc = "The host C++ compiler `cairn build` and `cairn test` use."),
}

cairn_library = rule(
    implementation = _library_impl,
    attrs = _COMMON | _TOOLS,
    doc = "CAIRN modules others depend on; `cairn check` runs over them and their dependencies as validation.",
)

cairn_binary = rule(
    implementation = _binary_impl,
    attrs = _COMMON | _TOOLS,
    executable = True,
    doc = "An executable: `cairn build --kind exe` over its sources and every dependency's, with one `fn main`.",
)

cairn_test = rule(
    implementation = _test_impl,
    attrs = _COMMON | _TOOLS,
    test = True,
    doc = "`cairn test` over its sources and every dependency's: each test block in a process of its own.",
)
