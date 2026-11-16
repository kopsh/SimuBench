"""A front-panel calibration/auxiliary output: the signal a scope drives out of its AUX OUT / CAL OUT."""

from __future__ import annotations

from ...engine.component import Component, PortSpec, component
from ...engine.signal import Signal, square
from ...scpi.errors import ScpiError

#: The guide's selection words (``:CALibrate:OUTPut``, p. 473) as the short forms the setting stores.
WORDS = ("DC", "PROB", "TOUT", "ZERO", "ONE", "DPUL", "NDPUL", "MHZ97", "MHZ7_5")


#: The CAL OUT DC level's limit, V (the guide, p. 473).
DC_MAX = 1.2


@component("cal_output")
class CalOutput(Component):
    """The signal a front-panel output port carries, chosen by ``:CALibrate:OUTPut`` (D50 source block).

    ``selection`` is the stored selection (a word, ``DC`` followed by ``,<level>`` for the DC form).
    ``port`` says which connector this is: on ``cal`` the DC word carries the commanded level
    (±``dc_max``); the ``aux`` connector's DC word is the fixed ``zero_level`` (the guide: AUX OUT's
    DC is the ZERO level, −440 mV). ZERO and ONE are constant levels, PROB the probe-compensation
    square (750 Hz), MHZ97 / MHZ7_5 clock squares. TOUT (trigger-event pulse, needs the high-bandwidth
    trigger option) and the DPUL / NDPUL pulses are not modeled: those outputs idle at ``zero_level``.
    ``rise`` (FAST or SLOW) limits the edges' bandwidth on the ``aux`` connector. Levels other than
    ZERO's and the 750 Hz are SimuBench values, not from the guide. The enable and the output
    impedance are the downstream ``output_stage``'s.
    """

    label = "校准输出选择"
    label_en = "Calibration output select"
    about = "选择前面板输出端口送出的信号（:CALibrate:OUTPut）。"
    about_en = "Chooses the signal a front-panel output port carries (:CALibrate:OUTPut)."

    ports = {"out": PortSpec("out", "signal")}
    defaults = {
        "selection": "ZERO",  # the stored :CALibrate:OUTPut selection, e.g. ZERO or DC,0.5
        "port": "cal",  # cal | aux: which connector this is
        "rise": "FAST",  # FAST | SLOW: the AUX OUT edge speed
        "dc_max": DC_MAX,  # V, the CAL OUT DC level's limit (the guide, p. 473)
        "zero_level": -0.44,  # V, the ZERO level (the guide: -440 mV on AUX OUT)
        "one_level": 0.44,  # V, the ONE level (SimuBench value)
        "probe_high": 0.5,  # V, the probe-compensation square's high level (SimuBench value)
        "probe_hz": 750.0,  # Hz, the probe-compensation square (the guide: about 750 Hz)
        "clock_swing": 0.5,  # V, peak-to-peak of the MHZ97 / MHZ7_5 clocks (SimuBench value)
        "fast_hz": 2e9,  # Hz, edge bandwidth of FAST (SimuBench value)
        "slow_hz": 2e8,  # Hz, edge bandwidth of SLOW (SimuBench value)
    }
    units = {"dc_max": "V", "zero_level": "V", "one_level": "V", "probe_high": "V", "probe_hz": "Hz"}
    #: Clock selections and their frequency.
    CLOCKS = {"MHZ97": 97e6, "MHZ7_5": 7.5e6}

    def _parsed(self) -> tuple[str, float | None]:
        word, _, level = str(self.param("selection")).partition(",")
        word = word.strip().upper()
        if word not in WORDS:
            raise ScpiError(-224, f"calibration output signal {word!r} is not one of {', '.join(WORDS)}")
        return word, float(level) if level.strip() else None

    def level(self) -> float | None:
        """The constant level when the selection is one, else None (a periodic signal)."""
        word, dc = self._parsed()
        zero = float(self.param("zero_level"))
        if word == "DC":
            if str(self.param("port")).lower() == "aux" or dc is None:
                return zero
            return max(-float(self.param("dc_max")), min(float(self.param("dc_max")), dc))
        if word == "ONE":
            return float(self.param("one_level"))
        if word in self.CLOCKS or word == "PROB":
            return None
        return zero  # ZERO, and the unmodeled TOUT / DPUL / NDPUL idle low

    def output(self, port: str) -> Signal:
        level = self.level()
        if level is not None:
            return Signal.constant(level)
        word, _ = self._parsed()
        zero = float(self.param("zero_level"))
        bw_key = "fast_hz" if str(self.param("rise")).upper().startswith("FAST") else "slow_hz"
        bw = (
            float(self.param(bw_key))
            if str(self.param("port")).lower() == "aux"
            else float(self.param("fast_hz"))
        )
        if word == "PROB":
            low, high, freq = 0.0, float(self.param("probe_high")), float(self.param("probe_hz"))
        else:
            swing = float(self.param("clock_swing"))
            low, high, freq = zero, zero + swing, self.CLOCKS[word]
        return square(freq, (high - low) / 2.0, bw) + Signal.constant((high + low) / 2.0)
