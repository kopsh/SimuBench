"""The change log of a definition (T3): every save enters the version history with what it changed,
in the instrument's language; comments and key order say nothing; real models get real snapshots."""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from simubench.engine.jsonpatch import PatchOp
from simubench.server.app import create_app
from simubench.server.benchfiles import BenchFiles
from simubench.server.definitionfiles import DefinitionFiles
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "test-token"

MINIMAL = {
    "schema": "simubench.instrument/1",
    "id": "acme-x1",
    "description": "一台虚构的万用表",
    "state": {"sense.nplc": {"type": "number", "min": 0.01, "max": 100, "default": 1}},
    "commands": [{"header": ":MEASure:VOLTage[:DC]", "query": {"reply": "+1.00000000E+00"}}],
}


def make_files(tmp_path: Path) -> DefinitionFiles:
    return DefinitionFiles(BenchFiles(tmp_path))


def as_tuples(changes: list) -> list[tuple]:
    return [(c.kind, c.action, c.target, c.detail, c.old, c.new) for c in changes]


def test_sb_dmm_1_real_edit_is_described(tmp_path):
    data = yaml.safe_load((ROOT / "validation/instruments/sb-dmm-1.yaml").read_text(encoding="utf-8"))
    files = make_files(tmp_path)
    doc = files.create("sb-dmm-1", data, "api")
    edited = files.edit(
        "sb-dmm-1",
        doc.sha256,
        [PatchOp(op="replace", path="/components/0/params/z_in", value=1.0e9)],
        "agent",
    )
    assert as_tuples(edited.changes) == [("component", "changed", "input.z_in", None, 10.0e6, 1.0e9)]


def test_34465a_real_edit_is_described(tmp_path):
    data = yaml.safe_load(
        (ROOT / "src/simubench/library/instruments/keysight-34465a.yaml").read_text(encoding="utf-8")
    )
    files = make_files(tmp_path)
    doc = files.create("keysight-34465a", data, "api")
    dc = data["components"][1]["params"]["ranges"]["DC"]
    edited = files.edit(
        "keysight-34465a",
        doc.sha256,
        [PatchOp(op="replace", path="/components/1/params/ranges/DC", value=[0.01, *dc])],
        "agent",
    )
    # "直流量程增加 100 mV": the range list gains a value, the rest of the definition says nothing
    assert as_tuples(edited.changes) == [("component", "added", "meter.ranges", "DC", None, 0.01)]


def test_saving_reordered_keys_records_a_version_without_changes(tmp_path):
    files = make_files(tmp_path)
    doc = files.create("acme-x1", MINIMAL, "api")
    reordered = yaml.safe_load(yaml.safe_dump(MINIMAL, sort_keys=True))
    again = files.save("acme-x1", doc.sha256, reordered, "external")
    assert again.changes == []
    assert [v.seq for v in files.versions("acme-x1")] == [2, 1]  # a version, but nothing to say


def test_a_comment_only_external_change_says_nothing(tmp_path):
    files = make_files(tmp_path)
    files.create("acme-x1", MINIMAL, "api")
    path = tmp_path / "instruments" / "acme-x1.yaml"
    commented = "# 台账：样品机\n" + path.read_text(encoding="utf-8")
    path.write_text(commented, encoding="utf-8")  # outside SimuBench, seen at the next save
    edited = files.edit(
        "acme-x1", _sha_of(path), [PatchOp(op="replace", path="/description", value="改过的说明")], "agent"
    )
    versions = files.versions("acme-x1")
    assert [v.source for v in versions] == ["agent", "external", "api"]
    external = next(v for v in versions if v.source == "external")
    assert external.changes == []  # only comments moved: no description
    assert as_tuples(edited.changes) == [
        ("description", "changed", "description", None, "一台虚构的万用表", "改过的说明")
    ]


def _sha_of(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_definition_history_and_changes_endpoints(tmp_path):
    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    files = make_files(tmp_path)
    first = files.create("acme-x1", MINIMAL, "api")
    files.edit("acme-x1", first.sha256, [PatchOp(op="replace", path="/description", value="改")], "agent")
    files.edit(
        "acme-x1",
        files.read("acme-x1").sha256,
        [PatchOp(op="replace", path="/state/sense.nplc/max", value=10)],
        "agent",
    )
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        history = c.get("/api/definitions/acme-x1/history").json()
        assert [v["seq"] for v in history] == [3, 2, 1]
        assert [v["source"] for v in history] == ["agent", "agent", "api"]
        assert history[0]["changes"] == [
            {
                "kind": "state",
                "action": "changed",
                "target": "sense.nplc",
                "detail": "max",
                "old": 100,
                "new": 10,
            }
        ]
        assert history[2]["changes"][0]["action"] == "added"  # the creation describes everything
        between = c.get("/api/definitions/acme-x1/history/changes", params={"base": 1, "target": 3}).json()
        assert between["base"] == 1 and between["target"] == 3
        assert {(x["kind"], x["action"]) for x in between["changes"]} == {
            ("description", "changed"),
            ("state", "changed"),
        }
        assert (
            c.get("/api/definitions/acme-x1/history/changes", params={"base": 1, "target": 9}).status_code
            == 404
        )
        assert c.get("/api/definitions/keysight-34465a/history").json() == []  # not a workspace file
