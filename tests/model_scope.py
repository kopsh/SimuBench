"""Which tests a model-scoped run keeps (#1680, D49): the models a pull request changed run whole,
the other models only a smoke subset.

``scripts/select_tests.py`` decides the changed models from the pull request's paths and hands them
to pytest as ``--model-scope``. Two kinds of tests belong to a model:

- a test marked ``@pytest.mark.model("<id>", ...)`` (a module's ``pytestmark`` or one test): run
  only when one of its models is in scope;
- a manual case of ``tests/test_real_models.py`` (its ``cases`` parameter names the model): a model
  not in scope keeps its first ``smoke`` cases, so a definition that no longer loads or answers its
  first commands still shows on every pull request.

Everything else is not about one model and always runs. Without ``--model-scope`` nothing is
dropped: main and a change to shared code run the whole set.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

#: Manual cases kept per model that is not in scope.
SMOKE_CASES = 3


def marked_models(item: Any) -> set[str]:
    """The models named by the item's ``model`` markers (its own and its module's)."""
    models: set[str] = set()
    for mark in item.iter_markers("model"):
        models.update(str(m) for m in mark.args)
    return models


def case_model(item: Any) -> str | None:
    """The model a manual case of ``test_real_models.py`` runs, or ``None`` for another test."""
    callspec = getattr(item, "callspec", None)
    if callspec is None or "case" not in callspec.params:
        return None
    model = getattr(callspec.params.get("cases"), "model", None)
    return model if isinstance(model, str) else None


def split(items: Sequence[Any], scope: set[str], smoke: int = SMOKE_CASES) -> tuple[list[Any], list[Any]]:
    """``(kept, dropped)`` for a run limited to the models in ``scope``, in collection order."""
    kept: list[Any] = []
    dropped: list[Any] = []
    seen: dict[str, int] = {}
    for item in items:
        models = marked_models(item)
        if models:
            (kept if models & scope else dropped).append(item)
            continue
        model = case_model(item)
        if model is None or model in scope:
            kept.append(item)
            continue
        seen[model] = seen.get(model, 0) + 1
        (kept if seen[model] <= smoke else dropped).append(item)
    return kept, dropped


def parse_scope(text: str) -> set[str]:
    """``--model-scope`` is a comma-separated list; an empty list means no model changed."""
    return {m.strip() for m in text.split(",") if m.strip()}
