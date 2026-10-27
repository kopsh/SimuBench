"""Closed-form circuit results used by the acceptance tests."""

from __future__ import annotations

import math


def par(*zs: complex) -> complex:
    """Parallel combination of impedances (math.inf allowed)."""
    y = sum(0 if (isinstance(z, float) and math.isinf(z)) else 1 / z for z in zs)
    return math.inf if y == 0 else 1 / y


def zc(c: float, f: float) -> complex:
    return 1 / (1j * 2 * math.pi * f * c) if f > 0 else math.inf


def emf_for_load_setting(v_set: float, z_out: float, load_setting: float) -> float:
    """EMF that puts ``v_set`` across ``load_setting`` behind ``z_out`` (inf: open-circuit amplitude)."""
    if math.isinf(load_setting):
        return v_set
    return v_set * (load_setting + z_out) / load_setting


def rc_chain(
    f: float,
    emf: float,
    rs: float = 50.0,
    r: float = 1e3,
    c: float = 100e-9,
    load_in: float = math.inf,
    load_out: float = math.inf,
) -> tuple[complex, complex]:
    """Source EMF behind ``rs`` into series R, shunt C at the output.

    ``load_in`` / ``load_out`` are extra resistances to ground at the RC input and output nodes.
    Returns the complex voltages (V_in, V_out) as peak phasors for an EMF of peak ``emf``.
    """
    z_out_node = par(zc(c, f), load_out)
    z_in_node = par(r + z_out_node, load_in)
    v_in = emf * z_in_node / (z_in_node + rs)
    v_out = v_in * z_out_node / (r + z_out_node)
    return v_in, v_out


def divider(v: complex, r_series: float, r_shunt: float) -> complex:
    return v * r_shunt / (r_series + r_shunt)


def fit_sine(t: list[float] | object, v: object, f: float) -> tuple[float, float, float]:
    """Least-squares fit of ``a*sin + b*cos + dc``; returns (peak, phase_rad, dc)."""
    import numpy as np

    t = np.asarray(t, dtype=float)
    v = np.asarray(v, dtype=float)
    basis = np.column_stack([np.sin(2 * np.pi * f * t), np.cos(2 * np.pi * f * t), np.ones_like(t)])
    (a, b, dc), *_ = np.linalg.lstsq(basis, v, rcond=None)
    return float(np.hypot(a, b)), float(np.arctan2(b, a)), float(dc)
