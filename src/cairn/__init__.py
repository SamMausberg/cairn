"""CAIRN: an inspectable native compiler and agent editing toolkit."""

from .cairnc import Diagnostic, compile_source
from .version import __version__

__all__ = ["Diagnostic", "__version__", "compile_source"]
