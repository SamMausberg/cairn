"""A device program built for the host, `--emulate`: every piece of its device work runs on host threads.

The program is judged against a real device target, resolved as a device build resolves it (projects/target.py), so
a program that emulates is one that would build for that target: the features its code needs, an implementation's
`needs`, the architecture its PTX names. The C++ is what nvcc would compile, byte for byte. The host compiler builds it
with CAIRN_EMULATE defined and runtime/cairn_emulate.hpp read first (toolchain.emulated), and that header is the
machine under runtime/cairn_exec.hpp: device memory is host memory, a region's lanes run on the host lane pool, a
cooperative region's threads are host threads at a std::barrier, and queued work runs to completion at its spawn.

What the host cannot run as the device would is refused by name before a compiler runs (E-EMULATE), never
approximated: typed PTX, whose host pass traps; an extern kernel started with `launch(...)`; vendored CUDA; and a
device feature the host machine does not model. Every record of an emulated build, run, test or validation carries
`emulation` (`record`), which says the device work ran on the host and which target judged it, so no record can be
taken for a device run. A validation it passes is `finite-tested-emulated` evidence, never `finite-tested` on the
device (verify/validation/validation.py).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..compiler.syntax.parser import Parser
from ..compiler.syntax.tree import Diagnostic
from .target import FEATURES, DeviceTarget

# The device features runtime/cairn_emulate.hpp runs as the device would: lanes and the runtime's collectors, the two
# fragment families and the whole-matrix multiply with their host lowerings, bf16 storage, and a pipeline's copies,
# which land at once on the host. Adding one is a host lowering of it first, then a name here.
MODELED = ("device_lanes", "wmma", "mma_sync", "bf16", "cp_async")
EVIDENCE = "finite-tested-emulated"
CLAIM = (
    "device work emulated on host threads for {target}: the program was judged against {target} and built for the "
    "host, so this is a host run, never a device run"
)


def refused(message: str, **details: Any) -> Diagnostic:
    return Diagnostic("E-EMULATE", message + " Emulation refuses what the host cannot run as the device would; build "
                      "for the device to run it.", **details)  # fmt: skip


def check(source: str, receipt: dict[str, Any], foreign: Iterable[tuple[str, tuple[str, ...]]] = ()) -> None:
    """Refuse, with E-EMULATE, a program whose device work the host cannot run as the device would. `receipt` is the
    front end's (`cairnc.generate`), and `foreign` the manifest's `[foreign]` table."""
    for name, row in sorted(receipt["functions"].items()):
        for asm in row.get("assembly", ()):
            if asm["target"] == "ptx":
                raise refused(f"{name} holds typed PTX (asm ptx {asm['needs']}), which only a device runs: the host "
                              "pass of a lane traps where it stands.", line=asm["line"], symbol=name)  # fmt: skip
    for f in Parser(source).parse().functions:
        if f.launch is not None:
            raise refused(f"{f.name} starts the CUDA kernel {f.symbol or f.name} with launch(...), and the host "
                          "cannot run a kernel it has only the declaration of.", line=f.line, symbol=f.name)  # fmt: skip
    if cuda := sorted(path for path, _ in foreign if path.endswith(".cu")):
        raise refused(f"The manifest vendors CUDA ({cuda[0]}), which only nvcc builds and only a device runs.",
                      source=cuda[0])  # fmt: skip
    for feature in receipt.get("device_features", ()):
        if feature not in MODELED:
            raise refused(f"This program needs {feature} ({FEATURES[feature].what}), which the host emulation does "
                          f"not model; it models {', '.join(MODELED)}.", feature=feature)  # fmt: skip


def judged(device: DeviceTarget, receipt: dict[str, Any], source: str) -> DeviceTarget:
    """`device`, holding what the program asks of it, as a device build judges it (`build.judged`): a selected
    implementation's needs, the program's features and its PTX's architecture; then what emulation refuses. For a
    program built outside `build`, as validation builds one."""
    from . import build  # which imports this module

    device = build.judged(device, receipt)
    check(source, receipt)
    return device


def target(name: str) -> str:
    """What a candidate history keeps as the target of a validation emulated for the device target `name`
    (agent/history.py): never the host's own, and never a device's."""
    return f"host emulation of {name}"


def record(device: DeviceTarget) -> dict[str, Any]:
    """What an emulated build, run, test or validation says about where its device work ran."""
    return {
        "runs_on": "host",
        "judged_against": device.name,
        "machine": "runtime/cairn_emulate.hpp",
        "claim": CLAIM.format(target=device.name),
        "not_checked": "what only nvcc or ptxas refuses: this build ran neither",
        "differences": "docs/devices.md#emulating-device-code-on-the-host",
    }
