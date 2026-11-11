"""Instrument definitions and the catalogs (D42, D59)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ...library.coverage import coverage_path, load_coverage
from ...models import api as M
from ...models.definition import InstrumentDefinition
from ..catalog import components as component_catalog
from ..errors import FILE_ERRORS
from ..mytemplates import save_as_template
from .context import ApiContext


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
        page to notice a change made elsewhere and reload."""
        try:
            return definition_files.read(def_id)
        except KeyError as exc:
            raise not_found(exc) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc).splitlines()[0]) from None

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
        if not path.is_file():
            return None
        try:
            cov = load_coverage(path)
        except Exception as exc:  # a broken table is reported, not hidden
            raise HTTPException(422, f"{path.name}: {str(exc).splitlines()[0]}") from None
        return M.DefinitionCoverage(
            model=cov.model,
            stats=cov.stats(),
            entries=[
                M.CoverageEntryInfo(section=section, header=e.header, level=e.level, note=e.note)
                for section, e in cov.entries()
            ],
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
            return definition_files.versions(def_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None


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
            return definition_files.changes_between(def_id, base, target)
        except (*FILE_ERRORS, ValueError) as exc:
            raise ctx.http_error(exc, missing="no such version", full_detail=True) from None


def build_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    _definition_routes(ctx, router)
    _definition_history_routes(ctx, router)
    _definition_change_routes(ctx, router)
    return router
