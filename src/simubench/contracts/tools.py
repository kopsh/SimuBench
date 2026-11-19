"""Input models of the agent-facing tools (#1382): the parameters the MCP tools and the assistant's
tools take, one source per tool (D21's tool-schema leg — the schemas are generated from these, not
hand-copied signatures).

No HTTP route references these models, so none of them appears in the OpenAPI schema
(``schemas/openapi.json`` describes the routes only). A parameter that differs by seam (which path
is required, the try pool's command limit) is one base model with one subclass per seam; everything
else is one model for both."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..engine.edit import EditOp


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BenchEdit(_Input):
    """The ``edit_bench`` parameters shared by both agent seams (the HTTP route's
    ``EditBenchRequest`` carries the same ``ops``; ``source`` is not an agent's to choose)."""

    base_sha256: str = Field(..., description="sha256 from the last read_bench / edit_bench")
    ops: list[EditOp] = Field(..., description="Applied in order, all or nothing")


class McpBenchEdit(BenchEdit):
    """The MCP tool's ``edit_bench``: the bench file is named in the call."""

    path: str = Field(..., description="Workspace-relative bench file")


class AssistantBenchEdit(BenchEdit):
    """The assistant's ``edit_bench``: the path defaults to the conversation's bench."""

    path: str | None = Field(None, description="The bench; default: this conversation's bench")


class CreateBench(_Input):
    path: str = Field(..., description="Workspace-relative, ending in .bench.yaml")
    name: str = Field(..., description="The bench's name")
    description: str | None = None


_TRY_COMMANDS = "Program messages, sent in order to the instrument alone"


class TryDefinition(_Input):
    """The ``try_definition`` parameters shared by both agent seams; the command limit is the
    seam's (the assistant works in shorter batches than an MCP agent)."""

    definition_id: str = Field(..., description="The definition to try (list_catalog has the ids)")
    commands: list[str] = Field(..., description=_TRY_COMMANDS)


class McpTryDefinition(TryDefinition):
    """The MCP tool's batch limit: 200 commands."""

    commands: list[str] = Field(..., max_length=200, description=_TRY_COMMANDS)


class AssistantTryDefinition(TryDefinition):
    """The assistant's batch limit: 100 commands."""

    commands: list[str] = Field(..., max_length=100, description=_TRY_COMMANDS)


class RunCases(_Input):
    path: str = Field(
        ...,
        description="Workspace-relative cases file (commands and expected replies), or a built-in "
        "model's manual cases as listed: builtin:<folder>/cases.yaml",
    )
    model: str | None = Field(
        None,
        description="Run the cases on this definition in place of the cases' own: a copy checks "
        "itself against the model it extends' cases",
    )
