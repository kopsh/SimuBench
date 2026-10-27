"""Counter-based random numbers: noise as a pure function of (seed, stream, index).

A reading's noise must be the same whenever it is evaluated at the same virtual instant, whatever
was evaluated before (architecture.md 2.5, 5.4). Stateful generators cannot give that; hashing the
sample index can. SplitMix64 is used as the hash: fast, vectorized and well distributed.
"""

from __future__ import annotations

import hashlib

import numpy as np

_M1 = np.uint64(0xBF58476D1CE4E5B9)
_M2 = np.uint64(0x94D049BB133111EB)
_GOLDEN = np.uint64(0x9E3779B97F4A7C15)


def stream_key(*parts: object) -> int:
    """A stable 64-bit key from any identifying parts (not Python's randomized ``hash``)."""
    digest = hashlib.blake2b(repr(parts).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "little")


def _splitmix(x: np.ndarray) -> np.ndarray:
    with np.errstate(over="ignore"):
        z = x + _GOLDEN
        z = (z ^ (z >> np.uint64(30))) * _M1
        z = (z ^ (z >> np.uint64(27))) * _M2
        return z ^ (z >> np.uint64(31))


def uniform(key: int, index: np.ndarray) -> np.ndarray:
    """Uniform numbers in (0, 1), one per index."""
    idx = np.asarray(index, dtype=np.int64).astype(np.uint64)
    with np.errstate(over="ignore"):
        bits = _splitmix(idx * _GOLDEN ^ np.uint64(key))
    return ((bits >> np.uint64(11)).astype(np.float64) + 0.5) / float(1 << 53)


def gaussian(key: int, index: np.ndarray) -> np.ndarray:
    """Standard normal numbers, one per index (Box-Muller on two independent streams)."""
    u1 = uniform(key, index)
    u2 = uniform(key ^ 0x5DEECE66D, index)
    return np.sqrt(-2.0 * np.log(u1)) * np.cos(2.0 * np.pi * u2)
