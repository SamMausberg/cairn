"""No GitHub network calls. Exercise the exact publication orchestration with fakes."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from release.audit_repository import audit
from release.publish_private import PublishError, publish, validate_private

SHA = "a" * 40


class Fake:
    def __init__(self, root, change=None):
        self.root = root
        self.calls = []
        self.change = change

    def __call__(self, args, *, cwd):
        self.calls.append(args)
        if args[:3] == ["git", "rev-parse", "--show-toplevel"]:
            return str(self.root)
        if args[:2] == ["git", "status"]:
            return "?? secret.txt" if self.change == "dirty" else ""
        if args[:2] == ["git", "branch"]:
            return "main"
        if args == ["git", "remote"]:
            return "origin" if self.change == "remote" else ""
        if args[:3] == ["git", "rev-parse", "HEAD"]:
            return SHA
        if args[0] == "git":
            return ""
        if args[-1] == "user":
            return json.dumps({"login": "Other" if self.change == "owner" else "TestOwner", "type": "User"})
        if "POST" in args and self.change == "collision":
            raise PublishError("Name conflict")
        if args[-1].endswith("/git/ref/heads/main"):
            return json.dumps({"object": {"sha": SHA}})
        return json.dumps(
            {
                "id": 123,
                "full_name": "TestOwner/cairn",
                "private": self.change != "public",
                "visibility": "public" if self.change == "public" else "private",
                "fork": False,
                "owner": {"login": "TestOwner", "type": "User"},
                "html_url": "https://github.com/TestOwner/cairn",
            }
        )


def clean(root):
    return {"findings": []}


def test_the_audit_admits_a_demo_frame_and_no_other_binary(tmp_path):
    import subprocess

    png = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"
    (tmp_path / "demos/v/frames").mkdir(parents=True)
    (tmp_path / "demos/v/frames/a.png").write_bytes(png)
    (tmp_path / "b.png").write_bytes(png + b"b")  # a picture outside demos/
    (tmp_path / "demos/v/c.png").write_bytes(b"GIF89a\0")  # named .png, and not one
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    for args in (["init", "-q"], ["add", "."], ["commit", "-qm", "frames"]):
        subprocess.run([*git, *args], check=True, capture_output=True)
    assert {f["path"] for f in audit(tmp_path)["findings"]} == {"b.png", "demos/v/c.png"}


def test_a_compressed_json_lines_record_under_evidence_is_read_as_its_text(tmp_path):
    import lzma
    import subprocess

    lines = b'{"role": "user", "text": "hello"}\n{"role": "assistant", "text": "hi"}\n'
    token = b'{"said": "ghp_' + b"a" * 36 + b'"}\n'  # a credential pattern, which compression must not hide
    files = {
        "evidence/v9/transcript.jsonl.xz": lzma.compress(lines),  # admitted: xz of JSON Lines
        "tools/transcript.jsonl.xz": lzma.compress(lines),  # the same bytes outside evidence/
        "evidence/v9/bytes.jsonl.xz": lzma.compress(b"\0\1\2\3"),  # decompresses to something that is no text
        "evidence/v9/prose.jsonl.xz": lzma.compress(b"not json\n"),  # text, and not JSON Lines
        "evidence/v9/named.jsonl.xz": b"\0 not xz",  # named as one, and not xz
        "evidence/v9/token.jsonl.xz": lzma.compress(token),
    }
    for name, data in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(data)
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    for args in (["init", "-q"], ["add", "."], ["commit", "-qm", "records"]):
        subprocess.run([*git, *args], check=True, capture_output=True)
    binaries = {"tools/transcript.jsonl.xz", "evidence/v9/bytes.jsonl.xz", "evidence/v9/prose.jsonl.xz",
                "evidence/v9/named.jsonl.xz"}  # fmt: skip
    found = {(f["path"], f["rule"]) for f in audit(tmp_path)["findings"]}
    assert found == {(name, "binary-file") for name in binaries} | {("evidence/v9/token.jsonl.xz", "github-token")}


def test_the_audit_reads_a_history_of_any_length_and_what_left_it(tmp_path):
    """Twelve hundred commits, past the thousand the audit once refused to read: a binary one commit added and the
    next removed is found, and a file every commit changes is read once for each of its versions."""
    import subprocess

    stream = []
    for n in range(1, 1201):
        stream.append(f"commit refs/heads/main\nmark :{n}\ncommitter t <t@t> {1700000000 + n} +0000\ndata 0\n".encode())
        stream.append(f"from :{n - 1}\n".encode() if n > 1 else b"")
        body = f"{n}\n".encode()
        stream.append(b"M 100644 inline counter.txt\ndata %d\n%s\n" % (len(body), body))
        if n == 600:
            stream.append(b"M 100644 inline tools/blob.bin\ndata 4\n\0\1\2\3\n")
        if n == 601:
            stream.append(b"D tools/blob.bin\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "fast-import", "--quiet"], input=b"".join(stream), check=True)
    result = audit(tmp_path)
    assert result["commits"] == 1200 and result["distinct_blobs"] == 1201
    assert [(f["path"], f["rule"]) for f in result["findings"]] == [("tools/blob.bin", "binary-file")]


def test_the_audit_judges_one_tree_by_the_folder_it_sits_in_and_refuses_names_links_and_submodules(tmp_path):
    import lzma
    import os
    import subprocess

    for top in ("evidence", "tools"):  # one tree, read under each top-level folder
        (tmp_path / top / "shared").mkdir(parents=True)
        (tmp_path / top / "shared/t.jsonl.xz").write_bytes(lzma.compress(b'{"a": 1}\n'))
    (tmp_path / "config").mkdir()
    (tmp_path / "config/.env").write_text("X=1\n")
    (tmp_path / "id_rsa").write_text("not a key\n")
    (tmp_path / "lib.o").write_text("text\n")
    os.symlink("id_rsa", tmp_path / "link")
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    for args in (["init", "-q"], ["add", "."], ["update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},vendor"],
                 ["commit", "-qm", "tree"]):  # fmt: skip
        subprocess.run([*git, *args], check=True, capture_output=True)
    found = {(f["path"], f["rule"]) for f in audit(tmp_path)["findings"]}
    assert found == {("tools/shared/t.jsonl.xz", "binary-file"), ("config/.env", "excluded-path"),
                     ("id_rsa", "excluded-path"), ("lib.o", "excluded-path"), ("link", "symlink-or-submodule"),
                     ("vendor", "symlink-or-submodule")}  # fmt: skip


def test_a_text_record_under_evidence_may_reach_four_megabytes_and_nothing_else_may_pass_two(tmp_path):
    import subprocess

    text = b'{"x": 1}\n' * 333_334  # three megabytes of text
    (tmp_path / "evidence/v9").mkdir(parents=True)
    (tmp_path / "tools").mkdir()
    (tmp_path / "evidence/v9/suite.json").write_bytes(text)  # admitted: a text record, under four megabytes
    (tmp_path / "evidence/v9/huge.json").write_bytes(text * 2)  # six megabytes, past the evidence limit
    (tmp_path / "evidence/v9/blob.json").write_bytes(text[:-1] + b"\0")  # a binary is held to two megabytes
    (tmp_path / "tools/other.json").write_bytes(text + b"\n")  # outside evidence/, two megabytes is still the limit
    (tmp_path / "tools/copy.json").write_bytes(text)  # the admitted record's own bytes, at a path that has no allowance
    git = ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    for args in (["init", "-q"], ["add", "."], ["commit", "-qm", "records"]):
        subprocess.run([*git, *args], check=True, capture_output=True)
    found = {f["path"]: f["rule"] for f in audit(tmp_path)["findings"]}
    assert found == {"evidence/v9/huge.json": "large-file", "evidence/v9/blob.json": "large-file",
                     "tools/other.json": "large-file", "tools/copy.json": "large-file"}  # fmt: skip


def test_default_never_contacts_github(tmp_path):
    runner = Fake(tmp_path)
    result = publish(tmp_path, "TestOwner/cairn", run=runner, audit_fn=clean)
    assert result["remote_created"] is False
    assert all(c[0] == "git" for c in runner.calls)


@pytest.mark.parametrize("cause", ["dirty", "remote", "owner", "public", "collision"])
def test_unsafe_state_never_pushes(tmp_path, cause):
    runner = Fake(tmp_path, cause)
    with pytest.raises(PublishError):
        publish(tmp_path, "TestOwner/cairn", execute=True, run=runner, audit_fn=clean)
    assert all("push" not in c for c in runner.calls)


def test_publication_order_private_before_push(tmp_path):
    runner = Fake(tmp_path)
    result = publish(tmp_path, "TestOwner/cairn", execute=True, run=runner, audit_fn=clean)
    assert result["status"] == "published-private"
    push = next(i for i, c in enumerate(runner.calls) if "push" in c)
    create = next(i for i, c in enumerate(runner.calls) if "POST" in c)
    gets = [i for i, c in enumerate(runner.calls) if c[-1] == "repos/TestOwner/cairn"]
    assert create < gets[0] < push < gets[1]
    assert "private=true" in runner.calls[create]
    assert all("--force" not in c for c in runner.calls)
    assert runner.calls[-1][:3] == ["git", "remote", "add"]


@pytest.mark.parametrize("target", ["x", "../repo", "owner/repo;echo", "owner/a b", "owner/", "/repo", "owner/a/b"])
def test_bad_target_has_no_actions(tmp_path, target):
    runner = Fake(tmp_path)
    with pytest.raises(PublishError):
        publish(tmp_path, target, execute=True, run=runner, audit_fn=clean)
    assert not runner.calls


def test_private_flag_must_be_boolean_true():
    with pytest.raises(PublishError):
        validate_private({"private": "true"}, "a", "b")


def test_secret_found_stops_before_github(tmp_path):
    runner = Fake(tmp_path)
    with pytest.raises(PublishError):
        publish(tmp_path, "TestOwner/cairn", execute=True, run=runner, audit_fn=lambda p: {"findings": ["secret"]})
    assert all(c[0] == "git" for c in runner.calls)


def test_historical_secret_is_not_printed(tmp_path):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.name", "test")
    git("config", "user.email", "test@local.invalid")
    secret = "ghp_" + "A" * 36
    (tmp_path / "config.txt").write_text(secret)
    git("add", ".")
    git("commit", "-m", "fixture")
    (tmp_path / "config.txt").write_text("clean")
    git("add", ".")
    git("commit", "-m", "remove")
    result = audit(tmp_path)
    assert result["status"] == "blocked"
    assert secret not in json.dumps(result)
    assert any(x["rule"] == "github-token" for x in result["findings"])


@pytest.mark.parametrize("mutation", ["private", "id", "owner", "name"])
def test_creation_readback_failure_stops_before_upload(tmp_path, mutation):
    base = Fake(tmp_path)

    def runner(args, *, cwd):
        text = base(args, cwd=cwd)
        if args[-1] == "repos/TestOwner/cairn":
            data = json.loads(text)
            if mutation == "private":
                data["private"] = False
                data["visibility"] = "public"
            if mutation == "id":
                data["id"] = 456
            if mutation == "owner":
                data["owner"]["login"] = "SomeoneElse"
            if mutation == "name":
                data["full_name"] = "TestOwner/other"
            return json.dumps(data)
        return text

    with pytest.raises(PublishError):
        publish(tmp_path, "TestOwner/cairn", execute=True, run=runner, audit_fn=clean)
    assert not any("push" in call for call in base.calls)


def test_final_visibility_change_never_registers_origin(tmp_path):
    base = Fake(tmp_path)

    def runner(args, *, cwd):
        text = base(args, cwd=cwd)
        if args[-1] == "repos/TestOwner/cairn" and any("push" in call for call in base.calls):
            data = json.loads(text)
            data["private"] = False
            data["visibility"] = "public"
            return json.dumps(data)
        return text

    with pytest.raises(PublishError):
        publish(tmp_path, "TestOwner/cairn", execute=True, run=runner, audit_fn=clean)
    assert any("push" in call for call in base.calls)
    assert not any(call[:3] == ["git", "remote", "add"] for call in base.calls)
