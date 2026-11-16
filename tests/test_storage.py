"""What a workspace's .simubench/ holds and what lost its file (#1238)."""

from pathlib import Path

from simubench.cli import main
from simubench.storage import clean_orphans, human, inventory


def put(path: Path, size: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def workspace(tmp_path: Path) -> Path:
    """Two benches and an instrument; ``gone`` was deleted outside SimuBench, ``kept`` is there."""
    for name in ("kept", "sub/deep"):
        put(tmp_path / f"{name}.bench.yaml")
    put(tmp_path / "instruments" / "dmm.yaml")
    sb = tmp_path / ".simubench"
    for name in (
        "kept.bench.yaml",
        "gone.bench.yaml",
        "sub/deep.bench.yaml",
        "instruments/dmm.yaml",
        "instruments/old.yaml",
    ):
        put(sb / "history" / name / "index.jsonl", 5)
        put(sb / "history" / name / "000001.yaml", 100)
    put(sb / "assistant" / "kept.bench.yaml.json", 30)
    put(sb / "assistant" / "gone.bench.yaml.json", 40)
    put(sb / "files" / "kept" / "dmm" / "USB" / "a.csv", 1000)
    put(sb / "files" / "sub" / "deep" / "awg" / "b.csv", 500)
    put(sb / "files" / "gone" / "awg" / "c.csv", 2000)
    put(sb / "files" / "gone" / "dso" / "d.csv", 3000)
    return tmp_path


def test_the_inventory_finds_each_kind_and_marks_what_lost_its_file(tmp_path):
    ws = workspace(tmp_path)
    got = {(e.kind, e.name): e for e in inventory(ws)}
    # history: a unit per saved file, orphan when the file is gone
    assert not got["history", "kept.bench.yaml"].orphan and got["history", "kept.bench.yaml"].bytes == 105
    assert got["history", "gone.bench.yaml"].orphan and got["history", "instruments/old.yaml"].orphan
    assert (
        not got["history", "instruments/dmm.yaml"].orphan and not got["history", "sub/deep.bench.yaml"].orphan
    )
    # conversations
    assert not got["assistant", "kept.bench.yaml"].orphan and got["assistant", "gone.bench.yaml"].orphan
    # instrument files: a live bench's folder, a live bench in a subfolder, and one orphan counted once
    assert not got["files", "kept"].orphan and got["files", "kept"].bytes == 1000
    assert not got["files", "sub/deep"].orphan
    assert got["files", "gone"].orphan and got["files", "gone"].bytes == 5000
    assert ("files", "gone/awg") not in got  # its instrument folders are inside the one orphan
    assert human(1536) == "1.5 KiB" and human(7) == "7 B"


def test_cleaning_removes_conversations_and_files_but_keeps_history_unless_asked(tmp_path):
    ws = workspace(tmp_path)
    removed = clean_orphans(ws)
    assert {(e.kind, e.name) for e in removed} == {("assistant", "gone.bench.yaml"), ("files", "gone")}
    sb = ws / ".simubench"
    assert not (sb / "files" / "gone").exists() and not (sb / "assistant" / "gone.bench.yaml.json").exists()
    assert (sb / "files" / "kept").exists() and (sb / "history" / "gone.bench.yaml").exists()  # history stays
    assert {(e.kind, e.name) for e in clean_orphans(ws, ("history",))} == {
        ("history", "gone.bench.yaml"),
        ("history", "instruments/old.yaml"),
    }
    assert (sb / "history" / "kept.bench.yaml").exists() and (ws / "kept.bench.yaml").exists()


def test_the_command_lists_first_and_deletes_only_with_yes(tmp_path, capsys):
    ws = workspace(tmp_path)
    assert main(["storage", str(ws), "--clean"]) == 0
    out = capsys.readouterr().out
    assert "files: 6.3 KiB in 3 entries" in out and "(its file is gone)" in out
    assert "would remove: 2 entries" in out and "nothing deleted" in out
    assert (ws / ".simubench" / "files" / "gone").exists()
    assert main(["storage", str(ws), "--clean", "--yes"]) == 0
    assert not (ws / ".simubench" / "files" / "gone").exists()
    assert not [e for e in inventory(ws) if e.orphan and e.kind != "history"]
