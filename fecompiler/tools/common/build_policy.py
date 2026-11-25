"""Whether a step may reuse what an earlier run of it built.

Reuse is the default: a build output is kept while its record says it still
corresponds to its inputs. ``run --clean`` asks a step to build everything
again regardless, for the case where a record is not trusted.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_CLEAN_BUILD: ContextVar[bool] = ContextVar("ecc_fe_clean_build", default=False)


@contextmanager
def clean_build(requested: bool = True) -> Iterator[None]:
    """Run the enclosed steps without reusing earlier build outputs."""
    token = _CLEAN_BUILD.set(bool(requested))
    try:
        yield
    finally:
        _CLEAN_BUILD.reset(token)


def clean_build_requested() -> bool:
    """Read in the thread that runs the step, before any worker is started."""
    return _CLEAN_BUILD.get()


__all__ = ["clean_build", "clean_build_requested"]
