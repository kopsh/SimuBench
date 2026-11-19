"""Re-record any gold standard file of the ``*_migration.py`` tests (#1635): one command walks the
same benches the tests walk, through the helpers' recording channel (``record_steps`` /
``record_gold_step`` / the module walks), and rewrites the JSON.

    uv run python tests/record_gold.py              # every gold file
    uv run python tests/record_gold.py eload smu    # only the named ones

The committed steps drive the walk — the step kinds (a command, a clock advance, a signal, a
trace, the screen) come from the file, the actual replies, digests, lines and times replace the
recorded values. The inline Python tables of the load, DMM and SMU (#186-#190) were migrated into
``tests/data/*.json`` here; no rows are pasted into source by hand any more. Record only when a
change to the replies is intended and reviewed: the tests afterwards compare every step with the
file, and the diff of a re-recording that still matches is empty.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: gold file -> (its test module, the function that walks and writes it).
RECORDER: dict[str, tuple[str, str]] = {
    "eload": ("test_eload_migration", "record"),
    "dmm": ("test_dmm_migration", "record"),
    "smu": ("test_smu_migration", "record"),
    "lockin": ("test_lockin_migration", "record"),
    "rfgen": ("test_rfgen_migration", "record"),
    "specan": ("test_specan_migration", "record_gold"),
    "specan-models": ("test_specan_migration", "record_models"),
    "scope-stream": ("test_scope_stream_migration", "record"),
}


def main(argv: list[str]) -> int:
    names = argv or list(RECORDER)
    unknown = [name for name in names if name not in RECORDER]
    for name in unknown:
        print(f"no gold file named {name!r}; record one of: {', '.join(RECORDER)}")
    for name in names:
        if name in unknown:
            continue
        print(f"== {name}")
        module_name, function = RECORDER[name]
        getattr(importlib.import_module(module_name), function)()
    return 1 if unknown else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
