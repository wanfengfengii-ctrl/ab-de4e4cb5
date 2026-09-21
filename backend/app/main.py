"""FastAPI application exposing the exact hazard audit engine."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import engine
from .models import AuditRequest, AuditResponse

app = FastAPI(
    title="异步设备联锁审计台",
    description=(
        "对输入成批切换下的整数惯性延迟无环门网表做完整状态探索，"
        "判定监测输出是否至多完成一次必要翻转。"
    ),
    version="1.0.0",
)


@app.exception_handler(engine.NetlistError)
async def netlist_error_handler(_: Request, exc: engine.NetlistError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": exc.message, "code": exc.code,
                 "location": exc.location},
    )


@app.exception_handler(engine.AuditLimit)
async def limit_error_handler(_: Request, exc: engine.AuditLimit) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": str(exc), "code": "state_limit", "location": None},
    )


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/api/audit", response_model=AuditResponse)
async def run_audit(payload: AuditRequest) -> dict:
    state_cap = int(os.environ.get("AUDIT_STATE_CAP", "2000000"))
    return engine.audit(payload, state_cap=state_cap)


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Re-shape pydantic's report into the same {error, code, location} body
    # so the frontend can clear stale results and point at the cause.
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(
        str(p) for p in first.get("loc", []) if p not in ("body", "payload")
    )
    return JSONResponse(
        status_code=422,
        content={
            "error": first.get("msg", "请求格式不合法"),
            "code": "schema_invalid",
            "location": loc or None,
        },
    )


# Serve the built React bundle when present (production single-image layout).
_STATIC_DIR = Path(os.environ.get("STATIC_DIR", "/app/static"))
if _STATIC_DIR.is_dir():
    from fastapi.responses import FileResponse

    app.mount(
        "/assets",
        StaticFiles(directory=_STATIC_DIR / "assets"),
        name="assets",
    )

    @app.get("/", include_in_schema=False)
    async def index_page() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str) -> FileResponse:
        candidate = _STATIC_DIR / path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")
