"""Instrument definitions and the catalogs (D42, D59)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...library.coverage import coverage_path, load_coverage
from ...library.loader import DefinitionLibrary
from ...models import api as M
from ...models.definition import InstrumentDefinition
from ..catalog import components as component_catalog
from ..mytemplates import save_as_template
from .context import ApiContext


def _changed_commands(library: DefinitionLibrary, def_id: str, model: str) -> list[str]:
    """The headers ``def_id`` runs differently from ``model``: commands changed, added or removed."""

    def table(d: str) -> dict[str, object]:
        return {c.header: c.model_dump() for c in library.get(d).commands}

    mine, theirs = table(def_id), table(model)
    return sorted(h for h in mine.keys() | theirs.keys() if mine.get(h) != theirs.get(h))


def _definition_routes(ctx: ApiContext, router: APIRouter) -> None:
    library = ctx.library
    files = ctx.files
    definition_files = ctx.definition_files
    auth = ctx.auth
    not_found = ctx.not_found

    @router.get(
        "/api/catalog",
        response_model=M.Catalog,
        operation_id="getCatalog",
        dependencies=[Depends(auth)],
    )
    def catalog() -> M.Catalog:
        return files.catalog()

    @router.get(
        "/api/components",
        response_model=list[M.ComponentDoc],
        operation_id="listComponents",
        dependencies=[Depends(auth)],
    )
    def list_components() -> list[M.ComponentDoc]:
        """Every component type, internal ones included: the building blocks of instrument definitions."""
        return component_catalog()

    @router.get(
        "/api/definitions",
        response_model=list[str],
        operation_id="listDefinitions",
        dependencies=[Depends(auth)],
    )
    def definitions() -> list[str]:
        return library.ids()

    @router.get(
        "/api/definitions/{def_id}",
        response_model=InstrumentDefinition,
        response_model_by_alias=True,
        operation_id="getDefinition",
        dependencies=[Depends(auth)],
    )
    def definition(def_id: str) -> InstrumentDefinition:
        try:
            return library.get(def_id)
        except KeyError as exc:
            raise not_found(exc) from None

    @router.post(
        "/api/definitions/{def_id}/copy",
        response_model=M.DefinitionDocument,
        operation_id="copyDefinition",
        dependencies=[Depends(auth)],
    )
    def copy_definition(def_id: str) -> M.DefinitionDocument:
        """ "Copy as my instrument": a workspace definition ``my-<id>`` that extends this one (D59)."""
        try:
            return definition_files.copy_of(def_id, "ui")
        except KeyError as exc:
            raise not_found(exc) from None

    @router.post(
        "/api/definitions/templates",
        response_model=M.SaveTemplateResponse,
        operation_id="saveAsTemplate",
        dependencies=[Depends(auth)],
        responses={409: {"description": "The bench changed since base_sha256 was read"}},
    )
    def save_template(req: M.SaveTemplateRequest) -> M.SaveTemplateResponse:
        """ "Save as my template" (#875): the bench instrument, its settings folded in, as a
        definition of its own (no extends); the bench's instrument then points at it."""
        return ctx.file_errors(  # type: ignore[no-any-return]
            lambda: save_as_template(
                definition_files, req.bench_path, req.base_sha256, req.instrument, req.name, "ui"
            )
        )


def _definition_history_routes(ctx: ApiContext, router: APIRouter) -> None:
    library = ctx.library
    manager = ctx.manager
    definition_files = ctx.definition_files
    auth = ctx.auth
    not_found = ctx.not_found

    @router.post(
        "/api/definitions/{def_id}/restore",
        response_model=M.DefinitionDocument,
        operation_id="restoreDefinition",
        dependencies=[Depends(auth)],
        responses={409: {"description": "The file changed since base_sha256 was read"}},
    )
    def restore_definition(def_id: str, req: M.RestoreDefinitionRequest) -> M.DefinitionDocument:
        """Write an earlier version of a workspace definition back as the newest one (D35)."""
        return ctx.file_errors(lambda: definition_files.restore(def_id, req.seq, req.base_sha256))  # type: ignore[no-any-return]

    @router.get(
        "/api/definitions/{def_id}/document",
        response_model=M.DefinitionDocument,
        operation_id="getDefinitionDocument",
        dependencies=[Depends(auth)],
    )
    def definition_document(def_id: str) -> M.DefinitionDocument:
        """The definition as stored (hash, diagnostics, workspace-relative path), for the acceptance
        page to notice a change made elsewhere and reload; ``sessions_to_restart`` names the running
        sessions that still use an older version (D25), as the MCP tools report it."""
        try:
            doc = definition_files.read(def_id)
        except Exception as exc:
            raise ctx.http_error(exc) from None
        doc.sessions_to_restart = manager.sessions_using_stale_definition(def_id)
        return doc

    @router.get(
        "/api/definitions/{def_id}/structure",
        response_model=M.DefinitionStructure,
        operation_id="getDefinitionStructure",
        dependencies=[Depends(auth)],
    )
    def definition_structure(def_id: str) -> M.DefinitionStructure:
        """The effective structure (components, wiring, panel ports; extends merged in) for the
        Web's build area (#1201); read even when the draft does not pass the checks."""
        try:
            return definition_files.structure(def_id)  # type: ignore[no-any-return]
        except Exception as exc:
            raise ctx.http_error(exc) from None

    @router.put(
        "/api/definitions/{def_id}/structure",
        response_model=M.DefinitionStructure,
        operation_id="saveDefinitionStructure",
        dependencies=[Depends(auth)],
        responses={
            403: {"description": "The definition is not in the workspace"},
            409: {"description": "The file changed since base_sha256 was read"},
        },
    )
    def save_definition_structure(def_id: str, req: M.StructureUpdate) -> M.DefinitionStructure:
        """Write the structure the build area drew into a workspace definition (#1201): one saved
        version, with the change log in the instrument's language (D35, T3)."""
        return ctx.file_errors(lambda: definition_files.save_structure(def_id, req, "ui"))  # type: ignore[no-any-return]

    @router.get(
        "/api/definitions/{def_id}/layout",
        response_model=M.DefinitionLayout,
        operation_id="getDefinitionLayout",
        dependencies=[Depends(auth)],
    )
    def definition_layout(def_id: str) -> M.DefinitionLayout:
        """Where the build area draws the components (#1207); empty when none were saved."""
        try:
            return definition_files.layout(def_id)  # type: ignore[no-any-return]
        except Exception as exc:
            raise ctx.http_error(exc, full_detail=True) from None

    @router.put(
        "/api/definitions/{def_id}/layout",
        response_model=M.DefinitionLayout,
        operation_id="saveDefinitionLayout",
        dependencies=[Depends(auth)],
        responses={403: {"description": "The definition is not in the workspace"}},
    )
    def save_definition_layout(def_id: str, req: M.DefinitionLayout) -> M.DefinitionLayout:
        """Keep the positions beside a workspace definition: no version, no session restart."""
        return ctx.file_errors(lambda: definition_files.save_layout(def_id, req))  # type: ignore[no-any-return]

    @router.get(
        "/api/definitions/{def_id}/coverage",
        response_model=M.DefinitionCoverage | None,
        operation_id="getDefinitionCoverage",
        dependencies=[Depends(auth)],
    )
    def definition_coverage(def_id: str) -> M.DefinitionCoverage | None:
        """The real model's coverage table next to the definition (D42): every command of the manual
        and how far SimuBench goes; null when the definition has none."""
        try:
            path = coverage_path(library.path(def_id))
        except KeyError as exc:
            raise not_found(exc) from None
        from_model: str | None = None
        if not path.is_file():  # a copy: the table of the nearest model it extends that has one
            from_model, path = next(
                ((m, coverage_path(f)) for m, f in library.ancestors(def_id) if coverage_path(f).is_file()),
                (None, path),
            )
            if from_model is None:
                return None
        try:
            cov = load_coverage(path)
        except Exception as exc:  # a broken table is reported, not hidden
            raise ctx.http_error(ValueError(f"{path.name}: {exc}")) from None
        return M.DefinitionCoverage(
            model=cov.model,
            stats=cov.stats(),
            gap_stats=cov.gap_stats(),
            entries=[
                M.CoverageEntryInfo(
                    section=section,
                    header=e.header,
                    level=e.level,
                    note=e.note,
                    gap=e.gap,
                    blocked_by=e.blocked_by,
                )
                for section, e in cov.entries()
            ],
            from_model=from_model,
            changed=_changed_commands(library, def_id, from_model) if from_model else [],
        )

    @router.get(
        "/api/definitions/{def_id}/history",
        response_model=list[M.DefinitionVersion],
        operation_id="definitionHistory",
        dependencies=[Depends(auth)],
    )
    def definition_history(def_id: str) -> list[M.DefinitionVersion]:
        """Saved versions of a workspace definition, newest first, each with what it changed since
        the previous one, in the instrument's language (the acceptance page's change log, T3)."""
        try:
            return definition_files.versions(def_id)  # type: ignore[no-any-return]
        except Exception as exc:
            raise ctx.http_error(exc, full_detail=True) from None


def _definition_change_routes(ctx: ApiContext, router: APIRouter) -> None:
    definition_files = ctx.definition_files
    auth = ctx.auth

    @router.get(
        "/api/definitions/{def_id}/history/changes",
        response_model=M.DefinitionChangeList,
        operation_id="definitionChanges",
        dependencies=[Depends(auth)],
    )
    def definition_change_list(
        def_id: str, base: int = Query(...), target: int = Query(...)
    ) -> M.DefinitionChangeList:
        """The changes between any two versions (base 0 compares against nothing: the creation)."""
        try:
            return definition_files.changes_between(def_id, base, target)  # type: ignore[no-any-return]
        except Exception as exc:
            raise ctx.http_error(exc, missing="no such version", full_detail=True) from None


def build_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    _definition_routes(ctx, router)
    _definition_history_routes(ctx, router)
    _definition_change_routes(ctx, router)
    return router
