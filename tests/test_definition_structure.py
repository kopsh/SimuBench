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
        # the command table stays the parent's: the new command is one per-command entry (D59)
        assert "commands" not in on_disk
        assert on_disk["command_overrides"] == {":OUTPut:LEVel": {"state": "out.level"}}
        assert len(saved["commands"]) == len(first["commands"]) + 1
        assert "label" not in saved["state"]["sense.nplc"] and saved["state"]["sense.nplc"]["type"] == "float"
        assert set(saved["inherited"]) == {"wiring", "ports", "commands"}  # still the parent's

        # a changed and a dropped inherited command are entries too; the copy runs the result
        body = _body(saved)
        dropped = body["commands"].pop(0)["header"]
        changed = next(x for x in body["commands"] if x.get("state") == "sense.nplc")
        changed["header"] = changed["header"]  # same header, a field changed below
        changed["query"] = False
        c.put(url, json=body).raise_for_status()
        on_disk = yaml.safe_load((tmp_path / "instruments" / f"{my_id}.yaml").read_text(encoding="utf-8"))
        assert on_disk["command_overrides"][dropped] is None
        assert on_disk["command_overrides"][changed["header"]]["query"] is False
        assert len(on_disk["command_overrides"]) == 3

        # the copy runs the new command
        sid = c.post(f"/api/try/{my_id}").json()["session_id"]
        scpi = f"/api/try/{sid}/scpi"
        assert c.post(scpi, json={"command": ":OUTPut:LEVel 2.5"}).status_code == 200
        reply = c.post(scpi, json={"command": ":OUTPut:LEVel?"}).json()
        assert float(reply["response"]) == 2.5


def test_build_area_keeps_infinite_values(tmp_path):
    """JSON has no infinity: the structure spells one "INF"/"-INF" (D60) and the write back reads it
    as the number, so editing one setting of a UXR1104B copy leaves the inherited
    ``display.persistence`` (default -inf, keywords MINimum/INFinite) alone instead of writing its
    infinities back as null deletion markers; an infinity typed in the Web is written as one."""
    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        my_id = c.post("/api/definitions/keysight-uxr1104b/copy").json()["data"]["id"]
        url = f"/api/definitions/{my_id}/structure"
        first = c.get(url).json()
        persistence = first["state"]["display.persistence"]
        assert persistence["default"] == "-INF"
        assert persistence["special"] == {"MINimum": "-INF", "INFinite": "INF"}

        # guards: a null inside the structure is refused (it can only be a value JSON lost), and
        # nothing is saved; the static check names a power-on value the setting refuses
        lost = _body(first)
        lost["state"]["display.persistence"]["default"] = None
        refused = c.put(url, json=lost)
        assert refused.status_code == 422 and "display.persistence" in refused.json()["detail"]
        assert c.get(url).json()["sha256"] == first["sha256"]
        below = _body(first)
        below["state"]["display.persistence"]["min"] = 1.0
        below["state"]["display.persistence"]["default"] = 0.5
        checked = c.put(url, json=below).json()
        assert [d["pointer"] for d in checked["diagnostics"] if d["code"] == "E-DEF-STATE-INITIAL"] == [
            "/state/display.persistence/default"
        ]

        body = _body(checked)
        body["state"]["display.persistence"] = first["state"]["display.persistence"]
        body["state"]["timebase.scale"]["min"] = 1e-12
        body["state"]["probe.limit"] = {"type": "float", "min": 0, "max": "INF", "default": 1}
        c.put(url, json=body).raise_for_status()
        on_disk = yaml.safe_load((tmp_path / "instruments" / f"{my_id}.yaml").read_text(encoding="utf-8"))
        assert on_disk["state"]["timebase.scale"] == {"min": 1e-12}
        assert "display.persistence" not in on_disk["state"]
        assert on_disk["state"]["probe.limit"]["max"] == float("inf")

        sid = c.post(f"/api/try/{my_id}").json()["session_id"]
        scpi = f"/api/try/{sid}/scpi"
        assert c.post(scpi, json={"command": ":DISPlay:PERSistence?"}).json()["response"] == "MINimum"
        reply = c.post(scpi, json={"command": ":DISPlay:PERSistence INFinite"}).json()
        assert reply["errors"] == []
        assert c.post(scpi, json={"command": ":TIMebase:SCALe 2e-12"}).json()["errors"] == []


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


def test_copy_history_reads_what_the_instrument_runs(tmp_path):
    """A copy's versions are described after layering (extends merged): its creation says only what
    it is based on, a changed inherited limit reads old → new, and a command added in the Web is
    one item even though the file now holds the whole command list (D59)."""
    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        my_id = c.post("/api/definitions/keysight-34465a/copy").json()["data"]["id"]
        url = f"/api/definitions/{my_id}/structure"
        body = _body(c.get(url).json())
        old_max = body["state"]["sense.nplc"]["max"]
        body["state"]["sense.nplc"]["max"] = 10
        body = _body(c.put(url, json=body).json())
        body["commands"].append({"header": ":SYSTem:FAN", "state": "sense.nplc"})
        c.put(url, json=body).raise_for_status()

        def items(version: dict) -> list[tuple]:
            return [
                (x["kind"], x["action"], x["target"], x["detail"], x["old"], x["new"])
                for x in version["changes"]
            ]

        added, limit, created = c.get(f"/api/definitions/{my_id}/history").json()
        assert items(created) == [("extends", "added", "extends", None, None, "keysight-34465a")]
        assert items(limit) == [("state", "changed", "sense.nplc", "max", old_max, 10)]
        assert items(added) == [("command", "added", ":SYSTem:FAN", None, None, None)]


def test_a_new_instrument_from_a_template_or_blank_and_its_identity(tmp_path):
    """P2-5: the Web starts an instrument from a template's inputs (judged by the template's own
    schema and checks; an error saves nothing, a warning comes with the saved definition) or blank,
    refuses a taken id, and edits identity — on a copy only what differs from the parent."""
    manager = SessionManager(tmp_path, scpi_base_port=None)
    app = create_app(manager, token=TOKEN, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.headers["x-simubench-token"] = TOKEN
        listed = {t["id"]: t for t in c.get("/api/instrument-templates").json()}
        scope = listed["oscilloscope"]
        assert scope["schema"]["properties"]["identity"]["title_en"] == "Identity"
        inputs = copy.deepcopy(scope["example"])
        inputs["identity"] |= {"manufacturer": "Zhiyuan", "model": "ZY-DSO3104"}
        wrong = copy.deepcopy(inputs)
        wrong["vertical"]["table"][1]["v_div"] = 0.001
        refused = c.post(
            "/api/definitions/new",
            json={"definition_id": "zy", "template_id": "oscilloscope", "inputs": wrong},
        ).json()
        assert refused["saved"] is False and not (tmp_path / "instruments" / "zy.yaml").exists()
        assert [d["pointer"] for d in refused["diagnostics"]] == ["/template/inputs/vertical/table/1/v_div"]
        inputs["adc"]["bits"] = 12
        made = c.post(
            "/api/definitions/new",
            json={"definition_id": "zy", "template_id": "oscilloscope", "inputs": inputs},
        ).json()
        assert made["saved"] and made["document"]["ok"]
        assert [d["code"] for d in made["diagnostics"]] == ["W-TPL-ADC-CODES"]
        assert (
            c.post(
                "/api/definitions/new",
                json={"definition_id": "zy", "template_id": "oscilloscope", "inputs": inputs},
            ).status_code
            == 409
        )
        assert (
            c.post(
                "/api/definitions/new",
                json={
                    "definition_id": "keysight-34465a",
                    "instrument_class": "multimeter",
                    "manufacturer": "A",
                    "model": "B",
                },
            ).status_code
            == 409
        )
        blank = c.post(
            "/api/definitions/new",
            json={
                "definition_id": "acme-dmm",
                "instrument_class": "multimeter",
                "manufacturer": "Acme",
                "model": "DM-1",
            },
        ).json()
        assert blank["saved"] and blank["document"]["data"]["class"] == "multimeter"

        my_id = c.post("/api/definitions/keysight-uxr1104b/copy").json()["data"]["id"]
        sha = c.get(f"/api/definitions/{my_id}/document").json()["sha256"]
        saved = c.put(
            f"/api/definitions/{my_id}/identity",
            json={
                "base_sha256": sha,
                "manufacturer": "Keysight Technologies",
                "serial": "MY65000123",
                "firmware": "11.90.00001",
            },
        ).json()
        on_disk = yaml.safe_load((tmp_path / "instruments" / f"{my_id}.yaml").read_text(encoding="utf-8"))
        assert on_disk == {
            "schema": "simubench.instrument/2",
            "id": my_id,
            "extends": "keysight-uxr1104b",
            "identity": {"serial": "MY65000123", "firmware": "11.90.00001"},
        }
        assert [(x["kind"], x["detail"], x["new"]) for x in saved["changes"]] == [
            ("identity", "firmware", "11.90.00001"),
            ("identity", "serial", "MY65000123"),
        ]
        sid = c.post(f"/api/try/{my_id}").json()["session_id"]
        idn = c.post(f"/api/try/{sid}/scpi", json={"command": "*IDN?"}).json()["response"]
        assert idn == "Keysight Technologies,UXR1104B,MY65000123,11.90.00001"
