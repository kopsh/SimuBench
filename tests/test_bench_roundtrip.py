"""An edit changes only what it edits (#1254, D73): the Hello World bench's comments, number spelling
(``1.0e6``), folded description, explicit ``run.clock: command`` and integer layout stay byte for byte
whatever the UI, MCP or API changes — every edit kind goes through ``BenchFiles.save``."""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from simubench.engine import edit as E
from simubench.library.loader import load_yaml
from simubench.server.benchfiles import BenchFiles

BENCH = "hello.bench.yaml"


def changed_lines(before: str, after: str) -> tuple[list[str], list[str]]:
    """The lines the edit removed and the lines it added."""
    diff = list(difflib.ndiff(before.splitlines(), after.splitlines()))
    return [d[2:] for d in diff if d.startswith("- ")], [d[2:] for d in diff if d.startswith("+ ")]


def comments_and_spelling(text: str) -> list[str]:
    """What an edit elsewhere must never touch: every comment and every hand-spelled number."""
    return [ln for ln in text.splitlines() if ln.strip().startswith("#") or "e6" in ln or "e-7" in ln]


@pytest.fixture
def files(tmp_path: Path) -> BenchFiles:
    fs = BenchFiles(tmp_path)
    fs.create_hello()
    return fs


def test_adding_an_instrument_and_a_wire_adds_only_their_lines(files: BenchFiles):
    """The issue's regression: on Hello World, one instrument and one wire are four added lines."""
    path = files.workspace / BENCH
    before = path.read_text(encoding="utf-8")
    doc = files.read(BENCH)
    files.edit(
        BENCH,
        doc.sha256,
        [
            E.AddInstrument(name="dmm", model="keysight-34465a", x=294, y=191),
            E.Connect(**{"from": "awg.CH1", "to": "dmm.IN"}),
        ],
    )
    removed, added = changed_lines(before, path.read_text(encoding="utf-8"))
    assert removed == []
    assert added == [
        "  dmm:",
        "    model: keysight-34465a",
        "  - {from: awg.CH1, to: dmm.IN}",
        "  dmm: {x: 294, y: 191}",
    ]


#: Each edit kind, and the comment or hand-spelled lines it may remove: only its own subject's.
EDITS = {
    "set a setting": ([E.SetState(instrument="awg", path="ch1.amplitude", value=2.0)], 0),
    "drag a node": ([E.Move(name="scope", x=440, y=10)], 0),
    "rename": ([E.Rename(name="sa", new_name="specan")], 0),
    "remove an instrument": ([E.Remove(name="scope")], 2),  # the scope's comment and its 5.0e-7
    "disconnect": ([E.Disconnect(index=0)], 0),
    "label": ([E.SetLabel(name="awg", label="信号源")], 0),
    "run": ([E.SetRun(seed=8)], 0),
}


@pytest.mark.parametrize(("ops", "own"), EDITS.values(), ids=EDITS.keys())
def test_every_edit_leaves_untouched_comments_and_numbers_as_written(files: BenchFiles, ops, own):
    path = files.workspace / BENCH
    before = path.read_text(encoding="utf-8")
    files.edit(BENCH, files.read(BENCH).sha256, ops)
    after = path.read_text(encoding="utf-8")
    removed, added = changed_lines(before, after)
    assert 0 < len(removed) + len(added) <= 10, (removed, added)  # a few lines, not the file
    lost = [ln for ln in comments_and_spelling(before) if ln not in after.splitlines()]
    assert len(lost) == own, lost
    assert "  clock: command" in after.splitlines()  # the explicit default stays
    assert after.split("instruments:")[0] == before.split("instruments:")[0]  # the folded description too


def test_a_drag_keeps_integer_coordinates_and_a_whole_document_put_keeps_comments(files: BenchFiles):
    path = files.workspace / BENCH
    files.edit(BENCH, files.read(BENCH).sha256, [E.Move(name="awg", x=10.0, y=130.0)])
    text = path.read_text(encoding="utf-8")
    assert "  awg: {x: 10, y: 130}" in text.splitlines()
    # The Web's text editor and the API save a whole document: only its differences are written.
    doc = files.read(BENCH)
    data = dict(doc.data)
    data["run"] = {**data["run"], "seed": 9}
    files.save(BENCH, doc.sha256, data, "ui")
    after = path.read_text(encoding="utf-8")
    removed, added = changed_lines(text, after)
    assert (removed, added) == (["  seed: 7"], ["  seed: 9"])
    assert load_yaml(after)["instruments"]["awg"]["state"]["ch1.frequency"] == 1.0e6


def test_a_new_file_and_json_are_written_in_canonical_form(files: BenchFiles):
    data = load_yaml((files.workspace / BENCH).read_text(encoding="utf-8"))
    files.save("other.bench.yaml", None, data, "api")
    other = (files.workspace / "other.bench.yaml").read_text(encoding="utf-8")
    assert "#" not in other  # a new file has nothing of the author's to keep
    assert load_yaml(other)["name"] == "hello"


def test_an_edit_through_the_http_api_keeps_the_file_s_text(tmp_path: Path):
    """The Web and the API reach the same writer: adding a wire over HTTP adds one line."""
    from fastapi.testclient import TestClient

    from simubench.server.app import create_app
    from simubench.server.manager import SessionManager

    manager = SessionManager(tmp_path, scpi_base_port=None)
    with TestClient(
        create_app(manager, token="t", web_dist=Path("/nonexistent")), base_url="http://127.0.0.1"
    ) as c:
        c.headers["x-simubench-token"] = "t"
        manager.files.create_hello()
        before = (tmp_path / BENCH).read_text(encoding="utf-8")
        sha = c.get("/api/benches/file", params={"path": BENCH}).json()["sha256"]
        r = c.post(
            "/api/benches/edit",
            json={
                "path": BENCH,
                "base_sha256": sha,
                "ops": [{"op": "disconnect", "index": 1}],
                "source": "ui",
            },
        )
        assert r.status_code == 200, r.text
    removed, added = changed_lines(before, (tmp_path / BENCH).read_text(encoding="utf-8"))
    assert (removed, added) == (["  - {from: awg.CH2, to: sa.RF_IN}"], [])
