"""Capture the Web internals view's fixture from the engine: never a hand-written graph or signal.

Opens examples/benches/siglent_scope_rc (an SDS2104X Plus on an RC low-pass) through the HTTP API
and writes, for web/src/features/workbench/internals/internals.fixture.json: the instrument as the
workbench describes it, its internals (components, wiring with each wire's medium, rate and format,
the definition's reference), and the internal probe (signal-chain.md 6) at three ports — an analog
port with a signal, a sample stream (the ADC), and a channel with nothing connected (why it reads 0);
and, for every signal or sample-stream output, the thumbnail the diagram draws on its block.
The stories, the layout tests and the probe panel's tests read it. Re-run after a change to the
model, the bench or the API:

    uv run python scripts/capture_internals_fixture.py
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "examples" / "benches" / "siglent_scope_rc.bench.yaml"
OUT = ROOT / "web" / "src" / "features" / "workbench" / "internals" / "internals.fixture.json"
INSTRUMENT = "scope"
#: The probed ports: an analog port with the RC's output on it, the ADC's stream, an idle channel.
PROBES = ("ch1.front.out", "adc.out1", "ch2.front.out")
#: The bench leaves the generator off; the probes are captured with its output on.
SETUP = (("awg", "C1:OUTPut ON"),)
#: A list of numbers on one line (the probes' samples and spectra would otherwise be a line each).
_NUMBERS = re.compile(r"\[\s*(-?[\d.eE+-]+(?:,\s*-?[\d.eE+-]+)*)\s*\]")


def _round(value: Any) -> Any:
    """Floats to 6 significant digits: the fixture stays small and diffs stay readable."""
    if isinstance(value, float):
        return float(f"{value:.6g}")
    if isinstance(value, list):
        return [_round(v) for v in value]
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    return value


def _thumbnail(probe: dict[str, Any]) -> dict[str, Any]:
    """A probe's answer as the diagram's thumbnail: the same thinning as ``miniOf`` in the view's
    probe.ts (probe.test.ts holds the two together) — 96 points of an analytic waveform, or a
    stream's first 40 samples."""
    volts = probe["waveform"]["volts"]
    silent = all(v == 0 for v in volts)
    if probe["medium"] == "samples":
        return {"kind": "samples", "values": volts[:40], "silent": silent}
    step = max(1, -(-len(volts) // 96))
    return {"kind": "analog", "values": volts[::step], "silent": silent}


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        shutil.copy(BENCH, workspace / BENCH.name)
        manager = SessionManager(workspace, scpi_base_port=None)
        with TestClient(create_app(manager, token=None, web_dist=Path("/nonexistent"))) as c:
            c.base_url = "http://127.0.0.1"
            start = c.post("/api/sessions", json={"bench_path": BENCH.name})
            start.raise_for_status()
            sid = start.json()["id"]
            for name, command in SETUP:
                c.post(
                    f"/api/sessions/{sid}/instruments/{name}/scpi", json={"command": command}
                ).raise_for_status()
            session = c.get(f"/api/sessions/{sid}").json()
            instrument = next(i for i in session["instruments"] if i["name"] == INSTRUMENT)
            base = f"/api/sessions/{sid}/instruments/{INSTRUMENT}/internals"
            internals = c.get(base).json()
            probes = {}
            signals = {}
            outputs = [
                f"{comp['name']}.{p['name']}"
                for comp in internals["components"]
                for p in comp["ports"]
                if p["direction"] == "out" and p["medium"] in ("signal", "samples")
            ]
            for port in outputs:
                r = c.get(f"{base}/probe", params={"port": port})
                r.raise_for_status()
                signals[port] = _thumbnail(_round(r.json()))
                if port in PROBES:
                    probes[port] = r.json()
            c.delete(f"/api/sessions/{sid}")
    assert set(probes) == set(PROBES), "a probed port is not an output of the model"
    data = {"instrument": instrument, "internals": internals, "probes": _round(probes), "signals": signals}
    text = json.dumps(data, ensure_ascii=False, indent=2)
    text = _NUMBERS.sub(lambda m: "[" + ", ".join(v.strip() for v in m.group(1).split(",")) + "]", text)
    OUT.write_text(text + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
