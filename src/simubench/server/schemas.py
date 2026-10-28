"""Export the interface schemas from the Pydantic models (single source, D21).

* ``openapi.json`` - HTTP interface, from FastAPI;
* ``events.schema.json`` - WebSocket event messages;
* ``bench.schema.json`` and ``instrument.schema.json`` - file formats;
* ``coverage.schema.json`` and ``cases.schema.json`` - a real model's coverage table and manual cases (D42).

The web client's TypeScript types are generated from these files (``web/scripts/gen-api.mjs``),
and CI fails when the committed output is stale.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..library.coverage import Cases, Coverage
from ..models.api import Event
from ..models.bench import BenchDefinition
from ..models.definition import InstrumentDefinition


def _dump(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def openapi() -> dict[str, Any]:
    from .app import create_app
    from .manager import SessionManager

    app = create_app(SessionManager(Path(".")), token="schema-export", web_dist=Path("/nonexistent"))
    return app.openapi()


def export_all(out: Path) -> list[Path]:
    return [
        _dump(out / "openapi.json", openapi()),
        _dump(out / "events.schema.json", Event.model_json_schema()),
        _dump(out / "bench.schema.json", BenchDefinition.model_json_schema(by_alias=True)),
        _dump(out / "instrument.schema.json", InstrumentDefinition.model_json_schema(by_alias=True)),
        _dump(out / "coverage.schema.json", Coverage.model_json_schema(by_alias=True)),
        _dump(out / "cases.schema.json", Cases.model_json_schema(by_alias=True)),
    ]
