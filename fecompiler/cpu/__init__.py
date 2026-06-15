"""CPU wrapper registry for frontend CPU integrations."""

from .registry import CpuWrapper, get_cpu_wrapper

__all__ = [
    "CpuWrapper",
    "get_cpu_wrapper",
]
