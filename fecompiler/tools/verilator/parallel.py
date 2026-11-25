"""Run independent pieces of a step side by side."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fecompiler.tools.common.process import stop_session
from fecompiler.tools.verilator.lint import (  # noqa: F401
    VerilatorLintStep,
    build_lint_summary,
    parse_verilator_diagnostics,
)


def _map_in_order(items: list[Any], function: Any, jobs: int, session_of: Any) -> list[Any]:
    """Apply *function* to every item, *jobs* at a time; results keep the order.

    *session_of* names the tool session directory of an item, so that tools
    still running can be stopped when the work is interrupted.
    """
    if jobs <= 1:
        return [function(item) for item in items]
    with ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="ecc-fe-sim") as pool:
        futures = [pool.submit(function, item) for item in items]
        try:
            return [future.result() for future in futures]
        except BaseException:
            # Interrupted or failed: do not leave tools running behind us.
            for future in futures:
                future.cancel()
            for item in items:
                stop_session(session_of(item))
            raise
