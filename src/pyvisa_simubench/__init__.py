"""PyVISA entry point for the SimuBench backend: ``ResourceManager("bench.yaml@simubench")``."""

from simubench.access.pyvisa_backend import WRAPPER_CLASS

__all__ = ["WRAPPER_CLASS"]
