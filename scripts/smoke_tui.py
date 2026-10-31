"""Manual smoke test: TUI against a real uvicorn server. Not part of the test suite."""

import asyncio
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    import socket

    import uvicorn

    from simubench.server.app import create_app
    from simubench.server.manager import SessionManager
    from simubench.tui.app import SimuBenchApp

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    manager = SessionManager(ROOT / "examples/benches", scpi_base_port=0, hislip_port=0)
    app = create_app(manager, token=None)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False)
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.02)

    served = await manager.start("siglent_scope_rc.bench.yaml", seed=7, clock="command")
    sid = served.id

    tui = SimuBenchApp(f"http://127.0.0.1:{port}", sid=sid, lang="zh")
    async with tui.run_test(size=(100, 32)) as pilot:
        for _ in range(40):
            await pilot.pause(0.1)
            if tui.conn == "connected" and tui.session_info:
                break
        print("conn:", tui.conn, "events:", len(tui.store.events))
        print(tui.screen._grid.size)
        # let cards render
        for _ in range(5):
            await pilot.pause(0.2)
        print("---- screen ----")
        for strip in tui.screen._compositor.render_strips():
            print("".join(seg.text for seg in strip))
    server.should_exit = True
    t.join(timeout=5)


asyncio.run(main())
