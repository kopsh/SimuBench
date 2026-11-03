---
name: component
description: Add or split a generic SimuBench component type (D50 functional block) — the Python class, registration, solve level, catalog metadata, tests and docs. Lead-agent (核心) work; use for capability issues, never inside a real-model issue.
---

# Adding a generic component

Only for `核心` capability issues. A real-model issue never adds components (see `real-model`).

## Before code

1. Read the class section of `docs/component-map.md` and `docs/blocks/<class>.yaml`: the block,
   its component type name, its **solve level** (circuit: `stamp`/`emf`; signal: `output`;
   setting/data: ops only). Decide the solve level first — no decorative blocks (D50).
2. Names and parameters in the instrument engineer's language (NPLC, CV/CC, RBW, scan list), not
   software terms. `grep -n '^## D5[0-2]' docs/decisions.md` for D50–D52.
3. Splitting an existing component: record golden values first (the #94 method), behaviour must
   not change.

## Code (`src/simubench/library/components/<class>.py`)

```python
from ...engine.component import Component, OpResult, PortSpec, component, op
from ...scpi import values as V
from ...scpi.errors import ScpiError

@component("counter_gate")            # the type name used in definitions
class CounterGate(Component):
    """Docstring: what real block this is and its model limits (shown in the catalog)."""
    # category = "internal" (default; inside an instrument) | "part" | "accessory" (bench-level, with label/label_en)
    ports = {"in": PortSpec(...)}      # medium: signal | electrical | reference
    defaults = {
        "time": 0.1,  # s, the gate time   <- this comment IS the catalog description (required)
    }
    units = {"time": "s"}
    choices = {"mode": ("TIMe", "COUNt")}  # word params: validation refuses other words; read with self.choice("mode")
    refs = {"clock": ("reference_clock", "instrument")}   # links to sibling components
    generic = True                     # False = model-specific (tracked in ALLOWED, only shrinks)

    @op("count", fields=("unit",))     # callable from a definition: op: <component>.count
    def op_count(self, **_): return OpResult(V.format_int(3))
```

- Override `output(port)` for signal outputs, `stamp(circuit, node)` / `emf()` for circuit
  contribution; `piecewise = True` for mode-dependent devices (D34 mode iteration, D52);
  `dynamic = True` for time state.
- Out of the model's range → raise `ScpiError` with the right code; never return a guess.
- Numeric replies: `self.reply_number(value, digits)`, never a hand-formatted string — a
  definition with `format.readings: format` answers in its own format (D54, #366).
- Randomness only through `engine/noise.py` streams (determinism, D6/D12).
- New module → import it in `library/components/__init__.py`.
- Parameters bind to state in definitions via `$path` or `{from, map, scale, offset, default}`.

## Tests and docs

- Unit tests in `tests/test_<class>.py` (≤100 ms each, no server). Exercise through an `sb-*`
  definition in `validation/instruments/` when a whole instrument is needed.
- `tests/test_component_catalog.py` reads metadata from the class: every default needs a comment
  next to (or above) it — that is the parameter description; op parameters come from the signature.
- Update `docs/blocks/<class>.yaml` and the class table in `docs/component-map.md`
  (implementation-status column); shrink `EXEMPT`/`ALLOWED` if your work closes a gap.
- If a definition field changed: `uv run simubench schemas --out schemas`, `cd web && pnpm gen:api`,
  commit both. Then `scripts/check.sh`.
