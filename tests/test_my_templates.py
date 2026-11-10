"""“Save as my template” (#875, D66): a bench instrument becomes a definition of its own — the
inheritance chain expanded (it runs exactly as the model it came from), the instance's settings
folded into the defaults, the identity carried over; and the file routes around it: copy a bench
(#874), restore a definition version (#875)."""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from simubench.engine.jsonpatch import PatchOp
from simubench.library.loader import BUILTIN_DIR, DefinitionLibrary
from simubench.models.definition import InstrumentDefinition
from simubench.server.app import create_app
from simubench.server.benchfiles import BenchFiles
from simubench.server.definitionfiles import DefinitionFiles
from simubench.server.manager import SessionManager
from simubench.server.mytemplates import fold_state, template_id

BENCH = "hello.bench.yaml"
BUILTINS = sorted(p.stem for p in BUILTIN_DIR.glob("*.yaml") if not p.name.endswith(".coverage.yaml"))


@pytest.fixture(scope="module")
def library():
    return DefinitionLibrary()


@pytest.mark.parametrize("def_id", BUILTINS)
def test_the_expanded_definition_is_the_model_it_came_from(tmp_path, library, def_id):
    data = DefinitionFiles(BenchFiles(tmp_path)).expanded(def_id)
    assert "extends" not in data
    snapshot = InstrumentDefinition.model_validate(data).model_dump(exclude={"extends"})
    original = library.get(def_id).model_dump(exclude={"extends"})
    assert snapshot == original


def test_settings_fold_into_defaults_and_reset_keeps_the_models_value():
    data = {
        "channels": {"count": 2},
        "state": {"mode": {"type": "enum", "default": "A"}},
        "channel_state": {
            "freq": {"type": "float", "default": 1.0},
            "amp": {"type": "float", "default": 1.0},
        },
    }
    left = fold_state(data, {"mode": "B", "ch1.freq": 5.0, "ch2.freq": 5.0, "ch1.amp": 2.0, "ghost": 1})
    assert data["state"]["mode"] == {"type": "enum", "default": "B", "rst": "A"}
    assert data["channel_state"]["freq"]["default"] == 5.0 and data["channel_state"]["freq"]["rst"] == 1.0
    assert data["channel_state"]["amp"]["default"] == 1.0  # only CH1 set: no default for both
    assert left == ["ghost", "ch1.amp"]
    assert template_id("我的示波器", "siglent-sds2104xplus") == "my-siglent-sds2104xplus"
    assert template_id("Scope A (lab 2)", "x") == "my-scope-a-lab-2"


@pytest.fixture
def client(tmp_path):
    manager = SessionManager(tmp_path, scpi_base_port=None, hislip_port=None, link_port=None)
    manager.files.create_hello("ui")
    app = create_app(manager, web_dist=tmp_path / "none")
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def test_save_as_template_copy_bench_and_restore_a_definition(client, tmp_path):
    doc = client.get("/api/benches/file", params={"path": BENCH}).json()
    edited = client.post(
        "/api/benches/edit",
        json={
            "path": BENCH,
            "base_sha256": doc["sha256"],
            "ops": [
                {"op": "set_identity", "instrument": "scope", "serial": "SN42", "idn": None, "firmware": None}
            ],
        },
    ).json()
    r = client.post(
        "/api/definitions/templates",
        json={
            "bench_path": BENCH,
            "base_sha256": edited["sha256"],
            "instrument": "scope",
            "name": "Lab scope",
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    new_id = out["definition"]["id"]
    assert new_id == "my-lab-scope" and out["based_on"] == "siglent-sds2104xplus" and out["definition"]["ok"]
    stored = yaml.safe_load((tmp_path / "instruments" / f"{new_id}.yaml").read_text(encoding="utf-8"))
    assert "extends" not in stored and stored["description"] == "Lab scope"
    assert stored["identity"]["serial"] == "SN42"
    assert stored["state"]["timebase.scale"]["default"] == 5e-7  # the instance's initial setting
    assert out["bench"]["data"]["instruments"]["scope"]["model"] == new_id and out["bench"]["ok"]
    # a second template of the same name gets its own id; a stale bench hash is a conflict
    stale = client.post(
        "/api/definitions/templates",
        json={
            "bench_path": BENCH,
            "base_sha256": edited["sha256"],
            "instrument": "scope",
            "name": "Lab scope",
        },
    )
    assert stale.status_code == 409
    again = client.post(
        "/api/definitions/templates",
        json={
            "bench_path": BENCH,
            "base_sha256": out["bench"]["sha256"],
            "instrument": "awg",
            "name": "Lab scope",
        },
    ).json()
    assert again["definition"]["id"] == "my-lab-scope-2"
    assert (
        client.post(
            "/api/definitions/templates",
            json={
                "bench_path": BENCH,
                "base_sha256": again["bench"]["sha256"],
                "instrument": "ghost",
                "name": "x",
            },
        ).status_code
        == 404
    )

    # restore a definition version (D35), as benches can
    d = client.get(f"/api/definitions/{new_id}/document").json()
    files = DefinitionFiles(client.app.state.manager.files)
    patch = TypeAdapter(list[PatchOp]).validate_python([{"op": "replace", "path": "/model", "value": "X"}])
    changed = files.edit(new_id, d["sha256"], patch, "agent")
    first = files.versions(new_id)[-1].seq
    restored = client.post(
        f"/api/definitions/{new_id}/restore", json={"seq": first, "base_sha256": changed.sha256}
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["data"]["model"] != "X" and files.versions(new_id)[0].source == "restore"

    # copy a bench: everything comes along, a free name beside it, never over an existing file
    copy = client.post("/api/benches/copy", json={"path": BENCH}).json()
    assert (
        copy["path"] == "hello-copy.bench.yaml" and copy["data"]["wires"] == again["bench"]["data"]["wires"]
    )
    assert copy["data"]["name"] == "hello（副本）"
    assert client.post("/api/benches/copy", json={"path": BENCH}).json()["path"] == "hello-copy-2.bench.yaml"
    taken = client.post("/api/benches/copy", json={"path": BENCH, "new_path": "hello-copy.bench.yaml"})
    assert taken.status_code == 409
