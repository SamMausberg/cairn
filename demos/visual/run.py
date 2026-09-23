#!/usr/bin/env python3
"""An agent looks at what the plate viewer draws, sees the colour bar cover the plate, and moves it.

The agent is scripted: its requests are written below, so every run tells the same story. The host answers each one
from the program as it stands, runs the program headless for a shot, and reports each frame's PNG, its layout record
and the effect rows the edit changed. The frames are copied, recompressed without loss, into --frames.
"""

import argparse
import json
import shutil
import struct
import subprocess
import sys
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cairn.agent.agent_tools import EditHost, stable_json  # noqa: E402
from cairn.projects.project import load_project  # noqa: E402

TASK = "The colour bar hides the right edge of the plate. Move it so that it does not."
REQUESTS = [  # what the scripted agent sends, in order
    {"protocol": "cairn.edit/2", "handle": "e1", "kind": "shot", "functions": ["heat.view.frame"]},
    {"protocol": "cairn.edit/2", "handle": "e1", "kind": "body", "replacement": "{ return MAP_X + span + 8; }"},
    {"protocol": "cairn.edit/2", "handle": "e1", "kind": "shot", "functions": ["heat.view.frame"]},
]


def cairn(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "bin/cairn"), *args], cwd=ROOT, capture_output=True, text=True)


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def repack(png: Path, out: Path) -> int:
    """The same pixels in a smaller file: std.image writes stored zlib blocks, and this recompresses them."""
    data, chunks, at = png.read_bytes(), [], 8
    while at < len(data):
        n, kind = struct.unpack(">I4s", data[at : at + 8])
        chunks.append((kind, data[at + 8 : at + 8 + n]))
        at += 12 + n
    pixels = zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT"))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    header = next(body for kind, body in chunks if kind == b"IHDR")
    packed = data[:8] + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels, 9)) + chunk(b"IEND", b"")
    out.write_bytes(packed)
    return len(packed)


def boxes(shot: dict) -> str:
    last = shot["frames"][-1]["layout"]["elements"]
    return ", ".join(f"{e['name']} x {e['x']}..{e['x'] + e['w']}" for e in last if e["name"] in {"map", "bar"})


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, default=ROOT / "results/demos/visual", help="Where the fixed project goes.")
    p.add_argument("--frames", type=Path, help="Copy the frames here, recompressed (demos/visual/frames).")
    a = p.parse_args()
    out = a.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    print("An agent sees what the plate viewer draws through the CAIRN edit host, and fixes its layout.\n")
    test = cairn("test", rel(HERE), "--format", "human")
    print(f"$ cairn test {rel(HERE)}\n{textwrap.indent(test.stdout + test.stderr, '  ')}", end="")
    source = load_project(HERE).source
    host = EditHost()
    packet = host.open(source, "heat.view.bar_x", {"task": TASK})
    print(
        f"\n[bar_x] host -> agent: a packet of {len(stable_json(packet)):,} bytes: bar_x's source, the constant MAP_X,"
    )
    print("    and the signature and effect row of frame, its caller; the task: " + TASK)
    record: dict = {"test_before": test.returncode, "exchanges": []}
    shots = []
    for request in REQUESTS:
        answer = host.reply(json.dumps(request))
        record["exchanges"].append({"request": request, "answer": answer})
        if request["kind"] == "shot":
            shots.append(answer)
            print(f"[bar_x] agent -> host: shot, with the effect row of {', '.join(request['functions'])}")
            print(f"[bar_x] host: {answer['status']}, {len(answer['frames'])} frames; the last marks {boxes(answer)}")
            print(f"    changed rows: {json.dumps(answer['changed'])}" if "changed" in answer else "    (the original)")
        else:
            print(f"[bar_x] agent -> host: body edit {request['replacement']}")
            print(f"[bar_x] host: {answer['status']}, effect row [{', '.join(answer['effects'])}] within the ceiling")
    candidate = host.admitted["e1"][-1][0]
    start = next(i for i, (x, y) in enumerate(zip(source, candidate, strict=False)) if x != y)
    end = next(i for i in range(1, len(source)) if source[-i] != candidate[-i]) - 1
    old, new = source[start : len(source) - end], candidate[start : len(candidate) - end]
    fixed = out / "plate_viewer"
    shutil.copytree(HERE, fixed, ignore=shutil.ignore_patterns("build", "frames", "*.py", "*.md"))
    view = fixed / "src/view.cairn"
    assert view.read_text().count(old) == 1, "the edit is one span of view.cairn"
    view.write_text(view.read_text().replace(old, new))
    test = cairn("test", rel(fixed), "--format", "human")
    print(f"\n$ cairn test {rel(fixed)}\n{textwrap.indent(test.stdout + test.stderr, '  ')}", end="")
    record["test_after"] = test.returncode
    if a.frames:
        a.frames.mkdir(parents=True, exist_ok=True)
        sizes = [repack(Path(shots[0]["frames"][-1]["png"]), a.frames / "before.png")]
        for f in shots[-1]["frames"]:
            sizes.append(repack(Path(f["png"]), a.frames / f"after-{f['frame']}.png"))
        print(f"\n{len(sizes)} frames written to {rel(a.frames)}, {sum(sizes) // 1024} KB in all.")
    (out / "record.json").write_text(json.dumps(record, indent=2) + "\n")
    return 0 if record["test_before"] != 0 and record["test_after"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
