from __future__ import annotations

import argparse
from collections.abc import Sequence

from fecompiler.runtime.stdio_server import main as run_stdio_runtime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ecc-fe rpc",
        description="Serve the private ECOS Studio runtime protocol.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="Start the JSON-RPC runtime server")
    serve.add_argument(
        "--stdio",
        action="store_true",
        help="Use Content-Length framed JSON-RPC over stdin/stdout",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command != "serve" or not args.stdio:
        parser.error("rpc serve requires --stdio")
    return run_stdio_runtime()
