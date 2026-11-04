"""SimuBench: software-defined virtual instrument benches."""

# Written at build or install time from git (hatch-vcs, D58); absent only in a bare source tree.
try:
    from ._version import __commit_id__, __version__
except ImportError:  # pragma: no cover - a source tree nobody built or installed
    __version__ = "0+unknown"
    __commit_id__ = None

__all__ = ["__commit_id__", "__version__"]
