"""examples/apps/panel: frames drawn by tasks that lease what they draw with, an update loop whose ceiling keeps it
from allocating, and the shot a person or an agent gets back. Every expectation about where the panels are comes
from the key script, recomputed here, and the pixels are read back from the PNG files the program wrote."""

import shutil
from pathlib import Path

import pytest
from test_std_image import decode_png

from cairn.agent.agent_tools import HANDLES, EditHost
from cairn.agent.shot import shot
from cairn.cli import main
from cairn.compiler.cairnc import Diagnostic, compile_source
from cairn.projects.project import load_project

APP = Path(__file__).resolve().parents[2] / "examples/apps/panel"
KEYS, ITEMS, ROW = "jjojkko", 6, 26


def copied(tmp_path: Path) -> Path:
    root = tmp_path / "panel"
    shutil.copytree(APP, root, ignore=shutil.ignore_patterns("build"))
    return root


def states() -> list[tuple[int, bool]]:
    """The (selected, opened) each frame draws: frame k shows the state after key k."""
    selected, opened, out = 0, False, []
    for key in KEYS:
        if key == "j":
            selected = (selected + 1) % ITEMS
        elif key == "k":
            selected = (selected - 1) % ITEMS
        elif key == "o":
            opened = not opened
        out.append((selected, opened))
    return out


def inside(a, b) -> bool:
    return (
        b["x"] <= a["x"]
        and b["y"] <= a["y"]
        and a["x"] + a["w"] <= b["x"] + b["w"]
        and a["y"] + a["h"] <= b["y"] + b["h"]
    )


def apart(a, b) -> bool:
    return (
        a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"] or a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"]
    )


def test_every_frame_draws_the_state_its_key_left(tmp_path):
    taken = shot(load_project(copied(tmp_path)), ["panel.ui.update"])
    assert taken["status"] == "shot" and taken["exit_code"] == 0, taken.get("stderr")
    assert [f["frame"] for f in taken["frames"]] == list(range(len(KEYS)))
    canvas = {"x": 0, "y": 0, "w": 320, "h": 200}
    for frame, (selected, opened) in zip(taken["frames"], states(), strict=True):
        marks = {e["name"]: e for e in frame["layout"]["elements"]}
        assert set(marks) == {"list", "highlight", "status"} | ({"detail"} if opened else set())
        assert all(inside(m, canvas) for m in marks.values())
        assert inside(marks["highlight"], marks["list"]) and marks["highlight"]["y"] == 12 + selected * ROW
        if opened:
            assert apart(marks["list"], marks["detail"]) and apart(marks["detail"], marks["status"])
        w, h, px = decode_png(Path(frame["png"]).read_bytes())
        assert (w, h) == (320, 200)
        corner = px[170 * w + 300]  # an empty spot of the detail panel, or of the background when it is closed
        assert corner == (0x2E3544FF if opened else 0x1B1F27FF)
    assert all(f["since_previous_ns"] > 0 for f in taken["frames"][1:])
    assert taken["effects"] == {"panel.ui.update": ["read:ui", "trap", "write:ui"]}  # no alloc, by its ceiling


def test_a_shot_says_what_a_change_added_to_a_row(tmp_path):
    root = copied(tmp_path)
    before = load_project(root).source
    render = root / "src/render.cairn"
    render.write_text(
        render.read_text()
        .replace("import std.image (Image);", "import std.image (Image);\nimport std.io;")
        .replace("  return l;\n}", '  io.println("frame");\n  return l;\n}')
    )
    taken = shot(load_project(root), ["panel.render.frame", "panel.ui.update"], before)
    added = taken["changed"]["panel.render.frame"]["added"]
    assert "io" in added and "ffi:write" in added and taken["changed"]["panel.render.frame"]["removed"] == []
    assert "panel.ui.update" not in taken["changed"]  # an unchanged row is not listed
    assert taken["stdout"].count("frame\n") == len(KEYS)


def test_the_cli_prints_the_record_and_a_program_without_captures_has_no_frames(tmp_path, capsys):
    root = copied(tmp_path)
    assert main(["shot", str(root), "--symbol", "panel.ui.update", "--format", "human"]) == 0
    said = capsys.readouterr().out
    assert said.startswith(f"shot: {len(KEYS)} frames") and "panel.ui.update: read:ui, trap, write:ui" in said
    one = tmp_path / "plain.cairn"
    one.write_text("fn main() -> i32 = 0;\n")
    assert main(["shot", str(one), "--format", "json"]) == 0
    assert '"frames": []' in capsys.readouterr().out


def test_the_edit_host_answers_a_shot_request(tmp_path):
    host = EditHost()
    source = load_project(copied(tmp_path)).source
    host.open(source, "panel.ui.update")
    taken = host.respond({"protocol": HANDLES, "handle": "e1", "kind": "shot", "functions": ["panel.ui.update"]})
    assert taken["status"] == "shot" and len(taken["frames"]) == len(KEYS) and "changed" not in taken
    assert all(Path(f["png"]).is_file() for f in taken["frames"])  # kept for the agent to open
    with pytest.raises(Diagnostic, match="functions"):
        host.respond({"protocol": HANDLES, "handle": "e1", "kind": "shot", "functions": ["not.shown"]})


def refused(tmp_path: Path, file: str, old: str, new: str) -> str:
    root = copied(tmp_path)
    path = root / "src" / file
    assert old in path.read_text()
    path.write_text(path.read_text().replace(old, new))
    with pytest.raises(Diagnostic) as caught:
        compile_source(load_project(root).source)
    return caught.value.data["code"]


def test_an_allocation_in_the_update_loop_is_refused(tmp_path):
    root = copied(tmp_path)
    path = root / "src/ui.cairn"
    grown = "  if key == 'j' {\n    let mut scratch = vec.new[u64]();\n    scratch.push(1);"
    text = path.read_text().replace("module panel.ui;", "module panel.ui;\nimport std.vec (Vec);")
    path.write_text(text.replace("  if key == 'j' {", grown))
    with pytest.raises(Diagnostic) as caught:
        compile_source(load_project(root).source)
    assert caught.value.data["code"] == "E-EFFECT-CEILING" and "alloc" in caught.value.data["added_effects"]


def test_touching_the_canvas_while_a_frame_draws_is_refused(tmp_path):
    code = refused(
        tmp_path, "main.cairn", "    let marks = wait(job);", "    image.fill(canvas, 0);\n    let marks = wait(job);"
    )
    assert code == "E-LEASED"


def test_a_frame_that_is_started_must_be_waited_for(tmp_path):
    started = "    let marks = wait(job);\n    match draw.capture(canvas, marks, k) {\n      Ok(taken) => { if taken { shots += 1; } }\n      Err(e) => return 1;\n    }\n"
    assert refused(tmp_path, "main.cairn", started, "") == "E-LINEAR-LEAK"
