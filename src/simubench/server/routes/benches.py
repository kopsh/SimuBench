"""Bench files: list, read, save, edit, history, restore, validate (D35)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...models import api as M
from .context import ApiContext


def _bench_file_routes(ctx: ApiContext, router: APIRouter) -> None:
    manager = ctx.manager
    files = ctx.files
    auth = ctx.auth
    file_errors = ctx.file_errors

    @router.get(
        "/api/benches",
        response_model=list[M.BenchFile],
        operation_id="listBenches",
        dependencies=[Depends(auth)],
    )
    def benches() -> list[M.BenchFile]:
        return manager.bench_files()

    @router.get(
        "/api/benches/file",
        response_model=M.BenchDocument,
        operation_id="readBench",
        dependencies=[Depends(auth)],
    )
    def read_bench(path: str = Query(...)) -> M.BenchDocument:
        return file_errors(lambda: files.read(path))  # type: ignore[no-any-return]

    @router.get(
        "/api/benches/preview",
        response_model=M.BenchPreview,
        operation_id="previewBench",
        dependencies=[Depends(auth)],
    )
    def preview_bench(path: str = Query(...)) -> M.BenchPreview:
        return file_errors(lambda: manager.preview(path))  # type: ignore[no-any-return]

    @router.put(
        "/api/benches/file",
        response_model=M.BenchDocument,
        operation_id="saveBench",
        dependencies=[Depends(auth)],
        responses={409: {"description": "The file changed since base_sha256 was read"}},
    )
    def save_bench(req: M.SaveBenchRequest) -> M.BenchDocument:
        return file_errors(lambda: files.save(req.path, req.base_sha256, req.data, req.source))  # type: ignore[no-any-return]

    @router.post(
        "/api/benches/hello",
        response_model=M.BenchDocument,
        operation_id="createHelloBench",
        dependencies=[Depends(auth)],
    )
    def create_hello_bench() -> M.BenchDocument:
        """Copy the packaged Hello World bench into the workspace (an existing one is kept as is)."""
        return file_errors(lambda: files.create_hello("ui"))  # type: ignore[no-any-return]

    @router.post(
        "/api/benches/copy",
        response_model=M.BenchDocument,
        operation_id="copyBench",
        dependencies=[Depends(auth)],
        responses={409: {"description": "The target file exists"}},
    )
    def copy_bench(req: M.CopyBenchRequest) -> M.BenchDocument:
        """Copy a whole bench — instruments, wires, settings, layout — into a new file (#874)."""
        return file_errors(lambda: files.copy(req.path, req.new_path, req.name, "ui"))  # type: ignore[no-any-return]

    @router.post(
        "/api/benches/edit",
        response_model=M.BenchDocument,
        operation_id="editBench",
        dependencies=[Depends(auth)],
        responses={409: {"description": "The file changed since base_sha256 was read"}},
    )
    def edit_bench(req: M.EditBenchRequest) -> M.BenchDocument:
        return file_errors(lambda: files.edit(req.path, req.base_sha256, req.ops, req.source))  # type: ignore[no-any-return]


def _bench_history_routes(ctx: ApiContext, router: APIRouter) -> None:
    files = ctx.files
    auth = ctx.auth
    file_errors = ctx.file_errors

    @router.get(
        "/api/benches/history",
        response_model=list[M.BenchVersion],
        operation_id="benchHistory",
        dependencies=[Depends(auth)],
    )
    def bench_history(path: str = Query(...)) -> list[M.BenchVersion]:
        """Saved versions of a bench file, newest first (D35)."""
        return file_errors(lambda: files.versions(path))  # type: ignore[no-any-return]

    @router.get(
        "/api/benches/version",
        response_model=M.BenchVersionDocument,
        operation_id="readBenchVersion",
        dependencies=[Depends(auth)],
    )
    def read_bench_version(path: str = Query(...), seq: int = Query(...)) -> M.BenchVersionDocument:
        return file_errors(lambda: files.version(path, seq))  # type: ignore[no-any-return]

    @router.post(
        "/api/benches/restore",
        response_model=M.BenchDocument,
        operation_id="restoreBench",
        dependencies=[Depends(auth)],
        responses={409: {"description": "The file changed since base_sha256 was read"}},
    )
    def restore_bench(req: M.RestoreBenchRequest) -> M.BenchDocument:
        """Write an earlier version back as the newest one; later versions stay in the history."""
        return file_errors(lambda: files.restore(req.path, req.seq, req.base_sha256))  # type: ignore[no-any-return]

    @router.post(
        "/api/benches/validate",
        response_model=M.ValidateResponse,
        operation_id="validateBench",
        dependencies=[Depends(auth)],
    )
    def validate_bench(req: M.ValidateRequest) -> M.ValidateResponse:
        report = file_errors(lambda: files.validate(req.data, req.path))
        return M.ValidateResponse(ok=report.ok, diagnostics=list(report.diagnostics))


def build_router(ctx: ApiContext) -> APIRouter:
    router = APIRouter()
    _bench_file_routes(ctx, router)
    _bench_history_routes(ctx, router)
    return router
