"""SoC wrapper registry for frontend simulation harnesses."""

from .registry import SocWrapper, get_soc_wrapper, soc_runtime_options

__all__ = [
    "SocWrapper",
    "get_soc_wrapper",
    "soc_runtime_options",
]
