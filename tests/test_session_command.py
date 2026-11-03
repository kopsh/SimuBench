"""``Session.command()``: one message is one action — its own reply, its own error-queue
entries, and one continuous lock hold from write to read (#121, #461). The MCP tools used to
send write and read as two actions, so a script on the instrument's SCPI port could take the
MCP reply in between."""

import threading
from pathlib import Path

import yaml

from simubench.api import open_bench

ROOT = Path(__file__).resolve().parents[1]

BENCH = {
    "schema": "simubench.bench/1",
    "name": "command",
    "instruments": {"lia": {"model": "sb-lia-1"}},
    "run": {"clock": "command", "seed": 3, "idle": "off"},
}


def open_command(tmp_path, name="command"):
    path = tmp_path / f"{name}.bench.yaml"
    path.write_text(yaml.safe_dump(BENCH), encoding="utf-8")
    return open_bench(path)


def test_command_returns_its_reply_and_only_its_own_errors(tmp_path):
    s = open_command(tmp_path)
    r = s.command("lia", "*IDN?")
    assert r.reply is not None and b"," in r.reply
    assert r.errors == []
    bad = s.command("lia", ":NOSUCH 1")
    assert bad.reply is None
    assert [e.code for e in bad.errors] == [-113]  # this command's error, not an older one


def test_command_holds_the_session_lock_across_write_and_read(tmp_path, monkeypatch):
    """The old shape (write action, then read action) releases the lock in between, and a script
    on the port can slip its own exchange in (#121); ``command()`` holds it from before its
    write event to after its read event."""
    s = open_command(tmp_path)
    seen = {"write": False, "read": False}
    violations: list[int] = []
    real = threading.RLock()
    depth = {"n": 0}

    class Tracked:
        def __enter__(self):
            real.acquire()
            depth["n"] += 1
            return self

        def __exit__(self, *exc):
            depth["n"] -= 1
            real.release()
            if seen["write"] and not seen["read"] and depth["n"] == 0:
                violations.append(depth["n"])
            return False

    def listener(ev: dict) -> None:
        if ev.get("client") != "mcp" or ev.get("kind") != "scpi":
            return
        if ev.get("dir") == "write":
            seen["write"] = True
        elif ev.get("dir") == "read":
            seen["read"] = True

    monkeypatch.setattr(s, "_lock", Tracked())
    s.subscribe(listener)
    r = s.command("lia", "*IDN?", client="mcp")
    assert r.reply is not None and b"," in r.reply
    assert seen == {"write": True, "read": True}
    assert violations == []


def test_no_other_client_gets_between_a_command_write_and_its_read(tmp_path):
    """A script on the instrument's SCPI port starts its own exchange as soon as MCP's write
    lands: it cannot complete until the whole command is done, and none of its events land
    between MCP's write and read events."""
    s = open_command(tmp_path)
    events: list[tuple] = []
    gate = threading.Event()

    def listener(ev: dict) -> None:
        events.append((ev.get("client"), ev.get("kind"), ev.get("dir")))
        if ev.get("client") == "mcp" and ev.get("dir") == "write":
            gate.set()

    s.subscribe(listener)

    foreign: list[bytes] = []

    def port_script() -> None:
        gate.wait(2)
        foreign.extend(s.exchange("lia", b"*OPC?", client="socket:127.0.0.1:4000"))

    t = threading.Thread(target=port_script)
    t.start()
    try:
        r = s.command("lia", "*IDN?", client="mcp")
    finally:
        gate.set()
        t.join(5)
    assert not t.is_alive()
    assert r.reply is not None and b"," in r.reply  # MCP's own reply
    assert foreign == [b"1\n"]  # the script's own reply
    write_i = events.index(("mcp", "scpi", "write"))
    read_i = events.index(("mcp", "scpi", "read"))
    assert events[write_i + 1 : read_i] == [], "another client ran inside the command"
