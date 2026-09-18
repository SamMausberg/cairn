"""The freestanding profile, executed: a real image on a real (emulated) machine.

Nothing here installs anything. Without an AArch64 host or qemu-system-aarch64 the whole module
skips with the reason, because the claim being made is "this ran", not "this compiled".
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

import pytest

from cairn.build import build
from cairn.project import ProjectError, load_project
from cairn.toolchain import BARE, TARGETS, emulator, flags

ROOT = Path(__file__).resolve().parents[1]
EMBEDDED = ROOT / "examples/embedded"
QEMU = shutil.which("qemu-system-aarch64")
TRAP_STATUS = 134  # cr::trap() leaves through cr_exit(134); see targets/aarch64-virt/start.S.

TRANSCRIPT = """cairn freestanding: qemu virt, pl011 uart
log 23,19,31,7,42,19,x9,,99999999999999999999,
  bad digit at 17
  empty field at 20
  too large at 40
readings 6 total 141 max 42 mean 23
sorted 7 19 19 23 31 42
ok
"""

pytestmark = [
    pytest.mark.skipif(platform.machine() not in {"aarch64", "arm64"}, reason="aarch64-virt needs an AArch64 host"),
    pytest.mark.skipif(QEMU is None, reason="qemu-system-aarch64 is not installed"),
]

HEAP = """fn main() -> i32 {
  buffer scratch:u8[4] = zeroed;
  scratch[0] = 1;
  return i32(scratch[0]) - 1;
}
"""
OWNED = """fn main() -> i32 {
  let mut room = Buf[u64](4);
  room[0] = 7;
  return i32(room[0]) - 7;
}
"""
LANES = """fn fill(n:usize, out:rw<u64>[n]) { parallel i in n { out[i] = u64(i); } }
fn main() -> i32 {
  stack data:u64[4] = zeroed;
  fill(4, data);
  return i32(data[3]) - 3;
}
"""
FOREIGN = """extern fn write(fd:i32, data:ro<u8>[n], n:usize) -> i64 effects(io);
fn main() -> i32 {
  let text = "hi\\n";
  unsafe { let sent = write(1, text, len(text)); }
  return 0;
}
"""
QUIET = 'fn main() -> i32 { unsafe { asm("nop"); } return 0; }\n'


def write_project(root: Path, source: str, target: str = "aarch64-virt", kind: str = "exe") -> Path:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src/main.cairn").write_text(source)
    manifest = f'[project]\nname = "probe"\nsources = ["src/main.cairn"]\n\n[build]\nkind = "{kind}"\n'
    (root / "cairn.toml").write_text(manifest + f'target = "{target}"\n')
    return root


def image(path: Path, out: Path, **kw) -> dict:
    record = build(load_project(path), output=out, timeout=120, **kw)
    assert record["status"] == "native-built", record.get("stderr", record.get("message"))
    return record


def emulate(record: dict, timeout: int = 60) -> subprocess.CompletedProcess:
    command = emulator(record["target"], record["artifact"])
    assert command[1:8] == ["-M", "virt", "-cpu", "cortex-a72", "-nographic", "-semihosting", "-kernel"]
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def application(tmp_path_factory) -> dict:
    return image(EMBEDDED, tmp_path_factory.mktemp("app"))


@pytest.fixture(scope="module")
def trap_demo(tmp_path_factory) -> dict:
    return image(EMBEDDED / "trap", tmp_path_factory.mktemp("trap"), target="aarch64-virt")


def test_manifest_selects_the_target():
    assert load_project(EMBEDDED).target == "aarch64-virt"
    assert load_project(ROOT / "examples/hello").target == "hosted"


def test_application_runs_on_the_machine(application):
    finished = emulate(application)
    assert finished.stdout == TRANSCRIPT
    assert finished.returncode == 0  # fn main() -> i32 reaches QEMU's exit status unchanged.


def test_guards_still_trap_on_bare_metal(trap_demo):
    finished = emulate(trap_demo)
    assert finished.returncode == TRAP_STATUS
    assert finished.stdout == "trap demo: reading window[4] of 4\n"
    assert "unreachable" not in finished.stdout  # The bounds guard stopped the machine before it.


@pytest.mark.parametrize("fixture", ["application", "trap_demo"])
def test_image_needs_no_library_at_link_time(fixture, request):
    record = request.getfixturevalue(fixture)
    if shutil.which("nm") is None:
        pytest.skip("nm is not installed")
    undefined = subprocess.run(["nm", "-u", record["artifact"]], capture_output=True, text=True, timeout=60)
    assert undefined.stdout == ""
    assert record["artifact"].endswith(".elf")


def test_gcc_produces_the_same_machine(tmp_path):
    if shutil.which("g++") is None:
        pytest.skip("g++ is not installed")
    record = image(EMBEDDED, tmp_path, cxx="g++")
    assert emulate(record).stdout == TRANSCRIPT
    assert subprocess.run(["nm", "-u", record["artifact"]], capture_output=True, text=True).stdout == ""


def test_start_up_owns_the_entry_point(application):
    generated = (Path(application["directory"]) / "program.cpp").read_text()
    assert "int main()" not in generated  # start.S calls cf_main; there is no hosted entry point.
    assert "-nostdlib" in application["command"] and "-ffreestanding" in application["command"]


@pytest.mark.parametrize(
    ("source", "effect"),
    [(HEAP, "'alloc'"), (OWNED, "'alloc'"), (LANES, "'par:host'"), (FOREIGN, "'ffi:write'")],
    ids=["buffer", "Buf", "parallel", "extern"],
)
def test_hosted_runtime_effects_are_rejected(tmp_path, source, effect):
    project = load_project(write_project(tmp_path, source))
    with pytest.raises(ProjectError) as rejected:
        build(project, output=tmp_path / "out")
    assert "A freestanding target has no hosted runtime" in str(rejected.value)
    assert effect in str(rejected.value)


def test_freestanding_needs_one_executable(tmp_path):
    project = load_project(write_project(tmp_path, QUIET, kind="library"))
    with pytest.raises(ProjectError, match='set kind = "exe"'):
        build(project, output=tmp_path / "out")


def test_unknown_target_is_named_not_guessed(tmp_path):
    write_project(tmp_path, QUIET, target="risc-v-moon")
    with pytest.raises(ProjectError, match="known targets are"):
        load_project(tmp_path)


def test_hosted_builds_are_unaffected(tmp_path):
    record = image(ROOT / "examples/hello", tmp_path / "hosted")
    assert record["target"] == "hosted" and emulator(record["target"], record["artifact"]) is None
    assert "int main()" in (Path(record["directory"]) / "program.cpp").read_text()
    assert not set(BARE) & set(record["command"])
    assert flags(kind="library")[-2:] == ["-shared", "-fPIC"]
    assert subprocess.run([record["artifact"]], timeout=60).returncode == 0


def test_targets_are_a_closed_documented_set():
    assert set(TARGETS) == {"hosted", "aarch64-virt"}
    for name in set(TARGETS) - {"hosted"}:
        directory = Path(__file__).resolve().parents[1] / "src/cairn/targets" / name
        assert (directory / "start.S").is_file() and (directory / "link.ld").is_file()
