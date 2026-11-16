"""The configurable oscilloscope acquisition (instrument-authoring.md 3.2, issue 44): record length,
trigger, run control, noise and the ADC are parameters of the definition, not code per model."""

from __future__ import annotations

import copy
import itertools
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from refcalc.noise import averaged_codes_sigma, quantized_sigma, sigma_interval

from simubench.api import open_bench
from simubench.engine import session as session_module
from simubench.engine.session import Session
from simubench.engine.stream import FrameRequest
from simubench.engine.validate_definition import validate_definition_data
from simubench.library.components.measurements import measure, top_base
from simubench.library.loader import DefinitionLibrary, load_yaml
from simubench.models.bench import BenchDefinition
from simubench.models.definition import InstrumentDefinition

ROOT = Path(__file__).resolve().parents[1]
LFCAL = ROOT / "examples" / "lowfreq-cal"
LECROY = load_yaml((LFCAL / "instruments" / "lfcal-reference-scope.yaml").read_text(encoding="utf-8"))
CALIBRATOR = load_yaml((LFCAL / "instruments" / "lfcal-scope-calibrator.yaml").read_text(encoding="utf-8"))
SIGLENT = ROOT / "examples" / "benches" / "siglent_scope_rc.bench.yaml"
DSOX = ROOT / "examples" / "benches" / "keysight-dsox2024a.bench.yaml"


def _lfcal(scope: dict):
    """The low-frequency calibration bench with ``scope`` as the reference scope's definition."""
    bench = BenchDefinition.model_validate(
        load_yaml((LFCAL / "lowfreq_cal.bench.yaml").read_text(encoding="utf-8"))
    )
    defs = {
        "lfcal-reference-scope": InstrumentDefinition.model_validate(scope),
        "lfcal-scope-calibrator": InstrumentDefinition.model_validate(CALIBRATOR),
    }
    session = Session(bench, defs.__getitem__, clock="command", seed=1, idle="off")
    for cmd in (":CHANnel1:BASE:AMPLitude 1", ":CHANnel1:BASE:FREQuency 1000", ":CHANnel1:OUTPut 1"):
        session.write("calibrator", cmd.encode() + b"\n")

    def query(cmd: str) -> str:
        session.write("scope", cmd.encode() + b"\n")
        out = session.read("scope") if "?" in cmd else None
        return out.decode().strip() if out is not None else ""

    for cmd in ("C1:TRA ON", "C1:CPL D50", "C1:VDIV 0.2", "MSIZ 10K", "TDIV 1E-3"):
        query(cmd)
    return session, query


def test_run_control_is_configuration():
    """The reference scope's TRMD behaves the same with the plain scope_acquisition component."""
    plain = copy.deepcopy(LECROY)
    acq = next(c for c in plain["components"] if c["name"] == "acq")
    acq["type"] = "scope_acquisition"
    plain["commands"] = [c for c in plain["commands"] if "acq.inspect" not in str(c.get("op"))]
    plain["commands"] = [c for c in plain["commands"] if "acq.pava" not in str(c.get("op"))]
    for definition in (LECROY, plain):
        session, query = _lfcal(definition)
        scope = session.instruments["scope"].components["acq"]
        query("C1:TRLV 10")  # above the signal: a single acquisition waits
        query("TRMD SINGLE")
        assert (query("TRMD?"), scope.acq_count) == ("TRMD SINGLE", 0)
        query("C1:TRLV 0")
        assert query("*OPC?") == "1"  # the pending acquisition is retried and completes
        assert (query("TRMD?"), scope.acq_count) == ("TRMD STOP", 1)
        query("TRMD AUTO")
        query("STOP")
        assert query("TRMD?") == "TRMD STOP"
        session.close()


def test_the_record_length_rules():
    session, _ = _lfcal(LECROY)
    scope = session.instruments["scope"].components
    acq, timebase = scope["acq"], scope["timebase"]  # the one-component form expands into modules
    assert acq._timing()[:2] == (10_000, pytest.approx(1e-6))  # exact: MSIZ points over ten divisions
    timebase._params.update(record_length="rate_ladder", points=30_000)
    points, dt, pre, _ = acq._timing()
    assert (points, dt, pre) == (20_000, pytest.approx(0.5e-6), 10_000)  # 3 MSa/s rounded down to 2
    timebase._params.update(delay=2e-3)  # the trigger 2 ms before the centre
    assert acq._timing()[2] == 6_000
    session.close()


def test_a_waiting_single_acquisition_completes_once_the_trigger_comes():
    """A read does not complete a waiting single acquisition (completion: pending, issue 134): the
    scope triggers when the signal crosses the level, and the record fills in virtual time."""
    session = open_bench(SIGLENT, clock="command", seed=1, idle="off")

    def query(cmd: str) -> str:
        session.write("scope", cmd.encode() + b"\n")
        out = session.read("scope") if "?" in cmd else None
        return out.decode(errors="replace").strip() if out is not None else ""

    session.write("awg", b"C1:OUTP ON\n")
    query(":TRIGger:EDGE:LEVel 0.15")  # above the signal
    query(":TRIGger:MODE SINGle")  # the running scope's last frame is its latest acquisition
    assert (query(":TRIGger:STATus?"), query(":ACQuire:NUMAcq?")) == ("Ready", "1")
    assert query("*OPC?") == ""  # no trigger: no reply, the client's read times out
    query(":WAVeform:DATA?")  # a read gets the previous record and does not trigger
    assert (query(":TRIGger:STATus?"), query(":ACQuire:NUMAcq?")) == ("Ready", "1")
    query(":TRIGger:EDGE:LEVel 0")  # now the signal crosses the level
    assert query("*OPC?") == "1"  # waits until the record after the trigger is full
    assert (query(":TRIGger:STATus?"), query(":ACQuire:NUMAcq?")) == ("Stop", "2")
    session.close()


def test_averaging_takes_the_time_of_its_acquisitions():
    session = open_bench(SIGLENT, clock="command", seed=1, idle="off")
    session.write("awg", b"C1:OUTP ON\n")
    acq = session.instruments["scope"].components["acq"]
    _, one, _ = acq.compute([1])
    session.write("scope", b":ACQuire:TYPE AVERage,16\n")
    _, sixteen, _ = acq.compute([1])
    overhead = round(float(acq.param("overhead_s")) * 1e9)
    assert sixteen - overhead == pytest.approx(16 * (one - overhead), rel=1e-6)
    session.close()


def test_an_average_beyond_the_model_is_refused_before_its_first_acquisition(monkeypatch):
    """#986, for a record computed whole as one frame (the session's fan-out limit lowered, the
    scope's own left, so the record is not deep): count x record samples beyond
    ``MAX_REQUEST_FANOUT`` is refused when the acquisition starts, not once the running count gets
    there — the ADC is not asked for a single frame, by the acquisition or by a measurement after it;
    a count within the limit still acquires. A deep record takes such an average and refuses only
    reads beyond the limit (#1012, test_scope_deep_record.py)."""
    session = open_bench(DSOX, clock="command", seed=1, idle="off")
    query = _noise_query(session)
    scope = session.instruments["scope"].components
    points = scope["acq"]._timing()[0]
    # The record's own request and 3 acquisitions: at most cap // points - 1 averages (#990).
    monkeypatch.setattr(session_module, "MAX_REQUEST_FANOUT", 4 * points)
    adc, calls = scope["adc"], []
    frames = adc.frames
    monkeypatch.setattr(adc, "frames", lambda port, request: calls.append(port) or frames(port, request))
    query(":ACQuire:TYPE AVERage;:ACQuire:COUNt 4;:DIGitize CHANnel1")
    assert (len(calls), query(":SYSTem:ERRor?")) == (0, '-221,"Settings conflict"')
    assert scope["acq"].records == {}  # nothing was acquired
    assert query(":MEASure:VPP? CHANnel1") == ""  # the measurement's own acquisition: refused as fast
    assert (len(calls), query(":SYSTem:ERRor?")) == (0, '-221,"Settings conflict"')
    query(":ACQuire:COUNt 3;:DIGitize CHANnel1")
    assert (len(calls), query(":SYSTem:ERRor?")) == (3, '+0,"No error"')
    session.close()


def _noise_query(session):
    def query(cmd: str) -> str:
        session.write("scope", cmd.encode() + b"\n")
        out = session.read("scope") if "?" in cmd else None
        return out.decode().strip() if out is not None else ""

    return query


def test_the_noise_and_processing_flow():
    """G4/D46, one flow: the input noise is the vertical division's own noise_table row - it
    rides the scale (the front-end gain sets it at the ADC) and the records match the table (F4);
    every averaged acquisition is noisy and quantized and their mean keeps the sub-code
    resolution, where averaging analog values and quantizing once would not; ERES,b is a boxcar
    of 4^b codes (at 2.0 bits the noise falls by four, the bandwidth with the window); peak
    detect keeps, of each interval's native codes, the exceptional one, and at the ADC's top rate
    there is nothing to oversample so the record is the plain one; the screen frame averages the
    same independent sub-acquisitions as the record (#971)."""
    session = open_bench(SIGLENT, clock="command", seed=1, idle="off")
    query = _noise_query(session)
    acq = session.instruments["scope"].components["acq"]
    # -- the noise comes from the division table (was test_noise_comes_from_the_division_table)
    for scale, sigma in ((0.5, 7.5e-3), (5e-3, 75e-6)):
        query(f":CHANnel1:SCALe {scale}")
        assert acq.vertical_of(1).input_noise_rms() == pytest.approx(sigma)
        records, _, _ = acq.compute([1])
        lo, hi = sigma_interval(quantized_sigma(sigma, scale / 30), records[1].volts.size)
        assert lo <= float(np.std(records[1].volts)) <= hi
    query(":CHANnel1:SCALe 7")  # not listed: the row of the largest division not above it
    assert acq.vertical_of(1).input_noise_rms() == pytest.approx(0.075)
    query(":CHANnel1:PROBe VALue,10;:CHANnel1:SCALe 5")  # 0.5 V/div at the input, x10 on screen
    assert acq.vertical_of(1).input_noise_rms() == pytest.approx(7.5e-3)  # input-referred
    records, _, _ = acq.compute([1])
    lo, hi = sigma_interval(quantized_sigma(0.075, 5 / 30), records[1].volts.size)
    assert lo <= float(np.std(records[1].volts)) <= hi  # display volts: the input rms times the probe ratio
    # -- the average of codes resolves below the ADC grid (was test_the_average_of_codes_resolves_below_the_adc_grid)
    query(":CHANnel1:PROBe VALue,1")  # bridge: back to 1x, the display range the act assumes
    query(":CHANnel1:SCALe 0.5;:ACQuire:TYPE AVERage,64")
    records, _, _ = acq.compute([1])
    lsb = 0.5 / 30
    # The mean of 64 records, each with the one-acquisition noise the table row quantizes to.
    sigma = quantized_sigma(7.5e-3, lsb) / 8
    lo, hi = sigma_interval(sigma, records[1].volts.size)
    assert lo <= float(np.std(records[1].volts)) <= hi
    assert float(np.std(records[1].volts)) < 0.25 * lsb  # the noise fell well below one code
    # -- the screen frame averages the same independent sub-acquisitions (#967 fixed shared noise)
    shown, _, _ = acq.compute(acq.displayed(), frame=1)
    lo, hi = sigma_interval(sigma, shown[1].volts.size)
    assert lo <= float(np.std(shown[1].volts)) <= hi
    # -- high resolution smooths codes and gains effective bits (was test_high_resolution_smooths_codes_and_gains_effective_bits)
    query(":ACQuire:TYPE NORMal")  # bridge: the previous act left AVERage,64; plain is unprocessed
    query(":CHANnel1:SCALe 0.5")
    plain, _, _ = acq.compute([1])
    query(":ACQuire:TYPE ERES,2.0")
    smoothed, _, _ = acq.compute([1])
    lsb = 0.5 / 30
    assert float(np.std(smoothed[1].volts)) == pytest.approx(float(np.std(plain[1].volts)) / 4, rel=0.05)
    assert float(np.std(smoothed[1].volts)) == pytest.approx(averaged_codes_sigma(7.5e-3, lsb, 16), rel=0.05)
    # -- peak detect keeps the excursions and the top rate keeps the record
    # (was test_peak_detect_keeps_the_excursions_and_the_top_rate_keeps_the_record)
    query(":ACQuire:TYPE NORMal")  # bridge: the previous act left ERES,2.0; plain is unprocessed
    query(":CHANnel1:SCALe 0.5")
    plain, _, _ = acq.compute([1])
    query(":ACQuire:TYPE PEAK")
    peaks, _, _ = acq.compute([1])
    assert float(np.std(peaks[1].volts)) > 1.5 * float(np.std(plain[1].volts))  # extremes of 64 codes
    query(":TIMebase:SCALe 5E-7")  # 10k points over 5 us: at the ADC's 2 GSa/s, m = 1
    query(":ACQuire:TYPE NORMal")
    plain, _, _ = acq.compute([1])
    query(":ACQuire:TYPE PEAK")
    peaks, _, _ = acq.compute([1])
    # Nothing to oversample: the peak record has the plain record's noise band (the type keeps
    # its own noise stream, so the realizations differ, the width does not).
    assert float(np.std(peaks[1].volts)) == pytest.approx(float(np.std(plain[1].volts)), rel=0.05)
    session.close()


def test_the_stream_blocks_carry_the_definition():
    """The expanded chain speaks the definition (signal-chain.md 4.2, #971): the ADC's frames are
    the codes kept in volts (D46) at the definition's bits and top rate, each sample on its code
    grid; peak detect oversamples the record interval toward the ADC's native rate, capped by
    peak_max_oversample so a deep record at a slow timebase stays feasible — a pulse narrower than
    the record interval / 64 is then not guaranteed to be kept — and the other modes take the
    record's rate."""
    session = open_bench(SIGLENT, clock="command", seed=1, idle="off")
    scope = session.instruments["scope"]
    adc, dec = scope.components["adc"], scope.components["dec"]
    rate = adc.top_rate({1})  # CH1 alone: not a rate pair, so the definition's top rate
    frame = adc.frames("out1", FrameRequest(0, 64, 1.0 / rate, key=("blocks",)))
    assert (frame.fmt.kind, frame.fmt.bits) == ("volts", int(adc.param("bits")))
    lsb = float(scope.get_state("ch1.scale")) / adc.codes_per_div()
    assert frame.fmt.lsb == pytest.approx(lsb)  # what one code step is worth, from the definition
    assert frame.dt_s == pytest.approx(1.0 / float(adc.param("max_rate")))
    assert np.allclose(frame.data / lsb, np.round(frame.data / lsb))  # the codes, on their grid
    session.write("scope", b":ACQuire:TYPE PEAK\n")
    session.write("scope", b":TIMebase:SCALe 1E-2\n")  # the record's 10 µs at 2 GSa/s: 20000 native samples
    assert (
        dec.oversample() == 64
    )  # capped by peak_max_oversample: 64 of the 20000 native samples, not the native rate
    assert dec.input_interval("out1") == pytest.approx(dec.sample_interval("out1") / 64)
    session.write("scope", b":ACQuire:TYPE NORMal\n")
    assert (
        dec.oversample() == 1
    )  # no oversampling off peak mode: the record's rate is what is asked of the ADC
    session.close()


def one_component(data: dict) -> dict:
    """The reference scope written the earlier, one-component way (it has used modules since G7):
    the timebase, trigger and ADC parameters back on the acquisition, no probe module — the form
    the macro expands into the modules when the definition loads."""
    earlier = copy.deepcopy(data)
    moves = {
        "timebase": {"record_length": "record_length", "points": "points"},
        "trigger": {"level": "trigger_level", "slope": "trigger_slope", "search_windows": "search_windows"},
        "adc": {"bits": "adc_bits", "divs": "adc_divs", "offset_sign": "adc_offset", "max_rate": "max_rate"},
    }
    params: dict[str, Any] = {}
    for name, renames in moves.items():
        module = next(c for c in earlier["components"] if c["name"] == name)
        earlier["components"].remove(module)
        params.update({old: module["params"][new] for new, old in renames.items()})
    acq = next(c for c in earlier["components"] if c["name"] == "acq")
    acq["params"] = {
        **params,
        **{
            k: v
            for k, v in acq["params"].items()
            if k not in ("horizontal", "trigger", "adc", "vertical", "probe")
        },
    }
    next(c for c in earlier["channel_components"] if c["name"] == "front")["type"] = "scope_frontend"
    earlier["channel_components"] = [c for c in earlier["channel_components"] if c["name"] != "probe"]
    earlier["wiring"] = {
        "channel": [["inp.out", "front.in"]],
        "instrument": [["ch{n}.front.out", "acq.in{n}"]],
    }
    return earlier


def test_channel_bindings_are_checked():
    bad = one_component(LECROY)
    acq = next(i for i, c in enumerate(bad["components"]) if c["name"] == "acq")
    bad["components"][acq]["params"]["trigger_level"] = "$ch{n}.trigger_lvl"
    _, report = validate_definition_data(bad)
    # The one-component form expands: the trigger module, right after the acquisition, has the level.
    assert [(d.code, d.pointer) for d in report.diagnostics] == [
        ("E-DEF-STATE-UNKNOWN", f"/components/{acq + 2}/params/level")
    ]


def test_amplitude_falls_back_to_the_extremes_without_levels():
    """A ramp or triangle fills the histogram evenly: no top and base to tell (F2, #131), and
    AMPLitude falls back to the extremes; a flat record or too little data does the same."""
    ramp = np.linspace(-1.0, 1.0, 10_000)
    assert top_base(ramp) is None
    assert measure("VAMPlitude", ramp, 1e-6) == pytest.approx(2.0)
    flat = np.zeros(100)
    assert measure("VAMPlitude", flat, 1e-6) == 0.0
    assert measure("VAMPlitude", np.array([0.0, 1.0, 0.0]), 1e-6) == pytest.approx(1.0)
    assert math.isnan(measure("FREQuency", flat, 1e-6))
    assert math.isnan(measure("PERiod", ramp, 1e-6))


def _modules_definition(**params: Any) -> InstrumentDefinition:
    """``sb-dso-4`` (explicit modules) with the metrology parameters a test sets.

    ``ref``: add a 50 Ω ``input_port`` on ``timebase.ref`` and expose it as REF, the way the
    spectrum analyzer's and lock-in's REF_IN are written; ``timebase`` / ``vertical``: parameters
    of those components (hidden values: a test writes them in the definition)."""
    data = load_yaml(DefinitionLibrary().path("sb-dso-4").read_text(encoding="utf-8"))
    timebase = next(c for c in data["components"] if c["name"] == "timebase")
    timebase["params"] = {**(timebase.get("params") or {}), **params.get("timebase", {})}
    front = next(c for c in data["channel_components"] if c["name"] == "front")
    front["params"] = {**(front.get("params") or {}), **params.get("vertical", {})}
    if params.get("ref"):
        data["components"].append({"name": "ref_in", "type": "input_port", "params": {"z_in": 50.0}})
        data["wiring"]["instrument"].append(["ref_in.out", "timebase.ref"])
        ports = data.setdefault("ports", {})
        ports.setdefault("instrument", {})["REF"] = "ref_in.pin"
        ports.setdefault("media", {})["REF"] = "reference"
    if "deviations" in params:
        data["deviations"] = params["deviations"]
    return InstrumentDefinition.model_validate(data)


def _bench(**params: Any) -> dict:
    state = {
        "ch1.frequency": 1000.0,
        "ch1.amplitude": 2.0,
        "ch1.load": math.inf,
        "ch1.output": True,
        **params.get("awg", {}),
    }
    instruments = {
        "awg": {"model": "sb-awg-2", "state": state},
        "scope": {
            "model": "sb-dso-4",
            "deviation": params.get("deviation", "nominal"),
            "hidden": params.get("hidden", {}),
        },
    }
    wires = [{"from": "awg.OUT1", "to": "scope.CH1"}]
    if params.get("ref"):
        state.update(
            {
                "ch2.frequency": 10.00005e6,  # 10 MHz + 0.5 ppm: a fast reference
                "ch2.amplitude": 1.0,
                "ch2.load": 50.0,
                "ch2.output": True,
            }
        )
        wires.append({"from": "awg.OUT2", "to": "scope.REF"})
    return {
        "schema": "simubench.bench/1",
        "name": "metrology",
        "instruments": instruments,
        "wires": wires,
        "run": {"seed": 3, "clock": "command"},
    }


def _session(bench: dict, definition: InstrumentDefinition) -> Session:
    lib = DefinitionLibrary()
    return Session(
        BenchDefinition.model_validate(bench),
        lambda model: definition if model == "sb-dso-4" else lib.get(model),
        clock="command",
        seed=3,
        idle="off",
    )


def test_the_clock_follows_ref_in_and_falls_back_to_the_internal_one():
    """G3 (#135): with ``rosc`` EXTernal the sampling clock is the 10 MHz on REF_IN — the
    reference's error replaces the unit's own ppm. A fast clock takes samples closer together,
    so the frequency counter reads low (the spectrum analyzer's reference convention); without a
    usable reference the internal clock stays in charge."""
    definition = _modules_definition(ref=True, timebase={"rosc": "EXTernal", "clock_ppm": 400.0})
    session = _session(_bench(ref=True), definition)
    scope = session.instruments["scope"]
    tb, acq = scope.components["timebase"], scope.components["acq"]
    assert tb.clock_error() == pytest.approx(5e-6)  # the reference's 0.5 ppm, not the unit's 400
    session.write("scope", b":DIGitize\n")
    record = acq.records[1]
    assert measure("FREQuency", record.volts, record.dt_s) == pytest.approx(1000.0 / (1 + 5e-6), abs=0.01)
    session.write("awg", b":OUTPut2:STATe OFF\n")  # the reference dies: the internal clock takes over
    assert tb.clock_error() == pytest.approx(400e-6)
    session.close()


def test_gain_offset_and_dac_errors_follow_the_input_v_div():
    """G3 (#135): ``gain_error_ppm`` and ``offset_error`` are per V/div at the input connector,
    the offset DAC's error scales with the display offset setting, and a probe ratio only moves
    the display (the errors stay with the input range). The record shows all three."""
    definition = _modules_definition(
        vertical={
            "gain_error_ppm": {1e-3: 10_000.0, 1e-2: -20_000.0},
            "offset_error": {1e-3: 5e-4},
            "dac_error_ppm": 10_000.0,
        }
    )
    session = _session(_bench(awg={"ch1.function": "DC", "ch1.offset": 0.0025}), definition)
    acq = session.instruments["scope"].components["acq"]
    loaded = 1e6 / (1e6 + 50)  # the scope's 1 MΩ input across the AWG's 50 Ω source

    def record(scale: float, offset: float = 0.0) -> np.ndarray:
        session.write("scope", f":CHANnel1:SCALe {scale:g};:CHANnel1:OFFSet {offset:g}\n".encode())
        session.write("scope", b":DIGitize\n")
        return acq.records[1].volts

    # 1 mV/div: +10000 ppm and 0.5 mV of input offset; the 0.2 V offset setting adds its DAC error
    assert float(np.mean(record(1e-3, 0.2))) == pytest.approx(
        0.0025 * loaded * 1.01 + 0.5e-3 + 0.2 * 10_000e-6, rel=1e-12
    )
    # 10 mV/div: -20000 ppm, no offset error (the table has none at this scale), no offset setting
    assert float(np.mean(record(1e-2))) == pytest.approx(0.0025 * loaded * 0.98, rel=1e-12)
    # A scale the table does not list has no characterized error.
    assert float(np.mean(record(2e-2))) == pytest.approx(0.0025 * loaded, rel=1e-12)
    # 10X probe, 10 mV/div displayed: the input range is 1 mV/div, so the 1 mV/div errors apply in
    # display volts: the offset error is 0.5 mV at the input, 5 mV after the probe ratio.
    session.write("scope", b":CHANnel1:PROBe 10\n")
    assert float(np.mean(record(1e-2))) == pytest.approx((0.0025 * loaded * 1.01 + 5e-4) * 10, rel=1e-9)
    session.close()


def test_skew_and_clock_deviations_are_drawn():
    """G3 (#135): a definition declares the probe's skew error and the timebase's clock ppm as
    individual deviations; each unit draws them from the bench seed."""
    definition = _modules_definition(
        deviations=[
            {"param": "front.gain_error_ppm", "limit": 1000.0, "distribution": "uniform"},
            {"param": "front.offset_error", "limit": 1e-3, "distribution": "uniform"},
            {"param": "probe.skew_error", "limit": 1e-7, "distribution": "uniform"},
            {"param": "timebase.clock_ppm", "limit": 10.0, "distribution": "uniform"},
        ]
    )
    session = _session(_bench(deviation="from_specs"), definition)
    scope = session.instruments["scope"]
    drawn = scope.deviations
    assert {k.rsplit(".", 1)[-1] for k in drawn} == {
        "gain_error_ppm",
        "offset_error",
        "skew_error",
        "clock_ppm",
    }
    assert all(abs(v) <= 1000.0 for k, v in drawn.items() if k.endswith("gain_error_ppm"))
    assert all(abs(v) <= 1e-3 for k, v in drawn.items() if k.endswith("offset_error"))
    assert all(abs(v) <= 1e-7 for k, v in drawn.items() if k.endswith("skew_error"))
    assert all(abs(v) <= 10.0 for k, v in drawn.items() if k.endswith("clock_ppm"))
    timebase = scope.components["timebase"]
    assert timebase.clock_error() == pytest.approx(drawn["timebase.clock_ppm"] * 1e-6)
    for n in range(1, 5):
        front = scope.components[f"ch{n}.front"]
        probe = scope.components[f"ch{n}.probe"]
        assert front._error("gain_error_ppm") == drawn[f"ch{n}.front.gain_error_ppm"]
        assert front._error("offset_error") == drawn[f"ch{n}.front.offset_error"]
        assert probe.delay() == drawn[f"ch{n}.probe.skew_error"]
    session.close()


def _segmented_single(seed: int = 1):
    """DSO-X 2024A on a 1 kHz sine, NORMal sweep, four segments armed by ``:SINGle``: the session,
    its scope's acquisition and a query function."""
    session = open_bench(DSOX, clock="command", seed=seed, idle="off")

    def query(cmd: str) -> str:
        session.write("scope", cmd.encode() + b"\n")
        out = session.read("scope") if "?" in cmd else None
        return out.decode(errors="replace").strip() if out is not None else ""

    query(":TRIGger:SWEep NORMal;:ACQuire:MODE SEGMented;:ACQuire:SEGMented:COUNt 4")
    query(":SINGle")
    return session, session.instruments["scope"].components["acq"], query


def _segment_volts(seed: int) -> list[np.ndarray]:
    session, acq, query = _segmented_single(seed)
    try:
        assert query("*OPC?") == "1"
        assert query(":WAVeform:SEGMented:COUNt?") == "4"
        return [seg[1].volts.copy() for seg in acq.segment_records]
    finally:
        session.close()


def test_the_segmented_acquisition_flow():
    """Segmented memory (#957): one single acquisition takes four records, each on its own
    trigger, the trigger armed again a re-arm time after a record is full; a segment is read by
    its index, with its time tag; the segments carry independent noise; more segments than the
    memory holds are refused."""
    session, acq, query = _segmented_single()
    try:
        period_ns, rearm_ns = 1_000_000, 19_000  # the 1 kHz signal; the definition's rearm_s
        # -- :SINGle (a digitize on this model) returned once the last of the four records was full
        plan = acq.pending._due[0]
        assert len(plan) == 4
        assert acq.pending.due_ns == plan[-1].end_ns + 1_000_000  # and the overhead (1 ms)
        assert session.now_ns == acq.pending.due_ns
        assert query("*OPC?") == "1"
        assert query(":WAVeform:SEGMented:COUNt?") == "4"
        assert query(":RSTate?") == "STOP"
        assert session.now_ns >= plan[-1].end_ns
        # -- the triggers: whole signal periods apart, and no closer than a record and the re-arm time
        records = [seg[1] for seg in acq.segment_records]
        triggers = [rec.trigger_ns for rec in records]
        record_ns = round(records[0].volts.size * records[0].dt_s * 1e9)
        assert record_ns == 2_000_000  # 200 us/div
        for earlier, later in itertools.pairwise(triggers):
            gap = later - earlier
            assert abs(gap - round(gap / period_ns) * period_ns) <= 2  # ns
            assert gap >= record_ns + rearm_ns
            assert gap == pytest.approx(3 * period_ns, abs=2)  # the first period after 2.019 ms
        # -- reading segment k gives the k-th record, and its time tag
        for k in range(1, 5):
            query(f":ACQuire:SEGMented:INDex {k}")
            assert acq.records[1] is acq.segment_records[k - 1][1]
            tag = float(query(":WAVeform:SEGMented:TTAG?"))
            assert tag == pytest.approx((triggers[k - 1] - triggers[0]) * 1e-9, abs=1e-12)
            assert float(query(":WAVeform:XORigin?")) == pytest.approx(records[k - 1].origin, abs=1e-12)
        query(":ACQuire:SEGMented:INDex 5")
        assert query(":SYSTem:ERRor?").startswith("-222")
        assert query(":ACQuire:SEGMented:INDex?") == "+4"
        # -- the segments see the same signal (the triggers are whole periods apart) through
        # independent noise: they differ, by a few codes (20 mV each at 0.5 V/div)
        first, second = records[0].volts, records[1].volts
        assert not np.array_equal(first, second)
        assert np.abs(first - second).max() < 0.2
        assert 0.001 < np.std(first - second) < 0.05
        # -- more segments than the memory holds at this record length: -221, the count stays
        session.set_parameter("scope", "acq", "memory_points", 300_000)
        query(":ACQuire:SEGMented:COUNt 3")
        assert query(":SYSTem:ERRor?").startswith("+0")
        query(":ACQuire:SEGMented:COUNt 4")  # 4 x 100 000 points
        assert query(":SYSTem:ERRor?").startswith("-221")
        assert query(":ACQuire:SEGMented:COUNt?") == "+3"
    finally:
        session.close()


def test_segments_repeat_with_the_seed():
    one, again, other = _segment_volts(1), _segment_volts(1), _segment_volts(2)
    assert all(np.array_equal(a, b) for a, b in zip(one, again, strict=True))
    assert not np.array_equal(one[0], other[0])


def test_a_segmented_single_waits_when_the_signal_stops_after_the_second_segment():
    """The signal goes away after two of four segments have triggered (SDS2104X Plus, whose single
    mode arms without waiting): those two are kept, the single acquisition is not done and
    ``*OPC?`` does not answer (``no_trigger: wait``); once the signal is back the remaining
    segments trigger and it completes."""
    session = open_bench(SIGLENT, clock="command", seed=1, idle="off")

    def query(cmd: str) -> str:
        session.write("scope", cmd.encode() + b"\n")
        out = session.read("scope") if "?" in cmd else None
        return out.decode(errors="replace").strip() if out is not None else ""

    try:
        acq = session.instruments["scope"].components["acq"]
        session.write("awg", b"C1:OUTP ON\n")
        session.set_parameter("scope", "acq", "segments", 4)
        session.set_parameter("scope", "acq", "rearm_s", 19e-6)
        query(":TRIGger:MODE SINGle")
        planned = [seg.trigger_ns for seg in acq.pending._due[0]]  # on the record's grid
        exact = [seg.records[1].trigger_ns for seg in acq.pending._due[0]]  # interpolated
        assert len(planned) == 4
        assert session.now_ns < planned[0]
        session.advance((planned[1] - session.now_ns) * 1e-9 + 1e-5)  # 10 us past the second trigger
        assert session.now_ns < planned[2]
        session.write("awg", b"C1:OUTP OFF\n")
        assert query("*OPC?") == ""  # no reply: waiting for the third segment's trigger
        assert acq.waiting()
        assert acq.pending.due_ns is None
        assert [seg.trigger_ns for seg in acq.pending._found] == planned[:2]
        assert acq.segment_records == []  # nothing stored yet
        assert query(":TRIGger:STATus?") == "Ready"
        session.advance(0.05)
        back_ns = session.now_ns
        session.write("awg", b"C1:OUTP ON\n")
        assert query("*OPC?") == "1"
        assert query(":TRIGger:STATus?") == "Stop"
        triggers = [seg[1].trigger_ns for seg in acq.segment_records]
        assert len(triggers) == 4
        assert triggers[:2] == exact[:2]  # the two that had triggered are the ones kept
        assert triggers[2] >= back_ns  # not before the signal came back
        assert triggers[3] > triggers[2]
    finally:
        session.close()
