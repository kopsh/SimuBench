"""A typical bench script: generator into scope, sweep amplitude, read Vpp."""

import pyvisa

SCOPE_ADDR = "TCPIP0::192.168.1.21::5025::SOCKET"


class Rig:
    def __init__(self, rm):
        self.awg = rm.open_resource("TCPIP0::192.168.1.20::5025::SOCKET")
        self.scope = rm.open_resource(SCOPE_ADDR)

    def setup(self, ch):
        self.awg.write("*RST")
        self.awg.write(f":SOUR{ch}:FUNC SIN")
        self.awg.write(":SOUR1:FREQ %g" % 1000)
        self.awg.write(":OUTP1 ON")
        self.awg.write(":SOUR1:SWEEP:STAT ON")
        self.scope.write(":CHAN1:SCAL {}".format(0.5))
        self.scope.write(":TIM:SCAL 0.0002")
        print(self.scope.query("*IDN?"))

    def measure(self):
        return float(self.scope.query(":MEAS:VPP? CHAN1"))


def main():
    rm = pyvisa.ResourceManager()
    rig = Rig(rm)
    rig.setup(1)
    print(rig.measure())
    with rm.open_resource("USB0::0x1AB1::0x0588::DM3R1::INSTR") as dmm:
        dmm.write(":CONF:VOLT:DC")
