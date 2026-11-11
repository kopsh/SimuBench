"""What ships: built-in definitions are real models only; ``sb-*`` stay in ``validation/`` (D42)."""

from __future__ import annotations

from pathlib import Path

from simubench.library.loader import BUILTIN_DIR, COVERAGE_SUFFIX, DEFINITIONS_ENV, DefinitionLibrary

ROOT = Path(__file__).resolve().parents[1]


def names_sb_id(line: str) -> bool:
    """Whether the line names an ``sb-*`` validation definition: ``sb-`` at the start of a word,
    then a lower-case letter or digit (``sb-dso-4``; ``usb-c`` and ``sb-`` alone are not)."""
    start = line.find("sb-")
    while start >= 0:
        before = line[start - 1] if start else " "
        after = line[start + 3 : start + 4]
        if not (before.isalnum() or before == "_") and after and (after.isdigit() or "a" <= after <= "z"):
            return True
        start = line.find("sb-", start + 1)
    return False


def test_builtin_definitions_are_real_models():
    files = [p for p in BUILTIN_DIR.glob("*.yaml") if not p.name.endswith(COVERAGE_SUFFIX)]
    assert files
    lib = DefinitionLibrary()
    for p in files:
        assert not p.stem.startswith("sb-"), p.name
        assert lib.get(p.stem).reference is not None, f"{p.name}: a built-in definition needs `reference`"


def test_validation_definitions_load_only_through_the_search_path(monkeypatch):
    monkeypatch.delenv(DEFINITIONS_ENV, raising=False)
    ids = DefinitionLibrary().ids()
    assert ids and not [i for i in ids if i.startswith("sb-")]
    monkeypatch.setenv(DEFINITIONS_ENV, str(ROOT / "validation" / "instruments"))
    assert "sb-dso-4" in DefinitionLibrary().ids()


def test_examples_use_real_models_only():
    hits = [
        f"{p.relative_to(ROOT).as_posix()}:{n}"
        for p in sorted((ROOT / "examples").rglob("*"))
        if p.is_file() and p.suffix in {".yaml", ".yml", ".json", ".py", ".md"}
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if names_sb_id(line)
    ]
    assert not hits, f"examples/ refers to sb-* validation definitions: {hits}"


def test_validation_workspace_serves_sb_benches_without_the_search_path(monkeypatch, tmp_path):
    """``simubench serve --workspace validation``: its ``instruments/`` folder holds the definitions
    for starting a bench and replaying its run record."""
    from fastapi.testclient import TestClient

    from simubench.server.app import create_app
    from simubench.server.manager import SessionManager

    monkeypatch.delenv(DEFINITIONS_ENV, raising=False)
    manager = SessionManager(ROOT / "validation", scpi_base_port=None, record_dir=tmp_path)
    app = create_app(manager, web_dist=Path("/nonexistent"))
    with TestClient(app, base_url="http://127.0.0.1") as c:
        models = {i["id"] for i in c.get("/api/catalog").json()["instruments"]}
        assert {"sb-dso-4", "siglent-sds2104xplus"} <= models
        r = c.post("/api/sessions", json={"bench_path": "benches/awg_rc_scope.bench.yaml"})
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        c.post(f"/api/sessions/{sid}/instruments/scope/scpi", json={"command": "*IDN?"})
        c.delete(f"/api/sessions/{sid}")
        [run] = c.get("/api/runs").json()
        r = c.post(f"/api/runs/{run['run_id']}/replay")
        assert r.status_code == 200 and r.json()["ok"], r.text


def test_the_sb_id_check_sees_a_validation_id_only():
    assert names_sb_id("definition: sb-dso-4") and names_sb_id("sb-9")
    assert not names_sb_id("usb-c cable") and not names_sb_id("sb- alone") and not names_sb_id("sb-Upper")
