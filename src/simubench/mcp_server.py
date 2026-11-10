"""Local MCP service: lets an AI agent build, deploy, drive and observe benches (D9, architecture.md 7.5).

The tools are thin wrappers over the same :class:`~simubench.server.manager.SessionManager` and
bench-file operations the Web Bench uses, so whatever an agent does shows up in the engineer's
browser, and agent edits land in readable bench files.

Rules the tools enforce:

* dangerous commands (outputs on, sweeps that could harm a DUT) need ``confirm=true``;
* whole-bench operations that touch the scripts on the ports — starting a bench (it replaces the
  running one), stopping one, advancing the clock — are refused while scripts are connected,
  until confirmed with ``force=true`` (issue 624);
* simulation control (advancing the clock, reading true values) is off unless the service was
  started with ``--allow-control``; true values also need ``run.reveal_true_values`` in the bench;
* calls are rate limited, so a looping agent cannot flood a session.
"""

from mcp.server.mcpserver import MCPServer

from . import __version__
from .mcp_tools import bench, definitions, sessions
from .mcp_tools.context import McpContext, RateLimiter
from .server.manager import SessionManager

__all__ = ["INSTRUCTIONS", "RateLimiter", "build_server"]

INSTRUCTIONS = """\
SimuBench runs virtual instrument benches: instruments (AWG, oscilloscope, ...), parts (RC networks,
resistors, ...) and wires, simulated with correct circuit physics. Any instrument or part may be
marked as the DUT, the object under test or calibration (dut: true). Typical flow:
1. list_catalog / get_definition to see what instruments and parts exist; list_components for the
   building blocks of instrument definitions (ports, parameters, operations);
2. read_bench or create_bench, then edit_bench with operations (always pass the sha256 you read);
   or draft_bench_from_script to start from an existing PyVISA test program;
3. validate_bench and fix every error the diagnostics point at (pointer + suggestion);
   run_cases checks instruments against a cases file of commands and expected replies;
   try_definition sends commands to one instrument alone, without a bench;
   to build an instrument the library lacks: list_templates / get_template and
   create_definition_from_template (fill in the data sheet), draft_definition_from_script or
   create_definition, then edit_definition (JSON Patch) / validate_definition until clean, then run_cases;
4. start_session deploys the bench: instruments answer SCPI at the returned addresses, and the
   engineer watches it in the Web Bench;
5. send_scpi / get_instrument_state / read_log to drive and observe it;
6. with --allow-control: inject_fault (timeouts, disconnects, error codes, drift…) to test how a
   script handles failures, advance_clock, read_true_values.
Edits change the bench file; a running session picks up topology changes only when restarted.
Bench files are the engineer's: explain what you change.
"""


def build_server(
    manager: SessionManager,
    *,
    allow_control: bool = False,
    ui_url: str | None = None,
    limiter: RateLimiter | None = None,
) -> MCPServer:
    mcp = MCPServer(name="simubench", version=__version__, instructions=INSTRUCTIONS)
    ctx = McpContext(manager, allow_control, ui_url, limiter or RateLimiter())
    for area in (bench, definitions, sessions):  # the order is the order of the tool list
        area.register(mcp, ctx)
    return mcp
