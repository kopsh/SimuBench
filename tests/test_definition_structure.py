"""The Web build area's structure of a definition (#1201, instrument-authoring.md 9): the effective
structure read with extends merged in, and a whole structure written back so that the file keeps
only what it does not inherit (D59)."""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from simubench.library.loader import DefinitionLibrary
from simubench.server.app import create_app
from simubench.server.manager import SessionManager

TOKEN = "test-token"


def _body(structure: dict) -> dict:
    keys = ("components", "channel_components", "wiring", "ports")
    return {"base_sha256": structure["sha256"], **{k: structure[k] for k in keys}}


def test_build_area_structure_flow(tmp_path):
    """A copy of a built-in model is read with its parent's structure, saved unchanged stays
    inherited, an edit writes the lists whole and a dropped inherited port as null, and a stale
    hash or a built-in definition is refused."""
    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        my_id = c.post("/api/definitions/keysight-34465a/copy").json()["data"]["id"]
        url = f"/api/definitions/{my_id}/structure"

        # read: the parent's structure, marked inherited
        first = c.get(url).json()
        assert first["editable"] and first["extends"] == "keysight-34465a"
        assert set(first["inherited"]) == {"components", "wiring", "ports"}
        names = [x["name"] for x in first["components"]]
        assert "input" in names and "meter" in names
        assert first["ports"]["instrument"]["IN"] == "input.pin"

        # save unchanged: the file still only extends the model
        same = c.put(url, json=_body(first)).json()
        path = tmp_path / "instruments" / f"{my_id}.yaml"
        on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "components" not in on_disk and "ports" not in on_disk and "wiring" not in on_disk
        assert set(same["inherited"]) == {"components", "wiring", "ports"}

        # edit: add a component and a wire, drop the SENSE port
        body = _body(same)
        body["components"].append({"name": "out", "type": "output_stage", "params": {"z_out": 50.0}})
        body["wiring"]["instrument"].append(["path.out", "out.in"])
        del body["ports"]["instrument"]["SENSE"]
        body["ports"]["instrument"]["OUT"] = "out.pin"
        edited = c.put(url, json=body).json()
        on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert on_disk["components"][-1] == {"name": "out", "type": "output_stage", "params": {"z_out": 50.0}}
        assert ["path.out", "out.in"] in on_disk["wiring"]["instrument"]
        assert on_disk["ports"]["instrument"] == {"SENSE": None, "OUT": "out.pin"}
        shunt = next(x for x in on_disk["components"] if x["name"] == "input")["params"]["shunt"]
        assert 0.001 in shunt  # a table's number keys survive the JSON round trip
        assert edited["inherited"] == []
        assert (
            "SENSE" not in edited["ports"]["instrument"]
            and edited["ports"]["instrument"]["IN"] == "input.pin"
        )
        loaded = DefinitionLibrary([tmp_path / "instruments"])(my_id)
        assert loaded.ports.instrument == {"IN": "input.pin", "OUT": "out.pin"}

        # the save is a version whose change log names the new component
        history = c.get(f"/api/definitions/{my_id}/history").json()
        assert history[0]["source"] == "ui"
        assert any(ch["target"] == "out" for ch in history[0]["changes"])

        # refused: a stale hash (409), a built-in definition (403), an unknown one (404)
        assert c.put(url, json=_body(first)).status_code == 409
        builtin = c.get("/api/definitions/keysight-34465a/structure").json()
        assert not builtin["editable"]
        assert c.put("/api/definitions/keysight-34465a/structure", json=_body(builtin)).status_code == 403
        assert c.get("/api/definitions/no-such-thing/structure").status_code == 404
