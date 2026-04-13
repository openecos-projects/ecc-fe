"""FastAPI server that exposes workspace and flow APIs plus static GUI."""

from __future__ import annotations

import argparse
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response

from server.ecos_server.ecc.config import DEFAULT_PROJECTS_ROOT
from server.ecos_server.ecc.routers import workspace as workspace_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = ROOT / "gui"
EXAMPLES_DIR = ROOT / "examples"

app = FastAPI(title="ecc-fe", docs_url="/api/docs", redoc_url=None)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dispatch(handler, payload: dict[str, Any]) -> JSONResponse:
    try:
        result = handler(payload)
        return JSONResponse(content=result)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"response": "failed", "message": [str(exc)]})
    except Exception as exc:
        logger.exception("unhandled error in API handler")
        return JSONResponse(status_code=500, content={"response": "error", "message": [f"Internal error: {exc}"]})


# ── GET endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/workspace/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict:
    return {"ok": True, "data": {"default_project_root": str(DEFAULT_PROJECTS_ROOT)}}


@app.get("/api/examples")
def examples() -> dict:
    filelists = sorted(EXAMPLES_DIR.glob("*.f")) if EXAMPLES_DIR.exists() else []
    return {"ok": True, "data": [{"name": p.name, "path": str(p)} for p in filelists]}


# ── POST endpoints ────────────────────────────────────────────────────────────

@app.post("/api/workspace/create_workspace")
def create_workspace(payload: dict[str, Any]) -> Response:
    return _dispatch(workspace_router.create_workspace, payload)


@app.post("/api/workspace/load_workspace")
def load_workspace(payload: dict[str, Any]) -> Response:
    return _dispatch(workspace_router.load_workspace, payload)


@app.post("/api/workspace/rtl2gds")
def rtl2gds(payload: dict[str, Any]) -> Response:
    return _dispatch(workspace_router.rtl2gds, payload)


@app.post("/api/workspace/run_step")
def run_step(payload: dict[str, Any]) -> Response:
    return _dispatch(workspace_router.run_step, payload)


@app.post("/api/workspace/get_home_page")
def get_home_page(payload: dict[str, Any]) -> Response:
    return _dispatch(workspace_router.get_home_page, payload)


@app.post("/api/flow/run")
def flow_run(payload: dict[str, Any]) -> Response:
    # compatibility shim for previous ecc-fe API
    return _dispatch(workspace_router.run_flow_compat, payload)


# ── Static file serving ───────────────────────────────────────────────────────

@app.get("/{full_path:path}")
def serve_static(full_path: str) -> Response:
    if not full_path or full_path == "/":
        full_path = "index.html"
    target = (GUI_DIR / full_path).resolve()
    if GUI_DIR not in target.parents and target != GUI_DIR:
        raise HTTPException(status_code=403, detail="Forbidden")
    if target.is_dir():
        target = target / "index.html"
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not Found")
    mime, _ = mimetypes.guess_type(str(target))
    return Response(content=target.read_bytes(), media_type=mime or "application/octet-stream")


# ── Entry point ───────────────────────────────────────────────────────────────

def serve(port: int = 8080) -> None:
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run ecc-fe server")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ECC_FE_SERVER_PORT", "8080")),
        help="HTTP port (default: 8080 or ECC_FE_SERVER_PORT)",
    )
    args = parser.parse_args()
    serve(port=args.port)
