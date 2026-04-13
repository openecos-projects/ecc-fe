"""Minimal server that exposes workspace and flow APIs plus static GUI."""

from __future__ import annotations

import argparse
import json
import logging
import os
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

from server.ecos_server.ecc.config import DEFAULT_PROJECTS_ROOT
from server.ecos_server.ecc.routers import workspace as workspace_router

ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = ROOT / "gui"
EXAMPLES_DIR = ROOT / "examples"


class AppHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib API
        parsed = urlparse(self.path)
        if parsed.path == "/api/examples":
            filelists = sorted(EXAMPLES_DIR.glob("*.f")) if EXAMPLES_DIR.exists() else []
            return self._write_json({
                "ok": True,
                "data": [{"name": p.name, "path": str(p)} for p in filelists],
            })
        if parsed.path == "/api/workspace/health":
            return self._write_json({"status": "ok"})
        if parsed.path == "/api/config":
            return self._write_json(
                {
                    "ok": True,
                    "data": {"default_project_root": str(DEFAULT_PROJECTS_ROOT)},
                },
            )
        if parsed.path.startswith("/api/"):
            self._write_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802 - stdlib API
        parsed = urlparse(self.path)
        if parsed.path == "/api/workspace/create_workspace":
            return self._dispatch_json_api(workspace_router.create_workspace)
        if parsed.path == "/api/workspace/load_workspace":
            return self._dispatch_json_api(workspace_router.load_workspace)
        if parsed.path == "/api/workspace/rtl2gds":
            return self._dispatch_json_api(workspace_router.rtl2gds)
        if parsed.path == "/api/workspace/run_step":
            return self._dispatch_json_api(workspace_router.run_step)
        if parsed.path == "/api/workspace/get_home_page":
            return self._dispatch_json_api(workspace_router.get_home_page)
        if parsed.path == "/api/flow/run":
            # compatibility shim for previous ecc-fe API
            return self._dispatch_json_api(workspace_router.run_flow_compat)
        self._write_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)

    def _dispatch_json_api(self, handler) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            result = handler(payload)
            self._write_json(result)
        except ValueError as exc:
            self._write_json({"response": "failed", "message": [str(exc)]}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.exception("unhandled error in API handler")
            self._write_json(
                {"response": "error", "message": [f"Internal error: {exc}"]},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _serve_static(self, req_path: str) -> None:
        cleaned = req_path.lstrip("/")
        if not cleaned:
            cleaned = "index.html"
        target = (GUI_DIR / cleaned).resolve()
        if GUI_DIR not in target.parents and target != GUI_DIR:
            self._write_json({"error": "Forbidden"}, status=HTTPStatus.FORBIDDEN)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.exists():
            self._write_json({"error": "Not Found"}, status=HTTPStatus.NOT_FOUND)
            return
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", _guess_content_type(target))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        super().log_message(format, *args)


def _guess_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".html":
        return "text/html; charset=utf-8"
    if suffix == ".js":
        return "application/javascript; charset=utf-8"
    if suffix == ".css":
        return "text/css; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    return "application/octet-stream"


def serve(port: int = 8080) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    print(f"ecc-fe server running at http://127.0.0.1:{port}")  # noqa: T201
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run ecc-fe ecos_server")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ECC_FE_SERVER_PORT", "8080")),
        help="HTTP port for the local server (default: 8080 or ECC_FE_SERVER_PORT)",
    )
    args = parser.parse_args()
    serve(port=args.port)
