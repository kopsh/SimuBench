"""The manual cases SimuBench ships with its built-in models (#1352) run where they are shipped: the
acceptance page and `simubench cases` find every definition their benches name among what the wheel
carries — the built-in library and the files beside the cases and benches — never among the ``sb-*``
validation definitions (D42), which only the repository's tests put on the search path (conftest.py).
A case bench that borrowed one opened in the tests and failed for every user (34465A, 5522A, WavePro
804HD, SDS2104X Plus)."""

from __future__ import annotations

from pathlib import Path

from simubench.library.coverage import _builtin_index, load_cases
from simubench.library.loader import BUILTIN_DIR, load_bench

ROOT = Path(__file__).resolve().parents[1]


def _shipped_case_folders() -> list[Path]:
    """The case folders the acceptance page offers as a built-in model's (the product's own index,
    ``builtin_cases_for_model``): those whose model is a built-in definition — what the wheel packs."""
    root = ROOT / "tests" / "models"
    return [
        root / folder
        for folder, model, _ in _builtin_index(root)
        if (BUILTIN_DIR / f"{model}.yaml").is_file()
    ]


def _found(model: str, *folders: Path) -> bool:
    return any((d / f"{model}.yaml").is_file() for d in (*folders, BUILTIN_DIR))


def test_shipped_cases_name_only_shipped_definitions():
    folders = _shipped_case_folders()
    assert len(folders) >= 20  # the build hook still selects the built-in models' cases
    missing = []
    for folder in folders:
        cases = load_cases(folder / "cases.yaml")
        models = {cases.model}
        places = [folder, folder / "instruments"]
        if cases.bench:
            bench_path = (folder / cases.bench).resolve()
            models = {i.model for i in load_bench(bench_path).instruments.values()}
            places += [bench_path.parent / "instruments"]
        missing += [f"{folder.name}: {m}" for m in sorted(models) if not _found(m, *places)]
    assert missing == [], "shipped cases name definitions the wheel does not carry"
