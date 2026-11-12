"""Capture the Web internals view's fixtures from the engine: never a hand-written graph or signal.

Two benches are opened through the HTTP API, and for each a fixture is written beside the view
(web/src/features/workbench/internals/):

- ``internals.fixture.json`` — examples/benches/siglent_scope_rc (an SDS2104X Plus on an RC
  low-pass): the internal probe (signal-chain.md 6) at an analog port with a signal, a sample stream
  (the ADC), and a channel with nothing connected (why it reads 0).
- ``specan.fixture.json`` — web/e2e/fixtures/benches/sa-two-tone (two tones 1 MHz apart on the
  digital-IF analyzer, #959): the mixer's IF output with the third-order products, the IF ADC, the
  down-converter's IQ stream and the transform's bins at the center frequency, and the IQ stream
  again tuned to the marker.

Each holds the instrument as the workbench describes it, its internals (components, wiring with each
wire's medium, rate and format, the definition's reference, a receiver's tuning context), the
probes, and, for every signal or sample-stream output, the thumbnail the diagram draws on its
block. The stories, the layout tests and the probe panel's tests read them. Re-run after a change
to the model, the benches or the API:

    uv run python scripts/capture_internals_fixture.py                       # both
    uv run python scripts/capture_internals_fixture.py specan.fixture.json   # one
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from simubench.server.app import create_app
from simubench.server.manager import SessionManager

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "src" / "features" / "workbench" / "internals"


@dataclass(frozen=True)
class Capture:
    bench: Path
    out: Path
    instrument: str
    #: The probed ports; ``port@hz`` reads the port tuned to that frequency.
    probes: tuple[str, ...]
    #: Commands sent before the capture, as (instrument, command).
    setup: tuple[tuple[str, str], ...] = ()
    #: Definitions the bench needs besides the built-in ones.
    definitions: Path | None = None
    #: Significant digits kept.
    digits: int = 6


CAPTURES = (
    # The bench leaves the generator off; the probes are captured with its output on.
    Capture(
        bench=ROOT / "examples" / "benches" / "siglent_scope_rc.bench.yaml",
        out=WEB / "internals.fixture.json",
        instrument="scope",
        probes=("ch1.front.out", "adc.out1", "ch2.front.out"),
        setup=(("awg", "C1:OUTPut ON"),),
    ),
    Capture(
        bench=ROOT / "web" / "e2e" / "fixtures" / "benches" / "sa-two-tone.bench.yaml",
        out=WEB / "specan.fixture.json",
        instrument="sa",
        probes=("mixer.out", "adc.out", "ddc.out", "rbw.out", "ddc.out@1e9"),
        definitions=ROOT / "validation" / "instruments",
        digits=4,
    ),
)
#: A list of numbers on one line (the probes' samples and spectra would otherwise be a line each).
_NUMBERS = re.compile(r"\[\s*(-?[\d.eE+-]+(?:,\s*-?[\d.eE+-]+)*)\s*\]")


#: Digits a frequency keeps: two tones 1 MHz apart at 1 GHz differ in the fourth.
_HZ_DIGITS = 10


def _round(value: Any, digits: int = 6) -> Any:
    """Floats to a few significant digits: the fixture stays small and diffs stay readable.
    Frequencies (keys ending in ``_hz``) keep theirs."""
    if isinstance(value, float):
        return float(f"{value:.{digits}g}")
    if isinstance(value, list):
        return [_round(v, digits) for v in value]
    if isinstance(value, dict):
        return {
            k: _round(v, max(digits, _HZ_DIGITS) if k.endswith("_hz") else digits) for k, v in value.items()
        }
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


def capture(cap: Capture) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        shutil.copy(cap.bench, workspace / cap.bench.name)
        if cap.definitions:
            shutil.copytree(cap.definitions, workspace / "instruments")
        manager = SessionManager(workspace, scpi_base_port=None)
        with TestClient(create_app(manager, token=None, web_dist=Path("/nonexistent"))) as c:
            c.base_url = "http://127.0.0.1"
            start = c.post("/api/sessions", json={"bench_path": cap.bench.name})
            assert start.status_code < 300, start.text
            sid = start.json()["id"]
            for name, command in cap.setup:
                c.post(
                    f"/api/sessions/{sid}/instruments/{name}/scpi", json={"command": command}
                ).raise_for_status()
            session = c.get(f"/api/sessions/{sid}").json()
            instrument = next(i for i in session["instruments"] if i["name"] == cap.instrument)
            base = f"/api/sessions/{sid}/instruments/{cap.instrument}/internals"
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
                signals[port] = _thumbnail(_round(r.json(), cap.digits))
                if port in cap.probes:
                    probes[port] = r.json()
            for key in cap.probes:
                if "@" in key:
                    port, hz = key.split("@")
                    r = c.get(f"{base}/probe", params={"port": port, "tune_hz": float(hz)})
                    r.raise_for_status()
                    probes[key] = r.json()
            c.delete(f"/api/sessions/{sid}")
    assert set(probes) == set(cap.probes), "a probed port is not an output of the model"
    data = {
        "instrument": instrument,
        "internals": internals,
        "probes": _round(probes, cap.digits),
        "signals": signals,
    }
    text = json.dumps(data, ensure_ascii=False, indent=2)
    text = _NUMBERS.sub(lambda m: "[" + ", ".join(v.strip() for v in m.group(1).split(",")) + "]", text)
    cap.out.write_text(text + "\n", encoding="utf-8")
    print(f"wrote {cap.out.relative_to(ROOT)}")


def main(argv: list[str]) -> int:
    """Every fixture, or only those named (``specan.fixture.json``): the oscilloscope's bench runs
    on real time, so re-capturing it moves its instant — and the stories' baselines with it."""
    wanted = [cap for cap in CAPTURES if not argv or cap.out.name in argv]
    assert wanted, f"no such fixture; known: {', '.join(cap.out.name for cap in CAPTURES)}"
    for cap in wanted:
        capture(cap)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
