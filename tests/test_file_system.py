"""An instrument's mass storage (#1069): paths stay in the instrument's folder, limits hold, files
persist across runs when the bench keeps them, and a run record checks the files a replay writes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from simubench.api import bench_files_dir, open_bench
from simubench.engine.replay import replay
from simubench.library.components import files as F
from simubench.models.bench import BenchDefinition

BENCH = {"schema": "simubench.bench/2", "name": "t", "instruments": {"awg": {"model": "keysight-33622a"}}}


def session(**kw):
    return open_bench(BenchDefinition.model_validate(BENCH), **kw)


def ask(s, text: str) -> bytes | None:
    s.write("awg", text.encode() + b"\n")
    return s.read("awg") if "?" in text.split()[0] else None


def last_error(s) -> int:
    return int(ask(s, "SYST:ERR?").split(b",")[0])


def test_parent_folders_are_refused():
    with pytest.raises(F.FileNameError):
        F.split_path("INT:\\a\\..\\..\\b")
    assert F.split_path("INT:\\a\\.\\b.csv") == ("INT", True, ["a", "b.csv"])
    assert F.split_path("a/b") == (None, False, ["a", "b"])


@pytest.mark.parametrize(
    "path",
    ['"INT:\\..\\..\\etc"', '"..\\x"', '"INT:\\a:b"', '"INT:\\a<b"', '"C:\\Windows"', '"/etc/passwd/.."'],
)
def test_a_path_that_would_leave_the_instruments_folder_is_a_file_error(path):
    with session() as s:
        ask(s, f"MMEM:CDIR {path}")
        assert last_error(s) in (-256, -257)
        assert ask(s, "MMEM:CDIR?") == b'"INT:\\"\n'


def test_a_host_path_lands_inside_the_instruments_folder():
    with session() as s:
        ask(s, 'MMEM:DOWN:FNAM "/tmp/escape.txt"')  # an absolute path on the current drive
        assert last_error(s) == -256  # INT:\tmp does not exist
        root = s.instrument_files("awg")
        assert not any(root.rglob("escape.txt"))


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need privileges on Windows")
def test_a_symbolic_link_is_never_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    with session(files_dir=tmp_path / "files") as s:
        root = s.instrument_files("awg")
        ask(s, "MMEM:CAT?")  # the drive folders exist
        (root / "INT" / "link").symlink_to(outside, target_is_directory=True)
        assert ask(s, 'MMEM:UPL? "INT:\\link\\secret.txt"') is None
        assert last_error(s) == -257
        ask(s, 'MMEM:DOWN:FNAM "INT:\\link\\new.txt"')
        assert last_error(s) == -257
        assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]
        assert b"link" not in ask(s, 'MMEM:CAT? "INT:\\"')


def test_a_file_above_the_limit_or_a_full_drive_is_refused(monkeypatch):
    monkeypatch.setattr(F, "MAX_FILE_BYTES", 10)
    monkeypatch.setattr(F, "MAX_TOTAL_BYTES", 15)
    with session() as s:
        ask(s, 'MMEM:DOWN:FNAM "INT:\\a"')
        ask(s, "MMEM:DOWN:DATA #211" + "x" * 11)
        assert last_error(s) == -257  # file too large
        ask(s, "MMEM:DOWN:DATA #210" + "x" * 10)
        ask(s, 'MMEM:COPY "INT:\\a","INT:\\b"')
        assert last_error(s) == -254  # media full
        ask(s, "MMEM:DOWN:DATA #210" + "y" * 10)  # replacing a file counts only the difference
        assert last_error(s) == 0
        assert ask(s, 'MMEM:UPL? "INT:\\a"') == b"#210" + b"y" * 10 + b"\n"


def test_names_are_matched_without_regard_to_case():
    with session() as s:
        ask(s, 'MMEM:MDIR "INT:\\Data"')
        ask(s, 'MMEM:DOWN:FNAM "INT:\\DATA\\Run1.CSV"')
        ask(s, "MMEM:DOWN:DATA #13abc")
        assert ask(s, 'MMEM:UPL? "int:\\data\\run1.csv"') == b"#13abc\n"
        assert ask(s, 'MMEM:CAT? "INT:\\"') == b'+3,+999999997,"Data,FOLD,0"\n'


def test_files_persist_across_runs_of_a_bench(tmp_path):
    bench = tmp_path / "b.bench.yaml"
    bench.write_text(json.dumps(BENCH), encoding="utf-8")
    where = bench_files_dir(tmp_path, bench)
    assert where == tmp_path / ".simubench" / "files" / "b.bench"
    with open_bench(bench, files_dir=where) as s:
        ask(s, 'MMEM:DOWN:FNAM "USB:\\kept.csv"')
        ask(s, "MMEM:DOWN:DATA #12ok")
    assert (where / "awg" / "USB" / "kept.csv").read_bytes() == b"ok"
    with open_bench(bench, files_dir=where) as s:
        assert ask(s, 'MMEM:UPL? "USB:\\kept.csv"') == b"#12ok\n"


def test_without_a_files_folder_the_files_go_with_the_session():
    s = session()
    ask(s, 'MMEM:DOWN:FNAM "INT:\\t.csv"')
    root = s.instrument_files("awg")
    assert (root / "INT" / "t.csv").exists()
    s.close()
    assert not root.exists()


def test_a_written_file_is_an_event_and_the_record_checks_it(tmp_path):
    s = session(record_dir=tmp_path)
    ask(s, 'MMEM:DOWN:FNAM "INT:\\r.csv"')
    ask(s, "MMEM:DOWN:DATA #151,2,3")
    s.close()
    rec = next(tmp_path.glob("run-*.jsonl"))
    events = [json.loads(x) for x in rec.read_text(encoding="utf-8").splitlines()]
    assert events[0]["files"] == {}
    writes = [e for e in events if e.get("kind") == "file" and e.get("action") == "write"]
    assert [(e["path"], e["size"]) for e in writes] == [("INT:\\r.csv", 0), ("INT:\\r.csv", 5)]
    assert replay(rec).ok
    # a record whose file differs from what the commands write now is a mismatch
    for e in events:
        if e.get("kind") == "file" and e.get("size") == 5:
            e["sha256"] = "0" * 64
    rec.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    report = replay(rec)
    assert not report.ok and "r.csv" in report.mismatches[0].expected


def test_a_run_that_began_with_files_warns_on_replay(tmp_path):
    files = tmp_path / "files"
    with session(files_dir=files) as s:
        ask(s, 'MMEM:DOWN:FNAM "INT:\\old.csv"')
    s = session(files_dir=files, record_dir=tmp_path / "rec")
    ask(s, "MMEM:CDIR?")
    s.close()
    rec = next((tmp_path / "rec").glob("run-*.jsonl"))
    header = json.loads(rec.read_text(encoding="utf-8").splitlines()[0])
    assert list(header["files"]["awg"]) == ["INT/old.csv"]
    assert any("awg held 1 file" in w for w in replay(rec).warnings)


def test_the_files_are_kept_apart_per_instrument(tmp_path):
    assert Path(tmp_path).is_dir()
    bench = {**BENCH, "instruments": {"a": {"model": "keysight-33622a"}, "b": {"model": "keysight-33622a"}}}
    with open_bench(BenchDefinition.model_validate(bench), files_dir=tmp_path) as s:
        s.write("a", b'MMEM:DOWN:FNAM "INT:\\only-a"\n')
        s.write("b", b"MMEM:CAT?\n")
        assert b"only-a" not in s.read("b")
    assert (tmp_path / "a" / "INT" / "only-a").exists() and not (tmp_path / "b" / "INT" / "only-a").exists()


def test_the_api_lists_and_downloads_an_instruments_files(tmp_path):
    from fastapi.testclient import TestClient

    from simubench.server.app import create_app
    from simubench.server.manager import SessionManager

    (tmp_path / "b.bench.yaml").write_text(json.dumps(BENCH), encoding="utf-8")
    manager = SessionManager(tmp_path, scpi_base_port=None)
    with TestClient(create_app(manager, token=None, web_dist=Path("/nonexistent"))) as c:
        c.base_url = "http://127.0.0.1"
        sid = c.post("/api/sessions", json={"bench_path": "b.bench.yaml"}).json()["id"]
        bench = manager.sessions[sid].session
        bench.write("awg", b'MMEM:DOWN:FNAM "USB:\\x.csv"\n')
        bench.write("awg", b"MMEM:DOWN:DATA #12hi\n")
        url = f"/api/sessions/{sid}/instruments/awg/files"
        listed = c.get(url).json()["files"]
        assert [(f["path"], f["size"]) for f in listed] == [("USB/x.csv", 2)]
        got = c.get(url + "/content", params={"path": "USB/x.csv"})
        assert got.status_code == 200 and got.content == b"hi"
        assert c.get(url + "/content", params={"path": "../../b.bench.yaml"}).status_code == 403
        assert c.get(url + "/content", params={"path": "USB/none"}).status_code == 404
    # kept in the workspace for the next run (#1069)
    assert (tmp_path / ".simubench" / "files" / "b.bench" / "awg" / "USB" / "x.csv").read_bytes() == b"hi"
