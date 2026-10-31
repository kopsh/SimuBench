"""Terminal console (TUI): a Textual client of the served HTTP/WebSocket interface (D18).

Everything here talks to the same ``/api`` endpoints as the Web Bench and reuses
``simubench.models.api``; the engine is never imported. The optional dependency
``simubench[tui]`` carries Textual and httpx; the ``websockets`` client comes with
``uvicorn[standard]``.
"""
