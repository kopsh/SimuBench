"""The Web build area's structure of a definition (#1201, instrument-authoring.md 9): the effective
structure read with extends merged in, and a whole structure written back so that the file keeps
only what it does not inherit (D59)."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from simubench.library.loader import DefinitionLibrary
from simubench.server.app import create_app
from simubench.server.manager import SessionManager

TOKEN = "test-token"


KEYS = ("components", "channel_components", "wiring", "ports", "state", "channel_state", "commands")
INHERITED = {"components", "wiring", "ports", "state", "commands"}


def _body(structure: dict) -> dict:
    keys = KEYS
    return copy.deepcopy({"base_sha256": structure["sha256"], **{k: structure[k] for k in keys}})


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
        assert set(first["inherited"]) == INHERITED
        names = [x["name"] for x in first["components"]]
        assert "input" in names and "meter" in names
        assert first["ports"]["instrument"]["IN"] == "input.pin"

        # save unchanged: the file still only extends the model
        same = c.put(url, json=_body(first)).json()
        path = tmp_path / "instruments" / f"{my_id}.yaml"
        on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert not set(KEYS) & set(on_disk)  # every item, commands and settings too, round-trips
        assert set(same["inherited"]) == INHERITED

        # edit: add a component and a wire, drop the SENSE port
        body = _body(same)
        body["components"].append(
            {"name": "out", "type": "output_stage", "label": "输出", "params": {"z_out": 50.0}}
        )
        body["wiring"]["instrument"].append(["path.out", "out.in"])
        del body["ports"]["instrument"]["SENSE"]
        body["ports"]["instrument"]["OUT"] = "out.pin"
        edited = c.put(url, json=body).json()
        on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
        # a block's own label is kept, and no empty label or description is written (#1261)
        assert on_disk["components"][-1] == {
            "name": "out",
            "type": "output_stage",
            "label": "输出",
            "params": {"z_out": 50.0},
        }
        assert next(x for x in edited["components"] if x["name"] == "out")["label"] == "输出"
        assert ["path.out", "out.in"] in on_disk["wiring"]["instrument"]
        assert on_disk["ports"]["instrument"] == {"SENSE": None, "OUT": "out.pin"}
        shunt = next(x for x in on_disk["components"] if x["name"] == "input")["params"]["shunt"]
        assert 0.001 in shunt  # a table's number keys survive the JSON round trip

        assert set(edited["inherited"]) == {"state", "commands"}
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
        # a row added to a number-keyed table in the Web (#1214) gets a number key too
        body = _body(edited)
        table = next(x for x in body["components"] if x["name"] == "input")["params"]["shunt"]
        table["30"] = 0.01
        edited = c.put(url, json=body).json()
        on_disk = yaml.safe_load(path.read_text(encoding="utf-8"))
        shunt = next(x for x in on_disk["components"] if x["name"] == "input")["params"]["shunt"]
        assert shunt[30] == 0.01 and "30" not in shunt and 0.001 in shunt

        # refused: a stale hash (409), a built-in definition (403), an unknown one (404)
        assert c.put(url, json=_body(first)).status_code == 409
        builtin = c.get("/api/definitions/keysight-34465a/structure").json()
        assert not builtin["editable"]
        assert c.put("/api/definitions/keysight-34465a/structure", json=_body(builtin)).status_code == 403
        assert c.get("/api/definitions/no-such-thing/structure").status_code == 404


def test_build_area_settings_and_commands(tmp_path):
    """#1206: a setting declared, a parameter bound to it and a command for it are written over
    what the copy inherits — the setting by field, a dropped inherited field as null, the commands
    as one list — and the instrument then answers the command."""
    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        my_id = c.post("/api/definitions/keysight-34465a/copy").json()["data"]["id"]
        url = f"/api/definitions/{my_id}/structure"
        first = c.get(url).json()
        nplc = first["state"]["sense.nplc"]
        assert nplc["type"] == "float" and "label" in nplc

        body = _body(first)
        body["state"]["out.level"] = {"type": "float", "unit": "V", "min": -10, "max": 10, "default": 0}
        del body["state"]["sense.nplc"]["label"]
        body["components"].append({"name": "out", "type": "output_stage", "params": {"z_out": "$out.level"}})
        body["commands"].append({"header": ":OUTPut:LEVel", "state": "out.level"})
        saved = c.put(url, json=body).json()
        on_disk = yaml.safe_load((tmp_path / "instruments" / f"{my_id}.yaml").read_text(encoding="utf-8"))
        assert on_disk["state"] == {
            "out.level": {"type": "float", "unit": "V", "min": -10, "max": 10, "default": 0},
            "sense.nplc": {"label": None},
        }
        assert on_disk["commands"][-1] == {"header": ":OUTPut:LEVel", "state": "out.level"}
        assert len(on_disk["commands"]) == len(first["commands"]) + 1
        assert "label" not in saved["state"]["sense.nplc"] and saved["state"]["sense.nplc"]["type"] == "float"
        assert set(saved["inherited"]) == {"wiring", "ports"}  # untouched: still the parent's

        # the copy runs the new command
        sid = c.post(f"/api/try/{my_id}").json()["session_id"]
        scpi = f"/api/try/{sid}/scpi"
        assert c.post(scpi, json={"command": ":OUTPut:LEVel 2.5"}).status_code == 200
        reply = c.post(scpi, json={"command": ":OUTPut:LEVel?"}).json()
        assert float(reply["response"]) == 2.5


def test_build_area_layout_is_kept_beside_the_definition(tmp_path):
    """#1207: positions live in instruments/<id>.layout.json — read back as written, no version
    of the definition, none for a built-in model, and a broken file reads as no positions."""
    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        my_id = c.post("/api/definitions/keysight-34465a/copy").json()["data"]["id"]
        url = f"/api/definitions/{my_id}/layout"
        assert c.get(url).json() == {"positions": {}}
        body = {"positions": {"i:input": {"x": 10, "y": 20.5}, "c:inp": {"x": -3, "y": 0}}}
        assert c.put(url, json=body).status_code == 200
        assert c.get(url).json() == body
        assert (tmp_path / "instruments" / f"{my_id}.layout.json").is_file()
        assert len(c.get(f"/api/definitions/{my_id}/history").json()) == 1  # only the copy
        assert c.put("/api/definitions/keysight-34465a/layout", json=body).status_code == 403
        (tmp_path / "instruments" / f"{my_id}.layout.json").write_text("{not json", encoding="utf-8")
        assert c.get(url).json() == {"positions": {}}
        assert c.get(f"/api/definitions/{my_id}").status_code == 200  # the loader ignores the file
